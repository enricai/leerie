# Make first-class commands bare subcommands (git-style) instead of `--` flags

## Context

leerie's top-level VERBS are currently `--`-prefixed flags (`--resume`, `--list`,
`--kill`, etc.). First-class commands should be **bare subcommands** like git
(`leerie resume`, `leerie list`, `leerie kill`) — the `config` verb is ALREADY bare
(`leerie config --init`) and is the exact model to replicate. This is a bug in the
leerie launcher (this repo). Read `CLAUDE.md` first and follow the three-layer rule.

**No backwards compatibility.** Do NOT keep the old `--verb` forms as aliases, and
DO hard-remove the five deprecated chain aliases entirely. This is a deliberate
breaking CLI change; state that plainly in the PR body.

> **Line numbers below are hints, not addresses.** They were accurate at the time of
> writing and every one of them shifts the moment you start editing. Locate each site
> with the `grep` given alongside it and treat a mismatch as drift in the prompt, not a
> missing site.

## What is a VERB vs a FLAG

Convert ONLY the mutually-exclusive top-level VERBS to bare form. Keep `--` on all
OPTIONS / modifiers.

**Launcher VERBS to convert** — all in the main `case "${1:-}"` in `leerie` (~945).
Locate them with `grep -n '^\s*--[a-z-]*)' leerie`:

`--list` (~1191) · `--accept-blocked` (~1444) · `--re-seed` (~1742) · `--stop` (~1782) ·
`--kill` (~1947) · `--finalize` (~2242) · `--status` (~2618) · `--attach` (~2757) ·
`--resume` (~2807) · `--chain` (~2887, see item 2) · `--group` (~3296)

`--version` (~946) is an info verb — convert it to bare `version` too for consistency
(keeping `--version` working is NOT required). `config` (~950) is already bare — leave
it, and use it as the dispatch template.

**Keep `--` (these are FLAGS/OPTIONS, not verbs):** `--model`, `--effort`, `--runtime`,
`--force`, `--pr-base-branch`, `--chain-id`, `--group-id`, `--wave`, `--repo`, `--brief`,
`--inspect-dir`, `--max-workers`, `--max-parallel`, `--confidence-rounds`,
`--source-of-truth`, and every other modifier in the value-taking-flag list (~3790–3797).

**`--report` and `--phase` are NOT launcher verbs.** They are `orchestrator/leerie.py`
argparse flags (`--report` ~24560, `--phase` ~24878) that pass through the launcher.
Leave them exactly as they are.

## Required changes

1. **Dispatch:** convert each launcher verb to a bare subcommand exactly as `config` is
   dispatched — the bare word must be handled in BOTH the ownership short-circuit
   `case "${1:-}"` (~937) AND the main `case "${1:-}"` (~945). Study how `config`
   appears in both (listed at ~937, own arm at ~950) and mirror that for every verb.

2. **Hard-remove the five deprecated chain aliases entirely:** `--chain-submit`,
   `--chain-status`, `--list-chains`, `--chain-kill`, `--chain-attach` (arms at ~2887,
   ~3251, ~3265, ~3270, ~3283, plus their listing at ~937). `--chain-submit` is
   currently fused with `--chain` as a single `--chain|--chain-submit)` pattern, so
   splitting `--chain`→`chain` in item 1 and deleting `--chain-submit` here are the same
   edit. No shim, no alias.

3. **REWRITTEN_ARGS / value-flag filter — TWO separate loops, not one span:** the
   `IS_RESUME` positional-run-id loop (~3756–3775; `IS_RESUME=false` at ~3756, the
   `--resume)` arm at ~3769) and the `_value_flags` skip-loop (~3785–3801;
   `_value_flags=` at ~3790, consumed via `case "$_value_flags" in` at ~3801). The
   launcher's own coupling comment at ~943 says "~line 3097" — that is STALE (3097 is
   inside a chain-resume echo hint); fix that comment while you are here. Every verb arm
   that `exit`s must stay correctly guarded so a misplaced verb errors clearly instead of
   leaking to the orchestrator. Update `IS_RESUME` detection to match bare `resume`.

4. **Update the CLAUDE.md checklist grep guard** (~2642 — `grep -n "chain-submit)" CLAUDE.md`).
   It currently *requires* the five alias arms to be present:
   ```
   grep -q -- '--chain-submit)\|--chain-status)\|--list-chains)\|--chain-kill)\|--chain-attach)' leerie
   ```
   Since those are being removed, rewrite it to assert their ABSENCE, or replace it with
   a bare-verb-presence guard (assert `chain)`, `status)`, etc. exist). Keep the
   checklist internally consistent.

5. **Update the launcher tests.** Exactly one test file currently asserts the
   deprecated-alias shim: `tests/test_chain_launcher_id_dispatch.py`. Confirm with
   `grep -rln -- '--chain-submit\|--chain-status\|--list-chains\|--chain-kill\|--chain-attach' tests/`
   and rewrite the ID-dispatch contract tests against the new bare verbs. Note that many
   *other* test files invoke `leerie --<verb>` for unrelated reasons (`--finalize`,
   `--stop`, `--kill`, …) — those must be updated too or they will exercise a verb form
   that no longer dispatches.

6. **Docs + every self-invocation + user-facing hint.** Run the sweep and update every
   hit. At the time of writing it reported **434 hits across 40 files** (excluding
   `tests/`, `.git/` and `*.log`) — treat that as a staleness check, not a target:
   ```
   grep -rn -- 'leerie --resume\|leerie --finalize\|leerie --kill\|leerie --stop\|leerie --status\|leerie --attach\|leerie --list\|leerie --accept-blocked\|leerie --re-seed\|leerie --chain\|leerie --group' . \
     | grep -v '^./tests/' | grep -v '^./.git/' | grep -v '\.log:'
   ```
   The work is heavily concentrated — `docs/IMPLEMENTATION.md` (93), `leerie` itself
   (66), `docs/DESIGN.md` (44), `README.md` (34), `docs/USAGE.md` (29),
   `scripts/remote/provision.sh` (26), `CLAUDE.md` (26), `orchestrator/leerie.py` (25) —
   with a long tail across `scripts/`, `commands/leerie.md` and `commands/chain.md`.
   **`README.md` is in scope**; it is easy to miss because it is not under `docs/`.

   User-facing recovery hints must keep using a **positional run-id**, never a
   `--run-id` flag (repeated operator correction): `leerie resume <run-id>`, not
   `leerie resume --run-id <id>`.

## How to decompose this — read before planning

**This is one atomic breaking change, not a set of independent ones.** Any intermediate
state is broken: a converted launcher with unconverted docs ships wrong instructions, and
converted tests against an unconverted launcher fail. Prefer **few, large subtasks** over
many small ones, and require every subtask to leave the tree self-consistent.

**Each subtask owns its code change, its tests, and its documentation.** Do not split one
concern across a code subtask, a test subtask, and a docs subtask.

This is not a style preference. Planners for different categories run blind and in
parallel — a testing-domain subtask cannot declare a `requires` on a capability tag the
refactoring planner has not invented yet, and two planners that both claim a file produce
a surface collision the overlap judge has to resolve. Both failure modes have been
observed on this exact task.

Concretely:

- **No separate `testing` subtasks.** The subtask that converts the launcher dispatch is
  the same subtask that rewrites `tests/test_chain_launcher_id_dispatch.py` and fixes
  every other test that invokes a `--verb` form.
- **No separate `documentation` subtasks, and no per-doc-file subtasks.** The doc sweep
  is one mechanical find/replace; splitting it per file produces several subtasks all
  claiming the same change, which is what a cross-planner collision looks like.
- **No cross-cutting "verify everything works" subtask.** Each subtask verifies its own
  surface; the definition-of-done checks below belong to whichever subtask owns the
  launcher dispatch.
- A reasonable shape is **two or three subtasks**: (a) launcher dispatch + filter loops +
  guards + launcher tests, (b) the docs/self-invocation sweep including `CLAUDE.md`'s
  checklist guard, and optionally (c) `scripts/**` recovery hints. Fewer is fine.

## Constraints

- bash 3.2 portability (macOS `/bin/bash`): no `local -n` / `declare -n` namerefs.
- Follow the three-layer rule: the CLI surface is documented in `docs/IMPLEMENTATION.md`
  and `CLAUDE.md`; update those to match. DESIGN only if the command model itself is
  described there.

## Definition of done (verify each; do not self-certify on "tests pass" alone)

- `leerie resume`, `leerie list`, `leerie status`, `leerie kill`, `leerie stop`,
  `leerie finalize`, `leerie attach`, `leerie accept-blocked`, `leerie re-seed`,
  `leerie chain`, `leerie group`, `leerie version` all dispatch correctly — demonstrate
  by invoking each and observing the right code path, not just a unit test.
- The five `--chain-*` aliases are GONE — `leerie --chain-submit` errors as an unknown
  verb, and
  `grep -rn -- '--chain-submit\|--chain-status\|--list-chains\|--chain-kill\|--chain-attach' .`
  finds no live launcher arm or doc reference.
- The item-6 sweep returns no `leerie --<verb>` forms (genuine flags excepted).
- `pytest tests/` passes; the CLAUDE.md checklist grep guard passes in its new form;
  `git diff --stat` matches the intended surface with no collateral edits.
