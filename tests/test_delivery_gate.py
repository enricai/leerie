"""Tests for the finalize-side delivery gate (DESIGN §8 *The delivery
gate*): `_delivery_unmet_majority`, `_delivery_judge_unmet`,
`_run_delivery_prejudge`, `_run_delivery_recheck`, and
`_format_unmet_required_items_section`.

Motivation (measured, 2026-09-27): a run shipped deliverables violating a
standing constraint carried in its own required_items; every in-run gate
passed them, and the NEXT run's no_work_judge vetoed no-work over exactly
that violation — enforcement asymmetry costing one extra run per
violation. This gate applies the same standard in the run that ships.

Coverage discipline: behavioral tests drive the real gate functions with
a stubbed `claude_p` that dispatches on `schema_key` and returns QUEUED
verdicts per call, so the sample count is observable (a gate that always
takes 3 samples, or never takes the extra 2, fails the count
assertions). Values are asserted — persisted unmet entries, the exact
conformer section text — never key presence alone.
"""
from __future__ import annotations

import asyncio
import inspect
import json

import pytest

MODELS = {"delivery_judge": "sonnet", "conformer": "sonnet"}
EFFORTS = {"delivery_judge": "medium", "conformer": "low"}

ITEMS = [
    {"item": "the API returns paginated results", "source_ref": "spec 1"},
    {"item": "the deliverable never references the forbidden term",
     "source_ref": "user instruction"},
]


def _verdicts(*met_flags, evidence="checked"):
    return {"verdicts": [
        {"item_index": i, "met": m, "evidence": f"{evidence}-{i}"}
        for i, m in enumerate(met_flags)]}


def _state(leerie, tmp_path, **overrides):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-delivery-gate"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "worktrees" / "staging").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test task", "worker_count": 0,
               # The gate mirrors _run_final_conformance's own guard: no
               # working_branch → the conformer pass it routes into
               # would skip, so the gate skips too.
               "working_branch": "main",
               "required_items": [dict(i) for i in ITEMS]}
    st.data.update(overrides)
    st.save()
    return st, run_dir


def _caps(leerie):
    return dict(leerie.DEFAULT_CAPS)


def _patch_judge(leerie, monkeypatch, queued: list[dict]):
    """Stub claude_p returning queued delivery_judge verdicts in order;
    captures every call's kwargs."""
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        assert kwargs.get("schema_key") == "delivery_judge", (
            f"unexpected worker {kwargs.get('schema_key')!r}")
        calls.append(kwargs)
        assert queued, "judge spawned more samples than the test queued"
        return dict(queued.pop(0))

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


# === majority tally (pure) =================================================

class TestMajority:
    def test_single_sample_any_unmet_flags(self, leerie):
        s = [_verdicts(True, False)]
        assert leerie._delivery_unmet_majority(s, 2) == [1]

    def test_two_of_three_confirms(self, leerie):
        s = [_verdicts(True, False), _verdicts(True, False),
             _verdicts(True, True)]
        assert leerie._delivery_unmet_majority(s, 2) == [1]

    def test_one_of_three_does_not(self, leerie):
        s = [_verdicts(True, False), _verdicts(True, True),
             _verdicts(True, True)]
        assert leerie._delivery_unmet_majority(s, 2) == []

    def test_omitted_index_is_a_met_vote(self, leerie):
        """A truncated verdict list fails open toward shipping: two
        samples that never mention index 1 outvote the one that flags
        it."""
        s = [_verdicts(True, False), {"verdicts": []}, {"verdicts": []}]
        assert leerie._delivery_unmet_majority(s, 2) == []

    def test_last_duplicate_wins_within_a_sample(self, leerie):
        s = [{"verdicts": [
            {"item_index": 0, "met": False, "evidence": "e"},
            {"item_index": 0, "met": True, "evidence": "e"}]}]
        assert leerie._delivery_unmet_majority(s, 1) == []

    def test_garbage_indices_are_ignored(self, leerie):
        s = [{"verdicts": [
            {"item_index": 99, "met": False, "evidence": "e"},
            {"item_index": "1", "met": False, "evidence": "e"},
            {"item_index": -1, "met": False, "evidence": "e"}]}]
        assert leerie._delivery_unmet_majority(s, 2) == []

    def test_bool_index_is_ignored(self, leerie):
        """bool subclasses int — a schema-escaping `true` must not tally
        as index 1 (review round 1, defense in depth)."""
        s = [{"verdicts": [
            {"item_index": True, "met": False, "evidence": "e"}]}]
        assert leerie._delivery_unmet_majority(s, 3) == []


# === prejudge, executed ====================================================

def test_clean_first_pass_costs_one_sample(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)
    calls = _patch_judge(leerie, monkeypatch, [_verdicts(True, True)])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert len(calls) == 1, "confirm-on-first-pass must not vote"
    gate = st.data["delivery_gate"]
    assert gate["unmet_before"] == [] and gate["samples_before"] == 1
    # Judge scope pins: read-only tools, staging cwd, non-autonomous.
    assert calls[0]["allowed_tools"] == leerie.SATISFIED_PROBE_TOOLS
    assert calls[0]["cwd"].endswith("staging")
    assert calls[0]["autonomous"] is False


def test_flagged_item_confirmed_by_majority(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)
    calls = _patch_judge(leerie, monkeypatch, [
        _verdicts(True, False, evidence="s0"),
        _verdicts(True, False, evidence="s1"),
        _verdicts(True, True, evidence="s2"),
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 3, "a flagged first pass must trigger the vote"
    assert [u["item_index"] for u in unmet] == [1]
    assert unmet[0]["item"] == ITEMS[1]["item"]
    # Evidence comes from an actual unmet vote, not invented.
    assert unmet[0]["evidence"] in ("s0-1", "s1-1")
    # Persisted (values, on disk — a resume must see it).
    on_disk = json.loads(st.path.read_text())
    assert on_disk["delivery_gate"]["unmet_before"] == unmet
    assert on_disk["delivery_gate"]["samples_before"] == 3
    # The judge payload numbered the items and carried their text.
    prompt = calls[0]["user_prompt"]
    assert ITEMS[1]["item"] in prompt
    assert '"item_index": 1' in prompt


def test_outvoted_first_sample_confirms_nothing(
        leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)
    calls = _patch_judge(leerie, monkeypatch, [
        _verdicts(True, False), _verdicts(True, True),
        _verdicts(True, True)])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert len(calls) == 3
    assert st.data["delivery_gate"]["unmet_before"] == []


@pytest.mark.parametrize("overrides", [
    {"required_items": []},
    {"skip_coverage_check": True},
])
def test_gate_is_free_when_inapplicable(leerie, tmp_path, monkeypatch,
                                        overrides):
    st, run_dir = _state(leerie, tmp_path, **overrides)
    calls = _patch_judge(leerie, monkeypatch, [])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == [] and calls == []
    assert "delivery_gate" not in st.data


def test_item_supplied_index_cannot_override_position(
        leerie, tmp_path, monkeypatch):
    """Review round 1: the classifier schema has no additionalProperties,
    so an item carrying its own item_index is schema-legal — spread
    after the literal it overrode the positional index, making the item
    unvotable and the gate report a false clean pass. The positional
    index must always win."""
    poisoned = [{"item": "poisoned requirement", "item_index": 99}]
    st, run_dir = _state(leerie, tmp_path, required_items=poisoned)
    calls = _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e0"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e1"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e2"}]},
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    # The judge payload numbers the item positionally, not as 99.
    assert '"item_index": 0' in calls[0]["user_prompt"]
    assert '"item_index": 99' not in calls[0]["user_prompt"]
    # And the votes for index 0 confirm it unmet.
    assert [u["item_index"] for u in unmet] == [0]
    assert unmet[0]["item"] == "poisoned requirement"


def test_plain_string_items_are_coerced_not_fatal(
        leerie, tmp_path, monkeypatch):
    """Review round 1: required_items can reach state as plain strings
    (tests/test_no_work_judge.py seeds that shape, and every pre-gate
    consumer tolerates it). The gate must run on them, not TypeError
    into a silent advisory no-op."""
    st, run_dir = _state(leerie, tmp_path,
                         required_items=["a plain string requirement"])
    calls = _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": True, "evidence": "ok"}]}])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert len(calls) == 1
    # The coerced text reached the judge.
    assert "a plain string requirement" in calls[0]["user_prompt"]
    assert st.data["delivery_gate"]["unmet_before"] == []


def test_missing_working_branch_skips(leerie, tmp_path, monkeypatch):
    """Review round 1: with working_branch absent the final-conformer
    pass skips, so the gate must not spend judge samples on a routing
    target that will not run (and a recheck that would then report
    'still unmet after the final-conformer pass' for a pass that never
    ran)."""
    st, run_dir = _state(leerie, tmp_path)
    del st.data["working_branch"]
    st.save()
    calls = _patch_judge(leerie, monkeypatch, [])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == [] and calls == []
    assert "delivery_gate" not in st.data


def test_dict_item_without_item_key_is_survivable(
        leerie, tmp_path, monkeypatch):
    """Review round 2: a dict item lacking an 'item' key (state-
    reachable, not model-reachable — the classifier schema requires
    'item') must still run the gate: the judge payload keeps the
    original keys, and the confirmed record carries a guaranteed STRING
    item (empty here) rather than crashing downstream consumers."""
    st, run_dir = _state(leerie, tmp_path, required_items=[
        {"requirement": "no item key here", "source_ref": "x"}])
    calls = _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert "no item key here" in calls[0]["user_prompt"]
    assert unmet[0]["item"] == ""
    assert isinstance(unmet[0]["item"], str)
    # The conformer section renders without crashing on the empty text.
    section = leerie._format_unmet_required_items_section(unmet)
    assert "[0]" in section


def test_non_string_item_value_survives_the_recheck_residual_log(
        leerie, tmp_path, monkeypatch):
    """Review round 2: a nested non-string 'item' value reached the
    recheck's summary slice ([:80]) and raised TypeError into the
    advisory wrapper, losing the residual log line. The normalization
    must coerce it to a string end-to-end."""
    st, run_dir = _state(leerie, tmp_path,
                         required_items=[{"item": {"nested": "text"}}],
                         delivery_gate={
                             "unmet_before": [{"item_index": 0,
                                               "item": "recorded",
                                               "evidence": "e"}],
                             "samples_before": 3})
    _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
    ])
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    residual = st.data["delivery_gate"]["unmet_after"]
    assert isinstance(residual[0]["item"], str)
    assert "nested" in residual[0]["item"]
    # The residual log line executed — that is the substance the
    # TypeError was eating.
    assert any("delivery gate residual" in ln for ln in lines)


def test_recheck_skips_without_working_branch(
        leerie, tmp_path, monkeypatch):
    """Review round 2: the prejudge's working_branch guard sat below its
    resume early-return, so a resume carrying unmet_before with
    working_branch since lost spent 3 samples and logged a residual for
    a conformer pass that skipped. Both halves must refuse
    independently."""
    st, run_dir = _state(leerie, tmp_path, delivery_gate={
        "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
        "samples_before": 3})
    del st.data["working_branch"]
    st.save()
    calls = _patch_judge(leerie, monkeypatch, [])
    # The prejudge resume path must not hand back a truthy list either.
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert calls == []
    assert "unmet_after" not in st.data["delivery_gate"]


def test_missing_staging_skips(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)
    import shutil
    shutil.rmtree(run_dir / "worktrees" / "staging")
    calls = _patch_judge(leerie, monkeypatch, [])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == [] and calls == []


def test_judge_crash_is_advisory(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)

    async def boom(**kwargs):
        raise leerie.WorkerError("judge boom")

    monkeypatch.setattr(leerie, "claude_p", boom)
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert "delivery_gate" not in st.data


def test_resume_after_completion_spawns_nothing(
        leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path, delivery_gate={
        "unmet_before": [], "samples_before": 1, "unmet_after": [],
        "samples_after": 1})
    calls = _patch_judge(leerie, monkeypatch, [])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == [] and calls == []


def test_resume_between_halves_reuses_the_verdict(
        leerie, tmp_path, monkeypatch):
    recorded = [{"item_index": 1, "item": ITEMS[1]["item"],
                 "evidence": "recorded"}]
    st, run_dir = _state(leerie, tmp_path, delivery_gate={
        "unmet_before": recorded, "samples_before": 3})
    calls = _patch_judge(leerie, monkeypatch, [])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == recorded and calls == []


# === recheck, executed =====================================================

def _with_unmet_before(leerie, tmp_path):
    return _state(leerie, tmp_path, delivery_gate={
        "unmet_before": [{"item_index": 1, "item": ITEMS[1]["item"],
                          "evidence": "e"}],
        "samples_before": 3})


def test_recheck_records_remedied(leerie, tmp_path, monkeypatch):
    st, run_dir = _with_unmet_before(leerie, tmp_path)
    calls = _patch_judge(leerie, monkeypatch, [_verdicts(True, True)])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    gate = st.data["delivery_gate"]
    assert gate["unmet_after"] == [] and gate["samples_after"] == 1
    assert len(calls) == 1


def test_recheck_records_residual(leerie, tmp_path, monkeypatch):
    st, run_dir = _with_unmet_before(leerie, tmp_path)
    _patch_judge(leerie, monkeypatch, [
        _verdicts(True, False), _verdicts(True, False),
        _verdicts(True, False)])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    on_disk = json.loads(st.path.read_text())
    residual = on_disk["delivery_gate"]["unmet_after"]
    assert [r["item_index"] for r in residual] == [1]
    assert residual[0]["item"] == ITEMS[1]["item"]


def test_recheck_noop_without_unmet_before(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path, delivery_gate={
        "unmet_before": [], "samples_before": 1})
    calls = _patch_judge(leerie, monkeypatch, [])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert calls == []
    assert "unmet_after" not in st.data["delivery_gate"]


def test_recheck_is_idempotent(leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path, delivery_gate={
        "unmet_before": [{"item_index": 1, "item": "x", "evidence": "e"}],
        "samples_before": 3, "unmet_after": [], "samples_after": 1})
    calls = _patch_judge(leerie, monkeypatch, [])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert calls == []


# === the conformer section (the routed value) ==============================

def test_section_carries_item_text_and_evidence(leerie):
    section = leerie._format_unmet_required_items_section([
        {"item_index": 1, "item": "never reference the forbidden term",
         "evidence": "3 occurrences in test fixtures"}])
    assert "never reference the forbidden term" in section
    assert "3 occurrences in test fixtures" in section
    assert "[1]" in section
    assert "commit" in section  # the instruction to actually fix, not report


# === wiring pins ===========================================================

class TestWiring:
    def test_run_phases_orders_the_three_calls(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        i_pre = src.index("_run_delivery_prejudge(")
        i_conf = src.index("_run_final_conformance(")
        i_re = src.index("_run_delivery_recheck(")
        assert i_pre < i_conf < i_re

    def test_final_conformance_injects_the_section(self, leerie):
        src = inspect.getsource(leerie._run_final_conformance)
        assert "_format_unmet_required_items_section(" in src
        assert "if unmet_required_items:" in src

    def test_unmet_list_is_actually_passed_through(self, leerie):
        """The prejudge result must reach _run_final_conformance — a
        call passing nothing would make the routing dead code."""
        src = inspect.getsource(leerie._run_phases)
        assert "unmet_required_items=unmet_delivery" in src

    def test_worker_registered(self, leerie):
        assert "delivery_judge" in leerie.WORKER_TYPES
        assert "delivery_judge" in leerie.PLANNING_WORKER_TYPES
        assert "delivery_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
        assert leerie.EFFORT_DEFAULT_PER_WORKER["delivery_judge"] == "medium"
        assert "delivery_judge" not in leerie.TIMEOUT_DEFAULT_PER_WORKER
        assert "delivery_judge" in leerie.SCHEMAS
