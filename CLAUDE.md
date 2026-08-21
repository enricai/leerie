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

**Scoped exception — finalize-time rebase.** The `rebaser` worker
(finalize, `scripts/host-finalize.sh` → `orchestrator/leerie.py`'s
`run_rebaser()`) is a deliberate, narrow exception to this principle: it
is trusted to perform the entire rebase-onto-base workflow itself
(branch switching, conflict detection, conflict resolution, and the
abort-if-irreconcilable decision) rather than having each mechanical
step coded and the LLM confined to conflict-resolution content only —
see DESIGN.md §6 *Finalization* "Rebase-onto-base before push" for the
full rationale and the empirical validation behind it. This exception
is scoped to that one worker and its disposable worktree; it does not
license moving other mechanical checks into prompts elsewhere. Code
still confines the worker to a disposable `git worktree add` copy (never
the user's real checkout) and mechanically re-checks the claimed outcome
(`check_rebaser_worktree_state` — clean tree vs. actually aborted)
before trusting it, mirroring the existing "don't trust an integrator's
self-report" discipline this principle already establishes for
`integrator`.

## Prompts must not name another project's code

`prompts/*.md` and `commands/*.md` are **product surface**: every one is sent
verbatim to a worker running against *whatever repository leerie was pointed
at*. An identifier from some other codebase in there teaches the model that
another project's domain model is the canonical example, on a repo where that
symbol does not exist.

Examples in a prompt must use **this repository's own symbols**
(`_gather_provision_fixtures`, `_run_checked_loop`) or an **obviously synthetic
placeholder**. Never a name taken from another project — including one you
found in a task file, a run log, or an existing test fixture. A fixture is
contained; a prompt ships.

Enforced by `tests/test_prompts_have_no_foreign_identifiers.py`, which applies
two independent rules: every backticked identifier in a shipped prompt must
exist elsewhere in this repo or be listed in its `GENERIC_PLACEHOLDERS`
allowlist, and a seeded denylist of known-foreign names is rejected on sight
(that second rule exists because a foreign name that also leaked into a test
fixture passes the first).

## No subagent spawning

Workers are headless `claude -p` subprocess invocations, not in-session
subagents. The orchestrator is an ordinary Python program. (Constraint
1, DESIGN.md §2.) The Claude Code Agent tool is not available to the
orchestrator and not used anywhere in this repo.

`DISALLOWED_TOOLS` enforces this mechanically, and it must name **`Task`**,
not only `Agent`: `Agent` is the retired spelling and current CLI builds ship
`Task`. Until 2026-08-18 the deny list carried `Agent` and the `Task*` family
(`TaskCreate`/`TaskGet`/`TaskList`/`TaskUpdate`, retired from current builds,
plus the live `TaskOutput`/`TaskStop`) but not the bare `Task`, so this invariant
was enforced against a name the live CLI no longer emits — the shape to watch
for whenever the CLI renames a tool.

## Mandatory requirements

- **Worker outputs are JSON-schema-validated.** New worker types must
  define a schema in the `SCHEMAS` dict in `orchestrator/leerie.py` and
  pass it via `--json-schema` in `claude_p()`.
- **All natural-language interpretation is done by an LLM worker
  returning schema-validated structured JSON — never by regex or
  hand-parsing in Python.** This is the input-side companion to the
  bullet above: Python operates only on already-structured data (JSON
  fields, typed values) — set/string/arithmetic comparison, never
  inferring meaning from prose. Regex is permitted only on *mechanical*
  strings (semver, shell commands, fixed CLI output, file paths) —
  never on task text, planner/worker prose, README/markdown content, or
  an LLM's response. If a check needs a fact from natural language, the
  owning worker must surface it as a JSON field. (`tests/test_capture_deps.py`'s
  `TestRegexPathAbsent` — dep-capture's migration off a regex path onto
  LLM-structured output, see below — is prior art for this same
  principle; see DESIGN.md §"Language-to-JSON: natural-language
  interpretation is never regex" for the architectural statement.)
- **Every worker — judgment and acting/workhorse alike — defaults to
  `sonnet`.** This was previously split: judgment workers (classify,
  plan, reconcile, judge, verify, gate) defaulted to opus, because
  measured evidence (DESIGN.md §"Opus-judgment, sonnet-workhorse
  (historical)") showed the same judge prompt producing opposite
  verdicts on the two tiers for the same input at the time. That gap
  has since closed for Sonnet 5 — externally verified to match Opus
  4.8 (the prior working judgment baseline) on the same class of
  decisions — so the split no longer applies: `MODEL_DEFAULT = "sonnet"`
  and no worker needs a judgment-tier exception to reach it. A new
  worker MUST be absent from `MODEL_DEFAULT_PER_WORKER` (so it falls
  through to `MODEL_DEFAULT`) unless it has its own documented reason to
  diverge. Judgment workers still carry `EFFORT_DEFAULT_PER_WORKER =
  "medium"` — that dial is about reproducibility/determinism, not model
  tier, and is unaffected by this change.
  **Separately, `implementer` and `conformer` — the two workers that
  actually write code — are pinned to `EFFORT_DEFAULT_PER_WORKER =
  "low"`,** a deliberate cost/latency trade-off distinct from the
  judgment workers' `medium`: these previously inherited Claude's own
  default reasoning depth (unset, i.e. high) so their effort stayed
  bounded by their own evidence gates (DESIGN §8); that tradeoff is now
  overridden in favor of a fixed low-effort ceiling, with the downstream
  conformer/confidence-gate loops absorbing the quality difference.
  `satisfied_probe` remains an explicit `MODEL_DEFAULT_PER_WORKER` entry
  (still sonnet, matching the global default, but kept for its own
  documented reason: it runs once per subtask and throughput dominates,
  with correctness resting on its base-tree-only tool scope rather than
  model tier — see the comment at its `MODEL_DEFAULT_PER_WORKER` entry).
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
- **Evaluate every ownership/permission change against all three runtimes
  separately** — local rootless containerd, local rootful (Colima/macOS),
  and Fly/EC2. A change can be correct on one and break another, and CI
  (`ubuntu-latest`, rootful) structurally cannot catch a rootless-only
  regression. The specific trap, documented at `Dockerfile:235-241`:
  rootless drops privilege via `unshare --user --map-user=$(id -u leerie)`,
  a single-entry map (outer UID 0 → inner leerie) that leaves outer leerie
  *unmapped*. So an image-layer dir **chowned to leerie is unwritable**
  (appears as nobody/65534) while a **root-owned dir is writable** — the
  inverse of normal Docker intuition. Rootful `runuser -u leerie` is a
  real uid switch with no remap, so it needs the opposite: literal leerie
  ownership, applied at runtime in `container-entry.sh`'s rootful guard.
  See `tests/test_tmp_cache_writable.py` and
  `tests/test_home_leerie_ownership.py` for the pinned form.

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
docs/DESIGN.md               Architecture and reasoning
docs/IMPLEMENTATION.md       Current code surface
docs/TESTING.md               Per-feature test coverage inventory; CLAUDE.md's
                              "## Testing" section covers only the operational
                              rules for running the suite, not the inventory
chain/                      Laptop-side chain helpers (see DESIGN.md §19). A chain is
                            N parallel `leerie --runtime fly` invocations per wave,
                            sequenced by the launcher's `chain` arm. `chain/git_ops.py`
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
./leerie resume

# Accept a blocked subtask so resume skips it (e.g., E2E tests
# that need external deps the container can't provide). --force settles a
# subtask abandoned mid-flight (in_progress, no `blocked` registry entry —
# what an ENOSPC or SIGKILL leaves behind):
./leerie accept-blocked <run-id> <subtask-id> [--force]

# Accept an integration_judge behavioral finding so resume advances past it.
# The verdict is recorded in state.json's `integration_gate` BEFORE
# integrate_wave dies, so acceptance survives the next resume; without this
# the merge is already committed to staging and resume re-reaches the same
# verdict forever. A false positive from that judge otherwise kills a run
# after full planning + implementation spend:
./leerie accept-integration <run-id> <subtask-id>

# Reclaim disk. Nothing reaps run state automatically, while preflight refuses
# to start a run on low headroom (measured: 1.5 GB, 71 run dirs, 23,158 cache
# entries, 64 stale leerie/subtasks/* branches after three weeks on one repo).
# Dry-run by DEFAULT — this deletes directories that may hold the only record
# of a paid-for run. Removes terminal run dirs (finished_at/killed_at only —
# a paused or in-flight run survives regardless of age), repo-map cache
# entries, and orphaned leerie/subtasks/<run-id>/* branches. Host-only.
# Branch reaping needs POSITIVE EVIDENCE, never absence: `-D` for a run dir this
# prune removed or a branch already merged into its own run branch, `git branch
# -d` otherwise (which git refuses on unmerged work). Kept branches are
# reported. Worktree registrations are dropped before the run dir goes, or git
# refuses the delete and the failure reads as "unmerged".
./leerie prune                        # show what would go
./leerie prune --apply
./leerie prune --older-than 30 --apply   # default cutoff is 14 days

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

# Route model calls through Amazon Bedrock via a static bearer token — the
# Bedrock analogue of CLAUDE_CODE_OAUTH_TOKEN. Needs no `aws` CLI, no SSO
# session, and no ~/.aws/ staging (unlike the settings.json-driven
# CLAUDE_CODE_USE_BEDROCK + `aws sso login` mode, whose SSO token the
# container cannot refresh). AWS_REGION is optional (the CLI defaults to
# us-east-1); CLAUDE_CODE_USE_BEDROCK defaults to 1 once the bearer token is
# set, but can still be overridden explicitly. If both this bearer token and
# a settings.json CLAUDE_CODE_USE_BEDROCK are present, the bearer token wins.
# Note: if also using --runtime ec2, its AWS_REGION-consuming preflight
# (require_aws) can pick up this same var — set LEERIE_AWS_REGION explicitly
# if you want a different EC2-provisioning region than the Bedrock region.
export AWS_BEARER_TOKEN_BEDROCK=<token>
export AWS_REGION=us-east-1              # optional, forwarded when set
./leerie "task"

# Reduce mid-run quota exhaustion by giving leerie multiple subscription
# OAuth tokens to rotate across (DESIGN §6 *Multi-token rotation*).
# CLAUDE_CODE_OAUTH_TOKENS (comma-separated) supersedes the singular
# CLAUDE_CODE_OAUTH_TOKEN when set. At start, each token is probed for
# remaining 5h/7d runway and the one with the most is selected — not
# round-robin. If the active token gets rate-limited mid-run, leerie
# rotates to another token with runway and continues in the same
# container (no restart); if all tokens are limited, it waits for
# whichever resets soonest. Probing is best-effort — an undocumented
# endpoint failing never blocks the run, it just falls back to
# react-on-429 with the first token.
export CLAUDE_CODE_OAUTH_TOKENS=sk-ant-oat01-token-a,sk-ant-oat01-token-b
./leerie "task"

# Pin which model version the `--model <tier>` alias (sonnet/opus/haiku)
# resolves to on Bedrock. leerie always invokes claude -p with an explicit
# --model <tier> flag, never a raw model ID (true of every worker since
# always, and of the preflight smoke test only since 2026-08-18 — it passed
# no --model at all and inherited whatever the CLI defaulted to, measured
# resolving to opus on a run whose every worker was sonnet) — and on Bedrock the Claude
# CLI's own alias table can lag the Anthropic-API one by a model
# generation or more (e.g. `sonnet` resolving to Sonnet 4.5 instead of
# Sonnet 5). These are the Claude CLI's own documented env vars for
# repointing an alias; set the ones you need alongside either Bedrock auth
# mode above (bearer-token or settings.json SSO/profile) and leerie
# forwards them into the container:
export ANTHROPIC_DEFAULT_SONNET_MODEL=us.anthropic.claude-sonnet-5
export ANTHROPIC_DEFAULT_OPUS_MODEL=us.anthropic.claude-opus-5
export ANTHROPIC_DEFAULT_HAIKU_MODEL=us.anthropic.claude-haiku-4-5
./leerie "task"

# Choose the model. Without overrides, every worker — judgment (classifier,
# planner, reconciler, plan_overlap_judge, provision, integrator) and acting
# (implementer, conformer) alike — defaults to sonnet. Per-worker overrides
# exist via --model-<worker> / LEERIE_MODEL_<WORKER>. See
# docs/IMPLEMENTATION.md §2 "Model selection" for the full table.
export LEERIE_MODEL=sonnet               # or: opus, haiku
./leerie "task" --model opus
./leerie "task" --model-implementer opus --model-classifier haiku

# Pin reasoning depth via `claude -p --effort`. Without overrides,
# judgment workers (classifier, planner, reconciler, plan_overlap_judge,
# provision, integrator) default to `medium`; acting workers
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

# Raise the per-run worker-invocation budget (default 2000 — a runaway
# backstop, not a capacity limit). Same precedence as confidence-rounds:
# CLI > env > leerie.toml.
export LEERIE_MAX_WORKERS=80
./leerie "task" --max-workers 80

# Override concurrent workers per wave (default 5). Same precedence:
# CLI > env > leerie.toml.
export LEERIE_MAX_PARALLEL=6
./leerie "task" --max-parallel 6

# Raise the per-worker cgroup PID cap (default 2048). Bounds fork/clone in
# each worker subtree; raise it for repos whose conformance step runs a
# subprocess-heavy full test suite (which can burst past a low cap in
# seconds and wedge every subsequent Bash call with EAGAIN). Positive
# integer; same precedence: CLI > env > leerie.toml.
export LEERIE_WORKER_PIDS_MAX=4096
./leerie "task" --worker-pids-max 4096

# Override the per-worker wall-clock ceiling (default 5400s / 90 min).
# Setting this BYPASSES the measured per-worker table
# (TIMEOUT_DEFAULT_PER_WORKER), which otherwise lowers the ceiling for fast
# worker types using a distribution measured on one host — so raise it when
# a worker is being killed at a ceiling derived on a faster machine.
# Detection is on whether you set it at all, so passing the default
# explicitly still bypasses the table.
# Positive integer seconds; same precedence: CLI > env > leerie.toml.
export LEERIE_WORKER_TIMEOUT=9000
./leerie "task" --worker-timeout 9000

# Allow a second run on task text a live run is already working. leerie
# fingerprints the task (`task_sha256` in run.json) and refuses to start when
# another live run carries the same one — live meaning started and not
# finished, killed or paused — measured, one brief
# ran twice for $72.21 and produced two incompatible branches with 14 files in
# collision. A finished run sharing the text is an ordinary re-run and never
# blocks. With the hatch set the duplicate still gets announced:
export LEERIE_ALLOW_DUPLICATE_TASK=1
./leerie "task"

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
# structural map*): suppresses _build_repo_map() and the ranked
# subgraph injection into planner/splitter context. The planner
# degrades gracefully to the prior grep/glob-only path. Use on repos
# where tree-sitter cannot parse the primary language, or to opt out
# of structural context. Also LEERIE_SKIP_REPO_MAP=1 or
# `skip_repo_map = true` in leerie.toml. Default: off.
./leerie "task" --skip-repo-map

# Skip the instruction-adherence gate: the deterministic prescribed-
# command-coverage floor and the adherence_judge worker in the
# planner check loop. A plan that diverges from an explicitly
# prescribed procedure is not caught before phase_execute spends. Also
# LEERIE_SKIP_ADHERENCE_CHECK=1 or `skip_adherence_check = true` in
# leerie.toml. Default: off.
./leerie "task" --skip-adherence-check

# Skip the phase 2⅞½ task-coverage review. The gate is ADVISORY since
# 2026-08-04 (its deterministic floor passed 0 of 102 items ever and was
# deleted; its judge's findings do not reproduce across re-samples), so
# this only suppresses the review and its worker call. Also
# LEERIE_SKIP_COVERAGE_CHECK=1 or `skip_coverage_check = true` in
# leerie.toml. Default: off.
./leerie "task" --skip-coverage-check

# Demote the conformer's gating solution_defects completeness axis (DESIGN
# §9 *The one gating axis: solution completeness*) to advisory: found defects
# surface as warnings but never re-drive the implementer, block a subtask, or
# die() the final-tree pass. Use when a false-positive completeness defect is
# blocking finalize on every resume. Also LEERIE_SKIP_COMPLETENESS_CHECK=1
# or `skip_completeness_check = true` in leerie.toml. Default: off.
./leerie "task" --skip-completeness-check

# Skip the phase-5 integration_judge behavioral-defect gate entirely — no
# worker spawn for any subtask in this run. Independent of the
# accept-integration verb above, which settles a finding the judge has
# already produced; this stops it producing one. Also
# LEERIE_SKIP_INTEGRATION_CHECK=1 or `skip_integration_check = true` in
# leerie.toml. Default: off.
./leerie "task" --skip-integration-check

# Make the conformer phase blocking instead of advisory.
# Residuals cause subtasks to return 'blocked' (fix + resume).
# Also LEERIE_STRICT_CONFORMER=1 or `strict_conformer = true` in
# leerie.toml:
./leerie "task" --strict-conformer

# How much of the repo's suite each per-subtask conformance round measures
# (DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*). The
# orchestrator — not the conformer — runs build/lint/test, before and after
# every round.
#   scoped  (default) a diff-scoped proxy where one resolves, canonical
#           otherwise. Declare `test_scoped` / `build_scoped` in
#           .leerie/config.toml, else two narrow inferences apply
#           (vitest `related`, jest `--findRelatedTests`, `tsc --noEmit`).
#           An axis with no proxy falls back to the canonical command; it is
#           never silently skipped -- and leerie now WARNS once per run when
#           that fallback means `scoped` is behaving as `full`.
#           A template uses `{files}` for runners that map source->tests
#           themselves (vitest/jest walk their own module graph), or
#           `{test_files}` for runners that do not (pytest collects under the
#           paths given, so a docs/source path is an ERROR that poisons the
#           whole invocation -- verified: `pytest docs/X.md tests/test_y.py`
#           exits 4). `{test_files}` substitutes only the test-shaped members
#           and renders nothing when the diff has none, so the axis falls back
#           to canonical rather than measuring a narrower thing silently.
#           `test_file_globs` in .leerie/config.toml (space-separated fnmatch
#           patterns) REPLACES the built-in test-path shapes when set.
#   full    always the canonical command. Note this restores concurrent
#           full-suite runs under --max-parallel, which is what the scoped
#           default exists to avoid.
#   off     measure nothing per subtask.
# The canonical command always runs at the base-health baseline and on the
# final integrated tree regardless of this setting — those two are where a
# whole-suite answer is meaningful. Also LEERIE_SUBTASK_TESTS or
# `subtask_tests` in leerie.toml:
./leerie "task" --subtask-tests full

# Disable finalize-time dependency capture (DESIGN §6½). Default: enabled.
# Also `capture_deps = false` in .leerie/config.toml (no leerie.toml tier):
export LEERIE_CAPTURE_DEPS=0
./leerie "task"

# Disable the language-dep COPY+RUN layer in the auto-generated Dockerfile
# (bake apt packages only). Default: enabled. Also `bake_language_deps =
# false` in leerie.toml or .leerie/config.toml:
export LEERIE_BAKE_LANGUAGE_DEPS=0
./leerie "task"

# Give judgment workers the repo's build/lint/test verbs (use on repos where
# the planner needs pnpm/tsc/vitest visibility — also
# LEERIE_DANGEROUSLY_SKIP_PERMISSIONS=1 or
# `dangerously_skip_permissions = true` in leerie.toml).
# It does NOT hand them the CLI flag of the same name: that is unreachable for
# judgment workers, because it removes the CLI's working-directory boundary
# along with the prompts. Measured (claude 2.1.237, filesystem-verified): a
# worker holding only INSPECT_TOOLS and carrying the flag used Write — absent
# from that allowlist — to overwrite a tracked file outside its cwd and commit
# on the user's branch, and did so even from a detached worktree. The flag now
# WIDENS the allowlist instead (`_widen_inspect_tools`). Still a real trust
# decision: a build verb runs arbitrary code and can write outside the
# worktree, which is what `_assert_repo_unchanged` catches. DESIGN §12
# *Judgment-worker isolation*:
./leerie "task" --dangerously-skip-permissions

# Run workers WITHOUT cgroup containment when the host can't enforce it
# (rootless containerd, or no usable cgroup hierarchy). DANGEROUS: workers
# then run with no memory/PID limits, so a runaway subtree can exhaust the
# VM thread/PID table (the failure the fail-closed gate prevents — DESIGN
# §6 Memory containment). Also LEERIE_DANGEROUSLY_ALLOW_UNCAPPED=1 or
# `dangerously_allow_uncapped = true` in leerie.toml:
./leerie "task" --dangerously-allow-uncapped

# Force constrained decoding on worker structured output. Off by default.
# `claude -p --json-schema` validates the model's output AFTER generation and
# re-prompts on a miss; it does not constrain sampling. This flag starts a
# per-run loopback proxy, points workers at it via ANTHROPIC_BASE_URL, and
# rewrites the CLI's injected StructuredOutput tool to carry `strict: true`
# (hardening the schema so the grammar compiles). DANGEROUS: it edits requests
# leerie does not own — the CLI's injected tool is a private interface with no
# compatibility guarantee, and schema keywords the grammar cannot express are
# stripped (see docs/IMPLEMENTATION.md for the full disclosure). Fail-open:
# anything unexpected forwards the request untouched. Collides with a
# pre-set ANTHROPIC_BASE_URL, and with Bedrock (AWS_BEARER_TOKEN_BEDROCK /
# CLAUDE_CODE_USE_BEDROCK, which route to a different endpoint) — die()s in
# both cases rather than silently running without the guarantee.
# Also LEERIE_DANGEROUSLY_FORCE_STRICT_OUTPUT=1 or
# `dangerously_force_strict_output = true` in leerie.toml:
./leerie "task" --dangerously-force-strict-output

# Skip the run-start pre-push hook probe. Finalize ends in `git push`, and a
# repo `pre-push` hook gates that push against the HOST CHECKOUT's working
# tree — which leerie never modifies during a run — so a hook that rejects
# today still rejects hours later, after the run is paid for. The launcher
# probes it with `git push --dry-run` (runs the hook, creates no ref) and
# WARNS; it never refuses to start. Repos with no pre-push hook pay nothing.
# Set this when the hook is expensive enough that you'd rather not pay it
# twice. Env-var only — no CLI flag, no leerie.toml key.
export LEERIE_SKIP_PREPUSH_PREFLIGHT=1
./leerie "task"

# Pick a PR template when the repo has multiple in PULL_REQUEST_TEMPLATE/.
# Also LEERIE_PR_TEMPLATE or `pr_template` in leerie.toml.
./leerie "task" --pr-template feature

# Override the final branch this run's PR merges into (default:
# working_branch — the diff fork-point is unaffected and stays
# working_branch regardless of this override). Also LEERIE_PR_BASE_BRANCH
# or `pr_base_branch` in leerie.toml.
./leerie "task" --pr-base-branch release/1.0

# Override the model for the finalize-time PR-writer worker (default sonnet).
# Also LEERIE_MODEL_PR_WRITER or `model_pr_writer` in leerie.toml.
./leerie "task" --pr-writer-model opus

# Override the model for the dep_capture worker (default sonnet). Env-var only
# (no CLI flag or leerie.toml key — dep_capture is a post-run worker):
export LEERIE_MODEL_DEP_CAPTURE=opus

# Filter `list` output by run status:
./leerie list status paused
./leerie list status seed-failed

# Persist leerie's own combined stdout+stderr to a file (N5b), replacing the
# manual `leerie task | tee leerie-task.log` habit -- a tee target left
# inside the repo gets bind-mounted whole into every worker container,
# letting a worker read its own orchestration log. Default lands under the
# state dir, never under the repo. CLI > env > leerie.toml (log_file), same
# precedence as --state-dir. Wired for the local runtime only (--runtime
# fly/ec2 have no in-repo bind-mount to leak through).
export LEERIE_LOG_FILE=~/logs/leerie-task.log
./leerie "task" --log-file /tmp/leerie-task.log
# …or commit a leerie.toml at the repo root with: log_file = /tmp/leerie-task.log

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
# path takes over — `./leerie resume` recovers the run normally:
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
# appear in `list` with status `seed-failed` and are resumable via
# `resume <id>`. Previously these runs were invisible:
./leerie list status seed-failed
./leerie resume <seed-failed-id>

# Chain verbs: submit + manage multi-run chains. A chain is N parallel
# `./leerie --runtime fly` invocations per wave, with synth-merge between
# waves to build the next wave's base branch. The laptop is the sequencer;
# no Fly coordinator machine. Each --wave flag defines one sequential wave
# (N waves supported); waves execute in order, jobs inside a wave run in
# parallel. Per-job flags (--effort, --model, --dangerously-skip-permissions,
# etc.) are forwarded to each wave invocation. No chain-specific env vars
# required — the per-job `--runtime fly` invocations have their own env
# requirements unchanged.
./leerie chain \
  --effort high --dangerously-skip-permissions \
  --wave "prompts/fetch.md,prompts/lint.md" \
  --wave "prompts/publish.md"

# Resume after a wave failure or synth-merge conflict: re-submit
# with --chain-id pinned to the prior chain's UUID. The wave loop
# skips already-pushed waves AND skips synth-merge for transitions
# whose staging branch is already on origin (idempotency probe via
# `git ls-remote --exit-code`). Run `./leerie resume <chain-id>`
# first to unpause any per-run paused machines.
./leerie resume <chain-id>
./leerie chain --chain-id <chain-id> \
  --wave "prompts/fetch.md,prompts/lint.md" \
  --wave "prompts/publish.md"

# ID-dispatched verbs: UUID → chain scope (iterates run.json filtered by
# chain_id); Fly machine id → existing single-run behavior. The five
# deprecated dash-prefixed chain aliases have been hard-removed (no
# shim) — use the bare verbs below.
./leerie status   <chain-id>        # render per-run states from run.json
./leerie attach   <chain-id>        # poll run.json files every 5s
./leerie stop     <chain-id>        # pause every running chain run
./leerie kill     <chain-id>        # destroy every chain run
./leerie resume   <chain-id>        # resume every paused chain run
./leerie finalize <chain-id>        # push + open PR for every unpushed run
./leerie list chains              # group runs by chain_id

# Group verbs: launch N single-repo runs together as a coordinated unit.
# Each member runs in its own state dir (basename-keyed), its own branch,
# and opens its own PR — nothing is shared except the group_id and a
# read-only view of siblings via --inspect-dir. The shared brief narrows
# each planner to the joint intent; cross-repo prerequisites are rendered
# as deploy-ordering notes in each member's PR body.
./leerie group \
  --repo ../api   "add /volumes endpoint" \
  --repo ../frontend "add-disk dialog" \
  --brief group-brief.md            # optional shared brief (prepended to each member's prompt)

# Resubmit a prior group (reuse its group_id instead of minting a new one):
./leerie group --group-id <prior-group-id> \
  --repo ../api   "add /volumes endpoint" \
  --repo ../frontend "add-disk dialog"

# Group-scoped verbs: UUID → group scope (scans each member's state dir).
./leerie status   <group-id>        # render per-member run states
./leerie stop     <group-id>        # pause every running member (Fly runtime only)
./leerie resume   <group-id>        # resume every paused member run
./leerie kill     <group-id>        # destroy every member run
./leerie finalize <group-id>        # push + open PR for every unpushed member
./leerie list --groups              # list all groups across state dirs
```

## Testing

**Never run two copies of this suite concurrently on one host.** It has dense
timing-sensitive coverage — real `fork()`s, PID reaping, subreaper races,
cgroup probes, stalled-transport `timeout` paths — and CPU starvation makes
dozens of them fail nondeterministically. Measured 2026-08-01 across six full
runs: serial with the host to itself gave **0 failures** four times out of four
(`main` twice, a feature branch twice), while two runs overlapping another
pytest container gave **78** and then **57** failures. The counts are unstable
and the individual tests pass in isolation — e.g.
`tests/test_worktree_failure_not_fatal.py` is 3/3 alone. Treat any failure list
gathered under concurrent load as unusable, and re-run alone before believing
it. `-n 4` (xdist) is fine on an otherwise-idle host and matches the serial
totals exactly; what breaks is *two suites at once*, not parallelism itself.

**A local pass is not evidence until the host lacks `claude`.** The binary is
on a developer's PATH and **not** on the CI runner's, so any test that reaches
`preflight()` → `_check_claude_cli_version()` passes here and fails there with
`FileNotFoundError: 'claude'`. That is not hypothetical: PR #211 reported
"7114 passed, 0 failed" while CI was red on seven tests of exactly this shape.
Before claiming a suite green, re-run the affected files with the directory
holding `claude` removed from PATH:

```bash
CLAUDE_DIR=$(dirname "$(command -v claude)")
PATH=$(echo "$PATH" | tr ':' '\n' | grep -vFx "$CLAUDE_DIR" | paste -sd:) \
  pytest tests/<affected>.py
```

Prefer removing that one directory over a minimal `env -i PATH=/usr/bin:/bin`:
several tests legitimately need `git`, `jq` and coreutils, and a too-small PATH
produces failures that look like regressions and are not. A test whose subject
is the gate rather than the CLI should **stub** `_check_claude_cli_version`
rather than skip — skipping on CI removes the coverage instead of the
dependency (`tests/test_append_system_prompt_file.py` shows the skip form, for
the case where the CLI genuinely *is* the subject).

**A test that drives the real `main()` needs a stub `claude` on PATH, and the
gate is in `main()` itself — not `preflight()`.** `main()`'s
`if not shutil.which("claude"): die(...)` fires 87 lines before `State(...)`
mints the run dir and long before the top-level try/except, so
`_check_claude_cli_version` (which lives under `_orchestrate`, routinely
stubbed) is never reached and stubbing it does nothing. `die()` exits 1, which
is the tell: *every* expected exit code collapses to 1 at once
(`1 == 75`, `1 == 130`, `1 == 143`, `1 == 7`). A test whose expected code is
itself 1 passes that assertion by coincidence and then fails one line later on
a missing sidecar. Measured: 14 of `tests/test_main_exception_arms.py` shipped
red this way while its sibling `tests/test_main_cli_wiring.py` — the same
harness plus one call — was green. `tests/conftest.py::fake_claude_on_path` is
the single owner; import it rather than copying it, and install it in the
*fixture* every test in the file shares, not in the harness function — three
tests there call `leerie.main()` directly and a harness-attached prerequisite
misses them silently.

**`main()` mutates the pytest process, and one of those mutations changes what
"alive" means.** `_become_subreaper()` is `main()`'s SECOND statement, before
argparse, so any test driving the real `main()` leaves
`prctl(PR_SET_CHILD_SUBREAPER, 1)` set on the pytest process for the rest of
the session. An orphan that would reparent to PID 1 and be reaped then
reparents to pytest, which never `wait()`s it; it lingers as a zombie, a zombie
still owns its PID slot, and `os.kill(pid, 0)` keeps succeeding — so a liveness
probe reports a process that was in fact killed. Measured: three
`tests/test_signal_cleanup.py` orphan-reaping assertions went red on CI with
both that file and `orchestrator/leerie.py` byte-identical to `main`. Collection
is alphabetical, so `test_main_*` poisons `test_signal_cleanup`, and
`tests/test_subreaper.py` escaped only by sorting *after* it — an accident of
filename, not a fix. Hence `tests/conftest.py`'s autouse
`_restore_child_subreaper` (delegating to the public `child_subreaper_restored`
context manager) rather than a per-file fixture. Note the failure is
**deterministic** — the same 17 IDs on all three Python versions — which is how
it is told apart from the CPU-starvation flake class above.

**The obvious test for that fixture is vacuous, and the vacuity only shows
under falsification.** The natural shape is an ordered pair: one test sets the
flag, the next asserts it was restored. With the fixture broken, an earlier
test in the same file has already left the flag at 1, so the second test's
baseline *is* 1, it observes 1, and it passes — confirmed live, where it
skipped instead of failing. Drive the context manager directly with a
`prctl(…, 0)` first so the starting value is pinned and the assertion is
unconditional. Drive the **public** context manager, not the fixture object:
pytest 9 wraps fixtures in `FixtureFunctionDefinition` with no
`__pytest_wrapped__`, so reaching for the raw function is version-coupled. That
split needs its own guard — the behavioural test only proves the fixture works
if the fixture actually delegates, so `test_conftest_defines_the_autouse_restore_fixture`
pins `autouse=True` *and* the delegation.

`shellcheck` is likewise not installed on every dev host but does run in CI, so
a `scripts/*.sh` change is unverified until pushed — and it catches things
`bash -n` cannot (SC1007 on a bare `LANGUAGE=` prefix, and the backtick class
CLAUDE.md records under `tests/test_launcher_integrity.py`).

**Do not edit `orchestrator/leerie.py` (or any file under test) while the
suite is running.** A great many guards here assert via
`inspect.getsource` / `ast.parse` on the module read from disk, and Python's
`linecache` re-reads a file whose mtime changed — so an edit mid-run makes
those guards see shifted or half-written source and fail en masse for reasons
that have nothing to do with the change. Measured once during the BLT work: a
single-line docstring edit ~3 minutes into a run produced **38 spurious
failures** (`test_subreaper`, `test_warnings_before_die`,
`test_wave_integration_instrumentation`, every `test_terminal_auth_routing`
handler pin, …), all of which passed 70/70 when re-run against a frozen tree.
This is a *separate* hazard from the concurrency one above and has the same
tell: a failure list dominated by source-coupling tests. Treat any such list
gathered while the tree was moving as unusable, exactly as with a list
gathered under concurrent load.

`pytest tests/` runs the full suite. For the full per-feature / per-incident
test coverage inventory (which test file covers which surface, and the
specific traps pinned against regressing), see `docs/TESTING.md`.
The dominant real cause behind that same incident batch — a `test-`-domain
subtask declaring no `requires`/`depends_on` edge to the feature subtask whose
not-yet-created output it targets — is pinned in
`tests/test_warn_test_missing_producer_edge.py`: the new
`_warn_test_subtask_missing_producer_edge` advisory (mirrors
`_warn_provider_subset_subtasks`) fires when a `test-` subtask has empty
`requires`+`depends_on` while another subtask in the plan is a producer
(`provides` or `files_likely_touched` non-empty), and stays silent when the
subtask declares either edge, when no other subtask is a producer, on a
single-subtask plan, and on a non-`test-` subtask (never the advisory's
target). `test_disjoint_paths_shape_fires` is a regression pin reproducing the
real sibling-service failure shape (a coverage-floors test subtask with disjoint
file paths from the feature subtasks it must register — the case a mechanical
file-overlap rule would miss, but a declaration-absence check catches). The
fix is deliberately advisory, not auto-wiring: research proved no mechanical
signal (exact-path or basename-stem overlap) reliably wires the real failure
cases, so the wiring gate (`phase_wiring_gate`, see below) remains the actual
enforcer. The full per-feature/per-incident test-coverage narrative for
this stretch of the codebase (artifact_registry, conformer/baseline
hardening, EC2 lifecycle/transport/credential surfaces, the worker-prompt-
over-stdin and appended-system-prompt transports, the no-result-event retry
and `_run_checked_loop` crash policy, the integrator-crash rescue path, and
`resume` auto-pick) has moved to docs/TESTING.md; see that file for the
underlying test names and measured claims.

**Host-only tests are gated on `jq`** (`HAS_JQ` in `tests/conftest.py`,
mirroring the `HAS_TREESITTER` pattern). Tests that source bash the host
owns (`scripts/host-finalize.sh`, `provision.sh`'s `decide_teardown`, the
launcher's `finalize`/`no_push` paths) parse `run.json` with real `jq` and
must skip under `HAS_JQ`, since the leerie container image deliberately
omits `jq` (code running *inside* the container uses python3 instead — see
`scripts/remote/seed-auth.sh`). **Do not "fix" a skip here by adding `jq`
to the Dockerfile**: per DESIGN §6 *Finalization*, those scripts can never
succeed in-container anyway (gh auth, ssh-agent, and Keychain are
host-side), so installing jq buys a green tick, not working code, and
erodes the host/container boundary. `tests/test_jq_gate_wiring.py` guards
that every such module both imports `HAS_JQ` and carries a `skipif`
referencing it — a module-level `skipif` does not propagate through an
import, so a file reusing another's runner needs its own. Full detail
(including the push-output-capture and locale/byte-vs-character traps this
gate's neighbors hit) is in docs/TESTING.md.
Raising bought diagnostics only, at the price of a precondition every
hand-built `State` must know about: measured, **141 tests red**, and 8 test
files still needed a fixture seed after the fallback landed.
**A conftest autouse fixture (`_no_real_planning_worktree`) stubs
`_ensure_planning_worktree` for every test**, opt-out via
`@pytest.mark.real_planning_worktree`. It shells out to a real
`git worktree add` rooted at `resolve_leerie_root()`, which with
`LEERIE_STATE_DIR` unset is `<repo>/.leerie` — so every test driving the real
`_run_phases` created a full checkout of this repo inside this repo. Silent
three ways over: `.leerie/*` is gitignored so `git status` stayed clean, the
directories outlived the session, and the damage surfaced in
`tests/test_helper_naming_convention.py`, whose `tests/` exclusion is a
relative-path prefix that a nested
`.leerie/…/worktrees/planning/tests/…` copy does not match. Measured before
the guard: 2 worktrees, 25 MB, and one red test on CI with no visible link to
the change that caused it — local runs were green because the pollution only
bites a later scanner. When adding a fixture that shells out to git, assume
the state root is inside the repo unless the test pins it elsewhere.

`leerie resume <run-id>` — the documented positional form — is pinned by
`tests/test_resume_positional_run_id.py`. It silently ignored the run-id on
every runtime until 2026-08-05: `main()` popped only `argv[0]` (the verb), so
a run-id in `argv[1]` bound to argparse's `task` positional, `--run-id` stayed
`None`, and `resolve_run_id` **auto-picked a different run** — measured live
against a *running* one, where only the run-directory flock prevented a second
orchestrator (an idle run would have been resumed silently). `resume` is the
only verb exposed to this: `stop` / `kill` / `accept-blocked` / `finalize` /
`status` all `exit` inside the launcher and never reach that argparse.
`_extract_resume_run_id()` now takes the positional **before** `parse_args`
(the ordering IS the contract — afterwards `task` has already swallowed it),
scoped to `resume` because `list` has its own positionals
(`list status paused`, `list chains`), with a `die()` when a positional and
`--run-id` disagree. **No existing test could catch it** —
`test_resolve_run_id*.py` call `resolve_run_id` directly *with* an id, so they
passed against broken plumbing; nothing crossed the launcher→argparse
boundary, the same shape as the coverage-gate bug above. Two traps recorded in
that file: reverting only the *wiring* (helper defined but uncalled) must fail
— a present-but-inert fix is the failure mode that let the coverage gate ship
— and the safety proof that `args.task` is read only on the non-resume branch
walks the AST of `_run_phases`, because the obvious
`"args.task" not in getsource(main)` passes trivially (the reads are in
`_run_phases`, not `main`) and proved nothing.

**The fresh-run branch of `_run_phases` had no execution coverage at all**
until `tests/test_run_phases_fresh_init.py`, and v0.20.0 shipped a
`NameError` in it that killed every non-resume run. Two structural reasons,
both worth remembering when adding a guard here. First, **every path that
executed `_run_phases` did so with `resume=True`** — `resume=False` appeared
nowhere under `tests/`, so the branch that every real run takes was never
run. Second, the guard that did exist was a key-presence AST walk, and **it
passed against the broken code**: the key was in the dict literal, only its
value expression was unevaluatable. Presence is not evaluation: a walk that
checks a key exists says nothing about whether that key's value resolves,
which takes either execution or scope resolution. See `docs/TESTING.md` for
the full detail (which test files, which harness quirks made the gap read
as covered).

**The general rule: a test asserting STRUCTURE must be paired with one
asserting SUBSTANCE.** Structure is a dict key, a source substring, an AST
node, a phrase in a prompt. Substance is the value that flows through it,
the result of executing it, or the order it appears in. Structure-only
assertions are necessary and never sufficient, and the gap is invisible
because they pass. Four measured instances, all from one change
(2026-08-17):

| structural assertion | what passed it |
|---|---|
| the reconciler payload has key `scope_note` | `"scope_note": ""` — key shipped, planner's text discarded |
| `phase_plan` calls `_effective_source_of_truth` | ctx reads the preference directly, or omits the key entirely |
| the abort message contains every remediation phrase | the fallback hoisted back to lead — the wording the A/B measured as misrouting 5/5 operators |
| `die(_unresolvable_die_message(...))` exists in source | the gate reads `out.get("unresolved")`, never fires, and **140 tests stay green** |

The cheapest discriminating test per shape: **execute the consumer** (not read
its source); **assert the value** (not the key); **assert the order** (not the
presence). Where the subject is prose, none of those reach semantic inversion
— a phrase can be present and negated — so the guard there is a behavioural
probe, not another substring. And when parametrizing a value test, make the
inputs **disagree**: a row where two sources of a value are equal cannot tell
a correct read from a bypass.

For the per-feature/per-incident test inventory that used to sit here
(planning-worktree isolation, resume-positional-run-id, the fresh-run
`_run_phases` NameError, launcher-block duplication guards, `leerie_commit`
state-field wiring, EC2 resume/list/accept-blocked coverage, the
context-overflow/terminal-auth-failure classifiers, the 2026-07-19
argv-E2BIG-and-coverage-freeze incident harness, and the
DiskLowSpace-handler-reraise fix) — see `docs/TESTING.md`.
re-raiser went unguarded because the comment named only one of the raise
sites, which is why `test_survives_a_save_that_is_still_failing` in
`tests/test_disk_preflight.py` asserts against *every* save in the arm
rather than pinning one call. Second, fixing one arm is never the fix if
the pattern (a bare `st.save()`) is repeated — eight other handlers in
`main()` carried the identical bare `st.save()`; all now route through
`_save_state_best_effort`, which logs and never raises. Third,
`issubclass(X, BaseException)` proves nothing about whether a handler is
reachable or safe — the test it replaced asserted only
`issubclass(DiskLowSpace, BaseException)` and concluded no separate
handler was required, a tautology that reasoned its way to a false
conclusion. Full incident detail: docs/TESTING.md.

**A timeout is infrastructure, not a leerie bug**, and must be classified
alongside `WorkerError` in every retry/escalation path, not treated as "a
bug in leerie itself" — and never interpolated into a message verbatim
(`str()` on a `TimeoutExpired` includes the full invoking argv). See
`tests/test_checked_loop.py` and docs/TESTING.md.

**Derivation guards are one-directional unless you write the converse.**
A test that iterates a table only proves every table entry is reproducible,
not that every entry that *should* be in the table is. See docs/TESTING.md
for the `TIMEOUT_DEFAULT_PER_WORKER` and `main()` caps-wiring instances.

**Ablate a pattern against its corpus instead of adding alternatives.**
Remove each alternative in a matching pattern in turn and check it against
real cases — unreachable alternatives are false-positive surface, not
defense in depth. See docs/TESTING.md for the
`_host_finalize_is_auth_or_network_push_error` case, including a
provably-dead companion arm found the same way.

## Commit messages are the permanent record

This repo **squash-merges**, and the repository setting is
`squash_merge_commit_message: COMMIT_MESSAGES`. So the squash body on `main`
is the branch's **commit messages, concatenated** — a four-commit PR lands
all four, in order, each prefixed with `*`. **The PR description is
discarded.** Verify with `git log -1 <squash-sha> --format=%b` on any recent
merge.

Three consequences, each learned the hard way:

- A correction belongs in a **commit message**, not only in the PR
  description. A description rewritten before merge reaches nobody.
- A branch that supersedes its own earlier work must say so in its **final**
  commit message, because the superseded ones remain on `main` verbatim. PR
  #203 landed with a body that opens by presenting a feature the same body
  later withdraws — accurate in sequence, misleading at a glance.
- A single-commit PR is the case where the two happen to look identical
  (its lone message is the whole body). Do not generalise from it; that
  inference is what produced the previous bullet.

**Verify counts and claims in a commit message the way you verify code.**
Four of five consecutive commit messages on one branch carried an unverified
number — "16 tests deleted" (20), "all four are corrected" (three), "as it
was before this branch" (the feature came from the previous PR), "three
alternatives are load-bearing" (four). Every one was a figure measured
against an earlier state and carried forward after the state changed. If a
message states a count, re-derive it against the diff you are about to push.

**Re-deriving is not enough on its own: the command's scope must match the
sentence's scope.** #208 — whose subject was *correcting* a claim written into
CLAUDE.md without being derived — then carried two more underived numbers of a
different shape. One was never measured at all ("21 of its 24 keys" — the
function has 21, the copy had 25, and 24 is neither). The other was measured
correctly and then described wrongly: "the three existing harness consumers
plus the two new files pass together (61 tests)" — 61 was the output of a
*six*-file pytest invocation, and the five files the sentence names collect 44.
A number lifted from a command with a wider scope than the claim reads as
verified and is not. So: run the command whose scope is exactly the sentence's
scope, at the moment of writing, and if the sentence names a set of files, name
that same set on the command line.

## Task completion checklist

Before marking a change complete:

- [ ] Re-derive every count and claim in the commit message against the
      actual diff — see "Commit messages are the permanent record" above.
- [ ] Update `IMPLEMENTATION.md` if the change affected code surface
      described there.
- [ ] Update `DESIGN.md` only if the architecture itself changed.
- [ ] `pytest tests/` — all pass.
- [ ] `python3 -c "import ast; ast.parse(open('orchestrator/leerie.py').read())"`
      as a static check.
- [ ] `pytest tests/test_no_undefined_names.py` — the undefined-name scan.
      **`ast.parse` does not subsume this**: an undefined name parses
      perfectly and raises `NameError` only when its line executes. v0.20.0
      shipped one (`repo_root` in `_run_phases`' fresh-run branch) past a
      green `ast.parse` and a green 6751-test suite, and it killed every
      fresh run.
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
- [ ] `grep -qE '^\s*chain\)|^\s*status\)|^\s*attach\)|^\s*kill\)|^\s*list\)' leerie`
      — if chain launcher verbs were touched, confirm the bare-verb arms
      (`chain`, `status`, `attach`, `kill`, `list`) are present and the five
      deprecated dash-prefixed chain aliases stay hard-removed (no shim);
      see DESIGN.md §19 and IMPLEMENTATION.md "Chain verbs";
      `pytest tests/test_chain_launcher_id_dispatch.py` for the
      ID-dispatch contract test.
