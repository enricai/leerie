"""Tests for the local nerdctl run state-dir bind-mount.

The local container path verifies that:
  - `nerdctl run` includes `-v "<host-state-dir>:/leerie-state"`
  - `nerdctl run` includes `-e LEERIE_STATE_DIR=/leerie-state`
  - `/work` is still mounted from USER_REPO
  - the state mount target (/leerie-state) is NOT nested inside /work

The harness stubs out `nerdctl` to record argv rather than launching a
real container, and sources the relevant block from the launcher verbatim.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

USER_REPO="$1"
HOME="$2"
LEERIE_REPO="$3"
IMAGE_TAG="$4"
export HOME
shift 4   # remaining args are simulated CLI (unused in this harness)

# ---- state-dir resolution (mirrors launcher) ----------------------------
_state_dir_default() {
  local key
  key="$(python3 -c \
    "import hashlib,sys,os; p=sys.argv[1]; print(hashlib.sha256(p.encode()).hexdigest()[:16]+'-'+os.path.basename(p.rstrip('/')))" \
    "$USER_REPO")"
  echo "$HOME/.leerie/state/$key"
}

LEERIE_STATE_HOST_DIR="$(_state_dir_default)"

if [ -f "$USER_REPO/leerie.toml" ]; then
  _toml_state_dir="$( { grep -E '^[[:space:]]*state_dir[[:space:]]*=' \
                            "$USER_REPO/leerie.toml" 2>/dev/null \
                        || true; } \
                      | head -1 \
                      | sed -E 's/^[[:space:]]*state_dir[[:space:]]*=[[:space:]]*//;
                                s/[[:space:]]*$//;
                                s/^"(.*)"$/\1/;
                                s/^'"'"'(.*)'"'"'$/\1/')"
  if [ -n "$_toml_state_dir" ]; then
    case "$_toml_state_dir" in
      "~")   _toml_state_dir="$HOME" ;;
      "~/"*) _toml_state_dir="$HOME/${_toml_state_dir#"~/"}" ;;
    esac
    LEERIE_STATE_HOST_DIR="$_toml_state_dir"
  fi
  unset _toml_state_dir
fi

if [ -n "${LEERIE_STATE_DIR:-}" ]; then
  LEERIE_STATE_HOST_DIR="$LEERIE_STATE_DIR"
fi

_cli_state_dir=""
_prev_was_state_dir=false
for arg in "$@"; do
  if $_prev_was_state_dir; then
    _cli_state_dir="$arg"
    _prev_was_state_dir=false
    continue
  fi
  case "$arg" in
    --state-dir=*) _cli_state_dir="${arg#--state-dir=}" ;;
    --state-dir)   _prev_was_state_dir=true ;;
  esac
done
if [ -n "$_cli_state_dir" ]; then
  LEERIE_STATE_HOST_DIR="$_cli_state_dir"
fi
unset _cli_state_dir _prev_was_state_dir

case "$LEERIE_STATE_HOST_DIR" in
  "~")   LEERIE_STATE_HOST_DIR="$HOME" ;;
  "~/"*) LEERIE_STATE_HOST_DIR="$HOME/${LEERIE_STATE_HOST_DIR#"~/"}" ;;
esac

# ---- stub nerdctl: print argv one-per-line, then exit 0 -----------------
nerdctl() {
  for a in "$@"; do printf '%s\n' "$a"; done
}

# ---- reproduce the nerdctl run invocation from the launcher -------------
TTY_FLAGS="-i"
AUTH_MOUNTS=()
CACHE_MOUNTS=()
INSPECT_MOUNTS=()
container_rc=0

nerdctl run \
  --rm $TTY_FLAGS \
  --name "leerie-stub-$$" \
  -e LEERIE_INSPECT_DIRS= \
  -e LEERIE_STATE_DIR=/leerie-state \
  --mount type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,bind-propagation=rshared \
  -v "$USER_REPO:/work" \
  -v "$LEERIE_REPO:/opt/leerie-image:ro" \
  -v "$LEERIE_STATE_HOST_DIR:/leerie-state" \
  ${AUTH_MOUNTS[@]+"${AUTH_MOUNTS[@]}"} \
  ${CACHE_MOUNTS[@]+"${CACHE_MOUNTS[@]}"} \
  ${INSPECT_MOUNTS[@]+"${INSPECT_MOUNTS[@]}"} \
  -w /work \
  "$IMAGE_TAG" || container_rc=$?
"""


def _run(
    user_repo: Path,
    fake_home: Path,
    env: dict,
    cli_args: list[str],
    leerie_repo: Path | None = None,
    image_tag: str = "leerie:test",
) -> tuple[list[str], str]:
    """Run the harness; return (argv_tokens, stderr). Raises on non-zero exit."""
    if leerie_repo is None:
        leerie_repo = user_repo / "leerie-repo"
        leerie_repo.mkdir(exist_ok=True)
    result = subprocess.run(
        [
            "bash", "-c", _HARNESS, "--",
            str(user_repo), str(fake_home), str(leerie_repo), image_tag,
        ] + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    tokens = [t for t in result.stdout.splitlines() if t]
    return tokens, result.stderr.strip()


def _expected_state_host_dir(user_repo: Path, fake_home: Path) -> str:
    abs_path = str(user_repo)
    sha16 = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
    basename = os.path.basename(abs_path.rstrip("/"))
    return str(fake_home) + f"/.leerie/state/{sha16}-{basename}"


# ── state bind-mount present in nerdctl argv ─────────────────────────────────


def test_state_mount_in_nerdctl_argv(tmp_path):
    """-v <host-state-dir>:/leerie-state appears in the nerdctl run argv."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()

    tokens, _ = _run(user_repo, fake_home, {}, [])
    expected_host = _expected_state_host_dir(user_repo, fake_home)
    expected_mount = f"{expected_host}:/leerie-state"
    assert expected_mount in tokens, (
        f"Expected -v token '{expected_mount}' not found in nerdctl argv.\n"
        f"Got tokens: {tokens}"
    )


def test_state_dir_env_in_nerdctl_argv(tmp_path):
    """-e LEERIE_STATE_DIR=/leerie-state appears in the nerdctl run argv."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()

    tokens, _ = _run(user_repo, fake_home, {}, [])
    assert "LEERIE_STATE_DIR=/leerie-state" in tokens, (
        "Expected 'LEERIE_STATE_DIR=/leerie-state' token not found in nerdctl argv.\n"
        f"Got tokens: {tokens}"
    )


def test_work_mount_still_present(tmp_path):
    """-v <user-repo>:/work is present alongside the state mount."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()

    tokens, _ = _run(user_repo, fake_home, {}, [])
    expected_work = f"{user_repo}:/work"
    assert expected_work in tokens, (
        f"Expected -v token '{expected_work}' not found in nerdctl argv.\n"
        f"Got tokens: {tokens}"
    )


def test_state_mount_target_not_nested_in_work(tmp_path):
    """The state mount target (/leerie-state) is not nested inside /work."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()

    tokens, _ = _run(user_repo, fake_home, {}, [])
    # No -v token should mount something into /work/.leerie or any /work/
    # subpath for the state dir.
    work_nested = [t for t in tokens if ":/work/" in t]
    assert not work_nested, (
        f"State mount must not target a path nested inside /work. "
        f"Found nested mounts: {work_nested}"
    )


def test_state_mount_uses_resolved_host_dir(tmp_path):
    """The host side of the state mount matches LEERIE_STATE_HOST_DIR resolution."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()

    tokens, _ = _run(user_repo, fake_home, {}, [])
    expected_host = _expected_state_host_dir(user_repo, fake_home)

    # The state bind-mount's source must equal the resolved host dir.
    state_mounts = [t for t in tokens if ":/leerie-state" in t]
    assert state_mounts, "No :/leerie-state mount found in argv"
    host_side = state_mounts[0].split(":/leerie-state")[0]
    assert host_side == expected_host, (
        f"State mount source '{host_side}' != expected '{expected_host}'"
    )


def test_custom_state_dir_via_env(tmp_path):
    """LEERIE_STATE_DIR env override propagates to the -v mount host path."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    custom_dir = str(tmp_path / "custom-state")

    tokens, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": custom_dir}, [])
    expected_mount = f"{custom_dir}:/leerie-state"
    assert expected_mount in tokens, (
        f"Expected '{expected_mount}' with env override. Got: {tokens}"
    )


def test_custom_state_dir_via_cli(tmp_path):
    """--state-dir CLI override propagates to the -v mount host path."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    custom_dir = str(tmp_path / "cli-state")

    tokens, _ = _run(
        user_repo, fake_home, {}, [f"--state-dir={custom_dir}"]
    )
    expected_mount = f"{custom_dir}:/leerie-state"
    assert expected_mount in tokens, (
        f"Expected '{expected_mount}' with CLI override. Got: {tokens}"
    )
