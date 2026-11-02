"""The confinement shim's isolation (`--isolate`) and held-out sandbox
(`--sandbox`), run for real (DESIGN §6 *Every confined process has its own
`/tmp`, `/dev/shm` and IPC, and sees every other mount read-only*, *A
held-out evaluation runs alone, in a world that dies with it (I3)*).

Every channel the measured design (§1.2) found open from a held-out
evaluation to a later reader is attempted by the evaluated code, and checked
afterwards from the orchestrator's own namespace — which sees everything a
later worker could, and more. Each must be refused, or land only in state
that died with the sandbox. Review round 20's H1 proof (held-out content
reaching a later confined worker through `/dev/shm` and System V IPC) is
turned around at the end: the same writes, and the worker finds nothing.

Needs a runtime that can build the sandbox: an unprivileged user namespace
with mounts (the container under the `leerie-container` profile with
`seccomp=unconfined`). A developer host or CI restricts user namespaces, so
every test here skips there (`isolation_error()`); run them in the
container, with the state under a path that is not `/tmp`
(`--basetemp=/leerie-state/...`), as the sandbox's write grants live in the
state root.
"""
from __future__ import annotations

import ctypes
import errno
import importlib.util
import json
import os
import signal
import socket
import subprocess
import sys
import textwrap
import time
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_iso", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LL = _shim_module()
_WHY = LL.isolation_error()
pytestmark = pytest.mark.skipif(
    _WHY is not None,
    reason=f"this host cannot build the sandbox ({_WHY}); run in the "
           "container under the leerie-container profile")


@pytest.fixture
def root(tmp_path):
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("the sandbox's write grants lie in the state root, not "
                    "beneath /tmp: run with --basetemp outside /tmp")
    return tmp_path


def _shim(opts: list[str], code: str, *, cwd: Path, extra_fds=(),
          timeout: float = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), *opts, "--",
         sys.executable, "-I", "-c", textwrap.dedent(code)],
        cwd=str(cwd), capture_output=True, text=True, timeout=timeout,
        pass_fds=tuple(extra_fds))


def _view(root: Path) -> Path:
    v = root / "view"
    v.mkdir()
    (v / "tree.txt").write_text("tree\n")
    return v


def _sandbox(root: Path, code: str, *, place=(), report=None,
             report_fd=None, extra_fds=()) -> subprocess.CompletedProcess:
    v = root / "view"
    opts = ["--sandbox", "--view", str(v), "--rw", str(v), "--rw",
            "/dev/null", "--ro", "/"]
    for src, rel in place:
        opts += ["--place", f"{src}={rel}"]
    if report is not None:
        opts += ["--report", report, "--report-fd", str(report_fd)]
    return _shim(opts, code, cwd=v, extra_fds=extra_fds)


def _libc():
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def _tree_state(v: Path) -> dict:
    out = {}
    for p in sorted(v.rglob("*")):
        st = os.lstat(p)
        try:
            xa = sorted(os.listxattr(p, follow_symlinks=False))
        except OSError:
            xa = []
        out[str(p.relative_to(v))] = (st.st_mode, st.st_mtime_ns, xa)
    return out


# --- the held-out sandbox: every §1.2 channel closed -----------------------


def test_files_in_dev_shm_and_tmp_die_with_the_sandbox(root):
    _view(root)
    tag = uuid.uuid4().hex[:12]
    r = _sandbox(root, f"""
        open("/dev/shm/leak-{tag}", "w").write("HELD-OUT")
        open("/tmp/leak-{tag}", "w").write("HELD-OUT")
        print("WROTE")
        """)
    assert r.returncode == 0 and "WROTE" in r.stdout, r.stderr
    assert not os.path.exists(f"/dev/shm/leak-{tag}")
    assert not os.path.exists(f"/tmp/leak-{tag}")


def test_sysv_and_posix_ipc_die_with_the_sandbox(root):
    _view(root)
    key = 0x3000000 + int(uuid.uuid4().hex[:5], 16)
    mq = f"/leak-{uuid.uuid4().hex[:8]}"
    r = _sandbox(root, f"""
        import ctypes, os
        libc = ctypes.CDLL(None, use_errno=True)
        assert libc.shmget({key}, 4096, 0o1000 | 0o666) >= 0
        assert libc.semget({key}, 1, 0o1000 | 0o666) >= 0
        assert libc.msgget({key}, 0o1000 | 0o666) >= 0
        rt = ctypes.CDLL("librt.so.1", use_errno=True)
        # A queue is refused outright (EACCES: the sandbox's mqueue mount
        # is beneath no Landlock rule) or made in the sandbox's own IPC
        # namespace; either way none is left outside.
        rt.mq_open({mq.encode()!r}, os.O_CREAT | os.O_RDWR, 0o666, None)
        print("MADE")
        """)
    assert r.returncode == 0 and "MADE" in r.stdout, r.stderr
    libc = ctypes.CDLL(None, use_errno=True)
    for get, args in ((libc.shmget, (key, 0, 0)), (libc.semget, (key, 0, 0)),
                      (libc.msgget, (key, 0))):
        assert get(*args) == -1 and ctypes.get_errno() == errno.ENOENT
    rt = ctypes.CDLL("librt.so.1", use_errno=True)
    assert rt.mq_open(mq.encode(), os.O_RDONLY) == -1
    assert ctypes.get_errno() == errno.ENOENT


def test_keyrings_refused(root):
    _view(root)
    r = _sandbox(root, """
        import ctypes, errno, os
        nr = {"x86_64": 248, "aarch64": 217}[os.uname().machine]
        libc = ctypes.CDLL(None, use_errno=True)
        libc.syscall.restype = ctypes.c_long
        for ring in (-4, -3):     # user, session
            rc = libc.syscall(ctypes.c_long(nr), b"user", b"leak", b"x",
                              ctypes.c_size_t(1), ctypes.c_long(ring))
            print(ring, rc, errno.errorcode.get(ctypes.get_errno()))
        """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["-4", "-1", "EPERM", "-3", "-1", "EPERM"]


def test_network_is_loopback_only(root):
    """Loopback TCP to an outer listener, an abstract socket, and the
    internet over TCP and UDP: none reaches anything outside."""
    _view(root)
    lst = socket.socket()
    lst.bind(("127.0.0.1", 0))
    lst.listen()
    lst.settimeout(0.5)
    port = lst.getsockname()[1]
    name = f"\0leerie-test-{uuid.uuid4().hex[:8]}".encode()
    ab = socket.socket(socket.AF_UNIX)
    ab.bind(name)
    ab.listen()
    ab.settimeout(0.5)
    try:
        r = _sandbox(root, f"""
            import socket
            def t(label, f):
                try:
                    f(); print(label, "DELIVERED")
                except OSError as e:
                    print(label, "refused", e.errno)
            t("tcp", lambda: socket.create_connection(("127.0.0.1", {port}),
                                                      2).sendall(b"HELD"))
            def abs_():
                s = socket.socket(socket.AF_UNIX); s.connect({name!r})
            t("abstract", abs_)
            t("internet-tcp", lambda: socket.create_connection(
                ("1.1.1.1", 443), 3))
            t("internet-udp", lambda: socket.socket(
                socket.AF_INET, socket.SOCK_DGRAM).sendto(b"x",
                                                          ("1.1.1.1", 53)))
            print("IFACES", [n for _i, n in socket.if_nameindex()])
            """)
        assert r.returncode == 0, r.stderr
        assert "DELIVERED" not in r.stdout
        assert "IFACES ['lo']" in r.stdout
        for s in (lst, ab):
            with pytest.raises(OSError):
                s.accept()
    finally:
        lst.close()
        ab.close()


def test_outer_processes_cannot_be_signalled(root):
    _view(root)
    outer = os.getpid()
    r = _sandbox(root, f"""
        import os
        try:
            os.kill({outer}, 0); print("ALIVE-SEEN")
        except OSError as e:
            print("refused", e.errno)
        """)
    assert r.returncode == 0, r.stderr
    assert "ALIVE-SEEN" not in r.stdout


def test_metadata_outside_the_grants_is_read_only(root):
    _view(root)
    victim = root / "victim"
    victim.write_text("data\n")
    os.utime(victim, (1577836800, 1577836800))
    before = os.stat(victim)
    r = _sandbox(root, f"""
        import os
        v = {str(victim)!r}
        for label, f in (("utime", lambda: os.utime(v, (1, 1))),
                         ("chmod", lambda: os.chmod(v, 0o777)),
                         ("setxattr", lambda: os.setxattr(v, "user.leak",
                                                          b"HELD"))):
            try:
                f(); print(label, "DONE")
            except OSError as e:
                print(label, e.errno)
        """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["utime", str(errno.EROFS), "chmod",
                                str(errno.EROFS), "setxattr",
                                str(errno.EROFS)]
    after = os.stat(victim)
    assert (after.st_mtime_ns, after.st_mode) == (before.st_mtime_ns,
                                                  before.st_mode)
    assert os.listxattr(victim) == []


def test_writes_in_the_tree_vanish(root):
    """A file, a symlink, an edit and an xattr in the evaluated tree land in
    the overlay's upper layer, on the sandbox's tmpfs."""
    v = _view(root)
    before = _tree_state(v)
    r = _sandbox(root, """
        import os
        open("LEAK", "w").write("HELD")
        os.symlink("HELD", "leak-link")
        open("tree.txt", "a").write("edited")
        os.setxattr("tree.txt", "user.leak", b"HELD")
        print(sorted(os.listdir(".")))
        """)
    assert r.returncode == 0, r.stderr
    assert "'LEAK'" in r.stdout
    assert _tree_state(v) == before
    assert (v / "tree.txt").read_text() == "tree\n"


def test_leftover_processes_end_with_the_sandbox(root):
    _view(root)
    tag = uuid.uuid4().hex[:12]
    r = _sandbox(root, f"""
        import subprocess
        subprocess.Popen(["setsid", "sh", "-c", "sleep 300; : {tag}"],
                         start_new_session=True)
        subprocess.Popen(["sh", "-c", "trap '' TERM; sleep 301; : {tag}"])
        print("STARTED")
        """)
    assert r.returncode == 0 and "STARTED" in r.stdout, r.stderr
    left = []
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            if tag.encode() in Path(f"/proc/{pid}/cmdline").read_bytes():
                left.append(pid)
        except OSError:
            pass
    assert left == []


def test_no_namespace_or_mount_from_the_evaluated_code(root):
    _view(root)
    r = _sandbox(root, """
        import ctypes, os
        libc = ctypes.CDLL(None, use_errno=True)
        print("unshare", libc.unshare(0x10000000), ctypes.get_errno())
        print("mount", libc.mount(b"tmpfs", b"/tmp", b"tmpfs", 0, None),
              ctypes.get_errno())
        """)
    assert r.returncode == 0, r.stderr
    import errno as e
    assert f"unshare -1 {e.EPERM}" in r.stdout
    assert f"mount -1 {e.EPERM}" in r.stdout


def test_placed_files_are_in_the_overlay_only(root):
    v = _view(root)
    held = root / "acceptance" / "set-1" / "held_test.py"
    held.parent.mkdir(parents=True)
    held.write_text("HELD-OUT\n")
    r = _sandbox(root, """
        import os, stat
        p = "tests/held_test.py"
        print(open(p).read().strip(), oct(stat.S_IMODE(os.stat(p).st_mode)))
        """, place=[(str(held), "tests/held_test.py")])
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["HELD-OUT", "0o444"]
    assert not (v / "tests").exists()


def test_a_set_is_never_placed_over_the_trees_own_file(root):
    v = _view(root)
    held = root / "held.txt"
    held.write_text("HELD\n")
    r = _sandbox(root, "print('RAN')", place=[(str(held), "tree.txt")])
    assert r.returncode == 126
    assert "RAN" not in r.stdout
    assert "tree.txt" in r.stderr
    assert (v / "tree.txt").read_text() == "tree\n"


def test_report_exit_code_and_signal_come_out(root):
    """The runner's report leaves through the descriptor leerie holds; the
    exit code and a terminating signal are the command's."""
    _view(root)
    rep = root / "report.out"
    with open(rep, "w+b") as f:
        r = _sandbox(root, """
            import json, sys
            json.dump({"failed": ["case"]}, open("report.json", "w"))
            sys.exit(3)
            """, report=str(root / "view" / "report.json"),
            report_fd=f.fileno(), extra_fds=(f.fileno(),))
    assert r.returncode == 3, r.stderr
    assert json.loads(rep.read_text()) == {"failed": ["case"]}
    assert not (root / "view" / "report.json").exists()
    r = _sandbox(root, "import os, signal; os.kill(os.getpid(), "
                       "signal.SIGTERM)")
    assert r.returncode == -signal.SIGTERM, r.stderr


def test_a_stop_signal_ends_the_whole_namespace(root):
    _view(root)
    tag = uuid.uuid4().hex[:12]
    v = root / "view"
    p = subprocess.Popen(
        [sys.executable, "-I", "-S", str(SHIM), "--sandbox", "--view", str(v),
         "--rw", str(v), "--ro", "/", "--",
         "sh", "-c", f"setsid sh -c 'sleep 300; : {tag}' & "
                     f"sleep 302; : {tag}"],
        cwd=str(v), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.5)
    p.send_signal(signal.SIGTERM)
    p.wait(timeout=20)
    assert p.returncode == -signal.SIGTERM
    for pid in filter(str.isdigit, os.listdir("/proc")):
        try:
            assert tag.encode() not in Path(
                f"/proc/{pid}/cmdline").read_bytes()
        except OSError:
            pass


# --- isolation for every confined process (--isolate) ----------------------


def _isolate(root: Path, code: str, rw=()) -> subprocess.CompletedProcess:
    opts = ["--isolate", "--rw", str(root / "tree"), "--rw", "/dev/null",
            "--ro", "/"]
    for p in rw:
        opts += ["--rw", str(p)]
    return _shim(opts, code, cwd=root / "tree")


def test_isolate_gives_each_command_its_own_tmp_and_keeps_its_grants(root):
    (root / "tree").mkdir()
    tag = uuid.uuid4().hex[:12]
    keep = Path(f"/tmp/l/test-{tag}")
    keep.mkdir(parents=True)
    try:
        r = _isolate(root, f"""
            import os
            open("/tmp/recon-{tag}", "w").write("fixed /tmp path works")
            open("/dev/shm/x-{tag}", "w").write("shm")
            open("written.txt", "w").write("tree")
            open("{keep}/carried.txt", "w").write("grant beneath /tmp")
            print(sorted(os.listdir("/tmp")))
            """, rw=[keep])
        assert r.returncode == 0, r.stderr
        assert (root / "tree" / "written.txt").read_text() == "tree"
        assert (keep / "carried.txt").read_text() == "grant beneath /tmp"
        assert not os.path.exists(f"/tmp/recon-{tag}")
        assert not os.path.exists(f"/dev/shm/x-{tag}")
        # The next command's /tmp holds nothing of the last one's.
        r2 = _isolate(root, f"""
            import os
            print(os.path.exists("/tmp/recon-{tag}"))
            """, rw=[keep])
        assert r2.stdout.strip() == "False", r2.stderr
    finally:
        for p in keep.iterdir():
            p.unlink()
        keep.rmdir()


def test_isolate_makes_metadata_outside_the_grants_read_only(root):
    (root / "tree").mkdir()
    victim = root / "victim"
    victim.write_text("x\n")
    r = _isolate(root, f"""
        import os
        try:
            os.chmod({str(victim)!r}, 0o777); print("DONE")
        except OSError as e:
            print(e.errno)
        """)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == str(errno.EROFS)
    assert oct(victim.stat().st_mode & 0o777) != "0o777"


def test_isolate_root_is_the_callers_and_ipc_is_its_own(root):
    (root / "tree").mkdir()
    r = _isolate(root, """
        import os
        print(os.stat("/").st_uid == os.getuid(),
              os.readlink("/proc/self/ns/ipc"))
        """)
    assert r.returncode == 0, r.stderr
    own_root, ipc = r.stdout.split()
    assert own_root == "True"
    assert ipc != os.readlink("/proc/self/ns/ipc")


# --- review round 20's H1, turned around -----------------------------------


def test_r20_h1_held_out_content_does_not_reach_a_later_worker(root):
    """The H1 proof's shape: the held-out run copies its own text into
    `/dev/shm` and a System V segment; a later worker, confined as a worker
    is (`--isolate`), looks for both. It used to read the held-out text
    twice; now there is nothing to read."""
    v = _view(root)
    (root / "tree").mkdir()
    nonce = uuid.uuid4().hex[:12]
    secret = f"HELD_OUT_ASSERTION_{nonce}"
    key = 0x2000000 + int(nonce[:6], 16)
    held = root / "acceptance" / "set-1" / "held.py"
    held.parent.mkdir(parents=True)
    held.write_text(f"# {secret}\n")
    r = _sandbox(root, f"""
        import ctypes, shutil
        src = open("held.py", "rb").read()
        shutil.copy("held.py", "/dev/shm/r20i-{nonce}")
        libc = ctypes.CDLL(None, use_errno=True)
        libc.shmat.restype = ctypes.c_void_p
        sid = libc.shmget({key}, 4096, 0o1000 | 0o600)
        addr = libc.shmat(sid, None, 0)
        ctypes.memmove(addr, src, len(src))
        print("LEFT", sid >= 0)
        """, place=[(str(held), "held.py")])
    assert r.returncode == 0 and "LEFT True" in r.stdout, r.stderr
    w = _isolate(root, f"""
        import ctypes, os
        print("SHM", os.path.exists("/dev/shm/r20i-{nonce}"))
        libc = ctypes.CDLL(None, use_errno=True)
        print("SYSV", libc.shmget({key}, 0, 0))
        """)
    assert w.returncode == 0, w.stderr
    assert secret not in w.stdout
    assert w.stdout.split() == ["SHM", "False", "SYSV", "-1"]
    assert not os.path.exists(f"/dev/shm/r20i-{nonce}")
    assert ctypes.CDLL(None).shmget(key, 0, 0) == -1
    assert not (v / "held.py").exists()
