"""The launcher refuses a linked git worktree before any container launch."""
from __future__ import annotations

import subprocess
import sys

import pytest

from tests.launcher_stub_run import make_world, run_to_end

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")


def test_linked_worktree_refused(tmp_path):
    w = make_world(tmp_path)
    wt = tmp_path / "wt"
    subprocess.run(["git", "-C", str(w["repo"]), "worktree", "add", "-q",
                    "-b", "wtb", str(wt)], check=True)
    w2 = dict(w, repo=wt)
    rc, out = run_to_end(w2, ["--no-push"])
    assert rc != 0
    assert "linked git worktree" in out


def test_plain_clone_passes(tmp_path):
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push"])
    assert rc == 0, out
