"""No confined process holds or can open the operator's terminal (DESIGN §6
*No confined process holds or can open the operator's terminal*; review
round 32 isolation P1, round 33 isolation Q1). In an interactive run the
orchestrator's fds 0-2 are the operator's pty, open read-write; Landlock
checks opens, not inherited descriptors. And under `nerdctl run -it` the
same pty is `/dev/console`, which a confined process could open for as long
as its grants read `/dev`. Every test here hands the real shim, or the real
spawners, a real pty and reads the pty's master side for what reached it."""
from __future__ import annotations

import asyncio
import fcntl
import importlib.util
import json
import os
import pty
import select
import subprocess
import sys
import termios
import textwrap
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_tty", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


LL = _shim_module()
_ISOLATION = LL.isolation_error()

pytestmark = pytest.mark.skipif(
    LL.abi() < LL.MIN_ABI, reason="the host kernel has no Landlock ABI 3")

# What a repository's `npm test` could do with a descriptor of the terminal:
# set the clipboard (OSC 52), read a keystroke, open the controlling
# terminal. It prints what it found, as JSON, on its stdout pipe.
PROBE = textwrap.dedent(r"""
    import json, os, select, sys
    out = {"fd0": os.readlink("/proc/self/fd/0"), "fd0_tty": os.isatty(0)}
    try:
        os.write(0, b"PWNED\x1b]52;c;UFdORUQ=\x07")
        out["wrote0"] = True
    except OSError:
        out["wrote0"] = False
    r, _w, _x = select.select([0], [], [], 0.5)
    out["read0"] = (os.read(0, 64).decode(errors="replace") if r else "")
    try:
        fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError as e:
        out["dev_tty"] = e.errno
    else:
        os.write(fd, b"PWNED\x1b]52;c;VFRZ\x07")
        out["dev_tty"] = "opened"
    print(json.dumps(out))
""")


def _reads_but_dev_and_tmp() -> list[str]:
    """Reads of every top-level entry but `/dev` and `/tmp`, as leerie's
    grants are (`_reads_outside`): a grant of `/` would read the
    container's `/dev/console`, and the shim refuses to run with one."""
    return [f"/{n}" for n in sorted(os.listdir("/"))
            if n not in ("dev", "tmp")]


def _shim_argv(tmp: Path, *, isolate: bool) -> list[str]:
    ro = [a for p in _reads_but_dev_and_tmp() for a in ("--ro", p)]
    return [sys.executable, "-I", "-S", str(SHIM),
            *(["--isolate"] if isolate else []),
            "--rw", str(tmp), "--rw", "/dev/null", "--rw", "/dev/tty",
            *ro, "--",
            sys.executable, "-I", "-S", "-c", PROBE]


def _drain(master: int) -> bytes:
    got = b""
    while True:
        r, _w, _x = select.select([master], [], [], 0.2)
        if not r:
            return got
        try:
            chunk = os.read(master, 4096)
        except OSError:
            return got
        if not chunk:
            return got
        got += chunk


@pytest.fixture
def term():
    """A pty whose master already holds what the operator typed."""
    master, slave = pty.openpty()
    os.write(master, b"operator-secret\n")
    yield master, slave
    for fd in (master, slave):
        try:
            os.close(fd)
        except OSError:
            pass


def _modes():
    return [pytest.param(False, id="plain"),
            pytest.param(True, id="isolate", marks=pytest.mark.skipif(
                _ISOLATION is not None,
                reason=f"this host cannot isolate ({_ISOLATION})"))]


@pytest.mark.parametrize("isolate", _modes())
def test_a_terminal_on_stdin_is_replaced_by_dev_null(tmp_path, term, isolate):
    master, slave = term
    r = subprocess.run(_shim_argv(tmp_path, isolate=isolate), cwd=tmp_path,
                       stdin=slave, capture_output=True, text=True,
                       timeout=60)
    seen = _drain(master)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["fd0"] == "/dev/null" and out["fd0_tty"] is False, out
    assert "operator-secret" not in out["read0"], out
    assert b"\x1b" not in seen and b"PWNED" not in seen, seen


@pytest.mark.parametrize("fd", [1, 2, "other"])
def test_a_terminal_on_any_other_descriptor_is_refused(tmp_path, term, fd):
    master, slave = term
    marker = tmp_path / "ran"
    argv = [sys.executable, "-I", "-S", str(SHIM), "--rw", str(tmp_path),
            "--ro", "/", "--", sys.executable, "-I", "-S", "-c",
            f"open({str(marker)!r}, 'w').close()"]
    kw: dict = {"stdin": subprocess.DEVNULL, "stdout": subprocess.PIPE,
                "stderr": subprocess.PIPE}
    if fd in (1, 2):
        kw["stdout" if fd == 1 else "stderr"] = slave
        r = subprocess.run(argv, cwd=tmp_path, timeout=60, **kw)
    else:
        other = os.dup(slave)
        try:
            r = subprocess.run(argv, cwd=tmp_path, timeout=60,
                               pass_fds=(other,), **kw)
        finally:
            os.close(other)
    _drain(master)
    assert r.returncode == 126
    assert not marker.exists()


# The shim started as `_ts_script_run` starts it: in the orchestrator's
# session, whose controlling terminal is the operator's pty, not leading a
# process group of its own (`subprocess.run`, no `start_new_session`).
_IN_SESSION = textwrap.dedent(r"""
    import fcntl, os, subprocess, sys, termios
    slave = int(sys.argv[1])
    os.setsid()
    fcntl.ioctl(slave, termios.TIOCSCTTY, 0)
    r = subprocess.run(sys.argv[3:], cwd=sys.argv[2],
                       stdin=subprocess.DEVNULL, capture_output=True,
                       pass_fds=())
    sys.stdout.write(r.stdout.decode())
    sys.stderr.write(r.stderr.decode())
    sys.exit(r.returncode)
""")


@pytest.mark.parametrize("isolate", _modes())
def test_the_controlling_terminal_is_given_up(tmp_path, term, isolate):
    master, slave = term
    r = subprocess.run(
        [sys.executable, "-I", "-S", "-c", _IN_SESSION, str(slave),
         str(tmp_path), *_shim_argv(tmp_path, isolate=isolate)],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=60, pass_fds=(slave,))
    seen = _drain(master)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["dev_tty"] != "opened", out
    assert b"\x1b" not in seen and b"PWNED" not in seen, seen


def test_a_session_leader_with_a_terminal_is_refused(tmp_path, term):
    """Giving the terminal up as its session leader would hang up the
    terminal's foreground group, so the shim does not run at all."""
    master, slave = term

    def own_terminal() -> None:
        fcntl.ioctl(slave, termios.TIOCSCTTY, 0)

    r = subprocess.run(_shim_argv(tmp_path, isolate=False), cwd=tmp_path,
                       stdin=subprocess.DEVNULL, capture_output=True,
                       timeout=60, start_new_session=True,
                       preexec_fn=own_terminal)
    seen = _drain(master)
    assert r.returncode == 126, r.stderr
    assert b"PWNED" not in seen and b"PWNED" not in r.stdout


# Each spawner of a confined command passes its own stdin, never the
# orchestrator's fd 0: run here with the shim taken out, so what the command
# holds is what the spawner gave it.
_FD0 = [sys.executable, "-I", "-S", "-c",
        "import os; print(os.readlink('/proc/self/fd/0'))"]


@pytest.fixture
def fd0_is_a_terminal(term):
    master, slave = term
    saved = os.dup(0)
    os.dup2(slave, 0)
    try:
        yield
    finally:
        os.dup2(saved, 0)
        os.close(saved)


@pytest.fixture
def unconfined(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_confined_argv",
                        lambda grants, cmd, **_kw: list(cmd))
    monkeypatch.setattr(leerie, "_exec_lock_held", lambda: True)
    return leerie


def test_run_streaming_gives_no_terminal(unconfined, fd0_is_a_terminal,
                                         tmp_path):
    lr = unconfined
    rc, tail = asyncio.run(lr._run_streaming(
        _FD0, grants=lr.WorkerGrants(rw=(str(tmp_path),), ro=("/",)),
        cwd=str(tmp_path), timeout=60, verbosity="quiet", label="fd0"))
    assert rc == 0 and tail.strip() == "/dev/null", tail


def test_run_proc_gives_no_terminal(unconfined, fd0_is_a_terminal, tmp_path):
    r = asyncio.run(unconfined._run_proc(_FD0, cwd=str(tmp_path), timeout=60))
    assert r.returncode == 0 and r.stdout.strip() == "/dev/null", r


def test_ts_script_run_gives_no_terminal(unconfined, fd0_is_a_terminal,
                                         tmp_path):
    lr = unconfined
    r = lr._ts_script_run(_FD0, tree=tmp_path,
                          grants=lr.WorkerGrants(rw=(str(tmp_path),),
                                                 ro=("/",)),
                          timeout=60, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "/dev/null", r


# --- nor can it open one (review round 33 isolation Q1) ----------------------
# Under `nerdctl run -it` runc binds the operator's pty at `/dev/console`;
# the container's `devpts` holds it too. Here the test's own pty stands in
# for it: its slave is a terminal of the world the shim starts in, which the
# confined process must not open, whether its name is `/dev/pts/<n>` or the
# console's. What it can do with a read-only descriptor: read what the
# operator types, and turn the terminal's echo and signals off.

OPEN_PROBE = textwrap.dedent(r"""
    import json, os, select, sys, termios
    out = {}
    for name in sys.argv[1:]:
        try:
            fd = os.open(name, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK)
        except OSError as e:
            out[name] = e.errno
            continue
        r, _w, _x = select.select([fd], [], [], 0.5)
        got = os.read(fd, 64).decode(errors="replace") if r else ""
        try:
            a = termios.tcgetattr(fd)
            a[3] &= ~termios.ECHO
            termios.tcsetattr(fd, termios.TCSANOW, a)
            echo_off = True
        except termios.error:
            echo_off = False
        out[name] = {"read": got, "echo_off": echo_off}
    dev = {}
    with open("/dev/null", "wb") as f:
        dev["null"] = f.write(b"x")
    with open("/dev/zero", "rb") as f:
        dev["zero"] = f.read(4) == bytes(4)
    with open("/dev/urandom", "rb") as f:
        dev["urandom"] = len(f.read(16))
    with open("/dev/random", "rb") as f:
        dev["random"] = len(f.read(16))
    try:
        with open("/dev/full", "wb", buffering=0) as f:
            f.write(b"x")
        dev["full"] = "wrote"
    except OSError as e:
        dev["full"] = e.errno
    out["devices"] = dev
    print(json.dumps(out))
""")


def _granted_argv(leerie, tmp: Path, isolate: bool, names: list[str]
                  ) -> list[str]:
    """The real shim, with the grants leerie gives every confined process:
    `_WORKER_DEVICES` written, `_reads_outside` read."""
    rw = [str(tmp), *leerie._WORKER_DEVICES]
    ro = leerie._reads_outside(tmp / "state", tmp=False)
    return [sys.executable, "-I", "-S", str(SHIM),
            *(["--isolate"] if isolate else []),
            *[a for p in rw for a in ("--rw", p)],
            *[a for p in ro for a in ("--ro", p)], "--",
            sys.executable, "-I", "-S", "-c", OPEN_PROBE, *names]


@pytest.mark.parametrize("isolate", _modes())
def test_no_terminal_opens_under_leeries_grants(leerie, tmp_path, term,
                                                isolate):
    """The operator's pty (its `/dev/pts/<n>` name, and `/dev/console`
    where the container has one) does not open, and every device leerie
    grants still works."""
    master, slave = term
    names = [os.ttyname(slave), "/dev/console"]
    r = subprocess.run(_granted_argv(leerie, tmp_path, isolate, names),
                       cwd=tmp_path, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    for name in names:
        assert isinstance(out[name], int), (name, out[name])
    assert out["devices"] == {"null": 1, "zero": True, "urandom": 16,
                              "random": 16, "full": 28}, out
    assert termios.tcgetattr(slave)[3] & termios.ECHO
    r, _w, _x = select.select([slave], [], [], 2)
    assert r and os.read(slave, 64) == b"operator-secret\n"


def test_reads_outside_names_nothing_in_dev(leerie, tmp_path):
    """No read grant leerie derives covers `/dev`, or any of it: the
    devices are named by `_WORKER_DEVICES`, which holds no terminal but
    `/dev/tty` (no pseudo-terminal: the shim grants an isolated process's
    own)."""
    for root in (tmp_path / "state", Path("/leerie-state-absent")):
        paths = leerie._reads_outside(root, tmp=False)
        assert "/usr" in paths and "/proc" in paths
        assert not any(Path(os.path.realpath(p)).is_relative_to("/dev")
                       for p in paths), paths
    assert set(leerie._WORKER_DEVICES) == {
        "/dev/null", "/dev/zero", "/dev/full", "/dev/random",
        "/dev/urandom", "/dev/tty", "/dev/shm"}


# The shim's own check, once it is confined as the command will be: a
# console it can open is a broken invariant, refused. `CONSOLE` is pointed
# at the test's pty, which stands in for the operator's.
_CONSOLE_DRIVER = textwrap.dedent(r"""
    import importlib.util, sys
    spec = importlib.util.spec_from_file_location("ll", sys.argv[1])
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m.CONSOLE = sys.argv[2]
    sys.exit(m.main(sys.argv[3:]))
""")


@pytest.mark.parametrize("reaches", [True, False],
                         ids=["grant-reaches-console", "leeries-grants"])
def test_a_console_the_confined_shim_can_open_is_refused(leerie, tmp_path,
                                                         term, reaches):
    master, slave = term
    marker = tmp_path / "ran"
    grants = (["--ro", "/", "--rw", "/dev/pts"] if reaches else
              [a for p in leerie._reads_outside(tmp_path / "state", tmp=False)
               for a in ("--ro", p)])
    argv = [sys.executable, "-I", "-S", "-c", _CONSOLE_DRIVER, str(SHIM),
            os.ttyname(slave), "--rw", str(tmp_path), *grants, "--",
            sys.executable, "-I", "-S", "-c",
            f"open({str(marker)!r}, 'w').close()"]
    r = subprocess.run(argv, cwd=tmp_path, stdin=subprocess.DEVNULL,
                       capture_output=True, text=True, timeout=60)
    if reaches:
        assert r.returncode == 126, r.stderr
        assert "could open" in r.stderr and not marker.exists()
    else:
        assert r.returncode == 0, r.stderr
        assert marker.exists()


@pytest.mark.skipif(_ISOLATION is not None,
                    reason=f"this host cannot isolate ({_ISOLATION})")
def test_an_isolated_dev_holds_only_leeries_devices(tmp_path):
    """Even a grant that reads everything finds, in an isolated process's
    `/dev`, only the nodes leerie grants, its own `devpts` (empty but its
    `ptmx`, and a pty opens on it) and its own `/dev/shm`: no console, no
    terminal of the container's, nothing else a runtime added."""
    probe = textwrap.dedent(r"""
        import json, os, pty
        out = {"dev": sorted(os.listdir("/dev")),
               "pts": sorted(os.listdir("/dev/pts")),
               "links": {n: os.readlink("/dev/" + n) for n in
                         ("fd", "stdin", "stdout", "stderr", "ptmx")}}
        m, s = pty.openpty()
        out["pty"] = os.ttyname(s)
        print(json.dumps(out))
    """)
    r = subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), "--isolate", "--rw",
         str(tmp_path), "--rw", "/dev/null", "--ro", "/", "--",
         sys.executable, "-I", "-S", "-c", probe],
        cwd=tmp_path, stdin=subprocess.DEVNULL, capture_output=True,
        text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["dev"] == sorted(["full", "null", "random", "tty", "urandom",
                                 "zero", "fd", "stdin", "stdout", "stderr",
                                 "ptmx", "pts", "shm"]), out
    assert out["pts"] == ["ptmx"], out
    assert out["links"] == {"fd": "/proc/self/fd", "stdin": "/proc/self/fd/0",
                            "stdout": "/proc/self/fd/1",
                            "stderr": "/proc/self/fd/2", "ptmx": "pts/ptmx"}
    assert out["pty"] == "/dev/pts/0", out
