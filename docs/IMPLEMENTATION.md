# Centella — Implementation Reference

> **This document describes the current code, not the design.** It is true only
> against the present state of `orchestrator/centella.py`, the worker prompts,
> and the shell scripts. A change to the code that is not reflected here makes
> *this document* wrong — unlike `DESIGN.md`, which describes the architecture
> and stays correct across reimplementation. When this document and the code
> disagree, the code is authoritative. When this document and `DESIGN.md`
> disagree, `DESIGN.md` defines what *should* be true.
>
> Read `DESIGN.md` first for the *why*; this document is the *what* and *where*.

---

## 1. Repository layout

```
centella/
├── .claude-plugin/plugin.json     plugin manifest
├── centella                        executable entry-point wrapper (chmod +x)
├── orchestrator/centella.py        the orchestrator — all control flow (chmod +x)
├── prompts/
│   ├── classifier.md              Phase 1 worker system prompt
│   ├── planner.md                 Phase 2 worker system prompt
│   ├── implementer.md             Phase 5 implementer worker system prompt
│   └── integrator.md              conflict-resolution worker system prompt
├── scripts/
│   ├── setup-staging.sh           create staging branch + worktree (idempotent)
│   ├── new-worktree.sh            create/reuse a per-subtask worktree
│   ├── integrate.sh               merge a subtask branch into staging
│   ├── finalize.sh                merge staging into the working branch
│   └── cleanup.sh                 remove worktrees (and optionally branches)
├── commands/centella.md            thin plugin skill — launches the orchestrator
├── docs/DESIGN.md                 the theory (architecture and rationale)
└── docs/IMPLEMENTATION.md         this document
```

The share-sheet flattens directories; the tree above must be reassembled
exactly or Claude Code will not load the plugin. After reassembly:
`chmod +x centella orchestrator/centella.py scripts/*.sh`.

Maps to `DESIGN.md`: §3 (architecture / phases), §2 (why a program, not a skill).

---

## 2. Installation and usage

```bash
# From the root of the target git repository:
/path/to/centella/centella "Fix the login timeout bug and add a regression test"

# Resume an interrupted run:
/path/to/centella/centella --resume

# Skip clarification (caller guarantees the task is fully specified):
/path/to/centella/centella "task" --no-clarify

# Pre-supply clarification answers:
/path/to/centella/centella "task" --answers answers.json

# Override caps:
/path/to/centella/centella "task" --max-workers 60 --max-parallel 6

# Recommended backstop for worker auto-compaction
# (Claude Code CLI variable — not consumed by centella itself):
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70
```

Requirements: the `claude` CLI on `PATH` and logged in interactively (no API
key — subscription auth); Python 3.10+; a git repository with `user.email` and
`user.name` configured.

Via the plugin skill, from inside Claude Code:

```
claude --plugin-dir /path/to/centella
/centella <task>
```

The `--answers` file is a JSON object keyed by classifier-assigned question
`id`, plus `source_of_truth` set to `"existing-patterns"` or
`"researched-standards"` when a feature source-of-truth question was asked:

```json
{ "q1": "answer text", "source_of_truth": "existing-patterns" }
```

Maps to `DESIGN.md`: §11 (clarification procedure).

---

## 3. Worker invocation contract

Each worker is one `claude -p` headless process. Flags used:

| Flag | Purpose |
|------|---------|
| `-p` | non-interactive single-shot |
| `--output-format json` | returns an envelope object (cost, usage, `terminal_reason`, `structured_output`) |
| `--json-schema <inline>` | the payload schema; serialized inline as a JSON string — a file path is silently ignored (verified against Claude Code 2.1.143) |
| `--append-system-prompt` | injects the worker's role prompt from `prompts/*.md` |
| `--allowedTools` | tool allowlist; acting workers get the act-tool set, read-only workers a narrower set |
| `--max-turns` | per-worker turn cap (values in §6) |
| `--dangerously-skip-permissions` | acting workers only — suppresses all permission prompts for unattended file writes |

The validated payload is read from `structured_output` on the envelope. On a
missing or schema-invalid payload, `claude_p()` retries once with the violation
quoted into the prompt; a second failure raises `WorkerError`.

`WorkerError` handling by worker type:
- **implementer** — `run_implementer()` catches it, converts to an
  `incomplete-handoff` result; a fresh implementer continues from the checkpoint.
- **classifier, planner, integrator, validator** — not caught locally;
  propagates to `main()`, which aborts with state saved for `--resume`.

`claude_p()` logs a non-fatal warning when the envelope `terminal_reason` is not
`"completed"` (e.g. `"max_turns"`).

Maps to `DESIGN.md`: §7 (worker contract), §2 (CLI subprocess form).

---

## 4. Phase walkthrough (`centella.py`)

| Phase | Function(s) | What it does |
|-------|-------------|--------------|
| 1 Classify | `phase_classify` | one classifier worker → categories + questions |
| 0 Clarify | `gather_answers` | if questions and interactive: collect; non-interactive: write `pending-questions.json`, exit code 10; `--no-clarify` skips |
| 2 Plan | `phase_plan` | one planner worker per category, in a `ThreadPoolExecutor`; a worker exception surfaces when results are consumed and propagates to `main()` |
| 3 Schedule | `schedule`, `validate_plan` | merge plans, build the global DAG, Kahn topological sort into waves; cycle → `die()` |
| 4 Setup | `phase_execute` head → `setup-staging.sh` | create staging branch + worktree |
| 5 Execute | `phase_execute`, `settle_subtask`, `integrate_wave`, `validate_wave` | per wave: implementers in parallel, integrate, re-validate |
| 6 Finalize | `phase_finalize` → `finalize.sh`, `cleanup.sh` | merge staging into working branch; post-merge sanity checks |

`phase_classify` runs before `gather_answers` because the question set depends
on the classification.

Maps to `DESIGN.md`: §3.

---

## 5. Deterministic enforcement points

All in `centella.py`, in execution order. This is the concrete catalogue behind
`DESIGN.md` §12 ("prompts advisory, code enforces").

### Preflight (before any LLM work)
| Check | Catches |
|-------|---------|
| `git user.email` / `user.name` set | commits would fail silently without identity |
| working tree clean | dirty tree → ambiguous diffs, corrupt merge history |
| no stale `centella/*` branches | name collisions with this run |
| no stale worktrees | branch checkout failures |
| live `claude -p` smoke test | auth failure, version mismatch, broken `--json-schema` |

`--skip-smoke` bypasses the last check (used by the test harness).

### Plan validation — `validate_plan` (after planners, before scheduling)
| Check | Catches |
|-------|---------|
| no duplicate subtask ids | filesystem collisions in `.centella/subtasks/` |
| ids match domain prefix (`bugfix-`, `feat-`, `refactor-`, `perf-`, `test-`, `deps-`, `config-`, `docs-`) | cross-domain collisions, audit ambiguity |
| no `size: large` subtasks | planner violated the sizing constraint |
| no empty `success_criteria_seed` | implementer has no criteria starting point |
| every `depends_on` id exists | dangling edges silently dropped by the scheduler |
| every `requires` tag has a provider | unresolvable cross-domain dependency |

### Per-subtask checks — in `settle_subtask`, every worker result
| Check | Catches | On failure |
|-------|---------|-----------|
| `validate_result()` cross-field invariants | `complete` with empty/failing criteria; `handoff` with no checkpoint file; `blocked` with no blocker | **Terminal** |
| `validate_result()` criteria file exists | fabricated `criteria_results`, no real criteria file | **Terminal** |
| `check_branch_has_commits()` | `complete` claim, nothing committed | **Retryable** |
| dirty worktree check | uncommitted changes that vanish on integration | **Retryable** |
| `verify_criteria_lock()` — before every re-invocation | criteria file changed after the hash was stored | raises `WorkerError`, run aborts |
| `lock_criteria()` | stores the sha256 of the criteria file on first settled result | — |
| `check_diff_scope()` | `.centella/` `.git/` `.claude/` in the diff | **Terminal** (protected path); scope-volume warning is non-fatal |
| `validate_checkpoint()` — on `incomplete-handoff` | required checkpoint sections missing | returns `blocked` |

### Wave-level checks (after integration, before validation)
| Check | Catches |
|-------|---------|
| `check_criteria_files_exist()` | missing criteria files, before spending validation workers |
| test-runner short-circuit | a passing deterministic runner (pytest/npm/go/cargo/make) skips the LLM validator |
| `scan_conflict_markers()` | unresolved `<<<<<<<` markers in staging after integration |

### Post-integrator checks (after an integrator handles a conflict)
| Check | Catches |
|-------|---------|
| `check_merge_committed()` | integrator returned `resolved` but left the worktree mid-merge (`MERGE_HEAD` present) or with staged-uncommitted changes — **terminal**: merge aborted, run stops |
| `check_integrator_commit()` | integrator merge commit touched `.centella/` files — non-fatal warning, recorded to `state.json` |
| integrator status `design-conflict` / `failed` | unresolvable conflict — **terminal**: in-progress merge aborted, staging left clean at last good wave, diagnosis saved, run stops |

### Post-finalize checks
| Check | Catches |
|-------|---------|
| merge commit present in `git log --merges` | finalize merged to the wrong branch |
| `git diff centella/staging..HEAD` empty | merge silently dropped changes |

### Resume integrity — `validate_resume_state()`
On `--resume`: asserts `task` is present and non-empty; asserts `waves`,
`completed_waves`, `subtask_status` are well-formed *if present*. `waves` is
intentionally optional — a run interrupted before scheduling has none, and
`main()` handles that case with a clearer message. Rejects corrupt or
hand-edited state without rejecting a legitimately-early interruption.

### Concurrency safety
`State` uses a `threading.RLock` (reentrant — a caller holding the lock can
still call `save()`); `save()` writes to a temp file then `os.replace()` for
atomicity. Prevents partial-write corruption when parallel workers in a wave
update shared state.

---

## 6. Caps and their values

Defaults in `DEFAULT_CAPS` and the per-worker `claude_p` call sites.

### Code-enforced caps (the orchestrator counts these)
| Loop | Cap | On cap |
|------|-----|--------|
| handoff continuations per subtask | 3 | return `blocked`; fatal at wave boundary |
| corrective retries of a *retryable* failure per subtask | 1 | return `failed` |
| wave staging re-validation rounds | 5 | abort run, name failing subtasks |
| total worker invocations per run | 40 (`--max-workers`) | abort, state saved for `--resume` |
| concurrent workers within a wave | 4 (`--max-parallel`) | throughput throttle |
| turns per `claude -p` call | per worker (below) | worker stops; implementer → `incomplete-handoff` |
| per-worker wall-clock | 90 min | worker killed; implementer → `incomplete-handoff` |

`--max-turns` by worker: classifier 20, planner 40, validator 40, integrator
60, implementer 120. For the implementer, 120 turns and 90 minutes both apply —
whichever trips first.

### Worker-internal caps (prompt-governed — NOT counted by the orchestrator)
These iterate inside one worker; the orchestrator sees only the final result.
The real backstop is the worker's `--max-turns` above.

| Loop | Instructed limit | Instructed outcome |
|------|------------------|--------------------|
| evidence gate iterations (implementer) | 5 | return `blocked` |
| validate-against-criteria iterations (implementer) | 5 | return `failed` |
| fix / re-validate iterations (integrator) | 5 | return `failed` |

Maps to `DESIGN.md`: §13. The code-enforced / prompt-governed split there is
*the* point — do not present the second table as a code guarantee.

### The two-tier retry policy — `_retryable_failure(reason)`
One classifier function decides retryable vs. terminal. It substring-matches
the failure reason; the markers must stay in sync with the strings the check
functions actually emit — there is no test enforcing this, so the coupling is
a discipline that has to be held by hand. When adding a new retryable failure
mode, edit `_retryable_failure` and the check function in the same change.

| Failure | Tier | Marker / source |
|---------|------|-----------------|
| branch has no commits ahead of staging | Retryable | `"no commits ahead of staging"` from `check_branch_has_commits` |
| worktree left dirty | Retryable | `"uncommitted change"` from the dirty-worktree check |
| cross-field invariant violation | Terminal | `validate_result` |
| diff touched a protected path | Terminal | `check_diff_scope` |
| worker-level error (timeout, schema-invalid twice) | Terminal | `WorkerError` path |

`settle_subtask` routes every failure through `_retryable_failure` via the
`fail()` helper. Retryable consumes the retry cap; terminal ends the subtask on
first occurrence.

---

## 7. Git worktree mechanics (`scripts/*.sh`)

| Script | Behavior |
|--------|----------|
| `setup-staging.sh` | Creates `centella/staging` **only if absent** — never force-resets it (an existing branch carries completed waves; resetting it would destroy resume state). Records the working branch to `.centella/working-branch` on first run only. Adds the staging worktree if missing. Idempotent — safe on `--resume`. |
| `new-worktree.sh <id>` | Creates `centella/<id>` worktree branched off the current `centella/staging` tip; reuses an existing worktree/branch if present (resume after handoff). Prints the absolute worktree path. |
| `integrate.sh <id>` | From repo root, inside the staging worktree: `git merge --no-ff centella/<id>`. Exit 0 clean; exit 1 on conflict, leaving the worktree mid-merge for an integrator. |
| `finalize.sh` | Checks out the working branch (recorded by `setup-staging.sh`), merges `centella/staging` into it. On conflict: `git merge --abort`, restore the working branch clean, exit non-zero with manual-merge instructions; staging left intact. |
| `cleanup.sh [--branches]` | Removes all `.centella/worktrees/*`, prunes worktree metadata. Keeps `centella/*` branches as an audit trail unless `--branches` is passed. |

`centella/staging` is never reset once created — this is the invariant `--resume`
depends on. See `DESIGN.md` §6 ("staging is the resume contract").

Maps to `DESIGN.md`: §6.

---

## 8. Coordination directory layout (`.centella/`)

Created in the main repository (not in any worktree — worktrees are disposable).
Git-excluded via the repo's `info/exclude`.

```
.centella/
├── state.json              run state. Fields:
│                             task, started_at, finished_at
│                             waves, completed_waves, subtask_status
│                             criteria_locks, blocked
│                             worker_count, telemetry (calls, cost_usd,
│                               input/output tokens — printed at run end)
│                             categories, classifier_questions, answers,
│                               needs_source_of_truth (phase-0/1 persistence)
│                             test_runner (detected short-circuit command)
│                             integrator_failure, integrator_warnings,
│                               scope_warnings (non-fatal signal log)
├── working-branch          the branch finalize.sh returns to
├── plan.json               merged planner output
├── subtasks/<id>.json      per-subtask spec handed to each implementer
├── criteria/<id>.md        frozen success criteria, sha256-locked
├── checkpoints/<id>.md     handoff checkpoints (7-section schema)
├── worktrees/staging       the staging worktree
├── worktrees/<id>          per-subtask worktrees
├── pending-questions.json  written when clarification needs a non-interactive relay
└── answers.json            written by the plugin skill when relaying
                            clarification answers; passed back via --answers
```

The checkpoint schema — seven required sections, enforced by
`validate_checkpoint()`: *Frozen success criteria*, *Current status*, *Files
touched*, *Decisions made*, *Evidence gate status*, *Next action*, *Open
unknowns*.

Maps to `DESIGN.md`: §10 (handoff, coordination-artifact location), §9 (criteria
locking).

---

## 9. Structured-output schemas

`claude_p()` validates each worker's payload against a schema keyed by worker
type. Required fields, current shape:

- **classifier** — `categories` (array), `questions` (array of
  `{id, question, why_underivable}`), `source_of_truth_question` (bool).
- **planner** — `domain`, `subtasks` (array of `{id, title, intent,
  scope_note, files_likely_touched, depends_on, requires, provides,
  success_criteria_seed, size, investigation_notes}`), `notes_for_orchestrator`.
  `size` is `small` or `medium` — `large` is rejected by `validate_plan`.
- **implementer** — `subtask_id`, `status` (`complete` / `incomplete-handoff` /
  `blocked` / `failed`), `branch`, `criteria_results` (array of
  `{criterion, met, evidence}`), `confidence` (`{root_cause, solution, basis}`
  — keys fixed; `root_cause` is read as problem-understanding for non-bug
  domains), `checkpoint_path`, `proposed_criteria_revision`, `blocker`,
  `summary`.
- **integrator** — `incoming_subtask`, `status` (`resolved` / `design-conflict`
  / `failed`), `revalidation` (array of
  `{subtask_id, all_criteria_met, notes}`), `resolution_summary`, `diagnosis`.
- **validator** — `results` (array of `{subtask_id, all_criteria_met,
  failing}`).

Schemas are embedded as Python dicts in `centella.py` and serialized inline.

Maps to `DESIGN.md`: §7.

---

## 10. Verification status of the code

Mirrors `DESIGN.md` §15, at the code level.

**No test suite exists in the repository.** The deterministic surface is
*constructed* — every cap, check, and state transition lives in real Python
control flow, not in a worker prompt — but it has not been exercised by
automated tests. Manual end-to-end runs against a stub `claude` binary were
used during development; the shell scripts were exercised against real
repositories the same way. None of that is captured as a re-runnable test,
so a future change to the orchestrator has no regression net.

The worker behavior is also unverified against a live `claude -p`. The flag
contract in §3 is from CLI documentation, not from observed runs.

First real step: one run on a throwaway repo with a small, fully-specified
task. If a regression net is wanted before that, the place to start is a
pytest suite covering `_retryable_failure` (the marker / check-string
coupling), `check_merge_committed`, `validate_result`, and `validate_plan` —
these are the deterministic enforcement points with the smallest input set
and the highest cost if they silently break.
