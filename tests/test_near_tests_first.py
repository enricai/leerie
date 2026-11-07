"""A round's near tests are measured first (DESIGN §8), and discovery spends its
budget on the tree that ships (DESIGN §9).

Real git repositories and a real confined pytest, as in
`test_one_measurement_per_change.py`, whose `Run` this extends. Only the worker
(`claude_p`) is ever stubbed. A broken test here is really broken on disk, so
B*'s base and final reruns give their verdict from real runs.
"""
from __future__ import annotations

import asyncio
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from tests.conftest import run_git_cwd_first_stdout  # noqa: E402
from tests import test_delivery_ratchet as T  # noqa: E402
from tests import test_delivery_redrive as RD  # noqa: E402
from tests.test_one_measurement_per_change import (  # noqa: E402
    CMD, Run, _test_file)

HUB_A, HUB_B = "tests/test_hub_a.py", "tests/test_hub_b.py"
FAR, LEAF, OTHER = "tests/test_far.py", "tests/test_leaf.py", "tests/test_other.py"
# `test_hub_*` import hub.py, `test_far` only through mid.py: a distance of 2.
OK_HUB = "H = 1\n"


@pytest.fixture
def run(leerie, tmp_path, monkeypatch) -> Run:
    r = Run(leerie, tmp_path, monkeypatch)
    r.commit(mid_DOT_py="import hub\nM = hub.H\n",
             tests__test_far_DOT_py=_test_file("mid", "far", "mid.M < 3"),
             tests__test_hub_a_DOT_py=_test_file("hub", "hub_a", "hub.H < 5"))
    r.base = run_git_cwd_first_stdout(r.staging, "rev-parse", "HEAD")
    r.st.data["conformance"]["_baseline"]["sha"] = r.base
    r.st.save()
    r.pre_ids = None
    return r


def _head(run: Run) -> str:
    return run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD")


def _named(cmd: str) -> set[str]:
    return {w for w in cmd.replace("'", " ").split()
            if w.startswith("tests/test_")}


def _measured(run: Run) -> list[set[str] | str]:
    """Each command that ran: the files it names, or `whole`."""
    return ["whole" if run.ran_whole(c) else _named(c) for c in run.runs]


def _before(run: Run) -> dict:
    """The round's start: the whole tree, measured."""
    got = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="pre",
        log_path=run.run_dir / "logs" / "m.log"))
    assert got is not None
    run.runs.clear()
    return got


def _near(run: Run, pre: str, *, named=()) -> dict:
    return asyncio.run(run.leerie._measure_axes(
        str(run.staging), {"tests": CMD}, run.st, run.caps,
        log_path=run.run_dir / "logs" / "m.log", verbosity="quiet",
        label_prefix="near", log_prefix="near", failing_ids_axes={"tests"},
        carry=True, near_of=pre, near_named=named))["tests"]


def _rejection(run: Run, pre: str, before: dict, *, named=()) -> dict | None:
    return asyncio.run(run.leerie._near_rejection(
        run.st, run.caps, run.staging, pre, _head(run), before, label="round",
        log_path=run.run_dir / "logs" / "m.log", named=named))


def _spy_discovery(run: Run, monkeypatch) -> list[dict]:
    calls: list[dict] = []
    real = run.leerie._intermittent_discovery

    async def spy(*a, **k):
        calls.append(k)
        return await real(*a, **k)
    monkeypatch.setattr(run.leerie, "_intermittent_discovery", spy)
    return calls


# --- The stage itself --------------------------------------------------------

def test_near_runs_the_nearest_tests_and_answers_for_no_tree(run):
    _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    res = _near(run, pre)
    assert _measured(run) == [{HUB_A, HUB_B}]
    assert res["near_stage"] == {"files": 2, "of": 5}
    assert res["passed"] is True
    # In no memo: the same tree, asked for its result, is measured.
    run.runs.clear()
    whole = run.measure("closure")
    assert run.runs, "the near stage must not stand in for the tree's result"
    assert "near_stage" not in whole


def test_green_near_tests_are_not_run_twice_and_the_closure_runs_the_rest(run):
    """The stage must cost a keeper nothing: what it settled, the ledger holds."""
    _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    _near(run, pre)
    res = run.measure("closure")
    assert _measured(run) == [{HUB_A, HUB_B}, {FAR}]
    assert res["passed"] is True
    assert res["carried"]["files"] == 4
    assert res["whole_command"] == CMD
    # Now the tree is answered for: the same tree again runs nothing.
    n = len(run.runs)
    run.measure("again")
    assert len(run.runs) == n


def test_the_ledger_keeps_a_file_the_stage_did_not_run_on_its_own_tree(run):
    """Rests on content, not on a round: the file that stage skipped still
    answers to the diff from the tree it was measured on."""
    _before(run)
    pre_tree = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    _near(run, pre)
    led = run.ledger()
    assert led[FAR]["tree"] == pre_tree
    assert led[HUB_A]["tree"] != pre_tree


def test_a_regression_in_a_far_test_is_still_caught_on_a_keeper(run, monkeypatch):
    """barnacle 83674caf: an edit of a leaf-looking module broke 53 IDs, none
    within one import of it, and the near stage was green. The closure is what
    rejects that round, on the round's own tree."""
    before = _before(run)
    pre = _head(run)
    calls = _spy_discovery(run, monkeypatch)
    run.commit(hub_DOT_py="H = 4\n")      # far asserts M < 3; hub_a H < 5
    assert _rejection(run, pre, before) is None
    after = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="closure",
        log_path=run.run_dir / "logs" / "m.log"))
    assert [i.split(" :: ")[0] for i in after["ids"]] == [FAR]
    ex = asyncio.run(run.leerie._ratchet_exec(
        run.st, run.caps, run.staging, pre, before, after, label="r",
        log_path=run.run_dir / "logs" / "m.log"))
    assert [i.split(" :: ")[0] for i in ex["new"]] == [FAR]
    assert calls == [], "rejected by its closure: no discovery either"
    assert ex["discovery"]["skipped"].endswith("already rejected")


def test_a_broken_near_test_rejects_the_round_with_the_rest_unrun(run, monkeypatch):
    """barnacle a79384a4 round 1: two tests importing the edited file broke,
    and the round paid 25 minutes of closure and 25 of discovery first."""
    before = _before(run)
    pre = _head(run)
    calls = _spy_discovery(run, monkeypatch)
    run.commit(hub_DOT_py="H = 9\n")      # hub_a H < 5 fails; far M < 3 too
    ex = _rejection(run, pre, before)
    assert [i.split(" :: ")[0] for i in ex["new"]] == [HUB_A]
    assert ex["near_stage"]["files"] == 2
    assert calls == [], "a rejected round pays for no discovery"
    assert not any(FAR in c for c in run.runs), run.runs
    assert ex["discovery"]["skipped"] == "near stage"


def test_a_flaky_near_test_does_not_reject_and_the_closure_runs(run, monkeypatch):
    """B* decides, not one red run: a near failure its reruns do not reproduce
    goes on to the closure."""
    marker = run.staging.parent / "flake-once"
    run.commit(tests__test_hub_b_DOT_py=(
        "import os, sys\n"
        "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
        "import hub\n\n"
        "def test_hub_b():\n"
        f"    p = {str(marker)!r}\n"
        "    assert not os.path.exists(p)\n"))
    before = _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    # Made to fail once by a file the measurement can read, then removed by
    # the spy so every rerun passes.
    marker.write_text("x")
    real = run.leerie._capture_failing_test_ids

    async def cleared(*a, **k):
        marker.unlink(missing_ok=True)
        return await real(*a, **k)
    monkeypatch.setattr(run.leerie, "_capture_failing_test_ids", cleared)
    run.caps["confirm_intermittent_repeats"] = 2
    calls = _spy_discovery(run, monkeypatch)
    assert _rejection(run, pre, before) is None
    assert calls == [], "a near stage is no tree to repeat"
    # It failed in the stage, or this proved nothing.
    assert not marker.exists()
    assert not run.ledger()[HUB_B]["passed"]
    run.runs.clear()
    res = run.measure("closure")
    assert res["passed"] is True
    assert FAR in " ".join(run.runs)


@pytest.mark.parametrize("name,text", [
    ("pytest_DOT_ini", "[pytest]\n"),
    ("data_DOT_json", "{\"a\": 1}\n"),
])
def test_a_change_nobody_can_scope_has_no_near_tests(run, name, text):
    """Config, lockfile, fixture: reaches everything, so nothing is nearer."""
    _before(run)
    pre = _head(run)
    # With a hub edit beside it, which alone would take a stage.
    run.commit(hub_DOT_py="H = 2\n", **{name: text})
    res = _near(run, pre)
    assert "near_stage" not in res
    assert len(run.runs) == 1 and run.ran_whole()


def test_a_changed_test_helper_makes_its_importers_near_and_all_it_reaches_due(run):
    run.commit(tests__helper_DOT_py="K = 1\n",
               tests__test_far_DOT_py=(
                   "import os, sys\n"
                   "sys.path.insert(0, os.path.dirname(__file__))\n"
                   "sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))\n"
                   "import helper, mid\n\ndef test_far():\n    assert helper.K\n"))
    _before(run)
    pre = _head(run)
    run.commit(tests__helper_DOT_py="K = 2\n")
    res = run.measure("closure-only")
    run.runs.clear()
    assert res["passed"] is True
    near = run.leerie._near_test_files(
        run.staging, _head(run), ["tests/helper.py"], None)
    assert near == {FAR}


def test_a_failing_test_is_near_at_any_distance(run):
    """A carried failure is the first thing a round is asked about."""
    run.commit(hub_DOT_py="H = 4\n")      # far fails on the round's start
    _before(run)
    pre = _head(run)
    run.commit(leaf_DOT_py="L = 1\n# c\n")
    res = _near(run, pre)
    assert _named(run.runs[-1]) == {LEAF, FAR}
    assert res["failing_ids"] and FAR in res["failing_ids"][0]


def test_a_test_the_round_was_named_is_near_at_any_distance(run):
    """A control an earlier attempt broke."""
    _before(run)
    pre = _head(run)
    run.commit(leaf_DOT_py="L = 1\n# c\n")
    _near(run, pre, named=[FAR])
    assert _named(run.runs[-1]) == {LEAF, FAR}


def test_a_forbidden_scoped_command_measures_no_stage(run):
    _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    near_cmd = f"python3 -m pytest -q -p no:cacheprovider {HUB_A} {HUB_B}"
    run.st.data["process_constraints"] = {"forbidden_commands": [near_cmd]}
    res = _near(run, pre)
    assert "near_stage" not in res
    # The narrowed form, every file the change reaches at once.
    assert _named(run.runs[-1]) == {HUB_A, HUB_B, FAR}


def test_a_rollback_keeps_the_broken_test_near_for_the_next_attempt(run, monkeypatch):
    before = _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 9\n")
    assert _rejection(run, pre, before)["new"]
    run_git_cwd_first_stdout(run.staging, "reset", "-q", "--hard", pre)
    run.runs.clear()
    # The start is measured: reading it costs nothing.
    assert _before(run)["ids"] == []
    assert run.runs == []
    run.commit(hub_DOT_py="H = 1\n# attempt 2\n", leaf_DOT_py="L = 1\n# c\n")
    _near(run, pre)
    assert HUB_A in _named(run.runs[-1])


def test_a_tree_past_the_round_rejects_nothing(run):
    """Staging moved on (an interrupted attempt committed before a resume):
    what it holds is not the round's, so it decides nothing about the round."""
    before = _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    post = _head(run)
    run.commit(hub_DOT_py="H = 9\n")
    assert asyncio.run(run.leerie._near_rejection(
        run.st, run.caps, run.staging, pre, post, before, label="round",
        log_path=run.run_dir / "logs" / "m.log")) is None
    assert run.runs == []


# --- The two rounds that roll back, driven whole -----------------------------

def _gate_world(leerie, tmp_path):
    (repo, head) = T._repo(tmp_path)
    (repo / "calc.py").write_text(
        T.BUGGY + "\n\ndef mul(a, b):\n    return a * b\n")
    (repo / "wrap.py").write_text("import calc\nW = calc.mul\n")
    (repo / "test_mul.py").write_text(
        "from calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    (repo / "test_far.py").write_text(
        "from wrap import W\n\ndef test_far():\n    assert W(2, 3) == 6\n")
    (repo / "test_other.py").write_text("def test_other():\n    pass\n")
    T._git(repo, "add", "-A")
    T._git(repo, "commit", "-qm", "far")
    head = T._git(repo, "rev-parse", "HEAD")
    st = T._st(leerie, tmp_path, repo, head, working_branch="main",
               required_items=T.ITEMS)
    staging = T._staging(st, repo)
    start = T._git(staging, "rev-parse", "HEAD")
    st.data["delivery_gate"] = {"judged_before": T._judged_before(start, [])}
    st.data["acceptance"] = {"sets": T._make_sets(leerie, st, 5)}
    return st, staging, start


def test_the_gate_rolls_back_a_round_on_its_near_tests_alone(
        leerie, tmp_path, monkeypatch):
    """a79384a4 round 1 through the real gate: fixed its held-out cases, broke
    a test importing what it edited. Rolled back with that test named, no
    closure of its tree, no discovery, and the next round starts clean."""
    st, staging, start = _gate_world(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)

    def break_mul(p):
        (p / "calc.py").write_text(
            T.FIXED + "\n\ndef mul(a, b):\n    return a + b\n")
        T._git(p, "commit", "-qam", "conformer: fix add, break mul")

    def fix_both(p):
        (p / "calc.py").write_text(
            T.FIXED + "\n\ndef mul(a, b):\n    return a * b\n")
        T._git(p, "commit", "-qam", "conformer: fix add")
    w = T.Workers([break_mul, fix_both])
    caps = T._caps(leerie, 5)
    caps["confirm_intermittent_repeats"] = 2
    monkeypatch.setattr(leerie, "claude_p", w)
    labels: list[tuple[str, str]] = []
    real = leerie._run_streaming

    async def spy(cmd, **kw):
        labels.append((str(kw.get("label") or ""), " ".join(cmd)))
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie, "_run_streaming", spy)
    discovery = _spy_discovery(types.SimpleNamespace(leerie=leerie), monkeypatch)
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, T.MODELS,
                                            T.EFFORTS))
    gate = st.data["acceptance"]["gate"]
    r1, r2 = gate["rounds"]
    assert [i.split(" :: ")[0] for i in r1["ratchet"]["new_failures"]] == [
        "test_mul.py"]
    assert r1["rolled_back"] is True
    assert r1["sha"] not in gate["test_ids"]
    # Its tree: the stage and B* on the one failing file, and nothing else.
    r1_runs = [c for lab, c in labels if lab.startswith("acceptance-r1")
               and "install" not in lab]
    assert r1_runs and not any("test_far" in c for c in r1_runs), r1_runs
    # One round's worth of discovery: round 2's, which is a keeper.
    assert len(discovery) == 1
    # The keeper's closure ran what the stage left, once.
    r2_runs = [c for lab, c in labels if lab.startswith("acceptance-r2-")
               and "install" not in lab and "discovery" not in lab]
    assert sum("test_mul" in c for c in r2_runs) == 1, r2_runs
    assert sum("test_far" in c for c in r2_runs) == 1, r2_runs
    assert r2["ratchet"]["new_failures"] == []
    assert not r2.get("rollback_reason")


def test_a_redrive_group_is_rolled_back_on_its_near_tests_naming_its_controls(
        leerie, tmp_path, monkeypatch):
    """The re-drive's call: the group's near tests reject it, the rollback goes
    through the same path, the test goes to the controls for the next group,
    and a control from an earlier group is near at any distance."""
    w = RD.World(leerie, tmp_path)
    w.install(monkeypatch)
    bad = "src/a.test.ts :: broke"
    control = "src/far.test.ts"
    seen: list[dict] = []

    async def ids(st, caps, staging, label):
        return {"ids": [], "files": {}, "command": "t"}

    async def ratchet_ids(st, caps, tree, *, label, log_path, near_of=None,
                          near_named=()):
        seen.append({"near_of": near_of, "named": list(near_named)})
        return {"ids": [bad], "files": {bad: "src/a.test.ts"}, "command": "t",
                "near_stage": {"files": 1, "of": 9}}

    async def new(st, caps, staging, pre, before, after, label, **k):
        return {"new": [bad], "pre_existing": [], "no_verdict": [],
                "resource_affected": [], "unmeasured": False}
    monkeypatch.setattr(leerie, "_redrive_test_ids", ids)
    monkeypatch.setattr(leerie, "_ratchet_test_ids", ratchet_ids)
    monkeypatch.setattr(leerie, "_redrive_new_failures", new)
    closure = _spy_discovery(types.SimpleNamespace(leerie=leerie), monkeypatch)
    w.st.data.setdefault("delivery_redrive", {})["pending_controls"] = [
        {"kind": "control_regression", "key": f"{control} :: x",
         "test": f"{control} :: x", "detail": "d"}]
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    g = w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]
    assert g["new_failures"] == [bad]
    assert g["rolled_back"] is True
    assert closure == []
    assert seen[0]["named"] == [control]
    assert seen[0]["near_of"] == g["pre_sha"]
    keys = {n["key"] for n in w.st.data["delivery_redrive"]["pending_controls"]}
    assert bad in keys


# --- The final conformer's real loop ----------------------------------------

def _drive_final(run: Run, leerie, monkeypatch, conformer) -> None:
    run.st.data["required_items"] = [{"item": "x"}]
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    monkeypatch.setattr(leerie, "_validate_conformance_result",
                        lambda r, wt: None)
    monkeypatch.setattr(leerie, "check_production_evidence", lambda r: [])
    monkeypatch.setattr(leerie, "claude_p", conformer)
    run.caps["conformance_rounds"] = 3
    asyncio.run(leerie._run_final_conformance(
        run.run_dir, run.st, run.caps, {"conformer": "sonnet"},
        {"conformer": "low"}))


def test_a_final_round_its_near_tests_reject_pays_no_closure(run, leerie, monkeypatch):
    rounds: list[int] = []
    seen: list[str] = []

    async def conformer(*a, **k):
        rounds.append(len(run.runs))
        seen.append(k.get("user_prompt") or "")
        if len(rounds) == 1:
            run.commit(hub_DOT_py="H = 9\n")     # breaks hub_a and far
        elif len(rounds) == 2:
            run.commit(hub_DOT_py="H = 1\n# r2\n")
        return {}
    run.commit(leaf_DOT_py="L = 1\n# integrated\n")
    _drive_final(run, leerie, monkeypatch, conformer)
    assert run.ran_whole(run.runs[0])
    assert len(rounds) >= 2
    # Between the two workers: round 0's near stage and its B* reruns, nothing
    # of far on that tree, and no closure for round 1 to start from.
    first_round = run.runs[rounds[0]:rounds[1]]
    assert first_round and any(HUB_A in c for c in first_round), run.runs
    assert not any(FAR in c for c in first_round), first_round
    # Far runs once round 1 has fixed the tree.
    assert any(FAR in c for c in run.runs[rounds[1]:]), run.runs
    # The worker was told what its near tests said.
    assert any(HUB_A in p for p in seen[1:])
    # It ships measured: the tree's closure ran.
    tid = run.st.data["test_id_diff"]
    assert not tid.get("near_stage")
    assert tid["new"] == []


def test_the_tree_a_near_stage_rejected_still_gets_its_closure_before_it_ships(
        run, leerie, monkeypatch):
    """All rounds rejected on near tests: the loop ends with no closure of the
    last tree, and phase 5 must run it rather than read the near verdict as the
    tree's."""
    async def conformer(*a, **k):
        run.commit(hub_DOT_py=f"H = 9\n# {id(a)} {len(run.runs)}\n")
        return {}
    run.commit(leaf_DOT_py="L = 1\n# integrated\n")
    _drive_final(run, leerie, monkeypatch, conformer)
    tid = run.st.data["test_id_diff"]
    assert tid["near_stage"] is True
    assert [i.split(" :: ")[0] for i in tid["new"]] == [HUB_A]
    tree = run_git_cwd_first_stdout(run.staging, "rev-parse", "HEAD^{tree}")
    assert run.ledger()[FAR]["tree"] != tree
    run.runs.clear()
    asyncio.run(leerie._refresh_shipping_test_id_diff(
        run.run_dir, run.st, run.caps, label="test-id-diff-acceptance"))
    assert any(FAR in c for c in run.runs), run.runs
    tid = run.st.data["test_id_diff"]
    assert not tid.get("near_stage")
    assert {i.split(" :: ")[0] for i in tid["new"]} == {HUB_A, FAR}


# --- Discovery ---------------------------------------------------------------

def _discovery(run: Run, *, partial_ok: bool) -> dict:
    return asyncio.run(run.leerie._intermittent_discovery(
        run.st, {**run.caps, "confirm_intermittent_repeats": 2,
                 "confirm_budget_sec": 60}, str(run.staging), CMD, run.base,
        log_path=run.run_dir / "logs" / "m.log", label="d",
        partial_ok=partial_ok))


def test_a_discovery_that_cannot_cover_every_file_waits_for_the_tree_that_ships(run):
    """95 of the 133 recorded discoveries covered part of what they coupled, off
    the tree that ships, at a median of 4% of the files, and decided nothing."""
    run.commit(hub_DOT_py="H = 2\n")
    run.st.data["test_file_durations"] = {HUB_A: 500.0, HUB_B: 1.0}
    got = _discovery(run, partial_ok=False)
    assert got["deferred"] is True
    assert got["repeats_run"] == 0 and got["files"] == []
    assert run.runs == []
    # The typing still needs what it would have repeated.
    assert set(got["coupled"]) == {HUB_A, HUB_B}
    # And the tree that ships takes what fits, as ever.
    got = _discovery(run, partial_ok=True)
    assert "deferred" not in got
    assert got["repeats_run"] == 2


def test_a_calibration_that_finds_part_fits_repeats_nothing_either(
        run, monkeypatch):
    """Only calibrating can say so when the durations on record do fit."""
    run.commit(hub_DOT_py="H = 2\n")
    run.st.data["test_file_durations"] = {HUB_A: 0.01, HUB_B: 0.01}

    def part(ranked, *a, **k):
        return {"chosen": ranked[:1], "planned_sec": 1.0,
                "skipped": [{"file": ranked[1], "reason": "over budget"}]}
    monkeypatch.setattr(run.leerie, "_discovery_select", part)
    got = _discovery(run, partial_ok=False)
    assert got["deferred"] is True
    assert got["repeats_run"] == 0 and got["files"] == []
    assert "1 of 2" in got["skipped"]


def test_a_discovery_that_covers_every_file_runs_on_any_tree(run):
    """web 6f147a0c: 7 of 7 files, the whole command green, 16 then 2 IDs red on
    every repeat and confirmed new. The one recorded case discovery decided."""
    run.commit(hub_DOT_py="H = 2\n")
    run.st.data["test_file_durations"] = {HUB_A: 0.01, HUB_B: 0.01}
    got = _discovery(run, partial_ok=False)
    assert "deferred" not in got
    assert got["repeats_run"] == 2
    assert set(got["files"]) == {HUB_A, HUB_B}


def _final_post(run: Run) -> dict:
    return asyncio.run(run.leerie._measure_axes(
        str(run.staging), {"tests": CMD}, run.st, run.caps,
        log_path=run.run_dir / "logs" / "m.log", verbosity="quiet",
        label_prefix="p", log_prefix="p", failing_ids_axes={"tests"},
        carry=True))


def _final_confirm(run: Run, post: dict, **k) -> dict:
    return asyncio.run(run.leerie._confirm_final_test_failures(
        run.st, run.caps, str(run.staging), post,
        log_path=run.run_dir / "logs" / "m.log", label="final", **k))


def test_a_near_stage_that_rejects_nothing_records_nothing(run):
    """It is about part of the tree, and the closure is next: neither the
    tree's verdict nor its failing IDs may come from it."""
    run.st.data["baseline_failing_ids"] = []
    _before(run)
    prior = {"tree_sha": "t", "new": ["x"], "candidates": ["x"]}
    run.st.data["test_id_diff"] = prior
    run.st.data["final_failing_ids"] = ["x"]
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    near = _near(run, pre)
    assert near["near_stage"]
    got = _final_confirm(run, {"tests": near})
    assert got == {"new": [], "near_stage": True}
    assert run.st.data["test_id_diff"] == prior
    assert run.st.data["final_failing_ids"] == ["x"]


def test_a_round_tree_that_holds_is_repeated_only_if_all_of_it_fits(
        run, monkeypatch):
    run.caps["confirm_intermittent_repeats"] = 2
    run.caps["confirm_budget_sec"] = 60
    run.st.data["baseline_failing_ids"] = []
    run.commit(hub_DOT_py="H = 2\n")
    post = _final_post(run)
    run.st.data["test_file_durations"] = {HUB_A: 500.0, HUB_B: 1.0}
    calls = _spy_discovery(run, monkeypatch)
    verdict = _final_confirm(run, post)
    assert [k["partial_ok"] for k in calls] == [False]
    assert verdict["discovery_deferred"] is True
    assert verdict["discovery"]["repeats_run"] == 0


def test_a_tree_its_own_failures_reject_is_not_repeated(run, monkeypatch):
    run.caps["confirm_intermittent_repeats"] = 2
    run.st.data["baseline_failing_ids"] = []
    run.commit(hub_DOT_py="H = 9\n")
    calls = _spy_discovery(run, monkeypatch)
    verdict = _final_confirm(run, _final_post(run))
    assert {i.split(" :: ")[0] for i in verdict["new"]} == {HUB_A, FAR}
    assert calls == []
    assert verdict["discovery_deferred"] is True


def test_the_shipping_tree_is_repeated_without_confirming_again(run, monkeypatch):
    """The verdict stands from its round; only the discovery it waited for runs."""
    run.caps["confirm_intermittent_repeats"] = 2
    run.st.data["baseline_failing_ids"] = []
    run.commit(hub_DOT_py="H = 9\n")
    first = _final_confirm(run, _final_post(run))
    assert first["discovery_deferred"] is True
    pairs = first["pairs_run"]
    calls = _spy_discovery(run, monkeypatch)
    real = run.leerie._confirm_new_failures
    confirms: list[list[str]] = []

    async def counted(cand, *a, **k):
        confirms.append(list(cand))
        return await real(cand, *a, **k)
    monkeypatch.setattr(run.leerie, "_confirm_new_failures", counted)
    verdict = _final_confirm(run, _final_post(run), shipping=True)
    assert [k["partial_ok"] for k in calls] == [True]
    assert confirms == []
    assert verdict["new"] == first["new"] and verdict["pairs_run"] == pairs
    assert "discovery_deferred" not in verdict
    assert verdict["discovery"]["repeats_run"] == 2


def test_an_id_only_discovery_found_is_confirmed_like_any_other(run, monkeypatch):
    run.caps["confirm_intermittent_repeats"] = 1
    run.st.data["baseline_failing_ids"] = []
    run.commit(hub_DOT_py="H = 2\n")
    post = _final_post(run)
    assert post["tests"]["failing_ids"] == []
    real = run.leerie._intermittent_discovery
    fake = "tests/test_hub_b.py :: test_hub_b"

    async def found(*a, **k):
        out = await real(*a, **k)
        out.update(failed_ids=[fake], id_files={fake: HUB_B},
                   fail_counts={fake: 1})
        return out
    monkeypatch.setattr(run.leerie, "_intermittent_discovery", found)
    verdict = _final_confirm(run, post, shipping=True)
    assert fake in verdict["candidates"]
    # It passes every real rerun: found, confirmed, not new.
    assert verdict["new"] == []


def test_the_ratchet_repeats_nothing_it_cannot_cover(run, monkeypatch):
    before = _before(run)
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    after = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="c",
        log_path=run.run_dir / "logs" / "m.log"))
    run.st.data["test_file_durations"] = {HUB_A: 500.0, HUB_B: 1.0}
    run.caps["confirm_intermittent_repeats"] = 2
    run.caps["confirm_budget_sec"] = 60
    run.runs.clear()
    ex = asyncio.run(run.leerie._ratchet_exec(
        run.st, run.caps, run.staging, pre, before, after, label="r",
        log_path=run.run_dir / "logs" / "m.log"))
    assert run.runs == []
    assert ex["discovery"]["deferred"] is True
    assert ex["new"] == []


def test_phase_five_runs_the_discovery_a_round_deferred(run, leerie, monkeypatch):
    """Through the real refresh: a verdict on the shipping tree with its
    discovery waiting is no reason to skip the tree, and asking again changes
    nothing once it has run."""
    run.caps["confirm_intermittent_repeats"] = 2
    run.st.data["baseline_failing_ids"] = []
    run.commit(hub_DOT_py="H = 9\n")
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: {"test": CMD})
    first = _final_confirm(run, _final_post(run))
    assert first["discovery_deferred"] is True
    calls = _spy_discovery(run, monkeypatch)
    for _ in range(2):
        asyncio.run(leerie._refresh_shipping_test_id_diff(
            run.run_dir, run.st, run.caps, label="test-id-diff-acceptance"))
    assert [k["partial_ok"] for k in calls] == [True]
    assert not run.st.data["test_id_diff"].get("discovery_deferred")


# --- The log says what ran ---------------------------------------------------

def _lines(capsys) -> list[str]:
    return [ln for ln in capsys.readouterr().out.splitlines() if "tests" in ln]


def test_the_replaced_line_never_claims_a_run_the_memo_then_serves(
        leerie, tmp_path, monkeypatch, capsys):
    """`tests replaced — … running the 606 test file(s)` printed right above
    `reusing recorded result`, and gave the coupled count, not what ran."""
    run = Run(leerie, tmp_path, monkeypatch, forbidden=[CMD])
    run.commit(leaf_DOT_py="L = 1\n# a\n", hub_DOT_py="H = 2\n")
    run.measure()
    out = _lines(capsys)
    replaced = [ln for ln in out if "tests replaced" in ln]
    assert replaced and "running" not in replaced[0]
    assert "3 test file(s)" in replaced[0]
    assert any("running all 3 test file(s)" in ln for ln in out)
    run.measure()
    out = _lines(capsys)
    assert len(run.runs) == 1
    assert any("reusing recorded result" in ln for ln in out)
    assert not any("running" in ln for ln in out), out
    run.commit(leaf_DOT_py="L = 1\n# b\n")
    run.measure()
    out = _lines(capsys)
    assert any("running the 1 test file(s)" in ln for ln in out), out
    assert not any("running all" in ln for ln in out)


def test_the_stage_says_how_many_of_how_many_it_ran(run, capsys):
    _before(run)
    capsys.readouterr()
    pre = _head(run)
    run.commit(hub_DOT_py="H = 2\n")
    _near(run, pre)
    out = _lines(capsys)
    assert any("running 2 of 5 test file(s)" in ln for ln in out), out
