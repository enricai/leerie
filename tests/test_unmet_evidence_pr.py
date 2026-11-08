"""What is still unmet when a run ends is shown as tests the reader can run
(DESIGN §6 *What is still unmet when the run ends is shown as tests the
reader can run*).

Barnacle 79c8fbe7 (PR #708) ended `unconverged` on item 8, "unmet on a
confirmed falsifier probe", and the PR showed nothing more: the probe, the
clearest statement of what was still wrong, stayed in the run directory, and
the held-out cases still failing were only counted. These drive the real
path — a real falsifier confirm under B* and a real held-out evaluation, both
running pytest in the held-out sandbox, then the real `phase_finalize` with
its real `finalize.sh` / `cleanup.sh` and the real pr_writer composition —
stubbing only `claude_p`, and read the rendered PR text."""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_git_out
from tests.test_delivery_detection import (EFFORTS, MODELS, PROBE_REL,
                                           PROBE_SRC, _claim, _demonstrate,
                                           _detection_env, _falsifier_stub)

_git = run_git_out
HEADING = "## Still failing when the run ended"
SET_REL = "acc/test_sum.py"
SET_SRC = ("from calc import add\n\n"
           "def test_sum_of_ones():\n"
           "    assert add(1, 1) == 2\n")
CONTROL_REL = "acc/test_control.py"
CONTROL_SRC = ("from calc import add\n\n"
               "def test_zero():\n"
               "    assert add(2, 0) == 2\n")


@pytest.fixture(autouse=True)
def _quiet_host(leerie, monkeypatch):
    # B*'s gate reads the host's PSI; pin it quiet so a loaded host cannot
    # stretch these tests by the gate's wait.
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


def _held_out_set(st, tmp_path: Path, defect_src: str = SET_SRC) -> dict:
    d = tmp_path / "set-1"
    (d / "acc").mkdir(parents=True)
    (d / SET_REL).write_text(defect_src)
    (d / CONTROL_REL).write_text(CONTROL_SRC)
    return {"index": 1, "valid": True, "dir": str(d),
            "defect_files": [SET_REL], "control_files": [CONTROL_REL],
            "support_files": [],
            "cases": {SET_REL: ["test_sum_of_ones"],
                      CONTROL_REL: ["test_zero"]}}


def _run_to_the_end_of_execution(leerie, tmp_path, monkeypatch, *,
                                 defect_src: str = SET_SRC):
    """Everything a run does before finalize that this rule reads, for real:
    a commit on the run branch, delivery detection with a falsifier whose
    probe fails under B*, and the held-out set evaluated on the final tree.
    Returns `(st, caps, staging)`."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch, k=1)
    st.data["required_items"] = [st.data["required_items"][0]]
    staging = st.run_dir / "worktrees" / "staging"
    _git(staging, "config", "user.email", "t@x")
    _git(staging, "config", "user.name", "t")
    (staging / "notes.txt").write_text("the run's change\n")
    _git(staging, "add", "notes.txt")
    _git(staging, "commit", "-qm", "the run's change")
    (st.run_dir / "working-branch").write_text("main\n")
    st.save()

    def behave(kw, n):
        return _demonstrate(kw, n)
    _falsifier_stub(leerie, monkeypatch, st, behave)
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, MODELS, EFFORTS))
    set_ = _held_out_set(st, tmp_path, defect_src)
    final = asyncio.run(leerie._evaluate_acceptance_sets(
        st, caps, str(staging), [set_], "final"))
    st.data["acceptance"] = {"sets": [set_], "gate": {
        "final": final, "final_sha": _git(staging, "rev-parse", "HEAD")}}
    st.data["waves"] = [["feat-001"]]
    st.data["completed_waves"] = 1
    st.data["subtask_status"] = {"feat-001": "complete"}
    st.save()
    return st, caps, staging


def _finalize(leerie, monkeypatch, st, caps, pr_body: str = "the body"):
    """The real `phase_finalize`, pushed, with only `claude_p` stubbed: the
    pr_writer answers `pr_body`, every other worker fails."""
    async def fake_claude_p(**kw):
        if kw["schema_key"] == "pr_writer":
            return {"title": "Fix add", "body": pr_body,
                    "used_template": None}
        raise RuntimeError("no other worker runs in this test")
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = dict(caps)
    caps["max_total_workers"] = 1000
    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=False, no_verify=False, caps=caps,
        models=MODELS, efforts=EFFORTS))
    return json.loads((st.run_dir / "run.json").read_text())


def _in_order(text: str, parts: list[str]) -> None:
    at = -1
    for p in parts:
        nxt = text.find(p, at + 1)
        assert nxt != -1, f"{p!r} missing after offset {at}:\n{text}"
        at = nxt


def test_an_unconverged_run_shows_the_probe_and_the_held_out_case(
        leerie, tmp_path, monkeypatch):
    st, caps, _ = _run_to_the_end_of_execution(leerie, tmp_path, monkeypatch)
    worker_copy = ("Summary.\n\n" + HEADING + "\n\n- fabricated by the "
                   "worker\n\n## Notes\n\nkept")
    rj = _finalize(leerie, monkeypatch, st, caps, pr_body=worker_copy)

    assert rj["outcome"] == "unconverged"
    end = rj["pr_end_state"]
    item = st.data["required_items"][0]["item"]
    root = st.run_dir / "unmet-evidence"
    _in_order(end, [
        "## Unconverged",
        HEADING,
        str(root),
        # The falsifier's unmet item: its text, the probe, the case, the
        # failing assertion and its line, B*, the runner's message, source.
        "### Item 0: `" + PROBE_REL + "`",
        "> " + item,
        "- Full file: `" + str(root / "item-0" / PROBE_REL) + "`",
        "- B* verdict: fails under B* on the final tree",
        "- Case: `test_add_returns_the_sum`",
        "- Failing assertion (line 5): `assert add(2, 3) == 5`",
        "Failure message:",
        "assert -1 == 5",
        "Source (lines 1–5 of 5):",
        ">    5 |     assert add(2, 3) == 5",
        # The held-out case still failing on the tree that ships.
        "### Held-out set 1: `" + SET_REL + "`",
        "- Full file: `" + str(root / "set-1" / SET_REL) + "`",
        "- B* verdict: fails under B* on the final tree",
        "test_sum_of_ones`",
        "- Failing assertion (line 4): `assert add(1, 1) == 2`",
        "Failure message:",
        "assert 0 == 2",
        ">    4 |     assert add(1, 1) == 2",
    ])
    # The passing control is copied, never listed as failing.
    assert "test_zero" not in end
    assert (root / "set-1" / CONTROL_REL).read_text() == CONTROL_SRC
    # Every file in full, in the run directory, under its repository path.
    assert (root / "item-0" / PROBE_REL).read_text() == PROBE_SRC
    assert (root / "set-1" / SET_REL).read_text() == SET_SRC
    # The pr_writer path: the worker's copy of the code-owned heading is
    # stripped, the real section appended once, the rest of its body kept.
    body = rj["pr_body"]
    assert body.count(HEADING) == 1
    assert "fabricated by the worker" not in body
    assert "## Notes\n\nkept" in body
    assert body.index("## Notes") < body.index(HEADING)
    _in_order(body, [HEADING, PROBE_REL, "assert add(2, 3) == 5",
                     SET_REL, "assert add(1, 1) == 2"])
    # The launcher's fallback appends `pr_end_state` as-is; the Python
    # reference of that fallback renders the same section.
    fallback = leerie.compose_pr_body(st.data, st.run_id)
    assert end in fallback


def test_the_run_report_logs_the_section(leerie, tmp_path, monkeypatch,
                                         capsys):
    st, caps, _ = _run_to_the_end_of_execution(leerie, tmp_path, monkeypatch)
    capsys.readouterr()
    _finalize(leerie, monkeypatch, st, caps)
    out = capsys.readouterr()
    text = out.out + out.err
    _in_order(text, ["still failing when the run ended",
                     str(st.run_dir / "unmet-evidence"),
                     PROBE_REL, "assert add(2, 3) == 5",
                     SET_REL, "assert add(1, 1) == 2"])


def test_the_paths_reach_the_operator_translated(leerie, tmp_path,
                                                 monkeypatch):
    """Every path the section prints goes through `_operator_path`, so the
    operator reads a path on their host, not the container's."""
    st, caps, _ = _run_to_the_end_of_execution(leerie, tmp_path, monkeypatch)
    real = leerie._operator_path
    host = "/home/dev/.leerie/myrepo"
    monkeypatch.setattr(
        leerie, "_operator_path",
        lambda p: real(p).replace(str(st.run_dir), host + "/runs/x"))
    end = _finalize(leerie, monkeypatch, st, caps)["pr_end_state"]
    assert f"`{host}/runs/x/unmet-evidence`" in end
    assert f"`{host}/runs/x/unmet-evidence/item-0/{PROBE_REL}`" in end
    assert f"`{host}/runs/x/unmet-evidence/set-1/{SET_REL}`" in end
    assert str(st.run_dir) not in end


def test_nothing_is_published_before_the_run_ends(leerie, tmp_path,
                                                  monkeypatch):
    """Up to finalize the probe and the set are held out (I3): no record, no
    copy, no PR section, and nothing a later reader is handed carries the
    probe's text or the runner's message."""
    st, caps, _ = _run_to_the_end_of_execution(leerie, tmp_path, monkeypatch)
    # The evidence a finished run will show exists: both producers ran.
    sample = st.data["delivery_gate"]["items"][0]["falsifier"]["samples"][0]
    assert sample["failure_line"] == 5
    assert "assert -1 == 5" in sample["failure_message"]
    res = st.data["acceptance"]["gate"]["final"][0]
    ev = next(iter(res["failing_case_evidence"][SET_REL].values()))
    assert ev["line"] == 4 and "assert 0 == 2" in ev["message"]
    assert res["failing_basis"] == {SET_REL: "bstar"}
    # ...and none of it is published before the run ends.
    assert "unmet_evidence" not in st.data
    assert not (st.run_dir / "unmet-evidence").exists()
    mid = leerie._format_pr_end_state_section(st.data)
    assert HEADING not in mid
    assert "assert add(2, 3) == 5" not in mid
    assert "assert add(1, 1) == 2" not in mid
    # What a fixer or the next run's planner is handed names neither.
    unmet = json.dumps(st.data["delivery_gate"]["unmet_final"])
    assert "assert" not in unmet and PROBE_REL not in unmet
    residual = json.dumps(leerie._acceptance_residual_of(
        st.data["acceptance"]["gate"]["final"],
        st.data["acceptance"]["sets"]))
    assert "assert 0 == 2" not in residual
    assert "failing_case_evidence" not in residual


def test_a_done_run_shows_no_such_section(leerie, tmp_path, monkeypatch):
    """A run that ends `done` left nothing unmet: no section, no record, no
    copies — even with a held-out set measured on its final tree."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch, k=1)
    st.data["required_items"] = []
    staging = st.run_dir / "worktrees" / "staging"
    _git(staging, "config", "user.email", "t@x")
    _git(staging, "config", "user.name", "t")
    (staging / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git(staging, "commit", "-qam", "fix add")
    (st.run_dir / "working-branch").write_text("main\n")
    set_ = _held_out_set(st, tmp_path)
    final = asyncio.run(leerie._evaluate_acceptance_sets(
        st, caps, str(staging), [set_], "final"))
    assert final[0]["passed"] is True
    st.data["acceptance"] = {"sets": [set_], "gate": {
        "final": final, "final_sha": _git(staging, "rev-parse", "HEAD")}}
    st.data["delivery_gate"] = {"unmet_final": [], "items": [],
                                "final_tree": leerie._shipping_tree(st)}
    st.data["test_id_diff"] = {"tree_sha": leerie._shipping_tree(st),
                               "new": []}
    st.data["waves"] = [["feat-001"]]
    st.data["completed_waves"] = 1
    st.data["subtask_status"] = {"feat-001": "complete"}
    st.save()
    rj = _finalize(leerie, monkeypatch, st, caps)
    assert rj["outcome"] == "done", st.data.get("outcome_reasons")
    assert HEADING not in rj["pr_end_state"]
    assert HEADING not in rj["pr_body"]
    assert "unmet_evidence" not in st.data
    assert not (st.run_dir / "unmet-evidence").exists()


def test_the_section_is_bounded(leerie, tmp_path, monkeypatch):
    """A per-file window around the failing line, a per-file case count,
    and a total budget past which files and cases are counted, pointing at
    the directory."""
    from tests.test_delivery_detection import _st, _repo
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    big = "\n".join(f"line_{n} = {n}" for n in range(1, 501)) + "\n"
    sets, final = [], []
    for k in range(1, 41):
        d = tmp_path / f"s{k}"
        (d / "t").mkdir(parents=True)
        rel = f"t/test_big_{k}.py"
        (d / rel).write_text(big)
        ids = [f"{rel} :: case_{c}" for c in range(15)]
        sets.append({"index": k, "valid": True, "dir": str(d),
                     "defect_files": [rel], "control_files": [],
                     "support_files": []})
        final.append({"index": k, "passed": False, "unmeasured": False,
                      "failing_files": [rel], "failing_cases": {rel: ids},
                      "failing_case_evidence": {rel: {
                          c: {"message": "AssertionError", "line": 400}
                          for c in ids}},
                      "failing_basis": {rel: "bstar"}})
    st.data["acceptance"] = {"sets": sets, "gate": {"final": final}}
    leerie._record_unmet_evidence(st)
    lines = leerie._pr_unmet_evidence_lines(st.data)
    text = "\n".join(lines)
    assert len(text) <= leerie._UNMET_SECTION_MAX + 2000
    shown = text.count("### Held-out set ")
    assert 0 < shown < 40
    root = st.run_dir / "unmet-evidence"
    assert (f"({40 - shown} more file(s) with {(40 - shown) * 15} failing "
            f"case(s) not shown here; every file is in full under "
            f"`{root}`.)") in text
    # The window holds the failing line, and no more than the cap.
    first = text[text.index("### Held-out set 1:"):]
    first = first[:first.index("### Held-out set 2:")]
    assert ">  400 | line_400 = 400" in first
    numbered = [x for x in first.splitlines() if " | line_" in x]
    assert len(numbered) == leerie._UNMET_SOURCE_LINES
    assert "of 500)" in first
    assert first.count("- Case: ") == leerie._UNMET_CASES_SHOWN
    assert "(5 more failing case(s) of this file not listed here.)" in first
    # Every file is still copied whole.
    assert all((root / f"set-{k}" / f"t/test_big_{k}.py").read_text() == big
               for k in range(1, 41))


def test_a_fence_in_the_source_cannot_close_the_block(leerie):
    st = {"unmet_evidence": {"dir": "/d", "entries": [{
        "kind": "held_out", "label": "Held-out set 1", "file": "t/x.md",
        "bstar": "bstar", "cases": [{"case": "c", "line": 1,
                                     "assertion": "x"}],
        "excerpt_start": 1, "excerpt": ["````", "## Not a heading"]}]}}
    text = "\n".join(leerie._pr_unmet_evidence_lines(st))
    assert "`````\n" in text and text.rstrip().endswith("`````")


# --- a run that ends `no_work` with held-out sets still failing -------------

def _no_work_env(leerie, tmp_path, monkeypatch, *, prior_dispute: bool):
    """A held no-work confirmation settled on the held-out sets run for real
    on HEAD (`_evaluate_acceptance_sets` over the run's tree); with
    `prior_dispute`, the previous same-task run already disputed on them —
    the dispute bound (barnacle 4777d790)."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch, k=1)
    staging = st.run_dir / "worktrees" / "staging"
    set_ = _held_out_set(st, tmp_path)
    st.data["acceptance"] = {"sets": [set_]}
    # Settled before planning: no delivery verdict exists yet.
    st.data.pop("delivery_gate", None)
    st.data["no_work_pending"] = True
    st.data["no_work_confirmation"] = {"classifier_evidence": "c",
                                       "judge_evidence": "j"}
    st.save()
    if prior_dispute:
        prev = st.run_dir.parent / "run-prev"
        prev.mkdir()
        (prev / "state.json").write_text(json.dumps({
            "task": st.data["task"],
            "acceptance_dispute": {"failing_sets": 1, "cases": ["x"]}}))
        (prev / "orchestrator.exit_code").write_text("0")

    async def on_head(st_, caps_):
        return await leerie._evaluate_acceptance_sets(
            st_, caps_, str(staging), st_.data["acceptance"]["sets"], "head")
    monkeypatch.setattr(leerie, "_acceptance_results_on_head", on_head)
    return st, caps


def test_a_no_work_run_at_the_dispute_bound_shows_what_still_fails(
        leerie, tmp_path, monkeypatch, capsys):
    st, caps = _no_work_env(leerie, tmp_path, monkeypatch, prior_dispute=True)
    capsys.readouterr()
    assert asyncio.run(leerie._settle_pending_no_work(st, caps)) is True
    assert st.data["no_work_acceptance"]["verdict"] == "accepted after dispute"
    rj = json.loads((st.run_dir / "run.json").read_text())
    assert rj["finished_at"] and rj["outcome"] == "no_work"
    root = st.run_dir / "unmet-evidence"
    ue = rj["unmet_evidence"]
    assert ue["dir"] == str(root)
    _in_order(ue["text"], [
        str(root),
        "### Held-out set 1: `" + SET_REL + "`",
        "- Full file: `" + str(root / "set-1" / SET_REL) + "`",
        "- B* verdict: fails under B* on the final tree",
        "test_sum_of_ones`",
        "- Failing assertion (line 4): `assert add(1, 1) == 2`",
        "assert 0 == 2",
        ">    4 |     assert add(1, 1) == 2"])
    assert (root / "set-1" / SET_REL).read_text() == SET_SRC
    out = capsys.readouterr().out
    _in_order(out, ["still failing when the run ended", str(root), SET_REL,
                    "assert add(1, 1) == 2", "phase 3: nothing to _schedule"])


def test_a_dispute_that_plans_the_work_publishes_nothing(
        leerie, tmp_path, monkeypatch):
    """The first dispute does not end the run: it plans the work, and the
    sets stay held out."""
    st, caps = _no_work_env(leerie, tmp_path, monkeypatch,
                            prior_dispute=False)
    assert asyncio.run(leerie._settle_pending_no_work(st, caps)) is False
    assert "unmet_evidence" not in st.data
    assert not (st.run_dir / "unmet-evidence").exists()
    rj = (json.loads((st.run_dir / "run.json").read_text())
          if (st.run_dir / "run.json").exists() else {})
    assert "unmet_evidence" not in rj
    assert "assert add(1, 1) == 2" not in json.dumps(
        st.data["no_work_dispute"])


def test_a_verdict_known_from_validation_shows_the_validations_failure(
        leerie, tmp_path, monkeypatch):
    """HEAD is the commit the sets were validated on: the verdict is known
    without a run (`_acceptance_results_on_head`'s shortcut), and its
    messages and lines are the validation's own, from that same commit."""
    repo, head, st, caps = _detection_env(leerie, tmp_path, monkeypatch, k=1)
    set_ = _held_out_set(st, tmp_path)
    cid = f"{SET_REL} :: test_sum_of_ones"
    set_["base_failing_cases"] = {SET_REL: [cid]}
    set_["base_failure_messages"] = {SET_REL: {
        cid: f"assert 0 == 2\n{SET_REL}:4: AssertionError"}}
    st.data["acceptance"] = {"sets": [set_], "validity_base": head}
    # The judgment worktree, at the own start, where the stub of
    # `_ensure_planning_worktree` (conftest) points it.
    planning = st.run_dir / "worktrees" / leerie._worktree_dirname(
        st.run_dir.name, "planning")
    _git(st.run_dir / "repo", "worktree", "add", "-q", "--detach",
         str(planning), head)
    st.data["planning_worktree"] = str(planning)
    st.save()
    res = asyncio.run(leerie._acceptance_results_on_head(st, caps))
    assert res[0]["failing_basis"] == {SET_REL: "validated"}
    leerie._record_unmet_evidence(st, res)
    text = json.loads((st.run_dir / "run.json").read_text())[
        "unmet_evidence"]["text"]
    _in_order(text, ["- B* verdict: fails: it failed when the set was "
                     "validated on this very commit",
                     "- Failing assertion (line 4): `assert add(1, 1) == 2`",
                     "assert 0 == 2"])
