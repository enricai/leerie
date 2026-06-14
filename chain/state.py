"""chain.state — SQLite-backed state model for the per-chain ephemeral coordinator.

Mirrors the semantics of the orchestrator's State class (atomic writes,
single-writer) but scoped to multi-run chains rather than a single run.

Single-writer guarantee
-----------------------
The coordinator is a single-process Python HTTP server running on one
ephemeral Fly machine. All ``BaseHTTPRequestHandler`` calls are serialised
on one thread (default ``HTTPServer`` behaviour); they interleave only
between requests, never inside a SQLite transaction. There is therefore
no multi-writer contention. For defence-in-depth, the DB is opened in
WAL mode: WAL allows concurrent *readers* while a write transaction is
in progress, and SQLite's writer-exclusive lock prevents concurrent
writes regardless.

Schema
------
Two tables:

  chains
    id           TEXT PRIMARY KEY   — UUID
    target       TEXT NOT NULL      — target repo URL
    queue_json   TEXT NOT NULL      — the full chain DAG (queue.json contents)
    wave_state   TEXT NOT NULL      — 'wave_0' | 'wave_1' | … | 'done'
    status       TEXT NOT NULL      — 'running' | 'paused' | 'done' | 'failed' | 'cancelled'
    paused       TEXT               — pause reason ('stale_creds', 'push_failed', etc.) or NULL
    created_at   TEXT NOT NULL      — ISO-8601 UTC timestamp
    updated_at   TEXT NOT NULL
    completed_at TEXT               — set when chain reaches a terminal state

  chain_runs
    id                TEXT PRIMARY KEY   — UUID
    chain_id          TEXT NOT NULL      — FK → chains.id
    prompt            TEXT NOT NULL      — task prompt text for this run
    wave              TEXT NOT NULL      — '0' | '1' | '2' | …
    machine_id        TEXT               — Fly machine ID (set when launched)
    status            TEXT NOT NULL      — see RUN_STATUSES below
    exit_code         INTEGER            — orchestrator exit code (set on done/failed)
    branch            TEXT               — leerie/runs/<run-id> branch name (set when worker reports)
    started_at        TEXT               — when transitioned to 'running'
    finished_at       TEXT               — when transitioned to a terminal status
    last_heartbeat_at TEXT               — last /heartbeat ping; used for staleness detection
    created_at        TEXT NOT NULL
    updated_at        TEXT NOT NULL
    FOREIGN KEY (chain_id) REFERENCES chains(id)

Idempotency
-----------
``ChainState.init_db()`` uses ``CREATE TABLE IF NOT EXISTS``, so calling it
multiple times (e.g. after a coordinator restart) is a no-op. The schema
survives across machine restarts because SQLite lives on the persistent
Fly volume mounted at /data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


_DDL = """\
CREATE TABLE IF NOT EXISTS chains (
    id           TEXT PRIMARY KEY,
    target       TEXT NOT NULL,
    queue_json   TEXT NOT NULL DEFAULT '{}',
    wave_state   TEXT NOT NULL DEFAULT 'wave_0',
    status       TEXT NOT NULL DEFAULT 'running',
    paused       TEXT,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS chain_runs (
    id                TEXT PRIMARY KEY,
    chain_id          TEXT NOT NULL,
    prompt            TEXT NOT NULL,
    wave              TEXT NOT NULL,
    machine_id        TEXT,
    status            TEXT NOT NULL DEFAULT 'queued',
    exit_code         INTEGER,
    branch            TEXT,
    started_at        TEXT,
    finished_at       TEXT,
    last_heartbeat_at TEXT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    FOREIGN KEY (chain_id) REFERENCES chains(id)
);
"""

# Valid status values — checked at transition boundaries.
CHAIN_STATUSES = frozenset({"running", "paused", "done", "failed", "cancelled"})

# Run statuses include the chain-extension states:
#   queued       — created, not yet launched
#   running      — worker machine is up and orchestrator is executing
#   done         — orchestrator exited cleanly; branch ready for push/PR
#   failed       — orchestrator exited non-zero (real failure)
#   stale_creds  — orchestrator exited because Claude OAuth token rotated;
#                  not a real failure — recoverable via `leerie --resume <chain>`
#   merge_failed — coordinator's synth-merge step for the next wave conflicted
RUN_STATUSES = frozenset({
    "queued", "running", "done", "failed", "stale_creds", "merge_failed",
})

# Terminal run statuses (won't transition further without user intervention).
RUN_TERMINAL_STATUSES = frozenset({"done", "failed", "stale_creds", "merge_failed"})


def _valid_wave_state(s: str) -> bool:
    """'done' or 'wave_N' for non-negative integer N."""
    if s == "done":
        return True
    return s.startswith("wave_") and s[5:].isdigit()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ChainState:
    """SQLite-backed state for one leerie-chain coordinator process.

    Usage::

        cs = ChainState.init_db("/data/chain.db")
        chain_id = cs.create_chain(
            target="https://github.com/org/repo",
            queue_json='{"jobs": {...}}',
            run_prompts=[("Fetch data", "0"), ("Summarise", "1")],
        )
        cs.transition_run(run_id, "running", machine_id="abc123")
        cs.record_heartbeat(run_id)
        cs.transition_run(run_id, "done", exit_code=0, branch="leerie/runs/abc123")
        snapshot = cs.load_chain(chain_id)
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------------
    # Construction / migration
    # ------------------------------------------------------------------

    @classmethod
    def init_db(cls, path: str | Path) -> "ChainState":
        """Open (or create) the SQLite DB at *path* and apply schema.

        Calling this multiple times on the same *path* is a no-op — DDL
        uses ``CREATE TABLE IF NOT EXISTS``.  WAL mode is enabled for
        read-write concurrency on a single-writer server.
        """
        conn = sqlite3.connect(str(path), check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        conn.executescript(_DDL)
        conn.commit()
        return cls(conn)

    # ------------------------------------------------------------------
    # Chain operations
    # ------------------------------------------------------------------

    def create_chain(
        self,
        target: str,
        run_prompts: list[tuple[str, str]],
        queue_json: str = "{}",
    ) -> str:
        """Insert a new chain and its associated run rows.

        Args:
            target: Target repo URL or local path.
            run_prompts: Ordered list of ``(prompt_text, wave)`` tuples.
                         ``wave`` is a non-negative integer string
                         (``'0'``, ``'1'``, …).
            queue_json: JSON-encoded DAG describing the chain
                        (synth_merge instructions, deps, etc.). Stored
                        as an opaque blob; the coordinator reads it on
                        wave transitions to drive synth-merge.

        Returns:
            The new chain's ``id``.
        """
        chain_id = _new_id()
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO chains"
                " (id, target, queue_json, wave_state, status, created_at, updated_at)"
                " VALUES (?, ?, ?, 'wave_0', 'running', ?, ?)",
                (chain_id, target, queue_json, now, now),
            )
            for prompt, wave in run_prompts:
                if not wave.isdigit():
                    raise ValueError(
                        f"wave must be a non-negative integer string, got {wave!r}"
                    )
                self._conn.execute(
                    "INSERT INTO chain_runs"
                    " (id, chain_id, prompt, wave, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, 'queued', ?, ?)",
                    (_new_id(), chain_id, prompt, wave, now, now),
                )
        return chain_id

    def load_chain(self, chain_id: str) -> dict | None:
        """Return a full chain snapshot, or *None* if not found.

        The returned dict has the shape::

            {
              "id": "...",
              "target": "...",
              "queue_json": "...",
              "wave_state": "wave_0" | "wave_1" | … | "done",
              "status": "running" | "paused" | "done" | "failed" | "cancelled",
              "paused": "stale_creds" | "push_failed" | … | None,
              "created_at": "...",
              "updated_at": "...",
              "completed_at": "..." | None,
              "runs": [
                {
                  "id": "...",
                  "chain_id": "...",
                  "prompt": "...",
                  "wave": "0" | "1" | …,
                  "machine_id": "..." | None,
                  "status": "queued" | "running" | "done" | "failed" | "stale_creds" | "merge_failed",
                  "exit_code": 0 | <int> | None,
                  "branch": "leerie/runs/..." | None,
                  "started_at": "..." | None,
                  "finished_at": "..." | None,
                  "last_heartbeat_at": "..." | None,
                  "created_at": "...",
                  "updated_at": "...",
                },
                ...
              ]
            }
        """
        row = self._conn.execute(
            "SELECT * FROM chains WHERE id = ?", (chain_id,)
        ).fetchone()
        if row is None:
            return None
        chain = dict(row)
        run_rows = self._conn.execute(
            "SELECT * FROM chain_runs WHERE chain_id = ? ORDER BY created_at",
            (chain_id,),
        ).fetchall()
        chain["runs"] = [dict(r) for r in run_rows]
        return chain

    def list_chains(self) -> list[dict]:
        """Return all chains (without their run rows)."""
        rows = self._conn.execute(
            "SELECT * FROM chains ORDER BY created_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Run-status transitions
    # ------------------------------------------------------------------

    def transition_run(
        self,
        run_id: str,
        new_status: str,
        machine_id: str | None = None,
        exit_code: int | None = None,
        branch: str | None = None,
    ) -> None:
        """Advance a run's status.

        Args:
            run_id: The run's ``id``.
            new_status: Target status; must be one of ``RUN_STATUSES``.
            machine_id: When provided, also records the Fly machine ID
                        on the run row (typically set when transitioning
                        to ``'running'``).
            exit_code: Orchestrator exit code (set on done/failed/
                       stale_creds).
            branch: Run branch name (set when worker reports done so
                    the coordinator knows what to push).

        Side-effects on timestamps:
        - Transitioning to ``'running'`` sets ``started_at`` if absent.
        - Transitioning to a terminal status sets ``finished_at``.

        Raises:
            ValueError: If ``new_status`` is not in ``RUN_STATUSES``.
            KeyError: If *run_id* is not found.
        """
        if new_status not in RUN_STATUSES:
            raise ValueError(
                f"invalid run status {new_status!r}; "
                f"must be one of {sorted(RUN_STATUSES)}"
            )
        now = _now()
        # Build the UPDATE dynamically so we only touch fields the caller
        # actually wants to change. The coalesce on started_at preserves
        # the first transition into 'running' (idempotent re-runs reuse
        # the existing timestamp).
        fields: list[str] = ["status = ?", "updated_at = ?"]
        params: list[object] = [new_status, now]
        if machine_id is not None:
            fields.append("machine_id = ?")
            params.append(machine_id)
        if exit_code is not None:
            fields.append("exit_code = ?")
            params.append(exit_code)
        if branch is not None:
            fields.append("branch = ?")
            params.append(branch)
        if new_status == "running":
            fields.append("started_at = COALESCE(started_at, ?)")
            params.append(now)
        if new_status in RUN_TERMINAL_STATUSES:
            fields.append("finished_at = ?")
            params.append(now)
        params.append(run_id)
        sql = "UPDATE chain_runs SET " + ", ".join(fields) + " WHERE id = ?"
        with self._conn:
            result = self._conn.execute(sql, params)
        if result.rowcount == 0:
            raise KeyError(f"run {run_id!r} not found")

    def record_heartbeat(self, run_id: str) -> None:
        """Stamp ``last_heartbeat_at`` to now.

        Called by the coordinator's POST /heartbeat handler. Cheap; runs
        every 60s per running worker. Does NOT update ``updated_at`` —
        heartbeats are not state transitions.

        Raises:
            KeyError: If *run_id* is not found.
        """
        now = _now()
        with self._conn:
            result = self._conn.execute(
                "UPDATE chain_runs SET last_heartbeat_at = ? WHERE id = ?",
                (now, run_id),
            )
        if result.rowcount == 0:
            raise KeyError(f"run {run_id!r} not found")

    # ------------------------------------------------------------------
    # Chain-level transitions
    # ------------------------------------------------------------------

    def transition_chain(
        self,
        chain_id: str,
        new_status: str,
        paused: str | None = None,
    ) -> None:
        """Set a chain's top-level status.

        Args:
            chain_id: The chain's ``id``.
            new_status: Target status; must be one of ``CHAIN_STATUSES``.
            paused: Pause reason (e.g., ``'stale_creds'``,
                    ``'push_failed'``). Stored when transitioning to
                    ``'paused'``; cleared when leaving the paused state.

        Side-effects on timestamps:
        - Transitioning to a terminal status (``done``/``failed``/
          ``cancelled``) sets ``completed_at``.

        Raises:
            ValueError: If *new_status* is not in ``CHAIN_STATUSES``.
            KeyError: If *chain_id* is not found.
        """
        if new_status not in CHAIN_STATUSES:
            raise ValueError(
                f"invalid chain status {new_status!r}; "
                f"must be one of {sorted(CHAIN_STATUSES)}"
            )
        now = _now()
        fields: list[str] = ["status = ?", "updated_at = ?"]
        params: list[object] = [new_status, now]
        if new_status == "paused":
            fields.append("paused = ?")
            params.append(paused)
        elif new_status == "running":
            # Leaving paused state clears the reason.
            fields.append("paused = NULL")
        if new_status in {"done", "failed", "cancelled"}:
            fields.append("completed_at = ?")
            params.append(now)
        params.append(chain_id)
        sql = "UPDATE chains SET " + ", ".join(fields) + " WHERE id = ?"
        with self._conn:
            result = self._conn.execute(sql, params)
        if result.rowcount == 0:
            raise KeyError(f"chain {chain_id!r} not found")

    def advance_wave(self, chain_id: str, new_wave_state: str) -> None:
        """Advance the chain's wave state (e.g. ``'wave_0'`` → ``'wave_1'``).

        Raises:
            ValueError: If *new_wave_state* is not ``'done'`` or
                        ``'wave_N'`` for a non-negative integer N.
            KeyError: If *chain_id* is not found.
        """
        if not _valid_wave_state(new_wave_state):
            raise ValueError(
                f"invalid wave state {new_wave_state!r}; "
                "must be 'done' or 'wave_N' for non-negative integer N"
            )
        now = _now()
        with self._conn:
            result = self._conn.execute(
                "UPDATE chains SET wave_state = ?, updated_at = ? WHERE id = ?",
                (new_wave_state, now, chain_id),
            )
        if result.rowcount == 0:
            raise KeyError(f"chain {chain_id!r} not found")

    def find_chain_id_by_machine_id(self, machine_id: str) -> str | None:
        """Return the chain_id for the run with the given Fly machine ID, or None."""
        row = self._conn.execute(
            "SELECT chain_id FROM chain_runs WHERE machine_id = ?",
            (machine_id,),
        ).fetchone()
        return row["chain_id"] if row is not None else None

    def set_machine_id(self, run_id: str, machine_id: str) -> None:
        """Record the Fly machine ID for a run (separate from status transition)."""
        now = _now()
        with self._conn:
            result = self._conn.execute(
                "UPDATE chain_runs SET machine_id = ?, updated_at = ? WHERE id = ?",
                (machine_id, now, run_id),
            )
        if result.rowcount == 0:
            raise KeyError(f"run {run_id!r} not found")

    def get_run(self, run_id: str) -> dict | None:
        """Return one run row by id, or None."""
        row = self._conn.execute(
            "SELECT * FROM chain_runs WHERE id = ?", (run_id,)
        ).fetchone()
        return dict(row) if row is not None else None

    def stale_running_runs(
        self,
        chain_id: str,
        staleness_threshold_seconds: int,
    ) -> list[dict]:
        """Return running runs whose last heartbeat is older than the threshold.

        Used by the coordinator's periodic stale-detection sweep. A run
        with no heartbeat ever (NULL ``last_heartbeat_at``) but in
        ``running`` status counts as stale once
        ``started_at + threshold`` has passed.

        The threshold is checked in Python (SQLite has no first-class
        datetime arithmetic without extensions), so this method is
        cheap but not zero-cost: it reads all running runs and filters
        in process. For a chain with a handful of runs that is fine.
        """
        from datetime import datetime as _dt
        rows = self._conn.execute(
            "SELECT * FROM chain_runs"
            " WHERE chain_id = ? AND status = 'running'",
            (chain_id,),
        ).fetchall()
        now = _dt.now(timezone.utc)
        stale: list[dict] = []
        for row in rows:
            d = dict(row)
            anchor_iso = d["last_heartbeat_at"] or d["started_at"]
            if not anchor_iso:
                # Worker hasn't even started; nothing to mark stale yet.
                continue
            try:
                anchor = _dt.fromisoformat(anchor_iso)
            except ValueError:
                continue
            if (now - anchor).total_seconds() > staleness_threshold_seconds:
                stale.append(d)
        return stale

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
