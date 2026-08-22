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

Full per-feature/per-incident test inventory lives in `docs/TESTING.md`; this
section is the operational rules plus the traps worth remembering, not the
inventory itself.

- **Never run two copies of the suite concurrently on one host.** Real
  `fork()`s, PID reaping, subreaper races, cgroup probes, and stalled-transport
  `timeout` paths make CPU starvation flake dozens of tests (measured
  2026-08-01: serial 0 failures across four runs; two suites overlapping gave
  78 and 57). `-n 4` (xdist) matches serial on an idle host — the hazard is
  concurrent *suites*, not parallelism. Treat any failure list gathered under
  concurrent load as unusable; re-run alone.
- **A local pass isn't evidence until the host lacks `claude`.** CI's
  `preflight()` → `_check_claude_cli_version()` fails with
  `FileNotFoundError: 'claude'` there (PR #211: green locally, seven red on
  CI). Re-run affected files with `claude`'s directory stripped from PATH
  (not a minimal `env -i` — tests need `git`/`jq`/coreutils):
  ```bash
  CLAUDE_DIR=$(dirname "$(command -v claude)")
  PATH=$(echo "$PATH" | tr ':' '\n' | grep -vFx "$CLAUDE_DIR" | paste -sd:) \
    pytest tests/<affected>.py
  ```
  A test whose subject *is* the gate should **stub** `_check_claude_cli_version`
  rather than skip (`tests/test_append_system_prompt_file.py` has the skip
  form, for when the CLI itself is the subject).
- **A test driving real `main()` needs a stub `claude` on PATH** — the gate is
  in `main()`'s `shutil.which("claude")`, ahead of `_check_claude_cli_version`,
  and `die()` always exits 1 regardless of intended code (14 of
  `tests/test_main_exception_arms.py` shipped red this way).
  `tests/conftest.py::fake_claude_on_path` is the single owner; use it, not a
  per-harness stub.
- **`main()` mutates the pytest process itself**: `_become_subreaper()` runs
  before argparse, so a test driving real `main()` leaves
  `prctl(PR_SET_CHILD_SUBREAPER, 1)` set for the rest of the session — an
  orphan reparents to pytest instead of PID 1, zombies, and `os.kill(pid, 0)`
  keeps reporting it alive (three `tests/test_signal_cleanup.py` assertions
  went red on CI purely from alphabetical collection order). Fix:
  `tests/conftest.py`'s autouse `_restore_child_subreaper`, delegating to the
  public `child_subreaper_restored` context manager — drive that manager
  directly in any restore-test (pytest 9's fixture wrapper has no
  `__pytest_wrapped__`), and pin the flag to 0 *before* asserting, or an
  earlier test's leftover 1 makes a broken fixture pass by coincidence.
- `shellcheck` runs in CI but isn't on every dev host, and catches things
  `bash -n` cannot (SC1007 on a bare `LANGUAGE=` prefix; see
  `tests/test_launcher_integrity.py`).
- **Do not edit `orchestrator/leerie.py` (or any file under test) while the
  suite is running.** Guards using `inspect.getsource`/`ast.parse` re-read via
  `linecache`, which notices mtime changes mid-run. A single-line docstring
  edit ~3 minutes into a run produced 38 spurious failures
  (`test_subreaper`, `test_wave_integration_instrumentation`, every
  `test_terminal_auth_routing` pin), all green against a frozen tree. Same
  tell as the concurrency hazard: a failure list dominated by
  source-coupling tests is unusable.
- `tests/test_warn_test_missing_producer_edge.py` pins the dominant root
  cause behind one incident batch — a `test-` subtask declaring no
  `requires`/`depends_on` edge to the feature subtask whose not-yet-created
  output it targets — via the `_warn_test_subtask_missing_producer_edge`
  advisory (mirrors `_warn_provider_subset_subtasks`); deliberately advisory,
  since no mechanical file-overlap signal reliably catches the real failure
  shapes (`phase_wiring_gate` remains the actual enforcer).
- **Host-only tests are gated on `jq`** (`HAS_JQ` in `tests/conftest.py`,
  mirroring `HAS_TREESITTER`) — host-owned bash
  (`scripts/host-finalize.sh`, `provision.sh`'s `decide_teardown`, launcher
  `finalize`/`no_push`) parses `run.json` with real `jq`, deliberately absent
  from the container image (in-container code uses python3 instead — see
  `scripts/remote/seed-auth.sh`). Do not "fix" a skip by adding `jq` to the
  Dockerfile: those scripts can never succeed in-container anyway (gh auth,
  ssh-agent, Keychain are host-side) per DESIGN §6 *Finalization*.
  `tests/test_jq_gate_wiring.py` guards every such module both imports
  `HAS_JQ` and carries its own `skipif` (a module-level `skipif` doesn't
  propagate through an import).
- **A conftest autouse fixture (`_no_real_planning_worktree`) stubs
  `_ensure_planning_worktree` for every test**, opt-out via
  `@pytest.mark.real_planning_worktree`. Absent it, a test driving real
  `_run_phases` shells a real `git worktree add` rooted at
  `resolve_leerie_root()` (`<repo>/.leerie` when `LEERIE_STATE_DIR` is unset),
  creating a full checkout of this repo inside itself — invisible because
  `.leerie/*` is gitignored and the dirs outlive the session (measured: 2
  worktrees, 25 MB, one CI-only red test with no visible link to the change).
  Assume the state root is inside the repo unless a test pins it elsewhere.
- `leerie resume <run-id>` (the documented positional form) is pinned by
  `tests/test_resume_positional_run_id.py`. It silently ignored the run-id on
  every runtime until 2026-08-05: `main()` popped only `argv[0]`, so the
  run-id bound to argparse's `task` positional instead, `--run-id` stayed
  `None`, and `resolve_run_id` auto-picked a different run — saved only by
  the run-directory flock. `_extract_resume_run_id()` now takes the
  positional **before** `parse_args` (order is the contract), scoped to
  `resume` only, `die()`ing when a positional and `--run-id` disagree.
- **The fresh-run branch of `_run_phases` had no execution coverage at all**
  until `tests/test_run_phases_fresh_init.py` — every test path exercised it
  with `resume=True`, so v0.20.0 shipped a `NameError` (`repo_root`) in the
  untested `resume=False` branch that killed every non-resume run. A
  key-presence AST walk passed against the broken code: presence of a dict
  key says nothing about whether its value expression evaluates.
- **A test asserting STRUCTURE must be paired with one asserting SUBSTANCE.**
  Four measured instances from one change (2026-08-17):

  | structural assertion | what passed it |
  |---|---|
  | payload has key `scope_note` | `"scope_note": ""` — key shipped, text discarded |
  | `phase_plan` calls `_effective_source_of_truth` | ctx reads the preference directly, bypassing it |
  | abort message contains every remediation phrase | the fallback hoisted back to lead — the wording an A/B measured as misrouting 5/5 operators |
  | `die(_unresolvable_die_message(...))` exists in source | the gate never calls it, and 140 tests stay green |

  Cheapest discriminating test per shape: execute the consumer (not read its
  source); assert the value (not the key); assert the order (not presence).
  Use a behavioural probe on prose, not a substring (a phrase can be present
  and negated). Parametrized value tests should make inputs *disagree* — two
  equal sources of a value can't tell a correct read from a bypass.
- Three more lessons, detailed in `docs/TESTING.md`: a handler must survive
  its own exception, not merely catch it
  (`test_survives_a_save_that_is_still_failing`,
  `tests/test_disk_preflight.py`); a timeout is infrastructure, not a leerie
  bug, and must be classified alongside `WorkerError` in every
  retry/escalation path, never interpolated into a message verbatim
  (`tests/test_checked_loop.py`); and ablate a pattern against its corpus
  instead of adding alternatives — unreachable alternatives are false-positive
  surface, not defense in depth (`_host_finalize_is_auth_or_network_push_error`).
  Derivation guards are one-directional unless you write the converse: a test
  iterating a table only proves every entry is reproducible, not that every
  entry that should be there is (`TIMEOUT_DEFAULT_PER_WORKER`, `main()`
  caps-wiring).

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
