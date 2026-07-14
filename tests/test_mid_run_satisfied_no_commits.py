"""Tests for the mid-run sibling-satisfied no-commits rescue
(DESIGN §8 *The mid-run sibling case*).

The defect: a test-only subtask whose entire deliverable a *sibling subtask*
already committed to the run branch (in an earlier wave, same run) reaches its
implementer, correctly reports `complete` with nothing to commit, and the
mechanical `check_branch_has_commits` no-op gate fails it as `no_commits`. The
plan-time `filter_satisfied_subtasks` probe judged the BASE tree and could not
see the sibling's mid-run commit. A retry reproduces the identical no-op (the
work exists on the branch the subtask is measured against), the retry cap is
exhausted, and the wave dies — a deterministic loop that repeats on `--resume`.

The fix (`settle_subtask`): before failing a no-commits `complete`, re-run the
`satisfied_probe` against the subtask's `success_criteria_seed` on the
run-branch HEAD via `probe_criteria_satisfied_on_head`. If satisfied, settle
`complete` (recorded in `dropped_subtasks` with reason
`already_satisfied_mid_run`); if not, the existing `no_commits` retry path is
unchanged.

These tests pin the helper's contract (stubbed `claude_p`, mirroring
test_filter_satisfied_subtasks.py) and source-couple the settle wiring
(mirroring test_empty_handoff_keeps_committed_work.py) so the fix can't be
silently reverted.
"""
from __future__ import annotations

import asyncio
import inspect
import subprocess
from pathlib import Path


def _git(path, *args):
    subprocess.run(["git", *args], cwd=str(path), check=True,
                   capture_output=True, text=True)


_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-midrun"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {
        "telemetry": {"calls": 0, "cost_usd": 0.0,
                      "input_tokens": 0, "output_tokens": 0},
        "verbosity": "quiet",
        "worker_count": 0,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


def _sub(sid, **kw):
    s = {"id": sid, "title": sid, "success_criteria_seed": f"{sid} done"}
    s.update(kw)
    return s


def _patch_probe(leerie, monkeypatch, verdict):
    """Patch claude_p so the HEAD re-probe returns `verdict` (a dict with at
    least {satisfied}), or raises WorkerError when verdict == "CRASH". Records
    the cwd the probe was invoked with so we can assert it ran against the
    worktree (HEAD), not some other dir."""
    seen = {}

    async def fake_claude_p(*, user_prompt, sid, cwd=None, **_kw):
        seen["sid"] = sid
        seen["cwd"] = cwd
        if verdict == "CRASH":
            raise leerie.WorkerError("probe boom")
        return verdict

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    # load_prompt reads prompts/satisfied_probe.md off disk; keep it cheap and
    # decoupled from the real prompt text.
    monkeypatch.setattr(leerie, "load_prompt", lambda *_a, **_k: "SYS")
    return seen


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# probe_criteria_satisfied_on_head — the helper contract
# ---------------------------------------------------------------------------

def test_satisfied_on_head_returns_drop_record(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    seen = _patch_probe(leerie, monkeypatch, {
        "satisfied": True, "evidence": "sibling committed the test file",
        "checked": ["tests/test_x.py"]})
    drop = _run(leerie.probe_criteria_satisfied_on_head(
        _sub("test-003"), str(tmp_path / "wt"), st, _CAPS, _MODELS, _EFFORTS))
    assert drop is not None
    assert drop["reason"] == "already_satisfied_mid_run"
    assert drop["evidence"] == "sibling committed the test file"
    assert drop["checked"] == ["tests/test_x.py"]
    # probed against the worktree (HEAD), and under a distinct sid namespace
    assert seen["cwd"] == str(tmp_path / "wt")
    assert seen["sid"] == "satisfied_probe-head-test-003"


def test_not_satisfied_returns_none(leerie, tmp_path, monkeypatch):
    # A genuine lazy/broken no-op: probe says not satisfied → no rescue, the
    # subtask must fall through to the existing retry path.
    st = _make_state(leerie, tmp_path / "run")
    _patch_probe(leerie, monkeypatch, {"satisfied": False, "evidence": "gap"})
    drop = _run(leerie.probe_criteria_satisfied_on_head(
        _sub("test-003"), str(tmp_path / "wt"), st, _CAPS, _MODELS, _EFFORTS))
    assert drop is None


def test_no_criterion_never_probes(leerie, tmp_path, monkeypatch):
    # Without a success_criteria_seed there is nothing to judge → return None
    # WITHOUT calling claude_p (which would raise if reached).
    st = _make_state(leerie, tmp_path / "run")

    async def boom(**_kw):
        raise AssertionError("claude_p must not be called with no criterion")
    monkeypatch.setattr(leerie, "claude_p", boom)
    monkeypatch.setattr(leerie, "load_prompt", lambda *_a, **_k: "SYS")

    sub = _sub("test-003")
    sub["success_criteria_seed"] = "   "  # blank/whitespace → no criterion
    drop = _run(leerie.probe_criteria_satisfied_on_head(
        sub, str(tmp_path / "wt"), st, _CAPS, _MODELS, _EFFORTS))
    assert drop is None


def test_probe_crash_fails_safe_to_none(leerie, tmp_path, monkeypatch):
    # A probe WorkerError must NOT rescue — fail safe toward the retryable
    # no-op path (a probe crash must never mask a real lazy no-op).
    st = _make_state(leerie, tmp_path / "run")
    _patch_probe(leerie, monkeypatch, "CRASH")
    drop = _run(leerie.probe_criteria_satisfied_on_head(
        _sub("test-003"), str(tmp_path / "wt"), st, _CAPS, _MODELS, _EFFORTS))
    assert drop is None


def test_budget_exhaustion_propagates(leerie, tmp_path, monkeypatch):
    # bump_workers' WorkerError (budget exhaustion) is the hard backstop and
    # must propagate, not be swallowed into a silent no-rescue. Mirrors
    # test_filter_satisfied_subtasks.py's budget test.
    st = _make_state(leerie, tmp_path / "run")
    caps = {"max_parallel": 4, "max_total_workers": 0}

    async def unreached(**_kw):
        raise AssertionError("claude_p must not run once budget is exhausted")
    monkeypatch.setattr(leerie, "claude_p", unreached)
    monkeypatch.setattr(leerie, "load_prompt", lambda *_a, **_k: "SYS")

    try:
        _run(leerie.probe_criteria_satisfied_on_head(
            _sub("test-003"), str(tmp_path / "wt"), st, caps, _MODELS, _EFFORTS))
        raised = False
    except leerie.WorkerError:
        raised = True
    assert raised, "budget-exhaustion WorkerError must propagate"


# ---------------------------------------------------------------------------
# settle_subtask wiring — source-coupling guards (mirror
# test_empty_handoff_keeps_committed_work.py::TestSettleWiring)
# ---------------------------------------------------------------------------

class TestSettleWiring:
    def test_settle_calls_head_reprobe_on_no_commits(self, leerie):
        src = inspect.getsource(leerie.settle_subtask)
        assert "probe_criteria_satisfied_on_head(" in src

    def test_reprobe_precedes_the_fail_call(self, leerie):
        """The HEAD re-probe must run BEFORE the no-commits fail() — fail()
        burns the retry cap and eventually kills the wave, exactly the loop
        the rescue prevents. Guard against the FIRST fail() at or after the
        probe (the commit-path fail), since an earlier empty_handoff fail()
        also exists above the probe."""
        src = inspect.getsource(leerie.settle_subtask)
        probe_idx = src.find("probe_criteria_satisfied_on_head(")
        assert probe_idx != -1
        fail_after = src.find("done = await fail(kind, message)", probe_idx)
        assert fail_after != -1, "the commit-path fail() must follow the probe"
        assert probe_idx < fail_after

    def test_reprobe_gated_on_no_commits_not_empty_handoff(self, leerie):
        """The re-probe lives on the check_branch_has_commits (`commit_err`)
        branch, distinct from the empty_handoff rescue above it."""
        src = inspect.getsource(leerie.settle_subtask)
        probe_idx = src.find("probe_criteria_satisfied_on_head(")
        # the nearest guard above the probe is the `if commit_err ...` branch
        region = src[:probe_idx]
        last_if = region.rfind("if commit_err")
        assert last_if != -1 and last_if < probe_idx

    @staticmethod
    def _rescue_region(leerie):
        """The source between the HEAD-probe call and its rescued `complete`
        return — the exact rescue block, sliced by structure (not a fixed
        char window) so adding comments can't silently break these guards."""
        src = inspect.getsource(leerie.settle_subtask)
        probe_idx = src.find("probe_criteria_satisfied_on_head(")
        assert probe_idx != -1, "probe call missing from settle_subtask"
        ret_idx = src.find(
            'return {"subtask_id": sid, "status": "complete"', probe_idx)
        assert ret_idx != -1, "rescued complete-return missing after the probe"
        # include the return line itself
        return src[probe_idx:src.find("\n", ret_idx)]

    def test_rescued_result_is_complete_with_drop_record(self, leerie):
        src = inspect.getsource(leerie.settle_subtask)
        # settle records the helper's drop and marks the subtask complete
        assert "dropped_subtasks" in src
        region = self._rescue_region(leerie)
        assert 'st.data.setdefault("dropped_subtasks", {})[sid] = drop' in region
        assert 'return {"subtask_id": sid, "status": "complete"' in region

    def test_rescue_writes_conformance_sentinel(self, leerie):
        """The rescue early-returns before the normal conformance block, so it
        must write a `conformance[sid]` sentinel itself — otherwise
        `_get_progress` (which keys `in_conformer` on a missing conformance
        entry for a `complete` subtask) would count the rescued subtask as
        perpetually in-conformer. Source-coupling guard: the write must appear
        between the probe call and the rescued return."""
        region = self._rescue_region(leerie)
        assert 'setdefault("conformance", {})[sid]' in region, \
            "rescue must write a conformance sentinel before returning"
        # and it must precede the rescued return in that region
        conf_idx = region.find('setdefault("conformance", {})[sid]')
        ret_idx = region.find('return {"subtask_id": sid, "status": "complete"')
        assert conf_idx != -1 and ret_idx != -1 and conf_idx < ret_idx


class TestRescueScopeProvenanceAgnostic:
    """DESIGN §8 *Scope*: the rescue judges WHETHER the criteria are met on
    HEAD, not WHO met them — so it fires identically whether a sibling
    committed the deliverable this run or it was already on the base tree.
    `probe_criteria_satisfied_on_head` therefore returns a drop record on any
    `satisfied: True`, with no provenance check."""

    def test_helper_rescues_regardless_of_provenance(self, leerie, tmp_path,
                                                     monkeypatch):
        # The probe just reports satisfied=True; the helper does not (and
        # cannot) inspect whether a sibling vs the base tree satisfied it.
        st = _make_state(leerie, tmp_path / "run")
        _patch_probe(leerie, monkeypatch, {
            "satisfied": True,
            "evidence": "criteria already met on base tree (no sibling)",
            "checked": ["src/x.py"]})
        drop = _run(leerie.probe_criteria_satisfied_on_head(
            _sub("feat-009"), str(tmp_path / "wt"), st, _CAPS, _MODELS,
            _EFFORTS))
        assert drop is not None
        assert drop["reason"] == "already_satisfied_mid_run"
        # no provenance field / no sibling-only gate
        assert "evidence" in drop


class TestRescuedSubtaskIntegratesAsNoOp:
    """The rescued subtask returns `status: complete` and flows into
    `integrate_wave` → `integrate.sh` → `git merge --no-ff <branch>`. Its
    branch has ZERO commits ahead of the run branch (that is why it was
    rescued), so the merge must be a clean no-op (exit 0, no commit, no
    conflict) — otherwise a rescued subtask would break integration. This
    pins the git-level property `integrate.sh` relies on."""

    def test_no_ff_merge_of_zero_commit_branch_is_clean_noop(self, tmp_path):
        d = tmp_path
        _git(d, "init", "-q", "-b", "main")
        _git(d, "config", "user.email", "t@leerie.local")
        _git(d, "config", "user.name", "leerie test")
        (d / "base.py").write_text("base\n")
        _git(d, "add", "-A")
        _git(d, "commit", "-qm", "base")
        # run branch and the rescued subtask branch at the SAME sha
        # (subtask committed nothing — its deliverable is already on run).
        _git(d, "branch", "run")
        _git(d, "branch", "leerie/subtasks/r/test-003")
        _git(d, "checkout", "-q", "run")
        head_before = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(d),
            capture_output=True, text=True).stdout.strip()
        # exactly integrate.sh's merge command
        r = subprocess.run(
            ["git", "merge", "--no-ff", "-m", "leerie: integrate test-003",
             "leerie/subtasks/r/test-003"],
            cwd=str(d), capture_output=True, text=True)
        assert r.returncode == 0, f"merge must be clean: {r.stderr}"
        head_after = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=str(d),
            capture_output=True, text=True).stdout.strip()
        # up-to-date: no new commit, no conflict
        assert head_before == head_after
        assert "up to date" in (r.stdout + r.stderr).lower()
