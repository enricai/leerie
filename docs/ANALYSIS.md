# Leerie — Deterministic Code Analysis (Compiler Front-End Lens)

> **What this is.** A mechanically-extracted, reproducible analysis of the
> leerie codebase. Every count, name, and table below was produced by
> *parsing* the source (Python `ast` + `tokenize`), not by reading it
> impressionistically. The analysis treats the code the way a compiler treats
> source text — preprocess → lex → parse/AST → semantic graph — so the facts
> are deterministic: same input, same output.
>
> **Provenance.** Generated 2026-06-18 against commit `4a7785d` (release
> 0.7.2). The primary subject, `orchestrator/leerie.py`, is pinned by content
> hash `sha256:e7175fa9a52dd03d08c911511201d580a49e0903e626de347e318b4ad050574f`.
>
> **Regenerate.** `python3 docs/tools/leerie_extract.py orchestrator/leerie.py`.
> If the headline numbers in §1–§2 differ from a fresh run, this document is
> stale, not the code.
>
> **Status — NOT canonical.** Per `CLAUDE.md`, the canonical chain is
> `DESIGN.md` → `IMPLEMENTATION.md` → code. This file is a *derived fourth
> view*: it explains the code through a structural lens and cross-references
> the canon. If it disagrees with the code, the code wins; if it disagrees
> with the spec, fix this file.

---

## 0. The two-compiler thesis (how to read this whole document)

There are two compilers in play, and keeping them apart is the key to a
correct deep perspective on this project.

- **Compiler A — the analysis tool.** `docs/tools/leerie_extract.py` is a real
  front end run *over* leerie's source to produce the deterministic facts in
  §1–§2. When this document says "888 `If` nodes" or "fan-in of `claude_p` is
  13," that is Compiler A's output, not prose.

- **Compiler B — leerie itself.** Leerie *is* a compiler. Its source language
  is one natural-language task description; its object code is an integrated
  git branch plus a pull request. Its module docstring is literally *"Leerie —
  deterministic task orchestrator for Claude Code."* The orchestrator lowers a
  task through stages that map one-to-one onto a compiler pipeline:

  ```
  task text ──▶ classify ──▶ plan ──▶ reconcile ──▶ schedule ──▶ execute ──▶ integrate ──▶ conform ──▶ finalize ──▶ PR
              (lexing)     (parsing)  (name res.)   (linearize)  (codegen)   (linking)    (lint)       (emit)
  ```

  The non-obvious, load-bearing idea (DESIGN §3, §12) is the division of
  labor: **judgment is delegated to LLM "workers"; everything checkable
  mechanically is done by the orchestrator in real Python.** In compiler
  terms, the LLM workers are the creative passes (they *write* the code) and
  the orchestrator is the type checker, linker, and driver that refuses to
  trust them. "Prompts are advisory; code enforces."

§1–§2 are Compiler A's report on the static artifact. §3 onward reads that
artifact as Compiler B and explains what it *does*.

---

## 1. Compiler A — the method that produced this document

Four stages, each emitting deterministic facts.

### 1.1 Stage P — Preprocess (the input surface)

The repository's tracked files are the "translation units." Counted from
`git ls-files`, the surface partitions cleanly by role:

| Layer | Files | Largest |
|---|---|---|
| Orchestrator (all control flow) | `orchestrator/leerie.py` | **15,011 lines** |
| Launcher (portable bash) | `leerie` | 3,012 lines |
| Canonical docs | `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`, `INSTALL`, `USAGE` | 3,052 / 4,743 |
| Worker prompts | `prompts/*.md` (12 files) | `reconciler.md` 541 |
| Worktree mechanics (bash) | `scripts/*.sh` | — |
| Remote/Fly mechanics (bash) | `scripts/remote/*.sh` (11 files) | `seed-repo.sh` 850 |
| Chain service (Python, stdlib) | `chain/*.py` (8 modules) | `server.py` 492 |
| Tests | `tests/test_*.py` (≈155 files) | `test_reconciler_cycle_gate.py` 2,739 |

The single most important preprocessing fact: **one file, `leerie.py`, holds
all orchestrator control flow** (a deliberate design choice — readable
top-to-bottom in one sitting). That is why a structural extractor pays off so
much here; almost the entire "semantics of leerie" lives in one parseable AST.

### 1.2 Stage L — Lex (the token census)

`tokenize` over `leerie.py`: **80,841 tokens**.

| Token class | Count | Reading |
|---|---|---|
| `OP` | 29,411 | operator-dense (subscripts, calls, dict literals) |
| `NAME` | 23,568 | identifiers + keywords |
| `STRING` | 4,881 | many prompt fragments and messages |
| `COMMENT` | 2,704 | **155,497 comment chars ≈ 22.3% of bytes** — heavily annotated "why" |
| `FSTRING_START` | 930 | 930 f-strings (dynamic prompt/message assembly) |
| `NUMBER` | 549 | caps, timeouts, byte budgets |

Keyword frequency exposes the dominant idiom — **branching and guarding**:

```
if 1008   return 493   for 487   not 425   await 151   async 81
try 112   except 122   raise 54   with 18
```

`if` appears 1,008 times. This is the lexical signature of the "code enforces"
principle: the file is mostly conditionals that validate worker output and
gate state transitions. The `async`/`await` counts (81 / 151) mark it as an
asyncio program (parallel worker waves).

The lexer also harvests the project's literal **vocabulary** from string
tokens: **34 distinct `LEERIE_*` environment-variable names** and the exit-code
symbols `EXIT_NEEDS_ANSWERS`, `EXIT_BUDGET_INFEASIBLE`, `EXIT_LOCKED`
(see §2.2).

### 1.3 Stage S — Parse / AST (the declared surface)

`ast.parse` yields **60,834 AST nodes**. The node histogram is itself a
fingerprint of the architecture:

| Node | Count | Meaning |
|---|---|---|
| `If` | 888 | enforcement/guard density |
| `Compare` | 621 | predicate-heavy validation |
| `BoolOp` | 451 | compound conditions |
| `Try` / `ExceptHandler` | 112 / 122 | defensive subprocess + IO handling |
| `Call` | 3,592 | — |
| `FunctionDef` / `AsyncFunctionDef` | 219 / 73 | 292 `def`s total |
| `Await` | 151 | concurrency points |
| `Raise` | 54 | explicit failure surfacing |

Declared module surface:

- **231 module-level functions**
- **8 classes** (one core, `State`; the rest are small — see §2.5)
- **142 module-level constants**
- **21 import statements** (see §2.1)

### 1.4 Determinism contract

Compiler A is pure stdlib, reads only, and is order-stable. The committed
tool was re-run during this analysis and reproduced the headline numbers
byte-for-byte (`sha256` equal, 15011 / 80841 / 231 / 8 / 142). That is the
operational meaning of "deterministic documentation": the report is a
*function* of the source, and the function is checked into the repo.

---

## 2. The static artifact — `orchestrator/leerie.py`

### 2.1 Module identity and dependency closure

Module docstring: **"Leerie — deterministic task orchestrator for Claude Code."**

The import list proves the stdlib-preferred claim from `CLAUDE.md`. Of 21
imports, **exactly one is third-party**:

```
stdlib: __future__.annotations  argparse asyncio contextlib copy fcntl json os re shutil signal
        subprocess sys time uuid  +  collections.deque  collections.abc
        datetime  pathlib  zoneinfo
3rd-party: tenacity  (AsyncRetrying, RetryError, retry_if_result,
                       stop_after_delay, wait_exponential_jitter, ...)
```

`tenacity` exists for exactly one job: exponential-backoff retry of transient
`claude -p` envelope failures (auth/rate-limit). Everything else — async
orchestration (`asyncio`), inter-process locking (`fcntl`), subtree signalling
(`signal`/`subprocess`), atomic persistence (`os`/`json`/`pathlib`) — is
stdlib. The dependency surface is deliberately tiny so the orchestrator is
auditable.

### 2.2 The lexical vocabulary — leerie's "reserved words"

These module constants are the closed sets the rest of the program switches on.
They are the type universe of Compiler B.

**Worker roles** — `WORKER_TYPES` (8), the call-type partition (DESIGN §14):
```
classifier  planner  reconciler  plan_overlap_judge  provision
implementer  integrator  conformer
```
Three more worker roles exist outside `WORKER_TYPES` (finalize/post-run):
`pr_writer`, `judge`, `patch_generator`.

**Task categories** — `CATEGORIES` (9) with `CATEGORY_ABBREV` id-prefixes:
```
feature-implementation→feat   bug-fixing→bugfix   refactoring→refactor
performance-optimization→perf testing→test        dependency-migration→deps
configuration-build→config    infrastructure→infra documentation→docs
```

**Enumerated knobs** (value sets + defaults):

| Constant | Values | Default |
|---|---|---|
| `SOURCE_OF_TRUTH_VALUES` | `codebase` `research` `both` | `both` |
| `RUNTIME_VALUES` | `local` `fly` | `local` |
| `MODEL_VALUES` | `sonnet` `opus` `haiku` | `opus` (global) |
| `EFFORT_VALUES` | `low` `medium` `high` `xhigh` `max` | `None` |
| `VERBOSITY_VALUES` | `quiet` `normal` `stream` `debug` | `stream` |
| `_TERMINAL_STATUSES` | `complete` `failed` `blocked` | — |

**Per-worker model/effort defaults** (extracted literals):
- `MODEL_DEFAULT_PER_WORKER = {implementer, conformer, judge, heal, pr_writer → sonnet}` (all others → `opus`).
- `EFFORT_DEFAULT_PER_WORKER = {classifier, planner, reconciler, plan_overlap_judge, provision, integrator, pr_writer → high}` (acting/post-run workers unset).

This is the mechanical truth behind "judgment workers default to opus/high;
acting workers default to sonnet/unset."

**Run lifecycle** — `RUN_STATUSES` (**11**, not 10):
```
seed-failed  corrupt-sidecar  in-progress  done  done-pushed-no-pr
done-pushed-pr  push-failed  pr-failed  paused  killed  sync-failed
```

**Exit codes** (structured, non-error exits use `sys.exit`, not `die`):

| Constant | Value | Meaning |
|---|---|---|
| `EXIT_NEEDS_ANSWERS` | 10 | non-TTY clarification deferred → `pending-questions.json` |
| `EXIT_BUDGET_INFEASIBLE` | 11 | planner produced more subtasks than the budget fits |
| `EXIT_LOCKED` | 75 | another orchestrator holds the run-dir flock |

**Persistence schema** — `STATE_FIELDS` (33 keys) is the canonical shape of
`state.json` (coupling-tested). **Tool grants** encode the §12 read-only
enforcement directly as constants:
```
_READ_BASE   = "Read,Grep,Glob,WebSearch,WebFetch"
INSPECT_TOOLS= _READ_BASE + read-only Bash(ls/find/cat/grep/git log/...)
ACT_TOOLS    = _READ_BASE + "Bash,Write,Edit"
_PROTECTED_PREFIXES        = (".leerie/", ".git/")
_CLAUDE_DELIVERABLE_PREFIXES = (".claude/agents/", ".claude/commands/", ".claude/skills/")
```
Judgment workers are launched with `INSPECT_TOOLS` (cannot mutate); acting
workers get `ACT_TOOLS` but are fenced from the protected prefixes. The
permission boundary is a Python constant, not a prompt request.

Also pinned: `MIN_CLAUDE_CLI = (2, 1, 22)` — the first `claude` CLI with
working `--json-schema` in `-p` mode (enforced at preflight).

### 2.3 The cap table — `DEFAULT_CAPS`

Every bounded loop has a real counter here (DESIGN §13). Extracted values:

| Cap | Default | Bounds |
|---|---|---|
| `max_total_workers` | 200 | total worker invocations per run |
| `max_parallel` | 5 | concurrent workers per wave |
| `subtask_continuations` | 3 | handoffs + clarifications per subtask (shared) |
| `failed_retries` | 1 | retries of a retryable failure |
| `conformance_rounds` | 3 | conformer iterations |
| `judgment_check_rounds` | 3 | CRITIC rounds for judgment workers |
| `planner_check_rounds` | 3 | CRITIC rounds for planners |
| `implementer_confidence_retries` | 2 | implementer confidence-gate retries |
| `planner_samples` | 3 | independent planner samples per domain |
| `worker_timeout_sec` | 5400 | 90-min hard worker timeout |
| `worker_idle_warn_sec` | 300 | idle-warning threshold |
| `confidence_rounds` | 8 | worker-internal evidence-gate rounds |
| `worker_memory_max_bytes` | `None` | auto-derived from `/proc/meminfo` |
| `worker_pids_max` | 256 | per-worker cgroup pids.max |
| `auth_retry_max_sec` | 300 | tenacity auth/quota backoff ceiling |
| `subtask_call_estimate` | 2.5 | est. worker calls per subtask (budget preflight) |
| `budget_safety_margin` | 1.15 | budget-preflight slack factor |

The last two feed `check_budget_feasibility` (EXIT 11): when the estimated
worker count (≈ `subtasks × subtask_call_estimate × budget_safety_margin`)
exceeds `max_total_workers`, it fails fast at plan time with a recommended
`--max-workers`.

### 2.4 The type system on the wire — `SCHEMAS`

Every worker returns JSON validated against a JSON-Schema passed via
`--json-schema`. This is Compiler B's type system at process boundaries: a
worker physically cannot return a shape the orchestrator does not expect
without triggering one corrective retry then a hard `WorkerError`. The
`SCHEMAS` dict has **11** entries; required fields (mechanically extracted):

| Schema | `required` fields | `additionalProperties` |
|---|---|---|
| `classifier` | `categories`, `confidence` | open |
| `planner` | `domain`, `subtasks`, `status`, `confidence` | open |
| `reconciler` | 8 action arrays + `confidence` | open |
| `implementer` | `subtask_id`, `status`, `confidence` | open |
| `integrator` | `incoming_subtask`, `status`, `confidence` | open |
| `conformer` | `subtask_id`, `rules_files_read`, `rule_violations_fixed/residual`, `docs_updates`, `tests_updates`, `build`, `lint`, `tests`, `summary`, `confidence` | open |
| `plan_overlap_judge` | `collisions`, `confidence` | **closed** |
| `provision` | `recipe`, `confidence` | open |
| `judge` | `passed`, `dimensions`, `rationale`, `suggested_fixes` | open |
| `patch_generator` | `anchor`, `replacement` | open |
| `pr_writer` | `title`, `body`, `used_template` | open |

The 8 reconciler arrays (`renames`, `added_provides`, `added_subtasks`,
`conditional_drops`, `dropped_requires`, `dependency_edges`, `merged_subtasks`,
`unresolvable`) are the full vocabulary of cross-domain plan repair (§3.4).
Note every core worker carries `confidence` — the §8 evidence-gate object.

### 2.5 The object model — 8 classes

Leerie is "functional first"; `State` is the deliberate stateful exception.

| Class | Base | Span | Role |
|---|---|---|---|
| `State` | — | 130 ln | run state + atomic persistence + per-dir flock (§4.1) |
| `HealState` | — | 46 ln | per-`call_type` heal-loop state |
| `_DescendantTracker` | — | 86 ln | background PID poller for subtree kill (§4.3) |
| `_ReplayState` | — | 29 ln | no-write `State`-alike for telemetry replay |
| `StateLockedError` | `Exception` | — | raised when the run-dir flock is held → `EXIT_LOCKED` |
| `WorkerError` | `RuntimeError` | — | schema-invalid / dishonest worker |
| `RateLimitedExit` | `BaseException` | — | `claude` subscription rate-limit (carries reset time) |
| `InterruptedBySignal` | `BaseException` | — | raised by SIGTERM/SIGHUP handlers |

The two `BaseException` subclasses are intentional: they bypass normal
`except Exception` handlers so signal/rate-limit unwinding cannot be swallowed
by routine error handling.

`State` methods: `__init__` → `_acquire_lock` (`fcntl.flock(LOCK_EX|LOCK_NB)`
on the run *directory*), `save` (temp-file write + `os.replace` = atomic),
`load`, `bump_workers` (raises `WorkerError` past `max_total_workers` — the
runtime budget backstop), `add_telemetry`, `release_lock`, `__del__`.

### 2.6 The function surface — 231 functions, grouped

Compiler A buckets the names by prefix; the buckets *are* the subsystem map:

| Prefix | n | What it is |
|---|---|---|
| `resolve_*` | 30 | config resolution (CLI > env > toml > default), one per knob |
| `phase_*` | 9 | the pipeline stages (§3) |
| `check_*` | 14 | **deterministic enforcement gates** (return issue lists) |
| `validate_*` | 6 | structural output validators (`die` on violation) |
| `run_*` | 8 | subprocess + worker drivers (`run_proc`, `run_script`, `run_streaming`, `run_implementer`, `run_conformer`, …) |
| `heal_*`, `judge_capture`, `phase_judge`, `replay_capture`, `request_patch` | — | self-improvement loop (§6) |
| `_cgroup_*` | 4 | cgroup-v2 memory containment |
| `_tarjan_sccs`, `_build_predecessor_graph`, `_attribute_cycle_edges`, `_shared_files_in_scc` | — | the scheduler's graph algorithms |
| `_DescendantTracker`, `_enumerate_descendants`, `_terminate_proc_tree`, `_signal_pids` | — | worker subtree termination |
| `_format_*` | 12 | prompt/diagnostic rendering |

The 14 `check_*` functions are the heart of the "code enforces" principle:
`check_branch_has_commits`, `check_budget_feasibility`, `check_classifier_output`,
`check_convergence`, `check_diff_scope`, `check_implementer_output`,
`check_integrator_commit`, `check_integrator_output`, `check_merge_committed`,
`check_overlap_judge_output`, `check_planner_output`, `check_provision_output`,
`check_reconciler_output`, `check_task_file_coverage`.

### 2.7 The semantic graph — the call graph

**Fan-in (most-depended-on primitives)** — the leaf utilities every pass calls:

```
log 41   die 33   run_proc 17   claude_p 13   load_prompt 12   now 11
_read_toml_key 10   compute_run_branch 8   _resolve_positive_int_pref 8
_confidence_issues 6   _run_checked_loop 6
```

`log`/`die` are universal. `claude_p` (fan-in 13) is the single chokepoint for
*all* LLM work — every worker, judge, and heal call funnels through it (§4.2).
`load_prompt` (fan-in 12) is the prompt preprocessor (§5.1). `_run_checked_loop`
(fan-in 6) is the CRITIC pattern reused by every judgment phase.

**Fan-out (the drivers)** — functions that orchestrate many others:

```
main 40   _run_phases 27   phase_reconcile 21   run_final_conformance 16
settle_subtask 16   _run_conformance_phase 14   phase_provision 13
phase_plan 11   phase_overlap_judge 11   integrate_wave 11
```

`main` is the dispatcher; `_run_phases` is the pipeline body; `phase_reconcile`
(fan-out 21) is the most complex single stage — it is the "semantic analysis"
pass and it shows (§3.4).

**The pipeline edge** (who calls `phase_*`), extracted exactly:
- `_run_phases` → `phase_classify`, `phase_provision`, `phase_plan`,
  `phase_reconcile`, `phase_overlap_judge`, `phase_execute`, `phase_finalize`
- `main` → `phase_judge`, `phase_heal` (separate post-run sub-commands)

So the normal compile is `main → orchestrate → _run_phases → [7 phases]`, and
`phase_judge`/`phase_heal` are offline tools over a finished run's telemetry.

---

## 3. Compiler B — leerie as a task→PR compiler

### 3.1 The pipeline as compiler stages

Mapping the verified `phase_*` set onto a compiler, with the LLM worker (if
any) and the deterministic gate that follows it:

| # | Phase (fn) | Compiler analog | Worker (judgment) | Deterministic gate (code) |
|---|---|---|---|---|
| 0 | `preflight` | environment/sanity check | — | CLI version `(2,1,22)`, git, auth |
| 1 | `phase_classify` | **lexing** — task → category tokens | classifier (opus) | `check_classifier_output`, same-work risk |
| 1½ | `phase_provision` | toolchain resolution | provision (fallback only) | `validate_provision_recipe`, argv-allowlist |
| 2 | `phase_plan` | **parsing** — domain → subtask AST | planner ×N (opus, 3 samples) | `validate_plan`, `check_planner_output`, coverage |
| 2½ | `phase_reconcile` | **name resolution** — bridge tag vocab | reconciler (opus, conditional) | `_tarjan_sccs` acyclicity gate |
| 2¾ | `phase_overlap_judge` | **conflict detection** | plan_overlap_judge (conditional) | `_apply_overlap_collisions` + `_tarjan_sccs` |
| 3 | `schedule` | **linearization** — DAG → waves | — (pure) | topological sort (Kahn) |
| 4 | (`setup-run.sh`) | allocate output buffer | — | run branch "create-if-absent, never reset" |
| 5 | `phase_execute` | **code generation** (per wave) | implementer ×N (sonnet) | `check_diff_scope`, `settle_subtask` |
| 5a | `integrate_wave` | **linking** — merge subtask branches | integrator (on conflict) | `scan_conflict_markers`, `check_merge_committed` |
| 5b | `run_conformer` | **lint/peephole** | conformer (sonnet) | protected-path re-check (advisory) |
| 6 | `phase_finalize` | **emit object code** | pr_writer (sonnet) | `finalize.sh` verify + push/PR (host) |

Visual control flow (verified by reading the `_run_phases` source body,
`orchestrator/leerie.py:14130-14219`; a static call list does not encode
order, so this was confirmed line-by-line, not inferred):

```
preflight
   │
   ▼
phase_classify ──▶ phase_provision ──▶ gather_answers?   (provision precedes clarify; both skipped on --resume)
   │
   ▼
phase_plan (×planner_samples → select best)
   │
   ▼
phase_reconcile ──[unresolved requires]──▶ reconciler ──▶ _tarjan_sccs (acyclic?) ──[cycle]──▶ retry/die
   │
   ▼
detect_no_work? ──[every planner cleared its gate + plan empty]──▶ _finish_no_work_run ──▶ done (exit 0)
   │ (work remains)
   ▼
phase_overlap_judge ──[>1 planner]──▶ overlap judge ──▶ apply collisions ──▶ _tarjan_sccs
   │
   ▼
schedule (topo-sort → waves[]) ──▶ check_budget_feasibility (EXIT 11?) ──▶ validate_plan ──▶ write_plan
   │
   ▼
phase_execute   for wave in waves:   implementers ‖ ... ──▶ integrate_wave ──▶ conform ──▶ settle_subtask
   │
   ▼
run_final_conformance ──▶ phase_finalize (verify ──▶ push ──▶ gh pr create)
```

### 3.2 Front end

**Classify (lexing).** `phase_classify`'s extracted body is
`load_prompt → _run_checked_loop(claude_p, check_classifier_output)`. The task
text is tokenized into 1..9 of the nine `CATEGORIES`. Classification precedes
clarification because *what to ask* depends on *what kind of task it is*
(DESIGN §3).

**Provision (toolchain resolution).** `phase_provision` is the model of
"top-down by determinism": its ordered calls are
`synth_mise_go_override → detect_recipe_from_lockfiles → run_setup_hook →
run_mise_install → [fallback] gather_provision_fixtures → load_prompt →
claude_p → check_provision_output`. The LLM `provision` worker fires *only*
when the deterministic lockfile table returns empty. This is the single §12
carve-out in the live compile (DESIGN §6½): the only place in the task→PR
pipeline where an LLM-generated artifact is rendered into a later prompt (the
offline heal loop in §6 does too, but post-run) — contained by a frozen
argv-allowlist
(`_PROVISION_ARGV0_ALLOW`: pnpm/npm/yarn/pip/uv/go/cargo/...) and a
shell-metachar denylist.

**Plan (parsing).** `phase_plan` runs `planner_samples` (default 3)
independent planner workers per matched category in parallel
(`gather_or_cancel`), then `_select_best_planner_sample` picks mechanically
(fewest issues, most subtasks — avoids LLM self-bias), then
`_run_checked_loop(check_planner_output)`, then `check_task_file_coverage`.
Output is the subtask AST: each subtask is a node with `provides`/`requires`/
`depends_on`/`files_likely_touched`/`size`.

### 3.3 Back end

**Schedule (linearization).** Pure graph computation, no worker: merge all
plans, match `requires`↔`provides` into a global DAG, topologically sort into
sequential waves. The scheduler "never has to trust a model's ordering"
(DESIGN §3) — the non-deterministic pass produced data, the deterministic pass
consumes it.

**Execute (codegen) + Integrate (linking).** `phase_execute` per wave:
`run_script` (new worktree) → parallel implementers (`gather_or_cancel`) →
`integrate_wave` → `scan_conflict_markers` → `settle_subtask`. Each implementer
runs in its own git worktree on branch `leerie/subtasks/<run-id>/<sid>` off the
run branch, so a wave's workers cannot clobber each other. Integration is
`git merge --no-ff` (not cherry-pick — recorded ancestry gives a real
three-way base and an audit trail). On conflict, an integrator worker resolves
*behaviorally* (preserving each side's intent), and its `resolved` claim is
verified by `check_merge_committed`/`check_integrator_commit`.

**Conform (lint) + Finalize (emit).** `run_conformer` brings each change into
good standing with repo rules/tests/docs — advisory only (bounded by
`conformance_rounds=3`; a failure never escalates). `phase_finalize`'s body:
`push_will_happen → _write_run_json → run_script(finalize.sh verify) →
_compose_pr_via_llm`. The actual push + `gh pr create` happen on the *host*
after the container exits (`scripts/host-finalize.sh`), because the container
exists to bound subprocess subtrees, not to be a git client.

### 3.4 The intermediate representation

The IR is the merged subtask graph. Three constructs carry it:

- **Subtask node** (planner schema): `{id, intent, success_criteria_seed,
  files_likely_touched, depends_on, requires[], provides[], size}` where
  `size ∈ {small, medium}` (large is rejected by `validate_plan`).
- **Capability tags**: `provides` is bare strings a subtask exports;
  `requires` is `{tag, extent ∈ {in_plan, external}, reason}`. Matching
  `requires`↔`provides` builds the dependency edges.
- **The reconciler** (`phase_reconcile`, fan-out 21 — the heaviest pass)
  bridges vocabulary drift between independently-run planners via its 8 action
  arrays, then proves the result is a DAG with `_tarjan_sccs`. Its extracted
  call sequence shows the full repair loop: apply reconciler output → build
  predecessor graph → Tarjan SCC → on cycle, `_build_cycle_retry_prompt` and
  respawn once → else `die` naming the SCC. **Acyclicity is a first-class
  output property**, not a hope.

### 3.5 The type system — deterministic enforcement (§12)

This is what makes leerie trustworthy: the orchestrator never trusts a worker's
self-report when it can check mechanically. The 14 `check_*` and 6 `validate_*`
functions are the type checker. Representative gates:

| Gate | Enforces |
|---|---|
| `validate_plan` | subtask shape, `size ≠ large`, required confidence axes |
| `check_diff_scope` / `is_protected_path` | no writes to `.leerie/`, `.git/`, top-level `.claude/` |
| `check_branch_has_commits` | implementer actually committed work (`no_commits`) |
| `check_merge_committed` / `scan_conflict_markers` | integrator finished the merge; no `<<<<<<<` left |
| `check_budget_feasibility` | plan fits `max_total_workers` (EXIT 11) |
| `_tarjan_sccs` | merged plan is acyclic |
| `validate_resume_state` | a resumed run's state is self-consistent |

The **one** load-bearing LLM signal that *is* trusted is the evidence-anchored
confidence score (DESIGN §8), and even it is hardened: workers must list
`falsifiers_tested`, `contradictions_reconciled`, and `gap_to_close`. Tests,
lint, and build are deliberately *advisory* — a code-enforced "tests must pass"
gate would invite a stuck model to weaken the test instead of fixing the code.

### 3.6 Error recovery

Three nested recovery mechanisms, all bounded by caps:

1. **Schema retry** — one corrective retry on schema violation, then
   `WorkerError`.
2. **Two-tier failure policy** — `_retryable_failure` over
   `_RETRYABLE_FAILURE_KINDS = {no_commits, dirty_worktree, empty_handoff}`:
   only a *correctable* mistake gets a fresh worker; a broken/dishonest worker
   terminates immediately ("re-running a broken worker does not make it
   honest"). A coupling test pins these literals so a rename can never silently
   downgrade a retryable failure into a hang.
3. **Handoff, not compaction** (DESIGN §10) — an implementer near context
   exhaustion writes a fixed-schema checkpoint and a fresh worker continues,
   bounded by `subtask_continuations = 3` (shared with clarifications).

---

## 4. Runtime and ABI

### 4.1 The `State` machine

All run state flows through `State`. `save()` writes a temp file then
`os.replace()`s it — atomic, crash-safe. Concurrency model: a single asyncio
event loop, so coroutines interleave only at `await` and never inside a
`st.data[k]=v; st.save()` pair (no in-process lock needed). Cross-process
safety is the **single-owner-per-run-dir** invariant: `State.__init__` takes an
exclusive `fcntl.flock` on the run *directory* (its inode is stable; the lock
survives the per-save inode swap of `state.json`). A second orchestrator on the
same run gets `StateLockedError` → `EXIT_LOCKED` (75).

### 4.2 Worker invocation ABI — `claude_p`

The whole LLM surface is one function. `claude_p(worker_type, prompt, schema,
model, effort, tools, ...)` → `_invoke` → `run_proc`
(`asyncio.create_subprocess_exec` of `claude -p`). It passes `--json-schema`,
`--model`, optional `--effort`, the tool grant (`INSPECT_TOOLS`/`ACT_TOOLS`),
and reads the validated payload from the `structured_output` event. Around it
sits the `tenacity` backoff (auth/quota, ceiling `auth_retry_max_sec=300`) and
the one-shot schema-corrective retry. Every invocation appends one row to
`calls.ndjson` via `_capture_call` (§6). **No subagents**: workers are OS
subprocesses, not in-session agents (DESIGN §2, Constraint 1).

### 4.3 The container as the cleanup boundary

Leerie runs inside one container per run; the orchestrator is PID 1 and every
worker (and every Bash call a worker makes) shares that PID namespace. The
abnormal-exit cleanup guarantee is the *kernel reaping the namespace*, not
Python signal handling (DESIGN §6). The happy path is faster and in-process:
`_DescendantTracker` polls `/proc` to accumulate every PID a worker ever
spawned, and `_terminate_proc_tree`/`_signal_pids` SIGKILL the whole subtree on
exit — catching even processes that detached. Memory is contained per worker
via cgroup v2 (`_cgroup_create/enroll/destroy`, `pids.max=256`,
`memory.swap.max=0`), so one worker's OOM cannot collapse its siblings or the
orchestrator.

### 4.4 The run lifecycle automaton

`RUN_STATUSES` (11) is the observable state of a run, derived by
`_derive_run_status` from on-disk sidecars:

```
                ┌─ seed-failed         (aborted before classify; resumable)
                ├─ corrupt-sidecar     (run.json unreadable)
 start ─▶ in-progress ─┬─▶ done                    (no_work_required; no push)
                       ├─▶ done-pushed-no-pr        (pushed, --no-push-PR/offline)
                       ├─▶ done-pushed-pr           (full success)
                       ├─▶ push-failed / pr-failed  (recorded with retry cmd)
                       ├─▶ paused                   (Fly pause-on-failure)
                       ├─▶ killed                   (operator --kill)
                       └─▶ sync-failed              (remote fetch-back failed)
```

The branch `leerie/runs/<run-id>` is the durable resume contract: state records
*which wave* to resume from; the run branch holds *the work* every prior wave
produced. "Create if absent, never reset" is the invariant that makes
`--resume` safe.

---

## 5. Sub-languages and satellite programs

### 5.1 The prompt language and its preprocessor

Worker prompts in `prompts/*.md` are a real sub-language with a real
preprocessor. `load_prompt(name)` (L75) reads `prompts/<name>.md` and expands
the include directive:

```python
_PROMPT_INCLUDE_RE = re.compile(r"\{\{\s*include:\s*(_[a-z0-9_]+\.md)\s*\}\}")
def load_prompt(name):
    raw = (PROMPTS / f"{name}.md").read_text()
    return _PROMPT_INCLUDE_RE.sub(lambda m: (PROMPTS / m.group(1)).read_text(), raw)
```

It is a **single-pass, non-recursive** textual include (one `.sub` over the
top file; the included fragment's own directives are not re-expanded). Only
fragments whose names start with `_` can be included. Mechanically, exactly two
prompts use it today — `classifier.md` and `implementer.md`, both pulling in
`_clarification_filter.md` — so the clarification-filter wording lives in one
place (DESIGN §11). Every other prompt ends with a `SCHEMAS`-validated JSON
object; the confidence object recurs across all 8 core-worker prompts.

### 5.2 The configuration language

30 `resolve_*` functions implement one precedence rule everywhere:
**CLI flag > env var > `leerie.toml` key > default.** Invalid env/toml values
`die` at startup; CLI values are constrained by argparse `choices=`. The knob
names are themselves a closed vocabulary (the 34 `LEERIE_*` env vars from the
lexer in §1.2). Per-worker overrides follow a uniform pattern
(`--model-<W>` / `LEERIE_MODEL_<W>` / `model_<w>`).

### 5.3 The bash worktree mechanics

`scripts/*.sh` are the deterministic git plumbing, run in a fixed order:
`setup-run.sh` (run branch + staging worktree, idempotent) → `new-worktree.sh`
(per-subtask isolated checkout) → `integrate.sh` (`git merge --no-ff` into the
run branch; exit 0 clean / 1 conflict / 2 precondition) → `finalize.sh`
(read-only push-readiness check) → `host-finalize.sh` (push + `gh pr create`,
idempotent on `pushed_at`) → `cleanup.sh` (remove worktrees, keep the run
branch and state as audit trail). `container-entry.sh` is PID 1: it does
cgroup-v2 delegation as root, `chown`s `/work`, then drops to the `leerie` user
via `runuser`.

### 5.4 The Fly remote runtime

`--runtime fly` runs the *same* pipeline on a Fly machine (run_id = machine
ID). `scripts/remote/*.sh` wrap it: `provision.sh` (create machine) →
`seed-auth.sh` (tar-pipe `~/.claude*` over `flyctl ssh`) → `seed-repo.sh`
(`git bundle` the repo + dirty delta) → orchestrator → `fetch-branch.sh`
(stream branches + state back). Recovery paths: `force-finalize.sh` (only after
proving the orchestrator is dead via `/proc` scan), `collect-subtrees.sh`,
`re-seed.sh`, `resume-machine.sh`. The "never lose work" contract:
`decide_teardown` runs `fetch_branch` *before* `destroy_machine`, and any sync
failure leaves the machine RUNNING.

### 5.5 The chain service — a meta-compiler over runs

`chain/` is a persistent, stdlib-only Fly app (`leerie-chain`) that sequences N
ordered waves of leerie runs (DESIGN §19). It is the same "deterministic driver
+ delegated work" pattern one level up: SQLite (WAL) holds chain/run state;
`POST /chains` launches wave-0 machines via the Fly Machines REST API; a
HMAC-verified `POST /webhooks/fly` on `io.fly.machine.exited` advances waves
(all-done → next wave; any-failed → `paused`; no-next → `done`). The chain
machine holds the GitHub PAT so the no-credentials-on-worker invariant is
preserved. HTTP surface: `POST /chains`, `GET /chains`, `GET /chains/<id>`,
`GET /chains/<id>/log`, `DELETE /chains/<id>`, `POST /webhooks/fly`.

---

## 6. The self-referential loop (telemetry → judge → heal)

Leerie is a compiler that profiles itself and rewrites its own passes. Every
`claude_p` call writes a fixed-envelope row to `<run>/calls.ndjson`
(`_capture_call`), partitioned by `call_type` (= `WORKER_TYPES`). Two offline
sub-commands consume it:

- **`phase_judge`** (`main → phase_judge`): replays harvested calls through a
  `judge` worker scoring `{schema_ok, factual_ok, hallucination_ok}` →
  verdict files.
- **`phase_heal`**: `heal_baseline` runs once to measure the noise floor, then
  a loop of `request_patch → heal_apply_patch → heal_replay_patched →
  check_convergence`. The `patch_generator` worker proposes a
  minimal edit to a *worker system prompt* (`anchor` must be a literal
  substring of the live prompt — code-validated), and the loop replays to see
  if pass-rate improves (threshold `0.9`, ≤10 rounds, plateau detection). This
  closes the loop the project's three-layer rule opens: a prompt-level rule
  that matters becomes a measured, testable change.

---

## 7. How the project keeps itself deterministic

The orchestrator shells out to `claude` and `flyctl` — both non-deterministic —
yet the suite (~155 test files) is fully deterministic via three techniques:

1. **Pure-function unit tests** for everything checkable (the 30 `resolve_*`,
   6 `validate_*`, 14 `check_*`, `_derive_run_status`), loaded by importing
   `leerie.py` as a module through an `importlib` conftest fixture.
2. **Monkeypatched `_invoke`** returning canned JSON envelopes, so async phase
   / heal / telemetry logic runs against a fake LLM; plus an in-process
   `HTTPServer` + `:memory:` SQLite for the chain app.
3. **A bash subprocess harness** that *sources* the real `.sh` scripts with a
   fake `flyctl`/`claude` placed first on `PATH`, asserting exit codes and
   stdout.

The standout is `test_retryable_failure.py`'s **coupling test**: it parses the
source with `ast`/`inspect`/`re` to assert the producer-emitted `failure_kind`
literals stay a subset of `_RETRYABLE_FAILURE_KINDS`. That is the §12 principle
turned on the codebase itself — pinning a load-bearing invariant against the
source text so it cannot silently drift.

---

## 8. A grammar of the pipeline

A normal run, as a grammar (verified against the `_run_phases` source body;
`?` = conditional, `‖` = parallel, `{n}` = bounded by a cap):

```
run            ::= preflight classify provision clarify? plan reconcile?
                   ( no_work_exit | overlap_judge? schedule setup execute_waves finalize )

classify       ::= classifier_worker  ▷ check_classifier_output            [opus]
clarify?       ::= gather_answers      ▷ SOURCE_OF_TRUTH_VALUES gate       (zero questions by default)
provision      ::= lockfile_detect | mise_install | provision_worker      (first deterministic hit wins)
plan           ::= ( planner_worker ){planner_samples}  ▷ select_best
                   ▷ check_planner_output ▷ check_task_file_coverage       [opus, per matched category, ‖]
reconcile?     ::= reconciler_worker  ▷ apply  ▷ tarjan_scc(acyclic!)      (iff unresolved requires)
overlap_judge? ::= overlap_worker  ▷ apply_collisions ▷ tarjan_scc         (iff >1 planner)
schedule       ::= topo_sort(dependency_dag) -> wave+   ▷ budget_feasible? (pure)
execute_waves  ::= wave+
wave           ::= ( implementer_worker ){max_parallel,‖}                  [sonnet]
                   ▷ integrate( integrator_worker? )  ▷ scan_conflict_markers
                   ▷ conformer_worker?  ▷ settle_subtask
finalize       ::= final_conformer ▷ pr_writer ▷ host( push ▷ gh_pr_create )

no_work_exit   ::= ε                  (decided after reconcile: every planner cleared its gate + plan empty → exit 0)
```

Total worker invocations across the whole derivation are hard-bounded by
`max_total_workers = 200`; concurrency within any `‖` by `max_parallel = 5`.

---

## Appendix A — Reproduce these facts

```bash
# Stages P/L/S/SEM over the orchestrator (JSON to stdout, digest to stderr):
python3 docs/tools/leerie_extract.py orchestrator/leerie.py /tmp/leerie_ast.json

# Headline numbers expected at commit 4a7785d:
#   lines=15011 tokens=80841 ast_nodes=60834 functions=231 classes=8 constants=142
#   sha256=e7175fa9a52dd03d08c911511201d580a49e0903e626de347e318b4ad050574f
```

The extractor (`docs/tools/leerie_extract.py`) is pure stdlib and read-only.
Its four stages correspond to §1.1–§1.4. The JSON it emits contains the full
constant list, function/class records (with signatures, line spans, and
one-line docstrings), and the complete intra-module call graph with per-function
call sequences ordered by source position — the raw material for the tables
above. (Runtime control-flow order — the §3.1 pipeline and the §8 grammar — was
confirmed by reading the `_run_phases` and `phase_heal` driver bodies directly,
because a static call list does not encode branches or loops.)

## Appendix B — Subsystem index (where to look in the code)

| Concern | Entry points |
|---|---|
| Pipeline driver | `main` → `orchestrate` → `_run_phases` |
| Phases | `phase_classify` `phase_provision` `phase_plan` `phase_reconcile` `phase_overlap_judge` `phase_execute` `phase_finalize` |
| Worker invocation | `claude_p` → `_invoke` → `run_proc`; telemetry `_capture_call` |
| Enforcement | `check_*` (14), `validate_*` (6), `is_protected_path` |
| Scheduling | `schedule`, `_build_predecessor_graph`, `_tarjan_sccs` |
| State / locking | `State` (`save`/`_acquire_lock`/`bump_workers`), `StateLockedError` |
| Cleanup / containment | `_DescendantTracker`, `_terminate_proc_tree`, `_cgroup_*` |
| Config | `resolve_*` (30), `_read_toml_key`, `_parse_bool_envtoml` |
| Self-heal | `phase_judge`, `phase_heal`, `heal_*`, `request_patch`, `replay_capture` |
| Prompt preprocessor | `load_prompt`, `_PROMPT_INCLUDE_RE` |
| Chain service | `chain/server.py` (`make_server`), `chain/state.py` (`ChainState`) |

## Appendix C — Cross-reference to the canon

| This file | Canonical source |
|---|---|
| §2.2 vocabulary, §2.3 caps, §2.4 schemas | `docs/IMPLEMENTATION.md` §0/§2/§6/§9 |
| §3 pipeline, §3.5 enforcement (§12) | `docs/DESIGN.md` §3, §5, §12, §13 |
| §4.1 state/lock, §4.3 container boundary | `docs/DESIGN.md` §6 |
| §5.4 Fly remote, §5.5 chain | `docs/DESIGN.md` §6 (remote), §19 |
| §6 telemetry/judge/heal | `docs/DESIGN.md` §14 |
| §7 testing | `CLAUDE.md` "Testing", `tests/test_retryable_failure.py` |
