"""Tests for resolve_repo_image_tag() in the leerie launcher.

Verifies Phase-2 per-repo image identity and rebuild-trigger logic:
  - empty tag when no .leerie/Dockerfile and no setup_packages
  - tag format leerie-repo/<repo-id>:<version> when Dockerfile present
  - repo-id derived from git remote get-url origin owner/repo, with
    basename fallback when no remote
  - rebuild signals: image absent, Dockerfile hash mismatch, base version change
  - no rebuild when image present and hash matches

Strategy: embed the per-repo image block from the launcher in a _HARNESS
string with git, nerdctl image inspect, and sha256sum stubbed via shell
functions. A coupling test asserts that load-bearing sentinel lines co-occur
in both the harness and the live launcher — the same discipline used in
test_ensure_image.py.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Harness preamble: shell stubs injected before the launcher block.
# nerdctl image inspect exit code is controlled by $NERDCTL_INSPECT_RC.
# git remote get-url origin returns $FAKE_GIT_REMOTE (empty = no remote).
# sha256sum is the real binary; tests pre-write .dockerfile-hash to match
# or mismatch.
_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

remote_log() { echo "[leerie] $*" >&2; }

nerdctl() {
  local cmd="${1:-}"
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

_leerie_sha256() {
  local f="$1"
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$f" | awk '{print $1}'
  else
    shasum -a 256 "$f" | awk '{print $1}'
  fi
}

_leerie_repo_id() {
  local raw sanitized
  raw="$(git -C "$USER_REPO" remote get-url origin 2>/dev/null || true)"
  if [ -z "$raw" ]; then
    raw="$(basename "$USER_REPO")"
  else
    raw="${raw%.git}"
    raw="$(printf '%s' "$raw" | sed -E 's|.*[:/]([^/:]+/[^/:]+)$|\1|')"
  fi
  sanitized="$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | sed 's|[^a-z0-9._/]|-|g')"
  sanitized="$(printf '%s' "$sanitized" | tr '/' '-')"
  printf '%s' "$sanitized"
}

resolve_repo_image_tag() {
  local dockerfile="$USER_REPO/.leerie/Dockerfile"
  if [ ! -f "$dockerfile" ]; then
    local sp
    sp="$( { grep -E '^[[:space:]]*setup_packages[[:space:]]*=' \
                "$USER_REPO/.leerie/config.toml" 2>/dev/null \
            || true; } \
          | head -1 \
          | sed -E 's/^[[:space:]]*setup_packages[[:space:]]*=[[:space:]]*//;
                    s/[[:space:]]*$//;
                    s/^"(.*)"$/\1/;
                    s/^'"'"'(.*)'"'"'$/\1/')"
    if [ -z "$sp" ]; then
      echo ""
      return
    fi
  fi
  local repo_id
  repo_id="$(_leerie_repo_id)"
  echo "leerie-repo/${repo_id}:${LEERIE_VERSION}"
}

_leerie_dockerfile="$USER_REPO/.leerie/Dockerfile"
_leerie_config_toml="$USER_REPO/.leerie/config.toml"
REPO_IMAGE_TAG=""

# Auto-generate a Dockerfile from setup_packages if needed.
if [ ! -f "$_leerie_dockerfile" ] && [ -f "$_leerie_config_toml" ]; then
  _sp="$( { grep -E '^[[:space:]]*setup_packages[[:space:]]*=' \
                "$_leerie_config_toml" 2>/dev/null \
            || true; } \
          | head -1 \
          | sed -E 's/^[[:space:]]*setup_packages[[:space:]]*=[[:space:]]*//;
                    s/[[:space:]]*$//;
                    s/^"(.*)"$/\1/;
                    s/^'"'"'(.*)'"'"'$/\1/')"
  if [ -n "$_sp" ]; then
    _sp_packages="$(printf '%s' "$_sp" | tr ',' ' ' | tr -s ' ')"
    remote_log "auto-generating .leerie/Dockerfile from setup_packages: $_sp_packages"
    mkdir -p "$USER_REPO/.leerie"
    _gen_df_tmp="$USER_REPO/.leerie/.Dockerfile.gen.$$"
    printf 'ARG BASE_IMAGE\nFROM $BASE_IMAGE\nUSER root\nRUN apt-get update && apt-get install -y --no-install-recommends \\\n' \
      > "$_gen_df_tmp"
    for _pkg in $_sp_packages; do
      printf '    %s \\\n' "$_pkg" >> "$_gen_df_tmp"
    done
    printf '    && rm -rf /var/lib/apt/lists/*\nUSER leerie\n' >> "$_gen_df_tmp"
    mv "$_gen_df_tmp" "$_leerie_dockerfile"
    unset _gen_df_tmp _pkg
  fi
  unset _sp _sp_packages
fi
unset _leerie_config_toml

if [ -f "$_leerie_dockerfile" ]; then
  REPO_IMAGE_TAG="$(resolve_repo_image_tag)"
  if [ -n "$REPO_IMAGE_TAG" ]; then
    _hash_file="$LEERIE_STATE_HOST_DIR/.dockerfile-hash"
    _cur_df_sha="$(_leerie_sha256 "$_leerie_dockerfile")"
    _cur_hash="${LEERIE_VERSION}:${_cur_df_sha}"
    _stored_hash=""
    [ -f "$_hash_file" ] && _stored_hash="$(cat "$_hash_file" 2>/dev/null || true)"

    _need_build=false
    if ! nerdctl image inspect "$REPO_IMAGE_TAG" >/dev/null 2>&1; then
      _need_build=true
    elif [ "$_cur_hash" != "$_stored_hash" ]; then
      _need_build=true
    fi

    if [ "$_need_build" = "true" ]; then
      remote_log "building per-repo container image ($REPO_IMAGE_TAG)..."
    else
      remote_log "per-repo image up-to-date ($REPO_IMAGE_TAG); skipping build"
    fi
    unset _hash_file _cur_df_sha _cur_hash _stored_hash _need_build
  fi
fi
unset _leerie_dockerfile

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
        ["bash", "-c", _HARNESS + "\n" + body],
        env=base_env,
        capture_output=True,
        text=True,
    )


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
    result = _run(
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
    result = _run(
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
    result = _run(
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
    result = _run(
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
    result = _run(
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


# ---------------------------------------------------------------------------
# (d) Coupling test: harness sentinels must match the live launcher
# ---------------------------------------------------------------------------

def test_resolve_repo_image_tag_harness_matches_launcher():
    """Coupling test: sentinel lines must co-occur in both _HARNESS and the live launcher.

    Edit the _HARNESS in this file whenever you edit these lines in leerie.
    """
    launcher_text = (REPO_ROOT / "leerie").read_text()
    sentinels = [
        # resolve_repo_image_tag function body
        'local dockerfile="$USER_REPO/.leerie/Dockerfile"',
        'echo "leerie-repo/${repo_id}:${LEERIE_VERSION}"',
        # _leerie_repo_id function body
        'raw="$(git -C "$USER_REPO" remote get-url origin 2>/dev/null || true)"',
        'raw="$(basename "$USER_REPO")"',
        # rebuild-decision block
        '_hash_file="$LEERIE_STATE_HOST_DIR/.dockerfile-hash"',
        '_cur_hash="${LEERIE_VERSION}:${_cur_df_sha}"',
        'if ! nerdctl image inspect "$REPO_IMAGE_TAG" >/dev/null 2>&1;',
        'elif [ "$_cur_hash" != "$_stored_hash" ]; then',
        'remote_log "per-repo image up-to-date ($REPO_IMAGE_TAG); skipping build"',
    ]
    for s in sentinels:
        assert s in launcher_text, f"missing in launcher: {s!r}"
        assert s in _HARNESS, f"missing in harness: {s!r}"
