"""A fix that keeps both, on opus when sonnet could not (DESIGN §8).

#333 runs each existing test the run changed in its BASE form on the tree
that ships; a case failing there blocks `done`. Before that verdict, the
delivery re-drive hands each such test file back once as a typed
`base_form_case` note (the implementer's own model and effort), judged by the
re-drive's own checks; the cases still failing get exactly one more round on
`MODEL_BASE_FORM_ESCALATION` at `EFFORT_BASE_FORM_ESCALATION`, driven alone.
What still fails after that stays a change: nothing is accepted by reading.

The re-drive World (`tests/test_delivery_redrive.py`) stands in for the tree;
the base-form measurement answers from staging's committed `src/a.ts`, as the
World's set evaluation does.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git

PIN = "src/a.test.ts"
CASE_A = "src/a.test.ts :: pins the budget"
CASE_B = "src/a.test.ts :: pins the ceiling"


def _world(leerie, tmp_path, monkeypatch, cases=(CASE_A,)):
    """The re-drive's World with one existing test file whose `cases` fail
    in their base form until `src/a.ts` names each case's marker (A, B), or
    says PINNED for all of them."""
    from tests.test_delivery_redrive import World as RedriveWorld
    w = RedriveWorld(leerie, tmp_path)
    w.install(monkeypatch)
    # Nothing else open: the base-form note is the only work.
    w.st.data["acceptance"] = {"sets": [], "gate": {}}
    seen: list[dict] = []
    marks = {CASE_A: "PIN_A", CASE_B: "PIN_B"}

    async def base_form(st, caps, staging, before, **kw):
        seen.append({"before": before, **kw})
        text = _git(staging, "show", "HEAD:src/a.ts")
        # The round's own check (reach_of: against its start) sees nothing
        # it newly broke; against the base, a case fails until its marker.
        fails = [] if kw.get("reach_of") else [
            c for c in cases if "PINNED" not in text and marks[c] not in text]
        return {"files": [PIN], "unmeasured": [], "unsettled": [],
                "changed": [{"id": c, "file": PIN,
                             "message": "expected 2 to be 5"}
                            for c in fails]}
    monkeypatch.setattr(leerie, "_base_form_regressions", base_form)
    w.dropped: list[str] = []

    async def dropped(st, caps, staging, pre_sha, **kw):
        return list(w.dropped)
    monkeypatch.setattr(leerie, "_redrive_dropped_cases", dropped)
    models: list[str] = []
    inner = leerie.claude_p

    async def recording(**kw):
        models.append(kw["model"])
        return await inner(**kw)
    monkeypatch.setattr(leerie, "claude_p", recording)
    tree = asyncio.run(leerie._view_tree_sha(w.st, w.staging))
    w.st.data["existing_behaviour"] = {
        "tree_sha": tree, "base": w.base, "files": [PIN],
        "changed": [{"id": c, "file": PIN,
                     "message": "\x1b[31mexpected 2 to be 5\x1b[39m"}
                    for c in cases]}
    w.st.save()
    w.seen, w.models = seen, models
    return w


def _note(call: dict) -> str:
    return call["prompt"].split("NOTE FROM ORCHESTRATOR: ", 1)[1]


def _driven(w) -> dict:
    return w.st.data["delivery_redrive"]["base_form_driven"][f"base-form:{PIN}"]


def test_the_round_that_keeps_both_is_kept_and_not_escalated(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // PINNED\n")]
    w.run()
    (call,) = w.calls
    note = _note(call)
    assert '"kind": "base_form_case"' in note
    assert f"git show {w.base}:{PIN}" in note
    assert "expected 2 to be 5" in note and "\x1b" not in note
    assert "keeps both" in note and "BASE-FORM CHECK:" in note
    d = _driven(w)
    (a,) = d["attempts"]
    # The implementer's own model and effort, recorded with the attempt.
    assert (a["model"], a["effort"], a["escalation"]) == ("sonnet", "low",
                                                          False)
    assert call["effort"] == "low" and w.models == ["sonnet"]
    assert a["outcome"] == "kept" and d["fixed"] == [CASE_A]
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert g["base_form_fixed"] == [CASE_A] and not g.get("rolled_back")
    # Measured against the run's base, not the round's start.
    assert any(s["before"] == w.st.data["base_sha"]
               and not s.get("reach_of") for s in w.seen)
    assert "PINNED" in w.a_text("HEAD")


def test_a_case_sonnet_could_not_keep_gets_one_round_on_opus(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.edits = [lambda w: w.write("export const a = 1; // other\n"),
               lambda w: w.write("export const a = 1; // PINNED\n")]
    w.run()
    assert len(w.calls) == 2
    first, second = _driven(w)["attempts"]
    assert first["outcome"] == "rolled_back"
    assert "fixed no failing case" in first["reason"]
    assert (second["model"], second["effort"], second["escalation"]) == (
        leerie.MODEL_BASE_FORM_ESCALATION,
        leerie.EFFORT_BASE_FORM_ESCALATION, True)
    assert w.models == ["sonnet", "opus"]
    assert w.calls[1]["effort"] == "high"
    assert second["outcome"] == "kept" and _driven(w)["fixed"] == [CASE_A]
    assert _git(w.staging, "rev-parse", "HEAD") != start
    assert "PINNED" in w.a_text("HEAD")


def test_a_case_neither_round_keeps_stays_a_change(leerie, tmp_path,
                                                   monkeypatch):
    """Two finished attempts and no third, whatever the budget: the case is
    still failing, so the run stays unconverged on it — nothing reads the
    task to accept it."""
    w = _world(leerie, tmp_path, monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.caps["subtask_redrive_rounds"] = 5
    w.edits = [lambda w: w.write("export const a = 1; // other\n")] * 4
    w.run()
    assert len(w.calls) == 2 and w.models == ["sonnet", "opus"]
    d = _driven(w)
    assert [a["outcome"] for a in d["attempts"]] == ["rolled_back",
                                                     "rolled_back"]
    assert d["fixed"] == [] and _git(w.staging, "rev-parse", "HEAD") == start
    assert leerie._redrive_base_form_open(
        w.st.data["delivery_redrive"]) == []
    leerie._decide_outcome(w.st, any_commits=True)
    assert any("existing_behaviour_changed" in r and CASE_A in r
               for r in w.st.data["outcome_reasons"])


def test_the_escalation_drives_only_the_cases_still_failing(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, cases=(CASE_A, CASE_B))
    w.edits = [lambda w: w.write("export const a = 1; // PIN_A\n"),
               lambda w: w.write("export const a = 1; // PIN_A PIN_B\n")]
    w.run()
    first, second = _driven(w)["attempts"]
    assert first["cases"] == [CASE_A, CASE_B] and first["fixed"] == [CASE_A]
    assert second["cases"] == [CASE_B] and second["escalation"]
    note = _note(w.calls[1])
    assert CASE_B in note and CASE_A not in note
    assert _driven(w)["fixed"] == [CASE_A, CASE_B]


def test_the_escalation_is_driven_alone(leerie, tmp_path, monkeypatch):
    """Opus works on the base-form cases only: an unmet item routed to the
    same subtask waits for a round of its own, on the implementer's model."""
    w = _world(leerie, tmp_path, monkeypatch)
    w.st.data["required_items"] = [{"item": "a does X", "span": "S1",
                                    "verifiability": "tree"}]
    w.st.data["delivery_gate"]["unmet_final"] = [
        {"item_index": 0, "item": "a does X", "evidence": "missing",
         "by": ["judge"]}]
    w.caps["subtask_redrive_rounds"] = 5
    w.edits = [lambda w: w.write("export const a = 1; // other\n")] * 4
    w.run()
    assert w.models.count("opus") == 1
    esc = _note(w.calls[w.models.index("opus")])
    assert '"kind": "base_form_case"' in esc
    assert '"kind": "unmet_item"' not in esc


def test_a_round_that_drops_a_case_that_still_holds_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    """Putting an original test back is allowed; dropping a case of the file
    it found that still passes on its commit is not, and neither is a drop
    it could not measure."""
    w = _world(leerie, tmp_path, monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")
    w.dropped = ["src/a.test.ts :: credits the late body"]
    w.edits = [lambda w: w.write("export const a = 1; // PINNED\n")] * 2
    w.run()
    first, second = _driven(w)["attempts"]
    assert first["outcome"] == second["outcome"] == "rolled_back"
    assert "still hold on its commit" in first["reason"]
    assert "credits the late body" in first["reason"]
    assert _git(w.staging, "rev-parse", "HEAD") == start


def test_an_interrupted_attempt_is_replaced_not_counted(leerie):
    rec = {"base_form_open": [{"kind": "base_form_case", "key": "k",
                               "file": PIN, "cases": [CASE_A],
                               "messages": ["m"], "exercised": [PIN]}]}
    leerie._redrive_base_form_attempt(rec, rec["base_form_open"], rnd=1,
                                      group=0, model="sonnet", effort="low")
    # Cut short: the same first attempt is offered again, not an escalation.
    (n,) = leerie._redrive_base_form_open(rec)
    assert not n.get("escalation")
    leerie._redrive_base_form_attempt(rec, [n], rnd=2, group=0,
                                      model="sonnet", effort="low")
    assert len(rec["base_form_driven"]["k"]["attempts"]) == 1
    leerie._redrive_base_form_settled(rec, [n], {"no_change": True})
    (e,) = leerie._redrive_base_form_open(rec)
    assert e["escalation"] and e["cases"] == [CASE_A]


def test_no_note_for_another_trees_record(leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    w.st.data["existing_behaviour"]["tree_sha"] = "0" * 40
    w.st.save()
    w.run()
    assert w.calls == []
    assert w.st.data["delivery_redrive"]["base_form_open"] == []


def test_the_escalation_is_a_per_call_override_only(leerie):
    """Opus reaches this round only: no worker's default changes."""
    assert leerie.MODEL_BASE_FORM_ESCALATION == "opus"
    assert leerie.EFFORT_BASE_FORM_ESCALATION == "high"
    assert leerie.MODEL_DEFAULT_PER_WORKER["implementer"] == "sonnet"
    assert leerie.EFFORT_DEFAULT_PER_WORKER["implementer"] == "low"
    src = __import__("inspect").getsource(leerie._run_delivery_redrive)
    assert src.count("MODEL_BASE_FORM_ESCALATION") == 1


def test_dropped_cases_are_the_held_ones_only(leerie, tmp_path):
    """Real git, real confined pytest: the round deletes two cases of a test
    file — one its change contradicts (fails in the file's start form on
    its commit), one that still holds. Only the second is dropped."""
    from tests.test_existing_behaviour_base_form import (
        CASES, SPENT, World as BaseWorld, _quiet)  # noqa: F401
    w = BaseWorld(leerie, tmp_path)
    pre = _git(w.staging, "rev-parse", "HEAD")
    w.commit({"lib.py": SPENT,
              "tests/test_cases.py": "from lib import spend\n\n"
              "def test_two_rounds():\n    assert spend() == 2\n"})
    got = asyncio.run(leerie._redrive_dropped_cases(
        w.st, w.caps, w.staging, pre, label="t",
        log_path=w.st.run_dir / "logs" / "t.log"))
    assert [g.rsplit(":", 1)[-1].rsplit(".", 1)[-1] for g in got] == [
        "test_kept"]
    assert not any("test_spends" in g for g in got)
    # A round that keeps its cases drops nothing.
    pre2 = _git(w.staging, "rev-parse", "HEAD")
    w.commit({"tests/test_cases.py": "from lib import spend\n\n"
              "def test_two_rounds():\n    assert spend() == 2\n\n"
              "def test_more():\n    assert True\n"})
    assert asyncio.run(leerie._redrive_dropped_cases(
        w.st, w.caps, w.staging, pre2, label="t2",
        log_path=w.st.run_dir / "logs" / "t2.log")) == []


# --- the test closure a base-form round may only put back ---------------------

def _repo(tmp_path, files: dict[str, str]) -> tuple:
    r = tmp_path / "r"
    r.mkdir()
    def git(*a):
        return subprocess.run(["git", "-C", str(r), *a], capture_output=True,
                              text=True, check=True).stdout.strip()
    git("init", "-q", "-b", "main")
    git("config", "user.email", "t@x")
    git("config", "user.name", "t")
    for rel, text in files.items():
        p = r / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    git("add", "-A")
    git("commit", "-qm", "base")
    return r, git


def _commit(r, git, files: dict[str, str | None], msg="c") -> str:
    for rel, text in files.items():
        p = r / rel
        if text is None:
            p.unlink()
        else:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
    git("add", "-A")
    git("commit", "-qm", msg)
    return git("rev-parse", "HEAD")


BASE_FILES = {
    "src/a.ts": "export const a = 1;\n",
    "src/a.test.ts": "import { a } from './a';\nimport { h } from "
                     "'../test/helpers/h';\nit('pins a', () => {});\n",
    "test/helpers/h.ts": "import { m } from './m';\nexport const h = m;\n",
    "test/helpers/m.ts": "export const m = 1;\n",
    "src/__mocks__/intl.ts": "export const t = (k: string) => k;\n",
    "vitest.config.ts": "export default { test: { setupFiles: "
                        "['./vitest.setup.ts'] } };\n",
    "vitest.setup.ts": "import { t } from './src/__mocks__/intl';\n"
                       "export { t };\n",
    "src/b.test.ts": "it('other', () => {});\n",
}


def test_the_closure_is_the_test_and_everything_it_runs_with(leerie,
                                                             tmp_path):
    r, git = _repo(tmp_path, BASE_FILES)
    head = git("rev-parse", "HEAD")
    got = leerie._base_form_closure(r, head, ["src/a.test.ts"])
    assert got == {"src/a.test.ts", "test/helpers/h.ts", "test/helpers/m.ts",
                   "vitest.config.ts", "vitest.setup.ts",
                   "src/__mocks__/intl.ts"}
    # Production code and another test are outside it.
    assert "src/a.ts" not in got and "src/b.test.ts" not in got


def test_a_round_may_change_production_and_put_the_original_back(
        leerie, tmp_path):
    r, git = _repo(tmp_path, BASE_FILES)
    base = git("rev-parse", "HEAD")
    # The run changed the test and added a test of its own.
    start = _commit(r, git, {
        "src/a.test.ts": BASE_FILES["src/a.test.ts"] + "it('run', () => {});\n",
        "src/c.test.ts": "it('added by the run', () => {});\n"})
    post = _commit(r, git, {"src/a.ts": "export const a = 2;\n",
                            "src/a.test.ts": BASE_FILES["src/a.test.ts"],
                            "src/d.test.ts": "it('new', () => {});\n",
                            "src/b.test.ts": "it('other', () => 1);\n"})
    assert leerie._base_form_closure_breach(
        r, base, start, post, ["src/a.test.ts"]) == []



@pytest.mark.parametrize("edit,hit", [
    ({"vitest.setup.ts": "export const t = (k: string) => 'es';\n"},
     "vitest.setup.ts"),
    ({"src/__mocks__/intl.ts": "export const t = () => 'x';\n"},
     "src/__mocks__/intl.ts"),
    ({"test/helpers/m.ts": "export const m = 2;\n"}, "test/helpers/m.ts"),
    ({"src/a.test.ts": "it('pins a', () => {});\n"}, "src/a.test.ts"),
    ({"src/a.test.ts": None}, "src/a.test.ts"),
    ({"src/c.test.ts": "it('weaker', () => {});\n"}, "src/c.test.ts"),
    ({"src/c.test.ts": None}, "src/c.test.ts"),
])
def test_a_round_that_changes_the_closure_or_a_run_test_breaches(
        leerie, tmp_path, edit, hit):
    r, git = _repo(tmp_path, BASE_FILES)
    base = git("rev-parse", "HEAD")
    start = _commit(r, git, {
        "src/a.test.ts": BASE_FILES["src/a.test.ts"] + "it('run', () => {});\n",
        "src/c.test.ts": "it('added by the run', () => {});\n"})
    post = _commit(r, git, edit)
    got = leerie._base_form_closure_breach(r, base, start, post,
                                           ["src/a.test.ts"])
    assert len(got) == 1 and got[0].startswith(hit + " (")


def test_a_round_that_breaches_the_closure_is_rolled_back(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch)
    start = _git(w.staging, "rev-parse", "HEAD")
    monkeypatch.setattr(leerie, "_base_form_closure_breach",
                        lambda *a, **k: ["vitest.setup.ts (its cases' test "
                                         "closure, not put back exactly as "
                                         "at the run's base)"])
    w.edits = [lambda w: w.write("export const a = 1; // PINNED\n")] * 2
    w.run()
    first, second = _driven(w)["attempts"]
    assert first["outcome"] == second["outcome"] == "rolled_back"
    assert "changed what a base-form case's test is" in first["reason"]
    assert "vitest.setup.ts" in first["reason"]
    assert _git(w.staging, "rev-parse", "HEAD") == start


# --- a case pinning text the run moved to the message files --------------------

MOVED_BASE = {
    "src/campaign-list.tsx": "export const L = () => <button>Enviar ahora"
                             "</button>;\nexport const H = () => <th>Nombre"
                             "</th>;\nexport const B = 'button';\n",
    "src/kept.tsx": "export const K = () => <p>Sin cambios</p>;\n",
    "messages/es.json": '{"Other": {"x": "y"}}\n',
    "fixtures/es.json": '{"Untouched": "Sin cambios"}\n',
}
MOVED_HEAD = {
    "src/campaign-list.tsx": "export const L = () => <button>{t('sendNow')}"
                             "</button>;\nexport const H = () => <th>"
                             "{t('colName')}</th>;\nexport const B = "
                             "'button';\n",
    "messages/es.json": '{"Other": {"x": "y"}, "Email": {"sendNow": '
                        '"Enviar ahora", "colName": "Nombre", "kept": '
                        '"Sin cambios"}}\n',
}


def test_a_literal_the_run_moved_to_the_message_files_is_found(leerie,
                                                                tmp_path):
    r, git = _repo(tmp_path, MOVED_BASE)
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, MOVED_HEAD)
    got = leerie._moved_literal_evidence(r, base, head, [
        {"kind": "string", "text": "Enviar ahora"},
        {"kind": "regex", "text": "nombre", "flags": "i"},
        {"kind": "string", "text": "button"},       # still in production
        {"kind": "string", "text": "Sin cambios"},  # still in production
        {"kind": "string", "text": "renders the list"},  # never production
        {"kind": "regex", "text": "nom(bre)?", "flags": "i"},  # regex syntax
    ])
    assert [(e["literal"], e["kind"], e["file"], e["message_file"], e["key"])
            for e in got] == [
        ("Enviar ahora", "string", "src/campaign-list.tsx",
         "messages/es.json", "Email.sendNow"),
        ("nombre", "regex", "src/campaign-list.tsx", "messages/es.json",
         "Email.colName")]
    # A regex without its `i` flag is matched as written: no hit.
    assert leerie._moved_literal_evidence(
        r, base, head, [{"kind": "regex", "text": "nombre", "flags": ""}]) == []


def test_no_moved_literal_without_a_message_file_the_run_changed(leerie,
                                                                 tmp_path):
    r, git = _repo(tmp_path, MOVED_BASE)
    base = git("rev-parse", "HEAD")
    # The literal leaves production, but no message file the run changed
    # holds it (the untouched fixture does not count).
    head = _commit(r, git, {"src/kept.tsx": "export const K = () => null;\n"})
    assert leerie._moved_literal_evidence(
        r, base, head, [{"kind": "string", "text": "Sin cambios"}]) == []


MOVED_EVIDENCE = {"literal": "Enviar ahora", "kind": "string", "ci": False,
                  "file": "src/campaign-list.tsx",
                  "message_file": "messages/es.json", "key": "Email.sendNow"}
MOVED_QID = "intent-base-form-moved-literal"


def _moved_world(leerie, tmp_path, monkeypatch, answer: str | None,
                 run_form: str = ""):
    """CASE_A pins text the run moved into the message files (its cause),
    with the cause's question answered `answer` (None: open) and the run's
    form of the case checked as `run_form` ("" = it still asserts the new
    behaviour); CASE_B has no cause."""
    w = _world(leerie, tmp_path, monkeypatch, cases=(CASE_A, CASE_B))
    eb = w.st.data["existing_behaviour"]
    eb["moved_literal"] = {CASE_A: [dict(MOVED_EVIDENCE)]}
    eb["causes"] = {CASE_A: [dict(MOVED_EVIDENCE, kind="moved_literal",
                                  literal_kind="string")]}
    eb["run_form"] = {CASE_A: run_form}
    w.st.data["intent_questions"] = {MOVED_QID: {
        "qid": MOVED_QID, "source": "base_form", "question": "q",
        "context": {"literals": [["Enviar ahora", "string",
                                  "src/campaign-list.tsx", False]]}}}
    if answer is not None:
        w.st.data.setdefault("answers", {})[MOVED_QID] = answer
    leerie._apply_base_form_rulings(w.st, eb)
    w.st.save()
    return w


def test_a_case_whose_causes_question_is_open_gets_no_round(
        leerie, tmp_path, monkeypatch):
    """Its base form passes only if the run's change is undone: while the
    task's answer is open it gets no round, and the run stops on it."""
    w = _moved_world(leerie, tmp_path, monkeypatch, None)
    w.edits = [lambda w: w.write("export const a = 1; // PIN_B\n")]
    w.run()
    (call,) = w.calls
    note = _note(call)
    assert CASE_B in note and CASE_A not in note
    d = _driven(w)
    assert d["cases"] == [CASE_B] and d["fixed"] == [CASE_B]
    w.st.data["tracked_test_report"] = {"files": [{"file": PIN,
                                                   "removed": ["x"]}]}
    lines = "\n".join(leerie._pr_tracked_test_lines(w.st.data))
    assert ("whether the task changes what it pins is an open question "
            f"(`{MOVED_QID}`): `{CASE_A}`") in lines
    leerie._decide_outcome(w.st, any_commits=True)
    assert any("existing_behaviour_changed" in r and CASE_A in r
               for r in w.st.data["outcome_reasons"])


def test_a_case_whose_cause_the_task_did_not_ask_for_gets_its_round(
        leerie, tmp_path, monkeypatch):
    """`no`: the text should not have moved, so putting it back is the fix —
    the case is a regression and gets the round that keeps both."""
    w = _moved_world(leerie, tmp_path, monkeypatch, "no")
    w.edits = [lambda w: w.write("export const a = 1; // PINNED\n")]
    w.run()
    (call,) = w.calls
    note = _note(call)
    assert CASE_A in note and CASE_B in note


def test_a_case_the_task_changes_with_a_kept_update_is_task_required(
        leerie, tmp_path, monkeypatch):
    """`yes`, and the run's form of the case still asserts the new
    behaviour: task-required — no round, listed with its cause, not counted
    by the outcome."""
    w = _moved_world(leerie, tmp_path, monkeypatch, "yes")
    eb = w.st.data["existing_behaviour"]
    assert [r["id"] for r in eb["changed"]] == [CASE_B]
    (tr,) = eb["task_required"]
    assert tr["id"] == CASE_A and tr["cause"] == ["moved_literal"]
    assert tr["qids"] == [MOVED_QID]
    w.edits = [lambda w: w.write("export const a = 1; // PIN_B\n")]
    w.run()
    (call,) = w.calls
    assert CASE_A not in _note(call)
    w.st.data["tracked_test_report"] = {"files": [{"file": PIN,
                                                   "removed": ["x"]}]}
    lines = "\n".join(leerie._pr_tracked_test_lines(w.st.data))
    assert ("existing test changed because the task changes what it pins "
            f"(moved_literal; `{MOVED_QID}`): `{CASE_A}`") in lines
    w.st.data["existing_behaviour"]["changed"] = []
    leerie._decide_outcome(w.st, any_commits=True)
    assert not any("existing_behaviour_changed" in r
                   for r in w.st.data.get("outcome_reasons") or [])


def test_a_case_the_task_changes_with_a_weakened_update_stays_a_change(
        leerie, tmp_path, monkeypatch):
    """`yes`, but the run's form of the case asserts less: not an update the
    answer allows. It stays a change, gets no round (its only round undoes
    the task), and the PR says why."""
    why = "the run's form of it makes 0 assertion(s) where the base form made 1"
    w = _moved_world(leerie, tmp_path, monkeypatch, "yes", run_form=why)
    eb = w.st.data["existing_behaviour"]
    assert {r["id"] for r in eb["changed"]} == {CASE_A, CASE_B}
    assert eb["cause_unkept"] == {CASE_A: why}
    w.edits = [lambda w: w.write("export const a = 1; // PIN_B\n")]
    w.run()
    (call,) = w.calls
    assert CASE_A not in _note(call)
    w.st.data["tracked_test_report"] = {"files": [{"file": PIN,
                                                   "removed": ["x"]}]}
    lines = "\n".join(leerie._pr_tracked_test_lines(w.st.data))
    assert (f"the task changes what this existing test pins, but {why}: "
            f"`{CASE_A}`") in lines


def test_the_classification_reads_the_base_forms_ast(leerie, tmp_path,
                                                     monkeypatch):
    """`_base_form_moved_literals` reads each case's literals from the AST
    of its BASE form, and classifies only JS/TS cases."""
    r, git = _repo(tmp_path, {**MOVED_BASE, "src/a.test.tsx": "it('x', () => "
                              "{ screen.getByRole('button', { name: "
                              "'Enviar ahora' }); });\n"})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, {**MOVED_HEAD, "src/a.test.tsx": "it('x', () => "
                            "{ screen.getByRole('button', { name: "
                            "'sendNow' }); });\n"})
    seen: list[tuple] = []

    async def facts(st, tree, src, rel, case):
        from pathlib import Path as P
        seen.append((P(src).read_text(), rel, case))
        return {"literals": [{"kind": "string", "text": "Enviar ahora"},
                             {"kind": "string", "text": "button"}]}

    async def view(st, tree, caps, **kw):
        return str(tree)
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", facts)
    monkeypatch.setattr(leerie, "_measured_tree", view)
    monkeypatch.setattr(leerie, "_run_repo", lambda st: r)

    class St:
        run_dir = tmp_path / "run"
        repo_root = r
    St.run_dir.mkdir()
    rec = {"base": base, "after": head, "changed": [
        {"id": "src/a.test.tsx :: x", "file": "src/a.test.tsx"},
        {"id": "tests/test_a.py::test_x", "file": "tests/test_a.py"}]}
    got = asyncio.run(leerie._base_form_moved_literals(St(), {}, r, rec))
    assert list(got) == ["src/a.test.tsx :: x"]
    assert got["src/a.test.tsx :: x"][0]["key"] == "Email.sendNow"
    ((src, rel, case),) = seen
    assert "'Enviar ahora'" in src and case == "x"
    # Read beside the test, at a path the tree does not hold: a held-out
    # sandbox places a file and refuses one the view already has (the base
    # form's own path), which classified nothing on e0d101e3 in bench 7.
    assert rel == "src/.leerie-base-form.a.test.tsx"
    assert not (r / rel).exists() and (r / "src" / "a.test.tsx").exists()
    assert rel.endswith(".tsx")


def test_the_facts_script_gives_a_cases_literals(tmp_path):
    """`scripts/test-case-facts.cjs` lists the string and regex literals of
    the case's reachable code, a const it reads resolved through its
    declaration."""
    from tests.test_delivery_redrive import _ts_dir
    ts = _ts_dir()
    if ts is None:
        pytest.skip("node or the typescript package unavailable")
    f = tmp_path / "a.test.tsx"
    f.write_text("const LABEL = 'Mostrar contraseña';\n"
                 "describe('PasswordInput', () => {\n"
                 "  it('toggles', () => {\n"
                 "    screen.getByRole('button', { name: LABEL });\n"
                 "    expect(screen.getByRole('button', { name: /nombre/i }))"
                 ".toBeTruthy();\n  });\n"
                 "  it('other', () => { expect('Unrelated').toBe('x'); });\n"
                 "});\n")
    out = subprocess.run(
        ["node", str(REPO_ROOT / "scripts" / "test-case-facts.cjs"), str(f),
         "PasswordInput toggles"], capture_output=True, text=True,
        cwd=str(tmp_path), env={**os.environ,
                                "LEERIE_BUNDLED_TYPESCRIPT": ts})
    lits = json.loads(out.stdout)["literals"]
    assert {"kind": "string", "text": "Mostrar contraseña"} in lits
    assert {"kind": "regex", "text": "nombre", "flags": "i"} in lits
    assert {"kind": "string", "text": "button"} in lits
    assert not any(x.get("text") == "Unrelated" for x in lits)


def test_a_bracketed_path_is_read_literally(leerie, tmp_path):
    """`app/[locale]/page.tsx` is one file, not a glob that also matches
    `app/l/page.tsx`: a literal still held by a file the glob would match
    does not hide that the bracketed file lost it."""
    r, git = _repo(tmp_path, {
        "app/[locale]/page.tsx": "export const P = () => <h1>Difusión</h1>;\n",
        "app/l/page.tsx": "export const Q = () => <h1>Difusión</h1>;\n",
        "messages/es.json": '{"x": "y"}\n'})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, {
        "app/[locale]/page.tsx": "export const P = () => <h1>{t('t')}</h1>;\n",
        "messages/es.json": '{"x": "y", "Broadcast": {"t": "Difusión"}}\n'})
    got = leerie._moved_literal_evidence(
        r, base, head, [{"kind": "string", "text": "Difusión"}])
    assert [e["file"] for e in got] == ["app/[locale]/page.tsx"]


def test_a_regex_is_read_with_its_own_flags_and_only_as_plain_text(
        leerie, tmp_path):
    r, git = _repo(tmp_path, {
        "src/t.tsx": "export const T = () => <><th>nombre</th><i>a+b</i>"
                     "</>;\n",
        "messages/es.json": '{"x": "y"}\n',
        "config/app.json": '{"x": "y"}\n'})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, {
        "src/t.tsx": "export const T = () => <><th>{t('n')}</th>"
                     "<i>{t('s')}</i></>;\n",
        "messages/es.json": '{"x": "y", "n": "Nombre", "b": "elige un nombre", '
                            '"s": "a+b"}\n'})
    ev = lambda lits: leerie._moved_literal_evidence(r, base, head, lits)
    # /nombre/ matches only the lowercase value; /nombre/i matches "Nombre",
    # the value that is the whole text, whose key is preferred.
    assert [e["key"] for e in ev([{"kind": "regex", "text": "nombre",
                                   "flags": ""}])] == ["b"]
    assert [e["key"] for e in ev([{"kind": "regex", "text": "nombre",
                                   "flags": "i"}])] == ["n"]
    # A regex with syntax is not read as the text it spells.
    assert ev([{"kind": "regex", "text": "a+b", "flags": ""}]) == []
    assert [e["key"] for e in ev([{"kind": "string", "text": "a+b"}])] == ["s"]


def test_only_a_message_file_counts(leerie, tmp_path):
    """A JSON the run changed outside a messages/locales directory is not a
    message file, whatever it holds."""
    r, git = _repo(tmp_path, {
        "src/t.tsx": "export const T = () => <p>Guardar</p>;\n",
        "config/app.json": '{"x": "y"}\n',
        "messages/es.json": '{"x": "y"}\n'})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, {
        "src/t.tsx": "export const T = () => <p>{label}</p>;\n",
        "config/app.json": '{"x": "y", "label": "Guardar"}\n',
        "messages/es.json": '{"x": "z"}\n'})
    assert leerie._moved_literal_evidence(
        r, base, head, [{"kind": "string", "text": "Guardar"}]) == []


def test_a_comment_naming_the_old_text_is_not_the_text(leerie, tmp_path):
    """4134b78c: `campaign-list.tsx` kept a doc comment naming the button's
    old label after the label moved to the message files. The comment is
    not the code: the literal still left the file."""
    r, git = _repo(tmp_path, {
        "src/c.tsx": "/** Each row shows a \"Enviar ahora\" button. */\n"
                     "export const C = () => <b>Enviar ahora</b>;\n",
        "messages/es.json": '{"x": "y"}\n'})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, {
        "src/c.tsx": "/** Each row shows a \"Enviar ahora\" button. */\n"
                     "export const C = () => <b>{t('sendNow')}</b>;\n",
        "messages/es.json": '{"x": "y", "sendNow": "Enviar ahora", '
                            '"body": "elige un nombre", "name": "Nombre"}\n'})
    got = leerie._moved_literal_evidence(
        r, base, head, [{"kind": "string", "text": "Enviar ahora"}])
    assert [e["key"] for e in got] == ["sendNow"]
    # Only in a comment at the base: never in the code, so not moved.
    (tmp_path / "two").mkdir()
    r2, git2 = _repo(tmp_path / "two", {
        "src/c.tsx": "// \"Enviar ahora\"\nexport const C = () => null;\n",
        "messages/es.json": '{"x": "y"}\n'})
    b2 = git2("rev-parse", "HEAD")
    h2 = _commit(r2, git2, {"src/c.tsx": "export const C = () => null;\n",
                            "messages/es.json": '{"sendNow": "Enviar ahora"}\n'})
    assert leerie._moved_literal_evidence(
        r2, b2, h2, [{"kind": "string", "text": "Enviar ahora"}]) == []


def test_a_flip_rollback_unkeeps_the_groups_base_form_attempt(leerie):
    """#365 rolls back a kept group after the rounds; a base-form attempt
    that group made reads `rolled_back`, and the file's `fixed` keeps only
    what a still-kept attempt fixed."""
    n = {"kind": "base_form_case", "key": "k", "file": PIN,
         "cases": [CASE_A, CASE_B], "messages": ["a", "b"],
         "exercised": [PIN]}
    rec = {"base_form_open": [n]}
    leerie._redrive_base_form_attempt(rec, [n], rnd=1, group=0,
                                      model="sonnet", effort="low")
    leerie._redrive_base_form_settled(rec, [n], {"base_form_fixed": [CASE_A]})
    (e,) = leerie._redrive_base_form_open(rec)
    leerie._redrive_base_form_attempt(rec, [e], rnd=2, group=1,
                                      model="opus", effort="high")
    leerie._redrive_base_form_settled(rec, [e], {"base_form_fixed": [CASE_B]})
    d = rec["base_form_driven"]["k"]
    assert d["fixed"] == sorted([CASE_A, CASE_B])
    leerie._redrive_base_form_unkept(rec, 2, 1, "flip: item 3")
    first, second = d["attempts"]
    assert (first["outcome"], first["fixed"]) == ("kept", [CASE_A])
    assert (second["outcome"], second["fixed"], second["reason"]) == (
        "rolled_back", [], "flip: item 3")
    assert d["fixed"] == [CASE_A] and d["outcome"] == "rolled_back"
    # Another group's rollback leaves the record as it is.
    leerie._redrive_base_form_unkept(rec, 1, 1, "other")
    assert first["outcome"] == "kept" and d["fixed"] == [CASE_A]


def test_the_flip_rollback_path_unkeeps_each_group_it_rolls_back(
        leerie, tmp_path, monkeypatch):
    """Driven through the real flip rollback (the 22a30525 replay): every
    group it rolls back is passed on, with that group's own reason."""
    from tests.test_met_items_refalsified_after_kept_rounds import (
        Replay, _fix_mul, _fix_mul_undo_add)
    seen: list[tuple[int, int, str]] = []
    real = leerie._redrive_base_form_unkept
    monkeypatch.setattr(
        leerie, "_redrive_base_form_unkept",
        lambda rec, r_, g_, reason: (seen.append((r_, g_, reason)),
                                     real(rec, r_, g_, reason))[1])
    r = Replay(leerie, tmp_path, monkeypatch, judge_reads_tree=True,
               edit=[_fix_mul_undo_add, _fix_mul])
    r.run()
    g1 = r.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert g1["rolled_back"] is True
    assert seen == [(1, 0, g1["rollback_reason"])]
