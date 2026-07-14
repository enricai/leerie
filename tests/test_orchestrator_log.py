"""Tests for orchestrator/leerie.py's log() prefix helper.

Asserts the ISO-8601 [leerie] [<repo>] <msg> prefix shape — the same
user-visible contract that tests/test_remote_log.py guards for the
host-side bash remote_log(). Both halves of the shell ↔ python boundary
have to render the same `<repo>` tag, otherwise the prefix flips
mid-stream when the launcher starts tailing the remote orchestrator
(observed: bash prints `[stackpulse]`, python prints `[work]` because
cwd inside the Fly machine is /work).

The critical property locked in here: USER_REPO wins over cwd, so
injecting USER_REPO into the orchestrator's env keeps the prefix stable.

Scope note: these tests stub USER_REPO into the subprocess env directly,
so they pin log()'s *precedence* and nothing about whether a launcher
actually delivers the var across the container boundary. That seam is
where the bug lives, and it is guarded separately — for both runtimes —
by test_launcher_env_forwarding.py::test_user_repo_delivered_to_container
(local `-e`) and ::test_fly_path_also_delivers_user_repo (Fly child_env).
An earlier version of this docstring described the injection as done,
which was true of Fly and false of local for months.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
ORCH = REPO_ROOT / "orchestrator" / "leerie.py"

# ISO-8601 with second precision and a local-tz offset: e.g.
# `2026-06-03T05:07:10-05:00` or `2026-06-03T10:07:10+00:00`. The
# offset is always present because log() goes through .astimezone(),
# which never produces a naive datetime.
PREFIX_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{2}:\d{2} "
    r"\[leerie\] \[(?P<repo>[^\]]+)\] (?P<body>.*)$"
)


def _invoke_log(msg: str, user_repo: str | None, cwd: Path) -> str:
    # Import log() out of the orchestrator module without executing main().
    # The module guards main() behind `if __name__ == "__main__":`, so a
    # plain importlib load is safe.
    script = (
        "import importlib.util, sys;"
        f"spec = importlib.util.spec_from_file_location('leerie', {str(ORCH)!r});"
        "mod = importlib.util.module_from_spec(spec);"
        "spec.loader.exec_module(mod);"
        f"mod.log({msg!r})"
    )
    env = {"PATH": "/usr/bin:/bin"}
    if user_repo is not None:
        env["USER_REPO"] = user_repo
    r = subprocess.run(
        [sys.executable, "-c", script],
        env=env,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=True,
    )
    return r.stdout.rstrip("\n")


def test_log_uses_user_repo_basename(tmp_path):
    # Simulate the remote-on-Fly setup: cwd is /work-like, USER_REPO
    # carries the host repo's basename. The prefix must reflect
    # USER_REPO, not the cwd basename.
    work = tmp_path / "work"
    work.mkdir()
    line = _invoke_log("hello", user_repo="stackpulse", cwd=work)
    m = PREFIX_RE.match(line)
    assert m, f"no prefix match: {line!r}"
    assert m.group("repo") == "stackpulse", (
        f"expected [stackpulse], got [{m.group('repo')}] — "
        "USER_REPO is not winning over cwd"
    )
    assert m.group("body") == "hello"


def test_log_uses_user_repo_basename_when_value_is_a_path(tmp_path):
    # Host-side invocations set USER_REPO to a full path. log() takes
    # Path(...).name, so either form (bare basename or full path) must
    # render the same tag.
    work = tmp_path / "work"
    work.mkdir()
    line = _invoke_log(
        "hi", user_repo="/Users/andres/src/enric/stackpulse", cwd=work
    )
    m = PREFIX_RE.match(line)
    assert m and m.group("repo") == "stackpulse"


def test_log_falls_back_to_cwd_basename_when_user_repo_unset(tmp_path):
    # Defense for the bootstrap path: if USER_REPO is somehow missing,
    # the cwd basename is the next-best signal. Documents the fallback.
    repo = tmp_path / "myrepo"
    repo.mkdir()
    line = _invoke_log("hi", user_repo=None, cwd=repo)
    m = PREFIX_RE.match(line)
    assert m and m.group("repo") == "myrepo"
