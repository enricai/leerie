"""Tests for `_validate_run_json()` — enforces the logical invariants
on the `run.json` sidecar (IMPLEMENTATION.md §8).

Invariants (mirrors orchestrator/leerie.py:_validate_run_json):
1. `pushed_at` and `push_error` are mutually exclusive.
2. `pr_url` and `pr_error` are mutually exclusive.
3. If `pr_url` is set, `pushed_at` must be set (no PR without a push).
4. `paused_at`, `pushed_at`, and `killed_at` are mutually exclusive
   (a run is at most one terminal-or-paused state).
5. If `paused_at` is set, `fly_machine_id` must also be set
   (cannot pause without knowing where to resume).
6. If `killed_at` is set on a Fly/EC2-shaped sidecar, at least one of
   `fly_machine_id`/`ec2_instance_id` must also be set (cannot have
   destroyed a machine you don't have a pointer to). "Fly/EC2-shaped"
   means either (a) it shows remote evidence — `fly_machine_id`,
   `ec2_instance_id`, `volume_id`, or `image_tag` non-null — or (b) it
   carries a `fly_machine_id`/`ec2_instance_id` key at all, even null
   (the shape `_ensure_run_json` writes from a `fly-machine.json` that
   lacks the id). A local (nerdctl) kill has neither, so it is exempt.
7. `sync_failed_at` is mutex-checked against `pushed_at` (a pushed run
   can't be sync-failed) and `killed_at` (a destroyed machine can't be
   sync-failed). When set, `fly_machine_id` must also be set.
8. If `volume_id` is set, `fly_machine_id` must also be set
   (a Fly volume without a machine to attach it to is invalid).

Valid status combinations (leerie list derives these via
`_derive_run_status`):
- `done`          — no push attempted, no PR.
- `done-pushed-no-pr`   — pushed, PR not attempted (offline-pr case).
- `done-pushed-pr`      — pushed + PR opened.
- `push-failed`         — push attempted and failed.
- `pr-failed`           — push succeeded, PR creation failed.
- `paused`       — remote run paused on failure; resume via `leerie resume`.
- `killed`       — terminal state via leerie kill; not resumable.
- `sync-failed` — orchestrator finished but fetch_branch failed;
                          machine still up, recover via `leerie finalize`/`leerie kill`.
- `corrupt-sidecar`     — run.json violates an invariant above.
- `in-progress`         — finalize hasn't run yet (no fields set).
"""
from __future__ import annotations

import pytest


def _minimal_run_json(**overrides) -> dict:
    base = {
        "run_id": "feat-foo-abc123",
        "branch": "leerie/runs/feat-foo-abc123",
        "working_branch": "main",
        "started_at": "2026-05-26T10:00:00+00:00",
        "finished_at": None,
        "task": "do thing",
        "pushed_at": None,
        "push_error": None,
        "pr_url": None,
        "pr_error": None,
    }
    base.update(overrides)
    return base


# --- accepts all valid status combinations ---------------------------------

def test_accepts_in_progress(leerie):
    """No push/PR fields set — run hasn't finalized yet."""
    leerie._validate_run_json(_minimal_run_json())


def test_accepts_done_local(leerie):
    """--no-push: finalize succeeded, nothing pushed, no PR."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
    ))


def test_accepts_done_pushed_no_pr(leerie):
    """Push succeeded, PR not attempted (e.g., --no-pr in a future flag,
    or `gh` not configured). All three pr_* fields stay null."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
        pushed_at="2026-05-26T11:00:05+00:00",
    ))


def test_accepts_done_pushed_pr(leerie):
    """Happy path: pushed and PR opened."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
        pushed_at="2026-05-26T11:00:05+00:00",
        pr_url="https://github.com/owner/repo/pull/123",
    ))


def test_accepts_push_failed(leerie):
    """Push attempted, push failed: push_error set, pushed_at null,
    no PR."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
        push_error="fatal: unable to access ...",
    ))


def test_accepts_pr_failed(leerie):
    """Push succeeded, PR creation failed: pushed_at set, pr_url null,
    pr_error set."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
        pushed_at="2026-05-26T11:00:05+00:00",
        pr_error="gh: authentication required",
    ))


# --- rejects invariant violations ------------------------------------------

def test_rejects_pushed_at_and_push_error_both_set(leerie):
    """Logically impossible: a push either succeeded or failed."""
    with pytest.raises(ValueError, match="pushed_at and push_error"):
        leerie._validate_run_json(_minimal_run_json(
            pushed_at="2026-05-26T11:00:05+00:00",
            push_error="something",
        ))


def test_rejects_pr_url_and_pr_error_both_set(leerie):
    with pytest.raises(ValueError, match="pr_url and pr_error"):
        leerie._validate_run_json(_minimal_run_json(
            pushed_at="2026-05-26T11:00:05+00:00",
            pr_url="https://github.com/owner/repo/pull/123",
            pr_error="something",
        ))


def test_rejects_pr_url_without_pushed_at(leerie):
    """A PR cannot exist without a successful push. This is the logical
    invariant called out explicitly in IMPLEMENTATION.md §8."""
    with pytest.raises(ValueError, match="PR cannot succeed without"):
        leerie._validate_run_json(_minimal_run_json(
            pr_url="https://github.com/owner/repo/pull/123",
        ))


def test_rejects_pr_url_with_push_failed(leerie):
    """Same invariant: if push_error is set, pushed_at is null, so pr_url
    being set is also invalid. Failure mode: the first check fires
    because pr_url is set but pushed_at is null."""
    with pytest.raises(ValueError, match="PR cannot succeed without"):
        leerie._validate_run_json(_minimal_run_json(
            push_error="x",
            pr_url="https://github.com/owner/repo/pull/123",
        ))


# --- pause-on-failure invariants -------------------------------------------

def test_accepts_paused_remote(leerie):
    """Valid paused run: paused_at + fly_machine_id, no pushed_at."""
    leerie._validate_run_json(_minimal_run_json(
        paused_at="2026-05-29T16:00:00+00:00",
        fly_machine_id="148e445b911389",
        pause_reason="worker-error",
    ))


def test_rejects_paused_and_pushed_both_set(leerie):
    """A run cannot be both paused and finalized."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        leerie._validate_run_json(_minimal_run_json(
            paused_at="2026-05-29T16:00:00+00:00",
            fly_machine_id="abc",
            pushed_at="2026-05-29T16:01:00+00:00",
        ))


def test_rejects_paused_without_fly_machine_id(leerie):
    """Cannot pause without a recoverable pointer to the machine."""
    with pytest.raises(ValueError, match="fly_machine_id is null"):
        leerie._validate_run_json(_minimal_run_json(
            paused_at="2026-05-29T16:00:00+00:00",
        ))


def test_accepts_fly_machine_id_alone(leerie):
    """fly_machine_id without paused_at is fine — provision.sh writes
    fly_machine_id at provision time, well before any pause decision."""
    leerie._validate_run_json(_minimal_run_json(
        fly_machine_id="148e445b911389",
    ))


# --- killed_at invariants (DESIGN §6 *The user-visible verb surface*) -----

def test_accepts_killed_remote(leerie):
    """Valid killed run: killed_at + fly_machine_id, nothing else."""
    leerie._validate_run_json(_minimal_run_json(
        killed_at="2026-05-29T16:00:00+00:00",
        fly_machine_id="148e445b911389",
    ))


def test_rejects_killed_without_any_machine_id_but_with_fly_evidence(leerie):
    """Cannot have killed a machine you don't have a pointer to, when the
    sidecar shows other Fly-only evidence (image_tag: 'null for
    local-runtime runs' per IMPLEMENTATION.md §8) — a corrupt Fly sidecar,
    not a local kill."""
    with pytest.raises(ValueError, match="killed_at is set but neither"):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            image_tag="registry.fly.io/leerie:0.6.7",
        ))


def test_rejects_killed_fly_shaped_without_fly_machine_id(leerie):
    """The key-presence half of rule 6, disjoint from the evidence half
    above: a sidecar that CARRIES `fly_machine_id` but has it null, with
    no other remote evidence at all. This is exactly what the launcher's
    `_ensure_run_json` writes when it bootstraps a missing sidecar from a
    `fly-machine.json` that lacks the id or fails to parse — a corrupt
    remote kill the evidence test alone cannot see.

    `_minimal_run_json` passes overrides through `**`, so `fly_machine_id
    =None` injects the KEY with a None value; omitting the argument
    entirely leaves the key absent (see `test_accepts_killed_local`).
    That difference is the whole point of this test."""
    with pytest.raises(
        ValueError,
        match="killed_at is set but neither fly_machine_id nor ec2_instance_id",
    ):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            fly_machine_id=None,
        ))


def test_rejects_killed_ec2_shaped_without_ec2_instance_id(leerie):
    """Same as above on the EC2 side: the key is present but null, which
    is what `_ensure_run_json` writes from an `ec2-instance.json` missing
    the instance id."""
    with pytest.raises(
        ValueError,
        match="killed_at is set but neither fly_machine_id nor ec2_instance_id",
    ):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            ec2_instance_id=None,
        ))


def test_rejects_killed_without_any_machine_id_but_with_volume_evidence(leerie):
    """Same as above, using volume_id (also Fly-only) as the evidence."""
    with pytest.raises(ValueError, match="killed_at is set but neither"):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            volume_id="vol_abc123",
        ))


def test_accepts_killed_local(leerie):
    """N7 regression: a local (nerdctl) `leerie kill` writes killed_at
    alone — no fly_machine_id, no ec2_instance_id, no other remote
    evidence. A nerdctl container has no machine id, so the invariant
    does not apply; this must not be rejected as invariant-violating."""
    leerie._validate_run_json(_minimal_run_json(
        killed_at="2026-05-29T16:00:00+00:00",
    ))


def test_accepts_killed_ec2(leerie):
    """Valid killed EC2 run: killed_at + ec2_instance_id, no
    fly_machine_id."""
    leerie._validate_run_json(_minimal_run_json(
        killed_at="2026-05-29T16:00:00+00:00",
        ec2_instance_id="i-0123456789abcdef0",
    ))


def test_rejects_killed_and_paused_both_set(leerie):
    """paused_at and killed_at are mutually exclusive."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            paused_at="2026-05-29T15:00:00+00:00",
            fly_machine_id="abc",
        ))


def test_rejects_killed_and_pushed_both_set(leerie):
    """killed_at and pushed_at are mutually exclusive."""
    with pytest.raises(ValueError, match="mutually exclusive"):
        leerie._validate_run_json(_minimal_run_json(
            killed_at="2026-05-29T16:00:00+00:00",
            pushed_at="2026-05-29T15:00:00+00:00",
            fly_machine_id="abc",
        ))


# --- volume_id invariant (DESIGN §6 *Remote disk policy*) ------------------

def test_accepts_volume_id_with_fly_machine_id(leerie):
    """Valid: volume_id and fly_machine_id both set (the FLY_VM_DISK_GB
    path; provision.sh always writes them together)."""
    leerie._validate_run_json(_minimal_run_json(
        fly_machine_id="148e445b911389",
        volume_id="vol_abcdef0123456789",
    ))


def test_rejects_volume_id_without_fly_machine_id(leerie):
    """A volume without a machine to attach it to is invalid. Defends
    against external mutation / corruption — leerie's own provision.sh
    cannot produce this state."""
    with pytest.raises(ValueError, match="volume_id is set but fly_machine_id"):
        leerie._validate_run_json(_minimal_run_json(
            volume_id="vol_abcdef0123456789",
        ))


# --- sync_failed_at invariants (rule 7) -------------------------------------

def test_accepts_sync_failed_with_fly_machine_id(leerie):
    """Valid: decide_teardown's clean-exit branch left the machine running
    with un-synced work — sync_failed_at + fly_machine_id, no pushed_at/
    killed_at."""
    leerie._validate_run_json(_minimal_run_json(
        sync_failed_at="2026-05-29T16:00:00+00:00",
        fly_machine_id="148e445b911389",
    ))


def test_rejects_sync_failed_and_pushed_both_set(leerie):
    """A successfully pushed run cannot also be sync-failed."""
    with pytest.raises(ValueError, match="sync_failed_at and pushed_at"):
        leerie._validate_run_json(_minimal_run_json(
            sync_failed_at="2026-05-29T16:00:00+00:00",
            pushed_at="2026-05-29T15:00:00+00:00",
            fly_machine_id="abc",
        ))


def test_rejects_sync_failed_and_killed_both_set(leerie):
    """A destroyed machine cannot also be sync-failed."""
    with pytest.raises(ValueError, match="sync_failed_at and killed_at"):
        leerie._validate_run_json(_minimal_run_json(
            sync_failed_at="2026-05-29T16:00:00+00:00",
            killed_at="2026-05-29T15:00:00+00:00",
            fly_machine_id="abc",
        ))


def test_rejects_sync_failed_without_fly_machine_id(leerie):
    """The running machine needs a pointer for the user to recover via
    finalize/kill."""
    with pytest.raises(ValueError, match="sync_failed_at is set but "
                        "fly_machine_id is null"):
        leerie._validate_run_json(_minimal_run_json(
            sync_failed_at="2026-05-29T16:00:00+00:00",
        ))


# --- defensive cases -------------------------------------------------------

def test_rejects_non_dict(leerie):
    """A non-object run.json (e.g., array) is a hard error — the contract
    is a JSON object."""
    with pytest.raises(ValueError, match="must be a JSON object"):
        leerie._validate_run_json(["not", "a", "dict"])
    with pytest.raises(ValueError, match="must be a JSON object"):
        leerie._validate_run_json("string")
    with pytest.raises(ValueError, match="must be a JSON object"):
        leerie._validate_run_json(None)


def test_accepts_extra_fields(leerie):
    """Forward-compat: extra fields not in the documented schema don't
    break validation. Leerie can read run.json from a newer version."""
    leerie._validate_run_json(_minimal_run_json(
        future_field="some value",
        another_extra=42,
    ))


def test_accepts_empty_dict(leerie):
    """An empty dict has no invariants violated (everything is null/missing).
    A reader can still infer 'in-progress' or 'corrupt-sidecar' from the
    absence of fields."""
    leerie._validate_run_json({})


# --- group_id (DESIGN §20 run groups) --------------------------------------

def test_accepts_group_id(leerie):
    """group_id is informational and orthogonal to push/pause/kill state.
    No invariant check; it passes through like any other unknown field."""
    leerie._validate_run_json(_minimal_run_json(
        group_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    ))


def test_accepts_group_id_with_push_state(leerie):
    """group_id coexists with a pushed run without triggering any invariant."""
    leerie._validate_run_json(_minimal_run_json(
        finished_at="2026-05-26T11:00:00+00:00",
        pushed_at="2026-05-26T11:00:05+00:00",
        pr_url="https://github.com/owner/repo/pull/42",
        group_id="a1b2c3d4-e5f6-7890-abcd-ef1234567890",
    ))
