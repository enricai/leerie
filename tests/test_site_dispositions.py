"""Per-site dispositions (DESIGN §5 *Every site carries a disposition*).

The dominant cause of serial narrow fixing (5 of 11 historical repeat pairs)
was the audit: the later-fixed site was usually LISTED, as a "consumer" left
to mechanical consequence. Each site now carries a required `disposition`
(evidence in `note`); the planner must claim every `fix` site, and the site
claims check compares only those.
"""
from __future__ import annotations

import asyncio

from tests.test_defect_scope_audit import (AUDIT, EFFORTS, MODELS, _caps,
                                           _patch_auditor, _state)


def test_schema_requires_disposition_without_extra_fields(leerie):
    sch = leerie.SCHEMAS["defect_scope_auditor"]
    site = sch["properties"]["sites"]["items"]
    # resolution_kind: how a `fix` site must be resolved (DESIGN §5 *Every
    # `fix` site is claimed*) — an enum, not a free-form string.
    assert sorted(site["required"]) == [
        "disposition", "extent", "file", "resolution_kind", "role", "symbol"]
    # No separate free-form evidence field (the strict grammar's
    # expensive element); the evidence lives in `note`.
    assert "evidence" not in site["properties"]
    assert site["properties"]["disposition"]["enum"] == ["fix", "ruled_out"]
    # No top-level already_resolved_on_tree: under strict decoding the
    # variants carrying it emptied `sites` in 5 of 18 calls, against 0 of
    # 6 with dispositions alone and 0 of 10 on the prior schema.
    assert sorted(sch["required"]) == ["applicable", "reading", "sites"]
    assert "already_resolved_on_tree" not in sch["properties"]


def _disposed_audit():
    return {**AUDIT, "sites": [
        {"file": "src/example_module.py", "symbol": "merge_candidates",
         "role": "decision_site", "disposition": "fix",
         "note": "example_module.py:120 compares by position"},
        {"file": "src/other_module.py", "symbol": "render_rows",
         "role": "consumer", "disposition": "ruled_out",
         "note": "other_module.py:40 only formats the resolved key"},
    ]}


def test_scope_keeps_dispositions(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch, _disposed_audit())
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert [s["disposition"] for s in scope["sites"]] == ["fix", "ruled_out"]
    assert scope["sites"][1]["note"].startswith("other_module.py:40")


def _plan_claiming(*site_ids):
    return {"subtasks": [{"id": "bugfix-001", "change_kind": "fix",
                          "claims": {"sites": [
                              {"site_id": i, "resolution_kinds": ["guard"]}
                              for i in site_ids]}}]}


def test_only_fix_sites_need_a_claim(leerie):
    scope = {"applicable": True, "sites": _disposed_audit()["sites"]}
    rows = leerie._site_claim_rows(_plan_claiming(), scope)
    assert [(r["site_id"], r["symbol"], r["status"]) for r in rows] == [
        ("D1", "merge_candidates", "unclaimed")]


def test_every_fix_site_claimed_means_no_issue(leerie):
    scope = {"applicable": True, "sites": [
        dict(s, resolution_kind="guard")
        for s in _disposed_audit()["sites"]]}
    assert leerie._check_site_claims(
        {"status": "ready", **_plan_claiming("D1")}, scope) == []


def test_legacy_site_without_disposition_still_counts(leerie):
    """A recorded scope from before dispositions existed still needs a
    claim."""
    scope = {"applicable": True, "sites": [
        {"file": "src/legacy.py", "symbol": "old_site", "role": "bypass"}]}
    rows = leerie._site_claim_rows(_plan_claiming(), scope)
    assert [(r["symbol"], r["status"]) for r in rows] == [
        ("old_site", "unclaimed")]


def test_prompts_carry_the_rules(leerie):
    auditor = leerie._load_prompt("defect_scope_auditor")
    assert "is not a ruling-out by" in auditor
    planner = leerie._load_prompt("planner")
    assert "disposed `fix` must be covered" in planner
