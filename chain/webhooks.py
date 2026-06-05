"""chain.webhooks — Fly webhook signature verification and event parsing.

Fly delivers machine-exit events to ``POST /webhooks/fly`` signed with a
shared HMAC-SHA256 secret.  This module provides three public callables:

``verify_signature``
    Check that the ``fly-signature-256`` header matches the HMAC-SHA256 of the
    raw request body.  Returns ``True`` on match, ``False`` otherwise.

``parse_machine_event``
    Extract ``(machine_id, exit_code, event_type)`` from an
    ``io.fly.machine.exited`` payload dict.  Tolerates the known Fly
    field-name variants for machine identity (``machine_id``, ``id``,
    ``instance_id``) so that minor API changes do not break the handler.
    Returns ``None`` for non-exit event types so callers can skip silently.

``handle_machine_exit``
    Verify signature, parse the event, find the matching
    ``chain_runs`` row by ``machine_id``, and transition the run to
    ``done`` (exit code 0) or ``failed`` (non-zero).  Raises
    ``WebhookError`` on signature mismatch or if the run is not found.

Design notes
------------
- stdlib-only (``hashlib``, ``hmac``): no third-party imports.
- Constant-time comparison via ``hmac.compare_digest`` prevents timing attacks.
- The header value format is ``hmac-sha256=<lowercase-hex>``, matching the
  convention used by Fly.io and mirroring GitHub/Stripe webhook conventions.
  The prefix is stripped before comparing digests.
- Defensive field lookup order: ``machine_id`` → ``id`` → ``instance_id``.
  The exit code is taken from ``exit_code``, falling back to ``exit_status``
  for API variants that use the alternate name.
"""
from __future__ import annotations

import hashlib
import hmac
from typing import Any

from chain.state import ChainState


_SIG_PREFIX = "hmac-sha256="
_FLY_EXIT_EVENT = "io.fly.machine.exited"


class WebhookError(Exception):
    """Raised when a webhook cannot be processed (bad sig, unknown run, etc.)."""


def verify_signature(secret: str, body: bytes, sig_header: str) -> bool:
    """Return True iff *sig_header* is a valid HMAC-SHA256 signature of *body*.

    Args:
        secret: The signing secret (plain text; will be UTF-8 encoded).
        body: Raw request body bytes — must be the exact bytes received.
        sig_header: Value of the ``fly-signature-256`` HTTP header.

    Returns:
        ``True`` if the signature matches, ``False`` otherwise.

    The expected header format is ``hmac-sha256=<lowercase-hex-digest>``.
    If the header is absent, malformed, or uses a different prefix, the
    function returns ``False`` rather than raising.
    """
    if not sig_header or not sig_header.startswith(_SIG_PREFIX):
        return False
    provided_hex = sig_header[len(_SIG_PREFIX):]
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    # compare_digest is constant-time and prevents timing side-channels.
    return hmac.compare_digest(expected, provided_hex)


def parse_machine_event(
    payload: dict[str, Any],
) -> tuple[str, int, str] | None:
    """Extract (machine_id, exit_code, event_type) from a Fly machine event.

    Returns ``None`` if the event type is not ``io.fly.machine.exited``
    (callers should silently ignore non-exit events).

    Args:
        payload: Parsed JSON payload dict from the Fly webhook body.

    Returns:
        ``(machine_id, exit_code, event_type)`` for exit events, or
        ``None`` for all other event types.

    Raises:
        WebhookError: If this is an exit event but required fields are absent.

    Field-name variants tolerated
    ------------------------------
    Machine identity
        ``machine_id``, ``id``, ``instance_id`` — tried in that order.
    Exit code
        ``exit_code``, ``exit_status`` — tried in that order.
    """
    event_type: str = payload.get("type", payload.get("event_type", ""))
    if event_type != _FLY_EXIT_EVENT:
        return None

    # Extract machine id — tolerate known variant field names.
    machine_id: str | None = (
        payload.get("machine_id")
        or payload.get("id")
        or payload.get("instance_id")
        or None
    )
    if not machine_id:
        raise WebhookError(
            f"exit event missing machine identity field "
            f"(tried machine_id, id, instance_id); payload keys: {list(payload)}"
        )

    # Extract exit code — tolerate exit_status as an alternate name.
    raw_code = payload.get("exit_code")
    if raw_code is None:
        raw_code = payload.get("exit_status")
    if raw_code is None:
        raise WebhookError(
            f"exit event missing exit code field "
            f"(tried exit_code, exit_status); payload keys: {list(payload)}"
        )
    try:
        exit_code = int(raw_code)
    except (TypeError, ValueError) as exc:
        raise WebhookError(
            f"exit code {raw_code!r} is not an integer"
        ) from exc

    return machine_id, exit_code, event_type


def handle_machine_exit(
    cs: ChainState,
    payload: dict[str, Any],
    secret: str,
    raw_body: bytes,
    sig_header: str,
) -> None:
    """Verify a Fly webhook and advance chain state for a completed run.

    On signature failure the function raises ``WebhookError`` immediately
    without touching the database.

    On a valid ``io.fly.machine.exited`` event:
    - exit code 0 → run transitions to ``'done'``
    - exit code non-zero → run transitions to ``'failed'``

    Non-exit event types are silently ignored (not an error).

    Args:
        cs: An open ``ChainState`` instance.
        payload: Parsed JSON payload dict.
        secret: Signing secret (same value as ``CHAIN_WEBHOOK_SECRET``).
        raw_body: Raw request body bytes (used for signature verification).
        sig_header: Value of the ``fly-signature-256`` HTTP header.

    Raises:
        WebhookError: On invalid signature or if no run matches the machine_id.
    """
    if not verify_signature(secret, raw_body, sig_header):
        raise WebhookError("webhook signature verification failed")

    result = parse_machine_event(payload)
    if result is None:
        return

    machine_id, exit_code, _event_type = result

    run_row = cs._conn.execute(
        "SELECT id FROM chain_runs WHERE machine_id = ?",
        (machine_id,),
    ).fetchone()
    if run_row is None:
        raise WebhookError(
            f"no chain_run found for machine_id {machine_id!r}"
        )
    run_id: str = run_row["id"]

    new_status = "done" if exit_code == 0 else "failed"
    cs.transition_run(run_id, new_status)
