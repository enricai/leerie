# Testing — per-feature coverage inventory

This file holds the detailed, per-feature/per-incident test coverage
narrative that used to live inline in CLAUDE.md's `## Testing` section.
CLAUDE.md keeps only the load-bearing operational rules a session needs
before running the suite; this file is the changelog-style record of what
each test file covers and why, preserved per the same rationale CLAUDE.md's
own "Commit messages are the permanent record" section gives for keeping
historical detail out of a living reference document.

(Region 2101-2800 of the original CLAUDE.md `## Testing` section.)

enforcer; the warn only reduces how often a plan reaches it broken. A
companion `TEST_OWNERSHIP_RISK` advisory in `check_classifier_output`
(pinned in `tests/test_check_functions.py`) flags when `testing` is selected
alongside `bug-fixing`/`feature-implementation`/`refactoring` in the same
category set — a real prior incident where a single category set produced
both the code change and its own test assertions with no ownership split.
`tests/test_phase_wiring_gate.py::test_die_message_does_not_recommend_skip_overlap_judge`
pins the corrected `phase_wiring_gate` die() message: it no longer recommends
`--skip-overlap-judge` as a bypass (that flag skips the earlier, distinct
phase 2¾ overlap judge and does not touch this gate — the old wording sent an
operator on a `--skip-overlap-judge` retry straight back into the same die()).
The same file pins that each `wiring_defects` entry's `severity` is **asked for
but not `required`** (changed 2026-08-03). Requiring it defeated its own
purpose: a judge that omitted the field produced no schema-valid payload at
all, so the gate never ran and caught **nothing** — measured across the run
corpus, every `wiring_judge` invocation that never produced valid output (9 of
66) failed on this single field, accounting for all 18 of its failing
submissions; relaxing it took `wiring_judge` to 100% and the global
never-valid count from 13 to 4. Both consumers already tolerate absence
(`d.get("severity")` compared against `"latent_risk"` in
`_live_wiring_defects` and in `phase_wiring_gate`'s latent-risk loop), so an
unlabelled entry **gates** — the conservative direction, matching DESIGN §8
*Findings carry a severity* ("the default is gating"). Pinned with
anti-vacuity coverage that a declared `latent_risk` is still excluded from
gating, so the relaxation cannot have disabled the severity channel itself.
The `artifact_registry` worker (DESIGN §5 *Artifact-registry worker*) — a
pre-planning worker that reads the task plus the global repo-map
(ranked to fit the token budget only, no task-file seeding) and emits a small
canonical `{description, tag, path}` vocabulary injected into every planner's
context, softening (not replacing) the reconciler's tag-drift resolution — is
tested in `tests/test_artifact_registry.py` (23 tests): schema validity
(`SCHEMAS["artifact_registry"]`, required `artifacts` array of
`{description, tag, path}`), worker registration parity
(`artifact_registry` in `WORKER_TYPES`, absent from
`MODEL_DEFAULT_PER_WORKER` so it resolves to sonnet,
`EFFORT_DEFAULT_PER_WORKER["artifact_registry"] == "medium"`), model/effort
resolution precedence, phase behavior (`test_phase_returns_artifacts`,
`test_phase_drops_malformed_items` — items missing `tag`/`path` are dropped
rather than propagated, `test_phase_degrades_to_empty_on_crash` — a
`WorkerError` on every `_run_checked_loop` round degrades to `[]` rather than
dying, since the registry is advisory), `--skip-repo-map` degrade (the worker
still runs on the task alone and can still return a non-empty list — only the
`ctx_dict["repo_map"]` build is skipped) plus the repo-map grounding branch
itself — the `skip_repo_map=False` path every other phase-behavior test above
leaves unexercised (`_make_state` always seeds `skip_repo_map=True`):
`_build_repo_map`/`_rank_repo_map` are called and a non-empty ranked map
reaches the worker's prompt when not skipped, `_build_repo_map` is never
called when skipped, an empty ranked map omits the `repo_map` ctx key
(mirroring `phase_plan`'s own degrade), and a crashing `_build_repo_map`
degrades silently rather than propagating — ctx-injection wiring
(`test_phase_plan_injects_registry_into_ctx` — every planner's context gets
`ctx_dict["artifact_registry"]` when the registry is non-empty), checkpoint
ordering (`test_run_phases_checkpoints_registry_before_plan` — the
`if "artifact_registry" not in st.data:` checkpoint runs between
`gather_answers` and the `plans_after_plan` block, the same key-presence
resume pattern every other `plans_after_*`/`artifact_registry` checkpoint
uses), and a `State.save()`/reload round-trip of the state key.
`tests/test_satisfied_probe_cache_invalidation.py` is the real-moving-repo
counterpart to the `base_sha` invalidation case above: rather than a
synthetic `"deadbeef-not-current"` sha
(`test_filter_satisfied_subtasks.py`'s `test_stale_sha_invalidates_cache_and_reprobes`),
it builds a real temp git repo (`git init` + commit) and actually advances
HEAD from sha A to sha B via a second commit, mirroring a sibling run
merging (or reverting) the deliverable between a pause and a resume
(DESIGN §8 "the mid-run sibling case"). Both stale directions are pinned: a
stale `satisfied=True` entry recorded at A must not silently drop a
subtask that is no longer satisfied on the tree at B (silent lost work),
and a stale `satisfied=False` entry must not silently keep a subtask that
has since become satisfied. A cache entry with a missing or malformed
(`None`, non-string) `base_sha` is treated as a miss and re-probed. The
falsifier is verified live: deleting the `cached.get("base_sha") ==
base_sha` comparison in `probe_one` (`orchestrator/leerie.py:7402`) fails
4 of the file's 5 tests with a stale drop/keep.
The conformer/baseline hardening (DESIGN §9 *No clobbering the implementer's
work* + the base-tree baseline's `measured` field) is tested across three
files. `tests/test_clobbered_owned_files.py` covers the clobber-survival guard:
`_clobbered_owned_files` against real temp git repos (legit conformer edit not
flagged; revert-to-base flagged; deletion flagged; a file outside the
implementer's owned set never flagged; a new file added not flagged; the
load-bearing round-0 snapshot test — a per-round HEAD misses a round-0 clobber
while the pre-loop `impl_head_sha` catches it; empty-ref no-op), `_blob_sha`'s
present/absent contract (the missing-path returns None, guarding the bare
`git rev-parse <ref>:<path>` footgun), `_rollback_conformer_commits` actually
restoring clobbered implementer content and dropping the conformer commit
(`TestRollbackRestoresClobber`), and source-coupling wiring guards that both
`_run_conformance_phase` and `_run_final_conformance` snapshot before the round
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
`measured` is not surfaced RED). The same file also pins the N8 fix — every
BLT axis command `_capture_conformance_baseline` runs is invoked as the exact
argv `["bash", "-c", cmd]`, never a login shell (`-lc`), since a login shell
sources `/etc/profile`/`~/.bash_profile` and discards Docker-ENV-only PATH
additions (e.g. mise's shims dir) — a source pin, an end-to-end argv-capture
pin driving `_capture_conformance_baseline` with `_run_streaming` stubbed, and
a regression control that reproduces the PATH-loss mechanism itself (an
env-only PATH entry resolves under `bash -c` and is lost under `bash -lc`)
against real subprocesses, with no container required.
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
The worker-prompt-over-stdin transport (docs/IMPLEMENTATION.md §3 "User
prompt transport — stdin, not argv" — a single argv element cannot exceed
Linux's `MAX_ARG_STRLEN`, 131,071 bytes, and reconciler/plan_overlap_judge
payloads routinely exceed that on their own, crashing with a raw execve
`OSError: [Errno 7] Argument list too long`) is pinned in
`tests/test_prompt_over_stdin.py`: `build()` emits no positional argument
after `-p` at any payload size, so no argv element it constructs can carry
the prompt (the argv-length property is true by construction, not merely
measured for one size); a positional prompt would silently win over stdin
with no error, so `test_no_positional_prompt_after_dash_p` pins the
element immediately after `-p` is always a flag; the retry path
(`build(retry_note)`) routes the concatenated retry text through
`stdin_data` too, not argv; `_invoke` passes `stdin=PIPE` when
`stdin_data` is given and `stdin=DEVNULL` otherwise (direct-cmd callers
with no prompt to feed, e.g. the preflight smoke test, are unaffected);
and `test_real_subprocess_150kb_stdin_no_deadlock` spawns a real `python3`
child and feeds it a real 150,063-byte payload over a real OS pipe via
`_invoke`'s concurrent `_feed_stdin` task, proving no deadlock between the
feeder and `_read_stream`/`_drain_stderr` for a payload well over both a
single pipe buffer and the single-argv ceiling this fix routes around.
`tests/test_replay_capture.py` and `tests/test_no_result_event_retry.py`
were updated in the same change to assert against `stdin_data` instead of
an argv element, since both stub `_invoke` to inspect what `claude_p`
constructs.
Routing the prompt over stdin then created a **deadline** the argv form
never had, and `tests/test_stdin_feeder_ordering.py` guards it. `claude -p`
waits a hard-coded 3 s for its first stdin byte (`KJr(process.stdin, 3000)`
in the CLI bundle — no env var), then drops its own `data` listener, so a
late write is DISCARDED and the worker exits 1 on `Input must be provided`.
leerie made two SYNCHRONOUS broker round-trips between the spawn and the
first write, each bounded by `_cgroup_request`'s 5 s timeout — an accepted
stall larger than the deadline in front of it, i.e. the failure was
permitted by construction, while the comment at that site called the stall
"negligible". Measured across every run on one host: **218 workers lost,
12.4% of all invocations in the affected runs**, retried up to 4x each with
every attempt charged to `max_total_workers`, spanning v0.9.95–v0.16.0.
`_cgroup_enroll`'s docstring had already recorded the pair in a different
run as "two apparently-unconnected events" — they are one event.
**Both halves of the fix are load-bearing**, which is what the file mostly
exists to pin: a reproduction harness scored all four combinations and only
`create_task` at the spawn AND `to_thread` on both broker calls delivers the
prompt — hoisting alone fails because the blocked loop never schedules the
task, and `to_thread` alone fails because the write lands after the child is
already gone. A future edit keeping one and dropping the other silently
reopens a 12% budget leak, so `test_only_both_halves_deliver_the_prompt_in_time`
drives all four combinations behaviourally rather than trusting the source
order. Two harness traps here, both hit on the first draft and both the
comment-matching class this file documents elsewhere: the region is dense
with comments that necessarily name `_feed_stdin`, `await` and
`_cgroup_enroll` while explaining the ordering, so `_invoke_src` strips
comments via `tokenize` (not a `#` heuristic — a `#` inside a string
literal would corrupt the result); and `async def _feed_stdin():` contains
`_feed_stdin()` as a substring, so a bare `.count()` reports two calls for
correct code and the call-site scan has to exclude the definition.
The appended system prompt (docs/IMPLEMENTATION.md §3 "Appended system
prompt transport — file, with a probe + inline fallback" — the second
large argv element that compounds with the user prompt toward the same
`MAX_ARG_STRLEN` ceiling, worst-case on the overlap judge) is pinned in
`tests/test_append_system_prompt_file.py`: `_append_system_prompt_file_supported()`'s
supported/unsupported classification (by stderr text — `"unknown
option"` means unsupported, since both outcomes exit non-zero and only
the message distinguishes them), fail-closed behavior on a missing
`claude` binary or a probe timeout, once-per-process memoization (a
second call makes no further `claude` invocation), and its own
throwaway probe file being cleaned up; `build()`'s branch on the probe
result (`--append-system-prompt-file <path>` with the temp file holding
`system_prompt` verbatim when supported, the inline
`--append-system-prompt` when not); the temp file being removed once
`claude_p()` returns, on both the success path and an exception path
(a `TerminalAuthFailure` raised from inside the try/finally-wrapped
retry loop — the schema-key drift guard itself runs before the temp
file is created, so it needs no cleanup); and the retry loop reusing
the same temp file across both attempts rather than recreating it, since
`system_prompt` is fixed for the whole `claude_p()` call.
`tests/test_replay_capture.py`'s two system-prompt-plumbing tests
(`test_args_match_capture_fields`, `test_override_system_prompt`) pin the
probe to unsupported via monkeypatch so their argv assertions don't
depend on whether the live `claude` CLI on the test host happens to
support the undocumented file flag.

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
`_run_checked_loop` caller until the retry existed. A worker KILLED at its
wall-clock ceiling is the same class and is retried too — `_invoke` raises
`subprocess.TimeoutExpired`, which is not a `WorkerError` — though bounded to
`_TIMEOUT_RETRY_MAX` attempts rather than the full round budget, since a
timeout has already spent its whole ceiling before it is observed. Any
*other* exception is a leerie bug rather than a flaky worker, so it still
abandons the loop immediately (`test_loop_crash_breaks`, which uses `RuntimeError` precisely to
pin that split). Also pinned: all-rounds-crash still returns `None` so the
callers' `is None` escalation is unchanged, the retry is bounded at exactly
`max_rounds`, and a crash must clear `last_res` so a stale earlier result is
never returned as the crashed round's output.
The integrator-crash salvage path (DESIGN §12 *salvage if there is something
to salvage*) is tested in `tests/test_rescue_integrator_work.py` against real
temp git repos left mid-merge. `_rescue_integrator_work` captures a crashed
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
**`scripts/remote/collect-subtrees.sh` embeds a second copy of
`SCHEMAS["integrator"]`** as a single-quoted shell string, because it invokes
`claude -p --json-schema` directly from bash on the remote machine and cannot
import the orchestrator. **Any edit to that schema must update both.** That same
direct invocation also puts the script outside the `--dangerously-force-strict-output`
path — it runs only after the orchestrator (which owns the proxy) has exited, so
output there is schema-validated but not constrained during generation.
`tests/test_collect_subtrees_integrator_schema.py` is the guard: it parses the
`integrator_schema='{...}'` assignment out of the real script and asserts
whole-object equality with the live `SCHEMAS["integrator"]` — deliberately
whole-object rather than a spot-check of the fields that last drifted, since
the next drift will be somewhere else. It exists because the copy **had already
silently drifted in production** (measured 2026-08-03): it still carried
`maxLength` 2000/500 on the confidence fields, values the live schema had moved
off twice since (to 8000/2000, then deleted outright), so remote integrator
runs were validating worker output against a materially different contract than
local ones — invisibly, because nothing compared the two. A corpus fixture had
even named this test file before it existed; the guard was planned and never
landed, which is precisely how the drift went unnoticed.
`tests/test_resolve_run_id_autopick.py` covers bare `resume` auto-picking
the newest resumable run (`in-progress`/`paused`/`incomplete`), including
the two traps found by running the design against a real 58-run state dir:
`seed-failed` rows carry no `started_at` and sorted to the *top* of a naive
newest-first sort (they are now list-only, never auto-picked), and a
missing `started_at` must never outrank a real timestamp. An explicit
run-id stays exempt from the filter (so `resume <seed-failed-id>` still
works) and an unknown one still fails closed. The `seed-failed` exclusion
is a deliberate behavior change with a UX cost, pinned by
`test_resolve_run_id.py::test_resolve_lone_orphan_is_not_auto_resumed`:
bare `resume` used to auto-pick a *lone* orphan, and now dies instead —
a seed-failed run aborted before `phase_classify` and needs an operator
decision (re-seed vs. kill), since resuming blind can re-trigger the same
seed failure. The die is therefore required to stay actionable (names the
run, its `status=seed-failed`, and the explicit-id escape hatch), because
that escape hatch is the documented recovery path for the 2026-06-04
hangs. `--report`/`--phase` still auto-pick a lone orphan — they are
read-only.
`tests/test_container_entry_run_id.py` covers `container-entry.sh` skipping
its cidfile `--run-id` injection when `resume` is present — a resume
container is a *new* container whose id matches no run on disk, which is
what made bare `resume` die naming an id the user never typed. The
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
`stop`/`kill`/`accept-blocked` with `LEERIE_AWS_PROFILE`/
`LEERIE_AWS_REGION` unset, since each of those arms builds its own
optional-arg array from those two vars directly in `leerie` before
calling `resolve_aws_credentials`. This surfaced a real, previously
unguarded instance of the class: all four call sites
(`accept-blocked`, `stop`, `kill`, and the main `RUNTIME=ec2`
dispatch) expanded their creds-args array as a bare `"${arr[@]}"`
instead of `${arr[@]+"${arr[@]}"}` — fixed in the same change. The
nameref ban was likewise extended to `leerie` itself
(`test_no_namerefs_in_launcher`). A later child added
`pytest.param(["accept-integration", ...])`, covering
`accept-integration`'s own `_ai_aws_creds_args` array expansion the
same way.

**Host-only tests are gated on `jq`** (`HAS_JQ` in `tests/conftest.py`,
mirroring the `HAS_TREESITTER` pattern). Five modules —
`test_host_finalize_sh.py`, `test_decide_teardown_auto_finalize.py`,
`test_launcher_finalize_no_work.py`, `test_launcher_no_push_skips.py`,
`test_push_output_capture.py` — source bash the **host** owns:
`scripts/host-finalize.sh`, `provision.sh`'s `decide_teardown`, and the
launcher's `finalize` / `no_push` paths. All parse `run.json` with real `jq`.
(A per-file test count in this file is a measurement with a date on it, not
a constant; re-derive before citing one.) The harnesses stub
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
reproduce the gated list — two of the five never mention jq and fail only
because the script under test shells out to it; the list is measured from a
real in-container run. The fifth entry shows a second way in: a module-level
`skipif` does **not** propagate through an import, so
`test_push_output_capture.py` reusing `test_host_finalize_sh.py`'s runner
needs its own. `tests/test_jq_gate_wiring.py` is the guard-the-guard
(conftest exposes a module-level `HAS_JQ` bool derived from a live
`shutil.which` probe; each of the five both imports it and carries a
`skipif` referencing it) — dropping one file's skipif fails it, which is the
same silent regression the `HAS_TREESITTER` gate exists to prevent.

**The push's two streams are captured separately, and the obvious fix is the
trap.** `host_finalize` captured the push with `2>&1 >/dev/null` — stderr
only — while git forwards a pre-push hook's stdout to git's own stdout, where
`tsc` and `biome` write their diagnostics (jest and vitest use stderr, which
is why this went unnoticed). Measured: a `push_error` of two pnpm deprecation
warnings for a push whose real cause was 13 lines of `TS2307`, undiagnosable
from leerie's own output, three misdiagnoses, at the end of a $57 run. But
plain `2>&1` is **wrong**, because the captured blob is also the input to
`_host_finalize_is_auth_or_network_push_error`, whose arm matches a qualified
phrase on a `^fatal:`/`^remote:` line — and a hook that refreshes submodules
or runs `git ls-remote` prints exactly that shape on stdout, flipping a hook
failure to "auth/network" and suppressing the `--no-verify` hint. Measured
against the real classifier: **3 of 3** adversarial hook shapes flip, while
real `tsc`/`vitest` output does not. So stderr classifies and stdout+stderr is
displayed, which leaves the committed 23-case corpus score unchanged **by
construction** rather than by re-measurement.
`tests/test_push_output_capture.py` pins both halves; its parametrized
`test_git_framed_hook_stdout_does_not_suppress_the_hook_hint` is the
load-bearing one, paired with an anti-vacuity control that a genuine
credential failure on stderr still classifies as auth (else the guard could
pass by disabling the classifier). Falsified live: routing `push_all` into the
classifier fails 4 tests, and the control keeps passing.

**Three further traps in the same change, each caught by a test rather than by
review.** (1) `push_error` reaches `run.json` as a single `jq --arg` value, so
it is bounded by `MAX_ARG_STRLEN` (131,072 bytes) — and one real recorded
`push_error` is already **104,520 bytes on stderr alone**, so folding hook
stdout into the same value is precisely what makes the ceiling reachable. Past
it `jq` cannot be exec'd and `set -e` aborts `host_finalize` *before* the
diagnostic prints, losing the output the capture exists to preserve. The
persisted copy is therefore tail-bounded at 32 KiB (the printed one at 4000
bytes — a separate and much tighter bound);
`test_oversized_push_output_still_writes_the_sidecar` drives ~200 KB through
it. Same argv-E2BIG class as the 2026-07-19 orchestrator incident.
(2) Husky v9 prints its banner on **stdout** — a repo with
`core.hooksPath=.husky/_` runs `.husky/_/h`, whose line 20 is a bare
`echo "husky - $n script failed (code $c)"` with no `>&2` — so the
supplementary "which hook" naming grep, reading stderr only, could never match
the commonest hook runner in existence, and the existing stderr-stub test in
`test_host_finalize_sh.py` is why that looked covered. It now reads stderr
plus the hook's stdout; classification is untouched.
(3) That grep must NOT read `push_all`, because the section marker leerie
itself inserts (`--- pre-push hook output (stdout) ---`) contains the words
"pre-push" and "hook" and is matched *first* — measured, the hint read
"(pre-push hook failed)" while husky's own banner further down the same blob
said "pre-push script failed". A separate `push_hook_out` variable holds the
raw stdout so the grep never sees leerie's own prose. This is the same
label-matching-the-thing-it-describes trap the zombie-reaper guard and the
`unreviewed_subtasks` scan document elsewhere in this project's history, in a
third disguise: a *label* read as *evidence*. The test asserts the name is
"script", not merely that "pre-push" appears — a laxer assertion passes
against the bug.

**A harness that strips the locale makes a byte-vs-character bug
undetectable, and this is the sharpest vacuity trap in the file.** Both push
bounds are `tail -c`, which cuts BYTES, while `${#var}` counts CHARACTERS —
but only under a multibyte locale. `test_host_finalize_sh.py`'s runner builds
a minimal env (`PATH`/`USER_REPO`/`HOME`, no `LANG`, no `LC_ALL`), so bash
runs in the **C locale, where `${#var}` counts bytes** and a char-based and a
byte-based implementation are *indistinguishable*. The first version of
`test_persist_bound_is_measured_in_bytes_not_characters` therefore passed
against the exact bug it was written for — falsification confirmed it: 35
passed with the fix reverted. It now resolves a working multibyte locale
first (`_multibyte_locale()` probes bash's own `${#}` and requires 2, not 6,
for a two-character Japanese string), passes it through `extra_env`, and
**skips loudly** when none exists rather than silently proving nothing.
Generalise the rule: when a test's subject is a locale-, encoding-, or
timezone-sensitive behaviour, the harness's minimal env is a *variable of the
experiment*, not neutral scaffolding.
