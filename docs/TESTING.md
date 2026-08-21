# Testing notes

This file is the per-feature / per-incident testing coverage inventory for
this repo: which test file covers which surface, and the specific traps
that shipped once and are now pinned against regressing. It is deliberately
separated from `CLAUDE.md`'s `## Testing` section (`CLAUDE.md` keeps only
the load-bearing operational rules a session needs before running the
suite — concurrency, PATH, subreaper, mid-run-edit, `jq`/`shellcheck`
gating — plus a pointer here) per the "Commit messages are the permanent
record" principle in `CLAUDE.md`: historical per-incident detail belongs
in a durable record, not perpetually accreting in the file loaded into
every session.

No coverage target is set — the suite was introduced from scratch and a
number now would be arbitrary.

`pytest tests/` from the repo root. Tests cover the deterministic
enforcement functions (`resolve_leerie_root`, `resolve_source_of_truth`,
`resolve_runtime`,
`gather_answers` validation gate, `_retryable_failure`,
`check_merge_committed`, `_validate_result`, `_validate_plan`,
`_validate_run_json`, `_derive_run_status`, `_load_blt_config`,
`resolve_blt`)
including a coupling test that the
retry-policy markers match the live check-function strings.
The `--aws-region`/`--aws-profile` knobs (which region/profile leerie
itself uses when provisioning `--runtime ec2` machines, distinct from the
AWS SDK's own credential-chain env vars) are **launcher-owned** and covered
in `tests/test_resolve_aws_launcher.py`. They were orchestrator-resolved
until 2026-08-10, into `args.aws_region`/`args.aws_profile` — which nothing
read, since the orchestrator runs inside the container where a host-side
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
`tests/test_resolve_aws_prefs.py` pinned the argparse flag and the
resolver and passed for months while the value reached nothing.
`tests/test_no_dead_resolutions.py` generalises that: no
`args.X = resolve_Y(...)` may go unread. It is the sibling
`tests/test_no_dead_functions.py` cannot be — that one scans for
unreferenced *functions*, and these resolvers **were** referenced, by the
dead assignments themselves. Its reader count must exclude each
assignment's own RHS (every resolution passes its current value in as the
CLI tier); without that exclusion the sweep reports zero dead resolutions
on the tree that had two. The launcher-side
EC2 instance-shape vars (`LEERIE_EC2_AMI`/`_INSTANCE_TYPE`/`_KEY_NAME`/
`_SECURITY_GROUP`/`_SUBNET_ID` — the five `RunInstances` params, distinct
from the region/profile prefs above) are covered in
`tests/test_resolve_ec2_vars.py`: the bash `_resolve_ec2_knob` CLI > env >
`leerie.toml` > (no default) ladder — **extracted** from the launcher at
test time by `_extract_resolve_ec2_knob()`, not reproduced. It was a
hand-written copy until 2026-08-10, which was body-blind *by construction*:
the tests executed a string literal defined in the test file, so no change
to the launcher could reach them, while `test_block_present_in_launcher`
pinned only the helper's name plus the flag/toml-key strings and never its
logic. `tests/test_no_duplicate_ec2_knob.py` keeps it the only
implementation — one of a family of guards of this shape, alongside
`tests/test_no_duplicate_state_walks.py` and
`tests/test_no_duplicate_launcher_splitters.py` (which guards the shared
derivation in `tests/launcher_blocks.py`), and now generalised over eight
bash blocks by `tests/test_no_duplicate_launcher_blocks.py` (below) —
with its marker anchored to the **start of a line**, since a reproduction
opens the body at column 0 while a legitimate reference
(`src.index("_resolve_ec2_knob() {")` inside an extractor) is always quoted
mid-line; matching the bare token would flag the very extractors the guard
exists to encourage. **A falsification trap worth remembering:** the first
attempt to prove the old copy was blind deleted the helper's `[ -f ]`
guard, and that passes either way — removing it is behaviourally inert,
since grep on a missing file already fails silently. Inverting the CLI/env
precedence is the sabotage that discriminates (5 failures with the
extraction, 0 with the copy). A falsification that changes no observable
proves nothing. Also covered: per-var isolation,
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
`kill --machine-id <id>` with **no run dir** still reaps, with the
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
the fail-closed `_enforce_and_record_cgroup_containment`; `tests/test_cgroup_broker.py`
covers the root broker (`scripts/cgroup-broker.py`) — protocol dispatch,
sid validation, v1/v2 path selection, and the startup **orphan sweep** — against
a fake cgroupfs. Neither can catch **wire-protocol drift between the two**,
which `tests/test_broker_wire_contract.py` exists for: the broker composes
`slice` (4 tokens) and `stat` (5) while the orchestrator parses them, the
field count is hand-written on both sides, and until that file nothing
compared them. Drift is silent in the worst way — both parsers return
`None` on a mismatch and `None` legitimately means "containment is off", so
a drifted `slice` makes worker sizing fall back to `/proc/meminfo` **and**
turns the admission gate into a no-op, while a drifted `stat` disables
PID-exhaustion detection and memory-OOM naming, with nothing logged and no
test failing. The existing two cannot see it by construction:
`test_cgroup_helpers.py` feeds leerie's parser hand-written fixture strings
(parser vs. fixture, not parser vs. broker) and `test_cgroup_broker.py`
never touches leerie's parsers. The contract file is the only place the two
meet — the **real** broker's emitted string fed to the **real** leerie
parser, with the socket the only thing stubbed — and it carries an
anti-vacuity test proving the guard fires on an added/removed field, plus a
transposition check (right arity, wrong order) driving the parsed values
into `_worker_memory_ceiling` and the headroom comparison. Same class as the
`collect-subtrees.sh` schema duplication above, which had already drifted in
production before its guard existed. Note the burst-reservation state
`_active_admissions` (token → monotonic stamp, mutated by
`_await_worker_memory_admission` and `_release_worker_memory_admission`) is
**module-level** and conftest's `leerie` fixture is **session-scoped**, so
`tests/test_slice_aware_memory.py` clears it in an autouse fixture on both
sides — without that its burst tests are order-dependent and leak
reservations into every other file that exercises the gate; a
guard-the-guard test source-couples to the fixture's `scope="session"` so a
scope change forces that reasoning to be re-examined.
`tests/test_memory_admission_degrade.py` covers the **first** admission stage,
`_degrade_max_parallel_for_wave` — the synchronous wave-entry shrink that sizes
a wave's `asyncio.Semaphore` to real headroom so the blocking gate rarely has
to act. It carries the same autouse `_active_admissions` reset for the same
reason, and its load-bearing test is
`test_uses_the_same_signal_as_the_blocking_gate`: the degrade and the gate must
both read `slice_max - unreclaimable`, because two signals could disagree about
one slice — sizing a wave down against page-cache pressure the gate then admits
into. Note `test_is_synchronous_not_a_per_spawn_gate` strips the docstring via
`ast` before scanning for `await`: the docstring names
`_await_worker_memory_admission` on purpose, and a naive substring check
matches the prose describing the thing it forbids (the same trap the
zombie-reaper guard documents above). Those burst tests use
the **measured** production density (15 worker starts per 180 s, from real
runs' `calls.ndjson`) rather than a number that looks representative: an
earlier revision bounded reservations by elapsed time instead of by worker
lifetime, which stalls every real run — its tests used 5, under
`max_parallel`, and passed against the defect, which first bites at 9.
The **repo-declared-heap reconciliation** (N14-16, DESIGN §6 — a repo's own
`--max-old-space-size` overrides whatever heap Node would infer from the
cgroup, so the per-worker ceiling must be reconciled against it) is covered by
`tests/test_worker_heap_ceiling_reconcile.py` and
`tests/test_worker_memory_heap_reconcile.py`, with the P9 `NODE_OPTIONS`
injection in `tests/test_node_options_injection.py` and the resolver chain in
`tests/test_resolve_worker_memory_max.py`. Four traps here are not obvious
from the test names. **(1) The two error directions are not symmetric, and
the asymmetry sets which tests matter.** The declared heap RAISES the cage
(`needed = declared_heap + _NODE_HEAP_HEADROOM_BYTES`), so over-detecting a
script name only inflates the cage and throttles admission, while MISSING one
under-sizes it and the worker OOMs — the failure the whole reconciliation
exists to prevent. `_pm_script_candidates` is therefore deliberately
over-inclusive, and three narrowings (abandon on `exec`/`dlx`, stop at `--`,
drop `npx`) were prototyped and rejected because each introduced misses;
`test_candidate_extraction_stays_over_inclusive` pins that intent at the unit,
including a block of rows guarding the shapes `_SEG_RE` must not break
(`2>&1`, `&` inside an argument, a PM after the separator) — those pass under
both the current and the superseded implementation on purpose, so they are a
guard against the next edit to that regex, not a second proof of the fix.
**(2) `_SEG_RE` splits before tokenising** because testing each
whitespace-split token against a separator set cannot see `build&&node`, which
is one token; the old form lost the real script on every space-free separator.
**(3) A config.toml fixture cannot reach the real code path** — measured
across the five repos leerie manages, 2 of 5 declare a heap in `package.json`
and **0 of 5** in `.leerie/config.toml`, so the original config.toml-only
suite reported full coverage while the reconciliation fired on none of them.
The second half of the file builds package.json fixtures for that reason.
A related trap: `_write_config` interpolates into a TOML **basic string**, so a
command containing a literal newline produces invalid TOML, the whole config is
silently dropped, and the assertion is answered by BLT inference instead — one
parametrization passed that way while testing nothing, which is why the newline
case is asserted at the unit rather than end-to-end.
**(4) `_NODE_HEAP_HEADROOM_BYTES` needs its own value pin.** Both P9's
injection and the reconciliation now derive from it (they compute mirror
images of one quantity and briefly disagreed, handing Node a heap 384 MiB
larger than fits the cage), which is the coupling we want — but it means every
assertion moves with the constant: setting it to `243 * 1024 * 1024` once left
the entire suite green. `test_node_heap_headroom_is_2432_mib` anchors the
value, and the AST pin resolves the reserve **name's binding** rather than
merely requiring an `ast.Name`, so `reserve_mb = 2432  # <const>` fails.
`tests/test_decompose_share_advisory.py` covers `_warn_decomposition_share`,
whose one non-obvious test is the partial-caps case: every other test supplies
`max_total_workers`, so the `.get()` fallback is only exercised by a caller
that omits it (`run_recapture_deps`, `run_rebaser`, `_replay_capture` each
build their own minimal caps) AND only on the branch that runs when the
warning fires.
The **production-grounded evidence gate** (DESIGN §9 — every other gate asks
whether the code matches its specification, none asks whether the
specification matches reality) is covered by `tests/test_production_evidence.py`
and `tests/test_unreviewed_subtasks.py`. Three traps are not obvious from the
test names. **(1) The field is optional in the schema and gating in the
check, and that is ONE decision, not two.** Requiring it costs the entire
submission rather than the one field — `_confidence_schema`'s docstring
records that measured at 40.9% valid on `plan_overlap_judge`, with 84 of its
85 failures being a single required field — while gating on absence is what
stops "optional" from meaning "ignorable". `test_schema_field_is_optional_at_the_top_level`
and `test_absent_evidence_gates` are anti-vacuity partners: remove either and
the field becomes decorative. The object is also flat with one required inner
bool for the same decoder-corruption reason (anthropics/claude-code#49747).
**(2) The conformer's copy must be READ, not merely declared.** It shipped
once as a dead field — on both schemas, with exactly one call site and no
mention in `conformer.md` — while DESIGN and IMPLEMENTATION both described it
as consumed. It is wired **advisory** (extends `conf_warnings`, never
`blocked_reason`), because `solution_defects` is deliberately the one gating
conformer axis and an advisory phase must not gain a new way to stop a run;
`test_conformer_side_is_advisory_not_gating` pins the distinction by
inspecting the statement, not just the call. **(3) Source scans here strip
comments first.** Both `conformance[sid] = {...}` write sites carry comments
naming `unreviewed_subtasks` while explaining why one of them must not touch
it, so a raw substring scan matches the prose describing what it forbids and
fails on correct code — the same trap the zombie-reaper guard documents. The
helper also bounds each site at its closing `st.save()` rather than by a
character count: a fixed window was tried twice and truncated mid-statement
both times, reporting a key as missing when it was merely past the cutoff.
Note the two write sites mean different things — the mid-run satisfied-rescue
sentinel sets `reviewed: False` but is deliberately excluded from
`unreviewed_subtasks`, since a zero-commit subtask has no diff to review and
folding it into the operator warning is how a warning becomes noise.
`tests/test_symptom_evidence.py` covers `check_symptom_evidence`, the sibling —
scoped by the planner's `fixes_reported_symptom` declaration, never by an id
prefix — that asks whether the reported symptom still reproduces
on the base tree — run fa979580's N18 subtask re-fixed a leak an earlier PR had
already fixed, shipping an event-loop stall on the way. Three traps. **(1) It is
advisory and must stay that way**: the output never reaches
`check_implementer_output`, because those issues drive
`implementer_confidence_retries` and a retry cannot make a stale finding
un-stale — it asks the same worker the same question — while a second *gating*
evidence field would stack retry pressure on the production-evidence gate.
`test_not_wired_into_the_gating_check` is the pin, and making it gating fails
exactly that test. Advisory does NOT mean ephemeral: the findings are also
persisted to `symptom_findings` in `state.json` (results are in-memory only —
`phase_execute` writes just `blocked` reasons out of them), cleared for a sid
whose later attempt reports cleanly so a re-driven subtask carries no stale
entry, and surfaced by `phase_finalize` for the `SYMPTOM_DID_NOT_REPRODUCE`
case ONLY — `NO_SYMPTOM_EVIDENCE` is worker hygiene and a summary line that
fires every run is how a warning stops being read. **(2) "The new tests fail on base" is NOT this and is
worthless** — measured on that run all four findings' tests already failed on
base (9 of 13 for one), because a new test against absent code trivially fails;
the field therefore asks for a command and an observation, not a bare boolean.
**(3) Scoped by the planner's `fixes_reported_symptom` declaration**, never by
the subtask's id — that was the original design and it produced 10 of 10 false
positives, because `_repair_prescribed_commands` mints ids from the HOST
subtask's domain and a merge re-homes a `feat-` subtask under a surviving
`bugfix-` id. *Language-to-JSON* is usually read as being about prose, but an
identifier is a string too. The `sid` still comes from the ORCHESTRATOR, never
from the worker's echoed `result["subtask_id"]` — nothing in the module
cross-checks that
echo, which is precisely why the id is not the scope signal. Taking the sid
string rather than the subtask dict
also removes a `None`-dereference by construction, and neither this check
nor `check_implementer_output` coerces a bad argument (`sid or ""`,
`subtask or {}`): both shapes swallow a contract violation and leave the
check silently disabled — measured, an empty subtask dict makes
`NO_PLANNED_FILES_TOUCHED` unable to fire — so both raise instead, each
pinned by its own non-coercion test.
The same change wired `check_production_evidence` into `_run_final_conformance`
— #197 wired the per-subtask site and missed the whole-tree pass, which is the
last gate before a run is declared done and the one that certified four inert
fixes at confidence 8.5. It runs after `_validate_conformance_result` so a
shape-rejected payload is not also reported as missing a field.
Memory-OOM naming
(DESIGN §6 *Detecting memory OOM*) —
the `empty_handoff` seam that prefers a worker's named OOM cause (offending
command + `memory.max`) over `_validate_result`'s generic "checkpoint ...
does not exist" text — is pinned end-to-end through `_settle_subtask` in
`tests/test_oom_naming.py`: both empty_handoff branches (the no-commits
`fail()` path and the has-commits rescue path that keeps the diff and
logs instead of discarding it) surface the named cause when
`_run_implementer`'s synthesized `incomplete-handoff` envelope carries one,
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
that `main()` calls `_become_subreaper()` and `_orchestrate()` spawns+cancels
`_zombie_reaper`. Three further surfaces arrived with the `PENDING_ISSUES.md` work order and are
catalogued here because their traps are not obvious from the test names.
`tests/test_duplicate_provider_merge_routing.py` (7 tests) pins that
`check_duplicate_providers`' detections are routed into a **merge** resolution
and never a drop — the transitive `survivor_of` chase is safe for a merge
(intent carries forward) and silently destroys a live subtask for a drop, which
is the hazard `_apply_multidrop` documents above. The floor had been advisory
only: measured across the run corpus, **4 of 5 runs where it fired applied zero
resolutions**, one of them with 35 detections and no action.
`tests/test_recursive_decompose_parallel.py` (4 tests) pins that `phase_plan`'s
expansion loop — previously a plain sequential `await`, measured at ~0.7x
parallelism — now bounds concurrency with the **existing** `_gather_or_cancel`
while preserving `decompose_snapshot`'s per-completion write, including the
`list(leaves)` copy that keeps a later crash from mutating an already-taken
snapshot (the aliasing class `test_checkpoint_aliasing.py` exists for).
`tests/test_require_fly_ssh_isolation.py` (8 tests) pins
`_leerie_fly_agent_ensure`'s reuse predicate, whose exit codes are the whole
point: `ssh-add -l` returns **1** for a reachable-but-keyless agent, **0** with
a key, **2** for a dead socket (verified live). Treating rc 1 like rc 2 `rm -f`s
a live agent's socket out from under it, orphaning the process — which is the
leak. The `-t 24h` on spawn bounds the **identities**, not the agent process,
so it is not an orphan mitigation; see `scripts/remote/lib.sh`'s comment.

The `fetch_branch()` stream-back surface (`scripts/remote/fetch-branch.sh`)
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
fixture matrix so the two can never silently diverge. The `group`
launcher arm and group-scoped ID-dispatched verbs are tested in
`tests/test_group_launcher.py` via the same bash-harness pattern
(stubbed `./leerie`, multi-state-dir fixtures), modeled on
`tests/test_chain_launcher_id_dispatch.py`. Group-scoped verb dispatch
across two state dirs (combined paused/unpushed + pushed fixture, plus
`stop` dispatch) is covered by `tests/test_group_launcher_verbs.py`.
Fan-out core contract (cwd per member, `--inspect-dir` for siblings,
brief prepend) is in `tests/test_group_launcher_fanout.py`.
Python-layer `group_id` in `run.json` (`_validate_run_json`,
`_write_run_json`, `_derive_run_status`) is in
`tests/test_group_run_json.py`. State-dir isolation (distinct
basename-keyed dirs per member, guard rejects `LEERIE_STATE_DIR`/
`--state-dir`) is in `tests/test_group_state_dir_guard.py`. That file's class-A harness
**extracted** `_state_dir_default` from the launcher rather than reproducing
it as of the N13 follow-up — it had been a hand-copy whose own docstring
cited nine launcher line numbers, every one stale (the block had moved to
`:785-844`), so no change to the launcher could fail it. Those citations
are now corrected in place rather than deleted — they are what makes the
extraction's target legible.
It now imports `_extract_state_dir_block` from
`tests/test_resolve_state_dir.py`, which is the single owner of that
extraction and has two importers (this file and
`tests/test_launcher_state_mount.py`). The
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
filtering, and malformed-line tolerance. It also carries the
guard-value-that-cannot-guard regression pin (bugfix-004, incident
2026-07-19): `test_depcap_budgets_not_argv_bound_by_source` asserts via
source inspection that the `_DEPCAP_TOTAL_BUDGET` comment states the
dep_capture payload travels over stdin (not argv) and names
`MAX_ARG_STRLEN` rather than the aggregate `ARG_MAX`;
`test_depcap_total_budget_value_unchanged_since_incident` pins
`_DEPCAP_TOTAL_BUDGET`/`_DEPCAP_MANIFEST_TOTAL_BUDGET` unchanged and that
their combined bound still exceeds `MAX_ARG_STRLEN` — safe only because
the payload is stdin-transported (bugfix-001), not argv-bound. Since
DESIGN §6½ moved the worker to a
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
`MODEL_DEFAULT_PER_WORKER` (sonnet via the global `MODEL_DEFAULT` fallback), and
present in `EFFORT_DEFAULT_PER_WORKER` with value `"medium"`.
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
directly (no `_build_repo_map`) — isolates ranking. No LLM calls; deterministic.
<!-- docs-001-f1-r3: lines 1403-2083 of the pre-split CLAUDE.md -->
The P1 recursive decomposition surface (DESIGN §5½ (P1)) is tested across four
files. `tests/test_fit_judge_schema.py` covers `SCHEMAS["fit_judge"]` —
required fields (`score`, `rationale`, `diffuse`, `confidence`), `score`
bounds (minimum 0, maximum 1), `confidence` using the `"fit"` axis, valid and
invalid instance acceptance, JSON serializability, and wiring (`fit_judge` in
`WORKER_TYPES`, not in `MODEL_DEFAULT_PER_WORKER`, `EFFORT_DEFAULT_PER_WORKER`
entry at `"medium"`, prompt file exists). `tests/test_splitter_schema.py` covers
`SCHEMAS["splitter"]` — `children` required but with **no `minItems`** (an
empty array is the valid answer "this does not split"; `minItems:1` was removed
2026-08-03 after the corpus showed the splitter returning `[]` 43 times and a
single no-op child 43 more, every empty return rejected and retried even though
`_recursive_decompose` already accepted it as a leaf), child required
fields (`id`, `title`, `success_criteria_seed`), optional child fields,
valid/invalid instances, JSON serializability, the same wiring guards, no
top-level `files` field (splitter never decides partition), and the child
`requires` array uses the `_REQUIRES_ITEM` shape (tag + extent enum).
`tests/test_resolve_fit_judge_model.py` and
`tests/test_resolve_fit_judge_splitter_model.py` cover model and effort
resolution for `fit_judge` and `splitter` — both in `WORKER_TYPES`; both absent
from `MODEL_DEFAULT_PER_WORKER` (sonnet via global `MODEL_DEFAULT` fallback); both
in `EFFORT_DEFAULT_PER_WORKER` at `"medium"`; per-worker CLI/env/TOML override
chains; isolation (override doesn't bleed to other workers); structural wiring
guards. `tests/test_partition_files.py` is the dedicated test for `_partition_files()`:
44 tests across parametrized invariant sweeps (input sizes 0, 1, 8, 29, 64;
chunk-size 1, equals-n, larger-than-n, partial-last-chunk) plus named
telemetry cases — the 29-file migration sweep and 64-file date-fns sweep that
drove the design (LLM silently dropped 14/29; code-partition is complete by
construction). Asserts: 100% coverage (sum of chunk lengths == len(input)),
zero overlap (no file in two chunks), chunks bounded by chunk_size, and order
preserved. `tests/test_recursive_decompose.py` covers `_recursive_decompose()`
(well-fit subtask is a leaf at score ≥ 0.70, oversized subtask recurses then
children are judged, depth cap terminates at `decompose_max_depth`, no-progress
guard terminates after `decompose_noprogress_rounds`, migration path uses
`_partition_files` for the file→chunk partition and invokes the splitter only in
label-only mode to title each chunk (distinct titles; deterministic fallback on
splitter failure), `st.bump_workers` called before every `claude_p`, both
`claude_p` call sites pass the full required signature (`cwd`/`autonomous`/`caps`
— the C0 regression guard), and a passed `repo_map` is re-ranked per node and
injected into fit_judge/splitter prompts); it also carries a parallel set of
structural `_partition_files` tests for regression coverage within that file.
`tests/test_recursive_decompose_schedule.py` is the integration test for the
seam between Layer B and the existing scheduler (DESIGN §5½ (P1) end-of-pipeline
claim): leaf ids from `_recursive_decompose` carry a valid domain prefix so
`_schedule()` cross-domain wiring and `_validate_plan`'s id-prefix check both
pass; a ready plan built from stubbed leaves feeds `_schedule()` and produces
the correct topo-sorted wave partition (independent leaves in wave 0, a
dependent leaf in wave 1); and `_validate_plan` accepts the full leaf set
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
(G6: `_build_repo_map` warns exactly once per process when source files exist
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
(a probe-only raise surfaces `"Probe failure:"` plus the exception type and
message in the logged warning) and
`test_no_probe_detail_when_empty_result_is_not_an_exception` (the existing
plain empty-graph path grows no spurious detail).
A related production failure surfaced the root cause for the `/tmp/.cache`
fix below: `RuntimeError: Download cache lock error: create cache dir
/tmp/.cache/tree-sitter-language-pack/v1.12.5: Permission denied (os error
13)`. Root cause (verified live against both the pre-fix and fixed image
via the real `unshare --user --map-user=$(id -u leerie)` mechanism
container-entry.sh uses under rootless containerd): that unshare remaps
only outer UID 0 -> inner leerie, so a directory explicitly chowned to
leerie's own (non-zero) UID is NOT covered by the remap and
appears owned by nobody/65534 to the privilege-dropped process: traversable
via mode-755 "other" bits, but not writable. This is the same bug class
already hit for corepack (`COREPACK_HOME` above, worked around with its own
dedicated bind-mounted cache dir rather than fixed at the source) — chasing
each offending tool down individually doesn't scale, so the fix instead
makes `/tmp/.cache` itself world-writable with the sticky bit (mirroring
`/tmp`'s own `drwxrwxrwt`) at both the Dockerfile (build time, the layer
rootless runs rely on exclusively) and `container-entry.sh` (a runtime
safety net for the rootful/Fly path, mirroring the existing `chown`
pattern there). `tests/test_tmp_cache_writable.py` pins both sites
source-coupled (mirroring `test_rootless_host_uid.py`'s extraction style):
the Dockerfile's build RUN step and `container-entry.sh`'s rootful-guard
block both carry `chmod -R a+rwX /tmp/.cache` + `chmod 1777 /tmp/.cache`
after the existing `chown`, and the runtime chmod specifically lives
*inside* the `ROOTLESS != true` guard (rootless has no runtime fixup path
and relies on the image's baked-in mode alone).
The same bug class hit `/home/leerie/.local`, `.cache`, and `.gnupg` directly
(`pip install --user` failing with `EACCES: /home/leerie/.local/lib`), fixed
in `tests/test_home_leerie_ownership.py` (same source-coupling style, but a
chown-back rather than a chmod-world-writable, since these are a fixed set
of pre-created dirs rather than an arbitrary-tool `XDG_CACHE_HOME`): the
Dockerfile's `mkdir` block for these dirs carries no `chown` (root-owned,
which is what maps correctly through the rootless remap — the same
mechanism that already makes bind-mounted host dirs writable with no chown),
`chmod 700 /home/leerie/.gnupg` is kept, and `container-entry.sh`'s
rootful-guard block chowns `/home/leerie`, `.local`, `.cache`, and `.gnupg`
to `leerie` at runtime instead (needed there since the rootful `runuser -u
leerie` drop is a real UID switch, not a remap). A dedicated test asserts
the `/tmp/.cache` fix above is unchanged by this.
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
`tests/test_repo_map.py` covers `_build_repo_map` (symbol/def extraction, class
methods, ref edges, relative-path keys, empty-repo, skip-.git/node_modules),
the mtime cache (dir created on first use, unchanged file served from cache
sentinel, changed file re-parsed, only-changed file re-parsed),
`_rank_repo_map` (string result, token-budget fits, seed-file/seed-symbol bias,
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
baseline keys present, values match inputs); empty-repo degrade (`_rank_repo_map`
returns `""` → key omitted); exception-swallow degrade (`_build_repo_map`
raises → exception caught, ctx emitted without `repo_map`).
The P1 Layer C wiring — `phase_plan` recursion expansion — is tested in
`tests/test_phase_plan_recursion_wiring.py`: source-coupling guard (`phase_plan`
source contains `_recursive_decompose(` at depth=0, reassigns `plan["subtasks"] = leaves`,
expansion loop precedes final logging); integration — one oversized subtask (stubbed
`_recursive_decompose` → two leaves) → `plan["subtasks"]` has 2 entries; two
first-pass subtasks → `_recursive_decompose` called once per subtask; well-fit
leaf pass-through (stub returns input unchanged → single-element `plan["subtasks"]`);
empty-subtasks plan not touched (`_recursive_decompose` never called, subtasks stays `[]`).
The plan-instruction-adherence gate's worker registration (schema, prompt,
model/effort defaults) is tested in two files mirroring the
`fit_judge`/`splitter` pair above. `tests/test_adherence_judge_schema.py` covers
`SCHEMAS["adherence_judge"]` — required fields
(`user_prescribed_a_procedure`, `instruction_adherence`, `violations`,
`rationale`), `instruction_adherence` bounds (0–10), the deliberate absence
of a nested `confidence` sub-object (this worker is itself the independent
check that replaces a self-report, so a self-confidence axis would
reintroduce the self-grading bias the gate exists to remove), valid/invalid
instance acceptance, JSON serializability, and wiring (`adherence_judge` in
`WORKER_TYPES`, absent from `MODEL_DEFAULT_PER_WORKER` so it resolves to
sonnet, `EFFORT_DEFAULT_PER_WORKER` entry at `"medium"`, prompt file exists).
`tests/test_resolve_adherence_judge_model.py` covers the model/effort
resolution precedence chain (mirrors `test_resolve_fit_judge_model.py`),
explicitly asserting the sonnet default. **History:** an earlier Sonnet
generation was empirically falsified here (false-positived a legitimate
plan), which required pinning this worker to opus specifically, and an
opus *understanding*-framed judge separately rubber-stamped the incident
(only the ADHERENCE frame was validated, independent of tier). Both gaps
are understood to have closed for Sonnet 5 (DESIGN §5
*Opus-judgment, sonnet-workhorse (historical)*); if this gate is ever
observed to regress, re-run the calibration before reintroducing a
per-worker opus override.
The deterministic PRIMARY layer of the same gate,
`check_prescribed_command_coverage(prescribed_procedure, subtasks) ->
list[str]` (pure JSON→verdict set logic, no NL parsing), is tested in
`tests/test_prescribed_cmd_coverage.py`: the motivating incident shape
(prescribed `recon browser`/`recon generate`, no subtask's `runs_commands`
covers either → both fire), a goal-only task (`is_prescribed=false` or
`commands=[]`, including `None`/`{}`) staying silent, paraphrase coverage
(the planner's `runs_commands` wraps the prescribed command's own tokens in
extra words, e.g. "barnacle recon browser" covers "recon browser", via
normalized lowercased/stopword-filtered token-SUBSET matching — not exact
string equality), full and partial coverage, no-subtasks-at-all firing for
every prescribed command, tolerance of subtasks with missing/empty
`runs_commands` and of non-string/blank prescribed commands, case-
insensitivity, and a negative control proving a shared-stopword-only overlap
does not falsely mark a command covered. The gate's own advisory-vs-gating
outcome — distinct from the G3 `decomposition_quality`/`task_understanding`
pair above, which covers the *planner's* self-report axes, not the
adherence floor — is pinned in `tests/test_check_functions.py`'s
`TestAdherenceGateAdvisoryVsGating`: a prescribed-and-uncovered command
gates (`PRESCRIBED_CMD_UNRUN`), a goal-only task and a fully-covered
command never gate, and `check_planner_output` itself carries no separate
adherence axis to demote to advisory, since the floor is wired only into
`phase_adherence_gate`, not the planner check loop.

The task-coverage gate is **advisory** (2026-08-04). Its deterministic
floor, `check_required_items_coverage`, was deleted: it required one
subtask's token set to be a SUPERSET of a required item's, and across every
run that ever carried `required_items` it passed **0 of 102 items** — a 100%
false-positive rate with no true negative in its history. It also violated
the *Language-to-JSON* rule above, since `required_items` are LLM-written
sentences. Its judge is retained but non-terminal: re-invoked on identical
input it returned a different finding set 85% of the time (n=20) and the
intersection across repeated samples was empty. `tests/test_phase_planning_coverage_gate.py`
pins the advisory contract and the floor's absence.

`migration_targets` carries a gap of the same shape — optional on the
subtask schema, and silently no-op when a planner omits it — closed by a
narrow, same-worker mechanical cross-check: `performs_replacement: bool` on the subtask schema
(alongside, not nested inside, `migration_targets` — that object's
`additionalProperties: False` forces this), and
`_check_migration_targets_declared(subtasks)` flags `MIGRATION_TARGETS_MISSING`
when `performs_replacement=true` but `migration_targets` is empty. Tested in
`tests/test_migration_surface.py`'s `TestCheckMigrationTargetsDeclared` (the
contradiction fires, both-empty and both-populated stay silent, independent
per-subtask evaluation) and `TestPerformsReplacementSchema` (field shape,
optional, sibling-not-nested). This is explicitly **not an independent
witness** — documented as such in DESIGN §8 and in the check function's own
docstring — since both signals come from the same non-adversarial planner
self-report; it closes the "forgot to fill the field" case, not the
"consistently wrong on both fields" case.

The gate wiring itself — `phase_adherence_gate`, the whole-plan "Phase 2⅞"
gate run after `phase_overlap_judge` and before `_schedule()`/`_validate_plan`,
composing the deterministic floor and the `adherence_judge` behind
`_run_checked_loop` — is tested in `tests/test_phase_adherence_gate.py` (22
tests), split into source-coupling wiring pins (floor+judge both run; a low
result routes through the retry path via a re-invoked `phase_plan`; a
`WorkerError` never discards the plan; the call site precedes
`_schedule()`/`_validate_plan`, ordered after `phase_overlap_judge`) and
behavioral integration tests against a stubbed `claude_p` and a stubbed
`phase_plan` (skip-flag and not-prescribed short-circuits returning `plans`
unchanged; a clean plan passing without a re-plan; a low-adherence round
triggering exactly one re-plan that then converges; exhaustion `die()`ing
with the unresolved violations; and the two `WorkerError`-every-round
degrade outcomes — a clean floor returning the plan unmodified, a violating
floor still `die()`ing).
The two-stage gate's composed end-to-end behavior — deliberately independent
of `test_phase_adherence_gate.py`'s per-branch wiring pins — is locked as a
regression fixture in `tests/test_adherence_gate_e2e.py`: a synthetic
incident shape (prescribed `[foo:build, foo:generate]`, no subtask's
`runs_commands` covers `foo:generate`) drives `check_prescribed_command_coverage`
directly (no stubbing) to prove the floor fires before any judge/re-plan
involvement, then drives the full `phase_adherence_gate` (opus
`adherence_judge` stubbed to a fixed envelope, mirroring
`test_dep_capture_worker.py`'s `_invoke` stub) to prove it re-plans exactly
once via the existing `phase_plan`-retry path and converges on a plan the
floor accepts; a synthetic ordinary shape (`prescribed_procedure` absent, or
present with `is_prescribed=false`) proves the gate short-circuits with zero
`claude_p`/`phase_plan` calls — the corpus-validated 0/21-false-positive
result this gate exists to protect.
The empirical incident/legit calibration behind the gate's threshold — real
opus judge runs against the cruiselines incident plan and a 21-run corpus,
finding the two-stage composition (`is_prescribed=true AND (floor violation
OR low adherence)`) fires on the incident and stays silent on ordinary
goal-only tasks with 0 false positives — is frozen as a deterministic,
no-live-LLM fixture in `tests/test_adherence_gate_regression.py`, distinct
from `test_prescribed_cmd_coverage.py` (floor in isolation, synthetic
cases) and `test_phase_adherence_gate.py` (phase wiring/control-flow, ad
hoc inline stubs): canned classifier/planner JSON committed under
`tests/fixtures/adherence_gate/{incident_plan,legit_plan}.json` drives both
the floor directly and the full two-stage composition through
`phase_adherence_gate` with `claude_p` stubbed to the fixture's recorded
`adherence_judge` score. Pinned: the floor's issue count on both fixtures;
the incident floor naming both unrun commands; the legit floor staying
silent; the incident fixture driving the gate to `die()` after exhausting
the re-plan budget on a still-noncompliant plan; the legit fixture never
invoking `claude_p`/`phase_plan` at all (0 false positives via
short-circuit); and a frozen-score separation test
(`test_incident_vs_legit_judge_scores_are_cleanly_separated`) that catches
threshold drift even when the fire/silent outcome alone doesn't change.
**A fixture that pins one branch reports coverage of both.**
`tests/test_gather_answers_validation.py` asserted the source-of-truth contract
across all three values and passed, while its `state` fixture hardcoded
`"needs_source_of_truth": True` — so the file exercised only the branch where
the classifier flagged the question. On the other branch `gather_answers` wrote
nothing and its consumers fell back to a **hardcoded `"codebase"` literal**,
silently overriding an explicit `--source-of-truth research`; measured, 74 of
196 corpus runs took it. The fixture is now parametrized over both values, which
is what makes the pre-existing assertions falsify the defect: reintroducing the
guard fails 6 of them, all on the `no_needs_sot` parametrization — including
`[no_needs_sot-codebase]`, because under the guard `gather_answers` writes no
key at all and the lookup raises. **The trap that a `codebase`-only assertion is
answered by the very literal it exists to remove lives one layer down, in the
consumers** (`phase_plan` / `_write_plan` / `_compose_pr_via_llm`), which is
where `tests/test_source_of_truth_delivery.py` aims it — its spec-file check
loops over `research` as well as `codebase`, and its
`_effective_source_of_truth` table carries a row where `answers` and the
preference *disagree* (an agreeing-only table passes against a helper that
ignores `answers`). The delivery file also source-couples three of the four `State`-holding
consumers (`phase_plan`, `_write_plan`, `_compose_pr_via_llm`) to
`_effective_source_of_truth(st)` — note only the first two ever carried a
`"codebase"` default, so for the third only the presence assertion can fail;
it is parametrized anyway as the forward guard. The fourth is
`phase_reconcile`'s, nested in the `_check_unresolvable` closure and pinned by
`tests/test_unresolvable_die_message.py` instead, which AST-resolves the
argument's binding. **That the count here reads "four" at all is the product of
a guard, not of care**: this sentence said "all three" while the fourth reader
was added in the same commit, and nothing noticed until
`tests/test_effective_sot_consumers.py` derived the set from the call sites.
An enumeration nested inside a closure is invisible to the obvious AST walk —
one over each module-level `def`'s direct body — so that file's load-bearing
test is the one asserting `phase_reconcile` is in the derived set. Two further readers cannot use
the helper and are documented rather than converted: `compose_pr_body` takes a
plain `state: dict` and `scripts/host-finalize.sh` reads the key with `jq`, because pinning the writer alone leaves the
value reaching nothing — the **deleted** `test_resolve_aws_prefs.py` trap.
`tests/test_unresolvable_die_message.py` covers the phase-2½ abort text, which
is treated as behaviour rather than cosmetics: against 5 simulated operators
given a real failure, the old wording sent **5 of 5** to widen the scope fence
and **0 of 5** to remove the offending criterion — the reverse of the correct
repair on the run that motivated it. The `--source-of-truth` bullet is **demoted and
conditioned**, not deleted: DESIGN §11 calls narrowing the preference
*historically* the escape hatch, and the bullet still fires when the effective
value is not `codebase`, where it addresses a real research-surfaced case.
Operators ignored it 0/5 in both arms; the *useful* bullet is what moved them,
and is pinned with an anti-vacuity partner requiring the text to say widening
is often wrong, plus a guard that the stated shape count matches the bullets
(the first draft said "two shapes" and printed three). The message was extracted to a module-level
pure function first: it had been inline in a closure, which is why the previous
test **re-synthesized the closure body in the test file** with a local stub
calling `leerie.die("test-die: …")` — a copy that passed whether or not the
real code existed. `tests/test_reconciler_payload_fields.py` guards a
prompt↔code drift of the same family: `prompts/reconciler.md` scopes
`conditional_drop` to signals in `intent`/`scope_note` while the payload shipped
only `intent`, so half the rule's signal surface was structurally invisible. The
*shipped-fields* check derives its field list from the prompt text at test
time; a deliberately enumerated guard-the-guard sits beside it
(`test_conditional_drop_rule_still_names_both_halves`, pinning the set at
`{intent, scope_note}`), so adding a third signal field to the prompt fails it and forces the decision to be
explicit rather than silently widening the requirement. The payload keys are
read **structurally** off the `subtask_views.append` dict literal — a first draft scanned `ast.unparse` output for
`"scope_note": s.get(` and failed against correct code, because the unparser
emits single quotes.
`tests/test_planner_extent_out_of_scope.py` gained the third `extent: external`
kind (a surface the task itself fences off). Its load-bearing assertion is an
**ordering** one — the fence question must precede the connector question,
since "could a connector subtask produce this?" answers *yes* for a fenced code
change and routes it to `in_plan`; a test asserting both sentences merely exist
passes against the unfixed file, which already contains the connector one. Every prose guard in that file — presence, absence and
ordering alike — normalizes whitespace through `_norm`, so re-wrapping a
hand-wrapped markdown line is not a false alarm that tempts someone to weaken
the assertion instead of the matching. The absence guard matters most: an
un-normalized absence assertion fails silently, passing while the forbidden
phrase sits in the file across a line break. Because the prompt is advisory, those guards prove
only that the words are present: the behavioural evidence is a sandbox
experiment scoring the pre-fix prompt at 1/6 and the as-shipped wording at
17/18, p = 0.00081. The harness ships as `tests/manual/planner_fence_probe.py`
— **not** collected by pytest (`python_files = test_*.py`), same arrangement as
`tests/fixtures/incident_2026_07_19/generate.py`, because it spawns real
`claude -p` workers. It extracts the rules from the live `prompts/planner.md`
rather than reproducing them, and must be run against a **sandbox copy** of the
target repo with its planning docs removed: two earlier attempts were
contaminated when the model read a task doc that had been corrected *after* the
failing run, and instructing it not to read them did not work. **Re-run it
before trusting an edit to that section** — the first re-validation of the
real text came back 5/6 where the design draft had scored 6/6, so the sample was
extended rather than the difference assumed away. Related measurement worth
keeping: `_demote_unresolvable_with_external_twin` has **never fired in 258
recorded runs**, so every measured improvement in this area has come from the
prose, not the code backstop.
The id-vanishing `depends_on` rewrite (DESIGN §5 *Id-vanishing operations* — every op
that removes a subtask id owes the plan a rewrite of inbound references; the tag
channel self-heals via inherited `provides`, so only the id channel dangles) is tested
across five files. `tests/test_remap_vanished_deps.py` is the unit surface for
`_remap_vanished_deps`: fan-out (a vanished parent → every leaf, mirroring the tag
channel's list-of-providers), prune (`id → []`, the drop case), empty-mapping no-op,
dep-absent-from-mapping pass-through (guards over-eager rewriting), dedup-after-rewrite
and two-vanished-ids-sharing-a-successor (mirrors `_apply_overlap_merge`), and the
`repl != sid` self-reference guard — pinned but documented as **currently dead code**:
it is unreachable because `_schedule()` already die()s on a planner self-edge
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
regression (a sibling of an expanded parent fans out to all leaves and `_validate_plan`
no longer die()s — the exact gate that killed a real run after full planner spend),
a dep on an unexpanded subtask left untouched, and dedup when a sibling already names a
leaf. `tests/test_filter_satisfied_subtasks.py` and
`tests/test_filter_offtree_subtasks.py` cover the two phase-3 soft-drop filters, which
vanish ids the same way: a dropped id's inbound refs pruned (non-dropped deps survive),
`_validate_plan` survives the drop end-to-end, and a no-drop run leaves `depends_on`
byte-identical. `tests/test_plan_snapshot_wiring.py` pins the `plan_snapshot` capture by
source inspection (mirroring `test_dep_capture_wiring.py`): the assignment is followed
by `st.save()`, follows `_schedule()`, and precedes **both** `check_budget_feasibility`
and `_validate_plan` — the ordering *is* the feature, since a die() at either gate
otherwise discards the whole planning spend (`_write_plan` never runs); plus that it is
deliberately not `_write_plan` (which would seed execution scaffolding for a run that
cannot start) and that the payload round-trips through a real `State.save()`.
`tests/test_decompose_snapshot.py` is `plan_snapshot`'s sibling for the D3 crash
barrier: a `WorkerError` from `_recursive_decompose`'s `fit_judge` call degrades the
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
and `_validate_plan` — the two gates that die() and would otherwise make a discarded
decomposition unrecoverable.
The safety-by-construction property the planning-resume checkpoint design rests
on — that `_schedule()` (`:17334`) re-sorts every wave by subtask id
(`wave = sorted(...)`, `:17374`), making the wave partition a pure function of the
dependency graph plus lexicographic ids, independent of dict/set iteration order
and of input plan/subtask order — is pinned directly, with no state/stubs/async,
in `tests/test_schedule_determinism.py`: a multi-domain fixture with both
intra-domain `depends_on` and cross-domain `requires`/`provides` edges (so the
tag channel resolved through `_build_predecessor_graph` is exercised, not just
`depends_on`) produces identical `waves` and subtask-id sets across a fresh call,
a JSON round-trip (simulating a checkpoint reload), reversed plan order, and
reversed per-plan subtask order. A companion test asserts every wave is
lexicographically sorted directly — the round-trip equality alone does not kill
a `sorted(...)` removal (within one process, unsorted set iteration is still
self-consistent across calls, so `waves_fresh == waves_rt` etc. can hold even
without the sort), so the direct per-wave sortedness check is the test that
actually fails when `sorted(...)` is removed at `:17374`.
The resumable-planning checkpoint keys (`plans_after_classify`,
`plans_after_plan`, `plans_after_reconcile`, `plans_after_overlap_judge`,
`plans_after_adherence_gate`, `plans_after_filters`, `satisfied_probe_cache`
— DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a
`waves` gate") are pinned by name in `tests/test_resumable_planning_keys.py`,
on top of the generic bidirectional parity `tests/test_state_fields.py`
already enforces for every `STATE_FIELDS` entry: each key is present in
`leerie.STATE_FIELDS` (mirroring `test_plan_snapshot_wiring.py`'s
`assert "plan_snapshot" in leerie.STATE_FIELDS` guard-the-guard pattern) and
has a row in the IMPLEMENTATION.md §8 `state.json` field table, plus a
regression pin that the field table no longer carries the old "A run that
died on the preflight is not resumable" claim now that `plan_snapshot`
makes a budget-check-stopped run resumable. bugfix-002 registered the keys
and documented them only; resume-rehydration code is separate work
(bugfix-004). `tests/test_planning_checkpoint_keys.py` adds the one check
neither `test_state_fields.py` nor `test_resumable_planning_keys.py`
covers: a real `State.save()` / on-disk JSON reload round-trip with all
seven checkpoint keys populated at once (mirroring
`test_plan_snapshot_wiring.py::TestSnapshotRoundTrips`), plus a
`State.load()` round-trip proving the reloaded in-memory `.data` dict —
not just the on-disk artifact — reproduces every key byte-equal, since
that in-memory dict is what the real `resume` path reads. Deliberately
its own file rather than folded into either of the above two, per its
narrow scope: pure state-surface assertions, no phase control flow, no
stubbed workers, no async.
**Contributor discipline for adding a new checkpoint/state key:**
`STATE_FIELDS` (`orchestrator/leerie.py:259`) is a static allowlist
checked by `tests/test_state_fields.py`, not a runtime filter —
`State.load()` reads the whole on-disk `state.json` unconditionally, so
an undeclared key is not silently dropped on `resume`. What actually
happens is louder: `test_state_fields.py::test_every_st_data_write_is_declared`
fails the moment a new `st.data["x"] = ...` write lands without a
matching `STATE_FIELDS` entry — though note that guarantee held for the
**subscript form only** until 2026-08-10. `_runtime_field_writes` matched the
run-init dict literal with `re.search(r"st\.data\s*=\s*\{(.*?)\}", ...)`, and
two bugs compounded: the pattern has no word boundary so `bst.data = {}` (the
`_BackstopState` stub) matched, and `re.search` returns that **first** match,
whose non-greedy body captured **zero characters**. Measured before the fix: an
undeclared key injected into the run-init literal was not detected, while the
same key in a subscript write was; the matcher saw 67 keys where a correct one
sees 70, blind to exactly the three literal-only keys (`task`, `started_at`,
`worker_count`). It is now an AST walk, which also kills the `bst` false match
by construction — the general lesson being that a text match on `st\.data`
cannot distinguish a real `State` from a stub whose attribute merely ends the
same way. `test_state_fields_matches_spec_table`
fails if the IMPLEMENTATION.md §8 field table and `STATE_FIELDS` drift
out of sync in either direction. The resumable-planning checkpoint keys
above additionally get their own named guard-the-guard pins in
`tests/test_resumable_planning_keys.py` rather than relying solely on
the generic parity sweep, precisely so a future refactor that drops one
of these seven keys specifically (versus any arbitrary state key) fails
with a message naming the checkpoint feature, not just a generic diff.
The practical rule: any new `st.data[...]` write — checkpoint or
otherwise — must land in the same commit as its `STATE_FIELDS` entry and
its IMPLEMENTATION.md §8 table row, or CI catches it immediately; there
is no scenario where it merely resumes with stale/missing data.
The checkpoint-writing half — `_run_phases`'s fresh-run branch persisting
each `plans_after_*` key immediately after its producing phase returns —
is pinned in `tests/test_plans_after_checkpoints.py` via the same
`inspect.getsource(leerie._run_phases)` source-coupling approach as
`tests/test_plan_snapshot_wiring.py` (driving `_run_phases` end-to-end is
infeasible: it spawns real workers and shells out to git/preflight).
Pinned: all six `plans_after_*` keys appear as `st.data[...]` assignments;
each assignment is followed by `st.save()` within 200 chars (an
in-memory-only write is lost on pause/crash); each key's assignment sits
strictly *after* its phase's call in source order — never at entry, which
is the same "`current_phase` is stamped at entry, not completion" trap
`plan_snapshot`'s own wiring test guards against; `plans_after_reconcile`
precedes the `_detect_no_work` short-circuit and `plans_after_filters`
precedes both the `satisfied_no_work` short-circuit and `_schedule()`, so a
run that turns out to have work is never left without its checkpoint; the
six keys' first-occurrence source order matches pipeline order (guards
against a correctly-individually-ordered but scrambled insertion producing
a resume cursor that silently skips a phase); and `plans_after_filters`
precedes `plan_snapshot`, keeping the existing post-schedule checkpoint
authoritative and undisturbed.
`tests/test_planning_checkpoint_ordering.py` is a second, independent pin
of the same write-ordering invariant (call-precedes-checkpoint,
checkpoint-precedes-`st.save()`, source order matches pipeline order),
plus the resume-cursor's gating on checkpoint-key presence
(`"plans_after_<phase>" not in st.data`) rather than `current_phase`, and
the earliest re-entry gate keying on `waves`/`categories` presence.
Deliberately overlapping with `test_plans_after_checkpoints.py` rather
than folded into it: this file is the standalone regression guard for the
single highest-severity implementation trap in this feature (a checkpoint
written at phase entry, before the phase spends, would mark an incomplete
phase done and resume with a half-built plan), kept intentionally small
and separate so it can't be diluted by unrelated changes to the larger
checkpoint-writing test file.
The re-entry (`resume`-consuming) half of the same mechanism — that a
`state.json` checkpointed through phase K reloads and re-enters at phase
K+1 without re-invoking any completed phase's worker — is pinned
behaviorally in `tests/test_resume_planning_reentry.py`, distinct from
`test_plans_after_checkpoints.py`'s source-coupling pin of the write side.
It drives the real `_run_phases` end-to-end with every phase function
stubbed via call-counting monkeypatches (mirroring
`test_phase_adherence_gate.py`'s stub discipline), a stubbed `phase_execute`
that raises a sentinel exception so the test can inspect state without
touching the unrelated execute/finalize phases, and asserts, per
`plans_after_*` checkpoint present in the seeded `state.json`, that every
phase up to and including the checkpointed one is absent from the call log
and every phase after it ran exactly once. `TestPerPhaseRoundTrip` covers
all six planning-phase boundaries (classify → plan → reconcile →
overlap_judge → adherence_gate → filters → schedule). Anti-vacuity per the
CLAUDE.md checklist: the completed phases are stubbed with counters that
would fire if called (not merely omitted from the fixture), and the
fixture never pre-seeds a *downstream* phase's output — only the
checkpoint(s) up to the resume point are present, so the "not re-invoked"
assertion is falsifiable by the code, not vacuously true because nothing
downstream could run anyway. Also pinned: `phase_provision`'s
key-presence-not-truthiness resume-skip (an empty `recipe: []` is a valid
completed state, not "resume must redo it"); the reported incident
directly (`current_phase` naming the satisfied-probe sweep with a partial
`satisfied_probe_cache` and no `plans_after_filters` resumes through to
`_write_plan` instead of dying "did not reach the scheduling phase");
post-scheduling resume falling straight through to `phase_execute`
unchanged when `waves` is already present; budget-check resume rehydrating
`plan_snapshot` instead of the old "Plans are not persisted" die; the
`_schedule()`-determinism guarantee end-to-end (a fresh `_schedule()` call
and a checkpoint-then-resume of the same `plans` produce byte-identical
`waves`/`subtasks`); an allowlist guard that every checkpoint key this
consumer reads is present in `STATE_FIELDS`; that the old
`"did not reach the scheduling phase"` die() message string is gone from
the source; and that a state.json with no progress at all (no
`categories`, no `waves` — never reached the first `st.save()` after
`phase_classify` started) is the one case that still `die()`s, since there
is nothing to resume from.
`tests/test_resume_planning_regression.py` is a narrower, deliberately
end-to-end regression lock on top of `test_resume_planning_reentry.py`'s
per-phase stub sweep: rather than stubbing every phase to assert call
counts, it drives `_run_phases` with only the phases upstream of
`_filter_satisfied_subtasks`/`_schedule` stubbed, leaving
`_filter_satisfied_subtasks`, `_schedule`, `check_budget_feasibility`, and
`_write_plan` REAL (against a real temp git repo, for the `base_sha`
scoping `satisfied_probe_cache` needs) — so its four scenarios prove the
fix composes end-to-end, not merely that each phase's skip-flag is
individually wired. (a) reproduces the reported incident shape verbatim:
`current_phase` at the satisfied-probe sweep with a partial
`satisfied_probe_cache` resumes, re-probes only the uncached sids (via
`claude_p` call tracking), and reaches scheduling with no die(); a paired
falsification test replays the retired `"waves" not in st.data` gate
against the same state shape and confirms it would have died with the
exact historical message, proving (a) exercises the fixed path rather
than passing vacuously. (b) reruns a real `check_budget_feasibility` twice
against the same seeded `plan_snapshot` — once under a low
`max_total_workers` (dies, as expected) and once under a raised cap on a
fresh `State` reload (mirroring a real second `resume` invocation) —
and asserts `_write_plan` runs exactly once and no upstream planning phase
re-runs. (c) asserts a `waves`-present resume reaches `phase_execute` with
zero calls to every planning phase, `_filter_satisfied_subtasks`,
`check_budget_feasibility`, `_validate_plan`, and `_write_plan`. (d) covers
both early-return guards, including the case where planning checkpoints
ARE present but `no_work_required` still wins ahead of any rehydration.
A final grep guard (prior art `tests/test_ec2_launcher_dispatch_e2e.py`)
asserts neither retired die() string survives as a live `die(...)` call
in `leerie.py` (a trailing comment referencing the old behavior by name is
permitted).
The `satisfied_probe_cache` checkpoint-writing half (bugfix-005) is tested
in `tests/test_filter_satisfied_subtasks.py`: a cache hit under the
CURRENT `base_sha` is consulted at the top of `probe_one` — before `async
with sem:` — and `claude_p` is never invoked for that subtask (dropped on
a cached `satisfied=True`, kept otherwise); a fresh probe (no cache entry)
persists its verdict to `satisfied_probe_cache` for BOTH satisfied and
not-satisfied outcomes, keyed by sid, carrying
`satisfied`/`evidence`/`checked`/`base_sha`; the `WorkerError` crash-keep
path writes no cache entry at all (a crashed probe must be re-probed on
resume, not treated as decided); a cached verdict whose recorded
`base_sha` differs from the current `HEAD` is invalidated and the
subtask is re-probed (the mid-run-sibling hazard — DESIGN §6); and THE
REPORTED FAILURE PINNED — a partial `satisfied_probe_cache` resumes,
re-probes only the uncached subtasks (asserted by `claude_p` call count),
and reaches scheduling, where before it would have re-run the whole
sweep. All 17 pre-existing tests in the file are unchanged (17 + 5 = 22
passing). `tests/test_satisfied_probe_cache.py` is a dedicated, narrower
pin for the same `probe_one` cache mechanism in isolation (no resume
control-flow, no state-surface parity — that stays
`test_filter_satisfied_subtasks.py`'s job): a cached `satisfied` verdict
drops the subtask with ZERO `claude_p` calls for that sid, a cached
not-satisfied verdict keeps it with zero calls, an uncached sid is
probed exactly once with the verdict persisted for both outcomes
(asserted per-sid, never in aggregate), and a `WorkerError` crash keeps
the subtask while asserting the cache KEY is ABSENT rather than merely
that the subtask survived — the anti-vacuity discipline from the
zombie-reaper harness lesson. The same file also pins the fix for a real
mid-sweep data-loss defect (2026-07-29 root-cause batch, PR #120): `probe_one`
wrote each verdict to `cache[sid]` in memory with no per-verdict `st.save()` —
only the post-`gather` aggregate save persisted — contradicting both commit
750ce33's message and DESIGN §6, which both claim per-verdict persistence.
A pause mid-sweep silently lost every already-decided verdict.
`test_verdict_reaches_disk_before_the_sweep_completes` pins the fix (an
`st.save()` immediately after the `cache[sid] = {...}` write); the falsifier
is verified live — reverting the added save fails the test.
The sibling-service half of that same incident batch — a satisfied-probe drop
blind to a surviving sibling's pending work invalidating the criterion it
just judged met — is pinned by two new tests in
`tests/test_filter_satisfied_subtasks.py`:
`test_probe_payload_carries_surviving_siblings_excluding_self` (the
`sibling_surface` built once per sweep and handed to each probe's payload as
`surviving_siblings` contains every other subtask with a non-empty `provides`
or `files_likely_touched`, and never the probed subtask itself) and
`test_sibling_invalidation_verdict_keeps_the_dropped_test` (a
sibling-service-shape regression: a test subtask the base tree already
satisfies is NOT dropped when the probe, given `surviving_siblings` context,
judges a sibling's still-pending work would break it). The guidance lives in
`prompts/satisfied_probe.md`'s "A sibling's pending work can invalidate an
already-met criterion" section, scoped explicitly to the pre-schedule call
site (`surviving_siblings` is absent from the post-execution
`_probe_criteria_satisfied_on_head` payload, since HEAD there already reflects
whatever siblings committed — there is no future left to anticipate).
P10 (evidence-citation requirement — `prompts/satisfied_probe.md`'s amended
guidance that success criteria naming test file paths are judged by
coverage/convention rather than a literal colocated-path match, and that the
probe cite the specific file+assertion as evidence) is pinned at the one
mechanically-checkable layer in the same file:
`test_schema_requires_evidence_on_satisfied_true_verdict` asserts
`SCHEMAS["satisfied_probe"]` rejects a `satisfied=True` verdict missing
`evidence` or with a non-string `evidence`, and accepts a well-formed
file+assertion citation; `test_satisfied_probe_prompt_exists_and_nonempty` is
a structural-only check that the prompt file exists and is non-empty. Prompt
prose itself is not asserted — only a live LLM run can verify the probe
actually follows the amended citation instruction (CLAUDE.md's own central
principle: prompts are advisory, code enforces).
