"""chain.state — SQLite-backed state model for leerie-chain.

Mirrors the semantics of the orchestrator's State class (atomic writes,
single-writer) but scoped to multi-run chains rather than a single run.

Single-writer guarantee
-----------------------
leerie-chain is a single-process Fly app (one machine, one Python process).
All HTTP handler calls are serialised on a single asyncio event loop; they
interleave only at ``await`` points, which never fall inside a SQLite
transaction. There is therefore no multi-writer contention and no need for
a threading lock. For defence-in-depth, the DB is opened in WAL mode: WAL
allows concurrent *readers* while a write transaction is in progress,
and SQLite's writer-exclusive lock prevents concurrent writes regardless.

Schema
------
Two tables:

  chains
    id           TEXT PRIMARY KEY   — opaque UUID-style identifier
    target       TEXT NOT NULL      — target repo URL or local path
    wave_state   TEXT NOT NULL      — 'wave_a' | 'wave_b' | 'done'
    status       TEXT NOT NULL      — 'running' | 'paused' | 'done' | 'failed' | 'cancelled'
    created_at   TEXT NOT NULL      — ISO-8601 UTC timestamp
    updated_at   TEXT NOT NULL

  chain_runs
    id           TEXT PRIMARY KEY   — opaque UUID-style identifier
    chain_id     TEXT NOT NULL      — FK → chains.id
    prompt       TEXT NOT NULL      — task prompt text for this run
    wave         TEXT NOT NULL      — 'a' | 'b'
    machine_id   TEXT               — Fly machine ID (set when launched)
    status       TEXT NOT NULL      — 'queued' | 'running' | 'done' | 'failed'
    created_at   TEXT NOT NULL
    updated_at   TEXT NOT NULL
    FOREIGN KEY (chain_id) REFERENCES chains(id)

Idempotency
-----------
``ChainState.init_db()`` uses ``CREATE TABLE IF NOT EXISTS``, so calling it
multiple times (e.g. after a machine restart) is a no-op.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import uuid


_DDL = """\
CREATE TABLE IF NOT EXISTS chains (
    id         TEXT PRIMARY KEY,
    target     TEXT NOT NULL,
    wave_state TEXT NOT NULL DEFAULT 'wave_a',
    status     TEXT NOT NULL DEFAULT 'running',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chain_runs (
    id         TEXT PRIMARY KEY,
    chain_id   TEXT NOT NULL,
    prompt     TEXT NOT NULL,
    wave       TEXT NOT NULL,
    machine_id TEXT,
    status     TEXT NOT NULL DEFAULT 'queued',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (chain_id) REFERENCES chains(id)
);
"""

# Valid status values — checked at transition boundaries.
CHAIN_STATUSES = frozenset({"running", "paused", "done", "failed", "cancelled"})
RUN_STATUSES = frozenset({"queued", "running", "done", "failed"})
WAVE_STATES = frozenset({"wave_a", "wave_b", "done"})


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ChainState:
    """SQLite-backed state for one leerie-chain server process.

    Usage::

        cs = ChainState.init_db("/data/chain.db")
        chain_id = cs.create_chain(target="https://github.com/org/repo",
                                   run_prompts=[("Fetch data", "a"),
                                                ("Summarise", "b")])
        cs.transition_run(run_id, "running")
        cs.transition_run(run_id, "done")
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
    ) -> str:
        """Insert a new chain and its associated run rows.

        Args:
            target: Target repo URL or local path.
            run_prompts: Ordered list of ``(prompt_text, wave)`` tuples.
                         ``wave`` must be ``'a'`` or ``'b'``.

        Returns:
            The new chain's ``id``.
        """
        chain_id = _new_id()
        now = _now()
        with self._conn:
            self._conn.execute(
                "INSERT INTO chains (id, target, wave_state, status, created_at, updated_at)"
                " VALUES (?, ?, 'wave_a', 'running', ?, ?)",
                (chain_id, target, now, now),
            )
            for prompt, wave in run_prompts:
                if wave not in ("a", "b"):
                    raise ValueError(f"wave must be 'a' or 'b', got {wave!r}")
                self._conn.execute(
                    "INSERT INTO chain_runs"
                    " (id, chain_id, prompt, wave, machine_id, status, created_at, updated_at)"
                    " VALUES (?, ?, ?, ?, NULL, 'queued', ?, ?)",
                    (_new_id(), chain_id, prompt, wave, now, now),
                )
        return chain_id

    def load_chain(self, chain_id: str) -> dict | None:
        """Return a full chain snapshot, or *None* if not found.

        The returned dict has the shape::

            {
              "id": "...",
              "target": "...",
              "wave_state": "wave_a" | "wave_b" | "done",
              "status": "running" | "paused" | "done" | "failed" | "cancelled",
              "created_at": "...",
              "updated_at": "...",
              "runs": [
                {
                  "id": "...",
                  "chain_id": "...",
                  "prompt": "...",
                  "wave": "a" | "b",
                  "machine_id": "..." | None,
                  "status": "queued" | "running" | "done" | "failed",
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
    ) -> None:
        """Advance a run's status.

        Args:
            run_id: The run's ``id``.
            new_status: Target status; must be one of ``RUN_STATUSES``.
            machine_id: When provided, also records the Fly machine ID on
                        the run row (typically set when transitioning to
                        ``'running'``).

        Raises:
            ValueError: If ``new_status`` is not in ``RUN_STATUSES``.
            KeyError: If *run_id* is not found.
        """
        if new_status not in RUN_STATUSES:
            raise ValueError(
                f"invalid run status {new_status!r}; must be one of {sorted(RUN_STATUSES)}"
            )
        now = _now()
        with self._conn:
            if machine_id is not None:
                result = self._conn.execute(
                    "UPDATE chain_runs SET status = ?, machine_id = ?, updated_at = ?"
                    " WHERE id = ?",
                    (new_status, machine_id, now, run_id),
                )
            else:
                result = self._conn.execute(
                    "UPDATE chain_runs SET status = ?, updated_at = ? WHERE id = ?",
                    (new_status, now, run_id),
                )
        if result.rowcount == 0:
            raise KeyError(f"run {run_id!r} not found")

    # ------------------------------------------------------------------
    # Chain-level transitions
    # ------------------------------------------------------------------

    def transition_chain(self, chain_id: str, new_status: str) -> None:
        """Set a chain's top-level status.

        Raises:
            ValueError: If *new_status* is not in ``CHAIN_STATUSES``.
            KeyError: If *chain_id* is not found.
        """
        if new_status not in CHAIN_STATUSES:
            raise ValueError(
                f"invalid chain status {new_status!r}; must be one of {sorted(CHAIN_STATUSES)}"
            )
        now = _now()
        with self._conn:
            result = self._conn.execute(
                "UPDATE chains SET status = ?, updated_at = ? WHERE id = ?",
                (new_status, now, chain_id),
            )
        if result.rowcount == 0:
            raise KeyError(f"chain {chain_id!r} not found")

    def advance_wave(self, chain_id: str, new_wave_state: str) -> None:
        """Advance the chain's wave state (e.g. ``'wave_a'`` → ``'wave_b'``).

        Raises:
            ValueError: If *new_wave_state* is not in ``WAVE_STATES``.
            KeyError: If *chain_id* is not found.
        """
        if new_wave_state not in WAVE_STATES:
            raise ValueError(
                f"invalid wave state {new_wave_state!r}; must be one of {sorted(WAVE_STATES)}"
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

    def close(self) -> None:
        """Close the underlying SQLite connection."""
        self._conn.close()
