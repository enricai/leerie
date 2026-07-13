"""Tests for the orchestrator-level conformance phase loop —
_run_conformance_phase() in leerie.py (DESIGN §9 *Post-work
conformance*).

The phase is advisory: it never raises and never returns a status that
fails the subtask. All failure modes (malformed conformer output,
WorkerError, protected-path violations on conformer commits,
criteria-lock mismatch, exhausted rounds) surface as `warnings` entries.

The tests stub `run_conformer` with a queue of canned results and use a
real git worktree on disk so the criteria-lock and check_diff_scope
re-runs against the conformer's commits exercise the real code paths.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path


def _write_log(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _bash_event(tid, cmd):
    return {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash",
         "input": {"command": cmd}}]}}


def _result_event(tid, text):
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}

import pytest


# --- shared fixtures -------------------------------------------------------

@pytest.fixture(autouse=True)
def _restore_run_conformer(leerie):
    """Snapshot `leerie.run_conformer` before each test and restore after.

    `_stub_run_conformer` below rebinds `leerie.run_conformer = _stub`
    directly (not via monkeypatch). Without this autouse fixture, the
    stub leaks into the shared session-scoped `leerie` fixture and
    breaks any later test that calls `inspect.getsource(leerie.run_conformer)`
    — it sees the stub's source, not the real one. This caught
    `tests/test_worker_timeout_handoff.py::test_run_conformer_*`
    failing under full-suite collection but passing when run in
    isolation."""
    original = leerie.run_conformer
    yield
    leerie.run_conformer = original


def _run(cmd, cwd, check=True):
    """Run a git command, asserting success unless check=False."""
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def env(leerie, tmp_path):
    """A real git repo with a .leerie run dir, one subtask worktree
    branched off a 'run branch', and the criteria locked.

    Returns a dict of every path / object the phase needs to run."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)

    # The "run branch" the implementer branched off of.
    run_id = "fix-001-abcdef"
    run_branch = f"leerie/runs/{run_id}"
    _run(["git", "checkout", "-q", "-b", run_branch], cwd=repo)

    # Set up .leerie coordination state first so the subtask worktree
    # can live under run_dir/worktrees/<sid> — the canonical location
    # settle_subtask uses (`worktree = str(leerie_dir / "worktrees" / sid)`).
    sid = "t1"
    subtask_branch = f"leerie/subtasks/{run_id}/{sid}"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "criteria").mkdir()
    (run_dir / "logs").mkdir()
    (run_dir / "worktrees").mkdir()
    worktree = run_dir / "worktrees" / sid
    _run(["git", "worktree", "add", "-q", "-b", subtask_branch,
          str(worktree), run_branch], cwd=repo)

    # Simulate an implementer commit on the subtask branch.
    (worktree / "src.py").write_text("def f():\n    pass\n")
    _run(["git", "add", "-A"], cwd=worktree)
    _run(["git", "commit", "-q", "-m", "implementer: add f()"], cwd=worktree)

    (run_dir / "criteria" / f"{sid}.md").write_text(
        "# Criteria\n- f() returns None\n")
    subtask = {"id": sid, "files_likely_touched": ["src.py"]}
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(subtask))

    State = leerie.State
    st = State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()

    caps = dict(leerie.DEFAULT_CAPS)
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    # No --effort pinned for these tests — predates the effort selector
    # and exercises the "inherit Claude default" branch.
    efforts: dict[str, str | None] = {}

    return {
        "leerie": leerie, "repo": repo, "worktree": worktree,
        "sid": sid, "subtask": subtask, "run_dir": run_dir, "st": st,
        "caps": caps, "models": models, "efforts": efforts,
        "run_branch": run_branch,
    }


def _stub_run_conformer(leerie_mod, results_queue, *, commits=None):
    """Patch leerie.run_conformer to return queued results in order. If
    `commits` is provided, the matching index's stub also writes a file
    and commits it to the worktree before returning."""
    commits = commits or {}
    state = {"i": 0, "feedbacks": []}

    async def _stub(sid, leerie_dir, worktree, caps, st, models, efforts,
                    *, rules_files, blt_commands, diff_base,
                    extra_feedback=None):
        i = state["i"]
        state["i"] += 1
        state["feedbacks"].append(extra_feedback)
        action = commits.get(i)
        if action is not None:
            action(Path(worktree))
        return results_queue[i] if i < len(results_queue) else None

    leerie_mod.run_conformer = _stub
    return state


def _clean_result(sid="t1", **overrides):
    """A conformer result that is well-formed and clean (no residuals,
    no failing build/lint/tests)."""
    base = {
        "subtask_id": sid,
        "rules_files_read": [],
        "rule_violations_fixed": [],
        "rule_violations_residual": [],
        "docs_updates": [],
        "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "", "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "", "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "", "summary": ""},
        "summary": "nothing to do",
    }
    base.update(overrides)
    return base


# --- happy path: clean result, no warnings, single round -------------------

def test_clean_result_exits_after_one_round(env, monkeypatch):
    c = env["leerie"]
    state = _stub_run_conformer(c, [_clean_result()])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == 1
    assert res is not None
    assert warnings == []


# --- malformed output: surfaced as warning, loop breaks --------------------

def test_malformed_result_breaks_loop_with_warning(env):
    c = env["leerie"]
    # residual without files_read — cross-field invariant violation.
    bad = _clean_result(rule_violations_residual=[{"rule": "x",
                                                   "why_not_fixed": "y"}])
    state = _stub_run_conformer(c, [bad])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == 1  # loop did not retry on malformed output
    assert res == bad
    assert any("malformed" in w for w in warnings)


# --- crash (None): surfaced as warning, loop breaks -----------------------

def test_worker_crash_surfaces_as_warning(env):
    c = env["leerie"]
    state = _stub_run_conformer(c, [None])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == 1
    assert res is None
    assert any("crashed" in w for w in warnings)


# --- protected path: conformer commits get rolled back --------------------

def test_protected_path_commit_is_rolled_back(env):
    c = env["leerie"]

    def _bad_commit(wt: Path):
        """Simulate a conformer that wrote to .claude/ — a protected path."""
        (wt / ".claude").mkdir(exist_ok=True)
        (wt / ".claude" / "x").write_text("bad\n")
        _run(["git", "add", "-A"], cwd=wt)
        _run(["git", "commit", "-q", "-m",
              "conformer: BAD touched protected path"], cwd=wt)

    state = _stub_run_conformer(c, [_clean_result()], commits={0: _bad_commit})
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["worktree"],
        capture_output=True, text=True).stdout.strip()

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    # The protected-path commit must be gone from HEAD after rollback.
    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["worktree"],
        capture_output=True, text=True).stdout.strip()
    assert head_after == head_before, "rollback didn't reset HEAD"
    assert any("protected-path" in w for w in warnings)
    assert state["i"] == 1  # loop broke after rollback


# --- clobber-survival guard (DESIGN §9) -----------------------------------

def _clobber_impl_file(wt: Path):
    """Simulate a conformer that reverted an implementer-owned file to the
    base version (deletes the implementer's content) and commits it — the
    data-loss signature."""
    (wt / "src.py").unlink()  # implementer added src.py; base didn't have it
    _run(["git", "add", "-A"], cwd=wt)
    _run(["git", "commit", "-q", "-m", "conformer: revert src.py"], cwd=wt)


def test_strict_mode_clobber_blocks_even_when_blt_clean(env):
    """A clobber under --strict-conformer must block the subtask even
    though the conformer's own result is BLT-clean with no residuals —
    it is the severest residual, so it blocks like any other strict one."""
    c = env["leerie"]
    env["caps"]["strict_conformer"] = True
    state = _stub_run_conformer(
        c, [_clean_result()], commits={0: _clobber_impl_file})

    res, warnings, blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    # Blocked despite a clean conformer result.
    assert blocked is not None
    assert "clobber" in blocked.lower() or "reverted/deleted" in blocked
    assert "src.py" in blocked
    assert any("reverted/deleted" in w for w in warnings)
    # And the clobber was rolled back (implementer's src.py restored).
    assert (env["worktree"] / "src.py").exists()


def test_advisory_mode_clobber_warns_but_does_not_block(env):
    """In advisory (default) mode a clobber only warns — no block, no
    auto-rollback (a legit revert-to-base is indistinguishable from a
    clobber, and the phase is advisory by design)."""
    c = env["leerie"]
    assert env["caps"].get("strict_conformer") in (None, False)
    state = _stub_run_conformer(
        c, [_clean_result()], commits={0: _clobber_impl_file})

    res, warnings, blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert blocked is None  # advisory: never blocks
    assert any("reverted/deleted" in w for w in warnings)


# --- rounds cap is respected ----------------------------------------------

def test_rounds_cap_respected_with_residuals(env):
    """If the conformer keeps returning a clean-looking result that the
    orchestrator considers non-clean (e.g. build failed), the loop runs
    up to `caps[conformance_rounds]` times. Residuals are surfaced as
    warnings; nothing escalates to failed/blocked."""
    c = env["leerie"]
    failing = _clean_result(
        rules_files_read=["README.md"],
        rule_violations_residual=[{"rule": "r", "why_not_fixed": "still bad"}],
        build={"ran": True, "passed": False, "command": "make",
               "summary": "oops"},
    )
    state = _stub_run_conformer(c, [failing, failing, failing, failing])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == env["caps"]["conformance_rounds"]
    assert any("rule-residual" in w for w in warnings)
    assert any("build-failed" in w for w in warnings)


# --- the phase never returns failure --------------------------------------

def test_phase_never_returns_failed_status(env):
    """No matter what the conformer does, _run_conformance_phase returns
    (result_or_none, warnings_list) — never a status that could escalate
    the subtask to failed/blocked."""
    c = env["leerie"]
    # Mix of crash, malformed, bad commits, residuals — none of these are
    # supposed to fail the subtask.
    state = _stub_run_conformer(c, [None])
    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))
    # Returned shape: 2-tuple, second element is a list.
    assert isinstance(warnings, list)
    # res may be None on crash; that's the intended advisory signal.
    assert res is None or isinstance(res, dict)


# --- commit-prefix observability ------------------------------------------

def test_unprefixed_conformer_commits_surface_as_warnings(env):
    """A conformer that commits with a subject NOT prefixed `conformer:`
    must surface a warning, but must NOT trigger rollback. The commit
    content is still valid; only the discipline is lapsed."""
    c = env["leerie"]

    def _unprefixed_commit(wt: Path):
        (wt / "docs.txt").write_text("doc\n")
        _run(["git", "add", "-A"], cwd=wt)
        _run(["git", "commit", "-q", "-m", "docs: update without prefix"],
             cwd=wt)

    state = _stub_run_conformer(c, [_clean_result()],
                                commits={0: _unprefixed_commit})
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["worktree"],
        capture_output=True, text=True).stdout.strip()

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["worktree"],
        capture_output=True, text=True).stdout.strip()
    assert head_after != head_before, \
        "unprefixed commits must NOT be rolled back — they're still valid work"
    assert any("missing `conformer:` prefix" in w for w in warnings)
    # The commit subject should appear in the warning text for traceability.
    assert any("docs: update without prefix" in w for w in warnings)


def test_prefixed_conformer_commits_do_not_warn(env):
    """A conformer that follows the discipline produces no prefix
    warning."""
    c = env["leerie"]

    def _good_commit(wt: Path):
        (wt / "notes.txt").write_text("notes\n")
        _run(["git", "add", "-A"], cwd=wt)
        _run(["git", "commit", "-q", "-m", "conformer: add release notes"],
             cwd=wt)

    state = _stub_run_conformer(c, [_clean_result()],
                                commits={0: _good_commit})

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert not any("missing `conformer:` prefix" in w for w in warnings)


# --- worker budget exhaustion is advisory, not fatal ----------------------

def test_bump_workers_exhaustion_surfaces_as_warning(env, monkeypatch):
    """When `max_total_workers` is exhausted at conformer-spawn time,
    `bump_workers` raises WorkerError. The conformance phase must catch
    that and surface it as a `conformance_warnings` entry — it must NOT
    propagate up and crash the subtask. This pins the fix for the
    third-pass audit bug: bump_workers placement inside run_conformer's
    try block."""
    c = env["leerie"]
    # Force the cap to a value already exceeded by st.data["worker_count"].
    env["st"].data["worker_count"] = 100
    env["st"].save()
    env["caps"]["max_total_workers"] = 1  # any positive value < worker_count

    # Make claude_p obviously detectable in case we incorrectly fall
    # through to it (we shouldn't — bump_workers should raise first).
    async def _should_not_be_called(*args, **kwargs):
        raise AssertionError("claude_p was called despite budget exhaustion")
    monkeypatch.setattr(c, "claude_p", _should_not_be_called)

    # Call run_conformer directly. It should catch the budget WorkerError
    # raised by bump_workers and return None.
    result = asyncio.run(c.run_conformer(
        env["sid"], env["run_dir"], str(env["worktree"]), env["caps"],
        env["st"], env["models"], env["efforts"],
        rules_files=[], blt_commands={"build": "", "lint": "", "test": ""},
        diff_base="dummy"))
    assert result is None, "budget-exhausted conformer must return None"

    # Now exercise the full phase loop to confirm the warning surfaces.
    env["st"].data["worker_count"] = 100
    env["st"].save()
    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))
    assert res is None
    assert any("crashed" in w for w in warnings), \
        "budget exhaustion should surface as a 'crashed' advisory warning"


# --- outer contract: settle_subtask never escalates conformance failures --

def test_settle_subtask_never_escalates_on_conformer_crash(env, monkeypatch):
    """The outer contract — settle_subtask must NEVER return a result
    with status `failed` or `blocked` due to a conformance failure. This
    tightens the contract verification beyond the inner-helper tests:
    those verify _run_conformance_phase returns advisory warnings; this
    verifies the caller actually honors that and doesn't re-escalate."""
    c = env["leerie"]

    # Stub run_implementer to return a clean `complete` result without
    # actually spawning a worker. The worktree already has the implementer's
    # commit from the env fixture, so the per-subtask gates will pass.
    async def _stub_implementer(sid, leerie_dir, caps, st, models, efforts,
                                continuation=False, note=""):
        return {
            "subtask_id": sid,
            "status": "complete",
            "criteria_results": [
                {"criterion": "f() exists", "met": True, "evidence": "src.py"},
            ],
        }
    monkeypatch.setattr(c, "run_implementer", _stub_implementer)

    # Conformer crashes (returns None). _run_conformance_phase surfaces
    # this as a warning; settle_subtask must still return `complete`.
    _stub_run_conformer(c, [None])

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"], env["models"], env["efforts"]))

    assert res["status"] == "complete", \
        f"conformer crash escalated subtask to {res['status']!r}"
    assert res["status"] not in ("failed", "blocked")
    # The conformance failure should still be surfaced — just not fatally.
    assert res.get("conformance_warnings"), \
        "conformer crash must produce conformance_warnings on the result"


def test_settle_subtask_never_escalates_on_conformer_residuals(env, monkeypatch):
    """Same outer contract under a different failure mode: the conformer
    reports residuals and failing build/lint/tests round after round
    until the cap is hit. The subtask still returns `complete`."""
    c = env["leerie"]

    async def _stub_implementer(sid, leerie_dir, caps, st, models, efforts,
                                continuation=False, note=""):
        return {
            "subtask_id": sid,
            "status": "complete",
            "criteria_results": [
                {"criterion": "f() exists", "met": True, "evidence": "src.py"},
            ],
        }
    monkeypatch.setattr(c, "run_implementer", _stub_implementer)

    failing = _clean_result(
        rules_files_read=["README.md"],
        rule_violations_residual=[{"rule": "r", "why_not_fixed": "still bad"}],
        tests={"ran": True, "passed": False, "command": "pytest",
               "summary": "1 failed"},
    )
    _stub_run_conformer(c, [failing] * 10)

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"], env["models"], env["efforts"]))

    assert res["status"] == "complete"
    assert res.get("conformance_warnings"), \
        "residuals must surface as warnings on the subtask result"


# --- the phase survives unexpected exceptions (fourth-pass audit follow-up) -

def test_settle_subtask_survives_unexpected_exception_in_conformance(env, monkeypatch):
    """The conformance phase is documented as 'never raises a workflow
    error.' But underlying `run_proc` calls `asyncio.create_subprocess_exec`,
    which raises FileNotFoundError when cwd is missing. The settle_subtask
    splice has a broad try/except specifically to honor the advisory
    contract for any unexpected exception — including this one. Verify the
    subtask still returns `complete` with a warning."""
    c = env["leerie"]

    # Stub run_implementer to short-circuit to a clean complete result.
    async def _stub_implementer(sid, leerie_dir, caps, st, models, efforts,
                                continuation=False, note=""):
        return {
            "subtask_id": sid,
            "status": "complete",
            "criteria_results": [
                {"criterion": "f() exists", "met": True, "evidence": "src.py"},
            ],
        }
    monkeypatch.setattr(c, "run_implementer", _stub_implementer)

    # Stub _run_conformance_phase to raise a synthetic unexpected exception
    # that mimics the realistic FileNotFoundError-from-missing-worktree case.
    async def _explode(*args, **kwargs):
        raise FileNotFoundError(2, "No such file or directory",
                                "/nonexistent/worktree")
    monkeypatch.setattr(c, "_run_conformance_phase", _explode)

    res = asyncio.run(c.settle_subtask(
        env["sid"], env["run_dir"], env["caps"], env["st"], env["models"], env["efforts"]))

    assert res["status"] == "complete", \
        f"unexpected exception in conformance escalated subtask to {res['status']!r}"
    warnings = res.get("conformance_warnings") or []
    assert warnings, "the exception must surface as a conformance_warnings entry"
    assert any("FileNotFoundError" in w for w in warnings)


# --- dirty-state warning before rollback (fourth-pass audit follow-up) -----

def test_protected_path_rollback_warns_about_discarded_uncommitted(env):
    """When the conformer commits to a protected path AND leaves
    uncommitted changes to tracked files, the rollback (git reset --hard)
    will silently erase those uncommitted scribbles. The phase must surface
    a warning naming the discarded files BEFORE rolling back."""
    c = env["leerie"]

    def _bad_with_uncommitted(wt: Path):
        # Commit a protected-path change (will trigger rollback).
        (wt / ".claude").mkdir(exist_ok=True)
        (wt / ".claude" / "x").write_text("bad\n")
        _run(["git", "add", "-A"], cwd=wt)
        _run(["git", "commit", "-q", "-m",
              "conformer: BAD touched protected path"], cwd=wt)
        # Now leave an uncommitted modification to a TRACKED file.
        # (`src.py` was committed by the env fixture.)
        (wt / "src.py").write_text(
            "def f():\n    pass\n\n# uncommitted scribble\n")

    _stub_run_conformer(c, [_clean_result()],
                        commits={0: _bad_with_uncommitted})

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    # The protected-path rollback warning should be present (as before).
    assert any("protected-path" in w for w in warnings)
    # AND a new warning should call out the discarded uncommitted file.
    assert any("discarding" in w and "src.py" in w for w in warnings), \
        f"expected a 'discarding' warning mentioning src.py; got {warnings!r}"


# --- Pattern B feedback injection ----------------------------------------- #

def test_pattern_b_bg_retry_injects_feedback_into_next_round(env):
    """When _emit_bash_axis_warnings detects an auto-backgrounded retry
    (Pattern B) in round 0, the orchestrator should inject structured
    feedback into round 1's extra_feedback parameter."""
    c = env["leerie"]
    # Two rounds: round 0 has residuals (not clean), round 1 is clean.
    dirty = _clean_result(
        build={"ran": True, "passed": False, "command": "npm run build",
               "summary": "fail"})
    state = _stub_run_conformer(c, [dirty, _clean_result()])

    # Write a log that triggers the Pattern B warning: a Bash command
    # auto-backgrounded, then immediately retried with a fresh Bash.
    log_dir = env["run_dir"] / "logs"
    _write_log(log_dir / f"{env['sid']}-conformer.log", [
        _bash_event("a1", "npm run build"),
        _result_event("a1",
                      "Command running in background with ID: bg42. "
                      "Output is being written to: /tmp/bg42.output"),
        _bash_event("a2", "npm run build"),
        _result_event("a2", "build passed"),
    ])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == 2, "expected 2 conformer rounds"
    assert state["feedbacks"][0] is None, \
        "round 0 should have no prior feedback"
    assert state["feedbacks"][1] is not None, \
        "round 1 should receive Pattern B feedback"
    assert "auto-backgrounded" in state["feedbacks"][1]


def test_pattern_a_multi_invocation_does_not_inject_feedback(env):
    """When the conformer runs the same axis multiple times (Pattern A:
    legitimate progressive testing), no feedback should be injected."""
    c = env["leerie"]
    dirty = _clean_result(
        build={"ran": True, "passed": False, "command": "npm run build",
               "summary": "fail"})
    state = _stub_run_conformer(c, [dirty, _clean_result()])

    # Write a log that triggers Pattern A only (multiple invocations,
    # no auto-backgrounding).
    log_dir = env["run_dir"] / "logs"
    _write_log(log_dir / f"{env['sid']}-conformer.log", [
        _bash_event("a1", "npm test -- --testPathPattern=foo"),
        _result_event("a1", "1 test passed"),
        _bash_event("a2", "npm test"),
        _result_event("a2", "42 tests passed"),
    ])

    res, warnings, _blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert state["i"] == 2, "expected 2 conformer rounds"
    assert state["feedbacks"][0] is None
    assert state["feedbacks"][1] is None, \
        "Pattern A (progressive testing) should NOT inject feedback"


# --- strict-conformer mode -------------------------------------------------

def test_strict_conformer_blocks_on_residuals(env):
    """When caps["strict_conformer"] is True and residuals remain,
    blocked_reason must be a non-None string."""
    c = env["leerie"]
    failing = _clean_result(
        rule_violations_residual=[{"rule": "r", "why_not_fixed": "bad"}],
    )
    _stub_run_conformer(c, [failing, failing, failing])
    caps = dict(env["caps"])
    caps["strict_conformer"] = True

    _res, _warnings, blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        caps, env["st"], env["models"], env["efforts"]))

    assert blocked is not None
    assert "strict-conformer" in blocked


def test_strict_conformer_none_when_clean(env):
    """When strict mode is on but the result is clean, blocked_reason
    must be None."""
    c = env["leerie"]
    _stub_run_conformer(c, [_clean_result()])
    caps = dict(env["caps"])
    caps["strict_conformer"] = True

    _res, _warnings, blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        caps, env["st"], env["models"], env["efforts"]))

    assert blocked is None


def test_advisory_mode_never_blocks(env):
    """When strict_conformer is off (default), blocked_reason is always
    None regardless of residuals."""
    c = env["leerie"]
    failing = _clean_result(
        rule_violations_residual=[{"rule": "r", "why_not_fixed": "bad"}],
    )
    _stub_run_conformer(c, [failing, failing, failing])

    _res, _warnings, blocked = asyncio.run(c._run_conformance_phase(
        env["sid"], env["run_dir"], str(env["worktree"]), env["subtask"],
        env["caps"], env["st"], env["models"], env["efforts"]))

    assert blocked is None
