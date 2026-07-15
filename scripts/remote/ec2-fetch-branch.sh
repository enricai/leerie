#!/usr/bin/env bash
# scripts/remote/ec2-fetch-branch.sh — EC2 counterpart to fetch-branch.sh:
# stream a completed leerie run branch and its state back from an EC2
# instance to the host repository.
#
# Same four steps as fetch-branch.sh (run discovery, git-bundle
# stream-back, run-state tar stream-back, best-effort .leerie/config.toml
# + Dockerfile stream-back), transport substituted per DESIGN §6
# "Transport substitution for `flyctl ssh console`":
#
#   - Small text commands (run discovery, branch-existence probe,
#     .leerie/ file existence probes) go over `ec2_remote_exec` (SSM
#     Session Manager, ec2-lib.sh). ec2_remote_exec captures the remote
#     command's stdout via bash command substitution, which is fine for
#     short text output but is NOT binary-safe (it drops trailing
#     newlines/NUL bytes) — it must never be used to carry bundle/tar
#     bytes.
#   - Bulk binary data (the git bundle, the run-state tar, each
#     .leerie/ file's bytes) goes over plain `ssh` directly — the same
#     "closer textual analog to `flyctl ssh console`" transport
#     ec2_tar_pipe uses for the upload direction (DESIGN §6). ec2_tar_pipe
#     itself is upload-only (host stdin -> instance tar -x), so this file
#     defines a small local download counterpart (_ec2_fetch_ssh) that
#     runs a remote command over ssh and streams its raw stdout straight
#     to a host-side file descriptor, preserving binary safety exactly
#     like fetch-branch.sh's `_fetch_machine_exec ... > host_bundle`.
#
# Called from ec2-provision.sh's _try_fetch_state_for_ec2_teardown hook
# (see that function's docstring): sources this file and calls
# fetch_state_ec2(), the EC2 analog of fetch-branch.sh's fetch_branch().
#
# Usage:
#
#   source scripts/remote/ec2-lib.sh
#   source scripts/remote/ec2-fetch-branch.sh
#   fetch_state_ec2        # blocks until fetch is complete
#
# Environment variables consumed:
#
#   LEERIE_EC2_INSTANCE_ID — id of the running EC2 instance
#   LEERIE_EC2_SSH_TARGET  — ssh(1) destination for the instance (e.g.
#                            "ec2-user@<public-ip>" or an ssh_config Host
#                            alias) — resolving an instance id to a
#                            reachable address is provisioning's job, same
#                            as ec2_tar_pipe's own <ssh-target> argument.
#   USER_REPO               — absolute path to the local git repo
#   LEERIE_STATE_HOST_DIR   — optional override for the host state root
#                            (default: USER_REPO/.leerie)
#   LEERIE_SEED_TIMEOUT_S   — stall timeout for the ssh bulk transfers
#                            (default 600s, via _seed_timeout_prefix)
#
# Exports (set by fetch_state_ec2 on success):
#   LEERIE_REMOTE_RUN_ID  — the run-id of the completed run on the instance

set -euo pipefail

_EC2_FETCH_BRANCH_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
. "$_EC2_FETCH_BRANCH_DIR/_log.sh"
# ec2_remote_exec / ec2_tar_pipe / require_aws / _seed_timeout_prefix.
# Guard against double-sourcing clobbering an already-loaded ec2-lib.sh
# (mirrors ec2-seed-repo.sh's independent-sourceability requirement).
if ! command -v ec2_remote_exec >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . "$_EC2_FETCH_BRANCH_DIR/ec2-lib.sh"
fi

# ---------------------------------------------------------------------------
# _ec2_fetch_ssh <remote-cmd>
#
# Run <remote-cmd> on the instance over plain ssh and stream its raw
# stdout to this function's own stdout, unmangled — the download-
# direction counterpart to ec2_tar_pipe's upload-only pipe. Binary-safe
# (no bash command substitution in the data path), so callers redirect
# this function's stdout straight to a host-side file, exactly like
# fetch-branch.sh's `_fetch_machine_exec ... > host_bundle`.
#
# Wrapped in $(_seed_timeout_prefix) so a stalled session yields rc
# 124/137 instead of hanging. Returns the remote command's exit code
# (ssh forwards it natively).
# ---------------------------------------------------------------------------
_ec2_fetch_ssh() {
  local remote_cmd="$1"
  local _to=""
  _to="$(_seed_timeout_prefix)"
  $_to ssh -o BatchMode=yes -o StrictHostKeyChecking=accept-new \
    "$LEERIE_EC2_SSH_TARGET" "$remote_cmd"
}

# ---------------------------------------------------------------------------
# fetch_state_ec2
#
# Find the completed run on the instance, stream its git branch and
# state directory back to the host repo. EC2 analog of fetch-branch.sh's
# fetch_branch().
# ---------------------------------------------------------------------------
fetch_state_ec2() {
  local instance_id="${LEERIE_EC2_INSTANCE_ID:-}"
  if [ -z "$instance_id" ]; then
    remote_log "fetch_state_ec2: LEERIE_EC2_INSTANCE_ID is not set"
    return 1
  fi
  if [ -z "${LEERIE_EC2_SSH_TARGET:-}" ]; then
    remote_log "fetch_state_ec2: LEERIE_EC2_SSH_TARGET is not set"
    return 1
  fi
  if [ -z "${USER_REPO:-}" ]; then
    remote_log "fetch_state_ec2: USER_REPO is not set"
    return 1
  fi
  if command -v require_aws >/dev/null 2>&1; then
    require_aws || return 1
  elif ! command -v aws >/dev/null 2>&1; then
    remote_log "fetch_state_ec2: aws CLI not found on PATH"
    return 1
  fi

  remote_log "remote: fetching completed run from instance $instance_id (ec2) ..."

  # --- Step 1: discover the completed run-id on the instance ---------------
  # Identical discovery script to fetch-branch.sh's Step 1 (finished_at
  # set, pushed_at absent, newest-mtime wins), run over ec2_remote_exec
  # (text-only output — safe for command substitution).
  local run_id run_branch working_branch run_no_push
  local discover_output
  discover_output="$(ec2_remote_exec "$instance_id" 'python3 -c "
import os, json, sys

runs_dir = \"/work/.leerie/runs\"
if not os.path.isdir(runs_dir):
    sys.exit(1)

best = None
best_mtime = 0
for name in os.listdir(runs_dir):
    rj = os.path.join(runs_dir, name, \"run.json\")
    if not os.path.isfile(rj):
        continue
    try:
        d = json.load(open(rj))
    except Exception:
        continue
    if not d.get(\"finished_at\"):
        continue
    if d.get(\"pushed_at\"):
        continue
    mtime = os.stat(rj).st_mtime
    if mtime > best_mtime:
        best_mtime = mtime
        best = (name, d.get(\"branch\", \"\"), d.get(\"working_branch\", \"\"),
                \"true\" if d.get(\"no_push\") else \"false\")

if best is None:
    print(\"ERROR: no completed unpushed run found on machine\")
    sys.exit(1)

print(best[0])
print(best[1])
print(best[2])
print(best[3])
"')" || {
    remote_log "fetch_state_ec2: failed to discover completed run on instance $instance_id"
    echo "  Output: $discover_output" >&2
    return 1
  }

  if printf '%s' "$discover_output" | grep -q "^ERROR:"; then
    remote_log "fetch_state_ec2: $discover_output"
    return 1
  fi

  run_id="$(printf '%s' "$discover_output" | sed -n '1p')"
  run_branch="$(printf '%s' "$discover_output" | sed -n '2p')"
  working_branch="$(printf '%s' "$discover_output" | sed -n '3p')"
  run_no_push="$(printf '%s' "$discover_output" | sed -n '4p')"

  if [ -z "$run_id" ] || [ -z "$run_branch" ]; then
    remote_log "fetch_state_ec2: could not parse run-id or branch from instance output"
    echo "  Output was: $discover_output" >&2
    return 1
  fi

  remote_log "remote: discovered run $run_id (branch: $run_branch)"
  export LEERIE_REMOTE_RUN_ID="$run_id"

  # --- Step 2: stream the run branch via git bundle -------------------------
  # Same two skip scenarios as fetch-branch.sh's Step 2 (cleared-but-empty
  # terminal-state run; other early-failure). Cannot trust
  # run.json.no_push as a proxy — it's a mechanism flag on EC2 too (the
  # instance has no GitHub auth, so the in-instance launcher always
  # passes --no-push).
  local _branch_present="false"
  if ec2_remote_exec "$instance_id" \
       "git -C /work rev-parse --verify refs/heads/$run_branch" >/dev/null 2>&1; then
    _branch_present="true"
  fi

  local _subtask_refs=""
  local _subtask_prefix="leerie/subtasks/${run_id}/"
  _subtask_refs="$(ec2_remote_exec "$instance_id" \
    "git -C /work for-each-ref --format='%(refname:short)' refs/heads/${_subtask_prefix}" \
    2>/dev/null || true)"

  if [ "$_branch_present" = "false" ] && [ -z "$_subtask_refs" ]; then
    remote_log "remote: run branch $run_branch not present on instance; skipping bundle"
  else
    local _bundle_refs=""
    if [ "$_branch_present" = "true" ]; then
      _bundle_refs="$run_branch"
    fi
    local _ref
    for _ref in $_subtask_refs; do
      [ -n "$_ref" ] || continue
      _bundle_refs="$_bundle_refs $_ref"
    done
    _bundle_refs="${_bundle_refs# }"

    local host_bundle
    host_bundle="$(mktemp "${TMPDIR:-/tmp}/leerie-ec2-bundle-XXXXXX.bundle")"
    # shellcheck disable=SC2064
    trap "rm -f '$host_bundle'" RETURN

    local _subtask_count=0
    for _ref in $_subtask_refs; do
      [ -n "$_ref" ] || continue
      _subtask_count=$((_subtask_count + 1))
    done
    if [ "$_branch_present" = "true" ] && [ "$_subtask_count" -gt 0 ]; then
      remote_log "remote: streaming git bundle for $run_branch (+ $_subtask_count subtask branch(es)) ..."
    elif [ "$_branch_present" = "true" ]; then
      remote_log "remote: streaming git bundle for $run_branch ..."
    else
      remote_log "remote: streaming git bundle for $_subtask_count subtask branch(es) (run branch absent) ..."
    fi

    if ! _ec2_fetch_ssh "git -C /work bundle create - $_bundle_refs" \
         > "$host_bundle" 2>/dev/null; then
      if [ "$_branch_present" = "true" ] && [ "$_subtask_count" -gt 0 ]; then
        remote_log "fetch_state_ec2: full bundle failed; retrying with run branch only"
        if ! _ec2_fetch_ssh "git -C /work bundle create - $run_branch" \
             > "$host_bundle" 2>/dev/null; then
          remote_log "fetch_state_ec2: failed to create git bundle on instance $instance_id"
          rm -f "$host_bundle"
          return 1
        fi
        _subtask_refs=""
      else
        remote_log "fetch_state_ec2: failed to create git bundle on instance $instance_id"
        rm -f "$host_bundle"
        return 1
      fi
    fi

    if [ ! -s "$host_bundle" ]; then
      remote_log "fetch_state_ec2: git bundle is empty — run branch may not exist on instance"
      rm -f "$host_bundle"
      return 1
    fi

    if ! git -C "$USER_REPO" bundle verify "$host_bundle" >/dev/null 2>&1; then
      remote_log "fetch_state_ec2: bundle verification failed — possible transfer corruption"
      rm -f "$host_bundle"
      return 1
    fi

    local _fetch_refspecs=""
    if [ "$_branch_present" = "true" ]; then
      _fetch_refspecs="+$run_branch:$run_branch"
    fi
    for _ref in $_subtask_refs; do
      [ -n "$_ref" ] || continue
      _fetch_refspecs="$_fetch_refspecs +$_ref:$_ref"
    done

    # shellcheck disable=SC2086
    if ! git -C "$USER_REPO" fetch "$host_bundle" \
           $_fetch_refspecs 2>/dev/null; then
      remote_log "fetch_state_ec2: git fetch from bundle failed"
      rm -f "$host_bundle"
      return 1
    fi
    rm -f "$host_bundle"
    if [ "$_branch_present" = "true" ]; then
      remote_log "remote: run branch $run_branch fetched to host"
    else
      remote_log "remote: subtask branches fetched to host"
    fi
  fi

  # --- Step 3: stream the .leerie run state directory back -------------------
  local host_leerie_runs
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    host_leerie_runs="$LEERIE_STATE_HOST_DIR/runs"
  else
    host_leerie_runs="$USER_REPO/.leerie/runs"
  fi
  mkdir -p "$host_leerie_runs"

  remote_log "remote: streaming .leerie/runs/$run_id state directory ..."
  if ! _ec2_fetch_ssh "tar -cC /work/.leerie/runs $run_id" \
       | tar -xC "$host_leerie_runs" 2>/dev/null; then
    remote_log "fetch_state_ec2: failed to stream run state directory from instance $instance_id"
    return 1
  fi

  remote_log "remote: run state directory fetched to $host_leerie_runs/$run_id"

  # DEFENSE-IN-DEPTH no_push stripper — identical logic and identical
  # $_branch_present conditional as fetch-branch.sh (see that file's
  # Step 3 comment for the full rationale).
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

  # --- Step 4: stream .leerie/config.toml + Dockerfile back (best-effort) ----
  # Same existence-guarded, non-fatal, never-clobber contract as
  # fetch-branch.sh's Step 4.
  local host_leerie_root
  if [ -n "${LEERIE_STATE_HOST_DIR:-}" ]; then
    host_leerie_root="$LEERIE_STATE_HOST_DIR"
  else
    host_leerie_root="$USER_REPO/.leerie"
  fi

  local _cap_file
  for _cap_file in config.toml Dockerfile; do
    local _remote_path="/work/.leerie/$_cap_file"
    local _host_path="$host_leerie_root/$_cap_file"
    if [ -e "$_host_path" ]; then
      continue
    fi
    if ! ec2_remote_exec "$instance_id" "test -f '$_remote_path'" >/dev/null 2>&1; then
      continue
    fi
    remote_log "remote: streaming .leerie/$_cap_file to host ..."
    mkdir -p "$host_leerie_root" 2>/dev/null || true
    if _ec2_fetch_ssh "cat '$_remote_path'" > "$_host_path" 2>/dev/null; then
      remote_log "remote: .leerie/$_cap_file written to $host_leerie_root/"
    else
      rm -f "$_host_path" 2>/dev/null || true
      remote_log "fetch_state_ec2: warning: failed to stream .leerie/$_cap_file (non-fatal)"
    fi
  done

  remote_log "remote: fetch complete — run $run_id ready for host-side finalize"
  return 0
}
