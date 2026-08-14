"""Execution coverage for `_run_phases`' FRESH-run branch.

v0.20.0 shipped `NameError: name 'repo_root' is not defined` at the
`subtask_tests` entry of the fresh-run state seed. Every non-resume run died
before the first worker; the run dir was left holding a literal `{}` for
state.json, because `State` creates the file and the seed raised before
`st.save()`.

Nothing in the suite could see it, for two independent reasons:

1. **No test executed this branch.** Every path that executed `_run_phases`
   did so with `resume=True`: `test_resume_planning_reentry.py` and
   `test_resume_planning_regression.py` contain the call, and
   `test_checkpoint_aliasing.py` and `test_wiring_gate_resume.py` reached it
   through the `_drive` they import from the former — four files before this
   one, and one `resume` value across all of them.
   `resume=False` appeared nowhere under `tests/`, and no test executes
   `_orchestrate` either. Even
   `test_wiring_gate_resume.py::test_fresh_run_invokes_the_gate` reuses that
   same `_args()` — "fresh" there means fresh *state*, not a fresh run.
2. **The guard that existed was a key-presence AST walk.**
   `test_orchestrator_owns_blt.py::test_subtask_tests_is_seeded_on_both_run_init_branches`
   asserts the *key* appears in both branches. It passed on the broken code:
   the key was there, only the expression was unevaluatable. Presence is not
   evaluation: a walk that checks a key exists says nothing about whether that
   key's value resolves. Establishing *that* takes either execution — this
   file — or scope resolution, which is what the companion below does
   statically. A walk over the dict literal supplies neither, which is why it
   passed while the branch it certified was dead.

So this file exists to *run* the branch. The sentinel goes on
`_enforce_and_record_cgroup_containment`, which is the first call after the
seed's `st.save()` — stopping there needs no other stubs at all, since
`preflight`, `_select_active_oauth_token`, `_backstop_capture_prior_runs`, the
`run_proc` HEAD shell-out and `run.json` init all sit after it.

The whole-module companion guard is `tests/test_no_undefined_names.py`.
"""

from __future__ import annotations

import asyncio
import json
import subprocess

import pytest

# `_args` is imported rather than copied: it is the one definition with
# importers (tests/test_checkpoint_aliasing.py and
# tests/test_wiring_gate_resume.py take it too), and a local copy here would
# have repeated every key in it — the duplication class this repo has been
# bitten by repeatedly (see the launcher-block and state-walk guards).
#
# It is not the *only* `_args` in tests/, and this is not a single-owner rule
# that a guard could enforce: test_resume_planning_regression.py maintains a
# parallel harness with its own `_args` and `_drive`, and
# test_absorb_supplied_answers.py has a purpose-built one with a different
# signature. `_caps` is taken from the same module only for consistency — it is
# a common local helper name that many test files define for themselves, not an
# owned harness.
#
# No key counts in either sentence on purpose: those are properties of the
# current tree and rot silently, unlike a measurement of a past event. The
# figures live in the commit that removed them (#209), where they describe a
# moment rather than a claim about the tree you are reading.
#
# `resume=False` is passed at each call site rather than defaulted here, so
# the branch under test is stated where it is exercised. The keys this file's
# path additionally reads (subtask_tests, skip_coverage_check,
# skip_completeness_check, skip_integration_check) are all `getattr(...,
# default)` reads and need no entry in the imported dict.
from tests.test_resume_planning_reentry import _args, _caps


class _ReachedCgroupGate(Exception):
    """Raised by the stubbed containment gate — the first call after the
    fresh-run state seed has been evaluated and saved."""


@pytest.fixture
def fresh_repo(tmp_path):
    """A real git repo — `resolve_subtask_tests` reads `leerie.toml` from
    `st.repo_root`, so a bare tmp_path would not exercise the resolution."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repo, check=True)
    (repo / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-qm", "init"],
        cwd=repo, check=True)
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / "fresh-init-001"
    run_dir.mkdir(parents=True)
    (run_dir / "subtasks").mkdir()
    return repo, leerie_root, run_dir


def _drive(leerie, monkeypatch, fresh_repo, **arg_overrides):
    """Run `_run_phases` down the fresh branch, stopping at the containment
    gate. Returns (state, reached_gate)."""
    repo, leerie_root, _run_dir = fresh_repo
    st = leerie.State(leerie_root, "fresh-init-001", repo_root=repo)

    def _boom(*_a, **_k):
        raise _ReachedCgroupGate()

    monkeypatch.setattr(
        leerie, "_enforce_and_record_cgroup_containment", _boom)

    args = _args(resume=False, task="a trivial task", **arg_overrides)

    reached = False
    try:
        asyncio.run(leerie._run_phases(
            args, _caps(leerie), leerie_root,
            st, "both", "quiet", {}, {}))
    except _ReachedCgroupGate:
        reached = True
    return st, reached


def test_fresh_run_state_seed_evaluates(leerie, monkeypatch, fresh_repo):
    """The regression pin. Against v0.20.0 this raises NameError."""
    st, reached = _drive(leerie, monkeypatch, fresh_repo)

    # Anti-vacuity: without this, the test passes against a `_run_phases`
    # that died earlier for some unrelated reason and never reached the seed
    # at all — which is exactly how this defect class hides.
    assert reached, "never reached the containment gate — the seed did not run"

    assert st.data["task"] == "a trivial task"
    assert st.data["subtask_tests"] == "scoped"


def test_subtask_tests_resolves_against_the_repo_not_the_state_root(
        leerie, monkeypatch, fresh_repo):
    """The value must come from the repo's own `leerie.toml`.

    This is what pins `st.repo_root` specifically rather than any Path that
    happens to be in scope: the state root is `<repo>/.leerie` here, so a
    resolver pointed at the wrong root would silently return the default and
    the assertion below would fail.
    """
    repo, _leerie_root, _run_dir = fresh_repo
    (repo / "leerie.toml").write_text("subtask_tests = full\n")

    st, reached = _drive(leerie, monkeypatch, fresh_repo)

    assert reached
    assert st.data["subtask_tests"] == "full"


def test_cli_flag_wins_over_the_repo_file(leerie, monkeypatch, fresh_repo):
    repo, _leerie_root, _run_dir = fresh_repo
    (repo / "leerie.toml").write_text("subtask_tests = full\n")

    st, reached = _drive(leerie, monkeypatch, fresh_repo, subtask_tests="off")

    assert reached
    assert st.data["subtask_tests"] == "off"


def test_the_seed_reaches_disk_before_the_gate(leerie, monkeypatch,
                                               fresh_repo):
    """`st.save()` runs before the containment gate, so a crash there still
    leaves a resumable state.json. The broken build left `{}` on disk — the
    file existed and carried nothing, which is the signature the incident was
    identified by.
    """
    _repo, _leerie_root, run_dir = fresh_repo
    st, reached = _drive(leerie, monkeypatch, fresh_repo)

    assert reached
    on_disk = json.loads((run_dir / "state.json").read_text())
    assert on_disk != {}, "state.json is empty — the v0.20.0 crash signature"
    assert on_disk["subtask_tests"] == "scoped"
    assert on_disk["task"] == "a trivial task"


def test_fresh_branch_is_actually_the_one_under_test(leerie, monkeypatch,
                                                     fresh_repo):
    """Guard-the-guard: prove these tests take the FRESH branch, not the
    resume one. With `resume=True` and no state.json to load, `_run_phases`
    dies instead of reaching the gate — so if this file were silently driving
    the resume path, every assertion above would be about the wrong branch.
    """
    repo, leerie_root, _run_dir = fresh_repo
    st = leerie.State(leerie_root, "fresh-init-001", repo_root=repo)
    monkeypatch.setattr(
        leerie, "_enforce_and_record_cgroup_containment",
        lambda *_a, **_k: (_ for _ in ()).throw(_ReachedCgroupGate()))

    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_phases(
            _args(resume=True), _caps(leerie), leerie_root, st,
            "both", "quiet", {}, {}))
