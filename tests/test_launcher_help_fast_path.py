"""`leerie --help` / `-h` is answered on the host, never through the container.

It used to be forwarded into the container, which ran the whole launcher run
path first (Colima probes, git fetch, preflight, image builds, and on
--runtime fly|ec2 a machine provision) and then let the post-run finalize
block treat the container's exit 0 as a finished job.
"""
from __future__ import annotations

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


@pytest.fixture
def forbidden_bins(tmp_path: Path) -> tuple[Path, Path]:
    """PATH-first stubs that record any call and fail it."""
    bindir = tmp_path / "bin"
    bindir.mkdir()
    calls = tmp_path / "calls.log"
    for name in FORBIDDEN:
        stub = bindir / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "{calls}"\nexit 97\n')
        stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bindir, calls


def _run(args: list[str], cwd: Path, bindir: Path, home: Path):
    env = dict(os.environ)
    env["PATH"] = f"{bindir}{os.pathsep}{env['PATH']}"
    env["HOME"] = str(home)
    env["COLUMNS"] = "80"
    for k in ("LEERIE_STATE_DIR", "LEERIE_RUNTIME"):
        env.pop(k, None)
    return subprocess.run(
        [str(LAUNCHER), *args], cwd=cwd, env=env,
        capture_output=True, text=True, timeout=60, check=False,
    )


def _expected_help() -> str:
    """The orchestrator's own --help, rendered by the same `python3` the
    launcher resolves from PATH (argparse headings differ across versions)."""
    env = dict(os.environ, COLUMNS="80")
    python3 = shutil.which("python3")
    assert python3, "python3 not found on PATH"
    return subprocess.run(
        [python3, "-B", str(LEERIE_PY), "--help"],
        capture_output=True, text=True, env=env, check=True,
    ).stdout


@pytest.mark.parametrize("args", [
    ["--help"],
    ["-h"],
    ["--help", "--runtime", "fly"],
    ["--runtime", "ec2", "-h"],
    ["some task", "--help"],
])
def test_help_runs_on_host_without_container(args, forbidden_bins, tmp_path):
    bindir, calls = forbidden_bins
    nongit = tmp_path / "not-a-repo"
    nongit.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    r = _run(args, nongit, bindir, home)
    assert r.returncode == 0, r.stderr
    # Substance, not structure: the orchestrator's own rendering, so a new
    # flag shows up here with no extra step.
    assert r.stdout == _expected_help()
    assert "launcher verbs" in r.stdout
    assert not calls.exists(), calls.read_text()
    assert "finalize" not in r.stderr
    assert not any(home.iterdir()), "help must not create state under $HOME"


def test_help_after_double_dash_is_not_help(forbidden_bins, tmp_path):
    """`--` ends option scanning: a task that is literally `--help` is not a request for help."""
    bindir, _ = forbidden_bins
    nongit = tmp_path / "not-a-repo"
    nongit.mkdir()
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["--", "--help"], nongit, bindir, home)
    assert "launcher verbs" not in r.stdout
    # The normal flow rejects a non-repo directory, so this cannot pass by
    # the launcher having silently done nothing.
    assert r.returncode != 0


def test_prune_keeps_its_own_help(forbidden_bins, tmp_path):
    bindir, calls = forbidden_bins
    home = tmp_path / "home"
    home.mkdir()
    r = _run(["prune", "--help"], tmp_path, bindir, home)
    assert r.returncode == 0, r.stderr
    assert "Usage: leerie prune" in r.stderr
    assert "launcher verbs" not in r.stdout


def test_missing_python3_fails_with_one_line_not_the_container(tmp_path):
    """No python3: say so and exit 1. A PATH holding no container tools makes
    any fall-through to the run path fail differently (stderr would not be
    this single line)."""
    bare = tmp_path / "bare-bin"
    bare.mkdir()
    # Only what the launcher touches before the help scan.
    for tool in ("bash", "env", "dirname", "awk", "git"):
        found = shutil.which(tool)
        assert found, tool
        (bare / tool).symlink_to(found)
    home = tmp_path / "home"
    home.mkdir()
    r = subprocess.run(
        [str(bare / "bash"), str(LAUNCHER), "--help"], cwd=tmp_path,
        env={"PATH": str(bare), "HOME": str(home)},
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert r.returncode == 1, r.stderr
    assert r.stderr.strip() == (
        "leerie: python3 not found on PATH (needed to render --help)")
    assert r.stdout == ""
