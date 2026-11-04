"""The egress/forward relay can carry no forged refusal (DESIGN §6 *A
confinement failure stops the run; it is never a result*; review round 40,
isolation F1).

The shim is made non-dumpable so that no command can open
`/proc/<shim>/fd/N` and write the refusal descriptor. The relay process is a
fork of the shim without an exec, and lives as long as the command: it was
forked before `run()` made the shim non-dumpable, so it was dumpable, and it
kept the refusal descriptor for the command's whole life (measured in the
container: relay readable through `/proc/<relay>/environ`, refusal pipe at
its fd 5). The relay is now forked from a non-dumpable shim, and closes the
refusal and taken descriptors, which it never writes.
"""
from __future__ import annotations

import contextlib
import importlib.util
import os
import socket
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_relay", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LL = _shim_module()
_WHY = LL.isolation_error() if LL.abi() >= LL.MIN_ABI else "no Landlock"
needs_isolation = pytest.mark.skipif(
    _WHY is not None,
    reason=f"this host cannot isolate ({_WHY}); run in the container under "
           "the leerie-container profile")


def _children(pid: int) -> list[int]:
    """Pids whose parent is `pid`, from each one's own `stat` (readable
    whatever the parent's dumpable state)."""
    kids = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            with contextlib.suppress(OSError):
                stat = Path(f"/proc/{d}/stat").read_text()
                if int(stat.rsplit(")", 1)[1].split()[1]) == pid:
                    kids.append(int(d))
    return kids


def _held(pid: int, inodes: set[int]) -> set[int]:
    held = set()
    for f in os.listdir(f"/proc/{pid}/fd"):
        try:
            ino = os.stat(f"/proc/{pid}/fd/{f}").st_ino
        except OSError:
            continue
        if ino in inodes:
            held.add(ino)
    return held


def test_the_relay_closes_the_refusal_and_taken_descriptors(tmp_path):
    """The real `serve_relays`, in a process of its own given both
    descriptors as the shim holds them: once it relays a connection, it
    holds neither."""
    target = tmp_path / "t.sock"
    up = socket.socket(socket.AF_UNIX)
    up.bind(str(target))
    up.listen(1)
    refusal_r, refusal_w = os.pipe()
    taken_r, taken_w = os.pipe()
    inodes = {os.fstat(refusal_w).st_ino, os.fstat(taken_w).st_ino}
    code = textwrap.dedent(f"""
        import importlib.util, socket, sys
        spec = importlib.util.spec_from_file_location("ll", {str(SHIM)!r})
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        m._REFUSAL_FD, m._TAKEN_FD = {refusal_w}, {taken_w}
        lst = socket.socket()
        lst.bind(("127.0.0.1", 0))
        lst.listen(1)
        print(lst.getsockname()[1], flush=True)
        m.serve_relays([(lst, {str(target)!r})])
        """)
    p = subprocess.Popen([sys.executable, "-I", "-S", "-c", code],
                         pass_fds=(refusal_w, taken_w), stdin=subprocess.PIPE,
                         stdout=subprocess.PIPE, text=True)
    os.close(refusal_w)
    os.close(taken_w)
    try:
        port = int(p.stdout.readline())
        c = socket.create_connection(("127.0.0.1", port), 10)
        up.settimeout(30)
        conn, _ = up.accept()       # the relay reached its target
        assert _held(p.pid, inodes) == set()
        conn.close()
        c.close()
    finally:
        p.kill()
        p.wait()
        up.close()
        for fd in (refusal_r, taken_r):
            os.close(fd)


def test_the_shim_is_non_dumpable_before_it_forks_the_relay(monkeypatch):
    """`main()`'s own order, with the namespace and Landlock steps stood in
    for (this host need not isolate): `_undumpable` before `start_relays`,
    and a failure to become non-dumpable refuses before any relay exists."""
    calls: list[str] = []

    class _Listener:
        def getsockname(self):
            return ("127.0.0.1", 4242)

        def close(self):
            calls.append("close")

    monkeypatch.setattr(os, "environ", dict(os.environ))
    monkeypatch.setattr(LL, "leave_terminal", lambda: None)
    monkeypatch.setattr(LL, "isolate", lambda *a, **k: (0, []))
    monkeypatch.setattr(LL, "join_session_keyring", lambda: None)
    monkeypatch.setattr(LL, "relay_listener", lambda port: _Listener())
    monkeypatch.setattr(LL, "_confine_or_126", lambda rw, ro: None)
    monkeypatch.setattr(LL, "_undumpable",
                        lambda: calls.append("undumpable"))
    monkeypatch.setattr(LL, "start_relays",
                        lambda relays: calls.append("start_relays"))
    monkeypatch.setattr(LL, "run", lambda cmd, relay, enter: 0)
    argv = ["--isolate", "--egress", "/e.sock", "--rw", "/dev/null", "--ro",
            "/", "--", "true"]
    assert LL.main(argv) == 0
    assert calls == ["undumpable", "start_relays"]

    calls.clear()
    monkeypatch.setattr(LL, "_undumpable",
                        lambda: "prctl(PR_SET_DUMPABLE): errno 1")
    assert LL.main(argv) == 126
    assert calls == ["close"]


@needs_isolation
def test_the_running_relay_is_non_dumpable(tmp_path):
    """The real shim, `--isolate --egress`, with a refusal descriptor
    (`--taken-fd` is `--sandbox`'s alone): while the
    command runs, its parent (this process, which owns the shim's user
    namespace) can read a dumpable relay's `/proc/<pid>/environ` and not a
    non-dumpable one's (measured in the container before the fix: the
    shim's refused, the relay's read)."""
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("grants beneath /tmp are carried, not bound: run with "
                    "--basetemp outside /tmp")
    egress = tmp_path / "egress.sock"
    srv = socket.socket(socket.AF_UNIX)
    srv.bind(str(egress))
    srv.listen(1)
    refusal_r, refusal_w = os.pipe()
    go = tmp_path / "go"
    p = subprocess.Popen(
        [sys.executable, "-I", "-S", str(SHIM), "--refusal-fd",
         str(refusal_w), "--isolate", "--egress",
         str(egress), "--rw", "/dev/null", "--rw", str(tmp_path), "--ro",
         "/", "--", sys.executable, "-I", "-S", "-c",
         f"import os, time\nwhile not os.path.exists({str(go)!r}): "
         "time.sleep(0.05)\n"],
        pass_fds=(refusal_w,), stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    os.close(refusal_w)
    try:
        relay = None
        deadline = time.monotonic() + 30
        while relay is None and time.monotonic() < deadline:
            assert p.poll() is None, p.stderr.read()
            for k in _children(p.pid):
                with contextlib.suppress(OSError):
                    if b"landlock_exec.py" in Path(
                            f"/proc/{k}/cmdline").read_bytes():
                        relay = k
            time.sleep(0.05)
        assert relay is not None
        with pytest.raises(PermissionError):
            Path(f"/proc/{relay}/environ").read_bytes()
    finally:
        go.touch()
        _out, err = p.communicate(timeout=60)
        srv.close()
        os.close(refusal_r)
    assert p.returncode == 0, err
