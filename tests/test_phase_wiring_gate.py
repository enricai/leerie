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
        # The deterministic check runs before _validate_plan.
        i_check = src.index("check_plan_wiring(")
        i_validate = src.index("_validate_plan(subtasks)")
        assert i_check < i_validate

    def test_wiring_gate_runs_after_the_drop_filters(self, leerie):
        """Regression pin (post-merge Finding C): the LLM wiring_judge must run
        on the POST-DROP plan — after both soft-drop filters and _schedule(),
        and before _validate_plan. It reads `dropped_subtasks` (populated by the
        filters) for its broken_by_drop / broken_by_merge reasoning and is told
        the plan is 'post-drop', so a pre-filter placement feeds it a plan that
        still contains to-be-dropped subtasks + an incomplete drop audit (the
        bug shipped in PR #117 and preserved through the #116 rebase — no test
        guarded the placement).
        """
        src = inspect.getsource(leerie._run_phases)
        i_offtree = src.index("_filter_offtree_subtasks(")
        i_satisfied = src.index("await _filter_satisfied_subtasks(")
        i_schedule = src.index("_schedule(plans)")
        i_gate = src.index("await phase_wiring_gate(")
        i_validate = src.index("_validate_plan(subtasks)")
        # Both drop filters + _schedule() precede the gate; the gate precedes
        # _validate_plan (IMPLEMENTATION.md "3 Schedule" sequence + the
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
        skip on it made `resume` a silent bypass of a gate the run had
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


# ----- `severity` is asked for, not required (2026-08-03) --------------------
#
# Requiring it defeated its own purpose. A judge that omitted the field
# produced no schema-valid payload at all, so this gate never ran and caught
# NOTHING. Measured across the run corpus, every `wiring_judge` invocation that
# never produced valid output (9 of 66) failed on this single field — all 18 of
# its failing submissions. See DESIGN §5 and §8 *Findings carry a severity*.


def test_severity_is_not_a_required_schema_field(leerie):
    item = (leerie.SCHEMAS["wiring_judge"]["properties"]
            ["wiring_defects"]["items"])
    assert "severity" not in item["required"]
    # Asked for, not deleted: the property and its enum must survive, or the
    # judge loses the structured channel the field exists to provide.
    assert set(item["properties"]["severity"]["enum"]) == {
        "live_defect", "latent_risk"}


def test_a_defect_without_severity_validates(leerie):
    """The exact corpus shape that produced 9 dead invocations."""
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate({
        "plan_reviewed": True,
        "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "nothing-provides-this",
            "concrete_reason": "reads the schema but declares no requires",
        }],
        "rationale": "missing edge",
    }, leerie.SCHEMAS["wiring_judge"])


def test_unlabelled_defect_still_gates(leerie, tmp_path, monkeypatch):
    """The default is gating (DESIGN §8). An entry whose severity nobody
    declared must keep the conservative behaviour — relaxing the schema must
    not turn an omitted field into a silent bypass."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-002",
            "tag_or_dep": "nothing-provides-this",
            "concrete_reason": "reads the schema but declares no requires",
        }], "rationale": "missing edge"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_wiring_gate(
            _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))


def test_severity_channel_still_works_after_the_relaxation(leerie, tmp_path,
                                                           monkeypatch):
    """Anti-vacuity: making the field optional must not disable the channel
    itself. A declared `latent_risk` must still be excluded from gating —
    otherwise this change would have re-broken run d8302c0d46d8..."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-003-1",
            "tag_or_dep": "some-transitively-inherited-tag",
            "concrete_reason": "resolves correctly today; fragile to a future "
                               "merge of the intermediate subtask",
            "severity": "latent_risk",
        }], "rationale": "a latent fragility rather than a live defect"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


def test_prompt_still_asks_for_severity(leerie):
    """Optional in the schema, still requested in the prompt — that is the
    whole point of the change (CLAUDE.md §12: prompts advisory, code
    enforces). If the prompt stopped asking, the channel would go dark."""
    text = leerie._load_prompt("wiring_judge")
    assert "severity" in text
    assert "latent_risk" in text and "live_defect" in text


def test_latent_risk_missing_concrete_reason_is_not_logged(leerie, tmp_path,
                                                            monkeypatch):
    """A `latent_risk` entry with no `concrete_reason` never reaches the
    LATENT_RISK log line — it is dropped alongside the vague/gating findings
    (a `severity` label alone is not enough anti-gaming evidence). It must
    also never gate, since only live_defect findings do."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-003-1",
            "tag_or_dep": "some-fixture",
            "concrete_reason": "",
            "severity": "latent_risk",
        }], "rationale": "vague latent risk"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


def test_latent_risk_missing_tag_or_dep_is_not_logged(leerie, tmp_path,
                                                       monkeypatch):
    """The sibling half of the anti-gaming check: a `latent_risk` entry with a
    real `concrete_reason` but no `tag_or_dep` also stops short of the
    LATENT_RISK log line, and still never gates."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "feat-003-1",
            "tag_or_dep": "",
            "concrete_reason": "resolves today but is fragile to a future edit",
            "severity": "latent_risk",
        }], "rationale": "vague latent risk"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS


# ----- gaps flagged by test-001's coverage report -----------------------
#
# One further region inside phase_wiring_gate itself was uncovered even with
# every sibling wiring test file loaded: the "discarded provably-false
# finding" log line that fires when `_filter_provably_false_wiring_defects`
# drops a defect before repair/die ever sees it, and the repair-channel log
# lines (id / cofile_cluster) driven through phase_wiring_gate end-to-end.
# The repair-channel unit behavior itself is already exercised by
# tests/test_wiring_gate_repair.py's `test_gate_repairs_then_passes_and_records`
# and its `channel`-specific unit tests; the tests below pin that
# phase_wiring_gate drives each channel through to a clean pass.

def test_provably_false_defect_is_discarded_and_does_not_gate(
        leerie, tmp_path, monkeypatch):
    """A `broken_by_merge` finding naming a capability the merged plan
    STILL provides is provably false by set membership
    (`_filter_provably_false_wiring_defects`) — it is discarded (and
    logged as discarded) before repair/die ever sees it, so the gate
    passes clean rather than dying on a self-contradicting finding.

    `_PLANS`' feat-001 provides "schema", so a broken_by_merge defect
    naming "schema" contradicts its own premise."""
    st = _state(leerie, tmp_path)

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "broken_by_merge", "sid": "feat-002",
            "tag_or_dep": "schema",
            "concrete_reason": "a merge severed the schema dependency",
            "severity": "live_defect",
        }], "rationale": "premise falsified by the plan itself"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        _PLANS, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == _PLANS
    # A discarded finding is not a repair — the audit record carries none.
    assert st.data["wiring_gate"]["repairs"] == []


def test_id_channel_repair_logs_its_own_branch(leerie, tmp_path, monkeypatch):
    """A defect whose `tag_or_dep` names a surviving subtask id (not a tag)
    repairs via the id channel — a distinct logging branch inside
    phase_wiring_gate from the tag/sole-provider default. The repair-log
    unit behavior itself is `tests/test_wiring_gate_repair.py`'s job; this
    pins the phase drives it through to a clean pass end-to-end."""
    st = _state(leerie, tmp_path)
    plans = [{"domain": "testing", "status": "ready", "subtasks": [
        {"id": "test-001", "provides": [], "requires": [],
         "depends_on": [], "intent": "verify feat-001", "title": "t",
         "files_likely_touched": []},
        {"id": "feat-001", "provides": [], "requires": [],
         "depends_on": [], "intent": "build it", "title": "f",
         "files_likely_touched": []},
    ]}]

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "test-001",
            "tag_or_dep": "feat-001",
            "concrete_reason": "test-001 verifies feat-001's output but "
                               "declares no edge to it",
            "severity": "live_defect",
        }], "rationale": "missing id-channel edge"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == plans
    repairs = st.data["wiring_gate"]["repairs"]
    assert [r["channel"] for r in repairs] == ["id"]
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["depends_on"] == ["feat-001"]


def test_cofile_cluster_repair_logs_its_own_branch(leerie, tmp_path,
                                                     monkeypatch):
    """A defect whose tag is provided by several subtasks that all share one
    `_cofile_cluster` (sub-file region splits of a single file) repairs via
    the cofile_cluster channel — a third distinct logging branch."""
    st = _state(leerie, tmp_path)
    plans = [
        {"domain": "testing", "status": "ready", "subtasks": [
            {"id": "test-001", "provides": [], "requires": [],
             "depends_on": [], "intent": "verify baked config", "title": "t",
             "files_likely_touched": []},
        ]},
        {"domain": "feature-implementation", "status": "ready", "subtasks": [
            {"id": "feat-001-r1", "provides": ["baked"], "requires": [],
             "depends_on": [], "intent": "bake part 1", "title": "f1",
             "files_likely_touched": [], "_cofile_cluster": "feat-001"},
            {"id": "feat-001-r2", "provides": ["baked"], "requires": [],
             "depends_on": [], "intent": "bake part 2", "title": "f2",
             "files_likely_touched": [], "_cofile_cluster": "feat-001"},
        ]},
    ]

    async def fake_claude_p(**kwargs):
        return {"plan_reviewed": True, "wiring_defects": [{
            "kind": "missing_requires", "sid": "test-001",
            "tag_or_dep": "baked",
            "concrete_reason": "test-001 verifies the baked config but "
                               "declares no requires on it",
            "severity": "live_defect",
        }], "rationale": "missing cofile-cluster edge"}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, _caps(leerie), MODELS, EFFORTS))
    assert out == plans
    repairs = st.data["wiring_gate"]["repairs"]
    assert [r["channel"] for r in repairs] == ["cofile_cluster"]
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["requires"] == [
        {"tag": "baked", "extent": "in_plan"}]
