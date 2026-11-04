"""One toolchain install at a time on a host, against mise's own completeness
record (DESIGN §6 *One toolchain install at a time on a host, against mise's
own completeness record*).

Three barnacle runs provisioned together over one shared `MISE_DATA_DIR`
(2026-11-04 03:11). One trusted another's still-running install of node
22.19.0 ("mise all tools are installed"). That install then failed and was
removed, leaving `installs/node/22.19 -> ./22.19.0` dangling. mise's install
lock and its `incomplete` marker live in its cache directory. The cache was
each confined command's private `XDG_CACHE_HOME`, so neither the lock nor
the marker was shared.

The source-level tests drive the real `_run_mise_install` and
`_measurement_grants`. The real-container test runs only when
LEERIE_TEST_CONTAINER_IMAGE names a leerie image (`nerdctl` on PATH,
CONTAINERD_NAMESPACE naming its namespace) and the network can reach
nodejs.org. It runs two leerie processes on one data directory: the first
is frozen mid-extract, then killed. The second must neither trust the
partial install nor leave a dangling alias.
"""
from __future__ import annotations

import fcntl
import json
import os
import shutil
import subprocess
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import REAL_HOME, XDG_BASE_DIRS
from tests.test_corepack_shim_dir import _drive
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _lock_path(tmp_path: Path) -> Path:
    # `_drive`'s state root, with no host directory in the environment.
    return tmp_path / ".leerie" / ".host" / "mise-install.lock"


def _held_elsewhere(path: Path) -> bool:
    fd = os.open(str(path), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return True
    finally:
        os.close(fd)
    return False


# ------------------------------------------------------------ the host lock

def test_the_install_and_the_enable_run_under_the_host_lock(
        leerie, tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_HOST_DIR", raising=False)
    held: list[tuple[str, bool]] = []

    async def enable(*a, **k):
        held.append(("enable", _held_elsewhere(_lock_path(tmp_path))))

    monkeypatch.setattr(leerie, "_enable_corepack_shims", enable)
    _drive(leerie, tmp_path, monkeypatch, data=tmp_path / "mise-data",
           on_install=lambda: held.append(
               ("mise install", _held_elsewhere(_lock_path(tmp_path)))))
    assert held == [("mise install", True), ("enable", True)]
    assert not _held_elsewhere(_lock_path(tmp_path))     # released after


def test_an_install_waits_while_another_run_holds_the_lock(
        leerie, tmp_path, monkeypatch):
    monkeypatch.delenv("LEERIE_HOST_DIR", raising=False)
    monkeypatch.setattr(leerie, "MISE_INSTALL_LOCK_POLL_SEC", 0.02)
    lock = _lock_path(tmp_path)
    lock.parent.mkdir(parents=True)
    other = os.open(str(lock), os.O_RDONLY | os.O_CREAT, 0o644)
    fcntl.flock(other, fcntl.LOCK_EX)
    released: list[float] = []

    def release():
        time.sleep(0.5)
        released.append(time.monotonic())
        os.close(other)

    started: list[float] = []
    t = threading.Thread(target=release)
    t.start()
    _drive(leerie, tmp_path, monkeypatch, data=tmp_path / "mise-data",
           on_install=lambda: started.append(time.monotonic()))
    t.join()
    assert released and started and started[0] >= released[0]


def test_the_lock_is_in_the_host_directory_when_there_is_one(
        leerie, tmp_path, monkeypatch):
    host = tmp_path / "leerie-host"
    host.mkdir()
    monkeypatch.setenv("LEERIE_HOST_DIR", str(host))
    seen: list[bool] = []
    data = tmp_path / "mise-data"
    _drive(leerie, tmp_path, monkeypatch, data=data, on_install=lambda: seen.append(
        _held_elsewhere(host / "mise-install.lock")))
    assert seen == [True]
    assert not (tmp_path / ".leerie" / ".host" / "mise-install.lock").exists()


# ----------------------------------------------------- mise's cache, shared

def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


@pytest.mark.parametrize("kind,shared", [
    ("TOOLCHAIN_KIND", True), ("SETUP_HOOK_KIND", True),
    ("INSTALL_KIND", False), ("MEASURE_KIND", False)])
def test_the_kinds_that_write_mise_data_share_its_cache(
        leerie, run, tmp_path, monkeypatch, kind, shared):
    data = tmp_path / "mise-data"
    monkeypatch.setenv("MISE_DATA_DIR", str(data))
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    g = leerie._measurement_grants(_st(run), view, getattr(leerie, kind))
    env = dict(g.env)
    if not shared:
        assert "MISE_CACHE_DIR" not in env
        return
    cache = Path(env["MISE_CACHE_DIR"])
    assert cache == data.resolve() / "cache"
    # Shared with every run, unlike the command's own temp and cache.
    assert not cache.is_relative_to(env["TMPDIR"])
    assert any(cache.is_relative_to(p) for p in g.rw), g.rw


# ------------------------------------------------------ the real container

_DRIVER = r'''
import asyncio, importlib.util, os, subprocess, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
leerie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leerie)
RUN = sys.argv[3]


class _St:
    def __init__(s, user, run_dir):
        s.repo_root, s.run_id, s.run_dir = user, RUN, run_dir
        s.data = {"working_branch": "main", "verbosity": "quiet"}

    def save(s):
        pass


base = Path(sys.argv[2]) / RUN
user = base / "user"
user.mkdir(parents=True)


def g(*a, cwd=user):
    subprocess.run(["git", *a], cwd=cwd, check=True, capture_output=True)


g("init", "-q", "-b", "main")
(user / ".nvmrc").write_text("22.19.0\n")
g("add", "-A")
g("-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "pin")
state_root = base / "state"
run_dir = state_root / "runs" / RUN
run_dir.mkdir(parents=True)
st = _St(user, run_dir)
repo = leerie._ensure_run_repo(st)
wt = run_dir / "worktrees" / f"{RUN}-provision"
g("branch", "b", "main", cwd=repo)
g("worktree", "add", "-q", str(wt), "b", cwd=repo)
leerie._require_confinement(run_dir, state_root, user)
print("READY", flush=True)
asyncio.run(leerie._run_mise_install(wt, run_dir / "logs", st))
rc, tail = asyncio.run(leerie._run_streaming(
    ["bash", "-c", 'b=$MISE_DATA_DIR/installs/node/22.19.0/bin; '
     'echo "node=$($b/node -v)"; echo "npm=$($b/npm -v)"; '
     'echo "pnpm=$(test -L $b/pnpm && echo linked)"'],
    grants=leerie._measurement_grants(st, wt, leerie.MEASURE_KIND),
    cwd=str(wt), timeout=300, verbosity="quiet", tail_lines=10))
print("MEASURE " + " ".join(tail.split()), flush=True)
'''

# Run as `leerie` in the container: A, frozen once its install has begun
# extracting, then B, then A killed; B's outcome and the data directory
# afterwards. Only processes this script started are signalled, by PID.
_SCRIPT = r'''
set -u
D=/home/leerie/.local/share/mise
run() { /usr/bin/python3 /out/driver.py /src/orchestrator/leerie.py /leerie-state "$1"; }
run runa > /leerie-state/a.log 2>&1 & A=$!
until [ -e $D/installs/node/22.19.0/lib ]; do
  kill -0 $A 2>/dev/null || { echo "A-ENDED-EARLY"; cat /leerie-state/a.log; exit 1; }
  sleep 0.05
done
# Freeze A's mise mid-extract: its processes are the `mise` ones under A.
mise_pids() {
  for p in /proc/[0-9]*; do
    c=$(tr '\0' ' ' < $p/cmdline 2>/dev/null) || continue
    case "$c" in "mise install"*) echo ${p#/proc/};; esac
  done
}
M=$(mise_pids)
[ -n "$M" ] || { echo "NO-MISE-PID"; exit 1; }
kill -STOP $M
run runb > /leerie-state/b.log 2>&1 & B=$!
sleep 8
kill -KILL $M; wait $A; echo "A-EXIT $?"
wait $B; echo "B-EXIT $?"
grep -h '^MEASURE' /leerie-state/b.log
echo "B-LOG $(grep -c 'all tools are installed' /leerie-state/runb/state/runs/runb/logs/provision.log)"
echo "DANGLING $(find $D/installs -xtype l | wc -l)"
echo "MARKER $(find $D/cache -name incomplete | wc -l)"
'''


def _users_env() -> dict[str, str]:
    env = {**os.environ, "HOME": REAL_HOME}
    for var in XDG_BASE_DIRS:
        real = os.environ.get(f"PYTEST_LEERIE_REAL_{var}", "")
        if real:
            env[var] = real
        else:
            env.pop(var, None)
    return env


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_two_runs_one_failing_mid_install(tmp_path):
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    (tmp_path / "driver.py").write_text(_DRIVER)
    (tmp_path / "script.sh").write_text(_SCRIPT)
    for p in (tmp_path, *tmp_path.iterdir()):          # read as `leerie`
        p.chmod(p.stat().st_mode | 0o055)
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", "apparmor=leerie-container"]
    tmpfs = "rw,exec,size=2g,uid=1000,gid=1000"
    r = subprocess.run(
        ["nerdctl", "run", "--rm", *sec, "--user", "leerie",
         "--entrypoint", "bash", "-e", "HOME=/home/leerie",
         "-e", "LEERIE_HOST_DIR=/leerie-host",
         "-v", f"{REPO_ROOT}:/src:ro", "-v", f"{tmp_path}:/out:ro",
         "--tmpfs", f"/leerie-state:{tmpfs}", "--tmpfs", f"/leerie-host:{tmpfs}",
         "--tmpfs", f"/home/leerie/.local/share/mise:{tmpfs}",
         image, "/out/script.sh"],
        capture_output=True, text=True, timeout=1500, env=_users_env())
    out = dict(ln.split(" ", 1) for ln in r.stdout.splitlines()
               if ln.split(" ", 1)[0] in {"A-EXIT", "B-EXIT", "MEASURE",
                                          "B-LOG", "DANGLING", "MARKER"})
    assert "B-EXIT" in out, r.stdout[-4000:] + r.stderr[-4000:]
    assert out["A-EXIT"] != "0", out                  # A failed mid-install
    assert out["B-LOG"] == "0", out      # B never took A's install as done
    assert out["B-EXIT"] == "0", r.stdout[-4000:]
    # B's own copy is whole: the version's node and npm run, and leerie's
    # enable made its shims (the image's PATH order is not this test's).
    assert out["MEASURE"] == "node=v22.19.0 npm=10.9.3 pnpm=linked", out
    assert out["DANGLING"] == "0", out
    assert out["MARKER"] == "0", out
