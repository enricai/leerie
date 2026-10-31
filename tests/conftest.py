"""Shared pytest fixtures for the leerie test suite.

leerie.py is a single script (no package), so we load it once as a
module via importlib and expose it to every test via the `leerie`
fixture.
"""
from __future__ import annotations

import asyncio
import contextlib
import ctypes
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def run_git_repo_first(repo: Path, *args: str) -> subprocess.CompletedProcess:
    """`git -C <repo> <args>`, repo-first signature (`_git(repo, *args)`)."""
    return subprocess.run(["git", "-C", str(repo), *args], check=True,
                          capture_output=True, text=True)


def run_git_cwd_kw(*args: str, cwd: Path) -> subprocess.CompletedProcess:
    """`git <args>` run in `cwd`, keyword-only signature (`_git(*args, cwd=)`)."""
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True)


def run_git_cwd_first_stdout(cwd: Path, *args: str) -> str:
    """`git <args>` run in `cwd`, cwd-first signature, returns stripped stdout."""
    out = subprocess.run(["git", *args], cwd=str(cwd), check=True,
                         capture_output=True, text=True)
    return out.stdout.strip()


def run_git_cwd_first_unchecked(
    cwd: Path, *args: str, env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """`git <args>` run in `cwd`, cwd-first signature, unchecked (`check=False`)
    so a nonzero rc is returned rather than raised. Several call sites expect
    a nonzero rc (a refused `-d`, a missing branch) as a normal outcome."""
    return subprocess.run(["git", *args], cwd=str(cwd), capture_output=True,
                          text=True, check=False, env=env)


def init_git_repo(path: Path) -> Path:
    """Create a minimal git repo at `path` with one commit on `main`."""
    path.mkdir(parents=True, exist_ok=True)
    run_git_repo_first(path, "init", "-q", "-b", "main")
    run_git_repo_first(path, "config", "user.email", "test@x")
    run_git_repo_first(path, "config", "user.name", "test")
    (path / "a.txt").write_text("a\n")
    run_git_repo_first(path, "add", ".")
    run_git_repo_first(path, "commit", "-q", "-m", "a")
    return path


def _run(coro):
    """Run an async coroutine synchronously (this repo does not use
    pytest-asyncio). Single owner for the byte-identical helper that was
    previously redefined in fifteen test files."""
    return asyncio.run(coro)


def run_bash(
    script: str,
    env: dict | None = None,
    *,
    cwd: Path | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a bash script via `bash -c`, merging `env` onto the parent
    process environment. Single owner for the byte-identical `_run_bash`
    helper previously redefined across ~22 test files."""
    base_env = {k: v for k, v in os.environ.items()}
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        input=input,
        capture_output=True,
        text=True,
    )


try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def validate_or_fallback_required(schema: dict, instance: dict) -> bool:
    """Validate `instance` against `schema`, or fall back to a required-field
    check when `jsonschema` isn't installed.

    Single owner for the idiom duplicated across the schema-test files
    (e.g. tests/test_fit_judge_schema.py:22-30): when jsonschema is
    available, use it directly (raises on an invalid instance) and return
    True; otherwise assert every `schema["required"]` key is present in
    `instance` and return False, so the caller runs its own type-specific
    fallback assertions on top.
    """
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return True
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    return False


@pytest.fixture(scope="session")
def leerie():
    """The leerie module loaded from orchestrator/leerie.py."""
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _has_treesitter() -> bool:
    """True only if the installed tree-sitter stack can actually extract a
    symbol. Delegates to leerie's own `_tree_sitter_extraction_works()`
    functional probe (mere importability is insufficient — an
    installed-but-incompatible language-pack version imports fine yet extracts
    nothing). Shared here so extraction-dependent repo-map test modules gate
    on it without duplication."""
    spec = importlib.util.spec_from_file_location("_leerie_ts_probe", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(mod)
        return mod._tree_sitter_extraction_works()
    except Exception:
        return False


# Evaluated once at collection; extraction-dependent repo-map test modules
# import this to skip cleanly on hosts without a working parser.
HAS_TREESITTER = _has_treesitter()


# Evaluated once at collection; test modules that exercise *host-side* bash
# import this to skip cleanly where `jq` is absent.
#
# Why a gate rather than adding jq to the image: the host/container split is
# deliberate. Host bash uses `jq` — the launcher hard-fails at preflight
# without it (`leerie`'s "jq not found on PATH" check, which tells you to
# `brew install jq`) — while code that runs *inside* the container uses
# python3, exactly as `scripts/remote/seed-auth.sh` documents: "python3 over
# jq because jq isn't in the leerie image (see Dockerfile)".
#
# The gated modules source scripts the host owns (`host-finalize.sh`,
# `provision.sh`'s `decide_teardown`, the launcher's finalize path) and stub
# `git`/`gh` on PATH but not `jq`, so they silently inherit it from whichever
# machine runs pytest. They pass on a dev host and in CI (both ship jq) and
# fail only inside `leerie:<version>` — where the scripts under test could
# never succeed anyway, since gh auth, ssh-agent, and Keychain are all
# host-side (DESIGN §6 *Finalization*).
#
# Do NOT "fix" a skip here by installing jq into the image: that buys a green
# tick, not working code, and erodes the boundary.
HAS_JQ = shutil.which("jq") is not None


def _make_caps(leerie, **overrides) -> dict:
    """Shared DEFAULT_CAPS-derived caps builder for the recursive-decompose
    test family (§5½ (P1)). Callers layer on extra keys (e.g.
    `subfile_split_max_span`) via **overrides rather than every file forcing
    an identical cap set."""
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _fit_response(score: float) -> dict:
    """Shared stubbed fit_judge worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "score": score,
        "rationale": f"score={score}",
        "diffuse": "" if score >= 0.70 else "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _split_response(parent_id: str, n: int) -> dict:
    """Shared stubbed splitter worker response for the recursive-decompose
    test family (§5½ (P1))."""
    return {
        "children": [
            {
                "id": f"{parent_id}-{i + 1}",
                "title": f"child {i + 1}",
                "success_criteria_seed": f"criterion {i + 1}",
                "files_likely_touched": [f"f{i}.py"],
            }
            for i in range(n)
        ],
    }


def fake_claude_on_path(tmp_path: Path, monkeypatch) -> Path:
    """Put a stub `claude` binary on PATH and return it.

    `main()` gates on `shutil.which("claude")` and `die()`s (exit 1) long
    before anything a `main()`-driving test asserts on -- 87 lines before
    `State(...)` mints the run dir, and well before the top-level
    try/except. The binary is on a developer's PATH and NOT on the CI
    runner's, so a suite that drives the real `main()` without this passes
    locally and fails on CI with every expected exit code collapsed to
    `die()`'s 1. That is the trap CLAUDE.md records ("A local pass is not
    evidence until the host lacks `claude`"), and it is how 14 tests in
    `test_main_exception_arms.py` shipped red.

    Single owner on purpose: this lived in `test_main_cli_wiring.py` while
    `test_main_exception_arms.py` -- the same harness minus this one call --
    had no copy at all. Anything driving the real `main()` imports it from
    here rather than reproducing it.
    """
    bindir = tmp_path / "fakebin"
    bindir.mkdir(exist_ok=True)
    stub = bindir / "claude"
    stub.write_text("#!/bin/sh\necho '{}'\n")
    stub.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    return stub


# prctl option numbers, mirrored from leerie's own `_PR_SET_CHILD_SUBREAPER` /
# `_PR_GET_CHILD_SUBREAPER`. Duplicated as literals rather than imported
# because this fixture is autouse and must not force the session-scoped
# `leerie` module load onto every test in the suite;
# `tests/test_subreaper.py` carries a coupling guard that the two agree.
_PR_SET_CHILD_SUBREAPER = 36
_PR_GET_CHILD_SUBREAPER = 37


@contextlib.contextmanager
def child_subreaper_restored():
    """Restore this process's `PR_SET_CHILD_SUBREAPER` flag on exit.

    The mechanism behind the autouse fixture below, exposed as a plain
    context manager so a test can drive it without reaching into pytest's
    private fixture internals (which differ across pytest versions).

    Linux-only (`prctl` is a Linux syscall); a no-op everywhere else, and a
    no-op if the flag is unreadable or comes back unchanged.
    """
    if not sys.platform.startswith("linux"):
        yield
        return
    try:
        libc = ctypes.CDLL(None, use_errno=True)
        before = ctypes.c_int(0)
        if libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(before),
                      0, 0, 0) != 0:
            yield
            return
    except OSError:
        yield
        return
    try:
        yield
    finally:
        after = ctypes.c_int(0)
        if (libc.prctl(_PR_GET_CHILD_SUBREAPER, ctypes.byref(after),
                       0, 0, 0) == 0
                and after.value != before.value):
            libc.prctl(_PR_SET_CHILD_SUBREAPER, before.value, 0, 0, 0)


@pytest.fixture(autouse=True)
def _default_aws_region(monkeypatch):
    """EC2 preflight (`require_aws`) dies without a region, and a dev host
    may have none configured; subprocess tests inherit os.environ. Tests
    that assert the missing-region error set their own env explicitly."""
    monkeypatch.setenv("AWS_REGION", "us-east-1")


@pytest.fixture(autouse=True)
def _no_iam_propagation_wait(monkeypatch):
    """`ensure_instance_profile` sleeps 10s after creating the IAM profile
    so AWS can propagate it; every provision against the aws stub creates
    one, and that sleep alone pushed the CI job past its 20-minute cap."""
    monkeypatch.setenv("LEERIE_EC2_IAM_PROPAGATION_S", "0")


_FAKE_RUN_DIR: Path | None = None


def fake_run_dir() -> Path:
    """This test's own run directory, for a test double of the state that
    has no real one. `claude_p` makes per-worker directories and a
    configuration template in the run directory (DESIGN §6 *Each worker's
    own configuration and temporary directories*), so one fixed path shared
    by every test (and every suite on the host) would carry one test's
    template and workers into the next."""
    assert _FAKE_RUN_DIR is not None
    return _FAKE_RUN_DIR


@pytest.fixture(autouse=True)
def _fresh_fake_run_dir(tmp_path_factory):
    global _FAKE_RUN_DIR
    _FAKE_RUN_DIR = tmp_path_factory.mktemp("fake-run")
    yield
    _FAKE_RUN_DIR = None


@pytest.fixture(autouse=True)
def _no_operator_claude_config(tmp_path_factory, monkeypatch):
    """Point the operator's CLI configuration at an empty directory: every
    `claude_p` snapshots it into the run's template (DESIGN §6 *Each
    worker's own configuration and temporary directories*), and the real
    `~/.claude` holds the developer's credentials and tens of MB of skills
    and plugins, which would otherwise land in every test's run directory
    under /tmp. A test of that snapshot sets its own."""
    monkeypatch.setenv("CLAUDE_CONFIG_DIR",
                       str(tmp_path_factory.mktemp("operator-claude")))


@pytest.fixture(autouse=True)
def _fresh_head_acceptance_results(leerie, monkeypatch):
    """Give every test an empty `_HEAD_ACCEPTANCE_RESULTS`: the `leerie`
    module is session-scoped, so a measurement cached by one test would
    otherwise be readable by the next one that reuses its (run dir, sha)
    key."""
    monkeypatch.setattr(leerie, "_HEAD_ACCEPTANCE_RESULTS", {})


@pytest.fixture(autouse=True)
def _fresh_cgroup_probe(leerie, monkeypatch):
    """Give every test an unprobed broker: `_cgroup_probe` memoizes into
    module globals, and the base-health baseline's peak measurement now
    probes (DESIGN §6 *The repo's own build peak*) — on a host with no
    broker one test's `False` would otherwise answer every later test's
    probe."""
    monkeypatch.setattr(leerie, "_CGROUP_PROBE_RESULT", None)
    monkeypatch.setattr(leerie, "_CGROUP_HIERARCHY", None)


@pytest.fixture(autouse=True)
def _no_real_planning_worktree(request, monkeypatch):
    """Keep `_ensure_planning_worktree` from shelling out to real git.

    It runs `scripts/planning-worktree.sh`, which does a real
    `git worktree add` rooted at `resolve_leerie_root()`. With
    `LEERIE_STATE_DIR` unset -- the default for a direct Python caller -- that
    resolves to `<repo>/.leerie`, so any test driving the real `_run_phases`
    created a FULL CHECKOUT OF THIS REPO inside this repo (registered, since
    per-run repositories, in that run's `<run-dir>/repo`; before them, in
    this repo's own `.git/worktrees/`). The run's repository itself is
    `_no_run_repo_without_a_git_repo`'s concern, below.

    The failure mode is silent in three ways at once, which is why this is a
    conftest-level guard rather than a stub in each driving test: `.leerie/*`
    is gitignored so `git status` stays clean; the directories survive the
    session; and the damage surfaces in an unrelated file, because a nested
    `.leerie/.../worktrees/planning/tests/...` copy does not match the
    `rel.startswith("tests/")` exclusion that scanners like
    `test_helper_naming_convention` use. Measured: two worktrees, 25 MB, and
    one red test with no visible connection to the change that caused it.

    Opt out with `@pytest.mark.real_planning_worktree` when the worktree
    mechanics are themselves the subject.
    """
    if request.node.get_closest_marker("real_planning_worktree"):
        return
    leerie = request.getfixturevalue("leerie")

    async def _stub(st):
        run_dir = Path(getattr(st, "run_dir", "."))
        path = str(run_dir / "worktrees"
                   / leerie._worktree_dirname(run_dir.name, "planning"))
        try:
            st.data["planning_worktree"] = path
            st.save()
        except Exception:
            pass
        return path

    monkeypatch.setattr(leerie, "_ensure_planning_worktree", _stub)


def relocate_into_run_repo(leerie, st) -> Path:
    """Turn a hand-built pre-change layout into the run's own repository
    (DESIGN §6 *The run's own repository*): for a test that committed the
    run's branches in its "user" repository (`st.repo_root`) and checked
    staging out there, make `<run-dir>/repo` (`_ensure_run_repo`), move
    every `leerie/*` branch into it (deleted from the user's repository),
    re-create every worktree of the user's repository that lies under the
    run directory as a worktree of the run's repository on the same branch
    or commit, and record `base_sha` — the cut point, the merge-base of the
    run branch and the working branch — when the state names a working
    branch and has none. Afterwards the user's repository holds no leerie
    ref or worktree, as in a real run. Returns the run's repository."""
    user = Path(st.repo_root)
    run_dir = Path(st.run_dir).resolve()

    def git(cwd, *a, check=True):
        return subprocess.run(["git", "-C", str(cwd), *a], check=check,
                              capture_output=True, text=True).stdout.strip()
    trees = []
    cur: dict = {}
    for line in git(user, "worktree", "list", "--porcelain").splitlines() + [""]:
        if not line:
            if cur.get("path") and Path(cur["path"]).resolve().is_relative_to(
                    run_dir):
                trees.append(cur)
            cur = {}
        elif line.startswith("worktree "):
            cur["path"] = line[len("worktree "):]
        elif line.startswith("HEAD "):
            cur["head"] = line[len("HEAD "):]
        elif line.startswith("branch "):
            cur["branch"] = line[len("branch "):]
    for t in trees:
        git(user, "worktree", "remove", "--force", t["path"])
    repo = leerie._ensure_run_repo(st)
    refs = git(user, "for-each-ref", "--format=%(refname)",
               "refs/heads/leerie").split()
    if refs:
        git(repo, "fetch", "-q", str(user), *[f"+{r}:{r}" for r in refs])
        for r in refs:
            git(user, "update-ref", "-d", r)
    for t in trees:
        if t.get("branch"):
            git(repo, "worktree", "add", "-q", t["path"],
                t["branch"][len("refs/heads/"):])
        else:
            git(repo, "worktree", "add", "-q", "--detach", t["path"],
                t["head"])
    wb = st.data.get("working_branch")
    run_branch = f"refs/heads/leerie/runs/{st.run_id}"
    if wb and not st.data.get("base_sha") and git(
            repo, "rev-parse", "-q", "--verify", run_branch, check=False):
        st.data["base_sha"] = git(repo, "merge-base", run_branch,
                                  f"refs/heads/{wb}")
        st.save()
    return repo


@pytest.fixture(autouse=True)
def _no_run_repo_without_a_git_repo(request, monkeypatch):
    """Let a test drive `_run_phases` / `phase_execute` / the planning
    worktree over a state whose `repo_root` is no git repository.

    Every run makes its own repository from the user's at start
    (`_ensure_run_repo`, DESIGN §6 *The run's own repository*), and dies
    when it cannot — `_preflight_repo` guarantees a repository in
    production. Many tests drive those phases with the scripts and git
    stubbed, over a temporary directory or a `Mock` state; for them the
    creation and the refresh are no-ops here. A test whose `repo_root` is a
    real repository gets the real thing, unstubbed."""
    leerie = request.getfixturevalue("leerie")
    real_ensure = leerie._ensure_run_repo_or_die
    real_refresh = leerie._refresh_run_repo_base_or_die

    def _has_repo(st) -> bool:
        root = getattr(st, "repo_root", None)
        run_dir = getattr(st, "run_dir", None)
        if not isinstance(root, (str, os.PathLike)) or not isinstance(
                run_dir, (str, os.PathLike)):
            return False
        return subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--git-dir"],
            capture_output=True).returncode == 0

    async def ensure(st):
        if _has_repo(st):
            # A real run repository is a full copy of the user's branch
            # (measured on this repository: 31 MB, ~11 s). Under this
            # checkout — the default state root `<repo>/.leerie` when
            # LEERIE_STATE_DIR is unset — it would outlive the session,
            # gitignored and unseen, the planning worktree's old leak
            # (above). Fail the test that would make it, by name.
            run_dir = Path(str(st.run_dir)).resolve()
            if run_dir.is_relative_to(REPO_ROOT):
                pytest.fail(
                    f"this test drives the real run repository under this "
                    f"checkout ({run_dir}): pin the state root to tmp_path "
                    "(State(tmp_path / ..., ...) or LEERIE_STATE_DIR)")
            return await real_ensure(st)
        return Path(str(getattr(st, "run_dir", "."))) / "repo"

    async def refresh(st, where):
        if _has_repo(st):
            return await real_refresh(st, where)
        return ""
    monkeypatch.setattr(leerie, "_ensure_run_repo_or_die", ensure)
    monkeypatch.setattr(leerie, "_refresh_run_repo_base_or_die", refresh)


@pytest.fixture(autouse=True)
def _restore_git_hooks_env():
    """Put the git config environment back after every test.

    `main()` turns leerie's hooks off by appending `core.hooksPath` to
    `os.environ`'s `GIT_CONFIG_*` entries (`_git_hooks_off`, DESIGN §6½
    *Hooks off*). A test that drives the real `main()` would leave that on
    the pytest process, and every later test's git would then run no hook
    — silently, depending on collection order (the same shape as the
    subreaper flag below)."""
    keys = lambda: {k for k in os.environ  # noqa: E731
                    if k.startswith(("GIT_CONFIG_", "LEERIE_GIT_HOOKS"))}
    before = {k: os.environ[k] for k in keys()}
    yield
    for k in keys() - set(before):
        os.environ.pop(k, None)
    os.environ.update(before)


@pytest.fixture(autouse=True)
def _restore_child_subreaper():
    """Put `PR_SET_CHILD_SUBREAPER` back after every test.

    `main()` calls `_become_subreaper()` among its first statements, before
    argparse -- so every test that drives the real `main()` sets
    `prctl(PR_SET_CHILD_SUBREAPER, 1)` on the *pytest* process, and nothing
    ever cleared it. The flag then silently changes the meaning of process
    liveness for every later test in the same session: an orphaned
    grandchild that would normally reparent to PID 1 (which `wait()`s it, so
    the PID vanishes) instead reparents to pytest, which never reaps it. It
    lingers as a zombie -- and a zombie still owns its PID slot, so
    `os.kill(pid, 0)` succeeds and a liveness probe reports a process that
    was in fact killed.

    Measured: this turned three `tests/test_signal_cleanup.py` orphan-reaping
    assertions red on CI while `orchestrator/leerie.py` and that file were
    both byte-identical to main. Collection is alphabetical, so `test_main_*`
    poisons `test_signal_cleanup`; `tests/test_subreaper.py` sets the same
    flag and escaped only by sorting *after* it -- an accident of filename,
    not a fix. Hence a suite-wide autouse restore rather than a per-file one.
    """
    with child_subreaper_restored():
        yield


def judge_checkout_in_place(leerie, monkeypatch) -> None:
    """For a test that fakes git itself (`run_proc` stubbed, a made-up
    commit): `_judge_checkout` yields the given tree instead of adding a
    real checkout of a commit that does not exist. Where the judge reads is
    not such a test's subject; tests/test_pr_review_runs_outside_staging.py
    drives the real one."""
    import contextlib

    @contextlib.asynccontextmanager
    async def in_place(st, staging, name, *, rev=None, share_ignored=False):
        yield staging
    monkeypatch.setattr(leerie, "_judge_checkout", in_place)
