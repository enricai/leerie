"""Tests for Dockerfile auto-generation from setup_packages.

Verifies the Phase-2 auto-generation behavior:
  (a) When .leerie/config.toml declares setup_packages but no .leerie/Dockerfile
      exists, the launcher synthesizes a Dockerfile with the correct scaffold:
        ARG BASE_IMAGE / FROM $BASE_IMAGE / USER root / apt-get install <pkgs>
        / USER leerie.
  (b) When .leerie/Dockerfile already exists it takes precedence — the
      existing content is preserved and setup_packages is NOT consumed.
  (c) When neither setup_packages nor a Dockerfile is present, no per-repo
      image is generated (REPO_IMAGE_TAG remains empty).

Uses the same bash-harness extraction pattern as test_launcher_per_repo_image.py
— extracts the per-repo image block verbatim from the live launcher so the
tests stay coupled to the real source.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Harness helpers
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


def _extract_autogen_block(text: str) -> str:
    """Extract the per-repo image block from the launcher verbatim."""
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- translate --inspect-dir paths"
    s = text.index(marker_start)
    e = text.index(marker_end, s)
    return text[s:e]


_HARNESS_PREFIX = r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

nerdctl() {
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image)  return "${NERDCTL_INSPECT_RC:-0}" ;;
    build)  return "${NERDCTL_BUILD_RC:-0}" ;;
    run)    echo "nerdctl-run: image=${REPO_IMAGE_TAG:-$IMAGE_TAG}"; return 0 ;;
    *)      return 0 ;;
  esac
}

git() {
  if [ "${1:-}" = "-C" ]; then shift 2; fi
  if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then
    echo "${FAKE_GIT_REMOTE:-}"; return 0
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
    block = _extract_autogen_block(launcher)
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
# (a) Generated Dockerfile content — scaffold and package listing
# ---------------------------------------------------------------------------

def test_autogen_contains_arg_base_image(tmp_path):
    """Generated Dockerfile must start with ARG BASE_IMAGE."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    assert "ARG BASE_IMAGE" in result.stdout


def test_autogen_contains_from_base_image(tmp_path):
    """Generated Dockerfile must contain FROM $BASE_IMAGE."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    assert "FROM $BASE_IMAGE" in result.stdout


def test_autogen_contains_user_root(tmp_path):
    """Generated Dockerfile must switch to USER root before apt-get."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    assert "USER root" in result.stdout


def test_autogen_contains_apt_get_install(tmp_path):
    """Generated Dockerfile must include an apt-get install line."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    assert "apt-get install" in result.stdout


def test_autogen_lists_all_declared_packages(tmp_path):
    """Generated Dockerfile lists every package declared in setup_packages."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    assert "libvips-dev" in result.stdout
    assert "fonts-noto" in result.stdout


def test_autogen_does_not_end_with_user_leerie(tmp_path):
    """Generated Dockerfile must NOT append a trailing USER leerie: the base
    image's ENTRYPOINT (container-entry.sh) must run as PID-1 root to set up
    cgroup containment before dropping to leerie via runuser (DESIGN §6). A
    trailing USER leerie makes PID 1 run as leerie and the container exits 1."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev, fonts-noto"\n'
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
    content = result.stdout
    assert "USER leerie" not in content, (
        f"generated Dockerfile must not contain USER leerie, got:\n{content}"
    )


def test_autogen_ends_with_user_root(tmp_path):
    """The generated apt layer switches to USER root and never leaves it — the
    image's effective final USER stays root so PID-1 is root (DESIGN §6)."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev"\n'
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
    content = result.stdout
    # USER root appears (before apt); no later USER directive resets it.
    assert "USER root" in content
    users = [l.strip() for l in content.splitlines() if l.strip().startswith("USER ")]
    assert users[-1] == "USER root", f"last USER directive must be root, got: {users}"


def test_autogen_log_message_emitted(tmp_path):
    """auto-generation logs a message naming the packages."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev"\n'
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'true',
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


# ---------------------------------------------------------------------------
# (b) Precedence: existing .leerie/Dockerfile wins over setup_packages
# ---------------------------------------------------------------------------

def test_existing_dockerfile_preserved_verbatim(tmp_path):
    """When .leerie/Dockerfile exists, its content is not modified."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev"\n'
    )
    custom_content = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n# CUSTOM MANUAL CONTENT\n"
    (user_repo / ".leerie" / "Dockerfile").write_text(custom_content)
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
    assert result.stdout == custom_content


def test_existing_dockerfile_suppresses_autogen(tmp_path):
    """When .leerie/Dockerfile exists, setup_packages is not consumed (no auto-gen log)."""
    user_repo = tmp_path / "repo"
    (user_repo / ".leerie").mkdir(parents=True)
    (user_repo / ".leerie" / "config.toml").write_text(
        'setup_packages = "libvips-dev"\n'
    )
    (user_repo / ".leerie" / "Dockerfile").write_text(
        "ARG BASE_IMAGE\nFROM $BASE_IMAGE\n# MANUAL\n"
    )
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'true',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "auto-generating" not in result.stderr


# ---------------------------------------------------------------------------
# (c) No setup_packages and no Dockerfile → no per-repo image
# ---------------------------------------------------------------------------

def test_no_setup_packages_no_dockerfile_leaves_repo_image_tag_empty(tmp_path):
    """REPO_IMAGE_TAG stays unset when neither setup_packages nor a Dockerfile exists."""
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


def test_no_setup_packages_no_dockerfile_does_not_build(tmp_path):
    """No build fires when neither setup_packages nor a Dockerfile is present."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        'true',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "1",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building" not in result.stderr
    assert "auto-generating" not in result.stderr


# ---------------------------------------------------------------------------
# Coupling tests — sentinel strings must exist in the live launcher
# ---------------------------------------------------------------------------

def test_coupling_autogen_log_sentinel():
    """The auto-gen log sentinel must be present verbatim in the launcher."""
    launcher = _launcher_text()
    assert 'remote_log "auto-generating .leerie/Dockerfile from setup_packages' in launcher


def test_coupling_arg_base_image_in_launcher():
    """The ARG BASE_IMAGE printf literal must be present in the launcher source."""
    launcher = _launcher_text()
    assert "ARG BASE_IMAGE" in launcher


def test_coupling_no_trailing_user_leerie_printf_in_launcher():
    """The generator printf must NOT emit a trailing `USER leerie` (DESIGN §6:
    PID-1 must stay root). Guard against the specific printf literals that
    previously appended it — a comment mentioning USER leerie is fine, but the
    `rm -rf .../lists/*\\nUSER leerie` printf must be gone from both sites."""
    launcher = _launcher_text()
    assert "/var/lib/apt/lists/*\\nUSER leerie" not in launcher, (
        "generator must not printf a trailing USER leerie after the apt RUN"
    )


def test_coupling_user_root_in_launcher():
    """The USER root printf literal must be present in the launcher source."""
    launcher = _launcher_text()
    assert "USER root" in launcher


def test_coupling_apt_get_install_in_launcher():
    """The apt-get install literal must be present in the launcher source."""
    launcher = _launcher_text()
    assert "apt-get install" in launcher
