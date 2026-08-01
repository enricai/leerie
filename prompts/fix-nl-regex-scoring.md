# Migrate the remaining natural-language regex parsing to LLM + schema-JSON

## Context

CLAUDE.md is emphatic: **"All natural-language interpretation is done by an LLM worker
returning schema-validated structured JSON — never by regex or hand-parsing in Python."**
Python operates only on already-structured data (JSON fields, typed values). Regex is
permitted **only** on *mechanical* strings (semver, shell commands, fixed CLI output,
file paths) — never on task text, planner/worker prose, README/markdown content, or an
LLM's response.

An audit (2026-07, extended 2026-08-01) found several pre-existing sites in
`orchestrator/leerie.py` that regex or hand-parse **prose / markdown / planner output** —
genuine violations of this rule. They live in load-bearing, heavily-tested incident-fix
code, and so were spun out here rather than fixed inline.

Read `CLAUDE.md` (the rule + the `TestRegexPathAbsent` prior art in
`tests/test_capture_deps.py` — dep-capture's own migration off a regex path onto
LLM-structured output is the template for this whole task), `docs/DESIGN.md`
§"Language-to-JSON: natural-language interpretation is never regex", and
`docs/IMPLEMENTATION.md`. DESIGN-first; "prompts advisory, code enforces."

## The violations to migrate

| Site (`orchestrator/leerie.py`) | What it parses | Why it's a violation |
|---|---|---|
| `extract_task_file_structure` | `re.finditer` over `.md`/`.txt`/`.yaml` **file content** (CLAUDE.md, README, task-referenced files) to harvest headings / numbered items / YAML keys as "coverage items" | Regex over markdown/prose content |
| `_is_uncoverable_convention_item` (`_BACKTICK_SPAN_RE`, `re.search(r'\bMUST\b')`) | the harvested heading **prose** | Regex over prose to classify it |
| `check_planner_output`'s migration surface (`_MIGRATION_SIGNAL_RE`) | the **planner's `intent` / `investigation_notes` prose** ("replaces direct `X`", "extract `X` as the new seam") | Regex over an LLM worker's prose output — the most direct violation; DESIGN §5 even documents it as "regex-detected phrases". Observed live extracting English stopwords as symbol names (`with` → 332 files, `both` → 178, `task` → 168) and burning planner rounds on the resulting false `UNCOVERED_MIGRATION_SURFACE` warnings |
| `gather_provision_fixtures` (`_README_SECTION_RE`, `_HEADER_DECOR_RE`) | **README section headings** | Regex over markdown headings. Softer: this is a *pre-filter feeding the provision LLM* (the worker still decides), but still regex over prose |
| `check_overlap_judge_output`'s `_depunctuate` / `_path_shaped` (PHANTOM_ARTIFACT) | the **overlap judge's `artifact` field** — whitespace-tokenized, punctuation- and possessive-stripped, then each token tested for path shape | Hand-parsing an LLM's response. Not regex, but the same rule: `artifact` is a free-text logical name the prompt asks for (`AuthShell component`), and Python is mining paths out of it |

Explicitly NOT in scope (these are permitted mechanical-string regex — leave them):
semver (`re.match(r"(\d+)\.(\d+)\.(\d+)"`, `_GO_MOD_VERSION_RE`), shell/argv install
shapes (`_DEPCAP_*`, the BLT command regexes), fixed CLI output (`_BG_ID_RE`,
`_SESSION_LIMIT_*`, `_LEERIE_PREFIX_RE`), toml `key =` lines, the `{{include:}}` prompt
fragment, workflow filenames.

## Required fix — structured JSON per site

For each violating site, replace the prose parsing with schema-validated structured
output that Python then operates on — following the dep-capture migration precedent
(`TestRegexPathAbsent` pins the deleted regex symbols so the regex path can never
silently return). Two shapes are available and the cheaper one is usually right:

- **Ask the worker that already runs** to emit one more structured field. No new worker,
  no extra spend.
- **Add a new worker** only where no existing worker owns the data.

Per site:

- **Task-file structure harvest** (`extract_task_file_structure` +
  `_is_uncoverable_convention_item`): a new worker that reads the task-referenced files
  and returns a structured list of coverage items (each with a `coverable` bool it
  decides, replacing the `\bMUST\b`/backtick heuristic) — Python then does set/subset
  comparison against the plan, never parsing headings itself.
- **Migration surface** (`_MIGRATION_SIGNAL_RE`): the planner already produces `intent` /
  `investigation_notes` as an LLM. Surface the signal as a **structured field on the
  planner's own schema** (e.g. `migration_targets: [{old_pattern, ...}]`), so Python reads
  a JSON field instead of regexing prose. No new worker.
- **README section pre-filter** (`_README_SECTION_RE`): fold into the provision worker's
  own reading of the README (it already reads fixtures) — let the LLM identify the
  install-relevant sections rather than a keyword regex pre-selecting them.
- **PHANTOM_ARTIFACT path extraction** (`_depunctuate` / `_path_shaped`): add an
  `artifact_paths: [string]` field to `SCHEMAS["plan_overlap_judge"]`'s collision object
  and have the judge name the repo-relative paths explicitly, keeping `artifact` as the
  prose label. `check_overlap_judge_output` then compares `artifact_paths` against
  `files_likely_touched` / the tree as plain set membership. Delete both helpers. No new
  worker.

New workers follow the CLAUDE.md conventions (**sonnet** — `MODEL_DEFAULT = "sonnet"`, so
a new judgment worker should stay absent from `MODEL_DEFAULT_PER_WORKER` rather than
being pinned to a different model — `EFFORT_DEFAULT_PER_WORKER = "medium"`,
`WORKER_TYPES`, `SCHEMAS`, prompt file, `INSPECT_TOOLS`). Any check that needs a fact from
natural language must get it as a JSON field from the owning worker.

## How to decompose this — read before planning

**Each site is ONE subtask that owns its code change, its tests, and its documentation.**
Do not split a site's work across a code subtask, a test subtask, and a docs subtask. A
subtask is done when its site is migrated, its tests are rewritten and passing, and the
DESIGN/IMPLEMENTATION rows describing that site are updated.

This is not a style preference. Planners for different categories run blind and in
parallel: a testing-domain subtask cannot declare a `requires` on a capability tag the
refactoring planner has not invented yet, and vice versa. Splitting one site's work
across domains manufactures cross-domain dependencies that no planner can wire, and the
plan-wiring gate then has to repair or reject them.

Concretely:

- **No separate `testing` subtasks.** The subtask that deletes `_MIGRATION_SIGNAL_RE` is
  the same subtask that rewrites `tests/test_migration_surface.py`.
- **No separate `documentation` subtasks.** The subtask that changes a site updates the
  DESIGN/IMPLEMENTATION prose for that site, in the same change.
- **No cross-cutting "verify the whole suite passes" subtask.** Every subtask runs
  `pytest tests/` as part of its own success criteria. A standalone final verifier
  depends on every other subtask's tests being finished, which is exactly the
  un-wireable shape above.
- The five sites are **independent of each other** — they touch different functions and
  different test files. Expect a flat plan with no inter-subtask dependencies, not a
  chain.

## Constraints / risk

These are load-bearing incident fixes — do not regress them:

- `check_task_file_coverage` is the 2026-07-19 coverage-freeze fix
  (`test_incident_2026_07_19.py`, `test_task_file_coverage_freeze.py`,
  `test_task_file_extraction.py`). The gate must still fire on the incident shape and
  stay silent on the uncoverable-convention shape after the migration.
- The migration-surface check (DESIGN §5 *Migration-surface completeness*) has its own
  tests. Preserve the `UNCOVERED_MIGRATION_SURFACE` behavior via the new structured field.
- PHANTOM_ARTIFACT has a live regression suite in `tests/test_phase_overlap_judge.py`
  covering descriptive artifact names, multi-path names, pure logical names, and invented
  paths inside prose. All of it must still pass against `artifact_paths`; the judge's
  prompt must be updated to populate the new field or every collision reads as pathless.

## Tests

Add a `TestRegexPathAbsent`-style guard per migrated site (assert the deleted symbols no
longer exist on the module, so the prose-parsing path can never silently return), plus
schema/wiring tests for each new worker or new structured field, and preserve every
existing incident/behavior test — they must pass against the LLM-structured path.

## Definition of done

- No regex or hand-parsing in `orchestrator/leerie.py` operates on task text,
  planner/worker prose, README/markdown content, or an LLM response (grep + per-site
  absence guards).
- Every NL fact a Python check needs is a JSON field from a schema-validated worker.
- `pytest tests/` green (incl. the incident regression tests), verified within each
  subtask rather than by a separate one.
- DESIGN/IMPLEMENTATION updated first, per the three-layer rule.
