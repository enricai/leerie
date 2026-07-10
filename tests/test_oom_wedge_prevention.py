"""Coupling tests for the OOM-wedge prevention surface (DESIGN §6
*container boundary's hidden precondition*).

Background: two multi-worker runs sharing one 8 GB Colima VM exhausted
all memory; the kernel's global OOM-killer killed the host `nerdctl`
clients (every in-container process is oom_score_adj:-998), orphaning the
still-alive orchestrators, which kept holding the run-dir flock — so every
`--resume` exited EXIT_LOCKED=75. Four fixes close this:

  A — aggregate container memory cap (container-entry.sh sets
      leerie.slice/memory.max from VM MemTotal).
  D — INT/TERM/EXIT trap on the local run path kills the container via
      the cidfile before the launcher exits.
  E — stale-container reaper on --resume kills an orphaned Up container
      whose owning launcher (leerie.launcher_pid label) is dead.
  I — completion gate (tested in test_derive_run_status.py + below).

These are source-text assertions (no subprocess) so a refactor that
silently drops any arm fails immediately, plus one executable test of the
reaper's decision logic against a stubbed nerdctl.
"""
from __future__ import annotations

import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER_PATH = REPO_ROOT / "leerie"
ENTRY_PATH = REPO_ROOT / "scripts" / "container-entry.sh"


def _launcher_text() -> str:
    return LAUNCHER_PATH.read_text()


def _entry_text() -> str:
    return ENTRY_PATH.read_text()


# --- Fix A: aggregate memory cap in container-entry.sh --------------------

def test_entry_sets_slice_memory_max():
    """container-entry.sh writes an aggregate cap to leerie.slice/memory.max
    (via $LEERIE_SLICE, which resolves to the top-level /sys/fs/cgroup
    rootful/Fly, or the systemd-delegated user slice when rootless — see
    DESIGN §6 *Rootless exception*)."""
    text = _entry_text()
    assert '"$LEERIE_SLICE/memory.max"' in text, (
        "container-entry.sh must set memory.max on the leerie.slice cgroup "
        "(the parent of all per-worker cgroups) to bound aggregate memory."
    )


def test_entry_leerie_slice_rooted_at_delegated_subtree_when_rootless():
    """LEERIE_SLICE must resolve under the systemd-delegated user slice in
    rootless mode, and the plain top-level cgroupfs otherwise — the
    unprivileged rootlesskit-mapped identity has no write access to the
    real top-level /sys/fs/cgroup (DESIGN §6 *Rootless exception*)."""
    text = _entry_text()
    assert "user.slice/user-${HOST_UID}.slice/user@${HOST_UID}.service" in text, (
        "rootless mode must anchor CGROUP_ROOT at the systemd-delegated "
        "user slice, not the top-level /sys/fs/cgroup"
    )
    assert 'CGROUP_ROOT="/sys/fs/cgroup"' in text, (
        "the non-rootless default must remain the plain top-level cgroupfs"
    )


def test_entry_derives_cap_from_meminfo():
    """The cap is derived from VM MemTotal via /proc/meminfo (portable
    across Colima and native Linux)."""
    text = _entry_text()
    assert "/proc/meminfo" in text
    assert "MemTotal" in text


def test_entry_cap_is_overridable_and_optout():
    """LEERIE_CONTAINER_MEMORY_MAX_BYTES overrides; 0/max opts out."""
    text = _entry_text()
    assert "LEERIE_CONTAINER_MEMORY_MAX_BYTES" in text


# --- Fix E: launcher_pid label + reaper ----------------------------------

def test_launcher_sets_launcher_pid_label():
    """nerdctl run carries --label leerie.launcher_pid=$$ so the reaper can
    test owner liveness."""
    text = _launcher_text()
    assert '--label "leerie.launcher_pid=$$"' in text, (
        "nerdctl run must set the leerie.launcher_pid label; the reaper "
        "reads it back to decide whether an Up container is orphaned."
    )


def test_reaper_function_defined_and_wired():
    """_reap_orphaned_container is defined AND invoked on the local resume
    path before the spawn."""
    text = _launcher_text()
    assert "_reap_orphaned_container() {" in text, "reaper function missing"
    # Invoked (a call site distinct from the definition).
    call_sites = [
        ln for ln in text.splitlines()
        if "_reap_orphaned_container" in ln
        and "() {" not in ln
        and not ln.strip().startswith("#")
    ]
    assert call_sites, "reaper is defined but never invoked"
    # The call must be gated on IS_RESUME + local runtime.
    assert 'IS_RESUME" = "true" ] && [ "$RUNTIME" = "local"' in text


def test_reaper_uses_label_and_kill():
    """Reaper reads the launcher_pid label, tests liveness with kill -0,
    and reaps with nerdctl kill."""
    text = _launcher_text()
    start = text.index("_reap_orphaned_container() {")
    end = text.index("\n}", start)
    fn = text[start:end]
    assert "leerie.launcher_pid" in fn
    assert "kill -0" in fn
    assert "nerdctl kill" in fn
    # Must only act on a running container.
    assert "running" in fn


# --- Fix D: kill-on-exit trap --------------------------------------------

def test_kill_container_trap_present():
    """The local run path installs INT/TERM/EXIT traps that kill the
    container via the cidfile before the launcher exits."""
    text = _launcher_text()
    assert "_kill_container_from_cidfile" in text
    # All three arms present.
    assert "_kill_container_from_cidfile; exit 130' INT" in text
    assert "_kill_container_from_cidfile; exit 143' TERM" in text
    # EXIT arm kills BEFORE removing the cidfile (order matters).
    exit_trap = [
        ln for ln in text.splitlines()
        if "_kill_container_from_cidfile" in ln and "EXIT" in ln
    ]
    assert exit_trap, "no EXIT trap that kills the container"
    ln = exit_trap[0]
    assert ln.index("_kill_container_from_cidfile") < ln.index("rm -f"), (
        "EXIT trap must kill the container before removing the cidfile"
    )


# --- Executable: reaper decision logic (stubbed nerdctl) -----------------

_REAPER_HARNESS = r"""
set -u
remote_log() {{ echo "$@"; }}
STUB_STATUS="{status}"; STUB_PID="{pid}"; KILLED=""
nerdctl() {{
  case "$2" in
    "-f")
      case "$3" in
        '{{{{.State.Status}}}}') echo "$STUB_STATUS" ;;
        *launcher_pid*) echo "$STUB_PID" ;;
      esac ;;
    *) [ "$1" = "kill" ] && KILLED="yes" ;;
  esac
}}
{fn}
_reap_orphaned_container "fakeid"
echo "KILLED=${{KILLED}}"
"""


def _reaper_fn_source() -> str:
    text = _launcher_text()
    start = text.index("_reap_orphaned_container() {")
    end = text.index("\n}", start) + 2
    fn = text[start:end]
    # Neuter the real sleep so the test is fast.
    return fn.replace("\n  sleep 1", "\n  :")


def _run_reaper(status: str, pid: str) -> bool:
    """Return True if the reaper issued `nerdctl kill`."""
    script = _REAPER_HARNESS.format(status=status, pid=pid, fn=_reaper_fn_source())
    out = subprocess.run(
        ["bash", "-c", script], capture_output=True, text=True, timeout=30
    )
    return "KILLED=yes" in out.stdout


def test_reaper_kills_running_dead_owner():
    """Running container + dead owner PID → reap."""
    assert _run_reaper("running", "999999") is True


def test_reaper_spares_running_live_owner():
    """Running container + live owner (this test process) → do NOT reap;
    the orchestrator flock will correctly refuse a genuine duplicate."""
    import os
    assert _run_reaper("running", str(os.getpid())) is False


def test_reaper_spares_stopped_container():
    """Exited container → nothing to reap."""
    assert _run_reaper("exited", "999999") is False


def test_reaper_spares_unlabeled_container():
    """No launcher_pid label (older container) → do NOT reap (avoid
    killing a genuinely live run whose label predates this change)."""
    assert _run_reaper("running", "<no value>") is False
