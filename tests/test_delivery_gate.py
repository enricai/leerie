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


def test_no_item_key_serialization_excludes_stray_item_index(
        leerie, tmp_path, monkeypatch):
    """Review round 6: the no-item-key serialization must EXCLUDE a
    stray item_index from the text — the positional index is
    authoritative, and echoing a contradicting number into the judge
    payload invites misaligned verdicts. Discriminating: without the
    filter the text is '{"item_index": 99, "requirement": ...}'."""
    st, run_dir = _state(leerie, tmp_path, required_items=[
        {"requirement": "keyless with stray index", "item_index": 99}])
    calls = _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
        {"verdicts": [{"item_index": 0, "met": False, "evidence": "e"}]},
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    text = unmet[0]["item"]
    assert "keyless with stray index" in text
    assert "item_index" not in text
    assert "99" not in text
    # The payload's authoritative index is still positional.
    assert '"item_index": 0' in calls[0]["user_prompt"]


def test_nested_item_value_serializes_with_sorted_keys(
        leerie, tmp_path, monkeypatch):
    """Review round 6: both json.dumps coercion branches sort keys, so
    the same requirement always produces the same judge-payload text
    across samples (vote alignment is by index, but identical text
    keeps the three samples judging the same rendering).
    Discriminating: an unsorted dump of this fixture starts with
    'zebra'."""
    st, run_dir = _state(leerie, tmp_path, required_items=[
        {"item": {"zebra": 1, "alpha": 2}}])
    calls = _patch_judge(leerie, monkeypatch, [
        {"verdicts": [{"item_index": 0, "met": True, "evidence": "ok"}]}])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    prompt = calls[0]["user_prompt"]
    assert "alpha" in prompt and "zebra" in prompt
    assert prompt.index("alpha") < prompt.index("zebra"), (
        "the nested value must serialize with sorted keys — alpha "
        "before zebra in the payload text; an unsorted dump of this "
        "fixture starts with zebra")


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


def test_dict_item_without_item_key_gets_serialized_text(
        leerie, tmp_path, monkeypatch):
    """Review rounds 2+4: a dict item lacking an 'item' key (state-
    reachable, not model-reachable — the classifier schema requires
    'item') must hand every downstream consumer USABLE text. The first
    fix mapped it to "" — indistinguishable from pre-fix behavior, so
    the original form of this test passed unfixed code (round 4:
    non-discriminating). Now the whole entry is serialized as the item
    text, and the assertions demand the CONTENT, which the pre-fix ""
    cannot satisfy."""
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
    assert isinstance(unmet[0]["item"], str)
    assert "no item key here" in unmet[0]["item"]
    # The conformer section carries the requirement's content, not a
    # blank line.
    section = leerie._format_unmet_required_items_section(unmet)
    assert "no item key here" in section


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
        # Routed on EITHER half: unmet items or an unmet contract
        # (DESIGN §8 *The gate judges the finding, not only the items*).
        assert ("if unmet_required_items or _contract_unmet is not None:"
                in src)

    def test_unmet_list_is_actually_passed_through(self, leerie):
        """The prejudge result must reach _run_final_conformance — a
        call passing nothing would make the routing dead code."""
        src = inspect.getsource(leerie._run_phases)
        assert "unmet_required_items=unmet_delivery" in src

    def test_recheck_spawns_on_either_flagged_half(self, leerie):
        """The recheck call site must fire on unmet ITEMS or an unmet
        CONTRACT — gating on the items list alone left the
        contract-only case (every item met, finding open) with a
        recheck that never ran (review round 1; the behavioral half
        is test_contract_only_unmet_triggers_recheck_and_records_after,
        which drives the recheck body — this pin covers the call
        site that decides whether it is reached at all)."""
        src = inspect.getsource(leerie._run_phases)
        assert "if unmet_delivery or _delivery_recheck_due(st):" in src
        # and the predicate itself is the state-reading one
        psrc = inspect.getsource(leerie._delivery_recheck_due)
        assert '"contract_before"' in psrc and '"unmet"' in psrc

    def test_worker_registered(self, leerie):
        assert "delivery_judge" in leerie.WORKER_TYPES
        assert "delivery_judge" in leerie.PLANNING_WORKER_TYPES
        assert "delivery_judge" not in leerie.MODEL_DEFAULT_PER_WORKER
        assert leerie.EFFORT_DEFAULT_PER_WORKER["delivery_judge"] == "medium"
        assert "delivery_judge" not in leerie.TIMEOUT_DEFAULT_PER_WORKER
        assert "delivery_judge" in leerie.SCHEMAS


# === turn budget scales with item count (measured incident) ================

@pytest.mark.parametrize("n_items,expected_turns", [
    # Disagreeing values on purpose: a bypass that hardcodes any one
    # number fails the other params (CLAUDE.md: parametrized value
    # tests make inputs disagree).
    (1, 36),    # floor region: base 30 + 6 (smallest real list)
    (9, 84),    # the measured live incident's larger item count
    (11, 90),   # first clamped count: 30 + 66 = 96 clamps to 90
    (20, 90),   # deep in the clamp: 30 + 120 clamps to 90
])
def test_judge_turn_budget_scales_with_item_count(
        leerie, tmp_path, monkeypatch, n_items, expected_turns):
    """min(30 + 6*items, 90), asserted on the value claude_p RECEIVES by
    executing the real prejudge — not by reading source. A fixed
    max_turns=30 was measured crashing all four live judge attempts at
    turns=31 (2026-09-29, both v0.32.1 runs, 9- and 6-item lists),
    skipping the gate in a run where a bench replay of the same payload
    flags an item unmet."""
    items = [{"item": f"required item {i}", "source_ref": "task"}
             for i in range(n_items)]
    st, run_dir = _state(leerie, tmp_path, required_items=items)
    calls = _patch_judge(
        leerie, monkeypatch, [_verdicts(*([True] * n_items))])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert len(calls) == 1
    assert calls[0]["max_turns"] == expected_turns


# === finding-level contract verdict (DESIGN §8 *The gate judges the
# === finding, not only the items*) — executed through the real gate

DEFECT_SCOPE = {
    "applicable": True,
    "defect_shape": "every matching record must be selected regardless "
                    "of its position",
    "sites": [{"file": "src/example_module.py",
               "symbol": "merge_candidates", "role": "decision_site"}],
}


def _contract(verdict, evidence="residual at site", conflicts=None):
    c = {"verdict": verdict, "evidence": evidence}
    if conflicts is not None:
        c["conflicting_contracts"] = conflicts
    return c


def test_contract_unmet_escalates_persists_and_budgets(
        leerie, tmp_path, monkeypatch):
    """Items all met but contract unmet on sample 0 → escalate to 3
    samples; 2-of-3 unmet persists contract_before with the evidence
    VALUE; the budget carries the +12 contract allowance: 54 at the
    harness's two items, where item-only arithmetic gives 42 (the
    no-defect-scope test pins that 42, so the pair discriminates —
    54 alone would not, since item-only reaches it at four items)."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("unmet", "gap A")},
        {**_verdicts(True, True), "contract": _contract("unmet", "gap B")},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert unmet == []
    assert len(calls) == 3, "a flagged contract must buy the vote"
    assert calls[0]["max_turns"] == 54
    gate = st.data["delivery_gate"]
    assert gate["contract_before"]["verdict"] == "unmet"
    # evidence comes from the LAST sample voting the winning verdict
    assert gate["contract_before"]["evidence"] == "gap B"
    assert "DEFECT CONTRACT" in calls[0]["user_prompt"]
    assert DEFECT_SCOPE["defect_shape"] in calls[0]["user_prompt"]


def test_contract_met_on_clean_first_pass_costs_one_sample(
        leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("met", "holds")}])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1
    assert st.data["delivery_gate"]["contract_before"]["verdict"] == "met"


def test_no_defect_scope_means_no_contract_section_or_allowance(
        leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path)
    calls = _patch_judge(leerie, monkeypatch, [_verdicts(True, True)])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert "DEFECT CONTRACT" not in calls[0]["user_prompt"]
    assert calls[0]["max_turns"] == 42  # 30 + 6*2, no +12
    assert "contract_before" not in st.data["delivery_gate"]


def test_contract_split_vote_fails_open_to_met(
        leerie, tmp_path, monkeypatch):
    """1-1-1 over {unmet, conflict, met} reaches no 2-of-3 → met,
    mirroring the items' fail-open direction."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("unmet")},
        {**_verdicts(True, True), "contract": _contract("conflict")},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert st.data["delivery_gate"]["contract_before"]["verdict"] == "met"


def test_contract_conflict_persists_both_contracts_and_skips_recheck(
        leerie, tmp_path, monkeypatch):
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True),
         "contract": _contract("conflict", "both pinned",
                               ["contract A", "contract B"])},
        {**_verdicts(True, True),
         "contract": _contract("conflict", "both pinned",
                               ["contract A", "contract B"])},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    cb = st.data["delivery_gate"]["contract_before"]
    assert cb["verdict"] == "conflict"
    assert cb["conflicting_contracts"] == ["contract A", "contract B"]
    # a conflict routes to the record, not the fix loop: the recheck
    # must not spend judge samples on it
    n_before = len(calls)
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == n_before
    assert "contract_after" not in st.data["delivery_gate"]


def test_contract_only_unmet_triggers_recheck_and_records_after(
        leerie, tmp_path, monkeypatch):
    """No unmet ITEMS, but an unmet contract, must still buy the
    post-conformer recheck; the remedied verdict lands in
    contract_after."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    st.data["delivery_gate"] = {
        "unmet_before": [], "samples_before": 3,
        "contract_before": _contract("unmet", "gap")}
    st.save()
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("met", "fixed")}])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1
    assert st.data["delivery_gate"]["contract_after"]["verdict"] == "met"


def test_formatter_contract_block_carries_the_evidence_value(leerie):
    text = leerie._format_unmet_required_items_section(
        [], {"verdict": "unmet",
             "evidence": "variant B of the contract still reproducible"})
    assert "DEFECT CONTRACT" in text
    assert "variant B of the contract still reproducible" in text


def test_exec_digest_section_reaches_the_judge(
        leerie, tmp_path, monkeypatch):
    """The executed-commands record must be IN the payload the judge
    receives, with the command text — value, not key."""
    st, run_dir = _state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    logs = run_dir / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "conformer-x.log").write_text(json.dumps({
        "message": {"content": [
            {"type": "tool_use", "id": "u1", "name": "Bash",
             "input": {"command": "pnpm test src/a.test.ts"}}]}}) + "\n")
    calls = _patch_judge(leerie, monkeypatch, [_verdicts(True, True)])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    up = calls[0]["user_prompt"]
    assert "EXECUTED COMMANDS RECORD" in up
    assert "pnpm test src/a.test.ts" in up


def test_conflict_discovered_at_recheck_is_recorded(
        leerie, tmp_path, monkeypatch):
    """The recheck can DISCOVER a conflict the pre-pass called unmet
    (review round 2: the arm existed only as code); it lands in
    contract_after for the next run's steering."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    st.data["delivery_gate"] = {
        "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
        "samples_before": 3,
        "contract_before": _contract("unmet", "gap")}
    st.save()
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True),
         "contract": _contract("conflict", "both pinned",
                               ["contract A", "contract B"])},
        {**_verdicts(True, True),
         "contract": _contract("conflict", "both pinned",
                               ["contract A", "contract B"])},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 3
    ca = st.data["delivery_gate"]["contract_after"]
    assert ca["verdict"] == "conflict"
    assert ca["conflicting_contracts"] == ["contract A", "contract B"]


def test_pre_pass_conflict_with_unmet_items_rechecks_the_contract(
        leerie, tmp_path, monkeypatch):
    """A pre-pass CONFLICT alone never buys a recheck — but one forced
    by unmet ITEMS re-judges the contract, and a met verdict there
    supersedes the pre-pass conflict in contract_after (review round
    5: a comment claimed this arm existed; now it does)."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    st.data["delivery_gate"] = {
        "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
        "samples_before": 3,
        "contract_before": _contract("conflict", "both pinned",
                                     ["contract A", "contract B"])}
    st.save()
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("met", "resolved")}])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1
    gate = st.data["delivery_gate"]
    assert gate["unmet_after"] == []
    assert gate["contract_after"]["verdict"] == "met"


# === ground-truth availability + the unverifiable verdict (DESIGN §8;
# === S-4) — executed through the real gate against the real filesystem

def _scope_with_ground_truth(*inputs):
    """DEFECT_SCOPE plus a data-dependent ground_truth whose input
    paths the gate re-checks against the REAL filesystem at judge
    time (the audit-time `present` flags are deliberately wrong in
    these fixtures, so a gate that trusts them instead of re-checking
    fails the assertions)."""
    return {**DEFECT_SCOPE, "ground_truth": {
        "data_dependent": True,
        "inputs": [{"path": str(p), "kind": "directory", "role": "archive",
                    "present": stale}
                   for p, stale in inputs],
        "repro_command": "run the generator against the archive"}}


def test_ungrounded_met_is_downgraded_to_unverifiable(
        leerie, tmp_path, monkeypatch):
    """The load-bearing S-4 arm: a judge that says met while the
    report's defect is data-dependent and EVERY named input is absent
    is recorded as unverifiable with the claim preserved — the exact
    shape two consecutive live v0.33.0 gates shipped silently. The
    stale present=True flag proves the gate re-checks the filesystem
    rather than trusting the audit-time record."""
    absent = tmp_path / "no-such-archive"
    st, run_dir = _state(
        leerie, tmp_path,
        defect_scope=_scope_with_ground_truth((absent, True)))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True),
         "contract": _contract("met", "synthetic fixtures pass")}])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1, "downgrade must not buy extra samples"
    cb = st.data["delivery_gate"]["contract_before"]
    assert cb["verdict"] == "unverifiable"
    assert cb["judge_claimed"] == "met"
    assert "synthetic fixtures pass" in cb["evidence"]
    assert str(absent) in cb["evidence"]
    prompt = calls[0]["user_prompt"]
    assert "GROUND-TRUTH AVAILABILITY" in prompt
    assert f"{absent} -- ABSENT" in prompt


def test_grounded_met_stands_when_every_input_is_present(
        leerie, tmp_path, monkeypatch):
    """Converse of the downgrade arm (inputs disagree with the stale
    flags in the OTHER direction): all inputs exist → met stands and
    the payload carries no availability caveat."""
    present = tmp_path / "real-archive"
    present.mkdir()
    st, run_dir = _state(
        leerie, tmp_path,
        defect_scope=_scope_with_ground_truth((present, False)))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("met", "holds")}])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    cb = st.data["delivery_gate"]["contract_before"]
    assert cb["verdict"] == "met"
    assert "judge_claimed" not in cb
    # The DEFECT CONTRACT instruction legitimately NAMES the section;
    # what must be absent is the section itself (its per-input lines).
    assert "-- ABSENT" not in calls[0]["user_prompt"]
    assert "-- PRESENT" not in calls[0]["user_prompt"]


def test_partially_present_inputs_caveat_but_no_downgrade(
        leerie, tmp_path, monkeypatch):
    """One of two inputs absent: the availability section lists each
    with its own flag (the judge weighs the gap), but the mechanical
    downgrade requires EVERY input absent — a partially grounded met
    is the judge's call, not the orchestrator's."""
    present = tmp_path / "real-archive"
    present.mkdir()
    absent = tmp_path / "gone-flow.json"
    st, run_dir = _state(
        leerie, tmp_path,
        defect_scope=_scope_with_ground_truth((present, False),
                                              (absent, True)))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True), "contract": _contract("met", "holds")}])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert st.data["delivery_gate"]["contract_before"]["verdict"] == "met"
    prompt = calls[0]["user_prompt"]
    assert f"{present} -- PRESENT" in prompt
    assert f"{absent} -- ABSENT" in prompt


def test_unverifiable_majority_wins_the_vote(
        leerie, tmp_path, monkeypatch):
    """unverifiable is a first-class tally verdict (no ground_truth in
    state here, so this is the vote path, not the downgrade): sample 0
    flags → escalate; 2-of-3 unverifiable persists, evidence from the
    last sample voting it."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True),
         "contract": _contract("unverifiable", "cannot decide A")},
        {**_verdicts(True, True),
         "contract": _contract("unverifiable", "cannot decide B")},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 3
    cb = st.data["delivery_gate"]["contract_before"]
    assert cb["verdict"] == "unverifiable"
    assert cb["evidence"] == "cannot decide B"


def test_unverifiable_does_not_trigger_the_recheck(
        leerie, tmp_path, monkeypatch):
    """There is nothing on the tree for a conformer to fix, so an
    unverifiable contract (items all met) must not buy a recheck —
    _delivery_recheck_due stays False and the recheck spends no
    samples."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    st.data["delivery_gate"] = {
        "unmet_before": [], "samples_before": 3,
        "contract_before": {"verdict": "unverifiable",
                            "judge_claimed": "met", "evidence": "e"}}
    st.save()
    assert leerie._delivery_recheck_due(st) is False
    calls = _patch_judge(leerie, monkeypatch, [])
    asyncio.run(leerie._run_delivery_recheck(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert calls == []
    assert "contract_after" not in st.data["delivery_gate"]


def test_voted_path_downgrade_fires_at_three_samples(
        leerie, tmp_path, monkeypatch):
    """Round-1 falsification gap: deleting the VOTED-path downgrade
    call left the whole suite green, because every availability
    fixture exited through the 1-sample early return. This arm forces
    the vote (sample 0 flags an item at 1-of-3 — not confirmed) while
    all three samples claim contract met over an all-absent ground
    truth: the recorded verdict must still be the downgrade's."""
    absent = tmp_path / "never-there"
    st, run_dir = _state(
        leerie, tmp_path,
        defect_scope=_scope_with_ground_truth((absent, True)))
    calls = _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, False), "contract": _contract("met", "m0")},
        {**_verdicts(True, True), "contract": _contract("met", "m1")},
        {**_verdicts(True, True), "contract": _contract("met", "m2")},
    ])
    unmet = asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 3
    assert unmet == []  # 1-of-3 never confirms the item
    cb = st.data["delivery_gate"]["contract_before"]
    assert cb["verdict"] == "unverifiable"
    assert cb["judge_claimed"] == "met"
    assert str(absent) in cb["evidence"]


def test_voted_unverifiable_log_claims_no_data_absence(
        leerie, tmp_path, monkeypatch, capsys):
    """A VOTED unverifiable with no ground_truth must not tell the
    operator the data-absence narrative or prescribe --inspect-dir —
    the orchestrator never established either (round-1 defect: the
    logger asserted both with zero absent-input bullets). It states
    the judge's evidence instead."""
    st, run_dir = _state(leerie, tmp_path, defect_scope=dict(DEFECT_SCOPE))
    _patch_judge(leerie, monkeypatch, [
        {**_verdicts(True, True),
         "contract": _contract("unverifiable", "judge's own reason")},
        {**_verdicts(True, True),
         "contract": _contract("unverifiable", "judge's own reason")},
        {**_verdicts(True, True), "contract": _contract("met")},
    ])
    asyncio.run(leerie._run_delivery_prejudge(
        run_dir, st, _caps(leerie), MODELS, EFFORTS))
    out = capsys.readouterr().out
    assert "UNVERIFIABLE per the judge" in out
    assert "judge's own reason" in out
    assert "--inspect-dir" not in out
    assert "external data no" not in out
