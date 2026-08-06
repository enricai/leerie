"""Only one of the two re-plan call sites scopes by domain — on purpose.

| call site | gate | scoped? |
|---|---|---|
| `phase_overlap_judge` | cross-domain surface overlap | ✅ `domains=_replan_domain_closure(...)` |
| `phase_adherence_gate` | plan-instruction adherence | ❌ re-plans every domain |

The asymmetry is **structural, not an oversight**. The overlap judge's findings
are pairwise between subtasks, so each carries sids a closure can expand. The
adherence gate's findings are plan-global: its deterministic floor emits
`PRESCRIBED_CMD_UNRUN` naming a *command*, and `adherence_judge`'s `violations`
is an array of plain **strings** — there is no field that could carry a sid.

This file exists because the asymmetry reads like a bug. Without it, the next
reader "fixes" it by inventing an attribution the findings do not have —
bolting a `suggested_domain` onto the judge and guessing when it is absent,
which keeps the unscoped path alive anyway while adding a field the judge has
no grounds to fill.

Cost of the unscoped path is low and measured: repair-before-re-drive means it
fired in **1 run of 130**.
"""
from __future__ import annotations

import ast
import inspect
import textwrap


def _phase_plan_kwargs(fn) -> list[set[str]]:
    """Keyword names on every `phase_plan(...)` call inside `fn`.

    Walks the AST rather than grepping: the adherence gate's call site now
    carries a comment *explaining* the absent `domains=` argument, and a
    substring check reads that explanation as the argument itself. That is
    exactly the false positive this repo keeps paying for."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    return [{k.arg for k in n.keywords if k.arg}
            for n in ast.walk(tree)
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Name) and n.func.id == "phase_plan"]


class TestTheAsymmetryIsReal:
    def test_overlap_judge_scopes_its_replan(self, leerie):
        calls = _phase_plan_kwargs(leerie.phase_overlap_judge)
        assert calls, "structure moved — no phase_plan call found"
        assert any("domains" in kw for kw in calls), (
            "the overlap judge's re-plan must stay scoped — its findings "
            "carry sids, so the closure is derivable")

    def test_adherence_gate_does_not_scope_its_replan(self, leerie):
        calls = _phase_plan_kwargs(leerie.phase_adherence_gate)
        assert calls, "structure moved — no phase_plan call found"
        assert not any("domains" in kw for kw in calls), (
            "the adherence gate must NOT pass domains= — its findings carry "
            "no sid, so any scope would be invented")

    def test_the_probe_is_not_fooled_by_the_comment(self, leerie):
        """ANTI-VACUITY for the probe itself: the source DOES contain the
        literal text `domains=` (in the explanatory comment), so a substring
        check would report the opposite of the truth here."""
        assert "domains=" in inspect.getsource(leerie.phase_adherence_gate)


class TestTheReasonIsRecorded:
    """A bare absence is indistinguishable from a forgotten argument."""

    def test_the_call_site_explains_why(self, leerie):
        src = inspect.getsource(leerie.phase_adherence_gate)
        i = src.index("cur_plans[0] = await phase_plan(")
        window = src[max(0, i - 1400):i]
        assert "_replan_domain_closure" in window, (
            "the comment must name the mechanism it is declining to use, so "
            "the reader can see the choice was considered")
        assert "violations" in window, (
            "the comment must name the schema fact that makes scoping "
            "underivable")

    def test_the_comment_is_adjacent_to_the_call(self, leerie):
        """Recorded 1400 chars above the call, not in a distant docstring."""
        src = inspect.getsource(leerie.phase_adherence_gate)
        i = src.index("cur_plans[0] = await phase_plan(")
        assert "No `domains=` here" in src[max(0, i - 1400):i]


class TestTheSchemaFactBehindIt:
    """If this ever changes, the comment above becomes a lie and the closure
    genuinely should be wired. That is the trigger to revisit."""

    def test_violations_is_an_array_of_bare_strings(self, leerie):
        v = leerie.SCHEMAS["adherence_judge"]["properties"]["violations"]
        assert v["type"] == "array"
        assert v["items"] == {"type": "string"}, (
            "violations items gained structure — if one of those fields can "
            "carry a subtask id, wire _replan_domain_closure into "
            "phase_adherence_gate and delete this test")

    def test_no_suggested_domain_field_was_bolted_on(self, leerie):
        v = leerie.SCHEMAS["adherence_judge"]["properties"]["violations"]
        assert "properties" not in v.get("items", {}), (
            "the rejected patch was adding an attribution field to the judge; "
            "it is not the fix")


class TestAntiVacuity:
    """`domains=` must still DO something, or the asymmetry is meaningless."""

    def test_phase_plan_accepts_and_filters_on_domains(self, leerie):
        sig = inspect.signature(leerie.phase_plan)
        assert "domains" in sig.parameters
        src = inspect.getsource(leerie.phase_plan)
        assert "in domains" in src, (
            "phase_plan must actually filter categories on domains — if it "
            "does not, scoping the overlap judge's re-plan is also a no-op "
            "and this whole distinction is theatre")

    def test_replan_domain_closure_exists_and_is_used(self, leerie):
        assert callable(leerie._replan_domain_closure)
        assert "_replan_domain_closure(" in inspect.getsource(
            leerie.phase_overlap_judge)
