"""Review round 14's kept delivery items, executed end to end.

M2 — a delivery verdict counts only on the tree it was measured on (DESIGN
§6 *A delivery verdict counts only on the tree it was measured on*); a final
judge that fails in any way reads nothing.
M3 — a ratchet's unverified items are derived from what its routes read, so
an execution item whose ID diff measured nothing is unverified (DESIGN §8
*Every post-execution verdict is of the committed tree*).
M4 — an item index means one item everywhere it is written: the judge is
always handed the whole list (DESIGN §8 *Delivery detection*).

Each test is the round's driver, rewritten to assert the fix.
"""
from __future__ import annotations

import asyncio

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_delivery_detection import EFFORTS, MODELS, _detection_env
from tests.test_delivery_redrive import World


def _tree(staging) -> str:
    return _git(staging, "rev-parse", "HEAD^{tree}")


# --- M2 ---------------------------------------------------------------------

def test_a_final_judge_that_fails_any_way_reads_nothing(leerie, tmp_path,
                                                        monkeypatch):
    """The driver: the judge failed with a non-worker exception, detection
    raised, and the recheck's "nothing unmet" of an earlier commit decided
    `done`. Now the failure is a crash: every item is `unknown`, listed in
    the PR, and the verdict is of the shipping tree."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch,
                                          judged=False, k=1)
    st.data["delivery_gate"] = {"unmet_after": [], "judged_after": {
        "sha": "0" * 40, "tree": "1" * 40, "unmet": [], "details": []}}
    st.save()
    calls: list[int] = []

    async def broken_judge(*a, **k):
        calls.append(1)
        raise RuntimeError("the checkout it reads could not be made")
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", broken_judge)

    async def fake_claude_p(**kw):
        return {"status": "no_probe"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie._run_delivery_detection(st.run_dir, st, caps, MODELS,
                                               EFFORTS))
    assert len(calls) == 1 + caps["delivery_judge_crash_retries"]
    gate = st.data["delivery_gate"]
    staging = st.run_dir / "worktrees" / "staging"
    assert gate["final_tree"] == _tree(staging)
    by_idx = {r["item_index"]: r for r in gate["items"]}
    # Nothing read items 0 and 3; item 2 is settled by its own diff of the
    # untouched paths, item 1 is external.
    assert by_idx[0]["verdict"] == by_idx[3]["verdict"] == "unknown"
    assert by_idx[2]["verdict"] == "met" and by_idx[1]["verdict"] == "external"
    body = leerie._format_pr_end_state_section(st.data)
    assert "crashed on every attempt" in body


def _ship(leerie, tmp_path):
    """A repository whose checkout is on the run branch: its tree is the
    tree that ships (`_shipping_tree`, the run branch's tree)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "leerie/runs/fix-001-abcdef")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "a.py").write_text("x = 1\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    st = leerie.State(tmp_path / ".leerie", "fix-001-abcdef", repo_root=repo)
    st.data = {"task": "x", "subtask_status": {"a": "complete"}}
    # The run branch lives in the run's own repository (DESIGN §6 *The
    # run's own repository*), where `_shipping_tree` reads it; the tests
    # commit on it through `repo`, a worktree of that repository.
    run_repo = leerie._ensure_run_repo(st)
    _git(repo, "checkout", "-q", "--detach")
    _git(repo, "update-ref", "-d", "refs/heads/leerie/runs/fix-001-abcdef")
    wt = tmp_path / "staging"
    _git(run_repo, "worktree", "add", "-q", "-b",
         "leerie/runs/fix-001-abcdef", str(wt), "refs/leerie/user-head")
    return st, wt


def test_a_verdict_of_an_earlier_tree_never_decides_done(leerie, tmp_path):
    st, staging = _ship(leerie, tmp_path)
    earlier = _tree(staging)
    (staging / "a.py").write_text("x = 2\n")
    _git(staging, "commit", "-qam", "later repair")
    st.data["delivery_gate"] = {"unmet_after": [], "judged_after": {
        "sha": "x", "tree": earlier, "unmet": [], "details": []}}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    (reason,) = st.data["outcome_reasons"]
    assert "not read on the tree that ships" in reason
    assert earlier[:12] in reason and _tree(staging)[:12] in reason


def test_a_verdict_of_the_shipping_tree_decides(leerie, tmp_path):
    st, staging = _ship(leerie, tmp_path)
    st.data["delivery_gate"] = {"unmet_final": [], "final_tree":
                                _tree(staging)}
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    st.data["delivery_gate"]["unmet_final"] = [{"item": "i"}]
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == ["1 required item(s) still unmet"]


def test_final_detection_wins_over_an_older_pass(leerie, tmp_path):
    """The latest pass decides: a recheck of the shipping tree does not
    rescue a final verdict of another tree, nor the reverse."""
    st, staging = _ship(leerie, tmp_path)
    ship = _tree(staging)
    st.data["delivery_gate"] = {
        "unmet_after": [], "judged_after": {"tree": ship},
        "unmet_final": [], "final_tree": "f" * 40}
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    st.data["delivery_gate"] = {
        "unmet_after": [{"item": "i"}], "judged_after": {"tree": "f" * 40},
        "unmet_final": [], "final_tree": ship}
    assert leerie._decide_outcome(st, any_commits=True) == "done"


# --- M3 ---------------------------------------------------------------------

def test_an_unmeasured_execution_item_is_unverified_not_met(
        leerie, tmp_path, monkeypatch):
    """The driver: a kept re-drive group whose ratchet's ID diff measured
    nothing kept its execution item `met`, re-keyed the verdict to the new
    commit, listed nothing, and the run ended `done`."""
    items = [{"item": "the integration suite passes", "span": "S1",
              "verifiability": "tree"}]
    w = World(leerie, tmp_path, items=items)
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["delivery_gate"]["items"] = [
        {"item_index": 0, "item": items[0]["item"], "verdict": "met",
         "by": [], "judge": {"met_evidence": "suite passed"}}]
    w.st.data["delivery_gate"]["final_sha"] = head
    w.st.data["delivery_gate"]["final_tree"] = _tree(w.staging)
    w.st.save()
    w.install(monkeypatch)
    implementer = leerie.claude_p

    async def workers(**kw):
        key = kw["schema_key"]
        if key == "implementer":
            return await implementer(**kw)
        if key == "verifiability_classifier":
            return {"items": [{"item_index": 0,
                               "verifiability": "execution",
                               "reason": "r"}]}
        raise leerie.WorkerError(f"no {key} in this test")
    monkeypatch.setattr(leerie, "claude_p", workers)

    async def unmeasured(*a, **k):
        return {"new": [], "pre_existing": [], "no_verdict": [],
                "resource_affected": [], "unmeasured": True,
                "intermittent_regression": []}
    monkeypatch.setattr(leerie, "_ratchet_exec", unmeasured)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert not g.get("rolled_back")
    assert g["ratchet"]["exec_unchecked"] == [0]
    assert g["ratchet"]["unverified"] == [0]
    gate = w.st.data["delivery_gate"]
    (row,) = gate["items"]
    assert row["verdict"] == "unknown"
    assert gate["final_sha"] == head              # not re-keyed
    assert "0" in w.st.data["delivery_ratchet"]["unverified"]
    body = leerie._format_pr_end_state_section(w.st.data)
    assert "the integration suite passes" in body
    w.st.data["subtask_status"] = {"s1": "complete", "s2": "complete"}
    w.st.data.pop("acceptance", None)
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"


def test_the_ratchet_derives_unverified_from_what_it_read(leerie, tmp_path,
                                                          monkeypatch):
    """Unit level: of three prior met items — one untouched-paths item read
    by its diff, one execution item measured, one execution item not — only
    the last is unverified; with the ID diff measured, none is."""
    items = [{"item": "a", "span": "S0", "verifiability": "tree"},
             {"item": "b", "span": "S1", "verifiability": "tree"}]
    w = World(leerie, tmp_path, items=items)
    st = w.st
    st.data["delivery_gate"]["items"] = [
        {"item_index": i, "item": it["item"], "verdict": "met", "by": []}
        for i, it in enumerate(items)]
    st.save()

    async def kinds(*a, **k):
        return {0: "execution", 1: "execution"}
    monkeypatch.setattr(leerie, "_ratchet_verifiability", kinds)
    head = _git(w.staging, "rev-parse", "HEAD")
    for ex, want in (({"unmeasured": True}, [0, 1]),
                     ({"unmeasured": False}, [])):
        rat = asyncio.run(leerie._delivery_ratchet(
            st, w.caps, {}, {}, w.staging, head, head, exec_result=ex,
            label="t"))
        assert rat["unverified"] == want, (ex, rat)


# --- M4 ---------------------------------------------------------------------

def test_a_recheck_of_item_1_keeps_item_0s_worker_only_entry(
        leerie, tmp_path, monkeypatch):
    """The driver: the re-check handed the judge only item 1, as its local
    item 0, and replaced global item 0's worker-only entry."""
    items = [{"item": "the e2e suite passes", "span": "S0",
              "verifiability": "tree"},
             {"item": "a is exported as 1", "span": "S1",
              "verifiability": "tree"}]
    w = World(leerie, tmp_path, items=items)
    st, staging = w.st, w.staging
    line1 = (staging / "src" / "a.ts").read_text().splitlines()[0]
    st.data["needs_human_verification"] = [{
        "source": leerie.RECORD_WORKER_GAP_SOURCE, "item_index": 0,
        "detail": "the e2e suite passes — its only evidence is a command a "
                  "worker ran ..."}]
    st.data["delivery_gate"]["unmet_final"] = [
        {"item_index": 1, "item": items[1]["item"], "by": ["judge"],
         "evidence": "x"}]
    st.save()
    asked: list[str] = []

    async def judge(**kw):
        assert kw["schema_key"] == "delivery_judge"
        asked.append(kw["user_prompt"])
        return {"verdicts": [{"item_index": 1, "met": True, "evidence": "e",
                              "parts": [{"requirement": "r",
                                         "kind": "behavioral",
                                         "status": "met", "cites": [{
                                             "path": "src/a.ts",
                                             "line_start": 1, "line_end": 1,
                                             "quote": line1}]}]}]}
    monkeypatch.setattr(leerie, "claude_p", judge)
    caps = {**leerie.DEFAULT_CAPS, "max_parallel": 1}
    out = asyncio.run(leerie._redrive_recheck_items(
        st.run_dir, st, caps, {"delivery_judge": "sonnet"},
        {"delivery_judge": "medium"}, staging, [1]))
    assert out == {1: {"unmet": False, "by": [], "evidence": ""}}
    # The judge saw item 1 under its own index, and only item 1.
    assert '"item_index": 1' in asked[0]
    assert '"item_index": 0' not in asked[0]
    entries = [e for e in st.data.get("needs_human_verification") or []
               if e.get("source") == leerie.RECORD_WORKER_GAP_SOURCE]
    assert [e["item_index"] for e in entries] == [0]
