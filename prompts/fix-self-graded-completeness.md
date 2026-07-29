# Replace self-graded confidence with independent adversarial verification

## Context

Running the same leerie task two or three times keeps finding more work. This is NOT
because the tasks are unbounded, and it is NOT fixed by a re-run loop, a completeness
gate, or a "tests must pass" gate. The real root cause — proven against real run
transcripts and merged PRs — is that **leerie's workers grade their own output
against evidence they themselves select, and that evidence is systematically shallow,
so real gaps ship as "complete" and every re-run discovers the gaps the last run's
self-grading left behind.**

This is a bug in the leerie orchestrator itself (this repo). Read `CLAUDE.md`,
`docs/DESIGN.md` §8 (the confidence gate) and §9 (post-work conformance), and
`docs/IMPLEMENTATION.md` before touching anything. Follow the three-layer rule
(DESIGN → IMPLEMENTATION → code, highest-affected-layer first) and "prompts advisory,
code enforces." This change reverses part of a documented §8/§9 decision, so it is
DESIGN-first: update DESIGN.md, then IMPLEMENTATION.md, then code.

## The proven root cause

DESIGN §8 claims the confidence gate "cannot be cheated by lowering a bar," and that
structural enforcement is "limited to 'did the worker fill in the self-gate fields at
all,' not 'is the model's score correct.'" That limitation IS the hole.

**Evidence 1 — the implementer.** A real subtask (`bugfix-002`, "route three
observe-blind call sites through the deep-locator resolver") scored itself
`root_cause 9.5 / solution 9.5 / gap_to_close {}` — maximal confidence, no gaps. But
all four of its `falsifiers_tested` entries were about test-plumbing ("my new tests
pass", "no regression in the existing suite", "the fake fixture change doesn't break
other tests"). NONE tested the actual behavior. It shipped three latent defects
(clicked index-0 decoy instead of ranking candidates; an invalid selector; a missing
safety guard) that two later re-runs of the SAME task had to fix. The implementer
prompt (`prompts/implementer.md:153–160`) ALREADY commands maximal adversarial
falsification ("explicitly look for evidence that would disprove it… A claim earns ≥
9.0 only when its falsifier was tested and failed"). The implementer FOLLOWED that
instruction and still missed the gaps.

**The precise defect is SELF-grading, not missing-adversarial-grading.** Adversarial
falsification run by the SAME worker that wrote the solution is structurally
incapable of finding that worker's own blind spots: you cannot disprove a failure
mode you cannot conceive, and the mind that produced the incomplete solution bounds
the failure modes it can imagine. Adversarial self-grading is a contradiction in
exactly the cases that matter. Adding or strengthening adversarial instructions will
NOT fix this — that already exists and failed. The fix is to change WHO runs the
check: move it to an independent party with no shared blind spot.

**The precedent already in the codebase.** DESIGN §8 already solved this once, for
the planner: the planner's `decomposition_quality` self-score "is retained as
advisory but no longer gates — the independent `fit_judge` is the authoritative
decomposition-quality gate." `fit_judge` works not because it is "more adversarial"
than the planner's own self-check, but because it is a SEPARATE worker that did not
produce the decomposition, so it sees cuts the planner was blind to. Use `fit_judge`
as the exact template.

## The defect is a CLASS — every self-grader with no independent gate

Audit of `_confidence_schema` self-graders in `orchestrator/leerie.py`. Five workers
self-grade with NO independent check; each has a REAL run where the self-graded ~9/10
confidence masked a defect that caused a bad outcome:

| Worker | Self-score axis | Proven harm |
|---|---|---|
| **implementer** | `solution` | `bugfix-002`: 9.5/9.5, shallow falsifiers, shipped 3 latent defects re-fixed by later runs |
| **classifier** | `classification` | Same task classified `[bug-fixing,testing]` → 10 subtasks in one run vs `[docs,feat,infra]` → **0 subtasks** in another (accomplished NOTHING); "Landing Page Feature Pillars" classified `documentation` (shipped only `.md`) vs `feature-implementation` (shipped a full landing page) |
| **reconciler** | `reconciliation` | A tag-channel dangle survived reconciliation → `validate_plan` die() at phase 3 → **zero output** after full planner+reconciler spend |
| **provision** | `recipe_correctness` | Recipe self-graded 9.3 but omitted `--break-system-packages` → **12 real install failures**; 28 runs got the broken recipe and 20 got the correct one for the SAME repo, both self-graded correct |
| **conformer** | `conformance` | Self-grades, advisory, unchecked — this is the vehicle for the implementer fix (below), not separately harmed |

The planner is the one already fixed (independent `fit_judge` + `plan_overlap_judge`
+ `adherence_judge`). The integrator has a partial deterministic check (`<<<<<<<`
marker scan + merge-completed verification) — leave it as-is unless trivially
improved.

## Required fix — an independent adversarial verifier for each of the 5

For each of the five ungated self-graders, add an INDEPENDENT verifier following the
`fit_judge` template: a separate `claude -p` worker that did NOT produce the artifact,
is handed only the artifact plus the task, and is told to ATTACK it — find concrete
unhandled inputs / paths / cases / mis-wirings. Its findings authoritatively GATE, and
the original worker's self-score is demoted to advisory (exactly as the planner's
`decomposition_quality` was demoted).

Specifics:

1. **Implementer's `solution` — REUSE and re-scope the CONFORMER.** The conformer
   already runs independently after the implementer reports `complete` (it did NOT
   write the code). Extend its remit to adversarially verify SOLUTION COMPLETENESS —
   attack the implementer's diff, find inputs/paths/cases it does not handle (the
   decoy click, the error path, the exit-guard, the sibling data-path site) — and
   make THAT axis GATING. Its existing drift/docs/rules job stays advisory. Wire the
   gate into `settle_subtask` (`leerie.py:19297`), after the implementer returns and
   before `complete` is honored — structurally identical to where `fit_judge` gates
   the planner. Found gaps become mandatory work (retry the subtask with the gaps as
   additional required criteria), not optional warnings.

2. **classifier** — add an independent verifier of the classification against the
   task + codebase (does the category set cover the actual work the task requires,
   and does it exclude none that it needs?). Gate on found miscategorization.

3. **reconciler** — add an independent verifier of the reconciled wiring: no
   surviving subtask `requires` a tag that no in-plan subtask `provides` (the
   tag-channel dangle). NOTE (important scope): the dangle in the proven harm case is
   introduced by the `satisfied_probe` drop, not only the reconciler — so the
   verifier must cover the drop→rewire seam (every id-vanishing / tag-vanishing
   operation must leave the plan wired), not merely re-grade the reconciler's own
   output. This overlaps the known "satisfied-probe tag-channel dangle" bug — fix
   both together: every operation that removes a subtask id/tag owes the plan a
   rewrite of inbound `depends_on` AND `requires` references.

4. **provision** — add an independent verifier of the detected recipe against the
   actual image/runtime (e.g. does a `pip install` recipe carry
   `--break-system-packages` on the externally-managed Debian image? does the
   detected package manager match the lockfiles actually present?). Gate on a recipe
   that would fail.

## CRITICAL design constraint — do NOT reintroduce a gameable bar (DESIGN §9)

The conformer was made advisory DELIBERATELY: DESIGN §9 removed an earlier
criteria-lock + hard gate because "any code-enforced 'tests must pass' gate invites a
stuck model to weaken the test rather than fix the code." Your new gating axes must
NOT resurrect that. The resolution is the INDEPENDENCE itself:

- The old gate was gameable because the SAME worker controlled the bar (it authored
  the criteria and could lower them, or weaken a test).
- An INDEPENDENT verifier that (a) did not write the artifact and (b) gates on "here
  is a concrete input/path this artifact mishandles" — NOT on "a test passed" or "the
  criteria say met" — cannot be gamed: there is no bar for the graded worker to
  lower, and you cannot weaken a test to defeat a verifier that constructs new
  adversarial inputs.
- Therefore every one of the five gates must gate on **independently-found concrete
  defects in the artifact's behavior**, never on a self-assertable or lowerable
  signal. This is exactly why `fit_judge` gates the planner without being gameable.

## Worker conventions (from CLAUDE.md — mandatory)

- Each new verifier is a JUDGMENT worker → it MUST be absent from
  `MODEL_DEFAULT_PER_WORKER` (so it defaults to `opus` via `MODEL_DEFAULT`) and carry
  `EFFORT_DEFAULT_PER_WORKER = "high"`. Justify any exception in a comment.
- Each new worker type needs a schema in the `SCHEMAS` dict, passed via `--json-schema`
  in `claude_p()`, and a prompt file in `prompts/`.
- All NL interpretation returns schema-validated structured JSON — never regex over
  prose. Python operates only on already-structured fields.
- All run state goes through the `State` class. Caps are real `DEFAULT_CAPS` counters.
  Respect the worker budget (`st.bump_workers`).

## Tests

Mirror the existing judgment-worker + gate test families:
`tests/test_fit_judge_schema.py`, `tests/test_phase_adherence_gate.py`,
`tests/test_check_functions.py` (advisory-vs-gating split),
`tests/test_resolve_fit_judge_model.py` (model/effort resolution),
`tests/test_remap_vanished_deps.py` / `tests/test_filter_satisfied_subtasks.py` (the
tag-channel rewrite). For each new verifier: schema, model/effort defaults (opus +
high), wiring into its gate seam, a case where the verifier finds a real defect and
gates, and a clean case that passes without a false gate. Add a regression fixture
per proven-harm case above (the shallow-falsifier implementer diff; the 0-vs-10
classification; the tag-channel dangle; the `--break-system-packages`-omitting
recipe).

## Definition of done (verify each — and note the irony: do not self-certify)

- The five self-graded axes (implementer.solution, classifier, reconciler, provision,
  conformer-as-vehicle) are each independently gated; the self-scores are advisory.
- Each proven-harm regression fixture now FAILS the gate (the verifier catches it)
  where it previously shipped `complete` / high-confidence.
- No new gate can be cleared by a self-assertable signal (no "tests pass" / "criteria
  met" gate) — demonstrate by showing the gate keys on an independently-constructed
  defect, per the §9 constraint.
- `pytest tests/` passes; `python3 -c "import ast; ast.parse(open('orchestrator/leerie.py').read())"`
  passes; DESIGN.md §8/§9 and IMPLEMENTATION.md are updated to describe the new
  independent-verification discipline BEFORE the code; `git diff --stat` is scoped.
