"""Tests for chain launcher verbs added per QUEUE_JOBS.md Option-1 CLI shape.

Verifies that each chain verb dispatches correctly to the leerie-chain HTTP API
(curl stubbed) and that --runs/--target are NOT in _value_flags.

Verbs under test:
  --chain-submit   POST /chains with --runs and --target
  --chain-status   GET  /chains/<id>
  --list-chains    GET  /chains
  --chain-kill     DELETE /chains/<id>
  --chain-attach   GET  /chains/<id>/log
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _stub_curl(tmp_path: Path, response: str = '{"ok":true}', rc: int = 0) -> Path:
    """Write a curl stub that logs its invocation and returns a fixed response."""
    log = tmp_path / "curl.log"
    fake = tmp_path / "curl"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@" >> "{log}"\n'
        f'echo \'{response}\'\n'
        f"exit {rc}\n"
    )
    fake.chmod(0o755)
    return fake


def _run_launcher(tmp_path: Path, args: list[str],
                  env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """Invoke the launcher with a stubbed curl on PATH."""
    _stub_curl(tmp_path)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "LEERIE_CHAIN_URL": "http://test-chain.internal",
        "USER_REPO": str(tmp_path),
        # Prevent the launcher from entering Keychain / runtime preflight:
        "LEERIE_REPO": str(REPO_ROOT),
        # state-dir resolution at the top of the launcher dereferences $HOME
        # under `set -u` before verb dispatch (PR #8 / config-001); chain
        # verbs are fast-paths after that point, not before it.
        "HOME": str(tmp_path),
    }
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", str(LAUNCHER)] + args,
        env=env,
        capture_output=True,
        text=True,
    )


def _curl_invocations(tmp_path: Path) -> str:
    log = tmp_path / "curl.log"
    return log.read_text() if log.exists() else ""


# ---------------------------------------------------------------------------
# --chain-submit
# ---------------------------------------------------------------------------

def test_chain_submit_posts_to_chains_endpoint(tmp_path: Path):
    """--chain-submit posts to /chains with the correct method and endpoint."""
    result = _run_launcher(tmp_path, [
        "--chain-submit",
        "--runs", "prompts/run-1.txt,prompts/run-2.txt",
        "--target", "/tmp/my-repo",
    ])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "-X POST" in invoc
    assert "/chains" in invoc
    assert "http://test-chain.internal" in invoc


def test_chain_submit_includes_runs_in_payload(tmp_path: Path):
    """--chain-submit includes the --runs value in the JSON payload."""
    result = _run_launcher(tmp_path, [
        "--chain-submit",
        "--runs", "a.txt,b.txt",
        "--target", "/tmp/repo",
    ])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    # The JSON body is passed via -d; it should contain the run filenames.
    assert "a.txt" in invoc
    assert "b.txt" in invoc


def test_chain_submit_includes_target_in_payload(tmp_path: Path):
    """--chain-submit includes the --target path in the JSON payload."""
    result = _run_launcher(tmp_path, [
        "--chain-submit",
        "--runs", "run.txt",
        "--target", "/my/special/repo",
    ])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "/my/special/repo" in invoc


def test_chain_submit_requires_runs(tmp_path: Path):
    """--chain-submit without --runs exits non-zero with an error."""
    result = _run_launcher(tmp_path, ["--chain-submit", "--target", "/tmp/repo"])
    assert result.returncode != 0
    assert "--runs" in result.stderr


def test_chain_submit_target_optional(tmp_path: Path):
    """--chain-submit without --target succeeds (target defaults to USER_REPO)."""
    result = _run_launcher(tmp_path, [
        "--chain-submit", "--runs", "run.txt",
    ])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "-X POST" in invoc
    assert "/chains" in invoc


def test_chain_submit_rejects_unknown_flags(tmp_path: Path):
    """--chain-submit rejects unknown flags."""
    result = _run_launcher(tmp_path, [
        "--chain-submit", "--runs", "a.txt", "--bogus",
    ])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# --chain-status
# ---------------------------------------------------------------------------

def test_chain_status_gets_chain_by_id(tmp_path: Path):
    """--chain-status <id> hits GET /chains/<id>."""
    result = _run_launcher(tmp_path, ["--chain-status", "chain-abc123"])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "/chains/chain-abc123" in invoc
    # Must be a GET (no -X flag, or explicit -X GET; curl default is GET)
    assert "-X DELETE" not in invoc
    assert "-X POST" not in invoc


def test_chain_status_requires_chain_id(tmp_path: Path):
    """--chain-status without <chain-id> exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-status"])
    assert result.returncode != 0
    assert "chain-id" in result.stderr


def test_chain_status_rejects_flag_as_id(tmp_path: Path):
    """--chain-status with a flag where <chain-id> is expected exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-status", "--bogus-flag"])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# --list-chains
# ---------------------------------------------------------------------------

def test_list_chains_gets_chains_root(tmp_path: Path):
    """--list-chains hits GET /chains (no path suffix)."""
    result = _run_launcher(tmp_path, ["--list-chains"])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "http://test-chain.internal/chains" in invoc
    # Not hitting a specific chain id
    assert "-X DELETE" not in invoc
    assert "-X POST" not in invoc


def test_list_chains_uses_chain_url_env(tmp_path: Path):
    """--list-chains respects LEERIE_CHAIN_URL."""
    _stub_curl(tmp_path)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "LEERIE_CHAIN_URL": "http://my-chain-app.fly.dev",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(tmp_path),
    }
    result = subprocess.run(
        ["bash", str(LAUNCHER), "--list-chains"],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "http://my-chain-app.fly.dev/chains" in invoc


# ---------------------------------------------------------------------------
# --chain-kill
# ---------------------------------------------------------------------------

def test_chain_kill_deletes_chain_by_id(tmp_path: Path):
    """--chain-kill <id> hits DELETE /chains/<id>."""
    result = _run_launcher(tmp_path, ["--chain-kill", "chain-xyz"])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "-X DELETE" in invoc
    assert "/chains/chain-xyz" in invoc


def test_chain_kill_requires_chain_id(tmp_path: Path):
    """--chain-kill without <chain-id> exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-kill"])
    assert result.returncode != 0
    assert "chain-id" in result.stderr


def test_chain_kill_rejects_flag_as_id(tmp_path: Path):
    """--chain-kill with a flag where <chain-id> is expected exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-kill", "--not-an-id"])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# --chain-attach
# ---------------------------------------------------------------------------

def test_chain_attach_streams_log_by_id(tmp_path: Path):
    """--chain-attach <id> hits GET /chains/<id>/log."""
    result = _run_launcher(tmp_path, ["--chain-attach", "chain-log-test"])
    assert result.returncode == 0, result.stderr
    invoc = _curl_invocations(tmp_path)
    assert "/chains/chain-log-test/log" in invoc
    assert "-X DELETE" not in invoc
    assert "-X POST" not in invoc


def test_chain_attach_requires_chain_id(tmp_path: Path):
    """--chain-attach without <chain-id> exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-attach"])
    assert result.returncode != 0
    assert "chain-id" in result.stderr


def test_chain_attach_rejects_flag_as_id(tmp_path: Path):
    """--chain-attach with a flag where <chain-id> is expected exits non-zero."""
    result = _run_launcher(tmp_path, ["--chain-attach", "--not-an-id"])
    assert result.returncode != 0


# ---------------------------------------------------------------------------
# Value-flags coupling: --runs and --target must NOT be in _value_flags
# ---------------------------------------------------------------------------

def test_chain_flags_absent_from_value_flags():
    """--runs and --target are launcher-fast-path flags, not forwarded to the
    orchestrator argparse, so they must NOT appear in _value_flags."""
    import re
    src = LAUNCHER.read_text()
    m = re.search(r'_value_flags="\s*([^"]+?)"', src, flags=re.DOTALL)
    assert m, "could not find _value_flags= assignment in launcher"
    raw = m.group(1).replace("\\\n", " ")
    flags = {w for w in raw.split() if w.startswith("--")}
    assert "--runs" not in flags, (
        "--runs must NOT be in _value_flags: chain verbs are launcher-handled "
        "fast-paths, not forwarded to the orchestrator argparse"
    )
    assert "--target" not in flags, (
        "--target must NOT be in _value_flags: chain verbs are launcher-handled "
        "fast-paths, not forwarded to the orchestrator argparse"
    )


# ---------------------------------------------------------------------------
# Fast-path placement: chain verbs must be before runtime preflight
# ---------------------------------------------------------------------------

def test_chain_verbs_are_before_runtime_preflight():
    """All chain verbs must be in the early fast-path case block (before the
    platform preflight), so they work without nerdctl/Colima installed."""
    text = LAUNCHER.read_text()
    preflight_idx = text.find("# --- platform preflight")
    assert preflight_idx != -1, "could not find platform preflight marker"
    for verb in ("--chain-submit)", "--chain-status)", "--list-chains)",
                 "--chain-kill)", "--chain-attach)"):
        idx = text.find(verb)
        assert idx != -1, f"launcher missing case arm for {verb}"
        assert idx < preflight_idx, (
            f"{verb} must appear before the platform preflight block"
        )
