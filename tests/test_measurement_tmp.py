"""A repository's own test suite runs inside leerie's sandbox the way it runs
outside it (DESIGN §6 *A confined command's scratch is short and private*,
*Every confined process has its own `/tmp`, `/dev/shm` and IPC*, *A
confined command's processes end with it*). The isolation itself — a private
`/tmp` per process — is tests/test_heldout_isolation.py's, on a host that
can build it; here no process is isolated, and no grant names `/tmp`.

Review round 19's execution driver (H1: barnacle's tsx IPC socket, its fixed
`/tmp/recon/<id>` paths, funeralworks' `/tmp/leerie-test-pgdata.lock`)
asserted the defect; the first tests here are that driver turned round, on
the real shim and `_measurement_grants`."""
from __future__ import annotations

import ast
import asyncio
import inspect
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _grants, _probe, pytestmark, run)

SUN_PATH_MAX = 107  # sizeof(sockaddr_un.sun_path) - 1 on Linux
SOCK_TAIL = "/tsx-1000/438625.pipe"  # what tsx puts in os.tmpdir()


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _stream(leerie, st, tree: Path, script: str, *, timeout: float = 60,
            env: dict | None = None) -> tuple[int, str]:
    g = leerie._measurement_grants(st, tree)
    return asyncio.run(leerie._run_streaming(
        ["bash", "-c", script], grants=g, cwd=str(tree), env=env,
        timeout=timeout, verbosity="quiet", tail_lines=50))


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            return f.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


# --- H1, turned round: the socket fits -------------------------------------

def test_the_container_measurement_tmpdir_fits_a_socket_path(leerie):
    """In the container the run directory alone is 82 characters
    (`/leerie-state/runs/<64 hex>`); the private temp directory does not
    depend on it, so tsx's IPC pipe fits for every tree."""
    rid = "f" * 64
    made = []
    try:
        for name in ("view-staging", "acceptance-eval",
                     "view-" + leerie._worktree_dirname(rid, "feat-mul")):
            tree = leerie._worktree_dirname(rid, name)
            own = Path(f"/leerie-state/runs/{rid}/{leerie.MEASURE_DIRNAME}/"
                       f"{tree}-{'0' * 16}")
            tmp = leerie._private_tmp(own)
            made.append(tmp)
            assert len(str(tmp) + SOCK_TAIL) <= SUN_PATH_MAX, tmp
    finally:
        for d in made:
            shutil.rmtree(d, ignore_errors=True)


def test_a_measured_command_binds_a_socket_in_its_tmpdir(leerie, run):
    view = _view(run)
    rc, tail = _stream(leerie, _st(run), view, textwrap.dedent("""\
        python3 -c "import os, socket, tempfile
        d = os.path.join(tempfile.gettempdir(), 'tsx-1000')
        os.makedirs(d, exist_ok=True)
        s = socket.socket(socket.AF_UNIX); s.bind(os.path.join(d, '438625.pipe'))
        s.listen(1); print('PY-LISTEN-OK')"
        """))
    assert rc == 0 and "PY-LISTEN-OK" in tail, tail
    if shutil.which("node"):
        rc, tail = _stream(leerie, _st(run), view, (
            "node -e \"const p=require('path'),os=require('os'),"
            "fs=require('fs');const d=p.join(os.tmpdir(),'tsx-1000');"
            "fs.mkdirSync(d,{recursive:true});const s=require('net')"
            ".createServer().listen(p.join(d,'1.pipe')).on('error',e=>"
            "{console.error(e.code);process.exit(3)}).on('listening',()=>"
            "{console.log('NODE-LISTEN-OK');s.close()})\""))
        assert rc == 0 and "NODE-LISTEN-OK" in tail, tail


def _rw_of(argv: list[str]) -> list[str]:
    return [argv[i + 1] for i, a in enumerate(argv) if a == "--rw"]


def test_no_grant_names_tmp_itself(leerie, run):
    """`/tmp` is never granted (the `shared_tmp` grant is gone): an isolated
    process's own is added by the shim. Unisolated, as here, a measured
    command writes its private temp directory and not `/tmp`."""
    st, view = _st(run), _view(run)
    for kind in (leerie.MEASURE_KIND, leerie.SETUP_HOOK_KIND,
                 leerie.TOOLCHAIN_KIND, leerie.STORE_FETCH_KIND):
        g = leerie._measurement_grants(st, view, kind)
        assert "/tmp" not in g.rw + g.ro, kind
        assert "/tmp" not in _rw_of(leerie._confined_argv(g, ["true"]))
    g = leerie._measurement_grants(st, view)
    planted = Path(f"/tmp/laneB-measure-planted-{os.getpid()}")
    try:
        got = _probe(leerie, g, f"""
private tmp|echo x > "$TMPDIR/ok"
shared tmp|echo x > {planted}
""", dict(g.env))
        assert got == {"private tmp": True, "shared tmp": False}
    finally:
        planted.unlink(missing_ok=True)


# --- every directory leerie makes for a command is 0700 ---------------------

def _swc_refuses(path: Path) -> Path | None:
    """The ancestor SWC's cache-root check would refuse: one writable by
    group or others without a sticky bit owned by root or this user."""
    for a in [path, *path.parents]:
        st_ = os.lstat(a)
        if stat.S_IMODE(st_.st_mode) & 0o022:
            if not (st_.st_mode & stat.S_ISVTX
                    and st_.st_uid in (0, os.geteuid())):
                return a
    return None


def test_the_private_tmp_chain_is_one_swc_accepts(leerie, run):
    g = leerie._measurement_grants(_st(run), _view(run))
    tmp = Path(dict(g.env)["TMPDIR"])
    xdg = Path(dict(g.env)["XDG_CACHE_HOME"])
    for p in (tmp, leerie.PRIVATE_TMP_ROOT):
        st_ = os.lstat(p)
        assert stat.S_ISDIR(st_.st_mode) and st_.st_uid == os.geteuid()
        assert stat.S_IMODE(st_.st_mode) == 0o700, (p, oct(st_.st_mode))
    assert _swc_refuses(tmp) is None
    assert _swc_refuses(xdg) is None
    # The state root here is under the host's umask (as `~/.leerie` is,
    # 775): the measurement directory's chain is not one SWC accepts,
    # which is why no cache or temp variable names it.
    own = leerie._measure_dir(_st(run), _view(run))
    env = dict(g.env)
    for k in ("TMPDIR", "TMP", "TEMP", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(env[k]).is_relative_to(tmp), k
        assert _swc_refuses(Path(env[k])) is None, k


def test_every_dir_leerie_makes_for_a_command_or_worker_is_0700(leerie, run):
    rd = run["run_dir"]
    st = _st(run)
    leerie._measurement_grants(st, run["wts"]["wt-a"])
    own = leerie._measure_dir(st, run["wts"]["wt-a"])
    made = [own, *(own / s for s in leerie._MEASURE_SUBDIRS),
            rd / leerie.MEASURE_DIRNAME,
            rd / leerie.TREE_STORES_DIRNAME / "wt-a"]
    config, tmp = leerie._worker_dirs(rd, "w-0700", None)
    made += [config, tmp, config.parent]
    leerie._worker_cache_env({}, rd)
    made += [rd / "workers" / "cache" / "xdg"]
    for d in made:
        assert stat.S_IMODE(os.lstat(d).st_mode) == 0o700, (d,
                                                            oct(os.lstat(d)
                                                                .st_mode))


def test_a_private_tmp_that_is_not_ours_stops_the_run(leerie, tmp_path,
                                                      monkeypatch):
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = tmp_path / "l"
    link.symlink_to(elsewhere)
    monkeypatch.setattr(leerie, "PRIVATE_TMP_ROOT", link)
    with pytest.raises(SystemExit):
        leerie._private_tmp(tmp_path / "measure" / "t-0")
    assert list(elsewhere.iterdir()) == []


def test_a_private_tmp_made_open_before_is_closed(leerie, tmp_path,
                                                 monkeypatch):
    """A `/tmp/l` (or a tree's directory in it) left group- or
    world-accessible — by an older leerie, a umask — is made 0700 again."""
    root = tmp_path / "l"
    root.mkdir(mode=0o777)
    os.chmod(root, 0o777)
    monkeypatch.setattr(leerie, "PRIVATE_TMP_ROOT", root)
    key = tmp_path / "measure" / "t-0"
    d = root / leerie.hashlib.sha256(str(key).encode()).hexdigest()[:16]
    d.mkdir()
    os.chmod(d, 0o755)
    assert leerie._private_tmp(key) == d
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert stat.S_IMODE(d.stat().st_mode) == 0o700


def test_the_private_tmp_goes_with_its_tree(leerie, run):
    """Removed by the sweep once its tree is gone, as its measurement
    directory is."""
    st = _st(run)
    tree = run["wts"]["wt-b"]
    tmp = Path(dict(leerie._measurement_grants(st, tree).env)["TMPDIR"])
    (tmp / "left").write_text("x")
    subprocess.run(["git", "-C", str(run["repo"]), "worktree", "remove",
                    "--force", str(tree)], check=False, capture_output=True)
    shutil.rmtree(tree, ignore_errors=True)
    leerie._measure_dir(st, _view(run))      # any later measurement sweeps
    assert not tmp.exists()


def test_the_private_tmp_goes_with_the_runs_scratch(leerie, run):
    """When the run's process releases its run directory — at exit, or
    before an auto-resume's exec — the measured trees' private temp
    directories go with `measure/`."""
    tmp = Path(dict(leerie._measurement_grants(_st(run), _view(run)).env)
               ["TMPDIR"])
    (tmp / "left").write_text("x")
    assert leerie._release_run_dir(run["run_dir"])
    assert not tmp.exists()
    assert not (run["run_dir"] / leerie.MEASURE_DIRNAME).exists()


def test_the_private_tmps_go_at_the_process_exit(leerie, tmp_path):
    from tests.conftest import LEERIE_PY
    out = subprocess.run([sys.executable, "-c", textwrap.dedent(f"""
        import importlib.util, pathlib, sys
        spec = importlib.util.spec_from_file_location("leerie", {str(LEERIE_PY)!r})
        m = importlib.util.module_from_spec(spec); sys.modules["leerie"] = m
        spec.loader.exec_module(m)
        d = m._private_tmp(pathlib.Path({str(tmp_path)!r}) / "measure" / "x-0")
        (d / "f").write_text("x")
        print(d)
        """)], capture_output=True, text=True, check=True)
    d = Path(out.stdout.strip().splitlines()[-1])
    assert d.parent == Path("/tmp/l") and not d.exists()


# --- an unisolated worker has no `/tmp` (an isolated one has its own) ------

def test_a_worker_neither_reads_nor_writes_tmp(leerie, run):
    marker = Path(f"/tmp/laneA-worker-marker-{os.getpid()}")
    marker.write_text("x\n")
    planted = Path(f"/tmp/laneA-worker-planted-{os.getpid()}")
    try:
        for key, tools in (("implementer", None),
                           ("delivery_judge", leerie.ACT_TOOLS),
                           ("classifier", leerie.INSPECT_TOOLS)):
            cwd = (run["wts"]["wt-a"] if key == "implementer"
                   else run["wts"][f"{run['run_dir'].name}-view-staging"])
            g = _grants(leerie, run, schema_key=key, cwd=cwd, tools=tools)
            # Nothing of `/tmp` but this host's own layout: the run
            # directory, leerie's checkout and the test's `$HOME`, each
            # beneath it here and each named.
            from tests.conftest import REPO_ROOT
            home = os.path.realpath(os.environ["HOME"])
            assert not [p for p in g.ro + g.rw
                        if Path(os.path.realpath(p)).is_relative_to("/tmp")
                        and not Path(p).is_relative_to(run["run_dir"])
                        and not Path(p).is_relative_to(REPO_ROOT)
                        and p != home], key
            assert "/tmp" not in g.ro + g.rw
            got = _probe(leerie, g, f"""
list tmp|ls /tmp
read tmp|cat {marker}
write tmp|echo x > {planted}
own tmpdir|echo x > "$TMPDIR/own"
""", run["env"])
            assert got == {"list tmp": False, "read tmp": False,
                           "write tmp": False, "own tmpdir": True}, (key, got)
    finally:
        marker.unlink(missing_ok=True)
        planted.unlink(missing_ok=True)
    assert not planted.exists()


def test_reads_outside_without_tmp_names_nothing_in_it(leerie, tmp_path):
    for root in (tmp_path / "state", Path("/leerie-state-absent")):
        paths = leerie._reads_outside(root, tmp=False)
        assert paths and not any(
            Path(os.path.realpath(p)).is_relative_to("/tmp") for p in paths)
        assert "/proc" in paths and "/usr" in paths


# --- a confined command's processes end with it ------------------------------

@pytest.mark.parametrize("ignore_term", [False, True])
def test_a_background_process_is_gone_when_run_streaming_returns(
        leerie, run, ignore_term):
    trap = "trap '' TERM; " if ignore_term else ""
    t0 = time.monotonic()
    rc, tail = _stream(leerie, _st(run), _view(run), (
        f"setsid bash -c \"{trap}exec sleep 300\" >/dev/null 2>&1 & "
        "echo PID=$!; (sleep 300 &); exit 0"), timeout=60)
    took = time.monotonic() - t0
    assert rc == 0, tail
    pid = int(tail.split("PID=")[1].split()[0])
    assert not _alive(pid), pid
    # The second `sleep` held the output pipe open: before the shim, the
    # read loop waited the 300 s out.
    assert took < 30, took


def test_a_timed_out_commands_processes_are_gone(leerie, run):
    st = _st(run)
    tree = _view(run)
    g = leerie._measurement_grants(st, tree)
    pidfile = Path(dict(g.env)["TMPDIR"]) / "daemon.pid"
    with pytest.raises(subprocess.TimeoutExpired):
        asyncio.run(leerie._run_streaming(
            ["bash", "-c", f"setsid sleep 300 >/dev/null 2>&1 & "
                           f"echo $! > {pidfile}; sleep 300"],
            grants=g, cwd=str(tree), timeout=2, verbosity="quiet"))
    pid = int(pidfile.read_text())
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def test_the_shim_keeps_the_commands_exit(leerie, run):
    g = leerie._measurement_grants(_st(run), _view(run))
    for script, want in (("exit 0", 0), ("exit 7", 7), ("kill -9 $$", -9),
                         ("kill -TERM $$", -15)):
        r = subprocess.run(leerie._confined_argv(g, ["bash", "-c", script]),
                           capture_output=True)
        assert r.returncode == want, (script, r.returncode, r.stderr)


def test_a_worker_sessions_background_processes_end_with_it(leerie, run):
    """The same shim starts every worker (`_invoke` spawns
    `_confined_argv`): a tool call's `setsid` daemon does not outlive the
    session, with or without a cgroup."""
    g = _grants(leerie, run)
    r = subprocess.run(leerie._confined_argv(g, [
        "bash", "-c", "setsid sleep 300 >/dev/null 2>&1 & echo $!"]),
        capture_output=True, text=True, env={**os.environ, **run["env"]},
        timeout=30)
    assert r.returncode == 0, r.stderr
    assert not _alive(int(r.stdout.split()[0]))


# --- leerie's own temp files are never in `/tmp` ----------------------------

def test_leeries_own_temp_dir_is_in_the_run_dir(leerie, tmp_path):
    import tempfile
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    system = tempfile.gettempdir()
    d = leerie._use_run_tmp(_St(tmp_path, run_dir))
    # The smoke test's directory stays outside every repository.
    assert leerie._system_temp_dir() == system
    assert d == run_dir / "tmp" and stat.S_IMODE(d.stat().st_mode) == 0o700
    assert tempfile.gettempdir() == str(d) == os.environ["TMPDIR"]
    fd, p = tempfile.mkstemp()
    os.close(fd)
    assert Path(p).parent == d
    assert "tmp" in leerie._MEASURE_PRIVATE


def test_run_phases_moves_leeries_temp_dir_first(leerie):
    tree = ast.parse(textwrap.dedent(inspect.getsource(leerie._run_phases)))
    body = tree.body[0].body
    calls = [ast.unparse(s.value.func) for s in body
             if isinstance(s, ast.Expr) and isinstance(s.value, ast.Call)]
    assert calls[:2] == ["log", "_use_run_tmp"], calls[:3]
