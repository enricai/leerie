"""A rescue ref is never overwritten (DESIGN §6; round-3 review L3).

`_rescue_integrator_work` wrote every rescue of a sid to the one ref
`refs/leerie/rescue/<run>/<sid>`, and `refs/leerie/*` keeps no reflog, so a
second crash replaced the first rescue — the one the die message had told
the operator to apply. Driven against real git repositories left
mid-merge.
"""
from __future__ import annotations

from tests.conftest import _run, run_git_cwd_kw as _git
from tests.test_rescue_integrator_work import (  # noqa: F401  (fixture)
    conflicted_repo)


def _rescue(leerie, repo, text, sid="feat-006"):
    (repo / "f.txt").write_text(text)
    return _run(leerie._rescue_integrator_work(repo, sid, "run1"))


def _show(repo, ref):
    return _git("show", f"{ref}:f.txt", cwd=repo).stdout


def test_a_second_crash_gets_its_own_ref(leerie, conflicted_repo):
    first = _rescue(leerie, conflicted_repo, "FIRST\n")
    second = _rescue(leerie, conflicted_repo, "SECOND\n")
    assert first == "refs/leerie/rescue/run1/feat-006/1"
    assert second == "refs/leerie/rescue/run1/feat-006/2"
    assert _show(conflicted_repo, first) == "FIRST\n"
    assert _show(conflicted_repo, second) == "SECOND\n"


def test_a_ref_that_exists_is_never_overwritten(leerie, conflicted_repo,
                                                monkeypatch):
    """Even when the name computed is taken, the create-only update-ref
    refuses, and the rescue reports nothing rather than replace it."""
    first = _rescue(leerie, conflicted_repo, "FIRST\n")
    monkeypatch.setattr(leerie, "_next_rescue_ref",
                        lambda staging, run_id, sid: first)
    assert _rescue(leerie, conflicted_repo, "SECOND\n") is None
    assert _show(conflicted_repo, first) == "FIRST\n"


def test_beside_a_flat_ref_from_an_older_leerie(leerie, conflicted_repo):
    """A flat `…/<sid>` ref cannot also be a directory: the new rescue goes
    beside it, and the old one is kept."""
    head = _git("rev-parse", "HEAD", cwd=conflicted_repo).stdout.strip()
    _git("update-ref", "refs/leerie/rescue/run1/feat-006", head,
         cwd=conflicted_repo)
    ref = _rescue(leerie, conflicted_repo, "NEW\n")
    assert ref == "refs/leerie/rescue/run1/feat-006.1"
    assert _git("rev-parse", "refs/leerie/rescue/run1/feat-006",
                cwd=conflicted_repo).stdout.strip() == head
    assert _rescue(leerie, conflicted_repo, "NEWER\n") == \
        "refs/leerie/rescue/run1/feat-006.2"
