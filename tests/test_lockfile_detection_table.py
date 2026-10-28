"""Tests for `_detect_recipe_from_lockfiles` — the deterministic table
that maps lockfile presence in a repo root to install commands.

Verified shape (DESIGN §6½, IMPLEMENTATION §6½):
- Single matches return a one-entry recipe.
- Polyglot repos (Gemfile.lock + yarn.lock) return ALL matches, not
  just the first — this is the Rails-style fix.
- pnpm > yarn > npm precedence holds when multiple Node lockfiles
  coexist.
- Ambiguous shapes (bare requirements.txt, bare pyproject.toml without
  a lockfile, pom.xml, build.gradle) abstain → empty list → caller
  falls back to the LLM worker.
"""
from __future__ import annotations

from pathlib import Path

import pytest


def _make_files(root: Path, names: list[str]) -> None:
    """Create empty files at `root/name` for each name. Mkdir parents
    so workspace-style nested paths work."""
    for name in names:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()


@pytest.mark.parametrize("files, expected_argv0", [
    (["pnpm-lock.yaml"], ["pnpm"]),
    (["yarn.lock"], ["yarn"]),
    (["package-lock.json"], ["npm"]),
    (["uv.lock"], ["uv"]),
    (["poetry.lock"], ["poetry"]),
    (["Pipfile.lock"], ["pipenv"]),
    (["go.mod", "go.sum"], ["go"]),
    (["Cargo.lock"], ["cargo"]),
    (["Gemfile.lock"], ["bundle"]),
])
def test_single_lockfile_matches(leerie, tmp_path, files, expected_argv0):
    """Each lockfile alone produces a one-entry recipe with the documented
    argv[0]."""
    _make_files(tmp_path, files)
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert len(recipe) == 1
    assert recipe[0]["command"][0] == expected_argv0[0]
    assert recipe[0]["working_dir"] == "."
    assert recipe[0]["kind"] == "install"


def test_pnpm_wins_over_yarn(leerie, tmp_path):
    """A repo with both pnpm-lock.yaml and yarn.lock picks pnpm — the
    presence of both lockfiles usually means a left-behind yarn artifact
    from a prior tool, and the most-specific match wins."""
    _make_files(tmp_path, ["pnpm-lock.yaml", "yarn.lock"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert len(recipe) == 1
    assert recipe[0]["command"][0] == "pnpm"


def test_pnpm_wins_over_npm(leerie, tmp_path):
    """Same precedence rule against package-lock.json."""
    _make_files(tmp_path, ["pnpm-lock.yaml", "package-lock.json"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert len(recipe) == 1
    assert recipe[0]["command"][0] == "pnpm"


def test_yarn_wins_over_npm(leerie, tmp_path):
    """Without pnpm-lock.yaml, yarn beats npm."""
    _make_files(tmp_path, ["yarn.lock", "package-lock.json"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert len(recipe) == 1
    assert recipe[0]["command"][0] == "yarn"


def test_polyglot_rails_with_frontend(leerie, tmp_path):
    """The Rails-style fix from the design verification: a repo with
    BOTH Gemfile.lock and yarn.lock at root must emit BOTH installs,
    not just the first match."""
    _make_files(tmp_path, ["Gemfile.lock", "yarn.lock"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    argv0 = {entry["command"][0] for entry in recipe}
    assert argv0 == {"bundle", "yarn"}


def test_polyglot_go_plus_node(leerie, tmp_path):
    """Another polyglot shape: Go backend + JS frontend."""
    _make_files(tmp_path, ["go.mod", "go.sum", "pnpm-lock.yaml"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    argv0 = {entry["command"][0] for entry in recipe}
    assert argv0 == {"go", "pnpm"}


@pytest.mark.parametrize("files", [
    [],                                # empty repo
    ["requirements.txt"],              # bare requirements (no marker)
    ["pyproject.toml"],                # bare pyproject (no lockfile)
    ["pom.xml"],                       # Maven
    ["build.gradle"],                  # Gradle Groovy
    ["build.gradle.kts"],              # Gradle Kotlin
    ["Makefile"],                      # opaque Makefile-driven setup
    ["go.mod"],                        # go.mod without go.sum
])
def test_table_abstains(leerie, tmp_path, files):
    """The deterministic table returns an empty list (caller falls back
    to LLM) for ambiguous or unsupported shapes."""
    _make_files(tmp_path, files)
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert recipe == []


def test_uv_wins_over_poetry(leerie, tmp_path):
    """A repo with both uv.lock and poetry.lock picks uv (verified
    pattern from FastAPI-style projects in transition)."""
    _make_files(tmp_path, ["uv.lock", "poetry.lock"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert len(recipe) == 1
    assert recipe[0]["command"][0] == "uv"


def test_python_lockfile_does_not_trigger_node(leerie, tmp_path):
    """Sanity: a Python-only repo doesn't accidentally emit Node
    install commands."""
    _make_files(tmp_path, ["uv.lock", "pyproject.toml"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    argv0 = {entry["command"][0] for entry in recipe}
    assert argv0 == {"uv"}


def test_recipe_entries_have_required_fields(leerie, tmp_path):
    """Every entry must carry kind, command, working_dir, timeout_s —
    matching the schema in SCHEMAS["provision"]."""
    _make_files(tmp_path, ["pnpm-lock.yaml"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    for entry in recipe:
        assert entry["kind"] in ("install", "build", "none")
        assert isinstance(entry["command"], list)
        assert all(isinstance(x, str) for x in entry["command"])
        assert entry["working_dir"] == "."
        assert isinstance(entry["timeout_s"], int)
        assert entry["timeout_s"] > 0


# --- independent package roots below the top (F4; DESIGN §6½ *One install
# per independent package root*) ---------------------------------------------

def _entries(recipe):
    return [(e["working_dir"], e["command"]) for e in recipe]


def test_each_nested_non_workspace_root_gets_its_own_install(leerie,
                                                             tmp_path):
    """The shape that killed 7 runs at the provision gate: a root pnpm
    project plus `worker/` and `infra/` with their own lockfiles and no
    workspace covering them."""
    _make_files(tmp_path, ["pnpm-lock.yaml", "worker/pnpm-lock.yaml",
                           "infra/pnpm-lock.yaml"])
    recipe = leerie._detect_recipe_from_lockfiles(tmp_path)
    assert _entries(recipe) == [
        (".", ["pnpm", "install", "--frozen-lockfile"]),
        ("infra", ["pnpm", "install", "--frozen-lockfile"]),
        ("worker", ["pnpm", "install", "--frozen-lockfile"]),
    ]
    leerie._validate_provision_recipe(recipe)


def test_workspace_members_are_not_separate_roots(leerie, tmp_path):
    _make_files(tmp_path, ["pnpm-lock.yaml", "packages/a/pnpm-lock.yaml",
                           "tools/x/pnpm-lock.yaml"])
    (tmp_path / "pnpm-workspace.yaml").write_text(
        "packages:\n  - 'packages/*'\n  # - 'tools/*'\n")
    assert _entries(leerie._detect_recipe_from_lockfiles(tmp_path)) == [
        (".", ["pnpm", "install", "--frozen-lockfile"]),
        ("tools/x", ["pnpm", "install", "--frozen-lockfile"]),
    ]


def test_package_json_workspaces_and_exclusions(leerie, tmp_path):
    import json
    _make_files(tmp_path, ["yarn.lock", "apps/web/yarn.lock",
                           "apps/legacy/package-lock.json",
                           "apps/web/deep/yarn.lock"])
    (tmp_path / "package.json").write_text(json.dumps(
        {"workspaces": {"packages": ["apps/*", "!apps/legacy"]}}))
    # `apps/*` stays within one segment, so apps/web/deep is its own root.
    assert _entries(leerie._detect_recipe_from_lockfiles(tmp_path)) == [
        (".", ["yarn", "install", "--frozen-lockfile"]),
        ("apps/legacy", ["npm", "ci"]),
        ("apps/web/deep", ["yarn", "install", "--frozen-lockfile"]),
    ]


def test_precedence_within_a_nested_root_and_node_modules_ignored(
        leerie, tmp_path):
    _make_files(tmp_path, ["svc/pnpm-lock.yaml", "svc/package-lock.json",
                           "svc/node_modules/dep/package-lock.json",
                           "node_modules/x/yarn.lock"])
    assert _entries(leerie._detect_recipe_from_lockfiles(tmp_path)) == [
        ("svc", ["pnpm", "install", "--frozen-lockfile"]),
    ]


def test_in_a_git_repo_only_tracked_lockfiles_count(leerie, tmp_path):
    """An untracked nested lockfile (a scratch checkout, a build output) is
    not part of the project."""
    import subprocess
    _make_files(tmp_path, ["pnpm-lock.yaml", "worker/pnpm-lock.yaml",
                           "scratch/pnpm-lock.yaml"])
    for args in (["init", "-q"], ["add", "pnpm-lock.yaml",
                                  "worker/pnpm-lock.yaml"]):
        subprocess.run(["git", "-C", str(tmp_path), *args], check=True)
    assert [d for d, _ in _entries(
        leerie._detect_recipe_from_lockfiles(tmp_path))] == [".", "worker"]
