"""F2: the pnpm store lives in the state mount, next to the worktrees
(DESIGN §6 *The pnpm store shares the worktrees' mount*).

pnpm hard-links from its store into node_modules, and a hard link cannot
cross a mount. With the store on its own cache mount every install copied,
and concurrent installs ran past their timeouts. These tests derive the
mount target and the run-directory root from the launcher and the remote
launch scaffold rather than restating them, so moving either without the
other fails here.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = (REPO_ROOT / "leerie").read_text()
SEED_COMMON = (REPO_ROOT / "scripts" / "remote" / "seed-common.sh").read_text()


def _env_values(text: str, name: str) -> list[str]:
    return re.findall(rf'-e "{name}=([^"]+)"', text)


def _state_mount_target() -> str:
    (target,) = re.findall(r'-v "\$LEERIE_STATE_HOST_DIR:([^"]+)"', LAUNCHER)
    return target


def test_local_store_is_inside_the_state_mount():
    (store,) = _env_values(LAUNCHER, "npm_config_store_dir")
    target = _state_mount_target()
    assert store.startswith(target.rstrip("/") + "/"), (store, target)
    # Not inside the run tree, which `leerie prune` and the reaper delete.
    assert "/runs/" not in store


def test_no_separate_pnpm_store_mount_remains():
    """A second mount for the store is exactly what forced copying."""
    mounts = re.findall(r'-v "([^"]+)"', LAUNCHER)
    assert not [m for m in mounts if "pnpm-store" in m]


def test_fly_store_is_inside_the_remote_state_root():
    (store,) = re.findall(
        r'child_env\["npm_config_store_dir"\] = "([^"]+)"', LAUNCHER)
    (run_root,) = re.findall(r'run_dir = "([^"]+)/runs/" \+ run_id',
                             SEED_COMMON)
    assert store.startswith(run_root.rstrip("/") + "/"), (store, run_root)
    assert "/runs/" not in store
