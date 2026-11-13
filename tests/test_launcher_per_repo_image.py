"""Tests for per-repo derived image resolve/build in the leerie launcher.

Covers the bash functions added near the base-build block:
  _leerie_repo_id, resolve_repo_image_tag, build_repo_image,
  the rebuild-decision block, and setup_packages auto-generation.

Uses the same bash-harness subprocess pattern as
test_launcher_base_image_build_error.py and test_ensure_image.py.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.committed_tree import commit_tree
from tests.conftest import BUILDCTL_STUB, FRONTEND_SH_SOURCE, LOG_SH_SOURCE
from tests.repo_image_block_extract import (BUILT_DOCKERFILE,
                                            EMPTY_CONTEXT_SHA,
                                            NERDCTL_IMAGE_INSPECT_STUB,
                                            REPO_DIGEST)
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
  # leerie names its namespace on every call (LEERIE_IMAGE_NS).
  if [ "${1:-}" = "--namespace" ]; then shift 2; fi
  local cmd="${1:-}"; shift || true
  case "$cmd" in
    image)
      _stub_image_inspect "$@" && return 0
      return "${NERDCTL_INSPECT_RC:-0}"
      ;;
    build)
      # With FIRST_BUILD_OUT, keep a copy of the first build's Dockerfile
      # (`-f <build dir>/repo/Dockerfile`): the launch removes its build
      # directory before the test can read it.
      if [ -n "${FIRST_BUILD_OUT:-}" ]; then
        local _a _prev=""
        for _a in "$@"; do
          case "$_prev:$_a" in
            -f:*/repo/Dockerfile) /usr/bin/cp "$_a" "$FIRST_BUILD_OUT" ;;
          esac
          _prev="$_a"
        done
      fi
      _stub_build_done "$@"
      return "${NERDCTL_BUILD_RC:-0}"
      ;;
    run)
      echo "nerdctl-run: image=${REPO_IMAGE_REF:-$IMAGE_TAG}"
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
_HARNESS_PREFIX = (LOG_SH_SOURCE + FRONTEND_SH_SOURCE + BUILDCTL_STUB
                   + _HARNESS_PREFIX)


def _run_harness(body: str, env: dict | None = None) -> subprocess.CompletedProcess:
    launcher = _launcher_text()
    # Extract only the per-repo functions block from the launcher
    # (from its banner through the end of the unset _leerie_dockerfile line).
    marker_start = "# --- per-repo derived image (local nerdctl) "
    marker_end = "\n# --- assemble auth/config mounts"
    block = _extract_block(launcher, marker_start, marker_end)

    if env and env.get("USER_REPO"):
        commit_tree(env["USER_REPO"])
    script = (_HARNESS_PREFIX + NERDCTL_IMAGE_INSPECT_STUB + block + "\n"
              + body)

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
        '_repo_image_prepare "$IMAGE_TAG"\n'
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
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _run_harness(
        '_repo_image_prepare "$IMAGE_TAG"\n'
        'build_repo_image "leerie-repo/test:0.99.test"',
        env={"USER_REPO": str(user_repo), "NERDCTL_BUILD_RC": "1",
             "LEERIE_STATE_HOST_DIR": str(state_dir)},
    )
    assert result.returncode == 1
    assert "error: per-repo container image build failed" in result.stderr


def test_build_repo_image_error_message_matches_launcher():
    """The error message literal must match what's in the launcher source."""
    launcher_text = _launcher_text()
    assert '_repo_build_fail "per-repo container image build failed"' in launcher_text


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
    (state_dir / ".dockerfile-hash").write_text(
        f"{version}:worker-root-2:sha256:base-id:{sha}:{EMPTY_CONTEXT_SHA} {REPO_DIGEST}\n")

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
        'echo "done"; ' + BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    assert "generating the repository image's Dockerfile" in result.stderr
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
        BUILT_DOCKERFILE,
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
    assert "generating the repository image's Dockerfile" not in result.stderr
    # The committed Dockerfile content is preserved
    assert "# CUSTOM" in result.stdout


def test_nerdctl_run_uses_repo_image_ref_when_set(tmp_path):
    """The container runs the repository image by the digest this checkout
    built (REPO_IMAGE_REF), never by its tag; the base image otherwise."""
    launcher_text = _launcher_text()
    # The nerdctl run line must use the fallback expression
    assert '"${REPO_IMAGE_REF:-$IMAGE_TAG}"' in launcher_text
    assert '"${REPO_IMAGE_TAG:-$IMAGE_TAG}"' not in launcher_text


# ---------------------------------------------------------------------------
# The repository image holds no dependency (DESIGN §6½): only the apt layer
# ---------------------------------------------------------------------------

def test_setup_packages_generate_only_the_apt_layer(tmp_path):
    """setup_packages generate the apt layer and nothing else: no COPY, no
    `RUN` but the apt one, whatever lockfile the tree holds."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    (user_repo / "package.json").write_text('{"name":"test"}')
    (user_repo / "pnpm-lock.yaml").write_text("lockfileVersion: '6.0'\n")
    state_dir = tmp_path / "state"
    state_dir.mkdir()

    result = _run_harness(
        BUILT_DOCKERFILE,
        env={
            "USER_REPO": str(user_repo),
            "LEERIE_STATE_HOST_DIR": str(state_dir),
            "NERDCTL_INSPECT_RC": "1",
            "NERDCTL_BUILD_RC": "0",
            "FAKE_GIT_REMOTE": "",
        },
    )
    assert result.returncode == 0, result.stderr
    df_content = result.stdout
    assert "apt-get install" in df_content and "curl" in df_content
    assert "COPY" not in df_content
    assert df_content.count("RUN ") == 1, df_content


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
    (state_dir / ".dockerfile-hash").write_text(
        f"{version}:worker-root-2:sha256:base-id:{sha}:{EMPTY_CONTEXT_SHA} {REPO_DIGEST}\n")

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
        '{ ' + BUILT_DOCKERFILE + '; } | head -1',
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
    r1 = _run_harness(BUILT_DOCKERFILE, env=env)
    assert r1.returncode == 0 and "curl" in r1.stdout

    # Change setup_packages; the generated Dockerfile must be refreshed.
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    r2 = _run_harness(BUILT_DOCKERFILE,
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
        BUILT_DOCKERFILE,
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


SENTINEL = ("# leerie-generated: do not edit (regenerated from "
            ".leerie/config.toml)")
_ENV_KEYS = {"NERDCTL_INSPECT_RC": "1", "NERDCTL_BUILD_RC": "0",
             "FAKE_GIT_REMOTE": ""}


def _launch(user_repo: Path, state_dir: Path, body: str = BUILT_DOCKERFILE,
            **env: str) -> subprocess.CompletedProcess:
    return _run_harness(body, env={"USER_REPO": str(user_repo),
                                   "LEERIE_STATE_HOST_DIR": str(state_dir),
                                   **_ENV_KEYS, **env})


def _built_bytes(tmp_path: Path) -> bytes:
    """The bytes the stub `nerdctl build` was given as the first build's
    Dockerfile (`-f <dir>/repo/Dockerfile`), copied out (FIRST_BUILD_OUT)
    before the launch removed its build directory."""
    return (tmp_path / "first-build.Dockerfile").read_bytes()




def test_a_committed_generated_dockerfile_stops_the_launch(tmp_path):
    """OPEN-ISSUES #18 (DESIGN §6½ *A generated Dockerfile is leerie's
    output, never the repository's*): a COMMITTED file that carries
    leerie's mark froze the image — the launcher left a tracked one alone
    as the repository's, so later setup_packages edits never reached the
    image (navegando, api, funeralworks, barnacle, measured 2026-11-09).
    It is never an authority: the launch stops before any build, naming
    the file and both ways out, and writes nothing in the checkout.
    Falsified: the old tracked gate restored (exit 0, the stale file
    built)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    committed = (SENTINEL + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\n"
                 "RUN echo MY-CUSTOM-LAYER\n")
    (leerie_dir / "Dockerfile").write_text(committed)
    _git_init_commit(user_repo, ".leerie/Dockerfile")
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    log = tmp_path / "nerdctl.log"
    result = _launch(user_repo, state_dir, "echo REACHED-THE-END")
    assert result.returncode == 1, result.stderr
    assert "REACHED-THE-END" not in result.stdout
    assert ".leerie/Dockerfile is committed" in result.stderr, result.stderr
    assert "git rm .leerie/Dockerfile" in result.stderr, result.stderr
    assert "delete its first line" in result.stderr, result.stderr
    assert "building per-repo container image" not in result.stderr
    assert not log.exists()
    assert (leerie_dir / "Dockerfile").read_text() == committed
    assert sorted(p.name for p in leerie_dir.iterdir()) == [
        "Dockerfile", "config.toml"]


def test_a_committed_dockerfile_whose_mark_was_removed_is_the_repositorys(
        tmp_path):
    """The second way out of the refusal: the same committed file with its
    first line deleted is the repository's own, built byte for byte;
    setup_packages is not read, and the launch says so."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    mine = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN echo MY-CUSTOM-LAYER\n"
    (leerie_dir / "Dockerfile").write_text(mine)
    _git_init_commit(user_repo, ".leerie/Dockerfile")
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out = tmp_path / "first-build.Dockerfile"
    result = _launch(user_repo, state_dir, "true",
                     FIRST_BUILD_OUT=str(out))
    assert result.returncode == 0, result.stderr
    assert _built_bytes(tmp_path) == mine.encode()
    assert "setup_packages is not read while it is there" in \
        result.stderr, result.stderr
    assert "generating the repository image's Dockerfile" not in result.stderr


def test_an_untracked_generated_dockerfile_is_not_read(tmp_path):
    """An UNTRACKED file carrying the mark — what an earlier leerie wrote
    into the checkout and rewrote on every launch — is not read: the
    launch generates from config.toml, builds those bytes, warns that the
    file is not read, and leaves it as it was (the launcher writes nothing
    in the checkout)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=user_repo, check=True)
    stale = (SENTINEL + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n"
             "RUN apt-get install -y curl\n")
    (leerie_dir / "Dockerfile").write_text(stale)
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _launch(user_repo, state_dir)
    assert result.returncode == 0, result.stderr
    assert "postgresql" in result.stdout and "curl" not in result.stdout, \
        result.stdout
    assert "is a file an earlier leerie generated; it is not read" in \
        result.stderr, result.stderr
    assert (leerie_dir / "Dockerfile").read_text() == stale
    assert sorted(p.name for p in leerie_dir.iterdir()) == [
        "Dockerfile", "config.toml"]


def test_the_image_builds_the_generated_bytes_not_the_checkouts(tmp_path):
    """The first build is given the bytes this launch generated, from the
    build directory, never a file of the checkout: with a stale untracked
    copy in the checkout, the Dockerfile `nerdctl build -f` reads holds
    the configuration's packages. Falsified: _repo_image_prepare reading
    the checkout's file (the first build gets the stale copy)."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    subprocess.run(["git", "init", "-q"], cwd=user_repo, check=True)
    (leerie_dir / "Dockerfile").write_text(
        SENTINEL + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\n")
    (leerie_dir / "config.toml").write_text(
        'setup_packages = "ffmpeg postgresql-client"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    out = tmp_path / "first-build.Dockerfile"
    result = _launch(user_repo, state_dir, "true", FIRST_BUILD_OUT=str(out))
    assert result.returncode == 0, result.stderr
    built = _built_bytes(tmp_path).decode()
    assert built.startswith(SENTINEL + "\n"), built
    assert "    ffmpeg \\\n" in built and "    postgresql-client \\\n" in built, \
        built


def test_generation_writes_nothing_in_the_checkout(tmp_path):
    """Generation leaves the checkout's .leerie/ as the operator left it:
    no Dockerfile, no temporary file."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text('setup_packages = "curl"\n')
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _launch(user_repo, state_dir)
    assert result.returncode == 0, result.stderr
    assert "curl" in result.stdout
    assert sorted(p.name for p in leerie_dir.iterdir()) == ["config.toml"]


def test_a_submodules_generated_dockerfile_stops_the_launch(tmp_path):
    """Review round 31, S1's class: `.leerie` a submodule holding a generated
    Dockerfile. `git ls-files --error-unmatch .leerie/Dockerfile` fails in
    the superproject (the submodule is one gitlink entry); a file another
    repository holds is committed (`_checkout_file_tracked`), so a marked
    one stops the launch as a committed one does, and is left as it is."""
    committed = SENTINEL + "\nARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN echo MINE\n"
    ident = ["-c", "user.email=t@t.co", "-c", "user.name=t",
             "-c", "protocol.file.allow=always"]
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "config.toml").write_text('setup_packages = "postgresql"\n')
    (sub / "Dockerfile").write_text(committed)
    subprocess.run(["git", "init", "-q"], cwd=sub, check=True)
    subprocess.run(["git", "add", "-A"], cwd=sub, check=True)
    subprocess.run(["git", *ident, "commit", "-qm", "s"], cwd=sub, check=True)
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=user_repo, check=True)
    subprocess.run(["git", *ident, "submodule", "add", "-q", str(sub),
                    ".leerie"], cwd=user_repo, check=True)
    subprocess.run(["git", *ident, "commit", "-qm", "m"], cwd=user_repo,
                   check=True)
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    result = _launch(user_repo, state_dir, "echo REACHED-THE-END")
    assert result.returncode == 1, result.stderr
    assert "REACHED-THE-END" not in result.stdout
    assert ".leerie/Dockerfile is committed" in result.stderr, result.stderr
    assert (user_repo / ".leerie" / "Dockerfile").read_text() == committed
