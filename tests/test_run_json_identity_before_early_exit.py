"""Source-coupling pin for a run.json identity-fidelity invariant found
during a post-implementation audit of the classification-gate no-work
routing fix (DESIGN §8 *Reaching the cleared-but-empty state from
classification*).

`_finish_no_work_run` writes only `{finished_at, no_push, no_verify}` to
run.json (via `_write_run_json`'s merge-on-top-of-existing semantics —
see `tests/test_no_work_finish.py`). Before this fix, the run-identity
init block (`run_id`, `branch`, `working_branch`, `pr_base_branch`,
`started_at`, `task`) ran strictly AFTER `phase_classification_gate` in
`_run_phases`, so a run routed to the no-work terminal state via that
gate's exhaustion path produced a run.json permanently missing all
identity fields — unlike the pre-existing `detect_no_work` post-plan
route, whose call site always sits downstream of that init block. The
launcher's local-runtime auto-finalize scan (`leerie:7124-7150`)
explicitly treats a run.json missing `branch`/`working_branch` as "died
before phase_classify completed," so a correctly-completed no-work run
would be indistinguishable from a crash by that reading.

Modeled directly on `tests/test_planning_checkpoint_ordering.py`'s
`TestCheckpointFollowsItsPhaseCall`: `_run_phases` spawns real workers and
shells out to git, so this is pure `inspect.getsource` + string-index
comparison, not a live drive.
"""
from __future__ import annotations

import inspect


def _phases_src(leerie) -> str:
    return inspect.getsource(leerie._run_phases)


class TestRunJsonIdentityPrecedesClassificationGate:
    """The load-bearing ordering: index(run.json identity write) <
    index(phase_classification_gate call) — so ANY early-exit path
    reachable from phase_classify/phase_classification_gate (including
    the cleared-but-empty terminal-state route on gate exhaustion) sees
    a run.json that already carries full run identity."""

    def test_write_run_json_call_precedes_classification_gate_call(self, leerie):
        src = _phases_src(leerie)
        i_write = src.find("_write_run_json(")
        i_gate = src.find("await phase_classification_gate(")
        assert i_write != -1, "_run_phases must call _write_run_json"
        assert i_gate != -1, "_run_phases must call phase_classification_gate"
        assert i_write < i_gate, (
            "the run.json identity write must precede "
            "phase_classification_gate — otherwise a run routed to the "
            "no-work terminal state on gate exhaustion writes a run.json "
            "missing run_id/branch/working_branch/started_at/task")

    def test_write_run_json_call_precedes_phase_classify(self, leerie):
        src = _phases_src(leerie)
        i_write = src.find("_write_run_json(")
        i_classify = src.find(
            "await phase_classify(task, st, caps, args.clarify, models, "
            "efforts)")
        assert i_write != -1
        assert i_classify != -1, "_run_phases must call phase_classify"
        assert i_write < i_classify, (
            "the run.json identity write must precede phase_classify too "
            "(not just the gate) — it was moved here specifically so "
            "nothing between run start and the first early-exit-capable "
            "phase call can reach _finish_no_work_run without identity "
            "already written")

    def test_write_run_json_passes_full_identity_fields(self, leerie):
        """Regression guard against a future edit silently dropping a
        field from the call — mirrors the fields tests/test_no_work_finish.py
        proves _finish_no_work_run does NOT write, so this call is the
        only source of them for an early-exit run.json."""
        src = _phases_src(leerie)
        i_write = src.find("_write_run_json(")
        # Slice a generous window after the call site to the closing paren
        # region; the call spans several kwarg lines.
        window = src[i_write:i_write + 700]
        for field in ("run_id=", "branch=", "working_branch=",
                      "pr_base_branch=", "started_at=", "task="):
            assert field in window, (
                f"_write_run_json call is missing {field!r} — an "
                "early-exit run.json would be incomplete")

    def test_write_run_json_gated_on_not_resume(self, leerie):
        """The identity write must stay gated on `not args.resume` — a
        resumed run already has run.json from its first invocation and
        must not re-derive working_branch from the (possibly different)
        current checkout."""
        src = _phases_src(leerie)
        i_not_resume = src.find("if not args.resume:")
        i_write = src.find("_write_run_json(")
        assert i_not_resume != -1
        assert i_not_resume < i_write < src.find(
            "await phase_classify(task, st, caps, args.clarify, models, "
            "efforts)"), (
            "_write_run_json must be inside the `if not args.resume:` "
            "block, positioned before phase_classify")
