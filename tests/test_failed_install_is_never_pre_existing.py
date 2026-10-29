"""Review round 9 suspicion (a), driven: a dependency install that fails in
a fresh checkout was ignored on both sides of the failing-test-ID diff, so
every failure there read as the base's own and a real regression shipped
`done` (DESIGN §9 *Which failures are new*, *A failed install is no
measurement*).

The driver: staging's deps installed fine, then a commit makes `test_x`
really fail. The install fails from then on (a registry outage). Phase 5
measures the tree that ships in a fresh checkout — its install fails, so
every test fails — and B* reruns the base in a fresh checkout whose install
fails too, so every candidate failed on the base as well: `pre_existing`.
Before the fix the run ended `done` with an empty end-state section."""
from __future__ import annotations

import asyncio
import os
import stat
import sys

import pytest

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import SUITE, _git  # noqa: E402
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
    run["st"].data["conformance"]["_baseline"]["sha"] = _git(
        repo, "rev-parse", "HEAD")
    flag = tmp_path / "registry-down"
    bindir = tmp_path / "bin"
    bindir.mkdir()
    tool = bindir / "fakeinst"
    calls = tmp_path / "install-calls"
    tool.write_text(f"#!/bin/sh\necho x >> '{calls}'\n"
                    f"[ -e '{flag}' ] && exit 1\n"
                    "mkdir -p deps && touch deps/ok\n")
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    run["st"].data["provision"] = {"recipe": [
        {"kind": "install", "command": ["fakeinst"]}]}
    run["st"].save()
    return run, flag


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
    # Staging installs first, then the registry goes down.
    asyncio.run(leerie._ensure_worktree_deps(
        str(staging), st, run["caps"], log_path=tmp_path / "i.log",
        verbosity="quiet"))
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
    assert axes["tests"]["failing_ids"]           # everything failed
    st.data["conformance"]["_baseline"]["axes"] = axes
    assert leerie._final_diff_base_gap(st, {}).startswith(
        "failing-test-ID diff: run the tests on the PR branch and on the "
        "base")
    # Not memoised: the outage is not a fact about the tree.
    assert not st.data.get("blt_results")


def _installs(tmp_path) -> int:
    p = tmp_path / "install-calls"
    return len(p.read_text().splitlines()) if p.exists() else 0


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
    assert go() is False and _installs(tmp_path) == 1
    flag.unlink()
    assert go() is True and _installs(tmp_path) == 2    # retried, cleared
    assert go() is True and _installs(tmp_path) == 2    # a memo hit
    # Down for good: the first install and two retries, then the memo.
    leerie._DEPS_INSTALLED.discard(os.path.realpath(run["repo"]))
    flag.write_text("")
    got = [go() for _ in range(5)]
    assert got == [False] * 5 and _installs(tmp_path) == 2 + 3


def test_a_flaky_first_install_does_not_unmeasure_a_clean_run(
        leerie, tmp_path, monkeypatch):
    """The reviewer's driver: staging's install fails the first time only
    (a registry 503). Before, it ran once, every later measurement carried
    `deps_failed`, and a clean run listed "not measured on the tree that
    ships". Now the next measurement installs again, and the run ends
    `done` with an empty section."""
    run = _repo(leerie, tmp_path, monkeypatch, ignore="__pycache__/\n")
    st = run["st"]
    flag = tmp_path / "registry-was-down-once"
    calls = tmp_path / "calls"
    sh = (f"echo x >> {calls}; if [ -f {flag} ]; then exit 0; fi; "
          f"touch {flag}; exit 1")
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": ["sh", "-c", sh]}]}
    st.save()
    _quiet_conformer(leerie, monkeypatch)
    asyncio.run(leerie._run_final_conformance(
        run["run_dir"], st, run["caps"], {"conformer": "sonnet"},
        {"conformer": "low"}))
    _phase5(leerie, run)
    assert len(calls.read_text().splitlines()) == 2
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
    n0 = _installs(tmp_path)
    for _ in range(4):
        asyncio.run(leerie._retry_failed_baseline(
            st, run["caps"], log_path=tmp_path / "b.log"))
    assert _installs(tmp_path) - n0 == 2
    # A resume is a new process: it tries again.
    monkeypatch.setattr(leerie, "_BASELINE_RETRIES", {})
    flag.unlink()
    assert asyncio.run(leerie._retry_failed_baseline(
        st, run["caps"], log_path=tmp_path / "b.log")) is True
