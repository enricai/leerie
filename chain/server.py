"""chain.server — stdlib HTTP server for the leerie-chain orchestrator app.

Three endpoints:

  POST /chains
      Create a new chain. Request body (JSON):
        {
          "target": "<repo https url>",
          "runs": [
            {"prompt": "<task text>", "wave": "a" | "b"},
            ...
          ]
        }
      Clones the target repo, creates the stage-<chain_id> branch, launches
      all Wave A machines immediately via the Fly Machines API, and persists
      the chain in SQLite. Returns 201 with the full chain snapshot.

  GET /chains/<id>
      Return the chain snapshot for *id* as JSON, or 404 if not found.

  POST /webhooks/fly
      Receive Fly machine-exit events. Verifies the HMAC-SHA256 signature
      in the ``fly-signature-256`` header; rejects with 400 on mismatch.
      Dispatches to ``handle_machine_exit`` from chain.webhooks; if all
      Wave A runs are now done, advances the chain to ``wave_b`` and
      launches the Wave B machines.

The handler is intentionally stateless across requests — all mutable state
lives in the ``ChainState`` SQLite DB. The server is single-threaded on a
single-process Fly app; all requests are serialised on the Python GIL.

Usage::

    from chain.server import make_server
    from chain.state import ChainState
    from chain.config import load_settings

    cs = ChainState.init_db("/data/chain.db")
    settings = load_settings()
    httpd = make_server(cs, settings, host="0.0.0.0", port=8080)
    httpd.serve_forever()
"""
from __future__ import annotations

import json
import os
import tempfile
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from chain import fly_client, git_ops
from chain.config import Settings
from chain.state import ChainState
from chain.webhooks import WebhookError, handle_machine_exit


# ---------------------------------------------------------------------------
# Public factory
# ---------------------------------------------------------------------------

def make_server(
    cs: ChainState,
    settings: Settings,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> HTTPServer:
    """Return an ``HTTPServer`` bound to *host*:*port* using the given state.

    The ``ChainState`` and ``Settings`` are captured in the handler class via
    a closure so the ``BaseHTTPRequestHandler`` constructor interface (which
    takes no custom kwargs) is unchanged.
    """

    class _Handler(ChainHTTPHandler):
        _cs = cs
        _settings = settings

    return HTTPServer((host, port), _Handler)


# ---------------------------------------------------------------------------
# Request handler
# ---------------------------------------------------------------------------

class ChainHTTPHandler(BaseHTTPRequestHandler):
    """HTTP handler for the leerie-chain API.

    Subclasses must set ``_cs`` and ``_settings`` as class attributes before
    the server is started (``make_server`` does this via an inner class).
    """

    _cs: ChainState
    _settings: Settings

    # Silence the default per-request log line; callers who want logging can
    # override this or re-enable it by overriding log_message.
    def log_message(self, fmt: str, *args: Any) -> None:
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path.startswith("/chains/") and self.path.count("/") == 2:
            chain_id = self.path[len("/chains/"):]
            self._handle_get_chain(chain_id)
        else:
            self._send_json(404, {"error": f"not found: {self.path}"})

    def do_POST(self) -> None:
        if self.path == "/chains":
            self._handle_post_chains()
        elif self.path == "/webhooks/fly":
            self._handle_post_webhook()
        else:
            self._send_json(404, {"error": f"not found: {self.path}"})

    # ------------------------------------------------------------------
    # GET /chains/<id>
    # ------------------------------------------------------------------

    def _handle_get_chain(self, chain_id: str) -> None:
        snap = self._cs.load_chain(chain_id)
        if snap is None:
            self._send_json(404, {"error": f"chain {chain_id!r} not found"})
            return
        self._send_json(200, snap)

    # ------------------------------------------------------------------
    # POST /chains
    # ------------------------------------------------------------------

    def _handle_post_chains(self) -> None:
        body = self._read_json_body()
        if body is None:
            return

        target: str | None = body.get("target")
        runs_raw: list[Any] | None = body.get("runs")

        if not target or not isinstance(target, str):
            self._send_json(400, {"error": "'target' is required and must be a string"})
            return
        if not runs_raw or not isinstance(runs_raw, list):
            self._send_json(400, {"error": "'runs' is required and must be a non-empty list"})
            return

        run_prompts: list[tuple[str, str]] = []
        for item in runs_raw:
            if not isinstance(item, dict):
                self._send_json(400, {"error": "each run must be an object with 'prompt' and 'wave'"})
                return
            prompt = item.get("prompt", "")
            wave = item.get("wave", "")
            if not prompt or wave not in ("a", "b"):
                self._send_json(
                    400,
                    {"error": "each run must have a non-empty 'prompt' and 'wave' ('a' or 'b')"},
                )
                return
            run_prompts.append((str(prompt), str(wave)))

        # Persist the chain row before any external I/O so partial failures
        # are visible in the DB (chain stays 'running' — caller can query it).
        try:
            chain_id = self._cs.create_chain(target=target, run_prompts=run_prompts)
        except Exception as exc:
            self._send_json(500, {"error": f"failed to create chain: {exc}"})
            return

        # Clone the target repo and create the stage branch in a tempdir.
        # git_ops functions call sys.exit on failure; we catch SystemExit so
        # the server process stays alive and we can return a structured error.
        clone_dir = tempfile.mkdtemp(prefix=f"leerie-chain-{chain_id}-")
        try:
            repo_path = git_ops.clone_target(
                repo_url=target,
                pat=self._settings.gh_dispatch_pat,
                clone_dir=clone_dir,
            )
            git_ops.create_stage_branch(repo_path, chain_id)
        except SystemExit as exc:
            self._send_json(500, {"error": f"git setup failed (exit {exc.code})"})
            return
        except Exception as exc:
            self._send_json(500, {"error": f"git setup failed: {exc}"})
            return

        # Launch all Wave A machines.
        snap = self._cs.load_chain(chain_id)
        if snap is None:
            self._send_json(500, {"error": "chain row disappeared after creation"})
            return

        fly_image = os.environ.get("LEERIE_IMAGE", "registry.fly.io/leerie:latest")
        fly_region = os.environ.get("LEERIE_REGION", "iad")

        for run in snap["runs"]:
            if run["wave"] != "a":
                continue
            try:
                machine_id = fly_client.launch_machine(
                    image=fly_image,
                    env={
                        "LEERIE_CHAIN_ID": chain_id,
                        "LEERIE_CHAIN_RUN_ID": run["id"],
                        "LEERIE_TASK": run["prompt"],
                        "LEERIE_TARGET_REPO": target,
                    },
                    region=fly_region,
                )
            except fly_client.FlyClientError as exc:
                self._send_json(500, {"error": f"failed to launch machine for run {run['id']}: {exc}"})
                return
            self._cs.transition_run(run["id"], "running", machine_id=machine_id)

        # Return the refreshed snapshot.
        updated_snap = self._cs.load_chain(chain_id)
        self._send_json(201, updated_snap)

    # ------------------------------------------------------------------
    # POST /webhooks/fly
    # ------------------------------------------------------------------

    def _handle_post_webhook(self) -> None:
        raw_body = self._read_raw_body()
        if raw_body is None:
            return

        sig_header = self.headers.get("fly-signature-256", "")

        # Parse JSON before calling handle_machine_exit so we have the dict.
        try:
            payload: dict[str, Any] = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return

        try:
            handle_machine_exit(
                cs=self._cs,
                payload=payload,
                secret=self._settings.chain_webhook_secret,
                raw_body=raw_body,
                sig_header=sig_header,
            )
        except WebhookError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(500, {"error": f"webhook handler error: {exc}"})
            return

        # After a successful machine exit, check whether all Wave A runs are
        # done and it's time to launch Wave B.
        self._maybe_advance_to_wave_b(payload)

        self._send_json(200, {"ok": True})

    def _maybe_advance_to_wave_b(self, payload: dict[str, Any]) -> None:
        """If all Wave A runs are done, advance chain to wave_b and launch Wave B machines."""
        # Find the run whose machine_id matches the payload's machine_id.
        machine_id: str | None = (
            payload.get("machine_id")
            or payload.get("id")
            or payload.get("instance_id")
            or None
        )
        if not machine_id:
            return

        # Find the chain that owns this machine_id.
        chain_id_or_none = self._cs.find_chain_id_by_machine_id(machine_id)
        if chain_id_or_none is None:
            return
        chain_id: str = chain_id_or_none

        snap = self._cs.load_chain(chain_id)
        if snap is None:
            return

        # Only advance if we're still in wave_a.
        if snap["wave_state"] != "wave_a":
            return

        wave_a_runs = [r for r in snap["runs"] if r["wave"] == "a"]
        wave_b_runs = [r for r in snap["runs"] if r["wave"] == "b"]

        # All Wave A runs must be in terminal state (done or failed).
        all_done = all(r["status"] in ("done", "failed") for r in wave_a_runs)
        if not all_done:
            return

        # If any Wave A run failed, pause the chain instead of launching Wave B.
        any_failed = any(r["status"] == "failed" for r in wave_a_runs)
        if any_failed:
            self._cs.transition_chain(chain_id, "paused")
            return

        # All Wave A runs completed successfully.
        if not wave_b_runs:
            # No Wave B runs — chain is done.
            self._cs.advance_wave(chain_id, "done")
            self._cs.transition_chain(chain_id, "done")
            return

        # Advance to wave_b and launch all Wave B machines.
        self._cs.advance_wave(chain_id, "wave_b")

        fly_image = os.environ.get("LEERIE_IMAGE", "registry.fly.io/leerie:latest")
        fly_region = os.environ.get("LEERIE_REGION", "iad")
        target = snap["target"]

        for run in wave_b_runs:
            try:
                machine_id_b = fly_client.launch_machine(
                    image=fly_image,
                    env={
                        "LEERIE_CHAIN_ID": chain_id,
                        "LEERIE_CHAIN_RUN_ID": run["id"],
                        "LEERIE_TASK": run["prompt"],
                        "LEERIE_TARGET_REPO": target,
                    },
                    region=fly_region,
                )
            except fly_client.FlyClientError:
                self._cs.transition_chain(chain_id, "failed")
                return
            self._cs.transition_run(run["id"], "running", machine_id=machine_id_b)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _read_raw_body(self) -> bytes | None:
        """Read and return the raw request body bytes."""
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            self._send_json(400, {"error": f"invalid Content-Length: {length_str!r}"})
            return None
        return self.rfile.read(length)

    def _read_json_body(self) -> dict[str, Any] | None:
        """Read, parse, and return the request body as a dict, or send 400."""
        raw = self._read_raw_body()
        if raw is None:
            return None
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            self._send_json(400, {"error": f"invalid JSON body: {exc}"})
            return None
        if not isinstance(data, dict):
            self._send_json(400, {"error": "request body must be a JSON object"})
            return None
        return data

    def _send_json(self, status: int, body: Any) -> None:
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
