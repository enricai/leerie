"""Invariants for the launcher's `--resume --runtime fly` dispatch.

Two contracts pinned here:

  1. The dispatch resolves the resume target via the dual-file resolver
     `_resolve_fly_machine_id_from_run_dir` (fly-machine.json then
     run.json) — same as --stop/--kill/--finalize/--attach. Bootstrap-id
     runs paused before classify don't have run.json on the host;
     fly-machine.json is the source of truth.
  2. Explicit `--resume` is strict-fail. A miss (no machine pointer, or
     resume_machine returned non-zero) must abort, not fall through to
     provision_machine. Silent provision on resume creates a duplicate
     Fly machine and orphans the original (DESIGN §6 *Remote
     pause-on-failure* — the sidecar is the source of truth for what's
     recoverable).

The dispatch block lives mid-script in the launcher's `RUNTIME=fly`
branch, so a bash harness mirrors its shape (same pattern as
test_e1_bootstrap_resume_handover.py) and a coupling test pins the
launcher source so a refactor that drifts from the mirror fails here
instead of silently lapsing coverage.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE = REPO_ROOT / "leerie"
PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "provision.sh"
RESUME_SH = REPO_ROOT / "scripts" / "remote" / "resume-machine.sh"


# Mirror of the launcher's resume/provision dispatch: sources the same
# helpers the launcher does, defines _resolve_fly_machine_id_from_run_dir
# verbatim from leerie:76-94, and runs the dispatch block from leerie:1620-1660.
# Test inputs: LEERIE_RUN_ID, IS_RESUME, USER_REPO, and a stub flyctl on
# PATH that simulates machine state.
_HARNESS = f"""
set -uo pipefail

# Mirror of leerie:76-94 — the dual-file resolver.
_resolve_fly_machine_id_from_run_dir() {{
  local run_dir="$1"
  local candidate
  for candidate in "$run_dir/fly-machine.json" "$run_dir/run.json"; do
    if [ -f "$candidate" ]; then
      python3 -c '
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    mid = d.get("fly_machine_id")
    print(mid if mid else "")
except Exception:
    pass
' "$candidate"
      return 0
    fi
  done
  return 0
}}

# Stub remote_log so we don't depend on the launcher's logger.
remote_log() {{ echo "[leerie] $*" >&2; }}

# Source the real provision.sh + resume-machine.sh so resume_machine /
# provision_machine / decide_teardown match production.
. {PROVISION_SH}
. {RESUME_SH}

# Mirror of the dispatch block from leerie:~1620-1660.
LEERIE_RUN_ID="$1"
IS_RESUME="$2"
shift 2
container_rc=0

_paused_mid=""
if [ -n "$LEERIE_RUN_ID" ]; then
  _paused_mid="$(_resolve_fly_machine_id_from_run_dir \\
                   "$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID")"
fi

_provisioned=false
_resumed=false
if [ -n "$_paused_mid" ]; then
  if resume_machine "$_paused_mid"; then
    _provisioned=true
    _resumed=true
  elif [ "$IS_RESUME" = "true" ]; then
    container_rc=1
  fi
elif [ "$IS_RESUME" = "true" ]; then
  remote_log "--resume: no Fly machine pointer found for run-id $LEERIE_RUN_ID"
  echo "  Looked for: $LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/fly-machine.json" >&2
  echo "             $LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json (fly_machine_id field)" >&2
  echo "  Neither exists with a usable machine id." >&2
  echo "  Run 'leerie --list' to see known runs, or omit --resume to" >&2
  echo "  start a fresh run." >&2
  container_rc=1
elif provision_machine; then
  _provisioned=true
fi

echo "RESULT _provisioned=$_provisioned _resumed=$_resumed container_rc=$container_rc"
"""


def _make_flyctl_stub(tmp_path: Path, *, state: str = "started",
                      stop_ok: bool = True) -> Path:
    """Write a stub flyctl. machine start returns 0 when state is
    started/stopped; status returns `State: <state>`. machine run
    succeeds (used only to detect unwanted provisioning)."""
    log = tmp_path / "flyctl.log"
    fake = tmp_path / "flyctl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f"_state='{state}'\n"
        "case \"$1 $2\" in\n"
        "  'auth status') exit 0 ;;\n"
        # provision_machine parses `Machine ID: <id>` from this output.
        "  'machine run') printf 'Success! A Machine has been launched\\n Machine ID: mach-FRESH-001\\n State: created\\n'; exit 0 ;;\n"
        "  'machine status') printf 'Machine ID: %s\\nState: %s\\n' \"$3\" \"$_state\"; exit 0 ;;\n"
        # resume-machine.sh:44 calls `machine start <id> --app <app>`. Return 0 when state is start-able.
        "  'machine start')\n"
        "    case \"$_state\" in\n"
        "      started|starting|stopped) exit 0 ;;\n"
        "      destroyed) exit 1 ;;\n"
        "      *) exit 1 ;;\n"
        "    esac\n"
        "    ;;\n"
        f"  'machine stop') exit {0 if stop_ok else 1} ;;\n"
        "  'machine destroy') exit 0 ;;\n"
        "esac\n"
        "exit 0\n"
    )
    fake.chmod(0o755)
    return fake


def _make_run_dir_with_fly_machine_json(state_dir: Path, run_id: str,
                                         machine_id: str) -> Path:
    """Synthesize a host-side run dir in the bootstrap-pre-classify
    shape: only fly-machine.json exists (the launcher writes it at the
    LEERIE_STATE_HOST_DIR/runs path), no run.json (the orchestrator inside
    the machine hasn't synced state back yet). This is the shape the
    dispatch must tolerate."""
    run_dir = state_dir / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "fly-machine.json").write_text(
        '{"fly_app": "leerie", "fly_machine_id": "' + machine_id + '",'
        ' "started_at": "2026-06-02T19:29:49+00:00",'
        ' "run_id": "' + run_id + '", "launcher_pid": 50902,'
        ' "host_no_push": false}'
    )
    return run_dir


def _run_harness(tmp_path: Path, run_id: str, is_resume: str) -> subprocess.CompletedProcess:
    user_repo = tmp_path / "user-repo"
    state_dir = tmp_path / "leerie-state"
    return subprocess.run(
        ["bash", "-c", _HARNESS, "_", run_id, is_resume],
        env={
            "PATH": f"{tmp_path}:/usr/bin:/bin",
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "LEERIE_FLY_APP": "leerie",
            # Provision-side env that provision_machine reads — only
            # consulted on the provision path (Case A and Case C should
            # NOT hit it; Case B/D never reach it).
            "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
            "FLY_REGION": "iad",
            "FLY_VM_CPUS": "1",
            "FLY_VM_MEMORY": "1024",
        },
        capture_output=True, text=True,
    )


# --- Sidecar present, machine reachable -----------------------------------
# Pinned invariant: the resolver finds the id in fly-machine.json (run.json
# absent — the bootstrap shape) and resume_machine starts the existing
# machine. No `flyctl machine run` — that would be a duplicate provision.

def test_resume_finds_machine_id_in_fly_machine_json(tmp_path: Path):
    _make_flyctl_stub(tmp_path, state="started")
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    state_dir = tmp_path / "leerie-state"
    _make_run_dir_with_fly_machine_json(
        state_dir, "_bootstrap-abc123", "mach-LIVE-001"
    )
    r = _run_harness(tmp_path, "_bootstrap-abc123", "true")
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "_provisioned=true _resumed=true container_rc=0" in r.stdout, r.stdout
    log = (tmp_path / "flyctl.log").read_text()
    assert "machine start mach-LIVE-001" in log, log
    assert "machine run" not in log, (
        "Resume must NOT provision a fresh machine when the target is "
        "reachable.\nflyctl.log:\n" + log
    )


# --- No machine pointer for the run-id → loud failure ---------------------
# Pinned invariant: explicit --resume with no recoverable machine pointer
# in either sidecar must die with a diagnostic, not silently provision a
# duplicate. Diagnostic must name both sidecar paths so the user can see
# what was looked for, and point at `leerie --list` for next steps.

def test_resume_with_no_machine_pointer_fails_loudly(tmp_path: Path):
    _make_flyctl_stub(tmp_path, state="started")
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    r = _run_harness(tmp_path, "_bootstrap-missing", "true")
    # rc=0 here is the harness shell itself; the dispatch sets container_rc=1.
    assert r.returncode == 0, r.stderr
    assert "_provisioned=false _resumed=false container_rc=1" in r.stdout, r.stdout
    assert "no Fly machine pointer found" in r.stderr, r.stderr
    assert "fly-machine.json" in r.stderr, r.stderr
    assert "leerie --list" in r.stderr, r.stderr
    log_path = tmp_path / "flyctl.log"
    log = log_path.read_text() if log_path.exists() else ""
    assert "machine run" not in log, (
        "--resume on a missing pointer must NOT fall through to "
        "provision.\n" + log
    )


# --- Sidecar present, machine destroyed → non-zero, no provision ----------
# Pinned invariant: when resume_machine returns non-zero (target machine
# is destroyed / unrecoverable), container_rc must propagate as 1 with no
# fall-through to provision_machine. The status probe after the failed
# start (resume-machine.sh:47-53) is what classifies the machine as
# destroyed; this test pins that we honor that classification.

def test_resume_against_destroyed_machine_does_not_provision(tmp_path: Path):
    _make_flyctl_stub(tmp_path, state="destroyed")
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    state_dir = tmp_path / "leerie-state"
    _make_run_dir_with_fly_machine_json(
        state_dir, "_bootstrap-gone", "mach-DEAD-001"
    )
    r = _run_harness(tmp_path, "_bootstrap-gone", "true")
    assert r.returncode == 0, r.stderr
    assert "_provisioned=false _resumed=false container_rc=1" in r.stdout, r.stdout
    log = (tmp_path / "flyctl.log").read_text()
    assert "machine start mach-DEAD-001" in log, log
    assert "machine status mach-DEAD-001" in log, log
    assert "machine run" not in log, (
        "Failed resume must NOT fall through to provision.\n" + log
    )


# --- IS_RESUME=false, no machine pointer → provision ----------------------
# Pinned invariant: the strict-fail policy is gated on explicit --resume.
# A fresh run (`./leerie "task" --runtime fly`, no --resume) with no
# host-side machine pointer must still call provision_machine.

def test_fresh_run_still_provisions(tmp_path: Path):
    _make_flyctl_stub(tmp_path, state="started")
    user_repo = tmp_path / "user-repo"
    user_repo.mkdir()
    r = _run_harness(tmp_path, "_bootstrap-fresh1", "false")
    assert r.returncode == 0, r.stderr
    assert "_provisioned=true" in r.stdout, r.stdout
    log = (tmp_path / "flyctl.log").read_text()
    assert "machine run" in log, (
        "Fresh runs (IS_RESUME=false) must still provision.\n" + log
    )


# --- Coupling test: launcher source must match the harness above ----------

def test_launcher_resume_dispatch_pinned():
    """Pin distinctive substrings of the resume dispatch so a refactor
    that drifts from the harness mirror fails here instead of silently
    lapsing the coverage above."""
    launcher = LEERIE.read_text()
    # The dispatch resolves the resume target via the dual-file resolver
    # (fly-machine.json first, then run.json) — same as the other verbs.
    assert '_paused_mid="$(_resolve_fly_machine_id_from_run_dir' in launcher, (
        "Resume dispatch must use the dual-file resolver, not an inline "
        "single-file reader."
    )
    # The resolver must use LEERIE_STATE_HOST_DIR, not USER_REPO/.leerie.
    assert '$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID")"' in launcher, (
        "Resume dispatch resolver must reference LEERIE_STATE_HOST_DIR, "
        "not USER_REPO/.leerie (fly path must target the centralized state dir)."
    )
    # An inline reader that gates on paused_at would skip bootstrap runs
    # (which never write run.json on the host). Reject that shape.
    assert "if d.get('paused_at') and d.get('fly_machine_id'):" not in launcher, (
        "Resume dispatch must not gate on run.json.paused_at — bootstrap "
        "runs never write run.json on the host."
    )
    # Strict-fail diagnostic on --resume miss must name the missing pointer.
    assert "no Fly machine pointer found for run-id" in launcher, (
        "Resume dispatch must emit a diagnostic on missing pointer, not "
        "fall through to provision."
    )
    # Diagnostic paths must reference LEERIE_STATE_HOST_DIR, not USER_REPO/.leerie.
    assert "$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/fly-machine.json" in launcher, (
        "Resume dispatch diagnostic must report LEERIE_STATE_HOST_DIR-based path."
    )
    # IS_RESUME must be initialized and set from --resume in argv so the
    # strict-fail policy can gate on explicit user intent.
    assert "IS_RESUME=false" in launcher, "IS_RESUME variable not initialized"
    assert "--resume)   IS_RESUME=true" in launcher or \
           '--resume) IS_RESUME=true' in launcher or \
           '"--resume")' in launcher, (
        "IS_RESUME is not set from --resume argv"
    )
