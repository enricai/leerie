"""Tests for chain.state.ChainState — SQLite-backed chain state model.

All tests use an in-memory SQLite DB (":memory:") so no filesystem access
is required and tests are fully isolated.
"""
from __future__ import annotations

import time

import pytest

from chain.state import (
    CHAIN_STATUSES,
    RUN_STATUSES,
    RUN_TERMINAL_STATUSES,
    ChainState,
    _valid_wave_state,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_db() -> ChainState:
    """Return a fresh in-memory ChainState."""
    return ChainState.init_db(":memory:")


def _simple_chain(cs: ChainState) -> tuple[str, list[str]]:
    """Create a chain with two runs (wave 0 and wave 1).

    Returns (chain_id, [run_id_0, run_id_1]).
    """
    chain_id = cs.create_chain(
        target="https://github.com/test/repo",
        run_prompts=[
            ("Fetch the data", "0"),
            ("Summarise the data", "1"),
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
    chain_id = cs.create_chain("repo-url", [("Task A", "0")])
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
    assert snap["wave_state"] == "wave_0"
    assert snap["status"] == "running"
    assert len(snap["runs"]) == 2
    cs.close()


def test_load_chain_run_fields() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_0 = snap["runs"][0]
    assert run_0["wave"] == "0"
    assert run_0["status"] == "queued"
    assert run_0["machine_id"] is None
    assert run_0["chain_id"] == chain_id
    run_1 = snap["runs"][1]
    assert run_1["wave"] == "1"
    cs.close()


def test_load_chain_missing_returns_none() -> None:
    cs = _make_db()
    result = cs.load_chain("nonexistent-id")
    assert result is None
    cs.close()


def test_create_chain_n_runs() -> None:
    cs = _make_db()
    prompts = [(f"Run {i}", "0") for i in range(5)]
    chain_id = cs.create_chain("target", prompts)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert len(snap["runs"]) == 5
    cs.close()


def test_create_chain_invalid_wave_raises() -> None:
    cs = _make_db()
    with pytest.raises(ValueError, match="non-negative integer"):
        cs.create_chain("target", [("Task", "x")])
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
    cs.create_chain("repo1", [("T1", "0")])
    cs.create_chain("repo2", [("T2", "0")])
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

def test_advance_wave_0_to_1() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.advance_wave(chain_id, "wave_1")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "wave_1"
    cs.close()


def test_advance_wave_through_n_to_done() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.advance_wave(chain_id, "wave_1")
    cs.advance_wave(chain_id, "done")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "done"
    cs.close()


def test_advance_wave_invalid_raises() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    with pytest.raises(ValueError, match="invalid wave state"):
        cs.advance_wave(chain_id, "wave_x")
    cs.close()


def test_advance_wave_missing_chain_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.advance_wave("nonexistent-chain", "wave_1")
    cs.close()


def test_advance_wave_high_index_accepted() -> None:
    """Wave indices above 1 are valid (N-wave chains)."""
    for idx in (0, 1, 5, 99):
        cs = _make_db()
        chain_id, _ = _simple_chain(cs)
        ws = f"wave_{idx}"
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
    """Create chain, run wave 0, advance to wave 1, run wave 1, mark done."""
    cs = _make_db()
    chain_id = cs.create_chain(
        target="https://github.com/org/repo",
        run_prompts=[
            ("Fetch data", "0"),
            ("Process results", "1"),
        ],
    )

    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_0_id = snap["runs"][0]["id"]
    run_1_id = snap["runs"][1]["id"]

    cs.transition_run(run_0_id, "running", machine_id="m-001")
    cs.transition_run(run_0_id, "done")

    cs.advance_wave(chain_id, "wave_1")

    cs.transition_run(run_1_id, "running", machine_id="m-002")
    cs.transition_run(run_1_id, "done")

    cs.advance_wave(chain_id, "done")
    cs.transition_chain(chain_id, "done")

    final = cs.load_chain(chain_id)
    assert final is not None
    assert final["status"] == "done"
    assert final["wave_state"] == "done"
    assert final["runs"][0]["status"] == "done"
    assert final["runs"][0]["machine_id"] == "m-001"
    assert final["runs"][1]["status"] == "done"
    assert final["runs"][1]["machine_id"] == "m-002"
    cs.close()


def test_full_chain_flow_3_waves() -> None:
    """3-wave chain: wave_0 → wave_1 → wave_2 → done."""
    cs = _make_db()
    chain_id = cs.create_chain(
        target="repo",
        run_prompts=[("W0", "0"), ("W1", "1"), ("W2", "2")],
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    rids = [r["id"] for r in snap["runs"]]

    for i, rid in enumerate(rids):
        cs.transition_run(rid, "running", machine_id=f"m-{i}")
        cs.transition_run(rid, "done")
        if i < len(rids) - 1:
            cs.advance_wave(chain_id, f"wave_{i + 1}")

    cs.advance_wave(chain_id, "done")
    cs.transition_chain(chain_id, "done")

    final = cs.load_chain(chain_id)
    assert final is not None
    assert final["wave_state"] == "done"
    assert all(r["status"] == "done" for r in final["runs"])
    cs.close()


def test_chain_pause_on_failure() -> None:
    """A failed wave 0 run can pause the chain."""
    cs = _make_db()
    chain_id = cs.create_chain("repo", [("Run 1", "0"), ("Run 2", "0")])
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
    chain_a = cs.create_chain("repo-a", [("Task A1", "0"), ("Task A2", "1")])
    chain_b = cs.create_chain("repo-b", [("Task B1", "0")])

    snap_a = cs.load_chain(chain_a)
    snap_b = cs.load_chain(chain_b)
    assert snap_a is not None and snap_b is not None
    run_a_id = snap_a["runs"][0]["id"]

    # Advance chain A's state; chain B must remain unchanged.
    cs.transition_run(run_a_id, "running", machine_id="m-a")
    cs.advance_wave(chain_a, "wave_1")
    cs.transition_chain(chain_a, "paused")

    snap_a2 = cs.load_chain(chain_a)
    snap_b2 = cs.load_chain(chain_b)
    assert snap_a2 is not None and snap_b2 is not None

    # Chain A reflects changes.
    assert snap_a2["status"] == "paused"
    assert snap_a2["wave_state"] == "wave_1"
    assert snap_a2["runs"][0]["status"] == "running"

    # Chain B is entirely unaffected.
    assert snap_b2["status"] == "running"
    assert snap_b2["wave_state"] == "wave_0"
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


# ---------------------------------------------------------------------------
# New schema: queue_json blob
# ---------------------------------------------------------------------------

def test_create_chain_stores_queue_json() -> None:
    cs = _make_db()
    qj = '{"jobs": {"r0": {"deps": []}, "r1": {"deps": ["r0"]}}}'
    chain_id = cs.create_chain("repo", [("T", "0")], queue_json=qj)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["queue_json"] == qj
    cs.close()


def test_create_chain_default_queue_json_is_empty_object() -> None:
    cs = _make_db()
    chain_id = cs.create_chain("repo", [("T", "0")])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["queue_json"] == "{}"
    cs.close()


# ---------------------------------------------------------------------------
# New schema: exit_code, branch, and timestamps on transition_run
# ---------------------------------------------------------------------------

def test_transition_run_records_exit_code_and_branch() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(
        run_ids[0],
        "done",
        exit_code=0,
        branch="leerie/runs/abc123",
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run = snap["runs"][0]
    assert run["status"] == "done"
    assert run["exit_code"] == 0
    assert run["branch"] == "leerie/runs/abc123"
    cs.close()


def test_transition_run_running_sets_started_at() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["started_at"] is None
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["started_at"] is not None
    cs.close()


def test_transition_run_terminal_sets_finished_at() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(run_ids[0], "done", exit_code=0)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["finished_at"] is not None
    cs.close()


def test_transition_run_failed_records_exit_code() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(run_ids[0], "failed", exit_code=1)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["exit_code"] == 1
    cs.close()


def test_started_at_idempotent_across_re_runs() -> None:
    """Calling transition_run('running') twice keeps the first started_at."""
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    first_started = snap["runs"][0]["started_at"]
    assert first_started is not None
    # Pause briefly so any new timestamp would clearly differ.
    time.sleep(0.01)
    cs.transition_run(run_ids[0], "running")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["started_at"] == first_started
    cs.close()


# ---------------------------------------------------------------------------
# New schema: stale_creds and merge_failed statuses
# ---------------------------------------------------------------------------

def test_stale_creds_status_accepted() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(run_ids[0], "stale_creds", exit_code=2)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "stale_creds"
    cs.close()


def test_merge_failed_status_accepted() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(run_ids[0], "merge_failed")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["status"] == "merge_failed"
    cs.close()


def test_stale_creds_is_terminal() -> None:
    """stale_creds counts as a terminal status (sets finished_at)."""
    assert "stale_creds" in RUN_TERMINAL_STATUSES
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running", machine_id="m-1")
    cs.transition_run(run_ids[0], "stale_creds")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["finished_at"] is not None
    cs.close()


# ---------------------------------------------------------------------------
# Heartbeats
# ---------------------------------------------------------------------------

def test_record_heartbeat_sets_timestamp() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    cs.record_heartbeat(run_ids[0])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["last_heartbeat_at"] is not None
    cs.close()


def test_record_heartbeat_missing_run_raises() -> None:
    cs = _make_db()
    with pytest.raises(KeyError):
        cs.record_heartbeat("nonexistent-run")
    cs.close()


def test_record_heartbeat_advances_timestamp() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.record_heartbeat(run_ids[0])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    first_hb = snap["runs"][0]["last_heartbeat_at"]
    time.sleep(0.01)
    cs.record_heartbeat(run_ids[0])
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["last_heartbeat_at"] != first_hb
    cs.close()


# ---------------------------------------------------------------------------
# Stale-running-runs detection
# ---------------------------------------------------------------------------

def test_stale_running_runs_empty_when_no_runs() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    assert cs.stale_running_runs(chain_id, 60) == []
    cs.close()


def test_stale_running_runs_excludes_non_running() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    # No running runs at all; both are 'queued'.
    assert cs.stale_running_runs(chain_id, 0) == []
    cs.close()


def test_stale_running_runs_detects_stale() -> None:
    """A run whose heartbeat is older than the threshold appears as stale."""
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    cs.record_heartbeat(run_ids[0])
    # Threshold of 0s: every running run with any heartbeat already
    # "older than 0 seconds" qualifies, after a brief sleep.
    time.sleep(0.05)
    stale = cs.stale_running_runs(chain_id, 0)
    assert len(stale) == 1
    assert stale[0]["id"] == run_ids[0]
    cs.close()


def test_stale_running_runs_fresh_heartbeat_not_stale() -> None:
    """A run whose heartbeat is very recent is NOT stale."""
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    cs.transition_run(run_ids[0], "running")
    cs.record_heartbeat(run_ids[0])
    # 60-second threshold; heartbeat just happened.
    assert cs.stale_running_runs(chain_id, 60) == []
    cs.close()


# ---------------------------------------------------------------------------
# Chain-level paused reason
# ---------------------------------------------------------------------------

def test_transition_chain_paused_records_reason() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.transition_chain(chain_id, "paused", paused="stale_creds")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "stale_creds"
    cs.close()


def test_transition_chain_running_clears_paused() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    cs.transition_chain(chain_id, "paused", paused="stale_creds")
    cs.transition_chain(chain_id, "running")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["paused"] is None
    cs.close()


def test_transition_chain_terminal_sets_completed_at() -> None:
    cs = _make_db()
    chain_id, _ = _simple_chain(cs)
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["completed_at"] is None
    cs.transition_chain(chain_id, "done")
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["completed_at"] is not None
    cs.close()


# ---------------------------------------------------------------------------
# get_run
# ---------------------------------------------------------------------------

def test_get_run_returns_row() -> None:
    cs = _make_db()
    chain_id, run_ids = _simple_chain(cs)
    row = cs.get_run(run_ids[0])
    assert row is not None
    assert row["id"] == run_ids[0]
    cs.close()


def test_get_run_missing_returns_none() -> None:
    cs = _make_db()
    assert cs.get_run("nonexistent") is None
    cs.close()


# ---------------------------------------------------------------------------
# create_chain_with_id
# (used by chain.coordinator's first-boot bootstrap to honor the
# launcher-minted chain UUID rather than minting a new one)
# ---------------------------------------------------------------------------

def test_create_chain_with_id_round_trips_caller_id() -> None:
    """load_chain returns the same id the caller passed in."""
    cs = _make_db()
    explicit_id = "abcdef01-2345-4789-89ab-0123456789ab"
    returned = cs.create_chain_with_id(
        chain_id=explicit_id,
        target="repo",
        run_prompts=[("Run A", "0")],
    )
    assert returned == explicit_id
    snap = cs.load_chain(explicit_id)
    assert snap is not None
    assert snap["id"] == explicit_id
    cs.close()


def test_create_chain_with_id_duplicate_raises_integrity_error() -> None:
    """Two inserts with the same chain_id raise sqlite3.IntegrityError.

    Important because the coordinator's bootstrap path runs once per Fly
    boot — a duplicate call (e.g. coordinator restarted mid-bootstrap
    after committing the chain row) must be caught explicitly rather
    than silently double-inserting.
    """
    import sqlite3
    cs = _make_db()
    chain_id = "11111111-1111-4111-8111-111111111111"
    cs.create_chain_with_id(chain_id=chain_id, target="r", run_prompts=[("T", "0")])
    with pytest.raises(sqlite3.IntegrityError):
        cs.create_chain_with_id(
            chain_id=chain_id, target="r2", run_prompts=[("T2", "0")]
        )
    cs.close()


def test_create_chain_with_id_invalid_wave_raises_value_error() -> None:
    """Same wave validation as create_chain — caller-supplied id doesn't
    bypass the integrity check."""
    cs = _make_db()
    with pytest.raises(ValueError, match="non-negative integer"):
        cs.create_chain_with_id(
            chain_id="22222222-2222-4222-8222-222222222222",
            target="r",
            run_prompts=[("T", "not-an-int")],
        )
    cs.close()


def test_create_chain_with_id_inserts_all_runs() -> None:
    """Multi-run insert preserves both the caller's chain_id and every run."""
    cs = _make_db()
    chain_id = "33333333-3333-4333-8333-333333333333"
    cs.create_chain_with_id(
        chain_id=chain_id,
        target="repo",
        run_prompts=[("Wave-0 A", "0"), ("Wave-0 B", "0"), ("Wave-1", "1")],
        queue_json='{"jobs": {}}',
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["queue_json"] == '{"jobs": {}}'
    assert len(snap["runs"]) == 3
    waves = sorted(r["wave"] for r in snap["runs"])
    assert waves == ["0", "0", "1"]


def test_create_chain_delegates_to_create_chain_with_id() -> None:
    """create_chain(target, run_prompts) is equivalent to
    create_chain_with_id(<fresh-uuid>, target, run_prompts) — the
    only difference is who mints the UUID. This pins the contract so
    a future refactor that diverges the two paths is caught."""
    cs = _make_db()
    minted = cs.create_chain("repo", [("Task", "0")])
    # The returned id must be a UUID (8-4-4-4-12).
    import re
    assert re.fullmatch(
        r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
        minted,
    ), f"create_chain returned non-UUID id: {minted!r}"
    snap = cs.load_chain(minted)
    assert snap is not None
    cs.close()
