"""The phase-2½ abort must tell the operator the right thing to do.

`_unresolvable_die_message` renders the `die()` that ends a run when the
reconciler cannot resolve a capability tag. It is the last thing an operator
sees after a full planning spend, and it is the only guidance they get.

**Why the wording is treated as behaviour, not cosmetics.** Given a real
failure from this gate, 5 simulated operators were asked for their single
next action. With the previous message, **5 of 5** chose to widen the fence
— make the forbidden surface writable — and **0 of 5** proposed removing the
criterion. On the run that motivated this, the deck's own decision log marked
that capability an unanswered product decision that must not be automated, so
widening was precisely the wrong repair and removing was the right one. With
the current message, 5 of 5 offer removing and 4 of 5 lead with it.

Three defects in the old text, in increasing order of what they cost:

1. It recommended `--source-of-truth codebase` unconditionally, on a run
   that had already set exactly that.
2. That advice is a non-sequitur for a scope disagreement — the preference
   selects where planners draw *conventions* from, not which files they may
   touch (`prompts/planner.md`).
3. DESIGN §11 records narrowing the preference as *historically* the only
   escape hatch, superseded by `requires.extent: external` — so the message
   named a retired mechanism instead of its successor.

Defects 1–3 turned out to be nearly inert: operators skipped that bullet in
both arms (0/5 either way). The damage came from the bullet that *was* read.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

import pytest

ENTRY = {"sid": "test-011", "tag": "config-support-phone",
         "reason": "no subtask in this plan can produce the fix"}
SECOND = {"sid": "feat-002", "tag": "invoice-endpoint-fixed",
          "reason": "owned by a sibling phase document"}
DOMAINS = {"test-011": "testing", "feat-002": "feature-implementation"}


def _msg(leerie, entries=None, sot="codebase"):
    return leerie._unresolvable_die_message(
        entries or [ENTRY], DOMAINS, sot)


# --- every entry is rendered ----------------------------------------------

def test_every_unresolvable_entry_is_named(leerie):
    """TWO entries, not one.

    A renderer that emits only `unresolvable[0]` passes a single-entry test
    while silently hiding the rest of the diagnosis — and multi-entry aborts
    are real: run `1178f696` carried two, run `71a749fe` two.
    """
    msg = _msg(leerie, [ENTRY, SECOND])
    lines = msg.splitlines()
    for e in (ENTRY, SECOND):
        # Per LINE, not per message. Cross-pairing the fields — rendering
        # entry A's sid beside entry B's tag and reason — leaves every
        # substring present while every diagnosis names the wrong subtask.
        owned = [ln for ln in lines
                 if f"{DOMAINS[e['sid']]}/{e['sid']}" in ln]
        assert len(owned) == 1, (
            f"expected exactly one line for {e['sid']}, got {len(owned)}")
        assert e["tag"] in owned[0], (
            f"{e['sid']}'s line does not carry its own tag")
        assert e["reason"] in owned[0], (
            f"{e['sid']}'s line does not carry its own reason")


def test_header_counts_the_entries(leerie):
    assert "resolve 2 capability-tag" in _msg(leerie, [ENTRY, SECOND])
    assert "resolve 1 capability-tag" in _msg(leerie, [ENTRY])


# --- the remediation that changes operator behaviour ----------------------

def test_offers_removing_the_criterion_not_only_widening(leerie):
    """THE regression pin. The old message offered only "make the disputed
    scope explicit", which 5/5 operators read as "authorize the surface"."""
    msg = _msg(leerie)
    assert "removing it from this task" in msg, (
        "the message does not offer the repair that was correct on the run "
        "this gate actually killed")
    assert "giving it to whatever owns that surface" in msg


def test_widening_is_flagged_as_often_wrong(leerie):
    """ANTI-VACUITY partner. Merely mentioning removal alongside widening,
    with no signal about which to prefer, is what the old message
    effectively did. The text must warn about the default choice."""
    msg = _msg(leerie)
    assert "Widening the fence" in msg
    assert "often the wrong one" in msg


def test_the_correct_repair_leads(leerie):
    """ORDER, not presence — order is what the A/B actually measured.

    Operators followed whichever repair came first: with the old text's
    lead ("refine the disputed scope") 5 of 5 widened the fence. Presence
    assertions alone permit a message that names every repair while leading
    with the wrong one — verified: hoisting the fallback back to the front
    left all 17 tests green, and so does reattaching "often the wrong one"
    to the removal clause instead of the widening one.
    """
    msg = _msg(leerie)
    removal = msg.find("removing it from this task")
    widening = msg.find("Widening the fence")
    fallback = msg.find("If neither shape fits")
    for name, i in (("removal", removal), ("widening", widening),
                    ("fallback", fallback)):
        assert i != -1, f"the {name} clause is absent; re-anchor this test"
    assert removal < widening, (
        "the message mentions widening the fence before offering to remove "
        "the criterion; 5/5 operators took whichever came first")
    assert widening < fallback, (
        "the refine-the-task fallback must trail both shapes — it led in "
        "the old message and that is what misrouted every operator")
    caveat = msg.find("often the wrong one")
    assert widening < caveat < widening + 160, (
        "'often the wrong one' must qualify the WIDENING clause; attached "
        "elsewhere it reads as a warning against the correct repair")


def test_names_external_as_the_live_channel(leerie):
    """DESIGN §11 supersedes the retired hatch with `requires.extent:
    external`; the message must point at the successor."""
    assert "requires.extent: external" in _msg(leerie)


def test_retired_source_of_truth_advice_is_gone(leerie):
    """The exact old sentence must not survive anywhere, at any setting."""
    for sot in ("codebase", "research", "both"):
        msg = _msg(leerie, sot=sot)
        assert "stop treating them as a feature checklist" not in msg
        assert "narrow scope with" not in msg


# --- the source-of-truth bullet is conditional AND rewritten --------------

def test_sot_bullet_absent_when_the_run_already_uses_codebase(leerie):
    msg = _msg(leerie, sot="codebase")
    assert "source_of_truth" not in msg
    assert "--source-of-truth" not in msg


@pytest.mark.parametrize("sot", ["research", "both"])
def test_sot_bullet_present_and_scoped_when_it_could_apply(leerie, sot):
    """PAIRED with the test above — that one alone passes if the bullet is
    deleted outright, which would lose a genuinely useful hint on a `both`
    run (DESIGN §11: research can surface phantom prerequisites)."""
    msg = _msg(leerie, sot=sot)
    assert f"source_of_truth = {sot}" in msg
    assert "--source-of-truth codebase" in msg
    assert "does not affect a scope-fence disagreement" in msg, (
        "the bullet must say what it does NOT fix, or it reads as the "
        "primary remedy again")


@pytest.mark.parametrize("sot", ["codebase", "research", "both"])
def test_the_scope_fence_shape_is_named_at_every_setting(leerie, sot):
    """The contradiction bullet is unconditional. Parametrized so it cannot
    pass by being present only on the path the other tests exercise."""
    msg = _msg(leerie, sot=sot)
    assert "its own scope forbids" in msg
    assert "fences off" in msg


def test_the_stated_shape_count_matches_the_bullets(leerie):
    """An unverified count in shipping text.

    The first draft said "usually one of **two shapes**:" and then printed
    three bullets — the third being a fallback *action*, not a shape. That
    is the failure mode CLAUDE.md records for commit messages, and it is
    worse here because it ships to an operator mid-abort. No other test in
    this file reads the enumeration as an enumeration.
    """
    msg = _msg(leerie)
    head, _, body = msg.partition("shapes:")
    assert body, "the message no longer states a shape count"
    words = {"one": 1, "two": 2, "three": 3, "four": 4}
    # `partition` consumes "shapes:", so `head` ends "...one of two ".
    stated = next((n for w, n in words.items() if f"one of {w} " in head), None)
    assert stated is not None, f"could not parse the stated count from {head[-60:]!r}"
    assert body.count("•") == stated, (
        f"message says {stated} shapes but prints {body.count('•')} bullets")


# --- wiring ---------------------------------------------------------------

def _closure_src(leerie) -> str:
    """`phase_reconcile` source with docstrings stripped.

    Mandatory: the helper's own docstring and the surrounding comments name
    `_unresolvable_die_message` and `_effective_source_of_truth` while
    explaining them, so a raw substring scan matches the prose describing
    what it checks — the trap CLAUDE.md records for the zombie reaper.
    """
    src = textwrap.dedent(inspect.getsource(leerie.phase_reconcile))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
    return ast.unparse(tree)


def test_closure_delegates_to_the_module_level_helper(leerie):
    """The extraction is what makes any of the above testable. Inlining the
    message back into the closure re-creates the condition that led
    `tests/test_reconciler_cycle_gate.py` to test a copy of it instead."""
    src = _closure_src(leerie)
    assert "_unresolvable_die_message(" in src
    assert "planner-coverage gap" not in src, (
        "the message text is inline in phase_reconcile again; it belongs in "
        "the module-level helper where it can be tested")


def test_message_reads_the_resolved_preference_not_a_literal(leerie):
    """AST, not substring: resolve the argument's BINDING.

    `_effective_source_of_truth(st)` appearing somewhere in the function is
    not the same as it being the argument actually passed — a literal
    `"codebase"` alongside an unrelated call would satisfy a text scan.
    """
    src = textwrap.dedent(inspect.getsource(leerie.phase_reconcile))
    found = []
    for node in ast.walk(ast.parse(src)):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "_unresolvable_die_message"):
            found.append(node)
    assert len(found) == 1, (
        f"expected exactly one call to the message helper, found {len(found)}")
    third = found[0].args[2]
    assert isinstance(third, ast.Call), (
        f"source_of_truth argument is {ast.dump(third)[:80]}, not a call — a "
        "hardcoded tier here re-creates the original defect")
    assert ast.unparse(third) == "_effective_source_of_truth(st)"


def test_the_message_is_passed_to_die(leerie):
    """The gate must FAIL CLOSED, and nothing else pins that.

    Every other test here checks what the message *says*. Swapping
    `die(...)` for `log(...)` at the call site would leave all of them
    green while the run stopped aborting — the whole phase-2½ contract is
    that an unresolved capability tag ends the run rather than executing a
    plan known to be broken.

    Verified absent elsewhere: of the tests combining `unresolvable` with a
    raises assertion, none exercise this die —
    `test_phase_overlap_judge_dies_on_unresolvable` is a different gate,
    `test_unresolved_retry_dies_after_attempt_2` covers the must-include
    validator, and `test_validate_plan.py::test_unresolvable_requires_dies`
    covers the `_validate_plan` backstop.
    """
    src = textwrap.dedent(inspect.getsource(leerie.phase_reconcile))
    wrapped = [
        n for n in ast.walk(ast.parse(src))
        if isinstance(n, ast.Call)
        and isinstance(n.func, ast.Name) and n.func.id == "die"
        and any(isinstance(a, ast.Call) and isinstance(a.func, ast.Name)
                and a.func.id == "_unresolvable_die_message"
                for a in n.args)
    ]
    assert len(wrapped) == 1, (
        "the rendered message must be the argument of exactly one die() "
        f"call; found {len(wrapped)}. A log() here would turn a fail-closed "
        "gate into a warning and every content test would stay green.")


def test_helper_is_pure_and_module_level(leerie):
    """It must be callable without a State, a run dir, or a worker — that
    is the whole reason the extraction happened."""
    assert callable(leerie._unresolvable_die_message)
    sig = inspect.signature(leerie._unresolvable_die_message)
    assert list(sig.parameters) == [
        "unresolvable", "sid_domain", "source_of_truth"]


# --- the gate itself, by execution ----------------------------------------

def test_the_gate_actually_aborts_the_run(leerie, monkeypatch, tmp_path,
                                          capsys):
    """Drive the REAL phase-2½ gate and observe it fail closed.

    Everything else in this file tests the message as a pure function, and
    the wiring as source shape. **Nothing tested the seam between them**, and
    the seam is where the contract lives.

    Measured: changing `out.get("unresolvable", [])` to
    `out.get("unresolved", [])` in `_check_unresolvable` — so the gate reads
    a key that never exists, never fires, and lets the run execute a plan it
    has already judged broken — left **140 tests green** across this file,
    `test_phase_reconcile.py`, `test_reconciler_cycle_gate.py`,
    `test_demote_unresolvable_twin.py` and `test_reconciler_payload_fields.py`.

    Swapping the first two arguments at the die site survived too: only
    `args[2]` was ever checked, so the abort would raise `TypeError` instead
    of rendering a diagnosis.

    This test closes both, plus the phantom-mutation half of
    `test_reconciler_cycle_gate.py`'s conditional-drops case: the gate must
    die *before* `_apply_reconciler_output`, so a `conditional_drop` emitted
    alongside an `unresolvable` must leave no trace in state.
    """
    import asyncio

    plans = [
        {"domain": "testing", "status": "ready", "subtasks": [{
            "id": "test-001", "title": "t", "intent": "i",
            "scope_note": "n", "success_criteria_seed": "s", "size": "small",
            "files_likely_touched": [], "depends_on": [], "provides": [],
            "requires": [{"tag": "nobody-provides-this", "extent": "in_plan"}]}]},
        {"domain": "configuration-build", "status": "ready", "subtasks": [{
            "id": "config-001", "title": "c", "intent": "i2",
            "scope_note": "n2", "success_criteria_seed": "s", "size": "small",
            "files_likely_touched": [], "depends_on": [],
            "provides": ["unrelated"], "requires": []}]},
    ]

    async def fake_claude_p(**kwargs):
        return {"renames": [], "added_requires": [], "added_subtasks": [],
                "dependency_edges": [], "merged_subtasks": [],
                "tag_ops": [
                    {"op": "unresolvable", "sid": "test-001",
                     "tag": "nobody-provides-this",
                     "reason": "no subtask can produce it"},
                    # Emitted alongside, so the "die before apply" property
                    # is observable: this must leave no trace in state.
                    {"op": "conditional_drop", "sid": "test-001",
                     "tag": "", "reason": "would-be drop"}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    leerie_root = tmp_path / ".leerie"
    run_id = "test-gate-fires"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "t", "worker_count": 0}
    st.save()

    with pytest.raises(SystemExit) as exc:
        asyncio.run(leerie.phase_reconcile(
            plans, "t", st, dict(leerie.DEFAULT_CAPS),
            {"reconciler": "sonnet"}, {"reconciler": "medium"}))

    assert exc.value.code != 0, "the gate exited zero — it did not fail closed"
    err = capsys.readouterr().err
    assert "could not resolve" in err, (
        "the gate aborted without rendering the diagnosis")
    assert "testing/test-001" in err and "nobody-provides-this" in err, (
        "the abort did not name the offending subtask/tag — the message "
        "helper may be receiving its arguments in the wrong order")
    assert not st.data.get("conditional_drops"), (
        "a conditional_drop was applied despite the gate firing; the check "
        "must run BEFORE _apply_reconciler_output so a fail-closed run "
        "leaves no phantom mutations")
