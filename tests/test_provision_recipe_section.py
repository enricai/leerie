"""Tests for `_format_provision_recipe_section` — the prompt-injection
helper that hands the persisted provision recipe to implementer and
conformer workers.

The function sits on the contract between phase_provision (which detects
the recipe) and the workers (which execute it in their worktrees). Since
2026-11-12 the repository image holds no dependency (DESIGN §6½ *The
repository image holds no dependency*), so every `install` and `build`
entry is shown, for every ecosystem: until then pip, uv, poetry, pipenv,
bundle, cargo and go installs were dropped as "baked" whether or not the
image's bake had built (a Rails repository's ten runs: 0 of 62 implementer
and 0 of 65 conformer prompts carried its `bundle install`).
"""
from __future__ import annotations

import json

import pytest
from tests.conftest import run_git_repo_first


PNPM_INSTALL = {
    "kind": "install",
    "command": ["pnpm", "install", "--frozen-lockfile"],
    "working_dir": ".",
    "timeout_s": 1800,
}
GO_DOWNLOAD = {
    "kind": "install",
    "command": ["go", "mod", "download"],
    "working_dir": ".",
    "timeout_s": 600,
}
NONE_ENTRY = {"kind": "none", "command": [], "working_dir": ".",
              "timeout_s": 0}

# One entry per ecosystem the bake used to hide, a Node one and a build
# step, in the order a polyglot recipe lists them.
POLYGLOT = [
    {"kind": "install", "command": ["bundle", "install"],
     "working_dir": ".", "timeout_s": 900},
    {"kind": "install", "command": ["uv", "sync"], "working_dir": "py",
     "timeout_s": 600},
    {"kind": "install", "command": ["cargo", "fetch"], "working_dir": "rs",
     "timeout_s": 600},
    GO_DOWNLOAD,
    {"kind": "install", "command": ["pip", "install", "-r",
                                    "requirements.txt",
                                    "--break-system-packages"],
     "working_dir": ".", "timeout_s": 300},
    PNPM_INSTALL,
    {"kind": "build", "command": ["pnpm", "run", "build"],
     "working_dir": ".", "timeout_s": 600},
]
POLYGLOT_LINES = [
    "1. bundle install   (cwd: ., timeout: 900s)",
    "2. uv sync   (cwd: py, timeout: 600s)",
    "3. cargo fetch   (cwd: rs, timeout: 600s)",
    "4. go mod download   (cwd: ., timeout: 600s)",
    "5. pip install -r requirements.txt --break-system-packages   "
    "(cwd: ., timeout: 300s)",
    "6. pnpm install --frozen-lockfile   (cwd: ., timeout: 1800s)",
    "7. pnpm run build   (cwd: ., timeout: 600s)",
]
# Words that would tell a worker its dependencies are already installed.
FALSE_CLAIMS = ("baked", "bake", "/opt/venv", "/opt/bundle", "/opt/")


def test_empty_recipe_returns_none(leerie):
    assert leerie._format_provision_recipe_section(
        [], audience="implementer") is None


def test_all_none_recipe_returns_none(leerie):
    """Docs-only recipes only carry a `kind: none` entry; workers
    should see no injected section."""
    assert leerie._format_provision_recipe_section(
        [NONE_ENTRY], audience="implementer") is None
    assert leerie._format_provision_recipe_section(
        [NONE_ENTRY], audience="conformer") is None


def test_implementer_audience_renders_advisory_framing(leerie):
    out = leerie._format_provision_recipe_section(
        [PNPM_INSTALL], audience="implementer")
    assert out is not None
    assert "PROVISION_RECIPE:" in out
    assert "Decide whether your subtask needs them" in out
    assert "starts with no installed dependencies" in out
    # The command itself is verbatim.
    assert "pnpm install --frozen-lockfile" in out
    # The cwd + timeout metadata is rendered.
    assert "(cwd: ., timeout: 1800s)" in out


def test_conformer_audience_emphasizes_pre_build_install(leerie):
    out = leerie._format_provision_recipe_section(
        [PNPM_INSTALL], audience="conformer")
    assert out is not None
    assert "PROVISION_RECIPE:" in out
    # Conformer framing since the handover (DESIGN §9): the orchestrator
    # applies the recipe itself before it measures build/lint/tests, so the
    # block is no longer a "run these before BUILD_CMD" instruction. It is
    # shown because a *targeted* command of the conformer's own may still
    # need the deps present.
    assert "BLT_RESULTS:" in out
    assert "targeted" in out
    assert "BUILD_CMD" not in out and "TEST_CMD" not in out, (
        "the conformer is no longer handed raw axis command names")


@pytest.mark.parametrize("audience", ["implementer", "conformer"])
def test_every_ecosystem_install_is_shown_in_order(leerie, audience):
    """Every install and build entry, numbered in the recipe's order,
    whatever its manager; and nothing says the dependencies are already
    in the image."""
    out = leerie._format_provision_recipe_section(POLYGLOT,
                                                  audience=audience)
    assert out is not None
    rendered = [line.strip() for line in out.splitlines()
                if line.strip()[:2].rstrip(".").isdigit()]
    assert rendered == POLYGLOT_LINES
    for word in FALSE_CLAIMS:
        assert word not in out, (audience, word)


def test_none_entries_are_skipped_in_mixed_recipe(leerie):
    """A recipe with a `none` entry alongside real installs renders
    only the real installs (and renumbers them)."""
    out = leerie._format_provision_recipe_section(
        [NONE_ENTRY, PNPM_INSTALL], audience="implementer")
    assert out is not None
    assert "1. pnpm install --frozen-lockfile" in out
    # The `none` entry must not appear under any rendering.
    for line in out.splitlines():
        assert "none" not in line.lower() or "PROVISION_RECIPE" in line


def test_unknown_audience_raises(leerie):
    """Defensive check — a typo in the call site shouldn't silently
    fall back to a default framing."""
    with pytest.raises(ValueError, match="unknown audience"):
        leerie._format_provision_recipe_section(
            [PNPM_INSTALL], audience="planner")


# --- the prompts the workers are given ------------------------------------


@pytest.fixture
def prompt_state(leerie, tmp_path):
    """A real run directory and `State` holding the polyglot recipe, as
    `phase_provision` persists it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git_repo_first(repo, "init", "-q", "-b", "main")
    (repo / "README.md").write_text("# repo\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         "init")
    run_id, sid = "recipe-001", "feat-001"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(
        {"id": sid, "title": "t", "success_criteria_seed": "c"}))
    (run_dir / "plan.json").write_text(json.dumps(
        {"task": "x", "waves": [], "subtasks": {
            sid: {"id": sid, "depends_on": [], "requires": []}}}))
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"},
               "provision": {"recipe": POLYGLOT, "source": "table"}}
    st.save()
    return st, run_dir, sid


def _section(prompt: str) -> str:
    start = prompt.index("PROVISION_RECIPE:")
    return prompt[start:]


def test_implementer_prompt_carries_every_install(leerie, prompt_state):
    """The implementer's user prompt, as `_implementer_base_prompt` builds
    it from the persisted recipe, lists every install and build entry and
    claims nothing is in the image."""
    st, run_dir, sid = prompt_state
    up = leerie._implementer_base_prompt(
        sid, run_dir, dict(leerie.DEFAULT_CAPS), st,
        cwd_line="Your current working directory IS your worktree.")
    prompt = "\n".join(up)
    section = _section(prompt)
    for line in POLYGLOT_LINES:
        assert line in section, line
    assert [section.index(line) for line in POLYGLOT_LINES] == sorted(
        section.index(line) for line in POLYGLOT_LINES)
    for word in FALSE_CLAIMS:
        assert word not in prompt, word


def test_conformer_prompt_carries_every_install(leerie, prompt_state):
    """The per-subtask and final conformers' context sections
    (`_append_conformer_context_sections`) list every entry too."""
    st, _run_dir, _sid = prompt_state
    up: list[str] = []
    leerie._append_conformer_context_sections(up, {}, "subtask", st)
    prompt = "\n".join(up)
    section = _section(prompt)
    for line in POLYGLOT_LINES:
        assert line in section, line
    for word in FALSE_CLAIMS:
        assert word not in prompt, word


@pytest.mark.parametrize("name", ["implementer", "conformer"])
def test_system_prompts_name_no_baked_environment(leerie, name):
    """No system prompt tells a worker its dependencies live in a shared
    image path: the image holds none (DESIGN §6½ *The repository image
    holds no dependency*)."""
    text = leerie._load_prompt(name)
    for word in ("/opt/venv", "/opt/bundle", ".venv-private"):
        assert word not in text, (name, word)
