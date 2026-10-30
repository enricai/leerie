"""Review round 7 M2 + L1 (+ the claims review's cut-no-pair defect): B*
sizing admits a rerun unit while one base/final pair of it fits the budget,
and spends what a pass leaves on the units it left out, so a real
regression in a longer file is no longer sized out on every measurement
(DESIGN §9 *Which failures are new*, step 2)."""
from __future__ import annotations

import asyncio
import inspect
import subprocess

import pytest

CMD = "python3 -m pytest -q -p no:cacheprovider tests"


def _git(cwd, *a):
    r = subprocess.run(["git", *a], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r.stdout.strip()


def _repo(leerie, tmp_path, files_base: dict, files_run: dict):
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True)
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / ".gitignore").write_text(".leerie/\n__pycache__/\n")
    for rel, body in files_base.items():
        (repo / rel).write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _git(repo, "rev-parse", "HEAD")
    run_id = "fix-001-abcdef"
    _git(repo, "checkout", "-qb", f"leerie/runs/{run_id}")
    for rel, body in files_run.items():
        (repo / rel).write_text(body)
    _git(repo, "commit", "-qam", "break")
    _git(repo, "checkout", "-q", "main")
    root = repo / ".leerie"
    run_dir = root / "runs" / run_id
    (run_dir / "logs").mkdir(parents=True)
    staging = run_dir / "worktrees" / "staging"
    _git(repo, "worktree", "add", "-q", str(staging), f"leerie/runs/{run_id}")
    st = leerie.State(root, run_id)
    st.data = {"task": "x", "working_branch": "main",
               "conformance": {"_baseline": {"sha": base}},
               "baseline_failing_ids": [], "subtask_status": {}}
    st.repo_root = repo
    st.save()
    return st, staging


def _final(leerie, st, staging, caps, durations, *, env=None,
           monkeypatch=None):
    post = {"tests": {"ran": True, "measured": True, "passed": False,
                      "command": CMD, "summary": ""}}
    log = st.run_dir / "logs" / "t.log"
    if env:
        for k, v in env.items():
            monkeypatch.setenv(k, v)
    assert asyncio.run(leerie._attach_failing_ids(
        post["tests"], str(staging), CMD, st, caps, log_path=log, label="m"))
    for k in env or {}:
        monkeypatch.delenv(k)
    st.data["test_file_durations"] = dict(durations)
    return asyncio.run(leerie._confirm_final_test_failures(
        st, caps, str(staging), post, log_path=log, label="final-r0"))


def test_a_regression_in_a_longer_file_is_no_longer_sized_out(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver: the final run failed a 30 s file (a one-off
    load flake) and a 290 s file (a real regression). Sized for all k
    pairs, (30 + 290 + 10) x 6 = 1980 > 1800 left the regression out on
    every measurement and the run `done`; one pair, 660 s, fits."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = _repo(
        leerie, tmp_path,
        {"tests/test_short.py": "import os\ndef test_flake():\n"
                                "    assert os.environ.get('FLAKE_ON') != '1'\n",
         "tests/test_long.py": "def test_reg():\n    assert True\n"},
        {"tests/test_long.py": "def test_reg():\n    assert False\n"})
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
    v = _final(leerie, st, staging, caps,
               {"tests/test_short.py": 30.0, "tests/test_long.py": 290.0},
               env={"FLAKE_ON": "1"}, monkeypatch=monkeypatch)
    assert [x.rsplit(".", 1)[-1] for x in v["new"]] == ["test_reg"]
    assert not v.get("sized_out")
    assert any("test_flake" in x for x in v["resource_affected"]
               + v["candidates"])
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def test_the_budget_a_pass_leaves_goes_to_the_files_it_left_out(
        leerie, tmp_path, monkeypatch):
    """Recorded 400 s and 500 s: one pair of both (1820 s) does not fit,
    so the first pass reruns the 400 s file alone. It finishes in about a
    second, and the second pass reruns the 500 s file with what is left —
    where the regression is. Before, it was `budget_short_unsettled`."""
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda path=None: 0.0)
    st, staging = _repo(
        leerie, tmp_path,
        {"tests/test_a.py": "import os\ndef test_flake():\n"
                            "    assert os.environ.get('FLAKE_ON') != '1'\n",
         "tests/test_b.py": "def test_reg():\n    assert True\n"},
        {"tests/test_b.py": "def test_reg():\n    assert False\n"})
    caps = {**leerie.DEFAULT_CAPS, "confirm_intermittent_repeats": 0}
    files_run: list = []
    gates: list = []
    real = leerie._capture_failing_test_ids
    real_gate = leerie._confirm_gate

    async def capture(tree, cmd, st_, caps_, *, label, timeout, files=None,
                      **kw):
        if label.startswith("final-r0"):
            files_run.append(tuple(files or ()))
        return await real(tree, cmd, st_, caps_, label=label,
                          timeout=timeout, files=files, **kw)

    async def gate(*a, **k):
        gates.append(1)
        return await real_gate(*a, **k)
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    monkeypatch.setattr(leerie, "_confirm_gate", gate)
    v = _final(leerie, st, staging, caps,
               {"tests/test_a.py": 400.0, "tests/test_b.py": 500.0},
               env={"FLAKE_ON": "1"}, monkeypatch=monkeypatch)
    assert files_run[0] == ("tests/test_a.py",)
    assert ("tests/test_b.py",) in files_run
    assert files_run.index(("tests/test_b.py",)) > files_run.index(
        ("tests/test_a.py",))
    assert [x.rsplit(".", 1)[-1] for x in v["new"]] == ["test_reg"]
    assert len(v["passes"]) == 2 and not v.get("sized_out")
    assert len(gates) == 1          # a later pass waits on no second gate
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"


def _fake_confirm(used_per_pass: list[float]):
    calls: list = []

    async def confirm(units, cands, pcaps, gate, **_pairs):
        i = len(calls)
        calls.append({"units": list(units), "cands": list(cands),
                      "budget": pcaps["confirm_budget_sec"], "gate": gate})
        return {"candidates": list(cands), "new": list(cands),
                "pre_existing": [], "no_verdict": [],
                "resource_affected": [], "budget_short": [],
                "budget_short_unsettled": [], "pairs_required": 3,
                "pairs_run": 3, "budget_exhausted": False,
                "gate": {"timed_out": False, "pass": i},
                "per_id": {c: {"final_failed": 3, "base_failed": 0,
                               "final_runs": 3, "base_runs": 3}
                           for c in cands},
                "budget_used_sec": used_per_pass[i]}
    return confirm, calls


@pytest.mark.parametrize("used, ran, out", [
    # 400 s and 500 s, budget 1800, overhead 10: one pair of both is 1820.
    ([100.0, 50.0], [["a"], ["b"]], []),
    # The first pass used 900 s: 900 left, one pair of b is 1020 — out.
    ([900.0], [["a"]], ["b"]),
])
def test_no_unit_is_skipped_while_one_pair_fits_what_is_left(
        leerie, used, ran, out):
    confirm, calls = _fake_confirm(used)
    v = asyncio.run(leerie._confirm_sized(
        {"x_a": "a", "x_b": "b"}, {"a": 400.0, "b": 500.0}, rank={},
        caps={**leerie.DEFAULT_CAPS, "confirm_budget_sec": 1800.0},
        overhead=10.0, label="t", confirm=confirm))
    assert [c["units"] for c in calls] == ran
    assert calls[0]["gate"] is None and calls[0]["budget"] == 1800.0
    if len(calls) > 1:
        assert calls[1]["gate"] == {"timed_out": False, "pass": 0}
        assert calls[1]["budget"] == pytest.approx(1700.0)
    assert v.get("sized_out_units", []) == out
    assert v["budget_short_unsettled"] == [f"x_{u}" for u in out]
    assert set(v["candidates"]) == {"x_a", "x_b"}


def test_the_final_runs_failures_rank_before_the_shortest(leerie):
    """Leftover passes take the units in the same order: a unit holding a
    final-run candidate before a discovery-only one, then the shortest."""
    confirm, calls = _fake_confirm([0.0, 0.0, 0.0])
    asyncio.run(leerie._confirm_sized(
        {"x_a": "a", "x_b": "b", "x_c": "c"},
        {"a": 880.0, "b": 100.0, "c": 50.0}, rank={"a": 0, "b": 0, "c": 1},
        caps={**leerie.DEFAULT_CAPS, "confirm_budget_sec": 1800.0},
        overhead=10.0, label="t", confirm=confirm))
    # First pass: b (220), then a (one pair of a+b 1980 > 1800, out), then
    # c (rank 1: 320). Second pass: a alone fits (1780).
    assert [c["units"] for c in calls] == [["b", "c"], ["a"]]


def test_a_cut_only_candidate_with_no_pair_is_unsettled_not_no_verdict(
        leerie):
    """Claims review R7-D5 (`cut_nv.py`): a base rerun the budget cut
    measured the candidate and no pair finished. It read `no_verdict`
    ("nothing was measured"); something ran, and the budget left it
    unsettled."""
    class Clock:
        t = 0.0

        def __call__(self):
            return self.t

        async def sleep(self, s):
            self.t += s
    clk = Clock()

    async def base(left):
        clk.t += left
        return leerie._RerunCut(frozenset({"x"}), frozenset({"x"}))

    async def final(left):
        raise AssertionError("the final rerun never starts")
    caps = {**leerie.DEFAULT_CAPS, "confirm_budget_sec": 10}
    v = asyncio.run(leerie._confirm_new_failures(
        ["x"], base, final, caps, label="q", seen_failing=True,
        read_psi=lambda: 0.0, sleep=clk.sleep, clock=clk))
    assert v["no_verdict"] == [] and v["new"] == []
    assert v["budget_short_unsettled"] == ["x"] and v["pairs_run"] == 0


def test_cut_and_sized_out_are_worded_apart(leerie):
    """L1: "no base/final rerun pair finished" was the text for a file
    never rerun at all."""
    class S:
        data: dict = {}

        def save(self):
            pass
    st = S()
    st.data = {}
    leerie._set_confirm_budget_gap(st, "failing-test-ID diff",
                                   ["tests/a.py"], sized_out=["tests/b.py"])
    (e,) = st.data["needs_human_verification"]
    cut, sized = e["detail"].split("; ")
    assert "could not confirm within the budget: tests/a.py" in cut
    assert "tests/b.py" not in cut
    assert "not rerun (sized out of the budget): tests/b.py" in sized
    assert "pair finished" not in e["detail"]
    reason = leerie._test_outcome_unknown_reason(
        {"tree_sha": "t", "candidates": ["A", "B"],
         "budget_short_unsettled": ["A", "B"],
         "budget_short_unsettled_files": ["tests/a.py", "tests/b.py"],
         "sized_out": ["B"], "sized_out_files": ["tests/b.py"]}, "t")
    assert "could not confirm within the budget: tests/a.py;" in reason
    assert "not rerun (sized out of the budget): tests/b.py" in reason


def test_a_kept_groups_unsettled_candidates_are_listed(leerie):
    """M2, re-drive: the group is kept (an unsettled candidate is not a
    confirmed regression) but never silently — its files and the items its
    ratchet could not check are listed."""
    class S:
        data: dict = {}

        def save(self):
            pass
    st = S()
    st.data = {}
    kept = [{"group": "r0g0", "ids": ["A", "B"], "sized_out": ["B"],
             "files": {"A": "tests/a.py", "B": "tests/b.py"},
             "unchecked_items": [1]}]
    leerie._set_redrive_unsettled_gap(
        st, kept, [{"item": "fix y"}, {"item": "no existing test breaks"}])
    (e,) = st.data["needs_human_verification"]
    assert e["site"] == "delivery re-drive"
    assert "could not confirm within the budget: tests/a.py" in e["detail"]
    assert "not rerun (sized out of the budget): tests/b.py" in e["detail"]
    assert "a re-drive group that was kept" in e["detail"]
    assert "no existing test breaks" in e["detail"]
    groups = leerie._pr_needs_human(st.data, lambda _s: True)
    assert groups["Regression checks the run could not settle"]


def test_the_redrive_records_a_kept_groups_unsettled_candidates(leerie):
    """Structure, paired with the substance test above: the kept branch
    records and lists them; the rollback branch does not."""
    src = inspect.getsource(leerie._run_delivery_redrive)
    i_keep = src.index("_ratchet_kept(st, rat)")
    i_rec = src.index('rec.setdefault("kept_unsettled", [])')
    i_gap = src.index("_set_redrive_unsettled_gap(")
    i_roll = src.index("ok = await _restore_tree(staging, pre_sha,")
    assert i_roll < i_keep < i_rec < i_gap


@pytest.mark.parametrize("fn", ["_intermittent_discovery_gate",
                                "_acceptance_discover", "_settle"])
def test_budget_zero_waits_for_no_gate_anywhere(leerie, fn):
    """Claims review R7-C4: with `confirm_budget_sec` <= 0 the confirms
    skipped the gate, but discovery, the held-out discovery and the
    execution settle still waited up to `confirm_gate_wait_sec`."""
    names = {"_intermittent_discovery_gate": "_intermittent_discovery",
             "_acceptance_discover": "_acceptance_discover",
             "_settle": "_settle_by_execution"}
    src = inspect.getsource(getattr(leerie, names[fn]))
    i = src.index("_confirm_gate(caps)")
    window = src[max(0, i - 200):i]
    assert "budget <= 0" in window or "budget > 0" in window


def test_budget_zero_held_out_discovery_runs_and_waits_for_nothing(
        leerie, monkeypatch, tmp_path):
    """Substance for the structural test above: with no budget, held-out
    discovery returns at once — the gate is never consulted."""
    async def gate(*a, **k):
        raise AssertionError("waited on the host-pressure gate")
    monkeypatch.setattr(leerie, "_confirm_gate", gate)

    class S:
        data: dict = {}
        run_dir = tmp_path
    out = asyncio.run(leerie._acceptance_discover(
        S(), {**leerie.DEFAULT_CAPS, "confirm_budget_sec": 0,
              "acceptance_intermittent_repeats": 3},
        tmp_path, ["tests/a.py"], tmp_path / "l.log", "t", None,
        lambda rel, obs: False))
    assert out["repeats_run"] == 0 and out["budget_exhausted"] is True


def test_the_first_pass_admits_a_unit_while_one_pair_fits(leerie):
    """The reviewer's durations: 30 s and 290 s, overhead 10, budget 1800.
    One pair of both (660 s) fits, so both go in the first pass; sized for
    all three pairs (1980 s) the 290 s file was left out — and, when the
    first pass used its whole estimate, never rerun."""
    confirm, calls = _fake_confirm([1800.0])
    v = asyncio.run(leerie._confirm_sized(
        {"x_s": "s", "x_l": "l"}, {"s": 30.0, "l": 290.0}, rank={},
        caps={**leerie.DEFAULT_CAPS, "confirm_budget_sec": 1800.0},
        overhead=10.0, label="t", confirm=confirm))
    assert [c["units"] for c in calls] == [["s", "l"]]
    assert v["new"] == ["x_l", "x_s"] and not v.get("sized_out")
