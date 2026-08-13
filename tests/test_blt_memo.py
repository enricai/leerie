"""`_measure_axes` — a per-run memo over orchestrator-measured BLT verdicts.

DESIGN §9. This is what makes measuring an axis before *and* after a conformer
round affordable: measured across a real 91-subtask run, 182 of 224 conformer
rounds (81%) committed nothing at all, so the post-round tree is usually
byte-identical to the pre-round one.

A memo that returns the right value and re-runs the suite anyway is exactly the
defect this exists to prevent, and only a call-count assertion catches it — so
`test_a_hit_runs_no_subprocess` is the load-bearing test in this file. Every
"is not stored" test is paired with the control that a normal result *is*
stored, because otherwise they all pass against a memo that stores nothing.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import re
import subprocess
import types

import pytest


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A real git worktree with one commit — `HEAD^{tree}` must resolve."""
    d = tmp_path / "wt"
    d.mkdir()
    _git("init", "-q", cwd=d)
    _git("config", "user.email", "t@example.com", cwd=d)
    _git("config", "user.name", "t", cwd=d)
    (d / "a.txt").write_text("one\n")
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "init", cwd=d)
    return d


@pytest.fixture
def st():
    saves = []
    s = types.SimpleNamespace(
        data={"provision": {"recipe": []}},
        save=lambda: saves.append(1),
    )
    s.saves = saves
    return s


@pytest.fixture(autouse=True)
def _clear_deps(leerie):
    leerie._DEPS_INSTALLED.clear()
    yield
    leerie._DEPS_INSTALLED.clear()


@pytest.fixture
def measure(leerie, monkeypatch):
    """Stub `_run_streaming`; return the call log."""
    calls = []

    def install(result=(0, "ok")):
        async def _fake(cmd, **kw):
            calls.append(cmd)
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(leerie, "_run_streaming", _fake)
        return calls
    return install


def _go(leerie, tree, st, axes=None, caps=None):
    return asyncio.run(leerie._measure_axes(
        str(tree), axes or {"tests": "pytest"}, st, caps or {},
        log_path=None, verbosity="quiet"))


# --------------------------------------------------------------------------
# The key
# --------------------------------------------------------------------------

def test_key_is_stable_and_discriminating(leerie):
    k = leerie._blt_memo_key
    base = k("tests", "pytest", "abc")
    assert base == k("tests", "pytest", "abc")
    assert base != k("build", "pytest", "abc")
    assert base != k("tests", "pytest -x", "abc")
    assert base != k("tests", "pytest", "def")
    assert re.fullmatch(r"[0-9a-f]{32}", base)


def test_key_fields_cannot_run_together(leerie):
    """A naive concatenation makes ("a","bc") and ("ab","c") collide."""
    assert (leerie._blt_memo_key("a", "bc", "t")
            != leerie._blt_memo_key("ab", "c", "t"))


# --------------------------------------------------------------------------
# Hit / miss
# --------------------------------------------------------------------------

def test_a_hit_runs_no_subprocess(leerie, repo, st, measure):
    """THE LOAD-BEARING ASSERTION."""
    calls = measure()
    first = _go(leerie, repo, st)
    assert len(calls) == 1
    second = _go(leerie, repo, st)
    assert len(calls) == 1, "an unchanged tree must not re-run the command"
    assert second["tests"]["passed"] == first["tests"]["passed"]


def test_a_changed_tree_misses(leerie, repo, st, measure):
    """ANTI-VACUITY PARTNER: without this, the hit test passes against a
    memo that serves every key regardless of tree."""
    calls = measure()
    _go(leerie, repo, st)
    (repo / "a.txt").write_text("two\n")
    _git("commit", "-aqm", "change", cwd=repo)
    _go(leerie, repo, st)
    assert len(calls) == 2


def test_a_different_command_misses(leerie, repo, st, measure):
    calls = measure()
    _go(leerie, repo, st, axes={"tests": "pytest"})
    _go(leerie, repo, st, axes={"tests": "pytest -x"})
    assert len(calls) == 2


def test_an_empty_commit_still_hits(leerie, repo, st, measure):
    """Content-addressed, not commit-addressed: a conformer that commits
    nothing of substance must not invalidate the measurement."""
    calls = measure()
    _go(leerie, repo, st)
    _git("commit", "-qm", "empty", "--allow-empty", cwd=repo)
    _go(leerie, repo, st)
    assert len(calls) == 1


# --------------------------------------------------------------------------
# Dirty tree — two separate defects, two separate tests
# --------------------------------------------------------------------------

# NOTE ON AN EQUIVALENT MUTANT. `_measure_axes` derives the tree sha twice —
# once up front (for the lookup, which must happen before any install) and
# again after `_ensure_worktree_deps` (because installing can itself dirty the
# tree via lockfile churn or generated clients). Each derivation is
# load-bearing for a different reason, but either one alone is enough to keep
# a dirty tree out of the memo, so defeating just one changes no observable
# behaviour and the tests below correctly do not fail. Defeating BOTH fails
# `test_dirty_tree_is_not_stored` and `test_a_non_git_tree_degrades_to_no_memo`
# (verified). Do not "strengthen" these tests chasing the single-site mutant.
def test_dirty_tree_yields_no_sha(leerie, repo):
    assert asyncio.run(leerie._worktree_tree_sha(str(repo))) is not None
    (repo / "a.txt").write_text("uncommitted\n")
    assert asyncio.run(leerie._worktree_tree_sha(str(repo))) is None


def test_dirty_tree_is_not_stored(leerie, repo, st, measure):
    measure()
    (repo / "a.txt").write_text("uncommitted\n")
    _go(leerie, repo, st)
    assert st.data.get("blt_results") in (None, {}), (
        "a verdict keyed on a dirty tree would describe something other "
        "than what was measured")


def test_dirty_tree_is_not_served(leerie, repo, st, measure):
    """Distinct defect from the above: a store-but-never-hit bug and a
    hit-a-stale-entry bug are not the same bug."""
    calls = measure()
    _go(leerie, repo, st)                      # clean: stores
    assert len(calls) == 1
    (repo / "b.txt").write_text("new\n")       # now dirty
    _go(leerie, repo, st)
    assert len(calls) == 2, "a dirty tree must re-measure, never serve"


def test_a_non_git_tree_degrades_to_no_memo(leerie, tmp_path, st, measure):
    calls = measure()
    d = tmp_path / "plain"
    d.mkdir()
    _go(leerie, d, st)
    _go(leerie, d, st)
    assert len(calls) == 2


# --------------------------------------------------------------------------
# Only reproducible verdicts are stored
# --------------------------------------------------------------------------

def test_a_normal_result_is_stored(leerie, repo, st, measure):
    """THE CONTROL for every negative test below — without it they all pass
    against a memo that stores nothing at all."""
    measure((0, "ok"))
    _go(leerie, repo, st)
    assert len(st.data["blt_results"]) == 1


def test_a_timeout_is_not_stored(leerie, repo, st, measure):
    measure(subprocess.TimeoutExpired(cmd="pytest", timeout=1))
    _go(leerie, repo, st)
    assert st.data.get("blt_results") in (None, {})


def test_a_crash_is_not_stored(leerie, repo, st, measure):
    measure(OSError("boom"))
    _go(leerie, repo, st)
    assert st.data.get("blt_results") in (None, {})


def test_a_missing_runner_is_not_stored(leerie, repo, st, measure):
    """`measured: False` is 'could not measure', not a verdict."""
    measure((127, "bash: pytest: command not found"))
    _go(leerie, repo, st)
    assert st.data.get("blt_results") in (None, {})


def test_a_failing_run_is_stored(leerie, repo, st, measure):
    """A red verdict is still a verdict — only *unreached* ones are skipped."""
    measure((1, "2 failed"))
    _go(leerie, repo, st)
    assert len(st.data["blt_results"]) == 1


def test_an_absent_command_spawns_nothing_and_stores_nothing(
        leerie, repo, st, measure):
    calls = measure()
    res = _go(leerie, repo, st, axes={"lint": ""})
    assert calls == []
    assert res["lint"]["ran"] is False
    assert st.data.get("blt_results") in (None, {})


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------

def test_the_verdict_reaches_disk_when_written(leerie, repo, st, measure):
    """`st.save()` must fire with the entry, not only at the end of a sweep —
    a pause mid-measurement would otherwise lose it."""
    measure()
    _go(leerie, repo, st)
    assert st.saves, "expected an st.save() after recording a verdict"


def test_entries_are_json_round_trippable(leerie, repo, st, measure):
    measure()
    _go(leerie, repo, st)
    assert json.loads(json.dumps(st.data["blt_results"])) == st.data["blt_results"]


def test_a_stored_entry_is_a_copy(leerie, repo, st, measure):
    """Mutating a returned result must not rewrite the memo (the aliasing
    class `test_checkpoint_aliasing.py` exists for)."""
    measure()
    res = _go(leerie, repo, st)
    res["tests"]["passed"] = "TAMPERED"
    stored = next(iter(st.data["blt_results"].values()))
    assert stored["passed"] is True


# --------------------------------------------------------------------------
# Guard-the-guard: the state key is declared in both places
# --------------------------------------------------------------------------

def test_blt_results_is_declared_in_state_fields(leerie):
    assert "blt_results" in leerie.STATE_FIELDS


def test_blt_results_has_a_spec_table_row(leerie):
    impl = (pathlib.Path(leerie.__file__).resolve().parent.parent
            / "docs" / "IMPLEMENTATION.md").read_text(encoding="utf-8")
    assert re.search(r"^\| `blt_results` \|", impl, re.M), (
        "every st.data key needs an IMPLEMENTATION.md §8 field-table row in "
        "the same commit as its STATE_FIELDS entry")
