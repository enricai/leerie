"""Tests for resolve_skip_satisfied_check() — the --skip-satisfied-check
opt-out (DESIGN §8 *Already-satisfied subtask elimination*).

Precedence: CLI flag → LEERIE_SKIP_SATISFIED_CHECK env → skip_satisfied_check
in leerie.toml → False (the probe runs by default).

Mirrors test_resolve_skip_overlap_judge.py — both share `_resolve_bool_pref`,
so this file locks the wiring (env var name + file key), not the resolution
logic.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def repo_root(tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_SKIP_SATISFIED_CHECK", raising=False)
    return tmp_path


def test_default_is_off(leerie, repo_root):
    assert leerie.resolve_skip_satisfied_check(
        repo_root, cli_value=False) is False


def test_cli_flag_wins(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_SATISFIED_CHECK", "0")
    (repo_root / "leerie.toml").write_text("skip_satisfied_check = false\n")
    assert leerie.resolve_skip_satisfied_check(
        repo_root, cli_value=True) is True


def test_env_set_true(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_SATISFIED_CHECK", "1")
    assert leerie.resolve_skip_satisfied_check(
        repo_root, cli_value=False) is True


def test_file_set_true_no_env(leerie, repo_root):
    (repo_root / "leerie.toml").write_text("skip_satisfied_check = true\n")
    assert leerie.resolve_skip_satisfied_check(
        repo_root, cli_value=False) is True


def test_env_wins_over_file(leerie, repo_root, monkeypatch):
    (repo_root / "leerie.toml").write_text("skip_satisfied_check = true\n")
    monkeypatch.setenv("LEERIE_SKIP_SATISFIED_CHECK", "false")
    assert leerie.resolve_skip_satisfied_check(
        repo_root, cli_value=False) is False


def test_env_garbage_dies(leerie, repo_root, monkeypatch):
    monkeypatch.setenv("LEERIE_SKIP_SATISFIED_CHECK", "maybe")
    with pytest.raises(SystemExit):
        leerie.resolve_skip_satisfied_check(repo_root, cli_value=False)


def test_satisfied_probe_in_worker_types(leerie):
    assert "satisfied_probe" in leerie.WORKER_TYPES


def test_satisfied_probe_default_model_is_sonnet(leerie):
    assert leerie.MODEL_DEFAULT_PER_WORKER["satisfied_probe"] == "sonnet"


def test_satisfied_probe_tools_exclude_history_git(leerie):
    """The probe's tool scope must NOT permit history-spanning git —
    only base-tree reads. Calibration proved full git access
    false-positives (code on other branches read as 'already done')."""
    tools = leerie.SATISFIED_PROBE_TOOLS
    assert "git show HEAD:" in tools
    assert "git log" not in tools
    # bare `git show:*` (any ref) must not appear — only the HEAD-scoped form
    assert "Bash(git show:*)" not in tools
    assert "git branch" not in tools


def test_satisfied_probe_git_status_uses_trailing_wildcard(leerie):
    """`git status` must carry the `:*` suffix, not be a bare pattern.

    A bare `Bash(verb)` is an EXACT-STRING match per the Claude Code
    permission docs ("Bash(npm run build) matches the exact command npm run
    build"), so `Bash(git status)` permits only the literal zero-argument
    form and denies every real invocation — `git status --porcelain=v1`,
    `--short`, `-uall`. Observed live: probes burned a `claude -p` call each,
    hit "This command requires approval", and then reasoned from partial
    evidence (one wrongly concluded a findings artifact was incomplete).

    The gate is fail-safe so this cost spend and probe quality, not
    correctness — but it is still a malformed pattern: every other verb in
    the bucket carries `:*`, and the constant's own comment documents the
    intent this suffix implements.
    """
    tools = leerie.SATISFIED_PROBE_TOOLS
    assert "Bash(git status:*)" in tools, (
        "git status must use the trailing-wildcard form so flagged "
        "invocations (--porcelain, --short, -uall) are permitted"
    )
    # the bare form must be gone (comma-anchored so the `:*` form isn't matched)
    assert "Bash(git status)," not in tools
    assert not tools.endswith("Bash(git status)")


def test_satisfied_probe_tools_exclude_python(leerie):
    """`python3` must never enter the probe's bucket.

    Two probes tried `python3 -c` / `python3 - <<EOF` for JSON comparison and
    were denied. That denial is CORRECT: a general-purpose interpreter
    trivially defeats the base-tree scoping that the 12/12-false-positive
    calibration exists to protect (it could read any ref, any branch). A
    probe that cannot answer with the read-only verbs must degrade to
    'not satisfied' — the fail-safe direction.
    """
    tools = leerie.SATISFIED_PROBE_TOOLS
    assert "python" not in tools


def test_satisfied_probe_verbs_all_carry_wildcard(leerie):
    """Every Bash verb in the bucket carries `:*` except argument-less `pwd`.

    Structural guard against re-introducing the bare-pattern class of bug on
    a different verb.
    """
    import re
    tools = leerie.SATISFIED_PROBE_TOOLS
    bare = [p for p in re.findall(r"Bash\([^)]*\)", tools) if ":*" not in p]
    assert bare == ["Bash(pwd)"], (
        f"unexpected bare (exact-match) Bash patterns: {bare} — every verb "
        "that takes arguments needs the `:*` trailing wildcard"
    )
