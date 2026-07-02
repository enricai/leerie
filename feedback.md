# Leerie User Feedback

Organized from raw feedback by Garry (Digisynd), a power user running
leerie against a Rails/web application. Collected June 20--29, 2026.

---

## 1. Actionable Product Issues

### `DONE` Per-repo `.leerie` directory and image caching

Workers reinstall ruby and gems on every run — not because install is
slow (~2--3 min) but because workers *re-discover* what to install each
time instead of remembering. The fix is per-repo Docker images: a
`.leerie/Dockerfile` committed to the repo inherits `FROM leerie` and
appends repo-specific packages. The first run discovers deps and
generates the Dockerfile; subsequent runs use the cached image. Tag
images by full GitHub path (e.g. `org/repo`) for guaranteed uniqueness.
The base image ships common deps (Postgres, MySQL client libs); the
per-repo layer adds the long tail (C libraries for gems like nokogiri,
image-processing libs, fonts, etc.).

The `.leerie/` directory follows the `.claude` convention — hierarchical
config where `$HOME/.leerie/` holds ephemeral state and history (not
committed) while `<repo>/.leerie/` holds committed config (Dockerfile,
BLT commands, future settings). Analogous to `.github/` with
`workflows/ci.yaml` — every developer who clones the repo
automatically gets the leerie image and config. Marketing side benefit:
public repos with a `.leerie/` directory create organic discovery.

Shipped in #26/#27/#28 (`f9f26dd`, `a8b47cf`, `b93554c`): per-repo
`.leerie/` config directory, derived per-repo Docker image with
`setup_packages` auto-gen, and image caching keyed by GitHub path.

### `DONE` Explicit BLT commands in `.leerie/` config

Users should be able to tell leerie exactly how to build, lint, and
test — like `package.json` scripts or a CI yaml tells GitHub Actions.
Commands go in `.leerie/` config, not re-discovered each run. Pattern:
"you can tell GitHub how to run your tests, so you should be able to
tell leerie how to run your tests."

Shipped in `a5bf44b`: `_load_blt_config`/`resolve_blt` in
`orchestrator/leerie.py` read declared build/lint/test/setup_packages
commands from `.leerie/config.toml`, falling back to auto-detection
only when undeclared.

### `DONE` Config command for upfront dependency configuration

`leerie.toml` handles flag overrides but there is no interactive
`leerie config` command for users to declare repo dependencies upfront
(e.g., "this project needs Chrome, MySQL, Redis"). Two paths agreed:
(a) power users write `.leerie/Dockerfile` and config directly, and
(b) a conversational `leerie config` launches an LLM chat that
generates the Dockerfile/config from natural language ("I need Postgres
libs, this image library with a C dependency, fonts in
`/usr/share/fonts`"). Both should be supported.

Shipped in #27 (`a8b47cf`): the `leerie config` verb supports all three
modes — `--init` (auto-detected BLT commands), bare (print effective
config with provenance), and `--chat` (conversational LLM session that
generates `.leerie/` config from natural language).

### `DONE` Selenium / Chrome / Capybara setup failures

Workers struggle to get browser-based test infrastructure working.
Reported as "do not stop until" prompts being necessary to force
persistence. No browser provisioning exists in the container image or
worker setup for any runtime (local or Fly). Concrete use case: Garry
needs headless Chrome to run Rails system tests (Capybara) that
exercise real browser flows — e.g., a system test that opens a Stripe
checkout dialog using a CI sandbox key and verifies the full payment
flow. The need is test *execution* inside the container, not visual
inspection. Separate from browser-based visual verification (see V2
items below).

Shipped in #23 (`f31d650`): the base image bakes Chromium + a matching
chromedriver plus container-appropriate flags (`--no-sandbox`,
`--disable-setuid-sandbox`, `--disable-dev-shm-usage`) into
`/etc/chromium.d/`, so Selenium/Capybara system tests run headless out
of the box.

### `PARTIAL` Self-healing verification

Post-run self-healing skill exists (`skills/llm-self-heal/`,
`prompts/patch_generator.md`) but user reports it may not be working
properly. Needs end-to-end verification.

### `PARTIAL` Auto-discover more languages for build/lint/test

Deterministic BLT detection covers Node.js, Python, Go, Rust, Ruby,
Java/Gradle, Kotlin/detekt, .NET, PHP, and basic Makefile inference,
with an LLM fallback for ambiguous cases. The lint command was `None`
on all prior Rails runs because leerie only searched for Node.js
tooling — Rubocop was never detected. A Gemfile-based detection PR
fixed this; conformer is now catching real test failures. Broader
modularization still open — Garry wants a plugin model where repo
maintainers define their own BLT detection logic for niche stacks
without requiring changes to leerie core.

### `BY DESIGN` Conformer leaves linting/test/build errors in PRs

The conformer phase runs a 3-round cap; residuals remain as advisory
warnings in the PR body and never block the subtask. This is
intentional (DESIGN.md: "Residuals surface as warnings ... where a
human and CI can act on them"). `--strict-conformer` already exists for
users who want residuals to block. Now that BLT detection actually
feeds real commands to the conformer, it is catching real failures.

### `DONE` "Generated by leerie" should link to Enric/leerie repo

Now a markdown link in `prompts/pr_writer.md`:
`_Generated by [leerie v<leerie_version>](https://github.com/enricai/leerie)._`

### `OPEN` Browser-based visual verification (V2)

Leerie should be able to launch a browser, take a screenshot, analyze
the CSS/layout, tweak, reload, take another screenshot, and iterate in
a loop until the visual output matches UX best practices. Use case:
Garry generated a full website with leerie — structurally correct but
needing visual polish. Challenge: "what looks good" is subjective, but
can be guided by established UX/UI heuristics. This is V2 scope,
separate from headless Chrome for test execution.

### `ON HOLD` Mid-run interaction (stop, interact, continue)

User noted: "hold on this, we might not need this anymore." The
decomposer workflow (see §2) refines prompts so thoroughly upfront
that mid-run steering is rarely needed — everything is decided before
the run starts.

### `OPEN` "Weird two tests"

Vague report -- likely a planner decomposition issue producing an
unexpected test split. Needs reproduction details.

---

## 2. User Workflow Insights

### Prompt decomposer pattern

User created a Claude Code slash command
(`/tools:leerie-prompt-creation-detailed`) that pre-screens and refines
prompts before feeding them to leerie. In one case, the decomposer
steered the user away from a bad idea entirely, saving a full leerie
run's worth of tokens.

The decomposer asks *product-manager-level* questions that can't be
inferred from code — purely intent disambiguation (e.g., "do you want
a feedback widget for the knowledge base articles?"). This is what
eliminates the need for mid-run interaction: 99% of the time, the
decomposed prompt is so solid that leerie never has questions.

> "If I had given this directly to leerie, would leerie have stopped
> on its own and said no, you shouldn't do this? I feel like I would
> have used a lot more tokens that way."

Garry wants this built into leerie as a pre-flight phase. Andres wants
to try improving leerie's own inference first — Claude could infer more
than it currently does ("it doesn't trust itself, it doubts itself a
lot, and it's lazy"). Open tension: upfront questions to save tokens
vs. full autonomy. Compromise: improve inference, revisit if needed.

### Website generation workflow

Garry generated a full production website in ~24 hours through 5
iterative leerie runs. Process: (1) analyze existing site with Claude,
(2) write comprehensive prompt via decomposer (~20 min), (3) submit to
leerie and leave ("I went to Taco Bell and forgot about it"), (4) come
back to a structurally complete site — core structure, copy, layout,
routes all working, non-visual details "autonomous perfect," (5) give
leerie a polish list, pull the PR, iterate 5x. Production-ready in 24
hours. "Now you understand how I can build a product in a month."

### Productivity stats

49 PRs merged in 7 days (VM created June 20, 2026). User says "it
feels like I've done one month worth of work." Claims ~3x productivity
improvement — 3 days of work done in 1 day.

### Prompt archiving

User keeps all prompts given to leerie (42 prompt files at time of
first feedback session). Stopped keeping detailed run notes because
writing them was slowing them down.

### One-shot wins

File attachments feature was a clean one-shot success ("Excellent
one-shot for file attachments").

---

## 3. Business / Strategy Notes

These are non-engineering notes captured for reference:

- **Readiness**: Garry says leerie is ready for broader users if the
  installer works. "If you took it away from me, I'd fucking cry."
  Nothing in the industry works as well — Spec-Driven Development is
  the current trend and "doesn't work."
- **Enric Coder concept**: Web UI version of leerie + other tools,
  GitHub integration, prompt-to-deploy from phone. Personal plans
  starting at $20/mo, Team + Enterprise at $100 and $200/mo.
- **Alternative model**: Keep tooling internal, charge companies $10K
  for 1 week of work.
- **Risk**: Anthropic might restrict autonomous use of the claude CLI
  (precedent with prior SWE attempts).
- **Growth via PRs**: Making "generated by leerie" a link gives free
  advertising on every public repo.
- **Growth via `.leerie/`**: Public repos with a committed `.leerie/`
  directory create organic discovery — "people will be like, what's
  this?"

---

## 4. Appendix: Prompt Decomposer Template

The user's `/tools:leerie-prompt-creation-detailed` slash command,
included here as reference for a potential built-in leerie feature:

```markdown
model: claude-sonnet-4-6
---

You are helping the user write a task prompt that will be handed to
**leerie**, an autonomous orchestrator. Understanding how leerie
consumes the prompt is what makes these prompts good -- so the rules
below are tied to its mechanics.

## How leerie will consume what you write

1. A **classifier** sorts the task into one or more of nine categories
   (feature, bug-fix, refactor, performance, testing,
   dependency-migration, configuration-build, infrastructure,
   documentation). It drops categories that would touch the same files
   for the same reason.
2. Per-category **planners** decompose the work into small,
   independently verifiable subtasks, run in parallel in isolated git
   worktrees. They investigate the codebase first and derive all
   conventions, patterns, and integration points themselves.
3. **Implementers** execute each subtask and self-gate on evidence
   (reproduction, acceptance criteria, file:line citations) before
   writing code.
4. By default leerie **asks the user nothing** -- workers make a
   documented best-effort decision on anything ambiguous. There is no
   mid-run steering.

## Always investigate first

Before writing the prompt, explore the codebase to ground it in real
files, patterns, and constraints. Do not ask the user what you can find
yourself.

## What the prompt must do

- **Eliminate genuine intent ambiguity.** This is the prime directive.
  Leerie derives the *how* by investigation; what it cannot derive is a
  decision nobody has made yet -- the *what* and the *which behavior*.
  Any choice you actually care about (a value, a threshold, a policy,
  which of two readings is meant) must be stated, because otherwise a
  worker will silently and defensibly guess.
- **Lead with problem + goal.** What's wrong or missing, and what
  success looks like.
- **Give checkable success conditions.** State what "done and correct"
  looks like in verifiable terms -- this feeds the implementer's
  evidence gate directly.
- **Make every wanted deliverable explicit and distinct.** If you want
  tests or docs as well as the change, say so as separate deliverables
  -- tests/lint/build are advisory in leerie and won't reliably happen
  unless named. Distinct deliverables also keep the classifier from
  collapsing categories.
- **Name scope fences -- what must NOT change.** Anything outside a
  subtask's scope is treated as out of bounds; explicit "do not touch X"
  boundaries are honored.
- **Surface real external prerequisites with their owner.** If the work
  depends on something no code change can produce (a deploy in another
  repo, an ops step), name it and who owns it.
- **Keep the work decomposable.** Point to the natural, separable units
  of change so the planner can cut cleanly -- without prescribing the
  cut.

## What the prompt must NOT do

- **Do not reference leerie's internals.** No mention of categories,
  subtasks, capability tags, confidence scores, worktrees, or worker
  types. The prompt describes the *engineering task*; leerie's machinery
  is invisible to it.
- **Do not prescribe the how.** No code snippets, no step-by-step
  implementation, no decomposition plan. Point to reference patterns in
  the codebase by location; let the workers read them.
- **Do not repeat CLAUDE.md.** Leerie reads it. Framework conventions,
  git rules, CSS/style rules -- all already covered.
- **Do not micromanage.** Inform; don't instruct. A weaker,
  over-specified prompt constrains decisions the workers make better
  with full codebase context.

Task Description (if given inline):
$ARGUMENTS
```
