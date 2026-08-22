# Leerie — Implementation Reference

> **This document describes the current code, not the design.** It is true only
> against the present state of `orchestrator/leerie.py`, the worker prompts,
> and the shell scripts. A change to the code that is not reflected here makes
> *this document* wrong — unlike `DESIGN.md`, which describes the architecture
> and stays correct across reimplementation. When this document and the code
> disagree, the code is authoritative. When this document and `DESIGN.md`
> disagree, `DESIGN.md` defines what *should* be true.
>
> Read `DESIGN.md` first for the *why*; this document is the *what* and *where*.

---

## 0. Install surface

Leerie ships two install paths. Both ultimately invoke the on-disk
`leerie` launcher; the difference is who put it there and how the
user reaches it. The launcher itself is a portable bash script —
the host needs neither Python nor `uv`. Everything Python lives
inside the container (DESIGN §6 / §0.5 below).

### Files

| Path | Purpose |
|------|---------|
| `.claude-plugin/marketplace.json` | Single-plugin marketplace manifest. Makes the repo itself discoverable via `/plugin marketplace add enricai/leerie` from inside Claude Code. Points at `.` so Claude Code reads the sibling `.claude-plugin/plugin.json`. |
| `.claude-plugin/plugin.json` | Existing plugin manifest (commands, skills, metadata). The `version` field is the single source of truth for `leerie version`. |
| `scripts/install.sh` | The `curl \| bash` shell installer. Preflight (git/curl checked; `claude` auto-installed via the official native installer if missing, opt-out `--no-claude-install`) → runtime install (colima on macOS; rootless containerd stack on Debian/Ubuntu — Fedora/Arch fall back to a docs hint) → clone → symlink → verify. Self-contained bash; deps: `bash`, `curl`, `git`. |
| `leerie` (launcher) | Portable bash. Symlink-walks to its own location, runs the per-OS runtime preflight, builds the leerie image once per version, and execs `nerdctl run` with TTY flags adapted via `[ -t 0 ]` (see §0.5). Passes `--cgroupns=host` so the container shares the host VM's cgroup namespace — required for cgroup v2 process enrollment (nerdctl's default `--cgroupns=private` + `nsdelegate` blocks non-root `cgroup.procs` writes; see DESIGN §6 *Memory containment*). Fast paths for `version` and `config` skip container startup. Per-run auth/config is staged into a fresh `mktemp -d "$HOME/.cache/leerie/cfg-XXXXXX"` (`$STAGE`); a `rm -rf "$STAGE"` EXIT trap is registered **immediately after the mktemp** (before the ~250-line stage-assembly block, which contains an `exit 1`) so an early exit can't leak the dir, and a best-effort startup sweep (`find "$HOME/.cache/leerie" -maxdepth 1 -type d -name 'cfg-*' -mtime +1 -exec rm -rf`) reclaims dirs leaked by trap-bypassing exits (SIGKILL / OOM / `nerdctl kill`). Because `-mtime` tests the cfg dir's own top-level mtime — which freezes at staging-completion (the running container's writes into `$STAGE/.claude/*` do NOT bump it) — a background keepalive (`while :; do touch "$STAGE"; sleep 3600; done &`, killed by the same EXIT trap) freshens the live dir hourly so a genuinely long-running run (e.g. one auto-resuming across rate-limit backoffs) is never mistaken for stale and deleted by a concurrent launch's sweep. |
| `Dockerfile` | Image recipe (Debian 13 + Node + pnpm + claude CLI + baked orchestrator source). Built locally on first run, tagged `leerie:<VERSION>`. |
| `scripts/container-entry.sh` | Container PID 1. Runs as **root** (rootful runtimes) or the **rootlesskit-mapped host UID** (rootless containerd — DESIGN §6 *Rootless exception*); the Dockerfile omits `USER leerie` so cgroup containment can be set up before privilege drop. Resolves `CGROUP_ROOT` (`/sys/fs/cgroup`, or the systemd-delegated user slice under rootless), creates `$CGROUP_ROOT/leerie.slice`, enables the memory+pids controllers, then launches the **cgroup broker** (`cgroup-broker.py`) at the same pre-drop identity — worker cgroup enrollment can't be done post-drop (see `scripts/cgroup-broker.py`, DESIGN §6). Also at pre-drop identity: `ulimit -c 0`, `cd /work`, and chowning `/work`, `/home/leerie` (+ `.local`/`.cache`/`.gnupg`), and `/tmp/.cache` (`chmod 1777`, since tools create their own subdirs there at runtime) — skipped under rootless, where only outer UID 0 remaps to inner leerie, so leerie-owned dirs must stay root-owned at build time (the rootful path instead needs literal `leerie` ownership via a real UID switch, restored by this chown). Drops privilege via `runuser -u leerie -- env HOME=/home/leerie USER=leerie LOGNAME=leerie ...` (explicit `env`, not `--login`, which would `cd` away from `/work`): execs `python3 .../leerie.py "$@"` when given argv (local/nerdctl path), or wraps `sleep infinity` when invoked with none (remote/Fly path, where the orchestrator is dropped in separately via `Popen(user="leerie")`). |
| `scripts/cgroup-broker.py` | Cgroup broker (DESIGN §6 *Memory containment*), launched by `container-entry.sh` before the privilege drop; the dropped-privilege orchestrator drives it over a Unix socket at `/run/leerie-cgroup.sock`. Handles `ping` / `probe` / `create <sid> <mem> <pids>` / `enroll <sid> <pid>` / `destroy <sid>` / `stat <sid>` / `slice`. The orchestrator calls `descendant_tracker.stop_and_reap()` **before** `_cgroup_destroy` (else a still-populated cgroup fails `rmdir` with EBUSY). `destroy` writes `cgroup.kill`, polls `cgroup.procs` until it drains (bounded by `_DESTROY_DRAIN_TIMEOUT_SEC` on v2, shorter on v1), then `rmdir`s, retrying on EBUSY and returning the error once the budget is exhausted. The client-side `_CGROUP_DESTROY_TIMEOUT_SEC` (15.0s) must stay ≥ the broker's drain budget, or an abandoned client can leave a `leerie-w-<sid>` dir orphaned on the host cgroupfs (workers run with `--cgroupns=host`). `_cgroup_destroy` runs via `asyncio.to_thread` so one worker's teardown can't stall others. Leaked dirs (pre-fix images, a SIGKILLed orchestrator) are swept at broker startup, restricted to dirs both empty and older than `_ORPHAN_MIN_AGE_SEC` (1h) so a live or concurrent run isn't swept. `stat <sid>` → `OK <pids.current> <pids.max> <pids.events.max> <memory.events.oom_kill>` for PID-exhaustion/OOM detection. `slice` → `OK <leerie.slice memory.max> <live sibling worker cgroups> <unreclaimable bytes>`, used by `_cgroup_slice_info` to gate admission on measured headroom; the third field is deliberately **unreclaimable** usage, not `memory.current` (which counts reclaimable page cache); `-1` on either numeric field means unreadable and the caller fails open. The "live" count is diagnostic only, not an input to per-worker sizing (load-independent — see `worker_memory_max_bytes`). The `memory.events` `oom_kill` counter is the definitive OOM signal, mirroring `pids.events`' `max` for fork denial. The broker exists because enrollment/limit-setting needs cgroup ownership the dropped-privilege orchestrator lacks. Detects cgroup **v2** (unified `<V2_ROOT>/leerie.slice/leerie-w-<sid>/{pids,memory}.max`, `V2_ROOT` overridden via `LEERIE_CGROUP_V2_ROOT` under rootless) vs **v1/hybrid** (split `pids/`+`memory/` at `V1_ROOT`, seen on Fly Firecracker VMs). Validates every `<sid>` against `^[A-Za-z0-9._-]+$` and requires integer limits. The orchestrator composes a **run-scoped** sid (`<run-id[:12]>-<sid>`, `_cgroup_worker_sid`) so concurrent runs never collide; `phase_plan`'s `replan_round` similarly round-scopes re-plan sids (`planner-<category>-r<N>`). `_cgroup_enroll` returns the broker's failure reason (`str | None`) so `_invoke` can name it as a probable crash cause. |
| `scripts/remote/build-push.sh` | Build and push a self-contained leerie image to Fly.io's registry. The baked source at `/opt/leerie-image/` lets the image run on Fly Machines with no bind mount. Default mode is Fly's remote builder (no host Docker daemon needed); local-build (nerdctl/docker on the host) is opt-in via `--local-build` or `LEERIE_LOCAL_BUILD=1`. The remote builder strips the `[build] image = ...` line from a tmp fly.toml to avoid flyctl#1686 (flyctl otherwise fetches the pre-pinned image instead of rebuilding). |
| `scripts/remote/provision.sh` | Fly.io machine lifecycle helper (sourced by the launcher's `RUNTIME=fly` branch). Exports `provision_machine()` (create → wait-started → register `decide_teardown` trap), `stop_machine()`, `destroy_machine()`, `destroy_volume()`, `_try_fetch_branch_for_teardown()`, `decide_teardown()`. `destroy_volume()` reaps `$LEERIE_VOLUME_ID` independently of any machine id, since Fly volumes outlive their machines and there's no platform teardown hook; `destroy_machine` calls it last (Fly refuses to destroy an attached volume) and treats a failed volume destroy as a logged, non-fatal billing issue. The EXIT/INT/TERM trap's `decide_teardown` classifies `$LEERIE_REMOTE_EXIT_RC` into three dispositions: **sync-then-finalize-then-destroy** (terminal exits: 0, `EXIT_NEEDS_ANSWERS=10`, genuine `EX_TEMPFAIL=75`) — fetches the branch, pushes + opens the PR via `host_finalize`, and only destroys the machine if the push succeeds (else leaves it running with a recovery banner pointing at `leerie finalize <run-id> --runtime fly`); **detach** (host-side SIGINT/SIGTERM: orchestrator keeps running, machine left alone, reattach hints printed); or **pause-on-failure** (other non-zero rc: best-effort state sync via a 60s-bounded tar-pipe, then stop the machine and write `paused_at`/`pause_reason` to the sidecar). `die()` exits (rc=1) route to pause, not clean-exit, so partial failures are paused rather than destroyed. |
| `scripts/remote/lib.sh` | Shared bash helpers sourced by `provision.sh`, `resume-machine.sh`, `re-seed.sh`, `fetch-branch.sh`, `seed-repo.sh`. Exports `_extract_flyctl_remote_rc()` (parses the real remote exit code out of `flyctl ssh console`'s stderr, since flyctl itself returns 1 for any non-zero remote exit), `update_run_json()` (atomic merge into the host's `run.json`), `wait_for_started()` (poll `flyctl machine status` until `started`, with timeout), `require_flyctl()` (detect/install `flyctl`, check `flyctl auth status`), `render_tail_wrapper()` (a POSIX-sh wrapper that tails `orchestrator.log`, cross-checks liveness via pid-file `kill -0` OR a `/proc` cmdline scan — closing the stale-pid contagion of DESIGN §6 *Single owner per run dir* — and on exit propagates `orchestrator.exit_code` as its own exit code so `decide_teardown` can route failures to pause; falls back to exit 0 if that file is absent), and `tail_with_optional_autofinalize()` (wraps the tail wrapper + `flyctl ssh console` with optional `AUTO_FINALIZE_TOKEN` plumbing: on clean exit, greps captured stderr for the token and `exec`s `leerie finalize <id>` on the host). Replaces four duplicated detection blocks across the remote scripts. |
| `scripts/remote/seed-common.sh` | Transport-agnostic seeding helpers shared by the Fly path (`lib.sh`→`seed-repo.sh`) and EC2 path (`ec2-lib.sh`→`ec2-seed-repo.sh`): `_seed_timeout_prefix()`, `_seed_use_shallow()`, `_seed_branch_shallow_safe()`, `_seed_dirty_filter()`, `_seed_auth_tar_excludes()` (single owner, replacing prior per-transport duplicates). `_seed_auth_tar_excludes()` echoes the `tar --exclude=...` flags guarding git/ssh/gnupg auth material (`.gitconfig`, `.git-credentials`, `.netrc`, `.ssh`, `.gnupg`, `.config`, etc. — that material lives on the host per DESIGN §6 *Finalization*) from the `$STAGE` tar shipped to the remote machine. `_seed_dirty_filter()` shells out to `seed_dirty_filter.py`. Bash 3.2 portable. |
| `scripts/remote/seed_dirty_filter.py` | Single-owner dirty-file transfer filter invoked by `_seed_dirty_filter()`. Reads newline-delimited candidate paths on stdin, writes surviving paths NUL-delimited to stdout (the shape `rsync --files-from=-` needs); `USER_REPO` anchors the vanished-entry check. Host-side only. Tested directly in `tests/test_seed_dirty_filter.py`. |
| `scripts/remote/resume-machine.sh` | Resume helper for paused Fly runs (run-id IS the machine ID). `resume_machine()` compares the sidecar's stored `image_tag` against the current `$FLY_IMAGE_TAG`; on mismatch, updates the stopped machine's image before starting it (fail-open on update failure; `/work` volume survives; `seed_auth` re-provisions the ephemeral rootfs on every resume). Starts the machine, waits for `started`, clears `paused_at`/`pause_reason`. A missing `image_tag` (pre-field runs) always triggers the update. |
| `scripts/remote/re-seed.sh` | Mid-run re-rsync helper (Phase 4). `re_seed()` wakes the machine if stopped, refuses to re-seed over uncommitted machine-side changes (unless `LEERIE_RE_SEED_FORCE=1`), then calls `seed_repo_dirty`. Used by `leerie re-seed <run-id>` and the auto-re-seed step of `leerie resume --runtime fly`. |
| `scripts/remote/seed-auth.sh` | Seeds Claude config + git identity into the Fly Machine. Tar-pipes the host's `$STAGE` (OAuth credentials, `~/.claude.json`, `.claude/` minus caches, `~/.aws/` under Bedrock; git/ssh/gnupg auth excluded, per DESIGN §6 *Finalization*) to `/home/leerie/` via `flyctl ssh console`, wrapped with `$(_seed_timeout_prefix)` (default 600s) so a stalled ssh-console session produces a clean rc 124/137 instead of hanging — which triggers a one-shot `flyctl agent restart` retry, then the PAUSED-on-failure path (DESIGN §6). A `_seed_progress_bg` heartbeat logs progress every `LEERIE_PROGRESS_INTERVAL_S` (default 10s). Writes git identity to `/home/leerie/.gitconfig` explicitly (not `--global`, which would land under root). Pre-warms `claude --version` since a cold Fly machine's first invocation takes ~17s (Node + statsig cold start). |
| `scripts/remote/seed-repo.sh` | Two-phase bundle + delta repo seeding, run after `provision_machine()`. `seed_repo_clone` wipes `/work`, creates a `git bundle` per parent+submodule, pipes each to the machine, clones/wires submodules (`protocol.file.allow=always` for git 2.38+'s file:// restriction, CVE-2022-39253), chowns to leerie. `seed_repo_dirty` rsyncs the dirty/untracked delta plus `.claude/`. Bundles avoid macOS BSD tar's NFC→NFD filename corruption on non-ASCII submodule paths; no in-machine `git clone` from origin (no GitHub creds shipped). Timeout/heartbeat/pause-on-stall handling mirrors `seed-auth.sh`. **Shallow-seed path** (DESIGN §6 *Shallow seeding for heavy repos*): when `.git` exceeds `LEERIE_SEED_SHALLOW_THRESHOLD_MB` (default 200) and depth is non-zero, ships a `git clone --depth=N` `.git`-only tar instead of a full bundle (git bundles can't carry grafted/shallow history), then checks out the branch machine-side and removes the stale origin. Requires a shell-safe branch name (`_seed_branch_shallow_safe`); unsafe names or detached HEAD fall back to the full-bundle path. `LEERIE_SEED_DEPTH=0` also forces full-bundle. |
| `scripts/remote/fetch-branch.sh` | Post-run stream-back helper, run before `destroy_machine` on clean exit and by `leerie finalize`. `fetch_branch()`: discovers the completed run-id from the machine's `run.json`; if the run branch exists, bundles it plus all `leerie/subtasks/<run-id>/*` branches (recovering raw subtask work even if integration never ran); tars back `.leerie/runs/<run-id>/`; when a branch was fetched, strips any stray mechanism `no_push=true`, else preserves `_finish_no_work_run`'s intended `no_push=true` so `host_finalize` short-circuits instead of pushing a non-existent ref; best-effort streams back `config.toml`/`Dockerfile` without clobbering existing host copies. Destination root is `$LEERIE_STATE_HOST_DIR` or `$USER_REPO/.leerie`. |
| `scripts/remote/aws-credentials.sh` | Standalone AWS credential/profile/region resolver for the EC2 runtime. `resolve_aws_credentials [--profile NAME] [--region NAME]` follows the AWS CLI/SDK precedence (explicit env vars → named profile via static credentials or cached SSO token → actionable `aws sso login`/`aws configure` hint; no IMDS fallback, since this runs host-side). Region: `AWS_REGION` > `AWS_DEFAULT_REGION` > profile `region` > die-with-hint. Prints `export KEY=value` lines on success for sourcing; pure file I/O + bash/python3 stdlib, no `aws` binary or boto3 needed. The launcher's `RUNTIME=ec2` branch sources this, evals its exports before `require_aws`, and then dispatches to `ec2-provision.sh`, `ec2-seed-auth.sh`, `ec2-seed-repo.sh`, and `ec2-ssm.sh` for the full create → seed → launch → tail/attach → teardown lifecycle. |
| `scripts/remote/ec2-lib.sh` | Shared bash helpers for the EC2 lifecycle, parallel to `scripts/remote/lib.sh`'s role for the Fly path. Exports `require_aws()`: the host-side preflight the launcher's `RUNTIME=ec2` branch calls before provisioning, modeled directly on `require_flyctl()`'s two-stage shape (binary-present? → authenticated?). Checks `command -v aws`; if missing, prints an actionable AWS CLI v2 install hint and returns 1 (no auto-install — unlike `require_flyctl`, the AWS CLI's official installers commonly need `sudo`, which is out of scope for an unattended preflight). If present, resolves a profile (`--profile`-equivalent precedence: `LEERIE_AWS_PROFILE` > `AWS_PROFILE` > unset, where `LEERIE_AWS_PROFILE` is resolved by the launcher's own CLI > env > `leerie.toml` ladder — see "AWS region/profile prefs" below) and probes `aws sts get-caller-identity` (with `--profile` when resolved); on failure prints the `aws sso login --profile <profile>` (or bare `aws sso login`) recovery hint and returns 1 — reusing `bedrock_preflight()`'s exact credential-error vocabulary (`leerie:4903-4907`) rather than inventing a second one. Also exports `resolve_ami()` / `resolve_instance_type()` / `resolve_key_name()` / `resolve_security_group()` / `resolve_subnet_id()`, one per `LEERIE_EC2_*` var (see "EC2 instance-lifecycle vars" below): each a thin required-var read (`_resolve_ec2_var`) that prints the value on success, or an actionable error naming the missing var on stderr and returns 1 (not a bare `${VAR:?}`, which would kill the whole sourcing shell with bash's generic "parameter null or not set" message under `set -u`). These stay in `ec2-lib.sh` (shared) rather than `ec2-provision.sh` (lifecycle-specific) because `ec2-ssm.sh`'s transport helpers also need `resolve_key_name`/`resolve_security_group` for the SSH-fallback path (DESIGN §6 "SSH ... remains available as a fallback transport"). |
| `scripts/remote/ec2-seed-repo.sh` | EC2 counterpart to `scripts/remote/seed-repo.sh` (DESIGN §6 *EC2 runtime lifecycle*, "Seed" row: "same two steps, transport substituted"). The payload logic — `.gitignore`-aware content via the bundle (committed tracked files) plus the porcelain-filtered dirty-delta rsync, unconditional `.leerie/` exclusion except the three whitelisted config files, the shallow-vs-full-bundle decision, submodule bundling — is IDENTICAL to `seed-repo.sh`; only the wire transport differs: `ec2_tar_pipe` (plain `ssh`, from `ec2-lib.sh`) for bulk data (the parent bundle/shallow `.git` tar and each submodule bundle) instead of `flyctl ssh console -C "sh -c 'cat > ...'"`, and `ec2_remote_exec` (SSM Session Manager, the default transport) for small instance-side commands (the `/work` reset, the machine-side clone/checkout script, `chown`) instead of the same `flyctl ssh console -C` calls. Since `ec2_tar_pipe`'s receiver is `tar -xzC <dir>` (not a bare `cat > file`), each bundle/tar payload is wrapped in a one-entry gzipped tar by the private helper `_ec2_pipe_file_via_tar` before going over the wire. Exports `ec2_seed_repo_clone` (same wipe-`/work`-preserve-inode step; full `git bundle create - --all` for the parent, or — above `LEERIE_SEED_SHALLOW_THRESHOLD_MB` with a non-zero `LEERIE_SEED_DEPTH` and a shell-safe branch name, gated by the `_seed_use_shallow`/`_seed_branch_shallow_safe` functions shared with `seed-repo.sh` via the single definition site `scripts/remote/seed-common.sh` — a `git clone --depth=N --no-local` tarred `.git`-only; per-submodule bundles; instance-side `git clone`/untar+`checkout`, submodule URL rewiring, `git -c protocol.file.allow=always submodule update --recursive`, `chown -R leerie: /work`), `ec2_seed_repo_dirty` (the dirty-set computation and `.leerie/`-whitelist/`.claude/`-force-include filter are the same `_seed_dirty_filter()` (`scripts/remote/seed-common.sh` → `seed_dirty_filter.py`) `seed_repo_dirty` calls, so the two transports share a single implementation rather than a byte-identical copy; transport is plain `rsync -e <ssh-wrapper>` directly against the resolved `LEERIE_EC2_SSH_TARGET` — no `flyctl`-console-tunneled `rsync --server` indirection needed, since SSH is a real, directly-usable transport for EC2 per DESIGN §6), and the wrapper `ec2_seed_repo`. New env var `LEERIE_EC2_SSH_TARGET`: the `ssh`(1) destination for the instance (e.g. `ec2-user@<public-ip>` or an `ssh_config` Host alias) that `ec2_tar_pipe`/the dirty-delta rsync consume verbatim — resolving an `LEERIE_EC2_INSTANCE_ID` to a reachable address is `ec2-provision.sh`'s job, populated by `provision_instance()`. Preflight (`_ec2_seed_repo_preflight`) requires `LEERIE_EC2_INSTANCE_ID`, `LEERIE_EC2_SSH_TARGET`, `USER_REPO`, and `require_aws` (from `ec2-lib.sh`). |
| `scripts/remote/ec2-seed-auth.sh` | EC2 counterpart to `scripts/remote/seed-auth.sh` (DESIGN §6 *EC2 runtime lifecycle*, "Seed" row). The payload logic — what gets seeded (`~/.claude.json`, `~/.claude/` minus `plugins/cache`/`plugins/marketplaces`, the `CLAUDE_CODE_OAUTH_TOKEN` credentials-JSON fallback, git identity, the Claude CLI pre-warm, the plugin-cache rebuild) and why — is IDENTICAL to `seed-auth.sh`; only the wire transport differs, following the same split `ec2-seed-repo.sh` already established: `ec2_tar_pipe` (plain `ssh`, from `ec2-lib.sh`) for the bulk `$STAGE` tar, and `ec2_remote_exec` (SSM Session Manager) for every small remote command (the post-tar `chown -R leerie:`, the token-fallback credentials write, git identity, the CLI pre-warm, the plugin-cache rebuild script). Exports `ec2_seed_auth()`: preflight requires `LEERIE_EC2_INSTANCE_ID`, `LEERIE_EC2_SSH_TARGET`, `STAGE`, and `require_aws` (from `ec2-lib.sh`); the tar-pipe step retries once on a non-timeout transport failure (mirroring `seed-auth.sh`'s tunnel-unavailable retry, minus the Fly-specific `flyctl agent restart`) and is wrapped in `$(_seed_timeout_prefix)` via `ec2_tar_pipe` so a stalled SSH session yields rc 124/137 instead of hanging; the unconditional post-tar `chown -R leerie: /home/leerie` (over `ec2_remote_exec`) exists because, unlike `flyctl ssh console` (always root), `ec2_tar_pipe`'s ssh target may land as the AMI default user. |
| `scripts/remote/ec2-provision.sh` | The `provision.sh` counterpart for the EC2 lifecycle (DESIGN §6 *EC2 runtime lifecycle*, "Stage mapping" table). The `leerie` launcher's `RUNTIME=ec2` branch sources `aws-credentials.sh` and `ec2-lib.sh` and gates on `resolve_aws_credentials`/`require_aws()` before anything else, then sources this file and dispatches to `provision_instance()` (fresh launch) or `resume_instance()` (a run-id whose sidecar names a resumable instance) — see "Runtime mode" below for the full dispatch shape. All EC2 API calls in this file go through the **`aws` CLI** (see "boto3 usage boundary" below), mirroring how `provision.sh` shells out to the `flyctl` binary rather than importing a Go SDK; JSON responses are parsed with inline `python3 -c` (not `aws ... --query/--output text`) so the same parsing works uniformly against the real CLI and against test stubs that ignore `--query`. Sources `lib.sh` (for `remote_log`/`update_run_json`/`iso_now`, which are Fly-agnostic pure functions despite `lib.sh`'s file-level Fly-specific docstring) and `ec2-lib.sh` (for `require_aws`/`resolve_*`). Exports, one per DESIGN §6 stage-mapping row: `provision_instance()` (`aws ec2 run-instances` with no explicit block-device mapping — AMI/instance-type/key-name/security-group/subnet from the `resolve_*` helpers in `ec2-lib.sh`; the AMI boots straight into a ready-to-seed instance with no per-run image push/pull/build step, per DESIGN §6 "Image delivery"'s bake-into-AMI default — see "EC2 image delivery" below; registers the EXIT/INT/TERM teardown trap only *after* a successful create, mirroring `provision.sh:700-704`; writes the crash-recovery sidecar `ec2-instance.json` unconditionally (instance id, region, created-at — the EC2 analog of `provision.sh`'s `fly-machine.json`) plus `ec2_instance_id`/`ec2_ami` onto `run.json` when `LEERIE_RUN_ID` is set, mirroring `provision.sh`'s `fly_machine_id` sidecar-write timing — before any orchestrator code runs, so `resume` survives a Ctrl-C during seed); `wait_for_instance_ready()` (poll `describe-instances` for `State.Name == running`, then `describe-instance-status` for both `InstanceStatus.Status` and `SystemStatus.Status == ok` — DESIGN §6 is explicit that `running` alone is not SSH/SSM-reachable, unlike Fly's `started`); `stop_instance()` / `terminate_instance()` (`aws ec2 stop-instances` / `terminate-instances`; both idempotent no-ops on an empty `LEERIE_EC2_INSTANCE_ID`); `decide_ec2_teardown()` (the same three-disposition classification `decide_teardown()` in `provision.sh` implements — sync-then-terminate / detach / pause — reusing `LEERIE_REMOTE_EXIT_RC` and the `LEERIE_TEARDOWN_DONE` idempotency guard unchanged, since DESIGN §6 states the exit-code classification table is runtime-agnostic by construction; the clean-exit branch calls `_try_fetch_state_for_ec2_teardown()` — a hook, overridable by tests, that sources `scripts/remote/ec2-fetch-branch.sh` and calls its `fetch_state_ec2()` (fails closed — leaves the instance running — if that file is absent, e.g. an older checkout) — BEFORE `terminate_instance()`, mirroring `provision.sh:262-272`'s one-way-ratchet ordering: destroy-then-fetch would make paid-for LLM work unrecoverable). `kill`'s EC2 action (see the "Runtime mode" section above) reuses this same `_try_fetch_state_for_ec2_teardown()` → `terminate_instance()` ordering directly, rather than duplicating it. No auto-finalize (push + PR) integration yet — unlike `provision.sh`'s `decide_teardown`, `decide_ec2_teardown`'s clean-exit branch only syncs-then-terminates or leaves-running-on-sync-failure; wiring `host_finalize` in is deferred to a later subtask. Root-EBS-volume lifecycle needs no dedicated reap function (DESIGN §6 "EBS volume lifecycle" case 1: `DeleteOnTermination=true` is AWS's default and this design adopts it as-is — no `destroy_volume()` counterpart exists or is needed, unlike Fly; a failed `run-instances` call creates nothing to orphan-clean, unlike Fly's pre-create volume window). |
| `scripts/remote/ec2-resume-instance.sh` | Resume helper for paused EC2 runs (the EC2 counterpart to `scripts/remote/resume-machine.sh`), sourced by the launcher's `RUNTIME=ec2` branch alongside `ec2-provision.sh` (for `wait_for_instance_ready`/`_aws_region_profile_args`/`decide_ec2_teardown`). The launcher resolves a paused instance id from `ec2-instance.json`/`run.json`'s `ec2_instance_id` field when `--run-id`/`LEERIE_RUN_ID` is set and calls `resume_instance()` before falling back to a fresh `provision_instance()` call — unlike Fly's bare-`resume` PID-record auto-discovery, EC2 resume requires an explicit run-id today (auto-discovery is not yet wired for EC2). `kill`'s EC2 action (feat-006, see the "Runtime mode" section above) also sources this file, but only for `_resolve_ssh_target_from_instance` — re-resolving `LEERIE_EC2_SSH_TARGET` before the fetch-before-terminate sync — not for `resume_instance()` itself. Exports `resume_instance(<instance-id>)`: describes the instance's current state; if already `running`, logs and skips straight to the readiness/ssh-target steps (idempotent — no `start-instances` call); if `stopped`/`stopping`/`pending`, issues `aws ec2 start-instances`; if `terminated`/absent, fails with the same "no longer recoverable" hint `resume_machine()` gives for a destroyed Fly machine. Then calls `wait_for_instance_ready()` (unchanged, from `ec2-provision.sh`) so the same `running` + `InstanceStatus`/`SystemStatus` `ok` gate applies as on first provision. Re-resolves `LEERIE_EC2_SSH_TARGET` from the instance's current `PublicIpAddress` via `describe-instances` — EC2 assigns a new public IP on every stop/start cycle absent an attached Elastic IP, so the address from provision time cannot be reused. Re-arms the `decide_ec2_teardown` EXIT/INT/TERM trap (the launcher process is fresh on resume, mirroring `resume_machine()`'s trap re-arm) and clears `paused_at`/`pause_reason` on the run.json sidecar when one is resolvable. Never calls `terminate-instances` or `delete-volume` on any path — resume is a pure wake path, honoring the one-way-ratchet invariant `decide_ec2_teardown()` already encodes elsewhere. |
| `scripts/remote/ec2-fetch-branch.sh` | EC2 counterpart to `scripts/remote/fetch-branch.sh` — the stream-back half of DESIGN §6 "Transport substitution for `flyctl ssh console`", sourced by `ec2-provision.sh`'s `_try_fetch_state_for_ec2_teardown()` hook and by the launcher's `finalize` EC2 arm (the counterpart of the Fly path's `fetch_branch` call — `finalize` wakes a stopped instance first, since `fetch_state_ec2` needs a reachable `LEERIE_EC2_SSH_TARGET` and a stopped instance has none, then re-stops it only if it was the one to wake it). Exports `fetch_state_ec2()`, porting `fetch_branch()`'s four steps (run discovery; branch-existence probe + git-bundle stream-back with the same subtask-branch defense-in-depth bundling and run-branch-only retry fallback; `.leerie/runs/<run-id>/` tar stream-back with the same `no_push`-stripper conditional on branch presence; best-effort, never-clobbering `.leerie/config.toml` + `.leerie/Dockerfile` stream-back) verbatim, with the transport substituted: short text commands (run discovery, `git rev-parse --verify`, `git for-each-ref`, `.leerie/` file existence probes) go over `ec2_remote_exec` (SSM, from `ec2-lib.sh`) since their output is small and command-substitution-safe; binary bulk data (the git bundle, the run-state tar, each streamed `.leerie/` file's bytes) goes over a private local helper, `_ec2_fetch_ssh` — a plain `ssh $LEERIE_EC2_SSH_TARGET "<cmd>"` invocation whose raw stdout is redirected straight to a host-side file/pipe, never captured via bash command substitution (unlike `ec2_remote_exec`, which drops trailing newlines/NUL bytes and would silently corrupt a bundle). `ec2_tar_pipe` itself is not reused here because it is upload-only (host stdin → instance `tar -x`); `_ec2_fetch_ssh` is `ec2-fetch-branch.sh`'s own download-direction counterpart, mirroring `fetch-branch.sh`'s `_fetch_machine_exec ... > host_bundle` binary-safety pattern one-for-one. Preflight requires `LEERIE_EC2_INSTANCE_ID`, `LEERIE_EC2_SSH_TARGET`, `USER_REPO`, and `require_aws` (from `ec2-lib.sh`). |
| `scripts/remote/ec2-ssm.sh` | SSM Session Manager transport substitution for `flyctl ssh console`'s *launch/attach* roles (DESIGN §6 "Transport substitution for `flyctl ssh console`"; the stream-back role is `ec2-fetch-branch.sh`, already shipped — see above; the small-command-exec and bulk-upload roles are `ec2-lib.sh`'s `ec2_remote_exec`/`ec2_tar_pipe`, already shipped). Wired into the launcher's `RUNTIME=ec2` dispatch branch: `ec2_launch_detached()` runs the detached-orchestrator Python launch wrapper, and `ec2_attach()` (via the `_attach_to_live_orchestrator_ec2()` helper this file also exports — the EC2 counterpart of `lib.sh`'s `_attach_to_live_orchestrator`, reusing `lib.sh`'s `render_tail_wrapper()` since the wrapper text is transport-agnostic POSIX sh) both tails the orchestrator log on a fresh launch and handles the rc=75 flock-loser smart-resume pivot (`container_rc=130`, mirroring the Fly branch's identical routing) and the early-resume flock probe. Default transport is **SSM Session Manager**, not SSH — DESIGN §6 states this explicitly (no inbound security-group rule, no key-pair distribution, no public IP; auth flows through the same AWS credential chain as the rest of the EC2 runtime). Exports `ec2_launch_detached()` and `ec2_attach()`, both built on a shared `_ec2_ssm_session <interpreter>` helper: `aws ssm start-session --target <instance-id> --document-name AWS-StartInteractiveCommand --parameters command="<wrapper>"` is the SSM analog of `flyctl ssh console --pty=false -C "python3 -"` (`ec2_launch_detached`, `<interpreter>="python3 -"`) or `-C "sh -s"` (`ec2_attach`, `<interpreter>="sh -s"`) — a short, fixed-size bootstrap naming the interpreter, with the caller's actual (potentially multi-KB) payload — e.g. the detached-launch wrapper, or `render_tail_wrapper()`'s output from `lib.sh` — piped through this function's own stdin, which `AWS-StartInteractiveCommand`'s interactive session forwards to the remote interpreter exactly like a normal ssh session would. This is deliberately different from `ec2_remote_exec`'s approach of embedding its whole command inside `--parameters command=[...]`: that document parameter has a ~4 KB ceiling, far under the size of a real launch-wrapper/tail-wrapper script, so only the interpreter name goes in the parameter and the payload travels over stdin instead, where no such ceiling applies. `<wrapper>` (the value actually sent in `--parameters`) is `<interpreter>; __rc=$?; printf "...sentinel..."`, base64-encoded and decoded remotely via `bash <(echo ... | base64 -d)` — a process substitution, not a `... | bash` pipe, since piping the decode into `bash` would consume `bash`'s own stdin as the pipeline's final stage and shadow the interactive session's real stdin (the caller's payload) before `<interpreter>` ever read it. Remote-rc recovery reuses `ec2_remote_exec`'s sentinel convention (`aws ssm start-session` exits 0 itself regardless of the wrapped command's real exit status — the same session-manager-plugin limitation); notably this is how `ec2_launch_detached` propagates rc=75, the flock-loser smart-resume pivot. Both functions fail closed (return 1, actionable stderr, no `aws` call) when `LEERIE_EC2_INSTANCE_ID` is empty. An SSH fallback (`LEERIE_EC2_KEY_NAME` + an inbound security-group rule on port 22) is documented in DESIGN §6 as available for operators whose IAM policy disallows SSM, but is not implemented here — not the default, and not required for this file's baseline. |
| `scripts/host-finalize.sh` | Host-side push + PR creation block, sourced by five call sites: the local-runtime post-run code path in `leerie`, `decide_teardown` in `scripts/remote/provision.sh` (Fly clean-exit auto-finalize), the `leerie finalize <run-id>` recovery fast-path, and two that need only `host_prepush_preflight` (described at the end of this row) — the launcher's host-preflight block and the `chain` arm's per-wave probe. Exports `host_finalize <run-dir>`: honors `run.json.no_push` (skip — this is the **intent** flag, written by the orchestrator's `phase_finalize` from `push_will_happen(no_push, host_no_push)`, not the launcher-forced mechanism flag), short-circuits when `pushed_at` is already set **by branch position, not mere presence** (DESIGN §6 *Finalization*): compares the local run-branch tip against the pushed origin tip via `git rev-parse` / `git ls-remote` — equal tips → no-op (the idempotent common case, including fully-pushed chain waves); origin a strict ancestor of the local tip via `git merge-base --is-ancestor`, or origin absent (a prior finalize pushed a *partial* branch — e.g. a mid-wave `die()` stamped `finished_at` before the completion gate) → falls through to a fast-forward re-push + re-open PR, still behind the completion gate so only a `completed_waves == len(waves)` run can re-push (the gate itself fails open on a missing/unreadable `state.json`, so that check only applies when the signal exists); a *diverged* origin (has commits the local branch lacks) keeps the idempotent short-circuit instead, since its push could not fast-forward; on success keeps `pushed_at` set and sets `pr_url` (invariant `pr_url ⇒ pushed_at` preserved), **defense-in-depth**: when the run branch named in `run.json` does not exist locally (`git rev-parse --verify refs/heads/<branch>` fails — the cleared-but-empty terminal-state case where no `setup-run.sh` ran), logs "run branch absent locally; treating as no-op" and returns 0 rather than attempting a push that would error with `src refspec ... does not match any`. **Empty-run-branch guard** (defense-in-depth): when the run branch exists but has **no commits beyond `working_branch`** (`git rev-list --count <working_branch>..<run-branch>` == 0 — the run reached the host push path with an un-integrated branch, e.g. a died in-container `finalize.sh` that the resume completion guard mistook for success), writes a `push_error` to `run.json`, prints an actionable recovery hint (naming the `leerie/subtasks/<run-id>/*` branches and the `git log <working>..<run>` inspection command), and returns 1 rather than pushing an empty branch that would fail at `gh pr create` with "No commits between …". This mirrors `finalize.sh`'s own non-empty check, which the host push path never re-runs; the base is `working_branch` (the diff fork-point), and the check is skipped (rather than blocking) when `working_branch` is unresolvable. Then runs `git push -u origin <run-branch>` (with `--no-verify` if `NO_VERIFY_PUSH=true`), **capturing the push's two streams separately** into a pair of `mktemp` files: `push_stderr` (stderr alone) is what the classifier below reads, and `push_all` (stderr plus a labelled `--- pre-push hook output (stdout) ---` section) is what the operator and `run.json.push_error` get. The split is not cosmetic. git forwards a pre-push hook's stdout to git's own stdout, and `tsc`/`biome` write diagnostics there (jest and vitest use stderr, which is why the gap went unnoticed), so the historical `2>&1 >/dev/null` capture recorded two pnpm deprecation warnings for a push whose real cause was 13 lines of `TS2307` — undiagnosable from leerie's own output, at the end of a $57 run. But the naive repair, plain `2>&1`, breaks the other consumer: a hook that refreshes submodules or runs `git ls-remote` prints `^fatal:`/`^remote:`-framed lines on stdout, which is exactly what `_host_finalize_is_auth_or_network_push_error` matches, so merging flips a hook failure to "auth/network" and suppresses the `--no-verify` hint (measured against the real classifier: 3 of 3 adversarial hook shapes flip). Keeping the streams apart leaves the committed 23-case corpus score unchanged **by construction** rather than by re-measurement. When `mktemp` yields nothing (the full-`/tmp` case N30 documents) the capture degrades to the historical stderr-only form rather than losing the push. Both copies are bounded, at different sizes and for different reasons: the **printed** copy at 4000 bytes because a hook running a test suite reaches megabytes, and the **persisted** copy at 32 KiB because `push_error` reaches `run.json` as a single `jq --arg` value and a single argv element cannot exceed `MAX_ARG_STRLEN` (131,072 bytes). That ceiling is not theoretical: one recorded `push_error` is already 104,520 bytes of jest output — 80% of it on stderr alone — so appending hook stdout to the same value is exactly what makes it reachable, and past it `jq` cannot be exec'd at all, aborting `host_finalize` under `set -e` *before* the diagnostic prints. Both truncations pipe through `tail -c` unconditionally rather than testing `${#var}` first, because `${#var}` counts CHARACTERS under a multibyte locale while `tail -c` and the argv ceiling count BYTES — a char-based guard under-measures by up to 4x, and 32,768 four-byte characters is exactly `MAX_ARG_STRLEN` with the guard silent. `tail -c` returns a short input unchanged, so the marker is added only on a real cut. A byte cut can land mid-character; verified rather than assumed, `jq --arg` substitutes U+FFFD and still writes valid JSON at rc 0, so the worst case is one replacement character. Both truncations are tail-anchored and indented with `sed 's/^/    /'` rather than `printf '    %s\n'`, which indents only the first line of a multi-line value and ran git's error onto the tail of a hook's last line. The hook diagnostic additionally states **which tree the hook measured** — git runs `pre-push` against the checked-out working tree, and leerie never checks out the run branch, so a hook that lints or typechecks is reporting on host state, not on the pushed commits — and surfaces the deduplicated set of `state.json` `blt_results` commands that **passed in-container**, the counter-evidence that the same check succeeded on the tree that actually holds the run's changes. Pinned by `tests/test_push_output_capture.py`. On push failure, classifies whether a `pre-push` git hook (rather than a push/auth/network problem) caused it via a **structural probe** (N24), `_host_finalize_pre_push_hook_present()`: resolves the hooks directory the way git itself does — `git config --get core.hooksPath` (relative paths resolved against `$USER_REPO`) falling back to `git rev-parse --git-path hooks` (handles worktrees / non-standard `.git` layouts) — and tests for an executable `pre-push` file there; when one exists, `_host_finalize_is_auth_or_network_push_error()` classifies stderr with a **single** arm anchored to how git FRAMES failures, not to bare English phrases: `_host_finalize_git_framed_auth_or_network()` requires the phrase on a line git itself prefixes (`fatal:`/`remote:` — `error:` is deliberately excluded, since git uses it for `failed to push some refs` on every failed push and Java emits `Error: Unable to access jarfile`). A second `_host_finalize_ssh_transport_failure()` arm, accepting an `ssh:`/`git@host:` line only when git also emitted its `fatal: Could not read from remote repository` companion, was removed as **provably dead**: that companion condition is `^fatal: could not read from remote repository`, which the first arm already matches via `^fatal:` + the same phrase, and the first arm runs first — so no input could reach the second arm undecided. The companion line alone was the discriminator; ssh-transport failures are still classified by it. Three alternatives carry a deliberate qualifier because case-insensitive matching cannot tell git's `fatal:` from a third party's `FATAL:` — `authentication failed for '` and `unable to access '` keep git's quote, `permission denied \(publickey` keeps the paren — and the bare transport phrases (`could not resolve host`, `connection refused|timed out`, `operation timed out`, `no route to host`) are deliberately absent: behind the `^(fatal|remote):` anchor they are unreachable for real git, which emits them on an unprefixed `ssh:` line or as the tail of an `unable to access '<url>':` line the list already matches. **Transport matters as much as the pattern here, and both alternatives shipped a misclassification.** The classifier reads the FULL captured stderr, which for a pre-push hook running a test suite is megabytes. `printf … | grep -q` fails because `grep -q` exits at its first match and closes the pipe: the writer takes SIGPIPE and, under the `set -euo pipefail` every caller sets, the pipeline reports 141 **even though grep matched** — reproduced on 1.19 MB, classifying a real credential failure as a hook failure. A herestring fails differently: bash backs one larger than a pipe buffer with a temp file (measured — 32 KiB is a pipe, 64 KiB is `sh-thd.XXXXXX`; it honours `$TMPDIR` and falls back to `/tmp` when that is unusable, so the dependency is on *some* writable temp dir), and when the file cannot be created the redirection returns 1, indistinguishable at the call site from "no match" — reproduced with `/tmp` as a full 256 KiB tmpfs, and note that on macOS `/tmp` shares the APFS container with `$HOME`, so N30's disk-full case implies it. The shipped form is process substitution, `grep -qiE "…" < <(printf '%s\n' "$1")`: no temp file at any size, and `pipefail` never sees the writer because it is not part of a pipeline. `tests/test_host_finalize_hook_probe.py` pins the transport structurally (a test cannot fill a filesystem in CI) alongside the behavioural 1.19 MB case. Scored against a committed 23-case corpus (9 real `git push` failures, 14 realistic hook outputs): the previous bare-phrase list got 5/14 hook cases right, this gets 14/14, both 9/9 on git. Each alternative's necessity was measured by ablation against that corpus. The purpose is unchanged: a genuine auth/network failure that happens to coincide with an installed hook must never be misclassified as a hook failure, and vice versa. This replaces the previous vendor-text grep (`husky`/`pre-push script failed`/`exit code 254`), which missed non-husky-branded or newer-husky hook failures entirely; that grep is retained only as a supplementary "which hook" naming signal in the printed diagnostic, never as the classification itself. Resolves the PR base as `run.json.pr_base_branch`, falling back to `working_branch` when the field is absent (older runs finalized before the "PR base branch override" field existed) — `working_branch` itself is never the PR base directly once `pr_base_branch` is present, only the diff fork-point. Before PR creation, validates that the resolved base still exists on origin via `git ls-remote --exit-code --heads`; if deleted (common when a stacked run's parent was squash-merged while this run was in flight, or an overridden base was renamed/removed), falls back to the repo's default branch. Then `gh pr create --base <resolved base>` (using `pr_title`/`pr_body` from `run.json` if the pr_writer worker populated them, otherwise the deterministic fallback), wrapped in a bounded retry (`0 5 10 20 30`s backoff, ~68 s total) to ride out GitHub's post-push ref-indexing lag ("No commits between" / "Head sha can't be blank"). PR-creation failure is non-fatal (push already succeeded); the error message suggests a retry command using the resolved base (original or fallback). Replaces ~140 lines of inline launcher code with a single function call so the three callers stay in sync. **Best-effort rebase onto the latest base** (DESIGN §6 *Finalization* "Rebase-onto-base before push"), inserted between the empty-run-branch guard and the push: if `pr_base_branch` resolves locally or on origin, creates a disposable `git worktree add` copy of `run_branch` and calls `orchestrator/leerie.py`'s `run_rebaser()` via a host-side python3 seam — the same `python3 <script> <<args>>` pattern `./leerie config --recapture` uses for `run_recapture_deps()`, except the python is written to a scratch file rather than a heredoc, because a `<<'PY' ... PY` heredoc whose closing redirection sits on the same line as a command-substitution close-paren (needed here to capture the worker's JSON stdout) fails to parse under bash 3.2 (macOS's `/bin/bash`; the recapture heredoc avoids this because it never captures stdout). `run_rebaser()` invokes the `rebaser` worker (a scoped, fully-agentic exception to §12 — see DESIGN §6) and mechanically re-verifies its claimed outcome (`check_rebaser_worktree_state`) before returning, so this shell function trusts the returned `status` as-is: on `"rebased"`, fetches the worktree's new tip back into the local `run_branch` ref and advances `working_branch` (both the git-ref sense, informationally, and via `_host_finalize_update_run_json` into `run.json`) to `origin/<pr_base_branch>` — the PROVEN-pitfall fix, since a rebase changes the run branch's parent chain and a stale `working_branch` would otherwise corrupt the `working_branch..run_branch` diff range with unrelated upstream commits; on `"irreconcilable"`/`"failed"`, leaves `run_branch`/`working_branch` untouched and folds the worker's `diagnosis` (via the `rebase_diagnosis_note` local, since `pr_body` does not exist yet at this point in the function) into whichever `pr_body` is later composed, LLM-authored or the deterministic fallback. Worktree-add failure, a missing/unresolvable base, or a failed python seam all skip the rebase and fall through to pushing `run_branch` unchanged. **The verdict travels on its own channel — a file, never stdout.** The seam takes the output path as `argv[9]` and `write_text`s `json.dumps(result)` there; the shell invokes it as a plain command with stdout redirected to stderr and then reads the file back. This is not a style choice: `run_rebaser` calls `claude_p`, whose `log()` is a bare `print(..., flush=True)` to **stdout**, so the original `_rebaser_json="$(python3 … 2>&1)"` capture handed `jq` several hundred lines of log text with the JSON at the end. `jq` returned rc 5 every time and control fell to the `*)` arm below — measured across a real state directory, `rebase_disposition_status` was `unusable` in **9 of 9** runs that ever reached the rebaser, meaning the `rebased` and `irreconcilable|failed` arms had never once executed and a rebaser returning a valid `{"status":"failed", …}` with a full conflict diagnosis had that diagnosis silently discarded instead of folded into the PR body. **The general rule, which applies to every shell-hosted seam and not just this one: stdout is the log channel, so a seam may print to it and a caller may consume the seam's exit code, but no caller may ever CAPTURE a seam's stdout for parsing.** A seam that needs to return a value takes an explicit output-path argument and writes there. Moving `log()` to stderr is not the alternative — on the remote runtime `sys.stdout` *is* `<run_dir>/orchestrator.log` by launcher design, so that would break log capture on every remote run. Enforced by `tests/test_orchestrator_seams_dont_capture_stdout.py`, which derives the seam list by scanning for the `spec_from_file_location("leerie_orch"` loader rather than enumerating known seams, so a third seam is caught too. At the time of writing there are exactly two: this one (needs a verdict → writes to an argv path) and `leerie`'s `config --recapture` (needs only an exit code → stdout flows to the terminal uncaptured), which is the counter-example showing the rule is about capturing rather than printing. Distinct from the seam-failure case (`_rebaser_rc != 0` or an absent/empty verdict file) is the case statement's `*)` fallback arm, reachable even when the seam itself succeeded (rc=0, non-empty file) if the returned JSON is empty, unparseable, or lacks a usable `status` field — logged as "rebaser returned no usable status; pushing `$run_branch` as-is" rather than "rebaser python seam failed". Its behavior is otherwise identical to the seam-failure/`irreconcilable`/`failed` cases: no diagnosis note is attached to the PR body (there is no parsed `.diagnosis` to fold in), and it never blocks, retries, or pauses finalize. Before falling through, the arm also prints `$_rebaser_json` (**tail**-truncated to 2000 bytes — a malformed payload shows its corruption at the end, and `head` was what preserved 2000 bytes of pure log noise while the channels were shared) and the `jq -e` parse exit code to stderr, and persists both — plus `rebase_disposition_status=unusable` — onto `run.json` via `_host_finalize_update_run_json` (see the `run.json` field table below), so the previously stderr-only, easy-to-lose diagnostic survives past the log. This step never returns non-zero, blocks, or pauses finalize. **Also exports `host_prepush_preflight <repo> <branch>`**, called from the launcher's host-preflight block as step 4 rather than from `host_finalize` — its entire value is running *before* the run spends. Everything above turns a hook rejection into a legible message; this turns it into one the operator gets at t=0. The prediction is sound by construction: the hook measures the host checkout's working tree, and leerie never modifies that tree during a run (workers run in the container, the finalize rebase uses a disposable worktree), so a probe at run start and the real push at finalize see the same inputs — measured on the motivating run, whose host manifests were rewritten at 18:46:10 while the run started at 18:48:14, so the defect that rejected the push 2h19m later was already present, as it was for all four earlier `pnpm: not found` rejections. Gated on `_host_finalize_pre_push_hook_present` first, so a hookless repo pays nothing (not even a round trip to origin). Probes with `git push --dry-run`, which **runs the hook and creates no ref** locally or on the remote (verified against real git), under `GIT_TERMINAL_PROMPT=0` so an HTTPS remote with no cached credential fails fast instead of blocking run start on a username prompt — that needs no extra branch, since git's resulting `fatal: could not read Username for '…': terminal prompts disabled` already matches two of the classifier's alternatives and lands in the auth/network arm. ssh's `BatchMode` is deliberately not forced: an ssh passphrase prompt moved earlier is arguably an improvement, and BatchMode would break agent-less setups that work today. The refspec is a **new** ref, `<branch>:refs/heads/leerie/runs/preflight-probe`, and that is load-bearing rather than incidental: probing the already-up-to-date working branch still runs the hook but hands it an **empty stdin**, so any hook that iterates the ref updates git feeds it exits 0 — a false pass, the worst outcome for a probe whose job is predicting a rejection. A new ref reproduces the exact line finalize will produce (all-zero old sha = "new branch"). Classifies with the same stderr-only rule as the push path, so a transport failure returns 0 silently (the real push reports it properly, and warning on every offline run would be noise). Returns 1 with a warning naming the probed branch, a tail of the hook's combined output, and both escape hatches. The launcher treats the verdict as **advisory** (`\|\| true`) — a hook can legitimately fail on a tree the run is about to fix, and this must never become a new way to refuse to start; skipped under `--no-push`, under `--no-verify` (hooks are being bypassed anyway), for a detached HEAD, and via `LEERIE_SKIP_PREPUSH_PREFLIGHT=1` for repos whose hook is expensive. **`chain` probes once per WAVE, not once per job**: it backgrounds one `./leerie` per job against a single shared checkout, so every child would otherwise run the hook concurrently — N lint/typecheck runs computing one answer and N identical warnings. The chain arm probes itself immediately after checking out the wave's base (per wave, because that checkout changes the tree between waves, and the probe must read the tree those jobs will push from) and hands each child `LEERIE_SKIP_PREPUSH_PREFLIGHT=1`. Its `--no-push` skip has to read `_ch_passthrough` rather than `NO_PUSH`, because that variable is first assigned further down the launcher, *after* the chain arm — so the single-run gate's opt-out has no counterpart here unless one is written explicitly. `group` is deliberately unaffected — its members are separate repositories, so per-member probing is correct there. Pinned by `tests/test_prepush_preflight.py`, including the launcher block extracted verbatim rather than reproduced. |

### Python runtime — provisioned inside the container

Leerie requires Python 3.10+. The container image installs Debian 13's
`python3` (3.13), so the host needs no Python at all. The orchestrator's
source is baked into the image at `/opt/leerie-image/`; on local runs
the launcher's bind mount (`-v $LEERIE_REPO:/opt/leerie-image:ro`)
shadows the baked copy, so iterating on `orchestrator/leerie.py` needs
no image rebuild.

The orchestrator prefers stdlib. Third-party runtime libraries are
permitted only when they replace non-trivial logic with a widely-used
implementation, earn their distribution cost, and are documented here.
Pins live in `requirements.txt`, installed once per image build
(`pip3 install --break-system-packages --no-cache-dir -r requirements.txt`).
No `pyproject.toml`, no PyPI release.

Current runtime deps:

- `tenacity` — exponential backoff for transient `claude -p` auth/rate-limit
  errors (§3 *Auth/quota backoff*).
- `tree-sitter` + `tree-sitter-language-pack` — parser core and prebuilt
  grammars (Python, TypeScript, JavaScript, Ruby, Go, Rust, …) powering the
  P6 repo-map (`_build_repo_map`, DESIGN §5½). Prebuilt manylinux wheels,
  no C build needed.
- `boto3` / `botocore` — AWS SDK for the `--runtime ec2` path, needed for
  AWS's credential-resolution chain (env → shared config/creds → SSO →
  instance profile/IMDS). Available only **inside the container image**
  — the host has no pip/venv surface at all, so every host-side EC2 API
  call instead shells out to the **`aws` CLI** via `ec2-provision.sh` /
  `ec2-ssm.sh` (mirroring how the Fly path shells out to `flyctl` rather
  than importing a Go SDK). `boto3`/`botocore` are reserved for future
  in-container AWS calls; none exist yet.

`pytest` remains the sole dev dependency, run on the host against the
bind-mounted source.

### Path A — Claude Code plugin marketplace (primary)

```
/plugin marketplace add enricai/leerie
/plugin install leerie@enricai-leerie
# then inside Claude Code:
/leerie "task description"
```

`marketplace.json` exposes one plugin (the existing `plugin.json`).
Claude Code clones the repo into its plugin directory and registers the
`commands/` and `skills/` entries. `/leerie` then runs the plugin
skill at `commands/leerie.md`, which shells out to the on-disk
`leerie` launcher in the cloned plugin directory — and through it,
to `nerdctl run`. See §0.5 for the launcher's per-mode (terminal vs
plugin) TTY adaptation.

### Path B — `curl | bash` installer (secondary)

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

The script:

1. **Preflight**: verifies `git`/`curl` on `PATH`. Auto-installs `claude` if
   missing via Anthropic's native installer (opt out: `--no-claude-install` /
   `LEERIE_NO_CLAUDE_INSTALL=1`) — a hard stop otherwise, since leerie shells
   out to `claude -p` for all LLM work.
2. **Runtime install**: per `uname -s`. macOS installs Colima via brew and
   starts the VM; Debian/Ubuntu installs and starts the full **rootless**
   containerd stack (containerd, rootlesskit/slirp4netns/uidmap, nerdctl, CNI,
   BuildKit) and verifies reachability via `nerdctl info`; Fedora/RHEL and
   Arch print a hint to `docs/INSTALL.md` and exit non-zero. Opt out entirely
   with `--no-runtime-install` / `LEERIE_NO_RUNTIME_INSTALL=1`.
3. **Clones** `enricai/leerie` to `$LEERIE_HOME` (default `~/.leerie`) —
   shallow for fresh installs, `git pull --ff-only` for upgrades.
4. **Symlinks** `$LEERIE_HOME/leerie` → `~/.local/bin/leerie`.
5. **PATH check**: prints the shell-rc line to add if `~/.local/bin` is
   missing from `$PATH` (never edits it silently).
6. **Verifies** via `leerie version` (fast path, no container startup).

Supports `--dry-run` (prints actions without executing), `--prefix DIR`
(overrides `LEERIE_HOME`), `--no-runtime-install`
(`LEERIE_NO_RUNTIME_INSTALL=1`), and `--no-claude-install`
(`LEERIE_NO_CLAUDE_INSTALL=1`).

### `version`

`leerie version` reads `.claude-plugin/plugin.json`'s `version`
field — single source of truth. Two parallel readers:

- **Orchestrator** (`_read_version()` in `leerie.py`): stdlib `json` load.
  Exercised by `tests/test_version_flag.py`.
- **Launcher** (bash `awk` extraction): used by the fast path that
  short-circuits container startup. Both readers return the same value
  on the same `plugin.json`, and `tests/test_version_flag.py` guards
  the canonical surface.

`install.sh` uses `leerie version` as its end-to-end smoke test — and
because the fast path doesn't require a running container, the smoke
test runs the moment the symlink is in place.

### Stale-install warning

The installed checkout at `$LEERIE_REPO` is never updated by running
`leerie`; only re-running `install.sh` (or a manual `git pull`) advances it,
so an install can sit arbitrarily far behind `origin` while the operator
believes they're running current code.

`_warn_if_leerie_stale()` runs in the host preflight and **warns, never
blocks**:

- Skips silently when `$LEERIE_REPO` isn't a git checkout, HEAD is detached,
  or the branch has no upstream.
- Compares `git rev-list --count HEAD..@{upstream}`; on non-zero, reports
  both `plugin.json` versions (local and upstream) plus the update command.
- Fetches at most once per 24h (gated by an mtime stamp file, `timeout 5`,
  backgrounded, non-fatal) since a never-fetched checkout's cached
  remote-tracking ref is otherwise as stale as the checkout itself.
- Every git call is `|| true`-guarded — offline/slow-network must never
  fail a run.

### In-repo tee-log warning (N5)

Operators commonly run `leerie task | tee leerie-<task>.log`, and the log
lands in `$USER_REPO` by default — which is bind-mounted whole into every
worker's container at `/work`. A worker can then `cat`/`grep` its own
orchestration log, including gate vocabulary and internal check names,
defeating judge independence.

`_warn_if_log_in_repo()` runs immediately after the stale-install warning,
before host preflight. It globs `"$USER_REPO"/leerie-*.log`, and for each
match logs a warning naming the file plus the bind-mount risk. **Detection
and warning only** — it never blocks the run; relocating the default log
destination and teeing the launcher's own output there is `--log-file`
(N5b), documented below.

Mirrors `_warn_if_leerie_stale()`'s detection-and-warn shape and is tested
the same way (`tests/test_log_in_repo_warning.py`, extracting the function
verbatim from the launcher).

---

### `config`

Launcher bash case arm: `config)`. `leerie config` is a host-only fast
path — it exits before `nerdctl run` and never starts a container. It is
listed alongside `version` in the ownership-guard skip-list so it never
claims a state directory. Four sub-modes:

- **bare (`leerie config`)**: prints effective build/lint/test config for
  `$USER_REPO` with `[config]` / `[inference]` provenance per axis (reads
  `.leerie/config.toml` if present, otherwise infers). Also prints any
  non-comment `key = value` lines from `leerie.toml` when that file exists.
- **`leerie config --init`**: creates `.leerie/` and writes
  `.leerie/config.toml` with auto-detected BLT values (uncommented) and a
  commented `setup_packages` example. Refuses with exit 1 if `config.toml`
  already exists. Prints the path and suggests `git add .leerie/`.
- **`leerie config --chat`**: execs interactive `claude` (NOT `claude -p`)
  with `--system-prompt-file $LEERIE_REPO/prompts/config_chat.md` and
  `--add-dir $USER_REPO`. No container started. Exits 1 if
  `prompts/config_chat.md` is missing.
- **`leerie config --recapture [--force]`**: host-only. Calls
  `run_recapture_deps()`, which consolidates across all finished runs with
  `logs/` under the state dir (or just `--run-id` if given). Without
  `--force`, runs already carrying a `dep_capture.done` sentinel are skipped
  and the write is a never-clobber **union**. `--force` drops the sentinel
  and switches to a wholesale **replace** (`capture_repo_deps(replace=True)`)
  — an empty capture leaves the config untouched. Each run's `State` is
  flocked (skipped, not fatal, on `StateLockedError`). Exits 1 if no runs
  directory or no finished run found. `orchestrator/leerie.py`'s sole
  third-party import (`tenacity`) is deferred into `claude_p()` rather than
  module scope, since the host `python3` isn't guaranteed to have
  `requirements.txt` deps (§0).

All four sub-modes share an inline BLT inferrer (`_config_read_key`,
`_infer_axis`, `_axis_source`) implemented directly in launcher bash so the
verb needs no container or orchestrator import; `_infer_axis` mirrors
`_infer_build_lint_test()`'s precedence by hand (§4, below).
`tests/test_config_verb.py` runs per-mode unit tests against a
self-contained bash harness plus a parity guard that diffs the real
`config)` case arm's inference output against `_infer_build_lint_test()`
across a fixture matrix, so the two can't silently diverge.

Maps to `DESIGN.md`: §6½ *Declared BLT commands* (the `.leerie/config.toml`
format and resolution); §6½ *Per-repo container image* (`setup_packages`,
`prompts/config_chat.md` for the interactive session).

---

## 0.5. Container shape

Leerie runs entirely inside a single container per run (DESIGN §6 *Worker
subtree termination*). The orchestrator is PID 1 in the container;
every `claude -p` worker it spawns is a child process in the same PID
namespace; every Bash tool call those workers make lands in the same
namespace too. When PID 1 exits, the kernel reaps the namespace —
which is the abnormal-exit cleanup guarantee.

### Runtime requirements per OS

| OS | Container engine | CLI | VM? |
|----|------------------|-----|-----|
| macOS (arm64 or x86_64) | containerd inside a Colima-managed Linux VM | `nerdctl` host-side shim (`colima nerdctl install`) | Yes — managed by Colima |
| Linux (Debian/Ubuntu) | rootless containerd (native) | `nerdctl` from upstream (+ CNI/BuildKit/RootlessKit) | No |
| Linux (Fedora/RHEL, Arch) | rootless containerd (manual) | `nerdctl` — set up by hand per `docs/INSTALL.md` | No |

The launcher detects `uname -s` and runs the right preflight. macOS:
requires `colima` on `PATH`, checks `colima status`, auto-installs the
`nerdctl` shim if missing, then checks `nerdctl info`. Linux: if `nerdctl`
is missing and auto-install isn't opted out, `runtime_install_linux`
(`scripts/runtime-install.sh`) stands up the full rootless stack on
**Debian/Ubuntu** (containerd + rootless prerequisites via apt, nerdctl/CNI/
BuildKit, the rootless setuptool) and verifies `nerdctl info`. **Fedora/RHEL
and Arch** aren't auto-installed yet — the launcher prints a hint to
`docs/INSTALL.md` "Rootless mode" and exits non-zero. Pass
`--no-runtime-install` (`LEERIE_NO_RUNTIME_INSTALL=1`) to skip the
Debian/Ubuntu auto-install and fall back to the hint.

`brew install nerdctl` does NOT work on macOS — the Homebrew formula
has `Requires: Linux` because the nerdctl binary talks directly to a
containerd Unix socket. Colima's `colima nerdctl install` is the
supported macOS path; it drops a host-side shim on `$PATH` that
proxies every invocation to nerdctl inside the VM.

### Image build

`Dockerfile` at the repo root. Built locally on first run
(`nerdctl image inspect "$IMAGE_TAG"` miss → `nerdctl build`).
`IMAGE_TAG=leerie:<VERSION>` so a leerie upgrade triggers a fresh build
once and reuses the layer cache thereafter. ~60–120s first build,
subsequent runs < 3s.

Base layers (top-down):

- `debian:13-slim` — minimal, predictable, glibc-based.
- `apt-get install`: `ca-certificates`, `curl`, `git`, `openssh-client`,
  `python3`, `python3-pip`, `build-essential`, plus dev libraries
  (`zlib1g-dev`, `libyaml-dev`, `libreadline-dev`, `libffi-dev`,
  `libssl-dev`, `libpq-dev`, `libsqlite3-dev`, `libgdbm-dev`,
  `default-libmysqlclient-dev`) covering native-extension compilation:
  `node-gyp` (sharp, bcrypt), Ruby C gems (`nokogiri`, `pg`, `sqlite3`,
  `mysql2`, `ffi`), Python C extensions.
- `libc6` + `chromium` + `chromium-driver` + `fonts-liberation` — headless
  Chrome for browser-based testing, installed from Debian's own repos so
  browser/chromedriver stay in sync (Selenium Manager has nothing to
  download at runtime). `libc6` is upgraded in the same transaction since a
  lagging base-image glibc otherwise makes chromium fail with
  `undefined symbol: localtime64_r`. `/home/leerie/.cache/selenium` is
  pre-created (chowned to `leerie` at runtime). Workers run as non-root
  `leerie`, so Chrome's SUID sandbox is disabled via baked flags in
  `/etc/chromium.d/leerie-container-flags`.
- LTS Node **and** Python 3.12 baked via `mise install --system node@lts
  python@3.12`, with a stable `.../installs/node/lts-current` symlink so
  `ENV PATH` doesn't need the concrete version. These are the fallback
  versions mise's resolver uses when a repo declares none of its own
  (DESIGN §6½).
- corepack activated via `MISE_NODE_COREPACK=true` so a repo's
  `package.json` `packageManager` field selects its own pnpm/yarn (no
  globally pinned pnpm baked). `npm install -g
  '@anthropic-ai/claude-code@>=2.1.219'` installs the `claude` CLI
  (leerie enforces ≥2.1.22 at runtime for `--json-schema`; the image pin
  is ≥2.1.219 for the `claude -p` mid-stream-drop fix — §3 "Transient
  transport disconnect").
- `ENV PATH` order is load-bearing: `<system mise shims>` : `<LTS Node
  bin>` : `<MISE_DATA_DIR/shims>` : `$PATH` : `/home/leerie/.local/bin`.
  Baked tooling (LTS Node hosting `claude`) comes first so a repo's own
  pinned Node/Python can't shadow it; per-repo `MISE_DATA_DIR/shims`
  (populated at runtime by `phase_provision`, DESIGN §6½) comes next so a
  worker's ad-hoc Bash commands reach a repo-pinned runtime by name; `pip
  install --user` console scripts land last, so they can never shadow a
  baked-in binary. Pinned by `tests/test_dockerfile_path.py`.
- Non-root `leerie` user created with `--build-arg HOST_UID/HOST_GID`
  matching the host user, so container writes into `/work` and
  `/leerie-state` keep the host user's ownership.
- `git config --system --add safe.directory '*'` (in `/etc/gitconfig`):
  the container is single-tenant with `/work` its only repo, so
  blanket-allow is the standard mitigation for Colima/virtiofs presenting
  a mismatched gid that would otherwise trip git's CVE-2022-24765 check on
  worker `git -C <worktree-subdir> ...` calls.
- `WORKDIR /work`, `ENTRYPOINT ["/opt/leerie-image/scripts/container-entry.sh"]`.
  **No `USER leerie` directive** — ENTRYPOINT runs as PID 1 at the
  slice-owning identity so it can create `/sys/fs/cgroup/leerie.slice` and
  launch the **cgroup broker** before dropping privilege via `runuser -u
  leerie -- ...` (skipped in rootless mode — DESIGN §6 *Rootless
  exception*). See DESIGN §6 *Memory containment* for the full mechanism.

### Per-repo derived image (local nerdctl)

After the base image is confirmed present, the launcher checks for a
`.leerie/Dockerfile` in the user's repo (or auto-generates one from
`setup_packages` — see DESIGN §6½ *Per-repo container image*). The
relevant bash surface:

| Function / variable | Location in `leerie` | Purpose |
|---|---|---|
| `_leerie_sha256 <file>` | after base-build block | Portable sha256 of a file — uses `sha256sum` (Linux) or `shasum -a 256` (macOS) |
| `_leerie_repo_id` | after base-build block | Sanitized repo identifier from `git remote get-url origin` (or `basename $USER_REPO` fallback); lowercase, `[a-z0-9._-]` only, `/` → `-` |
| `resolve_repo_image_tag()` | after base-build block | Returns `leerie-repo/<repo-id>:<LEERIE_VERSION>` when a Dockerfile is present (real or to-be-auto-generated), empty string otherwise |
| `ensure_base_in_buildkit_ns` | after base-build block | Copies the base `$IMAGE_TAG` into the `buildkit` containerd namespace via `nerdctl save \| nerdctl --namespace buildkit load`, so BuildKit (whose containerd worker is bound to the `buildkit` namespace, not `default`) can resolve the derived `FROM $BASE_IMAGE` locally instead of falling back to the registry (a never-pushed tag 401s). Idempotent: skips when `nerdctl --namespace buildkit image inspect "$IMAGE_TAG"` already succeeds. Best-effort/non-fatal (logs a warning on failure). Called before both the language-dep probe build and `build_repo_image`. |
| `build_repo_image <tag>` | after base-build block | Runs `nerdctl build --build-arg BASE_IMAGE=<IMAGE_TAG> --build-arg HOST_UID/GID -t <tag> -f .leerie/Dockerfile <USER_REPO>`; exits 1 on failure. Runs in the `default` namespace (no `--namespace`), so the derived image lands where `nerdctl run`/`image inspect` read it; `ensure_base_in_buildkit_ns` must have run first. |
| `REPO_IMAGE_TAG` | after base-build block | Set to `resolve_repo_image_tag()` output when a Dockerfile exists; empty string otherwise |
| `$LEERIE_STATE_HOST_DIR/.dockerfile-hash` | after base-build block | Stores `<LEERIE_VERSION>:<sha256>` of the last-built Dockerfile; rebuild fires on mismatch or image absence |

**Rebuild triggers** (checked in order): (1) `nerdctl image inspect "$REPO_IMAGE_TAG"` fails, OR (2) `<LEERIE_VERSION>:<sha256>` of the current Dockerfile differs from the stored hash — else skipped. Before a build fires, `ensure_base_in_buildkit_ns` copies the base into the `buildkit` namespace (idempotent) so `FROM $BASE_IMAGE` resolves locally.

**Auto-generation triggers**: when no `.leerie/Dockerfile` exists, the launcher generates one (atomic write via temp file + `mv`) if **any** of: (1) `.leerie/config.toml` declares `setup_packages`, (2) a dependency lockfile exists, or (3) `.leerie/config.toml` declares `language_installs`. A committed Dockerfile always takes precedence.

**`nerdctl run` image arg**: `"${REPO_IMAGE_TAG:-$IMAGE_TAG}"` — falls back to the base image transparently when no repo Dockerfile is present.

### Persistent out-of-repo dependency bake

Dependencies are installed once at image-build time into persistent paths
outside `/work`. The concrete bake targets and environment variables the
Dockerfile emitter and `PROVISION_RECIPE` generator must produce, per
ecosystem:

| Ecosystem | Bake target | Env var(s) | Notes |
|---|---|---|---|
| Python | `/opt/venv` | `VIRTUAL_ENV=/opt/venv`, `PATH` includes `/opt/venv/bin` | Virtual environment activated in the image; workers inherit it. `uv`/`poetry`/`pipenv` are installed INTO `/opt/venv` at build time (via `pip`) rather than relying on their own active-venv env-var detection — verified unreliable for `uv sync` (ignores `VIRTUAL_ENV` without `--active`) and `pipenv` (long-standing bugs). Plain `pip install -r requirements.txt` needs no such workaround. |
| Ruby | `/opt/bundle` | `BUNDLE_PATH=/opt/bundle`, `BUNDLE_APP_CONFIG=/opt/bundle` | Gems installed here; Bundler finds them without per-worktree install |
| Rust | Baked build artifacts + warmed cache | `CARGO_TARGET_DIR`, `CARGO_HOME` (warmed registry) | `cargo build` reuses compiled deps, no network. Requires a discardable dummy `src/main.rs` at build time — `cargo` cannot fetch/build against a manifest-only context with zero source files (see DESIGN §6½). The bake step must build the **debug** profile (no `--release`) — Cargo's cache is profile-keyed, so a `--release` bake shares nothing with the debug-profile `cargo build`/`cargo test` workers actually run. |
| Go | Baked cache + warmed modules | `GOCACHE`, `GOMODCACHE` (warmed) | `go build` network-free, reuses module cache. Requires a discardable dummy `.go` file at build time — `go mod download` alone warms only `GOMODCACHE`, not `GOCACHE` (see DESIGN §6½). |
| Node/pnpm | Warmed content-addressable store | pnpm store path, `frozenStore` set | Residual per-run: `pnpm install --offline --frozen-lockfile` relinks only |

**`PROVISION_RECIPE` contract:** For baked ecosystems (Python/Ruby/Rust/Go),
the recipe injected into implementer/conformer prompts is **informational
only** — the bake already satisfied the dependencies. For Node/pnpm repos,
it carries the residual offline-relink command (`pnpm install --offline
--frozen-lockfile`), run by workers whose subtask needs built dependencies;
a config/docs-only subtask skips it.

`_filter_residual_deps` decides what counts as that residual: a Node entry
qualifies only when its subcommand is in `_NODE_INSTALL_SUBCOMMANDS`
(`install`/`i`/`ci`) **and** it carries `--offline` or `--frozen-lockfile`
as a `shlex` token (OR, since the three managers spell offline-relink
differently; token match, not substring, so a package name containing
`--offline` doesn't count). The subcommand check excludes
`add`/`remove`/`up`/`dlx`, which mutate the dependency set over the
network rather than relinking. An unparseable command is dropped, not
raised.

**`capture_repo_deps` contract:** The `dep_capture` worker always runs at
finalize time, even with a committed `.leerie/Dockerfile` — it writes only
**residual** dependencies (`setup_packages`, `language_installs` entries
for commands that can't be baked). Fully-baked ecosystems typically yield
an empty or minimal capture; Node/pnpm may carry the offline-relink note.
On success it writes the `dep_capture.done` sentinel and sets
`dep_capture_done = True` in `state.json`.

**Dockerfile-emitter gating:** the auto-generated `.leerie/Dockerfile` bake
fires when **any** of: (1) `setup_packages` non-empty, (2) a dependency
lockfile exists (`package-lock.json`, `pnpm-lock.yaml`, `yarn.lock`,
`uv.lock`, `poetry.lock`, `Pipfile.lock`, `Gemfile.lock`, `Cargo.lock`,
`go.mod`+`go.sum`, `composer.lock`, `packages.lock.json`), or (3)
`language_installs` non-empty — so a repo with only language deps and no
apt packages still gets baked (DESIGN §6½). Bare `requirements.txt` (no
lockfile) is deliberately excluded, mirroring `_lockfile_table_entries`: it
goes to the LLM-driven `dep_capture` fallback rather than triggering a
bake on a guessed install command.

### Registry publish path (fly.io / remote Machines)

Fly.io Machines pull an image from a registry rather than using a
locally-built image. The `HOST_UID/HOST_GID` coupling exists only for
local bind-mounts (so files written by the container into `/work` keep
the host user's ownership). Remote Machines have no such bind-mount, so
the Dockerfile's defaults (`ARG HOST_UID=501 / HOST_GID=20`) are used
as-is — no UID matching required.

**Baked source.** The Dockerfile's `COPY` instructions bake
`orchestrator/`, `scripts/`, `prompts/`, and `.claude-plugin/` into the
image at `/opt/leerie-image/`, so a Fly Machine that pulls it can run the
orchestrator with no bind mount. Local runs' `-v
$LEERIE_REPO:/opt/leerie-image:ro` bind mount shadows the baked copy, so
development iteration works without rebuilding.

`scripts/remote/build-push.sh` provides the build-and-push path. By
default it uses Fly's remote builder (no host Docker daemon required):

```bash
# Default: Fly's remote builder builds + pushes (recommended):
./scripts/remote/build-push.sh --app <fly-app-name> --push

# Verify the baked source works inside a Machine:
flyctl machine run registry.fly.io/<fly-app-name>:<VERSION> \
  --app <fly-app-name> \
  -- python3 /opt/leerie-image/orchestrator/leerie.py version
```

Internally, the remote-builder path runs:

```bash
flyctl deploy --build-only --push --remote-only \
  --app <fly-app-name> \
  config <tmp-fly.toml> \
  --dockerfile <DOCKERFILE> \
  [--build-arg KEY=VAL ...] \
  --image-label <VERSION>
```

`<DOCKERFILE>` defaults to `$LEERIE_REPO/Dockerfile`; `--dockerfile <path>`
overrides it (used by `ensure_image()` for per-repo images). `--build-arg
KEY=VAL` is repeatable.

The `<tmp-fly.toml>` is a copy of the repo's `fly.toml` with the `[build]
image = "..."` line stripped — that line tells flyctl "fetch the existing
image", which makes `flyctl deploy --build-only` skip the build step and
fail with "Could not find image"
([flyctl#1686](https://github.com/superfly/flyctl/issues/1686)).

**Opt-in: `--local-build`** (or `LEERIE_LOCAL_BUILD=1`). Builds with
host `nerdctl`/`docker` and pushes from the host. Requires a working
Docker daemon authenticated to `registry.fly.io`. Does NOT work with
nerdctl-in-Colima on macOS — nerdctl reads `~/.docker/config.json`
but cannot resolve `credsStore: desktop` (no access to macOS
Keychain from inside the Lima VM). Documented in INSTALL.md for
completeness; most users should leave it off.

#### Auto-publish on first remote run (`ensure_image()` in the launcher)

A remote run requires the image at `$FLY_IMAGE_TAG` to already exist in
`registry.fly.io`, or `flyctl machine run` fails at provision time with
an unfriendly "manifest unknown" error. `ensure_image()` in the
launcher's `RUNTIME=fly` branch closes that gap, run before
`provision_machine`. Two variants:

**Base image path** (no `.leerie/Dockerfile`):

1. Cache check: if `$XDG_CACHE_HOME/leerie/published-tags.txt` already
   has `$FLY_IMAGE_TAG`, skip everything.
2. Auto-create the Fly app if it doesn't exist. `flyctl apps list
   --json` is parsed for a name match; on miss, `flyctl apps create
   $LEERIE_FLY_APP` is invoked. Idempotent — "already exists" is a
   silent success.
3. Invoke `scripts/remote/build-push.sh --app $LEERIE_FLY_APP --push`.
   `--local-build` is forwarded if `LOCAL_BUILD=true` (set by the
   `--local-build` CLI flag or `LEERIE_LOCAL_BUILD=1` env var).
   build-push.sh handles the actual remote-vs-local mode dispatch.
4. On success, append the tag to the positive cache.

**Per-repo derived image path** (`.leerie/Dockerfile` present):

The relevant bash surface:

| Function / variable | Location in `leerie` | Purpose |
|---|---|---|
| `_set_fly_per_repo_image()` | before `resolve_fly_image_tag()` call in the `RUNTIME=fly` block | Detects `.leerie/Dockerfile`, computes tag, sets `LEERIE_FLY_IMAGE` + context vars; no-op when absent |
| `_FLY_PER_REPO_DOCKERFILE` | module-level (set by `_set_fly_per_repo_image`) | Absolute path to `.leerie/Dockerfile`; empty string when no per-repo Dockerfile |
| `_FLY_BASE_TAG` | module-level (set by `_set_fly_per_repo_image`) | Base Fly tag (`registry.fly.io/$APP:$VERSION`) passed as `BASE_IMAGE` build-arg |

Before `resolve_fly_image_tag()` is called, `_set_fly_per_repo_image()`
detects `.leerie/Dockerfile`, hashes its content (12 hex chars), and sets
`LEERIE_FLY_IMAGE=registry.fly.io/$APP:$VERSION-$HASH`, which
`resolve_fly_image_tag()` returns via the existing override hook.
`ensure_image()` then:

1. Cache check on the per-repo tag — skip if already in
   `published-tags.txt`.
2. Ensure the base image is published: check the cache for the base tag
   (`registry.fly.io/$APP:$VERSION`); on miss, invoke build-push.sh for
   the base image first and cache the result.
3. Build and push the per-repo image: invoke build-push.sh with
   `--dockerfile $USER_REPO/.leerie/Dockerfile --build-arg
   BASE_IMAGE=registry.fly.io/$APP:$VERSION --tag <per-repo-tag>`.
4. Append the per-repo tag to the positive cache.

A rebuild fires automatically when the Dockerfile content or the leerie
version changes, since either changes the hash and causes a cache miss.

Results are cached at `$XDG_CACHE_HOME/leerie/published-tags.txt`
(default `~/.cache/leerie/published-tags.txt`), one line per known-present
`<tag>`. It's a *positive* list only — a missing entry means "probe", not
"absent" — so manual `flyctl image` deletions are self-healing.

Flags:

| Flag | Env | Default | Effect |
|---|---|---|---|
| `--no-auto-publish` | `LEERIE_NO_AUTO_PUBLISH=1` | off | Skip the probe entirely; trust the operator to have published the image. The run still proceeds; if the tag is missing, `provision_machine` fails as before. |

The flag is consumed by the launcher and not forwarded to the
orchestrator (same convention as `--no-runtime-install`).

Key paths inside the container:

- **`/leerie-state/`** — the run-state directory (state.json, logs,
  worktrees, telemetry), bind-mounted from the host (`LEERIE_STATE_HOST_DIR`)
  and persistent across runs. In local mode, worktrees land under
  `/leerie-state/runs/<run-id>/worktrees/`, outside `/work`.
- **`/opt/leerie-image/`** — the orchestrator source tree: a read-only
  bind mount of `$LEERIE_HOME` locally, or the Dockerfile's baked `COPY`
  on Fly Machines. Both resolve identically at runtime.

PID 1 reads from `/opt/leerie-image/` and writes to `/leerie-state/`;
confusing the two either breaks runs or corrupts the install.

### Entrypoint and source mounting

`scripts/container-entry.sh` is exec'd as PID 1, running as **root** (the Dockerfile intentionally omits `USER leerie` — see DESIGN §6 *Memory containment* for why root at PID 1 is required to launch the cgroup broker). Sketch of the relevant final exec:

```sh
#!/bin/sh
set -e
ulimit -c 0
# … CGROUP_ROOT resolution: /sys/fs/cgroup (rootful), or the
# systemd-delegated user slice (rootless — see below) …
# … cgroup slice setup: mkdir + enable controllers on
# $CGROUP_ROOT/leerie.slice …
# Launch the cgroup broker before the privilege drop (worker cgroup
# enrollment/limit-setting can't be done by the dropped-privilege
# orchestrator), telling it which root to operate under:
LEERIE_CGROUP_V2_ROOT="$CGROUP_ROOT" python3 /opt/leerie-image/scripts/cgroup-broker.py &
cd /work
# … /work ownership fix (Fly volume-attach path) …
# … /tmp/.cache ownership fix (Fly rootfs preserves root-owned mise cache) …
exec runuser -u leerie -- \
  env HOME=/home/leerie USER=leerie LOGNAME=leerie \
  python3 /opt/leerie-image/orchestrator/leerie.py "$@"
```

**Rootless containerd.** Under rootless containerd (Linux), rootlesskit maps the host UID to container UID 0. The entrypoint detects this via `/proc/self/uid_map` (non-zero host-start field on the first line → `ROOTLESS=true`) and, when true, also extracts `HOST_UID` (that line's second field). When rootless:

- `chown leerie: /work` and `runuser -u leerie --` are skipped — container "root" IS the host user, so privilege drop would break bind-mount access and chown would reassign to the subuid range.
- `CGROUP_ROOT` is anchored at `/sys/fs/cgroup/user.slice/user-$HOST_UID.slice/user@$HOST_UID.service` instead of the top-level `/sys/fs/cgroup`: the true top level is root-owned (mode 0555), but systemd delegates this subtree to the UID's login session, so any cgroup the UID `mkdir`s underneath inherits ownership on every auto-created interface file (`pids.max`/`memory.max`) — unlike a directory merely `chown`ed after creation. Passed to `cgroup-broker.py` via `LEERIE_CGROUP_V2_ROOT` (default `/sys/fs/cgroup` when unset). The broker runs at the same rootlesskit-mapped identity as the container, which is exactly what `CGROUP_ROOT` is delegated to. Cross-scope worker-PID migration into `leerie.slice` still works because cgroup v2 only requires write access to the destination and nearest common ancestor, not the source. See DESIGN §6 *Rootless exception*.
- Where the delegation doesn't hold (non-systemd rootless init, or a host that doesn't delegate `pids`/`memory` into the per-session slice), the slice-setup writes (`|| true`) and the broker's write-then-read-back check both fail silently, and the fail-closed containment gate stops the run unless `--dangerously-allow-uncapped` is passed.
- On macOS, the launcher unconditionally sets the `rshared` bind-mount — Colima always runs rootful containerd with cgroup v2 and shared propagation. Native rootful Linux gets the same mount. Rootless containerd (gated on the `containerd-rootless/child_pid` sentinel) uses a **plain** bind-mount instead: rootlesskit's `--propagation=rslave` is incompatible with `bind-propagation=rshared`, and only read/write visibility is needed, not mount-event propagation. When cgroup v2 isn't present, the mount is skipped and the fail-closed gate stops the run unless `--dangerously-allow-uncapped` is set.

**User-namespace remap.** Claude Code rejects `--dangerously-skip-permissions` from UID 0. The rootless entrypoint uses `unshare --user --map-user --map-group` to remap outer UID 0 to the `leerie` user in a nested user namespace, so the orchestrator runs as non-root and the flag is accepted. The OCI default seccomp profile blocks `unshare(CLONE_NEWUSER)`, so the launcher passes `--security-opt seccomp=unconfined` for rootless runs (gated on `containerd-rootless/child_pid`). See DESIGN.md §6.

The orchestrator's source lives at `/opt/leerie-image/`. It is present in two ways depending on execution mode:

- **Local runs:** the launcher bind-mounts `$LEERIE_HOME` read-only at `/opt/leerie-image`. Iterating on `orchestrator/leerie.py` does not need an image rebuild — the bind mount shadows the baked copy and the host file is picked up on the next `leerie` invocation.
- **Fly.io Machines (remote):** there is no bind mount. The Dockerfile `COPY` instructions bake `orchestrator/`, `scripts/`, `prompts/`, and `.claude-plugin/` into the image at `/opt/leerie-image/` so the entrypoint resolves without any host-side path. A new leerie version requires rebuilding and pushing the image (see §0.5 "Registry publish path").

### Bind-mount table

The launcher passes the following mounts to `nerdctl run`:

| Host path | Container path | Mode | Purpose |
|---|---|---|---|
| `$(pwd -P)` (user repo) | `/work` | rw | The repo leerie operates on. Git worktrees live here. Writes flow back to the host so `resume` works across container runs. Run state (`.leerie/`) is mounted separately via `/leerie-state` (see below). |
| `$LEERIE_STATE_HOST_DIR` (resolved host state dir) | `/leerie-state` | rw | *Local mode only.* Leerie run state (state.json, runs/, logs/, worktrees/). Mounted at a top-level container path distinct from `/work` so the repo checkout stays pristine — no `.leerie/` dir accumulates inside the project. The orchestrator reads the container path from `LEERIE_STATE_DIR=/leerie-state` (passed as `-e` in the same `nerdctl run` invocation). `LEERIE_STATE_HOST_DIR` is resolved on the host by the launcher before launch; see §2 "Host-side per-repo state directory". |
| `$LEERIE_HOME` (leerie install dir) | `/opt/leerie-image` | ro | *Local mode only.* Orchestrator source + Dockerfile + prompts. Read-only because the container has no business mutating the install. Shadows the baked COPY layer so edits to `orchestrator/leerie.py` take effect without an image rebuild. Absent in registry / fly.io mode — the baked COPY layer is used directly. |
| `$STAGE` (per-run host scratch — same tree seed-auth.sh/ec2-seed-auth.sh tar-pipe to Fly/EC2) | `/opt/leerie-claude-json-src` | **ro** | The per-container copy of `~/.claude.json` (with `projects[]` stripped) lives at `$STAGE/.claude.json`; `$STAGE` is bind-mounted read-only at this staging path — `.claude.json` is never bind-mounted directly onto `/home/leerie/.claude.json`. A shared mount there is a documented `claude-code` corruption race (anthropics/claude-code #28847, #29217, #29395, #40226) and the CLI's atomic rename() write returns `EBUSY` on a bind-mounted single file, forcing a non-atomic truncate fallback with a demonstrated corruption window under concurrent workers. `scripts/container-entry.sh` instead copies it to `/home/leerie/.claude.json` as a real file at container start (root-owned under rootless, `chown`ed to `leerie:` under rootful) — mirroring the tar-copy pattern the remote runtimes use. |
| `$STAGE/.claude` (per-run host scratch) | `/home/leerie/.claude` | rw | Per-container copy of `~/.claude/` with bulky/prior-session/history paths skipped (`history.jsonl`, `projects/`, `sessions/`, `tasks/`, `plans/`, `todos/`, `file-history/`, `paste-cache/`, `shell-snapshots/`, `session-env/`, `telemetry/`, `stats-cache.json`, `debug/`, `downloads/`, `backups/`, `chrome/`, `ralph-state/`, `.last-cleanup`, `settings.json.*`, `plugins/cache/`, `plugins/marketplaces/`). CLI capability dirs (`agents/`, `skills/`, `commands/`, `hooks/`, `plugins/installed_plugins.json` + siblings, `mcp-needs-auth-cache.json`, `settings.json`, `local/`, `statsig/`, `cache/`, `package.json`, `policy-limits.json`) ride along. `plugins/cache/`/`marketplaces/` are rebuilt on the remote for the fly runtime (`scripts/remote/seed-auth.sh` step 4). |
| `_extract_claude_credentials_json` → `$STAGE/.claude/.credentials.json` | `/home/leerie/.claude/.credentials.json` | rw | Resolves which Claude OAuth credential to use, in order: `$CLAUDE_CODE_OAUTH_TOKEN` first when set (synthesized into `{"claudeAiOauth":{"accessToken":…,"scopes":["user:inference"]}}` — `scopes` is mandatory, the CLI rejects a scope-less blob); then Keychain (`security find-generic-password -w`, macOS only); then `$HOME/.claude/.credentials.json`. A container can't refresh a copied token, so the long-lived token wins over file-based sources (DESIGN §6 *Credential strategy*). Keychain/on-disk branches are shape-checked via `_claude_creds_has_oauth_token` (non-empty `claudeAiOauth.accessToken`), guarding an upstream CLI bug (steipete/CodexBar#1844) where Keychain can hold only `{"mcpOAuth": {...}}` with no usable token even while the host CLI works — `claude /login` does not repair this. A failing source is treated as empty and resolution falls through; rejection reason goes to a PID-scoped temp file (`$_CLAUDE_CREDS_REJECT_REASON_FILE`, since the caller reads the helper via `$(...)` subshell). All branches write the same JSON shape at mode 600. When `$CLAUDE_CODE_OAUTH_TOKEN` is set the launcher also forwards it as a container env var unconditionally, since that path survives past the file blob's `expiresAt`. Same helper populates `LEERIE_WORKER_ENV_JSON`'s `LEERIE_CLAUDE_CREDS_B64` for the `chain` arm. **On failure**, with no Bedrock auth mechanism active, the STAGE-assembly block `die()`s immediately rather than running a container doomed to fail the smoke test; the message names the mcpOAuth bug and recommends `claude setup-token` or standard `/login` guidance. Exempted when either Bedrock auth mode is active. |
| `$STAGE/.gitconfig`, `.gitconfig.local`, `.gitignore`, `.gitignore_global`, `.git-credentials`, `.netrc` (per-run host scratch) | `/home/leerie/.<same>` | rw | Per-container copies of each present host `~/.git*` sibling and `~/.netrc`. Worker can `git config --local` / mutate freely without affecting host state. |
| `$STAGE/.config/git` (per-run host scratch) | `/home/leerie/.config/git` | rw | XDG-style git config (`~/.config/git/config`, `~/.config/git/ignore`) copied per-container. |
| `$STAGE/.ssh` (per-run host scratch) | `/home/leerie/.ssh` | rw | Per-container copy of `~/.ssh/` with `agent/`, `S.*`, and `*.sock` excluded — host UNIX sockets aren't reachable from inside the container and `cp -a` on them is pointless. Keys and `known_hosts` ride along so workers can SSH-push if needed. Permissions set to `0700`. |
| `$STAGE/.gnupg` (per-run host scratch) | `/home/leerie/.gnupg` | rw | Per-container copy of `~/.gnupg/` with agent socket files (`S.gpg-agent*`, `S.scdaemon`, `S.keyboxd`) excluded and `use-keyboxd` stripped from `common.conf` (the container cannot reach the host keyboxd daemon; stripping the directive makes gpg fall back to file-based `pubring.kbx` lookup — on keyboxd-only hosts signing keys become unfindable, which is acceptable since commit signing is best-effort). Keyrings + `trustdb.gpg` ride along so workers can `git commit -S` if signing is configured. Permissions set to `0700`. |
| `$STAGE/.aws` (per-run host scratch, **Bedrock SSO/profile mode only**) | `/home/leerie/.aws` | **ro** | Staged when `detect_bedrock_mode()` finds `CLAUDE_CODE_USE_BEDROCK` truthy (`1`/`true`/`yes`/`on`, matching the CLI's `isEnvTruthy`) in any of the three settings files the CLI merges (`~/.claude/settings.json`, `<USER_REPO>/.claude/settings.json`, `<USER_REPO>/.claude/settings.local.json`) — and only when `AWS_BEARER_TOKEN_BEDROCK` (below) is **not** set. The CLI's AWS SDK reads `~/.aws/config` and `~/.aws/sso/cache/*.json` (SSO tokens, ~12h TTL) directly via file I/O — no `aws` binary needed in-container. `~/.aws/cli/cache` is excluded. Mounted read-only since workers never write credentials. `aws sso login` needs an interactive TTY/browser, so `bedrock_preflight()` catches an expired SSO token on the host first and prints the recovery hint. On Fly.io, `$STAGE/.aws/` rides the tar pipe to `seed_auth` automatically. Belt-and-suspenders: the launcher also injects `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE`, `AWS_REGION` as explicit env vars so workers activate Bedrock via `process.env` regardless of `settings.json` handling. The same local block also forwards `ANTHROPIC_DEFAULT_SONNET_MODEL`/`_OPUS_MODEL`/`_HAIKU_MODEL` when set — see the bearer-token row. |
| `AWS_BEARER_TOKEN_BEDROCK` (host env var, **Bedrock bearer-token mode**) | forwarded as `-e`/`child_env` only — **no bind mount** | n/a | Static-bearer-token analogue of `CLAUDE_CODE_OAUTH_TOKEN`; takes precedence over the SSO/profile row when both are present (matches the CLI's own resolution order — verified live, v2.1.220). No `aws` CLI, no SSO session, no `~/.aws` staging — `bedrock_preflight()` is skipped. The launcher forwards the token verbatim, `CLAUDE_CODE_USE_BEDROCK` (defaulting to `1` — confirmed the token alone is a no-op without it), and `AWS_REGION` when set. The same block forwards `ANTHROPIC_DEFAULT_SONNET_MODEL`/`_OPUS_MODEL`/`_HAIKU_MODEL` when set: leerie always invokes `claude -p --model <tier>`, never a raw model ID, and on Bedrock the CLI's alias table can lag the Anthropic-API one by a generation (e.g. `sonnet` resolving to Sonnet 4.5 instead of Sonnet 5) — these are the CLI's documented env vars for repointing an alias. On Fly, every substituted value (bearer token, region, use-bedrock flag, plus `_BEDROCK_PROFILE`/`_BEDROCK_REGION`/host-TZ) is JSON-encoded host-side first, since an opaque bearer token can contain a `"` or `\` that would break out of a raw `"${VAR}"` substitution. |

The four host-auth mounts (`~/.config/gh`, `~/.git-credentials`, `~/.ssh`, `$SSH_AUTH_SOCK`) that earlier leerie versions bind-mounted **no longer exist** — finalize moved to the host (DESIGN §6 *Finalization*), so `git push`/`gh pr create` run with the host's own auth state.

| `~/.cache/leerie/mise-data` | `/home/leerie/.local/share/mise` | rw | Mise's `MISE_DATA_DIR` (per-repo runtime installs, plugins, cache). Lives in the user dir so the resolver checks it first then falls through to the image-baked `MISE_SYSTEM_DATA_DIR=/usr/local/share/mise` for the LTS fallback (DESIGN §6½). Its `shims` subdir is on the image's `ENV PATH` (see §0.5 "Image build") so a worker's own ad-hoc Bash commands can reach a repo-pinned runtime (e.g. Ruby via `.ruby-version`) without an explicit `mise exec --`. |
| `~/.cache/leerie/pnpm-store` | `/home/leerie/.cache/leerie/pnpm-store` | rw | pnpm content-addressable store. Pointed at via `npm_config_store_dir` (the pnpm-respected env var; `PNPM_STORE_PATH` doesn't exist and would be silently ignored). Safe for concurrent installs across worktrees (pnpm/discussions#10702). |
| `~/.cache/leerie/pip` | `/home/leerie/.cache/leerie/pip` | rw | pip HTTP + wheels cache. Each worker that needs Python deps runs `pip install` / `uv sync` itself in its own worktree against this shared cache; after the first install of a package the cache is warm and subsequent workers' installs are fast. Wheel-build race pypa/pip#9034 is still a theoretical concern but in practice rare given leerie's small worker concurrency (DESIGN §6½). |
| `~/.cache/leerie/go-mod` | `/home/leerie/.cache/leerie/go-mod` | rw | `GOMODCACHE`. Concurrent-safe via per-module-version `flock` in `cmd/go/internal/modfetch`. |
| `~/.cache/leerie/cargo` | `/home/leerie/.cache/leerie/cargo` | rw | Whole `CARGO_HOME` (registry + bin + config.lock). Mounting only `registry/` breaks `config.lock` (cargo#11376). Concurrent-safe via cargo's documented flock semantics. |
| `~/.cache/leerie/corepack` | `/home/leerie/.cache/leerie/corepack` | rw | `COREPACK_HOME`. Without this, corepack inherits `XDG_CACHE_HOME=/tmp/.cache` and tries to mkdir `/tmp/.cache/node/corepack/v1`, which fails under rootless UID remapping. Concurrent-safe: corepack downloads tarballs via atomic rename; the cache is read-mostly after first install. |
| `~/.cache/leerie/bundle` | `/home/leerie/.cache/leerie/bundle` | rw | `BUNDLE_PATH` for Bundler (Ruby gems). `BUNDLE_CACHE_ALL=1` instructs Bundler to cache all gems (including git-sourced ones) so each `bundle install` reuses downloaded gems across worktrees and runs. |
| Each `--inspect-dir` path (translated) | `/inspect/<basename>` | ro | See below. |

### `LEERIE_*` env-var forwarding (local `nerdctl run`)

The orchestrator runs **inside** the container and reads every override from `os.environ` — which only inherits what `nerdctl run` forwards. The launcher forwards **every `LEERIE_*` var in its environment except a deny-list** of launcher/host-only vars (the `_leerie_env_denylist` array in the `nerdctl run` block). A `for` loop over `compgen -v | grep '^LEERIE_'` appends a bare `-e "$name"` (host value passed through) for each non-deny-listed var with a non-empty value. Empty/unset vars are skipped.

**`LEERIE_STATE_HOST_DIR_DISPLAY` — a deliberate, narrow exception.** The orchestrator sees the state root bind-mounted at `/leerie-state`, so a bare `die()` naming `<state-root>/runs/<id>/state.json` would print a path the operator cannot open on the host. The launcher therefore forwards the *host* side of that mount explicitly, as `-e "LEERIE_STATE_HOST_DIR_DISPLAY=${LEERIE_STATE_HOST_DIR:-}"`, and `_operator_path()` uses it to rewrite the prefix in operator-facing text.

The `_DISPLAY` suffix is load-bearing. `LEERIE_STATE_HOST_DIR` itself stays on the deny-list, and must: a host path is meaningless *as a path* inside the container, and nothing may open this value. It may only be printed. The separate name is what keeps that restriction legible at the use site, and `tests/test_operator_path_translation.py` pins both halves — that the launcher forwards the display copy, and that the un-suffixed original remains denied.

Deny-list = forward-all-minus-known-host-only, not an allow-list, so dynamic per-worker names (`LEERIE_MODEL_<WORKER>`, `LEERIE_EFFORT_<WORKER>`, built at runtime from `f"{MODEL_ENV}_{worker.upper()}"`) forward automatically and a future override can't be stranded at the boundary. Deny-listed: `LEERIE_STATE_DIR`/`LEERIE_INSPECT_DIRS` (remapped to container-internal values), `LEERIE_HOME`/`LEERIE_REPO`/`LEERIE_STATE_HOST_DIR`/`LEERIE_SELF_CMD` (self-location + host paths), `LEERIE_NO_PUSH` (orchestrator always gets `--no-push`; host pushes), `LEERIE_RUNTIME` (decided launcher-side), and the Fly/EC2/remote/chain/wave machinery (EC2 instance-lifecycle vars, `LEERIE_FLY_APP`/`_FLY_IMAGE`/`_MACHINE_ID`). `tests/test_launcher_env_forwarding.py` extracts the loop verbatim, with a coupling guard asserting no orchestrator-read override is deny-listed except four justified exceptions (`LEERIE_STATE_DIR`, `LEERIE_INSPECT_DIRS`, `LEERIE_NO_PUSH`, `LEERIE_RUNTIME`). On Fly the equivalent forwarding is via `child_env` in the detached-launch heredoc, not this loop.

**`USER_REPO` (non-`LEERIE_*`, both runtimes).** `log()` renders its `[leerie] [<repo>]` prefix from `Path(os.environ.get("USER_REPO") or os.getcwd()).name`. The container's cwd is `/work`, so without an injected `USER_REPO` the fallback fires and every line reads `[leerie] [work]`. Both runtimes therefore inject it, each outside the `LEERIE_*` loop (the name does not match `^LEERIE_`):

- **Local:** an explicit `-e "USER_REPO=$(basename "$USER_REPO")"` in the `_run_argv` array, next to the other explicit `-e` lines.
- **Fly:** `child_env["USER_REPO"] = "$(basename "$USER_REPO")"` in the detached-launch heredoc (reproduced verbatim under §"Worker auth + config seeding", `seed-auth.sh`).

Both pass the **basename**, never the host path: `$USER_REPO` is a host absolute path that does not resolve inside the container (the repo is at `/work`), and `Path(x).name` is identity for a bare name. `log()` is the only in-container reader, so nothing treats the value as a path. The two mechanisms are independent — a change to one that is not mirrored in the other regresses that runtime to `[work]`.

### `--inspect-dir` path translation

Inspect dirs (`--add-dir` forwarded to `claude -p` for cross-repo context) come from CLI flags, the `LEERIE_INSPECT_DIRS` env var, or `leerie.toml`'s `inspect_dirs` key. They are *host* paths. The launcher:

1. Collects all three sources before any container is started.
2. For each host path: resolves it on the host (`cd -P "$path" && pwd`, so symlinks and `~` are expanded), bind-mounts it read-only at `/inspect/<basename>` inside the container, and rewrites the corresponding CLI flag to point at the in-container path.
3. Passes only the rewritten flags into the container, and clears `LEERIE_INSPECT_DIRS` in the container env so the in-container resolver doesn't see any host paths.

This honors the orchestrator's precedence rules in `resolve_inspect_dirs` (CLI > env > TOML) by emitting only CLI args — the env and TOML pre-passes in the launcher synthesize CLI flags.

A host path *inside* `$USER_REPO` (already visible at `/work/<subpath>`) collides with the launcher's `/inspect/<basename>` target. The launcher warns and skips the redundant mount.

#### Remote runtime (Fly.io) transport

Under `--runtime fly`, the launcher additionally ships each `--inspect-dir` host path to `/inspect/<basename>` on the Fly machine via `scripts/remote/seed-repo.sh:seed_inspect_dirs`. The rewritten `--inspect-dir /inspect/<basename>` CLI flag already carries the in-machine view to the orchestrator via `REWRITTEN_ARGS`; this step makes the path actually exist on the machine's filesystem.

Per inspect dir, transport is two-phase, mirroring the `seed_repo_clone` + `seed_repo_dirty` strategy used for `/work`:

- **Git repos** — `git bundle create - --all` packs every reachable object into one stream, piped via `flyctl ssh console` into `/tmp/leerie-inspect-<base>.bundle` (submodules likewise into `/tmp/leerie-inspect-<base>-subs/`). The machine `git clone`s from the local bundle into `/inspect/<base>` (with `protocol.file.allow=always` for the submodule update; CVE-2022-39253 mitigation). A second pass (`_seed_one_inspect_dir_dirty`) rsyncs the uncommitted-edit delta on top via `fly_rsync_wrapper`, so workers see in-flight changes just as for the main repo.
- **Non-git directories** — fall back to plain `rsync -a -H` via `fly_rsync_wrapper` (kept for the no-`.git/` case).

Bundling beats plain rsync over `flyctl ssh console` for non-trivial trees — a working tree with `node_modules`/build output can hang indefinitely, while the source-only bundle is orders of magnitude smaller and ships in under a second.

Resume probe: `seed_inspect_dirs` runs one `flyctl ssh console -C "test -d /inspect/<base>/.git"` per dir first. Already-seeded dirs skip the bundle and only refresh the dirty delta — a few seconds, not minutes. New dirs added at `resume` time take the full fresh path.

Each `/inspect/<basename>` is chowned `leerie:leerie` after every transport phase, same ownership-handover pattern as `/work`.

The launcher serializes its `INSPECT_HOST_TARGETS` bash array (parallel to `INSPECT_MOUNTS`, populated by `collect_inspect_path` for every out-of-repo inspect dir) into `LEERIE_INSPECT_HOST_TARGETS` before each call. In-repo inspect dirs (skip-redundant-mount branch) arrive via `seed_repo` at `/work/<subpath>` instead.

Called at two points inside the `--runtime fly` block:

1. **Fresh provision** — after `seed_repo` lands `/work`, before the detached orchestrator launches.
2. **Resume / re-seed** — after `re_seed` lands the dirty delta, on every resume. This honors the documented property that inspect dirs are re-resolved fresh on every run including `resume` (§2 *Inspect directories*); the user can add `--inspect-dir <path>` at resume time and expect it to land on the machine.

A failure of `seed_inspect_dirs` is fatal — the run aborts before the orchestrator launches, in the same class as `seed_repo` / `seed_auth` failures. Workers cannot do their job with `--add-dir` flags pointing at non-existent paths, so silent continuation would yield wrong classifier / planner output.

Read-only contract: inspect-bucket workers only `Read`/`Grep`/`Glob` inspect dirs (DESIGN §12). No rsync `--delete` or two-way sync is used.

Inspect dirs are **not** `git clone`d *from origin* on the machine because the machine deliberately holds no GitHub credentials (DESIGN §6 *Finalization*). The bundle approach above ships the host's local git state directly — no remote auth ever needed in-machine.

Same rsync-vs-tar rationale as `seed_repo_dirty` (applies to the fallback path and the dirty-delta phase): macOS BSD `tar -c` normalizes filenames NFC → NFD (libarchive); rsync preserves filename bytes verbatim. Bundles sidestep the problem entirely — filenames travel as pack-format binary objects, materialized natively by the receiving git.

### Browser-based testing

Chromium and its matching chromedriver are baked into the image, so workers needing a real browser have one with no runtime installation. The Selenium cache directory (`/home/leerie/.cache/selenium`) is pre-created (root-owned at build time, chowned to `leerie` at runtime on the rootful path) so Selenium Manager cache writes succeed if it ever runs.

**Container flags — baked in, no project changes required.** Three flags run Chromium in a rootless container:

- `--no-sandbox` — disables Chrome's user-namespace sandbox, unavailable in unprivileged containers.
- `--disable-setuid-sandbox` — suppresses the SUID sandbox-helper lookup. Without this, Chrome finds `/usr/lib/chromium/chrome-sandbox` and tries to exec it; SUID is stripped in rootless containers, so the exec fails and Chrome crashes with `SIGTRAP` before fully initializing — *even when `--no-sandbox` is present*. This is the most common silent failure mode.
- `--disable-dev-shm-usage` — redirects shared-memory to `/tmp`; `/dev/shm` is typically 64 MB in containers and Chrome's renderer can exceed it.

These are written to `/etc/chromium.d/leerie-container-flags` at image build time, so `/usr/bin/chromium` picks them up automatically. **No project-level Chrome flag configuration is required.** Projects that construct a `ChromeOptions`/`Options` object and add these flags explicitly are fine (idempotent); projects that don't touch Chrome options also work, since the wrapper sets them globally.

### macOS-specific: Colima auto-share scope

Colima auto-shares only paths under `/Users/$USER` into the VM by default. A bind mount of a path outside that range will silently appear empty inside the container. The launcher warns at preflight when `$USER_REPO` or any `--inspect-dir` falls outside, and points the user at `~/.colima/default/colima.yaml`'s `mounts:` section as the workaround.

VirtioFS is the mount type leerie documents (`colima start --runtime containerd --mount-type virtiofs`) — it's the fastest option and gives correct UID semantics for bind mounts.

### Logging, signal flow, and TTY adaptation

**`log()` and `die()` never raise.** Both wrap their `print` in `contextlib.suppress(OSError, ValueError)`. This matters because on the remote runtime `sys.stdout` **is** `<run_dir>/orchestrator.log` (fd1 redirected there; `_install_run_log_tee` skips its guarded tee in that case), so `print(..., flush=True)` writes to the state filesystem and can raise `ENOSPC` when full. Every terminating arm in `main()` logs *before* assigning `exit_code`, so an unguarded write failure would turn a resumable pause (`ContextOverflow`, `TerminalAuthFailure`, `RateLimitedExit`, `KeyboardInterrupt`, `InterruptedBySignal`) into an exit-1 traceback. For `die()` the exit **code** is load-bearing — an unwritable stderr must not convert a deliberate coded exit into an unhandled `OSError`.

`OSError`/`ValueError` only, never `BaseException`: a `KeyboardInterrupt` arriving mid-write must still propagate. `_save_state_best_effort` uses the same "not a real interrupt" tuple for the same reason, and `_TeeStream`'s log-copy guard carries the same two exceptions, covering its own `_orig.write`/`_orig.flush`. A failed write is deliberately lost silently rather than losing the whole run.

The launcher invokes `nerdctl run --rm $TTY_FLAGS …` where `TTY_FLAGS` is chosen by a one-line `[ -t 0 ]` test:

```sh
TTY_FLAGS="-i"
[ -t 0 ] && TTY_FLAGS="-it"
```

That single test is **the entire branch** between terminal mode and plugin mode. Everything else (mounts, image, env, entrypoint, signal handling) is identical.

**Terminal mode (`-it`)**:

- `-i` + `-t` give the orchestrator a controlling TTY → its existing `log(...)` and stream-event summarizers write directly to the user's terminal with no aggregation layer.
- `--clarify` prompts use `input()` interactively — the user types answers at the host terminal, characters flow through the pty to Python inside the container.
- Ctrl-C in the host terminal sends SIGINT to the container's PID 1 (the orchestrator). Python's `KeyboardInterrupt` fires, the existing `except KeyboardInterrupt` handler runs the worktree-only cleanup, the orchestrator exits — and the kernel reaps everything else in the PID namespace.

**Plugin mode (`-i` only)**:

- Claude Code's Bash tool spawns the launcher without a TTY on stdin. `[ -t 0 ]` returns false; the launcher passes only `-i`, no pty allocated inside the container.
- Inside the container, `sys.stdin.isatty()` returns False. The orchestrator's `gather_answers()` and the mid-execution clarification path (`_surface_clarification()`) both detect this and trigger the canonical no-TTY signal: write `<state-root>/runs/<run-id>/pending-questions.json` to disk and `sys.exit(EXIT_NEEDS_ANSWERS)` (= 10).
- `<state-root>/runs/<run-id>/pending-questions.json` is visible on the host because `/leerie-state` is bind-mounted from `LEERIE_STATE_HOST_DIR`. The plugin agent at `commands/leerie.md` reads it directly, asks the user via the chat UI, writes the matching `<state-root>/answers.json`, and re-runs the container with `--answers <state-root>/answers.json` and `resume`.
- Stdout/stderr stream back through the Bash tool to the agent's chat session — possibly in 30s-ish chunks per the harness's buffering, which is acceptable for the streaming UX.
- The kernel teardown guarantee applies the same way as in terminal mode: when the orchestrator exits (clean exit, exit 10, or any signal the harness sends), PID 1 dies and the namespace is reaped.

Common to both modes:

- **Orchestrator stdout/stderr are persisted to `<state-root>/runs/<run-id>/orchestrator.log`.** Once `main()` has the run dir, `_install_run_log_tee()` wraps `sys.stdout`/`sys.stderr` with a `_TeeStream` that mirrors every write to that file (flushed per write, so a crash still leaves a complete trail). This is the local-runtime counterpart of the Fly/EC2 path's `Popen(stdout=log_f)` → `orchestrator.log`: on those runtimes fd1 already *is* that file, so `_install_run_log_tee` no-ops there (`_stdout_already_targets` inode check prevents double-writing). It exists because local otherwise keeps no state-dir copy of the orchestrator's phase logs — an abnormal exit or lost pipe erased them (run 26fd0fa5's `leerie.log` was 0 bytes, undiagnosable post-hoc). Best-effort: a log-open failure logs and proceeds terminal-only; a mid-run write failure is swallowed. Per-worker `<state-root>/logs/<sid>.log` files are unaffected.
- **`die()` announces the run id on every terminal exit path.** `State.__init__` calls `_set_current_run_id(run_id)`, stashing it in module-level `_CURRENT_RUN_ID` — the only channel available to `die()`, since most call sites run at module scope with no `State` in hand. Once constructed, every subsequent `die("...")` appends `(run <id>)`; a `die()` before any `State` exists prints unaffected. Pinned in `tests/test_run_id_terminal_emit.py`. The paired `log(f"run id: …")` is the first statement of `_run_phases`, unconditional, so it fires on fresh runs and every resume.
- `--rm` removes the stopped container automatically so they don't accumulate. Worktrees and state on the bind-mounted host filesystem survive for `resume`.
- `--name leerie-<ts>-<pid>` makes `nerdctl ps` legible and `nerdctl logs <name>` targetable for the rare diagnostic case.
- `--label leerie.launcher_pid=<pid>` records the owning launcher's PID (`$$`) on the container. The stale-container reaper (below) reads it back via `nerdctl inspect` to test owner liveness without parsing the `--name` suffix. `<pid>` is the same `$$` used in `--name`.
- Aggregate memory cap: **not a `nerdctl run` flag.** `container-entry.sh` (PID 1) writes `leerie.slice/memory.max` (the parent cgroup of every per-worker cgroup), derived from VM `MemTotal` read from `/proc/meminfo` (portable across Colima and native Linux; the host launcher cannot read the VM's MemTotal on macOS, so a `nerdctl --memory` flag is not used). This bounds the sum across all concurrent workers, distinct from the per-worker cgroup caps in §6 (*Memory containment*) which bound each worker individually. See DESIGN §6 *container boundary's hidden precondition* and the caps table in §6.

**Abnormal-exit cleanup (traps + reaper).** The container boundary guarantees namespace teardown *when PID 1 exits*, but a host CLI that dies without forwarding a stop signal (OOM-killed `nerdctl` client, uncatchable SIGKILL) leaves the container orphaned and holding the run-dir flock — every later `resume` then exits `EXIT_LOCKED=75` (DESIGN §6). Two launcher mechanisms close this:

- **Kill-on-exit trap.** INT/TERM traps on the local run path `nerdctl kill` the container (via its run-id, which equals the container ID) before the launcher exits, and the EXIT trap does the same *before* removing the cidfile. Reliable for Ctrl-C/SIGTERM; does NOT help under SIGKILL/OOM — that's the reaper's job.
- **Stale-container reaper.** On the local `resume` path, before spawning, the launcher looks up any container whose ID equals the resume run-id, and if it's still running but its owning launcher (`leerie.launcher_pid` label) is dead, kills it first — letting `resume` self-heal the orphaned-flock wedge instead of returning 75.
- **Decoupled output streaming (piped mode only).** In piped mode (`leerie … | tee log`, stdout not a TTY), the launcher does NOT let `nerdctl run` write straight to its stdout pipe — Colima's persistent SSH ControlMaster can retain a copy of the pipe write-end on an abnormal exit, so `tee` never gets EOF and the launcher hangs (orphaning the container). Instead it redirects `nerdctl run > "$_run_log" 2>&1` (a regular file) and streams it via a background `tail -n +1 -f`. `_reap_tail` (called after the run and from all three EXIT/INT/TERM traps) briefly sleeps so `tail` drains the final write, then kills it and removes the log. The `nerdctl` argv is built once into a `_run_argv` array and invoked in two spelled-out branches (redirected vs. direct), since bash can't build a redirection through variable expansion. Interactive `-it` runs skip the decouple entirely. See DESIGN §6 *Launcher hang on abnormal container exit*.

The plugin mode flow above is exactly what `commands/leerie.md` already documents — it works through the container with zero new mechanism because the state dir lives on the bind-mounted `/leerie-state` host filesystem.

### What does NOT change in the orchestrator

`orchestrator/leerie.py` is unmodified by this design. It runs as PID 1 inside the container; everything it currently does — the asyncio event loop, the signal handlers, `claude -p` spawn via `asyncio.create_subprocess_exec`, the per-worker `_terminate_proc_tree` and `_DescendantTracker` (kept as the fast happy path for clean exits — see DESIGN §6), worktree management, telemetry — works unchanged. Container/process isolation is the launcher's concern, not the orchestrator's.

Maps to `DESIGN.md`: §6 *Cleanup on abnormal exit / Worker subtree termination*.

---

## 1. Repository layout

```
leerie/
├── .claude-plugin/plugin.json     plugin manifest
├── .claude-plugin/marketplace.json single-plugin marketplace manifest (Claude Code `/plugin marketplace add` entry point)
├── leerie                        executable entry-point wrapper (chmod +x); portable bash; runtime preflight + nerdctl run (DESIGN §6 / §0.5)
├── Dockerfile                  container image recipe; built locally on first run, tagged `leerie:<VERSION>` (§0.5)
├── fly.toml                    Fly.io Machine config — app, image, vm sizing (4 cpu / 8 GB midpoint), zero warm-pool (min_machines_running=0). See §0.5.
├── orchestrator/leerie.py        the orchestrator — all control flow (chmod +x)
├── prompts/
│   ├── _clarification_filter.md   shared include (codebase→research→ask filter); inlined by classifier.md / implementer.md via _load_prompt's {{include: …}} expansion
│   ├── classifier.md              Phase 1 worker system prompt
│   ├── planner.md                 Phase 2 worker system prompt
│   ├── reconciler.md              Phase 2½ worker — resolve cross-domain capability-tag drift between planners
│   ├── provision.md               §6½ LLM-fallback install-recipe worker
│   ├── implementer.md             Phase 5 implementer worker system prompt
│   ├── conformer.md               Phase 5 post-work conformance worker (DESIGN §9)
│   ├── integrator.md              conflict-resolution worker system prompt
│   ├── rebaser.md                 finalize-time rebase-onto-base worker (DESIGN §6 "Rebase-onto-base before push"; scoped, fully-agentic §12 exception)
│   ├── pr_writer.md               Phase 6 PR title + body author worker
│   ├── patch_generator.md         post-run self-heal worker — proposes minimal system-prompt patches against failing call_types
│   └── judge.md                   LLM judge worker — 3-dimensional rubric for reviewing captured call records
├── scripts/
│   ├── setup-run.sh               create per-run branch + worktree (idempotent)
│   ├── new-worktree.sh            create/reuse a per-subtask worktree (per-run scoped)
│   ├── worktree-lib.sh            prune_leerie_worktrees(): a SCOPED replacement for `git worktree prune`, sourced by setup-run.sh, new-worktree.sh and cleanup.sh
│   ├── integrate.sh               merge a subtask branch into the per-run branch
│   ├── finalize.sh                verify the run branch exists and is non-empty; ready for push
│   ├── host-finalize.sh           host-side push + PR creation block; sourced by the local-runtime post-run path in leerie, decide_teardown's Fly clean-exit branch, `leerie finalize <run-id>` (§7 Host-side finalize), and the launcher's host preflight, for host_prepush_preflight alone
│   ├── cgroup-broker.py           cgroup broker, runs at the slice-owning identity (create/enroll/destroy over a Unix socket; v1+v2); the dropped-privilege orchestrator drives it
│   ├── verify-strict-schemas.py   maintainer tool: sends every hardened SCHEMAS entry to the real API and reports which compile under strict mode (live creds; outside pytest's testpaths)
│   ├── measure/
│   │   └── worker_durations.py  maintainer tool: derives the per-worker-type duration distribution from a state root's calls.ndjson, feeding TIMEOUT_DEFAULT_PER_WORKER (writes tests/fixtures/worker_duration/summary.json; outside pytest's testpaths)
│   ├── cleanup.sh                 remove worktrees / branches (default: scoped to one run)
│   ├── container-entry.sh         container PID 1 (root rootful / mapped-UID rootless): create leerie.slice + launch cgroup broker + cd /work + drop to leerie via runuser (rootful)
│   ├── install.sh                 one-command installer (curl | bash); preflight git/curl + auto-install claude + runtime install (colima / rootless containerd) + clones + symlinks
│   ├── runtime-install.sh         per-OS auto-install of the container runtime (Colima on macOS; rootless containerd stack on Debian/Ubuntu — Fedora/Arch: docs hint). Sourced by install.sh and the launcher.
│   └── remote/
│       ├── _log.sh                shared remote_log() helper (timestamped, repo-tagged stderr) sourced by every other scripts/remote/*.sh file
│       ├── build-push.sh          build and push a self-contained image for Fly.io Machines; the baked /opt/leerie-image/ lets the image run without a bind mount (§0.5 "Registry publish path")
│       ├── provision.sh           Fly Machine lifecycle (sourced by launcher RUNTIME=fly branch); provision_machine() create→started→trap; stop_machine(); destroy_machine(); decide_teardown() classifies exit-rc and routes to stop (pause-on-failure) or destroy
│       ├── lib.sh                 shared bash helpers (_extract_flyctl_remote_rc stderr rc-parse; update_run_json atomic merge; iso_now; render_tail_wrapper; tail_with_optional_autofinalize); sourced by provision.sh, resume-machine.sh, and re-seed.sh
│       ├── resume-machine.sh      Resume helper for paused remote runs (DESIGN §6 *Remote pause-on-failure*); resume_machine() flyctl machine start + wait_for_started + clear paused_at sentinels
│       ├── re-seed.sh               Mid-run re-rsync (Phase 4) — wakes paused machine, runs safety check, calls seed_repo_dirty. Used by `leerie re-seed <run-id>` and auto on `resume`
│       ├── seed-auth.sh           Worker auth + config seeding (sourced by launcher after provision_machine() returns); seed_auth() tar-pipes ~/.claude.json + ~/.claude/ (minus .claude/local) + git identity to /home/leerie/ via `flyctl ssh console -C "tar -xC ..."`, then pre-warms `claude --version` for orchestrator preflight
│       ├── seed-repo.sh           Two-phase bundle + delta repo seeding (sourced by launcher after provision); seed_repo(): git bundle parent + submodules piped via ssh-console → machine clones from bundles on disk, then rsync's dirty delta + .claude/ — no in-machine git clone
│       ├── collect-subtrees.sh     Subtree collection (sourced by `leerie finalize`); collect_subtrees_remote(): SSHes a bash payload that runs setup-run.sh + integrate.sh for un-merged subtask branches on the machine; conflicts are skipped and reported via sentinels
│       └── fetch-branch.sh        Post-run stream-back (sourced by decide_teardown BEFORE destroy_machine on clean exit, and by `leerie finalize`); fetch_branch(): git bundle pipe + state tar-pipe → host repo
├── commands/leerie.md            thin plugin skill — launches the orchestrator
├── skills/
│   ├── judge-llm-batch/SKILL.md  post-run judge skill — scores a batch of captured LLM calls against a 3-dimensional accuracy rubric
│   └── llm-self-heal/SKILL.md    post-run self-heal skill — autonomous loop that proposes and measures prompt patches for failing call_types; uses judge verdicts as the signal
├── chain/                         Laptop-side chain helpers (DESIGN §19). A chain is N parallel single-run `--runtime fly` invocations per wave, sequenced by the launcher's `chain` arm. The laptop drives everything; no Fly coordinator machine.
│   ├── __init__.py                exports __version__ = "0.1.0"
│   ├── _log.py                    log()/die() helpers — shared with git_ops.
│   └── git_ops.py                 synth_merge_branches (used between waves) + create_stage_branch.
├── docs/DESIGN.md                 the theory (architecture and rationale)
├── docs/IMPLEMENTATION.md         this document
├── tests/                         pytest suite (see §10)
├── pytest.ini                     pytest configuration
└── README.md                      top-level user-facing readme
```

Maps to `DESIGN.md`: §3 (architecture / phases), §2 (why a program, not a skill).

---

## 2. Installation and usage

```bash
# From the root of the target git repository:
leerie "Fix the login timeout bug and add a regression test"

# Or pass a path to a .txt / .md file whose contents are the task — useful
# for multi-paragraph briefs that are awkward to quote on the shell:
leerie path/to/task.md

# Resume an interrupted run. Auto-picks if exactly one in-flight run exists;
# pass the run-id otherwise (see `leerie list`).
leerie resume
leerie resume bugfix-login-timeout-bug-b81e90

# List in-flight and completed runs in this repository:
leerie list

# Skip the default push + PR at finalize (run completes with the run branch
# local-only; the working branch is unchanged):
leerie "task" --no-push
export LEERIE_NO_PUSH=1

# Route to remote execution (e.g. Fly.io) instead of local nerdctl run:
leerie "task" --runtime fly
export LEERIE_RUNTIME=fly
# Or commit to leerie.toml for a per-repo default:
#   runtime = fly

# Skip pre-push hooks at finalize (the user's explicit override; defaults off).
# Affects only the final `git push`; worker `git commit` operations inside
# worktrees continue to run all hooks normally.
leerie "task" --no-verify

# Opt into clarification (DESIGN §11). Without --clarify (the default),
# the classifier's intent questions are filtered and dropped — the
# implementer makes a best-effort decision documented in its notes.
# Pass --clarify to surface the surviving questions to the user
# (interactively if a TTY, otherwise via pending-questions.json).
leerie "task" --clarify
leerie "task" --answers answers.json     # pre-supply clarification answers

# Caps, confidence rounds, verbosity — see "## 2½. Configuration reference"
# for the full flag/env/toml table (max-workers, max-parallel,
# confidence-rounds, verbosity levels, source-of-truth, state-dir, model
# selection incl. per-worker overrides, judge/heal dirs and models,
# heal-loop convergence knobs, LEERIE_WORKER_DEBUG).

# Run post-run skill phases against an existing run's captured LLM calls.
# --phase judge scores every call in calls.ndjson with the 3-dim judge
# rubric and writes verdicts to <run-dir>/<judge-dir>/; --phase heal reads
# the judge index for failing call_types and self-heals each (running
# judge first if no index exists). --run-id selects a run; omitted,
# auto-picks the most recent resumable one.
leerie --phase judge --run-id bugfix-login-timeout-bug-b81e90
leerie --phase heal --heal-max-rounds 5 --heal-success-threshold 0.8

# Read-only per-call_type token/cost/latency/failure + memory-peak report;
# exits without orchestrating. Omit the run id to auto-pick the sole run.
leerie --report bugfix-login-timeout-bug-b81e90

# Recommended backstop for worker auto-compaction
# (Claude Code CLI variable — not consumed by leerie itself):
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70

# Chain verbs: submit, inspect, pause, and destroy multi-run chains. A
# chain is N parallel single-run `--runtime fly` invocations per wave,
# with synth-merge between waves (DESIGN §19). The laptop is the
# sequencer; no Fly coordinator machine. Each --wave flag is one
# sequential wave (comma-separated prompt-file paths); waves run in
# order, runs within a wave run in parallel, against $USER_REPO directly.
leerie chain \
  --wave prompts/fetch.txt,prompts/lint.txt \
  --wave prompts/publish.txt

# ID-dispatched verbs (UUID → chain scope; Fly machine id → run scope).
# Chain-scope verbs iterate $LEERIE_STATE_HOST_DIR/runs/*/run.json
# filtered by chain_id, dispatching the existing single-run verb per
# discovered run.
leerie status   <chain-id>        # render per-run states from run.json
leerie attach   <chain-id>        # poll run.json files every 5s
leerie stop     <chain-id>        # pause every running chain run
leerie kill     <chain-id>        # destroy every chain run's machine
leerie resume   <chain-id>        # resume paused + list running chain runs
leerie finalize <chain-id>        # push + open PR for every unpushed run
leerie list chains               # group runs by chain_id

# The five deprecated dash-prefixed chain-verb aliases (submit / status /
# kill / attach, plus the separate --list-chains flag) are hard-removed —
# no shim, no back-compat. Use the bare verbs above.
```

Requirements: the `claude` CLI on `PATH` and logged in interactively (no API
key — subscription auth); `git`; a git repository with `user.email` and
`user.name` configured; a container runtime (colima on macOS, nerdctl +
containerd on Linux — see `docs/INSTALL.md`). Python is provisioned inside
the container by the image (Debian 13's `python3` 3.13); the host does not
need Python. The launcher's `version` fast path returns without starting
a container.

Via the plugin skill, from inside Claude Code (after
`/plugin marketplace add enricai/leerie` and
`/plugin install leerie@enricai-leerie` — see §0):

```
/leerie <task>
```

### Source-of-truth preference

For feature work, leerie needs to know whether to draw conventions from the
codebase, from online research, or from both (codebase first; research as
fallback). Resolution order (highest priority first):

1. **`--source-of-truth`** CLI flag, values `codebase` | `research` | `both`.
   Argparse rejects anything else before the orchestrator runs.

2. **`LEERIE_SOURCE_OF_TRUTH`** environment variable, same value set.

3. **`leerie.toml` at the repo root** (committed, so the preference travels
   with the repo). Plain `key=value` syntax:

   ```
   source_of_truth = codebase
   ```

4. **Default `both`.** When unset, leerie runs feature tasks with
   `source_of_truth = both` — codebase patterns first, with researched
   best-practice standards as a fallback where the codebase is insufficient.
   The preference is never surfaced as an interactive question; setting it
   explicitly (CLI, env, or file) overrides the default.

An invalid value in env or file is rejected at startup via `die()` — bad
config is caught before any worker spawns.

> The CLI/env > file order reflects that the CLI flag and env var are
> session-scoped knobs (a user reaching for them is making a one-off
> override), while `leerie.toml` is the committed default for the repo.

### Clarification preference

By default leerie runs without surfacing intent questions to the user
(DESIGN §11). The classifier still runs the codebase→research filter and
the implementer still applies it before any mid-execution decision —
"no questions" never means "skip the rigor." Pass `--clarify` to opt
into surfacing the surviving questions. Resolution order (highest
priority first):

1. **`--clarify`** CLI flag (action=`store_true`).
2. **`LEERIE_CLARIFY`** environment variable (boolean, parsed by
   `_parse_bool_envtoml`: 1/0, true/false, yes/no, on/off).
3. **`leerie.toml` at the repo root** with `clarify = true`.
4. **Default `False`.** No questions are surfaced; the implementer
   makes a best-effort decision and documents it in
   `investigation_notes`.

An invalid value in env or file is rejected at startup via `die()` —
same shape as `--source-of-truth` resolution.

### Permission override (dangerous)

Judgment workers (`PLANNING_WORKER_TYPES`) run in a **disposable detached
worktree** (`_judgment_cwd()`, created by `scripts/planning-worktree.sh`) with
a narrow Bash allowlist (`INSPECT_TOOLS`) and **never**
`--dangerously-skip-permissions` — not by default, but at any setting.
`claude_p` raises if such a worker is handed the real checkout as its `cwd`.
Acting workers (implementer, conformer, integrator, rebaser) run in isolated
worktrees with the broader `ACT_TOOLS` allowlist and the skip-permissions flag;
their blast radius is the worktree they own.

The reason the flag is unreachable for judgment workers is measured: it
removes the CLI's working-directory boundary as well as the prompts.
Probed live (claude 2.1.237, filesystem-verified), a worker holding only
`INSPECT_TOOLS` plus the flag used `Write` — absent from that allowlist —
to overwrite a tracked file outside its cwd and `git commit` on the user's
branch, even from a detached worktree. With the flag absent, every such
attempt was rejected.

So `--dangerously-skip-permissions` no longer bypasses permissions for
these workers; it **widens their allowlist** (`_widen_inspect_tools`) with
the leading verbs of the repo's own declared build/lint/test commands, as
`Bash(<verb>:*)` patterns — the visibility the flag was documented to buy
(Node/TS repos where the planner reaches for `pnpm`/`tsc`/`biome`/`vitest`/
`npx`, ~18-19% of whose Bash calls otherwise fail "requires approval" in
headless mode) without the write access that was never the point.
Residual: a build verb executes arbitrary code, so an allowlisted
`pnpm`/`node`/`python3` can still write outside the cwd;
`_assert_repo_unchanged()` catches that. See DESIGN §12 *Judgment-worker
isolation* for the full four-layer argument.

Resolution order (highest priority first):

1. **`--dangerously-skip-permissions`** CLI flag (action=`store_true`).
2. **`LEERIE_DANGEROUSLY_SKIP_PERMISSIONS`** environment variable
   (boolean, parsed by `_parse_bool_envtoml`: 1/0, true/false, yes/no,
   on/off).
3. **`leerie.toml` at the repo root** with
   `dangerously_skip_permissions = true`.
4. **Default `False`.** Judgment workers stay narrow-allowlisted; the
   §12 mechanical enforcement holds.

An invalid value in env or file is rejected at startup via `die()` —
same shape as `--no-push` resolution. When the flag is active, leerie
emits a visible startup log line so every run shows the escape hatch
is engaged.

### Containment override (dangerous)

Worker cgroup containment (DESIGN §6 *Memory containment*) is enforced by
a cgroup broker (`scripts/cgroup-broker.py`) running at the slice-owning
identity; the dropped-privilege orchestrator can neither enroll workers
nor set their limits itself. Just before the first worker spawns (in
`_run_phases`, past the resume short-circuits so zero-worker
completed/no-work resumes are not gated),
`_enforce_and_record_cgroup_containment` probes the broker end-to-end and
records `{enforced, hierarchy}` in `state.json`'s `cgroup_containment`
field. If containment cannot be enabled — broker down, no usable cgroup
hierarchy (neither a cgroup-v2 unified mount nor v1 pids+memory
controller mounts), or read-only cgroupfs — leerie `die()`s by default,
because a silently-uncapped run is what let a runaway subtree exhaust the
VM thread/PID table (a Bun `EAGAIN` crash).

`--dangerously-allow-uncapped` is the escape hatch: it downgrades the
fatal gate to a loud warning and runs workers without memory/PID limits.
Resolution order (same shape as `--dangerously-skip-permissions`):

1. **`--dangerously-allow-uncapped`** CLI flag (action=`store_true`).
2. **`LEERIE_DANGEROUSLY_ALLOW_UNCAPPED`** environment variable.
3. **`leerie.toml`** with `dangerously_allow_uncapped = true`.
4. **Default `False`.** Containment is required; the run stops if it
   cannot be enforced.

### Budget feasibility preflight

`max_total_workers` (DESIGN §13 *Budget feasibility — fail fast at
the cheapest moment*) is enforced two ways. The cheap, late check is
`State.bump_workers()`, which raises `WorkerError` the moment the
counter would exceed the cap mid-execution. The complementary *early*
check is `check_budget_feasibility()`, called once in `_run_phases()`
immediately after `_schedule()` returns its `(subtasks, waves)` pair
and before `_write_plan()` persists anything. It estimates the
remaining `claude -p` calls the run will consume:

```
estimated_remaining = (
    len(subtasks) * caps["subtask_call_estimate"]   # impl + ~conformer per subtask
    + len(waves)                                     # one integrator per wave
    + caps["conformance_rounds"]                     # final-tree conformance pass
    + 1                                              # pr_writer (finalize itself is shell)
)
total_estimate = st.data["worker_count"] + estimated_remaining
if total_estimate * caps["budget_safety_margin"] > caps["max_total_workers"]:
    die(... recommended --max-workers ..., code=EXIT_BUDGET_INFEASIBLE)
```

The estimate adds to `worker_count` (which already reflects every
upstream phase: classifier, provision, planners, reconciler, overlap
judge), so the only free variable is the per-subtask multiplier. Default
`subtask_call_estimate = 3.0`, covering the worst observed real
calls/subtask ratio plus one completeness re-drive per subtask
(DESIGN §9); the `budget_safety_margin = 1.15` on top gives ~1.20×
guaranteed headroom against the cap.

Resolution order for the opt-out (highest priority first):

1. **`--skip-budget-check`** CLI flag (action=`store_true`).
2. **`LEERIE_SKIP_BUDGET_CHECK`** environment variable
   (`_parse_bool_envtoml`).
3. **`leerie.toml` at the repo root** with `skip_budget_check = true`.
4. **Default `False`.** The check runs.

Skipped on a `resume` that already reached `waves`: the resume path
enters `_run_phases` past `_schedule()` (`waves` loads from
`state.json`), so the preflight has nothing left to gate. A run that
died on the preflight is itself resumable (DESIGN §6 "Budget-check
resume"): `plan_snapshot` — written immediately after `_schedule()`
and before this check (DESIGN §6 "Resumable planning") — lets `resume`
rehydrate `subtasks`/`waves` and re-run only the budget check, under a
higher `--max-workers` or `--skip-budget-check`, instead of a fresh run
from scratch.

Exit code `EXIT_BUDGET_INFEASIBLE = 11` on `die()`, distinct from
`EXIT_NEEDS_ANSWERS = 10` and the generic error code 1. The Fly
runtime's `decide_teardown` trap (`scripts/remote/provision.sh`) routes
`11` through the same case-arm as `0|10|75` (genuine terminal exits):
it fetches whatever state landed on the machine back to the host, then
destroys the machine cleanly (the `_run_finished_at == ""` fallback, no
`host_finalize`) with a code-11-specific recovery hint — "re-run with
the recommended --max-workers value" — distinct from code-10's
`finalize` hint. The machine is destroyed rather than paused: even
though `plan_snapshot` makes the host-side `resume` recoverable, the
Fly Machine has no further use once `decide_teardown` runs — the fix is
a higher `--max-workers` on a fresh remote launch, not resuming the
same (now-destroyed) machine.

### Decomposition budget partition

Recursive decomposition (`_recursive_decompose`, DESIGN §5½ (P1) — every
`fit_judge`/`splitter` call it spawns) shares the same `worker_count`
budget as execution, and can exhaust `max_total_workers` entirely during
planning, leaving zero calls for implementers/conformers. Two caps
address this together:

1. **`DEFAULT_CAPS["decompose_budget_share"] = 0.40`** — the fraction of
   `max_total_workers` recursive decomposition may spend. Enforced by
   `_bump_decompose_workers(st, caps)`, called by every fit_judge/splitter
   spawn site in `_recursive_decompose` (including the label-only
   migration-chunk splitter) instead of a bare `st.bump_workers`. It
   **checks before it bumps** — `decompose_worker_count >=
   decompose_budget_share * max_total_workers` raises
   `DecompositionBudgetExceeded` (a `WorkerError` subclass) before
   touching either counter, so a refused call cannot eat into the
   execution budget it protects — otherwise it bumps both
   `st.data["worker_count"]` (via `st.bump_workers`, preserving the
   pre-existing global-cap check) and `decompose_worker_count`. Callers
   catch the exception and accept the node as a leaf, or fall back to
   deterministic chunk labels (label-only migration site), without
   spawning the call.

   This is a runaway backstop, not a score gate — it ignores the
   fit_judge score, since stopping early on projected cost would ship
   exactly the low-scoring nodes `decompose_fit_threshold` exists to keep
   splitting. `_warn_decomposition_share` records the realized share in
   `state.json`'s `decompose_share` for calibration.
2. **`DEFAULT_CAPS["max_total_workers"] = 2000`** — the global runaway
   ceiling across the whole run. Runaway detection at the per-subtask
   level (8 separate retry-round caps — `failed_retries`,
   `conformance_rounds`, `completeness_retry_rounds`,
   `judgment_check_rounds`, `planner_check_rounds`,
   `implementer_confidence_retries`, `confidence_rounds`,
   `decompose_noprogress_rounds`) catches a looping subtask; this ceiling
   only needs to stay above legitimate large plans.

Both defaults remain overridable via the existing `--max-workers` /
`LEERIE_MAX_WORKERS` / `leerie.toml` resolution chain
(`decompose_budget_share` has no CLI/env/TOML override — it is not
exposed as a user-facing knob).

### Single-owner-per-run-dir enforcement

DESIGN §6 *Single owner per run dir*. The orchestrator refuses to
start a second instance against a run directory that another
orchestrator already owns. Two code-surface elements implement this:

- `EXIT_LOCKED = 75` constant in `orchestrator/leerie.py`. Emitted
  via `sys.exit(EXIT_LOCKED)` (not `die()`) so the prefix is not
  `leerie: error:` — same shape as `EXIT_NEEDS_ANSWERS`'s
  structured non-error exit, since refused-resume is a routing
  signal, not an error. Caller-side handlers print a
  `leerie resume <run-id>` hint via `log()` before exiting (the
  launcher's smart-router will then attach to the live stream
  rather than spawn a duplicate). Reachable from the own-instance-
  already-running refusal, the out-of-credits pause, and an
  expired-session/not-logged-in auth failure
  (`_is_terminal_auth_failure`) — all three share the same
  worktree-only-cleanup + `resume`-picks-back-up contract. See §3
  *Terminal auth failure* and DESIGN §6 *Credential strategy*.
- `StateLockedError` exception in `orchestrator/leerie.py`. Raised
  by `State.__init__` when `fcntl.flock(LOCK_EX | LOCK_NB)` on the
  run-directory fd fails with `BlockingIOError`. The exception
  carries `run_dir` so callers can include the path in the user
  message. Raised with `from None` to suppress the
  `BlockingIOError.__context__` chain in the traceback.

The lock primitive itself:

- `State.__init__(leerie_root, run_id, repo_root=None)` opens
  `self.run_dir` with `os.open(..., O_RDONLY)`, stores the fd on
  `self._lock_fd`, and acquires `fcntl.flock(LOCK_EX | LOCK_NB)`. The
  fd is held for the life of the State instance. The optional
  `repo_root: Path | None` parameter defaults to `leerie_root.parent`
  when not provided — needed because `LEERIE_STATE_DIR` can place
  `leerie_root` outside the repo, making `leerie_root.parent`
  incorrect as a repo root.
- `State.release_lock()` closes the fd. Idempotent. Used by tests;
  the production path relies on the kernel's process-exit cleanup.
- `State.__del__` is defensive (calls `release_lock` only if
  `_lock_fd` was set — `__init__` can raise before that field
  exists). Best-effort; the kernel guarantees release on process
  exit regardless.
- `State.save`'s locking behavior is unchanged: the flock is on the run
  directory inode, not the state.json inode, so the
  `os.replace(tmp, self.path)` swap inside `save()` does not affect the
  lock. `save()` also catches an `OSError(ENOSPC, ...)` from either half
  of the write and reraises it as `DiskLowSpace` — see §"Disk headroom
  (N30)". The rename uses `os.replace()` rather than `Path.replace()`:
  on Python 3.10, `pathlib`'s accessor binds `os.replace` at
  class-definition time, so patching the `os` module's `replace`
  attribute would not affect `Path.replace()` (only 3.12's rewritten
  pathlib looks it up dynamically) — `os.replace()` keeps the behavior
  version-independent.

Two checked construction sites that catch `StateLockedError`:

- `main()` at the `State(leerie_root, run_id, repo_root=repo_root)` call:
  logs the message + `sys.exit(EXIT_LOCKED)`.
- `--phase judge|heal` at the `phase_st = State(...)` call: same
  pattern, since `--phase` mutates state and would race the same
  way `resume` would.

The launcher heredoc (`leerie:2679-2696`) takes a fast-path flock
probe on `run_dir` before invoking the orchestrator subprocess. On
`BlockingIOError` the probe exits 75. The probe is advisory — the
orchestrator's `State.__init__` flock acquire is the load-bearing
enforcement that catches any path bypassing the launcher (manual
`python3 leerie.py resume`, future verbs, debugging).

Host-side rc=75 branch (`leerie:~3563`) sets `container_rc=130`
(not 1, not 75). decide_teardown's classifier treats `rc=130|143` as
detach-banner (leave the machine running, print reattach hints) —
exactly the right disposition when the original orchestrator is
still alive. Setting `container_rc=1` or 75 would route into
sync-then-finalize-then-destroy or pause-on-failure, both of which
would tear down the original orchestrator's machine.

**flyctl exit-code workaround.** `flyctl ssh console` does not forward
the remote process's exit code — it returns 1 for any non-zero remote
exit. The actual code appears only in stderr
(`Error: ssh shell: Process exited with status <N>`). The launcher
captures stderr to a tempfile at the launch-wrapper invocation and
uses `_extract_flyctl_remote_rc` (`scripts/remote/lib.sh`) to parse
the real remote code. Without this, the rc=75 branch never fires and
the generic `_launch_rc != 0` branch sets `container_rc=1`, causing
`decide_teardown` to pause (stop) the machine and kill the live
orchestrator.

The `resume` smart router's auto-discovery scan honors
`LEERIE_STATE_HOST_DIR` (the launcher exports it at line ~228 before
any verb dispatch) and falls back to `$USER_REPO/.leerie` for backward
compatibility, matching the pattern every other host-side verb uses.

### Host-side per-repo state directory

Resolves `LEERIE_STATE_HOST_DIR`: the host path where this repo's leerie
run state (`runs/`, `worktrees/`, etc.) is stored. Lives under `$HOME` so
Colima auto-shares it without an explicit `--mount` entry; keyed by repo
basename so each repo gets a readable, isolated subtree.

Default path: `$HOME/.leerie/<basename>/`. The default sits adjacent to
the installer's clone at `$HOME/.leerie/` (DESIGN §TBD) — the installer's
files live at the top level (`leerie` executable, `.git/`, `docs/`,
etc.), per-repo state dirs live as siblings (e.g.
`$HOME/.leerie/myproject/`). The launcher's `_validate_state_ownership`
check (below) catches the rare collision where a basename matches an
installer-dir marker (`.git/` or a `leerie` executable at top level
with no `runs/` subdir).

Resolution order (lowest → highest priority):

1. **Default** `$HOME/.leerie/<basename>/`. Computed by the
   `_state_dir_default` helper in the launcher as just the basename of
   `$USER_REPO`. No hashing; no `state/` segment.

2. **`leerie.toml` at the repo root** with key `state_dir`. Plain
   `key=value` syntax; bare `~` and `~/`-prefixed values are expanded to
   `$HOME`:

   ```
   state_dir = ~/.leerie/myproject
   ```

3. **`LEERIE_STATE_DIR`** environment variable. Overrides the default and
   any toml value; bare `~` and `~/`-prefixed values are expanded.

4. **`--state-dir PATH`** / `--state-dir=PATH` CLI flag. Highest priority;
   overrides everything. Launcher-only (stripped from `REWRITTEN_ARGS`;
   the orchestrator never sees it). Bare `~` and `~/`-prefixed values
   are expanded.

After resolution and before the verb dispatch, the launcher runs
`_validate_state_ownership` against the resolved path:

- **Fresh dir (does not exist):** create it and write `.owner` containing
  `$USER_REPO`.
- **Dir exists with matching `.owner`:** continue.
- **Dir exists with mismatched `.owner`:** error and exit. Two different
  repos share a basename; the operator must pick an explicit override
  (`--state-dir` / `LEERIE_STATE_DIR` / `leerie.toml: state_dir = ...`).
- **Dir exists, no `.owner`, contains `runs/` or `worktrees/`:** backfill
  the `.owner` sidecar from `$USER_REPO` (covers operators upgrading from
  the pre-`.owner` layout).
- **Dir exists, no `.owner`, contains `.git/` at top level or a `leerie`
  executable at top level:** error and exit. The dir looks like the
  leerie install directory, not a state dir.
- **Dir exists, no `.owner`, no recognizable markers (empty or
  unrelated):** claim it by writing `.owner`.

The check is skipped for `version`, `config`, and the `chain` verb (those
talk to the chain Fly app or are host-only fast paths that don't touch local state).

Resolution and ownership validation live entirely in the launcher (bash);
no Python counterpart — the path is passed to `nerdctl run` as a
bind-mount volume argument once resolved. Tested by
`tests/test_resolve_state_dir.py` (resolver + ownership check, 27 cases).

> The CLI/env > file order follows the same session-scoped vs.
> committed-default split as `--source-of-truth` and `--runtime`.

The **container-side** counterpart is `resolve_leerie_root(repo_root)`
in `leerie.py` (constant `STATE_DIR_ENV = "LEERIE_STATE_DIR"`), which
every `leerie_root` assignment in `main()` calls via
`resolve_leerie_root(Path(os.getcwd()))`. It mirrors the same resolution
order and default path above, evaluated inside the container rather
than by the launcher: the launcher resolves `LEERIE_STATE_HOST_DIR`
before container launch via `_state_dir_default()`, passes it as the
`/leerie-state` bind-mount argument, and sets
`-e LEERIE_STATE_DIR=/leerie-state` so the orchestrator inside the
container always writes to the mounted state dir. See §0.5 *Bind-mount
table* for the full mount specification.

### Runtime mode

Controls which execution backend runs the per-subtask worker containers.
`local` uses the local nerdctl/containerd runtime (the existing behavior);
`fly` routes each worker through Fly.io machines; `ec2` provisions and
runs the orchestrator on an AWS EC2 instance (AWS credentials resolve the
same way the AWS CLI/SDKs do — see `scripts/remote/aws-credentials.sh`
and `ec2-lib.sh`'s `require_aws()` preflight). The launcher's
`RUNTIME=ec2` branch sources `aws-credentials.sh` and `ec2-lib.sh`, first
calling `resolve_aws_credentials` (`--profile`/`--region` from
`LEERIE_AWS_PROFILE`/`LEERIE_AWS_REGION` when set) and exporting its
resolved credentials/region into the launcher's environment, then gating
on `require_aws()` — mirroring the `RUNTIME=fly` branch's
`require_flyctl` sequencing (`tests/test_ec2_e2e_provision.py` pins the
ordering: `resolve_aws_credentials` precedes `require_aws`'s `sts
get-caller-identity` call, which in turn precedes any `ec2
run-instances` call; a failing credential probe — including an
unresolvable chain caught by `resolve_aws_credentials` itself, e.g. an
expired SSO token — aborts with the `aws sso login --profile <p>` hint
before any AWS resource is created).

After preflight passes, the branch sources `ec2-provision.sh`,
`ec2-resume-instance.sh`, `ec2-seed-auth.sh`, `ec2-seed-repo.sh`,
`ec2-fetch-branch.sh`, and `ec2-ssm.sh`, then dispatches through the same
five DESIGN §6 stage-mapping rows the Fly branch implements, transport
substituted:

1. **Create/resume.** When `--run-id`/`LEERIE_RUN_ID` names a run whose
   `ec2-instance.json`/`run.json` sidecar carries an `ec2_instance_id`,
   `resume_instance()` wakes it; otherwise `provision_instance()` creates
   a fresh one and sets `LEERIE_RUN_ID` to the new instance id (run_id =
   instance_id, mirroring Fly's run_id = machine_id rule — DESIGN §6
   "Run identifier"). Unlike Fly, bare `resume` does not yet
   auto-discover an EC2 instance — the operator passes `--run-id`
   explicitly.
2. **Wait-ready** is `provision_instance()`'s/`resume_instance()`'s own
   internal `wait_for_instance_ready()` call.
3. **Seed.** `LEERIE_EC2_SSH_TARGET` resolves from the instance's public
   IP (`ec2-resume-instance.sh`'s `_resolve_ssh_target_from_instance`,
   reused for fresh provisions too), then `ec2_seed_auth()` followed by
   `ec2_seed_repo()` — mirroring `seed_auth`+`seed_repo` on the Fly path.
   An early flock probe over `ec2_remote_exec` mirrors Fly's
   resume-only optimization: if the run directory's flock is already
   held, seeding is skipped and the launcher attaches to the live
   orchestrator instead.
4. **Orchestrate.** A detached-`Popen` Python launch wrapper (same shape
   as the Fly launch script) is piped to `ec2_launch_detached()`. rc=75
   (flock-loser smart-resume) routes to
   `_attach_to_live_orchestrator_ec2()` instead of provisioning a
   duplicate — `container_rc=130` so `decide_ec2_teardown`'s detach arm
   leaves the instance alone, exactly like Fly's identical rc=75
   routing. On a clean launch, the launcher tails the log via
   `render_tail_wrapper()` (`lib.sh`, transport-agnostic) through
   `ec2_attach()`.
5. **Teardown** is `ec2-provision.sh`'s own `decide_ec2_teardown()` EXIT
   trap, registered/re-armed by `provision_instance()`/`resume_instance()`;
   the launcher only sets `LEERIE_REMOTE_EXIT_RC` before exiting, same as
   the Fly branch.

Not yet wired for EC2 (documented gaps, not required for an end-to-end
run): bare `resume` PID-record auto-discovery (an explicit
`resume <run-id>` is required), mid-run re-seed (`re-seed`/auto-re-seed on
`resume`), `--inspect-dir` seeding, chain-wave tagging, and
auto-finalize token plumbing on the tail/attach path. `finalize
--runtime ec2` is also not yet wired — that verb remains Fly-only
today; `stop --runtime ec2` and `kill --runtime ec2` *are* both
wired (see "Explicit pause and destroy verbs" above). DESIGN §6 *EC2
runtime lifecycle* is the canonical architecture. Default is `local`
so existing behavior is unchanged for users who have not opted in.

Resolution order (highest priority first):

1. **`--runtime`** CLI flag, values `local` | `fly` | `ec2`. Argparse
   rejects anything else before the orchestrator runs.

2. **`LEERIE_RUNTIME`** environment variable, same value set.

3. **`leerie.toml` at the repo root** with key `runtime`. Plain
   `key=value` syntax:

   ```
   runtime = fly
   ```

4. **Default `local`.** When unset, leerie runs workers in the local
   container runtime. The default preserves all existing behavior
   for users who have not configured a remote runtime.

An invalid value in env or file is rejected at startup via `die()` — bad
config is caught before any worker spawns. Valid values are
`{local, fly, ec2}`.

> The CLI/env > file order reflects the same session-scoped vs.
> committed-default split as `--source-of-truth`: the CLI flag and env
> var are one-off overrides, while `leerie.toml` is the per-repo default.

Maps to: `resolve_source_of_truth` resolution pattern in `leerie.py`
(`_read_toml_key` + env + CLI precedence). The code counterpart is
`resolve_runtime()` in `leerie.py`; constants are `RUNTIME_VALUES`,
`RUNTIME_ENV`, `RUNTIME_FILE`; argparse flag is `--runtime {local,fly,ec2}`.

### AWS region/profile prefs

Leerie-level knobs for which AWS region/profile leerie itself uses when
provisioning `--runtime ec2` machines — distinct from the AWS SDK's own
`AWS_REGION`/`AWS_PROFILE` credential-chain env vars, which
`scripts/remote/aws-credentials.sh` resolves independently via the
standard AWS precedence order (see that file's row in the Files table
above). Free-form strings, no enum validation — mirrors `resolve_pr_template`,
not `resolve_runtime`.

Resolution (identical for both knobs): `--aws-region`/`--aws-profile` CLI >
`LEERIE_AWS_REGION`/`LEERIE_AWS_PROFILE` env > `leerie.toml`
`aws_region`/`aws_profile` > default `None` (unset knobs leave
region/profile selection to the AWS credential chain `aws-credentials.sh`
resolves independently).

**Resolved by the launcher, not the orchestrator.** `_resolve_ec2_knob` in
`leerie` runs the ladder and assigns back into `LEERIE_AWS_REGION` /
`LEERIE_AWS_PROFILE`, read by `ec2-lib.sh`'s `require_aws()`, `ec2-ssm.sh`,
and `ec2-provision.sh`'s `_aws_region_profile_args()`. Both flags are
**launcher-only inputs** — stripped from `REWRITTEN_ARGS`, allowlisted in
`tests/test_launcher_value_flags_coupling.py`, and deny-listed from
container env forwarding, like the `LEERIE_EC2_*` vars below.

Resolved **above** the launcher's verb dispatch, since `accept-blocked`,
`stop`, `kill` and `finalize` each read `LEERIE_AWS_*` independently.

No orchestrator-side counterpart exists (`args.aws_region` /
`resolve_aws_region()` etc. are absent from `orchestrator/leerie.py`) — a
host-side provisioning region is meaningless inside the container.
`tests/test_no_dead_resolutions.py` fails any `args.X = resolve_Y(...)`
whose result goes unread.

### EC2 instance-lifecycle vars

Six `LEERIE_EC2_*` vars name the `RunInstances` parameters
`scripts/remote/ec2-provision.sh`'s `provision_instance()` needs
(DESIGN §6 *EC2 runtime lifecycle*, "Create" row):
`LEERIE_EC2_AMI`, `LEERIE_EC2_INSTANCE_TYPE`, `LEERIE_EC2_KEY_NAME`,
`LEERIE_EC2_SECURITY_GROUP`, `LEERIE_EC2_SUBNET_ID`, and
`LEERIE_EC2_INSTANCE_ID`. All six are **launcher-only inputs**, already
deny-listed from the `LEERIE_*` container-forwarding loop
(`leerie:6284-6297`) — the orchestrator runs *inside* the
already-provisioned instance and has no use for the parameters that
created it, mirroring `LEERIE_FLY_APP`/`LEERIE_FLY_IMAGE`/`LEERIE_MACHINE_ID`
on the Fly path (`tests/test_launcher_env_forwarding.py` pins all six on
the deny-list). No Python-side `resolve_*()` counterpart exists, same as
`LEERIE_AWS_REGION`/`LEERIE_AWS_PROFILE` above — both groups are consumed
exclusively by the host-side launcher/`ec2-provision.sh` before any
container or instance exists.

Five are per-instance `RunInstances` parameters, each on the standard
**CLI > env > `leerie.toml` > (no default)** precedence, resolved by the
launcher (`leerie:3644-3710`, `_resolve_ec2_knob`) before `ec2-lib.sh` is
sourced, then exported and stripped from `REWRITTEN_ARGS`:
`--ec2-ami`/`--ec2-instance-type`/`--ec2-key-name`/`--ec2-security-group`/
`--ec2-subnet-id` CLI > `LEERIE_EC2_AMI`/`LEERIE_EC2_INSTANCE_TYPE`/
`LEERIE_EC2_KEY_NAME`/`LEERIE_EC2_SECURITY_GROUP`/`LEERIE_EC2_SUBNET_ID` env >
`leerie.toml` keys `ec2_ami`/`ec2_instance_type`/`ec2_key_name`/
`ec2_security_group`/`ec2_subnet_id` > **(no default)** — these describe AWS
account resources leerie cannot choose on the operator's behalf (unlike Fly,
where `FLY_VM_CPUS`/`FLY_VM_MEMORY_MB` have working defaults). Once all tiers
are exhausted, the var exports empty; `ec2-lib.sh`'s `resolve_ami()` /
`resolve_instance_type()` / `resolve_key_name()` / `resolve_security_group()`
/ `resolve_subnet_id()` each read their var via `_resolve_ec2_var` — a
required-var check that `die()`s with an actionable message naming the
missing var, run host-side rather than a bare `${VAR:?}` (which would kill
the sourcing shell with bash's generic "parameter null or not set" under
`set -u`). `RUNTIME=ec2` without all five resolved fails the same way
`RUNTIME=fly` without `LEERIE_FLY_APP` fails: `die()` with setup instructions
before any AWS API call. `tests/test_resolve_ec2_vars.py` covers the ladder.

The sixth, **`LEERIE_EC2_INSTANCE_ID`**, is not a provisioning input —
it is the launcher's read of the just-created instance id back into the
environment after `provision_instance()` returns, mirroring how
`LEERIE_MACHINE_ID`/`LEERIE_RUN_ID` are set launcher-side after
`flyctl machine run` for the Fly path. It is written to the
crash-recovery sidecar `ec2-instance.json` rather than read from an
operator-set env var.

A seventh var, **`LEERIE_EC2_SSH_TARGET`**, is consumed by
`scripts/remote/ec2-seed-repo.sh`: the `ssh`(1) destination for the
instance (e.g. `ec2-user@<public-ip>` or an `ssh_config` Host alias) that
`ec2_tar_pipe` and the dirty-delta rsync consume verbatim. Like
`LEERIE_EC2_INSTANCE_ID`, resolving an instance id to a reachable SSH
address is `ec2-provision.sh`'s job (not yet implemented); the launcher
is expected to set it the same way once provisioning lands.

### EC2 image delivery

DESIGN §6 *EC2 runtime lifecycle* → "Image delivery" settles how the
leerie image reaches an EC2 instance: **bake into the AMI**, the direct
analog of Fly's shipped `ensure_image()` push-to-registry answer but for
a boot-from-snapshot target. The operator builds a custom AMI out of the
per-run critical path (a Packer / EC2 Image Builder pipeline, out of
scope for leerie itself) with the orchestrator source, Python 3.10+, and
every OS-level dependency `.leerie-setup.sh` would otherwise need root
for already present. `ec2-provision.sh`'s `provision_instance()` reflects
this: `run-instances` carries no explicit block-device mapping and no
per-run build/push/pull step — the instance is ready to accept
`ec2_seed_repo`/`ec2_remote_exec` calls the moment
`wait_for_instance_ready()` returns.

**No new `LEERIE_EC2_*` knob.** `LEERIE_EC2_AMI` (already spec'd above)
is sufficient to name the chosen artifact: a custom AMI under the
bake-into-AMI default, or a stock AMI paired with a documented user-data
fallback script for an operator who has not yet built one (DESIGN §6
rejects ECR-push and user-data pull-and-build as the default; user-data
pull remains a documented manual fallback, not a second code path). No
`resolve_*()` counterpart, no denylist change — `LEERIE_EC2_AMI` is
already launcher-only and already deny-listed for container forwarding.

**Future knob flagged, not added.** DESIGN §6 flags that an instance
profile (`IamInstanceProfile`, carrying the SSM managed-instance role
`ssm:StartSession` et al. need) is a `RunInstances` parameter the
provisioning subtask will have to supply — shaped like a future
`LEERIE_EC2_INSTANCE_PROFILE` knob. Not added here; belongs to whichever
subtask wires `IamInstanceProfile` into `run-instances`.

### Fly app name

Fly.io app names are globally unique. `LEERIE_FLY_APP` is required when
`RUNTIME=fly`; the launcher `die()`s with setup instructions when unset.

Resolution: `--fly-app NAME`/`--fly-app=NAME` CLI (launcher-only, stripped
from `REWRITTEN_ARGS`; the orchestrator never sees it) > `$LEERIE_FLY_APP`
env > **(none)** — no default, no `leerie.toml` key. Required.

The resolved value is exported as `LEERIE_FLY_APP` and assigned to
`FLY_APP` before any remote script is sourced. Verb paths (`stop`,
`kill`, `finalize`, `list --runtime fly`, `re-seed`) validate
independently since they exit before the main resolution gate.

### Prompt loading and the shared filter fragment

Worker prompts are loaded by `_load_prompt(name)` in
`orchestrator/leerie.py` rather than `read_text()` directly. The
helper expands any `{{include: _foo.md}}` placeholder by inlining the
named fragment from `prompts/`. Fragments prefixed with `_` are
internal includes — never standalone worker prompts. Today there is
one fragment, `prompts/_clarification_filter.md`, included by
`prompts/classifier.md` and `prompts/implementer.md`. It is the single
source of truth for the codebase→research→ask wording shown to
workers; DESIGN.md §11 is the architectural spec that the fragment
must conform to.

### Confidence rounds

Planners and implementers self-gate on confidence (DESIGN §8) and loop their
evidence-gate up to `confidence_rounds` times before they exit `blocked`.
Default 8. Increase if the user wants workers to push harder on hard
diagnoses; decrease for cheaper, faster runs that accept earlier
escalations.

Resolution: `--confidence-rounds N` CLI (argparse rejects non-positive
integers) > `LEERIE_CONFIDENCE_ROUNDS` env > `leerie.toml`
`confidence_rounds = N` > default `8` (`DEFAULT_CAPS["confidence_rounds"]`).

An invalid value in env or file is rejected at startup via `die()`. The
resolved value is written into `caps["confidence_rounds"]` and passed in
each planner / implementer's user prompt — the cap is prompt-governed (see
§6 "Worker-internal caps" and DESIGN §13), the user-visible knob is real.

### Seed depth (shallow seeding)

Governs the fresh-provision `seed_repo_clone` transport for remote
(Fly) runs (DESIGN §6 *Shallow seeding for heavy repos*). Two knobs,
both resolved **in the `leerie` launcher** (bash — the Python
orchestrator never reads them, unlike `confidence_rounds`), mirroring
the `FLY_VM_DISK_GB` resolution pattern (CLI → env → `leerie.toml` →
default):

- **`LEERIE_SEED_DEPTH`** — the `git clone --depth=N` used when the
  shallow path fires. Resolution: `--seed-depth N` CLI > `LEERIE_SEED_DEPTH`
  env > `leerie.toml` `seed_depth = N` > **default `50`**. `0` means
  *full history* — it disables shallow seeding entirely and forces the
  full `--all` bundle regardless of repo size. Must be a non-negative
  integer; an invalid value is rejected at startup via a launcher exit.
- **`LEERIE_SEED_SHALLOW_THRESHOLD_MB`** — the repo `.git` size (MB)
  above which the shallow path activates. Resolution:
  `--seed-shallow-threshold-mb N` CLI > env >
  `leerie.toml` `seed_shallow_threshold_mb = N` > **default `200`**.
  Must be a positive integer. Below the threshold, the full-bundle
  path is used (costs nothing to ship for small repos).

Both are `export`ed into the environment `seed-repo.sh` reads. The
shallow path fires only when `LEERIE_SEED_DEPTH != 0`, `.git` size (via
`du -sk`) exceeds the threshold, **and** the working branch name is
shell-safe (`^[A-Za-z0-9/._-]+$`); otherwise `seed_repo_clone` falls
back to the full `git bundle --all` path. The launcher **strips** both
flags (and their values) from `REWRITTEN_ARGS` — the same way it
handles `--fly-app` / `--state-dir` — so they never reach the
orchestrator's strict `parse_args()` (the orchestrator declares no
argument for either; it would otherwise error `unrecognized
arguments`). `tests/test_launcher_value_flags_coupling.py` guards this.

On resume, the launcher additionally probes `/work` validity with a
**token-based** `flyctl ssh console` command that always exits 0 when
SSH works and prints `VALID` (`/work/.git` present and `git -C /work
rev-parse --verify HEAD` succeeds) or `INVALID`. The destructive full
`seed_repo` (which wipes + re-clones `/work`) runs **only** on a
confirmed round-trip returning `INVALID` (initial seed never
completed); a `VALID` result — or an **inconclusive probe** (non-zero
flyctl rc, e.g. a transient SSH failure) — takes the non-wiping
dirty-only `re_seed` path, so a valid `/work` with a run branch is
never obliterated by a transport blip. The probe rc is captured via
`|| _work_probe_rc=$?` so a failing `flyctl` does not trip the
launcher's `set -e`. (DESIGN §6 *Shallow seeding for heavy repos*,
resume corollary.)

### `--log-file` / `LEERIE_LOG_FILE` (N5b)

Resolved **in the `leerie` launcher** (bash — the Python orchestrator never
reads it), mirroring the `--state-dir` resolution block: CLI flag > env var
> `leerie.toml` flat key > default.

- **`LEERIE_LOG_FILE_RESOLVED`** — the resolved log file path, exported for
  the teeing wiring below. Resolution: `--log-file <path>` CLI >
  `LEERIE_LOG_FILE` env > `leerie.toml` `log_file = "..."` > default
  `$LEERIE_STATE_HOST_DIR/logs/leerie-<pid>.log`.

A log left inside `$USER_REPO` (e.g. via manual `leerie task | tee
leerie-<task>.log`) is bind-mounted whole into every worker's container,
letting a worker read its own orchestration log and defeat judge
independence (`_warn_if_log_in_repo` detects this). The default lands
under `LEERIE_STATE_HOST_DIR` instead, since it is never bind-mounted into
a worker container.

`--log-file` is registered in the launcher's `_value_flags` list (so the
task-argument-extraction walk doesn't mistake its value for the task
string) and stripped (flag + value) from `REWRITTEN_ARGS` before
forwarding to `parse_args()`, same as `--seed-depth` /
`--seed-shallow-threshold-mb`.

**Teeing (local runtime).** The launcher writes its combined
stdout+stderr to `LEERIE_LOG_FILE_RESOLVED` itself, so the operator no
longer needs the manual `| tee`. In the piped/non-TTY local case
(`TTY_FLAGS=-i`), `nerdctl run` redirects into a launcher-owned
`$_run_log` file that a `tail -f` streams to our own stdout (so the SSH
mux never holds our stdout pipe); that `tail` is now piped through
`tee -a "$LEERIE_LOG_FILE_RESOLVED"` when the target is writable.
`$_run_log` is a scratch file removed at exit; `LEERIE_LOG_FILE_RESOLVED`
is the durable copy. `tail` does not reliably exit on its own when teeing
(a `tail -f` on a since-deleted file never gets a write to trigger
`SIGPIPE`), so `_reap_tail` recovers `tail`'s PID from the job table
(`jobs -l %%`) and kills it alongside `$_tail_pid` (which names only
`tee` in this path).

**Interactive/-it path.** Piping nerdctl's own stdout would defeat `-t`,
so for the real-tty case the `-it` branch is instead wrapped in
`script`(1) when a `--log-file` target is writable and `script` is on
`PATH`: `script` allocates its own pty for the `nerdctl run` child (so
nerdctl still gets a real console for `--clarify`'s interactive prompt)
while duplicating that pty's bytes into the log file. util-linux `script`
(Linux) takes a command via `-c <string>`; BSD `script` (macOS) takes
trailing positional args directly. Falls back to nerdctl inheriting stdout
directly when no target is writable or `script` is unavailable. Remote
runtimes (Fly, EC2) are out of scope — local-runtime-only.

### Verbosity

Controls how much of the per-worker activity surfaces to the
orchestrator log. Per-worker `<state-root>/logs/<sid>.log` files are
always written with the full raw event stream — verbosity governs
only the *inline* summary lines. Four named levels with stackable
`-v`/`-q` shortcuts, following the clig.dev / cargo / kubectl
convention.

| Level    | Flag             | What you see inline |
| -------- | ---------------- | ------------------- |
| `quiet`  | `-qq` / `--verbosity quiet` | Phase boundaries, final result, errors only |
| `normal` | `-q` | Phase boundaries + per-subtask status changes (leerie's pre-streaming behavior) |
| `stream` | `-v` / (default) | `normal` + one-line summary per worker event |
| `debug`  | `-vv` / `--verbosity debug` | `stream` + raw event payloads, tool I/O, schema diffs, retry diagnostics |

Streaming log lines for Phase 5 work carry an activity prefix:

```
[wave 1 of 1 · running 5 subtasks]                         # wave start
[wave 1 of 1 · running 2 subtasks · 3 subtasks done]       # mid-wave
[wave 1 of 1 · 1 subtask in conformer · 4 subtasks done]   # last subtask in advisory phase
[wave 1 of 1 · 5 subtasks done]                            # wave fully settled
```

The prefix is built from three per-wave counters, each its own
` · `-separated segment when non-zero (zero-count segments omitted, so
`0/M`-style fragments never appear): **`running N subtask(s)`** (implementer
not yet at terminal status — no entry in `subtask_status[sid]`, or value not
in `_TERMINAL_STATUSES = {complete, failed, blocked}`); **`N subtask(s) in
conformer`** (implementer reached `complete`, advisory conformer phase still
in flight); **`N subtask(s) done`** (implementer settled and, if `complete`,
conformer also wrapped; or implementer hit `failed`/`blocked` — always
rendered last).

The wave header `wave W of V` is the 1-based current wave index and total
wave count, restricted to the current wave's membership
(`waves[completed_waves]`), not the whole run. Singular/plural is rendered
on the count (`1 subtask` vs `5 subtasks`).

Built by `_get_progress`; emitted only after Phase 3 schedules the waves,
which is why classifier/planner/reconciler log lines have no prefix.
Post-wave-loop workers (`summarizer`, `pr_writer`,
`_run_final_conformance`) also emit no prefix.

`_invoke` takes `progress` as a callable, not a spawn-time snapshot, and
calls it per stream event — so a long-running worker's prefix advances
as siblings complete rather than carrying a frozen snapshot.

#### Rejected-payload diagnostic

`_read_stream` latches the input of every `StructuredOutput` tool_use into
`last_structured_payload` (rendered by `_format_payload_for_log`, capped at
`_REJECTED_PAYLOAD_LOG_MAX = 4000` chars, degrading to `repr` if
`json.dumps` raises). When a subsequent tool_result is an errored **schema**
rejection (`_is_schema_rejection`, matching `does not match required schema`
or `inputvalidationerror` case-insensitively), the latched payload is logged
beside the rejection, then cleared so a later unrelated failure can't
re-print a stale payload.

Emitted at every verbosity (a failure diagnostic, not per-event activity),
gated narrowly so an ordinary tool failure never drags an unrelated
structured payload into the log. The rejection text names offending fields
but never echoes what was submitted, so this closes that gap for the
parseable-but-invalid case (the unparseable-JSON `InputValidationError`
path already logged its payload). Pinned by
`tests/test_rejected_payload_logging.py`.

#### Blocked-planner gap diagnostic

`_format_blocked_gap(confidence) -> str` renders a blocked planner's
stated gap for `phase_plan`'s per-category summary line, capped at
`_BLOCKED_GAP_LOG_MAX = 400` chars with a visible `… [truncated; see
log]` marker. Whitespace is collapsed (an embedded newline could split a
one-line summary across rows) and the result truncated —
`confidence.basis` runs a median of ~1.1k characters and up to 4.3k
across real planner submissions. The full text stays in the per-worker
log. Returns `""` rather than `None` for absent/empty/malformed input,
so the caller interpolates an empty gap instead of the string `"None"`.
Pinned by `tests/test_schedule_blocked.py`.

Resolution: `--verbosity LEVEL` CLI (`quiet`/`normal`/`stream`/`debug`;
argparse rejects anything else) > `-v`/`-vv`/`-q`/`-qq` shortcuts (these
anchor to `normal`, not to the resolved default, so `-v` always means "show
me the streaming feature" and `-q` always means "back to the pre-streaming
terse output", independent of env-var/TOML defaults) > `LEERIE_VERBOSITY`
env > `leerie.toml` `verbosity = "stream"` > default `stream`
(`VERBOSITY_DEFAULT`).

An invalid value in env or file is rejected at startup via `die()`.
Errors always emit at every level (clig.dev "errors emit at every
level" anti-pattern guard) — `quiet` does NOT suppress error
messages, only the per-event chatter.

The resolved value lives on `st.data["verbosity"]` and is
re-resolved fresh on every run, including `resume` — the user
can dial up or down at resume time without editing state.

### Inspect directories

Extra directories the inspect-bucket workers (classifier, planner,
reconciler, plan_overlap_judge, provision) may read. Forwarded to each
`claude -p` invocation as one `--add-dir` flag per entry. Use this when a
task references a sibling repo outside the current repo cwd — without
`--inspect-dir ~/src/enric/beacon`, the classifier and planner can't
`Read`/`Grep`/`Glob` that path, and the workspace sandbox blocks a
fallback to `ls`/`find` even though `INSPECT_TOOLS` allowlists those
verbs.

Resolution: `--inspect-dir PATH` CLI (repeatable) > `LEERIE_INSPECT_DIRS`
env (colon-separated) > `leerie.toml` `inspect_dirs =
"/abs/path/a,/abs/path/b"` (comma-separated, parsed by `_read_toml_key`) >
default `[]` (no extra directories).

Paths are expanded (`~` → `$HOME`) and resolved to absolute form at
startup. Duplicates are removed. The resolved list lives on
`st.data["inspect_dirs"]` and is re-resolved fresh on every run,
including `resume`, so the user can add or remove paths without
editing state.

This applies only to inspect-bucket workers. Acting workers
(implementer, integrator, conformer) run inside the wave's worktree.
Those workers have `--dangerously-skip-permissions` and operate on the
worktree copy, not the user's wider filesystem — `--add-dir` is
unneeded.

### Telemetry

Telemetry is **always on and not configurable** — there is no enable flag. Per
DESIGN §14, the orchestrator unconditionally writes a per-run append-only
`calls.ndjson` (one JSON record per `claude -p` call) at the run root
`<state-root>/runs/<run-id>/calls.ndjson`, plus a `memory.ndjson` resource-usage
sampler and a `telemetry` aggregate block in `state.json`
(`{calls, cost_usd, input_tokens, output_tokens}`). All three live under
`<state-root>/` (outside the repo), so no `.gitignore` entry is needed. The
per-record schema is specified in §10; consumers are the `judge`/`heal` phases
(§14) and the `--report` verb (below).

### Judge output directory

The subdirectory name (relative to `<run-dir>`) where LLM judge output files
are written.

Resolution order (highest priority first):

Resolution: `--judge-dir DIR` CLI > `LEERIE_JUDGE_DIR` env > `leerie.toml`
`judge_dir = "judge-out"` > default `"judge-out"` (`JUDGE_DIR_DEFAULT`).

### Heal output directory

The subdirectory name (relative to `<run-dir>`) where LLM self-heal loop
output files are written.

Resolution: `--heal-dir DIR` CLI > `LEERIE_HEAL_DIR` env > `leerie.toml`
`heal_dir = "heal-out"` > default `"heal-out"` (`HEAL_DIR_DEFAULT`).

### Judge model

The `claude` model alias used when the judge skill spawns a worker to score a
batch of captured calls against a 3-dimensional rubric. `judge` is absent
from `MODEL_DEFAULT_PER_WORKER` and falls through to the global
`MODEL_DEFAULT` (`sonnet`), same as every other worker per CLAUDE.md's
model-default policy.

Resolution: `--judge-model MODEL` CLI > `LEERIE_MODEL_JUDGE` env >
`leerie.toml` `model_judge = "opus"` > default `"sonnet"` (`MODEL_DEFAULT`;
`judge` is absent from `MODEL_DEFAULT_PER_WORKER`).

### Heal model

The `claude` model alias used when the self-heal skill spawns workers for
patch generation and patched-arm replay.

Resolution: `--heal-model MODEL` CLI > `LEERIE_MODEL_HEAL` env >
`leerie.toml` `model_heal = "sonnet"` > default `"sonnet"`
(`MODEL_DEFAULT_PER_WORKER["heal"]`).

### PR-writer model

The `claude` model alias used at finalize time by the `pr_writer` worker,
which reads the target repo's PR template (if any), the run's commit
log, and a sampled diff, then emits `{title, body, used_template}`. The
host launcher reads the result from `run.json` and passes it to
`gh pr create`.

Resolution: `--pr-writer-model MODEL` CLI > `LEERIE_MODEL_PR_WRITER` env >
`leerie.toml` `model_pr_writer = "sonnet"` > default `"sonnet"`
(`MODEL_DEFAULT_PER_WORKER["pr_writer"]`).

### PR template selector

When the target repo has multiple PR templates inside a
`PULL_REQUEST_TEMPLATE/` directory, leerie picks the alphabetically first
`.md` by default. A repo-specific override selects a different basename
(with or without the `.md` suffix). Has no effect when the repo has a
single top-level template (e.g. `.github/pull_request_template.md`) or
no template at all.

Resolution: `--pr-template NAME` CLI > `LEERIE_PR_TEMPLATE` env >
`leerie.toml` `pr_template = "bug"` > default: alphabetically first `.md`
in the discovered directory.

An override that does not match an existing template is **not fatal** —
finalize must not block over a cosmetic preference — leerie logs a
warning and falls back to the alphabetical default.

### PR base branch override

The final branch a run's PR merges into defaults to `working_branch`
(the branch checked out when the run started). Distinct from the diff
fork-point, which always stays `working_branch` regardless of this
override — overloading `working_branch` for both roles would corrupt the
diff base if the override branch weren't the actual fork point.

Resolution (via `resolve_pr_base_branch`, mirroring `resolve_pr_template`'s
`_resolve_str_pref` delegation): `--pr-base-branch BRANCH` CLI >
`LEERIE_PR_BASE_BRANCH` env > `leerie.toml` `pr_base_branch =
"release/1.0"` > default: `working_branch`.

The resolved value is written to `state.json` and `run.json` as
`pr_base_branch`, alongside the unmodified `working_branch`.

`scripts/host-finalize.sh`'s `host_finalize` (the sole `gh pr create`
call site) reads `run.json.pr_base_branch` and passes it to
`gh pr create --base`, falling back to `working_branch` when the field
is absent (a run finalized before this field existed). The
origin-nonexistence default-branch fallback (base branch
deleted/renamed on origin) operates on this resolved base, same as it
always did for `working_branch`.

### PR-writer payload caps

The `pr_writer` worker's entire user prompt (task text, classification,
subtask titles, full commit log, diff stat/dirstat, sampled diff, and
the PR template body, serialized as one JSON string) is fed to
`claude -p` over stdin, not argv (§3 "User prompt transport"), so it's
not bound by Linux's per-argument `MAX_ARG_STRLEN` (131,071 bytes) the
way an argv-passed prompt would be.

Three constants in `orchestrator/leerie.py` still cap the unbounded
fields, purely to bound the worker's LLM context rather than to defend
an argv ceiling. Each capped field gets an in-band `... [<label>
truncated at ~N KB; remainder omitted — rely on the commit log] ...`
sentinel so the worker can see the truncation and avoid fabricating
detail past the cut-off.

| Constant | Default | Bounds |
|----------|---------|--------|
| `PR_WRITER_COMMIT_LOG_MAX_BYTES` | 80,000 | full `git log --no-merges` between `working_branch` and `run_branch` |
| `PR_WRITER_TEMPLATE_MAX_BYTES`   | 32,000 | contents of the resolved PR template file |
| `PR_WRITER_DIFF_SAMPLE_MAX_LINES`| 500    | sampled `git diff` hunks (line-capped because individual diff lines can be long and breaking one mid-line would render the surrounding hunk unreadable) |
| `PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES` | 8,000 | serialized JSON length of the `final_conformance` payload field. Enforced inside `_final_conformance_payload` by trimming `warnings` (then `residuals`) from the tail; at least one of each is preserved and a `truncated: true` marker is added when trimming fired |

These are **module constants, not `DEFAULT_CAPS` entries**, by design:
`DEFAULT_CAPS` is the surface for user-tunable run-wide operational caps
(`max_total_workers`, `worker_timeout_sec`, `worker_memory_max_bytes`,
etc.), while the PR-writer caps are internal protocol limits bounding a
single worker invocation's LLM context.
`tests/test_pr_writer_payload_cap.py::test_pr_writer_byte_budgets_defined`
pins the values.

Multi-byte UTF-8 safety: `_cap_text` slices at the byte boundary, then
back-decodes with `errors="ignore"` so the trimmed prefix never ends
mid-codepoint.

**`final_conformance` payload field** — when `_run_final_conformance`
produced a result, `_compose_pr_via_llm` reads
`st.data["conformance"]["_final"]` and adds a compact
`final_conformance` object with `{residuals, failed_axes, warnings}`
(plus an optional `truncated: true` marker). Omitted when the final
pass was skipped, crashed, or returned a fully clean result — absence
of the field is the cue that there's nothing advisory to say. Bounded
by `PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES` (8 KB), enforced in
`_final_conformance_payload` by trimming `warnings` (then `residuals`)
from the tail until it fits; at least one of each is preserved and the
`truncated` marker set.

### Heal-loop convergence parameters

Knobs governing the self-heal loop's iteration limit, pass-rate target, plateau
detection, and budget guard. All default values match Beacon's `DEFAULT_CONFIG`
(prior art at `scripts/heal-loop.ts:154`).

| Knob | CLI flag | Env var | TOML key | Default |
|------|----------|---------|----------|---------|
| Max iterations per call_type | `--heal-max-rounds N` | `LEERIE_HEAL_MAX_ROUNDS` | `heal_max_rounds = 10` | `10` (`HEAL_MAX_ROUNDS_DEFAULT`) |
| Success pass-rate threshold | `--heal-success-threshold F` | `LEERIE_HEAL_SUCCESS_THRESHOLD` | `heal_success_threshold = 0.9` | `0.9` (`HEAL_SUCCESS_THRESHOLD_DEFAULT`) |
| Plateau detection window | — | — | — | `3` (`HEAL_PLATEAU_WINDOW_DEFAULT`; not user-tunable) |
| Plateau minimum delta | — | — | — | `0.03` (`HEAL_PLATEAU_DELTA_DEFAULT`; not user-tunable) |
| Per-call_type replay count | — | — | — | `5` (`HEAL_N_REPLAYS_DEFAULT`; not user-tunable) |

The plateau window, plateau delta, and replay count are not currently exposed
as CLI/env/TOML knobs — they are implementation constants. Only the user-facing
knobs (`--heal-max-rounds`, `--heal-success-threshold`) are CLI/env/TOML
resolvable. Resolution for both follows the standard precedence: CLI flag →
env var → `leerie.toml` → default.

### Model selection

Every worker shells out to `claude -p`. The model passed via `--model` to that
subprocess is resolved per worker type. **The preflight smoke test is included**:
it is handed `models["classifier"]` — the tier the run's first worker actually
spawns with, so it honours `--model` / `--model-classifier` / `LEERIE_MODEL`
instead of ignoring them, and gives `_model_arg` an argument on which to append
`[1m]` (the strict-proxy lowered-ceiling case, which this call site reaches
first). Valid values: `sonnet` | `opus` | `haiku` (aliases — the `claude` CLI
resolves them to the current model version).

**Per-worker defaults: Sonnet 5 for both judgment and implementation.** Every
worker — judgment (classify, decompose, reconcile cross-domain coupling,
detect cross-planner overlap, resolve merge conflicts behaviorally, check
criteria, score captured calls) and workhorse alike — defaults to Sonnet. See
DESIGN §5 *Opus-judgment, sonnet-workhorse (historical)* for why a
judgment/workhorse split once existed and why it no longer applies.

| Worker       | Default | Why |
|--------------|---------|-----|
| classifier   | sonnet  | global judgment over the task description |
| planner      | sonnet  | decomposition is the load-bearing judgment step |
| reconciler   | sonnet  | cross-domain tag equivalence is judgment |
| plan_overlap_judge | sonnet | surface-overlap detection over the reconciled plan is judgment (two planners extracting the same artifact with incompatible APIs — DESIGN §5 *Cross-domain surface overlap*) |
| satisfied_probe | sonnet | per-subtask "already met on base tree?" check (DESIGN §8 *Already-satisfied subtask elimination*); runs once per subtask so throughput dominates — a **deliberate, documented cost tradeoff**, not a claim it needs no judgment. False-positive risk is contained by base-tree-only tool scope + conservative prompt, not model tier |
| provision    | sonnet  | fallback when the deterministic lockfile-detection table returns empty (DESIGN §6½); judgment over arbitrary repo shapes |
| integrator   | sonnet  | behavioral conflict resolution; a wrong merge silently corrupts integrated state |
| implementer  | sonnet  | concrete subtask execution; also pinned to `low` effort (see "Effort selection" below) — cost/latency, not a judgment-tier change |
| conformer    | sonnet  | reads a diff and runs commands; also pinned to `low` effort — same rationale as implementer |
| judge        | sonnet  | scoring a batch of captured calls against a 3-dimensional rubric |
| heal (patch) | sonnet  | patch generation and replay; throughput matters more than broad judgment |
| pr_writer    | sonnet  | finalize-time PR title + body; fills repo template when present, summarizes commits otherwise |
| dep_capture  | sonnet  | finalize-time dep inference from worker logs; broad judgment over arbitrary shell command sets |
| fit_judge    | sonnet  | P1 Task-Context Fit scoring is judgment |
| splitter     | sonnet  | LLM-driven structural partition (coupled-minority path) is judgment |
| adherence_judge | sonnet | plan-instruction-adherence scoring is judgment; empirically calibrated (goal-only task ⇒ high score, prescribed-and-violated ⇒ low score). If gating regresses, re-run calibration and consider `--model-adherence-judge opus` before reintroducing a blanket tier split |
| classification_judge | sonnet | independent adversarial verifier of the classifier's category set (DESIGN §8 *Independent adversarial verification*) |
| wiring_judge | sonnet | independent adversarial verifier of the plan's semantic wiring — dangles a structural `check_plan_wiring` scan cannot see (DESIGN §5, §8) |
| provision_judge | sonnet | independent adversarial verifier of the detected install recipe vs. the actual image/runtime (DESIGN §6½, §8) |
| artifact_registry | sonnet | pre-planning canonical-vocabulary worker (DESIGN §5 *Artifact-registry worker*) — decides one canonical tag+path per artifact |
| task_coverage_judge | sonnet | independent adversarial verifier of plan-vs-task coverage (DESIGN §8); wired into `phase_planning_coverage_gate` |
| integration_judge | sonnet | independent adversarial verifier of the integrator's merge for behavioral correctness (DESIGN §8); wired into `integrate_wave` as a post-merge-commit detect-and-die gate (`die()`s on non-empty `defects`) |
| rebaser      | sonnet  | finalize-time rebase worker (DESIGN §6 *Finalization*) — a scoped, fully-agentic exception to §12: does the entire rebase workflow itself, mirroring `integrator` |

`MODEL_DEFAULT` is the global default (`sonnet`); `MODEL_DEFAULT_PER_WORKER`
lists `implementer`, `conformer`, `heal`, `pr_writer`, and `satisfied_probe`
explicitly (all `sonnet` — matching the global default today, but kept as
explicit per-worker entries since they predate this change and may need to
diverge again later). `dep_capture`, `fit_judge`, `splitter`, `judge`,
`adherence_judge`, `classification_judge`, `wiring_judge`, `provision_judge`,
`artifact_registry`, `task_coverage_judge`, `integration_judge`, and
`rebaser` are **absent** from `MODEL_DEFAULT_PER_WORKER` — their `sonnet`
defaults come from the global `MODEL_DEFAULT` fallback.

Resolution order for each worker type `W` (highest priority first):

1. **`--model-<W>`** CLI flag (e.g. `--model-implementer opus`)
2. **`--model`** CLI flag (sets the global default for this run)
3. **`LEERIE_MODEL_<W>`** env var (e.g. `LEERIE_MODEL_IMPLEMENTER=opus`)
4. **`LEERIE_MODEL`** env var (sets the global default)
5. **`model_<w>`** key in `leerie.toml`
6. **`model`** key in `leerie.toml`
7. **Per-worker default** from `MODEL_DEFAULT_PER_WORKER`
8. **Global default `MODEL_DEFAULT`** (`sonnet`)

Nineteen worker types (`WORKER_TYPES`, plus the global override), each
independently overridable via the mechanical pattern `LEERIE_MODEL_<WORKER>` (env) /
`--model-<worker>` (CLI) / `model_<worker>` (TOML): classifier, planner,
reconciler, plan_overlap_judge, satisfied_probe, provision, implementer,
integrator, conformer, fit_judge, splitter, adherence_judge,
classification_judge, wiring_judge, provision_judge, task_coverage_judge,
integration_judge, artifact_registry, rebaser. Global override:
`LEERIE_MODEL` / `--model` / `model`.

`judge`, `heal`, `pr_writer`, and `dep_capture` are post-run / finalize-time
workers invoked outside the main orchestrate loop, so they don't follow that
pattern: `judge`/`heal`/`pr_writer` have dedicated CLI flags instead
(`--judge-model`, `--heal-model`, `--pr-writer-model`, with env vars
`LEERIE_MODEL_JUDGE`/`LEERIE_MODEL_HEAL`/`LEERIE_MODEL_PR_WRITER` and TOML
keys `model_judge`/`model_heal`/`model_pr_writer`); `dep_capture` has
**neither a CLI flag nor a `leerie.toml` key** — env var
`LEERIE_MODEL_DEP_CAPTURE` only. All four still honor the global
`--model` / `LEERIE_MODEL` override.

An invalid value in env or file is rejected at startup via `die()`. CLI
values are validated by argparse `choices=` and rejected with the standard
argparse error.

**Cost note:** every worker now defaults to Sonnet. A user who wants a
specific judgment worker on Opus (e.g. to re-check a regression against
the historical judgment-tier baseline) can still opt in per worker with
`--model-<worker> opus` / `LEERIE_MODEL_<WORKER>=opus`, or globally with
`--model opus` / `LEERIE_MODEL=opus`.

Models are not persisted in `<state-root>/state.json`. On `resume`, models are
re-resolved from the current environment, so changing `LEERIE_MODEL` between
the original run and the resume is intentional and takes effect.

### Effort selection

The `claude -p` CLI exposes `--effort {low,medium,high,xhigh,max}` to dial
reasoning depth. Leerie pins effort per worker so judgment workers think to a
consistent depth across runs — the previous behavior (no `--effort` flag,
worker inherits whatever the user's Claude settings happen to default to)
was a hidden source of cross-run variance in subtask count and other
judgment-shaped outputs.

The `claude -p` CLI exposes **no `--temperature` and no `--seed`**, so
sampling stochasticity cannot be pinned. Effort is the strongest dial
available; it does not eliminate run-to-run variance but does remove the
"this run thought harder than that one" axis.

**Per-worker defaults: `medium` for judgment workers, `low` for the
code-writing acting workers, unset for post-run skill workers.**
`implementer`/`conformer` previously defaulted to *unset* (inheriting
Claude's own reasoning depth) so their effort stayed bounded by their own
evidence gates (DESIGN §8); that tradeoff is now overridden in favor of a
fixed low-effort ceiling. `judge`/`heal` remain *unset* — when no effort is
resolved, no `--effort` flag is passed and the worker inherits Claude's
default.

`medium` (rather than `high`) keeps per-run OTPM (output tokens per minute)
rate-limit pressure down; Leerie's downstream checks (confidence gate,
conformer, adherence gate, overlap judge, `_run_checked_loop` retries) absorb
the small per-worker quality reduction. `high`/`xhigh`/`max` remain available
per-worker via the override chain below when a specific worker needs deeper
reasoning.

Per-worker rationale mirrors the "Why" column of the model-selection table
above (same judgment-vs-throughput reasoning); only the resolved depth
differs:

| Worker       | Default | Notes (where it diverges from the model-table rationale) |
|--------------|---------|-----|
| classifier, planner, reconciler, plan_overlap_judge, provision, integrator, pr_writer, dep_capture, fit_judge, splitter, adherence_judge, classification_judge, wiring_judge, provision_judge, task_coverage_judge, integration_judge, artifact_registry, rebaser | medium | judgment/finalize workers; `medium` is the reproducibility dial, not a cost one |
| implementer, conformer | low | code-writing workers; pinned low for cost/latency — a deliberate override of the prior "bounded by §8 evidence gate" unset default, since the conformer/confidence-gate loops downstream absorb the quality tradeoff |
| satisfied_probe | unset | per-subtask advisory prune; base-tree-only tool scope + conservative default carry correctness, not pinned depth |
| judge, heal  | unset   | post-run scoring/patching; no need to pin |

Two calibrated thresholds worth noting: `adherence_judge` (goal-only task ⇒
≥8.5, prescribed-and-violated ⇒ ≤3) and `fit_judge` (0.70) — raise the
relevant worker's effort via its override (e.g. `effort_adherence_judge`)
before reintroducing a blanket tier split if a gate regresses under `medium`.

`EFFORT_DEFAULT` is `None` (meaning "don't pass `--effort`");
`EFFORT_DEFAULT_PER_WORKER` overrides it per the table above — `"medium"`
for the seventeen judgment/finalize workers, `"low"` for `implementer` and
`conformer` (a distinct, cost-motivated pin rather than a
judgment-reproducibility one).

Resolution order for each worker type `W` (highest priority first), mirroring
model selection:

1. **`--effort-<W>`** CLI flag (e.g. `--effort-planner max`)
2. **`--effort`** CLI flag (sets the global default for this run)
3. **`LEERIE_EFFORT_<W>`** env var (e.g. `LEERIE_EFFORT_PLANNER=max`)
4. **`LEERIE_EFFORT`** env var (sets the global default)
5. **`effort_<w>`** key in `leerie.toml`
6. **`effort`** key in `leerie.toml`
7. **Per-worker default** from `EFFORT_DEFAULT_PER_WORKER`
8. **Global default `EFFORT_DEFAULT`** (`None` — flag omitted)

Same mechanical pattern as model selection: `LEERIE_EFFORT_<WORKER>` (env) /
`--effort-<worker>` (CLI) / `effort_<worker>` (TOML), for the same nineteen
worker names listed above under model selection. Global override:
`LEERIE_EFFORT` / `--effort` / `effort`.

`judge`, `heal`, `pr_writer`, and `dep_capture` are post-run / finalize-time
workers not in `WORKER_TYPES`; they receive no per-worker effort override (no
dedicated env var, CLI flag, or TOML key). They do honor the global
`--effort` / `LEERIE_EFFORT` override.

An invalid value in env or file is rejected at startup via `die()`. CLI
values are validated by argparse `choices=`. A worker that resolves to `None`
(no override and no per-worker default) produces the exact same CLI as
before this feature landed — zero behavior change for unconfigured workers.

Efforts are not persisted in `<state-root>/state.json`. Like models, on `resume`
they are re-resolved from the current environment.

### The `--answers` file

A JSON object keyed by classifier-assigned question `id`. Optionally
includes a `source_of_truth` key set to `"codebase"`, `"research"`, or
`"both"` to override the resolved preference for this run:

```json
{ "q1": "answer text", "source_of_truth": "codebase" }
```

Maps to `DESIGN.md`: §11 (clarification procedure).

### Chain verbs

Chain orchestration is implemented as a **laptop-side wave
sequencer** in the `leerie` launcher (DESIGN.md §19). A chain is N
parallel copies of today's single-run `--runtime fly` flow per
wave, with synth-merge between waves to build the next wave's base
branch. The laptop is the sequencer; there is no Fly coordinator
machine, no per-chain SQLite, no 6PN HTTP.

The primary verb is `leerie chain`. Chain-scoped verbs
(`status`, `stop`, `kill`, `resume`, `finalize`,
`attach`) detect UUID-formatted positional arguments and dispatch
by iterating `$LEERIE_STATE_HOST_DIR/runs/*/run.json` for runs with
matching `chain_id`.

| Verb | Behavior |
|------|----------|
| `leerie chain [--chain-id <uuid>] [<per-job-flags>] --wave <files> [--wave <files>] ...` | Wave-sequencer loop. Any flags not consumed by `chain`'s own parser (`--wave`, `--chain-id`, `--target`) are collected into a passthrough array and forwarded to each per-job `./leerie` invocation — so `--effort high`, `--model opus`, `--dangerously-skip-permissions`, etc. work the same as on a single run. Mints a fresh `chain_id` (UUID) unless `--chain-id <prior-uuid>` is supplied (in which case the prior chain's `chain_id` is reused so the wave-loop idempotency check skips already-pushed waves — see "Chain helpers" subsection below). For each wave N: if every wave-N run is already pushed (`_wave_already_done`), skip fan-out; else checks out `current_base` in `$USER_REPO` and fans out N background `./leerie "$prompt" --runtime fly <passthrough> --chain-id <id>` per prompt file, waits for all to finalize on the laptop (existing single-run path: `provision_machine` → `seed-auth.sh` → `seed-repo.sh` → orchestrator → `decide_teardown` trap → `fetch_branch` → `host_finalize` → `destroy_machine`), tags each finalized `run.json` with `chain_id` + `wave_idx` via `update_run_json`. Either way, gathers wave-N branches via `_wave_branches`, synth-merges into `leerie/stage/<chain-id>-wave-<N+1>` via `chain.git_ops.synth_merge_branches`, pushes the stage branch to origin, advances `current_base`. Trap handler `_ch_kill_wave` propagates SIGINT/SIGTERM to all in-flight wave children. |
| `leerie status <chain-id>` | Iterates run.json files, filters by `chain_id`, renders one row per matched run (wave, run_id, status, branch, notes). Status derived from run.json fields (`pushed_at` / `paused_at` / `killed_at` / `finished_at`). |
| `leerie attach <chain-id>` | Polls run.json files every 5s; exits 0 when every chain run is in a terminal state (`pushed_at` / `paused_at` / `killed_at` / `sync_failed_at`). |
| `leerie kill <chain-id>` | Enumerates run.json files with matching `chain_id` whose machines aren't already destroyed (`killed_at` is null), invokes `leerie kill <run-id>` per discovered run. Idempotent. |
| `leerie stop <chain-id>` | Enumerates runs that are actively running (have `fly_machine_id`, no terminal state), invokes `leerie stop <run-id>` per discovered run. |
| `leerie resume <chain-id>` | Two tiers: (1) auto-resumes paused runs (`paused_at` set, not `killed_at`) by invoking `leerie resume <run-id>` per discovered run; (2) lists still-running runs (have `fly_machine_id` + `chain_id`, no terminal state) with machine IDs so the user can reattach via `leerie resume <machine-id>`. Running runs are discoverable because the child writes `chain_id` into host-side `run.json` immediately after provisioning (early-write), before the orchestrator starts. After paused runs complete, the user re-invokes `leerie chain --chain-id <chain-id> --wave ...` to continue the wave loop from where it stopped. |
| `leerie finalize <chain-id>` | Enumerates runs that haven't been pushed yet (`pushed_at` null, not `killed_at`), invokes `leerie finalize <run-id>` per discovered run. |
| `leerie list chains` | Iterates run.json files, groups by `chain_id`, renders one row per chain (chain_id, status, pushed/total, wave count, started_at). |

Non-UUID positional ids fall through unchanged to the existing
single-run code paths. UUID detection uses the `8-4-4-4-12` hyphen
pattern.

**Test seam**: chain-scoped verbs use `${LEERIE_SELF_CMD:-"$0"}` for
the per-run recursive invocation, so tests can substitute a stub
binary via the `LEERIE_SELF_CMD` env var without faking `$0`. See
`tests/test_chain_launcher_id_dispatch.py`.

Chain verbs do NOT require `FLY_API_TOKEN`, `GH_DISPATCH_PAT`,
`LEERIE_CHAIN_IMAGE`, or `LEERIE_WORKER_IMAGE` — there is no
coordinator to provision. The per-job `./leerie --runtime fly`
invocations have their own env requirements unchanged.

#### Per-job lifecycle

Each wave job is a normal single-run `--runtime fly` invocation:

1. **Provision.** `scripts/remote/provision.sh::provision_machine` creates a Fly machine, writes `fly-machine.json` + `$LEERIE_STATE_HOST_DIR/remote/<launcher-pid>.json` right after `flyctl machine run` succeeds.
2. **Seed.** `scripts/remote/seed-auth.sh` + `seed-repo.sh` ship the laptop's Claude credentials + git identity + working tree via `flyctl ssh console` tar pipe. `seed-auth.sh:149-158` excludes git-push credentials by design — workers never see them.
3. **Orchestrate.** The orchestrator runs the standard classify → plan → execute → finalize phases on the worker.
4. **Decide teardown.** When the orchestrator exits, the launcher's `decide_teardown` trap fires on the LAPTOP (the worker's exit propagates via the SSH session's tail wrapper). The trap calls `fetch_branch` (pulls bundle + run-state), `host_finalize` (pushes branch + opens PR), `destroy_machine` (Fly DELETE).

The chain wave loop catches each per-job exit via `wait`. The launcher_pid
recorded in `$LEERIE_STATE_HOST_DIR/remote/<pid>.json` is `$!` from the
parent's background spawn, letting the wave loop discover each child's
`fly_machine_id` (= run_id) and tag the run with `chain_id` / `wave_idx`.

#### chain_id discovery for chain-scoped verbs

The `chain_id` (UUID minted by `chain`) is written into each
chain run's `run.json` by the wave loop AFTER `host_finalize`
completes for that run. The launcher's `update_run_json` bash
helper (`scripts/remote/lib.sh:42`) merges the field atomically into
the existing JSON.

The tagging loop discovers each child's machine ID via two paths (tried in
order): **primary** `remote/<child-pid>.json` — the PID-keyed pointer
written by `provision.sh` during provisioning; **fallback** scan
`runs/*/fly-machine.json` for a matching `launcher_pid` field, for when the
pointer file is absent (e.g. older images whose `destroy_machine()` deleted
it before the parent could read it).

All chain-scoped verbs operate by iterating
`$LEERIE_STATE_HOST_DIR/runs/*/run.json`, parsing each with
`json.load`, and filtering by the `chain_id` field. The standard
`for run_json in "$LEERIE_STATE_HOST_DIR"/runs/*/run.json` glob
(established in `leerie:3330-3347` for auto-finalize) is the shared
discovery pattern.

#### Chain helpers (launcher bash)

Three private launcher helpers near `_json_get` implement the
discovery + idempotency primitives the wave loop and chain-scoped
verbs build on. Each runs a self-contained `python3 - … <<'PY'`
heredoc against `$LEERIE_STATE_HOST_DIR`; none access global bash
state besides `$LEERIE_STATE_HOST_DIR`. Args come through positional
parameters (no env interpolation into Python source).

| Helper | Args | Contract |
|--------|------|----------|
| `_wave_already_done <chain_id> <wave_idx> <n_expected>` | UUID, integer, integer | Exits 0 iff `n_expected` runs are tagged with `chain_id` + `wave_idx` AND every matching run has `pushed_at` set. Used by the `chain` wave loop to skip fan-out on a resume submission. |
| `_wave_branches <chain_id> <wave_idx>` | UUID, integer | Emits one branch-name per line for every matching run. Used by the wave loop to gather wave-N branches for synth-merge (works for both the just-fanned path and the resume path). |
| `_resolve_volume_id_from_run_dir <run-dir>` | Run directory | Emits the run's `volume_id` (or nothing). Reads `fly-machine.json` then `run.json`, **continuing when a file exists but carries no `volume_id`** — `provision.sh` writes `volume_id` to `fly-machine.json` only conditionally (`if vol_id:`) while always writing it to `run.json`, so returning on mere file existence skipped `run.json` and leaked the volume. |
| `_resolve_volume_id_from_fly <machine-id> <app>` | Machine id + Fly app | Emits the volume mounted by that machine, by asking Fly: `flyctl machine list --app <app> --json` → `.[] \| select(.id==<mid>) \| .config.mounts[].volume`. The fallback for `kill --machine-id <id>` when no sidecar exists (the orphan path the usage hint advertises); without it the machine is destroyed and its volume bills forever. **Must be called before `destroy_machine`** — the volume→machine link (`attached_machine_id`, and the machine's own `config.mounts`) vanishes with the machine. Uses `machine list --json` because `machine status` has **no** `--json` flag (only `-d/--display-config`, which embeds JSON in prose); verified to keep reporting mounts while the machine is `stopped` (the `stop`-then-`kill` path). Best-effort: any failure emits nothing and returns 0, so `kill` still destroys the machine. |
| `_resolve_ec2_instance_id_from_run_dir <run-dir>` | Run directory | Emits the run's `ec2_instance_id` (or nothing). Reads `ec2-instance.json` then `run.json`, same "continue past a file that exists but carries no id" discipline as `_resolve_volume_id_from_run_dir`. Unlike Fly, the run-id is NOT the instance id for EC2 runs, so `kill`'s EC2 action always resolves it from a sidecar rather than assuming identity. |
| `_chain_runs_filter <chain_id> <verb>` | UUID, one of `stop`/`kill`/`finalize`/`resume`/`running` | Emits matching run-ids one per line. The `verb` parameter selects a hardcoded filter inside the heredoc (`stop`: machine running; `kill`: not yet destroyed; `finalize`: not yet pushed; `resume`: paused; `running`: active with chain_id, no terminal state). Used by the chain-scoped verb arms (`stop`/`kill`/`finalize`/`resume`) to enumerate runs for per-run dispatch. Returns rc=2 + `remote_log` error on unknown verb (bash-side assert; Python heredoc has its own `sys.exit(2)` backstop). |

The wave loop's tag-write step (`update_run_json … chain_id "$_ch_id"
wave_idx "$_wave_idx"`) fires BEFORE the failure-pause check so
runs that paused on failure still get tagged and are therefore
discoverable by `leerie resume <chain-id>` / `kill <chain-id>` /
etc. The `_ch_wave_pids` / `_ch_wave_child_pids` arrays reset at
the top of every wave iteration (above the `_wave_already_done`
check) so the SIGINT trap handler never sees stale entries from a
prior wave.

##### Resuming a chain via `chain --chain-id <uuid>`

`leerie chain --chain-id <prior-uuid> --wave …` pins the chain_id to a
prior chain's UUID instead of minting fresh. The wave loop's
`_wave_already_done` check then matches the prior chain's runs and skips
fan-out for already-pushed waves, advancing `current_base` through any
wave-staging branches already pushed to origin — the load-bearing recovery
path after `leerie resume <chain-id>` unpauses every paused run: the user
re-submits with `--chain-id <prior-uuid>` and the chain picks up at the
first not-yet-done wave.

The launcher normalizes the user-supplied chain_id to lowercase via `tr
'[:upper:]' '[:lower:]'` after UUID format validation (`UUID_PATTERN` is
case-insensitive, `grep -qiE`, so uppercase input passes validation; but
the wave-loop helpers compare `run.json`'s `chain_id` case-sensitively, and
`uuid.uuid4()` always emits lowercase). Without normalization, uppercase
`--chain-id` input would silently bypass idempotency and fork the chain
into two chain_ids — the v8 audit's S1 finding.

##### Synth-merge idempotency probe

Before invoking `chain.git_ops.synth_merge_branches` for wave N → N+1,
the wave loop probes origin via `git ls-remote --exit-code origin
leerie/stage/<chain-id>-wave-<N+1>`. If the stage branch already exists
(e.g. the user manually resolved a prior synth-merge conflict and
pushed), the wave loop fetches + checks out the existing branch instead
of re-running synth-merge — otherwise `synth_merge_branches`'s `git
checkout -B` would force-recreate the stage branch from `$current_base`,
discarding the resolved state and re-conflicting the same way.

#### Synth-merge between waves

After every wave-N job's `host_finalize` has pushed its branch to
origin, the wave loop runs synth-merge to build the next wave's
base branch:

```bash
python3 -c "
from chain.git_ops import synth_merge_branches, SynthMergeConflict
synth_merge_branches('$USER_REPO', '$current_base',
                     ['leerie/runs/...', ...],
                     'leerie/stage/<chain-id>-wave-<N+1>')
"
```

`synth_merge_branches` runs in `$USER_REPO`, does `git fetch
origin` + `git checkout -B <stage> origin/<base>` + sequential
`git merge --no-ff --no-edit origin/<branch>`. Conflicts raise
`SynthMergeConflict`; the wave loop catches and pauses the chain
with a clear message for manual resolution. The function works
unchanged from its v3 form — branches are on origin (each wave-N
job's `host_finalize` pushed it), so the `origin/<branch>`
references resolve.

After synth-merge, the wave loop pushes the stage branch to origin
so wave-N+1 workers can see it as their starting base.

#### Idempotent resume

If the user Ctrl-Cs mid-chain or any job fails, the wave loop exits
non-zero with a resume hint: `leerie resume <chain-id>` resumes every
paused run, then re-invoking `leerie chain --wave ...` picks the chain
back up (the idempotency check above skips waves whose runs are already
`pushed_at`). `pushed_at` is the canonical "this run is done, don't
re-spawn" sentinel — the same one `host_finalize` uses for push
idempotency, set after `git push -u origin <branch>` succeeds.

#### chain.git_ops surface (laptop-side)

`chain/git_ops.py` provides the git operations invoked by the wave
loop. Workers never invoke this module; all GitHub credential
touches happen on the laptop using its existing `gh auth` and
`~/.git-credentials`.

| Function | Purpose |
|----------|---------|
| `synth_merge_branches(repo, base_branch, dep_branches, stage_name)` | Build a stage branch by merging each dep branch into a fresh checkout of `base_branch`; raises `SynthMergeConflict` on any conflict. Used by the wave loop between waves. Passes `-c user.email=leerie-chain@bot.invalid -c user.name=leerie-chain` to `git merge` defensively so the merge commit succeeds even when the laptop's global git identity is unset (otherwise the merge would fail with "Committer identity unknown"). |
| `create_stage_branch(repo, chain_id, base_branch)` | Create (or check out, idempotently) the `stage-<chain_id>` branch off `base_branch`. |

Maps to `DESIGN.md`: §19 *Chain orchestration*.

### Run-group verbs

Run-group orchestration launches N ordinary single-repo leerie runs
together as a coordinated unit, sharing a `group_id` (DESIGN.md §20).
Each member is an unchanged, fully isolated run with its own
basename-keyed state directory, its own run branch, its own PR, and
its own resume record. The group layer adds a shared brief,
read-only cross-repo visibility via `--inspect-dir`, deploy-ordering
notes, and group-scoped verbs — nothing else.

**Contrast with chains (§19):** chain-scoped verbs scan ONE state
directory for `chain_id`; group-scoped verbs must scan ACROSS the set
of member state directories (one per member repo basename). The two
subsystems are complementary in design spirit but do not share
discovery machinery — `_chain_runs_filter` (`leerie:191`) assumes a
single `$LEERIE_STATE_HOST_DIR` and cannot be reused directly.

#### `group_id` in `run.json`

`group_id` is an optional string field in `run.json`. It is written
at two points: (1) by the orchestrator at run-start when `--group-id`
is supplied as a CLI arg (`orchestrator/leerie.py:15061`), so the
field appears in `run.json` immediately when the run begins; and (2)
by the `group` launcher arm after all members complete, via
`update_run_json … group_id "$_group_id"` (the tag-back step in
`leerie`). The `chain_id` field follows the same pattern.
`_validate_run_json`
(`orchestrator/leerie.py:1994`) does not add any invariant check on
`group_id` — it is informational and orthogonal to the push/pause/kill
state machine. The field is accepted by the validator without error
because validators only check fields they know about (unknown keys pass
through).

#### Launcher `group` verb

```
leerie group \
  --repo <path> "<prompt>" \
  --repo <path> "<prompt>" \
  [--brief <file>] \
  [<per-member-flags>]
```

Modeled on the `chain` arm (`leerie:2033`). The `group` arm parses repeated
`--repo <path> "<prompt>"` pairs and an optional `--brief <file>`; fails
fast if any repo path is not a git repository (mirrors the chain
prompt-file check at `leerie:2136`); mints a `_group_id` (UUID, same
mechanism as `chain`'s `_ch_id`); then per member builds the prompt as
`<brief>\n\n<member prompt>`, appends `--inspect-dir <sibling-repo>` for
every other member, and backgrounds:

```bash
# resolved once, before any cd, to an absolute path:
_grp_self_cmd="${LEERIE_SELF_CMD:-$_grp_leerie_dir/$(basename "$0")}"
( cd <repo> && "$_grp_self_cmd" "<prompt>" <flags> \
    --group-id "$_group_id" ) &
```

(mirrors `leerie:2237-2246` for chains). Each `cd` makes the member resolve
its own `USER_REPO` and basename-keyed state directory independently. The
self-command **must** be absolutized *before* the `cd`: a relative `$0`
(e.g. `./leerie`) would not resolve once the subshell has `cd`'d into the
member repo — unlike chains, which never `cd`. Finally, waits for all
members (`wait`) and runs group tag-back (below).

**State-dir guard (mandatory).** The arm rejects or per-member-namespaces
any `--state-dir` / `LEERIE_STATE_DIR` override in the calling environment,
before any member is backgrounded. These override `_state_dir_default`
(`:431`) and would pin every member to one shared state directory, causing
a `.owner` collision on member 2. Chains (one repo) forward these safely;
groups (N repos) must not.

Per-member flags are forwarded like `_ch_passthrough` for chains.
`LEERIE_SELF_CMD` is the same test seam used by chain verbs — it still
takes precedence in `_grp_self_cmd`, so tests substitute a stub binary
via `LEERIE_SELF_CMD`.

#### Group tag-back across state directories (both runtimes)

After `wait`, the launcher writes `group_id` into each member's
`run.json`. Because each member runs in its own `$HOME/.leerie/<basename>/`
state directory, the launcher must discover each member's run directory
from its per-member state dir, not from `$LEERIE_STATE_HOST_DIR`.

The launcher knows each child's PID (`$!`) and repo path (→ basename →
state dir), so the discovery is:

| Runtime | Discovery mechanism |
|---------|---------------------|
| **Local** | After `wait` on a member, scan `~/.leerie/<member-basename>/runs/*/run.json` for the newest file with `finished_at` set (the `group` arm's tag-back loop in `leerie`). No cidfile read, no `--rm` race — the `run.json` is durably on disk by the time `wait` returns. |
| **Fly** | The existing `remote/<child-pid>.json` / `fly-machine.json` pointer path (`leerie:2263-2289`), applied per-member using the member's own state dir. The child's PID is `$!`; the member's state dir is resolved from its basename. |

After discovering each member's `run.json`, the launcher calls
`update_run_json … group_id "$_group_id"` (the same runtime-agnostic
atomic merge used by the chain wave loop, `scripts/remote/lib.sh:70`). No
new per-child pointer file is required: the durable `run.json`-on-disk is
the coordination artifact, consistent with how chains discover members.

#### Group-scoped verbs

| Verb | Behavior |
|------|----------|
| `leerie group --repo <path> "<prompt>" [--repo ...] [--brief <file>] [--group-id <uuid>]` | Fan-out launcher. Mints a fresh `group_id` unless `--group-id <prior-uuid>` is supplied. Fans out one backgrounded member invocation per `--repo`, waits, then runs group tag-back. |
| `leerie status <group-id>` | Iterates member state dirs (derived from the group's member repos or a group-manifest the launcher drops), filters `run.json` by `group_id`, renders one row per matched run (run_id, status, branch, notes). Same field-derived status as chain `status`. |
| `leerie stop <group-id>` | Discovers running Fly members across all member state dirs; invokes `leerie stop <run-id>` per discovered run. Fly-runtime only (pauses machines). |
| `leerie resume <group-id>` | Discovers paused members across all member state dirs; invokes `leerie resume <run-id>` per discovered paused run. |
| `leerie kill <group-id>` | Discovers non-destroyed members across all member state dirs; invokes `leerie kill <run-id>` per discovered run. Idempotent. |
| `leerie finalize <group-id>` | Discovers members not yet pushed across all member state dirs; invokes `leerie finalize <run-id>` per discovered run. |
| `leerie list --groups` | Iterates across all leerie state dirs under `$HOME/.leerie/`, groups `run.json` files by `group_id`, renders one row per group (group_id, status, member count). |

These verbs are dispatched by UUID detection (same `8-4-4-4-12` hyphen
pattern as chain verbs). A UUID that matches a `group_id` across member
state dirs is a group-scoped dispatch.

#### `_group_runs_filter`

The group-scoped verb implementations build on `_group_runs_filter`,
a private launcher helper that scans a **set** of state directories
(one per member repo basename) for `run.json` files tagged with a
given `group_id`. Signature:

```
_group_runs_filter <group_id> <verb> <state_dir_1> [<state_dir_2> ...]
```

Emits matching run-ids one per line, filtered by the same per-verb logic as
`_chain_runs_filter` (`stop` / `kill` / `finalize` / `resume` / `running`).
Key difference: `_chain_runs_filter` iterates one directory
(`$LEERIE_STATE_HOST_DIR/runs/*/run.json`); `_group_runs_filter` iterates
`<state_dir_N>/runs/*/run.json` for each supplied directory.

#### Deploy-ordering notes

When a member's planner declares a cross-repo prerequisite as
`requires.extent: external` (DESIGN.md §5), those entries accumulate
in `State.data["external_preconditions"]` (written at plan time,
`orchestrator/leerie.py:9727`). The entry shape is:
`{tag, reasons:[{sid, reason}], originating_subtasks}`.

The deploy-note plumbing threads `external_preconditions` from State
into the finalize path at three points, so the note survives regardless
of which finalize path fires:

1. **`_compose_pr_via_llm` payload** (`orchestrator/leerie.py:14590`):
   added as a field in the JSON payload passed to `pr_writer`, alongside
   `task`, `commit_log`, etc. The pr_writer prompt renders a
   "⚠ Deploy-ordering" section when the field is non-empty.
2. **`compose_pr_body` fallback** (`orchestrator/leerie.py:2119`): the
   deterministic Python fallback PR body renders the same section from
   state, covering the case where the `pr_writer` LLM worker fails or is
   skipped.
3. **`host-finalize.sh` bash fallback**: the pure-bash deterministic PR
   body (the LLM-less host-side finalize path, used when neither
   `pr_body` nor the Python fallback reached `run.json`) renders the
   identical section via `jq` — byte-for-byte matching the Python
   renderer's shape (`- **<tag>** — <reason>`, reasons `"; "`-joined,
   nothing emitted when absent/empty). No `run.json` persistence is
   needed — `external_preconditions` is already a `STATE_FIELDS` key.

#### Run-summary cost line

Both deterministic renderers also emit a `- Cost:` line in the
`## Run summary` block (after `- Workers:`), sourced from `state.json`'s
`telemetry` block: `- Cost: $X.XX (N calls, I in / O out tokens)`.
Rendered only when the telemetry block is present (omitted on
pre-classify orphans), matching the deploy-note guard.
`compose_pr_body` and the `host-finalize.sh` `jq` fallback both produce
2-decimal, thousands-grouped output and are format-identical except for
a sub-cent rounding difference on an exact half-cent `cost_usd` that
never arises on a real summed cost. No `run.json` persistence is needed
— `telemetry` is already a `STATE_FIELDS` key.

**Key design note:** `reason` in `external_preconditions` is
unstructured free text (`required` is only `[tag, extent]`,
`orchestrator/leerie.py:731`). The group launcher, not the planner,
knows which sibling repos are group members — so the deploy note
identifies sibling members by injected group membership, not by
parsing planner free-text.

#### Planner steering (`prompts/planner.md`)

When a group member's planner receives a group brief (a shared context
block prepended by the launcher, marked `## Group brief` or similar),
`prompts/planner.md` directs it to: (1) **read the sibling's contract** —
`Read`/`Grep`/`Glob` under `/inspect/<name>/` to locate the sibling's API
surface, types, schema, or interface files, not just the brief; (2)
**honor the interface** — subtasks must conform to the sibling's actual
types/field names/endpoints as found in the code; (3) **declare the
dependency** — add a `requires` entry with `extent: "external"` whose
`reason` names the sibling repo and the specific contract item, for every
subtask depending on a sibling-owned contract.

This is advisory steering per DESIGN.md §12 ("prompts advisory, code
enforces"): the write-confinement guarantee stays code
(`_filter_offtree_subtasks`), not the prompt. The instruction lifts
reliable cross-repo-aware planning from emergent to dependable.

The planner prompt also documents the runtime asymmetry: inspect-dir
read-only is kernel-enforced locally (`:ro` bind-mount) but
convention-enforced on Fly (`chown leerie:` in `seed-repo.sh`). The
practical guarantee is the same for planning — acting workers that get
`/inspect/` do not receive `--add-dir` on Fly either — but the
mechanism differs.

#### No new schema, state, or cap changes

This is the point of the lean shape (DESIGN.md §20 *Why the lean
shape*). The following are explicitly unchanged:

- `STATE_FIELDS` / `state.json` schema — `group_id` lives in
  `run.json` (the per-run sidecar), not in `state.json`.
- Subtask schema and planner schema — group members are ordinary runs.
- `DEFAULT_CAPS` — no new per-member cap; each member consumes from
  its own run's cap budget.
- `_filter_offtree_subtasks` (DESIGN.md §12) — the existing guard
  enforces write-confinement for siblings seeded as inspect-dirs,
  unchanged.
- Branch helpers (`new-worktree.sh`, `setup-run.sh`, `integrate.sh`,
  `finalize.sh`, `host-finalize.sh`) — each member's finalize runs
  its own existing `host_finalize` against its own repo.

Maps to `DESIGN.md`: §20 *Run groups (multi-repo)*.

---

## 2½. Configuration reference

Complete reference for every CLI flag, environment variable, and
`leerie.toml` key the orchestrator and launcher read. Moved here from
the README (2026-08-21) to keep the README a quickstart entry point;
see the README's *Configuration* section for a pointer back to this
table.

### CLI flags

| Flag | Default | Description |
|------|---------|-------------|
| `task` (positional) | — | The task description (literal string, or path to a `.txt`/`.md` file). Required unless the `resume` / `list` verbs or `--phase` is given. |
| `resume` (verb) | off | Resume an interrupted run. Auto-picks if exactly one run exists; pass the run-id if multiple. |
| `--run-id ID` | — | Select a specific run by id (e.g., for `resume` or `--phase` when multiple runs are in flight). |
| `list` (verb) | off | Enumerate in-flight and completed runs in this repository (run id, started, status, cost, branch). |
| `--no-push` | off | Skip the default push + PR at finalize. The run completes with the run branch local-only; your working branch is unchanged. Overrides `LEERIE_NO_PUSH` / `leerie.toml`. |
| `--no-verify` | off | Pass `--no-verify` to the finalize `git push` only (skips pre-push hooks). Worker commits inside worktrees still run all hooks. The user's explicit override per CLAUDE.md's hooks principle. |
| `--answers FILE` | — | JSON object of pre-supplied clarification answers (keyed by question `id`; may include `source_of_truth`). |
| `--clarify` | off | Opt into surfacing intent questions to the user. Default: questions are dropped after the classifier's codebase→research filter, and the implementer makes a documented best-effort decision. Also `LEERIE_CLARIFY` env var or `clarify = true` in `leerie.toml`. |
| `--max-workers N` | `2000` | Cap on total `claude -p` invocations across the run. Also `LEERIE_MAX_WORKERS` env var or `max_workers` in `leerie.toml`. |
| `--max-parallel N` | `5` | Cap on concurrent workers within a wave. Per-worker cgroup containment keeps an OOM inside one worker's cgroup; users on smaller VMs can opt down. Also `LEERIE_MAX_PARALLEL` env var or `max_parallel` in `leerie.toml`. |
| `--worker-memory-max SIZE` | auto | Per-worker cgroup memory cap (e.g. `4G`, `512M`). Bounds RAM the worker subtree may consume; OOMs stay inside the worker cgroup rather than cascading to sshd / orchestrator. Auto-derived when unset from the shared `leerie.slice` budget (`/proc/meminfo` only when no slice budget is readable). Raised automatically when the repo's own build/lint/test command declares a Node heap via `--max-old-space-size`; if you pin an explicit value **below** that declared heap plus headroom, leerie refuses at startup rather than issuing a cap that guarantees an in-cgroup OOM. Also `LEERIE_WORKER_MEMORY_MAX` or `worker_memory_max` in `leerie.toml`. |
| `--worker-timeout SEC` | `5400` (90 min) | Global per-worker wall-clock ceiling. Setting it **bypasses** the measured per-worker table (`TIMEOUT_DEFAULT_PER_WORKER`), which otherwise lowers the ceiling for fast worker types — e.g. `classifier` to 1236 s — using a duration distribution measured on one host. Raise it when a worker is being killed at a ceiling derived on a faster machine. A bypass rather than a bound, so an explicit value wins outright in both directions; explicitly passing the default counts as setting it. Also `LEERIE_WORKER_TIMEOUT` or `worker_timeout_sec` in `leerie.toml`. |
| `--confidence-rounds N` | `8` | Evidence-gate rounds the planner and implementer may run before exiting blocked (DESIGN §8). Overrides `LEERIE_CONFIDENCE_ROUNDS` and `leerie.toml`. |
| `--skip-smoke` | off | Skip the live `claude -p` preflight smoke test. |
| `--source-of-truth VALUE` | `both` | `codebase` / `research` / `both`. Overrides `LEERIE_SOURCE_OF_TRUTH` and `leerie.toml`. |
| `--runtime VALUE` | `local` | `local` / `fly` / `ec2`. Execution backend for per-subtask worker containers. Overrides `LEERIE_RUNTIME` and `leerie.toml`. `--runtime ec2` provisions an EC2 instance, seeds it, and runs the orchestrator on it, mirroring `--runtime fly`; see `docs/INSTALL.md` "EC2 runtime" (requires `LEERIE_EC2_AMI` to name an AMI with the orchestrator already baked in). |
| `--aws-region VALUE` | none | AWS region leerie itself uses when provisioning `--runtime ec2` machines. Distinct from the AWS SDK's own `AWS_REGION` credential-chain var. Also `LEERIE_AWS_REGION` env var or `aws_region` in `leerie.toml`. |
| `--aws-profile VALUE` | none | AWS profile leerie itself uses when provisioning `--runtime ec2` machines. Distinct from the AWS SDK's own `AWS_PROFILE` credential-chain var. Also `LEERIE_AWS_PROFILE` env var or `aws_profile` in `leerie.toml`. |
| `--ec2-ami VALUE` | none (required for `--runtime ec2`) | AMI id for the `RunInstances` call. Also `LEERIE_EC2_AMI` env var or `ec2_ami` in `leerie.toml`. |
| `--ec2-instance-type VALUE` | none (required for `--runtime ec2`) | EC2 instance type (e.g. `t3.large`). Also `LEERIE_EC2_INSTANCE_TYPE` env var or `ec2_instance_type` in `leerie.toml`. |
| `--ec2-key-name VALUE` | none (required for `--runtime ec2`) | EC2 key-pair name for SSH access. Also `LEERIE_EC2_KEY_NAME` env var or `ec2_key_name` in `leerie.toml`. |
| `--ec2-security-group VALUE` | none (required for `--runtime ec2`) | Security group id to attach. Also `LEERIE_EC2_SECURITY_GROUP` env var or `ec2_security_group` in `leerie.toml`. |
| `--ec2-subnet-id VALUE` | none (required for `--runtime ec2`) | Subnet id to launch into. Also `LEERIE_EC2_SUBNET_ID` env var or `ec2_subnet_id` in `leerie.toml`. |
| `--inspect-dir PATH` | none | Extra directory the inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision, artifact_registry) may read; forwarded to `claude -p` as `--add-dir`. Repeatable. Also `LEERIE_INSPECT_DIRS` (colon-separated) or `inspect_dirs` in `leerie.toml` (comma-separated). |
| `--model ALIAS` | per-worker (every worker → `sonnet`) | `sonnet` / `opus` / `haiku`. Sets every worker this run; without it the per-worker defaults apply. |
| `--model-<worker> ALIAS` | per-worker default (every worker → `sonnet`) | Per-worker override. `<worker>` is one of `classifier`, `planner`, `reconciler`, `plan_overlap_judge`, `provision`, `implementer`, `integrator`, `conformer`, `fit_judge`, `splitter`, `adherence_judge`. Overrides `--model`, `LEERIE_MODEL`, and `leerie.toml`. |
| `--effort LEVEL` | per-worker (judgment: `medium`; implementer/conformer: `low`) | `low` / `medium` / `high` / `xhigh` / `max`. Reasoning-depth dial forwarded to `claude -p --effort`. Pins judgment workers to a consistent depth across runs to reduce same-job variance (e.g. planner subtask-count drift); implementer/conformer are pinned to `low` for cost/latency. §2 "Effort selection". |
| `--effort-<worker> LEVEL` | per-worker default (judgment workers → `medium`; implementer/conformer → `low`) | Per-worker override. `<worker>` is one of the orchestrator workers (same set as `--model-<worker>`). Overrides `--effort`, `LEERIE_EFFORT`, and `leerie.toml`. |
| `--judge-model ALIAS` | `sonnet` | Model alias for the post-run judge skill (absent from `MODEL_DEFAULT_PER_WORKER`, falls through to the global `MODEL_DEFAULT`). Also `LEERIE_MODEL_JUDGE` or `model_judge` in `leerie.toml`. |
| `--heal-model ALIAS` | `sonnet` | Model alias for the post-run self-heal skill. Also `LEERIE_MODEL_HEAL` or `model_heal` in `leerie.toml`. |
| `--heal-max-rounds N` | `10` | Maximum heal-loop iterations per `call_type`. Also `LEERIE_HEAL_MAX_ROUNDS` or `heal_max_rounds` in `leerie.toml`. |
| `--heal-success-threshold RATE` | `0.9` | Pass-rate threshold for the heal-loop SUCCESS verdict. Also `LEERIE_HEAL_SUCCESS_THRESHOLD` or `heal_success_threshold` in `leerie.toml`. |
| `--verbosity LEVEL` | `stream` | `quiet` / `normal` / `stream` / `debug`. Controls inline per-worker activity output; full per-worker stream is always saved to `<state-root>/logs/<sid>.log` (where `<state-root>` is the resolved state directory — default `$HOME/.leerie/<basename>/`). |
| `-v` / `-vv` | `0` (off) | Shortcuts that anchor to `normal`: `-v` = `stream`, `-vv` = `debug`. With no `-v` and no `--verbosity`, falls through to `LEERIE_VERBOSITY` / `leerie.toml` / default `stream`. |
| `-q` / `-qq` | `0` (off) | Shortcuts that anchor to `normal`: `-q` = `normal` (pre-streaming behavior), `-qq` = `quiet`. With no `-q` and no `--verbosity`, falls through to the same chain as `-v`. |
| `--judge-dir DIR` | `judge-out` | Subdirectory name under the run dir for LLM judge output. Also `LEERIE_JUDGE_DIR` or `judge_dir` in `leerie.toml`. |
| `--heal-dir DIR` | `heal-out` | Subdirectory name under the run dir for LLM self-heal output. Also `LEERIE_HEAL_DIR` or `heal_dir` in `leerie.toml`. |
| `--phase PHASE` | — | Run a post-run skill phase (`judge` or `heal`) against an existing run's captured LLM calls instead of starting a new run. Use `--run-id` to select when multiple runs exist. |
| `--report [RUN_ID]` | — | Print a read-only telemetry report for a run: per-call-type token/cost/latency/failure breakdown plus memory peak. Pass a run id, or omit to auto-pick when exactly one run exists. Exits without running orchestrate. |
| `--status STATE` | — | With `list`, restrict the table to runs whose derived status matches STATE. One of: `seed-failed`, `corrupt-sidecar`, `in-progress`, `done`, `done-pushed-no-pr`, `done-pushed-pr`, `push-failed`, `pr-failed`, `paused`, `killed`, `sync-failed`. |
| `--skip-overlap-judge` | off | Skip the phase 2¾ plan-overlap judge (DESIGN §5). Auto-skipped on single-planner runs; this flag disables it on multi-planner runs. Also `LEERIE_SKIP_OVERLAP_JUDGE` or `skip_overlap_judge` in `leerie.toml`. |
| `--skip-budget-check` | off | Skip the post-schedule budget-feasibility preflight (DESIGN §13). The runtime backstop in `State.bump_workers()` still fires. Also `LEERIE_SKIP_BUDGET_CHECK` or `skip_budget_check` in `leerie.toml`. |
| `--skip-repo-map` | off | Skip the P6 repo-map structural context (DESIGN §5½ (P6)): suppresses `_build_repo_map()` and the ranked subgraph injection into planner/splitter context; the planner degrades gracefully to the prior grep/glob-only path. Use on repos where tree-sitter cannot parse the primary language. Also `LEERIE_SKIP_REPO_MAP` or `skip_repo_map` in `leerie.toml`. |
| `--skip-adherence-check` | off | Skip the instruction-adherence gate: the deterministic prescribed-command-coverage floor and the `adherence_judge` worker in `phase_adherence_gate` (a whole-plan gate, "Phase 2⅞", run after `phase_overlap_judge` and before `_schedule()`). A plan that diverges from an explicitly prescribed procedure is not caught before `phase_execute` spends. Also `LEERIE_SKIP_ADHERENCE_CHECK` or `skip_adherence_check` in `leerie.toml`. |
| `--skip-integration-check` | off | Skip the `integration_judge` behavioral-defect gate (DESIGN §8) entirely: no worker spawn for any subtask in this run. Independent of the accept-integration/audit-key mechanism, which only accepts a finding the judge already produced. Also `LEERIE_SKIP_INTEGRATION_CHECK` or `skip_integration_check` in `leerie.toml`. |
| `--dangerously-skip-permissions` | off | Pass `--dangerously-skip-permissions` to every `claude -p` worker, including judgment workers that run in the real repo cwd. Waives DESIGN §12 read-only enforcement. Also `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` or `dangerously_skip_permissions` in `leerie.toml`. |
| `--pr-template NAME` | none | When the target repo has multiple PR templates in `PULL_REQUEST_TEMPLATE/`, pick this one by basename (with or without `.md`). Also `LEERIE_PR_TEMPLATE` or `pr_template` in `leerie.toml`. |
| `--pr-base-branch BRANCH` | `working_branch` | Override the final branch this run's PR merges into (passed to `gh pr create --base`). The diff fork-point used to compute the PR diff is unaffected and always stays `working_branch`. Also `LEERIE_PR_BASE_BRANCH` or `pr_base_branch` in `leerie.toml`. |
| `--pr-writer-model ALIAS` | `sonnet` | Model alias for the finalize-time PR title + body writer. Also `LEERIE_MODEL_PR_WRITER` or `model_pr_writer` in `leerie.toml`. |

### Launcher verbs

These bare subcommands are handled by the bash launcher before the
container starts. A summary appears in the `leerie --help` epilog.

**Per-repo configuration (no container required):** `leerie config` is
a host-only fast path — it exits before `nerdctl run` and never starts
a container.

| Verb | Description |
|------|-------------|
| `config` | Print the effective build/lint/test config for this repo, with `[config]` or `[inference]` provenance for each axis. Also shows `leerie.toml` operational knobs when present. |
| `config --init` | Create `.leerie/config.toml` with auto-detected BLT commands (uncommented) and a commented `setup_packages` example. Errors if the file already exists. Prints the path and suggests `git add .leerie/`. |
| `config --chat` | Open an interactive `claude` session with a config-generation system prompt and `--add-dir` pointing at the current repo. The model can read the repo and write `.leerie/config.toml` (and optionally `.leerie/Dockerfile`). |
| `config --recapture` | Host-only (no container). Consolidates across **all** finished runs' logs (not just the newest) and writes merged `setup_packages` / language-dep installs to `.leerie/config.toml` via the dep_capture LLM worker. Never-clobber union: already-captured runs (sentinel present) are skipped and only new packages/managers are added. |
| `config --recapture --force` | Re-runs the worker over runs already captured (drops the `dep_capture.done` sentinel) **and** wholesale-replaces the persisted `setup_packages` / `language_installs` from the fresh capture — deps no longer captured are dropped. An empty capture leaves the existing config untouched (never blanks a good config). Use to re-derive deps from current run history. |

**Lifecycle (remote mode):**

| Verb | Description |
|------|-------------|
| `stop <run-id> [--runtime local\|fly\|ec2]` | Pause a run — a remote Fly machine, an EC2 instance (`stop-instances`, preserving the root EBS volume), or a local container. Resumable via `resume` (EC2 `resume` calls `resume_instance()` and re-resolves the reassigned public IP). |
| `kill <run-id> [--force]` | Destroy a remote machine permanently. `--force` skips confirmation. Also accepts `--machine-id <id> [--app <app>]` for orphan cleanup. |
| `finalize <run-id> [--force] [--no-verify] [--no-push] [--runtime fly]` | Post-detach finalization: collect un-integrated subtask branches on the machine, fetch the run branch, then push + open PR on the host. Without `--force`, requires the orchestrator to be dead. `--force` SIGTERMs a live orchestrator first, then collects and fetches. |
| `re-seed <run-id> [--force]` | Mid-run host→machine re-rsync of dirty delta. `--force` bypasses the safety check that refuses to clobber machine-side uncommitted edits. |
| `status <run-id\|chain-id\|group-id>` | Render run/chain/group state from `run.json`. |
| `attach <run-id\|chain-id>` | Poll `run.json` files every 5s. |
| `accept-blocked <run-id> <subtask-id> [--force]` | Accept a blocked subtask so `resume` skips it. `--force` also settles one abandoned mid-flight (e.g. after a crash), where neither status field records it as blocked. |
| `accept-integration <run-id> <subtask-id>` | Accept a recorded `integration_judge` finding for a subtask so `resume` stops re-invoking the judge for it. |
| `chain [--chain-id <uuid>] --wave <files> [--wave <files>] ...` | Submit or resume a multi-run chain. `status`/`kill`/`resume`/`finalize`/`attach <chain-id>` and `list --chains` also operate on chains (see "Chain verbs" below). |
| `group --repo <path> "<prompt>" [--repo ...] [--brief <file>] [--group-id <uuid>]` | Fan-out launcher for N single-repo runs sharing a `group_id`. `status`/`kill`/`resume`/`finalize <group-id>` and `list --groups` also operate on groups (see "Run-group verbs" below). |
| `version` | Print `leerie <version>` and exit. |

`resume` and `list` are documented in the "CLI flags" table above (they interact with the `task` positional and `--run-id`/`--phase`); the rest of the bare verbs are listed here.

**Resume modifiers (used with `resume`):**

| Flag | Description |
|------|-------------|
| `--shell` | Drop into a bash shell at `/work` on the machine instead of tailing the orchestrator log. |
| `--auto-finalize` | On clean orchestrator exit, automatically run `leerie finalize`. |
| `--no-re-seed` | Skip the automatic re-seed of dirty delta on resume. |

**Build and runtime:**

| Flag | Description |
|------|-------------|
| `--state-dir PATH` | Override the per-repo state directory. Also `LEERIE_STATE_DIR` env var or `state_dir` in `leerie.toml`. |
| `--fly-app NAME` | Fly.io app name (required for `--runtime fly`; globally unique). Also `LEERIE_FLY_APP` env var. |
| `--fly-disk-gb N` | Provision a Fly volume of N GB mounted at `/home/leerie`. Also `FLY_VM_DISK_GB` env var. |
| `--no-runtime-install` | Skip auto-install of container runtime (Colima / nerdctl / containerd). Also `LEERIE_NO_RUNTIME_INSTALL`. |
| `--no-auto-publish` | Skip the image-publish probe on startup. Also `LEERIE_NO_AUTO_PUBLISH`. |
| `--local-build` | Force local `nerdctl build` instead of the Fly remote builder. Also `LEERIE_LOCAL_BUILD`. |

### Environment variables and `leerie.toml` keys

| Env var | `leerie.toml` key | Description |
|---------|---------------------|-------------|
| `LEERIE_STATE_DIR` | `state_dir` | Override the per-repo run state directory. Unset → default `$HOME/.leerie/<basename>/` (outside the repo; no `.gitignore` entry needed in target projects). Cross-repo basename collisions are caught at use time via an `.owner` sidecar inside the dir. Set once in your shell profile for a global directory across all repos. |
| `LEERIE_SOURCE_OF_TRUTH` | `source_of_truth` | Sticky source-of-truth preference (`codebase` / `research` / `both`). Overridden by `--source-of-truth`. Unset → default `both`. |
| `LEERIE_RUNTIME` | `runtime` | Execution backend for per-subtask worker containers (`local` / `fly` / `ec2`). Overridden by `--runtime`. Unset → default `local`. |
| `LEERIE_MODEL` | `model` | Model alias applied to every worker. Overridden by `--model` and per-worker overrides. Unset → every worker defaults to `sonnet`. |
| `LEERIE_MODEL_<WORKER>` | `model_<worker>` | Per-worker override (e.g. `LEERIE_MODEL_IMPLEMENTER=opus`). Overridden by `--model-<worker>`. `<worker>` ∈ `classifier`, `planner`, `reconciler`, `plan_overlap_judge`, `satisfied_probe`, `provision`, `implementer`, `integrator`, `conformer`, `fit_judge`, `splitter`, `adherence_judge`. Unset → every worker → `sonnet`. |
| `LEERIE_EFFORT` | `effort` | Reasoning-depth dial forwarded to `claude -p --effort` (`low` / `medium` / `high` / `xhigh` / `max`). Applies to every worker; overridden by `--effort` and per-worker overrides. Unset → judgment workers `medium`, implementer/conformer `low`, everything else inherits Claude default. |
| `LEERIE_EFFORT_<WORKER>` | `effort_<worker>` | Per-worker override (e.g. `LEERIE_EFFORT_PLANNER=max`). Overridden by `--effort-<worker>`. Same worker set as `LEERIE_MODEL_<WORKER>`. Unset → judgment workers `medium`; implementer/conformer `low`. |
| `LEERIE_CONFIDENCE_ROUNDS` | `confidence_rounds` | Evidence-gate rounds per worker (positive integer). Overridden by `--confidence-rounds`. Unset → default `8`. |
| `LEERIE_INSPECT_DIRS` | `inspect_dirs` | Extra directories the inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision, artifact_registry) may read; forwarded as `--add-dir`. Env value is colon-separated; TOML value is comma-separated. Overridden by `--inspect-dir` (repeatable). Unset → none. |
| `LEERIE_VERBOSITY` | `verbosity` | Inline-output verbosity (`quiet` / `normal` / `stream` / `debug`). Overridden by `--verbosity`. `-v` / `-vv` / `-q` / `-qq` shortcuts override both. Unset → default `stream`. |
| `LEERIE_NO_PUSH` | `no_push` | Sticky opt-out from push + PR at finalize (truthy → skip). Overridden by `--no-push`. `--no-verify` has no env/TOML mirror — it is a per-invocation override only. Unset → default `false` (push + PR happen). |
| `LEERIE_CLARIFY` | `clarify` | Sticky opt-in to surfacing intent questions to the user (truthy → on). Overridden by `--clarify`. Unset → default `false`. |
| `LEERIE_MODEL_JUDGE` | `model_judge` | Model alias for the post-run judge skill. Overridden by `--judge-model`. Unset → default `sonnet` (absent from `MODEL_DEFAULT_PER_WORKER`, falls through to the global `MODEL_DEFAULT`). |
| `LEERIE_MODEL_HEAL` | `model_heal` | Model alias for the post-run self-heal skill. Overridden by `--heal-model`. Unset → default `sonnet`. |
| `LEERIE_HEAL_MAX_ROUNDS` | `heal_max_rounds` | Maximum heal-loop iterations per `call_type`. Overridden by `--heal-max-rounds`. Unset → default `10`. |
| `LEERIE_HEAL_SUCCESS_THRESHOLD` | `heal_success_threshold` | Pass-rate threshold for the heal-loop SUCCESS verdict. Overridden by `--heal-success-threshold`. Unset → default `0.9`. |
| `LEERIE_JUDGE_DIR` | `judge_dir` | Subdirectory name under the run dir for LLM judge output. Overridden by `--judge-dir`. Unset → default `judge-out`. |
| `LEERIE_HEAL_DIR` | `heal_dir` | Subdirectory name under the run dir for LLM self-heal output. Overridden by `--heal-dir`. Unset → default `heal-out`. |
| `LEERIE_MAX_WORKERS` | `max_workers` | Total worker-invocation budget. Overridden by `--max-workers`. Unset → default `2000`. |
| `LEERIE_MAX_PARALLEL` | `max_parallel` | Concurrent workers per wave. Overridden by `--max-parallel`. Unset → default `5`. |
| `LEERIE_WORKER_MEMORY_MAX` | `worker_memory_max` | Per-worker cgroup memory cap (e.g. `4G`, `512M`). Overridden by `--worker-memory-max`. Unset → auto-derived from the shared `leerie.slice` budget (`/proc/meminfo` only as a fallback), and raised automatically when the repo declares a Node heap — see the `--worker-memory-max` row above. |
| `LEERIE_WORKER_TIMEOUT` | `worker_timeout_sec` | Global per-worker wall-clock ceiling in seconds. Overridden by `--worker-timeout`. Setting it at any tier bypasses the measured per-worker timeout table — see the `--worker-timeout` row above. Unset → default `5400` and the table applies. |
| `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` | `dangerously_skip_permissions` | Waive §12 read-only enforcement on judgment workers (truthy → on). Overridden by `--dangerously-skip-permissions`. Unset → default `false`. |
| `LEERIE_SKIP_OVERLAP_JUDGE` | `skip_overlap_judge` | Skip the phase 2¾ plan-overlap judge on multi-planner runs (truthy → skip). Overridden by `--skip-overlap-judge`. Unset → default `false`. |
| `LEERIE_SKIP_BUDGET_CHECK` | `skip_budget_check` | Skip the post-schedule budget-feasibility preflight (truthy → skip). Overridden by `--skip-budget-check`. Unset → default `false`. |
| `LEERIE_SKIP_REPO_MAP` | `skip_repo_map` | Skip the P6 repo-map structural context injection (truthy → skip). Overridden by `--skip-repo-map`. Unset → default `false`. |
| `LEERIE_SKIP_ADHERENCE_CHECK` | `skip_adherence_check` | Skip the instruction-adherence gate (deterministic command-coverage floor + `adherence_judge`) (truthy → skip). Overridden by `--skip-adherence-check`. Unset → default `false`. |
| `LEERIE_SKIP_INTEGRATION_CHECK` | `skip_integration_check` | Skip the `integration_judge` behavioral-defect gate entirely (truthy → skip). Overridden by `--skip-integration-check`. Unset → default `false`. |
| `LEERIE_PR_TEMPLATE` | `pr_template` | PR template basename for repos with multiple templates. Overridden by `--pr-template`. Unset → alphabetically first `.md`. |
| `LEERIE_PR_BASE_BRANCH` | `pr_base_branch` | Final branch this run's PR merges into. Overridden by `--pr-base-branch`. Unset → default `working_branch`. |
| `LEERIE_MODEL_PR_WRITER` | `model_pr_writer` | Model alias for the finalize-time PR writer. Overridden by `--pr-writer-model`. Unset → default `sonnet`. |
| `LEERIE_MODEL_DEP_CAPTURE` | *(none)* | Model alias for the finalize-time dep_capture worker. Env var only — no per-worker CLI flag and no `leerie.toml` key (it still honors the global `model` key / `--model`). Unset → default `sonnet`. |
| `LEERIE_CAPTURE_DEPS` | `capture_deps` (`.leerie/config.toml` only — not `leerie.toml`) | Enable finalize-time dependency capture (truthy → on). Precedence: `LEERIE_CAPTURE_DEPS` > `.leerie/config.toml` > default `true`. Set to `false` / `0` to disable entirely. |
| `LEERIE_BAKE_LANGUAGE_DEPS` | `bake_language_deps` | Include a language-dep `COPY`+`RUN` layer in the auto-generated `.leerie/Dockerfile` (truthy → on). Precedence: `LEERIE_BAKE_LANGUAGE_DEPS` > `leerie.toml` > `.leerie/config.toml` > default `true`. Set to `false` for an apt-only bake. |
| `LEERIE_WORKER_DEBUG` | — | Enable debug-level logging injection (`DEBUG=*`, `ANTHROPIC_LOG=debug`) into worker processes. Truthy → on. |
| `LEERIE_FLY_APP` | — | Fly.io app name (globally unique). Required when `--runtime fly`. Set via env or `--fly-app`. Launcher-only. |
| `LEERIE_REGION` | — | Fly region used by per-job `--runtime fly` machines (including those spawned by `leerie chain`). Unset → default `iad`. Launcher-only. |
| `LEERIE_AWS_REGION` | `aws_region` | AWS region leerie itself uses when provisioning `--runtime ec2` machines — distinct from the AWS SDK's own `AWS_REGION` credential-chain env var. Overridden by `--aws-region`. Unset → default `None` (region selection left to the AWS credential chain). |
| `LEERIE_AWS_PROFILE` | `aws_profile` | AWS profile leerie itself uses when provisioning `--runtime ec2` machines — distinct from the AWS SDK's own `AWS_PROFILE` credential-chain env var. Overridden by `--aws-profile`. Unset → default `None`. |
| `LEERIE_EC2_AMI` | `ec2_ami` | AMI id for the `--runtime ec2` `RunInstances` call. Overridden by `--ec2-ami`. Required for `--runtime ec2`, no default. Launcher-only. |
| `LEERIE_EC2_INSTANCE_TYPE` | `ec2_instance_type` | EC2 instance type (e.g. `t3.large`). Overridden by `--ec2-instance-type`. Required for `--runtime ec2`, no default. Launcher-only. |
| `LEERIE_EC2_KEY_NAME` | `ec2_key_name` | EC2 key-pair name for SSH access. Overridden by `--ec2-key-name`. Required for `--runtime ec2`, no default. Launcher-only. |
| `LEERIE_EC2_SECURITY_GROUP` | `ec2_security_group` | Security group id to attach. Overridden by `--ec2-security-group`. Required for `--runtime ec2`, no default. Launcher-only. |
| `LEERIE_EC2_SUBNET_ID` | `ec2_subnet_id` | Subnet id to launch into. Overridden by `--ec2-subnet-id`. Required for `--runtime ec2`, no default. Launcher-only. |
| `LEERIE_SEED_TIMEOUT_S` | — | Timeout in seconds for `seed_auth` / `seed_repo` bulk transfers over `flyctl ssh console`. Unset → default `600` (10 min). Launcher-only. |
| `LEERIE_PROGRESS_INTERVAL_S` | — | Heartbeat cadence in seconds for "still streaming" lines during bulk transfers. Set to `0` to suppress. Unset → default `10`. Launcher-only. |
| `LEERIE_MACHINE_START_TIMEOUT` | — | Timeout in seconds for Fly machine start. Unset → default `120`. Launcher-only. |
| `LEERIE_PAUSE_NOTIFY_CMD` | — | Shell command to `eval` when a Fly machine pauses on failure. Unset → no notification. Launcher-only. |
| `LEERIE_NO_RUNTIME_INSTALL` | — | Skip auto-install of container runtime (truthy → skip). Also `--no-runtime-install`. Launcher-only. |
| `LEERIE_NO_AUTO_PUBLISH` | — | Skip image publish probe (truthy → skip). Also `--no-auto-publish`. Launcher-only. |
| `LEERIE_LOCAL_BUILD` | — | Force local image build instead of Fly remote builder (truthy → local). Also `--local-build`. Launcher-only. |
| `LEERIE_NONINTERACTIVE` | — | Suppress interactive prompts in runtime-install and auth flows (truthy → non-interactive). Launcher-only. |
| `FLY_VM_DISK_GB` | — | Provision a Fly volume of this many GB. Also `--fly-disk-gb`. Launcher-only. |
| `CLAUDE_AUTOCOMPACT_PCT_OVERRIDE` | — | **Claude Code CLI variable**, not consumed by leerie. Set to `70` to backstop worker auto-compaction. |

### Precedence

- **Source-of-truth** (highest first): `--source-of-truth` →
  `LEERIE_SOURCE_OF_TRUTH` → `leerie.toml` → default `both`.
- **Model** (per worker, highest first): `--model-<worker>` →
  `--model` → `LEERIE_MODEL_<WORKER>` → `LEERIE_MODEL` →
  `model_<worker>` in `leerie.toml` → `model` in `leerie.toml` →
  per-worker default → global default (every worker resolves to
  `sonnet` today). To opt a specific worker into Opus-grade reasoning,
  set `LEERIE_MODEL_<WORKER>=opus` or pass `--model-<worker> opus`.
- **Confidence rounds** (highest first): `--confidence-rounds` →
  `LEERIE_CONFIDENCE_ROUNDS` → `confidence_rounds` in
  `leerie.toml` → default `8`.
- **Verbosity** (highest first): `--verbosity` → `-v`/`-vv`/`-q`/`-qq`
  shortcuts (anchored to `normal`, not to the resolved default) →
  `LEERIE_VERBOSITY` → `verbosity` in `leerie.toml` → default
  `stream`.

See §2 above for the rationale behind these orders and the full
validation contract.

### Worker types

Leerie spawns sixteen kinds of `claude -p` worker. Each is a separate
subprocess; there is no in-session agent nesting.

| Worker | Prompt source | Default model | Runs per task | Returns |
|--------|---------------|---------------|---------------|---------|
| `classifier` | `prompts/classifier.md` | sonnet | 1 | category set + intent questions |
| `classification_judge` | `prompts/classification_judge.md` | sonnet | 0 or 1 (phase 1½, adversarial re-check of the classifier's category set) | `{covers_task: bool, missing_categories[], rationale}` — independent verification the classifier's category set covers the task. DESIGN §8 *Independent adversarial verification* |
| `artifact_registry` | `prompts/artifact_registry.md` | sonnet | 0 or 1 (phase 2, before planning) | canonical `{description, tag, path}` artifact vocabulary shared across every planner |
| `planner` | `prompts/planner.md` | sonnet | one per category (parallel) | subtask list with deps |
| `reconciler` | `prompts/reconciler.md` | sonnet | 0, 1, or up to 3 (retried up to twice when its first attempt closes a dependency cycle or leaves unresolved tags) | eight arrays — `renames` / `added_provides` / `added_subtasks` / `conditional_drops` / `dropped_requires` (resolution; `conditional_drops` drops a planner-emitted consumer subtask whose own intent declares it conditional on an unresolvable in_plan precondition; `dropped_requires` removes an over-specified `requires` entry — an aggregate or coarser synonym of what the consumer itself provides — and ALSO plays a cycle-breaking role on retry); `dependency_edges` / `merged_subtasks` (cycle-breaking-only, used on retry when leerie's gates detect a cycle); `unresolvable` (escape hatch). DESIGN §5 |
| `plan_overlap_judge` | `prompts/plan_overlap_judge.md` | sonnet | 0 or 1 (phase 2¾, multi-planner runs only; auto-skipped on single-planner runs) | cross-domain surface overlap analysis. DESIGN §5 |
| `satisfied_probe` | `prompts/satisfied_probe.md` | sonnet | 0 or 1 per subtask (phase 3, before scheduling; skipped when `--skip-satisfied-check`) | `{satisfied: bool, evidence: str}` — soft-drops subtasks already met on the base tree. DESIGN §8 |
| `provision` | `prompts/provision.md` | sonnet | 0 or 1 (spawned only when the deterministic lockfile-detection table abstains — Java/Gradle, bare `pyproject.toml`, polyglot Makefile) | install recipe (argv-allowlisted) executed via `mise exec --`. See §6½ |
| `provision_judge` | `prompts/provision_judge.md` | sonnet | 0 or 1 (adversarial re-check of a spawned provision recipe) | `{recipe_would_succeed: bool, defects[], rationale}` — independent verification the recipe would actually install. §6½, DESIGN §8 |
| `wiring_judge` | `prompts/wiring_judge.md` | sonnet | 0 or 1 (phase 2¾ or later, "A wiring re-check on the fully-merged plan" — semantic check of the reconciled plan's cross-subtask edges) | `{plan_reviewed: bool, wiring_defects[] (each carrying severity: live_defect\|latent_risk — only live_defect gates), rationale}` — independent verification declared edges are the right ones, not just structurally resolvable. DESIGN §5, §8 |
| `implementer` | `prompts/implementer.md` | sonnet | one per subtask (per wave, parallel) | commits on a `leerie/subtasks/<run-id>/<subtask-id>` branch |
| `conformer` | `prompts/conformer.md` | sonnet | one per subtask, only on the implementer's success path | advisory `conformance_warnings` on the subtask result; doc/test/rule-fix commits prefixed `conformer:` on the same branch (DESIGN §9 *Post-work conformance*) |
| `integrator` | `prompts/integrator.md` | sonnet | on conflict during wave integration | resolved merge commit on `leerie/runs/<run-id>` |
| `fit_judge` | `prompts/fit_judge.md` | sonnet | 0 or more per subtask (P1 recursive decomposition — one per `_recursive_decompose()` call) | P1 Task-Context Fit score (0–1) with rationale and diffuse analysis. DESIGN §5½ (P1) |
| `splitter` | `prompts/splitter.md` | sonnet | 0 or more per subtask (P1 recursive decomposition — coupled-minority path only; migration sweeps use deterministic `_partition_files()`) | child subtask list with ids, titles, and success criteria. DESIGN §5½ (P1) |
| `adherence_judge` | `prompts/adherence_judge.md` | sonnet | 0 or 1 per `phase_adherence_gate` retry round (whole-plan gate, "Phase 2⅞" — only when `prescribed_procedure.is_prescribed=true`), bounded by `judgment_check_rounds` | plan-instruction-adherence score: `{user_prescribed_a_procedure, instruction_adherence (0–10), violations[], rationale}` |

Additionally, two post-run workers run outside the main orchestrate loop and are not in `WORKER_TYPES`:

- `pr_writer` (`prompts/pr_writer.md`, default sonnet) runs at finalize when the run will push — it produces the PR title and body. Overridable via `--pr-writer-model` / `LEERIE_MODEL_PR_WRITER`.
- `dep_capture` (`prompts/dep_capture.md`, default sonnet) runs at finalize (and on `--recapture` / next-run backstop) — it reads worker logs, decides what the repo needs across all languages, and writes `setup_packages` / `language_installs` to `.leerie/config.toml`. Overridable via `LEERIE_MODEL_DEP_CAPTURE`. See DESIGN §6½.

**Per-worker model defaults:** every worker — judgment (classifier,
classification_judge, artifact_registry, planner, reconciler,
plan_overlap_judge, provision, provision_judge, wiring_judge, integrator,
fit_judge, splitter, adherence_judge) and acting (implementer, conformer,
satisfied_probe) alike — defaults to Sonnet. This was previously split
(judgment workers defaulted to Opus), but Sonnet 5's judgment quality has
been externally verified to match the prior Opus 4.8 baseline on these
same decision-shaped tasks, closing the gap that motivated the split —
see `docs/DESIGN.md` §"Opus-judgment, sonnet-workhorse (historical)". To
opt a specific worker into Opus, set `LEERIE_MODEL_<WORKER>=opus` or pass
`--model-<worker> opus`. See §2 *Model selection* above for the full
precedence table.

See `docs/DESIGN.md` §7 for the worker contract and §3 above for the
invocation surface (flags, timeouts, schema enforcement).

---

## 3. Worker invocation contract

Every `claude -p` argv the orchestrator constructs comes from one builder,
`_contained_claude_argv(*, schema, allowed_tools, max_turns, model, deny_extra,
prompt=None)`. A hand-rolled `["claude", "-p", ...]` in `orchestrator/leerie.py`
is a defect; the one exemption is `_append_system_prompt_file_supported`'s
capability probe, which passes `input=""` and exits before any session or model
call. `scripts/remote/collect-subtrees.sh` is necessarily outside the builder —
it invokes the CLI directly on a remote machine and cannot import the
orchestrator — so it carries `ACT_TOOLS` and `DISALLOWED_TOOLS` as duplicated
shell constants, drift-guarded by
`tests/test_collect_subtrees_integrator_schema.py`. The rule is enforced across
those two trees (Python AST over `orchestrator/leerie.py` + a text scan of
`scripts/**/*.sh`) by `tests/test_claude_argv_containment.py`, which is derived
rather than a list of sites — the hand audit it replaces fixed the shell site
and missed the Python one. `prompt` stays `None` for workers: a positional
silently wins over stdin, and the user prompt goes over stdin (§3 *User prompt
transport*); only the smoke test passes one, and only because it is a fixed
31-byte string.

Each worker is one `claude -p` headless process. Flags used:

| Flag | Purpose |
|------|---------|
| `-p` | non-interactive single-shot |
| `--output-format stream-json --verbose` | streams one JSON event per stdout line as the worker runs; the final `result` event is the envelope (same shape as `--output-format json`'s single output — `cost`, `usage`, `terminal_reason`, `structured_output`). `_invoke` writes raw events to `<state-root>/logs/<sid>.log` and emits per-event inline summaries gated by `state.json["verbosity"]` |
| `--json-schema <inline>` | the payload schema; serialized inline as a JSON string — a file path is silently ignored (verified against Claude Code 2.1.143) |
| `--append-system-prompt` | injects the worker's role prompt — read from `prompts/*.md` for classifier/planner/reconciler/plan_overlap_judge/satisfied_probe/provision/implementer/integrator/conformer, plus the post-run / finalize workers pr_writer, judge, and patch_generator |
| `--allowedTools` | tool allowlist (soft — permission-tier pre-approval only); four profiles. **inspect** (`INSPECT_TOOLS`: read set + allowlisted `Bash(ls:*)` / `Bash(find:*)` / `Bash(cat:*)` / … for cross-cwd read-only inspection, **no Write/Edit**) for classifier, planner, reconciler, plan_overlap_judge, and provision; **acting** (`ACT_TOOLS`: read set + Bash/Write/Edit/NotebookEdit — the notebook writer sits here rather than on the deny list, which is global and would strip it from every acting worker in every user repo) for implementer, integrator, conformer, and **rebaser** (DESIGN §6 *Finalization* "Rebase-onto-base before push" — a scoped, fully-agentic §12 exception; unlike the other three, its `cwd` is a disposable `git worktree add` copy created and destroyed per finalize call, not a persistent per-subtask/staging worktree); **base-tree-only** (`SATISFIED_PROBE_TOOLS`: read set + read-only Bash verbs + HEAD-scoped git only — `Bash(git show HEAD:*)`/`Bash(git diff:*)`/`Bash(git status:*)`, deliberately **no** history-spanning git) for the satisfied_probe (DESIGN §8 *Already-satisfied subtask elimination*). The acting bucket keeps Bash unrestricted because its workers run with `--dangerously-skip-permissions`; the inspect and satisfied-probe profiles use `Bash(<verb>:*)` prefix patterns to pre-approve specific read-only verbs at the CLI level — no Write/Edit so the prompt's "you do not modify code" rule is enforced mechanically per DESIGN §12. **Note:** `--allowedTools` is bypassed entirely by `--dangerously-skip-permissions` (it is a permission pre-approval, not a visibility restriction). The hard-deny layer below compensates. A fourth, **smoke** (`SMOKE_TOOLS` = `Read`), is used by `preflight`'s smoke test; it calls no tool, and `Read` is simply the narrowest profile that keeps the argv shape identical to every other invocation. |
| `--disallowedTools` | hard-deny list (`DISALLOWED_TOOLS`); removes tools from the model's context entirely — the model cannot see or call them regardless of permission mode.  Survives `--dangerously-skip-permissions`.  Denies: `Agent`, `SendMessage`, `ScheduleWakeup`, `CronCreate`, `CronDelete`, `CronList`, `RemoteTrigger`, `PushNotification`, `Workflow`, `ReportFindings`, `Skill`, `Monitor`, `TaskCreate`, `TaskGet`, `TaskList`, `TaskUpdate`, `TaskOutput`, `TaskStop`, `ListAgents`, `EnterWorktree`, `ExitWorktree`, `DesignSync`, `ToolSearch` — tools that spawn untracked parallel work, set timers, load skills, or manipulate git worktrees outside the orchestrator's own scripts. Passed on every `claude -p` argv leerie builds, smoke test and remote integrator included (both were missing it until 2026-08-18). Also denies `Task` (the live CLI's subagent-spawning tool; `Agent` is the retired name, so the no-subagent constraint was enforced against a name current builds no longer ship — measured in the preflight smoke test's own then-uncontained surface, never a contained worker's, so this entry is defense-in-depth rather than a fix for an observed leak), `ListMcpResourcesTool`/`ReadMcpResourceTool`/`ReadMcpResourceDirTool` (inert once `--strict-mcp-config` leaves zero servers; denied so the surface stays enumerable).  Per call, `claude_p` appends `deny_extra=_repo_write_denials(st.repo_root, st.run_dir)` — the path-scoped rule `Edit(//<repo_root>/**)` (`//` is the CLI's anchor for an absolute path; `Edit(...)` subsumes `Write`/`NotebookEdit`/`MultiEdit`, and also covers `sed -i`). Two rules are emitted when `repo_root` and its realpath differ, because the CLI matches against the path the worker was handed, not its realpath — reachable via the host-side `run_rebaser`/`run_recapture_deps` entrypoints, which take `repo_root` as a parameter rather than from `os.getcwd()`. This is DESIGN §12 L1 for **acting** workers, which cannot have the flag removed: a deny rule is the only permission control that survives `--dangerously-skip-permissions`. Probed live (claude 2.1.237, ground truth from the filesystem): without it an outside-cwd write succeeded, with it it was rejected, and an inside-worktree write succeeded in both arms. Derived from `repo_root`, never hard-coded `/work`, so it cannot silently guard nothing if the mount path moves. The smoke test passes no `deny_extra` — it has no run and no checkout to guard. Returns `""` (no denial, one `log()` warning) when `run_dir` is inside `repo_root` — the `LEERIE_STATE_DIR`-unset layout, where worktrees live in the checkout and a blanket deny would deny each worker its own worktree. `scripts/remote/collect-subtrees.sh` does not carry the rule: it builds its own argv on the flyctl path, where `/work` is a seeded copy rather than the developer's checkout. |
| `--max-turns` | per-worker turn cap (values in §6) |
| `--model` | model alias for this worker — `sonnet` / `opus` / `haiku`. Value comes from per-worker resolution (see §2 *Model selection*) |
| `--add-dir` | repeated per entry in `state.json["inspect_dirs"]` (forwarded by `claude_p`'s `add_dirs` param). Used only by inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) so their sandboxed Read/Grep/Glob and allowlisted Bash verbs can reach sibling repos referenced in the task. See §2 *Inspect directories* |
| `--dangerously-skip-permissions` | acting workers only (implementer, integrator, conformer, rebaser) — suppresses all permission prompts for unattended Bash and file writes; their blast radius is the worktree they own. **Never** applied to judgment workers (`PLANNING_WORKER_TYPES`) at any setting: the flag removes the CLI's working-directory boundary as well as the prompts, and that boundary is the only thing confining a worker that runs against a tree it does not own — measured, a worker carrying the flag wrote outside its cwd and committed on the user's branch even from a detached worktree. The `Bash(<verb>:*)` patterns in `INSPECT_TOOLS` pre-approve listed verbs at the CLI level; anything else (e.g. `rm`, redirect-to-file) falls through and is rejected in non-interactive mode |
| `--strict-mcp-config` | unconditional for every `claude -p` argv leerie builds — every worker, **and** `preflight`'s smoke test, and the remote integrator in `collect-subtrees.sh` — independent of and prior to any `mcp__*` denylist enumeration. Cuts MCP tool exposure off at the source regardless of what `.claude.json` seeding copies into the container. Measured single-variable on CLI 2.1.234 (identical argv otherwise, fresh empty cwd): **27 MCP tools / 4 `mcp_servers` -> 0 / 0**, rc 0, at a cost of ~880 prompt tokens — this is a containment fix, not a prompt-size one. No `--mcp-config` is ever passed, so it can't strand a caller with zero server config but a nonzero tool surface |

`claude_p()` is `async`; every caller awaits it. Internally it awaits
`_invoke()`, which spawns the worker via the `run_proc` helper
(`asyncio.create_subprocess_exec` + `communicate()` with an optional timeout).
Shell scripts in `scripts/*.sh` are invoked via `_run_script()`, a thin async
wrapper that resolves the script path and forwards to `run_proc`.

The validated payload is read from `structured_output` on the envelope. On a
missing or schema-invalid payload, `claude_p()` retries once with the violation
quoted into the prompt; a second failure raises `WorkerError`.

#### Forced constrained decoding — `--dangerously-force-strict-output`

Off by default. Resolved by `resolve_dangerously_force_strict_output(repo_root,
cli_value)` with the standard CLI > env
(`LEERIE_DANGEROUSLY_FORCE_STRICT_OUTPUT`) > `leerie.toml`
(`dangerously_force_strict_output`) precedence.

**Why it exists.** `--json-schema` is *validated*, not constrained: the CLI
injects the schema as a synthetic `StructuredOutput` tool with no
`strict: true` and no `output_config`, so a meaningful fraction of
submissions come back malformed. Setting `strict: true` compiles the schema
into a sampling grammar and makes those shapes unrepresentable.

**Context-window side effect.** Owning `ANTHROPIC_BASE_URL` makes the CLI
treat the session as gateway-routed, applying a conservative client-side
context ceiling instead of the model's native window. `_model_arg(model)`
compensates by appending `[1m]` (the gateway 1M-window selector) to any
alias in `_ONE_M_CONTEXT_MODELS` (`sonnet`, `opus`; not `haiku`, which has no
1M variant) whenever `_STRICT_PROXY` is active; inert on the direct path.

**Mechanism.** `_StrictOutputProxy` is an `asyncio.start_server` loopback
listener, one per run, started in `_orchestrate()` before the first worker
and closed in its `finally` (covering normal completion, `die()`, and
SIGINT — no separate abnormal-exit hook is needed since SIGKILL is reaped by
the container boundary, DESIGN §6). Workers reach it via `ANTHROPIC_BASE_URL`
injected into `worker_env`; no port mapping is needed since the orchestrator
is PID 1 and workers are its children.

**Three entrypoints construct their own instance rather than reusing
`_orchestrate()`'s**, because `_invoke` gates on the module-level
`_STRICT_PROXY` global, which only `_orchestrate()` populates:

| entrypoint | why it misses `_orchestrate` | where the flag comes from |
|---|---|---|
| `run_rebaser` | separate `python3` process (`scripts/host-finalize.sh`'s heredoc, §6) | `st.data["dangerously_force_strict_output"]` |
| `run_recapture_deps` | separate `python3` process (`./leerie config --recapture`'s seam, §6½) | same, re-read per `target_run_dir` |
| `main()`'s `--phase heal` branch | `return`s before `_orchestrate()` is called | `caps`, already resolved in `main()` |

All three wrap their worker call in the shared `_strict_output_proxy(caps,
label)` async context manager, which constructs, starts, and tears down one
short-lived instance scoped to that call. The two host-seam entrypoints read
the value `_orchestrate()` already persisted to `state.json` for that run
rather than re-resolving the CLI flag, which never crosses the process
boundary; a `state.json` predating that field falls back to a CLI-blind
resolve. The proxy fails **soft** in both directions here (a `start()`
`OSError` logs and proceeds unconstrained; a `stop()` failure is caught and
swallowed) because all three callers are best-effort paths that must never
block a push, abort multi-run consolidation, or fail a heal — a deliberate
departure from `_orchestrate`'s fail-closed startup rule (DESIGN §7).
`tests/test_strict_output_proxy.py`'s `TestStrictOutputReachesEveryEntrypoint`
pins all three entrypoints structurally.

**Upstream read timeout** is floored at the module constant
`_STRICT_PROXY_TIMEOUT_SEC` but otherwise uses the *resolved*
`caps["worker_timeout_sec"]` (not the frozen default), so a raised
`--worker-timeout` ceiling can't leave the proxy giving up on a request the
worker is still legitimately waiting on.

| property | value | why |
|---|---|---|
| port | bind `0`, read back from the socket | no scan, no race, concurrent runs never collide |
| executor | dedicated `ThreadPoolExecutor(max_parallel + 8)` | the default pool saturates under concurrent load |
| socket | `reuse_address=True`, `backlog=256` | port stays rebindable after shutdown |
| shutdown | close listener, drain writers, `_pool.shutdown(wait=False, cancel_futures=True)` | keeps Ctrl-C responsive; the container boundary reaps any still-in-flight call |
| upstream | executor-bridged `urllib` | leerie has no async HTTP dependency |
| chunked request body | decoded by `_read_chunked`, never rewritten | a chunked body has no `content-length`, so a length-driven read would forward only the first packet |

**Logging reports categories, never a merged total** — a merged count on a
healthy run can misleadingly read as "the rewrite is being rejected" when the
real cause is a few unrelated transient errors. Four counters:
`passed_through` (no `StructuredOutput` tool in the request — ordinary
multi-turn traffic, ~25-30% of POSTs), `unexpected_tool_shape` (tool present
but malformed — the only pass-through worth warning about), `schema_errors`
(400s, the flag's own failure mode), `transient_errors` (429/5xx, unrelated).
A renamed `--append-system-prompt-file`-style tool is caught separately at
run level (no matching tool per request is indistinguishable from an
ordinary non-structured turn), reported once as a probable rename if the run
proxied requests but rewrote nothing.

| when | verbosity | line |
|---|---|---|
| listener starts | all | `strict output: rewriting worker API requests via 127.0.0.1:<port> …` |
| upstream ≥ 400 | **all, including `quiet`** | `strict-output proxy: upstream <status> on <method> <path> (<what was changed>) — <response body>` |
| upstream < 400 | `debug` (`-vv`) only | `strict-output proxy: <method> <path> -> <status> (<what was changed>)` |
| run ends | all | rewritten / passed-through / upstream-error counts |

The error line is never verbosity-gated: a 4xx here is most likely leerie's
own edit being rejected, and the response body names the offending schema
path — without it the operator only sees workers retrying, the exact
misattribution this flag risks. Echoes cap at `_STRICT_PROXY_ERROR_LOG_MAX`
(3) bodies of `_STRICT_PROXY_ERROR_BODY_MAX` (400) chars; further failures
are counted, not echoed (a rejected rewrite is systematic, so the pattern
repeats identically).

**Transform** (`_strictify_request`), applied only when exactly one tool is
named `StructuredOutput` with an `input_schema`: sets `strict: true`; adds
`additionalProperties: false` to every object node — including a bare
`properties` with no declared `type` and a nullable `["object", "null"]`
union, not just `{"type": "object"}` (a transform handling only the first
shape 400s leerie's own nullable `implementer.clarification_question`);
strips `minLength`/`maxLength`/`minimum`/`maximum`; clamps `minItems > 1` to
`1`. `scripts/verify-strict-schemas.py` sends every schema in `SCHEMAS` to
the **live API** (kept outside `pytest.ini`'s `testpaths` so the suite stays
LLM-free) and exits 0 (all compile) / 1 (rejection) / 2 (control not
rejected — the probe is untrustworthy) / 3 (inconclusive — throttled or
timed out; not a pass). Re-run after editing `SCHEMAS` or `_strictify_schema`.
Pinned by `test_every_object_shape_is_hardened` and
`test_no_schema_has_an_unhardened_object_shape`.

**All schemas in `SCHEMAS` compile**, but two needed restructuring beyond
mechanical hardening: `planner` refused as "too complex" from many optional
properties inside one `subtasks[]` array item — fixed by `_strictify_schema`'s
all-required pass, which collapses the combinatorial explosion. `reconciler`
refused as "grammar too large" even at zero optionals — fixed by lifting
`requires` out of `added_subtasks` into a sibling `added_requires` keyed by
`sid`, and collapsing four isomorphic `{sid, tag, reason}` arrays into one
enum-discriminated `tag_ops`; `_expand_reconciler_output` fans that back into
the nine arrays every consumer still expects. **Do not re-nest `requires` or
re-split `tag_ops`** — both put the schema back over the ceiling; grammar
size is driven by optional properties inside array items, not raw schema
size, and cheaper reductions ($defs dedup, stripping descriptions, trimming
property counts) were each tried and still refused.

`output_config.format` compiles the original schema but is unusable — it
returns the payload as a text block, so `structured_output` stays unpopulated
and removing the injected tool makes the model refuse to answer, believing
it's a prompt-injection attempt.

**Fail-open on the response too.** A 400 to a hardened request is answered
by re-sending the original, untouched. The proxy fingerprints the schema
(sha256 of the canonical `input_schema`) into `_unhardenable` so the doomed
attempt is paid once per run, not once per call, and logs the rejected
worker type at every verbosity via a fingerprint→worker-type map built from
`SCHEMAS`. Only 400s retry this way; 401/403/429/5xx are not schema
problems. Grammar compilation is cached upstream — the first hardened call
per schema is slow (tens of seconds), subsequent calls fast (~2s) — which is
what makes the flag affordable.

**Two fatal collisions**, both `die()` rather than silently degrade, because
the flag works by owning `ANTHROPIC_BASE_URL`: (1) an operator-set
`ANTHROPIC_BASE_URL` — overriding it silently or silently dropping the
guarantee are both wrong; (2) Bedrock (`AWS_BEARER_TOKEN_BEDROCK` or a
truthy `CLAUDE_CODE_USE_BEDROCK`) — Bedrock has its own base-URL override and
the proxy's upstream is hardcoded to `api.anthropic.com`, so the flag under
Bedrock would either no-op or misroute every call, indistinguishable from a
healthy run in the log.

**Stripped numeric bounds are re-checked in Python unconditionally**, not
only under this flag, since an out-of-range worker value was always a bug:
`fit_judge.score`, `adherence_judge.instruction_adherence`, and
`provision.recipe[].timeout_s` go through `_bounded_or_conservative` (the
consumers of the stripped string-length bounds already test truthiness and
needed no separate guard).

#### User prompt transport — stdin, not argv

`build()`'s argv carries no positional after `-p`; the user prompt (task +
subtask_views + any retry note) is fed to the child's stdin instead via
`_invoke()`'s `stdin_data` param, written to a temp file *before* spawn so
the file's own EOF is the end-of-input signal the CLI needs — a positional
prompt after `-p` would silently win over stdin with no error, so it must be
absent, not merely supplemented. The file is created fresh per call (so a
retry stages its own copy) and unlinked in `_invoke`'s `finally`.

This exists because a single argv element cannot exceed Linux's
`MAX_ARG_STRLEN` (131,071 bytes, not raisable), and reconciler /
plan_overlap_judge payloads routinely exceed that on their own. Pinned by
`tests/test_prompt_over_stdin.py`: the argv-length property, the absent
positional, the retry path, the file-vs-DEVNULL branch, the payload being
readable at spawn time, cleanup, and a real 150KB+ subprocess round trip.

**The prompt must be readable at `exec`, not delivered afterwards** — `claude
-p` waits a hard-coded 3s for its first stdin byte and then discards a late
write, exiting 1. A pipe+feeder transport lost the prompt under synchronous
event-loop bursts because delivery depended on the loop scheduling the
feeder within that window; a staged file has no writer to schedule and so no
deadline to lose. Pinned in `tests/test_stdin_feeder_ordering.py` (asserts
no writer task, no `proc.stdin.write`, stdin never a PIPE), plus a
behavioural pair showing a pipe losing and a file winning under a blocked
loop.

#### Appended system prompt transport — file, with a probe + inline fallback

The appended system prompt (`system_prompt`, e.g. `reconciler.md` at ~25KB)
is the second large argv element and compounds toward the same
`MAX_ARG_STRLEN` ceiling. `claude_p()` writes it to a throwaway temp file
once per call and passes it via `--append-system-prompt-file <path>` instead
of the inline flag.

That flag is **undocumented** (absent from `claude --help`, mentioned only
inside `--bare`'s help text), so its use is gated behind
`_append_system_prompt_file_supported()` — a once-per-process probe memoized
in `_APPEND_SYSTEM_PROMPT_FILE_SUPPORTED` — with unconditional fallback to
the inline flag when unsupported. The probe invokes `claude -p
--append-system-prompt-file <throwaway file>` with stdin closed: an
unrecognized flag fails immediately with `error: unknown option`, while a
recognized one reaches the CLI's own "no prompt given" error — both exit
non-zero, so the probe distinguishes them by stderr text, not exit code.

The temp file is created before `build()`'s first call and removed in a
`try/finally` covering every exit path (success, terminal-auth raise,
auth/quota raise, schema-retry exhaustion); the retry loop reuses the same
file across both attempts since `system_prompt` never changes between them.

Pinned by `tests/test_append_system_prompt_file.py`: the probe's
supported/unsupported/fail-closed branches, memoization, cleanup, the
file-vs-inline branch, the retry path reusing the file, and a live
(unmocked) sanity check against the installed `claude` CLI (skipped if
absent).

#### No result event

`claude -p` intermittently exits 0 having streamed a full session but never
emitting its terminal `result` event (anthropics/claude-code #8126, #1920,
#74761 — upstream, unresolved, no public repro). `_invoke()` returns a
synthetic envelope for that case — `is_error: True`,
`structured_output: None`, `_leerie_synthetic: "no_result_event"` — rather
than raising, so the failure routes into the 2-attempt corrective loop above
and the worker gets one fresh session. The attempt-2 nudge names the
`StructuredOutput` tool explicitly (the session-level variant of the
schema-violation nudge). Two failures raise `WorkerError` as before, so the
worst case is unchanged.

The synthetic `result` text must not contain `Invalid authentication` /
`rate limit` / `rate-limit`: `_is_auth_or_quota_failure()` falls back to
those text markers, and a false match would divert the retry into the
auth backoff below and burn the whole `auth_retry_max_sec` budget.
Pinned by `tests/test_no_result_event_retry.py`.

This is the **last** arm of `_invoke()`'s no-envelope block: every arm
above it (out-of-credits, OOM, nonzero exit code) is a named,
non-retryable condition and still raises — the nonzero-rc arm covers
leerie's own deliberate kills (SIGTERM/SIGKILL) — and the worker-timeout
path raises `subprocess.TimeoutExpired` before the block is reached.

#### Auth/quota backoff

A separate retry path handles transient `claude -p` envelope errors that
indicate the Claude Code subscription is rate-limited (HTTP 401, HTTP 429),
the gateway is transiently overloaded (HTTP 529), or the result text
contains `Invalid authentication` / `rate limit` / `rate-limit`. These
need *backoff*, not the immediate corrective retry above — the gateway
has already rejected the request and a fresh request will be rejected too
until the user's rolling usage window clears (401/429) or the overload
(529) subsides.

The text markers are skipped for envelopes carrying `_leerie_synthetic`
(the numeric `api_error_status` check still applies and still wins) —
leerie synthesizes its own envelopes and knows what they mean, so
text-matching them is wrong by construction. Concretely: the no-result
envelope interpolates the worker's **raw stderr** into `result`, and
stderr can legitimately contain `Invalid authentication` or `rate limit`
without the request having been auth-rejected, which would burn the
whole `auth_retry_max_sec` budget on a non-auth failure. Pinned by
`tests/test_no_result_event_retry.py::test_worker_stderr_cannot_trip_the_auth_classifier`.
On budget exhaustion the raised `WorkerError` names the subscription cap
for 401/429/auth-text and the transient overload for 529.

`_is_auth_or_quota_failure` only ever consults `api_error_status` or the
result text when the envelope's own `is_error` is truthy, so a
successful, schema-valid envelope never enters the backoff loop no
matter what its `result` text says (otherwise a worker legitimately
discussing API auth/rate limiting would trip the text markers on its
own correct output).

Because `_is_auth_or_quota_failure` requires a *result envelope*, it
cannot classify an **out-of-credits mid-stream kill** — `claude -p`
terminated the instant credits run out, before any `result` event
(`_invoke` returns `envelope is None`). That truncation is caught
earlier, in `_invoke` itself: a `nonlocal overage_blocked` flag latches
when a streamed `rate_limit_event` carries `overageDisabledReason in
{"out_of_credits", "out_of_overage"}` (an **exhaustion** reason). In the
no-envelope branch, if `overage_blocked` is set, `_invoke` raises
`RateLimitedExit(reset_at=None, out_of_credits=True, raw)` instead of a
bare `WorkerError`, routing into `main()`'s pause-and-surface arm
(worktree cleanup, `resume` hint, `EXIT_LOCKED`; DESIGN §6). The latch
does **not** key on `overageStatus == "rejected"`, a standing state every
org with overage disabled emits (`overageDisabledReason:
"org_level_disabled"`, `status:"allowed"`) that does not mean credits ran
out — keying on it misclassified unrelated truncations as
out-of-credits. The overage event alone is a benign warning most workers
survive; only an exhaustion reason coinciding with a missing `result`
event triggers the pause. Covered by
`test_invoke_overage_block_plus_truncation_raises_ratelimited`,
`test_invoke_overage_block_with_result_returns_envelope` (benign
control), and `test_invoke_org_level_disabled_truncation_raises_workererror`
(false-positive regression pin) in `tests/test_invoke_streaming.py`.

When `_is_auth_or_quota_failure(envelope)` matches, `claude_p()` enters a
`tenacity.AsyncRetrying` loop with `wait_exponential_jitter(initial=15,
max=120, jitter=5)` and `stop_after_delay(auth_retry_max_sec)`. Each
sleep is logged with the wait and the elapsed/total budget so the user
can Ctrl-C if they know the window won't clear in time. If the budget
is exhausted with the envelope still classified as auth/quota,
`claude_p()` raises `WorkerError` with a message naming the subscription
cap and instructing the user to re-run with `resume` once the window
clears. If a retry returns a non-auth envelope (success or a different
error), the loop exits and normal handling resumes — a schema-invalid
non-auth envelope still gets one corrective retry under the existing
2-attempt loop.

The first tenacity iteration runs without a pre-sleep (tenacity sleeps
*between* iterations, not before the first), so the effective sequence
is one immediate retry followed by waits of roughly 15 s, 30 s, 60 s,
120 s, 120 s up to the 300 s budget. Each `_invoke` produces one
`calls.ndjson` row, so a single logical `claude_p()` call can write up
to ~7 rows when the first outer schema-loop attempt's backoff exhausts
the budget, and up to ~13 rows in the rare double-burst case where the
second outer attempt also enters and exhausts backoff (budget resets
per outer attempt; total wait can then reach ~10 minutes).

The classifier and the budget constant (`auth_retry_max_sec`) live in
`leerie.py`; the budget is in §6 *Code-enforced caps*. The non-auth
`is_error` path is unchanged — schema parse failures stay immediate.

#### Transient transport disconnect

The same backoff loop also handles a **mid-stream transport disconnect**:
the network connection carrying a worker's streaming response drops
mid-answer, and `claude -p` surfaces a result envelope with `is_error`
set, `terminal_reason == "api_error"`, a **null** `api_error_status` (the
connection died before any HTTP status returned, so no numeric category
applies), and result text `"API Error: Connection closed mid-response. The
response above may be incomplete."` `_is_transient_transport_failure(envelope)`
classifies this on the same `is_error`-gated, `_leerie_synthetic`-exempt
discipline as the two auth classifiers, matching when `terminal_reason ==
"api_error"` and `_api_error_category(api_error_status)` is `None`
(401/429/529 keep the auth/quota path above), **or** the result text
carries a narrow connection-drop marker (`connection closed`, `connection
reset`, `connection error`, `mid-response`, `timeout while waiting for
response`). A matching envelope enters the *same* `tenacity.AsyncRetrying`
loop as `_is_auth_or_quota_failure` (they are OR'd into one branch
condition, so the backoff mechanics, budget, and per-iteration logging are
shared — not duplicated). It is checked **after** `_is_terminal_auth_failure`
so an expired session is never mistaken for a transport blip, and before
the generic `is_error` corrective-retry arm, which otherwise retries a
network drop once against the same bad window with a nonsensical "conform
to the schema" nudge and then fails the subtask. On budget exhaustion the
raised `WorkerError` names the transport disconnect (not a subscription
cap) and points at `resume`. This is the fresh-session complement to the
CLI's own in-session retry (the `claude -p` mid-stream fix landed in CLI
v2.1.219); leerie sees the drop only once the CLI's retries are exhausted.
Rationale: DESIGN §6 *Cleanup on abnormal exit* — most dropped sids are
pure-transport and recover on a later attempt.

#### Context overflow (client-side refusal)

`_is_context_overflow(envelope)` is checked in `claude_p()` immediately
after `_is_terminal_auth_failure` and **before** the generic 2-attempt
loop, raising `ContextOverflow` (a `BaseException`, deliberately **not** a
`WorkerError` — `_run_checked_loop` retries those across its whole round
budget). Claude Code enforces a context ceiling client-side: it emits a
synthetic assistant message (`model=<synthetic>`, usage all zeros) and ends
the session with **no API call**, so replaying identical input cannot
succeed. Before this classifier the envelope surfaced as `worker failed
schema-valid output twice: Prompt is too long`, blaming schema validation
for a context refusal.

Match requires **both** `terminal_reason == "blocking_limit"` and the
phrase in `result`: the reason alone is shared with other blocking limits
(sibling probe arms ended `max_turns`), and the text alone could appear in
a worker's own correct output. Gated on `is_error` and exempting
`_leerie_synthetic`, mirroring `_is_terminal_auth_failure`. Do **not** key
on `subtype`, which is a misleading `"success"`. `main()` treats it as a
resumable `EXIT_LOCKED` pause and names the likely remedy — dropping
`--dangerously-force-strict-output` (see `_model_arg` below).

#### Disk headroom (N30)

Two call sites share `_disk_free_ratio(path)` (walks up to the nearest
existing ancestor of `path`, then `shutil.disk_usage(p).free / .total`)
against a module constant `DISK_MIN_FREE_RATIO = 0.05` (5% free). It
scales with disk size without pretending to know per-run byte cost, and
is available before any worktree exists.

**The proportional ratio is the whole rule.** A per-worktree *measured*
bound was tried and withdrawn — the marginal cost of a not-yet-created
worktree depends on package-manager-store hardlinking (a mount topology
leerie does not control — DESIGN §6's `EXDEV` note), sibling count, and
scheduling-dependent peak coexistence, so no in-process accounting
scheme converged on a stable figure; it's measurable only from
*outside* (a `df` delta across a real second checkout). DESIGN's
figures survive, labelled as a single unreproduced measurement.

`tests/test_disk_preflight.py` guards the withdrawal by function name;
`tests/test_no_dead_functions.py` (a whole-module AST sweep) catches any
reintroduced-but-uncalled private helper regardless of name.

Signals whose reappearance means a specific already-fixed defect is back are collected in DESIGN §14½ *Regression tripwires*, guarded by `tests/test_regression_tripwires.py` — which distinguishes signals leerie EMITS (checked against the source, so a tripwire cannot quote a string the code never prints) from upstream messages it merely observes.

**What the floor alone still guarantees.** N30 was filed because disk
exhaustion "surfaces as a raw `OSError: [Errno 28]` from whatever
happened to be writing." Four mechanisms answer that (coverage is
*good, not total*: they convert every write leerie owns, but a
third-party `OSError` inside a worker's own subprocess is still that
worker's to report):

1. **Preflight** (`preflight(leerie_dir, ...)`, check "0.5", before git
   identity checks and the live smoke test): `_disk_free_ratio(leerie_dir)`
   below threshold `die()`s with an actionable message
   (`_disk_headroom_message`) naming free/total GB and the path.
   `leerie_dir` here is `st.run_dir`, already created by `main()`.
2. **Mid-run** (`phase_execute`'s wave loop, once per wave, before
   memory-admission/settle work): raises `DiskLowSpace` (a
   `BaseException`, same shape as `ContextOverflow` — never a
   `WorkerError`, so `_run_checked_loop` cannot swallow it into a retry)
   rather than `die()`ing, since workers have already spawned and state
   is worth preserving. `main()` catches it (mirroring the
   `ContextOverflow` arm): worktree-only cleanup
   (`_cleanup_on_abnormal_exit(st, full_purge=False)`), best-effort
   `capture_repo_deps`, resume hint, `EXIT_LOCKED`. `DiskLowSpace` was
   added alongside its siblings to every existing
   `except (Exception, TerminalAuthFailure, RateLimitedExit, ContextOverflow)`
   dep_capture guard.
3. **`State.save()` itself** — the first reactive checkpoint: a disk can
   cross zero between checks, so `save()`'s `tmp.write_text()` /
   `os.replace()` pair is wrapped in `try/except OSError` reraising as
   `DiskLowSpace`, caught by the same `main()` arm. "Out of space" is
   `_OUT_OF_SPACE_ERRNOS = {ENOSPC, EDQUOT}` — a quota'd home/NFS mount
   reports `EDQUOT` where a local disk reports `ENOSPC`, so a site
   converting one must convert both. Any other `OSError` propagates
   unchanged.
4. **`_invoke`'s prompt staging** — the second reactive checkpoint,
   closing the gap (2)'s wave-granularity leaves open. Each worker
   invocation writes its prompt to a temp file per attempt (the largest
   disk write leerie makes per worker); its reap-then-reraise guard
   converts `_OUT_OF_SPACE_ERRNOS` to `DiskLowSpace` on the same terms as
   (3). `tempfile.mkstemp()` sits INSIDE that guard deliberately: block
   exhaustion lets `mkstemp` succeed and fails the later write, while
   **inode** exhaustion fails `mkstemp` itself — both must be inside the
   guard or escape as a bare `OSError`.

**Saving from inside a handler.** Every terminating arm in `main()`
persists state through `_save_state_best_effort(st, where)` rather than a
bare `st.save()`. A raise inside an `except` block escapes `main()`
unnoticed by any sibling arm — skipping cleanup and `exit_code`, turning
a resumable pause into an exit-1 traceback — and in the catch-all
`except BaseException` arm the new exception REPLACES the unhandled one,
leaving the real bug reachable only as `__context__`. Both triggers are
real: `State.save()` converts an out-of-space errno to `DiskLowSpace`,
and a read-only run dir raises `PermissionError`. The helper logs rather
than swallows, and `tests/test_disk_preflight.py` sweeps `main()` for any
arm that regresses to a bare save.

A healthy disk is a no-op at all four checkpoints. Pinned in
`tests/test_disk_preflight.py`: near-zero free space triggers `die()` at
preflight before any subprocess runs; a mid-run drop raises `DiskLowSpace`
before wave-entry work starts; a healthy ratio is silent at both proactive
sites; a real `State.save()` call under a monkeypatched
`Path.write_text`/`os.replace` raising `OSError(ENOSPC, ...)` is caught and
reraised as `DiskLowSpace`, while a non-ENOSPC `OSError` still propagates
as-is.

#### Terminal auth failure

`_is_terminal_auth_failure(envelope)` is checked in `claude_p()` **before**
`_is_auth_or_quota_failure` — an expired or revoked session must never
enter the tenacity backoff loop at all (DESIGN §6 *Credential strategy* /
*Cleanup on abnormal exit*'s transient-vs-terminal split: Claude Code
sends no further request once it detects an expired session, so
retrying only burns the `auth_retry_max_sec` budget).

It mirrors `_is_auth_or_quota_failure`'s gating discipline: `False`
unless `envelope["is_error"]` is truthy; `False` for any envelope
carrying `_leerie_synthetic`. On a genuine `is_error` envelope, it
lowercases `result` and matches any of four substrings: `"failed to
authenticate"`, `"oauth session expired"`, `"session expired and could
not be refreshed"`, `"not logged in"`. The second marker is
deliberately the full phrase rather than the shorter `"oauth"`, which
appears often in worker `tool_result` blocks discussing OAuth and would
misclassify ordinary worker output.

A match raises immediately (never entering the tenacity loop) into the
same resumable-pause arm out-of-credits already uses: `main()` runs
`_cleanup_on_abnormal_exit(st, full_purge=False)`, logs a `leerie resume
<id>` hint, and exits `EXIT_LOCKED` (75) rather than `WorkerError` →
exit 1. This replaces the prior behavior at the auth-exhaustion exit
point (`claude_p()`'s budget-exhausted `WorkerError`, which previously
surfaced as "worker failed schema-valid output twice," misattributing
an auth failure to a schema problem, and exited non-resumably).
`_is_terminal_auth_failure` and the budget constant live alongside
`_is_auth_or_quota_failure` in `leerie.py`.

`WorkerError` handling by worker type — per DESIGN §7's salvage rule
("salvage if there is something to salvage; abort cleanly otherwise"):
- **implementer** — `_run_implementer()` catches it, converts to an
  `incomplete-handoff` result; a fresh implementer continues from the checkpoint.
- **conformer** — `_run_conformer()` catches it and returns `None`;
  `_settle_subtask` records a `conformer crashed` entry in
  `conformance_warnings` and the subtask still returns `complete` (DESIGN §9
  *Post-work conformance*: the phase is advisory and never fails the subtask).
- **classifier, planner, reconciler, plan_overlap_judge, provision,
  integrator** — not caught locally; propagates to `main()`, which
  aborts with state saved for `resume`.

`claude_p()` logs a non-fatal warning when the envelope `terminal_reason` is not
`"completed"` (e.g. `"max_turns"`).

Maps to `DESIGN.md`: §7 (worker contract), §2 (CLI subprocess form).

#### Multi-token rotation

`CLAUDE_CODE_OAUTH_TOKENS` (comma-separated) supersedes the singular
`CLAUDE_CODE_OAUTH_TOKEN` when set, per DESIGN §6 *Multi-token rotation*.
Whitespace around each entry is trimmed and empty entries are dropped; a
single-element list behaves exactly like the singular var.

**Launcher.** Before the existing `_extract_claude_credentials_json` call,
if `CLAUDE_CODE_OAUTH_TOKENS` is set and non-empty after parsing, `leerie`
reassigns `CLAUDE_CODE_OAUTH_TOKEN` to the list's first element — a plain
env-var reassignment, so the existing mcpOAuth-guard, die()-fast
diagnosis, and `_check_claude_credential_ttl` apply unchanged to whichever
token seeds the mounted `.credentials.json`. The launcher also forwards
the raw `CLAUDE_CODE_OAUTH_TOKENS` value into the container as its own
`-e CLAUDE_CODE_OAUTH_TOKENS=...` alongside the existing single-token
`-e`, so the orchestrator can probe/select across the full list
independently of which token seeded the file. `scripts/remote/seed-auth.sh`
and `scripts/remote/ec2-seed-auth.sh` mirror the same plural-forwarding
as a sibling condition to their existing single-token fallback block.

**Orchestrator — per-invocation env threading** (the mechanism that
makes rotation possible without a container restart). `_invoke` takes an
explicit `active_token: str | None = None` parameter. When given, it
builds `worker_env = os.environ.copy()` (independent of the pre-existing
`LEERIE_WORKER_DEBUG`-gated debug env, which still applies its own
overrides on top) and sets
`worker_env["CLAUDE_CODE_OAUTH_TOKEN"] = active_token` before
`create_subprocess_exec(..., env=worker_env)`. Per the Claude CLI's
documented authentication precedence (`CLAUDE_CODE_OAUTH_TOKEN`
outranks `.credentials.json`/Keychain subscription credentials
unconditionally — see `code.claude.com/docs/en/authentication`,
"Authentication precedence"), this env var alone steers which credential
a given `claude -p` spawn uses; no rewrite of the mounted
`.credentials.json` is needed on token switch. `claude_p`'s
`_spawn` passes `active_token=st.data.get("active_oauth_token")` on
every `_invoke` call; when unset (singular-var-only runs), `None` is
passed and behavior is byte-identical to before this feature (the
existing `env=None`/full-inherit path).

**`active_oauth_token`** is a `State` field (`STATE_FIELDS`): the raw
value of the token currently selected for this run. It is mutated via the
ordinary `st.data[...] = value; st.save()` pattern and is never written
to `calls.ndjson` or logged — only its fingerprint
(`_token_fingerprint(token)`, a truncated `sha256` hex digest) appears in
logs or telemetry.

**`NODE_OPTIONS` heap-cap injection (P9).** Node/V8 derives its default
heap ceiling (~4.2 GiB) from *host* memory, not the worker's cgroup
`memory.max`, so a build on a Node repo can abort with a V8 heap OOM
while most of leerie's (larger) per-worker allowance sits unused.
`_invoke` detects a Node repo via `_is_node_repo(cwd)` (presence of
`package.json`, `pnpm-lock.yaml`, `package-lock.json`, or `yarn.lock`)
and, when `worker_memory_max_bytes` is set, injects
`NODE_OPTIONS=--max-old-space-size=<N>` into `worker_env` as a fourth
sibling conditional block alongside debug/token/strict-proxy. `N =
max(worker_memory_max_bytes // (1024*1024) - reserve, 256)`, where
`reserve` is `_NODE_HEAP_HEADROOM_BYTES // (1024*1024)` — read from that
one constant, never retyped, so this and
`resolve_worker_memory_max`'s heap reconciliation (the mirror-image
heap → cap computation) cannot drift. The reserve covers Node's own
non-heap RSS plus the resident `claude -p` process sharing the cgroup;
the `max(..., 256)` clamp guards the explicit-override path
(`--worker-memory-max` / `LEERIE_WORKER_MEMORY_MAX` / leerie.toml
`worker_memory_max`, none of which share the auto-derive path's 8 GiB
floor) from handing V8 a non-positive or degenerately small ceiling. The
variable is absent for a non-Node repo or when `worker_memory_max_bytes`
is `None`.

**Start-of-run probe + selection.** After `preflight()` returns and before
`phase_classify`, if `CLAUDE_CODE_OAUTH_TOKENS` is present, each token is
probed for remaining runway and the winner becomes
`st.data["active_oauth_token"]`:

- **Probe A** (tried first): `GET https://api.anthropic.com/api/oauth/usage`
  with `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`,
  and `User-Agent: claude-code/<version>` (omitting the User-Agent places
  the request in an aggressively rate-limited bucket; with it, ~180s
  polling is safe). Returns `five_hour`/`seven_day` objects with
  `utilization` on a **0–100** scale and `resets_at` as ISO-8601 with a
  UTC offset, plus optional `seven_day_opus`/`seven_day_sonnet` sublimit
  objects (`null` treated as full runway, not missing data). Requires
  `user:profile` scope; a `user:inference`-scoped token (e.g. `claude
  setup-token`, what leerie itself uses) gets **403** here.
- **Probe B** (403 fallback): `POST /v1/messages` with `max_tokens: 1` and
  a one-character user message, reading the
  `anthropic-ratelimit-unified-5h-utilization` / `-5h-reset` /
  `-7d-utilization` / `-7d-reset` / `-5h-status` response headers.
  **These use a different representation than Probe A**: utilization is a
  **0.0–1.0 fraction** and reset is **Unix epoch seconds**.
  `_probe_token_usage` normalizes both onto one internal representation
  (0.0–1.0 fraction, `datetime` reset). `/v1/messages/count_tokens` does
  **not** carry these headers — a real inference call is required.
- **Ranking** (`_rank_tokens`): sorts by `min(1 − five_hour_util, 1 −
  seven_day_util)` descending (accounting for the Opus sublimit, since
  leerie's judgment workers default to Opus), tie-broken by furthest
  `resets_at`. A token whose probe failed entirely sorts last but remains
  eligible.
- **Cache**: each token's probe result is cached, keyed by
  `_token_fingerprint(token)` (never the raw token), for
  `caps["token_probe_cache_sec"]` (default 180s) — shared by both
  start-of-run selection and mid-run failover below.
- **Best-effort, never a hard gate**: if every probe fails, the first
  token in the list is selected and the run proceeds. A transient probe
  failure (timeout, connection error, 5xx, a 429 on the probe itself)
  logs quietly; a 2xx response missing an expected field (endpoint
  contract drift — these are undocumented, unstable endpoints) logs
  loudly at WARNING with the stable marker `token-probe: endpoint
  contract drift` plus the missing field name. A 401/expired token is
  logged as a distinct per-token dead-token signal.

**Mid-run failover.** A rate-limited active token can reach `claude_p`
through TWO independent surfaces, both covered by one shared helper,
`_rotate_oauth_token_or_raise(st, caps, *, known_reset_at, raw_message,
retry_fn)`:

1. **Protocol-level**: a `rate_limit_event` stream event (an unexpected
   `status`) is detected inside `_invoke`'s own streaming loop and raises
   `RateLimitedExit` directly — `_spawn` never returns an envelope.
   `claude_p`'s retry loop wraps `await _spawn(retry_note)` in
   `try/except RateLimitedExit`, catching it before it can propagate to
   `main()`'s single-token pause/auto-resume path. `out_of_credits=True`
   bypasses rotation entirely and re-raises immediately — account-level
   exhaustion is not a per-token rate limit.
2. **Envelope-level**: once `_spawn` returns a completed envelope, if it
   is a rate-limit/quota failure (`_is_auth_or_quota_failure`, not
   terminal-auth) and `CLAUDE_CODE_OAUTH_TOKENS` has more than one token,
   the same helper runs again (checked between the terminal-auth check
   and the tenacity backoff loop's entry).

In both cases the helper probes/ranks the *other* tokens (respecting the
shared cache); if one has runway, switches `active_oauth_token` and
retries immediately via the caller-supplied `retry_fn` — no re-exec, no
container restart, strictly before any `auth_retry_max_sec` is spent on
a token already known exhausted. If every token is rate-limited, it
picks the one with the soonest `resets_at` — preferring a live signal
(`known_reset_at`, e.g. a just-caught `RateLimitedExit.reset_at`) over a
possibly stale `_TOKEN_PROBE_CACHE` entry — and raises the existing
`RateLimitedExit`, picked up unchanged by `_sleep_then_reexec`. A probe
failure, or no probe data for any token, never raises from the helper
itself (returns `None`); each call site falls through to its own
pre-existing behavior. Terminal-auth failures are unaffected — a
dead/expired credential is never rotated.

Maps to `DESIGN.md`: §6 *Multi-token rotation*.

---

## 4. Phase walkthrough (`leerie.py`)

| Phase | Function(s) | What it does |
|-------|-------------|--------------|
| Preflight | `preflight` | disk headroom on the state-dir filesystem (N30 — see "Disk headroom" above), git identity, clean working tree, external `leerie` branch collision (DESIGN §3 *External collision hazard*), `claude` CLI version, live `claude -p` smoke test. The smoke test uses the shared contained argv (`_contained_claude_argv`, so tool denies + `--strict-mcp-config` apply), `SMOKE_MAX_TURNS`=5 against a measured happy path of 3, and the **resolved `classifier` model** — the tier the run's first worker actually spawns with. It runs in an **empty cwd with no repository ancestor** — a stable path under the system temp dir, named from a hash of the state root (the CLI resolves project context by walking UP from cwd, so a directory under `<repo>/.leerie` would reload the very CLAUDE.md this avoids). Not cleaned up: it is empty by construction and two runs of one state root share it. It validates the CLI, not the repo. A client-side context refusal is classified via `_is_context_overflow` and **raises `ContextOverflow`** (resumable `EXIT_LOCKED` pause) rather than printing a bare `Prompt is too long`. Run-id collisions are detected at two points: filesystem side in `State.__init__`; git side in `setup-run.sh`'s branch-creation step, which repeats the check as defense-in-depth for `resume`. Smoke test bypassed by `--skip-smoke`; preflight skipped entirely on `resume` |
| 1 Classify | `phase_classify` | one classifier worker → categories + questions. Returned categories are filtered against the 9-name whitelist in `CATEGORIES` (mirrors DESIGN §4); `die()` if none survive. On a fresh (non-resumed) run, `run.json`'s identity fields (`run_id`, `branch`, `working_branch`, `pr_base_branch`, `started_at`, `task`) are written immediately BEFORE this phase runs — not after, as originally implemented — so any early-exit path reachable from classification (including the classification gate's no-work routing, below) sees a fully-identified `run.json` rather than one carrying only `_finish_no_work_run`'s own `{finished_at, no_push, no_verify}` (DESIGN §8 *Reaching the cleared-but-empty state from classification*) |
|   • Classification gate | `phase_classification_gate` | independent adversarial verifier of the classifier's category set (DESIGN §8 *Independent adversarial verification*). One `classification_judge` worker attacks the chosen categories against the task + codebase; a non-empty `miscategorizations` array (a missing category the work requires, or a spurious one) re-drives `phase_classify` via `_run_checked_loop` (bounded by `judgment_check_rounds`, and cut short earlier by `_run_checked_loop`'s own oscillation guard when a round's issues repeat an earlier round's exactly — DESIGN §8 *The CRITIC retry pattern's oscillation guard*). Across rounds, the gate accumulates a `judge_confirmed` set — every category the judge has reviewed without objection or explicitly asked to add — and passes it into the re-invoked `phase_classify`, so `check_classifier_output`'s `SAME_WORK_RISK`/`TEST_OWNERSHIP_RISK` self-check never strips a category the judge has already vetted. On exhaustion, `die()`s with the residuals named — UNLESS `st.data["likely_already_satisfied"]` is `True` with non-empty evidence, in which case it routes to `_finish_no_work_run` (the same terminal state `_detect_no_work` produces post-plan; DESIGN §8 *Reaching the cleared-but-empty state from classification*) and returns `True`, signaling the caller (`_run_phases`) to stop the pipeline. Gates before provision/plan spend. Runs inside the `plans_after_classify` checkpoint block (DESIGN §6 "Resumable planning"), so a resume past classify skips it. Persists to `state.data["classification_coverage_gate"]`. |
|   • Provision | `phase_provision` | per-repo dep **detection** (DESIGN §6½ *Persistent out-of-repo dependency bake*). Always runs; runs after classify so a docs-only run can short-circuit to `kind: none`. Five steps: `.leerie-setup.sh` hook if present → `_synth_mise_go_override()` if `go.mod` lacks a `.go-version` / mise.toml go pin → `mise install` at the repo root (reads `.tool-versions` natively; `.nvmrc` / `.python-version` / `.ruby-version` / `rust-toolchain.toml` via image-set `MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS`) → version capture via `mise ls --current --json` → `_detect_recipe_from_lockfiles()` table-first, falls back to a `provision` worker on table miss. The recipe is **persisted to `st.data["provision"]["recipe"]` and injected into implementer/conformer prompts as a `PROVISION_RECIPE:` block** — for baked ecosystems (Python/Ruby/Rust/Go), the block is informational only; for Node, it carries the residual offline-relink command (not run by the orchestrator at `repo_root`, which would clobber the host's bind-mounted checkout). The synth-go-pin env var `MISE_OVERRIDE_CONFIG_FILENAMES` is exported to `os.environ` so all downstream worker subprocesses inherit it. `mise install` and `.leerie-setup.sh` run through `_run_streaming` so their output is visible live. Skipped on `resume` when `st.data["provision"]["recipe"]` is already present (key-presence, not truthiness — an empty recipe is a valid completed state; DESIGN §6 "Resumable planning"); the env var is re-exported from persisted state on resume. |
|   • Provision gate | `phase_provision_gate` | independent adversarial verifier of the detected install recipe (DESIGN §8, §6½). One `provision_judge` worker attacks `st.data["provision"]["recipe"]` against the image/runtime — catching the semantic gaps the deterministic `_normalize_pip_installs` / `_validate_provision_recipe` miss (a `pip install` missing `--break-system-packages` on the externally-managed Debian image, a package manager that doesn't match the lockfiles present). A non-empty `recipe_failures` array `die()`s immediately with the judge's concrete `fix` named — **detect-and-die, single pass** (no re-drive: a table recipe re-emits identically and an LLM recipe would re-produce the same defect, so re-driving only burns rounds before dying anyway; a broken recipe is fatal, matching `phase_provision`'s own recipe-validation die). A `provision_judge` `WorkerError` degrades (the deterministic checks already ran). Skipped when no recipe was detected (`kind: none`); runs inside the `plans_after_classify` checkpoint block so a resume past classify skips it. Persists to `state.data["provision_recipe_gate"]`. |
|   • Clarify *(optional)* | `gather_answers` | source-of-truth is satisfied non-interactively from the resolved preference (default `both`), **unconditionally** — never gated on the classifier's `needs_source_of_truth`, which records only whether the question was relevant. Every consumer **that holds a `State`** reads the value through `_effective_source_of_truth(st)` — `phase_plan`, `phase_reconcile` (inside the `_check_unresolvable` closure, sourcing the phase-2½ abort message), `_write_plan` and `_compose_pr_via_llm` — and none may fall back to a hardcoded tier. That list is **derived, not maintained**: `tests/test_effective_sot_consumers.py` resolves every `_effective_source_of_truth(...)` call site to its enclosing module-level function and fails if this sentence omits one. It exists because the hand-written list drifted inside the very commit that introduced it — the `phase_reconcile` reader was added as a deliberate design step and the prose still said three, which is the enumeration-vs-derivation failure recorded four times over in CLAUDE.md. The two readers that cannot are `compose_pr_body` (takes a plain `state: dict`, the deterministic PR-body fallback) and `scripts/host-finalize.sh`'s `jq`; both read the recorded answer directly and render `n/a` when absent. Intent questions from the classifier are dropped by default; pass `--clarify` to surface them. With `--clarify` + interactive: collect; with `--clarify` + non-interactive: write `pending-questions.json`, exit code 10 (DESIGN §11) |
| 2 Plan | `phase_artifact_registry`, `phase_plan` | **First: `phase_artifact_registry`** runs a single read-only `artifact_registry` worker (after classify, before any planner) that emits a small canonical `{description, tag, path}` vocabulary for the artifacts the task will plainly create (DESIGN §5 *Artifact-registry worker*). Persisted to `state.data["artifact_registry"]` (own resume checkpoint, keyed on presence — `[]` is a valid completed state); `phase_plan` injects it into every planner's `ctx_dict` so blind parallel planners prefer the same tag/path. Best-effort/non-fatal — never die()s, returns `[]` when the worker crashes every round or genuinely finds nothing to register; `--skip-repo-map` only suppresses the repo-map context handed to the worker (mirroring `phase_plan`'s own degrade) — the worker still runs and can still return a non-empty list. **Then `phase_plan`:** one planner worker per category, awaited concurrently via `_gather_or_cancel` (a small wrapper around `asyncio.gather` defined in `leerie.py`) under an `asyncio.Semaphore(max_parallel)`; the first worker exception cancels its siblings and propagates to `main()`. After all `plan_one` results are collected, P1 Layer C runs: each first-pass subtask in each plan is expanded through `_recursive_decompose(subtask, depth=0, …)`, and `plan["subtasks"]` is replaced with the union of all returned leaves (DESIGN §5½ *Wire-in to phase_plan*). Within a plan, the top-level subtasks' `_recursive_decompose` calls run under **bounded concurrency** — a single `asyncio.Semaphore(caps["max_parallel"])` created once before the loop over `plans` and shared across every plan's expansion, awaited via `_gather_or_cancel` — mirroring `_filter_satisfied_subtasks`'s accumulate-as-they-complete shape (M2 perf fix: replaces a strictly-sequential `for` loop). `st.data["decompose_snapshot"]` is written and `st.save()`d as each top-level subtask's expansion completes, same as before, but completion (and therefore write) order across subtasks is now nondeterministic — nothing depends on that order, only on every finished subtask's leaves being captured before a later crash. A plan with no subtasks is left untouched. Expansion vanishes each split parent's id, so the loop records `{parent_id: [leaf_ids]}` for every parent absent from its own leaves and then calls `_remap_vanished_deps(all_leaves, expansion)` **once over every plan's leaves after every plan has expanded** — a dependent may live in a different category's plan than the parent it names (DESIGN §5 *Id-vanishing operations*). The downstream path (reconcile → overlap_judge → schedule → _validate_plan → _write_plan) receives this expanded flat leaf set unchanged. |
|   • Reconcile *(when needed)* | `phase_reconcile` | compute set of `requires` capability tags with no matching `provides` across merged planner output. **Before matching, two mechanical passes run: (a) `_promote_external_collisions(plans)` rewrites any `extent: external` entry whose tag is in some plan's `provides` to `extent: in_plan` (the in-plan producer wins); (b) `_collect_external_preconditions(plans)` extracts every remaining `extent: external` entry into a deduped list `{tag, reasons[], originating_subtasks[]}` that bypasses the reconciler and is persisted by `_write_plan`. Both passes are re-run after `_apply_reconciler_output` so any `extent: external` entries on reconciler-added connector subtasks also flow through the same machinery (collision-promoted if a provider now exists; otherwise added to the persisted preconditions list). The second collection idempotently replaces `st.data["external_preconditions"]` — the helper returns the full deduped set so a re-run is a refresh, not an append.** Only `extent: in_plan` entries with no matching `provides` enter the unresolved set. If empty: short-circuit (no worker spawn, plan unchanged). Else: spawn one reconciler worker. Its per-subtask input view (`subtask_views`) carries `id`, `title`, `intent`, **`scope_note`**, `depends_on`, `files_likely_touched`, `provides` and the `in_plan` `requires` tags — `scope_note` because `prompts/reconciler.md` scopes `conditional_drop` to signals in the consumer's `intent`/`scope_note` and shipping only half of that surface disables the rule for whichever half the planner used. `tests/test_reconciler_payload_fields.py` derives the required field set from the prompt text so a newly-named signal cannot drift. Including `scope_note` grows `subtask_views` meaningfully, but the payload is deliberately **uncapped** — it travels over stdin, so `MAX_ARG_STRLEN` no longer bounds it (see "User prompt transport") and nothing truncates it. The nearest real ceiling is the ~224K-token client-side refusal that applies under `--dangerously-force-strict-output` (`ContextOverflow`), so a future field added here should be sized against that, not assumed free. The worker emits eight actions — five *resolution* (renames / add_provide / added_subtasks / conditional_drop / drop_require), two *cycle-breaking-only* (dependency_edges / merged_subtasks; `drop_require` also plays a cycle-breaking role), and one *escape hatch* (unresolvable). **Wire shape vs internal shape:** the four `{sid, tag, reason}` ops travel as one `op`-discriminated `tag_ops` array and a new subtask's `requires` travels in a sibling `added_requires` keyed by sid — both forced by grammar compilation (see §7). `_expand_reconciler_output` fans that into the eight arrays named here before any consumer runs, so every apply step and state field below uses the action names, not the wire names. If `unresolvable` is non-empty, dead-subtask elimination (`_prune_dead_subtasks`) first removes fully-speculative subtasks whose every `in_plan` requires is unresolvable when ≥1 domain has 0 subtasks (see "Phase 2½ checks" below); if entries remain after pruning, `die()` with the reconciler's diagnosis (DESIGN §5). Otherwise, the orchestrator applies the seven action arrays mechanically. After applying, runs an **acyclicity gate** (Tarjan's SCC over the post-mutation graph); on cycle, deep-copies the pre-mutation plans, computes a recommended cycle-resolution per SCC from structural signals, respawns the reconciler once with a structured retry prompt + bounded "must-include" set of acceptable operations, and re-runs the gate. If still cyclic, `die()` with the SCC + offending mutations enumerated. See "Phase 2½ checks" and "Cycle-resolution retry loop" below. |
|   • Overlap judge *(when 2+ planners)* | `phase_overlap_judge` | spawn one `plan_overlap_judge` worker against the reconciled plan to detect cross-planner **surface collisions** — two subtasks producing the same exported artifact (same component / function / primitive) with incompatible APIs. Schema in `SCHEMAS["plan_overlap_judge"]`. Output: zero or more `collisions`, each with `resolution ∈ {merge, drop_a, drop_b, unresolvable}` and (when `resolution=merge`) a non-empty `merge_feasibility` statement that becomes the merged subtask's unified intent. Orchestrator applies actions mechanically through `_apply_overlap_collisions` with the **anchor-survivor rule**: when one sid appears in 2+ non-`unresolvable` collisions (computed by `_compute_overlap_anchors`), it is the structural anchor of the cluster and survives every merge it participates in — overriding `_apply_overlap_merge`'s default lex-smaller rule (the default is a determinism device with no semantic content). Rationale: membership is bare appearance count and carries no semantic claim — a sid the judge dropped twice is an anchor too, it just never survives to use the hint — but a sid the judge kept returning to is a better merge tie-break than alphabetical order. Do **not** read it as "the broader subtask that absorbs each partner": that is false on 20 of the 64 two-collision combinations (DESIGN §5). Pairs that lack a shared endpoint use the lex-smaller default unchanged. Per-pair: `merge` → `_apply_overlap_merge` (with optional `survivor_hint=anchor_sid` when applicable; union of fields, intent concatenation, downstream `depends_on` rewrites); `drop_*` → `_apply_overlap_drop` (mirrors `conditional_drops` apply step); `unresolvable` → `die()` at plan time with both sids + artifact + judge's reason. The validator also die()s on the keep-and-delete contradiction (`_contradictory_drop_sids`: a `drop_*` whose dropped sid *survives* another collision — kept as a merge endpoint or as the non-dropped side of another `drop_*`. One claim deletes it, another keeps it; no apply order satisfies both. Deliberately **not** anchor membership — a sid dropped by several collisions is an anchor by appearance but is coherent multi-drop output, applied as a cluster by `_apply_multidrop`). **Per-resolution cycle avoidance:** before applying each `merge` / `drop_*`, `_apply_overlap_collisions` tentatively applies it to a copy (`_would_cycle_after`) and, if it would introduce a dependency cycle, skips it (`skipped_would_cycle`) — keeping both subtasks separate for the integrator instead of `die()`ing the run; the final post-merge Tarjan gate is retained only as a never-fires backstop. **Cheap-skip** when fewer than 2 planners produced subtasks, or total subtask count < 2 (no possible cross-planner collision). **Python backstop** asserts every `merge` carries non-empty `merge_feasibility` — caught at `_validate_overlap_judge_output` before any apply. Opt-out via `--skip-overlap-judge` (mirrors `--skip-smoke`; env `LEERIE_SKIP_OVERLAP_JUDGE`; `leerie.toml` `skip_overlap_judge`). Persists full judge output to `state.data["plan_overlap_judge"]` and post-apply mutations to `state.data["plan_overlap_applied"]` for audit. See "Phase 2¾ checks" below. |
|   • Adherence gate *(when prescribed)* | `phase_adherence_gate` | whole-plan instruction-adherence gate (DESIGN §12 sibling — see "Instruction-adherence gate" above for the full mechanism). Cheap-skip when `st.data["skip_adherence_check"]` or `st.data["prescribed_procedure"].is_prescribed` is falsy — the ~90% goal-only common case pays nothing. Otherwise: deterministic `check_prescribed_command_coverage` floor + `adherence_judge` worker, fed through the existing `_run_checked_loop` (bounded by `judgment_check_rounds`); a violation's feedback callback re-invokes `phase_plan` in full to actually re-plan, then re-invokes `phase_reconcile` **and `phase_overlap_judge`** on the re-planned output (a re-plan runs one planner per category in parallel with no cross-category tag visibility, and can reintroduce cross-domain `provides`/`requires` drift `phase_reconcile` already resolved on the first pass **and the cross-planner surface collisions `phase_overlap_judge` already merged** — DESIGN §5 *A re-plan invalidates every phase that already ran*. It does NOT re-run `phase_planning_coverage_gate`, which sits downstream and has not run yet; `phase_reconcile` short-circuits to a no-op when the re-plan introduced no new unresolved requires). `die()`s on exhaustion; `adherence_judge` `WorkerError` degrades to the floor's own verdict rather than discarding the plan. Persists to `state.data["adherence_gate"]` for audit. |
|   • Coverage gate | `phase_planning_coverage_gate` | **ADVISORY since 2026-08-04.** Invokes `task_coverage_judge` once, logs any gap carrying both a description and concrete evidence, records the verdict in `state.data["coverage_gate"]`, and returns `plans` unchanged. It never re-plans and never `die()`s. The deterministic `check_required_items_coverage` floor was DELETED: it required one subtask's `title + success_criteria_seed` token set to be a SUPERSET of a required item's and passed **0 of 102 items** across every run that ever carried `required_items`, while violating CLAUDE.md's prohibition on inferring meaning from prose. The judge is retained but non-terminal — on identical input it returned a different finding set 85% of the time (n=20) with an empty intersection across samples. Contrast `wiring_judge` (99% true, 69/70) and `plan_overlap_judge` (deterministic backstops), which keep their authority. Skipped entirely by `--skip-coverage-check`. |
| 3 Schedule | `_detect_no_work`, `_warn_cross_planner_file_overlap`, `_warn_layer_gaps`, `_warn_provider_subset_subtasks`, `_warn_test_subtask_missing_producer_edge`, `_filter_offtree_subtasks`, `_filter_satisfied_subtasks`, `_schedule`, `phase_wiring_gate`, `check_plan_wiring`, `_validate_plan` | **First: `_detect_no_work(plans)` short-circuits when every plan has `status: "ready"` and empty `subtasks` (DESIGN §8 *The cleared-but-empty terminal state*)** — `_finish_no_work_run` records `no_work_required=true` + per-domain bases in state.json, writes `finished_at` to state.json + run.json (`no_push=True`), logs the summary, and skips phases 4–6. Otherwise: warn on cross-planner file overlap; warn on layer gaps (DESIGN §5 *Migration-surface completeness*); warn on provider-subset subtasks (a subtask whose entire `files_likely_touched` is a subset of an ordered predecessor's; advisory only); warn on under-wired test subtasks (`_warn_test_subtask_missing_producer_edge` — a `test-`-prefixed subtask declaring no `requires`/`depends_on` while the plan has producing subtasks; advisory only, scoped to the `test-` prefix — `phase_wiring_gate`'s constrained repair is the actual enforcer of this class); soft-drop subtasks whose `files_likely_touched` resolves outside the run's repo root, recorded in `state.data["dropped_subtasks"]`. **Then `_filter_satisfied_subtasks(plans, repo_root, st, caps, models, efforts)`** spawns one read-only `satisfied_probe` worker per surviving subtask (bounded by `max_parallel`), each evaluating that subtask's `success_criteria_seed` against the base tree; subtasks the probe marks `satisfied` are soft-dropped (`reason: "already_satisfied"` + evidence, DESIGN §8 *Already-satisfied subtask elimination*). Each probe's payload carries a `surviving_siblings` array (every other subtask's `provides`/`files_likely_touched`) so a probe can decline a drop a still-pending sibling would invalidate (DESIGN §8 *The sibling-invalidation case*) — context for a *keep* decision only. Skipped when `state.data["skip_satisfied_check"]`. If this empties every `status: "ready"` plan, the gate routes to `_finish_no_work_run` via a synthesized `no_work_map`. Both soft-drop filters vanish subtask ids, so each calls `_remap_vanished_deps(surviving, {sid: [] for sid in dropped})` (DESIGN §5 *Id-vanishing operations*) to prune dangling `depends_on` references, and — since a drop also orphans the **tag channel** — calls `_prune_orphaned_requires(plans, dropped_provides)` once over all plans to remove inbound `requires` tags whose only provider was dropped. The probe's tool allowlist is a base-tree-only subset (`SATISFIED_PROBE_TOOLS` in `orchestrator/leerie.py`) — no `git log` / non-HEAD ref, since a worktree shares the repo's full ref DB. Advisory/soft — subordinate to the `check_branch_has_commits` backstop (DESIGN §12). **Then**: merge plans, build the global DAG via `_build_predecessor_graph` (shared with the phase 2½ acyclicity gate), Kahn topological sort into waves; a slipped-through cycle `die()`s with the full SCC report. **Wiring re-check (DESIGN §5 *A wiring re-check on the fully-merged plan*, §8)** runs on the fully-merged POST-DROP plan before `_validate_plan`: (1) `phase_wiring_gate` spawns the `wiring_judge` for semantic dangles a structural scan can't see; a non-empty `wiring_defects` array first passes through `_repair_missing_requires(plans, defects)`, and only the **unrepaired residual** `die()`s (detect, repair-what-is-unambiguous, then die — single pass, no re-drive). A defect is repaired only when `kind == "missing_requires"`, the sid is in the plan, the edge isn't already declared, and the edge doesn't close a cycle (trialled via `_would_cycle_after`, cumulative across trials). `tag_or_dep` resolves against **both** dependency channels, first match wins: (a) **tag channel** — one in-plan provider → append to `requires`; (b) **id channel** — `tag_or_dep` names a surviving subtask id → append to `depends_on` via `_add_depends_on_edge`; (c) **single-cluster fan-out** — several providers sharing one `_cofile_cluster` (§5½ (P1) *Sub-file*) → append the `requires` tag, ordering behind the whole cluster. Each repair records its `channel`. After the repair loop, `_filter_defects_already_ordered(plans, defects) -> (surviving, notes)` re-checks the unrepaired residual against the plan's actual ordering — resolved through `_build_predecessor_graph` (so `requires` edges with `extent: in_plan` count, not just `depends_on`) — dropping a defect only when **every** producer (never just one) is a direct predecessor of the sid; transitive-only orderings don't count. Scoped to `kind == "missing_requires"`; `broken_by_drop`/`broken_by_merge` findings pass through untouched, since ordering can't refute a claim that the work itself is gone. Before any repair, `_filter_provably_false_wiring_defects` discards findings the plan itself contradicts: a `broken_by_*` whose capability is still provided, or whose capability was provided by a subtask dropped `already_satisfied`. Before that loop, `_expand_multi_value_wiring_defects` splits a `missing_requires` defect whose `tag_or_dep` names several comma-joined values into one defect per value (the schema types the field as a bare string, so a comma-joined list is schema-valid but resolves nothing under exact lookup) — conservative: only `missing_requires`, only when the string doesn't already resolve whole, only when every part resolves to a surviving id or provided tag. When any repair lands, `_run_phases` re-runs `_schedule(plans)` and rewrites `state.data["plan_snapshot"]`. It reads `state.data["dropped_subtasks"]` for its `broken_by_drop`/`broken_by_merge` reasoning, so it must run post-drop. Its resume skip is keyed on the **presence of `state.data["wiring_gate"]`** (written only when the gate passes) — **not** `plan_snapshot`, which is written before the gate runs and so is present even on a died gate; keying on the snapshot would let `resume` silently bypass a gate the run had already failed. A `WorkerError` degrades (the deterministic check below still guards the structural channel). (2) The deterministic `check_plan_wiring(subtasks)` replays `_validate_plan`'s own provider-existence + `depends_on`-existence logic and `die()`s with a wiring-specific message before the generic `_validate_plan` die; it runs outside the `plan_snapshot` if/else on every path, including a budget-check resume, as the cheap structural backstop. |
| 4 Setup | `phase_execute` head → `setup-run.sh` → `_capture_conformance_baseline` | create the run branch `leerie/runs/<run-id>` and its worktree (per-run, isolated from any other run). After `setup-run.sh`, `_prune_leerie_worktrees` (scoped to the run dir) clears stale `.git/worktrees/` metadata that a prior SIGKILL'd invocation may have left behind (on Fly, `machine stop` SIGKILLs the orchestrator — the `finally`-block cleanup never runs; the stale metadata persists on the volume and crashes `git worktree list --porcelain` in `new-worktree.sh` on the next resume). Then, unless `state.data["skip_base_baseline"]`, `_capture_conformance_baseline(leerie_dir, st, caps)` runs once (DESIGN §9 *Base-tree health baseline*): the staging worktree is now an unmodified snapshot of the base HEAD, so it installs the persisted provision recipe into staging via `_ensure_worktree_deps(tree, st, caps, ...)` and measures each resolved build/lint/test command there via `_measure_blt(axis, cmd, tree, ...)` — the two helpers extracted from this function so the baseline and every later measurement share one implementation rather than drifting apart. `_measure_blt` owns the `_run_streaming` call and runs a non-login shell (`["bash", "-c", cmd]`, never `-lc`) so mise-managed runners (e.g. pnpm/npx shims) resolve via the Docker-image `ENV` PATH rather than whatever a login shell's `/etc/profile`/`~/.bash_profile` would additionally source or discard, recording the exit-code verdict per axis at `st.data["conformance"]["_baseline"]`. Each axis records a `measured` bool alongside `ran`/`passed`: a non-zero exit whose output matches `_runner_missing` (`command not found` / `No such file or directory` — the runner itself is absent, e.g. the recipe's `pip install` failed so `pytest` is missing) is recorded `measured: False` and is **excluded from `red_axes`** — it is "could not measure," not "base is RED," and a false-RED here is what provoked the conformer to re-derive the base destructively (`git checkout <base> -- .`). `_ensure_worktree_deps` is memoised on the resolved absolute worktree path in the module-level `_DEPS_INSTALLED` set (a per-process filesystem fact, not run state — re-installing once after a `resume` is correct, since a fresh container has an empty worktree), and is non-fatal throughout: a failed install is left to surface as whatever the subsequent BLT command reports, which `_runner_missing` already classifies. Tests that drive either helper must clear `_DEPS_INSTALLED` in an autouse fixture — conftest's `leerie` fixture is session-scoped, so the memo leaks across files exactly the way `_active_admissions` does. Deterministic (no LLM); advisory (never raises — a glue error logs and proceeds with no baseline); idempotent (the `_baseline` key is the resume sentinel). A RED base logs a loud provisioning warning and writes `run.json.health.base_suite`. A GREEN base logs via `_format_baseline_green_message`, which names only the axes that actually ran and were `measured` (never the hardcoded `(build/lint/tests)`) — an axis that ran but could not be measured (runner missing) is called out separately in the same message rather than silently folded into the GREEN axis list. Every axis dict carries `measured` (there is no legacy default; `red_axes`, `_format_baseline_section`, and `_base_health_payload` all treat it as mandatory). The baseline is threaded into every conformer prompt (`_format_baseline_section` → `BASELINE:` line, which surfaces unmeasurable axes as an explicit "could not measure — attribute failures yourself" line) so the conformer scopes build/lint/test residuals to the delta rather than re-deriving "pre-existing" |
| 5 Execute | `phase_execute`, `_settle_subtask`, `integrate_wave`, `_run_final_conformance` | per wave: subtasks whose `subtask_status` is already `"complete"` are skipped (they were integrated in a prior invocation); when every subtask in a wave is already complete the wave is skipped entirely and `completed_waves` is advanced. Before dispatch, stale `subtask_status` entries for retried subtasks (failed/blocked from the prior invocation) are deleted so `_get_progress` counts them as running (absent = running per the progress-prefix convention above). Remaining implementers are awaited concurrently via `_gather_or_cancel` under a fresh `asyncio.Semaphore(max_parallel)` (separate instance from Phase 2's), then integrate, then run a deterministic conflict-marker scan on the integrated worktree. `_settle_subtask` runs the **post-work conformance phase** (DESIGN §9 *Post-work conformance*) on the success path before returning — `_discover_rules_files` → `_run_conformer` loop (≤ `conformance_rounds`) → re-run the per-subtask mechanical-precondition gates (`check_branch_has_commits`, dirty-worktree, `check_diff_scope`) against the conformer's commits → attach `conformance_warnings` to the result. The phase is advisory: residuals, build/lint/test failures, gate violations on conformer commits, and `WorkerError` all surface as warnings, never as `failed`/`blocked`. If any subtask in the wave ends `blocked` or `failed`, `phase_execute` still calls `integrate_wave` for the successful subtasks (partial-wave integration — DESIGN §3) and runs the conflict-marker scan on the staging worktree, then aborts the run — the blocker is recorded in `state.json`, the successful subtasks' work is on the run branch, and the run resumes with `resume`. There is no LLM wave-level re-validation between waves; the §8 confidence gate is the load-bearing per-subtask signal, and `_scan_conflict_markers` is the deterministic post-integration safety net. **Integration-integrity gate**: after `integrate_wave` returns and the conflict-marker scan passes, and only once the wave has no `blocked`/`failed` subtasks (those die() with their own message first), `phase_execute` asserts `len(integrated) == expected`, where `expected` is the count of subtasks that settled `complete` in this wave. `integrate_wave` appends a sid to `integrated` on every path that processes it (rc 0 — including a zero-commit satisfied-rescue subtask, whose `git merge --no-ff` is a rc-0 "Already up to date"), and every other path `die()`s or resolves-via-integrator, so under correct operation the counts match. A shortfall means a subtask that settled complete was silently not merged into the run branch (subtasks all complete, none integrated, no failure — an empty run branch reaching finalize); the gate `die()`s with the integrated/expected counts before advancing `completed_waves`, so the DESIGN §6 completion signal (`completed_waves == len(waves)`) can never certify an un-integrated wave. It records no `blocked` entry — `resume` retries this wave via the un-advanced `completed_waves`, not by reading `blocked`, and no consumer reads a wave-level blocked key; the die() message is the complete diagnostic. The subtask work survives on `leerie/subtasks/<run-id>/*` and `resume` retries integration. Immediately after `integrate_wave` returns, `phase_execute` calls `_prune_subtask_worktree(sid, leerie_dir)` for every sid in the `integrated` list (N31): a scoped, non-fatal `git worktree remove --force` + rmtree fallback + `_prune_leerie_worktrees`, mirroring the pattern in `_cleanup_on_abnormal_exit` but bounded to one sid's worktree directory (including any `node_modules`-style bulk) — the branch itself is untouched, since finalize/PR history still needs it. Never called for blocked/failed sids, whose worktree `_reset_subtask_worktree` may still need for a corrective retry. Keeps disk usage bounded on long multi-wave runs instead of deferring every worktree's removal to run-end cleanup. **After every wave has integrated**, `_run_phases` calls `_run_final_conformance(leerie_dir, st, caps, models, efforts)` once on the staging worktree (DESIGN §6 *Worktree and integration model*, final-tree pass paragraph) — same `_run_conformer` loop with `cwd = <state-root>/runs/<id>/worktrees/staging`, `DIFF_BASE = st.data["working_branch"]` (the PR's base, captured by `phase_classify`), no subtask spec / criteria inputs, same `conformance_rounds` cap, same protected-path rollback discipline. Output lands at `st.data["conformance"]["_final"] = {result, warnings}` and is threaded into the `pr_writer` payload as `final_conformance`. Advisory: any failure mode (WorkerError, malformed result, exhausted rounds) surfaces as a warning; `phase_finalize` always runs |
| 6 Finalize | `phase_finalize` → `finalize.sh`, `cleanup.sh`, post-cleanup branch verification; launcher then pushes on host | verify the run branch is non-empty; run `cleanup.sh --subtask-branches` to delete per-subtask branches; **post-cleanup branch verification** (`git show-ref --verify` on the run branch — if the branch disappeared after cleanup, `die()` routes to the pause branch to preserve the machine for recovery); record `finished_at` in `run.json`; delete the per-subtask branches `leerie/subtasks/<run-id>/*` (the run branch is **kept** as the PR head; state dir is kept as audit). **The push + PR step has moved to the host launcher** (DESIGN §6 *Finalization*). A successfully finalized run (`finished_at` set AND `current_phase` == "phase 6: finalize") is **terminal on resume** — the orchestrator returns immediately without re-executing phases 4→5→6, preventing a concurrent `decide_teardown` race. **`current_phase` is stamped `"phase 6: finalize"` only *after* `finalize.sh` returns 0** (the non-empty-branch check passes), not on phase entry: the `die()` on a `finalize.sh` failure sets `finished_at` via the `except SystemExit` handler, so stamping the phase before the check would make a *died* finalize byte-identical to a *succeeded* one, and the resume completion guard would mistake it for terminal and hand the host launcher an empty run branch to push (which then fails at `gh pr create` with "No commits between …"). Stamping after the check keeps a died finalize resumable — `current_phase` stays at its pre-finalize value, the resume guard falls through, and `resume` re-runs `finalize.sh`'s non-empty check. |
| Post-run Judge | `phase_judge`, `_judge_capture` | standalone post-run phase (not part of main orchestrate flow): reads `calls.ndjson`, runs one `_judge_capture()` per record in parallel under `asyncio.Semaphore(max_parallel)`, writes per-record verdicts to `<judge-dir>/<call_id>.json` and a summary `INDEX.json`; uses `prompts/judge.md` rubric |
| Post-run Heal | `HealState`, `_heal_baseline`, `_heal_apply_patch`, `_heal_replay_patched`, `_request_patch`, `phase_heal` | heal-loop phases: `HealState` persists failing_samples / baseline / history / best_so_far at `<heal-dir>/<call_type>/state.json`; `_heal_baseline(call_type, failing_records, n, heal_dir, caps, st, models)` runs n unpatched replays per record + judge, writes baseline verdicts + state; `_heal_apply_patch(call_type, iter_n, patch_text, anchor_match, heal_dir, failing_records)` materialises patched prompts under `iter-<N>/patched-prompts/`; `_heal_replay_patched(call_type, iter_n, n, heal_dir, caps, st, models)` runs n patched replays per record + judge, appends iteration record to state.history; `_request_patch(state, iter_n, st, caps, models)` invokes the `patch_generator` worker (schema `SCHEMAS["patch_generator"]`, SID `heal-patch-<call_type>-iter<N>`, prompt from `prompts/patch_generator.md`) and returns `(anchor, replacement)` — raises `ValueError` if the returned anchor is not a literal substring of the resolved prompt body (code-enforced per the prompts-are-advisory principle); `phase_heal(call_type, failing_records, heal_dir, caps, st, models, request_patch_fn=None, n, config)` drives the full baseline→loop→report cycle; `request_patch_fn` defaults to the real `_request_patch` when `None`, or accepts a sync/async 2-arg stub for testing |

`phase_classify` runs before `gather_answers` because the question set depends
on the classification.

Between Phase 3 and Phase 4, `_write_plan()` persists the merged plan
(`<state-root>/runs/<run-id>/plan.json`, carrying the full task text
under its top-level `"task"` key) and per-subtask spec files
(`<state-root>/runs/<run-id>/subtasks/<id>.json`). It also writes
`<state-root>/runs/<run-id>/task.md`, the task text verbatim as plain
markdown. Each spec file carries `_task_ref` — the path to `task.md` —
plus `_task_ref_bytes`, its size, rather than a second copy of the task
text: inlining the full task into every subtask spec was measured to
bloat briefs significantly on large task documents, spilling past the
CLI's Read cap.

`_task_ref` points at `task.md` and **not** at `plan.json`, which is by
construction the task text plus every subtask body — strictly larger
than any single brief it replaced — so referencing it relocates the
Read-cap failure instead of removing it. Format carries the rest: the
cap is 25,000 **tokens**, and markdown measures meaningfully more
bytes/token than JSON, so the same text can sit over the cap inside
`plan.json` but under it as markdown. On large task documents that
alone isn't enough; the implementer prompt's `offset`/`limit` guidance,
keyed on `_task_ref_bytes`, is what keeps the read from failing. The
conformance phase derives its advisory build/lint/test commands
separately via `_infer_build_lint_test(repo_root)`, best-effort
discovery via config/lockfiles. Supported families (checked in this
order; first match wins per axis via
`out[axis] = out[axis] or "..."`):

- **Makefile** → `make` (build)
- **Node/JS** (`package.json`) → `<pm> run build` (build), `<pm> run test`
  (test), where `<pm>` is detected from lockfiles: `pnpm-lock.yaml` → `pnpm`,
  `yarn.lock` → `yarn`, `bun.lockb`/`bun.lock` → `bun`, else `npm`.
  Precedence mirrors `_detect_recipe_from_lockfiles()`. All PMs use the
  `<pm> run <script>` form uniformly — bun's bare `bun test` / `bun build`
  invoke built-in tools rather than package.json scripts
- **Python** (`pyproject.toml` / `pytest.ini` / `setup.cfg`) → `pytest` (test)
- **Rust** (`Cargo.toml`) → `cargo build` (build), `cargo test` (test)
- **Go** (`go.mod`) → `go build ./...` (build), `go test ./...` (test)
- **Maven** (`pom.xml`) → `mvn package` (build), `mvn test` (test)
- **Gradle** (`build.gradle` / `build.gradle.kts`) → `./gradlew build` /
  `./gradlew test` when `gradlew` exists, else `gradle build` / `gradle test`
- **ESLint** (`.eslintrc.*`) → `npx eslint .` (lint)
- **Ruff** (`.ruff.toml` / `ruff.toml`) → `ruff check .` (lint)
- **RuboCop** (`.rubocop.yml` / `.rubocop.yaml`) → `bundle exec rubocop` (lint)
- **Kotlin/detekt** (`detekt.yml` / `detekt.yaml`) → `detekt` (lint) — build/test
  are already filled by the Gradle family above; detekt fills only the lint
  axis. ktlint was considered and rejected as a marker: it has no dedicated
  config file (driven by `.editorconfig` / the Gradle plugin), so it isn't
  cleanly file-detectable in this inference style.
- **C#/.NET** (`*.sln` at root, or `*.csproj` at root as fallback) →
  `dotnet build` (build), `dotnet test` (test)
- **PHP** (`phpunit.xml` / `phpunit.xml.dist`) → `vendor/bin/phpunit` (test);
  (`phpstan.neon` / `phpstan.neon.dist`) → `vendor/bin/phpstan analyse` (lint)
- **Rails** — `_is_rails_repo(repo_root)` (requires both `Gemfile.lock` and
  `bin/rails` — the two-file check distinguishes Rails from
  Sinatra/Grape/etc.) → `bin/rails test` (test)

The short-circuit semantics mean earlier families take precedence: in a
polyglot Node+Rails repo, `npm run test` wins the test axis while
`bundle exec rubocop` still fills the lint axis if no ESLint/Ruff config
exists.

**Declared BLT commands (`.leerie/config.toml`).** A repo may commit
`.leerie/config.toml` with explicit `build`, `lint`, and/or `test` keys
that override the corresponding axis from inference. Missing keys fall
through to `_infer_build_lint_test()`. An empty-string value means "not
applicable" (same convention as inference) and is preserved rather than
replaced. The file also accepts a `setup_packages` key (comma-separated
apt package names) that triggers per-repo Dockerfile auto-generation
(§6½ *Auto-capture of repo dependencies*); not consumed by BLT resolution.

Resolution: **`_load_blt_config(repo_root)`** reads `.leerie/config.toml`
via `_read_toml_key()` for each of `build`/`lint`/`test`/`setup_packages`,
returning `None` when absent or a dict of only the present keys.
**`resolve_blt(repo_root)`** calls it; for each axis, uses the declared
value if present (including empty string), else falls through to
`_infer_build_lint_test()`. This is the function `_run_conformance_phase`
and `_run_final_conformance` both call — neither calls
`_infer_build_lint_test` directly.

`.leerie/config.toml` format (flat key = value, same parser as `leerie.toml`):

```toml
build = "make build"
lint  = "ruff check ."
test  = "pytest -x"
# setup_packages = "libvips-dev fonts-noto"
```

`plan.json` carries `{task, waves, subtasks, preconditions}`. The
`preconditions` array is the deduped list of `extent: external` `requires`
entries collected during phase 2½ (see DESIGN §5 `requires.extent`); each
entry is `{tag, reasons: [{sid, reason}, …], originating_subtasks: [sid, …]}`.
It is the human-facing surface for prerequisites the planners identified
but explicitly declared out-of-graph. The launcher / integrator surface
this list in the PR description so the human running the change sees what
must be true in the environment before the change is safe to ship.

Maps to `DESIGN.md`: §3.

---

## 5. Deterministic enforcement points

All in `leerie.py`, in execution order. This is the concrete catalogue behind
`DESIGN.md` §12 ("prompts advisory, code enforces").

### Preflight (before any LLM work)
| Check | Catches |
|-------|---------|
| `resolve_source_of_truth()` at startup | invalid value in `leerie.toml`, `LEERIE_SOURCE_OF_TRUTH`, or `--source-of-truth` — caught before any worker spawns, not mid-planner |
| `resolve_runtime()` at startup | invalid value in `leerie.toml`, `LEERIE_RUNTIME`, or `--runtime` — caught before any worker spawns |
| `resolve_models()` at startup | invalid model alias in `leerie.toml`, any `LEERIE_MODEL[_*]` env var, or any `--model[-*]` CLI flag — caught before any worker spawns |
| `git user.email` / `user.name` set | commits would fail silently without identity |
| working tree clean | dirty tree → ambiguous diffs, corrupt merge history |
| `claude --version` ≥ `MIN_CLAUDE_CLI` (currently `(2, 1, 22)`) | CLI too old for `--json-schema` (introduced for `claude -p` in v2.1.22) — replaces the cryptic "unknown option" message a stale CLI used to produce |
| `gh auth status` + `origin` remote (launcher bash, before container) | finalize would fail at push/PR after the full run already ran. Short-circuited when `--no-push` is passed (env / TOML mirrors). |
| live `claude -p` smoke test | auth failure or network problem |
| live `claude -p` smoke test — client-side context refusal | `ContextOverflow` -> resumable `EXIT_LOCKED` pause naming the remedy, not a bare `Prompt is too long` |

Run-id collisions are detected at two natural collision points:

| Check | Where | Catches |
|-------|-------|---------|
| `State.__init__` refuses if the run dir is locked by another process | container start | Another orchestrator already owns this `<state-root>/runs/<run-id>/` |
| `setup-run.sh` preserves an existing `leerie/runs/<run-id>` branch instead of creating it | wave-execute phase | A pre-existing branch with the same name (treated as a resume; the run picks up wherever the branch was left) |

The run-id is the container/machine ID (DESIGN §6), known at container creation time. No temporary directory or rename is needed.

`--skip-smoke` bypasses only the live smoke test (used by the test harness); the CLI version check and the `gh` check still run because they are local and read-only, and skipping them would defer a confusing failure to mid-run.

### Phase 1 checks — `phase_classify`
| Check | Catches |
|-------|---------|
| classifier-returned categories filtered against the 9-name whitelist `CATEGORIES` (mirrors DESIGN §4) | classifier hallucinating a category outside the nine |
| `die()` if no category survives the filter | a run with no valid domain for any planner |

### Phase 2½ checks — `phase_reconcile`
| Check | Catches |
|-------|---------|
| **dead-subtask elimination** (runs *before* `_check_unresolvable`) | subtasks whose *every* `in_plan` requires tag is in the reconciler's unresolvable set, when at least one domain has 0 subtasks. `_prune_dead_subtasks(plans, unresolvable_entries)` removes fully-speculative subtasks mechanically (mirrors dead code elimination after constant folding — DESIGN §5 *Dead-subtask elimination*). Prunes downstream `depends_on` references to pruned sids (same pattern as `conditional_drops`). Strips pruned entries from `output["unresolvable"]` before `_check_unresolvable` runs. If all unresolvable entries were pruned, `_check_unresolvable` returns immediately and the run proceeds. If some remain, `die()` as before. Pruned sids are recorded in `state.data["speculative_collapse_drops"]`. |
| **external-twin demotion** (runs *after* `_prune_dead_subtasks`, *before* `_check_unresolvable`) | an `unresolvable` entry whose tag another subtask already declared `extent: external`. `_demote_unresolvable_with_external_twin(plans, unresolvable_entries, external_preconditions)` matches first on the exact tag, then on `_tag_key` (lowercase, split on `-`/`_`, singularize tokens over 3 chars, compare as a set). A hit rewrites the consumer's `requires` entry to `extent: external` with the twin's `reason` attributed to its source sid, drops the entry from `output["unresolvable"]`, records it in `state.data["external_twin_demotions"]`, and refreshes `state.data["external_preconditions"]` via `_collect_external_preconditions` so `_write_plan` persists the rescued entry into `plan.json`'s `preconditions`. **Placement is the safety property** — running after the reconciler's verdict means it can only convert a `die()` into a deploy note, never preempt a resolution the reconciler would have made. The normalized pass is set equality after singularization, never partial token overlap (DESIGN §5 *The external twin*). |
| reconciler's `unresolvable` array non-empty → `die()` with the worker's diagnosis | genuine gaps where no planner produced a needed capability *in the build graph* and no plausible connector subtask can be inferred. Restricted to `extent: in_plan` entries — `extent: external` entries are filtered out before the unresolved set is computed and surface as `preconditions` in `plan.json` rather than as failures. Each unresolved `(sid, tag)` pair is annotated with the consuming subtask's producing planner-domain (from `_compute_unresolved_requires`) so the abort message can render `domain/sid` — naming the planner-domain whose plan held the dangling dependency. The message itself is rendered by `_unresolvable_die_message(unresolvable, sid_domain, source_of_truth)`, a module-level pure function (extracted from the closure so it is testable at all). Its remediation text names both repairs explicitly (satisfy the criterion inside the fence, or move it to whatever owns that surface) and flags widening the scope fence as usually wrong — `domain/sid` alone is *not* the remediation lever. A final paragraph about narrowing `--source-of-truth` is emitted **only when `_effective_source_of_truth(st)` is not `codebase`** — it addresses research-surfaced phantom prerequisites under `both`/`research` and is a non-sequitur otherwise (DESIGN §11 records narrowing the preference as *historically* the escape hatch, superseded by `requires.extent: external`). |
| reconciler output validated against `SCHEMAS["reconciler"]` | malformed reconciler response (caught by `claude_p`'s schema gate; structurally invalid output is retried once, then escalated) |
| **size gate** on `added_subtasks` (runs *before* the acyclicity gate) | a reconciler-added subtask emitted with `size: large`. The reconciler-authored subtasks carry `_added_by_reconciler: true` (set in `_apply_reconciler_output`); `_find_oversized_added_subtasks` collects every offender. On detection, leerie tries one size-resolution retry (see "Size-resolution retry loop" below); if the retry still emits `size: large`, `die()` with the offending sids enumerated. The downstream `_validate_plan` size check (line "no `size: large` subtasks" under "Plan validation") is the final backstop and only fires for planner-authored `large` after this retry exhausts; its error message names "planner" vs "reconciler" via the `_added_by_reconciler` flag so the user knows which prompt misbehaved. |
| **acyclicity gate** (Tarjan SCC over the post-mutation graph; runs *before* the unresolved-requires re-check) | a rename / added_subtask / dependency_edge that closes a dependency cycle. Each individual reconciler mutation can be locally correct yet jointly cycle-creating — e.g. two renames whose targets each provide what the other side requires. Tarjan localizes the SCC; edge attribution names which mutation closed each edge. On detection, leerie tries one cycle-resolution retry (see "Cycle-resolution retry loop" below); if the retry still cycles, `die()` with the SCC + offending mutations enumerated. |
| **must-include constraint** (apply-step enforcement on retry output) | a retried reconciler output that omits any operation from the bounded set leerie required for each named cycle. The retry prompt lists the legal operations per cycle (`drop_require` on either rename, `dependency_edges` in either direction, `merged_subtasks` in either direction); if the revised output doesn't include at least one for each cycle, `die()` with the missing-cycle diagnostic — surfaces "model defied a structural constraint" cleanly, never a silent cycle. |
| **unresolved-requires retry loop** (recompute unresolved set after applying reconciler output) | the reconciler's renames/added_subtasks/add_provide ops didn't actually close every gap. Common cause: model invented a new tag in `added_subtasks` and forgot to rename the original consumer's tag to match. On first detection, leerie tries one retry with a structured prompt that surfaces string-similarity hints from the post-mutation `provides` namespace. If the retry still leaves unresolved tags, `die()` with the structured report. |
| **unresolved-retry must-include constraint** (apply-step enforcement on retry output) | a retried output that omits any operation addressing the named unresolved entries. Legal addressing: `rename` on the (sid, tag), `add_provide` covering the tag, `added_subtask` whose provides includes the tag, `conditional_drop` on the consumer sid, `drop_require` on the (sid, tag) (consumer's `requires` is over-specified — an aggregate or coarser synonym of what the consumer itself provides, not a real cross-subtask dependency; the consumer stays in the plan, only the bad edge goes), or `unresolvable` on the (sid, tag). |
| **conditional_drops** apply step (DESIGN §5 resolution action; the worker emits it as `op: "conditional_drop"` in `tag_ops`) | a planner-emitted consumer subtask whose own `intent` declares it conditional on an unresolvable `extent: in_plan` precondition (signals like "no-op if X", "conditionally add", "drop if Y", "otherwise this subtask is dropped"). The apply step removes the named sid from its plan, prunes downstream `depends_on` references to that sid, and records the drop in `state.data["conditional_drops"]` (keyed by sid → `{reason, from_unresolved_tag}`). Distinct from `state.data["dropped_subtasks"]`, which records off-tree soft-drops from `_filter_offtree_subtasks` (phase 3) — same shape of audit signal, different cause. The apply step `die()`s if the target sid carries `_added_by_reconciler: true` (the op is restricted to planner-authored consumers — a reconciler-added subtask has no planner prose to convert into a structured drop). |
| **dropped_requires** apply step (DESIGN §5 resolution action — also a cycle-breaking op; the worker emits it as `op: "drop_require"` in `tag_ops`) | a consumer's `requires` entry that was over-specified by its planner — an aggregate, coarser synonym, or authoring-time decision the same subtask itself records, rather than a code artifact another subtask produces. The apply step removes the named `(sid, tag)` `extent: in_plan` entry from the consumer's `requires` list. The consumer itself stays in the plan (unlike `conditional_drops`, which removes the whole subtask) — only the bad edge goes. Apply mechanics are identical whether the op is emitted as a resolution (unresolved-tag retry, addressing an over-specified self-reference) or a cycle-breaker (the over-specified entry was what closed the cycle). Silent no-op on missing sid/entry, mirroring `renames`. |
| post-unresolved-retry cycle gate re-run | the retry's revised output reintroduces a cycle (e.g., a rename closes a loop). Same Tarjan check as the primary acyclicity gate; on cycle, `die()` with the SCC report. |

**Size-resolution retry loop.** When the size gate fires on the first
reconciler attempt (any `added_subtask` with `size: large`),
`phase_reconcile` deep-copies the pre-mutation plans, reverts the failed
mutations, builds a retry prompt (`_build_size_retry_prompt`) naming each
offending sid, its `provides`/`requires`/`depends_on`, and the explicit
decomposition rule ("emit one subtask per `provides` tag, or smaller
groupings that share state"), then respawns the reconciler once. Maximum
two attempts total — mirrors the cycle-retry shape; one extra reconciler
spawn on oversize runs only. No recommendation heuristic is computed
(unlike the cycle loop): the mechanical guarantee is rendered directly
into the retry prompt (and documented in `prompts/reconciler.md` on the
first attempt) — the retry is the enforcement. The size gate runs
*before* the acyclicity gate because oversize authoring is an upstream
defect — a `large` subtask bundling several capabilities is also more
likely to produce a cycle, so splitting first gives the cycle gate a
cleaner graph.

**Retry composition (snapshot refresh).** When multiple retries fire on
the same run (e.g., size retry succeeds and then the cycle gate fires),
each successful retry refreshes `pre_plans_snapshot` to the post-retry
state, so the next retry's revert restores the most recent good state
rather than undoing an already-successful split. The unresolved retry
doesn't refresh — it's the last gate before `phase_reconcile` returns.

**Cycle-resolution retry loop.** When the acyclicity gate fires on the first
reconciler attempt, `phase_reconcile` deep-copies the pre-mutation plans,
reverts the failed mutations, computes a *recommended* operation per SCC
from structural signals (in `_recommend_cycle_resolution`), builds a
retry prompt (in `_build_cycle_retry_prompt`) that names the SCC, the
mutations that closed each edge, the structural signals, the
recommendation, and the bounded "must-include" set of acceptable
operations, then respawns the reconciler worker once with that prompt.
Maximum two attempts total — mirrors the schema-fail retry shape at
`leerie.py: claude_p()`. Cost: one extra reconciler spawn on cycling
runs only; non-cycling runs pay nothing extra.

The recommendation heuristic is deterministic:

1. **Exactly one edge in the SCC is a planner-declared `depends_on`** →
   `drop_require` on the rename that closes the reverse direction
   (planner ordering wins).
2. **Else SCC members share `files_likely_touched`** → `merged_subtasks(into,
   from)`, `into` = smaller subtask by `success_criteria_seed` length
   (tie-break: lexicographic sid).
3. **Else** → `drop_require` on the rename whose `from` tag had no
   planner-declared producer pre-reconcile (speculative rename).
4. **Tie-breaker of last resort** → drop the lexicographically later rename.

The retry prompt presents the recommendation as the answer, not one of
several options, and explicitly forbids `unresolvable` for cycle
resolution — the mechanical floor (gate + must-include) is the
guarantee; the recommendation primes the model toward it.

**Unresolved-requires retry loop.** Symmetric architecture, fired by a
different gate: when post-mutation `_compute_unresolved_requires` is
non-empty (cycle gate already clear), `phase_reconcile` deep-copies the
pre-mutation plans, computes a string-similarity recommendation per
unresolved entry (`_recommend_unresolved_resolution`), builds a retry
prompt (`_build_unresolved_retry_prompt`) naming the unresolved `(sid,
tag)` pairs, top-3 candidate `provides` ranked by Jaccard, the
recommendation (if any), and the must-include set, then respawns the
reconciler once (max two attempts). Two guards filter candidates before
scoring — a self-loop guard (skip the consumer's own sid) and an
extent-aware guard (`in_plan` only). Cases (first match wins):

1. Unique top match, Jaccard ≥ 0.5 → `rename(sid, from=tag, to=top.tag)`.
2. Top match, Jaccard ≥ 0.7 (even if not unique) → same.
3. Else → no recommendation; model picks unaided (the common case).

`unresolvable` IS valid for this retry (unlike the cycle retry's strict
forbid) — if no real producer exists, surfacing that cleanly is right.
The mechanical floor (must-include validator + post-retry unresolved +
cycle re-check) catches every malformed revision; the recommendation is
best-effort.

### Phase 2¾ checks — `phase_overlap_judge`
| Check | Catches |
|-------|---------|
| **deterministic duplicate-provider floor** (`check_duplicate_providers(plans) -> list[str]`, `DUPLICATE_PROVIDER`) — runs **before** the cheap-skip and independently of `--skip-overlap-judge`, so it fires on every path including single-planner runs and a `plan_overlap_judge` `WorkerError` | two subtasks declaring the **same `provides` tag** whose `files_likely_touched` intersect (canonicalized via `_normalize_artifact_path`, same helper `NO_FILE_OVERLAP` uses) — duplicate work on the same file. Pure set logic (DESIGN *Language-to-JSON*). **Exclusion (load-bearing):** pairs sharing a non-`None` `_cofile_cluster` (§5½ (P1) *Sub-file*) are never flagged — otherwise legitimate sub-file splits flood the corpus with false positives. `check_duplicate_providers` remains advisory (`log()` only) — see the routing row below for the M11 resolution step. |
| **duplicate-provider merge routing** (`_duplicate_provider_merge_collisions(plans) -> list[dict]`, applied via `_apply_overlap_collisions`) — M11: the floor's detections are resolved, not just logged | mirrors the floor's detection logic, synthesizes one `resolution: "merge"` collision per flagged pair, and feeds them through the **same** `_apply_overlap_collisions` the judge's output uses (its `_would_cycle_after` guard, `skipped_redundant` dedup, anchor + transitive `survivor_of` cluster resolution). Runs above every cheap-skip, so single-planner and `--skip-overlap-judge` runs still get collisions resolved. Safe for the 3+-participant case because the merge chase carries every absorbed subtask's intent forward — a triangle of duplicate-provider pairs collapses to one survivor with the closing edge `skipped_redundant`. Persists to `state.data["duplicate_provider_merge_applied"]`, absent when nothing merged. |
| **cheap-skip when impossible** (fewer than 2 planners contributed subtasks, OR total subtask count < 2) | spurious worker spawn on single-planner / trivial runs. Log line `phase 2¾: overlap-judge skipped (single planner)` or `… (< 2 subtasks)`. |
| judge output validated against `SCHEMAS["plan_overlap_judge"]` | malformed judge response (retried once, then escalated). |
| **merge-feasibility backstop** (`_validate_overlap_judge_output`) — every `resolution == "merge"` must carry non-empty `merge_feasibility` | the judge skipping that discipline in `prompts/plan_overlap_judge.md` (§12 prompts advisory, code enforces). `die()` with the offending pair (`a_sid`/`b_sid`/`artifact`). |
| **`merge` apply step** (`_apply_overlap_merge`) | collapses the two subtasks: surviving sid is lex-smaller by default, or the `survivor_hint` when applying the anchor-survivor rule. Surviving subtask gets the union of `files_likely_touched`/`provides`/`requires`/`depends_on` (self-refs removed); `title` becomes `"{survivor.title} + {dropped.title}"`; `intent` concatenates survivor + absorbed intent (under an `--- Absorbed intent from {dropped.id} ---` marker) + a trailing merge-feasibility note (DESIGN §5 carry-forward invariant, so an already-once-merged intent chain isn't lost). `success_criteria_seed` becomes `"{survivor.criteria} AND {dropped.criteria}"`. Downstream `depends_on` referencing the dropped sid rewritten to the survivor. Recorded in `state.data["plan_overlap_applied"]`. |
| **`drop_a` / `drop_b` apply step** (`_apply_overlap_drop`) | removes the dropped sid; unions its `provides` into the survivor's (deduped, order-preserving, so downstream `requires` still resolve); drops any survivor `extent: in_plan` requires now self-looping; rewrites downstream `depends_on`. Title/intent/criteria are NOT copied — only capability-graph wiring is unioned. |
| **anchor-survivor rule** (`_apply_overlap_collisions` + `_compute_overlap_anchors`) | shared-endpoint clusters where one sid appears in 2+ non-`unresolvable` collisions (an *anchor*) survive every merge they participate in via `survivor_hint=anchor_sid`, overriding the lex-smaller default. Membership is bare appearance count with **no** semantic claim — a sid dropped twice is an anchor too and simply never uses the hint; do not read it as "the subtask that absorbs its partners" (false on roughly a third of resolution combinations). When both endpoints of a pair are anchors (e.g. a triangle's closing edge), falls through to lex-smaller. A `survivor_of` map rewrites later pairs against earlier survivors; fully-redundant closing edges are recorded `skipped_redundant`. A multi-drop cluster (below) collapses its N collisions into one `multi_drop_*` entry rather than one-per-collision. `_apply_overlap_drop` has a self-loop guard as defense in depth. |
| **keep-and-delete consistency gate** (`_validate_overlap_judge_output` + `_contradictory_drop_sids`) — self-contradictory output die()s before any mutation | a `drop_*` whose `dropped_sid` also *survives* another collision (kept as a merge endpoint or the non-dropped side of another `drop_*`) — no apply order satisfies both. die() names the sid, partner, artifact, and suggested resolution. **The predicate is `_contradictory_drop_sids` (survives-somewhere ∧ dropped-somewhere), NOT `_compute_overlap_anchors`** — conflating the two was the defect this gate was rewritten to remove; a sid dropped by 2+ collisions is an anchor by appearance but not a contradiction (that's the multi-drop shape below, sanctioned output). Gating on anchor membership instead killed runs whose judge output was correct, after full planner spend, unrecoverably. |
| **duplicate-pair rule** (`check_overlap_judge_output` `DUPLICATE_PAIR` + `_validate_overlap_judge_output` coalescing, keyed on `_collision_effect`) — a pair may repeat only when every row has the same *effect* | one pair colliding on several artifacts (one row listing every path, or one row per artifact — DESIGN §5 *Multi-artifact pair*; the per-artifact form is absorbed as `skipped_redundant`). Effect-identical rows are coalesced keeping every `artifact`/`merge_feasibility`. Rows whose effects **differ** (e.g. swapped-endpoint `drop_a` deleting opposite subtasks, or a `drop`+`merge` on one pair) surface as `DUPLICATE_PAIR` inside the retry loop, terminal at the keep-and-delete gate if unfixed (`tests/test_phase_overlap_judge.py` freezes the full 4×3 matrix). `resolution` alone is the wrong signal — swapped-endpoint rows share a resolution string. Gating on bare pair repetition instead `die()`d coherent output past the retry loop; a real run was killed this way after significant planning spend. |
| **multi-drop cluster apply** (`_apply_multidrop` inside `_apply_overlap_collisions`) — one sid dropped by 2+ collisions applies as a single whole-cluster operation, never by replaying the pairs | one subtask's surface jointly covered by several siblings (DESIGN §5 *Multi-drop*). Replaying pairs through `survivor_of` is **silent corruption** — pair 2 would drop a live sid the judge never named, and `_apply_overlap_drop` discards title/intent/criteria, so the loss is unrecoverable (damage scales with cluster size). Instead: union the dropped subtask's `provides` into **every** named survivor, drop each survivor's now-self-looping requires, remove the dropped subtask once, fan `depends_on` out to **all** survivors (dedup, self-refs removed — mirrors `_remap_vanished_deps`). Guarded by `_would_cycle_after` with a three-tier ladder: `multi_drop_fanout` (acyclic) → `multi_drop_degraded_single` (fan-out would cycle; fall back to `sorted(survivors)[0]`) → `skipped_would_cycle` (both would cycle). Survivors sorted for determinism. Each tier recorded in `state.data["plan_overlap_applied"]`; tier 3 keys on `resolution: "multi_drop"` to attribute it to this bucket rather than a pairwise skip — every `(action, resolution)` shape partitions and sums to `len(applied)`. |
| **`unresolvable` → `die()`** at plan time | genuine API contradictions the judge refuses to auto-merge. Names both sids, the artifact, the reason, and the next step (disambiguate or narrow the task). Message must not suggest `resume`: this precedes `_write_plan()`, so `state.json` has no `waves` key and `_run_phases()` dies on resume. Strictly better than the multi-hour wave-N integrator crash this phase prevents. |
| **per-resolution cycle avoidance** (`_would_cycle_after` inside `_apply_overlap_collisions`) — checked before each `merge`/`drop_a`/`drop_b` apply | a resolution's dependency-union can introduce a transitive cycle absent from the post-reconcile graph. Deep-copies `plans`, applies to the copy, rebuilds the predecessor graph, runs `_tarjan_sccs`; a would-cycle resolution is skipped (next row) and both subtasks kept separate for the integrator. Side-effect-free; sees every earlier-applied resolution. Covers `drop_*` too. |
| **post-merge acyclicity backstop** — Tarjan SCC immediately after `_apply_overlap_collisions` returns | with per-resolution avoidance in place this must never fire; a surviving cycle `die()`s with `_format_cycle_diagnostic`, framed as an orchestrator logic bug, not user-recoverable. Defense-in-depth against future drift, mirroring `_apply_overlap_merge`'s defensive missing-sid `die()`. |
| **`skipped_would_cycle` audit action** | a `merge`/`drop_*` whose apply would close a cycle. Recorded with both sids, artifact, resolution; `survivor_of` is **not** updated on a skip (both endpoints stay live for later collisions). The judge is not re-prompted — a global-graph property outside its pairwise competence. |
| **state persistence** | full judge output → `state.data["plan_overlap_judge"]`; post-apply mutations → `state.data["plan_overlap_applied"]`. Persisted before the phase returns; visible for resume-time replay debugging. |

The complementary `_warn_cross_planner_file_overlap()` check at phase 3
is **kept as-is** — it now serves as a complementary signal for file-
overlap that *doesn't* indicate surface collision (the deliberately-
permissive same-file-different-surface class).

### Plan validation — `_validate_plan` (after scheduling, before persisting the plan)
| Check | Catches |
|-------|---------|
| **budget feasibility** — `check_budget_feasibility()` runs at the same layer, immediately after `_schedule()` and before `_write_plan()`. Estimates remaining `claude -p` calls (implementers + conformers + integrators per wave + finalize), added to `worker_count` already spent, multiplied by `budget_safety_margin`, compared to `max_total_workers`. | a planner output too large for the configured `--max-workers` cap. `State.bump_workers()` is the runtime backstop (raises `WorkerError` mid-execution); this earlier check `die()`s with `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` at the cheapest possible moment. Opt-out via `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml`. See §"Budget feasibility preflight" and DESIGN §13. |
| ids match domain prefix (`bugfix-`, `feat-`, `refactor-`, `perf-`, `test-`, `deps-`, `config-`, `docs-`) | cross-domain collisions, audit ambiguity. The prompt receives the prefix directly as `ID_PREFIX = CATEGORY_ABBREV[domain] + "-"`, so it cannot drift from the validator's allowlist. |
| no `size: large` subtasks | planner OR reconciler violated the sizing constraint. Error names the actual author via `_added_by_reconciler` ("planner must split it further" vs. "reconciler must split it further (size-retry exhausted)"). This row is the post-merge backstop for both cases; the reconciler path is exercised through the phase 2½ size gate first. |
| no empty `success_criteria_seed` | implementer has no criteria starting point |
| every `depends_on` id exists | dangling edges silently dropped by the scheduler |
| every `requires` entry is `{tag, extent, reason?}`; `extent ∈ {in_plan, external}`; `reason` non-empty when `extent: external` | malformed planner output (JSON-schema-caught; this is the post-merge re-check) |
| every `requires` entry with `extent: in_plan` has a provider in some `provides` | unresolvable cross-domain dependency (`external` entries are declared out-of-graph) |
| no `files_likely_touched` entry matches `_is_protected_path()` (`.leerie/`, `.git/`, top-level `.claude/` outside the deliverable subtrees) | planner named a protected meta-directory as a deliverable — would fail `check_diff_scope` mid-run. Catching it here gives the planner a corrective-retry round instead of burning an implementer. Coordination artifacts should use `provides`/`depends_on` + the implementer's `artifacts` field (DESIGN §5), not `files_likely_touched`. |

`_warn_cross_planner_file_overlap()` runs immediately after `phase_reconcile`
(before `_validate_plan` and the scheduler) and **logs a warning, never
fails**, when two planners' subtasks share a `files_likely_touched` path. The
reconciler also consumes this signal as one input to the cycle-resolution
heuristic above (shared-file SCC members get a `merged_subtasks`
recommendation); the warning complements rather than replaces it.

`_warn_layer_gaps(plans)` runs at the same layer, two heuristic warnings
(DESIGN §5 *Migration-surface completeness*): (1) a `schema.prisma` path
touched with no subtask touching seed/migration files — database-init gap;
(2) `provides` tags with env/bootstrap/secret/credential keywords but no
subtask touching `.env.example`/env docs — env-contract gap.

`_filter_offtree_subtasks()` runs at the same layer (after
`_warn_cross_planner_file_overlap`, before `_schedule()`) and **soft-drops
any subtask whose `files_likely_touched` resolves outside the run's primary
repo root** — typically a leak into a read-only inspect-dir mount. Drops are
recorded in `state.data["dropped_subtasks"]`. Must run before `_schedule()`
since `phase_execute` iterates `state.data["waves"]` (computed by
`_schedule()`) and a later drop would leave `waves` referencing a sid with no
spec on disk. A soft drop, not `die()`, because resume does not re-run the
planner pipeline and needs `state.data["waves"]`. A dropped subtask whose
`provides` a survivor `requires` is caught by the unresolvable-requires check
above.

### Per-subtask checks — in `_settle_subtask`, every worker result
| Check | Catches | On failure |
|-------|---------|-----------|
| `_validate_result()` — `incomplete-handoff` with missing checkpoint file | session-limit no-op; `--max-turns` with no checkpoint written; **worker reaped mid-turn** (e.g. an OOM-killed backgrounded build before the checkpoint was written) | **Rescued when the worktree holds commits, else Retryable** (`failure_kind="empty_handoff"`). `_settle_subtask` calls `_branch_has_commits_ahead` (True only when the worktree exists, git succeeds, and there are commits ahead of the run branch — distinct from `check_branch_has_commits`'s indeterminate `None`); if there are commits ahead, the worker produced a real deliverable and is settled `complete` (advisory conformance records whatever step didn't finish) instead of discarded — `fail()` would `_reset_subtask_worktree` and destroy the committed diff. Only a genuine no-op (no commits) stays retryable; a gone worktree / git failure is never mistaken for a real deliverable. Confidence gate and dirty-worktree fail are skipped for a rescued result. See DESIGN §9. |
| `_validate_result()` — other cross-field invariants | `handoff` with null `checkpoint_path`; `blocked` with no blocker; `failed` with no summary; `needs-clarification` with no `clarification_question` / invalid `checkpoint_path` | **Terminal** (`failure_kind="broken"`) |
| `check_branch_has_commits()` | `complete` claim, nothing committed *and* no `artifacts` returned. A non-empty `artifacts` array (DESIGN §5 *Artifact passing*) is a substitute deliverable — research-style subtasks pass without commits. | **Rescued when the criteria are already met on the run-branch HEAD, else Retryable.** Before failing a no-commits `complete`, re-runs the `satisfied_probe` against `success_criteria_seed` on the **run-branch HEAD** (not the base tree) — a sibling subtask in an earlier wave may have committed this subtask's deliverable mid-run (DESIGN §8 *The mid-run sibling case*; also covers already-satisfied-on-base, since the probe judges *whether*, not *who*). If satisfied: settled `complete`, recorded in `state.data["dropped_subtasks"]` with `reason: "already_satisfied_mid_run"`; a `state.data["conformance"][sid]` sentinel keeps `_get_progress` from classifying it as stuck `in_conformer`. Only probed when `success_criteria_seed` is non-empty. The probe defaults to *not satisfied* on any error, so it can only rescue, never mask, a real no-op (DESIGN §12). |
| dirty worktree check | uncommitted changes that vanish on integration | **Retryable** |
| `check_diff_scope()` | `.leerie/` or `.git/` in the diff; any `.claude/` path except `.claude/agents/`, `.claude/commands/`, `.claude/skills/` (the documented user-deliverable subtrees — never `settings.json` or a top-level `.claude/` file) | **Terminal** (protected path); scope-volume warning is non-fatal (touched > max(3× expected, 5), or > 15 regardless) |
| `_validate_checkpoint()` — on `incomplete-handoff` | required section missing/empty/whitespace/placeholder-only (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`, trailing punctuation ignored); a `## Files touched` path no longer exists and isn't flagged `[deleted]` | returns `blocked` |
| `_retryable_failure(kind)` — on `status='failed'` from the worker | worker self-report of failure | `failure_kind="broken"`; **terminal** on first occurrence |

`_validate_result()` accepts `complete` regardless of what
`criteria_results` carries — empty, missing, or `met:false` entries are all
valid (DESIGN §8: the criteria file is informational). An unmet-criterion
self-report is recorded for telemetry and surfaces as a warning, never
affecting terminal status. The criteria-file lock and the
`criteria_revision_proposal` channel were both removed when the criteria
file's load-bearing role retired (DESIGN §9).

### Per-subtask post-work conformance — in `_settle_subtask`, success path only

Triggered only when an implementer's `status: "complete"` has already cleared
every check above (commits present, worktree clean, no protected path
written). None of the other terminal statuses (`incomplete-handoff`,
`needs-clarification`, `blocked`, `failed`) invoke the conformer.
Implements DESIGN §9 *Post-work conformance*.

| Step | Function | Behavior |
|------|----------|----------|
| Discover rules files | `_discover_rules_files(repo_root)` | Existing paths from a fixed, capped allowlist (`CLAUDE.md`, `AGENTS.md`, `.agent.md`, `.cursorrules`, `.windsurfrules`, `docs/CLAUDE.md`, `docs/AGENTS.md`, `docs/CONVENTIONS.md`, `docs/STYLE.md`, `docs/DESIGN-SYSTEM.md`, `docs/DESIGN_SYSTEM.md`, `docs/UI.md`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`), deterministic order, never raises, `[]` when nothing matches. The design-system candidates exist so a repo's component/color/banner conventions reach both conformer and implementer (DESIGN §9). |
| Run conformer | `_run_conformer()` | One `claude -p` invocation with `ACT_TOOLS`, `--dangerously-skip-permissions`, `SCHEMAS["conformer"]`. Optional `extra_feedback` appended to the user prompt (Pattern B backgrounding-retry feedback). `WorkerError` → `None` (warning). Output passed through `_expand_conformer_output()` (N29), restoring the flattened wire shape into the four arrays downstream steps expect. |
| Validate output | `_validate_conformance_result()` | Cross-field invariants — `rule_violations_residual` non-empty requires `rules_files_read` non-empty; each `rule_violations_fixed` cites a non-empty `rule`; each `docs_updates`/`tests_updates` cites an existing `path`. Failure → warning, loop breaks. |
| Re-run gates | `check_branch_has_commits`, dirty-worktree check, `check_diff_scope` | Same functions as the implementer, re-applied to conformer commits. A protected-path violation triggers `_rollback_conformer_commits()` (reset to `before_sha`), recorded as a warning, **not** `failed`/`blocked`. |
| Clobber-survival check | `_clobbered_owned_files(worktree, run_branch, impl_head_sha)` + `_blob_sha` | DESIGN §9 *No clobbering the implementer's work*. `impl_head_sha` snapshotted **once before the round loop** (a per-round HEAD would miss a round-0 clobber). Owned set = `git diff --name-only <run_branch>..<impl_head_sha>`; a clobber is a deletion at HEAD or a blob reverted to base (three-way `_blob_sha` compare via `git rev-parse --verify -q`); a legit conformer edit leaves a distinct third blob, not flagged. Warns always; under `--strict-conformer` also rolls back to the implementer HEAD **and blocks** — a `clobbered_files` flag forces a block even when `_conformance_clean` is True. Not auto-rolled-back in advisory mode (a legitimate revert-to-base is git-indistinguishable from a clobber). The final-tree pass uses `base=` a **snapshot SHA** (`_merge_base_sha(staging, working_branch, staging_before_sha)`), never `run_branch` — the staging worktree has the run branch checked out, so passing that name collapses the two blob lookups and reports every final-conformer edit as `(reverted-to-base)`. The per-subtask call site is correct with `base=run_branch` since a subtask worktree sits on a genuinely different ref. |
| Loop bound | `caps["conformance_rounds"]` (default 3) | Re-runs on malformed output or remaining residuals; exhausting the cap is a warning, not a failure. |
| Loop-continuation predicate | `_conformance_clean(conf_res, baseline)` + `_baseline_red_axes(baseline)` | DESIGN §9 *The signal that continues the loop is a delta, not a verdict*. True (ends loop) when nothing left is this subtask's responsibility. `baseline` is `st.data["conformance"]["_baseline"]` or `None`. **Checked ahead of the red-axis exclusion: `ran && !measured` returns False** — an unmeasurable axis (runner absent, or `_is_fork_exhaustion`) is a third state, not "red at baseline." Then two exclusions keyed on `_baseline_red_axes()` (admits only `_BLT_AXES` names): (1) `ran && !passed` red at baseline doesn't block; (2) a `rule_violations_residual` whose **`axis` field** is red at baseline doesn't block. Everything else blocks — an unlabelled residual, or a failure on an axis green at baseline (a real regression). `axis` is read from the schema field, never inferred from `rule`/`why_not_fixed` prose (*Language-to-JSON*); optional on the schema, absence gates. `baseline=None` reproduces the pre-change absolute-verdict behaviour byte-for-byte. |
| Axis selection | `resolve_blt_scoped(repo_root)` + `_changed_files(worktree, run_branch)` + `_select_subtask_axes(blt, scoped, files, base_ref, mode, test_globs)` | DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*. Resolved once per subtask before the round loop — the changed-file set is the implementer's diff; conformer commits don't widen scope. `mode` is `st.data["subtask_tests"]`. `resolve_blt_scoped` reads `test_scoped`/`build_scoped` from `.leerie/config.toml`, else infers two: `npx vitest related --run {files} --passWithNoTests` (vitest config present), `npx jest --findRelatedTests {files} --passWithNoTests` (jest config), and `npx tsc --noEmit` as `build_scoped` when `tsconfig.json` exists and canonical build isn't already `tsc`-shaped. Kept separate from `_infer_build_lint_test` so the launcher's mirrored bash inference stays untouched. No pytest inference, no lint tier. `_changed_files` uses `git diff -z --name-only` (`-z` avoids git's C-quoting of non-ASCII paths breaking `splitlines()`). `_render_scoped` `shlex.quote`s each path, returns `None` (falls back to canonical) when a `{files}` template has no files — rendering bare would run EVERYTHING. A `{test_files}` variant substitutes only `_is_test_file`-matching members with the same absence rule, for runners with no source→test impact analysis (pytest treats a non-test path as an ERROR, exit 4, and poisons the whole invocation). `_is_test_file` matches a `tests/`/`test/`/`spec/` segment or `test_*.py`/`*_test.*`/`*.test.*`/`*.spec.*`; `test_file_globs` in `.leerie/config.toml` replaces the built-ins. `_render_scoped` also hard-skips an unsubstitutable placeholder (`_UNKNOWN_PLACEHOLDER_RE`, warned once) rather than shipping a literal brace — an unguarded skew between a newer `.leerie/config.toml` and an older installed orchestrator would otherwise turn every subtask RED. The scan runs against the TEMPLATE with placeholders stripped, never the rendered command (a changed path may legitimately contain braces). Any axis whose proxy doesn't resolve falls back to canonical; the scope label is `scoped` only if at least one axis used a proxy. |
| Measure (pre / post) | `_measure_axes(worktree, axes, st, caps, ...)` | Run immediately before the round (feeds `BLT_RESULTS:`) and again after (overwrites the worker's self-report). Memoised via `blt_results`, so the post measurement is free when the round committed nothing (measured, 182/224 rounds). `--subtask-tests off` yields `{}` and skips both. Each axis is bounded by `caps["worker_timeout_sec"]` (5400s default) — no tighter per-axis ceiling. |
| Worktree deps | `_ensure_worktree_deps(tree, st, caps, ...)`, from inside `_measure_axes` | DESIGN §6½ *Who runs that install*. Applies the provision recipe's install/build on the FIRST axis actually measured — not at worktree creation, since a config/docs-only subtask (44/91 in the motivating run) correctly skips it. Memoised on the resolved absolute path in module-level `_DEPS_INSTALLED` (per-process fact, not run state). Non-fatal: a failed install surfaces as whatever the BLT command reports, classified by `_runner_missing`. Collapses 263 installs across 161 worker logs into one per worktree. |
| Apply (twice per round) | `_apply_measured_axes(conf_res, pre)` then `(conf_res, post)` | Replaces `conf_res["build"\|"lint"\|"tests"]` with the orchestrator's measurement into a NEW dict (raw worker payload stays as-emitted for telemetry). **Both applications are load-bearing.** `post` is the ordinary tail case. `pre` runs as soon as there's a dict — before `_validate_conformance_result` — because three gates `break` before the tail: malformed result, protected-path violation, strict-mode clobber. Without it those paths would carry the conformer's *claimed* axes into `_conformance_clean`'s `--strict-conformer` decision — gating on a self-report, the exact thing this phase stopped doing. `pre` is also the *accurate* measurement there, since both exits roll the worktree back toward pre-round state. Pinned by `test_the_overwrite_is_applied_twice_per_round`. |
| Round delta | `_round_axis_regressions(pre, post)` | An axis green before the round, red after — a regression the conformer just introduced. Appended to `warnings`, fed into next round's feedback, ANDed into the loop-exit condition so a self-inflicted break earns another round. Never fires when either side is unmeasured, when command strings differ, or on red→red (inherited debt). |
| BLT-axis observability + feedback | `_emit_bash_axis_warnings()` | Parses the per-worker JSONL conformer log after each round for two feedback-injected patterns: (1) **multi-invocation** — `ran <AXIS>_CMD K times in one round` (progressive testing is legitimate, but a provably-redundant re-run wastes an expensive cycle); (2) **retry-after-backgrounded** — `<AXIS>_CMD auto-backgrounded … followed by another <AXIS>_CMD invocation`, the "retry-instead-of-recover" pattern. Both formatted via `_format_check_feedback()` and passed to the next round. `_BLT_AXIS_RES` holds compiled per-axis regexes (test/build/lint runner invocations); `_count_orphaned_bg_axis` also accepts `BashOutput shell_id=<id>` polls as a valid recovery path. |
| Attach result | — | `res["conformance"]` and `res["conformance_warnings"]` added to the implementer's result; the subtask still returns `complete`. |

The phase is advisory: **no path through it produces a `failed` or `blocked`
subtask status.** Build/lint/test failures, malformed conformer output,
crashes, gate violations, and exhausted rounds all surface as
`conformance_warnings` and non-fatal log lines. Per §12: *discovery* of rule
files, *schema validity* of conformer output, and *protected-path
invariance* are code-enforced; whether the conformer made the right
docs/tests/rule-violation calls is left to the worker.

### Wave-level checks (after integration)
| Check | Catches |
|-------|---------|
| `_scan_conflict_markers()` | unresolved `<<<<<<<` markers in the run-branch worktree after integration — deterministic safety net |

There is no LLM wave-level re-validation. An earlier `validate_wave` ran a
deterministic test-runner fast-path and an LLM validator over per-subtask
criteria with a re-spawn loop; removed when the criteria file's load-bearing
role retired (DESIGN §8, §9). Per-subtask quality is the implementer's
confidence gate; the wave-level safety net is the conflict-marker scan.

### Post-integrator checks (after an integrator handles a conflict)
Verify the integrator honored DESIGN §6's *behavioral* conflict-resolution
contract — the integrator prompt (`prompts/integrator.md`) carries the
behavioral spec (read every involved subtask's intent, preserve each side's
intent, call irreconcilable cases a `design-conflict`); the orchestrator
only checks the outcome.

| Check | Catches |
|-------|---------|
| `check_merge_committed()` | integrator returned `resolved` but left the worktree mid-merge (`MERGE_HEAD` present) or staged-uncommitted — **terminal**: merge aborted, run stops |
| `check_integrator_commit()` | integrator merge commit touched `.leerie/` files — non-fatal warning |
| integrator status `design-conflict` / `failed` | unresolvable conflict — **terminal**: in-progress merge aborted, run branch left clean at the last good wave, diagnosis saved |
| integrator **crash** (`_run_checked_loop` returns `None`) | infrastructure failure, not a verdict — `_rescue_integrator_work()` captures the in-progress resolution to `refs/leerie/rescue/<run-id>/<sid>` **before** the merge is aborted, `blocked[sid]` recorded, die message names the ref + its `cherry-pick --no-commit` recovery command. `resume` retries the integration |

`_rescue_integrator_work(staging, sid, run_id) -> str | None` returns the rescue
ref, or `None` when nothing to save or the capture failed. **Not** gated on
`check_merge_committed`: a crashed integrator typically dies mid-resolution
with no merge commit, exactly the case worth rescuing (DESIGN §12). Stages
into a throwaway `GIT_INDEX_FILE` seeded from HEAD (`read-tree` → `add -A` →
`write-tree` → `commit-tree`) because `git stash` refuses a conflicted tree;
the real index/working tree are never touched, untracked files are
captured. Every git failure degrades to `None` — a rescue failure must
never mask the crash. `run_proc` gained an `env: dict[str, str] | None`
param for this.

### Resume integrity — `_validate_resume_state()`
Enforces (one half of) DESIGN §6's "the run branch is the resume contract"
invariant — state.json's `waves`/`completed_waves` say *which* wave to
resume; the never-reset `leerie/runs/<run-id>` branch holds *the work*
every prior wave produced. Both must be coherent for resume to be safe.

On `resume`: asserts `task` is present and non-empty; asserts `waves`,
`completed_waves`, `subtask_status` are well-formed *if present*. `waves` is
intentionally optional — a run interrupted before scheduling has none, and
per DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a
`waves` gate," `_run_phases` walks the planning-phase sequence (classify →
plan → reconcile → overlap-judge → adherence-gate → off-tree/satisfied
filters → schedule) and re-enters at the first phase whose `plans_after_*`
checkpoint key is absent, reusing the last completed phase's persisted
`plans` rather than re-deriving from scratch. Rejects corrupt/hand-edited
state without rejecting a legitimately-early interruption.

The `except SystemExit` handler in `main()` guards `st.save()` behind
`st.data.get("task")` so that a failed `resume` (which `die()`s before
state was loaded) does not poison the host-side `state.json` with a bare
`{"finished_at": …}` stub — that would block subsequent resume attempts
with "no usable task" instead of the clearer "no state.json".

`_orchestrate()` also re-resolves the source-of-truth preference on every
`resume` and overwrites `state.json`'s `source_of_truth_pref` with the
fresh value, so a change to `leerie.toml` or `LEERIE_SOURCE_OF_TRUTH`
between runs takes effect on resume.

Per-worker models are likewise re-resolved on every `resume` from the
current CLI flags, env, and `leerie.toml`. They are *not* persisted in
`state.json` (they are startup config, not run state), so a change to
`LEERIE_MODEL`, `--model`, or the per-worker overrides between runs
takes effect immediately on resume.

### Concurrency model
The orchestrator runs on a single `asyncio` event loop. Each `claude -p`
worker is spawned via `asyncio.create_subprocess_exec` (wrapped by
`run_proc`) with `start_new_session=True`, so each worker is its own
POSIX session and process-group leader. Parallel workers within a wave run
via `_gather_or_cancel` (an `asyncio.gather` wrapper that on the first
exception cancels every other in-flight task and awaits finalization
before re-raising) under an `asyncio.Semaphore(max_parallel)`. `State`
carries no lock — coroutines interleave only at `await` points, never
inside a `st.data[k] = v; st.save()` pair. `State.save()` writes a temp
file then `os.replace()`s for atomicity against a process crash.

Subprocess cleanup is four-layered, addressing two leak classes plus mid-run pressure reduction:

1. **Lifetime descendant tracking (`_DescendantTracker`).** A per-worker
   asyncio task polls `_enumerate_descendants(proc.pid)` every ~0.5s and
   accumulates every PID ever observed as a descendant. On every exit
   path, `stop_and_reap()` SIGKILLs the accumulated set. This is the
   load-bearing fix for Claude Code's Bash tool with `run_in_background:
   true`: the tool wrapper spawns the user command in a detached POSIX
   session and can exit while the command keeps running; by the time
   `claude -p` exits the backgrounded command has reparented to PID 1 and
   is invisible to a post-hoc PPID walk — but the tracker observed it
   mid-flight and has its PID.

2. **Abnormal-exit subtree termination (`_terminate_proc_tree`).** On
   `KeyboardInterrupt`/`SIGTERM`/`RateLimitedExit`/any other
   `BaseException`, `run_proc`'s and `_invoke`'s catch-all handlers call
   `_terminate_proc_tree(proc)`: SIGTERM to the worker's process group
   (`os.killpg`) AND every descendant via PPID walk, wait
   `_PROC_TREE_GRACE_SEC = 2.0`, then SIGKILL survivors via both
   mechanisms. The PPID walk is needed because Bash-tool subprocesses sit
   in a *different* POSIX session than `claude -p`, so `killpg` alone
   misses them. Exception paths run the tracker reap *after* this,
   catching anything orphaned during the run.

Layers 1 and 2 compose: `_terminate_proc_tree` is broad and synchronous
(kills the attached subtree), the tracker is narrow and historical (kills
only what it observed, including processes that have since reparented
away). Neither alone is sufficient.

3. **Mid-run PID reaping (`_poll_loop` + `_reparented_orphans`).** A
   pressure-gated reducer under the PID-exhaustion-detection backstop
   (below) that proactively reaps orphans before `pids.max` is reached.
   `_DescendantTracker` takes an optional `cgroup_sid`; each `_poll_loop`
   cycle, when set, computes the pressure ratio `pids.current / pids.max`
   via `_cgroup_stat`. Reaping arms at `_PID_REAP_HIGH_WATER = 0.90`, then
   `_reparented_orphans(self._seen, min_age)` SIGKILLs the killable set
   oldest-first until the ratio drops below `_PID_REAP_LOW_WATER = 0.75`.
   `_reparented_orphans(seen, min_age=None) -> list[int]` snapshots `ps
   -eo pid,ppid,etimes` (raises on failure → `[]`, never parses garbage)
   and returns, oldest-first, PIDs from `seen` that are alive, reparented
   to init or the orchestrator (post-`_become_subreaper`), and at least
   `min_age` seconds old. `min_age`'s `None` sentinel resolves
   `_PID_REAP_MIN_AGE_SEC` *at call time* rather than binding once at def
   time, so patching the constant moves the floor.

   **Two-tier age floor (DESIGN §6 *Why a single 60s floor is not
   enough*).** `_poll_loop` selects the floor from the same pressure
   ratio: at or above `_PID_REAP_CRITICAL_WATER = 0.90` it passes
   `_PID_REAP_CRITICAL_AGE_SEC = 5`; below it, `_PID_REAP_MIN_AGE_SEC =
   60`. Without the critical tier the reaper arms at 90% and finds every
   candidate younger than 60s — a disabled reducer. `_PID_REAP_CRITICAL_WATER`
   equals `_PID_REAP_HIGH_WATER` (both 0.90) but stays a separate named
   constant since arming and floor-escalation answer different questions.
   (DESIGN §6 carries the measurement behind the tier.)

4. **Zombie reaping (`_become_subreaper` + `_zombie_reaper`).** Handles
   *zombies* (`<defunct>`, not yet `wait()`ed), which also count against
   `pids.max`; the container PID 1 is not a reaping init, so orphaned
   `git`/`ssh-agent` descendants reparent to it and rot (DESIGN §6
   *Zombie reaping*). `_become_subreaper()` — called once early in
   `main()` — issues `prctl(PR_SET_CHILD_SUBREAPER, 1)` (Linux-guarded, a
   logged no-op elsewhere) so orphans reparent to the orchestrator.
   `_zombie_reaper()` is a background asyncio task, same lifecycle as
   `_memory_sampler`. It is an **allowlist, never a `/proc` scan**:
   `os.waitpid(pid, WNOHANG)`s only PIDs in `_REAPABLE_PIDS`.

   **`ChildProcessError` (ECHILD) does NOT discard the PID (N36).** For a
   live grandchild, ECHILD means "not ours **yet**" — while the worker
   lives the orchestrator isn't the pid's parent yet. Discarding would
   drop it permanently, so when the worker exits and the orphan reparents
   it's no longer on the allowlist and nothing ever waits it. The arm
   instead disambiguates with `_pid_still_exists(pid)` (a `/proc`
   existence check kept in its own function so `_zombie_reaper`'s own
   source never mentions `/proc`): alive → retain and retry; gone →
   discard. Retention is bounded by `_ECHILD_RETRY_MAX_SEC` (60s,
   first-ECHILD timestamps in `_REAPABLE_PID_FIRST_ECHILD`), safe
   only because `_DescendantTracker._poll_loop` re-marks every observed
   descendant every `_DESCENDANT_POLL_SEC` (0.5s), so an aged-out pid is
   re-added with a fresh window (both constants pinned together in
   `tests/test_subreaper.py`). Any other `OSError` discards immediately.

   `_mark_reapable(pids)` populates that set (minus
   `_ASYNCIO_MANAGED_PIDS`), fed from `_DescendantTracker._poll_loop`'s
   `_enumerate_descendants` snapshots — subtrees leerie observed and
   therefore owns. `_orphan_zombie_children()` **no longer exists**: any
   reaper that *discovers* PIDs is wrong, because a PID between `fork()`
   and asyncio's `os.pidfd_open()` is in no registry (DESIGN §6 *Zombie
   reaping*; the scanning design once killed `preflight`'s own `git
   config` PID on 40/40 real runs, fabricating rc=255). `_signal_pids`
   deliberately does not `waitpid` — `_zombie_reaper` is the single
   reaping point. `_reparented_orphans` accepts `ppid in (1,
   os.getpid())` since orphans now reparent to the orchestrator.

**PID-exhaustion detection (`_cgroup_stat` + `_read_stream` probe).** The
above runs at worker *exit*; leaked `run_in_background` subprocesses
accumulate against the worker cgroup's `pids.max` (default
`worker_pids_max = 2048`, resolved `--worker-pids-max` >
`LEERIE_WORKER_PIDS_MAX` > `leerie.toml` > `DEFAULT_CAPS`) *during* the
run. Once hit, every `fork()` in the subtree returns `EAGAIN`, so every
`Bash` tool-call fails and the worker spirals without diagnosing the cause
(DESIGN §6 *Detecting PID exhaustion*). The broker's read-only `stat <sid>`
verb → `OK <pids.current> <pids.max> <pids.events.max>
<memory.events.oom_kill>`; client `_cgroup_stat(sid) ->
tuple[int,int,int,int] | None`. `_read_stream` keeps a bounded
`deque(maxlen=_PID_EXHAUSTION_WINDOW)` of recent tool-result outcomes via
`_tool_result_outcome(event)` (non-tool-result events return `None`, not
counted). When the window holds `≥_PID_EXHAUSTION_ERROR_THRESHOLD` (3)
errors **and the latest result is itself an error** (so a healthy-but-failing
worker's interleaved successes don't re-trigger the probe), it calls
`_cgroup_stat`; if `current >= max` or `pids.events.max` is climbing it
logs the cause, relabels the tool-fail summary, and raises `WorkerError`
routed through `_terminate_proc_tree` + tracker-reap to the callers'
normal handling (implementer → retryable `incomplete-handoff`; conformer →
advisory `None`). `_is_fork_exhaustion(text)` is a cheap `EAGAIN`-string
fast-path; the cgroup probe is authoritative. A window (not a consecutive
counter) is required because tool-results are never adjacent — the
model's assistant turn always sits between them.

**Memory-OOM naming (`_invoke`'s no-envelope path + `_settle_subtask`,
DESIGN §6 *Detecting memory OOM*).** A command that overshoots `memory.max`
is killed with a bare `Killed` — no tool-result error, often no `result`
event before `claude -p` is reaped. `_read_stream` tracks `last_bash_cmd`
(the most recent `Bash` command, first line) alongside the PID-exhaustion
window. In `_invoke`'s `finally`, `final_stat = _cgroup_stat(cgroup_sid)`
is read immediately before `_cgroup_destroy` (the last point a read is
possible). When `envelope is None` and `final_stat[3]` (`oom_kill`) is `>
0`, `_invoke` raises `WorkerError(f"worker {sid} was OOM-killed on
\`{last_bash_cmd}\` (memory.max={cap} GiB) — raise --worker-memory-max or
lower --max-parallel")` instead of the generic message.
`_run_implementer`'s `except WorkerError` threads that text into the
synthesized `incomplete-handoff` envelope's `summary` unchanged.
`_settle_subtask`'s `empty_handoff` handling (rescue and no-commits
branches alike) prefers `res.get("summary")` — the worker's own
diagnostic — over `_validate_result`'s generic message, so a named OOM
survives even when the subtask ultimately terminates via the retry cap.

### Abnormal exit and rate-limit contract (DESIGN §6 *Cleanup on abnormal exit*)

All abnormal exits — Ctrl-C, SIGTERM/SIGHUP, WorkerError, unhandled
exception, or `RateLimitedExit` — route through
`_cleanup_on_abnormal_exit(st, full_purge=False)`. **State.json, the
run branch, per-subtask branches, and implementer checkpoints all
survive**; only worktrees are removed (and re-created idempotently on
`resume` via `scripts/new-worktree.sh`).

Per-worktree removal has a 240s timeout (a large worktree can be hundreds
of MB / tens of thousands of files under N-way concurrent disk
contention). Failures are non-fatal and counted; if any failed, cleanup
logs a pointer to `scripts/cleanup.sh --run-id <id>` to finish manually —
best-effort, a stale worktree is the worst case, not a corrupted run.

Per-worker `subprocess.TimeoutExpired` from `_invoke` (`worker_timeout_sec`,
default 5400s/90min) is caught by both `_run_implementer` (returns an
`incomplete-handoff` envelope, same path as WorkerError) and
`_run_conformer` (logs + returns `None`). Without these catches the
timeout escapes through asyncio cancellation into `main()`'s catch-all and
dumps a multi-KB traceback — including the full `claude -p` command line
— to the terminal.

`RateLimitedExit` is raised by `_detect_session_limit(text)` inside
`_summarize_stream_event` on the verbatim Claude Code subscription message
`"You've hit your session limit · resets <h>:<mm><am|pm> (<IANA TZ>)"`, or
by the same function's `rate_limit_event` branch when the protocol-level
`status` falls outside `{"allowed", "allowed_warning"}` — matching
everything-not-allowed avoids hardcoding a terminal-status guess that
could go stale. The protocol path parses `resetsAt` (Unix timestamp) into
a UTC `reset_at`; the text path parses wall-clock + IANA tz. A **third**
raise site is the `_invoke` no-result-envelope branch: when a stream
truncates with no `result` event *and* a mid-stream `rate_limit_event`
shows `overageDisabledReason in {"out_of_credits", "out_of_overage"}`
(latched via `nonlocal overage_blocked`), `_invoke` raises
`RateLimitedExit(reset_at=None, out_of_credits=True, raw)` — raised here,
not in `_summarize_stream_event`, so the latch survives to the post-stream
check even at quiet verbosity. The latch keys on `overageDisabledReason`,
**not** `overageStatus == "rejected"` (a standing, non-exhaustion state for
orgs with overage disabled) — keying on the latter misclassified unrelated
truncations. An `org_level_disabled` truncation takes the ordinary
`WorkerError` path.

Either source produces a `reset_at: datetime | None` (parse failure →
`None`, never a wrong-time guess). `main()`'s `except RateLimitedExit`:
when `reset_at` is set, cleanup → sleep until the moment + 30s margin →
`os.execv(sys.executable, [sys.executable, __file__, "resume", "--run-id",
<id>])` to re-exec the orchestrator itself (not the launcher, which isn't
baked into the image). `worker_count` persists across the re-exec, so a
repeatedly-limited run still respects `--max-workers`. When `reset_at` is
`None` (unparseable message), sleep a fixed `RATE_LIMIT_RETRY_BACKOFF_SEC`
(300s) and re-exec the same way. Both clock-based arms route through
`_sleep_then_reexec(st, wait_seconds, reason) -> int | None`: `None` when
`os.execv` succeeds (return unreachable), else an exit code — `130`
(Ctrl-C), `128 + signum` (SIGTERM/SIGHUP), `EXIT_LOCKED` (75, the
should-never-happen `os.execv` failure). The caller sets `abnormal = False`
(the helper already ran cleanup).

The `out_of_credits=True` arm does **not** auto-resume — no reset clock
(clears only on top-up/billing cycle). `main()` runs
`_cleanup_on_abnormal_exit` directly, logs a `leerie resume <id>` hint,
sets `exit_code = EXIT_LOCKED`, `abnormal = False`. Checked *before* the
`reset_at` branch; `_sleep_then_reexec` never called here. Out-of-credits
deliberately keeps the surface-and-pause semantics rate-limits no longer
use.

A terminal auth failure (`_is_terminal_auth_failure`, §3) copies this arm
verbatim — an expired session has no clock-based reset either.

**Auto-resume override persistence.** The re-exec passes only `resume
<id>` as argv — CLI overrides on the original launch (`--model`,
`--max-workers`, `--max-parallel`, `--confidence-rounds`,
`--source-of-truth`, `--clarify`, `--no-push`) are **not** propagated;
they fall back to `LEERIE_*` env vars and `leerie.toml`, re-resolved on
every `resume`. Configure non-default settings via env/`leerie.toml`
rather than a single CLI flag if they must survive an auto-resume; a
manual `resume` can re-supply CLI overrides.

Ctrl-C (SIGINT) is **resumable** — same contract as every other abnormal
exit. The explicit "throw this away" gesture is `scripts/cleanup.sh
--run-id <id> --branches`, not Ctrl-C.

---

## 5½. Mechanical-feedback loops (CRITIC pattern)

Every worker except the PR writer runs inside `_run_checked_loop` — a
generic async function that calls the worker, runs deterministic
structural checks on the output, and, for callers that pass
`make_feedback_prompt`, re-invokes with formatted feedback if issues
are found. The pattern is grounded in the CRITIC framework (ICLR 2024):
self-correction works only with external tool-verified feedback, not
intrinsic self-review.

Three callers — `wiring_judge`, `provision_judge`, and `integration_judge`
— are "detect-and-die, single pass": they pass no `make_feedback_prompt`,
because none can mechanically act on a found semantic defect the way a
planner can add a subtask or a classifier can add a category. A round
that finds issues stops the loop immediately — a further round would
attack the identical input with only a fresh, non-deterministic judge
session, which can only *lose* the finding on a lucky re-roll, never gain
information. The oscillation guard below doesn't apply here (no re-drive
between rounds). The `WorkerError` infrastructure-crash retry is
orthogonal and still applies to all callers regardless of
`make_feedback_prompt`.

### Core functions

| Function | Purpose |
|----------|---------|
| `_replan_domain_closure(plans, targets)` | Domains that must re-plan together with `targets` — the transitive closure across both the id (`depends_on`) and tag (`requires`→`provides`) channels, so a re-plan vanishing every id a domain used never dangles a still-live edge into it. Domains are subtask-id prefixes. Consumed by `phase_overlap_judge`'s unresolvable recovery; `phase_plan(..., domains=…)` takes the result. Pinned by `tests/test_scoped_replan.py`. |
| `_repair_prescribed_commands(plans, prescribed)` | Mechanical plan repair for the adherence floor (DESIGN §CRITIC *Repairing an omitted self-report beats re-driving for it*). Synthesises one subtask carrying every prescribed command, `depends_on` = the plan's current sinks, returns its id — or `None` when the floor is clean, there are no commands, or no plan supplies a valid id prefix. Mutates `plans` in place; never raises; declines rather than guessing (mirrors `_repair_missing_requires`). Called before `check_prescribed_command_coverage` so a repairable gap never reaches the ~125-spawn re-plan path. Deliberately doesn't attach to an existing subtask (a verification-shaped matcher hits 32/36 subtasks on the real incident plan). Pinned by `tests/test_prescribed_command_repair.py`. |
| `check_replan_affordable(st, caps, gate, plans)` | Budget preflight before a re-plan (DESIGN §13) — a re-plan re-runs the whole P1 decomposition and was previously authorised unchecked. Estimates `n_domains × planner_samples + n_subtasks × replan_decompose_estimate` from the **plans the caller passes in** (not `plan_snapshot`, written only after `_schedule()`). `die()`s with `EXIT_BUDGET_INFEASIBLE` when it exceeds what's left. Called at the top of the re-planning `_on_feedback` callback in `phase_adherence_gate`, and in `phase_overlap_judge` before `phase_plan`. Honours `skip_budget_check`. Pinned by `tests/test_replan_budget_preflight.py`. |
| `_run_checked_loop(invoke, check, name, max_rounds, make_feedback_prompt)` | Generic loop: call → check → feedback → retry. Returns `(result, warnings)`. Re-invokes on **gating** findings only (see below). Oscillation guard aborts only when a round's issue-signature set is EXACTLY EQUAL to an earlier round's — a proper subset (genuine partial progress) keeps retrying (DESIGN §8). |
| `_partition_issues_by_severity(issues)` | Splits into `(gating, advisory)`, order preserved. Used by `_run_checked_loop` and `_select_best_planner_sample`. |
| `_issue_is_advisory(issue)` | True when the `LABEL` prefix is in `_ADVISORY_ISSUE_LABELS`. Unknown labels/non-strings are gating. |
| `_confidence_axes_clear(conf, axes, threshold)` | Pure predicate: True when every named axis is a number ≥ threshold. Used by the loop and `_settle_subtask`'s implementer confidence check. |
| `_format_check_feedback(issues, rnd, max_rounds)` | Formats issues into the structured feedback block injected on re-invocation. |
| `_confidence_schema(axes)` | DRY helper building the §8 confidence sub-schema. Used by 10 worker schemas (`classifier`, `planner`, `reconciler`, `implementer`, `integrator`, `rebaser`, `conformer`, `provision`, `plan_overlap_judge`, `fit_judge` — not `splitter`, whose output carries no confidence axis). Shape: `required: [*axes, "basis"]`; `falsifiers_tested`/`contradictions_reconciled` optional; no `gap_to_close` field, no `maxLength` caps (both removed as a decoder-corruption mitigation, `anthropics/claude-code#49747`; DESIGN §8). `confidence` is **not** in any of these schemas' top-level `required` — a worker omitting it still validates. Pinned by `tests/test_confidence_not_required.py`; `tests/test_confidence_length_caps.py` covers callers that do emit it. |
| `_subtask_item_schema(*, include_requires, include_migration_targets, include_runs_commands, include_fixes_reported_symptom)` | DRY helper (same pattern as `_confidence_schema`) building the child-subtask item schema shared by `SCHEMAS["planner"]["subtasks"]`, `SCHEMAS["reconciler"]["added_subtasks"]`, `SCHEMAS["splitter"]["children"]` — previously three independently-written literals. Each call site passes its own `include_*` set (`reconciler.added_subtasks` narrowest — no `requires`/`migration_targets`/`runs_commands`/`fixes_reported_symptom`; `splitter.children` — `requires` only; `planner.subtasks` — all four) rather than converging on one shape, so a future field change is explicit about which sites it reaches. Pinned by `tests/test_shared_subtask_item_schema.py`, including an anti-vacuity check that narrower sites reject the wider ones' fields. |

### Finding severity — gating vs advisory

`_run_checked_loop` partitions each round's findings via
`_partition_issues_by_severity(issues) -> (gating, advisory)` and re-invokes
**only on gating findings**. Advisory findings go to `warnings` (never
hidden) and are logged once, but never consume a round, enter
`_format_check_feedback`, or enter the oscillation guard's signature set.
`_select_best_planner_sample` likewise ranks on the gating subset only.

`_issue_is_advisory(issue)` keys on the mechanical `LABEL` prefix (the same
one `_issue_signature` parses, generated by leerie's own check functions,
not LLM prose — not the natural-language parsing CLAUDE.md forbids). A
`LABEL (subtype):` parenthetical is stripped before lookup.

`_ADVISORY_ISSUE_LABELS` is a **frozenset allowlist**, so the default is
**gating**: an unclassified finding cannot silently disarm a real gate.

| Advisory label | Why it is advice, not a defect |
|---|---|
| `INTRA_DOMAIN_OVERLAP` | Its own text is "consider merging or splitting" — two subtasks touching one file is frequently legitimate (measured 43 → 12 → 6 across both 2026-08-03 runs, never zero). |
| `PHANTOM_PATH` | Fires when no ancestor dir exists for a planned path — exactly what a subtask that *creates* a new module looks like; the dominant driver of the issue-count/plan-size coupling. |
| `OVERSIZED` | Grades a planner self-report, but `fit_judge` in `_recursive_decompose` is the authoritative decomposition gate (DESIGN §5½, §8). |
| `MANY_CATEGORIES` | "typical tasks span 1–3" is a heuristic, not a correctness property. |
| `SAME_WORK_RISK`, `TEST_OWNERSHIP_RISK` | Both end by telling the classifier to apply judgement and keep both categories if deliverables genuinely differ — a remedy that may be "change nothing" cannot gate. |

Everything else — `DANGLING_DEP`, `INTRA_DOMAIN_CYCLE`, `EMPTY_CRITERIA`,
`PROTECTED_PATH`, `MIGRATION_TARGETS_MISSING`, `UNCOVERED_MIGRATION_SURFACE`,
`PRESCRIBED_CMD_UNRUN`, `REQUIRED_ITEM_UNCOVERED`, and every other worker's
codes — remains gating.

Pinned by `tests/test_issue_severity.py`; its most important test is
`test_unknown_labels_default_to_gating` — if that default ever inverts,
every future check silently stops gating until reclassified.

### Per-worker mechanical checks

Each returns `list[str]` — empty when clean. Pure Python, no LLM. Severity
is resolved from the issue code per the table above, not the function.

| Worker | Check function | Issue codes | Max rounds cap |
|--------|---------------|-------------|----------------|
| Classifier | `check_classifier_output(result, repo_root, judge_confirmed=frozenset())` | `CATEGORY_NO_DIR`, `EMPTY_WHY`, `EMPTY_EVIDENCE`, `MANY_CATEGORIES`, `SAME_WORK_RISK`, `TEST_OWNERSHIP_RISK` | `judgment_check_rounds` (3) |
| Planner | `check_planner_output(result, repo_root, domain)` | `PHANTOM_PATH`, `DANGLING_DEP`, `EMPTY_CRITERIA`, `OVERSIZED`, `INTRA_DOMAIN_OVERLAP`, `PROTECTED_PATH`, `INTRA_DOMAIN_CYCLE`, `UNCOVERED_MIGRATION_SURFACE`, `MIGRATION_TARGETS_MISSING` | `planner_check_rounds` (3) |
| Reconciler | `check_reconciler_output(output, plans)` | `RENAME_TO_NOWHERE`, `BAD_PREFIX`, `SELF_DEP` | `judgment_check_rounds` (3) |
| Overlap judge | `check_overlap_judge_output(output, plans, repo_root)` | `PHANTOM_ARTIFACT`, `NO_FILE_OVERLAP`, `DROP_BREAKS_GRAPH`, `DUPLICATE_PAIR` | `judgment_check_rounds` (3) |
| Adherence gate | `check_prescribed_command_coverage(prescribed_procedure, subtasks)` (deterministic floor) + inline `LOW_ADHERENCE` check on the `adherence_judge` result | `PRESCRIBED_CMD_UNRUN`, `LOW_ADHERENCE` | `judgment_check_rounds` (3) |
| Provision | `check_provision_output(result, repo_root)` | `WRONG_PM`, `MISSING_WORKDIR`, `EMPTY_RECIPE` | `judgment_check_rounds` (3) |
| Implementer | `check_implementer_output(result, subtask, actual_files)` | `NO_PLANNED_FILES_TOUCHED` (advisory — excluded from retry by `_gating_issues`), `UNMET_CRITERION`, plus `check_production_evidence`'s four (below) | `implementer_confidence_retries` (2) |
| Integrator | `check_integrator_output(result)` | — | `judgment_check_rounds` (3) |
| Conformer | (`_conformance_clean` on observable signals) | — | `conformance_rounds` (3) |

**`UNMET_CRITERION` must not fire on a criterion the implementer was never
responsible for.** A criterion naming the build is a *conformance-phase*
signal — the conformer runs the build, and one inside a worker's turn
budget can OOM the container and get it reaped mid-turn. With only
`{criterion, met, evidence}` on the schema, an obedient implementer had no
way to say so and every such report became a re-drive.

`SCHEMAS["implementer"]`'s `criteria_results` items therefore carry an optional
`not_applicable` bool, and `check_implementer_output` skips any criterion where
it `is True` (identity, not truthiness) before testing `met`.

**One channel, deliberately.** A second briefly existed —
`_criterion_is_conformance_owned`, substring-matching the repo's resolved
build/lint/test commands inside the criterion text — as a backstop for a worker
that forgot the flag. It is deleted, along with `check_implementer_output`'s
`blt_commands` parameter. `criterion` is planner-authored prose, and the
*Language-to-JSON* rule forbids Python reading meaning out of prose; the
owning worker surfaces it as a JSON field (`not_applicable`) instead.


`check_production_evidence(result) -> list[str]` (DESIGN §9 *Evidence must be
production-grounded*) reads the `production_evidence` object that
`_production_evidence_schema()` puts on the **implementer** and **conformer**
schemas, and emits `NO_PRODUCTION_EVIDENCE` (field absent or not an object),
`UNSUPPORTED_PRODUCTION_EVIDENCE` (`exercised: true` with neither `how` nor
`observed`), `UNEXERCISED_PRODUCTION_PATH` (`exercised: false` with no
`unexercisable_reason`), or `MALFORMED_PRODUCTION_EVIDENCE` (`exercised` not a
bool). It has **three call sites with deliberately different severities**.
From `check_implementer_output`, on the `status == "complete"` branch only (a
blocked implementer has no finished path to exercise), it routes through the
existing bounded `implementer_confidence_retries` retry and so never blocks a
run permanently. From `_settle_subtask`'s conformance block and from
`_run_final_conformance` (the whole-tree pass, run after
`_validate_conformance_result`) it is **advisory** — the output extends
`conf_warnings`/`warnings`, never `blocked_reason` — because `solution_defects`
is deliberately the one gating conformer axis (DESIGN §9) and an advisory phase
must not acquire a second way to stop a run. The conformer's copy is read only
when `conf_res is not None`, since a crashed conformer has no result to inspect.

`check_symptom_evidence(result, sid, fixes_reported_symptom) -> list[str]`
(DESIGN §9 *A stale finding is not a bug*) is its sibling for subtasks whose
plan entry declares `fixes_reported_symptom: true`, reading a
`symptom_evidence` object of the same flat shape on the **implementer** schema
only — the conformer neither wrote the fix nor has a base tree to reproduce
against. It emits `NO_SYMPTOM_EVIDENCE`, `SYMPTOM_DID_NOT_REPRODUCE` (the
useful one — the finding may already be fixed) or `MALFORMED_SYMPTOM_EVIDENCE`.
**Advisory**: the output is logged, attached to the result as
`symptom_warnings`, and persisted to `symptom_findings` in `state.json`
(results are in-memory only); it is *never* routed into
`check_implementer_output`, since a retry cannot make a stale finding
un-stale. Scoped by the planner's **declaration**, never by the subtask's id
(a `bugfix-` id prefix is not evidence a symptom exists — ids are re-homed by
merges — and scoping on it produced false positives across the run corpus,
docs/POSTMORTEM-2026-08-14.md F18). Takes the **orchestrator's** `sid` rather
than the worker's echoed `result["subtask_id"]`. Pinned by
`tests/test_symptom_evidence.py`.

Two shape decisions are load-bearing. The field is **optional in the schema
and gating in the check**: requiring it costs the entire submission on a miss
(measured 40.9%-valid on `plan_overlap_judge` from exactly this mistake), and
gating on absence is what stops optional from meaning ignorable. The object
is **flat with one required inner field, a bare bool** — `how`/`observed`
strings stay optional, since many required params mixed with verbose strings
triggers anthropics/claude-code#49747's decoder corruption.
`tests/test_production_evidence.py` pins both.

`PHANTOM_ARTIFACT` resolves a collision's `artifact` against the union of
every subtask's `files_likely_touched` **as well as** the working tree, not
the tree alone — the judge's charter is subtasks that both *create or
substantially rewrite* an artifact, so a not-yet-existing file is the
canonical subject. Only a path present in neither the tree nor the plan is
flagged. Path comparison is normalized (`_normalize_artifact_path`: leading
`./` stripped, `os.sep` → `/`, no case folding); `NO_FILE_OVERLAP` uses the
same normalizer.

`artifact` is a **logical** label and Python never parses it — the judge
names the files separately in the `artifact_paths` array, and
`PHANTOM_ARTIFACT` does plain set membership on that (CLAUDE.md
*Language-to-JSON*). An empty `artifact_paths` means the artifact has no
file and nothing is flagged; `minLength: 1` on the items makes a blank
entry a schema-gate retry rather than something the check filters at read
time. `artifact_paths` is **asked for but NOT in `collisions[].required`**
(changed 2026-08-03): requiring it drove `plan_overlap_judge`'s valid-output
rate to 40.9% against 99.6–100% for every other worker, almost all failures
the single error `'artifact_paths' is a required property` — a
whole-payload rejection of otherwise-sound collision analysis. Absence is
the designed-for case (`paths = c.get("artifact_paths") or []`; `if not
paths: continue`). See DESIGN §5 *Cross-domain surface overlap*.
`tests/test_phase_overlap_judge.py`'s `TestProsePathParsingAbsent` pins that
neither earlier hand-parsing shape (whole-string checking, whitespace
tokenizing) has returned.

`DUPLICATE_PAIR` covers two collisions naming the same `{a_sid, b_sid}`
pair. This is **coherent** when the pair genuinely overlaps on more than one
artifact (one row per artifact rather than one row listing every path), and
`_apply_overlap_collisions` absorbs the repeat via `skipped_redundant`.
What matters is the resolved **effect** (dropped sid for a `drop_*`, or the
sorted endpoint pair for a `merge`), not the `resolution` string:
identical-effect rows are coalesced by `_validate_overlap_judge_output`
into one collision (artifacts joined, `artifact_paths` unioned) and
applied; rows whose effects genuinely *differ* (e.g. the same pair emitted
twice as `drop_a` with swapped endpoints, dropping both subtasks) surface
as `DUPLICATE_PAIR` from `check_overlap_judge_output`, giving the judge a
retry round.

`LOW_CONFIDENCE` no longer exists. It was emitted by
`_confidence_issues(conf, axes, threshold=9.0)` from a worker's own
self-reported score; every gate that consumed it was replaced by an
independent adversarial verifier (DESIGN §8), and the helper has since been
deleted along with the axis.

The reconciler's size-gate and cycle-gate retry paths also run
`check_reconciler_output` after each retry's `_apply_reconciler_output`,
logging warnings for any structural issues.

### Task-referenced file extraction

When the task string references files (detected by
`_glob_task_references`), the orchestrator names the resolved paths for
the planner via `_format_task_file_references` — a list of paths, and
nothing else; the planner reads the files itself. Whether the plan
covers what they require is `task_coverage_judge`'s call (see
DESIGN.md §8 for the full rationale).

| Function | Purpose |
|----------|---------|
| `_expand_braces(pattern)` | Pre-expands `{a,b}` brace groups that Python's `glob.glob` does not handle. Recursive for nested braces. |
| `_glob_task_references(task, repo_root)` | Scans the task string for file-path tokens, expands braces, globs each pattern. Returns deduplicated `list[Path]`. Tokens are stripped of markdown emphasis (`*`, `_`, backticks) **before** classification and must still look like a path afterwards — some alphanumeric content plus an extension or a `/` — since a bare `*`/`**` (ordinary markdown emphasis) would otherwise glob to every file in the repo root. Absolute-path tokens are refused, and every glob result is independently re-checked for containment under `repo_root.resolve()` so `../` segments cannot escape either. The task file never appears in its own list (`_is_same_document`). |
| `_is_same_document(path, text_len, text)` | True when `path` holds exactly `text` modulo surrounding whitespace. Keeps a task file out of its own reference list, since the planner already has the task verbatim. A size pre-check (±8 bytes) means only a same-length candidate is ever opened. |
| `_repo_rel(path, repo_root)` | Repo-relative string for a path, falling back to its basename when it resolves outside the repo. Pure path arithmetic. |
| `_format_task_file_references(files, repo_root)` | Names the files the task references so the planner reads them itself. A list of paths and nothing else — it must not open them. `None` when the task names no files. |
| `_unreachable_task_references(task, repo_root)` | Advisory sibling of `_glob_task_references`. Scans the same de-emphasized, path-shaped tokens and flags three shapes invisible to the planner for different mechanical reasons: tokens starting with `/` (absolute, dropped as candidates), `~` (home-relative — pathlib never expands `~`), and `../` (parent-relative — resolved against the required `repo_root` parameter; flagged when it escapes the root or resolves to nothing). Trailing sentence punctuation is `rstrip`ped (never `strip` — a leading dot or `./`/`../` is meaningful). `phase_plan` logs a single warning line when non-empty; the check never gates. |

**Freeze guard (2026-07-19 incident, root cause A) — resolved by deletion.**
A single incidental dotted token (e.g. `CLAUDE.md` mentioned once in a
task's Verification section) used to make `extract_task_file_structure`
harvest the repo's real CLAUDE.md as spec items, including imperatives that
could never appear verbatim in a subtask — a literal-substring gate that
fired identically every round, burning ~35% of the run's spend on a signal
that could not move. The whole mechanism (`extract_task_file_structure`,
`_is_uncoverable_convention_item`, `_BACKTICK_SPAN_RE`,
`check_task_file_coverage`, `_dedup_frozen_coverage_issues`,
`_format_task_file_structure`, `_MAX_COVERAGE_ITEMS`, and the
`LOW_COVERAGE` issue kind) is deleted. `phase_plan` now names the
referenced files via `_format_task_file_references` and lets the planner
read them; coverage of what those files require belongs to
`task_coverage_judge` (phase 2⅞½). `tests/test_task_file_coverage_freeze.py`
and `TestProseHarvestAbsent` pin that none of the deleted symbols return.

No-op when the task doesn't reference files.

### Instruction-adherence gate (DESIGN §12 sibling — *Instruction adherence
is code-enforced*)

Fully shipped. `SCHEMAS["classifier"]` carries `prescribed_procedure`
(`{is_prescribed, commands, forbid_manual, evidence}`, persisted to
`st.data["prescribed_procedure"]` — see §3 "Worker output schemas"),
`SCHEMAS["planner"]` carries the optional per-subtask `runs_commands`
array, the `adherence_judge` worker is fully registered (schema, prompt,
`WORKER_TYPES`, sonnet/`"medium"` model-effort defaults — see "The
`adherence_judge` worker" below), and `phase_adherence_gate` (a
whole-plan gate, "Phase 2⅞", run once per assembled plan rather than
per-subtask) wires all of it together.

`phase_adherence_gate(plans, task, st, caps, models, efforts)` runs in
`_run_phases` immediately after `phase_overlap_judge` and before
`_schedule()`/`_validate_plan` (so a re-plan never rebuilds an
already-scheduled DAG). It short-circuits — free, no worker calls — when
`st.data["skip_adherence_check"]` is set, and again when
`st.data["prescribed_procedure"].is_prescribed` is falsy (the ~90%
goal-only common case never pays for a judge call). Two-stage
composition (corpus-validated: 0/21 false positives on real runs; do not
gate on the judge's score alone, which false-positived ~12% of ordinary
runs in isolation):

1. **Deterministic floor (primary, JSON→verdict, no NL):**
   `check_prescribed_command_coverage(prescribed_procedure, subtasks)`
   computes `set(prescribed_procedure.commands) − ⋃(subtask.runs_commands
   for subtask in plan)` using **normalized token-set matching**
   (lowercase + stopword-filtered token-SUBSET — the planner emits
   paraphrases of the prescribed command, not always the byte-identical
   string), not exact string equality. A non-empty result names a
   prescribed command no subtask runs — a `PRESCRIBED_CMD_UNRUN` issue.
   Silent (and free) when `prescribed_procedure` is absent, `is_prescribed`
   is false, or `commands` is empty.
2. **Adherence judge (secondary, semantic layer):** spawned only when
   `is_prescribed=true`. Scores `instruction_adherence` (0–10) +
   `violations[]` for the case the deterministic floor cannot see: every
   prescribed command runs, but the plan *also* substitutes hand-authored/
   manual work the user's `forbid_manual` signal prohibited. The gate fires
   when `instruction_adherence < _ADHERENCE_GATE_THRESHOLD` (5.0 — chosen
   from the corpus's clean separation: incident plans scored ≤3.0,
   legitimate plans ≥8.5).
3. **Gate wiring.** Either a `PRESCRIBED_CMD_UNRUN` floor issue or a low
   `instruction_adherence` score is fed to the **existing**
   `_run_checked_loop` (bounded by `judgment_check_rounds`), exactly like
   `phase_reconcile` and `phase_overlap_judge`. The retry's
   `make_feedback_prompt` callback **is** the re-plan action: it
   re-invokes `phase_plan` in full with the violation text folded into the
   task string, then re-invokes `phase_reconcile` on the re-planned output
   (short-circuiting to a no-op when the re-plan introduced no new
   unresolved requires), and the loop's next round re-runs the floor +
   judge against the new, reconciled plan. No new pause/resume machinery.
   Exhaustion `die()`s with the unresolved violations and the
   `--skip-adherence-check` escape hatch. `adherence_judge` `WorkerError`
   (every round crashes) degrades: the floor's own (model-independent)
   verdict is re-checked one final time — a clean floor returns the plan
   unmodified, a violating floor still `die()`s — mirroring `fit_judge`'s
   crash-barrier discipline (DESIGN §5½).

On success the gate persists `st.data["adherence_gate"] = {"judge":
<adherence_judge output>, "floor_issues": []}` for audit.

### P6 repo-map — `_build_repo_map` + `_rank_repo_map`

Implements DESIGN §5½ (P6) *Codebase structural map*. Both functions are
deterministic, lazy-import tree-sitter (so the module loads on a bare host
Python that lacks the package), and call no LLM.

| Symbol | Purpose |
|--------|---------|
| `_walk_calls(node)` | Walks a tree-sitter CST recursively, collecting bare-name identifiers from `call` expression function positions. Returns `list[str]`. Attribute callees (e.g. `obj.method`) are skipped — only bare-name callees become ref edges. |
| `_parse_repo_file(path)` | Parses one source file with `tree_sitter_language_pack.process()` (for defs/structure) and a tree-sitter CST walk (for call-site refs). Returns `(defs: list[str], refs: list[str])`. Returns `([], [])` on unsupported language or any error (graceful degrade). On an actual caught exception, stashes `f"{type(e).__name__}: {e}"` in the module-level `_last_parse_error` diagnostic (cleared to `None` on success). |
| `_build_repo_map(repo_root, leerie_root)` | Walks all source files under `repo_root` (skipping `.git`, `node_modules`, `__pycache__`, etc.), parses each with `_parse_repo_file`, and builds `{"files": {rel_path: [def_sym, ...]}, "refs": {def_sym: {rel_path, ...}}}`. mtime-caches per-file parse results under `<leerie_root>/<REPO_MAP_CACHE_DIR>/<sha256(abs_path)>.pkl` — only files whose `mtime_ns` changed since the last call are re-parsed. Always returns a valid dict; never raises. **Silent-degrade visibility (DESIGN §12):** if the repo contains source files but the graph comes back empty, `_warn_repo_map_empty_once()` runs a functional probe (`_tree_sitter_extraction_works()`) and emits exactly one warning per process **only if the probe confirms tree-sitter cannot extract symbols**. A genuinely non-code repo, or a working parser on a legitimately symbol-less repo, stays quiet. When the probe failed via a caught exception, the warning appends `" Probe failure: <type>: <message>"` from `_last_parse_error`. |
| `_pagerank(graph, personalization, damping, max_iter, tol)` | Personalized PageRank on a directed `dict[str, set[str]]` graph. Pure stdlib (no networkx). Handles dangling nodes via a dangling-mass redistribution term. Converges when sum of per-node rank deltas < `tol`. Returns `dict[str, float]`. |
| `_render_repo_map_subgraph(repo_map, ranked_files, max_files)` | Renders the top `max_files` files from `ranked_files` as a compact text block: one line per file listing its defined symbols (`path: Sym1, Sym2, ...`). Files with no defs are omitted. |
| `_count_tokens_approx(text)` | Approximate token count: `max(1, len(text.encode()) // 4)` — ~4 bytes per token. Used by `_rank_repo_map`'s binary-search budget fit. |
| `_rank_repo_map(repo_map, seed_files, seed_symbols, token_budget)` | Builds a file→file edge graph via shared symbols (definer → referencing files), runs personalized PageRank biased toward `seed_files` and files that define/reference `seed_symbols`, then binary-searches the largest prefix of the ranked-file list that fits within `token_budget` tokens (default `DEFAULT_CAPS["repo_map_tokens"]`). Returns the ranked subgraph as a plain text string. Returns `""` when the map is empty. |

**Edge direction:** `_build_repo_map` tracks `refs[sym] = {files that call sym}`. `_rank_repo_map` builds a file→file edge from the definer of `sym` to each file that references it — so widely-referenced utility files accumulate high in-degree and surface as structural backbone.

**Personalization in `_rank_repo_map`:** seed files get weight 1.0; files defining a seed symbol get 1.0; files *referencing* a seed symbol get 0.5. When no seed resolves to a known file, uniform personalization is used.

**Skip flag:** `resolve_skip_repo_map` (§2 "Skip flags") gates the call; when `True`, `_build_repo_map` is not called and the planner degrades to the prior grep/glob-only path.

### Phantom-path check

`PHANTOM_PATH` fires when a `files_likely_touched` entry does not exist
and no ancestor directory between the file and `repo_root` exists
either. This catches hallucinated paths (e.g.
`src/totally/invented/dir/file.ts` when `src/totally/` does not exist)
while tolerating greenfield features that create new subdirectories
under an existing parent.

### Migration-surface check

`check_planner_output` includes an `UNCOVERED_MIGRATION_SURFACE` check
(DESIGN §5 *Migration-surface completeness*). The planner **declares** the
migration in its own output — each subtask may carry
`migration_targets: [{old_pattern, replacement, is_real_identifier}]`
(`old_pattern` has `minLength: 3`; `is_real_identifier` is a required
boolean on each entry) — and the check greps `repo_root` for the
declared `old_pattern` string, collects files containing it,
cross-references against `files_likely_touched` across all subtasks in
the domain, and emits the issue when > 5 files are uncovered. The
threshold avoids false positives from comments, type definitions, and
test fixtures.

An earlier version inferred the old pattern from prose
(`_MIGRATION_SIGNAL_RE` matching phrases like "replaces direct `X`" against
`intent`/`investigation_notes`) — forbidden by CLAUDE.md *Language-to-JSON*,
and it did not work: measured on run `19a70d96`, all 27 extractions were
stopwords that grepped to hundreds of files and always cleared the
threshold. Python now greps a symbol the planner handed it directly.

Because `migration_targets` is optional, an omitted field (not a wrong
entry) silently disables `UNCOVERED_MIGRATION_SURFACE` for that subtask.
`_check_migration_targets_declared(subtasks)` closes the common case: the
schema's `performs_replacement: bool` sibling field is a same-worker
self-report of whether the subtask replaces anything at all, and the
check emits `MIGRATION_TARGETS_MISSING` when it's `true` but
`migration_targets` is empty — a same-call internal-consistency check, not
an independent verifier: a planner wrong on both fields together (false +
omitted) is not caught.

Whether `old_pattern` is shaped like a real identifier used to be enforced
by `_BARE_LOWERCASE_WORD_RE` (`^[a-z]+$`) — a regex Python ran against the
planner-populated field, itself a relocated *Language-to-JSON* violation.
It is retired: each `migration_targets` entry now carries a required
`is_real_identifier: bool` field the planner sets itself, and
`_check_migration_surface` trusts that attestation directly (skipping an
entry when it's `false` or absent) rather than re-deriving the judgment.

| Function/constant | Purpose |
|----------|---------|
| `_MIGRATION_SURFACE_THRESHOLD` | 5 — uncovered file count below which the check stays silent |
| `_grep_old_pattern(pattern, repo_root)` | `subprocess.run` grep for the pattern; returns set of file paths |
| `_check_migration_targets_declared(subtasks)` | `MIGRATION_TARGETS_MISSING` when `performs_replacement: true` and `migration_targets` is empty — same-worker contradiction check, not an independent witness. |

`check_planner_output` (and thus both migration checks) only ever runs on
the planner's raw first-pass sample, inside `plan_one`, before
`_recursive_decompose` expands an oversized subtask into leaf children
(`phase_plan`'s expansion loop reassigns `plan["subtasks"] = leaves` only
after that call returns) — so neither check ever sees a post-expansion
leaf. `_migration_child` (P1 recursive decomposition, DESIGN §5½) still
copies `migration_targets`/`performs_replacement` onto every leaf it
builds, the same way it copies `depends_on`/`requires`/`provides`.

### Multi-sample planning

When `planner_samples > 1`, `phase_plan` runs N independent
`plan_one(category, sample_idx)` calls per domain in parallel
(bounded by `max_parallel`). Each gets a unique `sid`
(`planner-{category}-s{idx}`) so log files don't collide.

`_select_best_planner_sample(samples, repo_root, domain)` mechanically
selects the winner: fewest `check_planner_output` issues, tiebreak on
subtask count (more = better coverage), tiebreak on first sample
(determinism). No LLM merge judge — avoids self-bias. A crashed sample
(worker returned `None`) is dropped from the candidate set before
selection. If all samples for a domain crash, the run aborts.

**Validity gate (runs before scoring).**
`_planner_sample_is_empty_ready(sample, sibling_subtask_counts=None)`
returns True for a `status == "ready"` plan that is exactly empty, or
"near-degenerate" — non-empty but with substantially fewer subtasks than
the largest sibling (`_PLANNER_SAMPLE_DEGENERACY_RATIO`, 4x) — when
`sibling_subtask_counts` is given. `_select_best_planner_sample` drops
those samples **before** ranking — but only while at least one surviving
sibling is not itself dropped; if every sample is empty/near-degenerate
relative to the rest the full set is ranked unchanged, so
`_detect_no_work`'s terminal route still fires.

The gate precedes the scoring rather than joining it as another sort key,
because `check_planner_output` inspects subtasks, so a plan with none to
inspect returns `[]` and scores a perfect zero on the **primary**
criterion — an empty plan is otherwise unfalsifiable and beats every
sibling with real content to critique. Scoped to `ready` only: an empty
`blocked` plan is a planner verdict `_schedule()` must still act on.

Pinned by `tests/test_planner_sample_validity_gate.py`, including an
anti-vacuity control (`test_falsifier_empty_sample_would_win_without_the_gate`)
and `test_selection_is_biased_toward_smaller_plans_when_issues_scale`,
which records the residual bias the gate does **not** fix: per-subtask
findings make issue count grow with plan size, so a larger plan can still
lose to a smaller one.

The selection log line lists **every ranked sample**, not just the
winner (`… — ranked: #0(2i/2s), #2(3i/3s)`), using each sample's index in
the ORIGINAL `samples` list so the number cross-references the
`planner-{category}-s{idx}` worker sid.

### Cap resolvers

Same resolution pattern as existing resolvers (CLI → env → TOML →
default): `resolve_judgment_check_rounds`,
`resolve_planner_check_rounds`,
`resolve_implementer_confidence_retries`, `resolve_planner_samples`,
`resolve_token_probe_cache_sec`.
Env vars: `LEERIE_JUDGMENT_CHECK_ROUNDS`,
`LEERIE_PLANNER_CHECK_ROUNDS`,
`LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES`, `LEERIE_PLANNER_SAMPLES`,
`LEERIE_TOKEN_PROBE_CACHE_SEC`.

---

## 6. Caps and their values

Defaults in `DEFAULT_CAPS` and the per-worker `claude_p` call sites.

### Code-enforced caps (the orchestrator counts these)
| Loop | Cap | On cap |
|------|-----|--------|
| subtask continuations (re-spawns of an implementer for the same subtask — both context-exhaustion handoffs *and* mid-execution clarifications consume from the same budget) | 3 (`subtask_continuations`) | return `blocked`; fatal at wave boundary |
| corrective retries of a *retryable* failure per subtask (`failed_retries`) | 1 | return `failed` |
| orchestrator-level conformer rounds per subtask (`conformance_rounds`) | 3 | exit the conformance loop; any residuals become `conformance_warnings` on the subtask result — never `failed` / `blocked` (DESIGN §9 *Post-work conformance*). Backgrounding-retry (Pattern B) warnings from round N are injected as structured CRITIC-pattern feedback into round N+1. |
| concurrent orchestrator-run build/lint/test measurements (`blt_parallel`) | 2 | not an escalation — a gate. Distinct from `max_parallel`, which bounds *workers*: a worker is admitted by `_await_worker_memory_admission` and enrolled in a cgroup, while a BLT command `_run_streaming` starts carries no `memory.max`/`pids.max` of its own (DESIGN §6 *Orchestrator-run build/lint/test is bounded, not contained*). Held around the command only — never the memo lookup (a hit must stay free) nor the install. Sized lazily on the running loop by `_blt_semaphore`, since a module-level `asyncio.Semaphore()` binds to whatever loop is current at import. 2 is a floor, not a measured optimum; it matters most under `--subtask-tests full`. |
| implementer completeness re-drives per subtask (`completeness_retry_rounds`) | 1 | the conformer's gating `solution_defects` axis found concrete behavioral gaps in the implementer's diff (DESIGN §9 *The one gating axis: solution completeness*). Each round folds the found defects into the implementer's next attempt as mandatory criteria and re-drives it (a **separate** counter from `implementer_confidence_retries`/`failed_retries`/`subtask_continuations`). On exhaustion the subtask returns `blocked` with the residual defects named (fix + `resume`) — never silently advisory. Independent of `--strict-conformer`, which governs only the advisory build/lint/test/residual axes. |
| total worker invocations per run | 2000 (`--max-workers`, also `LEERIE_MAX_WORKERS` env or `max_workers` in `leerie.toml`) | the cheap, runtime backstop in `State.bump_workers()`: raises `WorkerError`, abort, state saved for `resume`. The complementary early check is `check_budget_feasibility()` at the plan/execute boundary (after `_schedule()`, before `_write_plan()`) — it estimates remaining `claude -p` calls from the planner output and `die()`s with `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` value before any implementer spawns. See DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*. |
| per-subtask call-estimate (for the feasibility preflight) | 3.0 (`subtask_call_estimate`) | not a runtime gate; consumed by `check_budget_feasibility()` as the per-subtask multiplier in its remaining-call estimate. Raised from 2.5 to absorb the per-subtask conformer completeness gate (DESIGN §9), whose `solution_defects` finding re-drives the implementer up to `completeness_retry_rounds` times. |
| per-subtask re-plan estimate | 1.5 (`replan_decompose_estimate`) | not a runtime gate; consumed by `check_replan_affordable()` as the per-subtask multiplier when projecting a re-plan's cost. A re-plan re-runs the whole P1 decomposition, not just the planners — the larger half of the cost. Rounded up so the preflight errs toward refusing a marginal re-plan. |
| budget-preflight safety margin | 1.15 (`budget_safety_margin`) | not a runtime gate; consumed by `check_budget_feasibility()` as the multiplier on `total_estimate` before comparison to `max_total_workers`. |
| concurrent workers within a wave | 5 (`--max-parallel`, also `LEERIE_MAX_PARALLEL` env or `max_parallel` in `leerie.toml`) | throughput throttle. Per-worker cgroup memory containment (see row below) keeps an OOM inside one worker's cgroup, so the wave-level parallelism can be high without risking cascade to sshd / lima-guestagent. |
| turns per `claude -p` call | per worker (below) | worker stops; implementer → `incomplete-handoff` |
| per-worker wall-clock (`worker_timeout_sec`) | 5400 s (90 min) global cap, **lowered per worker type by `TIMEOUT_DEFAULT_PER_WORKER` via `resolve_worker_timeout(worker, caps)`** | worker killed; implementer → `incomplete-handoff`. **Two tiers.** With no explicit global (`resolve_worker_timeout_explicit()` re-walks CLI/env/TOML, not a comparison to the default), `resolve_worker_timeout(worker, caps)` applies the table, bounded by the global; a worker absent from it keeps the full 5400 s. With an **explicit** global (`--worker-timeout SEC` / `LEERIE_WORKER_TIMEOUT` / `worker_timeout_sec`), that value wins outright and the table is **bypassed** — tracked as its own cap, `caps["worker_timeout_explicit"]`, mirroring `resolve_worker_memory_max`. The three timeout log/handoff messages (`_run_implementer`, `_run_conformer`, `_run_final_conformance`) report `resolve_worker_timeout(...)`, not the global. **Values are derived, never chosen:** each is `min(cap, max(_WORKER_TIMEOUT_FLOOR_SEC=600, ceil(p99*3), ceil(max*1.2)))` computed from `tests/fixtures/worker_duration/summary.json` (15,951 real calls across 21 worker types, regenerated by `scripts/measure/worker_durations.py <state-root>`; re-executed by `tests/test_worker_duration_distribution.py`). The `max*1.2` term is load-bearing: `planner`'s p99*3 is 5,091 s while its observed maximum is 5,247.6 s, so a p99-only rule would kill a run inside its own derivation corpus — the guard pushes planner to the cap instead. A fired timeout retries once (`_TIMEOUT_RETRY_MAX = 1`), unlike a `WorkerError`, which keeps the full round budget. |
| per-worker idle-event warning (`worker_idle_warn_sec`) | 300 s (5 min) | log a `no stdout events in <gap>s` warning naming the worker, its PID, and any stderr tail. Observation-only — the worker is NOT killed. |
| per-worker cgroup memory cap (`worker_memory_max_bytes`) | auto-derived via `_auto_worker_memory_max` → `_worker_memory_ceiling(slice_max)` from the shared `leerie.slice/memory.max` budget alone (broker `slice` verb; `_cgroup_slice_info`): `max(_WORKER_BUILD_PEAK_BYTES, min(_WORKER_BUILD_PEAK_BYTES * _WORKER_MEMORY_CEILING_MULTIPLIER, slice_max // 2))` — a **fixed isolation ceiling**, deliberately **independent of the live sibling count and of `max_parallel`**, since `memory.max` is a ceiling, not a reservation (DESIGN §6). Falls back to the legacy `/proc/meminfo`-derived basis (`_auto_worker_memory_max_legacy`, VM RAM split across `max_parallel + 1` slots, floored at 8 GiB) only when no broker/slice budget is readable. Contention is handled by admission in **two stages**, never by shrinking caps. Stage 1, `_degrade_max_parallel_for_wave(max_parallel, build_peak_bytes=None)`, runs once at wave entry and is synchronous: it returns the largest N in `[1, max_parallel]` with `slice_max - unreclaimable >= demand * N` and sizes the wave's `asyncio.Semaphore` accordingly. Stage 2 is the per-spawn gate: before spawning, `_await_worker_memory_admission` blocks (polling every 5s, up to 10 min) while measured slice headroom (`slice_max - unreclaimable`, never `memory.current`) is below `demand * (1 + in-flight workers)`. Reservations are bounded by worker LIFETIME — the gate returns a token from `_active_admissions` and `_invoke_admitted` releases it in a `finally`. Pinned by `tests/test_memory_admission_degrade.py`. Overridable via `--worker-memory-max SIZE` / `LEERIE_WORKER_MEMORY_MAX` / `worker_memory_max` in `leerie.toml` (bypasses the derivation only — the admission gate still runs). Suffixes K/M/G/T accepted. **Reconciled against the repo's own declared Node heap** (`resolve_worker_memory_max`, `_declared_node_heap_bytes`): an explicit `--max-old-space-size` in a `package.json` script overrides Node's host-derived default regardless of container size, so `_declared_node_heap_bytes` follows the package-manager indirection through `scripts`, matching all four V8 spellings via `_pm_script_candidates` (splits on shell separators first — a whitespace split alone misses `"build&&node"`). Deliberately over-inclusive: a missed script under-sizes the cage and OOMs the worker; an extra candidate costs nothing. A declared heap overrides whatever `NODE_OPTIONS` leerie injects for that subprocess (P9), sharing the headroom constant `_NODE_HEAP_HEADROOM_BYTES` = 2432 MiB with P9's own injection so both read one name, not a duplicated literal (`tests/test_resolve_worker_memory_max.py` AST-pins it; `test_node_heap_headroom_is_2432_mib` pins the value). When the resolved cap undershoots `declared heap + headroom`: an auto-derived cap is raised unclamped; an explicit override is refused with an actionable `die()`; when even the whole slice can't fit the declared heap, `die()`s naming the shortfall. Regression: `tests/test_worker_heap_ceiling_reconcile.py`, `tests/test_worker_memory_heap_reconcile.py` | the kernel OOM-kills inside the worker's cgroup; sibling workers, the orchestrator, and host-side services are not eligible victims. Enforcement goes through the **cgroup broker** (`scripts/cgroup-broker.py`), driven over a Unix socket by the dropped-privilege orchestrator. It creates `<V2_ROOT>/leerie.slice/leerie-w-<sid>` (cgroup **v2** — `V2_ROOT` is `/sys/fs/cgroup` rootful/Fly, or the systemd-delegated user slice under rootless containerd via `LEERIE_CGROUP_V2_ROOT`) or the split `pids/`+`memory/` hierarchies at the fixed `V1_ROOT` (v1/hybrid, never rootless) and sets its `memory.max`. Local nerdctl needs the launcher's cgroup bind-mount (`bind-propagation=rshared` rootful, plain bind rootless) + `--cgroupns=host`; Fly's microVM exposes cgroupfs directly. `_cgroup_probe` asks the broker to round-trip a create+enroll+destroy, and `_enforce_and_record_cgroup_containment` `die()`s before the first worker if it fails (unless `--dangerously-allow-uncapped`). See DESIGN §6 *Memory containment*. |
| per-worker memory demand estimate (`worker_demand_estimate_bytes`) | resolved once at run start by `resolve_worker_demand_estimate()`: `_WORKER_BUILD_PEAK_BYTES` unless the repo declares a Node heap, else `declared_heap + _NODE_HEAP_HEADROOM_BYTES`. Threaded to both admission surfaces as a parameter, not module state. **Distinct from the ceiling above** — that bounds one worker, this predicts what one will use. | `_degrade_max_parallel_for_wave` shrinks the wave; `_await_worker_memory_admission` blocks the spawn. Both fall back to `_WORKER_BUILD_PEAK_BYTES` when the key is absent, so the three entrypoints that build their own caps (`run_recapture_deps`, `run_rebaser`, `_replay_capture`) are unchanged. |
| per-worker cgroup PIDs cap (`worker_pids_max`) | 2048, or `--worker-pids-max N` / `LEERIE_WORKER_PIDS_MAX` / `worker_pids_max` in `leerie.toml` (positive integer; `resolve_worker_pids_max` `die()`s on bad input) | kernel rejects further `fork()` from any process in the worker cgroup once the count is reached. Sized against measurement (DESIGN §6 *Detecting PID exhaustion*): leerie's own suite peaks at 33 concurrent PIDs, so 2048 sits well above the workload and a worker near it is leaking rather than testing. Raise it per-repo for suites heavier than the default. |
| aggregate container memory cap (`leerie.slice/memory.max`) | auto-derived in `scripts/container-entry.sh` (PID 1) from VM `MemTotal` in `/proc/meminfo`: `MemTotal - max(1 GiB, 12.5%)`, reserving headroom for PID 1 + VM daemons (sshd, lima-guestagent, containerd). Overridable via `LEERIE_CONTAINER_MEMORY_MAX_BYTES` (raw bytes); `0`/`max` opts out. No CLI flag / `leerie.toml` key / `DEFAULT_CAPS` entry — the cap is applied by the shell entrypoint before the Python orchestrator starts. Best-effort: any read/write failure leaves the slice uncapped. Sets `memory.max` (RAM) only, not `memory.swap.max`. | when the slice's aggregate RSS exceeds the cap the kernel triggers a *cgroup-scoped* OOM (`CONSTRAINT_MEMCG`) that kills a process *inside the container*, instead of a VM-wide *global* OOM that would kill unprotected host-session processes and orphan the container. See DESIGN §6 *container boundary's hidden precondition*. |
| auth/quota backoff budget (`auth_retry_max_sec`) | 300 s (5 min) | `claude_p()` retries the worker with `tenacity` exponential backoff (initial 15 s, max 120 s, ±5 s jitter) on 401/429/529/auth-message envelopes. Budget exhausted → `WorkerError` naming the subscription cap (401/429/auth-text) or the transient overload (529). See §3 *Auth/quota backoff*. Terminal auth failures (`_is_terminal_auth_failure`, below) never reach this loop. |
| credential near-expiry warning threshold (`credential_expiry_warn_sec`, proposed 90 min) | 5400 s (90 min) | Launcher-side preflight (`_check_claude_credential_ttl`, staging block) run only when the resolved credential is a *subscription* token — the long-lived `$CLAUDE_CODE_OAUTH_TOKEN` has no `expiresAt` and is exempt. Parses `claudeAiOauth.expiresAt` (ms epoch) and compares to now. Already expired → refuse to launch, print `claude /login`. Inside the threshold → warn with the exact expiry and point at `claude setup-token`, but still launch. `expiresAt` absent or malformed → proceed silently. Never hard-code a TTL duration; `expiresAt` is the sole source of truth. |
| multi-token probe cache floor (`token_probe_cache_sec`) | 180 s | `resolve_token_probe_cache_sec` (CLI → `LEERIE_TOKEN_PROBE_CACHE_SEC` env → `token_probe_cache_sec` in `leerie.toml` → default), same `_resolve_positive_int_pref` pattern as `resolve_confidence_rounds`. Minimum interval between re-probing a given token's usage/runway (§3 *Multi-token rotation*). |
| mechanical-feedback rounds for judgment workers (`judgment_check_rounds`) | 3 | classifier, reconciler, provision, overlap judge, integrator, adherence gate, and the five independent adversarial verifiers (`classification_judge`, `wiring_judge`, `provision_judge`, `integration_judge` — DESIGN §8; `task_coverage_judge` is NOT bound by this budget: it is invoked directly, exactly once, and never retried). **Feedback-driven** callers (those passing `make_feedback_prompt`: classifier, reconciler, provision, overlap judge, integrator, adherence gate, `classification_judge`) run deterministic checks or an independent judge on the output and re-invoke with structured feedback if issues are found, cutting a round short of exhaustion when a round's issue signatures repeat an earlier round's. On exhaustion, proceed with best result + warnings (or `die()` for the adversarial-verifier gates among them) — except the classification gate, which routes to the cleared-but-empty terminal state instead of `die()`ing when the OR-accumulated `likely_already_satisfied` signal is `True` with evidence (DESIGN §8). **Detect-and-die, single-pass** callers (`wiring_judge`, `provision_judge`, `integration_judge`) stop at the first round that finds issues rather than retrying an unchanged payload. Both families: CRITIC pattern (ICLR 2024). A round that raises `WorkerError` is retried against the same budget regardless of family; any other exception abandons the loop immediately. |
| mechanical-feedback rounds for planner (`planner_check_rounds`) | 3 | Same CRITIC pattern, but higher default because the planner has richer checks (phantom paths, dangling deps, intra-domain cycles, protected paths, migration surface). |
| implementer confidence retries (`implementer_confidence_retries`) | 2 | Separate from `subtask_continuations`. Orchestrator checks confidence scores + scope drift + unmet criteria on complete results and re-invokes as a continuation if issues found. |
| planner samples (`planner_samples`) | 3 | Independent parallel invocations per domain. Mechanical selection: fewest issues, tiebreak on subtask count. Set to 1 to disable. Also `LEERIE_PLANNER_SAMPLES` env or `planner_samples` in `leerie.toml`. CLI: `--planner-samples`. |
| P6 repo-map token budget (`repo_map_tokens`) | 1000 | Token budget for the personalized-PageRank-ranked subgraph injected into the planner/splitter (DESIGN §5½ (P6)). The subgraph is binary-searched to fit within this many tokens. Not user-tunable via CLI / env / toml. |
| P1 recursive decompose max depth (`decompose_max_depth`) | 5 | Maximum recursion depth for `_recursive_decompose()` (DESIGN §5½ (P1)). Recursion terminates at depth ≥ 5 even if `fit_judge` still scores below `decompose_fit_threshold`. A depth-5 tree can represent up to 32 leaves from one subtask. Not user-tunable. |
| P1 fit-judge pass threshold (`decompose_fit_threshold`) | 0.70 | `fit_judge` confidence score at or above which a subtask is accepted as a leaf. Measured on n=24 telemetry-labeled subtasks: oversized mean 0.26 vs well-fit mean 0.84, 88% accuracy at 0.70. Not user-tunable. |
| P1 no-progress guard (`decompose_noprogress_rounds`) | 2 | Consecutive recursion rounds that produce no child with a fit score above the parent's before the subtask is accepted as a leaf with a warning. Prevents a degenerate splitter from looping to `decompose_max_depth`. Not user-tunable. |
| P1 sub-file split span (`subfile_split_max_span`) | 700 | Line-span above which a single-file subtask (tier 1) or a single region (tier 2) is split intra-file rather than left a leaf (DESIGN §5½ (P1) *Sub-file*). Heuristic, not telemetry-calibrated. Not user-tunable. |

### P1 recursive decomposition surface (DESIGN §5½ (P1))

`_partition_files(files: list[str], chunk_size: int) -> list[list[str]]`
Deterministic chunker for the migration-sweep path. Splits `files` into
non-overlapping chunks of at most `chunk_size` (default 8). 100% coverage
and 0 overlap are guaranteed by construction (no LLM). When `chunk_size < 1`,
returns `[list(files)]` (degenerate guard). Used by `_recursive_decompose()`
when `len(files) > 8` so the code — not the LLM — decides the file partition
(the LLM splitter was measured dropping 14/29 migration files in testing);
the splitter worker then only *labels* the pre-computed chunks.

`_remap_vanished_deps(subtasks: list[dict], mapping: dict[str, list[str]]) -> None`
Mutates `subtasks` in place: rewrites every `depends_on` reference to an id that
vanished from the plan, per DESIGN §5 *Id-vanishing operations*. `mapping` is
`{vanished_id: [successor_ids]}`; fan-out (expansion — parent → N leaves) and prune
(drop — id → `[]`) are the same operation over it. Dedups after the rewrite and skips
self-references, mirroring `_apply_overlap_merge`'s discipline. An empty `mapping` is
a no-op; a dep absent from `mapping` passes through untouched. Called from four sites:
`_recursive_decompose()` (intra-generation sibling edges), `phase_plan()` (cross-subtask
edges after expansion), and both phase-3 soft-drop filters (`_filter_offtree_subtasks`,
`_filter_satisfied_subtasks`) with an all-empty mapping to prune dropped ids. This
handles only the **id (`depends_on`) channel**; a *drop* also orphans the tag channel
(see `_prune_orphaned_requires` below).

`_prune_orphaned_requires(plans: list[dict], dropped_provides: set[str]) -> None`
Mutates the subtasks in `plans` in place: removes from each survivor's `requires` any
tag whose only provider was a dropped subtask, per DESIGN §5 *Id-vanishing operations*.
The prune set is `dropped_provides - (union of surviving provides)`: a tag still
provided by a surviving subtask is kept, and a tag no subtask ever provided is left
intact so `_validate_plan` still surfaces it as a genuine planner error. `dropped_provides`
must be captured before the survivor-filter removes the dropped subtasks from
`plan["subtasks"]`. Takes **all plans and operates once over the whole merged set**,
not per-plan, since capability tags are cross-domain and `_validate_plan` checks
provider-existence globally. Called by both phase-3 soft-drop filters once, after the
per-plan `_remap_vanished_deps` loop.

`_recursive_decompose(subtask, depth, st, caps, models, efforts, repo_root, *, repo_map=None, _parent_score, _noprogress_count) -> list[dict]`
Async recursive function implementing DESIGN §5½ (P1) *Task-Context Fit*. For each
subtask: calls `fit_judge` to score Task-Context Fit (0–1); returns `[subtask]`
if score ≥ 0.70 (threshold from `caps["decompose_fit_threshold"]`) or depth ≥
`caps["decompose_max_depth"]` (5); checks the no-progress guard
(`caps["decompose_noprogress_rounds"]` consecutive rounds of no improvement
accept the subtask as leaf); then splits via one of:

- **Migration path** (≥ 9 files): `_partition_files()` owns the file→chunk
  partition (deterministic, 100% coverage). The `splitter` worker is then
  invoked in **label-only mode** (`_label_migration_chunks()`) to write a
  distinct `title` + `success_criteria_seed` per pre-computed chunk — it must
  not move files. On splitter failure or a mismatched label set, every chunk
  falls back to a distinct deterministic label (`_deterministic_chunk_label()`).
- **Coupled path** (≤ 8 files): `splitter` LLM worker — structural seam detection.
- **Sub-file path** (exactly 1 file, low fit, file/region span >
  `subfile_split_max_span`): checked **before** the file-count fork, since a
  single dense file falls into the coupled path today where the LLM splitter
  cannot break one file. Splits the file *intra*-file in two tiers (both
  deterministic): tier 1 tiles `[1, EOF]` on tree-sitter function-boundary
  spans (`_extract_symbol_ranges()` → `_partition_symbols_by_line()`); a
  tier-1 region that is itself still over the cap re-enters recursion and
  gets tier-2 contiguous line-windows (`_partition_lines()`), which also
  serves as the whole-file fallback when no ranges are available. Children
  are built by `_subfile_child()` (analog of `_migration_child()`), each
  listing the same single file plus an `owned_region` field, a
  `_cofile_cluster` marker, and a **region-scoped `intent`** — mechanically
  derived from the parent's intent plus the region's line range and symbols,
  not a byte-identical copy. `_check_intra_file_surface()` is the
  zero-tolerance coverage/overlap backstop (union == `[1, EOF]`,
  pairwise-disjoint). Same-file co-ownership is legitimate downstream
  (schedule ignores `files_likely_touched`; `git merge` reconciles);
  `check_planner_output`'s `INTRA_DOMAIN_OVERLAP` advisory is suppressed for
  `_cofile_cluster` children. The overlap judge excludes same-file/
  different-region collisions via two mechanisms: the region-scoped
  `intent` gives it a textual signal, and `check_overlap_judge_output`'s
  `SPURIOUS_COFILE_COLLISION` check is a code-enforced backstop that flags
  any `unresolvable` verdict between same-`_cofile_cluster` subtasks
  regardless of the judge's own reasoning.
- **Oversized-file peel** (`1 < len(files) ≤ chunk_size`, exactly one file
  over `subfile_split_max_span` lines): checked in the split step **before**
  `_subfile_split()`'s single-file tiling, since that tiling's `len(files)
  == 1` guard skips the dominant dense-file shape (the file bundled with its
  test file). The peel (`_peel_oversized_file()`, deterministic — a
  `read_text().count("\n")` probe per file, no worker) splits the subtask
  into a single-file child scoped to the one oversized file (which
  re-enters recursion and hits the sub-file tiling above) and a sibling
  child owning the remaining file(s). Both children inherit the parent's
  `depends_on`/`requires`/`provides` verbatim. Guard: if **zero** or **≥2**
  files exceed the cap it returns `[]` and the split falls through
  unchanged.

Both the `fit_judge` call and the coupled-path `splitter` call are wrapped in
`try/except WorkerError`, degrading the node to a leaf (`[subtask]`
unchanged) on a worker crash — DESIGN §5½ (P1), §6 *Credential strategy*. The
migration-path (label-only) `splitter` call inside `_label_migration_chunks()`
carries the same guard, falling back to `_deterministic_chunk_label()` per
chunk instead of a leaf, since the file partition there is already
code-computed.

After recursing into a generation's children, the function calls
`_remap_vanished_deps()` over the flattened leaves with `{child_id: [its_leaf_ids]}`
for every child whose own id did not survive its expansion — the splitter may
give a child `depends_on` on a *sibling* child, and if that sibling later
splits, its id vanishes mid-tree, invisible to `phase_plan` (DESIGN §5½ *Wire-in
to phase_plan*). On the migration path the map is always empty (`_migration_child`
builds children in code; none can name a sibling), so the call is a no-op there.

`repo_map` (the once-built global symbol graph passed from `phase_plan`) is
re-ranked per node via `_rank_repo_map(repo_map, node_files, [])` and injected
into each `fit_judge`/`splitter` prompt as a "RANKED REPO-MAP SUBGRAPH" section.
`None` (skip_repo_map or build failure) omits the injection.

Every `fit_judge` and `splitter` invocation calls `st.bump_workers(caps)` before
`claude_p()`, which is passed the full required signature
(`cwd=str(repo_root)`, `autonomous=False`, `caps=caps`). Both workers use
`INSPECT_TOOLS` (read-only).

`SCHEMAS["fit_judge"]` — required fields: `score` (number 0–1), `rationale`
(string), `diffuse` (string, narrates the diffuse coupling when score < 0.70),
`confidence` (sub-schema via `_confidence_schema(["fit"])`).

`SCHEMAS["splitter"]` — required field: `children` (array; **no `minItems`** —
an empty array is a valid "this does not split" answer, removed 2026-08-03
after the corpus showed the splitter returning `[]` 43 times and a single
no-op child 43 more, every empty return rejected and retried even though
`_recursive_decompose` already accepted it as a leaf). Each child mirrors the
planner subtask shape: required `id`, `title`, `success_criteria_seed`;
optional `intent`, `scope_note`, `files_likely_touched`, `depends_on`,
`requires`, `provides`, `size`, `investigation_notes`. See DESIGN §5½ (P1)
*"This does not split" is a valid answer*.

Both workers are registered in `WORKER_TYPES` and `EFFORT_DEFAULT_PER_WORKER`
(both default to `"medium"`). Both are absent from `MODEL_DEFAULT_PER_WORKER`
(default sonnet via the global `MODEL_DEFAULT` fallback).

### The `adherence_judge` worker (plan-instruction-adherence gate)

`SCHEMAS["adherence_judge"]` — required fields: `user_prescribed_a_procedure`
(boolean — independently re-derived from the task + plan, not copied from
the classifier's own `prescribed_procedure.is_prescribed` signal),
`instruction_adherence` (number 0–10), `violations` (array of strings, each
naming a prescribed step/command the plan circumvented and how), `rationale`
(string). Deliberately carries **no** `_confidence_schema` sub-object —
this worker *is itself* the independent check that replaces a self-report,
so a nested self-confidence axis would reintroduce the self-grading bias
the gate exists to remove.

Registered in `WORKER_TYPES` and `EFFORT_DEFAULT_PER_WORKER` (`"medium"`),
absent from `MODEL_DEFAULT_PER_WORKER` (sonnet via the global `MODEL_DEFAULT`
fallback). **History:** previously pinned to opus after an earlier Sonnet
generation false-positived a legitimate plan here; that gap has since
closed for Sonnet 5 (verified against Opus 4.8, DESIGN §5
*Opus-judgment, sonnet-workhorse*), so it now follows the global sonnet
default — `--model-adherence-judge opus` remains available if this gate
is ever observed to regress. Prompt at `prompts/adherence_judge.md` carries the
calibration: a goal-only task scores `instruction_adherence >= 8.5`; a plan
that substitutes hand-authored/manual work for an explicitly prescribed
procedure scores `<= 3`. The prompt is framed on **ADHERENCE** (does the
plan obey the prescribed process?), not **understanding** (does the plan
reflect correct comprehension of the task?) — an understanding-framed judge
was empirically shown to rubber-stamp a plan that disobeys a prescribed
procedure while still reflecting correct task comprehension, regardless of
model tier, so the ADHERENCE framing itself remains load-bearing.

This worker's `claude_p()` invocation, its position in the plan check loop,
and the `--skip-adherence-check` flag are gate-wiring concerns — see
"Instruction-adherence gate" in §5 for the full wiring (fully shipped as
`phase_adherence_gate`); this section covers only the worker's registration
(schema, prompt, model/effort defaults).

### The independent adversarial verifiers (DESIGN §8)

Three judgment workers replace the self-graded confidence gate on the
classifier, reconciler, and provision self-graders (DESIGN §8 *Independent
adversarial verification*). Each mirrors `adherence_judge`: it *is* the
independent check, so each carries **no** `_confidence_schema` sub-object, and
each gates on a **non-empty array of concretely-named found defects** rather
than a score crossing a threshold — there is no lowerable bar (DESIGN §9
anti-gaming). All three are registered in `WORKER_TYPES` and
`EFFORT_DEFAULT_PER_WORKER` (`"medium"`), absent from
`MODEL_DEFAULT_PER_WORKER` (sonnet via the global `MODEL_DEFAULT` fallback), and
invoked read-only (`INSPECT_TOOLS`, `autonomous=False`) after
`st.bump_workers(caps)`.

`SCHEMAS["classification_judge"]` — required fields: `categories_reviewed`
(array of strings), `miscategorizations` (array of `{kind:
enum[missing_category, spurious_category], category (string),
concrete_work_evidence (string)}` — non-empty ⇒ gate), `rationale` (string).
Verifies the classifier's category set against the task + codebase. Wired as
`phase_classification_gate` after `phase_classify`; a non-empty
`miscategorizations` re-drives `phase_classify` via `_run_checked_loop`
(bounded by `judgment_check_rounds`), `die()`ing on exhaustion — unless
`state.data`'s OR-accumulated `likely_already_satisfied`/`_evidence` signal
is set, in which case exhaustion routes to `_finish_no_work_run` instead
(DESIGN §8 *Reaching the cleared-but-empty state from classification*; see
§9 "classifier" entry above for the field contract). Persists to
`state.data["classification_coverage_gate"]`.

`SCHEMAS["wiring_judge"]` — required fields: `plan_reviewed` (boolean),
`wiring_defects` (array of `{kind: enum[missing_requires, missing_provides,
broken_by_merge, broken_by_drop, orphaned_dependent], sid (string), tag_or_dep
(string), concrete_reason (string)}`, each entry also carrying an **optional**
`severity: enum[live_defect, latent_risk]` — a `live_defect` entry ⇒ gate; a
`latent_risk` entry is logged as a warning and never gates; an entry with no
`severity` gates, per DESIGN §8 *Findings carry a severity* ("the default is
gating")), `rationale` (string). The *semantic*
half of the plan-wiring check (the *structural* half is the deterministic
`check_plan_wiring`, below); wired as `phase_wiring_gate` before
`_validate_plan` — **detect-and-die, single pass**: a `live_defect` entry
`die()`s immediately with the concrete defect named (the reconciler cannot
mechanically invent a missing edge, so no re-drive). Persists to
`state.data["wiring_gate"]`.

**`severity`** distinguishes a live defect (the plan as written will actually
misbehave) from a latent risk (correct today, fragile to a plausible future
edit). Only `live_defect` gates; a mixed list still gates on its
`live_defect` entries — severity narrows what counts as a defect, it is not
a per-entry bypass.

**Asked for, not `required`.** Requiring the field made a judge that omitted
it produce no schema-valid payload at all, so `phase_wiring_gate` never ran
and caught nothing. Both consumers already tolerate absence
(`d.get("severity") == "latent_risk"` at `:19098`, `!=` at `:20140`), so an
unlabelled entry gates — the conservative direction, matching *Findings carry
a severity*'s "default is gating" rule. Pinned by
`tests/test_phase_wiring_gate.py`.

`SCHEMAS["provision_judge"]` — required fields: `recipe_reviewed` (boolean),
`recipe_failures` (array of `{kind: enum[missing_break_system_packages,
wrong_package_manager, lockfile_mismatch, missing_runtime_dep,
wrong_image_assumption], command (string), concrete_reason (string), fix
(string)}` — non-empty ⇒ gate), `rationale` (string). Verifies the detected
install recipe against the image/runtime, catching the semantic gaps the
deterministic `_normalize_pip_installs` / `_validate_provision_recipe` miss.
Wired as `phase_provision_gate` after `phase_provision` — **detect-and-die,
single pass**: a non-empty `recipe_failures` `die()`s immediately with the
judge's concrete `fix` named (a recipe re-detects identically, so no re-drive).
Persists to `state.data["provision_recipe_gate"]`.

The implementer's `solution` self-grade is verified differently — not by a new
worker but by a new **gating `solution_defects` axis on the existing
conformer** (DESIGN §9 *The one gating axis: solution completeness*): the
conformer already runs independently after the implementer's success path and
reviews the implementer's committed diff, so it attacks that diff for behavioral
gaps. See "Per-subtask post-work conformance" in §5 for the gating wiring in
`_settle_subtask`.

### The final two independent adversarial verifiers: `task_coverage_judge` and `integration_judge` (DESIGN §8)

Two more judgment workers close out the remaining self-graded axes on the
planner (`task_understanding`) and the integrator (`resolution`) — DESIGN §8
*Independent adversarial verification*. Both mirror `adherence_judge`/the
three verifiers above exactly: no `_confidence_schema` sub-object (each
worker *is* the independent check), gate on a **non-empty array of
concretely-named found defects**, registered in `WORKER_TYPES` and
`EFFORT_DEFAULT_PER_WORKER` (`"medium"`), absent from
`MODEL_DEFAULT_PER_WORKER` (sonnet via the global `MODEL_DEFAULT` fallback —
`MODEL_DEFAULT` is currently `"sonnet"` for every worker; see CLAUDE.md
"Every worker — judgment and acting/workhorse alike — defaults to
`sonnet`"), invoked read-only (`INSPECT_TOOLS`, `autonomous=False`) after
`st.bump_workers(caps)`. `task_coverage_judge` is wired into
`phase_planning_coverage_gate` (a whole-plan gate run after
reconciliation); `integration_judge` is wired per-subtask into the
integrator's merge flow, attacking each merged result for behavioral
breakage.

`SCHEMAS["task_coverage_judge"]` — required fields: `task_covered` (boolean),
`coverage_gaps` (array of `{kind: enum[missing_work, off_task_subtask],
description (string), concrete_evidence (string)}` — non-empty ⇒ gate),
`rationale` (string). Its JSON payload is only the task text plus the
reconciled subtask set (titles, intents, success criteria), but it also
runs with `INSPECT_TOOLS` and reads task-referenced files itself per its
prompt; it attacks whether the union of subtasks actually covers what the
user asked for:
`missing_work` names a required piece of work no subtask addresses;
`off_task_subtask` names a subtask that does not serve the task at all.
Distinct from `fit_judge` (per-subtask sizing) and `wiring_judge`
(inter-subtask graph correctness) — this is the one check for
whole-plan-vs-task coverage. Wired as `phase_planning_coverage_gate` after
`phase_adherence_gate` and before the phase-3 soft-drop filters/`_schedule()`.

**Advisory.** A non-empty `coverage_gaps` is logged and recorded, never
gated on. The judge is invoked **directly, exactly once** — not through
`_run_checked_loop`, so it is not bound by `judgment_check_rounds`, has no
feedback callback, and never re-drives `phase_plan` or `phase_reconcile`.
Because the call is a single direct `await claude_p(...)` rather than a
loop-managed one, it carries the full required signature at its own call
site: all-keyword, `allowed_tools=INSPECT_TOOLS`, `max_turns=30`,
`autonomous=False`, and `model=models.get("task_coverage_judge",
MODEL_DEFAULT)`, after `st.bump_workers(caps)` — placed **outside** the
`try`, matching `_probe_criteria_satisfied_on_head`, since a
budget-exhaustion `WorkerError` is the run being over budget rather than
this judge failing and must abort instead of degrading. A `WorkerError` from
the judge itself, or an `OSError` from process spawn, degrades (advisory;
the plan is returned unchanged) — the gate never terminates a run. **Any
other exception propagates**, since a programming error at the call site is
not a worker failure and must not be reported as a clean degrade; `OSError`
is disjoint from every programming-error class, so admitting it re-opens
nothing. `tests/test_claude_p_call_sites.py` statically checks this
contract across every `claude_p` call site in the module; 0.10.0 shipped
this one raising `TypeError` on every invocation behind a broad `except`,
and no stub-based test could see it. Persists to
`state.data["coverage_gate"]`.

`SCHEMAS["integration_judge"]` — required fields: `merge_reviewed`
(boolean), `defects` (array of `{kind: enum[dropped_change,
reintroduced_conflict, call_site_mismatch, semantic_regression,
incomplete_resolution], concrete_scenario (string), location (string),
why_broken (string)}` — non-empty ⇒ gate), `rationale` (string). Each
`defects` item also carries an **optional** `coverage_elsewhere`
(`{searched (boolean), file (string), assertion (string)}`) — asked for by
the prompt, deliberately **not** in the item's `required` list (DESIGN §8
*Location is not coverage*; requiring a judge field has three times produced
a worker emitting no schema-valid output at all, and a gate that never runs
catches nothing). A
`dropped_change` whose `coverage_elsewhere` names a file that **exists in
the merged tree** plus a non-blank `assertion` is downgraded to advisory —
logged, not gating. Absence, a blank field, or a named file absent from
the tree all gate exactly as before (the conservative direction). Blankness
and file existence are checked in `_coverage_citation_clears`, not by the
schema: neither string carries a `minLength`, since one on an *optional*
property would break the `--dangerously-force-strict-output` invariant that
forcing a field must never make a trivial value illegal. A hallucinated path
therefore cannot buy a downgrade. Given the merged result plus both parent
diffs and the conflicting subtasks' intents, the judge attacks the merge for
behavioral breakage the mechanical conflict-marker scan and
`check_merge_committed` cannot see — a syntactically clean merge that keeps
one side's signature but the other side's call sites, or silently drops one
side's behavior entirely. Wired into `integrate_wave` as a **detect-and-die,
single pass** gate after a successful merge commit: a non-empty `defects`
array `die()`s immediately with the concrete defect named (an integrator
cannot always mechanically re-derive a correct resolution from a semantic
finding the way a planner can add a subtask, so no re-drive). Persists to
`state.data["integration_gate"][sid]` and
`state.data["integration_defects"][sid]` — see "Integration gate resume +
`accept-integration`" below.

### Integration gate resume + `accept-integration`

Unlike `wiring_gate`, which is written only on a clean pass,
`state.data["integration_gate"][sid]` is written **before** `die()`ing:
`{defects: list[str], advisories: list[str], merge_commit_sha: str, accepted:
bool}` (`accepted` is `not defects` on a fresh judge verdict). A non-empty
`defects` entry is mirrored to the flatter `state.data["integration_defects"][sid]`
(a plain `list[str]`), which is what `accept-integration` clears. Both keys
let a resume distinguish "never reviewed" (both keys absent) from "reviewed
and rejected, not yet accepted" (`integration_gate[sid]` present, `accepted:
False`) from "reviewed and either clean or operator-accepted" (`accepted:
True`) — `wiring_gate`'s single "written only on pass" key cannot express the
middle state, which is exactly the state a run stuck on a false-positive
`integration_judge` verdict is in.

**Granularity: per-sid, chosen on structural grounds.** An sid is stable by
construction, so per-sid acceptance sidesteps the harder question a
per-defect key would raise (a stable defect identity across re-judgements).
If per-defect acceptance is ever wanted, that stability still needs
measuring: re-judge the same merge N times and count how often a given
defect keeps its identity.

`integrate_wave`'s per-sid loop consults `integration_gate[sid]` **before**
re-driving `integrate.sh`/the integrator: `integrate.sh`'s `git merge --no-ff`
is idempotent, so on resume it would just see the branch already merged (rc
0, "Already up to date") and short-circuit straight to `integrated.append`
*without ever re-invoking the judge*. A present, not-yet-`accepted` entry
instead re-invokes the judge directly against the already-committed merge via
the shared `_run_integration_judge_gate` helper (used by both the normal
post-integrator-commit call site and this resume call site, so the
invoke/partition/persist/die sequence cannot drift between them); a present,
`accepted` entry skips straight to `integrated.append` with no judge call at
all. `phase_execute`'s wave loop has a matching adjustment: the
already-complete-subtasks resume shortcut additionally checks for any wave
sid with a pending, un-accepted `integration_gate` entry
(`pending_gate_sids`) and, when one exists, does NOT take the shortcut —
otherwise `integrate_wave` would never be reached again for that wave,
silently advancing `completed_waves` past a rejected merge. Such sids get a
`{"status": "complete"}` stand-in `results` entry, so the resumed judge
re-invocation runs with an empty `incoming_intent`/`incoming_criteria` —
cosmetic only, since the judge's primary evidence is the merge diff itself.

`leerie accept-integration <run-id> <subtask-id> [--runtime fly|ec2|local]`
mirrors `accept-blocked`'s shape and local/Fly/EC2 state-mutation machinery
exactly (same runtime auto-detection via `_auto_detect_run_runtime`, same
run-id/subtask-id allowlist validation, same wake-mutate-pause dance for a
stopped Fly machine or EC2 instance, same `ACCEPTED:`/`NOOP:`/`ERROR:`
sentinel-line convention for the ssh/ssm-piped mutation to survive
`flyctl`'s exit-code flattening) — only the mutated field and precondition
differ: it flips `integration_gate[sid]["accepted"]` to `True` and pops
`sid` from `integration_defects` (`NOOP:` if already accepted, `ERROR:` if
`sid` has no `integration_gate` entry at all). A subsequent `resume` then
takes the `accepted` branch above and advances past the finding without
re-invoking the judge.

**`plan_overlap_judge`'s `judgment` self-score gets no new verifier — it is
dropped, and its existing deterministic validators become its sole gate.**
`check_overlap_judge_output` no longer gates on the judge's self-reported `confidence`; the object stays on the schema as an advisory record only.

With these two workers wired, `_confidence_issues` had zero remaining callers and has been deleted: no worker gates on its own self-reported confidence anywhere in the pipeline. Every gating check is either deterministic or an independent adversarial verifier. `tests/test_check_functions.py`'s `TestConfidenceIssuesDeleted` pins both the absence and the property.

`--max-turns` by worker: classifier 60, planner 100, reconciler 30,
plan_overlap_judge 30, provision 30, integrator 60, implementer 120,
conformer 60, judge 40, heal patch_generator 40, pr_writer 20, fit_judge 30,
splitter 30, adherence_judge 30, classification_judge 30, wiring_judge 30,
provision_judge 30, task_coverage_judge 30, integration_judge 30 —
matching every other read-only judgment verifier. For
the implementer, 120 turns and 90 minutes both apply — whichever trips
first. The conformer cap is lower than the implementer's because its
scope is narrower (read a diff, read a small set of rules files, update
docs/tests, run build/lint/test) and the phase is advisory — running
out of turns becomes a warning, not a failure. The planner cap is the
largest of the inspect-tool workers because the planner drives the §8
confidence loop and is the worker most likely to need additional turns
on heavy domains; a too-tight cap there directly degrades the §8
confidence signal it emits.

The `wave_revalidation_rounds` and `revision_retries` caps were
removed when the wave-level LLM validator and the criteria-revision
channel retired (DESIGN §8, §9). State files from older runs may still
carry the corresponding fields; the orchestrator is read-tolerant of
them.

### Worker-internal caps (prompt-governed — NOT counted by the orchestrator)
These iterate inside one worker; the orchestrator sees only the final result.
The real backstop is the worker's `--max-turns` above.

| Loop | Instructed limit | Instructed outcome |
|------|------------------|--------------------|
| evidence-gate iterations (implementer) | `confidence_rounds` (default 8) | return `blocked` |
| evidence-gate iterations (planner) | `confidence_rounds` (default 8) | emit `status: "blocked"`, empty subtasks, gap analysis |
| validate-against-criteria iterations (implementer) | 5 | return `failed` |

The `confidence_rounds` cap is user-tunable (see §2 "Confidence rounds")
even though the iterations themselves are counted inside the worker. The
guarantee remains prompt-governed per DESIGN §13.

Per DESIGN §10 #1, **granular sizing is the primary defense** against
context exhaustion — these caps are a safety net, not the main path.
If they fire often, the planner is under-decomposing (DESIGN §5); look
there first when handoffs become routine.

Maps to `DESIGN.md`: §13. The code-enforced / prompt-governed split there is
*the* point — do not present the second table as a code guarantee.

### The two-tier retry policy — `_retryable_failure(kind)`
One classifier function decides retryable vs. terminal. It dispatches on a
structured `failure_kind` enum tagged at the producer; the prose `reason`
stays for user-visible diagnostics but no longer drives control flow. The
retryable set is the module-level constant `_RETRYABLE_FAILURE_KINDS`.

Per DESIGN §12, classifying a prose `reason` by substring match would be
deterministic code making a judgment call on natural-language text. Tagging
at the producer eliminates the prose round-trip.

The coupling test in `tests/test_retryable_failure.py` enforces that
every retryable-path return from a producer (`_validate_result`,
`check_branch_has_commits`, the inline dirty-worktree check,
`_run_implementer`'s `WorktreeSetupError`, `_invoke`'s
`PidExhaustedError`/`OomKilledError`) carries a `failure_kind` in
`_RETRYABLE_FAILURE_KINDS`. When adding a new retryable failure mode,
extend the enum and update the producer in the same change.

| Failure | Tier | `failure_kind` / source |
|---------|------|-----------------|
| branch has no commits ahead of the run branch | Retryable *unless* the success criteria are already met on the run-branch HEAD (a sibling subtask committed this deliverable this run, or it was already on the base tree — DESIGN §8 *The mid-run sibling case* + *Scope*), in which case `_settle_subtask` settles it `complete` (`dropped_subtasks` reason `"already_satisfied_mid_run"`) and it never reaches this tier | `"no_commits"` from `check_branch_has_commits` |
| worktree left dirty | Retryable | `"dirty_worktree"` from the inline dirty-worktree check in `_settle_subtask` |
| `incomplete-handoff` worker produced no checkpoint on disk | Retryable | `"empty_handoff"` from `_validate_result`'s incomplete-handoff branch when the checkpoint file is missing. Triggers in two known cases: (1) Claude Code session-limit / rate-limit no-op workers leave no checkpoint (primarily caught by `_detect_session_limit()` upstream; this is the safety net for a message-format change), and (2) a worker that hit `--max-turns` with no checkpoint written, which `_run_implementer`'s WorkerError handler synthesizes into the same envelope. Both are corrective-note cases. |
| cross-field invariant violation (other) | Terminal | `"broken"` from `_validate_result` |
| diff touched a protected path | Terminal | `"broken"` from `check_diff_scope` |
| worker-level error (timeout, schema-invalid twice) | Terminal | `"broken"` from `WorkerError` path |
| `new-worktree.sh` could not create the worktree | Retryable (infrastructure) | `"worktree_setup"` from `_run_implementer`'s `WorktreeSetupError`, caught by its own arm in `_settle_subtask` **before** the generic `except WorkerError` (it is a subclass, so a generic-first order swallows it). Infrastructure, not a broken worker: the raise happens before any worker runs, so `_retryable_failure`'s terminal rationale ("the worker is broken or dishonest, and re-running burns an invocation for no expected gain") does not apply — re-running is exactly what clears it |
| worker's cgroup exhausted its PID table (fork denials climbing / at `pids.max`) | Retryable (infrastructure) | `"pid_exhausted"` from `_invoke`'s `PidExhaustedError`, caught by its own arm in `_settle_subtask` **before** the generic `except WorkerError` (it is a subclass, so a generic-first order swallows it). Infrastructure, not a broken worker: a fresh worker gets a clean PID table, which is exactly the remedy the raise site's own log message already promised ("Terminating early so a fresh worker retries with a clean PID table") |
| worker's tool subtree (a build/test command) overshot `memory.max` and was kernel-killed mid-turn | Retryable (infrastructure) | `"oom_killed"` from `_invoke`'s `OomKilledError`, caught by its own arm in `_settle_subtask` **before** the generic `except WorkerError` (it is a subclass, so a generic-first order swallows it). Infrastructure, not a broken worker: a resource limit killed it, not the worker's own behaviour, so a fresh attempt is the remedy |
| structured-output envelope contains literal `antml:`-shaped markup inside a string field value | Retryable | `"corrupted_envelope"` from `_validate_result`, checked at the TOP of the function before the status dispatch. Upstream anthropics/claude-code#64690: a model-side token-generation bug can leak tool-call markup (`antml:parameter`, `antml:invoke`, …) into a structured-output field's own string value. No legitimate output contains this substring (per CLAUDE.md's Language-to-JSON rule), so it is an unambiguous corruption signal — the underlying work may well have succeeded, so it is retryable rather than the terminal `"broken"` a coincidentally-matching field shape (e.g. a corrupted `checkpoint_path`) would otherwise fall through to |

**Two categories, one source of truth.** `_INFRASTRUCTURE_FAILURE_KINDS` is
the single place a kind is declared *not the worker's doing*; everything else
derives from it, including `_RETRYABLE_FAILURE_KINDS`, which is **composed**
(`_WORKER_RETRYABLE_KINDS | _INFRASTRUCTURE_FAILURE_KINDS`) rather than
hand-maintained. A kind listed in only some of several lists is silently
half-wrong — retried but branch-deleted, or preserved but terminal — and that
is the bug class this area keeps producing. An infrastructure retry is also
**logged**; it used to be silent, since `fail()` logs only on terminal or
cap-reached.

*Candidates deliberately not yet moved into the category:* `worker {sid}
exhausted its PID`, `worker {sid} was OOM-killed`, `Claude API connection
dropped mid-response`, 529 overloaded, and auth/quota all raise
`WorkerError` on the same generic path and are infrastructure by nature, but
reclassifying them is a behaviour change beyond the incident that motivated
the category and could mask a real resource problem, so it needs its own
evidence.

**Retry in place vs. reset first.** `fail()` normally calls
`_reset_subtask_worktree` before looping, which runs `git branch -D` on the
subtask branch — correct for `no_commits` (nothing worth keeping) and
destructive when it does not. Infrastructure kinds skip that reset: a
worktree-setup failure fires before any worker runs, so there is nothing
from *this* attempt to clear, while an *earlier* attempt's commits may
already be sitting on the branch. `new-worktree.sh` reuses an existing
branch by design, so retrying in place re-attaches to that work instead of
deleting it.

The exemption also covers the `continuation` flag and the corrective `note`:
an infrastructure failure carries no information about what the worker
should do differently, so it must not overwrite state that does — a
worktree failure after the mechanical-check path has already set
`continuation=True` plus a `_format_check_feedback` note must leave that
feedback intact, or the retried worker burns `implementer_confidence_retries`
re-discovering it. Only a *worker* failure earns a corrective note.

`_settle_subtask` routes every failure through `_retryable_failure` via the
`fail(kind, reason)` helper. Retryable consumes the retry cap; terminal ends
the subtask on first occurrence.

On a retryable failure that will loop, `fail()` calls
`_reset_subtask_worktree(sid, leerie_dir, run_id)` to remove the leftover
per-subtask worktree directory and its branch
(`leerie/subtasks/<run-id>/<sid>`), then `_prune_leerie_worktrees` to clear the
stale `.git/worktrees/<sid>/` metadata entry, so the retry's `new-worktree.sh`
reaches its "fresh subtask" path on the next iteration. Without this reset the
retry re-runs the script against a still-registered worktree and an existing
branch — the second `git worktree add -b` fails with
`fatal: a branch ... already exists`, the `WorkerError` escapes
`_settle_subtask`, and `_gather_or_cancel` takes down the rest of the wave.

---

## 6½. Per-repo dependency provisioning

Implements DESIGN §6½. The provision phase fires once per run, between
classify and plan; on `resume` it is guarded on the presence of
`st.data["provision"]["recipe"]` (key-presence, not truthiness — an
empty recipe is a valid completed state) so a resume that already
persisted a recipe does not re-fire `mise install` (DESIGN §6
"Resumable planning").

### Worker registration

`WORKER_TYPES` gains `"provision"`. `SCHEMAS["provision"]` is the JSON
schema for the LLM-fallback recipe:

```python
{
    "type": "object",
    "required": ["recipe"],
    "properties": {
        "recipe": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["kind", "command", "working_dir"],
                "properties": {
                    "kind": {"enum": ["install", "build", "none"]},
                    "command": {
                        "type": "array",
                        "items": {"type": "string"},
                        "minItems": 1,
                    },
                    "working_dir": {"type": "string"},
                    "timeout_s": {"type": "integer", "minimum": 1},
                },
            },
        },
        "confidence": {"type": "string"},
        "notes": {"type": "string"},
    },
}
```

`_detect_recipe_from_lockfiles(repo_root) -> list[dict]` is the
deterministic table. It returns a list of `{kind, command, working_dir,
timeout_s}` dicts — possibly empty (table miss → LLM fallback), possibly
multi-entry (polyglot repos like Rails-with-frontend emit *all* matches,
not first-wins).

| Detected file | Emitted command | Notes |
|---|---|---|
| `pnpm-lock.yaml` | `pnpm install --frozen-lockfile` | takes precedence over yarn.lock and package-lock.json |
| `yarn.lock` (no pnpm-lock.yaml) | `yarn install --frozen-lockfile` | |
| `package-lock.json` (neither above) | `npm ci` | |
| `uv.lock` | `uv sync` | |
| `poetry.lock` | `poetry install` | |
| `Pipfile.lock` | `pipenv install` | |
| `go.mod` + `go.sum` | `go mod download` | |
| `Cargo.lock` | `cargo fetch` | |
| `Gemfile.lock` | `bundle install` | |
| `composer.lock` | `composer install --no-interaction` | |
| `packages.lock.json` | `dotnet restore` | NuGet lockfile |
| anything else | (no entry — caller falls back to LLM worker) | bare `requirements.txt`, bare `pyproject.toml`, Maven (`pom.xml`), Gradle, polyglot Makefile |

`_validate_provision_recipe(recipe) -> None` enforces (raises `ValueError`
on violation):
- `command[0]` is in the argv allowlist `{pnpm, npm, yarn, pip, pip3,
  uv, poetry, go, cargo, bundle, gem, mvn, gradle, gradlew, make,
  composer, dotnet}`.
- No `sudo` anywhere in the argv.
- No shell metacharacters (`|`, `&`, `;`, `$`, backticks, `>`, `<`, `\n`)
  in any argv element.
- `working_dir` is either `"."` or a relative path with no `..` segments
  and no leading `/`.

### Phase implementation (`phase_provision`)

Insertion point in `_orchestrate()`: inside the `else:` (fresh-run)
branch, after the `_write_run_json(...)` block and before
`gather_answers(st, supplied)`. Step order:

1. **Docs-only short-circuit.** If the categories from classify
   contain no code-touching category (only `documentation`, etc.),
   record `kind: none` and return.
2. **Setup hook.** `_run_setup_hook(repo_root, log_dir, st)` execs
   `<repo>/.leerie-setup.sh` if present (10-min timeout, streams to
   `<state-root>/runs/<id>/logs/setup-hook.log`). Idempotent via
   `st.data["provision"]["sh_hook_ran"]`. Nonzero exit → `die()`.
   **Runs as the non-root `leerie` container user; no sudo.** The hook
   can install user-space tooling (`mise install <lang>@<version>`,
   anything writing to `~/.local/bin`) and pre-populate fixtures, but
   cannot `apt-get install` or write to system directories. Repos
   that need root-level system packages maintain a fork of the leerie
   Dockerfile and override `IMAGE_TAG`; out of scope for the hook.
3. **Mise go-override synthesis.** `_synth_mise_go_override(
   repo_root, run_dir) -> Path | None`: if `go.mod` exists but the
   repo has no `.go-version`, no `.tool-versions` go entry, and no
   `mise.toml`/`.mise.toml` go pin, parse `go.mod`'s `go 1.X[.Y]`
   directive and write `<run_dir>/mise-overrides.toml` containing
   `[tools]\ngo = "<version>"`. **Both `mise.toml` AND `.mise.toml`
   (dotted form) are recognized**; non-dotted form wins if both exist
   (matches mise's discovery precedence). If the repo has an existing
   mise config, its `[tools]` content is preserved in the override
   file (`MISE_OVERRIDE_CONFIG_FILENAMES` replaces rather than merges,
   so the override must carry the repo's existing pins plus leerie's
   addition). Idiomatic version files (`.nvmrc`, `.node-version`,
   `.python-version`, `.ruby-version`) and `.tool-versions` entries are
   ALSO copied in when the same tool isn't already pinned — otherwise
   the override would silently drop them too (mise discussions #6598 /
   #7058). Returns the absolute path to the override file.

   **Precedence between idiomatic files** (leerie's choice, not mise's
   documented behavior): when both `.nvmrc` and `.tool-versions` pin
   the same tool with different versions, `.nvmrc` wins — the
   iteration order in `_read_idiomatic_pins` runs the dedicated
   single-tool files BEFORE `.tool-versions`, so the first-seen pin
   sticks. asdf-compatible names like `nodejs` and `python3` in
   `.tool-versions` are normalized to mise's `node` / `python` via
   `_ASDF_TOOL_ALIASES` so a `.nvmrc` + `.tool-versions: nodejs ...`
   repo doesn't end up with both `node` and `nodejs` pins.
4. **Mise install.** `_run_mise_install(repo_root, log_dir, st)`:
   exports `MISE_OVERRIDE_CONFIG_FILENAMES=<path>` if step 3
   produced one, then runs `mise install` at the repo root. mise
   reads `.tool-versions` natively, and reads `.nvmrc` /
   `.python-version` / `.ruby-version` / `rust-toolchain.toml` /
   `.go-version` because the image sets
   `MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS=node,python,ruby,rust`.
   Ruby uses precompiled binaries (`MISE_RUBY_COMPILE=false` in the
   image) to avoid requiring the full ruby-build toolchain.
   Streams to `<state-root>/runs/<id>/logs/provision.log`. Nonzero exit
   surfaces the failing tool+version to `die()`.
5. **Version capture.** Runs `mise ls --current --json` (the
   subcommand `mise current --json` does not exist; verified
   against mise.usage.kdl). Output is object-keyed-by-tool, each
   value an array of `{version, install_path, source}` objects.
   Raw blob stored at `st.data["provision"]["mise_versions"]`;
   `tools[name][0].version` is the value rendered in `leerie list`
   and one-line log summaries.
6. **Table-first detection.** `_detect_recipe_from_lockfiles(
   repo_root)`. Non-empty result is the recipe (marked
   `source: "table"` in state).
7. **LLM fallback.** Empty table result → `_gather_provision_fixtures(
   repo_root)` assembles inputs (see below), `claude_p("provision",
   prompt, fixtures, SCHEMAS["provision"], model)` returns a
   recipe (marked `source: "llm"` in state).
8. **Validate.** `_validate_provision_recipe(recipe)`. Reject →
   `die()`.
8½. **Normalize pip installs.** `_normalize_pip_installs(recipe)` adds
   `--break-system-packages` to every `pip`/`pip3`/`python -m pip`
   *install* entry that lacks it (`_is_pip_install` finds the `install`
   subcommand as the first non-option token after the pip prefix, so a
   leading global flag like `pip -v install` still matches; `uv pip
   install` and `pipx install` are exempt — they manage their own
   environments). The container's system Python is Debian-13
   externally-managed (PEP 668) — a bare `pip install` exits non-zero,
   silently breaking every recipe consumer (most visibly
   `_capture_conformance_baseline`, whose failed `pip install` leaves
   the base test axis recording `command not found`). Normalizing at
   this one chokepoint fixes the baseline installer *and* the
   `PROVISION_RECIPE:` prompt block at once. The flag is a no-op on a
   non-externally-managed interpreter, so it is applied unconditionally.
9. **Persist (do not execute).** Full recipe + `source` + resolved
    versions saved to `st.data["provision"]`. The recipe is not
    executed by `phase_provision` — the implementer and conformer
    workers run install commands from their own worktrees, given the
    recipe via prompt injection
    (`_format_provision_recipe_section()`). See "Worker-driven
    install" below.
10. **Export env.** If `_synth_mise_go_override()` created an override
    file, `os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"]` is set to
    its path so every downstream worker subprocess inherits it.

### Helper functions

| Function | Purpose |
|---|---|
| `_gather_provision_fixtures(repo_root) -> dict` | Assembles the LLM-worker input set under a 24KB total ceiling. README extracted by `_extract_readme_sections()`; root manifests (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, `Makefile`, `pom.xml`, `build.gradle*`) included if present; workspace child manifests capped at 3 (1KB each) for monorepos; up to 2 `.github/workflows/*.yml` files matching `(?i)ci\|test\|build\|release` (skip `codeql\|stale\|dependabot`); optional `CONTRIBUTING.md` / `docs/DEVELOPMENT.md` capped at 4KB. |
| `_extract_readme_sections(text) -> str` | **Size-bounding only** — returns the leading slice of the README, ≤ `_README_EXTRACT_BUDGET` (12KB), cut back to the last section boundary so the worker never receives a header with its body sheared off (never discarding more than 25% of the budget chasing one; a headerless document still gets its first three-quarters of the budget, i.e. ~9KB at the current 12KB setting). Under budget the README is returned verbatim. **It does not decide which sections are install-relevant** — that is a judgment about prose and belongs to the `provision` worker, which is told so in `prompts/provision.md`; no keyword filter is applied. `_gather_provision_fixtures` additionally reports `readme_bytes_unseen` / `readme_sections_unseen`, and `_format_provision_user_prompt` renders them as an explicit `[TRUNCATED: …]` note under an `UNFILTERED` header — a slice the worker cannot tell is a slice reads as the whole README. Reporting the size of the gap is mechanical; guessing at its contents would be back to classifying prose. |
| `_run_setup_hook(repo_root, log_dir, st)` | Execs `<repo>/.leerie-setup.sh` if present with a 10-min timeout via `_run_streaming` (live output to terminal + persistent log at `<log_dir>/setup-hook.log`); sets `st.data["provision"]["sh_hook_ran"] = True` on success. |
| `_synth_mise_go_override(repo_root, run_dir) -> Path \| None` | See step 3 above. Returns the absolute path to the override file or `None` if no synthesis was needed. |
| `_run_mise_install(repo_root, log_dir, st)` | Runs `mise install` + `mise ls --current --json` at `repo_root`. The install streams via `_run_streaming` so the user sees per-tool progress on a first-run Python/Ruby/Rust install. |
| `_format_provision_recipe_section(recipe, *, audience) -> str \| None` | Renders the persisted recipe as a `PROVISION_RECIPE:` block for injection into implementer or conformer prompts. Audience-specific framing ("decide whether your subtask needs them" vs "ensure deps before BUILD/LINT/TEST"). Returns None when the recipe is empty or all-`none`. |
| `phase_provision(repo_root, st, models)` | Orchestrates all of the above. Detects + persists the recipe; does NOT execute it (workers run installs in their worktrees per DESIGN §6½). Exports `MISE_OVERRIDE_CONFIG_FILENAMES` to `os.environ` if a synth override was created, so all downstream worker subprocesses inherit it. |
| `_run_streaming(cmd, ..., log_path, verbosity, ...)` | Async subprocess helper with live-streamed stdout+stderr, persistent log file, bounded tail deque, and `TimeoutExpired` carrying the tail in `.output`. Used by `_run_mise_install` and `_run_setup_hook`; replaces the previous `run_proc` calls that buffered output for the entire run duration. |

### Caches

Six host caches mounted into the container, all `rw`. Listed in §0.5
"Bind-mount table." Concurrency-safety verdicts:

- **mise installs** — Safe. Version dirs are immutable once installed;
  mise renames atomically on install.
- **pnpm store** — Safe (CAS, atomic ops; pnpm/discussions#10702).
- **Go modules** — Safe (`flock` per module-version in
  `cmd/go/internal/modfetch`).
- **Cargo** — Safe (flock on index + per-crate locks). Whole
  `CARGO_HOME` is mounted; mounting only `registry/` breaks
  `config.lock` (cargo#11376).
- **pip** — Mixed. Most races fixed (pypa/pip#9470, #12361, #13540
  closed). The wheel-build race #9034 (concurrent `pip install` of
  the same sdist into the same wheel-cache slot) is still open; in
  practice leerie runs a small number of concurrent workers and the
  collision window is narrow. A worker that does hit the race retries
  once via pip's own retry, and a persistent failure surfaces as a
  conformer warning (DESIGN §9), not a silent corruption.

- **Bundler** — Mounted. `BUNDLE_PATH` and `BUNDLE_CACHE_ALL=1` are set
  so `bundle install` reuses cached gems across worktrees and runs.
  The historic `unlink` race (rubygems/bundler#4519) was fixed in
  Bundler 2.2+; all supported Ruby versions ship a sufficiently recent
  Bundler.

### Persistent bake + residual worker install

`scripts/new-worktree.sh` does just the `git worktree add` and prints
the worktree path. Worktrees inherit the persistent bake from the
image (DESIGN §6½ *Persistent out-of-repo dependency bake*) with zero
or minimal install cost. There is **no orchestrator-driven install**
after the worktree is created.

How dependencies reach the worker:

1. `git worktree add` checks out the worktree (tracked files only).
   It starts with no `node_modules/` / `.venv/` / `target/` by
   design, but the persistent bake at `/opt/venv`, `/opt/bundle`,
   etc. is already present in the image.
2. The orchestrator parses the worktree path from the script's stdout.
3. `_run_implementer` (and later `_run_conformer`) read
   `st.data["provision"]["recipe"]` and inject it as a
   `PROVISION_RECIPE:` block in the worker's user prompt via
   `_format_provision_recipe_section(...)`.
4. The worker's prompt (see `prompts/implementer.md` §2 and
   `prompts/conformer.md` §Input) instructs it to decide whether the
   subtask needs the residual install step (for Node: the offline
   relink; for Python/Ruby/Rust/Go: typically nothing) and to run
   the command from its worktree if needed. For fully-baked
   ecosystems, the `PROVISION_RECIPE:` block is informational only.
5. If the recipe is missing or empty (docs-only run, or fully-baked
   with no residual), no `PROVISION_RECIPE:` block is injected and
   the worker proceeds without one.
6. Install failures inside a worker surface through the worker's
   normal exit machinery — a hard-failing build/test in the
   implementer becomes a `failed` or `blocked` status; in the
   conformer it surfaces as a `tests-failed: …` advisory warning
   (DESIGN §9).

Why this shape (persistent bake + residual worker install):

- The host's repo is bind-mounted at `repo_root`, so an
  orchestrator-driven install there would write linux-arm64 native
  binaries into the host's darwin `node_modules`, corrupting the
  host's checkout.
- Per-worktree pre-install wastes work for subtasks that don't need
  built deps (config-only, doc-only, non-test refactors). The
  persistent bake eliminates this for Python/Ruby/Rust/Go; Node's
  residual relink is minimal.
- The bake is shared read-only across concurrent worktrees, so
  installs are paid once per image build, not once per worktree.
- `claude -p`'s built-in stream-event plumbing surfaces Bash tool I/O
  to the orchestrator log live, so an install inside a worker is
  visible without any special streaming code.

The `MISE_OVERRIDE_CONFIG_FILENAMES` env var that `phase_provision`
synthesizes for polyglot Go repos (go.mod with no `.go-version`
sibling) is exported to `os.environ` once in `phase_provision` (and
re-exported from persisted state on `resume`); worker subprocesses
inherit it without any per-worker plumbing because `_invoke` does
not pass an explicit `env=` to `create_subprocess_exec`.

**Convention-doc injection (`CONVENTION_DOCS:` block).** Alongside the
recipe, `_run_implementer` injects the repo's authoritative convention docs
so the implementer writes to the repo's design conventions on the first try
rather than drifting and relying on a post-hoc conformer catch (DESIGN §9).
It calls `_discover_rules_files(st.repo_root)` — the same discovery the
conformer uses — and renders the surviving paths (relative to `repo_root`)
as a `CONVENTION_DOCS:` line, matching `_run_conformer`'s `RULES_FILES:`
formatting. Paths only, not contents: the implementer opens the docs
relevant to its subtask itself, avoiding inlining a large design-system
doc into every prompt. When discovery returns nothing, no block is
injected. The `prompts/implementer.md` §3 evidence gate and §4 Implement
step name this block so the worker reconciles the pattern it followed
against the discovered conventions.

### Auto-capture of repo dependencies

Implements DESIGN §6½ *Auto-capture of repo dependencies*. All Python
surface lives in `orchestrator/leerie.py`.

#### Capture functions

| Function / Constant | Signature / Value | Role |
|---------------------|-------------------|------|
| `_DEPCAP_TOTAL_BUDGET` | `307200` (bytes) | Byte ceiling for the install-command hint fed to the dep_capture worker (~300 KB ≈ 75k tokens). Mirrors the `_gather_provision_fixtures` add_bytes/hit_ceiling idiom. |
| `_DEP_MANIFEST_NAMES` | tuple | Fixed tuple of dependency-manifest filenames gathered as the PRIMARY corpus — matched by exact name, **not** a glob (`requirements.txt`, `requirements-dev.txt`, `requirements-test.txt`, `pyproject.toml`, `Pipfile`(`.lock`), `setup.py`/`setup.cfg`, `package.json`, `pnpm-lock.yaml`, `package-lock.json`, `yarn.lock`, `go.mod`, `Cargo.toml`(`.lock`), `Gemfile`(`.lock`), `composer.json`(`.lock`)). |
| `_DEPCAP_MANIFEST_FILE_BUDGET` / `_DEPCAP_MANIFEST_TOTAL_BUDGET` | `16384` / `131072` (bytes) | Per-file and total byte caps for the gathered manifest corpus. |
| `_DEPCAP_INSTALL_RE` / `_DEPCAP_TEXT_TOOLS` / `_DEPCAP_SEGMENT_RE` | regex / frozenset / regex | Install-verb matcher (verb at a command boundary), the set of text-scanning command words (`grep`/`git`/`sed`/…) excluded from the command hint, and the shell-separator splitter (`\n ; && \|\| \| &`) used to evaluate a chained command per-segment. |
| `_is_install_command` | `(cmd: str) -> bool` | True iff **some shell segment** of `cmd` (split on `_DEPCAP_SEGMENT_RE`) invokes a package-manager install verb (`_DEPCAP_INSTALL_RE`) while that segment's leading word (after an optional `sudo`) is not a text tool. Per-segment evaluation keeps a genuine install chained after a text tool (`echo hi\npip install x`, `git log && pip install x`) while still dropping the leak where the verb is inside the text tool's own arg (`grep "apt-get install intents"`). |
| `_gather_dep_manifests` | `(repo_root: Path) -> str` | Reads the repo's dependency-manifest files (`_DEP_MANIFEST_NAMES`) present in `repo_root`, bounded per file and in total; returns a labeled `### <filename>` block per manifest. The PRIMARY dep_capture corpus (DESIGN §6½). |
| `_extract_depcap_commands` | `(log_dir: Path) -> tuple[str, bool]` | Iterates `sorted(log_dir.glob("*.log"), reverse=True)` (newest-first); calls `_iter_log_tool_use` on each; collects distinct `command` values from `kind == "Bash"` tool-use blocks **that pass `_is_install_command`** into an insertion-order dict. Admits commands under `_DEPCAP_TOTAL_BUDGET` bytes (separator `\n---\n`). The SECONDARY hint (system/native deps) — manifests are primary. Returns `(commands_text, hit_ceiling)`. |
| `_normalize_setup_packages` | `(pkgs: list[str]) -> str` | Renders a package list in the canonical persisted form: order-preserving dedup, space-joined. Shared by `_merge_setup_packages` (union) and the `replace` path so both emit byte-identical TOML values. |
| `_merge_setup_packages` | `(existing: str, captured: list[str]) -> str \| None` | Parses `existing` (space- or comma-separated, per DESIGN §6½); takes the union with `captured`; returns the merged string (via `_normalize_setup_packages`) only if it grew (else `None` → no write). Preserves user-narrowed lists: only genuinely-new packages are appended; nothing is removed. |
| `_dump_language_installs` | `(entries: list[dict]) -> str` | JSON-encodes `language_installs` for TOML persistence, escaping any literal `'` in the payload (e.g. a shell-quoted install command `pip install 'requests[security]'`) as the JSON escape `'`. Guarantees the value contains no literal single quote so `_toml_value`'s single-quoted TOML *literal* wrapper stays valid; `json.loads` (both readers) recovers the original `'`. |
| `_toml_value` | `(val: str) -> str` | Renders `val` as a TOML string for `_write_config_toml_keys`. A value containing `"` (notably the JSON-encoded `language_installs`) is wrapped in a TOML *literal* (single-quoted) string; this requires the value to contain no literal `'` — guaranteed for `language_installs` by `_dump_language_installs`, and trivially true for `setup_packages` (apt names have no quotes). Both readers already `.strip("'")`, so it round-trips with no unescaping; plain values keep the `"..."` basic-string form. Prevents invalid TOML from inner quotes. |
| `_write_config_toml_keys` | `(cfg_path: Path, updates: dict[str, str]) -> None` | Minimal deterministic TOML upsert. Creates the file with a leerie header (matching the launcher's `config --init` heredoc tone) if absent; otherwise replaces the first *uncommented* `key =` line for each key, or appends if absent. Values rendered via `_toml_value`. Never touches commented lines. Writes via temp-file + `os.replace()` (State.save atomicity discipline). |
| `capture_repo_deps` | `async (repo_root: Path, st: State, caps: dict \| None, models: dict[str, str] \| None, efforts: dict[str, str \| None] \| None, replace: bool = False) -> None` | Main entry point. Guards: `resolve_capture_deps`, caps/models/efforts availability, `log_dir` existence, committed `.leerie/Dockerfile` skip. Builds a **manifests-first** corpus: `_gather_dep_manifests` (primary) + `_extract_depcap_commands` (secondary install-command hint); if BOTH are empty, returns. Checks worker budget; invokes `claude_p(schema_key='dep_capture', ...)` with `_load_prompt("dep_capture")`, composing a two-section user prompt (manifests, then install-command hint). **`replace=False` (default, every automatic seam):** writes `setup_packages` (via `_merge_setup_packages`, never-clobber) and `language_installs` (new managers only, keyed by `manager` field, never-clobber) to `.leerie/config.toml`. **`replace=True` (only the operator-driven `--recapture --force` path):** wholesale-replaces both keys from the fresh capture (drops deps no longer captured); an empty capture leaves the existing config untouched. Writes via `_write_config_toml_keys`. Non-fatality is enforced at each call site's `try/except`, not inside the function. |
| `resolve_capture_deps` | `(repo_root: Path) -> bool` | env `LEERIE_CAPTURE_DEPS` > `.leerie/config.toml` `capture_deps` key; default `True`. No CLI flag and no `leerie.toml` tier (env → config → default only). |

#### dep_capture worker

`dep_capture` is a non-WORKER_TYPES worker (like `pr_writer`) registered in
`SCHEMAS`, `_allowed_schema_keys`, `EFFORT_DEFAULT_PER_WORKER` (medium), and
`resolve_models`/`resolve_efforts`. It is **absent** from
`MODEL_DEFAULT_PER_WORKER`; its `sonnet` default comes from the global
`MODEL_DEFAULT` fallback. Its model override is env-var-only (no CLI flag, no
`leerie.toml` key): `MODEL_DEP_CAPTURE_ENV = "LEERIE_MODEL_DEP_CAPTURE"`.
System prompt is `prompts/dep_capture.md`. Output schema:

```json
{
  "setup_packages": ["string"],
  "language_installs": [
    {"manager": "string", "command": "string", "copy_inputs": ["string"]}
  ],
  "dockerfile_notes": "string | null"
}
```

`setup_packages` items and each `language_installs` `manager`/`command` carry
`minLength: 1` (mirrors `pr_writer`): a schema-valid empty-item capture would
render to `""` and, under `--recapture --force` (replace path), blank the
persisted config. The schema is the enforcement layer (DESIGN §12); the replace
path additionally gates the write on the rendered value being non-empty.

#### Capture trigger seams

`capture_repo_deps` is called from three seams; all are non-fatal (wrapped in
`try/except`):

1. **Finalize (clean finish).** Called with `await` from `phase_finalize`,
   after `finished_at` is written and run-branch verification completes.
   `caps`, `models`, and `efforts` are forwarded from `phase_finalize`'s
   parameters. The resume-of-finished guard in `_run_phases` returns before
   `phase_finalize` is reached, so capture never re-fires on a completed
   resume; a partial resume that reaches finalize re-runs capture, and the
   union merge makes this a no-op when nothing new was found.

2. **Cancel / SIGTERM arm (catchable signals).** In `main()`'s
   `KeyboardInterrupt` and `InterruptedBySignal` exception handlers, after
   `st.save()`, a best-effort `asyncio.run(capture_repo_deps(...))` runs in
   its own event loop — the same post-loop pattern as the `RateLimitedExit`
   arm. Non-fatal: any exception is logged and suppressed. Covers Ctrl-C and
   `nerdctl stop`, where the orchestrator gets a real Python window before
   the `finally` cleanup block.

3. **Host-side (`run_recapture_deps` / run-start backstop).** Two host-side
   seams funnel to the same worker:
   - **`run_recapture_deps(leerie_root, repo_root, force, run_id)`**: the
     on-demand recapture entrypoint invoked by `leerie config --recapture`.
     When `run_id` is given, targets that run only; otherwise consolidates
     across **all** finished runs with `logs/` (newest-first). Each target
     run's `State` is flocked (skipped on `StateLockedError`); with
     `force=True` the sentinel is dropped before capture **and**
     `capture_repo_deps(replace=True)` wholesale-replaces the persisted deps
     (vs. the default never-clobber union). Exits 1 if no runs directory or no
     finished run found; per-run errors are logged and skipped (non-fatal for
     multi-run consolidation).
   - **`_backstop_capture_prior_runs(leerie_root, repo_root, caps, models,
     efforts)`**: called at run-start (in `_run_phases`, before
     `phase_classify`) to cover SIGKILL / crash cases where the cancel arm
     could not fire. Scans `leerie_root/runs/` for run dirs that have `logs/`
     but no `dep_capture.done` sentinel and calls `capture_repo_deps` over
     each via a lightweight ad-hoc state object.

**Idempotency sentinel.** `capture_repo_deps` writes `<run_dir>/dep_capture.done`
(a one-line file) and sets `st.data["dep_capture_done"] = True` after a
successful write. The run-start backstop skips runs whose sentinel file is
present. The `dep_capture_done` state field is defined in `STATE_FIELDS` and
documented in the state-schema table above.

#### Language-dep Dockerfile template (launcher, gated on `bake_language_deps`)

When the launcher auto-generates `.leerie/Dockerfile` from `setup_packages`
(see *Per-repo derived image* above) and `bake_language_deps` resolves to
`true` (default), the generated Dockerfile includes a language-dep layer
after the apt `RUN`:

```dockerfile
COPY <copy_inputs> ./
RUN <install command>
```

The `COPY`+`RUN` layer is emitted by a `python3` script the launcher writes
to a temp file (`cat >"$_dep_pyf" <<'PY'`) and runs as `python3 "$_dep_pyf"
"$USER_REPO" "$_leerie_config_toml"` — de-nested from a `"$(…)"` command
substitution so the block parses under bash 3.2. It has two tiers:

1. **Primary — persisted `language_installs` from `.leerie/config.toml`.**
   The `dep_capture` worker writes a `language_installs` JSON array (keyed
   by `manager`) to `.leerie/config.toml`. When this key is present, the
   launcher reads it, iterates over every `{manager, command, copy_inputs}`
   entry, and emits one `COPY`+`RUN` block per manager. Each `copy_input`
   is validated with `p.exists()` before being added to the `COPY` list —
   hallucinated paths are silently dropped while the `RUN` line is always
   emitted (the install command itself is authoritative; the COPY list is
   advisory). Multiple managers yield multiple `COPY`+`RUN` layers.

2. **Fallback — lockfile detection (clean first run).** When no
   `language_installs` key is present in config.toml (e.g. on the very
   first run before `dep_capture` has fired), the script mirrors
   `_lockfile_table_entries`'s manager-precedence by hand to detect a
   single lockfile manager. For **all** node ecosystems (pnpm, yarn, npm)
   a shared `_node_ancillary` helper adds workspace `package.json`s,
   `patches/`, `.npmrc`, and `pnpm-workspace.yaml`, because the frozen
   install requires them — workspace globs come from `pnpm-workspace.yaml`
   (pnpm) or `package.json`'s `workspaces` field (yarn/npm, both list and
   `{packages: [...]}` forms). On a build failure (e.g. a missing patch
   file), the launcher falls back to `bake_language_deps=false` (apt layer
   only) and logs loudly.

**COPY-input-sha rebuild trigger.** Every file that participates in the
`COPY` list — lockfiles, manifests, workspace children, `patches/*`,
`.npmrc` — has its `sha256` embedded in a `# copy-input-shas:` comment
line inside the generated Dockerfile, so the Dockerfile's own sha
(the single `.dockerfile-hash` site) folds them in. Any dependency-input
change — a lockfile bump *or* a patch edit that leaves the lockfile
untouched — triggers a full image rebuild; an unrelated source file
change does not. The Fly path inherits the same generated Dockerfile via
the seed-repo whitelist, so there is no second hash site.

When `bake_language_deps=false`, the auto-generated Dockerfile contains only
the apt layer (`USER root; apt-get install ...`), identical to the
pre-existing path, and ends with the image still at `USER root` — it does
**not** append a trailing `USER leerie`. The base image's ENTRYPOINT
(`scripts/container-entry.sh`) is inherited by the derived image and
**must** run as PID-1 root to set up cgroup containment and launch the
cgroup broker before dropping to leerie via `runuser` (DESIGN §6 *Memory
containment*; the base Dockerfile omits `USER leerie` for the same reason).
A trailing `USER leerie` here would override that — cgroup writes, the
broker socket bind, and `runuser` would then all fail EACCES and the
container would exit 1.

#### Config knobs

Two new config keys. Neither has a CLI flag. Their precedence differs by
resolver:
- `capture_deps` (orchestrator, `resolve_capture_deps`): env
  `LEERIE_CAPTURE_DEPS` > `.leerie/config.toml` > default. Does **not**
  consult `leerie.toml`.
- `bake_language_deps` (launcher): env `LEERIE_BAKE_LANGUAGE_DEPS` >
  `leerie.toml` > `.leerie/config.toml` > default.

| Key | Env override | Default | Meaning |
|-----|-------------|---------|---------|
| `capture_deps` | `LEERIE_CAPTURE_DEPS` | `true` | Enable finalize-time dependency capture. Set to `false` to disable entirely. |
| `bake_language_deps` | `LEERIE_BAKE_LANGUAGE_DEPS` | `true` | Include language-dep `COPY`+`RUN` layer in auto-generated Dockerfile. Set to `false` for apt-only bake. |

Both keys are resolved by their respective `resolve_*` functions before
the finalize hook fires (`capture_deps` by `resolve_capture_deps` in the
orchestrator; `bake_language_deps` by the launcher's own resolver). They
are not emitted into `leerie config --init` output — that heredoc
documents only `build`/`lint`/`test`/`setup_packages` — but they are
documented in the `CLAUDE.md` quick-start and here.

Conforms to DESIGN §6½ *Auto-capture of repo dependencies*.

---

## 7. Git worktree mechanics (`scripts/*.sh`)

Every script takes a `RUN_ID` as its first positional argument (after any flags) so the per-run namespacing is explicit at the shell boundary, not implicit through `cwd`.

| Script | Behavior |
|--------|----------|
| `setup-run.sh <run-id>` | Creates `leerie/runs/<run-id>` **only if absent** — never force-resets it (an existing branch carries completed waves; resetting it would destroy resume state). Records the working branch (HEAD-at-run-start) to `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/working-branch` on first run only. Adds the run-branch worktree at `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/staging` if missing. Canonicalizes the staging path to absolute (`pwd -P`) before comparing against `git worktree list --porcelain` output (which always uses absolute, symlink-resolved paths); reclaims an unregistered-but-present staging directory (`rm -rf`) and calls `prune_leerie_worktrees` (scoped; see above), so a directory left behind by a SIGKILLed run (Fly `machine stop` — `_cleanup_on_abnormal_exit` never runs) does not make `git worktree add` fail with `fatal: '<path>' already exists`. Safe on `resume`. |
| `new-worktree.sh <id> <run-id>` | Creates `leerie/subtasks/<run-id>/<id>` worktree at `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/<id>` branched off the current `leerie/runs/<run-id>` tip. Canonicalizes the worktree path to absolute (`pwd -P`) before comparing against `git worktree list --porcelain` output; reuses an existing worktree/branch if present (resume after handoff). Prints the absolute worktree path. The run-branch (`leerie/runs/…`) and subtask-branch (`leerie/subtasks/…`) prefixes are deliberately disjoint so neither is an ancestor ref of the other. The `prune` → orphan-check → `rm -rf` → `add` sequence is wrapped in a `flock` on `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/.worktree.lock`, scoped to the run directory, since each step is check-then-act against git's shared worktree admin metadata and under `--max-parallel` a dozen siblings run this script concurrently. |
| `integrate.sh <id> <run-id>` | From repo root, inside the run-branch worktree (`${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/staging`): `git merge --no-ff leerie/subtasks/<run-id>/<id>`. Exit 0 clean; exit 1 on conflict, leaving the worktree mid-merge for an integrator; exit 2 on precondition failure (run-branch worktree or subtask branch missing) — `integrate_wave` treats exit 2 as fatal via `die()` and does *not* spawn an integrator, since the worktree-less case would fail in confusing ways. |
| `finalize.sh <run-id>` | Run-branch verifier. Exits 0 if `refs/heads/leerie/runs/<run-id>` exists and contains at least one commit beyond the working branch; exits non-zero with a diagnosis otherwise. The working branch is **never** modified — leerie does not merge into it locally; the PR is the proposed integration. The push and PR step lives in the **host launcher** (`leerie` bash script), not in the container — it runs after `nerdctl run` exits cleanly, using the host's own `git push` + `gh pr create` against the host's auth state. See "Host-side finalize" below. |
| `cleanup.sh [--run-id <id> \| --all-runs] [--branches \| --subtask-branches]` | Default (no flag): scans `<state-root>/runs/*/state.json` for the most-recently-failed run (most recent without `finished_at`), confirms y/N, then removes only that run's worktrees + prunes git metadata. State dir stays as audit. `--run-id <id>` is an explicit single-run cleanup (worktrees only). `--all-runs` runs the same per-run cleanup across every run dir under `<state-root>/runs/`. `--branches` (combinable with `--run-id` or `--all-runs`) additionally deletes the matching run branches *and* subtask branches (`leerie/runs/<id>` and `leerie/subtasks/<id>/*`). `--subtask-branches` deletes only the subtask branches and keeps `leerie/runs/<id>` (the post-finalize default — the run branch is the PR head and must outlive the orchestrator). Without either flag, all branches are kept as an audit trail. State dirs are always preserved by `cleanup.sh`. Ctrl-C and every other abnormal exit in the orchestrator also preserve state — they call `_cleanup_on_abnormal_exit(full_purge=False)`. There is no `full_purge=True` call site today; the flag is retained as a future hook for an explicit-purge gesture, but no current code path uses it. |

A run branch `leerie/runs/<run-id>` is never reset once created — this is the invariant `resume` depends on. See `DESIGN.md` §6 ("the run branch is the resume contract").

### Host-side finalize (bash + jq in the `leerie` launcher)

The push + PR step runs on the **host** in the launcher, after `nerdctl
run` exits cleanly. The container's `phase_finalize` writes
`finished_at` to `run.json` and exits 0; the launcher polls that
sentinel and proceeds. See DESIGN.md §6 *Finalization* for the
architecture (auth state lives in host processes the container can't
reach; the boundary is structural).

The launcher's finalize block in `leerie` (bash) does, in order:

1. **Skip if `--no-push`.**
2. **Read run state** via `jq` from `$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json` and
   `state.json` (run branch, working branch, finished_at).
3. **Push the run branch.** `git push -u origin leerie/runs/<run-id>`
   (with `--no-verify` if the flag was set). On failure: print a
   multi-line message (run branch + working branch, captured push
   output — stderr plus any pre-push hook stdout — and the exact retry
   command), update `run.json` with `push_error`, exit non-zero.
4. **Compose PR title + body.** Primary path: read `pr_title` /
   `pr_body` from `run.json` — written by the `pr_writer` worker that
   `phase_finalize` invokes when `push_will_happen` is true (see
   DESIGN §6 *Finalization* and §9 *Structured-output schemas*
   `pr_writer` entry). Fallback path (pr_writer skipped or crashed): a
   bash heredoc reads `state.json` fields with `jq` and emits the
   deterministic body shape `compose_pr_body` produces (task, category,
   source-of-truth, run timestamps, wave + subtask + worker counts, and
   — when `external_preconditions` is non-empty — a `⚠ Deploy-ordering`
   section rendered via `jq`, byte-identical to the Python renderer;
   see "Deploy-ordering notes"). The launcher branches on whether
   `pr_title_llm` / `pr_body_llm` are non-empty.
5. **Open PR.** Before calling `gh pr create`, validate that
   `working_branch` still exists on origin via `git ls-remote
   --exit-code --heads`. If deleted (a stacked run's parent squash-
   merged mid-flight), fall back to the repo's default branch (`git
   remote show origin | sed 's/.*HEAD branch: //'`). Then:
   `gh pr create --base <working-branch> --head
   leerie/runs/<run-id> --title "leerie: <pr_title>" --body-file -`
   with the composed body piped on stdin. On failure: log a warning
   with the pushed-branch URL and a retry command (using the resolved
   base); update `run.json` with `pr_error`. **Non-fatal** — exit 0.

**Local runtime only.** The inline finalize block above runs only when
`LEERIE_RUNTIME != "fly"`. On Fly the run dir is not yet on the host
when this block would otherwise execute (it's on the Fly Machine and
gets streamed back inside the EXIT trap `decide_teardown` that fires
*later*). The Fly path runs the same `host_finalize` function from a
different call site — see *Remote execution mode* below.

**Preflight (`leerie` bash, before `nerdctl run`):** the launcher
checks `git rev-parse --is-inside-work-tree`, `shutil.which gh`,
`gh auth status`, and `git remote get-url origin` before spinning up
the container, with actionable messages plus the `--no-push` escape
hatch. The orchestrator no longer runs these checks; they moved to the
host where the auth state actually lives.

`--no-push` skips the entire push + PR step. CLI flag, `LEERIE_NO_PUSH`
env, `no_push = true` in `leerie.toml`. **Both the launcher (bash) and
the in-container orchestrator (Python) resolve `no_push` from all three
sources** so they agree on intent: the orchestrator's
`resolve_no_push()` and the launcher's inline TOML fallback (mirroring
`_read_toml_key`'s flat grep — no `tomllib` dependency, since the
launcher runs on the user's host where Python 3.9 is still common)
both check CLI → env → TOML. Disagreement on a TOML-only opt-out would
make the Fly auto-finalize path push against user intent (the launcher
seeds `fly-machine.json.host_no_push` and the `--host-no-push` argv;
the orchestrator gates `pr_writer` and writes `run.json.no_push`).
`--no-verify` is CLI-only and only affects the push step (worker
`git commit`s inside worktrees still run all hooks).

### Remote execution mode

`--runtime fly` (or `LEERIE_RUNTIME=fly` / `leerie.toml runtime=fly`) routes
execution to Fly.io Machines instead of the local `nerdctl run`. The
Colima/containerd preflight block is gated on `RUNTIME=local` and skipped
entirely when `RUNTIME=fly`. `--runtime` flows through `REWRITTEN_ARGS`
to the orchestrator's argparse. The launcher's bash-side resolution block
also accepts `ec2` so `--runtime ec2` is not rejected before a
container/instance starts; EC2 provisioning itself, and the
orchestrator-side argparse enum, are out of this launcher knob's scope.

Resolution order (highest priority first):

1. **`--runtime local|fly|ec2`** CLI flag. Passed through to the
   orchestrator so both the launcher and the orchestrator share the same
   resolved value.
2. **`LEERIE_RUNTIME`** environment variable, values `local` | `fly` | `ec2`.
3. **`leerie.toml`** at the repo root, `runtime = local|fly|ec2`.
4. **Default `local`** — local `nerdctl run` is used when unset.

Invalid values in env or TOML are rejected immediately with an error
message and exit 1 before any preflight runs.

**Runtime auto-detection on run-id-bearing verbs:** the shared
`_auto_detect_run_runtime(run_id, explicit_runtime)` helper checks
`$LEERIE_STATE_HOST_DIR/runs/$run_id/` for `fly-machine.json` (Fly) then
`ec2-instance.json` (EC2 — the sidecar `ec2-provision.sh`'s
`provision_instance()` writes unconditionally on a successful create). If
`explicit_runtime` is empty and either sidecar is present, it echoes the
detected runtime (`"fly"` or `"ec2"`) to stdout and returns 0; the caller
promotes its local runtime variable to that value. `_auto_detect_fly_runtime`
remains as a thin Fly-only wrapper (returns 0 only when the detected runtime
is `"fly"`) for call sites not yet migrated to EC2 handling.
Applied to `stop`, `kill`, `accept-blocked`, and `finalize` (all four
now accept `ec2` in their `--runtime` enum validation, alongside `local` and
`fly`), and to `resume` (which also appends `--runtime fly` to
`REWRITTEN_ARGS` and tracks the `_RUNTIME_EXPLICIT` flag). When the user
explicitly passes `--runtime local` on a Fly-originated run, `resume` warns
but respects the choice; the fast-path verbs reject it as before.

Every verb wires a real EC2 *action*: `kill`, `stop`, `accept-blocked`,
`list` (see the rows above), and `resume` / `finalize`. Detecting or
being explicitly passed `--runtime ec2` routes to the EC2 path rather
than falling through to the Fly path (which would misdirect an EC2
instance id to `flyctl`) or defaulting to `local` (which would
mislabel the run).

`resume` promotes `RUNTIME=ec2` exactly as it promotes `RUNTIME=fly` two
lines above, so the `RUNTIME=ec2` dispatch branch's sidecar →
`resume_instance()` path is reachable from a real `resume` invocation
(`grep -c "not yet wired" leerie` is 0). `tests/test_ec2_launcher_resume.py`
drives the real `leerie` binary to assert the promotion fires.

**`kill`'s EC2 action.** Resolves `ec2_instance_id` from the run dir's
`ec2-instance.json` (preferred) or `run.json` via the new
`_resolve_ec2_instance_id_from_run_dir` helper (the EC2 counterpart to
`_resolve_volume_id_from_run_dir` — unlike Fly, the run-id is NOT the
instance id for EC2 runs, so it must always be read from a sidecar). Dies
with "no ec2_instance_id found ..." if neither sidecar carries one. Prompts
for confirmation (bypassed by `--force`, same convention as the Fly/local
paths) before sourcing `aws-credentials.sh` + `ec2-lib.sh` +
`ec2-provision.sh` + `ec2-resume-instance.sh`, resolving credentials via
`resolve_aws_credentials` (same precedence and `--profile`/`--region`
passthrough as the `RUNTIME=ec2` fresh-provision branch), and gating on
`require_aws`. Sets `LEERIE_EC2_INSTANCE_ID` and re-resolves
`LEERIE_EC2_SSH_TARGET` from the instance's current `PublicIpAddress` via
`ec2-resume-instance.sh`'s `_resolve_ssh_target_from_instance` (EC2 hands
out a new public IP on every stop/start cycle). Then calls
`_try_fetch_state_for_ec2_teardown` — the same hook
`decide_ec2_teardown`'s clean-exit branch uses, reused rather than
duplicated — to sync the run branch and state dir back to the host BEFORE
calling `terminate_instance()`: the one-way-ratchet invariant
(`ec2-provision.sh:262-272`) that destroy-then-fetch would make paid-for
LLM work unrecoverable. A failed sync leaves the instance running and dies
with a "LEFT RUNNING" message pointing at a retry (`leerie kill <id>
--force`) rather than escalating to termination. On success, writes
`killed_at` + `ec2_instance_id` onto the run sidecar (bootstrapping
`run.json` from `ec2-instance.json` first via the widened `_ensure_run_json`
helper, which also handles an EC2-only run dir the same way it already
handles a Fly-only one). `flyctl` is never invoked on this path.

When `RUNTIME=fly`, the launcher skips the per-OS nerdctl preflight, the
image-build check, the auth/cache mount assembly, and the `nerdctl run`
invocation, and instead calls the remote dispatch path via
`scripts/remote/provision.sh`.

#### Machine lifecycle (`scripts/remote/provision.sh`)

The provision script is **sourced** (not exec'd) by the launcher so the
machine ID and destroy trap live in the launcher's process. It provides
four functions:

- **`provision_machine()`** — creates a Fly Machine from `$FLY_IMAGE_TAG`
  (set by the launcher; see below), polls `flyctl machine status` until the
  machine reaches state `started`, and registers `decide_teardown` as an
  EXIT/INT/TERM trap. Exports `$LEERIE_MACHINE_ID`. Returns 0 on success;
  destroys the machine and returns 1 on failure. Writes `fly_machine_id`
  and `image_tag` (from `$FLY_IMAGE_TAG`) to the run sidecar
  (`$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json`) when `$LEERIE_RUN_ID`
  is set — written immediately after provision succeeds so a launcher
  crash before classification still leaves a recoverable pointer. The
  `image_tag` field lets `resume_machine()` detect version drift on
  `resume` and update the machine's image before starting it.
- **`stop_machine()`** — runs `flyctl machine stop $LEERIE_MACHINE_ID
  --app $FLY_APP`, tolerant of already-stopped machines. Preserves the
  machine's filesystem on its Fly volume so `resume-machine.sh` can wake
  it later.
- **`destroy_machine()`** — runs `flyctl machine destroy $LEERIE_MACHINE_ID
  --app $FLY_APP --force`, with a stop-then-destroy fallback for machines
  already in a terminal state.
- **`decide_teardown()`** — the trap entry point. Classifies
  `$LEERIE_REMOTE_EXIT_RC` (set by the launcher just before exit) and
  dispatches one of three ways:
  - `destroy_machine` for genuine terminal exits (rc=0, EXIT_NEEDS_ANSWERS=10,
    EX_TEMPFAIL=75): the orchestrator exited cleanly and the machine has no
    further value.
  - **Detach** for rc=130/143 (host-side SIGINT/SIGTERM): the user pressed
    Ctrl-C or the local stream broke (laptop closed, WiFi dropped). Since
    the orchestrator on the machine was started detached (Python
    `subprocess.Popen(start_new_session=True, user="leerie", ...)`, see
    *Worker auth + config seeding* below), it is still running. The function
    leaves the machine alone, prints a one-line "detached" banner with the
    reattach / pause / kill commands, and returns.
  - `stop_machine` for unknown non-zero failures (worker error, orchestrator
    exception): preserves the machine's filesystem on its Fly volume so the
    user can attach to inspect and then `leerie resume`. Writes `paused_at`
    and `pause_reason` to the run sidecar.

  Idempotent (the trap fires on every exit, including success).

The classification table is the canonical authority on which exit
codes are treated as which disposition; DESIGN §6 *Detached orchestrator
(remote mode)* and *Remote pause-on-failure (Fly.io)* document the
rationale.

Environment variables consumed by `provision.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `LEERIE_FLY_APP` | — (required) | Fly.io app name. Fly app names are globally unique; set via `--fly-app` or env. |
| `FLY_IMAGE_TAG` | `registry.fly.io/<app>:<version>` | Full image tag to launch (set by the launcher) |
| `FLY_REGION` | `iad` | Fly.io region |
| `FLY_VM_CPUS` | `4` | vCPU count for the machine. Setting >8 auto-promotes to Fly's `performance` CPU class (much more expensive — ~14x per CPU-second). |
| `FLY_VM_MEMORY` | `8192` | Memory in MB for the machine. Setting >16384 auto-promotes to Fly's `performance` CPU class. |
| `FLY_VM_DISK_GB` | `8` (on Fly runtime) | Per-machine Fly volume size in GB, mounted at `/work` — the path where the seeded repo, `.leerie/runs/<id>/` state, and per-subtask worktrees all live (and grow). The launcher defaults to `8` when `RUNTIME=fly` and no explicit value is given (CLI / env / toml). The volume is destroyed when the machine is destroyed (clean exit or `kill`). Override to a larger value for runs that hit ENOSPC (the rootfs is hard-capped at 2,000 IOPS / 8 MiB/s — N-wide worktree fans-out hit both the IOPS ceiling and the size cap fast). `/home/leerie` (caches + the `.claude` auth bundle) stays on the rootfs — `seed_auth` runs unconditionally on every resume, so the auth bundle is refreshed from host `$STAGE` regardless of pause length. |
| `LEERIE_MACHINE_START_TIMEOUT` | `120` | Seconds to wait for `state=started` |

`FLY_IMAGE_TAG` is resolved by the launcher (`resolve_fly_image_tag()`)
using `$LEERIE_FLY_APP` and `$LEERIE_VERSION`, or overridden by setting
`LEERIE_FLY_IMAGE` in the environment.

`provision_machine` requires `flyctl` on `PATH` and `flyctl auth status`
to succeed. The launcher's `RUNTIME=fly` preflight calls `require_flyctl`
from `scripts/remote/lib.sh` *before* sourcing any other remote script —
that helper detects missing `flyctl` and prompts for `brew install
flyctl` (macOS) or the Fly install script (Linux), then prompts for
`flyctl auth login` if unauthenticated. The auto-install mirrors the
local-runtime auto-install in `scripts/install.sh:200-208` and respects
`--no-runtime-install` / `LEERIE_NO_RUNTIME_INSTALL=1` (falls back to
hint-and-exit-1). By the time `provision_machine` runs, `flyctl` is
guaranteed to be on PATH and authenticated.

#### Private ssh-agent isolation (`_leerie_fly_agent_ensure`)

Before any `flyctl ssh ...` call can run, the launcher's `RUNTIME=fly`
branch invokes `_leerie_fly_agent_ensure` (in `scripts/remote/lib.sh`)
to spawn — or reuse — a leerie-owned ssh-agent at
`${XDG_CACHE_HOME:-$HOME/.cache}/leerie/agent/ssh-agent.sock` and
export `SSH_AUTH_SOCK` to point at it for the rest of the process
tree. The user's main ssh-agent is never touched.

This isolation matters because `flyctl ssh issue --agent` is
**additive** — it appends a fresh 24h cert to the agent and never
deletes prior certs. With multiple `require_fly_ssh` callers per
leerie run (seed-auth + two seed-repo paths), aiming flyctl at the
user's main agent accumulates dozens of certs, which OpenSSH then
offers to every ssh destination (including `github.com`); after
~5 failed auth attempts per connection GitHub rate-limits the account.
Containing all Fly certs in a private agent reachable only by leerie's
process tree eliminates the failure mode.

The private agent is persistent (lazy-spawned, never auto-killed) so
the 24h cert is reused across leerie runs — re-issuing on every
invocation was what produced the original accumulation. Reboot wipes
the socket inode; the next run lazy-spawns fresh. Parallel leerie
invocations serialize on `~/.cache/leerie/agent/.spawn.lock` via
`mkdir`-as-mutex (portable across darwin/linux without the non-stdlib
`flock` binary macOS lacks); only the first spawn wins, the rest see a
live socket and reuse it.

The reuse check probes the socket with `ssh-add -l` and reuses it on any
exit code **other than 2**: rc 0 (has keys) and rc 1 (reachable, no keys
yet) both mean the agent is alive; only rc 2 (cannot connect) means the
socket is stale. Treating rc 1 like rc 2 would unlink a live agent's
socket out from under the still-running process.

Any newly-spawned agent carries `-t 24h`, matching the 24h Fly cert
(`flyctl ssh issue --agent`) — this bounds only the lifetime of
**identities** added to the agent (`man ssh-agent`), not the agent
process itself (killing it is the separate `ssh-agent -k`). An
orphaned agent leaks indefinitely holding an empty keyring; there is
no reaper for it today.

#### Worker auth + config seeding (`scripts/remote/seed-auth.sh`)

After `provision_machine()` returns successfully, the launcher sources
`scripts/remote/seed-auth.sh` and calls `seed_auth()`. This is the remote
equivalent of the `AUTH_MOUNTS` bind-mount block (launcher lines ~542–726):
instead of mounting `$STAGE` as container volumes, the same content is
delivered via `flyctl ssh console -C` tar-pipe + small shell-command
invocations. (`flyctl machine exec` is NOT used — current flyctl removed
both `--stdin` on `machine exec` and the post-`--` argv form; `ssh
console -C` is the only flyctl transport that takes the remote command
as a single string AND forwards host stdin.)

`seed_auth()` performs six steps:

1. **Hallpass readiness probe.** Calls `require_fly_ssh` (ensures the
   leerie-private ssh-agent — see above — holds a valid Fly cert,
   issuing only if none exists) and `wait_for_fly_ssh_ready` (polls
   `flyctl ssh console --pty=false -C true` until success; hallpass
   takes 5-30 s to come up after `flyctl machine start` reports
   "started"). This is the *only* hallpass probe in a run — subsequent
   transports (`seed_repo_clone` parent + submodule bundles,
   `seed_repo_dirty` rsync) rely on each pipe's own
   `LEERIE_SEED_TIMEOUT_S` wrapper (rc 124/137) as the authoritative
   failure detector; an extra probe before each pipe would only
   manufacture false-positives once the channel is warm. Bound: ~175 s
   total (12 attempts × 10 s per-probe timeout + 11 × 5 s sleep); on
   success emits `remote: hallpass ready on <machine>`; on the rare
   exit-137 exhaustion (timeout's SIGKILL or an external SIGKILL like
   macOS Jetsam under host pressure), the warning includes a "killed
   externally" diagnostic so the operator can distinguish client-side
   pressure from a real Fly outage.

2. **Tar-pipe delivery of `$STAGE` to /home/leerie.** `tar -czC $STAGE`
   (gzip-compressed; excluding `.gitconfig`, `.gitconfig.local`,
   `.gitignore`, `.gitignore_global`, `.git-credentials`, `.netrc`,
   `.ssh`, `.gnupg`, `.config`; with `COPYFILE_DISABLE=1` host-side to
   silence macOS BSD tar's per-file
   `LIBARCHIVE.xattr.com.apple.provenance` warnings on the remote GNU
   tar) is piped to `flyctl ssh console --pty=false -C "sh -c
   'tar -xzC /home/leerie && chown -R leerie: /home/leerie'"`. The
   `chown -R leerie:` is necessary because the ssh-console session
   lands as root with default umask; without it the orchestrator
   (running as leerie) couldn't read its own credentials. The
   `leerie:` (trailing colon, no group name) uses leerie's numeric
   primary group rather than a hard-coded group name — leerie's
   primary GID is `HOST_GID` (defaults to 20 / staff on macOS) and the
   group is not necessarily called `leerie`.

   The launcher's `$STAGE` build skips `.claude/local` (the host npm
   install of `@anthropic-ai/claude-code` — the image installs claude
   globally via the Dockerfile, so shipping the host's local install
   is dead weight) plus `.claude/plugins/cache/` and
   `.claude/plugins/marketplaces/` (rebuilt on the remote in step 6
   from the small JSON metadata files that ride along). This keeps the
   stage well under the size where the `ssh console -C` stdin pipe
   starts hitting EOFs.

   On transient "tunnel unavailable" failure from a freshly-spawned
   flyctl agent, the seed retries once after `flyctl agent restart`.

3. **Token fallback.** If `$STAGE/.claude/.credentials.json` was not
   written (Linux, or macOS Keychain extraction failure) but
   `$CLAUDE_CODE_OAUTH_TOKEN` is set, `seed_auth()` writes a
   credentials JSON
   `{"claudeAiOauth":{"accessToken":"<token>","scopes":["user:inference"]}}`
   (the `scopes` field is mandatory — CLI 2.1.210's file-auth rejects a
   scope-less blob; see the `_extract_claude_credentials_json` row
   above) directly to `/home/leerie/.claude/.credentials.json` via
   `flyctl ssh console -C "sh -c 'cat > .../credentials.json
   && chmod 600 ... && chown leerie: ...'"`. If neither source is
   available, `seed_auth()` returns 1 with an actionable error.

4. **Git identity.** Reads `user.name` and `user.email` from the
   host's git config and writes them to `/home/leerie/.gitconfig` on
   the machine via `flyctl ssh console -C "sh -c 'IFS= read -r n;
   IFS= read -r e; git config --file /home/leerie/.gitconfig user.name
   \"\$n\" && git config --file /home/leerie/.gitconfig user.email
   \"\$e\" && chown leerie: /home/leerie/.gitconfig'"` with the two
   values piped on stdin — NOT `git config --global`, which under the
   ssh-console session's default root user would write to
   `/root/.gitconfig` where leerie can't read it. Worker commits carry
   the host user's identity.

5. **Pre-warm `claude --version`** once as the leerie user via
   `flyctl ssh console -C "su leerie -c 'HOME=/home/leerie PATH=... claude
   --version'"`. The first `claude --version` on a freshly-booted Fly
   machine takes ~17 s (Node runtime + statsig client cold-start);
   subsequent calls return in <0.2 s. Paying this upfront means the
   orchestrator's preflight `_check_claude_cli_version` call hits warm
   caches.

6. **Rebuild plugin cache.** The tar pipe excludes `plugins/cache/`
   and `plugins/marketplaces/` (step 2); the small JSON metadata files
   (`installed_plugins.json`, `known_marketplaces.json`) ride along
   and are the source of truth for rebuilding. Inside one
   `flyctl ssh console` invocation (running as the leerie user via
   `runuser -u leerie -- env HOME=... PATH=... sh -s` — not
   `su -c 'sh -s'`, which has implementation-specific stdin-forwarding
   under util-linux) a shell heredoc runs two phases: (a) read
   `known_marketplaces.json` with a python3 one-liner (jq isn't in the
   image), emit each `source.repo`, and run `claude plugin marketplace
   add <owner>/<repo>`; (b) read `installed_plugins.json` keys (e.g.
   `vercel@claude-plugins-official`) and run `claude plugin install`
   per entry. Output is appended to
   `/home/leerie/.cache/leerie/plugin-install.log`. Per-plugin
   failures are logged (`WARN: <spec> install failed (continuing)`)
   but non-fatal — a missing plugin only matters if a user-supplied
   task explicitly invokes it, in which case the Claude CLI's existing
   "plugin not found in cache" skip-with-warning behavior applies. The
   invocation is bracketed with the same `$(_seed_timeout_prefix)` +
   `_seed_progress_bg "plugin_rebuild"` heartbeat the main tar pipe
   uses (step 2), so a stalled `flyctl ssh console` produces a clean rc
   124/137 instead of hanging, and the user sees `plugin_rebuild: still
   streaming (Ns elapsed)` lines on the happy path. The rc is captured
   via `|| _rebuild_rc=$?` (grabs the rc and suppresses file-level
   `set -e`); the trailing `remote_log` line branches on rc —
   `complete` on 0, "timed out after Ns" on 124/137, "rc=N —
   continuing" otherwise. Replaces shipping ~200 MB of plugin contents
   over the WireGuard pipe with ~30–90 s of public-egress git-clone +
   bun-install on the Fly machine.

Git-push auth (SSH keys, `.netrc`, `~/.config/gh`) is **not** seeded — that
auth lives on the host per DESIGN §6 *Finalization* and is not needed inside
the remote machine for `claude -p` worker authentication or `git commit`.
The host pushes the run branch via `leerie finalize` after `fetch_branch`
streams the branch + state directory back; the machine never sees a
GitHub credential.

After seeding completes the launcher starts the orchestrator inside the
machine **detached** — see DESIGN §6 *Detached orchestrator (remote
mode)* for the rationale. The launcher generates the run-id host-side
(slug + suffix, same pattern as today's orchestrator-side generator)
and passes it explicitly via `--run-id <id>`, so it knows the
`orchestrator.log` path before the orchestrator has produced any output.
The detach is done by piping a Python wrapper script via stdin to
`flyctl ssh console -C "python3 -"`:

```bash
# Wrapper script built host-side with the argv JSON literal embedded
# (so no remote shell quoting touches the orchestrator argv).
_launch_argv_json="$(python3 -c '
import json, sys
print(json.dumps(sys.argv[1:]))
' "$LEERIE_RUN_ID" "${REWRITTEN_ARGS[@]}")"
_launch_script="$(cat <<PY
import fcntl, os, pwd, subprocess, sys, time
argv = ${_launch_argv_json}
run_id = argv[0]
orch_args = argv[1:]
run_dir = "/work/.leerie/runs/" + run_id
os.makedirs(run_dir, exist_ok=True)
leerie_pw = pwd.getpwnam("leerie")
# os.makedirs above created these as root; chown so the orchestrator
# (running as leerie) can write state files later.
for d in ("/work/.leerie", "/work/.leerie/runs", run_dir):
    try: os.chown(d, leerie_pw.pw_uid, leerie_pw.pw_gid)
    except OSError: pass
child_env = dict(os.environ)
child_env["HOME"] = "/home/leerie"   # ssh-console default is /root
child_env["USER"] = "leerie"
child_env["LOGNAME"] = "leerie"
# Host-side $(basename "$USER_REPO") expansion (heredoc is unquoted) —
# keeps orchestrator log() prefix consistent with host-side remote_log().
child_env["USER_REPO"] = "$(basename "$USER_REPO")"
# Host IANA TZ so in-machine log() offsets match host-side remote_log();
# _host_tz computed via `readlink /etc/localtime | sed 's|.*/zoneinfo/||'`.
# Empty value -> Python astimezone() falls back to UTC.
child_env["TZ"] = ${_host_tz_json}
# Bedrock activation. Every value is JSON-encoded host-side (not a raw
# quoted-var substitution — a bearer token could contain a quote/backslash
# that breaks out of the Python string literal). Bearer-token block takes
# precedence over the SSO/profile block. NOTE: heredoc is unquoted (<<PY) —
# never put a backtick pair in a comment here (command-substitution delimiter).
if "${_BEDROCK_BEARER_ACTIVE}" == "true":
    child_env["AWS_BEARER_TOKEN_BEDROCK"] = ${_bedrock_bearer_token_json}
    child_env["CLAUDE_CODE_USE_BEDROCK"] = ${_bedrock_use_bedrock_json}
    if ${_bedrock_bearer_region_json}:
        child_env["AWS_REGION"] = ${_bedrock_bearer_region_json}
elif "${_BEDROCK_ACTIVE}" == "true":
    child_env["CLAUDE_CODE_USE_BEDROCK"] = "1"
    if ${_bedrock_profile_json}:
        child_env["AWS_PROFILE"] = ${_bedrock_profile_json}
    if ${_bedrock_region_json}:
        child_env["AWS_REGION"] = ${_bedrock_region_json}
extra_path = "/usr/local/share/mise/installs/node/lts-current/bin"
if extra_path not in child_env.get("PATH", ""):
    child_env["PATH"] = extra_path + ":" + child_env.get("PATH", "")
log_path = run_dir + "/orchestrator.log"
pid_path = run_dir + "/orchestrator.pid"
with open(log_path, "ab") as log_f:
    p = subprocess.Popen(
        ["python3", "/opt/leerie-image/orchestrator/leerie.py",
         "--no-push", *orch_args],   # --host-no-push is in orch_args
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=log_f,
        start_new_session=True,    # bash setsid equivalent; portable
        cwd="/work",                # avoid stale-cwd ENOENT cascades
        user="leerie", group=leerie_pw.pw_gid,
        env=child_env,
    )
# Poll briefly before recording the pid. If this Popen lost the
# State.__init__ flock race against an already-running orchestrator for
# this run (DESIGN §6 *Single owner per run dir*), the child exits 75;
# writing its pid before the race resolves would overwrite the winner's
# pid with a dead one (the stale-pid contagion in DESIGN §6). Budget 2 s
# (Popen -> flock attempt is ~300-500 ms normally, ~1 s under disk
# pressure); the reader-side /proc cross-check catches any residual case.
for _ in range(10):
    if p.poll() is not None:
        break
    time.sleep(0.2)
if p.poll() == 75:
    # Stillborn — winner still owns the run; leave the pid file alone.
    sys.exit(75)
with open(pid_path, "w") as pid_f:
    pid_f.write(str(p.pid) + "\n")
PY
)"
printf '%s' "$_launch_script" \
  | flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
      --pty=false -C "python3 -"

# Separately tail the orchestrator log via a second ssh-console session
# (its death — Ctrl-C, broken pipe, laptop disconnect — does NOT
# propagate to the orchestrator).
printf '%s' "$_tail_invocation" \
  | flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
      --pty=false -C "sh -s"
```

`--no-push` is always injected so the remote orchestrator's
`phase_finalize` does not attempt a push itself — the Fly Machine has
no GitHub auth and cannot push regardless of user intent. Push is the
host's responsibility, run inline by `decide_teardown` after
stream-back (see *Run branch stream-back* below) or — as a recovery
path — via `leerie finalize <run-id>` after reattach.

**Intent vs mechanism.** The orchestrator must distinguish "the user
launched with `--no-push`" (intent) from "I am running on a Fly
Machine and physically cannot push" (mechanism). Both arrive as flags
on the orchestrator's argv:

- `--no-push` — the mechanism flag the launcher always passes on Fly.
- `--host-no-push true|false` — the intent flag the launcher
  *additionally* appends on Fly. The value is the launcher's resolved
  `$NO_PUSH` at machine-creation time (host_no_push in
  `fly-machine.json`).

`phase_finalize` gates `pr_writer` and the value it writes to
`run.json.no_push` on `push_will_happen(no_push, host_no_push)`:

```python
def push_will_happen(no_push: bool, host_no_push: bool | None) -> bool:
    if host_no_push is None:            # local runtime — no Fly Machine
        return not no_push
    return not host_no_push             # Fly — intent wins over mechanism
```

Without this split, `pr_writer` would be silently skipped on every
Fly run (because the mechanism flag silences it) and the LLM-written
PR body would always be replaced by the deterministic fallback.

When the tail's ssh-console session ends (the orchestrator wrote its
final log line and exited, or the user pressed Ctrl-C, or the laptop
disconnected), the launcher's EXIT trap classifies the rc via
`decide_teardown` per the table above — **sync-then-destroy** for
clean terminal exits (rc=0/10/75: `_try_fetch_branch_for_teardown` runs
`fetch_branch` BEFORE `destroy_machine`; on sync failure leaves the
machine RUNNING for user recovery), **detach** for SIGINT/SIGTERM,
**pause** for other non-zero rc.

**Pre-classify resume — `resume` is host-only, task is recovered
from `task.txt`.** `leerie resume` on the host means "wake the paused
Fly machine"; the in-machine orchestrator interprets the same flag as
"resume state from disk." Since the run-id is the machine ID from the
start (DESIGN §6), the launcher always has a valid run-id at resume time.
If classify never ran (no `state.json` exists), the orchestrator's
`resume` branch needs a `task` positional, which is gone from the
user's resume argv. The launcher persists the user's original task
argument to `$LEERIE_STATE_HOST_DIR/runs/$LEERIE_RUN_ID/task.txt` on
first launch, and on pre-classify resume — when `LEERIE_TASK_ARG` is
empty in this invocation's argv — reads it back and appends to
`REWRITTEN_ARGS`. Both writes are idempotent (`! -f` and "no task in
argv" guards), so an explicit re-supplied task on the resume command
line wins. `task.txt` is launcher-side; the orchestrator never reads it.

The launcher's task extractor walks `$@` once at startup, skipping
the value of any `--flag` that takes one. The list of value-taking
flags (`_value_flags` literal in `leerie`) is source-coupled to the
orchestrator's argparse by `tests/test_launcher_value_flags_coupling.py`
— a value-taker added upstream that is not mirrored in the launcher
would silently misclassify its value as the task and persist the wrong
string. Per-worker `--model-<W>` / `--effort-<W>` overrides are matched
by prefix pattern (`--model-*` / `--effort-*`) rather than enumerated.

Maps to `DESIGN.md` §6 (container boundary / teardown / finalization)
and §6 *Remote execution* (the one-microVM-per-run model and the
host-as-the-only-credential-holder contract).

#### Repo seeding (`scripts/remote/seed-repo.sh`)

Two-phase bundle-then-rsync seeding: the host has the full repo, so
pack its committed state as a `git bundle` and pipe it to the
machine; the machine clones from the bundle on its local disk; the
host then rsync's the small dirty/untracked delta to fill in
uncommitted edits, untracked files, and forced-in `.claude/`. No
in-machine `git clone` from origin — Fly machines deliberately receive
no GitHub credentials (DESIGN §6 *Finalization*: the host pushes via
`leerie finalize`, not the machine).

**Phase 1: bundle clone (`seed_repo_clone`).**

1. **Parent repo bundle.** Host runs `git -C "$USER_REPO" bundle
   create - --all 2>/dev/null` and pipes the output stream straight to
   `flyctl ssh console --pty=false -C "sh -c 'cat > /tmp/leerie-seed.bundle'"`
   on the machine (`--all` packs every ref into one pack-format binary
   stream). The `sh -c '...'` wrapper is load-bearing — bare
   `-C "cat > /tmp/..."` is parsed by flyctl as if `>` were a `cat`
   argument and fails with `cat: invalid option -- 'c'`.

2. **Submodule bundles, recursive.** Host runs `git submodule --quiet
   foreach --recursive 'git bundle create - --all | flyctl ssh
   console -C "sh -c '\''cat > /tmp/leerie-subs/<flat-displaypath>.bundle'\''"'`
   so each submodule's pack data lands as its own file on the machine.
   The flat-displaypath name (`/` → `_`) gives unambiguous filenames for
   nested submodules.

3. **Machine-side clone + submodule update.** A single
   `flyctl ssh console -C "sh -c '<script>'"` call:
   - `git clone /tmp/leerie-seed.bundle /work` (treats the bundle file
     like a remote; recreates `.git/` and checks out HEAD).
   - For each submodule, `git config submodule.<name>.url
     /tmp/leerie-subs/<bn>.bundle` (sets the URL in `.git/config`, NOT
     `.gitmodules` — the committed file is never modified).
   - `git -c protocol.file.allow=always submodule update --recursive`
     (clones each submodule from its bundle file). The
     `protocol.file.allow=always` flag is load-bearing — git 2.38+
     blocks the `file` protocol by default per CVE-2022-39253, which
     would otherwise abort with `fatal: transport 'file' not allowed`.
   - `chown -R leerie: /work` (orchestrator runs as leerie).
   - `rm -rf /tmp/leerie-seed.bundle /tmp/leerie-subs` (bundles served
     their purpose; tmpfs space reclaimed).

Before the clone runs, `/work` is emptied via `find /work -mindepth 1
-maxdepth 1 -exec rm -rf {} +`. Note the `find ... -exec rm` form
preserves the `/work` inode itself — a naive `rm -rf /work && mkdir
-p /work` would replace the inode and leave any process holding a
prior fd (the ssh-console shell, the orchestrator about to be
spawned) with a stale cwd, producing `getcwd: ENOENT` cascades.

**Why bundles instead of tar:** macOS BSD `tar -c` normalizes filenames
NFC → NFD when archiving, which desyncs a macOS-built git index (NFC)
from the Linux-written tree (NFD) and shows unicode filenames as
untracked + missing on the machine. Bundles avoid this — they store
pack-format binary objects, so filenames materialize natively on the
receiving Linux git from raw tree-object bytes with no transport-layer
normalization step.

**Phase 2: dirty delta (`seed_repo_dirty`).** Same call path as
`re-seed.sh` (Phase 4 mid-run re-rsync) but called automatically by
`seed_repo` immediately after the bundle clone succeeds. Computes the
dirty set from `git status --porcelain` on the host:
- Modified-but-uncommitted tracked files
- Untracked-not-ignored files
- Defensive filter drops `.git/*`, non-whitelisted `.leerie/*` paths, and worktree paths
  (`.leerie/runs/*/worktrees/*`) before handing the list to rsync; exception: `.leerie/config.toml`,
  `.leerie/Dockerfile`, `.leerie/.leerie-setup.sh` pass through
- Forced-in `.claude/` (workers need it, often gitignored) —
  enumerated via `find .claude -type f` host-side and appended to
  the dirty list before the defensive filter

The dirty set is rsync'd over `flyctl ssh console -C "rsync --server
..."` via `fly_rsync_wrapper` (lib.sh). NFC byte preservation is
free with rsync; the bundle path doesn't need it (filenames don't
transit), but the delta does.

The script is **sourced** (not exec'd) by the launcher — same
pattern as `provision.sh` — so `seed_repo()` runs in the launcher's
process after `provision_machine()` exports `$LEERIE_MACHINE_ID`.

Environment variables consumed by `seed-repo.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `LEERIE_MACHINE_ID` | — | ID of the started Fly Machine (exported by `provision.sh`) |
| `LEERIE_FLY_APP` | — (required) | Fly.io app name. Fly app names are globally unique; set via `--fly-app` or env. |
| `USER_REPO` | — | Absolute path to the local git repo (set by launcher) |

Requires: `flyctl` on `PATH` (authenticated); `git`; `python3`; `rsync`.

#### Run branch stream-back (`scripts/remote/fetch-branch.sh`)

Stream-back path that makes the completed run available on the host
so the existing host-side finalize block can push it and open a PR.
Runs in two contexts:

- **Sync-before-destroy** (the load-bearing safety net):
  `decide_teardown`'s clean-exit branch sources `fetch-branch.sh`
  via `_try_fetch_branch_for_teardown` and runs `fetch_branch`
  BEFORE calling `destroy_machine`. On sync failure the machine is
  left RUNNING with `sync_failed_at` written to the sidecar.
- **`leerie finalize`** (user-driven recovery / re-attempt). The
  launcher's `finalize` handler also detects "already synced to
  host" state and short-circuits past `fetch_branch` entirely. Two
  flavors qualify: (a) **normal run** — `finished_at` set, state.json
  present, AND the run branch exists locally (auto-sync's `git
  bundle` step landed); (b) **no-work run (DESIGN §8)** —
  `finished_at` set, state.json present, `run.json.no_push=true`, and
  the run branch was NEVER materialized (so it cannot exist locally).
  In flavor (b), `host_finalize`'s `no_push` gate short-circuits
  the push cleanly; its rev-parse defense-in-depth guard backstops
  the case where `no_push` was lost upstream.

The mechanism is the same in both contexts:

1. **Discover the completed run** — scans `.leerie/runs/*/run.json`
   on the machine via a python -c snippet through
   `flyctl ssh console -C`. Picks the entry with `finished_at` set,
   no `pushed_at`, and the most recent mtime, then prints four lines
   on stdout: run_id, branch, working_branch, no_push.

   CRITICAL: stderr is captured to a separate tmpfile, NOT merged
   into stdout via `2>&1`. `flyctl ssh console` prints "Connecting
   to fdaa:..." to stderr; merging it would shift every parsed line
   by one, corrupting the discovered run_id and branch name.
   Downstream `git bundle create` would silently produce an empty
   bundle against a nonexistent branch.

   We do NOT use the `no_push` flag from `run.json` as a proxy for
   "no branch was materialized." `no_push=true` is a *mechanism*
   flag the launcher always forces on the in-Fly orchestrator (the
   machine can't push), not a "no branch" signal — the user's actual
   no-push intent lives in `fly-machine.json`'s `host_no_push`.

3. **Run branch via git bundle** — `git -C /work bundle create -
   leerie/runs/<run-id>` on the machine, piped to a host tempfile,
   then fetched via `git fetch <bundle> +<branch>:<branch>` into
   the host repo. Resolves cleanly because both repos share the
   same origin history.

4. **Run state directory** — tars `/work/.leerie/runs/<run-id>`
   on the machine and extracts it under `$LEERIE_STATE_HOST_DIR/runs/`
   on the host, so `run.json` and `state.json` end up present exactly
   as after a local run.

5. **Strip mechanism `no_push` from synced run.json — conditional
   on branch presence.** After the tar extracts, if a run branch was
   fetched in step 3 AND the host-side run.json has `no_push=true`,
   remove the field — defense against in-flight old-image runs that
   wrote the mechanism flag; the user's intent lives in
   `fly-machine.json.host_no_push` instead.

   When step 2's branch probe returned absent (the cleared-but-empty
   terminal-state case — DESIGN §8), the stripper is **skipped**.
   `_finish_no_work_run` deliberately writes `no_push=true` to
   `run.json` as **intent** ("nothing to push — no branch exists"),
   and `host_finalize`'s `no_push` gate reads that intent to
   short-circuit cleanly (the rev-parse guard backstops the same
   case). Stripping `no_push` here would disarm the gate.

The script is **sourced** (not exec'd) by the launcher and exports
`LEERIE_REMOTE_RUN_ID` on success (the discovered run-id, in case the caller
needs it for diagnostics).

Environment variables consumed by `fetch-branch.sh`:

| Variable | Default | Purpose |
|---|---|---|
| `LEERIE_MACHINE_ID` | — | ID of the started Fly Machine (exported by `provision.sh`) |
| `LEERIE_FLY_APP` | — (required) | Fly.io app name. Fly app names are globally unique; set via `--fly-app` or env. |
| `USER_REPO` | — | Absolute path to the local git repo (set by launcher) |

Exports: `LEERIE_REMOTE_RUN_ID` — the run-id of the completed run on the machine.

Requires: `flyctl` on `PATH` (authenticated); `git`; `tar`; `python3` (on the machine — always present in the leerie image).

Maps to `DESIGN.md`: §6 *Finalization* (remote-finalize stream-back variant).

#### Smart resume (`leerie resume`)

`leerie resume [<run-id>] [--shell] [--auto-finalize]
[--app <app>] [--runtime fly]` re-engages with a remote run regardless
of state. The launcher routes by observation:

| Machine state | Orchestrator state | Behavior |
|---|---|---|
| Stopped (paused) | n/a | Wake via `resume_machine` → re-seed → launch orchestrator → tail |
| Running | Dead | (Re-)seed if needed → launch orchestrator → tail |
| Running | Alive | Skip seed + launch → attach: tail orchestrator.log (default) or open bash shell (`--shell`) |

The "alive orchestrator" case is detected by a two-layered flock probe.
**Early probe (resume path only):** on the `_resumed=true` path, the
launcher runs a lightweight Python flock snippet via `flyctl ssh console`
immediately after `resume_machine` — before `seed_auth`. rc=75 (lock
held) skips `seed_auth`/`re_seed`/launch entirely, pivoting straight to
`_attach_to_live_orchestrator` (lib.sh) — SSH readiness is not a concern
since an alive orchestrator means the machine was never stopped and
hallpass is already warm; any other rc falls through to `seed_auth`.
**Launch-time probe (belt-and-suspenders):** the in-machine Python
launch wrapper takes its own fast-path flock probe (DESIGN §6 *Single
owner per run dir*) and exits 75 when the lock is held, covering fresh
provisions and any race the early probe missed. `flyctl ssh console`
doesn't forward remote exit codes, so the launcher parses the real code
from stderr via `_extract_flyctl_remote_rc`. Both probes pivot via
`_attach_to_live_orchestrator`: `tail_with_optional_autofinalize()`
(default) or a `flyctl ssh console` bash payload (`--shell`), and set
`container_rc=130` so `decide_teardown` leaves the machine alone. The
attach transport is `flyctl ssh console` proxied through Fly's
hallpass + WireGuard mesh — no sshd, no key management, no public
exposure. Auth inherits from `flyctl auth status`.

Run-id resolution:

1. `leerie resume <id>` → look up
   `$LEERIE_STATE_HOST_DIR/runs/<id>/fly-machine.json` first, then
   `$LEERIE_STATE_HOST_DIR/runs/<id>/run.json` (which carries
   `fly_machine_id` per Phase 2). Neither → per-id "no Fly machine
   pointer found" error.
2. `leerie resume` (no run-id) → scan
   `$LEERIE_STATE_HOST_DIR/remote/*.json` for active records (filename
   is a launcher PID that still exists). Exactly one → resolve and
   continue. Multiple → print the list and exit 1. None → fall through
   to the per-id error path.

`provision.sh` writes the PID-keyed record at
`$LEERIE_STATE_HOST_DIR/remote/$$.json` immediately after creating the
machine, and also writes the run-keyed pointer
`$LEERIE_STATE_HOST_DIR/runs/$LEERIE_REMOTE_RUN_ID/fly-machine.json`
in the same call — before returning to the launcher — so `resume`
survives a Ctrl-C between `provision_machine()` returning and the
launcher's deferred copy. `destroy_machine` removes the PID-keyed
record on full reap; the launcher's own copy (guarded by `[ ! -f ]`)
is a no-op fallback for older images.

Schema for the record (both paths):

```json
{
  "fly_app": "my-leerie-app",
  "fly_machine_id": "148e445b911389",
  "started_at": "2026-05-29T16:00:00+00:00",
  "run_id": "feat-foo-abc123",
  "launcher_pid": 12345
}
```

Sub-mode flags:

| Flag | Effect |
|---|---|
| (default) | Tail `/work/.leerie/runs/<run-id>/orchestrator.log` via `render_tail_wrapper`. Ctrl-C detaches without affecting the orchestrator. |
| `--shell` | Open a bash shell at `/work` with `$PS1` set to `leerie@<run-id>:\w$` (the orchestrator runs unaffected in the background). |
| `--auto-finalize` | On clean orchestrator exit (alive→dead during tail), automatically `exec leerie finalize <run-id>` on the host. Plumbed via `tail_with_optional_autofinalize` and the `AUTO_FINALIZE_TOKEN` sentinel emitted by `render_tail_wrapper`. |

Both flags are launcher-only — the filter loop strips them from
`REWRITTEN_ARGS` before exec into the orchestrator (same convention as
`--no-re-seed`, `--no-runtime-install`).

Local-runtime `resume` is unaffected: local runs are synchronous
foreground processes (`nerdctl run --rm`, no backgrounding), so there's
no detached container to attach to — local `resume` keeps its inline
re-exec behavior. The smart router branches live inside `RUNTIME=fly`.

Maps to `DESIGN.md`: §6 *Smart resume in remote mode*.

#### Mid-run re-rsync (`scripts/remote/re-seed.sh`)

Two user-visible surfaces share one mechanism:

1. **`leerie re-seed <run-id> [--force]`** — explicit fast-path before
   runtime preflight. Wakes the machine if stopped, runs the safety
   check, runs `seed_repo_dirty`, exits — no orchestrator exec, for
   when the user wants to attach via Phase 3 to inspect before resuming.
2. **Auto-re-seed on `leerie resume <run-id> --runtime fly`** — inside
   the `RUNTIME=fly` branch, when `resume_machine` runs (the dual-file
   resolver yielded a `fly_machine_id`), the launcher calls `re_seed`
   between `seed_auth` and the orchestrator exec. `--no-re-seed` opts
   out (nothing changed host-side); `--force` bypasses the safety check.

   The dispatch is strict on `resume`: no machine pointer in either
   sidecar dies with a diagnostic pointing at `leerie list` rather than
   silently provisioning a fresh machine (which would orphan the
   original on Fly). A non-zero `resume_machine` (destroyed/unstart-able)
   exits with the failure rather than falling through to
   `provision_machine`. Without `resume`, fresh runs always provision.

Three operations in `re_seed`, in order:

1. **Wake the machine if needed.** `flyctl machine status` → if
   `stopped`, `flyctl machine start` + `wait_for_started`. Other states
   (`destroyed`, `replacing`, …) abort with an actionable message.
2. **Safety check (unless `LEERIE_RE_SEED_FORCE=1`).** Run
   `flyctl machine exec git -C /work status --porcelain`, filtering out
   `.leerie/` paths (worker state is expected to change there). Any
   dirty tracked file refuses with a message listing the first 10 paths
   and pointing at `leerie resume <run-id> --shell` and `--force` —
   prevents clobbering in-flight worker edits not yet committed.
3. **`seed_repo_dirty`.** Recompute the host's `git status --porcelain`
   dirty set, append every file under `.claude/` (force-included even
   when gitignored — workers need its hooks/agents/skills/commands),
   filter the combined list (drop `.git/*`, non-whitelisted `.leerie/*`
   paths, `.leerie/runs/*/worktrees/*`), then rsync to `/work` via
   `fly_rsync_wrapper` (lib.sh). The full-history clone on the machine
   is preserved — re-seed must never re-clone, which would obliterate
   the run branch and per-subtask branches.

Launcher flag consumption:

| Flag | Env | Default | Effect |
|---|---|---|---|
| `--no-re-seed` | — | off | Skip the auto-re-seed during `resume`. |
| `--force` | `LEERIE_RE_SEED_FORCE=1` | off | Bypass the safety check that refuses re-seed against machine-side dirty tracked files. |

Both flags are consumed by the launcher and not forwarded to the
orchestrator (same convention as `--no-runtime-install`,
`--no-auto-publish`).

Maps to `DESIGN.md`: §6 *Mid-run re-seed (remote mode)*.

#### Explicit pause and destroy verbs (`leerie stop`, `leerie kill`)

The detached orchestrator (DESIGN §6 *Detached orchestrator (remote
mode)*) decouples the user's local terminal from the run's lifetime.
Ctrl-C no longer means "destroy" — it means "stop watching." So the
destructive and pause actions need explicit verbs.

Two new launcher flags, routed at the top of `leerie` alongside
`resume` (line ~63):

- **`leerie stop <run-id>`** — clean pause. Runtime detection:
  (1) `_auto_detect_run_runtime` checks for `fly-machine.json` then
  `ec2-instance.json` → Fly/EC2 path; (2) `_is_local_container` probes
  `nerdctl inspect <run-id>` → local path; (3) neither → error.
  `--runtime` accepts `local`/`fly`/`ec2` explicitly, validated by the
  launcher (bash). `_auto_detect_fly_runtime` is a back-compat Fly-only
  wrapper around `_auto_detect_run_runtime` for call sites that have
  not yet grown EC2 handling.
  - **Fly path:** sources `provision.sh`, exports `LEERIE_MACHINE_ID`
    and `FLY_APP`, calls `stop_machine()`.
  - **EC2 path:** sources `aws-credentials.sh` + `ec2-lib.sh` +
    `ec2-provision.sh`, resolves AWS credentials via
    `resolve_aws_credentials()` and gates on `require_aws()` (same
    ordering as the `RUNTIME=ec2` dispatch branch — see "Remote
    execution mode"), resolves `LEERIE_EC2_INSTANCE_ID` via
    `_resolve_ec2_instance_id_from_run_dir` (checks `ec2-instance.json`
    first, then `run.json`), then calls `stop_instance()`.
    `StopInstances` preserves the root EBS volume (DESIGN §6 *EC2
    runtime lifecycle*, "EBS volume lifecycle" case 2 — stop-scoped,
    never `DeleteOnTermination`). Fails closed if no `ec2_instance_id`
    is in the sidecar, and via `require_aws`'s `aws sso login` hint if
    credentials don't resolve — before any `aws ec2 ...` call.
  - **Local path:** sources `lib.sh`, calls `nerdctl stop <run-id>`
    (SIGTERM first — `InterruptedBySignal` saves state before exit —
    then SIGKILL after grace period; `--rm` auto-removes the container).
  - All three paths call `update_run_json` to set `paused_at =
    <iso_now>` and `pause_reason = "user-requested"` (EC2 also writes
    `ec2_instance_id`, mirroring Fly's `fly_machine_id`). Resumable via
    `leerie resume <id>` (see `scripts/remote/ec2-resume-instance.sh`
    and `tests/test_ec2_launcher_resume.py`).
  - **Test coverage:** `tests/test_ec2_launcher_stop.py` — EC2
    autodetect and explicit `--runtime ec2`, `stop-instances` called
    and never `terminate-instances`, missing-instance-id and
    credential-failure fail-closed paths, and a regression pin that the
    local/Fly fallthrough error text still fires unchanged with no
    sidecar present.
- **`leerie kill <run-id> [--force]`** — destroy. Same runtime
  detection as `stop`. Prompts the user to type the run-id to confirm
  (unless `--force` / `LEERIE_FORCE_KILL=1`).
  - **Fly path:** calls `destroy_machine()`, sets `killed_at` and
    `fly_machine_id` on the sidecar.
  - **Local path:** calls `nerdctl kill <run-id>` (immediate SIGKILL),
    sets `killed_at` on the sidecar.
  - The run is no longer resumable.

  Recovery path for the orphan case: `leerie kill --machine-id <id>
  --app <app>` destroys by machine-id directly when the sidecar is
  missing or unreadable (e.g., `.leerie/` deleted but the machine is
  still running on Fly). Fly-only.

Both verbs route before any runtime preflight and exit without ever
sourcing `seed-auth.sh` / `seed-repo.sh`. The Fly path calls
`require_flyctl` from `lib.sh`; the local path only sources `lib.sh`
(for `update_run_json` / `iso_now`). Both are read-only with respect
to the local repo (except for the sidecar update).

The `killed_at` field is added to `RUN_STATUSES` in `orchestrator/leerie.py`
as a new terminal state (`killed`); `_derive_run_status` reads it
before `paused_at`. `_validate_run_json` enforces that `paused_at`,
`pushed_at`, and `killed_at` are mutually exclusive (same invariant
pattern as today's `paused_at` vs `pushed_at`).

#### Completion gate (`incomplete` status + finalize refusal)

DESIGN §6 *`finished_at` is a discovery sentinel, not a completion
signal*. Because `main()`'s `except SystemExit` handler stamps
`finished_at` on any post-setup `die()` (needed for `fetch_branch`
discovery), `finished_at` alone does not mean the run's waves all
integrated — a run OOM-killed mid-wave can carry `finished_at` with
`completed_waves < len(waves)`. Three code-surface elements gate on
real completion, all reading the same signal from `state.json`
(`run.json` never carries `completed_waves`/`waves`):

- **`_derive_run_status`** takes `state_json` and, when `finished_at`
  is set but `completed_waves < len(state_json["waves"])` and neither
  `killed_at` nor `paused_at` is set, returns the new status
  `incomplete` instead of `done`/`done-pushed-*`. Fires after the
  push/PR-error checks but before `finished_at`→`done`. `incomplete`
  is added to the derived-status set and is a valid `list status`
  filter value. The cleared-but-empty terminal state
  (`no_work_required`, `waves == []`) is exempt — `completed_waves (0)
  < len([]) (0)` is false, so it still reads `done`. Gates only the
  `list` *display*, not the push.
- **`phase_finalize`** guards its entry: `completed_waves < len(waves)`
  `die()`s with a "refusing to finalize: N of M waves complete"
  message rather than writing the real `finished_at`.
  Belt-and-suspenders — the wave loop only reaches `phase_finalize`
  after all waves integrate (no-work returns before it), but a stray
  finalize-only invocation is blocked here. This is the *in-container*
  orchestrator; it does not itself push.
- **`host_finalize`** (`scripts/host-finalize.sh`) is the
  **load-bearing gate**, because the push+PR is host-side. After the
  `no_push`/`pushed_at` early-returns, it reads `$run_dir/state.json`
  and `return 1`s with an actionable resume hint when
  `no_work_required != true` and `completed_waves < (.waves | length)`.
  All three host-side push entry points funnel through `host_finalize`
  — the launcher's auto-finalize block, `leerie finalize <id>`, and
  Fly's `decide_teardown` — so this one gate covers them all.
  Fail-open: absent/non-numeric wave fields skip the gate so a
  legitimately complete run is never blocked over a missing file.
  Without this gate, `_derive_run_status` and `phase_finalize` alone
  would still let a stray `finalize` push a partial branch (the
  PR-#22 incident).

#### `_create_empty_subtask_branch(repo_root, run_id, sid)`

A settle that never ran an implementer still owes the wave a branch.
`integrate_wave` filters on `status == "complete"` alone and never asks
whether `leerie/subtasks/<run-id>/<sid>` exists; `scripts/integrate.sh`
exits 2 on a missing branch, and `integrate_wave` turns rc 2 into a
`die()`.

The post-execution rescue is safe because its implementer ran: the
branch exists with zero commits, and `git merge --no-ff` of a branch
already an ancestor is a true no-op. The pre-spawn probe (DESIGN §8
*Probing a flagged subtask before it spends*) returns first, so this
helper creates the same artifact at the run-branch tip before settling.

Idempotent — an existing branch is never repointed, since on resume it
may carry commits — and the settle is gated on it: if the branch can't
be created the subtask falls through to its implementer, because a
settle integration cannot merge is worse than the spend it saves. A
failure names the git error rather than an unactionable "could not be
created".

#### Scoped worktree pruning (`worktree-lib.sh`, `_prune_leerie_worktrees`)

`scripts/worktree-lib.sh` exports `prune_leerie_worktrees <leerie-root>`,
sourced by `setup-run.sh`, `new-worktree.sh` and `cleanup.sh`, which
previously each ran a bare `git worktree prune`.

The repo is bind-mounted whole into every container, so `.git` is
SHARED with the host and any other container. A bare prune is
repository-global with **no grace period** (`gc.worktreePruneExpire`
applies to `git gc`, not an explicit `prune`), so it drops the
registration of any worktree whose path is absent from the pruning
process's namespace — including every host-side
`/tmp/tmp.*/rebase-<run-id>` worktree the finalize rebase creates,
which no container can see.

The replacement asks git what it would prune (`git worktree prune -n
-v`, output on **stderr**), maps each reported admin name back to its
path via `$GIT_DIR/worktrees/<name>/gitdir`, and prunes only
registrations under the given root. The two callers pass different
roots deliberately: the shell helper is called with `$LEERIE_ROOT`
(the state root), so it may reap a sibling run's stale registrations,
while the Python port is called with the run directory and cannot.
Host registrations are out of scope either way, which is the point —
removing the prune entirely is not an option, since it's what clears
the stale `.git/worktrees/` metadata a SIGKILLed run leaves behind.

The orchestrator's own four call sites — `_cleanup_on_abnormal_exit`,
`_reset_subtask_worktree`, `_prune_subtask_worktree` and
`phase_execute`'s post-`setup-run.sh` prune — go through
`_prune_leerie_worktrees`, the Python port at run-dir granularity. Two
of the four (`_reset_subtask_worktree`, `_prune_subtask_worktree`)
dispatch it via `asyncio.to_thread` since both are awaited from inside
the wave and shell out to git — sharing the rmtree-fallback+
`to_thread`-prune tail through one helper, `_rmtree_fallback_and_prune`;
`_cleanup_on_abnormal_exit` runs synchronously off `st.run_dir` where
there's no loop left to block. The probe runs under `LC_ALL=C
LANGUAGE=` — `git worktree prune -n -v` wraps its output in `_()`, so
parsing English prefixes under another locale matches nothing
(`tests/test_worktree_prune_scoping.py` pins the property).

`tests/test_worktree_prune_scoping.py` guards against a bare-prune
regression at any of these sites, deriving its surface from
`scripts/**/*.sh` + `scripts/**/*.py` + `orchestrator/**/*.py` +
`chain/**/*.py` + the launcher (including Python embedded in a shell
heredoc) via an `ast` walk over call names/argument shapes/languages,
backstopped by a coarse textual floor underneath.

#### Prune verb (`leerie prune`)

Reclaims state that nothing else reaps — run directories, repo-map-cache
entries, and stale `leerie/subtasks/*` branches accumulate unbounded,
while `preflight()` already refuses to start a run on low disk headroom
and tells the operator to prune by hand.

Host-only (no container). `leerie prune [--older-than DAYS] [--apply]`;
`--older-than` accepts both the space-separated and `=` forms and
defaults to **14**. **Dry-run is the default** and `--apply` is
required to delete anything: this removes directories that may hold
the only record of a paid-for run, so the safe mode is the default.

Three categories. Run directories and cache entries are subject to the
age cutoff; **branches are not** — they're scoped by run-dir liveness,
so a branch whose run dir was deleted by hand is in scope regardless
of age:

- **terminal run directories** — only those whose `run.json` carries
  `finished_at` or `killed_at`. A paused or in-flight run is resumable
  and survives regardless of age.
- **repo-map cache entries** under `<state-root>/repo-map-cache/` —
  regenerated on demand.
- **orphaned subtask branches** — `leerie/subtasks/<run-id>/*` whose
  run-id is not among the runs this pass left alive (`run_id not in
  live`), distinct from "run directory absent from
  `<state-root>/runs/`": a run dir removed by this very pass is gone
  from disk yet still in scope, and a surviving dir keeps its branches
  whatever their age (`docs/USAGE.md` already states it this way).
  Scoped to that namespace, so a user branch is never in scope.

**Branch reaping needs positive evidence.** "No run dir in this state
root" is not evidence a branch is orphaned — a state root is silent
about every run it never owned, so pruning from the wrong
`--state-dir` would read every branch as dead. Checking `runs.is_dir()`
doesn't help: the orchestrator creates that directory unconditionally.

Three tiers, in order:

1. a run dir **this prune just removed** is known terminal and old → `-D`;
2. the branch is an **ancestor of its own run branch**, so its commits
   are already reachable from the integrated history → `-D`. This tier
   keeps the feature useful: `git branch -d` checks merged-into-HEAD,
   and a subtask branch merges into `leerie/runs/<id>`, never `main`,
   so without it a fully integrated, long-pushed branch is refused
   exactly like one holding unique work;
3. everything else → `git branch -d`, which git refuses when unmerged.

Stale merged branches are still reclaimed (F22's 64); unmerged work
cannot be lost regardless of what this root knows. Kept branches are
reported (`kept N subtask branch(es) with unmerged commits`), and a
delete that fails for any *other* reason is reported as itself — `-D`
never refuses for unmergedness, and saying it did is what kept the
next defect invisible.

**Worktree registrations are dropped before the run dir is deleted.**
`shutil.rmtree` removes `<run>/worktrees/<sid>/` but leaves
`.git/worktrees/<sid>`, and git then refuses `git branch -D` with
"cannot delete branch … used by worktree at …" — so registrations
must be dropped first.

Attribution is by **host** path, and the `gitdir` file may hold either
spelling: a subtask worktree is created *inside* the container, where
the state root is bind-mounted at `/leerie-state`, so `new-worktree.sh`
writes `/leerie-state/runs/<id>/…` while prune runs on the host.
`_host_spelling` translates the container prefix — the same mapping
`_operator_path` performs for operator-facing text — before any
comparison; without it the whole deregistration was a no-op in the
only runtime that produces the defect. A relative `gitdir` (git ≥ 2.48
with `worktree.useRelativePaths=true`) is resolved against the *entry*
directory, not the process cwd, and an empty one is unattributable
rather than resolving to the cwd.

It's scoped by construction to registrations pointing inside the state
root, so a sibling checkout's or the operator's own worktrees are
never touched, and git's `locked` marker is honoured. Within that
scope it sweeps **every** orphaned registration, not only the
directories this pass removed: one removed by an earlier prune, by
`cleanup.sh` or by hand leaves a registration no later prune would
consult, and its branch is then unreapable forever.

**Reaping requires liveness, not just timestamps.** Nothing clears
`finished_at`, so a run that die()d once reads as terminal on every
later prune, leaving `mtime < cutoff` as the only protection — and
`--older-than 0` is accepted. `_is_live` probes the run-directory
flock (`State.__init__` holds it for the life of the orchestrator, an
inode lock; the container bind-mounts that directory, so a host-side
probe sees a container-side holder) and then `nerdctl inspect`, which
covers a crashed orchestrator that released the lock while its
container is still up. Fails **closed**: any probe that cannot
complete reads as live.

Dry run classifies branches the same way `--apply` does — it
previously appended every candidate without probing mergedness, so
the default mode overstated the result and never printed the `kept`
line, the one thing an operator needs before choosing `--apply`. The
two modes are not identical: a dry run performs no deregistration, so
it has no `branches_blocked` equivalent and cannot report a delete
that `--apply` would find blocked.

`prune` is a launcher-only verb and appears in the `REWRITTEN_ARGS`
guard arm, so a misplaced `leerie <task> prune` errors rather than
reaching the orchestrator's argparse.

#### Accept-blocked verb (`leerie accept-blocked`)

When a subtask returns `status: blocked` due to unsatisfied `extent:
external` prerequisites (DESIGN §5), `resume` retries it — which
blocks again indefinitely. The `accept-blocked` verb lets the operator
acknowledge the external block so `resume` skips that subtask.

- **`leerie accept-blocked <run-id> <subtask-id> [--runtime fly|local|ec2] [--force]`**
  — sets `subtask_status[sid]` to `"complete"` in state.json and
  removes the sid from the `blocked` dict (if present). On `resume`,
  `phase_execute`'s wave-skip filters subtasks whose `subtask_status`
  is `"complete"`, so the accepted subtask never re-dispatches.

  The gate resolves the `blocked` registry **before** testing
  `subtask_status`, because that registry — not the status string — is
  the authoritative record, and the two can disagree by **absence** (a
  sid in `blocked` with no `subtask_status` entry at all, because its
  checkpoint was rejected before a status was written) as well as by
  value.

  `--force` settles a subtask abandoned **mid-flight** — `in_progress`
  with no registry entry, what a hard crash (ENOSPC, SIGKILL) leaves
  behind and which neither field can express as blocked. It bypasses
  both status checks, so it validates the sid against the scheduled
  set (`waves`) to stop a typo minting a bogus `subtask_status` entry
  — **when `waves` is present**, which is every run where a subtask
  can be blocked or abandoned, since `waves` is populated at
  scheduling. Legacy or pre-scheduling state carries no `waves` and
  degrades open rather than refusing a legitimate accept. The default
  stays strict, keeping `--force` a deliberate operator act. Pinned in
  `tests/test_accept_blocked.py` (absent-key accepted, neither-field
  still refused, forced-abandoned accepted, forced-typo refused).

  Without an explicit `--runtime`, the verb auto-detects via the
  shared `_auto_detect_run_runtime` helper (probes `fly-machine.json`
  then `ec2-instance.json` — same order `stop` uses), falling back to
  `local` only when neither sidecar is present.
  - **Input validation (all runtimes):** both positionals are checked
    against `^[A-Za-z0-9._-]+$` immediately after parsing, before they
    reach any filesystem path or remote shell. The run-id is
    interpolated into the host state-dir path (traversal risk) and, on
    Fly, into the `flyctl ssh console -C` string; the sid into that
    same `-C` string. Since `-C` is parsed by a **remote shell**, an
    unvalidated metacharacter would be command-injection (SECURITY.md);
    the allowlist is the mechanical enforcement (DESIGN §12).
  - **Local path:** runs the mutation program (`python3 -c "$_ab_mutate"`)
    against `$LEERIE_STATE_HOST_DIR/runs/<id>/state.json` directly
    (bind-mounted into containers).
  - **Fly path:** inspects `flyctl machine status`; refuses on
    `destroyed`/missing; if `stopped`, wakes the machine (`flyctl
    machine start` + `wait_for_started`, fatal on failure) and records
    that it did so. Waits for hallpass via `wait_for_fly_ssh_ready
    "$FLY_APP" "$machine_id"`. Pipes the mutation program over
    **stdin** to `python3 -` on the machine (`printf '%s' "$_ab_mutate"
    | flyctl ssh console ... -C "python3 - '<remote-state>' '<sid>'"`)
    so the multi-line script body never round-trips through a shell
    quoter (same idiom as `force-finalize.sh`). The `-C` string itself
    IS parsed by a remote shell, so the two positional args are
    single-quoted and the run-id/sid inside them are the validated
    tokens above. The program prints an `ACCEPTED:` / `NOOP:` /
    `ERROR:` sentinel that the launcher greps (flyctl flattens the
    remote exit code); the host-side copy is mirrored best-effort.
    Teardown is **conditional**: the machine is paused again (with
    `paused_at`/`pause_reason`/`fly_machine_id` re-written via
    `update_run_json`) only if this verb woke a stopped machine — one
    already running is left running.
  - **EC2 path:** resolves credentials (`resolve_aws_credentials`,
    `require_aws`) and the instance id via
    `_resolve_ec2_instance_id_from_run_dir`, failing closed if
    `ec2_instance_id` is absent from the sidecar. Inspects instance
    state via `_describe_instance_state`; refuses on
    `terminated`/`shutting-down`/missing; if not `running`, wakes it
    (`resume_instance`, fatal on failure) and records that it did so.
    Pipes the same mutation program over **stdin** to `python3 -` via
    `ec2_remote_exec` (SSM — no ssh keypair or hallpass wait needed)
    and greps the same sentinels; host-side copy mirrored
    best-effort. Teardown is **conditional** the same way as Fly.
  - The mutation program validates the subtask's current status is
    `"blocked"` or `"failed"` before mutating (atomic temp-file +
    `os.replace`). No-ops with `NOOP:` if already `"complete"`.
  - **Test coverage:** `tests/test_accept_blocked.py` — local-path
    tests (mutation, no-op, error paths, blocked-dict cleanup),
    Fly-path tests with a stubbed `flyctl` parsing both `-C`
    positionals and routing the stdin-piped `python3 -` to a local
    fixture, and injection-rejection tests asserting a
    metacharacter-bearing run-id/sid is refused with no mutation.
    `tests/test_ec2_launcher_readonly_verbs.py` covers the EC2 path:
    runtime auto-detection over the pre-fix silent `local` default,
    the widened `fly|local|ec2` validator (with a control that a
    genuinely bogus value is still rejected), the accept-record
    mutation landing on both the remote (SSM) state.json and the
    mirrored host copy, the wake/re-pause discipline (and its
    already-running no-op counterpart), and the missing-`ec2_instance_id`
    fail-closed path.

Maps to `DESIGN.md`: §5 *`requires.extent` — in-graph vs. external
prerequisites*, *Accepting external-blocked subtasks*.

Maps to `DESIGN.md`: §6 *Detached orchestrator (remote mode)*, *The
user-visible verb surface*.

#### Unified `leerie list` (cost column + `status` + `--runtime` filters)

`_list_runs()` in `orchestrator/leerie.py` is extended to surface
remote runs alongside local runs in a single table. Status and runtime
are **orthogonal axes**: status describes lifecycle (`paused`,
`killed`, `done`, `sync-failed`, `in-progress`, `done-pushed-pr`,
`done-pushed-no-pr`, `push-failed`, `pr-failed`, `corrupt-sidecar`,
`seed-failed`); runtime describes where the run executed (`local` or
`fly`). `seed-failed` covers run dirs with a `fly-machine.json`
(launcher wrote it the moment Fly provision succeeded) but no
`state.json` (orchestrator never wrote one, typically because
`seed_auth` aborted before `phase_classify`). `_discover_runs()`
synthesizes a row dict with `_orphan=True` and `started_at` from the
fly sidecar; `_derive_run_status()` returns `seed-failed` for them
(earliest precedence, before the run.json corrupt-sidecar check).
`resolve_run_id()` accepts orphan ids transitively (no special-casing
needed once `_discover_runs` returns them), so `./leerie resume
<orphan-id> --runtime fly` works against a seed-failed run. An
**explicit** id is exempt from the resumable-status filter below;
`seed-failed` is excluded from the bare-`resume` *auto-pick* only (it
needs an operator decision first, and its rows carry no `started_at`
to rank by).

**EC2 counterpart (`list`'s Python-layer view, not `_discover_runs()`'s
orphan scan).** `_collect_run_rows()` tracks an `is_ec2` axis the same way it
tracks `is_fly`, so `--runtime ec2`/`--runtime local` filter EC2 runs
correctly and a plain `list` renders an EC2 run's status without
`LEERIE_FLY_APP` set. Distinct from `_discover_runs()`'s orphan scan (DESIGN
§6 *EC2 runtime lifecycle*, "Run identifier"), still hardcoded to
`fly-machine.json` and not yet widened to `ec2-instance.json` for
pre-`state.json` orphan discovery — separate, not-yet-landed work.

Changes: `_collect_run_rows()` returns `(run_id, started_at, status, branch,
is_fly, cost, is_ec2)` — `is_fly`/`is_ec2` are bools derived from `run.json`
or the matching sidecar file (mirrors `_auto_detect_run_runtime`),
**filter-only** (never rendered as columns; `is_fly` stays at index 4 for
existing `r[4]` consumers, `is_ec2` appended at index 6), and `cost` is the
run's aggregate `$X.XX` from `state.json`'s `telemetry.cost_usd` or `—` when
absent. `_render_run_table()` renders `run_id, started_at, status, cost,
branch` (right-aligned cost, auto-sized widths). `status <state>` on `list`
filters rows to a matching derived status (any `RUN_STATUSES` value; invalid
values error listing the allowed set). `--runtime` on `list` accepts
`local`/`fly`/`ec2` (`RUNTIME_VALUES`, argparse `choices=`); `local`
restricts to rows with **neither** Fly nor EC2 artifacts. `list --runtime
fly` is intercepted by the launcher (bash) before orchestrator dispatch and
queries Fly directly (`flyctl machines list --app <FLY_APP> --json`),
rendering `machine_id | state | region | created_at | run_id (local)` for
every machine under the app — `run_id` is best-effort filled by scanning
local sidecars (machines from another repo show `run_id=?`), falling back
to the orchestrator-side local-sidecar list when `flyctl` is missing or
auth fails. Any other `--runtime` value falls through unchanged.

Verbs `kill`, `stop`, and `accept-blocked` accept an optional
`--runtime <local|fly|ec2>` flag, validated against the same `RUNTIME_VALUES`
enum that gates launching a new run. `finalize` remains narrower — it
validates only `local`/`fly` (rejecting `ec2`) since `finalize --runtime ec2`
has not shipped.

The launcher's `RUNTIME=ec2` branch dispatches the full create → seed →
launch → teardown cycle for launching a run; `stop --runtime ec2` routes to
`stop_instance()`; `kill --runtime ec2` routes to `terminate_instance()` with
fetch-before-terminate ordering. Fly runs route to `flyctl machine
stop`/`destroy`; EC2 to `aws ec2 stop-instances`/`terminate-instances`; local
to `nerdctl stop`/`kill` via `_is_local_container` (`nerdctl inspect
<run-id>`). `stop` uses SIGTERM-equivalents (graceful state save); `kill`
uses immediate destroy (EC2 after the fetch-before-terminate sync).
`finalize --runtime local` still errors — local finalization is inline.
Without the flag, verbs infer runtime from the sidecar (Fly, then EC2, then
`nerdctl inspect`, via `_auto_detect_run_runtime`). `resume` accepts
`--runtime` directly (fly takes the smart-attach path, local the inline
re-exec path).

Maps to `DESIGN.md`: §6 *The user-visible verb surface*.

#### Detached run finalization (`leerie finalize <run-id>`)

With the detached orchestrator, the launcher cannot synchronously wait for
orchestrator completion and call `fetch_branch` — the tail's exec session
ends before (or independent of) the orchestrator's actual exit. Two surfaces
address this together:

1. **`orchestrator.pid` on the machine.** The detached-launch sh wrapper
   records the orchestrator's pid in
   `/work/.leerie/runs/<run-id>/orchestrator.pid` after the post-`Popen`
   poll clears the flock-loser case (DESIGN §6 *Single owner per run dir*).
   `leerie resume`'s in-machine tail watcher checks liveness via two ORed
   signals — pid-file `kill -0` and a `/proc/[0-9]*/cmdline` scan for
   `orchestrator/leerie.py` + run-id — alongside the `tail -F`; both must
   agree the orchestrator is dead before it prints its syncing-to-host
   message and exits. The `/proc` scan closes the stale-pid contagion from
   DESIGN §6: a stale pid file still leaves the real orchestrator found by
   the scan.
2. **`leerie finalize <run-id>`** — launcher fast-path running the
   post-orchestrator block inline: source `fetch-branch.sh`, call
   `fetch_branch`, then the host-side finalize block (push + `gh pr
   create`). Idempotent — an already-pushed run short-circuits with
   "already finalized."

`leerie finalize` resolves `<run-id>` directly against
`$LEERIE_STATE_HOST_DIR/runs/<run-id>/` (the run-id IS the machine id, DESIGN
§6, so no fallback lookup is needed); no match errors with a hint to run
`leerie list`.

**Non-force** first tries `fetch_branch` (clean-exit case). On failure it
auto-recovers: `force_finalize_remote` (checks orchestrator liveness, patches
`finished_at` — see below), then `collect_subtrees_remote` to integrate
un-merged subtask branches, then retries `fetch_branch`. If the orchestrator
is still alive, it refuses with a hint to use `--force`.

**`--force`** extends recovery to a still-alive orchestrator:
`force_finalize_remote` with `FORCE_STOP=1` SIGTERMs the orchestrator process
*inside the machine* (not the machine itself), waits for death (polling
`/proc`, escalating to SIGKILL after 30s), patches `finished_at`, then runs
`collect_subtrees_remote` and `fetch_branch`.

**Liveness checks** (`scripts/remote/force-finalize.sh`): lists
`/work/.leerie/runs/` for the single run dir (fails on multi-match); reads
`run.json` and no-ops if `finished_at` is already set; checks liveness via
two ORed signals — an authoritative `/proc/[0-9]*/cmdline` scan for
`orchestrator/leerie.py` + run-id, and a defensive `orchestrator.pid` check
(`kill -0` + `cmdline` containing `python`, not `comm`, since `comm` on a
pip-installed shim can read `"pytest"` and slip an alive orchestrator past
the guard). Either signal alive → **REFUSE-ALIVE[-SCAN]** (or **STOPPED**
under `FORCE_STOP=1`); both dead → safe to proceed; pid file missing →
refuse, pointing at `leerie resume <run-id> --shell --runtime fly`. The
`/proc` scan exists because the pid file is written *between* `Popen` and
the child's `State.__init__`, so a stillborn flock-loser can stamp a dead
pid before the winner claims authority (stale-pid contagion, DESIGN §6).
On success, patches `run.json` with `finished_at`, `no_push=false`,
`recovered_at`, `recovered_via="force-finalize"`, then falls through to
`fetch_branch`.

Sentinels: `OK:<run_id>`, `STOPPED:<run_id>:<pid>`, `STOP-FAILED:<run_id>:<pid>`,
`REFUSE-ALIVE-SCAN:*`, `REFUSE-ALIVE:*`, `REFUSE-NOPID:*`, `REFUSE-MULTI:*`,
`REFUSE-NONE`, `ERROR:*`.

**Subtree collection** (`scripts/remote/collect-subtrees.sh`):
`collect_subtrees_remote` SSHes a bash payload that discovers un-integrated
subtask branches and merges them via `setup-run.sh` (idempotent) +
`integrate.sh`, resolving conflicts by spawning `claude -p` with the
integrator prompt/schema (same invocation as `integrate_wave()`). This runs
only after the orchestrator has exited, so it sits outside the
`--dangerously-force-strict-output` path — output is still schema-validated
by the script's embedded `SCHEMAS["integrator"]` copy, just not constrained
during generation (DESIGN §7 *Forcing constrained decoding*). On success the
merge commit is verified (`MERGE_HEAD` absent, nothing staged); on failure
the merge aborts and the branch is skipped. Wave ordering comes from
`state.json` when available, else alphabetical. Sentinels:
`COLLECTED-ALL:<run_id>:<count>`,
`COLLECTED:<run_id>:<integrated>:<skipped>:<skipped_sids>`,
`COLLECTED-NONE:<run_id>`, `COLLECT-ERROR:<message>`.

`finalize` logs its action before SSHing in
(`finalize: machine=<id> run=<id> action=<fetch|force-stop+collect+fetch|already-synced>`),
matching the convention that destructive/side-effecting actions are explicit
verbs (DESIGN §6), not implicit consequences of stream timing.

`leerie resume <run-id> --auto-finalize` runs `leerie finalize` automatically
on clean-exit detection, for zero-touch finalization; the same plumbing
applies to the fresh-launch tail (`--runtime fly --auto-finalize`).

Maps to `DESIGN.md`: §6 *Detached orchestrator (remote mode)*,
*Finalization* (recovery sub-paragraph).

#### Chain orchestration (cross-reference)

The chain orchestration code surface is documented in
[**§2 *Chain verbs***](#chain-verbs) earlier in this file (the launcher
verbs, coordinator endpoints, state schema, and worker-side hooks).
DESIGN.md §19 holds the architecture rationale.


---

## 8. Coordination directory layout

State lives under the resolved state root — by default
`$HOME/.leerie/<basename>/`, or the path set via `LEERIE_STATE_DIR` /
`--state-dir` / `leerie.toml state_dir` (see §2 *Host-side per-repo state
directory* for the full resolution order). The state root is always outside the target repo,
so no `.leerie/` directory accumulates in project checkouts and no
`.gitignore` entry is needed. Worktrees are
disposable; the coordination directory outlives them.

Every run's artifacts live under `<state-root>/runs/<run-id>/`. The state
root is otherwise empty of run data; it only hosts the `runs/` directory.
Two concurrent runs in the same repository share no coordination state.

```
<state-root>/          (default: $HOME/.leerie/<basename>/)
                        also contains: .owner (sidecar — abs_path of the owning repo)
└── runs/
    └── <run-id>/                    (container/machine ID — known from creation)
        ├── state.json               run state — see field table below
        ├── run.json                 sidecar — see field table below
        ├── working-branch           the branch HEAD-at-run-start; used as the PR base (leerie does not merge into it locally)
        ├── plan.json                merged planner output
        ├── task.md                  the task document verbatim, as plain markdown
        ├── subtasks/<id>.json       per-subtask spec handed to each implementer
        ├── criteria/<id>.md         informational success-criteria notes (DESIGN §9)
        ├── artifacts/<id>.json      structured deliverables from an implementer's `artifacts` field (DESIGN §5); absent for code-implementation subtasks
        ├── checkpoints/<id>.md      handoff checkpoints (7-section schema)
        ├── logs/<sid>.log           per-worker raw stream-json event log, one file per claude_p invocation by sid
        ├── worktrees/staging        the run-branch worktree
        ├── worktrees/<id>           per-subtask worktrees
        ├── pending-questions.json   written when clarification needs a non-interactive relay
        ├── pending-clarifications.json  written when an implementer hits a §11 mid-execution clarification
        ├── answers.json             written by the plugin skill when relaying clarification answers; passed back via --answers
        ├── calls.ndjson             per-run NDJSON telemetry, one line per claude_p call (DESIGN §14)
        ├── memory.ndjson            orchestrator memory telemetry, one line per ~30s while _orchestrate() is alive (written by `_memory_sampler`)
        └── <heal_subdir>/           heal-loop on-disk state (default: "heal-out/")
            └── <call_type>/         one directory per call_type being healed
                ├── state.json       heal orchestrator state (history, best, baseline)
                └── iter-<N>/        one directory per heal iteration
                    ├── patch-request.json   inputs for the patch-generator worker
                    ├── patch-response.json  patch-generator worker's structured output
                    ├── applied-patch.txt    the patched system prompt text
                    ├── arm-results.json     n-replay results for each failing sample
                    └── scores.json          per-sample per-replay pass/fail verdicts
```

The `<run-id>` is the container/machine ID assigned by the container
runtime at creation time (DESIGN §6). There is no temporary directory
or rename step — the run directory is created with its final name from
the start.

`_task_ref` in every subtask spec points at `task.md` (N6), not `plan.json`
— the latter also carries every subtask body and can exceed the CLI's Read
cap on a large task. `memory.ndjson` lines carry `ts`, `rss_kb`, `phase`
(mirrors `state.current_phase`), `worker_count`, `open_fds` (`-1` off
Linux), `thread_count`; the final sample flushes on sampler cancellation so
the file always captures last-known state at exit, useful for
distinguishing a natural heavy run from a real orchestrator memory leak.

`run.json` fields (a minimal sidecar enabling `leerie list` and resume
discovery without parsing the full `state.json`):

| Field | Shape | Notes |
|-------|-------|-------|
| `run_id` | str | the run identifier (matches the directory name and the branch suffix) |
| `branch` | str | the run branch — always `leerie/runs/<run_id>` |
| `working_branch` | str | the branch HEAD-at-run-start; the diff fork-point (leerie does not merge into it locally). Also the PR base by default — see `pr_base_branch` below for the override. |
| `pr_base_branch` | str | the final branch this run's PR merges into; defaults to `working_branch`, overridable via `--pr-base-branch` / `LEERIE_PR_BASE_BRANCH` / `pr_base_branch` in `leerie.toml` (see "PR base branch override" above). |
| `started_at` | ISO-8601 str | wall-clock start time (also mirrored in `state.json`) |
| `finished_at` | ISO-8601 str \| null | wall-clock end time. Set at finalize success, or by the `except SystemExit` handler in `main()` for `die()` exits after the run directory exists (on Fly, the tail wrapper propagates the exit code). Idempotent on `resume`. |
| `task` | str | the task description (mirrored from `state.json`) |
| `task_sha256` | str | sha256 of the resolved task text, written at run start. Two launches of byte-identical task text are otherwise invisible to each other and can produce incompatible branches; `_live_duplicate_runs` refuses a duplicate unless `LEERIE_ALLOW_DUPLICATE_TASK=1`. |
| `pushed_at` | ISO-8601 str \| null | when the run branch was pushed to `origin`; null until push runs |
| `push_error` | str \| null | captured `git push` output if the push failed — stderr plus any pre-push hook stdout under a marker, tail-bounded to 32 KiB; mutually exclusive with `pushed_at`. |
| `pr_url` | str \| null | the PR URL `gh` returned; null until PR creation succeeds |
| `pr_error` | str \| null | captured `gh` stderr if PR creation failed; logical invariant — `pr_error` can be set only after `pushed_at` is set |
| `fly_machine_id` | str \| null | Fly Machine ID for a remote (`--runtime fly`) run; written immediately after `flyctl machine run` succeeds, so a launcher crash before classifying still leaves a recoverable pointer. Null for local runs. |
| `paused_at` | ISO-8601 str \| null | when the remote run was paused — on failure or by explicit `leerie stop <run-id>`. Cleared at finalize. |
| `pause_reason` | str \| null | short tag identifying which path set `paused_at` (`worker-error`, `orchestrator-exception`, `finalize-failed`, `user-requested`). Null when `paused_at` is null. Cleared with `paused_at` at finalize (see above). |
| `killed_at` | ISO-8601 str \| null | when the remote run was explicitly destroyed by `leerie kill <run-id>`. The Fly Machine has been destroyed and the run is no longer resumable. Null for any other terminal state. |
| `sync_failed_at` | ISO-8601 str \| null | when `fetch_branch` failed on the clean-exit path — the orchestrator finished but state couldn't be pulled back to host. The machine is left running for recovery via `finalize`/`resume`/`kill`. |
| `sync_fail_reason` | str \| null | short tag accompanying `sync_failed_at` (currently always `sync-failed-on-clean-exit`). Null when `sync_failed_at` is null. |
| `recovered_at` | ISO-8601 str \| null | when `leerie finalize --force` patched this run's `finished_at` after the orchestrator died before its natural finalize. Written once, on the first successful `--force` recovery. |
| `recovered_via` | str \| null | short tag accompanying `recovered_at`; currently always `"force-finalize"`. Null when `recovered_at` is null. |
| `volume_id` | str \| null | Fly volume ID when the machine was provisioned with one (default on `--runtime fly`). Mounted at `/work`; destroyed with the machine. Requires `fly_machine_id` non-null. |
| `image_tag` | str \| null | Full Fly registry image tag recorded at provision time; `resume_machine()` updates the machine's image on resume if `$FLY_IMAGE_TAG` has drifted from the stored value. |
| `pr_title` | str \| null | LLM-written PR title from the `pr_writer` worker (omits the `leerie: ` prefix; the launcher prepends it). Null when the worker errored or was skipped (no-push); `host_finalize` falls back to a deterministic title. |
| `pr_body` | str \| null | LLM-written PR body (markdown) from the `pr_writer` worker. Null on the same conditions as `pr_title`. |
| `pr_template_used` | str \| null | repo-relative path of the PR template the worker filled out (e.g. `.github/pull_request_template.md`). Null when the worker produced its no-template default structure. |
| `rebase_disposition_status` | str \| null | set to `"unusable"` by the rebase fallback arm when the rebaser seam returns rc=0 but its JSON is empty, unparseable, or lacks `status`. |
| `rebase_disposition_jq_rc` | str \| null | the `jq -e` exit code from parsing `$_rebaser_json` in that fallback arm — non-zero means the payload was unparseable JSON, not merely missing `.status`. Null under the same conditions as `rebase_disposition_status`. |
| `rebase_disposition_raw_json` | str \| null | `$_rebaser_json` (the seam's verdict file, not its stdout), tail-truncated to 2000 bytes — the artifact identifying why the rebase degraded. Null under the same conditions as `rebase_disposition_status`. |
| `chain_id` | str \| null | UUID of the chain this run is part of. Written early by the child process after `provision_machine` succeeds, and re-written by the parent post-wait. Null for non-chain runs. |
| `wave_idx` | int \| null | Zero-based wave index within the chain (set alongside `chain_id`). Used by the chain wave-sequencer to group runs by wave for synth-merge between waves. Null when `chain_id` is null. |
| `health` | dict \| null | Advisory run-health signals (DESIGN §9), merged from two seams: `_capture_conformance_baseline` writes `base_suite` `{status, red_axes}` at `phase_execute` start; `_record_run_health` writes `slowest_worker_sid`/`_min`/`truncated_worker_count` at finalize. Never gates. |

`_validate_run_json(data)` enforces these invariants on read:
- `pushed_at` and `push_error` are mutually exclusive (at most one is non-null).
- `pr_url` and `pr_error` are mutually exclusive.
- If `pr_url` is set, `pushed_at` must be set (cannot have a PR without a push).
- `paused_at`, `pushed_at`, and `killed_at` are mutually exclusive (a run cannot be in more than one terminal-or-paused state). If `paused_at` is set, `fly_machine_id` must also be set (you cannot pause a run without knowing where to resume it). If `killed_at` is set on a **Fly/EC2-shaped** sidecar, at least one of `fly_machine_id`/`ec2_instance_id` must still be set (you cannot have destroyed a machine you don't have a pointer to). A sidecar counts as Fly/EC2-shaped on **either** of two independent signals: (a) it shows **remote evidence** — `fly_machine_id`, `ec2_instance_id`, `volume_id`, or `image_tag` non-null; or (b) it **carries** a `fly_machine_id`/`ec2_instance_id` key at all, *even null*. Signal (b) is not redundant: the launcher's `_ensure_run_json` bootstraps a missing sidecar from `fly-machine.json`/`ec2-instance.json` as a single-key skeleton and writes that key as `null` when the source file lacks the id or fails to parse, so a genuinely corrupt remote kill can carry the key with no non-null evidence anywhere — invisible to (a) alone. A sidecar with **neither** signal is a local (`nerdctl`) kill — there is no machine id to point to, so the invariant is exempt (N7: this previously rejected every local `leerie kill`, since a local run never has `fly_machine_id`). The local path cannot trip (b) either: `_write_run_json` is merge-only with no skeleton and no call site passes those keys, and `_ensure_run_json` writes nothing at all when neither sidecar source exists.
- `sync_failed_at` is mutex-checked against `pushed_at` (a successfully pushed run can't be sync-failed) and against `killed_at` (a destroyed machine can't be sync-failed). When `sync_failed_at` is set, `fly_machine_id` must also be set — the running machine needs a pointer for the user to recover via `finalize`/`kill`.
- If `volume_id` is set, `fly_machine_id` must also be set — a Fly volume without a machine to attach it to is a corrupt sidecar (provision.sh always writes the two together).
- `killed_at` runs are not resumable; `resume` against a killed run errors with "run was killed at <ts>; start a new run instead."

A corrupt sidecar is flagged but does not block the rest of the system; `leerie list` will render that run with `status=corrupt-sidecar` and the user can inspect or delete the file.

`leerie list` derives a single status per run via `_derive_run_status(run_json, state_json)`. The taxonomy is checked in priority order — earlier rows fire first:

| Status | When it fires | Typical next step |
|--------|---------------|-------------------|
| `corrupt-sidecar` | `run.json` violates one of the four invariants above | inspect the file under `<state-root>/runs/<id>/run.json` |
| `push-failed` | `push_error` is set | re-run `git push -u origin leerie/<id>` after fixing the access issue |
| `pr-failed` | `pr_error` is set (and push succeeded) | re-run `gh pr create` manually using the command logged at finalize |
| `done-pushed-pr` | `pr_url` is set | the happy path: PR open, work merged locally |
| `done-pushed-no-pr` | `pushed_at` set but `pr_url` not | rare: push succeeded, PR wasn't attempted (e.g., gh removed between push and PR) |
| `sync-failed` | `sync_failed_at` set (and no `killed_at`) | the orchestrator finished but `fetch_branch` failed; the Fly machine is still running with un-synced work. Run `leerie finalize <id>` to retry, `leerie resume <id>` to inspect, or `leerie kill <id>` once work is safely on host. |
| `done` | `finished_at` set, no `pushed_at` | the user passed `--no-push`, or the orchestrator exited via `die()` after the run directory was created. `resume` re-enters `phase_execute` normally on the latter. |
| `paused` | `paused_at` is set | inspect/attach to the Fly Machine, then `leerie resume <id> --runtime fly` (DESIGN §6 *Remote pause-on-failure*) |
| `killed` | `killed_at` is set | terminal state — the machine was destroyed by `leerie kill`. Not resumable; start a new run instead. |
| `in-progress` | none of the above | the run is still active (or died very early); resume with `leerie resume <id>` |

`RUN_STATUSES` in `leerie.py` declares the ten values; a test coupling check asserts the tuple matches every value `_derive_run_status` can return.

`leerie list status <state>` filters the table to runs whose derived status matches. `<state>` accepts any value in `RUN_STATUSES`; invalid values produce an argparse error listing the allowed set. `list` short-circuits before any git/CLI preflight.

`state.json` fields. This table is canonical: every field the orchestrator
writes to `st.data` must appear here, and every field listed here must be
written somewhere in `orchestrator/leerie.py`. The coupling test in
`tests/test_state_fields.py` enforces parity in both directions against the
`STATE_FIELDS` tuple in `leerie.py`. Every `skip_*`/`strict_*`/`dangerously_*`
flag below is **re-resolved fresh on every run, including `resume`**, so the
user can flip it via CLI flag / env var / `leerie.toml` without editing state.

| Field | Shape | Purpose |
|-------|-------|---------|
| `task` | str | the task description passed on the command line |
| `started_at` | ISO-8601 str | wall-clock time at run start |
| `finished_at` | ISO-8601 str | wall-clock time at successful finalize |
| `plan_snapshot` | dict | `{subtasks, waves}` captured immediately after `_schedule()` returns and **before** `check_budget_feasibility` / `_validate_plan` — both of which `die()`. |
| `decompose_snapshot` | dict | `plan_snapshot`'s sibling for §5½ (P1) recursive decomposition: `phase_plan` writes the accumulated leaves after each top-level subtask finishes expanding under `_recursive_decompose`, so a mid-decomposition `WorkerError` (from either the `fit_judge` call or the coupled-minority `splitter` call — an auth failure, PID exhaustion) does not discard fit/split judgments already paid for on subtasks that already finished expanding — decomposition is routinely a large share of a run's total planning spend (DESIGN §6 *Credential strategy*). |
| `plans_after_classify` | list[dict] | per-phase planning checkpoint (DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a `waves` gate"): the `plans` list as it stood immediately after `phase_classify` completed and `st.save()`'d. |
| `plans_after_plan` | list[dict] | the per-phase planning checkpoint for `phase_plan` (post-recursive-decompose `plans`, DESIGN §6). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_reconcile` | list[dict] | the per-phase planning checkpoint for `phase_reconcile` (reconciled `plans`, DESIGN §6). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_overlap_judge` | list[dict] | the per-phase planning checkpoint for `phase_overlap_judge` (post-collision-resolution `plans`, DESIGN §6 *Cross-domain surface overlap*). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_adherence_gate` | list[dict] | the per-phase planning checkpoint for `phase_adherence_gate` (post-instruction-adherence-gate `plans`, DESIGN §6, §12 sibling). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_coverage_gate` | list[dict] | the per-phase planning checkpoint for `phase_planning_coverage_gate` (post-task-coverage-gate `plans`, DESIGN §8 *Independent adversarial verification*). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_filters` | list[dict] | the per-phase planning checkpoint written after the off-tree (`_filter_offtree_subtasks`) and already-satisfied (`_filter_satisfied_subtasks`) phase-3 filters both complete — the filtered `plans` immediately before `_schedule()`. |
| `satisfied_probe_cache` | dict[str, dict] | per-subtask `satisfied_probe` verdicts (DESIGN §6 "The satisfied-probe sweep needs finer-than-phase granularity"; §8 *Already-satisfied subtask elimination*), keyed by subtask id. |
| `planning_worktree` | str | absolute path to this run's disposable judgment-worker worktree (DESIGN §12 *Judgment-worker isolation*), created and reset by `scripts/planning-worktree.sh` via `_ensure_planning_worktree()`. |
| `repo_state_before_planning` | dict | `{head: str, porcelain: [str], refs: [str]}` for the USER'S REAL CHECKOUT, captured once before `phase_classify` and re-checked after every planning phase **and after every execute wave** by `_assert_repo_unchanged()`. |
| `active_oauth_token` | str \| None | the raw `CLAUDE_CODE_OAUTH_TOKEN` value currently selected for this run's `claude -p` spawns (DESIGN §6 *Multi-token rotation*; IMPLEMENTATION.md §3 *Multi-token rotation*). |
| `waves` | list[list[str]] | scheduled subtask ids per wave (from `_schedule`) |
| `completed_waves` | int | index of the next wave to run (resume cursor) |
| `subtask_status` | dict[str, str] | per-subtask terminal status |
| `accepted_blocked` | dict[str, dict] | one entry per subtask waived via `leerie accept-blocked`, written by the LAUNCHER mutator rather than by the orchestrator: `{at, previous_status, blocker, forced}`. The `die()` that sends an operator to that verb says "See ... |
| `blocked` | dict[str, str] | per-subtask blocker reason when a wave aborts |
| `worker_count` | int | running total of `claude -p` invocations against `max_total_workers` |
| `decompose_worker_count` | int | running total of `claude -p` invocations spent inside `_recursive_decompose` (fit_judge + splitter, including the label-only migration splitter), against `decompose_budget_share * max_total_workers`. |
| `decompose_share` | float | decomposition's share of the run's realized spend (`decompose_worker_count / worker_count`), recorded by `_warn_decomposition_share` after `phase_plan`'s expansion loop completes. |
| `current_phase` | str | the orchestrator's active phase string (e.g. |
| `telemetry` | dict | calls, cost_usd, input_tokens, output_tokens — printed at run end |
| `categories` | list[str] | classifier output, post-whitelist filtering |
| `classifier_questions` | list[dict] | intent questions the classifier surfaced |
| `prescribed_procedure` | dict | classifier's language→JSON signal declaring whether the user prescribed an explicit procedure/command-sequence: `{is_prescribed, commands, forbid_manual, evidence}`. Empty dict when the classifier omitted the field. |
| `required_items` | list[dict] | classifier's language→JSON signal declaring the task's explicit, enumerable requirements: `[{item, source_ref}]`. Empty list when the classifier found nothing genuinely enumerable (the common case). |
| `likely_already_satisfied` | bool | classifier's additive signal that the task's deliverable already appears present on HEAD (DESIGN §8). Written on every `phase_classify` invocation, default `False`. |
| `likely_already_satisfied_evidence` | str | required non-empty whenever `likely_already_satisfied` is `True` (`EMPTY_EVIDENCE` check); default `""` |
| `answers` | dict[str, str] | user answers to classifier questions (and source-of-truth) |
| `artifact_registry` | list[dict] | shared artifact vocabulary (DESIGN §5 *Artifact-registry worker*). |
| `needs_source_of_truth` | bool | whether classifier asked for source-of-truth disambiguation. |
| `source_of_truth_pref` | str | resolved preference (`codebase` / `research` / `both`) |
| `clarify` | bool | whether asking the user is allowed for this run (resolved from `--clarify` / `LEERIE_CLARIFY` / `leerie.toml` / default `False`) |
| `dangerously_skip_permissions` | bool | the operator's tooling escape hatch. Resolved from `--dangerously-skip-permissions` / `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` / `leerie.toml` / default `False`. |
| `dangerously_force_strict_output` | bool | whether this run forced constrained decoding via the per-run loopback proxy (`--dangerously-force-strict-output` / `LEERIE_DANGEROUSLY_FORCE_STRICT_OUTPUT` / `dangerously_force_strict_output` in leerie.toml). |
| `skip_overlap_judge` | bool | whether the phase 2¾ `plan_overlap_judge` worker is suppressed even on multi-planner runs (DESIGN §5 *Cross-domain surface overlap*). |
| `skip_adherence_check` | bool | whether the instruction-adherence gate (the deterministic prescribed-command-coverage floor + the `adherence_judge` worker in the planner check loop) is suppressed. |
| `skip_coverage_check` | bool | whether the phase 2⅞½ task-coverage gate (a single advisory `task_coverage_judge` invocation since 2026-08-04; the deterministic `check_required_items_coverage` floor it used to compose with was deleted) is suppressed. |
| `skip_completeness_check` | bool | whether the conformer's gating `solution_defects` completeness axis (DESIGN §9 *The one gating axis: solution completeness*) is demoted to advisory. |
| `skip_integration_check` | bool | whether `integrate_wave`'s `integration_judge` behavioral-defect gate (DESIGN §8 *Independent adversarial verification*) is suppressed entirely — no worker spawn for any subtask in this run. |
| `skip_budget_check` | bool | whether `check_budget_feasibility()` (DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*) is suppressed. Resolved from `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml` / default `False`. |
| `skip_satisfied_check` | bool | whether `_filter_satisfied_subtasks()` (DESIGN §8 *Already-satisfied subtask elimination*) is suppressed. Resolved from `--skip-satisfied-check` / `LEERIE_SKIP_SATISFIED_CHECK` / `leerie.toml` / default `False`. |
| `strict_conformer` | bool | whether the conformer phase is blocking instead of advisory (DESIGN §9 *Post-work conformance*, "Opt-in strict mode" paragraph). Resolved from `--strict-conformer` / `LEERIE_STRICT_CONFORMER` / `leerie.toml` / default `False`. |
| `subtask_tests` | str | How much of the repo's suite each per-subtask conformance round measures (DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*). |
| `skip_base_baseline` | bool | whether the base-tree health baseline (DESIGN §9 *Base-tree health baseline*) is suppressed. Resolved from `--skip-base-baseline` / `LEERIE_SKIP_BASE_BASELINE` / `leerie.toml` / default `False`. |
| `skip_repo_map` | bool | whether the P6 repo-map structural context (DESIGN §5½ (P6) *Codebase structural map*) is suppressed. Resolved from `--skip-repo-map` / `LEERIE_SKIP_REPO_MAP` / `skip_repo_map` in `leerie.toml` / default `False`. |
| `cgroup_containment` | dict | recorded by the fail-closed gate (`_enforce_and_record_cgroup_containment`, in `_run_phases` just before the first worker spawns) (DESIGN §6 *Memory containment*): `{enforced: bool, hierarchy: "v2"\|"v1"\|null}`. |
| `verbosity` | str | resolved verbosity level (`quiet` / `normal` / `stream` / `debug`); re-resolved fresh on every run, including `resume`, so the user can dial up or down without editing state |
| `inspect_dirs` | list[str] | extra absolute paths granted to inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) via `--add-dir`. |
| `integrator_warnings` | dict[str, str] | non-fatal commit warnings from `integrate_wave` (non-fatal signal log) |
| `scope_warnings` | dict[str, dict] | oversized-diff warnings from `check_diff_scope` (non-fatal signal log) |
| `conformance` | dict[str, dict] | per-subtask conformer output and `conformance_warnings` (non-fatal signal log). |
| `blt_results` | dict[str, dict] | Per-run memo of orchestrator-measured build/lint/test verdicts (DESIGN §9). |
| `unreviewed_subtasks` | list[str] | subtask ids whose conformer produced no result at all (worker crash, or the 5400 s timeout), so a subtask that was never reviewed is distinguishable from one that passed. |
| `symptom_findings` | dict[str, list[str]] | subtask id -> `check_symptom_evidence` findings, for subtasks whose plan entry declares `fixes_reported_symptom: true` (NOT those whose id begins `bugfix-`: ids are re-homed by plan merges and synthesised for verification-only work, so the prefix is not evidence a symptom exists) (DESIGN §9 *A stale finding is not a bug*). |
| `provision` | dict | output of `phase_provision` (DESIGN §6½). |
| `external_preconditions` | list[dict] | planner-declared `extent: external` `requires` entries collected during `phase_reconcile` (DESIGN §5 `requires.extent`). Each item is `{tag, reasons: [{sid, reason}, …], originating_subtasks: [sid, …]}`, deduped by tag. |
| `dropped_subtasks` | dict[str, dict] | subtasks soft-dropped pre-schedule. |
| `provider_subset_sids` | list[str] | sids flagged at plan time by `_warn_provider_subset_subtasks()` — every file in the subtask's `files_likely_touched` is already owned by an ordered predecessor it depends on (DESIGN §5 *Provider-subset subtasks*). |
| `conditional_drops` | dict[str, dict] | planner-emitted consumer subtasks dropped by the reconciler's `conditional_drop` resolution op (DESIGN §5) — i.e. the planner authored the subtask as "no-op if X" and X turned out to be unresolvable. |
| `external_twin_demotions` | list[dict] | `unresolvable` entries rescued by `_demote_unresolvable_with_external_twin` (DESIGN §5 *The external twin*) — a consumer declared a tag `in_plan` while another subtask declared the same capability `extent: external`. |
| `speculative_collapse_drops` | list[str] | subtask sids mechanically pruned by dead-subtask elimination (DESIGN §5) — fully-speculative subtasks whose every `in_plan` requires was unresolvable because the provider domain returned 0 subtasks. |
| `overlap_replan_done` | bool | set once when `phase_overlap_judge` answers an `unresolvable` collision with a **scoped re-plan** instead of `die()`ing (DESIGN §5). |
| `plan_overlap_judge` | dict | full output of the phase 2¾ `plan_overlap_judge` worker (DESIGN §5 *Cross-domain surface overlap*) — `{collisions: [{a_sid, b_sid, artifact, resolution, reason, merge_feasibility?}, …]}`. |
| `plan_overlap_applied` | list[dict] | post-apply mutation summary for the phase 2¾ judge. |
| `duplicate_provider_merge_applied` | list[dict] | post-apply mutation summary for merges the deterministic `check_duplicate_providers` floor synthesized and applied via `_duplicate_provider_merge_collisions` + `_apply_overlap_collisions` (M11 DECISION — see "Phase 2¾ checks" above). |
| `adherence_gate` | dict | audit record from the phase 2⅞ instruction-adherence gate (`phase_adherence_gate` — see "Instruction-adherence gate" above) — `{judge: <adherence_judge output>, floor_issues: list[str]}`. |
| `coverage_gate` | dict | audit record from the phase 2⅞½ task-coverage gate (`phase_planning_coverage_gate`, DESIGN §8 *Independent adversarial verification*) — the final `task_coverage_judge` output `{task_covered, coverage_gaps, rationale}`. |
| `classification_coverage_gate` | dict | audit record from `phase_classification_gate` (DESIGN §8 *Independent adversarial verification*) — the final `classification_judge` output `{categories_reviewed, miscategorizations, rationale}`. |
| `wiring_gate` | dict | audit record from `phase_wiring_gate` (DESIGN §5 *A wiring re-check on the fully-merged plan*, §8) — the final `wiring_judge` output `{plan_reviewed, wiring_defects, rationale}` plus a `repairs` array of `{sid, tag, provider, channel}` for every edge the gate added (`channel` is `"tag"`, `"id"`, or `"cofile_cluster"`; on the id channel `tag` and `provider` are both the named subtask id). |
| `provision_recipe_gate` | dict | audit record from `phase_provision_gate` (DESIGN §8, §6½) — the final `provision_judge` output `{recipe_reviewed, recipe_failures, rationale}`. |
| `integration_gate` | dict[str, dict] | per-sid audit record from `integrate_wave`'s `integration_judge` gate — `{sid: {defects: list[str], advisories: list[str], merge_commit_sha: str, accepted: bool}}`. |
| `integration_defects` | dict[str, list[str]] | per-sid flat mirror of `integration_gate[sid]["defects"]` for the sids with a currently-gating (not-yet-accepted) finding — the record `accept-integration` clears (popped, along with the whole key when it empties, once accepted or once a re-invoked judge comes back clean). |
| `no_work_required` | bool | set to `True` by `_finish_no_work_run` when every planner returns `status: "ready"` with `subtasks: []` (DESIGN §8 *The cleared-but-empty terminal state*). |
| `no_work_reasons` | dict[str, str] | per-domain `confidence.basis` quoted from each planner's empty-but-ready output, recorded alongside `no_work_required` for audit. |
| `working_branch` | str | the user's branch at the moment `phase_classify` runs (`git rev-parse --abbrev-ref HEAD`). |
| `pr_base_branch` | str | the final branch this run's PR merges into — overridable via `--pr-base-branch` / `LEERIE_PR_BASE_BRANCH` / `pr_base_branch` in `leerie.toml` (resolved by `resolve_pr_base_branch`, CLI > env > file precedence, mirroring `resolve_pr_template`). |
| `leerie_version` | str | the leerie version string from `.claude-plugin/plugin.json`, seeded once at the run's original start and **immutable across resumes** (N38) — a resume no longer overwrites it with whatever is installed at resume time, since doing so made a resumed run's failures read as attributable to the wrong release. |
| `leerie_commit` | str \| null | short sha of `$LEERIE_REPO`'s HEAD, forwarded by the launcher as `LEERIE_COMMIT`, seeded once at the run's original start and **immutable across resumes** (N38), same rationale as `leerie_version`. |
| `leerie_versions` | list[dict] | append-only resume history (N38), distinct from the immutable `leerie_version`/`leerie_commit` pair above. |
| `dep_capture_done` | bool | set to `True` in `state.json` by `capture_repo_deps` after a successful write. |


`pending-questions.json` (written by `gather_answers` on non-TTY exit, read by
the plugin skill in `commands/leerie.md`):

| Field | Shape | Notes |
|-------|-------|-------|
| `questions` | array of `{id, question, why_underivable?}` | the classifier-surfaced intent questions not already in `--answers` |

`answers.json` (written by the plugin skill, passed back via
`--answers <state-root>/answers.json`):

| Field | Shape | Notes |
|-------|-------|-------|
| `<question id>` | string | one entry per question id from `pending-questions.json.questions[].id` |
| `source_of_truth` | `"codebase"` / `"research"` / `"both"` | optional; overrides the resolved preference for this run |

The checkpoint schema — seven required sections, enforced by
`_validate_checkpoint()`: *Frozen success criteria*, *Current status*, *Files
touched*, *Decisions made*, *Evidence gate status*, *Next action*, *Open
unknowns*. Three layers: (a) every header present; (b) every section
non-whitespace; (c) the five handoff-context sections reject single-token
placeholders (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`,
trailing punctuation stripped first) — *Decisions made* and *Open unknowns*
accept them. When `worktree_root` is passed, a freshness check also requires
every *Files touched* path to still exist or carry a `[deleted]` annotation.

`claude_p()`'s CLI-reported `num_turns` is unreliable against `--max-turns`
(computed from two different counters depending on which exit path fired).
`terminal_reason` is the trustworthy cap signal; `tests/test_turn_cap_signal.py`
guards against reintroducing the `num_turns` comparison.

Maps to `DESIGN.md`: §10 (handoff, coordination-artifact location), §9 (criteria
locking).

---

## 9. Structured-output schemas

`claude_p()` validates each worker's payload against a schema keyed by worker
type. `confidence` is optional on every worker schema (a required object
shape would corrupt payloads under anthropics/claude-code#49747) but when
present follows `_confidence_schema(...)`: axis score(s) 1–10, `basis`
(string, required), `falsifiers_tested` / `contradictions_reconciled` (arrays
of strings, optional). There is no `gap_to_close` field — a low score's gap
is stated in `basis`.

Required fields, current shape:

- **classifier** — required: `categories` (array). Optional: `questions`
  (`{id, question, why_underivable?}[]`), `source_of_truth_question` (bool —
  flags relevance only; the orchestrator's preference resolution supplies the
  value, default `both`), `prescribed_procedure` (`{is_prescribed, commands[],
  forbid_manual, evidence}` — language→JSON signal for an explicit procedure
  vs. a goal description; `check_classifier_output` requires non-empty
  `evidence` when `is_prescribed`; persisted to `st.data["prescribed_procedure"]`,
  default `{}`), `likely_already_satisfied` (bool) + `likely_already_satisfied_evidence`
  (required non-empty when true — DESIGN §8; OR-preserved across
  `phase_classification_gate` re-classify rounds so a silent round never
  clears an earlier true finding).
- **planner** — required: `domain`, `subtasks`, `status` (`ready` / `blocked`
  — DESIGN §8 planner gate; a `ready` plan may carry empty `subtasks`, the
  cleared-but-empty terminal state). `confidence` keys when present:
  `task_understanding`, `decomposition_quality` (1–10), `basis`. Each subtask:
  `{id, title, success_criteria_seed, intent, scope_note, files_likely_touched,
  depends_on, requires, provides, size, investigation_notes, runs_commands}`.
  `requires` is `{tag, extent: "in_plan"|"external", reason (required
  non-empty when external)}[]` — `in_plan` is satisfied by a sibling's
  `provides` (a graph edge); `external` is a planner-declared prerequisite
  outside *this run's* graph — either outside the build graph entirely
  (another repo, ops runbook, manual step), or producible by code but owned
  by another run the task names (sibling phase document, earlier phase), or
  fenced off by the task itself (the task declares a surface out of scope
  and the capability's only implementation site lies on it) — surfaced in
  `plan.json`'s `preconditions` (DESIGN §5 `requires.extent`). In every case
  `reason` must name the owner; the discriminating test is "is it in this
  run's graph?", not "could any code produce it?".
  `provides` is bare strings. `size` is `small`/`medium`/`large` — `large`
  triggers the size-resolution retry loop, `_validate_plan` OVERSIZED-dies if
  it survives. `runs_commands` (optional strings) feeds
  `check_prescribed_command_coverage(prescribed_procedure, subtasks) ->
  list[str]`, the deterministic primary layer of the instruction-adherence
  gate (token-subset match of `prescribed.commands` against the union of
  `runs_commands`, emitting `PRESCRIBED_CMD_UNRUN: ...`; short-circuits to
  `[]` with no prescribed procedure). Tested in
  `tests/test_prescribed_cmd_coverage.py`; wired into `phase_adherence_gate`.
- **implementer** — required: `subtask_id`, `status` (`complete` /
  `incomplete-handoff` / `blocked` / `failed` / `needs-clarification`).
  `confidence`: `root_cause`, `solution` (1–10), `basis`. Optional: `branch`,
  `criteria_results` (`{criterion, met, evidence}[]` — telemetry only, does
  not gate), `checkpoint_path`, `blocker`, `summary`, `clarification_question`
  (DESIGN §11: `{id, question, why_underivable}`, all required together with
  `checkpoint_path`), `artifacts` (DESIGN §5: `{name, kind: "markdown"|"json"|"text",
  content, summary?}[]` — deliverables for downstream subtasks).
- **integrator** — required: `incoming_subtask`, `status` (`resolved` /
  `design-conflict` / `failed`). `confidence`: `_confidence_schema(["resolution"])`.
  Optional: `resolution_summary`, `diagnosis` (fallback on non-`resolved`).
- **rebaser** — required: `status` (`rebased` / `irreconcilable` / `failed`),
  `final_branch_state`. `confidence` mirrors `integrator`. Optional:
  `resolution_summary`, `diagnosis` (required in practice when
  `irreconcilable`). A scoped, fully-agentic §12 exception (DESIGN §6
  *Finalization* "Rebase-onto-base before push") — the worker performs the
  whole rebase workflow itself, and `check_rebaser_worktree_state()`
  mechanically re-verifies the claimed status against actual git state.
- **conformer** — required: `subtask_id`, `rules_files_read` (strings, empty
  when none found), `rule_violations` (`{status: fixed|residual, rule, fix,
  evidence, why_not_fixed}[]`), `file_updates` (`{kind: docs|tests, path,
  reason}[]`), `build`/`lint`/`tests` (each `{ran, passed, command, summary?}`
  — `ran: false` when not applicable), `summary`, and `solution_defects`
  (`{kind: unhandled_input|unhandled_path|missing_guard|sibling_site_unedited|
  wrong_selector|decoy_or_shortcut, concrete_case, where, why_ships_a_defect}[]`,
  all three fields non-empty). `solution_defects` is the **gating** axis
  (DESIGN §9 *The one gating axis: solution completeness*) — the conformer's
  independent adversarial attack on the diff; non-empty retries the
  implementer with the defects as mandatory criteria (bounded by
  `completeness_retry_rounds`), or blocks on exhaustion.

  `rule_violations`/`file_updates` are wire-flattened discriminated arrays
  (mirroring `SCHEMAS["reconciler"]`'s `tag_ops` technique) to keep the
  schema small for the strict-output proxy's grammar compiler.
  `_expand_conformer_output()` fans them back into `rule_violations_fixed`,
  `rule_violations_residual`, `docs_updates`, `tests_updates` right after the
  worker call (an unrecognised `status`/`kind` is dropped; pinned by
  `tests/test_conformer_schema_shrink.py`). `_validate_conformance_result()`
  then enforces cross-field invariants on the expanded shape: residuals
  require non-empty `rules_files_read`, every `rule_violations_fixed` cites
  a `rule`, every `docs_updates`/`tests_updates` `path` exists in the
  worktree, every `solution_defects` item carries non-empty
  `concrete_case`/`where`. `confidence`: `conformance` (1–10), `basis`.
- **judge** — required: `passed` (bool, true only when all three dimensions
  are true), `dimensions` (`{schema_ok, factual_ok, hallucination_ok}`),
  `rationale` (1–3 sentences), `suggested_fixes` (empty when `passed: true`).
  Used by `phase_judge()` / `_judge_capture()`, post-run. `prompts/judge.md`
  carries the rubric.
- **patch_generator** — required: `anchor` (exact substring of the current
  system prompt, validated against live prompt text before applying),
  `replacement`. Optional: `strategy`, `pivot_reason`. Used by the self-heal
  skill's patch-generation worker, post-run.

Schemas are embedded as Python dicts in `leerie.py` and serialized inline.

Maps to `DESIGN.md`: §7, §14.

---

## 10. Telemetry — NDJSON envelope and call_type mapping

Maps to `DESIGN.md`: §14.

### NDJSON envelope schema

Every `claude_p()` invocation appends one JSON object (one line) to
`<state-root>/runs/<run-id>/calls.ndjson`, opened for append at run start and
never truncated. Not read by the orchestrator at runtime — reading is a
post-run operation performed by the judge and heal skills.

| Field | Type | Notes |
|-------|------|-------|
| `call_id` | str (UUID v4) | unique identifier for this invocation; referenced by judge verdicts |
| `run_id` | str | matches the run directory name under `<state-root>/runs/` |
| `call_type` | str | one of the `WORKER_TYPES` schema keys, plus the post-run/finalize workers `pr_writer`, `judge`, `patch_generator`, `dep_capture` |
| `model` | str | the model alias passed to `--model` (e.g. `opus`, `sonnet`) |
| `system_prompt` | str | the full system prompt injected via `--append-system-prompt` |
| `user_content` | str | the user-turn content passed to the worker |
| `response_content` | str | the worker's raw text response (before schema parsing) |
| `parsed_ok` | bool | whether `structured_output` was present and schema-valid |
| `input_tokens` | int | total input via `_usage_input_tokens(usage)` — `input_tokens` plus `cache_creation_input_tokens` plus `cache_read_input_tokens` (every worker runs with prompt caching, so the bare API field alone understates weight) |
| `output_tokens` | int | `usage.output_tokens` from the CLI envelope |
| `latency_ms` | int | wall-clock milliseconds from subprocess start to return |
| `success` | bool | whether the call produced a schema-valid result |
| `failure_kind` | str \| null | derived by `_classify_failure_kind`: `api_error` (`:auth`/`:quota`/`:overload`/`:transport` suffix), `incomplete` (non-`completed` `terminal_reason`), `malformed_tool_call` (CLI tool-call-parse-failure diagnostic — `anthropics/claude-code#49747`), or `schema_parse_failed` (the dominant case). Rate-limit/out-of-credits/hard-crash failures raise `RateLimitedExit`/`WorkerError` past the capture block and get no record. |
| `cgroup_applied` | bool | whether per-worker cgroup memory/PID containment was active for this spawn |
| `ts` | str (ISO-8601) | UTC timestamp when the line is written |

The judge skill consumes `system_prompt`/`user_content`/`response_content`/
`parsed_ok`; the heal loop replays a call against a patched prompt using
`system_prompt`/`user_content`. `call_type` partitions calls for per-type
analysis.

### Capture file path

```
<state-root>/runs/<run-id>/calls.ndjson
```

One file per run. Written by the orchestrator; the judge and heal skills
read it as a post-run harvest.

### Reporting — the `--report` verb

`resume` takes its run-id positionally OR as `--run-id`; both are
equivalent. `main()` pops the bare verb from `argv[0]`, then — for `resume`
only — takes an optional leading non-flag positional via
`_extract_resume_run_id()` before `ap.parse_args()` and assigns it to
`args.run_id`; passing both with different values `die()`s. `list` is
excluded (it has its own positionals: `list status paused`, `list chains`).
Pinned by `tests/test_resume_positional_run_id.py`.

`leerie --report [RUN_ID]` is a read-only telemetry report for a single run;
like `list` it exits without running orchestrate. Run selection reuses
`resolve_run_id` (exact-match a passed id, else auto-pick the most recent
run, else die). Unlike `resume`, `--report` does not pass
`resumable_only=True` — reporting on a finished run is the normal case. It
prints a header (status, duration, `state.json`'s `telemetry` aggregate —
calls, `$cost`, in/out tokens); a per-`call_type` breakdown from
`calls.ndjson` (count, tokens, average latency, failure count, sorted by
call count via `_aggregate_calls`); a `failures by kind` rollup when any
failed; and a memory-peak line (peak `rss_kb`, max `open_fds`/`thread_count`)
from `memory.ndjson` via `_memory_peak`. All inputs already exist on disk;
`--report` adds no new telemetry.

### call_type → prompt-resolution table

Each `call_type` maps to exactly one system-prompt source; no call_type is
ever spawned without one, and no prompt is shared between call types.

| call_type        | Prompt source | Notes |
|------------------|---------------|-------|
| `classifier`     | `prompts/classifier.md` | read from disk |
| `planner`        | `prompts/planner.md` | read from disk |
| `reconciler`     | `prompts/reconciler.md` | read from disk |
| `satisfied_probe`| `prompts/satisfied_probe.md` | per-subtask base-tree already-satisfied probe, phase 3 (DESIGN §8) |
| `provision`      | `prompts/provision.md` | LLM fallback when the lockfile table misses (DESIGN §6½) |
| `implementer`    | `prompts/implementer.md` | read from disk |
| `integrator`     | `prompts/integrator.md` | read from disk |
| `conformer`      | `prompts/conformer.md` | read from disk; carries the gating `solution_defects` section |
| `classification_judge` | `prompts/classification_judge.md` | independent adversarial verifier of the classifier's category set (DESIGN §8) |
| `wiring_judge`   | `prompts/wiring_judge.md` | independent semantic-wiring verifier of the reconciled plan (DESIGN §5, §8) |
| `provision_judge`| `prompts/provision_judge.md` | independent verifier of the install recipe vs image/runtime (DESIGN §6½, §8) |
| `task_coverage_judge`| `prompts/task_coverage_judge.md` | independent adversarial verifier of plan coverage (DESIGN §8); wired into `phase_planning_coverage_gate` |
| `artifact_registry` | `prompts/artifact_registry.md` | pre-planning shared-vocabulary worker, phase 2 (DESIGN §5) |
| `pr_writer`      | `prompts/pr_writer.md` | invoked by `phase_finalize` when `push_will_happen` is true |
| `judge`          | `prompts/judge.md` | post-run skill |
| `patch_generator`| `prompts/patch_generator.md` | post-run heal-loop worker |

`resolve_prompt(call_type: str) -> tuple[str, str, str]` loads a worker's
system prompt: given any member of `WORKER_TYPES`, returns `(source_kind,
content, location_hint)` where `source_kind` is `"file"` and `location_hint`
is `"prompts/<call_type>.md"`. Raises `ValueError` for an unknown
`call_type`.

### _replay_capture — primitive for judge and heal-loop replays

```python
async def _replay_capture(
    record: dict,
    *,
    override_system_prompt: str | None = None,
    cwd: str | None = None,
) -> tuple[dict, dict]:
```

Given one NDJSON record, reconstructs the `claude_p()` invocation with the
captured `system_prompt`, `user_content`, `call_type` (used as `schema_key`),
and `model`, and returns `(envelope, structured_output)` from the new
invocation. `override_system_prompt` lets the heal loop replay with a
patched prompt in place of the captured one. Replays use a throwaway
in-memory `_ReplayState` and `_suppress_capture=True` — they never write to
`calls.ndjson`. Both judge (n=1 replay, then score) and heal (n=N replays,
baseline vs patched) build on this primitive.

---

## 11. Verification status of the code

Mirrors `DESIGN.md` §15, at the code level.

enforcement functions. No coverage target is set. It covers, by subsystem:
core resolvers and validation (incl. `test_state_fields.py`, pinning
`STATE_FIELDS` parity against both the §8 field table and every
`st.data[...]` write); planning (P6 repo map, P1 recursive decomposition);
worker isolation/prompts/tool scoping (incl. `test_judgment_worker_isolation.py`,
which pins that judgment workers never receive
`--dangerously-skip-permissions`); orchestrator wiring (incl.
`test_no_undefined_names.py`, a whole-module `symtable` scan for undefined
names — ruff F821 without the dependency); conformance (DESIGN §9, incl.
`test_conformance_clean_delta.py`'s delta-not-verdict discipline and the
three `{test_files}`-proxy files detailed below); container image/provisioning;
the `leerie config` verb; judge/heal skills; the group verb (DESIGN §20); and
finalize/host-side bash (`test_host_finalize_sh.py`'s no-push/already-pushed
idempotency and PR-base-branch-override contract). The full per-file,
per-incident inventory — which test covers which surface, and the specific
traps each one pins against regressing — is in `docs/TESTING.md`, not
duplicated here.

The `{test_files}`-proxy files carry the per-file detail a reader scans to
find what is already covered:

| Test file | What it covers |
|---|---|
| `test_test_files_proxy.py` | `_is_test_file` / `_render_scoped`'s `{test_files}` tier / `_select_subtask_axes`' fallback (DESIGN §9). The load-bearing case is the empty-AFTER-filter one: `files` is NON-empty so the pre-existing empty-list guard does not fire, yet every member is a non-test path — rendering there yields a bare `pytest`, which runs EVERYTHING, the same inversion the `{files}` rule forbids reached by a different route. Also pins that the shipped vitest/jest `{files}` templates take SOURCE files on purpose, the `lstrip("./")`-vs-`removeprefix` case, and declared `test_file_globs` REPLACING rather than extending the built-ins. |
| `test_scoped_proxy_corpus.py` | The measured basis for the `{test_files}` tier, frozen against `tests/fixtures/scoped_proxy_corpus/corpus.json` — 36 REAL per-subtask diffs recovered from leerie's own run branches. Exists because the ratio was first taken from the planner's `files_likely_touched` and was badly wrong (40% test-touching predicted vs 94% real). Each row must be ONE subtask's work (an integration merge's FIRST-PARENT diff, not a cumulative two-dot diff), and the fixture must retain its source-only rows or the canonical-fallback safety property goes untested. |
| `test_scoped_degrade_warning.py` | `_warn_scoped_degraded_once` (DESIGN §9): `scoped` is the default and an unresolvable proxy falls back to canonical, so a pytest repo paid the full oracle once per subtask with nothing saying so. The anti-vacuity partner `test_silent_when_a_proxy_resolves` is mandatory: without it a warning that fired unconditionally would pass, turning the signal into noise on the ~99% of repos where scoping works. Two wiring guards — the call precedes the baseline block, and an AST check that it is NOT nested under the `skip_base_baseline` guard (sentinel-skipped on resume, i.e. silent on exactly the runs that most need telling). |

**CI workflows:**

| Workflow | Coverage |
|----------|----------|
| `.github/workflows/syntax.yml` | AST-parses every Python file under the repo plus `tests/`; runs `pytest tests/test_no_undefined_names.py` for the undefined-name scan (one implementation, not reimplemented in the workflow). Path-filtered to the Python source trees. |
| `.github/workflows/shellcheck.yml` | `shellcheck -x scripts/*.sh`. Path-filtered to `scripts/**/*.sh`. |
| `.github/workflows/release.yml` | On a `chore(release): X.Y.Z` commit landing on `main`, creates the `vX.Y.Z` tag and matching GitHub Release, or fails loudly if either is missing at job end; both idempotency checks gate independently. |

Most workflows carry a `concurrency:` block keyed on `github.ref` with
`cancel-in-progress: true`; `release.yml` is the exception
(`cancel-in-progress: false`, since cancelling mid-release is worse than
letting it finish). Dependabot tracks the GitHub-Actions ecosystem weekly.

**Not tested.** No worker has run against a live `claude -p` as part of
automated CI. The flag contract in §3 is from CLI documentation, not
observed runs. `claude_p` itself is not unit-tested directly — meaningful
testing requires a stub or live `claude` binary, a separate end-to-end tier
exercised manually.
