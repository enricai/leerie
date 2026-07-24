"""End-to-end regression fixture for the two-stage instruction-adherence
gate (feat-006/007/008): locks the validated composition — the
deterministic prescribed-command-coverage floor (PRIMARY,
model-independent) plus the opus `adherence_judge` (SECONDARY, semantic
layer) — against the two shapes the design's corpus validation (21 real
runs, see the plan writeup) turned on:

1. Incident shape: a prescribed procedure whose commands no subtask runs
   ⇒ the gate fires and routes to a re-plan before the plan would ever
   reach schedule()/phase_execute.
2. Ordinary shape: a goal-only task with no prescribed procedure ⇒ the
   gate short-circuits with zero extra judge/re-plan spend.

This is deliberately independent of tests/test_phase_adherence_gate.py's
per-branch wiring pins (WorkerError degrade, exhaustion die(), individual
skip-flag checks) — this file's job is the composed incident-vs-ordinary
contrast the design's own validation methodology depends on, so that
result cannot silently regress. The judge layer is stubbed to a fixed
structured envelope (mirrors tests/test_dep_capture_worker.py's `_invoke`
stub pattern); the deterministic-floor half needs no stub at all. Nothing
here calls a live claude binary or does network I/O.
"""
from __future__ import annotations

import asyncio

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def _subtask(sid: str, *, runs_commands=(), title: str = "",
             intent: str = "", scs: str = "") -> dict:
    return {
        "id": sid,
        "title": title or f"Subtask {sid}",
        "intent": intent or f"intent for {sid}",
        "success_criteria_seed": scs or f"{sid} succeeds",
        "runs_commands": list(runs_commands),
        "files_likely_touched": [],
        "provides": [],
        "requires": [],
        "depends_on": [],
        "size": "small",
    }


def _plan(domain: str, *subtasks) -> dict:
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _prescribed(commands, is_prescribed=True, forbid_manual=True,
                 evidence="task said so"):
    return {
        "is_prescribed": is_prescribed,
        "commands": list(commands),
        "forbid_manual": forbid_manual,
        "evidence": evidence,
    }


def _minimal_state(leerie, tmp_path, run_id="test-adherence-gate-e2e-001"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


_MODELS = {"adherence_judge": "opus"}
_EFFORTS = {"adherence_judge": "high"}

# Synthetic incident shape, mirroring the recon-browser/recon-generate
# production incident this gate exists to catch, at arm's length so the
# fixture doesn't depend on the real repo/tool names ever staying the same.
_INCIDENT_TASK = (
    "your ONLY job is to run foo:build and then foo:generate — do NOT "
    "hand-write the output yourself"
)
_INCIDENT_PRESCRIBED = _prescribed(
    ["foo:build", "foo:generate"],
    evidence="user said 'your ONLY job is to run foo:build and then "
             "foo:generate — do NOT hand-write the output yourself'",
)

_ORDINARY_TASK = "add a `region` field to the User model and thread it " \
                  "through the API"


def _judge_envelope(adherence: float, violations=(), rationale: str = ""):
    return {
        "user_prescribed_a_procedure": adherence < 5.0,
        "instruction_adherence": adherence,
        "violations": list(violations),
        "rationale": rationale,
    }


# ===========================================================================
# 1. Deterministic-floor half — no LLM, no stub, real function
# ===========================================================================

class TestDeterministicFloorAlone:
    """The PRIMARY layer (check_prescribed_command_coverage) is pure
    JSON->verdict Python. These assertions call it directly with no
    stubbing at all, proving the incident/ordinary separation holds
    before any judge or re-plan machinery is even involved (STEP-0's
    'floor is PRIMARY' conclusion)."""

    def test_incident_shape_floor_fires(self, leerie):
        subtasks = [
            _subtask("feat-001", runs_commands=["write contract.ts"]),
            _subtask("feat-002", runs_commands=["write generated-output.ts"]),
        ]
        issues = leerie.check_prescribed_command_coverage(
            _INCIDENT_PRESCRIBED, subtasks)
        assert any("foo:generate" in i for i in issues), (
            "the deterministic floor must fire on the prescribed command "
            "no subtask actually runs"
        )
        assert all(i.startswith("PRESCRIBED_CMD_UNRUN:") for i in issues)

    def test_ordinary_shape_floor_silent(self, leerie):
        subtasks = [_subtask("feat-001", runs_commands=["pytest tests/"])]
        issues = leerie.check_prescribed_command_coverage(
            _prescribed([], is_prescribed=False), subtasks)
        assert issues == [], (
            "a goal-only task (no prescribed procedure) must never fire "
            "the floor, regardless of what commands subtasks happen to run"
        )

    def test_honored_prescribed_command_floor_silent(self, leerie):
        """When the plan DOES run the prescribed command (paraphrased),
        the floor must stay silent — this is the honored-procedure
        control that proves the floor isn't just always firing."""
        subtasks = [
            _subtask("feat-001", runs_commands=["run foo:build locally"]),
            _subtask("feat-002", runs_commands=["invoke foo:generate step"]),
        ]
        issues = leerie.check_prescribed_command_coverage(
            _INCIDENT_PRESCRIBED, subtasks)
        assert issues == []


# ===========================================================================
# 2. Composed end-to-end: phase_adherence_gate with stubbed judge/replan
# ===========================================================================

class TestAdherenceGateEndToEnd:
    """Drives the full phase_adherence_gate composition — deterministic
    floor (real) + opus adherence_judge (stubbed claude_p, mirroring
    test_dep_capture_worker.py's envelope-stub pattern) — for both
    shapes named in the seed. This is the regression fixture: if either
    behavior drifts (incident stops firing, or the ordinary case starts
    paying for a re-plan it doesn't need), this file fails."""

    def test_incident_shape_fires_and_replans_before_execute(
        self, leerie, monkeypatch, tmp_path
    ):
        st = _minimal_state(leerie, tmp_path)
        st.data["prescribed_procedure"] = _INCIDENT_PRESCRIBED
        bad_plans = [_plan(
            "feature-implementation",
            _subtask("feat-001", runs_commands=["write contract.ts"]),
            _subtask("feat-002", runs_commands=["write generated-output.ts"]),
        )]
        good_plans = [_plan(
            "feature-implementation",
            _subtask("feat-001", runs_commands=["run foo:build"]),
            _subtask("feat-002", runs_commands=["run foo:generate"]),
        )]

        judge_calls = []

        async def fake_claude_p(**kwargs):
            judge_calls.append(kwargs)
            if len(judge_calls) == 1:
                return _judge_envelope(
                    2.5, violations=["foo:generate never runs"],
                    rationale="plan substitutes manual work for the "
                               "prescribed process")
            return _judge_envelope(
                9.0, rationale="now honors the prescribed procedure")

        replan_calls = []

        async def fake_phase_plan(task, st_, caps, models, efforts):
            replan_calls.append(task)
            return good_plans

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

        result = asyncio.run(leerie.phase_adherence_gate(
            bad_plans, _INCIDENT_TASK, st, _caps(leerie), _MODELS, _EFFORTS))

        # The gate must have actually re-planned — i.e. it did NOT let the
        # incident-shaped plan fall through to schedule()/phase_execute
        # unchanged.
        assert len(replan_calls) == 1, (
            "the gate must re-plan exactly once when the incident shape "
            "is fed in — a silent pass-through here is the exact bug "
            "this gate exists to prevent"
        )
        assert "foo:generate" in replan_calls[0], (
            "the re-plan feedback must name the unrun prescribed command"
        )
        assert result == good_plans, (
            "the gate must return the re-planned (now-compliant) plan, "
            "ready for schedule()"
        )
        # The re-planned result is itself clean under the deterministic
        # floor — confirms the gate converged on a plan that actually
        # satisfies PRIMARY coverage, not just a judge score.
        final_subtasks = [
            s for plan in result for s in plan.get("subtasks", []) or []
        ]
        assert leerie.check_prescribed_command_coverage(
            _INCIDENT_PRESCRIBED, final_subtasks) == []

    def test_ordinary_shape_passes_with_no_extra_replan(
        self, leerie, monkeypatch, tmp_path
    ):
        st = _minimal_state(leerie, tmp_path)
        # No prescribed_procedure at all — the realistic shape for an
        # ordinary "add field X" task, where the classifier never
        # populated the key (mirrors the ~90% common-case default).
        plans = [_plan(
            "feature-implementation",
            _subtask("feat-001", runs_commands=["pytest tests/test_user.py"]),
        )]

        claude_p_calls = []
        phase_plan_calls = []

        async def fake_claude_p(**kwargs):
            claude_p_calls.append(kwargs)
            pytest.fail(
                "claude_p must never be invoked when no prescribed "
                "procedure exists — the ordinary case must be a free "
                "no-op short-circuit"
            )

        async def fake_phase_plan(*args, **kwargs):
            phase_plan_calls.append(args)
            pytest.fail(
                "phase_plan must never be re-invoked for an ordinary "
                "task with no prescribed procedure"
            )

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

        result = asyncio.run(leerie.phase_adherence_gate(
            plans, _ORDINARY_TASK, st, _caps(leerie), _MODELS, _EFFORTS))

        assert result is plans, (
            "the ordinary shape must short-circuit and return the same "
            "plans object unchanged"
        )
        assert claude_p_calls == []
        assert phase_plan_calls == []

    def test_ordinary_shape_with_explicit_is_prescribed_false(
        self, leerie, monkeypatch, tmp_path
    ):
        """Same no-extra-replan guarantee when the classifier explicitly
        emitted is_prescribed=false (as opposed to the key being absent
        entirely, covered above) — both are the same 'nothing to
        enforce' shape and must both be free."""
        st = _minimal_state(leerie, tmp_path)
        st.data["prescribed_procedure"] = _prescribed(
            [], is_prescribed=False, evidence="")
        plans = [_plan(
            "feature-implementation",
            _subtask("feat-001", runs_commands=["pytest tests/"]),
        )]

        async def fake_claude_p(**kwargs):
            pytest.fail("claude_p must not be called for is_prescribed=false")

        async def fake_phase_plan(*args, **kwargs):
            pytest.fail("phase_plan must not be called for is_prescribed=false")

        monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
        monkeypatch.setattr(leerie, "phase_plan", fake_phase_plan)

        result = asyncio.run(leerie.phase_adherence_gate(
            plans, _ORDINARY_TASK, st, _caps(leerie), _MODELS, _EFFORTS))

        assert result is plans
