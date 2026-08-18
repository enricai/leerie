"""The resolved source-of-truth preference must REACH every consumer.

DESIGN §11: "whichever path resolved the preference, its value becomes a
setting carried to every planner and implementer on every run, whatever the
classifier decided." The code did not implement that. `gather_answers` wrote
`answers["source_of_truth"]` only when the classifier had set
`needs_source_of_truth`, and its consumers then fell back to a **hardcoded
`"codebase"` literal** rather than the resolved preference — so an explicit
`--source-of-truth research` was silently downgraded on any run whose
classifier did not flag the question.

Measured over the local run corpus: 74 of 196 runs took that branch, 57 of
them with a resolved preference of `both`. The mechanism is absolute —
planners given `codebase` made 0 research calls across 1179 planner logs,
while `both` produced 62 — so the value genuinely changes worker behaviour.

**The trap this file exists for.** `tests/test_gather_answers_validation.py`
already asserted the correct contract for all three values and passed,
because its fixture hardcoded `needs_source_of_truth: True`. Pinning
`gather_answers` alone is not enough either: the value has to be shown
arriving at `phase_plan`'s ctx, `_write_plan`'s spec files and the
pr_writer payload, which is the
**deleted** `tests/test_resolve_aws_prefs.py` lesson (a resolver pinned for
months while
the value reached nothing).
"""
from __future__ import annotations

import ast
import inspect
import json
import pathlib
import textwrap

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent


def _state(leerie, tmp_path, **overrides):
    """A State with the classifier flag OFF by default — the branch that
    had no coverage anywhere, and the one every assertion here needs."""
    leerie_root = tmp_path / ".leerie"
    run_id = "test-run-sot"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {
        "task": "test task",
        "categories": ["testing"],
        "classifier_questions": [],
        "needs_source_of_truth": False,
        "source_of_truth_pref": "both",
    }
    st.data.update(overrides)
    return st


# --- gather_answers writes it regardless of the classifier flag ------------

@pytest.mark.parametrize("pref", ["codebase", "research", "both"])
@pytest.mark.parametrize("need_sot", [True, False])
def test_preference_is_delivered_on_both_classifier_branches(
        leerie, tmp_path, pref, need_sot):
    """The regression pin for run 2d7527f1's class.

    `research` and `both` are the rows that matter: `codebase` alone passes
    against the very literal this change removes.
    """
    st = _state(leerie, tmp_path,
                needs_source_of_truth=need_sot, source_of_truth_pref=pref)
    answers = leerie.gather_answers(st, None)
    assert answers["source_of_truth"] == pref
    assert st.data["answers"]["source_of_truth"] == pref


def test_supplied_answer_still_wins_over_the_preference(leerie, tmp_path):
    """Anti-vacuity partner for the test above.

    Writing unconditionally must not mean clobbering an operator's explicit
    `--answers`. Without this, "always write" could be implemented as
    "always overwrite" and the sweep above would not notice.
    """
    st = _state(leerie, tmp_path, source_of_truth_pref="both")
    answers = leerie.gather_answers(st, {"source_of_truth": "research"})
    assert answers["source_of_truth"] == "research"


# --- _effective_source_of_truth precedence --------------------------------

@pytest.mark.parametrize("answers,pref,expected", [
    # The load-bearing row: answers and pref DISAGREE. A table where they
    # always agree passes against a helper that ignores `answers` entirely.
    ({"source_of_truth": "research"}, "codebase", "research"),
    ({"source_of_truth": "codebase"}, "research", "codebase"),
    # answers empty -> fall through to the refreshed preference, never to a
    # hardcoded tier. This is the resumed-from-pre-fix-state.json case.
    ({}, "research", "research"),
    ({}, "both", "both"),
    ({}, "codebase", "codebase"),
])
def test_effective_source_of_truth_precedence(
        leerie, tmp_path, answers, pref, expected):
    st = _state(leerie, tmp_path, answers=answers, source_of_truth_pref=pref)
    assert leerie._effective_source_of_truth(st) == expected


def test_effective_source_of_truth_last_resort_is_both_not_codebase(
        leerie, tmp_path):
    """With nothing recorded at all the answer is `both` — the documented
    default from `resolve_source_of_truth` — never `codebase`. A `codebase`
    last resort is the original defect wearing a different hat."""
    st = _state(leerie, tmp_path)
    st.data.pop("source_of_truth_pref", None)
    st.data["answers"] = {}
    assert leerie._effective_source_of_truth(st) == "both"


# --- the value must REACH the consumers -----------------------------------

def _consumer_source(leerie, fn_name):
    """Function source with comments and EVERY docstring stripped.

    Nested functions too, matching `_closure_src` in
    `test_unresolvable_die_message.py` — `phase_plan` and
    `_compose_pr_via_llm` both contain nested defs, so stripping only the
    top-level docstring would let a nested one naming a literal tier
    false-positive the very assertion this helper exists to de-noise.

    Mandatory: the comments in this region necessarily name
    `source_of_truth` and quote the removed `"codebase"` literal while
    explaining it, so a raw substring scan matches the prose describing what
    it forbids — the trap CLAUDE.md records for the zombie reaper.
    """
    src = textwrap.dedent(inspect.getsource(getattr(leerie, fn_name)))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if (isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
                and node.body and isinstance(node.body[0], ast.Expr)
                and isinstance(node.body[0].value, ast.Constant)
                and isinstance(node.body[0].value.value, str)):
            node.body = node.body[1:]
    return ast.unparse(tree)


@pytest.mark.parametrize(
    "fn_name", ["phase_plan", "_write_plan", "_compose_pr_via_llm"])
def test_no_consumer_falls_back_to_a_hardcoded_tier(leerie, fn_name):
    """All three `State`-holding consumers must read through the helper.

    Anti-vacuity: assert the read was FOUND before asserting its shape — a
    scan that matches nothing passes every assertion it makes.

    Note the three parametrizations do not carry equal force. `phase_plan`
    and `_write_plan` both had a literal `"codebase"` default, so all three
    assertions bite there. `_compose_pr_via_llm` never did — it was a bare
    `answers.get("source_of_truth")` yielding `None` — so for that case only
    the first assertion can fail. Kept parametrized anyway: it is the
    forward-looking guard against someone adding a default there later.
    """
    body = _consumer_source(leerie, fn_name)
    assert "_effective_source_of_truth(st)" in body, (
        f"{fn_name} does not read the source-of-truth through the shared "
        "helper; a per-consumer read is how the two silently diverged")
    # NOTE: a double-quoted form of this pattern would be DEAD here —
    # `_consumer_source` returns `ast.unparse` output, which always emits
    # single quotes, so `'answers.get("source_of_truth", "codebase")'` can
    # never match. The single-quoted scan below is the one that works.
    # (Same quote-style trap `_subtask_view_keys` documents in
    # `tests/test_reconciler_payload_fields.py`.)
    assert "'codebase'" not in body, (
        f"{fn_name} still names a literal source-of-truth tier")
    # And the literal scan alone is not sound: moving the default one
    # indirection away (a module-level `_DEFAULT_SOT = "codebase"`) defeats
    # it. `test_planner_ctx_carries_the_resolved_preference` and
    # `test_spec_files_carry_the_resolved_preference` are what actually
    # pin the value; this guard is the cheap structural companion.


def test_spec_files_carry_the_resolved_preference(leerie, tmp_path):
    """End-to-end through `_write_plan`: the value must land in the spec
    files an implementer actually reads.

    Parametrization over a NON-codebase preference is the whole point; a
    `codebase`-only assertion is answered by the literal being removed.
    """
    for pref in ("research", "codebase"):
        st = _state(leerie, tmp_path / pref, source_of_truth_pref=pref)
        st.data["answers"] = {}
        leerie_dir = st.run_dir
        (leerie_dir / "subtasks").mkdir(parents=True, exist_ok=True)
        subtasks = {"test-001": {
            "id": "test-001", "title": "t", "intent": "i",
            "success_criteria_seed": ["s"], "files_likely_touched": [],
            "depends_on": [], "requires": [], "provides": [],
        }}
        leerie._write_plan(leerie_dir, "task", st, subtasks, [["test-001"]])
        spec = json.loads(
            (leerie_dir / "subtasks" / "test-001.json").read_text())
        assert spec["_source_of_truth"] == pref, (
            f"spec carried {spec['_source_of_truth']!r}, expected {pref!r}")


# --- resume must refresh answers, and in the right order -------------------

def test_resume_hazard_absent_for_any_spelling(leerie):
    """Generalises the literal scan below.

    That test forbids one exact statement, so
    `st.data.setdefault("answers", {})["source_of_truth"] = sot_pref` —
    the same hazard, different spelling — restores it untouched. This walks
    the resume arm for ANY assignment whose target subscripts an `answers`
    expression with `"source_of_truth"`.
    """
    src = textwrap.dedent(inspect.getsource(leerie._run_phases))
    offenders = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Assign):
            continue
        for t in node.targets:
            if (isinstance(t, ast.Subscript)
                    and isinstance(t.slice, ast.Constant)
                    and t.slice.value == "source_of_truth"
                    and "answers" in ast.unparse(t.value)):
                offenders.append(ast.unparse(node))
    assert not offenders, (
        "the resume arm writes answers['source_of_truth']; on a resume "
        f"without --answers that discards an operator's value: {offenders}")


def test_resume_never_overwrites_a_recorded_source_of_truth(leerie):
    """A resume must not discard an operator's `--answers` value.

    An earlier draft refreshed `st.data["answers"]["source_of_truth"]` from
    the re-resolved preference on every resume. That looks safe next to
    `_absorb_supplied_answers`, which lets an explicit `--answers` win — but
    that helper opens with `if not args.answers: return`, and a plain
    `leerie resume` passes no `--answers`. So the sequence

        leerie "task" --answers f.json   # {"source_of_truth": "research"}
        leerie resume                    # no --answers

    silently replaced `research` with the repo default. That is the same
    class of defect this whole file exists to remove, one function away
    from it.

    The refresh is gone: `_effective_source_of_truth` already falls back to
    `source_of_truth_pref` when `answers` carries no verdict, which covers
    the stale-state case without touching a recorded answer.

    The previous test asserted only `refresh < absorb` **source order**, and
    source order cannot deliver the guarantee its own docstring claimed —
    the absorber is a no-op on precisely the resume that matters.
    """
    src = inspect.getsource(leerie._run_phases)
    assert 'st.data["answers"]["source_of_truth"] = sot_pref' not in src, (
        "the resume arm writes answers['source_of_truth'] from the resolved "
        "preference; on a resume without --answers that silently discards an "
        "operator-supplied value")


def test_effective_helper_covers_what_the_refresh_was_for(leerie, tmp_path):
    """ANTI-VACUITY partner for the test above.

    Deleting the refresh is only safe because the helper's fallback handles
    a pre-fix `state.json` whose `answers` is empty. Without this, the guard
    above could be satisfied by code that simply lost the behaviour.
    """
    st = _state(leerie, tmp_path, source_of_truth_pref="research")
    st.data["answers"] = {}
    assert leerie._effective_source_of_truth(st) == "research"

    st.data["answers"] = {"source_of_truth": "codebase"}
    assert leerie._effective_source_of_truth(st) == "codebase", (
        "a recorded answer must win over the preference, or the resume "
        "hazard returns through the helper instead")


def test_prompt_names_the_on_disk_spec_key(leerie):
    """`prompts/implementer.md` told workers to read `source_of_truth`; the
    key `_write_plan` writes is `_source_of_truth`."""
    md = (REPO / "prompts" / "implementer.md").read_text()
    assert "`_source_of_truth`" in md
    assert "the `source_of_truth`," not in md, (
        "prompt names a spec key that does not exist on disk")


# --- the planner ctx, by execution ----------------------------------------

@pytest.mark.parametrize("answers,pref,expected", [
    # The load-bearing rows: `answers` and `pref` DISAGREE, so a consumer
    # that reads the preference directly instead of going through the
    # helper yields the WRONG value and is caught. Making them agree — as
    # the first draft of this test did — leaves the bypass invisible;
    # verified by mutation.
    ({"source_of_truth": "research"}, "both", "research"),
    ({"source_of_truth": "both"}, "codebase", "both"),
    # And the fallback path: no recorded answer, so the preference is the
    # answer. Non-`codebase` on purpose — a `codebase` row here would be
    # carried by the literal scan rather than by the value.
    ({}, "research", "research"),
])
def test_planner_ctx_carries_the_resolved_preference(leerie, monkeypatch,
                                                     tmp_path, answers,
                                                     pref, expected):
    """Drive the REAL `phase_plan` and read what the planner is handed.

    Everything else in this file source-couples `phase_plan`; only
    `_write_plan` was executed end to end. That left the primary consumer —
    the one whose divergence was the whole incident — guarded by shape
    alone, and two literal-free mutants proved it:

      * `"source_of_truth": st.data.get("source_of_truth_pref")` — planners
        bypass the helper and receive the preference even when an operator
        supplied a different answer.
      * deleting the ctx entry outright — planners receive no
        `source_of_truth` at all, though `prompts/planner.md:20` documents
        it as always present.

    Both left all 20 tests green. A third mutant substituting the literal
    `"codebase"` *was* caught, but only by the literal scan — so the
    coverage was an accident of that mutant's spelling.

    Parametrized so `answers` and `source_of_truth_pref` DISAGREE on two
    of three rows. That is what discriminates: with them equal — as this
    test's first draft had it — a consumer reading the preference directly
    produces the same string as one going through the helper, and the
    bypass mutant passes. Verified: it did.

    The stubbed planner returns an **empty `subtasks` array** — a legal
    outcome (`prompts/planner.md`) that skips `_recursive_decompose`
    entirely, so no `fit_judge`/`splitter` stubs are needed.
    `tests/test_phase_plan_recursion_wiring.py` pins that short-circuit, so
    this rests on documented behaviour rather than luck.

    Deliberately does NOT reproduce the ctx block —
    `tests/test_phase_plan_repo_map_ctx.py::_build_ctx` is the body-blind
    copy CLAUDE.md names, and copying it here would recreate the exact
    blindness this test exists to remove.
    """
    import asyncio

    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"domain": "testing", "status": "ready", "subtasks": [],
                "confidence": {"task_understanding": 9.0,
                               "decomposition_quality": 9.0,
                               "basis": "stub"}}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    st = _state(leerie, tmp_path, source_of_truth_pref=pref)
    st.data["categories"] = ["testing"]
    st.data["skip_repo_map"] = True
    st.data["answers"] = dict(answers)

    asyncio.run(leerie.phase_plan(
        "t", st, dict(leerie.DEFAULT_CAPS),
        {"planner": "sonnet", "fit_judge": "sonnet", "splitter": "sonnet"},
        {"planner": "medium", "fit_judge": "medium", "splitter": "medium"}))

    assert calls, (
        "no planner was spawned — this test proves nothing unless "
        "phase_plan actually reached claude_p")
    prompt = calls[0].get("user_prompt") or ""
    assert "CONTEXT:" in prompt, "the planner prompt has no CONTEXT block"
    assert f'"source_of_truth": "{expected}"' in prompt, (
        f"planners were not handed {expected!r}; the ctx either bypasses "
        "_effective_source_of_truth (reading the preference directly) or "
        "omits the key entirely")


# --- the pr_writer payload, by execution -----------------------------------

@pytest.mark.parametrize("answers,pref,expected", [
    # Disagreeing rows again: a consumer that calls the helper and then
    # discards its result, shipping the pre-fix
    # `(st.data.get("answers") or {}).get("source_of_truth")`, is invisible
    # when the two agree. Verified — that exact mutant left 24 tests green.
    ({"source_of_truth": "research"}, "both", "research"),
    ({}, "research", "research"),
])
def test_pr_payload_carries_the_resolved_preference(leerie, monkeypatch,
                                                    tmp_path, answers, pref,
                                                    expected):
    """Execute `_compose_pr_via_llm`; read the payload the worker receives.

    This was the last of the three `State`-holding consumers still guarded by
    source-coupling alone. Keeping a call to `_effective_source_of_truth(st)`
    — so the source scan is satisfied — while discarding its value and
    shipping the pre-fix expression reproduces the original defect verbatim
    in the payload that feeds `pr_writer`, and left every other test passing.
    """
    import asyncio

    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"title": "t", "body": "b", "used_template": None}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    st = _state(leerie, tmp_path, source_of_truth_pref=pref)
    st.data["answers"] = dict(answers)
    st.data["categories"] = ["testing"]

    try:
        asyncio.run(leerie._compose_pr_via_llm(
            st, dict(leerie.DEFAULT_CAPS), {"pr_writer": "sonnet"},
            {"pr_writer": "medium"}, tmp_path, None))
    except Exception:
        # The worker call is what this test is about; anything after it
        # (git plumbing, file writes) may legitimately fail in a bare
        # tmp_path and must not mask the assertion below.
        pass

    assert calls, "pr_writer was never invoked; this test proves nothing"
    # Whitespace-stripped so the check is exact on the value while staying
    # agnostic to the serializer's separators — this payload uses compact
    # `json.dumps` separators, the planner ctx uses `indent=2`.
    prompt = "".join((calls[0].get("user_prompt") or "").split())
    assert f'"source_of_truth":"{expected}"' in prompt, (
        f"the pr_writer payload does not carry {expected!r}; the consumer may "
        "call the helper and discard its result")
