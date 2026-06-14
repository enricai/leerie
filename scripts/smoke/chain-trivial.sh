#!/usr/bin/env bash
# scripts/smoke/chain-trivial.sh — minimal end-to-end smoke test for the
# per-chain ephemeral coordinator.
#
# Prerequisites (set before running):
#   FLY_API_TOKEN       — Fly Machines API token
#   GH_DISPATCH_PAT     — GitHub PAT for coordinator push + PR ops
#   LEERIE_CHAIN_IMAGE  — coordinator image (registry.fly.io/leerie-coordinator:N)
#   LEERIE_WORKER_IMAGE — worker image (registry.fly.io/leerie:N)
#   LEERIE_FLY_APP      — Fly app name (defaults to 'leerie')
#   SMOKE_TARGET_REPO   — https URL of a scratch test repo
#
# What it does:
#   1. Submits a 3-job chain via `leerie --chain`:
#        wave 0: two `echo`-style prompts in parallel
#        wave 1: one `echo`-style prompt that depends on wave 0
#   2. Polls `leerie --status <chain-id>` every 30s until terminal
#      (or timeout).
#   3. Asserts the chain reached `done` and that the audit artifact
#      landed at _leerie-chains/<chain-id>/chain.json in the target repo.
#   4. Cleans up: `leerie --kill <chain-id>` (no-op if coordinator
#      already self-destructed) and removes the local clone of the
#      target repo.
#
# Cost: < $0.05 per run (one coordinator + three workers, each runs
# the trivial prompt for ~30s).
#
# Run from the repo root:
#   bash scripts/smoke/chain-trivial.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
PROMPTS_DIR="$(mktemp -d -t leerie-smoke-prompts.XXXXXX)"
trap 'rm -rf "$PROMPTS_DIR"' EXIT

: "${FLY_API_TOKEN:?required}"
: "${GH_DISPATCH_PAT:?required}"
: "${LEERIE_CHAIN_IMAGE:?required}"
: "${LEERIE_WORKER_IMAGE:?required}"
: "${SMOKE_TARGET_REPO:?required (https URL of a scratch test repo)}"
LEERIE_FLY_APP="${LEERIE_FLY_APP:-leerie}"
export LEERIE_FLY_APP

# Write three trivial prompts.
cat > "$PROMPTS_DIR/wave-0a.md" <<'EOF'
Add a file `wave-0a.txt` to the repo root with the contents `done`.
Open a PR titled "smoke: wave-0a".
EOF

cat > "$PROMPTS_DIR/wave-0b.md" <<'EOF'
Add a file `wave-0b.txt` to the repo root with the contents `done`.
Open a PR titled "smoke: wave-0b".
EOF

cat > "$PROMPTS_DIR/wave-1.md" <<'EOF'
Add a file `wave-1.txt` to the repo root that lists the names of
files added by previous chain runs (read wave-0a.txt and wave-0b.txt
if present). Open a PR titled "smoke: wave-1".
EOF

echo "=== Submitting chain ==="
SUBMIT_OUT="$(
  "$REPO_ROOT/leerie" --chain \
    --target "$SMOKE_TARGET_REPO" \
    --wave "$PROMPTS_DIR/wave-0a.md,$PROMPTS_DIR/wave-0b.md" \
    --wave "$PROMPTS_DIR/wave-1.md"
)"
echo "$SUBMIT_OUT"
CHAIN_ID="$(echo "$SUBMIT_OUT" | awk '/^  chain_id:/ {print $2}')"
if [ -z "$CHAIN_ID" ]; then
  echo "FAIL: could not parse chain_id from submit output" >&2
  exit 1
fi
echo "chain_id: $CHAIN_ID"

# Poll for up to 30 minutes.
MAX_WAIT_S=$((30 * 60))
elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT_S" ]; do
  STATE_JSON="$("$REPO_ROOT/leerie" --status "$CHAIN_ID" 2>/dev/null || true)"
  if [ -z "$STATE_JSON" ]; then
    echo "coordinator unreachable — chain may be complete"
    break
  fi
  STATUS="$(echo "$STATE_JSON" | python3 -c 'import json,sys;print(json.load(sys.stdin).get("status",""))' 2>/dev/null || true)"
  echo "[$elapsed s] chain status: $STATUS"
  case "$STATUS" in
    done|failed|cancelled)
      break
      ;;
  esac
  sleep 30
  elapsed=$((elapsed + 30))
done

if [ "$STATUS" != "done" ]; then
  echo "FAIL: chain did not reach 'done' (final status: $STATUS)" >&2
  "$REPO_ROOT/leerie" --kill "$CHAIN_ID" >/dev/null || true
  exit 1
fi

echo "=== Checking audit artifact ==="
AUDIT_PATH="_leerie-chains/$CHAIN_ID/chain.json"
TMP_CLONE="$(mktemp -d -t leerie-smoke-audit.XXXXXX)"
trap 'rm -rf "$PROMPTS_DIR" "$TMP_CLONE"' EXIT
git clone --depth 1 "https://${GH_DISPATCH_PAT}@${SMOKE_TARGET_REPO#https://}" "$TMP_CLONE" >/dev/null 2>&1
if [ ! -f "$TMP_CLONE/$AUDIT_PATH" ]; then
  echo "FAIL: audit artifact missing at $AUDIT_PATH in $SMOKE_TARGET_REPO" >&2
  exit 1
fi
echo "audit artifact present:"
head -c 500 "$TMP_CLONE/$AUDIT_PATH"
echo

echo "=== Smoke test PASSED ==="
