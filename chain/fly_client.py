"""chain.fly_client — thin Fly Machines API client for leerie-chain.

Launches, queries, and destroys Fly Machines for per-run leerie workers.
Auth: FLY_API_TOKEN environment variable (required).
App:  FLY_APP_NAME environment variable (default: "leerie").

Uses stdlib urllib.request only — no third-party HTTP libraries.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_MACHINES_API = "https://api.machines.dev/v1"


class FlyClientError(Exception):
    """Raised when the Fly Machines API returns an error or token is missing."""


def _token() -> str:
    tok = os.environ.get("FLY_API_TOKEN", "").strip()
    if not tok:
        raise FlyClientError(
            "FLY_API_TOKEN is not set; set it via `fly secrets set FLY_API_TOKEN=<token>`"
        )
    return tok


def _app() -> str:
    return os.environ.get("FLY_APP_NAME", "leerie").strip()


def _request(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    url = f"{_MACHINES_API}{path}"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {_token()}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as exc:
        body_text = exc.read().decode(errors="replace")
        raise FlyClientError(
            f"Fly Machines API {method} {url} returned {exc.code}: {body_text}"
        ) from exc


def launch_machine(
    image: str,
    env: dict[str, str],
    region: str,
    vm_cpus: int = 4,
    vm_memory_mb: int = 8192,
) -> str:
    """Launch a Fly Machine and return its machine_id.

    Sends POST /v1/apps/{app}/machines with the leerie image, env vars, and
    region. Returns the machine id string from the response.
    """
    payload: dict[str, Any] = {
        "config": {
            "image": image,
            "env": env,
            "guest": {
                "cpus": vm_cpus,
                "memory_mb": vm_memory_mb,
            },
        },
        "region": region,
    }
    result = _request("POST", f"/apps/{_app()}/machines", body=payload)
    if not isinstance(result, dict) or "id" not in result:
        raise FlyClientError(
            f"launch_machine: unexpected response shape (no 'id' key): {result!r}"
        )
    return result["id"]


def get_machine_state(machine_id: str) -> str:
    """Return the current state of a Fly Machine (e.g. 'started', 'stopped').

    Sends GET /v1/apps/{app}/machines/{machine_id}.
    """
    result = _request("GET", f"/apps/{_app()}/machines/{machine_id}")
    if not isinstance(result, dict) or "state" not in result:
        raise FlyClientError(
            f"get_machine_state: unexpected response shape (no 'state' key): {result!r}"
        )
    return result["state"]


def destroy_machine(machine_id: str) -> None:
    """Destroy a Fly Machine (best-effort, force=true).

    Sends DELETE /v1/apps/{app}/machines/{machine_id}?force=true.
    Does not raise on 404 (machine already gone is success).
    """
    try:
        _request("DELETE", f"/apps/{_app()}/machines/{machine_id}?force=true")
    except FlyClientError as exc:
        if "404" in str(exc):
            return
        raise
