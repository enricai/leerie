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
