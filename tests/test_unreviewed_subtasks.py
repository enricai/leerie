"""DESIGN §9: a subtask whose conformer never produced a result must be
distinguishable from one that passed.

Measured across every run on one host: **170 of 680 conformer attempts (25%)
produce no result at all**, and when one does run it reports a defect in
**17 of 510 invocations (3.3%)**. Run `fa979580` lost two conformers — one to
a stdin race that killed the worker in ~6 s, one to the 5400 s timeout after
90 minutes of re-running the suite — and both subtasks were recorded
`complete`, byte-identical in `subtask_status` to the six that were reviewed.

The fix is deliberately a *record*, not a new blocking status. The phase is
advisory by design, and at a 3.3% yield an unreviewed subtask is usually
fine. What it must not be is invisible.
"""
from __future__ import annotations

import inspect
import json


def test_state_key_is_declared(leerie):
    """Guard-the-guard for the generic parity sweep: an undeclared key fails
    `test_state_fields.py`, and this names the feature when it does."""
    assert "unreviewed_subtasks" in leerie.STATE_FIELDS


def test_declared_adjacent_to_conformance(leerie):
    """The two are only meaningful together — one records the review, the
    other records its absence. Adjacency is what stops one moving without
    the other (same reasoning as `leerie_commit` beside `leerie_version`)."""
    fields = list(leerie.STATE_FIELDS)
    assert abs(fields.index("unreviewed_subtasks")
               - fields.index("conformance")) == 1


def _conformance_write_sites(leerie) -> list[str]:
    """The bodies of every `conformance[sid] = {...}` write.

    Derived rather than enumerated, and the reason is a defect this file
    already caught: an earlier version of these tests took `split(...)[1]`
    and landed on the *first* site — the mid-run satisfied-rescue sentinel —
    while asserting properties of the second. There are two writers with
    deliberately different meanings, and a test that cannot tell them apart
    pins neither.
    """
    src = inspect.getsource(leerie)
    marker = 'st.data.setdefault("conformance", {})[sid] = {'
    out = []
    for chunk in src.split(marker)[1:]:
        # Bounded SEMANTICALLY, at the `st.save()` that closes each block,
        # rather than by a character count. A fixed window was tried twice
        # and truncated mid-statement both times — reporting a key as
        # missing when it was simply past the cutoff, which is a guard that
        # fails on correct code.
        end = chunk.find("st.save()")
        body = chunk[:end + len("st.save()")] if end != -1 else chunk
        # Comments are STRIPPED before scanning. Both bodies carry comments
        # that name `unreviewed_subtasks` while explaining why one of them
        # must not touch it, so a raw substring scan matches the prose
        # describing the thing it forbids and the negative assertions below
        # fail on correct code. Same trap the zombie-reaper guard documents
        # (it strips its docstring via `ast` for the identical reason).
        code = "\n".join(ln for ln in body.splitlines()
                         if not ln.strip().startswith("#"))
        out.append(code)
    return out


def test_recorded_with_a_reviewed_flag(leerie):
    """Source-coupled: driving the real path needs a live worktree, a git
    branch and a spawned conformer, so the write is pinned here and its
    semantics by the round-trip below."""
    sites = _conformance_write_sites(leerie)
    assert len(sites) == 2, f"expected 2 conformance write sites, got {len(sites)}"
    assert any('"reviewed": conf_res is not None' in s for s in sites)
    assert all('"reviewed"' in s for s in sites), \
        "every write site must set `reviewed`, or the key means nothing"


def test_satisfied_rescue_is_not_reported_as_unreviewed(leerie):
    """A zero-commit subtask has no diff to attack, so the conformer was
    correctly skipped — that is not the same as a review that crashed.

    Folding it into the operator warning would put a correct outcome in a
    list of concerns, which is how a warning becomes noise and stops being
    read.
    """
    sites = _conformance_write_sites(leerie)
    rescue = [s for s in sites if "satisfied" in s]
    assert len(rescue) == 1
    assert "unreviewed_subtasks" not in rescue[0]
    assert '"reviewed": False' in rescue[0]


def test_round_trips_through_state(leerie, tmp_path):
    """The in-memory dict a `resume` reads must reproduce the key."""
    root = tmp_path / ".leerie"
    (root / "runs" / "r1").mkdir(parents=True)
    st = leerie.State(root, "r1")
    st.data = {"task": "t", "worker_count": 0,
               "unreviewed_subtasks": ["bugfix-002", "bugfix-007"],
               "conformance": {"bugfix-002": {"result": None,
                                              "warnings": ["crashed"],
                                              "reviewed": False}}}
    st.save()
    on_disk = json.loads((root / "runs" / "r1" / "state.json").read_text())
    assert on_disk["unreviewed_subtasks"] == ["bugfix-002", "bugfix-007"]
    assert on_disk["conformance"]["bugfix-002"]["reviewed"] is False


def test_summary_names_the_unreviewed_subtasks(leerie):
    """A count the operator has to grep the log for is not surfacing.

    Pins that the line reports both the count and the ids, and that it is
    emitted only when there is something to report — a clean run must not
    grow a noise line.
    """
    src = inspect.getsource(leerie.phase_finalize)
    assert 'unreviewed = st.data.get("unreviewed_subtasks")' in src
    assert "if unreviewed:" in src
    assert "WITHOUT" in src and "join(sorted(unreviewed))" in src


def test_a_later_successful_review_clears_the_entry(leerie):
    """The record must be symmetric: appended on a crash, DISCARDED on a
    later success.

    `_settle_subtask` is a `while True:` loop and this block can run more
    than once per subtask (the completeness gate re-drives), so an
    append-only list would keep naming a subtask a later round did review.
    Tracing today's flow that ordering looks unreachable — a crashed
    conformer produces no `solution_defects`, so nothing re-drives after it
    — but the invariant is one line to hold unconditionally, and a
    reachability argument rots the moment a new continuation source lands.
    Pinned structurally because reaching the second round for real needs a
    live worktree and two spawned conformers.
    """
    site = [s for s in _conformance_write_sites(leerie)
            if "unreviewed_subtasks" in s][0]
    assert "elif sid in unreviewed:" in site
    assert "unreviewed.remove(sid)" in site


def test_no_duplicate_ids_recorded(leerie):
    """A subtask re-driven by the completeness gate reaches the recording
    site more than once; the operator should see it named once."""
    sites = _conformance_write_sites(leerie)
    crash_site = [s for s in sites if "unreviewed_subtasks" in s]
    assert len(crash_site) == 1
    assert "if sid not in unreviewed:" in crash_site[0]
