"""Ownership of the host-wide `~/.leerie/.host` mount (`/leerie-host`), the
build semaphore's slot directory (DESIGN §6 *A host-wide build semaphore*).

The runtime rule (CLAUDE.md, `Dockerfile` rootless comment): under rootless
the privilege drop is `unshare --user --map-user=leerie`, a single-entry map
in which outer UID 0 — the host user, for a bind-mounted host dir — becomes
inner leerie, so the host user's own directory is writable as-is and a dir
chowned to leerie's literal uid would NOT be. Rootful's `runuser -u leerie` is
a real uid switch with no remap, so it needs literal leerie ownership, applied
at runtime in `container-entry.sh`'s rootful guard. Fly/EC2 have no such mount;
the orchestrator falls back to the state root there.

Paired: structural pins on the launcher/entry text, and an execution of the
real rootful-guard block with `chown`/`getent` faked onto PATH.
"""
from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

from tests.stub_helpers import _entry_sh_rootful_block

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
ENTRY_SH = REPO_ROOT / "scripts" / "container-entry.sh"
DOCKERFILE = REPO_ROOT / "Dockerfile"


def _run_argv_block() -> str:
    text = LAUNCHER.read_text()
    start = text.index("  _run_argv=(")
    return text[start:text.index("\n  )\n", start)]


def test_launcher_creates_the_host_dir_before_mounting_it():
    text = LAUNCHER.read_text()
    mkdir = text.index('mkdir -p "$HOME/.cache/leerie/mise-data"')
    created = text.index('"$HOME/.leerie/.host"', mkdir)
    mounted = text.index('-v "$HOME/.leerie/.host:/leerie-host"')
    assert mkdir < created < mounted


def test_launcher_mounts_it_and_names_it_after_the_env_forward():
    block = _run_argv_block()
    fwd = block.index('${_leerie_env_args[@]+"${_leerie_env_args[@]}"}')
    mount = block.index('-v "$HOME/.leerie/.host:/leerie-host"')
    env = block.index("-e LEERIE_HOST_DIR=/leerie-host")
    # After the LEERIE_* forward, so a host variable of the same name
    # cannot repoint the semaphore (a later -e wins).
    assert fwd < mount and fwd < env


def test_the_env_name_matches_the_orchestrator(leerie):
    assert leerie._HOST_DIR_ENV == "LEERIE_HOST_DIR"
    assert "-e LEERIE_HOST_DIR=/leerie-host" in _run_argv_block()


def test_rootful_guard_chowns_the_dir_and_only_the_dir():
    block = _entry_sh_rootful_block()
    assert "chown leerie: /leerie-host" in block
    assert "chown -R leerie: /leerie-host" not in block


def test_nothing_outside_the_rootful_guard_touches_its_ownership():
    text = ENTRY_SH.read_text()
    block = _entry_sh_rootful_block()
    outside = text.replace(block, "")
    assert "/leerie-host" not in outside
    assert "/leerie-host" not in DOCKERFILE.read_text()


def _run_guard(rootless: bool) -> list[str]:
    """Execute the REAL rootful-guard block with fake `chown`/`getent`, the
    mount path redirected into a temp dir; return the chown calls."""
    rootful_block = _entry_sh_rootful_block() + "\nfi\n"
    with tempfile.TemporaryDirectory() as tmp:
        t = Path(tmp)
        host = t / "leerie-host"
        host.mkdir()
        bin_dir = t / "bin"
        bin_dir.mkdir()
        log = t / "chown.log"
        (bin_dir / "getent").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "getent").chmod(0o755)
        (bin_dir / "chown").write_text(
            f'#!/bin/sh\necho "$@" >> "{log}"\nexit 0\n')
        (bin_dir / "chown").chmod(0o755)
        (bin_dir / "chmod").write_text("#!/bin/sh\nexit 0\n")
        (bin_dir / "chmod").chmod(0o755)
        script = ("#!/bin/sh\nset -e\n"
                  f'ROOTLESS={"true" if rootless else "false"}\n'
                  f'export PATH="{bin_dir}:$PATH"\n'
                  + rootful_block.replace("/leerie-host", str(host)))
        subprocess.run(["sh", "-c", script], check=True, cwd=t, timeout=10)
        calls = log.read_text().splitlines() if log.exists() else []
        return [c for c in calls if str(host) in c]


def test_rootful_execution_chowns_the_mount_to_leerie():
    calls = _run_guard(rootless=False)
    assert len(calls) == 1
    assert calls[0].split() == ["leerie:", calls[0].split()[-1]]


def test_rootless_execution_leaves_the_host_users_dir_alone():
    """Outer UID 0 is inner leerie under rootless: re-owning the dir to
    leerie's literal uid would make it unwritable there."""
    assert _run_guard(rootless=True) == []
