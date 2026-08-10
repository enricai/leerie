"""Tests for the LEERIE_STATE_HOST_DIR resolution block in the launcher.

The resolution logic lives in the bash launcher (`leerie`); these tests
extract the two real blocks under test — the `_state_dir_default` +
CLI/env/toml resolution ladder, and the `_validate_state_ownership`
sidecar check — verbatim out of the launcher (mirroring
`tests/test_resolve_ec2_vars.py`'s `_extract_resolve_ec2_knob`), rather
than hand-reproducing them. A hand-copied reproduction is body-blind by
construction: the tests run a string literal defined in this file, so no
change to the launcher's actual logic can affect them. This mattered in
practice here — the previous hand-copied ownership harness's
install-subtree basename list was missing `.leerie`, which the real
launcher's list already carried; the drift was invisible to the old
harness by construction.

Precedence (lowest → highest):
  default ($HOME/.leerie/<basename>)
  → leerie.toml `state_dir = ...`
  → LEERIE_STATE_DIR env var
  → --state-dir CLI flag

Verified live per N13's documented trap: an inert sabotage (e.g.
removing the `[ -f "$USER_REPO/leerie.toml" ]` guard around the toml
read) is not a valid falsification — it passes with or without the
extraction, since a missing toml file already makes the subsequent
`grep` fail silently. The discriminating falsification used here is
inverting the CLI/env precedence in the resolution ladder (moving the
`--state-dir` CLI block ahead of the env-var block so CLI no longer
wins): with the extraction, `test_cli_overrides_env` and
`test_precedence_cli_beats_env_beats_toml_beats_default` fail; against
the old hand-copied harness they could not have, since that harness
never read the launcher's source at all.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_state_dir_block() -> str:
    """The REAL `_state_dir_default` + CLI/env/toml resolution ladder,
    lifted out of the launcher verbatim, ending at
    `export LEERIE_STATE_HOST_DIR`. See module docstring for the
    body-blindness rationale and the live falsification result."""
    src = LAUNCHER.read_text()
    start = src.index("_state_dir_default() {")
    end_marker = "export LEERIE_STATE_HOST_DIR\n"
    end = src.index(end_marker, start) + len(end_marker)
    return src[start:end]


def _extract_ownership_function() -> str:
    """The REAL `_validate_state_ownership` function body, lifted out of
    the launcher verbatim (brace-matched via the first `\\n}\\n` after the
    opening line, mirroring `_extract_resolve_ec2_knob`'s technique)."""
    src = LAUNCHER.read_text()
    start = src.index("_validate_state_ownership() {")
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


# Bash harness wrapper: sets USER_REPO/HOME, sources the real resolution
# block, and echoes the resolved LEERIE_STATE_HOST_DIR value.
def _harness() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'USER_REPO="$1"\n'
        'HOME="$2"\n'
        "export HOME\n"
        "shift 2   # remaining args are simulated CLI\n"
        f"{_extract_state_dir_block()}\n"
        'echo "$LEERIE_STATE_HOST_DIR"\n'
    )


# Bash harness wrapper for the ownership check: sets USER_REPO/
# LEERIE_STATE_HOST_DIR, sources the real function, and calls it.
def _ownership_harness() -> str:
    return (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'USER_REPO="$1"\n'
        'LEERIE_STATE_HOST_DIR="$2"\n'
        f"{_extract_ownership_function()}\n"
        "_validate_state_ownership\n"
    )


def _run(
    user_repo: Path,
    fake_home: Path,
    env: dict,
    cli_args: list[str],
    *,
    expect_fail: bool = False,
) -> tuple[str, str]:
    """Run the harness; return (stdout, stderr). Raises on non-zero exit
    unless expect_fail=True."""
    result = subprocess.run(
        ["bash", "-c", _harness(), "--", str(user_repo), str(fake_home)]
        + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    if not expect_fail:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip(), result.stderr.strip()


def _run_ownership(
    user_repo: str,
    state_dir: Path,
    *,
    expect_fail: bool = False,
) -> tuple[int, str, str]:
    """Run the ownership harness; return (returncode, stdout, stderr)."""
    result = subprocess.run(
        ["bash", "-c", _ownership_harness(), "--", user_repo, str(state_dir)],
        env={"PATH": "/usr/bin:/bin"},
        capture_output=True,
        text=True,
    )
    if not expect_fail:
        assert result.returncode == 0, result.stderr
    return result.returncode, result.stdout.strip(), result.stderr.strip()


# ── default resolution ────────────────────────────────────────────────────────


def test_default_is_under_home(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out.startswith(str(fake_home))


def test_default_is_not_inside_user_repo(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert not out.startswith(str(user_repo))


def test_default_is_direct_basename_under_leerie(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(fake_home) + "/.leerie/myproject"


def test_default_no_state_subdirectory(tmp_path):
    """The legacy `state/` subdirectory is gone — paths sit directly under
    $HOME/.leerie/<basename>."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert "/.leerie/state/" not in out


def test_default_key_is_stable(tmp_path):
    """Same inputs → same output (deterministic)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out1, _ = _run(user_repo, fake_home, {}, [])
    out2, _ = _run(user_repo, fake_home, {}, [])
    assert out1 == out2


def test_two_repos_sharing_basename_resolve_to_same_default(tmp_path):
    """A consequence of the basename-only key: two different repo paths
    that share a basename map to the same default. The .owner sidecar
    check (separate harness) is what catches the collision at use time."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    repo_a = tmp_path / "src" / "myproject"
    repo_a.mkdir(parents=True)
    repo_b = tmp_path / "work" / "myproject"
    repo_b.mkdir(parents=True)
    out_a, _ = _run(repo_a, fake_home, {}, [])
    out_b, _ = _run(repo_b, fake_home, {}, [])
    assert out_a == out_b
    assert out_a == str(fake_home) + "/.leerie/myproject"


def test_default_path_format(tmp_path):
    """Exact path format: $HOME/.leerie/<basename>, no slashes inside the
    basename segment."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "nested" / "myproject"
    user_repo.mkdir(parents=True)
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(fake_home) + "/.leerie/myproject"


# ── leerie.toml `state_dir` override ─────────────────────────────────────────


def test_toml_state_dir_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = tmp_path / "custom-state"
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(toml_dir)


def test_toml_state_dir_quoted_value(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "quoted-state")
    (user_repo / "leerie.toml").write_text(f'state_dir = "{toml_dir}"\n')
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == toml_dir


def test_toml_state_dir_tilde_expansion(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    (user_repo / "leerie.toml").write_text("state_dir = ~/mystate\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(fake_home) + "/mystate"


def test_toml_unrelated_key_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    (user_repo / "leerie.toml").write_text("runtime = local\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(fake_home) + "/.leerie/myproject"


# ── LEERIE_STATE_DIR env override ─────────────────────────────────────────────


def test_env_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    env_dir = str(tmp_path / "env-state")
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [])
    assert out == env_dir


def test_env_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    env_dir = str(tmp_path / "env-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [])
    assert out == env_dir


def test_env_empty_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": ""}, [])
    assert out == str(fake_home) + "/.leerie/myproject"


# ── CLI --state-dir override ──────────────────────────────────────────────────


def test_cli_equals_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(user_repo, fake_home, {}, [f"--state-dir={cli_dir}"])
    assert out == cli_dir


def test_cli_space_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(user_repo, fake_home, {}, ["--state-dir", cli_dir])
    assert out == cli_dir


def test_cli_overrides_env(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    env_dir = str(tmp_path / "env-state")
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(
        user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [f"--state-dir={cli_dir}"]
    )
    assert out == cli_dir


def test_cli_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    cli_dir = str(tmp_path / "cli-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {}, [f"--state-dir={cli_dir}"])
    assert out == cli_dir


# ── precedence summary ────────────────────────────────────────────────────────


def test_precedence_cli_beats_env_beats_toml_beats_default(tmp_path):
    """Full precedence ladder: CLI wins over env wins over toml wins over default."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    env_dir = str(tmp_path / "env-state")
    cli_dir = str(tmp_path / "cli-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")

    # All three set; CLI should win.
    out, _ = _run(
        user_repo,
        fake_home,
        {"LEERIE_STATE_DIR": env_dir},
        [f"--state-dir={cli_dir}"],
    )
    assert out == cli_dir

    # Remove CLI; env should win over toml.
    out, _ = _run(
        user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, []
    )
    assert out == env_dir

    # Remove env; toml should win over default.
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == toml_dir


# ── ownership sidecar (.owner) ───────────────────────────────────────────────


def test_ownership_writes_owner_on_fresh_dir(tmp_path):
    state_dir = tmp_path / "state"
    repo = "/tmp/test/myproject"
    rc, _, _ = _run_ownership(repo, state_dir)
    assert rc == 0
    assert (state_dir / ".owner").read_text().strip() == repo


def test_ownership_passes_when_owner_matches(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    repo = "/tmp/test/myproject"
    (state_dir / ".owner").write_text(repo + "\n")
    rc, _, _ = _run_ownership(repo, state_dir)
    assert rc == 0


def test_ownership_fails_on_basename_collision(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / ".owner").write_text("/path/A/myproject\n")
    rc, _, stderr = _run_ownership(
        "/path/B/myproject", state_dir, expect_fail=True
    )
    assert rc != 0
    assert "state-dir collision" in stderr
    assert "/path/A/myproject" in stderr
    assert "/path/B/myproject" in stderr


def test_ownership_backfills_owner_when_runs_subdir_present(tmp_path):
    """A pre-existing state dir from before this commit (has runs/ but no
    .owner) gets the sidecar backfilled rather than rejected."""
    state_dir = tmp_path / "state"
    (state_dir / "runs").mkdir(parents=True)
    repo = "/tmp/test/myproject"
    rc, _, _ = _run_ownership(repo, state_dir)
    assert rc == 0
    assert (state_dir / ".owner").read_text().strip() == repo


def test_ownership_backfills_owner_when_worktrees_subdir_present(tmp_path):
    state_dir = tmp_path / "state"
    (state_dir / "worktrees").mkdir(parents=True)
    repo = "/tmp/test/myproject"
    rc, _, _ = _run_ownership(repo, state_dir)
    assert rc == 0
    assert (state_dir / ".owner").read_text().strip() == repo


def test_ownership_rejects_install_dir_via_git(tmp_path):
    """A dir with .git/ at top level and no runs/ looks like the installer's
    leerie clone, not a state dir — refuse to write."""
    state_dir = tmp_path / "fake-install"
    (state_dir / ".git").mkdir(parents=True)
    rc, _, stderr = _run_ownership(
        "/tmp/something/leerie", state_dir, expect_fail=True
    )
    assert rc != 0
    assert "install directory" in stderr


def test_ownership_rejects_install_dir_via_executable(tmp_path):
    """A dir with a `leerie` executable at top level and no runs/ looks
    like the installer's leerie clone."""
    state_dir = tmp_path / "fake-install"
    state_dir.mkdir()
    leerie_exec = state_dir / "leerie"
    leerie_exec.write_text("#!/bin/sh\n")
    leerie_exec.chmod(0o755)
    rc, _, stderr = _run_ownership(
        "/tmp/something/leerie", state_dir, expect_fail=True
    )
    assert rc != 0
    assert "install directory" in stderr


def test_ownership_claims_empty_dir(tmp_path):
    """An existing empty dir with no markers gets claimed without ceremony."""
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    repo = "/tmp/test/myproject"
    rc, _, _ = _run_ownership(repo, state_dir)
    assert rc == 0
    assert (state_dir / ".owner").read_text().strip() == repo


def test_ownership_rejects_install_subtree(tmp_path):
    """The default key is just the basename, so a user repo named
    `docs` (or any other top-level dir inside the installer's clone)
    resolves to a path that IS a subdirectory of the install dir. The
    install dir's top-level markers (.git/, leerie exec) sit one level
    above, so the existing install-detect branches don't fire — the
    parent-scan check catches it."""
    fake_install = tmp_path / "fake-install"
    fake_install.mkdir()
    (fake_install / ".git").mkdir()
    leerie_exec = fake_install / "leerie"
    leerie_exec.write_text("#!/bin/sh\n")
    leerie_exec.chmod(0o755)
    docs_subtree = fake_install / "docs"
    docs_subtree.mkdir()
    (docs_subtree / "DESIGN.md").write_text("")
    rc, _, stderr = _run_ownership(
        "/tmp/user/docs", docs_subtree, expect_fail=True
    )
    assert rc != 0
    assert "subdirectory of the leerie install dir" in stderr
    assert not (docs_subtree / ".owner").exists()


def test_ownership_rejects_file_at_target(tmp_path):
    """The default key is the user repo's basename. If that basename
    collides with a FILE in the installer's clone (Dockerfile, LICENSE,
    leerie.toml, …), the target exists but is not a directory — the
    write to $dir/.owner would crash with a raw 'Not a directory' bash
    error. Guard catches it with an actionable message."""
    target = tmp_path / "Dockerfile"
    target.write_text("FROM debian:13\n")
    rc, _, stderr = _run_ownership(
        "/tmp/user/Dockerfile", target, expect_fail=True
    )
    assert rc != 0
    assert "exists but is not a directory" in stderr


def test_install_subtree_list_matches_repo_top_level_dirs():
    """The hardcoded basename list in _validate_state_ownership must
    cover every git-tracked top-level directory in this repo. If a
    future PR adds a top-level dir without updating the validator, a
    user repo with that basename (installed via the installer)
    silently corrupts the installer's clone — the exact bug
    v0.3.21's install-subtree fix targets. This test catches that
    drift.

    Uses `git ls-tree HEAD` rather than `iterdir` because the
    installer clones tracked content; local-only dirs
    (.pytest_cache/, .claude/) are not in the install and don't need
    protection. Note: `.leerie/` IS tracked (its committable
    config.toml/Dockerfile — the run artifacts under it stay
    git-ignored), so `.leerie` is in the validator list."""
    repo_root = Path(__file__).resolve().parent.parent
    tracked = subprocess.run(
        ["git", "ls-tree", "--name-only", "-d", "HEAD"],
        cwd=repo_root, capture_output=True, text=True, check=True,
    )
    top_level_dirs = set(tracked.stdout.strip().splitlines())
    launcher = (repo_root / "leerie").read_text()
    match = re.search(
        r'case "\$_basename" in\s*\n\s*([^)\n]+)\)',
        launcher,
    )
    assert match, "could not locate the install-subtree case arm in leerie"
    hardcoded = set(match.group(1).strip().split("|"))
    missing = top_level_dirs - hardcoded
    assert not missing, (
        f"git-tracked top-level dirs not in validator's basename list: {missing}. "
        f"Add them to _validate_state_ownership in leerie."
    )
