"""A run's push choice is recorded once, at its start, on every runtime.

DESIGN §6 *One push choice per run, on every runtime*; IMPLEMENTATION §2½
*Run choices on resume*.

The defect this pins: on Fly and EC2 the in-machine orchestrator was always
started with `--no-push` as a "mechanism flag", and the user's intent
travelled beside it as a per-invocation `--host-no-push` that each launcher
invocation re-derived. Every host-side reader then guessed which meaning
`run.json.no_push` held and stripped a `true`. Measured on the real code
with a stubbed `flyctl`, before the fix: a fresh `leerie --runtime fly
--no-push` run was pushed by the auto-finalize (the fetch stripped the
`true`), and a plain `leerie resume <id>` of it passed `--host-no-push false`
and was pushed.

Every step here is the real code: the launcher's NO_PUSH resolution and its
hand-off of the choice to the orchestrator (sliced from `leerie`), the
in-machine launch argv (`_render_launch_suffix` from
`scripts/remote/seed-common.sh`, executed with `Popen` captured), the real
`main()` fresh and on `resume`, the real `fetch_branch` over a stubbed
`flyctl`, the real `host_finalize`, and the real launcher `finalize` verb.
No cloud CLI is called.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ, run_bash
from tests.test_fetch_branch_sh import FETCH_SH, _flyctl_stub, _make_git_repo
from tests.test_run_choices_on_resume import (  # noqa: F401  (fixture)
    RUN_ID, _finished, _fresh, _host_finalize, _launcher_no_push_block,
    _resume, run_env)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
SEED_COMMON_SH = REPO_ROOT / "scripts" / "remote" / "seed-common.sh"


# --------------------------------------------------------------------------
# The launcher: resolve NO_PUSH, then hand it to the orchestrator
# --------------------------------------------------------------------------

def _slice(text: str, start: str, end: str) -> str:
    i = text.index(start)
    j = text.index(end, i) + len(end)
    return text[i:j]


def _launcher_blocks() -> str:
    text = LAUNCHER.read_text()
    resolve = _launcher_no_push_block()
    handoff = _slice(text, "# The run's push choice, recorded once at its "
                     "start", "\nfi\n")
    return resolve + handoff


def _orchestrator_args(tmp_path: Path, args: list[str], *, is_resume: bool,
                       env_no_push: str = "",
                       host_run_json: dict | None = None) -> list[str]:
    """What the launcher hands the orchestrator: the command line it
    forwards (REWRITTEN_ARGS) plus the push hand-off."""
    host = tmp_path / "host-state"
    if host_run_json is not None:
        d = host / "runs" / RUN_ID
        d.mkdir(parents=True, exist_ok=True)
        (d / "run.json").write_text(json.dumps(host_run_json))
    user_repo = tmp_path / "launcher-repo"
    user_repo.mkdir(exist_ok=True)
    script = ("set -euo pipefail\n"
              "remote_log() { echo \"$*\" >&2; }\n"
              f"USER_REPO={user_repo}\n"
              f"LEERIE_STATE_HOST_DIR={host}\n"
              f"IS_RESUME={'true' if is_resume else 'false'}\n"
              f"LEERIE_RUN_ID={RUN_ID if is_resume else ''}\n"
              "RUNTIME=fly\n"
              f"LEERIE_REPO={LAUNCHER.parent}\n"
              'REWRITTEN_ARGS=("$@")\n'
              + _launcher_blocks()
              + 'printf "%s\\0" ${REWRITTEN_ARGS[@]+"${REWRITTEN_ARGS[@]}"}\n')
    env = {"PATH": "/usr/bin:/bin"}
    if env_no_push:
        env["LEERIE_NO_PUSH"] = env_no_push
    r = subprocess.run(["bash", "-c", script, "--", *args], env=env,
                       capture_output=True, text=True, check=False)
    assert r.returncode == 0, r.stderr
    return [a for a in r.stdout.split("\0") if a]


class _Spawned(Exception):
    pass


def _machine_orchestrator_argv(orch_args: list[str], tmp_path: Path
                               ) -> list[str]:
    """The orchestrator argv the in-machine launch script spawns, from the
    real `_render_launch_suffix` (shared by Fly and EC2)."""
    r = subprocess.run(
        ["bash", "-c", f". {SEED_COMMON_SH}; _render_launch_suffix"],
        capture_output=True, text=True, check=True)
    got: list[list[str]] = []

    class _Subprocess:
        DEVNULL = subprocess.DEVNULL

        @staticmethod
        def Popen(argv, **_kw):
            got.append(list(argv))
            raise _Spawned()

    class _Pw:
        pw_gid = os.getgid()

    import sys as _sys
    ns = {"os": os, "sys": _sys, "subprocess": _Subprocess,
          "child_env": {}, "orch_args": orch_args, "leerie_pw": _Pw(),
          "log_path": str(tmp_path / "orchestrator.log"),
          "pid_path": str(tmp_path / "orchestrator.pid")}
    with pytest.raises(_Spawned):
        exec(compile(r.stdout, "launch-suffix", "exec"), ns)
    (argv,) = got
    assert argv[:2] == ["/usr/bin/python3",
                        "/opt/leerie-image/orchestrator/leerie.py"]
    return argv[2:]


def _flags(argv: list[str]) -> list[str]:
    """The orchestrator flags, without the task / verb / run id the test's
    own `_fresh` / `_resume` supply."""
    return [a for a in argv if a.startswith("--")]


class TestTheLauncherHandsTheChoiceToAFreshRun:
    def test_a_fresh_no_push_run_gets_no_push(self, tmp_path):
        assert "--no-push" in _orchestrator_args(
            tmp_path, ["a task", "--no-push"], is_resume=False)

    def test_an_env_only_opt_out_reaches_the_orchestrator(self, tmp_path):
        """Neither the container nor a remote machine sees LEERIE_NO_PUSH:
        the launcher's resolution is the only one that does."""
        assert "--no-push" in _orchestrator_args(
            tmp_path, ["a task"], is_resume=False, env_no_push="1")

    def test_a_fresh_run_that_pushes_gets_nothing(self, tmp_path):
        assert "--no-push" not in _orchestrator_args(
            tmp_path, ["a task"], is_resume=False)

    def test_a_resume_adds_nothing_the_command_line_did_not_say(
            self, tmp_path):
        """Inputs disagree: the env says not to push, the host record says
        not to push, and neither may be handed to the orchestrator as if
        the resume's command line had said it — the orchestrator continues
        its own record."""
        assert _orchestrator_args(
            tmp_path, ["resume", RUN_ID], is_resume=True, env_no_push="1",
            host_run_json={"no_push": True}) == ["resume", RUN_ID]

    def test_a_resumes_own_push_flag_is_forwarded(self, tmp_path):
        assert "--push" in _orchestrator_args(
            tmp_path, ["resume", RUN_ID, "--push"], is_resume=True,
            host_run_json={"no_push": True})

    def test_the_machine_gets_exactly_the_launchers_flags(self, tmp_path):
        """The in-machine launch adds no push flag of its own."""
        for args in (["a task"], ["a task", "--no-push"]):
            orch = _orchestrator_args(tmp_path, args, is_resume=False)
            assert _machine_orchestrator_argv(orch, tmp_path) == orch


# --------------------------------------------------------------------------
# End to end on Fly: launcher → machine orchestrator → fetch → host_finalize
# --------------------------------------------------------------------------

def _fetch_home(tmp_path: Path, run_dir: Path) -> Path:
    """The real `fetch_branch` over a stubbed `flyctl`, with the run branch
    present on the "machine": returns the host-side run directory."""
    branch_repo = _make_git_repo(tmp_path, "machine-branches")
    subprocess.run(["git", "-C", str(branch_repo), "branch",
                    f"leerie/runs/{RUN_ID}"], check=True, capture_output=True)
    bindir = tmp_path / "flybin"
    bindir.mkdir(exist_ok=True)
    _flyctl_stub(bindir, run_dir.parent, branch_repo)
    host = tmp_path / "fetched-host-state"
    r = run_bash(f"source {FETCH_SH}; fetch_branch", env={
        "LEERIE_MACHINE_ID": RUN_ID, "LEERIE_FLY_APP": "app",
        "USER_REPO": str(branch_repo), "LEERIE_STATE_HOST_DIR": str(host),
        "PATH": f"{bindir}:/usr/bin:/bin:/usr/local/bin"})
    assert r.returncode == 0, r.stderr
    return host / "runs" / RUN_ID


def _pushed(git_log: str) -> bool:
    return bool(re.search(r"(^|\s)push(\s|$)", git_log, re.M))


def _machine_flags(tmp_path, args, **kw) -> list[str]:
    orch = _orchestrator_args(tmp_path, args, **kw)
    return _flags(_machine_orchestrator_argv(orch, tmp_path))


@pytest.mark.skipif(not HAS_JQ, reason="host-only script: needs real `jq`")
class TestAFlyRunKeepsItsPushChoice:
    def _start(self, leerie, monkeypatch, tmp_path, args, env_no_push=""):
        _fresh(leerie, monkeypatch, _machine_flags(
            tmp_path, args, is_resume=False, env_no_push=env_no_push))

    def _finalize(self, tmp_path, repo, run_dir):
        _finished(run_dir)
        host_run = _fetch_home(tmp_path, run_dir)
        return _host_finalize(tmp_path, host_run, repo)

    def test_a_fresh_no_push_run_is_not_pushed(
            self, leerie, monkeypatch, run_env, tmp_path):
        repo, run_dir = run_env
        self._start(leerie, monkeypatch, tmp_path, ["a task", "--no-push"])

        r, git_log = self._finalize(tmp_path, repo, run_dir)

        assert "no_push=true" in r.stderr, r.stderr
        assert not _pushed(git_log), git_log

    def test_an_env_only_opt_out_is_not_pushed(
            self, leerie, monkeypatch, run_env, tmp_path):
        repo, run_dir = run_env
        self._start(leerie, monkeypatch, tmp_path, ["a task"],
                    env_no_push="1")

        r, git_log = self._finalize(tmp_path, repo, run_dir)

        assert not _pushed(git_log), (r.stderr, git_log)

    def test_a_plain_resume_of_a_no_push_run_is_not_pushed(
            self, leerie, monkeypatch, run_env, tmp_path):
        """The host holds no record of a paused Fly run's choice (only the
        pause sidecar), and the resume's env says push."""
        repo, run_dir = run_env
        self._start(leerie, monkeypatch, tmp_path, ["a task", "--no-push"])
        monkeypatch.setenv("LEERIE_NO_PUSH", "0")
        _resume(leerie, monkeypatch, _machine_flags(
            tmp_path, ["resume", RUN_ID], is_resume=True,
            host_run_json={"paused_at": "2026-11-01T00:00:00+00:00",
                           "fly_machine_id": RUN_ID}))

        r, git_log = self._finalize(tmp_path, repo, run_dir)

        assert "no_push=true" in r.stderr, r.stderr
        assert not _pushed(git_log), git_log

    def test_control_resume_with_push_is_pushed(
            self, leerie, monkeypatch, run_env, tmp_path):
        """Anti-vacuity: the same chain observes a push when one is due."""
        repo, run_dir = run_env
        self._start(leerie, monkeypatch, tmp_path, ["a task", "--no-push"])
        _resume(leerie, monkeypatch, _machine_flags(
            tmp_path, ["resume", RUN_ID, "--push"], is_resume=True))

        r, git_log = self._finalize(tmp_path, repo, run_dir)

        assert _pushed(git_log), (r.stderr, git_log)

    def test_control_a_fresh_run_that_pushes_is_pushed(
            self, leerie, monkeypatch, run_env, tmp_path):
        """A machine that still forced `--no-push` would silence every Fly
        push; this is the case that would show it."""
        repo, run_dir = run_env
        self._start(leerie, monkeypatch, tmp_path, ["a task"])

        r, git_log = self._finalize(tmp_path, repo, run_dir)

        assert _pushed(git_log), (r.stderr, git_log)


# --------------------------------------------------------------------------
# The launcher's `finalize` verb
# --------------------------------------------------------------------------

def _synced_run(tmp_path: Path, *, no_push: bool, sidecar: str | None):
    user_repo = _make_git_repo(tmp_path, "user-repo")
    branch = f"leerie/runs/{RUN_ID}"
    subprocess.run(["git", "-C", str(user_repo), "branch", branch],
                   check=True, capture_output=True)
    state = tmp_path / "leerie-state"
    run_dir = state / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": RUN_ID, "branch": branch, "working_branch": "main",
        "finished_at": "2026-11-01T00:00:00+00:00", "no_push": no_push}))
    (run_dir / "state.json").write_text(json.dumps({
        "finished_at": "2026-11-01T00:00:00+00:00"}))
    if sidecar:
        (run_dir / sidecar).write_text(json.dumps({
            "fly_machine_id": RUN_ID, "ec2_instance_id": RUN_ID,
            "run_id": RUN_ID}))
    return user_repo, run_dir, state


def _launcher_finalize(user_repo: Path, state: Path, *extra: str):
    return subprocess.run(
        ["bash", str(LAUNCHER), "finalize", RUN_ID, *extra],
        cwd=str(user_repo), capture_output=True, text=True, check=False,
        env={**os.environ, "LEERIE_STATE_DIR": str(state),
             "LEERIE_FLY_APP": "app"})


@pytest.mark.skipif(not HAS_JQ, reason="host-only script: needs real `jq`")
class TestTheFinalizeVerbReadsTheRecord:
    @pytest.mark.parametrize("sidecar", ["fly-machine.json",
                                         "ec2-instance.json", None])
    def test_a_recorded_no_push_is_not_pushed(self, tmp_path, sidecar):
        """It used to strip a recorded true on every remote run whose
        sidecar carried no `host_no_push` (every EC2 run)."""
        user_repo, run_dir, state = _synced_run(tmp_path, no_push=True,
                                                sidecar=sidecar)

        r = _launcher_finalize(user_repo, state)

        assert r.returncode == 0, r.stderr
        assert "no_push=true; skipping push" in r.stderr, r.stderr
        after = json.loads((run_dir / "run.json").read_text())
        assert after["no_push"] is True
        assert after.get("pushed_at") is None and after.get(
            "push_error") is None, after

    def test_no_push_on_the_verb_skips_a_run_recorded_to_push(
            self, tmp_path):
        """Inputs disagree: the record says push, the verb says not to."""
        user_repo, run_dir, state = _synced_run(
            tmp_path, no_push=False, sidecar="fly-machine.json")

        r = _launcher_finalize(user_repo, state, "--no-push")

        assert r.returncode == 0, r.stderr
        assert "--no-push given; skipping push" in r.stderr, r.stderr
        after = json.loads((run_dir / "run.json").read_text())
        assert after.get("pushed_at") is None and after.get(
            "push_error") is None, after

    def test_control_a_run_recorded_to_push_reaches_the_push(self, tmp_path):
        """Anti-vacuity: without `--no-push` the same run reaches the push
        (which fails here: the repository has no `origin`)."""
        user_repo, run_dir, state = _synced_run(
            tmp_path, no_push=False, sidecar="fly-machine.json")

        r = _launcher_finalize(user_repo, state)

        after = json.loads((run_dir / "run.json").read_text())
        assert "skipping push" not in r.stderr, r.stderr
        assert after.get("push_error") or after.get("pushed_at"), (
            r.stderr, after)
