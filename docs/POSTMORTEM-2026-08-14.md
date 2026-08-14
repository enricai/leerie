# Post-mortem — v0.20.1 against `funeralworks`, 2026-08-14

This is the evidence record behind the `R1`–`R17` root-cause fixes. Each of those
commits cites a finding here rather than restating its measurements, so the
reasoning survives the diff.

Everything below was derived from `~/.leerie/funeralworks/` telemetry
(`state.json`, `orchestrator.log`, `calls.ndjson`, `run.json`), the surviving git
branches, `orchestrator/leerie.py` at `54c0b82`, the Claude Code CLI binary
2.1.232, and git 2.53 documentation. Findings are numbered `F1`–`F24` and were
re-derived a second time specifically to raise confidence; that second pass
**reversed five conclusions**, which are recorded as `R1`–`R5` retractions below
because the wrong versions are the ones a reader is most likely to re-invent.

## Corpus

Thirteen launches, eight of them concurrent against a single checkout.
Eight substantive runs: **$226.33, ~2.8 h elapsed** (16.4 h of run wall-clock
summed across runs that overlapped).

| run | task | end phase | exit | calls | cost |
|---|---|---|---|---|---|
| `14148296` | Integrations (2nd) | finalize | 0 | 96 | $39.31 |
| `474b1db0` | Reports | finalize | 0 | 51 | $24.04 |
| `ec7dae1c` | Webhooks/API keys | final conformance | **1** | 48 | $23.13 |
| `3463f30c` | Notifications | finalize | 0 | 66 | $25.39 |
| `ffb3b998` | Integrations (1st) | finalize | 0 | 77 | $32.90 |
| `1b9b52f5` | Billing | integrating wave 4 | **1** | 95 | $36.13 |
| `673a6dc6` | act-as sessions | finalize | 0 | 53 | $25.12 |
| `3bc46e7d` | Audit log | **phase 3: scheduling** | **1** | 71 | $20.32 |

Plus five launches that produced nothing: four refused by the dirty-tree
preflight, one interrupted and left unresumable.

**Three runs failed, costing $79.58 — 35.2% of spend — and none was ever
resumed** (`grep -c 'resuming:'` = 0; state mtimes unchanged since failure).

**The failures were not infrastructure.** Across all eight runs: zero
`WorkerError`s, zero timeouts, zero SIGKILL/exit-137, zero `_leerie_synthetic`
no-result envelopes, zero context overflows, zero API 429s, zero Python
tracebacks. The only failing worker calls in the entire corpus are **8 host-side
`rebaser` calls**. Every lost dollar was lost to orchestration logic.

## Retractions

These five are wrong. They are recorded because each is a plausible reading of
the same telemetry, and the second pass had to measure to reject them.

**⛔ R1 — "43% of bugfix subtasks re-fixed already-fixed code" is false.**
Classifying all 10 `SYMPTOM_DID_NOT_REPRODUCE` findings by their stored evidence:
**10 of 10 are false positives.** Eight say outright *"not applicable — this is a
test/coverage/verification subtask, not a bugfix"*. So do the other two:
`673a6dc6/bugfix-005` = *"adding unit test coverage… no independent bug"*;
`1b9b52f5/bugfix-002` = *"is a feature-implementation (bugfix-002 id but merged
from feat-003 per `_merged_from`)"*. **The true stale-fix rate is 0 of 23.**
This is evidence for F18, not for wasted spend.

**⛔ R2 — the satisfied probe is not misdesigned.** Its `evidence` strings are
correct every time: *"the route file does not exist on HEAD"*, *"the underlying
implementation is provided by sibling bugfix-002, which has not [landed]"*. The
deliverable genuinely does not exist on the base tree; the subtask becomes
redundant only once a **sibling** commits, which is exactly what the mid-run
rescue detects. The 2-of-87 vs 12-rescue gap is **structural and correct**.
`prompts/satisfied_probe.md`'s conservatism is a tuned position.

**⛔ R3 — `673a6dc6`'s red base was NOT container fork exhaustion.** The baseline
log names the failures: `src/tests/tribute/service-of-remembrance.integration.test.ts
(2 tests | 2 failed)` — *"(real database)"* tests — preceded by `FATAL ERROR:
Ineffective mark-compacts near heap limit — JavaScript heap out of memory`. Both
are genuine repo problems; the repo's own later commit `fd08ad98` is titled *"fix(test):
stop a render loop exhausting the heap, and provision a test database"*. leerie
reported that base correctly. The `Worker forks emitted error` line the first
pass built on is a downstream symptom of the vitest worker OOM.

**⛔ R4 — inferring "found and fixed" from `file_updates` path overlap would
suppress real defects.** Across the state dir, 152 `solution_defects` exist and
exactly **2** have a `where` matching a `file_updates` path — and one of those two
is `ec7dae1c`'s *genuine* finding, where the conformer edited `messages/en.json`
but deliberately left the `apiKeys` label unfixed. The heuristic is wrong on 50%
of the cases it fires on. Only an explicit schema channel works (F11).

**⛔ R5 — the coverage-gate demotion should stand.** The proposal to re-measure
its "file-anchored" subset separately assumed that subset was untested.
Measured: **17 of 23 coverage gaps (74%) are already file-anchored**, so the n=20
re-sample study that demoted the judge was mostly measuring it. There is no
untested slice to rescue.

## Findings

### F1 — the finalize rebase has never worked
`rebase_disposition_status` is `unusable` in **9 of 9** runs that reached the
rebaser; the `rebased` arm has never executed. `scripts/host-finalize.sh` captured
the seam with `2>&1` while `log()` prints to **stdout**, so `jq` received log text
with the JSON appended, returned rc 5, and control fell to the `*)` arm →
*"pushing `$run_branch` as-is"* → the scratch ref holding the rebased result was
deleted. `ffb3b998`'s rebaser call **succeeded** (`success=True`, `parsed_ok=True`,
3,958-byte valid JSON, `{"status":"failed", …}` with a full conflict diagnosis)
and the verdict was discarded, so the `irreconcilable|failed` arm that folds a
diagnosis into the PR body never ran. `head -c 2000` then preserved 2000 bytes of
log noise and dropped the JSON it exists to keep. → **R1**

### F2 — the clobber check false-positives on every conformer edit
`_run_final_conformance` passed `base_ref = _compute_run_branch(...)`, a *moving
branch name*, while the staging worktree **is** that branch's worktree
(`setup-run.sh`: `git worktree add "${STAGING_WT}" "${BRANCH}"`). `b_base` and
`b_head` therefore resolve the same ref, making `b_head == b_base` unconditionally
true. Reproduced in three runs — the flagged set is *exactly* the file list of the
final conformer's own tip commit (`ec7dae1c`→`7620bd99`, 4 files;
`474b1db0`→`05daa696`, 2; `14148296`→`6858f852`, 3) and none is reverted. Control:
the two runs whose final conformer committed nothing emitted no warning. Under
`--strict-conformer` this drives `_rollback_conformer_commits`, i.e. it would
`git reset --hard` away every legitimate final-conformer fix. **The spec was the
defect** — DESIGN.md §9 and IMPLEMENTATION.md both specified `base=run_branch`.
Independently corroborated in-tree: `_protected_paths_since`'s docstring says
staging "sits at the run-branch HEAD". → **R2**

### F3 — the implementer prompt and its check are mutually unsatisfiable
`prompts/implementer.md` orders `met: false` for build criteria; the check turned
any `met: false` into `UNMET_CRITERION` and re-drove the implementer. The schema
offered no third state, so an obedient worker could not pass. Verified re-drives:
`bugfix-008` (3 drives, $1.96), `feat-003` (3 drives, $3.00), one worker narrating
the contradiction verbatim before being re-driven for it. → **R7**

### F4 — four of five wiring-defect kinds could only `die()`
The expand/dismiss/repair handlers each early-out on `kind != "missing_requires"`,
and `_filter_provably_false_wiring_defects` is scoped to `broken_by_*`. Census
across all runs: **57 defects — 44 `missing_requires`, 6 `broken_by_drop`, 4
`broken_by_merge`, 3 `missing_provides`**, so **13 (23%) were in the die()-only
class**. This killed `3bc46e7d` — $20.32, 71 workers, 38 minutes, no branch, no
`plan.json` — over an edge test-009 **had already declared**; the idempotence
guard sat *after* the kind dispatch and was structurally unreachable. The `die()`
named three causes, none of which applied, and prescribed editing a `plan.json`
that a planning-phase death never writes. `tag_or_dep` was a bare
`{"type": "string"}`, which let the judge put prose in a field Python matches on.
→ **R4**

### F5 — resource exhaustion is adjudicated by worker prose
**37 worker calls** carry a genuine `OS can't spawn worker thread: Resource
temporarily unavailable (os error 11)` during builds, and in every case the
*worker* decides it is environmental (*"a container thread-limit resource issue
unrelated to the diff"*) — a judgment DESIGN §12 says must be code.
`_is_fork_exhaustion` exists but is consulted only in a stream relabel, never on
BLT output. The only "could not measure" classifier for a BLT axis is
`_runner_missing`, which recognises two strings, neither of them this one.
**Not** the cause of any red base in this corpus (see ⛔R3). → **R5**

*Corrected while fixing this (2026-08-14): an earlier draft of this finding
claimed `_FORK_EAGAIN_MARKERS` "contains none of these signatures". It does
contain one — `resource temporarily unavailable` — and `_is_fork_exhaustion`
therefore already matches `os error 11` correctly, as does its non-matching of
the vitest/Next OOM signatures per ⛔R3. The marker list needed no change; the
whole gap was that nothing consulted the detector on BLT output. Verified by
running both predicates against all five real signatures.*

### F6 — a later unmeasurable axis has no equivalent of `measured`
`673a6dc6` merged PR #204 with `tests: {ran: True, measured: False, passed: None,
command: './scripts/leerie-test-db.sh …'}` and `blocked: False` — the script did
not exist on that base. `_conformance_clean` tests only `ran and not passed and
axis not in red`; the word `measured` appears twice in the function and **both are
docstring prose**. Frequency: **1 of 46** final axes that ran. → **R5**

### F7 — the near-cap turn warning compares two different counters
From the CLI binary: the cap-enforcement path emits `{is_error: true, num_turns:
Fe.turnCount, subtype: "error_max_turns"}` from a `max_turns_reached` attachment,
while the success path emits `num_turns: Pe` — a separately computed count.
leerie's warning fires *only* when `terminal_reason` is absent (the success path)
and compares `Pe` against the `--max-turns` that bounds `Fe.turnCount`. Hence
**11 of 61** warnings are arithmetically impossible (`21/20`, `26/20`, `28/20`,
`31/30`, `62–72/60`) across three caps. leerie does pass `--max-turns`, so this is
not a missing flag. The adjacent comment asserts the cap path "exits 0", which
the binary contradicts. → **R6**

### F8 — redundant subtasks are a planning defect
12 subtasks settled via the mid-run rescue after a full implementer spend. The
pre-flight probe cannot catch these by construction (see ⛔R2). leerie **already
flags them at plan time and ignores its own signal**: `⚠ provider-subset
subtask(s)` fired in 5 runs, and in `1b9b52f5` all three flagged subtasks then ran
full implementers and produced **zero commits** (≈18 worker-minutes, ≈$2.6).
→ **R13**

### F9 — the coverage gate was right twice; the demotion still stands
`1b9b52f5`'s gate recorded `task_covered: False` and *"no subtask actually wires
the new UI into the admin console — admins have no route to reach it"*, naming
`src/app/[locale]/admin/page.tsx`; **95 minutes and ~$25 later the run died on
that exact finding**. `ffb3b998`'s reported the missing `lastError` persistence,
absent from the shipped PR. Both correct — but see ⛔R5. The actionable residue is
noise: `LAYER_GAP` named two subtasks touching no env or secrets. → **R17**

### F10 — the same task ran twice, concurrently
`ffb3b998/task.md` and `14148296/task.md` are byte-identical (`sha256
167b8e2801e35f72…`, 3,619 bytes), started 3m39s apart. `run_id` is the container
id and no task-identity or in-flight registry exists. The plans diverged
architecturally and **14 files collide**. Cost: **$72.21** and two incompatible
PRs. → **R15**

### F11 — the completeness gate blocked on a defect its conformer had fixed
`1b9b52f5/bugfix-004`: conformer round 2 found `sibling_site_unedited`, fixed it,
committed `1fb5a069 "conformer: wire InvoiceCheckPaymentPanel into the admin
console"` — then the gate blocked on retry-budget exhaustion. Verified on the
subtask branch: `1fb5a069` is the tip and `src/app/[locale]/admin/invoices/page.tsx`
— the "missing" file — **is present**. The gated result object lists that same
path in `file_updates[0]` and its `concrete_case` is in the past tense.
**Blast radius, verified:** the branch ships `mark-paid-check/route.ts` and
`invoice-check-payment.ts` **with no page and no panel** — backend without UI,
precisely the state the gate exists to prevent. → **R14**

### F12 — planning overhead
`1b9b52f5`: *"decomposition used 25/38 calls (66%) … healthy runs measured p50
13%"* (`decompose_worker_count: 25`), after which the duplicate-provider floor
merged away 8 of 25 subtasks. `3bc46e7d`: *"31/48 calls (65%)"* for a net **+1
subtask**, with 2 of 3 splitters returning no children. `3463f30c`: ~80% of $25.39
in planning for a 6-file diff. → **R13**

### F13 — the budget check is ordered late, and has never mattered
`phase_wiring_gate` is called before `check_budget_feasibility`. But measured
across the whole state dir every check passed with large headroom — the **largest
estimate ever recorded is 622 against a cap of 1200**, and `3bc46e7d`'s was 120
against 2000. Reordering saves nothing in this corpus; it is hygiene.

### F14–F17 — irreversible-before-valid, and unusable operator text
`State(...)` is constructed before `preflight()`, so a dirty-tree refusal still
mints a permanent run dir (4 junk dirs on disk) with a message naming neither the
files nor the fact that both were leerie's own tracked config. The
`KeyboardInterrupt` arm's `capture_repo_deps` call is guarded by a tuple that
**omits `KeyboardInterrupt`**, the one exception guaranteed to be in flight there;
`InterruptedBySignal` has the same gap. `resume` calls a pre-classify run "likely
corrupt or hand-edited" when it was merely interrupted with its `task` intact.
`accept-blocked` sets `complete`, pops the `blocked` registry, writes **no audit
trail** — while the `die()` says "See … state.json" — and flips the mode
0644→0600. Fatal messages cite `/leerie-state/...`, a container path absent on the
host. → **R9, R10, R11**

### F18 — the symptom check dispatches on an id prefix
`check_symptom_evidence` gates on `sid.startswith("bugfix-")`, and two independent
mechanisms mint that prefix onto non-bugfix work: `_repair_prescribed_commands`
synthesises `f"{prefix}{900+n:03d}"` from the *host* subtask's domain (hence
`bugfix-901`), and the duplicate-provider/overlap merge **re-homes a `feat-`
subtask under a surviving `bugfix-` id**. Result: **10 of 10 false positives**.
→ **R3**

### F19 — a container-side `git worktree prune` destroys host-side worktrees
Containers share the host `.git` (the repo is bind-mounted whole) and run **bare,
unscoped** `git worktree prune`. Confirmed against git 2.53 docs: the 3-month
`gc.worktreePruneExpire` grace period applies to `git gc` (which calls `prune
--expire 3.months.ago`); a **bare `git worktree prune` has no grace period** and
drops any entry whose directory is missing — which every host-side
`/tmp/tmp.*/rebase-*` path is, inside a container namespace. Timing confirmed:
`14148296` **spawned 3 workers during ffb3b998's rebase window (06:38–06:44)**,
each running `new-worktree.sh`. The victim narrated it: *"the worktree's git
metadata directory … has vanished"*. `new-worktree.sh` already documents this
hazard for leerie-namespace worktrees. → **R8**

### F20 — memory admission is per-run, with no observed harm
Six concurrent runs each read the same slice and each degraded to
`max_parallel=4`; `_active_admissions` is module-level, so reservations are
invisible across containers. **Measured: zero OOM-kill, exit-137, SIGKILL or
admission-block events across all six.** The mechanism is certain; the harm is
absent on this evidence. Documented, not fixed.

### F21 — telemetry under-reports input by orders of magnitude
`_accumulate_telemetry` and `_capture_call` read only `usage.input_tokens`;
`cache_read_input_tokens` and `cache_creation_input_tokens` appear **nowhere** in
the module — hence run weights like `24,281 in / 194,508 out` for 53 agentic
workers (~460 input tokens per call, impossible). Only `cost_usd` is trustworthy.
The summary also prints before the host-side rebaser calls land (under-reporting
2 calls / ~$0.85 per finalize-reaching run) and disagrees with its own adjacent
invocation count (`47` vs `49`). → **R12**

### F22 — nothing reaps
`~/.leerie/funeralworks` is **1.5 GB**: 71 run dirs, 23,158 repo-map-cache
entries, and 64 stale `leerie/subtasks/*` branches in the user's repo. Low disk
headroom is already a hard preflight failure. → **R16**

### F23 — advisory signals used as gates
`NO_PLANNED_FILES_TOUCHED` gates on `files_likely_touched`, which
`_clobbered_owned_files`' own docstring calls *"advisory and NOT used"*; it forced
a pure-rename commit in `1b9b52f5/test-007`. Every CONTINUATION prompt orders a
read of `…/checkpoints/<sid>.md`, written only on
`incomplete-handoff`/`needs-clarification` — 11 wasted `tool-fail` reads.
→ **R17**

### F24 — documentation drift
IMPLEMENTATION.md cited `scripts/host-finalize.sh:333` for the rebase `*)` arm;
the arm was at `:465` and line 333 was an unrelated call. Neither DESIGN nor
IMPLEMENTATION recorded that `*)` was the only arm ever taken. → **R11**

## Confidence

Cause / evidence / impact / fix / generality were rated separately after the
second pass; all 24 findings sit at ≥90% on every axis. "Impact" is confidence in
the impact *assessment*, so high confidence in a *small* impact (F13, F20) scores
high. The full matrix is in the plan file that drove this work.
