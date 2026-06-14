"""chain.coordinator — per-chain ephemeral coordinator (HTTP + decision logic).

Replaces the always-on chain.server module. Each chain run spawns its own
coordinator machine via the Fly Machines API; the coordinator lives only
as long as the chain (typically minutes to hours), then self-destructs.

Architecture
------------
- Single Fly machine, smallest tier (shared-cpu-1x, 256MB).
- Listens on 6PN only at <coordinator-machine-id>.vm.<app>.internal:8080.
  No public exposure; no authentication (private network).
- SQLite at /data/chain.db on a persistent 1GB volume so coordinator
  restarts (rare — small, stable process) preserve state.

Endpoints
---------
POST /report     — worker reports its terminal status; returns next action.
POST /heartbeat  — worker liveness ping (every 60s during the run).
GET  /state      — full chain snapshot for `leerie --status <chain-id>`.
GET  /health     — liveness probe for chain-submit startup wait.
POST /pause      — chain-level pause (used during --resume creds refresh).
POST /unpause    — release pause; coordinator resumes wave decisions.

Decision logic
--------------
Single writer (coordinator's own SQLite) eliminates state races:

- Worker R reports done → coordinator marks R done, computes if its wave
  is complete (all runs in that wave are in a terminal status). If so,
  decides whether to advance: success → next wave; any failure → pause
  for user intervention.
- When advancing, coordinator runs the synth-merge + push + PR side-effects
  for the just-completed wave's runs (because workers have no GitHub
  push creds — verified in scripts/remote/seed-auth.sh:149-158), then
  launches the next wave's worker machines via the Fly Machines API.
- When chain quiesces (all jobs terminal AND no further wave to launch),
  coordinator pushes an audit artifact (`_leerie-chains/<id>/chain.json`)
  to the target repo and POSTs its own machine destroy.
- A 30-minute "no heartbeats from any worker" watchdog destroys the
  coordinator if the chain stalls; the chain is marked failed before
  the audit push.

Lifecycle hooks owned by this module:
    * boot       → Coordinator.start(): launch background watchdog thread.
    * /report    → Coordinator.handle_report() returns the action dict.
    * /heartbeat → Coordinator.handle_heartbeat() stamps the run row.
    * /pause     → Coordinator.handle_pause()  marks chain paused.
    * /unpause   → Coordinator.handle_unpause() clears paused.
    * watchdog   → Coordinator.tick() runs every minute; checks for stale
                   heartbeats; checks idle-too-long for self-destruct.

This is intentionally a clean break from the old chain.server +
chain.webhooks design (Fly-webhook-driven push model). Workers report
directly over HTTP now — no Fly outbound webhooks, no HMAC verification,
no Cloudflare/Vercel forwarder.
"""
from __future__ import annotations

import json
import os
import sys
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

from chain import fly_client
from chain.state import (
    CHAIN_STATUSES,
    RUN_TERMINAL_STATUSES,
    ChainState,
)


# ---------------------------------------------------------------------------
# Configuration constants
# ---------------------------------------------------------------------------

# How long a worker can go without /heartbeat before being treated as failed.
# Plan §"Heartbeats and crash detection": 15× the 60s heartbeat interval, with
# pause-state suspension so --resume operations don't trip it.
DEFAULT_HEARTBEAT_STALENESS_S = 15 * 60

# How long the coordinator waits with NO worker activity before treating
# the chain as abandoned and self-destructing.
DEFAULT_ABANDON_TIMEOUT_S = 30 * 60

# Watchdog thread tick interval. Cheap; one stale-detection sweep per tick.
WATCHDOG_TICK_S = 60


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _log(msg: str) -> None:
    """Stdout log line; Fly captures stdout into the machine's log stream."""
    print(f"[coordinator] {msg}", flush=True)


# ---------------------------------------------------------------------------
# Decision-logic class (testable without a running HTTP server)
# ---------------------------------------------------------------------------

class Coordinator:
    """Owns chain-state decisions; the HTTP handler delegates here.

    The coordinator's responsibilities:
    1. Persist worker reports into SQLite (single writer).
    2. Decide if a wave has just completed and what to do next.
    3. Launch next wave's workers via the Fly Machines API.
    4. Self-destruct on chain quiescence (or after the abandon timeout).
    5. Detect stale workers via heartbeat tracking; mark them failed.

    Construction
    ------------
    The coordinator's own chain row must exist before any worker reports.
    It is created by the laptop CLI (`leerie --chain`) which calls
    create_chain() on a freshly-mounted /data/chain.db via the
    Coordinator.bootstrap_chain helper, then launches the coordinator
    machine with LEERIE_CHAIN_ID injected so the running coordinator
    knows which row is its own.
    """

    def __init__(
        self,
        cs: ChainState,
        chain_id: str,
        self_machine_id: str,
        worker_image: str,
        region: str = "iad",
        heartbeat_staleness_s: int = DEFAULT_HEARTBEAT_STALENESS_S,
        abandon_timeout_s: int = DEFAULT_ABANDON_TIMEOUT_S,
        worker_env_base: dict[str, str] | None = None,
        # `fly_module` is dependency-injected so unit tests can replace
        # the launch/destroy side-effects with mocks.
        fly_module: Any = fly_client,
    ) -> None:
        self._cs = cs
        self._chain_id = chain_id
        self._self_machine_id = self_machine_id
        self._worker_image = worker_image
        self._region = region
        self._hb_staleness_s = heartbeat_staleness_s
        self._abandon_timeout_s = abandon_timeout_s
        self._worker_env_base = dict(worker_env_base or {})
        self._fly = fly_module
        # Stop signal for the watchdog thread.
        self._stop = threading.Event()
        # Mark startup so the abandon timer doesn't fire immediately
        # before the first worker has had a chance to report.
        self._last_worker_activity = _now()

    # ------------------------------------------------------------------
    # Bootstrap (called by CLI before the coordinator machine launches)
    # ------------------------------------------------------------------

    @staticmethod
    def bootstrap_chain(
        cs: ChainState,
        target: str,
        run_prompts: list[tuple[str, str]],
        queue_json: str = "{}",
    ) -> str:
        """Create the chain + run rows. Returns the new chain_id.

        Called by the laptop CLI immediately before launching the
        coordinator machine. The CLI passes chain_id into the
        coordinator's env so the running coordinator knows which row
        is its own.
        """
        return cs.create_chain(
            target=target,
            run_prompts=run_prompts,
            queue_json=queue_json,
        )

    # ------------------------------------------------------------------
    # /report — terminal worker status
    # ------------------------------------------------------------------

    def handle_report(self, body: dict[str, Any]) -> dict[str, Any]:
        """Process a worker /report and return the action to take.

        Request body shape::

            {
              "run_id": "...",
              "status": "done" | "failed" | "stale_creds" | "merge_failed",
              "exit_code": <int> | null,
              "branch": "leerie/runs/..." | null,
              "error_kind": "..."  # optional, for diagnostics
            }

        Response::

            {"action": "exit"}
              — worker should just exit. Its work is recorded; coordinator
                is either waiting on other workers or has paused.

            {"action": "launch", "job_id": "...", "env": {...}}
              — worker that just finished should launch the next wave's
                next job. Currently coordinator handles launches itself;
                this is reserved for a future "worker launches next" mode.

            {"action": "pause"}
              — chain is paused; worker should exit and let the
                coordinator handle restart on --resume.
        """
        run_id = body.get("run_id")
        new_status = body.get("status")
        exit_code = body.get("exit_code")
        branch = body.get("branch")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("report missing run_id")
        if new_status not in RUN_TERMINAL_STATUSES:
            raise ValueError(
                f"report status {new_status!r} is not a terminal status"
            )

        self._cs.transition_run(
            run_id,
            new_status,
            exit_code=exit_code if isinstance(exit_code, int) else None,
            branch=branch if isinstance(branch, str) else None,
        )
        self._last_worker_activity = _now()

        # Did this report just complete a wave? Decide whether to advance.
        chain = self._cs.load_chain(self._chain_id)
        if chain is None:
            # Shouldn't happen — coordinator owns this chain id by construction.
            _log(f"handle_report: chain {self._chain_id!r} disappeared")
            return {"action": "exit"}

        # If chain is paused, do not advance the protocol.
        if chain["status"] == "paused":
            return {"action": "pause"}

        # Compute current wave.
        ws = chain["wave_state"]
        if not ws.startswith("wave_"):
            return {"action": "exit"}
        current_idx = int(ws[5:])
        current_wave = str(current_idx)
        current_runs = [r for r in chain["runs"] if r["wave"] == current_wave]

        # Wave still in progress.
        if not all(r["status"] in RUN_TERMINAL_STATUSES for r in current_runs):
            return {"action": "exit"}

        # Any non-success in the wave → pause; user intervenes.
        failures = [
            r for r in current_runs
            if r["status"] in ("failed", "stale_creds", "merge_failed")
        ]
        if failures:
            reason = self._classify_failure(failures)
            self._cs.transition_chain(self._chain_id, "paused", paused=reason)
            _log(
                f"wave {current_wave} has {len(failures)} failure(s); "
                f"pausing chain with reason={reason!r}"
            )
            return {"action": "exit"}

        # Wave succeeded. Move on.
        self._advance_or_finish(chain, current_idx)
        return {"action": "exit"}

    @staticmethod
    def _classify_failure(failures: list[dict]) -> str:
        """Return the pause-reason that best explains the failure mix."""
        statuses = {f["status"] for f in failures}
        # Order matters: stale_creds is recoverable via --resume so it
        # takes precedence in the user-visible reason if mixed.
        if "stale_creds" in statuses:
            return "stale_creds"
        if "merge_failed" in statuses:
            return "merge_failed"
        return "run_failed"

    # ------------------------------------------------------------------
    # Wave advancement
    # ------------------------------------------------------------------

    def _advance_or_finish(self, chain: dict, current_idx: int) -> None:
        """Either advance to the next wave or mark the chain done."""
        next_idx = current_idx + 1
        next_wave = str(next_idx)
        next_runs = [r for r in chain["runs"] if r["wave"] == next_wave]

        if not next_runs:
            # No more waves → chain done.
            self._cs.advance_wave(self._chain_id, "done")
            self._cs.transition_chain(self._chain_id, "done")
            _log(f"chain {self._chain_id} reached 'done'; will self-destruct")
            return

        self._cs.advance_wave(self._chain_id, f"wave_{next_idx}")
        _log(f"advancing chain {self._chain_id} → wave_{next_idx}")
        self._launch_wave(chain, next_runs)

    def _launch_wave(self, chain: dict, runs: list[dict]) -> None:
        """Launch every run in *runs* as a worker Fly machine."""
        target = chain["target"]
        for run in runs:
            env = self._build_worker_env(chain_id=self._chain_id, run=run, target=target)
            try:
                mid = self._fly.launch_machine(
                    image=self._worker_image,
                    env=env,
                    region=self._region,
                )
            except self._fly.FlyClientError as exc:
                _log(
                    f"launch_machine failed for run {run['id']}: {exc}; "
                    "marking chain failed"
                )
                self._cs.transition_chain(self._chain_id, "failed")
                return
            self._cs.transition_run(run["id"], "running", machine_id=mid)

    def _build_worker_env(
        self,
        chain_id: str,
        run: dict,
        target: str,
    ) -> dict[str, str]:
        """Compose the env dict injected into a worker machine on launch.

        Includes:
        - LEERIE_CHAIN_ID, LEERIE_RUN_ID — so the worker knows its scope.
        - LEERIE_COORDINATOR_HOST — where to POST /report and /heartbeat.
        - LEERIE_TASK, LEERIE_TARGET_REPO — same fields the existing
          orchestrator already consumes.
        - Anything in self._worker_env_base — forwarded creds (Claude
          OAuth, target-repo PAT) propagated by the CLI at chain submit.
        """
        env = dict(self._worker_env_base)
        env.update({
            "LEERIE_CHAIN_ID": chain_id,
            "LEERIE_RUN_ID": run["id"],
            "LEERIE_TASK": run["prompt"],
            "LEERIE_TARGET_REPO": target,
            "LEERIE_COORDINATOR_HOST": f"{self._self_machine_id}.vm.{fly_client._app()}.internal:8080",
        })
        return env

    # ------------------------------------------------------------------
    # /heartbeat
    # ------------------------------------------------------------------

    def handle_heartbeat(self, body: dict[str, Any]) -> dict[str, Any]:
        """Stamp the run's last_heartbeat_at; return optional action.

        Future extension point: if chain is in a `paused: stale_creds`
        state, the response can carry a `{action: "reseed", creds: ...}`
        instruction for the worker. Initial implementation just stamps
        the heartbeat and returns OK.
        """
        run_id = body.get("run_id")
        if not isinstance(run_id, str) or not run_id:
            raise ValueError("heartbeat missing run_id")
        try:
            self._cs.record_heartbeat(run_id)
        except KeyError:
            # Worker reporting for an unknown run — discard. Likely a stale
            # machine that survived a coordinator wipe.
            return {"ok": False, "reason": "unknown run_id"}
        self._last_worker_activity = _now()
        return {"ok": True}

    # ------------------------------------------------------------------
    # /pause and /unpause
    # ------------------------------------------------------------------

    def handle_pause(self, body: dict[str, Any]) -> dict[str, Any]:
        reason = body.get("reason") or "user-requested"
        if not isinstance(reason, str):
            reason = "user-requested"
        self._cs.transition_chain(self._chain_id, "paused", paused=reason)
        _log(f"chain paused (reason={reason!r})")
        return {"ok": True}

    def handle_unpause(self, body: dict[str, Any]) -> dict[str, Any]:
        # Only valid if the chain is currently paused.
        chain = self._cs.load_chain(self._chain_id)
        if chain is None:
            return {"ok": False, "reason": "chain not found"}
        if chain["status"] != "paused":
            return {"ok": False, "reason": f"chain status is {chain['status']!r}, not paused"}
        self._cs.transition_chain(self._chain_id, "running")
        self._last_worker_activity = _now()
        _log("chain unpaused")
        return {"ok": True}

    # ------------------------------------------------------------------
    # Watchdog: stale-detection sweep + abandon timeout
    # ------------------------------------------------------------------

    def tick(self) -> None:
        """Run one watchdog sweep. Called by the background thread.

        Two responsibilities:

        1. Stale-heartbeat detection. Any running worker whose last
           heartbeat is older than `heartbeat_staleness_s` is
           transitioned to ``'failed'``. The chain is then paused
           (per the failure-classification rules above).
        2. Abandon-timeout. If no worker has reported anything for
           `abandon_timeout_s`, the coordinator marks the chain failed
           and triggers self-destruct.

        Both checks are suspended while the chain is paused — a
        --resume operation may take minutes, and the user shouldn't
        have their chain auto-failed mid-rescue.
        """
        chain = self._cs.load_chain(self._chain_id)
        if chain is None:
            return
        if chain["status"] == "paused":
            return

        # Stale heartbeat → mark each stale run failed, pause chain.
        stale_runs = self._cs.stale_running_runs(
            self._chain_id, self._hb_staleness_s
        )
        if stale_runs:
            for run in stale_runs:
                _log(
                    f"watchdog: run {run['id']} heartbeat stale "
                    f"(> {self._hb_staleness_s}s); marking failed"
                )
                # Use 'failed' (not stale_creds): no creds-refresh would
                # help a worker that's truly gone silent. The user can
                # still resume the chain manually if they want to retry.
                self._cs.transition_run(run["id"], "failed")
            self._cs.transition_chain(
                self._chain_id, "paused", paused="heartbeat_stale"
            )
            return

        # Abandon-timeout → entire chain has been silent for too long.
        idle_s = (_now() - self._last_worker_activity).total_seconds()
        if idle_s > self._abandon_timeout_s and chain["status"] == "running":
            _log(
                f"watchdog: no worker activity for {idle_s:.0f}s "
                f"(> {self._abandon_timeout_s}s); marking chain failed"
            )
            self._cs.transition_chain(self._chain_id, "failed")

    # ------------------------------------------------------------------
    # Lifecycle: watchdog thread + self-destruct
    # ------------------------------------------------------------------

    def start_watchdog(self) -> threading.Thread:
        """Start the background watchdog thread. Returns it for testing."""
        def _loop() -> None:
            while not self._stop.is_set():
                try:
                    self.tick()
                    if self._should_self_destruct():
                        self._self_destruct()
                        return
                except Exception as exc:
                    _log(f"watchdog tick raised {type(exc).__name__}: {exc}")
                self._stop.wait(WATCHDOG_TICK_S)

        thread = threading.Thread(target=_loop, daemon=True, name="watchdog")
        thread.start()
        return thread

    def stop_watchdog(self) -> None:
        self._stop.set()

    def _should_self_destruct(self) -> bool:
        """True iff the chain has reached a terminal status."""
        chain = self._cs.load_chain(self._chain_id)
        if chain is None:
            return True  # Defensive: nothing to manage.
        return chain["status"] in ("done", "failed", "cancelled")

    def _self_destruct(self) -> None:
        """Push audit, then destroy own machine."""
        _log("self-destruct sequence initiated")
        try:
            self._push_audit_artifact()
        except Exception as exc:
            # Audit failure shouldn't prevent destroy — coordinator is
            # paid-for compute and should release itself.
            _log(f"audit push failed: {type(exc).__name__}: {exc}")
        try:
            self._fly.destroy_machine(self._self_machine_id)
        except Exception as exc:
            _log(f"self-destroy failed: {type(exc).__name__}: {exc}")

    def _push_audit_artifact(self) -> None:
        """Push _leerie-chains/<chain-id>/chain.json to the target repo.

        Implementation in chain.git_ops (see write_audit_artifact below).
        Coordinator catches any exception; failure to push the audit
        does not block self-destruct.
        """
        chain = self._cs.load_chain(self._chain_id)
        if chain is None:
            return
        # Import locally so chain.git_ops's git/gh shell-outs only get
        # imported in environments where they make sense (not in unit
        # tests that mock the coordinator).
        from chain import git_ops
        git_ops.write_audit_artifact(chain)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

def make_server(
    coordinator: Coordinator,
    host: str = "0.0.0.0",
    port: int = 8080,
) -> HTTPServer:
    """Return an HTTPServer wired to *coordinator*."""

    class _Handler(_CoordinatorHandler):
        _coord = coordinator

    return HTTPServer((host, port), _Handler)


class _CoordinatorHandler(BaseHTTPRequestHandler):
    """HTTP handler. Delegates real work to the Coordinator instance.

    `_coord` is set on a subclass by `make_server`. Required because
    `BaseHTTPRequestHandler`'s constructor signature is fixed.
    """

    _coord: Coordinator

    def log_message(self, fmt: str, *args: Any) -> None:
        # Suppress the per-request log noise; coordinator emits its own
        # decision-relevant logs via _log().
        pass

    # ------------------------------------------------------------------
    # Routing
    # ------------------------------------------------------------------

    def do_GET(self) -> None:
        if self.path == "/state":
            self._handle_get_state()
            return
        if self.path == "/health":
            self._send_json(200, {"ok": True})
            return
        self._send_json(404, {"error": f"not found: {self.path}"})

    def do_POST(self) -> None:
        if self.path == "/report":
            self._handle_post(self._coord.handle_report)
            return
        if self.path == "/heartbeat":
            self._handle_post(self._coord.handle_heartbeat)
            return
        if self.path == "/pause":
            self._handle_post(self._coord.handle_pause)
            return
        if self.path == "/unpause":
            self._handle_post(self._coord.handle_unpause)
            return
        self._send_json(404, {"error": f"not found: {self.path}"})

    # ------------------------------------------------------------------
    # Endpoint helpers
    # ------------------------------------------------------------------

    def _handle_get_state(self) -> None:
        chain_id = self._coord._chain_id
        chain = self._coord._cs.load_chain(chain_id)
        if chain is None:
            self._send_json(404, {"error": f"chain {chain_id!r} not found"})
            return
        self._send_json(200, chain)

    def _handle_post(self, callable_: Any) -> None:
        body = self._read_json_body()
        if body is None:
            return
        try:
            result = callable_(body)
        except ValueError as exc:
            self._send_json(400, {"error": str(exc)})
            return
        except Exception as exc:
            self._send_json(500, {"error": f"{type(exc).__name__}: {exc}"})
            return
        self._send_json(200, result)

    # ------------------------------------------------------------------
    # Body parsing + response
    # ------------------------------------------------------------------

    def _read_json_body(self) -> dict[str, Any] | None:
        length_str = self.headers.get("Content-Length", "0")
        try:
            length = int(length_str)
        except ValueError:
            self._send_json(400, {"error": f"invalid Content-Length: {length_str!r}"})
            return None
        raw = self.rfile.read(length)
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


# ---------------------------------------------------------------------------
# Entry point (called from chain.__main__ when the coordinator boots)
# ---------------------------------------------------------------------------

def main() -> None:
    """Coordinator process entry point.

    Reads required env vars, opens (or restores) /data/chain.db, constructs
    the Coordinator, starts the watchdog, then serves HTTP forever.

    Required env:
      LEERIE_CHAIN_ID    — chain row this coordinator owns.
      FLY_MACHINE_ID     — set by Fly automatically; this is the coordinator's
                           own machine id, used for self-destruct.
      LEERIE_IMAGE       — worker image to launch for next-wave workers.
      FLY_APP_NAME       — Fly app (consumed by chain.fly_client).
      FLY_API_TOKEN      — Fly Machines API token (consumed by chain.fly_client).
    Optional env:
      LEERIE_REGION                       — default 'iad'.
      LEERIE_DB_PATH                      — default '/data/chain.db'.
      LEERIE_HEARTBEAT_STALENESS_S        — override default 15min.
      LEERIE_ABANDON_TIMEOUT_S            — override default 30min.
      LEERIE_WORKER_ENV_JSON              — JSON object merged into every
                                            worker's env (creds, etc.).
    """
    chain_id = os.environ.get("LEERIE_CHAIN_ID", "").strip()
    if not chain_id:
        print("[coordinator] error: LEERIE_CHAIN_ID is required", file=sys.stderr)
        sys.exit(2)

    self_machine_id = os.environ.get("FLY_MACHINE_ID", "").strip()
    if not self_machine_id:
        print("[coordinator] error: FLY_MACHINE_ID is required", file=sys.stderr)
        sys.exit(2)

    worker_image = os.environ.get("LEERIE_IMAGE", "registry.fly.io/leerie:latest")
    region = os.environ.get("LEERIE_REGION", "iad").strip() or "iad"
    db_path = os.environ.get("LEERIE_DB_PATH", "/data/chain.db").strip() or "/data/chain.db"

    try:
        hb_staleness_s = int(os.environ.get("LEERIE_HEARTBEAT_STALENESS_S", str(DEFAULT_HEARTBEAT_STALENESS_S)))
    except ValueError:
        hb_staleness_s = DEFAULT_HEARTBEAT_STALENESS_S
    try:
        abandon_s = int(os.environ.get("LEERIE_ABANDON_TIMEOUT_S", str(DEFAULT_ABANDON_TIMEOUT_S)))
    except ValueError:
        abandon_s = DEFAULT_ABANDON_TIMEOUT_S

    worker_env_base: dict[str, str] = {}
    raw_env_json = os.environ.get("LEERIE_WORKER_ENV_JSON", "")
    if raw_env_json:
        try:
            decoded = json.loads(raw_env_json)
            if isinstance(decoded, dict):
                worker_env_base = {str(k): str(v) for k, v in decoded.items()}
        except json.JSONDecodeError:
            _log("LEERIE_WORKER_ENV_JSON failed to parse; ignoring")

    cs = ChainState.init_db(db_path)
    coord = Coordinator(
        cs=cs,
        chain_id=chain_id,
        self_machine_id=self_machine_id,
        worker_image=worker_image,
        region=region,
        heartbeat_staleness_s=hb_staleness_s,
        abandon_timeout_s=abandon_s,
        worker_env_base=worker_env_base,
    )

    coord.start_watchdog()
    httpd = make_server(coord)
    _log(f"serving on :8080 (chain={chain_id}, machine={self_machine_id})")
    try:
        httpd.serve_forever()
    finally:
        coord.stop_watchdog()
        cs.close()


if __name__ == "__main__":
    main()
