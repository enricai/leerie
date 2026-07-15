#!/usr/bin/env bash
# scripts/remote/provision.sh — provision a Fly.io Machine for one leerie run.
#
# This is the remote equivalent of `nerdctl run --rm`: start a Fly Machine
# from the leerie image, block until the machine is reachable (SSH/started),
# then destroy it on exit — whether the caller exits cleanly, is interrupted
# by Ctrl-C, or crashes.
#
# Usage (invoked from the leerie launcher's REMOTE=true branch):
#
#   source scripts/remote/provision.sh
#   provision_machine             # blocks until machine is started
#   # ... do work (run leerie inside the machine) ...
#   export LEERIE_REMOTE_EXIT_RC=$orch_rc   # launcher sets this on exit
#   # decide_teardown is registered as an EXIT trap; classifies the rc
#   # and routes to stop_machine (pause-on-failure) or destroy_machine.
#
# Environment variables (set by the launcher before sourcing):
#
#   LEERIE_FLY_APP    — Fly.io app name (required)
#   FLY_IMAGE_TAG   — full image tag to launch (e.g. registry.fly.io/leerie:0.2.1)
#   FLY_REGION      — Fly.io region (default: from fly.toml or "iad")
#   FLY_VM_CPUS     — vCPUs for the machine (default: 4)
#   FLY_VM_MEMORY   — memory in MB for the machine (default: 8192)
#   LEERIE_RUN_ID     — orchestrator-minted run id (optional; when set,
#                     provision writes fly_machine_id to the run sidecar
#                     and decide_teardown writes paused_at on pause)
#   USER_REPO       — host-side path to the user's repo (for sidecar I/O)
#   LEERIE_REMOTE_EXIT_RC — set by the launcher just before exit; read by
#                     the EXIT trap to classify the orchestrator's exit
#                     code. Pause-worthy: any non-zero other than
#                     EXIT_NEEDS_ANSWERS=10, EX_TEMPFAIL=75, SIGINT=130,
#                     SIGTERM=143. (DESIGN §6 Remote pause-on-failure.)
#
# Exports:
#   LEERIE_MACHINE_ID — the created machine's ID (available after provision_machine)
#
# The teardown trap is registered when provision_machine succeeds. It fires
# on EXIT (clean exit), INT (Ctrl-C), and TERM (SIGTERM). A machine that
# fails to start is destroyed immediately before provision_machine returns 1.

set -euo pipefail

# --- shared lib (update_run_json / iso_now) ------------------------------
_PROVISION_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_PROVISION_DIR/lib.sh"

# --- configuration with defaults -----------------------------------------
FLY_APP="${LEERIE_FLY_APP:-}"
FLY_REGION="${FLY_REGION:-iad}"
FLY_VM_CPUS="${FLY_VM_CPUS:-4}"
FLY_VM_MEMORY="${FLY_VM_MEMORY:-8192}"
# Opt-in: per-machine Fly volume mounted at /work. UNSET by default —
# when blank, no volume is created and the `flyctl machine run` argv is
# byte-for-byte today's. Setting to a positive integer N creates a Fly
# volume sized at N GB and adds --volume "<id>:/work" to the run argv.
# Volume is destroyed alongside the machine in destroy_machine.
#
# Mount target is /work because that's where the durable workload
# lives: the seeded repo, .leerie/runs/<id>/ state, and per-subtask
# worktrees that dominate disk growth. The caches and .claude auth
# bundle under /home/leerie don't grow N-wide per worker and are
# refreshed by seed_auth on every resume — they stay on the rootfs.
# (DESIGN §6 *Remote disk policy*, IMPLEMENTATION §2.)
FLY_VM_DISK_GB="${FLY_VM_DISK_GB:-}"

# Fly's `shared` CPU class tops out at 8 CPUs / 16384 MB. Above either
# ceiling, promote to `performance` CPUs (significantly more expensive —
# ~14x per CPU-second — but the only way to exceed shared-cpu-8x).
if [ "$FLY_VM_CPUS" -gt 8 ] || [ "$FLY_VM_MEMORY" -gt 16384 ]; then
  FLY_VM_CPU_KIND="performance"
  remote_log "remote: using performance CPUs (cpus=$FLY_VM_CPUS memory=${FLY_VM_MEMORY}MB exceeds shared-cpu-8x ceiling of 8/16384)"
else
  FLY_VM_CPU_KIND="shared"
fi

# Max seconds to wait for the machine to reach state "started".
MACHINE_START_TIMEOUT="${LEERIE_MACHINE_START_TIMEOUT:-120}"

# Exported machine ID — empty until provision_machine succeeds.
LEERIE_MACHINE_ID=""
# Volume ID — empty unless FLY_VM_DISK_GB is set and provision_machine
# successfully created a volume. Destroyed alongside the machine in
# destroy_machine.
LEERIE_VOLUME_ID=""

# require_flyctl now lives in lib.sh (sourced above) with auto-install
# support. Inline detection here has been removed to avoid drift.

# --- stop machine --------------------------------------------------------
# Pause-on-failure path: preserves the machine's filesystem on its Fly
# volume so resume-machine.sh can wake it later. Idempotent and tolerant
# of an already-stopped machine.
stop_machine() {
  local mid="$LEERIE_MACHINE_ID"
  if [ -z "$mid" ]; then
    return 0
  fi
  remote_log "remote: stopping machine $mid (paused)..."
  flyctl machine stop "$mid" --app "$FLY_APP" 2>/dev/null || true
  # Don't clear LEERIE_MACHINE_ID — the launcher's notification block
  # needs it to print the attach/resume commands.
}

# --- destroy volume ------------------------------------------------------
# Reap LEERIE_VOLUME_ID, independent of whether a machine still exists.
#
# Deliberately NOT nested inside destroy_machine: Fly volumes outlive their
# machines on a bare `machine destroy` ("a Machine can be destroyed without
# destroying its volume" — Fly docs), so the machine being already gone is
# exactly when a known volume still needs reaping. Gating this on a live
# machine id leaked every volume whose machine died first.
#
# Callers must invoke this AFTER the machine is gone — Fly refuses to
# destroy a volume that is still attached ("in use by machine X").
#
# Best-effort by design: a failure logs and returns 0. An orphan volume is a
# billing issue; a teardown that aborts is a correctness issue.
destroy_volume() {
  if [ -z "$LEERIE_VOLUME_ID" ]; then
    return 0
  fi
  remote_log "remote: destroying volume $LEERIE_VOLUME_ID ..."
  if flyctl volumes destroy "$LEERIE_VOLUME_ID" \
       --app "$FLY_APP" \
       --yes \
       2>/dev/null; then
    remote_log "remote: volume $LEERIE_VOLUME_ID destroyed"
  else
    # The user can reap a straggler via
    # `flyctl volumes list --app "$FLY_APP"` + manual destroy.
    remote_log "warning: failed to destroy volume $LEERIE_VOLUME_ID (may already be gone or pinned)"
  fi
  LEERIE_VOLUME_ID=""
}

# --- destroy machine -----------------------------------------------------
# Full reap. Idempotent: destroy is no-op if the machine is already gone.
# When LEERIE_VOLUME_ID is set (FLY_VM_DISK_GB path), the volume is
# destroyed AFTER the machine is gone — volumes pinned to a destroyed
# machine can be reaped cleanly; the reverse order (volume first) errors
# with "in use by machine X".
destroy_machine() {
  local mid="$LEERIE_MACHINE_ID"
  if [ -z "$mid" ]; then
    # No machine, but a volume may still be known — the machine was already
    # destroyed (by us, by Fly, or by hand) while its volume survived. That
    # is the orphan shape, and it is precisely what this early return used
    # to skip: the volume reap below was unreachable past here.
    destroy_volume
    return 0
  fi
  remote_log "remote: destroying machine $mid ..."
  if flyctl machine destroy "$mid" \
       --app "$FLY_APP" \
       --force \
       2>/dev/null; then
    remote_log "remote: machine $mid destroyed"
  else
    # destroy can fail if the machine was already stopped/destroyed by Fly.
    # Attempt stop first as a fallback, then a second destroy.
    flyctl machine stop "$mid" --app "$FLY_APP" 2>/dev/null || true
    flyctl machine destroy "$mid" --app "$FLY_APP" --force 2>/dev/null || true
    remote_log "remote: machine $mid stop+destroy attempted (may already be gone)"
  fi
  # Machine is gone — now the volume can be reaped cleanly.
  destroy_volume
  # Keep the PID-keyed attach pointer (remote/$$.json) after destroy.
  # The chain tagging loop reads it post-wait to discover the machine ID
  # and write chain_id + wave_idx into run.json. The resume auto-discovery
  # path already filters stale pointers via kill -0, so a pointer for a
  # dead PID is harmless.
  LEERIE_MACHINE_ID=""
}

# --- _try_fetch_branch_for_teardown --------------------------------------
# Source fetch-branch.sh (idempotent — safe to re-source) and run
# fetch_branch against the live machine. Returns 0 on success (state
# + run branch are on the host), 1 on any failure (network, missing
# run dir on machine, etc).
#
# Called from decide_teardown's clean-exit branch BEFORE destroy_machine,
# so a failure here means we keep the machine running rather than
# destroying work-in-progress.
#
# Requires: LEERIE_MACHINE_ID, USER_REPO, FLY_APP in scope (already set
# by the time decide_teardown runs, since provision_machine populates
# them).
_try_fetch_branch_for_teardown() {
  # LEERIE_REPO (set by the launcher) or LEERIE_HOME (set by some callers
  # for compat) points at the leerie install dir. Fall back to deriving
  # it from this file's location if neither is set (e.g. provision.sh
  # sourced standalone in a test).
  local _leerie_dir="${LEERIE_REPO:-${LEERIE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
  if [ ! -f "$_leerie_dir/scripts/remote/fetch-branch.sh" ]; then
    remote_log "_try_fetch_branch_for_teardown: fetch-branch.sh missing at $_leerie_dir"
    return 1
  fi
  # shellcheck disable=SC1091
  . "$_leerie_dir/scripts/remote/fetch-branch.sh"
  if ! fetch_branch; then
    return 1
  fi
  return 0
}

# --- decide_teardown (registered as EXIT/INT/TERM trap) ------------------
# Classifies $LEERIE_REMOTE_EXIT_RC (set by the launcher just before exit)
# and dispatches to stop_machine (pause-on-failure) or destroy_machine.
# Classification table is documented in DESIGN §6 Remote pause-on-failure.
#
# Clean-exit branch (rc=0/10/75): syncs run state to the host via
# _try_fetch_branch_for_teardown BEFORE destroying the machine. If
# sync fails, leaves machine running so the user can recover.
#
# Pause branch (other non-zero): writes paused_at + pause_reason to
# the run sidecar so the resume path can find the machine later.
#
# Idempotent: the trap fires on every exit, including success; the
# stop/destroy primitives no-op on an empty LEERIE_MACHINE_ID.
decide_teardown() {
  # Idempotency: when the launcher takes SIGINT, the INT trap fires
  # decide_teardown once, then the subsequent EXIT trap would fire it
  # a second time with a clobbered rc (the launcher's post-INT
  # cleanup path can set container_rc=1 → LEERIE_REMOTE_EXIT_RC=1 →
  # the *) pause arm would pause a machine the user only meant to
  # detach from). Skip every pass after the first.
  if [ -n "${LEERIE_TEARDOWN_DONE:-}" ]; then
    return 0
  fi
  # The launcher pre-sets LEERIE_REMOTE_EXIT_RC=130 at the top of
  # its Fly branch so the SIGINT-trap-before-line-2203 race routes
  # here with rc=130 (detach banner). The :-0 default is preserved
  # for unit tests and any other entry path that sources provision.sh
  # without going through the launcher's Fly branch — those want
  # clean-exit semantics.
  local rc="${LEERIE_REMOTE_EXIT_RC:-0}"
  local mid="$LEERIE_MACHINE_ID"
  if [ -z "$mid" ]; then
    LEERIE_TEARDOWN_DONE=1
    export LEERIE_TEARDOWN_DONE
    return 0
  fi
  case "$rc" in
    0|10|11|75)
      # Genuine terminal exits of the *run*:
      #   0   — orchestrator finished cleanly (the tail wrapper detected
      #         orchestrator-pid exit and printed the finalize hint).
      #   10  — EXIT_NEEDS_ANSWERS (plugin re-run).
      #   11  — EXIT_BUDGET_INFEASIBLE (DESIGN §13 *Budget feasibility —
      #         fail fast at the cheapest moment*). The orchestrator
      #         die()d at check_budget_feasibility() with a recommended
      #         --max-workers value, before any implementer spawned. The
      #         run is unrecoverable (--resume would die at the guard
      #         since waves was never written), so destroy the machine
      #         like the other terminal exits and let the user re-run
      #         with the recommended cap. Falls through to the
      #         _run_finished_at == "" path below: skip host_finalize
      #         (nothing committed), then destroy_machine cleanly.
      #   75  — EX_TEMPFAIL (rate-limit, parse-fail).
      #
      # SAFETY-CRITICAL: pull the run branch + state dir to the host
      # BEFORE destroying the machine. If we destroy first and then
      # try to fetch, the user has paid for LLM work that is now
      # unrecoverable. This is a one-way ratchet: machine destruction
      # is gated on confirmed host-side state.
      #
      # On sync failure we leave the machine RUNNING (not stopped) so
      # the user can investigate without first having to start a
      # paused machine. They explicitly destroy via `leerie --kill`
      # when they've recovered the work.
      if _try_fetch_branch_for_teardown; then
        remote_log "remote: run branch + state synced to host"
        # Auto-finalize: now that the run dir is on the host (and the
        # orchestrator wrote run.json.no_push as **intent**, not the
        # mechanism flag — see DESIGN §6), push + PR happen here so
        # the user doesn't need a second command. The local-runtime
        # path runs the same host_finalize inline; this is the Fly
        # parity call site.
        #
        # Ctrl-C semantics (documented per the Ctrl-C audit in the
        # implementation plan): bash masks the originating signal for
        # the duration of this handler, so re-entrancy is not
        # possible. SIGINT during `git push` → push fails → trap
        # leaves machine running (recovery). SIGINT during `gh pr
        # create` → push already succeeded → trap destroys machine;
        # the user can `gh pr create` manually using the URL hint
        # host_finalize already printed. Matches `leerie --finalize`'s
        # existing behavior — work is preserved on origin.
        local run_dir=""
        if [ -n "${LEERIE_REMOTE_RUN_ID:-}" ]; then
          if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
            run_dir="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_REMOTE_RUN_ID"
          elif [ -n "${USER_REPO:-}" ]; then
            run_dir="$USER_REPO/.leerie/runs/$LEERIE_REMOTE_RUN_ID"
          fi
        fi
        # Skip auto-finalize when the run exited cleanly (rc=0|10|75)
        # but didn't reach phase_finalize — most commonly
        # EXIT_NEEDS_ANSWERS=10 (orchestrator wrote
        # pending-questions.json and exited 10 so the user can answer
        # clarification questions and re-run with --answers). The run
        # isn't failed; it's waiting. Calling host_finalize on a
        # not-yet-finalized run would print a misleading "push FAILED"
        # banner because host_finalize requires run.json.finished_at +
        # branch fields. Fall through to the existing manual-hint
        # path: destroy the machine (work is on host either way) and
        # tell the user to re-run with --answers, after which
        # `leerie --finalize <run-id>` is available.
        local _run_finished_at=""
        if [ -n "$run_dir" ] && [ -f "$run_dir/run.json" ]; then
          _run_finished_at="$(jq -r '.finished_at // ""' "$run_dir/run.json" 2>/dev/null || true)"
        fi
        if [ -n "$run_dir" ] && [ -d "$run_dir" ] && [ -z "$_run_finished_at" ]; then
          if [ "$rc" = "11" ]; then
            # EXIT_BUDGET_INFEASIBLE: orchestrator dies pre-execute
            # with a recommended --max-workers value in stderr (which
            # the user already saw above this banner). --resume is not
            # an option — the resume guard rejects (no waves field).
            # The fix is to re-run with the cap raised.
            remote_log "remote: budget preflight rejected the plan; see the recommended --max-workers value above"
            remote_log "remote: re-run from the host with the recommended --max-workers value (or split the task into smaller scopes)"
          else
            remote_log "remote: run did not reach finalize (likely waiting for clarification); skipping auto-finalize"
            if [ -n "${LEERIE_REMOTE_RUN_ID:-}" ]; then
              remote_log "remote: run 'leerie --finalize $LEERIE_REMOTE_RUN_ID' to push and open a PR after the run completes"
            fi
          fi
          destroy_machine
        elif [ -n "$run_dir" ] && [ -d "$run_dir" ]; then
          local _leerie_dir="${LEERIE_REPO:-${LEERIE_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}}"
          if [ -f "$_leerie_dir/scripts/host-finalize.sh" ]; then
            # shellcheck disable=SC1091
            . "$_leerie_dir/scripts/host-finalize.sh"
            remote_log "auto-finalize: pushing + opening PR"
            if host_finalize "$run_dir"; then
              # Push succeeded (PR-creation failure is non-fatal — the
              # work is on origin; pr_error is in run.json).
              destroy_machine
            else
              # Push failed; host_finalize wrote push_error to run.json.
              # Mirror the sync-failure pattern below: keep the machine
              # running so the user can retry from the host. Surface a
              # banner with the recovery command.
              echo "" >&2
              echo "================================================================" >&2
              remote_log "WARNING — auto-finalize push FAILED."
              echo "  The run synced to host cleanly but git push failed." >&2
              echo "  Branch + state are on host; retry the push from here:" >&2
              echo "" >&2
              echo "    leerie --finalize ${LEERIE_REMOTE_RUN_ID} --runtime fly" >&2
              echo "" >&2
              echo "  Machine is being LEFT RUNNING. When recovered, destroy:" >&2
              echo "    leerie --kill ${LEERIE_REMOTE_RUN_ID} --runtime fly" >&2
              echo "  Machine: $mid (still running on Fly)" >&2
              echo "================================================================" >&2
              # Intentionally NO destroy_machine.
            fi
          else
            # Defensive: host-finalize.sh missing. Fall back to the old
            # behavior (print the hint, destroy). Work is on host.
            remote_log "remote: run 'leerie --finalize $LEERIE_REMOTE_RUN_ID' to push and open a PR"
            destroy_machine
          fi
        else
          # Defensive: sync said success but the expected run dir
          # isn't where we look for it. Fall back to the manual hint.
          if [ -n "${LEERIE_REMOTE_RUN_ID:-}" ]; then
            remote_log "remote: run 'leerie --finalize $LEERIE_REMOTE_RUN_ID' to push and open a PR"
          fi
          destroy_machine
        fi
      else
        local sync_reason="sync-failed-on-clean-exit"
        local sidecar=""
        if [ -n "${LEERIE_RUN_ID:-}" ]; then
          if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
            sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
          elif [ -n "${USER_REPO:-}" ]; then
            sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
          fi
        fi
        if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
          update_run_json "$sidecar" \
            sync_failed_at "$(iso_now)" \
            sync_fail_reason "$sync_reason" \
            fly_machine_id "$mid" || true
        fi
        echo "" >&2
        echo "================================================================" >&2
        remote_log "WARNING — sync from machine to host FAILED."
        echo "  The orchestrator finished cleanly but the run branch + state" >&2
        echo "  could not be pulled back. The machine is being LEFT RUNNING" >&2
        echo "  so your work is not lost. Recover manually:" >&2
        echo "" >&2
        echo "    1. Investigate / retry sync (most common):" >&2
        echo "         leerie --finalize ${LEERIE_RUN_ID:-<run-id>} --runtime fly" >&2
        echo "       (this calls fetch_branch + host push; safe to retry)" >&2
        echo "" >&2
        echo "       If that errors with \"no completed unpushed run\", the" >&2
        echo "       orchestrator died before writing finished_at. Recover with:" >&2
        echo "         leerie --finalize ${LEERIE_RUN_ID:-<run-id>} --force --runtime fly" >&2
        echo "       (--force refuses if the orchestrator is still alive.)" >&2
        echo "" >&2
        echo "    2. Or attach + inspect manually:" >&2
        echo "         leerie --resume ${LEERIE_RUN_ID:-<run-id>} --shell --runtime fly" >&2
        echo "" >&2
        echo "    3. When your work is safely on the host, destroy the" >&2
        echo "       machine:" >&2
        echo "         leerie --kill ${LEERIE_RUN_ID:-<run-id>} --runtime fly" >&2
        echo "" >&2
        echo "  Machine: $mid (still running on Fly)" >&2
        echo "================================================================" >&2
        # Intentionally NO stop_machine, NO destroy_machine. The user
        # owns this machine until they explicitly --kill it.
      fi
      ;;
    130|143)
      # Host-side SIGINT (130) / SIGTERM (143). With the detached
      # orchestrator (DESIGN §6 *Detached orchestrator (remote mode)*),
      # these signals reach the *tail wrapper* on the host, not the
      # orchestrator. The orchestrator is still running on the machine.
      # Leave the machine alone and print reattach hints.
      echo "" >&2
      echo "================================================================" >&2
      remote_log "detached from run ${LEERIE_RUN_ID:-<unknown>} — orchestrator still running on Fly."
      echo "  You stopped watching the tail. The orchestrator was NOT" >&2
      echo "  signalled and keeps making progress on the machine. When" >&2
      echo "  you want to come back:" >&2
      echo "" >&2
      echo "    1. Reattach + resume tailing (most common):" >&2
      echo "         leerie --resume ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "" >&2
      if [ -n "${LEERIE_VOLUME_ID:-}" ]; then
        echo "    2. Pause the machine (graceful stop, keeps work on the" >&2
        echo "       Fly volume; resume later with leerie --resume):" >&2
      else
        echo "    2. Pause the machine (graceful stop; WARNING: no volume" >&2
        echo "       provisioned — stopping may lose /work. Set FLY_VM_DISK_GB" >&2
        echo "       to persist across stops. Resume: leerie --resume):" >&2
      fi
      echo "         leerie --stop ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "" >&2
      echo "    3. Destroy the machine (drops it; only do this once your" >&2
      echo "       work is safely back on the host):" >&2
      echo "         leerie --kill ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "" >&2
      if [ -z "${LEERIE_RUN_ID:-}" ]; then
        echo "  (run-id was not exported yet; check leerie --list to find it.)" >&2
      fi
      # Chain-aware addition: if this run is part of a chain, surface the
      # chain-scoped commands too so the user can act on the whole chain
      # instead of just this run.
      if [ -n "${LEERIE_CHAIN_ID:-}" ]; then
        echo "" >&2
        echo "  This run is part of chain ${LEERIE_CHAIN_ID}. Chain-scoped:" >&2
        echo "    leerie --status ${LEERIE_CHAIN_ID}      # status of all chain runs" >&2
        echo "    leerie --attach ${LEERIE_CHAIN_ID}      # poll until chain done" >&2
        echo "    leerie --stop   ${LEERIE_CHAIN_ID}      # pause all chain runs" >&2
        echo "    leerie --kill   ${LEERIE_CHAIN_ID}      # destroy all chain workers" >&2
      fi
      echo "  Machine: $mid (still running on Fly)" >&2
      echo "================================================================" >&2
      # Intentionally no stop/destroy — the orchestrator must keep running.
      ;;
    *)
      # Unknown non-zero: pause. Stop the machine (preserves filesystem if
      # a volume is attached) and surface the failure to the user via the run
      # sidecar.
      local reason="${LEERIE_PAUSE_REASON:-worker-error}"
      local sidecar=""
      if [ -n "${LEERIE_RUN_ID:-}" ]; then
        if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
          sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
        elif [ -n "${USER_REPO:-}" ]; then
          sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
        fi
      fi
      if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
        update_run_json "$sidecar" \
          paused_at "$(iso_now)" \
          pause_reason "$reason" \
          fly_machine_id "$mid" || true
      fi
      # Sync the run state directory from the machine BEFORE stopping it
      # so the host has up-to-date state.json for subsequent --resume.
      # Best-effort: failure is logged but does not block the pause —
      # the machine-side state is preserved on the volume.
      if [ -n "${LEERIE_RUN_ID:-}" ]; then
        local _sync_target=""
        if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
          _sync_target="$LEERIE_STATE_HOST_DIR/runs"
        elif [ -n "${USER_REPO:-}" ]; then
          _sync_target="$USER_REPO/.leerie/runs"
        fi
        if [ -n "$_sync_target" ]; then
          mkdir -p "$_sync_target" 2>/dev/null || true
          remote_log "remote: syncing state from machine before pause..."
          # Timeout: 60 s (state dir is typically <10 MB; use the
          # _seed_timeout_prefix mechanism but override the budget to
          # avoid blocking decide_teardown for 600 s on a WireGuard
          # stall). Falls back to unbounded on hosts without `timeout`.
          local _pause_timeout=""
          if command -v timeout >/dev/null 2>&1; then
            _pause_timeout="timeout --kill-after=5 60"
          fi
          if $_pause_timeout flyctl ssh console --app "$FLY_APP" --machine "$mid" \
               --pty=false -C "tar -cC /work/.leerie/runs $LEERIE_RUN_ID" \
               2>/dev/null \
             | tar -xC "$_sync_target" 2>/dev/null; then
            remote_log "remote: state synced to $_sync_target/$LEERIE_RUN_ID"
            # The tar extraction overwrites run.json with the machine-side
            # copy (which lacks paused_at/pause_reason/fly_machine_id).
            # Re-apply the pause metadata so --list and --resume see it.
            if [ -n "$sidecar" ]; then
              update_run_json "$sidecar" \
                paused_at "$(iso_now)" \
                pause_reason "$reason" \
                fly_machine_id "$mid" || true
            fi
          else
            remote_log "warning: state sync before pause failed (machine state preserved on volume)"
          fi
        fi
      fi
      stop_machine
      echo "" >&2
      remote_log "PAUSED: machine $mid (rc=$rc, reason=$reason)"
      if [ -n "${LEERIE_RUN_ID:-}" ]; then
        echo "  run-id:  $LEERIE_RUN_ID" >&2
      fi
      echo "  resume:  leerie --resume ${LEERIE_RUN_ID:-<run-id>}" >&2
      echo "  kill:    leerie --kill ${LEERIE_RUN_ID:-<run-id>}" >&2
      if [ -n "${LEERIE_PAUSE_NOTIFY_CMD:-}" ]; then
        eval "$LEERIE_PAUSE_NOTIFY_CMD" || true
      fi
      # Don't clear LEERIE_MACHINE_ID — leave the pointer for the user.
      ;;
  esac
  LEERIE_TEARDOWN_DONE=1
  export LEERIE_TEARDOWN_DONE
}

# --- wait for machine to reach state "started" ---------------------------
# flyctl machine status does NOT accept --json (same caveat as
# `flyctl machine run` — see comment block in provision_machine). Parse
# the text output instead. The output begins with lines like:
#   Machine ID: <id>
#   Instance ID: <iid>
#   State: <state>
# We extract "State: " case-insensitively from the prefix-aligned format
# (note: lower in the output there's also a table with "State │ <state>"
# in box-drawing characters — we match the first standalone "State:" line).
wait_for_started() {
  local mid="$1"
  local deadline=$(( $(date +%s) + MACHINE_START_TIMEOUT ))
  local state=""
  remote_log "remote: waiting for machine $mid to start (timeout: ${MACHINE_START_TIMEOUT}s)..."
  while true; do
    state="$(flyctl machine status "$mid" \
               --app "$FLY_APP" 2>/dev/null \
             | sed 's/\x1b\[[0-9;]*m//g' \
             | awk -F': *' '/^State: / { print $2; exit }' \
             | tr -d '[:space:]' || true)"
    case "$state" in
      started)
        remote_log "remote: machine $mid is started"
        return 0
        ;;
      failed|stopped|destroyed|replacing)
        remote_log "machine $mid entered state '$state' — cannot proceed"
        return 1
        ;;
    esac
    if [ "$(date +%s)" -ge "$deadline" ]; then
      remote_log "timed out waiting for machine $mid to start (${MACHINE_START_TIMEOUT}s)"
      return 1
    fi
    sleep 2
  done
}

# --- provision_machine ---------------------------------------------------
# Creates a Fly Machine from $FLY_IMAGE_TAG, registers the destroy trap,
# and blocks until the machine reaches state "started".
# Requires: $FLY_IMAGE_TAG set by the caller.
# Exports:  $LEERIE_MACHINE_ID
# Returns:  0 on success; 1 on failure (machine is destroyed before returning).
provision_machine() {
  if [ -z "${FLY_IMAGE_TAG:-}" ]; then
    remote_log "FLY_IMAGE_TAG is not set — cannot start a Fly Machine"
    echo "  Build and push the leerie image first:" >&2
    echo "    ./scripts/remote/build-push.sh --app $FLY_APP --push" >&2
    return 1
  fi

  require_flyctl || return 1

  # Opt-in volume: create BEFORE `flyctl machine run` so we can pass the
  # volume ID on the run command (Fly mounts at machine create time, not
  # post-create). If volume-create fails, abort before creating the
  # machine. If it succeeds but the subsequent machine-create fails, the
  # explicit-orphan-cleanup block below reaps the volume — there is no
  # trap covering this window yet (the EXIT trap is only registered
  # after machine create succeeds).
  if [ -n "$FLY_VM_DISK_GB" ]; then
    # Volume name: alphanumeric + underscore, ≤30 chars per Fly's volume
    # naming rule. Use a leerie_data_<6hex> shape so collisions are
    # vanishingly unlikely and the name fits comfortably.
    local vol_name="leerie_data_$(python3 -c 'import secrets; print(secrets.token_hex(3))')"
    remote_log "remote: creating volume $vol_name (${FLY_VM_DISK_GB} GB, region=$FLY_REGION)..."
    local vol_create_output=""
    if ! vol_create_output="$(flyctl volumes create "$vol_name" \
           --app "$FLY_APP" \
           --region "$FLY_REGION" \
           --size "$FLY_VM_DISK_GB" \
           --yes \
           2>&1)"; then
      remote_log "failed to create Fly volume — flyctl output:"
      printf '  %s\n' "$vol_create_output" >&2
      return 1
    fi
    # Extract the volume ID (vol_*) from the create output. flyctl
    # volumes create emits a key/value table; the ID appears in a line
    # like "ID: vol_abc123..." (with possible ANSI color codes).
    LEERIE_VOLUME_ID="$(printf '%s' "$vol_create_output" \
                       | sed 's/\x1b\[[0-9;]*m//g' \
                       | awk '/^[[:space:]]*ID[[:space:]]*[:=]/ { print $2; exit }')"
    if [ -z "$LEERIE_VOLUME_ID" ]; then
      remote_log "failed to extract volume ID from flyctl output:"
      printf '  %s\n' "$vol_create_output" >&2
      # Best-effort orphan reap by name (we don't have the ID).
      flyctl volumes destroy "$vol_name" --app "$FLY_APP" --yes 2>/dev/null || true
      return 1
    fi
    export LEERIE_VOLUME_ID
    remote_log "remote: created volume $LEERIE_VOLUME_ID ($vol_name)"
  else
    remote_log "warning: no volume provisioned (FLY_VM_DISK_GB unset) — /work is ephemeral; a machine stop will lose all work. Set FLY_VM_DISK_GB=N to persist."
  fi

  remote_log "remote: creating machine (app=$FLY_APP region=$FLY_REGION image=$FLY_IMAGE_TAG)..."

  # flyctl machine run --detach starts the machine without streaming its
  # output. Note: flyctl machine run does NOT accept --json (only some
  # other subcommands do — `flyctl machine status --json` for example).
  # The text output contains a line like "Machine ID: <id>" which we
  # parse via awk. Defensively initialize machine_id="" so any future
  # parser failure doesn't trigger set -u at the empty-check below.
  local create_output=""
  local machine_id=""
  # Build the machine-run argv conditionally so the volume-less path is
  # byte-for-byte identical to the pre-FLY_VM_DISK_GB invocation. Bash
  # arrays handle the optional --volume arg without word-splitting
  # surprises.
  local _vol_args=()
  if [ -n "$LEERIE_VOLUME_ID" ]; then
    _vol_args=(--volume "${LEERIE_VOLUME_ID}:/work")
  fi
  # ${arr[@]+"${arr[@]}"} is the bash idiom to expand an array safely
  # under `set -u`: when the array is empty (no volume), the +word
  # substitution produces nothing and `"${arr[@]}"` is never evaluated;
  # when it has elements, they expand verbatim. Plain `"${_vol_args[@]}"`
  # under set -u errors with "unbound variable" on empty arrays.
  if create_output="$(flyctl machine run "$FLY_IMAGE_TAG" \
       --app "$FLY_APP" \
       --region "$FLY_REGION" \
       --vm-cpus "$FLY_VM_CPUS" \
       --vm-memory "$FLY_VM_MEMORY" \
       --vm-cpu-kind "$FLY_VM_CPU_KIND" \
       ${_vol_args[@]+"${_vol_args[@]}"} \
       --detach \
       2>&1)"; then
    # Extract the first whitespace-delimited token after "Machine ID:".
    # Tolerant of ANSI color codes by stripping ESC[<...>m sequences first.
    machine_id="$(printf '%s' "$create_output" \
                  | sed 's/\x1b\[[0-9;]*m//g' \
                  | awk '/Machine ID:/ { for (i=1; i<=NF; i++) if ($i == "ID:") { print $(i+1); exit } }')"
  fi

  if [ -z "$machine_id" ]; then
    remote_log "failed to create Fly Machine — flyctl output:"
    printf '  %s\n' "$create_output" >&2
    # Orphan cleanup: if we created a volume above, the failed machine-run
    # leaves it dangling (no machine ever attached to it). Reap now —
    # the EXIT trap isn't registered yet at this point.
    if [ -n "$LEERIE_VOLUME_ID" ]; then
      remote_log "remote: cleaning up orphan volume $LEERIE_VOLUME_ID after machine-create failure"
      flyctl volumes destroy "$LEERIE_VOLUME_ID" --app "$FLY_APP" --yes 2>/dev/null || true
      LEERIE_VOLUME_ID=""
    fi
    return 1
  fi

  remote_log "remote: created machine $machine_id"
  LEERIE_MACHINE_ID="$machine_id"
  export LEERIE_MACHINE_ID

  # Register teardown trap immediately after a successful creation so Ctrl-C
  # or any error after this point cannot leak the machine. decide_teardown
  # classifies $LEERIE_REMOTE_EXIT_RC and dispatches to stop or destroy.
  # shellcheck disable=SC2064
  trap 'decide_teardown' EXIT INT TERM

  # Persist fly_machine_id to the run sidecar immediately so a launcher
  # crash before classification still leaves a recoverable pointer.
  # DESIGN §6 Remote pause-on-failure: the sidecar is the source of truth
  # for what's recoverable; the env-var-only path of older revisions
  # leaks machines on launcher crash.
  if [ -n "${LEERIE_RUN_ID:-}" ]; then
    local sidecar=""
    if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
      sidecar="$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/run.json"
    elif [ -n "${USER_REPO:-}" ]; then
      sidecar="$USER_REPO/.leerie/runs/$LEERIE_RUN_ID/run.json"
    fi
    if [ -n "$sidecar" ] && [ -f "$sidecar" ]; then
      if [ -n "$LEERIE_VOLUME_ID" ]; then
        update_run_json "$sidecar" \
          fly_machine_id "$machine_id" \
          volume_id "$LEERIE_VOLUME_ID" \
          image_tag "${FLY_IMAGE_TAG:-}" || true
      else
        update_run_json "$sidecar" \
          fly_machine_id "$machine_id" \
          image_tag "${FLY_IMAGE_TAG:-}" || true
      fi
    fi
  fi

  # PID-keyed pointer for `leerie --resume` auto-discovery (no run-id
  # available yet on
  # fresh runs because the orchestrator hasn't minted one). The file is
  # under $LEERIE_STATE_HOST_DIR/remote/<launcher-pid>.json and is removed
  # by destroy_machine on teardown. Also written immediately as
  # runs/<run-id>/fly-machine.json (below) so --resume survives a Ctrl-C
  # between provision_machine() returning and the launcher's copy.
  # (Phase 3: PTY-over-SSH attach.)
  local _state_base=""
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    _state_base="$LEERIE_STATE_HOST_DIR"
  elif [ -n "${USER_REPO:-}" ]; then
    _state_base="$USER_REPO/.leerie"
  fi
  if [ -n "$_state_base" ]; then
    local remote_dir="$_state_base/remote"
    mkdir -p "$remote_dir"
    local pid_record="$remote_dir/$$.json"
    # Record the USER's launch-time --no-push intent here so the host's
    # `leerie --finalize` step can distinguish "user opted out of pushing"
    # from "the in-Fly orchestrator was told not to push because it
    # can't reach github" (the latter is a mechanism flag the launcher
    # forces, NOT a user-intent flag).
    python3 - "$pid_record" "$machine_id" "$FLY_APP" "${LEERIE_RUN_ID:-}" "$$" "${NO_PUSH:-false}" "${LEERIE_VOLUME_ID:-}" <<'PY'
import json, sys, datetime
path, mid, app, run_id, pid, no_push, vol_id = sys.argv[1:]
data = {
    "fly_app": app,
    "fly_machine_id": mid,
    "started_at": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    "run_id": run_id or None,
    "launcher_pid": int(pid),
    "host_no_push": no_push in ("true", "1", "yes"),
}
if vol_id:
    data["volume_id"] = vol_id
with open(path, "w") as f:
    json.dump(data, f, indent=2)
    f.write("\n")
PY
    # Write run-keyed pointer immediately so --resume survives a Ctrl-C
    # between provision_machine() returning and the launcher's deferred copy.
    # The launcher's copy (leerie:2645-2651) is guarded by [ ! -f ] so
    # it becomes a no-op when this write succeeds.
    if [ -n "${LEERIE_RUN_ID:-}" ]; then
      local run_dir="$_state_base/runs/$LEERIE_RUN_ID"
      mkdir -p "$run_dir" 2>/dev/null || true
      cp "$pid_record" "$run_dir/fly-machine.json" 2>/dev/null || true
    fi
  fi

  # Wait until the machine is reachable.
  if ! wait_for_started "$machine_id"; then
    # decide_teardown will fire via the EXIT trap as this function returns 1.
    # The non-zero rc the caller sets will route to destroy (not pause)
    # because a machine that never started has no useful state to inspect.
    return 1
  fi

  return 0
}
