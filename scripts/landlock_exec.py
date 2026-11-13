#!/usr/bin/env python3
"""Confine a command with Landlock, run it, and end every process it leaves
(DESIGN §6 *A worker writes only where it is granted (I5)*, *A confined
command's processes end with it*).

Usage: landlock_exec.py [--isolate [--egress SOCK] [--forward PORT=SOCK]...
                         [--link-root DIR]
                         [--worker-root DIR [--cli]]
                         [--home DIR --home-bytes N [--home-layer DIR]...
                          [--home-upper DIR]]]
                         [--refusal-fd N]
                        [--rw PATH]... [--ro PATH]... -- CMD [ARGS...]
       landlock_exec.py --sandbox --view TREE [--place SRC=REL]...
                        [--replace SRC=REL]... [--remove REL]...
                        [--report PATH --report-fd N] [--worker-root DIR]
                        [--home DIR --home-bytes N [--home-layer DIR]...]
                        [--refusal-fd N] [--taken-fd N]
                        [--rw PATH]... [--ro PATH]... -- CMD [ARGS...]

`--link-root DIR` (`--isolate`): the write grants beneath DIR are one
mount rooted at DIR, everything else beneath it read-only, so a hard link
between two of them works (`bind_grants`). DIR holds only trees and the
stores they install from (DESIGN §6 *The write grants on one mount are one
mount*); every other grant is its own mount.

`--home DIR --home-bytes N [--home-layer DIR]...` (with either): the
process's own home (DESIGN §6 *Every confined process has its own home*):
an overlay on DIR whose lower layers are each `--home-layer` (first on top)
and DIR itself, and whose upper is a `tmpfs` of N bytes in the process's
own mount namespace, so the kernel frees it on every exit. A grant beneath
DIR is bound from the real directory over it. DIR is then written like a
grant. The shim samples the home while the command runs; a home that fills
is a refusal (`--refusal-fd`), never the command's result. `--home-upper DIR`
(`--isolate` only): the upper is DIR on disk, its work directory
`DIR.work` beside it, and it is kept (a tree's install writes its tree
home layer so); a home on disk is not watched.

`--refusal-fd N`: every refusal (each exit 126 the shim makes itself, never
CMD's) also writes its reason to descriptor N, which the shim makes
close-on-exec before anything else, so CMD never holds it (DESIGN §6 *A
confinement failure stops the run; it is never a result*). The caller
tells the shim's refusal from CMD's own exit 126 by that descriptor, never
by the status or the text. The shim makes itself non-dumpable before CMD
starts, so CMD cannot reopen N through `/proc/<shim>/fd/N` either.

`--taken-fd N` (`--sandbox`): a held-out file that cannot be placed because
the tree already has its path — a file there, or a link or a non-directory
on its way — is a fact about the tree, not a refusal. The shim writes that
reason to N (never to the refusal descriptor), exits 126 and does not run
CMD.

`--replace SRC=REL` and `--remove REL` (`--sandbox`): an existing test file's
base form is run in place of its final form (DESIGN §8 *What an existing test
pins on the base holds on the tree that ships*). `--replace` writes SRC over
the regular file at REL (or creates it), `--remove` deletes the regular file
at REL (nothing when there is none); both in the overlay, following no link,
and a link, a directory or anything but a regular file at REL is a taken path
like `--place`'s.

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

Every confined process also gets a seccomp filter that refuses every
namespace, mount and keyring call (`install_seccomp`), and from Landlock
ABI 6 the abstract-socket and signal scopes.

`--isolate` first builds the isolation every confined process gets in a run
(DESIGN §6 *Every confined process has its own `/tmp`, `/dev/shm` and IPC,
and sees every other mount read-only*; *A confined process has its own
network*): a user namespace mapping the caller onto itself, mount, IPC and
network namespaces (loopback only), a root the caller owns, private `/tmp`
and `/dev/shm`, a new `devpts`, the write grants bound writable (those
beneath `--link-root DIR` as one mount, so a tree links from its store) and
every other mount read-only. `--egress SOCK` gives the command the outside network
through leerie's egress proxy, listening on the unix socket SOCK: a relay
in the namespace forwards `127.0.0.1:<port>` to it and the proxy variables
name it; `--forward PORT=SOCK` relays a fixed loopback port to a socket the
same way. `--sandbox` builds a held-out evaluation's (DESIGN §6 *A held-out
evaluation runs alone, in a world that dies with it (I3)*): the same, never
with egress, plus UTS and PID namespaces, every write grant overlaid (its
writes vanish), the set's files placed into the overlay, and the shim as the
namespace's PID 1, which copies the runner's report to `--report-fd`. Both
exit 126 without running CMD when any step fails.

`--worker-root DIR` (with either): the repository's root filesystem, built
from the repository's image stages (DESIGN §6 *The orchestrator's root is
leerie's; a repository's root is only a confined process's*). The shim
builds the worker view at `WORKER_VIEW` — the worker root's system
directories, the container's own mounts (the state root, the checkout,
`/home`, `/tmp`, `/dev`, `/proc`...) and leerie's `/opt/leerie-*` — and CMD
is `chroot`ed into it only after the Landlock domain and the seccomp filter
are in place: the shim itself never runs, imports or reads code from the
worker root. `--cli`: CMD is a worker's CLI, which stays in leerie's root;
it starts with `CAP_SYS_CHROOT` (its only capability, in the shim's user
namespace), so `worker-shell.py` can `chroot` each of its commands into the
view.

Before it confines anything the shim holds no descriptor of a terminal
(`leave_terminal`; DESIGN §6 *No confined process holds or can open the
operator's terminal*): a terminal on fd 0 becomes `/dev/null`, one on any
other fd exits 126, and a controlling terminal is given up. Isolated, its
`/dev` is its own and holds no terminal of the container (`private_dev`);
confined, it exits 126 if it can still open `/dev/console`.

Stdlib only and imported by nothing heavier: it starts every worker. The
orchestrator imports `abi()` and `MIN_ABI` from here for its preflight, so
the requirement has one spelling."""
from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import os
import signal
import socket
import stat
import struct
import sys
import termios
import time
# `os.execvp` imports `warnings` on its first call (`os.get_exec_path`): in
# a child that has just entered the worker view, that import would read the
# repository's `warnings.py` (and, in a root without Python, fail). Imported
# here, in leerie's root, so the child imports nothing after it enters the
# view (DESIGN §6 *The orchestrator's root is leerie's; a repository's root
# is only a confined process's*: nothing of the shim's runs from the worker
# root).
import warnings  # noqa: F401

# Landlock's syscalls share one number on every architecture (they were
# added after the syscall tables were unified).
_SYS_CREATE, _SYS_ADD_RULE, _SYS_RESTRICT = 444, 445, 446
_CREATE_RULESET_VERSION = 1
_RULE_PATH_BENEATH = 1
_PR_SET_NO_NEW_PRIVS = 38
_PR_SET_CHILD_SUBREAPER = 36
_PR_SET_DUMPABLE = 4

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


# ABI 6: a ruleset can scope abstract unix sockets and signals to its own
# domain (a process may connect to, or signal, only what is in its domain
# or one nested in it).
SCOPE_ABSTRACT_UNIX_SOCKET = 1 << 0
SCOPE_SIGNAL = 1 << 1
SCOPE_MIN_ABI = 6


class _Attr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _AttrScoped(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64),
                ("handled_access_net", ctypes.c_uint64),
                ("scoped", ctypes.c_uint64)]


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
    attr = (_AttrScoped(HANDLED, 0,
                        SCOPE_ABSTRACT_UNIX_SOCKET | SCOPE_SIGNAL)
            if v >= SCOPE_MIN_ABI else _Attr(HANDLED))
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



def nest() -> None:
    """Enter a Landlock domain nested inside this process's own, ruled to
    allow everything it handles beneath `/`. What the process may reach is
    unchanged (an access passes only when every layer of its domain allows
    it, so the parent's grants still bind), but Landlock refuses it — and
    everything it starts — ptrace-class access to any process outside its
    domain: `/proc/<pid>/fd`, `/proc/<pid>/mem`, `ptrace`, `pidfd_getfd`
    of the worker's CLI (DESIGN §6 *A worker's commands cannot reach its
    CLI*). No scope: a command may still signal, or connect to an abstract
    socket of, what an earlier command of the same worker left running,
    which a sibling nested domain would otherwise refuse. Raises OSError
    naming the step that failed."""
    libc = _libc()
    attr = _Attr(HANDLED)
    fd = libc.syscall(_SYS_CREATE, ctypes.byref(attr),
                      ctypes.c_size_t(ctypes.sizeof(attr)), ctypes.c_uint32(0))
    if fd < 0:
        raise OSError(f"landlock_create_ruleset: errno {ctypes.get_errno()}")
    try:
        pfd = os.open("/", os.O_PATH | os.O_CLOEXEC)
        try:
            rule = _PathBeneath(HANDLED, pfd)
            if libc.syscall(_SYS_ADD_RULE, ctypes.c_int(fd),
                            ctypes.c_int(_RULE_PATH_BENEATH),
                            ctypes.byref(rule), ctypes.c_uint32(0)) != 0:
                raise OSError(f"landlock_add_rule /: errno "
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


# The caller's refusal descriptor (`--refusal-fd`), or None.
_REFUSAL_FD: int | None = None
# The caller's descriptor for a held-out path the tree has (`--taken-fd`), or
# None.
_TAKEN_FD: int | None = None


def _refused(msg: str) -> int:
    """Refuse to run the command: `msg` to stderr and, when the caller gave
    one, to the refusal descriptor; returns 126. The only way the shim
    refuses (DESIGN §6 *A confinement failure stops the run; it is never a
    result*)."""
    print(msg, file=sys.stderr)
    if _REFUSAL_FD is not None:
        with contextlib.suppress(OSError):
            os.write(_REFUSAL_FD, msg.encode(errors="replace") + b"\n")
    return 126


def _taken(e: "PlacementTaken") -> int:
    """A held-out file the tree leaves no room for: its reason to stderr
    and, when the caller gave one, to the taken descriptor — never to the
    refusal descriptor, so a tree a worker committed can never read as a
    confinement failure (DESIGN §6 *A confinement failure stops the run; it
    is never a result*). The command does not run; returns 126."""
    msg = f"leerie: the held-out file is not placed, so the command does " \
          f"not run: {e}"
    print(msg, file=sys.stderr)
    if _TAKEN_FD is not None:
        with contextlib.suppress(OSError):
            os.write(_TAKEN_FD, msg.encode(errors="replace") + b"\n")
    return 126


def _undumpable() -> str | None:
    """Make the shim non-dumpable before the command starts, so no process
    without `CAP_SYS_PTRACE` over it — the command included, whatever its
    namespaces — can open `/proc/<shim>/fd/N`: the refusal descriptor stays
    the shim's to write (DESIGN §6 *A confinement failure stops the run; it
    is never a result*). Inherited by each fork until its `exec`. Returns
    why it could not, or None."""
    if _libc().prctl(_PR_SET_DUMPABLE, 0, 0, 0, 0) != 0:
        return f"prctl(PR_SET_DUMPABLE): errno {ctypes.get_errno()}"
    return None


def _before_exec(enter: "tuple | None") -> None:
    """In a forked child, just before it `exec`s the command: `enter` is
    `("view", VIEW, changes)` — put it in the worker view — or
    `("cli", VIEW, worker_root)` — keep `CAP_SYS_CHROOT` for the CLI and
    name the view to its `worker-shell.py`. Exits 126 when it cannot: the
    command never runs in a root other than the one asked for."""
    if enter is None:
        return
    try:
        if enter[0] == "view":
            enter_worker_view(enter[1], enter[2])
        else:
            keep_chroot_across_exec()
            os.environ[VIEW_ENV] = enter[1]
            os.environ[WORKER_ROOT_ENV] = enter[2]
    except OSError as e:
        os._exit(_refused(
            f"leerie: cannot start the command in the repository's root, "
            f"so it does not run: {e} (DESIGN §6 *The orchestrator's root "
            "is leerie's; a repository's root is only a confined "
            "process's*)"))


def run(cmd: list[str], relay: int | None = None,
        enter: "tuple | None" = None) -> int:
    """Run `cmd` as a child of this subreaper, then end what it left.
    `relay`, the relay process, holds no state of its own: it is killed
    outright once `cmd` has ended, before the grace the rest gets.
    `enter`: the root the child takes just before it `exec`s
    (`_before_exec`).
    SIGCHLD and the stop signals are blocked and taken with `sigwaitinfo`,
    so none is lost between a check and the wait; the child gets the mask
    it was started with, and the dispositions it would have outside a
    Python parent (`restore_default_signals`)."""
    libc = _libc()
    if libc.prctl(_PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0) != 0:
        return _refused(
            f"leerie: cannot make the shim a subreaper (errno "
            f"{ctypes.get_errno()}), so the command does not run "
            "(DESIGN §6 *A confined command's processes end with it*)")
    why = _undumpable()
    if why is not None:
        return _refused(
            f"leerie: the command does not run, because {why} (DESIGN §6 "
            "*A confinement failure stops the run; it is never a result*)")
    watched = {signal.SIGCHLD, *_STOP_SIGNALS}
    old = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    child = os.fork()
    if child == 0:
        signal.pthread_sigmask(signal.SIG_SETMASK, old)
        restore_default_signals()
        _before_exec(enter)
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
                if pid == relay:
                    relay = None
                if pid == child:
                    status = st
                    break
        except ChildProcessError:
            break
        if status is None:
            info = _wait_signal(watched)
            if info is not None and info.si_signo in _STOP_SIGNALS:
                stopped = info.si_signo
    if relay is not None:
        try:
            os.kill(relay, signal.SIGKILL)
            os.waitpid(relay, 0)
        except (OSError, ChildProcessError):
            pass
    end_descendants(STOP_GRACE_SEC if stopped is not None
                    else END_GRACE_SEC)
    full = _home_full()
    if full is not None:
        return _refused(full)
    if stopped is not None:
        return _die_of(stopped)
    if status is None:
        return 1
    if os.WIFSIGNALED(status):
        return _die_of(os.WTERMSIG(status))
    return os.WEXITSTATUS(status) if os.WIFEXITED(status) else 1


# --- seccomp: no namespace, mount or keyring call from a confined process ---
# DESIGN §6 *Every confined process has its own `/tmp`, `/dev/shm` and IPC*.
# Rootful runs with `seccomp=unconfined` so the shim can make a user
# namespace; this filter takes back, per process and tighter, what the
# container's default profile refused. Installed after `no_new_privs`.

_PR_SET_SECCOMP = 22
_SECCOMP_MODE_FILTER = 2
_RET_ALLOW = 0x7FFF0000
_RET_ERRNO = 0x00050000
_RET_KILL_PROCESS = 0x80000000
# NEWNS | NEWCGROUP | NEWUTS | NEWIPC | NEWUSER | NEWPID | NEWNET
CLONE_NEW_MASK = 0x7E020000
_X32_SYSCALL_BIT = 0x40000000

# Per architecture: its `AUDIT_ARCH_*`, the calls refused with EPERM, and
# `clone` / `clone3`. The new mount API and `clone3` share numbers on both.
SECCOMP_TABLES: dict[str, dict] = {
    "x86_64": {
        "audit_arch": 0xC000003E,
        "eperm": {"unshare": 272, "setns": 308, "mount": 165,
                  "umount2": 166, "pivot_root": 155, "open_tree": 428,
                  "move_mount": 429, "fsopen": 430, "fsconfig": 431,
                  "fsmount": 432, "fspick": 433, "mount_setattr": 442,
                  "add_key": 248, "request_key": 249, "keyctl": 250},
        "clone": 56, "clone3": 435, "x32": True,
    },
    "aarch64": {
        "audit_arch": 0xC00000B7,
        "eperm": {"unshare": 97, "setns": 268, "mount": 40, "umount2": 39,
                  "pivot_root": 41, "open_tree": 428, "move_mount": 429,
                  "fsopen": 430, "fsconfig": 431, "fsmount": 432,
                  "fspick": 433, "mount_setattr": 442, "add_key": 217,
                  "request_key": 218, "keyctl": 219},
        "clone": 220, "clone3": 435, "x32": False,
    },
}
_ARCH_ALIASES = {"amd64": "x86_64", "arm64": "aarch64"}

_LD_W_ABS, _JEQ_K, _JSET_K, _JGE_K, _RET_K = 0x20, 0x15, 0x45, 0x35, 0x06


def _arch() -> str:
    m = os.uname().machine
    return _ARCH_ALIASES.get(m, m)


def _ins(code: int, jt: int, jf: int, k: int) -> bytes:
    return struct.pack("HBBI", code, jt, jf, k)


def seccomp_program(arch: str) -> bytes:
    """The classic-BPF filter for `arch` (a `SECCOMP_TABLES` key): another
    architecture kills the process; x32 (x86_64), the namespace, mount and
    keyring calls and a `clone` with any `CLONE_NEW*` flag answer EPERM;
    `clone3`, whose flags a filter cannot read, answers ENOSYS, so libc
    falls back to `clone`."""
    t = SECCOMP_TABLES[arch]
    p = [_ins(_LD_W_ABS, 0, 0, 4),                    # seccomp_data.arch
         _ins(_JEQ_K, 1, 0, t["audit_arch"]),
         _ins(_RET_K, 0, 0, _RET_KILL_PROCESS),
         _ins(_LD_W_ABS, 0, 0, 0)]                     # seccomp_data.nr
    if t["x32"]:
        p += [_ins(_JGE_K, 0, 1, _X32_SYSCALL_BIT),
              _ins(_RET_K, 0, 0, _RET_ERRNO | errno.EPERM)]
    for nr in sorted(set(t["eperm"].values())):
        p += [_ins(_JEQ_K, 0, 1, nr),
              _ins(_RET_K, 0, 0, _RET_ERRNO | errno.EPERM)]
    p += [_ins(_JEQ_K, 0, 1, t["clone3"]),
          _ins(_RET_K, 0, 0, _RET_ERRNO | errno.ENOSYS),
          _ins(_JEQ_K, 0, 3, t["clone"]),
          _ins(_LD_W_ABS, 0, 0, 16),                   # args[0], low word
          _ins(_JSET_K, 0, 1, CLONE_NEW_MASK),
          _ins(_RET_K, 0, 0, _RET_ERRNO | errno.EPERM),
          _ins(_RET_K, 0, 0, _RET_ALLOW)]
    return b"".join(p)


class _SockFprog(ctypes.Structure):
    _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]


def install_seccomp() -> None:
    """Install `seccomp_program` for this machine on this process (and,
    inherited, everything it starts). Raises OSError naming the step."""
    arch = _arch()
    if arch not in SECCOMP_TABLES:
        raise OSError(f"no seccomp table for the architecture {arch!r}")
    prog = seccomp_program(arch)
    buf = ctypes.create_string_buffer(prog, len(prog))
    fp = _SockFprog(len(prog) // 8, ctypes.cast(buf, ctypes.c_void_p))
    libc = _libc()
    try:
        prctl = libc.prctl
    except AttributeError as e:
        # A libc with no `prctl` (not Linux). The contract above is an OSError
        # naming the step, and `_confine_or_126` catches only that: an
        # AttributeError escaping here made the shim traceback and exit 1
        # instead of writing its refusal and exiting 126, so a caller read
        # "no verdict" where the shim meant "I will not run this". `abi()`
        # also catches this missing symbol rather than letting it escape,
        # though to the opposite end: it answers 0, because its caller asks
        # a question, while this one is a step that must refuse.
        raise OSError(f"prctl: {e}") from None
    if prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0) != 0:
        raise OSError(f"prctl(PR_SET_NO_NEW_PRIVS): errno "
                      f"{ctypes.get_errno()}")
    if prctl(_PR_SET_SECCOMP, _SECCOMP_MODE_FILTER,
             ctypes.byref(fp), 0, 0) != 0:
        raise OSError(f"seccomp filter: errno {ctypes.get_errno()}")


# --- isolation: namespaces and mounts, built before anything runs ----------
# DESIGN §6 *Every confined process has its own `/tmp`, `/dev/shm` and IPC,
# and sees every other mount read-only*; *A held-out evaluation runs alone,
# in a world that dies with it (I3)*. The shim makes an unprivileged user
# namespace first (the caller's ids mapped onto themselves), which gives it
# the namespace capabilities inside it and nowhere else; the mounts below
# are then its own. Must run single-threaded, before Landlock (a Landlocked
# process cannot mount) and before the seccomp filter.

CLONE_NEWNS = 0x00020000
CLONE_NEWUTS = 0x04000000
CLONE_NEWIPC = 0x08000000
CLONE_NEWUSER = 0x10000000
CLONE_NEWPID = 0x20000000
CLONE_NEWNET = 0x40000000

MS_RDONLY = 1
MS_NOSUID = 2
MS_NODEV = 4
MS_NOEXEC = 8
MS_REMOUNT = 32
MS_NOATIME = 1024
MS_NODIRATIME = 2048
MS_BIND = 4096
MS_REC = 16384
MS_PRIVATE = 1 << 18
MS_RELATIME = 1 << 21
MS_STRICTATIME = 1 << 24
MNT_DETACH = 2
# A mount's flags an unprivileged user namespace may not clear (the kernel
# locks them on an inherited mount): every remount must repeat them.
_LOCKED_FLAGS = {"nosuid": MS_NOSUID, "nodev": MS_NODEV,
                 "noexec": MS_NOEXEC, "noatime": MS_NOATIME,
                 "nodiratime": MS_NODIRATIME, "relatime": MS_RELATIME,
                 "strictatime": MS_STRICTATIME}

_PIVOT_ROOT = {"x86_64": 155, "aarch64": 41}
_KEYCTL = {"x86_64": 250, "aarch64": 219}
_KEYCTL_JOIN_SESSION_KEYRING = 1
_SIOCGIFFLAGS, _SIOCSIFFLAGS, _IFF_UP = 0x8913, 0x8914, 0x1

TMP = "/tmp"
DEV = "/dev"
SHM = "/dev/shm"
PTS = "/dev/pts"
# The private mounts the shim makes and grants itself.
PRIVATE = (TMP, SHM, PTS)
# A confined process's `/dev` is its own and holds only these (DESIGN §6
# *No confined process holds or can open the operator's terminal*): the
# device nodes bound from the container's `/dev`, its own `devpts` and
# `/dev/shm`, and the links every `/dev` has. No terminal of the container
# is among them: not `/dev/console`, which `nerdctl run -it` binds to the
# operator's pty (review round 33 isolation Q1), nor the container's
# `devpts`, nor any other node a runtime adds (Fly's VM `/dev` is a whole
# devtmpfs).
DEV_NODES = ("null", "zero", "full", "random", "urandom", "tty")
DEV_LINKS = (("fd", "/proc/self/fd"), ("stdin", "/proc/self/fd/0"),
             ("stdout", "/proc/self/fd/1"), ("stderr", "/proc/self/fd/2"),
             ("ptmx", "pts/ptmx"))
CONSOLE = "/dev/console"
_OLDROOT = "/.oldroot"
_OVERLAY_DIR = "/tmp/.leerie-overlay"
# Where the home's upper `tmpfs` and the real home are mounted while the
# home's overlay is made (`private_home`); both are detached before the
# command starts, so nothing reaches either but through `$HOME`.
_HOME_UPPER = "/tmp/.leerie-home"
_HOME_REAL = "/tmp/.leerie-home-real"
# A home with less than this free is full (`HomeWatch`): a write that does
# not fit fails with ENOSPC once the `tmpfs` has no page left.
HOME_FULL_MARGIN = 1 << 20
# How often the shim samples its home's use while the command runs.
HOME_SAMPLE_SEC = 0.2
# The most of a runner's report PID 1 copies out (a larger one is cut short
# and reads as unreadable, which a caller treats as "could not measure").
REPORT_MAX_BYTES = 64 << 20


class PlacementTaken(Exception):
    """A held-out file's path is already the tree's: a file is there, or a
    link or a non-directory stands on its way (`EEXIST`, `ENOTDIR`,
    `ELOOP`). A fact about the tree a worker committed, never a confinement
    failure (`_taken`)."""


# The errors of `_place_one` that say the tree has the path: anything else
# (no space, a read-only overlay, a policy denial) is the environment's.
_TAKEN_ERRNOS = frozenset({errno.EEXIST, errno.ENOTDIR, errno.ELOOP})


class IsolationError(Exception):
    """A step of building the isolation failed: `step` names it, `err` is
    the errno (0 when the failure was not a syscall's)."""

    def __init__(self, step: str, err: int = 0) -> None:
        super().__init__(step, err)
        self.step = step
        self.err = err

    def __str__(self) -> str:
        if not self.err:
            return self.step
        return f"{self.step}: {errno.errorcode.get(self.err, self.err)}"


def _mount(src: str | None, tgt: str, fstype: str | None, flags: int,
           data: str | None = None) -> int:
    libc = _libc()
    libc.mount.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                           ctypes.c_char_p, ctypes.c_ulong, ctypes.c_char_p]
    return libc.mount(os.fsencode(src) if src else None, os.fsencode(tgt),
                      fstype.encode() if fstype else None, flags,
                      data.encode() if data else None)


def _must(step: str, rc: int) -> None:
    if rc != 0:
        raise IsolationError(step, ctypes.get_errno())


def _beneath(path: str, top: str) -> bool:
    return path == top or path.startswith(top.rstrip("/") + "/")


def _write_proc(path: str, text: str, step: str) -> None:
    try:
        fd = os.open(path, os.O_WRONLY)
        try:
            os.write(fd, text.encode())
        finally:
            os.close(fd)
    except OSError as e:
        raise IsolationError(step, e.errno or 0) from None


def enter_namespaces(*, sandbox: bool) -> None:
    """A new user namespace with the caller's uid and gid mapped onto
    themselves, then new mount, IPC and network namespaces (and UTS and PID
    ones for a sandbox; the PID namespace takes effect for the next
    child)."""
    uid, gid = os.geteuid(), os.getegid()
    libc = _libc()
    _must("unshare(CLONE_NEWUSER)", libc.unshare(CLONE_NEWUSER))
    _write_proc("/proc/self/setgroups", "deny", "write setgroups")
    _write_proc("/proc/self/uid_map", f"{uid} {uid} 1", "write uid_map")
    _write_proc("/proc/self/gid_map", f"{gid} {gid} 1", "write gid_map")
    flags = CLONE_NEWNS | CLONE_NEWIPC | CLONE_NEWNET
    if sandbox:
        flags |= CLONE_NEWUTS | CLONE_NEWPID
    _must("unshare(mount/IPC/net" + ("/UTS/PID)" if sandbox else ")"),
          libc.unshare(flags))


def private_mounts(old_dev: str) -> None:
    """A fresh tmpfs on `/tmp`, and `/dev` made the process's own
    (`private_dev`, from the old root's `old_dev`)."""
    _must(f"tmpfs {TMP}", _mount("tmpfs", TMP, "tmpfs",
                                 MS_NOSUID | MS_NODEV, "mode=1777"))
    private_dev(old_dev)


def private_dev(old_dev: str) -> None:
    """A tmpfs on `/dev` holding only `DEV_NODES`, each bound from
    `old_dev` (a node the container lacks is left out), the `DEV_LINKS`
    (`/dev/ptmx` among them: it follows the new `devpts`), a fresh tmpfs on
    `/dev/shm` and a new `devpts` instance on `/dev/pts`. Made in the user
    namespace, so no node can be made (`mknod` needs the initial
    namespace's `CAP_MKNOD`): a bound node is the container's own device."""
    _must(f"tmpfs {DEV}", _mount("tmpfs", DEV, "tmpfs",
                                 MS_NOSUID | MS_NODEV | MS_NOEXEC,
                                 "mode=0755"))
    try:
        for name in DEV_NODES:
            src = f"{old_dev}/{name}"
            try:
                if not stat.S_ISCHR(os.stat(src).st_mode):
                    continue
            except FileNotFoundError:
                continue
            open(f"{DEV}/{name}", "w").close()
            _must(f"bind {DEV}/{name}",
                  _mount(src, f"{DEV}/{name}", None, MS_BIND))
        for name, target in DEV_LINKS:
            os.symlink(target, f"{DEV}/{name}")
        os.mkdir(SHM)
        os.mkdir(PTS)
    except OSError as e:
        raise IsolationError(f"make {DEV}: {e}", e.errno or 0) from None
    _must(f"tmpfs {SHM}", _mount("tmpfs", SHM, "tmpfs",
                                 MS_NOSUID | MS_NODEV, "mode=1777"))
    _must("devpts /dev/pts",
          _mount("devpts", PTS, "devpts", MS_NOSUID | MS_NOEXEC,
                 "newinstance,ptmxmode=0666,mode=620"))


def _skeleton(src: str, dst: str) -> None:
    """`dst` made with every directory beneath `src`, no file: a held-out
    run's fresh copy of a write grant under `/tmp` (its `run/` included)."""
    os.makedirs(dst, mode=0o700, exist_ok=True)
    for top, dirs, _files in os.walk(src):
        rel = os.path.relpath(top, src)
        for d in dirs:
            os.makedirs(os.path.join(dst, rel, d), mode=0o700, exist_ok=True)


def pivot_to_owned_root(carry_ro: list[str], carry_rw: list[str], *,
                        sandbox: bool) -> list[str]:
    """Pivot into a root the caller owns: a tmpfs holding a bind of each
    entry of the old root (bwrap's shape). On rootful the user namespace
    maps only the caller, so the real `/` shows as owned by `nobody`, and a
    tool checking every ancestor of its cache refuses it (measured: SWC).
    Then the private mounts, then the grants beneath `/tmp` carried from the
    old root into the new `/tmp` at their own paths — a write grant as a
    fresh empty copy of its directories in a sandbox. Returns the carried
    write grants' mount points, which stay writable."""
    cwd = os.getcwd()
    _must("make / rprivate", _mount(None, "/", None, MS_REC | MS_PRIVATE))
    new = TMP
    _must("tmpfs new root", _mount("tmpfs", new, "tmpfs",
                                   MS_NOSUID | MS_NODEV, "mode=0755"))
    try:
        for name in sorted(os.listdir("/")):
            src, dst = "/" + name, new + "/" + name
            if name in ("tmp", "dev"):
                os.mkdir(dst)
            elif os.path.islink(src):
                os.symlink(os.readlink(src), dst)
            elif os.path.isdir(src):
                os.mkdir(dst)
                _must(f"rbind {src}", _mount(src, dst, None,
                                             MS_BIND | MS_REC))
            else:
                open(dst, "w").close()
                _must(f"bind {src}", _mount(src, dst, None, MS_BIND))
        os.mkdir(new + _OLDROOT)
    except OSError as e:
        raise IsolationError(f"stage the new root: {e}", e.errno or 0) \
            from None
    libc = _libc()
    nr = _PIVOT_ROOT.get(_arch())
    if nr is None:
        raise IsolationError(f"no pivot_root number for {_arch()!r}")
    _must("pivot_root", libc.syscall(nr, os.fsencode(new),
                                     os.fsencode(new + _OLDROOT)))
    os.chdir("/")
    private_mounts(_OLDROOT + DEV)
    kept: list[str] = []
    for paths, writable in ((carry_ro, False), (carry_rw, True)):
        for p in paths:
            src = _OLDROOT + p
            try:
                if sandbox and writable:
                    if os.path.isdir(src):
                        _skeleton(src, p)
                    continue
                if os.path.isdir(src):
                    os.makedirs(p, exist_ok=True)
                elif os.path.exists(src):
                    os.makedirs(os.path.dirname(p), exist_ok=True)
                    open(p, "a").close()
                else:
                    continue
            except OSError as e:
                raise IsolationError(f"carry {p}: {e}", e.errno or 0) \
                    from None
            _must(f"carry {p}", _mount(src, p, None, MS_BIND | MS_REC))
            if writable:
                kept.append(p)
    _must("detach the old root", libc.umount2(os.fsencode(_OLDROOT),
                                             MNT_DETACH))
    os.rmdir(_OLDROOT)
    try:
        os.chdir(cwd)
    except OSError as e:
        raise IsolationError(f"chdir {cwd}", e.errno or 0) from None
    return kept


def _bindable(p: str) -> bool:
    """A write grant the shim binds or overlays: not one of its private
    mounts or beneath them, not a device node (which writes on a read-only
    mount anyway)."""
    return not (_beneath(p, TMP) or _beneath(p, "/dev"))


def _shield(top: str, grants: list[str]) -> list[str]:
    """Every entry beneath `top` that is no grant, lies beneath none and
    leads to none, bound onto itself (`readonly_mounts` then makes it
    read-only); the directories that lead to a grant are walked. Returns
    the entries bound. A symbolic link is not bound (a mount follows it);
    an entry removed while this runs needs nothing."""
    out = []
    try:
        names = sorted(os.listdir(top))
    except FileNotFoundError:
        return out
    except OSError as e:
        raise IsolationError(f"list {top}", e.errno or 0) from None
    for name in names:
        p = os.path.join(top, name)
        if any(_beneath(p, g) for g in grants):
            continue
        if any(_beneath(g, p) for g in grants):
            out += _shield(p, grants)
            continue
        if os.path.islink(p):
            continue
        if _mount(p, p, None, MS_BIND | MS_REC) != 0:
            err = ctypes.get_errno()
            if err == errno.ENOENT:
                continue
            raise IsolationError(f"bind {p}", err)
        out.append(p)
    return out


def bind_grants(rw: list[str], link_root: str | None = None
                ) -> tuple[list[str], list[str]]:
    """The write grants bound so they stay writable when every other mount
    is made read-only (DESIGN §6 *The write grants on one mount are one
    mount*). Those beneath `link_root` are bound together: `link_root`
    bound onto itself once, and everything beneath it that no grant covers
    or leads to bound apart (`_shield`) for `readonly_mounts` to make
    read-only. One mount, because a hard link cannot cross mounts (`EXDEV`)
    even on one filesystem: bound one by one, a tree and the store it
    installs from lie on two, and every package manager that links from its
    store (pnpm, bun, uv) copies instead. `link_root` is the caller's
    directory of trees and stores and nothing else; every other grant is
    bound onto itself apart, as before. Returns `(grants, roots)`: the
    grants, and `link_root` when it was bound (kept writable itself, never
    what is beneath it)."""
    kept: list[str] = []
    linked: list[str] = []
    root = os.path.normpath(link_root) if link_root else None
    for p in rw:
        if not _bindable(p) or not os.path.exists(p):
            continue
        p = os.path.normpath(p)
        if root is not None and p != root and _beneath(p, root):
            linked.append(p)
            continue
        _must(f"bind grant {p}", _mount(p, p, None, MS_BIND | MS_REC))
        kept.append(p)
    roots: list[str] = []
    if linked:
        assert root is not None
        _must(f"bind grant {root}", _mount(root, root, None,
                                           MS_BIND | MS_REC))
        _shield(root, linked)
        roots.append(root)
        kept += linked
    return kept, roots


def overlay_grants(rw: list[str], view: str) -> list[str]:
    """A sandbox's write grants, `view` first: each directory an overlay
    whose upper layer is on the private `/tmp`, so the writes vanish with
    the namespace and never reach the directory. An overlay that cannot be
    made is a failed step: no fallback writes the real directory."""
    kept = []
    order = [view] + [p for p in rw if p != view]
    for n, p in enumerate(order):
        if not _bindable(p) or not os.path.isdir(p):
            continue
        if any(c in p for c in ",:\\"):
            raise IsolationError(f"overlay {p}: a path overlayfs cannot name")
        base = f"{_OVERLAY_DIR}/{n}"
        os.makedirs(base + "/u", mode=0o700)
        os.makedirs(base + "/w", mode=0o700)
        _must(f"overlay {p}", _mount(
            "overlay", p, "overlay", 0,
            f"lowerdir={p},upperdir={base}/u,workdir={base}/w,userxattr"))
        kept.append(p)
    return kept


def private_home(home: str, layers: list[str], size: int,
                 grants: list[str], upper: str | None = None) -> None:
    """Mount the process's own home on `home` (DESIGN §6 *Every confined
    process has its own home*): an overlay whose lower layers are `layers`
    (each that exists, the first on top) and then `home` itself, and whose
    upper and work directories are on a `tmpfs` of `size` bytes mounted in
    this mount namespace, so the kernel frees it with the namespace on any
    exit. Every grant in `grants` that lies beneath `home` is bound from the
    real directory over the overlay, so it names what it names. The upper
    `tmpfs` and the real home are then detached: the process reaches
    neither but through `home`. Fails (`IsolationError`) rather than mount
    a home over a mount beneath it, which the kernel refuses (`EINVAL`).
    `upper`: an upper on disk instead, its work directory `<upper>.work`
    beside it, kept after the process (a tree's install writes its tree
    home layer so); no `tmpfs` is made."""
    home = os.path.normpath(home)
    if not os.path.isdir(home) or os.path.islink(home):
        raise IsolationError(f"home {home}: not a directory")
    lowers = [os.path.normpath(p) for p in layers
              if os.path.isdir(p) and not _beneath(p, home)] + [home]
    if any(c in p for p in lowers for c in ",:\\"):
        raise IsolationError(f"home {home}: a path overlayfs cannot name")
    for mp, _opts in _mountinfo():
        if mp != home and _beneath(mp, home):
            raise IsolationError(
                f"home {home}: {mp} is mounted beneath it, and an overlay's "
                "lower layer cannot hold a mount")
    if upper is not None:
        upper = os.path.normpath(upper)
        if any(c in upper for c in ",:\\") or not os.path.isdir(upper) \
                or not os.path.isdir(upper + ".work"):
            raise IsolationError(f"home upper {upper}: not a directory "
                                 "overlayfs can name, with its .work")
    try:
        if upper is None:
            os.mkdir(_HOME_UPPER, 0o700)
        os.mkdir(_HOME_REAL, 0o700)
    except OSError as e:
        raise IsolationError(f"make {_HOME_UPPER}: {e}", e.errno or 0) \
            from None
    if upper is None:
        _must(f"tmpfs {_HOME_UPPER}", _mount(
            "tmpfs", _HOME_UPPER, "tmpfs", MS_NOSUID | MS_NODEV,
            f"size={size},mode=0700"))
        try:
            mode = stat.S_IMODE(os.stat(home).st_mode)
            os.mkdir(f"{_HOME_UPPER}/u", mode)
            os.chmod(f"{_HOME_UPPER}/u", mode)
            os.mkdir(f"{_HOME_UPPER}/w", 0o700)
        except OSError as e:
            raise IsolationError(f"make the home's upper: {e}",
                                 e.errno or 0) from None
        up, work = f"{_HOME_UPPER}/u", f"{_HOME_UPPER}/w"
    else:
        up, work = upper, upper + ".work"
    _must(f"bind {home}", _mount(home, _HOME_REAL, None, MS_BIND | MS_REC))
    _must(f"overlay {home}", _mount(
        "overlay", home, "overlay", 0,
        f"lowerdir={':'.join(lowers)},upperdir={up},"
        f"workdir={work},userxattr"))
    for g in sorted({os.path.normpath(g) for g in grants}):
        if g == home or not _beneath(g, home):
            continue
        src = _HOME_REAL + g[len(home):]
        if not os.path.lexists(src) or os.path.islink(src):
            continue
        _must(f"bind {g} over the home",
              _mount(src, g, None, MS_BIND | MS_REC))
    libc = _libc()
    for d in (_HOME_REAL, *([_HOME_UPPER] if upper is None else [])):
        _must(f"detach {d}", libc.umount2(os.fsencode(d), MNT_DETACH))
        with contextlib.suppress(OSError):
            os.rmdir(d)


class HomeWatch:
    """The use of the process's own home, sampled while its command runs
    (DESIGN §6 *Every confined process has its own home*): `full` once a
    sample found less than `HOME_FULL_MARGIN` free. The overlay's `statfs`
    is its upper's."""

    def __init__(self, home: str, size: int) -> None:
        self.home = home
        self.size = size
        self.full = False

    def sample(self) -> None:
        try:
            v = os.statvfs(self.home)
        except OSError:
            return
        if v.f_bavail * v.f_frsize < HOME_FULL_MARGIN:
            self.full = True

    def finding(self) -> str:
        return (f"leerie: the command filled its home {self.home} "
                f"({self.size >> 20} MiB, its private overlay's limit): "
                "whatever it then failed is the environment's, not a "
                "verdict on the code (DESIGN §6 *Every confined process has "
                "its own home*)")


# The home being watched for the command this shim runs, or None.
_HOME_WATCH: HomeWatch | None = None


def _wait_signal(watched: set) -> "signal.struct_siginfo | None":
    """`sigwaitinfo`, waking every `HOME_SAMPLE_SEC` to sample the home
    when one is watched; None on such a wake."""
    if _HOME_WATCH is None:
        return signal.sigwaitinfo(watched)
    _HOME_WATCH.sample()
    return signal.sigtimedwait(watched, HOME_SAMPLE_SEC)


def _home_full() -> str | None:
    """The finding when the watched home filled, else None: it goes to the
    refusal descriptor, so the caller never reads the command's result as
    one (DESIGN §6 *A confinement failure stops the run; it is never a
    result*)."""
    if _HOME_WATCH is None:
        return None
    _HOME_WATCH.sample()
    return _HOME_WATCH.finding() if _HOME_WATCH.full else None


def _watch_home(home: str | None, size: int | None, *,
                watch: bool = True) -> list[str]:
    """Watch `home`, the process's own (`HomeWatch`), when there is one and
    its upper is the shim's `tmpfs` (`watch`: an upper on disk is the
    disk's to bound); returns it as a write grant (the overlay is the
    process's), or nothing."""
    global _HOME_WATCH
    if home is None:
        return []
    if watch:
        _HOME_WATCH = HomeWatch(home, size or 0)
    return [home]


def _unescape_mountinfo(field: str) -> str:
    out, i = [], 0
    while i < len(field):
        if field[i] == "\\" and field[i + 1:i + 4].isdigit():
            out.append(chr(int(field[i + 1:i + 4], 8)))
            i += 4
        else:
            out.append(field[i])
            i += 1
    return "".join(out)


def _mountinfo() -> list[tuple[str, set[str]]]:
    out = []
    with open("/proc/self/mountinfo", encoding="utf-8",
              errors="surrogateescape") as f:
        for line in f:
            fields = line.split()
            out.append((_unescape_mountinfo(fields[4]),
                        set(fields[5].split(","))))
    return out


def readonly_mounts(keep: list[str], private: tuple[str, ...] = PRIVATE,
                    roots: tuple[str, ...] | list[str] = ()) -> int:
    """Every mount not already read-only, not a kept grant (or beneath one),
    not one of the `roots` `bind_grants` made for grants on one mount (that
    mount itself only) and not one of the `private` mounts, remounted
    read-only with its locked flags. A file outside the grants can then not
    have its mode, times or extended attributes changed. Returns how many
    were remounted."""
    n = 0
    for mp, opts in _mountinfo():
        if "ro" in opts or mp in private or mp in roots:
            continue
        if any(_beneath(mp, k) for k in keep):
            continue
        flags = MS_REMOUNT | MS_BIND | MS_RDONLY
        for name, bit in _LOCKED_FLAGS.items():
            if name in opts:
                flags |= bit
        _must(f"remount {mp} read-only", _mount(None, mp, None, flags))
        n += 1
    return n


def _regular_at(dir_fd: int, name: str) -> bool:
    """Whether `name` beneath `dir_fd` is a regular file (False: nothing
    there), not following a link; anything else there is a taken path."""
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
    except FileNotFoundError:
        return False
    if not stat.S_ISREG(st.st_mode):
        raise OSError(errno.EEXIST, f"{name} is not a regular file")
    return True


def _place_one(view_fd: int, src: str | None, rel: str, *,
               over: bool = False) -> None:
    """Write `src` at `rel` beneath the directory `view_fd`, resolving every
    component from that descriptor and following no link: each directory
    opened `O_NOFOLLOW | O_DIRECTORY` (made with `mkdirat` when absent), the
    file created `O_EXCL | O_NOFOLLOW`. A link anywhere on the path — the
    tree's own code, which a worker wrote — refuses the placement instead of
    sending the file through it. `over` (`--replace`): a regular file at
    `rel` is overwritten in place (`O_TRUNC | O_NOFOLLOW`); anything else
    there is still taken. `src` None (`--remove`): the regular file at `rel`
    is deleted, and a directory on the way that does not exist means there
    is nothing to delete."""
    parts = rel.split("/")
    fds: list[int] = []
    cur = view_fd
    try:
        for name in parts[:-1]:
            if src is not None:
                try:
                    os.mkdir(name, 0o755, dir_fd=cur)
                except FileExistsError:
                    pass
            try:
                cur = os.open(name, os.O_RDONLY | os.O_DIRECTORY
                              | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=cur)
            except FileNotFoundError:
                if src is None:
                    return
                raise
            fds.append(cur)
        if src is None:
            if _regular_at(cur, parts[-1]):
                os.unlink(parts[-1], dir_fd=cur)
            return
        with open(src, "rb") as f:
            data = f.read()
        flags = os.O_WRONLY | os.O_NOFOLLOW | os.O_CLOEXEC
        if over and _regular_at(cur, parts[-1]):
            flags |= os.O_TRUNC
        else:
            flags |= os.O_CREAT | os.O_EXCL
        fd = os.open(parts[-1], flags, 0o444, dir_fd=cur)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
    finally:
        for d in fds:
            os.close(d)


def place(pairs: list[tuple[str, str]], view: str, *,
          over: list[tuple[str, str]] = (),
          remove: list[str] = ()) -> None:
    """Copy each held-out file into the overlaid view, read-only. Refused
    when a destination is not a plain relative path; `PlacementTaken` when
    the view already has a file there, or a link or a non-directory is on
    its path (`_place_one`): a set is never placed over the tree's own
    file, nor anywhere but beneath the overlay. `over` pairs replace a
    regular file of the tree's (an existing test's base form), `remove`
    paths delete one; anything but a regular file there is taken. Runs
    after every other mount is read-only."""
    todo = ([(src, rel, False) for src, rel in pairs]
            + [(src, rel, True) for src, rel in over]
            + [(None, rel, False) for rel in remove])
    bad = []
    for _src, rel, _over in todo:
        parts = rel.split("/")
        if (not rel or rel.startswith("/") or ".." in parts or "." in parts
                or "" in parts):
            bad.append(rel)
    if bad:
        raise IsolationError("place: the view cannot hold "
                             + ", ".join(sorted(bad)))
    try:
        view_fd = os.open(view, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    except OSError as e:
        raise IsolationError(f"place: open {view}: {e}", e.errno or 0) \
            from None
    try:
        for src, rel, over_it in todo:
            try:
                _place_one(view_fd, src, rel, over=over_it)
            except OSError as e:
                if e.errno in _TAKEN_ERRNOS:
                    raise PlacementTaken(
                        f"place {rel}: the view already has it, or a link "
                        f"or a non-directory is on its path ({e})") from None
                raise IsolationError(f"place {rel}: {e}", e.errno or 0) \
                    from None
    finally:
        os.close(view_fd)


def loopback_up() -> None:
    """Bring up `lo`, the namespace's only interface."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        req = struct.pack("16sh", b"lo", 0)
        flags = struct.unpack("16sh", fcntl.ioctl(s, _SIOCGIFFLAGS, req))[1]
        fcntl.ioctl(s, _SIOCSIFFLAGS,
                    struct.pack("16sh", b"lo", flags | _IFF_UP))
    except OSError as e:
        raise IsolationError("bring up lo", e.errno or 0) from None
    finally:
        s.close()


def join_session_keyring() -> None:
    """An anonymous session keyring of its own: the inherited one is shared
    even across a nested user namespace (measured)."""
    libc = _libc()
    nr = _KEYCTL.get(_arch())
    if nr is None or libc.syscall(ctypes.c_long(nr),
                                  ctypes.c_long(_KEYCTL_JOIN_SESSION_KEYRING),
                                  None) < 0:
        raise IsolationError("join a session keyring", ctypes.get_errno())


# --- the worker view: the repository's root, for confined processes only ---
# DESIGN §6 *The orchestrator's root is leerie's; a repository's root is only
# a confined process's*. The repository's image stages are copied whole to
# `--worker-root` in leerie's image; nothing of leerie's runs in it. The shim
# builds a root from it and `chroot`s the command there once the command is
# confined, so the first code the worker root holds that ever runs is the
# confined command's own.

WORKER_VIEW = "/.leerie-worker-view"
# The top-level entries a worker view takes from the container whatever the
# worker root holds: the kernel's, the run's mounts and the private ones.
CONTAINER_ENTRIES = frozenset({"dev", "proc", "sys", "run", "tmp", "home",
                               "work", "inspect", "leerie-state",
                               "leerie-host"})
# The files of `/etc` the view takes from the container: the runtime mounts
# them when the container starts, so no image snapshot holds them, and they
# are the run's name service (`localhost` resolves through `hosts`).
CONTAINER_ETC_FILES = frozenset({"hosts", "hostname", "resolv.conf"})
# Files leerie's own image stages write at the worker root's top (`leerie`'s
# `_repo_image_wrap`): the worker stage's environment and the base
# image's, each `env -0`.
WORKER_ENV_FILE = ".leerie-worker-env"
BASE_ENV_FILE = ".leerie-base-env"
_ENV_FILE_LIMIT = 1 << 20
# Variables the worker stage's environment never sets in a confined
# command's: leerie's, the CLI's and the egress proxy's, and the ones the
# orchestrator gives every command.
_ENV_KEPT_OUT = frozenset({"PATH", "HOME", "TMPDIR", "PWD", "OLDPWD",
                           "SHLVL", "_", "HOSTNAME", "NO_PROXY", "no_proxy",
                           "NODE_USE_ENV_PROXY"})
_ENV_KEPT_OUT_PREFIXES = ("LEERIE_", "CLAUDE", "ANTHROPIC_")
VIEW_ENV = "LEERIE_WORKER_VIEW"
WORKER_ROOT_ENV = "LEERIE_WORKER_ROOT"
_PR_CAP_AMBIENT = 47
_PR_CAP_AMBIENT_RAISE = 2
_PR_CAP_AMBIENT_CLEAR_ALL = 4
CAP_SYS_CHROOT = 18
_CAP_VERSION_3 = 0x20080522


def _bind_entry(src: str, dst: str) -> None:
    """`dst` made as `src` is: a link copied as a link (it resolves in the
    view), a directory bound recursively, anything else bound as a file."""
    if os.path.islink(src):
        os.symlink(os.readlink(src), dst)
    elif os.path.isdir(src):
        os.mkdir(dst)
        _must(f"rbind {src}", _mount(src, dst, None, MS_BIND | MS_REC))
    elif os.path.exists(src):
        open(dst, "w").close()
        _must(f"bind {src}", _mount(src, dst, None, MS_BIND))


def build_worker_view(worker_root: str) -> list[str]:
    """Build `WORKER_VIEW` in the current root: each top-level entry of
    `worker_root` but `CONTAINER_ENTRIES`, those from the container, an
    `/opt` holding the worker root's entries with leerie's own
    (`/opt/leerie-*`) over them, and an `/etc` holding the worker root's
    entries but `CONTAINER_ETC_FILES`, the container's. Returns the view's entries that come from
    the worker root, which the caller grants read: a Landlock rule is on an
    inode, and the container's entries are already granted where they are.
    Lists and stats only: nothing in the worker root is opened or run."""
    try:
        theirs = set(os.listdir(worker_root))
        ours = set(os.listdir("/")) - {WORKER_VIEW.lstrip("/")}
        os.mkdir(WORKER_VIEW, 0o755)
        granted: list[str] = []
        for name in sorted(theirs | ours):
            dst = f"{WORKER_VIEW}/{name}"
            if name == "opt" and "opt" in theirs \
                    and os.path.isdir(f"{worker_root}/opt") \
                    and not os.path.islink(f"{worker_root}/opt"):
                os.mkdir(dst, 0o755)
                w_opt = set(os.listdir(f"{worker_root}/opt"))
                c_opt = (set(os.listdir("/opt")) if os.path.isdir("/opt")
                         else set())
                for sub in sorted(w_opt | c_opt):
                    if _beneath(f"/opt/{sub}", worker_root):
                        continue
                    if sub.startswith("leerie-") and sub in c_opt:
                        _bind_entry(f"/opt/{sub}", f"{dst}/{sub}")
                    elif sub in w_opt:
                        _bind_entry(f"{worker_root}/opt/{sub}",
                                    f"{dst}/{sub}")
                granted.append(dst)
            elif name == "etc" and os.path.isdir(f"{worker_root}/etc") \
                    and not os.path.islink(f"{worker_root}/etc"):
                os.mkdir(dst, 0o755)
                w_etc = set(os.listdir(f"{worker_root}/etc"))
                for sub in sorted(w_etc | CONTAINER_ETC_FILES):
                    if sub in CONTAINER_ETC_FILES:
                        if os.path.lexists(f"/etc/{sub}"):
                            _bind_entry(f"/etc/{sub}", f"{dst}/{sub}")
                    else:
                        _bind_entry(f"{worker_root}/etc/{sub}",
                                    f"{dst}/{sub}")
                granted.append(dst)
            elif name in CONTAINER_ENTRIES or name not in theirs:
                if name in ours:
                    _bind_entry("/" + name, dst)
            else:
                _bind_entry(f"{worker_root}/{name}", dst)
                granted.append(dst)
    except OSError as e:
        raise IsolationError(f"build the worker view: {e}", e.errno or 0) \
            from None
    return granted


def _read_env_file(path: str) -> dict[str, str]:
    """An `env -0` file, read without following a link and bounded; {} when
    it is absent or not a regular file."""
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return {}
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            return {}
        data = b""
        while len(data) <= _ENV_FILE_LIMIT:
            chunk = os.read(fd, 1 << 16)
            if not chunk:
                break
            data += chunk
    finally:
        os.close(fd)
    out: dict[str, str] = {}
    for item in data[:_ENV_FILE_LIMIT].split(b"\0"):
        k, eq, v = item.partition(b"=")
        if eq and k:
            out[os.fsdecode(k)] = os.fsdecode(v)
    return out


def worker_env_changes(worker_root: str) -> tuple[dict[str, str], list[str]]:
    """What the repository's stages changed in the environment: the
    variables whose value differs from the base image's (less
    `_ENV_KEPT_OUT` and leerie's, the CLI's and Anthropic's prefixes), and
    the `PATH` entries the base image's `PATH` lacks, in order."""
    worker = _read_env_file(f"{worker_root}/{WORKER_ENV_FILE}")
    base = _read_env_file(f"{worker_root}/{BASE_ENV_FILE}")
    changed = {k: v for k, v in worker.items()
               if base.get(k) != v and k not in _ENV_KEPT_OUT
               and k not in PROXY_VARS
               and not k.startswith(_ENV_KEPT_OUT_PREFIXES)}
    base_path = set(base.get("PATH", "").split(":"))
    added = [p for p in worker.get("PATH", "").split(":")
             if p and p not in base_path]
    return changed, list(dict.fromkeys(added))


def apply_worker_env(env: "os._Environ[str] | dict[str, str]",
                     changes: tuple[dict[str, str], list[str]]) -> None:
    """Lay the worker stage's changes over `env`: a variable the caller did
    not set (one leerie set, a store above all, is leerie's: DESIGN §6½ *The
    repository image holds no dependency*); its `PATH` entries first."""
    changed, added = changes
    for k, v in changed.items():
        if k not in env:
            env[k] = v
    if added:
        rest = [p for p in env.get("PATH", "").split(":")
                if p and p not in added]
        env["PATH"] = ":".join(added + rest)


def enter_worker_view(view: str, changes: tuple[dict[str, str], list[str]]
                      ) -> None:
    """`chroot` into `view` at the same working directory, drop every
    ambient capability (what this process `exec`s next holds none), and
    lay the worker stage's environment over this process's. Raises OSError.
    Called only by a confined process: after `confine`, or `nest`."""
    cwd = os.getcwd()
    os.chroot(view)
    os.chdir(cwd)
    _libc().prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_CLEAR_ALL, 0, 0, 0)
    apply_worker_env(os.environ, changes)


class _CapHeader(ctypes.Structure):
    _fields_ = [("version", ctypes.c_uint32), ("pid", ctypes.c_int)]


class _CapData(ctypes.Structure):
    _fields_ = [("effective", ctypes.c_uint32),
                ("permitted", ctypes.c_uint32),
                ("inheritable", ctypes.c_uint32)]


def keep_chroot_across_exec() -> None:
    """Make `CAP_SYS_CHROOT` ambient, so the CLI this process `exec`s holds
    it (in the shim's user namespace only) and `worker-shell.py` can put
    each command in the worker view. The CLI is leerie's code in leerie's
    root; every command drops it before it runs (`enter_worker_view`).
    Raises OSError."""
    libc = _libc()
    hdr = _CapHeader(_CAP_VERSION_3, 0)
    data = (_CapData * 2)()
    if libc.capget(ctypes.byref(hdr), data) != 0:
        raise OSError(f"capget: errno {ctypes.get_errno()}")
    data[0].inheritable |= 1 << CAP_SYS_CHROOT
    if libc.capset(ctypes.byref(hdr), data) != 0:
        raise OSError(f"capset: errno {ctypes.get_errno()}")
    if libc.prctl(_PR_CAP_AMBIENT, _PR_CAP_AMBIENT_RAISE, CAP_SYS_CHROOT,
                  0, 0) != 0:
        raise OSError(f"raise ambient CAP_SYS_CHROOT: errno "
                      f"{ctypes.get_errno()}")


def isolate(rw: list[str], ro: list[str], *, sandbox: bool = False,
            link_root: str | None = None,
            view: str | None = None,
            place_pairs: list[tuple[str, str]] = (),
            over_pairs: list[tuple[str, str]] = (),
            remove_paths: list[str] = (),
            sockets: list[str] = (),
            worker_root: str | None = None,
            home: str | None = None, home_layers: list[str] = (),
            home_bytes: int = 0,
            home_upper: str | None = None) -> tuple[int, list[str]]:
    """Build the namespaces and mounts for `--isolate` (or `--sandbox`,
    with `view`); returns how many mounts were made read-only and the read
    grants the worker view needs (`build_worker_view`; none without
    `worker_root`). `sockets`: the relays' unix sockets, whose directories
    are carried in read-only (one beneath `/tmp` is otherwise not there).
    The caller then joins a keyring, installs seccomp and confines."""
    cwd = os.getcwd()
    enter_namespaces(sandbox=sandbox)
    loopback_up()
    carry_ro = [p for p in ro if _beneath(p, TMP) and p != TMP]
    carry_ro += [os.path.dirname(p) for p in sockets
                 if _beneath(os.path.dirname(p), TMP)
                 and os.path.dirname(p) != TMP]
    carry_rw = [p for p in rw if _beneath(p, TMP) and p != TMP]
    kept = pivot_to_owned_root(carry_ro, carry_rw, sandbox=sandbox)
    roots: list[str] = []
    if home is not None:
        # Before the grants are bound: one beneath the home is bound from
        # the real directory over it, then bound or overlaid as any grant.
        private_home(home, list(home_layers), home_bytes, rw + ro,
                     upper=home_upper)
        roots.append(os.path.normpath(home))
    if sandbox:
        assert view is not None
        kept += overlay_grants(rw, view)
    else:
        bound, linked = bind_grants(rw, link_root)
        kept += bound
        roots += linked
    view_ro: list[str] = []
    private = PRIVATE
    if worker_root is not None:
        # After the grants: the view's binds of the container's entries
        # carry them, and stay writable where they are writable.
        view_ro = build_worker_view(worker_root)
        kept += [WORKER_VIEW + k for k in kept]
        roots += [WORKER_VIEW + r for r in roots]
        private = PRIVATE + tuple(WORKER_VIEW + p for p in PRIVATE)
    n = readonly_mounts(kept, private, roots)
    if sandbox:
        assert view is not None
        # After the read-only remount: the view's overlay is the one
        # writable place left on the path the placement resolves.
        place(list(place_pairs), view, over=list(over_pairs),
              remove=list(remove_paths))
    # The working directory was resolved before the binds and overlays: it
    # still names the directory beneath them (read-only now, or the
    # overlay's lower layer), so it is resolved again.
    try:
        os.chdir(cwd)
    except OSError as e:
        raise IsolationError(f"chdir {cwd}", e.errno or 0) from None
    return n, view_ro


def isolation_error() -> str | None:
    """None when this machine lets the shim build its isolation; else the
    shim's reason. Runs `--isolate` over `true` in a child process, so this
    process is untouched (tests skip on a host that restricts user
    namespaces)."""
    import subprocess  # noqa: PLC0415 - only this caller needs it
    r = subprocess.run([sys.executable, "-I", "-S", os.path.abspath(__file__),
                        "--isolate", "--ro", "/", "--", "true"],
                       capture_output=True, text=True, cwd="/", timeout=60)
    if r.returncode == 0:
        return None
    lines = r.stderr.strip().splitlines()
    return lines[-1] if lines else f"exit {r.returncode}"


# --- the network a confined process reaches: its own loopback, and leerie's
# egress proxy through a relay ------------------------------------------------
# DESIGN §6 *A confined process has its own network; the outside is reached
# only through leerie's proxy*. A loopback listener one confined process starts is in its own
# network namespace: no other confined process reaches it. A process that
# needs the outside network (a worker, an install) reaches it only through
# the proxy leerie holds, on a unix socket: the relay below listens on the
# namespace's loopback and forwards each connection to that socket.

PROXY_VARS = ("HTTPS_PROXY", "HTTP_PROXY", "ALL_PROXY",
              "https_proxy", "http_proxy", "all_proxy")
NO_PROXY_VALUE = "localhost,127.0.0.1,::1"
_RELAY_CHUNK = 1 << 16


def relay_listener(port: int) -> socket.socket:
    """A TCP listener on the namespace's `127.0.0.1:port` (0: the kernel
    picks one)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        s.bind(("127.0.0.1", port))
        s.listen(128)
    except OSError as e:
        s.close()
        raise IsolationError(f"relay listen 127.0.0.1:{port}",
                             e.errno or 0) from None
    return s


def proxy_env(port: int) -> dict[str, str]:
    """The variables that send a command's outside connections to the
    relay on `port`: the usual proxy variables in both cases, the loopback
    left direct, and Node's built-in `http`/`fetch` told to read them
    (`NODE_USE_ENV_PROXY`, Node 24)."""
    url = f"http://127.0.0.1:{port}"
    env = {v: url for v in PROXY_VARS}
    env.update(NO_PROXY=NO_PROXY_VALUE, no_proxy=NO_PROXY_VALUE,
               NODE_USE_ENV_PROXY="1")
    return env


def _pump(src: socket.socket, dst: socket.socket) -> None:
    try:
        while True:
            data = src.recv(_RELAY_CHUNK)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    try:
        dst.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def _relay_one(conn: socket.socket, target: str) -> None:
    up = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        up.connect(target)
    except OSError:
        up.close()
        conn.close()
        return
    import threading  # noqa: PLC0415 - only the relay process needs it
    back = threading.Thread(target=_pump, args=(up, conn), daemon=True)
    back.start()
    _pump(conn, up)
    back.join()
    up.close()
    conn.close()


def _relay_accept(listener: socket.socket, target: str) -> None:
    while True:
        try:
            conn, _ = listener.accept()
        except InterruptedError:
            continue
        except OSError:
            return
        import threading  # noqa: PLC0415 - only the relay process needs it
        threading.Thread(target=_relay_one, args=(conn, target),
                         daemon=True).start()


def serve_relays(relays: list[tuple[socket.socket, str]]) -> None:
    """The relay process: forward every connection to each listener to its
    unix socket, until the shim ends it with the command. Stdin is closed:
    the relay reads only its sockets. So are the caller's refusal and taken
    descriptors, which only the shim writes: the relay lives as long as the
    command, and holds nothing a refusal could be forged through (DESIGN §6
    *A confinement failure stops the run; it is never a result*)."""
    import threading  # noqa: PLC0415 - only the relay process needs it
    os.close(0)
    for fd in (_REFUSAL_FD, _TAKEN_FD):
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
    threads = [threading.Thread(target=_relay_accept, args=pair, daemon=True)
               for pair in relays]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    os._exit(0)


def start_relays(relays: list[tuple[socket.socket, str]]) -> int | None:
    """Fork the relay process (a child of the shim, so it ends with the
    command, *A confined command's processes end with it*), close the
    listeners here and return its pid."""
    if not relays:
        return None
    pid = os.fork()
    if pid == 0:
        try:
            serve_relays(relays)
        finally:
            os._exit(0)
    for s, _target in relays:
        s.close()
    return pid


# --- PID 1 of a held-out sandbox --------------------------------------------
# DESIGN §6 *A confined command's processes end with it*: inside a PID
# namespace the container's `/proc` shows the wrong pids and a `/proc` of the
# namespace cannot be mounted, so PID 1 ends the namespace with `kill(-1)`,
# which reaches exactly its processes, and the kernel kills whatever is left
# when it exits.

def _noop(_sig: int, _frame: object) -> None:
    return None


def _end_namespace(grace: float) -> None:
    for sig, wait in ((signal.SIGTERM, grace), (signal.SIGKILL, None)):
        try:
            os.kill(-1, sig)
        except ProcessLookupError:
            pass
        deadline = time.monotonic() + (wait or 0.0)
        while True:
            try:
                pid, _ = os.waitpid(-1, 0 if wait is None else os.WNOHANG)
            except ChildProcessError:
                return
            except InterruptedError:
                continue
            if pid == 0:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.02)


def _copy_report(report: str | None, report_fd: int | None) -> None:
    if report is None or report_fd is None:
        return
    try:
        os.lseek(report_fd, 0, os.SEEK_SET)
        os.ftruncate(report_fd, 0)
        with open(report, "rb") as f:
            left = REPORT_MAX_BYTES
            while left > 0:
                chunk = f.read(min(1 << 20, left))
                if not chunk:
                    break
                os.write(report_fd, chunk)
                left -= len(chunk)
    except OSError:
        pass


def run_as_init(cmd: list[str], report: str | None, report_fd: int | None,
                status_w: int, mask: set, enter: "tuple | None" = None
                ) -> None:
    """PID 1 of the sandbox's PID namespace: run `cmd`, end every process of
    the namespace, copy the report to `report_fd`, write how `cmd` ended to
    `status_w` (`E<code>` or `S<signal>`) and exit. An init receives only
    signals it handles, so the stop signals and SIGCHLD get handlers; they
    stay blocked from before this process was forked (so one forwarded
    early is pending, not lost) and are taken with `sigwaitinfo`. `cmd`
    starts with `mask`, the mask the shim was started with."""
    watched = {signal.SIGCHLD, *_STOP_SIGNALS}
    for sig in watched:
        signal.signal(sig, _noop)
    signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    child = os.fork()
    if child == 0:
        signal.pthread_sigmask(signal.SIG_SETMASK, mask)
        for sig in watched:
            signal.signal(sig, signal.SIG_DFL)
        restore_default_signals()
        _before_exec(enter)
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
            info = _wait_signal(watched)
            if info is not None and info.si_signo in _STOP_SIGNALS:
                stopped = info.si_signo
    _end_namespace(STOP_GRACE_SEC if stopped is not None else END_GRACE_SEC)
    _copy_report(report, report_fd)
    if stopped is not None:
        word = f"S{stopped}"
    elif status is not None and os.WIFSIGNALED(status):
        word = f"S{os.WTERMSIG(status)}"
    elif status is not None and os.WIFEXITED(status):
        word = f"E{os.WEXITSTATUS(status)}"
    else:
        word = "E1"
    try:
        os.write(status_w, word.encode())
    except OSError:
        pass
    os._exit(0)


def _close_fds_except(keep: set[int]) -> None:
    lo = 3
    for fd in sorted(k for k in keep if k >= 3):
        if fd > lo:
            os.closerange(lo, fd)
        lo = fd + 1
    os.closerange(lo, 1 << 20)


def console_open() -> bool:
    """True when this process can open `/dev/console` for reading. Under
    `nerdctl run -it` it is the operator's pty, and a read-only descriptor
    is enough to read what the operator types and to turn the terminal's
    echo and signals off (review round 33 isolation Q1)."""
    try:
        fd = os.open(CONSOLE, os.O_RDONLY | os.O_NOCTTY | os.O_NONBLOCK
                     | os.O_CLOEXEC)
    except OSError:
        return False
    os.close(fd)
    return True


def _confine_or_126(rw: list[str], ro: list[str]) -> int | None:
    try:
        install_seccomp()
        # `/proc`, read: the shim's own need — `descendants` reads it to
        # find what the command left. Every grant leerie derives reads it
        # anyway (it is outside the state root).
        confine(rw, ro + ["/proc"])
    except OSError as e:
        return _refused(
            f"leerie: cannot confine the worker, so it does not run: {e} "
            "(DESIGN §6 *A worker writes only where it is granted*)")
    # Confined as the command will be: an isolated process's `/dev` has no
    # console, and no grant names one, so an open that succeeds is a broken
    # invariant, refused rather than run.
    if console_open():
        return _refused(
            f"leerie: the command does not run, because it could open "
            f"{CONSOLE} (DESIGN §6 *No confined process holds or can "
            "open the operator's terminal*)")
    return None


def _isolation_failed(e: BaseException) -> int:
    return _refused(
        f"leerie: cannot isolate the command, so it does not run: {e} "
        "(DESIGN §6 *Every confined process has its own `/tmp`, "
        "`/dev/shm` and IPC*)")


def run_sandbox(cmd: list[str], rw: list[str], ro: list[str], view: str,
                place_pairs: list[tuple[str, str]], report: str | None,
                report_fd: int | None, worker_root: str | None = None, *,
                over_pairs: list[tuple[str, str]] = (),
                remove_paths: list[str] = (), home: str | None = None,
                home_layers: list[str] = (), home_bytes: int = 0) -> int:
    """`--sandbox`: build it, fork PID 1 of the new PID namespace, forward
    a stop signal to it, and exit as `cmd` did."""
    enter = None
    try:
        _n, view_ro = isolate(rw, ro, sandbox=True, view=view,
                              place_pairs=place_pairs,
                              over_pairs=over_pairs,
                              remove_paths=remove_paths,
                              worker_root=worker_root, home=home,
                              home_layers=home_layers,
                              home_bytes=home_bytes)
        if worker_root is not None:
            ro = ro + view_ro
            enter = ("view", WORKER_VIEW, worker_env_changes(worker_root))
    except PlacementTaken as e:
        return _taken(e)
    except (IsolationError, OSError) as e:
        return _isolation_failed(e)
    why = _undumpable()
    if why is not None:
        return _isolation_failed(OSError(why))
    _watch_home(home, home_bytes)
    if report_fd is not None:
        os.set_inheritable(report_fd, False)
    status_r, status_w = os.pipe()
    watched = {signal.SIGCHLD, *_STOP_SIGNALS}
    old = signal.pthread_sigmask(signal.SIG_BLOCK, watched)
    init = os.fork()
    if init == 0:
        # The watched signals stay blocked until `run_as_init` has its
        # handlers: a stop forwarded before then waits, pending.
        os.close(status_r)
        try:
            join_session_keyring()
        except IsolationError as e:
            os._exit(_isolation_failed(e))
        _close_fds_except({0, 1, 2, status_w}
                          | ({report_fd} if report_fd is not None else set())
                          | ({_REFUSAL_FD} if _REFUSAL_FD is not None
                             else set()))
        rc = _confine_or_126(rw + [view] + list(PRIVATE)
                             + ([home] if home is not None else []), ro)
        if rc is not None:
            os.write(status_w, f"E{rc}".encode())
            os._exit(0)
        run_as_init(cmd, report, report_fd, status_w, old, enter)
    os.close(status_w)
    while True:
        try:
            pid, _ = os.waitpid(init, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == init:
            break
        info = _wait_signal(watched)
        if info is not None and info.si_signo in _STOP_SIGNALS:
            try:
                os.kill(init, signal.SIGTERM)
            except OSError:
                pass
    with os.fdopen(status_r, "rb") as f:
        word = f.read().decode(errors="replace")
    full = _home_full()
    if full is not None:
        return _refused(full)
    if word.startswith("S") and word[1:].isdigit():
        return _die_of(int(word[1:]))
    if word.startswith("E") and word[1:].isdigit():
        return int(word[1:])
    return 1


def _fd_numbers() -> list[int]:
    try:
        names = os.listdir("/proc/self/fd")
    except OSError:
        names = [str(fd) for fd in range(3)]
    return sorted(int(n) for n in names if n.isdigit())


def leave_terminal() -> str | None:
    """Hold no descriptor of a terminal before anything is confined (DESIGN
    §6 *No confined process holds or can open the operator's terminal*):
    in an interactive run the orchestrator's fds 0-2 are the
    operator's pty, open read-write, and Landlock checks opens, not the
    descriptors a process inherits. A terminal on fd 0 is replaced by
    `/dev/null` (a confined command reads no input); a terminal on any other
    descriptor is a spawner's defect, refused. A controlling terminal is
    left (`setsid`, or `TIOCNOTTY` when the shim leads its process group),
    so `/dev/tty` names nothing. Returns why the shim must not run, or
    None."""
    if os.isatty(0):
        null = os.open(os.devnull, os.O_RDONLY)
        os.dup2(null, 0)
        os.close(null)
    for fd in _fd_numbers():
        if fd != 0 and os.isatty(fd):
            return (f"its fd {fd} is a terminal; a confined process writes "
                    "only to a pipe or a file")
    try:
        tty = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY | os.O_CLOEXEC)
    except OSError:
        return None
    try:
        if os.getsid(0) == os.getpid():
            # A session leader that has a terminal: giving it up would hang
            # up the terminal's foreground group.
            return "it leads a session that has a controlling terminal"
        try:
            os.setsid()
        except OSError:
            fcntl.ioctl(tty, termios.TIOCNOTTY)
    except OSError as e:
        return f"it cannot leave its controlling terminal: {e}"
    finally:
        os.close(tty)
    try:
        os.close(os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY))
    except OSError:
        return None
    return "it cannot leave its controlling terminal"


def _parse(argv: list[str]) -> dict | str:
    """The shim's options, or a usage error."""
    if "--" not in argv:
        return ("usage: landlock_exec.py [--isolate [--egress SOCK] "
                "[--forward PORT=SOCK]... [--link-root DIR] | --sandbox --view TREE "
                "[--place SRC=REL]... [--replace SRC=REL]... [--remove REL]... "
                "[--report PATH --report-fd N]] "
                "[--rw PATH]... [--ro PATH]... -- CMD")
    sep = argv.index("--")
    o: dict = {"rw": [], "ro": [], "isolate": False, "sandbox": False,
               "view": None, "place": [], "replace": [], "remove": [],
               "report": None,
               "report_fd": None, "egress": None, "forward": [],
               "worker_root": None, "cli": False, "refusal_fd": None,
               "taken_fd": None, "link_root": None,
               "home": None, "home_layer": [], "home_bytes": None,
               "home_upper": None,
               "cmd": argv[sep + 1:]}
    opts = argv[:sep]
    i = 0
    while i < len(opts):
        k = opts[i]
        if k in ("--isolate", "--sandbox", "--cli"):
            o[k[2:]] = True
            i += 1
            continue
        if k not in ("--rw", "--ro", "--view", "--place", "--replace",
                     "--remove", "--report",
                     "--report-fd", "--egress", "--forward",
                     "--worker-root", "--refusal-fd", "--taken-fd",
                     "--link-root", "--home", "--home-layer",
                     "--home-bytes", "--home-upper") \
                or i + 1 >= len(opts):
            return f"landlock_exec.py: bad option {k!r}"
        v = opts[i + 1]
        if k in ("--rw", "--ro"):
            o[k[2:]].append(v)
        elif k in ("--place", "--replace"):
            src, eq, rel = v.partition("=")
            if not eq or not src or not rel:
                return f"landlock_exec.py: bad {k} {v!r}"
            o[k[2:]].append((src, rel))
        elif k == "--remove":
            if not v:
                return f"landlock_exec.py: bad --remove {v!r}"
            o["remove"].append(v)
        elif k == "--egress":
            if not os.path.isabs(v):
                return f"landlock_exec.py: bad --egress {v!r}"
            o["egress"] = v
        elif k == "--forward":
            port, eq, sock = v.partition("=")
            if (not eq or not port.isdigit() or not 0 < int(port) < 65536
                    or not os.path.isabs(sock)):
                return f"landlock_exec.py: bad --forward {v!r}"
            o["forward"].append((int(port), sock))
        elif k == "--link-root":
            if not os.path.isabs(v) or os.path.normpath(v) in ("/", "//"):
                return f"landlock_exec.py: bad --link-root {v!r}"
            o["link_root"] = os.path.normpath(v)
        elif k in ("--home", "--home-layer", "--home-upper"):
            if not os.path.isabs(v) or os.path.normpath(v) in ("/", "//"):
                return f"landlock_exec.py: bad {k} {v!r}"
            if k == "--home":
                o["home"] = os.path.normpath(v)
            elif k == "--home-upper":
                o["home_upper"] = os.path.normpath(v)
            else:
                o["home_layer"].append(os.path.normpath(v))
        elif k == "--home-bytes":
            if not v.isdigit() or int(v) <= 0:
                return f"landlock_exec.py: bad --home-bytes {v!r}"
            o["home_bytes"] = int(v)
        elif k == "--worker-root":
            if not os.path.isabs(v) or os.path.normpath(v) in ("/", "//"):
                return f"landlock_exec.py: bad --worker-root {v!r}"
            o["worker_root"] = os.path.normpath(v)
        elif k == "--report-fd":
            if not v.isdigit():
                return f"landlock_exec.py: bad --report-fd {v!r}"
            o["report_fd"] = int(v)
        elif k in ("--refusal-fd", "--taken-fd"):
            if not v.isdigit() or int(v) < 3:
                return f"landlock_exec.py: bad {k} {v!r}"
            o[k[2:].replace("-", "_")] = int(v)
        else:
            o[k[2:]] = v
        i += 2
    if not o["cmd"]:
        return "landlock_exec.py: no command"
    if o["isolate"] and o["sandbox"]:
        return "landlock_exec.py: --isolate and --sandbox are exclusive"
    if o["sandbox"] and not o["view"]:
        return "landlock_exec.py: --sandbox needs --view"
    if not o["sandbox"] and (o["view"] or o["place"] or o["replace"]
                             or o["remove"] or o["report"]
                             or o["report_fd"] is not None
                             or o["taken_fd"] is not None):
        return ("landlock_exec.py: --view, --place, --replace, --remove, "
                "--report and --taken-fd need --sandbox")
    if not o["isolate"] and (o["egress"] or o["forward"]):
        return "landlock_exec.py: --egress and --forward need --isolate"
    if (o["report"] is None) != (o["report_fd"] is None):
        return "landlock_exec.py: --report and --report-fd go together"
    if o["worker_root"] and not (o["isolate"] or o["sandbox"]):
        return "landlock_exec.py: --worker-root needs --isolate or --sandbox"
    if o["home"] and not (o["isolate"] or o["sandbox"]):
        return "landlock_exec.py: --home needs --isolate or --sandbox"
    if (o["home"] is None) != (o["home_bytes"] is None) \
            or ((o["home_layer"] or o["home_upper"]) and o["home"] is None):
        return ("landlock_exec.py: --home and --home-bytes go together, and "
                "--home-layer needs them")
    if o["home_upper"] and o["sandbox"]:
        return "landlock_exec.py: --home-upper needs --isolate"
    if o["link_root"] and not o["isolate"]:
        return "landlock_exec.py: --link-root needs --isolate"
    if o["cli"] and not (o["worker_root"] and o["isolate"]):
        return "landlock_exec.py: --cli needs --isolate and --worker-root"
    return o


def main(argv: list[str]) -> int:
    o = _parse(argv)
    if isinstance(o, str):
        print(o, file=sys.stderr)
        return 2
    global _REFUSAL_FD, _TAKEN_FD
    for key, opt in (("refusal_fd", "--refusal-fd"), ("taken_fd",
                                                      "--taken-fd")):
        if o[key] is not None:
            # Close-on-exec before anything else: no command ever holds it.
            try:
                os.set_inheritable(o[key], False)
            except OSError as e:
                print(f"landlock_exec.py: bad {opt}: {e}", file=sys.stderr)
                return 2
    _REFUSAL_FD = o["refusal_fd"]
    _TAKEN_FD = o["taken_fd"]
    why = leave_terminal()
    if why is not None:
        return _refused(
            f"leerie: the command does not run, because {why} (DESIGN §6 "
            "*No confined process holds or can open the operator's "
            "terminal*)")
    rw, ro, cmd = o["rw"], o["ro"], o["cmd"]
    wr = o["worker_root"]
    home = {"home": o["home"], "home_layers": o["home_layer"],
            "home_bytes": o["home_bytes"] or 0}
    if o["sandbox"]:
        return run_sandbox(cmd, rw, ro, o["view"], o["place"], o["report"],
                           o["report_fd"], wr, over_pairs=o["replace"],
                           remove_paths=o["remove"], **home)
    relays: list[tuple[socket.socket, str]] = []
    enter = None
    if o["isolate"]:
        targets = list(o["forward"]) + ([(0, o["egress"])] if o["egress"]
                                        else [])
        try:
            _n, view_ro = isolate(rw, ro, sockets=[t for _p, t in targets],
                                  worker_root=wr,
                                  link_root=o["link_root"],
                                  home_upper=o["home_upper"], **home)
            if wr is not None:
                ro = ro + view_ro
                enter = (("cli", WORKER_VIEW, wr) if o["cli"] else
                         ("view", WORKER_VIEW, worker_env_changes(wr)))
            join_session_keyring()
            # Fixed ports first, so the kernel's pick cannot take one.
            relays = [(relay_listener(port), target)
                      for port, target in targets]
        except (IsolationError, OSError) as e:
            for s, _t in relays:
                s.close()
            return _isolation_failed(e)
        # Non-dumpable before the relay is forked, not only before the
        # command is (`run`): the relay inherits it, and lives as long as
        # the command (DESIGN §6 *A confinement failure stops the run; it is
        # never a result*). After `isolate`, as in `run_sandbox`.
        why = _undumpable()
        if why is not None:
            for s, _t in relays:
                s.close()
            return _isolation_failed(OSError(why))
        if o["egress"]:
            os.environ.update(proxy_env(relays[-1][0].getsockname()[1]))
        rw = rw + list(PRIVATE) + _watch_home(
            o["home"], o["home_bytes"], watch=o["home_upper"] is None)
    rc = _confine_or_126(rw, ro)
    if rc is not None:
        return rc
    return run(cmd, start_relays(relays), enter)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
