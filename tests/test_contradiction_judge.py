"""A pinned case a required item's probe contradicts is task-required (DESIGN
§8 *Delivery re-drive* rule 4).

Barnacle 5ddc1d67 (leerie 0.62.0): items 0 and 1 asked the HTTP client to keep
POST and its body across a 303, the repository's own test pinned "degrades a
303 to GET and drops the body", and all three re-drive groups were rolled back
on the conflict. No implementation passes both. A `contradiction_judge` (opus,
high; lane B3) now says so, code verifies its quotes and the assertion spans,
and only then are the cases task-required: the group is judged by everything
else, and nothing is contested. Any other verdict leaves the conflict rule.

Every re-drive test drives the real `_run_delivery_redrive` over a real git
repository and a real pytest (`tests/test_shown_probe_contest.py`'s `World`):
the probe re-run in the held-out sandbox, the failing-test-ID diff under B*,
the base-form check, the payload written and removed. Stubbed: `claude_p`
(the implementer, committing in staging, and the judge, answering a recorded
verdict), and the facts script's read of a pytest file, which it cannot parse
(`_facts_for`, as every pytest-probe gate test stubs it). The facts script
itself is driven for real on TS tests where node and typescript are present.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.test_delivery_detection import _facts_for, _placed, _ts_dir
from tests.test_shown_probe_contest import (HONEST, PIN_ID, PIN_REL, PIN_SRC,
                                            World, _groups)

# The item, as 5ddc1d67's: the opposite of what the existing test pins, for
# the same input.
EXCL_PROBE = ("from calc import add\n\n\n"
              "def test_add_returns_the_sum():\n"
              "    assert add(2, 2) == 4\n")
# The group's rewrite of the pinned case, agreeing with the probe: what the
# judge must never be shown.
PIN_REWRITTEN = ("from calc import add\n\n\n"
                 "def test_add_of_equal_numbers_is_zero():\n"
                 "    assert add(2, 2) == 4  # rewritten by the run\n")

EXCLUSIVE = {
    "same_entry_point": True, "same_input_by_meaning": True,
    "same_observable": True, "mutually_exclusive": True,
    "evidence": [
        {"test": "pinned", "quote": "assert add(2, 2) == 0"},
        {"test": "independent", "quote": "assert add(2, 2) == 4"}],
    "explanation": "Both call add(2, 2) and assert its value: 0 and 4."}
BOTH_CAN_HOLD = {**EXCLUSIVE, "mutually_exclusive": False,
                 "explanation": "an implementation can tell them apart"}
# Verbatim nowhere: the pinned case asserts 0, not "== 0.0".
QUOTE_NOT_FOUND = {**EXCLUSIVE, "evidence": [
    {"test": "pinned", "quote": "assert add(2, 2) == 0.0"},
    {"test": "independent", "quote": "assert add(2, 2) == 4"}]}
# Verbatim, but the pinned quote is its import, no assertion of the case.
NOT_AN_ASSERTION = {**EXCLUSIVE, "evidence": [
    {"test": "pinned", "quote": "from calc import add"},
    {"test": "independent", "quote": "assert add(2, 2) == 4"}]}

# The intent resolver's answers to an `unsettled` verdict's hand-off (DESIGN
# §11 *Intent conflicts are settled with evidence, else asked*, source (c)),
# each quoting the task verbatim so code trusts it.
def _intent(answer: str, status: str = "settled") -> dict:
    return {"decision": f"{answer}.", "answer": answer, "status": status,
            "evidence": [{"source": "task", "quote": "make add return the sum",
                          "locator": "task.md"}]}


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    # B*'s gate reads the host's PSI; pin it quiet so a loaded host cannot
    # stretch these tests by the gate's wait.
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


@pytest.fixture
def pytest_facts(leerie, monkeypatch):
    """The facts script reads a JS/TS case's syntax; a pytest file's facts
    are what it would report: its `assert` statements as the assertions."""
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", _placed(
        lambda tree, src, case: _facts_for(Path(src).read_text())))


class Judged(World):
    """`World` with the probe asserting the opposite of the pinned case for
    the same input, and `claude_p` answering the judge with `verdicts` in
    order, recording what each call was shown before its payload goes."""

    def __init__(self, leerie, tmp_path: Path, verdicts: list[dict]):
        super().__init__(leerie, tmp_path)
        probe = (self.st.run_dir / "acceptance" / "probes" / "i0"
                 / self.probe_rel)
        probe.write_text(EXCL_PROBE)
        self.verdicts = list(verdicts)
        self.judged: list[dict] = []
        # The task does not ask to change what the pinned case pins: the
        # conflict rule stands, as before the hand-off existed.
        self.intent = _intent("no")
        self.resolved: list[dict] = []

    def run(self, monkeypatch) -> dict:
        world = self

        async def fake_claude_p(**kw):
            if kw["schema_key"] == "contradiction_judge":
                cwd = Path(kw["cwd"])
                world.judged.append({
                    **{k: kw[k] for k in ("user_prompt", "model", "effort",
                                          "allowed_tools", "autonomous")},
                    "cwd": cwd, "files": {
                        p.relative_to(cwd).as_posix(): p.read_text()
                        for p in cwd.rglob("*") if p.is_file()}})
                return world.verdicts.pop(0)
            if kw["schema_key"] == "intent_resolver":
                world.resolved.append(kw)
                return world.intent
            assert kw["schema_key"] == "implementer", kw["schema_key"]
            world.calls.append({"prompt": kw["user_prompt"],
                                "cwd": kw["cwd"]})
            world.kw = kw
            edit = world.edits.pop(0) if world.edits else None
            if edit:
                edit(world)
            return {"status": "complete", "summary": "done"}
        monkeypatch.setattr(self.L, "claude_p", fake_claude_p)
        asyncio.run(self.L._run_delivery_redrive(
            self.st.run_dir, self.st, self._caps(),
            {"implementer": "sonnet"}, {"implementer": "low"}))
        return self.st.data["delivery_redrive"]


def _fix_and_update_test(w) -> None:
    w.commit({"calc.py": HONEST, PIN_REL: PIN_REWRITTEN})


@pytest.mark.usefixtures("pytest_facts")
def test_an_exclusive_verdict_keeps_the_fix_and_the_test_update(
        leerie, tmp_path, monkeypatch):
    """5ddc1d67's shape: the group fixes the code and rewrites the pinned
    case to the item's behaviour. Its base form fails on the commit, and
    the judge shows the two cannot both pass: the group is kept, the probe
    is not contested, the item is met, and the case is listed as changed
    because the item contradicts it — not as a regression."""
    w = Judged(leerie, tmp_path, [EXCLUSIVE])
    w.edits = [_fix_and_update_test]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["probes_after"] == {"0": "pass"}
    assert not g.get("rolled_back"), g.get("rollback_reason")
    assert "base_form_changed" not in g and "probe_conflict" not in g
    [tr] = g["task_required"]
    assert PIN_ID in tr["id"] and tr["item_index"] == 0
    assert rec["contested_probes"] == []
    [v] = rec["contradiction_verdicts"]
    assert v["status"] == "exclusive" and v["failures"] == []
    assert v["quotes"] == {"pinned": "assert add(2, 2) == 0",
                           "independent": "assert add(2, 2) == 4"}
    assert (v["model"], v["effort"]) == ("opus", "high")
    # The fix and the test update are what staging holds.
    assert _git(w.staging, "show", "HEAD:calc.py") == HONEST.rstrip("\n")
    assert _git(w.staging, "show", f"HEAD:{PIN_REL}") == \
        PIN_REWRITTEN.rstrip("\n")
    # The re-check re-ran the probe on the kept commit: the item is met.
    assert w.st.data["delivery_gate"]["unmet_final"] == []
    # On the tree that ships the case's base form still fails, and it is
    # task-required: listed, not counted.
    asyncio.run(leerie._check_existing_behaviour(w.st.run_dir, w.st,
                                                 w._caps(), {}, {}))
    eb = w.st.data["existing_behaviour"]
    assert eb["changed"] == []
    [row] = eb["task_required"]
    assert PIN_ID in row["id"] and row["item_index"] == 0
    assert row["quote"] == "assert add(2, 2) == 4"
    leerie._decide_outcome(w.st, any_commits=True)
    assert not any("existing_behaviour_changed" in r
                   for r in w.st.data.get("outcome_reasons") or [])
    asyncio.run(leerie._store_tracked_test_report(
        w.st, w.st.data["base_sha"]))
    lines = leerie._pr_tracked_test_lines(w.st.data)
    [line] = [x for x in lines if x.startswith("- existing test changed")]
    assert line.startswith(
        "- existing test changed because required item 0 contradicts it: `")
    assert line.endswith(f"— the item's probe `{w.probe_rel}` asserts "
                         "`assert add(2, 2) == 4`")
    assert not any(x.startswith("- fails in its base form") for x in lines)


@pytest.mark.usefixtures("pytest_facts")
@pytest.mark.parametrize("verdict, status", [
    (BOTH_CAN_HOLD, "both_can_hold"),
    ({**EXCLUSIVE, "same_input_by_meaning": False}, "unsettled"),
], ids=["both_can_hold", "exclusive_without_its_facts"])
def test_a_verdict_that_is_not_exclusive_keeps_the_rollback(
        leerie, tmp_path, monkeypatch, verdict, status):
    """Today's rule, unchanged: rolled back, the probe contested and never
    handed on again, the case a base-form change."""
    w = Judged(leerie, tmp_path, [verdict])
    w.edits = [_fix_and_update_test]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["rolled_back"] is True and "task_required" not in g
    assert any(PIN_ID in x for x in g["base_form_changed"])
    [c] = rec["contested_probes"]
    assert c["item_index"] == 0 and PIN_ID in c["tests"][0]["id"]
    assert [v["status"] for v in rec["contradiction_verdicts"]] == [status]
    assert _git(w.staging, "rev-parse", "HEAD") == w.head
    assert len(w.calls) == 1
    # Only an unsettled verdict is handed to the intent resolver; its `no`
    # leaves the rule.
    assert len(w.resolved) == (status == "unsettled")


@pytest.mark.usefixtures("pytest_facts")
def test_an_unsettled_verdict_the_task_decides_is_task_required(
        leerie, tmp_path, monkeypatch):
    """Source (c): the judge's facts did not all hold, so its verdict is
    `unsettled`; the intent resolver, quoting the task, says the task asks
    to change what the pinned case pins. The case is task-required: the
    fix and the test update are kept."""
    w = Judged(leerie, tmp_path, [{**EXCLUSIVE,
                                   "same_input_by_meaning": False}])
    w.intent = _intent("yes")
    w.edits = [_fix_and_update_test]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert not g.get("rolled_back"), g.get("rollback_reason")
    [tr] = g["task_required"]
    assert PIN_ID in tr["id"]
    [v] = rec["contradiction_verdicts"]
    assert v["status"] == "unsettled" and v["intent_decision"] == "yes"
    [r] = w.resolved
    assert "Does the task ask to change the behaviour this existing test " \
        "case pins?" in r["user_prompt"]
    assert "assert add(2, 2) == 4" in r["user_prompt"]
    q = w.st.data["intent_questions"][v["intent_qid"]]
    assert q["source"] == "contradiction"
    assert leerie._redrive_task_required_cases(rec)


@pytest.mark.usefixtures("pytest_facts")
def test_an_unsettled_verdict_nobody_settles_stops_and_asks(
        leerie, tmp_path, monkeypatch):
    """The resolver cannot settle it either: an unattended run stops with
    the question written down, mid-group, and the group is the one a
    resume rolls back (`in_flight`)."""
    class _In:
        def isatty(self):
            return False
    monkeypatch.setattr("sys.stdin", _In())
    w = Judged(leerie, tmp_path, [{**EXCLUSIVE,
                                   "same_input_by_meaning": False}])
    w.intent = _intent("yes", "unsettled")
    w.edits = [_fix_and_update_test]
    with pytest.raises(SystemExit) as ex:
        w.run(monkeypatch)
    assert ex.value.code == leerie.EXIT_NEEDS_ANSWERS
    rec = w.st.data["delivery_redrive"]
    assert rec.get("in_flight")
    import json
    (q,) = json.loads((w.st.run_dir / "pending-questions.json")
                      .read_text())["questions"]
    assert q["id"].startswith("intent-contradiction-")
    assert q["choices"] == ["yes", "no"]


@pytest.mark.usefixtures("pytest_facts")
@pytest.mark.parametrize("verdict, failure", [
    (QUOTE_NOT_FOUND, "pinned quote not found: assert add(2, 2) == 0.0"),
    (NOT_AN_ASSERTION, "no quote of an assertion of the pinned case"),
], ids=["quote_not_verbatim", "quote_not_an_assertion"])
def test_an_unverified_exclusive_keeps_the_rollback(
        leerie, tmp_path, monkeypatch, verdict, failure):
    """The judge says exclusive with every fact true, but code cannot verify
    its evidence: unsettled, and the conflict rule stands."""
    w = Judged(leerie, tmp_path, [verdict])
    w.edits = [_fix_and_update_test]
    rec = w.run(monkeypatch)
    [g] = _groups(rec)
    assert g["rolled_back"] is True and "task_required" not in g
    [v] = rec["contradiction_verdicts"]
    assert v["status"] == "unsettled" and failure in v["failures"]
    assert len(rec["contested_probes"]) == 1
    assert leerie._redrive_task_required_cases(rec) == {}


@pytest.mark.usefixtures("pytest_facts")
def test_the_judge_never_sees_the_rewrite(leerie, tmp_path, monkeypatch):
    """The payload holds the pinned file as the base has it, the probe and
    the code — never the group's rewrite of the case, which would be a test
    agreeing with the probe. It runs read-only in that payload, on opus at
    high effort, and the payload is gone after the call."""
    w = Judged(leerie, tmp_path, [EXCLUSIVE])
    w.edits = [_fix_and_update_test]
    w.run(monkeypatch)
    [j] = w.judged
    assert j["files"][f"pinned/{PIN_REL}"] == PIN_SRC
    assert j["files"][f"independent/{w.probe_rel}"] == EXCL_PROBE
    assert "rewritten by the run" not in j["user_prompt"]
    assert not any("rewritten by the run" in t for t in j["files"].values())
    assert set(j["files"]) == {f"pinned/{PIN_REL}",
                               f"independent/{w.probe_rel}"}
    assert j["user_prompt"].startswith(
        f"PINNED case: in `pinned/{PIN_REL}`, the case titled \"")
    assert (j["model"], j["effort"]) == ("opus", "high")
    assert j["allowed_tools"] == "Read,Grep,Glob" and not j["autonomous"]
    assert j["cwd"].parent == w.st.run_dir / "contradiction"
    assert not j["cwd"].exists()


@pytest.mark.usefixtures("pytest_facts")
def test_a_task_required_case_left_failing_is_still_a_failure(
        leerie, tmp_path, monkeypatch):
    """The first group fixes the code and leaves the pinned test as the base
    had it, red: a new failure, so it is rolled back — but not contested,
    and the next group is told the item contradicts the test. That group
    updates the test and is kept on the verdict already recorded: one judge
    call for both."""
    w = Judged(leerie, tmp_path, [EXCLUSIVE])
    w.edits = [lambda w: w.commit({"calc.py": HONEST}), _fix_and_update_test]
    rec = w.run(monkeypatch)
    g1, g2 = _groups(rec)
    assert g1["rolled_back"] is True
    assert any(PIN_ID in x for x in g1["new_failures"])
    assert "probe_conflict" not in g1 and rec["contested_probes"] == []
    assert PIN_ID in w.calls[1]["prompt"]
    assert ("required item 0 contradicts this existing test case"
            in w.calls[1]["prompt"])
    assert not g2.get("rolled_back"), g2.get("rollback_reason")
    assert [x["item_index"] for x in g2["task_required"]] == [0]
    assert len(w.judged) == 1
    assert len(rec["contradiction_verdicts"]) == 1


# === the JS/TS path, through the real facts script ==========================

HAS_TS = _ts_dir() is not None
needs_ts = pytest.mark.skipif(not HAS_TS, reason="node or the typescript "
                              "package unavailable")

TS_PIN = """import { describe, expect, it } from "vitest";
import { follow } from "../src/client";

describe("client", () => {
  it("degrades a 303 to GET", () => {
    const r = follow("POST", 303);
    expect(r.method).toBe("GET");
  });
});
"""
TS_PROBE = """import { expect, it } from "vitest";
import { follow } from "../../src/client";

it("303 keeps POST", () => {
  const r = follow("POST", 303);
  expect(r.method).toBe("POST");
});
"""
TS_CLIENT = """import { log } from "./log";
export function follow(method: string, status: number) {
  log(status);
  return { method: status === 303 ? "GET" : method };
}
"""


@needs_ts
def test_the_real_facts_verify_a_ts_verdict(leerie, tmp_path):
    """Over a real git repository at its base: the code the two tests reach
    (the client and what it imports, transitively; never the tests), the
    real facts script's assertion spans in a held-out sandbox, and the
    check — a quoted `expect` passes, a quoted setup line of the case does
    not."""
    w = World(leerie, tmp_path)
    repo = w.st.repo_root
    (repo / "src").mkdir()
    (repo / "src" / "client.ts").write_text(TS_CLIENT)
    (repo / "src" / "log.ts").write_text("export function log(x: unknown) {}\n")
    (repo / "test").mkdir()
    (repo / "test" / "client.test.ts").write_text(TS_PIN)
    (repo / "node_modules").mkdir()
    os.symlink(_ts_dir(), repo / "node_modules" / "typescript")
    _git(repo, "add", "src", "test")
    _git(repo, "commit", "-qm", "ts")
    base = _git(repo, "rev-parse", "HEAD")
    run_repo = w.st.run_dir / "repo"
    _git(run_repo, "fetch", "-q", str(repo), f"+{base}:refs/leerie/ts")
    g = leerie._js_source_graph(run_repo, base)
    assert leerie._contradiction_code_files(g, [
        ("test/client.test.ts", TS_PIN),
        ("probe/p/p.test.ts", TS_PROBE)]) == ["src/client.ts", "src/log.ts"]
    pin = tmp_path / "pin.test.ts"
    pin.write_text(TS_PIN)
    probe = tmp_path / "probe.test.ts"
    probe.write_text(TS_PROBE)
    facts = {t: asyncio.run(leerie._contradiction_facts(
        w.st, str(repo), src, rel, case))
        for t, src, rel, case in (
            ("pinned", pin, "test/client.test.ts",
             "client degrades a 303 to GET"),
            ("independent", probe, "probe/p/p.test.ts", "303 keeps POST"))}
    assert all("error" not in f for f in facts.values()), facts
    asserts = {t: f["asserts"] for t, f in facts.items()}
    srcs = {"pinned": TS_PIN, "independent": TS_PROBE}
    code = {"src/client.ts": TS_CLIENT}
    good = {"evidence": [
        {"test": "pinned", "quote": 'expect(r.method).toBe("GET");'},
        {"test": "independent", "quote": 'expect(r.method).toBe("POST");'},
        {"test": "code", "quote": 'status === 303 ? "GET" : method'}]}
    assert leerie._contradiction_check(good, srcs, code, asserts) == (
        [], {"pinned": 'expect(r.method).toBe("GET");',
             "independent": 'expect(r.method).toBe("POST");'})
    setup = {"evidence": [
        {"test": "pinned", "quote": 'const r = follow("POST", 303);'},
        {"test": "independent", "quote": 'expect(r.method).toBe("POST");'}]}
    fails, _ = leerie._contradiction_check(setup, srcs, code, asserts)
    assert fails == ["no quote of an assertion of the pinned case"]


def test_the_status_rule(leerie):
    s = leerie._contradiction_status
    assert s(EXCLUSIVE, []) == "exclusive"
    assert s(EXCLUSIVE, ["x"]) == "unsettled"
    assert s(BOTH_CAN_HOLD, []) == "both_can_hold"
    for k in ("same_entry_point", "same_input_by_meaning", "same_observable"):
        assert s({**EXCLUSIVE, k: False}, []) == "unsettled"
    assert leerie._contradiction_rule({"status": "exclusive"}) == \
        "task_required"
    for st_ in ("both_can_hold", "unsettled"):
        assert leerie._contradiction_rule({"status": st_}) == "regression"
    assert leerie._contradiction_rule(None) == "regression"


def test_the_prompt_is_the_benched_one(leerie):
    """What ships is what was measured (lane B3, `h/contradiction_judge.md`,
    sha256 39b76d30…)."""
    import hashlib
    text = leerie._load_prompt("contradiction_judge")
    assert hashlib.sha256(text.encode()).hexdigest() == (
        "39b76d30a63adafcf64156d6c9b03ed348f4132438dd6d01e0188b8082a685a0")
