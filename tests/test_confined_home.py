"""Every confined process has its own home (DESIGN §6 *Every confined
process has its own home*).

Until 2026-11-13 the run's one home was read-only to every confined process,
so each tool that writes beneath `~` by a fixed path failed until leerie named
a variable that moved it (pip's user base, the .NET CLI's home, gpg's home),
and rustup's `~/.rustup` killed every Rust repository at provision. Now the
shim mounts an overlay on `$HOME` per process: lower layers the toolchain home
layer and the pristine home, upper a `tmpfs` in the process's own mount
namespace. Only the toolchain install writes a layer others read: its `HOME`
is the toolchain home layer, and it sees no store variable.

The grant-shape tests run anywhere. The real-path tests build the overlay,
which needs a user namespace this host's AppArmor may refuse, so they run in
a leerie container (LEERIE_TEST_CONTAINER_IMAGE, `nerdctl` on PATH, the
`leerie-container` profile loaded where AppArmor is), as
`tests/test_pinned_runtime_runs.py` does: they drive leerie's real
`_require_confinement`, `_measurement_grants` and `_run_streaming` and count
what each exit path leaves.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REAL_HOME, XDG_BASE_DIRS  # noqa: F401
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


@pytest.fixture()
def homes(leerie, tmp_path, monkeypatch) -> tuple[Path, Path]:
    """A home and a toolchain home layer beside it. The session's paths lie
    beneath `/tmp`, which the shim's own `/tmp` would hide, so `/tmp` is
    named elsewhere for these tests (`_confined_home` answers "" for a home
    beneath it, pinned below)."""
    h, layer = tmp_path / "home", tmp_path / "layer"
    h.mkdir()
    layer.mkdir()
    monkeypatch.setattr(leerie, "SHARED_TMP", tmp_path / "no-shared-tmp")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise"))
    monkeypatch.setenv("LEERIE_TOOLCHAIN_HOME", str(layer))
    return h, layer


KINDS = ["MEASURE_KIND", "INSTALL_KIND", "SETUP_HOOK_KIND", "TOOLCHAIN_KIND"]


@pytest.mark.parametrize("kind", KINDS)
def test_no_grant_names_a_gnupg_home(leerie, run, homes, kind):
    """`GNUPGHOME` existed only because the home was read-only: a private
    home's `~/.gnupg` is the command's own."""
    g = leerie._measurement_grants(_st(run), _view(run),
                                   getattr(leerie, kind))
    assert "GNUPGHOME" not in dict(g.env)


@pytest.mark.parametrize("kind", ["MEASURE_KIND", "INSTALL_KIND",
                                  "SETUP_HOOK_KIND"])
def test_every_other_kind_reads_the_layer_beneath_its_own_home(
        leerie, run, homes, kind):
    h, layer = homes
    g = leerie._measurement_grants(_st(run), _view(run),
                                   getattr(leerie, kind))
    assert g.home == str(h.resolve())
    assert g.home_layers == (str(layer.resolve()),)
    assert g.drop == ()
    assert "HOME" not in dict(g.env)
    assert str(layer.resolve()) not in g.rw


def test_the_toolchain_install_writes_the_layer_and_sees_no_store(
        leerie, run, homes):
    h, layer = homes
    g = leerie._measurement_grants(_st(run), _view(run),
                                   leerie.TOOLCHAIN_KIND)
    assert dict(g.env)["HOME"] == str(layer.resolve())
    assert str(layer.resolve()) in g.rw
    # It reads no layer it writes.
    assert g.home_layers == ()
    assert set(leerie._STORE_VARS) <= set(g.drop)
    base = {**{v: "/store" for v in leerie._STORE_VARS},
            "pnpm_config_store_dir": "/store", "KEEP": "1"}
    env = leerie._grant_env(base, g)
    assert not any(v in env for v in leerie._STORE_VARS), env
    assert "pnpm_config_store_dir" not in env
    assert env["KEEP"] == "1" and env["HOME"] == str(layer.resolve())


def test_a_worker_names_its_home_and_the_layer(leerie, run, homes):
    h, layer = homes
    g = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS,
        env={**run["env"], "HOME": str(h)})
    assert g.home == str(h.resolve())
    assert g.home_layers == (str(layer.resolve()),)


def test_a_home_beneath_tmp_is_none(leerie, tmp_path):
    """The shim's own `/tmp` would hide a home beneath it (a host
    harness's), and a home holding `/tmp` would take it: no overlay is
    asked for either."""
    assert leerie._confined_home({"HOME": "/tmp/x"}) == ""
    assert leerie._confined_home({"HOME": "/"}) == ""
    assert leerie._confined_home({"HOME": "/home/leerie"}) == \
        os.path.realpath("/home/leerie")


def test_the_shim_is_asked_for_the_home_only_when_isolated(
        leerie, run, homes, monkeypatch):
    h, layer = homes
    g = leerie._measurement_grants(_st(run), _view(run), leerie.MEASURE_KIND)
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)
    assert "--home" not in leerie._confined_argv(g, ["true"])
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    argv = leerie._confined_argv(g, ["true"])
    i = argv.index("--home")
    assert argv[i + 1] == str(h.resolve())
    assert argv[argv.index("--home-bytes") + 1] == str(
        leerie.HOME_UPPER_BYTES)
    assert argv[argv.index("--home-layer") + 1] == str(layer.resolve())
    assert argv.index("--home") < argv.index("--")


def test_the_toolchain_install_keeps_its_home_writes_in_the_layer(
        leerie, run, homes):
    """Under the real shim (Landlock; not isolated on this host): the
    toolchain kind's `HOME` is the layer, it writes there, and no store
    variable leerie's own environment holds reaches it."""
    import asyncio
    h, layer = homes
    view = _view(run)
    g = leerie._measurement_grants(_st(run), view, leerie.TOOLCHAIN_KIND)
    env = {**os.environ, "npm_config_store_dir": "/nowhere",
           "CARGO_HOME": "/nowhere"}
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", 'mkdir -p "$HOME/.rustup" && echo kept > '
         '"$HOME/.rustup/marker" && echo "HOME=$HOME" && '
         'echo "CARGO=${CARGO_HOME-unset} NPM=${npm_config_store_dir-unset}"'],
        grants=g, cwd=str(view), env=env, timeout=60, verbosity="quiet",
        tail_lines=20))
    assert rc == 0, tail
    assert (layer / ".rustup" / "marker").read_text() == "kept\n"
    assert f"HOME={layer.resolve()}" in tail, tail
    assert "CARGO=unset NPM=unset" in tail, tail


# ------------------------------------------------------ the real container

_DRIVER = r'''
import asyncio, importlib.util, json, os, signal, subprocess, sys, time
from pathlib import Path

spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
leerie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leerie)
RUN = "confhome0"
HOME = Path(os.environ["HOME"])
LAYER = Path(os.environ["LEERIE_TOOLCHAIN_HOME"])


class _St:
    def __init__(s, user, run_dir):
        s.repo_root, s.run_id, s.run_dir = user, RUN, run_dir
        s.data = {"working_branch": "main", "verbosity": "quiet"}

    def save(s):
        pass


base = Path(sys.argv[2])
user = base / "user"
user.mkdir(parents=True)


def g(*a, cwd=user):
    return subprocess.run(["git", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


g("init", "-q", "-b", "main")
(user / "README").write_text("x\n")
g("add", "-A")
g("-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "base")
state_root = base / "state"
run_dir = state_root / "runs" / RUN
run_dir.mkdir(parents=True)
st = _St(user, run_dir)
repo = leerie._ensure_run_repo(st)
wt = run_dir / "worktrees" / f"{RUN}-view"
g("worktree", "add", "-q", str(wt), "main", cwd=repo)
leerie._require_confinement(run_dir, state_root, user)
out = {"isolated": leerie._isolated()}
(LAYER / ".rustup").mkdir(parents=True, exist_ok=True)
(LAYER / ".rustup" / "marker").write_text("layer\n")
real_before = sorted(os.listdir(HOME))


def shmem():
    for ln in open("/sys/fs/cgroup/memory.stat"):
        k, v = ln.split()
        if k == "shmem":
            return int(v)


def mounts():
    return len(open("/proc/self/mountinfo").read().splitlines())


def tmp_left():
    return sorted(n for n in os.listdir("/tmp") if n.startswith(".leerie-home"))


def grants(kind=leerie.MEASURE_KIND):
    return leerie._measurement_grants(st, wt, kind)


def cmd(script, timeout=120, kind=leerie.MEASURE_KIND):
    return asyncio.run(leerie._run_streaming(
        ["bash", "-c", script], grants=grants(kind), cwd=str(wt),
        timeout=timeout, verbosity="quiet", tail_lines=40))


# 1. A write lands in the process's own home; the layer is read beneath it.
rc, tail = cmd('echo a > ~/.hc-a && cat ~/.rustup/marker && '
               'echo w >> ~/.rustup/marker && cat ~/.rustup/marker && '
               'echo "statfs=$(stat -f -c %T ~)"')
out["write"] = {"rc": rc, "tail": tail}
# 2. Another process does not see it, nor does the real home or the layer.
rc, tail = cmd('test -e ~/.hc-a && echo SEEN || echo ABSENT; cat ~/.rustup/marker')
out["second"] = {"rc": rc, "tail": tail}
out["real_home_unchanged"] = sorted(os.listdir(HOME)) == real_before
out["layer_unchanged"] = (LAYER / ".rustup" / "marker").read_text() == "layer\n"

# 3. Every exit path leaves nothing: 128 MiB written to the home, then the
# command ends each way; the container's shared memory, its mounts and its
# /tmp are counted after.
FILL = 'dd if=/dev/zero of=$HOME/big bs=1M count=128 status=none; '
base_shm, base_mounts = shmem(), mounts()
paths = {}
rc, _ = cmd(FILL + "exit 0")
paths["exit0"] = rc
rc, _ = cmd(FILL + "exit 3")
paths["exit3"] = rc
try:
    cmd(FILL + "sleep 300", timeout=5)
    paths["timeout"] = "no timeout"
except subprocess.TimeoutExpired:
    paths["timeout"] = "TimeoutExpired"
# SIGKILL of the shim itself, then the tree kill leerie's own paths make.
argv = leerie._confined_argv(grants(), ["bash", "-c", FILL + "sleep 300"])
p = subprocess.Popen(argv, cwd=str(wt), start_new_session=True,
                     env=leerie._grant_env(leerie._repo_command_env(
                         dict(os.environ)), grants()))
time.sleep(4)
os.kill(p.pid, signal.SIGKILL)
p.wait()
try:
    os.killpg(p.pid, signal.SIGKILL)
except ProcessLookupError:
    pass
time.sleep(1)
paths["sigkill"] = p.returncode
time.sleep(1)
out["paths"] = paths
out["after"] = {"shmem_delta": shmem() - base_shm,
                "mounts_delta": mounts() - base_mounts,
                "tmp_left": tmp_left()}

# 4. A full home is a finding, never a result.
leerie.HOME_UPPER_BYTES = 16 << 20
try:
    rc, tail = cmd('dd if=/dev/zero of=$HOME/big bs=1M count=64 status=none; '
                   'echo "dd=$?"; exit 1')
    out["fill"] = {"raised": None, "rc": rc}
except leerie.ConfinementRefused as e:
    out["fill"] = {"raised": str(e)}
leerie.HOME_UPPER_BYTES = 1 << 30

# 5. The toolchain install's home writes are the layer's, seen by the next.
rc, tail = cmd('mkdir -p ~/.cargo/bin && echo tool > ~/.cargo/bin/t && '
               'echo "HOME=$HOME"', kind=leerie.TOOLCHAIN_KIND)
out["toolchain"] = {"rc": rc, "tail": tail}
rc, tail = cmd('cat /home/leerie/.cargo/bin/t')
out["after_toolchain"] = {"rc": rc, "tail": tail}
print("RESULT " + json.dumps(out), flush=True)
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
def test_the_real_container_gives_each_process_its_own_home(tmp_path):
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    (tmp_path / "driver.py").write_text(_DRIVER)
    for p in (tmp_path, tmp_path / "driver.py"):     # read as `leerie`
        p.chmod(p.stat().st_mode | 0o055)
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    # The profile this checkout's launcher loads; a host whose loaded one is
    # an older release's names a test copy of this one instead.
    profile = os.environ.get("LEERIE_TEST_APPARMOR_PROFILE",
                             "leerie-container")
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", f"apparmor={profile}"]
    # The entry's own drop: rootless maps outer root onto leerie (the home
    # stays root-owned), rootful switches uid after chowning the home.
    drop = ('unshare --user --map-user="$(id -u leerie)" '
            '--map-group="$(id -g leerie)"' if rootless.is_file() else
            "chown -R leerie: /home/leerie /leerie-toolchains && "
            "runuser -u leerie --")
    # Owned as the home is: by root under rootless (outer root is the inner
    # leerie), by leerie's uid under rootful.
    owner = ("uid=0,gid=0" if rootless.is_file() else "uid=1000,gid=1000")
    tmpfs = f"rw,exec,size=1g,{owner}"
    r = subprocess.run(
        ["nerdctl", "run", "--rm", *sec, "--user", "0",
         "--entrypoint", "bash",
         "-v", f"{REPO_ROOT}:/src:ro", "-v", f"{tmp_path}:/out:ro",
         "--tmpfs", f"/leerie-state:{tmpfs}",
         image, "-c",
         "mkdir -p /leerie-toolchains/home /leerie-toolchains/mise && "
         f"{drop} env HOME=/home/leerie "
         "LEERIE_TOOLCHAIN_HOME=/leerie-toolchains/home "
         "MISE_DATA_DIR=/leerie-toolchains/mise "
         "/usr/bin/python3 /out/driver.py "
         "/src/orchestrator/leerie.py /leerie-state/w"],
        capture_output=True, text=True, timeout=600, env=_users_env())
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, r.stdout[-4000:] + r.stderr[-4000:]
    got = json.loads(line[0][len("RESULT "):])
    print("RESULT", json.dumps(got["paths"]), json.dumps(got["after"]))
    assert got["isolated"] is True, got
    w = got["write"]
    assert w["rc"] == 0 and "layer\nlayer\nw" in w["tail"], got
    assert "statfs=overlayfs" in w["tail"], got
    s = got["second"]
    assert s["rc"] == 0 and "ABSENT" in s["tail"], got
    assert s["tail"].rstrip().endswith("layer"), got
    assert got["real_home_unchanged"] and got["layer_unchanged"], got
    assert got["paths"] == {"exit0": 0, "exit3": 3,
                            "timeout": "TimeoutExpired",
                            "sigkill": -9}, got
    # 512 MiB were written across the four paths; none of it is left.
    assert got["after"]["shmem_delta"] < 8 << 20, got
    assert got["after"]["mounts_delta"] == 0, got
    assert got["after"]["tmp_left"] == [], got
    raised = got["fill"]["raised"]
    assert raised and "filled its home /home/leerie (16 MiB" in raised, got
    t = got["toolchain"]
    assert t["rc"] == 0 and "HOME=/leerie-toolchains/home" in t["tail"], got
    a = got["after_toolchain"]
    assert a["rc"] == 0 and a["tail"].strip().endswith("tool"), got
