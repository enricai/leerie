"""The launcher's per-launch ~/.claude copy holds only what a worker reads.

Runs the real `$STAGE_CLI/.claude` assembly block (extracted from `leerie`,
in the style of tests/launcher_blocks.py) under bash against a fixture HOME,
once with rsync on PATH and once with rsync hidden (the cp fallback).
Names below are synthetic placeholders.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.launcher_blocks import LAUNCHER_PATH

_START = 'mkdir "$STAGE_CLI/.claude"\n'


def _full_block() -> str:
    src = LAUNCHER_PATH.read_text()
    i = src.index(_START)
    # the assembly ends at the credentials-step comment header
    j = src.index("# -- Extract OAuth credentials", i)
    return src[i:j]


def _fixture_home(home: Path) -> None:
    c = home / ".claude"
    for d in ("jobs", "projects", "sessions", "futurestore-xyz", "notallowed-dir"):
        (c / d).mkdir(parents=True)
        (c / d / "f.txt").write_text("x")
    for n in range(300):
        (c / "jobs" / f"job{n}").mkdir()
        (c / "jobs" / f"job{n}" / "log").write_text("x" * 100)
    (c / "settings.json").write_text("{}")
    (c / ".credentials.json").write_text("{}")
    for d in ("skills/s1", "agents", "commands"):
        (c / d).mkdir(parents=True, exist_ok=True)
    (c / "skills/s1/SKILL.md").write_text("s")
    (c / "agents/a.md").write_text("a")
    (c / "commands/c.md").write_text("c")
    p = c / "plugins"
    (p / "cache" / "big").mkdir(parents=True)
    (p / "marketplaces").mkdir()
    (p / "installed_plugins.json").write_text("{}")
    (p / "known_marketplaces.json").write_text("{}")


def _run(tmp_path: Path, with_rsync: bool) -> Path:
    home = tmp_path / "home"
    stage = tmp_path / "stage" / "cli"
    stage.mkdir(parents=True)
    _fixture_home(home)
    path = os.environ["PATH"]
    if not with_rsync:
        bindir = tmp_path / "bin"
        bindir.mkdir()
        for d in path.split(":"):
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                if f != "rsync" and not (bindir / f).exists():
                    try:
                        (bindir / f).symlink_to(Path(d) / f)
                    except OSError:
                        pass
        path = str(bindir)
    elif not shutil.which("rsync"):
        pytest.skip("rsync not installed")
    script = _full_block()
    r = subprocess.run(
        ["bash", "-c", "set -e\n" + script],
        env={"PATH": path, "HOME": str(home), "STAGE_CLI": str(stage)},
        capture_output=True, text=True,
    )
    assert r.returncode == 0, r.stderr
    return stage / ".claude"


@pytest.mark.parametrize("with_rsync", [True, False], ids=["rsync", "cp"])
def test_config_copy_is_an_allowlist(tmp_path, with_rsync):
    out = _run(tmp_path, with_rsync)
    for gone in ("jobs", "projects", "sessions", "futurestore-xyz", "notallowed-dir"):
        assert not (out / gone).exists(), f"{gone} leaked into the copy"
    assert not (out / "plugins" / "cache").exists()
    assert not (out / "plugins" / "marketplaces").exists()
    for kept in (
        "settings.json", ".credentials.json", "skills/s1/SKILL.md",
        "agents/a.md", "commands/c.md",
        "plugins/installed_plugins.json", "plugins/known_marketplaces.json",
    ):
        assert (out / kept).exists(), f"{kept} missing from the copy"
