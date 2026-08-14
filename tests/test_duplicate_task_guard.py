"""Two runs must not work the same task at the same time.

`run_id` is the container id, so two launches of byte-identical task text are
invisible to each other. Measured: one task ran twice, three minutes apart, for
**$72.21** across 173 worker calls, producing two architecturally incompatible
branches — one dynamic test route versus two static ones, no persistence versus
a Prisma migration — with **14 files in collision** and two PRs whose merge
order matters.

See docs/POSTMORTEM-2026-08-14.md, F10.
"""
from __future__ import annotations

import asyncio
import inspect
import json
import subprocess
from pathlib import Path

import pytest

from tests.test_resume_planning_reentry import _args, _caps


def _run_json(root: Path, run_id: str, **fields) -> Path:
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "run.json").write_text(json.dumps({"run_id": run_id, **fields}))
    return d


class TestFingerprint:
    def test_identical_text_hashes_identically(self, leerie):
        t = "# Integrations\n\nMake the panel usable.\n"
        assert leerie._task_fingerprint(t) == leerie._task_fingerprint(t)

    def test_different_text_differs(self, leerie):
        assert (leerie._task_fingerprint("a")
                != leerie._task_fingerprint("a "))

    @pytest.mark.parametrize("bad", ["", None])
    def test_degenerate_input_does_not_raise(self, leerie, bad):
        assert isinstance(leerie._task_fingerprint(bad), str)


class TestLiveDuplicateDetection:
    def test_finds_a_live_run_on_the_same_task(self, leerie, tmp_path):
        _run_json(tmp_path, "other", task_sha256="abc", started_at="t")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == ["other"]

    def test_ignores_itself(self, leerie, tmp_path):
        _run_json(tmp_path, "mine", task_sha256="abc", started_at="t")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == []

    def test_ignores_a_different_task(self, leerie, tmp_path):
        _run_json(tmp_path, "other", task_sha256="zzz", started_at="t")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == []

    @pytest.mark.parametrize("terminal", ["finished_at", "killed_at",
                                          "paused_at"])
    def test_ignores_a_run_that_is_not_live(self, leerie, tmp_path, terminal):
        """A completed re-run of the same brief is ordinary and says nothing."""
        _run_json(tmp_path, "other", task_sha256="abc", **{terminal: "t"})
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == []

    def test_reports_every_live_duplicate(self, leerie, tmp_path):
        _run_json(tmp_path, "a", task_sha256="abc")
        _run_json(tmp_path, "b", task_sha256="abc")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == ["a", "b"]

    def test_an_unreadable_sidecar_is_skipped_not_fatal(self, leerie, tmp_path):
        d = tmp_path / "runs" / "broken"
        d.mkdir(parents=True)
        (d / "run.json").write_text("{not json")
        _run_json(tmp_path, "other", task_sha256="abc")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == ["other"]

    def test_missing_runs_dir_is_empty(self, leerie, tmp_path):
        assert leerie._live_duplicate_runs(tmp_path, "mine", "abc") == []

    def test_empty_fingerprint_matches_nothing(self, leerie, tmp_path):
        """A run with no recorded fingerprint must not match every other one."""
        _run_json(tmp_path, "other", task_sha256="")
        assert leerie._live_duplicate_runs(tmp_path, "mine", "") == []


class _ReachedClassify(Exception):
    """Raised by the stubbed `phase_classify` — the first worker call after the
    duplicate gate, so reaching it means the gate let the run through."""


def _drive_to_the_gate(leerie, monkeypatch, tmp_path, *, task="a task",
                       siblings=()):
    """Run the fresh branch of `_run_phases` up to the first worker.

    Returns True when the gate allowed the run through, and raises SystemExit
    when it refused. Behavioural, because every other test here is a source
    substring: `if False and _dupes and …` leaves all four of them green with
    the guard entirely disabled.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-qm", "init"], cwd=repo, check=True)
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / "mine"
    (run_dir / "subtasks").mkdir(parents=True)
    for name, fields in siblings:
        _run_json(leerie_root, name, **fields)

    st = leerie.State(leerie_root, "mine", repo_root=repo)
    monkeypatch.setattr(
        leerie, "_enforce_and_record_cgroup_containment", lambda *a, **k: None)

    async def _boom(*_a, **_k):
        raise _ReachedClassify()

    monkeypatch.setattr(leerie, "phase_classify", _boom)

    try:
        asyncio.run(leerie._run_phases(
            _args(resume=False, task=task), _caps(leerie), leerie_root,
            st, "both", "quiet", {}, {}))
    except _ReachedClassify:
        return True
    return False


class TestTheGateActuallyRefuses:
    """The behavioural half. `TestWiring` below pins the shape of the message
    and the ordering; neither can see a gate that has been switched off."""

    def test_a_live_duplicate_stops_the_run(self, leerie, monkeypatch, tmp_path):
        fp = leerie._task_fingerprint("a task")
        with pytest.raises(SystemExit):
            _drive_to_the_gate(
                leerie, monkeypatch, tmp_path,
                siblings=[("other", {"task_sha256": fp, "started_at": "t"})])

    def test_no_duplicate_proceeds(self, leerie, monkeypatch, tmp_path):
        """Anti-vacuity: the gate must not stop every run."""
        assert _drive_to_the_gate(
            leerie, monkeypatch, tmp_path,
            siblings=[("other", {"task_sha256": "zzz", "started_at": "t"})])

    def test_a_finished_duplicate_proceeds(self, leerie, monkeypatch, tmp_path):
        """A completed re-run of the same brief is ordinary."""
        fp = leerie._task_fingerprint("a task")
        assert _drive_to_the_gate(
            leerie, monkeypatch, tmp_path,
            siblings=[("other", {"task_sha256": fp, "finished_at": "t"})])

    def test_the_escape_hatch_lets_it_through(self, leerie, monkeypatch,
                                              tmp_path):
        fp = leerie._task_fingerprint("a task")
        monkeypatch.setenv("LEERIE_ALLOW_DUPLICATE_TASK", "1")
        assert _drive_to_the_gate(
            leerie, monkeypatch, tmp_path,
            siblings=[("other", {"task_sha256": fp, "started_at": "t"})])

    def test_an_unset_hatch_is_not_a_bypass(self, leerie, monkeypatch,
                                            tmp_path):
        """The hatch matches an explicit truthy value; anything else refuses."""
        fp = leerie._task_fingerprint("a task")
        monkeypatch.setenv("LEERIE_ALLOW_DUPLICATE_TASK", "0")
        with pytest.raises(SystemExit):
            _drive_to_the_gate(
                leerie, monkeypatch, tmp_path,
                siblings=[("other", {"task_sha256": fp, "started_at": "t"})])


class TestWiring:
    def test_the_hash_is_recorded_on_run_json(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "task_sha256=_task_fingerprint(task)" in src

    def test_the_guard_runs_before_the_first_worker(self, leerie):
        """Ordering is the point — after phase_classify the money is spent."""
        src = inspect.getsource(leerie._run_phases)
        guard = src.index("_live_duplicate_runs(")
        first_worker = src.index("await phase_classify(")
        assert guard < first_worker

    def test_the_die_names_the_other_run_and_the_escape_hatch(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        i = src.index("_live_duplicate_runs(")
        window = src[i:i + 1800]
        assert "leerie attach" in window and "leerie kill" in window, (
            "the message must tell the operator what to do about the run it "
            "found, not just that it found one")
        assert "LEERIE_ALLOW_DUPLICATE_TASK" in window

    def test_the_escape_hatch_downgrades_to_a_warning(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        i = src.index("_live_duplicate_runs(")
        window = src[i:i + 1800]
        assert "elif _dupes:" in window, (
            "with the hatch set the run proceeds, but the duplicate must "
            "still be announced")
