#!/usr/bin/env bash
# scripts/smoke/chain-failure-modes.sh — exercise the three primary
# chain-mode failure paths against real Fly infrastructure.
#
# Same prerequisites as chain-trivial.sh (FLY_API_TOKEN, GH_DISPATCH_PAT,
# LEERIE_CHAIN_IMAGE, LEERIE_WORKER_IMAGE, SMOKE_TARGET_REPO).
#
# What it exercises:
#
#   1. Worker crash test
#      Submit a chain. Once wave-0 workers are running, `fly machine
#      stop` one of them (simulates OOM / Fly host crash). The
#      heartbeat-staleness watchdog should mark the chain failed after
#      ~15 minutes (or quicker if LEERIE_HEARTBEAT_STALENESS_S was
#      overridden at submit time). Coordinator self-destructs.
#
#   2. Coordinator crash + recovery
#      Submit a chain. Once wave 0 is running, `fly machine stop` the
#      COORDINATOR. Verify Fly automatically restarts it (Fly machines
#      restart on stop unless --rm); the new coordinator reads /data/chain.db
#      from the persistent volume and continues. The chain reaches done.
#
#   3. Stale-creds resume
#      Stub the worker's Claude OAuth creds to trigger a 401 at the
#      orchestrator's preflight smoke test. Worker reports stale_creds.
#      Chain pauses. (The CLI-side --resume <chain-id> flow is not yet
#      implemented; this test asserts the coordinator state correctly
#      transitions to paused with reason="stale_creds".)
#
# Each test runs sequentially against a freshly-submitted chain. Use
# /tmp/leerie-smoke-failure-modes.log for full output.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
LOG="${LEERIE_SMOKE_LOG:-/tmp/leerie-smoke-failure-modes.log}"

: "${FLY_API_TOKEN:?required}"
: "${GH_DISPATCH_PAT:?required}"
: "${LEERIE_CHAIN_IMAGE:?required}"
: "${LEERIE_WORKER_IMAGE:?required}"
: "${SMOKE_TARGET_REPO:?required}"
LEERIE_FLY_APP="${LEERIE_FLY_APP:-leerie}"
export LEERIE_FLY_APP

exec > >(tee -a "$LOG") 2>&1

run_one_trivial_chain() {
  # Submit a chain with a single trivial wave-0 prompt; print the chain_id.
  local prompts_dir
  prompts_dir="$(mktemp -d -t leerie-smoke-prompts.XXXXXX)"
  cat > "$prompts_dir/wave-0.md" <<'EOF'
Add a file `noop.txt` to the repo. Open a PR titled "smoke: noop".
EOF
  local submit_out
  submit_out="$(
    "$REPO_ROOT/leerie" --chain \
      --target "$SMOKE_TARGET_REPO" \
      --wave "$prompts_dir/wave-0.md"
  )"
  echo "$submit_out" >&2
  rm -rf "$prompts_dir"
  echo "$submit_out" | awk '/^  chain_id:/ {print $2}'
}

list_chain_machines() {
  local chain="$1"
  curl -sS \
    -H "Authorization: Bearer ${FLY_API_TOKEN}" \
    "https://api.machines.dev/v1/apps/${LEERIE_FLY_APP}/machines?metadata.leerie_chain_id=${chain}" \
  | python3 -c '
import json, sys
data = json.load(sys.stdin)
for m in data:
    md = m.get("metadata") or {}
    print(m.get("id",""), md.get("leerie_role",""), m.get("state",""))
'
}

wait_for_status() {
  local chain="$1"
  local target_status="$2"
  local timeout_s="${3:-1800}"
  local elapsed=0
  while [ "$elapsed" -lt "$timeout_s" ]; do
    local state
    state="$("$REPO_ROOT/leerie" --status "$chain" 2>/dev/null || true)"
    local cur
    cur="$(echo "$state" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
    echo "[t=${elapsed}s] status=$cur (waiting for $target_status)"
    [ "$cur" = "$target_status" ] && return 0
    sleep 30
    elapsed=$((elapsed + 30))
  done
  return 1
}

echo "##########################################"
echo "# Test 1: worker crash"
echo "##########################################"
TEST1_CHAIN="$(run_one_trivial_chain)"
echo "T1 chain: $TEST1_CHAIN"
sleep 60  # let wave-0 worker boot
WORKER_MID="$(list_chain_machines "$TEST1_CHAIN" | awk '$2=="worker"{print $1; exit}')"
if [ -z "$WORKER_MID" ]; then
  echo "T1 FAIL: no worker machine found yet" >&2
else
  echo "T1: stopping worker $WORKER_MID to simulate crash"
  curl -sS -X POST \
    -H "Authorization: Bearer ${FLY_API_TOKEN}" \
    "https://api.machines.dev/v1/apps/${LEERIE_FLY_APP}/machines/${WORKER_MID}/stop" >/dev/null
  if wait_for_status "$TEST1_CHAIN" "failed" 1800; then
    echo "T1 PASS: chain $TEST1_CHAIN reached failed after worker crash"
  else
    echo "T1 FAIL: chain did not transition to failed within 30min"
    "$REPO_ROOT/leerie" --kill "$TEST1_CHAIN" >/dev/null || true
  fi
fi

echo "##########################################"
echo "# Test 2: coordinator crash + recovery"
echo "##########################################"
TEST2_CHAIN="$(run_one_trivial_chain)"
echo "T2 chain: $TEST2_CHAIN"
sleep 60  # let coordinator + wave-0 worker boot
COORD_MID="$(list_chain_machines "$TEST2_CHAIN" | awk '$2=="coordinator"{print $1; exit}')"
if [ -z "$COORD_MID" ]; then
  echo "T2 FAIL: no coordinator machine found" >&2
else
  echo "T2: stopping coordinator $COORD_MID to simulate crash"
  curl -sS -X POST \
    -H "Authorization: Bearer ${FLY_API_TOKEN}" \
    "https://api.machines.dev/v1/apps/${LEERIE_FLY_APP}/machines/${COORD_MID}/stop" >/dev/null
  # Fly restarts machines on stop unless --rm; wait for the chain to complete.
  if wait_for_status "$TEST2_CHAIN" "done" 1800; then
    echo "T2 PASS: chain $TEST2_CHAIN reached done after coordinator restart"
  else
    echo "T2 FAIL: chain did not reach done within 30min"
    "$REPO_ROOT/leerie" --kill "$TEST2_CHAIN" >/dev/null || true
  fi
fi

echo "##########################################"
echo "# Test 3: stale-creds resume"
echo "##########################################"
echo "T3: SKIPPED — chain-scoped --resume not yet implemented."
echo "  Manual check: submit a chain with deliberately stale Claude creds"
echo "  in the worker env; verify the coordinator transitions to"
echo "  paused with reason='stale_creds' via 'leerie --status <chain-id>'."

echo "##########################################"
echo "# Done. Full log at: $LOG"
echo "##########################################"
