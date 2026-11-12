"""`leerie accept-heldout`: the operator accepts held-out cases as not
required by the task (DESIGN §8 *An operator accepts a held-out case the task
does not require*).

No automatic rule told an over-reaching held-out case from a genuine one
(lanes SB, SJ, OR), so on a task that never ended `done` an over-reaching
counted case stays unfinished work for as long as it fails: a draft PR, a
repair at the gate, the next run's residual. The verb records the operator's
call; `resume` acts on it.

Real paths throughout: the launcher's own arm and mutator (a subprocess, as
the operator runs it), a real git run repository, real `State`, the real
`_run_phases` resume of a finished run through `_decide_outcome` and the PR's
end-state section, the real acceptance gate on a real pytest repository, and
the real `host-finalize.sh` against real git with a stub `gh`. Only
`claude_p` and `gh` are stubbed.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.conftest import (HAS_JQ, init_git_repo, relocate_into_run_repo,
                            run_git_repo_first)
from tests.test_accept_blocked import _run_accept

REPO_ROOT = Path(__file__).resolve().parent.parent
HOST_FINALIZE_SH = REPO_ROOT / "scripts" / "host-finalize.sh"

RUN = "heldout-run-001"
TASK = "Fix the defect described in report.md"
F1 = "acc/defect_one.test.ts"
F2 = "acc/defect_two.test.ts"
C1 = "acc/control_one.test.ts"
# Case IDs as a runner names them: `<file> :: <describe path> <name>`.
OVER = f"{F1} :: write body asks for more than the report"
GOOD = f"{F1} :: write body the report's own scenario"
VARIANT = f"{F1} :: write body a variant the judge left out"
OTHER = f"{F2} :: other file a genuine case"
OVER_TITLE = "write body asks for more than the report"
MODELS: dict = {"pr_writer": "sonnet"}
EFFORTS: dict = {"pr_writer": None}


def _sets(st) -> list[dict]:
    d = st.run_dir / "acceptance" / "set-1"
    d.mkdir(parents=True)
    return [
        # Set 1: bounded; at the end OVER (counted) and VARIANT (not
        # counted) fail, GOOD passes.
        {"index": 1, "valid": True, "dir": str(d),
         "defect_files": [F1], "control_files": [C1],
         "cases": {F1: ["asks for more than the report",
                        "the report's own scenario",
                        "a variant the judge left out"],
                   C1: ["control"]},
         "base_failing_cases": {F1: [OVER, GOOD, VARIANT]},
         "base_case_status": {F1: {OVER: "failed", GOOD: "failed",
                                   VARIANT: "failed"}},
         "scope": {"bounded": True, "counted_cases": {F1: [OVER, GOOD]},
                   "judged": {}}},
        # Set 2: passes at the end.
        {"index": 2, "valid": True, "dir": str(d),
         "defect_files": [F2], "control_files": [],
         "cases": {F2: ["a genuine case"]},
         "base_failing_cases": {F2: [OTHER]},
         "scope": {"bounded": True, "counted_cases": {F2: [OTHER]},
                   "judged": {}}},
    ]


def _final(failing: dict) -> list[dict]:
    return [{"index": 1, "passed": False, "unmeasured": False,
             "failing_files": sorted(failing), "failing_cases": failing},
            {"index": 2, "passed": True, "unmeasured": False,
             "failing_files": [], "failing_cases": {}}]


def _residual(failing: dict) -> dict:
    return {"failing_sets": 1, "held_back_failing_sets": 0, "total_sets": 2,
            "cases": ["asks for more than the report",
                      "a variant the judge left out"],
            "sets": [1],
            "cases_by_set": {"1": ["asks for more than the report",
                                   "a variant the judge left out"]},
            "files_by_set": {"1": sorted(failing)},
            "failing_cases_by_set": {"1": failing}}


@pytest.fixture
def finished_run(leerie, tmp_path):
    """A run that finalized `unconverged` on one held-out set, its PR
    already open: a real run repository holding the run branch, and the
    outcome and end state the real decider and renderer gave it."""
    user = init_git_repo(tmp_path / "user")
    run_git_repo_first(user, "checkout", "-q", "-b", f"leerie/runs/{RUN}")
    (user / "fix.txt").write_text("the fix\n")
    run_git_repo_first(user, "add", "fix.txt")
    run_git_repo_first(user, "commit", "-q", "-m", "fix")
    run_git_repo_first(user, "checkout", "-q", "main")
    root = tmp_path / "state"
    st = leerie.State(root, RUN, repo_root=user)
    st.data = {"task": TASK, "worker_count": 3, "working_branch": "main",
               "waves": [["bugfix-001"]], "completed_waves": 1,
               "subtask_status": {"bugfix-001": "complete"},
               "finished_at": "2026-11-12T10:00:00Z",
               "current_phase": "phase 6: finalize"}
    st.save()
    relocate_into_run_repo(leerie, st)
    failing = {F1: [OVER, VARIANT]}
    st.data["delivery_gate"] = {"unmet_final": [],
                                "final_tree": leerie._shipping_tree(st)}
    st.data["acceptance"] = {"sets": _sets(st),
                             "gate": {"initial": _final(failing),
                                      "final": _final(failing),
                                      "residual": _residual(failing)}}
    st.save()
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    assert st.data["outcome_reasons"] == [
        "1 held-out acceptance set(s) still fail"]
    leerie._record_unmet_evidence(st)
    end = leerie._format_pr_end_state_section(st.data)
    assert "## Unconverged" in end
    leerie._write_run_json(
        st.run_dir, finished_at=st.data["finished_at"], no_push=False,
        branch=f"leerie/runs/{RUN}", working_branch="main",
        pushed_at="2026-11-12T10:01:00Z",
        pr_url="https://github.com/o/r/pull/7",
        pr_title="Fix the defect",
        pr_body="## Summary\nFixed it.\n\n" + end, pr_end_state=end)
    (st.run_dir / "orchestrator.exit_code").write_text("0\n")
    st.release_lock()
    return SimpleNamespace(root=root, user=user, run_dir=st.run_dir,
                           state=st.run_dir / "state.json", end=end)


def _accept(run, *specs: str) -> subprocess.CompletedProcess:
    return _run_accept(run.state, specs[0], verb="accept-heldout",
                       more=tuple(specs[1:]))


def _resume(leerie, run, monkeypatch) -> tuple[object, list]:
    calls: list = []

    async def _no_worker(**kw):
        calls.append(kw)
        return {}
    monkeypatch.setattr(leerie, "claude_p", _no_worker)
    st = leerie.State(run.root, RUN, repo_root=run.user)
    try:
        asyncio.run(leerie._run_phases(
            SimpleNamespace(resume=True, task=None, no_push=False,
                            pr_template=None, no_verify=False),
            dict(leerie.DEFAULT_CAPS), st.leerie_root, st, "codebase",
            "normal", MODELS, EFFORTS))
    finally:
        st.release_lock()
    return st, calls


def _run_json(run) -> dict:
    return json.loads((run.run_dir / "run.json").read_text())


def _state(run) -> dict:
    return json.loads(run.state.read_text())


# --- the verb, then a real resume of the finished run ------------------------

def test_an_accepted_case_turns_a_finished_run_done_and_the_pr_says_so(
        leerie, finished_run, monkeypatch):
    r = _accept(finished_run, f"1/{OVER_TITLE}")
    assert r.returncode == 0, r.stderr
    assert "ACCEPTED:" in r.stdout and OVER in r.stdout

    st, calls = _resume(leerie, finished_run, monkeypatch)

    assert calls == [], "a re-decided outcome calls no worker"
    assert st.data["outcome"] == "done"
    assert st.data["outcome_reasons"] == []
    rj = _run_json(finished_run)
    assert rj["outcome"] == "done"
    end = rj["pr_end_state"]
    assert "## Held-out cases accepted by the operator" in end
    assert f"`{OVER_TITLE}`" in end and f"`{F1}`" in end
    assert "operator's call, not leerie's" in end
    assert "## Unconverged" not in end
    # The open PR is updated by the host from exactly the text it holds.
    assert rj["pr_refresh"] == {"end_state_was": finished_run.end,
                                "outcome": "done"}
    assert finished_run.end not in rj["pr_body"] and end in rj["pr_body"]
    # The acceptance is read: a further resume decides nothing again.
    rec = st.data["acceptance"]["sets"][0]["operator_accepted"]
    assert "pending" not in rec
    assert rec["cases"][0]["case"] == OVER
    assert rec["scope_before"]["counted_cases"] == {F1: [OVER, GOOD]}


def test_a_finished_run_without_an_acceptance_is_not_redecided(
        leerie, finished_run, monkeypatch):
    """Control: the re-decision is keyed to the operator's acceptance, not
    to every resume of a finished run."""
    st, _ = _resume(leerie, finished_run, monkeypatch)
    assert st.data["outcome"] == "unconverged"
    assert "pr_refresh" not in _run_json(finished_run)


def test_a_variant_accepted_alone_leaves_the_run_unconverged(
        leerie, finished_run, monkeypatch):
    """Accepting a case that was not counted changes no outcome: the counted
    one still fails."""
    r = _accept(finished_run, "1/write body a variant the judge left out")
    assert r.returncode == 0, r.stderr
    assert "it was not counted already" in r.stdout
    st, _ = _resume(leerie, finished_run, monkeypatch)
    assert st.data["outcome"] == "unconverged"
    assert "## Held-out cases accepted by the operator" in _run_json(
        finished_run)["pr_end_state"]


# --- what the verb refuses ---------------------------------------------------

def test_a_live_run_is_refused(leerie, finished_run):
    """The run directory's lock: an orchestrator owning the run would
    overwrite the change with its next save."""
    before = finished_run.state.read_bytes()
    live = leerie.State(finished_run.root, RUN)
    try:
        r = _accept(finished_run, "1")
    finally:
        live.release_lock()
    assert r.returncode != 0
    assert "an orchestrator owns this run" in r.stderr
    assert finished_run.state.read_bytes() == before


@pytest.mark.parametrize("spec,why", [
    ("7", "this run has no held-out set 7 (its sets: 1, 2)"),
    ("1/no such case", "set 1 has no case named 'no such case'"),
    ("one", "expected <set> or <set>/<case>"),
    ("1/", "no case named after the set number"),
])
def test_a_bad_argument_changes_nothing(finished_run, spec, why):
    before = finished_run.state.read_bytes()
    # A good argument beside it: one bad argument changes nothing at all.
    r = _accept(finished_run, "2", spec)
    assert r.returncode != 0
    assert why in r.stderr, r.stderr
    assert finished_run.state.read_bytes() == before


def test_a_name_in_two_files_must_be_named_by_its_whole_id(finished_run):
    data = _state(finished_run)
    s1 = data["acceptance"]["sets"][0]
    twin = f"{F2} :: write body asks for more than the report"
    s1["base_failing_cases"][F2] = [twin]
    s1["scope"]["counted_cases"][F2] = [twin]
    finished_run.state.write_text(json.dumps(data))
    r = _accept(finished_run, f"1/{OVER_TITLE}")
    assert r.returncode != 0
    assert "names cases in 2 files" in r.stderr
    r = _accept(finished_run, f"1/{OVER}")
    assert r.returncode == 0, r.stderr
    counted = _state(finished_run)["acceptance"]["sets"][0]["scope"][
        "counted_cases"]
    assert counted == {F1: [GOOD], F2: [twin]}


def test_a_case_of_an_unbounded_set_is_refused_but_the_set_is_not(
        leerie, finished_run):
    data = _state(finished_run)
    data["acceptance"]["sets"][0]["scope"] = {"bounded": False,
                                              "error": "judge failed"}
    finished_run.state.write_text(json.dumps(data))
    r = _accept(finished_run, f"1/{OVER_TITLE}")
    assert r.returncode != 0
    assert "accept the whole set with 1" in r.stderr

    r = _accept(finished_run, "1")
    assert r.returncode == 0, r.stderr
    set_ = _state(finished_run)["acceptance"]["sets"][0]
    assert set_["scope"]["bounded"] is True
    assert set_["scope"]["counted_cases"] == {}
    assert set_["operator_accepted"]["scope_before"] == {
        "bounded": False, "error": "judge failed"}
    # Not work any more, by the readers' own predicate.
    assert not leerie._acceptance_result_has_work(
        set_, [F1], {F1: [OVER, VARIANT]})


def test_a_regressed_settled_bar_is_refused(finished_run):
    data = _state(finished_run)
    data["acceptance"]["sets"][1]["settled_bar"] = {"run": "earlier"}
    finished_run.state.write_text(json.dumps(data))
    before = finished_run.state.read_bytes()
    r = _accept(finished_run, "2")
    assert r.returncode != 0
    assert "regressed settled bar" in r.stderr
    assert finished_run.state.read_bytes() == before


def test_accepting_twice_is_a_noop(finished_run):
    assert _accept(finished_run, f"1/{OVER_TITLE}").returncode == 0
    before = finished_run.state.read_bytes()
    r = _accept(finished_run, f"1/{OVER_TITLE}")
    assert r.returncode == 0, r.stderr
    assert "NOOP:" in r.stdout and "already accepted" in r.stdout
    assert finished_run.state.read_bytes() == before


def test_a_worker_written_title_reaches_the_terminal_without_controls(
        finished_run):
    """Case titles come from tests a worker wrote (DESIGN §6 *No text leerie
    did not write reaches the operator's terminal as a control sequence*)."""
    data = _state(finished_run)
    evil = f"{F1} :: write body \x1b]52;c;cHduZWQ=\x07 sets the clipboard"
    data["acceptance"]["sets"][0]["base_failing_cases"][F1].append(evil)
    data["acceptance"]["sets"][0]["scope"]["counted_cases"][F1].append(evil)
    finished_run.state.write_text(json.dumps(data))
    r = _accept(finished_run, "1/nothing like it")
    assert r.returncode != 0
    assert "sets the clipboard" in r.stderr
    assert "\x1b" not in r.stderr and "\x07" not in r.stderr
    r = _accept(finished_run, f"1/{evil}")
    assert r.returncode == 0, r.stderr
    assert "sets the clipboard" in r.stdout
    assert "\x1b" not in r.stdout and "\x07" not in r.stdout


# --- the next run of the same task -------------------------------------------

def _next_run(leerie, run):
    st = leerie.State(run.root, "heldout-run-002", repo_root=run.user)
    st.data = {"task": TASK}
    st.save()
    return st


def test_the_next_run_is_not_handed_an_accepted_case(leerie, finished_run):
    nxt = _next_run(leerie, finished_run)
    try:
        before = leerie._prior_delivery_residual(nxt) or {}
        assert before.get("acceptance_unmet"), (
            "control: the counted failure is handed on before the acceptance")
        nxt.release_lock()
        assert _accept(finished_run, f"1/{OVER_TITLE}").returncode == 0
        nxt._acquire_lock()
        after = leerie._prior_delivery_residual(nxt) or {}
        assert not after.get("acceptance_unmet")
    finally:
        nxt.release_lock()


def test_a_kept_residual_names_no_accepted_case(leerie, finished_run):
    """Set 1 still has work (GOOD fails too); the accepted case's name is not
    handed to the next planner, the genuine one's is."""
    data = _state(finished_run)
    failing = {F1: [OVER, GOOD]}
    res = _residual(failing)
    res["cases_by_set"]["1"] = ["asks for more than the report",
                                "the report's own scenario"]
    data["acceptance"]["gate"]["residual"] = res
    finished_run.state.write_text(json.dumps(data))
    assert _accept(finished_run, f"1/{OVER_TITLE}").returncode == 0
    nxt = _next_run(leerie, finished_run)
    try:
        unmet = (leerie._prior_delivery_residual(nxt) or {}).get(
            "acceptance_unmet") or {}
    finally:
        nxt.release_lock()
    assert unmet.get("cases") == ["the report's own scenario"]


# --- the gate does not repair an accepted case --------------------------------

from tests.test_acceptance_gate import (  # noqa: E402  (fixtures, autouse)
    _caps, _clear_deps_memo, _confirm_at_once, _make_sets, _no_set_audit,
    _repo, _st, _staging)


def _gate(leerie, monkeypatch, st) -> list:
    calls: list = []

    async def fake_claude_p(**kw):
        calls.append(kw)
        return {}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie._run_acceptance_gate(
        st.run_dir, st, _caps(leerie, 5),
        {"conformer": "sonnet"}, {"conformer": "low"}))
    return calls


def _gate_run(leerie, tmp_path, *specs: str):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    _staging(st, repo)
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 2)}
    st.save()
    st.release_lock()
    if specs:
        r = _run_accept(st.path, specs[0], verb="accept-heldout",
                        more=tuple(specs[1:]))
        assert r.returncode == 0, r.stderr
    st = leerie.State(st.leerie_root, st.run_id, repo_root=repo)
    st.load()
    return st


def test_the_gate_repairs_no_case_the_operator_accepted(
        leerie, tmp_path, monkeypatch):
    st = _gate_run(leerie, tmp_path, "1", "2")
    calls = _gate(leerie, monkeypatch, st)
    assert calls == []
    gate = st.data["acceptance"]["gate"]
    assert not any(r["passed"] for r in gate["final"])
    # Measured and kept as measured, but not work.
    assert not leerie._acceptance_work_fails(gate["final"],
                                             st.data["acceptance"]["sets"])


def test_the_gate_repairs_and_names_only_what_was_not_accepted(
        leerie, tmp_path, monkeypatch):
    """One case of a set accepted, its sibling not: the set is still
    repaired, and the repair is told only the sibling's name."""
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    sets = _make_sets(leerie, st, 1)
    d = Path(sets[0]["dir"])
    (d / "acc" / "test_defect_1.py").write_text(
        "from calc import add\n\n"
        "def test_add_sums():\n    assert add(2, 3) == 5\n\n"
        "def test_add_more_than_asked():\n    assert add(1, 1) == 2\n")
    sets[0]["cases"]["acc/test_defect_1.py"] = ["test_add_sums",
                                                "test_add_more_than_asked"]
    # What own-start validation records: the runner's own failing IDs, and
    # a scope judge that counted both.
    (r,) = asyncio.run(leerie._evaluate_acceptance_sets(
        st, _caps(leerie, 5), str(staging), sets, "learn-ids"))
    ids = r["failing_cases"]["acc/test_defect_1.py"]
    assert len(ids) == 2, r
    sets[0]["base_failing_cases"] = {"acc/test_defect_1.py": ids}
    sets[0]["scope"] = {"bounded": True, "judged": {},
                        "counted_cases": {"acc/test_defect_1.py": ids}}
    st.data["acceptance"] = {"sets": sets}
    st.save()
    st.release_lock()
    # By its title, as the PR's "Still failing" section shows it.
    title = next(c for c in ids if c.endswith("test_add_more_than_asked")
                 ).partition(" :: ")[2]
    r = _run_accept(st.path, f"1/{title}", verb="accept-heldout")
    assert r.returncode == 0, r.stderr
    st = leerie.State(st.leerie_root, st.run_id, repo_root=repo)
    st.load()
    calls = _gate(leerie, monkeypatch, st)
    assert calls, "the sibling was not accepted: the set is repaired"
    up = calls[0]["user_prompt"]
    assert "- test_add_sums" in up
    assert "test_add_more_than_asked" not in up


# --- the open PR follows the re-decided outcome (host side) -------------------

_GH = r"""#!/bin/bash
printf '%s\n' "$*" >> "$GH_LOG"
case "$1 $2" in
  "pr view") printf '%s' "$GH_VIEW" ;;
  "pr edit")
    body=""
    while IFS= read -r line || [ -n "$line" ]; do body+="$line"$'\n'; done
    printf '%s' "$body" > "$GH_BODY" ;;
  "pr ready") [ -z "${GH_READY_FAILS:-}" ] || exit 1 ;;
esac
"""


@pytest.mark.skipif(not HAS_JQ, reason="host-only script: needs real `jq`")
@pytest.mark.parametrize("ready_fails", [False, True])
def test_host_finalize_updates_the_open_pr(tmp_path, ready_fails):
    user = init_git_repo(tmp_path / "user")
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    run_git_repo_first(user, "remote", "add", "origin", str(origin))
    run_git_repo_first(user, "branch", f"leerie/runs/{RUN}")
    run_git_repo_first(user, "push", "-q", "origin", f"leerie/runs/{RUN}")
    run_dir = tmp_path / "state" / "runs" / RUN
    run_dir.mkdir(parents=True)
    old, new = "## Unconverged\n\n- 1 held-out set fails\n", (
        "## Held-out cases accepted by the operator\n\n- set 1\n")
    (run_dir / "run.json").write_text(json.dumps({
        "branch": f"leerie/runs/{RUN}", "working_branch": "main",
        "finished_at": "2026-11-12T10:00:00Z",
        "pushed_at": "2026-11-12T10:01:00Z",
        "pr_url": "https://github.com/o/r/pull/7", "outcome": "done",
        "pr_end_state": new,
        "pr_refresh": {"end_state_was": old, "outcome": "done"}}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "gh").write_text(_GH)
    (bindir / "gh").chmod(0o755)
    env = {"PATH": f"{bindir}:/usr/bin:/bin", "USER_REPO": str(user),
           "HOME": str(tmp_path), "GH_LOG": str(tmp_path / "gh.log"),
           "GH_BODY": str(tmp_path / "body.md"),
           "GH_VIEW": json.dumps({"body": "## Summary\nok\n\n" + old,
                                  "isDraft": True})}
    if ready_fails:
        env["GH_READY_FAILS"] = "1"
    r = subprocess.run(
        ["bash", "-c", f"set -euo pipefail; . {HOST_FINALIZE_SH}; "
                       f"host_finalize {run_dir}"],
        env=env, capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    assert "origin up to date" in r.stderr
    body = (tmp_path / "body.md").read_text()
    assert old not in body and new in body and body.startswith("## Summary")
    log = (tmp_path / "gh.log").read_text().splitlines()
    assert [ln.split()[:2] for ln in log] == [
        ["pr", "view"], ["pr", "edit"], ["pr", "ready"]]
    after = json.loads((run_dir / "run.json").read_text())
    if ready_fails:
        assert "stays a draft" in r.stderr
        assert after["pr_refresh"] == {"end_state_was": old,
                                       "outcome": "done"}
    else:
        assert after["pr_refresh"] is None
    # Nothing was pushed.
    assert "push" not in " ".join(log)
