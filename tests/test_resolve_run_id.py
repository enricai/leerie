"""Tests for `resolve_run_id()` — pick the run_id to operate on for
`--resume` / `--list`.

An explicit run-id is fails-closed: it must match exactly, and an unknown
one dies rather than falling back to a guess. Without one, the caller
chooses the policy (DESIGN §6): `--resume` passes `resumable_only=True`
and gets the most recent run that still has work
(`in-progress`/`paused`/`incomplete`); the read-only verbs (`--report`,
`--phase`) omit it and get the most recent run of any status, since
reporting on a finished run is the ordinary case.

This file used to pin "ambiguity is a hard error, never a heuristic
guess". That policy made bare `--resume` unusable the moment a second run
existed — on a real repo, 58 runs, 47 of them finished. DESIGN §6 was
amended; see `test_resolve_run_id_autopick.py` for the auto-pick contract
in full.

Cases:
- Zero runs → die.
- Exactly one run → auto-pick (common case for single-run users).
- Multiple runs, no --run-id → newest (filtered by status for --resume).
- No *resumable* run and resumable_only → die with the available list.
- Multiple runs, --run-id matches → use it.
- Multiple runs, --run-id doesn't match → die with the available list.

`resolve_run_id` exits via `die()` (which calls `sys.exit(1)`) on failures,
so we catch `SystemExit`."""
from __future__ import annotations

import json
from pathlib import Path

import pytest


def _make_run(leerie_root: Path, run_id: str, state: dict) -> None:
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(state))


def _make_finished_run(leerie_root: Path, run_id: str, started: str) -> None:
    """A run that has nothing left to resume (`done-pushed-pr`).

    `pushed_at` is mandatory alongside `pr_url`: `_validate_run_json`
    invariant 3 forbids a PR without a push, and a violating sidecar
    derives as `corrupt-sidecar` — which is excluded from the auto-pick
    for a *different* reason, so a pr_url-only fixture would make these
    tests pass without proving anything.
    """
    _make_run(leerie_root, run_id, {"task": "x", "started_at": started})
    (leerie_root / "runs" / run_id / "run.json").write_text(json.dumps({
        "pushed_at": started,
        "pr_url": f"https://example.com/pr/{run_id}",
    }))


def test_resolve_zero_runs_dies(leerie, tmp_path):
    """Empty `.leerie/runs/` is a hard error — there's nothing to resume."""
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None)


def test_resolve_single_run_auto_picks(leerie, tmp_path):
    """One run + no --run-id → use it. Preserves the existing
    single-run experience."""
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    assert leerie.resolve_run_id(tmp_path, None) == "feat-foo-abc123"


def test_resolve_single_run_explicit_match(leerie, tmp_path):
    """One run + matching --run-id → use it."""
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    assert leerie.resolve_run_id(tmp_path, "feat-foo-abc123") == "feat-foo-abc123"


def test_resolve_single_run_wrong_explicit_dies(leerie, tmp_path):
    """Even with only one run, a wrong --run-id dies — `--run-id` requires
    an exact match, never a fuzzy auto-pick."""
    _make_run(tmp_path, "feat-foo-abc123",
              {"task": "x", "started_at": "2026-05-26T10:00:00+00:00"})
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, "feat-bar-xyz999")


def test_resolve_multiple_runs_no_run_id_picks_newest(leerie, tmp_path):
    """Multiple in-flight runs resolve to the most recent one rather than
    dying — for both the --resume path and the read-only verbs."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"task": "a", "started_at": "2026-05-26T10:00:00+00:00"})
    _make_run(tmp_path, "feat-b-bbbbbb",
              {"task": "b", "started_at": "2026-05-26T11:00:00+00:00"})
    assert leerie.resolve_run_id(tmp_path, None) == "feat-b-bbbbbb"
    assert leerie.resolve_run_id(
        tmp_path, None, resumable_only=True) == "feat-b-bbbbbb"


def test_resolve_no_resumable_run_dies(leerie, tmp_path):
    """The fails-closed path survives: when every run is finished there is
    nothing to resume, so `--resume` dies rather than picking one."""
    _make_finished_run(tmp_path, "feat-a-aaaaaa",
                       "2026-05-26T10:00:00+00:00")
    _make_finished_run(tmp_path, "feat-b-bbbbbb",
                       "2026-05-26T11:00:00+00:00")
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)
    # …but the read-only verbs still resolve it (reporting on a finished
    # run is the normal case).
    assert leerie.resolve_run_id(tmp_path, None) == "feat-b-bbbbbb"


def test_resolve_multiple_runs_explicit_match(leerie, tmp_path):
    """With --run-id and multiple runs, the exact match wins."""
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"task": "a", "started_at": "2026-05-26T10:00:00+00:00"})
    _make_run(tmp_path, "feat-b-bbbbbb",
              {"task": "b", "started_at": "2026-05-26T11:00:00+00:00"})
    assert leerie.resolve_run_id(tmp_path, "feat-b-bbbbbb") == "feat-b-bbbbbb"


def test_resolve_multiple_runs_wrong_explicit_dies(leerie, tmp_path):
    _make_run(tmp_path, "feat-a-aaaaaa",
              {"task": "a", "started_at": "2026-05-26T10:00:00+00:00"})
    _make_run(tmp_path, "feat-b-bbbbbb",
              {"task": "b", "started_at": "2026-05-26T11:00:00+00:00"})
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, "feat-nope-zzz999")


def test_resolve_message_includes_available_runs(leerie, tmp_path, capsys):
    """When resolution fails, the error should list available run ids so
    the user can copy-paste one — not just 'try again'."""
    _make_finished_run(tmp_path, "feat-foo-abc123",
                       "2026-05-26T10:00:00+00:00")
    _make_finished_run(tmp_path, "fix-bar-def456",
                       "2026-05-26T11:00:00+00:00")
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)
    err = capsys.readouterr().err
    assert "feat-foo-abc123" in err
    assert "fix-bar-def456" in err


# --- Change 4: orphan run dirs (seed_auth failed before state.json) -----
# When seed_auth aborts before phase_classify completes, the launcher
# wrote .leerie/runs/<run-id>/fly-machine.json but the orchestrator
# never wrote state.json. Pre-Change-4, resolve_run_id rejected
# --run-id <orphan> with "does not match any known run" because
# discover_runs filtered the dir out, leaving users with no way to
# recover three of the four hung runs from the 2026-06-04 incident
# (the fourth had progressed far enough to write state.json).

def _make_orphan(leerie_root: Path, run_id: str, fly: dict) -> None:
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fly-machine.json").write_text(json.dumps(fly))


def test_resolve_explicit_orphan_id_accepted(leerie, tmp_path):
    """Orphan dirs (fly-machine.json without state.json) must resolve
    so the user can `--resume <orphan-id>` after seed_auth
    aborted before phase_classify. This is the live regression test for
    the stackpulse/finalmemoriam hangs."""
    _make_orphan(tmp_path, "feat-seed-died-abc123", {
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
    })
    assert leerie.resolve_run_id(
        tmp_path, "feat-seed-died-abc123"
    ) == "feat-seed-died-abc123"


def test_resolve_orphan_single_auto_picks_for_readonly_verbs(leerie, tmp_path):
    """An orphan is a real run — the read-only verbs (`--report`,
    `--phase`) auto-pick a lone one like any other."""
    _make_orphan(tmp_path, "feat-seed-died-abc123", {
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
    })
    assert leerie.resolve_run_id(tmp_path, None) == "feat-seed-died-abc123"


def test_resolve_lone_orphan_is_not_auto_resumed(leerie, tmp_path, capsys):
    """Deliberate behavior change: bare `--resume` no longer auto-picks a
    lone `seed-failed` orphan (it used to, when it was the only run).

    A seed-failed run aborted before `phase_classify` and needs an
    operator decision — re-seed or kill — because resuming blind can just
    re-trigger the same seed failure. So it is surfaced, not chosen. The
    die must remain actionable: it names the run, its status, and the
    explicit-id escape hatch, which is the documented recovery path for
    the 2026-06-04 hangs (CLAUDE.md: "resumable via `--resume <id>`")."""
    _make_orphan(tmp_path, "feat-seed-died-abc123", {
        "fly_machine_id": "287061da360d78",
        "started_at": "2026-06-04T19:20:58+00:00",
    })
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)
    err = capsys.readouterr().err
    assert "feat-seed-died-abc123" in err
    assert "status=seed-failed" in err
    assert "run-id" in err  # points at the escape hatch
    # …and the escape hatch itself still works.
    assert leerie.resolve_run_id(
        tmp_path, "feat-seed-died-abc123") == "feat-seed-died-abc123"


def test_resolve_orphan_with_healthy_runs_prefers_the_healthy_run(
        leerie, tmp_path):
    """When an orphan and a healthy run coexist, bare `--resume` picks the
    healthy one: a `seed-failed` orphan needs an operator decision first
    (re-seed vs. kill), so it is listed but never auto-picked.

    The orphan is still reachable by explicit id — see
    `test_resolve_explicit_orphan_id_accepted`, the recovery path for the
    2026-06-04 hangs."""
    _make_orphan(tmp_path, "feat-died-aaa111", {
        "fly_machine_id": "machine-aaa",
        "started_at": "2026-06-04T19:00:00+00:00",
    })
    _make_run(tmp_path, "feat-live-bbb222",
              {"task": "y", "started_at": "2026-06-04T20:00:00+00:00"})
    assert leerie.resolve_run_id(
        tmp_path, None, resumable_only=True) == "feat-live-bbb222"


# --- disambiguation hint: status + last-activity per row -----------------

def test_resolve_multiple_runs_message_includes_status(leerie, tmp_path,
                                                       capsys):
    """The disambiguation message must show the derived status of each
    run (from _derive_run_status) so the user can spot e.g. a
    `done-pushed-pr` run versus an `in-progress` one without an extra
    `leerie --list` invocation."""
    _make_finished_run(tmp_path, "feat-a-aaaaaa",
                       "2026-05-26T10:00:00+00:00")
    _make_finished_run(tmp_path, "feat-b-bbbbbb",
                       "2026-05-26T11:00:00+00:00")
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)
    err = capsys.readouterr().err
    # Both runs pushed a PR → done-pushed-pr, which is *why* there is
    # nothing to resume: the status shown is the reason for the die.
    assert "status=done-pushed-pr" in err


def test_resolve_multiple_runs_message_includes_last_activity(leerie, tmp_path,
                                                              capsys):
    """The disambiguation message must show how long ago each run's
    state.json was last touched so the user can spot a hung or
    abandoned run (last-activity hours/days ago) vs. a live one
    (seconds-to-minutes). The exact format is fuzzy (it depends on
    when the test runs vs. when the file was created), but the
    `last-activity=` prefix is pinned."""
    _make_finished_run(tmp_path, "feat-a-aaaaaa",
                       "2026-05-26T10:00:00+00:00")
    _make_finished_run(tmp_path, "feat-b-bbbbbb",
                       "2026-05-26T11:00:00+00:00")
    with pytest.raises(SystemExit):
        leerie.resolve_run_id(tmp_path, None, resumable_only=True)
    err = capsys.readouterr().err
    assert "last-activity=" in err
    # Just-created file should be 0s or seconds ago — never "?".
    assert "last-activity=?" not in err


# --- _format_age: short human-friendly duration --------------------------

def test_format_age_seconds(leerie):
    assert leerie._format_age(0) == "0s ago"
    assert leerie._format_age(5) == "5s ago"
    assert leerie._format_age(59) == "59s ago"


def test_format_age_minutes(leerie):
    assert leerie._format_age(60) == "1m ago"
    assert leerie._format_age(180) == "3m ago"
    assert leerie._format_age(3599) == "59m ago"


def test_format_age_hours(leerie):
    assert leerie._format_age(3600) == "1h ago"
    assert leerie._format_age(3600 + 720) == "1h12m ago"
    assert leerie._format_age(2 * 3600 + 5 * 60) == "2h05m ago"


def test_format_age_days(leerie):
    assert leerie._format_age(86400) == "1d ago"
    assert leerie._format_age(86400 + 4 * 3600) == "1d4h ago"
    assert leerie._format_age(5 * 86400) == "5d ago"


def test_format_age_negative_clamps_to_zero(leerie):
    """A negative duration (e.g. from clock skew) must not produce a
    nonsense string — clamp to 0s rather than render "-12s ago"."""
    assert leerie._format_age(-10) == "0s ago"
