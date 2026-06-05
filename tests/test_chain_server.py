"""Tests for chain.server — the leerie-chain HTTP server.

The server is spun up in-process using a real HTTPServer on an ephemeral
port (port 0 — OS picks a free port).  fly_client.launch_machine and
git_ops.clone_target / create_stage_branch are stubbed so no live Fly API
calls or git network access is required.

Test coverage:
  POST /chains   — creates a chain row, stubs launch_machine for Wave A
  GET  /chains/<id>  — returns the snapshot
  GET  /chains/<missing-id>  — returns 404
  POST /webhooks/fly
      — Wave-A-final exit event triggers Wave B launch
      — Bad signature returns 400
      — Non-exit event is silently ignored (200 ok)
      — Wave A failure pauses chain (no Wave B launch)
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
import urllib.request
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from chain.config import Settings
from chain.server import make_server
from chain.state import ChainState

_FLY_EXIT_EVENT = "io.fly.machine.exited"
_SECRET = "test-webhook-secret"
_SETTINGS = Settings(
    gh_dispatch_pat="test-pat",
    fly_api_token="test-fly-token",
    chain_webhook_secret=_SECRET,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def cs() -> ChainState:
    return ChainState.init_db(":memory:")


@pytest.fixture()
def server_url(cs: ChainState, monkeypatch: pytest.MonkeyPatch):
    """Start the HTTP server on an ephemeral port in a background thread.

    Yields the base URL ``http://127.0.0.1:<port>``.
    """
    # Stub git_ops so no real git operations happen.
    monkeypatch.setattr(
        "chain.server.git_ops.clone_target",
        lambda repo_url, pat, clone_dir: _fake_clone(clone_dir),
    )
    monkeypatch.setattr(
        "chain.server.git_ops.create_stage_branch",
        lambda repo_path, chain_id, base_branch="main": f"stage-{chain_id}",
    )

    httpd = make_server(cs, _SETTINGS, host="127.0.0.1", port=0)
    port = httpd.server_address[1]
    thread = threading.Thread(target=httpd.handle_request, daemon=True)
    # We start a thread that handles ONE request at a time. Use serve_forever
    # instead for multi-request tests, but most test helpers dispatch one call.
    # Use serve_forever with a shorter poll so shutdown is fast.
    thread = threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fake_clone(clone_dir: str) -> "Path":
    from pathlib import Path
    p = Path(clone_dir)
    p.mkdir(parents=True, exist_ok=True)
    return p


def _sign(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"hmac-sha256={digest}"


def _post(url: str, body: Any, extra_headers: dict[str, str] | None = None) -> tuple[int, dict]:
    payload = json.dumps(body).encode()
    headers = {"Content-Type": "application/json", "Content-Length": str(len(payload))}
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, data=payload, method="POST", headers=headers)
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _get(url: str) -> tuple[int, dict]:
    req = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _post_webhook(base_url: str, payload: Any, secret: str = _SECRET) -> tuple[int, dict]:
    body = json.dumps(payload).encode()
    sig = _sign(secret, body)
    req = urllib.request.Request(
        f"{base_url}/webhooks/fly",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "fly-signature-256": sig,
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


# ---------------------------------------------------------------------------
# POST /chains
# ---------------------------------------------------------------------------

class TestPostChains:
    def test_creates_chain_returns_201(
        self, server_url: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        machine_ids = iter(["m-001", "m-002"])

        with patch("chain.server.fly_client.launch_machine", side_effect=lambda **kw: next(machine_ids)):
            status, body = _post(
                f"{server_url}/chains",
                {
                    "target": "https://github.com/org/repo",
                    "runs": [
                        {"prompt": "Task A1", "wave": "a"},
                        {"prompt": "Task A2", "wave": "a"},
                    ],
                },
            )

        assert status == 201
        assert "id" in body
        assert body["target"] == "https://github.com/org/repo"

    def test_launches_wave_a_machines(
        self, server_url: str, cs: ChainState
    ) -> None:
        launched: list[dict] = []

        def fake_launch(**kw: Any) -> str:
            launched.append(kw)
            return f"m-{len(launched):03d}"

        with patch("chain.server.fly_client.launch_machine", side_effect=fake_launch):
            status, body = _post(
                f"{server_url}/chains",
                {
                    "target": "https://github.com/org/repo",
                    "runs": [
                        {"prompt": "Wave A Task", "wave": "a"},
                        {"prompt": "Wave B Task", "wave": "b"},
                    ],
                },
            )

        assert status == 201
        # Only Wave A should have been launched.
        assert len(launched) == 1

    def test_does_not_launch_wave_b_machines_on_create(
        self, server_url: str, cs: ChainState
    ) -> None:
        launched_waves: list[str] = []

        def fake_launch(**kw: Any) -> str:
            # The env dict in the server call does not encode the wave; we
            # infer by checking how many calls happened vs wave composition.
            launched_waves.append("launched")
            return f"m-{len(launched_waves):03d}"

        with patch("chain.server.fly_client.launch_machine", side_effect=fake_launch):
            _, body = _post(
                f"{server_url}/chains",
                {
                    "target": "https://github.com/org/repo",
                    "runs": [
                        {"prompt": "A1", "wave": "a"},
                        {"prompt": "B1", "wave": "b"},
                        {"prompt": "B2", "wave": "b"},
                    ],
                },
            )

        # Only Wave A (1 run) launched.
        assert len(launched_waves) == 1

    def test_wave_a_runs_marked_running(
        self, server_url: str, cs: ChainState
    ) -> None:
        with patch("chain.server.fly_client.launch_machine", return_value="m-test"):
            _, body = _post(
                f"{server_url}/chains",
                {
                    "target": "https://github.com/org/repo",
                    "runs": [{"prompt": "A task", "wave": "a"}],
                },
            )

        chain_id = body["id"]
        snap = cs.load_chain(chain_id)
        assert snap is not None
        wave_a_run = next(r for r in snap["runs"] if r["wave"] == "a")
        assert wave_a_run["status"] == "running"
        assert wave_a_run["machine_id"] == "m-test"

    def test_missing_target_returns_400(self, server_url: str) -> None:
        status, body = _post(
            f"{server_url}/chains",
            {"runs": [{"prompt": "p", "wave": "a"}]},
        )
        assert status == 400
        assert "target" in body.get("error", "").lower()

    def test_missing_runs_returns_400(self, server_url: str) -> None:
        status, body = _post(
            f"{server_url}/chains",
            {"target": "https://github.com/org/repo"},
        )
        assert status == 400

    def test_invalid_wave_returns_400(self, server_url: str) -> None:
        status, body = _post(
            f"{server_url}/chains",
            {
                "target": "https://github.com/org/repo",
                "runs": [{"prompt": "p", "wave": "c"}],
            },
        )
        assert status == 400

    def test_empty_runs_returns_400(self, server_url: str) -> None:
        status, body = _post(
            f"{server_url}/chains",
            {
                "target": "https://github.com/org/repo",
                "runs": [],
            },
        )
        assert status == 400

    def test_malformed_body_does_not_create_chain(
        self, server_url: str, cs: ChainState
    ) -> None:
        """A 400 response must not persist a chain row in the DB."""
        status, _ = _post(
            f"{server_url}/chains",
            {"runs": [{"prompt": "p", "wave": "a"}]},  # missing target
        )
        assert status == 400
        assert cs.list_chains() == []

    def test_invalid_json_body_returns_400(self, server_url: str) -> None:
        """Non-JSON content in the request body returns 400."""
        payload = b"not-json"
        req = urllib.request.Request(
            f"{server_url}/chains",
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(payload)),
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400


# ---------------------------------------------------------------------------
# GET /chains/<id>
# ---------------------------------------------------------------------------

class TestGetChain:
    def test_returns_chain_snapshot(
        self, server_url: str, cs: ChainState
    ) -> None:
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[("Task A", "a"), ("Task B", "b")],
        )
        status, body = _get(f"{server_url}/chains/{chain_id}")
        assert status == 200
        assert body["id"] == chain_id
        assert body["target"] == "https://github.com/org/repo"
        assert len(body["runs"]) == 2

    def test_returns_404_for_missing_chain(self, server_url: str) -> None:
        status, body = _get(f"{server_url}/chains/nonexistent-id")
        assert status == 404

    def test_snapshot_includes_run_fields(
        self, server_url: str, cs: ChainState
    ) -> None:
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[("Task A", "a")],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_id = snap["runs"][0]["id"]
        cs.transition_run(run_id, "running", machine_id="fly-m-123")

        _, body = _get(f"{server_url}/chains/{chain_id}")
        run = body["runs"][0]
        assert run["status"] == "running"
        assert run["machine_id"] == "fly-m-123"


# ---------------------------------------------------------------------------
# POST /webhooks/fly
# ---------------------------------------------------------------------------

class TestWebhookFly:
    def test_unsigned_webhook_returns_400(self, server_url: str) -> None:
        payload = {
            "type": _FLY_EXIT_EVENT,
            "machine_id": "m-unknown",
            "exit_code": 0,
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{server_url}/webhooks/fly",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                "fly-signature-256": "hmac-sha256=deadbeef",  # wrong
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_valid_exit_event_marks_run_done(
        self, server_url: str, cs: ChainState
    ) -> None:
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[("Task A", "a")],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_id = snap["runs"][0]["id"]
        cs.transition_run(run_id, "running", machine_id="m-exit-test")

        payload = {
            "type": _FLY_EXIT_EVENT,
            "machine_id": "m-exit-test",
            "exit_code": 0,
        }
        status, body = _post_webhook(server_url, payload)
        assert status == 200
        assert body.get("ok") is True

        updated = cs.load_chain(chain_id)
        assert updated is not None
        assert updated["runs"][0]["status"] == "done"

    def test_wave_a_complete_launches_wave_b(
        self, server_url: str, cs: ChainState
    ) -> None:
        """When the last Wave A run exits 0, Wave B machines are launched."""
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[
                ("Wave A task", "a"),
                ("Wave B task 1", "b"),
                ("Wave B task 2", "b"),
            ],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_a = next(r for r in snap["runs"] if r["wave"] == "a")
        cs.transition_run(run_a["id"], "running", machine_id="m-wave-a")

        wave_b_launches: list[str] = []

        def fake_launch(**kw: Any) -> str:
            wave_b_launches.append("launched")
            return f"m-b-{len(wave_b_launches)}"

        with patch("chain.server.fly_client.launch_machine", side_effect=fake_launch):
            payload = {
                "type": _FLY_EXIT_EVENT,
                "machine_id": "m-wave-a",
                "exit_code": 0,
            }
            status, _ = _post_webhook(server_url, payload)

        assert status == 200
        # Both Wave B runs must have been launched.
        assert len(wave_b_launches) == 2

        updated = cs.load_chain(chain_id)
        assert updated is not None
        assert updated["wave_state"] == "wave_b"
        wave_b_runs = [r for r in updated["runs"] if r["wave"] == "b"]
        assert all(r["status"] == "running" for r in wave_b_runs)

    def test_wave_a_failure_pauses_chain(
        self, server_url: str, cs: ChainState
    ) -> None:
        """A non-zero Wave A exit pauses the chain — no Wave B launch."""
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[
                ("Wave A task", "a"),
                ("Wave B task", "b"),
            ],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_a = next(r for r in snap["runs"] if r["wave"] == "a")
        cs.transition_run(run_a["id"], "running", machine_id="m-fail")

        launched: list[str] = []
        with patch(
            "chain.server.fly_client.launch_machine",
            side_effect=lambda **kw: launched.append("b") or "m-b-1",
        ):
            payload = {
                "type": _FLY_EXIT_EVENT,
                "machine_id": "m-fail",
                "exit_code": 1,
            }
            status, _ = _post_webhook(server_url, payload)

        assert status == 200
        assert len(launched) == 0

        updated = cs.load_chain(chain_id)
        assert updated is not None
        assert updated["status"] == "paused"
        # wave_state remains wave_a — never advanced.
        assert updated["wave_state"] == "wave_a"

    def test_non_exit_event_ignored(
        self, server_url: str, cs: ChainState
    ) -> None:
        """Non-exit Fly events are accepted (200) but do not change state."""
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[("Task A", "a")],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_id = snap["runs"][0]["id"]
        cs.transition_run(run_id, "running", machine_id="m-started")

        payload = {
            "type": "io.fly.machine.started",
            "machine_id": "m-started",
        }
        status, body = _post_webhook(server_url, payload)
        assert status == 200

        updated = cs.load_chain(chain_id)
        assert updated is not None
        assert updated["runs"][0]["status"] == "running"

    def test_missing_sig_header_returns_400(self, server_url: str) -> None:
        payload = {
            "type": _FLY_EXIT_EVENT,
            "machine_id": "m-unknown",
            "exit_code": 0,
        }
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            f"{server_url}/webhooks/fly",
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Content-Length": str(len(body)),
                # no fly-signature-256 header
            },
        )
        try:
            with urllib.request.urlopen(req) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_wave_a_done_no_wave_b_marks_chain_done(
        self, server_url: str, cs: ChainState
    ) -> None:
        """When Wave A completes and there is no Wave B, the chain is marked done."""
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            run_prompts=[("Task A only", "a")],
        )
        snap = cs.load_chain(chain_id)
        assert snap is not None
        run_a = snap["runs"][0]
        cs.transition_run(run_a["id"], "running", machine_id="m-solo")

        payload = {
            "type": _FLY_EXIT_EVENT,
            "machine_id": "m-solo",
            "exit_code": 0,
        }
        status, _ = _post_webhook(server_url, payload)
        assert status == 200

        updated = cs.load_chain(chain_id)
        assert updated is not None
        assert updated["wave_state"] == "done"
        assert updated["status"] == "done"


# ---------------------------------------------------------------------------
# Unknown routes
# ---------------------------------------------------------------------------

class TestRouting:
    def test_unknown_get_returns_404(self, server_url: str) -> None:
        status, _ = _get(f"{server_url}/unknown/path")
        assert status == 404

    def test_unknown_post_returns_404(self, server_url: str) -> None:
        status, _ = _post(f"{server_url}/unknown/path", {})
        assert status == 404
