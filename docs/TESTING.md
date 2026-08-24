# Testing notes

This file is the per-feature / per-incident testing coverage inventory:
which test file covers which surface, and the specific traps that shipped
once and are now pinned against regressing. It is separated from
`CLAUDE.md`'s `## Testing` section (which keeps only load-bearing
operational rules — concurrency, PATH, subreaper, mid-run-edit,
`jq`/`shellcheck` gating — plus a pointer here) per the "Commit messages
are the permanent record" principle in `CLAUDE.md`: historical per-incident
detail belongs in a durable record, not perpetually accreting in the file
loaded into every session.

No coverage target is set — the suite was introduced from scratch and a
number now would be arbitrary.

## Running the suite

`pytest tests/` from the repo root. Tests cover the deterministic
enforcement functions (`resolve_leerie_root`, `resolve_source_of_truth`,
`resolve_runtime`, `gather_answers` validation gate, `_retryable_failure`,
`check_merge_committed`, `_validate_result`, `_validate_plan`,
`_validate_run_json`, `_derive_run_status`, `_load_blt_config`,
`resolve_blt`), including a coupling test that the retry-policy markers
match the live check-function strings.

## Launcher AWS / EC2 knobs

The `--aws-region`/`--aws-profile` knobs (which region/profile leerie
itself uses when provisioning `--runtime ec2` machines, distinct from the
AWS SDK's own credential-chain env vars) are **launcher-owned**, covered in
`tests/test_resolve_aws_launcher.py`. They were orchestrator-resolved until
2026-08-10, into `args.aws_region`/`args.aws_profile` — which nothing read,
since the orchestrator runs inside the container where a host-side
provisioning region is meaningless. The launcher, the only real consumer,
honoured `LEERIE_AWS_*` alone, so **the documented CLI flag and
`leerie.toml` keys were both silently inert while the env var worked.**
Resolution now runs in the launcher via `_resolve_ec2_knob`, deliberately
**above the top-level verb dispatch** — `accept-blocked`/`stop`/`kill`/
`finalize` each read `LEERIE_AWS_*` inside their own arms, so resolving
beside the `--ec2-*` knobs would have fixed only the main dispatch. The
block is *extracted* from the real launcher by the test rather than
reproduced (see `test_no_duplicate_state_walks.py` for why), and its
load-bearing case asserts the resolved value **reaches the consumer's
argv** (`_aws_region_profile_args`) — the deleted
`tests/test_resolve_aws_prefs.py` pinned the argparse flag and the resolver
and passed for months while the value reached nothing.
`tests/test_no_dead_resolutions.py` generalises that: no
`args.X = resolve_Y(...)` may go unread. It is the sibling
`tests/test_no_dead_functions.py` cannot be — that one scans for
unreferenced *functions*, and these resolvers **were** referenced, by the
dead assignments themselves. Its reader count excludes each assignment's
own RHS (every resolution passes its current value in as the CLI tier);
without that exclusion the sweep reports zero dead resolutions on a tree
that had two.

The launcher-side EC2 instance-shape vars (`LEERIE_EC2_AMI`/
`_INSTANCE_TYPE`/`_KEY_NAME`/`_SECURITY_GROUP`/`_SUBNET_ID`, distinct from
the region/profile prefs above) are covered in
`tests/test_resolve_ec2_vars.py`: the bash `_resolve_ec2_knob` CLI > env >
`leerie.toml` > (no default) ladder — **extracted** from the launcher at
test time by `_extract_resolve_ec2_knob()`, not reproduced. It was a
hand-written copy until 2026-08-10, body-blind *by construction*: the
tests executed a string literal defined in the test file, so no change to
the launcher could reach them, while `test_block_present_in_launcher`
pinned only the helper's name plus the flag/toml-key strings and never its
logic. `tests/test_no_duplicate_ec2_knob.py` keeps it the only
implementation — one of a family of guards of this shape, alongside
`tests/test_no_duplicate_state_walks.py` and
`tests/test_no_duplicate_launcher_splitters.py` (guards the shared
derivation in `tests/launcher_blocks.py`), generalised over eight bash
blocks by `tests/test_no_duplicate_launcher_blocks.py` — with its marker
anchored to the **start of a line**, since a reproduction opens the body at
column 0 while a legitimate reference (`src.index("_resolve_ec2_knob() {")`
inside an extractor) is always quoted mid-line; matching the bare token
would flag the very extractors the guard exists to encourage.

**A falsification trap worth remembering:** the first attempt to prove the
old copy was blind deleted the helper's `[ -f ]` guard, which passes either
way (removing it is behaviourally inert — grep on a missing file already
fails silently). Inverting the CLI/env precedence is the sabotage that
discriminates (5 failures with the extraction, 0 with the copy). A
falsification that changes no observable proves nothing.

Also covered: per-var isolation, `=`-form CLI flags, the env-forwarding
denylist guard (these vars must never leak into the container), and
`ec2-lib.sh`'s `_resolve_ec2_var` required-var-read contract (prints on
success, actionable "not set — required for --runtime ec2" error + rc 1 on
an unresolved var, never a bare `${VAR:?}`).

## Fly.io remote surface

The remote (Fly.io) bash surface — `ensure_image`, `provision_machine`,
`stop_machine`, `decide_teardown`, `resume_machine`, and `lib.sh`'s
`update_run_json` — is tested via bash-harness subprocess tests with
stubbed `flyctl`.

Fly **volume** reaping is covered in `tests/test_provision_volume.py`: Fly
volumes outlive their machines by design (no platform-side lifecycle hook
— *"a Machine can be destroyed without destroying its volume"*), so every
path that kills a machine must reap the volume itself, and three paths
silently did not. The tests pin `destroy_volume` reaping with an **empty**
`LEERIE_MACHINE_ID` (it must not live behind `destroy_machine`'s early
return — that made the volume block unreachable exactly when the machine
had already died); `_resolve_volume_id_from_run_dir` **falling through** a
`fly-machine.json` that lacks `volume_id` to `run.json` (provision writes
the former conditionally, the latter always); `_resolve_volume_id_from_fly`
reading `config.mounts[].volume` out of `machine list --json` (the stub
emits the shape measured against a live machine — `machine status` has no
`--json` flag, so it is deliberately unused); and end-to-end that
`kill --machine-id <id>` with **no run dir** still reaps, with the
load-bearing ordering asserted by call index: **Fly lookup → machine
destroy → volume destroy** (the volume→machine link vanishes with the
machine, but Fly refuses to destroy a still-attached volume, so the reap
must sit between those two events). Harness note: the launcher's state-dir
override is `LEERIE_STATE_DIR`, **not** `LEERIE_STATE_HOST_DIR` — setting
the latter silently resolves to the real `~/.leerie/...` and the test
asserts nothing.

The local per-repo image surface — `resolve_repo_image_tag`,
`_leerie_repo_id`, `build_repo_image`, and `ensure_base_in_buildkit_ns`
(copies the base into the `buildkit` containerd namespace before the
derived build so `FROM $BASE_IMAGE` resolves locally under Colima's
namespaced buildkit; `tests/test_build_repo_image.py` pins that the copy
fires and precedes the build, and the idempotent skip when the base is
already present) — is tested via bash-harness subprocess tests with
stubbed `git` and `nerdctl`.

## Worker cgroup containment

Worker cgroup containment (DESIGN §6 *Memory containment*) is tested in
two files: `tests/test_cgroup_helpers.py` covers the orchestrator-side
broker clients (`_cgroup_probe`/`_cgroup_create`/`_cgroup_enroll`/
`_cgroup_destroy` via a stubbed socket round-trip) and the fail-closed
`_enforce_and_record_cgroup_containment`; `tests/test_cgroup_broker.py`
covers the root broker (`scripts/cgroup-broker.py`) — protocol dispatch,
sid validation, v1/v2 path selection, and the startup **orphan sweep** —
against a fake cgroupfs.

Neither can catch **wire-protocol drift between the two**, which
`tests/test_broker_wire_contract.py` exists for: the broker composes
`slice` (4 tokens) and `stat` (5) while the orchestrator parses them, the
field count is hand-written on both sides, and until that file nothing
compared them. Drift is silent in the worst way — both parsers return
`None` on a mismatch and `None` legitimately means "containment is off",
so a drifted `slice` makes worker sizing fall back to `/proc/meminfo`
**and** turns the admission gate into a no-op, while a drifted `stat`
disables PID-exhaustion detection and memory-OOM naming, with nothing
logged and no test failing. The existing two cannot see it by
construction: `test_cgroup_helpers.py` feeds leerie's parser hand-written
fixture strings (parser vs. fixture, not parser vs. broker) and
`test_cgroup_broker.py` never touches leerie's parsers. The contract file
is the only place the two meet — the **real** broker's emitted string fed
to the **real** leerie parser, with the socket the only thing stubbed —
and it carries an anti-vacuity test proving the guard fires on an
added/removed field, plus a transposition check (right arity, wrong order)
driving the parsed values into `_worker_memory_ceiling` and the headroom
comparison. Same class as the `collect-subtrees.sh` schema duplication
elsewhere in this file, which had already drifted in production before its
guard existed.

The burst-reservation state `_active_admissions` (token → monotonic stamp,
mutated by `_await_worker_memory_admission` and
`_release_worker_memory_admission`) is **module-level** and conftest's
`leerie` fixture is **session-scoped**, so `tests/test_slice_aware_memory.py`
clears it in an autouse fixture on both sides — without that its burst
tests are order-dependent and leak reservations into every other file that
exercises the gate; a guard-the-guard test source-couples to the fixture's
`scope="session"` so a scope change forces that reasoning to be
re-examined.

`tests/test_memory_admission_degrade.py` covers the **first** admission
stage, `_degrade_max_parallel_for_wave` — the synchronous wave-entry
shrink that sizes a wave's `asyncio.Semaphore` to real headroom so the
blocking gate rarely has to act. It carries the same autouse
`_active_admissions` reset, and its load-bearing test is
`test_uses_the_same_signal_as_the_blocking_gate`: the degrade and the gate
must both read `slice_max - unreclaimable`, because two signals could
disagree about one slice — sizing a wave down against page-cache pressure
the gate then admits into. `test_is_synchronous_not_a_per_spawn_gate`
strips the docstring via `ast` before scanning for `await`: the docstring
names `_await_worker_memory_admission` on purpose, and a naive substring
check matches the prose describing the thing it forbids (the same trap the
zombie-reaper guard documents below). Those burst tests use the
**measured** production density (15 worker starts per 180 s, from real
runs' `calls.ndjson`) rather than an assumed number: an earlier revision
bounded reservations by elapsed time instead of by worker lifetime, which
stalls every real run — its tests used 5, under `max_parallel`, and passed
against the defect, which first bites at 9.

## Repo-declared heap reconciliation

The **repo-declared-heap reconciliation** (N14-16, DESIGN §6 — a repo's
own `--max-old-space-size` overrides whatever heap Node would infer from
the cgroup, so the per-worker ceiling must be reconciled against it) is
covered by `tests/test_worker_heap_ceiling_reconcile.py` and
`tests/test_worker_memory_heap_reconcile.py`, with the P9 `NODE_OPTIONS`
injection in `tests/test_node_options_injection.py` and the resolver chain
in `tests/test_resolve_worker_memory_max.py`. Four traps here are not
obvious from the test names.

1. **The two error directions are not symmetric, and the asymmetry sets
   which tests matter.** The declared heap RAISES the cage
   (`needed = declared_heap + _NODE_HEAP_HEADROOM_BYTES`), so
   over-detecting a script name only inflates the cage and throttles
   admission, while MISSING one under-sizes it and the worker OOMs — the
   failure the whole reconciliation exists to prevent. `_pm_script_candidates`
   is therefore deliberately over-inclusive, and three narrowings (abandon
   on `exec`/`dlx`, stop at `--`, drop `npx`) were prototyped and rejected
   because each introduced misses; `test_candidate_extraction_stays_over_inclusive`
   pins that intent at the unit, including a block of rows guarding shapes
   `_SEG_RE` must not break (`2>&1`, `&` inside an argument, a PM after the
   separator) — those pass under both the current and the superseded
   implementation on purpose, as a guard against the next edit to that
   regex, not a second proof of the fix.
2. `_SEG_RE` **splits before tokenising**, because testing each
   whitespace-split token against a separator set cannot see `build&&node`
   (one token); the old form lost the real script on every space-free
   separator.
3. **A config.toml fixture cannot reach the real code path** — measured
   across the five repos leerie manages, 2 of 5 declare a heap in
   `package.json` and **0 of 5** in `.leerie/config.toml`, so the original
   config.toml-only suite reported full coverage while the reconciliation
   fired on none of them. The second half of the file builds
   `package.json` fixtures for that reason. A related trap: `_write_config`
   interpolates into a TOML **basic string**, so a command containing a
   literal newline produces invalid TOML, the whole config is silently
   dropped, and the assertion is answered by BLT inference instead — one
   parametrization passed that way while testing nothing, which is why the
   newline case is asserted at the unit rather than end-to-end.
4. **`_NODE_HEAP_HEADROOM_BYTES` needs its own value pin.** Both P9's
   injection and the reconciliation derive from it (mirror images of one
   quantity that briefly disagreed, handing Node a heap 384 MiB larger
   than fits the cage), which is the coupling we want — but every
   assertion moves with the constant: setting it to `243 * 1024 * 1024`
   once left the entire suite green. `test_node_heap_headroom_is_2432_mib`
   anchors the value, and the AST pin resolves the reserve **name's
   binding** rather than merely requiring an `ast.Name`, so
   `reserve_mb = 2432  # <const>` fails.

`tests/test_decompose_share_advisory.py` covers `_warn_decomposition_share`,
whose one non-obvious test is the partial-caps case: every other test
supplies `max_total_workers`, so the `.get()` fallback is only exercised
by a caller that omits it (`run_recapture_deps`, `run_rebaser`,
`_replay_capture` each build their own minimal caps) AND only on the
branch that runs when the warning fires.

## Production-grounded evidence gate

The **production-grounded evidence gate** (DESIGN §9 — every other gate
asks whether the code matches its specification, none asks whether the
specification matches reality) is covered by
`tests/test_production_evidence.py` and `tests/test_unreviewed_subtasks.py`.
Three traps are not obvious from the test names.

1. **The field is optional in the schema and gating in the check, and
   that is ONE decision, not two.** Requiring it costs the entire
   submission rather than the one field — `_confidence_schema`'s docstring
   records that measured at 40.9% valid on `plan_overlap_judge`, with 84 of
   its 85 failures being a single required field — while gating on absence
   is what stops "optional" from meaning "ignorable".
   `test_schema_field_is_optional_at_the_top_level` and
   `test_absent_evidence_gates` are anti-vacuity partners: remove either
   and the field becomes decorative. The object is also flat with one
   required inner bool for the same decoder-corruption reason
   (anthropics/claude-code#49747).
2. **The conformer's copy must be READ, not merely declared.** It shipped
   once as a dead field — on both schemas, with exactly one call site and
   no mention in `conformer.md` — while DESIGN and IMPLEMENTATION both
   described it as consumed. It is wired **advisory** (extends
   `conf_warnings`, never `blocked_reason`), because `solution_defects` is
   deliberately the one gating conformer axis and an advisory phase must
   not gain a new way to stop a run; `test_conformer_side_is_advisory_not_gating`
   pins the distinction by inspecting the statement, not just the call.
3. **Source scans here strip comments first.** Both
   `conformance[sid] = {...}` write sites carry comments naming
   `unreviewed_subtasks` while explaining why one of them must not touch
   it, so a raw substring scan matches the prose describing what it
   forbids and fails on correct code — the same trap the zombie-reaper
   guard documents. The helper also bounds each site at its closing
   `st.save()` rather than by a character count: a fixed window was tried
   twice and truncated mid-statement both times, reporting a key as
   missing when it was merely past the cutoff. The two write sites mean
   different things — the mid-run satisfied-rescue sentinel sets
   `reviewed: False` but is deliberately excluded from
   `unreviewed_subtasks`, since a zero-commit subtask has no diff to
   review and folding it into the operator warning is how a warning
   becomes noise.

`tests/test_symptom_evidence.py` covers `check_symptom_evidence`, the
sibling — scoped by the planner's `fixes_reported_symptom` declaration,
never by an id prefix — that asks whether the reported symptom still
reproduces on the base tree. Run fa979580's N18 subtask re-fixed a leak an
earlier PR had already fixed, shipping an event-loop stall on the way.
Three traps:

1. **It is advisory and must stay that way**: the output never reaches
   `check_implementer_output`, because those issues drive
   `implementer_confidence_retries` and a retry cannot make a stale
   finding un-stale — it asks the same worker the same question — while a
   second *gating* evidence field would stack retry pressure on the
   production-evidence gate. `test_not_wired_into_the_gating_check` is the
   pin. Advisory does NOT mean ephemeral: the findings are also persisted
   to `symptom_findings` in `state.json` (results are in-memory only —
   `phase_execute` writes just `blocked` reasons out of them), cleared for
   a sid whose later attempt reports cleanly so a re-driven subtask
   carries no stale entry, and surfaced by `phase_finalize` for the
   `SYMPTOM_DID_NOT_REPRODUCE` case ONLY — `NO_SYMPTOM_EVIDENCE` is worker
   hygiene and a summary line that fires every run is how a warning stops
   being read.
2. **"The new tests fail on base" is NOT this and is worthless** —
   measured on that run all four findings' tests already failed on base (9
   of 13 for one), because a new test against absent code trivially
   fails; the field therefore asks for a command and an observation, not a
   bare boolean.
3. **Scoped by the planner's `fixes_reported_symptom` declaration**, never
   by the subtask's id — that was the original design and it produced 10
   of 10 false positives, because `_repair_prescribed_commands` mints ids
   from the HOST subtask's domain and a merge re-homes a `feat-` subtask
   under a surviving `bugfix-` id. *Language-to-JSON* is usually read as
   being about prose, but an identifier is a string too. The `sid` still
   comes from the ORCHESTRATOR, never from the worker's echoed
   `result["subtask_id"]` — nothing in the module cross-checks that echo,
   which is precisely why the id is not the scope signal. Taking the sid
   string rather than the subtask dict also removes a `None`-dereference
   by construction, and neither this check nor `check_implementer_output`
   coerces a bad argument (`sid or ""`, `subtask or {}`): both shapes
   swallow a contract violation and leave the check silently disabled —
   measured, an empty subtask dict makes `NO_PLANNED_FILES_TOUCHED` unable
   to fire — so both raise instead, each pinned by its own non-coercion
   test.

The same change wired `check_production_evidence` into
`_run_final_conformance` — #197 wired the per-subtask site and missed the
whole-tree pass, which is the last gate before a run is declared done and
the one that certified four inert fixes at confidence 8.5. It runs after
`_validate_conformance_result` so a shape-rejected payload is not also
reported as missing a field.

## Memory-OOM naming and PID reaping

Memory-OOM naming (DESIGN §6 *Detecting memory OOM*) — the `empty_handoff`
seam that prefers a worker's named OOM cause (offending command +
`memory.max`) over `_validate_result`'s generic "checkpoint ... does not
exist" text — is pinned end-to-end through `_settle_subtask` in
`tests/test_oom_naming.py`: both empty_handoff branches (the no-commits
`fail()` path and the has-commits rescue path that keeps the diff and logs
instead of discarding it) surface the named cause when
`_run_implementer`'s synthesized `incomplete-handoff` envelope carries
one, including the `--worker-memory-max` / `--max-parallel` remediation
pointer; a healthy no-op empty_handoff (no named cause) does not fabricate
an "OOM-killed" message.

Mid-run PID reaping (DESIGN §6 *Mid-run PID reaping*) is tested in
`tests/test_signal_cleanup.py`: `_reparented_orphans` selects only
alive+ppid==1+old PIDs sorted oldest-first (stubbed ps); `_poll_loop` reaps
only at ≥90% pressure and stops below 75% (hysteresis); below 90% is a
byte-identical no-op; attached (ppid!=1) PIDs are never reaped; and a
structural guard pins `cgroup_sid: str | None = None` on
`_DescendantTracker.__init__` so the 3 pre-existing direct-constructor call
sites remain compatible after the parameter was added.

The age floor is **two-tier** (DESIGN §6 *the critical tier*), so "young
PIDs are never reaped" holds only in the normal tier: below
`_PID_REAP_CRITICAL_WATER` a young orphan is protected by the 60 s floor
(`test_poll_loop_young_orphan_not_reaped`, which monkeypatches the
critical water *up* so that tier is reachable at all — the shipped
constants are equal at 0.90), and at or above it the floor drops to
`_PID_REAP_CRITICAL_AGE_SEC` (5 s) and the same orphan **is** reaped
(`test_poll_loop_young_orphan_reaped_at_critical_pressure`). The critical
tier is the fix for the measured burst case: a leak saturates `pids.max`
faster than the 60 s floor lets anything become eligible, so the reaper
armed, found an empty candidate list, and watched the worker die (run
879defae, wave 2). Reverting the tier fails that test with
`assert 900 in []` — the empty list *is* the production bug.

Four of these tests were previously **vacuous**: they stubbed
`_cgroup_stat` with a 3-tuple while `_poll_loop` unpacks 4, so the
`ValueError` skipped the entire reaping branch and they passed against
code that never ran — including `test_poll_loop_reaps_above_high_water`,
which additionally asserted only after `stop_and_reap()` (that path
SIGKILLs `_seen` wholesale, so it passed without any mid-run reap firing).
Both traps are fixed and pinned; snapshot `killed` *before*
`stop_and_reap` in any new test here.

Zombie reaping (DESIGN §6 *Zombie reaping* — the container PID 1 is
`runuser`/idle `sleep`, not a reaping init, so orphaned git/ssh-agent
descendants would pile up as `<defunct>` against `pids.max`) is tested in
`tests/test_subreaper.py`: `_become_subreaper` is a bool-returning no-op
off Linux and (Linux-guarded) sets the flag verifiable via
`prctl(PR_GET_CHILD_SUBREAPER)`; `_zombie_reaper` (Linux-guarded) reaps an
orphaned exited child so it's no longer a zombie and survives having no
children.

The load-bearing race test is
`test_zombie_reaper_does_not_steal_unregistered_subprocess_status`: it
spawns 40 short-lived asyncio children with the reaper hot at 1ms and
**registers nothing**, asserting every child reports its true code (7),
not a fabricated 255. Registering would defeat the test's purpose — the
production failure is a pid that is unregistrable *by construction*,
sitting in the window between `fork()` and asyncio's `os.pidfd_open()`.
The old design (scan `/proc` for state==Z + ppid==getpid, minus
`_ASYNCIO_MANAGED_PIDS`) passed a test that registered the pid *before*
starting the reaper — a sequencing production never provides — while
taking `preflight`'s own `git config` pid on 40/40 real runs. Safety now
comes from `_REAPABLE_PIDS`, an allowlist populated by `_mark_reapable`.
Paired with `test_zombie_reaper_still_reaps_a_recorded_orphan` (a reaper
that reaps nothing is not a fix, it is a disabled reaper) and three
source-coupling guards: the reaper's source contains no
`/proc`/`listdir`/`_orphan_zombie_children` (docstring stripped via `ast`
first, since it *describes* the forbidden scan), `_DescendantTracker._poll_loop`
calls `_mark_reapable` (the fix is inert without the wiring), and
`_mark_reapable` never admits an `_ASYNCIO_MANAGED_PIDS` member; plus a
`_reparented_orphans`-accepts-`ppid==getpid` test, and source-coupling
guards that `main()` calls `_become_subreaper()` and `_orchestrate()`
spawns+cancels `_zombie_reaper`.

## PENDING_ISSUES.md follow-up surfaces

Three further surfaces arrived with the `PENDING_ISSUES.md` work order,
catalogued here because their traps are not obvious from the test names.

`tests/test_duplicate_provider_merge_routing.py` (7 tests) pins that
`check_duplicate_providers`' detections are routed into a **merge**
resolution and never a drop — the transitive `survivor_of` chase is safe
for a merge (intent carries forward) and silently destroys a live subtask
for a drop, which is the hazard `_apply_multidrop` documents above. The
floor had been advisory only: measured across the run corpus, **4 of 5
runs where it fired applied zero resolutions**, one of them with 35
detections and no action.

`tests/test_recursive_decompose_parallel.py` (4 tests) pins that
`phase_plan`'s expansion loop — previously a plain sequential `await`,
measured at ~0.7x parallelism — now bounds concurrency with the
**existing** `_gather_or_cancel` while preserving `decompose_snapshot`'s
per-completion write, including the `list(leaves)` copy that keeps a
later crash from mutating an already-taken snapshot (the aliasing class
`test_checkpoint_aliasing.py` exists for).

`tests/test_require_fly_ssh_isolation.py` (8 tests) pins
`_leerie_fly_agent_ensure`'s reuse predicate, whose exit codes are the
whole point: `ssh-add -l` returns **1** for a reachable-but-keyless agent,
**0** with a key, **2** for a dead socket (verified live). Treating rc 1
like rc 2 `rm -f`s a live agent's socket out from under it, orphaning the
process — which is the leak. The `-t 24h` on spawn bounds the
**identities**, not the agent process, so it is not an orphan mitigation;
see `scripts/remote/lib.sh`'s comment.

## fetch_branch, config, and group verbs

The `fetch_branch()` stream-back surface (`scripts/remote/fetch-branch.sh`)
is tested across two files. `tests/test_fetch_branch_sh.py` covers run
discovery, bundle fetch, run-state tar, `no_push` strip, and baseline Step
4 stream-back (both files streamed when host has neither, never clobbers
an existing host file, non-fatal on absent machine files, respects
`LEERIE_STATE_HOST_DIR`) via bash-harness subprocess tests with a stubbed
`flyctl`. The expanded Step 4 best-effort `.leerie/` stream-back contracts
are covered by `tests/test_fetch_branch_leerie_streamback.py` (imports
stub helpers from `test_fetch_branch_sh` to avoid duplication): streams
both files when host has neither, never clobbers an existing
`config.toml`, never clobbers an existing `Dockerfile`, non-fatal when
machine files are absent, streams only the present machine file when only
one exists, skips both when both host files exist, and respects
`LEERIE_STATE_HOST_DIR` for the destination root.

The `leerie config` verb (all four sub-modes: `--init`, bare, `--chat`,
`--recapture`) is tested in `tests/test_config_verb.py` via a
self-contained bash harness with stubbed `nerdctl` and `claude`, plus a
parity guard that extracts the real launcher `config)` case arm and diffs
its BLT inference against `_infer_build_lint_test()` across a fixture
matrix so the two can never silently diverge.

The `group` launcher arm and group-scoped ID-dispatched verbs are tested
in `tests/test_group_launcher.py` via the same bash-harness pattern
(stubbed `./leerie`, multi-state-dir fixtures), modeled on
`tests/test_chain_launcher_id_dispatch.py`. Group-scoped verb dispatch
across two state dirs (combined paused/unpushed + pushed fixture, plus
`stop` dispatch) is covered by `tests/test_group_launcher_verbs.py`.
Fan-out core contract (cwd per member, `--inspect-dir` for siblings, brief
prepend) is in `tests/test_group_launcher_fanout.py`. Python-layer
`group_id` in `run.json` (`_validate_run_json`, `_write_run_json`,
`_derive_run_status`) is in `tests/test_group_run_json.py`. State-dir
isolation (distinct basename-keyed dirs per member, guard rejects
`LEERIE_STATE_DIR`/`--state-dir`) is in
`tests/test_group_state_dir_guard.py`. That file's class-A harness
**extracted** `_state_dir_default` from the launcher rather than
reproducing it as of the N13 follow-up — it had been a hand-copy whose own
docstring cited nine launcher line numbers, every one stale (the block had
moved to `:785-844`), so no change to the launcher could fail it. Those
citations are now corrected in place rather than deleted — they are what
makes the extraction's target legible. It now imports
`_extract_state_dir_block` from `tests/test_resolve_state_dir.py`, the
single owner of that extraction, which has two importers (this file and
`tests/test_launcher_state_mount.py`).

## Dependency capture engine

The capture engine (DESIGN §6½) — `_gather_dep_manifests` (the
manifests-first PRIMARY corpus), `_extract_depcap_commands` (the
install-filtered SECONDARY command hint), `_is_install_command` (the
install-verb filter), `_toml_value`/`_dump_language_installs`
(single-quote-safe TOML persistence), `_merge_setup_packages`,
`capture_repo_deps` (async, with stubbed `claude_p`), the idempotency
sentinel (`dep_capture_done` state field + `<run_dir>/dep_capture.done`
file), and `_backstop_capture_prior_runs` (skips runs with sentinel,
captures runs without) — is tested across four files.

`tests/test_dep_capture_budget.py` covers the extraction+budget unit
(`_extract_depcap_commands`) in focused isolation: dedup, newest-first
ordering, budget gate (`_DEPCAP_TOTAL_BUDGET`), `hit_ceiling` flag
semantics, non-Bash filtering, and malformed-line tolerance. It also
carries the guard-value-that-cannot-guard regression pin (bugfix-004,
incident 2026-07-19): `test_depcap_budgets_not_argv_bound_by_source`
asserts via source inspection that the `_DEPCAP_TOTAL_BUDGET` comment
states the dep_capture payload travels over stdin (not argv) and names
`MAX_ARG_STRLEN` rather than the aggregate `ARG_MAX`;
`test_depcap_total_budget_value_unchanged_since_incident` pins
`_DEPCAP_TOTAL_BUDGET`/`_DEPCAP_MANIFEST_TOTAL_BUDGET` unchanged and that
their combined bound still exceeds `MAX_ARG_STRLEN` — safe only because
the payload is stdin-transported (bugfix-001), not argv-bound.

Since DESIGN §6½ moved the worker to a manifests-first corpus,
`_extract_depcap_commands` now keeps **only install-shaped Bash commands**
(`_is_install_command`) — the install-verb filter and its text-tool-pattern
exclusion (e.g. `grep "apt-get install …"` is dropped) are pinned in
`tests/test_capture_deps.py` (`TestIsInstallCommand`,
`test_filters_to_install_shaped_only`,
`test_excludes_install_verb_inside_text_tool_pattern`), alongside
`_gather_dep_manifests` (`TestGatherDepManifests`) and `_toml_value` /
`_dump_language_installs` (`TestTomlValue`, including the both-quote
single-quoted-command TOML-validity regression).

`tests/test_capture_deps.py` covers the integration against a synthetic
JSONL fixture in the `_iter_log_tool_use` shape: absence pins
(`TestRegexPathAbsent`) that assert the four deleted regex-path symbols no
longer exist on the module (so the regex path can never silently return);
command extraction, budget ceiling truncation, merger union/no-op/
never-clobber, schema-validated worker output → setup_packages +
language_installs write, committed-Dockerfile skip, write-failure
non-fatal, and opt-out. It also pins the `--recapture --force`
wholesale-replace path (`replace=True` drops deps no longer captured; an
empty capture leaves the existing config untouched) alongside the default
union. Source-coupling guards in the same file pin that `main()`'s
`KeyboardInterrupt` and `InterruptedBySignal` handlers each invoke
`capture_repo_deps` (the cancel-arm seam — the fix is inert without the
wiring).

The worker-driven write path specifically — `capture_repo_deps` invoked
with a stubbed `_invoke` returning a fixed structured_output envelope
(mirroring `test_phase_judge.py`'s `_JUDGE_ENVELOPE` pattern) — is
separately covered in `tests/test_dep_capture_worker.py`: schema-validated
output written to `.leerie/config.toml`, warm-repo never-clobber (mtime
unchanged when all deps already present), union append for new packages,
env + config-file opt-out (worker not invoked), committed `.leerie/Dockerfile`
guard (worker not invoked), missing logs dir silent no-op, and non-fatal
write failure. A `TestDepCaptureReplace` class covers the `replace=True`
(`--recapture --force`) path: wholesale-overwrite of
`setup_packages`/`language_installs` (stale deps dropped), an empty
capture leaving the config untouched, and — the regression pin for the
empty-item blanking bug — a schema-valid empty-item capture
(`setup_packages=[""]`, empty-manager `language_installs`) not blanking a
good config.

The `dep_capture` schema contract — required fields, `language_installs`
item shape, valid/invalid instance acceptance, `minLength:1` on
package/manager/command (empty-string rejection), JSON round-trip, and
wiring checks (`WORKER_TYPES` exclusion, effort/model defaults) — is
pinned in `tests/test_dep_capture_schema.py` (mirrors
`test_pr_writer_schema.py`). The model/effort resolution precedence for
`dep_capture` is pinned in `tests/test_resolve_dep_capture_model.py`
(mirrors `test_resolve_models.py` and `test_resolve_efforts.py`).
`dep_capture`'s model override is **env-var-only** — no
`--model-dep-capture` CLI flag and no `model_dep_capture` `leerie.toml`
key (both were removed as dead slots); precedence is per-worker env
(`LEERIE_MODEL_DEP_CAPTURE`) > global CLI > global env > global TOML >
`MODEL_DEFAULT`. The file asserts a stray `args.dep_capture_model` and a
`model_dep_capture` TOML key are **not** honored. Effort: global CLI >
global env > global TOML > `EFFORT_DEFAULT_PER_WORKER["dep_capture"]`. It
also pins the `MODEL_DEP_CAPTURE_ENV` constant, `dep_capture` absent from
`MODEL_DEFAULT_PER_WORKER` (sonnet via the global `MODEL_DEFAULT`
fallback), and present in `EFFORT_DEFAULT_PER_WORKER` with value
`"medium"`.

The three orchestrator wiring seams that are only verifiable by source
inspection are pinned in `tests/test_dep_capture_wiring.py` (mirrors
`test_phase_finalize_capture_hook.py`'s `inspect.getsource` approach):
`main()`'s `KeyboardInterrupt` and `InterruptedBySignal` exit arms each
invoke `capture_repo_deps` inside their own `asyncio.run()` wrapped in a
non-fatal `try/except Exception`; `_run_phases()` calls
`_backstop_capture_prior_runs` before `phase_classify` (the SIGKILL /
crash recovery path); and the `dep_capture` prompt file exists alongside
`SCHEMAS['dep_capture']` (the §12 advisory + code-enforces split).

## P6 repo-map ranking

The P6 ranking contract (DESIGN §5½ (P6)) is pinned in
`tests/test_rank_repo_map.py` across three classes:
`TestSeedNeighborhoodRanking` (seed-adjacent nodes rank above unrelated
nodes — direct seed file, 1-hop neighbor, seed symbol biases definer, all
connected before unrelated, large-graph unrelated cluster at tail);
`TestTokenBudgetEnforcement` (output fits within explicit budget and
within `DEFAULT_CAPS["repo_map_tokens"]` when None; `None` budget equals
the cap value; empty map returns `""`); `TestBinarySearchShrink` (lowering
the budget yields shorter output and fewer files; increasing budgets yield
non-decreasing lengths; 1-token budget yields empty or a single very-short
entry). Fixture is built directly (no `_build_repo_map`) — isolates
ranking. No LLM calls; deterministic.

## P1 recursive decomposition

The P1 recursive decomposition surface (DESIGN §5½ (P1)) is tested across
four files.

`tests/test_fit_judge_schema.py` covers `SCHEMAS["fit_judge"]` — required
fields (`score`, `rationale`, `diffuse`, `confidence`), `score` bounds
(minimum 0, maximum 1), `confidence` using the `"fit"` axis, valid and
invalid instance acceptance, JSON serializability, and wiring (`fit_judge`
in `WORKER_TYPES`, not in `MODEL_DEFAULT_PER_WORKER`,
`EFFORT_DEFAULT_PER_WORKER` entry at `"medium"`, prompt file exists).

`tests/test_splitter_schema.py` covers `SCHEMAS["splitter"]` — `children`
required but with **no `minItems`** (an empty array is the valid answer
"this does not split"; `minItems:1` was removed 2026-08-03 after the
corpus showed the splitter returning `[]` 43 times and a single no-op
child 43 more, every empty return rejected and retried even though
`_recursive_decompose` already accepted it as a leaf), child required
fields (`id`, `title`, `success_criteria_seed`), optional child fields,
valid/invalid instances, JSON serializability, the same wiring guards, no
top-level `files` field (splitter never decides partition), and the child
`requires` array uses the `_REQUIRES_ITEM` shape (tag + extent enum).

`tests/test_resolve_fit_judge_model.py` and
`tests/test_resolve_fit_judge_splitter_model.py` cover model and effort
resolution for `fit_judge` and `splitter` — both in `WORKER_TYPES`; both
absent from `MODEL_DEFAULT_PER_WORKER` (sonnet via global `MODEL_DEFAULT`
fallback); both in `EFFORT_DEFAULT_PER_WORKER` at `"medium"`; per-worker
CLI/env/TOML override chains; isolation (override doesn't bleed to other
workers); structural wiring guards.

`tests/test_partition_files.py` is the dedicated test for
`_partition_files()`: 44 tests across parametrized invariant sweeps (input
sizes 0, 1, 8, 29, 64; chunk-size 1, equals-n, larger-than-n,
partial-last-chunk) plus named telemetry cases — the 29-file migration
sweep and 64-file date-fns sweep that drove the design (LLM silently
dropped 14/29; code-partition is complete by construction). Asserts: 100%
coverage (sum of chunk lengths == len(input)), zero overlap (no file in
two chunks), chunks bounded by chunk_size, and order preserved.

`tests/test_recursive_decompose.py` covers `_recursive_decompose()`
(well-fit subtask is a leaf at score ≥ 0.70, oversized subtask recurses
then children are judged, depth cap terminates at `decompose_max_depth`,
no-progress guard terminates after `decompose_noprogress_rounds`,
migration path uses `_partition_files` for the file→chunk partition and
invokes the splitter only in label-only mode to title each chunk (distinct
titles; deterministic fallback on splitter failure), `st.bump_workers`
called before every `claude_p`, both `claude_p` call sites pass the full
required signature (`cwd`/`autonomous`/`caps` — the C0 regression guard),
and a passed `repo_map` is re-ranked per node and injected into
fit_judge/splitter prompts); it also carries a parallel set of structural
`_partition_files` tests for regression coverage within that file.

`tests/test_recursive_decompose_schedule.py` is the integration test for
the seam between Layer B and the existing scheduler (DESIGN §5½ (P1)
end-of-pipeline claim): leaf ids from `_recursive_decompose` carry a valid
domain prefix so `_schedule()` cross-domain wiring and `_validate_plan`'s
id-prefix check both pass; a ready plan built from stubbed leaves feeds
`_schedule()` and produces the correct topo-sorted wave partition
(independent leaves in wave 0, a dependent leaf in wave 1); and
`_validate_plan` accepts the full leaf set without errors.

The post-ship gap fixes are pinned in `tests/test_recursive_decompose.py`
(C0: `test_recursive_decompose_calls_claude_p_with_full_signature` binds
each `claude_p` call against the real signature so a missing
`cwd`/`autonomous`/`caps` fails; G1:
`..._migration_partition_owns_files_splitter_only_labels`,
`..._migration_children_have_distinct_labels`,
`..._migration_label_fallback_on_splitter_failure`; G2:
`..._injects_repo_map_into_worker_prompts`, `..._no_repo_map_when_none`),
in `tests/test_check_functions.py` (G3:
`test_low_decomposition_quality_does_not_gate`,
`test_low_task_understanding_still_gates` — the axis is advisory, only
`task_understanding` gates), and in `tests/test_repo_map_degrade_warning.py`
(G6: `_build_repo_map` warns exactly once per process when source files
exist but the graph is empty, stays quiet for a non-code repo).
`tests/test_repo_map.py` now carries a `HAS_TREESITTER` module skip gate
(G4) mirroring `test_build_repo_map.py`.

## Tree-sitter probe and repo-map gate wiring

`_tree_sitter_extraction_works()` itself — the functional probe the
G4/G6 skip gates and degrade warning both delegate to — is pinned directly
in `tests/test_tree_sitter_probe.py`: the True branch (real, unstubbed
`_parse_repo_file` on a working tree-sitter host) is gated on
`HAS_TREESITTER` so it skips rather than fails on an incompatible host;
the two False branches — `_parse_repo_file` raising (simulates an
installed-but-incompatible language-pack version lacking `process()`) and
`_parse_repo_file` returning `([], [])` (extracts nothing) — are
host-independent and always run, since they are the load-bearing proof
that the probe fails closed regardless of the local tree-sitter install
state.

Because the actual G6 warning previously named only two possible causes
("unavailable or incompatible") with no way to tell which, or to see the
real underlying error, `_parse_repo_file` now stashes a short diagnostic
(`f"{type(e).__name__}: {e}"`) in the module-level `_last_parse_error` on
an actual caught exception (not on a plain unsupported-extension miss),
and `_warn_repo_map_empty_once()` appends it to the warning as
`" Probe failure: <type>: <message>"` when present. `test_tree_sitter_probe.py`
pins that the raising branch populates `_last_parse_error` with the exact
exception text and that the empty-result branch (no exception at all)
leaves it `None` — the distinction that keeps the parenthetical from
appearing on the legitimately-quiet path. `test_repo_map_degrade_warning.py`
adds the end-to-end pins: `test_warning_includes_probe_exception_detail`
(a probe-only raise surfaces `"Probe failure:"` plus the exception type
and message in the logged warning) and
`test_no_probe_detail_when_empty_result_is_not_an_exception` (the existing
plain empty-graph path grows no spurious detail).

A related production failure surfaced the root cause for the `/tmp/.cache`
fix below: `RuntimeError: Download cache lock error: create cache dir
/tmp/.cache/tree-sitter-language-pack/v1.12.5: Permission denied (os error
13)`. Root cause (verified live against both the pre-fix and fixed image
via the real `unshare --user --map-user=$(id -u leerie)` mechanism
container-entry.sh uses under rootless containerd): that unshare remaps
only outer UID 0 -> inner leerie, so a directory explicitly chowned to
leerie's own (non-zero) UID is NOT covered by the remap and appears owned
by nobody/65534 to the privilege-dropped process: traversable via mode-755
"other" bits, but not writable. This is the same bug class already hit for
corepack (`COREPACK_HOME`, worked around with its own dedicated
bind-mounted cache dir rather than fixed at the source) — chasing each
offending tool down individually doesn't scale, so the fix instead makes
`/tmp/.cache` itself world-writable with the sticky bit (mirroring `/tmp`'s
own `drwxrwxrwt`) at both the Dockerfile (build time, the layer rootless
runs rely on exclusively) and `container-entry.sh` (a runtime safety net
for the rootful/Fly path, mirroring the existing `chown` pattern there).
`tests/test_tmp_cache_writable.py` pins both sites source-coupled
(mirroring `test_rootless_host_uid.py`'s extraction style): the
Dockerfile's build RUN step and `container-entry.sh`'s rootful-guard block
both carry `chmod -R a+rwX /tmp/.cache` + `chmod 1777 /tmp/.cache` after
the existing `chown`, and the runtime chmod specifically lives *inside*
the `ROOTLESS != true` guard (rootless has no runtime fixup path and
relies on the image's baked-in mode alone).

The same bug class hit `/home/leerie/.local`, `.cache`, and `.gnupg`
directly (`pip install --user` failing with `EACCES: /home/leerie/.local/lib`),
fixed in `tests/test_home_leerie_ownership.py` (same source-coupling
style, but a chown-back rather than a chmod-world-writable, since these
are a fixed set of pre-created dirs rather than an arbitrary-tool
`XDG_CACHE_HOME`): the Dockerfile's `mkdir` block for these dirs carries
no `chown` (root-owned, which is what maps correctly through the rootless
remap — the same mechanism that already makes bind-mounted host dirs
writable with no chown), `chmod 700 /home/leerie/.gnupg` is kept, and
`container-entry.sh`'s rootful-guard block chowns `/home/leerie`,
`.local`, `.cache`, and `.gnupg` to `leerie` at runtime instead (needed
there since the rootful `runuser -u leerie` drop is a real UID switch, not
a remap). A dedicated test asserts the `/tmp/.cache` fix above is
unchanged by this.

The gate *wiring* itself — as opposed to the probe's own runtime contract
— is pinned in `tests/test_repo_map_gate_wiring.py` via source-coupling
assertions (mirroring `test_dep_capture_wiring.py`):
`conftest._has_treesitter()`'s source references
`_tree_sitter_extraction_works` (proving delegation to the functional
probe, not a bare `ImportError` check); `conftest` exposes a module-level
`HAS_TREESITTER` bool; and each of `test_build_repo_map.py`,
`test_repo_map.py`, `test_phase_plan_repo_map_ctx.py` both imports
`HAS_TREESITTER` from `tests.conftest` and contains a `skipif` referencing
it (module- or class-level — `test_phase_plan_repo_map_ctx.py` gates only
its `TestRepoMapEnabled` class). This guards against reverting to an ImportError-only gate or dropping
the skipif from one file, either of which re-introduces the 19-test
host-sensitive failure with no other signal.

## P6/P1 caps and wiring

The four new `DEFAULT_CAPS` values introduced by the F1 P6+P1 work are
pinned in `tests/test_decompose_caps.py`: `repo_map_tokens==1000`,
`decompose_max_depth==5`, `decompose_fit_threshold==0.70` (with a comment
citing F1-build-measure.md — the 0.95 value it replaced over-splits 100%
of well-fit subtasks), and `decompose_noprogress_rounds==2`. Mirrors the
`test_default_cap_is_eight` pattern from `test_resolve_confidence_rounds.py`.

The P6 repo-map builder is pinned in two files. `tests/test_repo_map.py`
covers `_build_repo_map` (symbol/def extraction, class methods, ref edges,
relative-path keys, empty-repo, skip-.git/node_modules), the mtime cache
(dir created on first use, unchanged file served from cache sentinel,
changed file re-parsed, only-changed file re-parsed), `_rank_repo_map`
(string result, token-budget fits, seed-file/seed-symbol bias, empty map,
determinism, very-tight budget), `_parse_repo_file` (unsupported
extension, markdown, python defs + refs), `_walk_calls` (bare call
extracted, attribute call not extracted), and `_pagerank` (dangling node,
personalization, empty). `tests/test_build_repo_map.py` (added by subtask
test-001) provides a focused HAS_TREESITTER-gated supplement: symbol graph
(defs, class defs, ref edge, keys shape, relative-path invariant), mtime
cache (cache dir created, sentinel cache hit, changed file re-parsed,
only-changed file re-parsed with sentinel for unchanged), and graceful
degrade (empty file, binary file, empty repo, skip-.git/node_modules).
Uses a `pytestmark` module-level skip gate so CI without
tree-sitter-language-pack skips all tests cleanly.

The P6 Layer A wiring — `phase_plan` ctx injection — is tested in
`tests/test_phase_plan_repo_map_ctx.py`: repo-map enabled path (ctx
contains `repo_map` string, non-empty, JSON-serializable, contains known
symbol names, seed_files from `task_file_items` respected); skip path (ctx
omits `repo_map`, baseline keys present, values match inputs); empty-repo
degrade (`_rank_repo_map` returns `""` → key omitted); exception-swallow
degrade (`_build_repo_map` raises → exception caught, ctx emitted without
`repo_map`).

The P1 Layer C wiring — `phase_plan` recursion expansion — is tested in
`tests/test_phase_plan_recursion_wiring.py`: source-coupling guard
(`phase_plan` source contains `_recursive_decompose(` at depth=0,
reassigns `plan["subtasks"] = leaves`, expansion loop precedes final
logging); integration — one oversized subtask (stubbed
`_recursive_decompose` → two leaves) → `plan["subtasks"]` has 2 entries;
two first-pass subtasks → `_recursive_decompose` called once per subtask;
well-fit leaf pass-through (stub returns input unchanged →
single-element `plan["subtasks"]`); empty-subtasks plan not touched
(`_recursive_decompose` never called, subtasks stays `[]`).

## Plan-instruction-adherence gate

The plan-instruction-adherence gate's worker registration (schema,
prompt, model/effort defaults) is tested in two files mirroring the
`fit_judge`/`splitter` pair above. `tests/test_adherence_judge_schema.py`
covers `SCHEMAS["adherence_judge"]` — required fields
(`user_prescribed_a_procedure`, `instruction_adherence`, `violations`,
`rationale`), `instruction_adherence` bounds (0–10), the deliberate
absence of a nested `confidence` sub-object (this worker is itself the
independent check that replaces a self-report, so a self-confidence axis
would reintroduce the self-grading bias the gate exists to remove),
instance acceptance, JSON serializability, and wiring (`adherence_judge` in
`WORKER_TYPES`, absent from `MODEL_DEFAULT_PER_WORKER` so it resolves to
sonnet, `EFFORT_DEFAULT_PER_WORKER` entry at `"medium"`, prompt file exists).
`tests/test_resolve_adherence_judge_model.py` covers the model/effort
resolution precedence chain (mirrors `test_resolve_fit_judge_model.py`),
asserting the sonnet default. **History:** an earlier Sonnet generation
was empirically falsified here (false-positived a legitimate plan), which
required pinning this worker to opus, and a separate opus
*understanding*-framed judge rubber-stamped the incident too (only the
ADHERENCE frame was validated, independent of tier). Both gaps are
understood to have closed for Sonnet 5 (DESIGN §5 *Opus-judgment,
sonnet-workhorse (historical)*); re-run the calibration before
reintroducing a per-worker opus override if this gate ever regresses.

### Deterministic prescribed-command-coverage floor

The deterministic PRIMARY layer of the same gate,
`check_prescribed_command_coverage(prescribed_procedure, subtasks) ->
list[str]` (pure JSON→verdict set logic, no NL parsing), is tested in
`tests/test_prescribed_cmd_coverage.py`: the motivating incident shape
(prescribed `recon browser`/`recon generate`, no subtask's `runs_commands`
covers either → both fire), a goal-only task (`is_prescribed=false` or
`commands=[]`, including `None`/`{}`) staying silent, paraphrase coverage
(normalized lowercased/stopword-filtered token-SUBSET matching, e.g.
"barnacle recon browser" covers "recon browser" — not exact string
equality), full/partial coverage, no-subtasks-at-all firing for every
prescribed command, tolerance of missing/empty `runs_commands` and of
non-string/blank prescribed commands, case-insensitivity, and a negative
control proving a shared-stopword-only overlap doesn't falsely mark a
command covered. The gate's advisory-vs-gating outcome — distinct from
the G3 `decomposition_quality`/`task_understanding` pair (the *planner's*
self-report axes, not the adherence floor) — is pinned in
`tests/test_check_functions.py`'s `TestAdherenceGateAdvisoryVsGating`: a
prescribed-and-uncovered command gates (`PRESCRIBED_CMD_UNRUN`), a
goal-only task and a fully-covered command never gate, and
`check_planner_output` carries no separate adherence axis to demote,
since the floor is wired only into `phase_adherence_gate`.

### Task-coverage gate (advisory) and migration_targets cross-check

The task-coverage gate is **advisory** (2026-08-04). Its deterministic
floor, `check_required_items_coverage`, was deleted: it required one
subtask's token set to be a SUPERSET of a required item's, and across
every run that ever carried `required_items` it passed **0 of 102
items** — a 100% false-positive rate with no true negative. It also
violated the *Language-to-JSON* rule, since `required_items` are
LLM-written sentences. Its judge is retained but non-terminal: re-invoked
on identical input it returned a different finding set 85% of the time
(n=20), intersection across samples empty. `tests/test_phase_planning_coverage_gate.py`
pins the advisory contract and the floor's absence.

`migration_targets` carries a gap of the same shape — optional on the
subtask schema, silently no-op when a planner omits it — closed by a
narrow, same-worker cross-check: `performs_replacement: bool` on the
subtask schema (sibling, not nested inside `migration_targets` —
`additionalProperties: False` forces this), and
`_check_migration_targets_declared(subtasks)` flags
`MIGRATION_TARGETS_MISSING` when `performs_replacement=true` but
`migration_targets` is empty. Tested in `tests/test_migration_surface.py`'s
`TestCheckMigrationTargetsDeclared` (contradiction fires, both-empty and
both-populated stay silent, independent per-subtask evaluation) and
`TestPerformsReplacementSchema` (field shape, optional, sibling-not-nested).
Explicitly **not an independent witness** (DESIGN §8 and the check
function's own docstring): both signals come from the same
non-adversarial planner self-report, so it closes "forgot to fill the
field," not "consistently wrong on both fields."

### phase_adherence_gate wiring and end-to-end regression

The gate wiring — `phase_adherence_gate`, run after `phase_overlap_judge`
and before `_schedule()`/`_validate_plan`, composing the deterministic
floor and the `adherence_judge` behind `_run_checked_loop` — is tested in
`tests/test_phase_adherence_gate.py` (22 tests): source-coupling wiring
pins (floor+judge both run; a low result routes through the retry path
via a re-invoked `phase_plan`; a `WorkerError` never discards the plan;
call site precedes `_schedule()`/`_validate_plan`, ordered after
`phase_overlap_judge`) and behavioral tests against stubbed
`claude_p`/`phase_plan` (skip-flag and not-prescribed short-circuits;
clean plan passing without re-plan; low-adherence round triggering
exactly one re-plan that converges; exhaustion `die()`ing with unresolved
violations; the two `WorkerError`-every-round degrades — clean floor
unmodified, violating floor still `die()`ing).

The two-stage gate's composed end-to-end behavior is locked separately in
`tests/test_adherence_gate_e2e.py`: a synthetic incident shape (prescribed
`[foo:build, foo:generate]`, no subtask covers `foo:generate`) drives
`check_prescribed_command_coverage` directly to prove the floor fires
first, then drives the full `phase_adherence_gate` (opus `adherence_judge`
stubbed, mirroring `test_dep_capture_worker.py`'s `_invoke` stub) to prove
it re-plans exactly once and converges on a plan the floor accepts; a
synthetic ordinary shape (`prescribed_procedure` absent or
`is_prescribed=false`) proves zero `claude_p`/`phase_plan` calls — the
corpus-validated 0/21-false-positive result this gate protects.

The empirical calibration behind the threshold — real opus judge runs
against the cruiselines incident plan and a 21-run corpus, finding
`is_prescribed=true AND (floor violation OR low adherence)` fires on the
incident and stays silent on ordinary goal-only tasks with 0 false
positives — is frozen as a no-live-LLM fixture in
`tests/test_adherence_gate_regression.py`, distinct from
`test_prescribed_cmd_coverage.py` (floor in isolation) and
`test_phase_adherence_gate.py` (phase wiring): canned classifier/planner
JSON under `tests/fixtures/adherence_gate/{incident_plan,legit_plan}.json`
drives both the floor and the full two-stage composition. Pinned: issue
count on both fixtures; incident floor naming both unrun commands; legit
floor silent; incident fixture `die()`ing after exhausting the re-plan
budget; legit fixture never invoking `claude_p`/`phase_plan` at all; and a
frozen-score separation test (`test_incident_vs_legit_judge_scores_are_cleanly_separated`)
catching threshold drift even when the fire/silent outcome doesn't change.

### Source-of-truth delivery: a fixture that pinned one branch and reported both

`tests/test_gather_answers_validation.py` asserted the source-of-truth
contract across all three values and passed, while its `state` fixture
hardcoded `"needs_source_of_truth": True` — exercising only the branch
where the classifier flagged the question. On the other branch
`gather_answers` wrote nothing and consumers fell back to a **hardcoded
`"codebase"` literal**, silently overriding an explicit
`--source-of-truth research`; measured, 74 of 196 corpus runs took it.
The fixture is now parametrized over both values: reintroducing the
guard fails 6 tests, all on `no_needs_sot`, including
`[no_needs_sot-codebase]`, since under the guard `gather_answers` writes
no key and the lookup raises. **The trap that a `codebase`-only
assertion is answered by the very literal it exists to remove lives one
layer down, in the consumers** (`phase_plan` / `_write_plan` /
`_compose_pr_via_llm`), which `tests/test_source_of_truth_delivery.py`
targets — its spec-file check loops over `research` too, and its
`_effective_source_of_truth` table carries a row where `answers` and the
preference *disagree* (an agreeing-only table passes a helper that
ignores `answers`). The delivery file also source-couples three of the
four `State`-holding consumers (`phase_plan`, `_write_plan`,
`_compose_pr_via_llm`) to `_effective_source_of_truth(st)` (only the
first two ever carried a `"codebase"` default, so the third only fails
on presence — parametrized anyway as a forward guard). The fourth reader
is `phase_reconcile`'s, nested in the `_check_unresolvable` closure and
pinned by `tests/test_unresolvable_die_message.py` via AST-resolved
binding. **That the count here reads "four" at all is a guard's product,
not care's**: this doc said "all three" while the fourth reader landed in
the same commit, unnoticed until `tests/test_effective_sot_consumers.py`
derived the set from the call sites — an enumeration nested inside a
closure is invisible to an AST walk over module-level `def` bodies alone.
Two further readers can't use the helper and are documented instead:
`compose_pr_body` takes a plain `state: dict`, and
`scripts/host-finalize.sh` reads the key with `jq` — pinning the writer
alone leaves the value reaching nothing (the **deleted**
`test_resolve_aws_prefs.py` trap).

### Unresolvable-die message and reconciler payload field drift

`tests/test_unresolvable_die_message.py` covers the phase-2½ abort text
as behaviour, not cosmetics: against 5 simulated operators given a real
failure, the old wording sent **5 of 5** to widen the scope fence and
**0 of 5** to remove the offending criterion — the reverse of the correct
repair. The `--source-of-truth` bullet is **demoted and conditioned**,
not deleted: DESIGN §11 calls narrowing the preference *historically*
the escape hatch, and the bullet still fires when the effective value
isn't `codebase`. Operators ignored it 0/5 in both arms; the *useful*
bullet is what moved them, pinned with an anti-vacuity partner requiring
the text to say widening is often wrong, plus a guard that the stated
shape count matches the bullets (the first draft said "two shapes" and
printed three). The message was extracted to a module-level pure
function first — it had been inline in a closure, which is why the prior
test **re-synthesized the closure body** with a local stub calling
`leerie.die("test-die: …")`, a copy that passed regardless of the real
code.

`tests/test_reconciler_payload_fields.py` guards a prompt↔code drift of
the same family: `prompts/reconciler.md` scopes `conditional_drop` to
signals in `intent`/`scope_note` while the payload shipped only `intent`,
making half the signal surface invisible. The *shipped-fields* check
derives its field list from the prompt text at test time, with a
guard-the-guard beside it (`test_conditional_drop_rule_still_names_both_halves`,
pinning `{intent, scope_note}`), so a third signal field added to the
prompt fails it. Payload keys are read **structurally** off the
`subtask_views.append` dict literal — a first draft scanned
`ast.unparse` output for `"scope_note": s.get(` and failed against
correct code, since the unparser emits single quotes.

### Planner extent fencing and the fence-probe sandbox harness

`tests/test_planner_extent_out_of_scope.py` gained the third
`extent: external` kind. Its load-bearing assertion is an **ordering**
one — the fence question must precede the connector question, since
"could a connector subtask produce this?" answers *yes* for a fenced
code change; a test asserting both sentences merely exist passes against
the unfixed file, which already contains the connector one. Every prose
guard normalizes whitespace through `_norm`, so re-wrapping a
hand-wrapped markdown line isn't a false alarm; the absence guard
matters most, since an un-normalized one fails silently across a line
break. Because the prompt is advisory, those guards prove only that the
words are present: the behavioural evidence is a sandbox experiment
scoring the pre-fix prompt at 1/6 and the as-shipped wording at 17/18,
p = 0.00081. The harness ships as `tests/manual/planner_fence_probe.py`
— **not** collected by pytest, same arrangement as
`tests/fixtures/incident_2026_07_19/generate.py`, since it spawns real
`claude -p` workers. It extracts the rules from the live
`prompts/planner.md` and must run against a **sandbox copy** with
planning docs removed: two earlier attempts were contaminated by a task
doc corrected *after* the failing run. **Re-run it before trusting an
edit to that section** — the first re-validation came back 5/6 where the
design draft had scored 6/6, so the sample was extended rather than the
difference assumed away. Related: `_demote_unresolvable_with_external_twin`
has **never fired in 258 recorded runs** — every measured improvement
here has come from the prose, not the code backstop.

### Id-vanishing `depends_on` rewrite

The id-vanishing `depends_on` rewrite (DESIGN §5 *Id-vanishing
operations* — every op removing a subtask id owes the plan a rewrite of
inbound references; the tag channel self-heals via inherited `provides`,
so only the id channel dangles) is tested across five files.
`tests/test_remap_vanished_deps.py` unit-tests `_remap_vanished_deps`:
fan-out (a vanished parent → every leaf), prune (`id → []`), empty-mapping
no-op, dep-absent-from-mapping pass-through, dedup-after-rewrite and
two-vanished-ids-sharing-a-successor (mirrors `_apply_overlap_merge`),
and the `repl != sid` self-reference guard — pinned but **currently dead
code**, unreachable because `_schedule()` already die()s on a planner
self-edge before recursion runs; retained to match
`_apply_overlap_merge`'s discipline for future callers.

`tests/test_recursive_decompose.py` covers the intra-generation remap —
the seam `phase_plan` can't see: a splitter child declaring `depends_on`
on a sibling whose id then vanishes when that sibling splits again,
asserting the survivor fans out to the terminal ids; plus the
migration-path no-op, driving a **hostile** label-only worker injecting
sibling deps and proving `_migration_child` discards them (children keep
the parent's `depends_on`/`provides` verbatim on the ~84% path).
`tests/test_phase_plan_recursion_wiring.py` covers the cross-subtask
remap: the reported regression (a sibling of an expanded parent fans out
to all leaves and `_validate_plan` no longer die()s — the gate that
killed a real run after full planner spend), a dep on an unexpanded
subtask left untouched, dedup when a sibling already names a leaf.
`tests/test_filter_satisfied_subtasks.py` and
`tests/test_filter_offtree_subtasks.py` cover the two phase-3 soft-drop
filters, which vanish ids the same way: dropped-id inbound refs pruned,
`_validate_plan` survives end-to-end, and a no-drop run leaves
`depends_on` byte-identical.

### Planning checkpoints: snapshot, decompose-crash barrier, schedule determinism

`tests/test_plan_snapshot_wiring.py` pins `plan_snapshot` by source
inspection: the assignment follows `_schedule()`, precedes **both**
`check_budget_feasibility` and `_validate_plan` (a die() at either
otherwise discards the whole planning spend), is deliberately not
`_write_plan`, and round-trips through a real `State.save()`.

`tests/test_decompose_snapshot.py` is `plan_snapshot`'s sibling for the
D3 crash barrier: a `WorkerError` from `_recursive_decompose`'s
`fit_judge` call degrades the node to a leaf rather than discarding
sibling subtasks' completed decisions. A `WorkerError` from the
coupled-minority `splitter` call degrades to a leaf the same way —
`TestSplitterCrashBarrier` pins this as D3's surviving half, since the
`fit_judge` guard alone left it unguarded. `phase_plan`'s expansion loop
persists `st.data["decompose_snapshot"]` after each top-level subtask, so
a later crash still preserves earlier leaves; a normal run's final leaf
count matches `plan["subtasks"]`; and `test_decompose_snapshot_precedes_the_die_gates`
pins `_run_phases` calling `phase_plan` strictly before
`check_budget_feasibility` and `_validate_plan`.

The safety-by-construction property the resume checkpoint design rests
on — that `_schedule()` (`:17334`) re-sorts every wave by subtask id
(`wave = sorted(...)`, `:17374`), making the wave partition a pure
function of the dependency graph plus lexicographic ids — is pinned
directly, no state/stubs/async, in `tests/test_schedule_determinism.py`:
a multi-domain fixture (intra-domain `depends_on` and cross-domain
`requires`/`provides`) produces identical `waves` and subtask-id sets
across a fresh call, a JSON round-trip, reversed plan order, and
reversed subtask order. A companion test asserts every wave is
lexicographically sorted directly — round-trip equality alone doesn't
kill a `sorted(...)` removal (unsorted set iteration is self-consistent
within one process), so the per-wave sortedness check is what actually
fails when `sorted(...)` is removed at `:17374`.

### Resumable-planning checkpoint keys and the STATE_FIELDS discipline

The resumable-planning checkpoint keys (`plans_after_classify`,
`plans_after_plan`, `plans_after_reconcile`, `plans_after_overlap_judge`,
`plans_after_adherence_gate`, `plans_after_filters`,
`satisfied_probe_cache` — DESIGN §6 "Resumable planning") are pinned by
name in `tests/test_resumable_planning_keys.py`, on top of the generic
bidirectional parity `tests/test_state_fields.py` already enforces: each
key is present in `leerie.STATE_FIELDS` and has a row in
IMPLEMENTATION.md §8's field table, plus a regression pin that the table
no longer carries the old "A run that died on the preflight is not
resumable" claim now that `plan_snapshot` makes a budget-check-stopped
run resumable. bugfix-002 registered the keys; resume-rehydration code is
separate (bugfix-004). `tests/test_planning_checkpoint_keys.py` adds a
real `State.save()`/on-disk JSON reload round-trip with all seven
checkpoint keys populated, plus a `State.load()` round-trip proving the
reloaded in-memory `.data` dict — not just the on-disk artifact —
reproduces every key byte-equal.

**Contributor discipline for adding a new checkpoint/state key:**
`STATE_FIELDS` (`orchestrator/leerie.py:259`) is a static allowlist
checked by `tests/test_state_fields.py`, not a runtime filter —
`State.load()` reads the whole on-disk `state.json` unconditionally, so
an undeclared key isn't silently dropped on `resume`; instead
`test_state_fields.py::test_every_st_data_write_is_declared` fails the
moment an undeclared `st.data["x"] = ...` write lands — though that
guarantee held for the **subscript form only** until 2026-08-10.
`_runtime_field_writes` matched the run-init dict literal with
`re.search(r"st\.data\s*=\s*\{(.*?)\}", ...)`, and two bugs compounded:
no word boundary, so `bst.data = {}` (the `_BackstopState` stub) matched,
and `re.search` returns the **first** match, whose non-greedy body
captured **zero characters**. Measured before the fix: the matcher saw
67 keys where a correct one sees 70, blind to exactly the three
literal-only keys (`task`, `started_at`, `worker_count`). Now an AST
walk, which also kills the `bst` false match by construction.
`test_state_fields_matches_spec_table` fails if the IMPLEMENTATION.md §8
table and `STATE_FIELDS` drift out of sync. The resumable-planning
checkpoint keys additionally get named guard-the-guard pins so a future
refactor dropping one of these seven specifically fails with a message
naming the checkpoint feature, not a generic diff. The practical rule:
any new `st.data[...]` write must land in the same commit as its
`STATE_FIELDS` entry and its IMPLEMENTATION.md §8 row, or CI catches it
immediately.

### Checkpoint-writing order and resume re-entry

The checkpoint-writing half — `_run_phases`'s fresh-run branch persisting
each `plans_after_*` key immediately after its producing phase returns —
is pinned in `tests/test_plans_after_checkpoints.py` via
`inspect.getsource(leerie._run_phases)` (driving `_run_phases`
end-to-end is infeasible: it spawns real workers, shells to git/preflight).
Pinned: all six `plans_after_*` keys appear as `st.data[...]`
assignments; each is followed by `st.save()` within 200 chars; each sits
strictly *after* its phase's call — never at entry, the same
"`current_phase` stamped at entry, not completion" trap `plan_snapshot`
guards against; `plans_after_reconcile` precedes the `_detect_no_work`
short-circuit and `plans_after_filters` precedes both
`satisfied_no_work` and `_schedule()`; the six keys' first-occurrence
order matches pipeline order; `plans_after_filters` precedes
`plan_snapshot`.

`tests/test_planning_checkpoint_ordering.py` is a second, independent
pin of the same write-ordering invariant, plus the resume-cursor's
gating on checkpoint-key presence rather than `current_phase`, and the
earliest re-entry gate keying on `waves`/`categories` presence.
Deliberately overlapping rather than folded in: the standalone
regression guard for the single highest-severity trap in this feature (a
checkpoint written at phase entry would mark an incomplete phase done),
kept small so it can't be diluted by unrelated changes elsewhere.

The re-entry (`resume`-consuming) half — that a `state.json` checkpointed
through phase K reloads and re-enters at K+1 without re-invoking any
completed phase's worker — is pinned behaviorally in
`tests/test_resume_planning_reentry.py`. It drives real `_run_phases`
end-to-end with every phase stubbed via call-counting monkeypatches, a
stubbed `phase_execute` raising a sentinel exception, and asserts, per
`plans_after_*` checkpoint present, that every phase up to and including
it is absent from the call log and every phase after ran exactly once.
`TestPerPhaseRoundTrip` covers all six boundaries (classify → plan →
reconcile → overlap_judge → adherence_gate → filters → schedule).
Anti-vacuity: completed phases are stubbed with counters that would fire
if called, and the fixture never pre-seeds downstream output. Also
pinned: `phase_provision`'s key-presence-not-truthiness resume-skip (an
empty `recipe: []` is valid completed state); the reported incident
directly (a partial `satisfied_probe_cache` and no `plans_after_filters`
resumes through to `_write_plan` instead of dying); post-scheduling
resume falling straight to `phase_execute`; budget-check resume
rehydrating `plan_snapshot` instead of the old "Plans are not persisted"
die; `_schedule()`-determinism end-to-end; an allowlist guard that every
checkpoint key read is in `STATE_FIELDS`; the old
`"did not reach the scheduling phase"` string gone from source; and a
state.json with zero progress still `die()`ing.

`tests/test_resume_planning_regression.py` is a narrower end-to-end lock
on top of the per-phase stub sweep: it drives `_run_phases` with only
phases upstream of `_filter_satisfied_subtasks`/`_schedule` stubbed,
leaving `_filter_satisfied_subtasks`, `_schedule`,
`check_budget_feasibility`, and `_write_plan` REAL against a real temp
git repo. (a) reproduces the reported incident verbatim: a partial
`satisfied_probe_cache` resumes, re-probes only uncached sids, reaches
scheduling with no die() — paired with a falsification test replaying
the retired `"waves" not in st.data` gate to confirm it would have died,
proving (a) exercises the fixed path. (b) reruns `check_budget_feasibility`
twice against the same `plan_snapshot` — once under a low cap (dies),
once raised on a fresh `State` reload — asserting `_write_plan` runs
exactly once with no upstream re-run. (c) asserts a `waves`-present
resume reaches `phase_execute` with zero planning-phase calls. (d)
covers both early-return guards, including checkpoints present but
`no_work_required` still winning. A final grep guard (prior art
`tests/test_ec2_launcher_dispatch_e2e.py`) asserts neither retired die()
string survives as a live `die(...)` call.

### satisfied_probe cache: writing, invalidation, and sibling-service

The `satisfied_probe_cache` checkpoint-writing half (bugfix-005) is
tested in `tests/test_filter_satisfied_subtasks.py`: a cache hit under
the CURRENT `base_sha` is consulted before `async with sem:` and
`claude_p` is never invoked; a fresh probe persists its verdict for BOTH
outcomes, carrying `satisfied`/`evidence`/`checked`/`base_sha`; the
`WorkerError` crash-keep path writes no cache entry; a cached verdict
whose `base_sha` differs from current `HEAD` is invalidated and
re-probed (the mid-run-sibling hazard — DESIGN §6); and THE REPORTED
FAILURE PINNED — a partial cache resumes, re-probes only uncached
subtasks, reaches scheduling. All 17 pre-existing tests unchanged (17 + 5
= 22 passing). `tests/test_satisfied_probe_cache.py` is a dedicated,
narrower pin for the same `probe_one` mechanism in isolation: a cached
`satisfied` verdict drops the subtask with ZERO `claude_p` calls, a
cached not-satisfied verdict keeps it with zero calls, an uncached sid is
probed exactly once with the verdict persisted for both outcomes, and a
`WorkerError` crash keeps the subtask while asserting the cache KEY is
ABSENT — the anti-vacuity discipline from the zombie-reaper harness
lesson. The same file pins the fix for a real mid-sweep data-loss defect
(2026-07-29 root-cause batch, PR #120): `probe_one` wrote each verdict to
`cache[sid]` in memory with no per-verdict `st.save()` — only the
post-`gather` aggregate save persisted — contradicting both commit
750ce33's message and DESIGN §6, both of which claim per-verdict
persistence; a pause mid-sweep silently lost every already-decided
verdict. `test_verdict_reaches_disk_before_the_sweep_completes` pins the
fix (`st.save()` immediately after `cache[sid] = {...}`); reverting the
added save fails the test.

The sibling-service half of the same incident batch — a satisfied-probe
drop blind to a surviving sibling's pending work invalidating the
criterion it just judged met — is pinned by two tests in
`tests/test_filter_satisfied_subtasks.py`:
`test_probe_payload_carries_surviving_siblings_excluding_self` (the
`sibling_surface`, built once per sweep, contains every other subtask
with non-empty `provides`/`files_likely_touched`, never the probed
subtask) and `test_sibling_invalidation_verdict_keeps_the_dropped_test`
(a test subtask the base tree already satisfies is NOT dropped when the
probe, given `surviving_siblings`, judges a sibling's pending work would
break it). The guidance lives in `prompts/satisfied_probe.md`'s "A
sibling's pending work can invalidate an already-met criterion" section,
scoped to the pre-schedule call site (`surviving_siblings` is absent from
the post-execution `_probe_criteria_satisfied_on_head` payload, since
HEAD there already reflects whatever siblings committed).

P10 (evidence-citation requirement — `prompts/satisfied_probe.md`'s
amended guidance that success criteria naming test file paths are judged
by coverage/convention, and that the probe cite the specific
file+assertion) is pinned at the mechanically-checkable layer:
`test_schema_requires_evidence_on_satisfied_true_verdict` asserts
`SCHEMAS["satisfied_probe"]` rejects a `satisfied=True` verdict missing
`evidence` or with non-string `evidence`, and accepts a well-formed
citation; `test_satisfied_probe_prompt_exists_and_nonempty` is a
structural check. Prompt prose itself is unasserted — only a live LLM run
can verify the probe follows the amended instruction (prompts are
advisory, code enforces).

### Wiring-gate severity and test-ownership advisories

enforcer; the warn only reduces how often a plan reaches it broken. A
companion `TEST_OWNERSHIP_RISK` advisory in `check_classifier_output`
flags when `testing` is selected alongside `bug-fixing`/
`feature-implementation`/`refactoring` in the same category set — a real
prior incident where one category set produced both the code change and
its own test assertions with no ownership split.
`tests/test_phase_wiring_gate.py::test_die_message_does_not_recommend_skip_overlap_judge`
pins the corrected `phase_wiring_gate` die() message: it no longer
recommends `--skip-overlap-judge` (that flag skips the earlier, distinct
phase 2¾ overlap judge and doesn't touch this gate — the old wording sent
an operator right back into the same die()). The same file pins that each
`wiring_defects` entry's `severity` is **asked for but not `required`**
(changed 2026-08-03): requiring it defeated its own purpose — a judge
omitting the field produced no schema-valid payload at all, so the gate
never ran; measured across the run corpus, every `wiring_judge`
invocation that never produced valid output (9 of 66) failed on this
field, accounting for all 18 failing submissions; relaxing it took
`wiring_judge` to 100% and the global never-valid count from 13 to 4.
Both consumers already tolerate absence, so an unlabelled entry **gates**
— matching DESIGN §8 *Findings carry a severity* ("the default is
gating"), with anti-vacuity coverage that a declared `latent_risk` is
still excluded from gating.

### Artifact-registry worker and satisfied-probe-cache invalidation

The `artifact_registry` worker (DESIGN §5) — a pre-planning worker
reading the task plus the global repo-map and emitting a small canonical
`{description, tag, path}` vocabulary injected into every planner's
context, softening (not replacing) the reconciler's tag-drift resolution
— is tested in `tests/test_artifact_registry.py` (23 tests): schema
validity, worker registration parity (in `WORKER_TYPES`, absent from
`MODEL_DEFAULT_PER_WORKER` so it resolves to sonnet,
`EFFORT_DEFAULT_PER_WORKER["artifact_registry"] == "medium"`),
model/effort resolution, phase behavior (`test_phase_returns_artifacts`,
`test_phase_drops_malformed_items` — missing `tag`/`path` dropped,
`test_phase_degrades_to_empty_on_crash` — a `WorkerError` on every
`_run_checked_loop` round degrades to `[]`), `--skip-repo-map` degrade
(only `ctx_dict["repo_map"]` build is skipped) plus the repo-map
grounding branch itself, unexercised by every other phase-behavior test
(`_make_state` always seeds `skip_repo_map=True`): `_build_repo_map`/
`_rank_repo_map` are called and a non-empty ranked map reaches the
worker's prompt when not skipped, never called when skipped, an empty
ranked map omits the `repo_map` ctx key, and a crashing
`_build_repo_map` degrades silently — ctx-injection wiring
(`test_phase_plan_injects_registry_into_ctx`), checkpoint ordering
(`test_run_phases_checkpoints_registry_before_plan` — runs between
`gather_answers` and the `plans_after_plan` block), and a
`State.save()`/reload round-trip.

`tests/test_satisfied_probe_cache_invalidation.py` is the real-moving-repo
counterpart to the `base_sha` invalidation case above: it builds a real
temp git repo and advances HEAD from sha A to sha B via a second commit,
mirroring a sibling run merging (or reverting) the deliverable between a
pause and resume (DESIGN §8 "the mid-run sibling case"). Both stale
directions are pinned: a stale `satisfied=True` at A mustn't silently
drop a subtask no longer satisfied at B, and a stale `satisfied=False`
mustn't silently keep one that's since become satisfied. A cache entry
with missing/malformed `base_sha` is treated as a miss and re-probed.
The falsifier is verified live: deleting the `cached.get("base_sha") ==
base_sha` comparison in `probe_one` (`orchestrator/leerie.py:7402`) fails
4 of the file's 5 tests with a stale drop/keep.

### Conformer/baseline hardening

The conformer/baseline hardening (DESIGN §9 *No clobbering the
implementer's work* + the base-tree baseline's `measured` field) is
tested across three files. `tests/test_clobbered_owned_files.py` covers
`_clobbered_owned_files` against real temp git repos (legit conformer
edit not flagged; revert-to-base flagged; deletion flagged; a file
outside the implementer's owned set never flagged; new file added not
flagged; the load-bearing round-0 snapshot test — a per-round HEAD misses
a round-0 clobber while the pre-loop `impl_head_sha` catches it;
empty-ref no-op), `_blob_sha`'s present/absent contract (missing-path
returns None, guarding the bare `git rev-parse <ref>:<path>` footgun),
`_rollback_conformer_commits` restoring clobbered content and dropping
the conformer commit (`TestRollbackRestoresClobber`), and source-coupling
guards that both `_run_conformance_phase` and `_run_final_conformance`
snapshot before the round loop and call the guard under
`strict_conformer`.

`tests/test_normalize_pip_installs.py` covers `_is_pip_install`/
`_normalize_pip_installs` (adds `--break-system-packages` to
`pip`/`pip3`/`python -m pip install` recipe entries): the incident recipe
entries, `-e .`, `python -m pip`, idempotency, non-pip/non-install
entries untouched, other fields preserved, and normalization running
before `prov["recipe"] = recipe` in `phase_provision`.
`tests/test_base_health_baseline.py` additionally covers `_runner_missing`
(`command not found`/`No such file or directory`), the `measured` field
on baseline axes (an unmeasurable axis surfaced as "could not measure,"
folded into neither GREEN nor RED), `measured` as mandatory with no
legacy default (a `passed: False` axis missing it isn't surfaced RED),
and the N8 fix — every BLT axis command is invoked as exact argv
`["bash", "-c", cmd]`, never a login shell (`-lc`), since a login shell
sources `/etc/profile`/`~/.bash_profile` and discards Docker-ENV-only
PATH additions (mise's shims dir) — a source pin, an argv-capture pin,
and a regression control reproducing the PATH-loss mechanism against
real subprocesses.

### AWS credentials, EC2 preflight, and the release workflow

The standalone AWS credential/profile/region resolver
(`scripts/remote/aws-credentials.sh`, EC2 runtime) is tested in
`tests/test_aws_credentials.py` by sourcing the real script against a
fake `$HOME` with fixture `~/.aws/config`/`~/.aws/credentials`/`~/.aws/sso/cache/`
files (mirroring `tests/test_fetch_branch_sh.py`'s source-and-call
pattern): explicit env-var credentials winning over a configured SSO
profile with a valid cached token; `AWS_PROFILE` selecting a named
profile over `[default]`; region precedence (`AWS_REGION` >
`AWS_DEFAULT_REGION` > profile `region` > die-with-hint); static
credentials; both `sso_session`-reference and legacy inline SSO config;
an expired SSO cache token and a never-logged-in profile both producing
the `aws sso login --profile <p>` hint; no `~/.aws` directory at all;
`AWS_PROFILE=nonexistent` not falling back to `[default]`; and
`--profile`/`--region` CLI flags overriding env-var equivalents. Pure
file I/O — no network, no `aws` binary, no boto3. Not yet wired into the
launcher's EC2 runtime path.

The EC2 runtime's host-side preflight (`scripts/remote/ec2-lib.sh`'s
`require_aws()`, modeled on `require_flyctl()`) is tested in
`tests/test_ec2_lib_sh.py` by sourcing the real script against a stubbed
`aws` binary (mirroring `tests/test_ensure_image.py`'s stubbed-flyctl
pattern): success when `aws` is present and `aws sts
get-caller-identity` succeeds; an actionable AWS CLI v2 install hint when
absent; the `aws sso login --profile <profile>` recovery hint (reusing
`bedrock_preflight()`'s vocabulary) when credentials are unresolvable;
profile resolution precedence (`--profile`, `LEERIE_AWS_PROFILE` over
`AWS_PROFILE`, `AWS_PROFILE` fallback) reflected in both the identity
call and the sso hint. Not yet wired into `RUNTIME=ec2` dispatch.

The release workflow's previously-untested embedded shell
(`.github/workflows/release.yml`) is covered in
`tests/test_release_workflow.py` against the raw YAML text (no pyyaml),
using the extract-the-real-text-at-test-time pattern from
`tests/test_config_verb.py`'s `_extract_config_arm`: a regex table (the
v0.9.62 squash-merge subject and every historical
`chore(release):` subject on `main`, run live rather than pinned to a
stale count) and structural pins that tag/release steps gate on
different `if:` conditions, the release step never references
`tagcheck`, `relcheck` exists and probes via `gh release view`, `gh
release create` carries `--verify-tag`, and a final end-state step
(gated on `success()`, not `always()`) is the job's last step and
asserts both artifacts exist.

### EC2 lifecycle: stateful stub, provisioning, and volume reaping

The resource-tracking `aws` stub state machine (`tests/ec2_stub.py`,
distinct from `test_ec2_lib_sh.py`'s argv-only `_stub_aws`) models EC2 as
a persistent state machine — `run-instances` creates a tracked instance
that `stop-instances`/`start-instances`/`terminate-instances` transition
through, `create-volume`/`delete-volume` likewise — so downstream tests
can assert on resource *leaks*, not just argv. It exposes `_stub_aws(dir)`,
`read_state(dir)`, `read_log(dir)`, and `leaked_resources(state)`.
Self-tests in `tests/test_ec2_stub.py` pin the state transitions,
`leaked_resources()` on clean/unclean teardown, multi-instance
independence, the real `aws` CLI's `--instance-ids i-1 i-2`
space-separated multi-value syntax (not a repeated flag), ordered
invocation logging, and a structural guard that the stub source contains
no networking imports (`socket`, `urllib`, `http.client`, `requests`,
`boto3`). Pure test fixture, importable ahead of the EC2 dispatch branch
landing. `ec2_stub.py` also implements `describe-instance-status`
(`InstanceStatus`/`SystemStatus` both `"ok"` for `running`,
`"initializing"` when seeded `status_ok: False`), consumed by
`wait_for_instance_ready()`'s poll-until-both-ok contract.

`scripts/remote/ec2-provision.sh` (`provision_instance()`,
`wait_for_instance_ready()`, `stop_instance()`/`terminate_instance()`,
`decide_ec2_teardown()`) is tested in `tests/test_ec2_provision.py`
against the stateful stub: required-var validation (missing
`LEERIE_EC2_AMI`/missing `aws` binary both fail closed); instance-id
export and `ec2-instance.json`/`run.json` sidecar writes on success;
id-parsing against real-shaped `run-instances` JSON; a failed create
leaking no resources and never registering the teardown trap;
`terminate_instance`'s no-op-on-empty-id idempotency; and
`decide_ec2_teardown`'s three-disposition classification (clean-exit
terminates, sync-failure leaves the instance running, SIGINT detaches,
unknown rc pauses), including `_try_fetch_state_for_ec2_teardown`
running before `terminate_instance` and teardown idempotency under
`LEERIE_TEARDOWN_DONE`.

`tests/test_ec2_volume_reaping.py` pins the EBS-volume side: DESIGN §6
"EBS volume lifecycle" case 1 (root volume only, AWS's implicit
`DeleteOnTermination=true` default) means there's no Fly-style
`destroy_volume()` reap path to test — instead this file pins the actual
leak-prevention mechanism (`run-instances` invoked with no
`--block-device-mapping`/`--block-device-mappings` override, at both
stub-argv and source-grep level against `DeleteOnTermination` appearing
in the call block), that `terminate_instance` is a true no-op on an
empty instance id, a full provision→terminate cycle leaking neither
instances nor volumes (with an explicit assertion no `create-volume`
call ever happens), and a structural regression guard that no
`destroy_volume`/`reap_volume`-shaped function exists anywhere in
`ec2-lib.sh` or `ec2-provision.sh`.
## EC2 seed-repo transport

The EC2 counterpart to `scripts/remote/seed-repo.sh` — `scripts/remote/
ec2-seed-repo.sh` (`ec2_seed_repo_clone`/`ec2_seed_repo_dirty`/
`ec2_seed_repo`, transported over `ec2-lib.sh`'s `ec2_tar_pipe`/
`ec2_remote_exec` instead of `flyctl ssh console`) is tested in two files
modeled on `tests/test_seed_repo_sh.py` + `tests/test_seed_repo_shallow_roundtrip.py`.
`tests/test_ec2_seed_repo.py` covers the transport-level contract against a
stubbed `aws` (decodes and locally executes `ec2_remote_exec`'s base64-wrapped
SSM command, rewriting `/work`/`/tmp/leerie-*` paths into the test's `dest`
dir — same technique as `test_ec2_transport.py`'s `_stub_aws_ssm`) and a
stubbed `ssh` (drains `ec2_tar_pipe`'s one-entry gzipped-tar payload for bulk
data, execs a real local `rsync --server` for rsync's `-e` transport):
preflight failures (missing instance id / ssh target / `USER_REPO` / `aws` on
PATH); a minimal repo round-trips to `/work`; both `aws` and `ssh` are
exercised and `flyctl` never appears in the transport log; `.gitignore`-
awareness plus `.claude/` force-inclusion via the rsync delta; the
`.leerie/config.toml` / `.leerie/Dockerfile` / `.leerie/.leerie-setup.sh`
whitelist (all other `.leerie/*` paths dropped); NFC-filename preservation
through a submodule bundle; and a stalled `ssh` transport (real, unstubbed
`timeout`) yielding a non-hanging failure. `tests/test_ec2_seed_repo_shallow.py`
reproduces the shallow-path host/instance commands directly (coupled to the
real script via `test_reconstruction_matches_source`, asserting the exact
clone/tar/checkout strings are present) to pin: checkout parity between the
shallow instance tree and the host tip, `.git/shallow` staying shallow,
NFC-filename survival, a fetch-back-by-branch-name round-trip whose merge-base
equals the host tip (PR-diff correctness), and `_seed_branch_shallow_safe`'s
shell-injection gate (safe vs. unsafe branch names, including the live
`__PARENT_MATERIALIZE__`/`__CLEANUP_TMP__` placeholder tokens) invoked against
the real function rather than a reproduction of it.

## EC2 seed-auth transport

The EC2 counterpart to `scripts/remote/seed-auth.sh` —
`scripts/remote/ec2-seed-auth.sh`'s `ec2_seed_auth()` — is tested in
`tests/test_ec2_seed_auth.py`, modeled on `tests/test_seed_auth_sh.py` and
reusing `tests/test_ec2_seed_repo.py`'s stubbed-`aws`/stubbed-`ssh` transport
harness (the `aws` stub rewrites `/home/leerie` into the test's `dest` dir;
the `ssh` stub drains `ec2_tar_pipe`'s gzipped-tar-of-`$STAGE` payload into
the same rewritten dest): a `$STAGE` dir containing `.claude/`,
`.claude.json`, and `.gitconfig` round-trips to the instance's home dir with
ownership fixed to `leerie:` (asserted via a `chown_log` sink so the test
observes the real script issuing the call, not just its source text); the
`CLAUDE_CODE_OAUTH_TOKEN` fallback writing a valid single-token
`.credentials.json` when `$STAGE` has none; `plugins/cache` and
`plugins/marketplaces` excluded from the tar (both a positive check that the
exclude list matches `seed-auth.sh`'s original and a check that files outside
those dirs are not swept up); preflight failing closed on missing
`LEERIE_EC2_INSTANCE_ID` / `LEERIE_EC2_SSH_TARGET` / `STAGE` / `aws` on PATH /
credentials-or-token / git identity; git identity written to
`/home/leerie/.gitconfig`; `flyctl` never appearing in the transport log
while `aws`/`ssh` both do; and a stalled transport (the process-group-killing
`_stub_timeout` imported from `tests/test_ec2_transport.py` — the local no-op
passthrough stub would hang for the full sleep) yielding rc 124/137 rather
than hanging, bounded by `LEERIE_SEED_TIMEOUT_S`.

## EC2 instance lifecycle (provision / wait / stop / terminate / teardown)

The EC2 instance lifecycle itself (`scripts/remote/ec2-provision.sh`'s
`provision_instance()`/`wait_for_instance_ready()`/`stop_instance()`/
`terminate_instance()`/`decide_ec2_teardown()`) is covered across two files.
`tests/test_ec2_provision.py` covers the broader surface: instance creation,
the running+ok readiness poll, stop/terminate idempotency on an empty
instance id, and the sidecar writes. `tests/test_ec2_decide_teardown.py` is
the dedicated, deeper pin for `decide_ec2_teardown()`'s
`$LEERIE_REMOTE_EXIT_RC` classification table — the highest-consequence EC2
behavior, mirroring `tests/test_decide_teardown_auto_finalize.py`'s Fly
coverage: each clean-exit rc (0/10/11/75) syncing state via
`_try_fetch_state_for_ec2_teardown` before calling `terminate_instance`; a
sync failure on any clean-exit rc leaving the instance `running` with no
`terminate-instances`/`stop-instances` call ever reaching the `aws` stub's log
(the one-way-ratchet invariant — destroy-then-fetch would make paid-for LLM
work unrecoverable); rc=130/143 taking the detach-banner arm without pausing;
any other non-zero rc stopping (never terminating) the instance and recording
`pause_reason` in the run sidecar; the fetch-before-terminate ordering
independently verified via a hook asserting the instance is still `running`
when `_try_fetch_state_for_ec2_teardown` runs; and `LEERIE_TEARDOWN_DONE`
idempotency surviving a double-fire (INT then EXIT) in both directions even
when `LEERIE_REMOTE_EXIT_RC` is clobbered between the two calls.

## EC2 fetch-branch streamback

The EC2 stream-back counterpart to `fetch-branch.sh` —
`scripts/remote/ec2-fetch-branch.sh`'s `fetch_state_ec2()` — is tested in
`tests/test_ec2_fetch_branch.py`, modeled on `tests/test_fetch_branch_sh.py` +
`tests/test_fetch_branch_leerie_streamback.py` and using
`tests/test_ec2_seed_repo.py`'s stubbed-`aws`/stubbed-`ssh` transport harness
(`ssh` streams the private download helper `_ec2_fetch_ssh`'s raw
remote-command stdout straight back, since `ec2_tar_pipe` itself is
upload-only): a branch committed on the instance round-trips to the host as a
fetchable bundle whose tip matches the instance-side tip; the run-state tar
extracts under `LEERIE_STATE_HOST_DIR` (or `USER_REPO/.leerie` by default)
and the `no_push` mechanism flag is stripped only on the branch-present path
(preserved as intent on the cleared-but-empty terminal-state path, same
conditional as `fetch-branch.sh`); `.leerie/config.toml` and
`.leerie/Dockerfile` stream back when the host has neither, are never
clobbered when the host already has one, and are non-fatal when absent on the
instance; and both `aws` and `ssh` appear in the transport log while `flyctl`
never does.

## EC2 SSM launch/attach

The launch/attach counterpart to `flyctl ssh console` — `scripts/remote/
ec2-ssm.sh`'s `ec2_launch_detached()`/`ec2_attach()` — is tested in
`tests/test_ec2_ssm.py` against a stubbed `aws` binary modeling
`ssm start-session`'s two quirks: it always exits 0 regardless of the wrapped
remote command's real exit status (the documented session-manager-plugin
limitation both `ec2_remote_exec` and this file work around via an
rc-sentinel), and it is a genuinely interactive session that drains its own
stdin and execs it as the bootstrap interpreter's program. Pinned: both functions issue
`aws ssm start-session --target <id> --document-name AWS-StartInteractiveCommand`;
rc=75 (the flock-loser smart-resume pivot) and other nonzero remote rcs
survive the round trip uncorrupted; both fail closed (rc 1, actionable
stderr, no `aws` call) on an empty `LEERIE_EC2_INSTANCE_ID`; a stalled session
yields 124/137 via the same `_seed_timeout_prefix` convention
`ec2_remote_exec` uses; `--profile`/`--region` passthrough; a payload well
over SSM's ~4 KB `--parameters` ceiling still round-trips cleanly since only
the interpreter name (`python3 -` / `sh -s`) goes in `--parameters` and the
real payload travels over the session's stdin; `ec2_attach`'s `sh -s`
bootstrap is verified by decoding the base64-wrapped `command=[...]` value
rather than asserting on plaintext no longer in the log; and double-sourcing
is idempotent and does not clobber `ec2_remote_exec`. `flyctl` never appears
in the transport log. Also added to `tests/test_ec2_bash32_portability.py`'s
`_EC2_SCRIPTS` list for bash 3.2 sourcing coverage.

## EC2 launcher dispatch branch (`RUNTIME=ec2`)

The launcher's `RUNTIME=ec2` dispatch branch itself — the seam none of the
above can see, since they test `ec2-lib.sh`/`ec2-provision.sh` standalone
rather than the `leerie` launcher's own dispatch — is covered in
`tests/test_ec2_e2e_provision.py`: the branch is extracted verbatim from the
launcher and run against `tests/ec2_stub.py`'s
resource-tracking `aws` stub. It pins that `require_aws`'s
`sts get-caller-identity` call precedes any `ec2 run-instances` call by call
index (mirroring `tests/test_provision_volume.py`'s ordering discipline), and
that a failing credential probe aborts the launch non-zero, emits the
`aws sso login --profile <p>` hint, and leaves zero tracked instances and
volumes in the stub's state — both with provisioning wired in after the
dispatch block and with the dispatch block alone, so the gate is pinned as
the branch's own contract independent of what runs after it. The module also
defines the shared bash harness (stub-on-PATH + launcher invocation helpers)
that sibling EC2-dispatch test modules import. A dedicated
`test_successful_provision_leaves_exactly_one_instance_and_no_orphaned_volume`
pins the provision-success resource count against the stub's *tracked state*
rather than argv/log line counts: exactly one instance (not zero — a no-op
regression; not two — a double-provision regression, both falsified live
against hand-broken harness variants) and zero tracked volumes, since
`provision_instance()` never calls `create-volume` — root EBS is implicit via
`run-instances` with AWS's own `DeleteOnTermination=true` default (DESIGN §6
"EBS volume lifecycle" case 1) — so any tracked volume on this path would by
construction be an orphan.

## Worker prompt transport (stdin, not argv)

The worker-prompt-over-stdin transport (docs/IMPLEMENTATION.md §3 "User
prompt transport — stdin, not argv" — a single argv element cannot exceed
Linux's `MAX_ARG_STRLEN`, 131,071 bytes, and reconciler/plan_overlap_judge
payloads routinely exceed that on their own, crashing with a raw execve
`OSError: [Errno 7] Argument list too long`) is pinned in
`tests/test_prompt_over_stdin.py`: `build()` emits no positional argument
after `-p` at any payload size, so no argv element it constructs can carry
the prompt (true by construction, not merely measured for one size); a
positional prompt would silently win over stdin with no error, so
`test_no_positional_prompt_after_dash_p` pins the element immediately after
`-p` is always a flag; the retry path (`build(retry_note)`) routes the
concatenated retry text through `stdin_data` too, not argv; `_invoke` passes
`stdin=PIPE` when `stdin_data` is given and `stdin=DEVNULL` otherwise
(direct-cmd callers with no prompt to feed, e.g. the preflight smoke test,
are unaffected); and `test_real_subprocess_150kb_stdin_no_deadlock` spawns a
real `python3` child and feeds it a real 150,063-byte payload over a real OS
pipe via `_invoke`'s concurrent `_feed_stdin` task, proving no deadlock
between the feeder and `_read_stream`/`_drain_stderr` for a payload well over
both a single pipe buffer and the single-argv ceiling this fix routes around.
`tests/test_replay_capture.py` and `tests/test_no_result_event_retry.py` were
updated in the same change to assert against `stdin_data` instead of an argv
element, since both stub `_invoke` to inspect what `claude_p` constructs.

### The stdin-feeder ordering deadline

Routing the prompt over stdin created a **deadline** the argv form never
had, guarded by `tests/test_stdin_feeder_ordering.py`. `claude -p` waits a
hard-coded 3 s for its first stdin byte, then drops its `data` listener, so
a late write is DISCARDED and the worker exits 1 on `Input must be
provided`. leerie made two SYNCHRONOUS broker round-trips between spawn and
first write, each bounded by `_cgroup_request`'s 5 s timeout — a stall
larger than the deadline in front of it. Measured: **218 workers lost,
12.4% of all invocations in the affected runs**, retried up to 4x each
against `max_total_workers`, spanning v0.9.95–v0.16.0. **Both halves of the
fix are load-bearing**: a reproduction harness scored all four combinations
and only `create_task` at the spawn AND `to_thread` on both broker calls
delivers the prompt — hoisting alone fails (blocked loop never schedules
the task), `to_thread` alone fails (write lands after the child is gone).
`test_only_both_halves_deliver_the_prompt_in_time` drives all four
combinations behaviourally rather than trusting source order. Two harness
traps: `_invoke_src` strips comments via `tokenize`, not a `#` heuristic (a
`#` inside a string literal would corrupt the result), since the region
names `_feed_stdin`/`await`/`_cgroup_enroll` in comments; and
`async def _feed_stdin():` contains `_feed_stdin()` as a substring, so a
bare `.count()` over-reports and the call-site scan excludes the
definition.

## Appended system prompt transport

The appended system prompt (docs/IMPLEMENTATION.md §3 "Appended system
prompt transport — file, with a probe + inline fallback" — the second large
argv element that compounds with the user prompt toward the same
`MAX_ARG_STRLEN` ceiling, worst-case on the overlap judge) is pinned in
`tests/test_append_system_prompt_file.py`:
`_append_system_prompt_file_supported()`'s supported/unsupported
classification (by stderr text — `"unknown option"` means unsupported, since
both outcomes exit non-zero and only the message distinguishes them),
fail-closed behavior on a missing `claude` binary or a probe timeout,
once-per-process memoization (a second call makes no further `claude`
invocation), and its own throwaway probe file being cleaned up; `build()`'s
branch on the probe result (`--append-system-prompt-file <path>` with the
temp file holding `system_prompt` verbatim when supported, the inline
`--append-system-prompt` when not); the temp file being removed once
`claude_p()` returns, on both the success path and an exception path (a
`TerminalAuthFailure` raised from inside the try/finally-wrapped retry loop —
the schema-key drift guard itself runs before the temp file is created, so it
needs no cleanup); and the retry loop reusing the same temp file across both
attempts rather than recreating it, since `system_prompt` is fixed for the
whole `claude_p()` call. `tests/test_replay_capture.py`'s two
system-prompt-plumbing tests (`test_args_match_capture_fields`,
`test_override_system_prompt`) pin the probe to unsupported via monkeypatch
so their argv assertions don't depend on whether the live `claude` CLI on the
test host happens to support the undocumented file flag.

## No-result-event retry

The no-result-event retry (DESIGN §6, `claude -p` exits 0 having streamed a
full session but never emits its terminal `result` event — upstream
anthropics/claude-code #8126/#1920/#74761, unresolved) is pinned in
`tests/test_no_result_event_retry.py`: `_invoke` returns a synthetic
`_leerie_synthetic: "no_result_event"` envelope rather than raising, so
`claude_p`'s 2-attempt loop absorbs it (a raised WorkerError propagated
past that loop and die()d the run non-resumably). The load-bearing test,
`test_synthetic_envelope_is_not_an_auth_or_quota_failure`, extracts the
**real** message from `_invoke`'s source via `ast` rather than a copied
fixture — `_is_auth_or_quota_failure` falls back to text markers on
`result`, so a hand-copied fixture passes while the shipping message
diverts every no-result retry into the tenacity backoff and burns the
whole `auth_retry_max_sec` budget (verified: the copied-fixture version
does **not** fail when the landmine is introduced; the ast-extracted one
does). Controlling leerie's own message is **not sufficient** — the
envelope interpolates the worker's **raw stderr** into `result`, so a
worker whose stderr merely mentions auth/rate-limiting trips the same
markers. Fix: an exemption in `_is_auth_or_quota_failure` for
`_leerie_synthetic` envelopes (the numeric `api_error_status` check still
runs first). `test_worker_stderr_cannot_trip_the_auth_classifier` pins it
against three realistic stderr payloads, and
`test_real_envelopes_still_match_the_text_markers` guards against
over-reaching. A source-coupling guard pins the synthetic return as the
**last** arm of the no-envelope block, since the nonzero-rc arm above it
covers leerie's own deliberate SIGTERM/SIGKILLs, which must never be
retried. `tests/test_warnings_before_die.py` pins that all four judgment
phases (classifier, provision, reconciler, plan_overlap_judge) log their
`_run_checked_loop` warnings **before** `die()`, since `die()` calls
`sys.exit()` and any loop after it is unreachable (falsified live:
reverting one site fails the guard).

### `_run_checked_loop` crash policy

`_run_checked_loop`'s crash policy is pinned in `tests/test_checked_loop.py`:
a `WorkerError` (infrastructure — PID exhaustion, OOM, a killed session) is
**retried** against the same `judgment_check_rounds` budget, because the
re-invocation is a fresh `claude -p` session with a clean PID table — true
for implementers but false for every `_run_checked_loop` caller until the
retry existed. A worker KILLED at its wall-clock ceiling is the same class
and is retried too — `_invoke` raises `subprocess.TimeoutExpired`, not a
`WorkerError` — though bounded to `_TIMEOUT_RETRY_MAX` attempts rather than
the full round budget, since a timeout has already spent its whole ceiling
before it is observed. Any *other* exception is a leerie bug rather than a
flaky worker, so it still abandons the loop immediately (`test_loop_crash_breaks`,
which uses `RuntimeError` precisely to pin that split). Also pinned:
all-rounds-crash still returns `None` so callers' `is None` escalation is
unchanged, the retry is bounded at exactly `max_rounds`, and a crash must
clear `last_res` so a stale earlier result is never returned as the crashed
round's output.

## Integrator-crash salvage path

The integrator-crash salvage path (DESIGN §12 *salvage if there is something
to salvage*) is tested in `tests/test_rescue_integrator_work.py` against real
temp git repos left mid-merge. `_rescue_integrator_work` captures a crashed
integrator's in-progress resolution to `refs/leerie/rescue/<run-id>/<sid>`
before `git merge --abort` destroys it (verified: abort reverts a resolved
file to its pre-merge content, leaving no stash and no reachable object). The
load-bearing pin is `test_rescue_does_not_require_a_merge_commit`: the rescue
must **not** be gated on `check_merge_committed`, because a crashed
integrator typically dies mid-resolution having committed nothing —
`integrator-feat-006` never ran `git commit` while `integrator-feat-005`
did — so a commit-gated rescue declines exactly the case worth saving.
Introducing that gate fails 4 tests. The mechanism: a throwaway
`GIT_INDEX_FILE` seeded from HEAD, because both `git stash push` **and**
`git stash create` refuse a conflicted tree ("Cannot save the current index
state") — an unmerged index is precisely what an integrator crash leaves
behind. Also pinned: untracked files are captured, the real index/worktree
and `MERGE_HEAD` are untouched, the temp index is cleaned up, refs are
namespaced per run+subtask so two crashes cannot clobber each other, and a
tree identical to `HEAD^{tree}` returns `None` rather than a ref naming an
empty diff.

## Remote schema duplication (`collect-subtrees.sh`)

**`scripts/remote/collect-subtrees.sh` embeds a second copy of
`SCHEMAS["integrator"]`** as a single-quoted shell string, because it invokes
`claude -p --json-schema` directly from bash on the remote machine and
cannot import the orchestrator. **Any edit to that schema must update both.**
That same direct invocation also puts the script outside the
`--dangerously-force-strict-output` path — it runs only after the
orchestrator (which owns the proxy) has exited, so output there is
schema-validated but not constrained during generation.
`tests/test_collect_subtrees_integrator_schema.py` is the guard: it parses
the `integrator_schema='{...}'` assignment out of the real script and
asserts whole-object equality with the live `SCHEMAS["integrator"]` —
deliberately whole-object rather than a spot-check of the fields that last
drifted, since the next drift will be somewhere else. It exists because the
copy **had already silently drifted in production** (measured 2026-08-03):
it still carried `maxLength` 2000/500 on the confidence fields, values the
live schema had moved off twice since (to 8000/2000, then deleted outright),
so remote integrator runs were validating worker output against a
materially different contract than local ones — invisibly, because nothing
compared the two. A corpus fixture had even named this test file before it
existed; the guard was planned and never landed, which is precisely how the
drift went unnoticed.

## `resume` auto-pick of the newest resumable run

`tests/test_resolve_run_id_autopick.py` covers bare `resume` auto-picking
the newest resumable run (`in-progress`/`paused`/`incomplete`), including
two traps found by running the design against a real 58-run state dir:
`seed-failed` rows carry no `started_at` and sorted to the *top* of a naive
newest-first sort (they are now list-only, never auto-picked), and a
missing `started_at` must never outrank a real timestamp. An explicit run-id
stays exempt from the filter (so `resume <seed-failed-id>` still works) and
an unknown one still fails closed. The `seed-failed` exclusion is a
deliberate behavior change with a UX cost, pinned by
`test_resolve_run_id.py::test_resolve_lone_orphan_is_not_auto_resumed`: bare
`resume` used to auto-pick a *lone* orphan, and now dies instead — a
seed-failed run aborted before `phase_classify` and needs an operator
decision (re-seed vs. kill), since resuming blind can re-trigger the same
seed failure. The die is therefore required to stay actionable (names the
run, its `status=seed-failed`, and the explicit-id escape hatch), because
that escape hatch is the documented recovery path for the 2026-06-04 hangs.
`--report`/`--phase` still auto-pick a lone orphan — they are read-only.
`tests/test_container_entry_run_id.py` covers `container-entry.sh` skipping
its cidfile `--run-id` injection when `resume` is present — a resume
container is a *new* container whose id matches no run on disk, which is
what made bare `resume` die naming an id the user never typed. The
injection block is extracted from the real script at test time (the
`_extract_config_arm` pattern) so it cannot drift.

## Bash 3.2 portability (EC2 shell surface)

**The EC2 shell surface must run on bash 3.2** — macOS's `/bin/bash`, and
the shell the EC2 tests actually get (they pin `PATH` to
`{stub_dir}:/usr/bin:/bin` to isolate their stubbed `aws`, which excludes
Homebrew's bash 5). CI is `ubuntu-latest`, so it **structurally cannot**
catch a bash-4-only construct; two of them lived in `ec2-lib.sh` /
`ec2-provision.sh` and showed up only as 33 failing tests on a developer's
Mac. `tests/test_ec2_bash32_portability.py` is the guard: it sources each
EC2 script under a real `/bin/bash` with `set -u` and no
`LEERIE_AWS_*`/`AWS_*` (the default config, which leaves every optional-arg
array empty), **and calls the functions that expand those arrays** —
sourcing alone is not enough, since an unguarded `"${arr[@]}"` sits inside a
function body the shell never evaluates until called (verified: the
source-only version of this test passes with the bug reintroduced). It skips
cleanly on hosts whose `/bin/bash` is ≥ 4.3, so it is a macOS-developer
guard, never a CI flake. Paired with a source-level `local -n` / `declare -n`
ban (namerefs are bash 4.3+; echo the tokens instead — see
`_aws_region_profile_args`). The guard was extended (test-006) to cover every
EC2 launcher arm wired by test-001..test-005: `_EC2_SCRIPTS` gained
`ec2-resume-instance.sh`, `ec2-seed-auth.sh`, and `ec2-fetch-branch.sh` (all
sourced by the launcher's EC2 arms but previously untested here);
`_EXPANSION_CALLSITES` gained `resume_instance`; and a new
`test_ec2_launcher_verb_runs_cleanly_under_bash32` runs the real `leerie`
binary itself (not just `scripts/remote/ec2-*.sh`) under bash 3.2 for
`stop`/`kill`/`accept-blocked` with `LEERIE_AWS_PROFILE`/`LEERIE_AWS_REGION`
unset, since each of those arms builds its own optional-arg array from those
two vars directly in `leerie` before calling `resolve_aws_credentials`. This
surfaced a real, previously unguarded instance of the class: all four call
sites (`accept-blocked`, `stop`, `kill`, and the main `RUNTIME=ec2` dispatch)
expanded their creds-args array as a bare `"${arr[@]}"` instead of
`${arr[@]+"${arr[@]}"}` — fixed in the same change. The nameref ban was
likewise extended to `leerie` itself (`test_no_namerefs_in_launcher`). A
later child added `pytest.param(["accept-integration", ...])`, covering
`accept-integration`'s own `_ai_aws_creds_args` array expansion the same way.

## Host-only tests gated on `jq`

**Host-only tests are gated on `jq`** (`HAS_JQ` in `tests/conftest.py`,
mirroring `HAS_TREESITTER`). Five modules — `test_host_finalize_sh.py`,
`test_decide_teardown_auto_finalize.py`, `test_launcher_finalize_no_work.py`,
`test_launcher_no_push_skips.py`, `test_push_output_capture.py` — source
bash the **host** owns (`scripts/host-finalize.sh`, `provision.sh`'s
`decide_teardown`, the launcher's `finalize`/`no_push` paths) and parse
`run.json` with real `jq`. The harnesses stub `git`/`gh` but not `jq`, so
it's silently inherited from whichever machine runs pytest — passing on a
dev host and CI (both ship jq), failing only inside the leerie image,
which deliberately omits it: host bash uses `jq` (launcher hard-fails at
preflight without it), code *inside* the container uses python3 (per
`scripts/remote/seed-auth.sh`). `gh` **is** in the image for the mirror
reason. **Do not "fix" a skip by adding `jq` to the Dockerfile** — per
DESIGN §6 *Finalization* those scripts can never succeed in-container
anyway (gh auth, ssh-agent, Keychain are host-side); installing jq buys a
green tick, not working code. A `grep jq` does **not** reproduce the gated
list — two of the five never mention jq and fail only because the script
under test shells out to it. A module-level `skipif` does **not**
propagate through an import, so `test_push_output_capture.py` reusing
`test_host_finalize_sh.py`'s runner needs its own.
`tests/test_jq_gate_wiring.py` guards that each of the five both imports
`HAS_JQ` and carries a `skipif` referencing it.

## Push output capture (stdout+stderr split)

**The push's two streams are captured separately, and the obvious fix is the
trap.** `host_finalize` captured the push with `2>&1 >/dev/null` — stderr
only — while git forwards a pre-push hook's stdout to git's own stdout, where
`tsc` and `biome` write their diagnostics (jest and vitest use stderr, which
is why this went unnoticed). Measured: a `push_error` of two pnpm deprecation
warnings for a push whose real cause was 13 lines of `TS2307`, undiagnosable
from leerie's own output, three misdiagnoses, at the end of a $57 run. But
plain `2>&1` is **wrong**, because the captured blob is also the input to
`_host_finalize_is_auth_or_network_push_error`, whose arm matches a
qualified phrase on a `^fatal:`/`^remote:` line — and a hook that refreshes
submodules or runs `git ls-remote` prints exactly that shape on stdout,
flipping a hook failure to "auth/network" and suppressing the `--no-verify`
hint. Measured against the real classifier: **3 of 3** adversarial hook
shapes flip, while real `tsc`/`vitest` output does not. So stderr classifies
and stdout+stderr is displayed, leaving the committed 23-case corpus score
unchanged **by construction** rather than by re-measurement.
`tests/test_push_output_capture.py` pins both halves; its parametrized
`test_git_framed_hook_stdout_does_not_suppress_the_hook_hint` is the
load-bearing one, paired with an anti-vacuity control that a genuine
credential failure on stderr still classifies as auth (else the guard could
pass by disabling the classifier). Falsified live: routing `push_all` into
the classifier fails 4 tests, and the control keeps passing.

### Three further traps in the same change

Three further traps, each caught by a test rather than by review. (1)
`push_error` reaches `run.json` as a single `jq --arg` value, bounded by
`MAX_ARG_STRLEN` (131,072 bytes) — one real recorded `push_error` is
already **104,520 bytes on stderr alone**, so folding hook stdout in is
what makes the ceiling reachable; past it `jq` cannot be exec'd and
`set -e` aborts `host_finalize` before the diagnostic prints. The
persisted copy is tail-bounded at 32 KiB (printed copy: 4000 bytes);
`test_oversized_push_output_still_writes_the_sidecar` drives ~200 KB
through it. (2) Husky v9 prints its banner on **stdout** (line 20 of
`.husky/_/h` is a bare `echo` with no `>&2`), so the "which hook" naming
grep, reading stderr only, could never match the commonest hook runner in
existence; it now reads stderr plus the hook's stdout. (3) That grep must
NOT read `push_all`, because leerie's own section marker
(`--- pre-push hook output (stdout) ---`) contains "pre-push"/"hook" and
matches *first* — measured, the hint misattributed husky's own banner. A
separate `push_hook_out` variable holds the raw stdout so the grep never
sees leerie's own prose. The test asserts the name is "script", not merely
that "pre-push" appears — a laxer assertion passes against the bug.

## Locale-stripped harness masks a byte-vs-character bug

**A harness that strips the locale makes a byte-vs-character bug
undetectable — the sharpest vacuity trap in the file.** Both push bounds
are `tail -c` (cuts BYTES), while `${#var}` counts CHARACTERS only under a
multibyte locale. `test_host_finalize_sh.py`'s runner builds a minimal env
(no `LANG`/`LC_ALL`), so bash runs in the **C locale, where `${#var}`
counts bytes** and byte-based vs char-based implementations are
*indistinguishable*. The first version of
`test_persist_bound_is_measured_in_bytes_not_characters` passed against the
exact bug it targeted — falsified: 35 passed with the fix reverted. It now
resolves a working multibyte locale first (`_multibyte_locale()` probes
bash's own `${#}`, requiring 2 not 6 for a two-character Japanese string),
passes it through `extra_env`, and **skips loudly** when none exists.
Generalise: for locale-/encoding-/timezone-sensitive behaviour, the
harness's minimal env is a *variable of the experiment*, not scaffolding.

Two further harness traps: the degrade test's `mktemp` stub must fail only
the plain-file form, since stubbing every form aborts at the rebase step's
`mktemp -d` earlier, passing on a path that never reached the push. And the
shared runner decodes with `errors="replace"`, since a byte-anchored
truncation can land mid-character and strict decoding raises
`UnicodeDecodeError` before any assertion runs. (`jq` substitutes U+FFFD
and still writes valid JSON at rc 0, so the byte cut is safe for
`run.json`.)

## Per-subtask delta proxy: the `{test_files}` tier

The per-subtask delta proxy's `{test_files}` tier is covered by
`tests/test_test_files_proxy.py` (48), `tests/test_scoped_proxy_corpus.py`
(5) and `tests/test_scoped_degrade_warning.py` (11). Three lessons
generalise. **(1) A non-test path is an ERROR to pytest, not a no-op** —
`pytest orchestrator/leerie.py` exits 5, `pytest docs/DESIGN.md` exits 4,
and `pytest docs/DESIGN.md tests/test_blt_semaphore.py` ALSO exits 4. A
real subtask diff mixes docs/source with tests, so a `{files}` template on
a runner with no source→test impact analysis reports RED on nearly every
subtask; fix: filter the substitution (`{test_files}`), with the
pre-existing empty-list rule falling back to canonical when no test file
is present. **(2) Scan the author's input, not the rendered output.** The
unknown-placeholder guard first scanned the SUBSTITUTED command, so a
changed-file path containing braces (e.g. `src/{locale}/page.test.ts`) was
misread as an unknown placeholder, disabling the proxy and misdiagnosing
it as install skew. Scanning the template with `_SCOPED_PLACEHOLDERS`
stripped removes the hazard by construction. **(3) A planner prediction is
not a diff.** The ratio was first taken from `files_likely_touched` and
was badly wrong — 40% test-touching predicted (109 of 270) vs. 94% real
(34 of 36) — since CLAUDE.md mandates tests regardless of planner
prediction. The frozen corpus is 36 REAL per-subtask diffs from leerie's
own run branches, each row an integration merge's **first-parent** diff (a
plain two-dot diff against the run base folds in siblings — the first
recovery attempt reported 0% source-only this way).
shipped a fixture that could not exercise the canonical fallback at all.

## Pre-push preflight probe

`tests/test_prepush_preflight.py` (25) covers `host_prepush_preflight`, the
run-start probe (DESIGN §6 *Finalization*). Real repos, real hooks, no
stubs — the probe's whole value is running the real gate. Its load-bearing
test is `test_probe_pushes_a_new_ref_so_the_hook_gets_real_stdin`: probing
the already-up-to-date working branch still runs the hook but hands it
**empty stdin** (verified against real git), so a hook that iterates the ref
updates git feeds it exits 0 — a false pass, the worst possible outcome for
a probe whose job is predicting a rejection. Pushing a new ref under
leerie's own namespace reproduces the exact line finalize will produce.
Falsified live: changing the refspec to `"$branch"` fails exactly that test
with rc 0. Paired with `test_probe_creates_no_ref_anywhere` (the property
that makes running a real gate safe) and a launcher-gate parametrization
that **extracts** the preflight block from `leerie` rather than reproducing
it. It also pins the **chain** contract: `chain` backgrounds one `./leerie`
per job against a single shared checkout, so without care every job re-runs
the hook — N concurrent lint/typecheck runs computing one answer, N
identical warnings. The chain arm probes once per WAVE (after the checkout
that establishes the tree those jobs will push from) and hands each child
`LEERIE_SKIP_PREPUSH_PREFLIGHT=1`. Both halves are pinned, and both are
load-bearing: skipping in the children alone removes the check from the
most expensive kind of run, the opposite of the point. `group` is
deliberately exempt — separate repos, separate questions. Two traps in that
arm: its `--no-push` skip must read `_ch_passthrough`, since `NO_PUSH` is
first assigned *after* the chain arm and so does not exist there — the
single-run gate's opt-out silently has no counterpart otherwise. And the
block is **executed** by its tests, not merely string-matched: `bash -n`
catches syntax, not an unbound variable or a bare `"${arr[@]}"` on an empty
array under `set -u`, the same "scanning is not calling" lesson
`test_ec2_bash32_portability.py` records — so `_chain_probe_block()` is
bounded before the fan-out (running the wider extraction would background a
real `./leerie`) and driven against a real repo.

### Three test-side traps (EC2 transport stubs)

Three test-side traps in the same area, all of which made a test pass or
hang while proving nothing: `tests/test_ec2_transport.py::_stub_timeout`
must **kill the process group**, not just the direct child — macOS ships no
`/usr/bin/timeout`, so `_seed_timeout_prefix` correctly no-ops on the
stubbed PATH and a stall test's `sleep 600` runs unbounded (a 10-minute
hang, not a failure); and killing only the child leaves its grandchildren
holding the captured stdout, so a `$(...)` capture blocks until every writer
closes the pipe. Real GNU `timeout` kills the group for exactly this reason.
`tests/test_ec2_seed_repo.py` imports that killing stub for its stall test
rather than its own local `_make_stub_timeout`, which is a no-op passthrough
(fine for tests that just need the binary to exist, useless for one
asserting the cap fires). And its `_make_stub_ssh` rewrite used
`${{a/\/work/$DEST\/work}}` — the replacement half of `${{var/pat/repl}}`
is not a regex and needs no escaping, so the `\/` was a **literal
backslash**: the transfer landed in a directory named `<dest>\`, rsync
exited 0, and the test failed with "untracked.txt missing" and no error
anywhere. Only the pattern half escapes.

## EC2 credential-resolution wiring

The launcher's credential-resolution wiring within that same `RUNTIME=ec2`
branch — sourcing `aws-credentials.sh`, calling `resolve_aws_credentials`,
`eval`ing its `export` lines before `require_aws` runs — is pinned in
`tests/test_ec2_e2e_provision.py` (call-index ordering: an SSO-configured
profile with explicit env-var credentials layered on top resolves via the
env vars, with `require_aws`'s `sts get-caller-identity` the first `aws`
call observed; explicit env credentials win over a fully-configured SSO
profile; `LEERIE_AWS_PROFILE` selects a named profile's static credentials
over `[default]`; an expired SSO cached token aborts non-zero with
`aws-credentials.sh`'s own hint and zero `aws` calls) and in
`tests/test_ec2_launcher_credentials.py`, which closes the one part of the
seam no sibling file exercises: region. `require_aws`'s
`sts get-caller-identity` never passes `--region` — the resolved region
reaches it only via the `AWS_REGION` env var the dispatch block `eval`s —
so this file's stub records the *effective `AWS_REGION` env value* to pin:
`LEERIE_AWS_REGION` (leerie's own knob, distinct from the SDK's
`AWS_REGION`) winning over an ambient `AWS_REGION`; the ambient value
passing through unchanged when unset; and an unresolvable region (no
`AWS_REGION`/`AWS_DEFAULT_REGION`/profile `region` key) aborting non-zero
via `resolve_aws_credentials`'s own die-with-hint before `require_aws`'s
probe ever runs. Also adds a direct argv assertion for `--profile
<resolved>` and a harness-sanity check that it exercises the same
verbatim-extracted dispatch block as `tests/test_ec2_e2e_provision.py`
rather than a hand-copied reproduction.

## EC2 resume path (`ec2-resume-instance.sh`)

The EC2 resume path — `scripts/remote/ec2-resume-instance.sh`'s
`resume_instance()`, the EC2 counterpart to `resume-machine.sh` — is tested
in `tests/test_ec2_resume_instance.py` against the same resource-tracking
`aws` stub: starting a `stopped` instance drives it to `running` via a
single `start-instances` call; the readiness poll does not return early
when a seeded `status_ok: False` keeps `describe-instance-status` reporting
"initializing" (and does return promptly once `status_ok: True`);
`LEERIE_EC2_SSH_TARGET` is re-resolved to the instance's current
`PublicIpAddress` rather than any address cached from provision time (EC2
assigns a new public IP on every stop/start cycle absent an attached
Elastic IP); a full provision → stop → resume round trip leaves exactly one
`running` instance with no leaked volumes; resuming an already-`running`
instance is an idempotent no-op that issues no `start-instances` call;
resuming an unknown/terminated instance fails with the "no longer
recoverable" hint and issues no `start-instances` call; the run.json
sidecar's `paused_at`/`pause_reason` fields are cleared on success; and the
one-way-ratchet invariant (never `terminate-instances` or `delete-volume`)
holds both on the success path and the failure path (instance never becomes
ready), (source-level grep guard).
`tests/ec2_stub.py` gained a reassigning `public_ip` (`_ip_gen` counter) and an optional `status_ok` flag for slow-poll simulation.

## `stop` verb EC2 dispatch

The launcher's `stop` verb EC2 dispatch — the counterpart to
`_auto_detect_fly_runtime` for EC2 runs, DESIGN §6 "Run identifier" — is
tested in `tests/test_ec2_launcher_stop.py` by invoking the real `leerie`
binary (not an extracted block, since `stop` is an early fast-path verb
dispatched before container preflight) against the same resource-tracking
`aws` stub: an `ec2-instance.json` sidecar auto-detects the EC2 runtime and
`stop <run-id>` drives the stub-tracked instance to `stopped` (never
`terminate-instances`) and writes `paused_at`/`pause_reason`/`ec2_instance_id`
onto `run.json`; explicit `--runtime ec2` works without autodetection; the
local/Fly fallthrough error text is unchanged when no sidecar of any kind is
present; `--runtime bogus` is still rejected, now with the `'local', 'fly',
or 'ec2'` wording; a sidecar present but missing `ec2_instance_id` fails
closed with an actionable error rather than silently no-op'ing; and a
failing AWS credential probe aborts before any `aws ec2 ...` call reaches
the stub, leaving the instance `running`.

## Full EC2 dispatch lifecycle (create -> seed -> orchestrate -> teardown)

The `RUNTIME=ec2` dispatch branch continuing past preflight into the full
create -> seed -> orchestrate -> teardown lifecycle (the old `--runtime ec2
preflight passed, but instance provisioning is not yet wired` abort is
gone) is pinned in `tests/test_ec2_launcher_dispatch_e2e.py`, which reuses
(rather than reimplements) `tests/test_ec2_e2e_provision.py`'s
`extract_ec2_dispatch_block`/`run_ec2_dispatch`/`stub_aws_env` harness and
`tests/ec2_stub.py`'s resource-tracking `aws` stub. It
pins: a full launch with valid credentials provisions exactly one instance,
reaches the stubbed `ec2_seed_repo`, and terminates cleanly at
`decide_ec2_teardown`'s clean-exit arm, leaving zero leaked instances and
zero leaked volumes; a grep guard that neither `"not yet wired"` nor the
more specific historical string `"instance provisioning is not yet wired"`
appears anywhere in `leerie`; `require_aws`'s `sts get-caller-identity`
still precedes any `ec2 run-instances` call by call index across the *full*
lifecycle path (not just the provision-only path `test_ec2_e2e_provision.py`
already covers); and a failing credential probe still aborts non-zero with
the `aws sso login --profile <p>` hint and zero tracked resources.

## Run-dir sidecar runtime autodetection

The generalized run-dir sidecar autodetection — `_auto_detect_run_runtime`
(checks `fly-machine.json` then `ec2-instance.json`, echoing the detected
runtime) and the `_auto_detect_fly_runtime` back-compat Fly-only wrapper
built on top of it — is tested in `tests/test_auto_detect_run_runtime.py`.
The first half extracts both functions verbatim from the launcher and
exercises them against fixture run dirs: an ec2-instance.json-only run dir
detects as `ec2`; a fly-machine.json-only run dir still detects as `fly`
(no regression); neither sidecar present returns nonzero with nothing
echoed; an explicit runtime short-circuits detection even when a sidecar
for a different runtime is present; Fly wins when (never expected in
practice) both sidecars co-exist; and the Fly-only wrapper returns
nonzero for an EC2 run.

`finalize`'s **local** arm is covered by six further end-to-end cases in the
same file. The discriminating one is
`test_finalize_local_run_without_finished_at_reaches_host_finalize`: a run
dir with no Fly/EC2 sidecar and deliberately **no `finished_at`** — the shape
left by a Ctrl-C after the waves integrated but before `phase_finalize`.
Before the local arm existed, `_auto_detect_run_runtime` left `_fin_runtime`
empty, the `_already_synced` probe missed for want of `finished_at`, and the
dispatch chain's bare `else` sent the run into the Fly path, where
`require_flyctl` offered to *install flyctl* for a run that never touched
Fly. The fixture sets `no_push` so `host_finalize` short-circuits at its own
early gate (`scripts/host-finalize.sh:294`) — a return from *inside*
`host_finalize`, which is what proves the launcher handed off, without the
test needing a git remote or a rebaser worker. The remaining cases pin
`--runtime local` as accepted (it used to `exit 1`), `--force` refused with
the local message rather than the EC2 one, a missing run branch failing
closed on the branch rather than deeper in `host_finalize`, a Fly sidecar
still promoting to `fly` (the local default must not swallow a real Fly
run), and the advertised usage enum reading `local|fly|ec2` — asserted
through a real rejection, so it checks what a user is shown rather than a
source substring. These fixtures point `USER_REPO` at a scratch git repo,
which the finalize arm honors via `USER_REPO="${USER_REPO:-$PWD}"`. Note the
local-shaped fixtures in `tests/test_launcher_finalize_no_work.py` cannot
catch this regression: all of them exit early through `_already_synced` /
`pushed_at` / argv validation and never reach the runtime dispatch.

The second half invokes the real launcher end to
end (mirroring
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

## EC2 `kill` action ordering

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

## Plan-overlap judge: multi-drop clusters

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

## Plan-overlap judge: multi-artifact pairs

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

## Plan-overlap judge: duplicate collision resolution

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

## Wiring gate: resume must not bypass it

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

## Wiring gate: constrained auto-repair

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

## Wiring gate: dismissing provably-false defects

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

## Wiring gate repair: corpus-measured effect

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

## Deterministic duplicate-provider floor

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

## Launcher stale-install warning

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

## Plan checkpoints: snapshot aliasing

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

## Dead-function guard

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

## Static `claude_p` call-site signature check

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

## Judgment-worker isolation

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

---

## Planning-worktree pollution: conftest fixture

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

## `resume <run-id>` positional argument bug

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

## Fresh-run branch coverage gap

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

## Structure-vs-substance testing principle

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

## Undefined-name static scan

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

## Tool-containment scan: source-slicing blind spot

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

## Turn-cap taint-walk scope isolation

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

## Under-specified fixture hides producer contract

**An under-specified fixture hides a producer-side contract.** Two files passed
`models={}` to `_run_phases`, which was invisible until `preflight` began reading
`models["classifier"]` and they raised `KeyError` before reaching the behaviour under
test. The fix is a real dict (derived from `WORKER_TYPES`, or a `defaultdict`), not a
`.get()` in production code — coercing there would have swallowed the contract violation
in exactly the way this file warns about elsewhere.

## No coverage target; launcher syntax check

No coverage target is set — the suite was introduced from scratch and a number
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

**Why a shared module, not two local copies**: PRs #180–#183 each replaced a
hard-coded enumeration with a derivation after a missed instance shipped —
`ContextOverflow` in 1 of 9 capture guards, `leerie_commit` in 1 of 2
state-init branches, then 1 of 2 launch blocks (caught by a reviewer, not the
suite). A rule written twice drifts like a list written twice.
`tests/test_no_duplicate_launcher_blocks.py` applies the same discipline to
**bash** blocks: eight start-of-line markers (`_resolve_ec2_knob`,
`_state_dir_default`, `_resolve_seed_knob`, `ensure_image`,
`resolve_repo_image_tag`, the `config)` case arm, the
`# --- runtime-mode knob ---` block, the `_run_argv=(` array), each asserted
to appear exactly once and never at the start of a line in any test file.
Converting N13's five named files fixed those instances but not the rule:
three more reproductions were found afterwards, two already drifted —
`tests/test_launcher_state_mount.py` reproduced the `nerdctl run` argv
missing `--cidfile`, `--cgroupns=host`, `ROOTLESS_SECOPT`, the `LEERIE_*`
auto-forward and `${REPO_IMAGE_TAG:-$IMAGE_TAG}`;
`tests/test_launcher_runtime_knob.py` omitted `_RUNTIME_EXPLICIT` entirely —
a flag set at six sites and read by the resume auto-detect (`leerie:4127`,
`:4152`) with **zero coverage suite-wide**; deleting every assignment left
the suite green and now fails five tests. None of the copies produced a
wrong answer — they were blind, and would pass identically if the launcher
deleted the behaviour under test (anti-vacuity control:
`test_the_scan_can_find_a_reproduction` plants a copy and proves the scan
fires on it while still ignoring a legitimately quoted reference).

`tests/test_no_duplicate_launcher_splitters.py` enforces the single owner
(`tests/launcher_blocks.py`) with two anti-vacuity controls: the marker must
be found *inside* the owner, and at least two other files must actually
import `launch_env_blocks`. The load-bearing falsification breaks the
shared splitter and confirms both
consuming files fail — proof they share it rather than merely import it.

## LEERIE_COMMIT state field

`tests/test_leerie_commit.py` pins the `leerie_commit` state field, which
disambiguates `leerie_version`: `plugin.json` only moves on a
`chore(release):` commit while `install.sh` tracks `main`, so a run between
releases records the same version whether or not it carries a given fix. The
launcher computes the short sha and forwards it as `LEERIE_COMMIT`; the
orchestrator stores it adjacent to the version in `STATE_FIELDS` (so one
can't move without the other), reading the env var with `or None` — an empty
value arrives on a tarball install and `""` would render as a
real-but-blank sha in the PR footer. Launcher-side the `git` call carries
`2>/dev/null || true`, is forwarded explicitly with `-e`, and is **not** on
the forwarding denylist, unlike host-only `LEERIE_VERSION`.

**Two traps this file exists to pin, both shipped broken once.**

1. `_run_phases` initialises state in *two* branches (`if args.resume:` vs.
   the fresh-run `else:`), so a key must be written in both. The original
   test compared two strings that both live in the resume branch, so it
   passed while the field was absent from every fresh run — the common
   case. The replacement walks the AST, locates the `args.resume` `If` node,
   and requires the key in `body` **and** `orelse`, with an anti-vacuity
   control that the same walk finds `leerie_version` (known present in
   both). `tests/test_state_fields.py::test_no_resume_only_state_keys`
   *derives* the rule (`resume_keys - fresh_keys == set()`) for every key —
   `task`, `started_at`, `worker_count` are legitimately fresh-only and
   excluded. The walk (`_state_init_branch_keys`) has one owner
   (`tests/test_no_duplicate_state_walks.py`; a drifted copy would
   under-report `resume_keys`, passing the symmetry guard vacuously),
   matches `ast.unparse(n.test) == "args.resume"` exactly, and **raises** on
   an `st.data.update()`/`setdefault()`/augmented write inside either arm
   rather than silently missing it. `_BOTH_BRANCH_KEYS` pins the three
   fields that have actually shipped broken here (PRs #180–#183): the
   derived rule immediately found a third defect a hand-built tuple missed —
   `skip_coverage_check`, seeded only under `if args.resume:` since PR #162,
   silently inert on every fresh run while
   `tests/test_phase_planning_coverage_gate.py`'s `TestSkipCoverageCheck`
   reported full coverage because every test sets the key by hand. By
   contrast `dangerously_force_strict_output` (M7) is a record only — its
   behaviour comes from `caps["force_strict_output"]` ahead of the split, so
   both paths always honoured it and only attribution was lost.

2. The local `-e` forward covers only `--runtime local`; Fly and EC2 each
   build their own `child_env = dict(os.environ)` inside their own unquoted
   `<<PY` heredoc, and both must forward the value JSON-encoded
   (`_leerie_commit_json` / `_ec2_leerie_commit_json`) — the Fly name also
   needs the `${...}` allowlist in `tests/test_bedrock_bearer_token.py` or
   its stray-substitution scan fails.

**Both heredoc scans are derived**, over every launch block rather than the
one Fly slice they originally hard-coded: `_launch_env_blocks()` feeds both
the stray-`${...}` allowlist scan and the backtick scan, which used to cover
only a 31-line Fly slice and were blind to the EC2 heredoc — an unbound
`${VAR}` anywhere in an unquoted `<<PY` body aborts under `set -euo
pipefail`, and a balanced backtick pair is read as command substitution,
silently dropping that text (`bash -n` misses it; `shellcheck -x` catches
it). Falsified both ways: injecting either defect into the EC2 body fails
naming `ec2`, and the old slice provably did not contain it.

**`_child_env_blocks()` finds every `child_env = dict(os.environ)`** in the
launcher and requires each to forward the var, so a third runtime fails
automatically — a hard-coded version once shipped covering Fly only while
being *named* `..._fly_ec2_path_too`, and EC2 recorded null until a reviewer
caught it (anti-vacuity: at least two blocks must be found, each also
setting `USER_REPO`, known present in both).

## `--dangerously-force-strict-output` context-window regression

(DESIGN §7 *Forcing constrained decoding*, §6 *A client-side context
refusal*) — covered by three files. The flag owns `ANTHROPIC_BASE_URL`, and
the CLI treats any custom base URL as an **LLM gateway**, behind which it
can no longer confirm the answering model and falls back to a conservative
client-side context ceiling instead of Sonnet 5's native 1M, refusing
prompts *itself* at ~224K with a synthetic assistant message
(`model=<synthetic>`, usage all zeros, no API call).

`tests/test_strict_proxy_context_window.py` pins `_model_arg`: `sonnet`/
`opus` gain the `[1m]` suffix only while `_STRICT_PROXY` is active, `haiku`
never does (no 1M variant), a full model id passes through untouched, the
suffix is not doubled, `_ONE_M_CONTEXT_MODELS` is a subset of `MODEL_VALUES`
(a typo would silently disable the fix), and `claude_p`'s source builds
`--model` via `_model_arg(model)` with no bare `"--model", model,` left. The
suffix is deliberately **not** admitted to `MODEL_VALUES`: inert with the
proxy off, so setting it by hand only breaks `haiku`.

`tests/test_context_overflow_classifier.py` pins `_is_context_overflow` and
`ContextOverflow` against verbatim `result` envelopes. Both
`terminal_reason == "blocking_limit"` **and** the result text are required —
the reason alone is shared with other terminal arms, and `subtype` is a
misleading `"success"` that must never be keyed on. Gated on `is_error`,
exempting `_leerie_synthetic`, disjoint from both auth classifiers.
`ContextOverflow` subclasses `BaseException`, not `WorkerError` —
`_run_checked_loop` retries `WorkerError` for its whole round budget, pure
waste for a deterministic client-side refusal — and routes to a resumable
`EXIT_LOCKED` pause whose message never says "schema" (unclassified, this
surfaced as *"worker failed schema-valid output twice: Prompt is too
long,"* costing three misdiagnoses on 2026-08-06). Extracting the handler
arm must split on `"\n    except "`, not a bare `"except "`, which
truncates at the inner `except Exception:`.

## Task-reference globbing (`_glob_task_references`)

`tests/test_task_file_globbing.py` covers a defect the same incident
surfaced but did **not** cause: markdown emphasis is stripped before glob
classification, since `*` is a `_GLOB_CHARS` member and `glob("*")` matches
every file in the repo root — measured, that handed the planner 18 files /
1.86 MB, including `LICENSE`, `.claude.json` and a prior run's 847 KB log.
Pinned: prose (`*`, `**`, `**Root**`, `_em_`, backticks) resolves nothing;
genuine references (`spec.md`, `tests/*.py`, `docs/DESIGN.md`,
`spec.{md,txt}`, a bolded path) still resolve; absolute paths and `../`
traversal resolve nothing — without that guard, separator-bearing tokens
reached **outside the repo** (`repo_root / "/bin/sh"` discards the root, so
`/bin/bash` matched a 1.4 MB binary), so containment is re-checked against
`repo_root.resolve()` independently; a task file never lists itself, while a
same-named file with different contents still does. Falsification: replaying
the pre-fix filter against the prose-only fixture matches 4 files where the
test expects none.


### `~` in task prose is approximation notation (2026-08-24)

Two runs died in `phase 2: planning` with `RuntimeError: Could not determine
home directory.`, raised by `Path("~17.9").expanduser()` from the task
sentence *"max 17,923,286 B (~17.9 MB)"*. Not environmental: `expanduser`
takes its `~user` branch for any `~` token whose first segment is non-empty,
`pwd.getpwnam("17.9")` raises `KeyError`, the string comes back unexpanded,
and pathlib converts that into `RuntimeError` — `HOME` is never read on that
branch, so it reproduces on a host with `HOME` set. The check is advisory
(one `log()` line, gates nothing), so the cost was a whole run for a warning.

Measured 2026-08-24 over the 518 task strings then stored under
`~/.leerie/*/runs/*/state.json` (a live corpus — re-running it later gives a
larger denominator): **62 distinct `~`-prefixed path-shaped tokens, 32 of
them prose, and all 32 raise**. Three of those 32 carry a
separator (`~2.5/subtask`, `~9/10`, `~14GB/64GB`), which is why the token rule
is home-*shape* (`~/…` or `~name/…`), not "contains a `/`" — a `/`-only rule
still crashes on three real corpus shapes. Differential falsification against
the same corpus: pre-fix **45 of 518** tasks raise, post-fix **0**, and the
473 non-raising tasks return byte-identical results.

The try/except is independently load-bearing: `~name/plan.md` for a name absent
from the password database is home-shaped, still raises, and must be *reported*
(it is genuinely unreadable), not propagated. Pinned by
`test_unknown_user_home_reference_is_flagged_not_raised` and, for the wrapper,
`test_advisory_never_gates_even_when_the_check_raises` — which executes
`_log_unreachable_task_references` against a check that really raises rather
than reading `phase_plan` for a `try` statement.


## EC2 read-mostly verbs (`accept-blocked`, `list`)

`accept-blocked` (validated `--runtime` against only `fly`/`local`,
mislabeling an EC2 run) and `list` (keyed its runtime view on
`fly-machine.json`/`LEERIE_FLY_APP`, rendering empty EC2 columns) are pinned
in `tests/test_ec2_launcher_readonly_verbs.py`. `accept-blocked` now
auto-detects EC2 (`_auto_detect_run_runtime`), accepts an explicit
`--runtime ec2`, and — mirroring the Fly wake-mutate-pause dance — wakes a
stopped instance, mutates `state.json` over SSM (`ec2_remote_exec`), mirrors
onto the host copy, and re-pauses only if this verb woke it, failing closed
on a missing `ec2_instance_id`. Tests invoke the real launcher against a
stubbed `aws` composing `tests/ec2_stub.py`'s instance tracking with an `ssm
start-session` handler decoding the base64 command through stdin (the same
mechanism `tests/test_ec2_launcher_dispatch_e2e.py` relies on).
`_collect_run_rows`/`_list_runs` now track an `is_ec2` axis alongside
`is_fly`, so `list --runtime ec2`/`--runtime local` filter correctly, a plain
`list` renders an EC2 status column without `LEERIE_FLY_APP`, and detection
works via the `ec2-instance.json` sidecar alone before `run.json` exists.
These `list` tests exercise `_list_runs()` directly (no launcher subprocess,
no AWS stub), mirroring `tests/test_list_runs.py`'s pattern.

## EC2 resume

`resume` routing a paused EC2 run through `resume_instance()` — distinct
from that function's own coverage in `tests/test_ec2_resume_instance.py` —
is pinned in `tests/test_ec2_launcher_resume.py` (reusing
`tests/test_ec2_e2e_provision.py`'s dispatch harness and `tests/ec2_stub.py`'s
`aws` stub, since EC2 resume lives inside the deep `RUNTIME=ec2` elif block
rather than the fast-path `stop` uses): a stopped instance issues exactly
one `start-instances` call and reaches `running`, no duplicate provisioning;
`LEERIE_EC2_SSH_TARGET` is re-resolved to the instance's NEW
`PublicIpAddress` (EC2 assigns a new public IP on every stop/start absent an
Elastic IP); `paused_at`/`pause_reason` clear and `ec2_instance_id` is
preserved; an already-`running` instance is an idempotent no-op; and neither
`terminate-instances` nor `delete-volume` is ever called, on both the
success and never-ready-timeout paths.

## Worker invocation layer

The worker invocation path is unit-tested only at the `claude_p` layer, via
a stubbed `_invoke` (`tests/test_no_result_event_retry.py`) — pinning the retry/envelope contract. `_invoke` itself (process spawn, stream
parsing, cgroup enrollment) needs a stub or live `claude` binary and lives
in a separate end-to-end tier.

## Credential resolution and expiry

The inverted credential-resolution precedence in
`_extract_claude_credentials_json` (DESIGN §6 *Credential strategy* —
`$CLAUDE_CODE_OAUTH_TOKEN` resolves ahead of Keychain and the on-disk file,
since a container cannot refresh a copied subscription token) is tested in
`tests/test_credential_precedence.py` (via `_invoke_helper` from
`tests/test_chain_credential_transport.py`): the env var wins over Keychain
(Darwin-only) and the file, wins over the file alone on non-Darwin, matches
`seed-auth.sh`'s independently-constructed JSON shape (extracted by regex so
the two can't silently diverge), Keychain still wins over the file when the
env var is unset, and no credential anywhere yields a clean rc 1.

It also pins the shape-validation gate rejecting a syntactically valid but
semantically empty Keychain/file blob (steipete/CodexBar#1844: a background
MCP-plugin OAuth flow overwrites the shared Keychain item with only
`{"mcpOAuth": {...}}`, dropping `claudeAiOauth`): an mcpOAuth-only blob
falls through to a file, or yields rc 1 with no fallback; a blob with
`claudeAiOauth` but an empty `accessToken` is rejected; both `mcpOAuth` and
a real `accessToken` together is still accepted. The synthesized blob's
mandatory `scopes:["user:inference"]` field (CLI 2.1.210 rejects a
scope-less blob as "Not logged in") is pinned byte-identical across all
three synthesized sites (`leerie`, `seed-auth.sh`, `ec2-seed-auth.sh`); the
always-forward `-e CLAUDE_CODE_OAUTH_TOKEN` injection (survives a headless
run past a copied file blob's `expiresAt`, anthropics/claude-code#21765)
sits *before* the credential-resolve `if`/`else`, not in the failure arm
where it was previously unreachable.

The paired best-effort expiry preflight, `_check_claude_credential_ttl` (a
no-op for the exempt long-lived-token path), is tested in
`tests/test_credential_ttl_preflight.py`: already-expired refuses and names
`claude /login`; inside the 90-minute threshold warns with the exact
ISO-8601 expiry and `claude setup-token` (replaying the b57027d3 incident's
expiry shape); a healthy TTL is silent; absent, malformed, or negative
(pre-1970 garbage) `expiresAt` all proceed silently (best-effort, not a hard
gate); the threshold is pinned at exactly 90 minutes — the launcher never
hard-codes an 8-hour assumption, since the community-reported range is
2–15h.

## Bedrock auth paths

The Bedrock bearer-token path (`AWS_BEARER_TOKEN_BEDROCK`, DESIGN §6
*Credential strategy*: preferred over the settings.json SSO/profile path
since a container cannot refresh a short-lived SSO token) is tested in
`tests/test_bedrock_bearer_token.py`: forwarded verbatim alongside
`CLAUDE_CODE_USE_BEDROCK` defaulting to `1` (confirmed live: the token alone
is a no-op without it) and an optional `AWS_REGION`; an explicit
`CLAUDE_CODE_USE_BEDROCK=0` still wins; the path never invokes
`bedrock_preflight()`/`aws sts get-caller-identity` and mounts no `~/.aws`;
it wins when both it and a settings.json flag are present; the pre-existing
SSO/profile path is unaffected when the bearer token is absent.

The Fly detached-launch heredoc has its own dedicated coverage for three
defects, since it is unquoted (`<<PY`) and substitutes shell expansions
inside inert-looking Python comment text: (1) a raw
`"${AWS_BEARER_TOKEN_BEDROCK}"` substitution let a `"`/`\`-bearing token
break out of the Python string literal and run as arbitrary code remotely —
fixed by JSON-encoding every heredoc-substituted value host-side, pinned by
`test_fly_heredoc_values_are_json_encoded_not_raw` and three live
end-to-end tests (`test_malicious_token_with_quote_does_not_break_out_of_python_literal`,
`test_malicious_token_with_backslash_does_not_break_out`,
`test_normal_token_unaffected_by_json_encoding`) that pipe the real
JSON-encoding lines through `python3 -`; (2) a first-draft fix comment
containing the literal text `${VAR}` crashed the launcher with `unbound
variable` under `set -u` on every Bedrock Fly launch — unconditional, worse
than the injection defect — pinned by
`test_child_env_heredoc_body_has_no_stray_unbound_var_substitution`; (3) a
balanced backtick pair in a comment was parsed by bash as command
substitution, a different mechanism unguarded by fix (1), silently dropping
that comment's text (caught by diffing `shellcheck -x leerie` against `git
stash`; `bash -n` misses it) — pinned by
`test_child_env_heredoc_body_has_no_backtick_characters`. All three were
falsified live (reintroduce, confirm red, confirm the fix turns it green).

`tests/test_bedrock_mode.py` covers the pre-existing SSO/profile path
(`detect_bedrock_mode()` / `bedrock_preflight()`), which shipped with zero
coverage: the 3-file merge and truthy-value matching (`1`/`true`/`yes`/`on`,
case-insensitive OR, tolerant of a malformed settings file), and
`bedrock_preflight()`'s three outcomes (missing `aws`, a failing `aws sts
get-caller-identity`, a valid SSO session), both via source-slicing rather
than reproducing the functions by hand.

## Terminal auth-failure classifier and routing

The `b57027d3…` incident — a container's expired OAuth session surfacing as
"worker failed schema-valid output twice" instead of a resumable pause — is
tested in `tests/test_terminal_auth_failure.py`: `_is_terminal_auth_failure`
is table-driven over the measured corpus (4 real strings positive,
mixed-case included; 8-string "API Error: …" corpus plus empty string
negative; the verbatim incident envelope true); gating mirrors
`_is_auth_or_quota_failure` (`False` without `is_error`, for
`_leerie_synthetic`, for a successful envelope discussing OAuth
legitimately, for a bare "oauth" substring — guarding the 2919-count
false-positive risk noted in the docstring — and for non-string `result`);
401/429/529 envelopes classify false here while true under
`_is_auth_or_quota_failure`, proving the two classifiers partition cleanly.
`claude_p()` routing tests replay the verbatim envelope through a stubbed
`_invoke`: completes in under 5 seconds rather than entering the ~300s
tenacity loop, raises `TerminalAuthFailure` not `WorkerError`; a control
with 401/429/529 envelopes still exhausts the real backoff loop before
raising `WorkerError`. Guards: an AST-based check (not substring — satisfied
by explanatory comments alone) that `main()`'s `except TerminalAuthFailure`
sets `exit_code = EXIT_LOCKED` (== 75) and mentions `resume`;
`_is_terminal_auth_failure` checked before `_is_auth_or_quota_failure`;
`TerminalAuthFailure` subclasses `BaseException` (propagates through
`asyncio.gather`) but not `WorkerError`. `tests/test_terminal_auth_routing.py`
covers the `claude_p`/`main()` seam separately (same assertions, plus
`_cleanup_on_abnormal_exit(st, full_purge=False)` and `abnormal = False`) —
doc-conformant per `docs/IMPLEMENTATION.md` §3 "Auth/quota backoff" after
commit `2652319` reverted an over-application of the reroute to the
transient-backoff case.

## 2026-07-19 incident: argv E2BIG and coverage-gate freeze

The 2026-07-19 incident (`argv-e2big-and-coverage-freeze`) — combined argv
E2BIG crash (root cause B) and coverage-gate freeze (root cause A) — has a
dedicated end-to-end harness in `tests/test_incident_2026_07_19.py`, backed
by synthetic, shape-matched fixtures
(`tests/fixtures/incident_2026_07_19/{shape.json,generate.py}`): task
51,142B, `subtask_views` 88,201B at `indent=2`, 114 subtasks, a 15-item
CLAUDE.md heading harvest split into 3 uncoverable convention items and 12
other headings — the real internal-audit task file is deliberately not
committed. `TestRootCauseB_ArgvE2BIG` pins that the generated ~150KB payload
exceeds `MAX_ARG_STRLEN` (131,071B) as a single string, and that `claude_p`'s
real `build()` closure constructs no argv element over that ceiling, routing
the prompt over stdin and the appended system prompt through
`--append-system-prompt-file`. `TestRootCauseA_CoverageFreeze` pins the
incident's exact 15-item harvest shape and that the mechanism which froze on
it (`extract_task_file_structure`, `_is_uncoverable_convention_item`,
`check_task_file_coverage`, `_dedup_frozen_coverage_issues`) is deleted
rather than guarded — coverage is now `task_coverage_judge`'s job, so the
freeze class cannot recur. `TestBothRootCausesComposeOnOnePayload` runs both
halves against the same fixtures in one test.

## Lessons worth keeping

**A handler must survive its own exception, not merely catch it.** `main()`'s
`except DiskLowSpace` arm opened with an unguarded `st.save()`, and
`State.save()`'s own out-of-space conversion is one of the three raise
sites — so on the disk-full path the save re-entered the failure and raised
again *from inside the handler*, escaping `main()` and skipping cleanup, dep
capture, and the `EXIT_LOCKED` assignment. `test_survives_a_save_that_is_still_failing`
in `tests/test_disk_preflight.py` now asserts against *every* save in the
arm; fixing that one arm was not the fix — eight other handlers carried the
identical bare `st.save()` (including `except BaseException`, where a raise
hides the real bug in `__context__`), all now routed through
`_save_state_best_effort` (logs, never raises — read-only run dirs raise
`PermissionError`, measured). The test it replaced asserted only
`issubclass(DiskLowSpace, BaseException)`, a tautology that reasoned to a
false conclusion.

**A timeout is infrastructure, not a leerie bug.** `_invoke` converts
`asyncio.TimeoutError` into `subprocess.TimeoutExpired` — an `Exception` but
**not** a `WorkerError` — so every `_run_checked_loop` caller took its
generic-bug arm and `die()`d instead of retrying, and five bare `except
WorkerError` sites let it escape entirely (the per-worker timeout table made
this ~4x more reachable for the 18 workers whose ceiling it lowered, though
the stated motivation was a hung `classifier`). The retry arm and those five
sites now name `subprocess.TimeoutExpired` alongside `WorkerError`. Never
interpolate one into a message: `str()` on a `TimeoutExpired` renders `cmd`
— the entire `claude -p` argv with an inlined system prompt, the 50 KB
terminal dump `_run_implementer`'s handler documents.
`_brief_worker_exc` names `exc.timeout` instead; `tests/test_checked_loop.py`
pins both the retry and that the argv never reaches a warning line.

**Derivation guards are one-directional unless you write the converse.**
Both `TIMEOUT_DEFAULT_PER_WORKER` tests iterated the *table*, so deleting
five entries passed the whole suite while those workers silently reverted
to the 5400s global; `test_every_measured_worker_below_the_cap_is_IN_the_table`
iterates the measured summary instead. `main()`'s caps wiring had the same
shape: its guard asserted the value line plus `"args.worker_timeout"`, which
appears *on* that line, so deleting the explicitness assignment left it
green.

**Ablate a pattern against its corpus instead of adding alternatives.**
`_host_finalize_is_auth_or_network_push_error` carried 11 alternatives;
removing each in turn showed only three load-bearing for the 9 real git
cases, and four were unreachable false-positive surface behind the
`^(fatal|remote):` anchor. Dropping them kept 9/9 and fixed three hook
misclassifications. `_host_finalize_ssh_transport_failure` was **provably
dead**: its condition was a line the first arm already matched, which ran
first.
