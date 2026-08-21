# Leerie

**Leerie** is an autonomous task driver for Claude Code. One prompt. Finished, committed, validated code. No steering mid-run, no polishing when it's done.

Most tools that call themselves autonomous still require you: to confirm a direction, catch a hallucination, or clean up the result before it's usable. Leerie doesn't. It classifies the task, decomposes it, implements each piece in parallel isolated worktrees, validates the integrated result, and merges — beginning to end, unattended.

It runs entirely on the **Claude Code CLI and your existing subscription** — no Anthropic API key, no per-call billing. If you have Claude Code installed and logged in, you have everything it needs.

**Why it actually finishes without you:** most AI "orchestrators" let the model pilot — decide what to do next, declare when it's done, judge whether it succeeded. That's where drift, hallucinated completion, and silent failures come from. Leerie inverts the relationship: **the model writes code, the program runs everything else.** Phases, wave scheduling, retries, caps, merge logic, and success-criteria enforcement are ordinary Python — real loops and conditionals that cannot drift. Every worker output is JSON-schema-validated before the orchestrator acts on it, and the load-bearing completion gate is an independent adversarial verifier, not the implementer's own self-report. See *How it works* below and [`docs/DESIGN.md`](docs/DESIGN.md) for the full rationale.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![tests](https://github.com/enricai/leerie/actions/workflows/test.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/test.yml)
[![syntax](https://github.com/enricai/leerie/actions/workflows/syntax.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/syntax.yml)
[![shellcheck](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml)
[![Version](https://img.shields.io/github/v/release/enricai/leerie?color=orange&label=version)](https://github.com/enricai/leerie/releases)

## How it works

The orchestrator is a Python program — not an in-session agent. It shells out
to `claude -p` (headless mode) for each unit of LLM work. Each call is a
separate process, so there is no subagent nesting anywhere.

```
leerie "<task>"
   ├─ Phase 1  Classify into 1..9 categories                    → 1 claude -p
   ├─ Phase 2  Plan — one planner per category (parallel)        → N×3 claude -p
   ├─ Phase 3  Schedule — global dependency graph → topo waves   (pure Python)
   ├─ Phase 4  Create leerie/runs/<run-id> branch + worktree
   ├─ Phase 5  Per wave: implement (parallel, isolated worktrees) → integrate
   │             → validate the integrated run branch
   └─ Phase 6  Push run branch; open PR against working branch; cleanup
```

For the full phase-by-phase breakdown, the evidence-gated confidence loop,
and every architectural decision, read [`docs/DESIGN.md`](docs/DESIGN.md).

## Requirements

- `claude` CLI on `PATH`, logged in interactively (a `claude setup-token`
  OAuth token is recommended for long/unattended runs — see
  [`docs/DESIGN.md` §6 *Credential strategy*](docs/DESIGN.md))
- `git`, with a repo that has `user.email`/`user.name` configured and a
  reasonably clean working tree
- A container runtime (one-time setup — see *Install* below). Leerie runs
  inside a container so Ctrl-C gives cleanup a hard kernel guarantee.
- `gh` CLI logged in, or pass `--no-push` to skip the finalize PR step

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

Auto-installs the container runtime per OS, clones leerie into `~/.leerie`,
and symlinks `leerie` onto `~/.local/bin`. For per-OS details, the rootless
path, manual runtime setup, or a clone-and-run install with no installer at
all, see [`docs/INSTALL.md`](docs/INSTALL.md).

**Inside Claude Code** (chat-based use):

```
/plugin marketplace add enricai/leerie
/plugin install leerie@enricai-leerie
```

## Quickstart

```bash
# From the root of the target git repository:
leerie "Fix the login timeout bug and add a regression test"

# Resume an interrupted or budget-capped run:
leerie resume

# List in-flight and completed runs in this repository:
leerie list

# Skip the default push + PR at finalize:
leerie "task" --no-push
```

Or from inside Claude Code, after `/plugin install leerie@enricai-leerie`:

```
/leerie Fix the login timeout bug and add a regression test
```

For a worked end-to-end walkthrough (invocation through clarification, wave
execution, run-branch review, merge, and multi-run chain orchestration), see
[`docs/USAGE.md`](docs/USAGE.md). For the complete inventory of every CLI
flag, environment variable, `leerie.toml` key, launcher verb, and the sixteen
`claude -p` worker types, see
[`docs/IMPLEMENTATION.md` §2½ *Configuration reference*](docs/IMPLEMENTATION.md).

## Documentation

Every Leerie document is reachable from this README. Architecture and code
surface:

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture, constraints, phase
  flow, the evidence-gated loop, deterministic enforcement
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — code-surface
  reference (CLI flags, env vars, worker types, functions, caps, schemas)
- [`docs/USAGE.md`](docs/USAGE.md) — worked end-to-end example
- [`docs/INSTALL.md`](docs/INSTALL.md) — per-OS container runtime setup,
  Fly.io and EC2 runtime prerequisites

Policy and process:

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup, task-completion
  checklist, PR conventions (and pointer to [`CLAUDE.md`](CLAUDE.md), the
  repo-local guidance for Claude Code)
- [`SECURITY.md`](SECURITY.md) — threat model and vulnerability reporting
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant

Post-run analysis skills (invoked via Claude Code, not the orchestrator
itself):

- [`skills/judge-llm-batch/SKILL.md`](skills/judge-llm-batch/SKILL.md) —
  score captured `claude -p` calls against a 3-dimensional accuracy rubric
- [`skills/llm-self-heal/SKILL.md`](skills/llm-self-heal/SKILL.md) —
  autonomous prompt-patch loop for failing call types

Repository layout (every file's role) is in
[`docs/IMPLEMENTATION.md` §1 *Repository layout*](docs/IMPLEMENTATION.md).

## Development

```bash
pip install -r requirements.txt   # runtime deps — the suite imports them
pip install pytest jsonschema     # pytest is the only dev dependency
pytest tests/                     # from the repo root
```

The suite covers the deterministic enforcement functions. The worker
invocation path itself is not unit-tested (a stub or live `claude` binary
would be needed). See [`docs/IMPLEMENTATION.md` §10](docs/IMPLEMENTATION.md)
for the test layout, and [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full
development setup and PR checklist.

## Safety

Acting workers use `--dangerously-skip-permissions`. That is a real risk
surface — it is what makes the run unattended. It is bounded by **two
isolation layers**: (1) worktree isolation — each worker operates in its
own isolated git checkout, not your main working tree; (2) the container
the orchestrator runs in — PID-namespace + cgroups bound every worker
subprocess (see [`docs/DESIGN.md`](docs/DESIGN.md) §6 and
[`SECURITY.md`](SECURITY.md)). These bound the blast radius; they do not
eliminate it. **Run on repositories you trust and review the run branch
(`leerie/runs/<run-id>`) before relying on the result.** Push + PR at
finalize is the natural review surface; `--no-push` keeps finalize fully
local. The run writes only to `<state-root>/runs/<run-id>/` (default
`$HOME/.leerie/<basename>/`, never inside the repo) and to
`leerie/runs/<run-id>` / `leerie/subtasks/<run-id>/<subtask-id>` branches.

## Troubleshooting

Common cases — resume mechanics after an interruption or rate-limit,
expired OAuth sessions, blocked subtasks, worktree/branch conflicts, and
failed pushes — along with their exact recovery commands, are covered in
[`docs/USAGE.md`](docs/USAGE.md) under "What happens when something goes
wrong". A quick pointer for the two most common ones:

- **Run interrupted (Ctrl-C, SIGTERM, reboot, rate-limit)** — nothing is
  lost. `leerie resume` (auto-picks the most recent resumable run) or
  `leerie resume <id>`. `leerie list` shows what's in flight.
- **Exits with code 10** — not an error; leerie needs clarification answers
  non-interactively. Read `<state-root>/pending-questions.json`, write
  `<state-root>/answers.json`, then `leerie resume --answers <path>`.

## Contributing

Contributions welcome. See [`CONTRIBUTING.md`](CONTRIBUTING.md) for
development setup, the task-completion checklist, and PR conventions.
Security issues: see [`SECURITY.md`](SECURITY.md).

## License

MIT — see [`LICENSE`](LICENSE).

## Status

See [GitHub Releases](https://github.com/enricai/leerie/releases) for the
current release. Limitations and planned work are in
[`docs/DESIGN.md`](docs/DESIGN.md).
