"""Operator-facing messages must name paths that exist on the operator's machine.

The orchestrator runs inside a container where the state root is bind-mounted at
`/leerie-state`, so a `die()` naming `<state-root>/runs/<id>/state.json` printed
a path the reader cannot open: `ls /leerie-state` fails on the host.

The launcher forwards the host side of that mount as
`LEERIE_STATE_HOST_DIR_DISPLAY`. The `_DISPLAY` suffix is load-bearing:
`LEERIE_STATE_HOST_DIR` is on the launcher's env deny-list precisely because a
host path is meaningless AS A PATH inside the container, and this value inherits
that restriction — it may be printed and must never be opened.

See docs/POSTMORTEM-2026-08-14.md, F17.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


@pytest.fixture
def host_dir(monkeypatch):
    monkeypatch.setenv("LEERIE_STATE_HOST_DIR_DISPLAY",
                       "/home/dev/.leerie/myrepo")


def test_translates_a_container_state_path(leerie, host_dir):
    assert leerie._operator_path(
        "/leerie-state/runs/abc123/state.json"
    ) == "/home/dev/.leerie/myrepo/runs/abc123/state.json"


def test_accepts_a_Path(leerie, host_dir):
    assert leerie._operator_path(
        Path("/leerie-state/runs/abc/logs")
    ) == "/home/dev/.leerie/myrepo/runs/abc/logs"


def test_a_trailing_slash_does_not_double_up(leerie, monkeypatch):
    monkeypatch.setenv("LEERIE_STATE_HOST_DIR_DISPLAY", "/home/dev/.leerie/x/")
    assert leerie._operator_path("/leerie-state/runs/a") == "/home/dev/.leerie/x/runs/a"


def test_unrelated_paths_are_untouched(leerie, host_dir):
    for p in ("/work/src/index.ts", "relative/path", "/tmp/x"):
        assert leerie._operator_path(p) == p


def test_absent_env_returns_the_input_unchanged(leerie, monkeypatch):
    """Any non-container invocation must be unaffected."""
    monkeypatch.delenv("LEERIE_STATE_HOST_DIR_DISPLAY", raising=False)
    assert leerie._operator_path("/leerie-state/runs/a") == "/leerie-state/runs/a"


def test_empty_env_returns_the_input_unchanged(leerie, monkeypatch):
    monkeypatch.setenv("LEERIE_STATE_HOST_DIR_DISPLAY", "   ")
    assert leerie._operator_path("/leerie-state/runs/a") == "/leerie-state/runs/a"


def test_operator_messages_use_the_helper(leerie):
    """The sweep: no operator message may interpolate a raw run-dir path.

    Comments are stripped first — the helper's own docstring necessarily names
    the container path it translates.
    """
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    code = "\n".join(l for l in src.splitlines()
                     if not l.lstrip().startswith("#"))
    offenders = [l.strip() for l in code.splitlines()
                 if re.search(r"\{st\.(path|run_dir)\}", l)]
    assert not offenders, (
        "these messages print a container path to an operator reading it on "
        "the host; wrap them in _operator_path():\n  " + "\n  ".join(offenders))


def test_the_launcher_forwards_the_display_value(leerie):
    src = LAUNCHER.read_text()
    assert "LEERIE_STATE_HOST_DIR_DISPLAY=${LEERIE_STATE_HOST_DIR:-}" in src, (
        "the container cannot know the host side of its own bind-mount unless "
        "the launcher tells it")


def test_the_real_name_stays_on_the_denylist(leerie):
    """Forwarding the display copy must not weaken the original rule.

    `LEERIE_STATE_HOST_DIR` is denied because a host path cannot be USED inside
    the container. That is still true, and the deny-list entry must stay.
    """
    src = LAUNCHER.read_text()
    m = re.search(r"_leerie_env_denylist=\"(.*?)\"", src, re.S)
    assert m, "could not find the env deny-list"
    assert "LEERIE_STATE_HOST_DIR " in m.group(1), (
        "the un-suffixed host path must remain denied to the auto-forward")
