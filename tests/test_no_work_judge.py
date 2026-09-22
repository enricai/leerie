"""Tests for the converged-gate no-work consumer (DESIGN §8 *The
healthy-path consumer: a converged gate still checks the claim*):
`_confirm_no_work_on_converged_gate` + the `no_work_judge` worker.

Motivation (measured, 2026-09-22): `likely_already_satisfied` was consumed
ONLY in `phase_classification_gate`'s exhaustion arm, so on every
converging run the claim was written and never read — three consecutive
re-runs of an already-merged barnacle task each cited the landed commits,
converged classification, and still planned, executed, and shipped a PR.

Coverage discipline: every behavioral test drives the REAL
`phase_classification_gate` (the consumer's caller), not the helper in
isolation — asserting the terminal-state VALUES (`no_work_required`,
`no_work_confirmation` contents), never key presence. The stubbed
`claude_p` dispatches on `schema_key` so the classification_judge and
no_work_judge stubs cannot mask each other, and the never-invoked cases
assert a per-schema call count of zero (anti-vacuity: a hook that was
never wired would pass a "returns False" assertion).
"""
from __future__ import annotations

import asyncio
import inspect

import pytest


def _minimal_state(leerie, tmp_path, run_id="test-no-work-judge-aaa"):
    leerie_root = tmp_path / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0,
               "categories": ["documentation"],
               # _judgment_cwd raises without it (DESIGN §12
               # *Judgment-worker isolation*).
               "planning_worktree": str(run_dir / "worktrees" / "planning")}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"classification_judge": "sonnet", "no_work_judge": "sonnet"}
EFFORTS = {"classification_judge": "medium", "no_work_judge": "medium"}

_CLEAN_JUDGE = {"categories_reviewed": ["documentation"],
                "miscategorizations": [], "rationale": "ok"}


def _seed_claim(st, evidence="deliverable already on HEAD at commit abc1234"):
    st.data["likely_already_satisfied"] = True
    st.data["likely_already_satisfied_evidence"] = evidence
    st.save()


def _patch_workers(leerie, monkeypatch, no_work_verdict):
    """Stub claude_p: classification_judge always converges clean;
    no_work_judge returns `no_work_verdict` (or raises when it is the
    "CRASH" sentinel). Returns per-schema call counts and captured
    no_work_judge kwargs."""
    calls: dict[str, int] = {}
    captured: dict = {}

    async def fake_claude_p(**kwargs):
        schema = kwargs.get("schema_key")
        calls[schema] = calls.get(schema, 0) + 1
        if schema == "classification_judge":
            return dict(_CLEAN_JUDGE)
        if schema == "no_work_judge":
            captured.update(kwargs)
            if no_work_verdict == "CRASH":
                raise leerie.WorkerError("judge boom")
            return dict(no_work_verdict)
        pytest.fail(f"unexpected schema_key {schema!r}")

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls, captured


# === Wiring pins ===========================================================

class TestWiring:
    def test_gate_calls_the_consumer_after_the_clean_log(self, leerie):
        src = inspect.getsource(leerie.phase_classification_gate)
        assert "_confirm_no_work_on_converged_gate(" in src
        # Ordering: the consult runs on the CONVERGED path, after the
        # audit-key persist and the clean log — never before the gate
        # has actually converged.
        assert (src.index('classification_coverage_gate')
                < src.index("_confirm_no_work_on_converged_gate("))

    def test_consumer_uses_probe_tool_scope_and_schema(self, leerie):
        src = inspect.getsource(leerie._confirm_no_work_on_converged_gate)
        assert 'schema_key="no_work_judge"' in src
        assert "SATISFIED_PROBE_TOOLS" in src

    def test_consumer_resets_the_judgment_worktree_first(self, leerie):
        """The judge is handed no diff — its cwd is the only thing
        determining which tree it verifies, and the classifier plus N
        classification_judge rounds lived there first. Same reset
        discipline as the satisfied-probe sweep, whose comment records
        the 12/12 false-positive calibration; a false confirm here ends
        the whole run."""
        src = inspect.getsource(leerie._confirm_no_work_on_converged_gate)
        i_reset = src.index("_ensure_planning_worktree(")
        i_spawn = src.index("claude_p(")
        assert i_reset < i_spawn

    def test_worker_registered(self, leerie):
        assert "no_work_judge" in leerie.WORKER_TYPES
        assert "no_work_judge" in leerie.PLANNING_WORKER_TYPES
        # Judgment tier via the global fallback (CLAUDE.md: a new worker
        # MUST be absent from MODEL_DEFAULT_PER_WORKER), medium effort.
        assert "no_work_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
        assert leerie.EFFORT_DEFAULT_PER_WORKER["no_work_judge"] == "medium"


# === Behavioral, through the real gate =====================================

def test_confirmed_claim_routes_to_no_work(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    calls, _ = _patch_workers(leerie, monkeypatch, {
        "confirmed": True,
        "evidence": "verified src/example_module.py and its test on HEAD",
        "checked": ["src/example_module.py"],
    })
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is True
    assert st.data["no_work_required"] is True
    assert st.data["finished_at"]
    assert calls == {"classification_judge": 1, "no_work_judge": 1}
    # Audit record carries BOTH halves of the agreement, verbatim.
    conf = st.data["no_work_confirmation"]
    assert conf["classifier_evidence"] == (
        "deliverable already on HEAD at commit abc1234")
    assert conf["judge_evidence"] == (
        "verified src/example_module.py and its test on HEAD")
    assert conf["checked"] == ["src/example_module.py"]
    # The gate still persisted its own audit key before the consult.
    assert st.data["classification_coverage_gate"] is not None


def test_disputed_claim_proceeds_to_planning(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    calls, _ = _patch_workers(leerie, monkeypatch, {
        "confirmed": False, "evidence": "required test file absent"})
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert "no_work_required" not in st.data
    assert "no_work_confirmation" not in st.data
    assert calls["no_work_judge"] == 1


def test_judge_crash_fails_open(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    calls, _ = _patch_workers(leerie, monkeypatch, "CRASH")
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert "no_work_required" not in st.data
    assert calls["no_work_judge"] == 1


def test_confirm_with_empty_evidence_is_discarded(
        leerie, tmp_path, monkeypatch):
    """A bare `confirmed: true` must not end the run — the mechanical
    empty-evidence guard mirrors check_classifier_output's
    EMPTY_EVIDENCE rule."""
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    _patch_workers(leerie, monkeypatch,
                   {"confirmed": True, "evidence": "   "})
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert "no_work_required" not in st.data


def test_no_claim_means_judge_never_spawns(leerie, tmp_path, monkeypatch):
    st = _minimal_state(leerie, tmp_path)  # flag never set
    calls, _ = _patch_workers(leerie, monkeypatch, {
        "confirmed": True, "evidence": "should never be consulted"})
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert "no_work_judge" not in calls


def test_skip_classification_check_also_suppresses_the_consult(
        leerie, tmp_path, monkeypatch):
    """Documented side effect (DESIGN §8 *The healthy-path consumer*):
    --skip-classification-check returns from the gate before the
    consumer's hook point, so the judge never spawns even on a True
    claim with evidence — the run proceeds on the classifier's own
    categories."""
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    st.data["skip_classification_check"] = True
    st.save()
    calls, _ = _patch_workers(leerie, monkeypatch, {
        "confirmed": True, "evidence": "should never be consulted"})
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert calls == {}  # neither judge spawns — the gate itself is skipped
    assert "no_work_required" not in st.data


def test_skip_satisfied_check_suppresses_the_consult(
        leerie, tmp_path, monkeypatch):
    """One flag governs both already-satisfied PRUNES (this consumer and
    the phase-3 pre-schedule sweep; the post-execution HEAD-probe
    rescues are deliberately outside its scope — they settle work,
    never delete it): with skip_satisfied_check set, the judge never
    spawns even on a True claim with evidence."""
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    st.data["skip_satisfied_check"] = True
    st.save()
    calls, _ = _patch_workers(leerie, monkeypatch, {
        "confirmed": True, "evidence": "should never be consulted"})
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is False
    assert "no_work_judge" not in calls
    assert "no_work_required" not in st.data


def test_judge_prompt_carries_claim_and_required_items(
        leerie, tmp_path, monkeypatch):
    """Substance, not structure: the judge must receive the classifier's
    evidence and the run's required_items — a consult that omits them
    verifies nothing."""
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st, evidence="claim text the judge must see")
    st.data["required_items"] = ["the widget endpoint returns 200"]
    st.save()
    _, captured = _patch_workers(leerie, monkeypatch, {
        "confirmed": False, "evidence": "not verified"})
    asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    prompt = captured["user_prompt"]
    assert "claim text the judge must see" in prompt
    assert "the widget endpoint returns 200" in prompt
    assert captured["autonomous"] is False


def test_exhaustion_arm_unchanged_by_the_new_consumer(
        leerie, tmp_path, monkeypatch):
    """The exhaustion arm keeps its raw-trust routing (no judge spawn):
    a gate that cannot converge with the claim set routes to no-work
    WITHOUT consulting the no_work_judge — its trust boundary is
    documented and deliberate (DESIGN §8)."""
    st = _minimal_state(leerie, tmp_path)
    _seed_claim(st)
    calls: dict[str, int] = {}

    async def fake_claude_p(**kwargs):
        schema = kwargs.get("schema_key")
        calls[schema] = calls.get(schema, 0) + 1
        assert schema == "classification_judge", (
            "exhaustion path must not spawn any other worker")
        return {"categories_reviewed": ["documentation"],
                "miscategorizations": [{
                    "kind": "missing_category", "category": "testing",
                    "concrete_work_evidence": "tests required"}],
                "rationale": "never converges"}

    async def fake_phase_classify(*a, **k):
        pass

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "phase_classify", fake_phase_classify)
    routed = asyncio.run(leerie.phase_classification_gate(
        "task", st, _caps(leerie), False, MODELS, EFFORTS))
    assert routed is True
    assert st.data["no_work_required"] is True
    assert "no_work_judge" not in calls
