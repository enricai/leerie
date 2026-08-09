#!/usr/bin/env python3
"""Cgroup broker for leerie worker containment.

Why this exists (DESIGN §6 *Memory containment*): the orchestrator runs
as the dropped-privilege `leerie` user (rootful) or a nested-userns-
remapped identity (rootless), but cgroup enforcement — creating a worker
cgroup, setting its limits, enrolling the worker PID, tearing it down —
requires an identity that actually owns (or was delegated) the relevant
cgroup subtree:
  - Migrating a task into a cgroup requires write access to
    `cgroup.procs` of the destination AND the nearest common ancestor of
    source and destination (NOT the source itself). Workers are born in
    a container-runtime-owned scope (`/system.slice/nerdctl-*.scope`
    locally, the machine scope on Fly, or — rootless — a scope the
    rootless containerd's own cgroup driver created).
  - A cgroup subtree keeps its controller limit files (`pids.max`,
    `memory.max`) owned by whoever created it. Real root, or a UID that
    was delegated the parent directory and created the subtree itself,
    can set these; anyone else cannot.

This broker is launched by `scripts/container-entry.sh` at PID 1
*before* the privilege drop to `leerie` — real root in the rootful case,
the rootlesskit-mapped host UID in the rootless case — and the
orchestrator drives it over a Unix socket regardless of which identity
that is. It is the single most-privileged surface in the worker path, so
it is deliberately tiny and validates every input.

Rootless: rootlesskit maps the container's "root" to the real host UID,
which has no privilege over the top-level `/sys/fs/cgroup` — but systemd
already delegates a writable subtree per login session at
`/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/`, chowned
to that UID. `container-entry.sh` anchors `leerie.slice` there instead of
the top level (via `LEERIE_CGROUP_V2_ROOT`, below) when it detects
rootless mode. The broker runs as that same delegated UID, and any
cgroup it creates under that subtree gets that UID's ownership on every
interface file, so create/enroll/limit-set all work directly. The
common-ancestor requirement for migration is satisfied because
`user@<uid>.service` — the ancestor of both the worker's native container
scope and our `leerie.slice` — is exactly what's delegated.

Protocol (newline-terminated, one request per connection):
  ping                          -> OK
  probe                         -> OK <hierarchy>   (round-trips create+enroll+destroy of a throwaway cgroup)
  create <sid> <mem_bytes> <pids_max>  -> OK | ERR <msg>
  enroll <sid> <pid>            -> OK | ERR <msg>
  destroy <sid>                 -> OK | ERR <msg>
  stat <sid>                    -> OK <pids.current> <pids.max> <pids.events.max> <memory.events.oom_kill> | ERR <msg>
  slice                         -> OK <leerie.slice memory.max, -1 if unset> <live sibling worker cgroups> <unreclaimable bytes, -1 if unreadable>
                                    (read-only. Used to gate worker admission on
                                    measured slice headroom: slice memory.max
                                    minus UNRECLAIMABLE usage — page cache is
                                    excluded, so this is not memory.current.
                                    Per-worker sizing is load-independent and
                                    does NOT consume the live-sibling count.)

`<sid>` is validated against `^[A-Za-z0-9._-]+$` and only ever composed
into `leerie-w-<sid>` under the fixed slice — no path traversal. `<pid>`,
`<mem_bytes>`, `<pids_max>` must be non-negative integers.

cgroup v1 vs v2 (both reproduced): on v2 (Colima, and rootless via the
delegated user slice) we write the unified
`<v2-root>/leerie.slice/leerie-w-<sid>/{pids,memory}.max`. On v1/hybrid
(Fly Firecracker VMs) the unified mount has no controllers, so we write
the split hierarchies (`/sys/fs/cgroup/pids/leerie.slice/...`,
`/sys/fs/cgroup/memory/leerie.slice/...`) — v1/hybrid is always rooted at
the literal `/sys/fs/cgroup`, since it's only ever observed on Fly
(rootful Firecracker), never rootless. The hierarchy is detected once
at startup and reported by `probe` so the orchestrator's telemetry records
which path was taken.
"""

import contextlib
import os
import re
import signal
import socket
import sys
import time

SOCK_PATH = "/run/leerie-cgroup.sock"
SLICE = "leerie.slice"
# v1/hybrid (Fly Firecracker VMs) is always the literal cgroupfs root — it's
# never observed rootless, so this one is not overridable.
V1_ROOT = "/sys/fs/cgroup"
# v2 unified root. Rootful (Colima, Fly): the literal cgroupfs root.
# Rootless: container-entry.sh sets LEERIE_CGROUP_V2_ROOT to the
# systemd-delegated user slice (see module docstring) before launching us,
# since we have no privilege over the true top level there.
V2_ROOT = os.environ.get("LEERIE_CGROUP_V2_ROOT", "/sys/fs/cgroup")
_SID_RE = re.compile(r"^[A-Za-z0-9._-]+$")

# `destroy` waits for the killed subtree to actually drain before rmdir'ing
# (see _drain_then_rmdir). 10s because the workers that leaked were
# conformers running full test suites, whose process trees reached the
# hundreds; a sub-second budget is not enough for those to die. The
# orchestrator's destroy is fire-and-forget on a finished worker, so this
# wait is off the critical path.
_DESTROY_DRAIN_TIMEOUT_SEC = 10.0
_DESTROY_DRAIN_POLL_SEC = 0.05
# v1 waits for a migration that has already either succeeded or failed, not
# for an in-flight async kill (v1 has no `cgroup.kill`) — so a long wait
# cannot change the outcome, and _v1_destroy pays it twice, once per
# controller dir. Short enough to stay off the critical path, long enough to
# cover a pid dying between the procs read and its migration write.
_V1_DRAIN_TIMEOUT_SEC = 0.5

# Hierarchy is one of: "v2", "v1", "none". Decided at startup by _detect().
_HIER = "none"


def _log(msg: str) -> None:
    # PID 1's stdout is the container log; prefix so these lines are greppable.
    print(f"[cgroup-broker] {msg}", file=sys.stderr, flush=True)


def _detect() -> str:
    """Decide which cgroup hierarchy is usable. v2 if the unified mount
    exposes controllers we can enable; v1 if the split controller mounts
    exist; else none."""
    try:
        if os.path.isfile(f"{V2_ROOT}/cgroup.controllers"):
            # Unified. Ensure pids+memory are delegatable into children.
            _write(f"{V2_ROOT}/{SLICE}/cgroup.subtree_control",
                   "+pids +memory", swallow=True, mkparent=True)
            ctrls = _read(f"{V2_ROOT}/{SLICE}/cgroup.controllers")
            if "pids" in ctrls and "memory" in ctrls:
                return "v2"
        # v1/hybrid: split controllers each at their own mount. Always at
        # the literal root — never observed rootless (see V1_ROOT above).
        if os.path.isdir(f"{V1_ROOT}/pids") and os.path.isdir(f"{V1_ROOT}/memory"):
            return "v1"
    except OSError as e:
        _log(f"detect error: {e}")
    return "none"


def _read(path: str) -> str:
    try:
        with open(path) as f:
            return f.read()
    except OSError:
        return ""


def _write(path: str, val: str, swallow: bool = False,
           mkparent: bool = False) -> None:
    if mkparent:
        os.makedirs(os.path.dirname(path), exist_ok=True)
    try:
        with open(path, "w") as f:
            f.write(val)
    except OSError:
        if not swallow:
            raise


# --- pids.* parsers (for the read-only `stat` verb) ------------------------
# Missing/unreadable files (containment off, race with destroy) degrade to
# safe sentinels rather than raising: current/events → 0, max → -1 ("no cap
# known"), so the orchestrator never false-detects exhaustion from a read
# error. `pids.max` may be the literal string "max" (unlimited).

def _pids_current(path: str) -> int:
    try:
        return int(_read(path).strip())
    except ValueError:
        return 0


def _pids_max(path: str) -> int:
    raw = _read(path).strip()
    if not raw or raw == "max":
        return -1
    try:
        return int(raw)
    except ValueError:
        return -1


def _pids_events_max(path: str) -> int:
    # cgroup v2 pids.events: newline-separated "<key> <count>" lines. The
    # `max` key counts fork denials — the definitive PID-exhaustion signal.
    for line in _read(path).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "max":
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


def _memory_events_oom(path: str) -> int:
    # cgroup v2/v1 memory.events: newline-separated "<key> <count>" lines.
    # The `oom_kill` key counts times the OOM killer fired inside this
    # cgroup — the definitive memory-exhaustion signal (mirrors
    # _pids_events_max above). Missing/unreadable file degrades to 0.
    for line in _read(path).splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] == "oom_kill":
            try:
                return int(parts[1])
            except ValueError:
                return 0
    return 0


# --- v2 paths --------------------------------------------------------------

def _v2_dir(sid: str) -> str:
    return f"{V2_ROOT}/{SLICE}/leerie-w-{sid}"


def _v2_create(sid: str, mem: int, pids: int) -> None:
    d = _v2_dir(sid)
    os.makedirs(d, exist_ok=True)
    if pids > 0:
        _write(f"{d}/pids.max", str(pids))
    if mem > 0:
        _write(f"{d}/memory.max", str(mem))
        # Match _cgroup_create's prior behavior: no swap padding to delay OOM.
        _write(f"{d}/memory.swap.max", "0", swallow=True)


def _v2_enroll(sid: str, pid: int) -> None:
    _write(f"{_v2_dir(sid)}/cgroup.procs", str(pid))


def _drain_then_rmdir(d: str, budget_sec: float) -> str | None:
    """Wait for `d/cgroup.procs` to drain, then rmdir it. Returns None on
    success or the OSError message once `budget_sec` is exhausted.

    The budget is a parameter rather than a constant because the two
    hierarchies are waiting for different things. On v2 the wait is for an
    ASYNCHRONOUS `cgroup.kill` the kernel is already carrying out, so it
    should be generous. On v1 there is no `cgroup.kill` at all: `_v1_destroy`
    migrates survivors to the parent first, and that either worked or did
    not before we get here — if it failed the processes are still alive and
    unmigrated, so `cgroup.procs` will never drain and a long wait buys
    nothing (twice over, since v1 loops two controller dirs). A short v1
    budget still covers the one race that matters there: a pid dying between
    the read of `cgroup.procs` and the write that migrates it.

    `cgroup.kill` is ASYNCHRONOUS: it delivers SIGKILL and returns, while
    the kernel tears the processes down over the following moments. An
    immediate `os.rmdir` therefore races that teardown and loses with
    EBUSY, leaking the directory — measured, 116 stale `leerie-w-*` dirs of
    which 88 were `-conformer`, whose test-suite subprocess trees reach
    hundreds of PIDs and take correspondingly longer to die. The
    orchestrator had been naming the cause all along: `cgroup destroy
    failed (ERR Device or resource busy); dir may be leaked`.

    Polls the real completion signal (procs draining) rather than sleeping
    a fixed interval, because how long teardown takes scales with the tree
    the worker built. Still returns the error when the budget runs out, so
    a genuine leak is reported rather than silently swallowed by retrying."""
    # Already gone: destroy is idempotent, and there is nothing to drain.
    # Without this, FileNotFoundError (an OSError like any other) would be
    # retried for the whole budget — 10s per call, 20s on v1's two dirs —
    # in a cleanup path that runs for every worker.
    if not os.path.isdir(d):
        return None
    deadline = time.monotonic() + budget_sec
    err: str | None = None
    while True:
        if not _read(f"{d}/cgroup.procs").strip():
            try:
                os.rmdir(d)
                return None
            except FileNotFoundError:
                # Reaped concurrently between the isdir() guard above and
                # here. Same disposition as the guard: already-gone is
                # success. Without this arm the retry loop would spend the
                # whole budget and then report a leak for a dir that is in
                # fact gone.
                return None
            except OSError as e:
                # Drained but still busy (e.g. a child cgroup, or a PID
                # reaped between the read and the rmdir) — keep retrying
                # within the same budget.
                err = str(e.strerror or e)
        if time.monotonic() >= deadline:
            if err is None:
                err = "Device or resource busy"
            return err
        time.sleep(_DESTROY_DRAIN_POLL_SEC)


def _v2_destroy(sid: str) -> str | None:
    """SIGKILL the worker subtree, wait for it to drain, then rmdir. Returns
    None on success, or the OSError message on failure (e.g. a lingering
    process kept it busy for the whole drain budget)."""
    d = _v2_dir(sid)
    _write(f"{d}/cgroup.kill", "1", swallow=True)
    return _drain_then_rmdir(d, _DESTROY_DRAIN_TIMEOUT_SEC)


def _v2_stat(sid: str) -> tuple[int, int, int, int]:
    d = _v2_dir(sid)
    return (_pids_current(f"{d}/pids.current"),
            _pids_max(f"{d}/pids.max"),
            _pids_events_max(f"{d}/pids.events"),
            _memory_events_oom(f"{d}/memory.events"))


# --- v1 paths (split controllers) -----------------------------------------

def _v1_dirs(sid: str) -> tuple[str, str]:
    return (f"{V1_ROOT}/pids/{SLICE}/leerie-w-{sid}",
            f"{V1_ROOT}/memory/{SLICE}/leerie-w-{sid}")


def _v1_create(sid: str, mem: int, pids: int) -> None:
    pdir, mdir = _v1_dirs(sid)
    os.makedirs(pdir, exist_ok=True)
    os.makedirs(mdir, exist_ok=True)
    if pids > 0:
        _write(f"{pdir}/pids.max", str(pids))
    if mem > 0:
        _write(f"{mdir}/memory.limit_in_bytes", str(mem))


def _v1_enroll(sid: str, pid: int) -> None:
    pdir, mdir = _v1_dirs(sid)
    # v1: enroll into every controller hierarchy via its tasks/cgroup.procs.
    _write(f"{pdir}/cgroup.procs", str(pid))
    _write(f"{mdir}/cgroup.procs", str(pid))


def _v1_destroy(sid: str) -> str | None:
    """rmdir both v1 controller dirs. Returns None on success, or the
    first OSError message encountered (pids dir checked before memory
    dir, matching _v1_dirs' ordering)."""
    pdir, mdir = _v1_dirs(sid)
    # v1 has no cgroup.kill; move survivors to the parent then rmdir.
    # Migration is likewise not instantaneous, so the same drain-then-rmdir
    # discipline applies here as on v2 (see _drain_then_rmdir).
    err = None
    for d in (pdir, mdir):
        for pid in _read(f"{d}/cgroup.procs").split():
            _write(f"{os.path.dirname(d)}/cgroup.procs", pid, swallow=True)
        e = _drain_then_rmdir(d, _V1_DRAIN_TIMEOUT_SEC)
        if e is not None and err is None:
            err = e
    return err


def _v1_stat(sid: str) -> tuple[int, int, int, int]:
    # We do not read pids.events on v1 — always report events_max=0 and let
    # detection fall back to the current >= max comparison. (Newer kernels
    # do expose pids.events under the v1 pids controller, but skipping it
    # keeps v1 handling simple and portable across kernels that may or may
    # not have it.) memory.events IS present under the v1 memory
    # controller on modern kernels and carries the same oom_kill key, so
    # we read it from mdir (mirrors the split-controller layout in
    # _v1_dirs/_v1_create/_v1_enroll).
    pdir, mdir = _v1_dirs(sid)
    return (_pids_current(f"{pdir}/pids.current"),
            _pids_max(f"{pdir}/pids.max"),
            0,
            _memory_events_oom(f"{mdir}/memory.events"))


# --- slice-wide read (N9: per-worker sizing must be aware of the shared
# leerie.slice budget and how many sibling worker cgroups are actually
# live right now, not merely how many exist — see N17: finished runs'
# empty leerie-w-* dirs persist, so a bare directory count overcounts). --

def _slice_dir() -> str:
    return f"{V2_ROOT}/{SLICE}" if _HIER == "v2" else f"{V1_ROOT}/memory/{SLICE}"


def _slice_memory_max() -> int:
    # v2: unified memory.max under the slice. v1: split memory controller's
    # memory.limit_in_bytes. Either may read "max"/be absent (no configured
    # ceiling) -> -1, meaning "no shared budget known" to the caller.
    path = (f"{V2_ROOT}/{SLICE}/memory.max" if _HIER == "v2"
            else f"{V1_ROOT}/memory/{SLICE}/memory.limit_in_bytes")
    raw = _read(path).strip()
    if not raw or raw == "max":
        return -1
    try:
        return int(raw)
    except ValueError:
        return -1


def _slice_unreclaimable() -> int:
    """Bytes in the slice the kernel CANNOT free under pressure. Returns -1
    when unreadable, which the caller treats as "unknown" and fails open.

    Deliberately not `memory.current`: that counts page cache, which is
    reclaimable, and on a live host 10.4 GiB of the 20.5 GiB in use was
    `inactive_file`. Gating admission on `memory.current` would therefore
    under-report headroom by half and stall a fleet with ample room.

    v2 counts anon + unevictable + unreclaimable slab. Anon is included
    because worker cgroups run with `memory.swap.max=0`, so it cannot be
    paged out. v1's memory controller spells the same quantities
    `total_rss` / `total_unevictable`."""
    raw = _read(f"{_slice_dir()}/memory.stat")
    if not raw:
        return -1
    fields = ("anon", "unevictable", "slab_unreclaimable") if _HIER == "v2" \
        else ("total_rss", "total_unevictable")
    vals: dict[str, int] = {}
    for line in raw.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in fields:
            try:
                vals[parts[0]] = int(parts[1])
            except ValueError:
                return -1
    # Require the primary anon/rss key; the others are best-effort extras
    # that some kernels omit, and treating their absence as a read failure
    # would fail open far more often than necessary.
    primary = fields[0]
    if primary not in vals:
        return -1
    return sum(vals.values())


def _live_sibling_count() -> int:
    # A worker cgroup is "live" iff it currently has enrolled processes —
    # cgroup.procs is non-empty. Finished runs' cgroups persist empty (N17)
    # and must not be counted; counting bare `leerie-w-*` directories
    # overcounted 7x when this was measured live.
    d = _slice_dir()
    try:
        entries = os.listdir(d)
    except OSError:
        return 0
    procs_name = "cgroup.procs"
    count = 0
    for name in entries:
        if not name.startswith("leerie-w-"):
            continue
        if _read(f"{d}/{name}/{procs_name}").strip():
            count += 1
    return count


# --- dispatch --------------------------------------------------------------

def _valid_sid(sid: str) -> bool:
    return bool(_SID_RE.match(sid)) and ".." not in sid


def _do(verb: str, args: list[str]) -> str:
    if _HIER == "none":
        return "ERR no usable cgroup hierarchy"
    create = _v2_create if _HIER == "v2" else _v1_create
    enroll = _v2_enroll if _HIER == "v2" else _v1_enroll
    destroy = _v2_destroy if _HIER == "v2" else _v1_destroy
    stat = _v2_stat if _HIER == "v2" else _v1_stat

    if verb == "create":
        sid, mem, pids = args[0], int(args[1]), int(args[2])
        if not _valid_sid(sid):
            return "ERR bad sid"
        if mem < 0 or pids < 0:
            return "ERR bad limit"
        create(sid, mem, pids)
        return "OK"
    if verb == "enroll":
        sid, pid = args[0], int(args[1])
        if not _valid_sid(sid) or pid <= 0:
            return "ERR bad sid/pid"
        enroll(sid, pid)
        return "OK"
    if verb == "destroy":
        sid = args[0]
        if not _valid_sid(sid):
            return "ERR bad sid"
        err = destroy(sid)
        if err is not None:
            return f"ERR {err}"
        return "OK"
    if verb == "stat":
        sid = args[0]
        if not _valid_sid(sid):
            return "ERR bad sid"
        cur, mx, ev, oom = stat(sid)
        return f"OK {cur} {mx} {ev} {oom}"
    if verb == "slice":
        return (f"OK {_slice_memory_max()} {_live_sibling_count()} "
                f"{_slice_unreclaimable()}")
    return f"ERR unknown verb {verb}"


def _handle(line: str) -> str:
    parts = line.strip().split()
    if not parts:
        return "ERR empty"
    verb, args = parts[0], parts[1:]
    if verb == "ping":
        return "OK"
    if verb == "probe":
        # End-to-end round-trip on a throwaway sid: the true test of the
        # path workers use (create + enroll a real pid + destroy).
        # Per-probe random suffix so two concurrent containers' startup
        # probes never share `leerie-w-PROBE` under the same VM slice
        # (the --cgroupns=host case). Without it, one container's probe
        # `destroy` (v2 cgroup.kill) could SIGKILL another's probe child
        # or rmdir its cgroup mid-probe — the same cross-run collision
        # class the run-scoped worker cgroup name fixes (see leerie.py
        # `_cgroup_worker_sid`). os.urandom is unique regardless of PID
        # namespace (a container's PIDs reset from 1, so a PID suffix
        # would not be); `PROBE-<8hex>` passes `_valid_sid`.
        sid = f"PROBE-{os.urandom(4).hex()}"
        try:
            child = os.fork()
            if child == 0:  # noqa: SIM115 — child just idles until killed
                signal.pause()
                os._exit(0)
            _do("create", [sid, "0", "64"])
            r = _do("enroll", [sid, str(child)])
            # Reap the child BEFORE destroy: on v2 `_do("destroy")` writes
            # cgroup.kill, which SIGKILLs the enrolled child — if PID 1 then
            # reaps the zombie before our os.kill/waitpid, those would raise
            # ProcessLookupError/ChildProcessError and falsely fail an
            # otherwise-healthy probe. Kill+reap here, tolerate already-gone.
            with contextlib.suppress(ProcessLookupError):
                os.kill(child, signal.SIGKILL)
            with contextlib.suppress(ChildProcessError):
                os.waitpid(child, 0)
            _do("destroy", [sid])
            return f"OK {_HIER}" if r == "OK" else f"ERR probe enroll: {r}"
        except OSError as e:
            return f"ERR probe: {e}"
    try:
        return _do(verb, args)
    except (OSError, ValueError, IndexError) as e:
        return f"ERR {type(e).__name__}: {e}"


def main() -> int:
    global _HIER
    _HIER = _detect()
    # Emit V2_ROOT so a rootless bug report can distinguish "v2 at the
    # systemd-delegated user slice" from "v2 at the top-level cgroupfs" —
    # the whole rootless path is best-effort, so this is the only positive
    # signal of which root the broker actually operated under.
    _log(f"hierarchy={_HIER} v2_root={V2_ROOT}")

    if os.path.exists(SOCK_PATH):
        os.unlink(SOCK_PATH)
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_PATH)
    # World-connectable so the post-privilege-drop leerie orchestrator can
    # reach it on every runtime — including the rootless path where the
    # orchestrator runs in a nested user namespace (`unshare --user`), so
    # its uid as seen here is not leerie's image uid and a chown+0o660
    # lockdown could lock the real client out. Every request is validated
    # (fixed verb set; `_valid_sid` regex; integer pid/limits), so a
    # crafted request cannot escape `leerie-w-<sid>` under the slice.
    # Residual: `enroll` trusts the caller-supplied pid without verifying
    # it belongs to leerie, so a hostile in-container process could enroll
    # an arbitrary pid into a capped cgroup (a local DoS). Accepted: the
    # container runs only leerie's own workers — there is no adversarial
    # process model inside it — and a peer-uid check (SO_PEERCRED) is
    # unreliable across the rootless userns boundary.
    os.chmod(SOCK_PATH, 0o666)
    srv.listen(16)
    _log(f"listening on {SOCK_PATH}")

    # Reap nothing else; ignore SIGCHLD from the probe fork via waitpid above.
    while True:
        try:
            conn, _ = srv.accept()
        except OSError:
            continue
        try:
            # Bound the per-connection recv so a client that connects but
            # never sends can't wedge this single-threaded accept loop and
            # starve every other worker's cgroup op. The orchestrator's
            # `_cgroup_request` always sends immediately, so a well-behaved
            # run never hits this; it guards against a buggy in-container
            # process holding a connection open.
            conn.settimeout(5.0)
            data = conn.recv(4096).decode(errors="replace")
            resp = _handle(data)
            conn.sendall((resp + "\n").encode())
        except OSError as e:
            _log(f"conn error: {e}")
        finally:
            conn.close()


if __name__ == "__main__":
    sys.exit(main())
