"""Tests for `resolve_run_id`'s bare-`--resume` auto-pick (DESIGN §6).

Bare `--resume` used to require an explicit run-id as soon as a second run
existed — unusable on a repo with dozens of finished runs. It now picks the
most recent *resumable* run: `in-progress` / `paused` / `incomplete`.

The two guards below were both found by running the design against a real
58-run state dir before writing any code:
  - `seed-failed` rows carry no `started_at`; a naive newest-first sort put
    them on top and auto-picked a stale orphan over the user's live run.
  - finished runs (`done`, `done-pushed-pr`) dominate a real state dir
    (47 of 58) and must never be candidates.

Covers:
  - the newest in-progress run wins over older ones
  - finished / killed runs are never auto-picked
  - `seed-failed` is never auto-picked (needs operator attention first)
  - a row with no `started_at` never outranks a real timestamp
  - zero resumable runs => die with the candidate list
  - an explicit run-id still wins, and an unknown one still fails closed
"""
from __future__ import annotations

import json

import pytest


def _mk_run(root, run_id, *, started=None, run_json=None, state=None):
    """Create a run dir discover_runs() will find."""
    d = root / "runs" / run_id
    d.mkdir(parents=True, exist_ok=True)
    st = {"task": "t", "worker_count": 0}
    if started is not None:
        st["started_at"] = started
    if state:
        st.update(state)
    (d / "state.json").write_text(json.dumps(st))
    if run_json is not None:
        (d / "run.json").write_text(json.dumps(run_json))
    return d


def test_picks_newest_in_progress(leerie, tmp_path):
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    _mk_run(tmp_path, "b" * 64, started="2026-06-01T00:00:00+00:00")
    _mk_run(tmp_path, "c" * 64, started="2026-03-01T00:00:00+00:00")
    assert leerie.resolve_run_id(tmp_path, None, resumable_only=True) == "b" * 64


def test_finished_runs_are_never_auto_picked(leerie, tmp_path):
    """A `done-pushed-pr` run is newer but has nothing left to resume."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    _mk_run(tmp_path, "b" * 64, started="2026-06-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-06-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    # `finished_at` is read from the run.json sidecar, not state.json —
    # same shape the real crashed run on disk has.
    _mk_run(tmp_path, "c" * 64, started="2026-05-01T00:00:00+00:00",
            run_json={"finished_at": "2026-05-01T01:00:00+00:00"})
    assert leerie.resolve_run_id(tmp_path, None, resumable_only=True) == "a" * 64


def test_seed_failed_is_never_auto_picked(leerie, tmp_path):
    """`seed-failed` is resumable but needs a human decision first, and its
    rows carry no started_at — the exact shape that broke the naive sort."""
    d = tmp_path / "runs" / ("f" * 64)
    d.mkdir(parents=True)
    (d / "fly-machine.json").write_text(json.dumps({"id": "m1"}))  # orphan
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    assert leerie.resolve_run_id(tmp_path, None, resumable_only=True) == "a" * 64


def test_missing_started_at_never_outranks_a_real_timestamp(leerie, tmp_path):
    """A run with no started_at must sort BELOW one that has it, however
    the string comparison would otherwise fall."""
    _mk_run(tmp_path, "a" * 64, started=None)             # in-progress, no ts
    _mk_run(tmp_path, "b" * 64, started="2020-01-01T00:00:00+00:00")
    assert leerie.resolve_run_id(tmp_path, None, resumable_only=True) == "b" * 64


def test_no_resumable_runs_dies_with_the_list(leerie, tmp_path):
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-06-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)


def test_sole_resumable_run_is_picked(leerie, tmp_path):
    """The pre-existing single-run case still works."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    assert leerie.resolve_run_id(tmp_path, None, resumable_only=True) == "a" * 64


def test_explicit_run_id_still_wins(leerie, tmp_path):
    """An explicit id is honoured even when it is not the newest, and even
    when it is not otherwise auto-pickable (finished)."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-06-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    _mk_run(tmp_path, "b" * 64, started="2026-06-01T00:00:00+00:00")
    assert leerie.resolve_run_id(tmp_path, "a" * 64) == "a" * 64


def test_unknown_explicit_run_id_still_fails_closed(leerie, tmp_path):
    """Never silently resume a different run than the one named."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, "e" * 64)


# ---------------------------------------------------------------------------
# the read-only consumers (--report / --phase) must NOT get the filter
# ---------------------------------------------------------------------------

def test_report_resolves_a_finished_only_repo(leerie, tmp_path):
    """Regression: `--report` / `--phase` share resolve_run_id but are
    read-only — reporting telemetry on a *completed* run is the normal
    case. Applying the resumable-only filter to them broke a repo whose
    only run had finished (it used to auto-pick via len(runs)==1)."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-01-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    assert leerie.resolve_run_id(tmp_path, None) == "a" * 64


def test_report_picks_newest_regardless_of_status(leerie, tmp_path):
    """Without the filter, recency alone decides — a finished run that is
    newer than an in-progress one still wins for --report."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00")
    _mk_run(tmp_path, "b" * 64, started="2026-06-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-06-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    assert leerie.resolve_run_id(tmp_path, None) == "b" * 64
    # …while --resume skips it for the older run that still has work.
    assert leerie.resolve_run_id(
        tmp_path, None, resumable_only=True) == "a" * 64


def test_resume_caller_opts_into_the_filter(leerie):
    """Source-coupling guard: the filter only helps if --resume passes it.
    The other two call sites (--report, --phase) must NOT."""
    import inspect
    src = inspect.getsource(leerie.main)
    assert "resolve_run_id(leerie_root, args.run_id, resumable_only=True)" in src


def test_fixtures_are_valid_run_json(leerie, tmp_path):
    """Guard the fixtures themselves: `pr_url` requires `pushed_at`
    (_validate_run_json invariant 3). A pr_url-only sidecar derives as
    `corrupt-sidecar`, which is excluded for a *different* reason — so
    the finished-run tests above would pass without proving anything."""
    _mk_run(tmp_path, "a" * 64, started="2026-01-01T00:00:00+00:00",
            run_json={"pushed_at": "2026-01-01T02:00:00+00:00",
                      "pr_url": "https://example.com/pr/1"})
    rows = leerie.discover_runs(tmp_path)
    assert leerie._run_status_for(rows[0], tmp_path) == "done-pushed-pr"
