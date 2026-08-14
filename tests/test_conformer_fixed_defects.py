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
import tokenize
import textwrap
import io
import ast

import pytest


def _code_only(src: str) -> str:
    """Source with comments AND docstrings removed.

    These scans forbid (or require) a token whose natural home is a comment
    documenting the rejected alternative — so a raw scan matches the prose
    describing the rule and fails on correct code, or matches prose instead of
    code and passes on broken code. CLAUDE.md records this trap repeatedly.
    `tokenize`, not a `#`-prefix heuristic: a `#` inside a string literal would
    corrupt the result.
    """
    out, last = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] > last[0]:
            out.append("\n" * (tok.start[0] - last[0]))
            last = (tok.start[0], 0)
        out.append(" " * max(0, tok.start[1] - last[1]) + tok.string)
        last = tok.end
    text = "".join(out)
    try:
        tree = ast.parse(textwrap.dedent(text))
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                text = text.replace(doc, "", 1)
    return text



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
    res = {"solution_defects": [_defect(fixed=True)]}
    assert leerie._actionable_solution_defects(res) == []


def test_a_residual_defect_still_gates(leerie):
    """Anti-vacuity: the gating axis must not be disabled wholesale."""
    res = {"solution_defects": [_defect(fixed=False)]}
    assert len(leerie._actionable_solution_defects(res)) == 1


def test_absent_status_still_gates(leerie):
    """Absence means residual, so an older conformer behaves as before."""
    res = {"solution_defects": [_defect()]}
    assert len(leerie._actionable_solution_defects(res)) == 1


def test_the_incident_shape(leerie):
    """Both entries at once: one repaired, one genuinely left."""
    res = {"solution_defects": [
        _defect(fixed=True),
        _defect(fixed=False, where="src/lib/other.ts"),
    ]}
    out = leerie._actionable_solution_defects(res)
    assert len(out) == 1 and out[0]["where"] == "src/lib/other.ts"


def test_a_fixed_defect_missing_evidence_is_still_dropped(leerie):
    """The anti-gaming guard is unchanged and independent."""
    res = {"solution_defects": [_defect(fixed=True, concrete_case="")]}
    assert leerie._actionable_solution_defects(res) == []


def test_schema_carries_the_fixed_flag(leerie):
    props = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
             ["items"]["properties"])
    assert props["fixed"]["type"] == "boolean"


def test_status_is_optional(leerie):
    """Requiring it risks the validity-rate collapse a required field caused
    on wiring_judge (9 of 66 invalid, all on one field)."""
    req = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
           ["items"]["required"])
    assert "fixed" not in req


def test_it_is_a_bool_because_the_schema_has_a_hard_size_bound(leerie):
    """Deliberately NOT a `status` enum mirroring rule_violations.

    This schema has a 2550-byte dumped bound, because the strict-output grammar
    compiler has actually rejected it when larger (DESIGN §7, N29). The enum
    encoding costs 59 bytes of field text and the bool 28 — +61 and +30 once
    `json.dumps`' `", "` separator is counted, which is what the bound actually
    measures. Only the bool fits. The
    consistency with rule_violations is worth less than worker output that
    validates at all.
    """
    import json
    props = (leerie.SCHEMAS["conformer"]["properties"]["solution_defects"]
             ["items"]["properties"])
    assert props["fixed"] == {"type": "boolean"}
    assert len(json.dumps(leerie.SCHEMAS["conformer"])) < 2550


def test_no_path_overlap_inference(leerie):
    """The rejected alternative must not creep back in.

    Inferring "fixed" from an overlap between `where` and `file_updates` fires
    on 2 of 152 corpus defects and is wrong on one of them: `ec7dae1c`'s
    conformer edited `messages/en.json` while deliberately leaving the
    `apiKeys` label unfixed, and the heuristic would have suppressed that
    genuine finding.
    """
    src = _code_only(inspect.getsource(leerie._actionable_solution_defects))
    assert "file_updates" not in src, (
        "gating must read the declared status, never infer it from which "
        "files the conformer happened to touch")


def test_the_prompt_asks_for_it(leerie):
    from pathlib import Path
    prompt = (Path(__file__).resolve().parent.parent
              / "prompts" / "conformer.md").read_text()
    assert "`fixed`" in prompt, (
        "the conformer must be told the field exists and what it costs to omit")
