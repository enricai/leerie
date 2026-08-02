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


def _run_accept(state_path: Path, sid: str) -> subprocess.CompletedProcess:
    """Run the accept-blocked mutation via the launcher."""
    env = {k: v for k, v in os.environ.items()}
    env["LEERIE_STATE_DIR"] = str(state_path.parent.parent.parent)
    run_id = state_path.parent.name
    return subprocess.run(
        [str(REPO_ROOT / "leerie"), "accept-blocked", run_id, sid,
         "--runtime", "local"],
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
    )


def _make_state(tmp_path: Path, subtask_status: dict,
                blocked: dict | None = None) -> Path:
    run_dir = tmp_path / "runs" / "test-run-001"
    run_dir.mkdir(parents=True)
    state = {"subtask_status": subtask_status}
    if blocked is not None:
        state["blocked"] = blocked
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
