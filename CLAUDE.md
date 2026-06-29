# CLAUDE.md

Guidance for Claude Code working in this repository. Read `docs/DESIGN.md`
before touching architecture; read `docs/IMPLEMENTATION.md` before touching
code surface; read this file first.

## Tech stack

Python 3.10+, stdlib-preferred orchestrator (runtime deps pinned in
`requirements.txt` and listed in `docs/IMPLEMENTATION.md` §0). The
orchestrator shells out to `claude -p` (Claude Code CLI, on the user's
subscription — no API key) and uses git worktrees for parallel
implementer isolation. `pytest` is the only dev dependency.

**Leerie runs inside a container.** The `leerie` launcher shells out to
`nerdctl run` to start a container per run (DESIGN §6 *Worker subtree
termination*). The orchestrator runs as PID 1 inside; every worker
(and every Bash tool call those workers make) lives in the same PID
namespace. On Ctrl-C / SIGTERM / SIGKILL / crash, the kernel reaps
the namespace — the abnormal-exit cleanup guarantee is the container
boundary, not Python signal handling.

Runtime: containerd + nerdctl. On Linux, native. On macOS, via
[Colima](https://colima.run) (a Lima-managed Linux VM). See
`docs/INSTALL.md` for per-OS install steps.

Python is provisioned *inside the container* by the image (Python 3
from Debian 13). The launcher itself is a portable bash script; it
no longer needs `uv` or a host Python. See `docs/IMPLEMENTATION.md`
§0 (install surface) and §0.5 (container shape).

All control flow lives in one file: `orchestrator/leerie.py`. The
launcher (a portable bash script) and the `Dockerfile` are the only
other moving parts on the orchestrator side. The orchestrator is
deliberately kept as a single module rather than split across
packages — the design goal is that you can read the whole control
flow top-to-bottom in one sitting. Stdlib-preferred on the Python side
— runtime deps are pinned in `requirements.txt` and listed in
`docs/IMPLEMENTATION.md` §0; `pytest` is the sole dev dependency.

## The three-layer rule (load-bearing — read first)

This repo deliberately separates *theory*, *mechanism*, and *code*, and
the layers are **top-down canonical**: each layer derives from and
conforms to the one above it.

- **`docs/DESIGN.md`** is the architecture and reasoning. It is
  canonical: the implementation spec and the code derive from it. A
  line goes stale here only when the *design* changes.
- **`docs/IMPLEMENTATION.md`** is the code-surface spec — function
  names, cap values, schemas, install steps — derived from DESIGN. It
  defines what the code must implement. It is canonical over the code.
- **The code** is derived from IMPLEMENTATION.md and conforms to it.

Precedence when they disagree:

- DESIGN.md vs IMPLEMENTATION.md → DESIGN.md wins; the spec is the
  defect.
- DESIGN.md vs code → DESIGN.md wins; the code is the defect.
- IMPLEMENTATION.md vs code → IMPLEMENTATION.md wins; the code is the
  defect.

When you change something: change the highest layer that the change
touches *first*, then propagate down. Changing how phases relate?
DESIGN.md, then IMPLEMENTATION.md, then code. Renaming a function or
changing a cap value? IMPLEMENTATION.md, then code. Pure mechanical
refactor that leaves the documented surface intact (rename a local
variable, restructure an unexported helper)? Code only.

If you find drift — the code does something the spec does not describe,
or contradicts what the spec describes — the resolution is *never*
"update the spec to match the code." Either the code is a defect (fix
it to match the spec) or the spec is missing something it should
specify (update the spec first, then verify the code still conforms).

## The central principle: prompts are advisory, code enforces

(`DESIGN.md` §12.) Any guarantee that *matters* and *can be checked
mechanically* lives in `orchestrator/leerie.py`, not in a worker
prompt. A prompt can ask for any behavior, but a model can drift; a
real Python check cannot.

Do not move a check from `leerie.py` into a prompt to "make the prompt
smarter" — that is the wrong direction. The reverse is correct: a
prompt-level rule that turns out to matter should become a code check
with the prompt downgraded to documentation.

## No subagent spawning

Workers are headless `claude -p` subprocess invocations, not in-session
subagents. The orchestrator is an ordinary Python program. (Constraint
1, DESIGN.md §2.) The Claude Code Agent tool is not available to the
orchestrator and not used anywhere in this repo.

## Mandatory requirements

- **Worker outputs are JSON-schema-validated.** New worker types must
  define a schema in the `SCHEMAS` dict in `orchestrator/leerie.py` and
  pass it via `--json-schema` in `claude_p()`.
- **Caps are real Python counters in `DEFAULT_CAPS`**, not prompt
  instructions. Adding a new cap means adding a counter and a check, not
  asking a worker to bound itself.
- **All run state goes through the `State` class.** Never write to
  `state.json` (under `<state-root>/runs/<run-id>/`) directly —
  `State.save()` writes a temp file then `os.replace()`s it for atomicity. The orchestrator runs on a single asyncio
  event loop, so no in-process lock is needed: coroutines only interleave at `await`
  points and never inside a `st.data[k] = v; st.save()` pair.
  (Cross-process contention — two orchestrators on the same run
  dir — is prevented separately by `State.__init__`'s exclusive
  `fcntl.flock` on the run directory; see DESIGN §6 *Single
  owner per run dir*.)
- **Source-of-truth answers go through the validation gate in
  `gather_answers`.** Anything reading `answers["source_of_truth"]` can
  trust the value is in `SOURCE_OF_TRUTH_VALUES` (`codebase` /
  `research` / `both`).
- **Don't write to the coordination state directory from inside a subtask worktree.** The
  worktree is disposable; coordination state must outlive it. The
  orchestrator writes to the state root (default: `$HOME/.leerie/<basename>/`,
  at `/leerie-state` inside the container); workers commit code to their
  worktree branch only.

## Code style

- **Imports:** stdlib first, then third-party, then local.
  Alphabetical within each group. Third-party deps are kept minimal —
  see `docs/IMPLEMENTATION.md` §0 for the current list.
- **Naming:** `snake_case` for functions and variables, `PascalCase` for
  classes, `ALL_CAPS` for module constants.
- **Logging:** `log("...")` for normal output, `die("...", code=N)` for
  fatal exits. Never `print(...)` *except* for the interactive question UI in
  `gather_answers()` — `log()`'s timestamp prefix would mangle a question
  rendered next to `input("  > ")`. Never `sys.exit(...)` directly (use `die`)
  *except* for documented non-error structured exits like
  `EXIT_NEEDS_ANSWERS=10`, where `die()`'s `leerie: error:` prefix would
  mislabel a non-error deferred-clarification signal. Both helpers live in
  `leerie.py`. The `chain/` subpackage is the second deliberate
  exception: it provides its own `chain/_log.py::log()` and `die()`
  because the package-isolation invariant forbids importing from
  `orchestrator/leerie.py`.
- **Type hints** on every function signature. Use PEP 604 union syntax
  (`str | None`, not `Optional[str]`) — Python 3.10+ is the minimum.
- **Comments explain *why*, not *what*.** Well-named identifiers
  document what; comments are for non-obvious constraints, hidden
  invariants, or workarounds for specific bugs.
- **Functional first.** Pure functions over classes. The `State` class
  is the deliberate exception (encapsulates mutable shared state with a
  lock).

## File layout

```
orchestrator/leerie.py    All orchestrator control flow (single file by design)
prompts/*.md                System prompts for each worker type
scripts/*.sh                Git worktree mechanics (setup, integrate, finalize, cleanup)
commands/leerie.md        Thin plugin skill — launches the orchestrator
docs/DESIGN.md              Architecture and reasoning
docs/IMPLEMENTATION.md      Current code surface
docs/ANALYSIS.md            Derived compiler-frontend analysis (non-canonical; regenerate
                            via `python3 docs/tools/leerie_extract.py orchestrator/leerie.py`)
docs/tools/                 Standalone extraction tools (stdlib only)
chain/                      Laptop-side chain helpers (see DESIGN.md §19). A chain is
                            N parallel `leerie --runtime fly` invocations per wave,
                            sequenced by the launcher's `--chain` arm. `chain/git_ops.py`
                            provides `synth_merge_branches` (used between waves).
                            No Fly coordinator machine.
tests/                      pytest suite
```

## Quick start

```bash
# One-time runtime setup (leerie runs in a container — see docs/INSTALL.md):
#   macOS:  brew install colima && colima start --runtime containerd --mount-type virtiofs \
#             --cpu 4 --memory 8   # ~half-host; docs/INSTALL.md explains the auto-sizing
#             # Also add the swap-provision YAML block from docs/INSTALL.md
#             # "Memory pressure: swap configuration" to ~/.colima/default/colima.yaml.
#   Linux:  install containerd + nerdctl from your distro (apt, dnf, pacman, etc.)
#
# Install leerie (one command — pick one):
#   Inside Claude Code:  /plugin marketplace add enricai/leerie
#                        /plugin install leerie@enricai-leerie
#   From a terminal:     curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
# See docs/INSTALL.md for details.

# Run on a task in the current git repo:
./leerie "Fix the login timeout bug and add a regression test"

# Resume after an interruption:
./leerie --resume

# Override the default per-repo state directory. Default is
# $HOME/.leerie/<basename>/ (outside the repo, no .gitignore entry
# needed). Cross-repo basename collisions are caught at use time via
# an .owner sidecar. Set in your shell profile to pin globally.
export LEERIE_STATE_DIR=~/.leerie/myproject

# Override the default source-of-truth preference (`both`) with an env
# var, the CLI flag, or a per-repo file:
export LEERIE_SOURCE_OF_TRUTH=codebase   # or: research, both
./leerie "task" --source-of-truth codebase
# …or commit a leerie.toml at the repo root with: source_of_truth = codebase

# Select the execution runtime (default: local). `fly` routes each worker
# through Fly.io machines instead of local nerdctl containers.
export LEERIE_RUNTIME=local              # or: fly
export LEERIE_FLY_APP=my-leerie-app      # required for --runtime fly (globally unique)
./leerie "task" --runtime fly
# …or commit a leerie.toml at the repo root with: runtime = fly

# Choose the model. Without overrides: judgment workers (classifier,
# planner, reconciler, plan_overlap_judge, provision, integrator)
# default to opus; acting workers (implementer, conformer) default to
# sonnet. Per-worker overrides exist via --model-<worker> /
# LEERIE_MODEL_<WORKER>. See docs/IMPLEMENTATION.md §2 "Model selection"
# for the full table.
export LEERIE_MODEL=sonnet               # or: opus, haiku
./leerie "task" --model opus
./leerie "task" --model-implementer opus --model-classifier haiku

# Pin reasoning depth via `claude -p --effort`. Without overrides,
# judgment workers (classifier, planner, reconciler, plan_overlap_judge,
# provision, integrator) default to `high`; acting workers
# (implementer, conformer) inherit Claude's default. Per-worker
# overrides exist via --effort-<worker> / LEERIE_EFFORT_<WORKER>. See
# docs/IMPLEMENTATION.md §2 "Effort selection".
export LEERIE_EFFORT=high                # low, medium, high, xhigh, max
./leerie "task" --effort max
./leerie "task" --effort-planner max

# Dial how persistent each planner/implementer is at building confidence
# before exiting blocked (default 8 rounds; see DESIGN.md §8). CLI flag,
# env var, or `confidence_rounds = N` in leerie.toml.
export LEERIE_CONFIDENCE_ROUNDS=12
./leerie "task" --confidence-rounds 12

# Raise the per-run worker-invocation budget (default 200). Same precedence
# as confidence-rounds: CLI > env > leerie.toml.
export LEERIE_MAX_WORKERS=80
./leerie "task" --max-workers 80

# Override concurrent workers per wave (default 5). Same precedence:
# CLI > env > leerie.toml.
export LEERIE_MAX_PARALLEL=6
./leerie "task" --max-parallel 6

# Skip the live `claude -p` smoke test during development:
./leerie "task" --skip-smoke

# Skip the phase 2¾ plan-overlap judge (DESIGN §5 *Cross-domain surface
# overlap*) — also LEERIE_SKIP_OVERLAP_JUDGE=1 or
# `skip_overlap_judge = true` in leerie.toml. The judge is skipped
# automatically on single-planner runs; use this flag to disable it on
# multi-planner runs (e.g., when you know the overlap is intentional):
./leerie "task" --skip-overlap-judge

# Skip the planner-output budget-feasibility preflight (DESIGN §13
# *Budget feasibility — fail fast at the cheapest moment*) — also
# LEERIE_SKIP_BUDGET_CHECK=1 or `skip_budget_check = true` in
# leerie.toml. The preflight die()s at plan-return time with a
# recommended --max-workers when the planner produces more subtasks
# than the budget can fit; the runtime backstop in
# State.bump_workers() always fires regardless, so this flag only
# suppresses the *early* die() — use when the operator knows the
# conformer phase will degrade heavily to advisory warnings or the
# per-subtask ratio will come in under the default 2.5 estimate:
./leerie "task" --skip-budget-check

# Make the conformer phase blocking instead of advisory.
# Residuals cause subtasks to return 'blocked' (fix + --resume).
# Also LEERIE_STRICT_CONFORMER=1 or `strict_conformer = true` in
# leerie.toml:
./leerie "task" --strict-conformer

# Waive §12 mechanical read-only enforcement on judgment workers
# (use on repos where the planner needs pnpm/tsc/vitest visibility —
# also LEERIE_DANGEROUSLY_SKIP_PERMISSIONS=1 or
# `dangerously_skip_permissions = true` in leerie.toml):
./leerie "task" --dangerously-skip-permissions

# Pick a PR template when the repo has multiple in PULL_REQUEST_TEMPLATE/.
# Also LEERIE_PR_TEMPLATE or `pr_template` in leerie.toml.
./leerie "task" --pr-template feature

# Override the model for the finalize-time PR-writer worker (default sonnet).
# Also LEERIE_MODEL_PR_WRITER or `model_pr_writer` in leerie.toml.
./leerie "task" --pr-writer-model opus

# Filter `--list` output by run status:
./leerie --list --status paused
./leerie --list --status seed-failed

# Verbosity: default is `stream` (one-line summary per worker event).
# Per-worker logs are always written to <state-root>/logs/<sid>.log.
./leerie "task" -q       # normal (pre-streaming terse output)
./leerie "task" -qq      # quiet (errors + phase boundaries only)
./leerie "task" -vv      # debug (raw event payloads + tool I/O)
export LEERIE_VERBOSITY=normal  # override (default is stream)

# Bound the seed_auth tar pipe over `flyctl ssh console` against the
# known flyctl-stalls-without-exiting failure mode. Default 600 s
# (10 min) per bulk transfer. On rc 124/137 (timeout fired), seed_auth
# runs its existing one-shot `flyctl agent restart` retry; if that also
# stalls, the function returns 1 and leerie's existing PAUSED-on-failure
# path takes over — `./leerie --resume` recovers the run normally:
export LEERIE_SEED_TIMEOUT_S=900

# Heartbeat cadence (default 10 s) for the "still streaming (Ns
# elapsed)" line emitted during seed_auth/seed_repo bulk transfers. Set
# to 0 to suppress entirely. The separate hallpass-wait heartbeat in
# wait_for_fly_ssh_ready fires on a fixed every-3rd-probe cadence and
# does not consult this variable:
export LEERIE_PROGRESS_INTERVAL_S=15

# Pre-classify failures (seed_auth aborted before phase_classify) now
# appear in `--list` with status `seed-failed` and are resumable via
# `--resume <id>`. Previously these runs were invisible:
./leerie --list --status seed-failed
./leerie --resume <seed-failed-id>

# Chain verbs: submit + manage multi-run chains. A chain is N parallel
# `./leerie --runtime fly` invocations per wave, with synth-merge between
# waves to build the next wave's base branch. The laptop is the sequencer;
# no Fly coordinator machine. Each --wave flag defines one sequential wave
# (N waves supported); waves execute in order, jobs inside a wave run in
# parallel. Per-job flags (--effort, --model, --dangerously-skip-permissions,
# etc.) are forwarded to each wave invocation. No chain-specific env vars
# required — the per-job `--runtime fly` invocations have their own env
# requirements unchanged.
./leerie --chain \
  --effort high --dangerously-skip-permissions \
  --wave "prompts/fetch.md,prompts/lint.md" \
  --wave "prompts/publish.md"

# Resume after a wave failure or synth-merge conflict: re-submit
# with --chain-id pinned to the prior chain's UUID. The wave loop
# skips already-pushed waves AND skips synth-merge for transitions
# whose staging branch is already on origin (idempotency probe via
# `git ls-remote --exit-code`). Run `./leerie --resume <chain-id>`
# first to unpause any per-run paused machines.
./leerie --resume <chain-id>
./leerie --chain --chain-id <chain-id> \
  --wave "prompts/fetch.md,prompts/lint.md" \
  --wave "prompts/publish.md"

# ID-dispatched verbs: UUID → chain scope (iterates run.json filtered by
# chain_id); Fly machine id → existing single-run behavior. Deprecated
# chain-prefixed aliases (--chain-submit, --chain-status, --chain-kill,
# --chain-attach, --list-chains) shim to the new verbs.
./leerie --status   <chain-id>        # render per-run states from run.json
./leerie --attach   <chain-id>        # poll run.json files every 5s
./leerie --stop     <chain-id>        # pause every running chain run
./leerie --kill     <chain-id>        # destroy every chain run's machine
./leerie --resume   <chain-id>        # resume every paused chain run
./leerie --finalize <chain-id>        # push + open PR for every unpushed run
./leerie --list --chains              # group runs by chain_id
```

## Testing

`pytest tests/` from the repo root. Tests cover the deterministic
enforcement functions (`resolve_leerie_root`, `resolve_source_of_truth`,
`resolve_runtime`, `gather_answers` validation gate, `_retryable_failure`,
`check_merge_committed`, `validate_result`, `validate_plan`,
`_validate_run_json`, `_derive_run_status`, `_load_blt_config`,
`resolve_blt`)
including a coupling test that the
retry-policy markers match the live check-function strings. The
remote (Fly.io) bash surface — `ensure_image`, `provision_machine`,
`stop_machine`, `decide_teardown`, `resume_machine`, and `lib.sh`'s
`update_run_json` — is tested via bash-harness subprocess tests with
stubbed `flyctl`. The local per-repo image surface —
`resolve_repo_image_tag`, `_leerie_repo_id`, `build_repo_image` — is
tested via bash-harness subprocess tests with stubbed `git` and
`nerdctl`. No coverage target is set — the suite was
introduced from scratch and a number now would be arbitrary.

The worker invocation path (`claude_p`) is not unit-tested; meaningful
testing requires a stub or live `claude` binary and lives in a separate
end-to-end tier.

## Task completion checklist

Before marking a change complete:

- [ ] Update `IMPLEMENTATION.md` if the change affected code surface
      described there.
- [ ] Update `DESIGN.md` only if the architecture itself changed.
- [ ] Regenerate `docs/ANALYSIS.md` if `orchestrator/leerie.py` changed
      (`python3 docs/tools/leerie_extract.py orchestrator/leerie.py`).
- [ ] `pytest tests/` — all pass.
- [ ] `python3 -c "import ast; ast.parse(open('orchestrator/leerie.py').read())"`
      as a static check.
- [ ] `grep -rn <removed-string> .` — confirm no stragglers if the change
      renamed or removed a string used elsewhere.
- [ ] `git diff --stat` — confirm the diff is scoped to what the change
      intended; no collateral edits.
- [ ] `python3 -c 'import json; json.load(open(".claude-plugin/plugin.json")); json.load(open(".claude-plugin/marketplace.json"))'`
      — if either manifest in `.claude-plugin/` was touched, confirm both
      are valid JSON and all referenced skill/command paths still exist.
      The `version` field is duplicated across the two manifests;
      `tests/test_version_flag.py` guards them from drifting.
- [ ] `python3 -c 'import json; [json.loads(l) for l in open("<state-root>/runs/<run>/calls.ndjson")]'`
      — if the telemetry writer (`_capture_call`) was touched, confirm a
      representative run produces a well-formed `calls.ndjson` (each line
      valid JSON with at least `call_type`, `system_prompt`, and
      `response_content` keys). Replace `<state-root>` with the resolved
      state directory (default: `$HOME/.leerie/<basename>/`).
- [ ] `grep -q -- '--chain-submit)\|--chain-status)\|--list-chains)\|--chain-kill)\|--chain-attach)' leerie`
      — if chain launcher verbs were touched, confirm all five deprecated-alias
      case-arms are still present in the launcher (the aliases shim to the new
      ID-dispatched verbs; see DESIGN.md §19 and IMPLEMENTATION.md "Chain
      verbs"; `pytest tests/test_chain_launcher_id_dispatch.py` for the
      ID-dispatch contract test).
