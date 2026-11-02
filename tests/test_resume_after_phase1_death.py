"""A run that died in phase 1 resumes at phase 1 (DESIGN §6 *Resumable
planning*: "the first phase is a phase like any other").

The defect (origin 16336c4e, 2026-08-14; review round 23 M2): `_run_phases`
refused any resume whose state held neither `waves` nor `categories` —
"cannot resume — state.json records neither progress nor a task" — although
`_validate_resume_state` had already required the `task`, and the comment
directly above the refusal called that state restartable. So every exit that
lands inside phase 1 and tells the operator to resume — the worker budget
("re-run with resume after raising --max-workers"), a usage-limit or
transport `WorkerError`, a context-overflow or disk-low pause, the rate-limit
auto-resume's own `exec` of `resume` — left a run that `resume` refused.

The invariant: a run whose state records its task resumes at its first
unpersisted phase, and that is classification when no phase persisted
anything. These tests drive the real `main()` twice — a fresh run that dies
inside the real `phase_classify`, then `resume` on the state it left — and
assert that the resume spawns the classifier, not that some message is
absent.
"""
from __future__ import annotations

import json
import sys

import pytest

from tests.test_main_exception_arms import repo  # noqa: F401  (fixture)


class _FirstSpawn(BaseException):
    """Out of every `except Exception` between `main()` and the spawn."""


def _arm(leerie, monkeypatch, on_spawn):
    """Stub the parts of a run that are other files' subjects — the kernel
    and runtime entry checks, the CLI preflight, cgroup containment, the
    run's own repository — and route every worker spawn to `on_spawn`. The
    phases themselves (`_run_phases`, the real `phase_classify` and its
    budget bump) run for real."""
    monkeypatch.setattr(leerie, "_require_landlock", lambda: None)
    monkeypatch.setattr(leerie, "_require_heldout_sandbox", lambda d: None)
    monkeypatch.setattr(leerie, "_check_claude_cli_version",
                        lambda *a, **k: None)

    async def _noop(*a, **kw):
        return None
    monkeypatch.setattr(leerie, "preflight", _noop)
    monkeypatch.setattr(leerie, "_ensure_run_repo_or_die", _noop)
    monkeypatch.setattr(leerie, "_backstop_capture_prior_runs", _noop)
    monkeypatch.setattr(leerie, "_enforce_and_record_cgroup_containment",
                        lambda st, allow_uncapped: None)
    spawns: list[dict] = []

    async def claude_p(*a, **kw):
        spawns.append(kw)
        return on_spawn(kw)
    monkeypatch.setattr(leerie, "claude_p", claude_p)
    return spawns


def _main(leerie, monkeypatch, argv: list[str]):
    monkeypatch.setattr(sys, "argv", ["leerie", *argv])
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    try:
        leerie.main()
    except SystemExit as e:
        return ("exit", e.code)
    except _FirstSpawn:
        return ("spawned", None)
    return ("returned", None)


def _state(repo, run_id):
    return json.loads(
        (repo / ".leerie" / "runs" / run_id / "state.json").read_text())


def _die_in_classify(leerie, monkeypatch, repo, run_id, on_spawn,
                     extra=()):
    """A fresh run whose first worker spawn — the classifier's — fails."""
    spawns = _arm(leerie, monkeypatch, on_spawn)
    got = _main(leerie, monkeypatch,
                ["a trivial task", "--run-id", run_id, "--skip-smoke",
                 "--no-push", *extra])
    # The resume below is a second process in life. Here it shares this one,
    # and the dead run's `State` is still referenced by the recorded spawn,
    # so its run-directory lock is released as that process's exit would.
    for kw in spawns:
        kw["st"].release_lock()
    data = _state(repo, run_id)
    # The state a phase-1 death leaves: the task and phase 1's entry stamp,
    # and no phase output at all — exactly the state the old gate refused.
    assert data["task"] == "a trivial task"
    assert data["current_phase"] == "phase 1: classify"
    assert "categories" not in data and "waves" not in data
    assert not any(k.startswith("plans_after_") for k in data)
    return got, data


def _resume_reaches_classify(leerie, monkeypatch, repo, run_id,
                             extra=()):
    def stop(kw):
        raise _FirstSpawn()
    spawns = _arm(leerie, monkeypatch, stop)
    got = _main(leerie, monkeypatch,
                ["resume", run_id, "--no-push", *extra])
    assert got == ("spawned", None), (
        f"resume of a run that died in phase 1 must re-enter it, got {got}")
    assert spawns[0]["schema_key"] == "classifier"
    return spawns


def test_budget_death_in_classify_resumes_after_raising_the_cap(
        leerie, monkeypatch, repo, capsys):
    """The M2 reproduction: the budget `WorkerError` says "re-run with resume
    after raising --max-workers", and that is what this does."""
    def overspend(kw):
        # A second bill against a budget of one: the real `State.bump_workers`
        # raises the real budget error from inside phase 1.
        kw["st"].bump_workers(kw["caps"])
        raise AssertionError("bump_workers did not enforce the budget")

    got, data = _die_in_classify(leerie, monkeypatch, repo, "p1-budget",
                                 overspend, extra=["--max-workers", "1"])
    assert got == ("exit", 1)
    err = capsys.readouterr().err
    assert "worker budget exhausted (1)" in err
    assert "re-run with resume after raising --max-workers" in err
    spent = data["worker_count"]

    _resume_reaches_classify(leerie, monkeypatch, repo, "p1-budget",
                             extra=["--max-workers", "200"])
    after = _state(repo, "p1-budget")
    # Resumed, not restarted: the spend so far is carried, and the record of
    # this leerie's resume is appended to the original start's.
    assert after["worker_count"] > spent
    assert after["started_at"] == data["started_at"]
    assert len(after["leerie_versions"]) == len(data["leerie_versions"]) + 1


@pytest.mark.parametrize("make_exc,code", [
    # Usage limit after the auth-retry window — "Run `leerie resume` once
    # the window clears".
    (lambda leerie: leerie.WorkerError(
        "Claude API returned auth/quota error after ~1s of retries"), 1),
    # "then resume with: leerie resume <id>"
    (lambda leerie: leerie.ContextOverflow("Prompt is too long",
                                           from_worker=True), "LOCKED"),
    (lambda leerie: leerie.DiskLowSpace("disk headroom low"), "LOCKED"),
], ids=["worker-error", "context-overflow", "disk-low"])
def test_every_resumable_phase1_exit_resumes(leerie, monkeypatch, repo,
                                             make_exc, code):
    """The rule, over main()'s exit arms whose message tells the operator to
    resume: whatever ended phase 1, the state it leaves re-enters phase 1."""
    run_id = "p1-" + type(make_exc(leerie)).__name__.lower()

    def fail(kw):
        raise make_exc(leerie)

    got, _ = _die_in_classify(leerie, monkeypatch, repo, run_id, fail)
    want = leerie.EXIT_LOCKED if code == "LOCKED" else code
    assert got == ("exit", want)
    _resume_reaches_classify(leerie, monkeypatch, repo, run_id)


def test_a_state_with_no_task_is_still_refused(leerie, monkeypatch, repo,
                                               capsys):
    """The task is the one input resume needs; a state without one is refused
    by name, before anything is spawned."""
    run_id = "p1-no-task"
    run_dir = repo / ".leerie" / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "state.json").write_text(json.dumps({
        "started_at": "2026-11-01T00:00:00Z", "worker_count": 2,
        "current_phase": "phase 1: classify",
        "run_layout": leerie.RUN_LAYOUT_VERSION}))

    def stop(kw):
        raise _FirstSpawn()
    spawns = _arm(leerie, monkeypatch, stop)
    got = _main(leerie, monkeypatch, ["resume", run_id, "--no-push"])
    assert got == ("exit", 1)
    assert spawns == []
    err = capsys.readouterr().err
    assert "state.json has no usable 'task' — cannot resume" in err
