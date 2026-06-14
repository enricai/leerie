from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

_MACHINES_API = "https://api.machines.dev/v1"


class FlyClientError(Exception):
    pass


def _token() -> str:
    tok = os.environ.get("FLY_API_TOKEN", "").strip()
    if not tok:
        raise FlyClientError(
            "FLY_API_TOKEN is not set; set it via `fly secrets set FLY_API_TOKEN=<token>`"
        )
    return tok


def app_name() -> str:
    """Return the Fly app name from ``FLY_APP_NAME`` env (default ``'leerie'``).

    Public so callers outside this module (e.g. ``chain.coordinator``)
    can compose machine-internal DNS names without poking at module
    internals.
    """
    return os.environ.get("FLY_APP_NAME", "leerie").strip()


# Backwards-compat alias. Used by older call sites; do not introduce
# new uses. New code should call ``app_name`` directly.
_app = app_name


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
    metadata: dict[str, str] | None = None,
) -> str:
    """Create a Fly machine via the Machines API and return its id.

    Args:
        image: Container image tag (e.g. ``registry.fly.io/leerie:0.7.3``).
        env: Environment variables to inject into the machine.
        region: Fly region (e.g. ``"iad"``).
        vm_cpus: Guest CPU count.
        vm_memory_mb: Guest memory in MB.
        metadata: Optional ``config.metadata`` dict — string keys + string
            values queryable via ``GET /machines?metadata.<key>=<value>``.
            The coordinator tags worker machines with
            ``{"leerie_chain_id": ..., "leerie_role": "worker",
            "leerie_run_id": ...}`` so ``leerie --kill <chain-id>``
            discovers them alongside the coordinator.
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
    if metadata:
        payload["config"]["metadata"] = metadata
    result = _request("POST", f"/apps/{_app()}/machines", body=payload)
    if not isinstance(result, dict) or "id" not in result:
        raise FlyClientError(
            f"launch_machine: unexpected response shape (no 'id' key): {result!r}"
        )
    return result["id"]


def get_machine_state(machine_id: str) -> str:
    result = _request("GET", f"/apps/{_app()}/machines/{machine_id}")
    if not isinstance(result, dict) or "state" not in result:
        raise FlyClientError(
            f"get_machine_state: unexpected response shape (no 'state' key): {result!r}"
        )
    return result["state"]


def destroy_machine(machine_id: str) -> None:
    try:
        _request("DELETE", f"/apps/{_app()}/machines/{machine_id}?force=true")
    except FlyClientError as exc:
        if "404" in str(exc):
            return
        raise
