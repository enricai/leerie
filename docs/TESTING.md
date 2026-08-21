# Testing — per-feature coverage inventory

This document is the detailed, per-feature/per-incident testing narrative
that used to accrete directly inside CLAUDE.md's `## Testing` section.
CLAUDE.md keeps only the operational rules a session needs before running
the suite (concurrency hazards, PATH/CLI gating, environment mutation
hazards) plus a pointer here. This file is the historical/reference layer:
which test file covers which feature, which incident it was written in
response to, and the specific traps ("measured", "falsified live") that
made a naive version of the test pass vacuously.

Per CLAUDE.md's own "Commit messages are the permanent record" discipline,
per-incident detail belongs in git history where possible — this file exists
because these lessons are load-bearing enough that a future contributor
needs them without archaeology through commit logs.

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
