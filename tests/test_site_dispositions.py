"""Per-site dispositions (DESIGN §5 *Every site carries a disposition*).

The dominant cause of serial narrow fixing (5 of 11 historical repeat pairs)
was the audit: the later-fixed site was usually LISTED, as a "consumer" left
to mechanical consequence. Each site now carries a required `disposition`
(evidence in `note`); the planner must cover every `fix` site, and the coverage
warning compares only those.
"""
from __future__ import annotations

import asyncio

from tests.test_defect_scope_audit import (AUDIT, EFFORTS, MODELS, _caps,
                                           _patch_auditor, _state)


def test_schema_requires_disposition_and_evidence(leerie):
    sch = leerie.SCHEMAS["defect_scope_auditor"]
    site = sch["properties"]["sites"]["items"]
    assert sorted(site["required"]) == [
        "disposition", "file", "role", "symbol"]
    # No separate free-form evidence field (the strict grammar's
    # expensive element); the evidence lives in `note`.
    assert "evidence" not in site["properties"]
    assert site["properties"]["disposition"]["enum"] == ["fix", "ruled_out"]
    # No top-level already_resolved_on_tree: under strict decoding the
    # variants carrying it emptied `sites` in 5 of 18 calls, against 0 of
    # 6 with dispositions alone and 0 of 10 on the prior schema.
    assert sorted(sch["required"]) == ["applicable", "sites"]
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


def _plans_touching(*files):
    return [{"domain": "bug-fixing", "subtasks": [
        {"id": "bugfix-001", "files_likely_touched": list(files)}]}]


def test_warning_names_uncovered_fix_sites_only(leerie, capsys):
    scope = {"applicable": True, "sites": _disposed_audit()["sites"]}
    leerie._warn_defect_sites_uncovered(_plans_touching("src/unrelated.py"),
                                        scope)
    out = capsys.readouterr().out
    assert "merge_candidates" in out
    assert "render_rows" not in out


def test_all_fix_sites_covered_means_no_warning(leerie, capsys):
    scope = {"applicable": True, "sites": _disposed_audit()["sites"]}
    leerie._warn_defect_sites_uncovered(
        _plans_touching("src/example_module.py"), scope)
    assert "WARNING" not in capsys.readouterr().out


def test_legacy_site_without_disposition_still_counts(leerie, capsys):
    """A recorded scope from before dispositions existed keeps warning."""
    scope = {"applicable": True, "sites": [
        {"file": "src/legacy.py", "symbol": "old_site", "role": "bypass"}]}
    leerie._warn_defect_sites_uncovered(_plans_touching("src/x.py"), scope)
    assert "old_site" in capsys.readouterr().out


def test_prompts_carry_the_rules(leerie):
    auditor = leerie._load_prompt("defect_scope_auditor")
    assert "is not a ruling-out by" in auditor
    planner = leerie._load_prompt("planner")
    assert "disposed `fix` must be covered" in planner
