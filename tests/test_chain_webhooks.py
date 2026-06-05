"""Tests for chain.webhooks — Fly webhook signature verification and event parsing.

All tests use in-memory SQLite (via ChainState.init_db(":memory:")) and
synthesised HMAC payloads so no network access or live Fly is required.
"""
from __future__ import annotations

import hashlib
import hmac
import json

import pytest

from chain.state import ChainState
from chain.webhooks import (
    WebhookError,
    handle_machine_exit,
    parse_machine_event,
    verify_signature,
)

_SECRET = "test-signing-secret"
_FLY_EXIT_EVENT = "io.fly.machine.exited"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sign(secret: str, body: bytes) -> str:
    """Return a correctly-formed fly-signature-256 header value."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"hmac-sha256={digest}"


def _make_exit_payload(
    machine_id_key: str = "machine_id",
    machine_id_val: str = "fly-machine-abc123",
    exit_code_key: str = "exit_code",
    exit_code_val: int = 0,
    event_type: str = _FLY_EXIT_EVENT,
) -> dict:
    return {
        "type": event_type,
        machine_id_key: machine_id_val,
        exit_code_key: exit_code_val,
    }


def _db_with_running_run() -> tuple[ChainState, str, str]:
    """Return (ChainState, chain_id, run_id) with one running run."""
    cs = ChainState.init_db(":memory:")
    chain_id = cs.create_chain("https://github.com/test/repo", [("Task A", "a")])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_id = snap["runs"][0]["id"]
    cs.transition_run(run_id, "running", machine_id="fly-machine-abc123")
    return cs, chain_id, run_id


# ---------------------------------------------------------------------------
# verify_signature
# ---------------------------------------------------------------------------

def test_verify_signature_valid() -> None:
    body = b'{"type": "io.fly.machine.exited"}'
    sig = _sign(_SECRET, body)
    assert verify_signature(_SECRET, body, sig) is True


def test_verify_signature_tampered_body() -> None:
    body = b'{"type": "io.fly.machine.exited"}'
    sig = _sign(_SECRET, body)
    tampered = b'{"type": "io.fly.machine.exited", "injected": true}'
    assert verify_signature(_SECRET, tampered, sig) is False


def test_verify_signature_wrong_secret() -> None:
    body = b'{"type": "io.fly.machine.exited"}'
    sig = _sign("different-secret", body)
    assert verify_signature(_SECRET, body, sig) is False


def test_verify_signature_empty_header() -> None:
    body = b'{"type": "io.fly.machine.exited"}'
    assert verify_signature(_SECRET, body, "") is False


def test_verify_signature_malformed_header_no_prefix() -> None:
    body = b'{"type": "io.fly.machine.exited"}'
    sig = "sha256=abc123"  # wrong prefix
    assert verify_signature(_SECRET, body, sig) is False


def test_verify_signature_empty_body_valid() -> None:
    body = b""
    sig = _sign(_SECRET, body)
    assert verify_signature(_SECRET, body, sig) is True


# ---------------------------------------------------------------------------
# parse_machine_event — standard payload
# ---------------------------------------------------------------------------

def test_parse_machine_event_standard_payload() -> None:
    payload = _make_exit_payload()
    result = parse_machine_event(payload)
    assert result is not None
    machine_id, exit_code, event_type = result
    assert machine_id == "fly-machine-abc123"
    assert exit_code == 0
    assert event_type == _FLY_EXIT_EVENT


def test_parse_machine_event_nonzero_exit() -> None:
    payload = _make_exit_payload(exit_code_val=1)
    result = parse_machine_event(payload)
    assert result is not None
    _, exit_code, _ = result
    assert exit_code == 1


# ---------------------------------------------------------------------------
# parse_machine_event — field-name variants
# ---------------------------------------------------------------------------

def test_parse_machine_event_id_variant() -> None:
    """Tolerate 'id' as the machine identity field."""
    payload = _make_exit_payload(machine_id_key="id")
    result = parse_machine_event(payload)
    assert result is not None
    machine_id, _, _ = result
    assert machine_id == "fly-machine-abc123"


def test_parse_machine_event_instance_id_variant() -> None:
    """Tolerate 'instance_id' as the machine identity field."""
    payload = _make_exit_payload(machine_id_key="instance_id")
    result = parse_machine_event(payload)
    assert result is not None
    machine_id, _, _ = result
    assert machine_id == "fly-machine-abc123"


def test_parse_machine_event_exit_status_variant() -> None:
    """Tolerate 'exit_status' as the exit code field."""
    payload = _make_exit_payload(exit_code_key="exit_status", exit_code_val=2)
    result = parse_machine_event(payload)
    assert result is not None
    _, exit_code, _ = result
    assert exit_code == 2


# ---------------------------------------------------------------------------
# parse_machine_event — non-exit event types
# ---------------------------------------------------------------------------

def test_parse_machine_event_non_exit_returns_none() -> None:
    payload = {"type": "io.fly.machine.started", "machine_id": "m-001"}
    result = parse_machine_event(payload)
    assert result is None


def test_parse_machine_event_unknown_type_returns_none() -> None:
    payload = {"type": "io.fly.something.else", "machine_id": "m-001"}
    result = parse_machine_event(payload)
    assert result is None


def test_parse_machine_event_missing_type_returns_none() -> None:
    payload = {"machine_id": "m-001", "exit_code": 0}
    result = parse_machine_event(payload)
    assert result is None


# ---------------------------------------------------------------------------
# parse_machine_event — error cases on exit events
# ---------------------------------------------------------------------------

def test_parse_machine_event_missing_machine_id_raises() -> None:
    payload = {"type": _FLY_EXIT_EVENT, "exit_code": 0}
    with pytest.raises(WebhookError, match="missing machine identity"):
        parse_machine_event(payload)


def test_parse_machine_event_missing_exit_code_raises() -> None:
    payload = {"type": _FLY_EXIT_EVENT, "machine_id": "m-001"}
    with pytest.raises(WebhookError, match="missing exit code"):
        parse_machine_event(payload)


# ---------------------------------------------------------------------------
# handle_machine_exit — end-to-end with ChainState
# ---------------------------------------------------------------------------

def test_handle_machine_exit_success_marks_done() -> None:
    """Exit code 0 transitions the matching run to 'done'."""
    cs, chain_id, run_id = _db_with_running_run()
    payload = _make_exit_payload(exit_code_val=0)
    body = json.dumps(payload).encode()
    sig = _sign(_SECRET, body)

    handle_machine_exit(cs, payload, _SECRET, body, sig)

    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "done"
    cs.close()


def test_handle_machine_exit_failure_marks_failed() -> None:
    """Non-zero exit code transitions the matching run to 'failed'."""
    cs, chain_id, run_id = _db_with_running_run()
    payload = _make_exit_payload(exit_code_val=1)
    body = json.dumps(payload).encode()
    sig = _sign(_SECRET, body)

    handle_machine_exit(cs, payload, _SECRET, body, sig)

    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "failed"
    cs.close()


def test_handle_machine_exit_bad_signature_raises() -> None:
    cs, _, _ = _db_with_running_run()
    payload = _make_exit_payload()
    body = json.dumps(payload).encode()
    bad_sig = "hmac-sha256=deadbeef"

    with pytest.raises(WebhookError, match="signature verification failed"):
        handle_machine_exit(cs, payload, _SECRET, body, bad_sig)
    cs.close()


def test_handle_machine_exit_unknown_machine_raises() -> None:
    """A machine_id with no matching run raises WebhookError."""
    cs, _, _ = _db_with_running_run()
    payload = _make_exit_payload(machine_id_val="fly-machine-unknown")
    body = json.dumps(payload).encode()
    sig = _sign(_SECRET, body)

    with pytest.raises(WebhookError, match="no chain_run found"):
        handle_machine_exit(cs, payload, _SECRET, body, sig)
    cs.close()


def test_handle_machine_exit_non_exit_event_ignored() -> None:
    """Non-exit events are silently ignored (no DB change)."""
    cs, chain_id, run_id = _db_with_running_run()
    payload = {"type": "io.fly.machine.started", "machine_id": "fly-machine-abc123"}
    body = json.dumps(payload).encode()
    sig = _sign(_SECRET, body)

    handle_machine_exit(cs, payload, _SECRET, body, sig)

    snap = cs.load_chain(chain_id)
    assert snap is not None
    # Run should still be 'running' — event was ignored
    assert snap["runs"][0]["status"] == "running"
    cs.close()


def test_handle_machine_exit_instance_id_variant() -> None:
    """Handler works when payload uses 'instance_id' instead of 'machine_id'."""
    cs, chain_id, run_id = _db_with_running_run()
    # The run was set up with machine_id="fly-machine-abc123"; use instance_id variant
    payload = {
        "type": _FLY_EXIT_EVENT,
        "instance_id": "fly-machine-abc123",
        "exit_code": 0,
    }
    body = json.dumps(payload).encode()
    sig = _sign(_SECRET, body)

    handle_machine_exit(cs, payload, _SECRET, body, sig)

    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "done"
    cs.close()


# ---------------------------------------------------------------------------
# Malformed body — verify_signature must not crash on non-JSON bytes
# ---------------------------------------------------------------------------

def test_verify_signature_malformed_body_correct_sig() -> None:
    """verify_signature returns True for any correctly-signed bytes, including non-JSON."""
    body = b"not json at all {{"
    sig = _sign(_SECRET, body)
    assert verify_signature(_SECRET, body, sig) is True


def test_verify_signature_malformed_body_bad_sig() -> None:
    """verify_signature returns False (not raises) when signature does not match non-JSON body."""
    body = b"not json at all {{"
    bad_sig = "hmac-sha256=deadbeef"
    assert verify_signature(_SECRET, body, bad_sig) is False
