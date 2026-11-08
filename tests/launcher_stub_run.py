"""Run the real `leerie` launcher, end to end, against a stub `nerdctl`.

Single owner for the tests that need the launcher's own control flow around
the container (its traps, its background helpers, the argv it hands the
container): `test_launcher_stage_keepalive.py`,
`test_launcher_signal_stops_run.py`, `test_launcher_double_dash.py` and
`test_launcher_repo_top_level.py`.

The stubs are built from shell builtins and absolute paths only (they never
call a program by name, so they can never resolve to themselves), and they
are two: `nerdctl` and `cat`. `run` records its argv, writes the cidfile, and "runs the
container": a `sleep` whose PID it records, so `kill` can end it the way
`nerdctl kill` ends a container. It also keeps leerie's namespace's
container store (`containers(w)`, `seed_container`), which `ps -a`, `inspect`
and `rm` read: `run` puts its container there, and its `--rm` is the
client's, as nerdctl's is, so it happens only if the client is still alive
once the container exits (`STUB_RM_LAG` delays it, `STUB_CLIENT_RM=0`
withholds it). `rm` without `-f` refuses a running container, as nerdctl's
does; removing a container takes one line naming its image out of the
world's `inuse` file, when there is one.

The world decides its own AppArmor state, not the host: the launcher's
probes (`runtime_has_apparmor`, `runtime_apparmor_current` in
scripts/runtime-install.sh) read the kernel's LSM list, its loaded profiles'
names and the installed profile with `cat`, which the world's `cat` answers
from the world (`make_world(apparmor=...)`): "current" (the default: the
kernel runs AppArmor and this checkout's profile is loaded), "none" (no
AppArmor) or "stale" (a different profile installed). Any other `cat` is the
real one. Read from the host, these runs passed only where the loaded profile
was this checkout's: red on a host with an older one, and on any runner whose
kernel lists `apparmor`. The paths are read from runtime-install.sh, and
`tests/test_launcher_stub_run_apparmor.py` proves each state reaches the
launcher.

Every process a launch starts carries `LEERIE_TEST_MARK=<unique>` in its
environment; `reap(mark)` kills every process that still carries it, so a
test that goes red (an orphaned `sleep`, a launcher still waiting) leaves
nothing behind.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
SLEEP = shutil.which("sleep") or "/bin/sleep"
CAT = shutil.which("cat") or "/bin/cat"
PROFILE = REPO_ROOT / "scripts" / "apparmor" / "leerie-container"


def _probe_paths() -> dict[str, str]:
    """The kernel files and installed profile the launcher's AppArmor
    probes read, and the profile's name, from runtime-install.sh's own
    assignments."""
    src = (REPO_ROOT / "scripts" / "runtime-install.sh").read_text()
    names = ("_RUNTIME_LSM_FILE", "_RUNTIME_AA_PROFILES_DIR",
             "LEERIE_APPARMOR_DEST", "LEERIE_APPARMOR_PROFILE")
    return {n: re.search(rf"^{n}=(\S+)$", src, re.M).group(1) for n in names}


_CAT_STUB = r"""#!/bin/bash
w="__WORLD__/kernel"
case "$1" in
  "__LSM__") exec "__CAT__" "$w/lsm" ;;
  "__NAMES__"/*) exec "__CAT__" "$w/names" ;;
  "__DEST__") exec "__CAT__" "$w/installed" ;;
esac
exec "__CAT__" "$@"
"""

APPARMOR_STATES = ("current", "none", "stale")

_STUB = r"""#!/bin/bash
w="__WORLD__"
printf '%s\n' "$*" >> "$w/nerdctl.calls"
ns=""
if [ "$1" = "--namespace" ]; then ns=$2; shift 2; fi
# The container store, leerie's namespace's: one `name|id|status|pid
# label|image` line per container, rewritten with builtins only.
cstore="$w/containers"
c_get() {
  local n i s l m
  [ -f "$cstore" ] || return 1
  while IFS='|' read -r n i s l m; do
    if [ -n "$n" ] && { [ "$n" = "$1" ] || [ "$i" = "$1" ]; }; then
      C_NAME=$n C_ID=$i C_ST=$s C_LB=$l C_IM=$m
      return 0
    fi
  done < "$cstore"
  return 1
}
c_put() {
  local keep=() l
  if [ -f "$cstore" ]; then
    while IFS= read -r l; do
      [ -z "$l" ] || [ "${l%%|*}" = "$1" ] || keep+=("$l")
    done < "$cstore"
  fi
  keep+=("$1|$2|$3|$4|$5")
  printf '%s\n' "${keep[@]}" > "$cstore"
}
c_del() {
  local keep=() l gone=""
  while IFS= read -r l; do
    [ -z "$l" ] || [ "${l%%|*}" = "$1" ] || keep+=("$l")
  done < "$cstore"
  printf '%s\n' ${keep[@]+"${keep[@]}"} > "$cstore"
  # The image it was created from is one container less in use.
  if [ -n "$C_IM" ] && [ -f "$w/inuse" ]; then
    keep=()
    while IFS= read -r l; do
      if [ -z "$gone" ] && [ "$l" = "$C_IM" ]; then gone=1; else keep+=("$l"); fi
    done < "$w/inuse"
    printf '%s\n' ${keep[@]+"${keep[@]}"} > "$w/inuse"
  fi
}
case "$1" in
  run)
    { for a in "$@"; do printf '%s\n' "$a"; done; } > "$w/run.argv"
    printf '%s\n' "$ns" > "$w/run.ns"
    cid="" prev="" name="" label="" image="" crm="" img_next=""
    for a in "$@"; do
      [ -z "$img_next" ] || { image=$a; img_next=""; }
      [ "$prev" = "--cidfile" ] && cid=$a
      [ "$prev" = "--name" ] && name=$a
      [ "$prev" = "-w" ] && img_next=1
      if [ "$prev" = "--label" ]; then
        case "$a" in leerie.launcher_pid=*) label=${a#*=} ;; esac
      fi
      [ "$a" = "--rm" ] && crm=1
      prev=$a
    done
    if [ -n "${STUB_CREATED_SECONDS:-}" ]; then
      # nerdctl writes the cidfile at create, before the task starts; a
      # created container cannot be killed.
      [ -z "$cid" ] || printf 'stubcid\n' > "$cid"
      c_put "$name" stubcid created "$label" "$image"
      printf 'created\n' > "$w/container.state"
      "__SLEEP__" "$STUB_CREATED_SECONDS"
    fi
    c_put "$name" stubcid running "$label" "$image"
    printf 'running\n' > "$w/container.state"
    # The run the orchestrator names in its run-id file (STUB_RUN_ID), at
    # the host path the state root's bind mount gives it.
    if [ -n "${STUB_RUN_ID:-}" ]; then
      sroot="" ridf=""
      for a in "$@"; do
        case "$a" in
          *:/leerie-state) sroot=${a%:/leerie-state} ;;
          LEERIE_RUN_ID_FILE=/leerie-state/*) ridf=${a#LEERIE_RUN_ID_FILE=/leerie-state/} ;;
        esac
      done
      printf '%s\n' "$STUB_RUN_ID" > "$sroot/$ridf"
    fi
    # What the container prints, for a test of the launcher's relay.
    [ -z "${STUB_RUN_PRINT:-}" ] || printf '%s\n' "$STUB_RUN_PRINT"
    if [ -n "${STUB_SHIM:-}" ]; then
      # The runtime's shim, the parent of the container's init, and what
      # the client waits on (`_SHIM`).
      [ -z "$cid" ] || printf 'stubcid\n' > "$cid"
      "$w/shim/containerd-shim-runc-v2" -I -S "$w/shim/shim.py" \
        -namespace "$ns" -id stubcid -address /stub &
      wait "$!"
      rc=$?
    else
      "__SLEEP__" "${STUB_RUN_SECONDS:-0}" &
      printf '%s\n' "$!" > "$w/container.pid"
      [ -z "$cid" ] || printf 'stubcid\n' > "$cid"
      wait "$!"
      rc=$?
    fi
    # The orchestrator's exit status, for a run that ends (STUB_RUN_RC).
    [ -z "${STUB_RUN_RC:-}" ] || rc=$STUB_RUN_RC
    # `--rm` is the client's: done only by a client alive after the
    # container exits (STUB_RM_LAG: how long it takes to get there), and
    # never with STUB_CLIENT_RM=0. A client killed first leaves it.
    # A TERM there ends the client (and its lag) without the `--rm`. The
    # trap is set before the lag starts and names it as `$!`: a TERM between
    # the fork and an assignment would otherwise leave the lag running.
    if [ -n "${STUB_RM_LAG:-}" ]; then
      trap 'kill -KILL "$!" 2>/dev/null; exit 143' TERM
      "__SLEEP__" "$STUB_RM_LAG" &
      wait "$!"
      trap - TERM
    fi
    if c_get "$name"; then
      if [ -n "$crm" ] && [ "${STUB_CLIENT_RM:-1}" = 1 ]; then
        c_del "$name"
      else
        c_put "$name" stubcid exited "$label" "$image"
      fi
    fi
    exit $rc ;;
  kill)
    if [ -f "$w/container.state" ] && [ "$(< "$w/container.state")" = created ]; then
      printf 'container not running\n' >&2; exit 1
    fi
    [ ! -f "$w/container.pid" ] || kill -KILL "$(< "$w/container.pid")" 2>/dev/null
    if c_get "${!#}"; then c_put "$C_NAME" "$C_ID" exited "$C_LB" "$C_IM"; fi
    exit 0 ;;
  ps)
    [ "$ns" = leerie ] && [ -f "$cstore" ] || exit 0
    while IFS='|' read -r n i s l m; do
      [ -n "$n" ] || continue
      case "$s" in
        created) s=Created ;;
        running) s=Up ;;
        exited) s="Exited (0) 2 minutes ago" ;;
        paused) s=Paused ;;
      esac
      printf '%s|%s\n' "$n" "$s"
    done < "$cstore"
    exit 0 ;;
  inspect)
    fmt="" prev=""
    for a in "$@"; do [ "$prev" = -f ] && fmt=$a; prev=$a; done
    { [ "$ns" = leerie ] && c_get "${!#}"; } || exit 0
    case "$fmt" in
      *leerie.launcher_pid*) printf '%s\n' "$C_LB" ;;
      *State.Status*) printf '%s\n' "$C_ST" ;;
    esac
    exit 0 ;;
  rm)
    [ "$ns" = leerie ] || exit 0
    # A runtime that does not answer (STUB_RM_HANG=1).
    [ "${STUB_RM_HANG:-}" != 1 ] || exec "__SLEEP__" 3600
    c_get "${!#}" || { printf 'Error: no such container: %s\n' "${!#}" >&2; exit 1; }
    if [ "$C_ST" = running ] || [ "$C_ST" = paused ]; then
      if [ "$2" != -f ]; then
        printf 'Error: cannot remove a %s container\n' "$C_ST" >&2; exit 1
      fi
      if [ "$C_ID" = stubcid ] && [ -f "$w/container.pid" ]; then
        kill -KILL "$(< "$w/container.pid")" 2>/dev/null
      fi
    fi
    c_del "$C_NAME"
    exit 0 ;;
  info|version) printf 'Version: 2.0.0\n'; exit 0 ;;
  *) exit 0 ;;
esac
"""


# The runtime's shim, for `STUB_SHIM=1`: python by its absolute path, run
# through a link named as containerd's runc shim is, so its comm reads
# `containerd-shim` and its argv carries `-id stubcid`, as the real one's do.
# It forks the container's init, which sleeps STUB_RUN_SECONDS, records the
# run's end (STUB_END_CODE into STUB_END_FILE, a line written whole, as the
# orchestrator's `orchestrator.exit_code`), lives STUB_LINGER more (the
# container's processes still running after the run recorded its end) and
# exits STUB_INIT_RC. A shim that answers exits with the init's code once it
# has reaped it. One that does not (STUB_SHIM_HANG=1) never exits, so the
# client waiting on it never returns, as a client waiting on a shim that
# stopped answering does; it reaps the init first unless
# STUB_SHIM_REAP=0, which leaves the init a zombie.
_SHIM = r"""import os, sys, time
e = os.environ
init = os.fork()
if init == 0:
    time.sleep(float(e.get("STUB_RUN_SECONDS", "0")))
    if e.get("STUB_END_CODE") and e.get("STUB_END_FILE"):
        tmp = e["STUB_END_FILE"] + ".tmp"
        with open(tmp, "w") as f:
            f.write(e["STUB_END_CODE"] + "\n")
        os.replace(tmp, e["STUB_END_FILE"])
    time.sleep(float(e.get("STUB_LINGER", "0")))
    os._exit(int(e.get("STUB_INIT_RC", "0")))
with open("__WORLD__/container.pid", "w") as f:
    f.write(f"{init}\n")
if e.get("STUB_SHIM_HANG") == "1":
    if e.get("STUB_SHIM_REAP", "1") == "1":
        os.waitpid(init, 0)
    while True:
        time.sleep(3600)
_, status = os.waitpid(init, 0)
sys.exit(os.waitstatus_to_exitcode(status))
"""


def _shim(world: Path) -> None:
    shim = world / "shim"
    shim.mkdir()
    (shim / "shim.py").write_text(_SHIM.replace("__WORLD__", str(world)))
    (shim / "containerd-shim-runc-v2").symlink_to(sys.executable)


def _kernel(world: Path, bindir: Path, apparmor: str) -> None:
    """The world's answers to the launcher's AppArmor probes."""
    if apparmor not in APPARMOR_STATES:
        raise ValueError(f"apparmor={apparmor!r}: one of {APPARMOR_STATES}")
    paths = _probe_paths()
    kernel = world / "kernel"
    kernel.mkdir()
    lsm = "lockdown,capability,landlock,yama"
    if apparmor != "none":
        lsm += ",apparmor"
        (kernel / "names").write_text(
            f"unconfined\n{paths['LEERIE_APPARMOR_PROFILE']}\n")
        (kernel / "installed").write_text(
            PROFILE.read_text() if apparmor == "current"
            else "# an older leerie-container\n")
    (kernel / "lsm").write_text(lsm + "\n")
    stub = bindir / "cat"
    stub.write_text(_CAT_STUB.replace("__WORLD__", str(world))
                    .replace("__LSM__", paths["_RUNTIME_LSM_FILE"])
                    .replace("__NAMES__", paths["_RUNTIME_AA_PROFILES_DIR"])
                    .replace("__DEST__", paths["LEERIE_APPARMOR_DEST"])
                    .replace("__CAT__", CAT))
    stub.chmod(0o755)


def make_world(tmp_path: Path, *, apparmor: str = "current") -> dict:
    """A git repository with a `sub/` directory, a HOME with the Claude
    config the launcher stages, a state root, a stub `nerdctl`, and the
    kernel's AppArmor answers (`apparmor`: one of `APPARMOR_STATES`)."""
    world = tmp_path / "world"
    repo, home, bindir = world / "repo", world / "home", world / "bin"
    for d in (repo / "sub", home / ".claude", bindir):
        d.mkdir(parents=True)
    (home / ".claude.json").write_text("{}\n")
    (home / ".gitconfig").write_text(
        "[user]\n\temail = ci@leerie.invalid\n\tname = Leerie CI\n")
    (repo / "README.md").write_text("r\n")
    git = ["git", "-c", "user.name=t", "-c", "user.email=t@t.invalid"]
    subprocess.run([*git, "init", "-q", str(repo)], check=True)
    subprocess.run([*git, "-C", str(repo), "add", "."], check=True)
    subprocess.run([*git, "-C", str(repo), "commit", "-qm", "init"],
                   check=True)
    stub = bindir / "nerdctl"
    stub.write_text(_STUB.replace("__WORLD__", str(world))
                    .replace("__SLEEP__", SLEEP))
    stub.chmod(0o755)
    _kernel(world, bindir, apparmor)
    _shim(world)
    return {"world": world, "repo": repo, "home": home, "bin": bindir,
            "state": world / "state", "mark": uuid.uuid4().hex}


def launch_env(w: dict, **extra: str) -> dict:
    env = dict(os.environ)
    for k in [k for k in env if k.startswith("LEERIE_")]:
        del env[k]
    env.update({
        "PATH": f"{w['bin']}{os.pathsep}{env['PATH']}",
        "HOME": str(w["home"]),
        "LEERIE_STATE_DIR": str(w["state"]),
        "LEERIE_HOST_DIR": str(w["world"] / "host"),
        "LEERIE_TEST_MARK": w["mark"],
    })
    env.update(extra)
    return env


def launch(w: dict, args: list[str], *, cwd: Path | None = None,
           **popen) -> subprocess.Popen:
    """Start the launcher in its own session (a signal to it reaches it
    alone, as `kill <pid>` does)."""
    env = launch_env(w, **popen.pop("env_extra", {}))
    return subprocess.Popen([str(LAUNCHER), *args], cwd=cwd or w["repo"],
                            env=env, start_new_session=True, **popen)


def run_to_end(w: dict, args: list[str], *, cwd: Path | None = None,
               env_extra: dict | None = None) -> tuple[int, str]:
    """Run the launcher to its exit, its output to a file (not a pipe, so a
    process that outlives it cannot hold the caller), and reap whatever it
    left. Returns (exit code, output)."""
    log = w["world"] / f"launch-{uuid.uuid4().hex}.log"
    try:
        with open(log, "wb") as f:
            p = launch(w, args, cwd=cwd, env_extra=env_extra or {},
                       stdout=f, stderr=subprocess.STDOUT,
                       stdin=subprocess.DEVNULL)
            rc = p.wait(timeout=120)
    finally:
        reap(w["mark"])
    return rc, log.read_text(errors="replace")


def run_argv(w: dict) -> list[str]:
    """The argv the launcher gave `nerdctl run` (empty: it never ran)."""
    f = w["world"] / "run.argv"
    return f.read_text().splitlines() if f.exists() else []


def containers(w: dict) -> dict[str, str]:
    """The stub's container store, leerie's namespace's: name -> status
    (`created`, `running`, `exited`, `paused`)."""
    f = w["world"] / "containers"
    if not f.exists():
        return {}
    return {l.split("|")[0]: l.split("|")[2]
            for l in f.read_text().splitlines() if l}


def seed_container(w: dict, name: str, status: str, launcher_pid: str = "",
                   image: str = "", cid: str = "") -> None:
    """Put a container in the stub's store, as a launch or the operator
    left it."""
    with open(w["world"] / "containers", "a") as f:
        f.write(f"{name}|{cid or name + '-id'}|{status}|{launcher_pid}|"
                f"{image}\n")


def marked_pids(mark: str) -> list[int]:
    """Every live process carrying `LEERIE_TEST_MARK=<mark>`."""
    needle = f"LEERIE_TEST_MARK={mark}".encode()
    out = []
    for d in Path("/proc").iterdir():
        if not d.name.isdigit() or int(d.name) == os.getpid():
            continue
        try:
            env = (d / "environ").read_bytes()
        except OSError:
            continue
        if needle in env.split(b"\0"):
            out.append(int(d.name))
    return out


def reap(mark: str) -> None:
    for pid in marked_pids(mark):
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
