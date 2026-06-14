---
description: Submit and manage a multi-run leerie chain. Use when the user wants to run a sequence of leerie runs across N sequential waves coordinated by a per-chain ephemeral Fly coordinator.
argument-hint: <submit|status|list|kill|stop|attach> [<args>]
---

# Manage Leerie Chains

The user wants to perform a chain operation:

```
$ARGUMENTS
```

A *chain* is a sequence of leerie runs grouped into N sequential
**waves** (wave 0, 1, …). Runs within a wave execute in parallel as
separate Fly machines; wave N+1 launches once every run in wave N
reaches a terminal status. Each chain is owned by a **per-chain
ephemeral coordinator** Fly machine that holds chain state in SQLite,
decides wave advancement, pushes branches + opens PRs on behalf of
worker runs, and self-destructs at chain end. (DESIGN.md §19.)

The chain verbs are **launcher fast-paths** — they call the Fly
Machines API directly (and, for `status`/`attach`, the coordinator's
6PN-only HTTP endpoint). They do not spawn a local container and do
not consult Claude's OAuth token.

**Runtime prerequisites** (set once in the user's shell profile):

```
export FLY_API_TOKEN=...                                    # Fly Machines API
export GH_DISPATCH_PAT=...                                  # coordinator push + PR
export LEERIE_CHAIN_IMAGE=registry.fly.io/leerie-coordinator:<tag>
export LEERIE_WORKER_IMAGE=registry.fly.io/leerie:<tag>
# Optional:
export LEERIE_FLY_APP=leerie     # default 'leerie'
export LEERIE_REGION=iad         # default 'iad'
```

The single-run verbs (`status`, `kill`, `stop`, `attach`) are
**ID-dispatched** at the launcher: a UUID positional argument
operates on the chain; a Fly machine id operates on a single run
(historical behavior). UUID format: `8-4-4-4-12` hyphenated. The
deprecated `--chain-*` aliases continue to work via the launcher's
shim arms.

## Steps

Parse the first word of `$ARGUMENTS` to decide the subcommand:

### `submit` — start a new chain

Required: at least one `--wave` flag. Optional: a target repo URL
(defaults to `$USER_REPO` or `$PWD`). Each `--wave` value is a
comma-separated list of prompt-file paths; the launcher reads each
file and sends its contents as the run prompt. Wave index is assigned
by `--wave` flag order (0, 1, 2, …).

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --chain \
  --wave <path/to/a1.md,path/to/a2.md> \
  --wave <path/to/b1.md> \
  --target <https-repo-url>
```

The launcher mints a fresh `chain_id` (UUID), base64-encodes the
queue spec into the coordinator's env, and POSTs to the Fly Machines
API to create the coordinator. It prints the `chain_id` and the
coordinator's machine id. Copy the `chain_id` — it's the argument to
every follow-up verb.

### `status` — print a chain snapshot

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --status <chain-id>
```

Resolves the coordinator via Fly metadata
(`metadata.leerie_chain_id=<id> & metadata.leerie_role=coordinator`)
and prints the JSON response from the coordinator's `/state`
endpoint: top-level status, wave_state, paused reason (if any), and
per-run rows with their status, branch, exit_code, and timestamps.

If the chain has already completed and the coordinator self-destructed,
`status` reports "no live coordinator"; the chain's audit artifact is
at `_leerie-chains/<chain-id>/chain.json` in the target repo.

### `list` — list active chains

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --list --chains
```

Or via the deprecated alias `--list-chains`. Queries the Fly Machines
API for live coordinator machines and prints one row per chain
(chain_id, coordinator machine id, state, created_at).

### `stop` — pause a chain

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --stop <chain-id>
```

POSTs `/pause` to the coordinator. The chain holds at its current
wave_state — wave advancement is suspended, and the watchdog's
heartbeat-staleness check is suspended too so a `--resume` operation
can take minutes without auto-failing. The chain resumes via POST
`/unpause` (a chain-scoped `--resume` is in a follow-up).

### `kill` — destroy a chain

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --kill <chain-id>
```

Lists every machine tagged with `metadata.leerie_chain_id=<id>`
(coordinator + every worker) and DELETEs each one via the Fly Machines
API with `?force=true`. Idempotent — re-running on an already-destroyed
chain is a no-op. The coordinator's persistent volume is destroyed
with it; this is NOT recoverable.

### `attach` — poll until terminal

```
bash "${CLAUDE_PLUGIN_ROOT}/leerie" --attach <chain-id>
```

Same coordinator discovery as `status`, then polls `/state` every 5s
until the chain reaches a terminal status (`done`, `failed`, or
`cancelled`) or the coordinator becomes unreachable (likely
self-destructed after chain completion). Each poll prints the full
snapshot.

## Relaying results

For every verb, surface the launcher's stdout to the user verbatim —
it's the API response JSON, and the user usually wants to read the
`chain_id` out of `submit` or the wave/status fields out of `status`.
On a non-zero exit, surface the error body the same way; the launcher
already identifies which verb failed and why.
