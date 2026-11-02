"""Site claims (DESIGN §5 *Every `fix` site is claimed, with how it is
resolved*; IMPLEMENTATION §5½ *Site claims*).

Measured motive (bench D3/P6): a plan covered every audited FILE while its
subtask re-raised an error where the audit's fix was to re-run the flow, and
the defect shipped. The audit now types each `fix` site's `resolution_kind`;
the bug-fixing planner types every subtask's `change_kind` and per-site
claims; code compares them through the `SITE_KIND_CLASS` equivalence table.

Discipline: the row builder and the check are executed on values; the
wiring is driven through the REAL `phase_defect_scope_audit` and the REAL
`phase_plan` with only `claude_p` stubbed, asserting what the planner's
feedback round actually receives and what State records.
"""
from __future__ import annotations

import asyncio
import json

import pytest

from tests.test_defect_scope_audit import (
    AUDIT, EFFORTS, MODELS, _caps, _patch_auditor, _state)


def _scope(*sites: dict) -> dict:
    return {"applicable": True, "defect_shape": "shape",
            "sites": [dict(s) for s in sites]}


def _site(symbol: str, kind: str | None, disposition: str = "fix",
          **extra) -> dict:
    s = {"file": "src/example_module.py", "symbol": symbol,
         "role": "decision_site", "disposition": disposition,
         "resolution": f"resolve {symbol}"}
    if kind is not None:
        s["resolution_kind"] = kind
    s.update(extra)
    return s


def _sub(sid: str, change_kind: str | None, *claims: tuple[str, list[str]]
         ) -> dict:
    s: dict = {"id": sid, "title": sid, "success_criteria_seed": "x"}
    if change_kind is not None:
        s["change_kind"] = change_kind
    if claims:
        s["claims"] = {"sites": [{"site_id": i, "resolution_kinds": k}
                                 for i, k in claims]}
    return s


def _plan(*subtasks: dict, not_needed=(), status="ready") -> dict:
    return {"domain": "bug-fixing", "status": status,
            "subtasks": list(subtasks),
            "sites_not_needed": [{"site_id": i, "reason": r}
                                 for i, r in not_needed]}


def _status(leerie, plan, scope) -> dict[str, str]:
    return {r["site_id"]: r["status"]
            for r in leerie._site_claim_rows(plan, scope)}


# === the equivalence table is the documented constant ======================

def test_equivalence_table_is_exactly_the_documented_one(leerie):
    assert leerie.SITE_KIND_CLASS == {
        "rerun_flow": "rerun_flow", "reraise": "reraise", "relink": "relink",
        "guard": "decide", "recompute": "decide", "propagate": "decide",
        "other": "other"}
    assert set(leerie.SITE_KIND_CLASS) == set(leerie.SITE_RESOLUTION_KINDS)


@pytest.mark.parametrize("claimed,audit,status", [
    # The decision-logic family collapses: any member satisfies any other.
    (["guard"], "recompute", "claimed"),
    (["propagate"], "guard", "claimed"),
    (["recompute"], "propagate", "claimed"),
    # The error-handling kinds stay distinct — the measured break.
    (["reraise"], "rerun_flow", "mismatch"),
    (["relink"], "reraise", "mismatch"),
    (["rerun_flow"], "relink", "mismatch"),
    (["guard"], "rerun_flow", "mismatch"),
    (["rerun_flow"], "guard", "mismatch"),
    # One equivalent kind among several is enough.
    (["reraise", "rerun_flow"], "rerun_flow", "claimed"),
])
def test_kind_equivalence_decides_the_verdict(leerie, claimed, audit, status):
    scope = _scope(_site("a", audit))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", claimed)))
    assert _status(leerie, plan, scope) == {"D1": status}


@pytest.mark.parametrize("claimed,audit", [
    (["other"], "recompute"),       # a claim of `other` contradicts nothing
    (["reraise"], "other"),         # nor does an audit of `other`
    (["other"], "rerun_flow"),
])
def test_other_never_contradicts_and_is_surfaced(leerie, claimed, audit):
    scope = _scope(_site("a", audit))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", claimed)))
    (row,) = leerie._site_claim_rows(plan, scope)
    assert row["status"] == "claimed"
    assert row["surface_other"] is True


def test_a_plain_equivalent_claim_is_not_surfaced(leerie):
    scope = _scope(_site("a", "guard"))
    (row,) = leerie._site_claim_rows(
        _plan(_sub("bugfix-001", "fix", ("D1", ["recompute"]))), scope)
    assert (row["status"], row["surface_other"]) == ("claimed", False)


# === only fix subtasks count; not_needed; ruled_out ========================

@pytest.mark.parametrize("change_kind", ["test", "docs", "other", None])
def test_only_fix_subtasks_claims_count(leerie, change_kind):
    scope = _scope(_site("a", "guard"))
    plan = _plan(_sub("test-001", change_kind, ("D1", ["guard"])))
    assert _status(leerie, plan, scope) == {"D1": "unclaimed"}


def test_not_needed_settles_an_unclaimed_site_and_keeps_the_reason(leerie):
    scope = _scope(_site("a", "guard"), _site("b", "reraise"))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", ["guard"])),
                 not_needed=[("D2", "already re-raises at :42")])
    rows = {r["site_id"]: r for r in leerie._site_claim_rows(plan, scope)}
    assert rows["D2"]["status"] == "not_needed"
    assert rows["D2"]["not_needed"] == "already re-raises at :42"
    assert rows["D1"]["not_needed"] is None


def test_an_empty_reason_is_no_not_needed(leerie):
    scope = _scope(_site("a", "guard"))
    plan = _plan(not_needed=[("D1", "   ")])
    assert _status(leerie, plan, scope) == {"D1": "unclaimed"}


def test_a_claim_wins_over_not_needed(leerie):
    scope = _scope(_site("a", "rerun_flow"))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", ["reraise"])),
                 not_needed=[("D1", "fine as is")])
    assert _status(leerie, plan, scope) == {"D1": "mismatch"}


def test_ruled_out_sites_need_nothing_and_ids_stay_positional(leerie):
    scope = _scope(_site("a", "other", disposition="ruled_out"),
                   _site("b", "guard"))
    rows = leerie._site_claim_rows(_plan(), scope)
    assert [(r["site_id"], r["symbol"], r["status"]) for r in rows] == [
        ("D2", "b", "unclaimed")]


def test_a_missing_or_unknown_audit_kind_reads_as_other(leerie):
    scope = _scope(_site("a", None), _site("b", "bogus"))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", ["reraise"]),
                      ("D2", ["guard"])))
    assert _status(leerie, plan, scope) == {"D1": "claimed", "D2": "claimed"}


def test_claims_from_several_fix_subtasks_are_pooled(leerie):
    scope = _scope(_site("a", "rerun_flow"))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", ["reraise"])),
                 _sub("bugfix-002", "fix", ("D1", ["rerun_flow"])))
    (row,) = leerie._site_claim_rows(plan, scope)
    assert row["status"] == "claimed"
    assert [c["sid"] for c in row["claims"]] == ["bugfix-001", "bugfix-002"]


# === _scope_sites ===========================================================

def test_scope_sites_fills_positional_ids_without_mutating(leerie):
    scope = _scope(_site("a", "guard"), _site("b", "guard", site_id="D9"))
    out = leerie._scope_sites(scope)
    assert [s["site_id"] for s in out] == ["D1", "D9"]
    assert "site_id" not in scope["sites"][0]


# === the check (issue strings) =============================================

def test_check_issues_name_the_site_the_audit_kind_and_the_exit(leerie):
    scope = _scope(_site("a", "rerun_flow"), _site("b", "guard"))
    plan = _plan(_sub("bugfix-001", "fix", ("D1", ["reraise"])))
    issues = leerie._check_site_claims(plan, scope)
    assert [i.split(" — ")[0] for i in issues] == [
        "SITE_KIND_MISMATCH: D1", "SITE_UNCLAIMED: D2"]
    mismatch, unclaimed = issues
    assert "bugfix-001: reraise" in mismatch
    assert "`rerun_flow`: resolve a" in mismatch
    assert "scope_note" in mismatch
    assert "src/example_module.py :: b" in unclaimed
    assert "sites_not_needed" in unclaimed
    # Gating: these must re-invoke the planner, not be logged and dropped.
    assert not any(leerie._issue_is_advisory(i) for i in issues)


@pytest.mark.parametrize("scope", [
    None, {"applicable": False}, {"applicable": True, "sites": []}])
def test_check_is_silent_without_an_applicable_audit(leerie, scope):
    assert leerie._check_site_claims(_plan(), scope) == []


def test_check_is_silent_on_a_blocked_plan(leerie):
    scope = _scope(_site("a", "guard"))
    assert leerie._check_site_claims(_plan(status="blocked"), scope) == []
    # ...and the same plan, ready, is not.
    assert leerie._check_site_claims(_plan(), scope) != []


# === schemas carry the typed fields ========================================

def _validate(schema: dict, payload: dict) -> None:
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(payload, schema)


def test_planner_schema_accepts_claims_and_rejects_bad_kinds(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    schema = leerie.SCHEMAS["planner"]
    sub = {"id": "bugfix-001", "title": "t", "success_criteria_seed": "c",
           "change_shape": "point", "independent_sites": False,
           "change_kind": "fix",
           "claims": {"sites": [{"site_id": "D1",
                                 "resolution_kinds": ["rerun_flow"]}]}}
    good = {"domain": "bug-fixing", "status": "ready", "blocker_kind": "none",
            "subtasks": [sub],
            "sites_not_needed": [{"site_id": "D2", "reason": "evidence"}]}
    _validate(schema, good)
    for bad in (
            {**good, "subtasks": [{**sub, "change_kind": "bugfix"}]},
            {**good, "subtasks": [{k: v for k, v in sub.items()
                                   if k != "change_kind"}]},
            {**good, "subtasks": [{**sub, "claims": {"sites": [
                {"site_id": "D1", "resolution_kinds": ["rethrow"]}]}}]},
            {**good, "subtasks": [{**sub, "claims": {"sites": [
                {"site_id": "D1", "resolution_kinds": []}]}}]},
            {**good, "sites_not_needed": [{"site_id": "D2", "reason": ""}]}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(bad, schema)


def test_only_the_planner_subtask_item_carries_claims(leerie):
    """`include_claims` emits `$ref`s into the planner's `$defs`; a schema
    without them must not carry the refs."""
    for key, path in (("reconciler", ("added_subtasks",)),
                      ("splitter", ("children",))):
        item = leerie.SCHEMAS[key]["properties"][path[0]]["items"]
        assert "claims" not in item["properties"]
        assert "change_kind" not in item["properties"]
    assert "change_kind" in (
        leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
        ["required"])


def test_auditor_schema_requires_a_typed_resolution_kind(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    schema = leerie.SCHEMAS["defect_scope_auditor"]
    site = {"file": "f", "symbol": "s", "role": "decision_site",
            "disposition": "fix", "resolution_kind": "relink",
            "resolution": "route into the existing path"}
    _validate(schema, {"applicable": True, "sites": [site]})
    for bad in ({**site, "resolution_kind": "retry"},
                {k: v for k, v in site.items() if k != "resolution_kind"},
                {**site, "site_id": "D1"}):
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"applicable": True, "sites": [bad]}, schema)


# === wiring: the real audit stamps IDs ======================================

def test_the_audit_stamps_positional_site_ids(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch, AUDIT)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert [(s["site_id"], s["symbol"]) for s in scope["sites"]] == [
        ("D1", "merge_candidates"), ("D2", "collect_pending_rows")]


# === wiring: the real phase_plan feeds back, then records ===================

SCOPE = _scope(_site("merge_candidates", "rerun_flow"),
               _site("collect_pending_rows", "guard"),
               _site("resolve_identity_key", "other",
                     disposition="ruled_out"))


def _planner_reply(*subtasks, not_needed=()):
    return {"domain": "bug-fixing", "status": "ready",
            "blocker_kind": "none", "subtasks": list(subtasks),
            "sites_not_needed": [{"site_id": i, "reason": r}
                                 for i, r in not_needed],
            "confidence": {"task_understanding": 9.5,
                           "decomposition_quality": 9.5, "basis": "stub"}}


def _drive(leerie, monkeypatch, st, replies):
    calls: list[dict] = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        if kw["schema_key"] != "planner":
            raise AssertionError(f"unexpected worker {kw['schema_key']}")
        return replies[min(len(calls), len(replies)) - 1]

    async def no_decompose(subtask, *a, **k):
        return [subtask]

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_recursive_decompose", no_decompose)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["planner_samples"] = 1
    asyncio.run(leerie.phase_plan("t", st, caps, MODELS, EFFORTS))
    return calls


def _sub_full(sid, change_kind, *claims):
    s = _sub(sid, change_kind, *claims)
    s.update({"files_likely_touched": [], "depends_on": [],
              "change_shape": "point", "size": "small"})
    return s


def test_planner_is_told_the_open_sites_and_the_record_follows(
        leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, defect_scope=SCOPE)
    first = _planner_reply(
        _sub_full("bugfix-001", "fix", ("D1", ["reraise"])))
    second = _planner_reply(
        _sub_full("bugfix-001", "fix", ("D1", ["rerun_flow"])),
        _sub_full("bugfix-002", "test", ("D2", ["guard"])),
        not_needed=[("D2", "guard already present at :88")])
    calls = _drive(leerie, monkeypatch, st, [first, second])

    assert len(calls) == 2, "the open sites must re-invoke the planner"
    # The ctx carries the code-assigned ids the planner claims against.
    ctx = calls[0]["user_prompt"]
    assert '"site_id": "D2"' in ctx
    feedback = calls[1]["user_prompt"]
    assert "SITE_KIND_MISMATCH: D1" in feedback
    assert "SITE_UNCLAIMED: D2" in feedback
    assert not [ln for ln in feedback.splitlines()
                if "SITE_" in ln and "D3" in ln], \
        "a ruled_out site is never fed back"

    rec = st.data["site_claims"]
    assert rec["domain"] == "bug-fixing"
    assert [(r["site_id"], r["status"]) for r in rec["sites"]] == [
        ("D1", "claimed"), ("D2", "not_needed")]
    assert rec["sites"][1]["not_needed"] == "guard already present at :88"


def test_a_residual_is_recorded_and_never_fatal(leerie, tmp_path,
                                                monkeypatch):
    st = _state(leerie, tmp_path, defect_scope=SCOPE)
    stuck = _planner_reply(_sub_full("bugfix-001", "fix",
                                     ("D1", ["reraise"])))
    calls = _drive(leerie, monkeypatch, st, [stuck])
    assert len(calls) >= 2
    rows = {r["site_id"]: r["status"] for r in st.data["site_claims"]["sites"]}
    assert rows == {"D1": "mismatch", "D2": "unclaimed"}


def test_another_domain_planner_is_not_checked(leerie, tmp_path,
                                               monkeypatch):
    st = _state(leerie, tmp_path, defect_scope=SCOPE,
                categories=["testing"])
    reply = dict(_planner_reply(_sub_full("test-001", "test")),
                 domain="testing")
    calls = _drive(leerie, monkeypatch, st, [reply])
    assert len(calls) == 1
    assert "site_claims" not in st.data


def test_planner_prompt_documents_the_fields(leerie):
    text = (leerie.ROOT / "prompts" / "planner.md").read_text()
    for name in ("change_kind", "claims", "resolution_kinds",
                 "sites_not_needed", "blocker_kind", "external_resource"):
        assert f"`{name}`" in text or f'"{name}"' in text, name
    aud = (leerie.ROOT / "prompts" / "defect_scope_auditor.md").read_text()
    for kind in leerie.SITE_RESOLUTION_KINDS:
        assert f"`{kind}`" in aud, kind
    assert json.dumps("resolution_kind") in aud


# === split children keep their parent's claims (stage 4c) ==================
#
# Claims are recorded on the first-pass plan; recursive decomposition then
# re-homes a split subtask into children. Before this, no child carried the
# parent's `change_kind`/`claims`, so a site the parent claimed was claimed
# by no leaf of the plan that actually executes.

def _claimed_parent(**extra) -> dict:
    p = _sub("bugfix-001", "fix", ("D1", ["rerun_flow"]),
             ("D2", ["guard", "propagate"]))
    p.update({"files_likely_touched": ["a.py", "b.py"], "intent": "i"})
    p.update(extra)
    return p


def test_every_code_built_child_carries_the_parents_claims(leerie):
    parent = _claimed_parent()
    chunk = leerie._migration_child(parent, ["a.py"], "bugfix-001-a", "t",
                                    "c")
    region = leerie._subfile_child(parent, "a.py", (1, 40), ["f"],
                                   "bugfix-001-r1", "cl", 0, 2,
                                   "bugfix-001-r1", True)
    for child in (chunk, region):
        assert child["change_kind"] == "fix"
        assert child["claims"] == parent["claims"]
        # A copy: one child's edit must not reach its siblings.
        assert child["claims"] is not parent["claims"]
        child["claims"]["sites"].append({"site_id": "D9",
                                         "resolution_kinds": ["other"]})
        assert len(parent["claims"]["sites"]) == 2
    # A subtask with no claims (another domain) gains none.
    bare = leerie._migration_child({"id": "test-001"}, ["a.py"], "t-a", "t",
                                   "c")
    assert "change_kind" not in bare and "claims" not in bare


def test_splitter_children_are_stamped_and_the_site_stays_claimed(leerie):
    from pathlib import Path
    from unittest.mock import patch

    from tests.conftest import _fit_response, _run, _split_response
    from tests.test_decompose_budget_share import _caps as dcaps
    from tests.test_decompose_budget_share import _make_real_state

    parent = _claimed_parent(title="P", success_criteria_seed="crit")

    async def fake_claude_p(*args, schema_key, sid="", **kwargs):
        if schema_key == "fit_judge":
            return _fit_response(0.10 if sid.endswith("-d0") else 0.9)
        if schema_key == "splitter":
            # The splitter's schema has no claims; one child even carries
            # a stray change_kind, which the parent's must replace.
            out = _split_response("bugfix-001", 2)
            out["children"][1]["change_kind"] = "test"
            return out
        pytest.fail(f"unexpected {schema_key}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie._recursive_decompose(
            parent, 0, _make_real_state(leerie), dcaps(leerie, 200),
            {"fit_judge": "sonnet", "splitter": "sonnet"},
            {"fit_judge": "medium", "splitter": "medium"}, Path("/tmp")))

    assert [s["id"] for s in leaves] == ["bugfix-001-1", "bugfix-001-2"]
    assert all(s["change_kind"] == "fix" for s in leaves)
    assert all(s["claims"] == parent["claims"] for s in leaves)
    scope = _scope(_site("a", "rerun_flow"), _site("b", "recompute"))
    rows = leerie._site_claim_rows(_plan(*leaves), scope)
    assert [(r["site_id"], r["status"]) for r in rows] == [
        ("D1", "claimed"), ("D2", "claimed")]
    assert [c["sid"] for c in rows[0]["claims"]] == [
        "bugfix-001-1", "bugfix-001-2"]


def test_phase_plan_rerecords_the_claims_over_the_leaves(
        leerie, tmp_path, monkeypatch):
    """The real phase_plan: the record written after decomposition names
    the leaves, with the first-pass verdicts."""
    st = _state(leerie, tmp_path, defect_scope=SCOPE)
    reply = _planner_reply(
        _sub_full("bugfix-001", "fix", ("D1", ["rerun_flow"]),
                  ("D2", ["guard"])))

    async def fake_claude_p(**kw):
        assert kw["schema_key"] == "planner"
        return reply

    async def split_in_two(subtask, *a, **k):
        return [leerie._migration_child(subtask, [f], f"{subtask['id']}-{n}",
                                        "t", "c")
                for n, f in enumerate(("a.py", "b.py"), 1)]

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_recursive_decompose", split_in_two)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["planner_samples"] = 1
    plans = asyncio.run(leerie.phase_plan("t", st, caps, MODELS, EFFORTS))

    assert [s["id"] for s in plans[0]["subtasks"]] == [
        "bugfix-001-1", "bugfix-001-2"]
    rows = st.data["site_claims"]["sites"]
    assert [(r["site_id"], r["status"]) for r in rows] == [
        ("D1", "claimed"), ("D2", "claimed")]
    assert [c["sid"] for c in rows[0]["claims"]] == [
        "bugfix-001-1", "bugfix-001-2"]
