"""Tests for `leerie version`.

The version is read from `.claude-plugin/plugin.json`'s `version` field
(single source of truth). The bare `version` verb (handled entirely by
the bash launcher, before any container starts — see `leerie:947`) must
exit 0 and print a string of the form `leerie <semver>`.

orchestrator/leerie.py's argparse deliberately has no `--version` flag:
the launcher's `version)` case arm always short-circuits before the
Python orchestrator is ever invoked, so a Python-level flag was dead
code duplicating the same plugin.json read (`_read_version()`, still
used internally for `state.json`'s `leerie_version` field).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
PLUGIN_JSON = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO_ROOT / ".claude-plugin" / "marketplace.json"


def test_version_verb_prints_plugin_json_version():
    expected = json.loads(PLUGIN_JSON.read_text())["version"]
    result = subprocess.run(
        [str(LAUNCHER), "version"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
    assert re.fullmatch(rf"leerie {re.escape(expected)}\s*", result.stdout), (
        f"unexpected version output: {result.stdout!r}"
    )
    assert re.match(r"\d+\.\d+\.\d+", expected), (
        f"plugin.json version is not semver-shaped: {expected!r}"
    )


def test_orchestrator_argparse_has_no_dash_version_flag():
    """`--version` must not survive as a Python-level flag (no dash-verbs
    anywhere, no shims — the bash launcher's bare `version` verb is the
    only entry point)."""
    leerie_py = REPO_ROOT / "orchestrator" / "leerie.py"
    result = subprocess.run(
        [sys.executable, str(leerie_py), "--version"],
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "unrecognized arguments" in result.stderr


def test_marketplace_version_matches_plugin_version():
    # plugin.json is the single source of truth for version
    # (_read_version() in orchestrator/leerie.py reads it). marketplace.json
    # duplicates the field for Claude Code's plugin browser. Guard against the
    # two drifting at release time.
    plugin_version = json.loads(PLUGIN_JSON.read_text())["version"]
    marketplace = json.loads(MARKETPLACE_JSON.read_text())
    marketplace_version = marketplace["plugins"][0]["version"]
    assert plugin_version == marketplace_version, (
        f"version drift: plugin.json={plugin_version!r}, "
        f"marketplace.json plugins[0].version={marketplace_version!r}"
    )
