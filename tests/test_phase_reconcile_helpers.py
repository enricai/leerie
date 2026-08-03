"""Tests for the pure-Python helpers behind `phase_reconcile`:

- `_compute_unresolved_requires` — set lookup mirroring `_validate_plan`'s
  cross-domain check, but emitting data instead of raising.
- `_apply_reconciler_output` — mechanical mutation of the merged plan
  according to the reconciler worker's output.

The actual LLM-driven reconciler worker is exercised separately (and
end-to-end at PR-review time); these tests pin the deterministic Python
that wraps it.
"""
from __future__ import annotations

import pytest


# --- _compute_unresolved_requires --------------------------------------

def _plan(domain: str, *subtasks: dict) -> dict:
    """Build a planner-shaped plan dict from a list of subtask dicts."""
    return {"domain": domain, "status": "ready", "subtasks": list(subtasks)}


def _req(tag: str, extent: str = "in_plan", reason: str = "") -> dict:
    """Build a `requires` entry in the object form (DESIGN §5
    `requires.extent`). Defaults to `in_plan` since that's the common
    case in pre-extent tests and matches what bare strings represented."""
    entry = {"tag": tag, "extent": extent}
    if reason:
        entry["reason"] = reason
    return entry


def test_unresolved_empty_when_plan_has_no_requires(leerie):
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x", "provides": ["a"]})]
    assert leerie._compute_unresolved_requires(plans) == []


def test_unresolved_empty_when_every_requires_has_a_provider(leerie):
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["a"]}),
        _plan("testing",
              {"id": "test-001", "title": "y", "requires": [_req("a")]}),
    ]
    assert leerie._compute_unresolved_requires(plans) == []


def test_unresolved_lists_missing_tags(leerie):
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["a"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("a"), _req("missing-1")]}),
        _plan("testing",
              {"id": "test-002", "title": "z",
               "requires": [_req("missing-2")]}),
    ]
    out = leerie._compute_unresolved_requires(plans)
    # Order is by iteration order over plans → subtasks → requires;
    # we don't pin it tightly but the (sid, tag) pairs are stable.
    pairs = {(u["sid"], u["tag"]) for u in out}
    assert pairs == {("test-001", "missing-1"), ("test-002", "missing-2")}


def test_unresolved_handles_subtask_with_no_requires_field(leerie):
    """A subtask that omits `requires` entirely (default empty) doesn't
    crash the lookup."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x"})]
    assert leerie._compute_unresolved_requires(plans) == []


def test_unresolved_handles_subtask_with_no_provides_field(leerie):
    """A subtask that omits `provides` entirely doesn't contribute to
    `all_provides`. The lookup still works."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x"}),
        _plan("testing",
              {"id": "test-001", "title": "y", "requires": [_req("a")]}),
    ]
    out = leerie._compute_unresolved_requires(plans)
    assert out == [{"sid": "test-001", "tag": "a", "domain": "testing"}]


def test_unresolved_duplicate_requires_emits_once_per_subtask(leerie):
    """A subtask declaring the same `requires` tag twice should not
    crash; the duplicate is fine (the scheduler dedup is unaffected by
    our emit ordering)."""
    plans = [
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("missing-1"), _req("missing-1")]}),
    ]
    out = leerie._compute_unresolved_requires(plans)
    # Two entries — same sid + tag, both surfaced. The reconciler
    # consumes the list as a set internally; preserving duplicates here
    # is harmless and avoids hiding a planner bug.
    assert len(out) == 2


# --- _apply_reconciler_output ------------------------------------------

def test_apply_empty_output_is_noop(leerie):
    """An all-empty output leaves plans unchanged."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x",
                    "requires": [_req("foo")]})]
    out = {"renames": [], "added_provides": [],
           "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    # No mutations.
    assert plans[0]["subtasks"][0]["requires"] == [_req("foo")]
    assert len(plans) == 1


def test_apply_rename_rewrites_requires_on_named_subtask(leerie):
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["canonical"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("old-name"), _req("other-req")]}),
    ]
    out = {"renames": [{"sid": "test-001", "from": "old-name",
                        "to": "canonical"}],
           "added_provides": [], "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    # The `from` tag is rewritten in-place; `extent` and other reqs
    # untouched. The rename mutates the entry's `tag` field rather than
    # building a new list, so order is preserved (DESIGN §5).
    requires = plans[1]["subtasks"][0]["requires"]
    tags = [e["tag"] for e in requires]
    assert tags == ["canonical", "other-req"]
    # extent preserved across the rename.
    assert all(e["extent"] == "in_plan" for e in requires)


def test_apply_rename_with_nonexistent_sid_is_silently_skipped(leerie):
    """Defensive: if the reconciler emits a rename for a sid that
    doesn't exist, drop it rather than crash. (The reconciler is told
    only existing sids; this is belt-and-suspenders.)"""
    plans = [_plan("testing",
                   {"id": "test-001", "title": "y",
                    "requires": [_req("foo")]})]
    out = {"renames": [{"sid": "nonexistent-001", "from": "foo",
                        "to": "bar"}],
           "added_provides": [], "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    # test-001 was not the target; its requires is unchanged.
    assert plans[0]["subtasks"][0]["requires"] == [_req("foo")]


def test_apply_rename_does_not_mutate_external_with_same_tag(leerie):
    """Pathological-but-legal: a subtask carries two `requires` entries
    with the same `tag` — one `extent: in_plan` (which the reconciler
    saw and renamed), one `extent: external` (which the reconciler
    never sees because the orchestrator filters externals out of its
    input). The rename must touch *only* the in_plan entry; mutating
    the external entry's tag would corrupt the planner's out-of-graph
    declaration (the `reason` would no longer describe the new tag).
    Pins the DESIGN §5 invariant that the reconciler's verdicts only
    apply to in_plan tags."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["canonical"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("old-name", "in_plan"),
                            _req("old-name", "external",
                                 "owned by api-services CDK stack")]}),
    ]
    out = {"renames": [{"sid": "test-001", "from": "old-name",
                        "to": "canonical"}],
           "added_provides": [], "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    requires = plans[1]["subtasks"][0]["requires"]
    # The in_plan entry is renamed; the external entry is untouched.
    assert requires[0] == {"tag": "canonical", "extent": "in_plan"}
    assert requires[1]["tag"] == "old-name"
    assert requires[1]["extent"] == "external"
    assert requires[1]["reason"] == "owned by api-services CDK stack"


def test_apply_added_provides_appends_to_subtask(leerie):
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x", "provides": ["a"]})]
    out = {"renames": [],
           "added_provides": [{"sid": "feat-001", "tag": "b"}],
           "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    assert plans[0]["subtasks"][0]["provides"] == ["a", "b"]


def test_apply_added_provides_idempotent(leerie):
    """If the reconciler emits an already-present tag, don't duplicate it."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x", "provides": ["a"]})]
    out = {"renames": [],
           "added_provides": [{"sid": "feat-001", "tag": "a"}],
           "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    assert plans[0]["subtasks"][0]["provides"] == ["a"]


def test_apply_added_provides_to_subtask_with_no_provides_field(leerie):
    """Subtask missing `provides` entirely → field is added."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x"})]
    out = {"renames": [],
           "added_provides": [{"sid": "feat-001", "tag": "b"}],
           "added_subtasks": [], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    assert plans[0]["subtasks"][0]["provides"] == ["b"]


def test_apply_added_subtasks_appends_reconciler_plan(leerie):
    """Added subtasks land in a new pseudo-plan with domain="_reconciler".
    The scheduler flattens by id, so the domain only affects logs."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x"})]
    new_subtask = {
        "id": "feat-008",
        "title": "Added connector",
        "success_criteria_seed": "criterion",
        "provides": ["new-cap"],
        "_added_by_reconciler": True,
    }
    out = {"renames": [], "added_provides": [],
           "added_subtasks": [new_subtask],
           "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    assert len(plans) == 2
    assert plans[1]["domain"] == "_reconciler"
    assert plans[1]["subtasks"] == [new_subtask]


def test_apply_dies_on_duplicate_added_subtask_id(leerie):
    """If the reconciler emits an added_subtask whose `id` collides with
    an existing subtask, `_apply_reconciler_output` must die() — not
    silently append. The scheduler later merges all subtasks into a
    single dict keyed by id; a collision would silently drop one of
    them, losing its requires/provides/depends_on from the DAG. This
    is exactly the kind of mechanical guarantee CLAUDE.md says must
    live in the code, not the prompt.
    """
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "shim",
                    "provides": ["shim-cap"]})]
    out = {
        "renames": [], "added_provides": [],
        "added_subtasks": [{
            "id": "feat-001",  # collides with the existing subtask
            "title": "Conflicting reconciler subtask",
            "success_criteria_seed": "x",
            "provides": ["new-cap"],
            "_added_by_reconciler": True,
        }],
        "unresolvable": [],
    }
    with pytest.raises(SystemExit) as exc:
        leerie._apply_reconciler_output(plans, out)
    assert exc.value.code != 0
    # The original subtask is still there — the helper must NOT have
    # mutated plans before dying. (die() runs at the top of the
    # added_subtasks branch, before the append.)
    assert len(plans) == 1
    assert plans[0]["subtasks"][0]["id"] == "feat-001"
    assert plans[0]["subtasks"][0]["title"] == "shim"


def test_apply_dies_names_colliding_ids_in_error(leerie, capsys):
    """The die() message must name the colliding id(s) so a user reading
    the error can map straight back to the offending plan. Pin the
    surface form so a future refactor can't degrade it to a generic
    'collision detected' message.
    """
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x"},
              {"id": "feat-002", "title": "y"}),
    ]
    out = {
        "renames": [], "added_provides": [],
        "added_subtasks": [
            {"id": "feat-002", "title": "collision-1",
             "success_criteria_seed": "x", "_added_by_reconciler": True},
            {"id": "feat-009", "title": "ok",
             "success_criteria_seed": "y", "_added_by_reconciler": True},
        ],
        "unresolvable": [],
    }
    with pytest.raises(SystemExit):
        leerie._apply_reconciler_output(plans, out)
    err = capsys.readouterr().err
    # The colliding id is named; the non-colliding one is not.
    assert "feat-002" in err
    assert "feat-009" not in err


def test_apply_dies_on_duplicate_added_subtask_self_collision(leerie):
    """The reconciler emitted two added_subtasks with the same id —
    neither colliding with an existing subtask, but colliding with each
    other. _schedule()'s dict-flatten would silently drop one; this
    must die() with the same fail-loud guarantee as the
    existing-vs-added case. Pin the behavior so a future refactor of
    the collision check (e.g., to a single-pass form) can't accidentally
    drop the self-collision arm.
    """
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "existing"})]
    out = {
        "renames": [], "added_provides": [],
        "added_subtasks": [
            {"id": "feat-009", "title": "first",
             "success_criteria_seed": "a", "_added_by_reconciler": True},
            {"id": "feat-009", "title": "second",  # same id as the first
             "success_criteria_seed": "b", "_added_by_reconciler": True},
        ],
        "unresolvable": [],
    }
    with pytest.raises(SystemExit) as exc:
        leerie._apply_reconciler_output(plans, out)
    assert exc.value.code != 0
    # Plans unmutated — the helper dies before appending the
    # _reconciler pseudo-plan, so the existing plan is still alone.
    assert len(plans) == 1
    assert plans[0]["subtasks"][0]["id"] == "feat-001"


def test_apply_dies_names_self_colliding_ids_in_error(leerie, capsys):
    """The die() message must use the 'duplicated within added_subtasks'
    surface form (not the 'collide with existing subtasks' form) so a
    user reading the error can tell self-collision apart from
    existing-collision and trace it back to the right reconciler-output
    array.
    """
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "existing"})]
    out = {
        "renames": [], "added_provides": [],
        "added_subtasks": [
            {"id": "feat-009", "title": "first",
             "success_criteria_seed": "a", "_added_by_reconciler": True},
            {"id": "feat-009", "title": "second",
             "success_criteria_seed": "b", "_added_by_reconciler": True},
            {"id": "feat-010", "title": "ok",
             "success_criteria_seed": "c", "_added_by_reconciler": True},
        ],
        "unresolvable": [],
    }
    with pytest.raises(SystemExit):
        leerie._apply_reconciler_output(plans, out)
    err = capsys.readouterr().err
    # Self-collision surface form named; the non-colliding id is not.
    assert "duplicated within added_subtasks" in err
    assert "feat-009" in err
    assert "feat-010" not in err
    # And the self-collision case must NOT be misreported as an
    # existing-vs-added collision (those use a different surface form).
    assert "collide with existing subtasks" not in err


def test_apply_combined_renames_provides_and_subtasks(leerie):
    """Realistic case: all three mutation types applied in one call."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "shim", "provides": ["shim-cap"]}),
        _plan("testing",
              {"id": "test-001", "title": "test",
               "requires": [_req("wrong-name"), _req("new-cap")]}),
    ]
    out = {
        "renames": [{"sid": "test-001", "from": "wrong-name",
                     "to": "shim-cap"}],
        "added_provides": [{"sid": "feat-001", "tag": "extra-cap"}],
        "added_subtasks": [{
            "id": "feat-009",
            "title": "New cap producer",
            "success_criteria_seed": "x",
            "provides": ["new-cap"],
            "_added_by_reconciler": True,
        }],
        "unresolvable": [],
    }
    leerie._apply_reconciler_output(plans, out)
    # rename applied — check via tag set since entries are objects now.
    tags = [e["tag"] for e in plans[1]["subtasks"][0]["requires"]]
    assert "wrong-name" not in tags
    assert "shim-cap" in tags
    # added_provides applied
    assert "extra-cap" in plans[0]["subtasks"][0]["provides"]
    # added_subtasks landed
    assert len(plans) == 3
    assert plans[2]["subtasks"][0]["id"] == "feat-009"


def test_apply_does_not_consume_unresolvable_array(leerie):
    """`unresolvable` is the orchestrator's responsibility (die() before
    calling _apply). `_apply_reconciler_output` ignores it — pin so the
    helper doesn't accidentally swallow unresolvable as a non-failure
    mutation."""
    plans = [_plan("testing",
                   {"id": "test-001", "title": "y",
                    "requires": [_req("x")]})]
    out = {"renames": [], "added_provides": [], "added_subtasks": [],
           "unresolvable": [{"sid": "test-001", "tag": "x",
                             "reason": "fake reason"}]}
    leerie._apply_reconciler_output(plans, out)
    # Plans unchanged — unresolvable is not the helper's concern.
    assert plans[0]["subtasks"][0]["requires"] == [_req("x")]
    assert len(plans) == 1


# --- _promote_external_collisions (DESIGN §5 collision rule) -----------

def test_promote_external_with_no_provider_left_alone(leerie):
    """The whole point of `extent: external` is "no in-plan producer";
    the promotion pass must leave such entries as-is."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["something-else"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("external-cap", "external",
                                 "owned by other repo")]}),
    ]
    promoted = leerie._promote_external_collisions(plans)
    assert promoted == 0
    assert plans[1]["subtasks"][0]["requires"][0]["extent"] == "external"


def test_promote_external_with_provider_demoted_to_in_plan(leerie):
    """If any plan provides the tag, the external declaration must be
    silently promoted to in_plan — the in-plan producer wins so a
    planner cannot unilaterally bypass a real producer that happens to
    exist in another domain (DESIGN §5 collision rule)."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "provides": ["redis-available"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("redis-available", "external",
                                 "the planner thought infra owned this")]}),
    ]
    promoted = leerie._promote_external_collisions(plans)
    assert promoted == 1
    entry = plans[1]["subtasks"][0]["requires"][0]
    assert entry["extent"] == "in_plan"
    # `reason` is preserved for telemetry — promotion does not strip it.
    assert entry["reason"] == "the planner thought infra owned this"


def test_promote_external_counts_each_promotion_separately(leerie):
    """Multiple external entries that collide are each counted."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "provides": ["cap-a", "cap-b"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("cap-a", "external", "r1"),
                            _req("cap-b", "external", "r2"),
                            _req("cap-c", "external", "r3")]}),
    ]
    promoted = leerie._promote_external_collisions(plans)
    # cap-a and cap-b collide; cap-c does not.
    assert promoted == 2
    extents = [e["extent"] for e in plans[1]["subtasks"][0]["requires"]]
    assert extents == ["in_plan", "in_plan", "external"]


def test_promote_external_ignores_in_plan_entries(leerie):
    """`_promote_external_collisions` only touches external entries;
    in_plan entries that look "wrong" are someone else's problem
    (_validate_plan or the reconciler)."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["cap-a"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("cap-a", "in_plan")]}),
    ]
    promoted = leerie._promote_external_collisions(plans)
    assert promoted == 0


# --- _collect_external_preconditions -----------------------------------

def test_collect_externals_emits_one_entry_per_tag(leerie):
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "requires": [_req("ext-1", "external", "reason-a")]}),
    ]
    out = leerie._collect_external_preconditions(plans)
    assert len(out) == 1
    assert out[0]["tag"] == "ext-1"
    assert out[0]["originating_subtasks"] == ["feat-001"]
    assert out[0]["reasons"] == [{"sid": "feat-001", "reason": "reason-a"}]


def test_collect_externals_dedupes_by_tag(leerie):
    """Two subtasks declaring the same external tag merge into one
    preconditions entry; each subtask's reason is preserved in the
    `reasons` array so attribution survives the dedup."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "requires": [_req("dynamo-table", "external", "feat says")]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("dynamo-table", "external", "test says")]}),
    ]
    out = leerie._collect_external_preconditions(plans)
    assert len(out) == 1
    assert out[0]["tag"] == "dynamo-table"
    assert out[0]["originating_subtasks"] == ["feat-001", "test-001"]
    reasons = {r["sid"]: r["reason"] for r in out[0]["reasons"]}
    assert reasons == {"feat-001": "feat says", "test-001": "test says"}


def test_collect_externals_skips_in_plan_entries(leerie):
    """Only `extent: external` entries land in preconditions; in_plan
    entries are graph edges and stay in the reconciler's domain."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x", "provides": ["cap-a"]}),
        _plan("testing",
              {"id": "test-001", "title": "y",
               "requires": [_req("cap-a", "in_plan"),
                            _req("ext-cap", "external", "reason")]}),
    ]
    out = leerie._collect_external_preconditions(plans)
    assert len(out) == 1
    assert out[0]["tag"] == "ext-cap"


def test_collect_externals_is_deterministically_ordered(leerie):
    """Output is sorted by tag so test assertions and the on-disk
    plan.json don't churn run-to-run for the same input."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "requires": [_req("zeta", "external", "z"),
                            _req("alpha", "external", "a"),
                            _req("mu", "external", "m")]}),
    ]
    out = leerie._collect_external_preconditions(plans)
    tags = [e["tag"] for e in out]
    assert tags == ["alpha", "mu", "zeta"]


def test_collect_externals_empty_for_plan_with_no_externals(leerie):
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x",
                    "requires": [_req("cap-a", "in_plan")]})]
    assert leerie._collect_external_preconditions(plans) == []


# --- _compute_unresolved_requires + extent filtering -------------------

def test_compute_unresolved_ignores_external_entries(leerie):
    """`extent: external` entries are out-of-graph by planner
    declaration; they must NOT appear in the unresolved set even when
    no subtask provides them (that's the whole point of the channel)."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "requires": [_req("ext-cap", "external", "external owner")]}),
    ]
    assert leerie._compute_unresolved_requires(plans) == []


# --- reconciler-added externals round-trip (DESIGN §5, P2.3 fix) -------

def test_collect_externals_includes_reconciler_added_subtasks(leerie):
    """Pin the P2.3 invariant: after `_apply_reconciler_output` appends a
    connector subtask that itself declares `extent: external`, a
    subsequent `_collect_external_preconditions` pass must include that
    new external entry in the deduped preconditions list. Without the
    re-run wired in `phase_reconcile`, the reconciler-added external
    would be silently dropped (collected only against the original
    planner output, not the post-reconciler plan tree)."""
    plans = [_plan("feature-implementation",
                   {"id": "feat-001", "title": "x"})]
    new_subtask = {
        "id": "feat-008",
        "title": "Connector for cross-system handoff",
        "success_criteria_seed": "criterion",
        "provides": ["new-cap"],
        "requires": [_req("manual-pagerduty-flag-on", "external",
                          "SRE must enable the feature flag in PagerDuty "
                          "before deploy")],
        "_added_by_reconciler": True,
    }
    out = {"renames": [], "added_provides": [],
           "added_subtasks": [new_subtask], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    preconditions = leerie._collect_external_preconditions(plans)
    assert len(preconditions) == 1
    assert preconditions[0]["tag"] == "manual-pagerduty-flag-on"
    assert preconditions[0]["originating_subtasks"] == ["feat-008"]
    assert preconditions[0]["reasons"][0]["sid"] == "feat-008"


def test_promote_externals_after_reconciler_handles_in_plan_producer(leerie):
    """Sibling to the above: if a reconciler-added subtask declares an
    external `requires` whose tag is `provides`d by some plan (either
    an original planner or another reconciler-added subtask), the
    second-pass `_promote_external_collisions` must promote it to
    in_plan so the graph edge gets wired. Same DESIGN §5 collision rule
    that applies to planner-declared externals."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "x",
               "provides": ["cap-a"]}),
    ]
    new_subtask = {
        "id": "feat-008",
        "title": "Connector",
        "success_criteria_seed": "criterion",
        "provides": ["new-cap"],
        "requires": [_req("cap-a", "external",
                          "reconciler thought infra owned this")],
        "_added_by_reconciler": True,
    }
    out = {"renames": [], "added_provides": [],
           "added_subtasks": [new_subtask], "unresolvable": []}
    leerie._apply_reconciler_output(plans, out)
    promoted = leerie._promote_external_collisions(plans)
    assert promoted == 1
    # The promoted entry is now in_plan; the reason is preserved for
    # telemetry but no longer load-bearing.
    promoted_entry = plans[-1]["subtasks"][0]["requires"][0]
    assert promoted_entry["extent"] == "in_plan"
    assert promoted_entry["tag"] == "cap-a"


def test_added_provides_absorbs_planner_external_on_second_pass(leerie):
    """Demotion direction: a planner declared `cap-X` as `extent:
    external` (so the first-pass collection captured it as a
    precondition). The reconciler then emits `added_provides` claiming
    some existing subtask actually produces `cap-X`. After
    `_apply_reconciler_output`, the second-pass
    `_promote_external_collisions` must demote the external entry to
    in_plan because a provider now exists in some plan's `provides`,
    and the second-pass `_collect_external_preconditions` must therefore
    return a *shorter* list than the first pass.

    Pins the correctness of the bracket pattern in the count-shrinks
    direction (the case that motivated the P3.2 neutral-wording log
    fix at leerie.py:7140-7143). Without this, a future refactor could
    accidentally regress to a one-way `if promoted_after:` guard that
    silently hides the demotion."""
    plans = [
        _plan("feature-implementation",
              {"id": "feat-001", "title": "shim",
               # Does not yet declare `provides: cap-X` — that comes
               # via the reconciler's added_provides below.
               "provides": []}),
        _plan("testing",
              {"id": "test-001", "title": "tests",
               "requires": [_req("cap-X", "external",
                                 "planner thought infra owned this")]}),
    ]
    # First-pass collection: 1 external precondition.
    preconditions_before = leerie._collect_external_preconditions(plans)
    assert len(preconditions_before) == 1
    assert preconditions_before[0]["tag"] == "cap-X"

    # Reconciler discovers that feat-001 actually produces cap-X.
    out = {
        "renames": [],
        "added_provides": [{"sid": "feat-001", "tag": "cap-X"}],
        "added_subtasks": [],
        "unresolvable": [],
    }
    leerie._apply_reconciler_output(plans, out)

    # Second-pass promotion finds cap-X in feat-001's `provides` and
    # demotes the testing subtask's external entry to in_plan.
    promoted = leerie._promote_external_collisions(plans)
    assert promoted == 1

    # Second-pass collection now returns 0 externals — the count
    # SHRANK from 1 to 0. This is the demotion direction the P3.2
    # log fix neutrally describes ("count changed from N to M").
    preconditions_after = leerie._collect_external_preconditions(plans)
    assert len(preconditions_after) == 0
    assert len(preconditions_after) < len(preconditions_before)

    # The previously-external entry is now in_plan; reason preserved
    # for telemetry but no longer load-bearing.
    entry = plans[1]["subtasks"][0]["requires"][0]
    assert entry["extent"] == "in_plan"
    assert entry["tag"] == "cap-X"
