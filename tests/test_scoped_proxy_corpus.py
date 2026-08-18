"""The measured basis for the `{test_files}` tier, frozen (DESIGN §9).

The design rests on one number: how often a real subtask diff carries a test
file. Get it wrong and the tier is either useless (proxy never resolves) or
unsafe (it resolves to something that measures nothing).

That number was originally taken from the planner's `files_likely_touched`
and was badly wrong — 40% test-touching by prediction (109 of 270) against 94%
in reality (34 of 36),
because CLAUDE.md requires tests and implementers add them whether or not the
planner predicted it. The fixture here is REAL per-subtask diffs recovered
from leerie's own run branches, so a future change to `_is_test_file` that
re-breaks the ratio fails with a message naming it.

Prior art: tests/test_wiring_repair_corpus.py.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

CORPUS = (Path(__file__).parent / "fixtures" / "scoped_proxy_corpus"
          / "corpus.json")
PROXY = "python3 -m pytest {test_files} -q"


@pytest.fixture(scope="module")
def corpus():
    return json.loads(CORPUS.read_text())["subtasks"]


def test_corpus_is_present_and_plural(corpus):
    """Anti-vacuity: a corpus that failed to load would pass every ratio
    assertion below by iterating nothing."""
    assert len(corpus) >= 25
    assert len({r["run"] for r in corpus}) >= 3, "needs >1 task type to de-bias"
    for r in corpus:
        assert r["files"], f"{r['sid']} has no files"


def test_the_proxy_resolves_for_the_measured_share(leerie, corpus):
    """34-of-36 at the time of writing. Pins the RATIO rather than a raw count:
    the corpus grows as runs complete, and an earlier revision of this fixture
    was a strict subset (a rebase had flattened one run's per-subtask
    boundaries), which moved the count without moving the claim."""
    resolved = [r for r in corpus
                if leerie._render_scoped(PROXY, r["files"], "base") is not None]
    share = len(resolved) / len(corpus)
    assert share >= 0.90, (
        f"only {len(resolved)}/{len(corpus)} ({share:.0%}) of real subtask "
        "diffs resolve a test-file proxy — below this the tier stops being "
        "worth its complexity and the canonical fallback is the common path")


def test_the_source_only_subtask_falls_back(leerie, corpus):
    """The other half of the contract, and the reason the fixture keeps a
    source-only row at all: a diff with no test file must render NOTHING so
    `_select_subtask_axes` uses the canonical command. A corpus of only
    test-touching diffs would pass the ratio test above while proving the
    safety property not at all."""
    src_only = [r for r in corpus
                if not any(f.startswith("tests/") for f in r["files"])]
    assert src_only, "fixture lost its source-only row; the fallback is untested"
    for r in src_only:
        assert leerie._render_scoped(PROXY, r["files"], "base") is None, r["sid"]


def test_no_resolved_command_carries_a_non_test_path(leerie, corpus):
    """The failure this tier exists to prevent: one non-test path makes pytest
    exit 4 and poisons the whole invocation, so a rendered command containing
    a docs or source path is a RED verdict for every subtask that has one."""
    for r in corpus:
        out = leerie._render_scoped(PROXY, r["files"], "base")
        if out is None:
            continue
        for f in r["files"]:
            if leerie._is_test_file(f):
                continue
            assert f not in out, f"{r['sid']}: non-test path {f} reached the command"


def test_every_rendered_command_names_at_least_one_test_file(leerie, corpus):
    for r in corpus:
        out = leerie._render_scoped(PROXY, r["files"], "base")
        if out is None:
            continue
        assert any(leerie._is_test_file(f) and f in out for f in r["files"]), r["sid"]
