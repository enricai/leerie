# Post-mortem — v0.20.1 against `funeralworks`, 2026-08-14

Evidence record behind the `R1`–`R17` root-cause fixes landed the same day.
Findings are `F1`–`F24`; other docs and tests cite them by number, so the
numbering below is stable and must not be renumbered. Derived from
`~/.leerie/funeralworks/` telemetry, the surviving git branches,
`orchestrator/leerie.py` at `54c0b82`, Claude Code CLI 2.1.232, and git 2.53
docs.

## Corpus

Thirteen launches, eight concurrent against a single checkout. Eight
substantive runs: **$226.34, ~2.8 h elapsed** (16.4 h wall-clock summed
across overlapping runs). Three failed, costing **$79.58 (35.2% of spend)**,
none ever resumed.

**The failures were not infrastructure.** Zero `WorkerError`s, timeouts,
SIGKILL/exit-137, `_leerie_synthetic` envelopes, context overflows, API
429s, or Python tracebacks across all eight runs. Every lost dollar was lost
to orchestration logic.

## Findings (root cause → fix)

- **F1 — finalize rebase never worked.** `host-finalize.sh` captured the
  rebaser's JSON verdict with `2>&1` while `log()` prints to stdout, so
  `jq` failed to parse and every run fell through to "push as-is," silently
  discarding the rebased result. Fixed by separating the streams. → R1
- **F2 — clobber check false-positived on every conformer edit.**
  `_run_final_conformance` diffed the staging branch against itself (base
  and staging worktree resolved to the same moving ref), flagging every
  legitimate final-conformer commit as "clobbered." The spec, not just the
  code, had this backwards. Fixed by pinning the base to an immutable sha.
  → R2
- **F3 — implementer prompt and its check were mutually unsatisfiable.**
  The prompt told implementers to report `met: false` on build criteria the
  conformance phase owns; the check re-drove the implementer for exactly
  that answer. Cost ~$5 in repeated re-drives. Fixed with a
  `not_applicable` flag. → R7
- **F4 — four of five wiring-defect kinds could mostly only `die()`.**
  Repair/dismissal coverage was incomplete and an idempotence guard sat
  unreachable after the kind dispatch, killing one run outright ($20.32, 71
  workers, no branch) over an edge the plan had already declared. Fixed by
  widening coverage and reordering the guard. → R4
- **F5 — resource exhaustion was adjudicated by worker prose, not code.**
  37 calls hit real fork/thread exhaustion (`os error 11`) during builds;
  only the worker's own narrative decided if it was environmental, contrary
  to DESIGN §12. The existing `_is_fork_exhaustion` detector was never
  consulted on BLT output. Fixed by wiring it in. → R5
- **F6 — a later BLT axis had no `measured` gate.** An unmeasurable test
  axis (script absent on base) was silently treated as clean, letting a PR
  ship untested. Fixed by applying the existing `measured` gate uniformly.
- **F7 — the near-cap turn warning compared two different counters.** The
  CLI reports `num_turns` from separately-computed counters on its
  cap-enforcement vs. success paths; leerie compared the wrong pair,
  producing impossible ratios (e.g. `21/20`) in 11 of 61 firings. Fixed by
  comparing like with like. → R6
- **F8 — redundant subtasks are a planning defect.** leerie already flags
  provider-subset subtasks at plan time and ignores its own signal — three
  flagged subtasks in one run ran full implementers and produced zero
  commits (~$2.60 wasted). The pre-flight probe cannot catch these by
  construction (see R2); the plan-time signal is the real fix surface.
  → R13
- **F9 — the coverage gate was right twice; its advisory demotion still
  stands.** Two real gaps it caught were correct findings, but the
  demotion (its false-positive rate elsewhere) is unaffected — see R5.
  → R17
- **F10 — the same task ran twice, concurrently.** No task-identity
  registry existed; two runs with byte-identical `task.md` started 3m39s
  apart, diverged architecturally, and produced 14 colliding files across
  two incompatible PRs ($72.21 wasted). Fixed by the `task_sha256`
  live-duplicate-run check (`LEERIE_ALLOW_DUPLICATE_TASK` overrides). → R15
- **F11 — the completeness gate blocked on a defect its own conformer had
  already fixed.** A conformer round fixed a missing UI wiring and
  committed it; the gate then re-evaluated a stale result object and
  blocked on retry-budget exhaustion, nearly shipping backend code with no
  UI. Fixed by re-checking against current HEAD. → R14
- **F12 — planning overhead.** Decomposition consumed 65–80% of a run's
  worker calls in three runs, in one case merging away 8 of 25 subtasks it
  had just spawned. Documented; not yet a code change. → R13
- **F13 — the budget check is ordered late, but has never mattered.**
  `phase_wiring_gate` runs before `check_budget_feasibility`; measured
  headroom was large in every run in this corpus, so reordering is hygiene
  rather than an active fix.
- **F14–F17 — irreversible-before-valid, unusable operator text.**
  `State(...)` minted a permanent run dir before the dirty-tree preflight
  could refuse it; the `KeyboardInterrupt`/`InterruptedBySignal` handlers
  omitted `KeyboardInterrupt` itself from their guard tuple; `resume`
  mislabeled an interrupted pre-classify run as "corrupt"; `accept-blocked`
  wrote no audit trail (F16) despite `die()` pointing to one; fatal
  messages cited container-only paths on the host (F17). All fixed. → R9,
  R10, R11
- **F18 — the symptom check dispatched on an id prefix.** Id-repair
  synthesis and duplicate/overlap-merge re-homing both mint a `bugfix-`
  prefix onto non-bugfix work, producing 10 of 10 false positives on
  `sid.startswith("bugfix-")`. Fixed by scoping on the planner's explicit
  `fixes_reported_symptom` declaration instead (CLAUDE.md's
  *Language-to-JSON* principle). → R3
- **F19 — a container-side `git worktree prune` destroyed host-side
  worktrees.** Containers share the host `.git` via bind mount and ran
  bare, unscoped `prune`, which has no grace period — a concurrent run's
  rebase worktree could vanish mid-operation. Fixed by scoping prune to the
  leerie-owned namespace. → R8
- **F20 — memory admission is per-run, with no observed harm.**
  `_active_admissions` is module-level, so reservations are invisible
  across concurrent containers reading the same host slice. Measured zero
  OOM-kill/SIGKILL/admission-block events across six concurrent runs.
  Documented, not fixed.
- **F21 — telemetry under-reports input tokens by orders of magnitude.**
  `_accumulate_telemetry`/`_capture_call` read only `usage.input_tokens`,
  omitting cached-token fields; only `cost_usd` was trustworthy for budget
  accounting. Fixed by widening the accumulator. → R12
- **F22 — nothing reaps.** State dir grew to 1.5 GB (71 run dirs, 23,158
  repo-map-cache entries, 64 stale subtask branches), with low disk
  headroom already a hard preflight failure. Fixed by `leerie prune`.
  → R16
- **F23 — advisory signals used as gates.** `NO_PLANNED_FILES_TOUCHED`
  gated on `files_likely_touched`, which is documented elsewhere as
  advisory, forcing a pure-rename commit to fail. Fixed by removing the
  gate. → R17
- **F24 — documentation drift.** IMPLEMENTATION.md's line-number citation
  for the rebase `*)` arm had drifted from the actual code location. Fixed.
  → R11

## Retracted during the second pass

A second, independent re-derivation of the same telemetry reversed five
initial conclusions — recorded because each is a plausible misreading of
the same evidence:

- **R1** — "43% of bugfix subtasks re-fixed already-fixed code" was false:
  all 10 flagged findings were non-bugfix work misclassified by F18; the
  true stale-fix rate is 0 of 23.
- **R2** — the satisfied-probe's conservatism is a tuned position, not a
  defect; its low mid-run-rescue catch rate is structurally correct.
- **R3** — one run's red base was a genuine repo bug (a render-loop heap
  exhaustion and a missing test database), not container fork exhaustion.
- **R4** — inferring "found and fixed" from file-path overlap between a
  defect report and a later commit is wrong 50% of the time it fires; only
  an explicit schema channel is reliable.
- **R5** — the coverage-gate's demotion to advisory should stand; a
  proposed re-measurement of its "file-anchored" subset assumed an
  untested slice that measurement showed didn't exist.

## Confidence

Cause / evidence / impact / fix / generality were rated separately after
the second pass; all 24 findings sit at ≥90% on every axis.
