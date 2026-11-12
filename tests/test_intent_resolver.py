"""Intent conflicts are settled with evidence, else asked (DESIGN §11).

Three sources ask the `intent_resolver` a yes/no question: the plan against
the required items (`plan_item_checker`), the final conformer's typed
`set_aside` of a finding on a required item, and a contradiction judge's
unsettled result (`resolve_intent_conflict`). Code checks the resolver's
quotes; a settled, trusted answer is applied; anything else stops and asks
(a terminal: once; unattended: `EXIT_NEEDS_ANSWERS` with the questions
written, and `resume --answers` applies them).

Real path: a real git repository, the run's own repository, a real staging
worktree and a real checkout of the base commit for the resolver; the real
quote verification (`git show <base>:<path>`, the task, the task's copied
files); the real `_run_final_conformance`, `_write_plan` and
`_absorb_supplied_answers`. Only `claude_p` is stubbed.
"""
from __future__ import annotations

import argparse
import asyncio
import builtins
import inspect
import json
import subprocess
import sys

import pytest

from tests.conftest import run_git_repo_first
from tests.test_run_final_conformance import _clean_result, env  # noqa: F401

REPORT = ("Also affects `303 See Other`, where the rewrite is mandatory - so "
          "any POST to an endpoint answering 303 can never work.")
TASK = "We need to fix the issues reported by docs/report.md"
QID = "intent-plan-bugfix-001"


def _models(leerie) -> dict:
    return {w: leerie.MODEL_DEFAULT_PER_WORKER.get(w, leerie.MODEL_DEFAULT)
            for w in leerie.WORKER_TYPES}


def _efforts(leerie) -> dict:
    return {w: leerie.EFFORT_DEFAULT_PER_WORKER.get(w)
            for w in leerie.WORKER_TYPES}


def _setup(env):  # noqa: F811
    """The task, its copied report and two required items."""
    st = env["st"]
    st.data["task"] = TASK
    st.data["required_items"] = [
        {"item": "preserves method, headers and body across 301/302/303 "
                 "redirects",
         "parts": ["POST is re-issued with body at the Location"]},
        "the public API is unchanged"]
    st.save()
    tf = env["run_dir"] / "task-files" / "docs"
    tf.mkdir(parents=True)
    (tf / "report.md").write_text(f"# Report\n\n{REPORT}\n")
    return st


def _subtasks() -> dict:
    return {"bugfix-001": {
        "id": "bugfix-001", "title": "Fix redirects",
        "intent": "Route POSTs through the follower.",
        "scope_note": "",
        "investigation_notes": "Preserve all existing behavior (303->GET, "
                               "hop cookies, 5-hop cap). Read the follower.",
        "success_criteria_seed": "redirect.test.ts passes unchanged",
        "verify": [{"criterion": "pinned", "argv": ["npx", "vitest", "run",
                                                    "redirect.test.ts"]},
                   {"criterion": "new", "argv": ["npx", "vitest", "run",
                                                 "follow.test.ts"]}]}}


CONFLICTS = [
    {"item_index": 0, "item_quote": "across 301/302/303 redirects",
     "subtask_id": "bugfix-001", "field": "investigation_notes",
     "plan_quote": "Preserve all existing behavior (303->GET, hop cookies, "
                   "5-hop cap).",
     "kind": "preserve_vs_change", "test_file": ""},
    {"item_index": 0, "item_quote": "POST is re-issued with body",
     "subtask_id": "bugfix-001", "field": "verify",
     "plan_quote": "pinned: npx vitest run redirect.test.ts",
     "kind": "pinned_test_contradicts", "test_file": "redirect.test.ts"},
    # A quote the plan does not hold: dropped, never asked.
    {"item_index": 0, "item_quote": "across 301/302/303 redirects",
     "subtask_id": "bugfix-001", "field": "intent",
     "plan_quote": "Keep 303 as a GET.", "kind": "preserve_vs_change",
     "test_file": ""},
]


def _verdict(answer="yes", status="settled", *, code_quote="# repo",
             code_path="README.md"):
    return {"decision": f"{answer.capitalize()}. The report counts 303.",
            "answer": answer, "status": status,
            "evidence": [
                {"source": "report", "quote": REPORT,
                 "locator": "report.md"},
                {"source": "task", "quote": "fix the issues reported",
                 "locator": "task.md"},
                {"source": "code", "quote": code_quote,
                 "locator": f"{code_path}:1"},
                {"source": "web", "quote": "anything",
                 "locator": "https://example.invalid/rfc"}]}


def _stub(leerie, monkeypatch, *, conflicts=CONFLICTS, verdict=None,
          conformer=None):
    calls: list[dict] = []

    async def fake(**kw):
        sk = kw["schema_key"]
        rec = dict(kw)
        if sk == "intent_resolver":
            # What the resolver's checkout is: the base commit.
            rec["head"] = subprocess.run(
                ["git", "rev-parse", "HEAD"], cwd=kw["cwd"],
                capture_output=True, text=True).stdout.strip()
        calls.append(rec)
        if sk == "plan_item_checker":
            return {"conflicts": conflicts}
        if sk == "intent_resolver":
            return verdict if verdict is not None else _verdict()
        if sk == "conformer":
            return conformer() if callable(conformer) else _clean_result()
        if sk == "pr_reviewer":
            return {"findings": []}
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake)
    return calls


def _no_tty(monkeypatch):
    class _In:
        def isatty(self):
            return False
    monkeypatch.setattr(sys, "stdin", _In())


def _plan(leerie, env, subtasks):  # noqa: F811
    st = env["st"]
    asyncio.run(leerie._plan_intent_check(
        st, subtasks, env["caps"], _models(leerie), _efforts(leerie)))


# === (a) the plan against the items ========================================

def test_a_settled_yes_amends_the_spec_the_implementer_reads(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch)
    subtasks = _subtasks()
    _plan(leerie, env, subtasks)

    check = st.data["plan_item_check"]
    assert len(check["conflicts"]) == 2 and check["rejected"] == 1
    (chk,) = [c for c in calls if c["schema_key"] == "plan_item_checker"]
    assert (chk["model"], chk["effort"]) == ("opus", "high")
    assert chk["allowed_tools"] == leerie.PAYLOAD_ONLY_TOOLS
    assert "[item 0] preserves method" in chk["user_prompt"]
    assert "    part: POST is re-issued" in chk["user_prompt"]
    assert "pinned: npx vitest run redirect.test.ts" in chk["user_prompt"]

    (res,) = [c for c in calls if c["schema_key"] == "intent_resolver"]
    assert (res["model"], res["effort"]) == ("opus", "high")
    assert res["autonomous"] is False
    assert res["head"] == st.data["base_sha"]
    assert res["cwd"] != str(env["staging"])
    assert res["add_dirs"] == [str(env["run_dir"] / "task-files")]
    assert "303->GET" in res["user_prompt"] and TASK in res["user_prompt"]
    assert str(env["run_dir"] / "task-files" / "docs" / "report.md") in \
        res["user_prompt"]

    v = st.data["intent_questions"][QID]["verdict"]
    assert [e["ok"] for e in v["evidence"]] == [True, True, True, None]
    assert v["trusted"] is True
    assert leerie._intent_decision(st, QID) == "yes"

    _no_tty(monkeypatch)
    leerie._stop_for_intent_questions(st)        # decided: no stop
    leerie._apply_plan_intent_rulings(st, subtasks)
    leerie._write_plan(env["run_dir"], TASK, st, subtasks, [["bugfix-001"]])
    spec = json.loads((env["run_dir"] / "subtasks" / "bugfix-001.json")
                      .read_text())
    assert "303->GET" not in spec["investigation_notes"]
    assert spec["investigation_notes"].startswith(
        f"[intent ruling {QID}: Yes. The report counts 303.]")
    assert spec["investigation_notes"].endswith("Read the follower.")
    assert [c["criterion"] for c in spec["verify"]] == ["new"]
    (ruling,) = spec["_intent_rulings"]
    assert ruling["qid"] == QID and ruling["decided_by"] == "resolver"
    # Applying again (a resume re-applies to the same plan) changes nothing.
    before = json.dumps(subtasks, sort_keys=True)
    leerie._apply_plan_intent_rulings(st, subtasks)
    assert json.dumps(subtasks, sort_keys=True) == before


def test_a_settled_no_leaves_the_plan(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _stub(leerie, monkeypatch, verdict=_verdict("no"))
    subtasks = _subtasks()
    before = json.dumps(subtasks, sort_keys=True)
    _plan(leerie, env, subtasks)
    assert leerie._intent_decision(st, QID) == "no"
    _no_tty(monkeypatch)
    leerie._stop_for_intent_questions(st)
    leerie._apply_plan_intent_rulings(st, subtasks)
    assert json.dumps(subtasks, sort_keys=True) == before


def test_no_conflict_asks_nothing(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch, conflicts=[])
    _plan(leerie, env, _subtasks())
    assert [c["schema_key"] for c in calls] == ["plan_item_checker"]
    assert not st.data.get("intent_questions")
    # Recorded once: a resume does not ask the checker again.
    _plan(leerie, env, _subtasks())
    assert [c["schema_key"] for c in calls] == ["plan_item_checker"]


def test_a_quote_only_the_run_wrote_is_not_trusted_and_an_unattended_run_stops(
        env, monkeypatch, tmp_path):  # noqa: F811
    """`src.py` exists on the run branch, not at the base: a resolver quoting
    it is not trusted, so the question stays open, the run exits 10 with it
    written down, and the resume applies the user's answer."""
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch, verdict=_verdict(
        code_quote="def f():", code_path="src.py"))
    subtasks = _subtasks()
    _plan(leerie, env, subtasks)
    v = st.data["intent_questions"][QID]["verdict"]
    assert [e["ok"] for e in v["evidence"]][:3] == [True, True, False]
    assert v["trusted"] is False and leerie._intent_decision(st, QID) is None

    _no_tty(monkeypatch)
    with pytest.raises(SystemExit) as ex:
        leerie._stop_for_intent_questions(st)
    assert ex.value.code == leerie.EXIT_NEEDS_ANSWERS == 10
    pending = json.loads((env["run_dir"] / "pending-questions.json")
                         .read_text())["questions"]
    (q,) = pending
    assert q["id"] == QID and q["choices"] == ["yes", "no"]
    assert "303->GET" in q["question"]
    assert "quotes not verified" in q["why_underivable"]
    # The quote that failed is not shown as evidence.
    assert [e["source"] for e in q["evidence"]] == ["report", "task", "web"]

    # An answer that is not a choice leaves it open.
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({QID: "maybe"}))
    leerie._absorb_supplied_answers(argparse.Namespace(answers=str(bad)),
                                    st, env["run_dir"])
    with pytest.raises(SystemExit):
        leerie._stop_for_intent_questions(st)

    # The resume: the answer is applied, the resolver is not asked again.
    good = tmp_path / "answers.json"
    good.write_text(json.dumps({QID: "yes"}))
    leerie._absorb_supplied_answers(argparse.Namespace(answers=str(good)),
                                    st, env["run_dir"])
    n = len(calls)
    _plan(leerie, env, subtasks)
    assert len(calls) == n
    leerie._stop_for_intent_questions(st)
    leerie._apply_plan_intent_rulings(st, subtasks)
    sub = subtasks["bugfix-001"]
    assert "303->GET" not in sub["investigation_notes"]
    assert sub["_intent_rulings"][0]["decided_by"] == "user"


def test_on_a_terminal_it_is_asked_once(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _stub(leerie, monkeypatch, verdict=_verdict(status="unsettled"))
    _plan(leerie, env, _subtasks())

    class _Tty:
        def isatty(self):
            return True
    prompts: list[str] = []
    replies = iter(["maybe", "No"])

    def fake_input(prompt=""):
        prompts.append(prompt)
        return next(replies)
    monkeypatch.setattr(sys, "stdin", _Tty())
    monkeypatch.setattr(builtins, "input", fake_input)
    leerie._stop_for_intent_questions(st)
    assert prompts == ["  (yes/no) > ", "  (yes/no) > "]
    assert st.data["answers"][QID] == "no"
    assert leerie._intent_decision(st, QID) == "no"
    leerie._stop_for_intent_questions(st)        # nothing left to ask
    assert len(prompts) == 2


def test_a_crashed_resolver_leaves_the_question_open_and_is_asked_again(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch)

    async def crash(**kw):
        if kw["schema_key"] == "intent_resolver":
            raise leerie.WorkerError("boom")
        return {"conflicts": CONFLICTS}
    monkeypatch.setattr(leerie, "claude_p", crash)
    _plan(leerie, env, _subtasks())
    assert "boom" in st.data["intent_questions"][QID]["verdict"]["error"]
    assert leerie._intent_decision(st, QID) is None
    calls = _stub(leerie, monkeypatch)
    _plan(leerie, env, _subtasks())
    assert [c["schema_key"] for c in calls] == ["intent_resolver"]
    assert leerie._intent_decision(st, QID) == "yes"


@pytest.fixture()
def fresh(leerie, tmp_path, monkeypatch):
    """A fresh run at the plan check: the user's checkout, the run's own
    repository and planning worktree made by the real code, and no
    `base_sha` and no staging (`phase_execute` cuts the run branch later).
    The user's branch then moves on, so the commit the planning worktree
    holds is not the branch's tip."""
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git_repo_first(repo, "init", "-q", "-b", "main", ".")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    (repo / "client.ts").write_text("// 303 is followed as a GET\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-qm", "init")
    monkeypatch.chdir(repo)
    state = tmp_path / "state"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    st = leerie.State(state, "fresh-plan-1", repo_root=repo)
    st.data.update(task=TASK, working_branch="main",
                   answers={"source_of_truth": "codebase"},
                   required_items=[
                       {"item": "preserves method, headers and body across "
                                "301/302/303 redirects",
                        "parts": ["POST is re-issued with body at the "
                                  "Location"]}])
    st.save()
    tf = st.run_dir / "task-files" / "docs"
    tf.mkdir(parents=True)
    (tf / "report.md").write_text(f"# Report\n\n{REPORT}\n")
    asyncio.run(leerie._ensure_run_repo_or_die(st))
    planning = asyncio.run(leerie._ensure_planning_worktree(st))
    head = run_git_repo_first(repo, "rev-parse", "HEAD").stdout.strip()
    (repo / "client.ts").write_text("// 303 keeps the method\n")
    run_git_repo_first(repo, "commit", "-qam", "later")
    return {"leerie": leerie, "st": st, "planning": planning, "head": head,
            "tip": run_git_repo_first(repo, "rev-parse",
                                      "HEAD").stdout.strip(),
            "caps": dict(leerie.DEFAULT_CAPS)}


@pytest.mark.real_planning_worktree
def test_a_fresh_runs_plan_conflict_is_settled_on_the_planning_commit(
        fresh, monkeypatch):
    """The plan check runs before `phase_execute` records `base_sha`, so on
    a fresh run the resolver's commit is the one the planning worktree
    holds, the commit the planner read. Reading `base_sha` alone sent every
    plan conflict to the user unsettled ("no base commit or tree to check
    out")."""
    leerie, st = fresh["leerie"], fresh["st"]
    assert not st.data.get("base_sha")
    assert not (st.run_dir / "worktrees" / "staging").exists()
    assert fresh["tip"] != fresh["head"]
    calls = _stub(leerie, monkeypatch, verdict=_verdict(
        code_quote="// 303 is followed as a GET", code_path="client.ts"))
    asyncio.run(leerie._plan_intent_check(
        st, _subtasks(), fresh["caps"], _models(leerie), _efforts(leerie)))

    (res,) = [c for c in calls if c["schema_key"] == "intent_resolver"]
    assert res["head"] == fresh["head"]
    assert res["sid"] == f"intent-resolver-{QID}"
    assert fresh["head"] in res["user_prompt"]
    v = st.data["intent_questions"][QID]["verdict"]
    assert "error" not in v
    # The code quote holds at the planning commit, not at the branch's tip.
    assert [e["ok"] for e in v["evidence"]] == [True, True, True, None]
    assert v["trusted"] is True
    assert leerie._intent_decision(st, QID) == "yes"
    _no_tty(monkeypatch)
    leerie._stop_for_intent_questions(st)        # decided: no stop


# === (b) the final conformer's set-aside ====================================

FINDING = {"item_id": "item 0", "kind": "unmet_requirement", "path": "src.py",
           "line_start": 1, "line_end": 2, "quote": "def f():\n    pass",
           "item_span_quote": "across 301/302/303",
           "explanation": "On a 303 the follower still rewrites to GET."}
NO_ITEM = {**FINDING, "item_id": None, "kind": "bug",
           "item_span_quote": None, "explanation": "unrelated bug"}


def _review(leerie, env, monkeypatch, findings):  # noqa: F811
    async def reviewer(**kw):
        return {"findings": findings}
    monkeypatch.setattr(leerie, "claude_p", reviewer)
    asyncio.run(leerie._run_pr_review(env["run_dir"], env["st"], env["caps"],
                                      _models(leerie), _efforts(leerie)))


def _set_aside(fid="pr-0", kind="deliberate"):
    return lambda: _clean_result(set_aside=[{
        "finding_id": fid, "reason_kind": kind,
        "quote": "A 303 is followed as a GET, preserved deliberately."}])


def _final(leerie, env, rounds=1):  # noqa: F811
    env["caps"]["conformance_rounds"] = rounds
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], env["st"], env["caps"], _models(leerie),
        _efforts(leerie)))
    return env["st"].data["conformance"]["_final"]


def test_a_set_aside_the_task_refuses_stays_open_and_is_driven(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _review(leerie, env, monkeypatch, [FINDING])
    calls = _stub(leerie, monkeypatch, conformer=_set_aside())
    fin = _final(leerie, env, rounds=2)

    conf = [c for c in calls if c["schema_key"] == "conformer"]
    assert len(conf) == 2
    assert '"finding_id": "pr-0"' in conf[0]["user_prompt"]
    assert "`set_aside`" in conf[0]["user_prompt"]
    # Round 0's set-aside was refused: round 1 is handed it as unfixed.
    assert "set_aside_refused" in conf[1]["user_prompt"]
    res = [c for c in calls if c["schema_key"] == "intent_resolver"]
    assert len(res) == 1                       # asked once, reused after
    assert "On a 303 the follower still rewrites to GET." in \
        res[0]["user_prompt"]
    assert "`deliberate`" in res[0]["user_prompt"]
    (row,) = fin["residual_defects"]
    assert row["kind"] == "set_aside_refused"
    assert row["where"] == "src.py:1-2"
    assert "[pr-0]" in row["concrete_case"]
    q = st.data["intent_questions"]["intent-finding-pr-0"]
    assert q["driven"] is True and q["source"] == "set_aside"
    # Nothing open: no stop, and the after-pass apply adds nothing twice.
    _no_tty(monkeypatch)
    leerie._stop_for_intent_questions(st)
    leerie._apply_set_aside_answers(st)
    assert len(fin["residual_defects"]) == 1
    # The run is unconverged while it is open, and the PR names the ruling.
    assert "set_aside_refused" in leerie._residual_defects_reason(st)
    sec = leerie._format_pr_end_state_section(st.data)
    assert "## Intent decisions" in sec
    assert ("**yes**, settled by the intent resolver" in sec
            and "intent-finding-pr-0" in sec)


def test_a_malformed_later_round_does_not_drop_the_refusal(
        env, monkeypatch):  # noqa: F811
    """barnacle e7512406 replay: round 0's set-aside was refused, round 1's
    result was malformed (a test path that is not in the tree) and the
    refusal vanished with it. What the last settled round left open stays
    residual."""
    leerie = env["leerie"]
    _setup(env)
    _review(leerie, env, monkeypatch, [FINDING])
    replies = iter([_set_aside()(), _clean_result(file_updates=[
        {"kind": "tests", "path": "no/such.test.ts", "reason": "covers it"}])])
    _stub(leerie, monkeypatch, conformer=lambda: next(replies))
    fin = _final(leerie, env, rounds=3)
    assert any("malformed result" in w for w in fin["warnings"])
    (row,) = fin["residual_defects"]
    assert row["kind"] == "set_aside_refused" and "[pr-0]" in \
        row["concrete_case"]


def test_a_set_aside_the_task_allows_stands(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _review(leerie, env, monkeypatch, [FINDING])
    _stub(leerie, monkeypatch, conformer=_set_aside(kind="out_of_scope"),
          verdict=_verdict("no"))
    fin = _final(leerie, env)
    assert not fin.get("residual_defects")
    q = st.data["intent_questions"]["intent-finding-pr-0"]
    assert q["context"]["reason_kind_known"] is True
    assert leerie._intent_decision(st, "intent-finding-pr-0") == "no"
    assert "**no**, settled by the intent resolver" in \
        leerie._format_pr_end_state_section(st.data)


def test_a_set_aside_on_no_required_item_is_not_asked(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _review(leerie, env, monkeypatch, [NO_ITEM])
    calls = _stub(leerie, monkeypatch, conformer=_set_aside())
    fin = _final(leerie, env)
    assert not [c for c in calls if c["schema_key"] == "intent_resolver"]
    assert not fin.get("residual_defects")
    assert not st.data.get("intent_questions")


def test_an_unsettled_set_aside_stops_after_the_pass_and_the_answer_drives_it(
        env, monkeypatch, tmp_path):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    _review(leerie, env, monkeypatch, [FINDING])
    _stub(leerie, monkeypatch, conformer=_set_aside(),
          verdict=_verdict(status="unsettled"))
    fin = _final(leerie, env)
    assert not fin.get("residual_defects")      # not decided best-effort
    qid = "intent-finding-pr-0"
    _no_tty(monkeypatch)
    with pytest.raises(SystemExit) as ex:
        leerie._stop_for_intent_questions(st)
    assert ex.value.code == 10
    (q,) = json.loads((env["run_dir"] / "pending-questions.json")
                      .read_text())["questions"]
    assert q["id"] == qid and "Does the task require the run to resolve" in q["question"]
    ans = tmp_path / "answers.json"
    ans.write_text(json.dumps({qid: "yes"}))
    leerie._absorb_supplied_answers(argparse.Namespace(answers=str(ans)),
                                    st, env["run_dir"])
    leerie._stop_for_intent_questions(st)
    leerie._apply_set_aside_answers(st)
    leerie._apply_set_aside_answers(st)
    (row,) = st.data["conformance"]["_final"]["residual_defects"]
    assert row["kind"] == "set_aside_refused"
    assert "the user answered" in row["why_ships_a_defect"]
    assert "**yes**, answered by the user" in \
        leerie._format_pr_end_state_section(st.data)


def test_a_detection_finding_has_an_id_and_its_set_aside_is_asked(
        env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch, conformer=_set_aside("unmet-1"))
    env["caps"]["conformance_rounds"] = 1
    asyncio.run(leerie._run_final_conformance(
        env["run_dir"], st, env["caps"], _models(leerie), _efforts(leerie),
        unmet_required_items=[{"item_index": 1,
                               "item": "the public API is unchanged",
                               "evidence": "f was renamed"}]))
    conf = [c for c in calls if c["schema_key"] == "conformer"]
    assert "(finding_id unmet-1) the public API is unchanged" in \
        conf[0]["user_prompt"]
    (res,) = [c for c in calls if c["schema_key"] == "intent_resolver"]
    assert "the public API is unchanged" in res["user_prompt"]
    (row,) = st.data["conformance"]["_final"]["residual_defects"]
    assert row["where"] == "required item 1"


# === (c) the contradiction judge's hand-off =================================

def test_the_contradiction_hook(env, monkeypatch):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    calls = _stub(leerie, monkeypatch, verdict=_verdict(status="unsettled"))
    got = asyncio.run(leerie.resolve_intent_conflict(
        st, "intent-contradiction-x", "Does the task require 303 to keep "
        "the method?", {"test": "redirect.test.ts"}, env["caps"],
        _models(leerie), _efforts(leerie)))
    assert got is None
    assert st.data["intent_questions"]["intent-contradiction-x"][
        "source"] == "contradiction"
    _no_tty(monkeypatch)
    with pytest.raises(SystemExit):
        leerie._stop_for_intent_questions(st)
    st.data["answers"]["intent-contradiction-x"] = "yes"
    assert asyncio.run(leerie.resolve_intent_conflict(
        st, "intent-contradiction-x", "q", {}, env["caps"], _models(leerie),
        _efforts(leerie))) == "yes"
    assert len([c for c in calls if c["schema_key"] == "intent_resolver"]) == 1


# === quote verification and wiring ==========================================

def test_history_and_path_rules(env):  # noqa: F811
    leerie = env["leerie"]
    st = _setup(env)
    tree = env["staging"]
    base = st.data["base_sha"]
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=tree,
                          capture_output=True, text=True).stdout.strip()
    files = leerie._task_file_copies(st)
    kw = dict(task=TASK, task_files=files,
              files_root=env["run_dir"] / "task-files", tree=tree, base=base)
    v = leerie._verify_intent_evidence
    # A commit's diff, raw or without its markers.
    assert v({"source": "history", "quote": "def f():",
              "locator": head[:9]}, **kw) is True
    assert v({"source": "history", "quote": "def g():",
              "locator": head[:9]}, **kw) is False
    # Code is read at the base, never at the tree's HEAD: `src.py` is the
    # run's own file.
    assert v({"source": "code", "quote": "def f():",
              "locator": "src.py:1"}, **kw) is False
    assert v({"source": "code", "quote": "# repo",
              "locator": "../README.md"}, **kw) is False
    assert v({"source": "code", "quote": "# repo",
              "locator": f"{tree}/README.md:1"}, **kw) is True
    assert v({"source": "report", "quote": "  Also affects\n`303 See Other`,",
              "locator": "docs/report.md"}, **kw) is True
    assert v({"source": "web", "quote": "x", "locator": "u"}, **kw) is None
    assert leerie._intent_verdict_trusted(
        [{"source": "web", "ok": None}]) is False


def test_the_resolver_reads_the_task_files_and_writes_nothing(env):  # noqa: F811
    leerie = env["leerie"]
    _setup(env)
    g = leerie._worker_grants(
        run_dir=env["run_dir"], state={}, schema_key="intent_resolver",
        cwd=env["staging"], allowed_tools=leerie.INTENT_RESOLVER_TOOLS,
        env={})
    tf = str((env["run_dir"] / "task-files").resolve())
    assert tf in g.ro and tf not in g.rw
    assert str(env["staging"].resolve()) not in g.rw


def test_run_phases_asks_where_the_answer_is_needed(leerie):
    src = inspect.getsource(leerie._run_phases)
    order = [src.index(s) for s in (
        "await _plan_intent_check(", "_stop_for_intent_questions(st)",
        "_apply_plan_intent_rulings(st, subtasks)",
        "_write_plan(leerie_dir, task, st, subtasks, waves)",
        "await _run_final_conformance(")]
    assert order == sorted(order)
    tail = src[src.index("await _run_final_conformance("):]
    order = [tail.index(s) for s in (
        "_stop_for_intent_questions(st)", "_apply_set_aside_answers(st)",
        "await _run_delivery_redrive(")]
    assert order == sorted(order)
    stops = [i for i in range(len(tail))
             if tail.startswith("_stop_for_intent_questions(st)", i)]
    assert len(stops) == 2
    assert (tail.index("await _run_delivery_redrive(") < stops[1]
            < tail.index("await phase_finalize("))


def test_schemas_and_tiers(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_verdict(), leerie.SCHEMAS["intent_resolver"])
    jsonschema.validate({"conflicts": CONFLICTS},
                        leerie.SCHEMAS["plan_item_checker"])
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({**_verdict(), "answer": "maybe"},
                            leerie.SCHEMAS["intent_resolver"])
    for w in ("intent_resolver", "plan_item_checker"):
        assert leerie.MODEL_DEFAULT_PER_WORKER[w] == "opus"
        assert leerie.EFFORT_DEFAULT_PER_WORKER[w] == "high"
        assert w in leerie.PLANNING_WORKER_TYPES
