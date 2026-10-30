#!/usr/bin/env python3
"""Confine a worker with Landlock, then exec it (DESIGN §6 *A worker writes
only where it is granted (I5)*).

Usage: landlock_exec.py [--rw PATH]... [--ro PATH]... -- CMD [ARGS...]

Every write-class right (write, truncate, create, remove, rename and link)
and every read right (read a file, list a directory) is handled: allowed
only beneath a `--rw` path (read and write) or a `--ro` path (read). The
ruleset is inherited by every process CMD starts and cannot be lifted.
Exits 126, naming the reason, when the kernel cannot enforce it — never
runs CMD unconfined. A grant that does not exist is skipped (nothing there
to grant).

Stdlib only and imported by nothing heavier: it starts every worker. The
orchestrator imports `abi()` and `MIN_ABI` from here for its preflight, so
the requirement has one spelling."""
from __future__ import annotations

import ctypes
import os
import stat
import sys

# Landlock's syscalls share one number on every architecture (they were
# added after the syscall tables were unified).
_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38

EXECUTE = 1 << 0
WRITE_FILE = 1 << 1
READ_FILE = 1 << 2
READ_DIR = 1 << 3
REMOVE_DIR = 1 << 4
REMOVE_FILE = 1 << 5
MAKE_CHAR = 1 << 6
MAKE_DIR = 1 << 7
MAKE_REG = 1 << 8
MAKE_SOCK = 1 << 9
MAKE_FIFO = 1 << 10
MAKE_BLOCK = 1 << 11
MAKE_SYM = 1 << 12
REFER = 1 << 13          # ABI 2: rename and link across directories
TRUNCATE = 1 << 14       # ABI 3

# ABI 2 lets a ruleset allow a rename or hard link between two directories
# at all (ABI 1 refuses every one, which breaks an ordinary `mv` in a
# worktree); one out of or into a grant is refused either way (EXDEV,
# measured). ABI 3 handles `truncate(2)`, unconfined before.
MIN_ABI = 3

READ = READ_FILE | READ_DIR
WRITE = (WRITE_FILE | REMOVE_DIR | REMOVE_FILE | MAKE_CHAR | MAKE_DIR
         | MAKE_REG | MAKE_SOCK | MAKE_FIFO | MAKE_BLOCK | MAKE_SYM | REFER
         | TRUNCATE)
HANDLED = READ | WRITE
# The rights Landlock accepts on a rule for a file that is not a directory.
_FILE_RIGHTS = EXECUTE | WRITE_FILE | READ_FILE | TRUNCATE


class _Attr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _pack_ = 1
    _layout_ = "ms"
    _fields_ = [("allowed_access", ctypes.c_uint64),
                ("parent_fd", ctypes.c_int32)]


def _libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    return libc


def abi() -> int:
    """The kernel's Landlock ABI version; 0 when it has none (not Linux, the
    LSM not enabled, or a seccomp profile that refuses the syscall)."""
    if not sys.platform.startswith("linux"):
        return 0
    try:
        v = _libc().syscall(_SYS_CREATE, None, ctypes.c_size_t(0),
                            ctypes.c_uint32(_CREATE_RULESET_VERSION))
    except (OSError, AttributeError):
        return 0
    return int(v) if v > 0 else 0


def confine(rw: list[str], ro: list[str]) -> None:
    """Restrict this process to `rw` (read and write) and `ro` (read).
    Raises OSError naming the step that failed."""
    v = abi()
    if v < MIN_ABI:
        raise OSError(f"the kernel's Landlock ABI is {v}; leerie needs "
                      f"{MIN_ABI} or later to confine a worker")
    libc = _libc()
    attr = _Attr(HANDLED)
    fd = libc.syscall(_SYS_CREATE, ctypes.byref(attr),
                      ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(f"landlock_create_ruleset: errno {ctypes.get_errno()}")
    try:
        for paths, rights in ((rw, READ | WRITE), (ro, READ)):
            for p in paths:
                try:
                    pfd = os.open(p, os.O_PATH | os.O_CLOEXEC)
                except FileNotFoundError:
                    continue
                try:
                    allowed = rights
                    if not stat.S_ISDIR(os.fstat(pfd).st_mode):
                        allowed &= _FILE_RIGHTS
                    rule = _PathBeneath(allowed, pfd)
                    if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(fd),
                                    ctypes.c_int(_RULE_PATH_BENEATH),
                                    ctypes.byref(rule),
                                    ctypes.c_uint32(0)) != 0:
                        raise OSError(f"landlock_add_rule {p}: errno "
                                      f"{ctypes.get_errno()}")
                finally:
                    os.close(pfd)
        if libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
            raise OSError(f"prctl(PR_SET_NO_NEW_PRIVS): errno "
                          f"{ctypes.get_errno()}")
        if libc.syscall(_SYS_RESTRICT, ctypes.c_int(fd),
                        ctypes.c_uint32(0)) != 0:
            raise OSError(f"landlock_restrict_self: errno "
                          f"{ctypes.get_errno()}")
    finally:
        os.close(fd)


def main(argv: list[str]) -> int:
    if "--" not in argv:
        print("usage: landlock_exec.py [--rw PATH]... [--ro PATH]... -- CMD",
              file=sys.stderr)
        return 2
    sep = argv.index("--")
    rw: list[str] = []
    ro: list[str] = []
    opts = argv[:sep]
    for i in range(0, len(opts), 2):
        if opts[i] not in ("--rw", "--ro") or i + 1 >= len(opts):
            print(f"landlock_exec.py: bad option {opts[i]!r}", file=sys.stderr)
            return 2
        (rw if opts[i] == "--rw" else ro).append(opts[i + 1])
    cmd = argv[sep + 1:]
    if not cmd:
        print("landlock_exec.py: no command", file=sys.stderr)
        return 2
    try:
        confine(rw, ro)
    except OSError as e:
        print(f"leerie: cannot confine the worker, so it does not run: {e} "
              "(DESIGN §6 *A worker writes only where it is granted*)",
              file=sys.stderr)
        return 126
    try:
        os.execvp(cmd[0], cmd)
    except OSError as e:
        print(f"landlock_exec.py: exec {cmd[0]}: {e}", file=sys.stderr)
        return 127
    return 127  # unreachable: execvp replaces this process


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
