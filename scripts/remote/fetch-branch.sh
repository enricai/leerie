#!/usr/bin/env bash
# scripts/remote/fetch-branch.sh — stream a completed leerie run branch and
# its state back from a Fly Machine to the host repository.
#
# After the orchestrator runs inside a Fly Machine, the run branch
# (leerie/runs/<run-id>) and the .leerie/runs/<run-id>/ state directory live on
# the machine's filesystem — not on the host.  This script streams both back
# so the existing host-side finalize block (git push + gh pr create) can run
# unchanged with the host's own auth.
#
# Two-channel fetch back to host:
#
#   1. Run branch — git bundle piped from machine to host, then fetched into
#      the host's local repo.  A bundle is the correct mechanism because the
#      host's repo shares the same ancestry (origin remote), so the bundle can
#      be fetched by ref name alone — no remote URL configuration on the machine
#      is required.
#
#   2. Run state — .leerie/runs/<run-id>/ tarred on the machine and piped to the
#      host.  The host-side finalize reads run.json and state.json from this
#      directory; they are not in git.
#
# (The seed direction — host to machine — is single-channel: tar-pipe a
# git-aware payload of `git ls-files` + `.git/` + force-included `.claude/`
# minus `.leerie/` directly to /work. See scripts/remote/seed-repo.sh.)
#
# Usage (called by the leerie launcher after remote orchestration exits 0):
#
#   source scripts/remote/fetch-branch.sh
#   fetch_branch        # blocks until fetch is complete
#
# Environment variables consumed:
#
#   LEERIE_MACHINE_ID  — ID of the Fly Machine (set by provision.sh)
#   LEERIE_FLY_APP     — Fly.io app name (default: "leerie")
#   USER_REPO        — absolute path to the local git repo (set by launcher)
#
# Exports (set by fetch_branch on success):
#   LEERIE_REMOTE_RUN_ID  — the run-id of the completed run on the machine

set -euo pipefail

# remote_log helper. Sourced directly (not via lib.sh) so the standalone
# test path in tests/test_fetch_branch_sh.py — which doesn't source lib.sh
# — still gets the logger without pulling in the rest of lib.sh's surface.
_FETCH_BRANCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_FETCH_BRANCH_DIR/_log.sh"

FLY_APP="${LEERIE_FLY_APP:-leerie}"

# ---------------------------------------------------------------------------
# _fetch_machine_exec <cmd>...
#
# Run a command on the Fly Machine. Current flyctl `machine exec`
# accepts only a single command-string argument (post-`--` argv was
# removed alongside `--stdin`). Use `ssh console -C` and join the
# argv into a single shell-escaped string so the remote shell sees
# the same effective command.
# ---------------------------------------------------------------------------
_fetch_machine_exec() {
  local cmd
  cmd="$(python3 -c '
import shlex, sys
print(" ".join(shlex.quote(a) for a in sys.argv[1:]))
' "$@")"
  flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
    --pty=false -C "$cmd"
}

# ---------------------------------------------------------------------------
# fetch_branch
#
# Find the completed run on the machine, stream its git branch and state
# directory back to the host repo.
# ---------------------------------------------------------------------------
fetch_branch() {
  local machine_id="${LEERIE_MACHINE_ID:-}"
  if [ -z "$machine_id" ]; then
    remote_log "fetch_branch: LEERIE_MACHINE_ID is not set"
    return 1
  fi
  if [ -z "${USER_REPO:-}" ]; then
    remote_log "fetch_branch: USER_REPO is not set"
    return 1
  fi
  # flyctl presence + auth via the shared helper from lib.sh. The launcher's
  # RUNTIME=fly preflight already calls require_flyctl; this is belt-and-
  # braces for callers that source fetch-branch.sh standalone.
  if ! command -v require_flyctl >/dev/null 2>&1; then
    if ! command -v flyctl >/dev/null 2>&1; then
      remote_log "fetch_branch: flyctl not found on PATH"
      return 1
    fi
  else
    require_flyctl || return 1
  fi

  remote_log "remote: fetching completed run from machine $machine_id ..."

  # --- Step 1: discover the completed run-id on the machine ----------------
  # The orchestrator writes .leerie/runs/<run-id>/run.json with finished_at
  # when phase_finalize OR `_finish_no_work_run` completes. Pick the run
  # whose run.json has finished_at set and pushed_at absent — same logic
  # the host-side finalize block uses. Carry the run's `no_push` flag
  # back as a fourth output line so the bundle step (Step 2) can be
  # skipped for cleared-but-empty terminal-state runs (DESIGN §8 — no
  # setup-run.sh ran, so there is no run branch to bundle on the
  # machine; the state dir still needs to come back so `leerie --list`
  # shows the run as `done`). Use python3 (always available in
  # the leerie image) to parse the JSON safely.
  local run_id run_branch working_branch run_no_push
  local discover_output
  # NOTE: 2>/dev/null (not 2>&1) — flyctl ssh console prints
  # "Connecting to fdaa:..." to stderr, and merging that into stdout
  # would shift every parsed line by one, corrupting run_id /
  # run_branch / working_branch / no_push. The python script writes
  # its diagnostics with sys.exit(1) on stderr-only paths, so losing
  # stderr is acceptable; the host-side error message below conveys
  # the failure type adequately.
  local _discover_err
  _discover_err="$(mktemp)"
  discover_output="$(_fetch_machine_exec python3 -c '
import os, json, sys

runs_dir = "/work/.leerie/runs"
if not os.path.isdir(runs_dir):
    sys.exit(1)

best = None
best_mtime = 0
for name in os.listdir(runs_dir):
    rj = os.path.join(runs_dir, name, "run.json")
    if not os.path.isfile(rj):
        continue
    try:
        d = json.load(open(rj))
    except Exception:
        continue
    if not d.get("finished_at"):
        continue
    if d.get("pushed_at"):
        continue
    mtime = os.stat(rj).st_mtime
    if mtime > best_mtime:
        best_mtime = mtime
        best = (name, d.get("branch", ""), d.get("working_branch", ""),
                "true" if d.get("no_push") else "false")

if best is None:
    print("ERROR: no completed unpushed run found on machine")
    sys.exit(1)

print(best[0])
print(best[1])
print(best[2])
print(best[3])
' 2>"$_discover_err")" || {
    remote_log "fetch_branch: failed to discover completed run on machine $machine_id"
    echo "  Output: $discover_output" >&2
    echo "  Stderr: $(cat "$_discover_err")" >&2
    rm -f "$_discover_err"
    return 1
  }
  rm -f "$_discover_err"

  # Check for ERROR prefix from the Python script
  if printf '%s' "$discover_output" | grep -q "^ERROR:"; then
    remote_log "fetch_branch: $discover_output"
    return 1
  fi

  run_id="$(printf '%s' "$discover_output" | sed -n '1p')"
  run_branch="$(printf '%s' "$discover_output" | sed -n '2p')"
  working_branch="$(printf '%s' "$discover_output" | sed -n '3p')"
  run_no_push="$(printf '%s' "$discover_output" | sed -n '4p')"

  if [ -z "$run_id" ] || [ -z "$run_branch" ]; then
    remote_log "fetch_branch: could not parse run-id or branch from machine output"
    echo "  Output was: $discover_output" >&2
    return 1
  fi

  remote_log "remote: discovered run $run_id (branch: $run_branch)"
  export LEERIE_REMOTE_RUN_ID="$run_id"

  # --- Step 2: stream the run branch via git bundle -------------------------
  # Probe whether the run branch actually exists on the machine. Two
  # scenarios where it doesn't:
  #   (a) cleared-but-empty terminal-state run (DESIGN §6) — the
  #       orchestrator exited cleanly because the task was already
  #       satisfied on HEAD; setup-run.sh never ran; no branch.
  #   (b) some other early-failure / placeholder case.
  # In both, skip the bundle step.
  #
  # We CANNOT trust run.json.no_push as a proxy: the in-Fly launcher
  # always passes --no-push to the orchestrator (the machine can't
  # push) so no_push=true is a mechanism flag, not a "no branch was
  # materialized" signal.
  local _branch_present="false"
  if _fetch_machine_exec git -C /work rev-parse --verify "refs/heads/$run_branch" >/dev/null 2>&1; then
    _branch_present="true"
  fi
  if [ "$_branch_present" = "false" ]; then
    remote_log "remote: run branch $run_branch not present on machine; skipping bundle"
  else
    # Create a bundle on the machine containing the run branch and all
    # its ancestry, then pipe it to the host and fetch from it.  The host
    # already has all history from origin (seeded via clone), so the
    # bundle resolves cleanly against the local repo objects.
    #
    # Bundle path is a tmpfile on the machine; we stream via stdout and
    # consume on the host side via `git fetch` reading from a temp
    # bundle file.
    local host_bundle
    host_bundle="$(mktemp "${TMPDIR:-/tmp}/leerie-bundle-XXXXXX.bundle")"
    # shellcheck disable=SC2064
    trap "rm -f '$host_bundle'" RETURN

    remote_log "remote: streaming git bundle for $run_branch ..."
    # git bundle create writes the bundle to stdout when given "-" as
    # the file. flyctl machine exec streams that stdout back to the host.
    if ! _fetch_machine_exec \
         git -C /work bundle create - "$run_branch" \
         > "$host_bundle" 2>/dev/null; then
      remote_log "fetch_branch: failed to create git bundle on machine $machine_id"
      rm -f "$host_bundle"
      return 1
    fi

    if [ ! -s "$host_bundle" ]; then
      remote_log "fetch_branch: git bundle is empty — run branch may not exist on machine"
      rm -f "$host_bundle"
      return 1
    fi

    # Verify the bundle is valid before attempting fetch.
    if ! git -C "$USER_REPO" bundle verify "$host_bundle" >/dev/null 2>&1; then
      remote_log "fetch_branch: bundle verification failed — possible transfer corruption"
      rm -f "$host_bundle"
      return 1
    fi

    # Fetch the run branch into the host repo from the bundle.
    # `git fetch <bundle> <refspec>` creates the local branch.
    if ! git -C "$USER_REPO" fetch "$host_bundle" \
           "+$run_branch:$run_branch" 2>/dev/null; then
      remote_log "fetch_branch: git fetch from bundle failed"
      rm -f "$host_bundle"
      return 1
    fi
    rm -f "$host_bundle"
    remote_log "remote: run branch $run_branch fetched to host"
  fi

  # --- Step 3: stream the .leerie run state directory back -------------------
  # The host-side finalize reads runs/<run-id>/run.json (for finished_at,
  # branch, working_branch) and state.json (for PR body composition).
  # Tar the whole run directory from the machine and extract it under the
  # host state dir (LEERIE_STATE_HOST_DIR if set, else USER_REPO/.leerie).
  local run_state_dir="/work/.leerie/runs/$run_id"
  local host_leerie_runs
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    host_leerie_runs="$LEERIE_STATE_HOST_DIR/runs"
  else
    host_leerie_runs="$USER_REPO/.leerie/runs"
  fi
  mkdir -p "$host_leerie_runs"

  remote_log "remote: streaming .leerie/runs/$run_id state directory ..."
  if ! _fetch_machine_exec \
       tar -cC /work/.leerie/runs "$run_id" \
       | tar -xC "$host_leerie_runs" 2>/dev/null; then
    remote_log "fetch_branch: failed to stream run state directory from machine $machine_id"
    return 1
  fi

  remote_log "remote: run state directory fetched to $host_leerie_runs/$run_id"

  # DEFENSE-IN-DEPTH: strip a stray mechanism-flag no_push=true from
  # the host-side run.json **only when a run branch was actually
  # fetched**. The in-Fly orchestrator gets --no-push (because the Fly
  # Machine has no GitHub auth) AND --host-no-push carrying the user's
  # actual launch-time intent; phase_finalize writes **intent**
  # (`not push_will_happen(...)`) to run.json.no_push, so this stripper
  # is a no-op for orchestrators built against the intent-vs-mechanism
  # split. It is retained as defense against in-flight runs started
  # against an older image where the orchestrator still wrote the
  # mechanism flag and a branch *did* materialize.
  #
  # Conditional on $_branch_present: when no branch was fetched (the
  # cleared-but-empty terminal-state case — DESIGN §8), the orchestrator's
  # `_finish_no_work_run` deliberately writes `no_push=true` as **intent**
  # ("nothing to push — no branch exists"). Stripping it here would
  # disarm host_finalize's no_push gate and cause it to attempt
  # `git push` on a non-existent branch.
  if [ "$_branch_present" = "true" ]; then
    local _host_run_json="$host_leerie_runs/$run_id/run.json"
    if [ -f "$_host_run_json" ]; then
      python3 - "$_host_run_json" <<'PY' || true
import json, os, sys
path = sys.argv[1]
try:
    data = json.load(open(path))
except Exception:
    sys.exit(0)
if data.get("no_push") is True:
    data.pop("no_push", None)
    tmp = path + ".tmp"
    json.dump(data, open(tmp, "w"), indent=2)
    open(tmp, "a").write("\n")
    os.replace(tmp, path)
PY
    fi
  fi

  remote_log "remote: fetch complete — run $run_id ready for host-side finalize"
  return 0
}
