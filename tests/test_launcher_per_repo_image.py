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

from tests.repo_image_block_extract import extract_block as _extract_block

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Shared harness preamble: stubs + the exact functions from the launcher.
# We extract the functions verbatim from the launcher rather than copying
# them so the tests stay coupled to the real source.
# ---------------------------------------------------------------------------

def _launcher_text() -> str:
    return (REPO_ROOT / "leerie").read_text()


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

# Stub git remote for repo-id tests. Preserves `-C <dir>` for pass-through
# commands (e.g. `git -C "$USER_REPO" ls-files`) so the auto-gen guard's
# git-tracked authority check runs against the real fixture repo.
git() {
  local _gitdir_args=()
  if [ "${1:-}" = "-C" ]; then _gitdir_args=("-C" "$2"); shift 2; fi
  if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then
    echo "${FAKE_GIT_REMOTE:-}"
    return 0
  fi
  # ${arr[@]+"${arr[@]}"} so an empty array expands to nothing under `set -u`
  # (bash 3.2, the macOS system default, otherwise errors) — the repo's own
  # idiom (see leerie:5799, scripts/remote/build-push.sh:203).
  command git "${_gitdir_args[@]+"${_gitdir_args[@]}"}" "$@"
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


# ---------------------------------------------------------------------------
# Language-dep bake (bake_language_deps, default true)
# ---------------------------------------------------------------------------

def test_language_dep_layer_included_for_pnpm_repo(tmp_path):
    """With pnpm-lock.yaml present and bake_language_deps=true, the auto-generated
    Dockerfile includes COPY of lockfile+manifests and RUN pnpm install."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "libvips-dev"\n')
    # Simulate a pnpm repo.
    (user_repo / "package.json").write_text('{"name":"test"}')
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
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
            "LEERIE_BAKE_LANGUAGE_DEPS": "true",
        },
    )
    assert result.returncode == 0, result.stderr
    df_content = result.stdout
    # Must have apt layer.
    assert "apt-get install" in df_content
    assert "libvips-dev" in df_content
    # Must have COPY line including pnpm-lock.yaml.
    assert "COPY" in df_content
    assert "pnpm-lock.yaml" in df_content
    # Must have RUN pnpm install.
    assert "RUN pnpm install --frozen-lockfile" in df_content
    # Must have copy-input-shas comment so Dockerfile sha captures dep drift.
    assert "copy-input-shas:" in df_content
    assert "pnpm-lock.yaml=" in df_content


def test_language_dep_layer_includes_patches_for_pnpm(tmp_path):
    """patches/ directory is included in COPY when present (pnpm patchedDependencies)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    (user_repo / "package.json").write_text('{"name":"test"}')
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
    patches_dir = user_repo / "patches"
    patches_dir.mkdir()
    (patches_dir / "foo.patch").write_text("--- a/foo\n+++ b/foo\n")
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
    assert "patches/" in result.stdout


def test_language_dep_layer_disabled_by_env(tmp_path):
    """LEERIE_BAKE_LANGUAGE_DEPS=false produces apt-only Dockerfile."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    (user_repo / "package.json").write_text('{"name":"test"}')
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
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
            "LEERIE_BAKE_LANGUAGE_DEPS": "false",
        },
    )
    assert result.returncode == 0, result.stderr
    df_content = result.stdout
    # apt layer present.
    assert "apt-get install" in df_content
    assert "curl" in df_content
    # No language-dep layer.
    assert "COPY" not in df_content
    assert "pnpm install" not in df_content
    assert "copy-input-shas" not in df_content


def test_language_dep_disabled_via_zero(tmp_path):
    """LEERIE_BAKE_LANGUAGE_DEPS=0 is accepted as false."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "libffi-dev"\n')
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
    (user_repo / "package.json").write_text('{}')
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
            "LEERIE_BAKE_LANGUAGE_DEPS": "0",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "COPY" not in result.stdout
    assert "pnpm install" not in result.stdout


def test_lockfile_sha_in_dockerfile_triggers_rebuild_on_change(tmp_path):
    """A lockfile change causes the Dockerfile sha to differ (because the sha
    comment line changes), which flips _need_build=true via the hash check."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    (user_repo / "package.json").write_text('{"name":"x"}')
    lockfile = user_repo / "pnpm-lock.yaml"
    lockfile.write_text("lockfileVersion: '6.0'\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # First run: generate Dockerfile, record its sha.
    result1 = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result1.returncode == 0, result1.stderr
    df_content_v1 = result1.stdout
    assert "copy-input-shas:" in df_content_v1

    # Now simulate a lockfile change — the sha comment will differ in a new
    # generation. No manual Dockerfile removal needed: the generated file
    # carries the leerie-generated sentinel, so the launcher refreshes it
    # in place (GAP 4 auto-refresh).
    lockfile.write_text("lockfileVersion: '6.0'\n# changed\n")

    result2 = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",  # image present
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result2.returncode == 0, result2.stderr
    df_content_v2 = result2.stdout

    # The copy-input-shas line must differ between v1 and v2 (different sha).
    sha_line_v1 = next((l for l in df_content_v1.splitlines() if "copy-input-shas:" in l), "")
    sha_line_v2 = next((l for l in df_content_v2.splitlines() if "copy-input-shas:" in l), "")
    assert sha_line_v1 != sha_line_v2, "copy-input sha comment must differ when lockfile changes"


def test_no_lockfile_no_language_dep_layer(tmp_path):
    """Without any lockfile, only the apt layer is generated even with bake_language_deps=true."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    # No lockfile in user_repo.
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
            "LEERIE_BAKE_LANGUAGE_DEPS": "true",
        },
    )
    assert result.returncode == 0, result.stderr
    df_content = result.stdout
    assert "apt-get install" in df_content
    assert "COPY" not in df_content


def test_unrelated_file_change_no_rebuild(tmp_path):
    """An unrelated repo file change (not a lockfile) does not trigger a rebuild.

    The .dockerfile-hash is derived from Dockerfile content.  When the
    Dockerfile already exists and only a non-dep file (e.g. src/app.js)
    changes, the Dockerfile content is unchanged so the stored hash still
    matches and nerdctl build is NOT invoked.
    """
    import hashlib
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    df = leerie_dir / "Dockerfile"
    df.write_text("ARG BASE_IMAGE\nFROM $BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    # Store hash matching current Dockerfile.
    sha = hashlib.sha256(df.read_bytes()).hexdigest()
    version = "0.99.test"
    (state_dir / ".dockerfile-hash").write_text(f"{version}:{sha}\n")

    # Mutate an unrelated source file — not a lockfile.
    src_dir = user_repo / "src"
    src_dir.mkdir()
    (src_dir / "app.js").write_text("console.log('changed');\n")

    result = _run_harness(
        'echo "done"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",   # image present
            "NERDCTL_BUILD_RC": "1",     # would fail if invoked
            "FAKE_GIT_REMOTE": "",
            "LEERIE_VERSION": version,
        },
    )
    assert result.returncode == 0, result.stderr
    # No rebuild should have fired.
    assert "per-repo image up-to-date" in result.stderr
    assert "building per-repo container image" not in result.stderr


def test_language_dep_sentinel_strings_in_launcher():
    """Coupling assertion: the language-dep template sentinel strings exist
    verbatim in the live launcher source so the test stays tied to reality."""
    launcher_text = _launcher_text()
    # The sha comment format string must be present in the Python embedded block.
    assert "copy-input-shas:" in launcher_text
    # The pnpm install command used in the RUN layer.
    assert "pnpm install --frozen-lockfile" in launcher_text
    # The COPY+RUN emission path.
    assert "COPY " in launcher_text
    # bake_language_deps resolution variable.
    assert "_BAKE_LANGUAGE_DEPS" in launcher_text
    # Shared node-ancillary helper (GAP 2) and generated-Dockerfile sentinel (GAP 4).
    assert "_node_ancillary" in launcher_text
    assert "# leerie-generated" in launcher_text


# ---------------------------------------------------------------------------
# GAP 2 — COPY set complete for yarn AND npm (not just pnpm): workspace
# children (from package.json "workspaces"), .npmrc, and patches/.
# ---------------------------------------------------------------------------

def _make_workspace_repo(user_repo, lockfile_name, workspaces_field):
    """Build a node workspace repo with a child package, .npmrc, and a patch."""
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    import json
    (user_repo / "package.json").write_text(
        json.dumps({"name": "root", "workspaces": workspaces_field}))
    (user_repo / lockfile_name).write_text("# lock\n")
    (user_repo / ".npmrc").write_text("registry=https://example.com\n")
    child = user_repo / "packages" / "child"
    child.mkdir(parents=True)
    (child / "package.json").write_text('{"name":"@x/child"}')
    patches = user_repo / "patches"
    patches.mkdir()
    (patches / "foo.patch").write_text("--- a/f\n+++ b/f\n")


def _assert_full_node_copy(df_content, lockfile_name):
    assert lockfile_name in df_content
    assert "packages/child/package.json" in df_content
    assert ".npmrc" in df_content
    assert "patches/" in df_content
    # sha comment must include the patch + child manifest, not just the lockfile.
    assert "copy-input-shas:" in df_content
    assert "patches/foo.patch=" in df_content
    assert "packages/child/package.json=" in df_content


def test_language_dep_full_copy_for_yarn_workspace(tmp_path):
    """GAP 2: a yarn workspace repo COPYs workspace children, .npmrc, patches/
    (workspaces declared as a list in package.json)."""
    user_repo = tmp_path / "repo"
    _make_workspace_repo(user_repo, "yarn.lock", ["packages/*"])
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
    assert "RUN yarn install --frozen-lockfile" in result.stdout
    _assert_full_node_copy(result.stdout, "yarn.lock")


def test_language_dep_full_copy_for_npm_workspace(tmp_path):
    """GAP 2: an npm workspace repo COPYs workspace children, .npmrc, patches/
    (workspaces declared as a {"packages": [...]} object)."""
    user_repo = tmp_path / "repo"
    _make_workspace_repo(user_repo, "package-lock.json", {"packages": ["packages/*"]})
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
    assert "RUN npm ci" in result.stdout
    _assert_full_node_copy(result.stdout, "package-lock.json")


def test_patch_edit_changes_copy_input_sha(tmp_path):
    """GAP 3: editing a file under patches/ WITHOUT touching the lockfile must
    change the copy-input-shas line (so .dockerfile-hash flips → rebuild)."""
    user_repo = tmp_path / "repo"
    _make_workspace_repo(user_repo, "pnpm-lock.yaml", ["packages/*"])
    (user_repo / "pnpm-workspace.yaml").write_text("packages:\n  - 'packages/*'\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    env = {
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "NERDCTL_INSPECT_RC": "1",
        "NERDCTL_BUILD_RC": "0",
        "FAKE_GIT_REMOTE": "",
    }
    r1 = _run_harness('cat "$USER_REPO/.leerie/Dockerfile"', env=env)
    assert r1.returncode == 0, r1.stderr
    sha1 = next((l for l in r1.stdout.splitlines() if "copy-input-shas:" in l), "")

    # Edit the patch only; lockfile untouched. Generated Dockerfile refreshes
    # in place (sentinel), so no manual removal.
    (user_repo / "patches" / "foo.patch").write_text("--- a/f\n+++ b/f\n@@ changed\n")
    r2 = _run_harness('cat "$USER_REPO/.leerie/Dockerfile"',
                      env={**env, "NERDCTL_INSPECT_RC": "0"})
    assert r2.returncode == 0, r2.stderr
    sha2 = next((l for l in r2.stdout.splitlines() if "copy-input-shas:" in l), "")
    assert sha1 and sha2 and sha1 != sha2, "patch edit must change copy-input-shas"


# ---------------------------------------------------------------------------
# GAP 4 — generated Dockerfile carries a sentinel and refreshes in place;
# a hand-committed Dockerfile (no sentinel) is never overwritten.
# ---------------------------------------------------------------------------

def test_generated_dockerfile_has_sentinel(tmp_path):
    """The auto-generated Dockerfile's first line is the leerie-generated sentinel."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_harness(
        'head -1 "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.startswith("# leerie-generated")


def test_generated_dockerfile_refreshes_on_setup_packages_change(tmp_path):
    """GAP 4b: a changed setup_packages refreshes an existing *generated*
    Dockerfile in place (no manual removal), because it carries the sentinel."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    env = {
        "USER_REPO": str(user_repo),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "NERDCTL_INSPECT_RC": "1",
        "NERDCTL_BUILD_RC": "0",
        "FAKE_GIT_REMOTE": "",
    }
    r1 = _run_harness('cat "$USER_REPO/.leerie/Dockerfile"', env=env)
    assert r1.returncode == 0 and "curl" in r1.stdout

    # Change setup_packages; the generated Dockerfile must be refreshed.
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    r2 = _run_harness('cat "$USER_REPO/.leerie/Dockerfile"',
                      env={**env, "NERDCTL_INSPECT_RC": "0"})
    assert r2.returncode == 0, r2.stderr
    assert "postgresql" in r2.stdout, "generated Dockerfile must refresh from new setup_packages"


def test_committed_dockerfile_is_never_overwritten(tmp_path):
    """GAP 4: a hand-committed Dockerfile (no sentinel) is authoritative — the
    auto-gen block must leave it byte-identical even when config.toml exists."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    committed = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN echo hand-written\n"
    (leerie_dir / "Dockerfile").write_text(committed)
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
    assert result.stdout == committed, "committed Dockerfile must be untouched"
    assert "# leerie-generated" not in result.stdout


def _git_init_commit(repo: Path, path_rel: str) -> None:
    """Initialize a git repo at `repo` and commit the file at `path_rel`."""
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "add", path_rel], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=repo, check=True)


def test_committed_generated_dockerfile_with_sentinel_not_overwritten(tmp_path):
    """GAP 4 / D3: a COMMITTED (git-tracked) Dockerfile that carries the
    leerie-generated sentinel is authoritative (DESIGN §6½ "a committed
    Dockerfile always takes precedence") — the auto-gen guard must NOT
    regenerate it, even though setup_packages changed. Only an UNCOMMITTED
    generated file is refreshed in place. This is the concern-#7 edge that the
    sentinel-only guard got wrong."""
    sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    # A generated Dockerfile the user committed AND then hand-edited below the
    # sentinel (they left line 1 intact, not knowing it is load-bearing).
    committed = (
        sentinel + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
        "RUN echo MY-CUSTOM-LAYER\n"
    )
    (leerie_dir / "Dockerfile").write_text(committed)
    _git_init_commit(user_repo, ".leerie/Dockerfile")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # Change setup_packages — under the sentinel-only guard this would clobber.
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
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
    assert result.stdout == committed, (
        "a committed (git-tracked) generated Dockerfile must NOT be regenerated"
    )
    assert "postgresql" not in result.stdout, "committed file's custom layer clobbered"


def test_uncommitted_generated_dockerfile_still_refreshes_in_git_repo(tmp_path):
    """D3 complement: in a git repo, an UNTRACKED generated Dockerfile is still
    refreshed in place when setup_packages changes (git-tracked gate lets
    untracked ones through)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    # git repo exists, but the Dockerfile is NOT added/committed.
    subprocess.run(["git", "init", "-q"], cwd=user_repo, check=True)
    sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
    (leerie_dir / "Dockerfile").write_text(sentinel + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\nRUN apt-get install -y curl\nUSER leerie\n")

    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    result = _run_harness(
        'cat "$USER_REPO/.leerie/Dockerfile"',
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "0",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "postgresql" in result.stdout, (
        "untracked generated Dockerfile should refresh from new setup_packages"
    )
