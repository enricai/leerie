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
`$HOME/.leerie/<basename>/`). No `.leerie/` directory appears inside
your repo, so there is nothing to add to `.gitignore`.

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
instead writes `<state-root>/pending-questions.json` and exits with
code 10 — not an error, a structured "need answers" signal. The plugin
skill at [`commands/leerie.md`](../commands/leerie.md) shows the
questions to the user, writes their answers to
`<state-root>/answers.json`, and resumes with
`--resume --answers <state-root>/answers.json`.

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

The merged plan lives at `<state-root>/plan.json`; per-subtask spec
files appear at `<state-root>/subtasks/<id>.json`. `<state-root>`
defaults to `$HOME/.leerie/<basename>/`; override with
`LEERIE_STATE_DIR`, `--state-dir`, or `state_dir =` in `leerie.toml`.

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

And `git worktree list` will show entries like (with `<state-root>`
expanded to the resolved per-repo state directory — by default
`$HOME/.leerie/<basename>/`):

```
/your/repo                                                                            abc1234 [main]
<state-root>/runs/feat-add-dry-run-flag-a3f7c2/worktrees/staging                      def5678 [leerie/runs/feat-add-dry-run-flag-a3f7c2]
<state-root>/runs/feat-add-dry-run-flag-a3f7c2/worktrees/feat-add-dry-run-flag        ghi9012 [leerie/subtasks/feat-add-dry-run-flag-a3f7c2/feat-add-dry-run-flag]
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
`<state-root>/runs/<run-id>/working-branch`) is **not** modified locally —
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
the resolved state directory — default `$HOME/.leerie/<basename>/`) is
kept as a smaller audit trail; `rm -rf` it manually when you no longer
need that either. For an audit cleanup across every past run, use
`--all-runs --branches`.

## What happens when something goes wrong

**A subtask reports `blocked`.** The implementer hit something it cannot
resolve (an external dependency, an ambiguous spec, a failing test it
cannot fix). The wave aborts *before* integration, the blocker reason
lands in `state['blocked'][<subtask-id>]` and `subtask_status[<id>] =
"blocked"` inside `<state-root>/runs/<run-id>/state.json`, and Leerie
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
is only watching the log stream. Four verbs cover the full lifecycle:

| You did | Leerie did | Verb to come back |
|---|---|---|
| `leerie "task" --runtime fly` | provisioned a Fly Machine, started the orchestrator detached, opened a tail of its log on your terminal | — (you're attached) |
| pressed Ctrl-C | detached your local tail; orchestrator on the machine is still running | `leerie --resume <run-id>` |
| closed your laptop / lost WiFi | same as Ctrl-C — the tail broke but the orchestrator did not | `leerie --resume <run-id>` |
| ran `leerie --stop <run-id>` | stopped the machine cleanly via `flyctl machine stop`; filesystem preserved on Fly volume | `leerie --resume <run-id> --runtime fly` |
| ran `leerie --kill <run-id>` | destroyed the machine via `flyctl machine destroy`; run is over | start a new run; this one is gone |

`leerie --resume` is a single smart-router verb: it wakes a paused
machine, attaches to a still-alive orchestrator, or relaunches against
an alive-but-orphaned machine — automatically, based on what it
observes. The default action is to tail the orchestrator log; pass
`--shell` to drop into a bash shell at `/work` instead.

**The "close your laptop" workflow.** Start the run, watch it for a
few minutes to make sure it's healthy, then Ctrl-C the tail. You'll
see a one-line banner:

```
[leerie] detached from run <id> (machine <mid> still running)
       reattach:  leerie --resume <id>
       pause:     leerie --stop <id>
       destroy:   leerie --kill <id>
```

Close your laptop, go wherever. When you come back, `leerie --resume
<id>` picks up the orchestrator log where it left off. The
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
(set via `LEERIE_FLY_APP=<name>` or `--fly-app <name>`) and
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
  (classifier, planner, reconciler, plan_overlap_judge, provision, integrator)
  run on `opus`; the acting workers (implementer, conformer) run on `sonnet`.
  Per-worker `--model-classifier`, `--model-planner`, `--model-reconciler`,
  `--model-plan_overlap_judge`, `--model-provision`, `--model-implementer`,
  `--model-integrator`,
  `--model-conformer` flags override the global default. Env-var equivalents are
  `LEERIE_MODEL` (and `LEERIE_MODEL_<WORKER>` for the per-worker
  overrides); TOML keys are `model` / `model_<worker>` in
  `leerie.toml`. Full precedence table in
  [`IMPLEMENTATION.md`](IMPLEMENTATION.md#model-selection). To restore
  the pre-0.3 all-sonnet behavior in one knob, set `--model sonnet` or
  `LEERIE_MODEL=sonnet`.
- `--max-workers N` — cap total `claude -p` subprocess count over the
  run. Default: `200` (`DEFAULT_CAPS["max_total_workers"]`). Also
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
  `5` (`DEFAULT_CAPS["max_parallel"]`). Also `LEERIE_MAX_PARALLEL`
  env var or `max_parallel` in `leerie.toml` (same precedence as
  `--max-workers`). Per-worker cgroup containment keeps an OOM inside
  one worker's cgroup, so high wave-level parallelism is safe. Users
  on smaller VMs can opt down.
- `--clarify` — opt into surfacing intent questions to the user
  (default: off). Without it the classifier's filter still runs but
  surviving questions are dropped, and the implementer makes a
  documented best-effort decision. Also `LEERIE_CLARIFY` env var
  and `clarify = true` in `leerie.toml`.
- `--runtime local|fly` — execution backend for per-subtask worker
  containers. Default: `local` (nerdctl on the local container
  runtime). `fly` routes each worker through Fly.io Machines instead
  — requires only `flyctl` logged in (`flyctl auth login`). The
  launcher auto-creates the Fly app (`LEERIE_FLY_APP`, required)
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

### Browser-based system tests just work

Headless Chromium and its matching chromedriver ship pre-installed in the
leerie image, so Capybara, Selenium, and Playwright system tests run with no
setup — no browser download step, no driver-version pinning. **No
project-level ChromeOptions or `--no-sandbox` configuration is required**:
the container flags Chrome needs to run rootless are baked into the image
and applied automatically to every invocation. Projects that already set
`ChromeOptions` (e.g. `--no-sandbox`) continue to work unchanged — the flags
are idempotent.

### Per-repo configuration: `.leerie/config.toml`

The knobs above (in `leerie.toml` or env vars) are *operational* — they
control how leerie runs. A separate file, `.leerie/config.toml` committed
inside your repo's `.leerie/` directory, controls *what leerie builds,
lints, and tests*. These are different concerns, owned by different
audiences: `leerie.toml` is for the operator; `.leerie/config.toml` is
for the repo.

**The problem it solves.** Without `.leerie/config.toml`, leerie
auto-detects your build/lint/test commands on every run by inspecting
lockfiles and config files. That works, but it means every worker
re-discovers the same commands. Declaring them once is the "CI yaml"
analog — the same way you tell GitHub Actions how to build and test, you
tell leerie.

**Keys.** The file is flat TOML (same parser as `leerie.toml`):

```toml
build = "make build"
lint  = "ruff check ."
test  = "pytest -x"
# setup_packages = "libvips-dev fonts-noto"
```

- `build`, `lint`, `test` — declare the corresponding axis explicitly.
  Missing keys fall through to auto-detection. An empty string (`""`)
  means "not applicable" and suppresses detection for that axis.
- `setup_packages` — comma-separated list of apt package names to
  install at the system level. Used to auto-generate a
  `.leerie/Dockerfile` (see below) when no committed Dockerfile exists.
  Not consumed by BLT resolution.

**Resolution order.** Declared values win over inferred values, per axis.
The orchestrator calls `resolve_blt()` (which reads `.leerie/config.toml`
then fills any missing axes from inference); neither the conformance phase
nor the final conformance pass calls `_infer_build_lint_test()` directly.

**Getting started.** Three entry points:

- **`leerie config`** (bare): prints the effective build/lint/test
  configuration for the current repo — each axis, its value, and whether
  it came from `.leerie/config.toml` (`[config]`) or from inference
  (`[inference]`). Run this to audit what leerie will use on the next
  run without actually starting one.

- **`leerie config --init`**: auto-detects BLT commands using the same
  table as inference and writes a `.leerie/config.toml` with the detected
  values pre-filled (uncommented) and a commented `setup_packages`
  example. No model involved — pure deterministic detection. Exits 1 if
  `config.toml` already exists. After it runs: edit the file if needed,
  then `git add .leerie/ && git commit`.

- **`leerie config --chat`**: opens an interactive `claude` session (not
  headless — a real interactive session) with a config-generation system
  prompt and `--add-dir` pointing at your repo. The session reads your
  repo's CI config, lockfiles, and manifests, asks you questions if
  needed, and writes `.leerie/config.toml` — and optionally
  `.leerie/Dockerfile` for repos that need system packages. Use this for
  polyglot or non-standard setups where `--init` would miss something.

All three sub-modes are host-only fast paths: they exit before `nerdctl
run` and never start a container.

**Per-repo container image.** If your repo needs system packages (C
libraries for native gems, fonts, specialized tooling) that require root
to install, `.leerie-setup.sh` cannot help — it runs as the unprivileged
`leerie` user. Instead, commit a `.leerie/Dockerfile` that extends the
base image:

```dockerfile
ARG BASE_IMAGE
FROM $BASE_IMAGE

USER root
RUN apt-get update && apt-get install -y --no-install-recommends \
    libvips-dev \
    fonts-noto \
    && rm -rf /var/lib/apt/lists/*
USER leerie
```

The launcher builds a derived image tagged `leerie-repo/<repo-id>:<version>`
and uses it for all subsequent runs. A second run with an unchanged
Dockerfile skips the build entirely (the launcher stores a content hash
and only rebuilds when the Dockerfile or base version changes). If you
declare `setup_packages` in `.leerie/config.toml` but commit no
`.leerie/Dockerfile`, the launcher auto-generates a minimal apt-install
Dockerfile and proceeds through the same build path. A committed
Dockerfile always takes precedence over `setup_packages`.

The full inventory of CLI flags and environment variables is in the
[README "Configuration" section](../README.md#configuration).

## Submitting and tracking a chain

A *chain* is a sequence of waves; each wave is a set of Leerie runs
that execute in parallel against the same target repository. Wave
N+1 only starts when every run in wave N reaches a terminal status.
Use chains for tasks with a fixed ordering — for example: run two
parallel scaffolds in wave 0, then run a follow-up integration job
in wave 1 that depends on both.

`leerie --chain` is a **laptop-side wave sequencer** (DESIGN §19).
It loops over waves on the laptop: for each wave, it fans out N
parallel `./leerie --runtime fly` invocations (one per prompt file),
waits for all to finalize on the laptop (existing single-run path),
runs synth-merge to build the next wave's base branch, pushes that
staging branch to origin, and advances. The laptop is the
sequencer; there is no Fly coordinator machine.

GitHub credentials are touched only by the laptop, via the existing
`host_finalize` mechanism per per-job run. Workers never see them.

### Step 1 — Write your prompt files

Prepare one prompt file per task. Plain text or Markdown, exactly as
you would pass to `leerie "..."`:

```
prompts/
  01-scaffold-api.md
  02-scaffold-worker.md
  03-integration.md
```

### Step 2 — Required env vars

No chain-specific env vars are required. Each per-job
`./leerie --runtime fly` invocation has its own env requirements
(same as today's single-run flow); set those once in your shell
profile.

### Step 3 — Submit the chain

```bash
# Each --wave defines one wave (one or more comma-separated prompt
# file paths). Waves execute sequentially on the laptop; runs within
# a wave run in parallel as separate Fly machines. In this example,
# two scaffolds run in parallel as wave 0, then the integration job
# runs in wave 1 once both scaffolds are done. The chain operates
# against $USER_REPO directly.
leerie --chain \
  --wave prompts/01-scaffold-api.md,prompts/02-scaffold-worker.md \
  --wave prompts/03-integration.md
```

The launcher mints a fresh `chain_id` (UUID), prints a submission
banner, then enters the wave loop. The wave loop runs in the
foreground of your terminal — keep it running until the chain
completes, or Ctrl-C to stop (the trap propagates SIGTERM to every
in-flight wave child).

`--chain-submit` is kept as a deprecated alias for `--chain`; both
behave identically.

### Step 4 — Monitor progress

The single-run verbs (`--status`, `--attach`, `--stop`, `--kill`,
`--resume`, `--finalize`) are ID-dispatched: pass a UUID and they
operate on the chain (iterating `$LEERIE_STATE_HOST_DIR/runs/*/run.json`
filtered by `chain_id`); pass a Fly machine id and they operate on
the single run (unchanged behavior).

From a different terminal:

```bash
# Per-run snapshot of every run in the chain:
leerie --status <chain-id>

# Poll until every chain run reaches a terminal state:
leerie --attach <chain-id>
```

### Step 5 — Worker branches and PRs

Each chain worker runs the leerie orchestrator on its own Fly
machine and produces a run branch (`leerie/runs/<run-id>`). When the
worker exits, the laptop's `decide_teardown` trap fires
`fetch_branch` + `host_finalize` (push + PR + destroy machine) just
like a single run today. By the time wave N completes, every wave-N
PR is open.

Between waves, the laptop synth-merges all wave-N branches into a
staging branch `leerie/stage/<chain-id>-wave-<N+1>` (via
`chain.git_ops.synth_merge_branches`), pushes the staging branch to
origin, and advances `current_base` to it. Wave N+1 workers see the
staged base as their starting point.

### Step 6 — List active chains

```bash
leerie --list --chains
```

Or via the deprecated alias `leerie --list-chains`. Both iterate
`$LEERIE_STATE_HOST_DIR/runs/*/run.json`, group runs by `chain_id`,
and print one row per chain (chain_id, status, pushed/total runs,
wave count, started_at).

### Step 7 — Pause, resume, cancel, or finalize a chain

```bash
# Pause every running chain run:
leerie --stop <chain-id>

# Resume every paused chain run; then re-run `leerie --chain --wave ...`
# to continue the wave loop from where it stopped. The wave loop's
# idempotency check skips waves whose runs are already all pushed.
leerie --resume <chain-id>

# Finalize every chain run that isn't pushed yet (push + open PR):
leerie --finalize <chain-id>

# Destroy every chain run's machine (idempotent).
leerie --kill <chain-id>
```

`--kill <chain-id>` iterates the chain's runs and invokes
`leerie --kill <run-id>` per run; already-killed runs are skipped.
