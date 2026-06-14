"""Tests for chain.coordinator.Coordinator — decision logic without HTTP.

The HTTP layer is a thin wrapper over Coordinator's handle_* methods;
these tests bypass it and exercise the decision logic directly with an
in-memory SQLite DB and a stub fly_module that records launches.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from chain.coordinator import Coordinator
from chain.state import ChainState


# ---------------------------------------------------------------------------
# Stub fly_client (records launches/destroys; never touches the network)
# ---------------------------------------------------------------------------

class _StubFly:
    """Records launch_machine + destroy_machine calls.

    Mirrors the surface used by Coordinator (launch_machine, destroy_machine,
    FlyClientError). Each launch returns a deterministic machine id so
    tests can assert that workers were assigned correctly.
    """

    class FlyClientError(Exception):
        pass

    def __init__(self) -> None:
        self.launched: list[dict[str, Any]] = []
        self.destroyed: list[str] = []
        self.launch_should_fail: bool = False
        self._next_id = 0

    def launch_machine(
        self,
        image: str,
        env: dict[str, str],
        region: str,
        vm_cpus: int = 4,
        vm_memory_mb: int = 8192,
    ) -> str:
        if self.launch_should_fail:
            raise self.FlyClientError("stubbed launch failure")
        self._next_id += 1
        mid = f"stub-machine-{self._next_id:04d}"
        self.launched.append({
            "image": image,
            "env": env,
            "region": region,
            "machine_id": mid,
        })
        return mid

    def destroy_machine(self, machine_id: str) -> None:
        self.destroyed.append(machine_id)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_coord(
    run_prompts: list[tuple[str, str]] | None = None,
    target: str = "https://github.com/test/repo",
    queue_json: str = "{}",
    heartbeat_staleness_s: int = 900,
    abandon_timeout_s: int = 1800,
) -> tuple[Coordinator, ChainState, _StubFly, str, list[str]]:
    """Spin up a coordinator with an in-memory DB and stub Fly client."""
    cs = ChainState.init_db(":memory:")
    chain_id = cs.create_chain(
        target=target,
        run_prompts=run_prompts or [("R0a", "0"), ("R0b", "0"), ("R1", "1")],
        queue_json=queue_json,
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_ids = [r["id"] for r in snap["runs"]]
    fly = _StubFly()
    coord = Coordinator(
        cs=cs,
        chain_id=chain_id,
        self_machine_id="stub-coordinator-001",
        worker_image="registry.fly.io/leerie:test",
        region="iad",
        heartbeat_staleness_s=heartbeat_staleness_s,
        abandon_timeout_s=abandon_timeout_s,
        fly_module=fly,
    )
    return coord, cs, fly, chain_id, run_ids


# ---------------------------------------------------------------------------
# bootstrap_chain
# ---------------------------------------------------------------------------

def test_bootstrap_chain_creates_row() -> None:
    cs = ChainState.init_db(":memory:")
    chain_id = Coordinator.bootstrap_chain(
        cs=cs,
        target="repo",
        run_prompts=[("R0", "0"), ("R1", "1")],
        queue_json='{"jobs": {}}',
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["target"] == "repo"
    assert snap["queue_json"] == '{"jobs": {}}'
    assert len(snap["runs"]) == 2
    cs.close()


# ---------------------------------------------------------------------------
# handle_report — wave advancement
# ---------------------------------------------------------------------------

def test_report_done_partial_wave_returns_exit() -> None:
    """When only one of two wave-0 runs is done, the other gets 'exit'."""
    coord, cs, fly, chain_id, run_ids = _make_coord()
    # Both wave-0 runs are launched and running.
    cs.transition_run(run_ids[0], "running", machine_id="mid-0")
    cs.transition_run(run_ids[1], "running", machine_id="mid-1")

    action = coord.handle_report({
        "run_id": run_ids[0],
        "status": "done",
        "exit_code": 0,
        "branch": "leerie/runs/mid-0",
    })
    assert action == {"action": "exit"}
    # No new wave launched yet.
    assert fly.launched == []
    # Chain is still in wave_0.
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "wave_0"


def test_report_done_completes_wave_advances_and_launches() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running", machine_id="mid-0")
    cs.transition_run(run_ids[1], "running", machine_id="mid-1")
    coord.handle_report({"run_id": run_ids[0], "status": "done", "exit_code": 0})
    coord.handle_report({"run_id": run_ids[1], "status": "done", "exit_code": 0})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["wave_state"] == "wave_1"
    # Exactly one new launch — the wave-1 run.
    assert len(fly.launched) == 1
    assert fly.launched[0]["env"]["LEERIE_RUN_ID"] == run_ids[2]
    # The launched run transitioned to 'running'.
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][2]["status"] == "running"
    assert snap["runs"][2]["machine_id"] == "stub-machine-0001"


def test_report_failed_pauses_chain() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[1], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    coord.handle_report({"run_id": run_ids[1], "status": "failed", "exit_code": 1})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "run_failed"
    # No further launches happen on failure.
    assert fly.launched == []


def test_report_stale_creds_pauses_with_stale_creds_reason() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[1], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    coord.handle_report({"run_id": run_ids[1], "status": "stale_creds"})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "stale_creds"


def test_report_merge_failed_pauses_with_merge_failed_reason() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[1], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    coord.handle_report({"run_id": run_ids[1], "status": "merge_failed"})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "merge_failed"


def test_report_last_wave_marks_chain_done() -> None:
    """One-wave chain → first report completes the chain."""
    coord, cs, fly, chain_id, run_ids = _make_coord(
        run_prompts=[("only", "0")]
    )
    cs.transition_run(run_ids[0], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "done"
    assert snap["wave_state"] == "done"
    assert fly.launched == []


def test_report_missing_run_id_raises() -> None:
    coord, *_ = _make_coord()
    with pytest.raises(ValueError, match="missing run_id"):
        coord.handle_report({"status": "done"})


def test_report_invalid_status_raises() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    with pytest.raises(ValueError, match="not a terminal status"):
        coord.handle_report({"run_id": run_ids[0], "status": "running"})


def test_report_while_paused_returns_pause() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    cs.transition_chain(chain_id, "paused", paused="user-requested")
    action = coord.handle_report({"run_id": run_ids[0], "status": "done"})
    assert action == {"action": "pause"}


# ---------------------------------------------------------------------------
# Wave launch fails → chain failed
# ---------------------------------------------------------------------------

def test_wave_launch_failure_marks_chain_failed() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    fly.launch_should_fail = True
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[1], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    coord.handle_report({"run_id": run_ids[1], "status": "done"})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "failed"


# ---------------------------------------------------------------------------
# Worker env injection on launch
# ---------------------------------------------------------------------------

def test_launch_env_includes_chain_id_run_id_and_coordinator_host() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    cs.transition_run(run_ids[1], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    coord.handle_report({"run_id": run_ids[1], "status": "done"})
    assert len(fly.launched) == 1
    env = fly.launched[0]["env"]
    assert env["LEERIE_CHAIN_ID"] == chain_id
    assert env["LEERIE_RUN_ID"] == run_ids[2]
    assert "LEERIE_COORDINATOR_HOST" in env
    assert env["LEERIE_COORDINATOR_HOST"].startswith("stub-coordinator-001.vm.")
    assert env["LEERIE_TARGET_REPO"] == "https://github.com/test/repo"


def test_worker_env_base_is_forwarded() -> None:
    """Creds + other base env vars are merged into every launched worker."""
    cs = ChainState.init_db(":memory:")
    chain_id = cs.create_chain(
        target="repo", run_prompts=[("R0", "0"), ("R1", "1")]
    )
    snap = cs.load_chain(chain_id)
    assert snap is not None
    run_ids = [r["id"] for r in snap["runs"]]
    fly = _StubFly()
    coord = Coordinator(
        cs=cs,
        chain_id=chain_id,
        self_machine_id="coord-x",
        worker_image="img",
        worker_env_base={"CLAUDE_API_KEY": "sk-stub", "FOO": "bar"},
        fly_module=fly,
    )
    cs.transition_run(run_ids[0], "running")
    coord.handle_report({"run_id": run_ids[0], "status": "done"})
    env = fly.launched[0]["env"]
    assert env["CLAUDE_API_KEY"] == "sk-stub"
    assert env["FOO"] == "bar"
    # And the standard chain env still wins on conflict (no overrides here).
    assert env["LEERIE_CHAIN_ID"] == chain_id


# ---------------------------------------------------------------------------
# handle_heartbeat
# ---------------------------------------------------------------------------

def test_heartbeat_stamps_run_row() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord()
    cs.transition_run(run_ids[0], "running")
    result = coord.handle_heartbeat({"run_id": run_ids[0]})
    assert result == {"ok": True}
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["runs"][0]["last_heartbeat_at"] is not None


def test_heartbeat_unknown_run_returns_not_ok() -> None:
    coord, *_ = _make_coord()
    result = coord.handle_heartbeat({"run_id": "nonexistent"})
    assert result == {"ok": False, "reason": "unknown run_id"}


def test_heartbeat_missing_run_id_raises() -> None:
    coord, *_ = _make_coord()
    with pytest.raises(ValueError, match="missing run_id"):
        coord.handle_heartbeat({})


# ---------------------------------------------------------------------------
# pause / unpause
# ---------------------------------------------------------------------------

def test_pause_marks_chain_paused() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    result = coord.handle_pause({"reason": "stale_creds"})
    assert result == {"ok": True}
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "stale_creds"


def test_pause_without_reason_uses_default() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    coord.handle_pause({})
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["paused"] == "user-requested"


def test_unpause_returns_chain_to_running() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    coord.handle_pause({"reason": "stale_creds"})
    result = coord.handle_unpause({})
    assert result == {"ok": True}
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "running"
    assert snap["paused"] is None


def test_unpause_when_not_paused_returns_not_ok() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    result = coord.handle_unpause({})
    assert result["ok"] is False
    assert "not paused" in result["reason"]


# ---------------------------------------------------------------------------
# Watchdog: stale heartbeat detection
# ---------------------------------------------------------------------------

def test_tick_does_nothing_when_no_running_runs() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    coord.tick()
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "running"


def test_tick_marks_stale_runs_failed_and_pauses() -> None:
    coord, cs, fly, chain_id, run_ids = _make_coord(
        heartbeat_staleness_s=0,  # any heartbeat is "stale" after a beat.
    )
    cs.transition_run(run_ids[0], "running")
    cs.record_heartbeat(run_ids[0])
    # Give the timestamp room to be "older than 0".
    import time as _t
    _t.sleep(0.05)
    coord.tick()
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "paused"
    assert snap["paused"] == "heartbeat_stale"
    assert snap["runs"][0]["status"] == "failed"


def test_tick_suspended_while_paused() -> None:
    """Watchdog must not interfere while a --resume is in progress."""
    coord, cs, fly, chain_id, run_ids = _make_coord(
        heartbeat_staleness_s=0,
    )
    cs.transition_run(run_ids[0], "running")
    cs.record_heartbeat(run_ids[0])
    cs.transition_chain(chain_id, "paused", paused="user-requested")
    import time as _t
    _t.sleep(0.05)
    coord.tick()
    snap = cs.load_chain(chain_id)
    assert snap is not None
    # Still paused for the original reason — watchdog did nothing.
    assert snap["paused"] == "user-requested"
    assert snap["runs"][0]["status"] == "running"


def test_tick_abandons_chain_when_idle_too_long() -> None:
    coord, cs, fly, chain_id, _ = _make_coord(
        heartbeat_staleness_s=10_000,
        abandon_timeout_s=0,
    )
    # Move the last-activity baseline into the past so tick sees idle > 0.
    coord._last_worker_activity = datetime.now(timezone.utc) - timedelta(seconds=10)
    coord.tick()
    snap = cs.load_chain(chain_id)
    assert snap is not None
    assert snap["status"] == "failed"


# ---------------------------------------------------------------------------
# Self-destruct gating
# ---------------------------------------------------------------------------

def test_should_self_destruct_after_chain_done() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    cs.transition_chain(chain_id, "done")
    assert coord._should_self_destruct() is True


def test_should_self_destruct_after_chain_failed() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    cs.transition_chain(chain_id, "failed")
    assert coord._should_self_destruct() is True


def test_should_not_self_destruct_while_running() -> None:
    coord, *_ = _make_coord()
    assert coord._should_self_destruct() is False


def test_should_not_self_destruct_while_paused() -> None:
    coord, cs, fly, chain_id, _ = _make_coord()
    cs.transition_chain(chain_id, "paused", paused="stale_creds")
    assert coord._should_self_destruct() is False
