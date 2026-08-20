"""Constrained auto-repair of `missing_requires` wiring defects.

DESIGN §5 *A wiring re-check on the fully-merged plan*. The commonest defect
the `wiring_judge` finds is one no planner could have avoided: planners run
blind and in parallel, so a subtask in domain X cannot declare a `requires` on
a tag domain Y's planner has not invented yet, and `phase_reconcile`'s charter
is *declared-but-unmatched* tags — a subtask that declared nothing never enters
its `unresolved_requires` input at all. The gate is the first point in the
pipeline at which the edge can be created.

Measured motivation (2026-08-01): 6 of the 9 runs that ever reached this gate
died at it, and the constrained repair resolves 5 of those 6 outright (it was
3 of 6 when the repair read only the tag channel — PR #145).

`tag_or_dep` is resolved against BOTH dependency channels, because the judge
fills that field with either a capability tag or a subtask id. This file pins
all three accepted shapes — tag / id / single-`_cofile_cluster` fan-out — and,
just as importantly, what each still refuses: a value that is neither a
provided tag nor a surviving subtask id means the plan lacks the *work*, not
the edge; providers spanning *different* clusters remain an ambiguity only a
human can settle (one shared cluster is not — those are the sub-file region
splits of a single file).

The cycle trial is load-bearing rather than defensive. A well-formed but WRONG
edge was measured closing a dependency cycle across an entire 13-subtask plan,
which `_schedule()` then die()s on — so an unguarded repair would convert a
survivable planning defect into a dead run. `test_cycling_edge_is_skipped`
is the pin for that.
"""
from __future__ import annotations

import copy

import pytest


# --------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------- #

def _sub(sid, *, provides=(), requires=(), depends_on=(), files=("f.py",),
         cluster=None):
    s = {
        "id": sid, "title": sid, "intent": f"intent {sid}",
        "success_criteria_seed": "c", "runs_commands": [],
        "files_likely_touched": list(files), "provides": list(provides),
        "requires": [{"tag": t, "extent": "in_plan"} for t in requires],
        "depends_on": list(depends_on), "size": "small",
    }
    if cluster is not None:
        s["_cofile_cluster"] = cluster
    return s


def _plans(*subs_by_domain):
    return [{"domain": d, "status": "ready", "subtasks": list(ss)}
            for d, ss in subs_by_domain]


def _defect(sid, tag, kind="missing_requires", severity="live_defect"):
    return {"kind": kind, "sid": sid, "tag_or_dep": tag,
            "concrete_reason": f"{sid} needs {tag}", "severity": severity}


def _incident_plan():
    """The 6146bd2f shape: a cross-cutting verifier in one domain that must
    run after four rewrites in another, declaring no edge to any of them."""
    return _plans(
        ("refactoring", [
            _sub("refactor-002", provides=["worker"]),
            _sub("refactor-005", requires=["worker"],
                 depends_on=["refactor-002"]),
        ]),
        ("testing", [
            _sub("test-002", provides=["tests-a"], depends_on=["refactor-002"]),
            _sub("test-003", provides=["tests-b"], depends_on=["refactor-002"]),
        ]),
    )


INCIDENT_DEFECTS = [_defect("refactor-005", "tests-a"),
                    _defect("refactor-005", "tests-b")]


# --------------------------------------------------------------------- #
# The repair itself
# --------------------------------------------------------------------- #

def test_repairs_the_incident_shape(leerie):
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, INCIDENT_DEFECTS)
    assert len(repairs) == 2 and not unrepaired
    assert {r["provider"] for r in repairs} == {"test-002", "test-003"}
    tags = {e["tag"] for p in plans for s in p["subtasks"]
            if s["id"] == "refactor-005" for e in s["requires"]}
    assert {"tests-a", "tests-b"} <= tags


def test_repaired_plan_schedules_producers_before_consumer(leerie):
    plans = _incident_plan()
    leerie._repair_missing_requires(plans, INCIDENT_DEFECTS)
    subtasks, waves = leerie._schedule(plans)
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    assert pos["test-002"] < pos["refactor-005"]
    assert pos["test-003"] < pos["refactor-005"]
    assert not leerie.check_plan_wiring(subtasks)
    leerie._validate_plan(subtasks)


def test_unrepaired_plan_races_the_consumer(leerie):
    """Anti-vacuity control: without the repair the consumer shares a wave
    with the producers it is supposed to run after."""
    subtasks, waves = leerie._schedule(_incident_plan())
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    assert pos["refactor-005"] <= pos["test-002"]


# --------------------------------------------------------------------- #
# What must NOT be repaired
# --------------------------------------------------------------------- #

def test_zero_providers_declines(leerie):
    """A value that is neither a provided tag NOR a surviving subtask id
    means the plan is missing the WORK, not the edge. Inventing a dependency
    on nothing would be worse than dying."""
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-005", "nobody-provides-this")])
    assert not repairs and len(unrepaired) == 1


def test_multiple_providers_in_different_clusters_declines(leerie):
    """Ambiguous — only a human can say which provider was meant. Providers
    in one `_cofile_cluster` are the deliberate exception (below)."""
    plans = _plans(
        ("refactoring", [_sub("refactor-001")]),
        ("testing", [_sub("test-001", provides=["shared"]),
                     _sub("test-002", provides=["shared"])]),
    )
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-001", "shared")])
    assert not repairs and len(unrepaired) == 1


# --------------------------------------------------------------------- #
# Channel (b): the id channel. `tag_or_dep` names a subtask, not a tag.
# 23 of the 24 corpus defects refused as "no in-plan provider" were this.
# --------------------------------------------------------------------- #

def test_id_channel_repairs_via_depends_on(leerie):
    plans = _plans(("testing", [_sub("test-001"), _sub("feat-001")]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "feat-001")])
    assert not unrepaired
    assert repairs == [{"sid": "test-001", "tag": "feat-001",
                        "provider": "feat-001", "channel": "id"}]
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["depends_on"] == ["feat-001"]
    # the tag channel must not have been used — no phantom `requires`
    assert by_id["test-001"]["requires"] == []


def test_id_channel_orders_the_producer_first(leerie):
    """The repair is worthless if the added edge does not actually _schedule
    the consumer behind the subtask it names."""
    plans = _plans(("testing", [_sub("test-001"), _sub("feat-001")]))
    leerie._repair_missing_requires(
        plans, [_defect("test-001", "feat-001")])
    _subtasks, waves = leerie._schedule(copy.deepcopy(plans))
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    assert pos["feat-001"] < pos["test-001"]


def test_id_channel_self_reference_declines(leerie):
    plans = _plans(("testing", [_sub("test-001")]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "test-001")])
    assert not repairs and len(unrepaired) == 1


def test_id_channel_already_declared_is_neither_repaired_nor_gating(leerie):
    plans = _plans(("testing", [_sub("test-001", depends_on=["feat-001"]),
                                _sub("feat-001")]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "feat-001")])
    assert not repairs and not unrepaired
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["depends_on"] == ["feat-001"], "no duplicate edge"


def test_id_channel_respects_the_cycle_guard(leerie):
    """feat-001 already depends on test-001, so test-001 -> feat-001 closes
    a cycle and must be refused exactly like the tag channel's."""
    plans = _plans(("testing", [_sub("test-001"),
                                _sub("feat-001", depends_on=["test-001"])]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "feat-001")])
    assert not repairs and len(unrepaired) == 1
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["depends_on"] == [], "edge must not be applied"


def test_tag_channel_wins_when_the_value_is_both_a_tag_and_an_id(leerie):
    """Ordering is deliberate: the tag channel is tried first so pre-existing
    behavior is bit-for-bit unchanged."""
    plans = _plans(("testing", [
        _sub("test-001"),
        _sub("feat-001"),                      # id collides with the tag name
        _sub("feat-002", provides=["feat-001"]),
    ]))
    repairs, _unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "feat-001")])
    assert [r["channel"] for r in repairs] == ["tag"]
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["depends_on"] == []
    assert by_id["test-001"]["requires"] == [
        {"tag": "feat-001", "extent": "in_plan"}]


# --------------------------------------------------------------------- #
# Channel (c): single-cluster fan-out. Several providers that are all
# sub-file region splits of ONE file are not a real ambiguity.
# --------------------------------------------------------------------- #

def test_single_cluster_fanout_repairs(leerie):
    plans = _plans(
        ("testing", [_sub("test-001")]),
        ("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], cluster="feat-001"),
            _sub("feat-001-r2", provides=["baked"], cluster="feat-001"),
            _sub("feat-001-r3", provides=["baked"], cluster="feat-001"),
        ]),
    )
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "baked")])
    assert not unrepaired
    assert repairs == [{"sid": "test-001", "tag": "baked",
                        "provider": "feat-001-r1",
                        "channel": "cofile_cluster"}]
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["test-001"]["requires"] == [
        {"tag": "baked", "extent": "in_plan"}]


def test_single_cluster_fanout_orders_behind_every_member(leerie):
    """`provider` records only the lex-smallest member, but the tag edge must
    order the consumer behind ALL of them — that is the whole point."""
    plans = _plans(
        ("testing", [_sub("test-001")]),
        ("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], cluster="feat-001"),
            _sub("feat-001-r2", provides=["baked"], cluster="feat-001"),
            _sub("feat-001-r3", provides=["baked"], cluster="feat-001"),
        ]),
    )
    leerie._repair_missing_requires(plans, [_defect("test-001", "baked")])
    _subtasks, waves = leerie._schedule(copy.deepcopy(plans))
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    for member in ("feat-001-r1", "feat-001-r2", "feat-001-r3"):
        assert pos[member] < pos["test-001"]


def test_providers_spanning_two_clusters_still_decline(leerie):
    """The exclusion is 'all one cluster', not 'any cluster' — two genuinely
    different split files are still an ambiguity only a human can resolve."""
    plans = _plans(
        ("testing", [_sub("test-001")]),
        ("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], cluster="feat-001"),
            _sub("feat-002-r1", provides=["baked"], cluster="feat-002"),
        ]),
    )
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "baked")])
    assert not repairs and len(unrepaired) == 1


def test_providers_with_no_cluster_marker_still_decline(leerie):
    """A `None` cluster is the absence of the marker, never a cluster that
    several unrelated subtasks share."""
    plans = _plans(
        ("testing", [_sub("test-001")]),
        ("feature-implementation", [_sub("feat-001", provides=["baked"]),
                                    _sub("feat-002", provides=["baked"])]),
    )
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("test-001", "baked")])
    assert not repairs and len(unrepaired) == 1


def test_cluster_channel_declines_when_sid_is_itself_a_member(leerie):
    """A split sibling requiring its own cluster's tag is a self-loop."""
    plans = _plans(("feature-implementation", [
        _sub("feat-001-r1", provides=["baked"], cluster="feat-001"),
        _sub("feat-001-r2", provides=["baked"], cluster="feat-001"),
    ]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("feat-001-r1", "baked")])
    assert not repairs and len(unrepaired) == 1


def test_empty_tag_or_dep_declines(leerie):
    """`SCHEMAS["wiring_judge"]` puts no `minLength` on `tag_or_dep`, so `""`
    is schema-valid and reaches this function."""
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-005", "")])
    assert not repairs and len(unrepaired) == 1


def test_empty_tag_never_matches_an_empty_provides_entry(leerie):
    """The shape that makes the above reachable rather than academic: a
    subtask declaring `provides: [""]` is a sole "provider" of the empty tag,
    so without the guard the tag channel matches and the gate synthesizes a
    `{"tag": ""}` edge — a meaningless dependency inside a correctness gate."""
    plans = _plans(("refactoring", [
        _sub("refactor-001"),
        _sub("refactor-002", provides=[""]),
    ]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-001", "")])
    assert not repairs and len(unrepaired) == 1
    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    assert by_id["refactor-001"]["requires"] == [], "no empty-tag edge"


def test_non_missing_requires_kind_declines(leerie):
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-005", "tests-a", kind="broken_by_drop")])
    assert not repairs and len(unrepaired) == 1


def test_unknown_sid_declines(leerie):
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("does-not-exist", "tests-a")])
    assert not repairs and len(unrepaired) == 1


def test_self_provider_declines(leerie):
    """A subtask cannot require what it itself provides — that is a graph
    self-loop, not a dependency."""
    plans = _plans(("refactoring", [_sub("refactor-001", provides=["own"]),
                                    _sub("refactor-002")]))
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-001", "own")])
    assert not repairs and len(unrepaired) == 1


def test_already_declared_is_neither_repaired_nor_gating(leerie):
    """The judge is wrong about this one; a duplicate edge would be worse
    than ignoring it, and dying over an edge that already exists is absurd."""
    plans = _incident_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("refactor-005", "worker")])
    assert not repairs and not unrepaired


# --------------------------------------------------------------------- #
# The cycle guard — the load-bearing one
# --------------------------------------------------------------------- #

def _cycle_plan():
    """b already depends on a, so wiring a -> requires(b's tag) closes a
    cycle. This is the shape a judge false-positive can produce."""
    return _plans(("d", [
        _sub("a-001", provides=["from-a"]),
        _sub("b-001", provides=["from-b"], requires=["from-a"]),
    ]))


def test_cycling_edge_is_skipped_not_applied(leerie):
    plans = _cycle_plan()
    repairs, unrepaired = leerie._repair_missing_requires(
        plans, [_defect("a-001", "from-b")])
    assert not repairs, "a cycle-closing edge must never be applied"
    assert len(unrepaired) == 1, "it must fall through to the gate's die()"
    tags = {e["tag"] for p in plans for s in p["subtasks"]
            if s["id"] == "a-001" for e in s["requires"]}
    assert "from-b" not in tags, "plans must be left unmutated"


def test_plan_still_schedules_after_a_skipped_cycle(leerie):
    """The proof that skipping matters: applying the edge would make
    _schedule() die, so an unguarded repair kills an otherwise-live run."""
    plans = _cycle_plan()
    leerie._repair_missing_requires(plans, [_defect("a-001", "from-b")])
    subtasks, waves = leerie._schedule(plans)
    assert len(subtasks) == 2
    forced = _cycle_plan()
    leerie._add_requires_edge(forced, "a-001", "from-b")
    with pytest.raises(SystemExit):
        leerie._schedule(forced)


def test_cycle_trials_are_cumulative(leerie):
    """Two individually-safe edges must not combine into a cycle: each
    trial runs against the plan as already mutated by the ones before it."""
    plans = _plans(("d", [
        _sub("a-001", provides=["from-a"]),
        _sub("b-001", provides=["from-b"]),
        _sub("c-001", provides=["from-c"], requires=["from-a"]),
    ]))
    repairs, _ = leerie._repair_missing_requires(
        plans, [_defect("a-001", "from-b"), _defect("b-001", "from-c")])
    subtasks, _waves = leerie._schedule(plans)
    assert len(subtasks) == 3, "the applied set must leave an acyclic graph"
    assert len(repairs) <= 2


# --------------------------------------------------------------------- #
# Defect filtering shared with the gate
# --------------------------------------------------------------------- #

def test_latent_risk_is_not_repaired_or_gated(leerie):
    jr = {"wiring_defects": [
        _defect("refactor-005", "tests-a", severity="latent_risk")]}
    assert leerie._live_wiring_defects(jr) == []


@pytest.mark.parametrize("missing", ["concrete_reason", "tag_or_dep"])
def test_anti_gaming_fields_required(leerie, missing):
    d = _defect("refactor-005", "tests-a")
    d[missing] = "  "
    assert leerie._live_wiring_defects({"wiring_defects": [d]}) == []


def test_non_dict_defect_ignored(leerie):
    assert leerie._live_wiring_defects({"wiring_defects": ["oops", None]}) == []


# --------------------------------------------------------------------- #
# Wiring into the phase + the caller
# --------------------------------------------------------------------- #

def test_gate_repairs_then_passes_and_records(leerie, monkeypatch, tmp_path):
    import asyncio

    plans = _incident_plan()

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": INCIDENT_DEFECTS}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self):
            # `_judgment_cwd` reads this; judgment workers run in a
            # disposable worktree (DESIGN §12), never the checkout.
            self.data = {"planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    st = _St()
    caps = dict(leerie.DEFAULT_CAPS)
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, caps, {}, {}))
    assert out is plans
    assert [r["sid"] for r in st.data["wiring_gate"]["repairs"]] == \
        ["refactor-005", "refactor-005"]
    subtasks, waves = leerie._schedule(plans)
    pos = {sid: i for i, w in enumerate(waves) for sid in w}
    assert pos["test-002"] < pos["refactor-005"]


def test_gate_still_dies_on_unrepairable(leerie, monkeypatch):
    import asyncio

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": [_defect("refactor-005", "nobody")]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self):
            # `_judgment_cwd` reads this; judgment workers run in a
            # disposable worktree (DESIGN §12), never the checkout.
            self.data = {"planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_wiring_gate(
            _incident_plan(), "task", _St(), dict(leerie.DEFAULT_CAPS), {}, {}))


def test_gate_logs_the_id_channel_repair(leerie, monkeypatch, capsys):
    """End-to-end through phase_wiring_gate: a defect whose tag_or_dep names a
    surviving subtask id repairs via the id channel and the gate logs the
    'named subtask id' line distinct from the tag-channel wording."""
    import asyncio

    plans = _plans(("testing", [_sub("test-001"), _sub("feat-001")]))

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": [_defect("test-001", "feat-001")]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self):
            # `_judgment_cwd` reads this; judgment workers run in a
            # disposable worktree (DESIGN §12), never the checkout.
            self.data = {"planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    st = _St()
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert out is plans
    assert [r["channel"] for r in st.data["wiring_gate"]["repairs"]] == ["id"]
    captured = capsys.readouterr()
    assert "named subtask id" in captured.out


def test_gate_logs_the_cofile_cluster_repair(leerie, monkeypatch, capsys):
    """End-to-end through phase_wiring_gate: a defect whose tag has several
    providers that are all one `_cofile_cluster` repairs via that channel and
    the gate logs the 'sub-file cluster of' line."""
    import asyncio

    plans = _plans(
        ("testing", [_sub("test-001")]),
        ("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], cluster="feat-001"),
            _sub("feat-001-r2", provides=["baked"], cluster="feat-001"),
        ]),
    )

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": [_defect("test-001", "baked")]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self):
            # `_judgment_cwd` reads this; judgment workers run in a
            # disposable worktree (DESIGN §12), never the checkout.
            self.data = {"planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    st = _St()
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert out is plans
    assert [r["channel"] for r in st.data["wiring_gate"]["repairs"]] == \
        ["cofile_cluster"]
    captured = capsys.readouterr()
    assert "sub-file cluster of" in captured.out


def test_gate_logs_a_provably_false_finding_as_discarded(leerie, monkeypatch,
                                                          capsys):
    """A defect naming a tag the plan still provides is provably false (the
    finding's own premise contradicts the plan) — the gate must log it as
    discarded and never gate or repair on it."""
    import asyncio

    plans = _plans(("testing", [_sub("test-001", provides=["still-here"])]))

    async def fake_claude_p(**kw):
        return {"plan_reviewed": True, "rationale": "r",
                "wiring_defects": [{
                    "kind": "broken_by_merge", "sid": "test-001",
                    "tag_or_dep": "still-here",
                    "concrete_reason": "a merge severed this capability",
                    "severity": "live_defect",
                }]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    class _St:
        def __init__(self):
            self.data = {"dropped_subtasks": {},
                         "planning_worktree": "/tmp/leerie-test-wt"}
        def save(self): pass
        def bump_workers(self, caps): pass

    st = _St()
    out = asyncio.run(leerie.phase_wiring_gate(
        plans, "task", st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert out is plans
    assert st.data["wiring_gate"]["repairs"] == []
    captured = capsys.readouterr()
    assert "discarded provably-false finding" in captured.out


def test_caller_reschedules_when_repairs_land(leerie):
    """Source-coupling pin: the added edges change the wave partition, so
    `_run_phases` must re-derive subtasks/waves and rewrite plan_snapshot —
    otherwise the budget preflight, check_plan_wiring, _validate_plan and
    _write_plan all operate on the pre-repair _schedule."""
    import inspect
    src = inspect.getsource(leerie._run_phases)
    gate = src.index("await phase_wiring_gate(")
    tail = src[gate:gate + 1200]
    assert '"repairs"' in tail, (
        "_run_phases must branch on the gate's repairs list")
    assert "_schedule(plans)" in tail, "it must re-run _schedule() after a repair"
    assert 'st.data["plan_snapshot"]' in tail, (
        "and rewrite plan_snapshot so a later resume rehydrates the repaired "
        "wave partition")


def test_gate_audit_key_written_only_on_a_clean_pass(leerie):
    """`wiring_gate` is the resume skip condition, so a run the gate killed
    must not carry it."""
    import inspect
    src = inspect.getsource(leerie.phase_wiring_gate)
    die_at = src.index("plan-wiring gate found unresolved")
    write_at = src.index('st.data["wiring_gate"] =')
    assert die_at < write_at, (
        "the die() must precede the audit write, so a failing gate leaves "
        "no key behind for resume to skip on")


# ---------------------------------------------------------------------------
# Re-evaluation of survivors against the POST-repair graph
# ---------------------------------------------------------------------------
#
# The per-defect checks in `_repair_missing_requires` are CHANNEL-LOCAL: the id
# arm asks `tag in depends_on` (where `tag_or_dep` is itself a sid), the tag arm
# asks `tag in declared`. Neither asks the question that decides whether the
# defect is still real — "is `sid` already ordered behind a provider of `tag` by
# ANY means?"
#
# That gap killed run 05fdffb8 (navegando) after ~97 workers. The judge emitted
# TWO defects for one subtask; the id-channel one repaired and added the edge,
# and the tag-channel one — naming a tag with two providers, so matching no
# channel — fell through to `unrepaired`:
#
#   wiring-gate: repaired test-003 -> depends_on 'feat-007-2-2' (named subtask id)
#   • WIRING_DEFECT (missing_requires) test-003 / action-echoed-row-payload
#
# By then the survivor's stated failure ("the scheduler can start test-003
# before feat-007-2-2") was already impossible. **leerie repaired the problem
# and then died on it**, on a gate with no bypass flag.
#
# Emission order is arbitrary, so this cannot be a pre-filter — the tag defect
# may be seen before the id defect lands its edge. The re-check must run after
# every repair is applied.


def _plan(subtasks: list[dict]) -> list[dict]:
    return [{"subtasks": subtasks}]


class TestSurvivorsRecheckedAfterRepairs:
    """Regression lock for the run-05fdffb8 shape."""

    def test_the_run_05fdffb8_shape_is_dismissed(self, leerie):
        """THE REGRESSION, reproduced from the real run's `plan_snapshot`.

        `test-003` DID declare `requires: action-echoed-row-payload` — the
        very tag the judge reported it as missing. Requiring an in-plan tag
        orders a subtask behind EVERY provider of it, so the judge's stated
        failure ("the scheduler can start test-003 before feat-007-2-2")
        was false on the plan as written.

        It died anyway because the existing already-declared guard sits
        DOWNSTREAM of channel selection: two providers spanning clusters
        match no channel, so the `else: unrepaired; continue` arm fires and
        `tag in declared` — computed three lines earlier — is never reached.
        The guard existed and was structurally dead on the one path that
        reaches the `die()`.
        """
        plans = _plan([
            {"id": "feat-007-2-2", "provides": ["action-echoed-row-payload"],
             "requires": [], "depends_on": []},
            {"id": "bugfix-010", "provides": ["action-echoed-row-payload"],
             "requires": [], "depends_on": []},
            {"id": "test-003", "provides": [],
             "requires": [{"tag": "action-echoed-row-payload",
                           "extent": "in_plan", "reason": ""}],
             "depends_on": []},
        ])
        defects = [{"kind": "missing_requires", "sid": "test-003",
                    "tag_or_dep": "action-echoed-row-payload",
                    "concrete_reason": "two providers, no channel matches"}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert unrepaired == [], (
            "test-003 requires the tag, which orders it behind BOTH "
            f"providers; still reported: {unrepaired}")

    def test_ordered_via_an_in_plan_requires_tag_is_also_dismissed(
            self, leerie):
        """Ordering comes from BOTH channels, not just `depends_on`.

        `_build_predecessor_graph` makes every provider of an
        `extent: in_plan` requires-tag a predecessor. Across the repair
        corpus, 99 of 535 direct orderings (19%) exist ONLY through that
        channel — so a `depends_on`-only re-check goes on killing runs whose
        ordering came through tags, which is the very class of bug the
        re-check exists to stop.
        """
        plans = _plan([
            # both producers of `shared-tag` also provide `schema-v2`
            {"id": "p1", "provides": ["shared-tag", "schema-v2"],
             "requires": [], "depends_on": []},
            {"id": "p2", "provides": ["shared-tag", "schema-v2"],
             "requires": [], "depends_on": []},
            # ordered behind BOTH by TAG, with no depends_on anywhere.
            {"id": "t", "provides": [],
             "requires": [{"tag": "schema-v2", "extent": "in_plan",
                           "reason": ""}],
             "depends_on": []},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "shared-tag", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert unrepaired == [], (
            "t is already scheduled after p1 and p2 via requires:schema-v2, "
            "so the defect's stated failure cannot occur; still reported: "
            f"{unrepaired}")

    def _two_repairs_then_a_survivor(self):
        """A shape whose survivor is refutable ONLY after both repairs land.

        `shared-tag` has two providers in different clusters, so its defect
        matches no repair channel. Two id-channel defects each add one
        `depends_on`; only once BOTH have landed is `t` ordered behind every
        provider. This is what makes the re-check a second pass rather than a
        pre-filter — unlike the real run-05fdffb8 shape, which was already
        refutable on the plan as written.
        """
        base = [
            {"id": "p1", "provides": ["shared-tag"], "requires": [],
             "depends_on": []},
            {"id": "p2", "provides": ["shared-tag"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [], "requires": [], "depends_on": []},
        ]
        survivor = {"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "shared-tag", "concrete_reason": ""}
        repairs = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": p, "concrete_reason": ""} for p in
                   ("p1", "p2")]
        return base, survivor, repairs

    def test_survivor_is_dismissed_once_both_repairs_land(self, leerie):
        base, survivor, repairs = self._two_repairs_then_a_survivor()
        _, unrepaired = leerie._repair_missing_requires(
            _plan(base), repairs + [survivor])
        assert unrepaired == []

    def test_order_independent(self, leerie):
        """Judge emission order is arbitrary — the survivor may come FIRST.

        A pre-filter would see `t` with no edges at all and keep the defect,
        killing the run. Only a post-repair pass can dismiss it.
        """
        base, survivor, repairs = self._two_repairs_then_a_survivor()
        _, unrepaired = leerie._repair_missing_requires(
            _plan(base), [survivor] + repairs)
        assert unrepaired == [], (
            "the survivor was emitted before the repairs that moot it — this "
            "is precisely why the re-check cannot be a pre-filter")


class TestItStillGatesGenuineDefects:
    """ANTI-VACUITY. A filter that dismisses everything is worse than none."""

    def test_no_edge_to_any_provider_still_gates(self, leerie):
        """The `docs-001` shape: requires tag A, the real producer provides
        tag B, and there is no edge between them."""
        plans = _plan([
            {"id": "bugfix-009", "provides": ["create-lead-ryow-fix"],
             "requires": [], "depends_on": []},
            {"id": "feat-009", "provides": ["create-lead-optimistic-insert"],
             "requires": [], "depends_on": []},
            {"id": "docs-001", "provides": [],
             "requires": [{"tag": "create-lead-optimistic-insert",
                           "extent": "in_plan", "reason": ""}],
             "depends_on": []},
        ])
        # single provider -> the tag channel repairs it; force the survivor path
        # by naming a tag with no provider at all.
        defects = [{"kind": "missing_requires", "sid": "docs-001",
                    "tag_or_dep": "nonexistent-capability",
                    "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["docs-001"]

    def test_depends_on_a_NON_provider_still_gates(self, leerie):
        """An edge to some unrelated subtask must not launder the defect."""
        plans = _plan([
            {"id": "real-producer", "provides": ["cap-x", "cap-y"],
             "requires": [], "depends_on": []},
            {"id": "other", "provides": ["cap-y"], "requires": [], "depends_on": []},
            {"id": "unrelated", "provides": [], "requires": [], "depends_on": []},
            {"id": "t", "provides": [], "requires": [], "depends_on": ["unrelated"]},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "cap-y", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"], (
            "depends_on=['unrelated'] does not order t behind a provider of "
            "cap-y, so the defect is still live")

    def _drop_shape(self, kind):
        """A `broken_by_*` whose `tag_or_dep` names a PRECEDING subtask id.

        Reaches the re-check because `_repair_missing_requires` routes every
        non-repairable defect to its residual, and slips past the upstream
        `_filter_provably_false_wiring_defects` because that predicate fires
        only when the named *capability* is still provided — a subtask id is
        not a tag.
        """
        plans = _plan([
            {"id": "p1", "provides": ["cap"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [], "requires": [], "depends_on": ["p1"]},
        ])
        return plans, [{"kind": kind, "sid": "t", "tag_or_dep": "p1",
                        "concrete_reason": "the dropped work is gone"}]

    def test_broken_by_drop_is_not_dismissed_on_ordering(self, leerie):
        """Ordering cannot refute a "the work is gone" finding.

        Being scheduled after `p1` does nothing if `p1` no longer provides
        the capability — so an ordering test must not speak to this kind at
        all. The re-check is scoped to `missing_requires` for that reason.
        """
        plans, defects = self._drop_shape("broken_by_drop")
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"]

    def test_broken_by_merge_is_not_dismissed_on_ordering(self, leerie):
        plans, defects = self._drop_shape("broken_by_merge")
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"]

    def test_the_same_shape_as_missing_requires_IS_dismissed(self, leerie):
        """Positive control for the two above.

        Byte-identical but for `kind`, so the scope guard cannot pass by
        having disabled the re-check wholesale.
        """
        plans, defects = self._drop_shape("missing_requires")
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert unrepaired == []

    def test_ordered_behind_only_SOME_producers_still_gates(self, leerie):
        """The soundness control: "every producer", never "any producer".

        A capability with two producers, where the subtask precedes only the
        first, is exactly the judge's complaint about the second — `t` can
        still start before `p2`. Dismissing on a non-empty intersection would
        wave through the race the gate exists to catch, which is strictly
        worse than the over-gating this whole re-check was written to fix.
        """
        plans = _plan([
            {"id": "p1", "provides": ["cap"], "requires": [], "depends_on": []},
            {"id": "p2", "provides": ["cap"], "requires": [], "depends_on": []},
            {"id": "t", "provides": [], "requires": [], "depends_on": ["p1"]},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "cap", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"], (
            "t precedes p1 but NOT p2, so the race against p2 is real")

    def test_a_capability_nothing_provides_still_gates(self, leerie):
        """The empty-set guard.

        `set() <= anything` is vacuously True, so an unguarded subset test
        would dismiss every defect naming a capability no subtask provides —
        the canonical TRUE finding (the plan lacks the work, not the edge).
        """
        plans = _plan([
            {"id": "a", "provides": ["other"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [], "requires": [], "depends_on": ["a"]},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "nobody-provides-this",
                    "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"]

    def test_requires_with_a_NON_in_plan_extent_still_gates(self, leerie):
        """The sharp control on the requires channel.

        `_build_predecessor_graph` skips any `requires` entry whose `extent`
        is not `in_plan` — an `external` entry is out-of-graph by planner
        declaration and creates NO edge, so it cannot refute the finding.
        This is the case that separates "resolved ordering through the real
        helper" from "loosely scanned the requires array": the shape is
        byte-identical to the dismissed test above but for `extent`.
        """
        plans = _plan([
            {"id": "p1", "provides": ["shared-tag", "schema-v2"],
             "requires": [], "depends_on": []},
            {"id": "p2", "provides": ["shared-tag"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [],
             "requires": [{"tag": "schema-v2", "extent": "external",
                           "reason": ""}],
             "depends_on": []},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "shared-tag", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"], (
            "an external requires entry creates no scheduling edge, so t is "
            "NOT ordered behind p1 and the defect is still live")

    def test_ordering_that_holds_only_TRANSITIVELY_still_gates(self, leerie):
        """Pins the deliberate direct-edges-only scope (not an accident).

        A transitive ancestor would refute the finding just as soundly, but
        dismissing on it is a much broader claim to make on a die-only gate,
        so the check stays 1:1 with `_build_predecessor_graph`'s own direct
        edge definition. Change that decision and this test should be
        updated deliberately, not silently.
        """
        plans = _plan([
            {"id": "producer", "provides": ["cap-x"], "requires": [],
             "depends_on": []},
            {"id": "other", "provides": ["cap-x"], "requires": [],
             "depends_on": []},
            {"id": "middle", "provides": [], "requires": [],
             "depends_on": ["producer"]},
            {"id": "t", "provides": [], "requires": [],
             "depends_on": ["middle"]},   # after producer, but only via middle
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "cap-x", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["t"]

    def test_unknown_sid_fails_closed(self, leerie):
        plans = _plan([{"id": "a", "provides": ["cap"], "requires": [],
                        "depends_on": []}])
        defects = [{"kind": "missing_requires", "sid": "ghost",
                    "tag_or_dep": "cap", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert [u["sid"] for u in unrepaired] == ["ghost"]


class TestDismissalIsVisible:
    """A silent dismissal would hide the judge degrading over time."""

    def test_the_recheck_logs_which_edge_made_it_moot(self, leerie, capsys):
        plans = _plan([
            {"id": "p1", "provides": ["shared"], "requires": [],
             "depends_on": []},
            {"id": "p2", "provides": ["shared"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [], "requires": [],
             "depends_on": ["p1", "p2"]},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "shared", "concrete_reason": ""}]
        _, unrepaired = leerie._repair_missing_requires(plans, defects)
        assert unrepaired == []
        # ONE readouterr() call: it resets the capture, so a second returns
        # empty and the `.err` term was dead. Same fix as
        # tests/test_disk_preflight.py's helper tests.
        _captured = capsys.readouterr()
        out = _captured.out + _captured.err
        assert "already ordered behind" in out, (
            "the dismissal must be logged, naming the edges responsible")
        # every producer that justified the dismissal is named, not just one —
        # a one-name log would hide which edge is load-bearing on review.
        assert "p1" in out and "p2" in out
        # NOT the per-channel message: two of this pass's three dismissal
        # shapes are not an edge the subtask declares (one is an edge a
        # sibling's repair just added, the other an in-plan requires tag), so
        # borrowing that wording would state the wrong reason on a gate whose
        # remaining value is that dismissals stay reviewable.
        assert "named an edge the subtask already declares" not in out

    def test_a_genuine_already_declared_skip_keeps_its_own_message(
            self, leerie, capsys):
        """The per-channel message must survive — the two are distinct claims.

        Here `t` really does declare the edge the defect names, and it is
        caught by the channel-local guard, never reaching the re-check.
        """
        plans = _plan([
            {"id": "p1", "provides": ["solo"], "requires": [],
             "depends_on": []},
            {"id": "t", "provides": [],
             "requires": [{"tag": "solo", "extent": "in_plan", "reason": ""}],
             "depends_on": []},
        ])
        defects = [{"kind": "missing_requires", "sid": "t",
                    "tag_or_dep": "solo", "concrete_reason": ""}]
        leerie._repair_missing_requires(plans, defects)
        out = capsys.readouterr().out
        assert "named an edge the subtask already declares" in out


class TestFilterDefectsAlreadyOrderedEmptyInput:
    """`_filter_defects_already_ordered`'s early return on an empty defects
    list — the residual pass is a no-op on a fully-repaired plan, and it
    must not build the predecessor graph or scan an empty defect list to
    prove that."""

    def test_empty_defects_returns_empty_lists_unchanged(self, leerie):
        plans = _plan([
            {"id": "p1", "provides": ["solo"], "requires": [],
             "depends_on": []},
        ])
        surviving, notes = leerie._filter_defects_already_ordered(plans, [])
        assert surviving == []
        assert notes == []
