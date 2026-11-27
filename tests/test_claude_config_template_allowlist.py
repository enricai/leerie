"""The run's CLI configuration template holds only what a worker reads, and
the launcher's copy keeps the same names (DESIGN §6 *What a config directory
holds*). Real `_claude_config_template` against a fixture HOME."""
from __future__ import annotations

import json
import re

import pytest

from tests.conftest import REPO_ROOT

READ = {
    "settings.json": "{}", "settings.local.json": "{}",
    ".credentials.json": '{"k": 1}', "CLAUDE.md": "# hi",
    "keybindings.json": "{}",
}
READ_DIRS = ("skills", "agents", "commands", "hooks")
STORES = ("jobs", "projects", "sessions", "history-store-unnamed",
          "todos", "shell-snapshots", "file-history")


@pytest.fixture()
def home(tmp_path, monkeypatch):
    h = tmp_path / "home"
    c = h / ".claude"
    c.mkdir(parents=True)
    for n, body in READ.items():
        (c / n).write_text(body)
    for d in READ_DIRS:
        (c / d).mkdir()
        (c / d / "x.md").write_text("x")
    (c / "plugins").mkdir()
    (c / "plugins" / "installed_plugins.json").write_text("{}")
    (c / "plugins" / "known_marketplaces.json").write_text("{}")
    for s in STORES:
        (c / s).mkdir()
        for i in range(50):
            (c / s / f"{i}.jsonl").write_text("secret transcript")
    (c / "jobs" / "deep" / "er").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    return h


@pytest.fixture()
def run_dir(tmp_path, leerie):
    r = tmp_path / "run"
    r.mkdir()
    yield r
    leerie._rmtree_owned(r)   # the template is read-only


def test_template_holds_only_what_a_worker_reads(leerie, home, run_dir):
    t = leerie._claude_config_template(run_dir)
    for s in STORES:
        assert not (t / s).exists(), s
    for n in READ:
        assert (t / n).is_file(), n
    for d in READ_DIRS:
        assert (t / d / "x.md").is_file(), d
    assert (t / "plugins" / "installed_plugins.json").is_file()
    assert (t / "plugins" / "known_marketplaces.json").is_file()
    assert json.loads((t / ".credentials.json").read_text()) == {"k": 1}


def test_launcher_and_orchestrator_keep_the_same_names(leerie):
    text = (REPO_ROOT / "leerie").read_text()
    m = re.search(r"^CLAUDE_READS=\(\s*(.*?)\)", text, re.S | re.M)
    assert m, "launcher CLAUDE_READS not found"
    launcher = set(m.group(1).split())
    orch = set(leerie._CLAUDE_CONFIG_READS)
    # `.claude.json` is the orchestrator's own seeded state file (copied
    # from ~/.claude.json), not an entry of the launcher's ~/.claude copy.
    assert orch - {".claude.json"} == launcher
