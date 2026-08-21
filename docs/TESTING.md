# Testing notes

Detailed per-feature test coverage inventory, incident post-mortems, and
harness pitfalls relocated out of `CLAUDE.md` (which now keeps only the
operational essentials — see its `## Testing` section). See "Commit
messages are the permanent record" in `CLAUDE.md` for why this kind of
historical detail belongs in a durable record rather than perpetually
accreting in the file every session loads.


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
