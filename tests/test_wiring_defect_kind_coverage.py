"""Every `wiring_judge` defect kind must have a handler, not just `die()`.

`SCHEMAS["wiring_judge"]` admits five `kind` values. Until 2026-08-14 the gate's
expand / dismiss / repair handlers each began `if kind != "missing_requires":
continue`, and `_filter_provably_false_wiring_defects`' provider-exists
predicate was scoped to `broken_by_*` — so `missing_provides` and
`orphaned_dependent` matched nothing anywhere and could reach only the `die()`.

Measured across the run corpus: **57 defects — 44 `missing_requires`, 6
`broken_by_drop`, 4 `broken_by_merge`, 3 `missing_provides`** — so **3 (5.3%)**
were in the die()-only class, the two kinds no predicate could dismiss. (An
earlier revision of this docstring said "13 (23%)", contradicting its own
sentence four lines above: 13 counts every kind outside `missing_requires`,
which is the repair channel's scope, not the dismissal channel's.) One of them killed run 3bc46e7d ($20.32, 71 workers, 38
minutes, no branch, no plan.json) on a finding whose named capability WAS
provided by an in-plan subtask. See docs/POSTMORTEM-2026-08-14.md, F4.

This file is the general guard: adding a sixth kind to the schema without giving
it an arm fails here rather than in production, on a run that has already paid
for a full planning phase.
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


# Every kind, and the predicate/repair surface that must acknowledge it.
# `orphaned_dependent` deliberately has no dismissal predicate — no set-membership
# fact refutes "this subtask depends on something that no longer exists" — so it
# gates, which is the conservative direction. It is listed here so that stays a
# decision rather than an oversight.
_GATES_WITHOUT_PREDICATE = {"orphaned_dependent"}


def _kinds(leerie) -> list[str]:
    props = (leerie.SCHEMAS["wiring_judge"]["properties"]["wiring_defects"]
             ["items"]["properties"])
    return list(props["kind"]["enum"])


def test_schema_still_declares_five_kinds(leerie):
    """Anti-vacuity: the sweep below is only meaningful against a known enum."""
    assert set(_kinds(leerie)) == {
        "missing_requires", "missing_provides",
        "broken_by_merge", "broken_by_drop", "orphaned_dependent",
    }


@pytest.mark.parametrize("kind", [
    "missing_requires", "missing_provides", "broken_by_merge", "broken_by_drop",
    "orphaned_dependent",
])
def test_kind_has_a_dismissal_predicate(leerie, kind):
    """Each kind, bar the documented exception, is named by a filter.

    The parametrization skips the exception by name. It is a literal list, so a
    sixth kind does NOT enter here automatically — `test_schema_still_declares_five_kinds`
    is what catches that, by set equality against the enum in both directions.
    An earlier version of this docstring claimed otherwise.
    """
    assert kind in _kinds(leerie), (
        f"{kind!r} is parametrized here but no longer in the schema enum")
    if kind in _GATES_WITHOUT_PREDICATE:
        pytest.skip(f"{kind} deliberately gates: no set-membership fact refutes it")
    src = inspect.getsource(leerie._filter_provably_false_wiring_defects)
    src += inspect.getsource(leerie._filter_defects_already_ordered)
    src += inspect.getsource(leerie._repair_missing_requires)
    # No `or kind.startswith("broken_by")` escape hatch. That disjunct was
    # vacuously true for `broken_by_merge` and `broken_by_drop`, so two of the
    # five parametrizations asserted nothing at all — deleting every
    # `broken_by_*` arm from the predicates left them green. Both kinds are in
    # fact named in the source, so the hatch bought nothing it was supposed to.
    assert kind in src, (
        f"{kind!r} is admitted by the schema but named by no dismissal "
        "predicate or repair channel, so it can only reach the die()"
    )


def test_missing_provides_is_dismissed_when_a_provider_exists(leerie):
    """The corpus case: the premise is false, so the run must not die.

    3bc46e7d died on exactly this shape.
    """
    plans = [{"domain": "feature-implementation", "subtasks": [
        {"id": "feat-006", "provides": ["audit-integration-config-diff-wired"]},
        {"id": "test-009", "provides": []},
    ]}]
    defects = [{
        "kind": "missing_provides",
        "sid": "test-009",
        "tag_or_dep": "audit-integration-config-diff-wired",
        "concrete_reason": "nothing declares this capability",
    }]
    kept, notes = leerie._filter_provably_false_wiring_defects(
        plans, defects, {})
    assert kept == [], (
        "a missing_provides whose capability IS provided in-plan must be "
        f"dismissed; it survived: {kept}")
    assert any("premise is false" in n for n in notes), notes


def test_missing_provides_survives_when_nothing_provides_it(leerie):
    """Anti-vacuity: the predicate must not dismiss the true finding."""
    plans = [{"domain": "feature-implementation", "subtasks": [
        {"id": "feat-006", "provides": ["something-else"]},
        {"id": "test-009", "provides": []},
    ]}]
    defects = [{
        "kind": "missing_provides",
        "sid": "test-009",
        "tag_or_dep": "audit-integration-config-diff-wired",
        "concrete_reason": "nothing declares this capability",
    }]
    kept, _ = leerie._filter_provably_false_wiring_defects(plans, defects, {})
    assert len(kept) == 1, (
        "a missing_provides naming a capability no subtask provides is a real "
        f"finding and must gate; got {kept}")


def test_missing_requires_is_never_dismissed_by_provider_existence(leerie):
    """The scoping that must not regress.

    A missing_requires naming a still-provided capability is the CANONICAL TRUE
    finding — the provider exists and the consumer failed to declare the edge.
    Extending the provider-exists predicate to it would silently disable the
    gate's main channel, where findings measure 99% true (69/70).
    """
    plans = [{"domain": "feature-implementation", "subtasks": [
        {"id": "feat-006", "provides": ["cap-a"]},
        {"id": "test-009", "provides": []},
    ]}]
    defects = [{
        "kind": "missing_requires",
        "sid": "test-009",
        "tag_or_dep": "cap-a",
        "concrete_reason": "test-009 can run before feat-006",
    }]
    kept, _ = leerie._filter_provably_false_wiring_defects(plans, defects, {})
    assert len(kept) == 1, (
        "missing_requires must survive provider existence — that IS the "
        f"finding; got {kept}")


def test_tag_or_dep_rejects_the_empty_string(leerie):
    """An empty tag silently disables every predicate keyed on it."""
    import jsonschema
    schema = leerie.SCHEMAS["wiring_judge"]
    inst = {
        "plan_reviewed": True,
        "wiring_defects": [{
            "kind": "missing_requires", "sid": "test-009",
            "tag_or_dep": "", "concrete_reason": "x",
        }],
    }
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(inst, schema)


def test_die_message_does_not_claim_causes_it_cannot_know(leerie):
    """The old text asserted three specific causes, none of which applied.

    Run 3bc46e7d's residual named a tag that WAS provided, by exactly one
    surviving subtask, with no cycle involved — so all three stated causes were
    false, and the message also told the operator to edit a plan.json that a
    planning-phase death never writes.
    """
    src = _code_only(inspect.getsource(leerie.phase_wiring_gate))
    assert "NEITHER a surviving subtask" not in src, (
        "the die() must not assert a cause it cannot verify")
    assert "plan_snapshot" in src, (
        "the die() must point at state.json's plan_snapshot — a run that dies "
        "in planning never writes plan.json")


class TestTagOrDepIsMatchedByEquality:
    """`tag_or_dep` is compared by equality, so it must be the bare token.

    Run 3bc46e7d's judge emitted the tag with a parenthetical appended. The
    bare tag WAS provided in-plan, so the provider-exists predicate would have
    dismissed the finding — but the commentary made every lookup miss and the
    run died having written no branch and no plan.json.
    """

    def _inst(self, value):
        return {"plan_reviewed": True, "rationale": "r", "wiring_defects": [{
            "kind": "missing_provides", "sid": "test-009",
            "tag_or_dep": value, "concrete_reason": "x"}]}

    def test_the_real_contaminated_value_is_rejected(self, leerie):
        import jsonschema
        bad = ("audit-integration-config-diff-wired (mismatched to feat-006's "
               "integrations/webhooks/[id] work)")
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(self._inst(bad), leerie.SCHEMAS["wiring_judge"])

    def test_the_bare_token_is_accepted(self, leerie):
        import jsonschema
        jsonschema.validate(
            self._inst("audit-integration-config-diff-wired"),
            leerie.SCHEMAS["wiring_judge"])

    @pytest.mark.parametrize("value", ["a, b", "a / b", "feat-006",
                                       "src/lib/x.ts"])
    def test_multi_value_and_path_forms_still_validate(self, leerie, value):
        """The pattern forbids parentheses ONLY, and that is deliberate.

        `_expand_multi_value_wiring_defects` splits this field on "," and " / "
        to handle a judge naming several values at once. A stricter
        "bare token, no spaces" pattern would reject those valid emissions —
        trading one silent-miss class for another.
        """
        import jsonschema
        jsonschema.validate(self._inst(value), leerie.SCHEMAS["wiring_judge"])


def test_the_3bc46e7d_shape_no_longer_kills_the_run(leerie):
    """End-to-end on the real finding: schema rejects, bare form dismisses.

    The two halves are both required. The predicate alone does not save the run
    (the contaminated tag matches no provider); the schema alone does not either
    (rejection only buys a re-prompt). Together, the re-prompted bare token hits
    the provider-exists predicate and the finding is dismissed.
    """
    import jsonschema
    plans = [{"domain": "feature-implementation", "subtasks": [
        {"id": "feat-006", "provides": ["audit-integration-config-diff-wired"]},
        {"id": "test-009", "provides": [], "requires": []},
    ]}]
    contaminated = ("audit-integration-config-diff-wired (mismatched to "
                    "feat-006's integrations/webhooks/[id] work)")

    # 1. the contaminated payload never reaches the gate
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"plan_reviewed": True, "rationale": "r", "wiring_defects": [{
                "kind": "missing_provides", "sid": "test-009",
                "tag_or_dep": contaminated, "concrete_reason": "x"}]},
            leerie.SCHEMAS["wiring_judge"])

    # 2. and had it reached the gate, it would have survived every predicate —
    #    which is precisely why the schema half is load-bearing.
    kept, _ = leerie._filter_provably_false_wiring_defects(
        plans, [{"kind": "missing_provides", "sid": "test-009",
                 "tag_or_dep": contaminated, "concrete_reason": "x"}], {})
    assert len(kept) == 1, (
        "the contaminated tag must still miss the predicate — if it does not, "
        "this test no longer demonstrates why the schema constraint is needed")

    # 3. the re-prompted bare token is dismissed, so the run lives
    kept, notes = leerie._filter_provably_false_wiring_defects(
        plans, [{"kind": "missing_provides", "sid": "test-009",
                 "tag_or_dep": "audit-integration-config-diff-wired",
                 "concrete_reason": "x"}], {})
    assert kept == [], f"the bare form must be dismissed; got {kept}"
    assert any("still provided by feat-006" in n for n in notes), notes
