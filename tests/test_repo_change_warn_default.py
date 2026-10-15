"""Tests for the sentinel's warn-by-default behaviour (DESIGN §12 L4).

Motivation (measured, 2026-10-15): across 731 logged runs on one host the
sentinel halted 73, and 71 of them carried only operator signatures — prompt
files, editor swap files, report `.md`s `scp`'d in for the next task, `main`
fast-forwarding. The other two are unattributed and none was a confirmed
worker escape; 27 of the 29 runs halted on a HEAD move never got past
planning. So by default `_assert_repo_unchanged`
logs a WARNING and the run continues; `--stop-on-repo-change` restores the
die() (pinned in tests/test_work_sentinel.py and
tests/test_tripwire_operator_signature.py, which run in that mode).

These drive the REAL `_assert_repo_unchanged` against a real git repo and
assert the logged VALUE — a test that only checks "no exception" is equally
satisfied by a sentinel that silently returns.
"""
from __future__ import annotations

import asyncio
import copy
import os
import sys
from pathlib import Path
from types import ModuleType

import pytest

from tests.conftest import (fake_claude_on_path, init_git_repo,
                            run_git_repo_first)


def _init(repo: Path) -> Path:
    repo.mkdir(parents=True, exist_ok=True)
    run_git_repo_first(repo, "init", "-q", ".")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    run_git_repo_first(repo, "config", "commit.gpgsign", "false")
    (repo / "src.txt").write_text("original\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-qm", "init")
    return repo


class _St:
    def __init__(self, repo: Path, data: dict | None = None) -> None:
        self.repo_root = repo
        self.run_id = "r1"
        self.data = data if data is not None else {}
        self.saves = 0

    def save(self) -> None:
        self.saves += 1


@pytest.fixture
def armed(leerie: ModuleType, tmp_path: Path,
          monkeypatch: pytest.MonkeyPatch) -> tuple[Path, _St, list[str]]:
    """A repo, a State with a baseline, a die() that fails the test, and a
    captured log."""
    repo = _init(tmp_path / "repo")
    st = _St(repo)
    st.data["repo_state_before_planning"] = asyncio.run(
        leerie._snapshot_repo_state(str(repo)))
    logs: list[str] = []
    monkeypatch.setattr(leerie, "log", logs.append)
    monkeypatch.setattr(leerie, "die",
                        lambda m, code=1: pytest.fail(
                            f"default mode must not die: {m}"))
    return repo, st, logs


def _check(leerie: ModuleType, st: _St, phase: str = "phase 2 (plan)",
           porcelain_only: bool = False) -> None:
    return asyncio.run(leerie._assert_repo_unchanged(
        st, phase, porcelain_only=porcelain_only))


def _warnings(logs: list[str]) -> list[str]:
    return [m for m in logs if m.startswith("WARNING: your real checkout")]


class TestWarnsAndContinues:
    def test_the_incident_shape_warns_with_the_operator_hint(
            self, leerie, armed):
        """The triggering run: report files copied into the checkout during
        phase 2. It must continue, name each file and the phase, call them
        likely the operator's, and point at the flag."""
        repo, st, logs = armed
        (repo / "recon-next-task-a.md").write_text("a\n")
        (repo / "recon-next-task-b.md").write_text("b\n")
        _check(leerie, st)
        [w] = _warnings(logs)
        assert "phase 2 (plan)" in w
        assert "recon-next-task-a.md" in w
        assert "recon-next-task-b.md" in w
        assert "most likely your own" in w
        assert "may have escaped" not in w
        assert "--stop-on-repo-change" in w

    def test_a_tracked_edit_warns_with_the_escape_hint(self, leerie, armed):
        repo, st, logs = armed
        (repo / "src.txt").write_text("TAMPERED\n")
        _check(leerie, st)
        [w] = _warnings(logs)
        assert "src.txt" in w
        assert "may have escaped" in w
        assert f"git -C {repo} status" in w
        assert "most likely your own" not in w

    def test_a_commit_during_planning_warns(self, leerie, armed):
        repo, st, logs = armed
        (repo / "src.txt").write_text("moved\n")
        run_git_repo_first(repo, "commit", "-qam", "operator commit")
        _check(leerie, st)
        [w] = _warnings(logs)
        assert "HEAD moved" in w
        assert "may have escaped" in w

    def test_a_clean_tree_logs_nothing(self, leerie, armed):
        """Anti-vacuity: a sentinel that warned unconditionally would pass
        every test above."""
        _, st, logs = armed
        _check(leerie, st)
        assert _warnings(logs) == []
        assert "repo_change_warned" not in st.data


class TestWarnsOnce:
    def test_a_persisting_delta_is_warned_once(self, leerie, armed):
        """The baseline is never refreshed, so the same delta reappears at
        every later phase and wave."""
        repo, st, logs = armed
        (repo / "notes.md").write_text("x\n")
        _check(leerie, st, "phase 2 (plan)")
        _check(leerie, st, "phase 2.5 (reconcile)")
        _check(leerie, st, "phase 3 (filters)")
        assert len(_warnings(logs)) == 1
        warned = st.data["repo_change_warned"]
        assert list(warned) == ["working tree changed: ?? notes.md"]
        assert warned["working tree changed: ?? notes.md"] == \
            leerie._delta_fingerprint(
                repo, "working tree changed: ?? notes.md")
        assert st.saves == 1

    def test_a_new_delta_warns_alone(self, leerie, armed):
        repo, st, logs = armed
        (repo / "first.md").write_text("x\n")
        _check(leerie, st, "phase 2 (plan)")
        (repo / "second.md").write_text("y\n")
        _check(leerie, st, "phase 3 (filters)")
        first, second = _warnings(logs)
        assert "first.md" in first
        assert "second.md" in second
        assert "first.md" not in second
        assert "phase 3 (filters)" in second

    def test_the_hint_classifies_only_the_new_deltas(self, leerie, armed):
        """The hint is classified on the NEW deltas only: an already-warned
        tracked edit must not keep framing a later untracked addition as a
        possible escape."""
        repo, st, logs = armed
        (repo / "src.txt").write_text("TAMPERED\n")
        _check(leerie, st, "phase 2 (plan)")
        (repo / "mine.md").write_text("x\n")
        _check(leerie, st, "phase 3 (filters)")
        first, second = _warnings(logs)
        assert "may have escaped" in first
        assert "mine.md" in second and "most likely your own" in second


class TestRewarnsWhenContentMoves:
    """Warn-once must not become warn-never: a write to a path the operator
    already touched (the shape of a later worker escape) re-warns."""

    def test_a_re_edit_of_a_warned_file_re_warns(self, leerie, armed):
        repo, st, logs = armed
        (repo / "src.txt").write_text("operator edit\n")
        _check(leerie, st, "phase 2 (plan)")
        _check(leerie, st, "phase 2.5 (reconcile)")
        # A different size, so the re-warn never rests on mtime granularity.
        (repo / "src.txt").write_text("a later, longer write\n")
        _check(leerie, st, "phase 5 (wave 1)", porcelain_only=True)
        first, second = _warnings(logs)
        assert "src.txt" in first and "changed again" not in first
        assert "src.txt (changed again)" in second
        assert "phase 5 (wave 1)" in second

    def test_a_new_file_in_a_warned_untracked_dir_re_warns(
            self, leerie, armed):
        """Porcelain collapses an untracked directory to `?? dir/`, so the
        delta string alone never changes as files land inside it."""
        repo, st, logs = armed
        (repo / "notes").mkdir()
        (repo / "notes" / "a.md").write_text("a\n")
        _check(leerie, st, "phase 2 (plan)")
        (repo / "notes" / "b.md").write_text("b\n")
        _check(leerie, st, "phase 3 (filters)")
        first, second = _warnings(logs)
        assert "?? notes/" in first
        assert "?? notes/ (changed again)" in second

    def test_fingerprint(self, leerie, tmp_path):
        repo = _init(tmp_path / "repo")
        fp = leerie._delta_fingerprint
        st_ = os.lstat(repo / "src.txt")
        assert fp(repo, "working tree changed:  M src.txt") == \
            f"{st_.st_size}:{st_.st_mtime_ns}:{st_.st_ino}"
        assert fp(repo, "working tree changed: ?? gone.md") == "absent"
        assert fp(repo, "HEAD moved: aaa -> bbb") == ""
        (repo / "new.txt").write_text("n\n")
        new_st = os.lstat(repo / "new.txt")
        assert fp(repo, "working tree changed: R  src.txt -> new.txt") == \
            f"{new_st.st_size}:{new_st.st_mtime_ns}:{new_st.st_ino}"

    def test_fingerprint_never_reads_contents(
            self, leerie, tmp_path, monkeypatch):
        """A multi-GB untracked file must cost one lstat per check, not a
        full read on the event loop."""
        repo = _init(tmp_path / "repo")
        (repo / "big.bin").write_bytes(b"\0" * 1024)

        def no_open(*_a: object, **_k: object) -> None:
            raise AssertionError("fingerprint opened a file")

        monkeypatch.setattr("builtins.open", no_open)
        assert leerie._delta_fingerprint(
            repo, "working tree changed: ?? big.bin").startswith("1024:")

    def test_quoted_paths_resolve(self, leerie, tmp_path):
        """Porcelain C-quotes non-ASCII names; ` -> ` splits only a
        rename. An untracked `x -> src.txt` must not fingerprint
        `src.txt`."""
        repo = _init(tmp_path / "repo")
        (repo / "café.md").write_text("c\n")
        porcelain = run_git_repo_first(
            repo, "status", "--porcelain").stdout.splitlines()
        line = next(ln for ln in porcelain if ln.startswith("?? "))
        assert line == '?? "caf\\303\\251.md"'
        assert leerie._delta_fingerprint(
            repo, f"working tree changed: {line}") != "absent"
        (repo / "x -> src.txt").write_text("y\n")
        own = leerie._delta_fingerprint(
            repo, 'working tree changed: ?? "x -> src.txt"')
        assert own != leerie._delta_fingerprint(
            repo, "working tree changed:  M src.txt")
        assert own != "absent"

    def test_ignored_churn_in_a_warned_dir_does_not_re_warn(
            self, leerie, armed):
        """Build output inside an untracked directory is gitignored; its
        churn is not a change to the operator's directory."""
        repo, st, logs = armed
        (repo / ".gitignore").write_text("*.log\n")
        run_git_repo_first(repo, "add", ".gitignore")
        run_git_repo_first(repo, "commit", "-qm", "ignore logs")
        st.data["repo_state_before_planning"] = asyncio.run(
            leerie._snapshot_repo_state(str(repo)))
        (repo / "work").mkdir()
        (repo / "work" / "plan.md").write_text("p\n")
        _check(leerie, st, "phase 2 (plan)")
        (repo / "work" / "server.log").write_text("x" * 50)
        _check(leerie, st, "phase 3 (filters)")
        assert len(_warnings(logs)) == 1


class TestResumeFromListForm:
    def test_list_written_by_an_earlier_build_is_adopted(self, leerie, armed):
        """An earlier build of this feature stored the warned deltas as a
        list. A resume must neither crash nor repeat those warnings."""
        repo, st, logs = armed
        (repo / "notes.md").write_text("x\n")
        st.data["repo_change_warned"] = ["working tree changed: ?? notes.md"]
        _check(leerie, st, "phase 3 (filters)")
        assert _warnings(logs) == []
        warned = st.data["repo_change_warned"]
        assert isinstance(warned, dict)
        assert warned["working tree changed: ?? notes.md"] == \
            leerie._delta_fingerprint(
                repo, "working tree changed: ?? notes.md")


class TestBaselineUntouched:
    def test_a_warning_does_not_refresh_the_baseline(self, leerie, armed):
        """`_acceptance_validity_base` reads the baseline `head`; laundering
        a moved HEAD into it would change which tree held-out sets are
        validated against."""
        repo, st, logs = armed
        before = copy.deepcopy(st.data["repo_state_before_planning"])
        (repo / "src.txt").write_text("moved\n")
        run_git_repo_first(repo, "commit", "-qam", "operator commit")
        (repo / "extra.md").write_text("x\n")
        _check(leerie, st)
        assert _warnings(logs)
        assert st.data["repo_state_before_planning"] == before


class TestFlagRestoresTheHalt:
    def test_flag_makes_a_delta_die(self, leerie, tmp_path, monkeypatch):
        """Discriminating partner of TestWarnsAndContinues: same delta, flag
        on, must die — and must not have logged the warning first."""
        repo = _init(tmp_path / "repo")
        st = _St(repo, {"stop_on_repo_change": True})
        st.data["repo_state_before_planning"] = asyncio.run(
            leerie._snapshot_repo_state(str(repo)))
        logs: list[str] = []
        monkeypatch.setattr(leerie, "log", logs.append)
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        (repo / "recon-next-task.md").write_text("x\n")
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        assert "recon-next-task.md" in str(ei.value)
        assert _warnings(logs) == []

    @pytest.mark.parametrize("change", ["untracked", "commit"])
    def test_die_names_the_way_to_continue(
            self, leerie, tmp_path, monkeypatch, change):
        """The baseline is never refreshed, so a resume with the flag still
        set stops again on the same change — and no restore can undo a
        pull. Both framings must name the path that works."""
        repo = _init(tmp_path / "repo")
        st = _St(repo, {"stop_on_repo_change": True})
        st.data["repo_state_before_planning"] = asyncio.run(
            leerie._snapshot_repo_state(str(repo)))
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        if change == "untracked":
            (repo / "mine.md").write_text("x\n")
        else:
            (repo / "src.txt").write_text("pulled\n")
            run_git_repo_first(repo, "commit", "-qam", "pull")
        with pytest.raises(SystemExit) as ei:
            _check(leerie, st)
        msg = str(ei.value)
        assert "resume without the flag" in msg
        assert "LEERIE_STOP_ON_REPO_CHANGE=0" in msg
        assert "./leerie resume r1" in msg

    @pytest.mark.parametrize("halt", [False, True])
    def test_execute_wave_call_shape_halts_on_commits_only_with_flag(
            self, leerie, tmp_path, monkeypatch, halt):
        """phase_execute passes `_execute_check_porcelain_only(st)` (pinned
        in tests/test_work_sentinel.py). Driven through that real helper: a
        plain pull mid-execute is silent by default and dies under the flag
        ("commits or files")."""
        repo = _init(tmp_path / "repo")
        st = _St(repo, {"stop_on_repo_change": halt})
        st.data["repo_state_before_planning"] = asyncio.run(
            leerie._snapshot_repo_state(str(repo)))
        logs: list[str] = []
        monkeypatch.setattr(leerie, "log", logs.append)
        monkeypatch.setattr(leerie, "die",
                            lambda m, code=1: (_ for _ in ()).throw(
                                SystemExit(m)))
        (repo / "src.txt").write_text("upstream\n")
        run_git_repo_first(repo, "commit", "-qam", "pull")
        porcelain_only = leerie._execute_check_porcelain_only(st)
        if halt:
            with pytest.raises(SystemExit) as ei:
                _check(leerie, st, "phase 5 (wave 1)", porcelain_only)
            assert "HEAD moved" in str(ei.value)
        else:
            _check(leerie, st, "phase 5 (wave 1)", porcelain_only)
            assert _warnings(logs) == []


# --- resolver precedence ----------------------------------------------------

@pytest.fixture
def repo_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("LEERIE_STOP_ON_REPO_CHANGE", raising=False)
    return tmp_path


class TestResolver:
    def test_default_is_off(self, leerie, repo_root):
        assert leerie.resolve_stop_on_repo_change(
            repo_root, cli_value=False) is False

    def test_cli_wins_over_disagreeing_env_and_file(
            self, leerie, repo_root, monkeypatch):
        monkeypatch.setenv("LEERIE_STOP_ON_REPO_CHANGE", "0")
        (repo_root / "leerie.toml").write_text(
            "stop_on_repo_change = false\n")
        assert leerie.resolve_stop_on_repo_change(
            repo_root, cli_value=True) is True

    def test_env_wins_over_disagreeing_file(
            self, leerie, repo_root, monkeypatch):
        (repo_root / "leerie.toml").write_text(
            "stop_on_repo_change = true\n")
        monkeypatch.setenv("LEERIE_STOP_ON_REPO_CHANGE", "false")
        assert leerie.resolve_stop_on_repo_change(
            repo_root, cli_value=False) is False

    def test_file_alone_turns_it_on(self, leerie, repo_root):
        (repo_root / "leerie.toml").write_text(
            "stop_on_repo_change = true\n")
        assert leerie.resolve_stop_on_repo_change(
            repo_root, cli_value=False) is True

    def test_env_garbage_dies(self, leerie, repo_root, monkeypatch):
        monkeypatch.setenv("LEERIE_STOP_ON_REPO_CHANGE", "maybe")
        with pytest.raises(SystemExit):
            leerie.resolve_stop_on_repo_change(repo_root, cli_value=False)


# --- main() wiring -------------------------------------------------------------

class _ReachedOrchestrate(Exception):
    pass


def _resolved_by_main(leerie: ModuleType, monkeypatch: pytest.MonkeyPatch,
                      tmp_path: Path,
                      extra_argv: tuple[str, ...] = ()) -> bool:
    """Run the real `main()` up to `_orchestrate` (harness shape from
    tests/test_main_cli_wiring.py) and return the `stop_on_repo_change` it
    resolved onto `args` — which `_run_phases` then seeds into state."""
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    repo = tmp_path / "repo"
    init_git_repo(repo)
    fake_claude_on_path(tmp_path, monkeypatch)
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(tmp_path / "state"))
    seen: dict[str, bool] = {}

    async def _capture(args: object, *_a: object, **_k: object) -> None:
        seen["v"] = args.stop_on_repo_change
        raise _ReachedOrchestrate()

    monkeypatch.setattr(leerie, "_orchestrate", _capture)
    monkeypatch.setattr(sys, "argv",
                        ["leerie", "--run-id", "run-wiring", *extra_argv])
    with pytest.raises(_ReachedOrchestrate):
        leerie.main()
    return seen["v"]


class TestMainWiring:
    def test_default_is_off(self, leerie, monkeypatch, tmp_path):
        monkeypatch.delenv("LEERIE_STOP_ON_REPO_CHANGE", raising=False)
        assert _resolved_by_main(leerie, monkeypatch, tmp_path) is False

    def test_env_alone_turns_it_on(self, leerie, monkeypatch, tmp_path):
        monkeypatch.setenv("LEERIE_STOP_ON_REPO_CHANGE", "1")
        assert _resolved_by_main(leerie, monkeypatch, tmp_path) is True

    def test_flag_turns_it_on(self, leerie, monkeypatch, tmp_path):
        monkeypatch.delenv("LEERIE_STOP_ON_REPO_CHANGE", raising=False)
        assert _resolved_by_main(
            leerie, monkeypatch, tmp_path,
            ("--stop-on-repo-change",)) is True
