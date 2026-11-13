"""A broken base-form case whose cause is a change the run made is a
question first (DESIGN §8; §11 source (d)).

navegando e0d101e3: a translations sweep broke 272 existing cases in their
base form. About 200 assert Spanish text the run moved into the message
files; about 40 more fail because the base test's `next-intl/server` mock
lacks the `getTranslations` the run's pages now call. The run had already
updated those tests (the heading case asserts the key `pageTitle`, the mock
provides `getTranslations`), and the base-form check blocked a correct run —
while every round asked to keep the old forms passing could only undo the
task (T9 benches 4-6: hard-coded Spanish defaults in production).

Each such cause is one intent question (does the task ask for this change?);
`yes` makes a case task-required only when the run's own form of it still
asserts the new behaviour, checked mechanically; `no` keeps it a regression;
an open question keeps it from any round. Text a `yes` let move that comes
back into production is `literal_restored`, an `unconverged` reason.

Real path: a real git repository, the run's own repository and a real
staging worktree (`tests/test_run_final_conformance.py`'s `env`); the real
`_check_existing_behaviour`, classification, question building, resolver
call path and its quote verification, the real facts script
(`scripts/test-case-facts.cjs`) over the real test files. Stubbed: `claude_p`
(the resolver's answer), the base-form run itself (`_base_form_regressions`,
which other base-form tests stub: its own tests run it for real) and the
held-out sandbox around the facts script (`_probe_case_facts_placed`,
`_measured_tree`: the script runs on the same file directly).
"""
from __future__ import annotations

import asyncio
import os
import subprocess

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_base_form_redrive import _commit, _repo
from tests.test_delivery_redrive import _ts_dir
from tests.test_run_final_conformance import env  # noqa: F401

TASK = ("Make every label of the site come from the message files, in both "
        "languages.")
MOCK_MSG = ('Error: [vitest] No "getTranslations" export is defined on the '
            '"next-intl/server" mock. Did you forget to return it from '
            '"vi.mock"?')
SEND = "src/campaign-list.test.tsx :: CampaignList shows the send button"
WEAK = "src/campaign-list.test.tsx :: CampaignList sends on click"
GATE = "src/page.test.tsx :: Page gates admins"
OTHER = "src/total.test.ts :: total adds"
MOVED_QID = "intent-base-form-moved-literal"
MOCK_QID = "intent-base-form-mock-next-intl-server-getTranslations"

BASE = {
    "src/campaign-list.tsx":
        "export const CampaignList = () => <button>Enviar ahora</button>;\n",
    "src/page.tsx":
        'import { setRequestLocale } from "next-intl/server";\n'
        "export default async function Page() { setRequestLocale('es'); "
        "return null; }\n",
    "src/total.ts": "export const total = (a: number, b: number) => a + b;\n",
    "messages/es.json": '{"Other": {"x": "y"}}\n',
    "messages/en.json": '{"Other": {"x": "y"}}\n',
    "src/campaign-list.test.tsx":
        "import { CampaignList } from './campaign-list';\n"
        "describe('CampaignList', () => {\n"
        "  it('shows the send button', () => {\n"
        "    render(<CampaignList />);\n"
        "    expect(screen.getByRole('button', { name: 'Enviar ahora' }))"
        ".toBeTruthy();\n  });\n"
        "  it('sends on click', () => {\n"
        "    render(<CampaignList />);\n"
        "    fireEvent.click(screen.getByText('Enviar ahora'));\n"
        "    expect(send).toHaveBeenCalledTimes(1);\n  });\n"
        "});\n",
    "src/page.test.tsx":
        "import Page from './page';\n"
        "vi.mock('next-intl/server', () => ({ setRequestLocale: vi.fn() }));\n"
        "describe('Page', () => {\n"
        "  it('gates admins', async () => {\n"
        "    await Page();\n"
        "    expect(requireAdmin).toHaveBeenCalledTimes(1);\n  });\n"
        "});\n",
    "src/total.test.ts":
        "import { total } from './total';\n"
        "it('total adds', () => { expect(total(2, 2)).toBe(4); });\n",
}
# The run: the label moves to the message files and the button reads it; the
# page reads its text through `getTranslations`; the tests follow — one of
# them asserting the key, one reduced to no assertion at all.
RUN = {
    "src/campaign-list.tsx":
        "export const CampaignList = () => <button>{t('sendNow')}</button>;\n",
    "src/page.tsx":
        'import { getTranslations, setRequestLocale } from "next-intl/server";'
        "\nexport default async function Page() { setRequestLocale('es'); "
        "const t = await getTranslations('Page'); return t('title'); }\n",
    "messages/es.json": '{"Other": {"x": "y"}, "Email": {"sendNow": '
                        '"Enviar ahora"}}\n',
    "messages/en.json": '{"Other": {"x": "y"}, "Email": {"sendNow": '
                        '"Send now"}}\n',
    "src/campaign-list.test.tsx":
        "import { CampaignList } from './campaign-list';\n"
        "describe('CampaignList', () => {\n"
        "  it('shows the send button', () => {\n"
        "    render(<CampaignList />);\n"
        "    expect(screen.getByRole('button', { name: 'sendNow' }))"
        ".toBeTruthy();\n  });\n"
        "  it('sends on click', () => {\n"
        "    render(<CampaignList />);\n  });\n"
        "});\n",
    "src/page.test.tsx":
        "import Page from './page';\n"
        "vi.mock('next-intl/server', () => ({ setRequestLocale: vi.fn(), "
        "getTranslations: vi.fn(async () => (k: string) => k) }));\n"
        "describe('Page', () => {\n"
        "  it('gates admins', async () => {\n"
        "    await Page();\n"
        "    expect(requireAdmin).toHaveBeenCalledTimes(1);\n  });\n"
        "});\n",
    "src/total.ts": "export const total = (a: number, b: number) => a - b;\n",
}


def _models(leerie) -> dict:
    return {w: leerie.MODEL_DEFAULT_PER_WORKER.get(w, leerie.MODEL_DEFAULT)
            for w in leerie.WORKER_TYPES}


def _efforts(leerie) -> dict:
    return {w: leerie.EFFORT_DEFAULT_PER_WORKER.get(w)
            for w in leerie.WORKER_TYPES}


def _write_commit(tree, files: dict[str, str], msg: str) -> str:
    for rel, text in files.items():
        p = tree / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
    _git(tree, "add", "-A")
    _git(tree, "commit", "-qm", msg)
    return _git(tree, "rev-parse", "HEAD")


@pytest.fixture
def world(env, monkeypatch):  # noqa: F811
    """Staging holds the base (the run's `base_sha`) and the run's commit on
    it; the base-form run reports SEND, WEAK, GATE and OTHER failing."""
    ts = _ts_dir()
    if ts is None:
        pytest.skip("node or the typescript package unavailable")
    leerie, st, staging = env["leerie"], env["st"], env["staging"]
    base = _write_commit(staging, BASE, "base")
    st.data["base_sha"] = base
    st.data["task"] = TASK
    st.save()
    after = _write_commit(staging, RUN, "run")
    rows = [{"id": SEND, "file": "src/campaign-list.test.tsx",
             "message": "Unable to find an accessible element with the role "
                        '"button" and name "Enviar ahora"'},
            {"id": WEAK, "file": "src/campaign-list.test.tsx",
             "message": "Unable to find an element with the text: Enviar "
                        "ahora"},
            {"id": GATE, "file": "src/page.test.tsx", "message": MOCK_MSG},
            {"id": OTHER, "file": "src/total.test.ts",
             "message": "expected 0 to be 4"}]

    async def base_form(st_, caps, tree, before, **kw):
        head = _git(tree, "rev-parse", "HEAD")
        fails = [r for r in rows if r["id"] in world_state["failing"]]
        return {"base": base, "before": before, "after": head,
                "files": sorted({r["file"] for r in rows}), "unsupported": [],
                "changed": [dict(r) for r in fails], "unmeasured": [],
                "unsettled": [], "verdict": None}

    async def view(st_, tree, caps, **kw):
        return str(tree)

    async def facts(st_, tree, src, rel, case):
        out = subprocess.run(
            ["node", str(REPO_ROOT / "scripts" / "test-case-facts.cjs"), src,
             case], capture_output=True, text=True, cwd=str(tree),
            env={**os.environ, "LEERIE_BUNDLED_TYPESCRIPT": ts})
        import json
        return json.loads(out.stdout)

    calls: list[dict] = []
    answers = {"verdict": "yes", "status": "settled"}

    async def fake(**kw):
        calls.append(kw)
        assert kw["schema_key"] == "intent_resolver"
        return {"decision": f"{answers['verdict'].capitalize()}. The task "
                            "asks for every label in the message files.",
                "answer": answers["verdict"], "status": answers["status"],
                "evidence": [{"source": "task", "locator": "task.md",
                              "quote": "every label of the site come from "
                                       "the message files"}]}

    world_state = {"failing": {SEND, WEAK, GATE, OTHER}}
    monkeypatch.setattr(leerie, "_base_form_regressions", base_form)
    monkeypatch.setattr(leerie, "_measured_tree", view)
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", facts)
    monkeypatch.setattr(leerie, "claude_p", fake)
    return {**env, "base": base, "after": after, "calls": calls,
            "answers": answers, "state": world_state}


def _check(w) -> dict:
    leerie = w["leerie"]
    asyncio.run(leerie._check_existing_behaviour(
        w["run_dir"], w["st"], w["caps"], _models(leerie), _efforts(leerie)))
    return w["st"].data["existing_behaviour"]


def _reasons(w) -> list[str]:
    w["leerie"]._decide_outcome(w["st"], any_commits=True)
    return w["st"].data.get("outcome_reasons") or []


def test_one_question_per_cause_and_kept_updates_are_task_required(world):
    eb = _check(world)
    qs = world["st"].data["intent_questions"]
    # Two causes, two questions, asked once each of the resolver (opus,
    # high), whatever the number of cases.
    assert sorted(qs) == [MOCK_QID, MOVED_QID]
    assert {q["source"] for q in qs.values()} == {"base_form"}
    resolver = [c for c in world["calls"]
                if c["schema_key"] == "intent_resolver"]
    assert len(resolver) == 2
    assert {(c["model"], c["effort"]) for c in resolver} == {("opus", "high")}
    moved_q = qs[MOVED_QID]["question"]
    assert '"Enviar ahora" from `src/campaign-list.tsx`' in moved_q
    assert "`Email.sendNow`" in moved_q and "2 existing test case(s)" in moved_q
    assert moved_q.endswith("Does the task ask for this text to move out of "
                            "the production code into the message files?")
    mock_q = qs[MOCK_QID]["question"]
    assert "imports `getTranslations` from `next-intl/server`" in mock_q
    assert "`src/page.tsx`" in mock_q
    assert "const t = await getTranslations('Page')" in mock_q
    # The quote verified: the answer is trusted and applied.
    assert all(q["verdict"]["trusted"] for q in qs.values())
    required = {r["id"]: r for r in eb["task_required"]}
    assert set(required) == {SEND, GATE}
    assert required[SEND]["cause"] == ["moved_literal"]
    assert required[GATE]["cause"] == ["mock_missing_export"]
    assert required[GATE]["qids"] == [MOCK_QID]
    # The update that asserts nothing is not one the answer allows; the case
    # with no cause is a regression, as before.
    assert [r["id"] for r in eb["changed"]] == [WEAK, OTHER]
    assert eb["cause_unkept"] == {WEAK: "the run's form of it makes 0 "
                                        "assertion(s) where the base form "
                                        "made 1"}
    assert eb["causes"].get(OTHER) is None
    assert eb["literal_restored"] == []
    reasons = _reasons(world)
    (bf,) = [r for r in reasons if "existing_behaviour_changed" in r]
    assert WEAK in bf and OTHER in bf and SEND not in bf and GATE not in bf


def test_a_no_keeps_every_case_a_regression(world):
    world["answers"]["verdict"] = "no"
    eb = _check(world)
    assert eb.get("task_required") in (None, [])
    assert {r["id"] for r in eb["changed"]} == {SEND, WEAK, GATE, OTHER}
    assert eb["cause_open"] == {} and eb["cause_unkept"] == {}


def test_an_unsettled_answer_holds_the_cases_and_is_asked(world, monkeypatch):
    world["answers"]["status"] = "unsettled"
    eb = _check(world)
    assert set(eb["cause_open"]) == {SEND, WEAK, GATE}
    assert eb["cause_open"][GATE] == [MOCK_QID]
    assert {r["id"] for r in eb["changed"]} == {SEND, WEAK, GATE, OTHER}
    leerie = world["leerie"]
    asked = [p["id"] for p in leerie._intent_open_questions(world["st"])]
    assert asked == [MOCK_QID, MOVED_QID]
    # The user's answer applies on the same tree, without a new measurement.
    world["st"].data.setdefault("answers", {}).update(
        {MOCK_QID: "yes", MOVED_QID: "yes"})
    eb = _check(world)
    assert {r["id"] for r in eb["task_required"]} == {SEND, GATE}
    assert len([c for c in world["calls"]
                if c["schema_key"] == "intent_resolver"]) == 2


def test_text_put_back_in_production_is_named(world):
    _check(world)
    # A later round hard-codes the label again so the old test passes.
    staging = world["staging"]
    _write_commit(staging, {
        "src/campaign-list.tsx": "export const CampaignList = () => <button>"
                                 "{t('sendNow') ?? 'Enviar ahora'}</button>;\n"},
        "restore")
    world["state"]["failing"] = {OTHER}
    eb = _check(world)
    assert eb["literal_restored"] == [
        {"literal": "Enviar ahora", "file": "src/campaign-list.tsx",
         "qid": MOVED_QID}]
    assert any("literal_restored" in r and "Enviar ahora" in r
               for r in _reasons(world))


def test_a_rounds_break_of_a_case_the_task_changes_is_not_its_change(world):
    """`_ratchet_exec(base_form=True)`: the same question and check on a
    round's commit; a kept update the task asked for is not the round's
    change, anything else is."""
    leerie = world["leerie"]
    out = asyncio.run(leerie._ratchet_exec(
        world["st"], world["caps"], world["staging"], world["base"], None,
        None, label="redrive-r1g0",
        log_path=world["run_dir"] / "logs" / "r.log", base_form=True,
        models=_models(leerie), efforts=_efforts(leerie)))
    assert out["base_form_changed"] == [WEAK, OTHER]
    assert sorted(world["st"].data["intent_questions"]) == [MOCK_QID,
                                                            MOVED_QID]


# --- the facts, mechanically ------------------------------------------------

def test_a_missing_mock_export_is_read_from_the_message_and_the_graph(
        leerie, tmp_path):
    r, git = _repo(tmp_path, {k: v for k, v in BASE.items()})
    base = git("rev-parse", "HEAD")
    head = _commit(r, git, RUN)
    src = BASE["src/page.test.tsx"]
    ev = leerie._mock_missing_export_evidence(r, base, head,
                                              "src/page.test.tsx", src,
                                              MOCK_MSG)
    assert ev["module"] == "next-intl/server"
    assert ev["export"] == "getTranslations"
    assert ev["files"] == ["src/page.tsx"]
    # Not the runner's message: no cause.
    assert leerie._mock_missing_export_evidence(
        r, base, head, "src/page.test.tsx", src, "expected 1 to be 2") is None
    # A test that does not reach the module that imports it: no cause.
    assert leerie._mock_missing_export_evidence(
        r, base, head, "src/total.test.ts", BASE["src/total.test.ts"],
        MOCK_MSG) is None
    # The base already imported it: the run did not make that change.
    (tmp_path / "two").mkdir()
    r2, git2 = _repo(tmp_path / "two", {**BASE, "src/page.tsx": RUN[
        "src/page.tsx"]})
    b2 = git2("rev-parse", "HEAD")
    h2 = _commit(r2, git2, {"src/total.ts": RUN["src/total.ts"]})
    assert leerie._mock_missing_export_evidence(
        r2, b2, h2, "src/page.test.tsx", src, MOCK_MSG) is None


VALUES = [("messages/es.json", "Email.sendNow", "Enviar ahora"),
          ("messages/en.json", "Email.sendNow", "Send now"),
          ("messages/es.json", "Other.x", "y")]
CAUSE = {"kind": "moved_literal", "literal": "Enviar ahora",
         "literal_kind": "string", "file": "src/c.tsx"}
BASE_FACTS = {"title": "t", "asserts": [{"start": 1, "end": 1}],
              "literals": [{"kind": "string", "text": "Enviar ahora"}]}


@pytest.mark.parametrize("lits,ok", [
    ([{"kind": "string", "text": "Enviar ahora"}], True),
    ([{"kind": "string", "text": "Email.sendNow"}], True),
    ([{"kind": "string", "text": "sendNow"}], True),
    ([{"kind": "string", "text": "Send now"}], True),
    ([{"kind": "regex", "text": "sendnow", "flags": "i"}], True),
    ([{"kind": "regex", "text": "sendnow", "flags": ""}], False),
    ([{"kind": "regex", "text": "send.*", "flags": "i"}], False),
    ([{"kind": "string", "text": "y"}], False),
    ([], False),
])
def test_the_run_form_must_still_assert_the_moved_text(leerie, lits, ok):
    head = {"title": "t", "asserts": [{"start": 1, "end": 1}],
            "literals": lits}
    why = leerie._base_form_run_form_check(BASE_FACTS, head, [CAUSE], VALUES)
    assert (why == "") is ok
    if not ok:
        assert "no longer asserts the moved text `Enviar ahora`" in why


def test_the_run_form_must_still_have_the_case_and_its_assertions(leerie):
    check = leerie._base_form_run_form_check
    lits = [{"kind": "string", "text": "sendNow"}]
    assert check(BASE_FACTS, {"error": "case_not_found"}, [CAUSE], VALUES) \
        == "the run's form of the test no longer has this case"
    assert check(BASE_FACTS, {"title": "t", "asserts": [], "literals": lits},
                 [CAUSE], VALUES).startswith("the run's form of it makes 0 "
                                             "assertion(s)")
    assert check({"error": "facts_failed"}, {"title": "t"}, [CAUSE],
                 VALUES) == "its base form's facts could not be read"
    # A mock cause asks only that the case stays and asserts as much.
    mock = {"kind": "mock_missing_export", "module": "m", "export": "x"}
    assert check(BASE_FACTS, {"title": "t", "asserts": [{}], "literals": []},
                 [mock], VALUES) == ""
