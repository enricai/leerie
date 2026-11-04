"""A launch runs only the leerie code it started with (DESIGN §6 *A launch
runs only the leerie code it started with*).

On 2026-11-03 the operator installed 0.44.0 under five running 0.43.0 runs.
The launcher bind-mounted the install directory itself at
`/opt/leerie-image`, so from 22:41 every confined command in those
containers ran 0.44.0's `scripts/landlock_exec.py` under 0.43.0's AppArmor
profile, and refused ("cannot isolate the command, so it does not run:
tmpfs /dev: EACCES").

The harness runs the launcher's own text, extracted verbatim: the span that
takes the launch's snapshot of its code (before any image step), the span
from `$STAGE`'s keepalive through the staging set-up (where the snapshot
moves into `$STAGE`) and the real `_run_argv` array, then `nerdctl run`
with it. The fake
`nerdctl` is the "running container": it reads leerie's code through the
`/opt/leerie-image` mount it was given, after the test has changed the
install under it.
"""
from __future__ import annotations

import contextlib
import os
import signal
import subprocess
import time
from pathlib import Path

from tests.launcher_argv_extract import LAUNCHER, extract_run_argv

OLD_SHIM = "# the shim this run started with\n"
NEW_SHIM = "# the shim an upgrade installed\n"


def _snapshot_span() -> str:
    """The launcher's snapshot of its code: `_snapshot_leerie_code` and the
    directory it is taken into, before the command line is checked."""
    src = LAUNCHER.read_text()
    start = src.index("# --- the leerie code this launch runs ---")
    end = src.index("# --- refuse a command line before any image step")
    return src[start:end]


def _staging_span() -> str:
    """From `_stage_keepalive` to the start of the credential staging: the
    launcher's `$STAGE` set-up, in which the launch's code copy is taken."""
    src = LAUNCHER.read_text()
    start = src.index("_stage_keepalive() {")
    end = src.index("# -- .claude.json: copy with projects[] stripped. --")
    return src[start:end]


def _harness() -> str:
    # The fake `nerdctl` uses builtins only. It records the source of the
    # /opt/leerie-image mount, waits for the test to change the install, then
    # reads the shim through the mount, as a running container would.
    return (
        "set -euo pipefail\n"
        'HOME="$1"; LEERIE_REPO="$2"; LEERIE_VERSION="$3"; OUT="$4"\n'
        "export HOME\n"
        'mkdir -p "$HOME/.cache/leerie"\n'
        "RUNTIME=local\n"
        'remote_log() { printf \'LOG: %s\\n\' "$*" >&2; }\n'
        "_repo_build_done() { :; }\n"
        + _snapshot_span()
        + _staging_span()
        + """
USER_REPO="$HOME/repo"
IMAGE_TAG="leerie:$LEERIE_VERSION"
LEERIE_STATE_HOST_DIR="$HOME/state"
TTY_FLAGS="-i"
_cidfile="/dev/null"
REPO_IMAGE_REF=""
LEERIE_COMMIT=""
AUTH_MOUNTS=()
CACHE_MOUNTS=()
INSPECT_MOUNTS=()
CGROUP_MOUNT_ARG=()
SECOPT=()
APPARMOR_OPT=()
REWRITTEN_ARGS=()
_leerie_env_args=()
_leerie_host_dir="$HOME/.leerie/.host"
nerdctl() {
  local _prev="" _a _src=""
  for _a in "$@"; do
    if [ "$_prev" = "-v" ]; then
      case "$_a" in
        *:/opt/leerie-image:ro) _src="${_a%:/opt/leerie-image:ro}" ;;
      esac
    fi
    _prev="$_a"
  done
  printf '%s' "$_src" > "$OUT/mount-src"
  read -r _ < "$OUT/go"
  local _body=""
  IFS= read -r -d '' _body < "$_src/scripts/landlock_exec.py" || true
  printf '%s' "$_body" > "$OUT/seen"
}
"""
        + extract_run_argv()
        + '\nnerdctl --namespace leerie run "${_run_argv[@]}"\n'
    )


def _install(root: Path, version: str = "9.9.9") -> Path:
    inst = root / "install"
    for d in ("orchestrator", "scripts", "prompts", ".claude-plugin"):
        (inst / d).mkdir(parents=True)
    (inst / "orchestrator" / "leerie.py").write_text("# orchestrator\n")
    (inst / "scripts" / "landlock_exec.py").write_text(OLD_SHIM)
    (inst / "prompts" / "implementer.md").write_text("prompt\n")
    (inst / ".claude-plugin" / "plugin.json").write_text(
        '{\n  "name": "leerie",\n  "version": "%s"\n}\n' % version)
    return inst


def _start(tmp_path: Path, inst: Path, version: str
           ) -> tuple[subprocess.Popen, Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    os.mkfifo(out / "go")
    proc = subprocess.Popen(
        ["/bin/bash", "-c", _harness(), "--", str(home), str(inst), version,
         str(out)],
        env={"PATH": "/usr/bin:/bin",
             # The install is not a git checkout; no repository above it is.
             "GIT_CEILING_DIRECTORIES": str(tmp_path)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True, start_new_session=True)
    return proc, home, out


def _end(proc: subprocess.Popen) -> None:
    """A launch that did not end by itself: TERM its whole process group,
    so the launcher's EXIT trap ends its `$STAGE` keepalive and nothing it
    started outlives the test."""
    if proc.poll() is None:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(proc.pid, signal.SIGKILL)
            proc.communicate()


def _wait_for(path: Path, proc: subprocess.Popen) -> None:
    deadline = time.monotonic() + 30
    while not path.exists():
        if proc.poll() is not None:
            raise AssertionError(f"the launch ended first: {proc.communicate()}")
        if time.monotonic() > deadline:
            raise AssertionError("the container never started")
        time.sleep(0.05)


def test_an_upgrade_under_a_running_run_does_not_reach_it(tmp_path):
    inst = _install(tmp_path)
    proc, home, out = _start(tmp_path, inst, "9.9.9")
    try:
        _wait_for(out / "mount-src", proc)
        # The upgrade: the install's shim is replaced while the run runs.
        (inst / "scripts" / "landlock_exec.py").write_text(NEW_SHIM)
        with open(out / "go", "w") as go:
            go.write("go\n")
        _stdout, stderr = proc.communicate(timeout=30)
    finally:
        _end(proc)
    assert proc.returncode == 0, stderr
    src = (out / "mount-src").read_text()
    # The substance: the running run reads the shim it started with.
    assert (out / "seen").read_text() == OLD_SHIM
    # The container is given the launch's own copy, never the install.
    assert Path(src) != inst, src
    assert Path(src).is_relative_to(home / ".cache" / "leerie"), src
    # And the copy lives only as long as the launch.
    assert not Path(src).exists()


def test_an_install_that_changed_version_stops_the_launch(tmp_path):
    # The launcher started as 9.9.9 (its LEERIE_VERSION, which names the
    # image); the install it copies from is already 10.0.0.
    inst = _install(tmp_path, version="10.0.0")
    proc, _home, out = _start(tmp_path, inst, "9.9.9")
    try:
        _stdout, stderr = proc.communicate(timeout=30)
    finally:
        _end(proc)
    assert proc.returncode == 1
    assert not (out / "mount-src").exists(), "a container started"
    assert "9.9.9, now 10.0.0" in stderr, stderr


def _bring_back_fn() -> str:
    src = LAUNCHER.read_text()
    start = src.index("  _bring_back_ran_run() {")
    end = src.index("\n  }\n", start) + 5
    return src[start:end]


def test_the_bring_back_sources_the_launchs_copy(tmp_path):
    """After the container exits, the host's bring-back runs the launch's
    `host-finalize.sh`, not the install's: the two define the step
    differently here, and the output names which ran."""
    copy = tmp_path / "copy" / "scripts"
    copy.mkdir(parents=True)
    (copy / "host-finalize.sh").write_text(
        'host_bring_back_run_branch() { printf "copy\\n"; }\n')
    inst = tmp_path / "install" / "scripts"
    inst.mkdir(parents=True)
    (inst / "host-finalize.sh").write_text(
        'host_bring_back_run_branch() { printf "install\\n"; }\n')
    state = tmp_path / "state"
    (state / "runs" / "r1" / "repo").mkdir(parents=True)
    (state / "runs" / "r1" / "repo" / "HEAD").write_text("ref\n")
    script = (
        "set -euo pipefail\n"
        f'_code_dir="{tmp_path / "copy"}"\n'
        f'LEERIE_REPO="{tmp_path / "install"}"\n'
        f'LEERIE_STATE_HOST_DIR="{state}"\n'
        '_read_ran_run_id() { _ran_run_id=r1; }\n'
        + _bring_back_fn()
        + "_bring_back_ran_run\n")
    r = subprocess.run(["/bin/bash", "-c", script], capture_output=True,
                       text=True, env={"PATH": "/usr/bin:/bin"}, timeout=30)
    assert r.returncode == 0, r.stderr
    assert r.stdout == "copy\n"
