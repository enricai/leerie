"""Tests for the per-run staging-dir (`~/.cache/leerie/cfg-*`) cleanup.

Each `./leerie` run stages a copy of `~/.claude` (creds + small config) into a
fresh `mktemp -d …/cfg-XXXXXX` and removes it on exit via an EXIT trap. A
SIGKILL (OOM, `nerdctl kill`) or hard crash bypasses the trap and leaks the
dir; historically 500+ leaked, tens of GB. Two defenses, both pinned here:

1. A startup sweep removes `cfg-*` dirs older than a day (reclaims trap-bypass
   leaks).
2. The EXIT trap that removes `$STAGE` is registered IMMEDIATELY after the
   `mktemp`, so the ~250 lines of stage assembly below it (which include an
   `exit 1` on bad config) can't leak the dir.

The sweep test is behavioral (real fixture dirs); the trap test is
source-coupling (the trap's placement relative to `mktemp` is the invariant).
"""
from __future__ import annotations

import re
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _extract_sweep_command() -> str:
    """Pull the `find … cfg-* -mtime +1 -exec rm -rf` sweep line from the
    launcher so the test runs the REAL command, not a copy."""
    src = LAUNCHER.read_text()
    m = re.search(r"\(\s*find \"\$HOME/\.cache/leerie\".*?cfg-\*.*?-exec rm -rf \{\} \+ \) </dev/null >/dev/null 2>&1",
                  src, re.DOTALL)
    assert m, "could not locate the cfg-* sweep command in the launcher"
    return m.group(0)


def test_startup_sweep_removes_old_but_keeps_fresh(tmp_path):
    """The sweep removes cfg-* dirs older than a day and leaves fresh ones and
    non-cfg dirs untouched."""
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    old = cache / "cfg-OLD001"
    fresh = cache / "cfg-NEW001"
    other = cache / "pnpm-store"  # a non-cfg cache dir must survive
    for d in (old, fresh, other):
        d.mkdir()
        (d / "marker").write_text("x")
    # Age `old` past the -mtime +1 (2 days) threshold; leave fresh/other now.
    two_days_ago = time.time() - 2 * 86400
    import os
    os.utime(old, (two_days_ago, two_days_ago))

    sweep = _extract_sweep_command()
    # Run the extracted sweep with HOME pointed at our fixture.
    subprocess.run(
        ["bash", "-c", sweep],
        env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
        check=True,
    )
    # Sweep is backgrounded (`&`) in the launcher; the extracted snippet keeps
    # the `( … ) &`, so wait briefly for it to finish.
    for _ in range(50):
        if not old.exists():
            break
        time.sleep(0.05)

    assert not old.exists(), "old cfg-* dir should be swept"
    assert fresh.exists(), "fresh cfg-* dir must be preserved"
    assert other.exists(), "non-cfg cache dirs must be preserved"


def test_stage_trap_registered_immediately_after_mktemp():
    """The EXIT trap that removes `$STAGE` (and kills the keepalive) must be
    registered right after the `STAGE=$(mktemp …)` line — before the
    stage-assembly block (which has an `exit 1`) — so an early exit can't leak
    the dir. Pin the ordering."""
    src = LAUNCHER.read_text()
    mktemp_pos = src.find('STAGE="$(mktemp -d "$HOME/.cache/leerie/cfg-XXXXXX")"')
    assert mktemp_pos != -1, "STAGE mktemp line not found"
    trap_pos = src.find('rm -rf "$STAGE" 2>/dev/null\' EXIT', mktemp_pos)
    assert trap_pos != -1, "early STAGE-removal EXIT trap not found after mktemp"
    # The trap must appear within a few lines of the mktemp (before the
    # ~250-line assembly block, well before the first `exit 1` in it).
    between = src[mktemp_pos:trap_pos]
    assert between.count("\n") < 16, (
        "the STAGE EXIT trap drifted far from mktemp; an early exit in the "
        "gap would leak the staging dir")


def test_all_stage_traps_kill_the_keepalive():
    """Every EXIT trap that removes `$STAGE` must also kill the keepalive
    (`$_stage_keepalive_pid`) — otherwise the hourly `touch $STAGE` loop
    outlives the run and leaks a background process."""
    src = LAUNCHER.read_text()
    # Each EXIT trap line that removes $STAGE must also reference the keepalive.
    for line in src.splitlines():
        if 'rm -rf "$STAGE"' in line and "EXIT" in line and line.strip().startswith("trap"):
            assert "_stage_keepalive_pid" in line, (
                f"STAGE-removing EXIT trap missing keepalive kill: {line.strip()}")


def test_keepalive_touch_protects_a_live_dir_from_the_sweep(tmp_path):
    """The keepalive `touch`es `$STAGE` so a live long-running dir never looks
    stale to the `-mtime +1` sweep, even when its nested files are old. Pin the
    behavior the keepalive relies on (top-level touch resets -mtime) so a future
    refactor can't silently reintroduce the frozen-mtime deletion of live dirs."""
    import os
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    live = cache / "cfg-LIVE"
    dead = cache / "cfg-DEAD"
    old = time.time() - 3 * 86400
    for d in (live, dead):
        (d / ".claude").mkdir(parents=True)
        (d / ".claude" / "f").write_text("x")
        os.utime(d / ".claude" / "f", (old, old))
        os.utime(d, (old, old))  # both dirs start stale
    (live).touch()  # keepalive freshens the live dir only

    sweep = _extract_sweep_command()
    subprocess.run(["bash", "-c", sweep],
                   env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"},
                   check=True)
    for _ in range(50):
        if not dead.exists():
            break
        time.sleep(0.05)
    assert live.exists(), "keepalive-touched (live) dir must survive the sweep"
    assert not dead.exists(), "stale (untouched) dir must be swept"
