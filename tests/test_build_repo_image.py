"""Tests for build_repo_image() invocation, error handling, and hash update.

Focuses on three checkable conditions not fully covered by
test_launcher_per_repo_image.py:
  (a) exact nerdctl argv on success: build, --build-arg BASE_IMAGE=<base>,
      -f <repo>/.leerie/Dockerfile, -t <repo-tag>, and <repo> as context.
  (b) .dockerfile-hash written with <version>:<sha256> on success; NOT
      updated when nerdctl build fails.
  (c) Coupling: base-image build error guard (Fix 1) sentinel present in
      launcher (the live behaviour is covered by test_launcher_base_image_build_error.py).

Uses an argv-recording nerdctl stub (writes "$@" to a log file) rather than
the exit-code-only stub in test_launcher_per_repo_image.py.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# Launcher source helpers
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]


# ---------------------------------------------------------------------------
# Harness preamble: argv-recording nerdctl stub + minimal env setup.
# The nerdctl stub writes all arguments to $NERDCTL_LOG (one line per call).
# ---------------------------------------------------------------------------

def _harness_prefix(tmp_path: Path) -> str:
    nerdctl_log = tmp_path / "nerdctl.log"
    return rf"""
#!/usr/bin/env bash
set -euo pipefail

NERDCTL_LOG="{nerdctl_log}"

remote_log() {{ echo "[leerie] $*" >&2; }}

# argv-recording nerdctl stub.  `return` (not `exit`) so set -e does not
# kill the shell on a deliberately non-zero exit.
nerdctl() {{
  local cmd="${{1:-}}"; shift || true
  # Record the subcommand + args for later inspection.
  echo "$cmd $*" >> "$NERDCTL_LOG"
  case "$cmd" in
    image)
      return "${{NERDCTL_INSPECT_RC:-0}}"
      ;;
    build)
      return "${{NERDCTL_BUILD_RC:-0}}"
      ;;
    *)
      return 0
      ;;
  esac
}}

# Stub git for repo-id derivation.
git() {{
  if [ "${{1:-}}" = "-C" ]; then shift 2; fi
  if [ "${{1:-}}" = "remote" ] && [ "${{2:-}}" = "get-url" ]; then
    echo "${{FAKE_GIT_REMOTE:-}}"
    return 0
  fi
  command git "$@"
}}

LEERIE_VERSION="${{LEERIE_VERSION:-0.99.test}}"
IMAGE_TAG="${{IMAGE_TAG:-leerie:${{LEERIE_VERSION}}}}"
USER_REPO="${{USER_REPO:-/tmp/test-user-repo}}"
LEERIE_STATE_HOST_DIR="${{LEERIE_STATE_HOST_DIR:-/tmp/leerie-state-test}}"

"""


def _run_harness(
    tmp_path: Path,
    body: str,
    env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Run the per-repo image block from the real launcher + `body` in a bash subprocess."""
    launcher = _launcher_text()
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    block = _extract_block(launcher, marker_start, marker_end)

    script = _harness_prefix(tmp_path) + block + "\n" + body

    base_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "0.99.test",
        "IMAGE_TAG": "leerie:0.99.test",
    }
    if env:
        base_env.update(env)

    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# (a) nerdctl argv on successful build
# ---------------------------------------------------------------------------

def test_build_repo_image_argv_contains_required_flags(tmp_path):
    """build_repo_image() passes the mandatory flags to nerdctl build."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",   # image absent → build fires
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "IMAGE_TAG": "leerie:1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    nerdctl_log = tmp_path / "nerdctl.log"
    assert nerdctl_log.exists(), "nerdctl was never called"
    log_content = nerdctl_log.read_text()

    # Must call `nerdctl build` (not just inspect).
    build_lines = [l for l in log_content.splitlines() if l.startswith("build ")]
    assert build_lines, f"no `build` call found in nerdctl log:\n{log_content}"
    build_args = build_lines[0]

    # BASE_IMAGE build-arg must use the base IMAGE_TAG.
    assert "--build-arg BASE_IMAGE=leerie:1.2.3" in build_args, build_args

    # -f must point at the repo's .leerie/Dockerfile.
    expected_df = str(user_repo / ".leerie" / "Dockerfile")
    assert f"-f {expected_df}" in build_args, build_args

    # Tag must be the per-repo leerie-repo/<id>:<version> form.
    assert "-t leerie-repo/" in build_args, build_args
    assert ":1.2.3" in build_args, build_args

    # Build context must be the USER_REPO path (last positional arg).
    assert str(user_repo) in build_args, build_args


# ---------------------------------------------------------------------------
# (a) .dockerfile-hash written with <version>:<sha256> on success
# ---------------------------------------------------------------------------

def test_hash_file_written_on_success(tmp_path):
    """After a successful build, .dockerfile-hash contains <version>:<sha256>."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr

    hash_file = state_dir / ".dockerfile-hash"
    assert hash_file.exists(), ".dockerfile-hash was not created"

    stored = hash_file.read_text().strip()
    expected_sha = hashlib.sha256(df.read_bytes()).hexdigest()
    expected = f"1.2.3:{expected_sha}"
    assert stored == expected, f"stored={stored!r}, expected={expected!r}"


# ---------------------------------------------------------------------------
# (b) hash file NOT updated on nerdctl build failure
# ---------------------------------------------------------------------------

def test_hash_file_not_written_on_failure(tmp_path):
    """When nerdctl build fails, the hash file must not be created or modified."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Pre-existing stale hash (must stay unchanged).
    hash_file = state_dir / ".dockerfile-hash"
    original_content = "0.98.old:stale\n"
    hash_file.write_text(original_content)

    result = _run_harness(
        tmp_path,
        # set -e + build_repo_image calling exit 1 ends the script; body
        # won't run and that's fine — we just care about the hash file state.
        "",
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",   # image absent → build fires
            "NERDCTL_BUILD_RC": "1",     # build fails
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 1, "expected exit 1 on build failure"
    assert "error: per-repo container image build failed" in result.stderr

    # Hash file must still contain the original stale content.
    assert hash_file.read_text() == original_content, (
        "hash file was modified despite build failure"
    )


def test_hash_file_absent_when_build_fails_from_scratch(tmp_path):
    """When build fails and no prior hash file exists, none should be created."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        tmp_path,
        "",
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "1",
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 1
    assert not (state_dir / ".dockerfile-hash").exists(), (
        "hash file must not exist when build failed"
    )


# ---------------------------------------------------------------------------
# (b) error message sentinel coupling
# ---------------------------------------------------------------------------

def test_per_repo_build_error_sentinel_in_launcher():
    """The per-repo build error message literal must be present in the launcher."""
    launcher_text = _launcher_text()
    assert 'remote_log "error: per-repo container image build failed"' in launcher_text, (
        "Per-repo build error sentinel changed in launcher — update this test to match."
    )


# ---------------------------------------------------------------------------
# (c) base-image build error guard (Fix 1) — coupling test
# ---------------------------------------------------------------------------

def test_base_image_build_error_sentinel_in_launcher():
    """Fix 1: the base image build must have error handling in the launcher.

    The live behaviour (exit 1 on failure) is exercised by
    test_launcher_base_image_build_error.py; this coupling test guards that
    the sentinel stays present in the launcher source.
    """
    launcher_text = _launcher_text()
    assert 'remote_log "error: container image build failed"' in launcher_text, (
        "Base-image build error sentinel changed in launcher — "
        "update test_launcher_base_image_build_error.py and this test to match."
    )
