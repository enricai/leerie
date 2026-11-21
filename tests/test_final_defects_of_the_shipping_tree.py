"""The final conformance's verdict is of the tree that ships (DESIGN §9; lane
SHIP, the B11 replay's B13 finding).

The held-out gate's repair rounds and the delivery re-drive commit after
the final conformance, and nothing measured its residual solution defects
again. Their veto (V11) read the pass's own record whatever tree it was of.
In 4 of the 5 finished runs on record whose residual defects vetoed, it was
an earlier tree than the one that ships (35999e65, 7fc42529, 2cf0b060,
b8066b2c).

Replay on real git and a real run repository: the real final conformance
leaves a defect, a later commit (a re-drive group) moves the run branch, the
re-decision runs, and the outcome is decided as finalize decides it.
Stubbed: `claude_p` only.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_run_final_conformance import _clean_result, env  # noqa: F401

DEFECT = {"kind": "unhandled_case", "where": "src.py:1",
          "concrete_case": "f(None) returns None", "actionable": True,
          "why_ships_a_defect": "callers read the result"}


def _commit(tree: Path, name: str, text: str, msg: str) -> None:
    (tree / name).write_text(text)
    _git(tree, "add", "-A")
    _git(tree, "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm",
         msg)


def _stub(leerie, monkeypatch, replies: list):
    """Each conformer call takes the next reply: a result, an exception to
    raise, or a callable run on its cwd that returns the result. Records
    each call with its cwd's `src.py` and HEAD."""
    calls: list[dict] = []

    async def fake(**kw):
        assert kw["schema_key"] == "conformer", kw["schema_key"]
        cwd = Path(kw["cwd"])
        calls.append({**kw, "src": (cwd / "src.py").read_text(),
                      "head": _git(cwd, "rev-parse", "HEAD")})
        reply = replies.pop(0) if replies else _clean_result()
        if isinstance(reply, BaseException):
            raise reply
        return reply(cwd) if callable(reply) else reply
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def _final_pass(env, monkeypatch, *, defects: bool = True):  # noqa: F811
    """The real final conformance: every round reports the defect unfixed."""
    leerie = env["leerie"]
    res = _clean_result(solution_defects=[DEFECT] if defects else [])
    calls = _stub(leerie, monkeypatch,
                  [res] * env["caps"]["conformance_rounds"])
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))
    return calls


def _redrive_fixes(env) -> str:  # noqa: F811
    """A step after the pass (a re-drive group) fixes f on staging."""
    _commit(env["staging"], "src.py", "def f():\n    return 1\n",
            "re-drive: f returns 1")
    return env["leerie"]._shipping_commit(env["st"])


def _redecide(env):  # noqa: F811
    return asyncio.run(env["leerie"]._redecide_final_defects(
        env["run_dir"], env["st"], env["caps"], env["models"],
        env["efforts"]))


def _outcome(env) -> tuple[str, list[str]]:  # noqa: F811
    st = env["st"]
    out = env["leerie"]._decide_outcome(st, any_commits=True)
    return out, list(st.data["outcome_reasons"])


def _defect_reasons(reasons: list[str]) -> list[str]:
    return [r for r in reasons if "solution defect" in r]


def test_a_defect_the_redrive_fixed_does_not_veto_the_tree_that_ships(
        env, monkeypatch):  # noqa: F811
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch)
    fin = st.data["conformance"]["_final"]
    assert [d["concrete_case"] for d in fin["residual_defects"]] == [
        DEFECT["concrete_case"]]
    shipping = _redrive_fixes(env)
    tree = leerie._shipping_tree(st)
    assert fin["tree_sha"] and fin["tree_sha"] != tree
    calls = _stub(leerie, monkeypatch, [_clean_result()])
    rec = _redecide(env)
    assert len(calls) == 1
    call = calls[0]
    # One final-conformer round, on the commit that ships, in its own
    # checkout (never staging), removed afterwards.
    assert call["head"] == shipping
    assert call["src"] == "def f():\n    return 1\n"
    assert Path(call["cwd"]).resolve() != env["staging"].resolve()
    assert not Path(call["cwd"]).exists()
    assert call["user_prompt"].startswith(
        "Run the post-integration whole-tree conformance phase")
    assert f"DIFF_BASE: {st.data['base_sha']}" in call["user_prompt"]
    assert call["sid"] == "final-conformer-shipped"
    assert rec == {"sha": shipping, "tree": tree, "result": rec["result"],
                   "residual_defects": []}
    assert st.data["conformance"]["_shipped"] is rec
    out, reasons = _outcome(env)
    assert _defect_reasons(reasons) == [], reasons
    assert out == "done", reasons


def test_a_defect_it_fixes_in_its_own_checkout_is_of_the_tree_that_ships(
        env, monkeypatch):  # noqa: F811
    """Its commits do not ship: a defect it reports `fixed` is still on the
    tree that ships, and the run branch does not move."""
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch)
    _commit(env["staging"], "other.py", "x = 1\n", "re-drive: unrelated")
    shipping = leerie._shipping_commit(st)

    def fixes_in_place(cwd: Path) -> dict:
        _commit(cwd, "src.py", "def f():\n    return 1\n",
                "conformer: f returns 1")
        return _clean_result(solution_defects=[{**DEFECT, "fixed": True}])
    _stub(leerie, monkeypatch, [fixes_in_place])
    rec = _redecide(env)
    assert [d["concrete_case"] for d in rec["residual_defects"]] == [
        DEFECT["concrete_case"]]
    assert leerie._shipping_commit(st) == shipping
    assert (env["staging"] / "src.py").read_text() == "def f():\n    pass\n"
    out, reasons = _outcome(env)
    assert out == "unconverged"
    [why] = _defect_reasons(reasons)
    assert why.startswith("1 solution defect(s) on the tree that ships, "
                          "found by the final conformance decided again")
    assert "not the tree that ships" not in why


def test_nothing_runs_when_the_tree_did_not_move(env, monkeypatch):  # noqa: F811
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch)
    calls = _stub(leerie, monkeypatch, [])
    assert _redecide(env) is None
    assert calls == [] and "_shipped" not in st.data["conformance"]
    out, reasons = _outcome(env)
    [why] = _defect_reasons(reasons)
    assert why.startswith("1 solution defect(s) the final conformance left "
                          "unfixed after its last round")


def test_nothing_runs_for_a_clean_pass_on_a_moved_tree(env, monkeypatch):  # noqa: F811
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch, defects=False)
    _redrive_fixes(env)
    calls = _stub(leerie, monkeypatch, [])
    assert _redecide(env) is None
    assert calls == [] and "_shipped" not in st.data["conformance"]


def test_a_crashed_redecision_leaves_the_pass_verdict_of_its_own_tree(
        env, monkeypatch):  # noqa: F811
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch)
    _redrive_fixes(env)
    _stub(leerie, monkeypatch, [leerie.WorkerError("boom")])
    rec = _redecide(env)
    assert "boom" in rec["error"] and "residual_defects" not in rec
    out, reasons = _outcome(env)
    assert out == "unconverged"
    [why] = _defect_reasons(reasons)
    old = st.data["conformance"]["_final"]["tree_sha"]
    assert f"on tree {old[:12]}, not the tree that ships" in why


def test_a_resume_reuses_the_record_of_the_tree_that_ships(env, monkeypatch):  # noqa: F811
    leerie, st = env["leerie"], env["st"]
    _final_pass(env, monkeypatch)
    _redrive_fixes(env)
    calls = _stub(leerie, monkeypatch, [_clean_result()])
    rec = _redecide(env)
    assert _redecide(env) is rec and len(calls) == 1
    # A record of another tree is not reused, and not read by the outcome.
    _commit(env["staging"], "src.py", "def f():\n    return None\n",
            "later: f returns None")
    assert leerie._final_defects_record(st)[0] is st.data[
        "conformance"]["_final"]
    _stub(leerie, monkeypatch, [_clean_result(solution_defects=[DEFECT])])
    again = _redecide(env)
    assert again is not rec
    assert again["tree"] == leerie._shipping_tree(st)
    assert len(again["residual_defects"]) == 1


# --- The faithful re-derivation: the re-decision runs the pass's own
# set-aside adjudication, scoped to the items still unmet on the shipped
# tree (lane SHIP, BF bench F1/F2). Without the adjudication the
# intent/set-aside defect class is silently dropped (false-done); without
# the scope a finding the re-drive met is re-raised (false-veto).

def _set_aside_env(env, *, still_unmet: bool):
    """A PR finding on required item 0 the pass set aside and intent refused
    (the task requires it, settled `yes`); `_final` left it as a residual
    defect on the pre-move tree, then a re-drive moved the branch. Whether
    item 0 is STILL UNMET on the shipped tree is `still_unmet` — the only
    difference between the two directions."""
    leerie, st = env["leerie"], env["st"]
    pre_tree = leerie._shipping_tree(st)
    st.data["required_items"] = [{"id": "0", "text": "price child ages"}]
    st.data["pr_review"] = {"findings": [{
        "verified": True, "item_known": True, "item_id_norm": "0",
        "item_id": "0", "kind": "unmet_requirement", "path": "src.py",
        "line_start": 1, "line_end": 1, "quote": "",
        "explanation": "children are not priced"}]}
    finding = {"finding_id": "pr-0", "kind": "unmet_requirement", "item": 0,
               "where": "src.py:1-1", "text": "children are not priced"}
    st.data["intent_questions"] = {"intent-finding-pr-0": {
        "qid": "intent-finding-pr-0", "source": "set_aside",
        "question": "does the task require pr-0?",
        "context": {"finding": finding, "reason_kind": "larger_change",
                    "reason_kind_known": True, "quote": ""},
        "verdict": {"status": "settled", "trusted": True, "answer": "yes"},
        "at": "t"}}
    # The pass's residual defect, on the pre-move tree, so the re-decision
    # runs; the re-drive then moves the branch.
    st.data["conformance"] = {"_final": {
        "result": _clean_result(), "warnings": [], "blocked": False,
        "tree_sha": pre_tree, "round_starts": [],
        "end_sha": leerie._shipping_commit(st),
        "residual_defects": [DEFECT]}}
    st.save()
    shipping = _redrive_fixes(env)
    ship_tree = leerie._shipping_tree(st)
    unmet_row = [{"item_index": 0, "item": "price child ages",
                  "evidence": "children not priced"}]
    st.data["delivery_gate"] = {
        "unmet_before": unmet_row,
        "unmet_final": unmet_row if still_unmet else [],
        "final_tree": ship_tree}
    st.save()
    return shipping, ship_tree


def _set_aside_reply(cwd):  # noqa: F811
    """The conformer sets pr-0 aside (it cannot verify the live-site finding
    is met here) and reports no solution defect of its own."""
    return _clean_result(solution_defects=[], set_aside=[
        {"finding_id": "pr-0", "reason_kind": "larger_change",
         "quote": "children are not priced"}])


def test_a_still_unmet_set_aside_the_task_requires_is_a_defect_on_the_ship_tree(
        env, monkeypatch):  # noqa: F811
    """Faithful re-derivation: the conformer sets a task-required finding
    aside, the pass's adjudication (reusing the settled `yes`) refuses it,
    and it vetoes — the intent/set-aside class the bare re-review dropped."""
    leerie, st = env["leerie"], env["st"]
    shipping, ship_tree = _set_aside_env(env, still_unmet=True)
    calls = _stub(leerie, monkeypatch, [_set_aside_reply])
    rec = _redecide(env)
    assert len(calls) == 1
    kinds = [d["kind"] for d in rec["residual_defects"]]
    assert leerie.SET_ASIDE_REFUSED_KIND in kinds, rec["residual_defects"]
    [d] = [d for d in rec["residual_defects"]
           if d["kind"] == leerie.SET_ASIDE_REFUSED_KIND]
    assert "pr-0" in d["concrete_case"]
    # The finding on the still-unmet item was shown to the conformer.
    assert "children are not priced" in calls[0]["user_prompt"]
    out, reasons = _outcome(env)
    assert out == "unconverged"
    assert _defect_reasons(reasons), reasons


def test_a_set_aside_the_redrive_met_is_scoped_out_and_does_not_veto(
        env, monkeypatch):  # noqa: F811
    """The load-bearing guard: the SAME set-aside the conformer makes is NOT
    a defect when item 0 is met on the shipped tree (`unmet_final` excludes
    it). The finding is scoped out of both the prompt and the adjudication,
    so the stale veto does not fire (BF bench F2, 2cf0b060)."""
    leerie, st = env["leerie"], env["st"]
    shipping, ship_tree = _set_aside_env(env, still_unmet=False)
    calls = _stub(leerie, monkeypatch, [_set_aside_reply])
    rec = _redecide(env)
    assert len(calls) == 1
    assert rec["residual_defects"] == [], rec["residual_defects"]
    # The finding on the now-met item was not shown to the conformer.
    assert "children are not priced" not in calls[0]["user_prompt"]
    out, reasons = _outcome(env)
    assert _defect_reasons(reasons) == [], reasons
    assert out == "done", reasons


def test_a_new_set_aside_the_pass_never_settled_is_not_asked_and_not_a_defect(
        env, monkeypatch):  # noqa: F811
    """Reuse-only: the shipping-tree conformer sets aside a finding the pass
    never set aside (no settled intent verdict). The re-decision runs AFTER
    the last intent stop, so it must NOT register/ask a new question — it
    skips the set-aside. Not a silent false-done: the finding's item is in
    `unmet_final`, which `_decide_outcome` vetoes on in its own right."""
    leerie, st = env["leerie"], env["st"]
    _set_aside_env(env, still_unmet=True)
    # The pass settled nothing about pr-0: no intent question for it.
    st.data["intent_questions"] = {}
    st.save()
    schemas: list[str] = []

    async def fake(**kw):
        schemas.append(kw["schema_key"])
        # A resolver call here is the bug: the re-decision asks a new
        # question at outcome time. Fail loudly if it happens.
        assert kw["schema_key"] == "conformer", (
            f"re-decision opened a new question: {kw['schema_key']}")
        return _set_aside_reply(Path(kw["cwd"]))
    monkeypatch.setattr(leerie, "claude_p", fake)
    rec = _redecide(env)
    assert schemas == ["conformer"], schemas
    assert rec["residual_defects"] == [], rec["residual_defects"]
    # No new intent question was left open after the last stop.
    assert "intent-finding-pr-0" not in (st.data.get("intent_questions") or {})
    out, reasons = _outcome(env)
    assert _defect_reasons(reasons) == [], reasons
