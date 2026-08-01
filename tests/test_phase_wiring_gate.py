"""Tests for phase_wiring_gate (DESIGN §5 *A wiring re-check on the
fully-merged plan*, §8): the SEMANTIC plan-wiring gate that runs an
independent wiring_judge, gates on a non-empty wiring_defects array, and
re-drives phase_reconcile via _run_checked_loop. (The deterministic
structural counterpart check_plan_wiring is tested in
test_check_plan_wiring.py.)
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _state(leerie, tmp_path, run_id="test-wiring-gate-aaa"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0, "dropped_subtasks": {}}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"wiring_judge": "opus"}
EFFORTS = {"wiring_judge": "medium"}

_PLANS = [{"domain": "feat", "status": "ready", "subtasks": [
    {"id": "feat-001", "provides": ["schema"], "requires": [],
     "depends_on": [], "intent": "write schema", "title": "s",
     "files_likely_touched": []},
    {"id": "feat-002", "provides": [], "requires": [], "depends_on": [],
     "intent": "read schema", "title": "r", "files_likely_touched": []},
]}]


class TestWiring:
    def test_invokes_wiring_judge(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert 'schema_key="wiring_judge"' in src

    def test_uses_run_checked_loop(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "_run_checked_loop(" in src

    def test_is_detect_and_die_no_re_drive(self, leerie):
        """The wiring gate is a single detect-and-die pass — it must NOT
        re-drive phase_reconcile (the reconciler can't invent a missing edge),
        i.e. it passes no make_feedback_prompt to _run_checked_loop."""
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "await phase_reconcile(" not in src
        assert "make_feedback_prompt=" not in src

    def test_dies_on_defect(self, leerie):
        src = inspect.getsource(leerie.phase_wiring_gate)
        assert "die(" in src

    def test_die_message_does_not_recommend_skip_overlap_judge(self, leerie):
        """The wiring gate is a distinct, later gate than the overlap judge;
        --skip-overlap-judge does NOT bypass it (there is no skip guard in
        phase_wiring_gate). The die() message must not advise it as a bypass —
        an operator who follows that advice re-runs and re-dies on the same
        defect. It may still name the flag to explain what it does NOT do."""
        src = inspect.getsource(leerie.phase_wiring_gate)
        # The die() text must not present --skip-overlap-judge as a way to
        # "bypass" this gate. Assert the retired bypass phrasing is gone.
        assert "to bypass reconciliation gates" not in src
        # And the message must state this gate has no bypass flag.
        assert "no bypass flag" in src

    def test_deterministic_check_and_gate_both_in_run_phases(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "await phase_wiring_gate(" in src
        assert "check_plan_wiring(" in src
        # The deterministic check runs before validate_plan.
        i_check = src.index("check_plan_wiring(")
        i_validate = src.index("validate_plan(subtasks)")
        assert i_check < i_validate

    def test_wiring_gate_runs_after_the_drop_filters(self, leerie):
        """Regression pin (post-merge Finding C): the LLM wiring_judge must run
        on the POST-DROP plan — after both soft-drop filters and schedule(),
        and before validate_plan. It reads `dropped_subtasks` (populated by the
        filters) for its broken_by_drop / broken_by_merge reasoning and is told
        the plan is 'post-drop', so a pre-filter placement feeds it a plan that
        still contains to-be-dropped subtasks + an incomplete drop audit (the
        bug shipped in PR #117 and preserved through the #116 rebase — no test
        guarded the placement).
        """
        src = inspect.getsource(leerie._run_phases)
        i_offtree = src.index("filter_offtree_subtasks(")
        i_satisfied = src.index("await filter_satisfied_subtasks(")
        i_schedule = src.index("schedule(plans)")
        i_gate = src.index("await phase_wiring_gate(")
        i_validate = src.index("validate_plan(subtasks)")
        # Both drop filters + schedule() precede the gate; the gate precedes
        # validate_plan (IMPLEMENTATION.md "3 Schedule" sequence + the
        # phase_wiring_gate docstring + DESIGN §5).
        assert i_offtree < i_gate
        assert i_satisfied < i_gate
        assert i_schedule < i_gate
        assert i_gate < i_validate
        # Exactly one call site — a rebase must not duplicate or leave a stale
        # pre-filter copy.
        assert src.count("await phase_wiring_gate(") == 1

    def test_wiring_gate_is_not_re_invoked_on_budget_check_resume(self, leerie):
        """The LLM gate is expensive, so a budget-check resume must not
        re-invoke it — but the skip is keyed on `st.data["wiring_gate"]`,
        the audit record the gate writes ONLY when it passes, NOT on
        `plan_snapshot`.

        `plan_snapshot` is written a few lines *earlier*, deliberately, so a
        die() at either terminal gate does not discard the planning spend.
        That means it is present even when the gate FAILED, so keying the
        skip on it made `--resume` a silent bypass of a gate the run had
        already failed (run 3a4abba3, 2026-08-01: resumed straight to
        `phase_execute` with zero gate invocations, executing the plan the
        gate had rejected — while the die() message claimed the gate had "no
        bypass flag"). The cheap-resume property this test was written to
        protect is unchanged: after a clean pass `wiring_gate` is present and
        the gate is skipped. See tests/test_wiring_gate_resume.py for the
        behavioural pins on all three shapes.
        """
        src = inspect.getsource(leerie._run_phases)
        i_snapshot_write = src.index('st.data["plan_snapshot"] = ')
        i_gate = src.index("await phase_wiring_gate(")
        # The gate still runs after the snapshot is safely persisted.
        assert i_snapshot_write < i_gate
        # But it is guarded by the pass-only audit key, not the snapshot —
        # and that guard is what makes the skip correct on a resume.
        i_guard = src.index('if "wiring_gate" not in st.data:')
        assert i_guard < i_gate, (
            "phase_wiring_gate must be invoked from a wiring_gate-keyed "
            "guard, so a resume after a gate die() re-runs it")
        # It sits outside the plan_snapshot if/else, so both the fresh path
        # and the rehydrate path reach the same guard.
        i_rehydrate = src.index('snap = st.data["plan_snapshot"]')
        assert i_rehydrate < i_guard


def test_clean_wiring_passes(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [],
                "rationale": "wired"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert st.data.get("wiring_gate") is not None
    assert out == _PLANS


def test_defect_dies(leerie, tmp_path, monkeypatch):
    """A concrete, live wiring defect the gate CANNOT auto-repair die()s
    immediately (single pass, no re-drive).

    The tag names no in-plan provider, which is the principled refusal case:
    the plan is missing the work, not just the edge, so inventing a
    dependency on nothing would be worse than dying. A defect whose tag has
    exactly one provider is repaired instead — see
    `tests/test_wiring_gate_repair.py`."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "nothing-provides-this",
            "concrete_reason": "reads the schema but declares no requires",
            "severity": "live_defect",
        }], "rationale": "missing edge"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_wiring_gate(
            _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))


def test_latent_risk_defect_does_not_gate(leerie, tmp_path, monkeypatch):
    """Regression pin for run d8302c0d46d8... (barnacle, 2026-07-31): a
    defect the judge itself scored latent_risk (correct today, fragile to
    a future edit — its own rationale said 'a latent fragility rather than
    a live defect... not a true missing edge') must NOT die(). Only
    live_defect gates."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-003-1",
            "tag_or_dep": "uchealth-workhistory-gate-fixture",
            "concrete_reason": "feat-003-1 only declares requires on "
                               "feat-002's tag, inheriting feat-001's "
                               "fixture transitively.",
            "severity": "latent_risk",
        }], "rationale": "a latent fragility rather than a live defect"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


def test_mixed_severity_still_dies_on_the_live_defect(leerie, tmp_path,
                                                        monkeypatch):
    """A live_defect entry gates the whole plan even alongside a
    latent_risk entry — severity filtering narrows what counts as a
    defect, it doesn't create a per-defect bypass for real ones.

    The live defect names a tag with no in-plan provider so it is not
    auto-repairable; the point being pinned is severity filtering, not the
    repair rule."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [
            {
                "kind": "missing_requires", "sid": "feat-003-1",
                "tag_or_dep": "some-fixture",
                "concrete_reason": "transitive but resolves today",
                "severity": "latent_risk",
            },
            {
                "kind": "missing_requires", "sid": "feat-002",
                "tag_or_dep": "nothing-provides-this",
                "concrete_reason": "reads the schema, no requires at all",
                "severity": "live_defect",
            },
        ], "rationale": "mixed"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_wiring_gate(
            _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))


def test_vague_defect_does_not_gate(leerie, tmp_path, monkeypatch):
    """A defect missing concrete_reason/tag_or_dep is dropped and must NOT
    die."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "",  # vague → dropped
            "concrete_reason": "",
        }], "rationale": "hand-wave"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


def test_judge_crash_degrades(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        raise leerie.WorkerError("crash")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS
