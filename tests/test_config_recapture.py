"""Tests for the `leerie config --recapture` LLM-seam dispatch and
multi-run consolidation (DESIGN §6½).

Coverage:
  (a) Launcher dispatch — `--recapture` arm dispatches to the python3 seam;
      exits 1 when no runs dir or no finished run; `--force` passed correctly;
      python3 failure exits 1; no nerdctl invoked; arm within parity-guard
      extraction boundary.
  (b) `run_recapture_deps` consolidation — all finished runs with logs/ are
      processed (not just the newest); without `--force`, already-captured
      runs (sentinel present) are skipped; with `--force`, sentinel dropped
      on each run so the worker re-fires; no-runs-dir and no-finished-run
      exit 1.
  (c) End-to-end with stubbed claude — real python3 seam over a ≥2-run
      fixture writes merged deps; Dockerfile survivors (committed, generated).

Strategy: launcher dispatch tests use the extract-from-launcher harness
(same pattern as test_config_verb.py).  Python-level tests patch
capture_repo_deps directly and assert call-site coverage.  End-to-end tests
stub `claude` to emit a fixed dep_capture stream result.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
# The directory of the running interpreter, so the real seam can import
# tenacity/other runtime deps when invoking the orchestrator.
_PYBIN = str(Path(sys.executable).parent)


# ---------------------------------------------------------------------------
# Shared: extract the real `config)` arm from the launcher
# ---------------------------------------------------------------------------

def _extract_config_arm() -> str:
    """Return the real `config)` case-arm body verbatim from the launcher."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    start_marker = "  config)\n"
    end_marker = "\n  --list)"
    s = launcher_text.index(start_marker)
    e = launcher_text.index(end_marker, s)
    return launcher_text[s:e]


def _run_real_config_arm_with_state(
    user_repo: Path,
    args: list[str],
    tmp_path: Path,
    state_dir: Path | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the real launcher `config)` arm (extracted verbatim) with
    optional LEERIE_STATE_HOST_DIR injection and PATH override."""
    block = _extract_config_arm()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'remote_log() { echo "[leerie] $*" >&2; }\n'
        "claude() {\n"
        "  printf '%s\\n' \"$*\" >> \"${CLAUDE_LOG:-/dev/null}\"\n"
        "  return 0\n"
        "}\n"
        "export -f claude\n"
        "\n"
        'case "${1:-}" in\n'
        f"{block}\n"
        "esac\n"
    )
    env: dict[str, str] = {
        # _PYBIN first so the --recapture python3 seam resolves to the
        # interpreter that has runtime deps (tenacity), matching the sibling
        # seam tests; system python3 may lack them.
        "PATH": f"{_PYBIN}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path / "home"),
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "CLAUDE_LOG": str(tmp_path / "claude.log"),
    }
    if state_dir is not None:
        env["LEERIE_STATE_HOST_DIR"] = str(state_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", script, "--", "config"] + args,
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Launcher dispatch tests (bash-harness, stubbed python3)
# ---------------------------------------------------------------------------

class TestRecaptureDispatch:
    """Launcher --recapture arm: dispatch to python3 seam and error paths."""

    def test_no_runs_dir_exits_1(self, tmp_path):
        """--recapture exits 1 with a diagnostic when no runs directory exists."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        state_dir.mkdir()
        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "no runs directory" in combined.lower()

    def test_no_finished_run_exits_1(self, tmp_path):
        """--recapture exits 1 when there are runs but none are finished."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-abc"
        runs_dir.mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"started_at": "2026-01-01T00:00:00"}')
        (runs_dir / "logs").mkdir()
        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir
        )
        assert result.returncode == 1
        combined = result.stdout + result.stderr
        assert "no completed run" in combined.lower()

    def test_dispatches_to_python3_seam(self, tmp_path):
        """--recapture dispatches to the python3 seam when a finished run exists."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-abc"
        (runs_dir / "logs").mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')

        python3_stub = tmp_path / "python3"
        python3_stub.write_text("#!/bin/sh\necho 'python3-stub: recapture OK'\nexit 0\n")
        python3_stub.chmod(0o755)

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "python3-stub" in result.stdout or "recapture" in result.stderr.lower()

    def test_force_flag_passed_to_python3_seam(self, tmp_path):
        """--recapture --force passes 'true' as the force argument to the seam."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-abc"
        (runs_dir / "logs").mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')

        python3_stub = tmp_path / "python3"
        python3_stub.write_text("#!/bin/sh\necho \"python3 args: $*\"\nexit 0\n")
        python3_stub.chmod(0o755)

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture", "--force"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert "true" in result.stdout

    def test_python3_failure_exits_1(self, tmp_path):
        """--recapture exits 1 when the python3 seam fails."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-abc"
        (runs_dir / "logs").mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')

        python3_stub = tmp_path / "python3"
        python3_stub.write_text("#!/bin/sh\nexit 1\n")
        python3_stub.chmod(0o755)

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 1
        assert "failed" in result.stderr.lower() or "python3 seam" in result.stderr.lower()

    def test_no_nerdctl_invoked(self, tmp_path):
        """--recapture must NOT invoke nerdctl (host-side only)."""
        user_repo = tmp_path / "repo"
        user_repo.mkdir()
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-abc"
        (runs_dir / "logs").mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')

        nerdctl_log = tmp_path / "nerdctl.log"
        python3_stub = tmp_path / "python3"
        python3_stub.write_text("#!/bin/sh\necho ok\nexit 0\n")
        python3_stub.chmod(0o755)
        nerdctl_stub = tmp_path / "nerdctl"
        nerdctl_stub.write_text(
            f"#!/bin/sh\necho \"nerdctl $*\" >> {nerdctl_log}\nexit 0\n"
        )
        nerdctl_stub.chmod(0o755)

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert not nerdctl_log.exists() or nerdctl_log.read_text() == "", (
            "config --recapture must not invoke nerdctl"
        )

    def test_recapture_arm_within_parity_guard_boundaries(self):
        """--recapture arm must be inside the config) ... --list) boundary."""
        arm = _extract_config_arm()
        assert "--recapture" in arm, (
            "--recapture arm is missing from the config) extraction boundary; "
            "it must be placed between 'config)' and '--list)' in the launcher"
        )


# ---------------------------------------------------------------------------
# Shared Python-level test infrastructure
# ---------------------------------------------------------------------------

def _make_finished_run(state_dir: Path, run_name: str,
                       finished_at: str = "2026-01-01T12:00:00",
                       commands: list[str] | None = None,
                       with_sentinel: bool = False) -> Path:
    """Create a finished run with logs/ dir, optional worker commands."""
    run_dir = state_dir / "runs" / run_name
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"finished_at": finished_at}))
    if commands:
        lines = []
        for i, cmd in enumerate(commands):
            event = {
                "message": {
                    "content": [{
                        "type": "tool_use",
                        "name": "Bash",
                        "id": f"t-{i}",
                        "input": {"command": cmd},
                    }]
                }
            }
            lines.append(json.dumps(event))
        (log_dir / "worker-001.log").write_text("\n".join(lines) + "\n")
    if with_sentinel:
        (run_dir / "dep_capture.done").write_text("1\n")
    return run_dir


# ---------------------------------------------------------------------------
# run_recapture_deps: multi-run consolidation tests
# ---------------------------------------------------------------------------

class TestRunRecaptureMultiRun:
    """run_recapture_deps consolidates across ALL finished runs, not just newest."""

    def test_captures_both_of_two_finished_runs(self, leerie, tmp_path, monkeypatch):
        """With two finished runs, capture_repo_deps is called for both."""
        state_dir = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()

        run_a = _make_finished_run(state_dir, "run-001", "2026-01-01T10:00:00",
                                   commands=["pip install flask"])
        run_b = _make_finished_run(state_dir, "run-002", "2026-01-02T10:00:00",
                                   commands=["pip install django"])

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        captured_run_dirs: list[Path] = []

        async def fake_capture(repo_root, st, **kwargs):
            captured_run_dirs.append(Path(getattr(st, "run_dir", "")))

        with patch.object(leerie, "capture_repo_deps", new=fake_capture):
            with patch.object(leerie, "State", wraps=leerie.State) as mock_state_cls:
                # Bypass State.__init__'s flock.
                def _make_fake_st(leerie_root, run_id, repo_root=None):
                    class _St:
                        pass
                    s = _St()
                    s.run_dir = leerie_root / "runs" / run_id
                    s.data = {}
                    s.bump_workers = lambda c: None
                    s.load = lambda: None
                    return s
                mock_state_cls.side_effect = _make_fake_st

                leerie.run_recapture_deps(state_dir, repo)

        captured = {p.name for p in captured_run_dirs}
        assert run_a.name in captured, f"run-001 not captured; captured={captured}"
        assert run_b.name in captured, f"run-002 not captured; captured={captured}"

    def test_only_newest_run_when_run_id_given(self, leerie, tmp_path, monkeypatch):
        """With explicit run_id, only that run is processed."""
        state_dir = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()

        _make_finished_run(state_dir, "run-001", "2026-01-01T10:00:00",
                           commands=["pip install flask"])
        run_b = _make_finished_run(state_dir, "run-002", "2026-01-02T10:00:00",
                                   commands=["pip install django"])

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        captured_run_dirs: list[Path] = []

        async def fake_capture(repo_root, st, **kwargs):
            captured_run_dirs.append(Path(getattr(st, "run_dir", "")))

        with patch.object(leerie, "capture_repo_deps", new=fake_capture):
            with patch.object(leerie, "State") as mock_state_cls:
                def _make_fake_st(leerie_root, run_id, repo_root=None):
                    class _St:
                        pass
                    s = _St()
                    s.run_dir = leerie_root / "runs" / run_id
                    s.data = {}
                    s.bump_workers = lambda c: None
                    s.load = lambda: None
                    return s
                mock_state_cls.side_effect = _make_fake_st

                leerie.run_recapture_deps(state_dir, repo, run_id="run-002")

        captured = {p.name for p in captured_run_dirs}
        assert "run-002" in captured
        assert "run-001" not in captured, "run-001 must not be captured when run_id targets run-002"

    def test_skips_run_with_sentinel_when_not_force(self, leerie, tmp_path, monkeypatch):
        """Without --force, runs that already have dep_capture.done are skipped."""
        state_dir = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()

        # run-001 already has a sentinel — should be skipped.
        _make_finished_run(state_dir, "run-001", "2026-01-01T10:00:00",
                           commands=["pip install flask"], with_sentinel=True)
        # run-002 has no sentinel — should be captured.
        run_b = _make_finished_run(state_dir, "run-002", "2026-01-02T10:00:00",
                                   commands=["pip install django"])

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        captured_run_dirs: list[Path] = []

        async def fake_capture(repo_root, st, **kwargs):
            captured_run_dirs.append(Path(getattr(st, "run_dir", "")))

        with patch.object(leerie, "capture_repo_deps", new=fake_capture):
            with patch.object(leerie, "State") as mock_state_cls:
                def _make_fake_st(leerie_root, run_id, repo_root=None):
                    class _St:
                        pass
                    s = _St()
                    s.run_dir = leerie_root / "runs" / run_id
                    s.data = {}
                    s.bump_workers = lambda c: None
                    s.load = lambda: None
                    return s
                mock_state_cls.side_effect = _make_fake_st

                leerie.run_recapture_deps(state_dir, repo, force=False)

        captured = {p.name for p in captured_run_dirs}
        assert "run-002" in captured, "run-002 (no sentinel) must be captured"
        assert "run-001" not in captured, "run-001 (sentinel present) must be skipped"

    def test_force_drops_sentinel_and_captures_all_runs(self, leerie, tmp_path, monkeypatch):
        """With --force, sentinel is dropped on each run and both runs are captured."""
        state_dir = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()

        run_a = _make_finished_run(state_dir, "run-001", "2026-01-01T10:00:00",
                                   commands=["pip install flask"], with_sentinel=True)
        run_b = _make_finished_run(state_dir, "run-002", "2026-01-02T10:00:00",
                                   commands=["pip install django"], with_sentinel=True)

        sentinel_a = run_a / "dep_capture.done"
        sentinel_b = run_b / "dep_capture.done"
        assert sentinel_a.exists() and sentinel_b.exists()

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        captured_run_dirs: list[Path] = []

        async def fake_capture(repo_root, st, **kwargs):
            captured_run_dirs.append(Path(getattr(st, "run_dir", "")))

        with patch.object(leerie, "capture_repo_deps", new=fake_capture):
            with patch.object(leerie, "State") as mock_state_cls:
                def _make_fake_st(leerie_root, run_id, repo_root=None):
                    class _St:
                        pass
                    s = _St()
                    s.run_dir = leerie_root / "runs" / run_id
                    s.data = {}
                    s.bump_workers = lambda c: None
                    s.load = lambda: None
                    return s
                mock_state_cls.side_effect = _make_fake_st

                leerie.run_recapture_deps(state_dir, repo, force=True)

        captured = {p.name for p in captured_run_dirs}
        assert "run-001" in captured, "run-001 must be captured (force drops sentinel)"
        assert "run-002" in captured, "run-002 must be captured (force drops sentinel)"
        # Sentinel should have been deleted before capture.
        assert not sentinel_a.exists(), "sentinel for run-001 should have been dropped by force"
        assert not sentinel_b.exists(), "sentinel for run-002 should have been dropped by force"

    def test_exits_1_when_no_runs_dir(self, leerie, tmp_path):
        """run_recapture_deps exits 1 when the runs directory does not exist."""
        state_dir = tmp_path / "nonexistent-state"
        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            leerie.run_recapture_deps(state_dir, repo)
        assert exc_info.value.code == 1

    def test_exits_1_when_no_finished_run(self, leerie, tmp_path):
        """run_recapture_deps exits 1 when no run has finished_at + logs/."""
        state_dir = tmp_path / "state"
        runs_dir = state_dir / "runs" / "run-incomplete"
        (runs_dir / "logs").mkdir(parents=True)
        (runs_dir / "run.json").write_text('{"started_at": "2026-01-01T00:00:00"}')

        repo = tmp_path / "repo"
        repo.mkdir()

        with pytest.raises(SystemExit) as exc_info:
            leerie.run_recapture_deps(state_dir, repo)
        assert exc_info.value.code == 1

    def test_three_finished_runs_all_captured(self, leerie, tmp_path, monkeypatch):
        """All three finished runs are processed in a three-run fixture."""
        state_dir = tmp_path / "state"
        repo = tmp_path / "repo"
        repo.mkdir()

        runs = [
            _make_finished_run(state_dir, f"run-00{i}",
                               f"2026-01-0{i+1}T10:00:00",
                               commands=[f"pip install pkg-{i}"])
            for i in range(3)
        ]

        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        captured_run_dirs: list[Path] = []

        async def fake_capture(repo_root, st, **kwargs):
            captured_run_dirs.append(Path(getattr(st, "run_dir", "")))

        with patch.object(leerie, "capture_repo_deps", new=fake_capture):
            with patch.object(leerie, "State") as mock_state_cls:
                def _make_fake_st(leerie_root, run_id, repo_root=None):
                    class _St:
                        pass
                    s = _St()
                    s.run_dir = leerie_root / "runs" / run_id
                    s.data = {}
                    s.bump_workers = lambda c: None
                    s.load = lambda: None
                    return s
                mock_state_cls.side_effect = _make_fake_st

                leerie.run_recapture_deps(state_dir, repo)

        captured = {p.name for p in captured_run_dirs}
        for run_dir in runs:
            assert run_dir.name in captured, (
                f"{run_dir.name} not captured; captured={captured}"
            )


# ---------------------------------------------------------------------------
# End-to-end: real python3 seam with stubbed claude
# ---------------------------------------------------------------------------

def _make_finished_run_with_apt_log(state_dir: Path, run_name: str,
                                    command: str,
                                    finished_at: str = "2026-01-01T12:00:00") -> None:
    """Create a finished run with a Bash apt-install command in JSONL shape."""
    run_dir = state_dir / "runs" / run_name
    log_dir = run_dir / "logs"
    log_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({"finished_at": finished_at}))
    event = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "id": "t-0",
                 "input": {"command": command}}
            ]
        }
    }
    (log_dir / "worker-001.log").write_text(json.dumps(event) + "\n")


def _make_claude_stub(stub_dir: Path, structured_output: dict) -> None:
    """Write a stub claude that emits a dep_capture stream-json result."""
    payload = json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": structured_output,
    })
    stub = stub_dir / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{payload}'\n"
        "exit 0\n"
    )
    stub.chmod(0o755)


class TestRecaptureEndToEnd:
    """End-to-end --recapture with real python3 seam and stubbed claude."""

    def test_two_finished_runs_invokes_capture(self, tmp_path):
        """--recapture with two finished runs invokes the dep_capture seam."""
        user_repo = tmp_path / "repo"
        leerie_dir = user_repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        (leerie_dir / "config.toml").write_text("")

        state_dir = tmp_path / "state"
        _make_finished_run_with_apt_log(
            state_dir, "run-001", "apt-get install -y postgresql",
            finished_at="2026-01-01T10:00:00"
        )
        _make_finished_run_with_apt_log(
            state_dir, "run-002", "apt-get install -y libpq-dev",
            finished_at="2026-01-02T10:00:00"
        )

        stub_dir = tmp_path / "stubs"
        stub_dir.mkdir()
        _make_claude_stub(stub_dir, {"setup_packages": [], "language_installs": []})

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, (
            f"stderr: {result.stderr}\nstdout: {result.stdout}"
        )

    def test_force_causes_recapture_on_already_captured_run(self, tmp_path):
        """--recapture --force drops the sentinel and re-captures."""
        user_repo = tmp_path / "repo"
        leerie_dir = user_repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        (leerie_dir / "config.toml").write_text("")

        state_dir = tmp_path / "state"
        run_dir = state_dir / "runs" / "run-001"
        _make_finished_run_with_apt_log(state_dir, "run-001", "apt-get install -y git")
        # Pre-place the sentinel so the non-force path would skip it.
        (run_dir / "dep_capture.done").write_text("1\n")

        stub_dir = tmp_path / "stubs"
        stub_dir.mkdir()
        _make_claude_stub(stub_dir, {"setup_packages": ["git"], "language_installs": []})

        # Without --force: run is skipped (sentinel present), exits 0 (no-op).
        result_no_force = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result_no_force.returncode == 0

        # Restore the sentinel (it may have been removed by the seam).
        (run_dir / "dep_capture.done").write_text("1\n")

        # With --force: sentinel dropped, worker fires.
        result_force = _run_real_config_arm_with_state(
            user_repo, ["--recapture", "--force"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result_force.returncode == 0, (
            f"stderr: {result_force.stderr}\nstdout: {result_force.stdout}"
        )
        # git should appear in config.toml.
        content = (leerie_dir / "config.toml").read_text()
        assert "git" in content, "force recapture must write new deps"

    def test_recapture_leaves_generated_dockerfile_intact(self, tmp_path):
        """--recapture must NOT remove an existing generated Dockerfile."""
        user_repo = tmp_path / "repo"
        leerie_dir = user_repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        (leerie_dir / "config.toml").write_text("")
        sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
        (leerie_dir / "Dockerfile").write_text(sentinel + "\nARG BASE_IMAGE\n")

        state_dir = tmp_path / "state"
        _make_finished_run_with_apt_log(state_dir, "run-001", "apt-get install -y git")

        stub_dir = tmp_path / "stubs"
        stub_dir.mkdir()
        _make_claude_stub(stub_dir, {"setup_packages": [], "language_installs": []})

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert (leerie_dir / "Dockerfile").exists(), (
            "recapture must not remove the generated Dockerfile"
        )

    def test_recapture_leaves_committed_dockerfile_intact(self, tmp_path):
        """--recapture must NOT remove or overwrite a committed Dockerfile."""
        user_repo = tmp_path / "repo"
        leerie_dir = user_repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        (leerie_dir / "config.toml").write_text("")
        committed = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN echo hand-written\n"
        (leerie_dir / "Dockerfile").write_text(committed)

        state_dir = tmp_path / "state"
        _make_finished_run_with_apt_log(state_dir, "run-001", "apt-get install -y git")

        stub_dir = tmp_path / "stubs"
        stub_dir.mkdir()
        _make_claude_stub(stub_dir, {"setup_packages": [], "language_installs": []})

        result = _run_real_config_arm_with_state(
            user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
            extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
        )
        assert result.returncode == 0, f"stderr: {result.stderr}"
        assert (leerie_dir / "Dockerfile").read_text() == committed, (
            "committed Dockerfile must survive recapture"
        )


# ---------------------------------------------------------------------------
# Regression: orchestrator module must load on a bare host python3
# ---------------------------------------------------------------------------

class TestBareHostImport:
    """The `config --recapture` host seam exec_module()s orchestrator/leerie.py
    on the host, whose python3 is not guaranteed to have requirements.txt deps
    (CLAUDE.md §0: the launcher needs no host Python). A module-scope third-party
    import there would crash before run_recapture_deps()'s pathlib guards can
    print their diagnostic — the bug these tests pin against."""

    def test_module_imports_without_tenacity(self):
        """orchestrator/leerie.py loads even when tenacity is unavailable."""
        import builtins
        import importlib.util

        real_import = builtins.__import__

        def _blocked_import(name, *args, **kwargs):
            if name == "tenacity" or name.startswith("tenacity."):
                raise ModuleNotFoundError("No module named 'tenacity'")
            return real_import(name, *args, **kwargs)

        orch_path = REPO_ROOT / "orchestrator" / "leerie.py"
        spec = importlib.util.spec_from_file_location("leerie_bare", orch_path)
        module = importlib.util.module_from_spec(spec)
        with patch.object(builtins, "__import__", _blocked_import):
            # Mirrors the launcher seam's load path (leerie:864 exec_module).
            spec.loader.exec_module(module)
        assert hasattr(module, "run_recapture_deps")

    def test_no_module_scope_tenacity_import(self):
        """`from tenacity import` must appear only inside claude_p(), never at
        module scope — guards against a future 'hoist imports to top' cleanup
        reintroducing the crash."""
        import ast

        orch_path = REPO_ROOT / "orchestrator" / "leerie.py"
        tree = ast.parse(orch_path.read_text())
        module_scope_tenacity = [
            node
            for node in tree.body  # top-level statements only
            if isinstance(node, ast.ImportFrom) and node.module == "tenacity"
        ]
        assert not module_scope_tenacity, (
            "tenacity must be imported lazily inside claude_p(), not at module "
            "scope — see the note at the top of orchestrator/leerie.py"
        )
