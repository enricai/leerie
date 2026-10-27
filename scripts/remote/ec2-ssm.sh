#!/usr/bin/env bash
# scripts/remote/ec2-ssm.sh — the launch and attach transport for the EC2
# runtime: the substitute for `flyctl ssh console`'s *launch* and *attach*
# roles (DESIGN §6 "Transport substitution for `flyctl ssh console`"; the
# stream-back role is ec2-fetch-branch.sh, the small-command-exec and
# bulk-upload roles are ec2-lib.sh's ec2_remote_exec / ec2_tar_pipe).
#
# Despite the file name, launch and attach go over SSH:
#   ssh … ec2-user@<ip> "sudo -n python3 -"   (launch wrapper on stdin)
#   ssh … ec2-user@<ip> "sudo -n sh -s"       (tail wrapper on stdin)
# are the analogs of `flyctl ssh console --pty=false -C "python3 -"` /
# `-C "sh -s"`. An SSM AWS-StartInteractiveCommand session cannot carry a
# piped payload: its command runs on a pty, so `python3 -` opens its
# interactive prompt and `sh -s` prints one, and the plugin ends the
# session at host-stdin EOF before the payload is read (measured on a
# real instance). A non-pty SSH channel delivers the payload intact,
# streams output, and returns the remote exit code natively — which is how
# callers recover rc=75, the flock-loser smart-resume pivot (DESIGN §6
# "Smart resume in remote mode").
#
# SSM remains for the one interactive role, `--shell` on a live run, where
# a pty is exactly what is wanted (_ec2_ssm_shell).
#
# Usage:
#   export LEERIE_EC2_INSTANCE_ID=i-0123456789abcdef0
#   printf '%s' "$launch_wrapper_script" | ec2_launch_detached
#   printf '%s' "$tail_wrapper_script"   | ec2_attach
#
# All fail closed (return 1, actionable stderr, no aws/ssh call) when
# LEERIE_EC2_INSTANCE_ID is empty.

set -euo pipefail

_EC2_SSM_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Guard against double-sourcing clobbering an already-loaded ec2-lib.sh
# (mirrors ec2-fetch-branch.sh's / ec2-seed-repo.sh's independent-
# sourceability requirement).
if ! command -v ec2_remote_exec >/dev/null 2>&1; then
  # shellcheck disable=SC1091
  . "$_EC2_SSM_DIR/ec2-lib.sh"
fi

# --- _ec2_ssh_session ------------------------------------------------------
# Run <interpreter> on the instance under `sudo -n`, with this function's
# stdin as the interpreter's stdin. Same target and ssh options as
# ec2_remote_exec, but no $(_seed_timeout_prefix): a log tail is long-lived
# by design. Returns the remote exit code.
_ec2_ssh_session() {
  local interpreter="$1"
  local instance_id="${LEERIE_EC2_INSTANCE_ID:-}"
  if [ -z "$instance_id" ]; then
    remote_log "error: LEERIE_EC2_INSTANCE_ID is not set — cannot reach the instance."
    echo "  Run: leerie list --runtime ec2  (to find the run's instance), then re-run leerie resume." >&2
    return 1
  fi
  local target
  target="$(_ec2_ssh_target_for "$instance_id")" || {
    remote_log "error: no SSH address for instance $instance_id (no public IP?)"
    echo "  Run: aws ec2 describe-instances --instance-ids $instance_id  (it must be running with a public IP)." >&2
    return 1
  }
  local _opts=() _a
  while IFS= read -r _a; do _opts+=("$_a"); done < <(_ec2_ssh_opts "$instance_id")
  # shellcheck disable=SC2029  # the interpreter is chosen host-side
  ssh "${_opts[@]}" "$target" "sudo -n $interpreter"
}

# --- _ec2_ssm_shell -----------------------------------------------------------
# An interactive root shell at /work over Session Manager, attached to the
# operator's terminal — the one role where SSM's pty is what is wanted.
# The command holds no double quotes: it sits inside the CLI's
# `command=["..."]` shorthand.
_ec2_ssm_shell() {
  local instance_id="${LEERIE_EC2_INSTANCE_ID:-}"
  if [ -z "$instance_id" ]; then
    remote_log "error: LEERIE_EC2_INSTANCE_ID is not set — cannot open a shell."
    return 1
  fi
  local aws_args=(--target "$instance_id" --document-name AWS-StartInteractiveCommand
                  --parameters 'command=["cd /work && exec sudo -n bash -i"]') _a
  while IFS= read -r _a; do aws_args+=("$_a"); done < <(_aws_region_profile_args)
  aws ssm start-session "${aws_args[@]}"
}

# --- ec2_launch_detached -----------------------------------------------
# Start the detached-orchestrator launch wrapper (this function's stdin) on
# the instance via `python3 -`. Returns the wrapper's real exit code,
# notably rc=75 (the flock-loser smart-resume pivot).
ec2_launch_detached() {
  _ec2_ssh_session "python3 -"
}

# --- ec2_attach ----------------------------------------------------------
# Run the tail-wrapper payload (this function's stdin, from
# render_tail_wrapper) against the running orchestrator's log. Returns the
# remote exit code (the tail wrapper's ORCH_EXIT).
ec2_attach() {
  _ec2_ssh_session "sh -s"
}

# --- _attach_to_live_orchestrator_ec2 ---------------------------------------
# EC2 counterpart of lib.sh's _attach_to_live_orchestrator: attach to an
# already-running orchestrator on the instance — either open a bash shell
# at /work (RESUME_SHELL=true) or tail the live log stream via
# render_tail_wrapper (lib.sh) + ec2_attach. The shell goes over SSM
# (_ec2_ssm_shell): a human at a terminal is what its pty is for.
#
# Call sites (leerie launcher's RUNTIME=ec2 branch):
#   - The early flock probe (resume path, before seed_auth).
#   - The launch-wrapper rc=75 pivot (after seed_auth, belt-and-suspenders).
#
# Reads from caller's scope: RESUME_SHELL, LEERIE_RUN_ID,
# LEERIE_EC2_INSTANCE_ID.
#
# No `resume --auto-finalize` token plumbing on this attach path (unlike
# lib.sh's tail_with_optional_autofinalize; a documented EC2 gap). The
# clean-exit auto-finalize itself lives in decide_ec2_teardown, which calls
# host_finalize (DESIGN §6 "Finalization parity").
#
# Side-effect: sets container_rc=130 in the caller's scope so
# decide_ec2_teardown leaves the instance alone (DESIGN §6 "Smart resume
# in remote mode").
_attach_to_live_orchestrator_ec2() {
  if [ "${RESUME_SHELL:-false}" = "true" ]; then
    remote_log "resume: orchestrator already running for $LEERIE_RUN_ID; opening shell at /work"
    _ec2_ssm_shell || true
  else
    remote_log "resume: orchestrator already running for $LEERIE_RUN_ID; attaching to live log stream (Ctrl-C to detach — orchestrator keeps running)"
    local _tail_script _tail_invocation
    _tail_script="$(render_tail_wrapper)"
    _tail_invocation="LEERIE_TAIL_RUN_ID='$LEERIE_RUN_ID'; export LEERIE_TAIL_RUN_ID
$_tail_script"
    printf '%s' "$_tail_invocation" | ec2_attach || true
  fi
  # shellcheck disable=SC2034  # set in the CALLER's scope on purpose —
  # see this function's 'Side-effect: sets container_rc=130' header
  container_rc=130
}
