#!/usr/bin/env python3
"""Confine a command with Landlock, run it, and end every process it leaves
(DESIGN §6 *A worker writes only where it is granted (I5)*, *A confined
command's processes end with it*).

Usage: landlock_exec.py [--rw PATH]... [--ro PATH]... -- CMD [ARGS...]

Every write-class right (write, truncate, create, remove, rename and link)
and every read right (read a file, list a directory) is handled: allowed
only beneath a `--rw` path (read and write) or a `--ro` path (read). The
ruleset is inherited by every process CMD starts and cannot be lifted.
Exits 126, naming the reason, when the kernel cannot enforce it — never
runs CMD unconfined. A grant that does not exist is skipped (nothing there
to grant).

The shim stays as CMD's parent: it makes itself a child subreaper
(`PR_SET_CHILD_SUBREAPER`), so every process CMD starts that loses its
parent — a daemon under `setsid`, a backgrounded tool — is reparented to the
shim rather than escaping to the orchestrator or init. When CMD exits, or the
shim is told to stop (SIGTERM, SIGINT, SIGHUP), it ends every descendant
(`end_descendants`: SIGTERM, a grace period, then SIGKILL) and exits only once
`waitpid` reports it has no child left, which the kernel guarantees means no
descendant is left either. It then exits as CMD did: CMD's exit code, or
CMD's terminating signal raised on itself.

Stdlib only and imported by nothing heavier: it starts every worker. The
orchestrator imports `abi()` and `MIN_ABI` from here for its preflight, so
the requirement has one spelling."""
from __future__ import annotations

import ctypes
import os
import signal
import stat
import sys
import time

# Landlock's syscalls share one number on every architecture (they were
# added after the syscall tables were unified).
_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_CHILD_SUBREAPER = 36

# How long the descendants of a command that exited get between SIGTERM and
# SIGKILL: long enough for a test database to shut down cleanly (and remove
# its PID file, which a SIGKILL leaves behind). A shim told to stop gets the
# shorter one, inside the orchestrator's own grace before it SIGKILLs the
# shim's group (`_PROC_TREE_GRACE_SEC`, 2 s).
END_GRACE_SEC = 2.0
STOP_GRACE_SEC = 1.0
_STOP_SIGNALS = (signal.SIGTERM, signal.SIGINT, signal.SIGHUP)

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


def descendants(root: int) -> set[int]:
    """Every live process beneath `root` by parent links, read from
    `/proc/<pid>/stat` (the field after the command name's closing
    parenthesis is the state, the next the parent pid)."""
    children: dict[int, list[int]] = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return set()
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/stat", "rb") as f:
                rest = f.read().rsplit(b")", 1)[1].split()
            children.setdefault(int(rest[1]), []).append(int(name))
        except (OSError, IndexError, ValueError):
            continue
    out: set[int] = set()
    todo = [root]
    while todo:
        for c in children.get(todo.pop(), ()):
            if c not in out:
                out.add(c)
                todo.append(c)
    return out


def _signal_all(pids: set[int], sig: int) -> None:
    for pid in pids:
        try:
            os.kill(pid, sig)
        except OSError:
            pass


def _reap_nohang() -> bool:
    """Reap every exited child; False once the shim has no child at all."""
    while True:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            return False
        except InterruptedError:
            continue
        if pid == 0:
            return True


def end_descendants(grace: float) -> None:
    """End every descendant: SIGTERM, then SIGKILL after `grace` seconds,
    until `waitpid` says no child is left. A subreaper's descendants are all
    reparented to it as their parents die, so no child left means no
    descendant left: the confirmation is the kernel's."""
    me = os.getpid()
    deadline: float | None = None
    while _reap_nohang():
        live = descendants(me)
        now = time.monotonic()
        if deadline is None:
            _signal_all(live, signal.SIGTERM)
            deadline = now + grace
        elif now >= deadline:
            _signal_all(live, signal.SIGKILL)
        time.sleep(0.02)


# The signals CPython sets to SIG_IGN at startup (SIGXFZ exists on some
# platforms only). `exec` keeps an ignored signal ignored, so a command a
# Python process `exec`s without resetting them starts with them ignored:
# `yes | head` then ends `yes` with EPIPE and "Broken pipe" (status 1), not
# SIGPIPE (141), and a non-interactive shell cannot un-ignore them. The same
# set `subprocess`'s `restore_signals` resets (DESIGN §6 *A command leerie
# runs starts with the signal dispositions it would have outside leerie*).
PYTHON_IGNORED_SIGNALS = ("SIGPIPE", "SIGXFZ", "SIGXFSZ")


def restore_default_signals() -> None:
    """Give every signal Python ignored at startup its default action again.
    Called in a forked child just before it `exec`s a command."""
    for name in PYTHON_IGNORED_SIGNALS:
        sig = getattr(signal, name, None)
        if sig is not None:
            signal.signal(sig, signal.SIG_DFL)


def _die_of(sig: int) -> int:
    """Raise `sig` on the shim itself with its default action, so the caller
    sees the return code it would have seen without the shim (negative for
    a signal). No core file: the command's own was already written."""
    try:
        import resource  # noqa: PLC0415
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    except (ImportError, OSError, ValueError):
        pass
    if sig not in (signal.SIGKILL, signal.SIGSTOP):
        signal.signal(sig, signal.SIG_DFL)
        signal.pthread_sigmask(signal.SIG_UNBLOCK, {sig})
    os.kill(os.getpid(), sig)
    return 128 + sig


def run(cmd: list[str]) -> int:
    """Run `cmd` as a child of this subreaper, then end what it left.
    SIGCHLD and the stop signals are blocked and taken with `sigwaitinfo`,
    so none is lost between a check and the wait; the child gets the mask
    it was started with, and the dispositions it would have outside a
    Python parent (`restore_default_signals`)."""
    libc = _libc()
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        print(f"leerie: cannot make the shim a subreaper (errno "
              f"{ctypes.get_errno()}), so the command does not run "
              "(DESIGN §6 *A confined command's processes end with it*)",
              file=sys.stderr)
        return 126
    watched = {signal.SIGCHLD, *_STOP_SIGNALS}
    old = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    child = os.fork()
    if child == 0:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
        restore_default_signals()
        try:
            os.execvp(cmd[0], cmd)
        except OSError as e:
            print(f"landlock_exec.py: exec {cmd[0]}: {e}", file=sys.stderr)
        os._exit(127)
    status: int | None = None
    stopped: int | None = None
    while status is None and stopped is None:
        try:
            while True:
                pid, st = os.waitpid(-1, os.WNOHANG)
                if pid == 0:
                    break
                if pid == child:
                    status = st
                    break
        except ChildProcessError:
            break
        if status is None:
            info = signal.sigwaitinfo(watched)
            if info.si_signo in _STOP_SIGNALS:
                stopped = info.si_signo
    end_descendants(STOP_GRACE_SEC if stopped is not None
                    else END_GRACE_SEC)
    if stopped is not None:
        return _die_of(stopped)
    if status is None:
        return 1
    if os.WIFSIGNALED(status):
        return _die_of(os.WTERMSIG(status))
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


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
        # `/proc`, read: the shim's own need — `descendants` reads it to find
        # what the command left. Every grant leerie derives reads it anyway
        # (it is outside the state root).
        confine(rw, ro + ["/proc"])
    except OSError as e:
        print(f"leerie: cannot confine the worker, so it does not run: {e} "
              "(DESIGN §6 *A worker writes only where it is granted*)",
              file=sys.stderr)
        return 126
    return run(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
