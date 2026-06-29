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
  the insight as a deploy note rather than a hard edge.

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

**Post-merge acyclicity.** The merge dependency-union (B inherits A's
`depends_on` when A is absorbed) plus downstream reference rewriting
(any subtask depending on A now depends on B) can introduce transitive
cycles absent from the post-reconcile graph — the phase 2½ acyclicity
gate passed before these merges ran. The orchestrator therefore runs
Tarjan's SCC on the post-merge graph immediately after applying
collisions. On cycle detection it `die()`s with the full cycle
diagnostic; the actionable recovery is `--skip-overlap-judge` (let the
integrator resolve file conflicts at integration time) or narrowing the
task to reduce cross-planner overlap.

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
needs to know *which* run to resume. The orchestrator auto-picks when
exactly one run exists, and requires an explicit run-id otherwise; the
discovery scans `<state-root>/runs/*/state.json`. Resume never guesses across
multiple runs.

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
`max_parallel + 1` slots, clamped to ≤ 4 GiB) and `pids.max` set
to `caps["worker_pids_max"]` (default 256). When the worker
subtree blows past `memory.max`, the kernel OOM-kills *inside that
cgroup*; sibling workers, the orchestrator, and host-side services
in different cgroups are not eligible victims. `memory.swap.max=0`
prevents the kernel from delaying an inevitable OOM by paging out
worker memory to the Colima swap file.

The mechanism is purely file-permission based on cgroup v2 — no
`CAP_SYS_ADMIN`, no `--privileged`, no `systemd-run`. Both runtimes
(Fly + local nerdctl) use the same delegation path:
`scripts/container-entry.sh` is PID 1 and runs as root (the Dockerfile
intentionally omits the `USER leerie` directive — root at PID 1 is
required so the entrypoint's chown can succeed before privilege drop).
The entrypoint creates `/sys/fs/cgroup/leerie.slice/`, enables
`+memory +pids` in its `cgroup.subtree_control` (so child cgroups
get the controller files), and chowns it (plus `cgroup.procs` and
`cgroup.subtree_control`) to the leerie user. The entrypoint then
drops to the leerie user via
`runuser -u leerie --` before exec'ing the orchestrator (local
nerdctl) or sleeping as PID 1 (Fly, where the orchestrator is started
out-of-band by the launcher's ssh-console wrapper that explicitly
drops via `Popen(user="leerie")`). Either way the orchestrator runs
as leerie and operates inside the delegated slice.

**Rootless exception.** Under rootless containerd (Linux), rootlesskit
maps the host UID to container UID 0, so "root" inside the container IS
the unprivileged host user — no actual privilege escalation occurs. In
this mode the entrypoint detects rootless via `/proc/self/uid_map`
(non-zero host-start field) and skips both the privilege drop (`runuser`)
and the `/work` chown (which would reassign ownership into the subuid
range, breaking host-side access). The cgroup delegation chowns are
best-effort (`|| true`) and harmlessly fail. The orchestrator runs as
the mapped root user, which has the same access as the host user.

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
the cgroupfs mount blocks non-root process migration to `cgroup.procs`
— the kernel treats the namespace boundary as a delegation boundary,
causing every `_cgroup_enroll` call to fail with EPERM regardless of
file ownership. With `--cgroupns=host`, the container sees its real
cgroup path (e.g., `/system.slice/nerdctl-<id>.scope`) and the
orchestrator can enroll worker PIDs into `leerie.slice/` children
normally. Fly's Firecracker microVM boots its own kernel with no
cgroup namespace boundary, so this flag only affects the local nerdctl
path.
On macOS (Darwin) the launcher sets the mount unconditionally —
Colima's VM always runs rootful containerd with cgroup v2 and shared
propagation, but the macOS host has no `/sys/fs/cgroup` to probe.
On Linux the launcher probes whether `/sys/fs/cgroup` is shared before adding this
mount; rootless containerd with `rootlesskit --propagation=rslave`
demotes it to slave, making `rshared` fail. When the probe detects
this, the mount is omitted and the orchestrator's `_cgroup_probe` falls
back to uncapped workers with one warn line. Fly's Firecracker microVM
exposes cgroupfs directly with no launcher flag required.

The orchestrator's `_detect_cgroup_root` prefers
`/sys/fs/cgroup/leerie.slice` and falls back to `/sys/fs/cgroup`.
`_cgroup_probe` runs a two-phase check: (1) create and remove a child
cgroup directory to verify write access, then (2) spawn a short-lived
subprocess and write its PID to `cgroup.procs` to verify that process
migration actually works. Phase 2 catches the `nsdelegate` +
`--cgroupns=private` failure that directory creation alone cannot
detect. If either phase fails, the probe degrades to uncapped behavior
with one warn line naming the attempted root and the likely cause.
The teardown path uses `cgroup.kill` (kernel ≥ 5.14) as
an atomic kill of any worker-subtree process that survived the
existing `_terminate_proc_tree` proc-walk — a backstop, not the
primary cleanup. See IMPLEMENTATION.md §"Caps" for the resolution
surface and `_cgroup_*` / `_detect_cgroup_root` in
`orchestrator/leerie.py` for the call sites.

Earlier versions of leerie gave Ctrl-C an explicit "throw this away"
semantic with a full purge of state + branches + run dir. That made
accidental Ctrl-C catastrophic — and it conflated user intent ("stop
this run") with run lifecycle ("nuke the artifacts"). The two are
now separate: Ctrl-C stops; `scripts/cleanup.sh --run-id <id>
--branches` is the explicit full-purge gesture.

**Rate-limited (RateLimitedExit) → auto-resume after the reset
window.** When `claude -p` reports the subscription session-limit
hit (delivered as assistant-text content in the verbatim format
`"You've hit your session limit · resets <time> (<tz>)"`, or as a
`rate_limit_event` whose `status` field reports a terminal value
— anything outside the known-allowed set
`{"allowed", "allowed_warning"}`), leerie raises
`RateLimitedExit(reset_at, raw)`.
The exception propagates through the existing asyncio cancellation
chain — `_invoke`'s `BaseException` guard terminates the in-flight
`claude -p` worker's full subprocess subtree (including detached
backgrounded tool subprocesses) and reaps it, sibling wave-tasks
cancel through the same path — so no orphan subprocesses remain (the
per-worker async cleanup is the fast happy path; the container
PID-namespace teardown is the abnormal-exit guarantee — see "Worker
subtree termination — kernel-enforced via the container boundary"
above). Then:

- If `reset_at` was parsed cleanly from the literal Claude Code
  message format, leerie runs the worktree-only cleanup, sleeps until
  the reset moment + a small margin, then `os.execv`'s the
  orchestrator itself (`sys.executable __file__ --resume --run-id <id>`)
  to start a fresh orchestrator process. We re-exec the orchestrator,
  not the launcher: the orchestrator already runs inside the
  container with state on disk, the launcher is not baked into the
  container image, and the launcher's `--resume` path would try to
  launch a new container (no inside-container sentinel exists).
  The `--max-workers` budget persists across the re-exec —
  `worker_count` lives in state.json — so a run that repeatedly hits
  the rate-limit still respects the user's cap.
- If the reset clause didn't parse (malformed time, unknown
  timezone, or Anthropic changed the message format), leerie runs
  the worktree-only cleanup, prints the literal message and the
  manual resume command, and exits with code 75 (`EX_TEMPFAIL`).

The auto-resume path is opt-in by message format: we only sleep
when the reset time is unambiguously parseable. A parse failure
must never produce a wrong-time sleep — the user gets a clean
manual-resume instruction instead.

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
(full reap), or *leave alone* (the user merely detached the local
stream — the orchestrator on the machine is still working). With the
detached orchestrator above, the classification is no longer "how did
the orchestrator's run exit?" but "what just happened on the host
side?" — because the launcher process now exits when the *tail*
finishes, not when the orchestrator finishes. The reclassified table:

| Exit | Meaning | Disposition |
|---|---|---|
| `0` | tail saw orchestrator exit cleanly (or could not read exit code) | destroy after stream-back |
| `EXIT_NEEDS_ANSWERS=10` | clarification (plugin re-runs) | destroy (nothing to inspect) |
| `75` (EX_TEMPFAIL) | rate-limit, parse-fail | destroy (state in run branch; cheaper to re-provision) |
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
shared `_auto_detect_fly_runtime` helper. Without this, the default
`local` runtime either starts a container with an empty host-side
`state.json` (`--resume`) or rejects the command with "no
local-runtime equivalent" (`--stop`/`--kill`/`--finalize`). If the
user explicitly sets `--runtime local` on a Fly-originated run,
`--resume` warns but respects the choice; the fast-path verbs reject
it as before.

**Smart resume in remote mode.** `--resume` is the single verb for
re-engaging with a remote run, regardless of the run's current state.
The launcher reads observed state and routes to the right behavior:

| Machine state | Orchestrator state | `--resume` behavior |
|---|---|---|
| Stopped (paused) | n/a | Wake machine → re-seed → launch orchestrator → tail |
| Running | Dead | (Re-)seed if needed → launch orchestrator → tail |
| Running | Alive | Skip launch → tail orchestrator.log |

The "machine running, orchestrator alive" branch is the §6 isolation
boundary's terminal-side surface — not a new privileged channel.
Detection: the launcher pipes a launch wrapper through `flyctl ssh
console -C "python3 -"`; the wrapper takes a fast-path flock probe on
the run directory (DESIGN §6 *Single owner per run dir*) and exits 75
if the lock is held. **flyctl exit-code limitation:** `flyctl ssh
console` does not forward the remote process's exit code — it returns
1 for any non-zero remote exit. The actual code appears only in
stderr (`Error: ssh shell: Process exited with status <N>`). The
launcher captures stderr and parses the real remote code via
`_extract_flyctl_remote_rc` (lib.sh) so the rc=75 pivot fires
correctly. The launcher's rc=75 branch pivots to attach
behavior instead of launching a duplicate. The attach channel itself
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
The **classifier, planner, reconciler, plan_overlap_judge, provision, and integrator** have no partial-progress
artifact to hand off — there is nothing for a successor to continue from — so
their hard failure aborts the run with state saved for `--resume`. The
**conformer** has commits but its phase is advisory, so a hard failure surfaces
as a warning, not an abort. The rule is general: salvage if there is something
to salvage; abort cleanly otherwise. When `planner_samples > 1`, a crashed
sample is dropped and the surviving samples for that domain proceed to
selection; the abort fires only when all samples for a domain fail.

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
simply contribute nothing. The all-blocked case still dies — a blocker
is a gate failure that the user must see.

The structural contract of these disciplines is mechanically enforced — the
worker's output schema requires the falsification, reconciliation, and gap
fields to be present, so a worker that skipped them fails its own JSON gate
before the orchestrator ever reads it (see §12). The *quality* of the
artifacts each field names is model-judged; the *presence* of the discipline
is not.

**Confidence is the only load-bearing gate.** The implementer's
`root_cause` / `solution` scores (and the planner's `task_understanding`
/ `decomposition_quality`) are the only signals the orchestrator
escalates to `failed` or `blocked` on. Tests passing, lint clean, build
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

---

## 13. Caps and escalation

Every loop in the system has a hard bound. Nothing spins forever; when a bound
is reached, Leerie escalates rather than looping. But the bounds are of **two
different kinds**, and the difference is itself a design point — it is the §12
principle applied to caps.

### Code-enforced caps

Some caps are counted by the orchestrator: the number of subtask continuations
for a subtask, the number of corrective retries, re-validation rounds per wave,
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
the classifier dies, the integrator aborts the merge.

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

Every LLM call in Leerie passes through one of the eight worker types in
`WORKER_TYPES`: `classifier`, `planner`, `reconciler`, `plan_overlap_judge`,
`provision`, `implementer`, `integrator`, or `conformer`. Each worker type is a distinct **call type** — a
first-class identifier that partitions every captured call into its role in the
system. The call_type partition is exactly `WORKER_TYPES`: one call_type per
worker role, no overlap, no gap.

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
- **Parallelism is single-clone.** Multiple concurrent runs in the same git
  clone are explicitly supported via the per-run state and branch design.
  Multiple clones running concurrently are also fine — they are independent
  by construction — but the per-run namespacing applies only within one
  clone; leerie does nothing to coordinate across clones (it has no need
  to).
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
