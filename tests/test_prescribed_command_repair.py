"""`_repair_prescribed_commands` — the adherence gate repairs instead of re-driving.

`check_prescribed_command_coverage` gates on `runs_commands`, a planner
self-report field populated on **4.9% of subtasks** (24 of 486 across 38 real
plans; 74% of plans have none at all). So on any task that prescribes a command
the floor fires almost everywhere, and its only remedy was a full re-plan.

Measured on run `d8a764f3…`: the first plan had **0 of 35** subtasks carrying
`runs_commands` and **8** floor issues. The re-plan *did* fix it (14 of 36, 0
issues) — so the floor is satisfiable, contrary to a first reading — but it
cost **~125 of the run's 201 spawns**, twice the entire first planning pass,
and the run then died of budget exhaustion having written no code.

The re-plan works; its *price* is the defect. leerie already holds the
prescribed commands as structured classifier output, so attaching them is a
mechanical repair costing zero workers — the same detect-repair-then-die
contract the wiring gate established (DESIGN §5).

**Synthesise, don't guess an owner.** An "attach to the subtask that owns
verification" rule was prototyped and rejected: against `d8a764f3`'s real plan
a verification-shaped matcher hits **32 of 36** subtasks, so an
exactly-one-owner rule would never fire and a looser one would attach
arbitrarily. `test_does_not_attach_to_an_arbitrary_existing_subtask` pins that
the repair adds a subtask rather than mutating one.
"""
from __future__ import annotations


def _sub(sid, deps=None, **kw):
    base = {
        "id": sid, "title": f"work {sid}", "intent": "do it",
        "scope_note": "one change", "files_likely_touched": [],
        "depends_on": list(deps or []), "requires": [], "provides": [],
        "success_criteria_seed": "done", "size": "small",
        "investigation_notes": "",
    }
    base.update(kw)
    return base


def _plans(n=3, domain="bug-fixing", prefix="bugfix"):
    """A plan with a linear dependency chain, so the sink is unambiguous."""
    subs = [_sub(f"{prefix}-{i:03d}", deps=([f"{prefix}-{i-1:03d}"] if i > 1 else []))
            for i in range(1, n + 1)]
    return [{"domain": domain, "status": "ready", "subtasks": subs}]


def _prescribed(*cmds):
    return {"is_prescribed": True, "commands": list(cmds)}


_INCIDENT = _prescribed("pnpm run lint:fix", "pnpm run build")


# ----- the measured incident ------------------------------------------------

def test_the_incident_shape_repairs_to_a_clean_floor(leerie):
    """`d8a764f3`'s shape: prescribed commands, zero subtasks declaring them."""
    plans = _plans(3)
    subs = [s for p in plans for s in p["subtasks"]]
    before = leerie.check_prescribed_command_coverage(_INCIDENT, subs)
    assert before, "fixture must reproduce the floor firing"

    sid = leerie._repair_prescribed_commands(plans, _INCIDENT)
    assert sid

    after = leerie.check_prescribed_command_coverage(
        _INCIDENT, [s for p in plans for s in p["subtasks"]])
    assert after == [], f"floor still fires after repair: {after}"


def test_repair_costs_no_workers(leerie):
    """The whole point. The function is pure Python — no `claude_p`, no
    `st.bump_workers`. Guarded by source inspection since a spawn would
    otherwise be invisible in a unit test."""
    import inspect
    src = inspect.getsource(leerie._repair_prescribed_commands)
    assert "claude_p" not in src
    assert "bump_workers" not in src
    assert "await" not in src


# ----- what it declines to do ----------------------------------------------

def test_already_clean_plan_is_untouched(leerie):
    """0 false positives: a plan that already runs the commands gets no
    synthetic subtask."""
    plans = _plans(2)
    plans[0]["subtasks"][1]["runs_commands"] = ["pnpm run lint:fix",
                                                "pnpm run build"]
    n_before = len(plans[0]["subtasks"])
    assert leerie._repair_prescribed_commands(plans, _INCIDENT) is None
    assert len(plans[0]["subtasks"]) == n_before


def test_no_commands_declines(leerie):
    plans = _plans(2)
    assert leerie._repair_prescribed_commands(
        plans, {"is_prescribed": True, "commands": []}) is None
    assert leerie._repair_prescribed_commands(
        plans, {"is_prescribed": True}) is None


def test_empty_plan_declines_rather_than_raising(leerie):
    assert leerie._repair_prescribed_commands([], _INCIDENT) is None
    assert leerie._repair_prescribed_commands(
        [{"domain": "bug-fixing", "subtasks": []}], _INCIDENT) is None


def test_unknown_domain_declines(leerie):
    """A synthetic plan (e.g. the reconciler's `_reconciler`) has no real
    category, so it cannot supply an id prefix `_validate_plan` accepts.
    Declining beats emitting an invalid subtask."""
    plans = [{"domain": "_reconciler", "status": "ready",
              "subtasks": [_sub("docs-006")]}]
    assert leerie._repair_prescribed_commands(plans, _INCIDENT) is None


def test_does_not_attach_to_an_arbitrary_existing_subtask(leerie):
    """Synthesise-first: no pre-existing subtask's `runs_commands` is
    mutated. A verification-shaped matcher hits 32 of 36 real subtasks, so
    'pick the owner' would attach somewhere arbitrary."""
    plans = _plans(3)
    original = [dict(s) for s in plans[0]["subtasks"]]
    leerie._repair_prescribed_commands(plans, _INCIDENT)
    for before, after in zip(original, plans[0]["subtasks"]):
        assert before == after, f"{before['id']} was mutated"


# ----- the synthesised subtask ---------------------------------------------

def test_synthesised_subtask_carries_every_command(leerie):
    pres = _prescribed("a b", "c d", "e f")
    plans = _plans(2)
    sid = leerie._repair_prescribed_commands(plans, pres)
    new = next(s for s in plans[0]["subtasks"] if s["id"] == sid)
    assert new["runs_commands"] == ["a b", "c d", "e f"]


def test_id_uses_the_domain_prefix_and_does_not_collide(leerie):
    plans = _plans(2)
    plans[0]["subtasks"].append(_sub("bugfix-901"))   # squat the first slot
    sid = leerie._repair_prescribed_commands(plans, _INCIDENT)
    assert sid.startswith("bugfix-")
    assert sid != "bugfix-901"
    ids = [s["id"] for s in plans[0]["subtasks"]]
    assert len(ids) == len(set(ids)), "id collision"


def test_depends_on_the_sinks_only(leerie):
    """Depending on every sink puts it strictly last without inventing an
    ordering opinion; nothing depends on it, so it cannot close a cycle."""
    plans = _plans(3)
    sid = leerie._repair_prescribed_commands(plans, _INCIDENT)
    new = next(s for s in plans[0]["subtasks"] if s["id"] == sid)
    assert new["depends_on"] == ["bugfix-003"]
    assert not any(sid in (s.get("depends_on") or [])
                   for s in plans[0]["subtasks"] if s["id"] != sid)


def test_schedules_alone_in_the_final_wave(leerie):
    plans = _plans(4)
    sid = leerie._repair_prescribed_commands(plans, _INCIDENT)
    subtasks, waves = leerie._schedule(plans)
    leerie._validate_plan(subtasks)
    assert waves[-1] == [sid]


def test_multi_domain_plan_still_validates(leerie):
    """Sinks span every plan, so the verifier trails the whole graph."""
    plans = _plans(2) + [{"domain": "testing", "status": "ready",
                          "subtasks": [_sub("test-001"), _sub("test-002")]}]
    sid = leerie._repair_prescribed_commands(plans, _INCIDENT)
    subtasks, waves = leerie._schedule(plans)
    leerie._validate_plan(subtasks)
    assert waves[-1] == [sid]
    new = subtasks[sid]
    assert set(new["depends_on"]) == {"bugfix-002", "test-001", "test-002"}


def test_repair_is_idempotent(leerie):
    """A second call finds the floor clean and adds nothing — the gate's
    check loop can run several rounds."""
    plans = _plans(2)
    first = leerie._repair_prescribed_commands(plans, _INCIDENT)
    n = len(plans[0]["subtasks"])
    assert leerie._repair_prescribed_commands(plans, _INCIDENT) is None
    assert len(plans[0]["subtasks"]) == n and first


# ----- wiring ---------------------------------------------------------------

def test_gate_repairs_before_evaluating_the_floor(leerie):
    """Source-coupling guard. Repairing *after* the floor is read would leave
    the gate re-planning exactly as before — the fix would be inert, which is
    the failure mode B1 already demonstrated for a different check."""
    import inspect
    src = inspect.getsource(leerie.phase_adherence_gate)
    assert "_repair_prescribed_commands(" in src, "gate does not repair"
    assert src.index("_repair_prescribed_commands(") < src.index(
        "check_prescribed_command_coverage("), (
        "the repair must precede the floor evaluation, or it changes nothing")


def test_repair_precedes_the_replan_path(leerie):
    """A repairable gap must never reach `phase_plan`."""
    import inspect
    src = inspect.getsource(leerie.phase_adherence_gate)
    assert src.index("_repair_prescribed_commands(") < src.index(
        "await phase_plan(")


# ----- behavioural wiring (not source-coupled) -------------------------------

def test_gate_actually_synthesises_the_subtask_end_to_end(leerie, tmp_path,
                                                          monkeypatch):
    """Drives the real `phase_adherence_gate`. The source guards above assert
    the repair is *called before* the floor read, but source inspection cannot
    see whether it runs — which is precisely how two earlier fixes in this
    area shipped inert (a preflight that read state written later, and a flag
    set before a die). So this asserts the observable outcome: the returned
    plan carries a subtask that runs the prescribed commands, and the gate
    does not re-plan.
    """
    import asyncio

    hp = tmp_path / "adherence"
    hp.mkdir()

    class _St:
        def __init__(self):
            self.data = {
                "prescribed_procedure": {
                    "is_prescribed": True,
                    "commands": ["pnpm run lint:fix", "pnpm run build"],
                },
                "categories": ["bug-fixing"],
                "worker_count": 0,
            }
            self.run_dir = hp
            # claude_p derives the checkout write-denial from this
            # (_repo_write_denials) and the §12 cwd guard compares against it;
            # a stub without it silently disables both.
            self.repo_root = "/leerie-test-user-repo"
        def save(self):
            pass
        def bump_workers(self, caps):
            pass

    plans = _plans(3)
    before = leerie.check_prescribed_command_coverage(
        st_pp := _St().data["prescribed_procedure"],
        [s for p in plans for s in p["subtasks"]])
    assert before, "fixture must start with the floor firing"

    async def _fake_loop(*, invoke, check, name, max_rounds,
                         make_feedback_prompt=None):
        # Drive the real `check` callback once, exactly as the loop does.
        return ({"instruction_adherence": 10, "violations": [],
                 "rationale": "ok"}, []) if check(
            {"instruction_adherence": 10, "violations": [],
             "rationale": "ok"}) == [] else (None, ["floor still firing"])

    replanned = []

    async def _fake_plan(*a, **k):
        replanned.append(True)
        return plans

    monkeypatch.setattr(leerie, "_run_checked_loop", _fake_loop)
    monkeypatch.setattr(leerie, "phase_plan", _fake_plan)

    out = asyncio.run(leerie.phase_adherence_gate(
        plans, "task", _St(), {"judgment_check_rounds": 1},
        {"adherence_judge": "sonnet"}, {"adherence_judge": "medium"}))

    subs = [s for p in (out or plans) for s in (p.get("subtasks") or [])]
    runners = [s for s in subs if s.get("runs_commands")]
    assert runners, "no subtask runs the prescribed commands after the gate"
    assert set(runners[0]["runs_commands"]) == {
        "pnpm run lint:fix", "pnpm run build"}
    assert not replanned, "the repair must make a re-plan unnecessary"
    assert leerie.check_prescribed_command_coverage(st_pp, subs) == [], (
        "floor must be clean after the gate")
