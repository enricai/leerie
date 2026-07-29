# Replace the last three self-score gates with independent adversarial verifiers

## Context

`prompts/fix-self-graded-completeness.md` established the principle and shipped it for
five self-graders: **wherever a worker gates on grading ITSELF (a confidence self-score
from the same `claude -p` that produced the work), replace that with an INDEPENDENT
adversarial verifier — a separate `claude -p` that did not produce the artifact, is
handed only the artifact plus the task, and is told to ATTACK it, gating on
independently-found concrete defects.** A worker cannot disprove a failure mode it cannot
conceive, and the mind that produced an incomplete artifact bounds the failure modes it
can imagine — so a self-score is structurally blind to the worker's own gaps.

That change converted four of the eight self-score gates (implementer → conformer
`solution_defects`; classifier → `classification_judge`; reconciler →
`check_plan_wiring` + `wiring_judge`; provision → `provision_judge`) and REMOVED the
now-redundant self-score gate at each of those four sites. **This spec finishes the
job:** three self-score gates remain, each with NO independent verifier yet. Until they
are converted, "no self-scoring anywhere" is not true.

Read `CLAUDE.md`, `docs/DESIGN.md` §8 *Independent adversarial verification* (the
established discipline), and `docs/IMPLEMENTATION.md`. This is DESIGN-first: DESIGN.md →
IMPLEMENTATION.md → code → prompts → tests. Follow "prompts advisory, code enforces."

## The three remaining self-score gates

Each still gates on `_confidence_issues(output.get("confidence"), [<axis>])` inside its
check function (grep `_confidence_issues` — after `fix-self-graded-completeness.md` exactly
three callers remain):

| Worker | Site | Self-axis | What an independent verifier must attack |
|---|---|---|---|
| **planner** | `check_planner_output` | `task_understanding` | Does the plan actually address the task the user asked for — is any required piece of work missing, or is any subtask off-task? (`decomposition_quality` is already demoted → `fit_judge`; only `task_understanding` remains.) |
| **plan_overlap_judge** | `check_overlap_judge_output` | `judgment` | This worker IS an adversarial judge grading its OWN judgment — the purest self-scoring case. It already has deterministic validators (`PHANTOM_ARTIFACT`, `NO_FILE_OVERLAP`, `DROP_BREAKS_GRAPH`, `_validate_overlap_judge_output`). Decide whether an independent second-opinion pass adds value or whether the deterministic validators alone are the authoritative gate (and the self-score is simply dropped). |
| **integrator** | `check_integrator_output` | `resolution` | Did the merge the integrator produced actually resolve the conflict correctly — behaviorally, not just "no `<<<<<<<` markers left"? It already has a partial deterministic check (conflict-marker scan + merge-committed verification). An independent verifier attacks the merged result for behavioral breakage the marker scan cannot see. |

## Required fix — one independent verifier per worker, then remove the self-gate

For each, follow the `adherence_judge` template EXACTLY (it is the canonical independent
verifier — see `SCHEMAS["adherence_judge"]`, `prompts/adherence_judge.md`,
`phase_adherence_gate`, and the three `classification_judge`/`wiring_judge`/`provision_judge`
verifiers `fix-self-graded-completeness.md` already shipped):

- New judgment worker: absent from `MODEL_DEFAULT_PER_WORKER` (→ opus via `MODEL_DEFAULT`),
  `EFFORT_DEFAULT_PER_WORKER = "medium"` (per commit `557c12b` — NOT "high"), in
  `WORKER_TYPES`, schema in `SCHEMAS` passed via `--json-schema`, prompt in `prompts/`,
  `INSPECT_TOOLS` + `autonomous=False`, `st.bump_workers(caps)` before the call.
- **Carries NO `_confidence_schema`** — it is itself the independent check; a nested
  self-confidence axis would reintroduce the bias.
- Gates on a **non-empty array of concretely-named found defects** (each item carrying a
  concrete case + where/why), never on a numeric score — the §9 anti-gaming property.
  Drop any defect lacking the concrete field (mirror `actionable_solution_defects` /
  the `_check` closures in the three shipped gates).
- Wire it into the right seam and **remove the `_confidence_issues([<axis>])` line** from
  that worker's check function (as `fix-self-graded-completeness.md`'s C1 did for the
  other three), so the independent verifier is the sole gate. Leave the `confidence`
  object emitted (the §8 falsifier/gap discipline record stays — do NOT touch it).
- Decide detect-and-die vs re-drive per gate: a gate whose owning worker can mechanically
  act on the feedback should re-drive (like the classifier gate re-drives `phase_classify`);
  a gate whose worker cannot mechanically fix the found defect should be detect-and-die
  single-pass (like the wiring/provision gates — see `fix-self-graded-completeness.md`'s
  F1/F2). The planner CAN re-plan on a task-coverage gap (re-drive `phase_plan`); the
  overlap-judge and integrator likely cannot mechanically fix a semantic finding
  (detect-and-die, or feed a bounded re-attempt).

## Tests

Mirror the shipped verifier test families exactly (see
`tests/test_classification_judge_schema.py`, `tests/test_phase_wiring_gate.py`,
`tests/test_resolve_new_verifier_models.py`, `tests/test_advisory_vs_gating.py`,
`tests/test_check_functions.py`'s `TestDemotedSelfScoresDoNotGate`): for each new
verifier — schema (no `_confidence_schema`), model/effort defaults (opus + medium),
gate wiring, a defect case that gates, a clean case that passes, an anti-gaming
vague-defect-does-not-gate case; plus an advisory-vs-gating test asserting the worker's
own self-score no longer gates (invert its current `test_*_low_confidence` in
`test_check_functions.py`, mirroring what was done for classifier/reconciler/provision).

## Definition of done
- `_confidence_issues` has ZERO callers (grep) — no worker gates on its own confidence.
- Each of planner/overlap-judge/integrator has an independent verifier that is the
  authoritative gate; each self-score is advisory (still emitted, never gated).
- `pytest tests/` green; `ast.parse` passes; DESIGN §8/§9 + IMPLEMENTATION.md updated
  before the code; the `confidence` objects/schemas are UNCHANGED (only gates moved).
