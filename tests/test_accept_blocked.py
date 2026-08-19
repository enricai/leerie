"""Tests for the accept-blocked launcher verb.

The verb modifies state.json to mark a blocked/failed subtask as
complete so `resume` skips it. The local-path tests exercise the
Python mutation via the launcher directly. The Fly-path test stubs
``flyctl`` so the machine-wake + stdin-piped ``python3 -`` transport
runs against a local fixture without touching Fly.io — this is the
path that previously shipped broken (wrong wait_for_fly_ssh_ready
args, printf %q over argv-not-shell -C).
"""
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent


def _run_accept(state_path: Path, sid: str,
                force: bool = False) -> subprocess.CompletedProcess:
    """Run the accept-blocked mutation via the launcher."""
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_path.parent.parent.parent)
    run_id = state_path.parent.name
    argv = [str(REPO_ROOT / "leerie"), "accept-blocked", run_id, sid,
            "--runtime", "local"]
    if force:
        argv.append("--force")
    return subprocess.run(
        argv,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_state(tmp_path: Path, subtask_status: dict,
                blocked: dict | None = None,
                waves: list | None = None) -> Path:
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    state = {"subtask_status": subtask_status}
    if blocked is not None:
        state["blocked"] = blocked
    if waves is not None:
        state["waves"] = waves
    state_file = run_dir / "state.json"
    state_file.write_text(json.dumps(state, indent=2))
    return state_file


def test_accepts_blocked_subtask(tmp_path):
    sf = _make_state(tmp_path, {"s1": "blocked", "s2": "complete"},
                     blocked={"s1": "missing postgres"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"
    assert "s1" not in st.get("blocked", {})
    assert st["subtask_status"]["s2"] == "complete"


def test_accepts_failed_subtask(tmp_path):
    sf = _make_state(tmp_path, {"s1": "failed"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    assert json.loads(sf.read_text())["subtask_status"]["s1"] == "complete"


def test_noop_on_already_complete(tmp_path):
    sf = _make_state(tmp_path, {"s1": "complete"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0
    assert "already complete" in r.stdout


def test_errors_on_unknown_subtask(tmp_path):
    sf = _make_state(tmp_path, {"s1": "blocked"})
    r = _run_accept(sf, "nonexistent")
    assert r.returncode != 0
    assert "not found" in r.stderr


def test_errors_on_running_subtask(tmp_path):
    sf = _make_state(tmp_path, {"s1": "incomplete-handoff"})
    r = _run_accept(sf, "s1")
    assert r.returncode != 0
    assert "expected blocked or failed" in r.stderr


def test_accepts_when_registry_disagrees_with_status_string(tmp_path):
    # The N19+N22-composed shape: st['blocked'] carries the sid (the
    # authoritative record of blockage) but subtask_status[sid] is
    # 'incomplete-handoff', not 'blocked'/'failed'. Gate on registry
    # membership, not the status string.
    sf = _make_state(tmp_path, {"s1": "incomplete-handoff"},
                     blocked={"s1": "checkpoint pending"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"
    assert "s1" not in st.get("blocked", {})


def test_accepts_in_progress_subtask_when_in_registry(tmp_path):
    # Same N21/N22 shape as test_accepts_when_registry_disagrees_with_status_string
    # but with subtask_status='in_progress' -- the gate must key off blocked-dict
    # membership for ANY non-complete status string, not just incomplete-handoff.
    sf = _make_state(tmp_path, {"s1": "in_progress"},
                     blocked={"s1": "worker died mid-run"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"
    assert "s1" not in st.get("blocked", {})


def test_accepts_blocked_status_without_registry_entry(tmp_path):
    # Regression pin for the precedence the fix chose: subtask_status='blocked'
    # (or 'failed') alone -- with no corresponding entry in the blocked dict --
    # still accepts, since `cur in ("blocked", "failed")` is an independent OR
    # branch alongside registry membership, not a replacement for it.
    sf = _make_state(tmp_path, {"s1": "blocked"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"


def test_cleans_up_empty_blocked_dict(tmp_path):
    sf = _make_state(tmp_path, {"s1": "blocked"},
                     blocked={"s1": "reason"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert "blocked" not in st


def test_preserves_other_blocked_entries(tmp_path):
    sf = _make_state(tmp_path, {"s1": "blocked", "s2": "blocked"},
                     blocked={"s1": "reason1", "s2": "reason2"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"
    assert st["subtask_status"]["s2"] == "blocked"
    assert st["blocked"] == {"s2": "reason2"}


# --- Fly path (stubbed flyctl) ------------------------------------------


def _make_fake_flyctl(tmp_path: Path, host_state_file: Path,
                      expected_remote_state: str) -> Path:
    """Stub flyctl for the accept-blocked Fly path.

    Routes the calls the verb makes:
      - `auth status`       → exit 0 (require_flyctl passes)
      - `machine status`    → "State: stopped" until `machine start` touches
        a marker, then "State: started" (so wait_for_started returns).
      - `machine start`     → touch the marker, exit 0
      - `machine stop`      → remove the marker, exit 0
      - `ssh console -C "true"`  → exit 0 (wait_for_fly_ssh_ready probe)
      - `ssh console -C "python3 - '<remote-state>' '<sid>'"`  → let a REAL
        shell parse both `-C` positionals (via `eval set --`, exactly the
        semantics of the remote shell flyctl invokes), ASSERT arg1 equals
        the expected remote-state path (this is what guards the argv[1]
        quoting — the previous stub hardcoded it and missed regressions),
        then run the stdin-piped program locally against the host fixture
        with the parsed sid.
    """
    started_marker = tmp_path / ".machine-started"
    stub = tmp_path / "flyctl"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'STARTED_MARKER="{started_marker}"\n'
        f'HOST_STATE="{host_state_file}"\n'
        f'EXPECT_REMOTE="{expected_remote_state}"\n'
        'CMD=""\n'
        'SUB=""\n'
        'MSUB=""\n'
        'while [ $# -gt 0 ]; do\n'
        '  case "$1" in\n'
        '    -C) CMD="$2"; shift 2 ;;\n'
        '    auth) SUB="auth"; shift ;;\n'
        '    machine) SUB="machine"; shift; MSUB="${1:-}"; shift || true ;;\n'
        '    ssh) SUB="ssh"; shift ;;\n'
        '    *) shift ;;\n'
        '  esac\n'
        'done\n'
        'case "$SUB" in\n'
        '  auth) exit 0 ;;\n'
        '  machine)\n'
        '    case "$MSUB" in\n'
        '      status)\n'
        '        if [ -f "$STARTED_MARKER" ]; then echo "State: started"; \n'
        '        else echo "State: stopped"; fi\n'
        '        exit 0 ;;\n'
        '      start) : > "$STARTED_MARKER"; exit 0 ;;\n'
        '      stop) rm -f "$STARTED_MARKER"; exit 0 ;;\n'
        '      *) exit 0 ;;\n'
        '    esac ;;\n'
        '  ssh)\n'
        '    case "$CMD" in\n'
        '      true) exit 0 ;;\n'
        '      python3*-*)\n'
        '        SCRIPT="$(cat)"\n'
        # Parse the -C string the way the remote shell would. Strip the
        # leading `python3 - ` and let the shell word-split + de-quote the
        # rest into $1 (remote-state) and $2 (sid). This faithfully models
        # flyctl's remote-shell parsing, so a quoting regression in the
        # launcher would show up here as wrong/misparsed args.
        '        REST="${CMD#python3 - }"\n'
        '        eval "set -- $REST"\n'
        '        GOT_STATE="$1"\n'
        '        GOT_SID="$2"\n'
        '        if [ "$GOT_STATE" != "$EXPECT_REMOTE" ]; then\n'
        '          echo "ERROR: stub got remote-state [$GOT_STATE] expected [$EXPECT_REMOTE]" >&2\n'
        '          exit 3\n'
        '        fi\n'
        # Run the stdin program against the host fixture (host copy stands
        # in for the machine volume), using the sid the shell actually parsed.
        '        printf "%s" "$SCRIPT" | python3 - "$HOST_STATE" "$GOT_SID"\n'
        '        exit $? ;;\n'
        '      *) exit 0 ;;\n'
        '    esac ;;\n'
        'esac\n'
        'exit 0\n'
    )
    stub.chmod(0o755)
    return stub


def _run_accept_fly(tmp_path: Path, state_path: Path, sid: str,
                    flyctl: Path) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_path.parent.parent.parent)
    env["LEERIE_FLY_APP"] = "test-app"
    env["LEERIE_NONINTERACTIVE"] = "1"
    env["MACHINE_START_TIMEOUT"] = "10"
    env["PATH"] = f"{flyctl.parent}:{env.get('PATH', '')}"
    run_id = state_path.parent.name
    return subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", run_id, sid,
         "--runtime", "fly"],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
    )


def _expected_remote_state(state_path: Path) -> str:
    run_id = state_path.parent.name
    return f"/work/.leerie/runs/{run_id}/state.json"


def test_fly_path_accepts_blocked_subtask(tmp_path):
    """The Fly path wakes the machine, pipes the mutate program over
    stdin (the H1 fix), passes both `-C` positionals correctly (the stub
    asserts arg1 == the expected remote-state path), mutates state, and
    reports the ACCEPTED sentinel. Regression guard for the transport +
    argv-quoting bugs."""
    sf = _make_state(tmp_path, {"s1": "blocked"},
                     blocked={"s1": "needs postgres"})
    fake_flyctl = _make_fake_flyctl(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_fly(tmp_path, sf, "s1", fake_flyctl)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    # The stdin-piped python actually ran and mutated the fixture.
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["s1"] == "complete"
    assert "blocked" not in st
    # The ACCEPTED sentinel surfaced (proves the transport + grep worked).
    assert "ACCEPTED:" in (r.stdout + r.stderr)


def test_fly_path_reports_noop_on_already_complete(tmp_path):
    """An already-complete subtask yields the NOOP sentinel and exit 0."""
    sf = _make_state(tmp_path, {"s1": "complete"})
    fake_flyctl = _make_fake_flyctl(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_fly(tmp_path, sf, "s1", fake_flyctl)
    assert r.returncode == 0, f"stdout:\n{r.stdout}\nstderr:\n{r.stderr}"
    assert "NOOP:" in (r.stdout + r.stderr)


# --- Injection / traversal rejection (both runtimes) --------------------


def test_rejects_injection_sid_local(tmp_path):
    """A subtask-id with a shell metacharacter is refused before any
    mutation — guards against the -C command-injection vector."""
    sf = _make_state(tmp_path, {"s1": "blocked"})
    r = _run_accept(sf, "s1'; touch pwned; '")
    assert r.returncode != 0
    assert "invalid subtask-id" in (r.stdout + r.stderr)
    # No mutation happened; the fixture is untouched.
    assert json.loads(sf.read_text())["subtask_status"]["s1"] == "blocked"
    assert not (tmp_path / "pwned").exists()


def test_rejects_injection_sid_fly(tmp_path):
    """Same guard on the Fly path: the injection sid never reaches the
    machine (rejected before flyctl is invoked at all)."""
    sf = _make_state(tmp_path, {"s1": "blocked"})
    fake_flyctl = _make_fake_flyctl(tmp_path, sf, _expected_remote_state(sf))
    r = _run_accept_fly(tmp_path, sf, "s1'; touch pwned; '", fake_flyctl)
    assert r.returncode != 0
    assert "invalid subtask-id" in (r.stdout + r.stderr)
    assert json.loads(sf.read_text())["subtask_status"]["s1"] == "blocked"
    assert not (tmp_path / "pwned").exists()


def test_rejects_traversal_run_id(tmp_path):
    """A run-id with path-traversal characters is refused before the
    state-dir path is built — guards the arbitrary-state-write vector."""
    sf = _make_state(tmp_path, {"s1": "blocked"})
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(sf.parent.parent.parent)
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked",
         "../../../etc", "s1", "--runtime", "local"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "invalid run-id" in (r.stdout + r.stderr)


# ---------------------------------------------------------------------------
# The two fields can disagree by ABSENCE, not just by value (N21/N22).
#
# The pre-existing "registry disagrees" tests above both read that as *a
# different status string*. Measured across the run corpus, the single real
# disagreement was the other shape: a subtask in `blocked` with NO
# subtask_status entry at all — its checkpoint was rejected (the N22
# `## Files touched: None.` defect) before any status was written. The gate
# resolved the registry only AFTER a `cur is None` early-exit, so the one
# case that actually occurred was the one it refused.
# ---------------------------------------------------------------------------

def test_accepts_when_absent_from_subtask_status_but_in_registry(tmp_path):
    """The measured incident: `_self/d1207301`'s refactor-013."""
    sf = _make_state(
        tmp_path, {"other-1": "complete"},
        blocked={"refactor-013": "checkpoint invalid: `## Files touched` "
                                 "lists 'None.' but the file does not exist"})
    r = _run_accept(sf, "refactor-013")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["refactor-013"] == "complete"
    assert "refactor-013" not in st.get("blocked", {})


def test_still_refuses_a_sid_in_neither_field(tmp_path):
    """Anti-vacuity for the test above: the relaxation must not accept
    everything. A sid in neither field is a typo, not a blocked subtask."""
    sf = _make_state(tmp_path, {"s1": "blocked"}, blocked={"s1": "r"})
    r = _run_accept(sf, "nonexistent-9")
    assert r.returncode != 0
    assert "not found in subtask_status" in r.stderr


def test_force_settles_a_subtask_abandoned_mid_flight(tmp_path):
    """`in_progress` with NO registry entry — what a hard crash (ENOSPC,
    SIGKILL) leaves behind, and what neither field can express as blocked.
    Observed on `9751c048`'s test-015. Without --force it must stay refused,
    so the default gate keeps its meaning."""
    sf = _make_state(tmp_path, {"test-015": "in_progress"}, blocked={},
                     waves=[["test-015"]])
    refused = _run_accept(sf, "test-015")
    assert refused.returncode != 0
    assert "expected blocked or failed" in refused.stderr

    forced = _run_accept(sf, "test-015", force=True)
    assert forced.returncode == 0, forced.stderr
    st = json.loads(sf.read_text())
    assert st["subtask_status"]["test-015"] == "complete"


def test_force_still_rejects_a_sid_not_in_the_plan(tmp_path):
    """--force bypasses both status fields, so without this it would mint a
    bogus subtask_status entry from a typo."""
    sf = _make_state(tmp_path, {"test-015": "in_progress"}, blocked={},
                     waves=[["test-015"]])
    r = _run_accept(sf, "test-999", force=True)
    assert r.returncode != 0
    assert "not in the plan" in r.stderr
    st = json.loads(sf.read_text())
    assert "test-999" not in st["subtask_status"]


def test_errors_on_missing_run_dir(tmp_path):
    """No run dir at all -- the check that runs before _ab_state_file is
    even built. Distinct from test_errors_on_missing_state_file below,
    which has a run dir but no state.json inside it."""
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(tmp_path)
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", "never-existed", "s1",
         "--runtime", "local"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "no run dir" in r.stderr


def test_errors_on_missing_state_file(tmp_path):
    """Run dir exists but state.json does not -- the local-path guard
    right before the mutation is invoked."""
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(tmp_path)
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", "test-run-001", "s1",
         "--runtime", "local"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "no state.json" in r.stderr


def test_rejects_invalid_runtime_value(tmp_path):
    sf = _make_state(tmp_path, {"s1": "blocked"})
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(sf.parent.parent.parent)
    run_id = sf.parent.name
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", run_id, "s1",
         "--runtime", "bogus"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "must be 'local', 'fly', or 'ec2'" in (r.stdout + r.stderr)
    # No mutation happened.
    assert json.loads(sf.read_text())["subtask_status"]["s1"] == "blocked"


def test_fly_runtime_requires_app_env(tmp_path):
    """`--runtime fly` with no LEERIE_FLY_APP set fails before flyctl is
    ever invoked -- pin the guard rather than only its happy path."""
    sf = _make_state(tmp_path, {"s1": "blocked"})
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(sf.parent.parent.parent)
    env.pop("LEERIE_FLY_APP", None)
    run_id = sf.parent.name
    r = subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", run_id, "s1",
         "--runtime", "fly"],
        env=env, capture_output=True, text=True, timeout=30,
    )
    assert r.returncode != 0
    assert "LEERIE_FLY_APP is required" in (r.stdout + r.stderr)
    assert json.loads(sf.read_text())["subtask_status"]["s1"] == "blocked"


def test_records_waiver_metadata_on_accept(tmp_path):
    """The accepted_blocked record (docs/POSTMORTEM-2026-08-14.md, F16) must
    carry enough to reconstruct what was waived, not just that it was."""
    sf = _make_state(tmp_path, {"s1": "blocked"}, blocked={"s1": "needs postgres"})
    r = _run_accept(sf, "s1")
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    rec = st["accepted_blocked"]["s1"]
    assert rec["previous_status"] == "blocked"
    assert rec["blocker"] == "needs postgres"
    assert rec["forced"] is False
    assert rec["at"]


def test_records_forced_true_when_force_used(tmp_path):
    sf = _make_state(tmp_path, {"test-015": "in_progress"}, blocked={},
                     waves=[["test-015"]])
    r = _run_accept(sf, "test-015", force=True)
    assert r.returncode == 0, r.stderr
    st = json.loads(sf.read_text())
    rec = st["accepted_blocked"]["test-015"]
    assert rec["forced"] is True
    assert rec["blocker"] is None


def test_force_reaches_every_transport(tmp_path):
    """`--force` must be threaded through ALL of accept-blocked's transports.

    The verb has five invocation sites — local, the Fly and EC2 remote
    commands, and the two host-side state mirrors. Wiring only the local one
    leaves `--force` silently ignored on Fly/EC2: the flag parses, the verb
    reports success, and the mutation refuses on the far side. That is the
    same partial-fix shape `tests/test_resolve_aws_launcher.py` documents
    for `_resolve_ec2_knob`, where fixing one arm of a multi-arm dispatch
    read as done.

    Structural because the remote arms need a live machine to exercise: pin
    that no invocation of the mutation program passes the sid WITHOUT the
    force argument following it.
    """
    src = (REPO_ROOT / "leerie").read_text()
    invocations = [ln.strip() for ln in src.splitlines()
                   if '"$_ab_mutate" "$_ab_state_file" "$_ab_sid"' in ln
                   or "'$_ab_remote_state' '$_ab_sid'" in ln]
    assert invocations, "found no accept-blocked mutation invocations to check"
    unwired = [ln for ln in invocations if "_ab_force" not in ln]
    assert not unwired, (
        "these accept-blocked invocations do not forward --force, so the "
        "flag is silently ignored on that transport:\n  "
        + "\n  ".join(unwired))
