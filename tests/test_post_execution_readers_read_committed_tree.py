"""Review round 9 suspicion (b): the delivery judges and the held-out
re-measure run with staging as their cwd, which can hold files that do not
ship. Proved equivalent rather than moved (DESIGN §8 *Every
post-execution verdict is of the committed tree*): every check that can
turn a verdict into "met" reads staging's HEAD, never its working tree.
These pin that with staging dirty both ways — content only the working
tree has, and committed content the working tree has lost."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_redrive_test_id_diff_refresh import _git  # noqa: E402

COMMITTED = "def committed_behaviour():\n    return 'shipped'\n"
UNCOMMITTED = "def staged_only_behaviour():\n    return 'never ships'\n"


def _staging(tmp_path):
    s = tmp_path / "s"
    s.mkdir()
    _git(s, "init", "-q", "-b", "main")
    _git(s, "config", "user.email", "t@t")
    _git(s, "config", "user.name", "t")
    (s / "keep.py").write_text("K = 1\n")
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "base")
    start = _git(s, "rev-parse", "HEAD")
    (s / "mod.py").write_text(COMMITTED)
    _git(s, "add", "-A")
    _git(s, "commit", "-qm", "feature")
    # Dirt that does not ship: a tracked edit replacing the committed
    # function, an untracked file, and an edit to a "stays unchanged" path.
    (s / "mod.py").write_text(UNCOMMITTED)
    (s / "extra.py").write_text(UNCOMMITTED)
    (s / "keep.py").write_text("K = 2\n")
    return s, start


def _met(quote, path="mod.py"):
    return {"met": True, "parts": [{"status": "met", "cites": [
        {"path": path, "line_start": 1, "line_end": 2, "quote": quote}]}]}


def test_a_met_needs_a_citation_from_the_committed_tree(leerie, tmp_path):
    s, _ = _staging(tmp_path)
    check = leerie._delivery_verdict_check
    # A quote only the working tree holds buys no met ...
    assert check(_met("return 'never ships'"), s, "")[0] == "unverified"
    assert check(_met("return 'never ships'", "extra.py"), s,
                 "")[0] == "unverified"
    # ... and a committed quote the working tree lost still verifies.
    assert check(_met("return 'shipped'"), s, "") == ("met", "")


def test_absent_checks_read_the_committed_tree(leerie, tmp_path):
    s, _ = _staging(tmp_path)
    cite = leerie._verify_delivery_cite
    assert cite({"path": "ABSENT", "quote": "staged_only_behaviour"},
                s, "") == "absent_ok"
    assert cite({"path": "ABSENT", "quote": "committed_behaviour"},
                s, "") == "absent_found"


def test_untouched_paths_read_the_committed_history(leerie, tmp_path):
    s, start = _staging(tmp_path)
    assert leerie._d1_untouched_verdict(s, start, ["keep.py"]) == {
        "met": True, "files": []}
    assert leerie._d1_untouched_verdict(s, start, ["mod.py"]) == {
        "met": False, "files": ["mod.py"]}
