"""Behavioral coverage for branches of `_settle_subtask`
(orchestrator/leerie.py:29271-29892) not exercised by the existing
per-concern test files (test_worktree_failure_not_fatal.py,
test_oom_naming.py, test_settle_subtask_completeness_gate.py,
test_mid_run_satisfied_no_commits.py, ...).

Reuses the `env` fixture from tests/test_oom_naming.py (real git repo +
`.leerie` run dir + real `State`) rather than re-deriving worktree/branch
plumbing -- see that file's docstring for why a real repo is used instead of
mocking git.
"""
from __future__ import annotations

import asyncio
import subprocess

from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


def _run(coro):
    return asyncio.run(coro)


def _git(*args, cwd):
    return subprocess.run(["git", *args], cwd=str(cwd),
                          capture_output=True, text=True)


_VALID_CHECKPOINT_TEXT = (
    "# Checkpoint: t1\n"
    "## Frozen success criteria\n- Add coverage for the fallback path\n"
    "## Current status\nStill working; worktree branch has no new commits.\n"
    "## Files touched\nNone yet.\n"
    "## Decisions made\nnone\n"
    "## Evidence gate status\nroot_cause=8.0\n"
    "## Next action\nkeep going\n"
    "## Open unknowns\nnone\n"
)


def _settle(leerie_mod, env, *, caps_overrides=None):  # noqa: F811
    caps = dict(env["caps"])
    if caps_overrides:
        caps.update(caps_overrides)
    return _run(leerie_mod._settle_subtask(
        env["sid"], env["run_dir"], caps, env["st"],
        env["models"], env["efforts"]))


# --- infrastructure-failure retry-in-place: PidExhaustedError / OomKilledError

def test_pid_exhausted_retries_in_place_then_terminates(env, monkeypatch):  # noqa: F811
    """PidExhaustedError is an infrastructure kind: retried WITHOUT a
    worktree reset (that would `git branch -D` a partially-completed
    attempt) and WITHOUT a corrective note, bounded by failed_retries."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append((continuation, note))
        raise leerie_mod.PidExhaustedError(
            f"worker {sid_} exhausted its PID table (pids.max)")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 2})

    assert res["status"] == "failed"
    assert len(calls) == 3, f"expected 1 + 2 retries, got {len(calls)}"
    for continuation, note in calls:
        assert continuation is False
        assert note == ""


def test_oom_killed_retries_in_place_then_terminates(env, monkeypatch):  # noqa: F811
    """Same infrastructure-retry contract as PidExhaustedError, for a
    kernel-killed worker."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(1)
        raise leerie_mod.OomKilledError(
            f"worker {sid_} was OOM-killed on `pnpm run build`")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 1})

    assert res["status"] == "failed"
    assert len(calls) == 2, f"expected 1 + 1 retry, got {len(calls)}"


def test_pid_exhausted_respects_zero_retry_budget(env, monkeypatch):  # noqa: F811
    """ANTI-VACUITY: with no budget, PidExhaustedError still terminates
    after exactly one attempt (retryable does not mean unconditional)."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(1)
        raise leerie_mod.PidExhaustedError("exhausted")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 0})

    assert res["status"] == "failed"
    assert len(calls) == 1


# --- generic WorkerError escaping _run_script (not claude_p) -------------

def test_generic_worker_error_is_routed_through_fail(env, monkeypatch):  # noqa: F811
    """A bare WorkerError (e.g. from a `_run_script` call outside
    `_run_implementer`'s own `except WorkerError` guard around `claude_p`)
    must be caught in `_settle_subtask`'s `while True` loop and routed
    through `fail("broken", ...)` rather than escaping unhandled (which
    would reach `_gather_or_cancel` and kill the whole wave). "broken" is
    NOT in `_WORKER_RETRYABLE_KINDS`/`_INFRASTRUCTURE_FAILURE_KINDS`, so
    `_retryable_failure` reports it non-retryable — exactly one attempt,
    even with retry budget available."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(note)
        raise leerie_mod.WorkerError(f"some infra step failed for {sid_}")
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 3})

    assert res["status"] == "failed"
    assert "some infra step failed" in res["summary"]
    assert len(calls) == 1, (
        f"a bare WorkerError ('broken') must be non-retryable, got "
        f"{len(calls)} attempts")


# --- worker self-reported "failed" status ---------------------------------

def test_worker_reported_failed_status_is_routed_through_fail(env, monkeypatch):  # noqa: F811
    """A worker that returns status='failed' itself (not raised, not an
    invariant violation) is routed through `fail("broken", ...)` with the
    worker's own summary as the reason."""
    leerie_mod = env["leerie"]

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        return {"subtask_id": sid_, "status": "failed",
                "summary": "could not reproduce the reported symptom"}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 0})

    assert res["status"] == "failed"
    assert res["summary"] == "could not reproduce the reported symptom"


def test_worker_reported_failed_with_no_summary_hits_invariant_check(
        env, monkeypatch):  # noqa: F811
    """A `failed` result with no summary is caught by `_validate_result`'s
    cross-field invariant BEFORE `_settle_subtask`'s own status dispatch —
    it never reaches the `status == "failed"` branch's own
    `"worker reported failure"` default, since a diagnosis-less failure is
    itself a self-contradictory result (kind='broken', non-retryable)."""
    leerie_mod = env["leerie"]

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        return {"subtask_id": sid_, "status": "failed"}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 0})

    assert res["status"] == "failed"
    assert "no diagnosis provided" in res["summary"]


# --- continuation-cap exhaustion: incomplete-handoff / needs-clarification -

def test_incomplete_handoff_exceeding_continuation_cap_is_blocked(
        env, monkeypatch):  # noqa: F811
    """A worker that keeps handing off (writing a valid checkpoint each
    time) past `subtask_continuations` terminates as blocked rather than
    looping forever."""
    leerie_mod = env["leerie"]
    ckpt = env["run_dir"] / "checkpoints" / f"{env['sid']}.md"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(_VALID_CHECKPOINT_TEXT)
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(1)
        return {"subtask_id": sid_, "status": "incomplete-handoff",
                "checkpoint_path": str(ckpt)}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)

    res = _settle(leerie_mod, env, caps_overrides={"subtask_continuations": 1})

    assert res["status"] == "blocked"
    assert "continuation cap" in res["blocker"]
    assert len(calls) == 2, f"expected 2 attempts, got {len(calls)}"


def test_needs_clarification_exceeding_continuation_cap_is_blocked(
        env, monkeypatch):  # noqa: F811
    """Same continuation-cap contract as incomplete-handoff, since
    needs-clarification shares the same subtask_continuations budget
    (DESIGN Sec.11: no extra "ask the user" allowance)."""
    leerie_mod = env["leerie"]
    ckpt = env["run_dir"] / "checkpoints" / f"{env['sid']}.md"
    ckpt.parent.mkdir(parents=True, exist_ok=True)
    ckpt.write_text(_VALID_CHECKPOINT_TEXT)
    calls: list = []

    async def _stub(sid_, leerie_dir, caps, st, models, efforts,
                    continuation=False, note=""):
        calls.append(1)
        return {"subtask_id": sid_, "status": "needs-clarification",
                "checkpoint_path": str(ckpt),
                "clarification_question": {
                    "id": f"{sid_}-q{len(calls)}",
                    "question": "which behavior did you mean?",
                    "why_underivable": "no doc names it"}}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub)
    surfaced: list = []

    def _stub_surface(sid_, question, checkpoint_path, st):
        surfaced.append(question)
    monkeypatch.setattr(leerie_mod, "_surface_clarification", _stub_surface)

    res = _settle(leerie_mod, env, caps_overrides={"subtask_continuations": 1})

    assert res["status"] == "blocked"
    assert "continuation cap" in res["blocker"]
    assert len(calls) == 2
    # The cap check runs BEFORE `_surface_clarification` on the call that
    # trips it, so only the first (accepted) round surfaces a question.
    assert len(surfaced) == 1


# --- mid-run sibling: no-commits "complete" claim rescued via _settle_subtask
#     (not just the standalone probe-helper tests in
#     test_mid_run_satisfied_no_commits.py) ---------------------------------

def test_no_commits_complete_settled_via_head_reprobe(env, monkeypatch):  # noqa: F811
    """A worker claims complete with nothing committed; the run-branch HEAD
    re-probe judges the criteria already met (a sibling committed the
    deliverable this run) -- `_settle_subtask` must settle it complete via
    `_settle_already_satisfied`, not fail it as a lazy no-op."""
    leerie_mod = env["leerie"]

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        return {"subtask_id": sid_, "status": "complete",
                "summary": "nothing to do", "criteria_results": [],
                "production_evidence": {"exercised": True, "how": "n/a",
                                         "observed": "n/a"}}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    async def _stub_probe(subtask, worktree_, st, caps, models, efforts,
                          label="post"):
        return {"satisfied": True, "evidence": "already on the run branch",
                "checked": ["src.py"]}
    monkeypatch.setattr(leerie_mod, "_probe_criteria_satisfied_on_head",
                        _stub_probe)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 0})

    assert res["status"] == "complete"
    assert env["st"].data["subtask_status"][env["sid"]] == "complete"


def test_no_commits_complete_falls_through_to_fail_when_probe_declines(
        env, monkeypatch):  # noqa: F811
    """ANTI-VACUITY: when the head re-probe returns None (genuinely not
    satisfied), the no-commits claim still fails normally rather than being
    settled."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(1)
        return {"subtask_id": sid_, "status": "complete",
                "summary": "nothing to do", "criteria_results": [],
                "production_evidence": {"exercised": True, "how": "n/a",
                                         "observed": "n/a"}}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    async def _stub_probe(subtask, worktree_, st, caps, models, efforts,
                          label="post"):
        return None
    monkeypatch.setattr(leerie_mod, "_probe_criteria_satisfied_on_head",
                        _stub_probe)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 0})

    assert res["status"] == "failed"
    assert len(calls) == 1


# --- scope violation: worker wrote outside allowed paths ------------------

def test_scope_violation_is_non_retryable_broken(env, monkeypatch):  # noqa: F811
    """A committed diff that touches a protected path (e.g. .git/ or
    outside every declared file) is `check_diff_scope`-flagged and routed
    through `fail("broken", ...)`, which is non-retryable -- the worker is
    broken, not merely unlucky."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(1)
        (env["worktree"] / "extra.py").write_text("x = 1\n")
        _git("add", "-A", cwd=env["worktree"])
        _git("commit", "-q", "-m", "work", cwd=env["worktree"])
        return {"subtask_id": sid_, "status": "complete",
                "summary": "done", "criteria_results": [],
                "production_evidence": {"exercised": True, "how": "n/a",
                                         "observed": "n/a"}}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    async def _stub_scope(sid_, worktree_, subtask, st):
        return "wrote to a protected path outside the declared scope"
    monkeypatch.setattr(leerie_mod, "check_diff_scope", _stub_scope)

    res = _settle(leerie_mod, env, caps_overrides={"failed_retries": 3})

    assert res["status"] == "failed"
    assert len(calls) == 1, (
        "scope violation ('broken') must be non-retryable -- only one "
        f"attempt expected, got {len(calls)}")
    assert "protected path" in res["summary"]


# --- _rescue_integrator_work: git-command failure branches ---------------

def _conflicted_staging(tmp_path):
    repo = tmp_path / "staging"
    repo.mkdir()
    _git("init", "-q", ".", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "f.txt").write_text("base\n")
    _git("add", ".", cwd=repo)
    _git("commit", "-qm", "base", cwd=repo)
    _git("checkout", "-qb", "side", cwd=repo)
    (repo / "f.txt").write_text("side\n")
    _git("commit", "-qam", "side", cwd=repo)
    _git("checkout", "-q", "-", cwd=repo)
    (repo / "f.txt").write_text("main\n")
    _git("commit", "-qam", "main", cwd=repo)
    _git("merge", "side", cwd=repo)
    assert (repo / ".git" / "MERGE_HEAD").exists()
    return repo


def _stub_run_proc_fail_on_matching_arg(leerie_mod, monkeypatch, needle):
    """Return an rc=1 fake result for the first `run_proc` call whose argv
    contains `needle`; delegate every other call to the real `run_proc`."""
    real = leerie_mod.run_proc

    class _FakeResult:
        def __init__(self):
            self.returncode = 1
            self.stdout = ""
            self.stderr = "simulated failure"

    async def _stub(cmd, **kw):
        if needle in cmd:
            return _FakeResult()
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie_mod, "run_proc", _stub)


def test_rescue_returns_none_when_add_fails(leerie, tmp_path, monkeypatch):
    repo = _conflicted_staging(tmp_path)
    (repo / "f.txt").write_text("RESOLVED\n")
    _stub_run_proc_fail_on_matching_arg(leerie, monkeypatch, "add")

    ref = _run(leerie._rescue_integrator_work(repo, "feat-x", "run1"))
    assert ref is None


def test_rescue_returns_none_when_write_tree_fails(leerie, tmp_path,
                                                    monkeypatch):
    repo = _conflicted_staging(tmp_path)
    (repo / "f.txt").write_text("RESOLVED\n")
    _stub_run_proc_fail_on_matching_arg(leerie, monkeypatch, "write-tree")

    ref = _run(leerie._rescue_integrator_work(repo, "feat-x", "run1"))
    assert ref is None


def test_rescue_returns_none_when_commit_tree_fails(leerie, tmp_path,
                                                     monkeypatch):
    repo = _conflicted_staging(tmp_path)
    (repo / "f.txt").write_text("RESOLVED\n")
    _stub_run_proc_fail_on_matching_arg(leerie, monkeypatch, "commit-tree")

    ref = _run(leerie._rescue_integrator_work(repo, "feat-x", "run1"))
    assert ref is None


def test_rescue_returns_none_when_update_ref_fails(leerie, tmp_path,
                                                    monkeypatch):
    repo = _conflicted_staging(tmp_path)
    (repo / "f.txt").write_text("RESOLVED\n")
    _stub_run_proc_fail_on_matching_arg(leerie, monkeypatch, "update-ref")

    ref = _run(leerie._rescue_integrator_work(repo, "feat-x", "run1"))
    assert ref is None
    show = _git("for-each-ref", "refs/leerie/rescue/run1/feat-x", cwd=repo)
    assert show.stdout.strip() == ""
