#!/usr/bin/env bash
# scripts/host-finalize.sh — host-side finalize block (push + PR creation).
#
# Sourced by the `leerie` launcher's normal post-run path and by the
# `leerie --finalize <run-id>` fast-path. Both code paths share the same
# push/PR mechanics — the discovery of *which* run to finalize differs.
#
# Exports: host_finalize <run-dir>
#
# Inputs (env or args):
#   $1                — absolute path to .leerie/runs/<run-id>/
#   USER_REPO         — git repo root (set by launcher)
#   NO_VERIFY_PUSH    — "true" to pass --no-verify to git push (optional)
#
# Side effects:
#   - git push -u origin <run-branch>
#   - gh pr create
#   - update_run_json on the sidecar to record pushed_at / pr_url / errors
#
# Exit code semantics:
#   0 — success (push OK; PR may have failed non-fatally)
#   1 — push failed (no PR attempted)
#
# DESIGN §6 *Finalization*. The host owns this step because gh auth,
# ssh-agent, and Keychain are host-side; the Fly Machine cannot push.

# update_run_json (local jq-based helper). Kept here rather than in
# scripts/remote/lib.sh because that one uses python3, and the launcher
# already imports jq via the preflight; using jq keeps this file
# python-free and matches the in-place semantics of the original block.
_host_finalize_update_run_json() {
  local sidecar="$1"
  shift
  local tmp="$sidecar.tmp"
  local jq_filter='.'
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    if [ -z "$val" ]; then
      jq_filter="${jq_filter} | .${key} = null"
    else
      jq_filter="${jq_filter} | .${key} = \$${key}"
    fi
  done
  local args=()
  for kv in "$@"; do
    local key="${kv%%=*}"
    local val="${kv#*=}"
    [ -z "$val" ] || args+=(--arg "$key" "$val")
  done
  jq "${args[@]+"${args[@]}"}" "$jq_filter" "$sidecar" > "$tmp"
  mv "$tmp" "$sidecar"
}

host_finalize() {
  local run_dir="$1"
  if [ -z "$run_dir" ] || [ ! -d "$run_dir" ]; then
    echo "host_finalize: missing or invalid <run-dir>: $run_dir" >&2
    return 1
  fi

  local run_json="$run_dir/run.json"
  local state_json="$run_dir/state.json"
  if [ ! -f "$run_json" ]; then
    echo "host_finalize: no run.json at $run_json" >&2
    return 1
  fi

  # Honor run.json.no_push (--no-push or no-work short-circuit).
  if [ "$(jq -r '.no_push // false' "$run_json")" = "true" ]; then
    echo "[leerie] finalize: run.json has no_push=true; skipping push + PR" >&2
    return 0
  fi

  # Early idempotency short-circuit (DESIGN §6 *Finalization*). `pushed_at`
  # records *that* a push happened, not *what* it pushed — a finalize that
  # fired mid-integration (a mid-wave die() stamped finished_at early) can
  # leave pushed_at set on a PARTIAL branch. So this is tip-aware: only
  # no-op when origin ALREADY matches the local run-branch tip (the
  # genuinely-pushed case). Doing this *before* the completion gate
  # preserves the invariant that re-finalizing an already-pushed run is
  # always a safe no-op — even if state.json shows a stale/incomplete
  # wave count (a resume artifact, or a run pushed under old semantics).
  # If pushed_at is set but origin is BEHIND local (a partial prior push),
  # we deliberately fall through to the completion gate below, so the
  # re-push is still gated on completed_waves == len(waves). We resolve the
  # branch here (duplicated with the block below) rather than reordering
  # the whole function, keeping the change surgical.
  if [ -n "$(jq -r '.pushed_at // ""' "$run_json")" ]; then
    local _rb _lt _ot
    _rb="$(jq -r '.branch // ""' "$run_json")"
    if [ -n "$_rb" ] && git -C "$USER_REPO" rev-parse --verify "refs/heads/$_rb" >/dev/null 2>&1; then
      # `|| true`: under the launcher's `set -euo pipefail`, a failing
      # `ls-remote` (no origin remote, or origin lacks the ref — the exact
      # partial-push shape) would otherwise abort finalize via pipefail.
      _lt="$(git -C "$USER_REPO" rev-parse "refs/heads/$_rb" 2>/dev/null || true)"
      _ot="$(git -C "$USER_REPO" ls-remote origin "refs/heads/$_rb" 2>/dev/null | cut -f1 || true)"
      if [ -n "$_ot" ] && [ "$_lt" = "$_ot" ]; then
        echo "[leerie] finalize: run already pushed (origin up to date); nothing to do" >&2
        return 0
      fi
      # Only fall through to re-push when the re-push would fast-forward —
      # i.e. origin's tip is a strict ancestor of the local tip. A DIVERGED
      # origin (has commits local lacks) is NOT a partial push we can
      # safely fast-forward; short-circuit it here rather than letting the
      # plain `git push` below reject it into the push_error path. Origin
      # absent (`_ot` empty) is treated as behind → re-push creates the ref.
      if [ -n "$_ot" ] && \
         ! git -C "$USER_REPO" merge-base --is-ancestor "$_ot" "$_lt" 2>/dev/null; then
        echo "[leerie] finalize: run already pushed; origin has diverged from the local run branch — not re-pushing (resolve manually)" >&2
        return 0
      fi
    fi
    # pushed_at set but origin strictly behind (or absent) → partial push
    # we can fast-forward. Fall through to the completion gate + re-push.
  fi

  # Completion gate (DESIGN §6 *finished_at is a discovery sentinel, not a
  # completion signal*). run.json's finished_at is stamped by the die-path
  # SystemExit handler on ANY mid-wave abort (needed for run discovery), so
  # it does NOT mean the run's waves all integrated. Pushing such a run
  # opens a PR containing only the waves that finished before the crash
  # (the PR-#22 incident). This is the single chokepoint every host-side
  # push path funnels through (the auto-finalize block, the --finalize
  # verb, and Fly decide_teardown all call host_finalize), so gating here
  # covers them all. The signal lives in state.json (run.json never carries
  # completed_waves/waves); this mirrors _derive_run_status case 6½.
  # Fail-open: if state.json is absent or its wave fields are non-numeric,
  # do NOT block — a legitimately complete run must never be refused over a
  # missing/unreadable file. The cleared-but-empty terminal state
  # (waves==[]) sets no_push=true and already returned above; even if it
  # reached here, `0 < len([])` is false, so it is not blocked.
  if [ -f "$state_json" ]; then
    local _no_work _completed _wave_total
    _no_work="$(jq -r '.no_work_required // false' "$state_json" 2>/dev/null)"
    _completed="$(jq -r '.completed_waves // 0' "$state_json" 2>/dev/null)"
    _wave_total="$(jq -r '.waves | length' "$state_json" 2>/dev/null)"
    case "$_completed:$_wave_total" in
      *[!0-9:]*) : ;;  # non-numeric (jq error / null) → fail-open, skip gate
      *)
        if [ "$_no_work" != "true" ] && [ "$_completed" -lt "$_wave_total" ]; then
          echo "leerie: error — refusing to finalize run ${run_dir##*/}: only" \
               "$_completed of $_wave_total waves integrated (run crashed" \
               "mid-wave). Resume to finish: leerie --resume ${run_dir##*/}" >&2
          return 1
        fi
        ;;
    esac
  fi

  local run_id run_branch working_branch
  run_id="$(basename "$run_dir")"
  run_branch="$(jq -r '.branch // ""' "$run_json")"
  working_branch="$(jq -r '.working_branch // ""' "$run_json")"
  if [ -z "$run_branch" ] || [ -z "$working_branch" ]; then
    echo "leerie: error — run.json at $run_json is missing branch info." >&2
    echo "  Skipping push + PR. Push the run branch manually if it exists." >&2
    return 1
  fi

  # Defense-in-depth: a run branch named in run.json that does not
  # exist locally cannot be pushed. This shape is legitimate for the
  # cleared-but-empty terminal state (DESIGN §8 — no setup-run.sh
  # ran, so no branch was created). Treat it as a no-op rather than
  # attempting a `git push` that will fail with `src refspec ... does
  # not match any`. Upstream callers (fetch-branch.sh's stripper and
  # the --finalize stripper in `leerie`) already preserve no_push=true
  # for this case; this guard backstops them.
  if ! git -C "$USER_REPO" rev-parse --verify "refs/heads/$run_branch" >/dev/null 2>&1; then
    echo "[leerie] finalize: run branch $run_branch absent locally; treating as no-op" >&2
    return 0
  fi

  # Note the re-push (DESIGN §6 *Finalization*). If pushed_at is set and we
  # reached here, the early short-circuit above already ruled out both the
  # equal-tips no-op AND the diverged-origin case — so origin is a strict
  # ancestor of (or absent vs) local: a prior finalize pushed a PARTIAL
  # branch. We have now PASSED the completion gate, so a re-push publishes
  # a complete branch, and the push below is guaranteed to fast-forward.
  # pushed_at stays set, so the chain wave-skip signal (which reads the
  # field, not the tip) is unaffected.
  if [ -n "$(jq -r '.pushed_at // ""' "$run_json")" ]; then
    echo "[leerie] finalize: run marked pushed but origin is not up to date (partial prior push); re-pushing" >&2
  fi

  # --- step 1: push -----------------------------------------------------
  local push_args=(git -C "$USER_REPO" push -u origin "$run_branch")
  [ "${NO_VERIFY_PUSH:-false}" = "true" ] && push_args+=(--no-verify)

  echo "[leerie] finalize: pushing $run_branch to origin$([ "${NO_VERIFY_PUSH:-false}" = "true" ] && echo " (--no-verify)")" >&2
  local push_stderr
  if ! push_stderr="$("${push_args[@]}" 2>&1 >/dev/null)"; then
    _host_finalize_update_run_json "$run_json" \
      "pushed_at=" "push_error=${push_stderr:-git push failed}" \
      "pr_url=" "pr_error="
    echo "leerie: error: git push failed for branch \`$run_branch\`." >&2
    echo "  Local state is intact:" >&2
    echo "    - run branch:     $run_branch (holds all wave merges)" >&2
    echo "    - working branch: $working_branch (unchanged from run start; the intended PR base)" >&2
    echo "  Resolve and retry manually:" >&2
    echo "    git push -u origin $run_branch$([ "${NO_VERIFY_PUSH:-false}" = "true" ] && echo " --no-verify")" >&2
    echo "  Push stderr was:" >&2
    printf '    %s\n' "$push_stderr" >&2
    return 1
  fi
  local pushed_at
  pushed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  _host_finalize_update_run_json "$run_json" "pushed_at=$pushed_at" "push_error="
  echo "[leerie] finalize: pushed $run_branch" >&2

  # --- step 2: PR creation ---------------------------------------------
  # Primary path: the pr_writer worker (DESIGN §6 *Finalization*) wrote
  # pr_title + pr_body to run.json. Use them when present.
  # Fallback: compose a deterministic body from state.json.
  local pr_title pr_body pr_title_llm pr_body_llm
  pr_title_llm="$(jq -r '.pr_title // ""' "$run_json")"
  pr_body_llm="$(jq -r '.pr_body // ""' "$run_json")"

  if [ -n "$pr_title_llm" ] && [ -n "$pr_body_llm" ]; then
    pr_title="leerie: $pr_title_llm"
    pr_body="$pr_body_llm"
  else
    # Deterministic fallback. Every `jq` here reads $state_json, which may
    # be ABSENT (the fail-open completion-gate case reaches here when the
    # pr_writer worker didn't populate pr_title/pr_body). `2>/dev/null ||
    # true` keeps each read from aborting finalize under the launcher's
    # `set -euo pipefail` when the file is missing/unreadable; the empty
    # result then degrades to "n/a" (via or_na_helper) or an empty section.
    local task first_cat source_of_truth started_at finished_at
    local wave_count subtask_count worker_count working_branch_display
    or_na_helper() { [ -n "$1" ] && [ "$1" != "null" ] && printf '%s' "$1" || printf 'n/a'; }
    task="$(jq -r '.task // ""' "$state_json" 2>/dev/null || true)"
    first_cat="$(or_na_helper "$(jq -r '.categories[0] // ""' "$state_json" 2>/dev/null || true)")"
    source_of_truth="$(or_na_helper "$(jq -r '.answers.source_of_truth // ""' "$state_json" 2>/dev/null || true)")"
    started_at="$(or_na_helper "$(jq -r '.started_at // ""' "$state_json" 2>/dev/null || true)")"
    finished_at="$(or_na_helper "$(jq -r '.finished_at // ""' "$state_json" 2>/dev/null || true)")"
    wave_count="$(jq -r '.waves | length' "$state_json" 2>/dev/null || true)"
    subtask_count="$(jq -r '[.waves[] | length] | add // 0' "$state_json" 2>/dev/null || true)"
    worker_count="$(or_na_helper "$(jq -r '.worker_count // ""' "$state_json" 2>/dev/null || true)")"
    working_branch_display="$(or_na_helper "$working_branch")"

    # Cost line — mirror of compose_pr_body's cost_line (orchestrator/leerie.py).
    # jq emits the "- Cost: ..." line only when a telemetry block is present
    # (absent on pre-classify orphans), matching the Python guard; the leading
    # newline is trimmed by the heredoc when cost_line is empty. `group` and
    # `money` reproduce Python's `${x:,.2f}` (thousands separators + 2-decimal
    # cents) for both the dollar figure and the token counts, so this fallback
    # is format-identical to compose_pr_body — with ONE residual edge: an exact
    # half-cent (e.g. 2.675) rounds up here (`round` is half-up) but down in
    # Python (IEEE-754 repr of 2.675 is 2.67499…), a sub-cent difference that
    # never arises on a real summed cost. Under the launcher's `set -euo
    # pipefail`, `2>/dev/null || true` keeps a missing/malformed state.json from
    # aborting finalize (cost_line then degrades to empty).
    local cost_line
    cost_line="$(jq -r '
      def group:
        tostring | . as $s | ($s | explode | reverse) as $r
        | ([range(0; ($r|length))]
           | map( ([$r[.]] | implode)
                  + (if . > 0 and (. % 3 == 0) then "," else "" end) )
           | reverse | join(""));
      def money:
        (. * 100 | round) as $c
        | (($c / 100) | floor) as $dollars
        | ($c % 100) as $cents
        | "$" + ($dollars | group) + "."
          + (if $cents < 10 then "0" else "" end) + ($cents | tostring);
      (.telemetry // {})
      | select(. != {})
      | "- Cost: " + ((.cost_usd // 0) | money)
        + " (" + ((.calls // 0) | tostring) + " calls, "
        + ((.input_tokens // 0) | group) + " in / "
        + ((.output_tokens // 0) | group) + " out tokens)"
    ' "$state_json" 2>/dev/null || true)"

    pr_title="leerie: $run_id"
    pr_body="$(cat <<EOF
## Task

$task

## Classification

- Category: $first_cat
- Source of truth: $source_of_truth

## Run summary

- Run ID: $run_id
- Started: $started_at
- Finished: $finished_at
- Waves: $wave_count, subtasks: $subtask_count
- Workers: $worker_count
${cost_line:+$cost_line
}- Generated by [leerie](https://github.com/enricai/leerie) on \`$working_branch_display\`.

See \`.leerie/runs/$run_id/state.json\` for full run state.
EOF
)"
    # Deploy-ordering note (DESIGN §20 run groups). Mirror the Python
    # compose_pr_body renderer (orchestrator/leerie.py) so a run that falls
    # all the way through to this LLM-less bash fallback still surfaces
    # cross-repo prerequisites. Read external_preconditions straight from
    # state.json — it lives in STATE_FIELDS, so no run.json persistence is
    # needed. jq emits nothing when the key is absent/empty; `2>/dev/null ||
    # true` keeps a missing/unreadable state.json from aborting finalize
    # under `set -euo pipefail`.
    local deploy_note
    deploy_note="$(jq -r '
      (.external_preconditions // [])
      | select(length > 0)
      | "\n## ⚠ Deploy-ordering\n\nOne or more cross-repo prerequisites were declared by the planner. Merge and deploy the following before merging this PR:\n\n"
        + ([.[]
            | "- **" + (.tag // "(unknown)") + "**"
              + ( (([.reasons // [] | .[] | .reason // "" | select(. != "")]) | join("; "))
                  | if . != "" then " — " + . else "" end )
           ] | join("\n"))
    ' "$state_json" 2>/dev/null || true)"
    if [ -n "$deploy_note" ]; then
      pr_body="$pr_body$deploy_note"
    fi
  fi

  # The working_branch recorded at run start may no longer exist on
  # origin — e.g. a stacked run whose parent was squash-merged (and
  # branch-deleted) while this run was in flight. Detect and fall back
  # to the repo's default branch so `gh pr create` doesn't 404.
  local original_working_branch="$working_branch"
  if ! git -C "$USER_REPO" ls-remote --exit-code --heads origin "$working_branch" >/dev/null 2>&1; then
    local default_branch
    default_branch="$(git -C "$USER_REPO" remote show origin 2>/dev/null \
                       | sed -n 's/.*HEAD branch: //p')"
    if [ -n "$default_branch" ]; then
      echo "[leerie] finalize: base branch $working_branch no longer exists on origin; falling back to $default_branch" >&2
      working_branch="$default_branch"
    fi
  fi

  echo "[leerie] finalize: opening PR against $working_branch" >&2
  local pr_output pr_ok=false
  # GitHub's API may not have indexed the freshly-pushed refs yet;
  # retry with backoff to ride out the race (symptom: "Head sha
  # can't be blank" / "No commits between" immediately after push).
  # Indexing lag has been observed to exceed the original 11 s window
  # (PR-#22 incident: a manual PR 8 min post-push was the first to
  # succeed); 0/5/10/20/30 gives ~68 s of coverage.
  for _pr_delay in 0 5 10 20 30; do
    [ "$_pr_delay" -gt 0 ] && {
      echo "[leerie] finalize: gh pr create failed; retrying in ${_pr_delay}s…" >&2
      sleep "$_pr_delay"
    }
    if pr_output="$(echo "$pr_body" | gh pr create \
                      --base "$working_branch" \
                      --head "$run_branch" \
                      --title "$pr_title" \
                      --body-file - 2>&1)"; then
      pr_ok=true
      break
    fi
  done
  if [ "$pr_ok" != "true" ]; then
    # PR-creation failure is NON-fatal — push succeeded.
    _host_finalize_update_run_json "$run_json" \
      "pr_url=" "pr_error=${pr_output:-gh pr create failed}"
    echo "⚠  \`gh pr create\` failed; branch was pushed successfully." >&2
    echo "  Pushed branch: $run_branch (on origin)" >&2
    if [ "$working_branch" != "$original_working_branch" ]; then
      echo "  (base branch $original_working_branch was already deleted from origin;" >&2
      echo "   tried fallback to $working_branch, which also failed)" >&2
    fi
    echo "  Open the PR manually:" >&2
    echo "    gh pr create --base $working_branch --head $run_branch" >&2
    echo "  Or via the GitHub web UI for the repo." >&2
    echo "  gh stderr was:" >&2
    printf '    %s\n' "$pr_output" >&2
    return 0
  fi
  local pr_url
  pr_url="$(printf '%s' "$pr_output" | tail -n 1)"
  _host_finalize_update_run_json "$run_json" "pr_url=$pr_url" "pr_error="
  echo "[leerie] finalize: opened PR $pr_url" >&2
  return 0
}
