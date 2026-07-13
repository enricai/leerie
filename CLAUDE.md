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

# Accept a blocked subtask so --resume skips it (e.g., E2E tests
# that need external deps the container can't provide):
./leerie --accept-blocked <run-id> <subtask-id>

# Generate .leerie/config.toml with auto-detected BLT commands (host-only, no container):
./leerie config --init

# Print effective config for the current repo with [config]/[inference] provenance:
./leerie config

# Launch an interactive Claude session to configure leerie for this repo:
./leerie config --chat

# Run the dep_capture LLM worker over past runs' logs and write/update
# .leerie/config.toml without starting a new run (host-only, no container):
./leerie config --recapture
./leerie config --recapture --force   # wholesale replace (not union)

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

# Raise the per-worker cgroup PID cap (default 1024). Bounds fork/clone in
# each worker subtree; raise it for repos whose conformance step runs a
# subprocess-heavy full test suite (which can burst past a low cap in
# seconds and wedge every subsequent Bash call with EAGAIN). Positive
# integer; same precedence: CLI > env > leerie.toml.
export LEERIE_WORKER_PIDS_MAX=2048
./leerie "task" --worker-pids-max 2048

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

# Skip the P6 repo-map structural context (DESIGN §P6 *Codebase
# structural map*): suppresses build_repo_map() and the ranked
# subgraph injection into planner/splitter context. The planner
# degrades gracefully to the prior grep/glob-only path. Use on repos
# where tree-sitter cannot parse the primary language, or to opt out
# of structural context. Also LEERIE_SKIP_REPO_MAP=1 or
# `skip_repo_map = true` in leerie.toml. Default: off.
./leerie "task" --skip-repo-map

# Make the conformer phase blocking instead of advisory.
# Residuals cause subtasks to return 'blocked' (fix + --resume).
# Also LEERIE_STRICT_CONFORMER=1 or `strict_conformer = true` in
# leerie.toml:
./leerie "task" --strict-conformer

# Disable finalize-time dependency capture (DESIGN §6½). Default: enabled.
# Also `capture_deps = false` in .leerie/config.toml (no leerie.toml tier):
export LEERIE_CAPTURE_DEPS=0
./leerie "task"

# Disable the language-dep COPY+RUN layer in the auto-generated Dockerfile
# (bake apt packages only). Default: enabled. Also `bake_language_deps =
# false` in leerie.toml or .leerie/config.toml:
export LEERIE_BAKE_LANGUAGE_DEPS=0
./leerie "task"

# Waive §12 mechanical read-only enforcement on judgment workers
# (use on repos where the planner needs pnpm/tsc/vitest visibility —
# also LEERIE_DANGEROUSLY_SKIP_PERMISSIONS=1 or
# `dangerously_skip_permissions = true` in leerie.toml):
./leerie "task" --dangerously-skip-permissions

# Run workers WITHOUT cgroup containment when the host can't enforce it
# (rootless containerd, or no usable cgroup hierarchy). DANGEROUS: workers
# then run with no memory/PID limits, so a runaway subtree can exhaust the
# VM thread/PID table (the failure the fail-closed gate prevents — DESIGN
# §6 Memory containment). Also LEERIE_DANGEROUSLY_ALLOW_UNCAPPED=1 or
# `dangerously_allow_uncapped = true` in leerie.toml:
./leerie "task" --dangerously-allow-uncapped

# Pick a PR template when the repo has multiple in PULL_REQUEST_TEMPLATE/.
# Also LEERIE_PR_TEMPLATE or `pr_template` in leerie.toml.
./leerie "task" --pr-template feature

# Override the model for the finalize-time PR-writer worker (default sonnet).
# Also LEERIE_MODEL_PR_WRITER or `model_pr_writer` in leerie.toml.
./leerie "task" --pr-writer-model opus

# Override the model for the dep_capture worker (default opus). Env-var only
# (no CLI flag or leerie.toml key — dep_capture is a post-run worker):
export LEERIE_MODEL_DEP_CAPTURE=sonnet

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

# Shallow-seed heavy repos (--runtime fly). For a repo with deep
# committed history, the fresh-provision `git bundle --all` can be
# hundreds of MB and exceed the seed timeout. When the repo's .git
# exceeds LEERIE_SEED_SHALLOW_THRESHOLD_MB (default 200), leerie ships a
# `git clone --depth=N` of the working branch (as a .git tar) instead —
# a fraction of the bytes. Workers on the machine then see only depth-N
# history (git log/blame beyond N unavailable; the machine can't deepen).
# Set depth to 0 to force the full-history bundle. CLI > env > leerie.toml
# (seed_depth / seed_shallow_threshold_mb):
export LEERIE_SEED_DEPTH=50                 # 0 = full history (disable shallow)
export LEERIE_SEED_SHALLOW_THRESHOLD_MB=200
./leerie "task" --runtime fly --seed-depth 100
./leerie "task" --runtime fly --seed-depth 0   # force full bundle

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
./leerie --kill     <chain-id>        # destroy every chain run
./leerie --resume   <chain-id>        # resume every paused chain run
./leerie --finalize <chain-id>        # push + open PR for every unpushed run
./leerie --list --chains              # group runs by chain_id

# Group verbs: launch N single-repo runs together as a coordinated unit.
# Each member runs in its own state dir (basename-keyed), its own branch,
# and opens its own PR — nothing is shared except the group_id and a
# read-only view of siblings via --inspect-dir. The shared brief narrows
# each planner to the joint intent; cross-repo prerequisites are rendered
# as deploy-ordering notes in each member's PR body.
./leerie --group \
  --repo ../api   "add /volumes endpoint" \
  --repo ../frontend "add-disk dialog" \
  --brief group-brief.md            # optional shared brief (prepended to each member's prompt)

# Resubmit a prior group (reuse its group_id instead of minting a new one):
./leerie --group --group-id <prior-group-id> \
  --repo ../api   "add /volumes endpoint" \
  --repo ../frontend "add-disk dialog"

# Group-scoped verbs: UUID → group scope (scans each member's state dir).
./leerie --status   <group-id>        # render per-member run states
./leerie --stop     <group-id>        # pause every running member (Fly runtime only)
./leerie --resume   <group-id>        # resume every paused member run
./leerie --kill     <group-id>        # destroy every member run
./leerie --finalize <group-id>        # push + open PR for every unpushed member
./leerie --list --groups              # list all groups across state dirs
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
`resolve_repo_image_tag`, `_leerie_repo_id`, `build_repo_image`, and
`ensure_base_in_buildkit_ns` (copies the base into the `buildkit` containerd
namespace before the derived build so `FROM $BASE_IMAGE` resolves locally under
Colima's namespaced buildkit; `tests/test_build_repo_image.py` pins that the copy
fires and precedes the build, and the idempotent skip when the base is already
present) — is tested via bash-harness subprocess tests with stubbed `git` and
`nerdctl`. Worker cgroup containment (DESIGN §6 *Memory containment*) is
tested in two files: `tests/test_cgroup_helpers.py` covers the
orchestrator-side broker clients (`_cgroup_probe`/`_cgroup_create`/
`_cgroup_enroll`/`_cgroup_destroy` via a stubbed socket round-trip) and
the fail-closed `enforce_and_record_cgroup_containment`; `tests/test_cgroup_broker.py`
covers the root broker (`scripts/cgroup-broker.py`) — protocol dispatch,
sid validation, and v1/v2 path selection — against
a fake cgroupfs. Mid-run PID reaping (DESIGN §6 *Mid-run PID reaping*) is
tested in `tests/test_signal_cleanup.py`: `_reparented_orphans` selects only
alive+ppid==1+old PIDs sorted oldest-first (stubbed ps); `_poll_loop` reaps
only at ≥90% pressure and stops below 75% (hysteresis); below 90% is a
byte-identical no-op; young (<60s) and attached (ppid!=1) PIDs are never
reaped; and a structural guard pins `cgroup_sid: str | None = None` on
`_DescendantTracker.__init__` so the 3 pre-existing direct-constructor call
sites remain compatible after the parameter was added. Zombie reaping (DESIGN
§6 *Zombie reaping* — the container PID 1 is `runuser`/idle `sleep`, not a
reaping init, so orphaned git/ssh-agent descendants would pile up as `<defunct>`
against `pids.max`) is tested in `tests/test_subreaper.py`: `_become_subreaper`
is a bool-returning no-op off Linux and (Linux-guarded) sets the flag verifiable
via `prctl(PR_GET_CHILD_SUBREAPER)`; `_zombie_reaper` (Linux-guarded) reaps an
orphaned exited child so it's no longer a zombie, survives having no children,
and — the load-bearing race test — does NOT steal a live
`create_subprocess_exec` child's exit status (asserts the true code, not 255)
because it is targeted (`_orphan_zombie_children`: state==Z + ppid==getpid,
minus `_ASYNCIO_MANAGED_PIDS`) rather than `waitpid(-1)`; plus a test that a
registered worker pid is excluded from the reap set, a
`_reparented_orphans`-accepts-`ppid==getpid` test, and source-coupling guards
that `main()` calls `_become_subreaper()` and `orchestrate()` spawns+cancels
`_zombie_reaper` (the fix is inert without the wiring). The `fetch_branch()` stream-back surface (`scripts/remote/fetch-branch.sh`)
is tested across two files. `tests/test_fetch_branch_sh.py` covers run
discovery, bundle fetch, run-state tar, `no_push` strip, and baseline Step 4
stream-back (both files streamed when host has neither, never clobbers an
existing host file, non-fatal on absent machine files, respects
`LEERIE_STATE_HOST_DIR`) via bash-harness subprocess tests with a stubbed
`flyctl`. The expanded Step 4 best-effort `.leerie/` stream-back contracts are
covered by `tests/test_fetch_branch_leerie_streamback.py` (imports stub
helpers from `test_fetch_branch_sh` to avoid duplication): streams both files
when host has neither, never clobbers an existing `config.toml`, never clobbers
an existing `Dockerfile`, non-fatal when machine files are absent, streams only
the present machine file when only one exists, skips both when both host
files exist, and respects `LEERIE_STATE_HOST_DIR` for the destination root. The `leerie config` verb (all four sub-modes: `--init`,
bare, `--chat`, `--recapture`) is tested in `tests/test_config_verb.py`
via a self-contained bash harness with stubbed `nerdctl` and `claude`,
plus a parity guard that extracts the real launcher `config)` case arm and
diffs its BLT inference against `_infer_build_lint_test()` across a
fixture matrix so the two can never silently diverge. The `--group`
launcher arm and group-scoped ID-dispatched verbs are tested in
`tests/test_group_launcher.py` via the same bash-harness pattern
(stubbed `./leerie`, multi-state-dir fixtures), modeled on
`tests/test_chain_launcher_id_dispatch.py`. Group-scoped verb dispatch
across two state dirs (combined paused/unpushed + pushed fixture, plus
`--stop` dispatch) is covered by `tests/test_group_launcher_verbs.py`.
Fan-out core contract (cwd per member, `--inspect-dir` for siblings,
brief prepend) is in `tests/test_group_launcher_fanout.py`.
Python-layer `group_id` in `run.json` (`_validate_run_json`,
`_write_run_json`, `_derive_run_status`) is in
`tests/test_group_run_json.py`. State-dir isolation (distinct
basename-keyed dirs per member, guard rejects `LEERIE_STATE_DIR`/
`--state-dir`) is in `tests/test_group_state_dir_guard.py`. The
capture engine (DESIGN §6½) — `_gather_dep_manifests` (the manifests-first
PRIMARY corpus), `_extract_depcap_commands` (the install-filtered SECONDARY
command hint), `_is_install_command` (the install-verb filter),
`_toml_value`/`_dump_language_installs` (single-quote-safe TOML persistence),
`_merge_setup_packages`, `capture_repo_deps` (async, with stubbed `claude_p`),
the idempotency sentinel (`dep_capture_done` state field +
`<run_dir>/dep_capture.done` file), and `_backstop_capture_prior_runs` (skips
runs with sentinel, captures runs without) — is tested across four files.
`tests/test_dep_capture_budget.py` covers the extraction+budget unit
(`_extract_depcap_commands`) in focused isolation: dedup, newest-first ordering,
budget gate (`_DEPCAP_TOTAL_BUDGET`), `hit_ceiling` flag semantics, non-Bash
filtering, and malformed-line tolerance. Since DESIGN §6½ moved the worker to a
manifests-first corpus, `_extract_depcap_commands` now keeps **only
install-shaped Bash commands** (`_is_install_command`) — the install-verb filter
and its text-tool-pattern exclusion (e.g. `grep "apt-get install …"` is dropped)
are pinned in `tests/test_capture_deps.py` (`TestIsInstallCommand`,
`test_filters_to_install_shaped_only`,
`test_excludes_install_verb_inside_text_tool_pattern`), alongside
`_gather_dep_manifests` (`TestGatherDepManifests`) and `_toml_value` /
`_dump_language_installs` (`TestTomlValue`, including the both-quote
single-quoted-command TOML-validity regression).
`tests/test_capture_deps.py` covers the integration against a synthetic
JSONL fixture in the `_iter_log_tool_use` shape: absence pins
(`TestRegexPathAbsent`) that assert the four deleted regex-path symbols
no longer exist on the module (so the regex path can never
silently return); command extraction, budget ceiling truncation, merger
union/no-op/never-clobber, schema-validated worker output → setup_packages +
language_installs write, committed-Dockerfile skip, write-failure non-fatal,
and opt-out. It also pins the `--recapture --force` wholesale-replace path
(`replace=True` drops deps no longer captured; an empty capture leaves the
existing config untouched) alongside the default union. Source-coupling guards in the same file pin that `main()`'s
`KeyboardInterrupt` and `InterruptedBySignal` handlers each invoke
`capture_repo_deps` (the cancel-arm seam — the fix is inert without the
wiring). The worker-driven write path specifically — `capture_repo_deps`
invoked with a stubbed `_invoke` returning a fixed structured_output envelope
(mirroring `test_phase_judge.py`'s `_JUDGE_ENVELOPE` pattern) — is separately
covered in `tests/test_dep_capture_worker.py`: schema-validated output written
to `.leerie/config.toml`, warm-repo never-clobber (mtime unchanged when all
deps already present), union append for new packages, env + config-file opt-out
(worker not invoked), committed `.leerie/Dockerfile` guard (worker not invoked),
missing logs dir silent no-op, and non-fatal write failure. A `TestDepCaptureReplace`
class covers the `replace=True` (`--recapture --force`) path: wholesale-overwrite
of `setup_packages`/`language_installs` (stale deps dropped), an empty capture
leaving the config untouched, and — the regression pin for the empty-item
blanking bug — a schema-valid empty-item capture (`setup_packages=[""]`,
empty-manager `language_installs`) not blanking a good config. The `dep_capture`
schema contract — required fields, `language_installs` item shape, valid/invalid
instance acceptance, `minLength:1` on package/manager/command (empty-string
rejection), JSON round-trip, and wiring checks (`WORKER_TYPES`
exclusion, effort/model defaults) — is pinned in
`tests/test_dep_capture_schema.py` (mirrors `test_pr_writer_schema.py`).
The model/effort resolution precedence for `dep_capture` is pinned in
`tests/test_resolve_dep_capture_model.py` (mirrors `test_resolve_models.py`
and `test_resolve_efforts.py`). `dep_capture`'s model override is
**env-var-only** — no `--model-dep-capture` CLI flag and no `model_dep_capture`
`leerie.toml` key (both were removed as dead slots); precedence is
per-worker env (`LEERIE_MODEL_DEP_CAPTURE`) > global CLI > global env >
global TOML > `MODEL_DEFAULT`. The file asserts a stray `args.dep_capture_model`
and a `model_dep_capture` TOML key are **not** honored. Effort: global CLI >
global env > global TOML > `EFFORT_DEFAULT_PER_WORKER["dep_capture"]`. It also
pins the `MODEL_DEP_CAPTURE_ENV` constant, `dep_capture` absent from
`MODEL_DEFAULT_PER_WORKER` (opus via the global `MODEL_DEFAULT` fallback), and
present in `EFFORT_DEFAULT_PER_WORKER` with value `"high"`.
The three orchestrator wiring seams that are only verifiable by source
inspection are pinned in `tests/test_dep_capture_wiring.py` (mirrors
`test_phase_finalize_capture_hook.py`'s `inspect.getsource` approach):
`main()`'s `KeyboardInterrupt` and `InterruptedBySignal` exit arms each
invoke `capture_repo_deps` inside their own `asyncio.run()` wrapped in a
non-fatal `try/except Exception`; `_run_phases()` calls
`_backstop_capture_prior_runs` before `phase_classify` (the SIGKILL /
crash recovery path); and the `dep_capture` prompt file exists alongside
`SCHEMAS['dep_capture']` (the §12 advisory + code-enforces split).
The P6 ranking contract (DESIGN §P6) is pinned in `tests/test_rank_repo_map.py`
across three classes: `TestSeedNeighborhoodRanking` (seed-adjacent nodes rank
above unrelated nodes — direct seed file, 1-hop neighbor, seed symbol biases
definer, all connected before unrelated, large-graph unrelated cluster at tail);
`TestTokenBudgetEnforcement` (output fits within explicit budget and within
`DEFAULT_CAPS["repo_map_tokens"]` when None; `None` budget equals the cap value;
empty map returns `""`); `TestBinarySearchShrink` (lowering the budget yields
shorter output and fewer files; increasing budgets yield non-decreasing lengths;
1-token budget yields empty or a single very-short entry). Fixture is built
directly (no `build_repo_map`) — isolates ranking. No LLM calls; deterministic.
The P1 recursive decomposition surface (DESIGN §P1) is tested across four
files. `tests/test_fit_judge_schema.py` covers `SCHEMAS["fit_judge"]` —
required fields (`score`, `rationale`, `diffuse`, `confidence`), `score`
bounds (minimum 0, maximum 1), `confidence` using the `"fit"` axis, valid and
invalid instance acceptance, JSON serializability, and wiring (`fit_judge` in
`WORKER_TYPES`, not in `MODEL_DEFAULT_PER_WORKER`, `EFFORT_DEFAULT_PER_WORKER`
entry at `"high"`, prompt file exists). `tests/test_splitter_schema.py` covers
`SCHEMAS["splitter"]` — `children` required with `minItems:1`, child required
fields (`id`, `title`, `success_criteria_seed`), optional child fields,
valid/invalid instances, JSON serializability, the same wiring guards, no
top-level `files` field (splitter never decides partition), and the child
`requires` array uses the `_REQUIRES_ITEM` shape (tag + extent enum).
`tests/test_resolve_fit_judge_model.py` and
`tests/test_resolve_fit_judge_splitter_model.py` cover model and effort
resolution for `fit_judge` and `splitter` — both in `WORKER_TYPES`; both absent
from `MODEL_DEFAULT_PER_WORKER` (opus via global `MODEL_DEFAULT` fallback); both
in `EFFORT_DEFAULT_PER_WORKER` at `"high"`; per-worker CLI/env/TOML override
chains; isolation (override doesn't bleed to other workers); structural wiring
guards. `tests/test_partition_files.py` is the dedicated test for `partition_files()`:
44 tests across parametrized invariant sweeps (input sizes 0, 1, 8, 29, 64;
chunk-size 1, equals-n, larger-than-n, partial-last-chunk) plus named
telemetry cases — the 29-file migration sweep and 64-file date-fns sweep that
drove the design (LLM silently dropped 14/29; code-partition is complete by
construction). Asserts: 100% coverage (sum of chunk lengths == len(input)),
zero overlap (no file in two chunks), chunks bounded by chunk_size, and order
preserved. `tests/test_recursive_decompose.py` covers `recursive_decompose()`
(well-fit subtask is a leaf at score ≥ 0.70, oversized subtask recurses then
children are judged, depth cap terminates at `decompose_max_depth`, no-progress
guard terminates after `decompose_noprogress_rounds`, migration path uses
`partition_files` not the splitter LLM, `st.bump_workers` called before every
`claude_p`); it also carries a parallel set of structural `partition_files`
tests for regression coverage within that file.
`tests/test_recursive_decompose_schedule.py` is the integration test for the
seam between Layer B and the existing scheduler (DESIGN §P1 end-of-pipeline
claim): leaf ids from `recursive_decompose` carry a valid domain prefix so
`schedule()` cross-domain wiring and `validate_plan`'s id-prefix check both
pass; a ready plan built from stubbed leaves feeds `schedule()` and produces
the correct topo-sorted wave partition (independent leaves in wave 0, a
dependent leaf in wave 1); and `validate_plan` accepts the full leaf set
without errors.
The four new `DEFAULT_CAPS` values introduced by the F1 P6+P1 work are
pinned in `tests/test_decompose_caps.py`: `repo_map_tokens==1000`,
`decompose_max_depth==5`, `decompose_fit_threshold==0.70` (with a comment
citing F1-build-measure.md — the 0.95 value it replaced over-splits 100% of
well-fit subtasks), and `decompose_noprogress_rounds==2`. Mirrors the
`test_default_cap_is_eight` pattern from `test_resolve_confidence_rounds.py`.
The P6 repo-map builder is pinned in two files.
`tests/test_repo_map.py` covers `build_repo_map` (symbol/def extraction, class
methods, ref edges, relative-path keys, empty-repo, skip-.git/node_modules),
the mtime cache (dir created on first use, unchanged file served from cache
sentinel, changed file re-parsed, only-changed file re-parsed),
`rank_repo_map` (string result, token-budget fits, seed-file/seed-symbol bias,
empty map, determinism, very-tight budget), `_parse_repo_file` (unsupported
extension, markdown, python defs + refs), `_walk_calls` (bare call extracted,
attribute call not extracted), and `_pagerank` (dangling node, personalization,
empty). `tests/test_build_repo_map.py` (added by subtask test-001) provides a
focused HAS_TREESITTER-gated supplement: symbol graph (defs, class defs, ref
edge, keys shape, relative-path invariant), mtime cache (cache dir created,
sentinel cache hit, changed file re-parsed, only-changed file re-parsed with
sentinel for unchanged), and graceful degrade (empty file, binary file, empty
repo, skip-.git/node_modules). Uses a `pytestmark` module-level skip gate so
CI without tree-sitter-language-pack skips all tests cleanly.
The P6 Layer A wiring — `phase_plan` ctx injection — is tested in
`tests/test_phase_plan_repo_map_ctx.py`: repo-map enabled path (ctx contains
`repo_map` string, non-empty, JSON-serializable, contains known symbol names,
seed_files from `task_file_items` respected); skip path (ctx omits `repo_map`,
baseline keys present, values match inputs); empty-repo degrade (`rank_repo_map`
returns `""` → key omitted); exception-swallow degrade (`build_repo_map`
raises → exception caught, ctx emitted without `repo_map`).
The P1 Layer C wiring — `phase_plan` recursion expansion — is tested in
`tests/test_phase_plan_recursion_wiring.py`: source-coupling guard (`phase_plan`
source contains `recursive_decompose(` at depth=0, reassigns `plan["subtasks"] = leaves`,
expansion loop precedes final logging); integration — one oversized subtask (stubbed
`recursive_decompose` → two leaves) → `plan["subtasks"]` has 2 entries; two
first-pass subtasks → `recursive_decompose` called once per subtask; well-fit
leaf pass-through (stub returns input unchanged → single-element `plan["subtasks"]`);
empty-subtasks plan not touched (`recursive_decompose` never called, subtasks stays `[]`).
No coverage
target is set — the suite was introduced from scratch and a number
now would be arbitrary.

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
