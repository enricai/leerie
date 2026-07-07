"""Tests for group-scoped verb dispatch across two state dirs.

Two-member group fixture: member A is paused/unpushed (in ~/.leerie/repo-a/),
member B is pushed/done (in ~/.leerie/repo-b/).  Tests assert that every
group-scoped verb (--status, --resume, --finalize, --kill, --stop) dispatches
across both dirs and applies the correct eligibility filter.

Mirror of tests/test_chain_launcher_id_dispatch.py: bash subprocess harness
with LEERIE_SELF_CMD stub to record recursive single-run invocations.

Key distinction from test_group_launcher.py (test-003):
  - Uses a single shared fixture (paused/unpushed + pushed) across two dirs
    to exercise all verbs against the same state in one place.
  - Adds --stop dispatch tests (missing from test_group_launcher.py).
  - Verifies dual-purpose-verb handling: a UUID that is a group_id (not a
    chain_id) still dispatches correctly via the chain→group fallback path.
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

GROUP_ID = "f00dface-cafe-4bad-beef-0123456789ab"
CHAIN_ID_ONLY = "a1b2c3d4-0000-4000-b000-000000000001"
RUN_ID_A = "aaa000000001aa"  # paused, fly member (unpushed)
RUN_ID_B = "bbb000000002bb"  # pushed, done (no fly_machine_id)
NON_GROUP_RUN = "ccc000000003cc"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_run(state_dir: Path, run_id: str, fields: dict) -> None:
    """Materialise $state_dir/runs/<run_id>/run.json with *fields*."""
    rd = state_dir / "runs" / run_id
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(json.dumps(fields))


def _stub_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Stub binary that records its full argv per invocation.

    Returns ``(stub_path, log_path)``.
    """
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _two_member_fixture(tmp_path: Path) -> tuple[Path, Path]:
    """Shared two-member group fixture across two separate state dirs.

    Member A (repo-a):  paused, fly member (fly_machine_id set, paused_at set,
                        no pushed_at) → eligible for resume, finalize, kill, stop.
    Member B (repo-b):  pushed, done (pushed_at set, no fly_machine_id) →
                        NOT eligible for resume (not paused), NOT for finalize
                        (already pushed), NOT for kill (no fly_machine_id),
                        NOT for stop (no fly_machine_id + already pushed).

    Returns (sd_a, sd_b).
    """
    sd_a = tmp_path / ".leerie" / "repo-a"
    sd_b = tmp_path / ".leerie" / "repo-b"
    _write_run(sd_a, RUN_ID_A, {
        "run_id": RUN_ID_A,
        "group_id": GROUP_ID,
        "branch": f"leerie/runs/{RUN_ID_A}",
        "fly_machine_id": RUN_ID_A,
        "paused_at": "2026-07-01T10:00:00Z",
        "pause_reason": "worker-error",
    })
    _write_run(sd_b, RUN_ID_B, {
        "run_id": RUN_ID_B,
        "group_id": GROUP_ID,
        "branch": f"leerie/runs/{RUN_ID_B}",
        "finished_at": "2026-07-01T11:00:00Z",
        "pushed_at": "2026-07-01T11:05:00Z",
        "pr_url": "https://github.com/x/b/pull/1",
    })
    return sd_a, sd_b


def _run(
    tmp_path: Path,
    args: list[str],
    stub: Path | None = None,
    stub_log: Path | None = None,
    env_extra: dict | None = None,
    use_self_stub: bool = False,
) -> subprocess.CompletedProcess:
    """Invoke the launcher and return a CompletedProcess with .stub_log."""
    state_dir = tmp_path / ".leerie" / "myrepo"
    env = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": str(tmp_path),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "LEERIE_REPO": str(REPO_ROOT),
        "USER_REPO": str(tmp_path),
    }
    if env_extra:
        env.update(env_extra)
    _log = stub_log
    if stub:
        env["LEERIE_SELF_CMD"] = str(stub)
    elif use_self_stub:
        stub, _log = _stub_self_cmd(tmp_path)
        env["LEERIE_SELF_CMD"] = str(stub)
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )
    result.stub_log = _log.read_text() if _log and _log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# --status <group-id> across two state dirs
# ---------------------------------------------------------------------------


class TestStatusDispatch:
    """--status <group-id> renders both members from separate state dirs."""

    def test_status_lists_both_members(self, tmp_path: Path) -> None:
        """Both run-ids appear in --status output."""
        _two_member_fixture(tmp_path)
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode == 0, result.stderr
        assert RUN_ID_A in result.stdout
        assert RUN_ID_B in result.stdout

    def test_status_excludes_non_group_run(self, tmp_path: Path) -> None:
        """A run in a third state dir without this group_id is not shown."""
        _two_member_fixture(tmp_path)
        sd_c = tmp_path / ".leerie" / "repo-c"
        _write_run(sd_c, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "branch": f"leerie/runs/{NON_GROUP_RUN}",
        })
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode == 0, result.stderr
        assert NON_GROUP_RUN not in result.stdout

    def test_status_unknown_group_id_errors(self, tmp_path: Path) -> None:
        """UUID with no matching group members → non-zero exit."""
        state_dir = tmp_path / ".leerie" / "myrepo"
        state_dir.mkdir(parents=True)
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode != 0

    def test_status_shows_member_count(self, tmp_path: Path) -> None:
        """Output mentions the group has 2 members."""
        _two_member_fixture(tmp_path)
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode == 0, result.stderr
        out = result.stdout + result.stderr
        assert "2" in out


# ---------------------------------------------------------------------------
# --list --groups across two state dirs
# ---------------------------------------------------------------------------


class TestListGroupsDispatch:
    """--list --groups groups members across all per-repo state dirs."""

    def test_list_groups_shows_group_id(self, tmp_path: Path) -> None:
        """--list --groups renders the shared group_id."""
        _two_member_fixture(tmp_path)
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0, result.stderr
        assert GROUP_ID in result.stdout

    def test_list_groups_shows_member_count(self, tmp_path: Path) -> None:
        """Two-member group → 2 appears in the output row."""
        _two_member_fixture(tmp_path)
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0, result.stderr
        assert "2" in result.stdout

    def test_list_groups_excludes_non_group_run(self, tmp_path: Path) -> None:
        """Runs without group_id are excluded from --list --groups."""
        _two_member_fixture(tmp_path)
        sd_c = tmp_path / ".leerie" / "repo-c"
        _write_run(sd_c, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "branch": f"leerie/runs/{NON_GROUP_RUN}",
        })
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0, result.stderr
        assert NON_GROUP_RUN not in result.stdout

    def test_list_groups_empty_is_friendly(self, tmp_path: Path) -> None:
        """No group_id-tagged runs → friendly empty message."""
        sd = tmp_path / ".leerie" / "myrepo"
        _write_run(sd, NON_GROUP_RUN, {"run_id": NON_GROUP_RUN})
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0
        assert "no groups" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# --resume <group-id> eligibility filtering
# ---------------------------------------------------------------------------


class TestResumeDispatch:
    """--resume <group-id> dispatches single-run --resume for paused members only."""

    def test_resume_dispatches_paused_member_only(self, tmp_path: Path) -> None:
        """Only the paused member (A) gets --resume; pushed member (B) does not."""
        _two_member_fixture(tmp_path)
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--resume {RUN_ID_A}" in result.stub_log
        assert f"--resume {RUN_ID_B}" not in result.stub_log

    def test_resume_skips_non_group_run(self, tmp_path: Path) -> None:
        """Non-group paused run in an unrelated state dir is not resumed."""
        _two_member_fixture(tmp_path)
        sd_c = tmp_path / ".leerie" / "repo-c"
        _write_run(sd_c, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "branch": f"leerie/runs/{NON_GROUP_RUN}",
            "fly_machine_id": NON_GROUP_RUN,
            "paused_at": "2026-07-01T09:00:00Z",
            "pause_reason": "worker-error",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert NON_GROUP_RUN not in result.stub_log

    def test_resume_no_paused_runs_exits_ok(self, tmp_path: Path) -> None:
        """When all group members are pushed (none paused), exit 0 with message."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "pushed_at": "2026-07-01T10:00:00Z",
            "fly_machine_id": RUN_ID_A,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0
        assert "no resumable" in (result.stdout + result.stderr).lower()

    def test_resume_already_killed_member_skipped(self, tmp_path: Path) -> None:
        """A killed member (killed_at set) is not eligible for resume."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "paused_at": "2026-07-01T10:00:00Z",
            "killed_at": "2026-07-01T10:30:00Z",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0
        assert f"--resume {RUN_ID_A}" not in result.stub_log


# ---------------------------------------------------------------------------
# --finalize <group-id> eligibility filtering
# ---------------------------------------------------------------------------


class TestFinalizeDispatch:
    """--finalize <group-id> dispatches single-run --finalize for unpushed members."""

    def test_finalize_dispatches_unpushed_member_only(self, tmp_path: Path) -> None:
        """Only the unpushed member (A) gets --finalize; pushed member (B) does not."""
        _two_member_fixture(tmp_path)
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--finalize {RUN_ID_A}" in result.stub_log
        assert f"--finalize {RUN_ID_B}" not in result.stub_log

    def test_finalize_skips_killed_member(self, tmp_path: Path) -> None:
        """A killed member (killed_at set) is not finalized."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "killed_at": "2026-07-01T10:30:00Z",
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--finalize {RUN_ID_A}" not in result.stub_log
        assert f"--finalize {RUN_ID_B}" in result.stub_log

    def test_finalize_all_pushed_exits_ok(self, tmp_path: Path) -> None:
        """When all members are already pushed, exit 0 cleanly."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "pushed_at": "2026-07-01T10:00:00Z",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# --kill <group-id> eligibility filtering
# ---------------------------------------------------------------------------


class TestKillDispatch:
    """--kill <group-id> dispatches single-run --kill for live (fly) members."""

    def test_kill_dispatches_fly_member_only(self, tmp_path: Path) -> None:
        """Only fly member (A, has fly_machine_id) gets --kill; local member (B) does not."""
        _two_member_fixture(tmp_path)
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" in result.stub_log
        # Member B has no fly_machine_id → not killed.
        assert f"--kill {RUN_ID_B}" not in result.stub_log

    def test_kill_skips_already_killed_member(self, tmp_path: Path) -> None:
        """A member with killed_at set is not killed again."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "killed_at": "2026-07-01T10:00:00Z",
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_B,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" not in result.stub_log
        assert f"--kill {RUN_ID_B}" in result.stub_log

    def test_kill_no_live_runs_exits_ok(self, tmp_path: Path) -> None:
        """No live runs → exit 0 with friendly message."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "killed_at": "2026-07-01T10:00:00Z",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0
        assert "no live runs found" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# --stop <group-id> eligibility filtering
# ---------------------------------------------------------------------------


class TestStopDispatch:
    """--stop <group-id> dispatches single-run --stop for running fly members.

    'Running' means: fly_machine_id set, no pushed_at, no killed_at, no paused_at.
    The two-member fixture has A as paused (not running) and B as pushed (not running),
    so stop needs its own fixture.
    """

    def _running_fixture(self, tmp_path: Path) -> tuple[str, str]:
        """Two group members: A is running (fly, no terminal state), B is paused."""
        run_a = "dddd000000001a"
        run_b = "eeee000000002b"
        sd_a = tmp_path / ".leerie" / "repo-stop-a"
        sd_b = tmp_path / ".leerie" / "repo-stop-b"
        _write_run(sd_a, run_a, {
            "run_id": run_a,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{run_a}",
            "fly_machine_id": run_a,
            # No pushed_at/paused_at/killed_at → running.
        })
        _write_run(sd_b, run_b, {
            "run_id": run_b,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{run_b}",
            "fly_machine_id": run_b,
            "paused_at": "2026-07-01T10:00:00Z",
            "pause_reason": "worker-error",
        })
        return run_a, run_b

    def test_stop_dispatches_running_member_only(self, tmp_path: Path) -> None:
        """--stop <group-id> stops only the running member, not the paused one."""
        run_a, run_b = self._running_fixture(tmp_path)
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--stop", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--stop {run_a}" in result.stub_log
        assert f"--stop {run_b}" not in result.stub_log

    def test_stop_skips_pushed_member(self, tmp_path: Path) -> None:
        """A pushed member is not stopped (already done)."""
        run_a = "ffff000000003c"
        run_b = "1111000000004d"
        sd_a = tmp_path / ".leerie" / "repo-stop-c"
        sd_b = tmp_path / ".leerie" / "repo-stop-d"
        _write_run(sd_a, run_a, {
            "run_id": run_a,
            "group_id": GROUP_ID,
            "fly_machine_id": run_a,
            # Running.
        })
        _write_run(sd_b, run_b, {
            "run_id": run_b,
            "group_id": GROUP_ID,
            "fly_machine_id": run_b,
            "pushed_at": "2026-07-01T10:00:00Z",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--stop", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--stop {run_a}" in result.stub_log
        assert f"--stop {run_b}" not in result.stub_log

    def test_stop_no_running_runs_exits_ok(self, tmp_path: Path) -> None:
        """All members paused/pushed → exit 0 cleanly."""
        sd_a = tmp_path / ".leerie" / "repo-stop-e"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "paused_at": "2026-07-01T10:00:00Z",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--stop", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0

    def test_stop_skips_local_member_no_fly_machine_id(self, tmp_path: Path) -> None:
        """A local member (no fly_machine_id) is never stopped (stop is Fly-only)."""
        sd_a = tmp_path / ".leerie" / "repo-stop-f"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            # No fly_machine_id.
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--stop", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0
        assert f"--stop {RUN_ID_A}" not in result.stub_log


# ---------------------------------------------------------------------------
# Dual-purpose-verb handling: chain→group fallback
# ---------------------------------------------------------------------------


class TestDualPurposeVerbFallback:
    """Verbs first try _chain_runs_filter; if empty, fall through to _group_runs_filter.

    A UUID that is a group_id (not a chain_id) must be dispatched correctly via
    the group fallback. A UUID that is a chain_id must NOT trigger group fallback.
    """

    def test_kill_group_id_not_chain_id_dispatches(self, tmp_path: Path) -> None:
        """A group_id-only UUID (no chain_id runs) is dispatched via group fallback."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" in result.stub_log

    def test_resume_group_id_not_chain_id_dispatches(self, tmp_path: Path) -> None:
        """A group_id-only UUID is dispatched for --resume via group fallback."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "paused_at": "2026-07-01T10:00:00Z",
            "pause_reason": "worker-error",
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--resume {RUN_ID_A}" in result.stub_log

    def test_chain_id_still_dispatches_when_no_group_runs(self, tmp_path: Path) -> None:
        """A chain_id dispatches via the chain path; group fallback is not tried."""
        state_dir = tmp_path / ".leerie" / "myrepo"
        _write_run(state_dir, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "chain_id": CHAIN_ID_ONLY,
            "wave_idx": 0,
            "fly_machine_id": RUN_ID_A,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", CHAIN_ID_ONLY], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" in result.stub_log

    def test_finalize_group_id_not_chain_id_dispatches(self, tmp_path: Path) -> None:
        """A group_id-only UUID is dispatched for --finalize via group fallback."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            # No pushed_at → unpushed.
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0, result.stderr
        assert f"--finalize {RUN_ID_A}" in result.stub_log

    def test_non_group_run_not_dispatched_by_group_id(self, tmp_path: Path) -> None:
        """A run without group_id is never dispatched by a group verb."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "fly_machine_id": NON_GROUP_RUN,
        })
        stub, log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=log)
        assert result.returncode == 0
        assert NON_GROUP_RUN not in result.stub_log
