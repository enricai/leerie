"""`_demote_unresolvable_with_external_twin` — DESIGN §5 *The external twin*.

Planners run blind, so two of them can classify the same capability
differently: one says `extent: external` (the work belongs to another run),
another says `in_plan`. The `in_plan` one has no provider by construction, so
it reaches `unresolvable` and kills the run — while the evidence that it is
out-of-graph sits unread in the collected externals.

Measured on run `1178f696` (2026-08-04): both fatal entries had a twin. One
exact (`invoice-payment-endpoint-fixed`, declared external by `feat-003` and
`in_plan` by `test-003`), one differing by a single character
(`webhook-event-types-schema-settled` vs `webhook-event-type-schema-settled`).

The load-bearing property is **placement**, and two tests pin it from opposite
sides: the rule must fire on the measured shape, and must NOT fire on a tag the
reconciler resolved (which never reaches `unresolvable` at all). An earlier
draft ran this check *before* the reconciler and was measured demoting 3 tags
the reconciler went on to resolve successfully — turning real ordering
constraints into mere deploy notes.
"""
from __future__ import annotations

import inspect

import pytest


# --- fixtures reproducing the measured run 1178f696 shape ------------------ #

def _external(tag: str, sid: str, reason: str) -> dict:
    """One entry in the shape `_collect_external_preconditions` emits."""
    return {"tag": tag, "reasons": [{"sid": sid, "reason": reason}],
            "originating_subtasks": [sid]}


def _plans_with(sid: str, tag: str, extent: str = "in_plan") -> list[dict]:
    return [{"domain": "testing", "subtasks": [{
        "id": sid, "title": "t", "success_criteria_seed": "s",
        "requires": [{"tag": tag, "extent": extent}],
        "provides": [], "depends_on": [],
    }]}]


INCIDENT_EXTERNALS = [
    _external("invoice-payment-endpoint-fixed", "feat-003",
              "Depends on a sibling phase file's A1 fix landing first."),
    _external("webhook-event-type-schema-settled", "feat-013",
              "Schema settling is owned by a sibling phase file's A5."),
]


class TestFiresOnTheMeasuredShape:
    def test_exact_twin_is_demoted(self, leerie):
        plans = _plans_with("test-003", "invoice-payment-endpoint-fixed")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "test-003",
                     "tag": "invoice-payment-endpoint-fixed"}],
            INCIDENT_EXTERNALS)
        assert len(out) == 1
        assert out[0]["match"] == "exact"
        entry = plans[0]["subtasks"][0]["requires"][0]
        assert entry["extent"] == "external"
        assert entry["reason"], "a demoted entry must carry a reason"

    def test_singularized_twin_is_demoted(self, leerie):
        """`types` vs `type` — one character, measured in the real run."""
        plans = _plans_with("test-013", "webhook-event-types-schema-settled")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "test-013",
                     "tag": "webhook-event-types-schema-settled"}],
            INCIDENT_EXTERNALS)
        assert len(out) == 1
        assert out[0]["match"] == "singularized"
        assert out[0]["twin_tag"] == "webhook-event-type-schema-settled"
        assert plans[0]["subtasks"][0]["requires"][0]["extent"] == "external"

    def test_inherited_reason_names_the_source_subtask(self, leerie):
        """A wrong normalized pairing must be traceable in plan.json, not
        silently reshape the graph."""
        plans = _plans_with("test-003", "invoice-payment-endpoint-fixed")
        leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "test-003",
                     "tag": "invoice-payment-endpoint-fixed"}],
            INCIDENT_EXTERNALS)
        reason = plans[0]["subtasks"][0]["requires"][0]["reason"]
        assert "feat-003" in reason
        assert "invoice-payment-endpoint-fixed" in reason


class TestDoesNotFireWithoutATwin:
    """Anti-vacuity: the abort must survive for genuine gaps. Four of the six
    measured fatal entries had no twin and must still die."""

    @pytest.mark.parametrize("tag", [
        "webhook-event-type-schema-unified",   # 20ae7e55/bugfix-014
        "invoice-pay-endpoint-fixed",          # 71a749fe/test-003
    ])
    def test_no_twin_is_not_demoted(self, leerie, tag):
        plans = _plans_with("bugfix-014", tag)
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "bugfix-014", "tag": tag}], INCIDENT_EXTERNALS)
        assert out == []
        assert plans[0]["subtasks"][0]["requires"][0]["extent"] == "in_plan"

    def test_no_externals_at_all_is_a_no_op(self, leerie):
        """Run 71a749fe declared zero externals — nothing to match against."""
        plans = _plans_with("feat-002", "invoice-payment-endpoint-fixed")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "feat-002",
                     "tag": "invoice-payment-endpoint-fixed"}], [])
        assert out == []
        assert plans[0]["subtasks"][0]["requires"][0]["extent"] == "in_plan"

    def test_empty_unresolvable_is_a_no_op(self, leerie):
        plans = _plans_with("test-003", "invoice-payment-endpoint-fixed")
        assert leerie._demote_unresolvable_with_external_twin(
            plans, [], INCIDENT_EXTERNALS) == []

    def test_entry_naming_an_absent_subtask_is_not_rescued(self, leerie):
        """A pruned sid must die rather than be silently 'rescued' with no
        corresponding rewrite."""
        plans = _plans_with("test-003", "invoice-payment-endpoint-fixed")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "gone-999",
                     "tag": "invoice-payment-endpoint-fixed"}],
            INCIDENT_EXTERNALS)
        assert out == []


class TestNormalizerIsDiscriminating:
    """The normalized pass must pair `types`/`type` and nothing looser. A
    false pairing demotes a real graph edge to a deploy note."""

    def test_pairs_only_a_plural_difference(self, leerie):
        assert (leerie._tag_key("webhook-event-types-schema-settled")
                == leerie._tag_key("webhook-event-type-schema-settled"))

    def test_does_not_pair_a_different_final_token(self, leerie):
        """`settled` vs `migrated` — both existed in the same real run."""
        assert (leerie._tag_key("webhook-event-type-schema-settled")
                != leerie._tag_key("webhook-event-type-schema-migrated"))

    def test_does_not_pair_a_missing_token(self, leerie):
        """`manual-` prefixed vs not — also both real, and NOT equivalent."""
        assert (leerie._tag_key("invoice-payment-endpoint-fixed")
                != leerie._tag_key("manual-invoice-payment-endpoint-fixed"))

    def test_short_tokens_keep_a_meaningful_trailing_s(self, leerie):
        assert leerie._tag_key("dns-record") == frozenset({"dns", "record"})

    def test_a_different_final_token_is_not_demoted_end_to_end(self, leerie):
        """Guard-the-guard: the normalizer's discrimination must actually
        reach the demotion decision."""
        plans = _plans_with("test-x", "webhook-event-type-schema-migrated")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "test-x",
                     "tag": "webhook-event-type-schema-migrated"}],
            [_external("webhook-event-type-schema-settled", "feat-013", "r")])
        assert out == []


class TestExactWinsOverNormalized:
    def test_exact_match_is_preferred(self, leerie):
        """Normalization must never perturb a case exact matching handles."""
        externals = [
            _external("api-keys-created", "feat-001", "exact owner"),
            _external("api-key-created", "feat-002", "normalized owner"),
        ]
        plans = _plans_with("test-1", "api-keys-created")
        out = leerie._demote_unresolvable_with_external_twin(
            plans, [{"sid": "test-1", "tag": "api-keys-created"}], externals)
        assert out[0]["match"] == "exact"
        assert out[0]["twin_tag"] == "api-keys-created"


class TestCallSiteWiring:
    """Source-coupling: the helper is inert unless wired, and the whole
    safety argument rests on WHERE it is wired."""

    def _src(self, leerie) -> str:
        return inspect.getsource(leerie.phase_reconcile)

    def test_called_from_phase_reconcile(self, leerie):
        assert "_demote_unresolvable_with_external_twin(" in self._src(leerie)

    def test_runs_after_prune_and_before_the_die(self, leerie):
        """Placement IS the safety property: after the reconciler's verdict
        (so it cannot preempt a resolution), before `_check_unresolvable`
        (so it can still prevent the die)."""
        src = self._src(leerie)
        prune = src.index("_prune_dead_subtasks(")
        demote = src.index("_demote_unresolvable_with_external_twin(")
        die = src.index("_check_unresolvable(output)")
        assert prune < demote < die

    def test_narrows_the_unresolvable_list(self, leerie):
        """A demotion that does not remove the entry leaves the die intact
        and the whole fix inert."""
        src = self._src(leerie)
        assert 'output["unresolvable"] = [' in src
        assert "_demoted_pairs" in src

    def test_refreshes_collected_preconditions(self, leerie):
        """The rescued entry must reach plan.json's `preconditions`, which
        `_write_plan` reads from state."""
        src = self._src(leerie)
        assert "_collect_external_preconditions(plans)" in src

    def test_state_key_is_declared(self, leerie):
        assert "external_twin_demotions" in leerie.STATE_FIELDS
