"""The run's own repository (DESIGN §6 *The run's own repository*, RC4/I4).

Every tree a worker gets hangs off `<run-dir>/repo`, a bare, self-contained
repository leerie makes once per run, so no worker's git verb reaches the
user's refs, stash, config, `info/exclude`, hooks or worktree registry.

Everything here runs real git against a throwaway "user" repository, through
the real scripts and helpers. Review round 16 H2's two drivers are rewritten
as I4 tests: the user's stash survives a worker's `stash`/`pop`, and a worker
cannot move the user's branches.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_planning_worktree

RUN_ID = "r1abcdef0123"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=check,
                          capture_output=True, text=True).stdout.strip()


def _user_repo(tmp_path: Path) -> Path:
    r = tmp_path / "user"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    (r / "app.py").write_text("x = 1\n")
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    _git(r, "tag", "-a", "v1", "-m", "v1")
    return r


class _St:
    """The attributes the run-repository code touches."""

    def __init__(self, repo: Path, state_root: Path, run_id: str = RUN_ID):
        self.repo_root = repo
        self.run_id = run_id
        self.run_dir = state_root / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.data: dict = {"working_branch": "main"}
        self.saves = 0

    def save(self) -> None:
        self.saves += 1


@pytest.fixture()
def env(tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    return repo, state, _St(repo, state)


def _refs(repo: Path) -> list[str]:
    return _git(repo, "for-each-ref", "--format=%(refname)").split()


# ---------------------------------------------------------------- creation

def test_run_repo_is_bare_self_contained_and_holds_only_the_base(leerie,
                                                                 env):
    repo, _state, st = env
    _git(repo, "branch", "develop")
    (repo / "app.py").write_text("x = 1\nWIP = 1\n")
    _git(repo, "stash", "push", "-qm", "user wip")
    run_repo = leerie._ensure_run_repo(st)
    assert run_repo == st.run_dir / "repo"
    assert _git(run_repo, "rev-parse", "--is-bare-repository") == "true"
    # Self-contained: no borrowed object store, no remote of the user's.
    assert not (run_repo / "objects" / "info" / "alternates").exists()
    assert _git(run_repo, "remote") == ""
    # The working branch, the user's HEAD and the tags in that history —
    # nothing else: no develop, no refs/stash.
    assert sorted(_refs(run_repo)) == ["refs/heads/main",
                                       "refs/leerie/user-head",
                                       "refs/tags/v1"]
    assert _git(run_repo, "rev-parse", "HEAD") == _git(repo, "rev-parse",
                                                       "HEAD")
    assert (_git(run_repo, "rev-parse", "--show-object-format")
            == _git(repo, "rev-parse", "--show-object-format"))
    assert st.data["run_repo"] == "repo"


def test_ensure_is_idempotent_and_leaves_no_temporary(leerie, env):
    _repo, _state, st = env
    first = leerie._ensure_run_repo(st)
    marker = first / "leerie-marker"
    marker.write_text("kept")
    assert leerie._ensure_run_repo(st) == first
    assert marker.read_text() == "kept"
    assert [p.name for p in st.run_dir.iterdir()
            if p.name.startswith(".repo.tmp")] == []


def test_a_failed_creation_leaves_no_repository(leerie, tmp_path,
                                                monkeypatch):
    not_git = tmp_path / "plain"
    not_git.mkdir()
    monkeypatch.chdir(not_git)
    st = _St(not_git, tmp_path / "state")
    with pytest.raises(leerie.RunRepoError):
        leerie._ensure_run_repo(st)
    assert not (st.run_dir / "repo").exists()
    assert "run_repo" not in st.data


# ----------------------------------------------------------- config carry

def test_config_carry_copies_behaviour_and_drops_identity_keys(leerie,
                                                                tmp_path):
    user = _user_repo(tmp_path)
    _git(user, "config", "core.hooksPath", ".husky/_")
    _git(user, "config", "include.path", "../extra.cfg")
    _git(user, "config", "core.autocrlf", "input")
    run_repo = tmp_path / "run.git"
    _git(tmp_path, "init", "-q", "--bare", str(run_repo))
    leerie._carry_repo_config(user, run_repo)
    cfg = str(run_repo / "config")
    assert _git(tmp_path, "config", "-f", cfg, "user.email") == "t@x"
    assert _git(tmp_path, "config", "-f", cfg, "core.hooksPath") == ".husky/_"
    assert _git(tmp_path, "config", "-f", cfg, "core.autocrlf") == "input"
    assert _git(tmp_path, "config", "-f", cfg, "core.bare") == "true"
    assert _git(tmp_path, "config", "-f", cfg, "--get-all",
                "core.bare").splitlines() == ["true"]
    assert _git(tmp_path, "config", "-f", cfg, "include.path",
                check=False) == ""


def test_no_hooks_path_points_at_the_users_hooks_even_with_hooks_off(
        leerie, tmp_path, monkeypatch):
    """`main()` puts leerie's hooks-off entry in the environment; the carry
    must read the user's real hooks path, not that entry."""
    env = dict(os.environ)
    leerie._git_hooks_off(env)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    user = _user_repo(tmp_path)
    run_repo = tmp_path / "run.git"
    _git(tmp_path, "init", "-q", "--bare", str(run_repo))
    leerie._carry_repo_config(user, run_repo)
    got = _git(tmp_path, "config", "-f", str(run_repo / "config"),
               "core.hooksPath")
    assert got == str((user / ".git" / "hooks").resolve())


def test_a_workers_commit_in_the_run_repo_runs_the_users_hook(leerie, env,
                                                               tmp_path):
    """Substance: workers keep the repository's hooks (DESIGN §6
    *Finalization*), in the run's repository too."""
    repo, _state, st = env
    log = tmp_path / "hook.log"
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text(f"#!/bin/sh\necho ran >> '{log}'\n")
    hook.chmod(0o755)
    run_repo = leerie._ensure_run_repo(st)
    wt = tmp_path / "wt"
    _git(run_repo, "worktree", "add", "-q", "-b", "w", str(wt), "main")
    (wt / "app.py").write_text("x = 2\n")
    _git(wt, "commit", "-qam", "worker")
    assert log.read_text() == "ran\n"
    assert _git(wt, "log", "-1", "--format=%ae") == "t@x"


# ---------------------------------------------------------------- refresh

def test_refresh_follows_the_users_tip_and_restores_a_moved_copy(leerie,
                                                                 env):
    repo, _state, st = env
    run_repo = leerie._ensure_run_repo(st)
    # A worker moves the run repository's copy of `main`.
    _git(run_repo, "update-ref", "refs/heads/main",
         _git(run_repo, "rev-parse", "main"))
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "user advances")
    tip = _git(repo, "rev-parse", "main")
    assert leerie._refresh_run_repo_base(st) == tip
    assert _git(run_repo, "rev-parse", "main") == tip
    assert _git(run_repo, "rev-parse", "HEAD") == tip


def test_a_detached_start_gives_a_detached_bare_head(leerie, env):
    repo, _state, st = env
    first = _git(repo, "rev-parse", "HEAD")
    (repo / "b.txt").write_text("b\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "two")
    _git(repo, "checkout", "-q", "--detach", first)
    st.data["working_branch"] = "HEAD"
    run_repo = leerie._ensure_run_repo(st)
    assert _git(run_repo, "rev-parse", "HEAD") == first
    assert _git(run_repo, "symbolic-ref", "-q", "HEAD", check=False) == ""


# ------------------------------------------------- r16 H2, as I4 tests

def _setup_run(leerie, st) -> Path:
    asyncio.run(leerie._ensure_run_repo_or_die(st))
    proc = asyncio.run(leerie._run_script("setup-run.sh", st.run_id, "main",
                                          st=st))
    assert proc.returncode == 0, proc.stderr
    return st.run_dir / "worktrees" / "staging"


def _subtask(leerie, st, sid: str) -> Path:
    proc = asyncio.run(leerie._run_script("new-worktree.sh", sid, st.run_id,
                                          st=st))
    assert proc.returncode == 0, proc.stderr
    return Path(proc.stdout.strip().splitlines()[-1])


def test_a_workers_stash_pop_never_sees_the_users_stash(leerie, env):
    repo, _state, st = env
    (repo / "app.py").write_text("x = 1\nSECRET_WIP = 'do not ship'\n")
    _git(repo, "stash", "push", "-qm", "user wip")
    _setup_run(leerie, st)
    wt = _subtask(leerie, st, "s1")
    branch = f"leerie/subtasks/{RUN_ID}/s1"
    base = _git(wt, "rev-parse", "HEAD")
    out = subprocess.run(["git", "stash"], cwd=wt, capture_output=True,
                         text=True)
    assert "No local changes to save" in out.stdout
    pop = subprocess.run(["git", "stash", "pop"], cwd=wt,
                         capture_output=True, text=True)
    assert pop.returncode != 0 and "No stash entries" in pop.stderr
    assert "SECRET_WIP" not in (wt / "app.py").read_text()

    async def reask(note):            # never needed: the tree is clean
        raise AssertionError(note)
    got = asyncio.run(leerie._clean_tree_gate(
        wt, caps=dict(leerie.DEFAULT_CAPS), label="s1", reask=reask,
        branch=branch, base=base))
    assert got == []
    assert "user wip" in _git(repo, "stash", "list")


def test_a_worker_cannot_move_the_users_branches(leerie, env):
    repo, _state, st = env
    _git(repo, "branch", "develop")
    before = {r: _git(repo, "rev-parse", r) for r in ("main", "develop")}
    staging = _setup_run(leerie, st)
    # The user's develop is not in the run's repository at all.
    co = subprocess.run(["git", "checkout", "-q", "develop"], cwd=staging,
                        capture_output=True, text=True)
    assert co.returncode != 0 and "did not match" in co.stderr
    # A commit on the run repository's copy of main, and a forced branch.
    _git(staging, "checkout", "-q", "main")
    (staging / "app.py").write_text("x = 2\n")
    _git(staging, "commit", "-qam", "worker on main")
    _git(staging, "branch", "-f", "develop", "HEAD")
    _git(staging, "checkout", "-q", f"leerie/runs/{RUN_ID}")
    assert {r: _git(repo, "rev-parse", r)
            for r in ("main", "develop")} == before


# --------------------------------------- every tree hangs off the run repo

def test_every_tree_of_a_run_hangs_off_the_run_repository(leerie, env):
    """Every kind of tree leerie makes — planning, staging, two subtasks
    and their merge, a judge checkout, a helper worktree, the acceptance
    writer's clone — and afterwards the user's repository has no leerie
    worktree, branch or ref: `git worktree list` there names only the
    user's own checkout."""
    repo, _state, st = env
    planning = asyncio.run(leerie._ensure_planning_worktree(st))
    staging = _setup_run(leerie, st)
    for sid in ("s1", "s2"):
        wt = _subtask(leerie, st, sid)
        (wt / f"{sid}.txt").write_text(sid)
        _git(wt, "add", "-A")
        _git(wt, "commit", "-qm", sid)
        merged = asyncio.run(leerie._integrate_merge(staging, st, sid))
        assert merged.returncode == 0, merged.stderr

    async def judge():
        async with leerie._judge_checkout(st, staging, "delivery-judge") as c:
            return _git(c, "rev-parse", "--path-format=absolute",
                        "--git-common-dir")
    judge_common = asyncio.run(judge())
    helper = leerie._helper_worktree_path(st, "confirm-base")
    add = leerie._worktree_add_detached(st, helper, st.data["base_sha"]
                                        if "base_sha" in st.data else "HEAD")
    assert add.returncode == 0, add.stderr
    writer = leerie._helper_worktree_path(st, "acceptance-1")
    assert leerie._acceptance_writer_checkout(
        st, writer, _git(repo, "rev-parse", "HEAD")) == "clone"

    run_repo = (st.run_dir / "repo").resolve()
    user_list = _git(repo, "worktree", "list", "--porcelain")
    assert [ln for ln in user_list.splitlines()
            if ln.startswith("worktree ")] == [f"worktree {repo.resolve()}"]
    assert [r for r in _refs(repo)
            if r.startswith(("refs/heads/leerie", "refs/leerie"))] == []
    run_list = _git(run_repo, "worktree", "list", "--porcelain")
    for tree in (planning, staging, helper):
        assert f"worktree {Path(tree).resolve()}" in run_list
    assert Path(judge_common).resolve() == run_repo
    for tree in (planning, staging, helper):
        assert (Path(_git(Path(tree), "rev-parse", "--path-format=absolute",
                          "--git-common-dir")).resolve() == run_repo)
    assert _git(run_repo, "rev-parse", f"leerie/runs/{RUN_ID}:s2.txt")


def test_setup_run_records_base_sha_through_phase_execute_code(leerie, env):
    """`base_sha` is the commit `setup-run.sh` cut the run branch from."""
    repo, _state, st = env
    _setup_run(leerie, st)
    cut = _git(st.run_dir / "repo", "rev-parse", f"leerie/runs/{RUN_ID}")
    assert cut == _git(repo, "rev-parse", "main")
    src = Path(leerie.__file__).read_text()
    i = src.index('    proc = await _run_script("setup-run.sh"')
    seg = src[i:i + 1500]
    assert 'st.data["base_sha"] = cut.stdout.strip()' in seg


# ------------------------------------------------ base_sha, not the name

def test_site_token_check_reads_the_base_not_the_moved_name(leerie, env):
    """A worker moving the run repository's `main` onto staging's HEAD
    empties `main..HEAD`; the check reads `base_sha` and still sees the
    added line. The moved name and `base_sha` disagree on purpose."""
    repo, _state, st = env
    staging = _setup_run(leerie, st)
    st.data["base_sha"] = _git(staging, "rev-parse", "HEAD")
    (staging / "app.py").write_text("x = 1\nfixture = 'acme-widget'\n")
    _git(staging, "commit", "-qam", "fix")
    _git(st.run_dir / "repo", "update-ref", "refs/heads/main",
         _git(staging, "rev-parse", "HEAD"))
    st.data["defect_scope"] = {"ground_truth": {"inline_examples": [
        {"literal": "acme-widget", "site_tokens": ["acme-widget"]}]}}
    hits = leerie._warn_site_token_leaks(st, st.run_dir)
    assert [h["token"] for h in hits] == ["acme-widget"]


def test_finalize_sh_counts_from_the_base_it_is_given(leerie, env):
    repo, _state, st = env
    staging = _setup_run(leerie, st)
    base = _git(staging, "rev-parse", "HEAD")
    (staging / "app.py").write_text("x = 3\n")
    _git(staging, "commit", "-qam", "work")
    _git(st.run_dir / "repo", "update-ref", "refs/heads/main",
         _git(staging, "rev-parse", "HEAD"))
    by_name = asyncio.run(leerie._run_script("finalize.sh", st.run_id,
                                             st=st))
    assert by_name.returncode == 1          # the moved name hides the work
    by_base = asyncio.run(leerie._run_script("finalize.sh", st.run_id, base,
                                             st=st))
    assert by_base.returncode == 0, by_base.stderr
    assert "1 commit(s) ahead" in by_base.stdout


# --------------------------------------------------------- resume refusal

def test_resume_of_a_pre_run_repo_run_is_refused(leerie, env, capsys):
    _repo, _state, st = env
    (st.run_dir / "run.json").write_text(json.dumps(
        {"run_id": RUN_ID, "branch": f"leerie/runs/{RUN_ID}"}))
    with pytest.raises(SystemExit):
        leerie._refuse_pre_run_repo_resume(st)
    err = capsys.readouterr().err
    assert f"leerie finalize {RUN_ID}" in err
    assert not (st.run_dir / "repo").exists()


def test_resume_of_a_new_run_is_not_refused(leerie, env):
    _repo, _state, st = env
    (st.run_dir / "run.json").write_text(json.dumps(
        {"run_id": RUN_ID, "branch": f"leerie/runs/{RUN_ID}"}))
    st.data["run_repo"] = "repo"
    leerie._refuse_pre_run_repo_resume(st)          # no exit
    st.data.pop("run_repo")
    (st.run_dir / "run.json").write_text(json.dumps({"run_id": RUN_ID}))
    leerie._refuse_pre_run_repo_resume(st)          # never got a branch


def test_refusal_sits_on_the_resume_path_before_the_run_repo(leerie):
    """Order, not presence: the refusal must run before `_run_phases`
    creates a run repository, or a pre-change run would get one and pass."""
    import inspect
    src = inspect.getsource(leerie._run_phases)
    refuse = src.index("_refuse_pre_run_repo_resume(st)")
    ensure = src.index("await _ensure_run_repo_or_die(st)")
    assert refuse < ensure
    assert src.index("if args.resume:") < refuse


# ------------------------------------------------------ deletions' guards

def test_full_purge_leaves_the_users_repository_alone(leerie, env):
    """The purge removes the run directory (the run's repository with it)
    and deletes nothing in the user's repository — not even a brought-back
    run branch of the same id."""
    repo, _state, st = env
    _setup_run(leerie, st)
    _git(repo, "branch", f"leerie/runs/{RUN_ID}")
    leerie._cleanup_on_abnormal_exit(st, full_purge=True)
    assert not st.run_dir.exists()
    assert _git(repo, "rev-parse", "--verify", f"leerie/runs/{RUN_ID}")


def test_finalize_cleanup_deletes_no_branch(leerie):
    """Finalize's cleanup removes worktrees only: no `--subtask-branches`
    or `--branches`, and no check that the run branch survived it."""
    import inspect
    src = inspect.getsource(leerie.phase_finalize)
    assert "--subtask-branches" not in src and "--branches" not in src
    assert "disappeared" not in src
    assert src.count('_run_script("cleanup.sh", "--run-id", st.run_id, '
                     'st=st)') == 2
