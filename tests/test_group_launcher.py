"""Tests for the launcher's --group arm and group-scoped ID-dispatched verbs.

--group fans out one child per member repo, each cd'd into the right repo
with --group-id <uuid>, --inspect-dir for all siblings, and an optional
shared brief prepended. Group-scoped verbs (--status, --resume, --kill,
--stop, --finalize) fall through the chain-id scan and dispatch by matching
group_id across all ~/.leerie/*/ state dirs.

Test layout mirrors tests/test_chain_launcher_id_dispatch.py: bash
subprocess with LEERIE_SELF_CMD stub to record recursive invocations.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

GROUP_ID = "deadbeef-1234-4abc-8def-0123456789ab"
RUN_ID_A = "aaaaaa000001aa"
RUN_ID_B = "bbbbbb000002bb"
NON_GROUP_RUN = "cccccc000003cc"

UUID_RE = re.compile(
    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_git_repo(path: Path) -> None:
    """Initialise a bare-minimum git repo at *path*."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(path)], capture_output=True, check=True)
    subprocess.run(
        ["git", "-C", str(path), "commit", "--allow-empty", "-m", "init"],
        env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
             "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"},
        capture_output=True, check=True,
    )


def _write_run(state_dir: Path, run_id: str, fields: dict) -> None:
    rd = state_dir / "runs" / run_id
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(json.dumps(fields))


def _stub_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Stub binary that records its argv to a log and exits 0."""
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _stub_group_self_cmd(tmp_path: Path) -> tuple[Path, Path]:
    """Stub for fan-out: records argv, creates a minimal run.json in the
    correct per-basename state dir so tag-back can find a run to update."""
    log = tmp_path / "stub-self.log"
    stub = tmp_path / "self-stub"
    stub.write_text(textwrap.dedent(f"""\
        #!/usr/bin/env bash
        echo "$@" >> "{log}"
        _bn="$(basename "$(pwd)")"
        _sd="{tmp_path}/.leerie/$_bn"
        mkdir -p "$_sd/runs/stub-run-$_bn"
        printf '{{"run_id":"stub-run-%s","finished_at":"2026-07-01T00:00:00Z","branch":"leerie/runs/stub-run-%s"}}' \\
          "$_bn" "$_bn" >"$_sd/runs/stub-run-$_bn/run.json"
        exit 0
        """))
    stub.chmod(0o755)
    return stub, log


def _run(
    tmp_path: Path,
    args: list[str],
    env_extra: dict | None = None,
    stub: Path | None = None,
    stub_log: Path | None = None,
    use_self_stub: bool = False,
    cwd: Path | None = None,
) -> subprocess.CompletedProcess:
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
    if stub:
        env["LEERIE_SELF_CMD"] = str(stub)
    elif use_self_stub:
        _stub_path, _stub_log_path = _stub_self_cmd(tmp_path)
        env["LEERIE_SELF_CMD"] = str(_stub_path)
        stub_log = _stub_log_path
    result = subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
        cwd=str(cwd) if cwd else None,
    )
    result.stub_log = stub_log.read_text() if stub_log and stub_log.exists() else ""
    return result


# ---------------------------------------------------------------------------
# _group_runs_filter via --list --groups
# ---------------------------------------------------------------------------


class TestGroupRunsFilter:
    """Exercise _group_runs_filter indirectly via --list --groups."""

    def test_groups_by_group_id(self, tmp_path: Path) -> None:
        """--list --groups collects run.json files across state dirs and
        groups them by group_id, rendering one row per group."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{RUN_ID_A}",
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{RUN_ID_B}",
        })
        # Unrelated run: different state dir, no group_id.
        sd_c = tmp_path / ".leerie" / "repo-c"
        _write_run(sd_c, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "branch": f"leerie/runs/{NON_GROUP_RUN}",
        })

        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0, result.stderr
        assert GROUP_ID in result.stdout
        # The non-grouped run must NOT appear under this group.
        assert NON_GROUP_RUN not in result.stdout
        # Should show 2 members.
        assert "2" in result.stdout

    def test_groups_empty(self, tmp_path: Path) -> None:
        """No group_id-tagged runs → friendly empty message."""
        sd = tmp_path / ".leerie" / "myrepo"
        _write_run(sd, NON_GROUP_RUN, {"run_id": NON_GROUP_RUN})
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0
        assert "no groups" in (result.stdout + result.stderr).lower()

    def test_cross_dir_discovery(self, tmp_path: Path) -> None:
        """Members in distinct ~/.leerie/<basename>/ dirs are discovered."""
        run_ids = [f"r{i:014d}" for i in range(3)]
        basenames = ["alpha", "beta", "gamma"]
        for basename, rid in zip(basenames, run_ids):
            sd = tmp_path / ".leerie" / basename
            _write_run(sd, rid, {"run_id": rid, "group_id": GROUP_ID,
                                 "branch": f"leerie/runs/{rid}"})
        result = _run(tmp_path, ["--list", "--groups"])
        assert result.returncode == 0, result.stderr
        assert GROUP_ID in result.stdout
        assert "3" in result.stdout


# ---------------------------------------------------------------------------
# State-dir guard
# ---------------------------------------------------------------------------


class TestStateDirGuard:
    """--group must reject LEERIE_STATE_DIR env and --state-dir arg."""

    def test_rejects_state_dir_env(self, tmp_path: Path) -> None:
        """LEERIE_STATE_DIR in env is forbidden inside --group."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_self_cmd(tmp_path)
        # _run() sets LEERIE_STATE_DIR by default — that's what we test.
        result = _run(
            tmp_path,
            ["--group", "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "LEERIE_STATE_DIR" in out or "state-dir" in out.lower()
        # No child must have been spawned.
        assert result.stub_log == ""

    def test_rejects_state_dir_arg(self, tmp_path: Path) -> None:
        """--state-dir CLI arg is forbidden inside --group."""
        repo_a = tmp_path / "repo-a"
        _make_git_repo(repo_a)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group", "--state-dir", "/tmp/custom",
             "--repo", str(repo_a), "task"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode != 0
        out = result.stdout + result.stderr
        assert "--state-dir" in out or "state-dir" in out.lower()
        assert result.stub_log == ""


# ---------------------------------------------------------------------------
# --group fan-out args
# ---------------------------------------------------------------------------


class TestGroupFanOut:
    """--group passes correct flags to each member child."""

    def test_each_member_gets_group_id(self, tmp_path: Path) -> None:
        """Each member child receives --group-id <uuid>."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--group-id", GROUP_ID,
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        log = result.stub_log
        assert f"--group-id {GROUP_ID}" in log
        assert log.count(f"--group-id {GROUP_ID}") >= 2

    def test_each_member_gets_inspect_dir_for_siblings(self, tmp_path: Path) -> None:
        """Each member child receives --inspect-dir for all sibling repos."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        repo_c = tmp_path / "repo-c"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        _make_git_repo(repo_c)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--group-id", GROUP_ID,
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b",
             "--repo", str(repo_c), "task c"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        log = result.stub_log
        # 3 members × 2 siblings each = 6 --inspect-dir appearances.
        assert log.count("--inspect-dir") >= 6

    def test_group_id_minted_when_not_provided(self, tmp_path: Path) -> None:
        """Without --group-id, a fresh UUID is minted and passed to each child."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        log = result.stub_log
        uuids = UUID_RE.findall(log)
        assert len(uuids) >= 2

    def test_brief_content_prepended(self, tmp_path: Path) -> None:
        """--brief <file> content is prepended to every member's prompt."""
        repo_a = tmp_path / "repo-a"
        repo_b = tmp_path / "repo-b"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        brief = tmp_path / "brief.md"
        brief.write_text("shared context line\n")
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--group-id", GROUP_ID,
             "--brief", str(brief),
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        log = result.stub_log
        assert log.count("shared context line") >= 2

    def test_member_repos_use_distinct_state_dirs(self, tmp_path: Path) -> None:
        """Two members in repos with different basenames land in different
        ~/.leerie/<basename>/ state dirs (inferred by basename, not pinned)."""
        repo_a = tmp_path / "alpha-repo"
        repo_b = tmp_path / "beta-repo"
        _make_git_repo(repo_a)
        _make_git_repo(repo_b)
        stub, stub_log = _stub_group_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--group-id", GROUP_ID,
             "--repo", str(repo_a), "task a",
             "--repo", str(repo_b), "task b"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode == 0, result.stderr
        assert (tmp_path / ".leerie" / "alpha-repo").is_dir()
        assert (tmp_path / ".leerie" / "beta-repo").is_dir()

    def test_invalid_repo_path_rejected(self, tmp_path: Path) -> None:
        """A --repo path that is not a git repo causes a fast failure."""
        not_a_repo = tmp_path / "not-a-git-repo"
        not_a_repo.mkdir()
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(
            tmp_path,
            ["--group",
             "--group-id", GROUP_ID,
             "--repo", str(not_a_repo), "task"],
            env_extra={"LEERIE_STATE_DIR": ""},
            stub=stub, stub_log=stub_log,
        )
        assert result.returncode != 0
        assert result.stub_log == ""


# ---------------------------------------------------------------------------
# Group-scoped --status UUID dispatch
# ---------------------------------------------------------------------------


class TestGroupStatusDispatch:
    """--status <group-id> falls through chain scan and renders group members."""

    def _fixture(self, tmp_path: Path) -> None:
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{RUN_ID_A}",
            "pushed_at": "2026-07-01T10:00:00Z",
            "pr_url": "https://github.com/x/a/pull/1",
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "branch": f"leerie/runs/{RUN_ID_B}",
            "paused_at": "2026-07-01T10:00:00Z",
            "pause_reason": "worker-error",
            "fly_machine_id": RUN_ID_B,
        })

    def test_status_group_id_lists_members(self, tmp_path: Path) -> None:
        """--status <group-id> enumerates members across state dirs."""
        self._fixture(tmp_path)
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode == 0, result.stderr
        assert RUN_ID_A in result.stdout
        assert RUN_ID_B in result.stdout

    def test_status_group_id_excludes_non_members(self, tmp_path: Path) -> None:
        """--status <group-id> does not include unrelated runs."""
        self._fixture(tmp_path)
        sd_c = tmp_path / ".leerie" / "repo-c"
        _write_run(sd_c, NON_GROUP_RUN, {
            "run_id": NON_GROUP_RUN,
            "branch": f"leerie/runs/{NON_GROUP_RUN}",
        })
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode == 0, result.stderr
        assert NON_GROUP_RUN not in result.stdout

    def test_status_unknown_uuid_errors(self, tmp_path: Path) -> None:
        """Unknown UUID → non-zero exit."""
        state_dir = tmp_path / ".leerie" / "myrepo"
        state_dir.mkdir(parents=True)
        result = _run(tmp_path, ["--status", GROUP_ID])
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# Group-scoped --resume UUID dispatch
# ---------------------------------------------------------------------------


class TestGroupResumeDispatch:
    """--resume <group-id> falls through chain scan and dispatches paused members."""

    def _fixture(self, tmp_path: Path) -> None:
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "paused_at": "2026-07-01T10:00:00Z",
            "pause_reason": "worker-error",
            "fly_machine_id": RUN_ID_A,
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "pushed_at": "2026-07-01T10:00:00Z",
            "fly_machine_id": RUN_ID_B,
        })

    def test_resume_group_id_dispatches_paused_only(self, tmp_path: Path) -> None:
        """--resume <group-id> resumes only the paused member."""
        self._fixture(tmp_path)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0, result.stderr
        assert f"--resume {RUN_ID_A}" in result.stub_log
        assert f"--resume {RUN_ID_B}" not in result.stub_log

    def test_resume_group_id_no_paused_runs_ok(self, tmp_path: Path) -> None:
        """No paused runs → exit 0 with friendly message."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "pushed_at": "2026-07-01T10:00:00Z",
            "fly_machine_id": RUN_ID_A,
        })
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0
        assert "no resumable" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# Group-scoped --kill UUID dispatch
# ---------------------------------------------------------------------------


class TestGroupKillDispatch:
    """--kill <group-id> falls through chain scan and kills live members."""

    def _fixture(self, tmp_path: Path) -> None:
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_B,
            "killed_at": "2026-07-01T10:00:00Z",
        })

    def test_kill_group_id_dispatches_live_runs(self, tmp_path: Path) -> None:
        """--kill <group-id> kills only live (not already-killed) members."""
        self._fixture(tmp_path)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" in result.stub_log
        assert f"--kill {RUN_ID_B}" not in result.stub_log

    def test_kill_group_id_no_live_runs_ok(self, tmp_path: Path) -> None:
        """No live runs → exit 0 with friendly message."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "killed_at": "2026-07-01T10:00:00Z",
        })
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0
        assert "no live runs found" in (result.stdout + result.stderr).lower()


# ---------------------------------------------------------------------------
# Group-scoped --finalize UUID dispatch
# ---------------------------------------------------------------------------


class TestGroupFinalizeDispatch:
    """--finalize <group-id> falls through chain scan and finalizes unpushed members."""

    def _fixture(self, tmp_path: Path) -> None:
        sd_a = tmp_path / ".leerie" / "repo-a"
        sd_b = tmp_path / ".leerie" / "repo-b"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            # No pushed_at → unpushed.
        })
        _write_run(sd_b, RUN_ID_B, {
            "run_id": RUN_ID_B,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_B,
            "pushed_at": "2026-07-01T10:00:00Z",
            "pr_url": "https://github.com/x/b/pull/2",
        })

    def test_finalize_group_id_dispatches_unpushed_runs(self, tmp_path: Path) -> None:
        """--finalize <group-id> finalizes only the unpushed member."""
        self._fixture(tmp_path)
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0, result.stderr
        assert f"--finalize {RUN_ID_A}" in result.stub_log
        assert f"--finalize {RUN_ID_B}" not in result.stub_log

    def test_finalize_group_id_no_pending_runs_ok(self, tmp_path: Path) -> None:
        """All runs already pushed → exit 0."""
        sd_a = tmp_path / ".leerie" / "repo-a"
        _write_run(sd_a, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "group_id": GROUP_ID,
            "fly_machine_id": RUN_ID_A,
            "pushed_at": "2026-07-01T10:00:00Z",
        })
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--finalize", GROUP_ID], stub=stub, stub_log=stub_log)
        assert result.returncode == 0


# ---------------------------------------------------------------------------
# Regression: chain verbs still work when group fallback finds nothing
# ---------------------------------------------------------------------------


CHAIN_ID_OTHER = "ffffffff-ffff-4fff-bfff-ffffffffffff"


class TestChainRegressions:
    """Existing chain-verb behavior must not be broken by group fallback."""

    def test_kill_chain_id_still_works(self, tmp_path: Path) -> None:
        """--kill <chain-id> still dispatches normally."""
        state_dir = tmp_path / ".leerie" / "myrepo"
        _write_run(state_dir, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "chain_id": CHAIN_ID_OTHER,
            "wave_idx": 0,
            "fly_machine_id": RUN_ID_A,
        })
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--kill", CHAIN_ID_OTHER],
                      stub=stub, stub_log=stub_log)
        assert result.returncode == 0, result.stderr
        assert f"--kill {RUN_ID_A}" in result.stub_log

    def test_resume_chain_id_still_works(self, tmp_path: Path) -> None:
        """--resume <chain-id> still dispatches paused chain runs normally."""
        state_dir = tmp_path / ".leerie" / "myrepo"
        _write_run(state_dir, RUN_ID_A, {
            "run_id": RUN_ID_A,
            "chain_id": CHAIN_ID_OTHER,
            "wave_idx": 0,
            "paused_at": "2026-07-01T10:00:00Z",
            "pause_reason": "worker-error",
            "fly_machine_id": RUN_ID_A,
        })
        stub, stub_log = _stub_self_cmd(tmp_path)
        result = _run(tmp_path, ["--resume", CHAIN_ID_OTHER],
                      stub=stub, stub_log=stub_log)
        assert result.returncode == 0, result.stderr
        assert f"--resume {RUN_ID_A}" in result.stub_log
