"""F2: the pnpm store lives in the state mount, next to the worktrees
(DESIGN §6 *The pnpm store shares the worktrees' mount*).

pnpm hard-links from its store into node_modules, and a hard link cannot
cross a mount. With the store on its own cache mount every install copied,
and concurrent installs ran past their timeouts. The stores are now set by
the orchestrator under the state root (DESIGN §6 *The stores a view
installs from are leerie's*), so the launcher names none and mounts none.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = (REPO_ROOT / "leerie").read_text()
SEED_COMMON = (REPO_ROOT / "scripts" / "remote" / "seed-common.sh").read_text()


def _env_values(text: str, name: str) -> list[str]:
    return re.findall(rf'-e "{name}=([^"]+)"', text)


def test_the_launcher_names_no_store():
    """The orchestrator sets every store (DESIGN §6 *The stores a view
    installs from are leerie's*); a launcher value would be a store every
    worker of every earlier run wrote."""
    for name in ("npm_config_store_dir", "PIP_CACHE_DIR", "GOMODCACHE",
                 "CARGO_HOME", "COREPACK_HOME"):
        assert not _env_values(LAUNCHER, name), name
    assert 'child_env["npm_config_store_dir"]' not in LAUNCHER


def test_no_separate_store_mount_remains():
    """A second mount for a store is exactly what forced copying."""
    mounts = re.findall(r'-v "([^"]+)"', LAUNCHER)
    assert not [m for m in mounts if "pnpm-store" in m or "/.stores" in m]
    assert not [m for m in mounts
                if any(c in m for c in ("leerie/pip", "leerie/go-mod",
                                        "leerie/cargo", "leerie/corepack"))]


def test_both_sets_of_stores_are_in_the_state_root(leerie, tmp_path):
    """In the state root, so in the state mount locally (`/leerie-state`)
    and on the state volume on Fly (`/work/.leerie`), beside the worktrees:
    pnpm hard-links on both sides."""
    root = tmp_path / "state-root"
    env: dict[str, str] = {}
    leerie._leerie_stores(env, root)
    wenv = leerie._worker_cache_env(env, root / "runs" / "r1")
    for v in (env["npm_config_store_dir"], wenv["npm_config_store_dir"]):
        assert Path(v).is_relative_to(root), v
    # Leerie's is not in a run tree, which `leerie prune` and the reaper
    # delete; a run's worker store is, and goes with it.
    assert "/runs/" not in env["npm_config_store_dir"]
    assert Path(wenv["npm_config_store_dir"]).is_relative_to(
        root / "runs" / "r1")
    (run_root,) = re.findall(r'run_dir = "([^"]+)/runs/" \+ run_id',
                             SEED_COMMON)
    assert run_root.rstrip("/") == "/work/.leerie"
