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
`nerdctl kill` ends a container.

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
case "$1" in
  run)
    { for a in "$@"; do printf '%s\n' "$a"; done; } > "$w/run.argv"
    cid="" prev=""
    for a in "$@"; do [ "$prev" = "--cidfile" ] && cid=$a; prev=$a; done
    if [ -n "${STUB_CREATED_SECONDS:-}" ]; then
      # nerdctl writes the cidfile at create, before the task starts; a
      # created container cannot be killed.
      [ -z "$cid" ] || printf 'stubcid\n' > "$cid"
      printf 'created\n' > "$w/container.state"
      "__SLEEP__" "$STUB_CREATED_SECONDS"
    fi
    printf 'running\n' > "$w/container.state"
    "__SLEEP__" "${STUB_RUN_SECONDS:-0}" &
    printf '%s\n' "$!" > "$w/container.pid"
    [ -z "$cid" ] || printf 'stubcid\n' > "$cid"
    wait "$!"
    exit $? ;;
  kill)
    if [ -f "$w/container.state" ] && [ "$(< "$w/container.state")" = created ]; then
      printf 'container not running\n' >&2; exit 1
    fi
    [ ! -f "$w/container.pid" ] || kill -KILL "$(< "$w/container.pid")" 2>/dev/null
    exit 0 ;;
  info|version) printf 'Version: 2.0.0\n'; exit 0 ;;
  *) exit 0 ;;
esac
"""


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
