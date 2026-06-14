# Leerie

**Leerie** is an autonomous task driver for Claude Code. One prompt. Finished, committed, validated code. No steering mid-run, no polishing when it's done.

Most tools that call themselves autonomous still require you: to confirm a direction, catch a hallucination, or clean up the result before it's usable. Leerie doesn't. It classifies the task, decomposes it, implements each piece in parallel isolated worktrees, validates the integrated result, and merges — beginning to end, unattended.

It runs entirely on the **Claude Code CLI and your existing subscription** — no Anthropic API key, no per-call billing. If you have Claude Code installed and logged in, you have everything it needs.

**Why it actually finishes without you:**

Most AI "orchestrators" let the model pilot: the model decides what to do next, declares when it's done, and judges whether it succeeded. That's where drift, hallucinated completion, and silent failures come from — and why you end up steering.

Leerie inverts the relationship. **The model writes code. The program runs everything else.** Phases, wave scheduling, retries, caps, merge logic, and success-criteria enforcement are ordinary Python — real loops and conditionals that cannot drift.

- **No silent failures.** Every worker output is JSON-schema-validated before the orchestrator acts on it. A worker cannot, by malformed output or confident hallucination, cause the system to do something undefined.
- **Confidence is the only hard gate.** The implementer self-gates on evidence-anchored confidence in `root_cause` and `solution` (≥9 on both, see DESIGN.md §8) — falsifiers tested, contradictions reconciled, gaps named with concrete artifacts. A worker that cannot justify the score exits `blocked` with the gap analysis. Everything else — tests passing, lint clean, build green, per-criterion satisfaction — is best-effort: surfaced as advisory warnings on the subtask result, never escalated to `failed` or `blocked` by the orchestrator. The criteria file is the implementer's working note, not a gate.
- **Workers must justify confidence with evidence, not feelings.** Before writing code, an implementer clears domain-specific evidence gates — file-and-line citations, reproductions, falsification attempts. A self-reported score without hard artifacts doesn't clear the bar.
- **Parallel work that's actually safe.** Each implementer gets an isolated git worktree. Parallel writes never collide. Conflicts surface one wave at a time, close to the work that caused them.
- **Resumable by design.** A reboot, network blip, budget cap, the Claude Code subscription rate-limit, Ctrl-C, or an external kill (SIGTERM from CI / systemd / a closed terminal) all lose nothing — the run branch is the durable record, worktrees are torn down, and `--resume` picks up from the last completed wave. When the subscription rate-limit hits and the reset time is unambiguously parseable, leerie even auto-resumes after the reset window without manual intervention. The explicit "throw this away" gesture is `scripts/cleanup.sh --run-id <id> --branches`, not Ctrl-C.
- **Parallel-safe across runs.** Multiple `./leerie` invocations in the same repository each get a unique `run_id` (a derived branch + state directory). Their branches, worktrees, and per-run state directories never collide. Launch a fix and a feature in parallel without coordination.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![tests](https://github.com/enricai/leerie/actions/workflows/test.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/test.yml)
[![syntax](https://github.com/enricai/leerie/actions/workflows/syntax.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/syntax.yml)
[![shellcheck](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml)
[![Version](https://img.shields.io/github/v/release/enricai/leerie?color=orange&label=version)](CHANGELOG.md)

## How it works

The orchestrator is a Python program — not an in-session agent. It shells out
to `claude -p` (headless mode) for each unit of LLM work. Each call is a
separate process, so there is no subagent nesting anywhere. Control flow lives
in real Python: `for` loops, `if` statements, counters. It cannot drift.

```
leerie "<task>"
   ├─ Phase 1  Classify into 1..9 categories                    → 1 claude -p
   │             ↓ derive run_id (category + slug + start-hex)
   │           • Clarify — intent-only questions (optional; skipped for fully-specified tasks)
   ├─ Phase 2  Plan — one planner per category (parallel)        → N claude -p
   │           • Reconcile — cross-domain capability-tag bridging (0 or 1 claude -p, when needed)
   ├─ Phase 3  Schedule — global dependency graph → topo waves   (pure Python)
   ├─ Phase 4  Create leerie/runs/<run-id> branch + worktree (per-run unique)
   ├─ Phase 5  Per wave: implement (parallel, isolated worktrees) → claude -p each
   │           integrate into the run branch; validate the run branch
   └─ Phase 6  Push run branch; open PR against working branch; cleanup
               (working branch not modified locally)
```

For the full rationale — why the orchestrator is a script rather than a plugin
command, all architectural decisions, and the complete enforcement surface —
read [`docs/DESIGN.md`](docs/DESIGN.md).

## Requirements

- `claude` CLI on `PATH`, logged in interactively
- `git`
- A git repository with `user.email` and `user.name` configured
- A reasonably clean working tree
- A container runtime (one-time setup — see *Install* below)
- `gh` CLI logged in (`gh auth status` succeeds), or pass `--no-push` to skip the finalize PR step

**Leerie runs inside a container** to give cleanup a hard kernel
guarantee: when you Ctrl-C, the Linux PID namespace is torn down and
every worker / build / test runner is reaped, even ones that detached
into their own POSIX sessions. See
[`docs/DESIGN.md` §6](docs/DESIGN.md) and
[`docs/IMPLEMENTATION.md` §0.5](docs/IMPLEMENTATION.md) for the
reasoning and mechanics. Python is provisioned *inside* the container
by the image; you don't need it on the host.

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

The installer auto-installs and starts the container runtime per OS
(Colima on macOS via `brew`; containerd + pinned `nerdctl` on
Debian/Ubuntu, Fedora/RHEL, and Arch via the distro package manager)
and then clones leerie into `~/.leerie` + symlinks `leerie` into
`~/.local/bin`. Sudo prompts apply on Linux. Full per-OS details and
the rootless / unsupported-distro paths live in
[`docs/INSTALL.md`](docs/INSTALL.md).

### Inside Claude Code (recommended for chat-based use)

```
/plugin marketplace add enricai/leerie
/plugin install leerie@enricai-leerie
```

Then in any Claude Code session:

```
/leerie Fix the login timeout bug and add a regression test
```

### Inspect before installing

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh -o install.sh
bash install.sh --dry-run            # print actions without executing
bash install.sh                       # then run for real
```

Customize with `--prefix DIR` (default `~/.leerie`), `--bin-dir DIR`
(default `~/.local/bin`), or `--ref REF` (default `main`).

### Manual container-runtime setup

If you'd rather install the runtime yourself (CI, dotfiles managers,
or you want to pin a different `nerdctl` version), do the runtime
steps manually then pass `--no-runtime-install` (or set
`LEERIE_NO_RUNTIME_INSTALL=1`):

**macOS** (Colima manages a Linux VM):

```bash
brew install colima
# Size the VM at ~half your host's CPU/RAM (Colima's 2-CPU / 2-GB
# default OOMs under parallel leerie workloads — see docs/INSTALL.md
# for the auto-sizing the installer applies). On an 8/16 host:
colima start --runtime containerd --mount-type virtiofs --cpu 4 --memory 8
# Also add 4 GB of swap (paste the YAML block from docs/INSTALL.md
# "Memory pressure: swap configuration" into ~/.colima/default/colima.yaml,
# then colima stop && colima start). This step is optional but strongly
# recommended — without swap the VM OOMs under heavy parallel load.
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

(Do not `brew install nerdctl` — the formula requires Linux. Leerie
auto-installs the host-side `nerdctl` shim from Colima on first run.)

**Linux** (Debian/Ubuntu — see [`docs/INSTALL.md`](docs/INSTALL.md)
for Fedora, Arch, and rootless setups):

```bash
sudo apt-get install -y containerd
NERDCTL_VERSION=2.3.1
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
curl -L "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" \
  | sudo tar -C /usr/local/bin -xz nerdctl
sudo systemctl enable --now containerd
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

### Manual (clone + run)

If you'd rather not run any installer at all:

```bash
git clone https://github.com/enricai/leerie.git
./leerie/leerie "your task"   # or symlink onto PATH
```

The first invocation builds the container image (~60–120s); subsequent
runs reuse it. The container runtime must already be set up — see
[`docs/INSTALL.md`](docs/INSTALL.md) for per-OS instructions.

## Usage

```bash
# From the root of the target git repository:
leerie "Fix the login timeout bug and add a regression test"
# (substitute leerie if you used the manual install)

# Or pass a path to a .txt / .md file whose contents are the task —
# useful for multi-paragraph briefs that are awkward to quote on the shell:
leerie path/to/task.md

# Resume an interrupted or budget-capped run. Auto-picks if exactly one
# in-flight run exists; otherwise pass the run-id (see `--list`).
leerie --resume
leerie --resume fix-login-timeout-bug-b81e90

# List in-flight and completed runs in this repository:
leerie --list

# Skip the default push + PR at finalize (run completes with the run
# branch local-only; your working branch is unchanged):
leerie "task" --no-push

# Skip pre-push hooks at finalize (the user's explicit override; defaults
# off). Affects only the final `git push`; worker commits still run hooks.
leerie "task" --no-verify

# Opt into intent questions (default: no questions are surfaced).
leerie "task" --clarify

# Pre-supply clarification answers (JSON object):
# Keys are question ids from the classifier, plus "source_of_truth"
# set to "codebase", "research", or "both".
leerie "task" --answers answers.json

# Override caps (defaults: 200 total workers, 5 in parallel per wave).
# --max-workers also reads LEERIE_MAX_WORKERS or max_workers in
# leerie.toml; --max-parallel also reads LEERIE_MAX_PARALLEL or
# max_parallel in leerie.toml.
leerie "task" --max-workers 80 --max-parallel 4
export LEERIE_MAX_WORKERS=80

# Dial how persistent the planner and implementer are at building
# confidence before they exit blocked (default 8 evidence-gate rounds
# inside each worker; see DESIGN §8):
leerie "task" --confidence-rounds 12
export LEERIE_CONFIDENCE_ROUNDS=12

# Override the default source-of-truth preference (`both`) — pass
# --source-of-truth on the command line for a one-off, set
# LEERIE_SOURCE_OF_TRUTH for the session, or commit a leerie.toml
# at the repo root with the line `source_of_truth = codebase` (or
# research / both).
# Precedence (highest first): --source-of-truth > env > leerie.toml.
export LEERIE_SOURCE_OF_TRUTH=codebase    # or: research, both
leerie "task" --source-of-truth codebase

# Choose the model. Without overrides, judgment workers (classifier /
# planner / reconciler / plan_overlap_judge / provision / integrator)
# default to opus and the acting
# workers (implementer, conformer) default to sonnet — see
# docs/IMPLEMENTATION.md §2 "Model selection" for the full env-var /
# CLI-flag / TOML-key table.
# Set LEERIE_MODEL=sonnet (or --model sonnet) to restore the
# pre-0.3 all-sonnet behavior in one knob.
export LEERIE_MODEL=sonnet                # or: opus, haiku
leerie "task" --model opus
leerie "task" --model-implementer opus --model-classifier haiku

# Optional but recommended — lower the auto-compaction threshold
# for worker processes (default is 95%):
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70

# Chain orchestration: submit and track multi-run chains. A chain is
# N parallel `leerie --runtime fly` invocations per wave, with
# synth-merge between waves to build the next wave's base branch.
# The laptop is the sequencer; no Fly coordinator machine. No chain-
# specific env vars required — each per-job `--runtime fly`
# invocation has its own env requirements unchanged.

# Submit a new chain. Each --wave defines one wave (comma-separated
# prompt files). Waves execute in order; runs within a wave execute
# in parallel as separate Fly machines. N waves supported. The chain
# operates against $USER_REPO directly.
leerie --chain \
  --wave prompts/fetch.md,prompts/lint.md \
  --wave prompts/publish.md

# ID-dispatched verbs: UUID positional → chain scope (iterates
# run.json filtered by chain_id); Fly machine id → existing
# single-run scope.
leerie --status   <chain-id>   # render per-run states from run.json
leerie --attach   <chain-id>   # poll run.json files every 5s
leerie --stop     <chain-id>   # pause every running chain run
leerie --kill     <chain-id>   # destroy every chain run's machine
leerie --resume   <chain-id>   # resume every paused chain run
leerie --finalize <chain-id>   # push + open PR for every unpushed run
leerie --list --chains         # group runs by chain_id

# Deprecated --chain-* aliases (kept for backwards compat) shim to
# the new verbs above.
```

Inside Claude Code (after `/plugin install leerie@enricai-leerie`):

```
/leerie Fix the login timeout bug and add a regression test
```

## Configuration

Complete reference for every CLI flag, environment variable, and
`leerie.toml` key the orchestrator reads.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `task` (positional) | — | The task description (literal string, or path to a `.txt`/`.md` file). Required unless `--resume`, `--list`, or `--phase` is given. |
| `--resume` | off | Resume an interrupted run. Auto-picks if exactly one run exists; pass the run-id if multiple. |
| `--run-id ID` | — | Select a specific run by id (e.g., for `--resume` or `--phase` when multiple runs are in flight). |
| `--list` | off | Enumerate in-flight and completed runs in this repository (run id, started, status, branch). |
| `--no-push` | off | Skip the default push + PR at finalize. The run completes with the run branch local-only; your working branch is unchanged. Overrides `LEERIE_NO_PUSH` / `leerie.toml`. |
| `--no-verify` | off | Pass `--no-verify` to the finalize `git push` only (skips pre-push hooks). Worker commits inside worktrees still run all hooks. The user's explicit override per CLAUDE.md's hooks principle. |
| `--answers FILE` | — | JSON object of pre-supplied clarification answers (keyed by question `id`; may include `source_of_truth`). |
| `--clarify` | off | Opt into surfacing intent questions to the user. Default: questions are dropped after the classifier's codebase→research filter, and the implementer makes a documented best-effort decision. Also `LEERIE_CLARIFY` env var or `clarify = true` in `leerie.toml`. |
| `--max-workers N` | `200` | Cap on total `claude -p` invocations across the run. Also `LEERIE_MAX_WORKERS` env var or `max_workers` in `leerie.toml`. |
| `--max-parallel N` | `5` | Cap on concurrent workers within a wave. Per-worker cgroup containment keeps an OOM inside one worker's cgroup; users on smaller VMs can opt down. Also `LEERIE_MAX_PARALLEL` env var or `max_parallel` in `leerie.toml`. |
| `--worker-memory-max SIZE` | auto | Per-worker cgroup memory cap (e.g. `4G`, `512M`). Bounds RAM the worker subtree may consume; OOMs stay inside the worker cgroup rather than cascading to sshd / orchestrator. Auto-derived from `/proc/meminfo` when unset (VM RAM / `max_parallel+1`, capped at 4 GiB). Also `LEERIE_WORKER_MEMORY_MAX` or `worker_memory_max` in `leerie.toml`. |
| `--confidence-rounds N` | `8` | Evidence-gate rounds the planner and implementer may run before exiting blocked (DESIGN §8). Overrides `LEERIE_CONFIDENCE_ROUNDS` and `leerie.toml`. |
| `--skip-smoke` | off | Skip the live `claude -p` preflight smoke test. |
| `--source-of-truth VALUE` | `both` | `codebase` / `research` / `both`. Overrides `LEERIE_SOURCE_OF_TRUTH` and `leerie.toml`. |
| `--runtime VALUE` | `local` | `local` / `fly`. Execution backend for per-subtask worker containers. Overrides `LEERIE_RUNTIME` and `leerie.toml`. |
| `--inspect-dir PATH` | none | Extra directory the inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) may read; forwarded to `claude -p` as `--add-dir`. Repeatable. Also `LEERIE_INSPECT_DIRS` (colon-separated) or `inspect_dirs` in `leerie.toml` (comma-separated). |
| `--model ALIAS` | per-worker (judgment: `opus`; acting workers — implementer, conformer: `sonnet`) | `sonnet` / `opus` / `haiku`. Sets every worker this run; without it the per-worker defaults apply. |
| `--model-<worker> ALIAS` | per-worker default (`implementer`, `conformer` → `sonnet`; everything else → `opus`) | Per-worker override. `<worker>` is one of `classifier`, `planner`, `reconciler`, `plan_overlap_judge`, `provision`, `implementer`, `integrator`, `conformer`. Overrides `--model`, `LEERIE_MODEL`, and `leerie.toml`. |
| `--effort LEVEL` | per-worker (judgment: `high`; acting workers — implementer, conformer: inherit Claude default) | `low` / `medium` / `high` / `xhigh` / `max`. Reasoning-depth dial forwarded to `claude -p --effort`. Pins judgment workers to a consistent depth across runs to reduce same-job variance (e.g. planner subtask-count drift). IMPLEMENTATION.md §2 "Effort selection". |
| `--effort-<worker> LEVEL` | per-worker default (judgment workers → `high`; acting workers → inherit Claude default) | Per-worker override. `<worker>` is one of the orchestrator workers (same set as `--model-<worker>`). Overrides `--effort`, `LEERIE_EFFORT`, and `leerie.toml`. |
| `--judge-model ALIAS` | `sonnet` | Model alias for the post-run judge skill. Also `LEERIE_MODEL_JUDGE` or `model_judge` in `leerie.toml`. |
| `--heal-model ALIAS` | `sonnet` | Model alias for the post-run self-heal skill. Also `LEERIE_MODEL_HEAL` or `model_heal` in `leerie.toml`. |
| `--heal-max-rounds N` | `10` | Maximum heal-loop iterations per `call_type`. Also `LEERIE_HEAL_MAX_ROUNDS` or `heal_max_rounds` in `leerie.toml`. |
| `--heal-success-threshold RATE` | `0.9` | Pass-rate threshold for the heal-loop SUCCESS verdict. Also `LEERIE_HEAL_SUCCESS_THRESHOLD` or `heal_success_threshold` in `leerie.toml`. |
| `--verbosity LEVEL` | `stream` | `quiet` / `normal` / `stream` / `debug`. Controls inline per-worker activity output; full per-worker stream is always saved to `<state-root>/logs/<sid>.log` (where `<state-root>` is the resolved state directory — default `$HOME/.leerie/<basename>/`). |
| `-v` / `-vv` | `0` (off) | Shortcuts that anchor to `normal`: `-v` = `stream`, `-vv` = `debug`. With no `-v` and no `--verbosity`, falls through to `LEERIE_VERBOSITY` / `leerie.toml` / default `stream`. |
| `-q` / `-qq` | `0` (off) | Shortcuts that anchor to `normal`: `-q` = `normal` (pre-streaming behavior), `-qq` = `quiet`. With no `-q` and no `--verbosity`, falls through to the same chain as `-v`. |
| `--telemetry` / `--no-telemetry` | on | Enable / disable telemetry NDJSON event writing. Also `LEERIE_TELEMETRY=1`/`0` or `telemetry=true`/`false` in `leerie.toml`. |
| `--telemetry-dir DIR` | `events` | Subdirectory name under the run dir for telemetry NDJSON events. Also `LEERIE_TELEMETRY_DIR` or `telemetry_dir` in `leerie.toml`. |
| `--judge-dir DIR` | `judge-out` | Subdirectory name under the run dir for LLM judge output. Also `LEERIE_JUDGE_DIR` or `judge_dir` in `leerie.toml`. |
| `--heal-dir DIR` | `heal-out` | Subdirectory name under the run dir for LLM self-heal output. Also `LEERIE_HEAL_DIR` or `heal_dir` in `leerie.toml`. |
| `--phase PHASE` | — | Run a post-run skill phase (`judge` or `heal`) against an existing run's captured LLM calls instead of starting a new run. Use `--run-id` to select when multiple runs exist. |
| `--version` | — | Print `leerie <version>` and exit. |
| `--status STATE` | — | With `--list`, restrict the table to runs whose derived status matches STATE. One of: `seed-failed`, `corrupt-sidecar`, `in-progress`, `done`, `done-pushed-no-pr`, `done-pushed-pr`, `push-failed`, `pr-failed`, `paused`, `killed`, `sync-failed`. |
| `--skip-overlap-judge` | off | Skip the phase 2¾ plan-overlap judge (DESIGN §5). Auto-skipped on single-planner runs; this flag disables it on multi-planner runs. Also `LEERIE_SKIP_OVERLAP_JUDGE` or `skip_overlap_judge` in `leerie.toml`. |
| `--skip-budget-check` | off | Skip the post-schedule budget-feasibility preflight (DESIGN §13). The runtime backstop in `State.bump_workers()` still fires. Also `LEERIE_SKIP_BUDGET_CHECK` or `skip_budget_check` in `leerie.toml`. |
| `--dangerously-skip-permissions` | off | Pass `--dangerously-skip-permissions` to every `claude -p` worker, including judgment workers that run in the real repo cwd. Waives DESIGN §12 read-only enforcement. Also `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` or `dangerously_skip_permissions` in `leerie.toml`. |
| `--pr-template NAME` | none | When the target repo has multiple PR templates in `PULL_REQUEST_TEMPLATE/`, pick this one by basename (with or without `.md`). Also `LEERIE_PR_TEMPLATE` or `pr_template` in `leerie.toml`. |
| `--pr-writer-model ALIAS` | `sonnet` | Model alias for the finalize-time PR title + body writer. Also `LEERIE_MODEL_PR_WRITER` or `model_pr_writer` in `leerie.toml`. |

### Launcher verbs

These flags are handled by the bash launcher before the container starts.
A summary appears in the `leerie --help` epilog; see below for full
details and sub-flags.

**Lifecycle (remote mode):**

| Flag | Description |
|------|-------------|
| `--stop <run-id> [--runtime fly]` | Pause a remote Fly machine. Resumable via `--resume`. |
| `--kill <run-id> [--force]` | Destroy a remote machine permanently. `--force` skips confirmation. Also accepts `--machine-id <id> [--app <app>]` for orphan cleanup. |
| `--finalize <run-id> [--force] [--no-verify] [--no-push] [--runtime fly]` | Post-detach finalization: collect un-integrated subtask branches on the machine, fetch the run branch, then push + open PR on the host. Without `--force`, requires the orchestrator to be dead. `--force` SIGTERMs a live orchestrator first, then collects and fetches. |
| `--re-seed <run-id> [--force]` | Mid-run host→machine re-rsync of dirty delta. `--force` bypasses the safety check that refuses to clobber machine-side uncommitted edits. |

**Resume modifiers (used with `--resume`):**

| Flag | Description |
|------|-------------|
| `--shell` | Drop into a bash shell at `/work` on the machine instead of tailing the orchestrator log. |
| `--auto-finalize` | On clean orchestrator exit, automatically run `leerie --finalize`. |
| `--no-re-seed` | Skip the automatic re-seed of dirty delta on resume. |

**Build and runtime:**

| Flag | Description |
|------|-------------|
| `--state-dir PATH` | Override the per-repo state directory. Also `LEERIE_STATE_DIR` env var or `state_dir` in `leerie.toml`. |
| `--fly-disk-gb N` | Provision a Fly volume of N GB mounted at `/home/leerie`. Also `FLY_VM_DISK_GB` env var. |
| `--no-runtime-install` | Skip auto-install of container runtime (Colima / nerdctl / containerd). Also `LEERIE_NO_RUNTIME_INSTALL`. |
| `--no-auto-publish` | Skip the image-publish probe on startup. Also `LEERIE_NO_AUTO_PUBLISH`. |
| `--local-build` | Force local `nerdctl build` instead of the Fly remote builder. Also `LEERIE_LOCAL_BUILD`. |

### Environment variables and `leerie.toml` keys

| Env var | `leerie.toml` key | Description |
|---------|---------------------|-------------|
| `LEERIE_STATE_DIR` | `state_dir` | Override the per-repo run state directory. Unset → default `$HOME/.leerie/<basename>/` (outside the repo; no `.gitignore` entry needed in target projects). Cross-repo basename collisions are caught at use time via an `.owner` sidecar inside the dir. Set once in your shell profile for a global directory across all repos. |
| `LEERIE_SOURCE_OF_TRUTH` | `source_of_truth` | Sticky source-of-truth preference (`codebase` / `research` / `both`). Overridden by `--source-of-truth`. Unset → default `both`. |
| `LEERIE_RUNTIME` | `runtime` | Execution backend for per-subtask worker containers (`local` / `fly`). Overridden by `--runtime`. Unset → default `local`. |
| `LEERIE_MODEL` | `model` | Model alias applied to every worker. Overridden by `--model` and per-worker overrides. Unset → per-worker defaults (judgment workers `opus`, acting workers — implementer, conformer — `sonnet`). |
| `LEERIE_MODEL_<WORKER>` | `model_<worker>` | Per-worker override (e.g. `LEERIE_MODEL_IMPLEMENTER=opus`). Overridden by `--model-<worker>`. `<worker>` ∈ `classifier`, `planner`, `reconciler`, `plan_overlap_judge`, `provision`, `implementer`, `integrator`, `conformer`. Unset → `implementer` and `conformer` → `sonnet`; everything else → `opus`. |
| `LEERIE_EFFORT` | `effort` | Reasoning-depth dial forwarded to `claude -p --effort` (`low` / `medium` / `high` / `xhigh` / `max`). Applies to every worker; overridden by `--effort` and per-worker overrides. Unset → judgment workers `high`, acting workers inherit Claude default. |
| `LEERIE_EFFORT_<WORKER>` | `effort_<worker>` | Per-worker override (e.g. `LEERIE_EFFORT_PLANNER=max`). Overridden by `--effort-<worker>`. Same worker set as `LEERIE_MODEL_<WORKER>`. Unset → judgment workers `high`; acting workers (implementer, conformer) inherit Claude default. |
| `LEERIE_CONFIDENCE_ROUNDS` | `confidence_rounds` | Evidence-gate rounds per worker (positive integer). Overridden by `--confidence-rounds`. Unset → default `8`. |
| `LEERIE_INSPECT_DIRS` | `inspect_dirs` | Extra directories the inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) may read; forwarded as `--add-dir`. Env value is colon-separated; TOML value is comma-separated. Overridden by `--inspect-dir` (repeatable). Unset → none. |
| `LEERIE_VERBOSITY` | `verbosity` | Inline-output verbosity (`quiet` / `normal` / `stream` / `debug`). Overridden by `--verbosity`. `-v` / `-vv` / `-q` / `-qq` shortcuts override both. Unset → default `stream`. |
| `LEERIE_NO_PUSH` | `no_push` | Sticky opt-out from push + PR at finalize (truthy → skip). Overridden by `--no-push`. `--no-verify` has no env/TOML mirror — it is a per-invocation override only. Unset → default `false` (push + PR happen). |
| `LEERIE_CLARIFY` | `clarify` | Sticky opt-in to surfacing intent questions to the user (truthy → on). Overridden by `--clarify`. Unset → default `false`. |
| `LEERIE_MODEL_JUDGE` | `model_judge` | Model alias for the post-run judge skill. Overridden by `--judge-model`. Unset → default `sonnet`. |
| `LEERIE_MODEL_HEAL` | `model_heal` | Model alias for the post-run self-heal skill. Overridden by `--heal-model`. Unset → default `sonnet`. |
| `LEERIE_HEAL_MAX_ROUNDS` | `heal_max_rounds` | Maximum heal-loop iterations per `call_type`. Overridden by `--heal-max-rounds`. Unset → default `10`. |
| `LEERIE_HEAL_SUCCESS_THRESHOLD` | `heal_success_threshold` | Pass-rate threshold for the heal-loop SUCCESS verdict. Overridden by `--heal-success-threshold`. Unset → default `0.9`. |
| `LEERIE_TELEMETRY` | `telemetry` | Enable / disable telemetry NDJSON event writing (boolean). Overridden by `--telemetry` / `--no-telemetry`. Unset → default `true` (telemetry on). |
| `LEERIE_TELEMETRY_DIR` | `telemetry_dir` | Subdirectory name under the run dir for telemetry NDJSON events. Overridden by `--telemetry-dir`. Unset → default `events`. |
| `LEERIE_JUDGE_DIR` | `judge_dir` | Subdirectory name under the run dir for LLM judge output. Overridden by `--judge-dir`. Unset → default `judge-out`. |
| `LEERIE_HEAL_DIR` | `heal_dir` | Subdirectory name under the run dir for LLM self-heal output. Overridden by `--heal-dir`. Unset → default `heal-out`. |
| `LEERIE_MAX_WORKERS` | `max_workers` | Total worker-invocation budget. Overridden by `--max-workers`. Unset → default `200`. |
| `LEERIE_MAX_PARALLEL` | `max_parallel` | Concurrent workers per wave. Overridden by `--max-parallel`. Unset → default `5`. |
| `LEERIE_WORKER_MEMORY_MAX` | `worker_memory_max` | Per-worker cgroup memory cap (e.g. `4G`, `512M`). Overridden by `--worker-memory-max`. Unset → auto-derived from `/proc/meminfo`. |
| `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` | `dangerously_skip_permissions` | Waive §12 read-only enforcement on judgment workers (truthy → on). Overridden by `--dangerously-skip-permissions`. Unset → default `false`. |
| `LEERIE_SKIP_OVERLAP_JUDGE` | `skip_overlap_judge` | Skip the phase 2¾ plan-overlap judge on multi-planner runs (truthy → skip). Overridden by `--skip-overlap-judge`. Unset → default `false`. |
| `LEERIE_SKIP_BUDGET_CHECK` | `skip_budget_check` | Skip the post-schedule budget-feasibility preflight (truthy → skip). Overridden by `--skip-budget-check`. Unset → default `false`. |
| `LEERIE_PR_TEMPLATE` | `pr_template` | PR template basename for repos with multiple templates. Overridden by `--pr-template`. Unset → alphabetically first `.md`. |
| `LEERIE_MODEL_PR_WRITER` | `model_pr_writer` | Model alias for the finalize-time PR writer. Overridden by `--pr-writer-model`. Unset → default `sonnet`. |
| `LEERIE_WORKER_DEBUG` | — | Enable debug-level logging injection (`DEBUG=*`, `ANTHROPIC_LOG=debug`) into worker processes. Truthy → on. |
| `LEERIE_FLY_APP` | — | Fly.io app name used by launcher verbs (`--stop`, `--kill`, `--finalize`, etc.). Unset → default `leerie`. Launcher-only. |
| `LEERIE_REGION` | — | Fly region used by per-job `--runtime fly` machines (including those spawned by `leerie --chain`). Unset → default `iad`. Launcher-only. |
| `LEERIE_SEED_TIMEOUT_S` | — | Timeout in seconds for `seed_auth` / `seed_repo` bulk transfers over `flyctl ssh console`. Unset → default `600` (10 min). Launcher-only. |
| `LEERIE_PROGRESS_INTERVAL_S` | — | Heartbeat cadence in seconds for "still streaming" lines during bulk transfers. Set to `0` to suppress. Unset → default `10`. Launcher-only. |
| `LEERIE_MACHINE_START_TIMEOUT` | — | Timeout in seconds for Fly machine start. Unset → default `120`. Launcher-only. |
| `LEERIE_PAUSE_NOTIFY_CMD` | — | Shell command to `eval` when a Fly machine pauses on failure. Unset → no notification. Launcher-only. |
| `LEERIE_NO_RUNTIME_INSTALL` | — | Skip auto-install of container runtime (truthy → skip). Also `--no-runtime-install`. Launcher-only. |
| `LEERIE_NO_AUTO_PUBLISH` | — | Skip image publish probe (truthy → skip). Also `--no-auto-publish`. Launcher-only. |
| `LEERIE_LOCAL_BUILD` | — | Force local image build instead of Fly remote builder (truthy → local). Also `--local-build`. Launcher-only. |
| `LEERIE_NONINTERACTIVE` | — | Suppress interactive prompts in runtime-install and auth flows (truthy → non-interactive). Launcher-only. |
| `FLY_VM_DISK_GB` | — | Provision a Fly volume of this many GB. Also `--fly-disk-gb`. Launcher-only. |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | — | **Claude Code CLI variable**, not consumed by leerie. Set to `70` to backstop worker auto-compaction. |

### Precedence

- **Source-of-truth** (highest first): `--source-of-truth` →
  `LEERIE_SOURCE_OF_TRUTH` → `leerie.toml` → default `both`.
- **Model** (per worker, highest first): `--model-<worker>` →
  `--model` → `LEERIE_MODEL_<WORKER>` → `LEERIE_MODEL` →
  `model_<worker>` in `leerie.toml` → `model` in `leerie.toml` →
  per-worker default (`implementer`, `conformer` → `sonnet`; everything
  else → `opus`). The judgment-vs-acting split keeps the
  most-frequently-invoked workers on the lower-cost model while
  every judgment step gets Opus-grade reasoning. To restore the
  pre-0.3 all-sonnet behavior in one knob, set `LEERIE_MODEL=sonnet`
  or pass `--model sonnet`.
- **Confidence rounds** (highest first): `--confidence-rounds` →
  `LEERIE_CONFIDENCE_ROUNDS` → `confidence_rounds` in
  `leerie.toml` → default `8`.
- **Verbosity** (highest first): `--verbosity` → `-v`/`-vv`/`-q`/`-qq`
  shortcuts (anchored to `normal`, not to the resolved default) →
  `LEERIE_VERBOSITY` → `verbosity` in `leerie.toml` → default
  `stream`.

See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §2 for the
rationale behind these orders and the full validation contract.

## Worker types

Leerie spawns eight kinds of `claude -p` worker. Each is a separate
subprocess; there is no in-session agent nesting.

| Worker | Prompt source | Default model | Runs per task | Returns |
|--------|---------------|---------------|---------------|---------|
| `classifier` | `prompts/classifier.md` | opus | 1 | category set + intent questions |
| `planner` | `prompts/planner.md` | opus | one per category (parallel) | subtask list with deps |
| `reconciler` | `prompts/reconciler.md` | opus | 0, 1, or up to 3 (retried up to twice when its first attempt closes a dependency cycle or leaves unresolved tags) | eight arrays — `renames` / `added_provides` / `added_subtasks` / `conditional_drops` / `dropped_requires` (resolution; `conditional_drops` drops a planner-emitted consumer subtask whose own intent declares it conditional on an unresolvable in_plan precondition; `dropped_requires` removes an over-specified `requires` entry — an aggregate or coarser synonym of what the consumer itself provides — and ALSO plays a cycle-breaking role on retry); `dependency_edges` / `merged_subtasks` (cycle-breaking-only, used on retry when leerie's gates detect a cycle); `unresolvable` (escape hatch). DESIGN §5 |
| `plan_overlap_judge` | `prompts/plan_overlap_judge.md` | opus | 0 or 1 (phase 2¾, multi-planner runs only; auto-skipped on single-planner runs) | cross-domain surface overlap analysis. DESIGN §5 |
| `provision` | `prompts/provision.md` | opus | 0 or 1 (spawned only when the deterministic lockfile-detection table abstains — Java/Gradle, bare `pyproject.toml`, polyglot Makefile) | install recipe (argv-allowlisted) executed via `mise exec --`. See DESIGN §6½ |
| `implementer` | `prompts/implementer.md` | sonnet | one per subtask (per wave, parallel) | commits on a `leerie/subtasks/<run-id>/<subtask-id>` branch |
| `conformer` | `prompts/conformer.md` | sonnet | one per subtask, only on the implementer's success path | advisory `conformance_warnings` on the subtask result; doc/test/rule-fix commits prefixed `conformer:` on the same branch (DESIGN §9 *Post-work conformance*) |
| `integrator` | `prompts/integrator.md` | opus | on conflict during wave integration | resolved merge commit on `leerie/runs/<run-id>` |

Additionally, `pr_writer` (`prompts/pr_writer.md`, default sonnet) runs
at finalize when the run will push — it produces the PR title and body.
It is not in `WORKER_TYPES` but has a dedicated `--pr-writer-model` flag.

**Per-worker model defaults:** judgment workers (classifier, planner,
reconciler, plan_overlap_judge, provision, integrator) default to Opus;
the acting workers (implementer, conformer) default to Sonnet — their
job is concrete subtask execution where throughput matters more than
broad-context judgment. To revert to the
all-Sonnet pattern of earlier versions, set `LEERIE_MODEL=sonnet` or
pass `--model sonnet`. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §2
*Model selection* for the full precedence table.

See [`docs/DESIGN.md`](docs/DESIGN.md) §7 for the worker contract and
[`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §3 for the invocation
surface (flags, timeouts, schema enforcement).

## Walkthrough

For worked end-to-end examples — from invocation through clarification,
wave execution, run-branch review, and merge; and for chain orchestration
(submitting, monitoring, and cancelling a multi-run chain) — see
[`docs/USAGE.md`](docs/USAGE.md).

## Documentation

Every Leerie document is reachable from this README. Architecture and code
surface:

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture, constraints, phase
  flow, the evidence-gated loop, deterministic enforcement
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — code-surface
  reference (functions, caps, schemas, model / effort selection)
- [`docs/USAGE.md`](docs/USAGE.md) — worked end-to-end example
- [`docs/INSTALL.md`](docs/INSTALL.md) — per-OS container runtime setup
  (Colima on macOS, containerd + nerdctl on Linux), Fly.io prerequisites

Policy and process:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup, task-completion
  checklist, PR conventions (and pointer to [`CLAUDE.md`](CLAUDE.md), the
  repo-local guidance for Claude Code)
- [`SECURITY.md`](SECURITY.md) — threat model and vulnerability reporting
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant
- [`CHANGELOG.md`](CHANGELOG.md) — release notes

Post-run analysis skills (invoked via Claude Code, not the orchestrator
itself):

- [`skills/judge-llm-batch/SKILL.md`](skills/judge-llm-batch/SKILL.md) —
  score captured `claude -p` calls against a 3-dimensional accuracy rubric
- [`skills/llm-self-heal/SKILL.md`](skills/llm-self-heal/SKILL.md) —
  autonomous prompt-patch loop for failing call types

## Development

Tests:

```bash
pip install pytest    # only dev dependency
pytest tests/         # from the repo root
```

The suite covers the deterministic enforcement functions, including a
coupling test that the retry-policy markers match the live check-function
strings. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §10 for
the test layout. The worker invocation path is not unit-tested (a stub or
live `claude` binary would be needed; out of scope for the current suite).

## Files

| Path | What it is |
|------|------------|
| `orchestrator/leerie.py` | The orchestrator — all phases, waves, caps, retries |
| `leerie` | Executable bash launcher — runtime preflight + `nerdctl run` (or Fly Machine provisioning when `--runtime fly`) |
| `Dockerfile` | Container image recipe — Debian 13 + Node + pnpm + `claude` CLI + baked orchestrator source. Built locally on first run, tagged `leerie:<VERSION>` |
| `fly.toml` | Fly.io Machine configuration — app name, region, vm sizing (4 cpu / 8 GB), `min_machines_running=0` (no warm pool) |
| `prompts/classifier.md` | System prompt: classify task + surface intent questions |
| `prompts/planner.md` | System prompt: decompose one category into a subtask plan |
| `prompts/reconciler.md` | System prompt: reconcile cross-domain capability-tag drift between planner outputs |
| `prompts/provision.md` | System prompt: LLM-fallback install recipe synthesis when the deterministic lockfile table misses (DESIGN §6½) |
| `prompts/implementer.md` | System prompt: execute one subtask end to end |
| `prompts/conformer.md` | System prompt: post-work doc/test/lint conformance pass (advisory; DESIGN §9) |
| `prompts/integrator.md` | System prompt: resolve merge conflicts behaviorally |
| `prompts/judge.md` | System prompt: 3-dimensional accuracy rubric for the post-run judge skill |
| `prompts/patch_generator.md` | System prompt: minimal prompt-patch proposal for the post-run self-heal loop |
| `prompts/pr_writer.md` | System prompt: finalize-time PR title + body author (invoked by `phase_finalize` when the run will push) |
| `prompts/_clarification_filter.md` | Shared include (codebase→research→ask filter) inlined by `classifier.md` and `implementer.md` via `load_prompt`'s `{{include: …}}` expansion |
| `scripts/install.sh` | One-command `curl \| bash` installer (preflight → runtime preflight → clone → symlink → verify) |
| `scripts/runtime-install.sh` | Per-OS auto-install of the container runtime (Colima on macOS; containerd + nerdctl on Debian / Fedora / Arch). Sourced by `install.sh` and the launcher |
| `scripts/container-entry.sh` | Container PID 1 entrypoint — `cd /work` then either `exec sleep infinity` (no argv: remote/Fly path, the launcher exec's the orchestrator separately via `flyctl ssh console`) or `exec python3 /opt/leerie-image/orchestrator/leerie.py "$@"` (with argv: local nerdctl path) |
| `scripts/setup-run.sh` | Create per-run branch + worktree (`leerie/runs/<run-id>`) |
| `scripts/new-worktree.sh` | Create per-subtask branch + worktree off the run branch |
| `scripts/integrate.sh` | Merge a subtask branch into the run branch |
| `scripts/finalize.sh` | Verify the run branch is non-empty and ready to push. The working branch is never modified locally. The push + `gh pr create` step lives in the **host launcher** (bash + `jq`) and runs after `nerdctl run` exits cleanly, using the host's own auth state — see [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7 *Host-side finalize*. Skipped when `--no-push` is set. |
| `scripts/host-finalize.sh` | Host-side push + PR creation block. Sourced by three call sites: the local-runtime post-run code path in the launcher, `decide_teardown` in `scripts/remote/provision.sh` (Fly clean-exit auto-finalize), and `leerie --finalize <run-id>` (recovery fast-path). Provides `host_finalize <run-dir>`; uses `pr_title` / `pr_body` from `run.json` when the `pr_writer` worker populated them, otherwise falls back to a deterministic body. |
| `scripts/cleanup.sh` | Remove worktrees for one run (default `--run-id`) or all runs (`--all-runs`). State dir always preserved as audit. `--branches` also deletes the matching `leerie/runs/<id>` run branch *and* `leerie/subtasks/<id>/*` subtask branches. `--subtask-branches` deletes only the subtask branches and keeps `leerie/runs/<id>` (the post-finalize default — the run branch is the PR head). |
| `scripts/remote/_log.sh` | Shared `remote_log()` helper — timestamped, repo-tagged stderr lines. Sourced by every other `scripts/remote/*.sh` file so all Fly-mode output is uniformly labeled. |
| `scripts/remote/build-push.sh` | Build and push a self-contained leerie image to Fly.io's registry (source baked in at `/opt/leerie-image/`). Default mode is Fly's remote builder (no host Docker daemon required); `--local-build` / `LEERIE_LOCAL_BUILD=1` opts into the legacy nerdctl/docker path. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §0.5 *Registry publish path*. |
| `scripts/remote/provision.sh` | Fly Machine lifecycle helper (sourced by the launcher's `RUNTIME=fly` branch). Provides `provision_machine()` (create → wait-started → register `decide_teardown` trap), `stop_machine()`, `destroy_machine()`, and `decide_teardown()` (classifies `$LEERIE_REMOTE_EXIT_RC`; on clean exit runs `fetch_branch` BEFORE `destroy_machine` so no work is lost; on sync failure leaves the machine RUNNING with `sync_failed_at` written for user-driven recovery; on pause-worthy non-zero rc stops the machine). See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7 *Machine lifecycle*. |
| `scripts/remote/lib.sh` | Shared bash helpers sourced by `provision.sh`, `resume-machine.sh`, and `re-seed.sh`. Provides `update_run_json()` (atomic merge into the run sidecar) and `wait_for_started()` (poll Fly Machine status until ready). |
| `scripts/remote/resume-machine.sh` | Resume helper for paused remote runs. Reads `fly_machine_id` from the sidecar, runs `flyctl machine start`, waits for `started`, and clears `paused_at`/`pause_reason`. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7. |
| `scripts/remote/seed-auth.sh` | Worker auth + config seeding (sourced by the launcher after `provision_machine()` returns). Provides `seed_auth()`, which tar-pipes `~/.claude.json` + `~/.claude/` (with `.claude/local` excluded — duplicates the Dockerfile-installed claude CLI) to `/home/leerie/` via `flyctl ssh console -C "tar -xC ..."`, writes git identity to `/home/leerie/.gitconfig`, and pre-warms `claude --version` once so the orchestrator's preflight call hits warm caches. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7 *Worker auth + config seeding*. |
| `scripts/remote/seed-repo.sh` | Single-channel git-aware repo seeding helper (sourced by the launcher after `provision_machine()` succeeds). Provides `seed_repo()`: wipe `/work` contents (preserving the inode), then tar-pipe a git-aware payload — `git ls-files -z --cached --others --exclude-standard` (honors `.gitignore`) + `.git/` verbatim + the repo's local `.claude/` verbatim (force-included) − `.leerie/` (defensively excluded; run state lives outside the repo at `$LEERIE_STATE_HOST_DIR`, not in-repo) — to `/work` on the machine. No in-machine `git clone`; the host has the repo, and Fly machines deliberately receive no GitHub credentials. See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7 *Repo seeding*. |
| `scripts/remote/re-seed.sh` | Mid-run re-rsync helper (Phase 4). Wakes a paused machine, runs a safety check, and re-runs `seed_repo_dirty`. Invoked by `leerie --re-seed <run-id>` and the auto-re-seed step on `--resume --runtime fly`. |
| `scripts/remote/fetch-branch.sh` | Post-run stream-back helper (sourced by `decide_teardown` BEFORE `destroy_machine` on clean exit, and by the `leerie --finalize` fast-path). Provides `fetch_branch()`: discovers the completed run-id on the machine, probes whether the run branch actually exists (skipping the bundle for cleared-but-empty terminal-state runs), streams the `leerie/runs/<run-id>` git bundle to the host via `git fetch`, tars `.leerie/runs/<run-id>/` back, and strips the mechanism-flag `no_push` from the host-side run.json (the user's intent lives in `fly-machine.json` as `host_no_push`). See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §7 *Run branch stream-back*. |
| `commands/leerie.md` | Thin plugin skill — reachable as `/leerie` from Claude Code; relays the `--clarify` Q-and-A flow |
| `skills/judge-llm-batch/SKILL.md` | Post-run skill — scores captured `claude -p` calls against a 3-dimensional accuracy rubric (schema, factual grounding, hallucination-freeness) |
| `skills/llm-self-heal/SKILL.md` | Post-run skill — autonomous self-heal loop that proposes prompt patches against failing call types, replays under judge scoring, and reports the best-found patch |
| `chain/Dockerfile` | leerie-chain container image — Debian 13-slim + git + gh + flyctl + stdlib Python3. Leaner than the root Dockerfile: omits mise, claude-code, and build-essential (the chain app runs the HTTP API, not worker tasks). Entrypoint: `python3 -m chain`. |
| `chain/fly.toml` | Fly app config for the persistent leerie-chain HTTP service — `min_machines_running=1`, `[http_service]` on port 8080, `[mounts]` SQLite volume at `/data`. Provision once with `fly launch --config chain/fly.toml`. |
| `CLAUDE.md` | Repo-local guidance for Claude Code working in this codebase (the three-layer rule, mandatory requirements, code style) |
| `CONTRIBUTING.md` | Development setup, task-completion checklist, PR conventions |
| `SECURITY.md` | Threat model, supported versions, vulnerability reporting policy |
| `CODE_OF_CONDUCT.md` | Contributor Covenant Code of Conduct |
| `CHANGELOG.md` | Release notes (Keep a Changelog format, SemVer) |
| `docs/DESIGN.md` | Full design document and rationale (theory) |
| `docs/IMPLEMENTATION.md` | Current code-surface spec — functions, caps, schemas (mechanism) |
| `docs/INSTALL.md` | Per-OS container runtime setup and the Fly.io runtime prerequisites |
| `docs/USAGE.md` | End-to-end walkthrough of one Leerie run + chain orchestration example |

## Safety

Acting workers use `--dangerously-skip-permissions`. That is a real risk
surface — it is what makes the run unattended. It is bounded by **two
isolation layers**: (1) worktree isolation — each worker operates in its
own isolated git checkout, not your main working tree; (2) the container
the orchestrator runs in — PID-namespace + cgroups bound every worker
subprocess inside the per-run container (see
[`docs/DESIGN.md`](docs/DESIGN.md) §6 and [`SECURITY.md`](SECURITY.md)).
These bound the blast radius; they do not eliminate it. **Run on
repositories you trust and review the run branch (`leerie/runs/<run-id>`)
before relying on the result.** Push + PR at finalize is the natural
review surface; you can also pass `--no-push` to keep finalize fully
local.

The run writes only to `<state-root>/runs/<run-id>/` (where `<state-root>`
is the resolved state directory — default `$HOME/.leerie/<basename>/`,
overridable via `LEERIE_STATE_DIR` / `--state-dir` / `leerie.toml state_dir`;
never inside the repo itself) and to `leerie/runs/<run-id>` plus
`leerie/subtasks/<run-id>/<subtask-id>` branches. Phase 6 (unless
`--no-push`) pushes the run branch to `origin` and opens a PR against
your working branch — your working branch itself is never modified
locally. After a run, the run branch (`leerie/runs/<run-id>`) is kept
as an audit trail; per-subtask branches are auto-deleted at finalize,
but each worker's commits remain reachable from the run branch's
`--no-ff` merge graph (`git log leerie/runs/<run-id> --graph`). Remove
the run branch (and any leftover subtask branches) with
`scripts/cleanup.sh --run-id <id> --branches` (or `--all-runs --branches`
for an audit cleanup across every past run).

## Troubleshooting

- **`claude: command not found`** — Leerie shells out to the Claude Code
  CLI; install it from https://claude.ai/code and confirm with
  `claude --version`. There is no fallback path.

- **Exits with code 10** — not an error. Leerie needs clarification
  answers and you are running non-interactively. Read
  `<state-root>/pending-questions.json`, write the answers to
  `<state-root>/answers.json`, then `./leerie --resume --answers <state-root>/answers.json`
  (where `<state-root>` is the resolved state directory — default `$HOME/.leerie/<basename>/`).
  The plugin skill at `commands/leerie.md` handles this relay
  automatically when invoked as `/leerie`.

- **Run interrupted (Ctrl-C, SIGTERM, SIGHUP, CI cancel, terminal close, reboot)** —
  worktrees are torn down but state.json + branches are preserved.
  Resume with `./leerie --resume` (auto-picks if exactly one in-flight
  run) or `./leerie --resume <id>`. Run `leerie --list` to see
  what's in flight. The explicit "throw this away" command is
  `scripts/cleanup.sh --run-id <id> --branches` — Ctrl-C alone is
  always safely resumable.

- **Run hit the Claude Code subscription rate-limit** — leerie detects
  the session-limit message from `claude -p` and exits cleanly.
  Worktrees are torn down; state and branches are preserved. When the
  reset time can be parsed unambiguously, leerie sleeps until the reset
  window and auto-resumes itself. When it cannot (malformed time,
  unfamiliar timezone, or a future format change), leerie exits with
  code 75 and prints the manual resume command — re-run that command
  yourself once the rate-limit clears. Auto-resume passes only
  `--resume <id>`; CLI-only overrides (`--model`,
  `--max-workers`, etc.) on the original launch are *not* preserved
  across an auto-resume. Set those via env (`LEERIE_*`) or `leerie.toml`
  if you want them to survive — both channels are re-resolved on
  every `--resume`.

- **A subtask reports `blocked`** — the implementer hit something it
  cannot resolve and bailed before integration. Read the blocker reason in
  `<state-root>/runs/<run-id>/state.json` under `blocked[<subtask-id>]`, address the
  upstream cause, then resume. See [`docs/DESIGN.md`](docs/DESIGN.md) §8
  for the evidence-gated loop.

- **Worktree or branch conflicts on a re-run** — `scripts/cleanup.sh --run-id <id> --branches`
  removes that run's worktrees and deletes its branches so a fresh run
  with the same task starts clean. For a global sweep across every past
  run, use `--all-runs --branches`. Then re-invoke as normal.

- **Push or PR failed at finalize** — the run completed locally. Check
  `leerie --list` for the run's status (`push-failed` / `pr-failed`)
  and read `$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json` for the captured stderr.
  The error message at finalize names the exact retry command. Local
  commits are intact on the run branch.

## FAQ

**Do I need an Anthropic API key?**
No. Leerie runs entirely on the Claude Code CLI and your existing
subscription. The orchestrator shells out to `claude -p` workers; no API
key is read or sent.

**Can I run multiple Leerie instances in the same repository?**
Yes. Each invocation derives a unique `run_id` and namespaces all of its
state under `$LEERIE_STATE_HOST_DIR/runs/<run-id>/` and its branches under
`leerie/runs/<run-id>` (run branch) and `leerie/subtasks/<run-id>/<sid>`
(subtask branches) — so parallel runs in the same clone never collide.
Use `--list` to see what's in flight and `--resume <id>` to
resume a specific one.

**Does Leerie work outside a git repository?**
No. Per-subtask isolation is provided by `git worktree`; the worktree
mechanism is load-bearing, not optional.

**What if my project has no test runner?**
That's fine — running tests is advisory only (DESIGN §9 *Post-work
conformance*). When `_infer_build_lint_test()` finds no test command,
the conformance phase reports the test axis as not-applicable and
surfaces no warning. The subtask's terminal status is determined by the
implementer's confidence gate (DESIGN §8), not by whether tests ran.
See [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) §5 for the
conformance phase's advisory contract.

**Can I see what each worker did?**
Yes. Every worker commits to its own `leerie/subtasks/<run-id>/<subtask-id>`
branch during the run; at finalize, those branches are auto-deleted, but
the integrator merges each one into the run branch with `--no-ff`, so
every worker's commits remain reachable from `leerie/runs/<run-id>` as a
named merge bubble. `git log leerie/runs/<run-id> --graph` is your
per-worker audit trail. When you no longer need the run branch either,
`scripts/cleanup.sh --run-id <id> --branches` removes it (and any
leftover subtask branches); `--all-runs --branches` removes all of them.

**Why not use the Claude Code SDK or the in-session Agent tool?**
Two platform constraints make subprocess workers the right shape. See
[`docs/DESIGN.md`](docs/DESIGN.md) §2.

## Contributing

Contributions welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for
development setup, the task-completion checklist, and PR conventions.
Security issues: see [`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).

## Status

See [`CHANGELOG.md`](CHANGELOG.md) for the current release. The orchestrator's phase flow, wave scheduling, cross-domain dependency
resolution, and git worktree mechanics are all tested. First contact with a live
`claude -p` session is the remaining verification step. Limitations and planned
work are in [`docs/DESIGN.md`](docs/DESIGN.md).
