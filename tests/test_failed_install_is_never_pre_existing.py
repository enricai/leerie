"""Review round 9 suspicion (a), driven: a dependency install that fails in
a fresh checkout was ignored on both sides of the failing-test-ID diff, so
every failure there read as the base's own and a real regression shipped
`done` (DESIGN §9 *Which failures are new*, *A failed install is no
measurement*).

The driver: staging's deps installed fine, then a commit makes `test_x`
really fail. The install fails from then on (a registry outage). Phase 5,
in a resumed process, installs into staging again (the tree that ships,
measured in place since I1 + I2 keep it at its commit) — the install fails,
so every test fails — and B* reruns the base in a fresh checkout whose
install fails too, so every candidate failed on the base as well:
`pre_existing`. Before the fix the run ended `done` with an empty end-state
section."""
from __future__ import annotations

import asyncio
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import CMD, SUITE, _git  # noqa: E402
from test_shipping_tree_is_committed import (  # noqa: E402
    _phase5, _quiet_conformer, _repo)

CONFTEST = ("import os, pytest\n\n@pytest.fixture(autouse=True)\n"
            "def _deps():\n    assert os.path.exists('deps/ok')\n")


@pytest.fixture(autouse=True)
def _clear_deps_memo(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_DEPS_INSTALLED", set())
    monkeypatch.setattr(leerie, "_DEPS_FAILED", {})
    monkeypatch.setattr(leerie, "_BASELINE_RETRIES", {})


def _setup(leerie, tmp_path, monkeypatch):
    run = _repo(leerie, tmp_path, monkeypatch, ignore="deps/\n")
    repo, staging = run["repo"], run["staging"]
    for wt in (repo, staging):
        (wt / "tests" / "conftest.py").write_text(CONFTEST)
        _git(wt, "add", "tests/conftest.py")
        _git(wt, "commit", "-qm", "deps-backed suite")
    base = _git(repo, "rev-parse", "HEAD")
    run["st"].data["conformance"]["_baseline"]["sha"] = base
    # Measured in the run's own repository (DESIGN §6 *The run's own
    # repository*): the base commit made in the user's repository above is
    # fetched there, as leerie fetches a commit it read in the checkout.
    assert leerie._run_repo_commit(run["st"], base, "refs/leerie/test-base")
    flag = tmp_path / "registry-down"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "fakeinst"
    tool.write_text("#!/bin/sh\n"
                    f"[ -e '{flag}' ] && exit 1\n"
                    "mkdir -p deps && touch deps/ok\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    run["st"].data["provision"] = {"recipe": [
        {"kind": "install", "command": ["fakeinst"]}]}
    run["st"].save()
    run["installs"] = _count_installs(leerie, monkeypatch)
    return run, flag


def _count_installs(leerie, monkeypatch, *, fail_first: bool = False
                    ) -> dict:
    """Count the recipe's install launches at leerie's own launch: the
    installs run in different trees, and a confined install keeps nothing
    across trees (DESIGN §6 *Leerie's runs of the repository's commands are
    confined too*). `fail_first`: the first one fails without running (a
    registry 503)."""
    real = leerie._run_streaming
    n = {"calls": 0}

    async def counted(argv, **kw):
        if "-install: " in (kw.get("label") or ""):
            n["calls"] += 1
            if fail_first and n["calls"] == 1:
                return 1, "503 from the registry"
        return await real(argv, **kw)
    monkeypatch.setattr(leerie, "_run_streaming", counted)
    return n


def test_a_regression_behind_a_failed_install_is_not_done(
        leerie, tmp_path, monkeypatch):
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    _quiet_conformer(leerie, monkeypatch)
    st, staging = run["st"], run["staging"]
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], st, run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    assert st.data["test_id_diff"]["new"] == []          # clean so far
    # A later commit (an acceptance repair, say) really breaks test_x.
    (staging / "tests" / "test_s.py").write_text(SUITE.format(ok="False"))
    _git(staging, "commit", "-qam", "repair")
    flag.write_text("")                                   # registry down
    # A resume: a fresh process installs into staging again.
    leerie._DEPS_INSTALLED.clear()
    _phase5(leerie, run)
    sec = leerie._format_pr_end_state_section(st.data)
    diff = st.data["test_id_diff"]
    # Before: test_x and test_m read `pre_existing`, outcome `done`, and
    # the section was empty.
    assert not diff.get("pre_existing"), diff.get("pre_existing")
    committed = asyncio.run(leerie._committed_tree_sha(str(staging)))
    assert diff["stale_for_tree"] == committed
    assert "Regression checks the run could not settle" in sec
    assert ("not measured on the tree that ships" in sec
            and "the dependency install failed in the tree measured" in sec)


def test_a_failed_base_install_gives_no_candidate_a_verdict(
        leerie, tmp_path, monkeypatch):
    """The base side alone: staging measures fine and finds `test_x`
    failing; only the base checkout's install fails. Before, the base
    reruns failed every test, so `test_x` read `pre_existing`."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st, staging = run["st"], run["staging"]
    (staging / "tests" / "test_s.py").write_text(SUITE.format(ok="False"))
    _git(staging, "commit", "-qam", "break")
    # Staging is measured in its committed view, which installs first;
    # then the registry goes down.
    asyncio.run(leerie._measured_tree(
        st, str(staging), run["caps"], log_path=tmp_path / "i.log",
        label="t"))
    flag.write_text("")
    post = asyncio.run(leerie._measure_axes(
        str(staging), {"tests": "python3 -m pytest -q -p no:cacheprovider "
                       "tests"}, st, run["caps"],
        log_path=tmp_path / "m.log", verbosity="quiet",
        failing_ids_axes={"tests"}))
    diff = asyncio.run(leerie._confirm_final_test_failures(
        st, run["caps"], str(staging), post, log_path=tmp_path / "c.log",
        label="final-r0"))
    assert [c.rsplit(".", 1)[-1] for c in diff["candidates"]] == ["test_x"]
    assert diff["pre_existing"] == []
    assert diff.get("base_install_failed") is True
    assert "test_x" in " ".join(diff["no_verdict"])
    # Listed for a human, instruction first, on the refresh's reuse path.
    asyncio.run(leerie._refresh_test_id_diff(
        st, run["caps"], staging, log_path=tmp_path / "r.log", label="r",
        log_prefix="phase 5"))
    nhv = [e["detail"] for e in st.data["needs_human_verification"]]
    assert len(nhv) == 1 and nhv[0].startswith(
        "failing-test-ID diff: rerun these against the base by hand")
    assert "its dependency install failed" in nhv[0] and "test_x" in nhv[0]


def test_a_baseline_measured_without_its_deps_has_no_failing_ids(
        leerie, tmp_path, monkeypatch):
    """The base side at the start of the run: a baseline whose install
    failed fails every test, and those IDs would make every final failure
    read as the base's own. They are unknown instead, and the gap says
    why."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    flag.write_text("")
    axes = asyncio.run(leerie._measure_axes(
        str(run["repo"]), {"tests": "python3 -m pytest -q -p "
                           "no:cacheprovider tests"}, st, run["caps"],
        log_path=tmp_path / "m.log", verbosity="quiet",
        failing_ids_axes={"tests"}))
    assert axes["tests"]["deps_failed"] is True
    # Unmeasured, so no report run and no IDs: every test failed, for want
    # of its dependencies. (Before every axis was unmeasured here, this
    # pinned the opposite: a measured red axis whose IDs were then dropped
    # by `_capture_conformance_baseline` alone.)
    assert axes["tests"]["measured"] is False
    assert axes["tests"]["passed"] is None
    assert "failing_ids" not in axes["tests"]
    st.data["conformance"]["_baseline"]["axes"] = axes
    assert leerie._final_diff_base_gap(st, {}).startswith(
        "failing-test-ID diff: run the tests on the PR branch and on the "
        "base")
    # Not memoised: the outage is not a fact about the tree.
    assert not st.data.get("blt_results")


def _installs(run) -> int:
    return run["installs"]["calls"]


def test_a_memo_hit_on_a_failed_install_installs_again_bounded(
        leerie, tmp_path, monkeypatch):
    """Review round 10 M2: a failed install was memoised for the whole
    process, so one registry blip unmeasured the tree for the rest of the
    run. A memo hit on a failed install now installs again, up to
    `deps_install_retries` times; a success clears the failure, and a
    success is never reinstalled."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    assert run["caps"].get("deps_install_retries",
                           leerie.DEFAULT_CAPS["deps_install_retries"]) == 2
    go = lambda: asyncio.run(leerie._ensure_worktree_deps(  # noqa: E731
        str(run["repo"]), run["st"], run["caps"],
        log_path=tmp_path / "i.log", verbosity="quiet"))
    flag.write_text("")
    assert go() is False and _installs(run) == 1
    flag.unlink()
    assert go() is True and _installs(run) == 2    # retried, cleared
    assert go() is True and _installs(run) == 2    # a memo hit
    # Down for good: the first install and two retries, then the memo.
    leerie._DEPS_INSTALLED.discard(os.path.realpath(run["repo"]))
    flag.write_text("")
    got = [go() for _ in range(5)]
    assert got == [False] * 5 and _installs(run) == 2 + 3


def test_a_flaky_first_install_does_not_unmeasure_a_clean_run(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver: staging's install fails the first time only
    (a registry 503). Before, it ran once, every later measurement carried
    `deps_failed`, and a clean run listed "not measured on the tree that
    ships". Now the next measurement installs again, and the run ends
    `done` with an empty section."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    st = run["st"]
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": ["true"]}]}
    st.save()
    installs = _count_installs(leerie, monkeypatch, fail_first=True)
    _quiet_conformer(leerie, monkeypatch)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], st, run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    _phase5(leerie, run)
    assert installs["calls"] == 2
    assert not st.data["test_id_diff"].get("ids_unknown")
    assert leerie._decide_outcome(st, any_commits=True) == "done"
    assert leerie._format_pr_end_state_section(st.data) == ""


def _failed_baseline(leerie, run, tmp_path):
    st = run["st"]
    axes = asyncio.run(leerie._measure_axes(
        str(run["repo"]), {"tests": "python3 -m pytest -q -p "
                           "no:cacheprovider tests"}, st, run["caps"],
        log_path=tmp_path / "m.log", verbosity="quiet",
        failing_ids_axes={"tests"}))
    assert axes["tests"]["deps_failed"] is True
    st.data["conformance"]["_baseline"]["axes"] = axes
    st.data["conformance"]["_baseline"]["red_axes"] = ["tests"]
    st.data["baseline_failing_ids"] = None
    st.save()


def test_a_baseline_whose_install_failed_is_measured_again(
        leerie, tmp_path, monkeypatch):
    """Saved state must not keep an outage for good: the baseline's
    `deps_failed` is run state, so before, every final diff — and every
    resume — listed "the base was measured without its dependencies". The
    base commit is measured again in a fresh checkout; once its install
    succeeds the baseline is replaced and the gap is gone."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    flag.write_text("")
    _failed_baseline(leerie, run, tmp_path)
    assert leerie._final_diff_base_gap(st, {})
    retry = lambda: asyncio.run(leerie._retry_failed_baseline(  # noqa: E731
        st, run["caps"], log_path=tmp_path / "b.log"))
    assert retry() is False                      # still down
    flag.unlink()
    assert retry() is True                       # back: replaced
    base = st.data["conformance"]["_baseline"]
    assert not base["axes"]["tests"].get("deps_failed")
    assert base["red_axes"] == [] and st.data["baseline_failing_ids"] == []
    assert leerie._final_diff_base_gap(st, {}) is None
    assert not leerie._helper_worktree_path(st, "baseline-retry").exists()
    assert retry() is False                      # nothing left to retry


def test_baseline_retries_are_bounded_per_process(leerie, tmp_path,
                                                  monkeypatch):
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    flag.write_text("")
    _failed_baseline(leerie, run, tmp_path)
    n0 = _installs(run)
    for _ in range(4):
        asyncio.run(leerie._retry_failed_baseline(
            st, run["caps"], log_path=tmp_path / "b.log"))
    assert _installs(run) - n0 == 2
    # A resume is a new process: it tries again.
    monkeypatch.setattr(leerie, "_BASELINE_RETRIES", {})
    flag.unlink()
    assert asyncio.run(leerie._retry_failed_baseline(
        st, run["caps"], log_path=tmp_path / "b.log")) is True


# Fails only without the installed dependency, as `prisma: not found` did on
# web 6f147a0c — an exit code no runner-missing check reads as unmeasured.
BUILD = (f"{sys.executable} -c \"import os, sys; "
         "sys.exit(not os.path.exists('deps/ok'))\"")


def _fresh_baseline(leerie, run, monkeypatch, blt: dict) -> dict:
    st = run["st"]
    del st.data["conformance"]["_baseline"]
    st.save()
    monkeypatch.setattr(leerie, "resolve_blt", lambda root: blt)
    asyncio.run(leerie._capture_conformance_baseline(
        run["run_dir"], st, run["caps"]))
    return st.data["conformance"]["_baseline"]


def test_a_build_failing_for_want_of_its_deps_is_not_base_red(
        leerie, tmp_path, monkeypatch):
    """Web 6f147a0c, driven through the real phase-4 baseline: the install
    failed, `build` failed for want of it, and the baseline counted `build`
    red (`base tree is RED on build, tests`), so a real build regression
    would have been forgiven as pre-existing. Every axis measured after a
    failed install is unmeasured, not only `tests`."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    flag.write_text("")
    base = _fresh_baseline(leerie, run, monkeypatch,
                           {"build": BUILD, "test": CMD})
    assert base["red_axes"] == []
    for axis in ("build", "tests"):
        ax = base["axes"][axis]
        assert ax["ran"] is True and ax["measured"] is False, (axis, ax)
        assert ax["passed"] is None and ax["deps_failed"] is True
        assert ax["summary"].startswith(
            "not measured: the dependency install failed")
    assert leerie._baseline_red_axes(base) == set()
    assert st.data["baseline_failing_ids"] is None
    assert leerie._base_health_payload(st)["base_red_axes"] == []
    section = leerie._format_baseline_section(base)
    assert "dependency install failed" in section
    assert "runner was not available" not in section
    # Once the registry is back, the base is measured again and builds.
    flag.unlink()
    assert asyncio.run(leerie._retry_failed_baseline(
        st, run["caps"], log_path=tmp_path / "b.log")) is True
    base = st.data["conformance"]["_baseline"]
    assert base["red_axes"] == []
    assert base["axes"]["build"]["passed"] is True


def test_a_baseline_with_no_test_command_is_measured_again(
        leerie, tmp_path, monkeypatch):
    """The re-measure keys on the tree's install, not on the `tests` axis:
    a repository whose only axis is `build` had its base build measured
    without dependencies too, and must not keep that for the run."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    flag.write_text("")
    base = _fresh_baseline(leerie, run, monkeypatch, {"build": BUILD})
    assert base["axes"]["build"]["deps_failed"] is True
    flag.unlink()
    assert asyncio.run(leerie._retry_failed_baseline(
        st, run["caps"], log_path=tmp_path / "b.log")) is True
    base = st.data["conformance"]["_baseline"]
    assert base["axes"]["build"]["measured"] is True
    assert base["axes"]["build"]["passed"] is True
    assert not base["axes"]["build"].get("deps_failed")


def test_a_final_build_failing_for_want_of_its_deps_is_no_regression(
        leerie, tmp_path, monkeypatch):
    """The final side: the build passed before a conformer round and fails
    after it only because the install failed in between. That is no
    regression to chase, and not a result the base can forgive either —
    it is unmeasured, which `_conformance_clean` reads as not clean."""
    run, flag = _setup(leerie, tmp_path, monkeypatch)
    st, staging = run["st"], run["staging"]

    def measure() -> dict:
        return asyncio.run(leerie._measure_axes(
            str(staging), {"build": BUILD}, st, run["caps"],
            log_path=tmp_path / "m.log", verbosity="quiet", memo=False))
    pre = measure()
    assert pre["build"]["measured"] is True and pre["build"]["passed"]
    flag.write_text("")
    leerie._DEPS_INSTALLED.clear()          # a fresh process installs again
    post = measure()
    assert post["build"]["measured"] is False
    assert post["build"]["deps_failed"] is True
    assert leerie._round_axis_regressions(pre, post) == []
    res = leerie._apply_measured_axes({}, post)
    # A baseline that counted `build` red (state saved before this fix)
    # does not make the unmeasured final build clean.
    assert leerie._conformance_clean(res, {"red_axes": ["build"]}) is False
    blt = leerie._format_blt_results_section(post, scope="canonical")
    assert "dependency install failed" in blt and "FAILED" not in blt


def test_a_saved_baseline_from_before_the_rule_counts_nothing_red(
        leerie, tmp_path, monkeypatch):
    """Web 6f147a0c's saved state, written by 0.44.6: `red_axes` holds
    build and tests, each `measured: True, passed: False, deps_failed:
    True`. A resume skips phase 4 and reuses it, so the readers must not
    trust the saved list: a real final build failure is not forgiven, and
    no reader calls the base red."""
    run, _flag = _setup(leerie, tmp_path, monkeypatch)
    st = run["st"]
    old = {"ran": True, "measured": True, "passed": False,
           "deps_failed": True}
    st.data["conformance"]["_baseline"] = {
        "sha": st.data["conformance"]["_baseline"]["sha"],
        "axes": {"build": {**old, "command": BUILD},
                 "tests": {**old, "command": CMD}},
        "red_axes": ["build", "tests"]}
    st.save()
    monkeypatch.setattr(leerie, "resolve_blt",
                        lambda root: {"build": BUILD, "test": CMD})
    asyncio.run(leerie._capture_conformance_baseline(      # a resume
        run["run_dir"], st, run["caps"]))
    base = st.data["conformance"]["_baseline"]
    assert base["red_axes"] == ["build", "tests"]          # reused as saved
    assert leerie._baseline_red_axes(base) == set()
    assert leerie._base_health_payload(st)["base_red_axes"] == []
    section = leerie._format_baseline_section(base)
    assert "already RED" not in section
    assert "dependency install failed" in section
    # A build the run really broke, measured with its dependencies, fails
    # the clean check instead of reading as the base's.
    final = {"build": {"ran": True, "measured": True, "passed": False,
                       "command": BUILD}}
    assert leerie._conformance_clean(final, base) is False
