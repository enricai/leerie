"""`leerie list` and `leerie --report` run on the host, never through the container.

Both only read <state-root>/runs/*. Through the container they also ran the
push preflights (origin / jq / pre-push / base freshness), image builds, and
then the post-run finalize block, which reported "finalize: no unpushed
finished run" for a command that never ran a job.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"

# Anything the launcher would shell out to on the run path.
FORBIDDEN = ("nerdctl", "docker", "colima", "flyctl", "aws")
RUN_ID = "run-aaa111"


@pytest.fixture
def world(tmp_path: Path) -> dict:
    """A tmp $HOME with one finished run, a project dir, and failing stubs."""
    home = tmp_path / "home"
    project = tmp_path / "proj"
    project.mkdir()
    state = home / ".leerie" / "proj"
    run = state / "runs" / RUN_ID
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps({
        "started_at": "2026-10-01T00:00:00+00:00",
        "finished_at": "2026-10-01T00:10:00+00:00",
        "telemetry": {"calls": 3, "cost_usd": 1.5,
                      "input_tokens": 10, "output_tokens": 5},
    }))
    (run / "run.json").write_text(json.dumps(
        {"branch": "b", "working_branch": "w",
         "finished_at": "2026-10-01T00:10:00+00:00"}))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    for name in FORBIDDEN:
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit 97\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return {"home": home, "project": project, "state": state,
            "bindir": bindir, "calls": calls}


def _launch_env(w: dict) -> dict:
    env = dict(os.environ)
    env["PATH"] = f"{w['bindir']}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(w["home"])
    env["COLUMNS"] = "80"
    for k in ("LEERIE_STATE_DIR", "LEERIE_RUNTIME"):
        env.pop(k, None)
    return env


def _launch(w: dict, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([str(LAUNCHER), *args], cwd=w["project"],
                          env=_launch_env(w), capture_output=True, text=True,
                          timeout=60, check=False)


def _direct(w: dict, args: list[str]) -> subprocess.CompletedProcess:
    """The orchestrator's own answer for the same argv and state root."""
    env = _launch_env(w)
    env["LEERIE_STATE_DIR"] = str(w["state"])
    return subprocess.run([shutil.which("python3"), "-B", str(LEERIE_PY), *args],
                          cwd=w["project"], env=env, capture_output=True,
                          text=True, timeout=60, check=False)


@pytest.mark.parametrize("args", [
    ["list"],
    ["list", "--status", "done"],
    ["list", "--runtime", "local"],
    ["list", "--runtime=local"],
    ["--report"],
    ["--report", RUN_ID],
    ["--report=" + RUN_ID],
    ["--run-id", RUN_ID, "--report"],
])
def test_runs_on_host_and_matches_the_orchestrator(args, world):
    r = _launch(world, args)
    assert r.returncode == 0, r.stderr
    expected = _direct(world, args)
    assert expected.returncode == 0, expected.stderr
    # Substance, not structure: the same bytes the orchestrator prints for
    # that state root, and they actually name the run.
    assert r.stdout == expected.stdout
    assert RUN_ID in r.stdout or "status=done" in r.stdout
    assert not world["calls"].exists(), world["calls"].read_text()
    assert "finalize" not in r.stderr


def test_list_with_a_filter_that_matches_nothing_still_stays_on_host(world):
    r = _launch(world, ["list", "--status", "paused"])
    assert r.returncode == 0, r.stderr
    assert RUN_ID not in r.stdout
    assert not world["calls"].exists()


def test_launcher_only_state_dir_flag_is_honored_and_not_leaked(world, tmp_path):
    """--state-dir is launcher-only; the strict argparse would reject it."""
    elsewhere = tmp_path / "elsewhere"
    run = elsewhere / "runs" / "run-bbb222"
    run.mkdir(parents=True)
    (run / "state.json").write_text(json.dumps(
        {"started_at": "2026-10-02T00:00:00+00:00"}))
    (run / "run.json").write_text(json.dumps({"branch": "b", "working_branch": "w"}))
    for form in (["--state-dir", str(elsewhere)], [f"--state-dir={elsewhere}"]):
        r = _launch(world, ["list", *form])
        assert r.returncode == 0, r.stderr
        assert "run-bbb222" in r.stdout
        assert RUN_ID not in r.stdout
        assert not world["calls"].exists()


def test_no_python3_falls_back_instead_of_failing(world, tmp_path):
    """Without python3 the allowlist helper returns and the old path runs."""
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    for tool in ("bash", "env", "dirname", "awk", "git", "mkdir", "head",
                 "cat", "tr", "sed", "grep", "find", "touch", "id", "uname"):
        found = shutil.which(tool)
        if found:
            (bare / tool).symlink_to(found)
    for name in FORBIDDEN:
        (bare / name).symlink_to(world["bindir"] / name)
    r = subprocess.run(
        [str(bare / "bash"), str(LAUNCHER), "list"], cwd=world["project"],
        env={"PATH": str(bare), "HOME": str(world["home"])},
        capture_output=True, text=True, timeout=60, check=False)
    assert RUN_ID not in r.stdout
    assert r.returncode != 0 or world["calls"].exists()


def test_unlisted_flag_does_not_take_the_host_path(world):
    """The allowlist is closed: an unknown flag keeps the container path."""
    r = _launch(world, ["list", "--verbosity", "debug"])
    assert RUN_ID not in r.stdout
    assert r.returncode != 0 or world["calls"].exists()
