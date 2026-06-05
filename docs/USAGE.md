# A worked example: one Leerie run, end to end

## What this document is

A walkthrough. It follows a single Leerie run from invocation to merge so
you know what to expect on stdout and on disk at each phase. It is *not* a
reference — for the architecture and the reasons it works that way, see
[`docs/DESIGN.md`](DESIGN.md); for the code surface (function names, cap
values, schemas), see [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md). This
document never restates either.

## Prerequisites recap

You need the `claude` CLI on `PATH` (logged in), `git`, and a git repo
with `user.email` and `user.name` set and a clean working tree. Leerie
runs inside a container, so you also need a container runtime: Colima
on macOS (`brew install colima && colima start --runtime containerd
--mount-type virtiofs --cpu N --memory M` where N/M are half your host
CPU/RAM — see [`docs/INSTALL.md`](INSTALL.md) for the bounds the
installer uses automatically; also add the swap-provision YAML block
documented under "Memory pressure: swap configuration"), or
`containerd + nerdctl` natively on Linux.
You do *not* need Python on the host — the image provisions it. For
the full per-OS setup walkthrough see
[`docs/INSTALL.md`](INSTALL.md); for the one-command leerie install
(Claude Code marketplace or `curl | bash`) see
[README "Install"](../README.md#install), and
[README "Requirements"](../README.md#requirements) for the full list.

## The example task

We will walk through one run on this concrete task:

> *"Add a `--dry-run` flag to the existing CLI tool that prints the plan
> without executing it, plus a regression test."*

It is a good demonstrator because it touches code (the CLI), touches tests
(a regression), has an obvious dependency (test imports the flag), and fits
in one or two waves. It exercises the orchestrator's classify → plan →
schedule → execute → finalize loop without being so large that the output
becomes noise.

## Step 1 — Invocation

From the root of the target repository:

```bash
export LEERIE_SOURCE_OF_TRUTH=codebase
leerie "Add a --dry-run flag to the CLI that prints the plan without executing it, plus a regression test"

# Equivalent one-off invocation without the env var:
leerie --source-of-truth codebase "Add a --dry-run flag …"

# Same idea for the model — judgment workers default to `opus` and the
# acting workers (implementer, conformer) default to `sonnet`; `--model
# <alias>` sets every worker. Per-worker overrides exist
# (e.g. --model-implementer opus).
leerie --model opus "Add a --dry-run flag …"
```

Setting `LEERIE_SOURCE_OF_TRUTH=codebase` up front pins the
source-of-truth preference for this run — useful when the default
(`both`) is not what you want.

Within the first few seconds you will see preflight output on stdout — git
identity check, working-tree-clean check, a live `claude -p` smoke test —
and leerie creates its run state directory outside the repo (default:
`$HOME/.leerie/state/<sha16>-<basename>/`). No `.leerie/` directory appears
inside your repo, so there is nothing to add to `.gitignore`.

## Step 2 — Classification and clarification

The classifier returns a category set; for our example you should expect
something like `["feat", "test"]` — a feature and a regression test. Along
with the categories the classifier surfaces *intent* questions — things it
genuinely cannot derive from the task or the codebase.

A realistic question for our task: *"Should `--dry-run` exit zero after
printing, or should it also validate the plan and exit non-zero if the
plan would have failed?"* That decision is not in the codebase; the
classifier asks.

In an interactive terminal Leerie prompts you; you type answers, the run
continues. In a non-interactive context (CI, a plugin skill) Leerie
instead writes `.leerie/pending-questions.json` and exits with code 10 —
not an error, a structured "need answers" signal. The plugin skill at
[`commands/leerie.md`](../commands/leerie.md) shows the questions to
the user, writes their answers to `.leerie/answers.json`, and resumes
with `--resume --answers .leerie/answers.json`.

## Step 3 — Planning and scheduling

One planner subprocess runs per category, in parallel. Each returns a list
of subtasks with id, domain prefix (`feat-`, `test-`, etc.), description,
and dependencies on other subtasks by id.

For our task expect roughly:

```
feat-add-dry-run-flag       (depends on: none)
test-dry-run-regression     (depends on: feat-add-dry-run-flag)
```

The scheduler merges plans across categories, builds a global dependency
DAG, topologically sorts it into waves, and persists the result. Our two
subtasks become two waves of one subtask each — the test cannot run until
the flag exists. The full rationale for the wave model is in
[`DESIGN.md`](DESIGN.md) §5.

The merged plan lives at `.leerie/plan.json`; per-subtask spec files
appear at `.leerie/subtasks/<id>.json`.

## Step 4 — Wave execution

For each wave Leerie creates a per-subtask git worktree off the run
branch (`leerie/runs/<run-id>`), then spawns an implementer worker in
each worktree. Workers run concurrently, capped by `--max-parallel`
(default 2).

On stdout you'll see lines like (with a hypothetical `<run-id>` of
`feat-add-dry-run-flag-a3f7c2`):

```
[wave 1] implementer feat-add-dry-run-flag: start
[wave 1] implementer feat-add-dry-run-flag: ok (3 turns, 12.4s)
[wave 1] integrating feat-add-dry-run-flag into leerie/runs/feat-add-dry-run-flag-a3f7c2
[wave 1] validating leerie/runs/feat-add-dry-run-flag-a3f7c2
```

And `git worktree list` will show entries like:

```
/your/repo                                                                       abc1234 [main]
/your/repo/.leerie/runs/feat-add-dry-run-flag-a3f7c2/worktrees/staging         def5678 [leerie/runs/feat-add-dry-run-flag-a3f7c2]
/your/repo/.leerie/runs/feat-add-dry-run-flag-a3f7c2/worktrees/feat-add-dry-run-flag  ghi9012 [leerie/subtasks/feat-add-dry-run-flag-a3f7c2/feat-add-dry-run-flag]
```

After every implementer commits in its worktree, the integrator merges
its branch into the run branch, and the post-work conformance phase
runs your project's detected build/lint/test commands as advisory
checks against the worktree — surfacing residuals as warnings on the
subtask result, not gating the wave. The wave boundary itself only
runs a deterministic conflict-marker scan; whether the work landed is
the implementer's confidence-gate call (DESIGN §8). Acting workers use
`--dangerously-skip-permissions` by design — bounded by worktree
isolation. See [README "Safety"](../README.md#safety) and
[`DESIGN.md`](DESIGN.md) §6, §9.

## Step 5 — Reviewing the run branch

Before phase 6 opens a PR proposing to merge into your working branch,
**review the run branch yourself**. This is what the
run-branch-as-integration-buffer (DESIGN §6) buys you:

```bash
git log leerie/runs/<run-id> --oneline
git diff main..leerie/runs/<run-id>
```

You will see one commit per subtask (one per worker), with subtask id in
the subject line. If the diff looks wrong — too broad, missed an edge
case, conflicting with something you wanted preserved — this is where you
intervene. Either re-run Leerie with a refined task, hand-edit the run
branch, or abandon and `./scripts/cleanup.sh --run-id <run-id> --branches`.

## Step 6 — Finalization

Phase 6 verifies `leerie/runs/<run-id>` is non-empty, pushes it to
`origin`, and opens a PR via `gh pr create --base <working-branch>
--head leerie/runs/<run-id>`. Your working branch (the branch you
were on when you invoked Leerie, recorded in
`.leerie/runs/<run-id>/working-branch`) is **not** modified locally —
review and merge the PR on GitHub when you're satisfied. The run branch
`leerie/runs/<run-id>` remains in your repo as the PR head until you
merge the PR. The per-subtask branches `leerie/subtasks/<run-id>/*`
are **deleted automatically** at finalize — they were the mechanism for
parallel implementer isolation and carry no information that isn't
already in the run branch's merge graph. Each worker's full commit
history is still reachable from the run branch (the integrator merges
each subtask with `--no-ff`, so every worker's commits appear as a
named merge bubble in `git log leerie/runs/<run-id> --graph`).

When you no longer need the run branch either (e.g., after the PR is
merged on GitHub):

```bash
./scripts/cleanup.sh --run-id <run-id> --branches
```

deletes the run branch and any remaining subtask branches. The per-run
state directory (`<state-root>/runs/<run-id>/`, where `<state-root>` is
the resolved state directory — default `$HOME/.leerie/state/<key>/`) is
kept as a smaller audit trail; `rm -rf` it manually when you no longer
need that either. For an audit cleanup across every past run, use
`--all-runs --branches`.

## What happens when something goes wrong

**A subtask reports `blocked`.** The implementer hit something it cannot
resolve (an external dependency, an ambiguous spec, a failing test it
cannot fix). The wave aborts *before* integration, the blocker reason
lands in `state['blocked'][<subtask-id>]` and `subtask_status[<id>] =
"blocked"` inside `.leerie/runs/<run-id>/state.json`, and Leerie
exits non-zero. You read the blocker, fix the upstream issue (often by
editing the task and re-running, sometimes by hand-resolving), then
`./leerie --resume`. See [`DESIGN.md`](DESIGN.md) §8 for the
evidence-gated loop logic that produces this signal.

**Integration fails.** The integrator can't merge a subtask branch into
the run branch — usually a conflict it cannot resolve behaviorally.
Leerie prints the diagnosis to stderr, aborts the in-progress merge so
the run branch stays clean, and exits non-zero. Pull up the conflicting
branches yourself, resolve, and resume.

**The run is interrupted.** Ctrl-C, system reboot, budget-cap hit. Run
`./leerie --resume` from the same directory. The resume cursor is
`state['completed_waves']`; finished waves are not re-run. The full state
schema is documented in [`IMPLEMENTATION.md`](IMPLEMENTATION.md) §8.

## Walking away from a remote run (`--runtime fly`)

Remote runs are designed to outlive your local terminal. The
orchestrator runs detached inside the Fly Machine; your local terminal
is only watching the log stream. Five verbs cover the full lifecycle:

| You did | Leerie did | Verb to come back |
|---|---|---|
| `leerie "task" --runtime fly` | provisioned a Fly Machine, started the orchestrator detached, opened a tail of its log on your terminal | — (you're attached) |
| pressed Ctrl-C | detached your local tail; orchestrator on the machine is still running | `leerie --attach <run-id> --tail` |
| closed your laptop / lost WiFi | same as Ctrl-C — the tail broke but the orchestrator did not | `leerie --attach <run-id> --tail` |
| ran `leerie --stop <run-id>` | stopped the machine cleanly via `flyctl machine stop`; filesystem preserved on Fly volume | `leerie --resume --run-id <id> --runtime fly` |
| ran `leerie --kill <run-id>` | destroyed the machine via `flyctl machine destroy`; run is over | start a new run; this one is gone |

**The "close your laptop" workflow.** Start the run, watch it for a
few minutes to make sure it's healthy, then Ctrl-C the tail. You'll
see a one-line banner:

```
[leerie] detached from run <id> (machine <mid> still running)
       reattach:  leerie --attach <id> --tail
       pause:     leerie --stop <id>
       destroy:   leerie --kill <id>
```

Close your laptop, go wherever. When you come back, `leerie --attach
<id> --tail` picks up the orchestrator log where it left off. The
orchestrator never noticed you were gone.

**Listing runs.** `leerie --list` shows every run (local and remote) in
one table, with the Fly Machine ID column populated for remote runs.
Filter by status with `leerie --list --status <state>` (e.g. `paused`,
`killed`, `in-progress`) and by runtime with `--runtime <local|fly>`.
The two axes are orthogonal: `--list --status paused --runtime fly`
shows every paused Fly run. The status taxonomy lives in
`RUN_STATUSES` in `orchestrator/leerie.py`; `leerie --list --status ?`
prints the full set. `--list --runtime fly` (without `--status`)
short-circuits to a direct Fly query (`flyctl machines list`) covering
every machine under the app, regardless of which host repo launched
them — useful when you've lost track of a machine ID after Ctrl-C.

> **In-flight detached runs** — runs that are still in the bootstrap
> phase (before classify completes, ~1 min) won't show up in `leerie
> --list` yet, because `state.json` lives on the Fly Machine until
> `leerie --finalize` streams it back. **The detach banner that prints
> when you Ctrl-C is the canonical source of the run-id during that
> window** — copy it. Once classify completes the run appears in
> `leerie --list` with its final category-prefixed id.

**`flyctl` auto-install.** The first time you pass `--runtime fly` on
a machine without `flyctl`, leerie offers to install it (`brew install
flyctl` on macOS, `curl -L https://fly.io/install.sh | sh` on Linux)
and prompts for `flyctl auth login`. The pattern mirrors the
local-runtime auto-install in `scripts/install.sh`. Opt out with
`--no-runtime-install` (you'll get the install hint and exit 1, same
as today).

**Fly app auto-create + remote image build.** On first `--runtime
fly` invocation per Fly account, leerie also auto-creates the Fly app
(default name: `leerie`; override with `LEERIE_FLY_APP=<name>`) and
builds the leerie image on Fly's remote builder. The remote build runs
inside Fly's infrastructure — no host Docker daemon required. Takes
~3-5 min the first time per leerie version; subsequent runs reuse the
cached registry tag and skip the build.

**`--local-build` opt-in.** If you have a working Docker daemon
authenticated to `registry.fly.io` (Docker Desktop + `flyctl auth
docker`, or apt-installed Docker on Linux + `flyctl auth docker`),
you can pass `--local-build` (or `LEERIE_LOCAL_BUILD=1`) to build the
image on your host instead. **Most users should leave this off** —
it doesn't work with nerdctl-in-Colima on macOS (the most common
local setup) because nerdctl can't reach Keychain. See
`docs/INSTALL.md` for the full caveat.

## Tuning for your workflow

- `--source-of-truth codebase|research|both` — one-off CLI override;
  beats env and `leerie.toml`. Unset → default `both`.
- `LEERIE_SOURCE_OF_TRUTH=codebase|research|both` — sticky preference.
- `leerie.toml` at the repo root with `source_of_truth = codebase` —
  committed per-repo default; outranked by env and CLI.
- `--model sonnet|opus|haiku` — model for every worker this run.
  Without any override the per-worker defaults apply: judgment workers
  (classifier, planner, reconciler, provision, integrator) run on `opus`; the
  acting workers (implementer, conformer) run on `sonnet`. Per-worker
  `--model-classifier`, `--model-planner`, `--model-reconciler`,
  `--model-provision`, `--model-implementer`, `--model-integrator`,
  `--model-conformer` flags override the global default. Env-var equivalents are
  `LEERIE_MODEL` (and `LEERIE_MODEL_<WORKER>` for the per-worker
  overrides); TOML keys are `model` / `model_<worker>` in
  `leerie.toml`. Full precedence table in
  [`IMPLEMENTATION.md`](IMPLEMENTATION.md#model-selection). To restore
  the pre-0.3 all-sonnet behavior in one knob, set `--model sonnet` or
  `LEERIE_MODEL=sonnet`.
- `--max-workers N` — cap total `claude -p` subprocess count over the
  run. Default: `100` (`DEFAULT_CAPS["max_total_workers"]`). Also
  `LEERIE_MAX_WORKERS` env var or `max_workers` in `leerie.toml`
  (same precedence as `--confidence-rounds`: CLI > env > TOML > default).
  Note that the post-work conformance phase (DESIGN §9) spawns up to
  `conformance_rounds` additional workers per *successful* subtask (default
  2), roughly doubling per-subtask worker usage. For large runs you may
  want to raise this proportionally — a cap-hit during the conformance
  phase surfaces as an advisory `conformance_warnings` entry, never as
  a subtask failure, but earlier subtasks would have hit it first and
  aborted the run.
- `--max-parallel N` — cap concurrent implementers per wave. Default:
  `2` (`DEFAULT_CAPS["max_parallel"]`). Lowered from 4 in May 2026
  because subprocess fan-out inside each worker (vitest pools, webpack
  workers, etc.) is unbounded; the only orchestrator-side knob that
  keeps total in-flight toolchain memory in check is the worker count.
  Raise this on machines with more RAM (16 GiB+ recommended for `N=4`).
- `--clarify` — opt into surfacing intent questions to the user
  (default: off). Without it the classifier's filter still runs but
  surviving questions are dropped, and the implementer makes a
  documented best-effort decision. Also `LEERIE_CLARIFY` env var
  and `clarify = true` in `leerie.toml`.
- `--runtime local|fly` — execution backend for per-subtask worker
  containers. Default: `local` (nerdctl on the local container
  runtime). `fly` routes each worker through Fly.io Machines instead
  — requires only `flyctl` logged in (`flyctl auth login`). The
  launcher auto-creates the Fly app (`LEERIE_FLY_APP`, default `leerie`)
  and builds the leerie image on Fly's remote builder if the registry
  tag is missing; opt out with `--no-auto-publish`. Opt into local
  build with `--local-build` / `LEERIE_LOCAL_BUILD=1` (requires
  working Docker daemon authenticated to `registry.fly.io`; see
  INSTALL.md). Also `LEERIE_RUNTIME` env var or `runtime = fly` in
  `leerie.toml` (committed per-repo default; outranked by env and
  CLI). Precedence: `--runtime` → `LEERIE_RUNTIME` → `leerie.toml` →
  default `local`.
- `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70` — lower auto-compaction threshold
  for worker processes.

The full inventory of CLI flags and environment variables is in the
[README "Configuration" section](../README.md#configuration).

## Submitting and tracking a chain

A *chain* is a sequence of Leerie runs coordinated by a separate
`leerie-chain` service (DESIGN §19). Each run in the chain executes
one task on the same target repository; `leerie-chain` launches them
in order, waits for each to complete cleanly, and moves on to the
next. This is the right shape for a series of tasks with a strict
ordering — for example: refactor the data model, then write the
migration, then update the API layer.

The chain verbs (`--chain-submit`, `--chain-status`, `--list-chains`,
`--chain-kill`, `--chain-attach`) are launcher fast-paths — they talk
to the `leerie-chain` HTTP API and never start a container. They read
`LEERIE_CHAIN_URL` to find the API endpoint (default:
`http://localhost:8080`).

### Step 1 — Write your prompt files

Prepare one prompt file per task. Plain text or Markdown, exactly as
you would pass to `leerie "..."`:

```
prompts/
  01-refactor-data-model.md
  02-write-migration.md
  03-update-api-layer.md
```

### Step 2 — Submit the chain

```bash
export LEERIE_CHAIN_URL=https://my-chain-app.fly.dev  # point at your deployed app

leerie --chain-submit \
  --runs prompts/01-refactor-data-model.md,prompts/02-write-migration.md,prompts/03-update-api-layer.md \
  --target ~/src/myrepo
```

`--runs` is a comma-separated list of prompt-file paths (resolved on
the host). `--target` is the local path of the repository to run
against; it defaults to `$PWD` when omitted. `leerie-chain` receives
a `POST /chains` request, inserts a chain record, and immediately
launches the first run. The command prints the new `chain-id`:

```
{"chain_id": "chain-abc123", "status": "running", "current_run": 0}
```

Copy the `chain_id` — you'll use it for the other verbs.

### Step 3 — Monitor progress

```bash
# One-shot status check (JSON response):
leerie --chain-status chain-abc123

# Stream the leerie-chain log (follows until interrupted):
leerie --chain-attach chain-abc123
```

`--chain-status` calls `GET /chains/<chain-id>` and prints the JSON
response, which includes the chain status (`running`, `paused`,
`completed`, `failed`), the index and run-id of the current run, and
the completion state of earlier runs.

`--chain-attach` streams `GET /chains/<chain-id>/log`. Press Ctrl-C to
detach from the stream; the chain keeps running. Re-attach whenever
you like.

### Step 4 — Review each run branch

While the chain is running, each completed run produces a run branch
(`leerie/runs/<run-id>`) and opens a PR just like a single run. Review
and merge those PRs as they appear — the chain continues regardless,
and you pull in each change when you're satisfied.

### Step 5 — List all chains

```bash
leerie --list-chains
```

Calls `GET /chains` and prints all chains the `leerie-chain` app knows
about. Filter in your terminal (e.g. `| jq '.[] | select(.status ==
"running")'`) to find a chain you submitted earlier.

### Step 6 — Cancel a chain

If you want to stop a chain in progress (for example, you spotted a
mistake in run two before it starts):

```bash
leerie --chain-kill chain-abc123
```

Sends `DELETE /chains/<chain-id>`. Any run that is already in flight
continues to its natural conclusion (running its own `--resume`
recovery path if interrupted); the chain moves to `cancelled` status
and no new runs are started.

### Pointing at a different leerie-chain app

`LEERIE_CHAIN_URL` selects the API endpoint for all five verbs:

```bash
# Deployed Fly app (production):
export LEERIE_CHAIN_URL=https://my-chain-app.fly.dev

# Local development instance:
export LEERIE_CHAIN_URL=http://localhost:8080

leerie --chain-submit --runs prompts/a.md,prompts/b.md --target ~/src/myrepo
```

For the `leerie-chain` setup steps (deploying the Fly app, setting
`GH_DISPATCH_PAT`, `FLY_API_TOKEN`, and `FLY_WEBHOOK_SIGNING_SECRET`),
see [`docs/IMPLEMENTATION.md`](IMPLEMENTATION.md) §7 "Chain launcher
verbs" and the `chain/` subdirectory's own `README`.
