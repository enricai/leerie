# Leerie — Design Document

> Deterministic, headless task orchestrator for Claude Code. Classifies an
> engineering task, decomposes it into granular subtasks, schedules them into
> dependency-ordered waves, and executes each in an isolated git worktree under
> an evidence-gated implement/validate loop — with the fewest possible
> interruptions to the user.

**Scope of this document.** This is the *theory*: the architecture, the
constraints that forced it, and the reasoning behind each design decision. It
describes the intended system, not the current code. It stays correct across
any reimplementation that honors the same architecture — a line here goes stale
only if the *design* changes, never because a function was renamed or a
constant retuned. Mechanism — function names, cap values, file paths, schemas,
enforcement tables, install steps — lives in the companion `IMPLEMENTATION.md`,
which is true only against the current code. Where the two disagree, this
document defines what *should* be true and the code is the defect.

---

## 1. Purpose

Given one task description, Leerie drives it to a validated, integrated result
without further human input — except where input is genuinely impossible to
derive. Every loop is bounded, every decision is made from the codebase or from
research, and state is kept on disk so a run is observable and resumable.

---

## 2. The two constraints that produced this architecture

The architecture is not a free choice. Two platform constraints eliminate the
obvious designs and leave essentially one.

**Constraint 1 — subagents cannot spawn subagents.** The original concept had
three levels of delegation: orchestrator → domain subagent → granular subagent.
Claude Code's documented rule is explicit: a subagent cannot spawn another
subagent; only the main thread can. A three-level delegation tree therefore has
no native implementation.

**Constraint 2 — a plugin slash-command body is advisory, not executable.** A
plugin command is a skill: its markdown is injected into a model's context as
instructions, not executed as deterministic code. For a long, capped,
multi-wave run, "the model will probably follow these steps" is not a strong
enough guarantee — control flow can drift, and the drift is silent.

Both constraints are resolved by the same move: **the orchestrator is an
ordinary program, not an in-session agent.** Every unit of LLM work is a
separate headless process. The program owns all control flow. Subagent nesting
is impossible because there are no subagents — only independent OS processes.
Control-flow drift is impossible because the orchestrator is real loops and
conditionals, not a model interpreting instructions.

**Why a headless CLI process, not an API library.** "The orchestrator is a
program" still admits two forms. One shells out to the headless CLI binary,
once per worker, and runs on the interactive Claude Code subscription with only
the CLI as a dependency. The other uses an agent library whose calls return
typed objects — less brittle, because there is no marshalling of CLI strings
and stdout — but it authenticates against the metered API rather than the
subscription. Running on the subscription rather than the API was a hard
requirement, so Leerie takes the CLI-subprocess form. The brittleness that
choice accepts (parsing process output rather than typed objects) is contained
by two later mechanisms: worktree isolation limits the blast radius of a
misbehaving worker, and every worker result is validated against a schema
before the orchestrator acts on it.

---

## 3. Architecture

The orchestrator is a deterministic program. It runs six phases; each unit of
LLM work within a phase is a separate headless worker process with its own
context and a defined input/output contract.

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

**Why classification precedes clarification.** The Clarify sub-step runs
within Phase 1, after the classifier, because Leerie cannot know what to ask
until it knows what kind of task this is — the set of questions worth
asking is a function of the classification. Clarify is skipped entirely for
fully-specified tasks.

**A note on phase numbering.** The diagram shows six integer-numbered
phases. Some phases have sub-steps shown as nested bullets — `Clarify`
under `Phase 1`, `Reconcile` under `Phase 2`. Sub-steps that always run
carry no qualifier; sub-steps that may be skipped are explicitly marked
`(optional)`. The phase table in `docs/IMPLEMENTATION.md` §4 uses the
same nesting and additionally lists `Provision` (per-repo dep detection)
as a Phase 1 sub-step — it's an implementation-layer concern not shown
in this architectural diagram.

**Why planners run before scheduling.** Decomposition (Phase 2) and scheduling
(Phase 3) are separate because decomposition needs LLM judgment about a domain
while scheduling is pure graph computation over the merged result. Keeping them
separate means the non-deterministic part produces data and the deterministic
part consumes it — the scheduler never has to trust a model's ordering.

**The division of labor.** Everything that requires understanding — classify,
decompose, write code, resolve a semantic merge conflict — is done by a worker.
Everything that can be checked mechanically — scheduling, caps, retries, state,
integration bookkeeping — is done by the orchestrator. This line is the single
most important idea in the system and recurs throughout: see §12.

**Invocation.** The orchestrator is invoked directly as a command-line program;
that terminal path is primary. A thin plugin skill is also provided as a
convenience entry point from inside Claude Code, but it is only a wrapper — it
launches the same orchestrator program and adds no logic of its own. All
control flow lives in the orchestrator regardless of how it was started.

**Observability.** Workers do their work inside a single `claude -p`
session that takes minutes; the orchestrator surfaces that activity as it
happens. Each worker's stream of tool calls, text, and intermediate
results is read line-by-line, written verbatim to a per-worker log file,
and summarized inline at a user-controllable verbosity level. The
default level shows one-line summary per worker event; the user can dial
down to leerie's pre-streaming terse output (`-q`) or up to raw event
payloads (`-vv`). Errors emit at every level. The per-worker file is
the ground-truth audit trail; the inline view is the live feed.

---

## 4. The nine task categories

Every task is classified into one or more of:

1. **feature-implementation** — new functionality that did not exist
2. **bug-fixing** — correcting wrong behavior, including diagnosis
3. **refactoring** — restructuring without changing behavior
4. **performance-optimization** — faster, lighter, or cheaper; same behavior
5. **testing** — writing and maintaining automated tests
6. **dependency-migration** — upgrading libraries, moving frameworks or API versions
7. **configuration-build** — CI/CD, build scripts, package and environment
   configuration at the application side — dotenv files, build entry points,
   Dockerfiles, GitHub Action workflows, operator scripts that consume cloud
   outputs. Excludes authoring the cloud resources themselves.
8. **infrastructure** — infrastructure-as-code that defines cloud resources
   (CDK / Terraform / Pulumi / CloudFormation / Helm / Kustomize): network,
   IAM, compute, data, messaging, observability backends, and the stack
   outputs (resource ARNs / IDs / endpoint names) the configuration-build
   work consumes. `configuration-build` and `infrastructure` form a
   producer→consumer pair — infrastructure authors the resources;
   configuration-build wires the app to them. The split keeps the
   capability-tag boundary crisp: infra provides
   `<stack>-stack-output-names`-style tags; config-build consumes them.
9. **documentation** — docstrings, comments, READMEs, changelogs

A task commonly spans several categories. One planner is assigned per matched
category; the categories are domains of expertise, not mutually exclusive bins.
The corollary is a **same-work test**: when two categories would produce the
same deliverables — same files modified for the same reason — the classifier
picks the single best-fitting label. The orchestrator surfaces a
`SAME_WORK_RISK` advisory for category pairs that commonly over-classify
(e.g. `bug-fixing` + `feature-implementation`); the classifier addresses it
on its structured-feedback retry.

---

## 5. Decomposition, sizing, and the wave model

### The sizing target

Each planner decomposes its domain into subtasks. The decomposition target is
**the smallest independently verifiable unit of change** — explicitly *not*
"the smallest possible unit." This is a deliberate correction to the original
specification, which asked for "the most granular possible" decomposition.

Over-decomposition is not free. Every subtask runs as a fresh worker that must
re-establish its understanding of the codebase from cold context. Splitting one
coherent change into five trivial subtasks pays that cold-start cost five times
and adds four integration steps. The correct floor is the point below which a
subtask can no longer be verified on its own; below that, finer granularity
buys nothing and costs coordination overhead. The matching ceiling: a subtask
must be small enough that one worker can finish it within its context. A
subtask that would require reading or changing a large surface area is split
before execution begins.

Sizing is also the **primary defense against context exhaustion** (see §10): a
subtask scoped to fit inside one worker's context never needs a handoff.
Splitting a plan is cheap; handing off mid-implementation is not. Planner
decomposition quality is therefore the load-bearing assumption of the whole
system — if planners under-decompose, implementers degrade before they hand
off. It is the first place to look when a run goes wrong.

**Conceptual dominance is a planner-judgment axis, deliberately not a
mechanical gate.** A related sizing failure is *dilution*: one subtask that is
so much more conceptually involved than its siblings that batching it with them
degrades the plan for everything else — the case where the right move is to
isolate it into its own subtask cluster rather than split it or leave it. The
planner prompt asks for exactly this judgment (§2). It is **not** backed by a
code check, and that is a considered decision, not an omission: **sizing is the
wrong variable — fit is the variable.** Two independent studies (Stanford/Microsoft:
30× intrinsic same-task token variance; BAGEN: 47% estimation ceiling) plus our
own estimator confirm that no pre-execution size predictor achieves useful
precision. File count, planner text-length, `requires`/`provides` fan-out, and
text-per-file density all fail the same way — they are proxies for a quantity
(turn count) that cannot be predicted. What *can* be judged is *Task-Context
Fit*: whether a subtask's scope and context are co-minimized (minimum necessary
complexity, maximum relevance). That judgment is tractable; size prediction is
not. Per §12, a signal that cannot be checked mechanically without misfiring
belongs in the prompt, not in code, so no `validate_plan` reject or
`DEFAULT_CAPS` threshold is added for it. The structural mechanism that gives
the planner the codebase knowledge needed to make this judgment well — and the
recursive fit-judge that becomes the authoritative decomposition-quality gate,
demoting the self-scored `decomposition_quality` axis to a non-gating advisory
self-report (removing the self-grading bias of letting the planner grade its
own decomposition) — is described in §5½. (Contrast `UNCOVERED_MIGRATION_SURFACE`, which *is* a
code check because migration coverage *is* mechanically countable.)

### Cross-domain dependencies

Planners run in parallel and cannot see each other's output. Yet dependencies
cross domains: a testing subtask may depend on the feature subtask it tests.
That coupling has to be reconciled somewhere, and it cannot be reconciled
inside a planner that cannot see the other planners.

It is reconciled by the orchestrator with three mechanisms:

- **Intra-domain ordering** — within its own domain a planner declares which
  subtasks must precede which, because it owns and can see those subtasks.
- **Cross-domain capability tags** — a planner cannot name another domain's
  subtasks, so it does not try. Instead each subtask declares the capabilities
  it *produces* and the capabilities it *requires*, as abstract tags. The
  orchestrator matches every "requires" against every domain's "provides" and
  adds a dependency edge from producer to consumer.
- **Reconciler worker** — capability tags are a shared vocabulary with no
  enforced dictionary. Two planners can name the same capability with
  different words (`slm-capture-shim` vs. `capture-slm-call-implemented`),
  and a literal-string match would miss the equivalence. After all planners
  finish, the orchestrator computes the set of `requires` tags that no
  `provides` claims, and if that set is non-empty, spawns a single
  *reconciler* worker. The reconciler reads the full task plus every
  subtask (with their `provides`, `requires`, `depends_on`, and
  `files_likely_touched`) and emits actions across eight arrays. Five
  *resolution* actions — `renames` (two tags mean the same thing — rewrite
  one to match the other), `added_provides` (an existing subtask actually
  produces the capability but didn't declare it), `added_subtasks` (a
  genuine gap — propose a new subtask to fill it), `conditional_drops`
  (drop a planner-emitted consumer subtask whose own `intent` declares it
  conditional on an unresolvable precondition — i.e. the planner authored
  it as "no-op if X" and X turned out to be false; the capability graph
  has no semantics for conditional subtasks, so the reconciler converts
  the planner's prose conditionality into a structured drop),
  `dropped_requires` (drop the consumer's `requires` entry when it was
  over-specified by its planner — an aggregate, coarser synonym, or
  authoring-time decision the same subtask itself records, rather than a
  code artifact another subtask produces; the consumer stays in the plan,
  only the bad edge goes — `dropped_requires` also plays a cycle-breaking
  role, but its primary home is now resolution). Two *cycle-breaking-only*
  actions for when the resolution actions would close a dependency cycle —
  `dependency_edges` (assert an explicit `depends_on` ordering when both
  sides legitimately need each other and one ordering is the right
  answer), `merged_subtasks` (collapse two subtasks into one when the
  cycle reflects a genuine authoring overlap — both edit the same file,
  both wait on the same decision). And one *escape hatch* —
  `unresolvable` for unmet requirements with no plausible resolution
  (aborts the run with the reconciler's diagnosis).
  All judgment about tag equivalence, conditional-drop eligibility, and
  cycle resolution lives in the reconciler worker; the orchestrator computes
  the unresolved set mechanically, runs Tarjan's SCC on the post-mutation
  graph, and applies the worker's output mechanically.

  **Dead-subtask elimination (code-enforced).** A planner can emit
  `requires: {tag, extent: in_plan}` for a capability it expects another
  domain to produce. If that domain returns 0 subtasks, the requires is
  unresolvable. The reconciler correctly surfaces this — but a subtask
  whose *every* `in_plan` requires is unresolvable is fully speculative:
  it tests or guards work that no domain will perform. Before `die()`,
  the orchestrator prunes such subtasks mechanically. This mirrors dead
  code elimination after constant folding in compilers: a domain
  returning 0 subtasks is a constant fold; subtasks depending solely on
  it are dead code. The prune fires only when at least one domain has 0
  subtasks — it does not weaken the reconciler's `unresolvable` verdict
  in the general case. After pruning, surviving subtasks with no
  unresolvable requires proceed normally. If all domains end up empty,
  `detect_no_work` fires. If unresolvable entries remain after pruning,
  the run dies as before.

  Acyclicity is a first-class output property — the reconciler's job is to
  produce an acyclic merged plan, not just to resolve unresolved capability
  tags. If the worker's first attempt closes a cycle (each rename can be
  individually correct yet jointly cycle-creating), the orchestrator
  detects it with Tarjan's SCC, computes a recommended resolution from
  structural signals (planner-declared `depends_on` orientation;
  `files_likely_touched` overlap), and respawns the worker once with the
  cycle data, the recommendation, and a bounded set of acceptable
  operations. The model never has to detect cycles itself; leerie does that
  in Python and hands the model concrete structural feedback. If the
  second attempt still cycles, the run aborts with the SCC and the
  offending mutations named — never a silent bad ordering.

  The same retry-with-structural-feedback pattern applies to the second
  failure mode the post-mutation gates catch: **unresolved `requires`
  tags that survive the reconciler's first attempt**. The common cause
  is the model inventing a new tag in `added_subtasks`/`added_provides`
  without renaming the original consumer's tag to match (two synonyms
  for the same concept that never get unified). The orchestrator
  computes string-similarity hints over the post-mutation `provides`
  namespace, surfaces them in the retry prompt as a *prior* (not the
  answer — textual similarity can produce false friends), and respawns
  the worker once. If the retry leaves the same tag unresolved, the
  run aborts with the structured report.

### `requires.extent` — in-graph vs. external prerequisites

Not every prerequisite a planner identifies is satisfiable by another code
subtask in the plan. When a planner researches its domain (especially under
`source_of_truth = both`), it sometimes surfaces a genuine prerequisite that
lives *outside* the build graph: a Dynamo table provisioned by another repo,
an ops runbook the deploy depends on, a manual step in a different team's
queue. Treating those as unresolved cross-domain edges forces the reconciler
either to invent a connector subtask that itself has out-of-scope `requires`,
or to abort. Neither preserves the insight.

To carry that distinction, each `requires` entry is an object — `{tag,
extent, reason}` — rather than a bare string. The planner classifies each
entry along one axis:

- `extent: in_plan` — satisfied by another subtask in this plan; the
  orchestrator wires a graph edge by matching against `provides`. The
  reconciler resolves these via the action vocabulary described in the
  *Cross-domain dependencies* subsection above (resolution actions for
  unmatched tags; cycle-breaking actions when its mutations close a
  dependency cycle; `unresolvable` as the abort escape hatch).
- `extent: external` — a real prerequisite the planner is declaring lives
  outside the build graph. The `reason` field names the owner (other repo,
  ops runbook, manual step) and why no in-repo subtask could plausibly
  produce it. The orchestrator filters these out of the matching pass
  entirely — they never enter the reconciler's queue — and instead collects
  them into a `preconditions` section of the assembled plan. The human sees
  the insight as a deploy note rather than a hard edge. When leerie is used
  as part of a run-group (§20), a sibling repo in the same group is still
  `external` by design — it is not in *this member's* build graph, and its
  cross-repo dependency cannot be a hard DAG edge. The `reason` field naming
  the sibling is what finalize turns into a deploy-ordering note for that
  member's PR.

The planner is the right classifier because it just did the research that
surfaced the prerequisite — it can answer "is this satisfiable by a code
change I'm describing?" The reconciler cannot, because asking it to predict
whether some *other* domain's planner produces a capability is exactly the
question planners cannot answer about each other.

**Collision rule.** If any planner declares `requires: {tag: X, extent:
external}` and another planner declares `provides: X`, the `provides` wins
and the entry is silently promoted to `in_plan` before the matching pass.
This prevents a planner from unilaterally bypassing a real producer that
happens to exist in another domain's plan. The promotion is mechanical and
needs no reconciler involvement: a real producer for the capability already
exists in the plan, so the edge can be wired without judgment.

`unresolvable` is now reserved for genuinely-broken in-plan tags — typos,
hallucinations, or in-plan capabilities the reconciler can neither rename,
attribute, nor connect. An external prerequisite never reaches that path;
a planner-declared *conditional* consumer (one whose own `intent` admits
it should be dropped if its precondition is false) routes through
`conditional_drops`; and an *over-specified* `requires` entry (an
aggregate or coarser synonym of what the consumer itself provides, rather
than a real cross-subtask dependency) routes through `dropped_requires` —
`unresolvable` is reserved for unconditional consumers whose required
capability genuinely cannot be produced AND is not an over-specified
self-reference.

**Accepting external-blocked subtasks.** When a subtask's only unsatisfied
prerequisites are `extent: external`, the worker will discover the external
dependency is missing (e.g. no Postgres server in the container) and return
`status: blocked`. Since the orchestrator does not gate dispatch on external
preconditions — they are informational, not graph edges — the wave dies and
`--resume` retries the same subtask, which blocks again. The `--accept-blocked
<run-id> <subtask-id>` verb lets the operator acknowledge an external block:
it sets `subtask_status[sid]` to `complete` in state.json so `--resume` skips
that subtask. This preserves the invariant that external preconditions are
a human concern while giving the operator an escape hatch for runs that
would otherwise loop indefinitely.

The result is a single global dependency graph spanning all domains. A
topological sort turns it into waves: subtasks within a wave are mutually
independent and run in parallel; waves run in sequence. A dependency cycle is
unsatisfiable; the reconciler's retry loop tries to break it (preferring
`dropped_requires` / `dependency_edges` / `merged_subtasks` over the cycle-
closing renames), and if that fails the run aborts with the SCC + the
mutations that closed it named — never silently broken.

Cross-domain dependencies are reconciled by the orchestrator from capability
tags (with the reconciler bridging vocabulary drift) and enforced as wave
ordering. Planners can therefore run in parallel without coordination: the
coupling between their outputs is recovered globally by the scheduler, and
vocabulary mismatches that would have produced silent missing-edges are
caught by the reconciler before they reach the scheduler.

### Cross-domain surface overlap

The reconciler bridges *vocabulary* drift — two planners naming the same
capability with different tags. There is a second class of drift the
reconciler does not address: two planners independently proposing
subtasks that produce **the same exported artifact** (the same component,
the same exported function, the same primitive extraction) with
**incompatible APIs**. Because the reconciler's mandate is unresolved
`requires` tags, and because each planner can legitimately declare its
own `provides` tag for the artifact (`auth-shell-component` and
`auth-shell-adopted` describe the same `AuthShell` extraction), this
class slips past every check between planning and integration. The
collision then surfaces as an integrator merge-conflict mid-run, with
worker budget already spent across earlier waves.

A **plan-overlap judge** worker runs between reconcile and schedule
specifically to catch this. It reads the full reconciled subtask list
(title, intent, `files_likely_touched`, `provides`, `requires`) and
emits zero or more `collisions`, each with one of four resolutions:
`merge` (one component satisfies both intents), `drop_a`/`drop_b` (one
intent is strictly superseded), or `unresolvable` (the intents are
structurally contradictory and the run should die at plan time rather
than crash at integration).

The judge is biased toward escalation. Before emitting `merge`, it must
verify the two intents are compositionally consistent — no required-
vs-forbidden prop conflict, no structural body contradiction, no
adoption-site contract conflict — and write a concrete
`merge_feasibility` statement that the orchestrator carries forward as
the merged subtask's unified intent. If no such statement can be
written, the resolution must be `unresolvable`, not `merge`. The
discipline is what distinguishes detection from silent auto-merging of
two incompatible specs into a frankenstein implementer brief. The
orchestrator enforces this in Python: a `merge` emission with empty
`merge_feasibility` is a fatal error.

The judge's recall on the test corpus was 100% (every observed surface
collision flagged including the run that motivated this phase), with
the merge-feasibility discipline correctly downgrading incompatible-API
pairs to `drop_*` and `unresolvable`. Skip is automatic on single-
planner runs; opt-out via `--skip-overlap-judge`. The complementary
file-overlap warning (`warn_cross_planner_file_overlap`) remains in
place but stays advisory — the judge handles the load-bearing case;
the warning surfaces the deliberately-permissive same-file-different-
surface cases.

A single subtask can legitimately overlap with several siblings on
different artifacts — e.g. one subtask creates a new config file
*and* wires an existing config to it, each half colliding with a
different sibling's narrower piece. The judge's protocol stays
pairwise; the orchestrator walks the pairs into a coherent cluster
decision via the **anchor-survivor rule**: when one subtask sid
appears in two or more non-`unresolvable` collisions, it is the
*anchor* of that cluster and survives every merge it participates
in. The anchor is by construction the subtask whose surface
overlaps with each of its partners, so absorbing each partner *into*
the anchor matches what the judge described. Without this override
the default lex-smaller survivor rule (a determinism device with
no semantic content) would silently keep an arbitrary narrower
subtask and discard the spec the judge actually identified as
broader. The orchestrator also enforces one pathological pattern
in this neighbourhood: a `drop_*` whose dropped sid is an anchor
of another collision contradicts itself (asking to delete the
subtask other collisions claim absorbs them) and `die()`s at plan
time with both pairs surfaced.

The anchor rule introduces one new invariant the orchestrator must
preserve: **merge_feasibility carry-forward**. Each `merge_feasibility`
statement is the load-bearing unified-intent record for the pair it
came from (see the discipline above). When subtask X is absorbed by
subtask Y in a merge, *every* `merge_feasibility` statement that has
ever been appended to X's intent — including ones from prior merges
where X was itself a survivor — must be preserved in Y's intent.
Otherwise a cluster like `merge(B,D)` followed by `merge(A,B)`
silently loses the mf statement from the first merge as B is
absorbed into A. This is the same silent-data-loss class the
per-pair merge-feasibility discipline exists to prevent, applied
across the chain of absorptions rather than within a single pair.

**Post-merge acyclicity.** A collision resolution's dependency-union — the
survivor inheriting the absorbed subtask's `provides`/`requires`/`depends_on`,
plus downstream reference rewriting (any subtask depending on the absorbed sid
now depends on the survivor) — can introduce transitive cycles absent from the
post-reconcile graph, even though the phase 2½ acyclicity gate passed before
these resolutions ran. The most common shape is a *pure dependency-tag
artifact*: the survivor absorbs a `provides` tag that some third subtask already
`requires`, closing a back-edge through a node outside the merged pair — with no
shared file overlap at all (the cycle diagnostic reports `Shared
files_likely_touched: none`). This affects both `merge` and `drop_*`
resolutions, because `_apply_overlap_drop` likewise unions the dropped subtask's
`provides` into the survivor and rewrites downstream `depends_on`.

**Id-vanishing operations must rewrite inbound references.** The rewriting above is
not a merge-specific courtesy; it is an invariant every operation that removes a
subtask id from the plan owes. Merge, drop, the phase-3 soft-drop filters
(`filter_offtree_subtasks`, `filter_satisfied_subtasks`), and P1 recursive expansion
all vanish an id. Each owes the plan two things: the vanishing subtask's
`provides`/`requires`/`depends_on` must be carried by its successor(s), *and* every
subtask referencing the vanishing id via `depends_on` must be rewritten to reference
those successor(s).

The second half is easy to forget because the two dependency channels do not fail
alike. The tag channel self-heals: `provides` is inherited by successors and
`_build_predecessor_graph` resolves a `requires` tag to *every* provider, so a
tag-expressed edge survives an id vanishing untouched. Only the id-expressed
`depends_on` channel dangles — silently at `schedule()`, which drops an unknown
predecessor without a word, and then fatally at `validate_plan`. An operation that
gets this wrong therefore looks correct under tag-based plans and dies only when a
planner happens to express the same intent by id.

Where an id vanishes into **several** successors (expansion), the rewrite fans out to
all of them — matching what the tag channel already does, and costing no additional
waves when the successors are mutually independent (they occupy the wave the parent
would have). Where an id vanishes with **no** successor (a drop), the reference is
pruned. Fanning out to a single "representative" successor, or dropping the id edge
on the theory that a tag will cover it, is the same silent-data-loss class named
above: a parent with no `provides` has no tag edge to fall back on.

The orchestrator handles this with **per-resolution cycle avoidance** rather than
an all-or-nothing post-hoc gate. Before applying each collision it tentatively
applies the resolution to a throwaway copy and runs Tarjan's SCC; if the
resolution *would* introduce a cycle, that specific resolution is **skipped**
(`skipped_would_cycle`) and both subtasks are kept separate for the integrator to
resolve at integration time. Every non-cycling resolution still applies. This is
a deterministic, per-resolution degradation of the `--skip-overlap-judge` escape
hatch, achieved locally with no extra worker round-trip. The judge is *not*
re-prompted: the cycle is a global-graph property of the whole subtask DAG,
outside the judge's pairwise-surface competence (its merge decision is typically
correct — two planners genuinely producing the same artifact — and the cycle is
an orthogonal topology side effect of unioning their dependency tags). Tarjan's
SCC still runs on the final post-merge graph as an internal backstop assertion;
with per-resolution avoidance in place it must never fire, so a surviving cycle
is treated as an orchestrator logic bug (the tentative check and the real apply
path disagreed), not a user-recoverable condition.

### Migration-surface completeness

When a plan introduces a new pattern replacing an old one — a new
accessor replacing direct field reads, a new seam replacing scattered
inline logic — the **migration surface** is the set of all call sites
of the old pattern. A plan that creates the seam but does not cover the
consumers is structurally incomplete: the seam exists but the codebase
still uses the old path, and a follow-up run will discover the gap and
repeat the classification/planning cost.

This is enforced mechanically at two levels:

- **Intra-domain (CRITIC-enforced).** `check_planner_output()` scans
  each subtask's `intent` and `investigation_notes` for migration
  signals (regex-detected phrases like "replaces direct `X`" or
  "extract `X` as the new seam"). For each detected old-pattern
  string, the check greps the repo for call sites, cross-references
  against `files_likely_touched` across the domain's subtasks, and
  emits `UNCOVERED_MIGRATION_SURFACE` when > 5 files are uncovered.
  The CRITIC loop feeds this back as structured feedback; multi-sample
  selection deprioritizes samples that miss the surface.

- **Cross-domain (advisory).** `warn_layer_gaps()` runs on the
  reconciled plan before scheduling and surfaces two heuristic warnings:
  (1) a subtask modifies `schema.prisma` but no subtask touches seed
  or migration files (database initialization gap); (2) a subtask's
  `provides` tags contain env/bootstrap/secret/credential keywords but
  no subtask touches `.env.example` or env documentation (env-contract
  gap). These are advisory `log()` warnings following the same pattern
  as `warn_cross_planner_file_overlap()`.

### Provider-subset subtasks (advisory)

A planner does not always know that a *sibling subtask* in the same plan
will produce another subtask's entire deliverable. The common shape: a code
subtask lists a test file in its `files_likely_touched` and commits that
test edit in the same commit, while a separate test-only subtask — scheduled
a wave later, `requires`-ing a tag the code subtask `provides`, and whose
whole surface is that same test file — reaches its worker with nothing left
to commit. The mechanical no-commits gate then fails it, and (before the
mid-run satisfied rescue, §8) the run loops to a wave death.

`warn_provider_subset_subtasks()` surfaces this one phase earlier. Reusing
`_build_predecessor_graph` (so "predecessor" matches the scheduler exactly),
it flags any subtask whose entire `files_likely_touched` set is a subset of
the union of its **direct** ordered predecessors' files (predecessors via
`depends_on` or a `requires`→`provides` tag match; direct edges only, not the
transitive closure — a subtask owned only by an indirect predecessor is left
unflagged to keep the signal specific, and the §8 rescue catches it anyway).
It is **advisory only — never a drop**: a subtask may make a genuinely distinct edit to a shared file, and
silently deleting it would be a strictly worse failure than an extra worker
round (the same safe-direction reasoning as the satisfied-probe's
conservative default). The actual safety net is the post-execution mid-run
satisfied rescue (§8 *The mid-run sibling case*), which settles such a
subtask `complete` when its criteria are already met on the run-branch HEAD;
this warning just lets the operator re-frame the plan before workers run.

### Artifact passing between subtasks

Some subtasks produce a structured deliverable that a downstream subtask
consumes — a research spec that an implementation subtask reads, a
parameter set that a configuration subtask applies, a design summary that
a coding subtask follows. Committing such a deliverable to the worktree
is the wrong shape: it pollutes the merged branch with a coordination
document users did not ask for, and it relies on the downstream worker
inferring relevance from the commit history rather than receiving the
deliverable through a declared contract.

The contract for cross-subtask deliverables is therefore separate from
the contract for code changes. A producing subtask returns an
`artifacts` field on its implementer result; the orchestrator persists
the artifacts to `<state-root>/runs/<run-id>/artifacts/<sid>.json` and injects
them into the prompts of subtasks whose predecessor graph names the
producer. Code-implementation subtasks emit an absent or empty
`artifacts` field — the deliverable mechanism does not change for them.

The routing channel is the predecessor graph already used for wave
ordering. A subtask B receives subtask A's artifacts when either (a) B
declares `depends_on: ["A"]`, or (b) B declares a `requires` entry whose
tag matches one of A's `provides`. No separate dependency mechanism is
needed — the same edges that gate execution also route artifacts. This
preserves tight-context discipline: a subtask sees only the artifacts of
its declared upstream, never the run-wide artifact set.

The artifacts directory is owned by the orchestrator. Workers do not
write there directly — the artifact payload travels through the
implementer result JSON, and the orchestrator materializes the file. The
`.leerie/` protected boundary stays intact; the artifacts channel exists
*instead of* widening that boundary. A producer subtask whose only
output is artifacts is permitted to return `status: "complete"` with no
commits — the `check_branch_has_commits` gate treats a non-empty
`artifacts` field as a deliverable that substitutes for a code commit.

This is the sanctioned channel for cross-subtask coordination data that
should not land on the production branch. Subtasks that need to share
production code use the existing worktree/commit/integration model; the
artifacts channel is for the orthogonal case where the data is
deliberately ephemeral to the run.

### Why waves are sequential

Each wave's worktrees are branched from the integrated result of all prior
waves. A subtask therefore always sees the complete, validated output of
everything it depends on — never a half-finished intermediate state. Sequential
waves are what make "this subtask depends on that one" mean something concrete:
the dependency is satisfied in the filesystem the dependent subtask starts from.

---

## 5½. ENRIC grounding — codebase-structural decomposition (P6 + P1)

Leerie's planner is a judgment worker: it reads a task description and a
light grep/glob seed of the codebase, then decomposes. The weakness of that
baseline is structural: an LLM instructed to "investigate the repo" in a prompt
forms shallow, prompt-driven splits. The ENRIC framework identifies two
principles that close this gap — P6 (questions shaped by the codebase itself)
and P1 (Task-Context Fit as the sizing variable) — and telemetry over 200 runs
confirms exactly this failure: 20% of runs exhaust the implementer's context
budget mid-execution, 84% of those concentrated in migration sweeps where the
planner packed 30–65 files into one subtask.

### P6 — codebase structural map (foundation)

P6's thesis: decomposition quality comes from the system *knowing* the codebase
before the LLM acts, so shallow splits are structurally impossible. The
mechanism is a **repo-map** — a tree-sitter symbol/reference graph computed
once and mtime-cached at `<state-root>/repo-map-cache/`.

**`build_repo_map(repo_root) → RepoMap`** extracts definitions, references,
and signatures per source file via tree-sitter `tags.scm` queries (multi-language
via prebuilt parsers; `universal-ctags`/`ast-grep` fallback for long-tail
languages). It builds a file/symbol reference graph (defs→refs edges), caching
by file mtime so only changed files re-parse. Measured on a real Python repo:
4 ms/file, 450-node graph, warm re-parse negligible.

**`rank_repo_map(repo_map, seed_files, seed_symbols) → ranked_subgraph`** runs
personalized PageRank biased toward the current task's files and symbols,
emits a k-hop ego-graph, and binary-search-fits the result to a token budget
(default ~1 k tokens, a new `DEFAULT_CAPS["repo_map_tokens"]` entry), ranking
top symbols at prompt extremes for recency bias. Measured: 13/15 task-relevant
symbols in the top-ranked subgraph in 0.92 s on a 450-node graph.

The ranked subgraph is injected into the planner context (and, per subtask, into
the splitter, re-ranked to each node's files). This generalizes the existing
`extract_task_file_structure` seed — P6 is a structurally richer version of
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

- **Migrations (dominant case, 84% of truncations):** `partition_files(exhaustive_list, ~8)`
  — a *deterministic* chunker that achieves 100% coverage and 0 overlap by
  construction. The exhaustive file list comes from P6 / `_grep_old_pattern`. A
  `splitter` worker only **titles and writes success criteria** for each
  pre-computed chunk; it never decides which files go where.
- **Coupled minority:** the `splitter` worker emits children along structural seams
  that the repo-map exposes, backstopped by the existing `UNCOVERED_MIGRATION_SURFACE`
  check (`_check_migration_surface`) which already rejects any split that fails to
  cover every file.

**`recursive_decompose(subtask, depth) → list[leaf_subtasks]`** is the loop:

```
conf = fit_judge(subtask)                         # P1 confidence, measured discriminating
if conf >= 0.70 or depth >= MAX_DEPTH(5):
    return [subtask]                              # leaf
children = split(subtask)                         # code-partition (migration) or
                                                  # LLM-splitter (coupled)
for each child: recurse(child, depth + 1)
# no-progress guard: 2 consecutive rounds where no child's conf rises above
# the parent's → accept parent as leaf + emit warning
flatten the tree → leaf subtasks
```

Bounds: `DEFAULT_CAPS["decompose_max_depth"] = 5`,
`DEFAULT_CAPS["decompose_fit_threshold"] = 0.70` (measured),
`DEFAULT_CAPS["decompose_noprogress_rounds"] = 2`. Every judge/split call
passes through `st.bump_workers` — a runaway tree hits the worker-cap backstop.

### Wire-in to `phase_plan`

After each per-category planner returns its first-pass subtasks, `recursive_decompose`
runs over each subtask; the union of all leaves is the flat set. The **existing**
path then continues unchanged: `phase_reconcile` → `phase_overlap_judge` →
`schedule()` → `validate_plan` → `write_plan` → `phase_execute`. The existing
self-scored `decomposition_quality` axis is demoted to a non-gating advisory
self-report: the independent `fit_judge` is now the authoritative
decomposition-quality gate (removing the self-grading bias BAGEN documents).
The axis remains in the planner schema as a signal, but `check_planner_output`
no longer escalates on it — only `task_understanding` gates the planner.

**Expansion vanishes the parent's id, so it owes the inbound-reference rewrite**
(§5 *Id-vanishing operations*). A first-pass sibling that declared
`depends_on: [parent]` must come out depending on every leaf the parent became;
otherwise the edge dangles and `validate_plan` kills the run after the full
planner/fit_judge/splitter spend. The rewrite happens at **two** levels, because
neither alone sees every vanished id:

- **Intra-generation, inside `recursive_decompose`.** The splitter is told it may
  give a child `depends_on` on a *sibling* child. When that sibling then recurses
  and splits again, its id vanishes mid-tree — visible only to the frame that
  created it, never to `phase_plan`, which sees fully-flattened leaves and so never
  learns the intermediate existed. Each generation therefore remaps its own
  children's sibling edges before returning. On the migration path this is provably
  a no-op: `_migration_child` builds children in code and no child can name a
  sibling.
- **Cross-subtask, in `phase_plan`.** `recursive_decompose` takes a single subtask
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

The ID is known at container creation time — before the orchestrator starts.
There is no deferred computation and no rename. The launcher passes the ID
to the orchestrator via `--run-id`.

The same string appears in three places: the run branch name
(`leerie/runs/<run-id>`), the per-run state directory
(`<state-root>/runs/<run-id>/`), and the PR body. A user looking at any of the
three can grep for the others; for Fly runs the run_id is also the machine
ID visible in the Fly dashboard.

A run identifier is *per-run*, not per-repository. Two concurrent invocations
in the same repository produce two different `run_id`s — their branches,
state directories, worktrees, and PRs are disjoint by construction (each
container/machine gets its own unique ID from the runtime). There is no
shared "staging" namespace that two runs could collide on.

### The run branch as an integration buffer

Integration does not happen on the user's working branch. Each run has its
own **run branch** (`leerie/runs/<run-id>`) that receives every subtask's
work; the user's branch is untouched until the run finishes and succeeds. A
failed or messy integration therefore never lands on the branch the user
cares about. Multiple runs in the same repository each have their own run
branch and integrate independently.

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
`setup-run.sh` repeats the check as defense-in-depth for the `--resume`
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
failed/blocked subtasks, so partial integration is a matter of
invocation order: `integrate_wave` runs before `die()`. The wave
counter (`completed_waves`) is **not** incremented for a
partially-integrated wave, so `--resume` re-enters the wave.
Already-integrated subtask branches produce a no-op `git merge
--no-ff` ("Already up to date.", exit 0) — `integrate.sh` uses `git
merge --no-ff`, which is idempotent on branches that are already
ancestors of the run branch.

### The run branch is the resume contract

The run branch is also the durable record of everything completed so far:
every integrated wave is a commit on it. This is what `--resume` is built on.
Run state records *which wave* to resume from; the run branch holds *the
work* every prior wave produced. The two together are the entire resume
contract. Within a wave, `phase_execute` skips subtasks whose
`subtask_status` is already `complete` — only failed or blocked
subtasks are re-run. When every subtask in a wave is already complete,
the wave is skipped entirely and `completed_waves` is advanced.

This places one hard requirement on the design: **a run branch, once
created, is never reset.** Setup creates it only if it does not already
exist (and a `run_id` collision against an existing branch is a preflight
failure, not a silent overwrite). On a resume the branch already carries
the completed waves' commits, and resetting it would silently discard them
while the wave loop resumed past them — delivering a final result that is
missing everything before the interruption. "Create if absent, never reset"
is not an implementation nicety; it is the invariant the resume guarantee
depends on.

When more than one run is in flight in the same repository, `--resume`
needs to know *which* run to resume; the discovery scans
`<state-root>/runs/*/state.json`. An explicit run-id always wins and must
match exactly (an unknown id fails closed — resume never falls back to a
guess when the user named a run). Without one, the orchestrator considers
only the runs that are actually resumable — those whose derived status is
`in-progress`, `paused`, or `incomplete` — and picks the most recent. A
run that has finished (`done`, `done-pushed-pr`) has nothing to resume, and
one that needs operator attention first (`seed-failed`, `sync-failed`) is
never auto-picked: both are listed, not chosen. When no run is resumable,
or the newest cannot be identified, `--resume` lists the candidates and
requires an explicit id.

Recency is read from `started_at`, falling back to the state file's mtime
when that field is absent — a run missing a timestamp must never sort above
a real one and win the auto-pick by accident.

Auto-picking a run that is still running is not an error: the run
directory's `flock` (see *Single owner per run dir*) rejects the second
orchestrator, so the outcome is a clear "already running", not a
double-drive.

The resumable-status narrowing belongs to `--resume` alone. The read-only
verbs that share the same run-selection logic (`--report`, `--phase`) skip
it: they act on a run's *records*, not its remaining work, and a finished
run is the ordinary thing to report on.

### Single owner per run dir

`--resume` picks a run; but `--resume` does not by itself prevent the
*same* run from being resumed twice. The hazard is concrete: a user
invokes `--resume` while the original orchestrator is still alive, the
launcher dutifully spawns a second orchestrator, and two processes now
race on the same `state.json` and the same run-branch worktrees — both
spawn workers, both write conformance entries, both interleave log
lines into the same `orchestrator.log` that the launcher tails. State
diverges; worker budget burns on duplicate work; the user sees a
streamed log whose progress prefix oscillates because the two
orchestrators have diverged in-memory views of `subtask_status`.

The architectural property: **at most one orchestrator owns a run
directory at any time.** The mechanism is an exclusive advisory flock
on the run directory, acquired in `State.__init__` and released
by the kernel on process exit (clean, SIGTERM, or SIGKILL — no manual
pidfile cleanup, no `/proc` liveness check, no PID-recycling false
positives). A second orchestrator that tries to construct `State` on
the same run dir gets `StateLockedError` and exits with `EXIT_LOCKED`,
the launcher routes the user to `leerie --resume <run-id>` instead
(which, observing the live-orchestrator condition, attaches to its
log stream rather than spawning a duplicate).

Why the *directory* and not `state.json`: `State.save()`'s atomic
`tmp + rename` swaps state.json's inode every save. A lock on
state.json's fd would be orphaned from the new inode at every save,
opening a window where a racing `--resume` could acquire on the
unlocked replacement. Directory inodes are never replaced — the lock
fd stays valid for the process lifetime.

Defense in depth: the launcher heredoc takes an opportunistic flock
probe before invoking the orchestrator subprocess (fast-path refusal,
saves the cost of spawning a Python process that would just die in
startup). The orchestrator's `State.__init__` flock acquire is the
load-bearing enforcement and catches anything the launcher misses
(manual `python3 leerie.py --resume`, future verbs, debugging).

What this does *not* prevent: cross-host races. The lock is per-host.
This is fine in practice: on Fly each run is pinned to a specific
Machine via `fly-machine.json`, and only that Machine runs the
orchestrator; on local runs the host is the user's workstation. There
is no architectural path today by which two hosts could attach to the
same state directory simultaneously.

Concurrent-spawn race between two `--resume` launches and the
stale-pid contagion: two `--resume` invocations against the same run
(an impatient user resuming a run whose orchestrator is still alive,
a CI retry, etc.) each pass the launcher's fast-path probe — the
probe is `LOCK_EX | LOCK_NB` then immediate `LOCK_UN`, so it tests
and releases — and each launcher spawns a child orchestrator. The
two `State.__init__` calls race in the kernel; the loser (B) gets
`BlockingIOError` and exits `EXIT_LOCKED=75`. The hazard is **not**
the duplicate spawn (that is correctly caught) but its by-product:
the launcher writes `orchestrator.pid` *between* `Popen` and the
child's `State.__init__`. By the time B exits 75, B's pid is already
in the file. The winner A's pid is overwritten with a dead pid —
silently — and every downstream reader of `orchestrator.pid` is now
wrong about A. `leerie --resume`'s in-machine tail watcher prints
a false "orchestrator exited" banner the moment B's pid is checked
with `kill -0`; `leerie --finalize --force`'s liveness gate sees
the same dead pid and would happily patch a `finished_at` onto A's
state mid-run.

The fix is two-sided: the launcher's `_launch_script` polls `Popen`
briefly for `rc=75` (B's flock-loser signal) before writing the
pid file; if the child exited 75 the file is not touched. Readers
do not trust the pid file as the sole liveness oracle — both the
`--resume` tail watcher and `--finalize --force`'s liveness check
cross-check via a `/proc` scan for any process whose argv contains
`orchestrator/leerie.py` AND this run-id. Either anchor catching
the live orchestrator is sufficient to declare "alive." This makes
the pid file advisory rather than authoritative: even a future race
or an unrelated cause that staled the file produces, at worst,
a false-positive REFUSE (operator-visible) rather than silent
corruption.

The earlier proposal to reserve a separate exit code for
`EXIT_LOCKED` (not 75) and teach `decide_teardown` to leave the
machine alone on that code is orthogonal to this fix and remains
deferred — once `orchestrator.pid` is no longer authoritative,
the launcher's `decide_teardown` arms can no longer be misrouted
by the stale-pid path.

### Why merge, not cherry-pick

Subtask branches are integrated into the run branch by merging, not by cherry-picking.
A merge records ancestry, which gives the integrator a real common base for
three-way conflict resolution: far more auto-resolves, and only genuine
conflicts surface. Cherry-pick copies commits without ancestry, so it has a
weaker base and produces more spurious conflicts. Recorded ancestry also makes
re-integration idempotent and the run's history a true audit trail rather than
a set of duplicated commits.

On the success path a subtask branch may contain commits from two distinct
workers: the implementer's code change and any conformer fixes (§9 *Post-work
conformance*) that landed before integration. Both flow through the same
merge — the integrator does not need to know which worker authored which
commit. Conformer commits are conventionally prefixed `conformer:` in their
subject so a reviewer can identify them in `git log`, and the orchestrator
emits a non-blocking warning for any conformer commit that lacks the prefix.

### Conflict resolution is behavioral, not textual

When two subtasks' branches conflict, resolving the conflict to git's
satisfaction is not enough. A textually clean merge can still silently break
the behavior one of the subtasks was validated against.

So conflict resolution is defined behaviorally. The integrator reads the intent
and the success-criteria notes of *every* subtask whose work is part of the
conflicting merge — the incoming subtask and every already-integrated subtask
it collides with — and resolves the merge so that each side's intent is
preserved. Resolving a *semantic* conflict is what the integrator is for;
a purely textual merge can satisfy git while silently breaking the behavior
one side was validated against, and only a worker that understands intent
can avoid that.

The mechanical re-check that *catches* a merge that broke the tree
runs immediately after: once the integrator commits the merge, the
orchestrator scans the integrated worktree for unresolved conflict
markers (`<<<<<<<`). A merge that left markers behind aborts the
run. Per-wave quality stops there: per-subtask quality is the
implementer's confidence gate (§8), and Leerie does not re-run
subtask criteria at the wave boundary — that role belonged to an
earlier wave-level validator that was removed when the criteria file
became informational (§8, §9).

After the *final* wave integrates — once the staging tree contains
every subtask's work — one conformer pass runs on the integrated
tree as a whole. It is the same conformer the per-subtask phase
spawns (§9), pointed at the staging worktree with `DIFF_BASE` set
to the user's working branch (the PR's eventual base) so the diff
under review is what the PR will contain. The pass catches drift
that only manifests once two subtasks co-exist: a lint rule
sensitive to file count, an import collision that compiled cleanly
in isolation, a test fixture two implementers each augmented in
incompatible ways. Its findings are advisory in the same sense as
the per-subtask phase (§9) — the orchestrator never blocks
finalize on them; the residuals surface as warnings on state and
in the PR body, where a human and CI can act on them. The pass is
bounded by the same `conformance_rounds` cap and the same per-run
worker budget; its `claude -p` invocation has no special standing.

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
already integrated on `leerie/runs/<run-id>`. Leerie does not merge
the run branch into the working branch locally — that would duplicate the
same change in two places (a local commit and a PR) and put the working
branch in a state the user did not request. The working branch is the same
ref at the end of a run as it was at the start; the PR is the proposal to
change that.

**Push and PR happen on the host, after the container exits.** The
container's job is the LLM work plus the deterministic integration
of every wave into `leerie/runs/<run-id>`. Once integration is done,
the container exits cleanly and the launcher takes over: it reads
`run.json`'s `finished_at` sentinel, then runs `git push` and
`gh pr create` on the host.

This boundary is load-bearing. The container exists to bound worker
subprocess subtrees (DESIGN §6 *Worker subtree termination*), not to
be a git/gh client. Auth state — gh tokens, SSH agent sockets,
Claude Code's OAuth token in macOS Keychain — lives in host
processes that don't traverse the Lima VM boundary cleanly. In
Bedrock mode (`CLAUDE_CODE_USE_BEDROCK=1` detected in any settings
file), the launcher additionally stages `~/.aws/` read-only so the
AWS SDK credential chain can resolve SSO tokens inside the container;
`awsAuthRefresh` (interactive `aws sso login`) remains a host-side
operation enforced by a preflight check before the container starts. Trying
to bind-mount that state into the container was a leaky workaround
for a structural mismatch: on macOS the SSH agent socket can't cross
the Lima VM boundary, the gh token bind mount catches stale states,
and Claude Code's OAuth token is in Keychain rather than any
mountable file. Moving the network-y phases to the host eliminates
all of that — the host has working auth for git, gh, and ssh
because the user already uses them daily.

**Local runs** hand off through `run.json` on the bind-mounted host
filesystem. The orchestrator writes `finished_at` and exits with status 0;
the launcher reads that field from the bind-mounted path and proceeds with
push + PR. If the container exits non-zero (an unrecoverable error
mid-run), the launcher does not push — nothing changed on disk that the
user didn't already see in the worker logs.

**Remote runs** (Fly.io `--runtime fly`) face the same auth boundary from
the other direction: the run branch and `.leerie/runs/<run-id>/` state live
on the Fly Machine's filesystem, not on the host. The launcher resolves
this with a **stream-back** step before the host-side finalize runs.

The orchestration is structured as a clean-exit EXIT trap
(`decide_teardown` in `scripts/remote/provision.sh`) so the sync and
the destroy stay on the same atomic path — sync gates destroy, and
push + PR sit between them on the host:

1. The orchestrator inside the Machine writes `finished_at` to `run.json`
   and exits 0, exactly as in local mode.
2. The launcher's `decide_teardown` trap fires. On a clean rc
   (`0 | 10 | 75`) it calls `scripts/remote/fetch-branch.sh`, which:
   - discovers the completed run-id by scanning `.leerie/runs/*/run.json` on
     the Machine for a `finished_at`-bearing, unpushed entry;
   - creates a `git bundle` of `leerie/runs/<run-id>` **and all
     `leerie/subtasks/<run-id>/*` branches present on the Machine** and
     pipes it to the host, where `git fetch` materialises the branches in
     the host's local repo;
   - tars `.leerie/runs/<run-id>/` on the Machine and extracts it under
     `$LEERIE_STATE_HOST_DIR/runs/` on the host.
3. With the run dir now on the host, the trap sources
   `scripts/host-finalize.sh` and calls `host_finalize` directly — push
   + `gh pr create` happen inline, with the host's own auth, before
   the trap proceeds.
4. **Only if push succeeds does the trap destroy the machine.** Push
   failure leaves the machine running and prints a recovery banner
   pointing at `leerie --finalize <run-id>`; this mirrors the
   sync-failure recovery path (work is preserved; the user destroys
   manually via `leerie --kill <run-id>` when satisfied).

**Controlled exits write `finished_at` eagerly.** `die()` raises
`SystemExit`; `main()`'s `except SystemExit` handler writes
`finished_at` to both `state.json` and `run.json` (best-effort,
guarded by `st is not None`; `state.json` is additionally guarded by
`st.data.get("task")` to avoid poisoning the host-side file with a
bare `{"finished_at": …}` stub when the handler fires before state
was loaded — e.g. a failed `--resume` against an incomplete
host-side state) before re-raising. It also writes the exit code to
`orchestrator.exit_code` in the run directory so the tail wrapper can
propagate it to `decide_teardown`. Without the exit code file, the
tail wrapper falls back to exit 0 (the pre-exit-code behavior) and
`decide_teardown` takes the clean-exit branch.

The `finished_at` write remains necessary even with exit code
propagation: `fetch_branch` needs `finished_at` to discover the run.
Without this write, every post-setup `die()` (e.g. "unresolved
subtasks") triggers the sync-failure banner. The value is idempotent
on `--resume`: `phase_finalize` overwrites it with the real completion
time if the run succeeds on retry.

**`finished_at` is a discovery sentinel, not a completion signal.**
Because the `except SystemExit` handler stamps `finished_at` on *any*
post-setup `die()` — including a mid-wave "unresolved subtasks" abort
with `completed_waves < len(waves)` — `finished_at` alone does not mean
the run finished its work. A run whose container was OOM-killed mid-wave
(the orphan case above) can end up with `finished_at` set and only 1 of
5 waves integrated. Treating `finished_at` as "done" is what makes
`leerie --list` render such a run `done` and lets a stray finalize push
an incomplete run branch and open a premature PR. **The completion
signal is `completed_waves == len(waves)`** (or the documented
cleared-but-empty terminal state, which sets `waves = []`). `finished_at`
is left overloaded (the die-path stamp is still needed for run
discovery); instead, every consumer that would treat a run as *terminal*
cross-checks wave completion against `state.json` (which alone carries
`completed_waves`/`waves` — `run.json` never does). Three gates enforce
this, all reading the same signal:

1. `_derive_run_status` returns `incomplete` (not `done`) when
   `finished_at` is set but `completed_waves < len(waves)` and the run is
   neither killed nor paused — so `leerie --list` never mislabels a
   crashed run.
2. `phase_finalize`'s entry `die()`s rather than writing the real
   `finished_at` when `completed_waves < len(waves)` — a defensive guard,
   since the normal wave loop only reaches finalize after all waves
   integrate.
3. **`host_finalize` (the load-bearing gate) refuses the push+PR** when
   `state.json` shows `completed_waves < len(waves)` and not
   `no_work_required`. The push is host-side, and *all three* host-side
   push paths — the launcher's auto-finalize block, the `leerie
   --finalize <id>` verb, and Fly's `decide_teardown` — funnel through
   `host_finalize`, so this single gate covers them all. It fails open
   when `state.json` is absent so a legitimately complete run is never
   blocked.

A genuine completion is unaffected: by the time `phase_finalize` and
`host_finalize` run on a real completion, `completed_waves == len(waves)`.

**Recovery when the orchestrator dies before `finished_at`.** An
uncontrolled exit — SIGKILL, OOM, power loss, or any crash that
bypasses the `except SystemExit` handler — before `phase_finalize`
leaves a run that `fetch-branch.sh` cannot discover (its predicate
requires `finished_at` set + `pushed_at` unset). For these cases,
`leerie --finalize <run-id>` SSHes into the machine, verifies the
orchestrator process is dead (via the `orchestrator.pid` file and
`/proc/<pid>/cmdline`), patches `finished_at` into `run.json` along with
audit fields `recovered_at` and `recovered_via="force-finalize"`, and
then falls through to the normal finalize flow. If the orchestrator is
still alive, the non-force path refuses with a message naming the
live pid and suggests `--force`.

**Subtree collection.** When the orchestrator dies mid-wave, subtask
branches (`leerie/subtasks/<run-id>/<sid>`) may have committed work
that was never integrated into the run branch. `--finalize` detects
un-integrated subtask branches on the machine, runs `setup-run.sh` to
ensure the staging worktree exists, and merges each un-integrated
branch via `integrate.sh`. Conflicts are resolved by spawning a
`claude -p` integrator worker — the same prompt, schema, and
verification as the orchestrator's `integrate_wave()`. The machine
has `claude` CLI, the integrator prompt, and seeded auth. Branches
the integrator cannot resolve (design-conflict or failed) are skipped
and reported. The collection step runs after the `finished_at` patch
and before `fetch_branch` streams the result to the host.

**Defense in depth: bundle scope vs. subtree collection.**
`fetch-branch.sh` bundles subtask branches alongside the run branch so
that even if integration never ran (crash, OOM, power loss between
subtask completion and integration), the raw work is recoverable on
the host. `collect_subtrees_remote` remains the on-machine
integration mechanism (merging subtask branches into the run branch
before fetch). The two are complementary: collection integrates; the
expanded bundle scope preserves.

**`--force`: stop the orchestrator, then collect.** `leerie --finalize
<run-id> --force` extends the recovery path to runs where the
orchestrator is still alive. It SIGTERMs the orchestrator process
inside the machine (the *process*, not the machine — the machine must
stay running for the subsequent collection and fetch steps), waits for
it to die (polling `/proc`; escalates to SIGKILL after 30 s), then
proceeds with the same subtree-collection and `finished_at`-patch
flow. The orchestrator's SIGTERM handler runs
`_cleanup_on_abnormal_exit(full_purge=False)`, which removes worktrees
but preserves all branches — `setup-run.sh` (idempotent) recreates the
staging worktree, and the collection step integrates from the preserved
branches. This makes the manual "attach, hand-edit
JSON, re-run" recovery procedure (last documented in
`docs/IMPLEMENTATION.md` §6) unnecessary while preserving the audit
trail of recovered runs.

The local-runtime path runs the same `host_finalize` block inline in
the launcher (no trap is needed; the launcher and the pusher are the
same process). Both paths share `scripts/host-finalize.sh`; the
recovery command `leerie --finalize <run-id>` also sources it. Three
call sites, one finalize implementation.

**`no_push`: intent vs mechanism.** The orchestrator inside the Machine
is *always* invoked with `--no-push` because the Fly Machine has no
GitHub auth — it cannot push regardless of user intent. This is a
**mechanism flag**, not the user's preference. The user's actual
launch-time intent is a separate signal: it lives in
`fly-machine.json.host_no_push` on the host (set by `provision.sh` at
machine creation) and is propagated *into* the Machine via a hidden
`--host-no-push true|false` argv flag that the launcher appends to
the orchestrator invocation. The orchestrator gates `pr_writer` and
the `run.json.no_push` it writes on **intent**
(`push_will_happen(no_push, host_no_push)` in `orchestrator/leerie.py`),
not on the mechanism flag. The host's `host_finalize` reads the
intent value from `run.json.no_push` and skips push if the user opted
out at launch. This split is load-bearing: without it, `pr_writer`
never runs on Fly (the mechanism flag silences it) and the LLM-written
PR body is replaced by the deterministic fallback.

The run branch is pushed to `origin` and a pull request is opened
via `gh pr create` against the working branch (the branch
HEAD-at-run-start). The PR title and body are written by an LLM
worker (`pr_writer`, defaults to Sonnet) that runs inside the
container right before it exits — placed there because that is where
`claude -p` is available and where the bind-mounted user repo is
readable. The worker reads the target repo's PR template if one
exists (canonical GitHub locations: `.github/pull_request_template.md`,
`pull_request_template.md`, `docs/pull_request_template.md`, then any
`PULL_REQUEST_TEMPLATE/` directory) and fills it out faithfully —
preserving HTML comments, leaving checklists unticked unless the diff
demonstrably satisfies them, honoring "delete if N/A" markers. When
no template is present, the worker produces a default structure
(Summary / What changed / Why / Run metadata) grounded in the run's
actual commits.

The worker's primary signal is the **commit log**
(`git log --no-merges working_branch..run_branch`) — every implementer
and conformer worker wrote those commit messages as it landed a
subtask, so they already describe the work in domain language. A
diff-stat, dirstat, and sampled hunks from the heaviest-changed files
(capped to keep the prompt within budget) supplement the log.
Subtask titles from the planner are passed through verbatim. The
launcher prepends `leerie: ` to the worker's title so leerie-opened PRs
stay easy to spot in lists.

The worker writes its output to `run.json` (`pr_title`, `pr_body`,
`pr_template_used`) — the same container→host handoff channel the
launcher already uses for `finished_at` / `pushed_at` / `pr_url`.
The host launcher reads those fields with `jq` and passes them to
`gh pr create`. This is a **fail-open** path: any failure (worker
error, schema mismatch, timeout, worker budget exhausted, oversized
payload) is logged and swallowed; the launcher falls back to a
deterministic body composed from `state.json` fields (the shape
documented by `compose_pr_body` in `orchestrator/leerie.py`).
Generating a richer body must never block finalize success.

When the target repo has multiple templates inside a
`PULL_REQUEST_TEMPLATE/` directory, the alphabetically first `.md`
wins by default. `--pr-template <name>` (also `LEERIE_PR_TEMPLATE`,
`pr_template` in `leerie.toml`) overrides the choice. A selector that
does not match an existing template is **not fatal** — leerie logs a
warning and falls back to the alphabetical default rather than
blocking finalize over a cosmetic preference.

Two flags control push and PR independently of body composition:

- `--no-push` skips both the push and the PR; the run completes with the
  run branch local-only. The user can inspect, push, or open a PR manually
  whenever they choose.
- `--no-verify` passes `--no-verify` to `git push`, skipping pre-push hooks.
  Worker commits inside worktrees continue to run all hooks normally — only
  the push gate is affected. This is the per-invocation explicit user
  override called out by the project's "never skip hooks unless asked"
  principle; defaults to off.

**Push and PR are honest about failure.** A push or PR step that fails does
not pretend the run failed: the local work is intact and reachable on the
run branch. The orchestrator records what was attempted and what failed in
a per-run sidecar (`run.json` — `pushed_at`, `push_error`, `pr_url`,
`pr_error`). Push failure exits non-zero with a multi-line message that
names the run branch (where the work lives) and the working branch
(unchanged from run start, but the intended PR base), shows the captured
stderr, and gives the exact retry command. PR-creation failure is treated
as non-fatal: the push has already succeeded, so the user receives a
warning with the GitHub URL of the pushed branch and the exact `gh pr
create` command to retry. The principle is that the user always knows
exactly what state things are in and exactly which branch holds the work
to be resolved.

**`pushed_at` gates re-finalize by branch position, not by mere presence.**
A re-invoked finalize (`leerie --finalize`, a second launcher pass, or
Fly's `decide_teardown`) must be idempotent, so a run whose `pushed_at` is
already set is normally a no-op. But `pushed_at` records *that* a push
happened, not *what* it pushed: a finalize that fired while the run was
still integrating (e.g. a mid-wave `die()` stamped `finished_at` early,
before the completion gate existed) can leave `pushed_at` set on a
**partial** branch — and the completion gate only refuses the *first*
premature push, not a later correct one. So the short-circuit compares the
local run-branch tip against the pushed origin tip: **equal tips → no-op**
(the common case, including every fully-pushed chain wave); **origin a
strict ancestor of the local tip (or origin absent) → fast-forward
re-push + re-open the PR**, gated behind the same completion check
(`completed_waves == len(waves)`, which itself *fails open* on a
missing/unreadable `state.json` — see gate #3 above — so the re-push is
gated only when that signal is available). A *diverged* origin (has
commits the local branch lacks) is **not** treated as a partial push: it
keeps the idempotent short-circuit rather than attempting a push that
could not fast-forward. This keeps a partial-push from permanently
wedging a run while preserving idempotency and the chain wave-skip signal
(which reads the `pushed_at` field, still set, not the tip). It does not
weaken the run.json invariants: a re-push keeps `pushed_at` set and, on
success, sets `pr_url` — `pr_url ⇒ pushed_at` still holds.

**Why push by default.** When leerie is invoked in CI or any unattended
context, a successful run that leaves work only on a local branch is a
silent failure mode — the work exists but the user has no signal that it
needs to be reviewed. Defaulting to push + PR turns every run into a
reviewable artifact. `--no-push` exists for users running leerie offline
or in repositories without a GitHub remote.

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

### Cleanup on abnormal exit

A run can end abnormally four ways: the user hits Ctrl-C, an external
process sends a signal (SIGTERM/SIGHUP from CI, systemd, a terminal
close), an unhandled exception fires, or the Claude Code subscription
rate-limit / session-limit is hit mid-worker. In each case the
orchestrator runs a cleanup pass before exiting, and the cleanup
*scope* is uniformly conservative — **state and branches are always
preserved**; only worktrees are torn down. The run is always
resumable via `--resume <id>` after any abnormal exit.

**Worktree-only cleanup, always.** Whether triggered by Ctrl-C,
SIGTERM, SIGHUP, WorkerError, or any other exception:

- Worktrees under `<state-root>/runs/<run-id>/worktrees/` are removed and
  `git worktree prune` clears stale metadata. Worktrees are
  disposable — `scripts/new-worktree.sh` re-creates them idempotently
  on `--resume` from the deterministic branch names.
- State.json, the run branch (`leerie/runs/<run-id>`), and per-subtask
  branches (`leerie/subtasks/<run-id>/*`) all survive. Implementer
  checkpoints under `<state-root>/runs/<run-id>/checkpoints/` survive too,
  so in-flight subtasks resume from where they left off.

**Worker subtree termination — kernel-enforced via the container
boundary.** Cleanup must reach not just the direct `claude -p`
child but every process *it* spawned (test runners, build tools,
dev servers — whatever a `claude -p` worker invoked as a tool
call). Signaling only the leader leaves descendants alive:
Claude Code's Bash tool runs every command via `bash -c "…"` in
its own POSIX session, and `run_in_background: true` deliberately
detaches long-running commands further. PPID chains break by
design, sessions break process-group kill, and reparenting hides
survivors as orphans of init. POSIX gives no in-OS guarantee that
ad-hoc lineage tracking can be made airtight against a tree that
intentionally detaches.

Leerie therefore makes cleanup a **property of the runtime boundary,
not a property of the orchestrator's signal handling**. The
orchestrator and every worker it spawns run inside a single
container (containerd-managed: on Linux native, on macOS via a
Colima-managed Linux VM). When the orchestrator process exits —
for *any* reason, including SIGKILL, segfault, OOM-kill, or power
loss — the container's PID 1 dies and the Linux kernel reaps every
process in the PID namespace via cgroup release. This is the same
guarantee runc, containerd, Kubernetes, and every production
container runtime rely on. There is no possible survivor: a process
that detached into its own session, a daemon that double-forked,
a vitest pool worker that reparented to init — all of them are
inside the namespace and all of them get reaped by the kernel,
not by any code leerie wrote.

The contract reads identically to before — every exit path
(Ctrl-C, SIGTERM, SIGHUP, WorkerError, RateLimitedExit, any
unhandled exception, plus the cases Python can't catch:
SIGKILL and hard crashes) terminates the worker's *entire*
subprocess subtree before resources are returned — but the
mechanism is now load-bearing in a way prompt-level or
heuristic-level cleanup never could be.

The per-worker async cleanup that lives in `claude_p` (the PPID
walk in `_terminate_proc_tree`, the `_DescendantTracker` polling
loop) is *kept* — it is the fast happy path that reaps a single
worker's subtree promptly on clean exit, so the next wave sees a
quiet process table. But it is no longer the abnormal-exit
guarantee. If it half-finishes under Ctrl-C, or fails to escalate
SIGTERM→SIGKILL before asyncio shutdown closes the event loop,
that is no longer a leak — the container boundary catches every
survivor when the orchestrator exits.

**The container boundary's hidden precondition: the orchestrator
must actually exit.** The kernel reaps the PID namespace *when PID 1
dies* — but nothing guarantees PID 1 dies just because the host-side
`nerdctl run` client did. On the local runtime the launcher relies on
`nerdctl run -i` forwarding the host process's terminating signal to
container PID 1; that link is not itself enforced by leerie. The
failure mode observed in practice: a **VM-wide OOM** (two multi-worker
runs sharing one 8 GB Colima VM exhausted all memory) invoked the
kernel's *global* OOM-killer, which — because every in-container
process carries `oom_score_adj:-998` (containerd's default) — spared
the workers and the orchestrator and instead killed the *unprotected
host-session processes*, including the `nerdctl run` clients. The
host CLI died (terminal shows `exit status 255`, finalize is skipped),
but container PID 1 kept running as an **orphan**. Because the
orchestrator is alive, it still holds the run-dir flock (§6 *Single
owner per run dir*), so every subsequent `--resume` correctly loses
the flock race and exits `EXIT_LOCKED=75` — the run is wedged until
the orphan is killed by hand. Three mechanisms close this gap, defense
in depth:

1. **Aggregate memory cap (prevention).** `container-entry.sh` (PID 1)
   writes a container-level cap to the `leerie.slice` cgroup's
   `memory.max` — the parent of every per-worker cgroup — derived from VM
   `MemTotal` read from `/proc/meminfo` (so it is portable across Colima
   and native Linux; the host launcher cannot read the VM's MemTotal on
   macOS, which is why the derivation lives in-container rather than as a
   `nerdctl run --memory` flag). A container that exceeds its cap triggers
   a *cgroup-scoped* OOM (`CONSTRAINT_MEMCG`), which kills a process
   *inside that container* — where the `-998` protection is only relative
   — rather than a global OOM that reaches out to host-session processes.
   This converts "kill the host client → orphan → wedge" into "kill a
   worker in the guilty container → the orchestrator observes a clean
   worker failure." It bounds the *aggregate*; the per-worker cgroup caps
   (below, *Memory
   containment*) bound each worker but not their sum.
2. **Kill-on-exit trap (proactive cleanup).** The launcher installs
   INT/TERM traps on the local run path that `nerdctl kill` the
   container (via its run-id / cidfile) before the launcher exits, so a
   Ctrl-C or SIGTERM to the host CLI tears the container down instead of
   orphaning it. *Limitation, stated honestly:* OOM and SIGKILL deliver
   an uncatchable signal — the trap never runs in exactly the OOM case
   above — so the trap is a complement to, not a substitute for, the
   reaper.
3. **Stale-container reaper (recovery).** Before spawning on the local
   `--resume` path, the launcher checks for an already-`Up` container
   for this run whose owning launcher PID is dead (identified via a
   `leerie.launcher_pid` label set at spawn) and `nerdctl kill`s it
   first. This is the load-bearing fix for the OOM case: it makes
   `--resume` self-heal the orphaned-flock wedge instead of returning
   75, regardless of *how* the orphan was created.

The container boundary holds across both invocation modes:

- **Terminal mode** — the user runs `leerie "task"` from a shell.
  The launcher gives the container a controlling TTY (`-it`). The
  orchestrator's `log()` lines stream live; clarification questions
  use `input()`. Ctrl-C in the user's terminal delivers SIGINT to
  container PID 1.
- **Plugin mode** — Claude Code's Bash tool invokes the launcher
  from inside another Claude Code session (no host TTY). The
  launcher passes `-i` only. Inside the container,
  `sys.stdin.isatty()` returns False, so the orchestrator's existing
  no-TTY clarification path activates: it writes
  `<state-root>/runs/<run-id>/pending-questions.json` (visible on the
  host via the `/leerie-state` bind mount) and exits with
  `EXIT_NEEDS_ANSWERS=10`. The plugin agent reads the file, asks the
  user in chat, writes `<state-root>/answers.json`, and re-runs the
  container with `--answers`.
  Same exit codes, same file passing, same kernel teardown
  guarantee. The container is transparent to the plugin's existing
  exit-10 dance.

See IMPLEMENTATION.md "Container shape" for the launcher's mount
table, image build, per-OS preflight, and the one-line `[ -t 0 ]`
TTY adaptation that selects between the two modes; and
"Concurrency model" for the unchanged in-container worker cleanup
that runs as the happy path.

**Launcher hang on abnormal container exit (decoupled streaming).**
There is a second way the "PID 1 must exit" precondition is subverted
— not the container failing to die, but the *launcher* failing to
return after it dies. In piped mode (`leerie … | tee log`, the common
`-i` case), the container's stdout is forwarded by Colima's persistent
SSH ControlMaster (`ControlPersist=yes`, kept alive by the
containerd/buildkitd socket forwards). That master holds a *copy* of
the launcher's stdout-pipe write-end for the duration of the run. On a
**clean** container exit the master closes its copy and `tee` receives
EOF. On an **abnormal** exit (PID-1 crash under `set -e`, OOM SIGKILL,
a mid-run `nerdctl kill`), the master **retains** the write-end — so
`tee` never receives EOF, the launcher never returns, its EXIT trap
never fires, and the `--rm` container is orphaned `Up` (holding the
run-dir flock, wedging `--resume`). This is why clean runs never hang
and only abnormal exits do. The blast-radius-free fix is *decoupled
streaming*: the launcher points `nerdctl`'s stdout/stderr at a run-log
**file** (the master does not retain a plain-file fd), and streams that
file to its own stdout via a `tail -f` the launcher owns and reaps in
its EXIT/INT/TERM traps. The EOF gate becomes the launcher-controlled
`tail`, never the mux — so a stuck master can no longer wedge the
pipeline, and no concurrent group/chain run is disturbed (contrast
`ssh -O exit`, which frees the pipe but tears down the shared master
mid-stream). Gated to the piped (`-i`, non-TTY-stdout) case; the
interactive `-it` path has a real pty, no `tee`, and thus no hang, and
keeps its stdin attached for `--clarify`. The stale-container reaper on
the next `--resume` (below) remains the backstop for the uncatchable
SIGKILL case where even the trap cannot run.

**Worker subtree termination — Memory containment via cgroup v2.**
The kernel reap above handles *process lifecycle*, not memory: when
a worker's tool subtree (a `pnpm test` spawning vitest pools, a
`tsc --noEmit` building a 1.5-2 GB V8 heap, an `npm run build`
forking webpack workers) overshoots the container's available RAM,
the kernel's OOM killer fires inside the container's single memcg.
The victim it picks is whichever process in that memcg has the
largest `oom_score`, which can land on `sshd` / `lima-guestagent` /
the leerie orchestrator itself — collapsing infrastructure that the
launcher relies on for SSH-tunnel survival to the macOS host. The
observed pattern on an undersized 11 GiB Colima VM with 4
concurrent implementers was: vitest worker (1.85 GiB anon-rss) →
OOM-killer fires → `agetty` → `journald` → `sshd` (Mac launcher
sees `exit status 255`) → `lima-guestagent` → only then the
offending Node process. Leerie's own RSS sat at 36.8 MiB throughout,
so the orchestrator wasn't leaking; the cascade was caused by all
processes sharing one memcg.

Each `claude -p` worker is therefore enrolled in its own child
cgroup at `<cgroup-root>/leerie-w-<sid>/` with `memory.max` set to
`caps["worker_memory_max_bytes"]` (default: VM RAM split across
`max_parallel + 1` slots, floored at 8 GiB) and `pids.max` set
to `caps["worker_pids_max"]` (default 1024, overridable per-repo via
`--worker-pids-max` / `LEERIE_WORKER_PIDS_MAX` / `worker_pids_max` in
leerie.toml). When the worker
subtree blows past `memory.max`, the kernel OOM-kills *inside that
cgroup*; sibling workers, the orchestrator, and host-side services
in different cgroups are not eligible victims. `memory.swap.max=0`
prevents the kernel from delaying an inevitable OOM by paging out
worker memory to the Colima swap file.

The per-worker cap must hold **both** the build/test subprocess tree
*and* the resident `claude -p` process at the same time — `claude`
stays alive running the build via Bash and streaming its output, so
it shares the cgroup with whatever it launches. Live in-container
`memory.peak` measurement on a Next.js/Turbopack build: build alone
peaks at 4.16 GiB (identical whether the build's own concurrency is
left at default or pinned to 2 cores — not a parallelism artifact),
and build + resident claude peaks at 5.6–6.3 GiB. An earlier `4 GiB`
clamp on the auto-derived value was *below* that combined peak, so no
VM size could auto-derive a cap sufficient for a build-running
worker — every such worker was cgroup-OOM-killed regardless of host
RAM. The fix floors the auto-derive at 8 GiB (`max(even_split, 8
GiB)`), giving margin over the measured 6.3 GiB peak, and drops the
upper clamp entirely: the real backstop against a runaway per-worker
cap is the *aggregate* `leerie.slice/memory.max` cap set by
`scripts/container-entry.sh` (`MemTotal - max(1 GiB, 12.5%)`), which
bounds the whole worker fleet regardless of any individual worker's
`memory.max`. Because that aggregate cap is the real VM-OOM backstop,
the per-worker floor can be generous without risking host-level
memory exhaustion — but it does mean **build-heavy waves need a lower
`--max-parallel`**: five concurrent 8 GiB-capped workers (40 GiB
aggregate) will not all fit under a ~13.6 GiB slice cap on a 16 GiB
VM, so the slice cap will itself OOM-kill one of them first. Pair a
generous per-worker cap with a reduced `--max-parallel` for waves
expected to run builds, rather than relying on the per-worker cap
alone to bound concurrency.

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

1. **Cross-scope migration is denied.** Moving a task into a cgroup needs
   write on `cgroup.procs` of the destination, the source, AND their
   common ancestor. Workers are born in the root-owned container scope
   (`/system.slice/nerdctl-<id>.scope` locally, the machine scope on Fly);
   migrating them into `leerie.slice` crosses the root cgroup, which the
   leerie user does not own → the enroll write fails with `EACCES`/`EIO`.
2. **Controller limit files stay root-owned.** Even inside a properly
   *delegated* subtree, the kernel keeps the controller interface files
   (`pids.max`, `memory.max`) owned by root — a delegatee may organize
   processes but not set controller limits.

An earlier design chowned `leerie.slice` to the leerie user and had the
orchestrator write the cgroupfs directly. It appeared to work but did
not: the direct-write probe passed while the actual per-worker enroll
silently failed on both runtimes, so every worker ran uncapped.

The fix is a **cgroup broker** (`scripts/cgroup-broker.py`).
`scripts/container-entry.sh` is PID 1 (the Dockerfile intentionally omits
`USER leerie`); *before the privilege drop* it launches the broker at the
identity that owns (or was delegated) the slice — real root in the rootful
case (Colima, Fly), the rootlesskit-mapped host UID in the rootless case
(which owns the systemd-delegated user slice; see *Rootless exception*
below). The broker listens on a Unix socket at
`/run/leerie-cgroup.sock` (world-connectable; every request is
validated). It performs `create` / `enroll` / `destroy` at that owning
identity — the only identities where enrollment and limit-setting work —
and detects the cgroup hierarchy: **v2** (Colima) uses the unified
`leerie.slice/leerie-w-<sid>/{pids,memory}.max`; **v1/hybrid** (observed
on Fly Firecracker VMs, whose unified mount exposes no controllers) uses
the split hierarchies (`/sys/fs/cgroup/pids/leerie.slice/...`,
`/sys/fs/cgroup/memory/leerie.slice/...`). The entrypoint then drops to
the leerie user via `runuser -u leerie --` before exec'ing the
orchestrator (local nerdctl) or sleeping as PID 1 (Fly, where the
orchestrator is started out-of-band by the launcher's ssh-console wrapper
that drops via `Popen(user="leerie")`). The orchestrator's `_cgroup_*`
helpers are thin socket clients of the broker; it never writes cgroupfs
directly. (Rootless containerd has no real root to drop from or broker
as — see *Rootless exception* below for how the same broker still works
there.)

**Fail-closed gate.** Because a silently-uncapped run is what caused the
crash, `enforce_and_record_cgroup_containment` runs once per run just
before the first worker spawns — in `_run_phases`, *after* the resume
short-circuits so an already-completed / no-work resume (which spawns
zero workers — the "host launcher pushes + opens the PR" flow) is not
gated and cannot `die()` spuriously on a containment-incapable host. It
probes the broker end-to-end (a real create+enroll+destroy round-trip —
the true test of the path workers use, unlike the old direct-write probe
that false-passed) and records `{enforced, hierarchy}` in `state.json`
(merged into the already-populated run state). If containment cannot be
enabled (broker down, no usable cgroup hierarchy — neither a v2 unified
mount nor v1 pids+memory controller mounts — or read-only cgroupfs), the
run `die()`s with an actionable message —
**unless** the operator passes `--dangerously-allow-uncapped`
(`LEERIE_DANGEROUSLY_ALLOW_UNCAPPED` / `leerie.toml`), which downgrades
the fatal gate to a loud warning. Persisting the outcome is deliberate:
the crash left no artifact of the silent failure; now it is visible.

**Rootless exception — the systemd-delegated user slice.** Under rootless
containerd (Linux), rootlesskit maps the host UID to container UID 0, so
"root" inside the container IS the unprivileged host user. In this mode
the entrypoint detects rootless via `/proc/self/uid_map` (non-zero
host-start field) and skips both the privilege drop (`runuser`) and the
`/work` chown (which would reassign ownership into the subuid range,
breaking host-side access).

`leerie.slice` is anchored at the cgroup v2 subtree systemd already
delegates to that UID's login session:
`/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/` — not the
top-level `/sys/fs/cgroup`, which is root-owned (mode 0555) and off-limits
to the mapped host UID. `pam_systemd`/logind chown that directory's own
`cgroup.procs`, `cgroup.subtree_control`, and `cgroup.threads` to the real
UID, and any cgroup the UID creates underneath it (via `mkdir`) inherits
that UID's ownership on every interface file the kernel auto-populates,
including `pids.max` and `memory.max` — so both create and limit-setting
work directly.

Cross-scope migration works too, by the same rule that requires write
access to the destination and the nearest common ancestor (not the
source, described above): a worker's `claude -p` process is born wherever
the rootless containerd's own cgroup driver placed the container (a scope
such as `user@<uid>.service/user.slice/nerdctl-<id>.scope`), and since
both that scope and `leerie.slice` descend from `user@<uid>.service` —
which is delegated to the real UID — migrating a worker PID from one into
the other succeeds.

`HOST_UID` (the real host UID rootlesskit mapped container UID 0 to) is
read from the second field of `/proc/self/uid_map`'s first line. The
entrypoint passes the resolved root to the broker via the
`LEERIE_CGROUP_V2_ROOT` environment variable (`scripts/cgroup-broker.py`'s
`V2_ROOT` reads it, defaulting to the literal `/sys/fs/cgroup` for every
other runtime). The broker needs no separate privileged identity here: it
is launched by PID 1 before the privilege drop, at the same
rootlesskit-mapped identity the whole container runs as — exactly the
identity `CGROUP_ROOT` is delegated to.

This relies on systemd + cgroup v2 delegating `pids`/`memory` into the
per-session slice, which is the default on modern systemd hosts. Where
that isn't the case (a non-systemd init, or a host that doesn't delegate
those controllers), the slice-setup writes (`|| true`) and the broker's
write-then-read-back verification in `_detect()` fail silently, and the
fail-closed containment gate stops the run unless the operator passes
`--dangerously-allow-uncapped`.

**User-namespace remap for `--dangerously-skip-permissions`.**
Claude Code rejects `--dangerously-skip-permissions` when
`os.getuid() == 0`. In rootless mode the entrypoint uses
`unshare --user --map-user=<leerie-uid> --map-group=<leerie-gid>` to
remap outer UID 0 (the mapped host user) to inner UID leerie in a
nested user namespace. Bind-mounted host dirs remain owned by us
(outer UID 0 → inner UID leerie), and image dirs at
`/opt/leerie-image/` (outer UID leerie) are traversed via their
mode-755 bits. The orchestrator sees `getuid() == leerie` and Claude
Code accepts `--dangerously-skip-permissions` without any escape hatch.
The OCI default seccomp profile blocks `unshare(CLONE_NEWUSER)` inside
containers, so the launcher passes `--security-opt seccomp=unconfined`
for rootless runs (gated on the `containerd-rootless/child_pid`
sentinel, not on `id -u`, so macOS/Colima runs are unaffected).

Local nerdctl additionally needs the launcher's writable bind-mount —
`--mount type=bind,source=/sys/fs/cgroup,target=/sys/fs/cgroup,
bind-propagation=rshared` in the bash launcher's nerdctl invocation —
so the in-container entrypoint can see the host VM's cgroupfs.
The launcher also passes `--cgroupns=host` so the container shares the
host VM's cgroup namespace. Without this, nerdctl's default private
cgroup namespace (`--cgroupns=private`) combined with `nsdelegate` on
the cgroupfs mount blocks process migration to `cgroup.procs` even for
the broker — the kernel treats the namespace boundary as a delegation
boundary. With `--cgroupns=host`, the container sees its real cgroup
path (e.g., `/system.slice/nerdctl-<id>.scope`) and the broker can
enroll worker PIDs into `leerie.slice/` children. Fly's Firecracker
microVM boots its own kernel with no cgroup namespace boundary, so this
flag only affects the local nerdctl path.

On macOS (Darwin) the launcher sets the mount unconditionally — Colima's
VM always runs rootful containerd with cgroup v2 and shared propagation,
but the macOS host has no `/sys/fs/cgroup` to probe. On native rootful
Linux the launcher adds the same `rshared` mount unconditionally.

Rootless containerd is its own branch, gated on the
`containerd-rootless/child_pid` sentinel, and uses a **plain** bind-mount
with no `bind-propagation` flag: rootlesskit's `--propagation=rslave`
demotes `/sys/fs/cgroup` to a slave mount inside its sandbox, which is
incompatible with `bind-propagation=rshared`. Propagation of new mount
events isn't actually needed here, though — only read/write visibility
into the already-mounted cgroupfs, so the entrypoint can create and
manage cgroups under the delegated user slice described above, and a
plain bind-mount provides exactly that. When cgroup v2 isn't present at
all (older kernel, disabled unified hierarchy) the mount is skipped and
`_cgroup_probe` falls back to uncapped workers (and, absent
`--dangerously-allow-uncapped`, the fail-closed gate stops the run).
Fly's Firecracker microVM exposes cgroupfs directly with no launcher
flag required.

`_cgroup_probe` sends a `probe` request to the broker, which does a real
create+enroll+destroy round-trip of a throwaway cgroup and returns the
detected hierarchy (`v2`/`v1`). This is the true test of the path
workers use — the earlier direct-write probe passed on hosts where the
subsequent non-root enroll actually failed, which is how the containment
silently disappeared. On v2 the broker's teardown uses `cgroup.kill`
(kernel ≥ 5.14) as an atomic kill of any worker-subtree process that
survived the existing `_terminate_proc_tree` proc-walk; on v1 it moves
survivors to the parent then rmdirs. See IMPLEMENTATION.md §"Caps" for
the resolution surface, `scripts/cgroup-broker.py` for the broker, and
the `_cgroup_*` clients in `orchestrator/leerie.py` for the call sites.

**Detecting PID exhaustion — the broker `stat` read-back.** The
`pids.max` cap protects the *host* (a runaway subtree cannot exhaust the
VM's PID table), but it has a failure mode of its own for the *worker*:
once the cap is reached, every subsequent `fork()`/`clone()` in the
subtree returns `EAGAIN`, so **every shell the worker's Bash tool tries
to launch fails** — while in-process tools (`Read`/`Grep`/`Glob`) keep
working. Observed live: a worker leaked `run_in_background` subprocesses
(reparented to init, so they escape the descendant walk and are reaped
either by the mid-run pressure-gated reaper below or, failing that, at
worker *exit*), saturated `pids.max`, and then spent the rest of the run
in a spiral where even `echo`/`true`/`pwd` returned a bare "Exit code 1".
The worker cannot diagnose this — the CLI surfaces only a generic tool
error, and the kernel's `EAGAIN` string usually does not survive into the
tool-result text for trivial commands — so it mis-attributes the failure
and burns its whole turn budget without recovering.

Detection is a backstop, not a substitute for a cap sized to the
workload. There is no reliable way to *detect and refuse* "a full test
suite run" (the command space — `pytest`, `make test`, `tox`, wrapper
scripts — is open-ended, and a misfiring guard is worse than none), so
the cap value itself is the enforcement surface for *legitimate* load:
the default is set generous enough (1024) to admit a real conformance
run, and it is overridable per-repo for suites heavier still. A runaway
fork-bomb (thousands of PIDs) still trips the cap.

How generous 1024 is, is a measured question rather than a guessed one.
Leerie's own suite — the canonical heavy example, 3762 tests across 251
modules, 117 of them fanning out into stubbed-binary subprocesses — peaks
at **33** concurrent PIDs (median 7, P99 29, sampled at 20 Hz against the
kernel's own `pids.current` in the release image). So the cap sits ~30×
above the workload it was sized for, and a worker approaching it is
almost never doing legitimate work: it is leaking. That measurement is
what lets the mid-run reaper act on pressure at all — see *Mid-run PID
reaping* below, whose critical tier closes the burst case this paragraph
used to call uncatchable.

Per §12 (*prompts are advisory, code enforces*), the orchestrator detects
this mechanically rather than leaving it to the model. The broker gains a
read-only `stat <sid>` verb returning the worker cgroup's
`pids.current` / `pids.max` and the `pids.events` `max` counter (the
kernel increments the latter once per denied fork — the definitive,
unambiguous signal, distinct from a memory OOM which instead bumps
`memory.events`). When enough of a worker's *recent tool-results* are
errors, `_read_stream` probes the broker; if the cgroup is at its PID cap
(or the `pids.events` `max` counter is climbing), the orchestrator logs
the real cause, relabels the inline summary, and terminates the worker
early via the existing abnormal-exit path (`_terminate_proc_tree` + the
`_DescendantTracker` reap) with a `WorkerError`. That routes through the
callers' existing handling: an implementer's PID-exhausted run becomes a
retryable `incomplete-handoff` (a fresh worker restarts in a new worktree
with a clean PID table, and the dead worker's leaked PIDs die with it),
and a conformer's stays advisory (§9).

**Detecting memory OOM — naming the cause instead of a cryptic checkpoint
error.** A build/test command that overshoots the worker cgroup's
`memory.max` is killed by the kernel with a bare `Killed` (exit 137, no
error text of its own) — unlike PID exhaustion, this leaves no failing
tool-result for `_read_stream`'s window detector to key on: `claude -p`
is often reaped mid-turn, before it can emit any `result` event at all.
That symptom lands in `_invoke`'s no-envelope path indistinguishable from
a session-limit no-op or a `--max-turns` exhaustion; downstream,
`validate_result` tags it `empty_handoff`, and once a run with no
committed work burns the retry cap the operator sees only *"checkpoint
... does not exist on disk"* — no mention of memory. On a real run this
drove an operator through a default → 6G → 12G → 16G
`LEERIE_WORKER_MEMORY_MAX` escalation before finding the actual cause.

The broker's `stat <sid>` verb (extended alongside the PID-exhaustion
counters above) also returns `memory.events`' `oom_kill` counter — the
kernel increments it once per OOM-kill inside the cgroup, mirroring
`pids.events`' `max` counter's role for fork denial. `_cgroup_stat`'s
client widens to a 4-tuple accordingly:
`(pids.current, pids.max, pids.events.max, oom_kill)`. `_invoke` reads
the cgroup's stat once more — in its `finally`, immediately before
`_cgroup_destroy` tears the cgroup down (destroy `rmdir`s it, so this is
the last point a read is possible) — and, in the no-envelope branch, if
`oom_kill > 0` it raises a `WorkerError` naming the cause: the last Bash
command the worker launched (tracked alongside the PID-exhaustion window,
first line only) and the cgroup's `memory.max` cap, with the same
actionable suggestion the operator's escalation ladder already
discovered by hand — *"worker OOM-killed on `<cmd>` (memory.max=N GiB) —
raise `--worker-memory-max` or lower `--max-parallel`."* That message
threads through `run_implementer`'s existing `except WorkerError` handler
into the synthesized `incomplete-handoff` envelope's `summary` field.

`settle_subtask`'s `empty_handoff` handling already branches on whether
the worktree holds committed work (see the rescue above): when it does,
the named-OOM `summary` was already preserved verbatim. The remaining
gap is the no-commits branch, which previously discarded the worker's
`summary` in favor of `validate_result`'s generic checkpoint-missing
`message` before calling `fail()`. That branch now prefers the worker's
own `summary` when present — falling back to the generic message only
when no worker output exists — so a genuinely OOM-killed build is named
even when the retry cap is exhausted and the subtask terminates.

The error signal is measured over a **sliding window of the last N
tool-results**, NOT a run of *consecutive* ones. The stream never places
two tool-results next to each other — a tool-result is always followed by
the model's next assistant turn plus `system`/`rate_limit` events before
the next tool-result — so a "consecutive tool-result" counter can never
exceed one and would never fire. The window counts only tool-result
outcomes (the interleaved assistant/system events are ignored, not treated
as resets); a single ordinary failing test leaves at most one error in the
window and never reaches the threshold, while a PID-exhausted worker —
whose every shell-spawning call fails — fills the window quickly. Even
then the kill only fires when the authoritative cgroup read confirms
exhaustion, so the window merely decides *when to probe*.

**Mid-run PID reaping — reducing the blast radius.** The 0.9.38 window
detector is a backstop: it *catches* a worker that has already saturated
`pids.max`. But the root cause — `run_in_background` subprocesses
reparenting to init and accumulating against the cap throughout the run —
is not addressed by detection alone. A complementary *reducer* layer sits
UNDER the backstop inside `_DescendantTracker._poll_loop`: it probes
`_cgroup_stat` each cycle and, when pressure rises, reaps the safest
killable set before the cap is hit. The load-bearing safety property: **below
the pressure gate, behavior is byte-identical to today — zero mid-run
kills.** Both mechanisms share the same `_cgroup_stat(sid)` call as their
authoritative source.

*Trigger — pressure-gated, not timer-based.* Each `_poll_loop` cycle, the
tracker probes `pids.current / pids.max`. Reaping is armed only when that
ratio reaches or exceeds `_PID_REAP_HIGH_WATER` (0.90). A timer would fire
under zero pressure — pure downside (could kill a live background test),
zero upside. Gating at 90% means the only time any process is killed mid-run
is when the worker is already near EAGAIN death.

*Target — the safest killable set.* From `_seen` (every PID the tracker has
observed), the reaper selects those that are simultaneously (i) still alive,
(ii) reparented to init (`ppid == 1`, i.e. no longer attached to the worker
process chain), and (iii) older than `_PID_REAP_MIN_AGE_SEC` (60 s). PIDs
are killed **oldest-first**, stopping as soon as `pids.current / pids.max`
drops below `_PID_REAP_LOW_WATER` (0.75) — hysteresis so one pass does not
over-kill. Killed PIDs are pruned from `_seen`; the exit-time
`stop_and_reap` path is unchanged.

*Why the age floor is load-bearing.* A background test the worker just
launched and is actively awaiting has also reparented to init (`ppid == 1`),
so `ppid == 1` alone cannot protect it. But it is *young*, so the 60 s floor
does. A leaked, forgotten orphan is *old*. Oldest-first + stop-at-low-water
kills the fewest, oldest PIDs needed to relieve pressure.

*Why a single 60 s floor is not enough — the critical tier.* The floor above
protects young orphans unconditionally, and that is precisely wrong at the
top of the range. A burst of leaked `run_in_background` trees saturates the
cap in **seconds** — faster than the 60 s floor lets any of them become
eligible — so the reaper arms at 90%, finds an empty candidate list, and
watches the worker die. Reaping nothing is not safety; it is a disabled
reducer. This is not hypothetical: it is the measured cause of the wave-2
integrator death in run `879defae` (`pids.current=1024/1024`, fork denials
213), which discarded a correct merge resolution and killed a 13.5-hour run.

Reproduced against the real `_reparented_orphans` in a `--pids-limit 1024`
release-image container: 20 leaked trees, each spawned by a wrapper that
lives ~1 s and then exits (`bash -c 'sleep 300 & sleep 1'`) so the orphans
reparent to init exactly as the real `run_in_background` path leaves them.
All 20 are tracked in `_seen`, and at t=8 s the 60 s floor yields **0**
candidates while a 5 s floor yields all of them. Detection was never the
gap; *eligibility* was.

The wrapper's lifetime is load-bearing in that reproduction, not incidental.
`_DescendantTracker` can only record a PID while the PPID chain is still
intact — a wrapper that detaches instantly (`setsid …`, tried first) is gone
before the 0.5 s poll, so its children never enter `_seen` and no age floor
is reachable. That models a leak the tracker cannot see, which is a
different bug; the production `run_in_background` wrapper does survive long
enough to be caught, which is why run `879defae` reaped 5856 orphans across
20 workers at exit. `_seen` demonstrably populates in production, so the
floor — not the tracking — is what stood between the reaper and the leak.

The resolution is a second tier rather than a lower floor. The cap's own
sizing measurement (see *Detecting PID exhaustion* above: the suite peaks at
33 concurrent PIDs) supplies the discriminator. A worker sitting at 90% of a
1024 cap holds ~920 PIDs to do work that costs 33; there is no legitimate
reading of that state. It is a leak, and the young orphans in it are leaked
too. So at `_PID_REAP_CRITICAL_WATER` (0.90) the floor drops to
`_PID_REAP_CRITICAL_AGE_SEC` (5 s); below that ratio the 60 s floor stands
unchanged. The normal tier keeps its protection; the critical tier trades it
for the worker's life.

*Accepted bounded regression.* Above the 90% gate it is possible that a
live background process is killed — older than 60 s in the normal tier, or
older than 5 s once the critical tier arms. This is strictly better than the
alternative — a guaranteed total-worker-death-then-full-retry from the
detector. The gate is what makes the tradeoff acceptable: the imperfect
reap is only ever attempted when the worker is already near EAGAIN death and
the backstop would otherwise fire regardless. The critical tier does not
widen this regression so much as make it *effective*: without it the reap is
attempted and reaps nothing, which is not safety but a disabled reducer.

*Reducer-under-backstop composition.* Both the mid-run reaper and the 0.9.38
window detector read `_cgroup_stat(sid)` — one authoritative exhaustion
source. If reaping keeps pressure below the cap, the detector's window never
confirms → never fires. If reaping stays too conservative and pressure still
hits the cap, the detector catches it → retry-fresh. The two mechanisms are
cleanly layered; neither duplicates the other's logic.

*Rejected alternative — `cgroup.procs` broker verb.* `cgroup.procs` is a
more precise orphan list (only PIDs actually still in the cgroup), but
reading and killing from it would require a new `list`/`kill` verb on
`scripts/cgroup-broker.py`, widening the single audited root surface that
§12 principle guards. `_seen ∩ (alive, ppid==1, old)` covers the same
population without a new root verb; the `cgroup.procs` path is noted here
as the rejected alternative.

Earlier versions of leerie gave Ctrl-C an explicit "throw this away"
semantic with a full purge of state + branches + run dir. That made
accidental Ctrl-C catastrophic — and it conflated user intent ("stop
this run") with run lifecycle ("nuke the artifacts"). The two are
now separate: Ctrl-C stops; `scripts/cleanup.sh --run-id <id>
--branches` is the explicit full-purge gesture.

**Zombie reaping — the container PID 1 is not an init.** The mid-run reaper
above relieves pressure from *live* leaked processes. A second, distinct
population also counts against a worker cgroup's `pids.max`: **zombies**
(`<defunct>` tasks — dead processes not yet `wait()`ed). These arise because
the leerie container's PID 1 is **not a reaping init**: on the local path PID 1
is the entrypoint's final `exec runuser -u leerie -- … python3 leerie.py` (so
PID 1 is `runuser`, with the orchestrator as its child); on Fly, PID 1 is the
idle `sleep infinity` and the orchestrator is a detached `Popen` grandchild. A
worker's tool subtree routinely orphans short-lived subprocesses — notably
`git` and the leerie-private `ssh-agent` (`_leerie_fly_agent_ensure`
daemonizes one), plus their children. When those orphan, they reparent to PID 1,
which — being `runuser`/`sleep`, not `init` — never `wait()`s them, so they
persist as zombies. Each zombie still occupies a task slot counted by the
cgroup, so they accumulate monotonically until `pids.max` fills and every
`fork()` in the subtree returns `EAGAIN`. This was observed live: a worker
running its repo's own test suite accumulated **453 `<defunct>` git** tasks
(all ppid==1) and wedged at the cap. Crucially, the mid-run reaper cannot help
here — a zombie is already dead; SIGKILL is a no-op on it, and only `wait()`
clears it.

The fix routes those orphans to the orchestrator and reaps them there.
`_become_subreaper()` (called once early in `main()`, before any worker spawns)
issues `prctl(PR_SET_CHILD_SUBREAPER)` so orphaned descendants in the
orchestrator's subtree reparent to **it** instead of climbing to PID 1;
`_zombie_reaper()` — a background asyncio task with the same lifecycle as
`_memory_sampler` (spawned in `orchestrate()`, cancelled in its `finally`) —
reaps them roughly once a second, keeping `pids.current` flat (live processes
only) instead of climbing. `prctl` is Linux-only and a logged no-op elsewhere
(the orchestrator only runs for real inside the Linux container).

**A second route to the same 255: an inherited `SIGCHLD=SIG_IGN`.** The
disposition survives `exec`, so a parent the orchestrator does not control (an
SSH daemon, a login shell, the Fly launch wrapper) can hand it down. Under
`SIG_IGN` the *kernel* reaps exiting children itself, so their status is gone
before asyncio can read it — `PidfdChildWatcher` then `waitpid`s a pid that no
longer exists, catches `ChildProcessError`, and reports returncode 255 with
empty output (it can also surface as a raised `ProcessLookupError`). Only the
**first** subprocess is corrupted, which makes the symptom maximally
misleading: whichever check happens to run first reports a bogus failure and
every later one succeeds, so the blame lands on that check's subject rather
than on the machinery. `main()` therefore calls `_restore_sigchld_default()`
before anything spawns, and `preflight()` gates on `_sigchld_is_ignored()` —
which reads the kernel's `SigIgn` mask from `/proc/self/status`, since
`signal.getsignal()` does not reliably reflect an *inherited* disposition.
This is one of **two** independent routes to a fabricated 255; the reaper race
below is the other, and `_restore_sigchld_default()` does nothing to prevent it.

**The reaper must reap only what it created — an allowlist, never a `/proc`
scan.** An earlier design had the reaper scan `/proc` for its own **zombie**
children (`state == Z`, `PPid == getpid()`) that were not in
`_ASYNCIO_MANAGED_PIDS`, on the theory that the exclusion set distinguished a
true orphan from an asyncio child briefly awaiting its watcher. **Measurement
disproved this.** CPython's `PidfdChildWatcher.add_child_handler` calls
`os.pidfd_open(pid)` *after* the fork, and `_do_wait` calls `os.waitpid(pid, 0)`
later still, reporting `returncode = 255` when the status is gone ("may happen
if `waitpid()` is called elsewhere" — `asyncio/unix_events.py`). Between the
`fork()` and `pidfd_open()` the child's PID exists in **no registry the reaper
can consult** — not `_ASYNCIO_MANAGED_PIDS`, not the watcher's tables — so *no*
exclusion, peek, or ordering trick in a scanning reaper can be correct. Reaping
a stranger that is really an asyncio child in that window is indistinguishable,
by construction, from reaping a true orphan.

This was not theoretical: with the real `preflight()` and the real reaper on a
Fly `performance-16x`, the reaper `waitpid`'d the exact PID asyncio had spawned
for `git config user.email` — **40/40 runs**, every one dying with a fabricated
255 that `preflight` misreported as "git user.email is not configured" on a
machine whose identity was correctly seeded. `preflight`'s first `run_proc` is
the first subprocess after the reaper starts, so the reaper's first tick lands
on it: the failure is deterministic, not a rare race. Measured alternatives all
failed — a `waitid(WNOWAIT)` peek made it *worse* (the peek is pure overhead;
the race is the window, not the consume), and excluding asyncio-known PIDs still
corrupted 212/300.

The reaper therefore reaps **only PIDs leerie itself recorded** (`_REAPABLE_PIDS`,
published by `_DescendantTracker` — the worker subtrees leerie spawns and already
tracks). Correctness is by construction: a PID in its fork→`pidfd_open` window
was never added, so it can never be taken. Measured 0/300 on the arm where the
scanning design failed 246/300, while still reaping real orphans. The trade-off
is deliberate: an orphan leerie never observed is not reaped, and the cgroup PID
cap plus the container boundary remain the real backstop. Because the subreaper
reparents orphans to the
orchestrator rather than PID 1, the mid-run `_reparented_orphans` filter accepts
`ppid in (1, getpid())`; exit-time `stop_and_reap` is unaffected (it SIGKILLs by
PID with no ppid filter). This is chosen over inserting a real init (e.g.
`nerdctl run --init` / tini as PID 1) because the subreaper (a) covers **both**
the local and Fly runtimes — `--init` is a nerdctl-local flag and would leave
the Fly path leaking — and (b) is purely additive in-process, changing neither
the entrypoint/cgroup setup nor the "PID-1 death reaps the namespace" teardown
contract above (the orchestrator remains a non-PID-1 process; PID-1 identity is
unchanged). The complementary test-side leak — `_leerie_fly_agent_ensure`'s
daemonized `ssh-agent` outliving its test — is fixed at its source in
`tests/test_require_fly_ssh_isolation.py`'s teardown; on real Fly runs the
in-container agent is now reaped by the subreaper when its run ends.

**Rate-limited (RateLimitedExit) → auto-resume after the reset
window.** When `claude -p` reports the subscription session-limit
hit (delivered as assistant-text content in the verbatim format
`"You've hit your session limit · resets <time> (<tz>)"`, or as a
`rate_limit_event` whose `status` field reports a terminal value
— anything outside the known-allowed set
`{"allowed", "allowed_warning"}`), leerie raises
`RateLimitedExit(reset_at, raw)`.

There is a second, subtler trigger: an **out-of-credits mid-stream
kill**. When credits actually run out, a `rate_limit_event` arrives
carrying `overageDisabledReason:"out_of_credits"` (or `out_of_overage`)
— observed with `status:"allowed"` — and the gateway terminates the
`claude -p` process the moment credits run out, often mid-turn, before
a `result` event is emitted. `_invoke` latches this **exhaustion**
state as the stream flows and, in its no-result-envelope branch, raises
`RateLimitedExit(reset_at=None, out_of_credits=True, raw)` *only* when
the stream truncated with no `result` event **and** an exhaustion
reason was seen. Without this, the truncated stream surfaces as a bare
`WorkerError`, which bypasses the auth/quota backoff (that classifier
needs a result envelope to inspect) and `die()`s the run non-resumably.

The discriminator keys on `overageDisabledReason ∈
{"out_of_credits", "out_of_overage"}`, **not** on
`overageStatus:"rejected"`. That distinction is load-bearing:
`overageStatus:"rejected"` is a *standing config state* for any org
that has extra-usage (overage) disabled — such orgs emit it in
**every** `rate_limit_event` (with `overageDisabledReason:
"org_level_disabled"` and `status:"allowed"`), whether or not the
worker succeeds. Keying the latch on it caused a false positive:
plenty of base subscription quota remained, but any unrelated
mid-stream truncation (a crash, a kill, a reaped subprocess) inherited
the permanently-latched flag and was misreported as out-of-credits. An
`org_level_disabled` truncation is therefore *not* an exhaustion event
— it takes the ordinary `WorkerError` path (retryable / advisory),
never a pause.

The exception propagates through the existing asyncio cancellation
chain — `_invoke`'s `BaseException` guard terminates the in-flight
`claude -p` worker's full subprocess subtree (including detached
backgrounded tool subprocesses) and reaps it, sibling wave-tasks
cancel through the same path — so no orphan subprocesses remain (the
per-worker async cleanup is the fast happy path; the container
PID-namespace teardown is the abnormal-exit guarantee — see "Worker
subtree termination — kernel-enforced via the container boundary"
above). Then:

A **rate-limit** resets on a clock — wait long enough and it clears on its
own — so the session-limit and terminal-`status` cases auto-resume via the
shared `_sleep_then_reexec(st, wait_seconds, reason)`: worktree-only cleanup,
sleep, then `os.execv` the orchestrator (`sys.executable __file__ --resume
--run-id <id>`) into a fresh process. **Out-of-credits does not reset on a
clock** — it clears only when a human tops up or the billing period rolls
over — so it does *not* auto-resume: `main()` does worktree-only cleanup,
logs a `leerie --resume <id>` hint, and exits `EXIT_LOCKED` (75). Looping a
fixed backoff against genuine exhaustion would only spin against the wall,
burn the persisted worker budget on retries that cannot succeed, and delay
surfacing "you're out of credits" to the operator.

For the auto-resume (rate-limit) path: we re-exec the orchestrator, not the
launcher — the orchestrator already runs inside the container with state on
disk; the launcher is not baked into the image and would try to launch a new
container. The `--max-workers` budget persists across the re-exec
(`worker_count` lives in state.json), so a run that repeatedly hits the
rate-limit still respects the cap and can never run away. Cleanup runs before
the sleep, and because
`_cleanup_on_abnormal_exit` removes every worktree — git-registered AND orphaned
dirs, then `git worktree prune` — the re-exec'd `--resume` finds a clean slate
(`setup-run.sh`'s staging-worktree re-creation can't hit a stale-dir conflict).
Ctrl-C during the sleep drops to a manual `--resume` (exit 130); a
SIGTERM/SIGHUP during the sleep drops to a manual `--resume` with the
signal's exit code (128 + signum → 143 / 129), matching main()'s
top-level signal arm; and the should-never-happen case of `os.execv`
itself failing exits `EXIT_LOCKED` (75, EX_TEMPFAIL). In every one of
these the worktree cleanup has already run, so state and the run branch
are intact for the manual `--resume`.

- If `reset_at` parsed cleanly from the literal message format, `wait_seconds`
  is the time until that moment + a small margin.
- If the reset clause didn't parse (malformed time, unknown timezone, format
  change), `wait_seconds` is a fixed `RATE_LIMIT_RETRY_BACKOFF_SEC` (300 s). We
  can't know when the limit refreshes, so we poll: sleep the fixed interval and
  re-resume. A premature retry (still limited) just re-hits the same clean pause
  and sleeps again — cheap, and bounded by the persisted worker budget.
- If the exit is an **out-of-credits mid-stream kill**
  (`out_of_credits=True`), there is no auto-resume at all: it does not reset on
  a clock, so `main()` cleans up worktrees, logs a `leerie --resume <id>` hint,
  and exits `EXIT_LOCKED`. The operator adds credits, then resumes.

Rationale for the fixed-backoff auto-resume on rate-limits (vs. the earlier
"parse failure → exit 75 manual resume" behavior): the old concern was that a
*wrong-time* sleep is worse than no sleep. With a fixed backoff there is no time
being guessed — the trade is "retry in 5 min" vs "die and require a human," and
an early retry is a harmless
no-op re-pause. (Out-of-credits is deliberately excluded from this reasoning: it
has no reset at all, so no interval is ever "right" — auto-resuming it would
spin against the wall until a human intervenes anyway. It pauses-and-surfaces
instead; see the `out_of_credits=True` bullet above.)

`_cleanup_on_abnormal_exit(st, full_purge=False)` is the single
helper for all four paths. The classification happens in `main()`'s
try/except: SIGINT raises Python's default `KeyboardInterrupt`;
SIGTERM and SIGHUP raise the dedicated `InterruptedBySignal`
exception via handlers installed at program start; `RateLimitedExit`
is raised inside the stream handler when the rate-limit message is
detected. SIGINT and SIGHUP are POSIX-only — guarded with
`hasattr(signal, ...)` so the orchestrator still runs on Windows
(degraded: only SIGTERM-equivalent termination works).

A `die()` call (the documented clean-exit mechanism for known failure
modes) is *not* an abnormal exit. The user already got an actionable
error message; running a worktree cleanup pass is correct (the run was
mid-flight) but it is silent unless there were worktrees to clean.

**Detached orchestrator (remote mode).** In local mode the orchestrator
is PID 1 of the container — its lifetime *is* the run's lifetime, and
the user's terminal owns that container directly via `nerdctl run`. In
remote mode the same coupling would be a mistake. The actual work of a
remote run (LLM calls, worker subprocesses, git, shell tools) happens
entirely inside the Fly Machine; the launcher's host-side role after
provisioning is purely **to stream the orchestrator log back for the
user's eyes**. Binding the orchestrator's *life* to that streaming
channel — which is what a foreground `flyctl ssh console -C "python3
leerie.py"` would do — means a closed laptop, a dropped WiFi connection,
or an accidental Ctrl-C kills a run that the laptop wasn't doing any
work for.

Leerie therefore starts the orchestrator **detached** on the Fly Machine.
The launcher pipes a small Python wrapper script via stdin to
`flyctl ssh console --pty=false -C "python3 -"`; the wrapper does
`subprocess.Popen(..., start_new_session=True, user="leerie",
group=<leerie gid>, env={HOME=/home/leerie, USER=leerie, PATH=mise+bin},
cwd="/work")` and records the resulting PID in `orchestrator.pid`.
`start_new_session=True` is the portable equivalent of
`setsid nohup`; running as the leerie user with explicit env is
required because the ssh-console session lands as root with
`HOME=/root` by default (claude would look for credentials in the
wrong place otherwise). The ssh-console call returns immediately. A
*second* ssh-console call then pipes a tail-wrapper script to
`sh -s` purely for the user's terminal. The orchestrator is session
leader inside the machine; the tail is an independent process on the
host side. Stream death (Ctrl-C, broken pipe, laptop closing, WiFi
dropping) breaks the tail, not the orchestrator.

This matches the prior-art mental model from comparable tools
(`fly machine` itself, Claude Code's `/bg` + `claude agents`, kubectl,
tmux): **sessions are the unit of management, not terminals**. Leerie's
session is the run; the local terminal is just one of many ways to
observe it.

The run-id is the bridge. With detached invocation the launcher needs
the run-id *before* starting the orchestrator (so it knows which
`orchestrator.log` path to tail), but today's orchestrator generates
its run-id internally during phase 1. The launcher therefore generates
the slug + suffix host-side using the same pattern and passes it as
`--run-id <id>` — reusing the plumbing that `--resume` already
establishes. The orchestrator's `--run-id` short-circuit accepts the
explicit value and skips auto-generation.

**Remote pause-on-failure (Fly.io).** Local mode reaps the container's
PID namespace on every exit (success or failure) because the host
filesystem holds the durable record. Remote mode has the same durable
record (the run branch and `<state-root>/runs/<run-id>/`, both of which the
stream-back finalize already understands) but the Fly Machine is *not*
free — keeping it alive after failure has a real per-second cost, and
destroying it after failure throws away the in-machine filesystem state
that is useful for diagnosis (orchestrator logs, partial worktrees,
recently-edited files that haven't yet been committed to a per-subtask
branch).

The compromise: classify the orchestrator's exit code on the host side
and route to either *stop* (preserves volume, frees compute), *destroy*
(full reap — machine **and** its volume; see *Volume lifecycle* below,
since Fly reaps neither for us), or *leave alone* (the user merely
detached the local stream — the orchestrator on the machine is still
working). With the
detached orchestrator above, the classification is no longer "how did
the orchestrator's run exit?" but "what just happened on the host
side?" — because the launcher process now exits when the *tail*
finishes, not when the orchestrator finishes. The reclassified table:

| Exit | Meaning | Disposition |
|---|---|---|
| `0` | tail saw orchestrator exit cleanly (or could not read exit code) | destroy after stream-back |
| `EXIT_NEEDS_ANSWERS=10` | clarification (plugin re-runs) | destroy (nothing to inspect) |
| `75` (EX_TEMPFAIL) | single-owner-per-run-dir refusal (`EXIT_LOCKED`) or a genuine EX_TEMPFAIL worker surface. NOTE: rate-limit / out-of-credits / parse-fail no longer exit 75 — they auto-resume in-process (see *Rate-limited → auto-resume*). | destroy (state in run branch; cheaper to re-provision) |
| `130` / `143` | host-side SIGINT / SIGTERM | **detach: leave machine alone, print reattach hints** |
| any other non-zero | worker/orchestrator failure (`die()`, etc.) | **pause: stop machine, write sidecar, notify** |

The tail wrapper reads the orchestrator's exit code from
`orchestrator.exit_code` (written by the `except SystemExit` handler
in `main()`) and uses it as its own exit code. When the file is absent
(OOM, SIGKILL, or a crash before the handler ran), the wrapper falls
back to exit 0 — the same behavior as the pre-exit-code era — so
uncontrolled exits still route through the clean-exit branch where
`fetch_branch` bundles whatever is on the run branch (and any subtask
branches) before destroying.

The Ctrl-C row is the load-bearing change. Earlier versions of leerie
treated rc=130 as "user cancelled, destroy the machine" — but with the
detach, rc=130 only means "user stopped watching." The orchestrator on
the machine has not been signalled and is still running. Destroying it
on Ctrl-C would be exactly the behavior the detach was introduced to
prevent. The launcher therefore prints a small banner listing the
reattach, pause, and destroy commands and exits without touching the
machine. The user can then come back hours or days later and either
`leerie --resume <run-id> --runtime fly` to watch progress (the
default tails the orchestrator log; `--shell` opens a bash shell
instead), `leerie --stop --runtime fly` to pause cleanly, or
`leerie --kill --runtime fly` to explicitly destroy.

The decision lives in the launcher (`scripts/remote/provision.sh`'s
EXIT trap), not the orchestrator. Per §6 *Worker subtree termination*
the orchestrator stays runtime-agnostic — it always exits with the same
exit codes regardless of where it runs, and the launcher routes those
exit codes through the runtime-appropriate teardown.

`flyctl machine stop` (not destroy) on the pause branch preserves the
machine's filesystem; the orchestrator's own state is already in
`<state-root>/runs/<run-id>/run.json` and the run branch holds the
committed work, and `flyctl machine start` brings the machine back from
disk without losing anything. Memory state is not preserved across a
pause — the contract this section relies on is that the run branch (the
committed work) plus `<state-root>/runs/<run-id>/` (the orchestrator's
own state) are the only durable record of a run, and both already live
on the machine's filesystem by the time a pause fires.

Before `stop_machine`, the pause branch syncs the machine-side
`.leerie/runs/<run-id>/` directory to the host via the same tar-pipe
primitive that `fetch_branch` uses. This is best-effort (bounded by
a 60 s timeout; failure is logged but does not block the pause) and
serves two purposes: it gives the host a copy of `state.json` so a
subsequent `--resume` against an auto-detected Fly run can read the
task and wave state locally, and it surfaces logs and checkpoint
artifacts for offline inspection without restarting the machine.

### Remote disk policy

**Every Fly-path run gets a per-machine volume by default.**
`FLY_VM_DISK_GB` defaults to `8` when `RUNTIME=fly` and the user has
not set it explicitly (CLI / env / toml). `provision.sh` creates the
volume before the machine, mounts it at `/work`, and the
pause-on-failure contract is unconditional — the volume survives
`machine stop` and reattaches on `machine start`, indefinitely. The
mount target is `/work` because that's where the durable workload
lives: the seeded repo, the run-state tree under
`.leerie/runs/<run-id>/`, and the per-subtask worktrees that dominate
the run's disk footprint. The caches under `/home/leerie/.cache/...`
and the auth bundle at `/home/leerie/.claude` are bounded in size and
intentionally left on the rootfs — they are not the durability
contract's concern. `seed_auth` re-runs unconditionally on every
resume, so the auth bundle is refreshed from the host's `$STAGE`
regardless of whether the rootfs survived the pause window. The
workload-vs-cache separation is empirical, not absolute: a heavy
pnpm-store or cargo registry can still hit the rootfs cap on
unusually large monorepos. The historical ENOSPC mode was per-
subtask worktree accumulation, not cache growth, so prioritizing
worktrees on the volume is the right default; runs that
empirically exhaust caches should set `FLY_VM_DISK_GB` higher and
treat the spillover as a rootfs problem to solve separately. The
rootfs's throughput cap (2,000 IOPS / 8 MiB/s) compounds disk
pressure by slowing spillover; the volume accelerates it as a
side-effect since per-machine tiers run 4k–32k IOPS.

**Volume lifecycle — leerie owns the reap, because Fly does not.** A Fly
volume is an independent resource with its own lifetime: *"a Machine can
be destroyed without destroying its volume"*, and what survives is a
documented **"unattached volume"** that keeps accruing per-GB-month
charges indefinitely. There is no platform-side lifecycle hook to lean
on — the Machines API's `auto_destroy` destroys the *machine* when its
work completes, and says nothing about that machine's volumes; `mounts`
has no ephemeral or destroy-on-exit mode. Since `FLY_VM_DISK_GB`
defaults to `8`, **every** Fly run creates a volume, so an unreaped one
is not an edge case — it is the default outcome of any teardown path
that forgets. The obligation is therefore structural: *every* path that
destroys a machine must also destroy its volume, and "the machine is
already gone" is precisely when a known volume still needs reaping — not
a reason to skip it. Gating the reap behind a live-machine check inverts
the requirement, and did: it silently leaked the volume of every run
whose machine died first.

Two platform facts fix the ordering, and they pull in opposite
directions. The volume→machine association lives **only** on Fly (the
volume's `attached_machine_id`, and the machine's own `config.mounts`)
and it vanishes the instant the machine is destroyed — so anything that
needs to *learn* a volume from its machine must do so **before** the
destroy. But Fly refuses to destroy a volume that is still attached
("in use by machine X") — so the reap itself must come **after**. The
reap is thus pinned between the two: look up, destroy machine, destroy
volume. (A *stopped* machine keeps its attachment, which is what makes
the pause/resume contract above compatible with this: a paused run's
volume is still owned, still attached, and must not be reaped.)

One residue is irreducible and deliberately unsolved. If a machine is
destroyed **and** the launcher dies before reaping the volume (SIGKILL,
crash), the association is gone from both sides — Fly no longer links
them, and the host sidecar may never have recorded it. Such a volume
cannot be attributed to its run by any mechanism, since volume names are
random and encode nothing. leerie does not guess: there is no
reconciliation sweep, because "unattached" alone cannot distinguish a
true orphan from a volume whose machine is seconds away from attaching.
The operator reaps these by hand (`flyctl volumes list` + `destroy`),
and the code says so where it gives up.

Six sidecar fields on `run.json` capture remote lifecycle state:

- `fly_machine_id` — written by `provision.sh` immediately after
  `flyctl machine run` succeeds, so a launcher that crashes before
  classifying still leaves a recoverable pointer to the machine.
- `paused_at` — ISO timestamp written either by the EXIT trap on the
  pause-on-failure branch or by an explicit `leerie --stop <run-id>`.
- `pause_reason` — short tag (`worker-error`, `orchestrator-exception`,
  `finalize-failed`, `user-requested`).
- `killed_at` — ISO timestamp written by an explicit
  `leerie --kill <run-id>`. Marks the run as terminated by user request;
  the machine has been destroyed and the run is no longer resumable.
- `sync_failed_at` — ISO timestamp written when the clean-exit branch
  of `decide_teardown` ran `fetch_branch` and it failed. The machine
  is left RUNNING (not stopped — see below); the user recovers by
  running `leerie --finalize <id> --runtime fly` (retry sync + push)
  or `leerie --kill <id> --runtime fly` (destroy after manually
  salvaging work).
- `sync_fail_reason` — short tag accompanying `sync_failed_at`
  (`sync-failed-on-clean-exit`).

These fields live on `run.json`. Since the run_id is the machine ID
(known at provision time), the run directory is created immediately
after `flyctl machine run` succeeds. `provision.sh` writes
`fly-machine.json` to the run directory as a crash-recovery pointer;
`run.json` is written later by the orchestrator. Every verb that acts
on a known run-id (`--stop`, `--kill`, `--finalize`, `--resume`) can
use the run_id directly as the machine ID — no lookup needed.

`paused_at`, `pushed_at`, and `killed_at` are mutually exclusive — a
run cannot be in more than one terminal-or-paused state.
`sync_failed_at` is orthogonal (the machine is neither paused nor
destroyed; it's running with unsynced work) but mutex-checked
against `pushed_at` (a pushed run cannot be sync-failed) and
`killed_at` (a destroyed machine cannot be sync-failed). The
orchestrator's `_validate_run_json` enforces all invariants.

**Sync-before-destroy (load-bearing — the "never lose work"
contract).** The clean-exit branch (rc=0/10/75) does NOT destroy
the machine first and hope the user runs `leerie --finalize` later.
That ordering is wrong: the orchestrator's committed work and the
`.leerie/runs/<id>/` state directory live ONLY on the machine until
they are streamed back. Destroying the machine while the user has
no host-side copy throws the work away unrecoverably.

Instead, `decide_teardown` sources `fetch-branch.sh` and runs
`fetch_branch` (git bundle of the run branch + tar of
`.leerie/runs/<id>/`) BEFORE calling `destroy_machine`. Only on
confirmed sync success does it destroy. On any sync failure
(network blip, bundle creation failure, etc.), the machine is
LEFT RUNNING — not stopped — and a multi-line WARNING points the
user at three recovery commands:

  1. `leerie --finalize <run-id> --runtime fly`  (retry sync + push)
  2. `leerie --resume <run-id> --runtime fly`    (manual inspection —
                                  attaches to the live orchestrator's
                                  log, or drops into a shell with `--shell`)
  3. `leerie --kill <run-id> --runtime fly`      (destroy AFTER user
                                  confirms work is safely on host)

The user owns the machine in this state. leerie does NOT auto-
destroy after a successful manual finalize either — the user must
explicitly `--kill`. The reclassified table (line 893+) already
documented the "destroy *after* stream-back" intent; this is the
mechanism that enforces it.

**The user-visible verb surface.** Four explicit verbs cover the
remote run lifecycle, each doing exactly one thing:

| Verb | Effect |
|---|---|
| `leerie "task" --runtime fly` | Provision machine, detach orchestrator, tail log |
| `leerie --stop <run-id>` | Clean pause (`flyctl machine stop`); resumable |
| `leerie --resume <id>` | Smart resume — wakes a paused machine, attaches to a live orchestrator, or relaunches against an alive-but-orphaned machine, automatically |
| `leerie --kill <run-id>` | Destroy machine, mark run terminated (irreversible) |

Plus `leerie --list` (unified across local and remote, with `--status
<state>` and `--runtime <local|fly>` filtering as orthogonal axes).
Status describes the run's lifecycle (`paused`, `killed`,
`done`, `sync-failed`, `in-progress`, `done-pushed-pr`, ...); runtime
describes where it ran (`local` or `fly`). `--list --runtime fly`
short-circuits in the launcher and queries Fly directly via `flyctl
machines list --json`, so it surfaces machines launched from any host
repo (not just the cwd).

This separation matches the convention every comparable tool follows:
`fly machine start` / `stop` / `destroy` are distinct verbs;
kubectl's `delete` is distinct from a watched stream ending;
tmux's `kill-session` is distinct from `detach`. Ctrl-C as a
destructive verb was an artifact of the lifetime coupling — once the
coupling is removed, Ctrl-C reduces to its conventional meaning ("stop
this terminal-side activity") and destruction needs its own verb.

**Runtime auto-detection on run-id-bearing verbs.** When `--resume`,
`--stop`, `--kill`, or `--finalize` targets a run whose state
directory contains a `fly-machine.json` sidecar and no explicit
`--runtime` was given, the launcher auto-promotes to `fly` via the
shared `_auto_detect_fly_runtime` helper. When no `fly-machine.json`
exists, `--stop` and `--kill` probe for a live local nerdctl container
via `_is_local_container` (`nerdctl inspect <run-id>`). `--stop` uses
`nerdctl stop` (SIGTERM → grace → SIGKILL) so the orchestrator's
signal handler can save state before exit; `--kill` uses `nerdctl kill`
(immediate SIGKILL) since the run is terminal. `--finalize` on a local
run is inline (no separate verb needed). If the user explicitly sets
`--runtime local` on a Fly-originated run, `--resume` warns but
respects the choice.

**Smart resume in remote mode.** `--resume` is the single verb for
re-engaging with a remote run, regardless of the run's current state.
The launcher reads observed state and routes to the right behavior:

| Machine state | Orchestrator state | `--resume` behavior |
|---|---|---|
| Stopped (paused) | n/a | Wake machine → re-seed → launch orchestrator → tail |
| Running | Dead | (Re-)seed if needed → launch orchestrator → tail |
| Running | Alive | Skip seed + launch → tail orchestrator.log |

The "machine running, orchestrator alive" branch is the §6 isolation
boundary's terminal-side surface — not a new privileged channel.
Detection is two-layered. **Early flock probe (resume path only):**
on the `_resumed=true` path, the launcher runs a lightweight flock
probe via `flyctl ssh console` immediately after `resume_machine`
succeeds and *before* `seed_auth`. If the probe detects a held lock
(rc=75), the launcher skips `seed_auth`, `re_seed`, and the launch
wrapper entirely, going straight to the attach path — avoiding ~60 s
of wasted seeding. SSH readiness is not a concern: when the
orchestrator is alive, the machine was never stopped and hallpass is
already warm. If the probe fails for any reason other than rc=75
(SSH not ready, run directory absent), the launcher falls through
silently to `seed_auth`. **Launch-time flock probe
(belt-and-suspenders):** the launcher pipes a launch wrapper through
`flyctl ssh console -C "python3 -"`; the wrapper takes a fast-path
flock probe on the run directory (§6 *Single owner per run dir*)
and exits 75 if the lock is held. This second probe covers fresh
provisions and any race the early probe missed. **flyctl exit-code
limitation:** `flyctl ssh console` does not forward the remote
process's exit code — it returns 1 for any non-zero remote exit.
The actual code appears only in stderr (`Error: ssh shell: Process
exited with status <N>`). The launcher captures stderr and parses
the real remote code via `_extract_flyctl_remote_rc` (lib.sh) so the
rc=75 pivot fires correctly. Both probes pivot to attach behavior
via `_attach_to_live_orchestrator` (lib.sh) instead of launching a
duplicate. The attach channel itself
is `flyctl ssh console` against the run's Fly Machine, proxied
through Fly's hallpass + WireGuard mesh, giving the user a real PTY
at `/work`. Default behavior runs `tail -F` of the orchestrator log
(the canonical way to watch a detached run's progress); `--shell`
opts into the bare bash shell at `/work`. No sshd in the image, no
key management, no public exposure; isolation inherits from the same
WireGuard mesh the launcher already uses for `flyctl machine exec`.

The orchestrator is unaware of attach — it's a launcher-host gesture
(mirrors §6's "container/process isolation is the launcher's
concern"). The same mechanism serves four roles:

1. The "feels-local" interactive terminal — `leerie --resume <run-id>
   --shell` drops a developer at `/work` to inspect what a worker is
   doing.
2. The mid-run attach mechanism — open a session against a running
   machine without disturbing PID 1 (the orchestrator). `flyctl ssh
   console` spawns an independent process; detach signals only the
   SSH session's own children.
3. The failure-inspection surface — the paused-machine state from
   the pause-on-failure path is reachable via exactly the same
   command (`--resume` wakes the machine, then tails). No second
   mechanism is needed.
4. **The detached-run reattach surface** — after a Ctrl-C detach or
   a closed-laptop disconnect, `leerie --resume <run-id>` picks
   up the orchestrator log stream where it left off. The orchestrator
   never noticed; only the local view paused.

State contract: `scripts/remote/provision.sh` writes a PID-keyed
record at `$LEERIE_STATE_HOST_DIR/remote/$$.json` immediately after
provisioning and copies it to
`$LEERIE_STATE_HOST_DIR/runs/<run-id>/fly-machine.json` once the
run-id is known. The pointer is retained after `destroy_machine` so
the chain wave loop's tagging step can read it post-wait; the
`--resume` auto-discovery path filters stale pointers via `kill -0`
(dead-PID records are harmless). `leerie --resume` resolves the
machine via either path. Multiple concurrent remote runs in the same
repo are disambiguated by passing a run-id; with no `--run-id` and a
single active launcher record, `--resume` resolves the run-id from
that record.

Local mode keeps its inline `--resume` behavior by design. Local
runs are synchronous foreground processes (`nerdctl run --rm` with no
backgrounding), so there is no detached container to attach to —
`--resume` just re-execs the orchestrator against `state.json`.

**Shallow seeding for heavy repos.** The fresh-provision seed
(`seed_repo_clone`) delivers the host's committed state as a
`git bundle create - --all` piped to the machine over
`flyctl ssh console`. `--all` packs the full history of every ref.
For a repo with deep history and large committed blobs (vendored
archives, seed-data dumps, media), that bundle can be hundreds of MB
— a single serialized stream over the WireGuard tunnel that can
exceed the `LEERIE_SEED_TIMEOUT_S` cap (default 600 s) and fail the
whole run before any worker starts. The bloat is *history*, not the
working tree: build artifacts and `node_modules` are already excluded
(a bundle carries committed objects only), so the lever is the depth
of committed history, not what's on disk.

Above a size threshold (repo `.git` larger than a bounded default),
the seed switches to a **shallow** transport: the host makes a
throwaway `git clone --depth=N` of the working branch, tars *only its
`.git` directory*, pipes that over the same channel, and the machine
untars it and `git checkout`s the working tree. This ships a fraction
of the bytes (a depth-50 clone of a 960 MB-history repo is ~140 MB
vs. a 420 MB full bundle) while remaining correct on every downstream
path:

- **Why tar-of-`.git`, not a shallow bundle.** `git bundle` refuses a
  shallow repository — a shallow clone's grafted commits have parents
  the pack cannot reach, so `bundle create --all` produces an object
  the receiver cannot clone. Tarring the shallow clone's `.git`
  sidesteps this: it ships the pack (including the `.git/shallow`
  graft boundary) verbatim, and the machine materializes a valid
  shallow repo.
- **Why it stays NFC-safe.** Tarring `.git` preserves the same
  filename-normalization guarantee that motivated bundles: object
  contents (tree entries by raw bytes) never serialize filenames
  through the transport; the receiving Linux git creates working-tree
  filenames natively on `checkout`. (Tarring the *working tree* would
  reintroduce the macOS BSD-tar NFC→NFD bug — so the seed tars `.git`
  only and reconstructs the tree with `checkout`.)
- **Why fetch-back is unaffected.** When a worker commits a run branch
  on the shallow machine repo, `fetch-branch.sh` bundles *that branch
  by name* (not `--all`). The bundle cites the shallow boundary commit
  as a prerequisite; the host — which always holds the full origin
  ancestry — has that commit, so `bundle verify` + `fetch` reassemble
  full history and the graft never leaks host-side.
- **Why the PR diff stays correct.** A real `git clone --depth=N`
  keeps the working branch's true tip hash, so `git merge-base` on the
  host resolves the run branch against the working branch and the PR
  "Files changed" view shows only the worker's changes. (A synthetic
  re-rooting of history would be smaller still but would break the
  merge-base — so the seed uses a genuine shallow clone, never a
  re-root.)

Submodules are orthogonal: a `--depth` clone of the superproject does
not populate `.git/modules`, so the existing per-submodule bundle
machinery (each submodule bundled separately, its URL rewired on the
machine) carries over unchanged.

The cost is that workers on the machine see only depth-N history
(`git log`/`git blame` beyond N is unavailable, and the machine
cannot deepen — it has no origin credentials, by the finalization
model above). The default depth is chosen to preserve useful recent
history rather than a bare depth-1 tip. Depth and the size threshold
are operator-tunable; setting depth to full disables shallow seeding
and restores the full-bundle path. Small repos are unaffected — they
stay on the full bundle, which costs nothing to ship. The shallow path
also falls back to the full bundle when the working branch is a
detached HEAD or has a name outside a conservative shell-safe charset
(the machine-side reconstruction interpolates the branch into a
`git checkout` run over `sh -c`, and the full path — which never names
the branch — is the safe default for the rare exotic branch).

Because `seed_repo_clone` always wipes and repopulates `/work`, this
switch is confined to fresh provisions. Mid-run re-seed (below) never
re-clones and is unchanged. A corollary robustness fix: `--resume`
now probes whether the initial seed actually produced a valid `/work`
git repo; if a prior seed died before completing (leaving no run
state on the machine), resume re-runs the full seed instead of
dead-ending on a dirty-only re-seed against a repo that isn't there.

**Mid-run re-seed (remote mode).** Once a remote run has started, the
host's working tree keeps evolving — the user lands new commits,
saves uncommitted edits, pulls in a new submodule. The remote machine
needs a user-triggered way to pick that up without destroying its
volume. leerie realises this as two surfaces sharing one mechanism:
an explicit `leerie --re-seed <run-id>` subcommand and an implicit
auto-re-seed step inside `leerie --resume <id> --runtime fly`.
Both wake the machine if stopped, run a safety check, and call the
same `seed_repo_dirty` helper used by the fresh-provision path.

Three operations, in order, mirroring the spec's intent ("current
laptop state" = host commits plus host dirty edits):

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

The dirty set is computed on the host where worktree paths
(`.leerie/runs/<run-id>/worktrees/...`) structurally cannot appear,
because worktrees live only on the machine. A defensive filter
excludes `.git/*` and non-whitelisted `.leerie/*` paths before handing
the file list to rsync's `--files-from=-` — protects against a future
change that lets host-side paths name worktree files or surfaces
host-side `.leerie/` run state to the machine. Exception: the three
committed config files (`.leerie/config.toml`, `.leerie/Dockerfile`,
`.leerie/.leerie-setup.sh`) are repo-owned declarations that workers
need on the machine and pass through the filter.

The repo-local `.claude/` directory is force-included in the dirty
set even when `.gitignore` excludes it (the common case). Workers
need the repo's hooks, agents, skills, commands, and settings to
function — and bundles can't carry gitignored content. Architectural
guarantee: every fresh seed and every mid-run re-seed delivers the
host's current `.claude/` to the machine.

Resume auto-re-seeds by default. `--no-re-seed` opts out for the
rate-limit auto-resume case where no host edits happened. The
trust model matches the spec: the user picks the moment (by typing
`--resume`), so the seed is treated as authoritative.

### EC2 runtime lifecycle

`--runtime ec2` is accepted today as a resolvable enum value with a
working credential chain (`scripts/remote/aws-credentials.sh`,
`resolve_aws_region`/`resolve_aws_profile`, the `boto3`/`botocore` pin —
see IMPLEMENTATION.md "Runtime mode" / "AWS region/profile prefs"), but
instance provisioning itself has not shipped. This section is the
canonical architecture that a provisioning subtask must implement
against — the EC2 counterpart to everything above in this section for
Fly. It reuses Fly's stage names and dispositions everywhere the two
platforms agree, and calls out explicitly where EC2's platform
semantics diverge and the design must not copy Fly's rule blindly.

**Stage mapping.** The five Fly stages above (provision → wait-ready →
seed → detached-orchestrate → teardown) carry over one-for-one:

| Stage | Fly (shipped) | EC2 (this design) |
|---|---|---|
| Create | `flyctl machine run` | `ec2:RunInstances` via `boto3` (AMI, instance type, key pair, security group, subnet — the `LEERIE_EC2_*` vars already reserved in IMPLEMENTATION.md's env-forwarding deny-list) |
| Wait-ready | poll `flyctl machine status` for `started`; hallpass warm-up probe | poll `describe_instances` for `state.Name == "running"`, then `instance-status-ok` + `system-status-ok` (`describe_instance_status`) — a `running` EC2 instance is not yet SSH/SSM-reachable, unlike a Fly Machine where `started` and hallpass-warm are close together |
| Seed | `seed_auth` + `seed_repo_clone` over `flyctl ssh console` | same two steps, transport substituted (see below) |
| Orchestrate | detached `Popen` via ssh-console wrapper, PID recorded, host tails via a second ssh-console session | same detached-`Popen` pattern, launched over the substituted transport; run-id-before-orchestrator-start constraint (line 2208) is unchanged — the launcher still generates the run-id host-side before create, since it needs the id to name `orchestrator.log`'s path ahead of the create call completing |
| Teardown | classify exit code → stop / destroy / detach (the reclassified table above) | same table, `flyctl machine stop`/`destroy` → `ec2:StopInstances`/`TerminateInstances` |

The pause-on-failure classification table (exit code → disposition) is
runtime-agnostic by construction (§ above: "the orchestrator ... always
exits with the same exit codes regardless of where it runs, and the
launcher routes those exit codes through the runtime-appropriate
teardown") — EC2 needs no new table, only a new teardown implementation
of the same three dispositions (stop / destroy / leave-alone).

**EBS volume lifecycle — the opposite default from Fly, not the same
discipline.** Fly's "Remote disk policy" above establishes a manual reap
obligation because a Fly volume has no platform-side destroy-on-exit
hook: *"a Machine can be destroyed without destroying its volume."*
Copying that discipline verbatim onto EC2 would be wrong, because EC2's
default behavior is the mirror image. AWS's own root-volume default is
`DeleteOnTermination=true`: the EBS root volume of an EC2 instance is
deleted automatically when the instance is *terminated* (not merely
stopped). This is a platform-enforced hook Fly simply does not have —
the reap obligation Fly's design places on leerie's own code is, for the
default EC2 shape, AWS's problem to solve, not leerie's.

This collapses to three cases, and the design must pick one and state it
plainly rather than default silently:

1. **Root volume only, default `DeleteOnTermination=true`.** This is the
   simple, recommended default for the EC2 runtime: `RunInstances` with
   a single root EBS volume and no explicit block-device override. On
   `TerminateInstances`, AWS reaps the volume itself — no leerie-side
   reap code is needed, and no `test_provision_volume.py`-style
   volume-orphan test surface exists to write, because there is no
   orphan case to test. **This is the default this design adopts.**
2. **Stop, don't terminate, on pause.** Exactly like Fly's `machine
   stop` (preserves the volume, frees compute — EC2 continues to bill
   for the attached EBS volume while stopped, mirroring Fly's per-GB
   volume charge while a machine is stopped), `StopInstances` leaves the
   root volume attached and never invokes `DeleteOnTermination` at all —
   that attribute is termination-scoped, not stop-scoped. The
   stop/start-preserves-filesystem contract that "Remote pause-on-
   failure" establishes for Fly (`run.json` + the run branch are the
   only durable record leerie relies on; the machine's own filesystem
   is a bonus, not the contract) carries over unchanged: an EC2 pause
   uses `StopInstances`, never `TerminateInstances`.
3. **A future secondary EBS volume** (if a later subtask adds one, e.g.
   to give `/work` a device independent of root-volume resizing) reintroduces
   exactly Fly's problem: additional (non-root) EBS volumes default
   `DeleteOnTermination=false`, so a leftover secondary volume *would*
   need the same "every path that destroys the instance must also
   reap the volume" discipline DESIGN's "Remote disk policy" section
   pins for Fly, including the same ordering constraint (the
   volume↔instance association is queryable while attached; detach/
   reap must happen before or as part of termination, mirroring the
   Fly ordering: look up, destroy machine, destroy volume). Since this
   design does not introduce a secondary volume, that discipline is
   deliberately **not** built now — it is flagged here so a future
   subtask proposing a secondary volume does not silently assume EC2
   is exempt from the discipline Fly needed.

**Transport substitution for `flyctl ssh console`.** Two roles need a
replacement: (a) piping the detached-orchestrator launch wrapper to the
instance, and (b) opening a session for `--resume`/`--shell` attach and
log tailing. AWS offers two candidate transports; this design picks SSM
Session Manager over SSH and states why:

- **SSM Session Manager** (`aws ssm start-session`,
  `send_command`/`start_session` via `boto3`) needs no inbound security
  group rule, no key-pair distribution, and no public IP — the SSM Agent
  (preinstalled on Amazon Linux / most current AMIs) calls out to the
  SSM service over HTTPS, the same "no sshd in the image, no key
  management, no public exposure" property the Fly section calls out for
  hallpass + WireGuard (line 2509-2511). Authentication and authorization
  flow through the same AWS credential chain and IAM already established
  for the rest of the EC2 runtime (`aws-credentials.sh`,
  `resolve_aws_region`/`resolve_aws_profile`) rather than a
  parallel key-pair-management surface — one credential model for the
  whole EC2 runtime, matching the "reuses Fly's ... dispositions
  everywhere the two platforms agree" framing above.
- **SSH** (a managed key pair, `LEERIE_EC2_KEY_NAME`, inbound security
  group rule on port 22) is the closer textual analog to `flyctl ssh
  console`, but requires provisioning and rotating a key pair and
  opening network ingress — exactly the surface SSM avoids. It remains
  available as a fallback transport for operators whose account policy
  disallows the SSM Agent or Session Manager IAM permissions, but is not
  the default.

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
volume (case 1 above) and the instance's private/public IP may change
on restart unless an Elastic IP or the instance is in a VPC with a
persistent ENI — a detail the provisioning subtask must pin down
(likely: don't rely on the public IP surviving a stop/start cycle;
resolve the instance's current address via `describe_instances` on every
resume rather than caching it, mirroring how Fly resolves machine
state fresh on every `--resume` rather than trusting a cached IP).
`TerminateInstances` is the `--kill` / clean-exit-after-sync-success
counterpart to `flyctl machine destroy`. The existing sidecar fields
(`paused_at`, `pause_reason`, `killed_at`, `sync_failed_at`,
`sync_fail_reason`) are runtime-agnostic in shape — they describe
*when* and *why*, not *how* — and the EC2 path reuses them verbatim; a
new `ec2_instance_id` field (see below) plays the role `fly_machine_id`
plays today, present whenever the corresponding EC2 sidecar state is
set.

**Run identifier.** DESIGN's "The run identifier" states the invariant
plainly: `run_id` is "the container/machine ID assigned by the container
runtime." An EC2 instance ID (`i-0123456789abcdef0`) fills exactly the
same role — known at `RunInstances` time, before the orchestrator starts,
with no deferred computation and no rename, satisfying the same
constraint the run-id-before-orchestrator-start ordering above depends
on. Two coupling points that hardcode the Fly-specific sidecar name will
need generalizing when EC2 provisioning actually lands (out of scope for
this subtask, flagged here so the next one doesn't have to rediscover
it): `scripts/remote/provision.sh` writes `fly-machine.json` as the
crash-recovery pointer, and `orchestrator/leerie.py`'s `discover_runs`
(DESIGN §6 multi-run resume) looks for that exact filename to recognize
a pre-`state.json` crash-recoverable orphan run.
The natural generalization is a same-shaped `ec2-instance.json` sidecar
(instance id, region, created-at) plus widening the orphan scan to check
for either sidecar file — not renaming or repurposing
`fly-machine.json`, which stays exactly as-is for Fly runs.

---

## 6½. Per-repo dependency provisioning

The container image ships a fixed base toolchain. Every target repo
ships its own — different language versions, different package
managers, different lockfiles. Two distinct things go wrong if the
orchestrator just runs workers against a fresh checkout:

- **Dependencies are missing.** A Next.js repo needs `pnpm install`
  before any worker can `pnpm lint` or `pnpm test`. A Django repo
  needs `uv sync`. A Go repo needs `go mod download`. The container
  has none of these installed for the specific repo.
- **Runtime versions are wrong.** A Next.js repo with `.nvmrc:
  20.11.0` does not behave correctly under the image's baked Node
  LTS. A Django repo with `.python-version: 3.11.7` should not run
  on Python 3.12. Mismatched runtimes manifest as opaque failures
  far from the cause — a worker reports a passing test under the
  wrong Python, the integration step finds the version mismatch
  later, the user sees a confusing failure.

A third compounding factor: `git worktree add` checks out tracked
files only. Untracked artifacts — `node_modules`, `.venv`, build
outputs — are *not* copied from the main checkout. Even if the host
repo were fully installed, every per-subtask worktree would start
empty. The orchestrator handles this in two layers: runtime
versions and the optional setup hook are pre-installed *in* the
container before any worker runs, because they're cross-cutting
state every worker shares; dependency installs (pnpm, pip, cargo,
etc.) are deferred to each worker, which runs the install in its
own worktree against shared package-manager caches.

The orchestrator addresses both with a dedicated phase between
classification and planning, layered top-to-bottom by determinism:

1. **`.leerie-setup.sh` hook.** Optional, repo-owned. If the repo
   needs user-space tooling the language layer can't install — a
   language version mise supports beyond the LTS bake (Ruby, Java,
   Rust), an additional CLI tool installed under `~/.local/bin`,
   pre-populated fixtures the workers need — the repo commits a
   script that handles it. The orchestrator execs it inside the
   container as the non-root `leerie` user (the image deliberately
   does not ship `sudo`). Repo author controls trust; the script
   runs in the same container that runs the workers.

   System packages requiring root (apt-get-installable libraries,
   anything writing to `/usr/*` or `/etc/*`) are out of scope for
   the hook — the container's unprivileged user model can't satisfy
   them. A repo with that need maintains a fork of the leerie
   Dockerfile that installs the package at image-build time and
   overrides `IMAGE_TAG`.
2. **Runtime version resolution.** The orchestrator delegates to
   a polyglot version manager that reads the repo's existing
   version declarations (the same files repo authors have already
   been committing for years — `.nvmrc`, `.python-version`,
   `.tool-versions`, `rust-toolchain.toml`, `.go-version`).
   Matching toolchain versions install into a cache that
   survives across runs. If a repo declares nothing, the
   image-baked LTS for Node and Python is the floor — the
   resolver checks the per-run cache first, falls through to the
   image-baked layer. This means the runtime selection has no
   model in the loop; the version manager's parser is the
   enforcement.
3. **Deterministic install-command detection.** A lockfile-keyed
   table maps observable file presence to the install command(s):
   a pnpm lockfile means `pnpm install`, a `uv.lock` means
   `uv sync`, a `Gemfile.lock` means `bundle install`. Polyglot
   repos (Rails with both a Ruby lockfile and a JS one) emit
   *all* matching commands, not the first match — silently
   dropping a frontend install would leave half the workers
   broken. When the table returns a non-empty result the
   orchestrator uses it; there is no model in this path either.
4. **LLM provision worker — fallback.** When the table returns
   empty (Java with Gradle, a bare `pyproject.toml` without
   lockfile, a polyglot Makefile-driven setup), the orchestrator
   invokes a `claude -p` worker whose only job is reading the
   repo's README and configuration files and emitting a JSON
   recipe. The recipe is schema-validated, the commands inside
   it are restricted by an argv-allowlist, and any deviation
   from the schema rejects the worker. This is a *deliberate*
   exception to §12 — see below.
5. **Worker-driven install.** Each fresh worktree is dependency-
   less by design. The orchestrator does *not* pre-install
   anything — neither at `repo_root` (which is bind-mounted from
   the host and writing to it would clobber the host's checkout
   with linux-built artifacts when the host is darwin) nor in
   each worktree (which would be redundant work the worker often
   doesn't need). Instead, the detected recipe is **persisted to
   state and injected into the implementer and conformer prompts
   as a `PROVISION_RECIPE:` advisory block**. Each worker reads
   the recipe and decides whether its subtask actually needs the
   install (a config-only or docs-only subtask doesn't; a "run
   the tests" subtask does), then runs the command itself from
   its own worktree via its Bash tool. The package-manager
   caches (pnpm store, pip wheel cache, go module cache, cargo
   registry, Bundler gem cache) are shared across worktrees and
   across runs, so re-running the install command in worktree N
   is fast.

   This shape has three benefits over an orchestrator-driven
   install: (a) the host's checked-out source tree and tracked
   dep artifacts (`node_modules/`, `.venv/`, `target/`, etc.) are
   never written to by leerie's install path — `.leerie-setup.sh`
   (user-opt-in) is the only path leerie ever modifies under the host
   repo (run state lives outside the repo at `<state-root>`); (b) no work
   is wasted on worktrees whose subtasks don't need built deps;
   (c) the same `claude -p` event-streaming the workers use for
   everything else makes install progress visible to the user,
   without any special orchestrator plumbing.

### The §12 carve-out

Step 4 is the only place in leerie where an LLM-generated artifact
gets persisted and shown to other workers as authoritative content.
The central principle of §12 is that prompts are advisory and code
enforces; an LLM-generated install plan that the orchestrator
then *renders verbatim into downstream worker prompts* needs the
same containment any other LLM-to-code path would. The carve-out
is justified by three constraints that contain it:

1. **It only fires when the table returns empty.** The 80% of
   repos with conventional lockfiles never reach the worker. The
   model sees the genuinely ambiguous tail, which is where
   human judgment would be doing the work anyway.
2. **The recipe is mechanically bounded.** Every command's
   `argv[0]` must come from a fixed allowlist of package managers.
   Shell metacharacters and traversing working directories are
   rejected. The worker cannot emit `sudo`, cannot pipe into
   `sh`, cannot reach outside the repo. This containment is
   *what makes the prompt-injection safe* — the validator ensures
   the rendered `PROVISION_RECIPE:` block carries only argv
   sequences from a known-safe vocabulary, so a downstream worker
   that copy-runs an entry can't accidentally execute something
   harmful. The §12 principle ("any guarantee that matters and
   can be checked mechanically lives in code") holds — the
   *guarantee* is in the validator, not in any worker prompt.
3. **It is the only documented exception.** Any future feature
   that wants to render LLM-generated content into a downstream
   worker prompt has to add its own §-level justification, not
   point at this one. Documenting the carve-out explicitly is
   what prevents it from becoming precedent.

The alternative — refusing the run when the table doesn't match —
would be strictly more §12-compliant but worse for the user. The
carve-out is a deliberate trade.

### Resume

Provisioning runs inside the same fresh-run branch of `orchestrate()`
that runs classify, plan, and schedule — none of which re-execute on
`--resume`. The resume path loads state and jumps to execution; the
recipe lives in state, the version-manager cache survives across
runs on disk, and workers see the right toolchain without anyone
re-running provisioning.

A successfully finalized run (`finished_at` set AND `current_phase`
== "phase 6: finalize") is terminal — `--resume` returns immediately
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
which is what both `_run_conformance_phase` and `run_final_conformance`
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
shell commands and reverse-engineered deps from command strings — that corpus was
overwhelmingly noise (greps, `git`, `pytest`, `python3 -c` one-liners) and let the
worker degenerate into echoing prose as package names. Reasoning over manifest
files (with commands as a hint) is what actually delivers the "across all
languages and frameworks" goal. Which files and commands the worker sees is
deterministic corpus selection in code; the model still decides content (§12
*Prompts are advisory, code enforces*). Structured output (`setup_packages` and
`language_installs`) is validated against a JSON schema and written to
`.leerie/config.toml` deterministically. The `dep_capture` worker defaults to
`opus`/`high` and is overridable via `LEERIE_MODEL_DEP_CAPTURE`.

**System packages → `setup_packages` → warm apt layer.** `dep_capture`'s
`setup_packages` output is union-merged into `setup_packages` in
`.leerie/config.toml` (never clobber: only new packages are appended;
user-edited values and comments are preserved). The existing launcher
auto-generation path (see *Per-repo container image* above) turns the updated
`setup_packages` into a derived apt-install Dockerfile next run. Workers that
previously failed every `apt-get install` attempt (because they run unprivileged)
find the package pre-installed; the install-intent loop stops.

**Language deps → `language_installs` → richer Dockerfile bake (gated on
`bake_language_deps`, default true).** `dep_capture`'s `language_installs`
output (per-manager `{manager, command, copy_inputs}` entries) is written to
`.leerie/config.toml`, keyed by manager, never-clobber. When `bake_language_deps`
is enabled, the auto-generated `.leerie/Dockerfile` (and, when `build_repo_image`
builds it, the derived image) also includes a language-dep layer: `COPY` for the
lockfile, manifest files, and any ancillary inputs the package manager requires,
followed by `RUN <command>` (`pnpm install --frozen-lockfile`,
`pip install -r requirements.txt`, etc.). Workers that inherit this image find
their `node_modules` / site-packages already populated — the per-worker install
drops to near-zero.

**Rebuild tradeoff.** A dependency-input change triggers a full image
rebuild (`build_repo_image` fires when the hash mismatches). To keep
rebuilds narrow, `.dockerfile-hash` folds in the sha256 of every input that
participates in the `COPY` list (lockfiles, manifests, workspace
`package.json`s, `patches/`, `.npmrc`). A change to an unrelated source file
does not invalidate the layer. The cost — minutes per rebuild — is paid once
across all subsequent runs, a clear net win against per-worker install time
accumulated across hundreds of workers.

**Trigger seams.** All three funnel to one `dep_capture` worker — the trigger
differs, the decision-maker does not:

- **Clean finish → finalize.** `capture_repo_deps` is called (with `await`)
  from `phase_finalize` after `finished_at` is written and run-branch
  verification completes. On a `--resume` of an already-finished run the
  resume guard returns before finalize; capture does not re-fire. On a
  `--resume` that reaches finalize (partial resume), capture re-runs — the
  union merge makes this a no-op when nothing new was found.
- **Cancel / SIGTERM → cancel arm in `main()`.** Catchable signals
  (`KeyboardInterrupt` / `InterruptedBySignal`) surface in `main()` after
  `asyncio.run(orchestrate)` unwinds, with a real Python window before the
  `finally` cleanup block. A best-effort `asyncio.run(capture_repo_deps(...))`
  runs there — the same post-loop pattern as the `RateLimitedExit` arm.
  Non-fatal; covers `nerdctl stop` / Ctrl-C. `SIGKILL` gives no window.
- **SIGKILL / crash / host-side → backstop + `--recapture`.** Covered two
  ways, both host-side, modeled on the `--phase judge` scaffolding:
  *Run-start backstop* — at run start, before `phase_classify`, a scan of
  prior run dirs detects any with `logs/` but no `dep_capture.done` sentinel
  and runs capture over them automatically. *On-demand `--recapture`* — the
  `leerie config --recapture` verb resolves the target run, constructs and
  flocks its `State` (refusing to race a live orchestrator via
  `StateLockedError`), and runs the worker via `asyncio.run`.

**Union by default; replace only on `--recapture --force`.** Every automatic
seam — finalize, cancel, backstop — writes as a never-clobber *union* so a
capture can only ever add packages/managers, never remove one the operator
narrowed by hand. The single deliberate exception is the operator-driven
`leerie config --recapture --force`, which wholesale-*replaces* the persisted
`setup_packages` + `language_installs` from the fresh capture (dropping deps no
longer captured) — an explicit "rebuild the dep set from current history"
gesture. Even under `--force`, an empty capture leaves the existing config
untouched, so a bad run can never blank a good config.

**Idempotency.** After a successful write, `capture_repo_deps` writes a
lightweight `<run_dir>/dep_capture.done` sentinel file and sets
`dep_capture_done = True` in `state.json`. The run-start backstop skips
any run whose sentinel file is already present. When the union merge adds
no new packages and no new install command, the function returns immediately
without touching `.leerie/config.toml`.

**No auto-commit.** Capture writes `.leerie/config.toml` (and, if generated,
`.leerie/Dockerfile`) as uncommitted files in the user's working tree.
Leerie logs one line: *"captured N package(s)/install command — run `git add
.leerie/ && git commit` to bake into the next run's image."* The user
controls when and whether to commit. This preserves the committed-Dockerfile
authority rule: a user who has hand-authored `.leerie/Dockerfile` is not
surprised by an auto-commit altering it.

**Non-fatal.** Any error during capture or write — log parsing failure,
TOML write error, filesystem permission issue — is caught, logged at debug
level, and swallowed. A run must never fail because dependency capture
failed. The run is marked complete regardless.

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
`--runtime fly` the workflow is split across two directions:
- **Machine → host (stream-back).** After the run-state tar, `fetch-branch.sh`
  best-effort streams `/work/.leerie/config.toml` and `/work/.leerie/Dockerfile`
  from the Fly Machine back to `$USER_REPO/.leerie/` (or `$LEERIE_STATE_HOST_DIR`).
  Each file is existence-guarded on the remote side and never clobbers a
  host-edited file; failure is non-fatal and does not affect `fetch_branch`'s
  return code. This fires only on a clean finish (the same condition gate that
  runs `fetch_branch` at all — rc `0|10|11|75`). Cancel/kill recovery uses the
  host-side `--recapture` / next-run backstop instead.
- **Host → machine (seed-repo whitelist).** Pre-existing committed `.leerie/`
  files (including a previously streamed-back and committed `config.toml`
  or `Dockerfile`) are included in the `seed-repo.sh` dirty-delta filter so
  they reach the machine's `/work/.leerie/` on the next run. The Fly
  derived-image path then picks them up identically to the local nerdctl path.

### Browser-based test execution in the base image

The base image ships headless Chromium and a version-matched
chromedriver (see *Image build*, IMPLEMENTATION.md §0.5). This is
scoped narrowly: it exists so that workers can **execute** browser-driven
tests — Selenium, Capybara, Playwright, Puppeteer — inside the
container, the same way they run any other test command. It is not a
visual-verification capability; nothing renders a screenshot back to a
worker or the user. A Rails repo with a Capybara feature-spec suite, or
a Next.js repo with Playwright e2e tests, needs a real browser to `bundle
exec rspec` or `pnpm test:e2e` at all — without one, an entire test
category is unreachable and reports as a false pass (skipped) or a
misleading failure (driver-not-found) rather than a real result.

**Baked at build time, not resolved at run time.** The browser and its
driver are installed from Debian's own apt repos in the same
transaction (`chromium` + `chromium-driver` + the `libc6` bump that
keeps `chromium` from failing to load — see *Image build*), so the two
are always version-matched and neither downloads anything when a
worker's test suite runs. This follows the same reasoning as runtime
version resolution in §6½ above: keep the model out of the loop for
something deterministic. Selenium Manager (the common
auto-download-a-driver mechanism) would otherwise reach out to the
network on first use inside every fresh worktree — extra latency per
subtask, and a dependency on network egress the container may not have.
Baking the browser into the image turns a per-worker runtime concern
into a one-time build-time concern, consistent with how the image
separates cross-cutting state (pre-installed in the container) from
per-worktree state (installed by each worker) elsewhere in this
section.

**Sandbox flags baked in, not left to each repo.** Workers run as the
non-root `leerie` user, so Chrome's SUID sandbox cannot work in
this container regardless of which project's test suite invokes it.
Rather than expect every repo's test config to discover and set
`--no-sandbox` / `--disable-setuid-sandbox` / `--disable-dev-shm-usage`
correctly, the flags are written once into
`/etc/chromium.d/leerie-container-flags` at image build time (detail:
IMPLEMENTATION.md §"Browser-based testing"). A project that already
sets these flags is unaffected — they're idempotent; a project that
doesn't now still works, because the wrapper applies them globally.
This mirrors the container-image posture elsewhere in this doc: fix a
class of failure once at the image layer instead of asking every
worker, in every worktree, on every run, to route around it correctly.

### `leerie config` — host-side onramp

Not every repo author wants to hand-write `.leerie/config.toml` or
`.leerie/Dockerfile`. The `leerie config` verb is a host-side fast-path
that generates and inspects these files without starting a container.

**Why no container.** Config generation only needs to read the repo's
existing files (lockfiles, CI yaml, `package.json`, `Gemfile`, etc.) and
write into `.leerie/`. That is a read-plus-local-write operation — no
worker isolation, no network, no package-manager caches. Starting a
container to do it would add thirty-plus seconds of startup overhead with
no benefit and would complicate the UX: the user is being asked to
*configure* leerie before running it, so making them provision a machine
first inverts the sequence.

**Why it is not in the four-verb remote-lifecycle table (§6 "verb
surface").** That table (`leerie "task" --runtime fly`, `--stop`,
`--resume`, `--kill`) is explicitly scoped to the remote *run* lifecycle —
machine allocation, pausing, resuming, and destruction. `leerie config` has
no run lifecycle; it never allocates a machine or a container. It is a
host-side utility verb in the same family as `leerie --list`: fast, local,
and orthogonal to run management.

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

What happens after a hard worker error depends on whether partial progress can
be salvaged. An **implementer** has a worktree branch and possibly a checkpoint,
so its failure is converted into a handoff: a fresh implementer can continue.
The **classifier, planner, reconciler, plan_overlap_judge, and provision** have no partial-progress
artifact to hand off — there is nothing for a successor to continue from — so
their hard failure aborts the run with state saved for `--resume`. The
**conformer** has commits but its phase is advisory, so a hard failure surfaces
as a warning, not an abort. The rule is general: salvage if there is something
to salvage; abort cleanly otherwise. When `planner_samples > 1`, a crashed
sample is dropped and the surviving samples for that domain proceed to
selection; the abort fires only when all samples for a domain fail.

The **integrator** is the case where that rule and the code disagreed. Its
partial progress is the *resolved staging worktree* — files whose conflict
markers are gone and whose hunks carry real merge judgment — and that is an
artifact in exactly the sense the implementer's branch is. It is also the
most expensive artifact in the run to recreate, because reproducing it means
re-deriving every side's intent from the subtask specs. Crucially, the work
need not be committed to be real: a crashed integrator typically dies
*mid-resolution*, with the resolution in the working tree and no merge commit
(this is what run `879defae`'s wave-2 integrator did). Preservation therefore
cannot be conditioned on `check_merge_committed` — that predicate is false in
precisely the case worth salvaging.

This distinction is between a *crash* and a *verdict*, and only the first is
new. A crash is infrastructure — PID exhaustion, OOM, a killed session — and
says nothing about whether the resolution was any good; the run rescues the
work and pauses for `--resume`. A `design-conflict` or `failed` **verdict** is
the integrator's considered judgment that the merge should not stand, and
still aborts and discards, exactly as *When integration cannot succeed*
describes. Salvaging a crash does not weaken that: a verdict is a fact about
the work, a crash is a fact about the machine.

---

## 8. The evidence-gated loop

The original specification asked each worker to self-report a 1–10 confidence
score and loop until it reached 9. The intent — force the worker to be sure
before it acts — is right. The mechanism is not: a self-reported number is not
a measurement. Models are systematically overconfident and will state high
confidence on a wrong root cause without hesitation. Looping on that number
just loops on the same vibe.

Leerie keeps the loop and the high-confidence bar but **anchors the score to
evidence**. Before an implementer writes any code it must clear a set of
domain-specific *evidence gates*, and each gate must carry a concrete artifact
— a file-and-line citation, a reproduction, a measurement, a cited research
source — not an assertion. The confidence score is then a *summary of which
gates carry hard evidence*, not an independent feeling. A bug-fixing task, for
instance, must show a deterministic reproduction, a test that fails because of
this specific bug, a traced symptom-to-cause path, and a mechanistic
explanation of why the fix addresses the cause. Other domains have their own
gate sets.

Three further disciplines apply at every scoring step, regardless of domain.
They are the mechanisms by which the confidence score becomes load-bearing
rather than ornamental.

- **Falsification.** For each major claim — a chosen root cause, a chosen
  solution — the worker explicitly looks for evidence that would *disprove*
  it: a probe, a counter-example, a research source that contradicts. A claim
  earns high confidence only when its falsifier was tested and failed. Looking
  only for confirming evidence is how a wrong hypothesis acquires high
  confidence; the falsification step is the structural defense.
- **Drift reconciliation.** Before scoring, the worker re-reads its own prior
  statements in the same session. Any current claim that contradicts an
  earlier one — or any earlier position the worker has quietly retreated from
  — is named and resolved with evidence for the kept version. An
  unreconciled contradiction blocks the high-confidence bar. This is the
  defense against a worker confidently asserting X early and confidently
  asserting ¬X later without flagging the change.
- **Gap surfacing.** When a score is below the bar, the worker must enumerate
  the specific *artifact* that would raise it — a citation, a measurement, a
  probe output, a research source — and then go obtain that artifact on the
  next iteration. A gap phrased as an activity ("look into it more", "verify
  the design") does not terminate; a gap phrased as an artifact does. This
  converts an open-ended "try harder" loop into a directed search whose next
  move is deterministic.

The loop is bounded. If the gates cannot be cleared within the bound, the
subtask stops and reports itself as *blocked*, stating precisely what evidence
is missing and whether obtaining it needs something only the user can supply —
for example a credential that exists nowhere in the codebase. This is the
narrow, legitimate exception to "never ask the user" (see §11).

### The planner gate

The same discipline applies one layer up. A planner that decomposes a domain
into subtasks self-gates on two axes — *task understanding* (does the planner
genuinely understand what the user wants and how it lands in this codebase)
and *decomposition quality* (are these subtasks the right cut, sized for one
worker, with real dependencies). The same three disciplines — falsification,
drift reconciliation, gap surfacing — apply. A planner whose gate cannot
clear emits `status: "blocked"` with the gap analysis instead of subtasks,
matching the implementer's blocked-with-evidence exit. The principle is the
same at both layers: a worker that cannot justify its confidence in evidence
hands the decision back to a layer that can, rather than fabricating one.

**The cleared-but-empty terminal state.** Symmetric with the blocked exit
above: a planner whose gate *does* clear can legitimately return zero
subtasks. The contract reads "I understand the task, I investigated this
domain, the work is already satisfied on HEAD" — distinct from blocked
("I could not clear the gate"). When *every* planner returns
`status: "ready"` with an empty `subtasks` array, the run has nothing to
schedule: there is no decomposition to feed phase 3, no work to execute
in phase 5, no run branch to integrate or push in phase 6. The
orchestrator records `no_work_required=true` in state.json with each
domain's `confidence.basis` quoted, writes `finished_at`, skips phases
3–6, and exits 0. The run renders as `done` in `leerie --list` (no
push, no PR — there is no commit to propose). A mixed outcome (some
ready+empty, some ready+nonempty) proceeds normally; the empty domains
simply contribute nothing (dead-subtask elimination can also produce a
mixed outcome by pruning fully-speculative subtasks from a non-empty
domain — see §5 *Dead-subtask elimination*). The all-blocked case still dies — a blocker
is a gate failure that the user must see.

**Already-satisfied subtask elimination (the per-subtask sibling).** The
cleared-but-empty state above is *whole-run*: it fires only when every planner
natively returned zero subtasks. But a planner does not always know that a
deliverable already exists — it is given the task and the codebase, not a
manifest of what a *sibling run* merged to the base branch an hour ago. So a
planner can, in good faith, emit a subtask whose success criteria are already
met on the seeded base (e.g. two runs derive from the same request; the first
merges its PR; the second is seeded from a base that now contains that work).
Left alone, such a subtask reaches an implementer, which correctly reports
`complete` while committing nothing — and the mechanical no-commits backstop
(the `check_branch_has_commits` gate, §5 *Artifact passing between subtasks*)
then fails it as a retryable no-op, burning the retry budget and pausing the
run. The honest answer ("already done upstream") is exactly the one the
backstop cannot represent.

The fix is the per-subtask analogue of the cleared-but-empty check: before
scheduling, a read-only **satisfied-probe** evaluates each subtask's
`success_criteria_seed` against the base tree and soft-drops the ones already
met — the same pre-schedule per-subtask soft-drop shape as dead-subtask
elimination (§5) and the off-tree `files_likely_touched` filter, recorded in
the same `dropped_subtasks` audit. If all subtasks drop, the run routes to the same
`no_work_required` terminal state as the native cleared-but-empty case. This
is a *soft, advisory* prune: it is subordinate to the mechanical no-commits
backstop, which stays as the last line of defense for anything the probe
misses (per §12, the code check is the guarantee; the LLM probe only reduces
how often it fires on already-done work). Two disciplines are load-bearing and
were established by calibration, not assumption: (1) the probe must judge the
**base tree only** — never `git log --all` or another branch/ref, because a
worktree shares the repo's full object DB and a history-spanning probe will
"find" the deliverable on an unrelated branch and false-positive; (2) the
probe defaults to *not satisfied* on any uncertainty, because a false "already
done" silently deletes real work — a strictly worse failure than a false
"still needed" (which merely costs one implementer round the backstop already
tolerates).

**The mid-run sibling case (why the base-tree probe is not enough).** The
pre-schedule probe judges the **base tree** — the checkout as it stood when the
run began. That is the correct discipline for the cross-run case above, but it
is *structurally blind* to a subtask that becomes satisfied **during this run**,
because a sibling subtask in an earlier wave committed the shared deliverable.
Concretely: a code subtask declares `files_likely_touched` that includes a test
file and commits its matching test update in the same commit, while a separate
test-only subtask — scheduled a wave later, whose entire deliverable is that
same test file — `requires` a capability the code subtask `provides`. By the
time the test subtask runs, its work is already on the run branch. It correctly
reports `complete` with nothing to commit, and the no-commits backstop fails it.
The base-tree probe never had a chance: the overlap did not exist at plan time.
The retry then reproduces the identical no-op — the subtask cannot re-do work
that already exists on the branch it is measured against — so the retry cap is
exhausted and the wave dies. A `--resume` re-runs the same doomed subtask and
dies the same way: a deterministic loop, not a transient failure.

The resolution is the post-execution analogue of the pre-schedule probe: on a
no-commits result, before failing, the orchestrator re-runs the satisfied-probe
against the subtask's `success_criteria_seed` on the **run-branch HEAD** (the
current integration state, which *does* contain the sibling's commit). If the
criteria are already met there, the subtask is settled as satisfied — a
legitimate terminal success, recorded in the same `dropped_subtasks` audit with
reason `already_satisfied_mid_run` — rather than routed to the retry cap. If the
probe is *not* satisfied (a genuine lazy/broken no-op, the case the backstop was
built for), the existing retryable-failure path is unchanged. The same
base-tree-only-vs-HEAD distinction is deliberate: the pre-schedule probe must
not span history, but the post-execution probe measures against exactly the ref
the commit-presence gate itself uses (`compute_run_branch`), so it cannot
"find" the deliverable on an unrelated branch.

**Scope: sibling-committed *or* base-tree-already-satisfied.** The probe judges
*whether* the criteria are met on HEAD, not *who* met them — so this rescue also
covers a subtask that was already satisfied on the seeded base (e.g. the
pre-schedule probe was skipped via `--skip-satisfied-check`, or returned a false
negative). That is intended, not a leak: a subtask whose criteria are genuinely
met on the run branch is legitimately complete regardless of provenance, and it
has no commits to make either way. The mid-run *sibling* case is the one that
motivated the fix; the base-satisfied case is the same code path with the same
correct outcome.

**Why this is §12-compliant, not an LLM breaching a mechanical gate.** The
guarantee "a lazy/broken worker that did nothing is caught" stays mechanical:
`check_branch_has_commits` fires first and unchanged, and the probe can only
*rescue* — it never turns a committed subtask into a failure, and it fails safe
to *not satisfied* on any crash or uncertainty, so an unavailable/undecided probe
leaves the mechanical no-commits failure intact. What the probe decides —
"are these success criteria semantically met on this tree?" — is exactly the
kind of judgment §12's *complementary half* assigns to a worker: it cannot be
checked mechanically (it requires matching criteria prose against file content),
which is precisely why the pre-schedule `filter_satisfied_subtasks` uses the same
worker. This adds no new §12 carve-out; it is the outcome-checked-where-possible,
judgment-left-to-the-worker split the principle already prescribes.

The structural contract of these disciplines is mechanically enforced — the
worker's output schema requires the falsification, reconciliation, and gap
fields to be present, so a worker that skipped them fails its own JSON gate
before the orchestrator ever reads it (see §12). The *quality* of the
artifacts each field names is model-judged; the *presence* of the discipline
is not.

**Confidence is the only load-bearing gate.** The implementer's
`root_cause` / `solution` scores (and the planner's `task_understanding`)
are the only confidence signals the orchestrator escalates to `failed` or
`blocked` on. (The planner's `decomposition_quality` axis is retained as an
advisory self-report but no longer gates — the independent `fit_judge` is the
authoritative decomposition-quality gate; see §5½.) Tests passing, lint clean, build
green, per-criterion satisfaction in a written criteria file — all
**best-effort signals**. The orchestrator surfaces them as warnings
attached to the subtask result and to telemetry, never as gating
conditions. The reason is the same incentive §9 *Post-work conformance*
flags from a different angle: any code-enforced "tests must pass" gate
invites a stuck model to weaken the test rather than fix the code. The
confidence gate, anchored to falsifiers and gap evidence, is the
discipline that cannot be cheated by lowering a bar — a worker that
cannot justify confidence in *the work itself* exits blocked, and the
orchestrator's structural enforcement is limited to "did the worker
fill in the self-gate fields at all," not "is the model's score
correct."

### Mechanical-feedback loops (the CRITIC pattern)

Research shows that LLMs cannot self-correct reasoning without external
feedback (Huang et al., ICLR 2024) and that self-bias amplifies when
reviewing their own output (ACL 2024). But self-correction WITH
external tool-verified feedback works — the CRITIC framework (ICLR
2024) showed 7.7 F1 gains grounded in search-API signals.

Leerie applies this finding: every worker (except the PR writer) runs
inside a code-enforced loop (`_run_checked_loop`) where the
orchestrator computes **deterministic structural checks** on the
worker's output — file-existence, dependency-graph cycles, lockfile
consistency, protected-path violations, **confidence-axis gates** — and
re-invokes the worker with the check results as external feedback if
issues are found. The feedback is mechanically derived (no LLM).
Confidence gating (threshold 9.0 on every worker's schema-defined axes)
is code-enforced for all workers, not just the implementer — a worker
that self-reports low confidence triggers re-invocation just like a
worker that hallucinates a file path.

The conformer loop (`_run_conformance_phase`) is the original instance
of this pattern — it loops on observable build/lint/test signals. The
generic `_run_checked_loop` extends it to all workers.

### Task-referenced file extraction

When the task string references files (detectable by globbing), the
orchestrator mechanically extracts structural elements (H3+ markdown
headings, YAML keys, numbered items — excluding H1/H2 section
structure and table-of-contents anchor links) and injects them into the
planner's prompt as an external coverage checklist. The extraction is
a novel technique — an external reference the planner did not generate,
grounded in files the user explicitly pointed to. It is inspired by but
distinct from the executable-specification architecture of "The
Specification as Quality Gate" (arxiv 2603.25773, 2026): that paper
recommends BDD scenarios and contract tests (pass/fail deterministic
checks), while our extraction uses document-structure parsing with
substring matching — a weaker but pragmatically useful mechanism for
coverage-oriented tasks.

The coverage gating check (`check_task_file_coverage`) triggers
re-invocation only when the extracted item count is ≤ 50
(`_MAX_COVERAGE_ITEMS`). Above that threshold the signal is too dilute
for meaningful gating — a planner with 5–15 subtasks cannot
realistically cover half of 200+ spec items — so the check logs
informationally but does not re-invoke. The prompt injection is
unconditional: the planner always sees the full checklist regardless of
item count. No-op when the task doesn't reference files.

### Multi-sample planning

Multiple independent planner invocations per domain, each a fresh
`claude -p` session (Cross-Context Review, arxiv 2603.12123, 2026:
context separation is the mechanism). Mechanical selection by issue
count and subtask count avoids self-bias (a novel extension beyond the
paper, which tested single fresh-session review only). Controlled by
the `planner_samples` cap (default 3).

---

## 9. Success criteria (informational; historical lock)

Each implementer's first step is to turn its assigned seed into a brief
success-criteria file describing what success looks like for the
subtask — the explicit success condition plus any regression guards
worth naming. The file is **informational**. It is written for the
implementer's own clarity, read by the conformance phase (§9
*Post-work conformance*) for context on what the subtask was about,
and useful as a reference for human reviewers. The orchestrator does
not gate on whether the file's individual criteria are satisfied; that
is what the confidence gate at §8 is for.

The implementer may update the file freely as its understanding
evolves. There is no lock. This is a reversal of an earlier discipline
in leerie that locked the criteria file by sha256 hash on first write
and used a worker-initiated `criteria_revision_proposal` channel to
thread any later edits through orchestrator approval. The lock was
introduced to guard against a stuck model lowering its own bar to
clear a hard gate. With the confidence gate as the sole load-bearing
signal (§8), the bar is the model's *anchored confidence in the
solution*, not the contents of a text file — there is no longer a
fixed bar to lower. The lock and the proposal channel were removed in
the same change that consolidated build/lint/test under the conformance
phase.

The criteria file remains useful as input to the conformance phase and
as PR-time documentation, but it does not produce `failed` or `blocked`
outcomes. A worker that wants to record "this criterion isn't met"
does so via `criteria_results[].met: false` in its result — the value
is recorded and surfaces as a warning, but does not change the
subtask's terminal status.

### Post-work conformance

The §8 confidence gate says whether the work landed; the
implementer's criteria notes describe what it was aimed at. Neither
says whether the *change* is in good standing with the repo it lives
in: whether documentation that describes the touched surface is still accurate,
whether tests for the touched code were updated, whether the change still
honors whatever rules the repo declares for itself (CLAUDE.md, AGENTS.md,
`.cursorrules`, a section of the README, a `docs/` file — the location is
repo-specific, and some repos declare nothing). These are real obligations of
a finished change, but they are not part of the assigned criteria and would be
the wrong thing to bake into them: criteria are scoped to the subtask, and the
repo's rules are an environmental fact that survives across subtasks.

So a separate phase runs once a subtask's work has settled:
the **conformer**. It triggers only on the success path — implementer reports
`status: "complete"`, commits are present, the worktree is clean, no
protected path was written. None of the other terminal statuses
(handoff, clarification, failed, blocked) invoke it. The conformer
reads the diff the subtask just produced, reads whatever rules files the
orchestrator located in the repo, and is empowered to commit fixes to the same
worktree branch — updating documentation, adding or amending tests, repairing
a rule violation it spotted.

Where the rule files live varies, so the location is not the worker's problem.
The orchestrator does discovery in code: a fixed, capped allowlist of paths
in the repo root and `docs/` is checked for existence, and the surviving paths
are handed to the conformer as inputs. The worker reads only what it was
given; "what counts as a rules file" is not a judgment call. If discovery
finds nothing, the phase still runs — the conformer focuses on whether the
diff touched a surface the README or a `docs/` file describes, and whether
tests for the touched code were updated — and silently skips the
rule-conformance axis. A repo with no docs and no tests gets a near-no-op.

The same discovered set is surfaced to the *implementer* at write time, not
only to the conformer post-hoc. Convention drift is cheaper to prevent than to
catch: a component written against the repo's design-system doc matches on the
first try, where a conformer that only *reads* the diff afterward can flag a
rule violation but cannot re-derive an unwritten visual convention. So the
implementer's prompt names the discovered convention docs as paths (it reads
the ones relevant to its subtask; it already has the full worktree checkout),
and its evidence gate asks it to reconcile the pattern it followed against
them. This is advisory — matching an existing design is judgment, not a
mechanically checkable invariant — but the *discovery* is code, so the doc
list itself cannot silently drift. The discovery allowlist therefore includes
the repo's design-system doc (e.g. `docs/DESIGN-SYSTEM.md`), which specifies
component/color/banner conventions a UI subtask must follow.

Two further disciplines apply, and they sit at the §12 axis:

- **Highest effort, never required.** Building, linting, and the test suite
  passing are *desired* outcomes of the phase but never gating ones. The
  conformer is told to attempt them and to report what it found, honestly,
  in structured output: each of build, lint, and tests resolves to *ran and
  passed*, *ran and failed*, or *not applicable*. A failure surfaces as an
  advisory warning on the subtask result; it never escalates the subtask to
  `failed` or `blocked`. The reason is the same failure mode §9 guards
  against from the other side: making "tests pass" a hard requirement of
  this phase invites the conformer to weaken a test, comment out an
  assertion, or skip a lint rule to clear the bar. Keeping the phase
  advisory removes that incentive while still surfacing the residual to the
  human and to telemetry.
- **No backsliding.** The conformer can add commits but must not write to
  protected paths. The diff-scope check — no writes to `.leerie/`,
  `.git/`, or `.claude/` *except for the user-deliverable subtrees*
  `.claude/agents/`, `.claude/commands/`, and `.claude/skills/` — is
  re-run against the conformer's commits, on the same protected paths
  and with the same terminality as it ran against the implementer's
  commits. The `.claude/` carve-out exists because those three subtrees
  are the documented Claude Code customization locations: refusing to
  write them would make leerie unable to produce a subagent or
  slash-command as a legitimate deliverable, even though `.claude/`
  top-level files (`settings.json`, `settings.local.json`) are
  coordination and must stay protected. (Earlier iterations of this
  phase also re-verified the criteria-file hash and rolled back
  conformer commits that touched it; that check was removed when the
  criteria lock was retired — §8.)
- **No clobbering the implementer's work.** The conformer's charter is
  *additive* — fix drift, add tests, repair rule violations — never to
  undo what the implementer built. But it runs with full Bash in the
  worktree, and a conformer that reaches for the base tree to attribute a
  pre-existing failure (`git checkout <base-ref> -- .`, `git reset
  --hard`) can revert or delete the implementer's committed files; if it
  then commits that state, integration (which merges the committed
  branch) carries the loss forward silently. So the orchestrator snapshots
  the implementer's committed HEAD *once, before the first conformer
  round*, and after the conformer runs computes the implementer's owned
  set (`git diff --name-only <run-branch>..<impl-HEAD>`) and checks, per
  owned file, whether the conformer reverted it to the base version or
  deleted it — a three-way blob comparison of base / implementer /
  post-conformer content, because a *legitimate* conformer edit leaves a
  distinct third state and must not be flagged. A detected clobber is
  surfaced as a loud advisory warning always; under `--strict-conformer`
  the conformer's commits are rolled back to the implementer HEAD **and
  the subtask is blocked** (the final-tree pass blocks the run) — a
  clobber is the severest residual, so it blocks like any other strict
  residual (fix + `--resume`), even when the conformer's own build/lint/
  test came back clean. It is *not* silently auto-rolled-back in advisory
  mode: a legitimate
  revert-to-base (the implementer's change was wrong and the conformer
  undid it) is indistinguishable from a clobber by git state alone, and
  the phase is advisory by design. The same guard applies to the
  final-tree pass, using the run branch as the base and the staging HEAD
  captured before that pass as the implementer-work snapshot. This is the
  §12 boundary again: the guarantee that matters (committed implementer
  work survives the conformer) is a code check, not a prompt rule.

- **Committed work survives a mid-turn worker death.** The clobber guard above
  protects committed work from the *conformer*; a symmetric threat is the
  *implementer* worker dying mid-turn. A worker that backgrounds an expensive
  final verification step (a `pnpm run build`, a heavy test suite) and then has
  that step OOM-killed gets its `claude -p` session reaped before it writes a
  checkpoint. The synthesized result is an `incomplete-handoff` whose
  `checkpoint_path` does not exist — `validate_result` tags it `empty_handoff`.
  Naively this is treated as a retryable no-op: `settle_subtask` calls `fail()`,
  which `_reset_subtask_worktree`s away the worktree (destroying any committed
  diff) and burns `failed_retries` until the whole run dies. But the worker may
  have *already committed a complete, green diff* and died only at a
  container-environmental verification step (the build OOMs the same way for
  sibling subtasks whose criteria don't gate on it — they return before dying
  and settle `complete`). So before failing an `empty_handoff`, the orchestrator
  runs the positive-polarity `branch_has_commits_ahead` gate: **if the branch has
  commits ahead of the run branch, the worker produced a real deliverable** and
  the result is settled as `complete` — routed through the advisory conformance
  phase, which records the unfinished verification as a warning — rather than
  discarded. Only a genuine no-op (no commits) stays retryable, and the
  commit-presence test is a *positive* proof (`branch_has_commits_ahead`, as
  distinct from the `check_branch_has_commits` no-op gate on the `complete`
  path): a
  worktree that is gone or on which git fails counts as "no proven commits" and
  is **not** rescued, so an indeterminate git state can never be mistaken for a
  real deliverable. This is the §12 boundary once more: whether committed work
  exists is a deterministic git check, not a judgment the prompt could be
  trusted to make about how the worker died. The prompt half is the *advisory*
  complement (§12's inverse): the implementer is instructed to commit in-scope
  work **before** running any verification step, precisely so that a reaped
  worker's diff is already committed and this code-enforced rescue has something
  to keep — the prompt reduces how often the rescue must fire, the code
  guarantees the outcome when it does. One class of "genuine no-op" is not a
  failure at all: a subtask whose deliverable a *sibling subtask* already
  committed to the run branch this run. That case is caught separately, on the
  `complete`-path no-commits gate, by re-probing the success criteria against
  the run-branch HEAD before failing — see §8 *The mid-run sibling case*.

The phase is bounded by a separate cap from the evidence loop: the conformer
gets a small number of orchestrator-level rounds (default 3) in which to
detect and fix drift. Exhausting the cap with residuals still present does
*not* fail the subtask — the residuals become warnings, the subtask still
returns `complete`, and the work moves on to integration. This is consistent
with the rest of §12: what cannot be guaranteed in code (a model genuinely
catching every documentation drift) is not promoted to a hard guarantee by
prompt; what *can* be guaranteed (protected paths stayed untouched, the
worker's structured output is well-formed) is enforced in code.

**Opt-in strict mode.** `--strict-conformer` (also `LEERIE_STRICT_CONFORMER`
env var, `strict_conformer` in `leerie.toml`) replaces the advisory framing
with a blocking one: when conformer residuals remain after the round cap is
exhausted, the subtask returns `blocked` instead of `complete`. The user
fixes the residuals manually and runs `--resume`. The same check applies to
the final-tree pass — if residuals remain, the run pauses before the PR
opens. This is an explicit trade-off: the operator accepts the risk described
above (that the conformer may weaken work to clear the bar, or that
pre-existing environmental failures will block unrelated subtasks) in exchange
for the guarantee that no subtask passes with known conformance failures.
Off by default; the advisory framing remains the recommended default for
most repos.

Within a round, the conformer is expected to invoke each build/lint/test axis
**exactly once** (with a targeted-falsifier exception when verifying a single
file's behavior). The orchestrator distinguishes two patterns: (a) running
the same axis multiple times with different scopes (targeted test → full
suite → verification grep) is legitimate progressive testing and is surfaced
as an advisory only; (b) a Bash-tool-auto-backgrounded BLT command followed
by a fresh BLT invocation rather than a temp-file `Read` recovery is the
"retry-instead-of-recover" pattern — this is wasteful and the orchestrator
injects it as structured CRITIC-pattern feedback into the next conformer
round so the conformer can correct the behavior. General "ran N times"
advisories remain observability-only. The expectation pairs with explicit
prompt guidance to pass `timeout: 600000` on long-running test/build commands
so the auto-background trap is avoided in the first place. This is the same
§12 boundary as the rest of the phase: the discipline is checked
mechanically (by parsing the per-worker JSONL log), the response is
advisory.

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

**Base-tree health baseline.** Until this baseline was added, the
conformer was *the first place the repo's build/lint/test suite ran
at all* — the orchestrator itself executed no BLT command. That made a
whole class of failure invisible until too late: when the seeded base
tree is already red — because the repo's own tests are red on the
developer's branch, **or** because leerie's container/provisioning
cannot run the suite (a missing dependency surfacing as a
module-not-found, an out-of-memory kill on a heavy test process) — the
conformer runs the suite, observes the failures, and (correctly, since
they are not in its diff) labels them *pre-existing technical debt*,
records an advisory residual, and the run ships a PR on top of a
baseline leerie never established was green. The two causes are
indistinguishable to a conformer that has no notion of the base state,
so a genuine provisioning defect is laundered as inherited debt. In
chain/group flows the effect compounds: the base is often a prior
run's branch, so an environment-induced red baseline propagates
forward.

The fix is a **base-health checkpoint**, code-computed and advisory
(never a gate — the same §12 reasoning as the conformer's own
build/lint/test axes). Once per run, after the staging worktree is
created off the base HEAD but **before any wave mutates it**, the
orchestrator installs the provision recipe into staging and runs the
resolved build/lint/test commands there directly. Deps must be present
for the suite to run, and per §6½ they live only in worktrees (the
orchestrator never installs into the bind-mounted `repo_root`), so the
staging worktree — unmodified at this point — is the earliest tree
where an accurate baseline can be taken. The verdict is **exit-code
based** (a non-zero exit is RED, zero is GREEN): this is 100% reliable
and needs no per-framework output parsing, which is deliberate — the
suite's own summary format varies by tool and cannot be relied on. A
RED base is surfaced **loudly** (a `log()` warning plus a
`run.json.health.base_suite` record) rather than silently absorbed,
because it usually means *leerie could not make this repo green before
starting* — the operator's signal to suspect provisioning, memory
limits, or missing deps, distinct from a genuinely red base branch.
The baseline is then passed to every conformer (per-subtask and
final-tree) as a `BASELINE:` context line so the conformer scopes its
build/lint/test judgment to the **delta** — failures the change
introduced — rather than re-deriving "these are pre-existing" from
scratch on each pass. The result also feeds a one-line advisory in the
PR body ("builds ✓/✗; +N test failures vs base, or clean net of
base") so the human reviewer sees whether the assembled change builds
and passes its tests relative to the base without checking out the
branch. None of this gates: the confidence gate (§8) remains the only
load-bearing signal.

Because the baseline runs the full suite once (the test run, not the
install, is the cost — measured at tens of seconds to a few minutes on
real repos), it is **skippable** for operators who know their base is
green or who don't want the up-front cost, and each BLT command is
bounded by the same per-command timeout the provision recipe uses.

---

## 10. Context management — handoff, not compaction

The original specification said each worker should compact its context at 70%
occupancy. This cannot be done as stated: there is no channel for an external
process to make a running worker compact itself, and a worker has no reliable
view of its own context percentage. An external monitor can *observe* context
occupancy but has no way to *act* on it.

Leerie replaces compaction with **orchestrator-driven fresh-context handoff**,
which achieves compaction's actual goal — bounded context with preserved
progress — without depending on a channel that does not exist:

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
5. **Involuntary handoffs reuse the same envelope.** A worker that hits the
   per-process wall-clock cap (`worker_timeout_sec`, default 90 min) or that
   produces no schema-valid result after retry is forced into the same
   `incomplete-handoff` shape by the orchestrator. The successor is spawned
   exactly as for a voluntary handoff and validates whatever partial
   checkpoint exists. If no checkpoint was written, the missing-checkpoint
   case routes through the corrective-retry path (see §13 caps) and is
   bounded by the `failed_retries` cap rather than the handoff-chain cap.

A lower auto-compaction threshold on the underlying CLI can be set as an
independent backstop, but it is a parallel safeguard, not the mechanism — the
handoff design stands on its own.

### Where coordination artifacts live

Checkpoints and criteria are coordination state, not code. They are written to
a coordination directory in the main repository, never inside a subtask's
worktree. A worktree is disposable — it is removed at cleanup — so a checkpoint
stored inside it would vanish exactly when a successor worker needs to read it.
Coordination state must outlive the worktree that produced it.

Coordination state is **per-run**, rooted at `<state-root>/runs/<run-id>/`
(where `<state-root>` is the resolved state directory — default
`$HOME/.leerie/<basename>/`, overridable via `LEERIE_STATE_DIR` /
`--state-dir` / `leerie.toml state_dir`; always outside the target
repo). The default key is the repo basename only; cross-repo basename
collisions (two different abs_paths sharing a basename) are caught at
use time via an `.owner` sidecar inside the dir that records the
abs_path of the repo that owns it — the launcher refuses to write into
a dir owned by a different repo and prints the override knobs. State,
plan, criteria, checkpoints, logs, the worktrees themselves, the
PR-result sidecar, and the per-subtask `artifacts/` directory (§5
*Artifact passing between subtasks*) all live under the per-run subtree.
Two runs in the same repository share no coordination state — each has
its own `runs/<run-id>/` subtree, and neither can clobber the other's
`state.json`, log files, or worktrees by collision.

---

## 11. The clarification procedure

The default is **zero questions**. The original goal — a fully automated run
that does not interrupt the user — is kept. The question is when an interruption
is genuinely unavoidable, and the answer is a strict filter applied by the
classifier:

1. Can it be derived from the **codebase**? Conventions, patterns, integration
   points, and existing behavior are all readable. If the answer is in the
   code, derive it — do not ask.
2. If not, can it be closed by **research**? Best-practice standards for a
   well-understood problem are findable. If research resolves it, do not ask.
3. Ask the user **only** what neither the codebase nor research can resolve.

The only thing that systematically survives this filter is **intent** — *what*
to build, *which* behavior is wanted. The reason is structural: a decision
nobody has made yet exists in no codebase and in no research source. The
codebase and research answer *how* to build something; they cannot answer
*what* to build when that has genuinely not been decided. A fully-specified
request leaves nothing for the filter to catch, so it runs with zero questions.

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
first; research only where the codebase is insufficient). The preference is
read, in order, from a CLI flag on the invocation, from an environment
variable, from a per-repo config file committed at the repo root, and
otherwise defaults to `both`. The CLI flag and env var outrank the file
because they are session-scoped knobs — a user reaching for either is making
a one-off override of the repo default. The preference is never surfaced as
an interactive question: any explicit setting overrides the default, and a
caller who sets nothing has implicitly accepted `both`. A request that
already names its own source of truth, or a non-feature task where the
question does not apply, runs without it. Whichever path resolved the
preference, its value becomes a setting carried to every planner and
implementer, so the whole run draws from one consistent source of truth.

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

The clarification filter runs at Phase 1 — early, before any implementer
has done work. That is the right time for *most* intent questions: they
are visible from the task description and the codebase. But some intent
questions surface only after partial implementation work has narrowed the
problem to a decision point neither the codebase nor research can resolve
— for example, whether a refactor should preserve backward compatibility
with a deprecated client, when both choices exist as patterns elsewhere in
the codebase and the task description does not say.

Leerie treats this as the same kind of question as a Phase-1 clarification,
not as a different category. The filter is identical: investigate the
codebase first; treat research as the second-line resolver; ask the user
only what neither can settle. The only difference is *when* the question
surfaces. The mechanism reuses the existing handoff infrastructure: the
implementer writes a checkpoint of its work-in-progress, returns a status
that carries the question to the orchestrator, and the orchestrator surfaces
the question through the same interactive/non-interactive paths the Phase-1
clarification step uses. On the user's answer (delivered either interactively
or via a re-run with `--answers`), a fresh implementer is spawned with the
checkpoint as a continuation and the answer added to its clarification
answers — exactly the channel used by Phase-1 answers.

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
model and a model can drift, misread, or — under pressure — rationalize around
it. Anything that *matters* and *can be checked mechanically* is therefore not
left to the prompt. It is checked by the orchestrator, in code, with no model
judgment involved.

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
  cycles, lockfile consistency, task-file coverage) on the output **and**
  gates on the confidence axes themselves (threshold 9.0 on every
  schema-defined axis). Both are code-enforced: a worker that
  hallucinates a file path and a worker that self-reports low confidence
  both trigger re-invocation with structured feedback. The confidence
  gate completes the "code enforces" principle — a number the model
  produces is still externally verified by the orchestrator rather than
  trusted at face value.

- The orchestrator does not trust the `dep_capture` worker to self-select
  what to write; it schema-validates the worker's structured output
  (`setup_packages`, `language_installs`) before writing anything to
  `.leerie/config.toml`. The worker decides content — what the repo
  genuinely needs — but the code enforces the write path: union merge,
  never-clobber, and the committed-Dockerfile-authoritative rule are all
  implemented as deterministic Python checks that the worker cannot
  override (§6½).

The complementary half of the principle is just as important: **what cannot be
checked mechanically is left to the worker, and not second-guessed by code.**
Understanding intent, writing code, decomposing a domain, resolving the
*semantics* of a merge conflict — these need judgment, so a worker does them.
The orchestrator checks the *outcome* where it can, but it does not pretend to
do the worker's reasoning.

A reader reasoning about *where a given guarantee comes from* should always ask:
is this enforced by code, or only requested by a prompt? The two have different
strengths, and the design depends on keeping them clearly separated. The
concrete enforcement points — which function checks what, at which phase — are
catalogued in `IMPLEMENTATION.md`.

One enforcement point — the mechanical "judgment workers (classifier, planner,
reconciler, plan_overlap_judge, provision) cannot mutate state because they run in the real repo
cwd without `--dangerously-skip-permissions`" guarantee — has an explicit
opt-out: `leerie --dangerously-skip-permissions`. The flag is named identically
to the underlying Claude Code CLI flag, on purpose: choosing it means the user
understands they are removing a guardrail, not merely changing a setting. When
set, every worker is invoked with `--dangerously-skip-permissions`, including
the judgment workers; the §12 mechanical enforcement that they stay read-only
is waived, and trust shifts onto the prompts. The default is off, the safe
invariant is preserved for everyone who does not pass it, and the opt-out is
intended for repositories where the planner needs visibility into build/test
tooling that the narrow inspect allowlist excludes (`pnpm`/`tsc`/`vitest` and
friends). This is a documented breach of the §12 contract for that single
invocation, not a softening of the contract.

A second enforcement layer compensates for the permission bypass:
`DISALLOWED_TOOLS` is passed via `--disallowedTools` on every `claude -p`
invocation. Unlike `--allowedTools` (permission-tier, bypassed by
`--dangerously-skip-permissions`), `--disallowedTools` with bare tool names
removes tools from the model's context entirely — the model cannot see or call
them regardless of permission mode. The deny list targets tools that spawn
untracked parallel work or set timers the orchestrator cannot track: `Agent`,
`SendMessage`, `ScheduleWakeup`, `CronCreate`, `CronDelete`, `CronList`,
`RemoteTrigger`, `PushNotification`. This is the §12-correct direction: a
mechanical code-side deny that survives the permission escape hatch.

---

## 13. Caps and escalation

Every loop in the system has a hard bound. Nothing spins forever; when a bound
is reached, Leerie escalates rather than looping. But the bounds are of **two
different kinds**, and the difference is itself a design point — it is the §12
principle applied to caps.

### Code-enforced caps

Some caps are counted by the orchestrator: the number of subtask continuations
for a subtask, the number of mechanical-feedback rounds for a judgment worker,
the total number of workers a whole run may spawn, the parallelism within a
wave, and a per-worker time and turn limit. These are real counters in real
code. When one is hit, the orchestrator takes a defined action — block the
subtask, abort the run with state saved, throttle. Because the orchestrator
owns the counter, the cap is a genuine guarantee.

The post-work conformance cap (`conformance_rounds`, §9) is also code-enforced
but its escalation is *advisory*, not blocking: when the cap is hit, residual
findings surface as `conformance_warnings` on the subtask result and the
subtask still returns `complete`. The cap bounds work, the warnings make the
unfinished work observable, and the subtask never escalates to `failed` or
`blocked`. This is the §12 principle applied to a phase that is itself
advisory: the count is real, the action it triggers is to record, not to
block.

The mechanical-feedback caps (`judgment_check_rounds`,
`planner_check_rounds`, `implementer_confidence_retries`) are also
code-enforced. The orchestrator runs deterministic structural checks on
each worker's output and re-invokes with the results as external
feedback (§8 *Mechanical-feedback loops*). Escalation on exhaustion is
worker-specific: planners proceed with the best result + warnings,
the classifier dies, the integrator aborts the merge. (That last is the
*check-exhaustion* path — the integrator kept returning output the
mechanical checks rejected, which is a verdict about the work. A worker
**crash** mid-resolution takes the salvage path in §12 instead.)

The multi-sample cap (`planner_samples`) controls independent parallel
invocations. Selection among samples is mechanical (fewest issues,
most subtasks) — no LLM judgment involved.

### Worker-internal caps

Other limits — how many times an implementer or planner re-runs its evidence
gate, how many times an implementer re-runs its validation loop — live
*inside* a single worker. The orchestrator never sees these iterations; it
sees only the worker's final result. These limits are therefore
*prompt-governed*: the worker is instructed to bound itself, and the genuine
hard backstop is the worker's overall turn limit, which the orchestrator does
control.

The evidence-gate bound is exposed to users as `--confidence-rounds` (also
`LEERIE_CONFIDENCE_ROUNDS` and `leerie.toml`); the orchestrator passes the
resolved value into each worker's prompt. The user-visible knob is real — the
worker reads it — but the worker is what counts iterations against it, so the
guarantee is still prompt-governed in the sense above. Surfacing the knob
lets a user dial how persistent workers are at building confidence without
changing what kind of guarantee that bound is.

This distinction matters and must not be blurred. Presenting a worker-internal,
prompt-governed limit as if it were a code-enforced guarantee would mislead
anyone reasoning about the system's reliability. The orchestrator enforces the
*consequences* of a worker's result deterministically; it does not count the
iterations inside the worker that produced it. That is acceptable only because
the orchestrator gates on outcomes, not on iteration counts — and because the
overall turn limit is a real backstop regardless of whether a worker honored
its instructed self-discipline.

### The two-tier retry policy

When a subtask fails, whether it is retried depends on *why* it failed. The
governing rule:

> Retry a failure only if a corrective note to a fresh worker can plausibly fix
> it. Terminate immediately on a failure that means the worker is broken or
> dishonest — re-running it burns a worker for no expected gain, and a cold
> restart can discard partial work.

A **retryable** failure is a correctable mistake: the worker did real work but,
say, forgot to commit it, or left its worktree dirty. A fresh worker told
exactly what went wrong can plausibly succeed. A retryable failure is retried
up to the retry cap; a second occurrence terminates it.

A **terminal** failure means the worker itself is unreliable: it returned a
self-contradictory result (claimed success with no supporting evidence), or
wrote to a protected path it was told never to touch, or failed at the process
level even after the schema retry. Re-running a broken worker does not make it
honest. A terminal failure ends the subtask on first occurrence.

Either way a terminated subtask is fatal at its wave boundary: the run stops
with state saved, rather than carrying a broken subtask forward into
integration. The specific failure-to-tier mapping is in `IMPLEMENTATION.md`;
the *principle* — correctable-mistake versus broken-worker — is the design.

### Budget feasibility — fail fast at the cheapest moment

`max_total_workers` is a hard ceiling on the number of `claude -p`
invocations a single run may spawn. The cheap, late check is
`State.bump_workers()` — it raises `WorkerError` the moment the
counter would exceed the cap. That check is necessary as a backstop,
but it fires *during execution*, after some — sometimes most — of the
run's compute has already been spent. A 63-subtask run with the
default cap of 200 cannot finish; the late check discovers this around
subtask 38, leaving the run branch with the first few waves' worth of
integrated commits and the rest unrunnable.

The corresponding *early* check belongs at the plan/execute boundary.
By the time `schedule()` returns its `(subtasks, waves)` pair, every
unknown that determines how much budget the rest of the run will
consume is resolved: the final subtask count is fixed, the wave count
is computed (deterministically, by Kahn's algorithm over the
dependency graph — no LLM call), the planner-domain count is settled,
and the upstream phases (classify, provision, plan, reconcile,
overlap-judge) have already been billed into `worker_count`. A
feasibility check at this point can estimate the remaining cost
(implementer + conformer per subtask, integrator per wave, finalize)
with no free variables beyond the per-subtask call multiplier, which
is well-bounded empirically.

The principle is the same one §12 enumerates: any guarantee that
matters and can be checked mechanically lives in code. The runtime
`WorkerError` will remain — a worker that goes wildly over its
expected call count must still hit a wall — but it stops being the
*primary* discovery mechanism for "this run was unwinnable from the
start." Fail-fast at planner-output time saves the most compute (the
implementers and conformers have not yet been spawned) and surfaces
the actionable fix (a recommended `--max-workers` value, or a hint to
split the task) at the moment the user can still trivially apply it.

The estimate is intentionally conservative — it covers the worst
observed per-subtask ratio plus a safety margin — and exposes a
documented escape hatch (`--skip-budget-check`) for runs whose
operator knows the conformer phase will degrade heavily to advisory
warnings or otherwise come in under the estimate. The escape hatch
matches the same precedence chain as `--skip-smoke` and
`--skip-overlap-judge`: a deliberate opt-out, not a default.

---

## 14. Telemetry, judging, and self-healing

Every main-loop LLM call in Leerie passes through one of the eleven worker types in
`WORKER_TYPES`: `classifier`, `planner`, `reconciler`, `plan_overlap_judge`,
`satisfied_probe`, `provision`, `implementer`, `integrator`, `conformer`,
`fit_judge`, or `splitter` (the last two are the P1 recursive-decomposition
workers — see §5½). Each worker type is a distinct **call type** — a
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
   structured record to a per-run append-only NDJSON file. The file is written
   by the orchestrator — one JSON object per line, one line per call —
   immediately after the call returns. Crash-safety comes from the format
   itself: each line is a complete, self-contained JSON object. A hard kill
   between writes leaves the file valid through the last fully-written line.
   No partial write can corrupt earlier records.

2. **LLM judge skill.** A Claude Code skill that reads a harvest of captured
   calls (one call_type at a time), applies a multi-dimensional rubric to each
   captured prompt/response pair, and writes structured verdicts. The rubric
   evaluates three dimensions: schema adherence (did the worker produce
   well-formed output), factual accuracy (are the claims grounded in the
   codebase or research the worker was given), and hallucination-freeness (does
   the output introduce content absent from the inputs). The judge is advisory
   at the rubric level — its rubric lives in a prompt — but the scoring
   aggregation and pass/fail threshold are real Python in the skill's
   orchestrator script (§12 applied: the rubric is a prompt, the verdict
   accounting is code).

3. **LLM self-heal skill.** A Claude Code skill that takes the judge's verdicts
   for a given call_type, identifies the failure modes, proposes targeted patches
   to the relevant worker system prompt in `prompts/`, applies those patches, and
   replays the failing samples against the patched prompt to measure improvement.
   The loop is capped and its convergence check — whether a heal iteration is an
   improvement, a plateau, or a regression — is real Python (§12 applied: the
   patch proposal is a prompt, the convergence detection is code).

### The subprocess contract — no new runtime

Both the judge skill and the self-heal skill run exclusively through the
existing `claude -p` subprocess invocation path (the same `claude_p()` function
the orchestrator uses for all workers). They introduce no new runtime, no API
key, and no dependency beyond the `claude` CLI already required for the rest of
the system. This is the same resolution as §2: subscriptions rather than the
metered API, and headless CLI subprocesses rather than an agent library.

The judge spawns a fresh `claude -p` worker per batch of calls to be scored;
the self-heal spawns fresh workers for patch generation and for replaying the
failing samples against the patched prompt. Each worker sees exactly the inputs
it needs for its slice of work, and its structured output is schema-validated
before the skill's orchestrator acts on it — the same contract as every other
worker in the system (§7).

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

The central principle (§12) governs this subsystem the same way it governs
everything else:

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

The heal loop re-applies the evidence-gate discipline from §8: each heal
iteration must show measured improvement (a quantitative outcome, not an
assertion) before it updates the "best patch so far." The loop is bounded; a
cap that cannot be cleared within the bound terminates the heal loop rather
than running forever. The same falsification and convergence discipline that
governs an implementer's confidence loop governs the heal loop's patch
iteration — the number of rounds, the success threshold, and the plateau
detection window are all configured, not left open-ended.

---

## 15. Known limitations

These are honest, designed-in limitations — not bugs, but the known edges of
what the architecture can guarantee.

- **Unattended execution requires broad write permission.** A worker that edits
  files without a human approving each action must run with permission prompts
  suppressed. A narrower "auto-approve edits only" mode was considered and
  rejected: it still prompts on shell commands, which would stall an unattended
  run the first time a worker needs to run one. The blast radius is bounded by
  worktree isolation, not eliminated. Leerie should be run on repositories the
  user trusts, ideally inside a container, and the run branch reviewed
  before it is relied on.
- **A worker that exhausts its turn limit without checkpointing loses its
  work.** Handoff depends on the worker writing a checkpoint before it stops. A
  worker that runs out of turns first leaves its successor to start cold. This
  is the most likely failure mode for an under-scoped, too-large subtask —
  which is why planner sizing (§5) is the primary defense.
- **Handoff timing is heuristic.** A worker cannot read its own context
  percentage; it estimates pressure from proxies like transcript length and
  tool-call count. The estimate can be wrong in either direction.
- **Checkpoint quality bounds handoff quality.** Schema validation catches a
  *structurally* incomplete checkpoint; it cannot judge whether a
  structurally-complete checkpoint is *semantically* adequate.
- **Evidence gates reduce overconfidence but do not eliminate it.** Anchoring
  the confidence score to artifacts is a large improvement over a self-reported
  number, but a worker can still misjudge the strength of evidence it did
  gather.
- **Cross-domain dependency detection now goes through a reconciler worker.**
  The scheduler wires cross-domain edges by matching capability tags. If two
  planners describe the same capability with different words, the literal-
  string match would miss the equivalence. A reconciler worker (DESIGN §5)
  catches these mismatches before the scheduler runs: it proposes renames,
  added `provides` declarations, or new connector subtasks. Genuinely
  unresolvable gaps (no plausible match and no reasonable connector) abort
  the run with the reconciler's diagnosis — fail-loud rather than the
  silent-edge-drop the v1 design accepted.
- **Headless usage is metered.** Subscription-based headless usage draws on a
  finite pool, and a large multi-wave run consumes a meaningful amount of it.
  Cost scales with worker count.
- **Parallelism is single-repo per run.** Multiple concurrent runs in the same git
  clone are explicitly supported via the per-run state and branch design.
  Multiple clones running concurrently are also fine — they are independent
  by construction — but the per-run namespacing applies only within one
  clone; leerie does nothing to coordinate across clones within a single run.
  For workloads that span *multiple repositories*, leerie offers **run-groups**
  (§20): N isolated single-repo runs launched together with a shared brief and
  read-only cross-repo visibility. The group boundary is deliberate: it does
  not merge across repos, does not produce cross-repo DAG edges inside the
  planning graph, and opens N independent PRs (non-atomic). Cross-repo
  prerequisites are surfaced as deploy-ordering notes rather than hard edges.
- **Push assumes a remote named `origin`.** Finalize pushes to `origin` and
  opens the PR against the same remote's GitHub repo. A fork pattern where
  the user's write-access remote is named something else (e.g., `mine`
  pushing to a personal fork, `origin` reading from upstream) is not
  supported today; the workaround is `--no-push` plus a manual push. A
  follow-up `--remote <name>` flag is possible but outside the current
  design.
- **System-wide worker concurrency scales with run count.** Each run obeys
  its own `max_parallel` cap; with N concurrent runs the total active
  worker count can be N × max_parallel. The blast radius is bounded per
  run but not globally; users running many concurrent leerie invocations
  should be aware of the headless-usage cost implication.

---

## 16. Verification status

A design document should be honest about how much of the system has been
*demonstrated* to work, as opposed to *reasoned* to work. The distinction is
the first thing anyone running Leerie needs.

**Demonstrated.** The deterministic scaffolding has been exercised. The git
worktree mechanics — branch setup, per-subtask worktrees, wave-to-wave
dependency layering, conflict detection, finalization, cleanup — have been run
against real repositories. The orchestrator's control flow — classification,
planning, scheduling, wave execution, integration, validation, finalize, and
resume — has been exercised end to end against a stubbed worker, including the
failure and retry paths. The deterministic enforcement points have unit tests.

**Not demonstrated.** No worker has been run against a live model. The contract
with the headless CLI is taken from documentation, not from observed behavior;
first contact with the real CLI is the genuine test. The behavioral quality of
the workers — whether the evidence gates, the handoff, and the conflict
resolution actually work as intended — cannot be known until the prompts run
against a live model. The deterministic surface is sound by construction and
by test; the worker behavior is the unverified surface.

Two parts of the surface described in this document are *new* and have not
yet been exercised end-to-end: the per-run namespacing (run-id derivation,
`<state-root>/runs/<run-id>/` layout, parallel-run coexistence, multi-run
resume), and the push-and-PR finalization step (`gh pr create`, run.json
sidecar with `pushed_at`/`pr_url`/error fields, `--no-push` and
`--no-verify`). The single-run, local-finalize design described in earlier
revisions of this document has been exercised; the broader design here
becomes verified only after the corresponding code lands and a first run
exercises it.

Remote-mode features stack on the host-side finalize path described
in §6 *Finalization*. `--runtime fly` provisioning, git-aware
host-to-machine seeding, stream-back finalize (sync-before-destroy),
and remote pause-on-failure all depend on the run-branch-as-durable-
record contract. Verifying them
end-to-end requires the local-mode finalize to be exercised first;
stacking new features on an unproven foundation is the failure mode
this section is meant to surface.

Chain orchestration (§19) is implemented as a **laptop-side wave
sequencer** and **not yet observed in a live deploy**. The
architecture is described in §19: each `leerie --chain` submission
runs a foreground bash loop on the laptop that, per wave, fans out
N background `./leerie --runtime fly` invocations (one per prompt
file) and waits for all to finalize on the laptop via the existing
single-run path (`provision_machine` → `seed-auth.sh` →
`seed-repo.sh` → orchestrator → `decide_teardown` →
`fetch_branch` → `host_finalize` → `destroy_machine`). Between
waves, the laptop runs `chain.git_ops.synth_merge_branches` against
`$USER_REPO` to build the next wave's staging branch and pushes it
to origin.

The launcher's `--chain` verb (and the deprecated `--chain-submit`
alias) is wired end-to-end. The ID-dispatched single-run verbs
(`--status`, `--stop`, `--kill`, `--resume`, `--finalize`,
`--attach`, `--list --chains`) operate on chains by iterating
`$LEERIE_STATE_HOST_DIR/runs/*/run.json` filtered by the `chain_id`
field, dispatching the existing single-run verb per discovered run.
The deprecated chain-prefixed aliases continue to shim to the new
verbs.

**GitHub credentials are never on a Fly machine.** Each per-job
`host_finalize` runs on the laptop using the user's `gh auth` and
`~/.git-credentials` — identical to today's single-run flow.
Workers have no GitHub credentials by construction
(`scripts/remote/seed-auth.sh:149-158` excludes them from the seed
tar). The earlier v3/v4 design that placed a coordinator on Fly
with `GH_DISPATCH_PAT` has been removed in favor of this
laptop-only model.

Verification today is purely unit-level: the chain subsystem's
behavior is mechanically tested across `tests/test_chain_*` (about
50 tests covering credential transport, git operations, ID-
dispatched verb routing, and the wave-sequencer wave loop with
stubs for the per-job `./leerie --runtime fly` invocation, git
operations, and `chain.git_ops.synth_merge_branches`). Live-deploy
verification — running an end-to-end chain against real Fly with
real worker machines — has not been done. Under v7 Shape A there
are no longer Fly coordinator failure modes (heartbeat staleness,
coordinator-volume restart, stale-creds resume) to test; the
relevant failure modes are now those of the underlying single-run
`--runtime fly` path applied N times per wave (which the existing
single-run tests + production usage already cover) plus two
chain-specific ones: a wave-job failure (handled by the wave loop's
`wait`-rc detection + paused-on-failure semantics inherited from
`decide_teardown`) and a synth-merge conflict at a wave boundary
(handled by `chain.git_ops.SynthMergeConflict` + the wave-loop
chain-paused exit). Both chain-specific paths are unit-tested with
stubs; neither has been observed in production.

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

- **Token-aware budgeting** instead of a blunt worker count — bound a run by
  cost rather than by number of workers.
- **Subtask-level resume.** Resume is currently wave-granular: work done since
  the last fully-completed wave is re-run. Finer-grained resume would re-run
  less.
- ~~A dependency-graph sanity pass~~ — implemented as the reconciler worker
  (§5 and §15). After all planners finish, a reconciler worker
  resolves vocabulary drift between domains' capability tags before the
  scheduler builds its DAG.
- **Per-domain implementer specialization.** One generic implementer serves all
  nine domains today. Nine domain-specialized implementers would allow richer
  per-domain guidance, at the cost of more to maintain.
- **Chain dependency DAG.** The chain subsystem (§19) uses an N-wave
  sequential model (wave 0, wave 1, …, wave N−1). A general task-dependency
  DAG would allow arbitrary inter-run ordering for workloads that do not
  fit a purely sequential pattern (e.g. diamond dependencies).

---




## 19. Chain orchestration

A single leerie run takes one task and drives it to a merged PR — one
classification, one plan, one wave sequence, one finalized branch. Many
real workloads are *sequences of tasks* that must run in a fixed order
across one repository: run job A and job B in parallel, then run job C
after both complete. That sequencing problem is outside the scope of
the core orchestrator, which is scoped to one run. **Chain
orchestration** is the subsystem that manages it.

### Shape: a chain is N parallel single runs per wave, sequenced by the laptop

A chain is **a laptop-side wave sequencer that fans out N parallel
copies of today's single-run `--runtime fly` flow per wave, then
synth-merges between waves to build the next wave's base branch,
then repeats.** Nothing more.

Every wave job is a normal `./leerie "$prompt" --runtime fly`
invocation. The existing single-run path
(`scripts/remote/provision.sh` → `seed-auth.sh` → `seed-repo.sh` →
orchestrator → `decide_teardown` trap on laptop →
`scripts/remote/fetch-branch.sh` → `scripts/host-finalize.sh` →
`destroy_machine`) handles each job's lifecycle **unchanged**. The
chain wrapper just loops over waves and synth-merges between them.

### Why no Fly coordinator

Earlier designs (v3+v4) launched an ephemeral Fly machine per chain
to hold chain state, watch worker heartbeats, push branches, and
open PRs. That introduced four new failure modes (workers
unreachable from coordinator's 6PN; coordinator volume contention;
coordinator self-destruct race; coordinator's own GitHub credential
surface) and didn't actually reduce total Fly footprint — the
coordinator was overhead on top of the worker count.

Shape A removes the coordinator entirely. The laptop is the
sequencer; the workers are normal single-run workers; GitHub is
touched only by the laptop via the existing `host_finalize`
mechanism, using the user's `gh auth` and `~/.git-credentials`. Zero
Fly machines hold GitHub credentials at any point.

### Full flow

```
laptop:
  leerie --chain --wave a,b --wave c
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
        run.json so chain-scoped verbs (--resume, --status) can
        discover the run while the orchestrator is still running.
        fetch_branch later overwrites run.json with the
        orchestrator's copy; the parent's post-wait tagging loop
        re-adds both fields.

    wait for ALL wave-N background jobs to finalize on laptop.
    ◀── At this point: every wave-N PR is open. Laptop has every
        wave-N branch (on origin via host_finalize).

    If any job failed → laptop wave loop exits non-zero. User runs
      `leerie --resume <chain-id>` to retry paused runs (and see
      any still-running runs), then re-invokes
      `leerie --chain --wave ...` to continue (the wave loop skips
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
   `GH_DISPATCH_PAT`, `GH_TOKEN`, or any github.com authentication. The
   existing `seed-auth.sh:149-158` exclusion list (`.git-credentials`,
   `.ssh`, `.netrc`, `.gnupg` excluded from the tar pipe) is what
   enforces this; chain workers take that same path unchanged.

2. **Each per-job lifecycle is independent.** A worker dying mid-chain
   pauses that one run (existing single-run pause-on-failure
   semantics). The chain wave loop detects the failure via its `wait`
   exit codes and pauses chain advancement; sibling wave-N runs that
   completed earlier remain done.

3. **Chain-scoped verbs operate by iteration, not coordination.**
   `leerie --status <chain-id>`, `--kill <chain-id>`, `--stop <chain-id>`,
   `--resume <chain-id>`, `--finalize <chain-id>`, and
   `--list --chains` all work by iterating
   `$LEERIE_STATE_HOST_DIR/runs/*/run.json` and filtering by the
   `chain_id` field. For per-run action, they dispatch to the
   existing single-run verb implementation per discovered run.

4. **The laptop is the sequencer.** Wave transitions, synth-merge,
   stage-branch pushes, and chain-scoped verbs all run on the
   laptop. The laptop must be online for wave advancement; per-job
   workers can run autonomously on Fly between fan-out and finalize.

5. **Synth-merge between waves is local + deterministic.** The laptop
   runs `chain.git_ops.synth_merge_branches` against `$USER_REPO`
   after each wave's branches reach origin. Conflicts pause the
   chain with a clear message; user resolves manually in
   `$USER_REPO` and re-runs `leerie --chain --wave ...` to continue.

### What this design deliberately rejects

- **A coordinator machine on Fly.** No per-chain SQLite, no 6PN
  HTTP, no `chain/coordinator.py`, no `chain/state.py`, no
  `chain/fly_client.py`, no worker hook scripts. The laptop already
  handles all of this for single runs; running the same path N
  times in parallel costs nothing extra.

- **Auto-retry on failure.** Wave failures pause the chain. The
  user resolves and explicitly resumes. This matches today's
  single-run pause-on-failure semantics; no new retry policy.

- **Always-on background poller on the laptop.** The wave loop runs
  in the foreground of the user's terminal. If the user wants to
  detach mid-chain, they can Ctrl-C (the `_kill_wave_children` trap
  propagates SIGTERM to in-flight wave children, each of which
  invokes its own `decide_teardown` trap to clean up its Fly
  machine). Resume re-invokes `leerie --chain --wave ...` and the
  wave loop's idempotency check (`pushed_at` set on all wave-N runs)
  skips already-done waves.

### Why this is the right scope for model judgment vs determinism

Per-job behavior — classification, planning, implementation, healing —
remains model-governed within each single run. The chain envelope is
purely deterministic:

- **Wave fan-out** is a bash for-loop, not a model decision.
- **Inter-wave dependencies** are encoded in the `--wave` flag
  ordering, not inferred by a model.
- **Synth-merge** is `git merge --no-ff --no-edit` in
  `chain.git_ops.synth_merge_branches`. A conflict is a bash exit
  code, not a prompt instruction to "resolve conflicts carefully."
- **Chain status** is a `jq` filter over run.json files, not a
  model judgment.

The task content passed to each per-job worker — what the leerie run
should do — is a user-authored prompt file, and the worker's behavior
is model-governed as in any single leerie run. That is the right
scope for model judgment; the sequencing envelope around it is not.

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
- **Its own resume** — `./leerie --resume <run-id>` inside the member's repo
  works exactly as it does for any standalone run.

The group layer adds four thin capabilities on top:

1. **Shared brief.** The group brief — joint intent plus each member's
   external contract — is authored once and prepended to every member's
   prompt. Repo B's planner reads what repo A is building *before* it writes
   its own plan. This is advisory steering; the write-confinement guarantee
   (§12) stays code, not prose.

2. **Read-only cross-repo visibility.** Each member is launched with its
   siblings seeded as read-only inspect-dirs (`--inspect-dir <sibling-repo>`).
   Workers may `Read`/`Grep`/`Glob` under `/inspect/<name>`; they may not
   write there. The enforcement mechanism is the existing
   `filter_offtree_subtasks` guard (§12), unchanged.

3. **Deploy-ordering notes.** When a member's planner declares a cross-repo
   prerequisite as `requires.extent: external` (§5) naming a sibling repo,
   those `external_preconditions` are collected and rendered by finalize as a
   "merge / deploy sibling first" section in that member's PR body. The two
   PRs cannot merge atomically on GitHub — the inconsistency window between a
   backend endpoint landing and a frontend using it is a deploy-ordering fact
   the user already manages (e.g., with feature flags). Leerie surfaces the
   ordering; it cannot enforce it.

4. **Group-scoped verbs.** `--status`, `--stop`, `--resume`, `--kill`,
   `--finalize`, and `--list --groups` on a `group_id` discover members by
   scanning for `group_id`-tagged `run.json` files across the members'
   *separate* state directories. Each verb dispatches to the existing per-run
   implementation for each discovered member. The scanning must iterate over
   the set of member state directories (one per member basename); unlike
   chain-scoped verbs (§19) it cannot assume a single state directory.
   (`--stop` is Fly-runtime-only; it pauses running machines.)

### Why the lean shape

An alternative design would fold N repositories into one run: N run-branches
in one state, a per-repo namespace inside `state.json`, a shared dependency
graph that crosses repo boundaries. That design was rejected because it
rewrites leerie's single most load-bearing invariant — the run-branch as the
resume contract (§6). The resume guarantees, the per-run flock, and the flat
state layout are all predicated on one run = one repo. Touching any of them
risks cascading breakage across the entire orchestrator for a capability (
cross-repo atomicity) that GitHub does not support anyway.

The lean shape reaches the same user value — a coordinated, contract-accurate
feature across two repos — because the value comes from the **shared plan**,
not from atomic joint execution. Two PRs across two repos cannot merge
atomically on GitHub regardless of design. Keeping each member as an ordinary
run means resume, state, isolation, and finalize mechanics are untouched.

### State isolation is free

Per-repo state isolation is a consequence of the existing basename-keyed state
directory design (§6 *Single owner per run dir*): a member that `cd`s into
`../frontend` resolves `$HOME/.leerie/frontend/` independently of any sibling.
The `.owner` sidecar guarantees that two concurrent members with distinct
basenames never collide. The only guard the group launcher must add is a
rejection of any `--state-dir` / `LEERIE_STATE_DIR` override that would pin
all members to one shared directory — the same mechanism that works correctly
for chains (one repo, shared dir is correct) would produce a `.owner`
collision for a group (N repos, N dirs required).

### Cross-repo visibility is enforced, not advisory

The `--inspect-dir` mechanism mounts a sibling repo read-only into the
worker's filesystem (`/inspect/<name>`). Locally, this is a kernel-enforced
`:ro` bind mount. On the Fly runtime, it is convention-enforced (the sibling
is seeded without the leerie user's write credentials). In both cases,
`filter_offtree_subtasks` (§12) soft-drops any subtask whose files fall
outside the member's repository root. This is the same enforcement that
prevents a single-run worker from writing outside its worktree; the group
adds no new mechanism, it just applies the existing one to a new directory.

### The laptop is the sequencer; no coordinator machine

The group launcher runs on the laptop (the same node that runs chain fan-out,
per §19). It mints a `group_id`, fans out one leerie invocation per member
(each `cd`'d into the member's repo), and waits. After all members complete,
it tags each member's `run.json` with the shared `group_id` — discovering
each member's run directory from its basename-keyed state dir using the same
newest-`finished_at` scan the local finalize already uses. No coordinator
machine, no in-container group state, no cross-machine protocol. GitHub is
touched only host-side, per member, by each member's existing `host_finalize`.

### Single-repo is the N=1 degenerate case

A run-group with one member is indistinguishable from a standalone run. The
`group_id` is written into `run.json` and the group-scoped verbs work, but
the cross-repo visibility and deploy-ordering machinery have nothing to
operate on. This means the group verb surface can be tested against a single
member before any multi-repo integration work.

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
