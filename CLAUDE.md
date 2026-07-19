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
# through Fly.io machines instead of local nerdctl containers; `ec2`
# resolves AWS credentials the same way the AWS CLI/SDKs do (env vars >
# named profile > SSO cached token; see scripts/remote/aws-credentials.sh)
# and runs `require_aws()` preflight, but instance provisioning itself
# (the create/wait-ready/teardown dispatch) has not been wired into the
# launcher yet — `--runtime ec2` dies with an actionable message right
# after preflight passes.
export LEERIE_RUNTIME=local              # or: fly, ec2
export LEERIE_FLY_APP=my-leerie-app      # required for --runtime fly (globally unique)
./leerie "task" --runtime fly
# …or commit a leerie.toml at the repo root with: runtime = fly

# ec2 runtime knobs (leerie-level knobs for which AWS region/profile
# leerie itself uses when provisioning EC2 machines — distinct from the
# AWS SDK's own AWS_REGION/AWS_PROFILE credential-chain env vars, which
# resolve independently via the standard AWS precedence order). CLI flag,
# env var, or leerie.toml — same CLI > env > file precedence as --runtime:
export LEERIE_AWS_REGION=us-east-1       # or: leerie.toml aws_region = us-east-1
export LEERIE_AWS_PROFILE=my-aws-profile # or: leerie.toml aws_profile = my-aws-profile
./leerie "task" --runtime ec2 --aws-region us-east-1 --aws-profile my-aws-profile

# ec2 instance-shape vars (the RunInstances params provision_instance()
# needs — AWS account resources leerie cannot default on your behalf, so
# there is no fallback tier: CLI > env > leerie.toml > die() with setup
# instructions):
export LEERIE_EC2_AMI=ami-0abcdef1234567890
export LEERIE_EC2_INSTANCE_TYPE=t3.large
export LEERIE_EC2_KEY_NAME=my-ec2-keypair
export LEERIE_EC2_SECURITY_GROUP=sg-0123456789abcdef0
export LEERIE_EC2_SUBNET_ID=subnet-0123456789abcdef0
./leerie "task" --runtime ec2
# …or commit a leerie.toml at the repo root with:
#   ec2_ami = ami-0abcdef1234567890
#   ec2_instance_type = t3.large
#   ec2_key_name = my-ec2-keypair
#   ec2_security_group = sg-0123456789abcdef0
#   ec2_subnet_id = subnet-0123456789abcdef0
# …or pass them as CLI flags per run:
./leerie "task" --runtime ec2 \
  --ec2-ami ami-0abcdef1234567890 --ec2-instance-type t3.large \
  --ec2-key-name my-ec2-keypair --ec2-security-group sg-0123456789abcdef0 \
  --ec2-subnet-id subnet-0123456789abcdef0

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

# Skip the P6 repo-map structural context (DESIGN §5½ (P6) *Codebase
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
`resolve_runtime`, `resolve_aws_region`, `resolve_aws_profile`,
`gather_answers` validation gate, `_retryable_failure`,
`check_merge_committed`, `validate_result`, `validate_plan`,
`_validate_run_json`, `_derive_run_status`, `_load_blt_config`,
`resolve_blt`)
including a coupling test that the
retry-policy markers match the live check-function strings.
`resolve_aws_region`/`resolve_aws_profile` (the `LEERIE_AWS_REGION`/
`LEERIE_AWS_PROFILE`/`leerie.toml` knobs for which region/profile leerie
itself uses when provisioning `--runtime ec2` machines, distinct from the
AWS SDK's own credential-chain env vars) are covered in
`tests/test_resolve_aws_prefs.py`, mirroring `test_resolve_runtime.py`'s
CLI/env/file precedence structure but for the unvalidated free-form-string
`_resolve_str_pref` machinery (no enum, no `die()` path). The launcher-side
EC2 instance-shape vars (`LEERIE_EC2_AMI`/`_INSTANCE_TYPE`/`_KEY_NAME`/
`_SECURITY_GROUP`/`_SUBNET_ID` — the five `RunInstances` params, distinct
from the region/profile prefs above) are covered in
`tests/test_resolve_ec2_vars.py`: the bash `_resolve_ec2_knob` CLI > env >
`leerie.toml` > (no default) ladder reproduced and pinned against the real
launcher source (`test_block_present_in_launcher`), per-var isolation,
`=`-form CLI flags, the env-forwarding denylist guard (these vars must
never leak into the container), and `ec2-lib.sh`'s `_resolve_ec2_var`
required-var-read contract (prints on success, actionable
"not set — required for --runtime ec2" error + rc 1 on an unresolved var,
never a bare `${VAR:?}`). The
remote (Fly.io) bash surface — `ensure_image`, `provision_machine`,
`stop_machine`, `decide_teardown`, `resume_machine`, and `lib.sh`'s
`update_run_json` — is tested via bash-harness subprocess tests with
stubbed `flyctl`. Fly **volume** reaping is covered in
`tests/test_provision_volume.py`: Fly volumes outlive their machines by
design (no platform-side lifecycle hook — *"a Machine can be destroyed
without destroying its volume"*), so every path that kills a machine must
reap the volume itself, and three paths silently did not. The tests pin
`destroy_volume` reaping with an **empty** `LEERIE_MACHINE_ID` (it must not
live behind `destroy_machine`'s early return — that made the volume block
unreachable exactly when the machine had already died);
`_resolve_volume_id_from_run_dir` **falling through** a `fly-machine.json`
that lacks `volume_id` to `run.json` (provision writes the former
conditionally, the latter always); `_resolve_volume_id_from_fly` reading
`config.mounts[].volume` out of `machine list --json` (the stub emits the
shape measured against a live machine — `machine status` has no `--json`
flag, so it is deliberately unused); and end-to-end that
`--kill --machine-id <id>` with **no run dir** still reaps, with the
load-bearing ordering asserted by call index: **Fly lookup → machine
destroy → volume destroy** (the volume→machine link vanishes with the
machine, but Fly refuses to destroy a still-attached volume, so the reap
must sit between those two events). Harness note: the launcher's state-dir
override is `LEERIE_STATE_DIR`, **not** `LEERIE_STATE_HOST_DIR` — setting
the latter silently resolves to the real `~/.leerie/...` and the test
asserts nothing. The local per-repo image surface —
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
a fake cgroupfs. Memory-OOM naming (DESIGN §6 *Detecting memory OOM*) —
the `empty_handoff` seam that prefers a worker's named OOM cause (offending
command + `memory.max`) over `validate_result`'s generic "checkpoint ...
does not exist" text — is pinned end-to-end through `settle_subtask` in
`tests/test_oom_naming.py`: both empty_handoff branches (the no-commits
`fail()` path and the has-commits rescue path that keeps the diff and
logs instead of discarding it) surface the named cause when
`run_implementer`'s synthesized `incomplete-handoff` envelope carries one,
including the `--worker-memory-max` / `--max-parallel` remediation
pointer; a healthy no-op empty_handoff (no named cause) does not
fabricate an "OOM-killed" message. Mid-run PID reaping (DESIGN §6 *Mid-run PID reaping*) is
tested in `tests/test_signal_cleanup.py`: `_reparented_orphans` selects only
alive+ppid==1+old PIDs sorted oldest-first (stubbed ps); `_poll_loop` reaps
only at ≥90% pressure and stops below 75% (hysteresis); below 90% is a
byte-identical no-op; attached (ppid!=1) PIDs are never reaped; and a
structural guard pins `cgroup_sid: str | None = None` on
`_DescendantTracker.__init__` so the 3 pre-existing direct-constructor call
sites remain compatible after the parameter was added. The age floor is
**two-tier** (DESIGN §6 *the critical tier*), so "young PIDs are never
reaped" holds only in the normal tier: below `_PID_REAP_CRITICAL_WATER` a
young orphan is protected by the 60 s floor
(`test_poll_loop_young_orphan_not_reaped`, which monkeypatches the critical
water *up* so that tier is reachable at all — the shipped constants are
equal at 0.90), and at or above it the floor drops to
`_PID_REAP_CRITICAL_AGE_SEC` (5 s) and the same orphan **is** reaped
(`test_poll_loop_young_orphan_reaped_at_critical_pressure`). The critical
tier is the fix for the measured burst case: a leak saturates `pids.max`
faster than the 60 s floor lets anything become eligible, so the reaper
armed, found an empty candidate list, and watched the worker die (run
879defae, wave 2). Reverting the tier fails that test with `assert 900 in
[]` — the empty list *is* the production bug. Note four of these tests were
previously **vacuous**: they stubbed `_cgroup_stat` with a 3-tuple while
`_poll_loop` unpacks 4, so the `ValueError` skipped the entire reaping
branch and they passed against code that never ran — including
`test_poll_loop_reaps_above_high_water`, which additionally asserted only
after `stop_and_reap()` (that path SIGKILLs `_seen` wholesale, so it passed
without any mid-run reap firing). Both traps are fixed and pinned; snapshot
`killed` *before* `stop_and_reap` in any new test here. Zombie reaping (DESIGN
§6 *Zombie reaping* — the container PID 1 is `runuser`/idle `sleep`, not a
reaping init, so orphaned git/ssh-agent descendants would pile up as `<defunct>`
against `pids.max`) is tested in `tests/test_subreaper.py`: `_become_subreaper`
is a bool-returning no-op off Linux and (Linux-guarded) sets the flag verifiable
via `prctl(PR_GET_CHILD_SUBREAPER)`; `_zombie_reaper` (Linux-guarded) reaps an
orphaned exited child so it's no longer a zombie and survives having no
children. The load-bearing race test is
`test_zombie_reaper_does_not_steal_unregistered_subprocess_status`: it spawns
40 short-lived asyncio children with the reaper hot at 1ms and **registers
nothing**, asserting every child reports its true code (7), not a fabricated
255. Registering would defeat the test's purpose — the production failure is a
pid that is unregistrable *by construction*, sitting in the window between
`fork()` and asyncio's `os.pidfd_open()`. The old design (scan `/proc` for
state==Z + ppid==getpid, minus `_ASYNCIO_MANAGED_PIDS`) passed a test that
registered the pid *before* starting the reaper — a sequencing production never
provides — while taking `preflight`'s own `git config` pid on 40/40 real runs.
Safety now comes from `_REAPABLE_PIDS`, an allowlist populated by
`_mark_reapable`. Paired with
`test_zombie_reaper_still_reaps_a_recorded_orphan` (a reaper that reaps nothing
is not a fix, it is a disabled reaper) and three source-coupling guards: the
reaper's source contains no `/proc`/`listdir`/`_orphan_zombie_children`
(docstring stripped via `ast` first, since it *describes* the forbidden scan),
`_DescendantTracker._poll_loop` calls `_mark_reapable` (the fix is inert
without the wiring), and `_mark_reapable` never admits an
`_ASYNCIO_MANAGED_PIDS` member; plus a
`_reparented_orphans`-accepts-`ppid==getpid` test, and source-coupling guards
that `main()` calls `_become_subreaper()` and `orchestrate()` spawns+cancels
`_zombie_reaper`. The `fetch_branch()` stream-back surface (`scripts/remote/fetch-branch.sh`)
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
The P6 ranking contract (DESIGN §5½ (P6)) is pinned in `tests/test_rank_repo_map.py`
across three classes: `TestSeedNeighborhoodRanking` (seed-adjacent nodes rank
above unrelated nodes — direct seed file, 1-hop neighbor, seed symbol biases
definer, all connected before unrelated, large-graph unrelated cluster at tail);
`TestTokenBudgetEnforcement` (output fits within explicit budget and within
`DEFAULT_CAPS["repo_map_tokens"]` when None; `None` budget equals the cap value;
empty map returns `""`); `TestBinarySearchShrink` (lowering the budget yields
shorter output and fewer files; increasing budgets yield non-decreasing lengths;
1-token budget yields empty or a single very-short entry). Fixture is built
directly (no `build_repo_map`) — isolates ranking. No LLM calls; deterministic.
The P1 recursive decomposition surface (DESIGN §5½ (P1)) is tested across four
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
`partition_files` for the file→chunk partition and invokes the splitter only in
label-only mode to title each chunk (distinct titles; deterministic fallback on
splitter failure), `st.bump_workers` called before every `claude_p`, both
`claude_p` call sites pass the full required signature (`cwd`/`autonomous`/`caps`
— the C0 regression guard), and a passed `repo_map` is re-ranked per node and
injected into fit_judge/splitter prompts); it also carries a parallel set of
structural `partition_files` tests for regression coverage within that file.
`tests/test_recursive_decompose_schedule.py` is the integration test for the
seam between Layer B and the existing scheduler (DESIGN §5½ (P1) end-of-pipeline
claim): leaf ids from `recursive_decompose` carry a valid domain prefix so
`schedule()` cross-domain wiring and `validate_plan`'s id-prefix check both
pass; a ready plan built from stubbed leaves feeds `schedule()` and produces
the correct topo-sorted wave partition (independent leaves in wave 0, a
dependent leaf in wave 1); and `validate_plan` accepts the full leaf set
without errors.
The post-ship gap fixes are pinned in `tests/test_recursive_decompose.py`
(C0: `test_recursive_decompose_calls_claude_p_with_full_signature` binds each
`claude_p` call against the real signature so a missing `cwd`/`autonomous`/`caps`
fails; G1: `..._migration_partition_owns_files_splitter_only_labels`,
`..._migration_children_have_distinct_labels`,
`..._migration_label_fallback_on_splitter_failure`; G2:
`..._injects_repo_map_into_worker_prompts`, `..._no_repo_map_when_none`),
in `tests/test_check_functions.py` (G3:
`test_low_decomposition_quality_does_not_gate`,
`test_low_task_understanding_still_gates` — the axis is advisory, only
`task_understanding` gates), and in `tests/test_repo_map_degrade_warning.py`
(G6: `build_repo_map` warns exactly once per process when source files exist
but the graph is empty, stays quiet for a non-code repo). `tests/test_repo_map.py`
now carries a `HAS_TREESITTER` module skip gate (G4) mirroring
`test_build_repo_map.py`.
`_tree_sitter_extraction_works()` itself — the functional probe the G4/G6
skip gates and degrade warning both delegate to — is pinned directly in
`tests/test_tree_sitter_probe.py`: the True branch (real, unstubbed
`_parse_repo_file` on a working tree-sitter host) is gated on
`HAS_TREESITTER` so it skips rather than fails on an incompatible host;
the two False branches — `_parse_repo_file` raising (simulates an
installed-but-incompatible language-pack version lacking `process()`) and
`_parse_repo_file` returning `([], [])` (extracts nothing) — are
host-independent and always run, since they are the load-bearing proof
that the probe fails closed regardless of the local tree-sitter install
state.
The gate *wiring* itself — as opposed to the probe's own runtime
contract — is pinned in `tests/test_repo_map_gate_wiring.py` via
source-coupling assertions (mirroring `test_dep_capture_wiring.py`):
`conftest._has_treesitter()`'s source references
`_tree_sitter_extraction_works` (proving delegation to the functional
probe, not a bare `ImportError` check); `conftest` exposes a
module-level `HAS_TREESITTER` bool; and each of `test_build_repo_map.py`,
`test_repo_map.py`, `test_phase_plan_repo_map_ctx.py` both imports
`HAS_TREESITTER` from `tests.conftest` and contains a `skipif`
referencing it (module- or class-level — `test_phase_plan_repo_map_ctx.py`
gates only its `TestRepoMapEnabled` class). This guards against a silent
regression — reverting to an ImportError-only gate, or dropping the
skipif from one file — re-introducing the 19-test host-sensitive failure
with no other signal.
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
The id-vanishing `depends_on` rewrite (DESIGN §5 *Id-vanishing operations* — every op
that removes a subtask id owes the plan a rewrite of inbound references; the tag
channel self-heals via inherited `provides`, so only the id channel dangles) is tested
across five files. `tests/test_remap_vanished_deps.py` is the unit surface for
`_remap_vanished_deps`: fan-out (a vanished parent → every leaf, mirroring the tag
channel's list-of-providers), prune (`id → []`, the drop case), empty-mapping no-op,
dep-absent-from-mapping pass-through (guards over-eager rewriting), dedup-after-rewrite
and two-vanished-ids-sharing-a-successor (mirrors `_apply_overlap_merge`), and the
`repl != sid` self-reference guard — pinned but documented as **currently dead code**:
it is unreachable because `schedule()` already die()s on a planner self-edge
(`feat-a → feat-a`) before recursion runs, and it is retained only to match
`_apply_overlap_merge`'s discipline for future callers.
`tests/test_recursive_decompose.py` covers the intra-generation remap — the seam
`phase_plan` cannot see: a splitter child declaring `depends_on` on a *sibling*
(`prompts/splitter.md`) whose id then vanishes when that sibling splits again, asserting
the survivor fans out to the terminal ids and the intermediate appears in no
`depends_on`; plus the migration-path no-op, which drives a **hostile** label-only
worker injecting sibling deps and proves `_migration_child` discards them (children keep
the parent's `depends_on`/`provides` verbatim, so the map stays empty on the ~84% path).
`tests/test_phase_plan_recursion_wiring.py` covers the cross-subtask remap: the reported
regression (a sibling of an expanded parent fans out to all leaves and `validate_plan`
no longer die()s — the exact gate that killed a real run after full planner spend),
a dep on an unexpanded subtask left untouched, and dedup when a sibling already names a
leaf. `tests/test_filter_satisfied_subtasks.py` and
`tests/test_filter_offtree_subtasks.py` cover the two phase-3 soft-drop filters, which
vanish ids the same way: a dropped id's inbound refs pruned (non-dropped deps survive),
`validate_plan` survives the drop end-to-end, and a no-drop run leaves `depends_on`
byte-identical. `tests/test_plan_snapshot_wiring.py` pins the `plan_snapshot` capture by
source inspection (mirroring `test_dep_capture_wiring.py`): the assignment is followed
by `st.save()`, follows `schedule()`, and precedes **both** `check_budget_feasibility`
and `validate_plan` — the ordering *is* the feature, since a die() at either gate
otherwise discards the whole planning spend (`write_plan` never runs); plus that it is
deliberately not `write_plan` (which would seed execution scaffolding for a run that
cannot start) and that the payload round-trips through a real `State.save()`.
`tests/test_decompose_snapshot.py` is `plan_snapshot`'s sibling for the D3 crash
barrier: a `WorkerError` from `recursive_decompose`'s `fit_judge` call degrades the
node to a leaf (`[subtask]` unchanged, not dropped, not propagated) rather than
discarding sibling subtasks' already-completed fit/split decisions. A `WorkerError`
from the coupled-minority `splitter` call (the non-migration split path, ~70 lines
below the `fit_judge` guard) degrades to a leaf the same way — `TestSplitterCrashBarrier`
pins this as the surviving half of D3: the `fit_judge` guard alone left this call
unguarded, so a crash there still discarded every fit/split decision already paid for
in sibling subtasks, including end-to-end through `phase_plan` that a sibling's
already-completed leaves survive a later subtask's splitter crash. `phase_plan`'s
expansion loop persists `st.data["decompose_snapshot"]` after each top-level subtask
finishes, so a later subtask's crash still leaves the earlier ones' completed leaves
in the snapshot, round-tripped through a real `State.save()`; a normal run's final
leaf count matches `plan["subtasks"]` (nothing silently dropped); and, mirroring
`test_plan_snapshot_wiring.py`'s `TestSnapshotPrecedesTheDieGates`,
`test_decompose_snapshot_precedes_the_die_gates` pins that `_run_phases` calls
`phase_plan` (which writes the snapshot) strictly before `check_budget_feasibility`
and `validate_plan` — the two gates that die() and would otherwise make a discarded
decomposition unrecoverable.
The conformer/baseline hardening (DESIGN §9 *No clobbering the implementer's
work* + the base-tree baseline's `measured` field) is tested across three
files. `tests/test_clobbered_owned_files.py` covers the clobber-survival guard:
`clobbered_owned_files` against real temp git repos (legit conformer edit not
flagged; revert-to-base flagged; deletion flagged; a file outside the
implementer's owned set never flagged; a new file added not flagged; the
load-bearing round-0 snapshot test — a per-round HEAD misses a round-0 clobber
while the pre-loop `impl_head_sha` catches it; empty-ref no-op), `_blob_sha`'s
present/absent contract (the missing-path returns None, guarding the bare
`git rev-parse <ref>:<path>` footgun), `rollback_conformer_commits` actually
restoring clobbered implementer content and dropping the conformer commit
(`TestRollbackRestoresClobber`), and source-coupling wiring guards that both
`_run_conformance_phase` and `run_final_conformance` snapshot before the round
loop and call the guard under `strict_conformer`.
`tests/test_normalize_pip_installs.py` covers `_is_pip_install` /
`_normalize_pip_installs` (adds `--break-system-packages` to
`pip`/`pip3`/`python -m pip install` recipe entries): the incident recipe
entries, `-e .`, `python -m pip`, idempotency (no double-add), non-pip and
non-install entries untouched, other fields preserved, and a source-coupling
guard that the normalization runs before `prov["recipe"] = recipe` in
`phase_provision`. `tests/test_base_health_baseline.py` additionally covers
`_runner_missing` (`command not found` / `No such file or directory`), the
`measured` field on baseline axes (an unmeasurable axis is surfaced as "could
not measure," folded into neither GREEN nor RED, by both
`_format_baseline_section` and `_base_health_payload`), and pins that `measured`
is a mandatory field with no legacy default (a `passed: False` axis missing
`measured` is not surfaced RED).
The standalone AWS credential/profile/region resolution helper
(`scripts/remote/aws-credentials.sh`, EC2 runtime) is tested in
`tests/test_aws_credentials.py` by sourcing the real script against a fake
`$HOME` with fixture `~/.aws/config`/`~/.aws/credentials`/`~/.aws/sso/cache/`
files (mirroring `tests/test_fetch_branch_sh.py`'s source-and-call pattern):
explicit env-var credentials winning over a fully-configured SSO profile
with a valid cached token; `AWS_PROFILE` selecting a named profile over
`[default]`; region precedence (`AWS_REGION` > `AWS_DEFAULT_REGION` >
profile `region` > die-with-hint); static credentials in
`~/.aws/credentials`; both `sso_session`-reference and legacy inline SSO
config; an expired SSO cache token and a never-logged-in profile both
producing the `aws sso login --profile <p>` hint rather than a silent
fallthrough; no `~/.aws` directory at all; `AWS_PROFILE=nonexistent` not
falling back to `[default]`; and `--profile`/`--region` CLI flags
overriding their env-var equivalents. Pure file I/O — no network, no `aws`
binary, no boto3. Not yet wired into the launcher's EC2 runtime path (that
lands in a separate subtask); this test file covers only the standalone
helper.
The EC2 runtime's host-side preflight (`scripts/remote/ec2-lib.sh`'s
`require_aws()`, modeled on `require_flyctl()` in `scripts/remote/lib.sh`) is
tested in `tests/test_ec2_lib_sh.py` by sourcing the real script against a
stubbed `aws` binary on PATH (mirroring `tests/test_ensure_image.py`'s
stubbed-flyctl pattern): success when `aws` is present and `aws sts
get-caller-identity` succeeds; an actionable AWS CLI v2 install hint when
`aws` is absent from PATH; the `aws sso login --profile <profile>` recovery
hint (reusing `bedrock_preflight()`'s exact vocabulary) when credentials are
unresolvable; profile resolution precedence (`--profile` passthrough,
`LEERIE_AWS_PROFILE` over `AWS_PROFILE`, `AWS_PROFILE` as fallback) reflected
in both the `aws sts get-caller-identity` call and the sso-login hint. Not
yet wired into the launcher's `RUNTIME=ec2` dispatch branch (that lands in a
separate subtask); this test file covers only the standalone helper.
The release workflow's previously-untested embedded shell
(`.github/workflows/release.yml`) is covered in `tests/test_release_workflow.py`,
which works against the raw YAML text (no pyyaml dependency) using the
extract-the-real-text-at-test-time pattern from `tests/test_config_verb.py`'s
`_extract_config_arm`: a regex table (including the v0.9.62 squash-merge
subject and every historical `chore(release):` subject on `main`, run live
rather than pinned to a stale count) and structural pins that the tag and
release steps gate on different `if:` conditions, that the release step
never references `tagcheck`, that `relcheck` exists and probes via
`gh release view`, that `gh release create` carries `--verify-tag`, and that
a final end-state step (gated on default `success()`, not `always()`) is the
job's last step and asserts both artifacts exist.
The resource-tracking `aws` stub state machine (`tests/ec2_stub.py`,
distinct from `test_ec2_lib_sh.py`'s argv-only `_stub_aws`) models EC2 as
a persistent state machine — `run-instances` creates a tracked instance
that `stop-instances`/`start-instances`/`terminate-instances` transition
through, and `create-volume`/`delete-volume` do the same for volumes —
so downstream lifecycle tests can assert on resource *leaks* rather than
merely inspecting argv. It exposes `_stub_aws(dir)` (writes the stub
binary plus an empty `state.json`/`aws.log`), `read_state(dir)`,
`read_log(dir)`, and `leaked_resources(state)` (non-terminated instances
and non-deleted volumes). State persists to `<dir>/state.json`; every
invocation's argv is appended to `<dir>/aws.log`. Self-tests in
`tests/test_ec2_stub.py` pin the state transitions (run-instances →
`running`; stop-instances → `stopped` without removing the record;
terminate-instances → `terminated`), `leaked_resources()` on both a
clean and an unclean teardown, multi-instance independence, the real
`aws` CLI's `--instance-ids i-1 i-2` space-separated multi-value flag
syntax (not a repeated flag), the log recording every invocation in
order, and a structural guard that the stub source contains no
networking imports (`socket`, `urllib`, `http.client`, `requests`,
`boto3`) so no invocation can reach a real AWS endpoint. Pure test
fixture — no dependency on `orchestrator/leerie.py` or
`scripts/remote/ec2-lib.sh`, importable ahead of the EC2 dispatch branch
landing. `ec2_stub.py` also implements `describe-instance-status`
(returns `InstanceStatus`/`SystemStatus` both `"ok"` for a `running`
instance, `"initializing"` when a test seeds `status_ok: False`),
consumed by `wait_for_instance_ready()`'s poll-until-both-ok contract.
`scripts/remote/ec2-provision.sh` (the `provision.sh` counterpart for
the EC2 lifecycle — `provision_instance()`, `wait_for_instance_ready()`,
`stop_instance()`/`terminate_instance()`, `decide_ec2_teardown()`; see
the Files table above) is tested in `tests/test_ec2_provision.py`
against the stateful `aws` stub: required-var validation (missing
`LEERIE_EC2_AMI` / missing `aws` binary both fail closed before any
call), instance-id export and `ec2-instance.json`/`run.json` sidecar
writes on a successful create, id-parsing against real-shaped
`run-instances` JSON output, a failed create leaking no resources and
never registering the teardown trap, `terminate_instance`'s no-op-on-
empty-id idempotency, and `decide_ec2_teardown`'s three-disposition
classification (clean-exit terminates, sync-failure leaves the instance
running, SIGINT detaches, unknown rc pauses) including that
`_try_fetch_state_for_ec2_teardown` runs before `terminate_instance`
(mirrors `provision.sh`'s fetch-before-destroy ordering) and that the
teardown routine is idempotent under `LEERIE_TEARDOWN_DONE`.
`tests/test_ec2_volume_reaping.py` pins the EBS-volume side of the same
script: DESIGN §6 "EBS volume lifecycle" case 1 (root volume only,
AWS's own implicit `DeleteOnTermination=true` default) means there is
no Fly-style `destroy_volume()` reap path to test — instead this file
pins the actual leak-prevention mechanism (`run-instances` invoked with
no `--block-device-mapping`/`--block-device-mappings` override, at both
the stub-argv level and via a source-level grep guard against
`DeleteOnTermination` appearing in the call block), that
`terminate_instance` (the sole reap path) is a true no-op making no AWS
call on an empty instance id, a full provision→terminate cycle leaking
neither instances nor volumes (with an explicit assertion that no
`create-volume` call ever happens, so the leak-free result isn't
vacuous), and a structural regression guard that no
`destroy_volume`/`reap_volume`-shaped function exists anywhere in
`ec2-lib.sh` or `ec2-provision.sh`.
The EC2 counterpart to `scripts/remote/seed-repo.sh` — `scripts/remote/
ec2-seed-repo.sh` (`ec2_seed_repo_clone`/`ec2_seed_repo_dirty`/
`ec2_seed_repo`, transported over `ec2-lib.sh`'s `ec2_tar_pipe`/
`ec2_remote_exec` instead of `flyctl ssh console`) is tested in two
files, modeled directly on `tests/test_seed_repo_sh.py` +
`tests/test_seed_repo_shallow_roundtrip.py`. `tests/test_ec2_seed_repo.py`
covers the transport-level contract against a stubbed `aws` (decodes and
locally executes `ec2_remote_exec`'s base64-wrapped SSM command,
rewriting `/work`/`/tmp/leerie-*` paths into the test's `dest` dir — same
technique as `test_ec2_transport.py`'s `_stub_aws_ssm`) and a stubbed
`ssh` (drains `ec2_tar_pipe`'s one-entry gzipped-tar payload when invoked
for bulk data, execs a real local `rsync --server` when invoked as
rsync's `-e` transport): preflight failures (missing instance id / ssh
target / `USER_REPO` / `aws` on PATH); a minimal repo round-trips to
`/work`; both `aws` and `ssh` are exercised and `flyctl` never appears in
the transport log; `.gitignore`-awareness plus `.claude/`
force-inclusion via the rsync delta; the `.leerie/config.toml` /
`.leerie/Dockerfile` / `.leerie/.leerie-setup.sh` whitelist (all other
`.leerie/*` paths dropped); NFC-filename preservation through a
submodule bundle; and a stalled `ssh` transport (real, unstubbed
`timeout`) yielding a non-hanging failure. `tests/
test_ec2_seed_repo_shallow.py` reproduces the shallow-path host/instance
commands directly (coupled to the real script via `test_
reconstruction_matches_source`, which asserts the exact clone/tar/
checkout strings are still present) to pin: checkout parity between the
shallow instance tree and the host tip, `.git/shallow` staying shallow,
NFC-filename survival, a fetch-back-by-branch-name round-trip whose
merge-base equals the host tip (PR-diff correctness), and
`_seed_branch_shallow_safe`'s shell-injection gate (safe vs. unsafe
branch names, including the live `__PARENT_MATERIALIZE__`/
`__CLEANUP_TMP__` placeholder tokens) invoked against the real function
rather than a reproduction of it.
The EC2 counterpart to `scripts/remote/seed-auth.sh` —
`scripts/remote/ec2-seed-auth.sh`'s `ec2_seed_auth()` — is tested in
`tests/test_ec2_seed_auth.py`, modeled on `tests/test_seed_auth_sh.py`
and reusing `tests/test_ec2_seed_repo.py`'s stubbed-`aws`/stubbed-`ssh`
transport harness (the `aws` stub decodes and locally executes
`ec2_remote_exec`'s base64-wrapped SSM command, rewriting `/home/leerie`
into the test's `dest` dir; the `ssh` stub drains `ec2_tar_pipe`'s
gzipped-tar-of-`$STAGE` payload into the same rewritten dest): a
`$STAGE` dir containing `.claude/`, `.claude.json`, and `.gitconfig`
round-trips to the instance's home dir with ownership fixed to
`leerie:` (asserted via a `chown_log` sink so the test observes the real
script issuing the call, not just its source text); the
`CLAUDE_CODE_OAUTH_TOKEN` fallback writing a valid single-token
`.credentials.json` when `$STAGE` has none; `plugins/cache` and
`plugins/marketplaces` excluded from the tar (both a positive check that
the exclude list matches `seed-auth.sh`'s original and a check that
files outside those dirs are not swept up by the same exclusion);
preflight failing closed on missing `LEERIE_EC2_INSTANCE_ID` /
`LEERIE_EC2_SSH_TARGET` / `STAGE` / `aws` on PATH / credentials-or-token
/ git identity; git identity written to `/home/leerie/.gitconfig`;
`flyctl` never appearing in the transport log while `aws`/`ssh` both do;
and a stalled transport (the process-group-killing `_stub_timeout`
imported from `tests/test_ec2_transport.py` — the local no-op passthrough
stub would hang for the full sleep, per the CLAUDE.md test-harness trap
documented above) yielding rc 124/137 rather than hanging, bounded by
`LEERIE_SEED_TIMEOUT_S`.
The EC2 instance lifecycle itself (`scripts/remote/ec2-provision.sh`'s
`provision_instance()`/`wait_for_instance_ready()`/`stop_instance()`/
`terminate_instance()`/`decide_ec2_teardown()`) is covered across two
files. `tests/test_ec2_provision.py` (landed with the lifecycle
implementation) covers the broader surface: instance creation, the
running+ok readiness poll, stop/terminate idempotency on an empty
instance id, and the sidecar writes. `tests/test_ec2_decide_teardown.py`
is the dedicated, deeper pin for `decide_ec2_teardown()`'s
`$LEERIE_REMOTE_EXIT_RC` classification table — the highest-consequence
EC2 behavior, mirroring `tests/test_decide_teardown_auto_finalize.py`'s
Fly coverage: each clean-exit rc (0/10/11/75) syncing state via
`_try_fetch_state_for_ec2_teardown` before calling `terminate_instance`;
a sync failure on any clean-exit rc leaving the instance `running` with
no `terminate-instances`/`stop-instances` call ever reaching the `aws`
stub's log (the one-way-ratchet invariant — destroy-then-fetch would
make paid-for LLM work unrecoverable); rc=130/143 taking the detach-
banner arm without pausing; any other non-zero rc stopping (never
terminating) the instance and recording `pause_reason` in the run
sidecar; the fetch-before-terminate ordering independently verified via
a hook that asserts the instance is still `running` at the moment
`_try_fetch_state_for_ec2_teardown` runs; and `LEERIE_TEARDOWN_DONE`
idempotency surviving a double-fire (INT then EXIT) in both directions
(clean-exit-then-pause and pause-then-clean-exit) even when
`LEERIE_REMOTE_EXIT_RC` is clobbered between the two calls.
The EC2 stream-back counterpart to `fetch-branch.sh` —
`scripts/remote/ec2-fetch-branch.sh`'s `fetch_state_ec2()` — is tested in
`tests/test_ec2_fetch_branch.py`, modeled on `tests/test_fetch_branch_sh.py`
+ `tests/test_fetch_branch_leerie_streamback.py` and using
`tests/test_ec2_seed_repo.py`'s stubbed-`aws`/stubbed-`ssh` transport
harness (`aws` decodes and locally executes `ec2_remote_exec`'s
base64-wrapped command; `ssh` streams the private download helper
`_ec2_fetch_ssh`'s raw remote-command stdout straight back, since
`ec2_tar_pipe` itself is upload-only): a branch committed on the
instance round-trips to the host as a fetchable bundle whose tip matches
the instance-side tip; the run-state tar extracts under
`LEERIE_STATE_HOST_DIR` (or `USER_REPO/.leerie` by default) and the
`no_push` mechanism flag is stripped only on the branch-present path
(preserved as intent on the cleared-but-empty terminal-state path, same
conditional as `fetch-branch.sh`); `.leerie/config.toml` and
`.leerie/Dockerfile` stream back when the host has neither, are never
clobbered when the host already has one, and are non-fatal when absent
on the instance; and both `aws` and `ssh` appear in the transport log
while `flyctl` never does.
The launch/attach counterpart to `flyctl ssh console` — `scripts/remote/
ec2-ssm.sh`'s `ec2_launch_detached()`/`ec2_attach()` — is tested in
`tests/test_ec2_ssm.py` against a stubbed `aws` binary that models
`ssm start-session`'s two defining quirks: it always exits 0 itself
regardless of the wrapped remote command's real exit status (the
documented session-manager-plugin limitation both `ec2_remote_exec` and
this file work around via an rc-sentinel), and it is a genuinely
interactive session that drains its own stdin and execs it as the
bootstrap interpreter's program — unlike `test_ec2_transport.py`'s
`_stub_aws_ssm`, which only ever inspects the `--parameters` value and
never touches stdin. Pinned: both functions issue `aws ssm start-session
--target <id> --document-name AWS-StartInteractiveCommand`; rc=75 (the
flock-loser smart-resume pivot) and other nonzero remote rcs survive the
round trip uncorrupted; both fail closed (rc 1, actionable stderr, no
`aws` call) on an empty `LEERIE_EC2_INSTANCE_ID`; a stalled session
yields 124/137 via the same `_seed_timeout_prefix` convention
`ec2_remote_exec` uses; `--profile`/`--region` passthrough; a payload
well over SSM's ~4 KB `--parameters` ceiling still round-trips cleanly
since only the interpreter name (`python3 -` / `sh -s`) goes in
`--parameters` and the real payload travels over the session's stdin;
`ec2_attach`'s `sh -s` bootstrap is verified by decoding the
base64-wrapped `command=[...]` value rather than asserting on plaintext
no longer in the log; and double-sourcing is idempotent and does not
clobber `ec2_remote_exec`. `flyctl` never appears in the transport log.
Also added to `tests/test_ec2_bash32_portability.py`'s `_EC2_SCRIPTS`
list for bash 3.2 sourcing coverage.
The launcher's `RUNTIME=ec2` dispatch branch itself — the seam none of
the above can see, since they test `ec2-lib.sh`/`ec2-provision.sh`
standalone rather than the `leerie` launcher's own dispatch — is
covered in `tests/test_ec2_e2e_provision.py`: the branch is extracted
verbatim from the launcher (mirroring `tests/test_launcher_env_forwarding.py`'s
`_extract_forwarding_loop` approach, since sourcing `leerie` directly
runs preflight + full CLI dispatch) and run against `tests/ec2_stub.py`'s
resource-tracking `aws` stub. It pins that `require_aws`'s `sts
get-caller-identity` call precedes any `ec2 run-instances` call by
call index (mirroring `tests/test_provision_volume.py`'s ordering
discipline), and that a failing credential probe aborts the launch
non-zero, emits the `aws sso login --profile <p>` hint, and leaves
zero tracked instances and volumes in the stub's state — both with
provisioning wired in after the dispatch block and with the dispatch
block alone, so the gate is pinned as the branch's own contract
independent of what runs after it. The module also defines the shared
bash harness (stub-on-PATH + launcher invocation helpers) that sibling
EC2-dispatch test modules import. A dedicated
`test_successful_provision_leaves_exactly_one_instance_and_no_orphaned_volume`
pins the provision-success resource count against the stub's *tracked
state* rather than argv/log line counts: exactly one instance (not
zero — a no-op regression; not two — a double-provision regression,
both falsified live against hand-broken harness variants during
development) and zero tracked volumes, since `provision_instance()`
never calls `create-volume` — root EBS is implicit via `run-instances`
with AWS's own `DeleteOnTermination=true` default (DESIGN §6 "EBS
volume lifecycle" case 1) — so any tracked volume on this path would by
construction be an orphan.
The no-result-event retry (DESIGN §6, `claude -p` exits 0 having streamed a
full session but never emits its terminal `result` event — upstream
anthropics/claude-code #8126/#1920/#74761, unresolved) is pinned in
`tests/test_no_result_event_retry.py`: `_invoke` returns a synthetic
`_leerie_synthetic: "no_result_event"` envelope rather than raising, so
`claude_p`'s existing 2-attempt loop absorbs it (a raised WorkerError
propagated past that loop and die()d the run non-resumably). The
load-bearing test is
`test_synthetic_envelope_is_not_an_auth_or_quota_failure`: it extracts the
**real** message from `_invoke`'s source via `ast` rather than asserting
against a copied fixture — `_is_auth_or_quota_failure` falls back to text
markers (`rate limit` / `invalid authentication`) on `result`, so a
hand-copied fixture passes happily while the shipping message silently
diverts every no-result retry into the tenacity backoff and burns the whole
`auth_retry_max_sec` budget (verified: the copied-fixture version of this
test does **not** fail when the landmine is introduced; the ast-extracted
one does). Controlling leerie's own message is **not sufficient**, and
assuming it was is how the bug shipped: the envelope interpolates the
worker's **raw stderr** into `result`, so a worker whose stderr merely
mentions auth or rate limiting trips the same markers. The fix is an
exemption in `_is_auth_or_quota_failure` for `_leerie_synthetic` envelopes
(the numeric `api_error_status` check still runs first and still wins);
`test_worker_stderr_cannot_trip_the_auth_classifier` pins it against three
realistic stderr payloads, and
`test_real_envelopes_still_match_the_text_markers` guards the exemption
from over-reaching. Paired with a source-coupling guard that the synthetic return is
the **last** arm of the no-envelope block — every arm above it (overage,
OOM, nonzero rc) is a named non-retryable condition that still raises, and
the nonzero-rc arm in particular covers leerie's own deliberate
SIGTERM/SIGKILLs, which must never be retried.
`tests/test_warnings_before_die.py` pins the ordering that made that bug
undiagnosable in the first place: all four judgment phases (classifier,
provision, reconciler, plan_overlap_judge) log their `_run_checked_loop`
warnings — which carry the underlying exception text — **before** `die()`,
since `die()` calls `sys.exit()` and any loop after it is unreachable
(falsified live: reverting one site fails the guard).
`_run_checked_loop`'s crash policy is pinned in `tests/test_checked_loop.py`:
a `WorkerError` (infrastructure — PID exhaustion, OOM, a killed session) is
**retried** against the same `judgment_check_rounds` budget, because the
re-invocation is a fresh `claude -p` session with a clean PID table — which
is what `_read_stream`'s own PID-cap message already promised ("a fresh
worker retries") and what was true for implementers but false for every
`_run_checked_loop` caller until the retry existed. Any *other* exception is
a leerie bug rather than a flaky worker, so it still abandons the loop
immediately (`test_loop_crash_breaks`, which uses `RuntimeError` precisely to
pin that split). Also pinned: all-rounds-crash still returns `None` so the
callers' `is None` escalation is unchanged, the retry is bounded at exactly
`max_rounds`, and a crash must clear `last_res` so a stale earlier result is
never returned as the crashed round's output.
The integrator-crash salvage path (DESIGN §12 *salvage if there is something
to salvage*) is tested in `tests/test_rescue_integrator_work.py` against real
temp git repos left mid-merge. `rescue_integrator_work` captures a crashed
integrator's in-progress resolution to `refs/leerie/rescue/<run-id>/<sid>`
before `git merge --abort` destroys it (verified: abort reverts a resolved
file to its pre-merge content, leaving no stash and no reachable object). The
load-bearing pin is `test_rescue_does_not_require_a_merge_commit`: the rescue
must **not** be gated on `check_merge_committed`, because a crashed
integrator typically dies mid-resolution having committed nothing —
`integrator-feat-006` never ran `git commit` while `integrator-feat-005` did
— so a commit-gated rescue declines exactly the case worth saving.
Introducing that gate fails 4 tests. The mechanism is a throwaway
`GIT_INDEX_FILE` seeded from HEAD, because both `git stash push` **and** `git
stash create` refuse a conflicted tree ("Cannot save the current index
state") — an unmerged index is precisely what an integrator crash leaves
behind. Also pinned: untracked files are captured, the real index/worktree
and `MERGE_HEAD` are untouched, the temp index is cleaned up, refs are
namespaced per run+subtask so two crashes cannot clobber each other, and a
tree identical to `HEAD^{tree}` returns `None` rather than a ref naming an
empty diff.
`tests/test_resolve_run_id_autopick.py` covers bare `--resume` auto-picking
the newest resumable run (`in-progress`/`paused`/`incomplete`), including
the two traps found by running the design against a real 58-run state dir:
`seed-failed` rows carry no `started_at` and sorted to the *top* of a naive
newest-first sort (they are now list-only, never auto-picked), and a
missing `started_at` must never outrank a real timestamp. An explicit
run-id stays exempt from the filter (so `--resume <seed-failed-id>` still
works) and an unknown one still fails closed. The `seed-failed` exclusion
is a deliberate behavior change with a UX cost, pinned by
`test_resolve_run_id.py::test_resolve_lone_orphan_is_not_auto_resumed`:
bare `--resume` used to auto-pick a *lone* orphan, and now dies instead —
a seed-failed run aborted before `phase_classify` and needs an operator
decision (re-seed vs. kill), since resuming blind can re-trigger the same
seed failure. The die is therefore required to stay actionable (names the
run, its `status=seed-failed`, and the explicit-id escape hatch), because
that escape hatch is the documented recovery path for the 2026-06-04
hangs. `--report`/`--phase` still auto-pick a lone orphan — they are
read-only.
`tests/test_container_entry_run_id.py` covers `container-entry.sh` skipping
its cidfile `--run-id` injection when `--resume` is present — a resume
container is a *new* container whose id matches no run on disk, which is
what made bare `--resume` die naming an id the user never typed. The
injection block is extracted from the real script at test time (the
`_extract_config_arm` pattern) so it cannot drift.

**The EC2 shell surface must run on bash 3.2** — macOS's `/bin/bash`, and
the shell the EC2 tests actually get (they pin `PATH` to
`{stub_dir}:/usr/bin:/bin` to isolate their stubbed `aws`, which excludes
Homebrew's bash 5). CI is `ubuntu-latest`, so it **structurally cannot**
catch a bash-4-only construct; two of them lived in `ec2-lib.sh` /
`ec2-provision.sh` and showed up only as 33 failing tests on a
developer's Mac. `tests/test_ec2_bash32_portability.py` is the guard: it
sources each EC2 script under a real `/bin/bash` with `set -u` and no
`LEERIE_AWS_*`/`AWS_*` (the default config, which leaves every
optional-arg array empty), **and calls the functions that expand those
arrays** — sourcing alone is not enough, since an unguarded
`"${arr[@]}"` sits inside a function body the shell never evaluates until
called (verified: the source-only version of this test passes with the
bug reintroduced). It skips cleanly on hosts whose `/bin/bash` is ≥ 4.3,
so it is a macOS-developer guard, never a CI flake. Paired with a
source-level `local -n` / `declare -n` ban (namerefs are bash 4.3+;
echo the tokens instead — see `_aws_region_profile_args`).
The guard was extended (test-006) to cover every EC2 launcher arm wired
by test-001..test-005: `_EC2_SCRIPTS` gained `ec2-resume-instance.sh`,
`ec2-seed-auth.sh`, and `ec2-fetch-branch.sh` (all sourced by the
launcher's EC2 arms but previously untested here); `_EXPANSION_CALLSITES`
gained `resume_instance`; and a new
`test_ec2_launcher_verb_runs_cleanly_under_bash32` runs the real `leerie`
binary itself (not just `scripts/remote/ec2-*.sh`) under bash 3.2 for
`--stop`/`--kill`/`--accept-blocked` with `LEERIE_AWS_PROFILE`/
`LEERIE_AWS_REGION` unset, since each of those arms builds its own
optional-arg array from those two vars directly in `leerie` before
calling `resolve_aws_credentials`. This surfaced a real, previously
unguarded instance of the class: all four call sites
(`--accept-blocked`, `--stop`, `--kill`, and the main `RUNTIME=ec2`
dispatch) expanded their creds-args array as a bare `"${arr[@]}"`
instead of `${arr[@]+"${arr[@]}"}` — fixed in the same change. The
nameref ban was likewise extended to `leerie` itself
(`test_no_namerefs_in_launcher`).

**Host-only tests are gated on `jq`** (`HAS_JQ` in `tests/conftest.py`,
mirroring the `HAS_TREESITTER` pattern). Four modules —
`test_host_finalize_sh.py` (19 tests), `test_decide_teardown_auto_finalize.py`
(2), `test_launcher_finalize_no_work.py` (1), `test_launcher_no_push_skips.py`
(1) — source bash the **host** owns: `scripts/host-finalize.sh`,
`provision.sh`'s `decide_teardown`, and the launcher's `--finalize` /
`no_push` paths. All parse `run.json` with real `jq`. The harnesses stub
`git` and `gh` onto PATH but not `jq`, so jq is silently inherited from
whichever machine runs pytest — it passes on a dev host and in CI (both ship
jq) and failed only inside the leerie image, which deliberately omits it.
That is the host/container split: host bash uses `jq` (the launcher
hard-fails at preflight without it — "jq not found on PATH", `brew install
jq`), while code running *inside* the container uses python3, exactly as
`scripts/remote/seed-auth.sh` documents ("python3 over jq because jq isn't in
the leerie image (see Dockerfile)"). `gh` **is** in the image for the mirror
reason: Python inside the container preflights for it.
**Do not "fix" a skip here by adding `jq` to the Dockerfile.** Per DESIGN §6
*Finalization* those scripts can never succeed in-container anyway (gh auth,
ssh-agent, and Keychain are host-side), so installing jq buys a green tick,
not working code, and erodes the boundary. Note a `grep jq` does **not**
reproduce the gated list — two of the four never mention jq and fail only
because the script under test shells out to it; the list is measured from a
real in-container run. `tests/test_jq_gate_wiring.py` is the guard-the-guard
(conftest exposes a module-level `HAS_JQ` bool derived from a live
`shutil.which` probe; each of the four both imports it and carries a
`skipif` referencing it) — dropping one file's skipif fails it, which is the
same silent regression the `HAS_TREESITTER` gate exists to prevent.

Three test-side traps in the same area, all of which made a test pass or
hang while proving nothing:
`tests/test_ec2_transport.py::_stub_timeout` must **kill the process
group**, not just the direct child — macOS ships no `/usr/bin/timeout`,
so `_seed_timeout_prefix` correctly no-ops on the stubbed PATH and a
stall test's `sleep 600` runs unbounded (a 10-minute hang, not a
failure); and killing only the child leaves its grandchildren holding the
captured stdout, so a `$(...)` capture blocks until every writer closes
the pipe. Real GNU `timeout` kills the group for exactly this reason.
`tests/test_ec2_seed_repo.py` imports that killing stub for its stall
test rather than its own local `_make_stub_timeout`, which is a no-op
passthrough (fine for tests that just need the binary to exist, useless
for one asserting the cap fires). And its `_make_stub_ssh` rewrite used
`${{a/\/work/$DEST\/work}}` — the replacement half of `${{var/pat/repl}}`
is not a regex and needs no escaping, so the `\/` was a **literal
backslash**: the transfer landed in a directory named `<dest>\`, rsync
exited 0, and the test failed with "untracked.txt missing" and no error
anywhere. Only the pattern half escapes. (Do not "fix" the resulting
`SyntaxWarning` by making that f-string raw — the surrounding bash relies
on Python collapsing `\\` to `\`, and `rf"""` silently breaks the stub.)

The launcher's credential-resolution wiring within that same `RUNTIME=ec2`
branch — sourcing `aws-credentials.sh`, calling `resolve_aws_credentials`,
and `eval`ing its `export` lines before `require_aws` runs — is pinned in
`tests/test_ec2_e2e_provision.py` (call-index ordering: an SSO-configured
profile with explicit env-var credentials layered on top resolves via the
env vars and `require_aws`'s `sts get-caller-identity` is the first `aws`
CLI call observed, proving credential resolution ran first without
invoking the `aws` binary itself; explicit env credentials winning over a
fully-configured SSO profile; `LEERIE_AWS_PROFILE` selecting a named
profile's static credentials over `[default]`; an expired SSO cached
token aborting non-zero with `aws-credentials.sh`'s own
`aws sso login --profile <p>` hint and zero `aws ec2 ...`/`sts
get-caller-identity` calls) and in the dedicated
`tests/test_ec2_launcher_credentials.py`, which closes the one part of
the seam neither that file nor `tests/test_aws_credentials.py` (internal
precedence, standalone) nor `tests/test_ec2_lib_sh.py` (`require_aws`'s
own profile precedence, standalone) exercises: region. `require_aws`'s
`sts get-caller-identity` call never passes a `--region` flag — the
resolved region reaches it only through the `AWS_REGION` env var the
dispatch block `eval`s from `resolve_aws_credentials`'s `export` lines —
so this file's stub records the *effective `AWS_REGION` env value* seen
at call time (not argv) to pin: `LEERIE_AWS_REGION` (leerie's own knob,
CLAUDE.md-distinguished from the SDK's `AWS_REGION` credential-chain var)
winning over an ambient `AWS_REGION`; the ambient `AWS_REGION` reaching
`require_aws` unchanged when `LEERIE_AWS_REGION` is unset; and an
unresolvable region (no `AWS_REGION`, no `AWS_DEFAULT_REGION`, no profile
`region` key) aborting non-zero via `resolve_aws_credentials`'s own
die-with-hint before `require_aws`'s probe ever runs, with zero `sts
get-caller-identity` calls reaching the stub's log. It also adds a direct
argv assertion for the profile seam (`--profile <resolved>` present when
`LEERIE_AWS_PROFILE` is set, absent entirely when neither var is set) and
a harness-sanity check that it imports and exercises the same
verbatim-extracted dispatch block as `tests/test_ec2_e2e_provision.py`
rather than a hand-copied reproduction.
The EC2 resume path — `scripts/remote/ec2-resume-instance.sh`'s
`resume_instance()`, the EC2 counterpart to `resume-machine.sh` — is
tested in `tests/test_ec2_resume_instance.py` against the same
resource-tracking `aws` stub: starting a `stopped` instance drives it
to `running` via a single `start-instances` call; the readiness poll
does not return early when a seeded `status_ok: False` keeps
`describe-instance-status` reporting "initializing" (and does return
promptly once `status_ok: True`); `LEERIE_EC2_SSH_TARGET` is
re-resolved to the instance's current `PublicIpAddress` rather than
any address cached from provision time (EC2 assigns a new public IP on
every stop/start cycle absent an attached Elastic IP); a full
provision → stop → resume round trip leaves exactly one `running`
instance with no leaked volumes; resuming an already-`running`
instance is an idempotent no-op that issues no `start-instances` call;
resuming an unknown/terminated instance fails with the "no longer
recoverable" hint and issues no `start-instances` call; the run.json
sidecar's `paused_at`/`pause_reason` fields are cleared on success; and
the one-way-ratchet invariant (never `terminate-instances` or
`delete-volume`) holds both on the success path and the failure path
(instance never becomes ready), backed by a source-level grep guard on
the script file. `tests/ec2_stub.py` was extended to model a
per-instance `public_ip` that's reassigned (via an `_ip_gen` counter)
on every `start-instances` call, and an optional `status_ok` flag so
`describe-instance-status` can report "initializing" instead of "ok"
without an infinite/slow poll in tests.
The launcher's `--stop` verb EC2 dispatch — the counterpart to
`_auto_detect_fly_runtime` for EC2 runs, DESIGN §6 "Run identifier" —
is tested in `tests/test_ec2_launcher_stop.py` by invoking the real
`leerie` binary (not an extracted block, since `--stop` is an early
fast-path verb dispatched before container preflight) against the
same resource-tracking `aws` stub: an `ec2-instance.json` sidecar
auto-detects the EC2 runtime and `--stop <run-id>` drives the
stub-tracked instance to `stopped` (never `terminate-instances`) and
writes `paused_at`/`pause_reason`/`ec2_instance_id` onto `run.json`;
explicit `--runtime ec2` works without autodetection; the local/Fly
fallthrough error text is unchanged when no sidecar of any kind is
present; `--runtime bogus` is still rejected, now with the
`'local', 'fly', or 'ec2'` wording; a sidecar present but missing
`ec2_instance_id` fails closed with an actionable error rather than
silently no-op'ing; and a failing AWS credential probe aborts before
any `aws ec2 ...` call reaches the stub, leaving the instance
`running`.
The `RUNTIME=ec2` dispatch branch continuing past preflight into the
full create -> seed -> orchestrate -> teardown lifecycle (the old
`--runtime ec2 preflight passed, but instance provisioning is not yet
wired` abort is gone) is pinned in
`tests/test_ec2_launcher_dispatch_e2e.py`, which reuses (rather than
reimplements) `tests/test_ec2_e2e_provision.py`'s
`extract_ec2_dispatch_block`/`run_ec2_dispatch`/`stub_aws_env` harness
and `tests/ec2_stub.py`'s resource-tracking `aws` stub — mirroring
`tests/test_ec2_launcher_credentials.py`'s harness-sanity convention.
It pins: a full launch with valid credentials provisions exactly one
instance, reaches the stubbed `ec2_seed_repo`, and terminates cleanly
at `decide_ec2_teardown`'s clean-exit arm, leaving zero leaked
instances and zero leaked volumes; a grep guard that neither `"not yet
wired"` nor the more specific historical string `"instance
provisioning is not yet wired"` appears anywhere in `leerie`;
`require_aws`'s `sts get-caller-identity` still precedes any `ec2
run-instances` call by call index across the *full* lifecycle path
(not just the provision-only path `test_ec2_e2e_provision.py` already
covers); and a failing credential probe still aborts non-zero with the
`aws sso login --profile <p>` hint and zero tracked resources.

The generalized run-dir sidecar autodetection — `_auto_detect_run_runtime`
(checks `fly-machine.json` then `ec2-instance.json`, echoing the detected
runtime) and the `_auto_detect_fly_runtime` back-compat Fly-only wrapper
built on top of it — is tested in `tests/test_auto_detect_run_runtime.py`.
The first half extracts both functions verbatim from the launcher (mirroring
`tests/test_oom_wedge_prevention.py`'s `_reaper_fn_source` approach) and
exercises them against fixture run dirs: an ec2-instance.json-only run dir
detects as `ec2`; a fly-machine.json-only run dir still detects as `fly` (no
regression); neither sidecar present returns nonzero with nothing echoed; an
explicit runtime short-circuits detection even when a sidecar for a
different runtime is present; Fly wins when (never expected in practice)
both sidecars co-exist; and the Fly-only wrapper returns nonzero for an EC2
run. The second half invokes the real launcher end to end (mirroring
`tests/test_accept_blocked.py`'s local-path pattern) across `--stop`,
`--kill`, `--accept-blocked`, and `--finalize`: each accepts `ec2`
alongside `local`/`fly` in its `--runtime` enum validation (rejects other
bogus values with the updated three-way message). `--accept-blocked` and
`--finalize` still fail closed with an explicit "does not support EC2 runs
yet" message — rather than silently falling through to the Fly path or
defaulting to `local` — whether `ec2` was passed explicitly or
auto-detected via the sidecar; the Fly auto-detect regression path (no
sidecar override, `LEERIE_FLY_APP` unset) still reaches the pre-existing
Fly-specific error, proving detection promoted to `fly` and reached the
Fly branch. `--stop` and `--kill` both wire real EC2 actions (test-001 and
feat-006 respectively — see `tests/test_ec2_launcher_stop.py` and
`tests/test_ec2_launcher_kill.py` above/below for their end-to-end
coverage): passing `--runtime ec2` against a run dir with no
`ec2_instance_id` anywhere dies with "no ec2_instance_id found" instead of
the old fail-closed message, and auto-detecting the `ec2-instance.json`
sidecar proceeds past detection into AWS credential resolution (which
fails in this test's env for unrelated reasons — no `aws` binary/credentials
set up) rather than hitting the old fail-closed message. `--resume` is
covered separately: an `ec2-instance.json` sidecar fails closed with a
resume-specific message instead of promoting `RUNTIME=ec2` (which would
otherwise fall into the launcher's fresh-provision `RUNTIME=ec2` branch and
die with an unrelated "not yet wired" message), while a `fly-machine.json`
sidecar still promotes to `fly` as before. Neither `--accept-blocked` nor
`--finalize` wire an EC2 verb *action* yet — that is feat-007/feat-008 (and
a later `--resume` subtask); this subtask's scope for those two remains the
detection helper and the `--runtime` enum validation it feeds.

`--kill`'s EC2 action — resolving `ec2_instance_id` from the run dir,
resolving AWS credentials, re-resolving `LEERIE_EC2_SSH_TARGET`, and
syncing state via `_try_fetch_state_for_ec2_teardown` BEFORE calling
`terminate_instance()` (the one-way-ratchet invariant
`ec2-provision.sh:262-272` documents) — is tested end to end in
`tests/test_ec2_launcher_kill.py` against the real `leerie` launcher
binary. The `aws` stub combines two behaviors behind one binary since
`--kill`'s EC2 path exercises both surfaces in a single run: `ssm
start-session` (the transport `ec2_remote_exec`/`fetch_state_ec2` use)
decodes and execs the wrapped command locally against a real git repo
standing in for the instance's `/work` (reusing
`tests/test_ec2_fetch_branch.py`'s `_make_stub_ssh`/
`_init_instance_repo_with_run`/`_setup_instance` helpers directly rather
than reimplementing them, so `fetch_state_ec2` runs for real instead of
being hand-waved), while `sts`/`ec2 <action>` route to
`tests/ec2_stub.py`'s resource-tracking state machine (imported and
reused as the lifecycle backend) so credential/instance-lifecycle calls
are tracked too — both halves append to the same `aws.log`/`state.json`
so `tests/ec2_stub.py`'s `read_log`/`read_state`/`leaked_resources` work
unmodified. Pinned: the fetch step's `ssm start-session` call precedes
`terminate-instances` by call index (falsified live — reordering the
launcher's fetch/terminate calls makes this test fail, since
`terminate_instance()` clears `LEERIE_EC2_INSTANCE_ID` and the
now-preceding fetch step then errors on a missing instance id); a
successful kill leaves zero non-terminated instances and zero leaked
volumes in the stub's tracked state; a failed fetch (no completed run
committed on the "instance" side, so `fetch_state_ec2`'s discovery step
fails closed) leaves the instance `running` rather than escalating to
termination; a hard-failing `flyctl` stub (records invocation, exits
nonzero) is on PATH throughout and its log stays empty on every path,
pinning that an EC2 run-id is never handed to `flyctl`; `run.json` gets
`killed_at` + `ec2_instance_id` on success, bootstrapped from
`ec2-instance.json` via the widened `_ensure_run_json` when `run.json`
doesn't exist yet; a sidecar with no resolvable `ec2_instance_id` dies
with "no ec2_instance_id found" without ever calling `terminate-instances`
or `flyctl`; and the confirmation prompt (bypassed by `--force`, same
convention as the Fly/local `--kill` paths) rejects a wrong confirmation
and proceeds on the correct one.
The phase 2¾ plan-overlap judge (DESIGN §5 *Cross-domain surface overlap*)
is tested in `tests/test_phase_overlap_judge.py`. Beyond the schema and
merge-feasibility backstop, it pins the **multi-drop cluster** contract
(DESIGN §5 *Multi-drop*): one sid dropped by 2+ collisions is coherent
judge output — the prompt explicitly instructs it — and must not `die()`.
The load-bearing pin is `test_apply_multi_drop_preserves_both_survivors`:
replaying such a cluster pairwise through the apply loop's transitive
`survivor_of` rewrite silently deletes a **live** subtask the judge never
named (pair 2's `_resolve` maps the already-dropped endpoint onto pair 1's
survivor), fabricating a supersedure claim between two subtasks never
compared; `_apply_overlap_drop` discards title/intent/success_criteria by
design, so the loss is unrecoverable and compounds —
`test_apply_multi_drop_three_way` pins that the pre-fix loop destroys
three of four subtasks. Chasing `survivor_of` is safe for a `merge`
(intent carries forward) and never safe for a `drop`. Also pinned: the
three-tier cycle ladder (`multi_drop_fanout` →
`multi_drop_degraded_single` → `skipped_would_cycle`), since the fan-out
*adds* graph edges and can close a cycle no individual pair would;
sorted-survivor determinism at both the cluster-collection and
`_apply_multidrop` layers (`schedule()` is documented deterministic, so
the plan must not depend on judge emission order); that a *legitimate*
transitive chain still applies both drops (the guard must not
over-suppress); and — previously **entirely unpinned**, the
highest-severity silent-disable in the phase —
`test_phase_overlap_judge_dies_on_unresolvable`, without which two
implementers ship incompatible APIs against one artifact and it surfaces
at integration with no trace back to phase 2¾. Two mutants in this
region are **equivalent** and deliberately left unkilled, documented in
the tests that would otherwise appear to cover them: `_resolve`'s `while`
vs `if` (path compression flattens the map before the second hop) and
`_apply_multidrop`'s `s != dropped_sid` filter (the removal loop runs
before anything reads survivors). Do not "strengthen" those tests
chasing a mutant that cannot be killed. `PHANTOM_ARTIFACT` resolves a
collision's artifact against the plan's `files_likely_touched` as well as
the working tree — two planners that both *create* the same file is the
judge's canonical collision, so a tree-only existence test rejects the
primary case — via a shared `_normalize_artifact_path` that
`NO_FILE_OVERLAP` also uses (both sides are planner-authored strings, so
`./x` and `x` must not read as disjoint; no case folding, since container
checkouts are case-sensitive).

No coverage
target is set — the suite was introduced from scratch and a number
now would be arbitrary.
The two read-mostly verbs that still assumed a two-runtime world —
`--accept-blocked` (validated `--runtime` against only `fly`/`local` and
defaulted anything non-fly to `local`, silently mislabeling an EC2 run)
and `--list` (keyed its runtime-aware view on `fly-machine.json`/
`LEERIE_FLY_APP`, so an EC2 run rendered empty columns) — are pinned in
`tests/test_ec2_launcher_readonly_verbs.py`. `--accept-blocked` now
auto-detects EC2 the same way `--stop` already does
(`_auto_detect_run_runtime`), accepts an explicit `--runtime ec2` (with
a control that a genuinely bogus value is still rejected), and —
mirroring the Fly path's wake-mutate-pause dance — wakes a stopped
instance, mutates state.json over SSM (`ec2_remote_exec`), mirrors the
mutation onto the host copy if one exists, and re-pauses the instance
only if this verb woke it (plus an already-running control that proves
no pause fires when the instance was already up), and fails closed on a
missing `ec2_instance_id`. The `--accept-blocked` tests invoke the real
`leerie` launcher binary against a stubbed `aws` that composes
`tests/ec2_stub.py`'s stateful EC2 instance tracking with an `ssm
start-session` handler that decodes `ec2_remote_exec`'s base64-wrapped
command and executes it with the invoking process's stdin drained
through — the same mechanism the launcher's EC2 branch relies on to
pipe the multi-line state-mutation Python program to the remote
`python3 -`. `_collect_run_rows`/`list_runs` in `orchestrator/leerie.py`
now track an `is_ec2` axis (`ec2_instance_id` in `run.json` or
`ec2-instance.json` present) alongside the existing `is_fly`, so
`--list --runtime ec2` filters correctly, `--list --runtime local`
excludes both Fly and EC2 runs, a plain `--list` renders an EC2 run's
status column without requiring `LEERIE_FLY_APP`, and an EC2 run is
still detected via the `ec2-instance.json` sidecar alone when
`run.json` doesn't exist yet. These `--list` tests exercise
`list_runs()` directly (no launcher subprocess, no AWS stub), mirroring
`tests/test_list_runs.py`'s pattern.

`--resume` routing a paused EC2 run through `resume_instance()` — the
launcher-level seam distinct from `resume_instance()`'s own standalone
coverage in `tests/test_ec2_resume_instance.py` — is pinned in
`tests/test_ec2_launcher_resume.py`, reusing
`tests/test_ec2_e2e_provision.py`'s `extract_ec2_dispatch_block`/
`run_ec2_dispatch`/`stub_aws_env` harness and `tests/ec2_stub.py`'s
resource-tracking `aws` stub (mirroring
`tests/test_ec2_launcher_dispatch_e2e.py`'s import convention), since
`--resume` for EC2 lives inside the deep `RUNTIME=ec2` elif dispatch
block rather than the early fast-path verb dispatch `--stop` uses. It
pins: a stopped instance named by an `ec2-instance.json` sidecar issues
exactly one `start-instances` call and reaches `running`, with no
duplicate `run-instances` provisioning a second instance; the
load-bearing IP-reassignment case — `LEERIE_EC2_SSH_TARGET` is
re-resolved to the instance's NEW `PublicIpAddress` after resume, not
the stale provision-time address, since EC2 hands out a new public IP
on every stop/start cycle absent an attached Elastic IP; `run.json`'s
`paused_at`/`pause_reason` are cleared and `ec2_instance_id` is
preserved; an already-`running` instance is an idempotent no-op with
zero `start-instances` calls; and neither `terminate-instances` nor
`delete-volume` is ever called, on both the success path and the
never-ready (`status_ok=False` timeout) failure path.

The worker invocation path is unit-tested only at the `claude_p` layer, via
a stubbed `_invoke` (`tests/test_no_result_event_retry.py`) — enough to pin
the retry/envelope contract. `_invoke` itself (process spawn, stream
parsing, cgroup enrollment) still needs a stub or live `claude` binary and
lives in a separate end-to-end tier.
The inverted credential-resolution precedence in the launcher's
`_extract_claude_credentials_json` (DESIGN §6 *Credential strategy* —
`$CLAUDE_CODE_OAUTH_TOKEN`, the long-lived `claude setup-token` token,
now resolves ahead of Keychain and the on-disk credentials file, since a
container cannot refresh a copied subscription token) is tested in
`tests/test_credential_precedence.py` by reusing `_invoke_helper` from
`tests/test_chain_credential_transport.py` (which already extracts
`_extract_claude_credentials_json` out of the launcher via `awk` and
sources it in a sub-bash with a controlled `HOME`/`PATH`): the env var
wins over both a Keychain entry and an on-disk file (Darwin-gated, since
the Keychain branch is `uname -s = Darwin`-only), wins over the file
alone on non-Darwin, the emitted JSON shape matches what `seed-auth.sh`
independently constructs for a bare `CLAUDE_CODE_OAUTH_TOKEN` (the printf
format string is extracted from `seed-auth.sh` at test time via regex
rather than hand-copied, so the two sites can't silently diverge — same
discipline as `test_no_result_event_retry.py`), Keychain still wins over
the file when the env var is unset (the pre-inversion fallback order is
unchanged), and no credential anywhere yields a clean rc 1. The paired
best-effort expiry preflight, `_check_claude_credential_ttl` (staged
before writing a resolved *subscription* credential into the container;
a no-op for the exempt long-lived-token path, which carries no
`expiresAt`), is tested in `tests/test_credential_ttl_preflight.py` by
extracting the function plus its `_CLAUDE_TTL_WARN_THRESHOLD_SEC`
constant out of the launcher via `awk` into a standalone sourceable
file: an already-expired `expiresAt` refuses (rc != 0) and names `claude
/login`; inside the 90-minute threshold warns (rc 0) naming both the
exact ISO-8601 expiry and `claude setup-token` as the durable fix,
including a regression case replaying the b57027d3 incident's
expiry shape; a healthy TTL is silent; absent `expiresAt`, malformed
JSON, a non-numeric `expiresAt`, a missing `claudeAiOauth` key, and a
negative `expiresAt` (pre-1970 garbage, not a genuine expiry) all
proceed silently rather than hardening the best-effort check into a
hard gate on missing/bogus data; and two constant pins confirm the
threshold is exactly 90 minutes and that the launcher never hard-codes
an 8-hour TTL assumption anywhere (the community-reported 2–15h range
is why `expiresAt` must be read, never assumed).
The terminal auth-failure classifier and its full routing path — the
`b57027d3…` incident this run's credential-strategy work responds to,
where a container's expired OAuth session surfaced as "worker failed
schema-valid output twice" instead of a resumable pause — is tested in
`tests/test_terminal_auth_failure.py`: `_is_terminal_auth_failure` is
table-driven over the measured corpus (4 real terminal-auth strings
positive, including mixed-case variants, proving the classifier
lowercases before comparing; the 8-string "API Error: …" corpus plus an
empty string negative; the verbatim incident envelope classifies true);
gating guards mirror `_is_auth_or_quota_failure`'s discipline (`False`
when `is_error` is `False` or absent entirely; `False` for a
`_leerie_synthetic` envelope whose interpolated stderr merely mentions
these markers; `False` for a *successful* envelope that legitimately
discusses OAuth in its own correct output; `False` for a bare "oauth"
substring that isn't the fuller marker phrase — guarding the 2919-count
false-positive risk noted in the classifier's own docstring; `False` for
a non-string `result`); 401/429/529 numeric-status envelopes classify
false here while still classifying true under
`_is_auth_or_quota_failure`, proving the two classifiers partition
cleanly. The `claude_p()` routing tests replay the verbatim incident
envelope through a stubbed `_invoke` (mirroring
`test_no_result_event_retry.py`'s harness): the call completes in under 5
seconds rather than entering the ~300s auth/quota tenacity loop, raises
`TerminalAuthFailure` (not `WorkerError`, and not the generic "worker
failed schema-valid output twice" message), while a control case with
401/429/529 envelopes still enters and exhausts the real backoff loop
(asserted via `invoke_calls` recording more than one `_invoke` call)
before raising `WorkerError` unchanged. Three source-coupling guards
close the loop: an AST-based check (not a bare substring match, which
would be satisfied by the handler's own explanatory comments even if the
real assignment regressed) that `main()`'s `except TerminalAuthFailure`
arm sets `exit_code = EXIT_LOCKED` and never `1`, and mentions
`--resume`; `EXIT_LOCKED == 75`; `_is_terminal_auth_failure` is checked
before `_is_auth_or_quota_failure` inside `claude_p`'s source; and
`TerminalAuthFailure` subclasses `BaseException` (so it propagates
through `asyncio.gather` and broad `except Exception` handlers, same as
`RateLimitedExit`) but not `WorkerError`.
The `claude_p`/`main()` routing seam for terminal auth failures (DESIGN §6
credential strategy) — distinct from the classifier-only coverage of
`_is_terminal_auth_failure` itself — is tested in
`tests/test_terminal_auth_routing.py` using the same stubbed-`_invoke`
harness as `test_no_result_event_retry.py`: the terminal-auth envelope
(`Failed to authenticate: OAuth session expired and could not be
refreshed`) causes exactly one `_invoke` call and completes in well under
a second, proving the 300s auth/quota tenacity budget is never entered;
the raised exception is `TerminalAuthFailure`, not `WorkerError`, and its
message never blames schema validation; `main()`'s `except
TerminalAuthFailure` handler is checked by source-coupling
(`inspect.getsource(leerie.main)`, mirroring `test_signal_cleanup.py`'s
`_main_body` approach) to set `exit_code = EXIT_LOCKED`, call
`_cleanup_on_abnormal_exit(st, full_purge=False)`, set `abnormal = False`,
and surface a `--resume` hint; a control case pins that 401/429/529
envelopes whose auth/quota backoff budget exhausts still raise
`WorkerError`, not `TerminalAuthFailure` — the doc-conformant behavior per
`docs/IMPLEMENTATION.md` §3 "Auth/quota backoff" after commit `2652319`
reverted an over-application of the terminal-auth reroute to that
transient case; and a source-coupling check that `_is_terminal_auth_failure`
is consulted before `_is_auth_or_quota_failure` inside `claude_p`.

## Task completion checklist

Before marking a change complete:

- [ ] Update `IMPLEMENTATION.md` if the change affected code surface
      described there.
- [ ] Update `DESIGN.md` only if the architecture itself changed.
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
