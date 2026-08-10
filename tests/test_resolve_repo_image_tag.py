"""Tests for resolve_repo_image_tag() and its rebuild-decision block in the
leerie launcher.

Verifies Phase-2 per-repo image identity and rebuild-trigger logic:
  - empty tag when no .leerie/Dockerfile and no setup_packages
  - tag format leerie-repo/<repo-id>:<version> when Dockerfile present
  - repo-id derived from git remote get-url origin owner/repo, with
    basename fallback when no remote
  - rebuild signals: image absent, Dockerfile hash mismatch, base version change
  - no rebuild when image present and hash matches

Strategy: extract the real functions (`_leerie_sha256`, `_leerie_repo_id`,
`_leerie_should_generate_dockerfile`, `resolve_repo_image_tag`,
`ensure_base_in_buildkit_ns`, `build_repo_image`) and the top-level
rebuild-decision block verbatim out of the launcher (mirroring
`tests/test_resolve_ec2_vars.py`'s `_extract_resolve_ec2_knob`), rather than
hand-reproducing them. A hand-copied reproduction is body-blind by
construction — the tests would run a string literal defined in this file, so
no change to the launcher's actual rebuild logic could affect them. `git`
and `nerdctl` are stubbed via shell functions (real building/pushing is out
of scope; only the decision of whether to build is under test).

Verified live per N13's documented trap: an inert sabotage is not a valid
falsification. The discriminating falsification used here is flipping the
`_need_build` decision's boolean sense (build when the hash *matches*
instead of when it *differs*): with the extraction,
`test_rebuild_false_when_image_present_and_hash_matches` and
`test_rebuild_true_when_dockerfile_hash_differs` both fail; against the old
hand-copied harness they could not have, since that harness never read the
launcher's source at all.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_fn(name: str) -> str:
    """Extract a single `name() { ... }` function verbatim from the
    launcher (brace-matched via the first `\\n}\\n` after the opening
    line, mirroring `_extract_resolve_ec2_knob`'s technique)."""
    src = LAUNCHER.read_text()
    start = src.index(name)
    end = src.index("\n}\n", start) + len("\n}\n")
    return src[start:end]


def _extract_rebuild_decision_block() -> str:
    """The REAL top-level rebuild-decision block, lifted verbatim out of
    the launcher: resolves REPO_IMAGE_TAG, computes the current/stored
    Dockerfile hash, and decides `_need_build`."""
    src = LAUNCHER.read_text()
    start = src.index("# Only proceed if a Dockerfile is now present")
    end_marker = "unset _leerie_dockerfile\n"
    end = src.index(end_marker, start) + len(end_marker)
    return src[start:end]


def _extracted_block() -> str:
    return "\n".join([
        _extract_fn("_leerie_sha256() {"),
        _extract_fn("_leerie_repo_id() {"),
        _extract_fn("_leerie_should_generate_dockerfile() {"),
        _extract_fn("resolve_repo_image_tag() {"),
        _extract_fn("ensure_base_in_buildkit_ns() {"),
        _extract_fn("build_repo_image() {"),
    ])


# Harness preamble: shell stubs for git/nerdctl/remote_log/id, injected
# before the real, extracted launcher functions and rebuild-decision block.
# nerdctl image inspect exit code is controlled by $NERDCTL_INSPECT_RC.
# git remote get-url origin returns $FAKE_GIT_REMOTE (empty = no remote).
def _harness() -> str:
    return r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

nerdctl() {
  local cmd="${1:-}"
  if [ "$cmd" = "--namespace" ]; then
    return 0   # ensure_base_in_buildkit_ns's namespace probe/copy: no-op ok
  fi
  if [ "$cmd" = "image" ]; then
    return "${NERDCTL_INSPECT_RC:-0}"
  fi
  if [ "$cmd" = "build" ]; then
    return "${NERDCTL_BUILD_RC:-0}"
  fi
  return 0
}

git() {
  if [ "${1:-}" = "-C" ]; then shift 2; fi
  if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then
    echo "${FAKE_GIT_REMOTE:-}"
    return 0
  fi
  command git "$@"
}

LEERIE_VERSION="${LEERIE_VERSION:-0.99.test}"
IMAGE_TAG="leerie:${LEERIE_VERSION}"
USER_REPO="${USER_REPO:-/tmp/test-repo}"
LEERIE_STATE_HOST_DIR="${LEERIE_STATE_HOST_DIR:-/tmp/leerie-state-test}"

""" + _extracted_block() + r"""

_leerie_dockerfile="$USER_REPO/.leerie/Dockerfile"

"""


def _run(body: str, *, env: dict | None = None) -> subprocess.CompletedProcess:
    base_env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": "/tmp",
        "LEERIE_VERSION": "0.99.test",
    }
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", _harness() + "\n" + body],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _run_with_rebuild_decision(body: str, *, env: dict | None = None,
                                ) -> subprocess.CompletedProcess:
    """Like _run, but also runs the real rebuild-decision block before body."""
    return _run(_extract_rebuild_decision_block() + "\n" + body, env=env)


# ---------------------------------------------------------------------------
# (a) No .leerie/Dockerfile and no setup_packages → empty string
# ---------------------------------------------------------------------------

def test_no_dockerfile_no_setup_packages_returns_empty(tmp_path: Path):
    """resolve_repo_image_tag returns empty when no Dockerfile and no setup_packages."""
    user_repo = tmp_path / "myrepo"
    user_repo.mkdir()
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={"USER_REPO": str(user_repo), "FAKE_GIT_REMOTE": ""},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "tag="


def test_no_dockerfile_no_setup_packages_repo_image_tag_empty(tmp_path: Path):
    """REPO_IMAGE_TAG stays empty (fallback to base IMAGE_TAG) when no Dockerfile."""
    user_repo = tmp_path / "myrepo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_with_rebuild_decision(
        'echo "repo_tag=${REPO_IMAGE_TAG:-EMPTY}"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "NERDCTL_INSPECT_RC": "1",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "repo_tag=EMPTY" in result.stdout


# ---------------------------------------------------------------------------
# ($_sp gating-bug fix) a lockfile present (no setup_packages) must still
# resolve a non-empty tag — regression coverage for the bug this session
# fixed: the early-return previously checked setup_packages ONLY.
# ---------------------------------------------------------------------------

def test_lockfile_without_setup_packages_resolves_nonempty_tag(tmp_path: Path):
    """A lockfile-only repo (no setup_packages, no Dockerfile) must resolve a
    real tag, not fall back to the empty string / base image."""
    user_repo = tmp_path / "myrepo"
    user_repo.mkdir()
    (user_repo / "uv.lock").write_text("")
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "FAKE_GIT_REMOTE": "https://github.com/owner/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "leerie-repo/owner-myrepo:1.2.3", tag


def test_bare_requirements_txt_without_setup_packages_returns_empty(tmp_path: Path):
    """Negative control: bare requirements.txt (no lockfile) is deliberately
    excluded — must still resolve empty, matching _lockfile_table_entries."""
    user_repo = tmp_path / "myrepo"
    user_repo.mkdir()
    (user_repo / "requirements.txt").write_text("requests==2.31.0\n")
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={"USER_REPO": str(user_repo), "FAKE_GIT_REMOTE": ""},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "tag="


# ---------------------------------------------------------------------------
# (b) Tag format: leerie-repo/<sanitized-repo-id>:<version>
# ---------------------------------------------------------------------------

def test_tag_format_https_remote(tmp_path: Path):
    """Tag is leerie-repo/<owner-repo>:<version> for an HTTPS remote URL."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "https://github.com/myorg/myrepo.git",
            "LEERIE_VERSION": "1.2.3",
            "NERDCTL_INSPECT_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "leerie-repo/myorg-myrepo:1.2.3", tag


def test_tag_format_ssh_remote(tmp_path: Path):
    """Tag repo-id extracted from SSH remote git@github.com:owner/repo.git."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "git@github.com:owner/repo.git",
            "LEERIE_VERSION": "2.0.0",
            "NERDCTL_INSPECT_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "leerie-repo/owner-repo:2.0.0", tag


def test_tag_format_basename_fallback_when_no_remote(tmp_path: Path):
    """When git remote returns empty, repo-id falls back to basename of USER_REPO."""
    user_repo = tmp_path / "my-project"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "3.0.0",
            "NERDCTL_INSPECT_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "leerie-repo/my-project:3.0.0", tag


def test_tag_format_uppercase_remote_sanitized(tmp_path: Path):
    """Uppercase letters in owner/repo are lowercased in the tag."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run(
        'echo "tag=$(resolve_repo_image_tag)"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "https://github.com/MyOrg/My-Repo.git",
            "LEERIE_VERSION": "1.0.0",
            "NERDCTL_INSPECT_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    tag = result.stdout.strip().removeprefix("tag=")
    assert tag == "leerie-repo/myorg-my-repo:1.0.0", tag


# ---------------------------------------------------------------------------
# (c) Rebuild decision matrix
# ---------------------------------------------------------------------------

def test_rebuild_true_when_image_absent(tmp_path: Path):
    """Rebuild fires when nerdctl image inspect exits non-zero (image absent)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_with_rebuild_decision(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "NERDCTL_INSPECT_RC": "1",  # image absent
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_rebuild_true_when_dockerfile_hash_differs(tmp_path: Path):
    """Rebuild fires when stored .dockerfile-hash content sha256 differs from current."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "Dockerfile").write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Store a hash with a wrong sha256 (but correct version).
    (state_dir / ".dockerfile-hash").write_text("0.99.test:aaaaaaaaaaaaaaaa\n")
    result = _run_with_rebuild_decision(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "NERDCTL_INSPECT_RC": "0",  # image present — hash decides
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_rebuild_true_when_base_version_changed(tmp_path: Path):
    """Rebuild fires when stored hash has an old base version prefix."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Correct sha but OLD version prefix — triggers version-change rebuild.
    sha = hashlib.sha256(df.read_bytes()).hexdigest()
    (state_dir / ".dockerfile-hash").write_text(f"0.98.old:{sha}\n")
    result = _run_with_rebuild_decision(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": "0.99.test",  # differs from stored "0.98.old"
            "NERDCTL_INSPECT_RC": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "building per-repo container image" in result.stderr


def test_rebuild_false_when_image_present_and_hash_matches(tmp_path: Path):
    """No rebuild when image is present and stored hash matches current Dockerfile + version."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    version = "0.99.test"
    sha = hashlib.sha256(df.read_bytes()).hexdigest()
    (state_dir / ".dockerfile-hash").write_text(f"{version}:{sha}\n")
    result = _run_with_rebuild_decision(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": version,
            "NERDCTL_INSPECT_RC": "0",  # image present
        },
    )
    assert result.returncode == 0, result.stderr
    assert "per-repo image up-to-date" in result.stderr
    assert "building per-repo container image" not in result.stderr
