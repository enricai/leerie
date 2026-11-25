"""A start with no report is an empty start (DESIGN §8 *A repair that
regresses met work is rolled back*).

Barnacle 788868f5 (leerie 0.100.0, issue #748): the task forbade the test
command, and the run's one subtask changed nothing, so acceptance repair round
1 started at the base, where no test is coupled to the run's changes. Its
start ran nothing and was recorded unmeasured; its commit failed 5 tests that
pass on the base; the round was kept, and the final ID diff confirmed all 5
new against the base.

The gate test runs the REAL acceptance gate against a real git repo and a
real pytest command, the task forbidding that command; only `claude_p` is
stubbed (and the host's PSI file). The ratchet tests use the real confined
pytest of `test_near_tests_first.py`.
"""
from __future__ import annotations

import asyncio

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import FIXED, _caps, _make_sets, _repo, _st, \
    _staging
from tests.test_delivery_ratchet import (EFFORTS, ITEMS, MODELS,  # noqa: F401
                                         Workers, _judged_before, _quiet)
from tests.test_near_tests_first import (FAR, HUB_A, _head,  # noqa: F401
                                         _rejection, run)

# A whole-suite command of its own, as barnacle's `npm run test` is beside its
# scoped `npx vitest run <files>`: forbidding it leaves the scoped runner.
FORBIDDEN = "python3 run_all.py"


def test_the_empty_start_is_the_start_only_when_it_gave_no_report(leerie):
    after = {"ids": ["t::a"], "files": {}, "command": "c"}
    assert leerie._ratchet_start({"ids": ["t::b"]}, after) == {"ids": ["t::b"]}
    assert leerie._ratchet_start(None, after) == {
        "ids": [], "files": {}, "command": "c", "start_unreported": True}
    assert leerie._ratchet_start(None, None) is None


def test_a_round_from_an_unmeasured_start_that_breaks_a_test_is_reset(
        leerie, tmp_path, monkeypatch):
    """The #748 shape, end to end: the start is the baseline, so the scoped
    replacement of the forbidden command has nothing to run there."""
    repo, head = _repo(tmp_path)
    (repo / "calc.py").write_text((repo / "calc.py").read_text()
                                  + "\n\ndef mul(a, b):\n    return a * b\n")
    (repo / "test_mul.py").write_text(
        "from calc import mul\n\ndef test_mul():\n    assert mul(2, 3) == 6\n")
    (repo / "run_all.py").write_text(
        "import subprocess, sys\nsys.exit(subprocess.call([sys.executable, "
        "'-m', 'pytest', '-q', '-p', 'no:cacheprovider']))\n")
    (repo / ".leerie" / "config.toml").write_text(
        'test_scoped = "python3 -m pytest -q -p no:cacheprovider {test_files}"\n'
        f'test = "{FORBIDDEN}"\n')
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "mul")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head, working_branch="main",
             required_items=ITEMS,
             process_constraints={"forbidden_commands": [FORBIDDEN]})
    staging = _staging(st, repo)
    start = _git(staging, "rev-parse", "HEAD")
    st.data["conformance"] = {"_baseline": {"sha": start}}
    st.data["delivery_gate"] = {"judged_before": _judged_before(start, [])}
    st.data["acceptance"] = {"sets": _make_sets(leerie, st, 5)}

    def break_mul(p):
        (p / "calc.py").write_text(FIXED + "\n\ndef mul(a, b):\n"
                                   "    return a + b\n")
        _git(p, "commit", "-qam", "conformer: fix add, break mul")

    def fix_add(p):
        (p / "calc.py").write_text(FIXED + "\n\ndef mul(a, b):\n"
                                   "    return a * b\n")
        _git(p, "commit", "-qam", "conformer: fix add")
    w = Workers([break_mul, fix_add])
    caps = _caps(leerie, 5)
    caps["confirm_intermittent_repeats"] = 2
    monkeypatch.setattr(leerie, "claude_p", w)
    asyncio.run(leerie._run_acceptance_gate(st.run_dir, st, caps, MODELS,
                                            EFFORTS))
    gate = st.data["acceptance"]["gate"]
    # The start really gave no report: nothing is coupled at the baseline.
    assert gate["test_ids"][start] == {"unmeasured": True}
    r1, r2 = gate["rounds"]
    rat = r1["ratchet"]
    assert [i.split(" :: ")[0] for i in rat["new_failures"]] == ["test_mul.py"]
    assert rat["exec_unchecked"] == []
    assert r1["rolled_back"] is True
    assert r2["ratchet"]["new_failures"] == [] and \
        not r2.get("rollback_reason")
    assert "return a * b" in (staging / "calc.py").read_text()


def test_a_failure_the_unmeasured_start_has_too_is_pre_existing(run):
    """The empty start makes every failure a candidate; B* on the start is
    what keeps one that fails there too out of `new`."""
    run.commit(hub_DOT_py="H = 9\n")          # hub_a (H < 5) fails here
    pre = _head(run)
    run.commit(mid_DOT_py="import hub\nM = 1\n")
    after = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="closure",
        log_path=run.run_dir / "logs" / "m.log"))
    assert [i.split(" :: ")[0] for i in after["ids"]] == [HUB_A]
    ex = asyncio.run(run.leerie._ratchet_exec(
        run.st, run.caps, run.staging, pre, None, after, label="r",
        log_path=run.run_dir / "logs" / "m.log"))
    assert ex["unmeasured"] is False and ex["new"] == []
    assert [i.split(" :: ")[0] for i in ex["pre_existing"]] == [HUB_A]


def test_a_broken_far_test_is_new_from_an_unmeasured_start(run):
    pre = _head(run)
    run.commit(hub_DOT_py="H = 4\n")          # far asserts M < 3
    after = asyncio.run(run.leerie._ratchet_test_ids(
        run.st, run.caps, str(run.staging), label="closure",
        log_path=run.run_dir / "logs" / "m.log"))
    ex = asyncio.run(run.leerie._ratchet_exec(
        run.st, run.caps, run.staging, pre, None, after, label="r",
        log_path=run.run_dir / "logs" / "m.log"))
    assert [i.split(" :: ")[0] for i in ex["new"]] == [FAR]


def test_a_round_from_an_unmeasured_start_is_rejected_as_its_caller_reads_it(
        run):
    """What `_round_ratchet` and the re-drive do: the near stage first, then
    the closure and `_ratchet_exec`. With no start report and no tree measured
    before it there is no near stage, and the closure rejects the round."""
    pre = _head(run)
    run.commit(hub_DOT_py="H = 9\n")          # hub_a H < 5 and far M < 3 fail
    log = run.run_dir / "logs" / "m.log"
    ex = _rejection(run, pre, None)
    if ex is None:
        after = asyncio.run(run.leerie._ratchet_test_ids(
            run.st, run.caps, str(run.staging), label="closure",
            log_path=log))
        ex = asyncio.run(run.leerie._ratchet_exec(
            run.st, run.caps, run.staging, pre, None, after, label="r",
            log_path=log))
    assert sorted(i.split(" :: ")[0] for i in ex["new"]) == [FAR, HUB_A]


def test_no_report_on_either_side_is_still_unmeasured(run):
    pre = _head(run)
    ex = asyncio.run(run.leerie._ratchet_exec(
        run.st, run.caps, run.staging, pre, None, None, label="r",
        log_path=run.run_dir / "logs" / "m.log"))
    assert ex["unmeasured"] is True and ex["new"] == []
