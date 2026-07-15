"""Tests for `phase_overlap_judge` and its helpers — the phase 2¾
plan-overlap judge (DESIGN §5 *Cross-domain surface overlap*).

Covers the SCHEMAS["plan_overlap_judge"] contract, the
_validate_overlap_judge_output merge-feasibility backstop, and the two
pure apply functions `_apply_overlap_merge` and `_apply_overlap_drop`.
The async phase_overlap_judge wrapper itself is exercised indirectly
through its short-circuit conditions; the load-bearing logic lives in
the helpers and is easier to test directly.

Mirrors the test style of test_reconciler_schema.py +
test_apply_reconciler_output_*.py: extract the schema / call the pure
helper / reason over the result. The schema tests use a HAS_JSONSCHEMA gate
with a manual structural fallback (as test_dep_capture_schema.py /
test_fit_judge_schema.py do) so CI without jsonschema installed still
catches drift — it is not a declared dependency.
"""
from __future__ import annotations

import asyncio
import copy

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


# --------------------------------------------------------------------- #
# Schema tests
# --------------------------------------------------------------------- #

def _stub_confidence() -> dict:
    return {"judgment": 9.0, "basis": "", "falsifiers_tested": [],
            "contradictions_reconciled": [], "gap_to_close": {}}


def _valid_collision_merge() -> dict:
    return {
        "a_sid": "feat-001",
        "b_sid": "refactor-001",
        "artifact": "EmptyState primitive (src/components/features/empty-state.tsx)",
        "resolution": "merge",
        "reason": "Both create EmptyState in the same file with shared "
                  "provides tag empty-state-primitive.",
        "merge_feasibility": "Both intents can be satisfied by a single "
                             "EmptyState with optional icon/title/description/"
                             "CTA props.",
    }


def _valid_collision_drop_a() -> dict:
    return {
        "a_sid": "feat-008",
        "b_sid": "refactor-001",
        "artifact": "AuthShell component",
        "resolution": "drop_a",
        "reason": "feat-008 is superseded by the refactor-001 + refactor-002 "
                  "extract+adopt pair, which split the same scope cleanly.",
    }


def _valid_collision_unresolvable() -> dict:
    return {
        "a_sid": "feat-008",
        "b_sid": "refactor-001",
        "artifact": "AuthShell component",
        "resolution": "unresolvable",
        "reason": "feat-008's required {children}-only contract and "
                  "refactor-001's required `description` prop are "
                  "structurally incompatible.",
    }


def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests must
    pass in both modes so CI without jsonschema installed still catches
    drift."""
    schema = leerie.SCHEMAS["plan_overlap_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["collisions"], list)
    item = schema["properties"]["collisions"]["items"]
    allowed = set(item["properties"])
    for c in instance["collisions"]:
        assert isinstance(c, dict)
        for k in item["required"]:
            assert k in c, f"collision missing required field {k!r}"
        assert not set(c) - allowed, f"collision has unknown keys: {set(c) - allowed}"
        assert c["resolution"] in item["properties"]["resolution"]["enum"]
    conf_schema = schema["properties"]["confidence"]
    conf = instance["confidence"]
    assert isinstance(conf, dict)
    for k in conf_schema["required"]:
        assert k in conf, f"confidence missing required field {k!r}"


def test_schema_exists(leerie):
    assert "plan_overlap_judge" in leerie.SCHEMAS


def test_schema_empty_collisions_valid(leerie):
    """The no-collision case is the common one — the judge must be able
    to return {collisions: []}."""
    _validate(leerie, {"collisions": [], "confidence": _stub_confidence()})


def test_schema_full_payload_valid(leerie):
    _validate(leerie, {"collisions": [
        _valid_collision_merge(),
        _valid_collision_drop_a(),
        _valid_collision_unresolvable(),
    ], "confidence": _stub_confidence()})


def test_schema_rejects_unknown_resolution(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; enum check requires it")
    bad = _valid_collision_drop_a()
    bad["resolution"] = "merge_or_split"
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"collisions": [bad],
                            "confidence": _stub_confidence()},
                            leerie.SCHEMAS["plan_overlap_judge"])


def test_schema_rejects_extra_top_level_keys(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; additionalProperties requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"collisions": [], "confidence": _stub_confidence(),
             "extra": "nope"},
            leerie.SCHEMAS["plan_overlap_judge"])


def test_schema_requires_core_fields(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    incomplete = {"a_sid": "feat-001", "b_sid": "refactor-001",
                  "resolution": "merge"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"collisions": [incomplete],
                            "confidence": _stub_confidence()},
                            leerie.SCHEMAS["plan_overlap_judge"])


# --------------------------------------------------------------------- #
# _validate_overlap_judge_output (the merge-feasibility backstop)
# --------------------------------------------------------------------- #

def _by_id(subtasks):
    return {s["id"]: s for s in subtasks}


def test_validator_passes_on_drop_without_merge_feasibility(leerie):
    """drop_a/drop_b/unresolvable don't need merge_feasibility — only
    `merge` requires the discipline."""
    by_id = _by_id([
        {"id": "feat-008"}, {"id": "refactor-001"},
    ])
    leerie._validate_overlap_judge_output(
        {"collisions": [_valid_collision_drop_a()]}, by_id)


def test_validator_passes_on_unresolvable_without_merge_feasibility(leerie):
    by_id = _by_id([
        {"id": "feat-008"}, {"id": "refactor-001"},
    ])
    leerie._validate_overlap_judge_output(
        {"collisions": [_valid_collision_unresolvable()]}, by_id)


def test_validator_passes_on_merge_with_feasibility(leerie):
    by_id = _by_id([
        {"id": "feat-001"}, {"id": "refactor-001"},
    ])
    leerie._validate_overlap_judge_output(
        {"collisions": [_valid_collision_merge()]}, by_id)


def test_validator_dies_on_merge_without_feasibility(leerie, capsys):
    """The load-bearing case: a `merge` without a concrete
    merge_feasibility statement must die — auto-merging without the
    unified-intent would produce a frankenstein implementer spec."""
    bad = _valid_collision_merge()
    del bad["merge_feasibility"]
    by_id = _by_id([
        {"id": "feat-001"}, {"id": "refactor-001"},
    ])
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(
            {"collisions": [bad]}, by_id)
    err = capsys.readouterr().err
    assert "merge_feasibility" in err
    assert "feat-001" in err
    assert "refactor-001" in err


def test_validator_dies_on_merge_with_blank_feasibility(leerie, capsys):
    """Empty / whitespace-only merge_feasibility is also rejected —
    the model could satisfy the schema by emitting ' ' but the
    discipline requires a concrete statement."""
    bad = _valid_collision_merge()
    bad["merge_feasibility"] = "   "
    by_id = _by_id([
        {"id": "feat-001"}, {"id": "refactor-001"},
    ])
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(
            {"collisions": [bad]}, by_id)


def test_validator_dies_on_unknown_sid(leerie, capsys):
    bad = _valid_collision_drop_a()
    bad["a_sid"] = "ghost-001"
    by_id = _by_id([
        {"id": "feat-008"}, {"id": "refactor-001"},
    ])
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(
            {"collisions": [bad]}, by_id)
    err = capsys.readouterr().err
    assert "ghost-001" in err


def test_validator_dies_on_self_collision(leerie, capsys):
    bad = _valid_collision_drop_a()
    bad["b_sid"] = bad["a_sid"]
    by_id = _by_id([
        {"id": bad["a_sid"]},
    ])
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(
            {"collisions": [bad]}, by_id)


def test_validator_dies_on_duplicate_pair(leerie, capsys):
    """Two collisions on the same pair (even with different field
    order) would be incoherent — the model must pick one resolution
    per pair."""
    c1 = _valid_collision_drop_a()
    c2 = dict(c1)
    c2["a_sid"], c2["b_sid"] = c1["b_sid"], c1["a_sid"]  # swapped
    by_id = _by_id([
        {"id": c1["a_sid"]}, {"id": c1["b_sid"]},
    ])
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(
            {"collisions": [c1, c2]}, by_id)


# --------------------------------------------------------------------- #
# _apply_overlap_drop
# --------------------------------------------------------------------- #

def _two_plans_basic():
    """Two plans, three subtasks: feat-008 + refactor-001 are the
    colliding pair; downstream-001 depends on feat-008."""
    return [
        {
            "domain": "feature-implementation",
            "subtasks": [
                {"id": "feat-008", "title": "Add AuthShell",
                 "intent": "...", "provides": ["auth-shell-adopted"],
                 "requires": [], "depends_on": [],
                 "files_likely_touched": ["src/auth-shell.tsx"],
                 "success_criteria_seed": "feat-008 criteria"},
                {"id": "downstream-001", "title": "Use AuthShell",
                 "intent": "...", "provides": [],
                 "requires": [{"tag": "auth-shell-adopted",
                               "extent": "in_plan"}],
                 "depends_on": ["feat-008"],
                 "files_likely_touched": [],
                 "success_criteria_seed": "downstream criteria"},
            ],
        },
        {
            "domain": "refactoring",
            "subtasks": [
                {"id": "refactor-001", "title": "Extract AuthShell",
                 "intent": "...", "provides": ["auth-shell-component"],
                 "requires": [], "depends_on": [],
                 "files_likely_touched": ["src/auth-shell.tsx"],
                 "success_criteria_seed": "refactor-001 criteria"},
            ],
        },
    ]


def test_apply_drop_removes_dropped_sid(leerie):
    plans = _two_plans_basic()
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    surviving = {s["id"] for plan in plans for s in plan["subtasks"]}
    assert "feat-008" not in surviving
    assert {"downstream-001", "refactor-001"} == surviving


def test_apply_drop_rewrites_downstream_depends_on(leerie):
    plans = _two_plans_basic()
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    downstream = next(s for plan in plans for s in plan["subtasks"]
                      if s["id"] == "downstream-001")
    assert "feat-008" not in downstream["depends_on"]
    assert "refactor-001" in downstream["depends_on"]


def test_apply_drop_silent_on_missing_sid(leerie):
    """Mirrors the conditional_drops apply step — dropping an unknown
    sid is a silent no-op, not a die. The validator catches truly
    unknown sids upstream."""
    plans = _two_plans_basic()
    before = copy.deepcopy(plans)
    leerie._apply_overlap_drop(plans, dropped_sid="ghost-999",
                               surviving_sid="refactor-001")
    assert plans == before


def test_apply_drop_unions_provides_to_survivor(leerie):
    """The dropped subtask's `provides` tags must be unioned into the
    survivor — otherwise downstream `requires` entries that matched
    the dropped subtask's tags become orphans that surface as a
    confusing `validate_plan` error instead of a clean plan-time
    resolution. This is the load-bearing fix from the self-review."""
    plans = _two_plans_basic()
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    # feat-008 provided "auth-shell-adopted"; refactor-001 originally
    # provided "auth-shell-component"; post-drop the survivor must
    # provide both.
    assert "auth-shell-adopted" in survivor["provides"]
    assert "auth-shell-component" in survivor["provides"]


def test_apply_drop_resolves_orphan_downstream_requires(leerie):
    """End-to-end pin on the `0c4bab`-style failure mode: a downstream
    subtask requires a tag that only the dropped subtask provides;
    after the drop, that requirement must resolve against the
    surviving subtask's now-unioned provides — not orphan into a
    `validate_plan` error."""
    plans = [
        {"domain": "feature-implementation", "subtasks": [
            {"id": "feat-008", "title": "x", "intent": "x",
             "provides": ["auth-shell-adopted"], "requires": [],
             "depends_on": [], "files_likely_touched": [],
             "success_criteria_seed": "x"},
            {"id": "feat-011", "title": "consumer", "intent": "x",
             "provides": [], "requires": [
                 {"tag": "auth-shell-adopted", "extent": "in_plan"}],
             "depends_on": [], "files_likely_touched": [],
             "success_criteria_seed": "x"},
        ]},
        {"domain": "refactoring", "subtasks": [
            {"id": "refactor-001", "title": "x", "intent": "x",
             "provides": ["auth-shell-component"], "requires": [],
             "depends_on": [], "files_likely_touched": [],
             "success_criteria_seed": "x"},
        ]},
    ]
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    # Build the merged subtasks dict the way schedule()/validate_plan do.
    merged = {s["id"]: s for plan in plans for s in plan["subtasks"]}
    all_provides = {tag for s in merged.values()
                    for tag in s.get("provides", [])}
    consumer = merged["feat-011"]
    for entry in consumer["requires"]:
        if entry.get("extent") == "in_plan":
            assert entry["tag"] in all_provides, (
                f"orphan requires: {entry['tag']} not provided by any "
                "surviving subtask — _apply_overlap_drop failed to "
                "union dropped.provides into the survivor")


def test_apply_drop_drops_self_referencing_requires(leerie):
    """After unioning the dropped subtask's `provides` into the
    survivor, any `extent: in_plan` requires entry on the survivor
    whose tag is now in its own provides is a graph self-loop and
    must be removed. Mirrors the same cleanup in _apply_overlap_merge."""
    plans = _two_plans_basic()
    # Make refactor-001 require what feat-008 provides — after the
    # drop, the survivor (refactor-001) would self-loop if the cleanup
    # didn't fire.
    refactor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    refactor["requires"] = [
        {"tag": "auth-shell-adopted", "extent": "in_plan"}]
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    assert "auth-shell-adopted" in survivor["provides"]
    # The self-loop requires entry must be gone.
    assert all(e.get("tag") != "auth-shell-adopted"
               for e in survivor["requires"]
               if isinstance(e, dict))


def test_apply_drop_preserves_external_requires_on_survivor(leerie):
    """External requires (out-of-graph) stay regardless of the
    provides union — they are deploy-time preconditions, not graph
    edges, so they can't be self-loops."""
    plans = _two_plans_basic()
    refactor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    refactor["requires"] = [
        {"tag": "auth-shell-adopted", "extent": "external",
         "reason": "out of graph"}]
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    # External entry survives even though the tag is now in provides.
    assert any(e.get("tag") == "auth-shell-adopted"
               and e.get("extent") == "external"
               for e in survivor["requires"]
               if isinstance(e, dict))


def test_apply_drop_idempotent_provides_dedup(leerie):
    """If the survivor already provides one of the dropped tags, the
    union must not duplicate it."""
    plans = _two_plans_basic()
    # Pre-stamp refactor-001 with the same tag feat-008 provides.
    refactor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    refactor["provides"].append("auth-shell-adopted")
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="refactor-001")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    # Exactly one occurrence, not two.
    assert survivor["provides"].count("auth-shell-adopted") == 1


# --------------------------------------------------------------------- #
# _apply_overlap_merge
# --------------------------------------------------------------------- #

def test_apply_merge_collapses_to_lex_smaller_sid(leerie):
    """Stable surviving-sid rule: lexicographically smaller wins.
    Calling with (a, b) and (b, a) must produce the same surviving sid."""
    plans1 = _two_plans_basic()
    surviving1 = leerie._apply_overlap_merge(
        plans1, "feat-008", "refactor-001",
        artifact="AuthShell component",
        merge_feasibility="Unified API: optional description prop.")
    plans2 = _two_plans_basic()
    surviving2 = leerie._apply_overlap_merge(
        plans2, "refactor-001", "feat-008",
        artifact="AuthShell component",
        merge_feasibility="Unified API: optional description prop.")
    assert surviving1 == surviving2 == "feat-008"  # lex-smaller


def test_apply_merge_unions_files_provides_requires(leerie):
    plans = _two_plans_basic()
    # Add a distinct file + require + depends to refactor-001 so we
    # can confirm union semantics.
    refactor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "refactor-001")
    refactor["files_likely_touched"].append("src/auth-shell.test.tsx")
    refactor["provides"].append("extra-tag")
    refactor["requires"] = [
        {"tag": "brand-tokens", "extent": "external",
         "reason": "globals.css"}]
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell",
        merge_feasibility="Unified.")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert set(survivor["files_likely_touched"]) == {
        "src/auth-shell.tsx", "src/auth-shell.test.tsx"}
    assert set(survivor["provides"]) == {
        "auth-shell-adopted", "auth-shell-component", "extra-tag"}
    # external requires survive (out-of-graph; not a self-loop)
    assert any(e.get("tag") == "brand-tokens"
               for e in survivor["requires"])


def test_apply_merge_drops_self_referencing_requires(leerie):
    """If the merged unit's `provides` now covers a requires tag from
    either half, that requires entry becomes a self-loop and must be
    dropped."""
    plans = _two_plans_basic()
    # Make feat-008 require what refactor-001 provides → after merge,
    # the merged unit provides both, so the requires entry becomes a
    # self-loop.
    feat = next(s for plan in plans for s in plan["subtasks"]
                if s["id"] == "feat-008")
    feat["requires"] = [
        {"tag": "auth-shell-component", "extent": "in_plan"}]
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell",
        merge_feasibility="Unified.")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert all(e.get("tag") != "auth-shell-component"
               for e in survivor["requires"]
               if isinstance(e, dict))


def test_apply_merge_writes_feasibility_into_intent(leerie):
    plans = _two_plans_basic()
    feasibility = ("Both can be satisfied by a brand-styled AuthShell "
                   "with optional description prop.")
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell component",
        merge_feasibility=feasibility)
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert feasibility in survivor["intent"]
    assert "Merged with refactor-001 by plan-overlap-judge" in survivor["intent"]
    assert "AuthShell component" in survivor["intent"]


def test_apply_merge_concatenates_success_criteria(leerie):
    plans = _two_plans_basic()
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell", merge_feasibility="Unified.")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert "feat-008 criteria" in survivor["success_criteria_seed"]
    assert "refactor-001 criteria" in survivor["success_criteria_seed"]
    assert " AND " in survivor["success_criteria_seed"]


def test_apply_merge_rewrites_downstream_depends_on(leerie):
    plans = _two_plans_basic()
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell", merge_feasibility="Unified.")
    downstream = next(s for plan in plans for s in plan["subtasks"]
                      if s["id"] == "downstream-001")
    # feat-008 is the survivor (lex-smaller), so downstream-001's
    # depends_on stays pointing at feat-008.
    assert downstream["depends_on"] == ["feat-008"]


def test_apply_merge_stamps_merged_from(leerie):
    plans = _two_plans_basic()
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="AuthShell", merge_feasibility="Unified.")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert survivor.get("_merged_from") == ["refactor-001"]


def test_apply_merge_die_on_missing_sid(leerie, capsys):
    plans = _two_plans_basic()
    with pytest.raises(SystemExit):
        leerie._apply_overlap_merge(
            plans, "feat-008", "ghost-999",
            artifact="X", merge_feasibility="Unified.")


# --------------------------------------------------------------------- #
# Idempotence — applying the same merge twice is a noop on the second
# pass (the absorbed sid is already gone; the survivor's `_merged_from`
# already lists it). Pin the invariant.
# --------------------------------------------------------------------- #

def test_apply_merge_then_drop_idempotence(leerie):
    """A run that re-applies a previously-applied merge (e.g. resume)
    must not silently double-stamp _merged_from."""
    plans = _two_plans_basic()
    leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="X", merge_feasibility="Unified.")
    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-008")
    assert survivor.get("_merged_from") == ["refactor-001"]
    # Re-applying with the dropped sid no longer present — the
    # validator would catch this upstream, but the helper's missing-sid
    # die() makes it explicit.
    with pytest.raises(SystemExit):
        leerie._apply_overlap_merge(
            plans, "feat-008", "refactor-001",
            artifact="X", merge_feasibility="Unified.")


def test_apply_drop_self_loop_is_noop(leerie):
    """If the apply-loop's survivor-rewrite collapses both endpoints
    onto the same sid (transitive cluster of drops), calling
    _apply_overlap_drop with dropped_sid == surviving_sid must leave
    the plan untouched — not filter the sole surviving copy out."""
    plans = _two_plans_basic()
    before = copy.deepcopy(plans)
    leerie._apply_overlap_drop(plans, dropped_sid="feat-008",
                               surviving_sid="feat-008")
    assert plans == before


# --------------------------------------------------------------------- #
# _compute_overlap_anchors — pure helper
# --------------------------------------------------------------------- #

def test_compute_anchors_empty_collisions(leerie):
    assert leerie._compute_overlap_anchors([]) == set()


def test_compute_anchors_no_shared_endpoints(leerie):
    """Each sid appears in at most one collision → no anchors."""
    collisions = [
        {"a_sid": "A", "b_sid": "B", "resolution": "merge"},
        {"a_sid": "C", "b_sid": "D", "resolution": "drop_a"},
    ]
    assert leerie._compute_overlap_anchors(collisions) == set()


def test_compute_anchors_shared_endpoint(leerie):
    """A sid appearing in 2+ non-unresolvable collisions is an anchor."""
    collisions = [
        {"a_sid": "feat-002", "b_sid": "config-001", "resolution": "merge"},
        {"a_sid": "feat-002", "b_sid": "config-002", "resolution": "merge"},
    ]
    assert leerie._compute_overlap_anchors(collisions) == {"feat-002"}


def test_compute_anchors_excludes_unresolvable(leerie):
    """Unresolvable collisions don't establish overlap claims — they
    surface separately and never mutate the plan."""
    collisions = [
        {"a_sid": "A", "b_sid": "B", "resolution": "merge"},
        # A also appears in an unresolvable; that one doesn't count.
        {"a_sid": "A", "b_sid": "C", "resolution": "unresolvable"},
    ]
    assert leerie._compute_overlap_anchors(collisions) == set()


# --------------------------------------------------------------------- #
# _validate_overlap_judge_output — anchor consistency checks
# --------------------------------------------------------------------- #
#
# A drop of an anchor would delete the subtask other collisions claim
# absorbs them. A merge between two anchors is a multi-way tangle the
# pairwise protocol can't express. Both die() before any mutation.

def test_validator_dies_on_drop_of_anchor_via_drop_a(leerie, capsys):
    """drop_a(A, B) where A also appears as the anchor in merge(A, C)
    must die — the judge is contradicting itself."""
    by_id = _by_id([{"id": "A"}, {"id": "B"}, {"id": "C"}])
    output = {"collisions": [
        {"a_sid": "A", "b_sid": "B", "artifact": "X",
         "resolution": "drop_a", "reason": "drop A"},
        {"a_sid": "A", "b_sid": "C", "artifact": "Y",
         "resolution": "merge", "reason": "merge",
         "merge_feasibility": "ok"},
    ]}
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(output, by_id)
    err = capsys.readouterr().err
    assert "'A'" in err
    assert "anchor" in err


def test_validator_dies_on_drop_of_anchor_via_drop_b(leerie, capsys):
    """Mirror of the above but the anchor appears as b_sid in a drop_b."""
    by_id = _by_id([{"id": "A"}, {"id": "B"}, {"id": "C"}])
    output = {"collisions": [
        {"a_sid": "B", "b_sid": "A", "artifact": "X",
         "resolution": "drop_b", "reason": "drop A"},
        {"a_sid": "A", "b_sid": "C", "artifact": "Y",
         "resolution": "merge", "reason": "merge",
         "merge_feasibility": "ok"},
    ]}
    with pytest.raises(SystemExit):
        leerie._validate_overlap_judge_output(output, by_id)


def test_validator_passes_on_multi_anchor_merge(leerie):
    """A `merge` between two anchors is no longer rejected at
    validation time — the apply loop handles it via lex-smaller
    within the unified cluster, and the absorbed subtask's intent
    carries forward per the DESIGN §5 merge_feasibility carry-
    forward invariant. This pins the removal of the earlier over-
    aggressive both-endpoints-anchor check; the apply-time tests
    `test_apply_collisions_triangle_resolves_to_one_survivor` and
    `test_apply_collisions_4cycle_resolves_to_one_survivor` cover
    the corresponding multi-anchor cluster behavior end-to-end."""
    by_id = _by_id([{"id": "A"}, {"id": "B"}, {"id": "C"}, {"id": "D"}])
    output = {"collisions": [
        {"a_sid": "A", "b_sid": "C", "artifact": "X",
         "resolution": "merge", "reason": "x",
         "merge_feasibility": "ok"},
        {"a_sid": "B", "b_sid": "D", "artifact": "Y",
         "resolution": "merge", "reason": "x",
         "merge_feasibility": "ok"},
        {"a_sid": "A", "b_sid": "B", "artifact": "Z",
         "resolution": "merge", "reason": "x",
         "merge_feasibility": "ok"},
    ]}
    # Should not raise.
    leerie._validate_overlap_judge_output(output, by_id)


def test_validator_passes_on_clean_anchor_cluster(leerie):
    """The summarizer-shape case: merge(A, B) and merge(A, C) where A
    is the anchor, nothing else. Must validate cleanly so the apply
    loop can run."""
    by_id = _by_id([
        {"id": "feat-002"}, {"id": "config-001"}, {"id": "config-002"},
    ])
    output = {"collisions": [
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "tsconfig.server.json", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
        {"a_sid": "feat-002", "b_sid": "config-002",
         "artifact": "tsconfig.json", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
    ]}
    # Should not raise.
    leerie._validate_overlap_judge_output(output, by_id)


# --------------------------------------------------------------------- #
# _apply_overlap_collisions — anchor-survivor rule
# --------------------------------------------------------------------- #
#
# These pin the load-bearing fix for the summarizer-run crash. The judge
# emitted merge(feat-002, config-001) and merge(feat-002, config-002) —
# one subtask overlapping with two siblings on two different artifacts.
# The anchor rule keeps feat-002 (the broader scope per the judge's own
# `reason`) as the survivor of both merges, overriding the lex-smaller
# default that would have kept the narrowest (config-001) instead.

def _three_planner_cluster():
    """Three plans, three subtasks (feat-002, config-001, config-002).
    feat-002 is the broader scope; config-001 and config-002 each
    cover half of what feat-002 covers."""
    return [
        {
            "domain": "feature-implementation",
            "subtasks": [
                {"id": "feat-002", "title": "Author tsconfig.server.json",
                 "intent": "feat: author the emitting backend tsconfig",
                 "provides": ["server-build-target-ready"],
                 "requires": [], "depends_on": [],
                 "files_likely_touched": ["tsconfig.server.json",
                                          "tsconfig.json"],
                 "success_criteria_seed": "tsc -b emits dist/server.js"},
            ],
        },
        {
            "domain": "configuration-build",
            "subtasks": [
                {"id": "config-001", "title": "Add tsconfig.server.json",
                 "intent": "config: add the emitting backend project",
                 "provides": ["backend-emit-config"],
                 "requires": [], "depends_on": [],
                 "files_likely_touched": ["tsconfig.server.json"],
                 "success_criteria_seed": "backend project emits to dist"},
                {"id": "config-002", "title": "Wire backend into tsc -b",
                 "intent": "config: reference backend project from root",
                 "provides": ["backend-wired-into-tsc-b"],
                 "requires": [], "depends_on": [],
                 "files_likely_touched": ["tsconfig.json",
                                          "tsconfig.app.json"],
                 "success_criteria_seed": "pnpm build runs both projects"},
            ],
        },
    ]


def test_apply_collisions_anchor_survives_both_merges(leerie):
    """The exact failure shape from the summarizer run: judge emits
    merge(feat-002, config-001) and merge(feat-002, config-002).
    feat-002 is the anchor (appears in 2 merges) and MUST survive
    both — even though lex order would pick config-001."""
    plans = _three_planner_cluster()
    collisions = [
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "tsconfig.server.json",
         "resolution": "merge",
         "reason": "config-001 is a strict subset of feat-002",
         "merge_feasibility": "single emitting tsconfig satisfies both"},
        {"a_sid": "feat-002", "b_sid": "config-002",
         "artifact": "tsconfig.json (tsc -b wiring)",
         "resolution": "merge",
         "reason": "both rewire root tsconfig.json",
         "merge_feasibility": "one wired tsconfig with backend reference"},
    ]
    applied = leerie._apply_overlap_collisions(plans, collisions)

    # Anchor survives — NOT the lex-smaller config-001.
    survivors = {s["id"] for plan in plans for s in plan["subtasks"]}
    assert survivors == {"feat-002"}, (
        f"expected anchor feat-002 to survive, got {survivors}")

    survivor = next(s for plan in plans for s in plan["subtasks"]
                    if s["id"] == "feat-002")
    # Both merge_feasibility statements carried into intent — the
    # discipline that distinguishes merge from frankenstein, applied
    # twice as the anchor absorbs each partner.
    assert "single emitting tsconfig satisfies both" in survivor["intent"]
    assert "one wired tsconfig with backend reference" in survivor["intent"]
    # provides is the union of all three subtasks.
    assert set(survivor["provides"]) >= {
        "server-build-target-ready", "backend-emit-config",
        "backend-wired-into-tsc-b"}
    # files_likely_touched is the union.
    assert set(survivor["files_likely_touched"]) >= {
        "tsconfig.server.json", "tsconfig.json", "tsconfig.app.json"}
    # _merged_from records both absorbed sids.
    merged_from = set(survivor.get("_merged_from") or [])
    assert merged_from == {"config-001", "config-002"}

    # applied audit trail records both as `merge`, no skips.
    actions = [a["action"] for a in applied]
    assert actions == ["merge", "merge"]


def test_apply_collisions_no_anchor_uses_lex_smaller(leerie):
    """Regression: when no shared endpoint exists, the lex-smaller
    rule still drives the survivor (preserves determinism for the
    common pairwise case)."""
    plans = _three_planner_cluster()
    # Add a 4th subtask so we can have two independent merges.
    plans[0]["subtasks"].append({
        "id": "feat-003", "title": "x", "intent": "x",
        "provides": [], "requires": [], "depends_on": [],
        "files_likely_touched": [],
        "success_criteria_seed": "x",
    })
    collisions = [
        # Pair 1: feat-002 ↔ config-001, no shared endpoint elsewhere.
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
        # Pair 2: feat-003 ↔ config-002, also no shared endpoint.
        {"a_sid": "feat-003", "b_sid": "config-002",
         "artifact": "Y", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
    ]
    leerie._apply_overlap_collisions(plans, collisions)

    survivors = {s["id"] for plan in plans for s in plan["subtasks"]}
    # Lex-smaller wins each pair: config-001 < feat-002, config-002 < feat-003.
    assert survivors == {"config-001", "config-002"}


def test_apply_collisions_anchor_preserves_unrelated_subtasks(leerie):
    """A cluster touching {anchor, B, C} must not perturb sibling
    subtasks outside the cluster. Pin the blast radius."""
    plans = _three_planner_cluster()
    # downstream subtask that depended on the anchor — its depends_on
    # should remain pointed at the anchor (which survived).
    plans[0]["subtasks"].append({
        "id": "downstream-001", "title": "consume server build",
        "intent": "x", "provides": [],
        "requires": [{"tag": "server-build-target-ready",
                      "extent": "in_plan"}],
        "depends_on": ["feat-002"],
        "files_likely_touched": ["src/server.ts"],
        "success_criteria_seed": "dist/server.js exists",
    })
    # unrelated subtask in a third plan.
    plans.append({
        "domain": "dependency-migration",
        "subtasks": [
            {"id": "deps-001", "title": "unrelated work",
             "intent": "x", "provides": ["unrelated"],
             "requires": [], "depends_on": [],
             "files_likely_touched": ["package.json"],
             "success_criteria_seed": "prisma CLI installed"},
        ],
    })
    collisions = [
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
        {"a_sid": "feat-002", "b_sid": "config-002",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok"},
    ]
    leerie._apply_overlap_collisions(plans, collisions)

    deps_001 = next(
        (s for plan in plans for s in plan["subtasks"]
         if s["id"] == "deps-001"), None)
    assert deps_001 is not None
    assert deps_001["provides"] == ["unrelated"]
    # downstream-001's depends_on remains pointing at the anchor.
    downstream = next(s for plan in plans for s in plan["subtasks"]
                      if s["id"] == "downstream-001")
    assert downstream["depends_on"] == ["feat-002"]


def test_apply_collisions_pair_ordering_independent_with_anchor(leerie):
    """Calling with [merge(A,B), merge(A,C)] vs [merge(A,C), merge(A,B)]
    must produce the same anchor-as-survivor outcome regardless of pair
    ordering — A is the anchor in both inputs."""
    cs1 = [
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok1"},
        {"a_sid": "feat-002", "b_sid": "config-002",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok2"},
    ]
    cs2 = [
        {"a_sid": "feat-002", "b_sid": "config-002",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok2"},
        {"a_sid": "feat-002", "b_sid": "config-001",
         "artifact": "X", "resolution": "merge",
         "reason": "x", "merge_feasibility": "ok1"},
    ]
    plans1 = _three_planner_cluster()
    plans2 = _three_planner_cluster()
    leerie._apply_overlap_collisions(plans1, cs1)
    leerie._apply_overlap_collisions(plans2, cs2)
    surv1 = {s["id"] for plan in plans1 for s in plan["subtasks"]}
    surv2 = {s["id"] for plan in plans2 for s in plan["subtasks"]}
    assert surv1 == surv2 == {"feat-002"}


def test_apply_overlap_merge_survivor_hint_overrides_lex(leerie):
    """Direct unit-test of the new survivor_hint parameter. Without
    it, lex-smaller (refactor-001) wins; with hint=feat-008, the
    larger-lex sid wins."""
    plans = _two_plans_basic()
    surv_lex = leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="X", merge_feasibility="ok")
    assert surv_lex == "feat-008"  # NOTE: lex-smaller of these two

    # Reset and try with explicit hint pointing the other way.
    plans = _two_plans_basic()
    surv_hint = leerie._apply_overlap_merge(
        plans, "feat-008", "refactor-001",
        artifact="X", merge_feasibility="ok",
        survivor_hint="refactor-001")
    assert surv_hint == "refactor-001"


def test_apply_overlap_merge_survivor_hint_must_be_endpoint(leerie):
    """Passing a hint that isn't either endpoint dies — defensive
    against orchestrator logic bugs that compute the wrong anchor."""
    plans = _two_plans_basic()
    with pytest.raises(SystemExit):
        leerie._apply_overlap_merge(
            plans, "feat-008", "refactor-001",
            artifact="X", merge_feasibility="ok",
            survivor_hint="ghost-999")


# --------------------------------------------------------------------- #
# DESIGN §5 merge_feasibility carry-forward invariant
# --------------------------------------------------------------------- #
#
# Pin the load-bearing fix for Defect E from the audit: when a subtask
# becomes the survivor of one merge and is later absorbed by another
# merge, the `merge_feasibility` statement from the earlier merge must
# survive in the final survivor's intent. Reproducible via REPL across
# multiple collision shapes (E3 and E15 from the audit) and 3-of-6
# orderings of the same tangle (E23) before the fix.

def _four_subtask_plans():
    return [{"domain": "d", "subtasks": [
        {"id": "A", "title": "A-t", "intent": "A-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "A-c"},
        {"id": "B", "title": "B-t", "intent": "B-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "B-c"},
        {"id": "C", "title": "C-t", "intent": "C-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "C-c"},
        {"id": "D", "title": "D-t", "intent": "D-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "D-c"},
    ]}]


def test_merge_feasibility_carry_forward_e15_shape(leerie):
    """Reproduces audit experiment E15 / E23 perm 0 — a transitive
    merge chain where one subtask first becomes a merge survivor and
    is then itself absorbed. Pre-fix: the first merge's mf is silently
    lost. Post-fix (DESIGN §5 carry-forward invariant): all three mfs
    must survive in the final intent."""
    plans = _four_subtask_plans()
    collisions = [
        # B absorbs D, gains mf_BD in its intent.
        {"a_sid": "B", "b_sid": "D", "artifact": "F1",
         "resolution": "merge", "merge_feasibility": "mf_BD",
         "reason": "x"},
        # A absorbs C, gains mf_CA in its intent.
        {"a_sid": "C", "b_sid": "A", "artifact": "F2",
         "resolution": "merge", "merge_feasibility": "mf_CA",
         "reason": "x"},
        # A absorbs B — B's intent must carry mf_BD forward into A.
        {"a_sid": "A", "b_sid": "B", "artifact": "F3",
         "resolution": "merge", "merge_feasibility": "mf_AB",
         "reason": "x"},
    ]
    leerie._apply_overlap_collisions(plans, collisions)
    survivors = [s["id"] for plan in plans for s in plan["subtasks"]]
    assert survivors == ["A"], f"expected single survivor A, got {survivors}"
    surv = next(s for plan in plans for s in plan["subtasks"])
    # All three merge_feasibility statements MUST appear in the
    # surviving intent — the carry-forward invariant.
    for mf in ("mf_BD", "mf_CA", "mf_AB"):
        assert mf in surv["intent"], (
            f"merge_feasibility {mf!r} lost from final intent — "
            f"DESIGN §5 carry-forward invariant violated.\n"
            f"intent was: {surv['intent']!r}")


def test_merge_feasibility_carry_forward_marker_in_intent(leerie):
    """The intent assembly uses a `--- Absorbed intent from {sid} ---`
    marker so the absorbed subtask's intent block is auditable and
    distinguishable from the survivor's original intent. Pin the
    marker so we don't accidentally regress to a format that loses
    traceability."""
    plans = _four_subtask_plans()
    # Anchor set is computed once upfront from the full collision list:
    # B appears in pairs 1+2 (= 2 collisions) → anchors = {B}.
    # Pair 1: D non-anchor, B anchor → survivor_hint=B. B survives,
    # D absorbed. B's intent now contains mf_BD.
    # Pair 2: A non-anchor, B anchor → survivor_hint=B. B survives,
    # A absorbed. B's intent contains the absorbed-A block AND the
    # prior absorbed-D block (carried forward from pair 1 — DESIGN §5
    # merge_feasibility carry-forward invariant).
    collisions = [
        {"a_sid": "B", "b_sid": "D", "artifact": "F1",
         "resolution": "merge", "merge_feasibility": "mf_BD",
         "reason": "x"},
        {"a_sid": "A", "b_sid": "B", "artifact": "F2",
         "resolution": "merge", "merge_feasibility": "mf_AB",
         "reason": "x"},
    ]
    leerie._apply_overlap_collisions(plans, collisions)
    survivors = {s["id"] for plan in plans for s in plan["subtasks"]}
    # B survives (anchor); C survives (untouched); A and D absorbed.
    assert survivors == {"B", "C"}, (
        f"expected B (anchor) and C (untouched) to survive, got {survivors}")
    surv = next(s for plan in plans for s in plan["subtasks"]
                if s["id"] == "B")
    # Pair 1 absorbed D into B → "Absorbed intent from D" marker.
    # Pair 2 absorbed A into B → "Absorbed intent from A" marker.
    assert "--- Absorbed intent from D ---" in surv["intent"]
    assert "--- Absorbed intent from A ---" in surv["intent"]
    # Both merge_feasibility statements preserved.
    assert "mf_BD" in surv["intent"]
    assert "mf_AB" in surv["intent"]


# --------------------------------------------------------------------- #
# Connected-cluster (multi-anchor) shapes — apply loop handles them
# without the now-removed both-endpoints-anchor validator check
# --------------------------------------------------------------------- #

def test_apply_collisions_triangle_resolves_to_one_survivor(leerie):
    """Audit experiment E2: `merge(A,B), merge(A,C), merge(B,C)` — all
    three sids appear 2x → all in anchor set. The first two merges
    collapse A/B/C onto A (lex-smaller). The third pair's endpoints
    both resolve to A → recorded as `skipped_redundant`. No crash."""
    plans = [{"domain": "d", "subtasks": [
        {"id": "A", "title": "A-t", "intent": "A-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "A-c"},
        {"id": "B", "title": "B-t", "intent": "B-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "B-c"},
        {"id": "C", "title": "C-t", "intent": "C-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "C-c"},
    ]}]
    collisions = [
        {"a_sid": "A", "b_sid": "B", "artifact": "X",
         "resolution": "merge", "merge_feasibility": "mf_AB", "reason": "x"},
        {"a_sid": "A", "b_sid": "C", "artifact": "Y",
         "resolution": "merge", "merge_feasibility": "mf_AC", "reason": "x"},
        {"a_sid": "B", "b_sid": "C", "artifact": "Z",
         "resolution": "merge", "merge_feasibility": "mf_BC", "reason": "x"},
    ]
    applied = leerie._apply_overlap_collisions(plans, collisions)
    survivors = [s["id"] for plan in plans for s in plan["subtasks"]]
    assert survivors == ["A"]
    actions = [a["action"] for a in applied]
    assert actions == ["merge", "merge", "skipped_redundant"]
    # The redundant entry must name the original sids and the survivor.
    redundant = applied[2]
    assert redundant["original_a_sid"] == "B"
    assert redundant["original_b_sid"] == "C"
    assert redundant["collapsed_to"] == "A"
    assert redundant["merge_feasibility"] == "mf_BC"


def test_apply_collisions_4cycle_resolves_to_one_survivor(leerie):
    """Audit experiment E12: a 4-cycle `merge(A,B),(B,C),(C,D),(D,A)`
    — every sid appears 2x. The first three merges collapse all four
    onto A (lex-smaller). The fourth pair (D,A) resolves to (A,A) →
    `skipped_redundant`. No validator die() because the both-endpoints-
    anchor check has been removed."""
    plans = _four_subtask_plans()
    collisions = [
        {"a_sid": "A", "b_sid": "B", "artifact": "X1",
         "resolution": "merge", "merge_feasibility": "mf1", "reason": "x"},
        {"a_sid": "B", "b_sid": "C", "artifact": "X2",
         "resolution": "merge", "merge_feasibility": "mf2", "reason": "x"},
        {"a_sid": "C", "b_sid": "D", "artifact": "X3",
         "resolution": "merge", "merge_feasibility": "mf3", "reason": "x"},
        {"a_sid": "D", "b_sid": "A", "artifact": "X4",
         "resolution": "merge", "merge_feasibility": "mf4", "reason": "x"},
    ]
    # Validator must NOT die (regression pin on the removal of the
    # both-endpoints-anchor check).
    by_id = {s["id"]: s for plan in plans for s in plan["subtasks"]}
    leerie._validate_overlap_judge_output({"collisions": collisions}, by_id)

    applied = leerie._apply_overlap_collisions(plans, collisions)
    survivors = [s["id"] for plan in plans for s in plan["subtasks"]]
    assert survivors == ["A"]
    actions = [a["action"] for a in applied]
    assert actions == ["merge", "merge", "merge", "skipped_redundant"]


# --------------------------------------------------------------------- #
# Anchor as b_sid AND lex-largest — pin the survivor_hint behavior
# --------------------------------------------------------------------- #

def test_apply_collisions_anchor_b_sid_lex_largest(leerie):
    """Audit experiment E14: anchor is b_sid in both pairs AND
    lex-LARGEST. Without the anchor rule, lex-smaller would pick the
    non-anchor partner. The anchor rule must win, otherwise the
    summarizer-style structural intent is silently inverted."""
    plans = [{"domain": "d", "subtasks": [
        {"id": "a-small", "title": "a", "intent": "a-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "x"},
        {"id": "b-small", "title": "b", "intent": "b-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "x"},
        {"id": "z-anchor", "title": "z", "intent": "z-i", "provides": [],
         "requires": [], "depends_on": [], "files_likely_touched": [],
         "success_criteria_seed": "x"},
    ]}]
    collisions = [
        {"a_sid": "a-small", "b_sid": "z-anchor", "artifact": "X",
         "resolution": "merge", "merge_feasibility": "mf1", "reason": "x"},
        {"a_sid": "b-small", "b_sid": "z-anchor", "artifact": "Y",
         "resolution": "merge", "merge_feasibility": "mf2", "reason": "x"},
    ]
    leerie._apply_overlap_collisions(plans, collisions)
    survivors = [s["id"] for plan in plans for s in plan["subtasks"]]
    # Anchor wins even though z-anchor > a-small and z-anchor > b-small
    # lexicographically.
    assert survivors == ["z-anchor"], (
        f"anchor rule must override lex when anchor is b_sid and "
        f"lex-largest. Got survivors {survivors}.")


# --------------------------------------------------------------------- #
# Post-merge acyclicity — per-resolution cycle avoidance (DESIGN §5
# *Post-merge acyclicity*). A merge/drop dependency-union that would
# close a transitive cycle is skipped (skipped_would_cycle), not applied.
# --------------------------------------------------------------------- #

def _sccs_of(leerie, plans) -> list:
    """Non-trivial SCCs of the plans' subtask dependency graph, via the
    same helpers the orchestrator's gate uses."""
    by_id = {s["id"]: s for p in plans for s in p.get("subtasks", [])}
    preds, _, _ = leerie._build_predecessor_graph(by_id)
    succ = {sid: set() for sid in by_id}
    for tgt, src_set in preds.items():
        for src in src_set:
            succ[src].add(tgt)
    return leerie._tarjan_sccs(set(by_id), succ)


def test_merge_that_would_cycle_is_skipped(leerie):
    """A(feat-001)→B(bugfix-001), C(bugfix-002)→A. Merging B+C would make
    the survivor inherit C's dep on A → A→B and B→A. Per-resolution cycle
    avoidance must SKIP this merge (skipped_would_cycle), leaving both
    subtasks live and the graph acyclic — rather than applying it and
    die()ing at the post-merge backstop."""
    plans = [
        {"domain": "feature-implementation", "subtasks": [
            {"id": "feat-001", "title": "A", "intent": "a",
             "provides": [], "requires": [], "depends_on": ["bugfix-001"],
             "files_likely_touched": ["src/a.tsx"],
             "success_criteria_seed": "a"},
        ]},
        {"domain": "bug-fixing", "subtasks": [
            {"id": "bugfix-001", "title": "B", "intent": "b",
             "provides": [], "requires": [], "depends_on": [],
             "files_likely_touched": ["src/shared.tsx"],
             "success_criteria_seed": "b"},
            {"id": "bugfix-002", "title": "C", "intent": "c",
             "provides": [], "requires": [], "depends_on": ["feat-001"],
             "files_likely_touched": ["src/shared.tsx"],
             "success_criteria_seed": "c"},
        ]},
    ]
    # Pre-merge: feat-001 → bugfix-001, bugfix-002 → feat-001. Acyclic.
    assert _sccs_of(leerie, plans) == []

    collisions = [{
        "a_sid": "bugfix-001", "b_sid": "bugfix-002",
        "artifact": "src/shared.tsx", "resolution": "merge",
        "merge_feasibility": "both touch shared.tsx", "reason": "overlap",
    }]
    applied = leerie._apply_overlap_collisions(plans, collisions)

    # The merge was skipped, not applied.
    assert len(applied) == 1
    a = applied[0]
    assert a["action"] == "skipped_would_cycle"
    assert a["resolution"] == "merge"
    assert a["a_sid"] == "bugfix-001" and a["b_sid"] == "bugfix-002"
    assert a["artifact"] == "src/shared.tsx"
    assert a["merge_feasibility"] == "both touch shared.tsx"

    # Both subtasks still live; graph stayed acyclic.
    ids = {s["id"] for p in plans for s in p["subtasks"]}
    assert {"feat-001", "bugfix-001", "bugfix-002"} <= ids
    assert _sccs_of(leerie, plans) == []


def test_overlap_judge_cross_cluster_backedge_config_001_002(leerie):
    """Regression for the real PER-REPO-CONFIG.md failure (run 311a67c6):
    an anchor merge (config-001 ← feat-010) makes the survivor inherit
    feat-010's provides tag `T`, which a THIRD subtask (config-002) already
    requires in_plan → new edge config-001 → config-002. config-001 already
    depends_on config-002 → 2-node cycle. Empirically this merge produced
    the exact `config-001 <-> config-002` diagnostic; the fix must SKIP it
    while still applying the separate non-cycling merge, and never
    SystemExit."""
    def make_plans():
        return [
            {"domain": "configuration-build", "subtasks": [
                {"id": "config-001", "title": "impl knobs (anchor)",
                 "intent": "c1", "provides": [], "requires": [],
                 "depends_on": ["config-002"],
                 "files_likely_touched": ["docs/IMPLEMENTATION.md"],
                 "success_criteria_seed": "c1"},
                {"id": "config-002", "title": "resolvers", "intent": "c2",
                 "provides": [],
                 "requires": [{"tag": "T", "extent": "in_plan"}],
                 "depends_on": [],
                 "files_likely_touched": ["orchestrator/leerie.py"],
                 "success_criteria_seed": "c2"},
            ]},
            {"domain": "feature-implementation", "subtasks": [
                {"id": "feat-010", "title": "impl doc rows", "intent": "f10",
                 "provides": ["T"], "requires": [], "depends_on": [],
                 "files_likely_touched": ["docs/IMPLEMENTATION.md"],
                 "success_criteria_seed": "f10"},
                # A separate, non-cycling merge partner for config-002.
                {"id": "feat-003", "title": "resolvers dup", "intent": "f3",
                 "provides": [], "requires": [], "depends_on": [],
                 "files_likely_touched": ["orchestrator/leerie.py"],
                 "success_criteria_seed": "f3"},
            ]},
        ]

    cluster = {"a_sid": "config-001", "b_sid": "feat-010",
               "artifact": "IMPLEMENTATION.md §6½ rows", "resolution": "merge",
               "merge_feasibility": "same doc rows", "reason": "dup"}
    separate = {"a_sid": "config-002", "b_sid": "feat-003",
                "artifact": "resolve_capture_deps", "resolution": "merge",
                "merge_feasibility": "same fn", "reason": "dup"}

    plans = make_plans()
    assert _sccs_of(leerie, plans) == []
    applied = leerie._apply_overlap_collisions(plans, [cluster, separate])

    actions = {(x["action"], x.get("resolution"),
                x.get("a_sid") or x.get("surviving_sid"))
               for x in applied}
    # The cycle-closing cluster merge is skipped; the separate merge applies.
    assert any(x["action"] == "skipped_would_cycle"
               and {x["a_sid"], x["b_sid"]} == {"config-001", "feat-010"}
               for x in applied)
    assert any(x["action"] == "merge"
               and x["dropped_sid"] == "feat-003"
               for x in applied)
    assert _sccs_of(leerie, plans) == []
    # config-001 and config-002 both survive (cluster merge skipped).
    ids = {s["id"] for p in plans for s in p["subtasks"]}
    assert {"config-001", "config-002"} <= ids

    # Determinism: reversed collision order → identical action multiset.
    plans_rev = make_plans()
    applied_rev = leerie._apply_overlap_collisions(
        plans_rev, [separate, cluster])
    actions_rev = {(x["action"], x.get("resolution"),
                    x.get("a_sid") or x.get("surviving_sid"))
                   for x in applied_rev}
    assert actions == actions_rev
    assert _sccs_of(leerie, plans_rev) == []


def test_drop_that_would_cycle_is_skipped(leerie):
    """A drop also unions the dropped subtask's `provides` into the
    survivor and rewrites downstream `depends_on`, so it can close a cycle
    too. drop_b(feat-010) folds provides `T` into config-001 → config-001
    → config-002 (which requires T); config-001 already depends_on
    config-002 → cycle. Must be skipped, not applied."""
    plans = [
        {"domain": "configuration-build", "subtasks": [
            {"id": "config-001", "title": "anchor", "intent": "c1",
             "provides": [], "requires": [], "depends_on": ["config-002"],
             "files_likely_touched": ["docs/IMPLEMENTATION.md"],
             "success_criteria_seed": "c1"},
            {"id": "config-002", "title": "resolvers", "intent": "c2",
             "provides": [],
             "requires": [{"tag": "T", "extent": "in_plan"}],
             "depends_on": [],
             "files_likely_touched": ["orchestrator/leerie.py"],
             "success_criteria_seed": "c2"},
        ]},
        {"domain": "feature-implementation", "subtasks": [
            {"id": "feat-010", "title": "rows", "intent": "f", "provides": ["T"],
             "requires": [], "depends_on": [],
             "files_likely_touched": ["docs/IMPLEMENTATION.md"],
             "success_criteria_seed": "f"},
        ]},
    ]
    assert _sccs_of(leerie, plans) == []
    collisions = [{"a_sid": "config-001", "b_sid": "feat-010",
                   "artifact": "IMPL rows", "resolution": "drop_b",
                   "reason": "supersede"}]
    applied = leerie._apply_overlap_collisions(plans, collisions)
    assert applied[0]["action"] == "skipped_would_cycle"
    assert applied[0]["resolution"] == "drop_b"
    ids = {s["id"] for p in plans for s in p["subtasks"]}
    assert {"config-001", "config-002", "feat-010"} <= ids
    assert _sccs_of(leerie, plans) == []


def test_non_cycling_resolutions_still_apply(leerie):
    """The cycle guard is a happy-path no-op: a merge that does NOT create
    a cycle is applied exactly as before."""
    plans = [{"domain": "a", "subtasks": [
        {"id": "a-1", "title": "x", "intent": "i", "provides": [],
         "requires": [], "depends_on": [],
         "files_likely_touched": ["f.py"], "success_criteria_seed": "s"},
        {"id": "b-1", "title": "y", "intent": "i", "provides": [],
         "requires": [], "depends_on": [],
         "files_likely_touched": ["f.py"], "success_criteria_seed": "s"},
    ]}]
    applied = leerie._apply_overlap_collisions(plans, [{
        "a_sid": "a-1", "b_sid": "b-1", "artifact": "f.py",
        "resolution": "merge", "merge_feasibility": "u", "reason": "r"}])
    assert applied[0]["action"] == "merge"
    survivors = {s["id"] for p in plans for s in p["subtasks"]}
    assert survivors == {"a-1"}  # lex-smaller survives, b-1 absorbed


def test_skip_does_not_update_survivor_of(leerie):
    """A skipped merge must NOT pollute the survivor_of map: a later
    collision referencing a skipped endpoint must still resolve to that
    live sid, not a stale survivor. Here the first merge is skipped
    (would cycle); the second merges the still-live config-001 with a
    fresh non-cycling partner and must succeed."""
    plans = [
        {"domain": "configuration-build", "subtasks": [
            {"id": "config-001", "title": "anchor", "intent": "c1",
             "provides": [], "requires": [], "depends_on": ["config-002"],
             "files_likely_touched": ["docs/IMPLEMENTATION.md"],
             "success_criteria_seed": "c1"},
            {"id": "config-002", "title": "resolvers", "intent": "c2",
             "provides": [],
             "requires": [{"tag": "T", "extent": "in_plan"}],
             "depends_on": [],
             "files_likely_touched": ["orchestrator/leerie.py"],
             "success_criteria_seed": "c2"},
        ]},
        {"domain": "feature-implementation", "subtasks": [
            {"id": "feat-010", "title": "rows", "intent": "f", "provides": ["T"],
             "requires": [], "depends_on": [],
             "files_likely_touched": ["docs/IMPLEMENTATION.md"],
             "success_criteria_seed": "f"},
            {"id": "feat-020", "title": "more rows", "intent": "f2",
             "provides": [], "requires": [], "depends_on": [],
             "files_likely_touched": ["docs/IMPLEMENTATION.md"],
             "success_criteria_seed": "f2"},
        ]},
    ]
    collisions = [
        {"a_sid": "config-001", "b_sid": "feat-010", "artifact": "IMPL",
         "resolution": "merge", "merge_feasibility": "u", "reason": "r"},
        {"a_sid": "config-001", "b_sid": "feat-020", "artifact": "IMPL2",
         "resolution": "merge", "merge_feasibility": "u", "reason": "r"},
    ]
    applied = leerie._apply_overlap_collisions(plans, collisions)
    assert applied[0]["action"] == "skipped_would_cycle"
    # Second merge references the still-live config-001 and applies.
    assert applied[1]["action"] == "merge"
    assert applied[1]["surviving_sid"] == "config-001"
    assert applied[1]["dropped_sid"] == "feat-020"
    assert _sccs_of(leerie, plans) == []


def test_would_cycle_helper_is_side_effect_free(leerie):
    """`_would_cycle_after` must not mutate the passed plans — it runs the
    apply on a deep copy."""
    plans = [
        {"domain": "cb", "subtasks": [
            {"id": "config-001", "title": "a", "intent": "c1", "provides": [],
             "requires": [], "depends_on": ["config-002"],
             "files_likely_touched": ["x"], "success_criteria_seed": "c1"},
            {"id": "config-002", "title": "r", "intent": "c2", "provides": [],
             "requires": [{"tag": "T", "extent": "in_plan"}], "depends_on": [],
             "files_likely_touched": ["y"], "success_criteria_seed": "c2"},
        ]},
        {"domain": "fi", "subtasks": [
            {"id": "feat-010", "title": "r", "intent": "f", "provides": ["T"],
             "requires": [], "depends_on": [],
             "files_likely_touched": ["x"], "success_criteria_seed": "f"},
        ]},
    ]
    before = copy.deepcopy(plans)
    result = leerie._would_cycle_after(
        plans,
        lambda tr: leerie._apply_overlap_merge(
            tr, "config-001", "feat-010", "art", "mf"))
    assert result is True  # this merge WOULD cycle
    assert plans == before  # ...but plans is untouched


def test_backstop_die_on_logic_bug(leerie, monkeypatch, capsys):
    """If `_would_cycle_after` is (bug) forced to report no cycle, a
    cycle-inducing merge gets applied and the post-merge backstop must
    still fire — die()ing with the orchestrator-logic-bug wording, NOT
    the user-facing --skip-overlap-judge recovery."""
    monkeypatch.setattr(leerie, "_would_cycle_after", lambda *a, **k: False)

    plans = [
        {"domain": "feature-implementation", "subtasks": [
            {"id": "feat-001", "title": "A", "intent": "a", "provides": [],
             "requires": [], "depends_on": ["bugfix-001"],
             "files_likely_touched": ["src/a.tsx"], "success_criteria_seed": "a"},
        ]},
        {"domain": "bug-fixing", "subtasks": [
            {"id": "bugfix-001", "title": "B", "intent": "b", "provides": [],
             "requires": [], "depends_on": [],
             "files_likely_touched": ["src/shared.tsx"],
             "success_criteria_seed": "b"},
            {"id": "bugfix-002", "title": "C", "intent": "c", "provides": [],
             "requires": [], "depends_on": ["feat-001"],
             "files_likely_touched": ["src/shared.tsx"],
             "success_criteria_seed": "c"},
        ]},
    ]
    collisions = [{"a_sid": "bugfix-001", "b_sid": "bugfix-002",
                   "artifact": "src/shared.tsx", "resolution": "merge",
                   "merge_feasibility": "mf", "reason": "overlap"}]

    class _St:
        def __init__(self):
            self.data = {}

        def save(self):
            pass

    st = _St()
    caps = {"judgment_check_rounds": 1}

    async def _fake_loop(**kwargs):
        return ({"collisions": collisions}, [])

    monkeypatch.setattr(leerie, "_run_checked_loop", _fake_loop)
    monkeypatch.setattr(leerie, "check_overlap_judge_output",
                        lambda *a, **k: [])

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_overlap_judge(
            plans, "task", st, caps, {}, {}))
    err = capsys.readouterr().err
    assert "orchestrator logic bug" in err
    assert "post-merge acyclicity backstop" in err
