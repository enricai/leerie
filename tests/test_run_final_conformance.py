"""Tests for _run_final_conformance() — the whole-tree conformer pass on
the integrated staging worktree (DESIGN §6 *Worktree and integration
model*, final-tree pass paragraph).

This is the §9 conformer pointed at the merged tree with `DIFF_BASE`
set to the user's working branch instead of the run branch. Same
prompt, same schema, same advisory framing as the per-subtask phase.

The tests stub `leerie.claude_p` with a queue of canned results and
use a real on-disk staging worktree so the protected-path rollback
and the dirty-worktree observability exercise the real code paths.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_claude_p(leerie):
    """Snapshot leerie.claude_p before each test; restore after.

    The tests below rebind leerie.claude_p = _stub directly. Without
    this autouse fixture, the stub would leak into the shared
    session-scoped `leerie` fixture and break any later test that
    introspects the real claude_p."""
    original = leerie.claude_p
    yield
    leerie.claude_p = original


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def env(leerie, tmp_path):
    """A real git repo with a .leerie run dir and a staging worktree
    branched off the run branch (which itself branched off `main`)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _run(["git", "init", "-q", "-b", "main"], cwd=repo)
    _run(["git", "config", "user.email", "t@t"], cwd=repo)
    _run(["git", "config", "user.name", "t"], cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "initial"], cwd=repo)

    run_id = "fix-001-abcdef"
    run_branch = f"leerie/runs/{run_id}"
    _run(["git", "checkout", "-q", "-b", run_branch], cwd=repo)
    # Simulate the wave-integrated state: one commit on the run branch.
    (repo / "src.py").write_text("def f():\n    pass\n")
    _run(["git", "add", "-A"], cwd=repo)
    _run(["git", "commit", "-q", "-m", "wave-1: add f"], cwd=repo)

    # Move the primary checkout off the run branch before creating the
    # staging worktree on it — git refuses to check the same branch out
    # of two worktrees. This mirrors what the real orchestrator does:
    # `setup-run.sh` creates the staging worktree from a fresh process
    # that has not pre-checked-out the run branch.
    _run(["git", "checkout", "-q", "main"], cwd=repo)

    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "logs").mkdir()
    (run_dir / "worktrees").mkdir()
    staging = run_dir / "worktrees" / "staging"
    _run(["git", "worktree", "add", "-q", str(staging), run_branch],
         cwd=repo)

    State = leerie.State
    st = State(leerie_root, run_id)
    st.data = {
        "task": "x",
        "answers": {"source_of_truth": "codebase"},
        "working_branch": "main",
    }
    st.save()

    caps = dict(leerie.DEFAULT_CAPS)
    models = {w: "sonnet" for w in leerie.WORKER_TYPES}
    efforts: dict[str, str | None] = {w: None for w in leerie.WORKER_TYPES}

    return {
        "leerie": leerie, "repo": repo, "staging": staging,
        "run_dir": run_dir, "st": st, "caps": caps, "models": models,
        "efforts": efforts, "run_branch": run_branch,
    }


def _clean_result(**overrides):
    """A conformer result that is well-formed and clean."""
    base = {
        "subtask_id": "_final",
        "rules_files_read": [],
        "rule_violations_fixed": [],
        "rule_violations_residual": [],
        "docs_updates": [],
        "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "(none)",
                  "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "(none)",
                 "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "(none)",
                  "summary": ""},
        "summary": "nothing to do",
        # DESIGN §9 *Evidence must be production-grounded*: the whole-tree
        # pass runs `check_production_evidence` on its result, so a
        # well-formed conformer result now carries this. Without it the
        # "clean" result below is not clean — it warns, which is the
        # behaviour `test_clean_result_without_evidence_warns` pins.
        "production_evidence": {
            "exercised": True,
            "how": "pytest -q",
            "observed": "6252 passed",
        },
    }
    base.update(overrides)
    return base


def _stub_claude_p(leerie_mod, queue, *, commits=None):
    """Patch leerie.claude_p to return queued results. `commits[i]` is
    an optional callable that mutates the staging worktree on the i-th
    call (before the stub returns) so protected-path / dirty-worktree
    paths can be exercised."""
    commits = commits or {}
    state = {"i": 0}

    async def _stub(**kwargs):
        i = state["i"]
        state["i"] += 1
        if i in commits:
            commits[i](Path(kwargs["cwd"]))
        if i < len(queue):
            return queue[i]
        # Out of canned responses — return a clean result so a runaway
        # loop bound by conformance_rounds exits cleanly rather than
        # hanging.
        return _clean_result()

    leerie_mod.claude_p = _stub
    return state


# --- skip conditions ------------------------------------------------------

def test_skipped_when_staging_worktree_absent(env):
    c = env["leerie"]
    # Remove the staging worktree to trigger the skip branch.
    subprocess.run(["git", "worktree", "remove", "-f", str(env["staging"])],
                   cwd=env["repo"], capture_output=True, text=True)
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    # No conformance block was written.
    assert "_final" not in (env["st"].data.get("conformance") or {})


def test_skipped_when_working_branch_absent(env):
    c = env["leerie"]
    env["st"].data["working_branch"] = ""
    env["st"].save()
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    assert "_final" not in (env["st"].data.get("conformance") or {})


def test_skipped_on_resume_when_already_complete(env):
    """Resume idempotence: when `st.data["conformance"]["_final"]` is
    already populated, the pass short-circuits without spawning a
    worker. Without this guard, `resume` after the final pass
    completed would burn worker budget re-running the conformer and
    potentially overwrite a previously clean result with a different
    one. Mirrors the `completed_waves` gate in `phase_execute`."""
    c = env["leerie"]
    env["st"].data["conformance"] = {
        "_final": {"result": _clean_result(), "warnings": []},
    }
    env["st"].save()
    state = _stub_claude_p(c, [_clean_result()])

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    # claude_p must not have been called.
    assert state["i"] == 0
    # The pre-existing block was not overwritten.
    block = env["st"].data["conformance"]["_final"]
    assert block["warnings"] == []


# --- happy path: clean result, single round, state written ----------------

def test_clean_result_writes_final_block(env):
    c = env["leerie"]
    state = _stub_claude_p(c, [_clean_result()])

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    assert state["i"] == 1
    block = env["st"].data["conformance"]["_final"]
    assert block["result"] is not None
    assert block["result"]["subtask_id"] == "_final"
    assert block["warnings"] == []


def test_clean_result_without_evidence_warns(env):
    """Anti-vacuity partner to the test above, and the only BEHAVIOURAL
    coverage of the final pass's production-evidence check.

    Adding `production_evidence` to `_clean_result()` fixes CI; on its own it
    would merely silence the signal. Stripping the field from an otherwise
    identical result must produce the warning — that is what proves the
    fixture change reflects the check rather than hiding it.

    The whole-tree pass is where this matters most: on run `fa979580` it
    certified four inert fixes with `solution_defects: []` at confidence 8.5.
    Until now it was pinned only by source inspection
    (`test_final_conformance_checks_production_evidence`).
    """
    c = env["leerie"]
    result = _clean_result()
    del result["production_evidence"]
    _stub_claude_p(c, [result])

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    warnings = env["st"].data["conformance"]["_final"]["warnings"]
    assert any("NO_PRODUCTION_EVIDENCE" in w for w in warnings), warnings
    # Advisory, not blocking: the result is still recorded.
    assert env["st"].data["conformance"]["_final"]["result"] is not None


# --- malformed output: surfaced as warning, loop breaks -------------------

def test_malformed_result_surfaces_warning(env):
    c = env["leerie"]
    # residual without files_read — cross-field invariant violation.
    # Uses the wire (pre-expansion) field name since this flows through
    # the real claude_p stub -> _expand_conformer_output path.
    bad = _clean_result(
        rule_violations=[{"status": "residual", "rule": "x",
                          "why_not_fixed": "y"}],
    )
    state = _stub_claude_p(c, [bad])

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    assert state["i"] == 1
    block = env["st"].data["conformance"]["_final"]
    assert any("malformed" in w for w in block["warnings"])


# --- WorkerError: caught, recorded as warning, never raised ---------------

def test_worker_error_does_not_raise(env):
    c = env["leerie"]

    async def _stub(**kwargs):
        raise c.WorkerError("budget exhausted")
    c.claude_p = _stub

    # Must not raise — advisory framing.
    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    block = env["st"].data["conformance"]["_final"]
    assert any("WorkerError" in w for w in block["warnings"])


# --- protected-path commit gets rolled back -------------------------------

def test_protected_path_commit_is_rolled_back(env):
    c = env["leerie"]

    def _bad_commit(wt: Path):
        (wt / ".leerie-marker").write_text("bad\n")
        # write to the actual .leerie/ via a relative-from-staging path.
        # The staging worktree is at repo/.leerie/runs/<id>/worktrees/staging,
        # so writing to wt/".leerie" creates a *new* nested .leerie which is
        # the protected-path the check guards. Easier: write top-level
        # .claude/settings.json which is protected at any level.
        (wt / ".claude").mkdir(exist_ok=True)
        (wt / ".claude" / "settings.json").write_text("{}\n")
        _run(["git", "add", "-A"], cwd=wt)
        _run(["git", "commit", "-q", "-m",
              "conformer: BAD touched protected path"], cwd=wt)

    state = _stub_claude_p(c, [_clean_result()], commits={0: _bad_commit})
    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["staging"],
        capture_output=True, text=True).stdout.strip()

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=env["staging"],
        capture_output=True, text=True).stdout.strip()
    assert head_after == head_before, "rollback did not reset HEAD"
    block = env["st"].data["conformance"]["_final"]
    assert any("protected-path" in w for w in block["warnings"])
    assert state["i"] == 1  # loop broke after rollback


# --- BLT-repetition feedback threading (mirrors the per-subtask phase) ----

def _bash_event(tid, cmd):
    return {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash",
         "input": {"command": cmd}}]}}


def _result_event(tid, text):
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}]}}


def _write_log(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_within_round_repetition_injects_feedback_into_next_round(env):
    """When _emit_bash_axis_warnings detects a within-round repetition
    (e.g. TEST_CMD run twice in one round — Pattern A) at the final,
    whole-tree conformer call site, that warning must be threaded into
    the next round's prompt, exactly like the auto-backgrounded
    (Pattern B) class already is."""
    c = env["leerie"]
    dirty = _clean_result(
        build={"ran": True, "passed": False, "command": "npm run build",
               "summary": "fail"})

    prompts: list[str] = []

    async def _stub(**kwargs):
        i = len(prompts)
        prompts.append(kwargs["user_prompt"])
        if i == 0:
            # Round 0's own transcript, discovered by
            # _emit_bash_axis_warnings after this round completes.
            _write_log(
                env["run_dir"] / "logs" / "final-conformer-r0.log",
                [_bash_event("a1", "npm test"),
                 _result_event("a1", "1 test passed"),
                 _bash_event("a2", "npm test"),
                 _result_event("a2", "42 tests passed")])
            return dirty
        return _clean_result()

    c.claude_p = _stub

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    assert len(prompts) >= 2, "expected at least 2 final-conformer rounds"
    assert not any("times in one round" in p for p in prompts[:1]), \
        "round 0's own prompt should carry no prior-round feedback"
    assert any("times in one round" in p for p in prompts[1:]), \
        f"round 1's prompt should carry the repetition feedback; " \
        f"prompts={prompts!r}"


# --- residuals get summarized into warnings -------------------------------

def test_failing_axis_summarized_into_warnings(env):
    c = env["leerie"]
    failing = _clean_result(
        rules_files_read=["README.md"],
        build={"ran": True, "passed": False, "command": "make",
               "summary": "compile error in src.py"},
    )
    _stub_claude_p(c, [failing] * env["caps"]["conformance_rounds"])

    asyncio.run(c._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))

    block = env["st"].data["conformance"]["_final"]
    assert any("build-failed" in w for w in block["warnings"]), \
        f"warnings = {block['warnings']}"


# ---------------------------------------------------------------------------
# _final_conformance_payload — the PR-writer payload surfacing helper
# ---------------------------------------------------------------------------

class _StatePayload:
    """Minimal stand-in for State for _final_conformance_payload, which
    only reads `data`."""

    def __init__(self, data: dict):
        self.data = data


def test_payload_none_when_block_absent(leerie):
    st = _StatePayload({})
    assert leerie._final_conformance_payload(st) is None


def test_payload_none_when_clean(leerie):
    st = _StatePayload({"conformance": {"_final": {
        "result": _clean_result(), "warnings": [],
    }}})
    assert leerie._final_conformance_payload(st) is None


def test_payload_surfaces_residuals(leerie):
    res = _clean_result(
        rules_files_read=["README.md"],
        rule_violations_residual=[
            {"rule": "no print statements", "why_not_fixed": "user-facing UI"},
        ],
    )
    st = _StatePayload({"conformance": {"_final": {
        "result": res, "warnings": [],
    }}})
    out = leerie._final_conformance_payload(st)
    assert out is not None
    assert out["residuals"] == [{"rule": "no print statements",
                                 "why_not_fixed": "user-facing UI"}]
    assert out["failed_axes"] == []


def test_payload_surfaces_failed_axes(leerie):
    res = _clean_result(
        lint={"ran": True, "passed": False, "command": "ruff check .",
              "summary": "src.py:5: E501 line too long"},
    )
    st = _StatePayload({"conformance": {"_final": {
        "result": res, "warnings": [],
    }}})
    out = leerie._final_conformance_payload(st)
    assert out is not None
    assert out["failed_axes"] == [{
        "axis": "lint",
        "command": "ruff check .",
        "summary": "src.py:5: E501 line too long",
    }]


def test_payload_surfaces_warnings(leerie):
    st = _StatePayload({"conformance": {"_final": {
        "result": None,
        "warnings": ["final conformer round 0: WorkerError: budget"],
    }}})
    out = leerie._final_conformance_payload(st)
    assert out is not None
    assert out["warnings"] == [
        "final conformer round 0: WorkerError: budget"]


def test_payload_truncates_when_over_cap(leerie):
    """`_final_conformance_payload` keeps the JSON-serialized field
    under `PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES` by dropping warnings
    (and then residuals) from the tail. At least one of each is kept
    so the worker still sees that drift was present, and a
    `truncated: True` marker is added so the prompt can mention the
    cut-off honestly."""
    big_warning = "x" * 600  # ~600 bytes per warning, 50 of them = 30 KB
    res = _clean_result(
        rules_files_read=["README.md"],
        rule_violations_residual=[
            {"rule": f"rule-{i}", "why_not_fixed": "y" * 200}
            for i in range(20)
        ],
    )
    st = _StatePayload({"conformance": {"_final": {
        "result": res,
        "warnings": [f"{big_warning} ({i})" for i in range(50)],
    }}})
    out = leerie._final_conformance_payload(st)
    assert out is not None
    import json as _json
    serialized = _json.dumps(out, separators=(",", ":")).encode("utf-8")
    assert len(serialized) <= leerie.PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES
    assert out.get("truncated") is True
    # At least one of each kept so the worker still sees there was drift.
    assert len(out["residuals"]) >= 1
    assert len(out["warnings"]) >= 1


# ---------------------------------------------------------------------------
# Wiring: the call site lives between phase_execute and phase_finalize
# ---------------------------------------------------------------------------

def test_run_final_conformance_called_between_execute_and_finalize(leerie):
    """Coupling test: `_run_phases` must call `_run_final_conformance`
    *after* `phase_execute` and *before* `phase_finalize`. If anyone
    moves the call, this test surfaces it as a real change."""
    import inspect
    src = inspect.getsource(leerie._run_phases)
    exec_idx = src.find("await phase_execute(")
    final_idx = src.find("await _run_final_conformance(")
    finalize_idx = src.find("await phase_finalize(")
    assert exec_idx != -1, "phase_execute call missing"
    assert final_idx != -1, "_run_final_conformance call missing"
    assert finalize_idx != -1, "phase_finalize call missing"
    assert exec_idx < final_idx < finalize_idx, (
        "ordering broken: final conformance must run between execute "
        "and finalize")
