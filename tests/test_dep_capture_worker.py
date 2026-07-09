"""Tests for the dep_capture worker path inside capture_repo_deps.

Stubs _invoke (not claude_p directly) with a fixed structured_output
envelope, mirroring test_phase_judge.py's _JUDGE_ENVELOPE pattern.

Covers:
  - schema-validated worker output (setup_packages + language_installs)
    written to .leerie/config.toml via _write_config_toml_keys
  - warm repo (all deps already present) → no write (mtime unchanged)
  - LEERIE_CAPTURE_DEPS=0 → opt-out, worker not invoked
  - capture_deps = false in config.toml → opt-out, worker not invoked
  - committed .leerie/Dockerfile → write skipped, worker not invoked
  - missing logs dir → silent no-op (no exception, no write)
  - _write_config_toml_keys raises → exception is catchable (non-fatal guard)
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# Fixed _invoke envelope containing dep_capture structured_output
# ---------------------------------------------------------------------------

_DEP_CAPTURE_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "num_turns": 2,
    "total_cost_usd": 0.002,
    "is_error": False,
    "terminal_reason": "completed",
    "result": "{}",
    "structured_output": {
        "setup_packages": ["postgresql", "libpq-dev"],
        "language_installs": [
            {
                "manager": "pip",
                "command": "pip install -r requirements.txt",
                "copy_inputs": ["requirements.txt"],
            }
        ],
        "dockerfile_notes": None,
    },
    "usage": {"input_tokens": 400, "output_tokens": 100},
}

_CAPS = {
    "worker_timeout_sec": 60,
    "max_total_workers": 200,
    "max_parallel": 4,
    "worker_idle_warn_sec": 30,
}

_MODELS = {"dep_capture": "opus"}
_EFFORTS: dict[str, str | None] = {"dep_capture": None}


# ---------------------------------------------------------------------------
# Fake state compatible with claude_p's expectations
# ---------------------------------------------------------------------------

def _make_state(leerie, run_dir: Path) -> object:
    """Minimal State-alike that satisfies claude_p without network I/O.

    Uses State.__new__ (same pattern as test_phase_judge.py) so leerie's
    own class is used, but __init__'s flock acquisition is bypassed.
    """
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-dep-capture"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {
        "telemetry": {"calls": 0, "cost_usd": 0.0,
                      "input_tokens": 0, "output_tokens": 0},
        "verbosity": "quiet",
        "worker_count": 0,
        "dangerously_skip_permissions": False,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


# ---------------------------------------------------------------------------
# Shared helpers (copied from test_capture_deps.py for independence)
# ---------------------------------------------------------------------------

def _make_jsonl_log_in(log_dir: Path, commands: list[str]) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "worker-001.log"
    lines: list[str] = []
    for i, cmd in enumerate(commands):
        event = {
            "message": {
                "content": [{
                    "type": "tool_use",
                    "name": "Bash",
                    "id": f"tool-{i}",
                    "input": {"command": cmd},
                }]
            }
        }
        lines.append(json.dumps(event))
    log_path.write_text("\n".join(lines) + "\n")


def _make_fake_state(leerie, tmp_path: Path, commands: list[str]) -> object:
    """Return a State-like object whose run_dir/logs contains worker logs."""
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    _make_jsonl_log_in(run_dir / "logs", commands)
    return _make_state(leerie, run_dir)


def _git_init(repo: Path) -> None:
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config",
                    "user.email", "test@test.com"], check=True, capture_output=True)
    subprocess.run(["git", "-C", str(repo), "config",
                    "user.name", "Test"], check=True, capture_output=True)


# ---------------------------------------------------------------------------
# _invoke patch helper (mirrors test_phase_judge.py's _patch_invoke)
# ---------------------------------------------------------------------------

def _patch_invoke(leerie, monkeypatch, envelope: dict = _DEP_CAPTURE_ENVELOPE) -> None:
    """Patch leerie._invoke to return envelope without network I/O."""
    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          progress=None, **_kw):
        return envelope

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)


# ---------------------------------------------------------------------------
# Tests: schema-validated output written to config.toml
# ---------------------------------------------------------------------------

class TestDepCaptureWorkerWritePath:
    """capture_repo_deps with stubbed _invoke writes TOML from structured_output."""

    def test_writes_setup_packages(self, leerie, tmp_path, monkeypatch):
        """setup_packages from worker structured_output are written to config.toml."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(leerie, tmp_path, [
            "sudo -n apt-get install -y postgresql libpq-dev",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        cfg = repo / ".leerie" / "config.toml"
        assert cfg.exists(), "config.toml not written"
        content = cfg.read_text()
        assert "setup_packages" in content
        assert "postgresql" in content

    def test_writes_language_installs(self, leerie, tmp_path, monkeypatch):
        """language_installs from worker structured_output are written to config.toml."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(leerie, tmp_path, ["pip install -r requirements.txt"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        cfg = repo / ".leerie" / "config.toml"
        assert cfg.exists(), "config.toml not written"
        content = cfg.read_text()
        assert "language_installs" in content
        assert "pip" in content

    def test_both_setup_packages_and_language_installs_written(
            self, leerie, tmp_path, monkeypatch):
        """Both keys written in a single pass from the worker envelope."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(leerie, tmp_path, [
            "apt-get install -y postgresql",
            "pip install -r requirements.txt",
        ])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        content = (repo / ".leerie" / "config.toml").read_text()
        assert "setup_packages" in content
        assert "language_installs" in content

    def test_structured_output_shape_matches_dep_capture_schema(
            self, leerie, tmp_path, monkeypatch):
        """The envelope's structured_output satisfies SCHEMAS['dep_capture']."""
        schema = leerie.SCHEMAS["dep_capture"]
        output = _DEP_CAPTURE_ENVELOPE["structured_output"]
        # Required fields must be present.
        for field in schema.get("required", []):
            assert field in output, f"structured_output missing required field: {field}"
        assert isinstance(output["setup_packages"], list)
        assert isinstance(output["language_installs"], list)


# ---------------------------------------------------------------------------
# Tests: warm repo no-op (never-clobber)
# ---------------------------------------------------------------------------

class TestDepCaptureNeverClobber:
    """When all captured deps are already present, no write occurs."""

    def test_no_write_when_setup_packages_already_present(
            self, leerie, tmp_path, monkeypatch):
        """Warm repo: all setup_packages already in config → mtime unchanged."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        # Pre-populate exactly the packages the worker returns.
        cfg.write_text('setup_packages = "postgresql libpq-dev"\n')
        mtime_before = cfg.stat().st_mtime

        # Use a stripped-down envelope: only setup_packages the file already has.
        warm_envelope = dict(_DEP_CAPTURE_ENVELOPE)
        warm_envelope["structured_output"] = {
            "setup_packages": ["postgresql", "libpq-dev"],
            "language_installs": [],
            "dockerfile_notes": None,
        }

        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch, envelope=warm_envelope)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        assert cfg.stat().st_mtime == mtime_before, (
            "config.toml was rewritten on a warm repo (never-clobber violated)")

    def test_new_packages_appended_existing_preserved(
            self, leerie, tmp_path, monkeypatch):
        """Existing packages survive; only genuinely new ones are appended."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('setup_packages = "git curl"\n')

        partial_envelope = dict(_DEP_CAPTURE_ENVELOPE)
        partial_envelope["structured_output"] = {
            "setup_packages": ["git", "wget"],
            "language_installs": [],
            "dockerfile_notes": None,
        }

        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y git wget"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch, envelope=partial_envelope)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        content = cfg.read_text()
        assert "git" in content
        assert "curl" in content
        assert "wget" in content

    def test_language_installs_existing_manager_not_replaced(
            self, leerie, tmp_path, monkeypatch):
        """Existing language_installs manager entry is never overwritten."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        existing = [{"manager": "pip",
                     "command": "pip install -r requirements.txt",
                     "copy_inputs": ["requirements.txt"]}]
        inner_json = json.dumps(existing, separators=(",", ":"))
        cfg = leerie_dir / "config.toml"
        cfg.write_text(f'language_installs = "{inner_json}"\n')

        conflict_envelope = dict(_DEP_CAPTURE_ENVELOPE)
        conflict_envelope["structured_output"] = {
            "setup_packages": [],
            "language_installs": [
                {"manager": "pip", "command": "pip install -e .",
                 "copy_inputs": ["setup.py"]},
            ],
            "dockerfile_notes": None,
        }

        st = _make_fake_state(leerie, tmp_path, ["pip install -e ."])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch, envelope=conflict_envelope)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        content = cfg.read_text()
        # Original pip command must still be there (not replaced).
        assert "requirements.txt" in content


# ---------------------------------------------------------------------------
# Tests: opt-out
# ---------------------------------------------------------------------------

class TestDepCaptureOptOut:
    """LEERIE_CAPTURE_DEPS=0 and capture_deps=false each prevent any write."""

    def test_env_opt_out_prevents_write(self, leerie, tmp_path, monkeypatch):
        """LEERIE_CAPTURE_DEPS=0 skips write; _invoke is never called."""
        repo = tmp_path / "repo"
        repo.mkdir()
        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.setenv("LEERIE_CAPTURE_DEPS", "0")

        invoke_called: list[bool] = []

        async def spy_invoke(*_a, **_kw):
            invoke_called.append(True)
            return _DEP_CAPTURE_ENVELOPE

        monkeypatch.setattr(leerie, "_invoke", spy_invoke)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        assert not invoke_called, "_invoke should not be called when opt-out is set"
        cfg = repo / ".leerie" / "config.toml"
        assert not cfg.exists(), "config.toml must not be written on opt-out"

    def test_config_file_opt_out_prevents_write(self, leerie, tmp_path, monkeypatch):
        """capture_deps = false in .leerie/config.toml skips write; _invoke not called."""
        repo = tmp_path / "repo"
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(parents=True)
        cfg = leerie_dir / "config.toml"
        cfg.write_text('capture_deps = "false"\n')
        content_before = cfg.read_text()

        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        invoke_called: list[bool] = []

        async def spy_invoke(*_a, **_kw):
            invoke_called.append(True)
            return _DEP_CAPTURE_ENVELOPE

        monkeypatch.setattr(leerie, "_invoke", spy_invoke)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        assert not invoke_called, "_invoke called despite capture_deps=false"
        assert cfg.read_text() == content_before, (
            "config.toml was modified despite capture_deps=false opt-out")


# ---------------------------------------------------------------------------
# Tests: committed Dockerfile skips write
# ---------------------------------------------------------------------------

class TestDepCaptureDockerfileGuard:
    """A committed .leerie/Dockerfile prevents the worker from running."""

    def test_committed_dockerfile_skips_write(self, leerie, tmp_path, monkeypatch):
        """If .leerie/Dockerfile is git-tracked, worker is not invoked."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        dockerfile = leerie_dir / "Dockerfile"
        dockerfile.write_text("FROM base\n")

        subprocess.run(["git", "-C", str(repo), "add", ".leerie/Dockerfile"],
                       check=True, capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "add Dockerfile"],
                       check=True, capture_output=True)

        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

        invoke_called: list[bool] = []

        async def spy_invoke(*_a, **_kw):
            invoke_called.append(True)
            return _DEP_CAPTURE_ENVELOPE

        monkeypatch.setattr(leerie, "_invoke", spy_invoke)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        assert not invoke_called, (
            "_invoke should not be called when committed Dockerfile exists")
        cfg = leerie_dir / "config.toml"
        assert not cfg.exists(), (
            "config.toml must not be created when committed Dockerfile is authoritative")

    def test_untracked_dockerfile_does_not_block(self, leerie, tmp_path, monkeypatch):
        """An untracked .leerie/Dockerfile does not prevent the write."""
        repo = tmp_path / "repo"
        repo.mkdir()
        _git_init(repo)

        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir()
        (leerie_dir / "Dockerfile").write_text("FROM base\n")
        # NOT committed — just present on disk.

        st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
        monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
        _patch_invoke(leerie, monkeypatch)

        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))

        cfg = leerie_dir / "config.toml"
        assert cfg.exists(), (
            "config.toml must be written when Dockerfile is only untracked")


# ---------------------------------------------------------------------------
# Tests: missing logs dir → silent no-op
# ---------------------------------------------------------------------------

def test_missing_logs_dir_is_silent_noop(leerie, tmp_path, monkeypatch):
    """Missing logs directory is a silent no-op — no exception, no write."""
    repo = tmp_path / "repo"
    repo.mkdir()

    run_dir = tmp_path / "nonexistent-run"
    st = _make_state(leerie, run_dir)
    # Overwrite run_dir to a path with no logs subdir.
    nonexistent = tmp_path / "does-not-exist"
    st.run_dir = nonexistent

    monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

    async def spy_invoke(*_a, **_kw):
        pytest.fail("_invoke must not be called when logs dir is absent")

    monkeypatch.setattr(leerie, "_invoke", spy_invoke)

    try:
        asyncio.run(leerie.capture_repo_deps(
            repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
        ))
    except Exception as exc:
        pytest.fail(f"capture_repo_deps raised on missing logs dir: {exc!r}")

    cfg = repo / ".leerie" / "config.toml"
    assert not cfg.exists()


# ---------------------------------------------------------------------------
# Tests: non-fatal write failure
# ---------------------------------------------------------------------------

def test_write_failure_is_catchable(leerie, tmp_path, monkeypatch):
    """_write_config_toml_keys raising propagates from capture_repo_deps
    as a catchable Exception — the phase_finalize guard can catch it."""
    repo = tmp_path / "repo"
    repo.mkdir()
    st = _make_fake_state(leerie, tmp_path, ["apt-get install -y postgresql"])
    monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)
    _patch_invoke(leerie, monkeypatch)

    exc_caught = None
    with patch.object(leerie, "_write_config_toml_keys",
                      side_effect=OSError("disk full")):
        try:
            asyncio.run(leerie.capture_repo_deps(
                repo, st, caps=_CAPS, models=_MODELS, efforts=_EFFORTS,
            ))
        except Exception as exc:
            exc_caught = exc

    if exc_caught is not None:
        assert isinstance(exc_caught, Exception), (
            f"Expected a catchable Exception, got {type(exc_caught)}")
