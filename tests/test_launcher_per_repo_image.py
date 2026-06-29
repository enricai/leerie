"""Tests for per-repo derived image resolve/build in the leerie launcher.

Covers the bash functions added near the base-build block:
  _leerie_sha256, _leerie_repo_id, resolve_repo_image_tag, build_repo_image,
  the rebuild-decision block, and setup_packages auto-generation.

Uses the same bash-harness subprocess pattern as
test_launcher_base_image_build_error.py and test_ensure_image.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Shared harness preamble: stubs + the exact functions from the launcher.
# We extract the functions verbatim from the launcher rather than copying
# them so the tests stay coupled to the real source.
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_block(text: str, start_marker: str, end_marker: str) -> str:
    """Return the text between start_marker and end_marker (exclusive)."""
    s = text.index(start_marker)
    e = text.index(end_marker, s)
    return text[s:e]


# ---------------------------------------------------------------------------
# Helper: build a minimal harness that stubs nerdctl + git and sources the
# per-repo image block from the real launcher.
# ---------------------------------------------------------------------------

_HARNESS_PREFIX = r"""
#!/usr/bin/env bash
set -euo pipefail

# Env inputs (see individual test helpers below).

remote_log() { echo "[leerie] $*" >&2; }

# Stub nerdctl: dispatch on first two words of subcommand.
nerdctl() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image)
      return "${NERDCTL_INSPECT_RC:-0}"
      ;;
    build)
      return "${NERDCTL_BUILD_RC:-0}"
      ;;
    run)
      echo "nerdctl-run: image=${REPO_IMAGE_TAG:-$IMAGE_TAG}"
      return 0
      ;;
    *)
      return 0
      ;;
  esac
}

# Stub git remote for repo-id tests.
git() {
  if [ "${1:-}" = "-C" ]; then shift 2; fi
  if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then
    echo "${FAKE_GIT_REMOTE:-}"
    return 0
  fi
  command git "$@"
}

LEERIE_VERSION="${LEERIE_VERSION:-0.99.test}"
USER_REPO="${USER_REPO:-/tmp/test-user-repo}"
IMAGE_TAG="${IMAGE_TAG:-leerie:${LEERIE_VERSION}}"
LEERIE_STATE_HOST_DIR="${LEERIE_STATE_HOST_DIR:-/tmp/leerie-state-test}"

"""


def _run_harness(body: str, env: dict | None = None) -> subprocess.CompletedProcess:
    launcher = _launcher_text()
    # Extract only the per-repo functions block from the launcher
    # (from _leerie_sha256 through the end of the unset _leerie_dockerfile line).
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    block = _extract_block(launcher, marker_start, marker_end)

    script = _HARNESS_PREFIX + block + "\n" + body

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
# resolve_repo_image_tag
# ---------------------------------------------------------------------------

def test_resolve_repo_image_tag_no_dockerfile_no_setup_packages(tmp_path):
    """Returns empty string when no Dockerfile and no setup_packages."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    result = _run_harness(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={"USER_REPO": str(user_repo), "FAKE_GIT_REMOTE": ""},
    )
    assert result.returncode == 0, result.stderr
    assert "tag=" in result.stdout
    # empty — no Dockerfile, no setup_packages
    assert result.stdout.strip() == "tag="


def test_resolve_repo_image_tag_with_dockerfile(tmp_path):
    """Returns leerie-repo/<id>:<version> when .leerie/Dockerfile exists."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_harness(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag.startswith("leerie-repo/")
    assert tag.endswith(":1.2.3")
    # repo-id should be sanitized from owner/myrepo
    assert "owner" in tag or "myrepo" in tag


def test_resolve_repo_image_tag_with_setup_packages_only(tmp_path):
    """Returns a tag when setup_packages is set but no Dockerfile exists."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "libvips-dev"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_harness(
        'echo "tag=$(resolve_repo_image_tag)"',
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
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag.startswith("leerie-repo/")
    assert tag.endswith(":1.2.3")


# ---------------------------------------------------------------------------
# _leerie_repo_id
# ---------------------------------------------------------------------------

def test_repo_id_from_https_remote(tmp_path):
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    result = _run_harness(
        'echo "id=$(_leerie_repo_id)"',
        env={
            "USER_REPO": str(user_repo),
            "FAKE_GIT_REMOTE": "https://github.com/MyOrg/My-Repo.git",
        },
    )
    assert result.returncode == 0, result.stderr
    repo_id = result.stdout.strip().removeprefix("id=")
    # Should be lowercase, no dots-from-.git, slashes replaced with -
    assert repo_id == "myorg-my-repo", repo_id


def test_repo_id_from_ssh_remote(tmp_path):
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    result = _run_harness(
        'echo "id=$(_leerie_repo_id)"',
        env={
            "USER_REPO": str(user_repo),
            "FAKE_GIT_REMOTE": "git@github.com:owner/repo.git",
        },
    )
    assert result.returncode == 0, result.stderr
    repo_id = result.stdout.strip().removeprefix("id=")
    assert repo_id == "owner-repo", repo_id


def test_repo_id_basename_fallback(tmp_path):
    user_repo = tmp_path / "my-project"
    user_repo.mkdir()
    result = _run_harness(
        'echo "id=$(_leerie_repo_id)"',
        env={"USER_REPO": str(user_repo), "FAKE_GIT_REMOTE": ""},
    )
    assert result.returncode == 0, result.stderr
    repo_id = result.stdout.strip().removeprefix("id=")
    assert repo_id == "my-project", repo_id


# ---------------------------------------------------------------------------
# build_repo_image
# ---------------------------------------------------------------------------

def test_build_repo_image_success(tmp_path):
    """build_repo_image exits 0 when nerdctl build succeeds."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_harness(
        'build_repo_image "leerie-repo/test:0.99.test" && echo "build-ok"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "build-ok" in result.stdout


def test_build_repo_image_failure_exits_1(tmp_path):
    """build_repo_image exits 1 with a clear message when nerdctl build fails."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    result = _run_harness(
        'build_repo_image "leerie-repo/test:0.99.test"',
        env={"USER_REPO": str(user_repo), "NERDCTL_BUILD_RC": "1"},
    )
    assert result.returncode == 1
    assert "error: per-repo container image build failed" in result.stderr


def test_build_repo_image_error_message_matches_launcher():
    """The error message literal must match what's in the launcher source."""
    launcher_text = _launcher_text()
    assert 'remote_log "error: per-repo container image build failed"' in launcher_text


# ---------------------------------------------------------------------------
# Rebuild decision logic
# ---------------------------------------------------------------------------

def test_rebuild_skipped_when_image_present_and_hash_matches(tmp_path):
    """When image exists and hash matches, build is skipped."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Pre-compute the hash that the script will compute.
    import hashlib
    sha = hashlib.sha256(df.read_bytes()).hexdigest()
    version = "0.99.test"
    (state_dir / ".dockerfile-hash").write_text(f"{version}:{sha}\n")

    result = _run_harness(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",  # image present
            "NERDCTL_BUILD_RC": "1",    # would fail if invoked
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": version,
        },
    )
    assert result.returncode == 0, result.stderr
    assert "per-repo image up-to-date" in result.stderr
    assert "building per-repo container image" not in result.stderr
    assert "done" in result.stdout


def test_rebuild_fires_when_image_absent(tmp_path):
    """When image is absent (inspect fails), build fires."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",  # image absent
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_rebuild_fires_when_hash_changed(tmp_path):
    """When hash differs from stored, build fires."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Store a stale hash
    (state_dir / ".dockerfile-hash").write_text("0.99.test:aaaaaaaaaaaaaaaa\n")

    result = _run_harness(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",  # image present but hash mismatch
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_rebuild_fires_when_base_version_changed(tmp_path):
    """When the base version changes (new leerie release), rebuild fires."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Store hash with OLD version but correct Dockerfile sha256
    import hashlib
    sha = hashlib.sha256(df.read_bytes()).hexdigest()
    (state_dir / ".dockerfile-hash").write_text(f"0.98.old:{sha}\n")

    result = _run_harness(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "0.99.test",  # different from stored "0.98.old"
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_no_dockerfile_no_setup_packages_uses_base_image(tmp_path):
    """When no Dockerfile and no setup_packages, REPO_IMAGE_TAG stays empty."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'echo "repo_tag=${REPO_IMAGE_TAG:-EMPTY}"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "1",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "repo_tag=EMPTY" in result.stdout
    assert "building" not in result.stderr


# ---------------------------------------------------------------------------
# setup_packages auto-generation
# ---------------------------------------------------------------------------

def test_setup_packages_generates_dockerfile(tmp_path):
    """When setup_packages is set and no Dockerfile exists, one is auto-generated."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'echo "done"; cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "auto-generating .leerie/Dockerfile" in result.stderr
    assert "libvips-dev" in result.stdout
    assert "fonts-noto" in result.stdout
    assert "ARG BASE_IMAGE" in result.stdout
    assert "FROM $BASE_IMAGE" in result.stdout
    assert "apt-get install" in result.stdout


def test_existing_dockerfile_wins_over_setup_packages(tmp_path):
    """When both .leerie/Dockerfile and setup_packages exist, Dockerfile wins."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text(
        'setup_packages = "libvips-dev"\n'
    )
    (leerie_dir / "Dockerfile").write_text(
        "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n# CUSTOM\n"
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    # auto-gen should NOT have run
    assert "auto-generating" not in result.stderr
    # The committed Dockerfile content is preserved
    assert "# CUSTOM" in result.stdout


def test_nerdctl_run_uses_repo_image_tag_when_set(tmp_path):
    """REPO_IMAGE_TAG is set when Dockerfile is present; fallback to IMAGE_TAG otherwise."""
    launcher_text = _launcher_text()
    # The nerdctl run line must use the fallback expression
    assert '"${REPO_IMAGE_TAG:-$IMAGE_TAG}"' in launcher_text
