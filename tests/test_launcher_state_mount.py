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

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"

# Both halves are EXTRACTED from the launcher, not reproduced. The copy this
# replaced was materially stale against `leerie`'s real `_run_argv` array —
# it inlined one of three `CGROUP_MOUNT_ARG` branches as a literal and was
# missing `--cidfile` and its bind, `--label`, `-e USER_REPO=`,
# `-e LEERIE_COMMIT=`, the `LEERIE_*` auto-forward, `--cgroupns=host`,
# `ROOTLESS_SECOPT`, `REWRITTEN_ARGS`, and `${REPO_IMAGE_TAG:-$IMAGE_TAG}`.
# None of that flipped an assertion, which is the point: the tests were
# blind, not wrong. They would pass identically if the launcher dropped the
# state mount tomorrow — the exact regression this file exists to catch.
from tests.test_resolve_state_dir import _extract_state_dir_block


def _extract_run_argv() -> str:
    """The real `nerdctl run` argv array, lifted verbatim. Same marker pair
    `tests/test_launcher_env_forwarding.py` already uses."""
    src = LAUNCHER.read_text()
    m = re.search(r"(  _run_argv=\(\n.*?\n  \)\n)", src, re.DOTALL)
    assert m, "could not locate the _run_argv array in the launcher"
    return m.group(1)


# The extracted argv references many launcher-scope variables; stub the ones
# this test does not vary, and let the real ones (USER_REPO, LEERIE_REPO,
# LEERIE_STATE_HOST_DIR) flow through from the resolution block above.
_HARNESS = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    'USER_REPO="$1"\n'
    'HOME="$2"\n'
    'LEERIE_REPO="$3"\n'
    'IMAGE_TAG="$4"\n'
    "export HOME\n"
    "shift 4\n"
    + _extract_state_dir_block()
    + """
nerdctl() { for a in "$@"; do printf '%s\\n' "$a"; done; }
TTY_FLAGS="-i"
_cidfile="/dev/null"
REPO_IMAGE_TAG=""
LEERIE_COMMIT=""
AUTH_MOUNTS=()
CACHE_MOUNTS=()
INSPECT_MOUNTS=()
CGROUP_MOUNT_ARG=()
ROOTLESS_SECOPT=()
REWRITTEN_ARGS=()
_leerie_env_args=()
"""
    + _extract_run_argv()
    + '\nnerdctl run "${_run_argv[@]}"\n'
)


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
    basename = os.path.basename(str(user_repo).rstrip("/"))
    return str(fake_home) + f"/.leerie/{basename}"


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
