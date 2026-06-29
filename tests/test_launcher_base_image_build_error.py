"""Tests for base-image build error handling in the leerie launcher.

The local nerdctl build block at leerie:2540-2548 wraps `nerdctl build` with
`|| { remote_log "error: container image build failed"; exit 1; }` so a
failed build surfaces a clear message instead of silently falling through to
a confusing image-not-found at `nerdctl run`.

Uses the same bash-harness subprocess pattern as test_launcher_state_mount.py
and test_ensure_image.py.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Minimal harness that reproduces the image-build block from the launcher.
# We define `remote_log` inline (matches launcher's impl: writes to stderr),
# stub `nerdctl` to allow controlling inspect/build exit codes, and
# source the exact block under test.
#
# NOTE: stub uses `return` (not `exit`) because bash functions called with
# `set -e` active exit the whole shell when `exit N` is used with N!=0.
_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

# Inputs via env:
#   NERDCTL_INSPECT_RC  — exit code for `nerdctl image inspect` (0=cached, 1=miss)
#   NERDCTL_BUILD_RC    — exit code for `nerdctl build`
#   IMAGE_TAG           — the image tag to inspect/build

IMAGE_TAG="${IMAGE_TAG:-leerie:test}"

remote_log() { echo "[leerie] $*" >&2; }

# Stub nerdctl: dispatch on subcommand, use `return` not `exit`.
nerdctl() {
  local cmd="$1"; shift
  case "$cmd" in
    image)
      return "${NERDCTL_INSPECT_RC:-0}"
      ;;
    build)
      return "${NERDCTL_BUILD_RC:-0}"
      ;;
    *)
      return 0
      ;;
  esac
}

# --- build image if not present (one-time, ~60-120s) --------------------
if ! nerdctl image inspect "$IMAGE_TAG" >/dev/null 2>&1; then
  remote_log "building container image (one-time; ~60-120s)..."
  nerdctl build \
    --build-arg HOST_UID="$(id -u)" \
    --build-arg HOST_GID="$(id -g)" \
    -t "$IMAGE_TAG" \
    "${LEERIE_REPO:-.}" \
    || { remote_log "error: container image build failed"; exit 1; }
fi

echo "image-ready"
"""


def _run(
    *,
    inspect_rc: int,
    build_rc: int,
    image_tag: str = "leerie:test",
) -> subprocess.CompletedProcess:
    env = {
        "PATH": "/usr/bin:/bin",
        "NERDCTL_INSPECT_RC": str(inspect_rc),
        "NERDCTL_BUILD_RC": str(build_rc),
        "IMAGE_TAG": image_tag,
    }
    return subprocess.run(
        ["bash", "-c", _HARNESS],
        env=env,
        capture_output=True,
        text=True,
    )


def test_image_already_present_skips_build():
    """When nerdctl image inspect succeeds (rc=0), build is skipped entirely."""
    result = _run(inspect_rc=0, build_rc=1)  # build_rc=1 would fail if run
    assert result.returncode == 0, result.stderr
    assert "image-ready" in result.stdout
    assert "building container image" not in result.stderr


def test_image_absent_triggers_build_success():
    """When inspect misses (rc=1) and build succeeds (rc=0), block exits cleanly."""
    result = _run(inspect_rc=1, build_rc=0)
    assert result.returncode == 0, result.stderr
    assert "image-ready" in result.stdout
    assert "building container image" in result.stderr
    assert "error: container image build failed" not in result.stderr


def test_image_absent_build_failure_exits_1():
    """When inspect misses and build fails, the block exits 1 with a clear message."""
    result = _run(inspect_rc=1, build_rc=1)
    assert result.returncode == 1
    assert "error: container image build failed" in result.stderr
    assert "image-ready" not in result.stdout


def test_build_failure_message_matches_launcher():
    """The error message literal must match what's in the launcher source."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    assert 'remote_log "error: container image build failed"' in launcher_text, (
        "Launcher error message changed — update this test and the harness to match."
    )


def test_host_uid_gid_args_present_in_launcher():
    """--build-arg HOST_UID and HOST_GID must still be present in the launcher."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    assert '--build-arg HOST_UID="$(id -u)"' in launcher_text
    assert '--build-arg HOST_GID="$(id -g)"' in launcher_text
