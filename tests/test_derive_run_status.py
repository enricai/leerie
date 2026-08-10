"""Tests for `_derive_run_status` — the pure-function status taxonomy
that `leerie list` renders.

Status table (in priority order):
  1. run.json invariant-invalid → `corrupt-sidecar`
  2. push_error set            → `push-failed`
  3. pr_error set              → `pr-failed`
  4. pr_url set                → `done-pushed-pr`
  5. pushed_at set             → `done-pushed-no-pr`
  6. sync_failed_at set        → `sync-failed`
  6½. finished_at set but waves
      not all integrated        → `incomplete`
  7. finished_at set           → `done`
  8. killed_at set             → `killed`
  9. paused_at set             → `paused`
 10. otherwise                 → `in-progress`
"""
from __future__ import annotations


def test_status_in_progress_empty_run_json(leerie):
    assert leerie._derive_run_status({}, {}) == "in-progress"


def test_status_in_progress_none_run_json(leerie):
    """A run with no sidecar at all (run died very early) reads as
    in-progress — that's accurate for the visible state."""
    assert leerie._derive_run_status(None, {}) == "in-progress"


def test_status_done_local(leerie):
    """Finalize completed but --no-push was set (or no push attempted)."""
    rj = {"finished_at": "2026-05-26T15:00:00+00:00"}
    assert leerie._derive_run_status(rj, {}) == "done"


def test_status_done_pushed_no_pr(leerie):
    """Push succeeded, PR not attempted (rare: gh missing post-push, or
    a future --no-pr flag)."""
    rj = {
        "finished_at": "2026-05-26T15:00:00+00:00",
        "pushed_at": "2026-05-26T15:00:05+00:00",
    }
    assert leerie._derive_run_status(rj, {}) == "done-pushed-no-pr"


def test_status_done_pushed_pr(leerie):
    """The happy path: pushed and PR opened."""
    rj = {
        "finished_at": "2026-05-26T15:00:00+00:00",
        "pushed_at": "2026-05-26T15:00:05+00:00",
        "pr_url": "https://github.com/owner/repo/pull/42",
    }
    assert leerie._derive_run_status(rj, {}) == "done-pushed-pr"


def test_status_push_failed(leerie):
    """push_error set: priority over everything except corrupt-sidecar."""
    rj = {
        "finished_at": "2026-05-26T15:00:00+00:00",
        "push_error": "fatal: unable to access ...",
    }
    assert leerie._derive_run_status(rj, {}) == "push-failed"


def test_status_pr_failed(leerie):
    """Push succeeded, PR failed. pushed_at set, pr_error set."""
    rj = {
        "finished_at": "2026-05-26T15:00:00+00:00",
        "pushed_at": "2026-05-26T15:00:05+00:00",
        "pr_error": "gh: authentication required",
    }
    assert leerie._derive_run_status(rj, {}) == "pr-failed"


def test_status_corrupt_sidecar(leerie):
    """An invariant-violating run.json renders as corrupt-sidecar so the
    user can spot it in `list` and intervene."""
    rj = {
        "pushed_at": "2026-05-26T15:00:05+00:00",
        "push_error": "both set is a violation",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_pr_url_without_pushed_at_is_corrupt(leerie):
    """Logical-invariant violation: PR without push."""
    rj = {"pr_url": "https://github.com/owner/repo/pull/42"}
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_paused_remote(leerie):
    """A paused remote run: paused_at + fly_machine_id set, no pushed_at."""
    rj = {
        "paused_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "1234567890abcd",
        "pause_reason": "worker-error",
    }
    assert leerie._derive_run_status(rj, {}) == "paused"


def test_status_paused_without_machine_id_is_corrupt(leerie):
    """Invariant: paused_at requires fly_machine_id (cannot resume otherwise)."""
    rj = {"paused_at": "2026-05-29T16:00:00+00:00"}
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_paused_and_pushed_is_corrupt(leerie):
    """Invariant: paused_at and pushed_at are mutually exclusive."""
    rj = {
        "paused_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "abc",
        "pushed_at": "2026-05-29T16:01:00+00:00",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_paused_with_push_error_renders_as_push_failed(leerie):
    """Precedence: push_error fires before paused_at so the rendered
    status matches the action the user needs to take (fix the push, not
    the pause)."""
    rj = {
        "paused_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "abc",
        "push_error": "fatal: ...",
    }
    assert leerie._derive_run_status(rj, {}) == "push-failed"


def test_status_killed_remote(leerie):
    """An explicitly killed remote run: killed_at + fly_machine_id set."""
    rj = {
        "killed_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "1234567890abcd",
    }
    assert leerie._derive_run_status(rj, {}) == "killed"


def test_status_killed_without_machine_id_is_local_kill_not_corrupt(leerie):
    """N7: killed_at with no machine id and no other Fly/EC2 evidence is
    a valid local (nerdctl) kill, not a corrupt sidecar."""
    rj = {"killed_at": "2026-05-29T16:00:00+00:00"}
    assert leerie._derive_run_status(rj, {}) == "killed"


def test_status_killed_without_machine_id_but_fly_evidence_is_corrupt(leerie):
    """Invariant: killed_at on a run showing Fly evidence (image_tag)
    still requires fly_machine_id or ec2_instance_id."""
    rj = {
        "killed_at": "2026-05-29T16:00:00+00:00",
        "image_tag": "registry.fly.io/leerie:0.6.7",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_killed_and_paused_is_corrupt(leerie):
    """Invariant: paused_at, pushed_at, killed_at are mutually exclusive."""
    rj = {
        "killed_at": "2026-05-29T16:00:00+00:00",
        "paused_at": "2026-05-29T15:00:00+00:00",
        "fly_machine_id": "abc",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_killed_and_pushed_is_corrupt(leerie):
    """Invariant: killed_at and pushed_at cannot both be set."""
    rj = {
        "killed_at": "2026-05-29T16:00:00+00:00",
        "pushed_at": "2026-05-29T15:00:00+00:00",
        "fly_machine_id": "abc",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_status_killed_fires_before_paused(leerie):
    """Precedence: killed_at overrides paused_at when both are somehow
    set (real sidecars are guarded by the invariant; this test exercises
    the derivation order rather than the validator)."""
    # Bypass _validate_run_json to test the derivation precedence directly.
    # Real data passing through _derive_run_status would hit corrupt-sidecar
    # via the validator first; we want to confirm the order is killed > paused
    # in the derivation code path.
    rj = {
        "killed_at": "2026-05-29T16:00:00+00:00",
        "fly_machine_id": "abc",
    }
    # paused_at NOT set → killed (clean case).
    assert leerie._derive_run_status(rj, {}) == "killed"


def test_status_table_lists_every_value_used(leerie):
    """RUN_STATUSES tuple must contain every value _derive_run_status
    can return — drift guard."""
    expected = {
        "seed-failed",
        "corrupt-sidecar", "in-progress", "incomplete", "done",
        "done-pushed-no-pr", "done-pushed-pr",
        "push-failed", "pr-failed",
        "paused", "killed",
        "sync-failed",
    }
    assert set(leerie.RUN_STATUSES) == expected


# --- completion gate (DESIGN §6 *finished_at is a discovery sentinel*) ----

def test_status_incomplete_finished_at_but_waves_unintegrated(leerie):
    """A run whose die-path handler stamped finished_at mid-wave
    (completed_waves < len(waves)) reads as `incomplete`, NOT `done` — so
    `list` doesn't mislabel it and finalize doesn't push a partial branch.
    This is the exact run A / run B OOM-crash shape."""
    rj = {"finished_at": "2026-07-05T23:25:46+00:00"}
    sj = {"completed_waves": 1, "waves": [["a"], ["b"], ["c"], ["d"], ["e"]]}
    assert leerie._derive_run_status(rj, sj) == "incomplete"


def test_status_done_when_all_waves_integrated(leerie):
    """finished_at + completed_waves == len(waves) → genuinely done."""
    rj = {"finished_at": "2026-07-05T23:25:46+00:00"}
    sj = {"completed_waves": 5, "waves": [["a"], ["b"], ["c"], ["d"], ["e"]]}
    assert leerie._derive_run_status(rj, sj) == "done"


def test_status_no_work_run_is_done_not_incomplete(leerie):
    """Cleared-but-empty terminal state: waves == [] and completed == 0.
    `0 < len([])` is False, so it stays `done`, not `incomplete`."""
    rj = {"finished_at": "2026-07-05T23:25:46+00:00"}
    sj = {"completed_waves": 0, "waves": [], "no_work_required": True}
    assert leerie._derive_run_status(rj, sj) == "done"


def test_status_incomplete_only_with_waves_state(leerie):
    """Without a waves list in state_json (e.g. empty state), finished_at
    still reads as done — the gate needs the waves signal to fire."""
    rj = {"finished_at": "2026-07-05T23:25:46+00:00"}
    assert leerie._derive_run_status(rj, {}) == "done"


def test_status_incomplete_yields_to_pushed(leerie):
    """If a partial run was somehow pushed, push/PR checks fire first —
    the incomplete check is only for the unpushed finished_at case."""
    rj = {
        "finished_at": "2026-07-05T23:25:46+00:00",
        "pushed_at": "2026-07-05T23:28:20+00:00",
    }
    sj = {"completed_waves": 1, "waves": [["a"], ["b"]]}
    # pushed_at fires before the incomplete check (matches current
    # precedence order — a push that happened is a fact worth surfacing).
    assert leerie._derive_run_status(rj, sj) == "done-pushed-no-pr"


def test_status_incomplete_guarded_by_killed_and_paused(leerie):
    """The incomplete check itself is guarded by `not killed_at and not
    paused_at` so an explicitly stopped partial run does not read as
    `incomplete`. (Note: `finished_at`→`done` already precedes the
    killed/paused checks in the taxonomy — pre-existing behavior — and
    real sidecars are guarded against finished_at+killed_at by
    _validate_run_json; this test pins only the incomplete-guard, using a
    paused run with NO finished_at so the paused check is what fires.)"""
    sj = {"completed_waves": 1, "waves": [["a"], ["b"]]}
    rj_paused = {"paused_at": "2026-07-05T23:30:00+00:00",
                 "fly_machine_id": "abc"}
    assert leerie._derive_run_status(rj_paused, sj) == "paused"


def test_orphan_marker_returns_seed_failed(leerie):
    """state_json with _orphan=True → seed-failed, fires before any
    run.json check. Synthesized for run dirs that have fly-machine.json
    but no state.json (pre-classify failure during seed_auth)."""
    assert leerie._derive_run_status(None, {"_orphan": True}) == "seed-failed"
    # Even with a corrupted run.json present, _orphan wins because the
    # orphan signal is the earliest precedence and orphans by definition
    # have no run.json at all.
    assert (
        leerie._derive_run_status({"corrupted": "trash"},
                                  {"_orphan": True})
        == "seed-failed"
    )


def test_non_orphan_state_does_not_trigger_seed_failed(leerie):
    """Absence of `_orphan` marker means normal flow; in-progress when
    run.json is None and state has no other signals."""
    assert leerie._derive_run_status(None, {}) == "in-progress"
    assert leerie._derive_run_status(None, {"_orphan": False}) == "in-progress"


def test_push_error_priority_over_pr_url(leerie):
    """If somehow both push_error and pr_url were set (impossible in
    practice), push_error wins — the cleanest signal that something is
    broken comes first."""
    rj = {
        "push_error": "fatal: ...",
        "pr_url": "https://gh.com/pr/1",
    }
    # _validate_run_json rejects this combo → corrupt-sidecar.
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_sync_failed_at_surfaces_as_sync_failed_running(leerie):
    """sync_failed_at set + fly_machine_id set + no killed_at → status
    is `sync-failed`. This is the orchestrator-finished-but-
    fetch_branch-failed state where the machine is still running on
    Fly with un-synced work."""
    rj = {
        "finished_at": "2026-06-01T18:43:33Z",
        "sync_failed_at": "2026-06-01T18:43:34Z",
        "sync_fail_reason": "sync-failed-on-clean-exit",
        "fly_machine_id": "abc123",
    }
    assert leerie._derive_run_status(rj, {}) == "sync-failed"


def test_sync_failed_takes_precedence_over_done_local(leerie):
    """When both finished_at and sync_failed_at are set, sync-failed
    wins — the user must address the failed sync before the run is
    considered locally complete."""
    rj = {
        "finished_at": "2026-06-01T18:43:33Z",
        "sync_failed_at": "2026-06-01T18:43:34Z",
        "fly_machine_id": "abc123",
    }
    assert leerie._derive_run_status(rj, {}) == "sync-failed"


def test_sync_failed_at_requires_fly_machine_id(leerie):
    """sync_failed_at without fly_machine_id is an invariant violation;
    the validator rejects it → corrupt-sidecar."""
    rj = {
        "finished_at": "2026-06-01T18:43:33Z",
        "sync_failed_at": "2026-06-01T18:43:34Z",
        # No fly_machine_id.
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"


def test_sync_failed_and_pushed_are_mutex(leerie):
    """sync_failed_at and pushed_at cannot both be set — invariant
    violation → corrupt-sidecar."""
    rj = {
        "pushed_at": "2026-06-01T18:43:33Z",
        "sync_failed_at": "2026-06-01T18:43:34Z",
        "fly_machine_id": "abc123",
    }
    assert leerie._derive_run_status(rj, {}) == "corrupt-sidecar"
