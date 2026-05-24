# Centella

Deterministic, headless task orchestrator for Claude Code. Give it one task; it
classifies it into up to eight categories, decomposes each into granular
subtasks, schedules them into dependency-ordered waves, and executes each in an
isolated git worktree under an evidence-gated implement/validate loop.

Runs entirely on the Claude Code CLI and your subscription. **No API key.**

## How it works

The orchestrator is a Python program — not an in-session agent. It shells out
to `claude -p` (headless mode) for each unit of LLM work. Each call is a
separate process, so there is no subagent nesting anywhere. Control flow lives
in real Python: `for` loops, `if` statements, counters. It cannot drift.

```
centella "<task>"
   ├─ Phase 1  Classify into 1..8 categories                    → 1 claude -p
   ├─ Phase 0  Clarify — intent-only questions, default zero
   ├─ Phase 2  Plan — one planner per category (parallel)        → N claude -p
   ├─ Phase 3  Schedule — global dependency graph → topo waves   (pure Python)
   ├─ Phase 4  Create centella/staging branch + worktree
   ├─ Phase 5  Per wave: implement (parallel, isolated worktrees) → claude -p each
   │           integrate into staging; validate staging
   └─ Phase 6  Merge staging → working branch; cleanup
```

For the full rationale — why the orchestrator is a script rather than a plugin
command, all architectural decisions, and the complete enforcement surface —
read [`docs/DESIGN.md`](docs/DESIGN.md).

## Requirements

- `claude` CLI on `PATH`, logged in interactively
- Python 3.10+
- A git repository with `user.email` and `user.name` configured
- A reasonably clean working tree

## Install and run

```bash
# From the root of the target git repository:
/path/to/centella/centella "Fix the login timeout bug and add a regression test"

# Resume an interrupted or budget-capped run:
/path/to/centella/centella --resume

# Skip the clarification phase entirely:
/path/to/centella/centella "task" --no-clarify

# Pre-supply clarification answers (JSON object):
# Keys are question ids from the classifier, plus "source_of_truth"
# set to "existing-patterns" or "researched-standards".
/path/to/centella/centella "task" --answers answers.json

# Override caps:
/path/to/centella/centella "task" --max-workers 60 --max-parallel 6

# Optional but recommended — lower the auto-compaction threshold
# for worker processes (default is 95%):
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70
```

Via the thin plugin skill from inside Claude Code:

```bash
claude --plugin-dir /path/to/centella
# then in the session:
/centella Fix the login timeout bug and add a regression test
```

## Files

| Path | What it is |
|------|------------|
| `orchestrator/centella.py` | The orchestrator — all phases, waves, caps, retries |
| `prompts/classifier.md` | System prompt: classify task + surface intent questions |
| `prompts/planner.md` | System prompt: decompose one category into a subtask plan |
| `prompts/implementer.md` | System prompt: execute one subtask end to end |
| `prompts/integrator.md` | System prompt: resolve merge conflicts behaviorally |
| `scripts/setup-staging.sh` | Create `centella/staging` branch + worktree |
| `scripts/new-worktree.sh` | Create per-subtask branch + worktree off staging |
| `scripts/integrate.sh` | Merge a subtask branch into staging |
| `scripts/finalize.sh` | Merge staging into the working branch |
| `scripts/cleanup.sh` | Remove worktrees; optionally delete `centella/*` branches |
| `centella` | Executable entry-point wrapper |
| `commands/centella.md` | Thin plugin skill — reachable as `/centella` from Claude Code |
| `docs/DESIGN.md` | Full design document and rationale |

## Safety

Acting workers use `--dangerously-skip-permissions`. That is a real risk
surface — it is what makes the run unattended. It is bounded by worktree
isolation (each worker operates in its own isolated checkout, not your main
working tree) but not eliminated. **Run on repositories you trust, ideally in
a container, and review the `centella/staging` branch before relying on the
result.**

The run writes only to `.centella/` (auto-excluded from git via
`.git/info/exclude`) and to `centella/*` branches until Phase 6, when it merges
into your working branch. After a run, `centella/*` branches are kept as an
audit trail. Remove them with `scripts/cleanup.sh --branches`.

## Status

v0.2.0. The orchestrator's phase flow, wave scheduling, cross-domain dependency
resolution, and git worktree mechanics are all tested. First contact with a live
`claude -p` session is the remaining verification step. Limitations and planned
work are in [`docs/DESIGN.md`](docs/DESIGN.md).
