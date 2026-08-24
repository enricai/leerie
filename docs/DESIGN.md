# Leerie — Design Document

> Deterministic, headless task orchestrator for Claude Code. Classifies an
> engineering task, decomposes it into granular subtasks, schedules them into
> dependency-ordered waves, and executes each in an isolated git worktree
> under an evidence-gated implement/validate loop — with the fewest possible
> interruptions to the user.

**Scope of this document.** This is the *theory*: the architecture, the
constraints that forced it, and the reasoning behind each decision. It
describes the intended system, not the current code, and stays correct
across any reimplementation honoring the same architecture. Mechanism —
function names, cap values, file paths, schemas, enforcement tables, install
steps — lives in the companion `IMPLEMENTATION.md`, true only against
current code. Where the two disagree, this document wins and the code is
the defect.

---

## 1. Purpose

Given one task description, Leerie drives it to a validated, integrated
result without further human input except where input is genuinely
impossible to derive. Every loop is bounded, every decision is made from the
codebase or research, and state is kept on disk so a run is observable and
resumable.

---

## 2. The two constraints that produced this architecture

Two platform constraints eliminate the obvious designs and leave one.

**Constraint 1 — subagents cannot spawn subagents.** Claude Code lets only
the main thread spawn a subagent, so the original three-level delegation
concept (orchestrator → domain subagent → granular subagent) has no native
implementation.

**Constraint 2 — a plugin slash-command body is advisory, not executable.**
A plugin command's markdown is injected as instructions, not executed as
code. For a long, capped, multi-wave run, "the model will probably follow
these steps" is not a strong enough guarantee — control flow can drift,
silently.

Both resolve the same way: **the orchestrator is an ordinary program, not
an in-session agent.** Every unit of LLM work is a separate headless
process; the program owns all control flow. Subagent nesting is impossible
because there are no subagents, only independent OS processes. Control-flow
drift is impossible because the orchestrator is real loops and
conditionals, not a model interpreting instructions.

**Why a headless CLI process, not an API library.** The CLI binary runs on
the interactive Claude Code subscription with only the CLI as a
dependency; an agent library returning typed objects would be less brittle
but billed against the metered API. Subscription billing was a hard
requirement, so Leerie takes the CLI-subprocess form; the brittleness it
accepts is contained by worktree isolation and schema validation of every
worker result before the orchestrator acts on it.

---

## 3. Architecture

The orchestrator is a deterministic program running six phases; each unit
of LLM work within a phase is a separate headless worker process with its
own context and a defined input/output contract.

```
Orchestrator (deterministic — owns all control flow, caps, state)
│
├─ Phase 1   Classify the task into 1..9 categories          → 1 worker
│              ↓ derive the run identifier from category + task + start time
│           • Clarify — intent-only questions (optional; skipped for fully-specified tasks)
├─ Phase 2   Plan — one planner per matched category         → N workers (parallel)
│           • Reconcile — cross-domain capability-tag bridging (0 or 1 worker, when needed)
├─ Phase 3   Schedule — merge plans, build global DAG, sort into waves
├─ Phase 4   Set up the run branch and worktree (per-run unique)
├─ Phase 5   For each wave, in sequence:
│   ├─ Implement — one implementer per subtask               → workers (parallel)
│   ├─ Integrate each result into the run branch; on conflict → 1 integrator worker
│   └─ Validate the integrated run branch result
└─ Phase 6   Verify the run branch; push it and open a PR against the
             working branch; clean up. (Working branch is not modified
             locally — the PR is the proposed integration.)
```

**Why classification precedes clarification.** The set of worthwhile
clarifying questions is a function of the classification, so Clarify runs
within Phase 1, right after the classifier, and is skipped entirely for
fully-specified tasks.

**Why planners run before scheduling.** Decomposition needs LLM judgment
about a domain; scheduling is pure graph computation over the merged
result. Separating them means the scheduler never trusts a model's
ordering.

**The division of labor.** Everything requiring understanding — classify,
decompose, write code, resolve a semantic merge conflict — is done by a
worker. Everything checkable mechanically — scheduling, caps, retries,
state, integration bookkeeping — is done by the orchestrator. This is the
single most important idea in the system and recurs throughout: see §12.

**Invocation.** The orchestrator is invoked directly as a command-line
program; a thin plugin skill is a convenience wrapper with no logic of its
own.

**Observability.** Each worker's stream of tool calls, text, and
intermediate results is written verbatim to a per-worker log file (the
ground-truth audit trail) and summarized inline at a user-controllable
verbosity level (default: one line per event; `-q` terser, `-vv` raw
payloads). Errors emit at every level.

---

## 4. The nine task categories

Every task is classified into one or more of:

1. **feature-implementation** — new functionality that did not exist
2. **bug-fixing** — correcting wrong behavior, including diagnosis
3. **refactoring** — restructuring without changing behavior
4. **performance-optimization** — faster, lighter, or cheaper; same behavior
5. **testing** — writing and maintaining automated tests
6. **dependency-migration** — upgrading libraries, moving frameworks or API versions
7. **configuration-build** — CI/CD, build scripts, package/environment
   configuration on the application side (dotenv, build entry points,
   Dockerfiles, GitHub Action workflows, operator scripts consuming cloud
   outputs). Excludes authoring the cloud resources themselves.
8. **infrastructure** — infrastructure-as-code defining cloud resources
   (CDK / Terraform / Pulumi / CloudFormation / Helm / Kustomize): network,
   IAM, compute, data, messaging, observability backends, and the stack
   outputs (ARNs / IDs / endpoint names) configuration-build consumes.
   `configuration-build` and `infrastructure` form a producer→consumer
   pair; infra provides `<stack>-stack-output-names`-style tags,
   config-build consumes them.
9. **documentation** — docstrings, comments, READMEs, changelogs

A task commonly spans several categories; one planner is assigned per
matched category, since categories are domains of expertise, not mutually
exclusive bins. The **same-work test**: when two categories would produce
the same deliverables, the classifier picks the single best-fitting label.
A `SAME_WORK_RISK` advisory fires for category pairs that commonly
over-classify (e.g. `bug-fixing` + `feature-implementation`); the
classifier addresses it on retry, yielding to `classification_judge`'s
(§8) authoritative finding — once the judge confirms both categories are
genuinely required, the advisory is suppressed for that pair on later
re-classify rounds (§8 *CRITIC oscillation guard*).

---

## 5. Decomposition, sizing, and the wave model

### The sizing target

Each planner decomposes its domain into subtasks. The target is **the
smallest independently verifiable unit of change** — deliberately not "the
smallest possible unit."

Over-decomposition is not free: every subtask runs as a fresh worker that
must re-establish its understanding of the codebase from cold context, so
splitting one coherent change into five trivial subtasks pays that
cold-start cost five times and adds four integration steps. The floor is
the point below which a subtask can no longer be verified on its own; the
ceiling is that a subtask must fit inside one worker's context — one
requiring a large read/change surface is split before execution begins.

Sizing is also the **primary defense against context exhaustion** (§10): a
correctly-scoped subtask never needs a mid-run handoff, and splitting a
plan is cheap while a mid-implementation handoff is not. Planner
decomposition quality is the load-bearing assumption of the whole system,
and the first place to look when a run goes wrong.

**Conceptual dominance is a planner-judgment axis, deliberately not a
mechanical gate.** A related sizing failure is *dilution*: one subtask far
more conceptually involved than its siblings, degrading the plan if
batched with them — the fix is to isolate it into its own cluster, not
split it. This is **not** backed by a code check, because **sizing is the
wrong variable — fit is the variable**: two independent studies
(Stanford/Microsoft: 30× intrinsic same-task token variance; BAGEN: 47%
estimation ceiling) plus our own estimator confirm no pre-execution size
predictor achieves useful precision — file count, text length,
`requires`/`provides` fan-out are all proxies for an unpredictable
quantity (turn count). What *can* be judged is *Task-Context Fit*: whether
a subtask's scope and context are co-minimized, a signal that per §12
belongs in the prompt rather than a mechanical check. §5½ describes the
structural mechanism giving the planner the codebase knowledge to judge
this well, and the recursive fit-judge that becomes the authoritative
decomposition-quality gate (demoting the self-scored
`decomposition_quality` axis to non-gating advisory) — contrast
`UNCOVERED_MIGRATION_SURFACE`, a code check because migration coverage
*is* mechanically countable.

### Cross-domain dependencies

Planners run in parallel and cannot see each other's output, yet
dependencies cross domains (a testing subtask may depend on the feature
subtask it tests). Three mechanisms reconcile that coupling:

- **Intra-domain ordering** — within its own domain a planner declares
  which subtasks must precede which, since it owns and can see them.
- **Cross-domain capability tags** — a planner cannot name another
  domain's subtasks, so each subtask instead declares the capabilities it
  *produces* and *requires* as abstract tags; the orchestrator matches
  every "requires" against every domain's "provides" and adds a
  producer→consumer edge.
- **Reconciler worker** — tags are a shared vocabulary with no enforced
  dictionary, so two planners can name the same capability differently
  (`event-capture-shim` vs. `capture-call-implemented`) and a
  literal-string match misses it. When any `requires` tag goes unclaimed
  after all planners finish, a single *reconciler* worker reads the full
  task plus every subtask and emits one of eight actions: `renames`
  (unifies two tags naming the same thing), `add_provide` (an existing
  subtask already produces the capability undeclared), `added_subtasks`
  (a genuine gap needs a new subtask), `conditional_drop`
  (a consumer's unresolvable prose conditionality becomes a structured
  drop), `drop_require` (an over-specified `requires` entry is dropped,
  consumer kept), `dependency_edges`/`merged_subtasks` (cycle-breaking:
  explicit ordering, or collapsing genuinely-overlapping subtasks), or
  `unresolvable` (escape hatch: aborts the run with the reconciler's
  diagnosis). All judgment lives in the reconciler; the orchestrator
  computes the unresolved set, cycle-checks with Tarjan's SCC, and applies
  the output mechanically. The wire shape is flatter than this reads —
  one `tag_ops` array discriminated by `op` plus a sibling
  `added_requires`, since the natural nested shape exceeds what grammar
  compilation accepts (§ *Forcing constrained decoding*) — with one
  adapter fanning it back out before downstream code sees it.

  **Artifact-registry worker (advisory, upstream of the reconciler)**
  narrows this problem before it starts: a read-only `artifact_registry`
  worker reads the task plus the ranked repo-map and emits a canonical
  list of artifacts the task will plainly create, each with a suggested
  tag and path, injected into every planner's context to *prefer*. Purely
  advisory — the reconciler and wiring gate remain the backstops — it
  only raises the *rate* at which two blind planners land on the same
  string; a planner must still *declare* the edge.

  **Test subtasks must wire to their producers.** An edge only forms when
  the consumer *declares* it. Recurring shape: a `testing`-domain subtask
  exercises what another subtask creates but declares neither a
  `requires` tag nor a `depends_on` id for it, so the wiring gate rejects
  the plan — including *indirect* cases like a coverage-floor test
  depending on the feature subtask whose files it enumerates even when
  the file sets differ (semantic, so prompt-side per §12, not a Python
  inference). `_warn_test_subtask_missing_producer_edge` flags the
  high-risk shape a phase earlier as advisory; the wiring gate is the
  enforcer, since (measured 2026-08-01) the commoner failure is a
  *specific* missing edge among several declared ones, which a
  both-channels-empty advisory can't see.

  **Transitive-chain wiring is not producer wiring.** Test subtask `T`
  declaring `depends_on: [B]` doesn't substitute for wiring directly to
  an earlier subtask `A` whose fixture `T` also exercises — `A`'s output
  isn't guaranteed to survive as `B`'s own `provides` if `B` is later
  merged, dropped, or re-scoped. Two incidents (barnacle, 2026-07-31) hit
  this and the wiring gate correctly rejected both plans, so the planner
  now traces every subtask whose *output* the test's assertions touch and
  wires to each directly.

  **Dead-subtask elimination (code-enforced).** A subtask whose *every*
  `in_plan` requires targets a domain that returned 0 subtasks is fully
  speculative; before `die()` the orchestrator prunes such subtasks
  mechanically (dead-code elimination on a constant-folded 0-subtask
  domain). `_detect_no_work` fires if all domains end up empty; the run
  still dies if unresolvable entries remain after pruning.

  **Acyclicity is first-class.** If the reconciler's first attempt closes
  a cycle, the orchestrator detects it with Tarjan's SCC, computes a
  recommended fix from structural signals (`depends_on` orientation,
  `files_likely_touched` overlap), and respawns the worker once with the
  cycle data — the model never has to detect cycles itself. A second
  cycle aborts the run with the SCC and offending mutations named. The
  same structural-feedback retry applies to a tag still unresolved after
  the first attempt (commonly a newly-invented tag not renamed to match
  the consumer's): the orchestrator surfaces string-similarity hints over
  the post-mutation `provides` namespace and respawns once; still
  unresolved, the run aborts with the structured report.

### `requires.extent` — in-graph vs. external prerequisites

Not every prerequisite a planner identifies is satisfiable by another
subtask in the plan. A planner researching its domain (especially under
`source_of_truth = both`) sometimes surfaces a genuine prerequisite that
lives *outside* the build graph: a Dynamo table provisioned by another
repo, an ops runbook, a manual step in another team's queue. Treating
those as unresolved cross-domain edges forces the reconciler to either
invent a connector subtask with its own out-of-scope `requires`, or abort
— neither preserves the insight.

To carry that distinction, each `requires` entry is `{tag, extent,
reason}` rather than a bare string:

- `extent: in_plan` — satisfied by another subtask in this plan; the
  orchestrator wires a graph edge by matching against `provides`.
- `extent: external` — a real prerequisite the planner declares lives
  outside *this run's* build graph. Three kinds qualify. **Outside the
  build graph entirely**: another repo's deploy, an ops runbook, a manual
  step elsewhere. **Producible by code, but owned by another run**: a
  sibling phase document, an earlier phase, another run of the same
  multi-part deck. **Fenced off by the task itself**: the task declares a
  surface out of scope whose only implementation site lies there.
  `reason` always names the owner; the orchestrator filters these out of
  matching and collects them into a `preconditions` section of the
  assembled plan — the human sees the insight as a deploy note, not a
  hard edge. In a run-group (§20), a sibling repo in the group is still
  `external` by design, and `reason` naming it is what finalize turns into
  a deploy-ordering note.

The test separating the two values is **"is it in *this run's* graph?"**
— not "could any code produce it?": the second `external` kind above is
producible by code, so that reading forces `in_plan`, which has no
provider and routes straight to `unresolvable`, aborting a multi-part
task deck after the full planning spend — the run-group carve-out is the
same principle one scope wider. A task-declared fence removes a surface
from the graph just as surely as another run owning it does: run
`2d7527f1` (2026-08-17, 55 workers, $12.46, no code written) fenced off a
directory and then listed a criterion whose only implementation site was
inside it — planners split blind (the owning domain obeyed the fence,
`testing` obeyed the criterion and declared `in_plan`), and the
reconciler had no legal resolution (an unconditional consumer,
`conditional_drop` inapplicable) and correctly aborted. The fence, not
"could a connector subtask produce this," is what the planner should key
on — it did the research and is the right classifier; the reconciler
cannot answer "does some *other* domain's planner produce this?" any
better.

**The external twin.** Two blind planners can classify the same
capability differently — one `external`, one `in_plan`, which reaches
`unresolvable` by construction while contrary evidence sits unread in the
externals collected moments earlier. Measured on run `1178f696`, both
fatal entries had such a twin. Before dying, the orchestrator now checks
each `unresolvable` entry against the collected externals — exact tag
first, then a singularized token set — and a hit rewrites it to
`external`, inheriting the twin's `reason`. This runs *after* the
reconciler's verdict deliberately — running it before was measured
demoting three tags the reconciler would otherwise have resolved (which
resolves far more than it aborts: 279 vs. 4 across the corpus) — and
every demotion is logged for audit.

**Collision rule.** If one planner declares `requires: {tag: X, extent:
external}` and another declares `provides: X`, `provides` wins and the
entry is silently promoted to `in_plan` — a planner cannot unilaterally
bypass a real producer already in another domain's plan.

`unresolvable` is now reserved for genuinely-broken in-plan tags — typos,
hallucinations, or in-plan capabilities the reconciler can neither rename,
attribute, nor connect; a *conditional* consumer routes through
`conditional_drop`, an *over-specified* entry through `drop_require`.

**Accepting external-blocked subtasks.** When a subtask's only
unsatisfied prerequisites are `extent: external`, the worker discovers
the dependency is missing (e.g. no Postgres server in the container) and
returns `status: blocked`; the orchestrator doesn't gate dispatch on
external preconditions, so `resume` would block again indefinitely.
`accept-blocked <run-id> <subtask-id>` sets `subtask_status[sid]` to
`complete` so `resume` skips it, keeping external preconditions a human
concern.

**The integration gate needs the same escape hatch, for a sharper
reason:** `integrate_wave` dies on a behavioral defect from
`integration_judge`, an LLM whose false positive would otherwise
permanently kill a run after full spend, since the merge is already
committed to staging and a naive resume re-reaches the same verdict.
`integrate_wave` persists the verdict to `integration_gate[sid]` *before*
dying, so resume distinguishes "rejected" from "accepted" rather than
seeing an already-merged branch and skipping past a rejected verdict.
`accept-integration <run-id> <subtask-id>` flips the record's `accepted`
flag; an unaccepted finding re-invokes the judge on resume, so a verdict
that no longer reproduces resolves itself. `--skip-integration-check`
disables the gate wholesale, matching the five other gates that carry a
bypass.

The result is a single global dependency graph spanning all domains. A
topological sort turns it into waves: subtasks within a wave are mutually
independent and run in parallel; waves run in sequence. A dependency cycle
is unsatisfiable; the reconciler's retry loop tries to break it (preferring
`drop_require` / `dependency_edges` / `merged_subtasks` over cycle-closing
renames), and if that fails the run aborts with the SCC and the mutations
that closed it named.

### Cross-domain surface overlap

The reconciler bridges *vocabulary* drift. A second class it does not
address: two planners independently proposing subtasks that produce **the
same exported artifact** with **incompatible APIs**. Because each planner
can legitimately declare its own `provides` tag for the artifact
(`widget-frame-component` and `widget-frame-adopted` for the same
`WidgetFrame` extraction), this class slips past every check between
planning and integration, surfacing as an integrator merge-conflict
mid-run with worker budget already spent.

**A re-plan invalidates every phase that already ran.** The planning
pipeline is `reconcile → overlap-judge → adherence-gate → coverage-gate`;
the two later gates can reject a plan and re-drive `phase_plan` with no
cross-category visibility, reintroducing the same drift and collisions
already resolved — so whatever a gate re-plans, it owes a re-run of every
phase upstream of itself. The repair is asymmetric: a re-plan from the
adherence gate must re-run reconcile and the overlap judge but not the
coverage gate, which hasn't run yet. The coverage gate itself no longer
re-plans at all — demoted to advisory on 2026-08-04 (§8 *Independent
adversarial verification*) — so its obligation is currently unexercised,
stated here because the rule is positional, not gate-specific. Nesting a
gate inside another's retry loop is bounded, not recursive.

Getting this wrong is expensive and silent. Run `19a70d96` (2026-08-01):
the overlap judge merged 8 subtasks down to 4; the coverage gate
re-planned, all 8 duplicates came back undetected and executed, two did
the same migration, and the integration gate refused the result after 4.7
hours and 164 workers.

A **plan-overlap judge** worker runs between reconcile and schedule
specifically to catch this. It reads the full reconciled subtask list
(title, intent, `files_likely_touched`, `provides`, `requires`) and emits
zero or more `collisions`, each with one of four resolutions: `merge` (one
component satisfies both intents), `drop_a`/`drop_b` (one intent is
strictly superseded), or `unresolvable` (the intents are structurally
contradictory and the run should die at plan time, not at integration).

**An `unresolvable` verdict re-plans before it dies.** A terminal `die()`
after the entire planning spend is too costly for a correct refusal to
merge. Measured across the corpus: 43 runs reached this judge and 5 (12%)
died here, burning 404 unrecoverable workers, while the judge resolved
95.5% of collisions (232/243) without incident. So an unresolvable
collision — a verdict about the plan, not the judge — now re-plans the
implicated domains once with the contradiction as feedback, and dies only
if the second verdict is still unresolvable (`overlap_replan_done`).

The re-plan is **scoped**, and this is the only gate where that's
currently possible: a collision names `a_sid`/`b_sid`, so the implicated
domains are mechanically derivable, whereas `coverage_gaps` and the
adherence judge's `violations` carry no subtask reference at all. Scope is
the implicated domains plus `_replan_domain_closure` (the transitive set
of dependents across both id and tag channels, so no edge dangles).
Measured over 85 (domain, plan) simulations: closure-scoped schedules and
validates 85/85, naive single-domain 79/85.

The artifact a collision names often does **not yet exist** — the
canonical case is two planners each proposing to *create* the same file.
Existence is checked against the union of `files_likely_touched` and the
repo, so a to-be-created file counts as real evidence.

`artifact` is a free-text label and Python never parses it (CLAUDE.md
*Language-to-JSON*). A collision instead carries `artifact_paths`: the
repo-relative paths the judge names explicitly, the only thing
`PHANTOM_ARTIFACT`'s existence check reads.

`artifact_paths` is **asked for but not schema-required**. Requiring it
was far more destructive than the false positives it prevented: this
phase produced valid output on only **40.9%** of invocations (vs.
99.6–100% elsewhere), with **84 of its 85 validation failures** the
single error `'artifact_paths' is a required property'` — a whole-payload
rejection of an otherwise-sound analysis. Absence was already the
designed-for case, so requiring the field only turned a graceful skip
into a discarded plan.

The judge is biased toward escalation: before emitting `merge` it must
verify the two intents are compositionally consistent and write a
concrete `merge_feasibility` statement the orchestrator carries forward
as the merged subtask's unified intent. If no such statement can be
written the resolution must be `unresolvable`, not `merge`; the
orchestrator enforces this in Python, since a `merge` with empty
`merge_feasibility` is a fatal error.

The judge's recall on the test corpus was 100%, with the merge-feasibility
discipline correctly downgrading incompatible-API pairs to `drop_*` and
`unresolvable`. Skip is automatic on single-planner runs; opt-out via
`--skip-overlap-judge`. The complementary `_warn_cross_planner_file_overlap`
stays advisory — the judge handles the load-bearing case.

**A deterministic floor underneath the judge.** The judge is skipped
outright on single-planner runs, skippable by flag, and — before the
re-plan repair above — could be bypassed by a downstream gate re-planning
after it had already passed. §12's split applies here too: the judgment
layer keeps the semantic call, and a mechanical floor beneath it catches
the shape needing no judgment.

The floor is pure set logic (*Language-to-JSON*): two subtasks declaring
the **same `provides` tag** with intersecting **`files_likely_touched`**
are doing the same work to the same file. One exclusion is load-bearing:
subtasks sharing a `_cofile_cluster` are deliberate sub-file splits and
must never be flagged — without it the rule matches 3571 pairs across the
corpus; with it, 9, in exactly two runs, both destroyed by duplicate
work. The other 50 corpus runs produce no flags. An "already ordered by
`depends_on`" exemption looks obviously necessary and is not — zero
flagged pairs were ordered across the corpus — so it stays unimplemented,
evaluated even when the judgment layer crashes.

**M11 DECISION — the floor's detections are resolved, not merely
logged.** Each flagged pair is synthesized into a `merge` collision and
applied through `_apply_overlap_collisions`, the same machinery the
judge's own output uses — including anchor + transitive `survivor_of`
cluster resolution, so a 3-or-more-participant collision collapses to a
single survivor. This runs above every skip in the phase, firing on the
paths the floor exists for (single-planner plans,
`--skip-overlap-judge` runs).

A single subtask can legitimately overlap with several siblings on
different artifacts. Since the judge's protocol stays pairwise, the
orchestrator walks pairs into a coherent cluster via the
**anchor-survivor rule**: a sid appearing in two or more
non-`unresolvable` collisions is the *anchor* and survives every merge it
participates in, absorbing each partner. Without this, the default
lex-smaller survivor rule (a determinism device with no semantic content)
would silently discard the broader spec the judge identified.

Membership is bare *appearance*, not absorption — including a sid
*dropped* twice, which never survives to use the hint; "the anchor
absorbs its partners" is false on roughly a third of resolution
combinations. Separately, the orchestrator rejects a `drop_*` whose
dropped sid *survives* another collision, since no apply order can
satisfy both claims — that `die()`s at plan time with both pairs
surfaced. The condition is *survives-somewhere ∧ dropped-somewhere*, not
anchor membership: gating on the anchor set was a real defect (a sid
dropped by several collisions is an anchor by appearance though nothing
claims it survives) that killed those runs unrecoverably.

**Multi-drop.** A single sid may legitimately be the dropped side of
several collisions at once — the judge found its surface jointly
covered by several siblings, the drop-shaped analogue of the anchor
cluster above and exactly what the judge prompt instructs when a
shared endpoint genuinely should be dropped. Coherent output; must
not `die()`.

It cannot, however, be applied by replaying the pairs through the
apply loop's transitive `survivor_of` rewrite. Chasing that pointer is
safe for a `merge` — the absorbed subtask's intent carries forward —
but a `drop_*` *deliberately discards* the dropped subtask's title,
intent, and success criteria. Replaying pair two after pair one has
rewritten the endpoint therefore drops a **live, wanted** subtask the
judge never named, silently. The damage scales with cluster size: a
sid dropped by three collisions destroys three of the four subtasks
involved.

Multi-drop is instead applied as a single operation over the whole
cluster. The dropped subtask's `provides` are unioned into **every**
named survivor and inbound `depends_on` references fan out to all of
them (mirroring the id-vanishing fan-out rule — a dropped sid's work
is genuinely split across its survivors, so a consumer depends on all
of them). Because the fan-out *adds* graph edges it can close a
dependency cycle that none of the individual pairs would, so it is
guarded by the same trial-apply check every other resolution uses,
degrading rather than dying:

1. **`multi_drop_fanout`** — the full fan-out, when it stays acyclic.
2. **`multi_drop_degraded_single`** — fan-out would cycle: fall back
   to the lex-smallest survivor alone. Deterministic by sort order,
   never by collision order.
3. **`skipped_would_cycle`** — both would cycle: keep the subtask and
   leave the overlap for the integrator, exactly as a cyclic merge
   does today.

The whole-cluster application is what makes the result independent of
the order the judge happened to emit its pairs in — a determinism
requirement the scheduler's contract depends on.

**Multi-artifact pair.** The third coherent shape, alongside the anchor
cluster and multi-drop above: one *pair* colliding on several artifacts.
The judge may encode this either way — a single row whose `artifact_paths`
lists every overlapping file, or one row per artifact — since
`artifact_paths` is itself a list (`prompts/plan_overlap_judge.md`:
"Naming several artifacts in one row is equally fine"). This section
covers the split-row case: when the judge does emit one row per artifact,
the distinguishing question is not whether the pair repeats but whether
the rows agree on what they *do* to the plan: the resolved dropped sid for
a `drop_*`, the unordered endpoint pair for a `merge`. Rows whose effect is
identical are the same decision stated once per surface, and are
coalesced into one collision that keeps every artifact name, every
`artifact_paths` entry, and every `merge_feasibility` statement (the
carry-forward invariant below applies unchanged). Rows whose effect
*differs* cannot all hold — the same pair
emitted twice as `drop_a` with the endpoints swapped deletes both
subtasks — and are fed back to the judge as a retryable issue. The retry
is an opportunity to fix a contradiction, never an escape from one: a
contradictory duplicate that survives the retry budget is still refused,
because on a two-subtask pair any effect difference necessarily makes one
sid both dropped and surviving, which the keep-and-delete rule above
already forbids. No apply order satisfies it, so it never reaches the
apply loop.

`resolution` alone is the wrong signal here, and treating any repeated pair
as incoherent is worse: it `die()`s correct output after the full planning
spend, at a validator that runs *after* the retry loop and so cannot be
recovered from. Both failure modes were observed together on run
2026-08-01: a spurious phantom-artifact issue forced a retry, the retry
expressed a two-file overlap as two rows, and the pair gate killed the run.

The anchor rule introduces one invariant the orchestrator must preserve:
**merge_feasibility carry-forward.** When subtask X is absorbed by subtask Y
in a merge, *every* `merge_feasibility` statement ever appended to X's
intent — including ones from prior merges where X was itself a survivor —
must be preserved in Y's intent. Otherwise `merge(B,D)` followed by
`merge(A,B)` silently loses the first merge's statement as B is absorbed
into A — the same silent-data-loss class the per-pair discipline exists to
prevent, applied across a chain of absorptions rather than within one pair.

**Post-merge acyclicity.** A collision resolution's dependency-union — the
survivor inheriting the absorbed subtask's `provides`/`requires`/`depends_on`,
plus downstream reference rewriting — can introduce transitive cycles absent
from the post-reconcile graph, even though the phase 2½ acyclicity gate passed
before these resolutions ran. The most common shape: the survivor absorbs a
`provides` tag that some third subtask already `requires`, closing a back-edge
through a node outside the merged pair — with no shared file overlap at all
(the cycle diagnostic reports `Shared files_likely_touched: none`). This
affects both `merge` and `drop_*` resolutions, since `_apply_overlap_drop`
likewise unions the dropped subtask's `provides` into the survivor and
rewrites downstream `depends_on`.

**Id-vanishing operations must rewrite inbound references.** The rewriting above is
not a merge-specific courtesy; it is an invariant every operation that removes a
subtask id from the plan owes. Merge, drop, the phase-3 soft-drop filters
(`_filter_offtree_subtasks`, `_filter_satisfied_subtasks`), and P1 recursive expansion
all vanish an id. Each owes the plan two things: the vanishing subtask's
`provides`/`requires`/`depends_on` must be carried by its successor(s), *and* every
subtask referencing the vanishing id via `depends_on` must be rewritten to reference
those successor(s).

The two dependency channels do not fail alike *under expansion*. On
**expansion** the tag channel self-heals: `provides` is inherited by
successors and `_build_predecessor_graph` resolves a `requires` tag to
*every* provider, so a tag-expressed edge survives untouched. Only the
id-expressed `depends_on` channel dangles — silently at `_schedule()` (drops
an unknown predecessor without a word), then fatally at `_validate_plan`. An
operation that gets this wrong looks correct under tag-based plans and dies
only when a planner happens to express the same intent by id.

**A drop is not symmetric with an expansion on the tag channel.** The self-healing
above holds only because a successor inherits the vanishing subtask's `provides`. A
**drop** has no successor, so its `provides` are simply gone — nothing inherits them,
and any surviving subtask whose `requires` names a tag *only* the dropped subtask
provided is now orphaned. That orphan dies at `_validate_plan` exactly like a dangling
`depends_on` (`requires 'X' but nothing provides it`). Therefore a drop owes the plan a
prune of **both** channels: the inbound `depends_on` (id) references, *and* the inbound
`requires` (tag) references whose only provider was dropped. The prune is gated on the
dropped subtasks' `provides`: a tag still provided by a *surviving* subtask is kept, and
a tag no subtask ever provided is left intact so `_validate_plan` still surfaces it as a
genuine planner error rather than having it silently masked. "Surviving" is evaluated
**across the whole merged plan, not per-domain** — capability tags are cross-domain
(§*Cross-domain capability tags*), so a `requires` in one domain's plan may be satisfied
by a `provides` in another; the prune must see every plan at once, matching how
`_validate_plan` checks provider-existence globally.

Where an id vanishes into **several** successors (expansion), the rewrite fans out to
all of them — matching what the tag channel already does, and costing no additional
waves when the successors are mutually independent (they occupy the wave the parent
would have). Where an id vanishes with **no** successor (a drop), both the id reference
and the now-orphaned tag reference are pruned (per the paragraph above). Fanning out to
a single "representative" successor, or dropping the id edge on the theory that a tag
will cover it, is the same silent-data-loss class named above: a parent with no
`provides` has no tag edge to fall back on.

The orchestrator handles this with **per-resolution cycle avoidance** rather
than an all-or-nothing post-hoc gate. Before applying each collision it
tentatively applies the resolution to a throwaway copy and runs Tarjan's SCC;
if the resolution would introduce a cycle, that resolution is **skipped**
(`skipped_would_cycle`) and both subtasks are kept separate for the
integrator to resolve at integration time. Every non-cycling resolution
still applies — a deterministic, per-resolution degradation of the
`--skip-overlap-judge` escape hatch, with no extra worker round-trip: the
cycle is a global-graph property outside the judge's pairwise competence.
Tarjan's SCC still runs on the final post-merge graph as an internal
backstop; with per-resolution avoidance in place it must never fire, so a
surviving cycle is an orchestrator logic bug, not a user-recoverable
condition.

**A wiring re-check on the fully-merged plan, before `_validate_plan`.** The
per-operation rewrite discipline above is applied at each vanishing site — the
drop filters prune both channels, expansion fans out. But the discipline is
distributed across many operations (reconciler merge/rename/drop, both phase-3
soft-drop filters, P1 expansion), and each is an independent opportunity to leave
a dangle: a reconciler `merged_subtasks`/`renames` that rewired the id channel but
not an inbound `requires`, an expansion whose successor inheritance missed a tag,
or a *chained* vanishing that `_remap_vanished_deps`'s single-pass flatten does not
fully resolve. `_validate_plan` is the terminal backstop that catches any survivor —
but it fires *after* the full planner/reconciler spend, so a dangle it catches
throws that spend away. So a deterministic `check_plan_wiring` runs on the
fully-merged post-drop plan (after both soft-drop filters and `_schedule()`, before
`_validate_plan`): it replays `_validate_plan`'s own provider-existence and
`depends_on`-existence logic and, on a dangle, dies with a wiring-specific message
that names the vanishing operation when derivable — front-running the generic
`_validate_plan` die with an actionable one, while `_validate_plan` stays the
backstop. This is the §12 boundary: the structural guarantee (every surviving
`requires`/`depends_on` resolves) is a code check, not spread across per-operation
prompt discipline alone.

Structural existence is necessary but not sufficient — a plan can be *wired* (every
tag resolves) yet *semantically* incomplete: a subtask whose work genuinely needs a
capability but never declares the `requires` tag, or a merge that silently dropped a
real dependency the tags never encoded. Those gaps are invisible to a
provider-existence scan. So an independent `wiring_judge` (§8 *Independent
adversarial verification*) reviews the merged plan and attacks its wiring for the
semantic dangles the structural check is blind to: the deterministic check owns
"does every declared edge resolve," the judge owns "is the set of declared
edges the right one."

**The judge repairs what is unambiguously repairable, and dies on the rest.**
Detecting-and-dying alone was wrong: the commonest defect this judge finds is one
*no planner could have avoided*. Planners run blind and in parallel — a planner's
context carries the task, the repo map, and the shared artifact registry, never a
sibling domain's subtasks — so when subtask A in domain X needs a capability that
domain Y's planner will invent a tag for, A cannot declare that `requires`; the
tag does not exist yet. The reconciler cannot fix it either: its charter is
*declared-but-unmatched* tags, and A declared nothing, so A never enters its
input. No phase upstream of the judge owns this edge, making the judge's finding
the first and only point the plan can be corrected. Measured across the run
corpus, two thirds of runs reaching this gate died at it, half of those deaths
this exact shape.

The repair is deliberately narrow, but it must read the defect on **both** of the
plan's dependency channels, because the judge names either one. The schema field
is `tag_or_dep`, and the judge fills it with a capability tag *or* a subtask id
depending on which the defect is about. Three shapes are therefore repairable:

- **Tag channel.** The named tag resolves to exactly one in-plan provider that is
  not the subtask itself → append an in-plan `requires`.
- **Id channel.** The named value is a surviving subtask id (and not the subtask
  itself) → append a `depends_on`. Unambiguous by construction: an id names
  exactly one subtask, so the several-providers ambiguity below cannot arise.
- **Single-cluster fan-out.** The named tag has several providers, but every one
  of them shares a single `_cofile_cluster` → append an in-plan `requires`. Those
  providers are the sub-file region splits of one file (§5½ (P1) *Sub-file*), so
  requiring the tag orders the subtask behind the whole cluster, which is exactly
  the intent. The ambiguity is an artifact of counting split siblings as rival
  providers.

In every case the resulting graph must still be acyclic — trialled against a copy
before it is applied, using the same cycle definition as every other site.

**The residual is re-checked against the ordering the plan actually has.** The
already-declared guard above is *channel-local* (the id arm asks whether the
named id is in `depends_on`, the tag arm whether the named tag is in
`requires`) and sits **downstream of channel selection** — a defect matching
no channel takes the `else` arm straight to the residual, so the guard is
never reached on the one path that reaches the `die()`. Run `05fdffb8` died on
`WIRING_DEFECT (missing_requires) test-003 / action-echoed-row-payload` even
though `test-003` **already declared `requires: action-echoed-row-payload`**
— the tag had two providers in different clusters, no channel matched, and the
plan died with the whole planning spend on a gate with no bypass flag.

So after the repair loop, any residual defect whose subtask is already
ordered behind **every** producer of the named capability is dropped. Three
properties matter:

- **Ordering is resolved through `_build_predecessor_graph`**, not read off
  `depends_on`, for the same reason the cycle trials route through it: so
  "ordered behind" cannot drift from what the scheduler does. Ordering also comes
  from `requires` entries with `extent: in_plan`, and across the repair corpus 99
  of 535 direct orderings (19%) exist *only* through that channel.
- **Every producer, never any one.** A capability with two producers where the
  subtask precedes only the first is precisely the judge's complaint about the
  second; dismissing on a non-empty intersection would wave through the race this
  gate exists to catch — strictly worse than the over-gating being fixed. The
  producer set is required non-empty, since the empty set is vacuously a subset
  and would dismiss every defect naming a capability nothing provides, which is
  the canonical *true* finding.
- **Direct edges only, not the transitive closure.** A further 127 corpus
  orderings hold only transitively. Those would refute the finding just as
  soundly, but dismissing on them is a much broader claim to make on a die-only
  gate, so the check stays 1:1 with the graph's own edge definition.

It applies to `missing_requires` alone. The repair loop routes *every*
non-repairable defect to the same residual, so `broken_by_drop` and
`broken_by_merge` arrive here too — and ordering cannot refute those: they assert
the *work* is gone, and no amount of scheduling behind a subtask restores a
capability it no longer provides.

It runs after the repairs rather than before them because a defect can be
mooted by an edge a *sibling* defect's repair added, and the judge's
emission order is arbitrary. Each dismissal is logged naming the responsible
edges, so a judge degrading over time stays visible rather than being
silently absorbed. Anything else is refused and the gate dies as before: a
value that is neither a subtask id nor a provided tag means the plan
genuinely lacks the capability, and several providers spanning *different*
clusters is an ambiguity only a human can resolve. The cycle trial is
load-bearing rather than defensive — a well-formed but wrong edge can close
a cycle spanning the entire plan, so skipping it would convert a survivable
planning defect into a dead run.

Reading only the tag channel was the original shape of this repair, and was
the dominant reason a repairable run still died: measured across the
corpus, **23 of the 24 defects refused as "no in-plan provider" named a
surviving subtask id** (one run died with 22 defects, all this shape);
counting split siblings as rival providers accounted for the rest in a
second run. Closing both channels takes the corpus from 19/27 runs clearing
the gate to 21/27, with the residual defects then all genuine missing work.

Because added edges change the wave partition, the scheduler is re-run and the
plan snapshot rewritten after a repair, so everything downstream — the budget
preflight, the deterministic wiring re-check, `_validate_plan`, `_write_plan` — sees
the repaired graph rather than the one the judge rejected.

**Each defect declares a severity, and the severity is asked for rather than
required.** A `live_defect` gates; a `latent_risk` is logged as a warning only.
The field exists because the judge could already express "this isn't really a
defect" — but only in free-text `rationale`, which the gate never reads, so a
run died on a finding the judge itself had called "a latent fragility rather
than a live defect" (§12: a judgment must reach code as structured JSON, never
be stranded in prose).

Making it a *required* field did not serve that purpose; it defeated it. A
judge that omitted the field produced no schema-valid payload at all, so the
gate did not run and caught **nothing** — measured across the corpus, every
`wiring_judge` invocation that never produced valid output failed on exactly
this one field. The severity channel is therefore optional, and an unlabelled
defect gates, per *Findings carry a severity* above: **the default is gating**,
so an incomplete classification keeps the conservative behaviour instead of
silently weakening a real gate. One conservatively-gated defect is a recoverable
false positive; a gate that never runs is not.

### Migration-surface completeness

When a plan introduces a new pattern replacing an old one — a new
accessor replacing direct field reads, a new seam replacing scattered
inline logic — the **migration surface** is the set of all call sites
of the old pattern. A plan that creates the seam but does not cover the
consumers is structurally incomplete: the codebase still uses the old
path, and a follow-up run repeats the classification/planning cost to
discover the gap.

This is enforced mechanically at two levels:

- **Intra-domain (CRITIC-enforced).** The planner declares what a subtask
  replaces as a structured `migration_targets: [{old_pattern, replacement,
  is_real_identifier}]` field on its own schema — Python never infers this
  from prose. `check_planner_output()` reads `migration_targets` directly:
  for each declared `old_pattern`, the check greps the repo for call sites
  (a symbol the planner named, not one mined from prose), cross-references
  against `files_likely_touched` across the domain's subtasks, and emits
  `UNCOVERED_MIGRATION_SURFACE` when > 5 files are uncovered. This used to
  regex `intent`/`investigation_notes` for phrases like "replaces direct
  `X`" — measured on run `19a70d96`, every extraction was a stopword
  (`with` → 332 files, `both` → 178, `task` → 168) and not one was a real
  symbol. The regex is deleted; the field is required-by-convention but
  not schema-required, since most subtasks replace nothing.

  Whether `old_pattern` is actually a real, grep-pastable identifier — not
  a stopword restated from the subtask's own prose — was initially
  enforced by a mechanical shape check (`_BARE_LOWERCASE_WORD_RE`), itself
  a *Language-to-JSON* violation just relocated. It is replaced by a
  required sibling field, `is_real_identifier: bool` — the planner's own
  attestation, made at the same time it names the pattern.
  `_check_migration_surface` skips any target where `is_real_identifier`
  is false or absent, and never re-derives the judgment itself (mirroring
  `performs_replacement` and `artifact_paths` self-reporting elsewhere).

  Because `migration_targets` is optional, a planner that omits it
  entirely produces silent agreement — nothing to check. A narrower check
  closes the common case: the schema also carries a self-reported
  `performs_replacement: bool` sibling, and
  `_check_migration_targets_declared()` flags `MIGRATION_TARGETS_MISSING`
  when a subtask sets it true but declares no `migration_targets`. This is
  a same-worker, same-call internal-consistency check, explicitly **not an
  independent witness** — a planner wrong on both fields in the same
  direction still defeats it. It narrows the silent-miss window to
  "self-consistently wrong," not "forgot one field."

- **Cross-domain (advisory).** `_warn_layer_gaps()` runs on the
  reconciled plan before scheduling and surfaces two heuristic warnings:
  (1) a subtask modifies `schema.prisma` but no subtask touches seed
  or migration files (database initialization gap); (2) a subtask's
  `provides` tags contain env/bootstrap/secret/credential keywords but
  no subtask touches `.env.example` or env documentation (env-contract
  gap). These are advisory `log()` warnings following the same pattern
  as `_warn_cross_planner_file_overlap()`.

### Provider-subset subtasks (advisory)

A planner does not always know that a *sibling subtask* in the same plan
will produce another subtask's entire deliverable. The common shape: a code
subtask lists a test file in its `files_likely_touched` and commits that
test edit in the same commit, while a separate test-only subtask — scheduled
a wave later, `requires`-ing a tag the code subtask `provides`, whose whole
surface is that same test file — reaches its worker with nothing left to
commit. The mechanical no-commits gate then fails it, and (before the
mid-run satisfied rescue, §8) the run loops to a wave death.

`_warn_provider_subset_subtasks()` surfaces this one phase earlier. Reusing
`_build_predecessor_graph` (so "predecessor" matches the scheduler exactly),
it flags any subtask whose entire `files_likely_touched` set is a subset of
the union of its **direct** ordered predecessors' files (direct edges only —
a subtask owned only by an indirect predecessor is left unflagged, since the
§8 rescue catches it anyway). It is **advisory only — never a drop**: a
subtask may make a genuinely distinct edit to a shared file, and silently
deleting it would be strictly worse than an extra worker round. The actual
safety net is the post-execution mid-run satisfied rescue (§8 *The mid-run
sibling case*), which settles such a subtask `complete` when its criteria
are already met on the run-branch HEAD; this warning just lets the operator
re-frame the plan before workers run.

### Artifact passing between subtasks

Some subtasks produce a structured deliverable that a downstream subtask
consumes — a research spec, a parameter set, a design summary. Committing
such a deliverable to the worktree is the wrong shape: it pollutes the
merged branch with a coordination document users did not ask for, and
relies on the downstream worker inferring relevance from commit history
rather than a declared contract.

The contract for cross-subtask deliverables is therefore separate from the
contract for code changes. A producing subtask returns an `artifacts` field
on its implementer result; the orchestrator persists them to
`<state-root>/runs/<run-id>/artifacts/<sid>.json` and injects them into the
prompts of subtasks whose predecessor graph names the producer.
Code-implementation subtasks emit an absent or empty field.

The routing channel is the predecessor graph already used for wave
ordering: a subtask B receives A's artifacts when B declares
`depends_on: ["A"]` or a `requires` entry matching one of A's `provides`. No
separate dependency mechanism is needed, and tight-context discipline is
preserved — a subtask sees only its declared upstream's artifacts, never
the run-wide set.

The artifacts directory is owned by the orchestrator; workers never write
there directly — the payload travels through the implementer result JSON
and the orchestrator materializes the file, keeping the `.leerie/`
protected boundary intact. A producer subtask whose only output is
artifacts may return `status: "complete"` with no commits —
`check_branch_has_commits` treats a non-empty `artifacts` field as a
substitute deliverable. Subtasks sharing production code still use the
ordinary worktree/commit/integration model.

### Why waves are sequential

Each wave's worktrees are branched from the integrated result of all prior
waves. A subtask therefore always sees the complete, validated output of
everything it depends on — never a half-finished intermediate state. Sequential
waves are what make "this subtask depends on that one" mean something concrete:
the dependency is satisfied in the filesystem the dependent subtask starts from.

---

## 5½. ENRIC grounding — codebase-structural decomposition (P6 + P1)

Leerie's planner is a judgment worker: it reads a task description and a
light grep/glob seed of the codebase, then decomposes. The weakness is
structural: an LLM instructed to "investigate the repo" in a prompt forms
shallow, prompt-driven splits. The ENRIC framework identifies two principles
that close this gap — P6 (questions shaped by the codebase itself) and P1
(Task-Context Fit as the sizing variable). Telemetry over 200 runs confirms
the failure: 20% of runs exhaust the implementer's context budget
mid-execution, 84% of those in migration sweeps where the planner packed
30–65 files into one subtask.

### P6 — codebase structural map (foundation)

P6's thesis: decomposition quality comes from the system *knowing* the codebase
before the LLM acts, so shallow splits are structurally impossible. The
mechanism is a **repo-map** — a tree-sitter symbol/reference graph computed
once and mtime-cached at `<state-root>/repo-map-cache/`.

**`_build_repo_map(repo_root) → RepoMap`** extracts definitions, references,
and signatures per source file via tree-sitter `tags.scm` queries (multi-language
via prebuilt parsers; `universal-ctags`/`ast-grep` fallback for long-tail
languages). It builds a file/symbol reference graph (defs→refs edges), caching
by file mtime so only changed files re-parse. Measured on a real Python repo:
4 ms/file, 450-node graph, warm re-parse negligible.

**`_rank_repo_map(repo_map, seed_files, seed_symbols) → ranked_subgraph`** runs
personalized PageRank biased toward the current task's files and symbols,
emits a k-hop ego-graph, and binary-search-fits the result to a token budget
(default ~1 k tokens, a new `DEFAULT_CAPS["repo_map_tokens"]` entry), ranking
top symbols at prompt extremes for recency bias. Measured: 13/15 task-relevant
symbols in the top-ranked subgraph in 0.92 s on a 450-node graph.

The ranked subgraph is injected into the planner context (and, per subtask, into
the splitter, re-ranked to each node's files). This generalizes the existing
`_glob_task_references` seed — P6 is a structurally richer version of
what leerie already does in embryo. A `--skip-repo-map` flag
(`LEERIE_SKIP_REPO_MAP` / `skip_repo_map` in `leerie.toml`) degrades to the
current grep/glob-only planner for repos where tree-sitter cannot parse.

### P1 — recursive fit-judge (mechanism)

P1's thesis: *size is irrelevant; fit is the variable.* A subtask is correctly
decomposed when its scope and context are co-minimized — not when it is under N
files. That is a **judgment**, which is tractable; size prediction (§5) is not.

**`fit_judge` worker** scores one subtask's Task-Context Fit as a confidence
0–1 (plus a rationale and a "what is diffuse" field). It is read-only
(`INSPECT_TOOLS`), fed the subtask spec plus its P6-ranked subgraph. The rubric
is P1 (co-minimized scope and context) plus leerie's "single verifiable unit /
one conceptual thing" criterion.

*Measured discriminating power*: on 24 telemetry-labeled subtasks, oversized
subtasks scored a mean of 0.26, well-fit subtasks a mean of 0.84 — a 0.57
separation, 88% accuracy at a **0.70 threshold**
(`DEFAULT_CAPS["decompose_fit_threshold"] = 0.70`). The originally-planned 0.95
threshold over-split 100% of well-fit subtasks (their scores sit at 0.82–0.93);
0.70 was empirically selected, not assumed.

**Splitter — code partitions, LLM labels.** An unconstrained LLM splitter
dropped 14 of 29 files in measured testing (silent under-coverage — the §12
lesson). Migration files are empirically independent: a 29-file sweep had only
3 import edges and 4/29 coupled pairs — a DAG → embarrassingly parallel. The
split mechanism therefore separates by structural type:

- **Migrations (dominant case, 84% of truncations):** `_partition_files(exhaustive_list, ~8)`
  — a *deterministic* chunker that achieves 100% coverage and 0 overlap by
  construction. The exhaustive file list comes from P6 / `_grep_old_pattern`. A
  `splitter` worker only **titles and writes success criteria** for each
  pre-computed chunk; it never decides which files go where.
- **Coupled minority:** the `splitter` worker emits children along structural seams
  that the repo-map exposes, backstopped by the existing `UNCOVERED_MIGRATION_SURFACE`
  check (`_check_migration_surface`) which already rejects any split that fails to
  cover every file.
- **Sub-file (a single dense file):** the mirror image of the many-file migration
  sweep. Both whole-file mechanisms above operate at file granularity, so neither
  can help a subtask whose entire scope is *one* very large, edit-dense file — and
  the coupled-minority LLM splitter, asked to split one file, can only return it
  unchanged. That leaves such a subtask a monolithic unit too large to hold in
  one implementer context, finish before a transport blip, or checkpoint before
  dying. Measured failure (run `5d488583`, subtask feat-005): a 7,041-line file
  with ~85 edit sites failed 9 times across three runs, each restarting from
  scratch because it died before writing a checkpoint. The mechanism decomposes
  *within* the file, deterministically, in two tiers:
  - **Tier 1 — function boundaries.** Tree-sitter symbol spans
    (`_extract_symbol_ranges`, reading `item.span.start_line`/`end_line`) tile
    `[1, EOF]` with no gaps or overlap by construction — the `_partition_files`
    guarantee applied to ordered line spans (inter-symbol gaps attach to the
    preceding region so the union stays exhaustive). Adjacent functions group
    until a region approaches `subfile_split_max_span`.
  - **Tier 2 — line windows.** A *single function* can itself exceed the span cap
    (measured: `executeStepWithHealing` is 1,733 lines — 25% of the file and 34%
    of feat-005's edit sites — 3.7× the largest function in any sibling file).
    Function boundaries cannot break it, so that one region is sub-split into
    contiguous line-windows via the same tiling. This tier needs no range data and
    is also the whole-file fallback when tree-sitter yields no ranges.

  Each child owns a region of the same file, so N children co-own it. This is
  already legitimate downstream: `_schedule()` derives waves only from
  `depends_on`/`requires` (never `files_likely_touched`), so co-owners run the
  same wave in parallel; the phase 2¾ overlap judge's charter is *same exported
  artifact with incompatible APIs* and explicitly excludes "multiple primitive
  extractions in the same parent file"; and integration is a plain `git merge`
  whose 3-way merge clean-merges non-overlapping regions, escalating to the
  integrator worker only on a true textual conflict. `check_planner_output`'s
  advisory `INTRA_DOMAIN_OVERLAP` warning is suppressed for children tagged with
  a co-ownership cluster marker, since the overlap is intentional — an
  accidental same-file overlap between unrelated subtasks still warns.
  `_check_intra_file_surface` is the zero-tolerance analog of
  `_check_migration_surface`: the child regions' union must equal `[1, EOF]`
  and be pairwise-disjoint. Bound: `DEFAULT_CAPS["subfile_split_max_span"]`
  (line-span above which a file or region is split rather than left a leaf —
  anchored to the measured 1,733-vs-≤474 span separation, not
  telemetry-calibrated like the 0.70 fit threshold).

  - **Oversized-file peel (a dense file bundled with a few others).** The tier-1/2
    tiling above operates only on a subtask whose `files_likely_touched` is
    *exactly one* file — a single region can be tiled, a set of files cannot. But
    the dominant real shape of a dense-file subtask is the file plus its test
    file (measured: 382 two-file subtasks across run history, 244 of them
    impl+test). For a small file-set (`1 < len(files) ≤ chunk_size`) where
    **exactly one** file exceeds `subfile_split_max_span` lines, the mechanism
    first *peels*: it splits the subtask into a single-file child scoped to the
    dense file — which re-enters the tier-1/2 tiling above — and a sibling child
    owning the remaining file(s). The peel is deterministic (a line-count probe,
    no worker). It fires only when exactly one file is oversized: zero oversized
    files is nothing to peel, and ≥2 oversized files is genuinely coupled
    multi-file work the LLM splitter should own — both fall through unchanged.
    The peeled children inherit the parent's edges exactly as the migration/tier
    children do, so `_schedule()`/overlap/integration are unaffected.

**`_recursive_decompose(subtask, depth) → list[leaf_subtasks]`** is the loop:

```
conf = fit_judge(subtask)                         # P1 confidence, measured discriminating
                                                  # WorkerError on the judge call → degrade
                                                  # to leaf, same as the two guards below
if conf >= 0.70 or depth >= MAX_DEPTH(5):
    return [subtask]                              # leaf
children = split(subtask)                         # code-partition (migration) or
                                                  # LLM-splitter (coupled);
                                                  # WorkerError on the coupled-minority
                                                  # splitter call → degrade to leaf too
for each child: recurse(child, depth + 1)
# no-progress guard: 2 consecutive rounds where no child's conf rises above
# the parent's → accept parent as leaf + emit warning
flatten the tree → leaf subtasks
```

Bounds: `DEFAULT_CAPS["decompose_max_depth"] = 5`,
`DEFAULT_CAPS["decompose_fit_threshold"] = 0.70` (measured),
`DEFAULT_CAPS["decompose_noprogress_rounds"] = 2`. Every judge/split call
passes through `st.bump_workers` — a runaway tree hits the worker-cap backstop.

**"This does not split" is a valid answer.** The splitter may return an empty
`children` array, and that reaches the same leaf disposition as the depth cap
and the no-progress guard. The schema previously forbade it (`minItems: 1`),
which made the honest answer unrepresentable: across the corpus the splitter
returned an empty array 43 times and exactly *one* child 43 more times — and a
"split" into a single child is a no-op wearing a costume. Every one of those 43
empty returns was rejected and retried, even though `_recursive_decompose`
already accepted "no children" as a leaf. The consumer was correct; the schema
rejected the payload before the consumer could ever see it.

**A `WorkerError` from either the `fit_judge` call or the coupled-minority
`splitter` call degrades that node to leaf**, the same disposition the
depth cap and the no-progress guard above reach when they cannot establish
a confident split: an infrastructure failure mid-decomposition is
uncertainty about *this* node, not a reason to discard every fit/split
decision the run has already paid for elsewhere in the tree. The
migration-path splitter (label-only mode, invoked from
`_label_migration_chunks`) already carried this guard — a crash there
keeps the code-computed file partition and falls back to deterministic
per-chunk labels, since `_partition_files` already owns the split. `phase_plan`
snapshots decomposition progress into state as each top-level subtask
finishes expanding, mirroring how `plan_snapshot` already persists the plan
after `_schedule()`; like `plan_snapshot`, this is diagnostic only. The
resumable-planning cursor (§6 *Resumable planning*) checkpoints at the
coarser `phase_plan` granularity (`plans_after_plan`, written only after
the whole phase returns), so a resume that lands mid-decomposition
re-invokes `phase_plan` from scratch rather than rehydrating from
`decompose_snapshot`'s partial leaves.

### Wire-in to `phase_plan`

After each per-category planner returns its first-pass subtasks, `_recursive_decompose`
runs over each subtask; the union of all leaves is the flat set. The **existing**
path then continues unchanged: `phase_reconcile` → `phase_overlap_judge` →
`_schedule()` → `_validate_plan` → `_write_plan` → `phase_execute`. The existing
self-scored `decomposition_quality` axis is demoted to a non-gating advisory
self-report: the independent `fit_judge` is now the authoritative
decomposition-quality gate (removing the self-grading bias BAGEN documents).
The axis remains in the planner schema as a signal, but `check_planner_output`
no longer escalates on it.

`task_understanding` does **not** independently gate the planner. The
naive extension of this section's pattern — replace the self-graded
`task_understanding` score with an independent judge, as was done for
`decomposition_quality` above — was tried and measurably falsified: an
independent judge asked to score *understanding* against a plan that had
silently overridden an explicit, prescribed user instruction still scored
that plan highly, because the plan did, in fact, reflect a correct reading
of the task — it simply chose not to obey it. A planner can understand an
instruction correctly and still not follow it; no variant of an
understanding axis catches that. The axis that actually discriminates is
**instruction adherence**, gated by a dedicated mechanism — see §12
*Instruction adherence is code-enforced*. (A separate gate,
`task_coverage_judge` — §8 *Independent adversarial verification* — later
replaces the `task_understanding` self-score for a distinct question,
plan-vs-task *coverage* rather than *understanding*; it does not reopen
this falsification.)

**Expansion vanishes the parent's id, so it owes the inbound-reference rewrite**
(§5 *Id-vanishing operations*). A first-pass sibling that declared
`depends_on: [parent]` must come out depending on every leaf the parent became;
otherwise the edge dangles and `_validate_plan` kills the run after the full
planner/fit_judge/splitter spend. The rewrite happens at **two** levels, because
neither alone sees every vanished id:

- **Intra-generation, inside `_recursive_decompose`.** The splitter is told it may
  give a child `depends_on` on a *sibling* child. When that sibling then recurses
  and splits again, its id vanishes mid-tree — visible only to the frame that
  created it, never to `phase_plan`, which sees fully-flattened leaves and so never
  learns the intermediate existed. Each generation therefore remaps its own
  children's sibling edges before returning. On the migration path this is provably
  a no-op: `_migration_child` builds children in code and no child can name a
  sibling.
- **Cross-subtask, in `phase_plan`.** `_recursive_decompose` takes a single subtask
  and has no access to its siblings — the same structural reason the merge rewrite
  lives in the plan-level apply and not inside a per-pair helper. `phase_plan` holds
  every plan, so it records parent → leaf-ids across the expansion loop and applies
  one pass afterward. The pass must run over **all** plans after **all** expand:
  the dependent may live in another category's plan than the parent.

Both levels use the same rewrite as merge/drop, with the same dedup discipline. The
`fit_judge`/`splitter` workers are never asked to preserve the graph — per §12 the
edges are code-enforced, and the splitter's id convention is a prompt example, not a
guarantee the remap may rely on.

The runtime truncation backstop (`_record_run_health` surfacing
`truncated_worker_count`) is retained: truncation is now rare, but the signal
remains so that residual cases are observable. A mid-run split path (splitting
a running subtask) is prototype-validated and available as a future addition;
build it only if post-ship telemetry shows residual truncation after P6+P1 ships.

### ENRIC principles mapping

Seven ENRIC principles map to leerie. P3, P4, P5, and P7 are already implemented
(§12 code-enforcement, waves+conformer, parallelize-only-independent,
`--runtime fly` async). P6 and P1 are the gaps that §5½ closes. P2 (prompt
discipline) is partially covered by §12's read-only enforcement on judgment
workers.

---

## 6. Worktree and integration model

### Isolation

Parallel workers that write to a shared directory race. Leerie gives each
implementer its own git worktree — an isolated checkout backed by the same
repository. Parallel writes land in separate working directories and never
collide. This is what makes "a wave of parallel implementers" safe even when
two of them touch the same file.

### The run identifier

Every run has a unique identifier `run_id` that is the container/machine ID
assigned by the container runtime:

- **Fly runtime**: the machine ID returned by `flyctl machine run`
  (e.g. `e286535ab70d89`).
- **Local runtime**: the container ID written by `nerdctl run --cidfile`
  (full 64-character hex digest).

The ID is known at container creation time, before the orchestrator starts —
no deferred computation, no rename. The launcher passes it via `--run-id`.

The same string appears in three places: the run branch name
(`leerie/runs/<run-id>`), the per-run state directory
(`<state-root>/runs/<run-id>/`), and the PR body — a user looking at any one
can grep for the others; for Fly runs it is also the machine ID in the Fly
dashboard.

A run identifier is *per-run*, not per-repository: two concurrent invocations
in the same repository get two different `run_id`s, so their branches, state
directories, worktrees, and PRs are disjoint by construction. There is no
shared "staging" namespace to collide on.

### The run branch as an integration buffer

Integration does not happen on the user's working branch. Each run has its
own **run branch** (`leerie/runs/<run-id>`) that receives every subtask's
work; the user's branch is untouched until the run finishes and succeeds, so
a failed or messy integration never lands on the branch the user cares
about. Multiple runs in the same repository each have their own run branch
and integrate independently.

Subtask branches live under a sibling namespace: `leerie/subtasks/<run-id>/<sid>`.
The run-branch and subtask-branch prefixes are deliberately disjoint
(`leerie/runs/…` vs. `leerie/subtasks/…`) because git's loose ref store
cannot hold both a ref AT a path and a ref UNDER that same path
simultaneously — `leerie/<run-id>` as a leaf ref and
`leerie/<run-id>/<sid>` as a child ref would collide on the first
`git worktree add`. Sibling prefixes make the collision structurally
impossible.

**External collision hazard.** The same loose-ref-store constraint
applies externally: a pre-existing user branch named exactly `leerie`
(without any `/` suffix) occupies the path that `leerie/runs/…` and
`leerie/subtasks/…` need as a directory. The orchestrator's `preflight()`
checks for this and `die()`s with an actionable rename suggestion;
`setup-run.sh` repeats the check as defense-in-depth for the `resume`
path, which skips `preflight()`.

Integration is **incremental, one wave at a time**. Each wave's results are
merged into the run branch and the merged result is validated before the
next wave starts. Conflicts surface one wave at a time, close to the work
that caused them — not all at once at the end, where they are far harder to
untangle.

**Partial-wave integration.** When some subtasks in a wave fail while
others succeed, the orchestrator integrates the *successful* subtasks
into the run branch before exiting with the failure diagnostic.
`integrate_wave` already filters for `status == "complete"` and skips
failed/blocked subtasks, so partial integration is a matter of invocation
order: `integrate_wave` runs before `die()`. `completed_waves` is **not**
incremented for a partially-integrated wave, so `resume` re-enters it.
Already-integrated subtask branches produce a no-op `git merge --no-ff`
("Already up to date.", exit 0) — idempotent on branches that are already
ancestors of the run branch.

### The run branch is the resume contract

The run branch is also the durable record of everything completed so far:
every integrated wave is a commit on it, and this is what `resume` is
built on. Run state records *which wave* to resume from; the run branch
holds *the work* every prior wave produced — together they are the entire
resume contract. Within a wave, `phase_execute` skips subtasks whose
`subtask_status` is already `complete`; when every subtask in a wave is
already complete, the wave is skipped entirely and `completed_waves` is
advanced.

This places one hard requirement on the design: **a run branch, once
created, is never reset.** Setup creates it only if it does not already
exist (a `run_id` collision against an existing branch is a preflight
failure, not a silent overwrite). On resume the branch already carries the
completed waves' commits, and resetting it would silently discard them
while the wave loop resumed past them, delivering a final result missing
everything before the interruption. "Create if absent, never reset" is the
invariant the resume guarantee depends on.

When more than one run is in flight in the same repository, `resume`
needs to know *which* run to resume; the discovery scans
`<state-root>/runs/*/state.json`. An explicit run-id always wins and must
match exactly (an unknown id fails closed — resume never falls back to a
guess when the user named a run). Without one, the orchestrator considers
only the runs that are actually resumable — those whose derived status is
`in-progress`, `paused`, or `incomplete` — and picks the most recent. A
run that has finished (`done`, `done-pushed-pr`) has nothing to resume, and
one that needs operator attention first (`seed-failed`, `sync-failed`) is
never auto-picked: both are listed, not chosen. When no run is resumable,
or the newest cannot be identified, `resume` lists the candidates and
requires an explicit id.

Recency is read from `started_at`, falling back to the state file's mtime
when absent — a run missing a timestamp must never sort above a real one
and win the auto-pick by accident.

Auto-picking a still-running run is not an error: the run directory's
`flock` (see *Single owner per run dir*) rejects the second orchestrator,
so the outcome is a clear "already running", not a double-drive.

The resumable-status narrowing belongs to `resume` alone. The read-only
verbs sharing the same run-selection logic (`--report`, `--phase`) skip it:
they act on a run's *records*, not its remaining work, and a finished run
is the ordinary thing to report on.

**The wave loop above is not the whole resume story.** It describes resume
*after* scheduling — once `waves` exists. But the planning pipeline
(classify → provision → plan → reconcile → overlap-judge → adherence-gate →
off-tree/satisfied filters → schedule) can itself run 30+ minutes and spend
real budget before `waves` is ever written, and before the per-phase
checkpoint model below existed none of that was resumable: a pause anywhere
in planning threw away everything before it.

### Resumable planning — a per-phase checkpoint cursor, not a `waves` gate

Resume must not be gated *solely* on whether scheduling finished. Every
planning phase that mutates the plan persists its output through `State`
as it completes — the same "write to `state.json` only via `State.save()`"
discipline this document requires everywhere else (§12) — and `resume`
re-enters at the first planning phase whose output has not yet been
persisted, rather than either re-running the whole pipeline or refusing
to resume at all.

**The checkpoint model.** Each planning phase that mutates `plans` writes
its result to a phase-named key in `state.json` immediately after the
phase completes — e.g. `plans_after_reconcile` after `phase_reconcile`,
`plans_after_overlap_judge` after `phase_overlap_judge` — mirroring how
`plan_snapshot` already persists `{"subtasks", "waves"}` right after
`_schedule()` and `decompose_snapshot` already persists the flattened leaf
list as each top-level subtask finishes expanding (§5½). Every one of
these keys must be added to `STATE_FIELDS` (`orchestrator/leerie.py:259`).
`STATE_FIELDS` is a static allowlist checked by `tests/test_state_fields.py`,
not a runtime filter — `State.load()` reads the whole on-disk `state.json`
unconditionally, so an undeclared key is not silently dropped on
`resume`. What actually happens is louder: a new `st.data[...]` write
without a matching `STATE_FIELDS` entry fails
`test_state_fields.py::test_every_st_data_write_is_declared` immediately,
before the checkpoint can ever ship uncovered.

On `resume`, the orchestrator walks the phase
sequence in order and **skips every phase whose output key is already
present**, re-entering the pipeline at the first phase with no persisted
output — reusing the persisted `plans` as that phase's input rather than
re-deriving it. A phase that already ran is never billed again; only the
phases still needed to reach `_schedule()`/`_write_plan()` spend further
budget.

**Why the resume cursor is the *presence of an output key*, and not
`current_phase` — the load-bearing distinction.** Every planning phase
stamps `current_phase` at phase *entry*, before it spends anything, so the
value on disk when a pause lands mid-phase names the phase that was
interrupted, not the last one that finished. Treating `current_phase` as
"this phase is done" would resume by skipping a phase that only *started*
and never produced output — silently carrying forward a half-built plan as
complete. The correctness property the checkpoint model depends on: a
phase's output key is written **only after** the phase fully completes and
`st.save()`s, never at entry — the same ordering already pinned for
`plan_snapshot` (`tests/test_plan_snapshot_wiring.py`) and
`decompose_snapshot`'s crash barrier (§5½,
`tests/test_decompose_snapshot.py`). `current_phase` remains a useful
secondary, human-readable hint, but is never the resume cursor itself.

This makes the checkpoint approach safe by construction, not merely
convenient: `_schedule()` re-sorts every wave by subtask id, so the wave
partition is a pure function of the dependency graph and lexicographic
ids, independent of the order in which the persisted-and-reloaded `plans`
list happens to iterate. A fresh run and a checkpoint-then-resume of the
same task therefore produce byte-identical `waves`.

**The satisfied-probe sweep needs finer-than-phase granularity.**
Phase-level checkpointing alone is not enough for the satisfied-probe
sweep (§8 *Already-satisfied subtask elimination*): that sweep fans out
one probe per subtask and can itself run for minutes, so a pause
mid-sweep would otherwise re-probe every subtask from scratch on resume,
including the ones already judged. The fix persists each subtask's
verdict — `satisfied`, `evidence`, and the fact that it was `checked` —
into a `satisfied_probe_cache` keyed by subtask id as soon as that
subtask's probe returns, rather than only in the aggregate after the
whole sweep's `gather` completes. On resume, a subtask with a cached
verdict is not re-probed at all; only the subtasks the pause interrupted
mid-flight are sent back through the probe. A probe that crashes
(`WorkerError`) is deliberately **not** cached — the existing
crash-keeps-the-subtask disposition (§8) means no verdict was actually
reached, and caching "kept" for a crash would wrongly skip re-probing a
subtask that was never really judged.

**Correctness-critical: the cache is scoped to the base-tree commit sha,
and a resume across a moved `HEAD` invalidates it.** This is a distinct
hazard from the mid-run sibling case §8 describes (where the run's *own*
later wave changes what's satisfied): here the gap is between a *pause*
and a *resume* of the same run, during which some other process —
commonly a sibling run merging its own PR into the same base branch — can
move `HEAD` out from under a cached verdict. A stale hit is not a harmless
inefficiency: it can keep a subtask whose deliverable now already exists
(which a fresh probe would catch), or drop one that no longer holds
(silently discarding real work). So the cache is keyed on the base commit
sha recorded at probe time, and any cached verdict whose sha no longer
matches `HEAD` on resume is treated as absent — re-probed, never trusted
stale. This is mandatory, not an optional refinement: correctness of a
resumed run must not depend on nothing else having touched the repository
while it was paused.

**Budget-check resume.** Once plans are checkpointed per phase, a run
that stopped at the post-`_schedule()` budget-feasibility gate (§13) is no
longer a dead end: `subtasks`/`waves` are already recoverable from the
`plan_snapshot` checkpoint, so `resume` can rehydrate them and re-run
only the budget check — under a higher `--max-workers` or
`--skip-budget-check` — instead of discarding the plan and forcing a
from-scratch re-run.

### Single owner per run dir

`resume` picks a run, but does not by itself prevent the *same* run being
resumed twice. The hazard is concrete: a user invokes `resume` while the
original orchestrator is still alive, the launcher spawns a second one, and
both now race on the same `state.json` and run-branch worktrees — both
spawn workers, both write conformance entries, both interleave log lines
into the same `orchestrator.log`. State diverges and worker budget burns on
duplicate work.

The architectural property: **at most one orchestrator owns a run
directory at any time.** The mechanism is an exclusive advisory flock on
the run directory, acquired in `State.__init__` and released by the kernel
on process exit (clean, SIGTERM, or SIGKILL — no manual pidfile cleanup, no
`/proc` liveness check, no PID-recycling false positives). A second
orchestrator that tries to construct `State` on the same run dir gets
`StateLockedError` and exits with `EXIT_LOCKED`; the launcher routes the
user to `leerie resume <run-id>` instead, which attaches to the live log
stream rather than spawning a duplicate.

Why the *directory* and not `state.json`: `State.save()`'s atomic
`tmp + rename` swaps state.json's inode every save, so a lock on
state.json's fd would be orphaned from the new inode at every save,
opening a window where a racing `resume` could acquire the unlocked
replacement. Directory inodes are never replaced — the lock fd stays
valid for the process lifetime.

Defense in depth: the launcher heredoc takes an opportunistic flock probe
before invoking the orchestrator subprocess (fast-path refusal, saves
spawning a Python process that would just die in startup). The
orchestrator's `State.__init__` flock acquire is the load-bearing
enforcement and catches anything the launcher misses (manual `python3
leerie.py resume`, future verbs, debugging).

What this does *not* prevent: cross-host races — the lock is per-host.
This is fine in practice: on Fly each run is pinned to a specific Machine
via `fly-machine.json`, and only that Machine runs the orchestrator; on
local runs the host is the user's workstation. There is no architectural
path today by which two hosts could attach to the same state directory
simultaneously.

**Concurrent-spawn race between two `resume` launches, and stale-pid
contagion.** Two `resume` invocations against the same run can each pass
the launcher's fast-path probe (`LOCK_EX | LOCK_NB` then immediate
`LOCK_UN` — it tests and releases) and each spawn a child orchestrator.
The two `State.__init__` calls race in the kernel; the loser (B) gets
`BlockingIOError` and exits `EXIT_LOCKED=75`. The hazard is not the
duplicate spawn (correctly caught) but that the launcher writes
`orchestrator.pid` *between* `Popen` and the child's `State.__init__` — by
the time B exits 75, B's pid is already in the file, silently overwriting
winner A's. Every downstream reader of `orchestrator.pid` (the `resume`
tail watcher's `kill -0` liveness check, `finalize --force`'s liveness
gate) is then wrong about A: a false "orchestrator exited" banner, or a
`finished_at` patched onto A's state mid-run.

The fix is two-sided: `_launch_script` polls `Popen` briefly for `rc=75`
before writing the pid file, and readers do not trust the pid file as the
sole liveness oracle — both the tail watcher and `finalize --force`
cross-check via a `/proc` scan for any process whose argv contains
`orchestrator/leerie.py` AND this run-id. Either anchor catching the live
orchestrator is sufficient to declare "alive," making the pid file
advisory: even a future race produces at worst a false-positive REFUSE,
never silent corruption.

### Never a repository-global git operation

The user's repo is bind-mounted whole, so `.git` is SHARED — with the host,
and with every other container. A repository-global operation therefore
reaches state this run does not own and cannot see.

`git worktree prune` is the case that bit. It has no grace period (the
three-month `gc.worktreePruneExpire` applies to `git gc`, not to an
explicit prune), so it drops the registration of every worktree whose path
is absent from the pruning process's namespace — including each host-side
`/tmp/tmp.*/rebase-<run-id>` the finalize rebase creates, which no
container can see. The result: a rebase worktree git has forgotten while
its directory still exists.

So no call site runs one. `prune_leerie_worktrees` (shell) and
`_prune_leerie_worktrees` (Python) ask git what it *would* prune, attribute
each entry to a path, and remove only entries under a root the caller
names; an entry that cannot be attributed is left strictly alone. Both pin
`LC_ALL=C`, because git wraps that output in gettext and a translated
prefix silently matches nothing — a no-op indistinguishable from "nothing
was stale", worse than the bare prune it replaced.

The rule generalises: any git verb that acts on the whole repository rather
than on this run's own refs and worktrees is out of bounds from inside a
container.

### One task, one run

The flock above makes a *run directory* single-owner. It says nothing about
two runs working the same **task**, because `run_id` is the container id —
two launches of byte-identical task text are, to each other, invisible.

Measured: one brief ran twice three minutes apart, for $72.21 across 173
worker calls, and produced two architecturally incompatible branches with
14 files in collision — two PRs whose merge order decides which design
survives. Neither run was wrong; nothing told either one the other existed
(docs/POSTMORTEM-2026-08-14.md, F10).

So a run records a fingerprint of its task text (`task_sha256` on
`run.json`) and, before spawning its first worker, refuses to start when
another **live** run carries the same one. "Live" means started and not
finished, killed or paused: a completed run sharing a fingerprint is an
ordinary re-run. The check sits at the last cheap moment — after state
exists, before this task's first worker (`preflight()`'s smoke test and
the dep-capture backstop both run earlier and both cost). The refusal
names the other run and the commands that resolve it (`leerie attach`,
`leerie kill`).

Running the same brief twice on purpose is a real thing to want, so there
is an escape hatch, `LEERIE_ALLOW_DUPLICATE_TASK` — an environment
variable rather than a CLI flag because it should be *stated* rather than
discovered mid-argument-list. With it set the duplicate is still
announced, not silenced.

Fingerprinting the text rather than the resolved plan is the conservative
choice both ways: two different briefs that would produce the same plan
are not caught (also not obviously wrong to run together), and two
identical briefs are caught before the planner has been paid for.


### Why merge, not cherry-pick

Subtask branches are integrated into the run branch by merging, not
cherry-picking. A merge records ancestry, giving the integrator a real
common base for three-way conflict resolution: far more auto-resolves, and
only genuine conflicts surface. Cherry-pick copies commits without
ancestry, so it has a weaker base and produces more spurious conflicts.
Recorded ancestry also makes re-integration idempotent and the run's
history a true audit trail rather than duplicated commits.

On the success path a subtask branch may contain commits from two distinct
workers — the implementer's code change and any conformer fixes (§9
*Post-work conformance*) that landed before integration — both flowing
through the same merge; the integrator does not need to know which worker
authored which commit. Conformer commits are conventionally prefixed
`conformer:` in their subject so a reviewer can identify them in `git log`,
and the orchestrator emits a non-blocking warning for any conformer commit
lacking the prefix.

### Conflict resolution is behavioral, not textual

When two subtasks' branches conflict, resolving to git's satisfaction is
not enough: a textually clean merge can still silently break the behavior
one of the subtasks was validated against.

So conflict resolution is defined behaviorally. The integrator reads the
intent and success-criteria notes of *every* subtask whose work is part of
the conflicting merge — the incoming subtask and every already-integrated
subtask it collides with — and resolves the merge so each side's intent is
preserved. Resolving a *semantic* conflict is what the integrator is for; a
purely textual merge can satisfy git while silently breaking behavior one
side was validated against, and only a worker that understands intent can
avoid that.

The mechanical re-check that *catches* a merge that broke the tree runs
immediately after: once the integrator commits, the orchestrator scans the
integrated worktree for unresolved conflict markers (`<<<<<<<`); a merge
that left markers behind aborts the run. Per-wave quality stops there —
per-subtask quality is the implementer's confidence gate (§8), and Leerie
does not re-run subtask criteria at the wave boundary (that role belonged
to an earlier wave-level validator, removed when the criteria file became
informational, §8/§9).

After the *final* wave integrates — once the staging tree contains every
subtask's work — one conformer pass runs on the integrated tree as a
whole. It is the same conformer the per-subtask phase spawns (§9), pointed
at the staging worktree with `DIFF_BASE` set to the user's working branch
(the PR's eventual base) so the diff under review is what the PR will
contain. The pass catches drift that only manifests once two subtasks
co-exist: a lint rule sensitive to file count, an import collision that
compiled cleanly in isolation, a test fixture two implementers each
augmented incompatibly. Its findings are advisory in the same sense as the
per-subtask phase (§9) — the orchestrator never blocks finalize on them;
residuals surface as warnings on state and in the PR body. The pass is
bounded by the same `conformance_rounds` cap and per-run worker budget; its
`claude -p` invocation has no special standing.

This pass always measures the repo's **canonical** build/lint/test
commands, never a delta proxy — distinguishing it from the per-subtask
phase, which may run a diff-scoped proxy instead (§9 *Per-subtask scope: a
delta proxy, not the suite*). The three failures above are precisely the
ones no diff-scoped selection can see: each arises from subtasks
co-existing, not from any one subtask's diff, so a per-subtask selection
would exclude them by construction. Together with the base-health baseline
these are the only two places a run executes the whole suite.

### When integration cannot succeed

Two outcomes are not failures of the integrator but facts about the work:

- **A `resolved` claim is verified, not trusted.** The orchestrator confirms an
  integrator that reports success actually completed the merge — a worker
  claiming to have finished while leaving the merge incomplete is treated as a
  failure, the same way an implementer claiming success while committing
  nothing is.
- **Genuinely irreconcilable intents are a design conflict.** If two subtasks
  want contradictory things, no merge can satisfy both — that is a problem with
  the decomposition or the task, not a merge to be papered over. The
  orchestrator stops the run, leaves the run branch intact at the last
  fully-integrated wave, and reports the conflict for a human to resolve. An
  unresolved conflict never proceeds silently onto a corrupt run-branch state.

### Finalization

The final step turns the completed run branch into a reviewable artifact
and never touches the user's working branch.

**The run branch is the integration artifact.** Every wave's work is
already integrated on `leerie/runs/<run-id>`. Leerie does not merge the
run branch into the working branch locally — that would duplicate the
same change in two places (a local commit and a PR) and put the working
branch in a state the user did not request. The working branch is the same
ref at the end of a run as at the start; the PR is the proposal to change
that.

**Push and PR happen on the host, after the container exits.** The
container's job is the LLM work plus deterministic integration of every
wave into `leerie/runs/<run-id>`. Once integration is done, the container
exits cleanly and the launcher takes over: it reads `run.json`'s
`finished_at` sentinel, then runs `git push` and `gh pr create` on the
host.

This boundary is load-bearing. The container exists to bound worker
subprocess subtrees (DESIGN §6 *Worker subtree termination*), not to be a
git/gh client. Auth state — gh tokens, SSH agent sockets, Claude Code's
OAuth token in macOS Keychain — lives in host processes that don't
traverse the Lima VM boundary cleanly. In Bedrock mode
(`CLAUDE_CODE_USE_BEDROCK=1` detected in any settings file), the launcher
additionally stages `~/.aws/` read-only so the AWS SDK credential chain
can resolve SSO tokens inside the container; `awsAuthRefresh` (interactive
`aws sso login`) remains a host-side operation enforced by a preflight
check before the container starts. Bind-mounting that state into the
container was a leaky workaround for a structural mismatch: on macOS the
SSH agent socket can't cross the Lima VM boundary, the gh token bind mount
catches stale states, and Claude Code's OAuth token is in Keychain rather
than any mountable file. Moving the network-y phases to the host
eliminates all of that — the host already has working git/gh/ssh auth.

**Local runs** hand off through `run.json` on the bind-mounted host
filesystem. The orchestrator writes `finished_at` and exits 0; the
launcher reads that field and proceeds with push + PR. If the container
exits non-zero (an unrecoverable error mid-run), the launcher does not
push — nothing changed on disk beyond what the user already saw in the
worker logs.

**Remote runs** (Fly.io `--runtime fly`) face the same auth boundary from
the other direction: the run branch and `.leerie/runs/<run-id>/` state live
on the Fly Machine's filesystem, not on the host. The launcher resolves
this with a **stream-back** step before the host-side finalize runs.

The orchestration is a clean-exit EXIT trap (`decide_teardown` in
`scripts/remote/provision.sh`) so sync gates destroy, and push + PR sit
between them on the host:

1. The orchestrator inside the Machine writes `finished_at` to `run.json`
   and exits 0, exactly as in local mode.
2. The launcher's `decide_teardown` trap fires. On a clean rc
   (`0 | 10 | 75`) it calls `scripts/remote/fetch-branch.sh`, which
   discovers the completed run-id by scanning `.leerie/runs/*/run.json` on
   the Machine for a `finished_at`-bearing, unpushed entry; bundles
   `leerie/runs/<run-id>` **and all `leerie/subtasks/<run-id>/*` branches
   present on the Machine** and pipes it to the host, where `git fetch`
   materialises the branches locally; and tars `.leerie/runs/<run-id>/`
   on the Machine, extracted under `$LEERIE_STATE_HOST_DIR/runs/`.
3. With the run dir now on the host, the trap sources
   `scripts/host-finalize.sh` and calls `host_finalize` directly — push
   + `gh pr create` happen inline, with the host's own auth.
4. **Only if push succeeds does the trap destroy the machine.** Push
   failure leaves the machine running and prints a recovery banner
   pointing at `leerie finalize <run-id>`, mirroring the sync-failure
   recovery path: work is preserved; the user destroys manually via
   `leerie kill <run-id>` when satisfied.

**Controlled exits write `finished_at` eagerly.** `die()` raises
`SystemExit`; `main()`'s `except SystemExit` handler writes `finished_at`
to both `state.json` and `run.json` (best-effort, guarded by `st is not
None`; `state.json` additionally guarded by `st.data.get("task")` so the
handler firing before state was loaded — e.g. a failed `resume` against
incomplete host-side state — never poisons the file with a bare
`{"finished_at": …}` stub) before re-raising, and writes the exit code to
`orchestrator.exit_code` so the tail wrapper can propagate it to
`decide_teardown` (absent that file, the wrapper falls back to exit 0 and
`decide_teardown` takes the clean-exit branch). `fetch_branch` needs the
`finished_at` write to discover the run at all — without it, every
post-setup `die()` triggers the sync-failure banner. The value is
idempotent on `resume`: `phase_finalize` overwrites it with the real
completion time if the run succeeds on retry.

**`finished_at` is a discovery sentinel, not a completion signal.**
The `except SystemExit` handler stamps `finished_at` on *any* post-setup
`die()`, including a mid-wave abort with `completed_waves <
len(waves)` — so a container OOM-killed mid-wave can end up with
`finished_at` set and only some waves integrated. **The completion
signal is `completed_waves == len(waves)`** (or the documented
cleared-but-empty terminal state, `waves = []`); `finished_at` stays
overloaded (the die-path stamp is still needed for discovery) and every
consumer that treats a run as *terminal* instead cross-checks wave
completion against `state.json`. Three gates enforce this:

1. `_derive_run_status` returns `incomplete` (not `done`) when
   `finished_at` is set but `completed_waves < len(waves)` and the run is
   neither killed nor paused.
2. `phase_finalize`'s entry `die()`s rather than writing the real
   `finished_at` when `completed_waves < len(waves)` (a defensive guard —
   the normal wave loop only reaches finalize after all waves integrate).
3. **`host_finalize` (the load-bearing gate) refuses the push+PR** when
   `state.json` shows `completed_waves < len(waves)` and not
   `no_work_required`. All three host-side push paths — the launcher's
   auto-finalize block, `leerie finalize <id>`, and Fly's
   `decide_teardown` — funnel through `host_finalize`, so this one gate
   covers them all. It fails open when `state.json` is absent.

A genuine completion is unaffected: by the time `phase_finalize` and
`host_finalize` run, `completed_waves == len(waves)`.

**Recovery when the orchestrator dies before `finished_at`.** An
uncontrolled exit (SIGKILL, OOM, power loss, or any crash bypassing the
`except SystemExit` handler) before `phase_finalize` leaves a run
`fetch-branch.sh` cannot discover (its predicate requires `finished_at`
set + `pushed_at` unset). `leerie finalize <run-id>` SSHes into the
machine, verifies the orchestrator process is dead (via `orchestrator.pid`
and `/proc/<pid>/cmdline`), patches `finished_at` into `run.json` with
audit fields `recovered_at` and `recovered_via="force-finalize"`, and
falls through to the normal finalize flow. If the orchestrator is still
alive, the non-force path refuses with a message naming the live pid and
suggesting `--force`.

**Subtree collection.** When the orchestrator dies mid-wave, subtask
branches (`leerie/subtasks/<run-id>/<sid>`) may hold committed work never
integrated into the run branch. `finalize` detects un-integrated subtask
branches, runs `setup-run.sh` to ensure the staging worktree exists, and
merges each via `integrate.sh` — conflicts resolved by spawning a
`claude -p` integrator worker (same prompt, schema, and verification as
`integrate_wave()`). Branches the integrator cannot resolve are skipped
and reported. This runs after the `finished_at` patch and before
`fetch_branch` streams the result to the host. `fetch-branch.sh`'s bundle
already carries the raw subtask branches regardless of whether collection
ran, so a crash between subtask completion and integration is still
recoverable on the host.

**`--force`: stop the orchestrator, then collect.** `leerie finalize
<run-id> --force` extends recovery to runs where the orchestrator is
still alive. It SIGTERMs the orchestrator process inside the machine (not
the machine, which must stay running for collection and fetch), waits for
it to die (polling `/proc`, escalating to SIGKILL after 30s), then runs
the same subtree-collection and `finished_at`-patch flow. The SIGTERM
handler runs `_cleanup_on_abnormal_exit(full_purge=False)`, which removes
worktrees but preserves all branches, so `setup-run.sh` (idempotent)
recreates the staging worktree and collection integrates from what
survived.

The local-runtime path runs the same `host_finalize` block inline in the
launcher (no trap needed — launcher and pusher are the same process).
Both paths share `scripts/host-finalize.sh`; the recovery command
`leerie finalize <run-id>` also sources it — three call sites, one
finalize implementation.

That inline block is the *normal* local path, but it only runs when the
launcher reaches the end of a run. A local run interrupted before then —
Ctrl-C after the waves integrated but before `phase_finalize` — leaves a
complete run branch on the host and no `finished_at` in `run.json`. So
`leerie finalize <run-id>` is the recovery verb for local runs too, not
only remote ones: it resolves the runtime to local (no Fly or EC2 sidecar
present), skips the fetch — there is nothing to fetch, the state root is
bind-mounted and the branch is already in the user's repo — and calls
`host_finalize` directly. The completion gate there keys on
`completed_waves`, deliberately not on `finished_at`, precisely so an
interrupted-but-complete run is finalizable while a crashed-mid-wave one
is not.

**`no_push`: intent vs mechanism.** The orchestrator inside the Machine is
*always* invoked with `--no-push` because the Fly Machine has no GitHub
auth — a **mechanism flag**, not the user's preference. The user's actual
launch-time intent lives separately in `fly-machine.json.host_no_push` on
the host (set by `provision.sh` at machine creation) and is propagated
into the Machine via a hidden `--host-no-push true|false` argv flag. The
orchestrator gates `pr_writer` and the `run.json.no_push` it writes on
**intent** (`push_will_happen(no_push, host_no_push)`), not the mechanism
flag, and `host_finalize` reads that intent value to decide whether to
skip push. This split is load-bearing: without it, `pr_writer` never runs
on Fly and the LLM-written PR body is replaced by the deterministic
fallback.

The run branch is pushed to `origin` and a pull request opened via `gh pr
create` against the working branch (HEAD-at-run-start) by default. **This
PR base is overridable** — `--pr-base-branch` / `LEERIE_PR_BASE_BRANCH` /
`pr_base_branch` in `leerie.toml` — without changing where the diff is
computed from: `working_branch` always remains the diff fork-point
(`rev_range = working_branch..run_branch`) regardless of the override, so
the diff base never corrupts if the override branch isn't the actual fork
point.

The PR title and body are written by an LLM worker (`pr_writer`, defaults
to Sonnet) that runs inside the container right before it exits, where
`claude -p` and the bind-mounted repo are both available. The worker
reads the target repo's PR template if one exists (canonical GitHub
locations, then any `PULL_REQUEST_TEMPLATE/` directory) and fills it out
faithfully — preserving HTML comments, leaving checklists unticked unless
the diff demonstrably satisfies them, honoring "delete if N/A" markers;
absent a template it produces a default structure (Summary / What changed
/ Why / Run metadata). Its primary signal is the **commit log** (`git log
--no-merges working_branch..run_branch`), since every implementer/
conformer worker already wrote those messages in domain language landing
a subtask; a capped diff-stat, dirstat, and sampled hunks from the
heaviest-changed files supplement it. Subtask titles pass through
verbatim; the launcher prepends `leerie: ` to the title.

The worker writes `pr_title`/`pr_body`/`pr_template_used` to `run.json` —
the same container→host handoff channel used for
`finished_at`/`pushed_at`/`pr_url` — and the host launcher passes them to
`gh pr create` via `jq`. This is **fail-open**: any failure (worker
error, schema mismatch, timeout, budget exhaustion, oversized payload) is
logged and swallowed, falling back to a deterministic body composed from
`state.json` (`compose_pr_body`). Generating a richer body must never
block finalize success.

When the target repo has multiple templates inside
`PULL_REQUEST_TEMPLATE/`, the alphabetically first `.md` wins by
default; `--pr-template <name>` (also `LEERIE_PR_TEMPLATE`,
`pr_template` in `leerie.toml`) overrides. A non-matching selector is
not fatal — leerie warns and falls back to the alphabetical default.

**Rebase-onto-base before push (`rebaser` worker) — a scoped, fully-agentic
exception to §12.** A run that takes a while can finish against a
`pr_base_branch` that has since moved, so the PR it opens conflicts at
merge time or is stale by review. `host_finalize` addresses this with a
best-effort rebase step, inserted after the empty-branch guard and before
the push, that is deliberately **not** driven by mechanical bash
rebase/conflict/abort logic. Instead a single autonomous worker
(`rebaser`, Sonnet, `EFFORT_DEFAULT_PER_WORKER["rebaser"] = "medium"`,
matching `integrator`) is handed a disposable `git worktree add` copy of
the run branch and told to fetch the latest `pr_base_branch`, rebase the
run's own commits (`working_branch..run_branch`) onto it, resolve any
conflicts itself — preserving both sides' intent — and abort the rebase
itself, leaving the branch untouched, if a conflict is genuinely
semantically irreconcilable rather than merely textually messy. It
reports a schema-validated verdict (`status ∈ {rebased, irreconcilable,
failed}` + `diagnosis`, the same trichotomy shape as
`SCHEMAS["integrator"]`).

This is a **named, deliberately scoped exception** to §12 ("prompts are
advisory, code enforces"), the same shape as the existing
`--dangerously-skip-permissions` carve-out but narrower: scoped to one
worker acting inside one disposable worktree, not a run-level flag. It is
warranted because the procedure itself — switch branches, detect a
conflict, judge whether it is resolvable, resolve or abort — is not
usefully decomposable into mechanically checkable sub-steps the way "did
the merge commit complete" is for `integrator`; the abort-or-resolve
judgment call *is* the task. Validated empirically before adoption: two
live trials (a resolvable adjacent-line conflict, and a genuinely
irreconcilable same-function mutually-exclusive conflict) both produced
the intended outcome, confirmed by inspecting the resulting git state
rather than trusting the worker's self-report.

What stays mechanical, because it is cheap, objective, and losing it would
be a real regression:

- **Isolation.** The worker never operates on the user's real checkout or
  the run's primary worktree — only a disposable `git worktree add` copy,
  matching the containment already used for every other `ACT_TOOLS` +
  `autonomous=True` worker (`implementer`, `conformer`, `integrator` — §6;
  none of their call sites pass a real-repo `cwd`).
- **Post-hoc mechanical verification of the claimed outcome**, not the
  reasoning behind it: on `status: "rebased"`, `host_finalize` confirms
  the worktree has no conflict markers and is not mid-rebase; on
  `status ∈ {irreconcilable, failed}`, it confirms the worktree's tip is
  unchanged from the pre-rebase run branch — the same "don't trust an
  integrator's self-report" discipline §12 already establishes.
- **`working_branch` bookkeeping.** A rebase changes the run branch's
  parent chain, so a naive `working_branch..run_branch` diff after
  rebasing would silently pick up unrelated upstream commits. Only on a
  verified successful rebase does `host_finalize` advance `working_branch`
  to `origin/<pr_base_branch>` (git ref and `run.json`) so
  `rev_range`/`DIFF_BASE` keep computing the PR diff correctly; on an
  aborted or failed rebase, `working_branch` is left untouched.
- **Routing the outcome.** `host_finalize` reads the worker's
  schema-validated verdict and branches on it — push the rebased branch,
  or push the original with the worker's `diagnosis` folded into the PR
  body — without re-deriving *how* the worker reached that verdict.
- **Never blocking the run.** Every branch (worktree-creation failure,
  success, a resolved conflict, an aborted rebase, a worker seam failure,
  or a malformed response) falls through to a push — the rebase step is
  strictly best-effort and never returns non-zero, pauses, or blocks
  finalize.

Two flags control push and PR independently of body composition:

- `--no-push` skips both the push and the PR; the run completes with the
  run branch local-only, for the user to inspect, push, or PR by hand.
- `--no-verify` passes `--no-verify` to `git push`, skipping pre-push hooks.
  Worker commits inside worktrees still run all hooks normally — only the
  push gate is affected. This is the explicit per-invocation override to the
  project's "never skip hooks unless asked" principle; defaults to off.

**A `pre-push` hook is a gate on the host, not on the run — so it is
probed at run start.** git runs `pre-push` in the repository root against
whatever is *checked out*. leerie never checks out the run branch — the push
happens from the user's own checkout, and the finalize rebase uses a
disposable worktree — so a hook that lints, typechecks, or tests the working
tree measures the host's state and can reject a push for reasons no run
could have caused or prevented (a stale `node_modules`, a tool missing from
the hook's PATH, a half-finished dependency bump). Measured: every recorded
push rejection in one operator's state directory was of exactly this shape,
the most expensive arriving after 2h19m and $57 of work.

leerie does not treat that gate as a verdict on the run — it already ran the
equivalent checks in-container — but can't ignore it either, since the push
genuinely won't happen. Because the hook reads the host checkout, which
leerie never modifies during a run, a probe at run start predicts the
finalize outcome by construction. `git push --dry-run` runs the hook and
creates no ref, so the launcher runs it during host preflight and warns
(never refuses — a hook can legitimately fail on a tree the run is about to
fix, and preflight must never become a new way to decline to start). A
chain probes once per *wave* (its jobs share one checkout, so N probes would
compute one answer N times concurrently); a group probes per member, since
its members are separate repositories. Same "fail fast at the cheapest
moment" reasoning as §13's budget feasibility.

**Push and PR are honest about failure.** A push or PR step that fails does
not pretend the run failed: the local work is intact on the run branch. The
orchestrator records what was attempted and what failed in a per-run
sidecar (`run.json` — `pushed_at`, `push_error`, `pr_url`, `pr_error`). Push
failure exits non-zero with a message naming the run branch (where the work
lives), the working branch (the diff fork-point, unchanged from run start),
and the PR base branch (`pr_base_branch`, defaulting to `working_branch`),
shows the captured push output — stderr plus any pre-push hook stdout,
since git forwards a hook's stdout to its own and that's where
`tsc`/`biome` report — and gives the exact retry command. PR-creation
failure is non-fatal: the push already succeeded, so the user gets a
warning with the pushed branch's URL and the exact `gh pr create` retry
command.

**`pushed_at` gates re-finalize by branch position, not mere presence.** A
re-invoked finalize (`leerie finalize`, a second launcher pass, Fly's
`decide_teardown`) must be idempotent, so a run whose `pushed_at` is already
set is normally a no-op — but `pushed_at` records *that* a push happened,
not *what* it pushed, and a finalize that fired while the run was still
integrating can leave it set on a **partial** branch. The short-circuit
instead compares the local run-branch tip against the pushed origin tip:
**equal tips → no-op** (the common case); **origin a strict ancestor of the
local tip (or origin absent) → fast-forward re-push + re-open the PR**,
gated behind the same completion check (`completed_waves == len(waves)`,
itself fail-open on a missing `state.json`). A **diverged** origin is not
treated as a partial push — it keeps the idempotent short-circuit rather
than attempting a push that cannot fast-forward. This prevents a partial
push from permanently wedging a run while preserving idempotency, the
chain wave-skip signal (which reads `pushed_at`, still set, not the tip),
and the invariant `pr_url ⇒ pushed_at`.

**Why push by default.** A successful unattended run that leaves work only
on a local branch is a silent failure mode — the work exists but nothing
signals it needs review. Defaulting to push + PR turns every run into a
reviewable artifact. `--no-push` exists for offline use or repos without a
GitHub remote.

**Branch cleanup at finalize.** After the push + PR (or after the run
completes under `--no-push`), the orchestrator deletes the per-subtask
branches `leerie/subtasks/<run-id>/*` automatically. They were the
mechanism by which parallel implementers committed in isolation; once
their work has been merged into the run branch their individual commit
histories are still reachable from the run branch's `--no-ff` merges, so
the named refs are pure clutter. The **run branch** itself
(`leerie/runs/<run-id>`) is *kept* — it is the PR head, and deleting it
locally before the PR is merged would dangle the PR base reference. The
per-run state directory (`state.json`, `run.json`, logs, criteria,
checkpoints) is also kept as an audit trail. A user who wants to
completely scrub a finished run can do so with
`scripts/cleanup.sh --run-id <id> --branches`.

### Credential strategy

**A containerized headless run must prefer the long-lived token.** The
host's interactive Claude Code session authenticates with a short-lived
subscription OAuth token — corroborated at roughly 8h, though the exact TTL
is undocumented (community reports range 2–15h), so leerie never
hard-codes a duration and instead reads the credential's own authoritative
`expiresAt` field. That token is refreshable only by the interactive host
session that owns it — Claude Code renews it silently in the background
while the user is logged in.

A leerie container cannot participate in that renewal. Keychain and
`~/.claude/.credentials.json` never traverse the Lima VM boundary directly
— the launcher copies whatever credential it resolves into a fresh
`mktemp -d` staging directory and bind-mounts it read-through into the
container, so the container holds a **snapshot**, not a live view. It
cannot refresh a token whose refresh state lives in the host's Keychain,
and Anthropic's refresh flow **rotates the refresh token on every use** —
a container-side refresh, even if it succeeded, would silently invalidate
the host's own copy out from under the still-running interactive session.
Copied subscription credentials are documented upstream as not refreshing
at all once removed from their original context
(anthropics/claude-code#21765). Container-side token refresh is therefore
**architecturally impossible**, not merely unimplemented.

The credential Anthropic's own docs prescribe here is the **long-lived
OAuth token** minted by `claude setup-token` — lasting roughly a year,
meant for CI/automation, and — critically — still authenticating against
the user's Claude subscription rather than the API, preserving leerie's
no-API-key constraint. Sessions authenticated via `CLAUDE_CODE_OAUTH_TOKEN`
never see the "OAuth session expired" failure at all, per Anthropic's own
error reference, because they never depend on the saved, host-refreshed
login — a container invoked with it set is immune to the mid-run expiry
class of failure by construction, not by any retry or backoff leerie adds
on top.

Consequently, credential resolution takes `$CLAUDE_CODE_OAUTH_TOKEN`
**first**, ahead of Keychain and the credentials file — an inversion of the
historical precedence, which silently preferred the 8h token even when a
user had minted a durable one. A user who has set nothing is unaffected:
resolution falls back to Keychain, then the file, as before. The emitted
JSON shape is unchanged regardless of which branch resolved the credential
(`{"claudeAiOauth":{"accessToken":…,"scopes":["user:inference"]}}` — the
`scopes` field is required by the CLI's file-auth path, which rejects a
scope-less blob as "Not logged in"), matching what `seed-auth.sh` already
stages into the container.

**Keychain/file resolution requires a real token, not just non-empty
JSON.** The macOS Keychain entry and `~/.claude/.credentials.json` are
shared with Claude Code's own MCP-server OAuth state — a documented
upstream bug (steipete/CodexBar#1844) lets a background MCP-plugin auth
flow overwrite the shared `Claude Code-credentials` Keychain item with only
`{"mcpOAuth": {...}}`, dropping `claudeAiOauth` (the actual session token)
entirely. That blob is syntactically valid, non-empty JSON, so a
presence-only check accepts it, stages it into the container, and the CLI
fails with "Not logged in" despite the launcher reporting success. Both
the Keychain and on-disk-file branches now additionally require a
non-empty `claudeAiOauth.accessToken` before accepting the blob; a blob
failing that check is treated as if that source were empty and resolution
falls through the same chain (Keychain → file → the existing "could not
extract credentials" hint) rather than short-circuiting on a false
positive.

**The expiry preflight is best-effort, not a hard gate.** Before staging a
resolved *subscription* credential (the long-lived token has no
`expiresAt` and is exempt entirely), leerie parses `claudeAiOauth.expiresAt`
and compares it to the current time, mirroring the pattern already proven
for AWS SSO token freshness in `scripts/remote/aws-credentials.sh`:

- **Already expired** → refuse to launch and print `claude /login`, rather
  than starting a run doomed to fail at worker #1.
- **Inside a near-expiry threshold** (proposed 90 minutes — long enough to
  cover realistic run durations without false-alarming on every launch) →
  warn with the credential's exact expiry time and point at
  `claude setup-token` as the durable fix, but still launch.
- **`expiresAt` absent or malformed** → proceed silently. The 1-year token
  has no `expiresAt` field at all, and a best-effort freshness check must
  never harden into a hard requirement for a field that legitimately may
  not be present — that would break the exact non-expiring credential this
  strategy exists to prefer.

**Auth failures split into two classes with different remedies.**
`_is_auth_or_quota_failure` (see *Cleanup on abnormal exit* below for the
full taxonomy) treats numeric 401/429/529 statuses as **transient**: the
gateway rejected one request, but a retry after backoff has a real chance
once the rolling usage window clears or gateway overload subsides. An
expired or revoked *session* is categorically different — Claude Code's own
error handling clears the saved credential locally and, per Anthropic's
docs, sends no further request at all once it detects that state. Backing
off against a session that will never renew itself just burns the full
`auth_retry_max_sec` budget — this was the `b57027d3…` incident's D2
defect: two retries fired 1.2s apart against the same dead session before
the run gave up with a schema-error message that misattributed the
failure.

The fix is a **terminal** auth-failure class, checked before the transient
classifier ever runs: envelope text carrying "failed to authenticate,"
"oauth session expired," "session expired and could not be refreshed," or
"not logged in" (measured against 611 historical `is_error` envelopes: 215
true positives, 0 false positives) never enters the backoff loop, raising
immediately into the same **resumable pause** leerie already uses for
out-of-credits (`EXIT_LOCKED`, exit 75) — worktree-only cleanup, state and
branches preserved, a `leerie resume <id>` hint logged. Like a billing
shortfall, an expired session doesn't resolve on a clock, so auto-resume
would spin uselessly; the run's completed work is preserved and picked
back up on `resume` once the operator re-authenticates or sets
`CLAUDE_CODE_OAUTH_TOKEN`.

**The same refresh-vs-static distinction applies to Bedrock auth.** The
launcher's Bedrock support defaults to AWS SSO/profile auth:
`detect_bedrock_mode()` reads `CLAUDE_CODE_USE_BEDROCK` out of the merged
Claude `settings.json` files, then `bedrock_preflight()` requires the host
`aws` CLI and a live `aws sts get-caller-identity` before staging `~/.aws/`
read-only into the container. SSO access tokens are short-lived (~12h) and
refreshed only by the host's interactive `aws sso login` browser flow — the
identical container-cannot-refresh problem this section already solved for
subscription auth.

`AWS_BEARER_TOKEN_BEDROCK` is the Bedrock analogue: a static bearer
credential the Claude CLI sends directly to the Bedrock runtime endpoint,
with no refresh-token/expiry machinery near it (verified against the CLI's
own bundled source and live end-to-end testing — the token alone is a
no-op unless `CLAUDE_CODE_USE_BEDROCK` is also set). Because it needs no
`aws` CLI, no SSO session, and no `~/.aws/` staging, leerie triggers this
path from a plain host env var, independent of the settings.json-driven
gate, and skips `bedrock_preflight()`/the `~/.aws` mount entirely when set.
When both are present, the bearer token wins, matching the Claude CLI's own
resolution order.

**Multi-token rotation reduces quota exhaustion the same way the
long-lived token above reduces expiry risk — by picking the credential
least likely to fail, rather than reacting after it does.** A single
`CLAUDE_CODE_OAUTH_TOKEN` still has one shared 5-hour/7-day usage window; a
long run (many workers, high `max_parallel`) can exhaust that window
mid-run even though the token itself never expires. `leerie` supports
`CLAUDE_CODE_OAUTH_TOKENS` — a comma-separated list of tokens — which
**supersedes** the singular var when set:

- **Start-of-run smart selection.** Each token's remaining runway (5h/7d
  utilization, reset time, per-model weekly sublimits) is probed via the
  same undocumented usage-telemetry surface Anthropic's own `/usage` view
  and Claude Code's statusline read internally: `GET /api/oauth/usage`
  (`user:profile`-scoped tokens) with a fallback, for
  `user:inference`-scoped tokens such as a `claude setup-token` mint, to
  reading the `anthropic-ratelimit-unified-*` headers off a minimal
  `/v1/messages` call. The token with the most runway is selected before
  any worker spawns — not round-robin, which would burn a nearly-exhausted
  token at the same rate as a fresh one.
- **Mid-run failover.** If the active token gets rate-limited mid-run,
  leerie rotates to another token with runway and **continues in the same
  container** — no re-exec, no restart — by threading the active token
  through each worker spawn's environment. If every token is limited,
  leerie picks the one whose window resets soonest and falls through to the
  existing reset-wait auto-resume path. This covers both surfaces a rate
  limit can reach `claude_p` through: a completed envelope carrying a
  401/429/529/auth-message, and the protocol-level `rate_limit_event`
  stream event that raises `RateLimitedExit` directly out of the streaming
  loop before any envelope is produced.

**This is deliberately structured data in, deterministic Python out — no
LLM worker** (§12). Ranking is `min(1 − 5h_utilization, 1 −
7d_utilization)` with a furthest-reset tie-break, computed identically
given the same inputs.

**Both probe endpoints are undocumented and explicitly best-effort.**
Anthropic can change or remove either without notice; every probe path
degrades to "use the current/first token and react to the next 429" rather
than fail the run. A probe response missing an expected field (contract
drift, distinct from an ordinary transient failure) is logged at WARNING
with a stable marker so a silent endpoint-shape change doesn't quietly
degrade this feature for weeks with no signal. Each token's probe result
is cached for a floor of ~180 seconds and identified in logs only by a
short fingerprint — the raw token value is never written to a log line,
`calls.ndjson`, or `run.json`. `state.json`'s `active_oauth_token` field is
the one place the raw active token persists at rest (local-orchestrator-
owned, not published), and it travels with `state.json` on the existing
Fly/EC2 `fetch-branch.sh` state sync.

**Decomposition needs the same discipline against the loss of in-progress
spend.** §5½ (P1)'s `fit_judge` and coupled-minority `splitter` calls have
no crash barrier: a single `WorkerError` mid-decomposition discards every
fit/split judgment already paid for on that planning pass, not just the
node being judged — the same class of loss D3 measured in the motivating
incident, where decomposition was 27.8% of the run's total spend. The
remedy mirrors disciplines `_recursive_decompose` already applies
elsewhere: a `WorkerError` from either call degrades that node to a
**leaf**, the same way the existing depth-cap and no-progress-guard cases
resolve uncertainty by accepting the node as-is; and `phase_plan` snapshots
decomposition progress into state as each top-level subtask finishes
expanding, the same way `plan_snapshot` persists the post-`_schedule()`
plan. Like `plan_snapshot`, this snapshot is **diagnostic only** — nothing
reads it back. The resumable-planning cursor (§6) checkpoints
`phase_plan`'s output as a whole, so a pause mid-decomposition still
re-runs the entire `phase_plan` invocation on resume rather than
rehydrating from partial leaves; finer-grained resume is a separate,
not-yet-shipped capability.

### Cleanup on abnormal exit

A run can end abnormally four ways: the user hits Ctrl-C, an external
process sends a signal (SIGTERM/SIGHUP from CI, systemd, a terminal close),
an unhandled exception fires, or the Claude Code subscription rate-limit /
session-limit is hit mid-worker. In each case the orchestrator runs a
cleanup pass before exiting, and the cleanup *scope* is uniformly
conservative — **state and branches are always preserved**; only worktrees
are torn down. The run is always resumable via `resume <id>` after any
abnormal exit.

**Auth failures split into transient and terminal, and only one benefits
from backoff.** 401/429/529 — a rejected request against a still-valid
session, whether from the rolling subscription cap or transient gateway
overload — is **transient**: the session is fine, so retrying after
backoff has a real chance once the window clears, handled by the existing
auth/quota retry loop (`_is_auth_or_quota_failure`, taxonomy below). An
expired or revoked session — "OAuth session expired," "failed to
authenticate," "not logged in" — is **terminal**: per Anthropic's docs,
Claude Code clears the saved credential and sends no further request at
all once it detects this state, so every retry fails identically to the
first and backoff only burns the `auth_retry_max_sec` budget for no
benefit (see *Credential strategy* above). Terminal auth failures are
checked *before* the transient classifier and routed straight to the same
resumable pause (`EXIT_LOCKED`, exit 75) used for out-of-credits below —
worktree-only cleanup, state and branches preserved, `resume` picks back
up once the operator re-authenticates.

**A mid-stream transport disconnect is a third transient class, and it
gets the same backoff.** When the network connection carrying a worker's
streaming response drops mid-answer, `claude -p` surfaces a result
envelope with `is_error` set, `terminal_reason` = `"api_error"`, a *null*
`api_error_status` (the connection died before any HTTP status returned),
and result text `"API Error: Connection closed mid-response. The response
above may be incomplete."` This is the same *family* as the 529 overload
case — the session is fine, the request just never completed — so a fresh
session after backoff has a real chance of succeeding. It is distinct
from a schema mistake, which is what the immediate corrective-note retry
exists for; routing a transport drop through that path retries once
against the same bad network window with a nonsensical "conform to the
schema" nudge, then fails the subtask. `_is_transient_transport_failure`
classifies it (keyed on `terminal_reason == "api_error"` with no numeric
`api_error_status`, or — as a secondary catch — a narrow connection-drop
text-marker set) and routes it through the *same*
`_is_auth_or_quota_failure` tenacity backoff loop, checked after the
terminal-auth classifier so an expired session is never mistaken for a
transport blip. Measured over 9,020 worker logs: 58 sids hit this drop; of
the 56 that did **not** also hit `max_turns` (the pure-transport
population), 83% recovered on a later attempt and 73% on the very next
one — a network transient, not context exhaustion (a third of drops fire
at ≤3 turns). The two `max_turns`-coupled sids recovered 0% — an oversized
subtask that also drops is a *decomposition* problem (see §5½ P1
*Sub-file*), not one backoff can fix. This path mirrors Claude Code's own
remedy for a mid-stream drop (an automatic retry, CLI v2.1.219); leerie's
backoff is the fresh-session complement for when the CLI's in-session
retries are exhausted.

**A client-side context refusal is a fourth class, and it is terminal.**
Claude Code enforces a context ceiling *itself*: when the assembled prompt
exceeds the window it believes the model has, it emits a synthetic
assistant message (`model=<synthetic>`, usage all zeros) and ends the
session with `terminal_reason` = `"blocking_limit"` and result text
`"Prompt is too long"` — without issuing an API call at all. Retrying
identical input therefore cannot succeed, putting this in the same bucket
as terminal auth rather than the transient classes above.
`_is_context_overflow` requires *both* `terminal_reason` and the result
text (the reason alone is shared with other blocking limits; the text
alone could appear in a worker's own correct output), is checked
immediately after the terminal-auth classifier and before the generic
corrective-note retry, and routes to the same resumable `EXIT_LOCKED`
pause — the remedy is an operator change, not a wait.

Before refusing, the CLI attempts reactive compaction, which summarises
**message groups**; a conversation with fewer than two of them fails
`too_few_groups` and the refusal follows immediately. Every worker holds a
multi-turn conversation and compacts normally — the **preflight smoke
test does not**, since it is a single exchange whose tokens are almost
entirely system prompt, tool schemas and `CLAUDE.md`, none of which
compaction can touch. So for that one invocation the effective limit is
the *compaction trigger*, not the context ceiling itself.

That is why the smoke test runs in an **empty working directory**: it
validates the CLI (auth, streaming, `--json-schema`), not the repository.
Loading the repo's own `CLAUDE.md`/skills/commands is by far the largest
term (measured: 126,022 prompt tokens in this repo vs. 17,222 empty,
against a failing run that reached 183,485). Since `CLAUDE.md` only grows,
the margin has to be structural rather than a number someone re-tunes — an
empty cwd is the only one whose prompt size is bounded by construction.
The cost is real: preflight no longer proves the CLI can start *in this
repo* (e.g. a malformed `.claude/settings.json`), which now surfaces at
the classifier instead — acceptable because the refusal there is
classified rather than printed as a bare string. Left unclassified, this
failure was actively misleading: the envelope fell through to the
two-attempt schema loop and surfaced as *"worker failed schema-valid
output twice: Prompt is too long"* — blaming schema validation for a
context refusal, costing three misdiagnoses before the real cause was
measured (2026-08-06). The pause message therefore names the actual
contributors (task text, the repo's `CLAUDE.md`), and, when the
strict-output proxy is active, names it first (see §7 *Forcing
constrained decoding*).

**Worktrees are also pruned mid-run, not only at cleanup.** Once
`integrate_wave` reports a subtask's branch merged into staging, that
subtask's worktree is dead weight — the commits live in the branch, which
survives worktree removal — so `phase_execute` removes it at the end of
that wave rather than waiting for run end. The prune runs **once per
wave**, after every subtask in it has been integrated, so peak worktree
coexistence is the size of a wave. Without this, every worktree a run ever
created persisted until the run finished: at 30–87 subtasks against a repo
whose `node_modules` is ~1.4 GB, that is the measured 51 GB that killed
two runs with an unhandled `ENOSPC`.

The prune is scoped to *integrated* subtasks specifically. A `blocked` or
`failed` subtask keeps its worktree, because that tree is exactly what an
operator inspects by hand before settling it with `accept-blocked` or
`accept-integration` — pruning the whole wave instead would destroy that
evidence.

**Why a worktree costs what it does.** Package managers like pnpm are
content-addressed and normally *hardlink* from a shared store into each
`node_modules`, so a second checkout of the same dependency set costs
almost nothing when store and tree share a mount (measured once, on one
host: 92.6% of `node_modules` bytes shared, private remainder 102 MiB
against a 1.35 GiB tree — illustrative, not a verified general quantity).
But leerie bind-mounts the package-manager store and the state directory
as *separate* mounts, and Linux refuses `link()` across different mounts
even when both resolve to the same filesystem (`do_linkat`'s `EXDEV`) — so
the store cannot hardlink into a worktree, pnpm falls back to copying, and
each worktree pays full freight. That is the multiplier behind the 51 GB,
and the mid-run prune bounds coexistence to one wave's worth of trees.

This explains *why* worktrees are expensive; it deliberately does not
drive a gate. A per-worktree measured bound was attempted and withdrawn
after repeated defects, because the marginal cost of a not-yet-created
worktree depends on mount topology leerie does not control and a peak
count that depends on scheduling. The disk guardrail is instead a
proportional free-space floor plus a resumable pause; see
IMPLEMENTATION.md's "Disk headroom (N30)". Colocating the store with the
worktrees on one mount would collapse the per-worktree cost and is the
more fundamental fix, but it changes runtime behaviour across the local,
Fly and EC2 runtimes and both privilege models, so it is held as a
separate change rather than folded into the guardrail.

**Worktree-only cleanup, always.** Whether triggered by Ctrl-C,
SIGTERM, SIGHUP, WorkerError, or any other exception:

- Worktrees under `<state-root>/runs/<run-id>/worktrees/` are removed and
  `_prune_leerie_worktrees` (scoped) clears stale metadata. Worktrees are
  disposable — `scripts/new-worktree.sh` re-creates them idempotently
  on `resume` from the deterministic branch names.
- State.json, the run branch (`leerie/runs/<run-id>`), and per-subtask
  branches (`leerie/subtasks/<run-id>/*`) all survive. Implementer
  checkpoints under `<state-root>/runs/<run-id>/checkpoints/` survive too,
  so in-flight subtasks resume from where they left off.

**Worker subtree termination — kernel-enforced via the container
boundary.** Cleanup must reach not just the direct `claude -p` child but
every process *it* spawned (test runners, build tools, dev servers —
whatever a `claude -p` worker invoked as a tool call). Signaling only the
leader leaves descendants alive: Claude Code's Bash tool runs every
command via `bash -c "…"` in its own POSIX session, and
`run_in_background: true` detaches long-running commands further. PPID
chains break by design, sessions break process-group kill, and
reparenting hides survivors as orphans of init — POSIX gives no in-OS
guarantee that ad-hoc lineage tracking can be made airtight against a
tree that intentionally detaches.

Leerie therefore makes cleanup a **property of the runtime boundary, not
of the orchestrator's signal handling**. The orchestrator and every
worker it spawns run inside a single container (containerd-managed: on
Linux native, on macOS via a Colima-managed Linux VM). When the
orchestrator process exits — for *any* reason, including SIGKILL,
segfault, OOM-kill, or power loss — the container's PID 1 dies and the
kernel reaps every process in the PID namespace via cgroup release, the
same guarantee runc/containerd/Kubernetes rely on. There is no possible
survivor: a process that detached into its own session, a daemon that
double-forked, a pool worker that reparented to init — all get reaped by
the kernel, not by any code leerie wrote.

The contract reads identically to before — every exit path (Ctrl-C,
SIGTERM, SIGHUP, WorkerError, RateLimitedExit, any unhandled exception,
plus SIGKILL and hard crashes) terminates the worker's *entire* subprocess
subtree before resources are returned — but the mechanism is now
load-bearing in a way prompt-level or heuristic-level cleanup never could
be.

The per-worker async cleanup that lives in `claude_p` (the PPID walk in
`_terminate_proc_tree`, the `_DescendantTracker` polling loop) is *kept*
as the fast happy path that reaps a single worker's subtree promptly on
clean exit, but it is no longer the abnormal-exit guarantee — if it
half-finishes under Ctrl-C, that's no longer a leak, since the container
boundary catches every survivor when the orchestrator exits.

**The container boundary's hidden precondition: the orchestrator must
actually exit.** The kernel reaps the PID namespace *when PID 1 dies* —
but nothing guarantees PID 1 dies just because the host-side `nerdctl run`
client did. On the local runtime the launcher relies on `nerdctl run -i`
forwarding the host process's terminating signal to container PID 1; that
link is not itself enforced by leerie. Observed failure mode: a
**VM-wide OOM** (two multi-worker runs sharing one 8 GB Colima VM
exhausted all memory) invoked the kernel's *global* OOM-killer, which —
because every in-container process carries `oom_score_adj:-998`
(containerd's default) — spared the workers and the orchestrator and
instead killed the *unprotected host-session processes*, including the
`nerdctl run` clients. The host CLI died (`exit status 255`, finalize
skipped), but container PID 1 kept running as an orphan; because the
orchestrator is alive, it still holds the run-dir flock (§6 *Single owner
per run dir*), so every subsequent `resume` correctly loses the flock
race and exits `EXIT_LOCKED=75` — the run is wedged until the orphan is
killed by hand. Three mechanisms close this gap, defense in depth:

1. **Aggregate memory cap (prevention).** `container-entry.sh` (PID 1)
   writes a container-level cap to the `leerie.slice` cgroup's
   `memory.max` — the parent of every per-worker cgroup — derived from VM
   `MemTotal` read from `/proc/meminfo` (in-container, since the host
   launcher cannot read the VM's MemTotal on macOS). A container that
   exceeds its cap triggers a *cgroup-scoped* OOM (`CONSTRAINT_MEMCG`),
   which kills a process *inside that container* rather than a global OOM
   reaching host-session processes — converting "kill the host client →
   orphan → wedge" into "kill a worker in the guilty container → the
   orchestrator observes a clean worker failure." It bounds the
   *aggregate*; the per-worker cgroup caps (below) bound each worker but
   not their sum.
2. **Kill-on-exit trap (proactive cleanup).** The launcher installs
   INT/TERM traps on the local run path that `nerdctl kill` the
   container before the launcher exits, so a Ctrl-C or SIGTERM to the
   host CLI tears the container down instead of orphaning it. OOM and
   SIGKILL deliver an uncatchable signal, so the trap never runs in
   exactly the OOM case above — it complements the reaper, not
   substitutes for it.
3. **Stale-container reaper (recovery).** Before spawning on the local
   `resume` path, the launcher checks for an already-`Up` container for
   this run whose owning launcher PID is dead (via a `leerie.launcher_pid`
   label set at spawn) and `nerdctl kill`s it first — the load-bearing
   fix for the OOM case, making `resume` self-heal the orphaned-flock
   wedge regardless of how the orphan was created.

The container boundary holds across both invocation modes:

- **Terminal mode** — `leerie "task"` from a shell. The launcher gives the
  container a controlling TTY (`-it`); `log()` streams live and
  clarification questions use `input()`. Ctrl-C delivers SIGINT to
  container PID 1.
- **Plugin mode** — Claude Code's Bash tool invokes the launcher from
  inside another Claude Code session (no host TTY). The launcher passes
  `-i` only; `sys.stdin.isatty()` returns False, activating the
  orchestrator's no-TTY clarification path, which writes
  `<state-root>/runs/<run-id>/pending-questions.json` (visible on the host
  via the `/leerie-state` bind mount) and exits with
  `EXIT_NEEDS_ANSWERS=10`. The plugin agent reads the file, asks the user
  in chat, writes `<state-root>/answers.json`, and re-runs the container
  with `--answers`. Same exit codes, same file passing, same kernel
  teardown guarantee.

See IMPLEMENTATION.md "Container shape" for the mount table, image build,
per-OS preflight, and the `[ -t 0 ]` TTY adaptation between the two modes;
and "Concurrency model" for the unchanged in-container worker cleanup that
runs as the happy path.

**Launcher hang on abnormal container exit (decoupled streaming).** A
second way the "PID 1 must exit" precondition is subverted: not the
container failing to die, but the *launcher* failing to return after it
dies. In piped mode (`leerie … | tee log`), the container's stdout is
forwarded by Colima's persistent SSH ControlMaster (`ControlPersist=yes`),
which holds a *copy* of the launcher's stdout-pipe write-end for the
run's duration. On a **clean** exit the master closes its copy and `tee`
receives EOF; on an **abnormal** exit (PID-1 crash under `set -e`, OOM
SIGKILL, a mid-run `nerdctl kill`), the master **retains** the write-end
— `tee` never receives EOF, the launcher never returns, its EXIT trap
never fires, and the `--rm` container is orphaned `Up` (holding the
run-dir flock, wedging `resume`). The fix is *decoupled streaming*: the
launcher points `nerdctl`'s stdout/stderr at a run-log **file** (the
master does not retain a plain-file fd) and streams that file to its own
stdout via a `tail -f` the launcher owns and reaps in its EXIT/INT/TERM
traps. The EOF gate becomes the launcher-controlled `tail`, never the
mux, so a stuck master can no longer wedge the pipeline. Gated to the
piped (`-i`, non-TTY-stdout) case; the interactive `-it` path has a real
pty and thus no hang. The stale-container reaper above remains the
backstop for the uncatchable SIGKILL case.

**Worker subtree termination — Memory containment via cgroup v2.** The
kernel reap above handles *process lifecycle*, not memory: when a worker's
tool subtree (a `pnpm test` spawning vitest pools, a `tsc --noEmit`
building a 1.5–2 GB V8 heap) overshoots the container's available RAM, the
kernel's OOM killer fires inside the container's single memcg and can
land on `sshd` / `lima-guestagent` / the leerie orchestrator itself,
collapsing infrastructure the launcher relies on for SSH-tunnel survival
to the macOS host. Observed on an undersized 11 GiB Colima VM with 4
concurrent implementers: vitest worker (1.85 GiB anon-rss) → OOM-killer
fires → `agetty` → `journald` → `sshd` (Mac launcher sees `exit status
255`) → `lima-guestagent` → only then the offending Node process, while
leerie's own RSS sat at 36.8 MiB throughout — the cascade was caused by
all processes sharing one memcg.

Each `claude -p` worker is therefore enrolled in its own child cgroup at
`<cgroup-root>/leerie-w-<sid>/` (`<sid>` is the run-scoped composed sid,
below) with `memory.max` set to `caps["worker_memory_max_bytes"]` (default:
a fixed isolation ceiling derived from the measured build peak, below) and
`pids.max` set to `caps["worker_pids_max"]` (default 2048, overridable via
`--worker-pids-max` / `LEERIE_WORKER_PIDS_MAX`). When the worker subtree
blows past `memory.max`, the kernel OOM-kills *inside that cgroup*;
sibling workers, the orchestrator, and host-side services are not
eligible victims. `memory.swap.max=0` prevents the kernel from delaying an
inevitable OOM by paging worker memory to the Colima swap file.

**The per-worker cgroup name is run-scoped**: `leerie-w-<run-id-prefix>-<sid>`,
not a bare `leerie-w-<sid>`. The prefix is the first 12 hex chars of the
run id (~48 bits, negligible collision probability). This is load-bearing
whenever two runs execute concurrently on the same VM (the common case on
Colima, since the launcher passes `--cgroupns=host` so the broker can
enroll worker PIDs into `leerie.slice/`): worker `sid`s alone are not
unique across runs (a phase-1 `sid="classifier"` repeats identically
across two concurrent runs of the same task), and teardown on cgroup v2 is
`cgroup.kill=1`, which SIGKILLs *every* process in that cgroup — so
without the prefix, run B finishing its classifier would SIGKILL run A's
still-running classifier mid-stream, indistinguishable from a bare crash
(reproduced live). `--cgroupns=private` is not an option — it breaks the
broker's cross-scope PID enrollment (see below) — so the name must carry
the run identity instead.

The per-worker cap must hold **both** the build/test subprocess tree
*and* the resident `claude -p` process, since `claude` stays alive
running the build via Bash and shares the cgroup with whatever it
launches. Live in-container `memory.peak` measurement on a
Next.js/Turbopack build: build alone peaks at 4.16 GiB, build + resident
claude peaks at 5.6–6.3 GiB. An earlier `4 GiB` clamp was *below* that
combined peak, so no VM size could auto-derive a sufficient cap — every
build-running worker was cgroup-OOM-killed regardless of host RAM. Any
cap below the combined peak guarantees the OOM it exists to contain.

**Reap the worker's own subprocesses before destroying its cgroup.** The
`finally` that tears a worker down calls
`descendant_tracker.stop_and_reap()` *before* `_cgroup_destroy()`.
Backgrounded subprocesses (Bash tool `run_in_background: true`) are still
cgroup members while alive, so destroying first hands the broker a
populated cgroup and its `rmdir` fails `EBUSY`. The timeout and abort
paths always reaped first; the success path did not, and that asymmetry
was the dominant source of leaked `leerie-w-*` directories. Measured over
1801 workers: conformers are 8% of all workers but 88% of destroy
failures, with a median backgrounded-subprocess count of 984 against 12
for everything else. The broker's drain (below) remains the backstop for
the residual race; the ordering fix is pinned by a source-coupled test,
since an ordering is invisible to a behavioural one.

**Prevention is not reclamation, so the broker also sweeps.** Worker
cgroups live on the *host* hierarchy (`--cgroupns=host`), so they outlive
the container that made them; an orchestrator killed outright skips its
own cleanup entirely. The broker sweeps abandoned worker cgroups once at
startup, before binding its socket, so no client of its own run can be
mid-create while it walks. The sweep is **deliberately cross-run** — a
run cannot clean up after a predecessor it never knew about — so its
predicate must be a safety question, not a housekeeping one: a cgroup
qualifies only when it has no processes in it *and* is older than a
generous age floor. Neither alone is sufficient (create and enroll are
separate steps, so a live cgroup is briefly empty; a concurrent run's
broker cannot be serialised against, so only age covers that window).
Identity cannot substitute for age: the cgroup name carries its owning run
id, but a resumed run keeps its original id with a new container, so "is
a container with this id still alive" reports *dead* for every resumed
run — a predicate that would have deleted live runs on the host where
this was measured.

**A per-worker cap is a ceiling, not a reservation.** Writing `memory.max`
allocates nothing; it only bounds. The real backstop against host-level
exhaustion is the *aggregate* `leerie.slice/memory.max` set by
`scripts/container-entry.sh` (`MemTotal - max(1 GiB, 12.5%)`), which
bounds the whole worker fleet regardless of any individual worker's
`memory.max`. Two consequences follow, and they are the load-bearing part
of this design:

1. Per-worker ceilings **may safely sum past the slice budget**. Eight
   workers capped at 9.45 GiB do not reserve 75.6 GiB; each promises only
   "kill me before I exceed 9.45". The slice cap still binds.
2. Therefore the per-worker cap must **never** be derived by dividing the
   slice budget across a projected worker count — that treats a ceiling
   as a reservation and manufactures caps *below* the measured build
   peak, reintroducing the OOM above. A run sized during a busy moment
   stays handicapped for its whole life, since the cap is resolved once
   at startup.

The cap is therefore a fixed isolation ceiling —
`max(build_peak, min(build_peak × 1.5, slice_max / 2))` — a function of
the host's slice budget and the repo's own declared needs, never of
*load*. The half-slice bound stops one worker eating the fleet's
headroom, but the build peak outranks it: on a slice too small to honour
both, a `memory.max` above the slice is harmless (the aggregate cap binds
first) while one below the build peak guarantees the OOM. Being
load-independent is what makes resolving it once, at startup, correct.

**A repo can declare its own memory needs, and the ceiling must honour
them.** Node 20+ sizes its heap from the container it finds itself in,
but an explicit `--max-old-space-size` overrides that entirely, and the
heap then throws OOM at its declared limit regardless of cgroup room. A
repo whose build/test command declares a heap (most often inside the
`package.json` script that command runs, which is why the resolver
follows the indirection rather than scanning the literal string) has
told leerie exactly what one of its workers needs — the repo's own
number outranks any measurement leerie inherited elsewhere.

The ceiling is therefore raised to `declared heap + headroom` when the
repo declares one, where headroom covers everything else sharing the
cgroup (Node's non-heap memory, the resident worker process). Two
refusals bound it: an operator who pins a smaller cap explicitly is
refused rather than silently overridden (a cap below a declared heap
guarantees the OOM), and a declared heap that cannot fit the shared
slice even alone is refused outright, since no per-worker arithmetic can
rescue it. The same headroom figure runs in reverse elsewhere — leerie
also tells Node how big a heap it may take given a cap — and the two
must move together; they were once out of step, granting a heap larger
than the cage it had to live in.

**The ceiling is not the only per-worker figure: admission needs a
*demand* estimate, and the two are different quantities.** The ceiling
answers "how big before *this* worker is killed" and reserves nothing.
Admission answers "is there room for another build right now," which
genuinely does reserve and needs a prediction of what a worker will
actually *use*. Reserving against the ceiling would throttle every run
to fit a bound nobody is expected to reach. The two figures coincide
only when a repo declares a heap — the ceiling is raised to exactly
`declared heap + headroom`, the same value the demand estimate takes —
and that is not the divide-the-slice failure above: it is a *floor*
driven by what the repo says it needs, never a *share* of the slice
divided by a worker count.

The estimate defaults to the measured build peak, and becomes distinct
from the ceiling only when a repo *declares* its own memory demand: an
explicit `--max-old-space-size` overrides Node's container-aware
default, so such a worker's real demand is the declared heap plus
non-heap headroom, well above the historical peak, and admission sizes
on that instead — deliberately only then, since raising the estimate
fleet-wide would throttle every repo to fix a case most do not have.

Two consequences follow:

1. The provable reservation ceiling is `demand × (max_parallel + 1)`,
   not `build_peak × (max_parallel + 1)`. For a repo that declares a
   large heap this can exceed the slice budget, and that is expected:
   wave-entry degradation shrinks concurrency toward what fits, flooring
   at one worker (a wave of zero makes no progress). A heap larger than
   the whole slice is refused at startup, before the wave ever runs. The
   refusal fires only when the resolved ceiling is *below* `declared
   heap + headroom`, so a repo whose auto-derived ceiling already clears
   that floor is admitted without the slice ever being consulted; the
   slice check asks whether ONE such worker fits, never `max_parallel`
   of them.
2. Because degradation is the designed response, a declared heap large
   relative to the slice buys fewer concurrent workers — the operator is
   told so at startup rather than discovering it as unexplained
   slowness.

**Contention is handled by admission in two stages, not by shrinking
caps.** The cheap stage runs once at wave entry
(`_degrade_max_parallel_for_wave`, called by `phase_execute`): it
shrinks the wave's own concurrency to the largest N whose workers fit
the headroom that actually exists, and hands N straight to the wave's
`asyncio.Semaphore`. The expensive stage is the per-spawn gate below,
which can block for minutes. Sizing the wave to real headroom first
means the gate is a backstop for what changes **during** the wave — a
sibling run's workers arriving — rather than the routine path;
shrinking concurrency is also the only lever this run controls, since it
cannot shrink a sibling run's live worker count and must not shrink its
own per-worker cap (the reservation error above). Both stages read the
**same** signal, `slice_max - unreclaimable` — deliberately, since two
signals could disagree about one slice, with the degrade sizing a wave
down against page-cache pressure the gate then cheerfully admits into.
The degrade never feeds its own output back into a later headroom
computation, so successive waves cannot ratchet down.

Before spawning a worker, leerie blocks while the slice lacks room for
another build (`_await_worker_memory_admission`). The signal is measured
headroom — `slice_max` minus *unreclaimable* usage (anon + unevictable +
unreclaimable slab, from `memory.stat`), never `memory.current`, which
counts page cache: on a live host, 10.4 GiB of 20.5 GiB in use was
reclaimable file cache, so gating on `memory.current` would under-report
headroom by half and stall a fleet with ample room.

The gate also **reserves** one per-worker demand estimate per worker
still in flight (admitted and not yet exited) plus one for the worker
being admitted, and is otherwise stateless; `_invoke` runs under
`Semaphore(max_parallel)` with the gate *inside* it, so a whole wave
would otherwise evaluate identical pre-allocation headroom and all admit
at once. (The superseded divisor read `live_siblings`, which counts
enrolled cgroups, and enrollment happens microseconds after spawn — no
such gap.) On a slice already at 40 GiB of 54.9, the first two workers
of a wave admit and the third waits, where a stateless gate would have
let all five through against 14.9 GiB of headroom.

**A reservation is bounded by the worker's lifetime, not by elapsed
time**, and that distinction is the whole correctness argument. Most
workers are short-lived (classifier, fit_judge, splitter,
satisfied_probe finish in seconds), so an interval-based reservation
outlives its worker by orders of magnitude and they accumulate. Measured
against real runs, 13–15 workers start within any 180 s window, which
under an interval model demands 88–101 GiB on a 54.9 GiB slice —
unsatisfiable at any load, so every worker stalls the full wait and
admits anyway, reintroducing the same stall the mechanism exists to
remove. Bounding by lifetime makes the ceiling provable instead:
in-flight workers are capped by the semaphore the spawn path already
runs under, so reservations cannot exceed `demand × (max_parallel + 1)`
(about 38 GiB at the default estimate, fitting an idle 54.9 GiB slice
while still blocking a busy one). A repo declaring a large heap raises
the estimate and can push that product past the slice; wave-entry
degradation then shrinks concurrency toward what fits, flooring at one,
which is the designed response rather than a broken bound.

`_WORKER_ADMISSION_RAMP_SEC` survives only as a leak backstop, for the
window between the gate and the spawn path's `try`/`finally` — past that
long, a still-running worker's demand is already in the `unreclaimable`
reading, and reserving for it again would double-count. The wait is
bounded (10 min) and then admits anyway: a run that never progresses is
worse than a tight one, and because the ceiling is now always ≥ the
build peak, a late-admitted worker is no longer doomed by construction.
When no slice budget is readable at all (containment off,
`--dangerously-allow-uncapped`, no broker), admission is a no-op and
sizing falls back to the legacy `/proc/meminfo` basis. This replaces the
older advice to hand-tune `--max-parallel` down for build-heavy waves,
which was sound under a divide-the-slice cap but asked the operator to
predict contention the orchestrator can now simply measure.

**The containment must be performed by an identity that owns (or was
delegated) the relevant cgroup subtree — it cannot be delegated to the
dropped-privilege orchestrator.** In the rootful case (Colima, Fly) that
identity is root; the rootless case has its own delegated identity,
covered separately below. This was established empirically after a run
exhausted the VM thread table (a Bun `EAGAIN` panic) because worker
containment was *silently off*. Two kernel facts make self-enforcement
from the orchestrator's own (non-root) identity impossible in the
rootful case, both reproduced live inside a real leerie container and on
a Fly Firecracker VM:

1. **Cross-scope migration is denied.** Moving a task into a cgroup
   needs write on `cgroup.procs` of the destination, the source, AND
   their common ancestor. Workers are born in the root-owned container
   scope (`/system.slice/nerdctl-<id>.scope` locally, the machine scope
   on Fly); migrating them into `leerie.slice` crosses the root cgroup,
   which the leerie user does not own → the enroll write fails with
   `EACCES`/`EIO`.
2. **Controller limit files stay root-owned.** Even inside a properly
   *delegated* subtree, the kernel keeps the controller interface files
   (`pids.max`, `memory.max`) owned by root — a delegatee may organize
   processes but not set controller limits.

An earlier design chowned `leerie.slice` to the leerie user and had the
orchestrator write the cgroupfs directly. It appeared to work but did
not: the direct-write probe passed while the actual per-worker enroll
silently failed on both runtimes, so every worker ran uncapped.

The fix is a **cgroup broker** (`scripts/cgroup-broker.py`).
`scripts/container-entry.sh` is PID 1 (the Dockerfile intentionally
omits `USER leerie`); *before the privilege drop* it launches the broker
at the identity that owns (or was delegated) the slice — real root in
the rootful case (Colima, Fly), the rootlesskit-mapped host UID in the
rootless case (which owns the systemd-delegated user slice; see
*Rootless exception* below). The broker listens on a Unix socket at
`/run/leerie-cgroup.sock` (world-connectable; every request is
validated). It performs `create` / `enroll` / `destroy` at that owning
identity — the only identities where enrollment and limit-setting work
— and detects the cgroup hierarchy: **v2** (Colima) uses the unified
`leerie.slice/leerie-w-<sid>/{pids,memory}.max`; **v1/hybrid** (observed
on Fly Firecracker VMs, whose unified mount exposes no controllers) uses
the split hierarchies (`/sys/fs/cgroup/pids/leerie.slice/...`,
`/sys/fs/cgroup/memory/leerie.slice/...`). The `<sid>` in these path
templates is the run-scoped composed sid (`<run-id-prefix>-<worker-sid>`,
above); the broker treats it as an opaque validated string. The
entrypoint then drops to the leerie user via `runuser -u leerie --`
before exec'ing the orchestrator (local nerdctl) or sleeping as PID 1
(Fly, where the orchestrator is started out-of-band by the launcher's
ssh-console wrapper that drops via `Popen(user="leerie")`). The
orchestrator's `_cgroup_*` helpers are thin socket clients of the
broker; it never writes cgroupfs directly. (Rootless containerd has no
real root to drop from or broker as — see *Rootless exception* below.)

**Fail-closed gate.** Because a silently-uncapped run is what caused the
crash, `_enforce_and_record_cgroup_containment` runs once per run just
before the first worker spawns — in `_run_phases`, *after* the resume
short-circuits so an already-completed / no-work resume (which spawns
zero workers) is not gated and cannot `die()` spuriously on a
containment-incapable host. It probes the broker end-to-end (a real
create+enroll+destroy round-trip — the true test of the path workers
use, unlike the old direct-write probe that false-passed) and records
`{enforced, hierarchy}` in `state.json`. If containment cannot be
enabled (broker down, no usable cgroup hierarchy, or read-only
cgroupfs), the run `die()`s with an actionable message — **unless** the
operator passes `--dangerously-allow-uncapped`
(`LEERIE_DANGEROUSLY_ALLOW_UNCAPPED` / `leerie.toml`), which downgrades
the fatal gate to a loud warning. Persisting the outcome is deliberate:
the crash left no artifact of the silent failure; now it is visible.

**Rootless exception — the systemd-delegated user slice.** Under
rootless containerd (Linux), rootlesskit maps the host UID to container
UID 0, so "root" inside the container IS the unprivileged host user. The
entrypoint detects rootless via `/proc/self/uid_map` (non-zero
host-start field) and skips both the privilege drop (`runuser`) and the
`/work` chown (which would reassign ownership into the subuid range,
breaking host-side access).

`leerie.slice` is anchored at the cgroup v2 subtree systemd already
delegates to that UID's login session
(`/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/`) — not
the root-owned (mode 0555) top-level `/sys/fs/cgroup`. `pam_systemd`/logind
chown that directory's `cgroup.procs`/`cgroup.subtree_control`/
`cgroup.threads` to the real UID, so any cgroup the UID creates
underneath it inherits ownership on every kernel-populated interface
file, including `pids.max`/`memory.max`. Cross-scope migration works the
same way: a worker's `claude -p` process is born under whatever scope
rootless containerd placed the container in (e.g.
`user@<uid>.service/user.slice/nerdctl-<id>.scope`), and since both that
scope and `leerie.slice` descend from the delegated `user@<uid>.service`,
migrating a worker PID between them succeeds.

`HOST_UID` (the real host UID rootlesskit mapped container UID 0 to) is
read from the second field of `/proc/self/uid_map`'s first line. The
entrypoint passes the resolved root to the broker via
`LEERIE_CGROUP_V2_ROOT` (`scripts/cgroup-broker.py`'s `V2_ROOT`,
defaulting to `/sys/fs/cgroup` for every other runtime). The broker
needs no separate privileged identity: it launches at the same
rootlesskit-mapped identity the whole container runs as, before the
privilege drop.

This relies on systemd + cgroup v2 delegating `pids`/`memory` into the
per-session slice — the default on modern systemd hosts. Where that
isn't the case, the slice-setup writes (`|| true`) and the broker's
write-then-read-back verification in `_detect()` fail silently, and the
fail-closed containment gate stops the run unless the operator passes
`--dangerously-allow-uncapped`.

**User-namespace remap for `--dangerously-skip-permissions`.** Claude
Code rejects `--dangerously-skip-permissions` when `os.getuid() == 0`.
In rootless mode the entrypoint uses
`unshare --user --map-user=<leerie-uid> --map-group=<leerie-gid>` to
remap outer UID 0 to inner UID leerie in a nested user namespace:
bind-mounted host dirs stay writable (outer UID 0 → inner UID leerie),
image dirs at `/opt/leerie-image/` (outer UID leerie) traverse via their
mode-755 bits, and Claude Code sees `getuid() == leerie` and accepts the
flag with no escape-hatch check. The OCI default seccomp profile blocks
`unshare(CLONE_NEWUSER)` inside containers, so the launcher passes
`--security-opt seccomp=unconfined` for rootless runs (gated on the
`containerd-rootless/child_pid` sentinel, not `id -u`, so macOS/Colima
runs are unaffected).

Local nerdctl additionally needs the launcher's writable bind-mount —
`--mount type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,
bind-propagation=rshared` — so the entrypoint can see the host VM's
cgroupfs, plus `--cgroupns=host`: without it, nerdctl's default private
cgroup namespace (`--cgroupns=private`) combined with `nsdelegate`
blocks the broker's process migration to `cgroup.procs` (the kernel
treats the namespace boundary as a delegation boundary). With
`--cgroupns=host` the container sees its real cgroup path and the broker
can enroll worker PIDs under `leerie.slice/`. Fly's Firecracker microVM
boots its own kernel with no cgroup namespace boundary, so this flag
only affects local nerdctl.

On macOS (Darwin) the launcher sets the mount unconditionally — Colima's
VM always runs rootful containerd with cgroup v2 and shared propagation,
but the macOS host has no `/sys/fs/cgroup` to probe. On native rootful
Linux the launcher adds the same `rshared` mount unconditionally.

Rootless containerd is its own branch, gated on the
`containerd-rootless/child_pid` sentinel, and uses a **plain** bind-mount
with no `bind-propagation` flag: rootlesskit's `--propagation=rslave`
demotes `/sys/fs/cgroup` to a slave mount inside its sandbox,
incompatible with `bind-propagation=rshared`. Only read/write visibility
into the already-mounted cgroupfs is needed, which a plain bind-mount
provides. When cgroup v2 isn't present at all, the mount is skipped and
`_cgroup_probe` falls back to uncapped workers (and, absent
`--dangerously-allow-uncapped`, the fail-closed gate stops the run).
Fly's Firecracker microVM exposes cgroupfs directly with no launcher
flag required.

`_cgroup_probe` sends a `probe` request to the broker, which does a real
create+enroll+destroy round-trip of a throwaway cgroup and returns the
detected hierarchy (`v2`/`v1`) — the true test of the path workers use;
an earlier direct-write probe passed on hosts where the subsequent
non-root enroll actually failed, letting containment silently disappear.
On v2 the broker's teardown uses `cgroup.kill` (kernel ≥ 5.14) as an
atomic kill of any worker-subtree process that survived the existing
`_terminate_proc_tree` proc-walk; on v1 it moves survivors to the parent
then rmdirs. See IMPLEMENTATION.md §"Caps" for the resolution surface,
`scripts/cgroup-broker.py` for the broker, and the `_cgroup_*` clients in
`orchestrator/leerie.py` for the call sites.

**Detecting PID exhaustion — the broker `stat` read-back.** The
`pids.max` cap protects the *host* (a runaway subtree cannot exhaust the
VM's PID table), but it has a failure mode of its own for the *worker*:
once reached, every `fork()`/`clone()` in the subtree returns `EAGAIN`,
so every shell the worker's Bash tool tries to launch fails while
in-process tools keep working. Observed live: a worker leaked
`run_in_background` subprocesses (reparented to init, escaping the
descendant walk until the mid-run reaper or worker exit reaps them),
saturated `pids.max`, and spent the rest of the run in a spiral where
even `echo`/`pwd` returned a bare "Exit code 1" — the CLI surfaces only a
generic tool error and the kernel's `EAGAIN` string rarely survives into
the tool-result text, so the worker mis-attributes the failure and burns
its turn budget without recovering.

Detection is a backstop, not a substitute for a cap sized to the
workload. There is no reliable way to *detect and refuse* "a full test
suite run" (the command space is open-ended and a misfiring guard is
worse than none), so the cap value itself is the enforcement surface for
legitimate load: generous enough (2048) to admit a real conformance run,
overridable per-repo for heavier suites. A runaway fork-bomb still trips
it.

The cap is sized against a measured workload, not a guess: leerie's own
suite (3762 tests, 251 modules) peaks at **33** concurrent PIDs (median
7, P99 29, sampled at 20 Hz against the release image's `pids.current`),
so the cap sits ~62× above it — a worker approaching the cap is almost
never doing legitimate work, which is what lets the mid-run reaper below
act on pressure at all. The default was 1024 until a second
1024-saturation incident (a conformer backgrounded a test run, lost
track of its output file, and ran the suite a second time, together
hitting the cap) — nothing in that trace needed more than 1024 PIDs, so
the raise to 2048 buys a misbehaving worker more headroom before it dies
without addressing why it misbehaved.

Per §12 (*prompts are advisory, code enforces*), the orchestrator detects
this mechanically. The broker gains a read-only `stat <sid>` verb
returning the worker cgroup's `pids.current`/`pids.max` and the
`pids.events` `max` counter (incremented once per denied fork — distinct
from a memory OOM, which bumps `memory.events` instead). When enough of a
worker's recent tool-results are errors, `_read_stream` probes the
broker; if the cgroup is at its PID cap (or `pids.events.max` is
climbing), the orchestrator logs the real cause, relabels the inline
summary, and terminates the worker early via the existing abnormal-exit
path with a `WorkerError`. That routes through the callers' existing
handling: an implementer's PID-exhausted run becomes a retryable
`incomplete-handoff` (a fresh worker restarts in a clean worktree), and a
conformer's stays advisory (§9).

**Detecting memory OOM — naming the cause instead of a cryptic checkpoint
error.** A build/test command that overshoots the worker cgroup's
`memory.max` is killed by the kernel with a bare `Killed` (exit 137, no
error text) — unlike PID exhaustion this leaves no failing tool-result
for `_read_stream`'s window detector to key on, since `claude -p` is
often reaped mid-turn before any `result` event is emitted. That symptom
lands in `_invoke`'s no-envelope path indistinguishable from a
session-limit no-op or `--max-turns` exhaustion; `_validate_result` tags
it `empty_handoff`, and once the retry cap burns the operator sees only
*"checkpoint ... does not exist on disk"* — no mention of memory (a real
run drove an operator through a default → 6G → 12G → 16G
`LEERIE_WORKER_MEMORY_MAX` escalation before finding the actual cause).

The broker's `stat <sid>` verb also returns `memory.events`' `oom_kill`
counter (incremented once per OOM-kill, mirroring `pids.events.max`'s role
for fork denial); `_cgroup_stat` widens to a 4-tuple:
`(pids.current, pids.max, pids.events.max, oom_kill)`. `_invoke` reads the
cgroup's stat once more in its `finally`, immediately before
`_cgroup_destroy` (`rmdir`s the cgroup, so this is the last point a read
is possible) — and in the no-envelope branch, if `oom_kill > 0`, raises a
`WorkerError` naming the cause: the last Bash command the worker launched
and the cgroup's `memory.max` cap, with the actionable fix — *"worker
OOM-killed on `<cmd>` (memory.max=N GiB) — raise `--worker-memory-max` or
lower `--max-parallel`."* That message threads through
`_run_implementer`'s `except WorkerError` handler into the synthesized
`incomplete-handoff` envelope's `summary` field.

`_settle_subtask`'s `empty_handoff` handling branches on whether the
worktree holds committed work: when it does, the named-OOM `summary` was
already preserved verbatim. The no-commits branch previously discarded
the worker's `summary` in favor of `_validate_result`'s generic
checkpoint-missing `message`; it now prefers the worker's own `summary`
when present, falling back to the generic message only when none exists —
so a genuinely OOM-killed build is named even when the subtask
terminates.

The error signal is measured over a **sliding window of the last N
tool-results**, not a run of *consecutive* ones — the stream never places
two tool-results adjacently, so a "consecutive" counter could never fire.
The window counts only tool-result outcomes; a single ordinary failing
test leaves at most one error in the window and never reaches the
threshold, while a PID-exhausted worker (every shell-spawning call
failing) fills it quickly. Even then the kill only fires once the
authoritative cgroup read confirms exhaustion — the window merely decides
*when to probe*.

**Mid-run PID reaping — reducing the blast radius.** The window detector
above is a backstop: it *catches* a worker that has already saturated
`pids.max`. But the root cause — `run_in_background` subprocesses
reparenting to init and accumulating against the cap throughout the run
— is not addressed by detection alone. A complementary *reducer* layer
sits under the backstop inside `_DescendantTracker._poll_loop`: it
probes `_cgroup_stat` each cycle and, when pressure rises, reaps the
safest killable set before the cap is hit. Load-bearing safety property:
below the pressure gate, behavior is byte-identical to today — zero
mid-run kills. Both mechanisms share the same `_cgroup_stat(sid)` call
as their authoritative source.

*Trigger — pressure-gated, not timer-based.* Each cycle the tracker probes
`pids.current / pids.max`; reaping arms only at or above
`_PID_REAP_HIGH_WATER` (0.90) — a timer would fire under zero pressure
(pure downside, could kill a live background test, zero upside).

*Target — the safest killable set.* From `_seen` (every PID the tracker
has observed), the reaper selects those that are simultaneously alive,
reparented to init (`ppid == 1`), and older than `_PID_REAP_MIN_AGE_SEC`
(60 s). PIDs are killed oldest-first, stopping once
`pids.current / pids.max` drops below `_PID_REAP_LOW_WATER` (0.75) —
hysteresis so one pass does not over-kill. Killed PIDs are pruned from
`_seen`; exit-time `stop_and_reap` is unchanged. The age floor matters
because a background test the worker just launched has also reparented
to init (`ppid == 1` alone cannot distinguish it from a leaked orphan),
but it is young, so the floor protects it while a forgotten orphan (old)
is still reaped.

*The critical tier.* A single fixed floor is not enough: a burst of
leaked `run_in_background` trees can saturate the cap in seconds — faster
than 60 s lets any of them become eligible — so the reaper arms, finds an
empty candidate list, and watches the worker die. This is the measured
cause of a wave-2 integrator death (`pids.current=1024/1024`, fork denials
213) that discarded a correct merge resolution and killed a 13.5-hour run.
Reproduced directly: 20 leaked orphan trees in a `--pids-limit 1024`
container, all tracked in `_seen`; at t=8s the 60 s floor yields 0
candidates while a 5 s floor yields all 20 — detection was never the gap,
*eligibility* was. The fix is a second tier, not a lower single floor: the
cap's own sizing measurement (33 concurrent PIDs for legitimate work,
above) supplies the discriminator — a worker at 90% of a 2048 cap holds
~1843 PIDs to do work that costs 33, which has no legitimate reading. So
at `_PID_REAP_CRITICAL_WATER` (0.90) the floor drops to
`_PID_REAP_CRITICAL_AGE_SEC` (5 s); below that ratio the 60 s floor stands
unchanged. This discriminator holds only while the cap sits well above
the 33-PID measurement — a `--worker-pids-max` set near the workload
(e.g. 64) would put the critical tier's trigger inside the range
legitimate work occupies and break the premise.

*Accepted bounded regression.* Above the 90% gate a live background
process older than the active floor can be killed — strictly better than
guaranteed total-worker-death-then-full-retry, since the imperfect reap
fires only when the worker is already near EAGAIN death.

*Composition.* Both mechanisms read `_cgroup_stat(sid)` as one
authoritative source: if reaping keeps pressure below the cap, the
detector never fires; if reaping stays too conservative, the detector
catches it and retries fresh. Neither duplicates the other's logic.

*Rejected alternative.* A `cgroup.procs`-based broker verb would give a
more precise orphan list but needs a new `list`/`kill` verb, widening the
single audited root surface §12 guards. `_seen ∩ (alive, ppid==1, old)`
covers the same population without one.

Earlier versions of leerie gave Ctrl-C an explicit "throw this away"
semantic with a full purge of state + branches + run dir. That made
accidental Ctrl-C catastrophic — and it conflated user intent ("stop
this run") with run lifecycle ("nuke the artifacts"). The two are
now separate: Ctrl-C stops; `scripts/cleanup.sh --run-id <id>
--branches` is the explicit full-purge gesture.

**Zombie reaping — the container PID 1 is not an init.** The mid-run
reaper above relieves pressure from *live* leaked processes. A second,
distinct population also counts against `pids.max`: **zombies**
(`<defunct>` tasks — dead processes not yet `wait()`ed), which arise
because the leerie container's PID 1 is not a reaping init: on the local
path PID 1 is `runuser` (the orchestrator its child); on Fly, PID 1 is
an idle `sleep infinity` and the orchestrator a detached `Popen`
grandchild. A worker's tool subtree routinely orphans short-lived
subprocesses (notably `git` and the leerie-private `ssh-agent`); those
reparent to PID 1, which never `wait()`s them, so they persist as
zombies, each occupying a cgroup task slot until `pids.max` fills.
Observed live: a worker running its repo's test suite accumulated 453
`<defunct> git` tasks and wedged at the cap — the mid-run reaper cannot
help, since SIGKILL is a no-op on an already-dead process; only `wait()`
clears it.

The fix routes those orphans to the orchestrator and reaps them there.
`_become_subreaper()` (called once early in `main()`) issues
`prctl(PR_SET_CHILD_SUBREAPER)` so orphaned descendants reparent to the
orchestrator instead of climbing to PID 1; `_zombie_reaper()` — a
background asyncio task with the same lifecycle as `_memory_sampler` —
reaps them roughly once a second, keeping `pids.current` flat. `prctl` is
Linux-only and a logged no-op elsewhere.

**A second route to the same 255: an inherited `SIGCHLD=SIG_IGN`.** The
disposition survives `exec`, so a parent leerie does not control (an SSH
daemon, a login shell, the Fly launch wrapper) can hand it down. Under
`SIG_IGN` the *kernel* reaps exiting children itself, so their status is
gone before asyncio can read it — `PidfdChildWatcher` then `waitpid`s a
PID that no longer exists and reports returncode 255 with empty output.
Only the *first* subprocess is corrupted, which is maximally misleading:
whichever check runs first reports a bogus failure and blame lands on its
subject, not the machinery. `main()` therefore calls
`_restore_sigchld_default()` before anything spawns, and `preflight()`
gates on `_sigchld_is_ignored()` (reads the kernel's `SigIgn` mask from
`/proc/self/status`, since `signal.getsignal()` doesn't reliably reflect
an inherited disposition). This is one of two independent routes to a
fabricated 255; the reaper race below is the other.

**The reaper must reap only what it created — an allowlist, never a
`/proc` scan.** An earlier design scanned `/proc` for zombie children
(`state == Z`, `PPid == getpid()`) not in `_ASYNCIO_MANAGED_PIDS`, on the
theory that exclusion distinguished a true orphan from an asyncio child
briefly awaiting its watcher. Measurement disproved this: CPython's
`PidfdChildWatcher` calls `os.pidfd_open(pid)` after the fork and
`os.waitpid` later still, so between fork and `pidfd_open` the child's PID
exists in *no registry the reaper can consult* — no exclusion or ordering
trick can be correct. Measured live on Fly: the reaper `waitpid`'d the
exact PID asyncio had spawned for `git config user.email` in 40/40 runs,
every one misreported by `preflight` as "git user.email is not
configured." Alternatives measured and rejected: a `waitid(WNOWAIT)` peek
made it worse (pure overhead, doesn't close the window), and excluding
asyncio-known PIDs still corrupted 212/300.

The reaper therefore reaps only PIDs leerie itself recorded
(`_REAPABLE_PIDS`, published by `_DescendantTracker` — the worker subtrees
it already tracks). Correctness is by construction: a PID in its
fork→`pidfd_open` window was never added, so it can never be taken.
Measured 0/300 on the arm where the scanning design failed 246/300, while
still reaping real orphans; the trade-off is that an orphan leerie never
observed is not reaped, with the cgroup PID cap plus the container
boundary as backstop. Because the subreaper reparents orphans to the
orchestrator rather than PID 1, the mid-run `_reparented_orphans` filter
accepts `ppid in (1, getpid())`; exit-time `stop_and_reap` is unaffected.
This is chosen over inserting a real init (e.g. `nerdctl run --init` / tini
as PID 1) because the subreaper covers both local and Fly runtimes
(`--init` is nerdctl-local only) and is purely additive, changing nothing
about the entrypoint/cgroup setup or the PID-1 teardown contract above.

**Rate-limited (RateLimitedExit) → auto-resume after the reset window.**
When `claude -p` reports the subscription session-limit hit (assistant
text in the verbatim format `"You've hit your session limit · resets
<time> (<tz>)"`, or a `rate_limit_event` whose `status` is outside
`{"allowed", "allowed_warning"}`), leerie raises
`RateLimitedExit(reset_at, raw)`.

A second, subtler trigger: an **out-of-credits mid-stream kill**. When
credits run out, a `rate_limit_event` arrives carrying
`overageDisabledReason:"out_of_credits"` (or `out_of_overage`), and the
gateway terminates `claude -p` the moment credits run out, often mid-turn,
before any `result` event is emitted. `_invoke` latches this exhaustion
state and, in its no-result-envelope branch, raises
`RateLimitedExit(reset_at=None, out_of_credits=True, raw)` only when the
stream truncated with no `result` event *and* an exhaustion reason was
seen — otherwise a truncated stream surfaces as a bare `WorkerError`,
bypassing the auth/quota backoff and `die()`ing the run non-resumably.

The discriminator keys on `overageDisabledReason ∈
{"out_of_credits", "out_of_overage"}`, **not** `overageStatus:"rejected"`
— that field is a standing config state any org with overage disabled
emits on *every* `rate_limit_event` regardless of success, so keying on it
caused false positives (an unrelated crash inheriting a permanently
latched "out of credits" flag). An `org_level_disabled` truncation takes
the ordinary `WorkerError` path instead, never a pause.

The exception propagates through the existing asyncio cancellation chain
— `_invoke`'s `BaseException` guard terminates and reaps the in-flight
worker's full subprocess subtree, sibling wave-tasks cancel through the
same path — so no orphans remain.

A **rate-limit** resets on a clock, so the session-limit and
terminal-`status` cases auto-resume via `_sleep_then_reexec(st,
wait_seconds, reason)`: worktree-only cleanup, sleep, then `os.execv` the
orchestrator into a fresh process. **Out-of-credits does not reset on a
clock** — it clears only when a human tops up or the billing period rolls
over — so it does *not* auto-resume: `main()` cleans up worktrees, logs a
`leerie resume <id>` hint, and exits `EXIT_LOCKED` (75). Looping a fixed
backoff against genuine exhaustion would only spin against the wall and
burn the persisted worker budget on retries that cannot succeed.

We re-exec the orchestrator, not the launcher — it already runs inside
the container with state on disk, while the launcher is not baked into
the image. The `--max-workers` budget persists across the re-exec
(`worker_count` in state.json), so a repeatedly rate-limited run still
respects the cap. Cleanup runs before the sleep and removes every
worktree, so the re-exec'd `resume` finds a clean slate — a convenience,
not a guarantee, since cleanup cannot run if the process is SIGKILLed
(`setup-run.sh` reclaims a stale staging directory itself instead of
relying on a predecessor having tidied up). Ctrl-C during the sleep drops
to a manual `resume` (exit 130); SIGTERM/SIGHUP likewise (143/129); an
`os.execv` failure exits `EXIT_LOCKED` (75). In each case cleanup has
already run, so state and the run branch are intact for the manual
`resume`.

- If `reset_at` parsed cleanly, `wait_seconds` is the time until that
  moment plus a small margin.
- If the reset clause didn't parse, `wait_seconds` is a fixed
  `RATE_LIMIT_RETRY_BACKOFF_SEC` (300 s) — we poll: sleep, re-resume,
  bounded by the persisted worker budget. A premature retry just re-hits
  the same clean pause.
- Out-of-credits does not auto-resume at all: `main()` cleans up, logs a
  `leerie resume <id>` hint, and exits `EXIT_LOCKED`. The operator adds
  credits, then resumes.

Rationale for the fixed-backoff auto-resume (vs. the earlier "parse
failure → exit 75 manual resume"): with a fixed backoff no time is being
guessed — the trade is "retry in 5 min" vs "die and require a human," and
an early retry is a harmless no-op re-pause. Out-of-credits is excluded
from this reasoning since it has no reset at all — auto-resuming it would
spin against the wall until a human intervenes.

`_cleanup_on_abnormal_exit(st, full_purge=False)` is the single helper for
all four paths. Classification happens in `main()`'s try/except: SIGINT
raises `KeyboardInterrupt`; SIGTERM/SIGHUP raise `InterruptedBySignal` via
handlers installed at program start; `RateLimitedExit` is raised inside
the stream handler. SIGINT and SIGHUP are POSIX-only, guarded with
`hasattr(signal, ...)` so the orchestrator still runs (degraded) on
Windows.

A `die()` call (the documented clean-exit mechanism for known failure
modes) is *not* an abnormal exit. The user already got an actionable
error message; running a worktree cleanup pass is correct (the run was
mid-flight) but it is silent unless there were worktrees to clean.

**Detached orchestrator (remote mode).** In local mode the orchestrator is
PID 1 of the container — its lifetime *is* the run's lifetime, and the
user's terminal owns that container directly via `nerdctl run`. In remote
mode the same coupling would be a mistake: the actual work of a remote
run happens entirely inside the Fly Machine, and the launcher's host-side
role after provisioning is purely to stream the log back for the user's
eyes. Binding the orchestrator's *life* to that streaming channel (a
foreground `flyctl ssh console -C "python3 leerie.py"`) means a closed
laptop, a dropped WiFi connection, or an accidental Ctrl-C kills a run the
laptop wasn't doing any work for.

Leerie therefore starts the orchestrator **detached** on the Fly Machine.
The launcher pipes a small Python wrapper script via stdin to
`flyctl ssh console --pty=false -C "python3 -"`; the wrapper does
`subprocess.Popen(..., start_new_session=True, user="leerie",
group=<leerie gid>, env={HOME=/home/leerie, USER=leerie, PATH=mise+bin},
cwd="/work")` and records the PID in `orchestrator.pid`.
`start_new_session=True` is the portable equivalent of `setsid nohup`;
running as the leerie user with explicit env is required because the
ssh-console session lands as root with `HOME=/root` by default. The
ssh-console call returns immediately. A *second* ssh-console call then
pipes a tail-wrapper script to `sh -s` purely for the user's terminal —
the orchestrator is session leader inside the machine, the tail an
independent process on the host side, so stream death (Ctrl-C, broken
pipe, laptop closing, WiFi dropping) breaks the tail, not the
orchestrator.

This matches the prior-art mental model of comparable tools (`fly
machine`, Claude Code's `/bg` + `claude agents`, kubectl, tmux):
**sessions are the unit of management, not terminals** — leerie's session
is the run; the local terminal is just one way to observe it.

The run-id is the bridge: the launcher needs it *before* starting the
orchestrator (to know which `orchestrator.log` path to tail), but
normally the orchestrator generates its run-id internally during phase 1.
The launcher instead generates the slug + suffix host-side using the same
pattern and passes it as `--run-id <id>` — reusing the plumbing `resume`
already establishes — and the orchestrator's `--run-id` short-circuit
skips auto-generation.

**Remote pause-on-failure (Fly.io).** Local mode reaps the container's PID
namespace on every exit because the host filesystem holds the durable
record. Remote mode has the same durable record (the run branch and
`<state-root>/runs/<run-id>/`) but the Fly Machine is not free — keeping
it alive after failure has a real per-second cost, and destroying it
throws away in-machine state useful for diagnosis (logs, partial
worktrees, uncommitted edits).

The compromise: classify the orchestrator's exit code on the host side
and route to *stop* (preserves volume, frees compute), *destroy* (full
reap — machine and volume, since Fly reaps neither for us), or *leave
alone* (the user merely detached the local stream; the machine is still
working). With the detached orchestrator, the classification is "what
just happened on the host side?" rather than "how did the orchestrator's
run exit?" — the launcher process now exits when the *tail* finishes, not
when the orchestrator finishes. The reclassified table:

| Exit | Meaning | Disposition |
|---|---|---|
| `0` | tail saw orchestrator exit cleanly (or could not read exit code) | destroy after stream-back |
| `EXIT_NEEDS_ANSWERS=10` | clarification (plugin re-runs) | destroy (nothing to inspect) |
| `75` (EX_TEMPFAIL) | single-owner-per-run-dir refusal (`EXIT_LOCKED`) or a genuine EX_TEMPFAIL worker surface. NOTE: rate-limit / out-of-credits / parse-fail no longer exit 75 — they auto-resume in-process (see *Rate-limited → auto-resume*). | destroy (state in run branch; cheaper to re-provision) |
| `130` / `143` | host-side SIGINT / SIGTERM | **detach: leave machine alone, print reattach hints** |
| any other non-zero | worker/orchestrator failure (`die()`, etc.) | **pause: stop machine, write sidecar, notify** |

The tail wrapper reads the orchestrator's exit code from
`orchestrator.exit_code` (written by `main()`'s `except SystemExit`
handler) and uses it as its own exit code. When the file is absent (OOM,
SIGKILL, or a crash before the handler ran), the wrapper falls back to
exit 0, so uncontrolled exits still route through the clean-exit branch
where `fetch_branch` bundles whatever is on the run branch before
destroying.

The Ctrl-C row is load-bearing. Earlier versions treated rc=130 as
"user cancelled, destroy the machine" — but with the detach, rc=130
only means "user stopped watching"; the orchestrator on the machine is
still running, and destroying it would defeat the detach. The launcher
instead prints a banner listing reattach/pause/destroy commands and
exits without touching the machine: `leerie resume <run-id> --runtime
fly` to watch progress (`--shell` opens a bash shell instead), `leerie
stop --runtime fly` to pause cleanly, or `leerie kill --runtime fly` to
destroy.

The decision lives in the launcher (`scripts/remote/provision.sh`'s EXIT
trap), not the orchestrator — per §6 *Worker subtree termination* the
orchestrator stays runtime-agnostic, always exiting with the same codes,
and the launcher routes those through runtime-appropriate teardown.

`flyctl machine stop` (not destroy) on the pause branch preserves the
machine's filesystem; the orchestrator's state is already in
`<state-root>/runs/<run-id>/run.json` and the run branch holds the
committed work, so `flyctl machine start` brings the machine back from
disk without losing anything. Memory state is not preserved across a
pause — the run branch plus `<state-root>/runs/<run-id>/` are the only
durable record, and both already live on the machine's filesystem by
the time a pause fires.

Before `stop_machine`, the pause branch syncs the machine-side
`.leerie/runs/<run-id>/` directory to the host via the same tar-pipe
primitive `fetch_branch` uses — best-effort (bounded 60 s timeout,
failure logged but non-blocking) — giving the host a local copy of
`state.json` for a subsequent auto-detected `resume` and surfacing logs
for offline inspection without restarting the machine.

### Remote disk policy

**Every Fly-path run gets a per-machine volume by default.**
`FLY_VM_DISK_GB` defaults to `8` when `RUNTIME=fly` and unset. `provision.sh`
creates the volume before the machine, mounts it at `/work`, and the
pause-on-failure contract is unconditional — the volume survives
`machine stop`/`start` indefinitely. `/work` holds the durable workload:
the seeded repo, the run-state tree, and the per-subtask worktrees that
dominate disk footprint. The caches under `/home/leerie/.cache/...` and
the auth bundle at `/home/leerie/.claude` are bounded in size and
intentionally left on the rootfs — `seed_auth` re-runs unconditionally on
every resume, refreshing the auth bundle regardless of whether the rootfs
survived. The split is empirical, not absolute: a heavy pnpm-store or
cargo registry can still hit the rootfs cap on unusually large
monorepos; runs that exhaust caches should set `FLY_VM_DISK_GB` higher.
The rootfs's throughput cap (2,000 IOPS / 8 MiB/s) also compounds disk
pressure by slowing spillover; the volume avoids this since per-machine
tiers run 4k–32k IOPS.

**Volume lifecycle — leerie owns the reap, because Fly does not.** A Fly
volume outlives its machine (*"a Machine can be destroyed without
destroying its volume"*) and an orphaned one is a documented
**"unattached volume"** that bills per-GB-month indefinitely. There is
no platform-side lifecycle hook: `auto_destroy` only destroys the
machine, and `mounts` has no destroy-on-exit mode. Since every Fly run
creates a volume by default, an unreaped one is the default outcome of
any teardown path that forgets it — every path that destroys a machine
must also destroy its volume, and "the machine is already gone" is
precisely when the reap is still owed, not a reason to skip it. Gating
the reap behind a live-machine check inverts the requirement, and did:
it silently leaked the volume of every run whose machine died first.

Two platform facts fix the ordering, pulling opposite ways: the
volume→machine association (`attached_machine_id` / `config.mounts`)
vanishes the instant the machine is destroyed, so it must be looked up
**before** the destroy; but Fly refuses to destroy an attached volume,
so the reap itself must come **after**. Order: look up, destroy
machine, destroy volume. A *stopped* machine keeps its attachment, so a
paused run's volume stays untouched.

One residue is irreducible and deliberately unsolved: if a machine is
destroyed and the launcher dies before reaping the volume (SIGKILL,
crash), the association is gone from both sides and the orphan cannot
be attributed to its run (volume names are random). There is no
reconciliation sweep — "unattached" alone can't distinguish a true
orphan from a volume whose machine is seconds from attaching — so the
operator reaps these by hand (`flyctl volumes list` + `destroy`).

Six sidecar fields on `run.json` capture remote lifecycle state:

- `fly_machine_id` — written by `provision.sh` immediately after
  `flyctl machine run` succeeds, so a launcher that crashes before
  classifying still leaves a recoverable pointer to the machine.
- `paused_at` — ISO timestamp written either by the EXIT trap on the
  pause-on-failure branch or by an explicit `leerie stop <run-id>`.
- `pause_reason` — short tag (`worker-error`, `orchestrator-exception`,
  `finalize-failed`, `user-requested`).
- `killed_at` — ISO timestamp written by an explicit
  `leerie kill <run-id>`. Marks the run as terminated by user request;
  the machine has been destroyed and the run is no longer resumable.
- `sync_failed_at` — ISO timestamp written when the clean-exit branch
  of `decide_teardown` ran `fetch_branch` and it failed. The machine
  is left RUNNING (not stopped — see below); the user recovers by
  running `leerie finalize <id> --runtime fly` (retry sync + push)
  or `leerie kill <id> --runtime fly` (destroy after manually
  salvaging work).
- `sync_fail_reason` — short tag accompanying `sync_failed_at`
  (`sync-failed-on-clean-exit`).

These fields live on `run.json`. Since the run_id is the machine ID
(known at provision time), the run directory is created immediately
after `flyctl machine run` succeeds; `provision.sh` writes
`fly-machine.json` as a crash-recovery pointer, `run.json` is written
later by the orchestrator, and every run-id verb (`stop`, `kill`,
`finalize`, `resume`) uses the run_id directly as the machine ID — no
lookup needed.

`paused_at`, `pushed_at`, and `killed_at` are mutually exclusive — a
run cannot be in more than one terminal-or-paused state.
`sync_failed_at` is orthogonal (the machine is neither paused nor
destroyed; it's running with unsynced work) but mutex-checked
against `pushed_at` (a pushed run cannot be sync-failed) and
`killed_at` (a destroyed machine cannot be sync-failed). The
orchestrator's `_validate_run_json` enforces all invariants.

**Sync-before-destroy (load-bearing — the "never lose work"
contract).** The clean-exit branch (rc=0/10/75) must not destroy the
machine and hope the user runs `leerie finalize` later: the
orchestrator's committed work and `.leerie/runs/<id>/` live ONLY on
the machine until streamed back, so destroying it with no host-side
copy throws the work away unrecoverably.

`decide_teardown` therefore runs `fetch_branch` (git bundle of the run
branch + tar of `.leerie/runs/<id>/`) BEFORE calling `destroy_machine`,
and only destroys on confirmed sync success. On any sync failure
(network blip, bundle-creation failure), the machine is LEFT
RUNNING — not stopped — and a multi-line WARNING points the user at
three recovery commands:

  1. `leerie finalize <run-id> --runtime fly`  (retry sync + push)
  2. `leerie resume <run-id> --runtime fly`    (manual inspection —
                                  attaches to the live orchestrator's
                                  log, or drops into a shell with `--shell`)
  3. `leerie kill <run-id> --runtime fly`      (destroy AFTER user
                                  confirms work is safely on host)

The user owns the machine in this state. leerie does NOT auto-
destroy after a successful manual finalize either — the user must
explicitly `kill`.

**The user-visible verb surface.** Four explicit verbs cover the
remote run lifecycle, each doing exactly one thing:

| Verb | Effect |
|---|---|
| `leerie "task" --runtime fly` | Provision machine, detach orchestrator, tail log |
| `leerie stop <run-id>` | Clean pause (`flyctl machine stop`); resumable |
| `leerie resume <id>` | Smart resume — wakes a paused machine, attaches to a live orchestrator, or relaunches against an alive-but-orphaned machine, automatically |
| `leerie kill <run-id>` | Destroy machine, mark run terminated (irreversible) |

Plus `leerie list` (unified across local and remote, with `status
<state>` and `--runtime <local|fly>` filtering as orthogonal axes).
Status describes the run's lifecycle (`paused`, `killed`,
`done`, `sync-failed`, `in-progress`, `done-pushed-pr`, ...); runtime
describes where it ran (`local` or `fly`). `list --runtime fly`
short-circuits in the launcher and queries Fly directly via `flyctl
machines list --json`, so it surfaces machines launched from any host
repo (not just the cwd).

This separation matches the convention every comparable tool follows
(`fly machine start`/`stop`/`destroy`; kubectl's `delete` distinct from
a watched stream ending; tmux's `kill-session` distinct from `detach`).
Ctrl-C as a destructive verb was an artifact of the lifetime coupling;
with it removed, Ctrl-C reduces to its conventional meaning ("stop this
terminal-side activity") and destruction gets its own verb.

**Runtime auto-detection on run-id-bearing verbs.** When `resume`,
`stop`, `kill`, `accept-blocked`, `accept-integration`, or `finalize`
targets a run whose state directory contains a `fly-machine.json` or
`ec2-instance.json` sidecar and no explicit `--runtime` was given, the
launcher auto-promotes to `fly` or `ec2` respectively via the shared
`_auto_detect_run_runtime` helper (`_auto_detect_fly_runtime` remains a
thin Fly-only wrapper for call sites not yet migrated). All six verbs
wire real EC2 actions; no verb fails closed on EC2. `resume` promotes
`RUNTIME=ec2` exactly as it promotes `fly`, reaching the dispatch
branch's sidecar → `resume_instance()` path. `finalize` streams the run
back with `fetch_state_ec2()` and then runs the same `host_finalize`
Fly uses; because a stopped instance has no reachable target and EC2
reassigns its public IP on every stop/start, `finalize` wakes a paused
instance, fetches, and re-stops it only if finalize itself woke it —
the same wake → act → restore-prior-state discipline `accept-blocked`
uses. `finalize --force` is the one EC2 gap: it's a transport
boundary, not an omission — `force_finalize_remote()` and
`collect_subtrees_remote()` are both `flyctl ssh console`-only, and
finalizing without collecting un-integrated subtask branches would
push an incomplete branch.

`accept-blocked`'s EC2 action mirrors Fly's wake-mutate-pause dance:
resolve AWS credentials, wake the instance via `resume_instance()` if
stopped, mutate `state.json` over SSM (`ec2_remote_exec` — no ssh
keypair or hallpass wait needed), mirror onto the host copy when one
exists, and re-pause only if this verb woke it. `stop`'s EC2 action
resolves credentials, gates on `require_aws()`, resolves
`LEERIE_EC2_INSTANCE_ID` from the sidecar, and calls `stop_instance()`
(`aws ec2 stop-instances` — preserves the root EBS volume, same
semantics as Fly's `machine stop`). `kill`'s EC2 action resolves the
instance id, re-resolves its current SSH target (public IP changes on
every stop/start), and calls `_try_fetch_state_for_ec2_teardown` — the
same hook `decide_ec2_teardown`'s clean-exit branch uses — to sync back
BEFORE `terminate_instance()` (the one-way-ratchet invariant:
destroy-then-fetch would make paid-for LLM work unrecoverable). A
failed sync leaves the instance running; `flyctl` is never invoked for
an EC2 run.

When no sidecar exists, `stop`/`kill` probe for a live local nerdctl
container via `_is_local_container` (`nerdctl inspect <run-id>`).
`stop` uses `nerdctl stop` (SIGTERM → grace → SIGKILL, letting the
signal handler save state) or `aws ec2 stop-instances`; `kill` uses
`nerdctl kill` (immediate SIGKILL) or `aws ec2 terminate-instances`
(after the fetch-before-terminate sync), since the run is terminal.
`finalize` on a local run is inline. If the user explicitly sets
`--runtime local` on a Fly-originated run, `resume` warns but respects
the choice.

**Smart resume in remote mode.** `resume` is the single verb for
re-engaging with a remote run, regardless of the run's current state.
The launcher reads observed state and routes to the right behavior:

| Machine state | Orchestrator state | `resume` behavior |
|---|---|---|
| Stopped (paused) | n/a | Wake machine → re-seed → launch orchestrator → tail |
| Running | Dead | (Re-)seed if needed → launch orchestrator → tail |
| Running | Alive | Skip seed + launch → tail orchestrator.log |

The "machine running, orchestrator alive" branch is the §6 isolation
boundary's terminal-side surface — not a new privileged channel.
Detection is two-layered. **Early flock probe (resume path only):** on
`_resumed=true`, the launcher runs a lightweight flock probe via
`flyctl ssh console` right after `resume_machine` and before
`seed_auth`; a held lock (rc=75) skips seeding/launch entirely and
goes straight to attach, avoiding ~60 s of wasted seeding (SSH is
already warm since the machine was never stopped). Any other probe
failure falls through silently to `seed_auth`. **Launch-time flock
probe (belt-and-suspenders):** the launcher pipes a launch wrapper
through `flyctl ssh console -C "python3 -"`, which takes a fast-path
flock probe on the run directory (§6 *Single owner per run dir*) and
exits 75 if held — covering fresh provisions and any race the early
probe missed. Because `flyctl ssh console` collapses any non-zero
remote exit to rc 1, the launcher parses the real code out of stderr
(`_extract_flyctl_remote_rc`) so the rc=75 pivot fires correctly. Both
probes pivot to `_attach_to_live_orchestrator` (lib.sh) instead of
launching a duplicate.

The attach channel is `flyctl ssh console` against the run's Fly
Machine, proxied through Fly's hallpass + WireGuard mesh, giving a
real PTY at `/work` — default `tail -F` of the orchestrator log,
`--shell` for a bare bash shell. No sshd in the image, no key
management, no public exposure. The orchestrator is unaware of attach
(a launcher-host gesture, per §6's "isolation is the launcher's
concern"), and the same mechanism serves the interactive-terminal
case, the mid-run attach case, the paused-machine failure-inspection
case, and reattaching after a Ctrl-C detach or closed-laptop
disconnect — one command, `leerie resume <run-id>`, covers all four;
the orchestrator never notices, only the local view paused.

State contract: `scripts/remote/provision.sh` writes a PID-keyed
record at `$LEERIE_STATE_HOST_DIR/remote/$$.json` right after
provisioning and copies it to
`.../runs/<run-id>/fly-machine.json` once the run-id is known. The
pointer survives `destroy_machine` so the chain wave loop can read it
post-wait; stale pointers are filtered via `kill -0`. `leerie resume`
resolves the machine via either path — disambiguated by an explicit
run-id, or by the single active launcher record when none is given.

Local mode keeps its inline `resume` behavior by design: local runs
are synchronous foreground processes (`nerdctl run --rm`, no
backgrounding), so there is no detached container to attach to —
`resume` just re-execs the orchestrator against `state.json`.

**Shallow seeding for heavy repos.** The fresh-provision seed
(`seed_repo_clone`) delivers the host's committed state as a
`git bundle create - --all` piped over `flyctl ssh console`. `--all`
packs full history of every ref; for a repo with deep history or large
committed blobs, that bundle can be hundreds of MB — enough to exceed
`LEERIE_SEED_TIMEOUT_S` (default 600 s) and fail the run before any
worker starts. The bloat is history, not the working tree (build
artifacts / `node_modules` are already excluded — a bundle carries
committed objects only), so the lever is history depth, not disk usage.

Above a size threshold (`.git` larger than a bounded default), the
seed switches to a **shallow** transport: the host makes a throwaway
`git clone --depth=N` of the working branch, tars only its `.git`
directory, pipes that over the same channel, and the machine untars +
`git checkout`s. This ships a fraction of the bytes (a depth-50 clone
of a 960 MB-history repo is ~140 MB vs. a 420 MB full bundle) while
staying correct on every downstream path:

- **Tar-of-`.git`, not a shallow bundle.** `git bundle` refuses a
  shallow repository (its grafted commits have parents the pack can't
  reach), so tarring the shallow clone's `.git` ships the pack —
  including the `.git/shallow` graft boundary — verbatim instead.
- **Stays NFC-safe.** Tarring `.git` preserves the same
  filename-normalization guarantee bundles have: object contents never
  serialize filenames through the transport, and the receiving Linux
  git creates working-tree filenames natively on `checkout`. (Tarring
  the working tree would reintroduce the macOS BSD-tar NFC→NFD bug.)
- **Fetch-back is unaffected.** `fetch-branch.sh` bundles the worker's
  run branch *by name*, not `--all`; it cites the shallow boundary
  commit as a prerequisite, the host already has that commit, so
  `bundle verify` + `fetch` reassemble full history and the graft
  never leaks host-side.
- **PR diff stays correct.** A real `git clone --depth=N` keeps the
  working branch's true tip hash, so `git merge-base` on the host
  resolves the run branch correctly and the PR diff shows only the
  worker's changes — a synthetic re-rooting would break the merge-base.

Submodules are orthogonal (a `--depth` clone doesn't populate
`.git/modules`, so the existing per-submodule bundle machinery carries
over unchanged). Cost: workers see only depth-N history (the machine
can't deepen — no origin credentials). Depth and the size threshold
are operator-tunable; setting depth to full disables shallow seeding.
Small repos stay on the full bundle. The shallow path also falls back
to full bundle for a detached HEAD or a branch name outside a
conservative shell-safe charset (the machine-side reconstruction
interpolates the branch into a `git checkout` over `sh -c`).

This switch is confined to fresh provisions (`seed_repo_clone` always
wipes `/work`); mid-run re-seed (below) never re-clones. Corollary:
`resume` probes whether the initial seed produced a valid `/work` git
repo, and re-runs the full seed rather than dead-ending on a
dirty-only re-seed if a prior seed died before completing.

**Mid-run re-seed (remote mode).** The host's working tree keeps
evolving after a remote run starts (new commits, dirty edits, a new
submodule), and the machine needs a user-triggered way to pick that up
without destroying its volume. Two surfaces share one mechanism — an
explicit `leerie re-seed <run-id>` subcommand and an implicit
auto-re-seed inside `leerie resume <id> --runtime fly` — both wake the
machine if stopped, run a safety check, and call the same
`seed_repo_dirty` helper the fresh-provision path uses.

Three operations, in order ("current laptop state" = host commits plus
host dirty edits):

1. `flyctl machine start` (if stopped) + `wait_for_started`.
2. Refuse re-seed when `/work` on the machine has uncommitted
   tracked changes outside `.leerie/` — those represent in-flight
   worker edits that haven't yet been committed to a per-subtask
   branch, and silently clobbering them produces a wrong PR.
   `--force` bypasses.
3. `seed_repo_dirty` — recompute `git status --porcelain` on the
   host, rsync the dirty set over `flyctl ssh console -C "rsync
   --server ..."` (via the `fly_rsync_wrapper` helper in
   `lib.sh`). The full-history clone on the machine is preserved
   (never re-cloned, which would obliterate the run branch).

The dirty set is computed on the host, where worktree paths structurally
cannot appear (worktrees live only on the machine). A defensive filter
excludes `.git/*` and non-whitelisted `.leerie/*` paths before handing
the file list to rsync's `--files-from=-`, guarding against a future
change letting host-side paths name worktree files. The three committed
config files (`.leerie/config.toml`, `.leerie/Dockerfile`,
`.leerie/.leerie-setup.sh`) pass through the filter as repo-owned
declarations workers need.

The repo-local `.claude/` directory is force-included even when
`.gitignore` excludes it (the common case) — workers need the repo's
hooks, agents, skills, commands, and settings, and bundles can't carry
gitignored content. Every fresh seed and mid-run re-seed delivers the
host's current `.claude/` to the machine.

Resume auto-re-seeds by default; `--no-re-seed` opts out for the
rate-limit auto-resume case where no host edits happened. The user
picks the moment (by typing `resume`), so the seed is treated as
authoritative.

### EC2 runtime lifecycle

`--runtime ec2` provisions and runs the orchestrator on an AWS EC2
instance (`scripts/remote/aws-credentials.sh`, the launcher's AWS
region/profile resolution, the `boto3`/`botocore` pin, and the
launcher's `RUNTIME=ec2` dispatch — see IMPLEMENTATION.md "Runtime
mode" / "AWS region/profile prefs"). This is the EC2 counterpart to the
Fly design above: it reuses Fly's stage names and dispositions wherever
the platforms agree, and calls out explicitly where EC2's semantics
diverge.

**Stage mapping.** The five Fly stages above (provision → wait-ready →
seed → detached-orchestrate → teardown) carry over one-for-one:

| Stage | Fly (shipped) | EC2 (this design) |
|---|---|---|
| Create | `flyctl machine run` | `aws ec2 run-instances` (the `aws` CLI, not `boto3` — see "boto3 usage boundary" in IMPLEMENTATION.md: the host has no pip surface, so every host-side EC2 call shells out the same way `flyctl` does for Fly) (AMI, instance type, key pair, security group, subnet — the `LEERIE_EC2_*` vars already reserved in IMPLEMENTATION.md's env-forwarding deny-list) |
| Wait-ready | poll `flyctl machine status` for `started`; hallpass warm-up probe | poll `describe-instances` for `State.Name == "running"`, then `instance-status-ok` + `system-status-ok` (`describe-instance-status`) — a `running` EC2 instance is not yet SSH/SSM-reachable, unlike a Fly Machine where `started` and hallpass-warm are close together |
| Seed | `seed_auth` + `seed_repo_clone` over `flyctl ssh console` | same two steps, transport substituted (see below) |
| Orchestrate | detached `Popen` via ssh-console wrapper, PID recorded, host tails via a second ssh-console session | same detached-`Popen` pattern, launched over the substituted transport; run-id-before-orchestrator-start constraint (line 2208) is unchanged — the launcher still generates the run-id host-side before create, since it needs the id to name `orchestrator.log`'s path ahead of the create call completing |
| Teardown | classify exit code → stop / destroy / detach (the reclassified table above) | same table, `flyctl machine stop`/`destroy` → `aws ec2 stop-instances`/`terminate-instances` |

The pause-on-failure classification table (exit code → disposition) is
runtime-agnostic by construction (§ above: "the orchestrator ... always
exits with the same exit codes regardless of where it runs, and the
launcher routes those exit codes through the runtime-appropriate
teardown") — EC2 needs no new table, only a new teardown implementation
of the same three dispositions (stop / destroy / leave-alone).

**Image delivery — how the leerie image reaches the instance.** Fly's
`ensure_image()` pushes a self-contained image to
`registry.fly.io/$APP:$VERSION[-$HASH]` and `flyctl machine run <tag>`
pulls it. EC2 has no registry-pull equivalent for an *instance* the way
`machine run` has for a *container* — it boots from an AMI (a full
disk-image snapshot), not a per-run pulled artifact. Three strategies:

1. **Bake into the AMI.** The operator builds (once, out of the run's
   critical path) a custom AMI with the orchestrator source, Python
   3.10+, and every OS-level dependency `.leerie-setup.sh` would
   otherwise need root for; `RunInstances` boots straight into a
   ready-to-seed instance, named by `LEERIE_EC2_AMI`. No push, no pull,
   no per-run build step — cost is paid once by whoever maintains the
   AMI (a Packer/EC2 Image Builder pipeline, out of scope for leerie
   itself). **This is the default this design adopts.**
2. **Push to a registry (ECR), pull at boot.** The direct Fly analog.
   Rejected: it reintroduces registry-auth surface a second time
   (`aws ecr get-login-password`), requires a container runtime inside
   the EC2 instance, and adds per-provision pull latency with no
   evidence it's necessary. Worth reconsidering only if leerie ever
   needs the same image shared verbatim across EC2 and a containerized
   runtime.
3. **User-data pull-and-build.** The instance clones leerie fresh on
   every boot off a generic stock AMI. Rejected as default:
   multi-minute build cost per `RunInstances` call, requires outbound
   internet egress at boot (the security-group/NAT surface the
   SSM-only transport below otherwise avoids), and turns registry
   flakiness into a provisioning failure mode. Remains a reasonable
   manual bootstrap fallback (`LEERIE_EC2_AMI` pointing at a stock AMI
   plus a documented user-data script) for an operator without a
   custom AMI yet.

**IAM actions the chosen strategy requires.** Baking into the AMI keeps
the build-time IAM surface (Packer/Image Builder's own
`ec2:CreateImage`/`RegisterImage`/`ec2:RunInstances`) entirely outside
leerie's per-run credential path — a separate, operator-owned pipeline
with its own IAM role. leerie's own AWS identity needs exactly this,
and nothing more:

- `ec2:RunInstances`, `ec2:DescribeInstances`, `ec2:DescribeInstanceStatus`
  (create, wait-ready)
- `ec2:StopInstances`, `ec2:StartInstances`, `ec2:TerminateInstances`
  (teardown, pause/resume)
- `ec2:CreateTags` (tagging the instance with the run id for
  `_discover_runs`-style orphan recovery, mirroring how Fly machine
  metadata carries the run id)
- `ssm:StartSession`, `ssm:SendCommand`, `ssm:TerminateSession`,
  `ssm:DescribeSessions`, plus the SSM Agent's own instance-side role
  (`AmazonSSMManagedInstanceCore` attached to the instance profile
  named by a future `LEERIE_EC2_INSTANCE_PROFILE`-shaped knob, not to
  leerie's caller identity) for the transport-substitution stage below
- No `ecr:*` actions and no `iam:PassRole` beyond the SSM instance
  profile attachment above — bake-into-AMI needs neither a registry
  pull permission nor a broad user-data role, a security argument for
  strategy 1 over 2/3: the run-time IAM surface stays minimal and
  auditable.

**New `LEERIE_EC2_*` knobs implied.** None beyond what IMPLEMENTATION.md
already reserves. `LEERIE_EC2_AMI` names the chosen artifact under all
three strategies (a custom AMI under 1, a stock AMI under fallback 3;
strategy 2 introduces no knob and is rejected). No
`LEERIE_EC2_INSTANCE_PROFILE` var is added now — that's a
provisioning-subtask-level `RunInstances` parameter, not an
image-delivery decision — flagged here so the subtask wiring
`IamInstanceProfile` doesn't have to rediscover that SSM's instance-side
role must come from somewhere.

**EBS volume lifecycle — the opposite default from Fly, not the same
discipline.** Fly's "Remote disk policy" places a manual reap
obligation on leerie because a Fly volume has no destroy-on-exit hook.
EC2's default is the mirror image: AWS's `DeleteOnTermination=true`
deletes the root EBS volume automatically when the instance is
*terminated* — a platform-enforced hook Fly lacks, so for the default
EC2 shape the reap is AWS's problem, not leerie's. Three cases:

1. **Root volume only, default `DeleteOnTermination=true`.**
   `RunInstances` with a single root EBS volume and no block-device
   override; AWS reaps it on `TerminateInstances` — no leerie-side reap
   code, no volume-orphan test surface. **This is the default this
   design adopts.**
2. **Stop, don't terminate, on pause.** Like Fly's `machine stop`,
   `StopInstances` leaves the root volume attached (EC2 keeps billing
   it while stopped, mirroring Fly) and never touches
   `DeleteOnTermination` — that attribute is termination-scoped, not
   stop-scoped. Fly's stop/start-preserves-filesystem contract carries
   over unchanged: an EC2 pause uses `StopInstances`, never
   `TerminateInstances`.
3. **A future secondary EBS volume** would reintroduce Fly's exact
   problem — additional (non-root) volumes default
   `DeleteOnTermination=false` — and would need the same reap
   discipline and ordering (look up while attached, destroy instance,
   destroy volume) Fly's "Remote disk policy" pins. Not built now since
   no secondary volume exists; flagged so a future subtask doesn't
   assume EC2 is exempt from the discipline Fly needed.

**Transport substitution for `flyctl ssh console`.** Two roles need
replacing: piping the detached-orchestrator launch wrapper, and
opening a session for `resume`/`--shell` attach and log tailing. This
design picks SSM Session Manager over SSH:

- **SSM Session Manager** (`aws ssm start-session`) needs no inbound
  security group rule, no key-pair distribution, and no public IP — the
  preinstalled SSM Agent calls out to the SSM service over HTTPS, the
  same "no sshd, no key management, no public exposure" property Fly
  gets from hallpass + WireGuard. Auth flows through the same AWS
  credential chain and IAM already established for EC2, rather than a
  parallel key-pair-management surface.
- **SSH** (a managed key pair, `LEERIE_EC2_KEY_NAME`, inbound port-22
  rule) is the closer textual analog to `flyctl ssh console`, but
  requires provisioning/rotating a key pair and opening network ingress
  — exactly what SSM avoids. Remains available as a fallback for
  operators whose account policy disallows SSM, but is not the
  default.

`aws ssm start-session --target <instance-id> --document-name
AWS-StartInteractiveCommand --parameters command="python3 -"` is the SSM
analog of `flyctl ssh console --pty=false -C "python3 -"` for the
detached-launch wrapper; the same analog with `command="tail -F
orchestrator.log"` (or a bare interactive shell for `--shell`) serves
the attach/tail role. The detached-`Popen` pattern itself — session
leader inside the instance, independent host-side tail process, stream
death does not touch the orchestrator — is transport-agnostic and
carries over unchanged from the Fly design (lines 2185-2206 above).

**Pause/resume semantics.** EC2 `stop`/`start` maps directly onto Fly's
`machine stop`/`machine start`: `StopInstances` preserves the root EBS
volume (case 1 above); the instance's public IP may change on restart
unless an Elastic IP or a persistent ENI is used, so its current
address is resolved via `describe_instances` on every resume rather
than cached, mirroring Fly. `TerminateInstances` is the `kill` /
clean-exit-after-sync-success counterpart to `flyctl machine destroy`.
The sidecar fields (`paused_at`, `pause_reason`, `killed_at`,
`sync_failed_at`, `sync_fail_reason`) are runtime-agnostic in shape
(they describe *when* and *why*, not *how*) and the EC2 path reuses
them verbatim; `ec2_instance_id` plays the role `fly_machine_id` plays
today.

**Run identifier.** `run_id` is "the container/machine ID assigned by
the container runtime" (DESIGN's "The run identifier"). An EC2 instance
ID (`i-0123456789abcdef0`) fills the same role — known at
`RunInstances` time, before the orchestrator starts, no deferred
computation, no rename. Two Fly-specific coupling points needed
generalizing: `provision.sh` writing `fly-machine.json` as the
crash-recovery pointer, and `_discover_runs`'s (DESIGN §6) lookup for
that filename to recognize a pre-`state.json` crash-recoverable orphan.
The generalization is a same-shaped `ec2-instance.json` sidecar
(instance id, region, created-at) plus widening the orphan scan to
check either sidecar — `fly-machine.json` is unchanged for Fly runs.

---

## 6½. Per-repo dependency provisioning

The container image ships a fixed base toolchain; every target repo
ships its own — different language versions, package managers,
lockfiles. Two things go wrong if the orchestrator just runs workers
against a fresh checkout:

- **Dependencies are missing.** A Next.js repo needs `pnpm install`
  before any worker can `pnpm lint` or `pnpm test`. A Django repo
  needs `uv sync`. A Go repo needs `go mod download`. The container
  has none of these installed for the specific repo.
- **Runtime versions are wrong.** A Next.js repo with `.nvmrc:
  20.11.0` does not behave correctly under the image's baked Node LTS.
  A Django repo with `.python-version: 3.11.7` should not run on
  Python 3.12. Mismatched runtimes manifest as opaque failures far
  from the cause — a worker reports a passing test under the wrong
  Python, the integration step finds the version mismatch later, the
  user sees a confusing failure.

A third compounding factor: `git worktree add` checks out tracked
files only — untracked artifacts (`node_modules`, `.venv`, build
outputs) are not copied, so every per-subtask worktree starts empty
even if the host repo were fully installed. The orchestrator handles
this in two layers: runtime versions and the optional setup hook are
pre-installed *in* the container before any worker runs (cross-cutting
state every worker shares); dependency installs (pnpm, pip, cargo,
etc.) happen per worktree, against shared package-manager caches.

**Who runs that install.** Both the orchestrator and the workers do,
for different trees and moments. Since the orchestrator took over
build/lint/test execution (§9), it needs deps present in a worktree
before it can measure anything there, so it applies the recipe itself
**lazily, on the first axis it actually measures for that worktree, and
at most once per worktree per process**. Workers still install for
their own targeted work, and the recipe stays in their prompt for that
reason.

Lazy rather than eager at worktree creation is not cosmetic: a
config-only or docs-only subtask correctly skips the install (measured,
44 of 91 subtasks touched zero source files), and pre-installing for
every worktree would hand that cost back. What laziness removes is the
*repeat*: 263 installs ran across 161 worker logs — ~2.8 per worktree,
since a subtask's implementer and conformer share one — converging on
the same state each time. The memo is keyed on the resolved absolute
worktree path and lives in the process, not in run state: it records a
filesystem fact, and re-installing once after a `resume` is correct
since a fresh container starts with an empty worktree.

The orchestrator addresses both with a dedicated phase between
classification and planning, layered top-to-bottom by determinism:

1. **`.leerie-setup.sh` hook.** Optional, repo-owned. If the repo
   needs user-space tooling the language layer can't install — a
   language version mise supports beyond the LTS bake (Ruby, Java,
   Rust), an additional CLI tool under `~/.local/bin`, pre-populated
   fixtures — the repo commits a script that handles it. The
   orchestrator execs it inside the container as the non-root `leerie`
   user (the image deliberately does not ship `sudo`). Repo author
   controls trust; the script runs in the same container that runs the
   workers.

   System packages requiring root (apt-get-installable libraries,
   anything writing to `/usr/*` or `/etc/*`) are out of scope for the
   hook — the container's unprivileged user model can't satisfy them.
   A repo with that need maintains a fork of the leerie Dockerfile
   that installs the package at image-build time and overrides
   `IMAGE_TAG`.
2. **Runtime version resolution.** The orchestrator delegates to a
   polyglot version manager that reads the repo's existing version
   declarations (`.nvmrc`, `.python-version`, `.tool-versions`,
   `rust-toolchain.toml`, `.go-version`). Matching toolchain versions
   install into a cache that survives across runs. If a repo declares
   nothing, the image-baked LTS for Node and Python is the floor — the
   resolver checks the per-run cache first, falls through to the
   image-baked layer. Runtime selection has no model in the loop; the
   version manager's parser is the enforcement.
3. **Deterministic install-command detection.** A lockfile-keyed table
   maps observable file presence to the install command(s): a pnpm
   lockfile means `pnpm install`, a `uv.lock` means `uv sync`, a
   `Gemfile.lock` means `bundle install`. Polyglot repos (Rails with
   both a Ruby lockfile and a JS one) emit *all* matching commands, not
   the first match — silently dropping a frontend install would leave
   half the workers broken. When the table returns a non-empty result
   the orchestrator uses it; no model in this path either.
4. **LLM provision worker — fallback.** When the table returns empty
   (Java with Gradle, a bare `pyproject.toml` without lockfile, a
   polyglot Makefile-driven setup), the orchestrator invokes a
   `claude -p` worker whose only job is reading the repo's README and
   configuration files and emitting a JSON recipe. The recipe is
   schema-validated, the commands inside it are restricted by an
   argv-allowlist, and any deviation from the schema rejects the
   worker. This is a *deliberate* exception to §12 — see below.
5. **Persistent out-of-repo dependency bake.** Dependencies are
   installed once at image-build time into persistent paths outside
   `/work`, so a fresh worktree inherits the bake with zero (or
   minimal, for Node) install cost. The bake targets:

   - **Python:** `/opt/venv` — a virtual environment created via `pip`
     or `uv` from the repo's `requirements.txt`, `pyproject.toml`, or
     `Pipfile.lock`. Workers activate it via `ENV VIRTUAL_ENV=/opt/venv`
     and `PATH`.
   - **Ruby:** `/opt/bundle` — Bundler installs gems here via
     `BUNDLE_PATH=/opt/bundle`. Workers inherit the env var and find
     gems without a per-worktree `bundle install`.
   - **Rust:** A pre-populated `CARGO_TARGET_DIR` (build artifacts)
     plus a warmed `CARGO_HOME` (registry cache). A `cargo build` in a
     worktree hits no network and reuses compiled dependencies.
   - **Go:** A pre-populated `GOCACHE` (build cache) plus a warmed
     `GOMODCACHE` (fetched modules). A `go build` in a worktree is
     network-free and reuses the module cache.
   - **Node/pnpm:** A warmed pnpm content-addressable store with
     `frozenStore` set. `node_modules` cannot be fully baked — it must
     live inside the repo tree to resolve — so the residual per-run
     step is a fast, network-free, extract-free `pnpm install
     --offline --frozen-lockfile` that relinks the baked store into
     the worktree: not zero-cost, but no download or extraction, only
     symlink creation.

   **Rust and Go require a discardable dummy source file at
   build time.** Neither `cargo fetch`/`cargo build` nor `go
   build` will populate their respective build-artifact caches
   (`CARGO_TARGET_DIR` / `GOCACHE`) against a manifest-only build
   context — `cargo` refuses to parse a manifest with no build
   target at all, and `go build ./...` against zero `.go` files
   is a silent no-op (only `go mod download`'s `GOMODCACHE` warms
   without one). This is a well-documented Cargo/Go Docker-layer-
   caching workaround (no `cargo build --dependencies-only`
   exists): emit a throwaway `src/main.rs` (`fn main() {}`) or
   `main.go` into the build scratch dir, run the fetch/build step
   against it to force the dependency graph to compile, then
   discard the dummy file — never `COPY` it into `/work`. Verified:
   a real worktree's subsequent build against real source then
   gets a genuine cache hit from the same baked cache, fully
   offline, from a different directory.

   **The Rust bake step must NOT use `cargo build --release`.**
   Cargo keys its build cache by profile — `debug/` and
   `release/` are separate subtrees under `CARGO_TARGET_DIR` —
   so a `--release` bake produces **zero cache benefit** for a
   plain `cargo build`/`cargo test`, both of which default to the
   debug profile. Reproduced live: a `--release` bake left a
   worktree's subsequent `cargo build` recompiling every dependency
   from scratch. Go has no equivalent split, so this trap is
   Rust-specific.

   **Immutability invariant:** The baked layer is shared and
   read-only across up to `max_parallel` (default 5) concurrent
   worktrees. A worktree that changes a dependency (edits
   `package.json`, `requirements.txt`, `Gemfile`, `Cargo.toml`,
   etc.) must materialize its own private, mutated layer rather
   than writing to the shared bake — this prevents both staleness
   (a worktree silently resolving deps it changed) and
   cross-worktree corruption. Node, Rust, and Go are naturally
   safe to bake: their package managers use content-addressed
   stores or input-hash-keyed caches, so concurrent access is
   read-only by design.

   **Python requires a clone-then-delta approach.** A `.pth`-file
   overlay or a `--system-site-packages` venv does not correctly
   handle dependency *removal* or a fresh venv's isolation from
   another venv's packages. The mechanism is `cp -r /opt/venv` into
   a private copy, then apply the dependency delta via `pip
   install`/`pip uninstall`. No `pyvenv.cfg` editing or `bin/`
   shebang relocation is needed — `pyvenv.cfg`'s `home =` line
   points at the system Python install and is unaffected by moving
   the clone. The one real trap is invoking the clone's `bin/pip`
   directly: that script's shebang is hardcoded to the *original*
   `/opt/venv`'s python binary, so `bin/pip install`/`uninstall`
   silently operate on the shared `/opt/venv` instead of the clone,
   corrupting the bake for every other concurrent worktree
   (reproduced live). The fix is an invocation convention: always
   run `<clone>/bin/python3 -m pip install|uninstall`, never
   `<clone>/bin/pip` — `-m pip` runs pip as a module inside that
   interpreter's own `sys.prefix`, sidestepping the shebang. This
   materializes a full private environment only when a subtask
   actually mutates dependencies — the common case consumes the
   shared `/opt/venv` directly with zero clone cost. `uv sync`
   silently ignores `VIRTUAL_ENV` unless `--active` is passed, and
   `pipenv` has long-standing bugs about not respecting an
   already-active venv at all — the bake sidesteps both by
   installing the tool itself into `/opt/venv` via pip at build
   time, so the tool's own `/opt/venv/bin/<tool>` resolves
   `sys.prefix` correctly by construction.

   **Cache invalidation is preserved.** The existing rebuild-decision
   mechanism — SHA-256 of every dependency-input file (lockfiles,
   manifests, workspace `package.json`s, `patches/`, `.npmrc`) folded
   into the generated Dockerfile, driving a `.dockerfile-hash`
   rebuild check — continues to work; only the install *target*
   changes from `/work` to `/opt/*`. A dependency-input change
   triggers a full image rebuild; an unrelated source-file change
   does not. The cost — minutes per rebuild — is paid once across all
   subsequent runs.

   **config.toml's role narrows to residual-only.** The file no
   longer represents "what gets installed per run" broadly — it
   holds only the irreducible residual that cannot be baked: typically
   empty for Python/Ruby/Rust/Go, and just the offline-relink note for
   Node/pnpm. The `dep_capture` worker (*Auto-capture* below) always
   runs at finalize time, even with a committed `.leerie/Dockerfile`,
   and writes only residual dependencies workers executed but could
   not bake — so `config.toml` grows only when new residuals appear,
   never churning on every baked-dep change.

   **Permissions under rootless containerd.** The baked `/opt/*`
   directories must be **root-owned and world-readable** (`drwxr-xr-x
   root:root`), not chowned to the `leerie` user. This is the
   inverse of normal Docker intuition and is load-bearing under
   rootless containerd. Rootless drops privilege via `unshare
   --user --map-user=$(id -u leerie)`, a single-entry UID map
   (outer UID 0 → inner `leerie`) that leaves outer `leerie`
   *unmapped*. An image-layer directory explicitly chowned to
   `leerie`'s non-zero UID appears as `nobody/65534` to the
   privilege-dropped process — traversable via mode-755 "other"
   bits but not writable. A root-owned directory, by contrast, is
   writable because outer root maps to inner `leerie`. Rootful
   (Colima/macOS, Fly/EC2) needs the opposite: `runuser -u
   leerie` is a real UID switch with no remap, so
   `container-entry.sh`'s rootful guard applies literal `leerie`
   ownership at runtime for `/home/leerie` (user dirs), but
   `/opt/*` (shared cross-worktree state) stays root-owned in the
   image. See `CLAUDE.md` "Evaluate every ownership/permission
   change" and `tests/test_tmp_cache_writable.py` /
   `test_home_leerie_ownership.py` for the pinned form.

   The detected recipe is still **persisted to state and injected
   into the implementer and conformer prompts as a
   `PROVISION_RECIPE:` advisory block**, but its role has narrowed:
   informational for baked ecosystems (Python/Ruby/Rust/Go, shows
   what was baked), and the residual offline-relink command for
   Node. Each worker reads the recipe and decides whether its
   subtask needs the residual step (a config-only or docs-only
   subtask doesn't; a "run the tests" subtask does). The host's
   checked-out source tree and tracked dep artifacts
   (`node_modules/`, `.venv/`, `target/`, etc.) are never written to
   by leerie's install path — `.leerie-setup.sh` (user-opt-in) is the
   only path leerie ever modifies under the host repo (run state
   lives outside the repo at `<state-root>`).

### The §12 carve-out

Step 4 is the only place in leerie where an LLM-generated artifact
gets persisted and shown to other workers as authoritative content.
§12's central principle is that prompts are advisory and code
enforces; an LLM-generated install plan that the orchestrator then
*renders verbatim into downstream worker prompts* needs the same
containment any other LLM-to-code path would. Three constraints
contain it:

1. **It only fires when the table returns empty.** The 80% of repos
   with conventional lockfiles never reach the worker. The model sees
   the genuinely ambiguous tail, where human judgment would be doing
   the work anyway.
2. **The recipe is mechanically bounded.** Every command's `argv[0]`
   must come from a fixed allowlist of package managers; shell
   metacharacters and directory traversal are rejected. The worker
   cannot emit `sudo`, pipe into `sh`, or reach outside the repo —
   this is what makes the prompt-injection safe, since the validator
   ensures the rendered `PROVISION_RECIPE:` block carries only argv
   sequences from a known-safe vocabulary. The §12 guarantee lives in
   the validator, not in any worker prompt.
3. **It is the only documented exception.** Any future feature that
   wants to render LLM-generated content into a downstream worker
   prompt needs its own §-level justification, not a pointer to this
   one — documenting the carve-out explicitly is what prevents it
   from becoming precedent.

The alternative — refusing the run when the table doesn't match —
would be strictly more §12-compliant but worse for the user. The
carve-out is a deliberate trade.

### Resume

Provisioning runs inside the same fresh-run branch of `_orchestrate()`
that runs classify, plan, and schedule — none of which re-execute on
`resume`. The resume path loads state and jumps to execution; the
recipe lives in state, the version-manager cache survives across
runs on disk, and workers see the right toolchain without anyone
re-running provisioning.

A successfully finalized run (`finished_at` set AND `current_phase`
== "phase 6: finalize") is terminal — `resume` returns immediately
without re-executing phases 4→5→6. Without this guard, a resume of
a completed run re-runs setup-run.sh + finalize.sh + cleanup.sh,
creating a window where a concurrent `decide_teardown` (from the
prior exit's launcher child) can race and destroy the machine.
The `die()` handler also sets `finished_at` (for `fetch_branch`
discovery) but leaves `current_phase` at whatever phase died — those
runs ARE resumable and fall through normally. (See *§12*.)

### Declared BLT commands

A repo may commit `.leerie/config.toml` with explicit `build`, `lint`,
and/or `test` keys. When present, these override the corresponding axis
from `_infer_build_lint_test()`. Missing keys fall through to inference.
An empty-string value means "not applicable" — same convention as
today's inference — and is preserved rather than replaced by inference.
This is the "CI yaml" analog: the repo author tells leerie exactly how
to build, lint, and test, the same way they tell GitHub Actions.

The file also accepts a `setup_packages` key (comma-separated apt
package names) that triggers per-repo image auto-generation (see below);
it is not consumed by BLT resolution.

Resolution is handled by `resolve_blt(repo_root)` (calls
`_load_blt_config()` first, then fills missing axes from inference),
which is what both `_run_conformance_phase` and `_run_final_conformance`
call — neither calls `_infer_build_lint_test` directly any longer.

### Per-repo container image

System packages requiring root (C libraries for native gems, fonts,
specialized tooling) cannot be installed by `.leerie-setup.sh` — that
hook runs as the unprivileged `leerie` user. A repo that needs such
packages commits `.leerie/Dockerfile` that extends the base image with
`ARG BASE_IMAGE` / `FROM $BASE_IMAGE`. The launcher builds a derived
image tagged `leerie-repo/<repo-id>:<version>` (where `<repo-id>` is
derived from the git remote URL, sanitized to tag chars) and uses it for
all subsequent `nerdctl run` invocations. When no `.leerie/Dockerfile`
exists but `.leerie/config.toml` declares `setup_packages`, the launcher
auto-generates an apt-install Dockerfile and proceeds through the same
build path. A committed Dockerfile always takes precedence — `setup_packages`
is ignored when both are present.

Rebuild is triggered by any of: the derived image is absent, the sha256
of the Dockerfile changed (stored as `<base_version>:<sha256>` at
`$LEERIE_STATE_HOST_DIR/.dockerfile-hash`), or the base version changed.
A second run with an unchanged Dockerfile skips the build entirely.

The `nerdctl run` image argument uses `${REPO_IMAGE_TAG:-$IMAGE_TAG}`,
so the base image is used transparently when no repo Dockerfile is
present.

**Fly runtime variant.** On `--runtime fly` the same `.leerie/Dockerfile`
triggers a derived image at `registry.fly.io/$APP:$VERSION-$HASH` where
`$HASH` is the first 12 hex characters of the Dockerfile's sha256. Before
`resolve_fly_image_tag()` is called, `_set_fly_per_repo_image()` detects the
Dockerfile, computes the hash, and sets `LEERIE_FLY_IMAGE` to the per-repo
tag — the existing override hook in `resolve_fly_image_tag()` picks it up
transparently. `ensure_image()` then first guarantees the base image is
published (checking `published-tags.txt`; building and pushing if absent),
then calls `build-push.sh --dockerfile $USER_REPO/.leerie/Dockerfile
--build-arg BASE_IMAGE=$base_tag --tag $per_repo_tag` to build and push
the derived image. Both the base tag and the per-repo tag are recorded in
`published-tags.txt` so subsequent runs skip the build entirely. Without
`.leerie/Dockerfile` the Fly path is unchanged — the base tag resolves and
`ensure_image` proceeds as before.

### Auto-capture of repo dependencies

At the end of a normal (non-resume) finalize, leerie invokes the `dep_capture`
LLM worker. The worker is given a **manifests-first** corpus and **decides** what
the repo genuinely needs across all languages and frameworks:

- **Primary — dependency-manifest files.** The contents of the repo's dependency
  manifests present in `repo_root` (`requirements.txt`, `pyproject.toml`,
  `Pipfile`, `package.json` + lockfile, `go.mod`, `Cargo.toml`, `Gemfile`,
  `composer.json`, …), gathered by `_gather_dep_manifests` (bounded per file and
  in total). These are the unambiguous ground truth for a repo's language
  dependencies.
- **Secondary — install-filtered commands.** A hint list of package-manager
  *install* commands observed during the run (extracted from `logs/*.log` via
  `_iter_log_tool_use`, then narrowed by `_extract_depcap_commands` to commands
  that invoke an install verb at a command boundary, excluding text-scanning
  tools; deduped, newest-first, byte-bounded). Purpose: surface **system/native**
  (apt) deps a worker had to install that no language manifest records (e.g.
  `libvips-dev`, `pkg-config`).

This replaces an earlier design in which the worker read the *complete* set of
shell commands and reverse-engineered deps from command strings — that corpus
was overwhelmingly noise (greps, `git`, `pytest`, `python3 -c` one-liners) and
let the worker degenerate into echoing prose as package names. Reasoning over
manifest files (with commands as a hint) is what delivers the "across all
languages and frameworks" goal. Which files and commands the worker sees is
deterministic corpus selection in code; the model still decides content (§12
*Prompts are advisory, code enforces*). Structured output (`setup_packages`
and `language_installs`) is validated against a JSON schema and written to
`.leerie/config.toml` deterministically. The `dep_capture` worker defaults to
`sonnet`/`medium`, overridable via `LEERIE_MODEL_DEP_CAPTURE`.

**System packages → `setup_packages` → warm apt layer.** `dep_capture`'s
`setup_packages` output is union-merged into `setup_packages` in
`.leerie/config.toml` (never clobber: only new packages are appended;
user-edited values and comments preserved). The launcher auto-generation path
(*Per-repo container image* above) turns the updated `setup_packages` into a
derived apt-install Dockerfile next run. Workers that previously failed every
`apt-get install` attempt (unprivileged) find the package pre-installed; the
install-intent loop stops.

**Language deps → `language_installs` → richer Dockerfile bake (gated on
`bake_language_deps`, default true).** `dep_capture`'s `language_installs`
output (per-manager `{manager, command, copy_inputs}` entries) is written to
`.leerie/config.toml`, keyed by manager, never-clobber. When enabled, the
auto-generated `.leerie/Dockerfile` (and the derived image, when
`build_repo_image` builds it) also includes a language-dep layer: `COPY` for
the lockfile, manifest files, and any ancillary inputs the package manager
requires, followed by `RUN <command>` (`pnpm install --frozen-lockfile`,
`pip install -r requirements.txt`, etc.). Workers that inherit this image find
`node_modules`/site-packages already populated — per-worker install drops to
near-zero.

**Rebuild tradeoff.** A dependency-input change triggers a full image rebuild
(`build_repo_image` fires when the hash mismatches). To keep rebuilds narrow,
`.dockerfile-hash` folds in the sha256 of every input in the `COPY` list
(lockfiles, manifests, workspace `package.json`s, `patches/`, `.npmrc`); an
unrelated source-file change does not invalidate the layer. The cost —
minutes per rebuild — is paid once across all subsequent runs, a clear net
win against per-worker install time accumulated across hundreds of workers.

**Trigger seams.** All three funnel to one `dep_capture` worker — the trigger
differs, the decision-maker does not:

- **Clean finish → finalize.** `capture_repo_deps` is called (`await`) from
  `phase_finalize` after `finished_at` is written and run-branch verification
  completes. A `resume` of an already-finished run returns before finalize —
  capture does not re-fire; a `resume` that reaches finalize (partial resume)
  re-runs it, and union merge makes that a no-op when nothing new is found.
- **Cancel / SIGTERM → cancel arm in `main()`.** Catchable signals
  (`KeyboardInterrupt` / `InterruptedBySignal`) surface in `main()` after
  `asyncio.run(orchestrate)` unwinds, with a real Python window before the
  `finally` cleanup block. A best-effort `asyncio.run(capture_repo_deps(...))`
  runs there — same post-loop pattern as the `RateLimitedExit` arm. Non-fatal;
  covers `nerdctl stop` / Ctrl-C. `SIGKILL` gives no window.
- **SIGKILL / crash / host-side → backstop + `--recapture`.** Covered two
  ways, both host-side, modeled on the `--phase judge` scaffolding: a
  *run-start backstop* scans prior run dirs at start, before `phase_classify`,
  for any with `logs/` but no `dep_capture.done` sentinel and runs capture
  over them automatically; and *on-demand `--recapture`* — `leerie config
  --recapture` resolves the target run, constructs and flocks its `State`
  (refusing to race a live orchestrator via `StateLockedError`), and runs the
  worker via `asyncio.run`.

**Union by default; replace only on `--recapture --force`.** Every automatic
seam — finalize, cancel, backstop — writes as a never-clobber *union*, so
capture can only add packages/managers, never remove one the operator
narrowed by hand. The one exception is operator-driven `leerie config
--recapture --force`, which wholesale-*replaces* the persisted
`setup_packages` + `language_installs` from the fresh capture (dropping deps
no longer captured) — an explicit "rebuild the dep set from current history"
gesture. Even under `--force`, an empty capture leaves the existing config
untouched, so a bad run can never blank a good config.

**Idempotency.** After a successful write, `capture_repo_deps` writes a
lightweight `<run_dir>/dep_capture.done` sentinel and sets
`dep_capture_done = True` in `state.json`. The run-start backstop skips any
run whose sentinel is already present. When the union merge finds nothing
new, the function returns immediately without touching `.leerie/config.toml`.

**No auto-commit.** Capture writes `.leerie/config.toml` (and, if generated,
`.leerie/Dockerfile`) as uncommitted files. Leerie logs one line: *"captured N
package(s)/install command — run `git add .leerie/ && git commit` to bake
into the next run's image."* The user controls when to commit — this
preserves the committed-Dockerfile authority rule: a hand-authored
`.leerie/Dockerfile` is never surprised by an auto-commit.

**Non-fatal.** Any error during capture or write — log parsing failure, TOML
write error, filesystem permission issue — is caught, logged at debug level,
and swallowed. A run must never fail because dependency capture failed; the
run is marked complete regardless.

**Opt-out.** Set `capture_deps = false` in `.leerie/config.toml` or
`LEERIE_CAPTURE_DEPS=0` in the environment to disable capture entirely.
The `capture_deps` knob is resolved by `resolve_capture_deps()` with
`LEERIE_CAPTURE_DEPS` env > `.leerie/config.toml` > default `true`
precedence. There is no CLI flag and no `leerie.toml` tier.

**Committed Dockerfile is authoritative.** When `.leerie/Dockerfile` is
already committed to the repo, capture skips writing `setup_packages` — the
Dockerfile speaks for itself. This mirrors the existing rule: `setup_packages`
is ignored when a committed Dockerfile is present (see *Per-repo container
image* above).

**Fly parity.** Capture writes the same files regardless of runtime. On
`--runtime fly` the workflow splits across two directions:
- **Machine → host (stream-back).** After the run-state tar, `fetch-branch.sh`
  best-effort streams `/work/.leerie/config.toml` and `/work/.leerie/Dockerfile`
  from the Fly Machine back to `$USER_REPO/.leerie/` (or `$LEERIE_STATE_HOST_DIR`).
  Each file is existence-guarded on the remote side and never clobbers a
  host-edited file; failure is non-fatal and doesn't affect `fetch_branch`'s
  return code. This fires only on a clean finish (same condition gate that
  runs `fetch_branch` at all — rc `0|10|11|75`). Cancel/kill recovery uses the
  host-side `--recapture` / next-run backstop instead.
- **Host → machine (seed-repo whitelist).** Pre-existing committed `.leerie/`
  files (including a previously streamed-back and committed `config.toml` or
  `Dockerfile`) are included in `seed-repo.sh`'s dirty-delta filter so they
  reach the machine's `/work/.leerie/` next run. The Fly derived-image path
  then picks them up identically to the local nerdctl path.

### Browser-based test execution in the base image

The base image ships headless Chromium and a version-matched chromedriver
(see *Image build*, IMPLEMENTATION.md §0.5). This is scoped narrowly: it
exists so workers can **execute** browser-driven tests — Selenium, Capybara,
Playwright, Puppeteer — inside the container, the same way they run any
other test command. It is not a visual-verification capability; nothing
renders a screenshot back to a worker or the user. A Rails repo with a
Capybara feature-spec suite, or a Next.js repo with Playwright e2e tests,
needs a real browser to `bundle exec rspec` or `pnpm test:e2e` at all —
without one an entire test category is unreachable and reports as a false
pass (skipped) or a misleading failure (driver-not-found).

**Baked at build time, not resolved at run time.** The browser and its
driver install from Debian's own apt repos in the same transaction
(`chromium` + `chromium-driver` + the `libc6` bump that keeps `chromium`
from failing to load — see *Image build*), so the two are always
version-matched and neither downloads anything when a worker's suite runs.
This follows the same reasoning as runtime version resolution in §6½: keep
the model out of the loop for something deterministic. Selenium Manager (the
common auto-download-a-driver mechanism) would otherwise reach out to the
network on first use inside every fresh worktree — extra latency per
subtask, and a dependency on egress the container may not have. Baking the
browser turns a per-worker runtime concern into a one-time build-time
concern, consistent with how the image separates cross-cutting state
(pre-installed) from per-worktree state (installed by each worker)
elsewhere in this section.

**Sandbox flags baked in, not left to each repo.** Workers run as the
non-root `leerie` user, so Chrome's SUID sandbox cannot work here regardless
of which project's test suite invokes it. Rather than expect every repo's
test config to discover and set `--no-sandbox` /
`--disable-setuid-sandbox` / `--disable-dev-shm-usage` correctly, the flags
are written once into `/etc/chromium.d/leerie-container-flags` at image
build time (detail: IMPLEMENTATION.md §"Browser-based testing"). A project
that already sets these flags is unaffected (idempotent); one that doesn't
now still works, because the wrapper applies them globally — the same
image-layer posture as elsewhere in this doc: fix a class of failure once
instead of asking every worker, in every worktree, on every run, to route
around it.

### `leerie config` — host-side onramp

Not every repo author wants to hand-write `.leerie/config.toml` or
`.leerie/Dockerfile`. The `leerie config` verb is a host-side fast-path
that generates and inspects these files without starting a container.

**Why no container.** Config generation only needs to read the repo's
existing files (lockfiles, CI yaml, `package.json`, `Gemfile`, etc.) and
write into `.leerie/` — a read-plus-local-write operation needing no worker
isolation, network, or package-manager caches. A container would add
thirty-plus seconds of startup for no benefit, and would invert the UX: the
user is configuring leerie before running it, not provisioning a machine
first.

**Why it is not in the four-verb remote-lifecycle table (§6 "verb
surface").** That table (`leerie "task" --runtime fly`, `stop`, `resume`,
`kill`) is scoped to the remote *run* lifecycle — allocation, pausing,
resuming, destruction. `leerie config` has none of that; it never
allocates a machine or a container. It is a host-side utility verb in the
same family as `leerie list`: fast, local, and orthogonal to run
management.

**Three modes:**

- **`leerie config`** (bare): Reads the effective configuration — merging
  `.leerie/config.toml` (if present) with BLT inference — and prints a
  summary of each key, its value, and whether it came from the file or from
  inference. Useful for auditing what leerie will actually use on the next
  run without starting one.

- **`leerie config --init`**: Auto-detects BLT commands (the same table
  used by `_infer_build_lint_test()`) and writes a `.leerie/config.toml`
  with the detected values as uncommented entries, plus commented-out
  examples for `setup_packages`. No model involved — this is pure
  deterministic detection. The user can then edit the generated file, `git
  add .leerie/`, and commit. Subsequent runs pick up the declared values via
  `resolve_blt()`.

- **`leerie config --chat`**: Launches an interactive `claude` session (NOT
  `claude -p` — interactive, not headless) with a config-generation system
  prompt. The session can read the full repo, ask the user questions, and
  write `.leerie/config.toml` and optionally `.leerie/Dockerfile` when the
  repo needs system packages. This mode handles the cases `--init` misses:
  polyglot Makefile-driven setups, repos with non-standard toolchains, or
  users who want to explain their setup rather than edit a TOML file.

---

## 7. The worker contract

Every worker is a separate process with its own context. The orchestrator and a
worker communicate through a strict contract:

- The orchestrator passes the worker its role, its inputs, and the exact shape
  of the structured result it must return.
- The worker's final output is **validated against that schema** before the
  orchestrator acts on it. A worker cannot, by malformed output, cause the
  orchestrator to do something undefined.
- A worker that fails to produce a schema-valid result is retried once with the
  violation pointed out. A second failure is a hard worker error.

**Validation is post-hoc, not constrained — and that is the dominant source of
worker failure.** `claude -p --json-schema` does not constrain generation. The
CLI injects the schema as a synthetic `StructuredOutput` *tool* and checks the
result afterwards, re-prompting on mismatch; its own reference calls the flag
"structured output **validation**". Captured from the wire (2026-08-04): the
outbound request carries `tools[n].name == "StructuredOutput"` with **`strict`
absent** and no `output_config`.

The consequence is measured: across the run corpus, **28.8% of `StructuredOutput`
submissions are malformed**, in the shapes the vendor documents as the cost of
omitting strict mode — payload wrapped in a container key, the decoder
flipping to legacy XML mid-value, unparseable bytes, required fields simply
absent. None of this is a model unable to do the work — the answers are
usually correct and merely unreachable, which is why the CLI's own re-prompt
loop recovers most of them at the cost of regenerating the payload.

**Optional counter-measure: grammar-constrained decoding.** The API can compile
a schema into a grammar that restricts token sampling, making every one of those
shapes *unrepresentable* rather than merely rarer. The CLI exposes no way to ask
for it. leerie can, behind
`--dangerously-force-strict-output` (§ *Forcing constrained decoding*), by
routing worker traffic through a loopback proxy that sets `strict: true` on the
injected tool. It is **off by default**: it rewrites outbound requests to reach
a capability the CLI does not expose and that is undocumented for subscription
auth, and it depends on an internal tool name that carries no compatibility
guarantee.

Because that mechanism works by owning `ANTHROPIC_BASE_URL`, it **cannot
coexist with an operator-supplied one**. A user who has already pointed that
variable at a gateway, proxy, or alternative endpoint has a configuration leerie
must not silently take over — and silently declining to enable the flag would be
equally wrong, since the run would then lack the guarantee it was asked for.
leerie therefore refuses to start when both are present, naming both and leaving
the choice to the operator.

The same reasoning rules out **Bedrock**. `ANTHROPIC_BASE_URL` is the
first-party endpoint override; Bedrock routes through its own, and the proxy's
upstream is the first-party API. So under Bedrock the flag is either inert — the
proxy is never contacted and the operator is silently handed the post-hoc
validation they explicitly asked to replace — or it misroutes every worker call.
Since a run cannot tell those apart from a healthy one, leerie refuses that
combination too rather than guessing.

**The guarantee is scoped to a run's workers, and one LLM call sits outside
it by construction.** The proxy is owned by the orchestrator process and torn
down when that process exits. The recovery integrator in
`scripts/remote/collect-subtrees.sh` invokes `claude -p` directly, and it runs
only *after* the orchestrator is confirmed dead — the `finalize --force` path
calls it following a force-stop, and the non-force path only as recovery once
`force_finalize_remote` has succeeded, which by design refuses while the run is
alive. There is therefore no proxy left to route it through, and the later
`finalize` invocation need not even carry the flag. Erasing this boundary would
mean standing up a second proxy on the machine purely for a post-mortem salvage
step, which buys a generation-time guarantee on an operation that is not part
of the run.

So it is stated rather than closed. That integrator still validates its output
against its own copy of `SCHEMAS["integrator"]` — output is checked, just not
constrained during generation. The distinction matters because the failure this
section otherwise refuses to permit is silence: an operator who asked for
constrained decoding should know exactly which calls got it.

**Owning that variable also costs the model's native context window, and
leerie has to buy it back.** The Claude Code CLI treats any custom
`ANTHROPIC_BASE_URL` as an LLM gateway, and behind a gateway it can no longer
confirm which model actually answers, so it falls back to a conservative
client-side context ceiling instead of the model's real window. Sonnet 5
natively carries 1M on the first-party API; behind the proxy the CLI refuses
prompts at roughly 224K, client-side and silently — no API call, no server
error (see §6 *A client-side context refusal*) — so nothing in the run
explains why an otherwise-fine prompt was rejected.

The remedy is the documented gateway-side selector: leerie appends `[1m]` to
the model alias whenever the proxy is active (`_model_arg`), a no-op on the
direct path where the native window already applies. It's scoped to aliases
that *have* a 1M variant — `haiku` has none and rejects the suffix — and
applied automatically rather than exposed as a flag, since an operator gains
nothing setting by hand a value that's inert whenever the proxy is off.

Measured across five arms on one 225 KB worker payload, varying only
`ANTHROPIC_BASE_URL`: direct sustained 235,805 tokens with no refusal; both a
passthrough proxy and the real strict proxy refused around ~224K (120 tokens
apart, establishing the base-URL override rather than schema rewriting as the
cause). Adding `[1m]` cleared both paths, with the proxy's
rewritten/passed-through/fell-back counters identical under either alias —
the window is bought back without giving up the constrained decoding the
flag exists for.

What happens after a hard worker error depends on whether partial progress can
be salvaged. An **implementer** has a worktree branch and possibly a
checkpoint, so its failure converts into a handoff: a fresh implementer can
continue. The **classifier, planner, reconciler, plan_overlap_judge, and
provision** have no partial-progress artifact to hand off, so their hard
failure aborts the run with state saved for `resume`. The **conformer** has
commits but its phase is advisory, so a hard failure surfaces as a warning,
not an abort. The rule is general: salvage if there is something to salvage;
abort cleanly otherwise. When `planner_samples > 1`, a crashed sample is
dropped and the surviving samples for that domain proceed to selection; the
abort fires only when all samples for a domain fail.

The **integrator** is the case where that rule and the code disagreed. Its
partial progress is the *resolved staging worktree* — files whose conflict
markers are gone and whose hunks carry real merge judgment — an artifact in
exactly the sense the implementer's branch is, and the most expensive one in
the run to recreate, since reproducing it means re-deriving every side's
intent from the subtask specs. The work need not be committed to be real: a
crashed integrator typically dies *mid-resolution*, with the resolution in
the working tree and no merge commit (run `879defae`'s wave-2 integrator did
exactly this). Preservation therefore cannot be conditioned on
`check_merge_committed` — that predicate is false in precisely the case
worth salvaging.

This distinction is between a *crash* and a *verdict*, and only the first is
new. A crash is infrastructure — PID exhaustion, OOM, a killed session — and
says nothing about whether the resolution was any good; the run rescues the
work and pauses for `resume`. A `design-conflict` or `failed` **verdict** is
the integrator's considered judgment that the merge should not stand, and
still aborts and discards, exactly as *When integration cannot succeed*
describes: a verdict is a fact about the work, a crash is a fact about the
machine.

### Forcing constrained decoding

`--dangerously-force-strict-output` converts the schema contract above from
checked-afterwards into enforced-during-generation. Off by default.

The mechanism is a **loopback proxy, one per run, started by the orchestrator**.
Because the orchestrator is PID 1 inside the container and every worker is its
child (§6 *Worker subtree termination*), they share a network namespace: a
listener on `127.0.0.1` needs no port mapping or host networking, and the
container boundary reaps it on an abnormal exit. The port is chosen by binding
to `0` and reading back what the OS assigned, so concurrent runs never collide.

The guarantee is per-*call*, not per-orchestrator-process. Three entrypoints
invoke workers without reaching `_orchestrate()`, so each opens its own proxy:
`run_rebaser` and `run_recapture_deps` (host-seam entrypoints, §6
*Finalization*, §6½, each a short-lived `python3` process reading the flag
`_orchestrate()` already resolved and persisted onto run state, since it
never crosses that process boundary), and `--phase heal` (returns before
`_orchestrate()` is reached). Before this, all three silently ran
unconstrained regardless of the flag. Because they're best-effort paths that
must never block a push, abort a multi-run loop, or fail a heal, their
proxies fail *soft* rather than following the fail-closed startup rule below:
a listener that cannot bind costs the guarantee for that call, not the call
itself.

The proxy rewrites exactly one thing: on a request carrying a single tool
named `StructuredOutput`, it sets `strict: true` and normalises the schema to
the subset grammar compilation accepts — `additionalProperties: false` on
every object, and removal of keywords strict cannot express (only
`minLength`/`maxLength`/`minimum`/`maximum` occur in leerie's own schemas
today; the rest are stripped defensively). Everything else is forwarded
untouched.

Two properties make that safe to run in the path of every worker call.

**It fails open.** If the tool is renamed, duplicated, or shaped unexpectedly
by an upstream release, the request is forwarded unmodified — the guarantee
is lost, not the run, and that loss is reported. A request carrying no such
tool is *not* a loss and is not reported: the CLI injects the tool only on
turns that ask for structured output, so a multi-turn worker routinely makes
some requests without it (measured, roughly a quarter to a third). The
rename case is indistinguishable per-request from that ordinary traffic, but
not across a run — every worker is invoked with a schema, so a run that
rewrote *nothing* is reported once, at the end, as a probable rename.

**It fails closed at startup.** If the listener cannot bind, the run dies
rather than proceeding unconstrained, so an operator who asked for the
guarantee is never quietly given the old behaviour.

**Two distinct limits, both undocumented.** The API refuses an over-large
schema two ways — *"Schema is too complex for compilation"* and *"The
compiled grammar is too large"* — with no numeric bound documented anywhere.
Measured drivers: **optional properties** (strict mode must admit every
subset of them in any order — 2^k paths per node, multiplied per array
element) and **free-form strings** (the expensive element per path — 20
string properties are refused where 20 enums/booleans/integers/arrays
compile). Nesting array-of-objects inside array-of-objects compounds both.

leerie answers each at the layer that owns it: the proxy forces every
optional `required` on the wire only, collapsing the subset explosion
without touching the schema the CLI validates against; the two schemas that
still didn't fit were restructured — the planner's by that transform alone,
the reconciler's by lifting its nested `requires` array into a sibling
keyed by id and collapsing four isomorphic `{sid, tag, reason}` arrays into
one enum-discriminated `tag_ops`. Seven lesser in-place reductions ($defs
dedup, stripping descriptions, dropping subtrees, trimming properties,
identifiers-to-enums) were tried against the live API first and all
refused; only the restructure worked.

**A schema that still cannot be constrained is survivable.** Measured
against the API across all 23 schemas (2026-08-04), two are refused
outright — the planner's and the reconciler's, both driven by optional
properties inside array items (twelve each), not size (the conformer's
larger schema compiles fine). The fix is not to make those fields required
— that already failed once, for a different reason: requiring fields is
what made workers fail to produce schema-valid output at all (see *Findings
carry a severity*, and the overlap judge's `artifact_paths`). So the proxy
fails open on the *response* too: a rejection of the hardened request is
answered by re-sending the untouched one, and that worker falls back to
ordinary post-hoc validation while every other worker still gets
constrained decoding. Logged at every verbosity and counted in the
end-of-run summary, since a silently lost guarantee is the one outcome
this design refuses.

The normalisation has a real cost: stripped keywords were carrying
validation. Sixteen of twenty-one are string-length bounds on strings whose
consumers already test truthiness, so nothing changes. The remaining five
are numeric bounds that fail *permissively* if dropped (e.g. `fit_judge`'s
score compared against a threshold with no range check would read an
out-of-range value as well-fit), so leerie re-checks those in Python —
unconditionally, not gated on the flag, since a value outside its declared
range was always a worker bug regardless of what removed the schema-level
bound.

---

## 8. The evidence-gated loop

The original design asked each worker to self-report a 1–10 confidence score
and loop until it reached 9. The intent — force the worker to be sure before
it acts — is right; the mechanism is not, since a self-reported number is not
a measurement and models are systematically overconfident on a wrong root
cause. Looping on that number just loops on the same vibe.

Leerie keeps the loop and the high-confidence bar but **anchors the score to
evidence**. Before an implementer writes any code it must clear domain-specific
*evidence gates*, each carrying a concrete artifact — a file-and-line
citation, a reproduction, a measurement, a cited research source — not an
assertion. The confidence score is a *summary of which gates carry hard
evidence*, not a feeling. A bug-fixing task, for instance, must show a
deterministic reproduction, a test that fails because of this specific bug, a
traced symptom-to-cause path, and a mechanistic explanation of the fix. Other
domains have their own gate sets.

Three further disciplines apply at every scoring step, and are what make the
score load-bearing rather than ornamental:

- **Falsification.** For each major claim, the worker explicitly looks for
  evidence that would *disprove* it — a probe, a counter-example, a
  contradicting source — and earns high confidence only when the falsifier
  was tested and failed. Looking only for confirming evidence is how a wrong
  hypothesis acquires high confidence.
- **Drift reconciliation.** Before scoring, the worker re-reads its own prior
  statements in the session; any contradiction with an earlier claim, or
  quiet retreat from one, is named and resolved with evidence. An
  unreconciled contradiction blocks the high-confidence bar.
- **Gap surfacing.** Below the bar, the worker must name the specific
  *artifact* that would raise the score — not an activity ("look into it
  more"). This converts an open-ended "try harder" loop into a directed
  search with a deterministic next move.

The loop is bounded. If the gates cannot be cleared within it, the subtask
reports itself `blocked`, stating precisely what evidence is missing and
whether obtaining it needs something only the user can supply — the narrow,
legitimate exception to "never ask the user" (§11).

**The disciplines are asked for; they are not schema-required.** Falsification
and drift reconciliation keep an optional property each
(`falsifiers_tested`/`contradictions_reconciled`); gap surfacing has none — the
gap lives in the required `basis` field. None of the three is `required` in
the schema, a deliberate reversal forced by measurement: requiring them made
the confidence block a five-required-field object with two arrays, a
paragraph string, and a nested object — field for field the trigger profile in
upstream [anthropics/claude-code#49747](https://github.com/anthropics/claude-code/issues/49747),
where the decoder flips from JSON to legacy XML mid-argument on tool calls
with many required parameters and verbose string/array mixes. A controlled A/B
(real `fit_judge` schema, n=8 per arm) measured 8/8 first attempts corrupted
with the block present, 0/8 without; corpus-wide, 48.9% of all worker calls
were wasted retries — requiring the field destroyed the entire payload
including the score itself. leerie keeps the numeric score axes and `basis`
required, and asks for falsifiers/contradictions/gaps in the prompt, where a
missing one costs a judgment rather than the whole answer.

`gap_to_close` (the block's only nested object, the sharpest edge of the
#49747 profile) is removed outright — its sole consumer, a diagnostic log
line, now reads `confidence.basis` instead.

This is not a retreat from "code enforces" (§12): a schema is not the
enforcement layer for a discipline whose absence Python can check directly on
the returned object, at a moment when the object still exists.

### The planner gate

The same discipline applies one layer up. A planner self-gates on two axes —
*task understanding* and *decomposition quality* — using the same three
disciplines (falsification, drift reconciliation, gap surfacing). A planner
whose gate cannot clear emits `status: "blocked"` with the gap analysis
instead of subtasks, matching the implementer's blocked-with-evidence exit: a
worker that cannot justify its confidence in evidence hands the decision back
to a layer that can.

**The cleared-but-empty terminal state.** Symmetric with the blocked exit: a
planner whose gate *does* clear can legitimately return zero subtasks — "I
investigated this domain, the work is already satisfied on HEAD," distinct
from blocked. When *every* planner returns `status: "ready"` with an empty
`subtasks` array, there is nothing to schedule: the orchestrator records
`no_work_required=true` in state.json with each domain's `confidence.basis`
quoted, writes `finished_at`, skips phases 3–6, and exits 0. The run renders
as `done` in `leerie list` (no push, no PR). A mixed outcome (some
ready+empty, some ready+nonempty) proceeds normally, the empty domains simply
contributing nothing. The all-blocked case still dies — a blocker is a gate
failure the user must see.

**Reaching the cleared-but-empty state from classification, before any
planner runs.** An already-satisfied task can make the classification gate
itself unable to converge on a category set: `check_classifier_output` and
the independent `classification_judge` (phase 1's adversarial-verification
pair) can pull in opposite directions round after round when the
classifier's investigation keeps surfacing that the deliverable is already
present, since a category set is being fitted to a diff that doesn't exist.
The classifier schema carries an optional, additive
`likely_already_satisfied`/`likely_already_satisfied_evidence` pair for this
— structuring a claim it could previously only make as discarded prose. If
the classification-gate retry loop exhausts without converging and
`st.data["likely_already_satisfied"]` is `True` with evidence,
`phase_classification_gate` routes directly to `_finish_no_work_run`, the
same terminal state reached one phase earlier. The field is OR-accumulated
across re-classify rounds within one gate call, not last-write-wins: a fresh
`True`+evidence claim always wins, and a falsy/absent claim only clears a
prior `True` if there was none. (A production incident hit the un-accumulated
version: one round found the deliverable already on HEAD, a later
category-focused re-classify silently dropped the claim, and the gate died
instead of routing to no-work.) This extends the trust boundary
`_detect_no_work` already accepts — an investigation un-double-checked by a
second judge — to "classification could not otherwise converge anyway." A
classifier that never sets the field sees zero behavior change.

Reaching this state from classification instead of post-plan meant a run
could hit `_finish_no_work_run` earlier than `run.json`'s own run-identity
fields (`run_id`, `branch`, `working_branch`, `pr_base_branch`, `started_at`,
`task`) used to be written — and `_finish_no_work_run` writes only
`{finished_at, no_push, no_verify}`, producing a `run.json` the launcher's
auto-finalize scan can't distinguish from a crash before `phase_classify`
completed. The identity write is therefore hoisted to run start, before
`phase_classify`, so every early-exit path sees a correctly-identified
`run.json`.

**The CRITIC retry pattern's oscillation guard.** `_run_checked_loop` — the
shared mechanical-feedback retry primitive behind the classifier,
classification-gate, reconciler, provision, overlap-judge, and integrator
checks — does not accumulate feedback across rounds, so a round-0 fix for
issue A can introduce issue B, whose round-1 fix reintroduces A: the loop
cycles between two "fixes" that individually resolve the flagged issue
without ever converging, burning the round budget before the caller's own
exhaustion `die()` fires. The loop now tracks each round's issue set (keyed
by a stable `LABEL: subject` signature, not the free-form evidence prose,
which regenerates each round and would defeat exact-match comparison) and
breaks early the moment a round's signature set **exactly equals** one
already seen.

The comparison is exact equality, not subset containment. An earlier version
used `issue_set <= seen`, on the theory that a shrinking signature set is
never a subset of a prior one — false: a round that fixes some issues and
leaves others open produces a set that is both shrinking and a proper subset
of the prior round's, the ordinary shape of incremental convergence. That
version aborted mid-convergence on real progress (root-caused against a
2026-07-31 incident where a classification-gate run needing three categories
simultaneously never held all three and died at exhaustion despite genuine
forward progress each round). Exact-equality still catches the true A→B→A
cycle while letting a proper subset keep retrying, bounded as always by
`max_rounds`.

The same incident surfaced a compounding defect: `check_classifier_output`'s
`SAME_WORK_RISK`/`TEST_OWNERSHIP_RISK` advisories fire unconditionally inside
`phase_classify`'s own inner retry loop and could strip a category the
independent `classification_judge` had just confirmed required, since
`phase_classification_gate`'s re-classify re-invokes `phase_classify` from
scratch with no memory of what the judge already vetted.
`check_classifier_output` now accepts an accumulated `judge_confirmed` set
(categories reviewed without a *concrete, evidenced* objection, or
explicitly requested, across the gate call) and suppresses a
same-work/test-ownership pair only when both categories are judge-confirmed
— the classifier's self-check yields to the independent judge instead of
re-litigating every round.

**Already-satisfied subtask elimination (the per-subtask sibling).** The
cleared-but-empty state above is *whole-run*. A planner does not know what a
*sibling run* merged to the base branch an hour ago, so it can, in good
faith, emit a subtask whose success criteria are already met on the seeded
base. Left alone, that subtask reaches an implementer, which correctly
reports `complete` while committing nothing — and the mechanical no-commits
backstop (`check_branch_has_commits`, §5 *Artifact passing between
subtasks*) fails it as a retryable no-op, burning the retry budget. The
honest answer ("already done upstream") is exactly what the backstop can't
represent.

The fix is the per-subtask analogue: before scheduling, a read-only
**satisfied-probe** evaluates each subtask's `success_criteria_seed` against
the base tree and soft-drops the ones already met (same shape as
dead-subtask elimination, §5, recorded in `dropped_subtasks`). If all
subtasks drop, the run routes to `no_work_required`. This is a soft,
advisory prune subordinate to the no-commits backstop, which remains the
last line of defense (per §12, the code check is the guarantee; the LLM
probe only reduces how often it fires). Two disciplines, established by
calibration: (1) the probe judges the **base tree only**, never
`git log --all` or another ref — a worktree shares the object DB, so a
history-spanning probe "finds" the deliverable on an unrelated branch; (2)
the probe defaults to *not satisfied* on any uncertainty, since a false
"already done" silently deletes real work, strictly worse than a false
"still needed."

**The mid-run sibling case.** The pre-schedule probe judges the base tree as
it stood at run start, so it is *structurally blind* to a subtask that
becomes satisfied **during this run** by an earlier-wave sibling. Concretely:
a code subtask commits a test file's update as a byproduct, while a
separate, later-wave test-only subtask `requires` that same capability. By
the time it runs, its work is already on the run branch — it reports
`complete` with nothing to commit, the no-commits backstop fails it, and the
retry reproduces the identical no-op deterministically until the retry cap
exhausts and the wave dies.

The resolution is the post-execution analogue: on a no-commits result,
before failing, the orchestrator re-runs the satisfied-probe against
`success_criteria_seed` on the **run-branch HEAD** (which *does* contain the
sibling's commit). If met there, the subtask settles as satisfied (recorded
as `already_satisfied_mid_run`) rather than routed to the retry cap; a
genuinely unsatisfied probe leaves the existing retryable-failure path
unchanged. The base-tree-only-vs-HEAD distinction is deliberate: the
pre-schedule probe must not span history, while the post-execution probe
measures against exactly the ref the commit-presence gate uses
(`_compute_run_branch`).

**Scope: sibling-committed *or* base-tree-already-satisfied.** The probe
judges *whether* criteria are met on HEAD, not *who* met them, so this
rescue also covers a subtask satisfied on the seeded base whose pre-schedule
probe was skipped or false-negatived. That's intended: such a subtask is
legitimately complete regardless of provenance.

**Probing a flagged subtask before it spends.** The rescue above is correct
but wasteful in ordering: `_warn_provider_subset_subtasks` (§5
*Provider-subset subtasks*) already flags, at plan time, every subtask whose
entire `files_likely_touched` surface belongs to an ordered predecessor —
measured always right and inert (a run's three flagged subtasks all ran a
full implementer and committed nothing; twelve corpus-wide reached this
rescue only after their whole spend). So the flagged sids are persisted
(`provider_subset_sids`) and `_settle_subtask` runs the same HEAD probe
against staging *before* spawning the implementer — staging sits at
run-branch HEAD, and a provider-subset predecessor is by construction
already merged there. A hit settles on one read-only probe instead of a
full implementer (one run's three cost ≈18 worker-minutes/≈$2.6 combined). A
miss costs one probe. This does **not** become a drop — the plan-time signal
stays advisory (a subtask may make a genuinely distinct edit to a shared
file) and the decision stays the probe's, judged against a real tree; only
*when* it's asked changes.

**A settle without an implementer still owes the wave a branch.**
`integrate_wave` filters only on `status == "complete"`, never on whether
`leerie/subtasks/<run-id>/<sid>` exists. The post-execution rescue is safe
because its implementer ran and created the branch (zero commits, a true
`git merge --no-ff` no-op). The pre-spawn probe returns *before*
`_run_implementer`, so no branch exists and `integrate.sh` exits 2 on every
probe hit. So the pre-spawn path creates the branch itself, at the
run-branch tip, before settling — the general invariant: **any path that
marks a subtask complete must leave behind the branch integration will look
for.** If the branch cannot be created, the subtask falls through to its
implementer.

**The sibling-invalidation case.** A third, opposite hazard: a subtask whose
criteria are met on the base tree *now*, but which a **surviving sibling in
the same plan will invalidate** — e.g. a coverage-floor test passes on the
seeded base and gets soft-dropped, but a surviving feature subtask later
adds the keys the test guards, turning it red, with no survivor owning that
file. The base-tree-only discipline is exactly what makes the probe blind to
this. The resolution keeps the base-tree rule but hands the probe the
**surviving siblings' declared surface** (`provides` and
`files_likely_touched`) as context, and asks it whether any surviving
sibling's work would invalidate the criteria it just judged met — declining
the drop (defaulting to *not satisfied*) if so. A mechanical
file-overlap rule was considered and rejected: the motivating case has
disjoint file sets (`nav-parity.test.ts` vs `messages/*.json`) whose
dependency lives only in intent, which a file-overlap predicate would miss
entirely — the judgment is left to the worker as the sole mechanism.

**Why this is §12-compliant.** `check_branch_has_commits` fires first and
unchanged; the probe can only *rescue*, never turn a committed subtask into
a failure, and fails safe to *not satisfied* on any crash or uncertainty.
"Are these success criteria semantically met on this tree" is exactly the
judgment §12's complementary half assigns to a worker — no new carve-out.

The structural contract of the §8 disciplines is mechanically enforced: the
worker's output schema requires the falsification, reconciliation, and gap
fields present, so a worker that skipped them fails its own JSON gate before
the orchestrator reads it. Their *quality* is model-judged; their *presence*
is not.

**Self-graded confidence is advisory; an independent verifier gates.** A
self-report has a structural ceiling no adversarial-falsification instruction
can raise — you cannot disprove a failure mode you cannot conceive, and the
mind that produced an incomplete solution bounds the failure modes it can
imagine. A real subtask scored itself `solution 9.5` with every falsifier it
"tested" about its own test plumbing, and shipped three latent behavioral
defects a later re-run had to fix — despite the prompt already commanding
maximal adversarial falsification. The fix is to change *who* runs the check,
generalizing the `fit_judge` precedent below: an independent judge finds cuts
the self-grader was blind to *because it did not produce the artifact*. So the
load-bearing gate for each self-graded axis becomes an **independent
adversarial verifier**, and the self-score is demoted to advisory.

Tests passing, lint clean, build green, and per-criterion satisfaction
remain **best-effort signals**, surfaced as warnings, never gating — any
code-enforced "tests must pass" gate invites a stuck model to weaken the
test instead (§9). Independence dissolves the gaming incentive without
reintroducing that bar.

#### Independent adversarial verification

The planner demonstrated the pattern first: `decomposition_quality`
self-score is retained as advisory, while the independent `fit_judge` is the
authoritative decomposition-quality gate (§5½), and `plan_overlap_judge` /
`adherence_judge` gate other planner dimensions the same way — `fit_judge`
works because it is a separate worker that did not produce the
decomposition, so it sees cuts the planner was blind to.

The same discipline now covers every ungated self-grader. Each remaining
self-graded axis gets an independent verifier that (a) did not produce the
artifact, (b) is handed only the artifact plus the task, and (c) is told to
**attack** it — enumerate concrete unhandled inputs, paths, cases, or
mis-wirings:

- **implementer `solution`** → verified by the **conformer's `solution_defects`
  axis**. The conformer already runs independently after the implementer's
  success path and reviews its committed diff (it did not write that diff —
  its own conformance edits are a separate, later layer), attacking it for
  behavioral gaps the self-grade missed. Gates in `_settle_subtask`; the
  conformer's existing build/lint/test and drift/docs/rule work stays
  advisory (below).
- **classifier** → an independent verifier of the category set against the
  task + codebase. A miscategorization here is not cosmetic — the same task
  classified one way produced ten subtasks, another way zero, and a "landing
  page feature" classified as documentation shipped only markdown. Gates on
  a found miscategorization.
- **reconciler / plan wiring** → the deterministic `check_plan_wiring` plus the
  independent `wiring_judge` described in §5 *A wiring re-check on the
  fully-merged plan*.
- **provision** → an independent verifier of the detected recipe against the
  actual image/runtime — e.g. does a `pip install` recipe carry
  `--break-system-packages` on the externally-managed Debian image, does the
  package manager match the lockfiles present. A recipe self-graded 9.3
  that omitted `--break-system-packages` caused twelve real install
  failures. Gates on a recipe that would fail.
- **planner `task_understanding`** → an independent `task_coverage_judge`,
  handed only the task plus the reconciled subtask set: does the union of
  subtasks actually *cover the task* — any required work missing entirely,
  any subtask off-task? Distinct from `fit_judge` (is each subtask
  sized/scoped correctly) and `wiring_judge` (are subtasks correctly wired to
  each other). A planner can self-report high `task_understanding` while a
  whole required piece of work never became a subtask, since a decomposition
  self-review is anchored to the decomposition already committed to, not the
  task independently re-read. Gates on a non-empty array of concretely-named
  coverage gaps, and **re-drives** `phase_plan` (mirroring
  `classification_judge`) since the planner can mechanically act on a found
  gap. A re-plan runs one planner per category with no cross-category
  visibility, so it can reintroduce cross-domain tag drift `phase_reconcile`
  already resolved — the re-drive is followed by a second `phase_reconcile`
  call before the next judge round (§5 *Bridge cross-domain capability-tag
  mismatches*). This doesn't reopen the earlier falsified attempt at gating
  `task_understanding` directly (§12 *Instruction adherence is
  code-enforced*): that finding was specific to an *understanding*-framed
  judge, which can't distinguish "understood and disobeyed" from "understood
  and obeyed." `task_coverage_judge` scores neither — only whether the
  subtask union is complete against the task, since a plan can honor every
  prescribed instruction and still omit unprescribed required work.

  `task_coverage_judge` originally had no deterministic PRIMARY check, only
  this SECONDARY judge. A floor, `check_required_items_coverage`, was tried
  and **deleted on 2026-08-04**: measured across every run that ever carried
  `required_items`, it passed 0 of 102 items — a 100% false-positive rate —
  and violated the *Language-to-JSON* rule (token-matching LLM-written
  sentences against subtask titles).

  The judge itself is retained but **advisory**: re-invoked on identical
  input it returned a different finding set 85% of the time (n=20), with an
  empty intersection across repeated samples — not a stable property of the
  input, so it cannot justify discarding a plan. The principle: **a judge's
  terminal authority must be proportional to its measured reproducibility,
  and a judgment layer with no mechanical backstop should not be terminal at
  all.** `wiring_judge` keeps its authority (99% of findings verify true,
  69/70) and `plan_overlap_judge` keeps its (impossible-assertion catches);
  coverage has neither, since no mechanical check can verify "the plan
  misses work X" without reading prose.

  `phase_plan` injects `required_items` verbatim into every planner's
  context (only when non-empty), mirroring the instruction-adherence gate's
  `prescribed_procedure` injection, so the planner can echo a required
  item's wording into a subtask at birth instead of the omission surfacing
  only after the fact.
- **integrator `resolution`** → an independent `integration_judge` that did
  not perform the merge, handed the merged result plus both parent diffs and
  the conflicting subtasks' intents: did the merge actually resolve the
  conflict *behaviorally*, not just remove `<<<<<<<` markers? The existing
  conflict-marker scan plus `check_merge_committed` catch the mechanical
  failure; they cannot see a syntactically clean merge that silently drops
  one side's behavior — e.g. a merge that keeps side A's function signature
  but side B's call sites, compiling but breaking at runtime. Gates on a
  non-empty array of
  concretely-named behavioral defects in the merged result. An integrator
  cannot always mechanically fix a semantic finding it didn't itself reason
  through the same way a planner can add a subtask, so this gate is
  **detect-and-die, single pass**, mirroring `wiring_judge` /
  `provision_judge`.

  **Location is not coverage.** A merge that drops a *duplicate* is textually
  indistinguishable from one that drops the only copy — both remove content
  present in a parent — so reasoning from the parent diffs alone can't tell
  them apart. A `dropped_change` is therefore only behavioral if the behavior
  is absent from the **merged tree**, not merely from the side the merge
  chose. The judge holds inspection tools, so it's asked to search the merged
  tree for equivalent coverage and cite it concretely (file + assertion). A
  defect carrying a specific citation is advisory; one without still gates —
  judge by *coverage*, not *location*, the same correction applied to the
  on-HEAD satisfied-probe.

  The citation is **asked for and never required**: requiring a field on a
  judge's schema has repeatedly produced a worker that emits no schema-valid
  output at all, and a gate that never runs catches nothing (§8 *Findings
  carry a severity* — the default is gating). Absence therefore gates.
- **`plan_overlap_judge`'s own `judgment` self-score is dropped, with no new
  independent verifier.** This worker is already the independent adversarial
  check for cross-planner surface collisions — layering a second judge on top
  of a judge would be self-scoring one level removed. Its existing
  deterministic validators — `PHANTOM_ARTIFACT`, `NO_FILE_OVERLAP`,
  `DROP_BREAKS_GRAPH`, `DUPLICATE_PAIR` (`check_overlap_judge_output`, §5
  *Cross-domain surface overlap*), and `_validate_overlap_judge_output`'s
  `merge_feasibility`-presence backstop — already catch the concrete,
  checkable failure modes. What remains ungated by dropping the self-score is
  purely a *quality-of-judgment* concern with no mechanically-attackable
  "artifact" for a second worker to find defects in — the same reason the
  implementer's `solution` axis is verified by a *different-role* worker (the
  conformer) rather than a second implementer grading the first. The
  deterministic validators become this worker's sole gate; `confidence` stays
  an advisory record only.

**Why this does not reintroduce the gameable bar §9 removed.** The old
criteria-lock / "tests must pass" gate was gameable because the *same* worker
controlled the bar. Independence dissolves that incentive: a verifier that
(a) did not write the artifact and (b) gates on "here is a concrete input
this artifact mishandles" — never "a test passed" or "the criteria say met"
— presents no bar for the graded worker to lower, and can't be defeated by
weakening a test, because it constructs *new* adversarial inputs the graded
worker never anticipated. A verdict is a list of concrete found defects, not
a score crossing a threshold — the invariant every new verifier must
preserve.

### Mechanical-feedback loops (the CRITIC pattern)

Research shows that LLMs cannot self-correct reasoning without external
feedback (Huang et al., ICLR 2024), and that self-correction WITH
tool-verified feedback works (the CRITIC framework, ICLR 2024).

Leerie applies this: every worker (except the PR writer) runs inside a
code-enforced loop (`_run_checked_loop`) where the orchestrator computes
**deterministic structural checks** on the worker's output — file-existence,
dependency-graph cycles, lockfile consistency, protected-path violations,
**confidence-axis gates** — and re-invokes the worker with the check results
as external feedback if issues are found. The feedback is mechanically
derived (no LLM). Confidence gating (threshold 9.0 on every worker's
schema-defined axes) is code-enforced for all workers, not just the
implementer.

The conformer loop (`_run_conformance_phase`) is the original instance of
this pattern — it loops on observable build/lint/test signals. The generic
`_run_checked_loop` extends it to all workers.

#### Findings carry a severity; only gating findings re-invoke

A check function's findings are not all the same kind of thing. Some name a
defect that makes the output **unusable** — a dependency on a subtask that
does not exist, a cycle, a subtask with no success criteria. Others are
**advice** about a judgement call that is frequently correct as it stands.

Treating both as retry triggers is a mistake with two distinct costs:

- **Advice cannot converge, so it burns the whole retry budget.**
  `INTRA_DOMAIN_OVERLAP`'s own text is *"consider merging or splitting"* — it
  never reliably reaches zero, because there is frequently nothing wrong to
  fix.
- **Per-subtask advice turns the retry count into a proxy for plan size.**
  Findings like `PHANTOM_PATH` fire once per offending subtask, so a large
  plan must be flawless to beat a small plan with one flaw, and an *empty*
  plan scores a perfect zero — the same defect the sample validity gate
  addresses at its extreme.

So each finding declares a severity. `_run_checked_loop` re-invokes only on
**gating** findings; **advisory** findings are surfaced once, in the returned
warnings, without costing a round. Multi-sample selection likewise ranks on
gating findings only, so plan size stops being a scoring penalty.

**The default is gating.** A finding whose severity nobody declared keeps
today's behaviour, so the classification can be incomplete without silently
weakening a real gate — the advisory set is an explicit allowlist.

**A mechanical floor is not thereby an independent one.** A pure-Python
check can still gate on a planner self-report field the planner is free to
omit (`check_prescribed_command_coverage` on `runs_commands`, measured
populated on under 5% of subtasks) — being deterministic is not the same as
being independent, the same trap documented for `migration_targets` in §8. A
floor must either read something the planner cannot omit — the repository,
the diff, the dependency graph — or repair the omission itself rather than
re-driving the worker that made it.

**Repairing an omitted self-report beats re-driving for it.** The floor
above *is* satisfiable — told explicitly, planners fill the field — but a
full re-plan to fix it once cost roughly the entire first planning pass and
the run then died of budget exhaustion having written no code. leerie
already holds the prescribed commands as structured classifier output, so
`_repair_prescribed_commands` synthesises a subtask that runs them, at zero
worker cost, before the floor is evaluated. A repairable gap therefore never
reaches the re-plan path.

The repair **synthesises rather than picking an owner**: attaching the
commands to "the subtask that owns verification" was prototyped and
rejected, since a verification-shaped matcher hits most subtasks in a real
plan, making an exactly-one-owner rule never fire and a looser one attach
arbitrarily. A dedicated subtask whose entire content is running the
prescribed commands cannot be wrong about intent, depends on the plan's
current sinks (acyclic by construction), and schedules alone in the final
wave.

### Task-referenced file extraction

When the task string references files (detectable by globbing), the
orchestrator mechanically resolves the paths (`_glob_task_references`) and
names them for the planner, which reads them itself; whether the plan
covers what they require is the `task_coverage_judge`'s call (§8). This is
deliberately just a list of paths — naming the files is mechanical, but
judging whether a document's requirements are met is a judgment about
meaning, which is a worker's job, not Python's.

An earlier version harvested the referenced files' headings with regex,
classified the harvested prose with another regex, and gated on substring
overlap between the result and the plan text — three layers of prose
parsing over one mechanism, and the source of a real freeze incident
(identical feedback rounds on a ratio no planner could move, because
backtick+MUST convention headings cannot appear verbatim in a subtask by
construction). It is deleted; `task_coverage_judge` reads the referenced
files itself and judges substance rather than string overlap.

### Multi-sample planning

Multiple independent planner invocations per domain, each a fresh
`claude -p` session (Cross-Context Review, arxiv 2603.12123, 2026: context
separation is the mechanism). Mechanical selection by issue count and
subtask count avoids self-bias (a novel extension beyond the paper, which
tested single fresh-session review only). Controlled by the
`planner_samples` cap (default 3).

---

## 9. Success criteria (informational; historical lock)

Each implementer's first step is to turn its assigned seed into a brief
success-criteria file describing what success looks like for the subtask —
the explicit success condition plus any regression guards worth naming. The
file is **informational**: written for the implementer's own clarity, read
by the conformance phase for context, and useful for human reviewers. The
orchestrator does not gate on whether the file's individual criteria are
satisfied; that is what the confidence gate at §8 is for. The implementer
may update the file freely — there is no lock.

This is a reversal of an earlier discipline that locked the criteria file by
sha256 hash on first write and threaded any later edit through orchestrator
approval, to guard against a stuck model lowering its own bar. With the
confidence gate as the sole load-bearing signal (§8), the bar is the model's
*anchored confidence in the solution*, not the contents of a text file —
there is no longer a fixed bar to lower. The lock and its proposal channel
were removed in the same change that consolidated build/lint/test under the
conformance phase.

A worker that wants to record "this criterion isn't met" does so via
`criteria_results[].met: false` in its result — recorded and surfaced as a
warning, but it does not change the subtask's terminal status.

### Post-work conformance

The §8 confidence gate says whether the work landed; the implementer's
criteria notes describe what it was aimed at. Neither says whether the
*change* is in good standing with the repo it lives in: whether documentation
that describes the touched surface is still accurate, whether tests for the
touched code were updated, whether the change still honors whatever rules the
repo declares for itself (CLAUDE.md, AGENTS.md, `.cursorrules`, a section of
the README, a `docs/` file). These are real obligations of a finished change,
but the wrong thing to bake into the assigned criteria: criteria are scoped
to the subtask, and the repo's rules are an environmental fact that survives
across subtasks.

So a separate phase runs once a subtask's work has settled: the
**conformer**. It triggers only on the success path — implementer reports
`status: "complete"`, commits are present, the worktree is clean, no
protected path was written. It reads the diff just produced, reads whatever
rules files the orchestrator located in the repo, and is empowered to commit
fixes to the same worktree branch — updating documentation, adding or
amending tests, repairing a rule violation it spotted.

Where the rule files live varies, so location discovery is code, not the
worker's problem: a fixed, capped allowlist of paths in the repo root and
`docs/` is checked for existence, and the surviving paths are handed to the
conformer as inputs. If discovery finds nothing, the phase still runs — the
conformer focuses on whether the diff touched a surface the README or a
`docs/` file describes, and whether tests were updated — and silently skips
the rule-conformance axis.

The same discovered set is surfaced to the *implementer* at write time too,
not only to the conformer post-hoc: convention drift is cheaper to prevent
than to catch, since a conformer that only reads the diff afterward can flag
a rule violation but can't re-derive an unwritten visual convention. The
implementer's prompt names the discovered convention docs as paths, and its
evidence gate asks it to reconcile the pattern it followed against them —
advisory, since matching an existing design is judgment, but discovery itself
is code so the doc list can't silently drift. The allowlist therefore
includes the repo's design-system doc (e.g. `docs/DESIGN-SYSTEM.md`).

Two further disciplines sit at the §12 axis:

- **Highest effort, never required.** Building, linting, and the test suite
  passing are *desired* outcomes but never gating ones: each of build, lint,
  and tests resolves to *ran and passed*, *ran and failed*, or *not
  applicable*, and a failure surfaces only as an advisory warning. Making
  "tests pass" a hard requirement here invites the conformer to weaken a
  test or skip a lint rule to clear the bar — the same failure mode §9
  guards against from the other side.
- **The one gating axis: solution completeness.** The conformer carries a
  single gating axis, `solution_defects` — gating for the exact reason
  build/lint/test are not: it is not a self-assertable bar the conformer can
  lower, but an adversarial attack on an artifact the conformer did not
  write (§8 *Independent adversarial verification*). It enumerates concrete
  behavioral gaps — an unhandled input, a missing guard, a decoy shortcut, a
  sibling call site left unedited. A non-empty set of concretely-named
  defects gates the subtask: the found gaps become mandatory additional
  criteria and the subtask retries the implementer with them folded in
  (bounded by `completeness_retry_rounds`; on exhaustion the subtask blocks
  with the residual defects named, fix + `resume`). This is independent of
  `--strict-conformer`, which governs the advisory axes above. It does not
  reintroduce the gameable bar because there is no bar to lower — a defect
  without a concrete case is dropped as non-actionable. When the diff is
  empty or unreadable the axis fails open.
- **Evidence must be production-grounded: a fix that never fires is not a
  fix.** Every gate above asks whether the code matches its specification.
  None asks whether the specification matches reality, and a wrong
  specification propagates cleanly through implementer, criteria, conformer,
  and the final whole-tree pass. Measured on one run: four fixes shipped
  inert, six conformers reported zero defects, and the run exited 0 at
  confidence 8.5. The clearest instance resolved a repo's declared Node heap
  against a `.leerie/config.toml` fixture shape **0 of the 5 repos leerie
  manages use** — every criterion was met; the mechanism never ran once in
  production.

  The response is a `production_evidence` field on the implementer and
  conformer results: the worker must exercise the path it just wrote against
  the repo **as it actually is**, and record the command and what it
  observed. The requirement is not proof of correctness — a fixture can be
  right and a real repo still take a different branch — it is that *"I could
  not make this fire here"* becomes a recorded, gating outcome instead of
  silence. Absence of the field gates, matching *Findings carry a severity*
  above.

  Two adjacent prompt-level disciplines follow from the same measurement,
  because neither can be checked in general: **a new decision point logs
  which branch it took, including the branch that does nothing** (a
  mechanism with zero `log()` calls was found only by manual audit, while
  one that logged its own failure was caught from the log line); and **when
  a test forces a constant to be duplicated, the test is the defect** — an
  implementer that instead declared a second constant to keep a pin green
  produced two figures that then silently disagreed by 384 MiB.

  Two candidate gates were tested against the same run and **refused**.
  *Red-before-green* — requiring the new tests to fail on the base tree — is
  free and worthless: a new test against code that doesn't exist yet
  trivially fails, so all four findings already satisfied it. What has value
  is reproducing the reported *symptom*, a stronger requirement that would
  have caught the finding already fixed by an earlier PR. *Producer-branch
  coverage* — requiring tests to exercise both branches of the producer
  feeding the fix — also fails: instrumented, the defective branch executed
  under the defective fixture too, so the gate passes while the defect
  stands.
- **A stale finding is not a bug.** The evidence contract above asks whether
  a fix fires; this asks whether the failure it fixes still happens. On one
  run a subtask "fixed" a cgroup leak an earlier PR had already fixed before
  the run began, and shipped an event-loop stall doing it — nothing asked. A
  subtask whose planner entry declares `fixes_reported_symptom: true`
  therefore records a `symptom_evidence` object — did the reported symptom
  reproduce on the base tree, how, and what was observed — and a subtask
  that could not reproduce it says so.

  **Advisory, and the asymmetry with the evidence gate is deliberate.** A
  retry can't make a stale finding un-stale — it asks the same worker the
  same question — so gating would spend budget without changing the answer.
  "This may already be done" is the most valuable thing such a subtask can
  report, and it only has to be visible once. This is not "the new tests
  fail on the base tree" — that's free and proves nothing, since a new test
  against code that doesn't yet exist trivially fails; the claim wanted is
  behavioural, which is why the field asks for a command and an observation
  rather than a boolean.
- **No backsliding.** The conformer can add commits but must not write to
  protected paths. The diff-scope check — no writes to `.leerie/`, `.git/`,
  or `.claude/` *except for the user-deliverable subtrees*
  `.claude/agents/`, `.claude/commands/`, and `.claude/skills/` — is re-run
  against the conformer's commits, on the same protected paths and with the
  same terminality as against the implementer's. The `.claude/` carve-out
  exists because those subtrees are the documented Claude Code
  customization locations; top-level files (`settings.json`,
  `settings.local.json`) stay protected as coordination state.
- **No clobbering the implementer's work.** The conformer's charter is
  *additive* — fix drift, add tests, repair rule violations — never to undo
  what the implementer built. But it runs with full Bash in the worktree,
  and a conformer that reaches for the base tree to attribute a pre-existing
  failure (`git checkout <base-ref> -- .`, `git reset --hard`) can revert or
  delete the implementer's committed files, and if it then commits that
  state, integration carries the loss forward silently. So the orchestrator
  snapshots the implementer's committed HEAD *once, before the first
  conformer round*, and afterward computes the implementer's owned set
  (`git diff --name-only <run-branch>..<impl-HEAD>`) and checks, per owned
  file, whether the conformer reverted it to the base version or deleted it
  — a three-way blob comparison of base / implementer / post-conformer
  content, since a *legitimate* conformer edit leaves a distinct third state
  and must not be flagged. A detected clobber is surfaced as a loud advisory
  warning always; under `--strict-conformer` the conformer's commits are
  rolled back to the implementer HEAD **and the subtask is blocked**. It is
  *not* silently auto-rolled-back in advisory mode, since a legitimate
  revert-to-base is indistinguishable from a clobber by git state alone.

  The same guard applies to the final-tree pass, using **a snapshot SHA** as
  the base and the staging HEAD captured before that pass as the
  implementer-work snapshot — emphatically not the run branch itself, since
  the staging worktree has that branch checked out and naming it as the base
  makes base and `HEAD` the same ref, reporting every file the final
  conformer touched as reverted-to-base. Any ref used as a comparison base
  in a worktree must be a snapshot, because a branch name moves when that
  worktree commits — the §12 boundary again: the guarantee that matters
  (committed implementer work survives the conformer) is a code check, not a
  prompt rule.
- **Committed work survives a mid-turn worker death.** The clobber guard
  above protects committed work from the *conformer*; a symmetric threat is
  the *implementer* worker dying mid-turn — e.g. backgrounding an expensive
  final verification step that then gets OOM-killed before it can write a
  checkpoint. The synthesized result is an `incomplete-handoff` whose
  `checkpoint_path` does not exist (`empty_handoff`), naively treated as a
  retryable no-op that discards the worktree and burns retries. But the
  worker may have already committed a complete, green diff and died only at
  a container-environmental verification step. So before failing an
  `empty_handoff`, the orchestrator runs the positive-polarity
  `_branch_has_commits_ahead` gate: **if the branch has commits ahead of the
  run branch, the worker produced a real deliverable** and the result is
  settled as `complete` — routed through the advisory conformance phase,
  which records the unfinished verification as a warning — rather than
  discarded. Only a genuine no-op (no commits) stays retryable; a worktree
  that is gone or on which git fails counts as "no proven commits" and is
  **not** rescued. This is the §12 boundary once more: whether committed
  work exists is a deterministic git check, not a judgment the prompt could
  be trusted to make. The prompt half is the advisory complement: the
  implementer is instructed to commit in-scope work **before** running any
  verification step, so a reaped worker's diff is already committed. One
  class of "genuine no-op" is not a failure at all — a subtask whose
  deliverable a *sibling subtask* already committed to the run branch — and
  is caught separately by re-probing success criteria against the run-branch
  HEAD before failing (§8 *The mid-run sibling case*).

The phase is bounded by a separate cap from the evidence loop: the conformer
gets a small number of orchestrator-level rounds (default 3) to detect and
fix drift. Exhausting the cap with residuals still present does *not* fail
the subtask — the residuals become warnings, the subtask still returns
`complete`, and the work moves on to integration.

**Orchestrator-run build/lint/test is bounded, not contained.** A BLT
command the orchestrator starts is not a worker: it does not pass through
the memory-admission gate and is not enrolled in a cgroup, so it has no
`memory.max`/`pids.max` of its own (§6 *Memory containment* covers the
worker path only). What was one serial pre-wave run becomes one per subtask
per round.

Two things bound it. The default `scoped` mode keeps each measurement
small, so the uncontained footprint is negligible. And `blt_parallel`
(default 2) caps how many run at once, mattering most under
`--subtask-tests full`. Reaping is handled: these run in their own session
and are torn down as a process tree on timeout or exception. Enrolling them
in a cgroup is the architecturally consistent answer and is not done yet —
it would have to go through the broker, whose wire contract needs its own
guard against silent drift.

**The signal that continues the loop is a delta, not a verdict.** The round
cap bounds the loop; what *ends* it early is the orchestrator's judgment
that the conformer has nothing left to do. That judgment must be relative
to the base tree, or the loop becomes unsatisfiable on any repo whose base
is not already green. Measured on a 91-subtask run whose base was RED on
tests: only 6 of 79 subtasks were clean at round 1, and 57 ran the full
3-round cap, each round re-running the whole suite to re-observe a failure
the baseline had already recorded.

The pre-existing failure reaches the loop through *two* channels, and both
must be closed: the **axis** channel (an axis reporting `ran && !passed`),
and the **residual** channel (a `rule_violations_residual` entry — measured,
most residuals in that run carried the single rule `build/lint/tests must
pass`, with `why_not_fixed` explicitly citing the pre-existing baseline; the
conformer read the baseline, obeyed it, and was re-spawned anyway).

So the loop-continuation predicate consults the baseline directly. An axis
that was red on the base tree is not a reason to spend another round, and
neither is a residual that labels itself as being about such an axis.
Anything else is unchanged: a residual about something else, an unlabelled
one, a *newly* introduced one, or a failure on an axis that was **green** at
baseline all continue the loop exactly as before.

**An axis that could not be measured is a third state, and it is not
clean.** An axis whose command never produced a verdict is recorded
`measured: False` and excluded from `red_axes` — "could not measure" is not
"RED". That distinction was originally defined only for the baseline, but
every later measurement can hit the same condition, and the loop predicate
must treat it as unresolved rather than silently clean: reading an
unmeasured axis as clean is how a run once shipped a PR whose test axis
never ran at all, its unmeasurability swallowed by the same `red_axes`
exclusion meant for the baseline (docs/POSTMORTEM-2026-08-14.md, F6). This
is rare by construction, so treating it as unresolved costs almost nothing.

The baseline was already handed to the *worker* as prose in a `BASELINE:`
block, but the orchestrator's own predicate never read it, so the guarantee
lived in a prompt while the code that could enforce it looked away. §12
says that is backwards.

Which axis a residual is about is read from a **schema field the conformer
fills** (`axis`, one of `build`/`lint`/`tests`), never inferred from the
`rule` or `why_not_fixed` prose — inferring it would be regex over an LLM's
response, which *Language-to-JSON* forbids, and the prose is not stable
enough to compare anyway (measured, only a fifth of consecutive round
transitions repeat a byte-identical residual set).

The field is **optional in the schema and gating on absence**, and that is
one decision rather than two: requiring it would cost the entire submission
rather than the single field (the same trade measured on
`plan_overlap_judge`), while treating an unlabelled residual as excusable
would let a worker silently switch the loop off — so an unlabelled residual
blocks, matching *Findings carry a severity* in §8.

This splits the fix's guarantee in two. The axis channel is pure code
enforcement — the orchestrator measured the baseline itself and compares
its own record. The residual channel depends on the conformer labelling its
output. Replaying the motivating run's real round-1 output through the
shipped predicate: 6 of 79 subtasks clean today, most of the rest recovered
across the two channels. The floor is code; the rest is the prompt earning
it.

**The orchestrator measures; the conformer consumes.** Build, lint and test
are executed by the orchestrator, not the conformer. The worker receives
*results* — the exact command, the exit-code verdict, and an output tail —
in a `BLT_RESULTS:` block, and is told it did not run them and must not
re-run a full axis. This is §12 applied to the axis that was costing the
most: whether a suite ran, with what command, in which tree, at what scope,
is mechanically knowable, so it belongs in `leerie.py`; whether a resulting
failure is worth fixing is judgment, so it stays with the worker.

Two things make it a real transfer rather than a request. The command
strings are no longer injected, so running a full axis now requires the
worker to synthesise one. And the orchestrator's measurement **overwrites**
the conformer's self-reported `build`/`lint`/`tests` before any consumer
reads them — the loop-continuation predicate, the residual summary, and the
persisted `conformance` entry all see what was measured, never what was
claimed; the worker's own report survives only as telemetry. The overwrite
happens twice per round: once at the tail for the ordinary case, and once
immediately after the worker returns for the three gates that abandon a
round early (a malformed result, a protected-path violation, a strict-mode
clobber) — also the accurate answer there, since two of those three exits
roll the worktree back toward the state that earlier measurement describes.

This does *not* fix over-firing conformers (measured, implementers ran the
full suite zero times and conformers ran it almost exactly once per round
as prompted); moving execution into the orchestrator saves little by
itself — it is what makes the next two paragraphs possible. The conformer
keeps **targeted falsifiers**, promoted from exception to primary tool:
`production_evidence` (§9) requires exercising the path the diff actually
changed against the repo as it is, scoped work by construction and not a
suite run.

**Per-subtask scope: a delta proxy, not the suite.** A subtask's conformer
does not need to know whether the repo's whole suite is green — the
whole-tree pass (§6) is where that question is asked, and it is the only
place cross-subtask interaction can be answered. What the per-subtask pass
needs is whether *this diff* broke something.

So the per-subtask axes may run a **delta proxy** — a cheaper command
scoped to the changed files — while the canonical command runs exactly
twice per run: once at the base-health baseline and once (per round) on the
integrated tree. Measured, the median subtask in one run touched a single
source file, yet every conformer round ran the whole ~990-file suite at
~499s a time against ~23s for a scoped run of the same repo.

A proxy is repo-declared (`test_scoped` / `build_scoped` in
`.leerie/config.toml`) or inferred from two narrow signals, rendered from a
template with `{files}` and `{base}`. It is **not a subset** of the
canonical command and is not expected to be — `tsc --noEmit` catches a
different set than `next build`. It is a cheap falsifier run once per
subtask, backed by an expensive oracle run twice.

**Not every runner can map source to tests, and the ones that cannot need
the file list filtered.** `vitest related` and `jest --findRelatedTests`
take *source* files and resolve their tests through the runner's own module
graph, so `{files}` can be handed to them whole. pytest has no such
mechanism — it takes paths and collects what is under them, so a source or
docs path is not merely uninformative, it is an error: `pytest
orchestrator/leerie.py` exits 5, `pytest docs/DESIGN.md` exits 4, and mixing
one bad path with a good one still exits 4 — one non-test path poisons an
otherwise-valid invocation. Since a real subtask diff almost always mixes
source and docs with its tests, a `{files}` template on such a repo reports
RED on nearly every subtask.

So a template may instead ask for `{test_files}`, which substitutes only
the test-shaped members of the changed-file set — a narrower proxy than the
import-graph kind, running the tests the diff *touched* rather than the
tests it *affects*, which is the price of working at all on a runner with
no impact analysis. The absence rule below makes it safe: a diff with no
test file renders nothing and falls back to the canonical command, which
measured over a real corpus is the exception rather than the common path.

Absence is the default in every direction: an axis with no resolvable proxy
falls back to the canonical command rather than being skipped; a changed
file set that is empty skips the axis rather than rendering a bare
runner (which would run everything, the exact inversion of the feature);
and a template naming a placeholder this version cannot substitute is
likewise an absence rather than a command to run, since shipping the
literal brace to a shell is a hard error on every subtask.
`--subtask-tests full|scoped|off` lets an operator force either end.

**The round signal is a regression, not a verdict.** Because a scoped
result and a full baseline are not comparable, the base-health baseline
cannot attribute a scoped failure to a subtask, nor should it — the
question is narrower. The orchestrator measures each axis immediately
**before** a conformer round and again **after** it, with the identical
command and scope, and compares those two. An axis green before and red
after is a regression the conformer just introduced — attributable, with no
output parsing and no framework knowledge. Differing command strings are
never compared, which is what stops a scoped `pre` being weighed against a
canonical `post`.

**Opt-in strict mode.** `--strict-conformer` (also `LEERIE_STRICT_CONFORMER`
env var, `strict_conformer` in `leerie.toml`) replaces the advisory framing
with a blocking one: when conformer residuals remain after the round cap is
exhausted, the subtask returns `blocked` instead of `complete`. The user
fixes the residuals manually and runs `resume`; the same check applies to
the final-tree pass. Explicit trade-off: the operator accepts the risk that
the conformer may weaken work to clear the bar, or that pre-existing
environmental failures block unrelated subtasks, in exchange for the
guarantee that no subtask passes with known conformance failures. Off by
default; advisory framing is the recommended default for most repos.

Within a round, the conformer is expected to invoke each build/lint/test axis
**exactly once** (a targeted-falsifier exception applies when verifying a
single file's behavior). Running the same axis multiple times with different
scopes (targeted test → full suite → verification grep) is legitimate
progressive testing and surfaces as advisory only. A Bash-tool-auto-backgrounded
BLT command followed by a fresh BLT invocation, rather than a temp-file `Read`
recovery, is the "retry-instead-of-recover" pattern; the orchestrator injects
it as structured CRITIC-pattern feedback into the next conformer round.
Prompt guidance pairs with this: pass `timeout: 600000` on long-running
test/build commands so the auto-background trap is avoided in the first
place. Same §12 boundary as the rest of the phase — checked mechanically by
parsing the per-worker JSONL log, response is advisory.

The same conformer runs once more after every wave has integrated, on
the staging worktree, with `DIFF_BASE` set to the working branch (§6,
*Worktree and integration model*, final-tree pass paragraph). The per-subtask passes review each subtask's
diff in isolation; this final pass reviews the merged whole. Every
discipline above applies unchanged: the protected-path check is
re-run against the conformer's commits, the round budget is the same
`conformance_rounds` cap, residuals are advisory, and the prompt is
unchanged — only the inputs (cwd, `DIFF_BASE`, and the absence of a
subtask spec / criteria file) differ. The pass's structured output
lands at `st.data["conformance"]["_final"]` and is threaded into the
`pr_writer` payload so its residuals surface as an advisory section
in the PR body alongside the wave-by-wave summary.

**Base-tree health baseline.** Before this existed, the conformer was the
first place the repo's build/lint/test suite ran at all, so a red base tree
(the repo's own tests red on the developer's branch, or leerie's own
container/provisioning unable to run the suite — a missing dep, an OOM kill)
was indistinguishable from a genuine regression: the conformer labels
pre-existing failures "technical debt" and ships a PR on a baseline nobody
established was green. In chain/group flows this compounds, since the base
is often a prior run's branch.

The fix is a **base-health checkpoint**, code-computed and advisory (never a
gate — same §12 reasoning as the conformer's own axes). Once per run, after
the staging worktree is created off the base HEAD but **before any wave
mutates it**, the orchestrator installs the provision recipe into staging
and runs the resolved build/lint/test commands there directly — the
earliest tree where an accurate baseline can be taken, since deps live only
in worktrees (§6½). The verdict is **exit-code based** (non-zero = RED,
zero = GREEN) — deliberately not output-parsed, since summary format varies
by tool.

An exit code cannot distinguish *the command never got to run* from a real
failure, so that case is classified separately and recorded
`measured: False`. It has two causes leerie must cover, since it owns the
environment: the runner is absent (install failed) or the container's own
limits killed it (e.g. `pids.max` blocking a worker-thread spawn). Leaving
the second uncovered pushed the judgment into workers, which then
adjudicated it in prose — measured across a run corpus, 37 worker calls
reasoned about `os error 11` and decided for themselves it was
environmental (docs/POSTMORTEM-2026-08-14.md, F5). §12 says that decision
belongs in code.

A RED base is surfaced loudly (`log()` warning plus `run.json.health.base_suite`)
rather than silently absorbed, since it usually means leerie could not make
the repo green before starting. It is passed to every conformer as a
`BASELINE:` context line so build/lint/test judgment scopes to the **delta**
rather than re-deriving "pre-existing" from scratch each pass, and feeds a
one-line PR-body advisory. None of this gates — the confidence gate (§8)
remains the only load-bearing signal.

The baseline runs the full suite once (tens of seconds to a few minutes on
real repos), so it is **skippable**, and each BLT command is bounded by the
same per-command timeout the provision recipe uses.

---

## 10. Context management — handoff, not compaction

The original specification said each worker should compact its context at 70%
occupancy. This cannot be done as stated: there is no channel for an external
process to make a running worker compact itself, and a worker has no reliable
view of its own context percentage — it can only be observed, not acted on,
from outside.

Leerie replaces compaction with **orchestrator-driven fresh-context handoff**,
achieving compaction's actual goal — bounded context with preserved
progress — without the channel that does not exist:

1. **Granular sizing is the primary defense.** Subtasks are sized so one worker
   finishes within its context. Handoff is a safety net, not the main path; if
   it fires often, the planner is under-decomposing (§5).
2. **A worker nearing its limit hands off.** It writes a structured checkpoint,
   commits whatever coherent partial work it has, and returns an
   *incomplete-handoff* result. The checkpoint is a *fixed schema*, not free
   prose — success criteria and their current status, files touched, decisions
   and their rationale, the exact next action, open unknowns — because a
   freeform handoff is only as good as what a degrading worker happened to
   write down, and a fixed schema fails loudly when a section is missing.
3. **The orchestrator spawns a fresh worker** with the checkpoint as input. The
   successor's first act is to validate the checkpoint against the actual repo
   state before trusting it — a bad handoff fails fast and visibly rather than
   producing confident wrong work.
4. **Handoff is bounded.** A worker can hand off to a worker that hands off
   again; the chain is capped. Exhausting the cap means the subtask was
   mis-scoped — it is reported as blocked for re-decomposition, not retried
   forever.
5. **Involuntary handoffs reuse the same envelope.** A worker that hits its
   per-process wall-clock ceiling or that produces no schema-valid result
   after retry is forced into the same `incomplete-handoff` shape by the
   orchestrator.

   That ceiling is **per worker type**, not one number for everything — a
   single global cap has to be sized for the slowest worker, so a hung
   `classifier` (p99 ~7 min) would hold its phase for the same 90 minutes a
   full `implementer` legitimately needs. Ceilings are derived from the
   observed duration distribution of the run corpus, with headroom over
   each worker's slowest *observed* call rather than merely its p99 (a
   percentile-anchored rule can sit under its own tail, which the planner's
   demonstrably did). A worker whose derived ceiling reaches the global cap
   stays there; an unmeasured worker gets no invented number.

   An operator can bypass the whole table with an explicit global override
   — `caps["worker_timeout_sec"]` (default 5400 s / 90 min, via
   `--worker-timeout` / `LEERIE_WORKER_TIMEOUT` / `worker_timeout_sec`) —
   necessarily a bypass rather than a bound, since the corpus is measured
   on one host and the operator needing it is the one being killed at a
   ceiling measured on a faster machine. The bypass keys on **whether the
   operator set anything**, not on whether the resolved value differs from
   the default — otherwise explicitly passing the default (the first thing
   someone debugging a timeout tries) would be indistinguishable from
   passing nothing.

   When a worker is killed at whichever ceiling applied, the successor is
   spawned exactly as for a voluntary handoff and validates whatever partial
   checkpoint exists. If no checkpoint was written, the missing-checkpoint
   case routes through the corrective-retry path (see §13 caps) and is
   bounded by the `failed_retries` cap rather than the handoff-chain cap.

A lower auto-compaction threshold on the underlying CLI can be set as an
independent backstop, but it is a parallel safeguard, not the mechanism — the
handoff design stands on its own.

### Where coordination artifacts live

Checkpoints and criteria are coordination state, not code, written to a
coordination directory in the main repository, never inside a subtask's
worktree: a worktree is disposable — removed at cleanup — so a checkpoint
stored inside it would vanish exactly when a successor worker needs it.

Coordination state is **per-run**, rooted at `<state-root>/runs/<run-id>/`
(where `<state-root>` is the resolved state directory — default
`$HOME/.leerie/<basename>/`, overridable via `LEERIE_STATE_DIR` /
`--state-dir` / `leerie.toml state_dir`; always outside the target
repo). The default key is the repo basename only; cross-repo basename
collisions (two different abs_paths sharing a basename) are caught at
use time via an `.owner` sidecar inside the dir that records the
abs_path of the repo that owns it — the launcher refuses to write into
a dir owned by a different repo and prints the override knobs. State,
plan, the task document, criteria, checkpoints, logs, the worktrees
themselves, the PR-result sidecar, and the per-subtask `artifacts/`
directory (§5 *Artifact passing between subtasks*) all live under the
per-run subtree.
Two runs in the same repository share no coordination state — each has
its own `runs/<run-id>/` subtree, and neither can clobber the other's
`state.json`, log files, or worktrees by collision.

---

## 11. The clarification procedure

The default is **zero questions** — the original goal of a fully automated
run that does not interrupt the user is kept. The question is when an
interruption is genuinely unavoidable, answered by a strict filter applied
by the classifier:

1. Can it be derived from the **codebase**? Conventions, patterns, integration
   points, and existing behavior are all readable. If the answer is in the
   code, derive it — do not ask.
2. If not, can it be closed by **research**? Best-practice standards for a
   well-understood problem are findable. If research resolves it, do not ask.
3. Ask the user **only** what neither the codebase nor research can resolve.

The only thing that systematically survives this filter is **intent** — *what*
to build, *which* behavior is wanted — because a decision nobody has made
yet exists in no codebase and in no research source. The codebase and
research answer *how*; they cannot answer *what* when that has genuinely
not been decided. A fully-specified request leaves nothing for the filter
to catch, so it runs with zero questions.

The exact wording presented to workers lives in
`prompts/_clarification_filter.md`. That file is the single source of truth
and is included verbatim into the classifier and implementer prompts at load
time. DESIGN.md (this section) is the architectural specification; the
prompt fragment is the directly-loaded text. They must stay in agreement
under CLAUDE.md's three-layer rule.

By default leerie does not surface intent questions to the user at all.
Workers run the filter, treat anything that survives as a forced best-effort
decision, and document it. Pass `--clarify` (or set `LEERIE_CLARIFY=true`
/ `clarify = true` in `leerie.toml`) to opt into surfacing the surviving
questions — interactively if a TTY is attached, otherwise via
`pending-questions.json` and the standard deferred-resume flow. The
no-questions default reflects that most intent questions are closable by
deeper investigation, and that an LLM's instinct to ask is something the
system has to push back against, not ride.

When a feature task's request leaves the source of truth ambiguous, leerie
resolves it from a preference: `codebase` (build from existing patterns only),
`research` (build from researched best-practice standards), or `both` (codebase
first; research only where the codebase is insufficient). Read, in order,
from a CLI flag, an environment variable, a per-repo config file committed
at the repo root, else defaults to `both` — CLI/env outrank the file since
they are session-scoped overrides. The preference is never surfaced as a
question: whichever path resolved it, its value becomes a setting carried to
every planner and implementer on the run, independent of what the classifier
decided — a run that skipped the question still carries the resolved
preference, since the resolver always yields one of the three real values
and never an "unset".

Under `both`, planners may legitimately surface prerequisites from research
that are real but not produced by any code subtask in the plan — the target
Dynamo table provisioned by another repo, an ops runbook, a manual deploy
step. The channel for those is `requires.extent: external` (see §5): the
planner declares the prerequisite and names its external owner, and the
reconciler does not try to wire it as a graph edge. Without that channel,
`both` tends to produce phantom `requires` that abort the run — narrowing
to `source_of_truth = codebase` was historically the only escape hatch.

When Leerie runs under `--clarify` in a context where it cannot block for
an answer, the clarification step is non-blocking: it records the questions,
exits with a distinct status, and lets the surrounding layer collect answers
and resume.

### Mid-execution clarification

The clarification filter runs at Phase 1 — before any implementer has done
work, the right time for *most* intent questions since they are visible
from the task description and the codebase. But some questions surface
only after partial implementation has narrowed the problem to a decision
point neither the codebase nor research can resolve — for example, whether
a refactor should preserve backward compatibility with a deprecated
client, when both choices exist as patterns elsewhere and the task
description does not say.

Leerie treats this as the same kind of question as a Phase-1 clarification,
not a different category — same filter, only the *timing* differs. The
mechanism reuses the existing handoff infrastructure: the implementer writes
a checkpoint of its work-in-progress, returns a status that carries the
question to the orchestrator, and the orchestrator surfaces it through the
same interactive/non-interactive paths the Phase-1 step uses. On the user's
answer (interactively or via a re-run with `--answers`), a fresh implementer
is spawned with the checkpoint as a continuation and the answer added to its
clarification answers — exactly the channel used by Phase-1 answers.

The same constraint that keeps Phase-1 questions narrow applies here: a
question's `why_underivable` must be explicit and grounded in what the
worker tried. Without that gate, a worker is incentivized to ask the user
rather than do the investigative work the filter requires. The schema
makes the field required, and the prompt forbids the exit when `--clarify`
is *not* in effect (the worker must make a best-effort decision and
continue — the default mode, since most intent questions are closable by
deeper investigation).

A subtask has a single re-spawn budget — `subtask_continuations` — that is
consumed by *both* context-exhaustion handoffs and mid-execution
clarifications, with no separate allowance for either. A subtask that
exhausts the budget on a mix of the two is fundamentally mis-scoped and
the orchestrator surfaces it as such. The unified cap is a deliberate
defense against the "ask instead of research" drift: making clarifications
a free resource would invite the worker to prefer asking over investigating.

---

## 12. Deterministic enforcement — the central principle

The single governing principle of the whole system:

> **Prompts are advisory. Code enforces.**

A worker prompt can ask for any behavior, but a prompt is an instruction to a
model, and a model can drift, misread, or — under pressure — rationalize
around it. Anything that *matters* and *can be checked mechanically* is
therefore checked by the orchestrator, in code, with no model judgment
involved.

This is why the orchestrator is a real program and not a skill (§2), and it
recurs everywhere in the design:

- The scheduler does not trust a planner's ordering; it computes the wave order
  itself from the dependency graph (§5).
- The orchestrator does not trust an implementer's "complete" claim; it checks
  mechanically that real work was committed (§7-style verification).
- The orchestrator does not trust an integrator's "resolved" claim; it confirms
  the merge was actually completed (§6).
- Every worker result is schema-validated before it is acted on (§7) — a worker
  that skipped its self-gate fields (§8) fails its own JSON validation before
  the orchestrator reads the payload.
- The orchestrator does not trust a planner to keep `files_likely_touched`
  scoped to the run's own repo when inspect-dir mounts are in play; it
  computes path resolution itself and soft-drops any subtask whose paths
  resolve under a read-only mount instead. The planner prompt documents
  the constraint, but the soft-drop is the actual guarantee.
- The orchestrator does not trust the reconciler to verify its own mutations
  are acyclic; it runs Tarjan's SCC on the post-mutation graph itself,
  recommends a resolution from structural signals, and respawns the
  reconciler once with the cycle data if the first attempt cycled (§5).
  Asking a model to mentally execute SCC detection on a 40+ node graph with
  20+ pending mutations is at the edge of model capability; doing the
  detection in Python and handing the model structured feedback plays to
  model strengths.

- The orchestrator does not trust a worker's confidence score at face
  value; it runs deterministic structural checks (file existence, graph
  cycles, lockfile consistency) on the output **and** gates on the
  confidence axes themselves (threshold 9.0 on every schema-defined axis).
  Both are code-enforced: a worker that hallucinates a file path and a
  worker that self-reports low confidence both trigger re-invocation with
  structured feedback. The confidence gate completes the "code enforces"
  principle — a number the model produces is still externally verified by
  the orchestrator rather than trusted at face value. Task-file coverage is
  the one axis this pattern doesn't fit: judging whether a task's
  referenced files are substantively addressed needs judgment, not a
  deterministic check, so it is verified by an independent LLM judge
  (`task_coverage_judge`) instead — see §"Task-referenced file extraction".

- The orchestrator does not trust the `dep_capture` worker to self-select
  what to write; it schema-validates the worker's structured output
  (`setup_packages`, `language_installs`) before writing anything to
  `.leerie/config.toml`. The worker decides content — what the repo
  genuinely needs — but the code enforces the write path: union merge,
  never-clobber, and the committed-Dockerfile-authoritative rule are all
  implemented as deterministic Python checks that the worker cannot
  override (§6½).

The complementary half is just as important: **what cannot be checked
mechanically is left to the worker, and not second-guessed by code.**
Understanding intent, writing code, decomposing a domain, resolving the
*semantics* of a merge conflict — these need judgment, so a worker does
them. The orchestrator checks the *outcome* where it can, but does not
pretend to do the worker's reasoning.

A reader reasoning about *where a given guarantee comes from* should ask: is
this enforced by code, or only requested by a prompt? The concrete
enforcement points — which function checks what, at which phase — are
catalogued in `IMPLEMENTATION.md`.

### Judgment-worker isolation

Judgment workers (`PLANNING_WORKER_TYPES`: classifier, planner, reconciler,
the judges, provision, the satisfied-probe) are kept away from the user's
checkout by **four layers**, in order of how much each actually buys. Acting
workers cannot be given L1 at all: they get L2, plus L4 per wave and a
path-scoped write denial — see *Acting-worker isolation* below.

This used to be one layer, and it failed. The old guarantee was "judgment
workers cannot mutate state because they run in the real repo cwd *without*
`--dangerously-skip-permissions`", with that flag as a documented opt-out
that "shifts trust onto the prompts." Measured: a classifier on a run with
the flag set implemented an entire task in the operator's checkout on
`main` (`Edit` to three files, a repo-wide `lint:fix`, a `git stash`/`pop`
pair) and died at its turn cap — the prompt said "you run read-only", and
that was the only thing that said so.

**L1 — the flag never reaches a judgment worker.** `claude_p` appends
`--dangerously-skip-permissions` on `autonomous` alone. Probed live (claude
2.1.237, ground truth from the filesystem): with the flag set, a worker
holding only `INSPECT_TOOLS` used `Write` — a tool absent from that
allowlist — to create a file outside its cwd; with the flag absent, the CLI
rejected every such attempt, naming the allowed working directory. **The
working-directory boundary is real; the flag is what erases it.** A
separate probe combined cwd = detached worktree with the flag still on, and
the worker overwrote a tracked file in the real checkout and committed on
its branch — the result behind L2's caveat below.

**L2 — they run in a disposable worktree.** A detached worktree per run,
reset to the checkout's HEAD on entry (`scripts/planning-worktree.sh`).
**L2 is worth nothing without L1** — a worktree is not a boundary, it is
where the boundary lands once L1 restores one (confirmed above: with the
flag on, the worktree did not stop the checkout being overwritten).

**L3 — the operator's escape hatch, re-expressed.** The flag's documented
purpose was always tooling visibility ("repositories where the planner
needs `pnpm`/`tsc`/`vitest`"), never write access — write access was
collateral, and the collateral is what rewrote a branch.
`--dangerously-skip-permissions` now *widens the allowlist* with the repo's
own declared build/lint/test verbs (`_widen_inspect_tools`) instead of
bypassing permissions. The planner gets its tooling; the boundary stays.
The residual is real: a build verb runs arbitrary code, so an allowlisted
`pnpm`/`node`/`python3` can still write outside the cwd (measured:
`python3 -c "open('<outside>','w')"` succeeded while `Write` at the same
path was rejected) — a strictly narrower hole than the flag it replaces,
and L4 covers it.

**L4 — the sentinel, which is the actual guarantee.** `_snapshot_repo_state`
records the real checkout's HEAD, porcelain status and refs before phase 1;
`_assert_repo_unchanged` re-checks after every planning phase and `die()`s
on any delta — same discipline as `check_rebaser_worktree_state` (trust the
worker, then mechanically re-check the claim), run per-phase so it fires
within one worker of the damage. Untracked files are compared deliberately,
since a worker *creating* files is exactly what a clean-tree `??`-filtered
gate cannot see.

This does **not** achieve kernel-level confinement: `/work` is a read-write
bind mount in the same container for the whole run, and nothing short of a
read-only mount or a separate uid stops a determined worker. L1–L3 make the
escape unlikely; L4 makes it loud.

A further layer sits underneath all four and holds even for `autonomous`
workers, which still carry the permission bypass: `DISALLOWED_TOOLS` via
`--disallowedTools` on every session-starting `claude -p` invocation (sole
exemption: the capability probe, which passes empty stdin and exits before
any model call). Unlike `--allowedTools` (permission-tier, bypassed by the
flag), `--disallowedTools` with bare tool names removes tools from the
model's context entirely, regardless of permission mode. The deny list
targets tools that spawn untracked parallel work or set timers the
orchestrator cannot track (`Agent`, `SendMessage`, `ScheduleWakeup`,
`CronCreate`/`Delete`/`List`, `RemoteTrigger`, `PushNotification`, plus
corpus-measured additions like `Workflow`, `Skill`, `Monitor`, the `Task*`
family, `Task` itself, and the three MCP-resource tools).

#### Acting-worker isolation — the same deny, scoped to a path

L1–L4 above are scoped to `PLANNING_WORKER_TYPES`. Acting workers
(implementer, conformer, integrator, rebaser) had only L2, and *L2 is worth
nothing without L1*: they cannot be given L1 (writing files unprompted is
the job), so `claude_p` appends `--dangerously-skip-permissions` on
`autonomous` alone and the working-directory boundary is gone for the whole
execute phase. Measured across the state-dir corpus: 146 `Edit`/`Write`
calls whose `file_path` was under the user's real checkout (36 runs, 4
repos, 8.8% of runs then present) — the judgment-worker share of the same
measurement stopped dead at 0.25.0 (proof L1 works), the acting-worker
share did not.

The fix reuses the same insight — deny rules survive the bypass — scoped to
a path rather than a bare tool name. `_repo_write_denials(repo_root,
run_dir)` renders `Edit(//<root>/**)` (`//` is the CLI's anchor for an
absolute path) and `claude_p` appends it to `DISALLOWED_TOOLS` per call,
derived from `repo_root` rather than hard-coding `/work` so it cannot
silently guard nothing if the mount moves. It denies both the given path
and its realpath when they differ, since `repo_root` is a host path (not
`os.getcwd()`) for `run_rebaser`/`run_recapture_deps`, which run against a
real user checkout whose path may contain a symlink. Probed live: with no
path deny, writes outside cwd succeed; with the deny in place, writes
outside cwd are rejected while writes inside cwd still succeed (containment
that also broke a worker's own worktree would be useless). `Edit(...)`
subsumes `Write`, `NotebookEdit`, `MultiEdit`, and covers `sed -i` (the CLI
models that as an edit).

**The residual, measured rather than assumed.** Bash is not covered by a
file-tool deny — a shell redirect, `rm -f`, `touch`, `mkdir -p` all still
write to the denied path, and the corpus shows real Bash writes and `cd
/work` invocations inside the user's checkout. So **L4 now runs during
execute too**, after every wave, closing the phase that previously had no
check at all — passing `porcelain_only` (dropping HEAD/ref comparisons,
since the execute phase runs for hours and an operator legitimately pulls
their own checkout mid-run; a `HEAD moved` false alarm there is worse than
the escape). Its own blind spot: `git status --porcelain` never lists
gitignored paths, so in-checkout `node_modules/` installs stay invisible —
closing that needs the underlying motive fixed (a worktree with no
dependencies), not a wider guard.

**Two further non-coverages, recorded rather than hidden.** The denial is
skipped when run state lives inside the checkout (`resolve_leerie_root`
falling back to `repo_root / ".leerie"` when `LEERIE_STATE_DIR` is unset —
the direct-invocation/test path; the launcher always sets
`/leerie-state`), since a blanket deny there would deny each worker its own
worktree; `_repo_write_denials` returns `""` in that layout and logs once
rather than staying silently confident. The remote integrator
(`scripts/remote/collect-subtrees.sh`) cannot import the orchestrator, so
it carries its own duplicated deny list (like its duplicated
`SCHEMAS["integrator"]`) — its blast radius is the seeded machine and the
branch it pushes, not the laptop, which is the argument against adding a
third copy of a value the orchestrator owns.

`Task` is the live CLI's name for subagent spawning, `Agent` the retired
one — until `Task` was added, the "no subagent spawning" constraint (§2,
Constraint 1) was enforced only against a name current builds no longer
ship (found in the preflight smoke test's own then-uncontained surface;
contained workers never leaked it, so this entry is defense-in-depth).

**A plain file writer does not belong on this list**, which is why
`NotebookEdit` is classified into `ACT_TOOLS` rather than denied — the
original leak's escape was `Bash`, not `NotebookEdit`, and the deny list
is a single global constant, so denying a writer would remove it from
every acting worker in every repo.

**Containment comes from one builder, not from each call site
remembering it.** Every session-starting `claude -p` argv goes through
`_contained_claude_argv` (the capability probe is the one exemption), so
a new call site inherits the deny list and `--strict-mcp-config` by
construction. This replaces a per-site discipline that had already
failed once: `--strict-mcp-config` was audited by hand, fixed the shell
integrator, and missed `preflight`'s smoke test, which then ran with the
CLI's full default surface (measured: 78 tools, 4 MCP servers, 46
`mcp__claude_ai_*` including `send_message`, `slack_send_message`) — and
left `--disallowedTools` off the shell integrator entirely.
`tests/test_claude_argv_containment.py` now derives and enforces the
rule across the module and `scripts/**/*.sh` rather than naming call
sites by hand.

### Instruction adherence is code-enforced

A sibling of the central principle above, forced into the design by a
production incident: a run was given an explicit, prescribed procedure —
"your ONLY job is to run tool X in a loop until it finishes, then run
tool Y; do not hand-write the output." The planner reasoned (correctly,
on its own terms) that running Y alone would likely be insufficient, and
silently substituted hand-written code for the prescribed step. The plan
looked internally coherent, self-scored high confidence, and shipped a
PR containing exactly the change the user had prohibited.

The gap this exposed: §12's discipline is applied rigorously to *worker*
behavior — schema validation, caps, the conformer gate — but nothing
mechanically checks whether a plan's *shape* honors an instruction the user
explicitly prescribed. The existing plan-time gates (`_validate_plan`,
`check_budget_feasibility`, `phase_overlap_judge`) are purely structural — id
prefixes, cycles, budget, file overlap. None of them compares the plan
against the literal thing the user asked for. A planner can reason its way
around an explicit "only do X" instruction with no mechanical backstop.

The first fix considered — replace a self-graded axis with an independent
judge, as §5½ already did for `decomposition_quality`, applied to the
planner's self-reported `task_understanding` score — was built, tested
against the real incident, and **falsified**: an independent judge asked
to score *understanding* rated the incident plan highly, because the
plan did, in fact, reflect a correct reading of the task. It just chose
not to obey it. Understanding and adherence are different axes; no
framing of an understanding judge catches a plan that understood
correctly and disobeyed anyway.

**The axis that discriminates is instruction adherence, not understanding,**
and it is enforced as a genuinely separate, code-owned gate:

- A **deterministic floor** is the primary layer: when the user's prose
  prescribes concrete commands, and the assembled plan never runs one of
  them, that is a fact checkable by set subtraction over structured data —
  no model judgment required at the check itself. Zero false positives by
  construction, and it cannot be argued away by a plausible-sounding
  rationale the way a judge can be.
- An **independent semantic judge** is the secondary layer, for the fuzzier
  case the deterministic floor cannot see — a plan that runs every
  prescribed command but *also* substitutes manual work the user forbade.
  This judge must be gated behind the deterministic signal that the user
  prescribed a procedure at all: run unconditionally, an adherence judge
  measurably produces false positives against ordinary tasks that were
  never given a prescribed procedure to begin with. Composed as
  is-prescribed → judge, the two-stage gate is clean; validated against the
  judge alone, it is not — see *Opus-judgment, sonnet-workhorse* below for
  why the judge's model tier is itself part of this gate's correctness.
- On violation, the plan is not silently accepted or silently discarded —
  it re-enters the existing planner feedback loop (§8's evidence-gated
  retry), the same mechanical-feedback path every other planner-check
  failure already uses. No new pause/resume machinery; the escalation
  bound is the existing judgment-check-round cap. The re-plan this retry
  triggers is followed by a second `phase_reconcile` call before the
  loop's next judge round, for the same reason `task_coverage_judge`'s
  re-drive is (§8 *Independent adversarial verification*): a re-plan runs
  one planner per category in parallel with no cross-category tag
  visibility, and can reintroduce cross-domain `provides`/`requires`
  drift `phase_reconcile` already resolved on the first pass.

This is deliberately not a new subsystem — the same §12 principle applied:
a plan's adherence to a prescribed instruction is a fact that, in the
deterministic case, can be checked mechanically, so it is not left to a
prompt telling the planner "please follow instructions." Where it cannot
be checked purely mechanically (the semantic-substitution case), judgment
is still required — but independent of the planner and gated by the
deterministic signal, not trusted as an unconditional self-report.

### Language-to-JSON: natural-language interpretation is never regex

A second sibling principle: **all interpretation of natural language is
done by an LLM worker, returned as schema-validated structured JSON —
never by pattern-matching or hand-parsing prose in the orchestrator.**
Python operates only on already-structured data: set membership, string
equality on typed fields, arithmetic. It never infers meaning from prose.

This is the input-side twin of the worker-output contract in §7. If a
check needs a fact that only exists in natural language (does the task
prescribe a specific command; does a subtask's own description reference
a sibling's output), the fact is extracted by the LLM that already reads
that prose, returned as a structured field, and the orchestrator's code
compares structured fields to structured fields. A regex over task text,
planner prose, or a worker's free-text response is not a shortcut — it is
a hand-written model of natural-language understanding that fails
silently on inputs the author didn't anticipate. Regex remains legitimate
only where the string matched is itself mechanical rather than natural
language — a semver, a shell command, a fixed CLI output string, a file
path — never prose a human wrote to communicate intent.

An earlier audit found several orchestrator sites that violated this by
regexing natural-language prose (task text, planner intent,
README/markdown headings). All have since been migrated to LLM-worker
extraction: the task-file coverage harvest, the migration-surface signal
(now a planner-schema field), the README section pre-filter, and
`PHANTOM_ARTIFACT` (now reads a structured `artifact_paths` field — see §5
*Cross-domain surface overlap*).

### Opus-judgment, sonnet-workhorse (historical) — now sonnet for both

Judgment workers (classify, plan, reconcile, judge, verify, gate) and
workhorse workers (implement, conform, write a PR) both default to
Sonnet 5. This was not always true: an earlier model generation showed the
same adherence-judge prompt produce opposite verdicts on the judgment vs.
workhorse tier for the same input, which required pinning judgment workers
to the stronger tier. Sonnet 5's judgment quality on these decision-shaped
tasks has been externally verified to match the prior judgment-tier
baseline, closing that gap — so the split no longer applies. (`implementer`
and `conformer` additionally default to `low` reasoning effort — a
separate, cost-motivated decision; see IMPLEMENTATION.md §2 "Effort
selection".) If a verdict-flip-with-tier regression is ever observed again
on a future model swap, re-run the adherence-judge incident/control pair
before reintroducing a tier split.

---

## 13. Caps and escalation

Every loop in the system has a hard bound. Nothing spins forever; when a bound
is reached, Leerie escalates rather than looping. But the bounds are of **two
different kinds**, and the difference is itself a design point — it is the §12
principle applied to caps.

### Code-enforced caps

Some caps are counted by the orchestrator: subtask continuations, the
mechanical-feedback rounds for a judgment worker, the total number of
workers a run may spawn, parallelism within a wave, and a per-worker time
and turn limit. These are real counters in real code. When one is hit,
the orchestrator takes a defined action — block the subtask, abort the
run with state saved, throttle. Because the orchestrator owns the
counter, the cap is a genuine guarantee.

The post-work conformance cap (`conformance_rounds`, §9) is code-enforced
but its escalation is *advisory*: when hit, residual findings surface as
`conformance_warnings` and the subtask still returns `complete`. The cap
bounds work, the warnings make the unfinished work observable, and the
subtask never escalates to `failed`/`blocked` — §12 applied to a phase
that is itself advisory.

The mechanical-feedback caps (`judgment_check_rounds`,
`planner_check_rounds`, `implementer_confidence_retries`) are also
code-enforced: the orchestrator runs deterministic structural checks on
each worker's output and re-invokes with the results as external
feedback (§8 *Mechanical-feedback loops*). Escalation on exhaustion is
worker-specific: planners proceed with the best result + warnings, the
classifier dies, the integrator aborts the merge. (That last is the
*check-exhaustion* path — the integrator kept returning output the
mechanical checks rejected, a verdict about the work. A worker **crash**
mid-resolution takes the salvage path in §12 instead.)

The multi-sample cap (`planner_samples`) controls independent parallel
invocations. Selection among samples is mechanical (fewest issues, most
subtasks) — no LLM judgment involved.

### Worker-internal caps

Other limits — how many times an implementer or planner re-runs its evidence
gate or validation loop — live *inside* a single worker. The orchestrator
never sees these iterations; it sees only the worker's final result. These
limits are therefore *prompt-governed*: the worker is instructed to bound
itself, and the genuine hard backstop is the worker's overall turn limit,
which the orchestrator does control.

The evidence-gate bound is exposed to users as `--confidence-rounds` (also
`LEERIE_CONFIDENCE_ROUNDS` and `leerie.toml`); the orchestrator passes the
resolved value into each worker's prompt. The knob is real — the worker
reads it — but the worker is what counts iterations against it, so the
guarantee stays prompt-governed. Surfacing it lets a user dial how
persistent workers are at building confidence without changing what kind
of guarantee that bound is.

This distinction must not be blurred: presenting a worker-internal,
prompt-governed limit as a code-enforced guarantee would mislead anyone
reasoning about the system's reliability. The orchestrator enforces the
*consequences* of a worker's result deterministically; it does not count
the iterations inside the worker that produced it. That is acceptable
only because the orchestrator gates on outcomes, not iteration counts —
and because the overall turn limit is a real backstop regardless of
whether a worker honored its instructed self-discipline.

### The two-tier retry policy

When a subtask fails, whether it is retried depends on *why* it failed. The
governing rule:

> Retry a failure only if a corrective note to a fresh worker can plausibly fix
> it. Terminate immediately on a failure that means the worker is broken or
> dishonest — re-running it burns a worker for no expected gain, and a cold
> restart can discard partial work.

A **retryable** failure is a correctable mistake: the worker did real work
but, say, forgot to commit it, or left its worktree dirty. A fresh worker
told exactly what went wrong can plausibly succeed. It is retried up to
the retry cap; a second occurrence terminates it.

A **terminal** failure means the worker itself is unreliable: it returned
a self-contradictory result (claimed success with no supporting
evidence), wrote to a protected path it was told never to touch, or
failed at the process level even after the schema retry. Re-running a
broken worker does not make it honest — a terminal failure ends the
subtask on first occurrence.

Either way a terminated subtask is fatal at its wave boundary: the run
stops with state saved rather than carrying a broken subtask forward into
integration. The specific failure-to-tier mapping is in
`IMPLEMENTATION.md`; the *principle* — correctable-mistake versus
broken-worker — is the design.

### Budget feasibility — fail fast at the cheapest moment

`max_total_workers` is a hard ceiling on the number of `claude -p`
invocations a single run may spawn. The late check, `State.bump_workers()`,
raises `WorkerError` the moment the counter would exceed the cap — a
necessary backstop, but it fires *during execution*, discovering
infeasibility mid-run and leaving only a partial set of integrated waves.

The corresponding *early* check runs once at the plan/execute boundary.
By the time `_schedule()` returns its `(subtasks, waves)` pair, every
unknown that determines remaining budget is resolved: subtask count is
fixed, wave count is computed deterministically (Kahn's algorithm, no LLM
call), and every upstream phase — including the easy-to-forget
per-subtask ones (P1 decomposition's `fit_judge`/`splitter`, phase-3
`satisfied_probe`) — is already billed into `worker_count`. A feasibility
check here estimates the remaining cost (implementer + conformer per
subtask, integrator per wave, finalize) with no free variables beyond an
empirically-bounded per-subtask call multiplier.

**A re-plan needs its own preflight.** The gate above runs once, after
`_schedule()`. A gate that re-plans (`phase_adherence_gate`,
`phase_planning_coverage_gate`) authorises the single largest budget event
in a run — `phase_plan` re-runs the entire P1 decomposition, which
historically dominates total spend — and previously ran with no budget
check at all, able to exhaust the cap mid-decomposition with no code
written and nothing recoverable. `check_replan_affordable` now runs
before each re-plan, projecting its cost from the domain count and the
already-known subtask count (the term the decomposition cost scales on)
and `die()`ing early when it cannot fit. Dying at the gate costs nothing
and names the real cause; dying at the worker cap costs the whole run. It
honours the same `skip_budget_check` opt-out, with `State.bump_workers()`
remaining the load-bearing backstop either way.

**Why the gate cannot move earlier, even though the satisfied-probe
spends first.** The probe runs before `_schedule()`, so its per-subtask
calls are billed before this gate can fire. Moving the gate earlier was
considered and rejected: the probe *drops* subtasks and `_schedule()`
merges the post-drop plans, so anything running before it sees a
pre-drop, systematically inflated count — a guard conservative enough to
be safe there would fire only when upstream spend alone nearly exhausts
the cap, which is almost never. The gate accepts the probe's spend as the
price of an accurate post-drop estimate; `WorkerError` still bounds the
run either way.

This is the §12 principle applied: fail-fast at planner-output time saves
the most compute (implementers/conformers have not yet been spawned) and
surfaces the actionable fix — a recommended `--max-workers`, or a hint to
split the task.

The estimate is intentionally conservative (worst observed per-subtask
ratio plus margin), with a documented escape hatch
(`--skip-budget-check`, same precedence chain as `--skip-smoke` and
`--skip-overlap-judge`) for operators who know the conformer phase will
degrade heavily to advisory warnings or otherwise come in under estimate.

**This gate firing is not a dead end.** Because `_schedule()`'s output is
one of the per-phase planning checkpoints (§6 *Resumable planning*), a
run that stops here has its `subtasks`/`waves` already recoverable from
`plan_snapshot` — `resume` rehydrates them and re-runs only the budget
check, under a higher `--max-workers` or `--skip-budget-check`, rather
than discarding the plan and forcing the operator to re-run from scratch.

---

## 14. Telemetry, judging, and self-healing

Every main-loop LLM call in Leerie passes through one of the sixteen worker types in
`WORKER_TYPES`: `classifier`, `planner`, `reconciler`, `plan_overlap_judge`,
`satisfied_probe`, `provision`, `implementer`, `integrator`, `conformer`,
`fit_judge`, `splitter`, `adherence_judge`, `classification_judge`,
`wiring_judge`, `provision_judge`, or `artifact_registry` (`fit_judge`/`splitter` are the P1
recursive-decomposition workers — see §5½; `classification_judge`,
`wiring_judge`, and `provision_judge` are the independent adversarial verifiers
— see §8; `artifact_registry` is the pre-planning shared-vocabulary worker —
see §5). Each worker type is a distinct **call type** — a
first-class identifier that partitions every captured call into its role in the
system. The call_type partition is exactly `WORKER_TYPES`: one call_type per
worker role, no overlap, no gap. Post-run skill workers — `judge`,
`patch_generator`, `pr_writer`, and `dep_capture` — are not in `WORKER_TYPES`
(they run outside the main orchestrate loop), but they share the same
`claude_p()` invocation path and emit telemetry records with their `schema_key`
as `call_type`. (The self-heal loop's worker uses `schema_key="patch_generator"`;
`heal` is the name of the *skill/phase* — see pillar 3 below — not a `call_type`.)

### The three pillars

Three capabilities build on this partition to make the system observable,
self-diagnosing, and self-improving:

1. **Per-call NDJSON telemetry.** Every `claude -p` invocation emits a
   structured record to a per-run append-only NDJSON file, written by the
   orchestrator immediately after each call returns. Crash-safety comes
   from the format itself: each line is a complete, self-contained JSON
   object, so a hard kill between writes leaves the file valid through
   the last fully-written line.

2. **LLM judge skill.** A Claude Code skill that reads a harvest of captured
   calls (one call_type at a time), applies a multi-dimensional rubric to each
   prompt/response pair, and writes structured verdicts across three
   dimensions: schema adherence, factual accuracy (grounded in the codebase
   or research the worker was given), and hallucination-freeness. The
   rubric is advisory — it lives in a prompt — but the scoring aggregation
   and pass/fail threshold are real Python in the skill's orchestrator
   script (§12 applied: rubric is a prompt, verdict accounting is code).

3. **LLM self-heal skill.** A Claude Code skill that takes the judge's
   verdicts for a given call_type, identifies failure modes, proposes
   targeted patches to the relevant worker system prompt in `prompts/`,
   applies them, and replays the failing samples to measure improvement.
   The loop is capped and its convergence check — improvement, plateau,
   or regression — is real Python (§12 applied: patch proposal is a
   prompt, convergence detection is code).

### The subprocess contract — no new runtime

Both the judge and self-heal skills run exclusively through the existing
`claude -p` subprocess path (the same `claude_p()` function the
orchestrator uses for all workers) — no new runtime, no API key, no
dependency beyond the `claude` CLI. Same resolution as §2: subscriptions
over the metered API, headless CLI subprocesses over an agent library.

The judge spawns a fresh `claude -p` worker per batch of calls to be
scored; self-heal spawns fresh workers for patch generation and for
replaying failing samples against the patched prompt. Each worker sees
exactly the inputs it needs, and its structured output is schema-validated
before the skill's orchestrator acts on it — the same contract as every
other worker (§7).

### The NDJSON file convention

Each run's telemetry lives at:

```
<state-root>/runs/<run-id>/calls.ndjson
```

One file per run. The file is opened for append at run start and written to
by the orchestrator as each call completes. It is never read by the runtime —
the orchestrator writes it and moves on. Reading is a post-run operation:
the judge and heal skills are invoked separately, after the run, against a
harvested set of files.

Each line is a JSON object with a fixed envelope:

```
{"ts": "<ISO-8601>", "run_id": "<run-id>", "call_type": "<worker-type>",
 "call_id": "<uuid>", "model": "...", "input_tokens": N, "output_tokens": N,
 "latency_ms": N, "success": true|false, "system_prompt": "...",
 "user_content": "...", "response_content": "...", "parsed_ok": true|false}
```

Fields are sufficient for the judge to evaluate quality (`system_prompt`,
`user_content`, `response_content`, `parsed_ok`) and for the heal loop to
replay the call against a patched prompt (`system_prompt`, `user_content`).
The `call_type` field is how the judge and heal skills partition their input —
they always operate on one call_type at a time, matching Beacon's design.

### §12 applied — prompts are advisory, code enforces

§12 governs this subsystem the same way it governs everything else:

- The **judge rubric** — what counts as schema-valid, factually grounded, or
  hallucination-free — is an instruction to the judge worker. The worker
  applies it under judgment; the same drift risk applies as with any worker
  prompt.
- The **judge verdict aggregation** — counting pass/fail per dimension, computing
  pass rate across a batch, deciding which calls are "failures" for the heal
  loop — is real Python in the skill's orchestrator script. A Python counter
  cannot drift.
- The **heal convergence check** — is the patched prompt's pass rate above the
  success threshold? is improvement plateauing? is there a regression? — is
  real Python. These are measurements over numbers, not model judgment.
- The **patch proposal** itself — what text to change in a system prompt, and
  where — is a worker output and is therefore advisory. The heal loop does not
  trust it unconditionally: it validates the proposed anchor match before
  applying, and it verifies the improvement by replay rather than by the
  subagent's own assessment.

The heal loop re-applies the evidence-gate discipline from §8: each
iteration must show measured improvement (a quantitative outcome, not an
assertion) before it updates the "best patch so far." The loop is bounded
and terminates rather than running forever; the number of rounds, success
threshold, and plateau-detection window are all configured, not
open-ended.

---

## 14½. Regression tripwires

Signals whose *reappearance* means a specific, already-fixed defect has come
back. Each is here because the underlying bug shipped once and was expensive
to diagnose from first principles the second time; none of them is a gate,
and none can be turned into one — they are things to recognise in a log.

- **`fit_judge crashed for`** … `; accepting as leaf` (leerie's own log line —
  the subtask id sits between the two halves, so grep the first fragment) is
  the stdin-race's downstream symptom. The crash-to-leaf degrade is
  deliberately silent — it is the correct fail-safe — so this line is the
  *only* visible trace. If it reappears, the prompt-transport race (#198) has
  regressed.
- **A worker rejected with "exceeds maximum allowed tokens"** (an *upstream*
  API message, not a leerie string — it will never appear in this source
  tree) means the context-budget fix (#194) has regressed. It stopped being
  possible when the payload moved off the argv, so its return indicates the
  transport changed.
- **A finalize outcome judged before the container has exited** is a
  measurement error, not a leerie failure. Never conclude anything about a
  push until the container is gone AND `run.json` has stopped changing, and
  read `push_error` from `run.json` rather than inspecting the remote — the
  remote lags, and a run that is still finalising looks identical to one that
  failed.
- **A work order launched from its appendix rather than its work-order
  section** produces a plan against the wrong text. The appendix is
  discussion; the work-order section is the specification. When handing
  leerie a long document, pass the section, not the file.

## 15. Known limitations

These are honest, designed-in limitations — not bugs, but the known edges of
what the architecture can guarantee.

- **Unattended execution requires broad write permission.** A worker that
  edits files without a human approving each action must run with
  permission prompts suppressed. A narrower "auto-approve edits only"
  mode was rejected: it still prompts on shell commands, stalling an
  unattended run the first time one is needed. The blast radius is
  bounded by worktree isolation, not eliminated — run leerie on trusted
  repositories, ideally inside a container, and review the run branch
  before relying on it.
- **A worker that exhausts its turn limit without checkpointing loses its
  work.** Handoff depends on the worker writing a checkpoint before it
  stops; a worker that runs out of turns first leaves its successor to
  start cold. This is the likeliest failure mode for an under-scoped,
  too-large subtask — planner sizing (§5) is the primary defense.
- **Handoff timing is heuristic.** A worker cannot read its own context
  percentage; it estimates pressure from proxies like transcript length
  and tool-call count, which can be wrong in either direction.
- **Checkpoint quality bounds handoff quality.** Schema validation catches
  a *structurally* incomplete checkpoint; it cannot judge whether a
  structurally-complete one is *semantically* adequate.
- **Evidence gates reduce overconfidence but do not eliminate it.**
  Anchoring the confidence score to artifacts is a large improvement over
  a self-reported number, but a worker can still misjudge the strength of
  evidence it gathered.
- **Cross-domain dependency detection goes through a reconciler worker.**
  The scheduler wires cross-domain edges by matching capability tags; a
  literal-string match would miss two planners describing the same
  capability differently. A reconciler worker (§5) catches these
  mismatches before the scheduler runs, proposing renames, added
  `provides` declarations, or new connector subtasks. Genuinely
  unresolvable gaps abort the run with the reconciler's diagnosis —
  fail-loud rather than the silent-edge-drop the v1 design accepted.
- **Headless usage is metered.** Subscription-based headless usage draws
  on a finite pool; cost scales with worker count.
- **Parallelism is single-repo per run.** Multiple concurrent runs in the
  same git clone are supported via the per-run state and branch design,
  and multiple clones running concurrently are independent by
  construction — but leerie does nothing to coordinate across clones
  within a single run. For workloads spanning *multiple repositories*,
  leerie offers **run-groups** (§20): N isolated single-repo runs
  launched together with a shared brief and read-only cross-repo
  visibility. The boundary is deliberate — no cross-repo merge, no
  cross-repo DAG edges, N independent (non-atomic) PRs, with cross-repo
  prerequisites surfaced as deploy-ordering notes rather than hard edges.
- **Push assumes a remote named `origin`.** Finalize pushes to `origin`
  and opens the PR against its GitHub repo. A fork pattern where the
  user's write-access remote has a different name (e.g. `mine` pushing to
  a personal fork) isn't supported today; the workaround is `--no-push`
  plus a manual push. A follow-up `--remote <name>` flag is possible but
  outside the current design.
- **System-wide worker concurrency scales with run count.** Each run obeys
  its own `max_parallel` cap; with N concurrent runs the total active
  worker count can be N × max_parallel — bounded per run but not
  globally.

---

## 16. Verification status

A design document should be honest about how much of the system has been
*demonstrated* to work, as opposed to *reasoned* to work.

**Demonstrated.** The deterministic scaffolding: git worktree mechanics
(branch setup, per-subtask worktrees, wave-to-wave dependency layering,
conflict detection, finalization, cleanup) against real repositories; the
orchestrator's control flow (classification, planning, scheduling, wave
execution, integration, validation, finalize, resume) end to end against a
stubbed worker, including failure/retry paths; the deterministic
enforcement points have unit tests.

**Not demonstrated as a matter of principle.** The behavioral quality of
workers — whether evidence gates, handoff, and conflict resolution work
as *intended* rather than merely as *coded* — is inherently unverifiable
by inspection; it can only be observed by running the prompts against a
live model and reading the outcome. The deterministic surface is sound by
construction and by test; worker behavior is validated by production
usage over time, not by this document.

Remote-mode features (`--runtime fly`, git-aware host-to-machine seeding,
stream-back finalize, remote pause-on-failure) stack on the host-side
finalize path (§6 *Finalization*) and depend on the run-branch-as-
durable-record contract; the local-mode finalize path is the foundation
they build on.

**Chain orchestration (§19)** is a laptop-side wave sequencer: each
`leerie chain` submission runs a foreground bash loop that, per wave, fans
out N background `./leerie --runtime fly` invocations and waits for each
to finalize via the existing single-run path. Between waves, the laptop
runs `chain.git_ops.synth_merge_branches` to build the next wave's staging
branch and pushes it to origin. The launcher's `chain` verb and the
ID-dispatched single-run verbs (`status`/`stop`/`kill`/`resume`/
`finalize`/`attach`/`list chains`) are wired end-to-end, operating on
chains by filtering `run.json` by `chain_id`. GitHub credentials are never
on a Fly machine: each per-job `host_finalize` runs on the laptop using
the user's `gh auth` and `~/.git-credentials`, and workers have no GitHub
credentials by construction (`scripts/remote/seed-auth.sh:149-158`
excludes them from the seed tar) — an earlier design placing a
coordinator machine on Fly with a GitHub token has been removed in favor
of this laptop-only model. Chain verification today is unit-level only
(`tests/test_chain_*`, covering credential transport, git operations,
verb routing, and the wave loop with stubs); an end-to-end run against
real Fly worker machines has not been observed.

**Recommended first step.** Run Leerie once on a throwaway repository with a
small, fully-specified task before trusting it on real work.

---

## 17. Traceability to the original specification

Every requirement of the original eight-step specification is accounted for in
the design. Where the design departs from the original wording, the departure
is deliberate and is justified in the section named.

| Original requirement | Where it lives in the design | Note |
|----------------------|------------------------------|------|
| Classify the task into 9 categories | §4; Phase 1 | — |
| A subagent per category | §3, §4; Phase 2 planners | Planners *return plans*; they do not spawn. Forced by Constraint 1 (§2). |
| Decompose into the most granular subtasks | §5 | Target narrowed to *smallest independently verifiable unit* — "most granular possible" over-decomposes (§5). |
| Determine parallel vs. sequential — waves | §5; Phase 3 | Done globally over a merged dependency graph, not per-domain. |
| A subagent per granular subtask | §3; Phase 5 implementers | — |
| Define success criteria | §9 | Written as an informational file; orchestrator does not gate on it. The confidence gate (§8) is the load-bearing discipline; tests / lint / build / per-criterion satisfaction are best-effort signals surfaced as warnings. |
| Plan the change | §8 | — |
| Confidence 1–10 on root cause and solution | §8 | Kept, but anchored to evidence gates — a self-reported number is not a measurement. The only load-bearing gate. |
| Loop until confidence ≥ 9 | §8 | Kept, bounded, and gated on evidence rather than intuition. |
| Implement the change | §3; Phase 5 | — |
| Validate against criteria; loop until met | §8, §9 | Replaced by the §8 confidence gate. The criteria file is informational; the orchestrator does not loop on per-criterion satisfaction (an earlier lock + proposal-only revision channel was retired with the criteria file's load-bearing role). |
| Reassess criteria if strong evidence | §9 | The implementer updates the criteria file freely as understanding evolves; no lock, no proposal channel. |
| Fully automated, no questions | §11 | Default zero questions; the derive-or-research filter defines the only exception. |
| Gather information from the codebase | §11 | Codebase first, research second, user only for genuine intent. |
| Compact context at 70% | §10 | Replaced by orchestrator-driven handoff — no channel exists to trigger self-compaction. A lower auto-compaction threshold is an optional backstop only. |
| (implicit) bounded cost | §13 | A hard cap on total workers; the original bounded every inner loop but not total fan-out. |
| (extension) multi-repo coordination | §20 | Not in the original specification. Run-groups extend leerie to N isolated single-repo runs sharing a `group_id`, with read-only cross-repo visibility and deploy-ordering notes. The original spec was scoped to one repository; run-groups are a deliberate extension beyond that scope, not a mapping to any original requirement. |

---

## 18. Future work

Directions that would strengthen the system but are not part of the current
design:

- **Token-aware budgeting** instead of a blunt worker count — bound a run
  by cost rather than by number of workers.
- **Subtask-level resume.** Resume is currently wave-granular: work done
  since the last fully-completed wave is re-run. Finer-grained resume
  would re-run less.
- ~~A dependency-graph sanity pass~~ — implemented as the reconciler
  worker (§5 and §15): after all planners finish, it resolves vocabulary
  drift between domains' capability tags before the scheduler builds its DAG.
- **Per-domain implementer specialization.** One generic implementer
  serves all nine domains today; nine domain-specialized implementers
  would allow richer per-domain guidance, at the cost of more to
  maintain.
- **Chain dependency DAG.** The chain subsystem (§19) uses an N-wave
  sequential model (wave 0, wave 1, …, wave N−1). A general
  task-dependency DAG would allow arbitrary inter-run ordering for
  workloads that don't fit a purely sequential pattern (e.g. diamond
  dependencies).

---




## 19. Chain orchestration

A single leerie run takes one task and drives it to a merged PR — one
classification, one plan, one wave sequence, one finalized branch. Many
real workloads are *sequences of tasks* that must run in a fixed order
across one repository: run job A and job B in parallel, then run job C
after both complete. That sequencing problem is outside the scope of the
core orchestrator, which is scoped to one run. **Chain orchestration** is
the subsystem that manages it.

### Shape: a chain is N parallel single runs per wave, sequenced by the laptop

A chain is **a laptop-side wave sequencer that fans out N parallel
copies of today's single-run `--runtime fly` flow per wave, then
synth-merges between waves to build the next wave's base branch, then
repeats.** Nothing more.

Every wave job is a normal `./leerie "$prompt" --runtime fly` invocation.
The existing single-run path (`scripts/remote/provision.sh` →
`seed-auth.sh` → `seed-repo.sh` → orchestrator → `decide_teardown` trap
on laptop → `scripts/remote/fetch-branch.sh` → `scripts/host-finalize.sh`
→ `destroy_machine`) handles each job's lifecycle **unchanged**. The
chain wrapper just loops over waves and synth-merges between them.

### Why no Fly coordinator

Earlier designs (v3+v4) launched an ephemeral Fly machine per chain to
hold chain state, watch worker heartbeats, push branches, and open PRs.
That introduced four new failure modes (workers unreachable from the
coordinator's 6PN; coordinator volume contention; coordinator
self-destruct race; coordinator's own GitHub credential surface) and
didn't reduce total Fly footprint — the coordinator was overhead on top
of the worker count.

Shape A removes the coordinator entirely. The laptop is the sequencer;
the workers are normal single-run workers; GitHub is touched only by the
laptop via the existing `host_finalize` mechanism, using the user's
`gh auth` and `~/.git-credentials`. Zero Fly machines hold GitHub
credentials at any point.

### Full flow

```
laptop:
  leerie chain --wave a,b --wave c
    → mints chain_id (UUID)
    → current_base = $USER_REPO HEAD (typically main)

  For each wave N (sequential):
    git -C $USER_REPO checkout $current_base
    For each job in wave N (parallel, in background):
      → ./leerie "$prompt" --runtime fly --chain-id $chain_id &
      → REUSES the single-run path verbatim:
          provision_machine → seed-auth + seed-repo → orchestrator
          → decide_teardown (laptop trap)
          → fetch_branch (laptop pulls bundle + run-state)
          → host_finalize (laptop pushes branch + opens PR)
          → destroy_machine
      → Early-write: immediately after provision_machine, the
        child writes chain_id + wave_idx into its host-side
        run.json so chain-scoped verbs (resume, status) can
        discover the run while the orchestrator is still running.
        fetch_branch later overwrites run.json with the
        orchestrator's copy; the parent's post-wait tagging loop
        re-adds both fields.

    wait for ALL wave-N background jobs to finalize on laptop.
    ◀── At this point: every wave-N PR is open. Laptop has every
        wave-N branch (on origin via host_finalize).

    If any job failed → laptop wave loop exits non-zero. User runs
      `leerie resume <chain-id>` to retry paused runs (and see
      any still-running runs), then re-invokes
      `leerie chain --wave ...` to continue (the wave loop skips
      waves whose runs are all already pushed).

    If wave N+1 exists:
      → laptop synth-merges all wave-N branches (now on origin)
        into a new staging branch leerie/stage/<chain-id>-wave-<N+1>,
        via chain.git_ops.synth_merge_branches (existing function;
        unchanged).
      → laptop pushes the staging branch to origin.
      → current_base = leerie/stage/<chain-id>-wave-<N+1>

  chain done. All wave PRs are open. Final staging branch reflects
  everything.
```

### What lives where

| Artifact | Lives on | Why |
|---|---|---|
| Chain identity (`chain_id`) | `run.json` of each chain run | No coordinator; the chain exists only as the set of single runs sharing a `chain_id` tag. |
| Wave membership (`wave_idx`) | `run.json` of each chain run | Same reason. Used by `synth_merge_branches` discovery between waves. |
| Wave job lifecycle | Single-run Fly machine + laptop's per-job `decide_teardown` trap | Identical to today's single-run flow; no chain-specific code path. |
| Wave-N branches | origin (pushed by each per-job `host_finalize`) | Same as single runs; synth-merge reads them via `git fetch origin`. |
| Staging branch (`leerie/stage/<chain-id>-wave-<N+1>`) | origin (pushed by laptop after synth-merge) | Wave N+1 workers seed off it via the normal seed-repo bundle (laptop checks out the stage branch before fan-out). |
| GitHub credentials | Laptop only (`gh auth`, `~/.git-credentials`) | Workers never see them. The coordinator doesn't exist. |

### Strict invariants

1. **Workers never see GitHub credentials.** Worker env never contains
   `GH_DISPATCH_PAT`, `GH_TOKEN`, or any github.com authentication —
   enforced by the existing `seed-auth.sh:149-158` exclusion list
   (`.git-credentials`, `.ssh`, `.netrc`, `.gnupg` excluded from the tar
   pipe); chain workers take that same path unchanged.

2. **Each per-job lifecycle is independent.** A worker dying mid-chain
   pauses that one run (existing single-run pause-on-failure semantics).
   The wave loop detects the failure via its `wait` exit codes and pauses
   chain advancement; sibling wave-N runs that already completed stay
   done.

3. **Chain-scoped verbs operate by iteration, not coordination.**
   `leerie status/kill/stop/resume/finalize <chain-id>` and `leerie list
   chains` all iterate `$LEERIE_STATE_HOST_DIR/runs/*/run.json` filtering
   by `chain_id`, dispatching to the existing single-run verb per
   discovered run.

4. **The laptop is the sequencer.** Wave transitions, synth-merge,
   stage-branch pushes, and chain-scoped verbs all run on the laptop,
   which must be online for wave advancement; per-job workers run
   autonomously on Fly between fan-out and finalize.

5. **Synth-merge between waves is local + deterministic.** The laptop
   runs `chain.git_ops.synth_merge_branches` against `$USER_REPO` after
   each wave's branches reach origin. Conflicts pause the chain; the user
   resolves manually in `$USER_REPO` and re-runs `leerie chain --wave ...`
   to continue.

### What this design deliberately rejects

- **A coordinator machine on Fly.** No per-chain SQLite, no 6PN HTTP, no
  `chain/coordinator.py`/`chain/state.py`/`chain/fly_client.py`, no
  worker hook scripts — the laptop already handles this for single runs,
  and running the same path N times in parallel costs nothing extra.

- **Auto-retry on failure.** Wave failures pause the chain; the user
  resolves and explicitly resumes, matching today's single-run
  pause-on-failure semantics.

- **Always-on background poller on the laptop.** The wave loop runs in
  the foreground; Ctrl-C mid-chain triggers `_kill_wave_children`, which
  propagates SIGTERM to in-flight wave children, each invoking its own
  `decide_teardown` trap to clean up its Fly machine. Resume re-invokes
  `leerie chain --wave ...`; the wave loop's idempotency check
  (`pushed_at` set on all wave-N runs) skips already-done waves.

### Why this is the right scope for model judgment vs determinism

Per-job behavior stays model-governed within each single run, per §12's
central principle. The chain envelope itself is purely deterministic: wave
fan-out is a bash for-loop, inter-wave ordering comes from `--wave` flag
order, synth-merge is `git merge --no-ff --no-edit`
(`chain.git_ops.synth_merge_branches`, a conflict is a bash exit code), and
chain status is a `jq` filter over `run.json` files — none of it inferred
or judged by a model.

### Relation to run-groups

Chains and run-groups (§20) share the same foundational shape — laptop
as sequencer, `run.json` tagging, ID-dispatched verbs — but address
complementary problems:

| | Chains | Run-groups |
|---|---|---|
| **Repos** | One repo | N different repos |
| **Sequencing** | Sequential waves (A → B → C) | Parallel launch (no ordering between members) |
| **Integration** | Synth-merge between waves; one staging branch per wave transition | No merge across repos (impossible); N independent branches and PRs |
| **State dirs** | One `$LEERIE_STATE_HOST_DIR` for all wave jobs | One per-repo `$HOME/.leerie/<basename>/` per member |
| **Verb scope** | Scan one state dir for `chain_id` | Scan N member state dirs for `group_id` |
| **Deploy notes** | Not applicable within one repo | Cross-repo `external_preconditions` rendered as deploy-ordering notes |

A "multi-repo chain" — waves that span different repos — is out of scope.
The two subsystems do not compose: a chain operates on one repository across
time (waves); a group operates on N repositories in parallel. Mixing them
would require cross-repo synth-merge, which git does not support.

Both subsystems keep the laptop as the sequencer and use no coordinator
machine. The ID-dispatch pattern — passing a UUID to each member invocation
and discovering members by scanning `run.json` files — is identical in shape,
but the implementation must scan across separate state directories for groups
rather than within a single state directory as chains do.

---

## 20. Run groups (multi-repo)

A single leerie run is scoped to one repository. Many real features touch
*multiple* repositories — an API repo and a frontend repo must change
together for one logical capability. Leerie's answer is the **run-group**: N
ordinary single-repo leerie runs launched together as a coordinated unit,
sharing a `group_id` and a brief that makes each member aware of its siblings.

### The core design: N isolated runs, coordinated at launch and reporting

A run-group does not change what a run *is*. Each member is an unchanged,
fully isolated leerie run:

- **Its own repository** — one writeable repo, one basename-keyed state
  directory (`$HOME/.leerie/<basename>/`), one `fcntl.flock` (§6), one
  `state.json`, one flat resume record. Nothing shared at the storage layer.
- **Its own branch** — one `leerie/runs/<run-id>` branch on that repo's
  `origin`. The run-branch invariant (§6, "a run branch, once created, is
  never reset") is untouched.
- **Its own PR** — one GitHub pull request, opened by that member's
  `host_finalize` against its repo's main branch.
- **Its own resume** — `./leerie resume <run-id>` inside the member's repo
  works exactly as it does for any standalone run.

The group layer adds four thin capabilities on top:

1. **Shared brief.** The group brief — joint intent plus each member's
   external contract — is authored once and prepended to every member's
   prompt, so repo B's planner reads what repo A is building before it
   writes its own plan. This is advisory steering; the write-confinement
   guarantee (§12) stays code, not prose.

2. **Read-only cross-repo visibility.** Each member is launched with its
   siblings seeded as read-only inspect-dirs (`--inspect-dir <sibling-repo>`).
   Workers may `Read`/`Grep`/`Glob` under `/inspect/<name>` but not write
   there, enforced by the existing `_filter_offtree_subtasks` guard (§12),
   unchanged.

3. **Deploy-ordering notes.** When a member's planner declares a cross-repo
   prerequisite as `requires.extent: external` (§5) naming a sibling repo,
   the collected `external_preconditions` render as a "merge / deploy
   sibling first" section in that member's PR body. The two PRs cannot
   merge atomically on GitHub — the inconsistency window between a backend
   endpoint landing and a frontend using it is a deploy-ordering fact the
   user already manages (e.g. with feature flags). Leerie surfaces the
   ordering; it cannot enforce it.

4. **Group-scoped verbs.** `status`, `stop`, `resume`, `kill`, `finalize`,
   and `list --groups` on a `group_id` discover members by scanning for
   `group_id`-tagged `run.json` files across the members' *separate* state
   directories, then dispatch to the existing per-run implementation for
   each discovered member. Unlike chain-scoped verbs (§19) this cannot
   assume a single state directory — it iterates the set of member state
   directories, one per member basename. (`stop` is Fly-runtime-only; it
   pauses running machines.)

### Why the lean shape

The rejected alternative folds N repositories into one run — N run-branches
in one state, a per-repo namespace inside `state.json`, a dependency graph
crossing repo boundaries. That rewrites leerie's single most load-bearing
invariant, the run-branch as the resume contract (§6): resume guarantees,
the per-run flock, and the flat state layout are all predicated on one run =
one repo, for a capability (cross-repo atomicity) GitHub doesn't support
anyway — two PRs across two repos can never merge atomically regardless of
design. The lean shape reaches the same user value through the **shared
plan**, not atomic joint execution, so resume, state, isolation, and
finalize mechanics stay untouched.

### State isolation is free

Per-repo state isolation falls out of the existing basename-keyed state
directory design (§6 *Single owner per run dir*): a member that `cd`s into
`../frontend` resolves `$HOME/.leerie/frontend/` independently of any
sibling, and the `.owner` sidecar prevents two concurrent members with
distinct basenames from colliding. The one guard the group launcher must add:
reject any `--state-dir` / `LEERIE_STATE_DIR` override that would pin all
members to one shared directory — correct for a chain (one repo) but a
`.owner` collision for a group (N repos, N dirs required).

### Cross-repo visibility is enforced, not advisory

`--inspect-dir` mounts a sibling repo read-only into the worker's filesystem
(`/inspect/<name>`) — a kernel-enforced `:ro` bind mount locally,
convention-enforced on Fly (the sibling is seeded without write
credentials). `_filter_offtree_subtasks` (§12) soft-drops any subtask whose
files fall outside the member's repo root — the same mechanism that already
stops a single-run worker writing outside its worktree, applied to a new
directory.

### The laptop is the sequencer; no coordinator machine

The group launcher runs on the laptop (the same node as chain fan-out, §19):
it mints a `group_id`, fans out one leerie invocation per member (each
`cd`'d into the member's repo), waits, then tags each member's `run.json`
with the shared `group_id` — discovered from each member's basename-keyed
state dir via the same newest-`finished_at` scan local finalize already
uses. No coordinator machine, no in-container group state, no cross-machine
protocol; GitHub is touched only host-side, per member, by that member's own
`host_finalize`.

### Single-repo is the N=1 degenerate case

A run-group with one member is indistinguishable from a standalone run. The
`group_id` is written into `run.json` and the group-scoped verbs work, but
the cross-repo visibility and deploy-ordering machinery have nothing to
operate on — so the group verb surface can be tested against a single member
before any multi-repo integration work.

### What run-groups deliberately do not provide

- **Cross-repo DAG edges.** A planner in repo B cannot declare a hard
  dependency on a subtask in repo A's plan. Cross-repo prerequisites are
  always `extent: external` deploy notes (§5), never in-graph edges. The
  deep design that would support hard cross-repo edges was rejected (see *Why
  the lean shape* above).
- **Cross-repo synth-merge.** Each member merges only within its own
  repository. Git does not support merging across repositories.
- **Atomic multi-repo landing.** N PRs merge independently; there is no
  two-phase commit across GitHub repositories.
- **A coordinator machine.** The laptop is the sequencer, per §19's
  established pattern.
- **Multi-repo chains.** Composing chains (sequential same-repo waves) with
  groups (parallel different-repo runs) is out of scope. The two subsystems
  are complementary, not composable.
