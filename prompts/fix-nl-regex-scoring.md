# Migrate the remaining natural-language regex parsing to LLM + schema-JSON

## Context

CLAUDE.md is emphatic: **"All natural-language interpretation is done by an LLM worker
returning schema-validated structured JSON — never by regex or hand-parsing in Python."**
Python operates only on already-structured data (JSON fields, typed values). Regex is
permitted **only** on *mechanical* strings (semver, shell commands, fixed CLI output,
file paths) — never on task text, planner/worker prose, README/markdown content, or an
LLM's response.

An audit (2026-07) found several pre-existing sites in `orchestrator/leerie.py` that
regex over **prose / markdown / planner output** — genuine violations of this rule. They
predate and are unrelated to the self-graded-completeness work, live in load-bearing,
heavily-tested incident-fix code, and so were spun out here rather than fixed inline.

Read `CLAUDE.md` (the rule + the `TestRegexPathAbsent` prior art in
`tests/test_capture_deps.py` — dep-capture's own migration off a regex path onto
LLM-structured output is the template for this whole task), `docs/DESIGN.md`
§"Language-to-JSON: natural-language interpretation is never regex", and
`docs/IMPLEMENTATION.md`. DESIGN-first; "prompts advisory, code enforces."

## The violations to migrate

| Site (`orchestrator/leerie.py`) | What it regexes | Why it's a violation |
|---|---|---|
| `extract_task_file_structure` | `re.finditer` over `.md`/`.txt`/`.yaml` **file content** (CLAUDE.md, README, task-referenced files) to harvest headings / numbered items / YAML keys as "coverage items" | Regex over markdown/prose content |
| `_is_uncoverable_convention_item` (`_BACKTICK_SPAN_RE`, `re.search(r'\bMUST\b')`) | the harvested heading **prose** | Regex over prose to classify it |
| `check_planner_output`'s migration surface (`_MIGRATION_SIGNAL_RE`) | the **planner's `intent` / `investigation_notes` prose** ("replaces direct `X`", "extract `X` as the new seam") | Regex over an LLM worker's prose output — the most direct violation; DESIGN §5 even documents it as "regex-detected phrases" |
| `gather_provision_fixtures` (`_README_SECTION_RE`, `_HEADER_DECOR_RE`) | **README section headings** | Regex over markdown headings. Softer: this is a *pre-filter feeding the provision LLM* (the worker still decides), but still regex over prose |

Explicitly NOT in scope (these are permitted mechanical-string regex — leave them):
semver (`re.match(r"(\d+)\.(\d+)\.(\d+)"`, `_GO_MOD_VERSION_RE`), shell/argv install
shapes (`_DEPCAP_*`, the BLT command regexes), fixed CLI output (`_BG_ID_RE`,
`_SESSION_LIMIT_*`, `_LEERIE_PREFIX_RE`), toml `key =` lines, the `{{include:}}` prompt
fragment, workflow filenames.

## Required fix — an LLM worker returning schema-JSON per site

For each violating site, replace the regex-over-NL with a JSON-schema-validated LLM
worker whose output Python then operates on structurally — following the dep-capture
migration precedent (`TestRegexPathAbsent` pins the deleted regex symbols so the regex
path can never silently return):

- **Task-file structure harvest** (`extract_task_file_structure` +
  `_is_uncoverable_convention_item`): a worker that reads the task-referenced files and
  returns a structured list of coverage items (each with a `coverable` bool it decides,
  replacing the `\bMUST\b`/backtick heuristic) — Python then does set/subset comparison
  against the plan, never parsing the headings itself.
- **Migration surface** (`_MIGRATION_SIGNAL_RE`): the planner already produces `intent` /
  `investigation_notes` as an LLM. Surface the migration signal as a **structured field
  on the planner's own schema output** (e.g. `migration_targets: [{old_pattern, ...}]`),
  so Python reads a JSON field instead of regexing the prose. This is the input-side
  companion to "planner returns schema-validated JSON."
- **README section pre-filter** (`_README_SECTION_RE`): fold into the provision worker's
  own reading of the README (it already reads fixtures) — let the LLM identify the
  install-relevant sections rather than a keyword regex pre-selecting them.

Judgment workers follow the CLAUDE.md conventions (opus, `EFFORT_DEFAULT_PER_WORKER =
"medium"`, `WORKER_TYPES`, `SCHEMAS`, prompt file, `INSPECT_TOOLS`). Any check that needs
a fact from natural language must get it as a JSON field from the owning worker.

## Constraints / risk

These are load-bearing incident fixes — do not regress them:
- `check_task_file_coverage` is the 2026-07-19 coverage-freeze fix (`test_incident_2026_07_19.py`,
  `test_task_file_coverage.py`). The gate must still fire on the incident shape and stay
  silent on the uncoverable-convention shape after the migration.
- The migration-surface check (DESIGN §5 *Migration-surface completeness*) has its own
  tests. Preserve the `UNCOVERED_MIGRATION_SURFACE` behavior via the new structured field.

## Tests
Add a `TestRegexPathAbsent`-style guard per migrated site (assert the deleted regex
symbols no longer exist on the module, so the NL-regex path can never silently return),
plus schema/wiring tests for each new worker or new structured field, and preserve every
existing incident/behavior test (they must pass against the LLM-structured path).

## Definition of done
- No regex in `orchestrator/leerie.py` operates on task text, planner/worker prose,
  README/markdown content, or an LLM response (grep + per-site absence guards).
- Every NL fact a Python check needs is a JSON field from a schema-validated worker.
- `pytest tests/` green (incl. the incident regression tests); DESIGN/IMPLEMENTATION
  updated first.
