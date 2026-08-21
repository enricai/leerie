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

Two further harness traps —
the degrade test stubs `mktemp` to force the no-temp-dir fallback, and
stubbing it for **every** form aborts the function at the rebase step's
`mktemp -d`, several steps earlier, so the test passed on a path that never
reached the push; the stub must fail only the plain-file form. And the shared
runner decodes with `errors="replace"`, because a byte-anchored truncation
can legitimately land mid-character and strict decoding makes the harness
raise `UnicodeDecodeError` before a single assertion runs — testing the
harness rather than the code. (`jq` itself is unbothered: verified, it
substitutes U+FFFD and still writes valid JSON at rc 0, which is why the
byte cut is safe for `run.json`.)

The per-subtask delta proxy's `{test_files}` tier is covered by
`tests/test_test_files_proxy.py` (48), `tests/test_scoped_proxy_corpus.py` (5)
and `tests/test_scoped_degrade_warning.py` (11). Three lessons generalise past
this feature. **(1) A non-test path is an ERROR to pytest, not a no-op, and one
of them poisons the whole invocation** — measured, `pytest orchestrator/leerie.py`
exits 5, `pytest docs/DESIGN.md` exits 4, and `pytest docs/DESIGN.md
tests/test_blt_semaphore.py` ALSO exits 4. Since a real subtask diff mixes docs
and source with its tests, a `{files}` template on a runner with no source→test
impact analysis reports RED on nearly every subtask; the fix is to filter the
substitution (`{test_files}`), not to abandon the proxy, with the pre-existing
empty-list rule doing the rest — a diff with no test file renders nothing and
falls back to canonical. **(2) Scan the author's input, not the rendered
output.** The unknown-placeholder guard first shipped scanning the SUBSTITUTED
command, so a changed-file path containing braces (`src/{locale}/page.test.ts`
— the brace-routing analogue of the `src/app/[locale]/(app)/…` path
`shlex.quote` exists for in that very function) was read as an unknown
placeholder: it disabled the proxy *and* emitted a warning misdiagnosing it as
install skew, sending the operator to re-run install.sh for nothing. Scanning
the template with `_SCOPED_PLACEHOLDERS` stripped removes the hazard by
construction rather than by widening the regex. **(3) A planner prediction is
not a diff.** The ratio the tier rests on was first taken from
`files_likely_touched` and was badly wrong — 40% test-touching predicted (109
of 270) against 94% real (34 of 36) — because CLAUDE.md mandates tests and
implementers add them whether or not the planner predicted it. The frozen
corpus is 36 REAL per-subtask diffs recovered from leerie's own run branches,
and each row must be ONE subtask's work: an integration merge's **first-parent**
diff, since a plain two-dot diff against the run base is cumulative and folds
in siblings — which is how the first recovery attempt reported 0% source-only
and nearly shipped a fixture that could not exercise the canonical fallback at
all.

`tests/test_prepush_preflight.py` (25) covers `host_prepush_preflight`, the
run-start probe (DESIGN §6 *Finalization*). Real repos, real hooks, no stubs —
the probe's whole value is running the real gate. Its load-bearing test is
`test_probe_pushes_a_new_ref_so_the_hook_gets_real_stdin`: probing the
already-up-to-date working branch still runs the hook but hands it **empty
stdin** (verified against real git), so a hook that iterates the ref updates
git feeds it exits 0 — a false pass, the worst possible outcome for a probe
whose job is predicting a rejection. Pushing a new ref under leerie's own
namespace reproduces the exact line finalize will produce. Falsified live:
changing the refspec to `"$branch"` fails exactly that test with rc 0.
Paired with `test_probe_creates_no_ref_anywhere` (the property that makes
running a real gate safe) and a launcher-gate parametrization that **extracts**
the preflight block from `leerie` rather than reproducing it. It also pins the
**chain** contract: `chain` backgrounds one `./leerie` per job against a single
shared checkout, so without care every job re-runs the hook — N concurrent
lint/typecheck runs computing one answer, N identical warnings. The chain arm
probes once per WAVE (after the checkout that establishes the tree those jobs
will push from) and hands each child `LEERIE_SKIP_PREPUSH_PREFLIGHT=1`. Both
halves are pinned, and both are load-bearing: skipping in the children alone
removes the check from the most expensive kind of run, which is the opposite of
the point. `group` is deliberately exempt — separate repos, separate questions.
Two traps in that arm. Its `--no-push` skip must read `_ch_passthrough`, since
`NO_PUSH` is first assigned *after* the chain arm and so does not exist there —
the single-run gate's opt-out silently has no counterpart otherwise. And the
block is **executed** by its tests, not merely string-matched: `bash -n` catches
syntax, not an unbound variable or a bare `"${arr[@]}"` on an empty array under
`set -u`, which is the same "scanning is not calling" lesson
`test_ec2_bash32_portability.py` records — so `_chain_probe_block()` is bounded
before the fan-out (running the wider extraction would background a real
`./leerie`) and driven against a real repo.

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
The launcher's `stop` verb EC2 dispatch — the counterpart to
`_auto_detect_fly_runtime` for EC2 runs, DESIGN §6 "Run identifier" —
is tested in `tests/test_ec2_launcher_stop.py` by invoking the real
`leerie` binary (not an extracted block, since `stop` is an early
fast-path verb dispatched before container preflight) against the
same resource-tracking `aws` stub: an `ec2-instance.json` sidecar
auto-detects the EC2 runtime and `stop <run-id>` drives the
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
`tests/test_accept_blocked.py`'s local-path pattern) across `stop`,
`kill`, `accept-blocked`, and `finalize`: each accepts `ec2`
alongside `local`/`fly` in its `--runtime` enum validation (rejects other
bogus values with the updated three-way message). No verb fails closed on
EC2 any more: `finalize` and `resume` now promote to `ec2` and enter their
EC2 arms (covered end to end by `tests/test_ec2_launcher_finalize.py` and
`tests/test_ec2_launcher_resume.py`), so this file's three EC2 cases assert
the *promotion* plus an arm-specific failure — never the retired blanket
refusal. `resume` is the one verb here that does not exit promptly after
detection (it falls through into the launch path's unconditional container
image build), so its case captures stderr on timeout rather than waiting;
the Fly auto-detect regression path (no
sidecar override, `LEERIE_FLY_APP` unset) still reaches the pre-existing
Fly-specific error, proving detection promoted to `fly` and reached the
Fly branch. `stop` and `kill` both wire real EC2 actions (test-001 and
feat-006 respectively — see `tests/test_ec2_launcher_stop.py` and
`tests/test_ec2_launcher_kill.py` above/below for their end-to-end
coverage): passing `--runtime ec2` against a run dir with no
`ec2_instance_id` anywhere dies with "no ec2_instance_id found" instead of
the old fail-closed message, and auto-detecting the `ec2-instance.json`
sidecar proceeds past detection into AWS credential resolution (which
fails in this test's env for unrelated reasons — no `aws` binary/credentials
set up) rather than hitting the old fail-closed message. `resume` is
covered separately: an `ec2-instance.json` sidecar fails closed with a
resume-specific message instead of promoting `RUNTIME=ec2` (which would
otherwise fall into the launcher's fresh-provision `RUNTIME=ec2` branch and
die with an unrelated "not yet wired" message), while a `fly-machine.json`
sidecar still promotes to `fly` as before. Neither `accept-blocked` nor
`finalize` wire an EC2 verb *action* yet — that is feat-007/feat-008 (and
a later `resume` subtask); this subtask's scope for those two remains the
detection helper and the `--runtime` enum validation it feeds.

`kill`'s EC2 action — resolving `ec2_instance_id` from the run dir,
resolving AWS credentials, re-resolving `LEERIE_EC2_SSH_TARGET`, and
syncing state via `_try_fetch_state_for_ec2_teardown` BEFORE calling
`terminate_instance()` (the one-way-ratchet invariant
`ec2-provision.sh:262-272` documents) — is tested end to end in
`tests/test_ec2_launcher_kill.py` against the real `leerie` launcher
binary. The `aws` stub combines two behaviors behind one binary since
`kill`'s EC2 path exercises both surfaces in a single run: `ssm
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
convention as the Fly/local `kill` paths) rejects a wrong confirmation
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
`_apply_multidrop` layers (`_schedule()` is documented deterministic, so
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

The same file also covers the **multi-artifact pair** contract (DESIGN §5
*Multi-artifact pair*) and the `artifact`-is-a-logical-name rule, both
added after run `e2882da6…` (2026-08-01) died in phase 2¾ having written
no code. `check_overlap_judge_output` treated any `artifact` containing
`/` as a bare path, so the descriptive names the prompt actually asks for
(`docs/USAGE.md bare-verb rewrite`) read as hallucinated files — 6
spurious `PHANTOM_ARTIFACT` issues on an emission that replays clean. The
retry those issues forced then expressed one pair's two-file overlap as
two rows, which the bare pair-repetition gate refused. `artifact` is now a
prose **label** Python never parses: the judge names the files in an
`artifact_paths` array and `PHANTOM_ARTIFACT` does set membership
on that (CLAUDE.md *Language-to-JSON* — never hand-parse an LLM's
response). That array is **asked for but no longer `required`** (changed
2026-08-03): requiring it proved far more destructive than the false positives
it prevented — `plan_overlap_judge` produced valid output on only 40.9% of its
corpus invocations (27/66) against 99.6–100% for every other worker, and 84 of
its 85 validation failures were the lone error `'artifact_paths' is a required
property`. Absence was already the designed-for case (`if not paths:
continue`), so the requirement bought no verification and turned a graceful
skip into a discarded plan. Pinned by `TestProsePathParsingAbsent`: `_depunctuate` /
`_path_shaped` are gone, the check calls no `.split()`/`.strip()` on
`artifact`, the schema requires the field with `minLength: 1` items, and
the prompt actively asks the judge to fill it — a pathless collision
silently disables the check, and 84% of the 64 collisions ever emitted
carry a path. The behavioural pair is
`test_prose_in_artifact_is_never_parsed_for_paths`: the same invented path
must be invisible in the label and flagged in the field, so neither half
can pass vacuously.
For duplicates, what must agree is the resolved **effect**
(`_collision_effect`: dropped sid, or unordered merge pair) — never the
`resolution` string, since swapped-endpoint `drop_a` rows share a string
and delete opposite subtasks. A 4×3 parametrized matrix freezes the
composition of the three gates involved: every effect-differing shape is
terminal (via the pre-existing `_contradictory_drop_sids` keep-and-delete
gate — on a two-sid pair any effect difference makes one sid both dropped
and surviving, which is *why* relaxing the pair check opened no hole),
every effect-identical shape coalesces to one row keeping all artifacts,
and each conflicting shape is offered a `DUPLICATE_PAIR` retry round
first. A separate test pins that the `DUPLICATE_PAIR` string keeps the
`LABEL: subject — detail` shape, since `_issue_signature` splits on the
first em dash and the row count must not perturb the oscillation key.

`resume` must not bypass the phase-3 semantic wiring gate
(`tests/test_wiring_gate_resume.py`). `phase_wiring_gate` is
detect-and-die, and its skip-on-resume used to key on `plan_snapshot` —
which is written *earlier*, deliberately, so a die() at either terminal
gate does not discard the planning spend. The snapshot is therefore
present even when the gate FAILED, so a resume skipped the whole branch,
never re-invoked the gate, and executed the plan the gate had rejected —
while the die() message claimed the gate had "no bypass flag" (run
`3a4abba3…`, 2026-08-01: verified to reach `phase_execute` with zero gate
invocations). The skip is now keyed on `st.data["wiring_gate"]`, written
only on a clean pass. All three shapes are pinned behaviorally against the
real `_run_phases` with every phase stubbed and counted (reusing
`tests/test_resume_planning_reentry.py`'s harness): gate-died → re-runs,
fresh run → runs (the anti-vacuity control), gate-passed → skips, so the
budget-check resume this branch exists for stays cheap. A `WorkerError`
degrade writes nothing, so it re-attempts rather than inheriting a verdict
never reached. Two source-coupling guards pin the structure the stubs
cannot see: the call sits behind its own `wiring_gate`-keyed guard
*outside* the `plan_snapshot` if/else, and the die() text states that
`resume` re-runs it.
`tests/test_phase_wiring_gate.py`'s
`test_wiring_gate_is_not_re_invoked_on_budget_check_resume` was rewritten
in the same change — it previously pinned the old structure by source
order (gate call between the snapshot write and the `else:`); it now pins
the audit-key guard while asserting the same cheap-resume property.

The wiring gate's **constrained auto-repair** (DESIGN §5 *A wiring re-check on
the fully-merged plan*) is pinned in `tests/test_wiring_gate_repair.py`. The
commonest defect the `wiring_judge` finds is one no planner could have
avoided: planners run blind, so a subtask in domain X cannot declare a
`requires` on a tag domain Y's planner has not invented yet, and
`phase_reconcile`'s charter is *declared-but-unmatched* tags — a subtask that
declared nothing never enters its `unresolved_requires` input. Measured across
the corpus (2026-08-01), **6 of the 9 runs that ever reached this gate died at
it**. `_repair_missing_requires` adds an edge only when the defect is
`missing_requires`, the sid is in the plan, `tag_or_dep` is non-empty (the
schema carries no `minLength`, and a subtask declaring `provides: [""]` would
otherwise make the tag channel synthesize a meaningless empty-tag edge), the
edge is not already declared, and it leaves the graph acyclic.

Given that, **`tag_or_dep` is resolved against BOTH dependency channels** —
the field name is literal, and the judge fills it with either. First match
wins, tag first, so pre-existing behavior is unchanged: **(a) tag** — exactly
one in-plan provider that is not the sid → append an in-plan `requires`;
**(b) id** — a surviving subtask id that is not the sid → append a
`depends_on`, unambiguous by construction since an id names exactly one
subtask; **(c) single-cluster fan-out** — several providers that all share one
`_cofile_cluster` → append the `requires` tag, because those providers are the
sub-file region splits of ONE file (§5½ (P1)) and requiring the tag orders the
subtask behind the whole cluster. Each repair records a `channel`.

Reading only the tag channel was the original shape (PR #145) and was the
dominant refusal cause: **23 of the 24 defects refused as "no in-plan
provider" named a surviving subtask id**, and run `62a19deb` died with 22
defects of which every one was that shape; a second run's refusals were one
tag with eleven providers that were all one cluster. Closing both channels
took the corpus from 19/27 to 21/27 runs clearing the gate and from 35/63 to
9/63 unrepaired defects — and the repair now resolves **5 of those 6** historic
deaths rather than 3. The residual refusals are genuine: a `tag_or_dep` that
is neither a surviving id nor a provided tag means the plan lacks the *work*,
not the edge.

Pinned: the incident shape repairs and reschedules producers strictly before
the consumer (with an anti-vacuity control that the *un*repaired plan races
them); a value that is neither a provided tag nor a subtask id declines;
providers spanning *different* clusters decline
(`test_multiple_providers_in_different_clusters_declines`), while one shared
cluster repairs; the id channel declines a self-reference and respects the
same cycle guard; the tag channel wins when a value is both a tag and an id; a
non-`missing_requires` kind, an unknown sid, an empty `tag_or_dep`, and a
self-provider all decline; an already-declared edge is neither repaired nor
gating (on both channels).

**The already-declared guard is channel-local AND sits downstream of channel
selection**, so a defect matching no channel takes the `else: unrepaired;
continue` arm and never reaches `tag in declared` — the guard is structurally
dead on the only path that reaches the `die()`. Run `05fdffb8…` (navegando)
died there on a finding that was false as written: `test-003` already declared
`requires: action-echoed-row-payload`, the very tag reported missing, which
orders it behind *every* provider — but the tag's two providers spanned
clusters, so no channel matched and the whole planning spend was lost on a gate
with no bypass flag. `_filter_defects_already_ordered(plans, defects) ->
(surviving, notes)` re-checks the residual after the repair loop (same return
shape as its pre-repair sibling `_filter_provably_false_wiring_defects`; notes
route into the existing `already` log line). Three properties are load-bearing
and each has its own killing test. **(a)** Ordering resolves through
`_build_predecessor_graph`, not `depends_on` — the same reason
`_would_cycle_after` routes through it — because `requires` entries with
`extent: in_plan` create edges too and **99 of 535 direct corpus orderings (19%)
exist only through that channel**; `test_ordered_via_an_in_plan_requires_tag_is_also_dismissed`
fails against a `depends_on`-only check, and
`test_requires_with_a_NON_in_plan_extent_still_gates` is the sharp control
separating "used the real helper" from "loosely scanned the requires array".
**(b)** *Every* producer must precede the sid, never any one:
`test_ordered_behind_only_SOME_producers_still_gates` — dismissing on `&` waves
through the exact race the gate exists to catch, strictly worse than the
over-gating being fixed — plus `test_a_capability_nothing_provides_still_gates`,
since `set() <= anything` is vacuously True and an unguarded subset test
dismisses the canonical TRUE finding (that mutation kills 7 tests, 5 of them
pre-existing). **(c)** Direct edges only, never the transitive closure (a
further 127 corpus orderings hold only transitively):
`test_ordering_that_holds_only_TRANSITIVELY_still_gates` pins the scope as a
decision, not an accident. **The pass is scoped to `kind ==
"missing_requires"`** — the repair loop routes every non-repairable defect to
the same residual, so `broken_by_drop`/`broken_by_merge` reach it too, and
ordering cannot refute those (they assert the *work* is gone; scheduling behind
a subtask does not restore a capability it no longer provides). The upstream
`_filter_provably_false_wiring_defects` does not backstop it — that predicate
fires only when the named *capability* is still provided, and a `tag_or_dep`
naming a surviving subtask id is not a tag, which was measured dismissing a
`broken_by_drop` before the guard existed
(`test_broken_by_drop_is_not_dismissed_on_ordering`, with
`test_the_same_shape_as_missing_requires_IS_dismissed` as the byte-identical
positive control so the guard cannot pass by disabling the pass wholesale).
The pass emits its own log line rather than reusing the per-channel `already`
wording, since two of its three dismissal shapes are not an edge the subtask
declares; both messages are pinned in `TestDismissalIsVisible`. The pass runs after the repairs rather than before
because a residual can also be mooted by an edge a *sibling* defect's repair
added and emission order is arbitrary — `test_order_independent` emits the
survivor FIRST, which a pre-filter cannot dismiss. Provably inert on the pinned
corpus (0 unrepaired defects across all 6 runs, so
`test_wiring_repair_corpus.py`'s counts cannot move). The cycle
guard has its own group because it is load-bearing rather than defensive — a
well-formed but WRONG edge was measured closing a cycle across an entire plan,
so `test_plan_still_schedules_after_a_skipped_cycle` asserts both that the
skipped edge leaves a schedulable plan AND that force-applying it makes
`_schedule()` die; `test_cycle_trials_are_cumulative` pins that trials run
against the plan as already mutated, so individually-safe edges cannot combine
into a cycle. Two source-coupling guards close the loop: `_run_phases` must
re-run `_schedule()` and rewrite `plan_snapshot` when repairs land (otherwise
the budget preflight, `check_plan_wiring`, `_validate_plan` and `_write_plan` all
see the pre-repair wave partition), and the `die()` must precede the
`st.data["wiring_gate"]` write so a failing gate leaves no key for `resume`
to skip on. **Note:** widening
`_warn_test_subtask_missing_producer_edge` past its `test-` prefix was tried in
the same change and reverted — it does not catch this class. Run 6146bd2f's
under-wired subtask declared 3 `requires` and 3 `depends_on`; it was missing
four *specific* edges, not all of them, so the advisory's both-empty condition
never held regardless of prefix. Widening only added noise by flagging
legitimate root producers.

The repair's *measured effect* — as opposed to its per-rule behavior — is
locked separately in `tests/test_wiring_repair_corpus.py` against
`tests/fixtures/wiring_repair_corpus/corpus.json`, the real recorded
`wiring_judge` output plus real post-filter plans from the six runs that ever
died at this gate (prose redacted; only the fields the repair reads are kept,
plus `_cofile_cluster`, which the single-cluster channel needs). It pins the
per-run repaired/unrepaired counts, the **channel** each run's repairs flow
through (a defect repairing for the *wrong* reason is a regression the counts
alone cannot see), and the headline 5-of-6 ratio. Change the acceptance rules
and this file fails with a message telling you the documented trade-off moved
— which is the point.

The **deterministic duplicate-provider floor** beneath `phase_overlap_judge`
(DESIGN §5 *A deterministic floor underneath the judge*) is
`check_duplicate_providers(plans) -> list[str]`, pinned in
`tests/test_duplicate_providers.py`. It flags two subtasks that declare the
same `provides` tag AND whose `files_likely_touched` intersect — pure set
logic over structured planner fields, no prose read. It exists because the
judge's 100% corpus recall is recall *when it runs*: it cheap-skips
single-planner plans, is skippable by flag, and was bypassable by a downstream
gate re-planning after it passed. The call therefore sits **above every skip**
in `phase_overlap_judge`, and a source-coupling test enforces that ordering —
a mechanical check a flag can switch off is not a floor.
**The `_cofile_cluster` exclusion is load-bearing, not a refinement**: without
it the rule matches 3571 pairs across the corpus, with it 9 — in exactly two
runs, both destroyed by duplicate work (`392b5e7f` died at the wiring gate;
`19a70d96` executed both duplicates and was refused at the integration gate
after 4.7h/164 workers, having been scored CLEAN by the `wiring_judge`). The
committed fixture makes that reproducible: stripping the marker floods
`62a19deb` with 1752 false positives and `ad69057f` with 165. An
"already ordered by `depends_on`" exemption is deliberately absent — measured
zero such pairs. Paths are canonicalized with `_normalize_artifact_path` (not
`os.path.normpath`, which keeps a leading `/` and would miss `/src/x.ts` vs
`src/x.ts`), matching the sibling `NO_FILE_OVERLAP` check. Shipped
**advisory** — logged, never gating — pending confirmation across live runs.

The launcher's **stale-install warning** (`_warn_if_leerie_stale`,
IMPLEMENTATION.md §0) is pinned in `tests/test_stale_install_warning.py`,
which extracts the function verbatim from `leerie` and drives it against real
local git fixtures (an "origin" plus a clone rewound behind it; no network).
Running `leerie` never advances `$LEERIE_REPO` — only re-running `install.sh`
does — so an install can sit arbitrarily far behind while the operator
believes otherwise. Measured cost: two multi-hour funeralworks runs on
2026-08-02 died at the wiring gate on a v0.9.100 install, reproducing the exact
failure v0.9.101 fixes, with `state.json` recording `leerie_version: 0.9.100`
while the dev checkout was already 0.9.102. **The throttled fetch is mandatory,
not an optimization**: `HEAD..@{upstream}` reads the *cached* remote-tracking
ref, which on a never-fetched install is exactly as stale as the checkout — so
a fetch-free guard stays silent through precisely the failure it exists to
catch (`test_warns_when_the_cached_ref_is_stale`, falsified live: removing the
fetch fails 4 tests). Bounded at `timeout 5`, throttled to once per 24h via an
mtime stamp in the state dir (same convention as `.dockerfile-hash`), and
warn-only — a detached HEAD, no upstream, a non-git prefix, or an unreachable
remote must all stay silent and never fail a run.

`plans_after_*` checkpoints must be snapshots, not live references
(`tests/test_checkpoint_aliasing.py`). `_run_phases` assigned
`st.data["plans_after_X"] = plans` and handed the SAME list to the next
phase — and `phase_reconcile`'s renames, `phase_overlap_judge`'s
merges/drops, and both phase-3 soft-drop filters all mutate `plans` **in
place**, so every later `st.save()` retroactively rewrote all earlier
checkpoints. Measured on run `3a4abba3…` before the fix: all six of
`plans_after_plan` … `plans_after_filters` were byte-identical, and
`plans_after_reconcile` held 15 subtasks while the overlap judge's
independently-recorded input (`calls.ndjson`) had 16 — a silent
contradiction of the DESIGN §6 "Resumable planning" contract, which
describes each key as that phase's output "as it stood immediately
after". Pinned: an earlier checkpoint survives a later in-place drop AND
field rewrite, adjacent checkpoints are neither the same object nor
byte-identical across a mutating phase, the distinction survives a real
`State.save()` round trip read back off disk (constructed by reading
`state.json` directly, not a second `State` — `State.__init__` takes an
exclusive flock the live instance still holds), and a source-coupling
guard requires `copy.deepcopy(` on all six assignments so a newly added
checkpoint cannot reintroduce the alias. The same aliasing class applies
to `st.data["plan_overlap_judge"]`, deep-copied at its persist so the
coalescing step cannot rewrite the "raw judge output" audit.

`tests/test_no_dead_functions.py` is a whole-module guard that no
**private** module-level function in `orchestrator/leerie.py` is defined
but never referenced. It is deliberately not a list of names: pinning
specific ones catches a regression on exactly those and nothing else. It
scopes to underscore-prefixed helpers because public names are API surface
invoked from outside the module — `run_rebaser` from
`scripts/host-finalize.sh`, `run_recapture_deps` from the launcher's
`config --recapture` arm, `compose_pr_body` / `_compute_subtask_branch` /
`resolve_token_probe_cache_sec` from bash or tests — none of which appear
as references inside `leerie.py` itself, so a module-scoped scan calls all
five dead. It found three real ones (2026-08-01 audit), all pre-existing:
`_confidence_issues` (IMPLEMENTATION.md had already recorded it as having
"zero remaining callers" after DESIGN §8 replaced every self-score gate
with an independent verifier — the function and its unit tests, the only
remaining callers, were left behind), `_repo_map_cache_key` (described a
cache key nothing computed), and `_is_node_offline_relink` (superseded by
`_filter_residual_deps`, which tests the same condition inline and
deliberately more broadly — pnpm needs both `--offline` and
`--frozen-lockfile`, while `npm install --offline` and `yarn install
--frozen-lockfile` each stand alone, so the pnpm-only helper could not
replace it). That third one had a test pinning its *existence*, which only
guaranteed it stayed dead; retiring that pin is what let it go. Dead code
matters more here than in a normal repo because the stated design goal is
that the whole control flow reads top-to-bottom in one sitting, and an
unused helper reads as live — two of these three were removed gates, where
a leftover helper is an invitation to wire it back up.

Auditing that third one surfaced a real defect in the live path it had been
superseded by: `_filter_residual_deps` tested only for the *flags*, so
`pnpm add left-pad --frozen-lockfile` was kept as an "irreducible residual"
and re-run in every worktree — an `add` mutates the dependency set over the
network, which is the opposite of the offline relink the residual exists
for. It now also requires an install-shaped subcommand
(`_NODE_INSTALL_SUBCOMMANDS` = `install`/`i`/`ci`, deliberately excluding
`add`/`remove`/`up`/`dlx`) and matches flags as `shlex` tokens rather than
substrings, so `--offline` inside a package name no longer counts. The OR
between the two flags is unchanged and is load-bearing — requiring both
would drop the npm and yarn forms, which
`tests/test_capture_deps.py::test_keeps_node_offline_relink_only` pins.

Every `claude_p` call site in the module is statically checked against the
real signature by `tests/test_claude_p_call_sites.py` — all-keyword (no
positionals), every required parameter present, no unknown keyword, and
`model=` never a defaultless `<dict>.get(k)` (which yields `None` for any
worker absent from `MODEL_DEFAULT_PER_WORKER` — i.e. most of them, since a
new worker is *required* to be absent and fall through to `MODEL_DEFAULT`).
It exists because 0.10.0 shipped `phase_planning_coverage_gate` calling
`claude_p` with two positionals plus a duplicate `system_prompt=`, and
omitting the required `allowed_tools`/`max_turns`: it raised `TypeError` on
**every** invocation, and the gate's own broad `except Exception` logged it
as a clean advisory degrade. The judge never ran once for a whole release,
and the log line read like a healthy degrade path. **No stub-based test can
catch this class** — every test in the suite stubs `claude_p`, and a stub
accepts any signature — which is exactly why the guard is a static AST sweep
over the whole module rather than a behavioral pin on one call site. The
gate's own behavioral counterpart lives in
`tests/test_phase_planning_coverage_gate.py::TestCallSignature` (a recording
stub captures the real kwargs, then `inspect.signature(leerie.claude_p).bind(...)`
binds them against the live signature — generalizing
`test_recursive_decompose.py`'s C0 guard) paired with
`TestProgrammingErrorsPropagate`, which pins that the gate catches
`WorkerError`, `OSError` and `subprocess.TimeoutExpired` **only**: a worker failure, or a failure to spawn
the process at all, is an expected advisory degrade (the gate's docstring
promises it never terminates a run), while a `TypeError` is a leerie bug and
must propagate rather than masquerade as one. `OSError` is disjoint from every
programming-error class, so admitting it re-opens nothing —
`TestInfrastructureFailureDegrades` pins both halves. `TestBudgetIsCharged` pins the `st.bump_workers(caps)` this call was
missing (IMPLEMENTATION.md §8 requires it, and `integration_judge` — named in
that same sentence — already did it), including that the bump sits OUTSIDE the
`try` so budget exhaustion aborts instead of degrading.
Both files carry anti-vacuity controls — the static scan asserts it found the
call sites at all (a scan that finds nothing passes every assertion), and the
behavioral file pins that narrowing the `except` did not make an advisory
gate fatal. All were falsified live against each defect reintroduced
individually.

**Judgment-worker isolation** (DESIGN §12) is covered by four files, and the
thing worth remembering is that the design was settled by *experiment*, not by
reading the CLI's docs. `tests/test_judgment_worker_isolation.py` pins the
four layers: judgment workers never receive `--dangerously-skip-permissions`
(the load-bearing one), `claude_p` refuses any of them whose cwd resolves to
`st.repo_root`, and the flag instead widens their allowlist with the repo's
build verbs. Probed live against claude 2.1.237, filesystem-verified — with
the flag set, a worker holding only `INSPECT_TOOLS` used `Write` (absent from
that allowlist) to create a file outside its cwd, and in the exact shape this
feature ships (cwd = a detached worktree, flag still on) overwrote the real
checkout and committed on its branch. **A worktree is not a boundary while
that flag is set**, which is why the isolation tests pin the flag and the cwd
together rather than either alone. Two traps: the widening is scoped to
`INSPECT_TOOLS` because `SATISFIED_PROBE_TOOLS` is deliberately narrower and
*calibrated* (12/12 false positives with full latitude, 0 when scoped), and an
earlier revision handed that probe `Bash(pytest:*)`; and `_blt_verbs`
memoizes, because `resolve_blt` logs, so an unmemoized call per judgment
worker is dozens of identical lines — its `_BLT_VERBS_CACHE` is module-level
against a session-scoped `leerie` fixture, so the file clears it in an autouse
fixture for the reason `_active_admissions` does.
`tests/test_work_sentinel.py` covers the mechanical half — snapshot the real
checkout's HEAD/porcelain/refs before phase 1, re-check after every planning
phase — including the trap that a *failed* after-snapshot returns empty
strings that a naive diff reads as "HEAD moved" plus "every branch deleted",
fabricating tampering on a healthy run; hence the `ok` field, and an
anti-vacuity partner proving the underlying diff really would have fired.
`tests/test_planning_worktree_script.py` drives the real script against real
repos: detached, no branch created (the reapers know only `leerie/runs/*` and
`leerie/subtasks/*`, so a fourth namespace would leak forever), reset on
re-entry, and `clean -fd` **not** `-fdx` so `node_modules` survives.
`tests/test_ensure_planning_worktree.py` covers the Python wrapper — path
parsing, fail-closed on a script error, and the staging of what
`git worktree add` cannot carry (untracked task-reference files, an untracked
`.claude/`). It is the ONLY file that opts out of the conftest stub via
`@pytest.mark.real_planning_worktree`, so every test in it `chdir`s into a
throwaway repo AND sets `LEERIE_STATE_DIR`; both halves are load-bearing, and
`test_no_worktree_leaks_into_this_repo` is the standing proof. Its subtlest
pin is `test_staging_runs_after_the_reset` — staging before the script means
`git clean -fd` deletes it, which presence-only assertions cannot see.
All three guards were falsified live — restoring the `autonomous or …` OR
fails 2 tests, neutering `_diff_repo_state` fails exactly the 4 detection
tests, and moving the staging call above the script fails 3.
A related discipline note: `_judgment_cwd` falls back to the conventional
run-dir path rather than raising when `planning_worktree` is absent. That is
deliberate and costs nothing — the fallback is derived from `run_dir`, so it
can never *be* the checkout, and `claude_p`'s guard is the actual enforcement.
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
nowhere under `tests/`, and no test executes `_orchestrate` either, so the
branch that every real run takes was never run. Count the callers through the
shared harness, not by grepping for the call: only
`test_resume_planning_reentry.py` and `test_resume_planning_regression.py`
contained one, but `test_checkpoint_aliasing.py` and
`test_wiring_gate_resume.py` executed it too, via the `_drive` they import from
the former — four files before `test_run_phases_fresh_init.py` existed, and one
`resume` value across all of them. Note
`test_wiring_gate_resume.py::test_fresh_run_invokes_the_gate` reuses that same
`_args()`: "fresh" there means fresh *state*, not a fresh run, which is how
the gap reads as covered. Second, the guard that did exist —
`test_orchestrator_owns_blt.py::test_subtask_tests_is_seeded_on_both_run_init_branches`
— is a key-presence AST walk, and **it passed against the broken code**: the
key was in the dict literal, only its value expression was unevaluatable.
Presence is not evaluation: a walk that checks a key exists says nothing about
whether that key's value resolves, which takes either execution or scope
resolution (the symtable scan below does the latter statically).

**The general rule, of which that is one instance: a test asserting STRUCTURE
must be paired with one asserting SUBSTANCE.** Structure is a dict key, a source
substring, an AST node, a phrase in a prompt. Substance is the value that flows
through it, the result of executing it, or the order it appears in. Structure-only
assertions are necessary and never sufficient, and the gap is invisible because
they pass. Four measured instances, all from one change (2026-08-17):

| structural assertion | what passed it |
|---|---|
| the reconciler payload has key `scope_note` | `"scope_note": ""` — key shipped, planner's text discarded |
| `phase_plan` calls `_effective_source_of_truth` | ctx reads the preference directly, or omits the key entirely |
| the abort message contains every remediation phrase | the fallback hoisted back to lead — the wording the A/B measured as misrouting 5/5 operators |
| `die(_unresolvable_die_message(...))` exists in source | the gate reads `out.get("unresolved")`, never fires, and **140 tests stay green** |

The cheapest discriminating test per shape: **execute the consumer** (not read
its source); **assert the value** (not the key); **assert the order** (not the
presence). Where the subject is prose, none of those reach semantic inversion —
a phrase can be present and negated — so the guard there is a behavioural probe,
not another substring (`tests/manual/planner_fence_probe.py` is the worked
example). And when parametrizing a value test, make the inputs **disagree**: a
row where two sources of a value are equal cannot tell a correct read from a
bypass. The new file
executes the branch, stopping at a sentinel on
`_enforce_and_record_cgroup_containment` (the first call after the seed's
`st.save()`, so no other stub is needed), and carries the guard-the-guard test
that the resume branch would `die()` here — without it the file could silently
drift onto the path it exists to avoid.
`tests/test_no_undefined_names.py` is the whole-module generalisation: stdlib
`symtable` over `orchestrator/`, `chain/`, `scripts/` and `tests/`, flagging
any name that is referenced, resolves to global scope, and is bound in neither
module scope nor `builtins` — ruff's F821 rule without the dependency, since
pytest is the sole dev dependency here. Two traps are pinned by its own
parametrized false-positive table, both of which a naive scan gets wrong: a
`global X` + assignment **inside a function** binds the module name even when
`X` appears at module scope nowhere else, so the collector needs a pre-pass
over every scope; and `__file__`/`__name__` are interpreter-injected, never
assigned in source. That pre-pass is **provably inert on this tree** — the scan
returns `[]` with and without it — because every global leerie.py mutates under
`global` is also bound at module scope (`_last_parse_error`, `_STRICT_PROXY`,
both annotated assignments); it is kept because the rule must be right, and its
own parametrized case is the only thing that fails without it. An earlier
revision of this paragraph and of the file's own comment cited those two
symbols as *evidence* the pre-pass was load-bearing, which is exactly
backwards, and neither was re-derived before being written down. The
positive control beside that table is mandatory — a scan returning `[]`
unconditionally passes every negative case. Anti-vacuity is a canary injected
into the **real** module rather than a synthetic snippet, so a refactor that
quietly stops analysing `leerie.py` fails.

**A test that source-slices one function cannot observe a property it asserts
repo-wide, and that is how a containment bug shipped.** `tests/test_strict_mcp_config.py`
opened *"unconditionally for every worker"* while its `_claude_p_body()` helper sliced
`claude_p`'s source — so it was structurally incapable of seeing `preflight`'s smoke
test, which hand-rolled its own argv and ran with **78 tools / 4 MCP servers**, 46 of
them `mcp__claude_ai_*` (`send_message`, `trash_thread`, `slack_send_message`), plus
every tool the deny list exists to remove. The file passed throughout. Its replacement,
`tests/test_claude_argv_containment.py`, derives the site list (AST over
`orchestrator/leerie.py`, text over `scripts/**/*.sh`) instead of slicing one function —
the same enumeration-to-derivation move PRs #180-#183 record. Two lessons compound: a
scan that finds nothing certifies everything, so it carries minimum-count and
known-member anti-vacuity checks plus a planted-reproduction guard; and its first
shared-owner test was itself **vacuous**, asserting a flag was ABSENT after crippling the
builder — which the pre-fix argv also satisfied, because missing that flag *was* the bug.
A negative assertion the defect already satisfies proves nothing; the positive control
(flag present on both argvs with the builder intact) is the load-bearing half.

**A name-keyed AST taint walk needs scope isolation or it swallows the module.**
`tests/test_turn_cap_signal.py`'s `_aliases` propagates taint by variable NAME with no
notion of scope, and was run over the whole module. One new assignment —
`cmd = _contained_claude_argv(..., max_turns=max_turns, ...)` — tainted the name `cmd`,
which is assigned in dozens of unrelated functions, and within the four fixpoint rounds
the cap set grew from a handful to **1201 names**: every name in the module. The scan
then reported `seconds < 0`, `min_age is None` and `found < MIN_CLAUDE_CLI` as
turn-ratio comparisons and failed a correct tree. It now analyses one top-level function
(with its nested defs) at a time; measured largest per-scope set is 2. Note the guard for
this cannot be a synthetic snippet — a minimal two-function reproduction does **not**
cascade, so it passes under both implementations; the discriminating assertion is a
property of the real module (no unrelated name tainted, per-scope set bounded).

**An under-specified fixture hides a producer-side contract.** Two files passed
`models={}` to `_run_phases`, which was invisible until `preflight` began reading
`models["classifier"]` and they raised `KeyError` before reaching the behaviour under
test. The fix is a real dict (derived from `WORKER_TYPES`, or a `defaultdict`), not a
`.get()` in production code — coercing there would have swallowed the contract violation
in exactly the way this file warns about elsewhere.

No coverage
target is set — the suite was introduced from scratch and a number
now would be arbitrary.
`tests/test_launcher_integrity.py` is the **only** thing that checks the
`leerie` launcher parses. CI does not: `shellcheck.yml` lints `scripts/*.sh`
and `scripts/remote/*.sh`, and the launcher has no `.sh` extension nor lives in
either, while `syntax.yml`
AST-parses Python only. No test runs shellcheck at all — every occurrence of
the word under `tests/` is prose describing this gap. So a `bash -n`-level
syntax error in a 7k-line launcher would otherwise ship green.
That check first appeared inside `test_leerie_commit.py`, a file about one
state field, where it was coverage by accident: restructuring that file would
have removed the launcher's only validation silently. It is now named and
owned, with a derived guard (`_files_checking_launcher_syntax`) asserting some
file still runs `bash -n` against the launcher — scanning `tests/` rather than
naming itself, so moving the check again is fine and deleting it is not. The
scan is **structural**: it walks each test file's AST for a `run(...)` call
whose argv list literal contains both `bash` and `-n` *and* references the
launcher, matching what this repo reaches for when the shape of a call is the
assertion (`test_state_fields`'s write sweep, `test_claude_p_call_sites`, the
`args.resume` branch walk). A text scan for those facts appearing *anywhere in
the same file* is not equivalent and was the first version: co-occurrence is
not connection, and `container-entry.sh` is `bash -n`-checked in
`test_container_entry_run_id.py`. Falsified — with the real check gutted and a
decoy file that runs `bash -n` on something else while mentioning `LAUNCHER`,
the text predicate matched both files and passed while coverage was gone; the
AST predicate fires. An anti-vacuity test requires the scan to find its own
file, so a broken scan fails as a broken scan. The parse suppresses warnings:
reading every test file surfaces other files' `SyntaxWarning`s, at least one
deliberate and documented as not-to-be-fixed (`test_ec2_seed_repo.py`'s `\/`).
**Known shortfall, deliberately not papered over:** `bash -n` does not catch
the backtick class — a balanced pair inside what reads as a comment is parsed
as command substitution, silently dropping that text from the script sent to a
remote machine. leerie has shipped that defect once; it was caught by diffing
`shellcheck -x leerie`, with `bash -n` clean throughout. Linting the whole
launcher with shellcheck is the real fix and needs a measured baseline of
pre-existing findings first.
`tests/launcher_blocks.py` is the **single** derivation of the launcher's
orchestrator launch blocks — the `child_env = dict(os.environ)` regions inside
each unquoted `<<PY` heredoc, one per remote runtime. It owns three constants
that would otherwise be replicated per consumer: the block marker, the `\nPY\n`
terminator, and the preamble window searched for the `--runtime` label. Both
`test_leerie_commit.py` (LEERIE_COMMIT forwarding) and
`test_bedrock_bearer_token.py` (stray-`${...}` and backtick scans) import
`launch_env_blocks()` from it — package-qualified as
`from tests.launcher_blocks import ...`, the same form every shared test module
here uses (`tests.ec2_stub`, `tests.conftest`), with no `sys.path` juggling:
`tests/__init__.py` exists, so `tests` is a real package. A neutral module
rather than a cross-test import
because the two consumers are unrelated concerns and neither should own it
(`tests/ec2_stub.py` is the precedent for the shape). It reads the launcher
itself rather than taking the source as an argument, so callers holding a `str`
or a `Path` are equally served.
**Why a shared module and not two local copies**: PRs #180–#183 each replaced a
hard-coded enumeration with a derivation after a missed instance shipped —
`ContextOverflow` in 1 of 9 capture guards, `leerie_commit` in 1 of 2
state-init branches, then 1 of 2 launch blocks (that last one caught by a
reviewer, not the suite). The derivation was then written twice, once per
consumer. Two copies of a *rule* drift exactly the way two copies of a *list*
do. `tests/test_no_duplicate_launcher_blocks.py` is the general form of that
discipline for **bash** blocks: a table of eight start-of-line markers
(`_resolve_ec2_knob`, `_state_dir_default`, `_resolve_seed_knob`,
`ensure_image`, `resolve_repo_image_tag`, the `config)` case arm, the
`# --- runtime-mode knob ---` block, and the `_run_argv=(` array), each
asserted to appear exactly once in the launcher and never at the start of a
line in any test file (two of the eight — the `config)` arm and the
`_run_argv=(` array — are matched at their own two-space indent). It exists because converting N13's five named files fixed those
instances but not the rule: **three more reproductions were found afterwards
outside that list, and two had already drifted** —
`tests/test_launcher_state_mount.py` reproduced the `nerdctl run` argv
missing `--cidfile`, `--cgroupns=host`, `ROOTLESS_SECOPT`, the `LEERIE_*`
auto-forward and `${REPO_IMAGE_TAG:-$IMAGE_TAG}`, and
`tests/test_launcher_runtime_knob.py` omitted `_RUNTIME_EXPLICIT` entirely —
a flag set at six sites and read by the resume auto-detect
(`leerie:4127`, `:4152`) that had **zero coverage suite-wide**; deleting
every assignment left the whole suite green, and now fails five tests. None
of those copies produced a *wrong* answer, which is the point: they were
blind, and would have passed identically if the launcher deleted the
behaviour under test. The `config)` row needs an explicit marker rather than
a derived `name() {` shape, and its anti-vacuity controls are ec2_knob's pair
(the launcher really defines each marker; consumers extract rather than
reproduce) plus one that file does NOT have —
`test_the_scan_can_find_a_reproduction`, which plants a copy and proves the
scan fires on it while still ignoring a legitimately quoted reference. A
scan that matched nothing would otherwise certify "no duplicates" forever.

`tests/test_no_duplicate_launcher_splitters.py` enforces the single owner
and carries two anti-vacuity controls, since a scan that matches nothing would
certify 'no duplicates' forever: the marker must be found *inside* the owner,
and at least two other files must actually import `launch_env_blocks`. The
load-bearing falsification is breaking the shared splitter and confirming
guards in **both** consuming files fail — that is what proves they share it
rather than merely importing it.
`tests/test_leerie_commit.py` pins the `leerie_commit` state field, which
disambiguates `leerie_version`. `plugin.json` only moves on a `chore(release):`
commit while `install.sh` tracks `main` (`DEFAULT_REF`), so every run between
releases records the same version whether or not it carries a given fix — a bug
report citing `v0.11.1` cannot be placed on either side of it. The launcher
computes the short sha (`git -C "$LEERIE_REPO" rev-parse --short HEAD`) and
forwards it as `LEERIE_COMMIT`; the orchestrator records it beside the version.
Pinned: the key is in `STATE_FIELDS`, **adjacent to `leerie_version`** (the two
are only useful together, and adjacency is what stops one moving without the
other) and carries an IMPLEMENTATION.md §8 row; the write reads the env var and
uses `or None` — **load-bearing**, since an empty `LEERIE_COMMIT` arrives
whenever leerie was installed from a tarball or an older launcher runs against a
newer orchestrator, and recording `""` would render as a real-but-blank sha in
the PR footer; a real `State.save()` round-trip preserves both a sha and a
`null`; the rendered suffix appends `(sha)` only when both parts are present, so
an absent commit yields no empty parens. Launcher-side: the `git` call carries
`2>/dev/null || true` so a non-checkout install cannot abort under `set -e`
(absence is a normal state, never an error), it is forwarded explicitly with
`-e` rather than via the `LEERIE_*` auto-forward (it is launcher-computed, not a
user knob, and the auto-forward only carries exported vars), and it is **not**
on the forwarding denylist — unlike `LEERIE_VERSION`, which is host-only for the
image tag.
**Two traps this file exists to pin, both of which shipped broken once.** (1)
`_run_phases` initialises state in *two* branches — `if args.resume:` uses
subscript assignments, the fresh-run `else:` a dict literal — so the key must be
written in both. The original test compared `src.index()` of two strings that
both live in the resume branch, so it passed while the field was absent from
every fresh run, i.e. the common case and the whole point of the field. The
replacement walks `_run_phases`'s AST, locates the `args.resume` `If` node, and
requires the key in `body` **and** `orelse`, with an anti-vacuity control
asserting the same walk finds `leerie_version` (known to be in both, and
deliberately excluded from the parametrised list below) so a broken walk fails
as a broken walk rather than a missing key. **The enforcement for this seam is
not here** — it is `tests/test_state_fields.py::test_no_resume_only_state_keys`,
which *derives* the rule (`resume_keys - fresh_keys == set()`, walked over the
`if args.resume:` node) for every key, with **no list to maintain and no
allowlist**: the reverse direction is deliberately unasserted, since `task`,
`started_at` and `worker_count` are legitimately fresh-only. That walk
(`_state_init_branch_keys`) has **one owner**, imported by its consumers and
enforced by `tests/test_no_duplicate_state_walks.py` — the same single-owner
discipline `tests/launcher_blocks.py` carries, and for a sharper reason: a
drifted second copy under-reports `resume_keys`, which makes the symmetry guard
pass **vacuously** rather than fail. Two traps are pinned inside the walk
itself: it matches `ast.unparse(n.test) == "args.resume"` **exactly** and
asserts exactly one node (a substring match also catches the later
`if not args.resume:` guard, and `ast.walk` is breadth-first rather than source
order, so `nodes[0]` was selecting the right branch by luck), and it **raises**
on an `st.data.update()` / `setdefault()` / augmented write inside either arm
instead of silently not collecting it. `_BOTH_BRANCH_KEYS`
in `test_leerie_commit.py` is a frozen set of *named pins* for the three fields
that have actually shipped broken on this seam, kept for the same reason the
resumable-planning keys keep theirs — a generic sweep fails with a diff, a named
pin fails naming the field. A new field does not go in it. **This is the fourth
time an enumeration here was replaced by a derivation after a missed instance
shipped** (PRs #180–#183 are the others): the first fix parametrised the walk
over a two-entry tuple, and the derived rule immediately found a third defect
the tuple could not have caught — `skip_coverage_check`, seeded only under
`if args.resume:` since PR #162 (*"add --skip-coverage-check, the escape hatch
this gate lacked"*). That one was **behavioural, not attribution**:
`phase_planning_coverage_gate` reads it straight off `st.data`, so `.get()`
returned `None` and the flag was silently inert on every fresh run — while
`tests/test_phase_planning_coverage_gate.py`'s `TestSkipCoverageCheck` reported
full coverage, because every test in it sets the key **by hand** and so pins the
consumer while proving nothing about the producer. That file now carries a named
producer pin importing the shared walk. By contrast
`dangerously_force_strict_output` (M7) is a **record only** — the flag's
behaviour comes from `caps["force_strict_output"]` in `_orchestrate`, ahead of
the split and independent of `st.data`, so both paths always honoured it and
what was lost was attribution. (2) The local `-e`
forward covers only `--runtime local`, and there are **two** further launch
blocks — Fly and EC2 each build their own `child_env = dict(os.environ)` inside
their own unquoted `<<PY` heredoc. Both must forward the value, JSON-encoded
(`_leerie_commit_json` / `_ec2_leerie_commit_json`) like every other
substitution there; the Fly name additionally goes in the `${...}` allowlist in
`tests/test_bedrock_bearer_token.py` or its stray-substitution scan fails
(verified live: it does).
**Both heredoc scans are themselves derived**, over every launch block rather
than the one Fly slice they originally hard-coded: `_launch_env_blocks()` in
`tests/test_bedrock_bearer_token.py` feeds both the stray-`${...}` allowlist
scan and the backtick scan, each with a per-runtime allowlist
(`_KNOWN_HEREDOC_SUBSTITUTIONS`). Before that, the scans covered a 31-line
`TZ`→`AWS_REGION` slice of the Fly body and were **structurally blind** to the
EC2 heredoc — an unquoted `<<PY` with identical failure modes: an unbound
`${VAR}` anywhere in the body, comments included, aborts the launcher under
`set -euo pipefail`, and a balanced backtick pair is read as command
substitution, silently dropping that text from the script sent to the machine
(`bash -n` does not catch it; `shellcheck -x` does). Falsified in both
directions: injecting either defect into the EC2 body now fails naming `ec2`,
and the old slice provably did not contain it.
**The guard is derived, not enumerated**: `_child_env_blocks()` finds every
`child_env = dict(os.environ)` in the launcher and requires each to forward the
var, so a third runtime fails automatically. This exists because the
hard-coded version shipped covering Fly only while being *named*
`..._fly_ec2_path_too` — EC2 recorded null and a reviewer caught it, not the
suite. Two anti-vacuity controls are mandatory alongside it, since a splitter
that finds nothing passes everything: at least two blocks must be found, and
every block must also set `USER_REPO` (known present in both), so a broken
slice fails as a broken slice rather than as a missing key.
The `--dangerously-force-strict-output` context-window regression (DESIGN §7
*Forcing constrained decoding*, §6 *A client-side context refusal*) is covered
by three files. The defect: the flag works by owning `ANTHROPIC_BASE_URL`, and
the CLI treats any custom base URL as an **LLM gateway** — behind which it can
no longer confirm the answering model and falls back to a conservative
client-side context ceiling instead of Sonnet 5's native 1M. It then refuses
prompts *itself* at ~224K, emitting a synthetic assistant message
(`model=<synthetic>`, usage all zeros) with **no API call**.
`tests/test_strict_proxy_context_window.py` pins `_model_arg`: `sonnet`/`opus`
gain the `[1m]` suffix only while `_STRICT_PROXY` is active, `haiku` never does
(it has no 1M variant and the CLI rejects the suffix), a full model id passes
through untouched, the suffix is not doubled, `_ONE_M_CONTEXT_MODELS` is a
subset of `MODEL_VALUES` (a typo there would silently disable the fix rather
than fail), and — the wiring pin — `claude_p`'s source builds `--model` via
`_model_arg(model)` and no longer contains a bare `"--model", model,`. The
suffix is applied automatically and is deliberately **not** admitted to
`MODEL_VALUES`: it is inert whenever the proxy is off, so an operator gains
nothing by setting it by hand and could set it on `haiku`, where it breaks.
`tests/test_context_overflow_classifier.py` pins `_is_context_overflow` and
`ContextOverflow` against verbatim `result` envelopes from that probe. Both
signals are required — `terminal_reason == "blocking_limit"` **and** the result
text — because the reason alone is shared (sibling arms ended `max_turns`, a
healthy run `completed`) and the text alone can appear in a worker's own
correct output; `subtype` is a misleading `"success"` and must never be keyed
on, the same trap `_is_transient_transport_failure` documents. Gated on
`is_error` and exempting `_leerie_synthetic`, mirroring
`_is_terminal_auth_failure`, plus disjointness pins against both auth
classifiers. `ContextOverflow` subclasses `BaseException` and explicitly **not**
`WorkerError` — `_run_checked_loop` retries WorkerError across its whole round
budget, which for a deterministic client-side refusal is pure waste — and
source-coupling guards require `claude_p` to raise before the generic
two-attempt failure and `main()` to route it to a resumable `EXIT_LOCKED` pause
whose message never says "schema". That message is the point: unclassified,
this surfaced as *"worker failed schema-valid output twice: Prompt is too
long,"* which cost three successive misdiagnoses on 2026-08-06. When extracting
the handler arm in a test, split on `"\n    except "` (a top-level handler), not
a bare `"except "` — the latter truncates at the inner `except Exception:`
guarding the cleanup call and hides the `exit_code` assignment after it.
`tests/test_task_file_globbing.py` covers the independent `_glob_task_references`
defect the same incident surfaced but which did **not** cause it (the failing
run's very first request was already over the ceiling, before the planner read
anything): markdown emphasis is stripped before glob classification, since `*`
is a `_GLOB_CHARS` member and `glob("*")` matches every file in the repo root —
measured, that handed the planner 18 files / 1.86 MB as required reading,
including `LICENSE`, `.claude.json` and a prior run's 847 KB log. Pinned: prose
(`*`, `**`, `**Root**`, `_em_`, backticks) resolves nothing; genuine references
(`spec.md`, `tests/*.py`, `docs/DESIGN.md`, `spec.{md,txt}`, and a path wrapped
in bold) still resolve; absolute paths and `../` traversal resolve nothing —
admitting separator-bearing tokens without that guard reached **outside the
repo** (`repo_root / "/bin/sh"` discards the root, so a task mentioning
`/bin/bash` matched a 1.4 MB binary), which is why containment is re-checked
against `repo_root.resolve()` independently of the token-level test; and a task
file never lists itself, while a same-named file with *different* contents
still does. Falsification is recorded: replaying the pre-fix token filter
against the prose-only fixture matches 4 files where the test expects none.
The two read-mostly verbs that still assumed a two-runtime world —
`accept-blocked` (validated `--runtime` against only `fly`/`local` and
defaulted anything non-fly to `local`, silently mislabeling an EC2 run)
and `list` (keyed its runtime-aware view on `fly-machine.json`/
`LEERIE_FLY_APP`, so an EC2 run rendered empty columns) — are pinned in
`tests/test_ec2_launcher_readonly_verbs.py`. `accept-blocked` now
auto-detects EC2 the same way `stop` already does
(`_auto_detect_run_runtime`), accepts an explicit `--runtime ec2` (with
a control that a genuinely bogus value is still rejected), and —
mirroring the Fly path's wake-mutate-pause dance — wakes a stopped
instance, mutates state.json over SSM (`ec2_remote_exec`), mirrors the
mutation onto the host copy if one exists, and re-pauses the instance
only if this verb woke it (plus an already-running control that proves
no pause fires when the instance was already up), and fails closed on a
missing `ec2_instance_id`. The `accept-blocked` tests invoke the real
`leerie` launcher binary against a stubbed `aws` that composes
`tests/ec2_stub.py`'s stateful EC2 instance tracking with an `ssm
start-session` handler that decodes `ec2_remote_exec`'s base64-wrapped
command and executes it with the invoking process's stdin drained
through — the same mechanism the launcher's EC2 branch relies on to
pipe the multi-line state-mutation Python program to the remote
`python3 -`. `_collect_run_rows`/`_list_runs` in `orchestrator/leerie.py`
now track an `is_ec2` axis (`ec2_instance_id` in `run.json` or
`ec2-instance.json` present) alongside the existing `is_fly`, so
`list --runtime ec2` filters correctly, `list --runtime local`
excludes both Fly and EC2 runs, a plain `list` renders an EC2 run's
status column without requiring `LEERIE_FLY_APP`, and an EC2 run is
still detected via the `ec2-instance.json` sidecar alone when
`run.json` doesn't exist yet. These `list` tests exercise
`_list_runs()` directly (no launcher subprocess, no AWS stub), mirroring
`tests/test_list_runs.py`'s pattern.

`resume` routing a paused EC2 run through `resume_instance()` — the
launcher-level seam distinct from `resume_instance()`'s own standalone
coverage in `tests/test_ec2_resume_instance.py` — is pinned in
`tests/test_ec2_launcher_resume.py`, reusing
`tests/test_ec2_e2e_provision.py`'s `extract_ec2_dispatch_block`/
`run_ec2_dispatch`/`stub_aws_env` harness and `tests/ec2_stub.py`'s
resource-tracking `aws` stub (mirroring
`tests/test_ec2_launcher_dispatch_e2e.py`'s import convention), since
`resume` for EC2 lives inside the deep `RUNTIME=ec2` elif dispatch
block rather than the early fast-path verb dispatch `stop` uses. It
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
rather than hand-copied, so `leerie`'s synthesized shape and
`seed-auth.sh`'s fallback can't silently diverge — same discipline as
`test_no_result_event_retry.py`), Keychain still wins over
the file when the env var is unset (the pre-inversion fallback order is
unchanged), and no credential anywhere yields a clean rc 1.
`test_credential_precedence.py` additionally pins the shape-validation
gate that rejects a syntactically valid but semantically empty
Keychain/file blob (the documented upstream Claude Code bug,
steipete/CodexBar#1844, where a background MCP-plugin OAuth flow
overwrites the shared `Claude Code-credentials` Keychain item with only
`{"mcpOAuth": {...}}`, dropping `claudeAiOauth` entirely — see DESIGN §6
*Credential strategy*): an mcpOAuth-only Keychain blob is rejected and
falls through to a present on-disk file; the same mcpOAuth-only
Keychain blob with no file fallback available yields rc 1 rather than a
false-positive success; the identical mcpOAuth-only shape is rejected on
the on-disk-file branch too; a blob with `claudeAiOauth` present but an
empty `accessToken` is rejected (the check inspects the actual token
value, not just key presence); and a positive control confirms a blob
carrying both `mcpOAuth` and a real `claudeAiOauth.accessToken` (the
healthy shape Claude Code should normally produce) is still accepted —
the gate rejects on absence of a usable token, not on the mere presence
of an `mcpOAuth` sibling key. The extraction harness in
`test_chain_credential_transport.py`'s `_invoke_helper` was widened to
also pull the new `_claude_creds_has_oauth_token` helper out of the
launcher alongside `_extract_claude_credentials_json` (the two must
travel together when sourced for tests, since the latter calls the
former). The synthesized blob's mandatory `scopes:["user:inference"]` field (CLI
2.1.210's file-auth path rejects a scope-less
`{claudeAiOauth.accessToken}` blob as "Not logged in · Please run
/login" — measured by field-ablation against the real image;
`refreshToken`/`expiresAt` are not required, only this scope) is pinned
at all three synthesized sites — `leerie` by executing the awk-extracted
helper (`_invoke_helper`), `seed-auth.sh` / `ec2-seed-auth.sh` by
regex-extracting their printf format strings — and asserted
byte-identical across the three; and the always-forward
`-e CLAUDE_CODE_OAUTH_TOKEN` container injection (the env-var auth path
is permissive and long-lived, so it survives a headless run past a
copied file blob's `expiresAt` — a copied blob cannot refresh,
anthropics/claude-code#21765) is source-coupled to sit *before* the
credential-resolve `if`/`else`, not in the resolve-failure `else` arm
where it was previously unreachable when the token staged into the file.
The paired
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
The Bedrock bearer-token auth path (`AWS_BEARER_TOKEN_BEDROCK` — the
static-credential analogue of `CLAUDE_CODE_OAUTH_TOKEN` for Bedrock,
DESIGN §6 *Credential strategy*: preferred over the pre-existing
settings.json-driven SSO/profile Bedrock path since a container cannot
refresh a short-lived SSO token any more than it can refresh a subscription
OAuth session) is tested in `tests/test_bedrock_bearer_token.py`: the
bearer token is forwarded verbatim alongside a `CLAUDE_CODE_USE_BEDROCK`
default of `1` (confirmed live against the real CLI that the token alone is
a no-op without this flag — the CLI falls through to firstParty/OAuth
dispatch otherwise) and an optional `AWS_REGION`; an explicit
`CLAUDE_CODE_USE_BEDROCK=0` override still wins over the default; the
bearer-token path never invokes `bedrock_preflight()`/`aws sts
get-caller-identity` and mounts no `~/.aws`, even when `aws` is present and
would fail; the bearer-token path wins when both it and a settings.json
`CLAUDE_CODE_USE_BEDROCK=1` are present (matching the real CLI's own
credential-resolution order — its Bedrock client construction
short-circuits SSO/profile resolution once `AWS_BEARER_TOKEN_BEDROCK` is
set); and the pre-existing SSO/profile path is unaffected when the bearer
token is absent (regression control). The Fly detached-launch heredoc gets
its own dedicated coverage for three defects found and fixed during
implementation, since the heredoc is unquoted (`<<PY`) and therefore
substitutes shell expansions inside what looks like inert Python comment
text: (1) a raw `"${AWS_BEARER_TOKEN_BEDROCK}"` string substitution let a
token containing `"`/`\` break out of the Python string literal and run as
arbitrary code on the remote Fly machine — fixed by JSON-encoding every
heredoc-substituted value host-side (mirroring the pre-existing
`_launch_argv_json` technique), pinned by
`test_fly_heredoc_values_are_json_encoded_not_raw` and three live
end-to-end tests (`test_malicious_token_with_quote_does_not_break_out_of_python_literal`,
`test_malicious_token_with_backslash_does_not_break_out`,
`test_normal_token_unaffected_by_json_encoding`) that extract the real
JSON-encoding lines and `child_env[...]` block verbatim from the launcher,
splice them into a harness, and actually pipe the result through
`python3 -`; (2) a first-draft fix comment containing the literal text
`${VAR}` crashed the entire launcher with `unbound variable` under
`set -u` on every Bedrock bearer-token Fly launch (worse than the injection
defect, since it fired unconditionally rather than only on a hostile
token) — pinned by
`test_child_env_heredoc_body_has_no_stray_unbound_var_substitution`, which
scans the real extracted heredoc body for any `${...}`-shaped token outside
an explicit allowlist of the known, intentional substitution names; (3) a
balanced backtick pair in a comment (`` `if <json>:` ``) was parsed by bash
as a command-substitution delimiter — a different expansion mechanism than
`${...}`, so unguarded by the previous fix — printing a spurious `syntax
error: unexpected end of file` to the user's terminal on every launch and
silently dropping that comment's text from the script sent to the remote
machine (caught by diffing `shellcheck -x leerie` against `git stash`,
since `bash -n` does not catch it) — pinned by
`test_child_env_heredoc_body_has_no_backtick_characters`. All three
regression tests were falsified live (reintroducing each exact defect and
confirming the corresponding test fails, then re-confirming it passes on
the fix) rather than trusted on inspection alone.
Since the existing Bedrock SSO/profile path (`detect_bedrock_mode()` /
`bedrock_preflight()`) shipped with zero test coverage before this work,
`tests/test_bedrock_mode.py` closes that gap: `detect_bedrock_mode()`'s
3-file merge and truthy-value matching (`1`/`true`/`yes`/`on`,
case-insensitive, OR semantics since the flag has no "disable" value, and
tolerance of a malformed settings file); and `bedrock_preflight()`'s three
outcomes (missing `aws` binary, a failing `aws sts get-caller-identity`
simulating an expired/missing SSO token — with and without an `AWS_PROFILE`
naming the profile in the recovery hint — and a valid SSO session). Both
files extract `detect_bedrock_mode()`/`bedrock_preflight()` verbatim from
the launcher via source-slicing (same discipline as
`test_launcher_env_forwarding.py`'s `_extract_forwarding_loop`) rather than
reproducing them by hand.
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
`resume`; `EXIT_LOCKED == 75`; `_is_terminal_auth_failure` is checked
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
and surface a `resume` hint; a control case pins that 401/429/529
envelopes whose auth/quota backoff budget exhausts still raise
`WorkerError`, not `TerminalAuthFailure` — the doc-conformant behavior per
`docs/IMPLEMENTATION.md` §3 "Auth/quota backoff" after commit `2652319`
reverted an over-application of the terminal-auth reroute to that
transient case; and a source-coupling check that `_is_terminal_auth_failure`
is consulted before `_is_auth_or_quota_failure` inside `claude_p`.
The 2026-07-19 incident (`argv-e2big-and-coverage-freeze`) — the
combined argv E2BIG crash (root cause B) and coverage-gate freeze
(root cause A) that motivated the stdin-transport and coverage-freeze
fixes above — has a dedicated end-to-end reproduction harness in
`tests/test_incident_2026_07_19.py`, backed by
`tests/fixtures/incident_2026_07_19/{shape.json,generate.py}`. The
fixtures are synthetic and shape-matched to the incident's measured
per-field byte distribution (task 51,142B, `subtask_views` 88,201B at
`indent=2`, 114 subtasks, a 15-item CLAUDE.md heading harvest split
into 3 uncoverable backtick+MUST convention items and 12 other
headings) — the real internal-audit task file is deliberately not
committed. `generate.py` rebuilds the reconciler payload shape
(`build_task`/`build_subtask_views`/`build_user_prompt`) and the
CLAUDE.md-shaped heading text (`build_claude_md_text`) from
`shape.json`; it is not itself a test module (`pytest.ini`'s
`python_files = test_*.py` never collects it) and is imported directly
via `importlib.util`. `TestRootCauseB_ArgvE2BIG` pins that the
generated ~150KB payload exceeds `MAX_ARG_STRLEN` (131,071B) as a
single string, and that `claude_p`'s real `build()` closure — driven
through a stubbed `_invoke`, no live `claude` binary required —
constructs no argv element over that ceiling for it, routes the user
prompt over stdin, and routes the appended system prompt through
`--append-system-prompt-file`. `TestRootCauseA_CoverageFreeze` pins
that the fixture still reproduces the incident's exact 15-item
harvest shape, and that the mechanism which froze on it —
`extract_task_file_structure`, `_is_uncoverable_convention_item`,
`check_task_file_coverage`, `_dedup_frozen_coverage_issues` — is
deleted rather than guarded. Coverage of a task's referenced files is
`task_coverage_judge`'s job; the freeze class cannot recur because
there is no substring gate left to freeze.
`TestBothRootCausesComposeOnOnePayload` runs both halves against the
same generated fixtures in one test, matching the incident note's
claim that the two fixes compose on one realistic payload.

**A handler must SURVIVE its own exception, not merely catch it.**
`main()`'s `except DiskLowSpace` arm opened with an unguarded `st.save()`
— and `State.save()`'s own out-of-space conversion is one of the three
raise sites, so on the disk-full path that call re-entered the failure and
raised again *from inside the handler*. A sibling `except` of the same
`try` does not see an exception raised in another arm's body, so it
escaped `main()`, skipping the cleanup, the dep capture and the
`EXIT_LOCKED` assignment: an exit-1 traceback where the whole arm exists
to produce a resumable pause. Three lessons worth keeping. First,
the arm's own comment already documented this hazard for the
`dep_capture` call ten lines below and guarded that one — the likelier
re-raiser went unguarded because the comment named only one of the raise
sites, which is why `test_survives_a_save_that_is_still_failing`
in `tests/test_disk_preflight.py` asserts against *every* save in the arm
rather than pinning one call. Second, **fixing one arm was not the fix**:
eight other handlers in `main()` carried the identical bare `st.save()`,
including the catch-all `except BaseException`, where a raise REPLACES the
unhandled exception and leaves the real bug reachable only as
`__context__`, which nothing prints. They all now route through
`_save_state_best_effort`, which logs and never raises — deliberately
broader than `except DiskLowSpace`, because a read-only run dir raises
`PermissionError` (measured) and no conversion touches it.
Third, the test it replaced asserted only
`issubclass(DiskLowSpace, BaseException)` and concluded "no separate
save()-specific handler is required" — a tautology that *reasoned* its
way to a false conclusion. Reaching a handler was never the question.

**A timeout is infrastructure, not a leerie bug.** `_invoke` converts
`asyncio.TimeoutError` into `subprocess.TimeoutExpired`, which is an
`Exception` but **not** a `WorkerError` — so every `_run_checked_loop`
caller took its `except Exception: … break` arm ("anything else is a bug
in leerie itself") and `die()`d instead of retrying, and five bare
`except WorkerError` sites let it escape to `main()`'s terminal handler
entirely. Both pre-date the per-worker timeout table; the table made them
~4x more reachable for the 18 worker types whose ceiling it lowered,
while the stated motivation was a hung `classifier`. The retry arm and
those five sites now name `subprocess.TimeoutExpired` alongside
`WorkerError`. **Never interpolate one into a message**: `str()` on a
`TimeoutExpired` renders `cmd`, i.e. the entire `claude -p` argv with an
inlined system prompt — the 50 KB terminal dump `_run_implementer`'s
handler documents. `_brief_worker_exc` exists for exactly this and names
`exc.timeout` instead; `tests/test_checked_loop.py` pins both the retry
and that the argv never reaches a warning line.

**Derivation guards are one-directional unless you write the converse.**
Both `TIMEOUT_DEFAULT_PER_WORKER` tests iterated the *table* ("every
shipped entry is reproducible"), so deleting five entries passed the whole
suite while those workers silently reverted to the 5400 s global.
`test_every_measured_worker_below_the_cap_is_IN_the_table` iterates the
measured summary instead. The same shape bit `main()`'s caps wiring: its
guard asserted the value line plus `"args.worker_timeout"` — which appears
*on* that value line — so deleting the explicitness assignment left it
green and restored the silent-no-op defect. The three minimal entrypoints
had both halves asserted; `main()`, the primary path, did not.

**Ablate a pattern against its corpus instead of adding alternatives.**
`_host_finalize_is_auth_or_network_push_error` carried 11 alternatives;
removing each in turn showed only three were load-bearing for the 9 real
git cases, and four of the rest (`could not resolve host`, `connection
refused|timed out`, `operation timed out`, `no route to host`) were pure
false-positive surface — unreachable for real git behind the
`^(fatal|remote):` anchor, which emits them on an unprefixed `ssh:` line
or inside an `unable to access '<url>':` line already matched. Dropping
them kept 9/9 and fixed three hook misclassifications. Separately,
`_host_finalize_ssh_transport_failure` was **provably dead**: its
companion condition was a line the first arm already matched, and the
first arm ran first. A second arm that cannot change an answer is worse
than no arm, because three places documented it as the discriminator.

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
