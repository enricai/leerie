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

For every CLI flag, environment variable, and `leerie.toml` key — model/effort
selection, runtime backends (local/fly/ec2), gate skip-flags, chain/group
verbs, seed/transport tuning — see `docs/IMPLEMENTATION.md` §2½
*Configuration reference*. It is the single source of truth; this section
covers only the muscle-memory verbs used every session.

One-time runtime setup (leerie runs in a container) and install steps are in
`docs/INSTALL.md`.

```bash
# Run on a task in the current git repo:
./leerie "Fix the login timeout bug and add a regression test"

# Resume after an interruption:
./leerie resume

# Enumerate in-flight and completed runs in this repo:
./leerie list

# Accept a blocked subtask so resume skips it (e.g., E2E tests that need
# external deps the container can't provide). --force settles a subtask
# abandoned mid-flight (in_progress, no `blocked` registry entry):
./leerie accept-blocked <run-id> <subtask-id> [--force]

# Accept an integration_judge behavioral finding so resume advances past it
# instead of re-reaching the same verdict forever:
./leerie accept-integration <run-id> <subtask-id>

# Reclaim disk. Nothing reaps run state automatically; dry-run by default.
./leerie prune                        # show what would go
./leerie prune --apply                # default cutoff is 14 days

# Generate/inspect .leerie/config.toml (host-only, no container):
./leerie config --init      # auto-detect BLT commands
./leerie config             # print effective config with provenance
./leerie config --chat      # interactive configuration session
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
