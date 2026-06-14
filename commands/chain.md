---
description: Submit and manage a multi-run leerie chain. Use when the user wants to run a sequence of leerie runs across N sequential waves driven by the laptop.
argument-hint: <submit|status|list|kill|stop|resume|finalize|attach> [<args>]
---

# Manage Leerie Chains

The user wants to perform a chain operation:

```
$ARGUMENTS
```

A *chain* is **N parallel `./leerie --runtime fly` invocations per wave**,
with synth-merge between waves to build the next wave's base branch.
The laptop is the sequencer; there is no Fly coordinator machine. Each
per-job worker takes the existing single-run path unchanged
(provision → seed-auth + seed-repo → orchestrator → `decide_teardown`
trap on laptop → `fetch_branch` → `host_finalize` → `destroy_machine`).

Wave N+1 starts only after every wave-N job has finalized on the laptop
(branch pushed to origin, PR opened). Between waves the laptop runs
`chain.git_ops.synth_merge_branches` to build the staging branch
`leerie/stage/<chain-id>-wave-<N+1>`. (DESIGN.md §19.)

GitHub credentials are touched only by the laptop, via the existing
`host_finalize` mechanism. Workers never see them.

The single-run verbs (`status`, `kill`, `stop`, `resume`, `finalize`,
`attach`) are **ID-dispatched**: a UUID positional argument operates on
the chain (iterates `$LEERIE_STATE_HOST_DIR/runs/*/run.json` filtered by
the `chain_id` field, dispatches the single-run verb per discovered
run); a Fly machine id operates on a single run (historical behavior).
UUID format: `8-4-4-4-12` hyphenated. The deprecated `--chain-*` aliases
continue to work via the launcher's shim arms.

## Steps

Parse the first word of `$ARGUMENTS` to decide the subcommand:

### `submit` — start a new chain

Required: at least one `--wave` flag. Each `--wave` value is a
comma-separated list of prompt-file paths; the launcher reads each file
and passes its contents as the run prompt to a background
`./leerie --runtime fly` invocation. Wave index is assigned by
`--wave` flag order (0, 1, 2, …).

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --chain \
  --wave <path/to/a1.md,path/to/a2.md> \
  --wave <path/to/b1.md>
```

The launcher mints a fresh `chain_id` (UUID), prints a submission
banner with the chain id and per-wave job counts, then enters the wave
loop. The wave loop runs in the foreground of the user's terminal; if
the user wants to detach, they can Ctrl-C (the trap propagates SIGTERM
to all in-flight wave children, each of which runs its own
`decide_teardown` to clean up its Fly machine).

#### Resuming a chain (after wave failure or Ctrl-C)

When a wave fails or the user Ctrl-Cs mid-chain, the chain pauses.
To resume:

1. `leerie --resume <chain-id>` — resumes every paused single-run
   in the chain. After each paused run completes, the wave it
   belongs to has all runs `pushed_at`.
2. Re-submit the chain with `--chain-id` pinned to the prior UUID.
   The wave loop's idempotency check detects waves whose runs are
   all already pushed and skips fan-out, advancing directly to the
   first incomplete wave:

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --chain \
  --chain-id <prior-uuid> \
  --wave <same --wave args as the original submission>
```

The `--chain-id` value is the UUID printed by the original submit
banner. If a synth-merge between waves conflicted, the user resolves
the conflict in `$USER_REPO` + pushes the staging branch, then re-
submits with `--chain-id`. The wave loop detects the now-existing
staging branch on origin (via `git ls-remote`) and skips synth-merge
for that wave transition, resuming at the next wave.

### `status` — print a chain snapshot

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --status <chain-id>
```

Iterates `$LEERIE_STATE_HOST_DIR/runs/*/run.json`, filters by the
`chain_id` field, and prints one row per matched run (wave, run_id,
status, branch, notes). Status derived from run.json fields
(`pushed_at` / `paused_at` / `killed_at` / `finished_at`).

### `list` — list chains

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --list --chains
```

Or via the deprecated alias `--list-chains`. Iterates run.json files,
groups by `chain_id`, and prints one row per chain
(chain_id, status, pushed/total, wave count, started_at).

### `stop` — pause a chain

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --stop <chain-id>
```

Enumerates running runs in the chain (have `fly_machine_id`, no
terminal state) and invokes `leerie --stop <run-id>` per run. Each
paused run's machine is stopped (preserving filesystem) and run.json
records `paused_at` + `pause_reason`. Resume with `--resume`.

### `kill` — destroy a chain

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --kill <chain-id>
```

Enumerates non-killed runs in the chain and invokes
`leerie --kill <run-id>` per run. Each run's Fly machine is destroyed
and `killed_at` is recorded. Idempotent — already-killed runs are
skipped.

### `resume` — resume paused chain runs

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --resume <chain-id>
```

Enumerates paused runs (`paused_at` set, not `killed_at`) and invokes
`leerie --resume <run-id>` per run. After paused runs complete, the
user re-invokes `leerie --chain --wave ...` and the wave loop's
idempotency check skips waves whose runs are all already `pushed_at`,
continuing from where the chain stopped.

### `finalize` — push + open PRs for unpushed chain runs

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --finalize <chain-id>
```

Enumerates runs with `pushed_at` unset (and not `killed_at`), invokes
`leerie --finalize <run-id>` per run. Useful when the wave loop was
interrupted between orchestrator finalize and laptop push.

### `attach` — poll until terminal

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --attach <chain-id>
```

Polls `$LEERIE_STATE_HOST_DIR/runs/*/run.json` every 5s. Exits 0 when
every chain run reaches a terminal state (`pushed_at` / `paused_at` /
`killed_at` / `sync_failed_at`). Useful for waiting on a chain
submitted in a different terminal.

## Relaying results

For every verb, surface the launcher's stdout to the user verbatim —
it's already formatted for human reading. On a non-zero exit, surface
the error body the same way; the launcher already identifies which
verb failed and why.
