"""Tests for the per-run staging-dir (`~/.cache/leerie/cfg-*`) cleanup.

Each `./leerie` run stages a copy of `~/.claude` (creds + small config) into a
fresh `mktemp -d …/cfg-<launcher pid>-XXXXXX` and removes it on exit via an EXIT trap. A
SIGKILL (OOM, `nerdctl kill`) or hard crash bypasses the trap and leaks the
dir; historically 500+ leaked, tens of GB. Two defenses, both pinned here:

1. A startup sweep removes every `cfg-*` entry whose launch is over: one
   named for a launcher PID that no longer runs at once, any other once
   untouched for a day (DESIGN §6 *A staged copy of the credentials lives
   only as long as its launch*).
2. The EXIT trap that removes `$STAGE` is registered IMMEDIATELY after the
   `mktemp`, so the ~250 lines of stage assembly below it (which include an
   `exit 1` on bad config) can't leak the dir.

The sweep test is behavioral (real fixture dirs); the trap test is
source-coupling (the trap's placement relative to `mktemp` is the invariant).
"""
from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _sweep_function() -> str:
    """The launcher's real `_sweep_stale_stages`, so the tests run it, not a
    copy."""
    src = LAUNCHER.read_text()
    m = re.search(r"^_sweep_stale_stages\(\) \{\n.*?^\}\n", src,
                  re.DOTALL | re.MULTILINE)
    assert m, "could not locate _sweep_stale_stages in the launcher"
    return m.group(0)


def _sweep(home: Path) -> None:
    """Run the sweep in the foreground with HOME at `home`."""
    subprocess.run(["/bin/bash", "-c", _sweep_function() + "_sweep_stale_stages"],
                   env={"HOME": str(home), "PATH": "/usr/bin:/bin"},
                   check=True, timeout=30)


def _age(p: Path, hours: float) -> None:
    t = time.time() - hours * 3600
    os.utime(p, (t, t), follow_symlinks=False)


def _dead_pid() -> int:
    """A PID that ran and is gone (reaped)."""
    pr = subprocess.Popen(["/bin/true"])
    pr.wait()
    return pr.pid


def test_startup_sweep_removes_old_but_keeps_fresh(tmp_path):
    """A stage with no launcher PID in its name (a launcher from before the
    rule) goes once untouched for a day; fresh ones and non-cfg dirs stay."""
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    old = cache / "cfg-OLD001"
    fresh = cache / "cfg-NEW001"
    other = cache / "pnpm-store"  # a non-cfg cache dir must survive
    for d in (old, fresh, other):
        d.mkdir()
        (d / "marker").write_text("x")
    _age(old, 48)
    _age(other, 48)
    _sweep(tmp_path)
    assert not old.exists(), "old cfg-* dir should be swept"
    assert fresh.exists(), "fresh cfg-* dir must be preserved"
    assert other.exists(), "non-cfg cache dirs must be preserved"


def test_one_day_means_one_day(tmp_path):
    """Documented as a day: 30 h untouched goes (the old `-mtime +1` kept
    it to 48 h), 23 h stays."""
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    d30, d23 = cache / "cfg-Abc30h", cache / "cfg-Abc23h"
    for d, h in ((d30, 30), (d23, 23)):
        d.mkdir()
        _age(d, h)
    _sweep(tmp_path)
    assert not d30.exists() and d23.exists()


def test_every_entry_type_is_swept(tmp_path):
    """A 0-byte regular `cfg-*` file (one sat on the live host since
    2026-08-03) and a `cfg-*` link go too; a link goes as a link."""
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    f = cache / "cfg-KkTZzc"
    f.write_bytes(b"")
    target = tmp_path / "elsewhere"
    target.mkdir()
    (target / "keep").write_text("x")
    link = cache / "cfg-Link01"
    link.symlink_to(target)
    _age(f, 48)
    _age(link, 48)
    _sweep(tmp_path)
    assert not os.path.lexists(f) and not os.path.lexists(link)
    assert (target / "keep").exists(), "the sweep followed a link"


def test_a_stage_whose_launcher_is_gone_goes_at_once(tmp_path):
    """Named for its launcher: a dead launcher's stage goes however fresh;
    a live launcher's stays; a live PID's stage untouched for a day (a
    reused PID — a live launch's keepalive touches it hourly) goes."""
    cache = tmp_path / ".cache" / "leerie"
    cache.mkdir(parents=True)
    dead = cache / f"cfg-{_dead_pid()}-AbCdEf"
    live = cache / f"cfg-{os.getpid()}-AbCdEf"
    reused = cache / f"cfg-{os.getpid()}-ZyXwVu"
    for d in (dead, live, reused):
        (d / ".claude").mkdir(parents=True)
    _age(reused, 30)
    _sweep(tmp_path)
    assert not dead.exists(), "a dead launcher's stage must go at once"
    assert live.exists(), "a live launcher's stage must stay"
    assert not reused.exists()


def test_the_sweep_runs_in_the_background_holding_no_stdio():
    src = LAUNCHER.read_text()
    assert "( _sweep_stale_stages ) </dev/null >/dev/null 2>&1 &" in src


def test_stage_trap_registered_immediately_after_mktemp():
    """The EXIT trap that removes `$STAGE` (and kills the keepalive) must be
    registered right after the `STAGE=$(mktemp …)` line — before the
    stage-assembly block (which has an `exit 1`) — so an early exit can't leak
    the dir. Pin the ordering."""
    src = LAUNCHER.read_text()
    mktemp_pos = src.find('STAGE="$(mktemp -d "$HOME/.cache/leerie/cfg-$$-XXXXXX")"')
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
    stale to the `-mmin +1440` sweep, even when its nested files are old. Pin the
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

    _sweep(tmp_path)
    assert live.exists(), "keepalive-touched (live) dir must survive the sweep"
    assert not dead.exists(), "stale (untouched) dir must be swept"
