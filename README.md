# Leerie

**Leerie** is a graph-engineered task driver for Claude Code. One prompt. Finished, committed, validated code. No steering mid-run, no polishing when it's done.

It classifies the task, decomposes it into subtasks wired into a dependency graph — nodes are subtasks, edges are `requires`/`provides` — then a deterministic Python scheduler topo-sorts that graph into waves, implements each wave in parallel isolated worktrees, validates the integrated result, and merges — beginning to end, unattended.

It runs entirely on the **Claude Code CLI and your existing subscription** — no Anthropic API key, no per-call billing.

**Why it finishes without you:** most AI "orchestrators" let the model pilot — decide what's next, declare when it's done, judge its own success. Leerie inverts that: **the model writes code, the program runs everything else.** The graph — its structure, its scheduling, its edges — is owned by ordinary Python, not by agents negotiating with each other; workers are headless, one-shot `claude -p` calls at each node, not a multi-agent conversation. Phases, scheduling, retries, caps, merge logic, and success-criteria enforcement are ordinary Python. Every worker output is JSON-schema-validated, and completion is gated by an independent adversarial verifier, not the implementer's self-report. See [`docs/DESIGN.md`](docs/DESIGN.md) for the full rationale.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/downloads/)
[![tests](https://github.com/enricai/leerie/actions/workflows/test.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/test.yml)
[![syntax](https://github.com/enricai/leerie/actions/workflows/syntax.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/syntax.yml)
[![shellcheck](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml/badge.svg)](https://github.com/enricai/leerie/actions/workflows/shellcheck.yml)
[![Version](https://img.shields.io/github/v/release/enricai/leerie?color=orange&label=version)](https://github.com/enricai/leerie/releases)

## How it works

The orchestrator is a Python program — not an in-session agent. It shells
out to `claude -p` for each unit of LLM work, one process per call.

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

Full phase-by-phase breakdown and every architectural decision: [`docs/DESIGN.md`](docs/DESIGN.md).

## Requirements

- `claude` CLI on `PATH`, logged in interactively (a `claude setup-token`
  OAuth token is recommended for long/unattended runs — see
  [`docs/DESIGN.md` §6 *Credential strategy*](docs/DESIGN.md))
- `git`, with a repo that has `user.email`/`user.name` configured and a
  reasonably clean working tree
- A container runtime (one-time setup, see *Install* below)
- `gh` CLI logged in, or `--no-push` to skip the finalize PR step

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

Auto-installs the container runtime per OS, clones leerie into `~/.leerie`,
and symlinks `leerie` onto `~/.local/bin`. For per-OS details, the rootless
path, or a manual/clone-and-run install, see [`docs/INSTALL.md`](docs/INSTALL.md).

**Inside Claude Code** (chat-based use):

```
/plugin marketplace add enricai/leerie
/plugin install leerie@enricai-leerie
```

## Quickstart

```bash
# From the root of the target git repository:
leerie "Fix the login timeout bug and add a regression test"

leerie resume   # resume an interrupted or budget-capped run
leerie list     # list in-flight and completed runs
leerie "task" --no-push   # skip the finalize push + PR
```

Or from inside Claude Code: `/leerie Fix the login timeout bug and add a regression test`.

For a worked end-to-end walkthrough, see [`docs/USAGE.md`](docs/USAGE.md).
For the complete CLI/env-var/`leerie.toml`/worker reference, see
[`docs/IMPLEMENTATION.md` §2½ *Configuration reference*](docs/IMPLEMENTATION.md).

## Documentation

- [`docs/DESIGN.md`](docs/DESIGN.md) — architecture, constraints, phase
  flow, the evidence-gated loop, deterministic enforcement
- [`docs/IMPLEMENTATION.md`](docs/IMPLEMENTATION.md) — code-surface
  reference (CLI flags, env vars, worker types, functions, caps, schemas);
  §1 covers repository layout
- [`docs/USAGE.md`](docs/USAGE.md) — worked end-to-end example
- [`docs/INSTALL.md`](docs/INSTALL.md) — per-OS container runtime setup,
  Fly.io and EC2 runtime prerequisites
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — development setup, task-completion
  checklist, PR conventions (see also [`CLAUDE.md`](CLAUDE.md))
- [`SECURITY.md`](SECURITY.md) — threat model and vulnerability reporting
- [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) — Contributor Covenant

Post-run analysis skills (invoked via Claude Code, not the orchestrator):

- [`skills/judge-llm-batch/SKILL.md`](skills/judge-llm-batch/SKILL.md) —
  score captured `claude -p` calls against a 3-dimensional accuracy rubric
- [`skills/llm-self-heal/SKILL.md`](skills/llm-self-heal/SKILL.md) —
  autonomous prompt-patch loop for failing call types

## Safety

Acting workers use `--dangerously-skip-permissions`, bounded by two
isolation layers: each worker's own git worktree, and the container the
orchestrator runs in (PID-namespace + cgroups). These bound the blast
radius; they do not eliminate it. **Run on repositories you trust and
review the run branch (`leerie/runs/<run-id>`) before relying on the
result.** See [`docs/DESIGN.md`](docs/DESIGN.md) §6 and
[`SECURITY.md`](SECURITY.md) for the full threat model.

## Troubleshooting

Resume mechanics, expired OAuth sessions, blocked subtasks, and failed
pushes are covered in [`docs/USAGE.md`](docs/USAGE.md) under "What happens
when something goes wrong". The two most common cases:

- **Run interrupted (Ctrl-C, SIGTERM, reboot, rate-limit)** — nothing is
  lost. `leerie resume` (auto-picks the most recent resumable run).
- **Exits with code 10** — not an error; leerie needs clarification
  answers non-interactively. Read `<state-root>/pending-questions.json`,
  write `<state-root>/answers.json`, then `leerie resume --answers <path>`.

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
