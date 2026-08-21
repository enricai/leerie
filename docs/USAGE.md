# A worked example: one Leerie run, end to end

## What this document is

A walkthrough. It follows a single Leerie run from invocation to merge so
you know what to expect on stdout and on disk at each phase. It is *not* a
reference — for the architecture and the reasons it works that way, see
[`docs/DESIGN.md`](DESIGN.md); for the full CLI-flag / env-var / worker-type
reference, see
[`docs/IMPLEMENTATION.md` §2½ *Configuration reference*](IMPLEMENTATION.md).
This document never restates either.

## Prerequisites recap

You need the `claude` CLI on `PATH` (logged in), `git`, a git repo with
`user.email`/`user.name` set and a clean working tree, and a container
runtime (Colima on macOS, `containerd`+`nerdctl` natively on Linux). You do
*not* need Python on the host. See [`docs/INSTALL.md`](INSTALL.md) for the
full per-OS setup and [README "Requirements"](../README.md#requirements) for
the complete prerequisite list, including why a long-lived `claude
setup-token` matters for multi-hour runs.

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
```

Setting `LEERIE_SOURCE_OF_TRUTH=codebase` pins the source-of-truth
preference for this run — useful when the default (`both`) is not what you
want. Every worker defaults to `sonnet`; `--model <alias>` overrides all of
them, `--model-<worker> <alias>` overrides one. Full flag/env-var reference:
[`IMPLEMENTATION.md` §2½](IMPLEMENTATION.md).

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
classifier asks (only surfaced when `--clarify` is passed — otherwise the
implementer makes a documented best-effort decision).

In an interactive terminal Leerie prompts you; you type answers, the run
continues. In a non-interactive context (CI, a plugin skill) Leerie
instead writes `<state-root>/pending-questions.json` and exits with
code 10 — not an error, a structured "need answers" signal. The plugin
skill at [`commands/leerie.md`](../commands/leerie.md) shows the
questions to the user, writes their answers to
`<state-root>/answers.json`, and resumes with
`resume --answers <state-root>/answers.json`.

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

The merged plan lives at `<state-root>/runs/<run-id>/plan.json`;
per-subtask spec files appear alongside it under `subtasks/<id>.json`;
the task document itself is written verbatim to `task.md` in the same
directory, which is what each spec's `_task_ref` points at. `<state-root>`
defaults to `$HOME/.leerie/<basename>/`; override with
`LEERIE_STATE_DIR`, `--state-dir`, or `state_dir =` in `leerie.toml`.

## Step 4 — Wave execution

For each wave Leerie creates a per-subtask git worktree off the run
branch (`leerie/runs/<run-id>`), then spawns an implementer worker in
each worktree. Workers run concurrently, capped by `--max-parallel`
(default 5).

On stdout you'll see lines like (with a hypothetical `<run-id>` of
`feat-add-dry-run-flag-a3f7c2`):

```
[wave 1] implementer feat-add-dry-run-flag: start
[wave 1] implementer feat-add-dry-run-flag: ok (3 turns, 12.4s)
[wave 1] integrating feat-add-dry-run-flag into leerie/runs/feat-add-dry-run-flag-a3f7c2
[wave 1] validating leerie/runs/feat-add-dry-run-flag-a3f7c2
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
--head leerie/runs/<run-id>`. Your working branch is **not** modified
locally — review and merge the PR on GitHub when you're satisfied. The
run branch remains in your repo as the PR head until you merge the PR.
The per-subtask branches `leerie/subtasks/<run-id>/*` are **deleted
automatically** at finalize — they were the mechanism for parallel
implementer isolation and carry no information that isn't already in the
run branch's merge graph (each subtask is merged with `--no-ff`, so its
commits still appear as a named merge bubble in `git log leerie/runs/<run-id>
--graph`).

When you no longer need the run branch either (e.g., after the PR is
merged on GitHub):

```bash
./scripts/cleanup.sh --run-id <run-id> --branches
```

deletes the run branch and any remaining subtask branches. The per-run
state directory is kept as a smaller audit trail; `rm -rf` it manually
when you no longer need that either. For an audit cleanup across every
past run, use `--all-runs --branches`.

## What happens when something goes wrong

**A subtask reports `blocked`.** The implementer hit something it cannot
resolve (an external dependency, an ambiguous spec, a failing test it
cannot fix). The wave aborts *before* integration, the blocker reason
lands in `<state-root>/runs/<run-id>/state.json`, and Leerie exits
non-zero. You read the blocker, fix the upstream issue, then `./leerie
resume`. See [`DESIGN.md`](DESIGN.md) §8 for the evidence-gated loop
logic that produces this signal.

**Integration fails.** The integrator can't merge a subtask branch into
the run branch — usually a conflict it cannot resolve behaviorally.
Leerie prints the diagnosis to stderr, aborts the in-progress merge so
the run branch stays clean, and exits non-zero. Pull up the conflicting
branches yourself, resolve, and resume.

**The run is interrupted.** Ctrl-C, system reboot, budget-cap hit. Run
`./leerie resume` from the same directory; finished waves are not
re-run. The full state schema is documented in
[`IMPLEMENTATION.md`](IMPLEMENTATION.md) §8.

**The worker session's credential expires mid-run.** A container can't
refresh a copied subscription OAuth token, so a run started with only an
interactive login can outlive it — you'll see "Failed to authenticate:
OAuth session expired" on stderr. Leerie does worktree-only cleanup,
state and branches survive, and it exits non-zero without touching
finalize. Re-authenticate (`claude /login`, or better, a `claude
setup-token`) and run `./leerie resume`. See [`DESIGN.md`](DESIGN.md) §6
*Credential strategy* for why the container can't self-heal this.

## Walking away from a remote run (`--runtime fly`)

Remote runs are designed to outlive your local terminal. The
orchestrator runs detached inside the Fly Machine; your local terminal
only watches the log stream.

| You did | Leerie did | Verb to come back |
|---|---|---|
| `leerie "task" --runtime fly` | provisioned a Fly Machine, started the orchestrator detached, opened a tail of its log | — (you're attached) |
| pressed Ctrl-C / closed your laptop / lost WiFi | tail broke; orchestrator on the machine keeps running | `leerie resume <run-id>` |
| `leerie stop <run-id>` | stopped the machine cleanly; filesystem preserved on Fly volume | `leerie resume <run-id> --runtime fly` |
| `leerie kill <run-id>` | destroyed the machine; run is over | start a new run |

`leerie resume` is a single smart-router verb: it wakes a paused machine,
attaches to a still-alive orchestrator, or relaunches against an
alive-but-orphaned machine, automatically. Pass `--shell` to drop into a
bash shell at `/work` instead of tailing.

On Ctrl-C you'll see a banner with the reattach/pause/destroy commands —
copy the run id from it if you need to. `leerie list` shows every run
(local and remote); `leerie list status <state> --runtime fly` filters by
both axes; `leerie list --runtime fly` (no `status`) queries Fly directly
for every machine under the app, useful when you've lost track of a
machine id.

> **In-flight detached runs** won't show up in `leerie list` until
> classification completes (~1 min), because `state.json` lives on the
> Fly Machine until `leerie finalize` streams it back. The detach banner
> printed on Ctrl-C is the canonical source of the run-id during that
> window.

The first `--runtime fly` invocation auto-installs `flyctl`, auto-creates
the Fly app, and builds the leerie image on Fly's remote builder (no host
Docker daemon required, ~3-5 min the first time, cached after). See
[`docs/INSTALL.md`](INSTALL.md) for the `--local-build` opt-in and its
caveats.

## Tuning for your workflow

The full inventory of CLI flags, environment variables, `leerie.toml`
keys, and worker types is in
[`docs/IMPLEMENTATION.md` §2½ *Configuration reference*](IMPLEMENTATION.md).
Two things worth calling out here because they change what you'll see in
this walkthrough:

- **Per-repo build/lint/test declaration** — commit `.leerie/config.toml`
  (`leerie config --init` to generate one from auto-detection, `leerie
  config --chat` for an interactive session on polyglot repos) so every
  worker skips re-discovering your build/lint/test commands. After each
  run, the `dep_capture` worker also auto-updates this file with the
  packages/installs the run actually needed — never auto-committed; `git
  add .leerie/ && git commit` when you're ready. See
  [`IMPLEMENTATION.md` §0 "`config`"](IMPLEMENTATION.md) and §6½ for the
  full mechanism (including `.leerie/Dockerfile` for repos needing system
  packages).
- **Browser-based system tests just work** — headless Chromium and a
  matching chromedriver ship pre-installed in the leerie image, with the
  rootless-container Chrome flags baked in automatically. No
  project-level `ChromeOptions`/`--no-sandbox` configuration needed;
  existing project config continues to work unchanged.

## Submitting and tracking a chain

A *chain* is a sequence of waves; each wave is a set of Leerie runs that
execute in parallel against the same target repository. Wave N+1 only
starts when every run in wave N reaches a terminal status. Use chains for
tasks with a fixed ordering — for example: run two parallel scaffolds in
wave 0, then a follow-up integration job in wave 1 that depends on both.

`leerie chain` is a **laptop-side wave sequencer** (DESIGN §19): it loops
over waves, fanning out N parallel `./leerie --runtime fly` invocations
per wave, waiting for all to finalize, synth-merging the wave's branches
into the next wave's base branch, and advancing. There is no Fly
coordinator machine; GitHub credentials are touched only by the laptop.
From inside Claude Code the same verbs are available as the `/chain`
plugin skill ([`commands/chain.md`](../commands/chain.md)).

**Write one prompt file per task** (plain text or Markdown, exactly as
you'd pass to `leerie "..."`), then submit:

```bash
leerie chain \
  --wave prompts/01-scaffold-api.md,prompts/02-scaffold-worker.md \
  --wave prompts/03-integration.md
```

Two scaffolds run in parallel as wave 0; the integration job runs in wave
1 once both finish. No chain-specific env vars are required — each
per-job `--runtime fly` invocation has the same requirements as a single
run. The wave loop runs in your terminal's foreground until the chain
completes, or Ctrl-C to stop it (propagates SIGTERM to every in-flight
wave child).

**Monitor and manage** with the ID-dispatched verbs — pass the chain's
UUID and they operate on every run in the chain (iterating
`run.json` filtered by `chain_id`); pass a Fly machine id and they operate
on the single run as usual:

```bash
leerie status <chain-id>      # per-run snapshot
leerie attach <chain-id>      # poll until every run reaches a terminal state
leerie stop <chain-id>        # pause every running chain run
leerie resume <chain-id>      # resume every paused run, then re-run `chain --wave ...` to continue
leerie finalize <chain-id>    # push + open PR for every unpushed run
leerie kill <chain-id>        # destroy every chain run's machine
leerie list chains            # group runs by chain_id
```

Each wave-N job produces a normal run branch and PR the same way a
single run does; by the time wave N completes, every wave-N PR is open.
Full chain mechanics (per-job lifecycle, synth-merge, idempotency,
resume-after-conflict) are in
[`IMPLEMENTATION.md` "Chain verbs"](IMPLEMENTATION.md).

## Reclaiming disk: `leerie prune`

Nothing reaps run state automatically. Measured on one repo after three
weeks: **1.5 GB** across 71 run directories and 23,158 repo-map-cache
entries, plus 64 stale `leerie/subtasks/*` branches — while leerie's own
preflight refuses to start a run on low disk headroom and tells you to
prune by hand ([`docs/POSTMORTEM-2026-08-14.md`](POSTMORTEM-2026-08-14.md), F22).

```bash
leerie prune                       # dry-run: shows what it would remove
leerie prune --apply               # actually remove
leerie prune --older-than 30 --apply   # default cutoff is 14 days
```

It removes three things, only when it has positive evidence a piece is
safe to delete — never on absence of evidence:

- **terminal run directories** — only ones with `finished_at` or
  `killed_at`; a paused or in-flight run survives regardless of age
  (subject to `--older-than`).
- **repo-map cache entries** — regenerated on demand (subject to
  `--older-than`).
- **orphaned subtask branches** — `leerie/subtasks/<run-id>/*` whose run
  is not live, deleted via `git branch -d` (refuses an unmerged branch,
  so work with no other copy survives). Not subject to `--older-than`.
  Branches with unmerged commits are reported, not deleted; remove them
  anyway with `scripts/cleanup.sh --run-id <id> --subtask-branches`.

**Dry-run is the default and that is deliberate**: this deletes
directories that may hold the only record of a paid-for run, so the safe
mode is the one you get without asking for it.
