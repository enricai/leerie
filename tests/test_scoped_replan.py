"""Scoped re-plan and unresolvable recovery (DESIGN §5).

Two changes tested together because the second depends on the first.

**`_replan_domain_closure`.** A re-plan replaces a domain's subtasks with fresh
ones, so every id it used vanishes and any other domain holding an edge into it
dangles. `validate_plan` catches that — as a `die()`, which is the outcome
scoping exists to avoid. So the scope is the targets plus the transitive
closure of domains depending on them, over both the id (`depends_on`) and tag
(`requires`/`provides`) channels.

Measured across every multi-domain plan in the corpus, 85 (domain, plan)
re-plan simulations: closure-scoped yields a plan that schedules and validates
**85/85**, naive single-domain **79/85**. The 6 failures are exactly the
dangling-edge hazard, so the closure is necessary, not defensive.

**Unresolvable recovery.** An unresolvable collision is a verdict about the
PLAN, yet it re-drove nothing and `die()`d terminally ("this run cannot be
resumed") after the full planning spend. 5 of 43 corpus runs that reached the
judge died there (12%), burning 404 workers, while the judge resolved the other
95.5% of collisions fine. Unlike the coverage and adherence judges — whose
findings carry no subtask reference — a collision names `a_sid`/`b_sid`, so the
implicated domains are mechanically derivable and the re-plan can be scoped.
"""
from __future__ import annotations

import pytest


def _sub(sid, deps=None, requires=None, provides=None):
    return {
        "id": sid, "title": f"t {sid}", "intent": "do", "scope_note": "s",
        "files_likely_touched": [], "depends_on": list(deps or []),
        "requires": [{"tag": t, "extent": "in_plan"} for t in (requires or [])],
        "provides": list(provides or []), "success_criteria_seed": "done",
        "size": "small", "investigation_notes": "",
    }


def _plan(domain, subs):
    return {"domain": domain, "status": "ready", "subtasks": subs}


# ----- _replan_domain_closure ------------------------------------------------

def test_isolated_domain_closes_to_itself(leerie):
    """85% of real cases: nothing depends on the target."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001")]),
             _plan("testing", [_sub("test-001")])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {"bugfix"}


def test_id_channel_dependent_is_pulled_in(leerie):
    """`test-001` depends_on `bugfix-001`; re-planning bugfix alone would
    dangle that edge."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001")]),
             _plan("testing", [_sub("test-001", deps=["bugfix-001"])])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {"bugfix", "test"}


def test_tag_channel_dependent_is_pulled_in(leerie):
    """The tag channel matters as much as the id channel: `test-001` requires
    a capability only bugfix provides."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001", provides=["api"])]),
             _plan("testing", [_sub("test-001", requires=["api"])])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {"bugfix", "test"}


def test_closure_is_transitive(leerie):
    plans = [_plan("bug-fixing", [_sub("bugfix-001", provides=["a"])]),
             _plan("feature-implementation",
                   [_sub("feat-001", requires=["a"], provides=["b"])]),
             _plan("testing", [_sub("test-001", requires=["b"])])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {
        "bugfix", "feat", "test"}


def test_direction_matters_producers_are_not_pulled_in(leerie):
    """Re-planning a CONSUMER does not require re-planning its producer —
    the producer's ids survive, so nothing dangles. Pulling it in anyway
    would inflate every scope back toward a full re-plan."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001", provides=["api"])]),
             _plan("testing", [_sub("test-001", requires=["api"])])]
    assert leerie._replan_domain_closure(plans, {"test"}) == {"test"}


def test_intra_domain_edges_are_ignored(leerie):
    plans = [_plan("bug-fixing", [_sub("bugfix-001", provides=["a"]),
                                  _sub("bugfix-002", requires=["a"],
                                       deps=["bugfix-001"])])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {"bugfix"}


def test_multiple_targets_and_empty_inputs(leerie):
    plans = [_plan("bug-fixing", [_sub("bugfix-001")]),
             _plan("testing", [_sub("test-001")])]
    assert leerie._replan_domain_closure(plans, {"bugfix", "test"}) == {
        "bugfix", "test"}
    assert leerie._replan_domain_closure(plans, set()) == set()
    assert leerie._replan_domain_closure([], {"bugfix"}) == {"bugfix"}


def test_unresolvable_tag_does_not_invent_an_edge(leerie):
    """A `requires` nothing provides yields no producer, so no edge — it must
    not silently widen the scope."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001")]),
             _plan("testing", [_sub("test-001", requires=["nope"])])]
    assert leerie._replan_domain_closure(plans, {"bugfix"}) == {"bugfix"}


# ----- the property the corpus sweep measured -------------------------------

def test_closure_scope_leaves_no_dangling_edge(leerie):
    """The guarantee, stated directly: after re-planning the closure, every
    surviving cross-domain edge points at a domain that was NOT re-planned
    (so its ids are intact). This is why the coherence question is vacuous
    rather than merely checked."""
    plans = [_plan("bug-fixing", [_sub("bugfix-001", provides=["a"])]),
             _plan("feature-implementation",
                   [_sub("feat-001", requires=["a"])]),
             _plan("testing", [_sub("test-001", deps=["feat-001"])])]
    scope = leerie._replan_domain_closure(plans, {"bugfix"})
    for p in plans:
        for s in p["subtasks"]:
            dom = s["id"].split("-", 1)[0]
            if dom in scope:
                continue                      # re-planned; its edges are rebuilt
            for dep in s["depends_on"]:
                assert dep.split("-", 1)[0] not in scope, (
                    f"{s['id']} would dangle on {dep}")


# ----- phase_plan domains parameter -----------------------------------------

def test_phase_plan_accepts_a_domains_scope(leerie):
    import inspect
    sig = inspect.signature(leerie.phase_plan)
    assert "domains" in sig.parameters
    assert sig.parameters["domains"].default is None, (
        "domains must default to None so every existing caller is unaffected")


def test_phase_plan_filters_categories_by_prefix(leerie):
    """The closure returns id PREFIXES (`bugfix`), while `st.data['categories']`
    holds category NAMES (`bug-fixing`). The filter must map between them or it
    silently selects nothing."""
    import inspect
    src = inspect.getsource(leerie.phase_plan)
    assert "CATEGORY_ABBREV.get(c, c) in domains" in src, (
        "categories must be matched on their abbreviation, not their name")
    assert "scoped re-plan selected no categories" in src, (
        "a scope that matches nothing must die loudly, not plan zero domains")


# ----- unresolvable recovery ------------------------------------------------

def test_gate_replans_instead_of_dying(leerie):
    """Source-coupling: the terminal die is replaced by a scoped re-plan, and
    the old 'cannot be resumed' wording is gone from that path."""
    import inspect
    src = inspect.getsource(leerie.phase_overlap_judge)
    assert "_replan_domain_closure(" in src
    assert "domains=scope" in src
    # Comments are stripped first: the code *explains* the old terminal
    # behaviour in a comment, and matching that would pass while the die
    # itself was still there — the trap CLAUDE.md documents for docstrings
    # in the zombie-reaper guard.
    code = "\n".join(ln for ln in src.splitlines()
                     if not ln.lstrip().startswith("#"))
    idx = code.index("unresolvable = [c for c in collisions")
    tail = code[idx:]
    assert "this run cannot be resumed" not in tail, (
        "the first unresolvable verdict must no longer be terminal")
    # The bounded second verdict is still allowed to die, and must say so.
    assert "after a scoped re-plan" in tail, (
        "the exhausted-recovery die must explain that a re-plan was tried")


def test_recovery_is_bounded_to_one_attempt(leerie):
    """A second unresolvable verdict after re-planning must die — the
    contradiction is then not something re-planning resolves, and an unbounded
    loop would burn the budget the recovery exists to protect."""
    import inspect
    src = inspect.getsource(leerie.phase_overlap_judge)
    assert 'st.data.get("overlap_replan_done")' in src
    assert 'st.data["overlap_replan_done"] = True' in src
    assert src.index('st.data.get("overlap_replan_done")') < src.index(
        "_replan_domain_closure("), (
        "the bound must be checked BEFORE spending on a re-plan")


def test_recovery_preflights_the_budget(leerie):
    """A re-plan here is subject to the same budget preflight as the other
    gates' — it is the same kind of spend."""
    import inspect
    src = inspect.getsource(leerie.phase_overlap_judge)
    assert 'check_replan_affordable(st, caps, "overlap judge", plans)' in src


def test_recovery_reconciles_before_re_judging(leerie):
    """A re-plan invalidates every phase upstream of this one (DESIGN §5), and
    fresh plans can reintroduce cross-domain tag drift."""
    import inspect
    src = inspect.getsource(leerie.phase_overlap_judge)
    assert "await phase_reconcile(" in src
    assert src.index("await phase_plan(") < src.index("await phase_reconcile(")


def test_state_field_is_registered(leerie):
    """Guard-the-guard: an unregistered `st.data` write fails the parity
    sweep, so pin the field by name here too."""
    assert "overlap_replan_done" in leerie.STATE_FIELDS


def test_first_unresolvable_verdict_replans_rather_than_dies(
        leerie, monkeypatch):
    """Behavioural, not source-coupled. The source guards above would pass
    against a re-plan that never actually runs — the inert-fix failure mode
    this session already hit twice. Drives the real `phase_overlap_judge`.

    The judge returns `unresolvable` on the first pass and a clean verdict on
    the second, so the phase must re-plan exactly once and then return a plan
    instead of exiting."""
    import asyncio

    plans = [
        _plan("feature-implementation", [_sub("feat-001", provides=["x"])]),
        _plan("bug-fixing", [_sub("bugfix-001", requires=["x"])]),
    ]

    class _St:
        def __init__(self):
            self.data = {}
        def save(self):
            pass

    verdicts = iter([
        {"collisions": [{"a_sid": "feat-001", "b_sid": "bugfix-001",
                         "artifact": "x.ts", "resolution": "unresolvable",
                         "reason": "contradictory designs"}]},
        {"collisions": []},
    ])
    replans: list[set[str]] = []

    async def _fake_loop(**kw):
        return (next(verdicts), [])

    async def _fake_plan(task, st, caps, models, efforts,
                         replan_round=0, domains=None):
        replans.append(set(domains or ()))
        return [_plan("feature-implementation", [_sub("feat-900")]),
                _plan("bug-fixing", [_sub("bugfix-900")])]

    async def _fake_reconcile(p, *a, **k):
        return p

    monkeypatch.setattr(leerie, "_run_checked_loop", _fake_loop)
    monkeypatch.setattr(leerie, "check_overlap_judge_output",
                        lambda *a, **k: [])
    monkeypatch.setattr(leerie, "phase_plan", _fake_plan)
    monkeypatch.setattr(leerie, "phase_reconcile", _fake_reconcile)
    monkeypatch.setattr(leerie, "check_replan_affordable",
                        lambda *a, **k: None)

    st = _St()
    out = asyncio.run(leerie.phase_overlap_judge(
        plans, "task", st, {"judgment_check_rounds": 1}, {}, {}))

    assert len(replans) == 1, "must re-plan exactly once, not zero or twice"
    # bugfix requires what feat provides, so re-planning feat pulls bugfix in.
    assert replans[0] == {"feat", "bugfix"}
    assert st.data.get("overlap_replan_done") is True
    assert out is not None, "a resolved second verdict must return a plan"


def test_budget_die_does_not_consume_the_recovery(leerie, monkeypatch):
    """The flag must record an ATTEMPT, not an intention.

    `check_replan_affordable` can `die()`. Setting `overlap_replan_done`
    before it persisted the flag to disk while `plans_after_overlap_judge` was
    never written — so `./leerie resume --max-workers N`, the remedy that very
    die() recommends, re-entered the gate, saw the flag, and died immediately
    **without ever attempting the re-plan the raised budget now afforded** —
    with a message claiming re-planning had failed to resolve the
    contradiction when no re-plan had run.

    Note this test deliberately does NOT stub `check_replan_affordable`. Its
    sibling above does, which is exactly why that test could not catch this: a
    guard stubbed out of the path cannot reveal an ordering bug involving it.
    """
    import asyncio

    plans = [
        _plan("feature-implementation", [_sub("feat-001", provides=["x"])]),
        _plan("bug-fixing", [_sub("bugfix-001", requires=["x"])]),
    ]

    class _St:
        def __init__(self):
            # Budget all but exhausted, so the real preflight dies.
            self.data = {"categories": ["feature-implementation",
                                        "bug-fixing"],
                         "worker_count": 199}
            self.saved = None
        def save(self):
            import copy as _c
            self.saved = _c.deepcopy(self.data)

    async def _fake_loop(**kw):
        return ({"collisions": [
            {"a_sid": "feat-001", "b_sid": "bugfix-001", "artifact": "x.ts",
             "resolution": "unresolvable", "reason": "contradictory"}]}, [])

    replans: list = []

    async def _fake_plan(task, st, caps, models, efforts,
                         replan_round=0, domains=None):
        replans.append(domains)
        return plans

    monkeypatch.setattr(leerie, "_run_checked_loop", _fake_loop)
    monkeypatch.setattr(leerie, "check_overlap_judge_output",
                        lambda *a, **k: [])
    monkeypatch.setattr(leerie, "phase_plan", _fake_plan)

    caps = {"judgment_check_rounds": 1, "max_total_workers": 200,
            "planner_samples": 3, "replan_decompose_estimate": 1.5}
    st = _St()

    with pytest.raises(SystemExit) as exc:
        asyncio.run(leerie.phase_overlap_judge(
            plans, "task", st, caps, {}, {}))
    assert exc.value.code == leerie.EXIT_BUDGET_INFEASIBLE
    assert not replans, "no re-plan should have been attempted"
    # The load-bearing assertions: the flag must be neither in memory nor on
    # disk, so a resume with a raised budget can still recover.
    assert st.data.get("overlap_replan_done") is None, (
        "the flag was set despite no re-plan being attempted — a resume would "
        "skip the recovery permanently")
    assert (st.saved or {}).get("overlap_replan_done") is None, (
        "the flag was PERSISTED despite no re-plan being attempted")


def test_recovery_survives_a_budget_die_and_a_resume(leerie, monkeypatch):
    """End-to-end of the same defect: die on budget, then re-enter with the
    same state and a raised cap, and the re-plan must actually happen."""
    import asyncio

    plans = [
        _plan("feature-implementation", [_sub("feat-001", provides=["x"])]),
        _plan("bug-fixing", [_sub("bugfix-001", requires=["x"])]),
    ]

    class _St:
        def __init__(self):
            self.data = {"categories": ["feature-implementation",
                                        "bug-fixing"],
                         "worker_count": 199}
        def save(self):
            pass

    verdicts = [
        {"collisions": [
            {"a_sid": "feat-001", "b_sid": "bugfix-001", "artifact": "x.ts",
             "resolution": "unresolvable", "reason": "contradictory"}]},
        {"collisions": []},
    ]
    idx = {"i": 0}

    async def _fake_loop(**kw):
        v = verdicts[min(idx["i"], len(verdicts) - 1)]
        idx["i"] += 1
        return (v, [])

    replans: list = []

    async def _fake_plan(task, st, caps, models, efforts,
                         replan_round=0, domains=None):
        replans.append(domains)
        return plans

    monkeypatch.setattr(leerie, "_run_checked_loop", _fake_loop)
    monkeypatch.setattr(leerie, "check_overlap_judge_output",
                        lambda *a, **k: [])
    monkeypatch.setattr(leerie, "phase_plan", _fake_plan)
    monkeypatch.setattr(leerie, "phase_reconcile",
                        lambda p, *a, **k: _async_id(p))

    st = _St()
    base = {"judgment_check_rounds": 1, "planner_samples": 3,
            "replan_decompose_estimate": 1.5}

    # Attempt 1: budget exhausted -> dies, recovery NOT consumed.
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_overlap_judge(
            plans, "task", st, dict(base, max_total_workers=200), {}, {}))
    assert not replans

    # Attempt 2: same state, raised cap (the documented remedy).
    idx["i"] = 0
    out = asyncio.run(leerie.phase_overlap_judge(
        plans, "task", st, dict(base, max_total_workers=400), {}, {}))
    assert replans == [{"feat", "bugfix"}], (
        "resume with a raised budget must actually attempt the re-plan")
    assert out is not None


async def _async_id(x):
    return x
