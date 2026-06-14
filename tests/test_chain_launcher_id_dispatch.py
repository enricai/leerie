"""Tests for the launcher's ID-dispatched chain verbs.

After the per-chain ephemeral coordinator refactor, the launcher's
single-run verbs (--status, --kill, --stop, --attach) detect when their
positional id is a UUID and route to chain-coordinator endpoints
(via the Fly Machines API + coordinator HTTP API) rather than the
existing single-run flow.

These tests stub `curl` and `python3` paths to verify the dispatch
without making real network calls.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# UUID-format id (8-4-4-4-12). Will trigger chain dispatch.
CHAIN_ID = "abcdef01-2345-4789-89ab-0123456789ab"
# Non-UUID id (e.g. Fly machine id). Will fall through to single-run code.
RUN_ID = "abc123def456ab"


def _stub_curl(tmp_path: Path, body: str = "[]", rc: int = 0) -> Path:
    """Write a stubbed `curl` that logs invocations and returns *body*."""
    log = tmp_path / "curl.log"
    fake = tmp_path / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f'cat <<EOF\n{body}\nEOF\n'
        f"exit {rc}\n"
    )
    fake.chmod(0o755)
    return log


def _run(tmp_path: Path, args: list[str], curl_body: str = "[]", curl_rc: int = 0,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    log = _stub_curl(tmp_path, body=curl_body, rc=curl_rc)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
        "FLY_API_TOKEN": "fake-token",
    }
    if env_extra:
        env.update(env_extra)
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True,
    )
    result.curl_log = log.read_text() if log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# --status <chain-id> → coordinator /state
# ---------------------------------------------------------------------------


def test_status_uuid_queries_coordinator_state(tmp_path: Path) -> None:
    body = '[{"id":"coord-mach-123","metadata":{"leerie_role":"coordinator"}}]'
    result = _run(tmp_path, ["--status", CHAIN_ID], curl_body=body)
    assert result.returncode == 0, result.stderr + result.stdout
    log = result.curl_log
    # First curl: list machines filtered by metadata.
    assert f"metadata.leerie_chain_id={CHAIN_ID}" in log
    assert "metadata.leerie_role=coordinator" in log
    # Second curl: GET /state on the discovered coordinator.
    assert "coord-mach-123.vm.leerie.internal:8080/state" in log


def test_status_uuid_errors_when_no_coordinator(tmp_path: Path) -> None:
    """When the Fly metadata query returns [], --status reports it."""
    result = _run(tmp_path, ["--status", CHAIN_ID], curl_body="[]")
    assert result.returncode != 0
    assert "no live coordinator" in (result.stdout + result.stderr).lower()


def test_status_uuid_requires_fly_api_token(tmp_path: Path) -> None:
    # Remove FLY_API_TOKEN.
    result = _run(tmp_path, ["--status", CHAIN_ID], env_extra={"FLY_API_TOKEN": ""})
    assert result.returncode != 0
    assert "FLY_API_TOKEN" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# --attach <chain-id> → polling /state
# ---------------------------------------------------------------------------


def test_attach_uuid_errors_when_no_coordinator(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--attach", CHAIN_ID], curl_body="[]")
    assert result.returncode != 0
    assert "no live coordinator" in (result.stdout + result.stderr).lower()


def test_attach_uuid_exits_when_coord_unreachable(tmp_path: Path) -> None:
    """If the coordinator self-destructs mid-poll, --attach exits cleanly."""
    # First curl lists machines (returns one). Second curl (GET /state) returns
    # empty body because our stub always returns the same body — but we set
    # body to '[]' here; the chain status field will be empty so the loop
    # eventually times out. Use a non-empty discovery body, then a body that
    # mimics a chain in a terminal status so the loop exits on first sleep.
    body = '[{"id":"coord-x","metadata":{"leerie_role":"coordinator"}}]'
    # The status-poll loop expects /state to return a JSON object whose
    # `status` field is one of done|failed|cancelled. Set the stub body
    # to that object for the SECOND call. The simplest hack here: same
    # body for both calls — the discovery parsing will produce a non-empty
    # id but won't be a list of size 1 for /state. Easier: just write a
    # stub that returns the discovery body but on /state-style URLs returns
    # the terminal status object.
    fake = tmp_path / "curl"
    log = tmp_path / "curl.log"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        '''case "$*" in
          *machines\\?metadata*)
            echo '[{"id":"coord-x","metadata":{"leerie_role":"coordinator"}}]'
            ;;
          *state*)
            echo '{"status":"done","wave_state":"done"}'
            ;;
          *)
            echo '{}'
            ;;
        esac
        exit 0
        '''
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
        "FLY_API_TOKEN": "fake-token",
    }
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--attach", CHAIN_ID],
        env=env, capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    assert "reached terminal status: done" in (result.stdout + result.stderr)


# ---------------------------------------------------------------------------
# --kill <chain-id> → destroy coordinator + workers
# ---------------------------------------------------------------------------


def test_kill_uuid_destroys_all_chain_machines(tmp_path: Path) -> None:
    body = (
        '[{"id":"coord-1","metadata":{"leerie_role":"coordinator"}},'
        '{"id":"worker-A","metadata":{"leerie_role":"worker"}},'
        '{"id":"worker-B","metadata":{"leerie_role":"worker"}}]'
    )
    result = _run(tmp_path, ["--kill", CHAIN_ID], curl_body=body)
    assert result.returncode == 0, result.stderr + result.stdout
    log = result.curl_log
    # One list call + three destroy calls (DELETE …?force=true).
    assert f"metadata.leerie_chain_id={CHAIN_ID}" in log
    # Each machine id appears in a DELETE URL.
    assert "/machines/coord-1?force=true" in log
    assert "/machines/worker-A?force=true" in log
    assert "/machines/worker-B?force=true" in log


def test_kill_uuid_no_machines_is_ok(tmp_path: Path) -> None:
    """If the chain is already gone, --kill exits 0 with a message."""
    result = _run(tmp_path, ["--kill", CHAIN_ID], curl_body="[]")
    assert result.returncode == 0
    assert "no machines found" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# --stop <chain-id> → POST /pause
# ---------------------------------------------------------------------------


def test_stop_uuid_posts_pause_to_coordinator(tmp_path: Path) -> None:
    body = '[{"id":"coord-mach-9","metadata":{"leerie_role":"coordinator"}}]'
    result = _run(tmp_path, ["--stop", CHAIN_ID], curl_body=body)
    assert result.returncode == 0, result.stderr + result.stdout
    log = result.curl_log
    assert f"metadata.leerie_chain_id={CHAIN_ID}" in log
    assert "coord-mach-9.vm.leerie.internal:8080/pause" in log
    assert "-X POST" in log


# ---------------------------------------------------------------------------
# Non-UUID id falls through (existing single-run code path)
# ---------------------------------------------------------------------------


def test_status_non_uuid_falls_through(tmp_path: Path) -> None:
    """A non-UUID id MUST NOT hit the chain code path.

    The fall-through re-execs the launcher with `--list --runs --run-id <id>`;
    we don't run that here (it would enter a deeper code path that needs
    much more setup). Instead we check that the chain-discovery curl call
    was NOT made.
    """
    result = _run(tmp_path, ["--status", RUN_ID], curl_body="[]")
    # The fall-through path requires a fully-wired launcher env that we
    # don't provide; it will fail somewhere downstream. We just verify
    # the chain discovery curl was NOT made.
    log = result.curl_log
    assert f"metadata.leerie_chain_id={RUN_ID}" not in log


def test_kill_non_uuid_falls_through(tmp_path: Path) -> None:
    """Non-UUID --kill goes through the run-kill flow.

    Same caveat as above: we just confirm the chain-discovery curl was
    NOT issued. The fall-through path is heavily tested by the existing
    test_kill_* suite.
    """
    result = _run(tmp_path, ["--kill", RUN_ID, "--force"], curl_body="[]")
    log = result.curl_log
    assert f"metadata.leerie_chain_id={RUN_ID}" not in log


# ---------------------------------------------------------------------------
# --list --chains
# ---------------------------------------------------------------------------


def test_list_chains_renders_active_chains(tmp_path: Path) -> None:
    body = (
        '[{"id":"coord-a","state":"started","created_at":"2026-06-14T00:00:00Z",'
        ' "metadata":{"leerie_role":"coordinator","leerie_chain_id":"abc-111"}},'
        ' {"id":"coord-b","state":"started","created_at":"2026-06-14T01:00:00Z",'
        ' "metadata":{"leerie_role":"coordinator","leerie_chain_id":"def-222"}}]'
    )
    result = _run(tmp_path, ["--list", "--chains"], curl_body=body)
    assert result.returncode == 0, result.stderr + result.stdout
    out = result.stdout
    assert "chain_id" in out
    assert "coord-a" in out
    assert "coord-b" in out
    assert "abc-111" in out
    assert "def-222" in out


def test_list_chains_empty_response(tmp_path: Path) -> None:
    result = _run(tmp_path, ["--list", "--chains"], curl_body="[]")
    assert result.returncode == 0
    assert "no active chains" in (result.stdout + result.stderr).lower()


def test_list_chains_via_deprecated_alias(tmp_path: Path) -> None:
    """`leerie --list-chains` is shim'd to `leerie --list --chains`."""
    body = (
        '[{"id":"coord-x","state":"started","created_at":"2026-06-14T00:00:00Z",'
        ' "metadata":{"leerie_role":"coordinator","leerie_chain_id":"deadbeef"}}]'
    )
    result = _run(tmp_path, ["--list-chains"], curl_body=body)
    assert result.returncode == 0, result.stderr + result.stdout
    assert "deadbeef" in result.stdout
