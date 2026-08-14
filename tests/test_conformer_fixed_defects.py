"""A defect the conformer FIXED must not block the run.

`solution_defects` is the one gating conformance axis, and it carried two
different reports on one channel: "here is a gap I found" and "here is a gap I
found and repaired". The gate read both as outstanding.

Measured: `1b9b52f5`'s bugfix-004 conformer found `sibling_site_unedited`, fixed
it, and committed "conformer: wire InvoiceCheckPaymentPanel into the admin
console" — listing the created page in its own `file_updates` — and was then
blocked for the defect it had just repaired. Its branch was never integrated, so
the run shipped `mark-paid-check/route.ts` and `invoice-check-payment.ts` with no
page and no panel: backend without UI, precisely the state the gate exists to
prevent.

`status: "fixed"` mirrors `rule_violations.status`, which has carried this
distinction in the same schema all along.

See docs/POSTMORTEM-2026-08-14.md, F11 and retraction R4.
"""
from __future__ import annotations

import inspect

import pytest


def _defect(**over):
    base = {
        "kind": "sibling_site_unedited",
        "concrete_case": ("An admin navigates to /admin and finds no route to "
                          "the invoice panel"),
        "where": "src/app/[locale]/admin/page.tsx (NAV_GROUPS)",
        "why_ships_a_defect": "the panel is unreachable",
    }
    return {**base, **over}


def test_a_fixed_defect_is_not_actionable(leerie):
    res = {"solution_defects": [_defect(status="fixed")]}
    assert leerie._actionable_solution_defects(res) == []


def test_a_residual_defect_still_gates(leerie):
    """Anti-vacuity: the gating axis must not be disabled wholesale."""
    res = {"solution_defects": [_defect(status="residual")]}
    assert len(leerie._actionable_solution_defects(res)) == 1


def test_absent_status_still_gates(leerie):
    """Absence means residual, so an older conformer behaves as before."""
    res = {"solution_defects": [_defect()]}
    assert len(leerie._actionable_solution_defects(res)) == 1


def test_the_incident_shape(leerie):
    """Both entries at once: one repaired, one genuinely left."""
    res = {"solution_defects": [
        _defect(status="fixed"),
        _defect(status="residual", where="src/lib/other.ts"),
    ]}
    out = leerie._actionable_solution_defects(res)
    assert len(out) == 1 and out[0]["where"] == "src/lib/other.ts"


def test_a_fixed_defect_missing_evidence_is_still_dropped(leerie):
    """The anti-gaming guard is unchanged and independent."""
    res = {"solution_defects": [_defect(status="fixed", concrete_case="")]}
    assert leerie._actionable_solution_defects(res) == []


def test_schema_carries_the_status_enum(leerie):
    props = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
             ["items"]["properties"])
    assert props["status"]["enum"] == ["fixed", "residual"]


def test_status_is_optional(leerie):
    """Requiring it risks the validity-rate collapse a required field caused
    on wiring_judge (9 of 66 invalid, all on one field)."""
    req = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
           ["items"]["required"])
    assert "status" not in req


def test_it_mirrors_rule_violations(leerie):
    """The precedent it follows, in the same schema."""
    rv = (leerie.SCHEMAS["conformer"]["properties"]["rule_violations"]
          ["items"]["properties"]["status"]["enum"])
    sd = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
          ["items"]["properties"]["status"]["enum"])
    assert rv == sd


def test_no_path_overlap_inference(leerie):
    """The rejected alternative must not creep back in.

    Inferring "fixed" from an overlap between `where` and `file_updates` fires
    on 2 of 152 corpus defects and is wrong on one of them: `ec7dae1c`'s
    conformer edited `messages/en.json` while deliberately leaving the
    `apiKeys` label unfixed, and the heuristic would have suppressed that
    genuine finding.
    """
    src = inspect.getsource(leerie._actionable_solution_defects)
    assert "file_updates" not in src, (
        "gating must read the declared status, never infer it from which "
        "files the conformer happened to touch")


def test_the_prompt_asks_for_it(leerie):
    from pathlib import Path
    prompt = (Path(__file__).resolve().parent.parent
              / "prompts" / "conformer.md").read_text()
    assert "`status`" in prompt and '"fixed"' in prompt, (
        "the conformer must be told the field exists and what it costs to omit")
