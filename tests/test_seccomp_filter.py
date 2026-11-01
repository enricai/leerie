"""The seccomp filter every confined process installs (DESIGN §6 *Every
confined process has its own `/tmp`, `/dev/shm` and IPC* — *The seccomp
filter*; `scripts/landlock_exec.py` `install_seccomp`).

Two halves:
- the program for each architecture, executed by a small classic-BPF
  interpreter over synthetic `seccomp_data`: every call the design refuses
  answers EPERM, `clone3` ENOSYS, `clone` with a `CLONE_NEW*` flag EPERM, an
  ordinary `clone` and every other call ALLOW, another architecture KILL,
  x32 EPERM — so the aarch64 table (Colima on Apple Silicon) is checked here
  on an x86_64 host too;
- the real filter on this host, through the shim, without isolation: the
  refused calls fail, and threads, `subprocess` and Node's worker threads
  still run.
"""
from __future__ import annotations

import errno
import importlib.util
import shutil
import struct
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_sec", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LL = _shim_module()
ALLOW = 0x7FFF0000
KILL = 0x80000000


def _errno(e: int) -> int:
    return 0x00050000 | e


def _run_bpf(prog: bytes, *, nr: int, arch: int, args0: int = 0) -> int:
    """Execute the subset of classic BPF the program uses (LD W ABS, JEQ K,
    JGE K, JSET K, RET K) over a `seccomp_data`."""
    data = struct.pack("iIQ6Q", nr, arch, 0, args0, 0, 0, 0, 0, 0)
    ins = [struct.unpack("HBBI", prog[i:i + 8])
           for i in range(0, len(prog), 8)]
    pc, acc = 0, 0
    while True:
        code, jt, jf, k = ins[pc]
        if code == 0x20:
            acc = struct.unpack_from("I", data, k)[0]
            pc += 1
        elif code == 0x06:
            return k
        else:
            cond = {0x15: acc == k, 0x35: acc >= k,
                    0x45: bool(acc & k)}[code]
            pc += 1 + (jt if cond else jf)


@pytest.mark.parametrize("arch", sorted(LL.SECCOMP_TABLES))
def test_program_refuses_exactly_what_the_design_lists(arch):
    t = LL.SECCOMP_TABLES[arch]
    prog = LL.seccomp_program(arch)
    aa = t["audit_arch"]
    assert set(t["eperm"]) == {
        "unshare", "setns", "mount", "umount2", "pivot_root", "open_tree",
        "move_mount", "fsopen", "fsconfig", "fsmount", "fspick",
        "mount_setattr", "add_key", "request_key", "keyctl"}
    for name, nr in t["eperm"].items():
        assert _run_bpf(prog, nr=nr, arch=aa) == _errno(errno.EPERM), name
    assert _run_bpf(prog, nr=t["clone3"], arch=aa) == _errno(errno.ENOSYS)
    for flag in (0x00020000, 0x02000000, 0x04000000, 0x08000000,
                 0x10000000, 0x20000000, 0x40000000):
        assert (_run_bpf(prog, nr=t["clone"], arch=aa, args0=flag | 0x11)
                == _errno(errno.EPERM)), hex(flag)
    # An ordinary fork/thread clone, and calls the filter does not name.
    assert _run_bpf(prog, nr=t["clone"], arch=aa, args0=0x3D0F00) == ALLOW
    named = set(t["eperm"].values()) | {t["clone"], t["clone3"]}
    for nr in range(0, 460):
        if nr not in named:
            assert _run_bpf(prog, nr=nr, arch=aa) == ALLOW, nr
    assert _run_bpf(prog, nr=0, arch=0x40000003) == KILL    # i386
    if t["x32"]:
        assert (_run_bpf(prog, nr=0x40000000 | 1, arch=aa)
                == _errno(errno.EPERM))


def test_aarch64_numbers_are_the_kernels():
    """aarch64's generic table, as the design lists it; the new mount API
    and clone3 share x86_64's numbers."""
    a, x = LL.SECCOMP_TABLES["aarch64"], LL.SECCOMP_TABLES["x86_64"]
    assert (a["eperm"]["unshare"], a["eperm"]["setns"], a["eperm"]["mount"],
            a["eperm"]["umount2"], a["eperm"]["pivot_root"], a["clone"]) \
        == (97, 268, 40, 39, 41, 220)
    assert [a["eperm"][k] for k in ("add_key", "request_key", "keyctl")] \
        == [217, 218, 219]
    for k in ("open_tree", "move_mount", "fsopen", "fsconfig", "fsmount",
              "fspick", "mount_setattr"):
        assert a["eperm"][k] == x["eperm"][k]
    assert a["clone3"] == x["clone3"] == 435


@pytest.mark.skipif(LL._arch() not in LL.SECCOMP_TABLES
                    or not sys.platform.startswith("linux"),
                    reason="no seccomp table for this machine")
def test_real_filter_refuses_and_programs_still_run(tmp_path):
    probe = (
        "import ctypes, errno, os, subprocess, threading\n"
        "libc = ctypes.CDLL(None, use_errno=True)\n"
        "libc.syscall.restype = ctypes.c_long\n"
        "print('unshare', libc.unshare(0x10000000), ctypes.get_errno())\n"
        "print('mount', libc.mount(b'none', b'/', b'tmpfs', 0, None), "
        "ctypes.get_errno())\n"
        "nr = {'x86_64': 248, 'aarch64': 217}[os.uname().machine]\n"
        "print('add_key', libc.syscall(ctypes.c_long(nr), b'user', b'k', "
        "b'x', ctypes.c_size_t(1), ctypes.c_long(-3)), ctypes.get_errno())\n"
        "t = threading.Thread(target=lambda: None); t.start(); t.join()\n"
        "print('subprocess', subprocess.run(['true']).returncode)\n"
        "print('Seccomp', [l.split()[1] for l in open('/proc/self/status')"
        " if l.startswith('Seccomp:')][0])\n")
    r = subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), "--rw", str(tmp_path),
         "--ro", "/", "--", sys.executable, "-I", "-c", probe],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    lines = dict(ln.split(" ", 1) for ln in r.stdout.splitlines())
    assert lines["unshare"] == f"-1 {errno.EPERM}"
    assert lines["mount"] == f"-1 {errno.EPERM}"
    assert lines["add_key"] == f"-1 {errno.EPERM}"
    assert lines["subprocess"] == "0"
    assert lines["Seccomp"] == "2"


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_node_worker_threads_run_under_the_filter(tmp_path):
    r = subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), "--rw", str(tmp_path),
         "--ro", "/", "--", "node", "-e",
         "const {Worker}=require('worker_threads');"
         "new Worker('require(\"worker_threads\").parentPort"
         ".postMessage(7)',{eval:true}).on('message',m=>console.log('W'+m))"],
        capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.strip() == "W7"


def test_no_table_is_a_refusal_not_an_unfiltered_run(monkeypatch):
    monkeypatch.setattr(LL, "_arch", lambda: "riscv64")
    with pytest.raises(OSError, match="no seccomp table"):
        LL.install_seccomp()
