"""Tests for chain.state.ChainState — SQLite-backed chain state model.

All tests use an in-memory SQLite DB (":memory:") so no filesystem access
is required and tests are fully isolated.
"""
from __future__ import annotations

import pytest

from chain.state import ChainState, CHAIN_STATUSES, RUN_STATUSES, WAVE_STATES


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> ChainState:
    """Return a fresh in-memory ChainState."""
    return ChainState.init_db(":memory:")


def _simple_chain(cs: ChainState) -> tuple[str, list[str]]:
    """Create a chain with two runs (one Wave A, one Wave B).

    Returns (chain_id, [run_id_a, run_id_b]).
    """
    chain_id = cs.create_chain(
        target="https://github.com/test/repo",
        run_prompts=[
            ("Fetch the data", "a"),
            ("Summarise the data", "b"),
        ],
    )
    snapshot = cs.load_chain(chain_id)
    assert snapshot is not None
    run_ids = [r["id"] for r in snapshot["runs"]]
    return chain_id, run_ids


# ---------------------------------------------------------------------------
# init_db — idempotency
# ---------------------------------------------------------------------------

def test_init_db_creates_schema() -> None:
    cs = _make_db()
    assert cs is not None
    cs.close()


def test_init_db_idempotent(tmp_path) -> None:
    """Calling init_db twice on the same file is a no-op (no error)."""
    db_path = tmp_path / "chain.db"
    cs1 = ChainState.init_db(db_path)
    cs1.close()
    cs2 = ChainState.init_db(db_path)
    cs2.close()


def test_init_db_wal_mode(tmp_path) -> None:
    """init_db enables WAL journal mode."""
    db_path = tmp_path / "chain.db"
    cs = ChainState.init_db(db_path)
    row = cs._conn.execute("PRAGMA journal_mode").fetchone()
    assert row[0] == "wal"
    cs.close()


# ---------------------------------------------------------------------------
# create_chain / load_chain
# ---------------------------------------------------------------------------

def test_create_chain_returns_id() -> None:
    cs = _make_db()
    chain_id = cs.create_chain("repo-url", [("Task A", "a")])
    assert isinstance(chain_id, str)
    assert len(chain_id) > 0
    cs.close()


def test_load_chain_returns_snapshot() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["id"] == chain_id
    assert snap["target"] == "https://github.com/test/repo"
    assert snap["wave_state"] == "wave_a"
    assert snap["status"] == "running"
    assert len(snap["runs"]) == 2
    cs.close()


def test_load_chain_run_fields() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_a = snap["runs"][0]
    assert run_a["wave"] == "a"
    assert run_a["status"] == "queued"
    assert run_a["machine_id"] is None
    assert run_a["chain_id"] == chain_id
    run_b = snap["runs"][1]
    assert run_b["wave"] == "b"
    cs.close()


def test_load_chain_missing_returns_none() -> None:
    cs = _make_db()
    result = cs.load_chain("nonexistent-id")
    assert result is None
    cs.close()


def test_create_chain_n_runs() -> None:
    cs = _make_db()
    prompts = [(f"Run {i}", "a") for i in range(5)]
    chain_id = cs.create_chain("target", prompts)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert len(snap["runs"]) == 5
    cs.close()


def test_create_chain_invalid_wave_raises() -> None:
    cs = _make_db()
    with pytest.raises(ValueError, match="wave must be"):
        cs.create_chain("target", [("Task", "c")])
    cs.close()


# ---------------------------------------------------------------------------
# list_chains
# ---------------------------------------------------------------------------

def test_list_chains_empty() -> None:
    cs = _make_db()
    assert cs.list_chains() == []
    cs.close()


def test_list_chains_returns_all() -> None:
    cs = _make_db()
    cs.create_chain("repo1", [("T1", "a")])
    cs.create_chain("repo2", [("T2", "a")])
    chains = cs.list_chains()
    assert len(chains) == 2
    targets = {c["target"] for c in chains}
    assert targets == {"repo1", "repo2"}
    cs.close()


# ---------------------------------------------------------------------------
# transition_run — status transitions
# ---------------------------------------------------------------------------

def test_transition_run_queued_to_running() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "running"
    cs.close()


def test_transition_run_running_to_done() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[0], "done")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "done"
    cs.close()


def test_transition_run_running_to_failed() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[0], "failed")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "failed"
    cs.close()


def test_transition_run_with_machine_id() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="fly-machine-abc123")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["machine_id"] == "fly-machine-abc123"
    assert snap["runs"][0]["status"] == "running"
    cs.close()


def test_transition_run_invalid_status_raises() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    with pytest.raises(ValueError, match="invalid run status"):
        cs.transition_run(run_ids[0], "unknown-status")
    cs.close()


def test_transition_run_missing_id_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.transition_run("nonexistent-run", "running")
    cs.close()


def test_all_run_statuses_accepted() -> None:
    """Each status in RUN_STATUSES is accepted by transition_run."""
    for status in RUN_STATUSES:
        cs = _make_db()
        chain_id, run_ids = _simple_chain(cs)
        cs.transition_run(run_ids[0], status)
        snap = cs.load_chain(chain_id)
        assert snap is not None
        assert snap["runs"][0]["status"] == status
        cs.close()


# ---------------------------------------------------------------------------
# transition_chain
# ---------------------------------------------------------------------------

def test_transition_chain_to_paused() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.transition_chain(chain_id, "paused")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    cs.close()


def test_transition_chain_to_done() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.transition_chain(chain_id, "done")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "done"
    cs.close()


def test_transition_chain_invalid_status_raises() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    with pytest.raises(ValueError, match="invalid chain status"):
        cs.transition_chain(chain_id, "bad-status")
    cs.close()


def test_transition_chain_missing_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.transition_chain("nonexistent-chain", "done")
    cs.close()


def test_all_chain_statuses_accepted() -> None:
    for status in CHAIN_STATUSES:
        cs = _make_db()
        chain_id, _ = _simple_chain(cs)
        cs.transition_chain(chain_id, status)
        snap = cs.load_chain(chain_id)
        assert snap is not None
        assert snap["status"] == status
        cs.close()


# ---------------------------------------------------------------------------
# advance_wave
# ---------------------------------------------------------------------------

def test_advance_wave_a_to_b() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.advance_wave(chain_id, "wave_b")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "wave_b"
    cs.close()


def test_advance_wave_b_to_done() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.advance_wave(chain_id, "wave_b")
    cs.advance_wave(chain_id, "done")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "done"
    cs.close()


def test_advance_wave_invalid_raises() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    with pytest.raises(ValueError, match="invalid wave state"):
        cs.advance_wave(chain_id, "wave_c")
    cs.close()


def test_advance_wave_missing_chain_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.advance_wave("nonexistent-chain", "wave_b")
    cs.close()


def test_all_wave_states_accepted() -> None:
    for ws in WAVE_STATES:
        cs = _make_db()
        chain_id, _ = _simple_chain(cs)
        cs.advance_wave(chain_id, ws)
        snap = cs.load_chain(chain_id)
        assert snap is not None
        assert snap["wave_state"] == ws
        cs.close()


# ---------------------------------------------------------------------------
# set_machine_id
# ---------------------------------------------------------------------------

def test_set_machine_id() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.set_machine_id(run_ids[0], "mach-xyz")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["machine_id"] == "mach-xyz"
    cs.close()


def test_set_machine_id_missing_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.set_machine_id("nonexistent-run", "mach-xyz")
    cs.close()


# ---------------------------------------------------------------------------
# Full flow: create → run → advance wave → run → done
# ---------------------------------------------------------------------------

def test_full_chain_flow() -> None:
    """Create chain, run Wave A, advance to Wave B, run Wave B, mark done."""
    cs = _make_db()
    chain_id = cs.create_chain(
        target="https://github.com/org/repo",
        run_prompts=[
            ("Fetch data", "a"),
            ("Process results", "b"),
        ],
    )

    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_a_id = snap["runs"][0]["id"]
    run_b_id = snap["runs"][1]["id"]

    # Wave A: launch, complete
    cs.transition_run(run_a_id, "running", machine_id="m-001")
    cs.transition_run(run_a_id, "done")

    # Advance chain to wave_b
    cs.advance_wave(chain_id, "wave_b")

    # Wave B: launch, complete
    cs.transition_run(run_b_id, "running", machine_id="m-002")
    cs.transition_run(run_b_id, "done")

    # Mark chain done
    cs.advance_wave(chain_id, "done")
    cs.transition_chain(chain_id, "done")

    # Final snapshot
    final = cs.load_chain(chain_id)
    assert final is not None
    assert final["status"] == "done"
    assert final["wave_state"] == "done"
    assert final["runs"][0]["status"] == "done"
    assert final["runs"][0]["machine_id"] == "m-001"
    assert final["runs"][1]["status"] == "done"
    assert final["runs"][1]["machine_id"] == "m-002"
    cs.close()


def test_chain_pause_on_failure() -> None:
    """A failed Wave A run can pause the chain."""
    cs = _make_db()
    chain_id = cs.create_chain("repo", [("Run 1", "a"), ("Run 2", "a")])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_ids = [r["id"] for r in snap["runs"]]

    cs.transition_run(run_ids[0], "running", machine_id="m-100")
    cs.transition_run(run_ids[0], "failed")
    cs.transition_chain(chain_id, "paused")

    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["runs"][0]["status"] == "failed"
    # Run 1 still queued — not launched
    assert snap["runs"][1]["status"] == "queued"
    cs.close()


# ---------------------------------------------------------------------------
# Disjoint storage — two distinct chain IDs
# (mirrors test_two_states_disjoint_paths / test_two_states_save_independently
#  from tests/test_state_per_run.py)
# ---------------------------------------------------------------------------

def test_two_chains_disjoint_state() -> None:
    """Transitioning one chain's state must not affect the other chain."""
    cs = _make_db()
    chain_a = cs.create_chain("repo-a", [("Task A1", "a"), ("Task A2", "b")])
    chain_b = cs.create_chain("repo-b", [("Task B1", "a")])

    snap_a = cs.load_chain(chain_a)
    snap_b = cs.load_chain(chain_b)
    assert snap_a is not None and snap_b is not None
    run_a_id = snap_a["runs"][0]["id"]

    # Advance chain A's state; chain B must remain unchanged.
    cs.transition_run(run_a_id, "running", machine_id="m-a")
    cs.advance_wave(chain_a, "wave_b")
    cs.transition_chain(chain_a, "paused")

    snap_a2 = cs.load_chain(chain_a)
    snap_b2 = cs.load_chain(chain_b)
    assert snap_a2 is not None and snap_b2 is not None

    # Chain A reflects changes.
    assert snap_a2["status"] == "paused"
    assert snap_a2["wave_state"] == "wave_b"
    assert snap_a2["runs"][0]["status"] == "running"

    # Chain B is entirely unaffected.
    assert snap_b2["status"] == "running"
    assert snap_b2["wave_state"] == "wave_a"
    assert snap_b2["runs"][0]["status"] == "queued"
    assert snap_b2["runs"][0]["machine_id"] is None

    cs.close()


# ---------------------------------------------------------------------------
# Persistence: data survives close + reopen
# ---------------------------------------------------------------------------

def test_persistence_across_reopen(tmp_path) -> None:
    db_path = tmp_path / "chain.db"
    cs = ChainState.init_db(db_path)
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="fly-persisted")
    cs.close()

    cs2 = ChainState.init_db(db_path)
    snap = cs2.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "running"
    assert snap["runs"][0]["machine_id"] == "fly-persisted"
    cs2.close()
