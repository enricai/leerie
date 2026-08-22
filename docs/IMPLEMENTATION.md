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

`scripts/container-entry.sh` is exec'd as PID 1, running as **root**
(the Dockerfile intentionally omits `USER leerie` — see DESIGN §6
*Memory containment* for why root at PID 1 is required to launch the
cgroup broker). Sketch of the relevant final exec:

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

**Rootless containerd.** Under rootless containerd (Linux), rootlesskit
maps the host UID to container UID 0. The entrypoint detects this by
checking `/proc/self/uid_map` (non-zero host-start field on the first
line → `ROOTLESS=true`) and, when true, also extracts `HOST_UID` (that
line's second field — the real host UID rootlesskit mapped container UID
0 to). When rootless:

- The `chown leerie: /work` and `runuser -u leerie --` steps are
  skipped — container "root" IS the host user, so privilege drop would
  break bind-mount access and chown would reassign to the subuid range.
- `CGROUP_ROOT` is anchored at
  `/sys/fs/cgroup/user.slice/user-$HOST_UID.slice/user@$HOST_UID.service`
  instead of the top-level `/sys/fs/cgroup` — the mapped host UID has no
  privilege over the true top level (root-owned, mode 0555), but systemd
  already delegates this subtree to that UID's login session. Any cgroup
  the UID creates underneath it (via `mkdir`) inherits that UID's
  ownership on every auto-created interface file, including `pids.max` /
  `memory.max` — unlike a directory merely `chown`ed after creation. This
  is passed to `cgroup-broker.py` via `LEERIE_CGROUP_V2_ROOT` (its
  `V2_ROOT`, default `/sys/fs/cgroup` when unset — every non-rootless
  case); the v1/hybrid split-hierarchy path (`V1_ROOT`, Fly-only) is
  never overridden. The broker itself needs no separate privileged
  identity here: it's launched at the same rootlesskit-mapped identity
  the whole container runs as, which is exactly what `CGROUP_ROOT` is
  delegated to. Cross-scope worker-PID migration into `leerie.slice`
  still works because cgroup v2 only requires write access to the
  destination and the nearest common ancestor (not the source), and that
  ancestor — `user@$HOST_UID.service` — is what's delegated. See DESIGN
  §6 *Rootless exception* for the full mechanism.
- On hosts where this delegation doesn't hold (non-systemd rootless init,
  or a systemd host that doesn't delegate `pids`/`memory` into the
  per-session slice), the slice-setup writes (`|| true`) and the broker's
  write-then-read-back check in `_detect()` both fail silently — same as
  any other containment-incapable host — and the fail-closed containment
  gate stops the run unless the operator passes
  `--dangerously-allow-uncapped`.
- On macOS (Darwin), the launcher unconditionally sets the `rshared`
  bind-mount — Colima's VM always runs rootful containerd with cgroup
  v2 and shared propagation, but the host has no `/sys/fs/cgroup` to
  probe. On native rootful Linux the launcher adds the same `rshared`
  mount unconditionally. Rootless containerd is its own branch, gated on
  the `containerd-rootless/child_pid` sentinel, and uses a **plain**
  bind-mount with no `bind-propagation` flag: rootlesskit's
  `--propagation=rslave` demotes `/sys/fs/cgroup` to a slave mount, which
  is incompatible with `bind-propagation=rshared`. Only read/write
  visibility into the already-mounted cgroupfs is needed here — not
  propagation of new mount events — so the plain bind-mount is
  sufficient. When cgroup v2 isn't present at all, the mount is skipped,
  the broker probe fails, and the fail-closed gate
  (`_enforce_and_record_cgroup_containment`) stops the run unless
  `--dangerously-allow-uncapped` is set.

**User-namespace remap.** Claude Code rejects
`--dangerously-skip-permissions` from UID 0. The rootless entrypoint
uses `unshare --user --map-user --map-group` to remap outer UID 0 to
the `leerie` user in a nested user namespace, so the orchestrator runs
as non-root and the flag is accepted. The OCI default seccomp profile
blocks `unshare(CLONE_NEWUSER)`, so the launcher passes
`--security-opt seccomp=unconfined` for rootless runs (gated on
`containerd-rootless/child_pid`). See DESIGN.md §6.

The orchestrator's source lives at `/opt/leerie-image/`. It is present
in two ways depending on execution mode:

- **Local runs:** the launcher bind-mounts `$LEERIE_HOME` read-only at
  `/opt/leerie-image`. Iterating on `orchestrator/leerie.py` does not
  need an image rebuild — the bind mount shadows the baked copy and
  the host file is picked up on the next `leerie` invocation.
- **Fly.io Machines (remote):** there is no bind mount. The Dockerfile
  `COPY` instructions bake `orchestrator/`, `scripts/`, `prompts/`,
  and `.claude-plugin/` into the image at `/opt/leerie-image/` so the
  entrypoint resolves without any host-side path. A new leerie version
  requires rebuilding and pushing the image (see §0.5 "Registry publish
  path").

### Bind-mount table

The launcher passes the following mounts to `nerdctl run`:

| Host path | Container path | Mode | Purpose |
|---|---|---|---|
| `$(pwd -P)` (user repo) | `/work` | rw | The repo leerie operates on. Git worktrees live here. Writes flow back to the host so `resume` works across container runs. Run state (`.leerie/`) is mounted separately via `/leerie-state` (see below). |
| `$LEERIE_STATE_HOST_DIR` (resolved host state dir) | `/leerie-state` | rw | *Local mode only.* Leerie run state (state.json, runs/, logs/, worktrees/). Mounted at a top-level container path distinct from `/work` so the repo checkout stays pristine — no `.leerie/` dir accumulates inside the project. The orchestrator reads the container path from `LEERIE_STATE_DIR=/leerie-state` (passed as `-e` in the same `nerdctl run` invocation). `LEERIE_STATE_HOST_DIR` is resolved on the host by the launcher before launch; see §2 "Host-side per-repo state directory". |
| `$LEERIE_HOME` (leerie install dir) | `/opt/leerie-image` | ro | *Local mode only.* Orchestrator source + Dockerfile + prompts. Read-only because the container has no business mutating the install. Shadows the baked COPY layer so edits to `orchestrator/leerie.py` take effect without an image rebuild. Absent in registry / fly.io mode — the baked COPY layer is used directly. |
| `$STAGE` (per-run host scratch — the same tree seed-auth.sh/ec2-seed-auth.sh tar-pipe to Fly/EC2) | `/opt/leerie-claude-json-src` | **ro** | The per-container copy of `~/.claude.json` (with the `projects[]` block stripped) lives at `$STAGE/.claude.json`. `$STAGE` is bind-mounted read-only in its entirety at this staging path — `.claude.json` is never bind-mounted directly onto `/home/leerie/.claude.json`. Directly mounting the host file as the live target has two failure modes: a shared mount is a documented `claude-code` corruption race (anthropics/claude-code issues #28847, #29217, #29395, #40226) that hangs workers in a recovery loop, and the CLI's rename()-based atomic write returns `EBUSY` on a bind-mounted single file, forcing a non-atomic truncate-in-place fallback with a demonstrated empty-file corruption window under concurrent workers. `scripts/container-entry.sh` copies `leerie-claude-json-src/.claude.json` to `/home/leerie/.claude.json` as a real file inside the container's own filesystem at container start — root-owned under rootless (correct under the single-entry `unshare --map-user` remap), explicitly `chown`ed to `leerie:` under rootful — mirroring the tar-copy pattern `scripts/remote/seed-auth.sh`/`scripts/remote/ec2-seed-auth.sh` already use for the remote runtimes. |
| `$STAGE/.claude` (per-run host scratch) | `/home/leerie/.claude` | rw | Per-container copy of `~/.claude/` with bulky, prior-session, and history paths skipped (`history.jsonl`, `projects/`, `sessions/`, `tasks/`, `plans/`, `todos/`, `file-history/`, `paste-cache/`, `shell-snapshots/`, `session-env/`, `telemetry/`, `stats-cache.json`, `debug/`, `downloads/`, `backups/`, `chrome/`, `ralph-state/`, `.last-cleanup`, `settings.json.*`, `plugins/cache/`, `plugins/marketplaces/`). CLI capability dirs (`agents/`, `skills/`, `commands/`, `hooks/`, `plugins/installed_plugins.json` + sibling JSON, `mcp-needs-auth-cache.json`, `settings.json`, `local/`, `statsig/`, `cache/`, `package.json`, `policy-limits.json`) ride along. `plugins/cache/` and `plugins/marketplaces/` are rebuilt on the remote in the fly runtime; see `scripts/remote/seed-auth.sh` step 4 (`# --- 4. Rebuild plugin cache`). |
| `_extract_claude_credentials_json` → `$STAGE/.claude/.credentials.json` | `/home/leerie/.claude/.credentials.json` | rw | The launcher's `_extract_claude_credentials_json` helper resolves which Claude OAuth credential to use, in order: `$CLAUDE_CODE_OAUTH_TOKEN` (the long-lived `claude setup-token` token) first when set, synthesized into `{"claudeAiOauth":{"accessToken":…,"scopes":["user:inference"]}}` (the `scopes` field is mandatory — the CLI's file-auth path rejects a scope-less blob); then Keychain (service `Claude Code-credentials`, via `security find-generic-password -w`, macOS only); then `$HOME/.claude/.credentials.json` on disk. A container cannot refresh a copied token, which is why the long-lived token wins over the file-based sources (DESIGN §6 *Credential strategy*). The Keychain and on-disk branches are shape-checked via `_claude_creds_has_oauth_token` (requires a non-empty `claudeAiOauth.accessToken`) before acceptance, guarding against an upstream Claude Code CLI bug (steipete/CodexBar#1844) where the Keychain item can hold only `{"mcpOAuth": {...}}` with no usable session token even while the host CLI works fine — `claude /login` does not repair this. A source failing the shape check is treated as empty and resolution falls through the chain; the rejected source and reason are written to a PID-scoped temp file (`$_CLAUDE_CREDS_REJECT_REASON_FILE`) rather than a shell variable, since the caller invokes the helper via `$(...)` subshell substitution where an internally-set variable would not survive. All three branches (and `seed-auth.sh`/`ec2-seed-auth.sh` on Fly/EC2) write the same JSON shape to the staged path at mode 600. Independently, when `$CLAUDE_CODE_OAUTH_TOKEN` is set the launcher also forwards it as a container env var (`-e CLAUDE_CODE_OAUTH_TOKEN`, unconditionally) since that auth path is permissive/long-lived and survives past the file blob's `expiresAt`. The same helper populates `LEERIE_WORKER_ENV_JSON`'s `LEERIE_CLAUDE_CREDS_B64` key for the `chain` arm. **Call-site failure behavior:** when extraction fails and no Bedrock auth mechanism (`$AWS_BEARER_TOKEN_BEDROCK` or `detect_bedrock_mode`) is active, the STAGE-assembly block `die()`s immediately rather than continuing into a container run doomed to fail at the in-container smoke test; the message names the mcpOAuth-only upstream bug and recommends `claude setup-token` when that shape is the rejection reason, or the standard `/login`/Keychain-access guidance otherwise. The guard exempts both Bedrock auth modes since they need no Claude subscription credential at all. |
| `$STAGE/.gitconfig`, `.gitconfig.local`, `.gitignore`, `.gitignore_global`, `.git-credentials`, `.netrc` (per-run host scratch) | `/home/leerie/.<same>` | rw | Per-container copies of each present host `~/.git*` sibling and `~/.netrc`. Worker can `git config --local` / mutate freely without affecting host state. |
| `$STAGE/.config/git` (per-run host scratch) | `/home/leerie/.config/git` | rw | XDG-style git config (`~/.config/git/config`, `~/.config/git/ignore`) copied per-container. |
| `$STAGE/.ssh` (per-run host scratch) | `/home/leerie/.ssh` | rw | Per-container copy of `~/.ssh/` with `agent/`, `S.*`, and `*.sock` excluded — host UNIX sockets aren't reachable from inside the container and `cp -a` on them is pointless. Keys and `known_hosts` ride along so workers can SSH-push if needed. Permissions set to `0700`. |
| `$STAGE/.gnupg` (per-run host scratch) | `/home/leerie/.gnupg` | rw | Per-container copy of `~/.gnupg/` with agent socket files (`S.gpg-agent*`, `S.scdaemon`, `S.keyboxd`) excluded and `use-keyboxd` stripped from `common.conf` (the container cannot reach the host keyboxd daemon; stripping the directive makes gpg fall back to file-based `pubring.kbx` lookup — on keyboxd-only hosts signing keys become unfindable, which is acceptable since commit signing is best-effort). Keyrings + `trustdb.gpg` ride along so workers can `git commit -S` if signing is configured. Permissions set to `0700`. |
| `$STAGE/.aws` (per-run host scratch, **Bedrock SSO/profile mode only**) | `/home/leerie/.aws` | **ro** | Staged when `detect_bedrock_mode()` finds `CLAUDE_CODE_USE_BEDROCK` set to a truthy value (`1`, `true`, `yes`, or `on`, case-insensitive — matching Claude CLI's `isEnvTruthy`) in the `env` block of any of the three settings files the Claude CLI merges (`~/.claude/settings.json` (userSettings), `<USER_REPO>/.claude/settings.json` (projectSettings), `<USER_REPO>/.claude/settings.local.json` (localSettings)) — and only when `AWS_BEARER_TOKEN_BEDROCK` (see below) is **not** set on the host; the bearer-token path needs none of this. The Claude CLI's AWS SDK resolves credentials via pure file I/O — reads `~/.aws/config` (profile + SSO session config) and `~/.aws/sso/cache/*.json` (SSO access tokens, ~12 h TTL) directly; no `aws` binary is needed inside the container. `~/.aws/cli/cache` is excluded (CLI result cache; large, irrelevant to auth). Mounted **read-only** because workers never write credentials. The `aws` binary (`awsAuthRefresh`) is a host-only concern: `aws sso login` requires an interactive TTY/browser and cannot run inside a non-interactive container; `bedrock_preflight()` catches an expired SSO token on the host before the container starts and prints the recovery hint (`aws sso login --profile <profile>`). On the Fly.io path, `$STAGE/.aws/` is included in the tar pipe to `seed_auth` automatically (`.aws` is not in the seed-auth exclude list) and lands at `/home/leerie/.aws/` on the remote machine. Belt-and-suspenders: when Bedrock SSO/profile mode is active, the launcher also injects `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE`, and `AWS_REGION` as explicit env vars — via `AUTH_MOUNTS` `-e` flags on the local nerdctl path and via `child_env` in the Fly detached-launch heredoc — so workers activate Bedrock through `process.env` independently of how the in-container claude binary handles `settings.json` env blocks. The same `AUTH_MOUNTS` block (local nerdctl path only — not yet wired into the Fly `child_env` heredoc) also forwards `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` when set on the host: leerie always invokes `claude -p` with an explicit `--model <tier>` alias (never a raw model ID), and on Bedrock the Claude CLI's own alias table can lag the Anthropic-API alias table by a model generation or more (e.g. `sonnet` resolving to Sonnet 4.5 instead of Sonnet 5) — these are the CLI's own documented env vars for repointing what an alias resolves to, read as plain process env vars at CLI startup. |
| `AWS_BEARER_TOKEN_BEDROCK` (host env var, **Bedrock bearer-token mode**) | forwarded as `-e`/`child_env` only — **no bind mount** | n/a | The static-bearer-token analogue of `CLAUDE_CODE_OAUTH_TOKEN`, triggered by a plain host env var independently of `detect_bedrock_mode()`'s settings.json scan, and taking precedence over the SSO/profile path above when both are present (matching the Claude CLI's own credential-resolution order — verified live against the CLI, v2.1.220: its Bedrock client construction short-circuits SSO/profile resolution once `AWS_BEARER_TOKEN_BEDROCK` is set). No `aws` CLI, no SSO session, no `~/.aws` staging — `bedrock_preflight()` is skipped entirely on this path. The launcher forwards `AWS_BEARER_TOKEN_BEDROCK` verbatim, `CLAUDE_CODE_USE_BEDROCK` (defaulting to `1` if the host didn't set it — confirmed live that the bearer token alone is a no-op without this flag, since the CLI otherwise falls through to firstParty/OAuth dispatch), and `AWS_REGION` when set (optional — the CLI defaults to `us-east-1`) as explicit `-e` flags on the local nerdctl path and `child_env` entries in the Fly detached-launch heredoc, mirroring the `CLAUDE_CODE_OAUTH_TOKEN` forwarding pattern above rather than the SSO path's settings.json extraction. The same local-nerdctl `-e` block (not yet extended to the Fly `child_env` heredoc) also forwards `ANTHROPIC_DEFAULT_SONNET_MODEL`, `ANTHROPIC_DEFAULT_OPUS_MODEL`, and `ANTHROPIC_DEFAULT_HAIKU_MODEL` when set on the host — the Claude CLI's documented mechanism for repointing what the `--model <tier>` alias leerie always passes resolves to, since Bedrock's alias table can lag the Anthropic-API one; see the SSO/profile row above for the same rationale in full. On the Fly path specifically, every value substituted into the detached-launch heredoc (the bearer token, region, and use-bedrock flag, plus the pre-existing `_BEDROCK_PROFILE`/`_BEDROCK_REGION`/host-TZ values) is JSON-encoded host-side first (`python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))'`, the same technique `_launch_argv_json` already uses for orchestrator argv) rather than substituted as a raw `"${VAR}"` string — an opaque bearer token is exactly the kind of value likely to contain a `"` or `\` that would otherwise break out of the Python string literal and execute as arbitrary code on the remote machine. |

The four host-auth mounts (`~/.config/gh`, `~/.git-credentials`, `~/.ssh`,
`$SSH_AUTH_SOCK`) that earlier versions of leerie bind-mounted **no longer
exist** — finalize moved to the host (DESIGN §6 *Finalization*), so
`git push` and `gh pr create` run with the host's working auth state and
don't need to be forwarded into the container. The macOS-only "SSH agent
forwarding is not available" note is gone for the same reason.
| `~/.cache/leerie/mise-data` | `/home/leerie/.local/share/mise` | rw | Mise's `MISE_DATA_DIR` (per-repo runtime installs, plugins, cache). Lives in the user dir so the resolver checks it first then falls through to the image-baked `MISE_SYSTEM_DATA_DIR=/usr/local/share/mise` for the LTS fallback (DESIGN §6½). Its `shims` subdir is on the image's `ENV PATH` (see §0.5 "Image build") so a worker's own ad-hoc Bash commands can reach a repo-pinned runtime (e.g. Ruby via `.ruby-version`) without an explicit `mise exec --`. |
| `~/.cache/leerie/pnpm-store` | `/home/leerie/.cache/leerie/pnpm-store` | rw | pnpm content-addressable store. Pointed at via `npm_config_store_dir` (the pnpm-respected env var; `PNPM_STORE_PATH` doesn't exist and would be silently ignored). Safe for concurrent installs across worktrees (pnpm/discussions#10702). |
| `~/.cache/leerie/pip` | `/home/leerie/.cache/leerie/pip` | rw | pip HTTP + wheels cache. Each worker that needs Python deps runs `pip install` / `uv sync` itself in its own worktree against this shared cache; after the first install of a package the cache is warm and subsequent workers' installs are fast. Wheel-build race pypa/pip#9034 is still a theoretical concern but in practice rare given leerie's small worker concurrency (DESIGN §6½). |
| `~/.cache/leerie/go-mod` | `/home/leerie/.cache/leerie/go-mod` | rw | `GOMODCACHE`. Concurrent-safe via per-module-version `flock` in `cmd/go/internal/modfetch`. |
| `~/.cache/leerie/cargo` | `/home/leerie/.cache/leerie/cargo` | rw | Whole `CARGO_HOME` (registry + bin + config.lock). Mounting only `registry/` breaks `config.lock` (cargo#11376). Concurrent-safe via cargo's documented flock semantics. |
| `~/.cache/leerie/corepack` | `/home/leerie/.cache/leerie/corepack` | rw | `COREPACK_HOME`. Without this, corepack inherits `XDG_CACHE_HOME=/tmp/.cache` and tries to mkdir `/tmp/.cache/node/corepack/v1`, which fails under rootless UID remapping. Concurrent-safe: corepack downloads tarballs via atomic rename; the cache is read-mostly after first install. |
| `~/.cache/leerie/bundle` | `/home/leerie/.cache/leerie/bundle` | rw | `BUNDLE_PATH` for Bundler (Ruby gems). `BUNDLE_CACHE_ALL=1` instructs Bundler to cache all gems (including git-sourced ones) so each `bundle install` reuses downloaded gems across worktrees and runs. |
| Each `--inspect-dir` path (translated) | `/inspect/<basename>` | ro | See below. |

### `LEERIE_*` env-var forwarding (local `nerdctl run`)

The orchestrator runs **inside** the container and reads every override from
`os.environ` — which only inherits what `nerdctl run` forwards. The launcher
forwards **every `LEERIE_*` var in its environment except a deny-list** of
launcher/host-only vars (the `_leerie_env_denylist` array in the `nerdctl run`
block). A `for` loop over `compgen -v | grep '^LEERIE_'` appends a bare
`-e "$name"` (host value passed through) for each non-deny-listed var with a
non-empty value. Empty/unset vars are skipped.

**`LEERIE_STATE_HOST_DIR_DISPLAY` — a deliberate, narrow exception.** The
orchestrator sees the state root bind-mounted at `/leerie-state`, so a bare
`die()` naming `<state-root>/runs/<id>/state.json` would print a path the
operator cannot open on the host. The launcher therefore forwards the *host*
side of that mount explicitly, as
`-e "LEERIE_STATE_HOST_DIR_DISPLAY=${LEERIE_STATE_HOST_DIR:-}"`, and
`_operator_path()` uses it to rewrite the prefix in operator-facing text.

The `_DISPLAY` suffix is load-bearing. `LEERIE_STATE_HOST_DIR` itself stays on
the deny-list, and must: a host path is meaningless *as a path* inside the
container, and nothing may open this value. It may only be printed. The
separate name is what keeps that restriction legible at the use site, and
`tests/test_operator_path_translation.py` pins both halves — that the launcher
forwards the display copy, and that the un-suffixed original remains denied.

Deny-list = forward-all-minus-known-host-only, not an allow-list, so the
dynamic per-worker names (`LEERIE_MODEL_<WORKER>`, `LEERIE_EFFORT_<WORKER>`,
built at runtime from `f"{MODEL_ENV}_{worker.upper()}"`) forward automatically
and a future override cannot silently be stranded at the container boundary.
Deny-listed vars are the launcher/host-only ones: `LEERIE_STATE_DIR` and
`LEERIE_INSPECT_DIRS` (remapped separately to container-internal values —
`-e LEERIE_STATE_DIR=/leerie-state`, `-e LEERIE_INSPECT_DIRS=`), `LEERIE_HOME`
/ `LEERIE_REPO` / `LEERIE_STATE_HOST_DIR` / `LEERIE_SELF_CMD` (self-location +
host paths), `LEERIE_NO_PUSH` (orchestrator always gets `--no-push`; host does
the push), `LEERIE_RUNTIME` (decided launcher-side before launch), the
Fly/EC2/remote/chain/wave machinery — including the EC2 instance-lifecycle
vars `LEERIE_EC2_INSTANCE_ID` / `LEERIE_EC2_AMI` / `LEERIE_EC2_INSTANCE_TYPE`
/ `LEERIE_EC2_KEY_NAME` / `LEERIE_EC2_SECURITY_GROUP` / `LEERIE_EC2_SUBNET_ID`,
launcher-only like their Fly counterparts (`LEERIE_FLY_APP` /
`LEERIE_FLY_IMAGE` / `LEERIE_MACHINE_ID`). `tests/test_launcher_env_forwarding.py`
extracts the loop verbatim and includes a coupling guard asserting no
orchestrator-read override is deny-listed except four justified exceptions
(`LEERIE_STATE_DIR`, `LEERIE_INSPECT_DIRS`, `LEERIE_NO_PUSH`, `LEERIE_RUNTIME`).
On the Fly path the equivalent forwarding is via `child_env` in the
detached-launch heredoc, not this loop.

**`USER_REPO` (non-`LEERIE_*`, both runtimes).** `log()` renders its
`[leerie] [<repo>]` prefix from `Path(os.environ.get("USER_REPO") or
os.getcwd()).name`. The container's cwd is `/work`, so without an injected
`USER_REPO` the fallback fires and every line reads `[leerie] [work]`. Both
runtimes therefore inject it, each outside the `LEERIE_*` loop (the name
does not match `^LEERIE_`):

- **Local:** an explicit `-e "USER_REPO=$(basename "$USER_REPO")"` in the
  `_run_argv` array, next to the other explicit `-e` lines.
- **Fly:** `child_env["USER_REPO"] = "$(basename "$USER_REPO")"` in the
  detached-launch heredoc (reproduced verbatim under §"Worker auth +
  config seeding", `seed-auth.sh`).

Both pass the **basename**, never the host path: `$USER_REPO` is a host
absolute path that does not resolve inside the container (the repo is at
`/work`), and `Path(x).name` is identity for a bare name. `log()` is the
only in-container reader, so nothing treats the value as a path. The two
mechanisms are independent — a change to one that is not mirrored in the
other regresses that runtime to `[work]`.

### `--inspect-dir` path translation

Inspect dirs (`--add-dir` forwarded to `claude -p` for cross-repo
context) come from CLI flags, the `LEERIE_INSPECT_DIRS` env var, or
`leerie.toml`'s `inspect_dirs` key. They are *host* paths. The launcher:

1. Collects all three sources before any container is started.
2. For each host path: resolves it on the host (`cd -P "$path" && pwd`,
   so symlinks and `~` are expanded), bind-mounts it read-only at
   `/inspect/<basename>` inside the container, and rewrites the
   corresponding CLI flag to point at the in-container path.
3. Passes only the rewritten flags into the container, and clears
   `LEERIE_INSPECT_DIRS` in the container env so the in-container
   resolver doesn't see any host paths.

This honors the orchestrator's precedence rules in `resolve_inspect_dirs`
(CLI > env > TOML) by emitting only CLI args — the env and TOML pre-passes
in the launcher synthesize CLI flags.

A host path *inside* `$USER_REPO` (already visible at `/work/<subpath>`)
collides with the launcher's `/inspect/<basename>` target. The launcher
warns and skips the redundant mount.

#### Remote runtime (Fly.io) transport

Under `--runtime fly`, the launcher additionally ships each
`--inspect-dir` host path to `/inspect/<basename>` on the Fly machine
via `scripts/remote/seed-repo.sh:seed_inspect_dirs`. The rewritten
`--inspect-dir /inspect/<basename>` CLI flag already carries the
in-machine view to the orchestrator via `REWRITTEN_ARGS`; this step
makes the path actually exist on the machine's filesystem.

Per inspect dir, transport is two-phase, mirroring the
`seed_repo_clone` + `seed_repo_dirty` strategy used for `/work`:

- **Git repos** — `git bundle create - --all` packs every reachable
  object into one pack-format binary stream, piped via
  `flyctl ssh console -C "sh -c 'cat > /tmp/leerie-inspect-<base>.bundle'"`.
  Submodules are bundled the same way into
  `/tmp/leerie-inspect-<base>-subs/`. The machine then `git clone`s
  from the local bundle file into `/inspect/<base>` (with
  `protocol.file.allow=always` for the submodule update;
  CVE-2022-39253 mitigation). A second pass (`_seed_one_inspect_dir_dirty`)
  rsyncs the uncommitted-edit delta on top via `fly_rsync_wrapper` so
  workers see your in-flight changes for inspect dirs, the same way
  they do for the main repo.
- **Non-git directories** (docs folders, etc.) — fall back to plain
  `rsync -a -H` via `fly_rsync_wrapper` (the v1 path; kept for the
  no-`.git/` case).

Why bundle for git repos: plain rsync over `flyctl ssh console` is
unworkable for non-trivial trees — a large working tree with
`node_modules`/build output can hang indefinitely, while the same
repo's bundle (source only, no gitignored artifacts) is orders of
magnitude smaller and ships in one pipe in under a second.

Resume probe: before the bundle phase, `seed_inspect_dirs` runs one
`flyctl ssh console -C "test -d /inspect/<base>/.git"` per inspect
dir. If the directory was already seeded on a prior run, the bundle
is skipped and only the dirty delta refreshes — typical resume cost
is a few seconds per inspect dir, not a few minutes. New inspect
dirs added at `resume` time take the full fresh path.

Each `/inspect/<basename>` is chowned `leerie:leerie` after every
transport phase so the orchestrator (which runs as `leerie`) and
its workers can read the tree — same ownership-handover pattern
`seed_repo_clone` / `seed_repo_dirty` use for `/work`.

The launcher serializes its `INSPECT_HOST_TARGETS` bash array (parallel
to `INSPECT_MOUNTS`, populated by `collect_inspect_path` for every
out-of-repo inspect dir) into the `LEERIE_INSPECT_HOST_TARGETS` env var
before each call. In-repo inspect dirs (the skip-redundant-mount branch)
are not appended to `INSPECT_HOST_TARGETS` — they arrive on the machine
via `seed_repo` at `/work/<subpath>` and need no separate transport.

Called at two points inside the `--runtime fly` block:

1. **Fresh provision** — after `seed_repo` lands `/work`, before the
   detached orchestrator launches.
2. **Resume / re-seed** — after `re_seed` lands the dirty delta, on
   every resume. This honors the documented property that inspect
   dirs are re-resolved fresh on every run including `resume`
   (§2 *Inspect directories*); the user can add `--inspect-dir <path>`
   at resume time and expect it to land on the machine.

A failure of `seed_inspect_dirs` is fatal — the run aborts before the
orchestrator launches, in the same class as `seed_repo` / `seed_auth`
failures. Workers cannot do their job with `--add-dir` flags pointing
at non-existent paths, so silent continuation would yield wrong
classifier / planner output.

Read-only contract: inspect-bucket workers only `Read`/`Grep`/`Glob`
inspect dirs (DESIGN §12). No rsync `--delete` or two-way sync is
used.

Inspect dirs are **not** `git clone`d *from origin* on the machine
because the machine deliberately holds no GitHub credentials (DESIGN §6
*Finalization*). The bundle approach above ships the host's local git
state directly — no remote auth ever needed in-machine.

Same rsync-vs-tar rationale as `seed_repo_dirty` (applies to the
fallback path and the dirty-delta phase): macOS BSD `tar -c`
normalizes filenames NFC → NFD (libarchive); rsync preserves filename
bytes verbatim. Bundles sidestep the problem entirely — filenames
travel as pack-format binary objects, materialized natively by the
receiving git.

### Browser-based testing

Chromium and its matching chromedriver are baked into the image (see *Image
build* above), so workers that need a real browser have one available without
any runtime installation. The Selenium cache directory
(`/home/leerie/.cache/selenium`) is pre-created (root-owned at build time,
chowned to `leerie` at runtime on the rootful path) so Selenium Manager
cache writes succeed if it ever runs.

**Container flags — baked in, no project changes required.** Three flags are
needed to run Chromium in a rootless container:

- `--no-sandbox` — disables Chrome's user-namespace sandbox, which is
  unavailable in unprivileged containers.
- `--disable-setuid-sandbox` — suppresses the SUID sandbox-helper lookup.
  Without this, Chrome finds `/usr/lib/chromium/chrome-sandbox` and tries to
  exec it; SUID is stripped in rootless containers, so the exec fails and
  Chrome crashes with `SIGTRAP` before fully initializing — *even when
  `--no-sandbox` is present*. This is the most common silent failure mode.
- `--disable-dev-shm-usage` — redirects shared-memory to `/tmp`; `/dev/shm`
  is typically 64 MB in containers and Chrome's renderer can exceed it.

These are written to `/etc/chromium.d/leerie-container-flags` at image build
time, so the `/usr/bin/chromium` wrapper picks them up automatically on every
invocation. **No project-level Chrome flag configuration is required** — the
image handles it.

Projects that construct a `ChromeOptions` / `Options` object and add these
flags explicitly are fine; the flags are idempotent. Projects that don't touch
Chrome options at all also work, because the wrapper sets them globally.

### macOS-specific: Colima auto-share scope

Colima auto-shares only paths under `/Users/$USER` into the VM by
default. A bind mount of a path outside that range will silently
appear empty inside the container. The launcher warns at preflight
when `$USER_REPO` or any `--inspect-dir` falls outside, and points
the user at `~/.colima/default/colima.yaml`'s `mounts:` section as
the workaround.

VirtioFS is the mount type leerie documents (`colima start
--runtime containerd --mount-type virtiofs`) — it's the fastest
option and gives correct UID semantics for bind mounts.

### Logging, signal flow, and TTY adaptation

**`log()` and `die()` never raise.** Both wrap their `print` in
`contextlib.suppress(OSError, ValueError)`. This matters because on the
remote runtime `sys.stdout` **is** `<run_dir>/orchestrator.log` — the
launcher redirects fd1 there and `_install_run_log_tee` skips installing its
guarded tee in that case — so `print(..., flush=True)` performs a real write
to the state filesystem, which can raise `ENOSPC` when full. Every
terminating arm in `main()` logs *before* assigning `exit_code`, so an
unguarded write failure there would turn a resumable pause
(`ContextOverflow`, `TerminalAuthFailure`, `RateLimitedExit`,
`KeyboardInterrupt`, `InterruptedBySignal`) into an exit-1 traceback. For
`die()` the exit **code** is the load-bearing part — an unwritable stderr
must not convert a deliberate coded exit into an unhandled `OSError`.

`OSError`/`ValueError` only, never `BaseException`: a `KeyboardInterrupt`
arriving during the write must still propagate. `_save_state_best_effort`
uses the same "everything that is not a real interrupt" tuple (`Exception`
plus the four exit-signal classes) rather than catching everything, for the
same reason. `_TeeStream`'s log-copy guard carries the same two exceptions;
guarding `log()` extends that discipline to the real stream too, covering
`_TeeStream`'s own `_orig.write` / `_orig.flush`. The cost is deliberate: a
failed write is silently lost rather than losing the whole run.

The launcher invokes `nerdctl run --rm $TTY_FLAGS …` where `TTY_FLAGS`
is chosen by a one-line `[ -t 0 ]` test:

```sh
TTY_FLAGS="-i"
[ -t 0 ] && TTY_FLAGS="-it"
```

That single test is **the entire branch** between terminal mode and
plugin mode. Everything else (mounts, image, env, entrypoint, signal
handling) is identical.

**Terminal mode (`-it`)**:

- `-i` + `-t` give the orchestrator a controlling TTY → its existing
  `log(...)` and stream-event summarizers write directly to the user's
  terminal with no aggregation layer.
- `--clarify` prompts use `input()` interactively — the user types
  answers at the host terminal, characters flow through the pty to
  Python inside the container.
- Ctrl-C in the host terminal sends SIGINT to the container's PID 1
  (the orchestrator). Python's `KeyboardInterrupt` fires, the
  existing `except KeyboardInterrupt` handler runs the worktree-only
  cleanup, the orchestrator exits — and the kernel reaps everything
  else in the PID namespace.

**Plugin mode (`-i` only)**:

- Claude Code's Bash tool spawns the launcher without a TTY on stdin.
  `[ -t 0 ]` returns false; the launcher passes only `-i`, no pty
  allocated inside the container.
- Inside the container, `sys.stdin.isatty()` returns False. The
  orchestrator's `gather_answers()` and the mid-execution
  clarification path (`_surface_clarification()`) both detect this and trigger
  the canonical no-TTY signal: write `<state-root>/runs/<run-id>/pending-questions.json`
  to disk and `sys.exit(EXIT_NEEDS_ANSWERS)` (= 10).
- `<state-root>/runs/<run-id>/pending-questions.json` is visible on the
  host because `/leerie-state` is bind-mounted from `LEERIE_STATE_HOST_DIR`.
  The plugin agent at `commands/leerie.md` reads it directly, asks the
  user via the chat UI, writes the matching `<state-root>/answers.json`,
  and re-runs the container with `--answers <state-root>/answers.json`
  and `resume`.
- Stdout/stderr stream back through the Bash tool to the agent's
  chat session — possibly in 30s-ish chunks per the harness's
  buffering, which is acceptable for the streaming UX.
- The kernel teardown guarantee applies the same way as in terminal
  mode: when the orchestrator exits (clean exit, exit 10, or any
  signal the harness sends), PID 1 dies and the namespace is reaped.

Common to both modes:

- **Orchestrator stdout/stderr are persisted to
  `<state-root>/runs/<run-id>/orchestrator.log`.** Once `main()` has the run
  dir, `_install_run_log_tee()` wraps `sys.stdout`/`sys.stderr` with a
  `_TeeStream` that mirrors every write to that file (flushed per write, so a
  crash still leaves a complete trail). This is the local-runtime counterpart
  of the Fly/EC2 path's `Popen(stdout=log_f)` → `orchestrator.log`: on those
  runtimes the orchestrator's fd1 already *is* that file, so
  `_install_run_log_tee` no-ops there (an inode check via
  `_stdout_already_targets` prevents double-writing). It exists because the
  local runtime otherwise keeps no state-dir copy of the orchestrator's own
  phase logs — stdout goes only to nerdctl → the launcher's decoupled tail →
  the user's `tee` — so an abnormal exit or a lost pipe erased them (run
  26fd0fa5's `leerie.log` was 0 bytes, which is why its integration skip
  could not be diagnosed post-hoc). Best-effort: a log-open failure logs and
  proceeds with terminal-only output; a mid-run write failure to the log copy
  is swallowed so the terminal stream never breaks. Since `log()`/`die()`
  became total (see *Logging, signal flow, and TTY adaptation* above), a
  failure of the **terminal** stream is swallowed too — that asymmetry is
  gone, and deliberately: on the remote runtime that stream IS the log file.
  Per-worker `<state-root>/logs/<sid>.log` files (the raw event streams) are
  unaffected and orthogonal.
- **`die()` announces the run id on every terminal exit path.**
  `State.__init__` calls `_set_current_run_id(run_id)`, which stashes the id
  in the module-level `_CURRENT_RUN_ID` — the only channel available to
  `die()`, since most call sites run at module scope with no `State` in
  hand. Once a `State` has been constructed, every subsequent `die("...")`
  appends `(run <id>)` to its message; a `die()` before any `State` exists
  (e.g. an early preflight failure) prints its plain message unaffected.
  Pinned in `tests/test_run_id_terminal_emit.py`. The paired
  `log(f"run id: …")` announcement is the **first statement of
  `_run_phases`**, at function-body depth with no enclosing `if`, so it
  fires on both fresh runs and every resume regardless of which phase
  checkpoint is being resumed from.
- `--rm` removes the stopped container automatically so they don't
  accumulate. Worktrees and state on the bind-mounted host
  filesystem survive for `resume`.
- `--name leerie-<ts>-<pid>` makes `nerdctl ps` legible and
  `nerdctl logs <name>` targetable for the rare diagnostic case.
- `--label leerie.launcher_pid=<pid>` records the owning launcher's
  PID (`$$`) on the container. The stale-container reaper (below) reads
  it back via `nerdctl inspect` to test owner liveness without parsing
  the `--name` suffix. `<pid>` is the same `$$` used in `--name`.
- Aggregate memory cap: **not a `nerdctl run` flag.** `container-entry.sh`
  (PID 1) writes `leerie.slice/memory.max` (the parent cgroup of every
  per-worker cgroup), derived from VM `MemTotal` read from
  `/proc/meminfo` (portable across Colima and native Linux; the host
  launcher cannot read the VM's MemTotal on macOS, so a `nerdctl
  --memory` flag is not used). This bounds the sum across all concurrent
  workers, distinct from the per-worker cgroup caps in §6 (*Memory
  containment*) which bound each worker individually. See DESIGN §6
  *container boundary's hidden precondition* and the caps table in §6.

**Abnormal-exit cleanup (traps + reaper).** The container boundary
guarantees namespace teardown *when PID 1 exits*, but a host CLI that
dies without forwarding a stop signal (OOM-killed `nerdctl` client,
uncatchable SIGKILL) leaves the container orphaned and holding the
run-dir flock — every later `resume` then exits `EXIT_LOCKED=75`
(DESIGN §6). Two launcher mechanisms close this:

- **Kill-on-exit trap.** INT/TERM traps on the local run path
  `nerdctl kill` the container (via its run-id, which equals the
  container ID — see *Single-owner enforcement*) before the launcher
  exits, and the EXIT trap performs the same kill *before* it removes
  the cidfile. Reliable for Ctrl-C/SIGTERM; does NOT help under
  SIGKILL/OOM (uncatchable) — that is the reaper's job.
- **Stale-container reaper.** On the local `resume` path, before the
  `nerdctl run` spawn, the launcher looks up any container whose ID
  equals the resume run-id (`nerdctl inspect`), and if it is still
  running but its owning launcher (`leerie.launcher_pid` label) is dead
  (`kill -0` fails), `nerdctl kill`s it first — making `resume`
  self-heal the orphaned-flock wedge instead of returning 75.
- **Decoupled output streaming (piped mode only).** In piped mode
  (`leerie … | tee log`, i.e. `TTY_FLAGS="-i"` and stdout is not a TTY),
  the launcher does NOT let `nerdctl run` write straight to its stdout
  pipe — Colima's persistent SSH ControlMaster can retain a copy of the
  pipe write-end on an abnormal container exit, so `tee` never gets EOF
  and the launcher hangs (orphaning the container). Instead the launcher
  points `nerdctl run > "$_run_log" 2>&1` (a regular file — the mux does
  not retain a plain-file fd) and starts `tail -n +1 -f "$_run_log"` in
  the background, streaming the file to its own stdout. `_reap_tail`
  (called after the run and from all three EXIT/INT/TERM traps) briefly
  sleeps so `tail` drains the final write, then `kill`s it and `rm`s the
  log — no post-kill `cat`, which would duplicate the whole log. The
  `nerdctl` argv is assembled once into a `_run_argv` array and invoked
  in two spelled-out branches (redirected vs. direct), since bash cannot
  build a redirection through variable expansion. Container exit-code
  capture (`|| container_rc=$?`) is unaffected — `> file` is not a pipe.
  Interactive `-it` runs skip the decouple entirely (real pty, no `tee`,
  no hang, stdin needed for `--clarify`). See DESIGN §6 *Launcher hang on
  abnormal container exit*.

The plugin mode flow above is exactly what `commands/leerie.md` already
documents — it works through the container with zero new mechanism
because the state dir lives on the bind-mounted `/leerie-state` host filesystem.

### What does NOT change in the orchestrator

`orchestrator/leerie.py` is unmodified by this design. It runs as PID 1
inside the container; everything it currently does — the asyncio
event loop, the signal handlers, `claude -p` spawn via
`asyncio.create_subprocess_exec`, the per-worker `_terminate_proc_tree`
and `_DescendantTracker` (kept as the fast happy path for clean exits
— see DESIGN §6), worktree management, telemetry — works unchanged.
Container/process isolation is the launcher's concern, not the
orchestrator's.

Maps to `DESIGN.md`: §6 *Cleanup on abnormal exit / Worker subtree
termination*.

---

## 1. Repository layout

```
leerie/
├── .claude-plugin/plugin.json     plugin manifest
├── .claude-plugin/marketplace.json single-plugin marketplace manifest (Claude Code `/plugin marketplace add` entry point)
├── leerie                        executable entry-point wrapper (chmod +x);
│                                   portable bash; runtime preflight + nerdctl run
│                                   (DESIGN §6 / §0.5)
├── Dockerfile                  container image recipe; built locally on first
│                                   run, tagged `leerie:<VERSION>` (§0.5)
├── fly.toml                    Fly.io Machine config — app, image, vm sizing
│                                   (4 cpu / 8 GB midpoint), zero warm-pool
│                                   (min_machines_running=0). See §0.5.
├── orchestrator/leerie.py        the orchestrator — all control flow (chmod +x)
├── prompts/
│   ├── _clarification_filter.md   shared include (codebase→research→ask filter)
│   │                              inlined by classifier.md / implementer.md via
│   │                              _load_prompt's {{include: …}} expansion
│   ├── classifier.md              Phase 1 worker system prompt
│   ├── planner.md                 Phase 2 worker system prompt
│   ├── reconciler.md              Phase 2½ worker — resolve cross-domain
│   │                              capability-tag drift between planners
│   ├── provision.md               §6½ LLM-fallback install-recipe worker
│   ├── implementer.md             Phase 5 implementer worker system prompt
│   ├── conformer.md               Phase 5 post-work conformance worker (DESIGN §9)
│   ├── integrator.md              conflict-resolution worker system prompt
│   ├── rebaser.md                 finalize-time rebase-onto-base worker
│   │                              (DESIGN §6 "Rebase-onto-base before push";
│   │                              scoped, fully-agentic §12 exception)
│   ├── pr_writer.md               Phase 6 PR title + body author worker
│   ├── patch_generator.md         post-run self-heal worker — proposes minimal
│   │                              system-prompt patches against failing call_types
│   └── judge.md                   LLM judge worker — 3-dimensional rubric for
│                                  reviewing captured call records
├── scripts/
│   ├── setup-run.sh               create per-run branch + worktree (idempotent)
│   ├── new-worktree.sh            create/reuse a per-subtask worktree (per-run scoped)
│   ├── worktree-lib.sh            prune_leerie_worktrees(): a SCOPED replacement for
│   │                              `git worktree prune`, sourced by setup-run.sh,
│   │                              new-worktree.sh and cleanup.sh
│   ├── integrate.sh               merge a subtask branch into the per-run branch
│   ├── finalize.sh                verify the run branch exists and is non-empty; ready for push
│   ├── host-finalize.sh           host-side push + PR creation block; sourced by
│   │                              the local-runtime post-run path in leerie,
│   │                              decide_teardown's Fly clean-exit branch,
│   │                              `leerie finalize <run-id>` (§7 Host-side finalize),
│   │                              and the launcher's host preflight, for
│   │                              host_prepush_preflight alone
│   ├── cgroup-broker.py           cgroup broker, runs at the slice-owning identity (create/enroll/destroy over a Unix socket; v1+v2); the dropped-privilege orchestrator drives it
│   ├── verify-strict-schemas.py   maintainer tool: sends every hardened SCHEMAS entry to the real API and reports which compile under strict mode (live creds; outside pytest's testpaths)
│   ├── measure/
│   │   └── worker_durations.py  maintainer tool: derives the per-worker-type duration distribution from a state root's calls.ndjson, feeding TIMEOUT_DEFAULT_PER_WORKER (writes tests/fixtures/worker_duration/summary.json; outside pytest's testpaths)
│   ├── cleanup.sh                 remove worktrees / branches (default: scoped to one run)
│   ├── container-entry.sh         container PID 1 (root rootful / mapped-UID rootless): create leerie.slice + launch cgroup broker + cd /work + drop to leerie via runuser (rootful)
│   ├── install.sh                 one-command installer (curl | bash); preflight git/curl + auto-install
│   │                               claude + runtime install (colima / rootless containerd) + clones + symlinks
│   ├── runtime-install.sh         per-OS auto-install of the container runtime (Colima on macOS;
│   │                              rootless containerd stack on Debian/Ubuntu — Fedora/Arch: docs hint).
│   │                              Sourced by install.sh and the launcher.
│   └── remote/
│       ├── _log.sh                shared remote_log() helper (timestamped, repo-tagged
│       │                          stderr) sourced by every other scripts/remote/*.sh file
│       ├── build-push.sh          build and push a self-contained image for Fly.io Machines;
│       │                           the baked /opt/leerie-image/ lets the image run without
│       │                           a bind mount (§0.5 "Registry publish path")
│       ├── provision.sh           Fly Machine lifecycle (sourced by launcher RUNTIME=fly branch);
│       │                           provision_machine() create→started→trap; stop_machine();
│       │                           destroy_machine(); decide_teardown() classifies exit-rc
│       │                           and routes to stop (pause-on-failure) or destroy
│       ├── lib.sh                 shared bash helpers (_extract_flyctl_remote_rc stderr
│       │                           rc-parse; update_run_json atomic merge; iso_now;
│       │                           render_tail_wrapper; tail_with_optional_autofinalize);
│       │                           sourced by provision.sh, resume-machine.sh, and re-seed.sh
│       ├── resume-machine.sh      Resume helper for paused remote runs (DESIGN §6 *Remote
│       │                           pause-on-failure*); resume_machine() flyctl machine start
│       │                           + wait_for_started + clear paused_at sentinels
│       ├── re-seed.sh               Mid-run re-rsync (Phase 4) — wakes paused machine,
│       │                           runs safety check, calls seed_repo_dirty. Used by
│       │                           `leerie re-seed <run-id>` and auto on `resume`
│       ├── seed-auth.sh           Worker auth + config seeding (sourced by launcher after
│       │                           provision_machine() returns); seed_auth() tar-pipes
│       │                           ~/.claude.json + ~/.claude/ (minus .claude/local) + git identity
│       │                           to /home/leerie/ via `flyctl ssh console -C "tar -xC ..."`,
│       │                           then pre-warms `claude --version` for orchestrator preflight
│       ├── seed-repo.sh           Two-phase bundle + delta repo seeding (sourced by launcher after
│       │                           provision); seed_repo(): git bundle parent + submodules
│       │                           piped via ssh-console → machine clones from bundles on disk,
│       │                           then rsync's dirty delta + .claude/ — no in-machine git clone
│       ├── collect-subtrees.sh     Subtree collection (sourced by `leerie finalize`);
│       │                           collect_subtrees_remote(): SSHes a bash payload that runs
│       │                           setup-run.sh + integrate.sh for un-merged subtask branches
│       │                           on the machine; conflicts are skipped and reported via sentinels
│       └── fetch-branch.sh        Post-run stream-back (sourced by decide_teardown BEFORE
│                                   destroy_machine on clean exit, and by `leerie finalize`);
│                                   fetch_branch(): git bundle pipe + state tar-pipe → host repo
├── commands/leerie.md            thin plugin skill — launches the orchestrator
├── skills/
│   ├── judge-llm-batch/SKILL.md  post-run judge skill — scores a batch of captured
│   │                              LLM calls against a 3-dimensional accuracy rubric
│   └── llm-self-heal/SKILL.md    post-run self-heal skill — autonomous loop that
│                                  proposes and measures prompt patches for failing
│                                  call_types; uses judge verdicts as the signal
├── chain/                         Laptop-side chain helpers (DESIGN §19).
│   │                              A chain is N parallel single-run `--runtime fly`
│   │                              invocations per wave, sequenced by the launcher's
│   │                              `chain` arm. The laptop drives everything; no Fly
│   │                              coordinator machine.
│   ├── __init__.py                exports __version__ = "0.1.0"
│   ├── _log.py                    log()/die() helpers — shared with git_ops.
│   └── git_ops.py                 synth_merge_branches (used between waves) +
│                                  create_stage_branch.
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

# Pre-supply clarification answers:
leerie "task" --answers answers.json

# Override caps. Both also read LEERIE_* env vars and leerie.toml keys.
leerie "task" --max-workers 80 --max-parallel 6
export LEERIE_MAX_WORKERS=80
export LEERIE_MAX_PARALLEL=6

# Dial how persistent workers are at building confidence before they exit
# blocked (default: 8 rounds inside each planner / implementer):
leerie "task" --confidence-rounds 12
export LEERIE_CONFIDENCE_ROUNDS=12

# Verbosity controls how much per-worker activity surfaces inline.
# Default is `stream`: one-line summary per worker event. -q drops to
# leerie's pre-streaming terse output; -qq is fully quiet (errors
# still emit). -vv adds raw payloads. Per-worker <state-root>/logs/<sid>.log
# files are always written regardless of level.
leerie "task"        # default: stream
leerie "task" -q      # normal (pre-streaming)
leerie "task" -qq     # quiet (errors only)
leerie "task" -vv     # debug
leerie "task" --verbosity normal
export LEERIE_VERBOSITY=stream

# Override the default source-of-truth preference (`both`). CLI flag and
# env var are session-scoped overrides; commit `source_of_truth = ...` in
# leerie.toml for a per-repo default.
export LEERIE_SOURCE_OF_TRUTH=codebase    # or: research, both
leerie "task" --source-of-truth codebase

# Override the host-side per-repo state directory (default:
# $HOME/.leerie/<basename>/). Each repo gets its own subtree under
# $HOME so Colima auto-shares it. Cross-repo basename collisions are
# caught at use time via the .owner sidecar (see §2 "Host-side per-repo
# state directory"). Precedence:
# default < leerie.toml state_dir < LEERIE_STATE_DIR env < --state-dir CLI.
export LEERIE_STATE_DIR=~/.leerie/myproject
leerie "task" --state-dir ~/.leerie/myproject
# Or commit a per-repo default in leerie.toml:
#   state_dir = ~/.leerie/myproject

# Select the execution runtime (default: local). `fly` routes each worker
# through Fly.io machines instead of local nerdctl containers.
export LEERIE_RUNTIME=local               # or: fly
leerie "task" --runtime fly

# Choose the model. Without overrides, every worker — judgment (classifier,
# planner, reconciler, plan_overlap_judge, provision, integrator) and acting
# (implementer, conformer) alike — defaults to sonnet. Use the env var
# for a sticky preference, the CLI flag for a one-off, or leerie.toml
# for the committed repo default. Per-worker overrides also exist —
# see §2.
export LEERIE_MODEL=sonnet                # or: opus, haiku
leerie "task" --model opus
leerie "task" --model-implementer opus --model-classifier haiku

# Override judge/heal output subdirectories:
leerie "task" --judge-dir my-judge --heal-dir my-heal
export LEERIE_JUDGE_DIR=my-judge
export LEERIE_HEAL_DIR=my-heal

# Judge and heal model overrides (default: sonnet):
leerie "task" --judge-model opus --heal-model opus
export LEERIE_MODEL_JUDGE=sonnet
export LEERIE_MODEL_HEAL=sonnet

# Heal-loop convergence knobs (defaults shown):
leerie "task" --heal-max-rounds 10 --heal-success-threshold 0.9
export LEERIE_HEAL_MAX_ROUNDS=10
export LEERIE_HEAL_SUCCESS_THRESHOLD=0.9

# Diagnostic toggle: every `claude -p` worker subprocess inherits DEBUG=*
# and ANTHROPIC_LOG=debug so its internal state surfaces on stderr; the
# idle watchdog (worker_idle_warn_sec, see §Caps) flushes a tail of that
# stderr alongside its silence warning. Off by default (noisy on healthy
# runs).
export LEERIE_WORKER_DEBUG=1
leerie "task"

# Run post-run skill phases against an existing run's captured LLM calls.
# --phase judge: score every call in calls.ndjson with the 3-dim judge rubric
#   and write verdict files to <run-dir>/<judge-dir>/.
# --phase heal: read the judge index for failing call_types and run the
#   self-heal loop for each; if no judge index exists yet, runs judge first.
# Use --run-id to select a specific run; otherwise auto-picks the most
# recent resumable one.
leerie --phase judge --run-id bugfix-login-timeout-bug-b81e90
leerie --phase heal  --run-id bugfix-login-timeout-bug-b81e90
# Combine with heal-loop knobs:
leerie --phase heal --heal-max-rounds 5 --heal-success-threshold 0.8

# Read-only telemetry report for a run: per-call_type token/cost/latency/
# failure breakdown + memory peak. Pass a run id, or omit to auto-pick the
# sole run. Exits without running orchestrate.
leerie --report bugfix-login-timeout-bug-b81e90
leerie --report            # auto-picks when exactly one run exists

# Recommended backstop for worker auto-compaction
# (Claude Code CLI variable — not consumed by leerie itself):
export CLAUDE_AUTOCOMPACT_PCT_OVERRIDE=70

# Chain verbs: submit, inspect, pause, and destroy multi-run chains.
# A chain is N parallel single-run `--runtime fly` invocations per wave,
# with synth-merge between waves (DESIGN §19). The laptop is the
# sequencer; no Fly coordinator machine. No chain-specific env vars are
# required — the underlying `./leerie --runtime fly` invocations have
# their own env requirements unchanged.

# Submit a new chain. Each --wave flag defines one sequential wave
# (comma-separated prompt-file paths). Waves execute in order; runs
# within a wave execute in parallel. N waves are supported. The chain
# operates against $USER_REPO directly (the laptop's current repo).
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
# kill / attach, plus the separate --list-chains flag) have been hard-removed
# entirely — no shim, no back-compat. Use the bare verbs above.
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

The reason the flag is unreachable for judgment workers is measured, not
stylistic: it removes the CLI's working-directory boundary as well as the
prompts. Probed live (claude 2.1.237, filesystem-verified), a worker holding
only `INSPECT_TOOLS` and carrying the flag used `Write` — absent from that
allowlist — to overwrite a tracked file outside its cwd and `git commit` on the
user's branch, and did so *even with its cwd already set to a detached
worktree*. With the flag absent, every one of those attempts was rejected.

`--dangerously-skip-permissions` therefore no longer bypasses permissions for
these workers; it **widens their allowlist** (`_widen_inspect_tools`) with the
leading verbs of the repo's own declared build/lint/test commands, as
`Bash(<verb>:*)` patterns. That is the visibility the flag was always
documented to buy — Node/TS repos where the planner reaches for
`pnpm`/`tsc`/`biome`/`vitest`/`npx`, ~18-19% of whose Bash calls otherwise fail
with "requires approval" in headless mode — without the write access that was
never the point. Residual, stated rather than hidden: a build verb executes
arbitrary code, so an allowlisted `pnpm`/`node`/`python3` *can* still write
outside the cwd; `_assert_repo_unchanged()` is what catches that. See DESIGN
§12 *Judgment-worker isolation* for the full four-layer argument.

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
nor set their limits itself. Just before the first worker
spawns (in `_run_phases`, past the resume short-circuits so zero-worker
completed/no-work resumes are not gated), `_enforce_and_record_cgroup_containment`
probes the broker end-to-end and records `{enforced, hierarchy}` in
`state.json` (the `cgroup_containment` field). If containment cannot be enabled — broker
down, no usable cgroup hierarchy (neither a cgroup-v2 unified mount nor
v1 pids+memory controller mounts), or read-only cgroupfs — leerie
`die()`s by default, because a silently-uncapped run is what let a
runaway subtree exhaust the VM thread/PID table (a Bun `EAGAIN` crash).

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
enters `_run_phases` past `_schedule()` (the `waves` field is loaded from
`state.json`), so the preflight has nothing left to gate. A run that
died on the preflight *is* resumable (DESIGN §6 "Budget-check resume"):
`_schedule()` had already returned by the time `check_budget_feasibility`
ran, so `subtasks`/`waves` are recoverable from `plan_snapshot`, which is
written immediately after `_schedule()` and before this check (DESIGN §6
"Resumable planning"). `resume` rehydrates `subtasks`/`waves` from
`plan_snapshot` and re-runs only the budget check — under a higher
`--max-workers` or `--skip-budget-check` — instead of dying "Plans are
not persisted." The user re-runs `resume` with the recommended
`--max-workers` value (or `--skip-budget-check`), rather than starting a
fresh run from scratch.

Exit code `EXIT_BUDGET_INFEASIBLE = 11` on `die()`, distinct from
`EXIT_NEEDS_ANSWERS = 10` (deferred-clarification structured exit)
and the generic `die()` error code 1. The Fly runtime's `decide_teardown`
trap (`scripts/remote/provision.sh`) routes `11` through the same
case-arm as `0|10|75` (genuine terminal exits): the trap calls
`_try_fetch_branch_for_teardown` to pull whatever state landed on
the machine back to the host, then takes the `_run_finished_at == ""`
fallback (the run never reached finalize, so no `host_finalize` is
attempted) and `destroy_machine` runs cleanly. A code-11-specific
recovery hint is printed: "re-run with the recommended --max-workers
value" — distinct from the code-10 hint which suggests `finalize`.
The machine is still destroyed rather than paused: even though
`plan_snapshot` now makes the *host-side* `resume` recoverable
(DESIGN §6 "Budget-check resume"), the Fly Machine itself has no
further use once `decide_teardown` runs — the recommended fix is a
higher `--max-workers` or `--skip-budget-check` on a fresh remote
launch, not resuming the same (now-destroyed) machine. This routing
keeps the user from paying for a Fly volume indefinitely once the
budget check has already fired.

### Decomposition budget partition

Recursive decomposition (`_recursive_decompose`, DESIGN §5½ (P1) — every
`fit_judge`/`splitter` call it spawns) shares the same `worker_count`
budget as execution, and can exhaust `max_total_workers` entirely during
planning, leaving zero calls for implementers/conformers. Two caps
address this together:

1. **`DEFAULT_CAPS["decompose_budget_share"] = 0.40`** — the fraction of
   `max_total_workers` recursive decomposition may spend. Enforced by
   `_bump_decompose_workers(st, caps)`, which every fit_judge/splitter
   spawn site in `_recursive_decompose` (including the label-only
   migration-chunk splitter) calls instead of a bare `st.bump_workers`.
   It **checks before it bumps** — `decompose_worker_count >=
   decompose_budget_share * max_total_workers` raises
   `DecompositionBudgetExceeded` (a `WorkerError` subclass) *before*
   touching either counter, so a refused call cannot itself eat into the
   execution budget the partition protects — and otherwise bumps
   `st.data["worker_count"]` (via `st.bump_workers`, so the pre-existing
   global-cap `WorkerError` still fires first and unchanged) and
   `st.data["decompose_worker_count"]`. Callers catch it and accept the
   node as a leaf (fit_judge/splitter sites) or fall back to the
   pre-existing deterministic chunk labels (label-only migration site)
   without spawning the call. Each of the three spawn sites has its own
   `try/except DecompositionBudgetExceeded` closing before its `claude_p`
   call.

   This is a runaway backstop, not a score gate: it does not consult the
   fit_judge score, since stopping early on projected cost would ship
   exactly the low-scoring nodes `decompose_fit_threshold` exists to keep
   splitting. `_warn_decomposition_share` records the realized share in
   `state.json`'s `decompose_share` after expansion for calibration.
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
  lock. `save()`'s body also catches an `OSError(ENOSPC, ...)` from
  either half of the write and reraises it as `DiskLowSpace` — see
  §"Disk headroom (N30)". The rename uses `os.replace()` rather than
  `Path.replace()`: on Python 3.10, `pathlib`'s accessor binds
  `os.replace` at class-definition time, so patching the `os` module's
  `replace` attribute would not affect `Path.replace()` — only Python
  3.12's rewritten pathlib looks it up dynamically. `os.replace()` keeps
  the behavior version-independent.
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

### State directory

Controls where leerie writes all run state (`state.json`, `runs/`, `logs/`,
etc.). By default, state is written to a per-repo subtree under `$HOME` —
never inside the repo itself — so target projects do not accumulate a
`.leerie/` directory and do not need to add anything to their `.gitignore`.
The default path is `$HOME/.leerie/<basename>/`, giving each repo an
isolated subtree keyed by basename. Cross-repo basename collisions are
caught at use time via an `.owner` sidecar (see
*Host-side per-repo state directory* above for the full check).

Resolution order (lowest → highest priority):

1. **Default** `$HOME/.leerie/<basename>/`. The basename of the
   absolute repo path.

2. **`leerie.toml` at the repo root** with key `state_dir`. Plain
   `key=value` syntax; bare `~` and `~/`-prefixed values are expanded to
   `$HOME`.

3. **`LEERIE_STATE_DIR`** environment variable — any non-empty value is
   expanded (`~/` → `$HOME/`) and used verbatim. Set once in your shell
   profile to keep all repos under a common directory.

4. **`--state-dir PATH`** / `--state-dir=PATH` CLI flag. Highest priority;
   overrides everything. Launcher-only (stripped from `REWRITTEN_ARGS`;
   the orchestrator never sees it). Bare `~` and `~/`-prefixed values
   are expanded.

Code counterpart: `resolve_leerie_root(repo_root)` in `leerie.py`;
constant `STATE_DIR_ENV = "LEERIE_STATE_DIR"`. All three `leerie_root`
assignments in `main()` call `resolve_leerie_root(Path(os.getcwd()))`.
The launcher resolves `LEERIE_STATE_HOST_DIR` (the same value, before
container launch) via `_state_dir_default()` and passes it as the
`/leerie-state` bind-mount argument and via `-e LEERIE_STATE_DIR=/leerie-state`
so the orchestrator inside the container always writes to the mounted state
dir. See §0.5 *Bind-mount table* for the full mount specification.

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
   `resume_instance()` wakes that instance; otherwise `provision_instance()`
   creates a fresh one and `LEERIE_RUN_ID` is set to the new instance id
   (run_id = instance_id, mirroring Fly's run_id = machine_id rule — DESIGN
   §6 "Run identifier"). Unlike Fly, bare `resume` with no `--run-id`
   does not auto-discover an EC2 instance from a PID-record scan yet —
   the operator passes `--run-id` explicitly.
2. **Wait-ready** is `provision_instance()`'s/`resume_instance()`'s own
   `wait_for_instance_ready()` call (already internal to those functions).
3. **Seed.** `LEERIE_EC2_SSH_TARGET` is resolved from the instance's
   public IP (via `ec2-resume-instance.sh`'s `_resolve_ssh_target_from_instance`,
   reused for the fresh-provision path too), then `ec2_seed_auth()`
   followed by `ec2_seed_repo()` — mirroring `seed_auth`+`seed_repo` on
   the Fly path. An early flock probe (over `ec2_remote_exec`) mirrors
   the Fly branch's resume-only optimization: when the run directory's
   flock is already held, seeding is skipped entirely and the launcher
   attaches to the live orchestrator instead.
4. **Orchestrate.** A detached-`Popen` Python launch wrapper (same shape
   as the Fly launch script, `/opt/leerie-image/orchestrator/leerie.py`
   under `runuser`-equivalent `user="leerie"`) is piped to
   `ec2_launch_detached()`. rc=75 (the flock-loser smart-resume pivot)
   routes to `_attach_to_live_orchestrator_ec2()` (`ec2-ssm.sh`) instead
   of provisioning a duplicate orchestrator — `container_rc=130` so
   `decide_ec2_teardown`'s detach arm leaves the instance alone, exactly
   like the Fly branch's identical rc=75 routing. On a clean launch, the
   launcher tails the log via `render_tail_wrapper()` (from `lib.sh`,
   transport-agnostic POSIX sh) piped through `ec2_attach()`.
5. **Teardown** is `ec2-provision.sh`'s own `decide_ec2_teardown()` EXIT
   trap, registered by `provision_instance()`/re-armed by
   `resume_instance()`; the launcher only sets `LEERIE_REMOTE_EXIT_RC`
   before exiting, same as the Fly branch.

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

Resolution order (highest priority first), identical for both knobs:

1. **CLI value** — `--aws-region` / `--aws-profile` flag.
2. **`LEERIE_AWS_REGION`** / **`LEERIE_AWS_PROFILE`** environment variable.
3. **`leerie.toml`** at the repo root, keys `aws_region` / `aws_profile`.
4. **Default `None`.** Unset knobs leave region/profile selection to the
   AWS credential chain `aws-credentials.sh` resolves independently.

**Resolved by the launcher, not the orchestrator** (changed 2026-08-10).
The `_resolve_ec2_knob` helper in `leerie` runs the whole ladder and
assigns back into `LEERIE_AWS_REGION` / `LEERIE_AWS_PROFILE`, which every
consumer already reads: `ec2-lib.sh`'s `require_aws()`, `ec2-ssm.sh`, and
`ec2-provision.sh`'s `_aws_region_profile_args()`. Both flags are
**launcher-only inputs** — stripped from `REWRITTEN_ARGS`, allowlisted in
`tests/test_launcher_value_flags_coupling.py`, and deny-listed from the
container env forwarding, exactly like the `LEERIE_EC2_*` vars below.

The block sits **above** the launcher's top-level verb dispatch, because
`accept-blocked`, `stop`, `kill` and `finalize` each read `LEERIE_AWS_*`
inside their own arms; resolving beside the `--ec2-*` knobs further down
would leave those four on the env tier alone.

These knobs have no orchestrator-side counterpart: `args.aws_region` /
`args.aws_profile` and their `resolve_aws_region()` / `resolve_aws_profile()`
resolvers do not exist in `orchestrator/leerie.py` — a host-side
provisioning region is meaningless inside the container.
`tests/test_no_dead_resolutions.py` fails any `args.X = resolve_Y(...)`
whose result goes unread, guarding against reintroducing an inert copy.

### EC2 instance-lifecycle vars

Six `LEERIE_EC2_*` vars name the `RunInstances` parameters
`scripts/remote/ec2-provision.sh`'s `provision_instance()` needs
(DESIGN §6 *EC2 runtime lifecycle*, "Create" row):
`LEERIE_EC2_AMI`, `LEERIE_EC2_INSTANCE_TYPE`, `LEERIE_EC2_KEY_NAME`,
`LEERIE_EC2_SECURITY_GROUP`, `LEERIE_EC2_SUBNET_ID`, and
`LEERIE_EC2_INSTANCE_ID`. All six are **launcher-only inputs**, not
orchestrator-read prefs — they are already deny-listed from the
`LEERIE_*` container-forwarding loop (`leerie:6284-6297`; see
"`LEERIE_*` env-var forwarding" above) for exactly this reason: the
orchestrator runs *inside* the already-provisioned instance and has
no use for the parameters that created it, mirroring how
`LEERIE_FLY_APP`/`LEERIE_FLY_IMAGE`/`LEERIE_MACHINE_ID` are deny-listed
for the Fly path (`tests/test_launcher_env_forwarding.py` pins the
five instance-shape vars plus `LEERIE_EC2_INSTANCE_ID` on the
deny-list). No Python-side `resolve_*()` counterpart exists — same as
`LEERIE_AWS_REGION`/`LEERIE_AWS_PROFILE` above. Both groups are consumed
exclusively by the host-side launcher/`ec2-provision.sh` before any
container or instance exists.

Five are per-instance `RunInstances` parameters, each brought up to
the same **CLI > env > `leerie.toml` > (no default)** precedence every
other leerie knob has (mirroring `FLY_VM_DISK_GB` and the shallow-seed
knobs — `LEERIE_SEED_DEPTH`/`LEERIE_SEED_SHALLOW_THRESHOLD_MB`), resolved
by the launcher itself (`leerie:3644-3710`, `_resolve_ec2_knob`) before
`ec2-lib.sh` is sourced, then exported and stripped from
`REWRITTEN_ARGS` so the flag doesn't leak through as the task string:

1. **`--ec2-ami`** / **`--ec2-instance-type`** / **`--ec2-key-name`** /
   **`--ec2-security-group`** / **`--ec2-subnet-id`** CLI flag.
2. **`LEERIE_EC2_AMI`** / **`LEERIE_EC2_INSTANCE_TYPE`** /
   **`LEERIE_EC2_KEY_NAME`** / **`LEERIE_EC2_SECURITY_GROUP`** /
   **`LEERIE_EC2_SUBNET_ID`** environment variable.
3. **`leerie.toml`** at the repo root, keys `ec2_ami` / `ec2_instance_type`
   / `ec2_key_name` / `ec2_security_group` / `ec2_subnet_id`.
4. **(no default)** — unlike `runtime`/`source_of_truth`, these describe
   AWS account resources leerie cannot choose on the operator's behalf
   (unlike Fly, where `FLY_VM_CPUS`/`FLY_VM_MEMORY_MB` have working
   defaults today). Once all three tiers are exhausted, the var is
   exported empty; `ec2-lib.sh`'s `resolve_ami()` / `resolve_instance_type()`
   / `resolve_key_name()` / `resolve_security_group()` / `resolve_subnet_id()`
   (see the `ec2-lib.sh` Files-table row above) each read their one var
   via `_resolve_ec2_var` — a required-var check that `die()`s with an
   actionable message naming the missing var, run host-side after the
   launcher's own resolution ladder rather than a bare `${VAR:?}` (which
   would kill the whole sourcing shell with bash's generic "parameter
   null or not set" message under `set -u`). `RUNTIME=ec2` without all
   five resolved fails the same way `RUNTIME=fly` without
   `LEERIE_FLY_APP` fails: `die()` with setup instructions before any
   AWS API call. `tests/test_resolve_ec2_vars.py` covers the launcher-side
   ladder (CLI > env > `leerie.toml` precedence, per-var isolation, unset
   stays empty).

The sixth, **`LEERIE_EC2_INSTANCE_ID`**, is not a provisioning input —
it is the launcher's read of the just-created instance id back into
the environment after `provision_instance()` returns, mirroring how
`LEERIE_MACHINE_ID`/`LEERIE_RUN_ID` are set launcher-side after
`flyctl machine run` for the Fly path (see the denylist comment at
`leerie:6281`, "Fly/EC2/remote/chain/wave machinery: consumed
launcher-side only"). It is written to the crash-recovery sidecar
`ec2-instance.json` (see the `ec2-provision.sh` Files-table row above)
rather than read from an operator-set env var.

A seventh var, **`LEERIE_EC2_SSH_TARGET`**, is consumed by
`scripts/remote/ec2-seed-repo.sh` (see the Files table row above): the
`ssh`(1) destination for the instance (e.g. `ec2-user@<public-ip>` or an
`ssh_config` Host alias) that `ec2_tar_pipe` and the dirty-delta rsync
consume verbatim. Like `LEERIE_EC2_INSTANCE_ID`, this is not an
operator-set provisioning input — resolving an instance id to a
reachable SSH address is `ec2-provision.sh`'s job (not yet
implemented); the launcher is expected to set it the same way it sets
`LEERIE_EC2_INSTANCE_ID`, once provisioning lands.

### EC2 image delivery

DESIGN §6 *EC2 runtime lifecycle* → "Image delivery" settles how the
leerie image reaches an EC2 instance: **bake into the AMI**, the direct
analog of Fly's shipped `ensure_image()` push-to-registry answer but for
a boot-from-snapshot target rather than a pulled container. The operator
builds a custom AMI, out of the per-run critical path (a Packer / EC2
Image Builder pipeline, out of scope for leerie itself), with the
orchestrator source, Python 3.10+, and every OS-level dependency
`.leerie-setup.sh` would otherwise need root for already present.
`ec2-provision.sh`'s `provision_instance()` (see the Files table above)
reflects this today: `run-instances` carries no explicit block-device
mapping and no per-run build/push/pull step — the instance is ready to
accept `ec2_seed_repo`/`ec2_remote_exec` calls the moment
`wait_for_instance_ready()` returns.

**No new `LEERIE_EC2_*` knob.** `LEERIE_EC2_AMI` (already spec'd under
"EC2 instance-lifecycle vars" above, same CLI > env > `leerie.toml` >
(no default) precedence) is sufficient to name the chosen artifact: a
custom AMI under the bake-into-AMI default, or a stock AMI paired with a
documented user-data fallback script for an operator who has not yet
built one (DESIGN §6 names and rejects the two alternatives — ECR-push
and user-data pull-and-build — as the default; user-data pull remains a
documented manual fallback, not a second code path leerie implements).
No `resolve_*()` counterpart, no denylist change: `LEERIE_EC2_AMI` is
already a launcher-only input and already on the container
env-forwarding deny-list for the same reason the other five
instance-shape vars are (see "EC2 instance-lifecycle vars" above) — the
image-delivery decision does not change which side consumes the var.

**Future knob flagged, not added.** DESIGN §6 flags that an instance
profile (`IamInstanceProfile`, carrying the SSM managed-instance role
`ssm:StartSession` et al. need) is a `RunInstances` parameter the
provisioning subtask will have to supply from somewhere, alongside the
five already-reserved shape vars — shaped like a future
`LEERIE_EC2_INSTANCE_PROFILE` knob. This design does not add that knob
now; it is out of scope for image delivery and belongs to whichever
subtask wires `IamInstanceProfile` into `run-instances`.

### Fly app name

Fly.io app names are globally unique. `LEERIE_FLY_APP` is required when
`RUNTIME=fly`; the launcher `die()`s with setup instructions when unset.

Resolution order (highest priority first):

1. **`--fly-app NAME`** / `--fly-app=NAME` CLI flag. Launcher-only
   (stripped from `REWRITTEN_ARGS`; the orchestrator never sees it).

2. **`$LEERIE_FLY_APP`** environment variable.

3. **(none)** — no default, no `leerie.toml` key. Required.

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

Resolution order (highest priority first):

1. **`--confidence-rounds N`** CLI flag. Argparse rejects non-positive
   integers.
2. **`LEERIE_CONFIDENCE_ROUNDS`** environment variable, same value set.
3. **`leerie.toml` at the repo root**, `confidence_rounds = N`.
4. **Default `8`** (`DEFAULT_CAPS["confidence_rounds"]`).

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

Operators commonly run `leerie task | tee leerie-<task>.log`, and a log left
inside `$USER_REPO` is bind-mounted whole into every worker's container —
letting a worker read its own orchestration log, including gate/judge
vocabulary, and defeat judge independence (the failure mode the N5 startup
warning at `_warn_if_log_in_repo` detects). The default therefore lands
under `LEERIE_STATE_HOST_DIR` — never under `$USER_REPO` — settling N5's
own stated residual (whether "outside the repo" should specifically mean
the state dir) in favor of the state dir: it already exists, is never
bind-mounted into a worker container, and is the convention every other
per-run artifact (`state.json`, per-worker logs) already uses.

`--log-file` is registered in the launcher's `_value_flags` list (so the
task-argument-extraction walk does not mistake its value for the task
string) and is stripped (flag + value) from `REWRITTEN_ARGS` before
forwarding to the orchestrator's `parse_args()`, the same way
`--seed-depth` / `--seed-shallow-threshold-mb` are — the orchestrator
declares no argument for it and would otherwise error `unrecognized
arguments`.

**Teeing (local runtime).** The launcher itself now writes its combined
stdout+stderr to `LEERIE_LOG_FILE_RESOLVED`, so the operator no longer
needs to run the manual `| tee` that created the N5 leak in the first
place. Wired into the existing decoupled-streaming mechanism (DESIGN §6
*Launcher hang on abnormal container exit*): in the piped/non-TTY local
case (`TTY_FLAGS=-i` and stdout is not a TTY), `nerdctl run` already
redirects into a launcher-owned `$_run_log` file that a `tail -f` WE own
streams to our own stdout, so the SSH mux (Colima) never holds our stdout
pipe. That `tail` is now piped through `tee -a "$LEERIE_LOG_FILE_RESOLVED"`
when the target is writable (probed with a throwaway `: >> ...` append,
after a best-effort `mkdir -p` of its parent directory) — `$_run_log`
itself is a scratch file removed at exit; `LEERIE_LOG_FILE_RESOLVED` is
the durable copy. No enclosing subshell around the pipeline: `$!` names
tee (the pipeline's last process) when teeing, or tail itself when not —
identical to the pre-teeing behavior in the non-teeing case. When teeing,
`$_tail_pid` names only `tee`; `tail` itself is a distinct process in the
pipeline that does not reliably exit on its own — a `tail -f` on a
since-deleted file never gets the write that would trigger a `SIGPIPE`
once its stdout pipe is broken, so it would otherwise survive `_reap_tail`
and orphan under init. `_reap_tail` therefore also recovers `tail`'s PID
from the job table (`jobs -l %%`) at reap time and kills it alongside
`$_tail_pid`.

**Interactive/-it path.** `$_run_log`/`tail`+`tee` is gated to the piped
case only — piping nerdctl's own stdout there would defeat `-t`, the same
reason the launcher itself drops to `-i` when the operator's own stdout is
piped (see the TTY_FLAGS comment above the local execution branch). For
the real-tty case, the `-it` branch is instead wrapped in `script`(1) when
a `--log-file` target is writable and `script` is on `PATH`: `script`
allocates its own pty for the `nerdctl run` child, so nerdctl still gets a
real console for `--clarify`'s interactive prompt — TTY_FLAGS and
nerdctl's own argv/redirection are untouched — while `script` itself
duplicates that pty's bytes into the log file on the side. util-linux
`script` (Linux) only accepts a command via `-c <string>` run through
`$SHELL -c`, so the `nerdctl run` argv is `%q`-quoted into one string;
BSD `script` (macOS) takes the command as trailing positional args and
execs it directly. Falls back to nerdctl inheriting stdout directly,
unchanged, when no `--log-file` target is writable or `script` is
unavailable. The documented piped-mode/TTY-flag hazards at
`leerie:7580-7702` are unaffected either way. Remote runtimes (Fly, EC2)
are out of scope for this teeing wiring — the `$USER_REPO` bind-mount
leak N5 targets is a local-runtime-only condition.

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

The prefix is built from three per-wave counters, each rendered as its
own ` · `-separated segment when non-zero (segments with a zero count
are omitted entirely, so `0/M`-style fragments never appear):

- **`running N subtask(s)`** — implementer not yet at terminal status
  (no entry in `subtask_status[sid]`, or value not in
  `_TERMINAL_STATUSES = {complete, failed, blocked}`).
- **`N subtask(s) in conformer`** — implementer reached `complete` and
  the advisory conformer phase is still in flight. The signal is
  `subtask_status[sid] == "complete"` *and* `conformance[sid]` absent —
  the conformance dict is written by `_settle_subtask` exactly when the
  conformer settles, so this is a precise live indicator (DESIGN §9
  *Post-work conformance*).
- **`N subtask(s) done`** — implementer settled *and*, if `complete`,
  the conformer has also wrapped; or implementer hit `failed` /
  `blocked` (terminal regardless of conformer). Always rendered last so
  rising progress reads on the right side of the prefix.

The wave header `wave W of V` is the 1-based current wave index and
total wave count. Counts are restricted to the current wave's
membership (`waves[completed_waves]`), not the whole run — that's what
keeps `running 5 subtasks` meaningful at wave start.

Singular/plural is rendered on the count (`1 subtask` vs `5 subtasks`).

Built by `_get_progress` (`orchestrator/leerie.py`); emitted only after
Phase 3 schedules the waves, which is why classifier / planner /
reconciler log lines have no prefix. Post-wave-loop workers
(`summarizer`, `pr_writer`, `_run_final_conformance`) also emit no prefix:
`_get_progress` returns `None` once `completed_waves >= len(waves)`,
since there is no in-flight wave to count.

`_invoke` takes `progress` as a callable (`Callable[[], tuple[...] |
None] | None`), not a spawn-time snapshot, and calls it per stream
event. This is so a long-running worker's prefix advances as siblings
complete — two workers logging at the same wall-clock instant agree on
the count instead of carrying frozen snapshots from their respective
spawn moments.

#### Rejected-payload diagnostic

`_read_stream` latches the input of every `StructuredOutput` tool_use into
`last_structured_payload` (rendered by `_format_payload_for_log`, capped at
`_REJECTED_PAYLOAD_LOG_MAX = 4000` chars, degrading to `repr` if
`json.dumps` raises — a diagnostic must never kill the run it is explaining).
When a subsequent tool_result is an errored **schema** rejection —
`_is_schema_rejection`, matching `does not match required schema` or
`inputvalidationerror` case-insensitively — the latched payload is logged
beside the rejection, then cleared so a later unrelated failure cannot
re-print a stale payload.

Emitted at every verbosity (it is a failure diagnostic, not per-event
activity). The gate is deliberately narrow: an ordinary tool failure (a
failing test, a missing file) must never drag an unrelated structured payload
into the log beside it.

Why this exists: the rejection text names the offending fields but never
echoes what was submitted, and the payload lives in a preceding event the
error text cannot reach — so the commonest worker failure signature was
undiagnosable from a log. The `InputValidationError` (unparseable JSON) path
already logged its payload; this closes the gap for the parseable-but-invalid
case.

#### Blocked-planner gap diagnostic

`_format_blocked_gap(confidence) -> str` renders a blocked planner's stated
gap for `phase_plan`'s per-category summary line, capped at
`_BLOCKED_GAP_LOG_MAX = 400` chars with a visible `… [truncated; see log]`
marker — never a silent cut, matching `_format_payload_for_log` above.

Two transforms, both because the value is free prose. Whitespace is collapsed
so an embedded newline cannot split a one-line summary across several rows,
and the result is truncated: `confidence.basis` runs a **median of ~1.1k
characters and up to 4.3k** across real planner submissions — so an
untruncated line would put multiple KB on one row of the operator's
terminal. The full text stays in the per-worker log, which the scheduling
gate's own blocked-domain message already points at.

Returns `""` rather than `None` for absent, empty or malformed input, so the
caller interpolates an empty gap instead of the string `"None"`. The cap is
much smaller than `_REJECTED_PAYLOAD_LOG_MAX` because this is one line inside a
routine summary rather than a standalone failure dump. Pinned by
`tests/test_schedule_blocked.py`.
Pinned by `tests/test_rejected_payload_logging.py`.

Resolution order (highest priority first):

1. **`--verbosity LEVEL`** CLI flag, values `quiet` / `normal` /
   `stream` / `debug`. Argparse rejects anything else.
2. **`-v` / `-vv` / `-q` / `-qq`** shortcuts. These anchor to
   `normal` (not to the resolved default), so `-v` always means
   "show me the streaming feature" and `-q` always means "back to
   the pre-streaming terse output", independent of what
   env-var / TOML defaults are set to.
3. **`LEERIE_VERBOSITY`** environment variable.
4. **`leerie.toml`**, `verbosity = "stream"`.
5. **Default `stream`** (`VERBOSITY_DEFAULT`).

An invalid value in env or file is rejected at startup via `die()`.
Errors always emit at every level (clig.dev "errors emit at every
level" anti-pattern guard) — `quiet` does NOT suppress error
messages, only the per-event chatter.

The resolved value lives on `st.data["verbosity"]` and is
re-resolved fresh on every run, including `resume` — the user
can dial up or down at resume time without editing state.

### Inspect directories

Extra directories the inspect-bucket workers (classifier, planner,
reconciler, plan_overlap_judge, provision) may read. Forwarded to each `claude -p` invocation as
one `--add-dir` flag per entry. Use this when a task references a
sibling repo outside the current repo cwd — for example, "compare
how beacon and leerie handle X, beacon is at `~/src/enric/beacon`":
without `--inspect-dir ~/src/enric/beacon`, the classifier and
planner cannot `Read`/`Grep`/`Glob` that path, and an attempt to
fall back to `ls`/`find` is blocked by the workspace sandbox even
though `INSPECT_TOOLS` allowlists those verbs.

Resolution order (highest priority first):

1. **`--inspect-dir PATH`** CLI flag, repeatable.
2. **`LEERIE_INSPECT_DIRS`** environment variable, colon-separated.
3. **`leerie.toml`**, `inspect_dirs = "/abs/path/a,/abs/path/b"`
   (a comma-separated string, parsed by `_read_toml_key`).
4. **Default** `[]` (no extra directories).

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

1. **`--judge-dir DIR`** CLI flag.
2. **`LEERIE_JUDGE_DIR`** environment variable.
3. **`leerie.toml`**, `judge_dir = "judge-out"`.
4. **Default `"judge-out"`** (`JUDGE_DIR_DEFAULT`).

### Heal output directory

The subdirectory name (relative to `<run-dir>`) where LLM self-heal loop output
files are written.

Resolution order (highest priority first):

1. **`--heal-dir DIR`** CLI flag.
2. **`LEERIE_HEAL_DIR`** environment variable.
3. **`leerie.toml`**, `heal_dir = "heal-out"`.
4. **Default `"heal-out"`** (`HEAL_DIR_DEFAULT`).

### Judge model

The `claude` model alias used when the judge skill spawns a worker to score a
batch of captured calls against a 3-dimensional rubric. `judge` is absent
from `MODEL_DEFAULT_PER_WORKER` and falls through to the global
`MODEL_DEFAULT` (`sonnet`), same as every other worker per CLAUDE.md's
model-default policy.

Resolution order (highest priority first):

1. **`--judge-model MODEL`** CLI flag.
2. **`LEERIE_MODEL_JUDGE`** environment variable.
3. **`leerie.toml`**, `model_judge = "opus"`.
4. **Default `"sonnet"`** (`MODEL_DEFAULT`; `judge` is absent from
   `MODEL_DEFAULT_PER_WORKER`).

### Heal model

The `claude` model alias used when the self-heal skill spawns workers for patch
generation and patched-arm replay.

Resolution order (highest priority first):

1. **`--heal-model MODEL`** CLI flag.
2. **`LEERIE_MODEL_HEAL`** environment variable.
3. **`leerie.toml`**, `model_heal = "sonnet"`.
4. **Default `"sonnet"`** (`MODEL_DEFAULT_PER_WORKER["heal"]`).

### PR-writer model

The `claude` model alias used at finalize time by the `pr_writer` worker
that composes the PR title and body. The worker reads the target repo's
PR template (if any), the run's commit log, and a sampled diff, then
emits a JSON object with `title`, `body`, and `used_template`. The host
launcher reads the result from `run.json` and passes it to
`gh pr create`.

Resolution order (highest priority first):

1. **`--pr-writer-model MODEL`** CLI flag.
2. **`LEERIE_MODEL_PR_WRITER`** environment variable.
3. **`leerie.toml`**, `model_pr_writer = "sonnet"`.
4. **Default `"sonnet"`** (`MODEL_DEFAULT_PER_WORKER["pr_writer"]`).

### PR template selector

When the target repo has multiple PR templates inside a
`PULL_REQUEST_TEMPLATE/` directory, leerie picks the alphabetically first
`.md` by default. A repo-specific override selects a different basename
(with or without the `.md` suffix). Has no effect when the repo has a
single top-level template (e.g. `.github/pull_request_template.md`) or
no template at all.

Resolution order (highest priority first):

1. **`--pr-template NAME`** CLI flag.
2. **`LEERIE_PR_TEMPLATE`** environment variable.
3. **`leerie.toml`**, `pr_template = "bug"`.
4. **Default**: alphabetically first `.md` in the discovered directory.

An override that does not match an existing template is **not fatal** —
finalize must not block over a cosmetic preference — leerie logs a
warning and falls back to the alphabetical default.

### PR base branch override

The final branch a run's PR merges into defaults to `working_branch`
(the branch checked out when the run started). This is distinct from
the diff fork-point, which always stays `working_branch` regardless of
this override — overloading `working_branch` for both roles would
corrupt the diff base if the override branch weren't the actual fork
point.

Resolution order (highest priority first), via `resolve_pr_base_branch`
(mirrors `resolve_pr_template`'s `_resolve_str_pref` delegation):

1. **`--pr-base-branch BRANCH`** CLI flag.
2. **`LEERIE_PR_BASE_BRANCH`** environment variable.
3. **`leerie.toml`**, `pr_base_branch = "release/1.0"`.
4. **Default**: `working_branch`.

The resolved value is written to `state.json` and `run.json` as
`pr_base_branch`, alongside the unmodified `working_branch`.

`scripts/host-finalize.sh`'s `host_finalize` (the sole `gh pr create`
call site — see the Files table above) reads `run.json.pr_base_branch`
and passes it to `gh pr create --base`, falling back to
`working_branch` when the field is absent (a run finalized before this
field existed). The origin-nonexistence default-branch fallback (base
branch deleted/renamed on origin) operates on this resolved base, same
as it always did for `working_branch`.

### PR-writer payload caps

The `pr_writer` worker is invoked with its entire user prompt (task
text, classification, subtask titles, full commit log, diff
stat/dirstat, sampled diff, and the PR template body — all serialized
as one JSON string) passed as `claude_p`'s `user_prompt`, which
`_invoke` feeds to `claude -p` over stdin rather than argv (§3 "User
prompt transport — stdin, not argv") — so this payload is not bound by
Linux's per-argument `MAX_ARG_STRLEN` (131,071 bytes, `PAGE_SIZE * 32`)
the way an argv-passed prompt would be.

Three constants in `orchestrator/leerie.py` still cap the unbounded
fields, now purely to bound the worker's LLM context rather than to
defend an argv ceiling. Each capped field gets an in-band `... [<label>
truncated at ~N KB; remainder omitted — rely on the commit log] ...`
sentinel so the worker can see the truncation and avoid fabricating
detail past the cut-off.

| Constant | Default | Bounds |
|----------|---------|--------|
| `PR_WRITER_COMMIT_LOG_MAX_BYTES` | 80,000 | full `git log --no-merges` between `working_branch` and `run_branch` |
| `PR_WRITER_TEMPLATE_MAX_BYTES`   | 32,000 | contents of the resolved PR template file |
| `PR_WRITER_DIFF_SAMPLE_MAX_LINES`| 500    | sampled `git diff` hunks (line-capped because individual diff lines can be long and breaking one mid-line would render the surrounding hunk unreadable) |
| `PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES` | 8,000 | serialized JSON length of the `final_conformance` payload field. Enforced inside `_final_conformance_payload` by trimming `warnings` (then `residuals`) from the tail; at least one of each is preserved and a `truncated: true` marker is added when trimming fired |

These are **module constants, not `DEFAULT_CAPS` entries**, by
design. `DEFAULT_CAPS` is the surface for run-wide operational caps
that are intended to be user-tunable through CLI / env / TOML
(`max_total_workers`, `worker_timeout_sec`, `worker_memory_max_bytes`,
etc.). The PR-writer caps are internal protocol limits bounding a
single worker invocation's LLM context: lowering them silently
degrades summaries, and raising them risks overwhelming the worker's
context rather than an OS-imposed argv ceiling (the payload travels
over stdin, not argv — see above).
`tests/test_pr_writer_payload_cap.py::test_pr_writer_byte_budgets_defined`
pins the values so any future change goes through code review.

Multi-byte UTF-8 safety: `_cap_text` slices at the byte boundary,
then back-decodes with `errors="ignore"` so the trimmed prefix never
ends mid-codepoint.

**`final_conformance` payload field** — when `_run_final_conformance`
produced a result, `_compose_pr_via_llm` reads
`st.data["conformance"]["_final"]` and adds a compact
`final_conformance` object to the pr_writer payload with
`{residuals: [...], failed_axes: [...], warnings: [...]}` (plus an
optional `truncated: true` marker). Omitted when the final pass was
skipped, crashed, or returned a fully clean result (no residuals,
every axis `ran:false` or `passed:true`, no warnings) — the absence
of the field is the cue that there is nothing advisory to say. The
serialized JSON is bounded by
`PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES` (8 KB), enforced in
`_final_conformance_payload` by trimming `warnings` (then `residuals`)
from the tail until the field fits; at least one of each is
preserved and the `truncated` marker is set so the prompt can
mention the cut-off honestly. The cap bounds the worker's LLM context
alongside the other `PR_WRITER_*` caps above, not an argv-size
constraint — the payload travels over stdin, not argv.

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
| plan_overlap_judge | sonnet | surface-overlap detection over the reconciled plan is judgment (two planners independently extracting the same artifact with incompatible APIs — DESIGN §5 *Cross-domain surface overlap*) |
| satisfied_probe | sonnet | per-subtask "is this already met on the base tree?" check (DESIGN §8 *Already-satisfied subtask elimination*); runs once per subtask so throughput/cost dominates — a **deliberate, documented cost tradeoff** (see the comment at `MODEL_DEFAULT_PER_WORKER["satisfied_probe"]`), not a claim that the check needs no judgment. The false-positive risk is contained by the base-tree-only tool scope + conservative-default prompt, not by model tier |
| provision    | sonnet  | fallback when the deterministic lockfile-detection table returns empty (DESIGN §6½); reads README + configs to emit an install recipe — judgment over arbitrary repo shapes |
| integrator   | sonnet  | behavioral conflict resolution; a wrong merge silently corrupts integrated state |
| implementer  | sonnet  | concrete subtask execution; also pinned to `low` effort (see "Effort selection" below) — cost/latency, not a judgment-tier change |
| conformer    | sonnet  | reads a diff and runs commands; also pinned to `low` effort (see "Effort selection" below) — same cost/latency rationale as implementer |
| judge        | sonnet  | scoring a batch of captured calls against a 3-dimensional rubric |
| heal (patch) | sonnet  | patch generation and replay; throughput matters more than broad judgment |
| pr_writer    | sonnet  | finalize-time PR title + body; fills repo template when present, summarizes commits otherwise; throughput-shaped one-shot call |
| dep_capture  | sonnet  | finalize-time dep inference from worker logs; broad judgment over arbitrary shell command sets |
| fit_judge    | sonnet  | P1 Task-Context Fit scoring is judgment |
| splitter     | sonnet  | LLM-driven structural partition (coupled-minority path) is judgment |
| adherence_judge | sonnet | plan-instruction-adherence scoring is judgment; empirically calibrated per-worker (goal-only task ⇒ high score, prescribed-and-violated ⇒ low score). If adherence gating regresses under the sonnet default, re-run the calibration and consider `--model-adherence-judge opus` as a per-worker override before reintroducing a blanket tier split |
| classification_judge | sonnet | independent adversarial verifier of the classifier's category set against the task + codebase (DESIGN §8 *Independent adversarial verification*); like every verifier it is *itself* the independent check |
| wiring_judge | sonnet | independent adversarial verifier of the reconciled plan's semantic wiring — the tag/dep dangles a structural `check_plan_wiring` scan cannot see (DESIGN §5 *A wiring re-check on the fully-merged plan*, §8) |
| provision_judge | sonnet | independent adversarial verifier of the detected install recipe against the actual image/runtime (missing `--break-system-packages`, wrong package manager vs lockfiles — DESIGN §6½, §8) |
| artifact_registry | sonnet | pre-planning canonical-vocabulary worker (DESIGN §5 *Artifact-registry worker*) — decides one canonical tag+path per artifact the task creates, judgment |
| task_coverage_judge | sonnet | independent adversarial verifier of the reconciled plan's coverage of the task (DESIGN §8 *Independent adversarial verification*); wired into `phase_planning_coverage_gate` — see §8 "The final two independent adversarial verifiers" |
| integration_judge | sonnet | independent adversarial verifier of the integrator's merge for behavioral (not just textual) correctness (DESIGN §8); wired into `integrate_wave` as a post-merge-commit detect-and-die gate (attacks for behavioral breakage the conflict-marker scan cannot see, `die()`s on non-empty `defects`) |
| rebaser      | sonnet  | finalize-time rebase-onto-base worker (DESIGN §6 *Finalization* "Rebase-onto-base before push") — a scoped, fully-agentic exception to §12: does the entire rebase workflow itself (fetch, rebase, conflict resolution, abort-if-irreconcilable judgment), mirroring `integrator` in every other respect |

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

Twenty worker types (plus the global override), each independently overridable:

| Worker             | env var                           | CLI flag                     | TOML key                  |
|--------------------|-----------------------------------|------------------------------|---------------------------|
| (global)           | `LEERIE_MODEL`                  | `--model`                    | `model`                   |
| classifier         | `LEERIE_MODEL_CLASSIFIER`       | `--model-classifier`         | `model_classifier`        |
| planner            | `LEERIE_MODEL_PLANNER`          | `--model-planner`            | `model_planner`           |
| reconciler         | `LEERIE_MODEL_RECONCILER`       | `--model-reconciler`         | `model_reconciler`        |
| plan_overlap_judge | `LEERIE_MODEL_PLAN_OVERLAP_JUDGE`| `--model-plan_overlap_judge` | `model_plan_overlap_judge`|
| satisfied_probe    | `LEERIE_MODEL_SATISFIED_PROBE`  | `--model-satisfied_probe`    | `model_satisfied_probe`   |
| provision          | `LEERIE_MODEL_PROVISION`        | `--model-provision`          | `model_provision`         |
| implementer        | `LEERIE_MODEL_IMPLEMENTER`      | `--model-implementer`        | `model_implementer`       |
| integrator         | `LEERIE_MODEL_INTEGRATOR`       | `--model-integrator`         | `model_integrator`        |
| conformer          | `LEERIE_MODEL_CONFORMER`        | `--model-conformer`          | `model_conformer`         |
| fit_judge          | `LEERIE_MODEL_FIT_JUDGE`        | `--model-fit_judge`          | `model_fit_judge`         |
| splitter           | `LEERIE_MODEL_SPLITTER`         | `--model-splitter`           | `model_splitter`          |
| adherence_judge    | `LEERIE_MODEL_ADHERENCE_JUDGE`  | `--model-adherence_judge`    | `model_adherence_judge`   |
| classification_judge | `LEERIE_MODEL_CLASSIFICATION_JUDGE` | `--model-classification_judge` | `model_classification_judge` |
| wiring_judge       | `LEERIE_MODEL_WIRING_JUDGE`     | `--model-wiring_judge`       | `model_wiring_judge`      |
| provision_judge    | `LEERIE_MODEL_PROVISION_JUDGE`  | `--model-provision_judge`    | `model_provision_judge`   |
| task_coverage_judge | `LEERIE_MODEL_TASK_COVERAGE_JUDGE` | `--model-task_coverage_judge` | `model_task_coverage_judge` |
| integration_judge  | `LEERIE_MODEL_INTEGRATION_JUDGE` | `--model-integration_judge` | `model_integration_judge` |
| artifact_registry  | `LEERIE_MODEL_ARTIFACT_REGISTRY` | `--model-artifact_registry` | `model_artifact_registry` |
| rebaser            | `LEERIE_MODEL_REBASER`          | `--model-rebaser`            | `model_rebaser`           |
| judge              | `LEERIE_MODEL_JUDGE`            | `--judge-model`              | `model_judge`             |
| heal               | `LEERIE_MODEL_HEAL`             | `--heal-model`               | `model_heal`              |
| pr_writer          | `LEERIE_MODEL_PR_WRITER`        | `--pr-writer-model`          | `model_pr_writer`         |
| dep_capture        | `LEERIE_MODEL_DEP_CAPTURE`      | *(none)*                     | *(none)*                  |

Note: `judge`, `heal`, `pr_writer`, and `dep_capture` do not follow the
`--model-<W>` CLI flag pattern used by orchestrator workers, because they
are post-run / finalize-time workers invoked outside the main orchestrate loop.
`judge`, `heal`, and `pr_writer` have dedicated CLI flags; `dep_capture` has
**neither a CLI flag nor a `leerie.toml` key** — it supports the env-var
`LEERIE_MODEL_DEP_CAPTURE` override only. All four still honor the global
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
The judgment / finalize workers (classifier, planner, reconciler,
plan_overlap_judge, provision, integrator, pr_writer, dep_capture, fit_judge,
splitter, adherence_judge, classification_judge, wiring_judge, provision_judge,
task_coverage_judge, integration_judge, artifact_registry, rebaser)
default to `medium`. `implementer` and `conformer` — the workers that
actually write code — default to `low`, a deliberate cost/latency call
(distinct from the judgment workers' `medium`, which is about determinism,
not cost): these previously defaulted to *unset* (inheriting Claude's own
default reasoning depth) so their effort stayed bounded by their own
evidence gates (DESIGN §8) rather than a global dial; that tradeoff is now
overridden in favor of a fixed low-effort ceiling. The post-run skill
workers `judge` and `heal` remain *unset* — when no effort is resolved, no
`--effort` flag is passed and the worker inherits Claude's default.

`medium` (rather than `high`) keeps per-run OTPM (output tokens per minute)
rate-limit pressure down; Leerie's downstream checks (confidence gate,
conformer, adherence gate, overlap judge, `_run_checked_loop` retries) absorb
the small per-worker quality reduction. `high`/`xhigh`/`max` remain available
per-worker via the override chain below when a specific worker needs deeper
reasoning.

| Worker       | Default | Why |
|--------------|---------|-----|
| classifier   | medium  | category choice is judgment over the whole task |
| planner      | medium  | decomposition granularity is the load-bearing judgment step (DESIGN §8 planner gate) |
| reconciler   | medium  | cross-domain tag equivalence is judgment |
| plan_overlap_judge | medium | surface-overlap detection over the reconciled plan is judgment (DESIGN §5 *Cross-domain surface overlap*); merge-feasibility discipline rewards pinning reasoning depth |
| satisfied_probe | unset | per-subtask advisory prune (DESIGN §8 *Already-satisfied subtask elimination*); runs once per subtask, same unset profile as conformer/judge — the base-tree-only tool scope and conservative default carry the correctness, not pinned depth |
| provision    | medium  | recipe synthesis over arbitrary repo shapes is judgment |
| integrator   | medium  | behavioral conflict resolution; a wrong merge corrupts state |
| implementer  | low     | code-writing worker; pinned low for cost/latency — a deliberate override of the prior "bounded by §8 evidence gate" unset default, since the conformer/confidence-gate loops downstream absorb the quality tradeoff |
| conformer    | low     | code-writing worker; same cost/latency rationale as implementer — the phase is advisory, so a borderline judgment call costs at most a warning |
| judge        | unset   | post-run scoring; no need to pin |
| heal         | unset   | post-run patch generation; no need to pin |
| pr_writer    | medium  | one-shot finalize call; pin reasoning to keep template-fill discipline (preserve HTML comments, do not invent ticked checkboxes) consistent across runs |
| dep_capture  | medium  | finalize-time dep inference; broad judgment over shell command sets benefits from pinned reasoning depth |
| fit_judge    | medium  | P1 Task-Context Fit score is judgment over scope+context co-minimization; calibrated threshold (0.70) makes pinned depth the reproducibility dial |
| splitter     | medium  | LLM-driven structural partition (coupled-minority path) is judgment over seam detection; wrong split corrupts downstream implementer context |
| adherence_judge | medium | plan-instruction-adherence scoring is judgment; empirically calibrated (goal-only task ⇒ ≥8.5, prescribed-and-violated ⇒ ≤3). If adherence gating regresses, raise back to `high` via `effort_adherence_judge` before reintroducing a blanket tier split |
| classification_judge | medium | independent adversarial verification of the classifier's category set (DESIGN §8); raise via `effort_classification_judge` if the gate regresses |
| wiring_judge | medium | independent semantic-wiring verification of the reconciled plan (DESIGN §5, §8) |
| provision_judge | medium | independent recipe verification against the image/runtime (DESIGN §6½, §8) |
| artifact_registry | medium | pre-planning canonical-vocabulary worker (DESIGN §5 *Artifact-registry worker*) |
| task_coverage_judge | medium | independent adversarial verification of plan-vs-task coverage (DESIGN §8); wired into `phase_planning_coverage_gate` |
| integration_judge | medium | independent adversarial verification of the integrator's merge for behavioral correctness (DESIGN §8); wired into `integrate_wave` as a post-merge-commit detect-and-die gate |
| rebaser      | medium  | finalize-time rebase-onto-base worker (DESIGN §6 *Finalization* "Rebase-onto-base before push"); judgment-adjacent — decides abort-vs-resolve per conflict, not just resolution content, so it gets `integrator`'s profile |

`EFFORT_DEFAULT` is `None` (meaning "don't pass `--effort`");
`EFFORT_DEFAULT_PER_WORKER` overrides it to `"medium"` for the seventeen
judgment / finalize workers in the table above: the six core judgment workers
(classifier, planner, reconciler, plan_overlap_judge, provision, integrator),
the finalize-time `pr_writer`, `dep_capture`, and `rebaser` workers, the P1
decomposition workers `fit_judge` and `splitter`, the plan-instruction-adherence
worker `adherence_judge`, the five independent adversarial verifiers
`classification_judge`, `wiring_judge`, `provision_judge`,
`task_coverage_judge`, and `integration_judge`, and the pre-planning
shared-vocabulary worker `artifact_registry`. It separately
overrides `implementer` and `conformer` to `"low"` — a distinct,
cost-motivated pin rather than a judgment-reproducibility one, so it is
called out separately from the `"medium"` judgment cohort above.

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

| Worker             | env var                            | CLI flag                      | TOML key                   |
|--------------------|------------------------------------|-------------------------------|----------------------------|
| (global)           | `LEERIE_EFFORT`                  | `--effort`                    | `effort`                   |
| classifier         | `LEERIE_EFFORT_CLASSIFIER`       | `--effort-classifier`         | `effort_classifier`        |
| planner            | `LEERIE_EFFORT_PLANNER`          | `--effort-planner`            | `effort_planner`           |
| reconciler         | `LEERIE_EFFORT_RECONCILER`       | `--effort-reconciler`         | `effort_reconciler`        |
| plan_overlap_judge | `LEERIE_EFFORT_PLAN_OVERLAP_JUDGE`| `--effort-plan_overlap_judge` | `effort_plan_overlap_judge`|
| satisfied_probe    | `LEERIE_EFFORT_SATISFIED_PROBE`  | `--effort-satisfied_probe`    | `effort_satisfied_probe`   |
| provision          | `LEERIE_EFFORT_PROVISION`        | `--effort-provision`          | `effort_provision`         |
| implementer        | `LEERIE_EFFORT_IMPLEMENTER`      | `--effort-implementer`        | `effort_implementer`       |
| integrator         | `LEERIE_EFFORT_INTEGRATOR`       | `--effort-integrator`         | `effort_integrator`        |
| conformer          | `LEERIE_EFFORT_CONFORMER`        | `--effort-conformer`          | `effort_conformer`         |
| fit_judge          | `LEERIE_EFFORT_FIT_JUDGE`        | `--effort-fit_judge`          | `effort_fit_judge`         |
| splitter           | `LEERIE_EFFORT_SPLITTER`         | `--effort-splitter`           | `effort_splitter`          |
| adherence_judge    | `LEERIE_EFFORT_ADHERENCE_JUDGE`  | `--effort-adherence_judge`    | `effort_adherence_judge`   |
| classification_judge | `LEERIE_EFFORT_CLASSIFICATION_JUDGE` | `--effort-classification_judge` | `effort_classification_judge` |
| wiring_judge       | `LEERIE_EFFORT_WIRING_JUDGE`     | `--effort-wiring_judge`       | `effort_wiring_judge`      |
| provision_judge    | `LEERIE_EFFORT_PROVISION_JUDGE`  | `--effort-provision_judge`    | `effort_provision_judge`   |
| task_coverage_judge | `LEERIE_EFFORT_TASK_COVERAGE_JUDGE` | `--effort-task_coverage_judge` | `effort_task_coverage_judge` |
| integration_judge  | `LEERIE_EFFORT_INTEGRATION_JUDGE` | `--effort-integration_judge` | `effort_integration_judge` |
| artifact_registry  | `LEERIE_EFFORT_ARTIFACT_REGISTRY` | `--effort-artifact_registry` | `effort_artifact_registry` |
| rebaser            | `LEERIE_EFFORT_REBASER`          | `--effort-rebaser`            | `effort_rebaser`           |
| judge              | *(none)*                         | *(none)*                      | *(none)*                   |
| heal               | *(none)*                         | *(none)*                      | *(none)*                   |
| pr_writer          | *(none)*                         | *(none)*                      | *(none)*                   |
| dep_capture        | *(none)*                         | *(none)*                      | *(none)*                   |

Note: `judge`, `heal`, `pr_writer`, and `dep_capture` are post-run / finalize-time
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

1. **Provision.** `scripts/remote/provision.sh::provision_machine` creates a
   Fly machine, writes `fly-machine.json` + `$LEERIE_STATE_HOST_DIR/remote/<launcher-pid>.json`
   immediately after `flyctl machine run` succeeds.
2. **Seed.** `scripts/remote/seed-auth.sh` + `seed-repo.sh` ship the
   laptop's Claude credentials + git identity + working tree to the
   worker via `flyctl ssh console` tar pipe. `seed-auth.sh:149-158`
   excludes git-push credentials by design — workers never see them.
3. **Orchestrate.** The orchestrator runs the standard
   classify → plan → execute → finalize phases on the worker.
4. **Decide teardown.** When the orchestrator exits, the launcher's
   `decide_teardown` trap fires on the LAPTOP (it's a trap on the
   bash process that sourced provision.sh; the worker's exit
   propagates via the SSH session's tail wrapper). The trap calls
   `fetch_branch` (pulls bundle + run-state), `host_finalize`
   (pushes branch + opens PR), `destroy_machine` (Fly DELETE).

The chain wave loop catches each per-job exit via `wait` and
captures the rc. The launcher_pid recorded in
`$LEERIE_STATE_HOST_DIR/remote/<pid>.json` is `$!` from the parent's
background spawn, which lets the wave loop discover each child's
`fly_machine_id` (= run_id) and tag the run with `chain_id` /
`wave_idx`.

#### chain_id discovery for chain-scoped verbs

The `chain_id` (UUID minted by `chain`) is written into each
chain run's `run.json` by the wave loop AFTER `host_finalize`
completes for that run. The launcher's `update_run_json` bash
helper (`scripts/remote/lib.sh:42`) merges the field atomically into
the existing JSON.

The tagging loop discovers each child's machine ID via two paths
(tried in order):

1. **Primary:** `remote/<child-pid>.json` — the PID-keyed pointer
   written by `provision.sh` during provisioning.
2. **Fallback:** scan `runs/*/fly-machine.json` for a matching
   `launcher_pid` field. This path fires when the pointer file is
   absent (e.g., older images whose `destroy_machine()` deleted
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

`leerie chain --chain-id <prior-uuid> --wave …` pins the chain_id
to a prior chain's UUID instead of minting fresh. The wave loop's
`_wave_already_done` check then matches the prior chain's runs and
skips fan-out for already-pushed waves, advancing `current_base`
through any wave-staging branches already pushed to origin. This is
the load-bearing recovery path after `leerie resume <chain-id>`
unpauses every paused run: the user re-submits with `--chain-id
<prior-uuid>` and the chain picks up at the first not-yet-done
wave.

The launcher normalizes the user-supplied chain_id to lowercase via
`tr '[:upper:]' '[:lower:]'` after UUID format validation. The
validation regex (`UUID_PATTERN`, defined near the top of the
launcher) is case-insensitive (`grep -qiE`) so uppercase input
passes; but the wave-loop helpers compare against `run.json`'s
`chain_id` field case-sensitively, and `uuid.uuid4()` always emits
lowercase. Without normalization, uppercase `--chain-id` input
would silently bypass idempotency and fork the chain into two
chain_ids — the v8 audit's S1 finding.

##### Synth-merge idempotency probe

Before invoking `chain.git_ops.synth_merge_branches` for wave
N → N+1, the wave loop probes origin via `git ls-remote
--exit-code origin leerie/stage/<chain-id>-wave-<N+1>`. If the
stage branch already exists (e.g., the user manually resolved a
prior synth-merge conflict and pushed), the wave loop fetches +
checks out the existing branch and skips synth-merge entirely.
Without this probe, `synth_merge_branches`'s `git checkout -B`
would force-recreate the stage branch from `$current_base`,
discarding the user's resolved state, and then re-merge the same
wave-N branches — re-conflicting in exactly the same way that
prompted the resume.

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

If the user Ctrl-Cs mid-chain or any job fails, the wave loop
exits non-zero with a resume hint. To resume:

1. `leerie resume <chain-id>` resumes every paused run (existing
   single-run resume per discovered run).
2. After paused runs complete, the user re-invokes
   `leerie chain --wave ...`. The wave loop's idempotency check
   (waves whose runs are all already `pushed_at` are skipped) lets
   the chain pick up from where it stopped.

The canonical "this run is done, don't re-spawn" sentinel is
`pushed_at` being set on the run.json — written by `host_finalize`
after `git push -u origin <branch>` succeeds. This is the same
sentinel `host_finalize` itself uses for push idempotency.

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

Modeled on the `chain` arm (`leerie:2033`). The `group` arm:

1. Parses repeated `--repo <path> "<prompt>"` pairs and an optional
   `--brief <file>`.
2. Fails fast if any repo path is not a git repository (mirrors
   the chain prompt-file check at `leerie:2136`).
3. Mints a `_group_id` (UUID, same mechanism as `chain`'s `_ch_id`).
4. **State-dir guard (mandatory).** Rejects or per-member-namespaces
   any `--state-dir` / `LEERIE_STATE_DIR` override in the calling
   environment. These override `_state_dir_default` (`:431`) and would
   pin every member to one shared state directory, causing a `.owner`
   collision on member 2. Chains (one repo) forward these safely;
   groups (N repos) must not. The guard must fire before any member
   is backgrounded.
5. Per member: builds the prompt as `<brief>\n\n<member prompt>`,
   appends `--inspect-dir <sibling-repo>` for every other member
   (reusing the inspect-dir translation at `leerie:3337`+), and
   backgrounds:
   ```bash
   # resolved once, before any cd, to an absolute path:
   _grp_self_cmd="${LEERIE_SELF_CMD:-$_grp_leerie_dir/$(basename "$0")}"
   ( cd <repo> && "$_grp_self_cmd" "<prompt>" <flags> \
       --group-id "$_group_id" ) &
   ```
   (mirrors `leerie:2237-2246` for chains). Each `cd` makes the member
   resolve its own `USER_REPO` and basename-keyed state directory
   independently. The self-command **must** be absolutized *before* the
   `cd`: a relative `$0` (e.g. `./leerie`, the documented quick-start
   form) would not resolve from the member's cwd once the subshell has
   `cd`'d into the member repo. Unlike chains — which never `cd`, so a
   relative `$0` still resolves — the group fan-out changes directory,
   so it anchors `$0` to the launcher's own resolved dir first.
6. Waits for all members (`wait`), then runs group tag-back (below).

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
`update_run_json … group_id "$_group_id"` (the same
runtime-agnostic atomic merge used by the chain wave loop,
`scripts/remote/lib.sh:70`).

No new per-child pointer file is required: the durable
`run.json`-on-disk is the coordination artifact, consistent with how
chains discover their members.

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

Emits matching run-ids one per line, filtered by the same per-verb
logic as `_chain_runs_filter` (`stop` / `kill` / `finalize` / `resume`
/ `running`). The key difference: `_chain_runs_filter` iterates
`$LEERIE_STATE_HOST_DIR/runs/*/run.json` (one directory); `_group_runs_filter`
iterates `<state_dir_N>/runs/*/run.json` for each supplied directory.

#### Deploy-ordering notes

When a member's planner declares a cross-repo prerequisite as
`requires.extent: external` (DESIGN.md §5), those entries accumulate
in `State.data["external_preconditions"]` (written at plan time,
`orchestrator/leerie.py:9727`). The entry shape is:
`{tag, reasons:[{sid, reason}], originating_subtasks}`.

The deploy-note plumbing threads `external_preconditions` from State
into the finalize path at three points:

1. **`_compose_pr_via_llm` payload** (`orchestrator/leerie.py:14590`):
   `external_preconditions` is added as a field in the JSON payload
   passed to the `pr_writer` worker, alongside `task`, `commit_log`,
   etc. The pr_writer prompt instructs the worker to render a
   "⚠ Deploy-ordering" section when the field is non-empty.

2. **`compose_pr_body` fallback** (`orchestrator/leerie.py:2119`):
   The deterministic Python fallback PR body is extended to render a
   "⚠ Deploy-ordering" section from `external_preconditions` when
   present in state. This ensures the deploy note appears even when
   the `pr_writer` LLM worker fails or is skipped.

3. **`host-finalize.sh` bash fallback** (`scripts/host-finalize.sh`):
   the pure-bash deterministic PR body (used when neither `pr_body`
   from the `pr_writer` worker nor the Python `compose_pr_body` output
   reached `run.json` — the LLM-less host-side finalize path) renders
   the same "⚠ Deploy-ordering" section from
   `state.json.external_preconditions` via `jq`. Its output is
   byte-for-byte identical to the Python renderer's section shape
   (`- **<tag>** — <reason>`, reasons `"; "`-joined; nothing emitted
   when the field is absent or empty), so the note survives even the
   LLM-less path. No `run.json` persistence is needed —
   `external_preconditions` is already a `STATE_FIELDS` key in
   `state.json`.

#### Run-summary cost line

Both deterministic renderers also emit a `- Cost:` line in the
`## Run summary` block (after `- Workers:`), sourced from
`state.json`'s `telemetry` block: `- Cost: $X.XX (N calls, I in / O out
tokens)`. Rendered only when the telemetry block is present (omitted on
pre-classify orphans), matching the deploy-note guard. Both renderers —
`compose_pr_body` (`orchestrator/leerie.py`, `${x:,.2f}` + `,`-grouped
tokens) and the `host-finalize.sh` `jq` fallback (`money`/`group`
helpers reproducing the same 2-decimal, thousands-grouped output) — are
format-identical except for a sub-cent rounding difference on an exact
half-cent `cost_usd` that never arises on a real summed cost. Like the
deploy note, no `run.json` persistence is needed — the `telemetry`
block is a `STATE_FIELDS` key.

**Key design note:** `reason` in `external_preconditions` is
unstructured free text (`required` is only `[tag, extent]`,
`orchestrator/leerie.py:731`). The group launcher, not the planner,
knows which sibling repos are group members — so the deploy note
identifies sibling members by injected group membership, not by
parsing planner free-text.

#### Planner steering (`prompts/planner.md`)

When a group member's planner receives a group brief (a shared context
block prepended by the launcher, marked `## Group brief` or similar),
`prompts/planner.md` contains a positive instruction directing it to:

1. **Read the sibling's contract.** Use `Read`, `Grep`, and `Glob`
   under `/inspect/<name>/` to locate and read the sibling's API
   surface, type definitions, schema, or interface files — not just
   the brief.
2. **Honor the interface.** Subtasks must conform to the sibling's
   actual types, field names, and endpoints as found in the code.
3. **Declare the dependency.** Add a `requires` entry with
   `extent: "external"` whose `reason` names the sibling repo and the
   specific contract item, for every subtask that depends on a
   sibling-owned contract.

This is advisory steering per DESIGN.md §12 ("prompts advisory, code
enforces"): the write-confinement guarantee stays code
(`_filter_offtree_subtasks`), not the prompt. The instruction lifts
reliable cross-repo-aware planning from emergent (task-text-driven) to
dependable (explicit prompt rule).

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
cli_value)` with
the standard CLI > env (`LEERIE_DANGEROUSLY_FORCE_STRICT_OUTPUT`) > `leerie.toml`
(`dangerously_force_strict_output`) precedence.

**Why it exists.** `--json-schema` is *validated*, not constrained: the CLI
injects the schema as a synthetic `StructuredOutput` tool with **no
`strict: true`** and no `output_config`. A meaningful fraction of
submissions are malformed as a result. Setting `strict: true` compiles
the schema into a sampling grammar and makes those shapes unrepresentable.

**Context-window side effect (`_model_arg`).** Owning `ANTHROPIC_BASE_URL`
makes the CLI treat the session as gateway-routed, so it applies a
conservative client-side context ceiling instead of the model's native
window. `_model_arg(model)` therefore appends `[1m]` — the documented
gateway-side selector for the 1M window — to any alias in
`_ONE_M_CONTEXT_MODELS` (`sonnet`, `opus`; **not** `haiku`, which has no 1M
variant and rejects the suffix) whenever `_STRICT_PROXY` is active. Inert on
the direct path, where `sonnet` already resolves to Sonnet 5's native 1M.

**Mechanism.** `_StrictOutputProxy` — an `asyncio.start_server` listener on
`127.0.0.1`, **one per run**, started in `_orchestrate()` before the first
worker and closed in its `finally` — which covers normal completion, `die()`,
and SIGINT. There is deliberately no `_cleanup_on_abnormal_exit` hook: on
SIGKILL the container boundary reaps the listener (DESIGN §6), which is the same
guarantee every other worker resource relies on. Workers
reach it via `ANTHROPIC_BASE_URL` injected into `worker_env`. The orchestrator
is PID 1 in the container and workers are its children, so loopback needs no
port mapping.

**Three entrypoints get their own instance, not `_orchestrate()`'s.** The
module-level `_STRICT_PROXY` global is what `_invoke` actually gates on
(`if _STRICT_PROXY is not None` — never `caps["force_strict_output"]`, which
only `_orchestrate` and `main`'s collision guards read). Any path that
invokes a worker without `_orchestrate` having run therefore sees no proxy:

| entrypoint | why it misses `_orchestrate` | where the flag comes from |
|---|---|---|
| `run_rebaser` | separate `python3` process (`scripts/host-finalize.sh`'s heredoc, §6) | `st.data["dangerously_force_strict_output"]` |
| `run_recapture_deps` | separate `python3` process (`./leerie config --recapture`'s seam, §6½) | same, re-read per `target_run_dir` |
| `main()`'s `--phase heal` branch | `return`s before `_orchestrate()` is called | `caps` — already resolved in `main()` |

All three wrap their worker call in the shared
`_strict_output_proxy(caps, label)` async context manager (defined beside
`_StrictOutputProxy`), which constructs, starts, and tears down one
short-lived instance scoped to that call. The two host-seam entrypoints
cannot call `resolve_dangerously_force_strict_output` meaningfully — the CLI
flag never crosses the process boundary, only `state.json` does — so they
read the value `_orchestrate()` already persisted for that exact run
(§ *State fields*), falling back to a CLI-blind resolve only for a
`state.json` predating that field. The heal branch needs no such read: it is
still inside `main()`, which resolved the flag (and passed `main()`'s
`ANTHROPIC_BASE_URL`/Bedrock collision guards) before the branch runs.
`_replay_capture`, which the heal loop drives, deliberately needs **no**
wiring of its own: it inherits whatever proxy is ambient, which is why
rebuilding its `caps` from `DEFAULT_CAPS` is harmless here.

The helper fails **soft in both directions**, deliberately departing from
`_orchestrate`'s fail-closed startup rule (DESIGN §7) because all three
callers are best-effort paths that must never block a push (`run_rebaser`'s
own contract), abort multi-run consolidation, or fail a heal: a `start()`
`OSError` logs and proceeds unconstrained, and a `stop()` failure is caught,
logged, and swallowed rather than escaping the `finally`. The global is
reset unconditionally either way, which `run_recapture_deps` depends on per
loop iteration: a stale non-`None` value would silently route the next
target run through the previous run's closed proxy. `run_recapture_deps`
additionally keeps a broad guard *around* `asyncio.run` as well as inside
it, since proxy construction sits outside the inner guard and a setup
failure must not abort the whole consolidation.

Before this wiring, all three built their own `caps` with no
`force_strict_output` key, so every worker they invoked ran unconstrained
regardless of the flag. `SCHEMAS["rebaser"]["diagnosis"]` is also narrowed
from `["string", "null"]` to plain `"string"` (the prompt's own convention
is "empty string" for "nothing to diagnose"), independent of and composing
with the proxy-wiring fix. `tests/test_strict_output_proxy.py`'s
`TestStrictOutputReachesEveryEntrypoint` pins all three entrypoints
structurally.

**Upstream read timeout.** `_StrictOutputProxy(max_parallel, verbosity,
upstream_timeout_sec)`, applied to the `urlopen` that forwards each request
and floored at the module constant `_STRICT_PROXY_TIMEOUT_SEC`.
`_orchestrate()` passes the **resolved** `caps["worker_timeout_sec"]`, not the
frozen default: that constant is `DEFAULT_CAPS["worker_timeout_sec"]`
evaluated at import, so once `--worker-timeout` could raise the ceiling above
it the proxy became able to give up before the worker — the exact outcome the
constant's own comment says it exists to prevent ("a shorter bound would kill
requests the worker is still legitimately waiting on, converting a slow call
into an unexplained worker failure"). The floor keeps a *lowered* worker cap
from making the proxy the first to quit either. Note the two bound different
things — the proxy bounds one upstream request, the cap bounds the whole
worker process — so this is an invariant repair rather than a reachable
failure.

| property | value | why |
|---|---|---|
| port | bind `0`, read back from `server.sockets[0].getsockname()[1]` | no scan, no race, concurrent runs never collide |
| executor | dedicated `ThreadPoolExecutor(max_parallel + 8)` | the default pool saturates under concurrent load |
| socket | `reuse_address=True`, `backlog=256` | without it the port is not rebindable after shutdown |
| shutdown | close listener, drain tracked writers, `_pool.shutdown(wait=False, cancel_futures=True)` | port is rebindable afterwards. `wait=False` keeps Ctrl-C responsive — a blocking join could hold the finally open for a full upstream timeout. `ThreadPoolExecutor` registers an atexit join, so an in-flight upstream call can delay the interpreter by up to `_STRICT_PROXY_TIMEOUT_SEC`; in the container the boundary reaps first |
| `ConnectionResetError` / `BrokenPipeError` | caught per connection, non-fatal | normal client hang-up |
| upstream | executor-bridged `urllib` | leerie has no async HTTP dependency |
| method | parsed from the request line and threaded to `_upstream` | only POST bodies are rewritten; every other verb the CLI issues must reach upstream as itself |
| chunked request body | decoded by `_read_chunked`, never rewritten | a chunked body carries no `content-length`, so a length-driven read forwards only the first packet — a silently truncated request, which reads downstream as a model error rather than a proxy bug |

**Logging reports categories, never a merged total.** The proxy runs in the
orchestrator process, so `log()` from the handler interleaves with every other
leerie line — there is no separate proxy log to go find.

Four counters, deliberately not merged: `passed_through` (no `StructuredOutput`
tool in the request — ordinary multi-turn traffic, measured at ~25-30% of POSTs
because the CLI injects the tool only on turns that want structured output),
`unexpected_tool_shape` (the tool IS present but duplicated or missing its
`input_schema` — the only pass-through worth warning about), `schema_errors`
(400s, the flag's own failure mode) and `transient_errors` (429/5xx, unrelated
to the rewrite). Echo budgets are **per class**.

A **renamed** tool is caught separately, at run level: it yields no matching
tool per request, exactly like an ordinary turn that never asked for structured
output, so it cannot be classified where the other shape problems are. If a run
ends having rewritten nothing while requests were proxied, the summary reports
a probable rename — once, so it cannot reintroduce per-request false positives.

The counters are deliberately not merged into one total: a merged count on a
healthy run can read as "the rewrite may be being rejected" even when the real
cause was a few transient errors consuming the shared echo budget, sending the
operator chasing nothing. Three levels, emitted by `_log_exchange`:

| when | verbosity | line |
|---|---|---|
| listener starts | all | `strict output: rewriting worker API requests via 127.0.0.1:<port> …` |
| upstream ≥ 400 | **all, including `quiet`** | `strict-output proxy: upstream <status> on <method> <path> (<what was changed>) — <response body>` |
| upstream < 400 | `debug` (`-vv`) only | `strict-output proxy: <method> <path> -> <status> (<what was changed>)` |
| run ends | all | rewritten / passed-through / upstream-error counts |

The error line is deliberately not verbosity-gated. This proxy is the only thing
in the path that rewrites a request, so a 4xx is most likely leerie's own edit
being rejected — and the response body names the offending schema path. Without
it the operator sees only workers retrying, which is precisely the
misattribution this flag's failure mode consists of. Echoes are capped at
`_STRICT_PROXY_ERROR_LOG_MAX` (3) bodies of `_STRICT_PROXY_ERROR_BODY_MAX` (400)
chars, because a rejected rewrite is systematic — every worker call fails the
same way — after which they are counted, not echoed. The count is complete
regardless, so the end-of-run summary never under-reports.

**Transform** (`_strictify_request(body) -> tuple[bytes, str] | None` — the
second element describes what changed and is what the log lines below report),
applied only when
exactly one tool is named `StructuredOutput` and carries an `input_schema`:
sets `strict: true`; adds `additionalProperties: false` to every object node
including inside array `items`; strips `minLength` / `maxLength` / `minimum` /
`maximum`; clamps `minItems > 1` to `1`. Verified against all entries in
`SCHEMAS` with zero residual violations.

**"Object node" is three shapes, not one.** `{"type": "object"}`, a *union*
type containing it (`["object", "null"]`), and a bare `properties` with no
declared type. The API requires `additionalProperties: false` on all three —
a transform that only handles the first shape 400s any schema with a nullable
object field (leerie's own `implementer.clarification_question` is one).

Verified against the **real API**, not just self-consistently against the
transform's own constants: `scripts/verify-strict-schemas.py` sends every
schema in `SCHEMAS` to the live API, deliberately outside `pytest.ini`'s
`testpaths` so the pytest suite stays LLM-free. Re-run it after editing any
entry in `SCHEMAS` or touching `_strictify_schema`. Pinned in the pytest
suite by `test_every_object_shape_is_hardened` (the three shapes) and
`test_no_schema_has_an_unhardened_object_shape` (the whole corpus, using an
independently-spelled definition of "is an object").

**Running the probe.** `python3 scripts/verify-strict-schemas.py`. It sends one
request per schema and exits **0** every schema compiles / **1** at least one
was rejected / **2** the control was *not* rejected, so the probe cannot detect
a rejection and a pass would be meaningless / **3** inconclusive — at least one
schema was throttled or timed out. **3 is not a pass**: a schema with no verdict
is unchecked, and the summary names which ones. Grammar compilation for a
large schema is genuinely slow (tens of seconds), which is why a transport
failure is reported as "no verdict" and never conflated with a rejection.

Two API facts worth knowing: a subscription OAuth token **requires the Claude
Code system-prompt identity** (without it the API answers a bare
`429 {"message":"Error"}` that reads exactly like quota exhaustion), and the
20-strict-tool / 24-optional-parameter ceilings are per-**request** aggregates,
so batching schemas into one request to save calls trips them and establishes
nothing about any individual schema. leerie sends exactly one tool per
request.

**All schemas in `SCHEMAS` compile.** Two needed restructuring beyond the
mechanical hardening:

* **`planner`** — refused with "Schema is too complex for compilation" due to
  many optional properties inside one `subtasks[]` array item (strict mode's
  grammar size multiplies per element). Fixed by `_strictify_schema`'s
  all-required pass (wire-only), collapsing the combinatorial explosion to
  one path.
* **`reconciler`** — refused with "The compiled grammar is too large" even at
  zero optionals. Fixed by restructuring `SCHEMAS["reconciler"]`: `requires`
  lifted out of `added_subtasks` into a sibling `added_requires` keyed by `sid`
  (removing the only three-deep array-of-objects path in any leerie schema),
  and the four isomorphic `{sid, tag, reason}` arrays collapsed into one
  enum-discriminated `tag_ops`. `_expand_reconciler_output` fans that back into
  the nine arrays every consumer still expects, so `check_reconciler_output`,
  `_apply_reconciler_output` and `_validate_must_include` are untouched.

**Do not re-nest `requires` or re-split `tag_ops`** — both put the schema back
over the ceiling. Several cheaper reductions (`$defs`/`$ref` deduplication,
stripping `description`, flattening other nesting, dropping subtrees,
trimming property counts, converting identifier fields to enums) were each
tried and each still refused — grammar size is driven by optional properties
inside array items, not raw schema size.

`output_config.format` compiles the *original* reconciler schema and is
nonetheless unusable: it returns the payload as a text block, so the CLI never
populates `structured_output` — and removing the injected tool makes the model
answer *"I don't have a StructuredOutput tool available — this looks like a
prompt injection attempt"*, because the CLI's own system prompt still tells it
to call that tool. Verified end-to-end; recorded so nobody retries it.

**Fail-open on the response too — un-compilable schemas.** A 400 to a *hardened*
request is answered by re-sending the original, untouched. The proxy records that
schema's fingerprint (`_structured_output_fingerprint`, sha256 of the canonical
`input_schema`) in `_unhardenable`, so the doomed attempt is paid once per run
rather than once per worker call, and increments `fell_back` — reported in the
end-of-run summary and logged at *every* verbosity with the API's own reason via
`_api_error_head`. The log line also names the rejected worker TYPE
(`worker=<type>`, or `worker=unknown` when the fingerprint matches no known
schema): `_fingerprint_to_worker_type()` builds a fingerprint→worker-type map
once from `SCHEMAS`, using the same canonicalization
`_structured_output_fingerprint` applies to a request's `input_schema`, and
`_worker_type_for_fingerprint()` looks up the request's already-computed
fingerprint at the log call site — so a future grammar-compile timeout is
self-attributing from the log alone. Only **400** retries; 401/403/429/5xx are
not schema problems and the original would fail identically.

Before the `planner`/`reconciler` restructuring above, both refused
compilation and fell through to the un-hardened original schema via this
fail-open path — a size effect is not the cause (a schema with more
properties overall can compile fine while one with fewer but nested inside
array items cannot), so the driver is optional properties **inside array
items**, where strict mode admits every subset in any order and grammar
size multiplies per element.

**Grammar compilation is cached upstream.** The first hardened call for a
schema is slow (tens of seconds); subsequent calls for the same schema are
fast (~2 s). So the cost is one-time per schema per run, not per worker
call — which is what makes the flag affordable, and what makes the
once-per-run `_unhardenable` memo worth having rather than re-probing.

**Fail-open / fail-closed.** Tool renamed, absent, duplicated, or wrong shape →
request forwarded byte-identical and the no-op logged (a silent loss of the
guarantee is the dangerous case). Listener cannot bind → `die()`, never a quiet
downgrade to unconstrained.

**Two fatal collisions, same contract.** The flag works by owning
`ANTHROPIC_BASE_URL`, so leerie `die()`s rather than proceed when that ownership
is contested:

1. **An operator-set `ANTHROPIC_BASE_URL`.** Overriding a user's gateway
   silently and silently skipping the requested guarantee are both wrong, so
   leerie names both and lets the operator unset one or drop the flag.
2. **Bedrock** (`AWS_BEARER_TOKEN_BEDROCK`, or a truthy `CLAUDE_CODE_USE_BEDROCK`
   — the same spellings the launcher's `detect_bedrock_mode()` accepts).
   `ANTHROPIC_BASE_URL` is the *first-party* endpoint override; Bedrock has its
   own (`ANTHROPIC_BEDROCK_BASE_URL`) and the proxy's upstream is hardcoded to
   `api.anthropic.com`. So the flag under Bedrock either does nothing — the CLI
   never contacts the proxy and the operator is silently handed post-hoc
   validation, the exact failure case 1 exists to prevent — or misroutes every
   worker call. Neither is distinguishable from a healthy run in the log, so
   this is a `die()`, not a warning.

**Stripped numeric bounds are re-checked in Python.** Of the 21 stripped
keywords, 16 are string-length bounds (15 `minLength`, 1 `maxLength`) whose
consumers already test truthiness. The 5 numeric ones (3 `minimum`, 2 `maximum`)
do not and fail permissively — `score = judge_result.get("score", 0.0)` then
`if score >= threshold` would read an out-of-range score as well-fit — so
`fit_judge.score` (0–1, `_recursive_decompose`),
`adherence_judge.instruction_adherence` (0–10, `phase_adherence_gate`) and
`provision.recipe[].timeout_s` (≥1, `_recipe_timeout_s`, used by both the
prompt-rendering and the baseline-install call sites) go through
`_bounded_or_conservative`.

These guards run **unconditionally, not only under the flag**: a value outside
its declared range was always a worker bug, and distrusting it is right whether
or not strict mode is what removed the bound. `timeout_s` additionally has a
pre-existing `or 1800` fallback that already absorbed `0`; the guard adds the
negative case, which is truthy and would otherwise reach
`wait_for(timeout=-5.0)` and fire instantly.

#### User prompt transport — stdin, not argv

`build()` obtains its argv from `_contained_claude_argv` and appends the system-prompt flag with **no positional argument
after `-p`** — the user prompt (task + subtask_views + any retry note)
is fed to the child's stdin instead, via `_invoke()`'s `stdin_data`
param. `_invoke()` writes that payload to a temp file **before** the
spawn and hands the child that file as its stdin; the file reaches EOF
on its own once read, which is the end-of-input the CLI needs to start
processing. `stdin=DEVNULL` otherwise (callers with no prompt to feed,
e.g. the preflight smoke test, are unaffected). The file is closed and
unlinked from `_invoke`'s `finally`, so a timed-out or crashed worker
cannot leak it, and it is created per call — so `claude_p`'s 2-attempt
retry, which appends a retry note, stages a fresh file rather than
replaying the first payload.

This exists because a single argv element cannot exceed Linux's
`MAX_ARG_STRLEN` (131,071 bytes, `PAGE_SIZE * 32`, not raisable) —
independent of the larger aggregate `ARG_MAX` — and reconciler/
plan_overlap_judge payloads routinely exceed that on their own. A positional
prompt after `-p` silently wins over stdin with no error, so it must be
absent, not merely supplemented. Pinned by `tests/test_prompt_over_stdin.py`:
the argv-length property (no `build()`-constructed argv element exceeds
`MAX_ARG_STRLEN` for a 150KB+ prompt), the absent positional, the retry path
routing `retry_note` through stdin too, `_invoke`'s file-vs-DEVNULL branch,
the whole payload being readable *at spawn time* (asserted inside the spawn,
since checking afterwards cannot distinguish "written before exec" from
"written during the run"), the staged file being unlinked, and a real
subprocess round trip for a 150,063-byte payload proving no deadlock with
the stdout/stderr readers.

**The prompt must be readable at `exec`, not delivered afterwards.**
`claude -p` waits a hard-coded **3 s** for its first stdin byte, then
removes its own `data` listener and proceeds without it — a late write is
**discarded**, not buffered, and the worker exits 1 with `Input must be
provided either through stdin or as a prompt argument`. leerie previously
made two synchronous broker round-trips (`_cgroup_create`, `_cgroup_enroll`)
between the spawn and the first write, each bounded by a timeout larger
than that 3 s deadline — an accepted stall the failure was permitted by
construction. A first fix hoisted the write into a task scheduled
immediately after spawn and moved the broker calls to `asyncio.to_thread`,
which narrowed the window without closing it: delivery still depended on
the event loop *scheduling* the feeder within 3 s, and under synchronous
bursts on the parent loop a pipe+feeder transport lost the prompt
reliably while a staged file lost none.

The transport is therefore a **file**, which has no writer to schedule and
so no deadline to lose. The broker calls stay on `asyncio.to_thread`
regardless: stdin no longer depends on it, but every other coroutine still
does. Pinned in `tests/test_stdin_feeder_ordering.py`, which asserts the
property negatively — no writer task, no `proc.stdin.write`, stdin never a
PIPE — because that is the form a regression takes, plus a behavioural pair
showing a pipe losing and a file winning against a real child under a
blocked loop.

#### Appended system prompt transport — file, with a probe + inline fallback

The appended system prompt (`system_prompt`, e.g. `reconciler.md` at
~25KB) is the *second* large argv element, and on the overlap judge it
compounds with the (now stdin-routed) user prompt toward the same
`MAX_ARG_STRLEN` ceiling above. `claude_p()` writes `system_prompt` to a
throwaway temp file once per call (not per retry attempt — the value is
fixed for the whole call) and passes it via `--append-system-prompt-file
<path>` instead of the inline `--append-system-prompt <text>`, removing
it from argv the same way the user prompt was removed.

`--append-system-prompt-file` is **undocumented** — it has no entry of
its own in `claude --help`, appearing only inside `--bare`'s help text
("Explicitly provide context via: --system-prompt[-file],
--append-system-prompt[-file], ..."). Because an undocumented flag may
be renamed or removed in a future CLI release without notice, its use
is gated behind `_append_system_prompt_file_supported()` — a
once-per-process probe memoized in the module-level
`_APPEND_SYSTEM_PROMPT_FILE_SUPPORTED` global (same pattern as
`_cgroup_probe()`'s `_CGROUP_PROBE_RESULT`) — with an unconditional
fallback to the inline flag when the probe reports unsupported.

The probe invokes `claude -p --append-system-prompt-file <throwaway
file>` with stdin closed and no `--output-format`/model dispatch
requested. Commander.js validates every flag before `-p` reaches "no
prompt given": an unrecognized flag fails immediately with `error:
unknown option '--append-system-prompt-file'`, while a recognized flag
instead reaches the CLI's own "Input must be provided either through
stdin or as a prompt argument" error. Both exit non-zero, cost nothing
(no auth, no model call), and return in well under a second — the probe
distinguishes them by the stderr text (`"unknown option"` means
unsupported), not by exit code alone, since both paths exit non-zero.

`claude_p()`'s temp file is created before `build()`'s first call, and
`build()` through the end of the retry loop runs inside a `try/finally`
that removes it once `claude_p()` returns — on both the success path and
every exception path out of that block (the terminal-auth raise, either
auth/quota-exhaustion raise, the final "worker failed schema-valid output
twice" raise). The schema-key drift guard runs before the temp file is
created at all, so it never needs cleanup. The retry loop (`_spawn`
re-invoked with a `retry_note`) reuses the same file across both attempts
rather than rewriting it, since `system_prompt` never changes between
retries.

Pinned by `tests/test_append_system_prompt_file.py`: the probe's
supported/unsupported/fail-closed-on-OSError-or-timeout branches, once-
per-process memoization, its own throwaway-file cleanup,
`build()`'s file-flag-vs-inline-flag branch and the temp file's
contents matching `system_prompt`, the temp file being removed after
`claude_p()` returns on both the success and exception paths, the retry
path reusing rather than recreating the file, and a live (unmocked)
sanity check against the installed `claude` CLI (skipped if absent).

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

This is the **last** arm of `_invoke()`'s no-envelope block, and
deliberately so: every arm above it (out-of-credits, OOM, nonzero exit
code) is a named, non-retryable condition and still raises. The nonzero-rc
arm in particular covers leerie's own deliberate kills (SIGTERM/SIGKILL),
which must never be retried — and the worker-timeout path raises
`subprocess.TimeoutExpired` before the block is reached at all.

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
(the numeric `api_error_status` check still applies, and still wins). They
exist to sniff a gateway message out of an envelope whose provenance is
unknown; leerie synthesizes its own envelopes and knows what they mean, so
text-matching them is wrong by construction. Concretely: the no-result
envelope interpolates the worker's **raw stderr** into `result`, and
stderr can legitimately contain `Invalid authentication` or `rate limit`
without the request having been auth-rejected — which would divert the
retry into this loop and burn the whole `auth_retry_max_sec` budget on a
non-auth failure. Pinned by
`tests/test_no_result_event_retry.py::test_worker_stderr_cannot_trip_the_auth_classifier`. On budget exhaustion the raised `WorkerError` names the
subscription cap for 401/429/auth-text and the transient overload for
529, so the user isn't told to wait for a usage window that isn't the
actual cause.

`_is_auth_or_quota_failure` only ever consults `api_error_status` or the
result text when the envelope's own `is_error` is truthy. A successful,
schema-valid envelope never enters the backoff loop, no matter what its
`result` text says — a worker whose task legitimately discusses API auth
or rate limiting (e.g. planning a rate-limited endpoint) would otherwise
trip the text markers on its own correct output, and `claude_p()` would
burn the full backoff budget re-running an already-successful worker
before eventually raising a false subscription-cap `WorkerError`.

Because `_is_auth_or_quota_failure` requires a *result envelope*, it
cannot classify an **out-of-credits mid-stream kill** — the case where
the `claude -p` process is terminated the instant credits run out, before
a `result` event is emitted (`_invoke` returns `envelope is None`). That
truncation is caught earlier, in `_invoke` itself: as events stream, a
`nonlocal overage_blocked` flag latches when a `rate_limit_event` carries
`overageDisabledReason in {"out_of_credits", "out_of_overage"}` — an
**exhaustion** reason. In the no-envelope branch, if `overage_blocked` is
set, `_invoke` raises `RateLimitedExit(reset_at=None, out_of_credits=True,
raw)` instead of a bare `WorkerError`, routing the failure into `main()`'s
pause-and-surface arm (worktree cleanup, `resume` hint, `EXIT_LOCKED`;
DESIGN §6). The latch does **not** key on `overageStatus == "rejected"`:
that is a standing state emitted by every `rate_limit_event` from an org
with overage disabled (`overageDisabledReason:"org_level_disabled"`,
`status:"allowed"`) and does not mean credits ran out — keying on it
misclassified unrelated mid-stream truncations as out-of-credits. The
overage event alone is *not* treated as terminal — it is a benign warning
most workers survive; only an exhaustion reason coinciding with a missing
`result` event triggers the pause. Covered by
`test_invoke_overage_block_plus_truncation_raises_ratelimited` (raises,
`out_of_credits=True`), `test_invoke_overage_block_with_result_returns_envelope`
(the benign control), and
`test_invoke_org_level_disabled_truncation_raises_workererror` (the
false-positive regression pin) in `tests/test_invoke_streaming.py`.

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

The first tenacity iteration runs without a pre-sleep — tenacity
sleeps *between* iterations, not before the first — so the effective
sequence is one immediate retry followed by waits of roughly 15 s,
30 s, 60 s, 120 s, 120 s up to the 300 s budget. Each `_invoke`
produces one `calls.ndjson` row, so a single logical `claude_p()`
call can write up to ~7 rows when the first outer schema-loop
attempt's backoff exhausts the budget (initial `_spawn` + ~6
tenacity iterations before exhaust), and up to ~13 rows in the rare
case where the first attempt's backoff resolves to a non-auth error
and the second outer attempt also enters backoff and exhausts. The
budget resets per outer schema-loop attempt; in that rare
double-burst case, total wait can reach ~10 minutes.

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
against a module constant `DISK_MIN_FREE_RATIO = 0.05` (5% free). It scales
with disk size without pretending to know per-run byte cost, and it is
available before any worktree exists — which matters, because it is the
only disk rule there is.

**The proportional ratio is the whole rule.** A per-worktree *measured*
bound was tried and withdrawn — the marginal cost of a not-yet-created
worktree depends on package-manager-store hardlinking (a mount topology
leerie does not control — DESIGN §6's `EXDEV` note), on sibling count at
measurement time, and on scheduling-dependent peak coexistence, so no
in-process accounting scheme converged on a stable figure. It is
measurable only from *outside* — a `df` delta across a real second
checkout in a real container — which is the only basis on which it
should be rebuilt. DESIGN's figures survive, labelled as a single
unreproduced measurement.

`tests/test_disk_preflight.py` guards the withdrawal by function name;
`tests/test_no_dead_functions.py` (a whole-module AST sweep) catches any
reintroduced-but-uncalled private helper regardless of name.

Signals whose reappearance means a specific already-fixed defect is back are collected in DESIGN §14½ *Regression tripwires*, guarded by `tests/test_regression_tripwires.py` — which distinguishes signals leerie EMITS (checked against the source, so a tripwire cannot quote a string the code never prints) from upstream messages it merely observes.

**What the floor alone still guarantees.** N30 was filed because disk
exhaustion "surfaces as a raw `OSError: [Errno 28]` from whatever happened to
be writing." Four mechanisms answer that, none of which needed the measured
bound. Note the coverage is *good, not total*: these convert every write
leerie owns, but a third-party `OSError` raised inside a worker's own
subprocess is still that worker's to report.

1. **Preflight** (`preflight(leerie_dir, ...)`, check "0.5", before the git
   identity checks and before the live smoke test — i.e. before any
   worker spawns): `_disk_free_ratio(leerie_dir)` below the threshold
   `die()`s with an actionable message (`_disk_headroom_message`) naming
   the measured free/total GB and the filesystem path. `leerie_dir` here
   is `st.run_dir`, already created by `main()` before `preflight` runs,
   so it resolves to the state-dir filesystem.
2. **Mid-run** (`phase_execute`'s wave loop, once per wave — before that
   wave's memory-admission/settle work begins): the same check raises
   `DiskLowSpace` (a `BaseException`, same shape as `ContextOverflow` —
   never a `WorkerError`, so `_run_checked_loop` cannot swallow it into a
   retry) rather than `die()`ing, since workers have already spawned and
   there is state worth preserving. `main()` catches it in its own arm
   (mirroring the `ContextOverflow` arm immediately above it): worktree-
   only cleanup (`_cleanup_on_abnormal_exit(st, full_purge=False)`),
   best-effort `capture_repo_deps`, a resume hint, and `EXIT_LOCKED` — the
   same resumable-pause convention documented above for rate-limiting.
   `DiskLowSpace` was added to every existing `except (Exception,
   TerminalAuthFailure, RateLimitedExit, ContextOverflow)` dep_capture
   guard alongside its siblings, since those tuples exist to catch the
   whole `BaseException` exit-signal family.
3. **`State.save()` itself** is the first of two *reactive* checkpoints,
   alongside the two proactive ones above: a disk can cross zero between one
   periodic check and the next write, so `save()`'s `tmp.write_text()` /
   `os.replace()` pair is wrapped in a `try/except OSError` that reraises
   an out-of-space failure as the same `DiskLowSpace`, caught by the same
   `main()` arm described above. "Out of space" is `_OUT_OF_SPACE_ERRNOS` =
   `{ENOSPC, EDQUOT}`: a state root on a quota'd home or an NFS mount
   reports `EDQUOT` where a local disk reports `ENOSPC`, and a site that
   converts one must convert both or the same exhaustion surfaces as a bare
   `OSError` on exactly those hosts. Any other `OSError` (permissions, a
   read-only mount unrelated to capacity) propagates unchanged.
4. **`_invoke`'s prompt staging** is the second reactive one, and closes the
   gap the wave-granularity of (2) leaves open. Each worker invocation writes
   its prompt to a temp file, once per attempt — the largest disk write
   leerie makes per worker — and its reap-then-reraise guard converts
   `_OUT_OF_SPACE_ERRNOS` to `DiskLowSpace` on the same terms as (3). The
   `tempfile.mkstemp()` call is INSIDE that guard, which is not cosmetic:
   block exhaustion lets `mkstemp` succeed (an empty file needs no data
   blocks) and fails the later write, while **inode** exhaustion fails
   `mkstemp` itself — two real conditions reach the create, and with it
   outside the guard both escaped as a bare `OSError`. The proactive check
   in (2) runs once per wave and cannot see a disk that crosses zero
   mid-wave, which is exactly when this fires.

**Saving from inside a handler.** Every terminating arm in `main()` persists
state so the run stays resumable, and each does it through
`_save_state_best_effort(st, where)` rather than a bare `st.save()`. A raise
from inside an `except` block escapes `main()` unnoticed by any sibling arm
— skipping cleanup and the `exit_code` assignment, so a resumable pause
becomes an exit-1 traceback — and in the catch-all `except BaseException`
arm the new exception REPLACES the unhandled one, leaving the real bug
reachable only as `__context__`. Both triggers are real: `State.save()`
converts an out-of-space errno to `DiskLowSpace`, and a read-only run dir
raises `PermissionError`. The helper logs the failure rather than
swallowing it, and `tests/test_disk_preflight.py` sweeps `main()` for any
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
`_is_auth_or_quota_failure` — an expired or revoked session is a
different failure class from a rejected-but-recoverable request, and
must never enter the tenacity backoff loop above at all (see DESIGN §6
*Credential strategy* / *Cleanup on abnormal exit*'s transient-vs-terminal
split for the rationale: Claude Code sends no further request once it
detects an expired session, so retrying is guaranteed to fail
identically every time and only burns the `auth_retry_max_sec` budget).

It mirrors `_is_auth_or_quota_failure`'s gating discipline: `False` unless
`envelope["is_error"]` is truthy; `False` for any envelope carrying
`_leerie_synthetic` (worker prose can never reach `result` on those —
only the no-result-event synthetic path can, and that is handled
separately above; text-matching a leerie-authored envelope for a
gateway-shaped message is wrong by construction, same reasoning as the
auth/quota classifier). On a genuine (non-synthetic) `is_error`
envelope, it lowercases `result` and matches any of four substrings:
`"failed to authenticate"`, `"oauth session expired"`, `"session expired
and could not be refreshed"`, `"not logged in"`. The second marker is
intentionally the full phrase `"oauth session expired"` rather than the
shorter `"oauth"`, which appears often in worker `tool_result` blocks
discussing OAuth and would misclassify ordinary worker output.

A match raises immediately (never entering `_is_auth_or_quota_failure`'s
tenacity loop) into the same resumable-pause arm out-of-credits already
uses: `main()` runs `_cleanup_on_abnormal_exit(st, full_purge=False)`
(worktree-only cleanup, state and branches preserved), logs a `leerie
resume <id>` hint, and exits `EXIT_LOCKED` (75) rather than `WorkerError`
→ exit 1. This also replaces the prior behavior at the auth-exhaustion
exit point (§3 above, `claude_p()`'s budget-exhausted `WorkerError`):
that path previously surfaced as "worker failed schema-valid output
twice," misattributing an auth failure to a schema problem, and exited
non-resumably. `_is_terminal_auth_failure` and the budget constant live
alongside `_is_auth_or_quota_failure` in `leerie.py`.

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
env-var reassignment, not a parallel credential-construction path, so the
existing mcpOAuth-guard, die()-fast diagnosis, and
`_check_claude_credential_ttl` in `_extract_claude_credentials_json`
(above) apply unchanged to whichever token seeds the mounted
`.credentials.json`. The launcher also forwards the raw
`CLAUDE_CODE_OAUTH_TOKENS` value into the container as its own `-e
CLAUDE_CODE_OAUTH_TOKENS=...`, alongside the existing single-token `-e`,
so the orchestrator can probe/select across the full list independently
of which token seeded the file. `scripts/remote/seed-auth.sh` and
`scripts/remote/ec2-seed-auth.sh` mirror the same plural-forwarding as a
sibling condition to their existing single-token fallback block.

**Orchestrator — per-invocation env threading (the mechanism that makes
rotation possible without a container restart).** `_invoke` takes an
explicit `active_token: str | None = None` parameter. When given, it
builds `worker_env = os.environ.copy()` (independent of the pre-existing
`LEERIE_WORKER_DEBUG`-gated debug env, which still applies its own
overrides on top) and sets
`worker_env["CLAUDE_CODE_OAUTH_TOKEN"] = active_token` before
`create_subprocess_exec(..., env=worker_env)`. Per the Claude CLI's own
documented authentication precedence (`CLAUDE_CODE_OAUTH_TOKEN`
outranks `.credentials.json`/Keychain subscription credentials
unconditionally — see `code.claude.com/docs/en/authentication`,
"Authentication precedence"), this env var alone is sufficient to steer
which credential a given `claude -p` spawn uses; no rewrite of the
mounted `.credentials.json` is needed on token switch. `claude_p`'s
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
`memory.max` — so a build on a Node repo can abort with a V8 heap OOM
while most of leerie's (larger) per-worker memory allowance sits unused.
`_invoke` detects a Node repo via `_is_node_repo(cwd)` (presence of
`package.json`, `pnpm-lock.yaml`, `package-lock.json`, or `yarn.lock` at
the worker's cwd) and, when `worker_memory_max_bytes` is set, injects
`NODE_OPTIONS=--max-old-space-size=<N>` into `worker_env` as a fourth
sibling conditional block alongside the debug/token/strict-proxy blocks
above. `N = max(worker_memory_max_bytes // (1024*1024) - reserve, 256)`,
where `reserve` is `_NODE_HEAP_HEADROOM_BYTES // (1024*1024)` — read from
that one constant, never retyped, so this injection and
`resolve_worker_memory_max`'s heap reconciliation (which computes the
mirror image, heap → cap) cannot drift. The reserve covers Node's own
non-heap RSS plus the resident `claude -p` process sharing the same
cgroup; the `max(..., 256)` clamp guards the explicit-override path
(`--worker-memory-max` / `LEERIE_WORKER_MEMORY_MAX` / leerie.toml
`worker_memory_max`, none of which share the auto-derive path's 8 GiB
floor) from handing V8 a non-positive or degenerately small ceiling. The
variable is absent entirely for a non-Node repo or when
`worker_memory_max_bytes` is `None`.

**Start-of-run probe + selection.** After `preflight()` returns and before
`phase_classify`, if `CLAUDE_CODE_OAUTH_TOKENS` is present, each token is
probed for remaining runway and the winner becomes
`st.data["active_oauth_token"]`:

- **Probe A** (tried first): `GET https://api.anthropic.com/api/oauth/usage`
  with `Authorization: Bearer <token>`, `anthropic-beta: oauth-2025-04-20`,
  and `User-Agent: claude-code/<version>` — omitting the User-Agent places
  the request in an aggressively rate-limited bucket (persistent 429s);
  with it, ~180s polling is safe (both behaviors externally corroborated,
  not merely assumed). Returns `five_hour`/`seven_day` objects with
  `utilization` on a **0–100** scale and `resets_at` as ISO-8601 with a
  UTC offset, plus optional `seven_day_opus`/`seven_day_sonnet` sublimit
  objects (`null` when no usage of that model has been recorded — treated
  as zero usage, i.e. full runway, not as missing data). Requires
  `user:profile` scope; a `user:inference`-scoped token (e.g. a `claude
  setup-token` mint, which is what leerie itself uses) gets **403** here.
- **Probe B** (403 fallback): `POST /v1/messages` with `max_tokens: 1` and
  a one-character user message, reading the
  `anthropic-ratelimit-unified-5h-utilization` / `-5h-reset` /
  `-7d-utilization` / `-7d-reset` / `-5h-status` response headers.
  **These headers use a different representation than Probe A's JSON
  body**: utilization is a **0.0–1.0 fraction** and reset is **Unix epoch
  seconds**, not the 0–100/ISO-8601 shape Probe A returns.
  `_probe_token_usage` normalizes both probes onto one internal
  representation (0.0–1.0 fraction, `datetime` reset) before returning, so
  ranking never has to know which probe produced a given result.
  `/v1/messages/count_tokens` does **not** carry these headers — a real
  inference call is required.
- **Ranking** (`_rank_tokens`): sorts by `min(1 − five_hour_util, 1 −
  seven_day_util)` descending (accounting for the Opus sublimit, since
  leerie's judgment workers default to Opus), tie-broken by furthest
  `resets_at`. A token whose probe failed entirely (as opposed to a `null`
  sub-field) sorts last but remains eligible.
- **Cache**: each token's probe result is cached, keyed by
  `_token_fingerprint(token)` (never the raw token), for
  `caps["token_probe_cache_sec"]` (default 180s) — both the start-of-run
  selection and mid-run failover below share this cache and never
  re-probe a token whose cached result is still fresh.
- **Best-effort, never a hard gate**: if every probe fails, the first
  token in the list is selected and the run proceeds — probing never
  `die()`s. A transient probe failure (timeout, connection error, 5xx, a
  429 on the probe itself) logs quietly; a 2xx response missing an
  expected field (endpoint contract drift — these are undocumented,
  unstable endpoints) logs loudly at WARNING with the stable marker
  `token-probe: endpoint contract drift` plus the missing field name, so
  a silent shape change doesn't quietly degrade this feature to
  "always pick the first token" with no signal. A 401/expired token is
  logged as a real per-token dead-token signal, distinct from both of the
  above.

**Mid-run failover.** A rate-limited active token can reach `claude_p`
through TWO independent surfaces, and both are covered by one shared
helper, `_rotate_oauth_token_or_raise(st, caps, *, known_reset_at,
raw_message, retry_fn)`:

1. **Protocol-level**: a `rate_limit_event` stream event (an unexpected
   `status`, i.e. outside the known-allowed set) is detected inside
   `_invoke`'s own streaming loop and raises `RateLimitedExit` directly —
   `_spawn` never returns an envelope at all for this case. `claude_p`'s
   retry loop wraps `await _spawn(retry_note)` in
   `try/except RateLimitedExit`, catching it *before* it can propagate to
   `main()`'s single-token pause/auto-resume path. `out_of_credits=True`
   bypasses rotation entirely and re-raises immediately unchanged — an
   account-level exhaustion is not a per-token rate limit, and rotating
   tokens would not help.
2. **Envelope-level**: once `_spawn` returns a completed envelope, if it
   is a rate-limit/quota failure (`_is_auth_or_quota_failure`, not
   terminal-auth) and `CLAUDE_CODE_OAUTH_TOKENS` has more than one token,
   the same helper is called again (checked between the terminal-auth
   check and the tenacity backoff loop's entry).

In both cases the helper: probes/ranks the *other* tokens (respecting the
shared cache); if one has runway, switches `active_oauth_token` and
retries the invocation immediately via the caller-supplied `retry_fn` —
no re-exec, no container restart, strictly before any of
`auth_retry_max_sec` is spent on a token already known to be exhausted.
If every token is currently rate-limited, it picks the one with the
soonest `resets_at` — preferring a live signal (`known_reset_at`, e.g. a
just-caught `RateLimitedExit.reset_at` for the protocol-level path, which
has no probe-cache entry to fall back on for the active token since it is
deliberately excluded from the fresh probe/rank call) over a possibly
stale or absent `_TOKEN_PROBE_CACHE` entry — and raises the existing
`RateLimitedExit`, which the pre-existing `_sleep_then_reexec` reset-wait
path picks up unchanged. A probe failure, or no probe data for any token,
never raises from the helper itself (returns `None`); each call site
falls through to its own pre-existing behavior (re-raising the caught
exception, or continuing into the tenacity backoff loop, respectively).
Terminal-auth failures are entirely unaffected by this feature — a
dead/expired credential is not a rate limit and is never rotated.

Maps to `DESIGN.md`: §6 *Multi-token rotation*.

---

## 4. Phase walkthrough (`leerie.py`)

| Phase | Function(s) | What it does |
|-------|-------------|--------------|
| Preflight | `preflight` | disk headroom on the state-dir filesystem (N30 — see "Disk headroom" above), git identity, clean working tree, external `leerie` branch collision (DESIGN §3 *External collision hazard*), `claude` CLI version, live `claude -p` smoke test. The smoke test uses the shared contained argv (`_contained_claude_argv`, so tool denies + `--strict-mcp-config` apply), `SMOKE_MAX_TURNS`=5 against a measured happy path of 3, and the **resolved `classifier` model** — the tier the run's first worker actually spawns with, and the value `_model_arg` needs in order to restore `[1m]` behind the strict proxy. It runs in an **empty cwd with no repository ancestor** — a stable path under the system temp dir, named from a hash of the state root (the CLI resolves project context by walking UP from cwd, so a directory under `<repo>/.leerie` would reload the very CLAUDE.md this avoids; the state root takes that shape whenever `LEERIE_STATE_DIR` is unset). Not cleaned up: it is empty by construction, and two runs of one state root share it, so removing it would unlink a concurrent run's live cwd. It validates the CLI, not the repo, and a single-exchange conversation cannot be rescued by the CLI's own reactive compaction (`too_few_groups`), so its prompt must stay structurally below the compaction trigger. A client-side context refusal is classified via `_is_context_overflow` and **raises `ContextOverflow`** (resumable `EXIT_LOCKED` pause) rather than printing a bare `Prompt is too long`. Run-id collisions are detected at two points: filesystem side in `State.__init__` (the run dir is created at container start since the run-id is the container/machine ID); git side in `setup-run.sh`'s branch-creation step. `setup-run.sh` repeats the external-branch check as defense-in-depth for `resume`. Smoke test bypassed by `--skip-smoke`; preflight skipped entirely on `resume` |
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
(`<state-root>/runs/<run-id>/plan.json`, which carries the full task text
under its top-level `"task"` key) and per-subtask spec files
(`<state-root>/runs/<run-id>/subtasks/<id>.json`). It also writes
`<state-root>/runs/<run-id>/task.md`, the task text verbatim as plain
markdown. Each spec file carries `_task_ref` — the path to that
`task.md` — plus `_task_ref_bytes`, its size, rather than a second copy
of the task text: no prompt reads a `_task` field, and inlining the full
task into every subtask spec was measured to bloat briefs significantly
on large task documents, spilling past the CLI's Read cap.

`_task_ref` points at `task.md` and **not** at `plan.json`, which is by
construction the task text plus every subtask body — strictly larger than
any single brief it replaced — so referencing it relocates the Read-cap
failure instead of removing it. Format carries the rest: the cap is
25,000 **tokens**, and markdown measures meaningfully more bytes/token
than JSON, so the same text can sit over the cap inside `plan.json` but
under it as markdown. On large task documents that alone isn't enough;
the implementer prompt's `offset`/`limit` guidance, keyed on
`_task_ref_bytes`, is what keeps the read from failing. The conformance
phase derives its advisory build/lint/test commands separately via
`_infer_build_lint_test(repo_root)`, which performs best-effort
discovery by checking for configuration files and lockfiles. Supported
families (checked in this order; first match wins per axis via
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
applicable" — same convention as inference — and is preserved rather than
replaced by inference. The file also accepts a `setup_packages` key
(comma-separated apt package names) that triggers per-repo Dockerfile
auto-generation (see §6½ *Auto-capture of repo dependencies*); it is
not consumed by BLT resolution.

Resolution is implemented by two functions:

- **`_load_blt_config(repo_root: Path) -> dict[str, str] | None`** — reads
  `.leerie/config.toml` via `_read_toml_key()` for each of `build`, `lint`,
  `test`, `setup_packages`. Returns `None` when the file is absent; returns a
  dict containing only the keys present in the file (no defaults for absent
  keys).

- **`resolve_blt(repo_root: Path) -> dict[str, str]`** — calls
  `_load_blt_config()`; for each axis, uses the declared value if present
  (including empty string), otherwise falls through to
  `_infer_build_lint_test()`. Logs which axes came from config vs inference.
  This is the function called by both `_run_conformance_phase` and
  `_run_final_conformance` — neither calls `_infer_build_lint_test` directly.

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
reconciler attempt (any `added_subtask` with `size: large`), `phase_reconcile`
deep-copies the pre-mutation plans, reverts the failed mutations, builds a
retry prompt (in `_build_size_retry_prompt`) that names each offending sid,
its `provides`/`requires`/`depends_on`, and the explicit decomposition rule
("emit one subtask per `provides` tag, or smaller groupings that share state"),
then respawns the reconciler worker once with that prompt. Maximum two
attempts total — mirrors the cycle-retry shape. Cost: one extra reconciler
spawn on oversize runs only; non-oversize runs pay nothing extra.

No recommendation heuristic is computed (unlike the cycle loop): the
mechanical guarantee — "split it into N subtasks each providing one
`provides` tag" — is rendered directly into the retry prompt, and is
also documented in `prompts/reconciler.md` on the first attempt; the
retry is the enforcement.

The size gate runs *before* the acyclicity gate because oversize
authoring is an upstream defect — a `large` subtask bundling several
capabilities is also more likely to produce a cycle, so splitting first
lets the cycle gate evaluate the cleaner graph.

**Retry composition (snapshot refresh).** When multiple retries fire on
the same run (e.g., size retry succeeds and then the cycle gate fires),
each successful retry refreshes `pre_plans_snapshot` to the post-retry
state, so the next retry's revert restores the most recent good state
rather than undoing an already-successful split. The unresolved retry
doesn't refresh because it's the last gate before `phase_reconcile`
returns.

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
   recommend `drop_require` on the rename that closes the reverse
   direction (planner ordering wins; the reconciler's rename is the drift).
2. **Else SCC members share `files_likely_touched`** → recommend
   `merged_subtasks(into, from)` where `into` is the smaller subtask by
   `success_criteria_seed` length (tie-break: lexicographic sid) — they
   author the same file, so one commit does both pieces of work.
3. **Else** → recommend `drop_require` on the rename whose `from` tag
   had no planner-declared producer in the pre-reconcile graph (the
   rename was speculative — dropping the requirement is structurally honest).
4. **Tie-breaker of last resort** → drop the lexicographically later rename.

The retry prompt presents the recommendation as the answer, not as one of
several options, and explicitly forbids `unresolvable` for cycle
resolution — the model must commit to one of the bounded operations or
echo the recommendation. The mechanical floor (gate + must-include) is
the guarantee; the recommendation primes the model toward the correct answer.

**Unresolved-requires retry loop.** Symmetric architecture to the
cycle-resolution loop, fired by a different gate. When the post-mutation
`_compute_unresolved_requires` set is non-empty (after the cycle gate
has already cleared), `phase_reconcile` deep-copies the pre-mutation
plans, computes a string-similarity recommendation per unresolved entry
(in `_recommend_unresolved_resolution`), builds a retry prompt (in
`_build_unresolved_retry_prompt`) that surfaces the unresolved
`(sid, tag)` pairs, the top-3 candidate `provides` ranked by Jaccard,
the recommendation (if computed), and the bounded must-include set,
then respawns the reconciler once. Maximum two attempts total; cost
mirrors the cycle retry.

The recommendation heuristic is deterministic but framed as a *hint*
(not the answer), since the underlying signal is textual string
similarity and can produce false friends. Two guards filter candidates
before scoring: a **self-loop guard** skips candidates whose provider is
the consumer's own sid, and an **extent-aware** guard admits only
`extent: in_plan` entries. Cases (first match after guards wins):

1. **Unique top match with Jaccard ≥ 0.5** → recommend
   `rename(sid, from=tag, to=top.tag)`.
2. **Top match with Jaccard ≥ 0.7 (even if not unique)** → same.
3. **Else** → no recommendation; model picks unaided (the common case —
   most post-mutation unresolved entries lack a strong-similarity
   candidate).

`unresolvable` IS valid for this retry (unlike the cycle retry's
strict forbid) — if no real producer exists for the tag, surfacing
that cleanly is the right answer. The mechanical floor (must-include
validator + post-retry unresolved + cycle re-check) catches every
malformed revision; the recommendation is best-effort.

### Phase 2¾ checks — `phase_overlap_judge`
| Check | Catches |
|-------|---------|
| **deterministic duplicate-provider floor** (`check_duplicate_providers(plans) -> list[str]`, `DUPLICATE_PROVIDER`) — runs **before** the cheap-skip and independently of `--skip-overlap-judge`, so it is evaluated on every path including single-planner runs and a `plan_overlap_judge` `WorkerError` | two subtasks that declare the **same `provides` tag** AND whose `files_likely_touched` intersect (paths canonicalized with `_normalize_artifact_path`, the same helper `check_overlap_judge_output`'s `NO_FILE_OVERLAP` uses — it strips a leading `/`, which `os.path.normpath` keeps, so `/src/x.ts` and `src/x.ts` are not read as distinct files) — i.e. two subtasks doing the same work to the same file. Pure set logic over structured planner fields; no prose is read (DESIGN *Language-to-JSON*). **Exclusion (load-bearing):** pairs sharing a non-`None` `_cofile_cluster` are the deliberate sub-file region splits of one file (§5½ (P1) *Sub-file*) and are never flagged — without it the rule floods the corpus with false positives on legitimate sub-file splits, drowning the genuine duplicate-work cases it exists to catch. An "already ordered by `depends_on`" exemption is deliberately **not** implemented — no such pairs have been observed, so adding it would be untested speculation. `check_duplicate_providers` itself remains advisory (`log()` only) and unchanged — see the routing row below for the M11 resolution step. |
| **duplicate-provider merge routing** (`_duplicate_provider_merge_collisions(plans) -> list[dict]`, applied via `_apply_overlap_collisions`) — M11 DECISION: the floor's detections are resolved, not just logged | a standalone mirror of `check_duplicate_providers`'s detection logic (same `_cofile_cluster` exclusion, same `_normalize_artifact_path` file-overlap test) that synthesizes one `resolution: "merge"` collision per flagged pair and feeds them through the **same** `_apply_overlap_collisions` the judge's own output uses — reusing its per-resolution `_would_cycle_after` guard, `skipped_redundant` dedup, and (for the N-way case) its anchor + transitive `survivor_of` cluster resolution, rather than reimplementing any of that. Runs immediately after the advisory log lines, above every cheap-skip, so it fires on single-planner plans and `--skip-overlap-judge` runs too — exactly the paths where the judge itself never gets a chance to resolve the collision. Safe for the 3-participant (or larger) case specifically because `_apply_overlap_collisions`'s transitive chase is a *merge* chase: unlike the `_apply_multidrop` cluster path (a separate mechanism for `drop_*`, not reused here), a merge's intent assembly carries the absorbed subtask's full intent forward, so no live subtask's intent is silently discarded — a triangle of duplicate-provider pairs (A↔B, A↔C, B↔C) collapses to one survivor with the closing edge recorded as `skipped_redundant`, never a dangling dependency target. Persists the post-apply mutation summary (same shape as `plan_overlap_applied`) to `state.data["duplicate_provider_merge_applied"]`, absent when the floor found nothing to merge. |
| **cheap-skip when impossible** (fewer than 2 planners contributed subtasks, OR total subtask count < 2) | spurious worker spawn on single-planner / trivial runs. No `plan_overlap_judge` call in `calls.ndjson`; log line `phase 2¾: overlap-judge skipped (single planner)` or `… (< 2 subtasks)` at normal verbosity. |
| judge output validated against `SCHEMAS["plan_overlap_judge"]` | malformed judge response (caught by `claude_p`'s schema gate; structurally invalid output retried once, then escalated per the standard policy). |
| **merge-feasibility backstop** (`_validate_overlap_judge_output`) — every collision with `resolution == "merge"` must carry non-empty `merge_feasibility` | the judge skipping the merge-feasibility discipline section in `prompts/plan_overlap_judge.md`. Per `DESIGN.md §12` (prompts advisory, code enforces): the prompt asks for `merge_feasibility` whenever `merge` is emitted, and Python rejects a `merge` without it. `die()` with the offending pair (`a_sid`/`b_sid`/`artifact`). |
| **`merge` apply step** (`_apply_overlap_merge`) | collapse the two subtasks: surviving sid is the lexicographically smaller id by default (a determinism device — same merged plan regardless of pair argument order) OR the value of the optional `survivor_hint` parameter when the caller is applying the anchor-survivor rule for a cluster. Surviving subtask gets the union of `files_likely_touched`, `provides`, `requires`, `depends_on` (with self-references removed); `title` becomes `"{survivor.title} + {dropped.title}"`; `intent` is the concatenation of the survivor's existing intent, the absorbed subtask's full existing intent (under a `--- Absorbed intent from {dropped.id} ---` marker), and a trailing `"Merged with {dropped.id} by plan-overlap-judge:\n{judge.merge_feasibility}"` note. Carrying the absorbed subtask's intent is required by the DESIGN §5 *merge_feasibility carry-forward* invariant: any merge_feasibility statement previously appended to the absorbed subtask's intent (from an earlier merge where it was a survivor) must be preserved. `success_criteria_seed` becomes `"{survivor.criteria} AND {dropped.criteria}"`. Downstream subtasks whose `depends_on` referenced the dropped sid are rewritten to point at the surviving sid. Records the mutation in `state.data["plan_overlap_applied"]`. |
| **`drop_a` / `drop_b` apply step** (`_apply_overlap_drop`) | remove the dropped sid; union the dropped subtask's `provides` tags into the survivor's `provides` (deduped, order-preserving — without this union, any downstream `requires` that matched the dropped subtask's tags would orphan into a confusing `_validate_plan` error rather than resolving cleanly against the survivor); drop any survivor `extent: in_plan` requires whose tag is now in the post-union provides (would-be graph self-loop, mirrors `_apply_overlap_merge`); rewrite downstream `depends_on` references from the dropped sid to the survivor. Title / intent / success_criteria_seed are NOT copied from the dropped subtask — the judge said one intent supersedes the other, so the survivor's intent is the intent that wins; only the capability-graph wiring is unioned. |
| **anchor-survivor rule** (`_apply_overlap_collisions` + `_compute_overlap_anchors`) — pairwise collisions resolve into a coherent cluster decision | shared-endpoint clusters where one subtask appears in 2+ non-`unresolvable` collisions (an *anchor*, e.g. judge emits both `merge(A, B)` and `merge(A, C)` because A overlaps with B on one artifact and with C on another). The apply loop passes `survivor_hint=anchor_sid` into `_apply_overlap_merge` when exactly one of the pair's endpoints is in the anchor set, so the anchor survives that merge (overriding the default lex-smaller rule, which is a determinism device with no semantic content). In the all-merge cluster above the anchor is indeed the broader subtask, but that is a property of *that shape*, not of anchor membership — membership is bare appearance count and a sid dropped twice is an anchor too (see `:3194`). When both endpoints are anchors — legitimate within a single connected cluster, e.g. the closing edge of a triangle — the rule falls through to lex-smaller; the merged subtask still carries forward every prior `merge_feasibility` via the absorbed-intent block (DESIGN §5 carry-forward invariant). A `survivor_of: dict[str, str]` map rewrites later pairs against earlier survivors so a partner already absorbed into the anchor isn't looked up as a stale endpoint. Pairs whose endpoints have both rewritten to the same survivor (the redundant closing edge of a connected cluster) are recorded as `skipped_redundant` entries in `state.data["plan_overlap_applied"]`. Every emitted collision is accounted for in that audit trail, though **not** always one-entry-per-collision: a multi-drop cluster (next row) collapses its N collisions into a single `multi_drop_*` entry whose `surviving_sids` names every partner the judge paired against the dropped sid. Anchors are computed over **all** non-`unresolvable` collisions — every resolution type, either side of the pair, single drops and multi-drop clusters alike, including the cluster collisions the pairwise loop then skips (DESIGN §5). Membership is bare appearance count and carries **no** semantic claim: a sid the judge dropped twice is an anchor too, it simply never survives to use the hint. Do not read it as "the subtask that absorbs its partners" — that is false on roughly a third of the resolution combinations (e.g. `merge(S,P)` + `drop_a(S,Q)` makes S an anchor while S survives only one of the two). The pairwise judge protocol stays simple; cluster decisions are enforced in code, not in the prompt (DESIGN §12). `_apply_overlap_drop` has a `dropped_sid == surviving_sid` self-loop guard as defense in depth against future callers reaching it with a self-collapsed pair. |
| **keep-and-delete consistency gate** (`_validate_overlap_judge_output` + `_contradictory_drop_sids`) — self-contradictory output die()s before any mutation | a `drop_*` whose `dropped_sid` *survives* another collision in the same output — kept as a merge endpoint, or as the non-dropped side of another `drop_*`. One claim deletes the subtask, another keeps it (X must both survive and vanish), and no apply order satisfies both. die() with the sid, the partner sid, the artifact, and the suggested resolution (refine task or downgrade to `unresolvable`). **The predicate is `_contradictory_drop_sids` (survives-somewhere ∧ dropped-somewhere), NOT `_compute_overlap_anchors`** — the two sets are deliberately distinct and conflating them is the defect this gate was rewritten to remove. `_compute_overlap_anchors` stays appearance-based because its one consumer, the merge `survivor_hint` rule (previous row), needs it that way. A sid dropped by 2+ collisions therefore *is* an anchor by appearance, but is **not** a contradiction — nothing claims it survives — and must not die() here; that is the multi-drop shape (next row), coherent output explicitly sanctioned by `prompts/plan_overlap_judge.md`. Gating on anchor membership instead killed runs whose judge output was correct, after full planner spend, unrecoverably (this phase precedes `_write_plan()`). (Earlier iterations also gated `merge`-between-two-anchors, but the apply loop's natural semantics — fall-through to lex-smaller with absorbed-intent carry-forward — handles every observed multi-anchor shape cleanly, so the check was removed as over-aggressive.) |
| **duplicate-pair rule** (`check_overlap_judge_output` `DUPLICATE_PAIR` + `_validate_overlap_judge_output` coalescing, keyed on `_collision_effect`) — a pair may repeat only when every row has the same *effect* | one pair colliding on **several artifacts**. The judge may encode this either way: a single row whose `artifact_paths` lists every overlapping file, or one row per artifact (DESIGN §5 *Multi-artifact pair*). When it emits one row per artifact, `_apply_overlap_collisions` already absorbs the repeat as `skipped_redundant`. Effect-identical rows (same resolved dropped sid, or same unordered merge pair) are coalesced into one collision keeping every `artifact` and `merge_feasibility`. Rows whose effects **differ** — `drop_a` on (A,B) plus `drop_a` on (B,A) deletes both subtasks; a `drop` mixed with a `merge` on one pair keeps and deletes the same sid — surface as a `DUPLICATE_PAIR` issue *inside* the retry loop, so the judge gets a round to fix it, and are terminal at the keep-and-delete gate above if it does not (on a two-sid pair any effect difference necessarily makes one sid both dropped and surviving, so that gate covers every conflicting shape; `tests/test_phase_overlap_judge.py` freezes the full 4×3 matrix). **`resolution` alone is the wrong signal** — swapped-endpoint `drop_a` rows share a resolution string and delete opposite subtasks. Gating on bare pair repetition (the pre-fix behavior) `die()`d coherent output at a validator that runs *after* `_run_checked_loop` and therefore cannot be retried — a real run was killed this way after significant planning spend. |
| **multi-drop cluster apply** (`_apply_multidrop` inside `_apply_overlap_collisions`) — one sid dropped by 2+ collisions is applied as a single whole-cluster operation, never by replaying the pairs | the judge finding one subtask's surface jointly covered by several siblings (DESIGN §5 *Multi-drop*). Replaying the pairs through the `survivor_of` transitive rewrite is **silent corruption**: pair 2's `_resolve` maps the already-dropped endpoint onto pair 1's survivor, so the loop drops *that* subtask instead — a live, wanted subtask the judge never named — fabricating a supersedure claim between two subtasks the judge never compared. `_apply_overlap_drop` discards title/intent/success_criteria_seed by design, so the loss is unrecoverable; damage scales with cluster size (a 3-collision cluster destroys 3 of 4 subtasks). Instead: union the dropped subtask's `provides` into **every** named survivor, drop each survivor's now-self-looping `extent: in_plan` requires, remove the dropped subtask once, and fan inbound `depends_on` references out to **all** survivors (deduped, self-refs removed) — the same fan-out rule `_remap_vanished_deps` uses, and semantically right because the dropped subtask's work is genuinely split across its survivors. Because the fan-out *adds* edges it can close a cycle no individual pair would, so it is guarded by `_would_cycle_after` with a three-tier ladder: `multi_drop_fanout` (acyclic, full fan-out) → `multi_drop_degraded_single` (fan-out would cycle; fall back to `sorted(survivors)[0]` alone via `_apply_overlap_drop`) → `skipped_would_cycle` (both would cycle; keep the subtask, leave the overlap to the integrator). Survivors are sorted so the outcome is independent of the order the judge emitted its pairs, satisfying `_schedule()`'s determinism contract. Each tier records its action in `state.data["plan_overlap_applied"]`. Tier 3 records `action: "skipped_would_cycle"` with `resolution: "multi_drop"` — the action alone is indistinguishable from a pairwise merge/drop skip, so the phase-summary counters key on the `resolution` field to attribute it to the multi-drop bucket. The counters must partition: every emitted `(action, resolution)` shape lands in exactly one bucket and the parts sum to `len(applied)`. Single-drop collisions and merges are unaffected and continue through the existing loop, including legitimate transitive chains (X dropped for A, then A genuinely dropped for B) which still apply both drops. |
| **`unresolvable` → `die()`** at plan time | genuine API contradictions the judge correctly refuses to silently auto-merge. The abort message names both sids, the colliding artifact, the judge's reason, and the suggested next step (revise the task and re-run: either disambiguate the disputed surface, or narrow the task so a single planner owns it). The message must not suggest `resume`: this phase precedes `_write_plan()`, so `<run-dir>/subtasks/` is still empty and `state.json` has no `waves` key — `_run_phases()` dies on any resume attempt. Strictly better than the multi-hour wave-N integrator design-conflict crash this phase exists to prevent. |
| **per-resolution cycle avoidance** (`_would_cycle_after` inside `_apply_overlap_collisions`) — checked before each `merge` / `drop_a` / `drop_b` apply | a collision resolution's dependency-union (survivor inherits the absorbed subtask's `provides`/`requires`/`depends_on` plus downstream `depends_on` rewrites) can introduce a transitive cycle absent from the post-reconcile graph (phase 2½'s acyclicity gate passed before these resolutions ran). Before applying each resolution, `_would_cycle_after(plans, apply_fn)` deep-copies `plans`, applies the resolution to the copy, rebuilds the predecessor graph via `_build_predecessor_graph`, and runs `_tarjan_sccs`. If the resolution *would* cycle, it is skipped (`skipped_would_cycle`; see next row) and both subtasks are kept separate for the integrator. The check is side-effect-free (operates on the copy) and runs against the *current live* `plans` so it sees every earlier-applied resolution. Covers `drop_*` too, because `_apply_overlap_drop` also unions `provides` and rewrites `depends_on`. |
| **post-merge acyclicity backstop** — Tarjan SCC on the final post-merge graph, immediately after `_apply_overlap_collisions` returns | with per-resolution avoidance above in place, this gate must never fire. It rebuilds the predecessor graph via `_build_predecessor_graph`, runs `_tarjan_sccs`, and on a surviving cycle `die()`s with `_format_cycle_diagnostic` output — but framed as an **orchestrator logic bug** (the tentative check and the real apply path disagreed), *not* a user-recoverable condition (per-resolution skipping already exhausted the `--skip-overlap-judge` lever). Retained as defense-in-depth against future drift between `_would_cycle_after` and the real apply, mirroring `_apply_overlap_merge`'s defensive missing-sid `die()`. |
| **`skipped_would_cycle` audit action** (`_apply_overlap_collisions`) | a `merge` / `drop_*` whose apply would close a dependency cycle. Recorded in `state.data["plan_overlap_applied"]` as `{"action": "skipped_would_cycle", …}` with both sids, the artifact, and `resolution`; logged at normal verbosity. Crucially, `survivor_of` is **not** updated on a skip — both endpoints stay live, so later collisions referencing either endpoint resolve against a present sid (mirrors how a merge would otherwise repoint them). The judge is not re-prompted; the cycle is a global-graph property outside its pairwise-surface competence (DESIGN §5 *Cross-domain surface overlap* → *Post-merge acyclicity*). |
| **state persistence** | full judge output written to `state.data["plan_overlap_judge"]` (for audit / replay); post-apply mutation summary written to `state.data["plan_overlap_applied"]`. Persisted before `phase_overlap_judge` returns; visible in `state.json` for resume-time replay debugging. |

The complementary `_warn_cross_planner_file_overlap()` check at phase 3
is **kept as-is** — it now serves as a complementary signal for file-
overlap that *doesn't* indicate surface collision (the deliberately-
permissive same-file-different-surface class).

### Plan validation — `_validate_plan` (after scheduling, before persisting the plan)
| Check | Catches |
|-------|---------|
| **budget feasibility** — `check_budget_feasibility()` runs at the same layer as `_validate_plan`, immediately after `_schedule()` returns and before `_write_plan()` persists. Estimates remaining `claude -p` calls (implementers + conformers + integrators per wave + finalize) added to `worker_count` already spent on upstream phases, multiplied by `budget_safety_margin`, compared to `max_total_workers`. | a planner output that is mathematically too large to fit the configured `--max-workers` cap. The pre-existing runtime backstop is `State.bump_workers()` which raises `WorkerError` partway through execution; this earlier check `die()`s with `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` value at the cheapest possible moment (no implementer has spawned yet, only the integrated commits from upstream judgment phases are sunk). Opt-out via `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml`. See §"Budget feasibility preflight" above and DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*. |
| ids match domain prefix (`bugfix-`, `feat-`, `refactor-`, `perf-`, `test-`, `deps-`, `config-`, `docs-`) | cross-domain collisions, audit ambiguity. The planner's user prompt receives the prefix directly as `ID_PREFIX = CATEGORY_ABBREV[domain] + "-"`, so the prompt cannot drift from the validator's allowlist — both derive from the same `CATEGORY_ABBREV` map (in `leerie.py`). |
| no `size: large` subtasks | planner OR reconciler violated the sizing constraint. The error message names the actual author via the `_added_by_reconciler` flag — "planner must split it further" for planner-authored, "reconciler must split it further (size-retry exhausted)" for reconciler-added subtasks that survived the size-resolution retry loop. The reconciler path is exercised through the phase 2½ size gate first; this row is the post-merge backstop for the planner case and the exhaustion case. |
| no empty `success_criteria_seed` | implementer has no criteria starting point |
| every `depends_on` id exists | dangling edges silently dropped by the scheduler |
| every `requires` entry is an object `{tag, extent, reason?}`; `extent ∈ {in_plan, external}`; `reason` non-empty when `extent: external` | malformed planner output (caught at JSON-schema validation in `claude_p`; this row is the post-merge defensive re-check) |
| every `requires` entry with `extent: in_plan` has a provider in some subtask's `provides` | unresolvable cross-domain dependency (only `in_plan` is checked; `external` entries are explicitly out-of-graph by planner declaration) |
| no `files_likely_touched` entry matches `_is_protected_path()` (`.leerie/`, `.git/`, or top-level `.claude/` outside the deliverable subtrees) | planner named a protected meta-directory as an implementer deliverable — the implementer would either fail `check_diff_scope` mid-run or work around the gitignore and still be rejected. Catching this at plan-validation time gives the planner a corrective-retry round instead of burning an implementer invocation. For coordination artifacts (research specs, design summaries) the planner should use `provides`/`depends_on` and the implementer's `artifacts` result field — see DESIGN §5 *Artifact passing between subtasks* — not `files_likely_touched`. |

`_warn_cross_planner_file_overlap()` runs immediately after
`phase_reconcile` (before `_validate_plan` and the scheduler) and **logs a
warning, never fails**, when two planners' subtasks both list the same
path in `files_likely_touched`. The reconciler now consumes the same shared-files signal as one input to
the recommendation heuristic (above) when a cycle requires resolution
— SCC members that share `files_likely_touched` get a `merged_subtasks`
recommendation. The warning itself remains as runtime visibility for the
user; it complements the recommendation heuristic rather than replacing
it.

`_warn_layer_gaps(plans)` runs at the same layer and surfaces two
heuristic warnings (DESIGN §5 *Migration-surface completeness*):
(1) any subtask's `files_likely_touched` includes a `schema.prisma`
path but no subtask across the full plan touches seed or migration
files — database-initialization gap; (2) any subtask's `provides`
tags contain env/bootstrap/secret/credential keywords but no subtask
touches `.env.example` or env documentation — env-contract gap.

`_filter_offtree_subtasks()` runs at the same layer (after
`_warn_cross_planner_file_overlap`, before `_schedule()`) and **soft-drops
any subtask whose `files_likely_touched` contains a path that does not
resolve under the run's primary repo root** — the common case is a leak
into an inspect-dir mount (`/inspect/<repo>/...`), where the planner
named a file the implementer cannot modify because the mount is
read-only. Drops are recorded in `state.data["dropped_subtasks"]` and
logged per-subtask. The drop must run before `_schedule()` because
`phase_execute` iterates `state.data["waves"]` (not the in-memory
`subtasks` dict), and `waves` is computed by `_schedule()` — a drop
after that point leaves `waves` referencing a sid with no spec on disk.
A soft drop is the right shape because a `die()` here is unrecoverable:
the resume branch in `_run_phases` does not re-run the planner pipeline
and requires `state.data["waves"]` (only written by `_write_plan` after
this point). When a dropped subtask provides a tag a survivor requires,
`_validate_plan`'s existing unresolvable-requires check (above) catches
it and dies with `<sid>: requires '<tag>' but nothing provides it —
dependency is unresolvable and will be silently dropped` — the user
sees both messages and re-frames the task.

### Per-subtask checks — in `_settle_subtask`, every worker result
| Check | Catches | On failure |
|-------|---------|-----------|
| `_validate_result()` — `incomplete-handoff` with missing checkpoint file | session-limit no-op; `--max-turns` with no checkpoint written; **worker reaped mid-turn** (e.g. it backgrounded an expensive final step like a build that OOM-died, so `claude -p` was killed before writing a checkpoint) | **Rescued when the worktree holds commits, else Retryable** (`failure_kind="empty_handoff"`). Before failing, `_settle_subtask` calls `_branch_has_commits_ahead` (a positive-polarity bool — True only when the worktree exists, git succeeds, and there are commits; distinct from the `check_branch_has_commits` no-op gate, whose indeterminate states return `None`); **if the branch has commits ahead of the run branch the worker produced a real deliverable** — it is settled as `complete` (with the advisory conformance phase recording whatever verification step didn't finish) instead of being discarded. `fail()` would `_reset_subtask_worktree` and destroy the committed diff, then burn `failed_retries` until the run dies; the positive commit-proof keeps green work while a gone worktree / git failure is **not** rescued (never mistaken for a real deliverable). Only when there are **no** commits (a genuine no-op) does it stay retryable. The confidence gate and dirty-worktree fail are skipped for a rescued result (a reaped worker returned no confidence envelope and may have left uncommitted debris, which is discarded). See DESIGN §9. |
| `_validate_result()` — other cross-field invariants | `handoff` with null `checkpoint_path`; `blocked` with no blocker; `failed` with no summary; `needs-clarification` with no `clarification_question` / invalid `checkpoint_path` | **Terminal** (`failure_kind="broken"`) |
| `check_branch_has_commits()` | `complete` claim, nothing committed *and* no `artifacts` returned. A non-empty `artifacts` array on the result is a substitute deliverable (DESIGN §5 *Artifact passing between subtasks*) — research-style subtasks whose only output is structured data for downstream subtasks pass this gate without commits. | **Rescued when the criteria are already met on the run-branch HEAD, else Retryable.** Before failing a no-commits `complete`, `_settle_subtask` re-runs the `satisfied_probe` (same prompt/schema as `_filter_satisfied_subtasks`) against the subtask's `success_criteria_seed`, this time on the **run-branch HEAD** (`_compute_run_branch(st.run_id)`), not the base tree — because a sibling subtask in an earlier wave may have committed this subtask's entire deliverable during the run (DESIGN §8 *The mid-run sibling case*). (this also covers a subtask already satisfied on the base tree — the probe judges *whether* the criteria are met, not *who* met them; DESIGN §8 *Scope*). If the probe returns `satisfied`, the subtask is settled `complete` and recorded in `state.data["dropped_subtasks"]` with `reason: "already_satisfied_mid_run"` (evidence + `checked` list, same shape as the pre-schedule drop); `_settle_subtask` also writes a `state.data["conformance"][sid]` sentinel (`{result: None, warnings: [...]}`) so `_get_progress` classifies the rescued subtask as `done` rather than perpetually `in_conformer` (the real conformer is correctly skipped — a zero-commit subtask has no diff to conform). Only a subtask with a non-empty `success_criteria_seed` is probed; without a criterion there is nothing to judge, so it stays retryable. If the probe is not satisfied (a genuine lazy/broken no-op) the existing `"no_commits"` retryable path is unchanged. The probe is subordinate to the mechanical gate per DESIGN §12 and defaults to *not satisfied* on any error/uncertainty, so it can only *rescue* a real no-op, never mask one. |
| dirty worktree check | uncommitted changes that vanish on integration | **Retryable** |
| `check_diff_scope()` | `.leerie/` or `.git/` in the diff; any `.claude/` path *except* `.claude/agents/`, `.claude/commands/`, `.claude/skills/` (the documented Claude Code user-deliverable subtrees — implementers may write a subagent/command/skill file there as a legitimate deliverable, but never `settings.json` or any top-level `.claude/` file) | **Terminal** (protected path); scope-volume warning is non-fatal (triggered when `files_likely_touched` is non-empty *and* touched > max(3× expected, 5), or when touched > 15 regardless of the planner's estimate) |
| `_validate_checkpoint()` — on `incomplete-handoff` | required section missing; required section empty/whitespace; required section contains only a placeholder token (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`, trailing `.`/`!`/`?`/`…` ignored and repeated `?` collapsed); a path listed under `## Files touched` no longer exists in the worktree and is not flagged `[deleted]` | returns `blocked` |
| `_retryable_failure(kind)` — on `status='failed'` returned by the worker itself | worker self-report of failure | routed through the retry policy with `failure_kind="broken"` (worker self-report has no producer to tag a more specific kind, and a self-reported failure is broken-worker territory by default); **terminal** on first occurrence |

`_validate_result()` accepts a `complete` status regardless of what
`criteria_results` carries — empty, missing, or with `met:false`
entries are all valid. Per DESIGN §8 the criteria file is
informational, not a gate. A worker's unmet-criterion self-report is
recorded on the result for telemetry and surfaces as a warning in
`state.json["conformance"]` alongside the conformance-phase residuals,
but does not affect the subtask's terminal status. The criteria-file
lock (`lock_criteria` / `verify_criteria_lock`) and the
worker-initiated `criteria_revision_proposal` channel were both removed
when the criteria file's load-bearing role retired — see DESIGN §9.

### Per-subtask post-work conformance — in `_settle_subtask`, success path only

Triggered only when an implementer's `status: "complete"` has already cleared
every check above (commits present, worktree clean, no protected path
written). None of the other terminal statuses (`incomplete-handoff`,
`needs-clarification`, `blocked`, `failed`) invoke the conformer.
Implements DESIGN §9 *Post-work conformance*.

| Step | Function | Behavior |
|------|----------|----------|
| Discover rules files | `_discover_rules_files(repo_root)` | Returns existing paths from a fixed, capped allowlist (`CLAUDE.md`, `AGENTS.md`, `.agent.md`, `.cursorrules`, `.windsurfrules`, `docs/CLAUDE.md`, `docs/AGENTS.md`, `docs/CONVENTIONS.md`, `docs/STYLE.md`, `docs/DESIGN-SYSTEM.md`, `docs/DESIGN_SYSTEM.md`, `docs/UI.md`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`), deterministic order, never raises. Empty list when nothing matches. The design-system candidates (`docs/DESIGN-SYSTEM.md` and spelling variants) exist so a repo's component/color/banner conventions reach both the conformer and the implementer (DESIGN §9). |
| Run conformer | `_run_conformer()` | One `claude -p` invocation with `ACT_TOOLS`, `--dangerously-skip-permissions`, `SCHEMAS["conformer"]`. Accepts optional `extra_feedback: str \| None` — when non-None, appended to the user prompt (used for Pattern B backgrounding-retry feedback from prior round). Catches `WorkerError` and returns `None` (surfaced as a warning). The raw structured output is passed through `_expand_conformer_output()` (N29) before returning, restoring the flattened wire shape into the four arrays every downstream step below expects. |
| Validate output | `_validate_conformance_result()` | Cross-field invariants — `rule_violations_residual` non-empty requires `rules_files_read` non-empty; each `rule_violations_fixed` item must cite a non-empty `rule` string; each `docs_updates` / `tests_updates` item must cite a `path` that exists. On failure → warning, loop breaks. |
| Re-run gates | `check_branch_has_commits`, dirty-worktree check, `check_diff_scope` | Same functions used on the implementer, re-applied to any new commits the conformer added. A scope-protected-path violation triggers `_rollback_conformer_commits()` (reset to `before_sha`) and is recorded as a warning, **not** as `failed` / `blocked`. |
| Clobber-survival check | `_clobbered_owned_files(worktree, run_branch, impl_head_sha)` + `_blob_sha` | DESIGN §9 *No clobbering the implementer's work*. `impl_head_sha` is snapshotted **once before the round loop** (a per-round HEAD would fold in prior conformer commits and miss a round-0 clobber). Owned set = `git diff --name-only <run_branch>..<impl_head_sha>`; for each owned file, a clobber is a deletion at HEAD or a blob reverted to the base version (three-way blob compare via `_blob_sha`, which uses `git rev-parse --verify -q` to avoid the bare-`rev-parse` missing-path footgun) while a legit conformer edit leaves a distinct third blob and is not flagged. Warns **always**; under `--strict-conformer` also `_rollback_conformer_commits()` to the implementer HEAD **and blocks** — a `clobbered_files` flag threaded to the post-loop `blocked_reason` (per-subtask) / `final_blocked` (final) sets a block even when `_conformance_clean(last_res)` is True, so a clobber is never silently completed. Not auto-rolled-back in advisory mode — a legitimate revert-to-base is git-indistinguishable from a clobber. The final-tree pass applies the same guard with `base=` **a snapshot SHA** — `_merge_base_sha(staging, working_branch, staging_before_sha)`, the point the run branch forked from the working branch — and `impl_head=staging HEAD snapshotted before that pass`. **It must not be `run_branch`.** The staging worktree has the run branch checked out (`setup-run.sh`: `git worktree add "${STAGING_WT}" "${BRANCH}"`), so passing that branch name makes `_blob_sha(base_ref, f)` and `_blob_sha("HEAD", f)` resolve the same ref, `b_head == b_base` is unconditionally true, and every file the final conformer edits is reported `(reverted-to-base)`, driving `_rollback_conformer_commits` under `--strict-conformer` and reverting every legitimate final-conformer fix. The per-subtask call site is correct with `base=run_branch`: a subtask worktree sits on `leerie/subtasks/<run-id>/<sid>`, a genuinely different ref from the run branch, so the two blob lookups do not collapse. |
| Loop bound | `caps["conformance_rounds"]` (default 3) | Re-runs the conformer if its output is malformed or residuals remain. Exhausting the cap with residuals still present is a warning, not a failure. |
| Loop-continuation predicate | `_conformance_clean(conf_res, baseline)` + `_baseline_red_axes(baseline)` | DESIGN §9 *The signal that continues the loop is a delta, not a verdict*. Returns True (ends the loop) when nothing is left that this subtask is responsible for. `baseline` is `st.data["conformance"]["_baseline"]` or `None`, read once per phase by the caller so the predicate stays a pure function of its arguments. **Checked ahead of the red-axis exclusion (1) below: an axis with `ran && !measured` returns False.** A command that produced no verdict at all — the runner is absent, or the process died to resource exhaustion (`_axis_unmeasurable` = `_runner_missing` OR `_is_fork_exhaustion`) — is a third state, not a pass; an unmeasurable axis is not "red at baseline and therefore not ours", it is unknown. Then two exclusions, both keyed on the `_baseline_red_axes()` set (which admits only names in `_BLT_AXES`, so a junk entry cannot widen it): (1) an axis with `ran && !passed` whose name is red at baseline does not block; (2) a `rule_violations_residual` entry whose **`axis` field** is red at baseline does not block. Everything else blocks exactly as before — a residual under any other rule, an *unlabelled* residual, and a failure on an axis that was *green* at baseline (a real regression). The axis is read from the schema field, never inferred from the `rule`/`why_not_fixed` prose: that would be regex on an LLM's response (*Language-to-JSON*), and `why_not_fixed` is not stable enough to compare anyway. `axis` is optional on the schema and gating on absence (one decision, not two — see the schema comment and `check_production_evidence`'s precedent), normalised in `_expand_conformer_output` so this stays a plain set test. `baseline=None` (skipped via `--skip-base-baseline`, not yet captured, or malformed) reproduces the pre-change absolute-verdict behaviour byte-for-byte. |
| Axis selection | `resolve_blt_scoped(repo_root)` + `_changed_files(worktree, run_branch)` + `_select_subtask_axes(blt, scoped, files, base_ref, mode, test_globs)` | DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*. Resolved ONCE per subtask, before the round loop — the changed-file set is the implementer's diff, and a conformer's own commits do not widen what the subtask is responsible for. `mode` is `st.data["subtask_tests"]`. `resolve_blt_scoped` reads `test_scoped`/`build_scoped` from `.leerie/config.toml` (added to `_load_blt_config`'s key tuple), else infers exactly two: `npx vitest related --run {files} --passWithNoTests` when a `vitest.config.*`/`vitest.workspace.*` exists, `npx jest --findRelatedTests {files} --passWithNoTests` for `jest.config.*`, and `npx tsc --noEmit` as `build_scoped` when a `tsconfig.json` exists and the canonical build is not already `tsc`-shaped. Kept in a SEPARATE function from `_infer_build_lint_test` so the launcher's mirrored bash inference and its parity guard (`tests/test_config_verb.py`) stay untouched. No pytest inference (a `{files}` render of a changed `orchestrator/leerie.py` collects nothing) and no lint tier (lint measured at 0.4 h across a 51 h run). `_changed_files` uses `git diff -z --name-only <base>..HEAD`: `-z` because git C-quotes paths containing spaces or non-ASCII, so `splitlines()` returns a literal that does not exist on disk. `_render_scoped` `shlex.quote`s each path and returns None when a `{files}` template has no files — rendering the bare runner would run EVERYTHING. A template may instead ask for `{test_files}`, which substitutes only the members of the changed set satisfying `_is_test_file` and applies the SAME absence rule — a diff carrying no test file renders nothing and falls back to canonical. That tier exists for runners with no source→test impact analysis: pytest takes paths and collects under them, so a non-test path is an ERROR, not a no-op (`pytest docs/DESIGN.md` exits 4, and one such path poisons an otherwise-valid invocation — `pytest docs/DESIGN.md tests/test_blt_semaphore.py` also exits 4). `_is_test_file(path, globs)` matches a `tests/`|`test/`|`spec/` path segment or a `test_*.py`|`*_test.*`|`*.test.*`|`*.spec.*` basename; `test_file_globs` in `.leerie/config.toml` (space-separated `fnmatch` patterns, added to `_load_blt_config`'s key tuple) REPLACES the built-ins when set. `resolve_test_file_globs(repo_root)` reads that key and is what `_run_conformance_phase` passes as `test_globs`. `_render_scoped` also hard-skips a template naming a placeholder this version cannot substitute (`_UNKNOWN_PLACEHOLDER_RE`, once-per-process warning via `_warn_unknown_placeholder_once`), falling back to canonical rather than shipping a literal brace to the shell — `pytest '{test_files}'` exits 4, so an unguarded skew between a newer committed `.leerie/config.toml` and an older installed orchestrator (`/opt/leerie-image`, updated only by install.sh) turns EVERY subtask RED. The scan runs against the TEMPLATE with known placeholders stripped, never the rendered command: a changed-file path may legitimately contain braces, and scanning after substitution both disables the proxy and misdiagnoses the cause as install skew. Any axis whose proxy does not resolve falls back to the canonical command; the returned scope label is `scoped` only if at least one axis actually used a proxy |
| Measure (pre / post) | `_measure_axes(worktree, axes, st, caps, ...)` | Run immediately before the conformer round (its results become the prompt's `BLT_RESULTS:` block) and again immediately after (its results overwrite the worker's self-report). Memoised via `blt_results`, so the post measurement is free whenever the round committed nothing — measured, 182 of 224 rounds. `--subtask-tests off` yields `{}` and skips both. Each axis command is bounded by `caps["worker_timeout_sec"]` (5400 s default) — there is no tighter per-axis ceiling, so a hung command blocks the subtask for that long; previously the conformer's own worker timeout bounded it |
| Worktree deps | `_ensure_worktree_deps(tree, st, caps, ...)`, called from inside `_measure_axes` | DESIGN §6½ *Who runs that install*. Applies the provision recipe's `install`/`build` entries on the FIRST axis actually measured for a worktree — not at worktree creation. Lazy on purpose: `_run_implementer:25018-25021` declines to pre-install because a config-only / docs-only subtask correctly skips it (44 of 91 subtasks in the motivating run touched zero source files), and an eager install would hand that cost back. A memo hit and an absent command both skip it, since neither needs deps. Memoised on the resolved absolute path in the module-level `_DEPS_INSTALLED` — a per-process filesystem fact, not run state. Non-fatal: a failed install surfaces as whatever the subsequent BLT command reports, which `_runner_missing` already classifies. Removes the repeat, not the install: 263 installs across 161 worker logs (~2.8 per worktree, since a subtask's implementer and conformer share one) become one |
| Apply (twice per round) | `_apply_measured_axes(conf_res, pre)` then `_apply_measured_axes(conf_res, post)` | Replaces `conf_res["build"\|"lint"\|"tests"]` with the orchestrator's measurement, returning a NEW dict so the raw worker payload stays as-emitted for telemetry. **Both applications are load-bearing and neither is redundant.** The `post` apply at the loop tail is the ordinary case. The `pre` apply runs as soon as there is a dict to apply it to — before `_validate_conformance_result` — because three gates `break` out of the round before the tail is ever reached: a malformed result, a protected-path violation, and a strict-mode clobber. Without it those paths carry the conformer's *claimed* axes into `_summarize_residuals`, into the persisted `conformance` entry, and into the post-loop `_conformance_clean` that decides whether `--strict-conformer` blocks the subtask — i.e. strict mode gating on a self-report, the exact thing this phase stopped doing. `pre` is also the *accurate* measurement on those paths: the protected-path and clobber exits both roll the worktree back toward its pre-round state. Safe before validation because `_validate_conformance_result` inspects `rules_files_read`, `rule_violations_*` and the update paths, never the axes. Pinned by `test_the_overwrite_is_applied_twice_per_round` and by per-path behavioural tests in `tests/test_run_conformance_phase.py`; the guard this replaced compared source *indexes*, which a `break` jumps over, and demonstrably still passed with the fix reverted |
| Round delta | `_round_axis_regressions(pre, post)` | An axis measured green before the round and red after it — a regression the conformer just introduced, attributable with no output parsing. Appended to `warnings`, fed into the next round via `_format_check_feedback`, and ANDed into the loop-exit condition (`_conformance_clean(...) and not regressions`) so a self-inflicted break earns another round. Refuses to fire when either side is unmeasured (no evidence is not evidence of green), when the command strings differ (which is what stops a scoped `pre` being compared against a canonical `post`), and on red→red (inherited debt) |
| BLT-axis observability + feedback | `_emit_bash_axis_warnings()` | After each round, parses the per-worker JSONL log at `<state-root>/runs/<id>/logs/<sid>-conformer.log` (or `final-conformer-r<N>.log` for the final pass) and surfaces two types, both feedback-injected: (1) **multi-invocation** (Pattern A) — `conformer round N: ran <AXIS>_CMD K times in one round` — legitimate progressive testing (targeted → full suite → grep) is a common cause, but a worker that keeps re-running the same axis on a provably unchanged tree wastes an expensive install/lint/build/test cycle, so this class is fed back too. (2) **retry-after-backgrounded** (Pattern B) — `conformer round N: <AXIS>_CMD auto-backgrounded (bash_id=<id>) and was followed by another <AXIS>_CMD invocation` — the "retry-instead-of-recover" pattern. Both classes are collected after each round (matched via `"auto-backgrounded" in w or "times in one round" in w`) and, if non-empty, formatted via `_format_check_feedback()` and passed as `extra_feedback` to the next round's `_run_conformer()` call (or inlined into the next round's prompt for the final-conformer call site) so the conformer can correct the behavior. Helpers `_count_bash_axis_invocations()` and `_count_orphaned_bg_axis()` are pure log-parsing — never raise. `_BLT_AXIS_RES` is a `dict[str, re.Pattern[str]]` containing compiled regexes for the test, build, and lint axes: test matches `pnpm/npm/yarn/bun/npx test` (and `vitest`), `vitest run`, `bin/rails test`; build matches `pnpm/npm/yarn/bun build`, `tsc`, `next build`; lint matches `pnpm/npm/yarn/bun lint`, `biome check`, `eslint`, `rubocop`. The `_count_orphaned_bg_axis` detection logic also accepts `BashOutput shell_id=<id>` polls as a valid recovery path — forward-compatible with future tool-surface changes. |
| Attach result | — | `res["conformance"]` (worker output blob) and `res["conformance_warnings"]` (list of strings) are added to the implementer's result. The subtask still returns `complete`. |

The phase is advisory: **no path through the conformance phase produces a
`failed` or `blocked` subtask status.** Build/lint/test failures, malformed
conformer output, conformer crashes, gate violations on conformer commits,
and exhausted rounds all surface as entries in `conformance_warnings` and as
non-fatal log lines. This is the §12 enforcement boundary for the phase:
*discovery* of rule files, *schema validity* of the conformer's output, and
the *protected-path invariance* across conformer commits are code-enforced;
whether the conformer made the right docs/tests/rule-violation calls is left
to the worker and not second-guessed.

### Wave-level checks (after integration)
| Check | Catches |
|-------|---------|
| `_scan_conflict_markers()` | unresolved `<<<<<<<` markers in the run-branch worktree after integration — deterministic safety net |

There is no LLM wave-level re-validation. An earlier version of
`validate_wave` ran a deterministic test-runner fast-path and an LLM
validator over per-subtask criteria, with a re-spawn loop bounded by
`wave_revalidation_rounds`; all of that was removed when the criteria
file's load-bearing role retired (DESIGN §8, §9). Per-subtask quality
is the implementer's confidence gate; the wave-level safety net is the
deterministic conflict-marker scan.

### Post-integrator checks (after an integrator handles a conflict)
These verify the integrator honored DESIGN §6's *behavioral* conflict-
resolution contract — the integrator prompt itself
(`prompts/integrator.md`) carries the behavioral spec (read every
involved subtask's intent, preserve each side's intent, call
irreconcilable cases a `design-conflict`); the orchestrator only checks
the outcome.

| Check | Catches |
|-------|---------|
| `check_merge_committed()` | integrator returned `resolved` but left the worktree mid-merge (`MERGE_HEAD` present) or with staged-uncommitted changes — **terminal**: merge aborted, run stops |
| `check_integrator_commit()` | integrator merge commit touched `.leerie/` files — non-fatal warning, recorded to `state.json` |
| integrator status `design-conflict` / `failed` | unresolvable conflict — **terminal**: in-progress merge aborted, the run branch left clean at the last good wave, diagnosis saved, run stops |
| integrator **crash** (`_run_checked_loop` returns `None`) | infrastructure failure, not a verdict — `_rescue_integrator_work()` captures the in-progress resolution to `refs/leerie/rescue/<run-id>/<sid>` **before** the merge is aborted, `blocked[sid]` is recorded, and the die message names the ref plus its `cherry-pick --no-commit` recovery command. `resume` retries the integration |

`_rescue_integrator_work(staging, sid, run_id) -> str | None` returns the rescue
ref, or `None` when there was nothing to save (captured tree identical to
`HEAD^{tree}`) or the capture failed. It is **not** gated on
`check_merge_committed`: a crashed integrator typically dies mid-resolution with
no merge commit, which is exactly the case worth rescuing (DESIGN §12). It stages
into a throwaway `GIT_INDEX_FILE` seeded from HEAD (`read-tree` → `add -A` →
`write-tree` → `commit-tree`) because both `git stash push` and `git stash
create` refuse a conflicted tree ("Cannot save the current index state") — the
unmerged index is the exact state an integrator crash leaves behind. The real
index and working tree are never touched, and untracked files are captured. Every
git failure degrades to `None`: a rescue failure must never mask the crash.
`run_proc` gained an `env: dict[str, str] | None = None` parameter for this (the
default inherits the orchestrator's environment, so existing call sites are
unchanged).

### Resume integrity — `_validate_resume_state()`
Enforces (one half of) DESIGN §6's "the run branch is the resume contract"
invariant — state.json's `waves`/`completed_waves` say *which* wave to
resume; the never-reset `leerie/runs/<run-id>` branch holds *the work*
every prior wave produced. Both must be coherent for resume to be safe.

On `resume`: asserts `task` is present and non-empty; asserts `waves`,
`completed_waves`, `subtask_status` are well-formed *if present*. `waves` is
intentionally optional — a run interrupted before scheduling has none. In
that case `main()` no longer treats the absence of `waves` as unresumable:
per DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a
`waves` gate," `_run_phases` walks the planning-phase sequence (classify →
plan → reconcile → overlap-judge → adherence-gate → off-tree/satisfied
filters → schedule) and re-enters at the first phase whose `plans_after_*`
checkpoint key is absent, reusing the persisted `plans` from the last
completed phase as that phase's input rather than re-deriving it from
scratch. Rejects corrupt or hand-edited state without rejecting a
legitimately-early interruption.

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
worker is spawned via `asyncio.create_subprocess_exec` (wrapped by the
`run_proc` helper) and awaited; both spawn sites pass
`start_new_session=True` so each worker becomes its own POSIX session and
process-group leader (PGID == PID), isolating it from the orchestrator's
own group. Parallel workers within a wave run concurrently via
`_gather_or_cancel` — a small `asyncio.gather` wrapper that, on the first
exception, cancels every other in-flight task and awaits its finalization
before re-raising — under an `asyncio.Semaphore` bounded by
`max_parallel`. Because every mutator runs on the single loop, `State`
carries no lock — coroutines only interleave at `await` points, which
never fall inside a `st.data[k] = v; st.save()` pair. `State.save()`
still writes to a temp file then `os.replace()` for atomicity against
process crash.

Subprocess cleanup is three-layered, addressing two distinct leak classes plus mid-run pressure reduction:

1. **Lifetime descendant tracking (`_DescendantTracker`).** A per-worker
   asyncio task started at spawn polls `_enumerate_descendants(proc.pid)`
   every ~0.5s and accumulates every PID ever observed as a descendant
   of the worker. On every exit path — success AND failure — the
   tracker's `stop_and_reap()` SIGKILLs the accumulated set. This is
   the load-bearing fix for Claude Code's Bash tool with
   `run_in_background: true`: the tool wrapper spawns its user command
   in a detached POSIX session, then the wrapper itself can exit while
   the user command keeps running. By the time `claude -p` exits, the
   backgrounded command has been reparented to PID 1 and is no longer
   reachable via post-hoc PPID walk from the worker — but the tracker
   observed it mid-flight and has its PID. Without lifetime tracking,
   the descendant is invisible to cleanup.

2. **Abnormal-exit subtree termination (`_terminate_proc_tree`).** On
   `KeyboardInterrupt`, `SIGTERM`, `RateLimitedExit`, or any other
   `BaseException`, `run_proc`'s and `_invoke`'s catch-all handlers
   call `_terminate_proc_tree(proc)`. The helper sends SIGTERM to the
   worker's process group (`os.killpg`) AND to every descendant
   currently reachable via PPID walk (`_enumerate_descendants`), waits
   `_PROC_TREE_GRACE_SEC = 2.0` for graceful shutdown, then SIGKILLs
   the survivors via the same two mechanisms. The PPID walk is needed
   because Claude Code's Bash tool subprocesses are in a *different*
   POSIX session than `claude -p` — `killpg(claude_p_pgid)` does not
   reach them, so the walk is the only way to enumerate them while
   the parent chain is still intact. Exception paths run the tracker
   reap *after* `_terminate_proc_tree`, catching any backgrounded
   subprocess that was orphaned during the run.

Layers 1 and 2 compose: `_terminate_proc_tree` is broad and
synchronous (one call, kills attached subtree), the tracker is narrow
and historical (kills only what it observed, including processes
that have since reparented away). Neither alone is sufficient; both
together close the leak.

3. **Mid-run PID reaping (`_poll_loop` + `_reparented_orphans`).** A
   pressure-gated reducer that sits under the PID-exhaustion-detection
   backstop (see below) and proactively reaps orphaned subprocesses before
   `pids.max` is reached. `_DescendantTracker` gains a `cgroup_sid: str | None = None`
   parameter (default `None` so existing direct constructors in the test
   suite keep working and the reaper is inert without a cgroup). `_invoke`
   threads the in-scope `cgroup_sid` into the constructor call. Each
   `_poll_loop` cycle, when `cgroup_sid` is set, the tracker calls
   `_cgroup_stat(cgroup_sid)` and computes the pressure ratio
   `pids.current / pids.max`. Reaping is armed only when that ratio reaches
   or exceeds `_PID_REAP_HIGH_WATER = 0.90`; when armed, it calls
   `_reparented_orphans(self._seen, min_age)` to obtain the killable set and
   sends `SIGKILL` oldest-first (via the existing `_signal_pids`), stopping as
   soon as the ratio drops below `_PID_REAP_LOW_WATER = 0.75`. Killed PIDs
   are pruned from `_seen`; the exit-time `stop_and_reap` path is unchanged.
   `_reparented_orphans(seen: set[int], min_age: int | None = None)
   -> list[int]` runs one `ps -eo pid,ppid,etimes` snapshot (with
   `check=True`, so a failing `ps` raises into the `except` and returns `[]`
   through the documented path rather than silently parsing unusable output)
   and returns, sorted oldest-first, the PIDs from `seen` that are
   simultaneously alive, reparented to init (`ppid == 1`, or to the
   orchestrator itself once `_become_subreaper` has run), and at least
   `min_age` seconds old. `min_age` is a **sentinel default**: `None`
   resolves to `_PID_REAP_MIN_AGE_SEC` *at call time*, so a caller omitting
   it gets the normal-tier floor and monkeypatching the constant moves the
   floor. A literal `min_age: int = _PID_REAP_MIN_AGE_SEC` default would bind
   once at def time, silently pinning 60 regardless of the constant — a trap
   for any future test that patches it and calls this bare.

   **Two-tier age floor (DESIGN §6 *Why a single 60 s floor is not enough*).**
   `_poll_loop` selects the floor from the same pressure ratio it already
   computed: at or above `_PID_REAP_CRITICAL_WATER = 0.90` it passes
   `_PID_REAP_CRITICAL_AGE_SEC = 5`; below it, `_PID_REAP_MIN_AGE_SEC = 60`.
   Without the critical tier the reaper arms at 90% and finds an empty
   candidate list — every orphan in a fresh burst is younger than 60 s —
   which is a disabled reducer, not a safe one. DESIGN §6 carries the
   measurement the tier rests on and is the source of truth for it; do not
   restate the numbers here. `_PID_REAP_CRITICAL_WATER` is deliberately equal
   to `_PID_REAP_HIGH_WATER` (both 0.90) and kept as a separate named
   constant: the arming threshold and the floor-escalation threshold answer
   different questions and may diverge.
   Module-level constants (placed next to `_DESCENDANT_POLL_SEC` /
   `_PID_EXHAUSTION_WINDOW`): `_PID_REAP_HIGH_WATER = 0.90`,
   `_PID_REAP_LOW_WATER = 0.75`, `_PID_REAP_MIN_AGE_SEC = 60`,
   `_PID_REAP_CRITICAL_WATER = 0.90`, `_PID_REAP_CRITICAL_AGE_SEC = 5`.

4. **Zombie reaping (`_become_subreaper` + `_zombie_reaper`).** The reaper
   above handles *live* leaked processes; **zombies** (`<defunct>` tasks not
   yet `wait()`ed) also count against the cgroup `pids.max`, and the container
   PID 1 (`runuser` locally / idle `sleep infinity` on Fly) is not a reaping
   init, so orphaned `git`/`ssh-agent` descendants reparent to it and rot
   (DESIGN §6 *Zombie reaping*). `_become_subreaper()` — called once early in
   `main()` before any worker spawns — issues
   `ctypes.CDLL(None).prctl(_PR_SET_CHILD_SUBREAPER=36, 1, 0, 0, 0)` so
   orphaned descendants reparent to the orchestrator; Linux-guarded
   (`sys.platform`), a logged no-op elsewhere; returns `bool`. `_zombie_reaper()`
   is a background asyncio task spawned in `_orchestrate()` next to `sampler_task`
   and cancelled in the same `finally` — mirroring `_memory_sampler`'s lifecycle.
   It is an **allowlist, never a `/proc` scan**: the reaper
   `os.waitpid(pid, WNOHANG)`s only PIDs in `_REAPABLE_PIDS` (~1 s).

   **`ChildProcessError` (ECHILD) does NOT discard the PID (N36).** For a
   live *grandchild* — the dominant case, since `_mark_reapable` is fed
   `_enumerate_descendants(leader_pid)`, i.e. descendants of a *worker* —
   ECHILD means "not ours **yet**", not "gone": while the worker lives the
   orchestrator is not the pid's parent. Discarding there drops the pid
   permanently, so when the worker exits and the orphan reparents (via
   `_become_subreaper`) it is no longer on the allowlist and nothing ever
   waits it.

   The arm instead disambiguates with `_pid_still_exists(pid)` (a one-pid
   `/proc` existence check, deliberately its own function so
   `_zombie_reaper`'s source never literally mentions `/proc` — see
   `test_zombie_reaper_never_scans_proc_for_zombies`): still alive → retain
   and retry next tick; confirmed gone → discard. Retention is bounded by
   `_ECHILD_RETRY_MAX_SEC` (60 s, first-ECHILD timestamps in
   `_REAPABLE_PID_FIRST_ECHILD`) so a pid that never reparents cannot grow
   the set without limit. That bound is safe only because
   `_DescendantTracker._poll_loop` re-marks every observed descendant each
   `_DESCENDANT_POLL_SEC` (0.5 s) for the worker's whole life, so a pid aged
   out mid-run is re-added within a tick with a fresh window; both constants
   are pinned together in `tests/test_subreaper.py`. Any other `OSError`
   (e.g. ESRCH) still discards immediately.

   `_mark_reapable(pids)` populates that set, minus
   anything in `_ASYNCIO_MANAGED_PIDS`, and is called from
   `_DescendantTracker._poll_loop` with each `_enumerate_descendants` snapshot —
   the worker subtrees leerie observed and therefore owns.
   `_orphan_zombie_children()` **no longer exists**: any reaper that *discovers*
   PIDs is wrong regardless of how it filters, because a PID between `fork()`
   and asyncio's `os.pidfd_open()` is in no registry, so every exclusion has a
   hole (DESIGN §6 *Zombie reaping*; the scanning design took `preflight`'s own
   `git config` PID on 40/40 real runs → fabricated rc=255 → bogus "git
   user.email is not configured"). `_invoke` still adds `proc.pid` to
   `_ASYNCIO_MANAGED_PIDS` at spawn and `discard`s it in its `finally`, but that
   set is **not** the reaper's safety mechanism — the allowlist is; it serves
   telemetry and `_reparented_orphans` (which must not SIGKILL a live worker).
   `_signal_pids` deliberately does NOT `waitpid` (it only SIGKILLs); the central
   `_zombie_reaper` is the single reaping point. Because orphans now reparent to
   the orchestrator (not PID 1), `_reparented_orphans` accepts
   `ppid in (1, os.getpid())`. `_PR_GET_CHILD_SUBREAPER = 37` exists for the test
   read-back.

**PID-exhaustion detection (`_cgroup_stat` + `_read_stream` probe).** The
above cleanup runs at worker *exit*; leaked `run_in_background`
subprocesses accumulate against the worker cgroup's `pids.max` (default
`worker_pids_max = 2048`, resolved by `resolve_worker_pids_max`:
`--worker-pids-max` > `LEERIE_WORKER_PIDS_MAX` > `worker_pids_max` in
`leerie.toml` > `DEFAULT_CAPS["worker_pids_max"]`) *during* the run.
Once the cap is hit every
`fork()` in the subtree returns `EAGAIN`, so every `Bash` tool-call fails
(in-process tools are unaffected) and the worker spirals without
diagnosing the cause (DESIGN §6 *Detecting PID exhaustion*). The broker
gains a read-only `stat <sid>` verb → `OK <pids.current> <pids.max>
<pids.events.max> <memory.events.oom_kill>` (or `ERR <msg>`); its client is
`_cgroup_stat(sid) -> tuple[int,int,int,int] | None` (the 4th element is
`oom_kill`, consumed by the memory-OOM diagnostic below; None when the
broker is down or containment is off). `_read_stream` keeps a bounded
`deque(maxlen=_PID_EXHAUSTION_WINDOW)` of recent tool-result outcomes
(True=errored) via `_tool_result_outcome(event)` — which returns None for
non-tool-result events (assistant/system/rate_limit) so they are skipped,
NOT counted as resets. When the window holds `≥_PID_EXHAUSTION_ERROR_THRESHOLD`
(3) errors **and the latest result is itself an error** (so the synchronous
broker probe is not re-issued on the interleaved successes of a
healthy-but-failing worker), it calls `_cgroup_stat`, and if `current >= max` or
`pids.events.max` is climbing it `log()`s the cause, relabels the inline
`tool-fail` summary (`_summarize_stream_event`) to name the PID cap, and
raises `WorkerError` — which the existing `except BaseException` in
`_invoke` turns into a `_terminate_proc_tree` + tracker-reap, routing to
the callers' normal handling (implementer → retryable `incomplete-handoff`;
conformer → advisory `None`). `_is_fork_exhaustion(text)` is a cheap
fast-path that also matches the `EAGAIN` string when it survives into the
tool-result, but the cgroup probe is authoritative. A window (not a
*consecutive* counter) is required because tool-results are never adjacent
in the stream — the model's assistant turn always sits between them — so a
consecutive counter could never exceed one. The window still leaves an
ordinary failing test (≤1 error) well below the threshold.

**Memory-OOM naming (`_invoke`'s no-envelope path + `_settle_subtask`,
DESIGN §6 *Detecting memory OOM*).** A build/test command that overshoots
`memory.max` is killed with a bare `Killed` — no tool-result error for the
window detector above to key on, and often no `result` event at all before
`claude -p` is reaped. `_read_stream` tracks `last_bash_cmd` (the most
recent `Bash` tool_use's command, first line only) alongside the
PID-exhaustion window state. In `_invoke`'s `finally`, `final_stat =
_cgroup_stat(cgroup_sid)` is read immediately before `_cgroup_destroy`
(the last point a read is possible — destroy `rmdir`s the cgroup). When
`envelope is None`, if `final_stat[3]` (`oom_kill`) is `> 0`, `_invoke`
raises `WorkerError(f"worker {sid} was OOM-killed on \`{last_bash_cmd}\`
(memory.max={cap} GiB) — raise --worker-memory-max or lower
--max-parallel")` instead of the generic no-result-event message.
`_run_implementer`'s existing `except WorkerError` handler threads that
text into the synthesized `incomplete-handoff` envelope's `summary`
unchanged. `_settle_subtask`'s `empty_handoff` handling (the rescue branch
that keeps committed work, and the no-commits branch that calls `fail()`)
both now prefer `res.get("summary")` — the worker's own diagnostic, when
present — over `_validate_result`'s generic "checkpoint ... does not
exist" `message`, so a named OOM survives even when the subtask
ultimately terminates via the retry cap.

### Abnormal exit and rate-limit contract (DESIGN §6 *Cleanup on abnormal exit*)

All abnormal exits — Ctrl-C, SIGTERM/SIGHUP, WorkerError, unhandled
exception, or `RateLimitedExit` — route through
`_cleanup_on_abnormal_exit(st, full_purge=False)`. **State.json, the
run branch, per-subtask branches, and implementer checkpoints all
survive**; only worktrees are removed (and re-created idempotently on
`resume` via `scripts/new-worktree.sh`).

Per-worktree removal has a 240s timeout, sized for a large worktree
(hundreds of MB, tens of thousands of files after an npm install +
build) under N-way concurrent disk contention. Per-worktree failures (timeout or OS error) are
non-fatal and counted; if any failed, the cleanup emits one closing
log line pointing the user at `scripts/cleanup.sh --run-id <id>` to
finish manually. The pass is best-effort: a stale worktree on disk is
the worst case, not a corrupted run.

Per-worker `subprocess.TimeoutExpired` from `_invoke` (raised when the
worker hits `worker_timeout_sec`, default 5400s / 90 min) is caught
by both `_run_implementer` (returns an `incomplete-handoff` envelope,
matching the WorkerError handoff path so _settle_subtask's existing
machinery handles it) and `_run_conformer` (logs + returns None,
matching the WorkerError advisory-phase semantics). Without these
catches the timeout escapes through the asyncio cancellation chain
into `main()`'s catch-all and dumps a multi-KB traceback — including
the entire `claude -p` command line — to the user's terminal.

`RateLimitedExit` is raised by `_detect_session_limit(text)` inside
`_summarize_stream_event` when a worker stream contains the verbatim
Claude Code subscription message
`"You've hit your session limit · resets <h>:<mm><am|pm> (<IANA TZ>)"`,
or by the same function's `rate_limit_event` branch when the
protocol-level event's `status` field falls outside the known-allowed
set `{"allowed", "allowed_warning"}` — a defensive match against
future terminal status strings (Anthropic's terminal value, e.g.
"exceeded" / "denied" / "blocked", is internal and unobserved by us;
matching everything-not-allowed avoids hardcoding a guess that could
go stale). The protocol-level path parses `resetsAt` (a Unix timestamp
in seconds) into a UTC `reset_at`; the text path parses the wall-clock
time + IANA tz. A **third** raise site lives outside `_summarize_stream_event`: the
`_invoke` no-result-envelope branch. When a worker stream truncates
with no `result` event *and* the account hit credit exhaustion (a
`rate_limit_event` seen mid-stream with `overageDisabledReason in
{"out_of_credits", "out_of_overage"}`, latched into a `nonlocal
overage_blocked`), `_invoke` raises `RateLimitedExit(reset_at=None,
out_of_credits=True, raw)` instead of a bare `WorkerError` — the
out-of-credits-mid-stream-kill case described under §3 *Auth/quota
backoff*. It is deliberately raised here, not in `_summarize_stream_event`,
because the latch must survive to the post-stream no-envelope check even
at quiet verbosity (where the summarizer returns `None`). The latch keys
on `overageDisabledReason`, **not** on `overageStatus == "rejected"`:
the latter is a standing state emitted by every `rate_limit_event` from
an org with overage disabled (`overageDisabledReason:
"org_level_disabled"`, `status:"allowed"`) and is *not* exhaustion —
keying on it misclassified unrelated truncations as out-of-credits. An
`org_level_disabled` truncation therefore takes the ordinary
`WorkerError` path.

Either source produces a `reset_at: datetime | None`
(parse failure → `None`, never a wrong-time guess) and the raw
message. `main()`'s `except RateLimitedExit` arm: when `reset_at` is
set, run worktree cleanup, sleep until the moment + 30s margin, then
`os.execv(sys.executable, [sys.executable, __file__, "resume",
"--run-id", <id>])` to re-exec the orchestrator itself (NOT the
launcher — the launcher is not baked into the container image and
its `resume` path would attempt to spawn a new container; the
orchestrator already runs inside the container with state on disk
and accepts `resume --run-id`). The `--max-workers` budget is NOT
reset across the re-exec: `worker_count` persists in state.json,
so a run that repeatedly hits the rate-limit still respects the
user's cap;
when `reset_at` is None because of an unparseable session-limit
message, sleep a fixed `RATE_LIMIT_RETRY_BACKOFF_SEC` (300 s) and
re-exec `resume` the same way — we can't compute a wake time, so we
poll; a premature retry re-hits the same clean pause. Both of these
(clock-based) arms route through the shared `_sleep_then_reexec(st,
wait_seconds, reason) -> int | None` helper (cleanup → sleep →
`os.execv`). It returns `None` when the `os.execv` succeeds (the process
is replaced, so the return is unreachable), and an **exit code** when
the sleep or re-exec was interrupted/failed instead: `130` on Ctrl-C
(SIGINT), `128 + signum` on SIGTERM/SIGHUP (143 / 129, matching main()'s
top-level signal arm), and `EXIT_LOCKED` (75) on the should-never-happen
`os.execv` failure. The caller does `rc = _sleep_then_reexec(...); if rc
is not None: exit_code = rc` and leaves `abnormal = False` (the helper
already ran cleanup, so the `finally` must not re-run it).

The `out_of_credits=True` arm does **not** auto-resume: out-of-credits
has no reset clock (it clears only on a top-up / billing cycle), so
`main()` runs `_cleanup_on_abnormal_exit(st, full_purge=False)`
directly, logs a `leerie resume <id>` hint, sets `exit_code =
EXIT_LOCKED` and `abnormal = False`, and falls through to the `finally`
(which must not re-run cleanup). This is checked *before* the
`reset_at` branch. `_sleep_then_reexec` is never called for this case.
The old `reset_at=None → exit 75 manual-resume` behavior is gone for
rate-limits (they auto-resume), but out-of-credits deliberately
preserves the surface-and-pause semantics for the reason above.

A terminal auth failure (`_is_terminal_auth_failure`, §3 *Terminal auth
failure*) copies this exact arm verbatim: `_cleanup_on_abnormal_exit(st,
full_purge=False)`, a `resume` hint, `exit_code = EXIT_LOCKED`,
`abnormal = False`. Like out-of-credits, an expired session has no
clock-based reset, so it takes the surface-and-pause disposition rather
than `_sleep_then_reexec`'s auto-resume path.

**Auto-resume override persistence.** The re-exec passes only
`resume <id>` as argv — any CLI overrides on the original
launch (`--model`, `--max-workers`, `--max-parallel`, `--confidence-rounds`,
`--source-of-truth`, `--clarify`, `--no-push`) are **not** propagated
to the fresh process. They fall back to env vars (`LEERIE_*`) and
`leerie.toml` settings, which are re-resolved on every `resume`
(see "Resume integrity" above). Users who rely on a non-default
setting should configure it via env or `leerie.toml` rather than a
single CLI flag, so an auto-resume preserves it. A manual `resume`
(invoked by the user after they Ctrl-C the auto-resume wait, or after
the rare interrupt/execv-failure exit) can re-supply CLI overrides as
needed.

Ctrl-C (SIGINT) is **resumable** — same contract as every other
abnormal exit. The explicit "throw this away" gesture is
`scripts/cleanup.sh --run-id <id> --branches`, not Ctrl-C.

---

## 5½. Mechanical-feedback loops (CRITIC pattern)

Every worker except the PR writer runs inside `_run_checked_loop` — a
generic async function that calls the worker, runs deterministic
structural checks on the output, and, for callers that pass
`make_feedback_prompt`, re-invokes with formatted feedback if issues
are found. The pattern is grounded in the CRITIC framework (ICLR 2024):
self-correction works only with external tool-verified feedback, not
intrinsic self-review.

Three callers — `wiring_judge`, `provision_judge`, and
`integration_judge` — are "detect-and-die, single pass": they pass no
`make_feedback_prompt`, because none can mechanically act on a found
semantic defect the way a planner can add a subtask or a classifier can
add a category. For these, a round that finds issues stops the loop
immediately rather than retrying — a further round would attack the
identical unchanged input with only a fresh, non-deterministic judge
session, which can only ever *lose* the first round's finding (on a
re-roll that happens not to reproduce it), never gain real information.
The oscillation guard below does not apply to this path (it has no
meaning without a re-drive between rounds). The `WorkerError`
infrastructure-crash retry (a fresh session recovering from e.g. a
saturated PID table) is orthogonal and still applies to all callers
regardless of `make_feedback_prompt`.

### Core functions

| Function | Purpose |
|----------|---------|
| `_replan_domain_closure(plans, targets)` | Domains that must be re-planned together with `targets` — the transitive closure of domains depending on them across BOTH the id (`depends_on`) and tag (`requires`→`provides`) channels. A re-plan vanishes every id the domain used, so any other domain holding an edge into it would dangle; re-planning the whole closure makes that vacuous rather than merely checked. Domains are subtask-id prefixes. Consumed by `phase_overlap_judge`'s unresolvable recovery; `phase_plan(..., domains=…)` takes the result. Pinned by `tests/test_scoped_replan.py`. |
| `_repair_prescribed_commands(plans, prescribed)` | Mechanical plan repair for the adherence floor (DESIGN §CRITIC *Repairing an omitted self-report beats re-driving for it*). Synthesises one subtask carrying every prescribed command, `depends_on` = the plan's current sinks (acyclic by construction, schedules alone in the final wave), and returns its id — or `None` when the floor is already clean, there are no commands, or no plan can supply a valid id prefix (a `_reconciler` pseudo-plan's `domain` is not a real category). Mutates `plans` in place; never raises; declines rather than guessing, mirroring `_repair_missing_requires`. Called from `_check_adherence` **before** `check_prescribed_command_coverage`, so a repairable gap never reaches the ~125-spawn re-plan path. Deliberately does not attach to an existing subtask: a verification-shaped matcher hits 32 of 36 subtasks on the real incident plan. Pinned by `tests/test_prescribed_command_repair.py`. |
| `check_replan_affordable(st, caps, gate, plans)` | Budget preflight before a re-plan (DESIGN §13). `check_budget_feasibility` runs once after `_schedule()`, but a re-plan is the largest budget event in a run and was previously authorised unchecked — it re-runs the whole P1 decomposition, not just the planners. Estimates `n_domains × planner_samples + n_subtasks × replan_decompose_estimate` from the **plans the caller passes in** — NOT from `plan_snapshot`, which `_run_phases` writes only after `_schedule()` while both gates run before it. The recommended `--max-workers` is `int()`-cast, since the estimate is fractional and the flag is `type=_positive_int`. `die()`s with `EXIT_BUDGET_INFEASIBLE` when it exceeds what is left. Called at the top of the re-planning `_on_feedback` callback in `phase_adherence_gate`, and in `phase_overlap_judge`, BEFORE `phase_plan`. (`phase_planning_coverage_gate` no longer calls it — that gate is advisory and no longer re-plans, so it has no `_on_feedback`.) Honours `skip_budget_check`. Pinned by `tests/test_replan_budget_preflight.py`. |
| `_run_checked_loop(invoke, check, name, max_rounds, make_feedback_prompt)` | Generic loop: call → check → feedback → retry. Returns `(result, warnings)`. Re-invokes on **gating** findings only — see *Finding severity* below. Oscillation guard aborts a round only when its issue-signature set is EXACTLY EQUAL to an earlier round's — a proper subset (fewer, still-open issues) is genuine partial progress and is allowed to keep retrying (DESIGN §8 *The CRITIC retry pattern's oscillation guard*). |
| `_partition_issues_by_severity(issues)` | Splits findings into `(gating, advisory)`, order preserved. Used by `_run_checked_loop` and `_select_best_planner_sample`. |
| `_issue_is_advisory(issue)` | True when the issue's `LABEL` prefix is in `_ADVISORY_ISSUE_LABELS`. Unknown labels and non-strings are gating. |
| `_confidence_axes_clear(conf, axes, threshold)` | Pure predicate: True when every named axis in `conf` is a number ≥ threshold. Used by the loop and by `_settle_subtask`'s implementer confidence check. |
| `_format_check_feedback(issues, rnd, max_rounds)` | Formats issue list into the structured feedback block injected on re-invocation. |
| `_confidence_schema(axes)` | DRY helper: builds the §8 confidence sub-schema for the given score axes. Used by 10 worker schemas — `classifier`, `planner`, `reconciler`, `implementer`, `integrator`, `rebaser`, `conformer`, `provision`, `plan_overlap_judge`, `fit_judge` (**not** `splitter`, whose output — required `children` only — carries no confidence axis). Current shape: `required: [*axes, "basis"]`; `falsifiers_tested` and `contradictions_reconciled` are **optional** properties; there is no `gap_to_close` field and no `maxLength` caps — both were removed as part of a decoder-corruption mitigation for a required-fields-heavy schema (`anthropics/claude-code#49747`; DESIGN §8 *The disciplines are asked for; they are not schema-required*). The prompts still ask for all three disciplines, directing the gap into `basis` instead. `confidence` itself is **not** in any of these 10 schemas' top-level `required` array (still declared in `properties`, so a worker that does emit it is still recorded) — a worker that omits the whole self-gate block still validates; see each worker's own entry for its current required-field list. Pinned by `tests/test_confidence_not_required.py`; `tests/test_confidence_length_caps.py` covers the sub-schema shape for callers that do emit it. |
| `_subtask_item_schema(*, include_requires, include_migration_targets, include_runs_commands, include_fixes_reported_symptom)` | DRY helper (same pattern as `_confidence_schema`/`_REQUIRES_ITEM`): builds the child-subtask item schema shared by `SCHEMAS["planner"]["subtasks"]`, `SCHEMAS["reconciler"]["added_subtasks"]`, and `SCHEMAS["splitter"]["children"]`, which previously repeated the `id`/`title`/`intent`/`scope_note`/`files_likely_touched`/`depends_on`/`provides`/`success_criteria_seed`/`size`/`investigation_notes` property block as three independently-written literals. The three call sites emit structurally overlapping but not identical objects — `reconciler.added_subtasks` (narrowest: no `requires`, no `migration_targets`/`performs_replacement`, no `runs_commands`, no `fixes_reported_symptom`, since bridging work the reconciler adds is not itself planner-authored original scope), `splitter.children` (`requires` only), and `planner.subtasks` (all four flags) each pass their own `include_*` set rather than converging on one shape — so a future field addition/removal at the builder is explicit about which call sites it reaches instead of silently landing on only one or two. Pinned by `tests/test_shared_subtask_item_schema.py`, including an anti-vacuity check that the narrower call sites do NOT accept the wider ones' optional fields. |

### Finding severity — gating vs advisory

`_run_checked_loop` partitions each round's findings with
`_partition_issues_by_severity(issues) -> (gating, advisory)` and re-invokes
**only on gating findings**. Advisory findings are appended to `warnings` (so
nothing is hidden) and logged once, but never consume a round, never enter
`_format_check_feedback`, and never enter the oscillation guard's signature
set. `_select_best_planner_sample` likewise ranks on the gating subset only.

`_issue_is_advisory(issue)` keys on the mechanical `LABEL` prefix — the same
prefix `_issue_signature` parses, generated by leerie's own check functions,
not LLM prose, so this is not the natural-language parsing CLAUDE.md forbids.
A `LABEL (subtype):` parenthetical is stripped before lookup.

`_ADVISORY_ISSUE_LABELS` is a **frozenset allowlist**, and the default is
therefore **gating**: a finding nobody classified keeps today's behaviour, so
an incomplete classification cannot silently disarm a real gate.

| Advisory label | Why it is advice, not a defect |
|---|---|
| `INTRA_DOMAIN_OVERLAP` | Its own text is "consider merging or splitting". Two subtasks touching one file is frequently legitimate — measured 43 → 12 → 6 across every planner in both 2026-08-03 runs, never reaching zero. |
| `PHANTOM_PATH` | Fires when no ancestor dir exists for a planned path — exactly what a subtask that *creates* a new module looks like. Also the dominant driver of the issue-count/plan-size coupling. |
| `OVERSIZED` | "size='large' — split it" grades a planner self-report, but the independent `fit_judge` in `_recursive_decompose` is the authoritative decomposition gate (DESIGN §5½, §8). |
| `MANY_CATEGORIES` | "typical tasks span 1–3" is a heuristic, not a correctness property. |
| `SAME_WORK_RISK`, `TEST_OWNERSHIP_RISK` | Both end by telling the classifier to apply a judgement test and keep both categories if the deliverables genuinely differ. A finding whose own remedy may be "change nothing" cannot gate. |

Everything else — `DANGLING_DEP`, `INTRA_DOMAIN_CYCLE`, `EMPTY_CRITERIA`,
`PROTECTED_PATH`, `MIGRATION_TARGETS_MISSING`, `UNCOVERED_MIGRATION_SURFACE`,
`PRESCRIBED_CMD_UNRUN`, `REQUIRED_ITEM_UNCOVERED`, and every other worker's
codes — remains gating.

Pinned by `tests/test_issue_severity.py`, whose most important test is
`test_unknown_labels_default_to_gating`: if that default ever inverts, every
future check silently stops gating until someone remembers to classify it.

### Per-worker mechanical checks

Each returns `list[str]` — empty when clean. Pure Python, no LLM. Severity is
resolved from the issue code per the table above, not from the check function.

| Worker | Check function | Issue codes | Max rounds cap |
|--------|---------------|-------------|----------------|
| Classifier | `check_classifier_output(result, repo_root, judge_confirmed=frozenset())` | `CATEGORY_NO_DIR`, `EMPTY_WHY`, `EMPTY_EVIDENCE`, `MANY_CATEGORIES`, `SAME_WORK_RISK`, `TEST_OWNERSHIP_RISK` | `judgment_check_rounds` (3) |
| Planner | `check_planner_output(result, repo_root, domain)` | `PHANTOM_PATH`, `DANGLING_DEP`, `EMPTY_CRITERIA`, `OVERSIZED`, `INTRA_DOMAIN_OVERLAP`, `PROTECTED_PATH`, `INTRA_DOMAIN_CYCLE`, `UNCOVERED_MIGRATION_SURFACE`, `MIGRATION_TARGETS_MISSING` | `planner_check_rounds` (3) |
| Reconciler | `check_reconciler_output(output, plans)` | `RENAME_TO_NOWHERE`, `BAD_PREFIX`, `SELF_DEP` | `judgment_check_rounds` (3) |
| Overlap judge | `check_overlap_judge_output(output, plans, repo_root)` | `PHANTOM_ARTIFACT`, `NO_FILE_OVERLAP`, `DROP_BREAKS_GRAPH`, `DUPLICATE_PAIR` | `judgment_check_rounds` (3) |
| Adherence gate | `check_prescribed_command_coverage(prescribed_procedure, subtasks)` (deterministic floor) + inline `LOW_ADHERENCE` check on the `adherence_judge` result | `PRESCRIBED_CMD_UNRUN`, `LOW_ADHERENCE` | `judgment_check_rounds` (3) |
| Provision | `check_provision_output(result, repo_root)` | `WRONG_PM`, `MISSING_WORKDIR`, `EMPTY_RECIPE` | `judgment_check_rounds` (3) |
| Implementer | `check_implementer_output(result, subtask, actual_files)` | `NO_PLANNED_FILES_TOUCHED` (advisory — reported but excluded from the retry decision by `_gating_issues`), `UNMET_CRITERION`, plus `check_production_evidence`'s four (below) | `implementer_confidence_retries` (2) |
| Integrator | `check_integrator_output(result)` | — | `judgment_check_rounds` (3) |
| Conformer | (unchanged: `_conformance_clean` on observable signals) | — | `conformance_rounds` (3) |

**`UNMET_CRITERION` must not fire on a criterion the implementer was never
responsible for.** `prompts/implementer.md` tells the worker that a criterion
naming the build is a *conformance-phase* signal — the conformer runs the build,
and a build inside a worker's turn budget can OOM the container and get it reaped
mid-turn. With only `{criterion, met, evidence}` on the schema, an obedient
implementer had no way to say so and every such report became a re-drive.

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
rather than the one field (`_confidence_schema`'s docstring records a measured
40.9%-valid outcome on `plan_overlap_judge` from exactly this mistake), while
gating on absence is what stops optional from meaning ignorable. And the
object is **flat with one required inner field, a bare bool** — the verbose
`how`/`observed` strings are optional, since anthropics/claude-code#49747's
decoder corruption is triggered by many required parameters mixed with
verbose strings. `tests/test_production_evidence.py` pins both.

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
rate to 40.9% (27/66) against 99.6–100% for every other worker, with 84 of
85 failures the single error `'artifact_paths' is a required property` — a
whole-payload rejection of otherwise-sound collision analysis. Absence is
the designed-for case (`paths = c.get("artifact_paths") or []`; `if not
paths: continue`). See DESIGN §5 *Cross-domain surface overlap*.
`tests/test_phase_overlap_judge.py`'s `TestProsePathParsingAbsent` pins that
neither of two earlier hand-parsing shapes (whole-string path-checking,
then whitespace tokenizing) has returned.

`DUPLICATE_PAIR` covers two collisions naming the same `{a_sid, b_sid}`
pair. This is **coherent** when the pair genuinely overlaps on more than one
artifact (one row per artifact instead of one row listing every path), and
`_apply_overlap_collisions` absorbs the repeat via its `skipped_redundant`
branch. What matters is not the `resolution` *string* but the resolved
**effect** (dropped sid for a `drop_*`, or the sorted endpoint pair for a
`merge`): identical-effect rows are coalesced by
`_validate_overlap_judge_output` into one collision (artifacts joined,
`artifact_paths` unioned) and applied; rows whose effects genuinely
*differ* (e.g. the same pair emitted twice as `drop_a` with swapped
endpoints, dropping both subtasks) surface as a `DUPLICATE_PAIR` issue from
`check_overlap_judge_output`, giving the judge a retry round.

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
harvest the repo's real CLAUDE.md as spec items, including imperatives
that could never appear verbatim in a subtask — a literal-substring gate
that fired identically every round, burning ~35% of the run's spend on a
signal that could not move. The whole mechanism — `extract_task_file_structure`,
`_is_uncoverable_convention_item`, `_BACKTICK_SPAN_RE`,
`check_task_file_coverage`, `_dedup_frozen_coverage_issues`,
`_format_task_file_structure`, `_MAX_COVERAGE_ITEMS` — is deleted, along
with the `LOW_COVERAGE` issue kind. `phase_plan` now names the referenced
files via `_format_task_file_references` and lets the planner read them;
coverage of what those files require belongs to `task_coverage_judge`
(phase 2⅞½). `tests/test_task_file_coverage_freeze.py` and
`TestProseHarvestAbsent` pin that none of the deleted symbols return.

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
(`_MIGRATION_SIGNAL_RE` matching phrases like "replaces direct `X`"
against `intent`/`investigation_notes`) — the reading-meaning-out-of-prose
CLAUDE.md *Language-to-JSON* forbids, and it did not work: measured on run
`19a70d96`, all 27 extractions were stopwords that grepped to hundreds of
files and always cleared the threshold. Python now greps a symbol the
planner handed it directly.

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
by `_BARE_LOWERCASE_WORD_RE` (`^[a-z]+$`), a regex Python ran against the
planner-populated field — itself a relocated *Language-to-JSON* violation.
It is retired; each `migration_targets` entry now carries a required
`is_real_identifier: bool` field the planner sets itself, and
`_check_migration_surface` trusts that attestation directly (skipping
an entry when it's `false` or absent) rather than re-deriving the
judgment.

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
| per-worker wall-clock (`worker_timeout_sec`) | 5400 s (90 min) global cap, **lowered per worker type by `TIMEOUT_DEFAULT_PER_WORKER` via `resolve_worker_timeout(worker, caps)`** | worker killed; implementer → `incomplete-handoff`. **Two tiers.** With no explicit global — detected by `resolve_worker_timeout_explicit()`, which re-walks the CLI/env/TOML tiers rather than comparing the resolved int to the default — `resolve_worker_timeout(worker, caps)` applies the table, bounded by the global; a worker absent from it keeps the full 5400 s (`conformer`/`implementer`/`planner`, whose derived ceilings reach the cap, plus `judge`/`patch_generator` and anything new). With an **explicit** global — `--worker-timeout SEC` / `LEERIE_WORKER_TIMEOUT` / `worker_timeout_sec` in `leerie.toml` — that value wins outright and the table is **bypassed**. The explicit/implicit bit travels as its own cap, `caps["worker_timeout_explicit"]`, since the resolver returns a plain `int` and "5400 because the operator asked" is otherwise indistinguishable from "5400 because nothing was set." Mirrors `resolve_worker_memory_max`, where an explicit value likewise bypasses the derivation. The three timeout log/handoff messages (`_run_implementer`, `_run_conformer`, `_run_final_conformance`) report `resolve_worker_timeout(...)`, not the global. **Values are derived, never chosen:** each is `min(cap, max(_WORKER_TIMEOUT_FLOOR_SEC=600, ceil(p99*3), ceil(max*1.2)))` computed from `tests/fixtures/worker_duration/summary.json` — the measured distribution of 15,951 real calls across 21 worker types, regenerated by `scripts/measure/worker_durations.py <state-root>`. `tests/test_worker_duration_distribution.py` re-executes the rule against the committed summary. The `max*1.2` term is load-bearing: `planner`'s p99*3 is 5,091 s while its observed maximum is 5,247.6 s, so a p99-only rule would kill a run contained in the corpus it was derived from — the guard pushes planner to the cap, where it is omitted. A fired timeout is retried, but only `_TIMEOUT_RETRY_MAX = 1` time, unlike a `WorkerError` which keeps the full round budget. |
| per-worker idle-event warning (`worker_idle_warn_sec`) | 300 s (5 min) | log a `no stdout events in <gap>s` warning naming the worker, its PID, and any stderr tail. Observation-only — the worker is NOT killed. |
| per-worker cgroup memory cap (`worker_memory_max_bytes`) | auto-derived via `_auto_worker_memory_max` → `_worker_memory_ceiling(slice_max)` from the shared `leerie.slice/memory.max` budget alone (broker `slice` verb; `_cgroup_slice_info`): `max(_WORKER_BUILD_PEAK_BYTES, min(_WORKER_BUILD_PEAK_BYTES * _WORKER_MEMORY_CEILING_MULTIPLIER, slice_max // 2))` — a **fixed isolation ceiling**, deliberately **independent of the live sibling count and of `max_parallel`**, since `memory.max` is a ceiling, not a reservation (DESIGN §6). Falls back to the legacy `/proc/meminfo`-derived basis (`_auto_worker_memory_max_legacy`, VM RAM split across `max_parallel + 1` slots, floored at 8 GiB) only when no broker/slice budget is readable. Contention is handled by admission in **two stages**, never by shrinking caps. Stage 1, `_degrade_max_parallel_for_wave(max_parallel, build_peak_bytes=None)`, runs once at wave entry and is synchronous: it returns the largest N in `[1, max_parallel]` with `slice_max - unreclaimable >= demand * N` and sizes the wave's `asyncio.Semaphore` accordingly; it is never fed back into a later computation. Stage 2 is the per-spawn gate: before spawning, `_await_worker_memory_admission` blocks (polling every 5s, up to 10 min) while measured slice headroom (`slice_max - unreclaimable`, never `memory.current`) is below `demand * (1 + in-flight workers)`. Reservations are bounded by worker LIFETIME, not by elapsed time — the gate returns a token from `_active_admissions` and `_invoke_admitted` (a thin admission wrapper around `_invoke`) releases it in a `finally`. Both stages read the same signal deliberately, so they cannot disagree about one slice's headroom. Pinned by `tests/test_memory_admission_degrade.py`. Overridable via `--worker-memory-max SIZE` / `LEERIE_WORKER_MEMORY_MAX` / `worker_memory_max` in `leerie.toml` (bypasses the derivation only — the admission gate still runs). Suffixes K/M/G/T accepted. **Reconciled against the repo's own declared Node heap (`resolve_worker_memory_max`, `_declared_node_heap_bytes`).** Node 20+ derives its default V8 heap ceiling from the host, but an explicit `--max-old-space-size` overrides that regardless of container size, and a repo's build/lint/test command commonly sets it, most often **inside a `package.json` script**. `_declared_node_heap_bytes` follows a package-manager indirection one level through `package.json`'s `scripts` map, matching all four V8 spellings, via candidates from `_pm_script_candidates` (splits the command on shell separators before tokenising — testing whitespace-split tokens against a separator set alone misses `"build&&node"`). The matcher is deliberately over-inclusive: a missed script under-sizes the cage and the worker OOMs, while an extra candidate costs nothing. A declared heap overrides whatever `NODE_OPTIONS` leerie itself injects for that subprocess (P9), so a declared heap bigger than the per-worker cgroup ceiling would otherwise guarantee an in-cgroup OOM. The headroom constant is `_NODE_HEAP_HEADROOM_BYTES` = 2432 MiB, shared with P9's own injection — both compute mirror images of one quantity and must read the same name, not a duplicated literal (`tests/test_resolve_worker_memory_max.py` AST-pins the subtrahend to a name; `test_node_heap_headroom_is_2432_mib` pins the value). When the resolved cap undershoots `declared heap + _NODE_HEAP_HEADROOM_BYTES`: an auto-derived cap is raised to that floor unclamped; an explicit override is left alone but refused with an actionable `die()`; when even the whole slice budget cannot fit the declared heap, `die()`s naming the shortfall. Regression: `tests/test_worker_heap_ceiling_reconcile.py` and `tests/test_worker_memory_heap_reconcile.py` | the kernel OOM-kills inside the worker's cgroup; sibling workers, the orchestrator, and host-side services are not eligible victims. Enforcement goes through the **cgroup broker** (`scripts/cgroup-broker.py`), which the dropped-privilege orchestrator drives over a Unix socket. The broker creates `<V2_ROOT>/leerie.slice/leerie-w-<sid>` (cgroup **v2** — `V2_ROOT` is `/sys/fs/cgroup` rootful/Fly, or the systemd-delegated user slice under rootless containerd via `LEERIE_CGROUP_V2_ROOT`) or the split `pids/`+`memory/` hierarchies at the fixed `V1_ROOT` (cgroup v1/hybrid, never rootless) and sets its `memory.max`. Local nerdctl needs the launcher's cgroup bind-mount (`bind-propagation=rshared` rootful, a plain bind rootless) + `--cgroupns=host`; Fly's microVM exposes cgroupfs directly. `_cgroup_probe` asks the broker to round-trip a create+enroll+destroy, and `_enforce_and_record_cgroup_containment` `die()`s before the first worker if it fails (unless `--dangerously-allow-uncapped`). See DESIGN §6 *Memory containment*. |
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
fallback). **History:** this worker was previously pinned to opus as a
required override, after an earlier Sonnet generation false-positived a
legitimate plan here. That gap has since closed for Sonnet 5 (externally
verified against Opus 4.8, DESIGN §5 *Opus-judgment, sonnet-workhorse*), so
the worker now follows the global sonnet default; `--model-adherence-judge
opus` remains available as a per-worker override if this gate is ever
observed to regress. Prompt at `prompts/adherence_judge.md` carries the
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
the tree all gate exactly as before; absence is the conservative
direction. Blankness and file existence are checked in
`_coverage_citation_clears`, not by the schema: neither string carries a
`minLength`, because a `minLength` on an *optional* property breaks the
`--dangerously-force-strict-output` invariant that forcing a field must
never make a trivial value illegal. A hallucinated path therefore cannot
buy a downgrade, and the strict-output grammar stays satisfiable. Given the
merged result plus both parent diffs and the conflicting subtasks' intents,
it attacks the merge for behavioral breakage the mechanical conflict-marker
scan and `check_merge_committed` cannot see — a syntactically clean merge
that keeps one side's signature but the other side's call sites, or
silently drops one side's behavior entirely. Wired into `integrate_wave` as a
**detect-and-die, single pass** gate after a successful merge commit: a
non-empty `defects` array `die()`s immediately with the concrete defect named
(an integrator cannot always mechanically re-derive a correct behavioral
resolution from a semantic finding the same way a planner can add a subtask,
so no re-drive). Persists to `state.data["integration_gate"][sid]` and
`state.data["integration_defects"][sid]` — see "Integration gate resume +
`accept-integration`" below.

### Integration gate resume + `accept-integration`

Unlike `wiring_gate`, which is written only on a clean pass,
`state.data["integration_gate"][sid]` is written **before** `die()`ing:
`{defects: list[str], advisories: list[str], merge_commit_sha: str, accepted:
bool}` (`accepted` is `not defects` on a fresh judge verdict — true for a
clean pass, false for a gating finding). A non-empty `defects` entry is
mirrored to the flatter `state.data["integration_defects"][sid]` (a plain
`list[str]`), which is what `accept-integration` clears. Both keys let a
resume distinguish "this sid's merge was never reviewed" (both keys absent)
from "reviewed and rejected, not yet accepted" (`integration_gate[sid]`
present, `accepted: False`) from "reviewed and either clean or
operator-accepted" (`accepted: True`) — `wiring_gate`'s single "written only
on pass" key cannot express the middle state, which is exactly the state a
run stuck on a false-positive `integration_judge` verdict is in.

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
*without ever re-invoking the judge* — the judge only ever runs from inside
the conflict/integrator branch. A present, not-yet-`accepted` entry instead
re-invokes the judge directly against the already-committed merge via the
shared `_run_integration_judge_gate` helper (used by both the normal
post-integrator-commit call site and this resume call site, so the
invoke/partition/persist/die sequence cannot drift between them); a present,
`accepted` entry skips straight to `integrated.append` with no judge call at
all. `phase_execute`'s wave loop has a matching adjustment: the
already-complete-subtasks resume shortcut (`if not remaining: ... skip the
whole wave`) additionally checks for any wave sid with a pending,
un-accepted `integration_gate` entry (`pending_gate_sids`) and, when one
exists, does NOT take the shortcut — otherwise `integrate_wave` (and its gate
re-check) would never be reached again for that wave, silently advancing
`completed_waves` past a rejected merge. Such sids get a `{"status":
"complete"}` stand-in `results` entry (their original `intent`/
`criteria_results` are not persisted anywhere this far removed from the
original settle, so the resumed judge re-invocation runs with an empty
`incoming_intent`/`incoming_criteria` — cosmetic only, since the judge's
primary evidence is the merge diff itself).

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

Per DESIGN §12, classification by substring match on a prose `reason`
would be deterministic code making a judgment call on natural-language
text — a model should classify prose; a substring match cannot. Tagging
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
subtask branch — correct for `no_commits` (the branch holds nothing worth
keeping) and destructive when it does not. Infrastructure kinds skip that
reset: a worktree-setup failure fires before any worker runs, so there is no
leftover from *this* attempt to clear, while an *earlier* attempt's commits
may already be sitting on the branch. `new-worktree.sh` reuses an existing
branch by design, so retrying in place re-attaches to that work instead of
deleting it.

The exemption covers the `continuation` flag and the corrective `note` too,
not just the reset. An infrastructure failure carries no information about
what the worker should do differently, so it must not overwrite the state
that says what the worker should do differently — a worktree failure after
the mechanical-check path has already set `continuation=True` plus a
`_format_check_feedback` note must leave that pending feedback intact, or
the retried worker is blind to the thing it was sent back to fix and burns
`implementer_confidence_retries` re-discovering it. Only a *worker* failure
earns a corrective note, because only a worker failure produced one.

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
   (dotted form, also a valid mise config name) are recognized**;
   non-dotted form wins if both exist (matches mise's discovery
   precedence). If the repo has an existing mise config, its
   `[tools]` content is preserved in the override file
   (`MISE_OVERRIDE_CONFIG_FILENAMES` replaces rather than merges; the
   override is the only file mise reads, so it must carry the repo's
   existing pins plus leerie's addition). Idiomatic version files
   (`.nvmrc`, `.node-version`, `.python-version`, `.ruby-version`)
   and `.tool-versions` entries are ALSO copied into the override
   when the same tool isn't already pinned in the existing mise
   config — otherwise the override would silently drop them too
   (mise discussions #6598 / #7058). Returns the absolute path to
   the override file.

   **Precedence between idiomatic files** (leerie's choice, not
   mise's documented behavior): when the synth fires and both
   `.nvmrc` and `.tool-versions` pin the same tool with different
   versions, `.nvmrc` wins. The iteration order in
   `_read_idiomatic_pins` runs the dedicated single-tool files
   (`.nvmrc`, `.python-version`, etc.) BEFORE `.tool-versions`,
   so the first-seen pin sticks. A repo with conflicting pins is
   a misconfiguration, but leerie picks `.nvmrc` over
   `.tool-versions` for determinism. asdf-compatible names like
   `nodejs` and `python3` in `.tool-versions` are normalized to
   mise's `node` / `python` via `_ASDF_TOOL_ALIASES` so a
   `.nvmrc` + `.tool-versions: nodejs ...` repo doesn't end up
   with both `node` and `nodejs` pins in the override.
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
   *install* entry that lacks it (`_is_pip_install` identifies them — it
   finds the `install` subcommand as the first non-option token after the
   pip prefix, so a leading global flag like `pip -v install` is still
   matched; `uv pip install` and `pipx install` are not, as they manage
   their own environments). The
   container's system Python is Debian-13 externally-managed (PEP 668) —
   a bare `pip install` exits non-zero, which otherwise silently breaks
   every recipe consumer (most visibly `_capture_conformance_baseline`,
   whose failed `pip install` leaves the base test axis recording
   `command not found`). Normalizing at this single data chokepoint —
   the one point every consumer reads the recipe — fixes the baseline
   installer *and* the `PROVISION_RECIPE:` prompt block for
   implementer/conformer workers at once (§12: code enforces; the LLM
   worker mirrors CI, which runs in a venv and never emits the flag). The
   flag is a no-op on a non-externally-managed interpreter, so it is
   applied unconditionally.
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
  built deps (config-only, doc-only, pure-code refactors that don't
  run tests). The persistent bake eliminates this waste for
  Python/Ruby/Rust/Go; Node's residual relink is minimal.
- The bake is shared read-only across concurrent worktrees, so
  dependency installs are paid once per image build, not once per
  worktree.
- `claude -p`'s built-in stream-event plumbing surfaces Bash tool
  I/O to the orchestrator log live, so an install running inside a
  worker is visible to the user without any special orchestrator
  streaming code.

The `MISE_OVERRIDE_CONFIG_FILENAMES` env var that `phase_provision`
synthesizes for polyglot Go repos (go.mod with no `.go-version`
sibling) is exported to `os.environ` once in `phase_provision` (and
re-exported from persisted state on `resume`); worker subprocesses
inherit it without any per-worker plumbing because `_invoke` does
not pass an explicit `env=` to `create_subprocess_exec`.

**Convention-doc injection (`CONVENTION_DOCS:` block).** Alongside the
recipe, `_run_implementer` injects the repo's authoritative convention
docs so the implementer writes UI to the repo's design conventions on
the first try rather than drifting and relying on a post-hoc conformer
catch (DESIGN §9). It calls `_discover_rules_files(st.repo_root)` — the
same discovery the conformer uses — and renders the surviving paths
(relative to `repo_root`) as a `CONVENTION_DOCS:` line in the user
prompt, using the same relative-path formatting as `_run_conformer`'s
`RULES_FILES:` line. Paths only, not contents: the implementer runs in
a full worktree checkout and opens the docs relevant to its subtask, so
inlining a large design-system doc into every prompt is avoided. When
discovery returns nothing, no block is injected. `st.repo_root` is
already in scope in `_run_implementer` (set at `State` construction), so
this needs no new parameter or call-site change. The `prompts/implementer.md`
§3 evidence gate and §4 Implement step name this block so the worker
reconciles the pattern it followed against the discovered conventions.

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
   resume. A partial resume that reaches finalize re-runs capture — the union
   merge makes this a no-op when nothing new was found.
   `st.run_dir / "logs" / "*.log"` is populated by this point.

2. **Cancel / SIGTERM arm (catchable signals).** In `main()`'s
   `KeyboardInterrupt` and `InterruptedBySignal` exception handlers, after
   `st.save()`, a best-effort `asyncio.run(capture_repo_deps(...))` runs in
   its own event loop — the same post-loop pattern as the `RateLimitedExit`
   arm. Non-fatal: any exception is logged and suppressed. This covers the
   Ctrl-C and `nerdctl stop` cases where the orchestrator gets a real Python
   window before the `finally` cleanup block.

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

The `COPY`+`RUN` layer is emitted by a `python3` script the launcher
writes to a temp file (`cat >"$_dep_pyf" <<'PY'`) and runs as
`python3 "$_dep_pyf" "$USER_REPO" "$_leerie_config_toml"` — de-nested
from a `"$(…)"` command substitution so the block parses under bash 3.2
(it is extracted and run under the system bash by the Dockerfile-bake
tests). It has two tiers:

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

When `bake_language_deps=false`, the auto-generated Dockerfile contains
only the apt layer (`USER root; apt-get install ...`), identical to the
pre-existing path. The generated Dockerfile ends with the image still at
`USER root` — it does **not** append a trailing `USER leerie`. The base
image's ENTRYPOINT (`scripts/container-entry.sh`) is inherited by the
derived image and **must** run as PID-1 root to set up cgroup containment
and launch the cgroup broker before dropping to leerie itself via `runuser`
(DESIGN §6 *Memory containment*; the base Dockerfile deliberately omits
`USER leerie` for the same reason). A trailing `USER leerie` here would
override that, making PID 1 run as leerie — cgroup writes, the broker
socket bind, and `runuser` then all fail EACCES and the container exits 1.

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

1. **Skip if `--no-push`.** Same opt-out as before.
2. **Read run state** via `jq` from `$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json` and
   `state.json` (run branch, working branch, finished_at).
3. **Push the run branch.** `git push -u origin leerie/runs/<run-id>`
   (with `--no-verify` if the flag was set). On failure: print the
   same multi-line message as the old Python path (names run branch +
   working branch, the captured push output — stderr plus any pre-push
   hook stdout — and the exact retry command), update `run.json` with
   `push_error`, exit non-zero.
4. **Compose PR title + body.** Primary path: read `pr_title` /
   `pr_body` from `run.json` — these are written by the `pr_writer`
   worker that `phase_finalize` invokes when `push_will_happen` is
   true (see DESIGN §6 *Finalization* and §9 *Structured-output
   schemas* `pr_writer` entry). Fallback path (pr_writer skipped or
   crashed): a bash heredoc reads `state.json` fields with `jq` and
   emits the deterministic body shape that `compose_pr_body` produces
   (task, category, source-of-truth, run timestamps, wave + subtask +
   worker counts, and — when `external_preconditions` is non-empty — a
   `⚠ Deploy-ordering` section rendered from it via `jq`, byte-identical
   to the Python renderer; see "Deploy-ordering notes"). The launcher
   branches on whether `pr_title_llm` / `pr_body_llm` are non-empty.
5. **Open PR.** Before calling `gh pr create`, validate that
   `working_branch` still exists on origin via `git ls-remote
   --exit-code --heads`. If the branch was deleted (common when a
   stacked run's parent was squash-merged while this run was in
   flight), fall back to the repo's default branch (`git remote show
   origin | sed 's/.*HEAD branch: //'`). Then:
   `gh pr create --base <working-branch> --head
   leerie/runs/<run-id> --title "leerie: <pr_title>" --body-file -`
   with the composed body piped on stdin. On failure: log a warning
   with the pushed-branch URL and a retry command (using the
   resolved base — original or fallback); update `run.json` with
   `pr_error`. **Non-fatal** — exit 0 (the run is complete; only
   the PR is missing).

**Local runtime only.** The inline finalize block above runs only when
`LEERIE_RUNTIME != "fly"`. On Fly the run dir is not yet on the host
when this block would otherwise execute (it's on the Fly Machine and
gets streamed back inside the EXIT trap `decide_teardown` that fires
*later*). The Fly path runs the same `host_finalize` function from a
different call site — see *Remote execution mode* below.

**Preflight (`leerie` bash, before `nerdctl run`):** the launcher
checks `git rev-parse --is-inside-work-tree`, `shutil.which gh`,
`gh auth status`, and `git remote get-url origin` BEFORE spinning up
the container. Each failure dies with the same actionable message
actionable messages about each failure, plus the `--no-push`
escape hatch. The orchestrator no longer runs these checks; they
moved to the host where the auth state actually lives.

`--no-push` skips the entire push + PR step. CLI flag, `LEERIE_NO_PUSH`
env, `no_push = true` in `leerie.toml`. **Both the launcher (bash) and
the in-container orchestrator (Python) resolve `no_push` from all three
sources** so they agree on intent: the orchestrator's
`resolve_no_push()` and the launcher's
inline TOML fallback (mirroring `_read_toml_key`'s flat grep — no
`tomllib` dependency, since the launcher runs on the user's host where
Python 3.9 is still common) both check CLI → env → TOML. Disagreement
on a TOML-only opt-out would make the Fly auto-finalize path push
against user intent (the launcher seeds `fly-machine.json.host_no_push`
and the `--host-no-push` argv; the orchestrator gates `pr_writer` and
writes `run.json.no_push`). `--no-verify` is CLI-only and only
affects the push step (worker `git commit`s inside worktrees still
run all hooks).

### Remote execution mode

`--runtime fly` (or `LEERIE_RUNTIME=fly` / `leerie.toml runtime=fly`) routes
execution to Fly.io Machines instead of the local `nerdctl run`. The
Colima/containerd preflight block is gated on `RUNTIME=local` and skipped
entirely when `RUNTIME=fly`. `--runtime` flows through `REWRITTEN_ARGS`
to the orchestrator's argparse. The launcher's bash-side resolution block
also accepts `ec2` so `--runtime ec2` is not rejected by the launcher
before a container/instance starts; EC2 provisioning itself, and the
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
two functions:

- **`provision_machine()`** — creates a Fly Machine from `$FLY_IMAGE_TAG`
  (set by the launcher; see below), polls `flyctl machine status` until the
  machine reaches state `started`, and registers `decide_teardown` as an
  EXIT/INT/TERM trap. Exports `$LEERIE_MACHINE_ID`. Returns 0 on success;
  destroys the machine and returns 1 on failure. Writes `fly_machine_id`
  and `image_tag` (from `$FLY_IMAGE_TAG`) to the run sidecar
  (`$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json`) when `$LEERIE_RUN_ID`
  is set in the environment — written immediately after provision succeeds
  so a launcher crash before classification still leaves a recoverable
  pointer. The `image_tag` field enables `resume_machine()` to detect
  version drift on `resume` and update the machine's image before
  starting it.
- **`stop_machine()`** — runs `flyctl machine stop $LEERIE_MACHINE_ID
  --app $FLY_APP`, tolerant of already-stopped machines. Preserves the
  machine's filesystem on its Fly volume so `resume-machine.sh` can wake
  it later.
- **`destroy_machine()`** — runs `flyctl machine destroy $LEERIE_MACHINE_ID
  --app $FLY_APP --force`, with a stop-then-destroy fallback for machines
  that are already in a terminal state.
- **`decide_teardown()`** — the trap entry point. Classifies
  `$LEERIE_REMOTE_EXIT_RC` (set by the launcher just before exit) and
  dispatches one of three ways:
  - `destroy_machine` for genuine terminal exits (rc=0, EXIT_NEEDS_ANSWERS=10,
    EX_TEMPFAIL=75): the orchestrator exited cleanly and the machine has no
    further value.
  - **Detach** for rc=130/143 (host-side SIGINT/SIGTERM): the user pressed
    Ctrl-C or the local stream broke (laptop closed, WiFi dropped). Since the
    orchestrator on the machine was started detached (Python
    `subprocess.Popen(start_new_session=True, user="leerie", ...)`,
    see *Worker auth + config seeding* below), it is still running. The function
    leaves the machine alone, prints a one-line "detached" banner with the
    reattach / pause / kill commands, and returns.
  - `stop_machine` for unknown non-zero failures (worker error,
    orchestrator exception): preserves the machine's filesystem on its Fly
    volume so the user can attach to inspect and then `leerie resume`. On the
    stop branch, writes `paused_at` and `pause_reason` to the run sidecar.

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
offers to every ssh destination (including `github.com`). After
~5 failed auth attempts per connection, GitHub rate-limits the
account. Containing all Fly certs in a private agent reachable only
by leerie's process tree eliminates the failure mode.

The private agent is persistent (lazy-spawned, never auto-killed)
so the 24h cert is fully reused across leerie runs — re-issuing on
every invocation was what produced the original accumulation. Reboot
wipes the socket inode; the next run lazy-spawns fresh. Parallel
leerie invocations serialize on `~/.cache/leerie/agent/.spawn.lock`
via `mkdir`-as-mutex (portable across darwin/linux without the
non-stdlib `flock` binary that macOS lacks); only the first spawn
wins, the rest see a live socket and reuse it.

The reuse check probes the socket with `ssh-add -l` and reuses it on any
exit code **other than 2**: rc 0 (has keys) and rc 1 (reachable, no keys
yet) both mean the agent is alive; only rc 2 (cannot connect) means the
socket is stale. Treating rc 1 like rc 2 would unlink a live agent's
socket out from under the still-running process.

Any newly-spawned agent carries `-t 24h`, matching the 24h Fly cert
(`flyctl ssh issue --agent`). This bounds only the lifetime of
**identities** added to the agent (`man ssh-agent`), not the agent
process itself — killing the agent is the separate `ssh-agent -k`. An
orphaned agent therefore leaks indefinitely, holding an empty keyring;
there is no reaper for it today.

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

1. **Hallpass readiness probe.** Call `require_fly_ssh` (ensures the
   leerie-private ssh-agent — see above — holds a valid Fly cert,
   issuing only if no cert exists) and `wait_for_fly_ssh_ready` (poll
   `flyctl ssh console --pty=false -C true` against the target
   machine until success; hallpass takes 5-30 s to come up after
   `flyctl machine start` reports "started"). This is the *only*
   hallpass probe in a run — subsequent transports (`seed_repo_clone`
   parent + submodule bundles, `seed_repo_dirty` rsync) rely on each
   pipe's own `LEERIE_SEED_TIMEOUT_S` wrapper (rc 124/137) as the
   authoritative failure detector. An extra probe before each pipe
   would only manufacture false-positives — the channel is
   demonstrably warm by the time seed_auth's multi-MB tar-pipe and
   plugin-cache rebuild have finished. Bound: ~175 s total (12
   attempts × 10 s per-probe timeout + 11 × 5 s sleep); on success
   emits `remote: hallpass ready on <machine>`; on the rare exit-137
   exhaustion (timeout's SIGKILL fire OR external SIGKILL like macOS
   Jetsam under host pressure), the warning includes the "killed
   externally" diagnostic so the operator can distinguish client-side
   pressure from a real Fly outage.

2. **Tar-pipe delivery of `$STAGE` to /home/leerie.** `tar -czC $STAGE`
   (gzip-compressed; excluding `.gitconfig`, `.gitconfig.local`, `.gitignore`,
   `.gitignore_global`, `.git-credentials`, `.netrc`, `.ssh`,
   `.gnupg`, `.config`; with `COPYFILE_DISABLE=1` on the host-side
   tar to silence macOS BSD tar's per-file
   `LIBARCHIVE.xattr.com.apple.provenance` warnings on the remote
   GNU tar) is piped to `flyctl ssh console --pty=false -C "sh -c
   'tar -xzC /home/leerie && chown -R leerie: /home/leerie'"`. The
   `chown -R leerie:` is necessary because the ssh-console session
   lands as root with default umask; without it the orchestrator
   (which runs as leerie) couldn't read its own credentials. The
   `leerie:` (trailing colon, no group name) uses leerie's numeric
   primary group rather than hard-coding a literal group name —
   leerie's primary GID is `HOST_GID` (defaults to 20 / staff on
   macOS hosts) and the group is not necessarily called `leerie`.

   The launcher's `$STAGE` build skips `.claude/local` (the host npm
   install of `@anthropic-ai/claude-code` — the leerie image installs
   claude globally via the Dockerfile, so shipping the host's local
   install is dead weight) plus `.claude/plugins/cache/` and
   `.claude/plugins/marketplaces/` (rebuilt on the remote in step 6
   from the small JSON metadata files that ride along). This keeps
   the stage well under the size where the `ssh console -C` stdin
   pipe starts hitting EOFs.

   On transient "tunnel unavailable" failure from a freshly-spawned
   flyctl agent, the seed retries once after `flyctl agent restart`.

3. **Token fallback.** If `$STAGE/.claude/.credentials.json` was
   not written (Linux, or macOS Keychain extraction failure) but
   `$CLAUDE_CODE_OAUTH_TOKEN` is set, `seed_auth()` writes a
   credentials JSON
   `{"claudeAiOauth":{"accessToken":"<token>","scopes":["user:inference"]}}`
   (the `scopes` field is mandatory — CLI 2.1.210's file-auth rejects a
   scope-less blob; see the `_extract_claude_credentials_json` row above)
   directly to
   `/home/leerie/.claude/.credentials.json` on the machine via
   `flyctl ssh console -C "sh -c 'cat > .../credentials.json
   && chmod 600 ... && chown leerie: ...'"`. If neither source is
   available, `seed_auth()` returns 1 with an actionable error.

4. **Git identity.** Reads `user.name` and `user.email` from the
   host's git config and writes them to
   `/home/leerie/.gitconfig` on the machine via
   `flyctl ssh console -C "sh -c 'IFS= read -r n; IFS= read -r e;
   git config --file /home/leerie/.gitconfig user.name \"\$n\" &&
   git config --file /home/leerie/.gitconfig user.email \"\$e\" &&
   chown leerie: /home/leerie/.gitconfig'"` with the two values piped
   on stdin. Note: NOT `git config --global` — under the
   ssh-console session's default root user that would write to
   `/root/.gitconfig` where the leerie user can't read it. Worker
   commits carry the host user's identity.

5. **Pre-warm `claude --version`** once as the leerie user via
   `flyctl ssh console -C "su leerie -c 'HOME=/home/leerie PATH=... claude
   --version'"`. The FIRST `claude --version` on a freshly-booted
   Fly machine takes ~17 s (Node runtime + statsig client cold-start);
   subsequent calls return in <0.2 s. Paying this upfront means the
   orchestrator's preflight `_check_claude_cli_version` call hits
   warm caches.

6. **Rebuild plugin cache.** The tar pipe excludes
   `plugins/cache/` and `plugins/marketplaces/` (see step 2); the
   small JSON metadata files (`installed_plugins.json`,
   `known_marketplaces.json`) ride along and are the source of
   truth for rebuilding. Inside one `flyctl ssh console` invocation
   (running as the leerie user via `runuser -u leerie -- env HOME=...
   PATH=... sh -s` — not `su -c 'sh -s'`, which has implementation-
   specific stdin-forwarding under util-linux) a shell heredoc runs
   two phases: (a) read `known_marketplaces.json` with a python3
   one-liner — jq isn't in the image — emit each `source.repo` and
   run `claude plugin marketplace add <owner>/<repo>`; (b) read
   `installed_plugins.json` keys (e.g., `vercel@claude-plugins-official`)
   and run `claude plugin install` per entry. Output is appended to
   `/home/leerie/.cache/leerie/plugin-install.log`. Per-plugin
   failures are logged (`WARN: <spec> install failed (continuing)`)
   but non-fatal — a missing plugin only matters if a user-supplied
   task explicitly invokes it, in which case the Claude CLI's
   existing "plugin not found in cache" skip-with-warning behavior
   is the appropriate surface. The invocation is bracketed with the
   same `$(_seed_timeout_prefix)` + `_seed_progress_bg
   "plugin_rebuild"` heartbeat the main tar pipe uses (step 2 above),
   so a stalled `flyctl ssh console` produces a clean rc 124/137
   instead of an indefinite hang and the user sees `plugin_rebuild:
   still streaming (Ns elapsed)` lines on the happy path. The rc is
   captured via `|| _rebuild_rc=$?` (which both grabs the rc and
   suppresses the file-level `set -e` on failure); the trailing
   `remote_log` line branches on rc — `complete` on 0, "timed out
   after Ns" on 124/137, "rc=N — continuing" on any other non-zero
   — so the launcher log honestly reports failure surface without
   aborting the run. Replaces shipping ~200 MB of plugin contents
   over the WireGuard pipe with ~30–90 s of public-egress git-clone
   + bun-install on the Fly machine.

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
# Build the wrapper script host-side with the argv JSON literal
# embedded (so no remote shell quoting touches the orchestrator argv).
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
# /work/.leerie and /work/.leerie/runs were created as root by
# os.makedirs above; chown all three so the orchestrator
# (running as leerie) can write state files later.
for d in ("/work/.leerie", "/work/.leerie/runs", run_dir):
    try: os.chown(d, leerie_pw.pw_uid, leerie_pw.pw_gid)
    except OSError: pass
child_env = dict(os.environ)
child_env["HOME"] = "/home/leerie"   # ssh-console default is /root
child_env["USER"] = "leerie"
child_env["LOGNAME"] = "leerie"
# host-side $(basename "$USER_REPO") expansion — the heredoc is
# unquoted so this becomes a literal basename in the script piped
# to the Fly machine. Keeps orchestrator log() prefix consistent
# with host-side remote_log() (else log() falls back to cwd=/work).
child_env["USER_REPO"] = "$(basename "$USER_REPO")"
# Host IANA TZ baked in so the in-machine log() ISO-8601 offset
# matches host-side remote_log() (else mixed -05:00 / +00:00 in
# the tailed stream). _host_tz is computed in outer bash via
# `readlink /etc/localtime | sed 's|.*/zoneinfo/||'` (works on
# macOS and Linux). Dockerfile installs `tzdata` so the IANA name
# resolves; empty value → Python astimezone() falls back to UTC.
child_env["TZ"] = ${_host_tz_json}
# Bedrock bearer-token activation (when _BEDROCK_BEARER_ACTIVE=true on the
# host): takes precedence over the SSO/profile block below, mirroring the
# nerdctl path's AUTH_MOUNTS ordering. Every value below is JSON-encoded
# host-side (via a `python3 -c 'import json,sys; print(json.dumps(...))'`
# call per variable, same technique _launch_argv_json above already uses
# for argv) rather than substituted as a raw quoted-var string — a raw
# substitution is not injection-safe: an opaque secret like a bearer token
# is exactly the kind of value likely to contain a double-quote or
# backslash that would otherwise break out of the Python string literal
# and run as arbitrary code on this remote machine. A JSON-encoded empty
# string is falsy in Python, so the truthiness checks below behave the
# same as unset. NOTE: this heredoc is unquoted (<<PY) -- never put a
# backtick pair in a comment here, since bash treats it as a
# command-substitution delimiter even inside heredoc body text.
if "${_BEDROCK_BEARER_ACTIVE}" == "true":
    child_env["AWS_BEARER_TOKEN_BEDROCK"] = ${_bedrock_bearer_token_json}
    child_env["CLAUDE_CODE_USE_BEDROCK"] = ${_bedrock_use_bedrock_json}
    if ${_bedrock_bearer_region_json}:
        child_env["AWS_REGION"] = ${_bedrock_bearer_region_json}
# Belt-and-suspenders Bedrock SSO/profile activation (when
# _BEDROCK_ACTIVE=true on the host), skipped when the bearer-token block
# above already activated:
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
                                     # (appended by the launcher; see below)
        stdin=subprocess.DEVNULL, stdout=log_f, stderr=log_f,
        start_new_session=True,    # bash setsid equivalent; portable
        cwd="/work",                # avoid stale-cwd ENOENT cascades
        user="leerie",                # Python 3.9+ user= param
        group=leerie_pw.pw_gid,
        env=child_env,
    )
# Poll briefly before recording the pid. If this Popen lost the
# State.__init__ flock race against an already-running orchestrator
# for this run (the concurrent-spawn race described in DESIGN §6
# *Single owner per run dir*), the child exits 75. Writing its pid
# to orchestrator.pid before the race resolves would overwrite the
# winning orchestrator's pid with a dead one — see the stale-pid
# contagion in DESIGN §6. Budget 2 s: the realistic time from Popen
# to State.__init__'s flock attempt is ~300-500 ms (Python startup
# + leerie.py imports + main()'s pre-State config resolution), up
# to ~1 s under disk pressure. State.__init__ itself is microseconds.
# The reader-side /proc cross-check catches any residual case where
# the budget is exceeded on the loser path.
for _ in range(10):
    if p.poll() is not None:
        break
    time.sleep(0.2)
if p.poll() == 75:
    # Stillborn — winner still owns the run; do not touch the pid file.
    # The launcher's existing rc=75 short-circuit (~30 lines below)
    # pivots into the resume smart-router's attach-tail behavior.
    # Container-rc 130 (detach banner) leaves the live machine alone.
    sys.exit(75)
with open(pid_path, "w") as pid_f:
    pid_f.write(str(p.pid) + "\n")
PY
)"
printf '%s' "$_launch_script" \
  | flyctl ssh console --app "$FLY_APP" --machine "$LEERIE_MACHINE_ID" \
      --pty=false -C "python3 -"

# Separately tail the orchestrator log via a second ssh-console
# session (its death — Ctrl-C, broken pipe, laptop disconnect —
# does NOT propagate to the orchestrator).
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
first launch (the run dir already exists — `provision_machine()` wrote
`fly-machine.json` there first), and on pre-classify resume — when
`LEERIE_TASK_ARG` is empty in this invocation's argv — reads it back
and appends to `REWRITTEN_ARGS`. Both writes are idempotent (`! -f`
and "no task in argv" guards), so an explicit re-supplied task on the
resume command line wins. `task.txt` is launcher-side; the orchestrator
never reads it.

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
   on the machine. `--all` packs every ref into one pack-format binary
   stream. The `sh -c '...'` wrapper is load-bearing — bare
   `-C "cat > /tmp/..."` is parsed by flyctl as if `>` were a `cat`
   argument and fails with `cat: invalid option -- 'c'`.

2. **Submodule bundles, recursive.** Host runs `git submodule --quiet
   foreach --recursive 'git bundle create - --all | flyctl ssh
   console -C "sh -c '\''cat > /tmp/leerie-subs/<flat-displaypath>.bundle'\''"'`
   so each submodule's pack data lands as its own file on the machine.
   The flat-displaypath name (`/` → `_`) gives unambiguous filenames
   for nested submodules.

3. **Machine-side clone + submodule update.** A single
   `flyctl ssh console -C "sh -c '<script>'"` call:
   - `git clone /tmp/leerie-seed.bundle /work` (treats the bundle file
     like a remote; recreates `.git/` and checks out HEAD).
   - For each submodule, `git config submodule.<name>.url
     /tmp/leerie-subs/<bn>.bundle` (sets the URL in `.git/config`, NOT
     `.gitmodules` — we never modify the committed file).
   - `git -c protocol.file.allow=always submodule update --recursive`
     (clones each submodule from its bundle file). The
     `protocol.file.allow=always` flag is load-bearing — git 2.38+
     blocks the `file` protocol by default per CVE-2022-39253, which
     would otherwise abort the submodule clone with `fatal: transport
     'file' not allowed`.
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
transit at all), but the delta does.

The script is **sourced** (not exec'd) by the launcher — the same
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
   `flyctl ssh console -C`. The python script picks the entry
   with `finished_at` set, no `pushed_at`, and the most recent
   mtime, then prints four lines on stdout: run_id, branch,
   working_branch, no_push.

   CRITICAL: stderr is captured to a separate tmpfile, NOT
   merged into stdout via `2>&1`. `flyctl ssh console` prints
   "Connecting to fdaa:..." to stderr; merging it would shift
   every parsed line by one and corrupt the discovered run_id
   into the "Connecting to..." string, then the branch name
   becomes what should have been the run_id, etc. Downstream
   `git bundle create` would silently produce an empty bundle
   against a nonexistent branch.

2. **Probe branch existence** — `git -C /work rev-parse --verify
   refs/heads/<run_branch>` via ssh console. If the branch does
   not exist (the cleared-but-empty terminal-state case described
   in DESIGN §8 — the orchestrator exited cleanly because the
   task was already satisfied on HEAD; setup-run.sh never ran),
   skip step 3.

   We do NOT use the `no_push` flag from `run.json` as a proxy
   for "no branch was materialized." `no_push=true` is a
   *mechanism* flag the launcher always forces on the in-Fly
   orchestrator (the machine can't push), not a *user-intent*
   flag and not a "no branch" signal. The user's actual no-push
   intent lives in `fly-machine.json`'s `host_no_push`.

3. **Run branch via git bundle** — `git -C /work bundle create -
   leerie/runs/<run-id>` on the machine, piped to a host tempfile,
   then fetched via `git fetch <bundle> +<branch>:<branch>` into
   the host repo. The bundle resolves cleanly because both repos
   share the same origin history.

4. **Run state directory** — tars `/work/.leerie/runs/<run-id>`
   on the machine and extracts it under `$LEERIE_STATE_HOST_DIR/runs/`
   on the host. After extraction, `run.json` and `state.json`
   are present on the host exactly as they would be after a
   local run.

5. **Strip mechanism `no_push` from synced run.json — conditional
   on branch presence.** After the tar extracts, if a run branch
   was actually fetched in step 3 AND the host-side run.json has
   `no_push=true`, remove the field. This is defense against
   in-flight old-image runs that wrote the mechanism flag; the
   user's intent is stored elsewhere (see
   `fly-machine.json.host_no_push`).

   When step 2's branch probe returned absent (the cleared-but-empty
   terminal-state case — DESIGN §8), the stripper is **skipped**.
   `_finish_no_work_run` deliberately writes `no_push=true` to
   `run.json` as **intent** ("nothing to push — no branch exists"),
   and `host_finalize`'s `no_push` gate reads that intent to
   short-circuit cleanly (the rev-parse defense-in-depth guard is
   a backstop for the same case). Stripping `no_push` here would
   disarm the gate; host_finalize would fall through to the
   rev-parse guard and still return cleanly, but the on-disk
   run.json would no longer reflect the orchestrator's intent.

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
immediately after `resume_machine` — before `seed_auth`. If the probe
returns rc=75 (lock held), the launcher skips `seed_auth`, `re_seed`,
and the launch wrapper entirely, pivoting straight to
`_attach_to_live_orchestrator` (lib.sh). SSH readiness is not a concern:
when the orchestrator is alive, the machine was never stopped and
hallpass is already warm; if the probe fails for any non-75 reason, the
launcher falls through to `seed_auth`. **Launch-time probe
(belt-and-suspenders):** the launcher's in-machine Python launch
wrapper takes a fast-path flock probe (DESIGN §6 *Single owner per run
dir*) and exits 75 when the run-directory lock is held. This covers
fresh provisions and any race the early probe missed. Because `flyctl
ssh console` does not forward remote exit codes (see
§Single-owner-per-run-dir enforcement, *flyctl exit-code workaround*),
the launcher parses the real code from stderr via
`_extract_flyctl_remote_rc`. Both probes pivot via
`_attach_to_live_orchestrator` (lib.sh): it invokes
`tail_with_optional_autofinalize()` (default) or a `flyctl ssh console`
bash payload (`--shell`) against the live machine, and sets
`container_rc=130` so `decide_teardown` leaves the machine alone. The
attach transport is `flyctl ssh console` proxied through Fly's
hallpass + WireGuard mesh — no sshd in the image, no key management,
no public exposure. Auth inherits from `flyctl auth status`.

Run-id resolution:

1. `leerie resume <id>` → look up
   `$LEERIE_STATE_HOST_DIR/runs/<id>/fly-machine.json` first, then
   `$LEERIE_STATE_HOST_DIR/runs/<id>/run.json` (which carries
   `fly_machine_id` per Phase 2). If neither yields a value, exit
   with the per-id "no Fly machine pointer found" error.
2. `leerie resume` (no run-id) → scan
   `$LEERIE_STATE_HOST_DIR/remote/*.json` for active records (records
   whose filename is a launcher PID that still exists). Exactly one
   → resolve the run-id from the record and continue. Multiple →
   print the list and exit 1. None → fall through to the existing
   per-id "no Fly machine pointer found" error path.

`provision.sh` writes the PID-keyed record at
`$LEERIE_STATE_HOST_DIR/remote/$$.json` immediately after creating the
machine, and also writes the run-keyed pointer
`$LEERIE_STATE_HOST_DIR/runs/$LEERIE_REMOTE_RUN_ID/fly-machine.json`
in the same call — before returning to the launcher — so `resume`
survives a Ctrl-C between `provision_machine()` returning and the
launcher's deferred copy. `destroy_machine` removes the PID-keyed
record on full reap. The launcher's copy (guarded by `[ ! -f ]`) is a
no-op fallback for compatibility with older images.

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

Local-runtime `resume` is unaffected by this smart router. Local
runs are synchronous foreground processes (`nerdctl run --rm` with no
backgrounding), so there is no detached container to attach to; local
`resume` keeps its existing inline-re-exec behavior. The smart
router branches live inside the `RUNTIME=fly` guard.

Maps to `DESIGN.md`: §6 *Smart resume in remote mode*.

#### Mid-run re-rsync (`scripts/remote/re-seed.sh`)

Two user-visible surfaces share one mechanism:

1. **`leerie re-seed <run-id> [--force]`** — explicit fast-path
   before runtime preflight. Wakes the machine if stopped, runs the
   safety check, runs `seed_repo_dirty`, exits. No orchestrator
   exec — for the case where the user wants to attach via Phase 3
   to inspect before resuming.
2. **Auto-re-seed on `leerie resume <run-id> --runtime fly`** —
   inside the `RUNTIME=fly` branch, when `resume_machine` runs
   (i.e., the dual-file resolver — `fly-machine.json` first, then
   `run.json` — yielded a `fly_machine_id` for the run-id), the
   launcher calls `re_seed` between `seed_auth` and the orchestrator
   exec. `--no-re-seed` opts out (rate-limit case where nothing
   changed host-side). `--force` bypasses the safety check.

   The dispatch is strict on `resume`: if no machine pointer is
   found in either sidecar, the launcher dies with a diagnostic
   pointing at `leerie list` rather than silently provisioning a
   fresh machine (which would orphan the original on Fly). Likewise,
   if `resume_machine` returns non-zero (machine destroyed or
   unstart-able), the launcher exits with the failure instead of
   falling through to `provision_machine`. Without `resume`,
   behavior is unchanged — fresh runs always provision.

Three operations in `re_seed`, in order:

1. **Wake the machine if needed.** `flyctl machine status` → if
   `stopped`, `flyctl machine start` + `wait_for_started`. Other
   states (`destroyed`, `replacing`, …) abort with an actionable
   message.
2. **Safety check (unless `LEERIE_RE_SEED_FORCE=1`).** Run
   `flyctl machine exec git -C /work status --porcelain` and filter
   out paths under `.leerie/` (worker state is expected to change
   there). If any tracked file is dirty, refuse with a message
   listing the first 10 paths and pointing at `leerie resume
   <run-id> --shell` and the `--force` bypass. Prevents silent
   clobbering of in-flight worker edits that haven't yet been
   committed to a per-subtask branch.
3. **`seed_repo_dirty`.** Recompute the host's `git status
   --porcelain` dirty set, append every file under the repo-local
   `.claude/` directory (force-included even when gitignored — workers
   need its hooks/agents/skills/commands), filter the combined list
   (drop `.git/*`, non-whitelisted `.leerie/*` paths, and `.leerie/runs/*/worktrees/*` defensive
   entries), then rsync the result to `/work` on the machine via
   `fly_rsync_wrapper` from `lib.sh` (transports `rsync --server` over
   `flyctl ssh console -C`). The full-history clone on the machine is
   preserved — re-seed must never re-clone, because that would
   obliterate the run branch and per-subtask branches.

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
  launcher (bash). `_auto_detect_fly_runtime` is retained as a
  back-compat Fly-only wrapper around `_auto_detect_run_runtime` for
  call sites that have not yet grown EC2 handling.
  - **Fly path:** sources `provision.sh`, exports `LEERIE_MACHINE_ID`
    and `FLY_APP`, calls `stop_machine()`.
  - **EC2 path:** sources `aws-credentials.sh` + `ec2-lib.sh` +
    `ec2-provision.sh`, resolves AWS credentials via
    `resolve_aws_credentials()` and gates on `require_aws()` (same
    ordering as the `RUNTIME=ec2` dispatch branch — see "Remote
    execution mode"), resolves `LEERIE_EC2_INSTANCE_ID` from the run's
    sidecar via `_resolve_ec2_instance_id_from_run_dir` (checks
    `ec2-instance.json` first, then `run.json` — mirrors
    `_resolve_volume_id_from_run_dir`'s fallback order), then calls
    `stop_instance()`. `StopInstances` preserves the root EBS volume
    (DESIGN §6 *EC2 runtime lifecycle*, "EBS volume lifecycle" case 2
    — stop-scoped, never `DeleteOnTermination`). Fails closed with an
    actionable message if no `ec2_instance_id` is found in the
    sidecar, and via `require_aws`'s existing `aws sso login` hint if
    credentials don't resolve — before any `aws ec2 ...` call is made.
  - **Local path:** sources `lib.sh`, calls `nerdctl stop <run-id>`
    (SIGTERM first — the orchestrator's `InterruptedBySignal` handler
    saves state before exit — then SIGKILL after grace period; `--rm`
    on the original `nerdctl run` auto-removes the container).
  - All three paths call `update_run_json` to set `paused_at =
    <iso_now>` and `pause_reason = "user-requested"` on the sidecar
    (the EC2 path also writes `ec2_instance_id`, mirroring how the Fly
    path writes `fly_machine_id`). The run is resumable via `leerie
    resume <id>` — the `RUNTIME=ec2` dispatch branch resolves a
    paused instance id from the sidecar and calls `resume_instance()`
    (see the `scripts/remote/ec2-resume-instance.sh` row above and
    `tests/test_ec2_launcher_resume.py`).
  - **Test coverage:** `tests/test_ec2_launcher_stop.py` — EC2
    autodetect and explicit `--runtime ec2`, `stop-instances` called
    and never `terminate-instances`, missing-instance-id and
    credential-failure fail-closed paths (asserting zero `aws ec2`
    calls reach the stub), and a regression pin that the local/Fly
    fallthrough error text still fires unchanged when no sidecar of
    any kind is present.
- **`leerie kill <run-id> [--force]`** — destroy. Same runtime
  detection as `stop`. Prompts the user to type the run-id to
  confirm (unless `--force` / `LEERIE_FORCE_KILL=1`).
  - **Fly path:** calls `destroy_machine()`, sets `killed_at` and
    `fly_machine_id` on the sidecar.
  - **Local path:** calls `nerdctl kill <run-id>` (immediate SIGKILL),
    sets `killed_at` on the sidecar.
  - The run is no longer resumable.

  Recovery path for the orphan case: `leerie kill --machine-id <id>
  --app <app>` allows destruction by machine-id directly when the
  sidecar is missing or unreadable (e.g., `.leerie/` was deleted but
  the machine is still running on Fly). This path is Fly-only.

Both verbs route before any runtime preflight (fast-path dispatch)
and exit without ever sourcing `seed-auth.sh` / `seed-repo.sh`. The
Fly path calls `require_flyctl` from `lib.sh`; the local path only
sources `lib.sh` (for `update_run_json` / `iso_now`). Both are
read-only with respect to the local repo (except for the sidecar
update).

The `killed_at` field is added to `RUN_STATUSES` in `orchestrator/leerie.py`
as a new terminal state (`killed`); `_derive_run_status` reads it
before `paused_at`. `_validate_run_json` enforces that `paused_at`,
`pushed_at`, and `killed_at` are mutually exclusive (same invariant
pattern as today's `paused_at` vs `pushed_at`).

#### Completion gate (`incomplete` status + finalize refusal)

DESIGN §6 *`finished_at` is a discovery sentinel, not a completion
signal*. Because `main()`'s `except SystemExit` handler stamps
`finished_at` on any post-setup `die()` (needed for `fetch_branch`
discovery), `finished_at` does not by itself mean the run's waves all
integrated. A run OOM-killed mid-wave can carry `finished_at` with
`completed_waves < len(waves)`. Three code-surface elements gate on real
completion, all reading the same signal from `state.json` (`run.json`
never carries `completed_waves`/`waves`):

- **`_derive_run_status`** takes `state_json` (already passed) and, when
  `finished_at` is set but `completed_waves < len(state_json["waves"])`
  and neither `killed_at` nor `paused_at` is set, returns the new status
  `incomplete` instead of `done`/`done-pushed-*`. The check fires after
  the push/PR-error checks (a real push/PR error still surfaces as
  itself) but before the `finished_at`→`done` check. `incomplete` is
  added to the derived-status set and is a valid `list status`
  filter value. The cleared-but-empty terminal state
  (`no_work_required`, `waves == []`) is exempt — `completed_waves (0)
  < len([]) (0)` is false, so it still reads `done`. This gates only the
  `list` *display*, not the push.
- **`phase_finalize`** guards its entry: if
  `completed_waves < len(waves)`, it `die()`s with a "refusing to
  finalize: N of M waves complete" message rather than writing the real
  `finished_at`. Belt-and-suspenders — the normal wave loop only reaches
  `phase_finalize` after all waves integrate (the no-work path returns
  before it), but a stray finalize-only invocation is blocked here. Note
  this is the *in-container* orchestrator; it does not itself push.
- **`host_finalize`** (`scripts/host-finalize.sh`) is the **load-bearing
  gate**, because the push+PR is host-side. After the `no_push` and
  `pushed_at` early-returns, it reads `$run_dir/state.json` (already in
  scope for the PR-body fallback) and `return 1`s with an actionable
  resume hint when `no_work_required != true` and `completed_waves <
  (.waves | length)`. All three host-side push entry points funnel
  through `host_finalize` — the launcher's auto-finalize block
  (`leerie`), the `leerie finalize <id>` verb, and Fly's
  `decide_teardown` (`scripts/remote/provision.sh`) — so this one gate
  covers them all. Fail-open: absent/non-numeric wave fields skip the
  gate so a legitimately complete run is never blocked over a missing
  file. Without this gate, `_derive_run_status` and `phase_finalize`
  alone would still let a stray `finalize` push a partial branch (the
  PR-#22 incident).

#### `_create_empty_subtask_branch(repo_root, run_id, sid)`

A settle that never ran an implementer still owes the wave a branch.
`integrate_wave` filters on `status == "complete"` alone and never asks whether
`leerie/subtasks/<run-id>/<sid>` exists; `scripts/integrate.sh` exits 2 on a
missing branch, and `integrate_wave` turns rc 2 into a `die()`.

The post-execution rescue is safe because its implementer ran: the branch
exists with zero commits, and `git merge --no-ff` of a branch already an
ancestor is a true no-op. The pre-spawn probe (DESIGN §8 *Probing a flagged
subtask before it spends*) returns first, so this helper creates the same
artifact at the run-branch tip before settling.

Idempotent — an existing branch is never repointed, since on resume it may
carry commits — and the settle is gated on it: if the branch cannot be created
the subtask falls through to its implementer, because a settle integration
cannot merge is worse than the spend it saves. A failure names the git error
rather than reporting an unactionable "could not be created".

#### Scoped worktree pruning (`worktree-lib.sh`, `_prune_leerie_worktrees`)

`scripts/worktree-lib.sh` exports `prune_leerie_worktrees <leerie-root>`, sourced by `setup-run.sh`,
`new-worktree.sh` and `cleanup.sh`, which previously each ran a bare
`git worktree prune`.

The repo is bind-mounted whole into every container, so `.git` is SHARED with
the host and with any other container. A bare prune is repository-global and
has **no grace period** — the 3-month `gc.worktreePruneExpire` applies to
`git gc`, not to an explicit `prune` — so it drops the registration of any
worktree whose path is absent from the pruning process's namespace. That
includes every host-side `/tmp/tmp.*/rebase-<run-id>` worktree the finalize
rebase creates, which no container can see.

The replacement asks git what it would prune (`git worktree prune -n -v`,
whose output is on **stderr**), maps each reported admin name back to its path
via `$GIT_DIR/worktrees/<name>/gitdir`, and prunes only registrations under the
root it is GIVEN. The two callers pass different roots and that is deliberate:
the shell helper is called with `$LEERIE_ROOT` (the state root), so it may reap
a sibling run's stale registrations, while the Python port is called with the
run directory and cannot. Host registrations — every
`/tmp/tmp.*/rebase-<run-id>` the finalize rebase creates — are out of scope
either way, which is the whole point. Removing the prune entirely is not an option — it is what
clears the stale `.git/worktrees/` metadata a SIGKILLed run leaves behind, the
failure `new-worktree.sh` documents.

The orchestrator's own four call sites — `_cleanup_on_abnormal_exit`,
`_reset_subtask_worktree`, `_prune_subtask_worktree` and `phase_execute`'s
post-`setup-run.sh` prune — go through `_prune_leerie_worktrees`, the Python
port of the same mechanism at run-dir granularity (see above). Two of the four
(`_reset_subtask_worktree` and `_prune_subtask_worktree`) dispatch it via
`asyncio.to_thread`, because both are awaited from inside the wave and the
call shells out to git — both share that rmtree-fallback+`to_thread`-prune
tail through one helper, `_rmtree_fallback_and_prune`, rather than each
inlining it; `_cleanup_on_abnormal_exit` runs synchronously off
`st.run_dir` on a path where there is no loop left to block. The probe itself
runs under `LC_ALL=C LANGUAGE=` — `git worktree prune -n -v` wraps its output
in `_()`, so parsing English prefixes under another locale matches nothing
(`tests/test_worktree_prune_scoping.py` pins the property, not the spelling).

`tests/test_worktree_prune_scoping.py` guards against a bare-prune
regression at any of these sites. It derives its surface from
`scripts/**/*.sh` + `scripts/**/*.py` + `orchestrator/**/*.py` +
`chain/**/*.py` + the launcher, including Python embedded in a shell
heredoc, via an `ast` walk over call names, argument shapes and
languages, backstopped by a coarse textual floor underneath.

#### Prune verb (`leerie prune`)

Reclaims state that nothing else reaps — run directories, repo-map-cache
entries, and stale `leerie/subtasks/*` branches accumulate unbounded, while
`preflight()` already refuses to start a run on low disk headroom and tells
the operator to prune by hand.

Host-only (no container). `leerie prune [--older-than DAYS] [--apply]`;
`--older-than` accepts both the space-separated and `=` forms and defaults to
**14**. **Dry-run is the default** and `--apply` is required to delete anything:
this removes directories that may hold the only record of a paid-for run, so the
safe mode is the one you get without asking.

Three categories. Run directories and cache entries are subject to the age cutoff; **branches are not** — they are scoped by run-dir liveness, so a branch whose run dir was deleted by hand is in scope regardless of age:

- **terminal run directories** — only those whose `run.json` carries
  `finished_at` or `killed_at`. A paused or in-flight run is resumable and
  survives regardless of age.
- **repo-map cache entries** under `<state-root>/repo-map-cache/` — regenerated
  on demand.
- **orphaned subtask branches** — `leerie/subtasks/<run-id>/*` whose run-id is
  not among the runs this pass left alive (`run_id not in live`), which is a
  different set from "run directory absent from `<state-root>/runs/`": a run
  dir removed by this very pass is gone from disk yet still in scope, and a
  surviving dir keeps its branches whatever their age. `docs/USAGE.md`
  already states it this way. Scoped to that namespace, so a user branch is
  never in scope.

**Branch reaping needs positive evidence.** "No run dir in this state root" is
not evidence a branch is orphaned — a state root is silent about every run it
never owned, so pruning from the wrong `--state-dir` would read every branch as
dead. Checking `runs.is_dir()` does not help: the orchestrator creates that
directory unconditionally, so the guard can never fire.

Three tiers, in order:

1. a run dir **this prune just removed** is known terminal and old → `-D`;
2. the branch is an **ancestor of its own run branch**, so its commits are
   already reachable from the integrated history → `-D`. This tier is what
   keeps the feature useful: `git branch -d` checks merged-into-HEAD, and a
   subtask branch is merged into `leerie/runs/<id>`, never into `main`, so
   without it a fully integrated, long-pushed branch is refused exactly like
   one holding unique work;
3. everything else → `git branch -d`, which git refuses when the branch is
   unmerged.

Stale merged branches are still reclaimed (F22's 64); unmerged work cannot be
lost whatever this root does or does not know. Kept branches are reported
(`kept N subtask branch(es) with unmerged commits`), and a delete that fails for
any *other* reason is reported as itself — `-D` never refuses for unmergedness,
and saying it did is what kept the next defect invisible.

**Worktree registrations are dropped before the run dir is deleted.**
`shutil.rmtree` removes `<run>/worktrees/<sid>/` but leaves
`.git/worktrees/<sid>`, and git then refuses `git branch -D` with "cannot delete
branch … used by worktree at …" — so registrations must be dropped first.

Attribution is by **host** path, and the `gitdir` file may hold either
spelling: a subtask worktree is created *inside* the container, where the
state root is bind-mounted at `/leerie-state`, so `new-worktree.sh` writes
`/leerie-state/runs/<id>/…` while prune runs on the host. `_host_spelling`
translates the container prefix — the same mapping `_operator_path` performs
for operator-facing text — before any comparison. Without it the whole
deregistration was a no-op in the only runtime that produces the defect.
A relative `gitdir` (git ≥ 2.48 with `worktree.useRelativePaths=true`) is
resolved against the *entry* directory, not the process cwd, and an empty one
is unattributable rather than resolving to the cwd.

It is scoped by construction to registrations pointing inside the state root,
so a sibling checkout's or the operator's own worktrees are never touched, and
git's `locked` marker is honoured. Within that scope it sweeps **every**
orphaned registration, not only the directories this pass removed: one removed
by an earlier prune, by `cleanup.sh` or by hand leaves a registration no later
prune would consult, and its branch is then unreapable forever.

**Reaping requires liveness, not just timestamps.** Nothing clears
`finished_at`, so a run that die()d once reads as terminal on every later
prune, leaving `mtime < cutoff` as the only protection — and `--older-than 0`
is accepted. `_is_live` probes the run-directory flock (`State.__init__` holds
it for the life of the orchestrator, it is an inode lock, and the container
bind-mounts that directory, so a host-side probe sees a container-side holder)
and then `nerdctl inspect`, which covers a crashed orchestrator that released
the lock while its container is still up. It fails **closed**: any probe that
cannot complete reads as live.

Dry run classifies branches the same way `--apply` does — it previously
appended every candidate without probing mergedness, so the default mode
overstated the result and never printed the `kept` line, the one thing an
operator needs before choosing `--apply`. The two modes are not identical: a
dry run performs no deregistration, so it has no `branches_blocked` equivalent
and cannot report a delete that `--apply` would find blocked.

`prune` is a launcher-only verb and appears in the `REWRITTEN_ARGS` guard arm,
so a misplaced `leerie <task> prune` errors rather than reaching the
orchestrator's argparse.

#### Accept-blocked verb (`leerie accept-blocked`)

When a subtask returns `status: blocked` due to unsatisfied `extent:
external` prerequisites (DESIGN §5), `resume` retries it — which
blocks again indefinitely. The `accept-blocked` verb lets the
operator acknowledge the external block so `resume` skips that
subtask.

- **`leerie accept-blocked <run-id> <subtask-id> [--runtime fly|local|ec2] [--force]`**
  — sets `subtask_status[sid]` to `"complete"` in state.json and removes
  the sid from the `blocked` dict (if present). On `resume`,
  `phase_execute`'s wave-skip filters subtasks whose `subtask_status` is
  `"complete"`, so the accepted subtask never re-dispatches.

  The gate resolves the `blocked` registry **before** testing
  `subtask_status`, because that registry — not the status string — is the
  authoritative record, and the two can disagree by **absence** (a sid in
  `blocked` with no `subtask_status` entry at all, because its checkpoint
  was rejected before a status was written) as well as by value.

  `--force` settles a subtask abandoned **mid-flight** — `in_progress` with
  no registry entry, what a hard crash (ENOSPC, SIGKILL) leaves behind and
  which neither field can express as blocked. It bypasses both status
  checks, so it validates the sid against the scheduled set (`waves`) to
  stop a typo minting a bogus `subtask_status` entry — **when `waves` is
  present**, which is every run where a subtask can be blocked or
  abandoned, since `waves` is populated at scheduling and nothing can be
  abandoned before then. Legacy or pre-scheduling state carries no `waves`
  and degrades open rather than refusing a legitimate accept. The default
  stays strict, keeping `--force` a deliberate operator act. Pinned in
  `tests/test_accept_blocked.py` (absent-key accepted, neither-field still
  refused, forced-abandoned accepted, forced-typo refused).

  Without an explicit `--runtime`, the verb auto-detects via the shared
  `_auto_detect_run_runtime` helper (which probes `fly-machine.json`
  then `ec2-instance.json` — the same sidecar-probe order `stop`
  uses), falling back to `local` only when neither sidecar is present.
  - **Input validation (all runtimes):** both positionals are checked
    against `^[A-Za-z0-9._-]+$` immediately after parsing, before they
    reach any filesystem path or remote shell. The run-id is interpolated
    into the host state-dir path (traversal risk) and, on Fly, into the
    `flyctl ssh console -C` string; the sid is interpolated into that same
    `-C` string. Since `-C` is parsed by a **remote shell**, an unvalidated
    metacharacter would be a command-injection vector (SECURITY.md); the
    allowlist is the mechanical enforcement (DESIGN §12).
  - **Local path:** runs the mutation program (`python3 -c "$_ab_mutate"`)
    against `$LEERIE_STATE_HOST_DIR/runs/<id>/state.json` directly
    (bind-mounted into containers).
  - **Fly path:** inspects `flyctl machine status`; refuses on
    `destroyed`/missing; if `stopped`, wakes the machine (`flyctl machine
    start` + `wait_for_started`, fatal on failure) and records that it did
    so. Waits for hallpass via `wait_for_fly_ssh_ready "$FLY_APP"
    "$machine_id"` (gated on the return). Pipes the mutation program over
    **stdin** to `python3 -` on the machine (`printf '%s' "$_ab_mutate" |
    flyctl ssh console ... -C "python3 - '<remote-state>' '<sid>'"`) so the
    multi-line script body never round-trips through a shell quoter (same
    idiom as `force-finalize.sh`). The `-C` string itself IS parsed by a
    remote shell, so the two positional args are single-quoted and the
    run-id/sid inside them are the validated tokens above. The program
    prints an `ACCEPTED:` / `NOOP:` / `ERROR:` sentinel that the launcher
    greps (flyctl flattens the remote exit code). The host-side copy is
    mirrored best-effort. Teardown is **conditional**: the machine is
    paused again (with `paused_at`/`pause_reason`/`fly_machine_id`
    re-written via `update_run_json`) only if this verb woke a stopped
    machine — a machine that was already running is left running.
  - **EC2 path:** resolves credentials (`resolve_aws_credentials`,
    `require_aws`) and the instance id via
    `_resolve_ec2_instance_id_from_run_dir`, failing closed with an
    actionable message if `ec2_instance_id` is absent from the sidecar.
    Inspects instance state via `_describe_instance_state`; refuses on
    `terminated`/`shutting-down`/missing; if not `running`, wakes it
    (`resume_instance`, fatal on failure) and records that it did so.
    Pipes the same mutation program over **stdin** to `python3 -` on the
    instance via `ec2_remote_exec` (SSM — no ssh keypair or hallpass wait
    needed, unlike Fly) and greps the same `ACCEPTED:`/`NOOP:`/`ERROR:`
    sentinel. The host-side copy is mirrored best-effort. Teardown is
    **conditional** the same way as Fly: `stop_instance` +
    `paused_at`/`pause_reason`/`ec2_instance_id` re-written via
    `update_run_json`, only if this verb woke a stopped instance.
  - The mutation program validates that the subtask's current status is
    `"blocked"` or `"failed"` before mutating (atomic temp-file +
    `os.replace`). No-ops with a `NOOP:` sentinel if already `"complete"`.
  - **Test coverage:** `tests/test_accept_blocked.py` — local-path tests
    (mutation, no-op, error paths, blocked-dict cleanup), Fly-path tests
    with a stubbed `flyctl` that parses both `-C` positionals and routes
    the stdin-piped `python3 -` to a local fixture, and injection-rejection
    tests asserting a metacharacter-bearing run-id/sid is refused with a
    nonzero exit and no mutation. `tests/test_ec2_launcher_readonly_verbs.py`
    covers the EC2 path: runtime auto-detection over the pre-fix silent
    `local` default, the widened `fly|local|ec2` validator (with a control
    that a genuinely bogus value is still rejected), the accept-record
    mutation landing on both the remote (SSM) state.json and the mirrored
    host copy, the wake/re-pause discipline (and its already-running
    no-op counterpart), and the missing-`ec2_instance_id` fail-closed path.

Maps to `DESIGN.md`: §5 *`requires.extent` — in-graph vs. external
prerequisites*, *Accepting external-blocked subtasks*.

Maps to `DESIGN.md`: §6 *Detached orchestrator (remote mode)*, *The
user-visible verb surface*.

#### Unified `leerie list` (cost column + `status` + `--runtime` filters)

`_list_runs()` in `orchestrator/leerie.py` is extended to surface remote
runs alongside local runs in a single table. Status and runtime are
**orthogonal axes**: status describes lifecycle (`paused`, `killed`,
`done`, `sync-failed`, `in-progress`, `done-pushed-pr`,
`done-pushed-no-pr`, `push-failed`, `pr-failed`, `corrupt-sidecar`,
`seed-failed`); runtime describes where the run executed (`local` or
`fly`). The `seed-failed` status covers run dirs that have a
`fly-machine.json` (launcher wrote it the moment Fly provision
succeeded) but no `state.json` (the orchestrator never wrote one,
typically because `seed_auth` aborted before `phase_classify`).
`_discover_runs()` synthesizes a row dict with `_orphan=True` and
`started_at` from the fly sidecar; `_derive_run_status()` returns
`seed-failed` for them (earliest precedence, before the run.json
corrupt-sidecar check). `resolve_run_id()` accepts orphan ids
transitively (no special-casing needed once `_discover_runs` returns
them), so `./leerie resume <orphan-id> --runtime fly` works against a
seed-failed run. An **explicit** id is exempt from the resumable-status
filter below, so this keeps working; `seed-failed` is excluded from the
bare-`resume` *auto-pick* only (it needs an operator decision first,
and its rows carry no `started_at` to rank by).

**EC2 counterpart (`list`'s Python-layer view, not `_discover_runs()`'s
orphan scan).** `_collect_run_rows()` (below) now tracks an `is_ec2`
axis the same way it tracks `is_fly`, so `--runtime ec2`/`--runtime
local` filter EC2 runs correctly and a plain `list` renders an EC2
run's status column without `LEERIE_FLY_APP` set. This is distinct from
`_discover_runs()`'s orphan scan (DESIGN §6 *EC2 runtime lifecycle*, "Run
identifier"), which is still hardcoded to the literal filename
`fly-machine.json` and has not yet been widened to also check
`ec2-instance.json` for pre-`state.json` orphan discovery — that
remains a separate, not-yet-landed piece of work, and should not
repurpose or rename `fly-machine.json`, which stays exactly as-is for
Fly runs.

Changes:

- `_collect_run_rows()` returns a per-run tuple `(run_id, started_at,
  status, branch, is_fly, cost, is_ec2)`. `is_fly` is a bool derived
  from `fly_machine_id` in `run.json` or a present `fly-machine.json`;
  `is_ec2` is a bool derived from `ec2_instance_id` in `run.json` or a
  present `ec2-instance.json` (mirrors `_auto_detect_run_runtime`'s
  sidecar probe in the launcher). Both are **filter-only** (consumed by
  the `--runtime` filter), never rendered as columns. `is_fly` stays at
  index 4 so existing `r[4]` consumers are unaffected; `is_ec2` is
  appended at index 6, after `cost`. `cost` is the run's aggregate
  `$X.XX` from `state.json`'s `telemetry.cost_usd` (present in the state
  summary `_discover_runs` passes through — no extra disk read), or `—`
  when telemetry is absent (orphans / pre-classify runs).
- `_render_run_table()` renders columns in the order `run_id,
  started_at, status, cost, branch` (the filter-only `is_fly`/`is_ec2`
  are not columns). The `cost` column is right-aligned; widths
  auto-size.
- `status <state>` argparse flag on `list` filters rows to only
  those whose derived status matches. `<state>` accepts any value in
  `RUN_STATUSES` (see list above). Invalid values produce an
  argparse error listing the allowed set.
- `--runtime` on `list` accepts `local`/`fly`/`ec2` (the
  `RUNTIME_VALUES` enum, validated by argparse `choices=`). `fly`
  restricts to rows with Fly artifacts; `ec2` restricts to rows with EC2
  artifacts; `local` restricts to rows with **neither** (not just
  "not fly").
- `list --runtime fly` is intercepted by the launcher (bash) before
  the orchestrator dispatch and queries Fly directly via `flyctl
  machines list --app <FLY_APP> --json`. Renders a `machine_id |
  state | region | created_at | run_id (local)` table covering every
  machine under the app, regardless of which host repo launched them.
  `run_id` is best-effort filled by scanning `<state-root>/runs/*/{fly-
  machine.json,run.json}` for the current repo; machines launched from
  another repo show `run_id=?`. Falls back to the orchestrator-side
  local-sidecar list when `flyctl` is missing or auth fails. Any other
  `list --runtime` value (including `ec2`) falls through unchanged
  into the orchestrator's argparse dispatch above. Plain `list` (no
  `--runtime fly`) is unchanged.

Verbs `kill`, `stop`, and `accept-blocked` accept an optional
`--runtime <local|fly|ec2>` flag — validated by the launcher (bash)
against `local`/`fly`/`ec2`, matching the top-level `RUNTIME_VALUES`
enum (`local|fly|ec2` — see "Remote execution mode" below) that gates
the `--runtime` flag for launching a new run. See "Explicit pause and
destroy verbs" above and "Accept-blocked verb" above for their
respective EC2 paths. `finalize` remains narrower: it still
validates only `local`/`fly` (rejecting `ec2` with an error) since
`finalize --runtime ec2` has not shipped.

The launcher's `RUNTIME=ec2` branch dispatches the full create → seed
→ launch → teardown cycle for *launching* a run (see "Runtime mode"
above); `stop --runtime ec2` routes to `stop_instance()` for pausing
one; and `kill --runtime ec2` routes to `terminate_instance()` with
fetch-before-terminate ordering (see "`kill`'s EC2 action" above).
`finalize --runtime ec2` has not shipped. Fly runs route to `flyctl
machine stop`/`flyctl machine destroy`; EC2 runs route to `aws ec2
stop-instances`/`terminate-instances` (via `stop_instance()`/
`terminate_instance()`); local runs route to `nerdctl stop`/`nerdctl
kill` via the `_is_local_container` probe (`nerdctl inspect
<run-id>`). `stop` uses `nerdctl stop` (SIGTERM first, allowing
graceful state save) or `aws ec2 stop-instances`; `kill` uses
`nerdctl kill` (immediate SIGKILL) or `aws ec2 terminate-instances`
(after the fetch-before-terminate sync). `finalize --runtime local`
still errors — local finalization is inline. Without the flag, the
verbs infer the runtime from the sidecar (`fly-machine.json` presence
for Fly, `ec2-instance.json` presence for EC2, `nerdctl inspect` for
local — in that probe order, via `_auto_detect_run_runtime`).
`resume` accepts `--runtime` directly (the smart router branches by
runtime: fly takes the smart-attach path, local takes the inline
re-exec path).

Maps to `DESIGN.md`: §6 *The user-visible verb surface*.

#### Detached run finalization (`leerie finalize <run-id>`)

With the detached orchestrator, the launcher cannot synchronously wait
for orchestrator completion and call `fetch_branch` — the tail's exec
session ends before (or independent of) the orchestrator's actual exit.
Two surfaces address this together:

1. **`orchestrator.pid` on the machine.** The detached-launch sh wrapper
   records the orchestrator's pid in
   `/work/.leerie/runs/<run-id>/orchestrator.pid` after the post-`Popen`
   poll has cleared the flock-loser case (see the launcher
   `_launch_script` listing above and DESIGN §6 *Single owner per
   run dir*). `leerie resume`'s in-machine tail watcher checks
   liveness via two ORed signals — pid-file `kill -0` and a
   `/proc/[0-9]*/cmdline` scan for `orchestrator/leerie.py` + run-id
   — alongside the `tail -F`. Both must agree the orchestrator is
   dead before the watcher prints
   `<ISO-8601> [leerie] remote: orchestrator exited — syncing run branch + state to host...`.
   The tail then exits. The `/proc` scan is what closes the
   stale-pid contagion described in DESIGN §6: even if the pid file
   went stale (concurrent-spawn race, future cause), the scan finds
   the real orchestrator and the watcher keeps tailing.
2. **`leerie finalize <run-id>`** — new launcher fast-path that runs the
   post-orchestrator block the launcher used to run inline: source
   `fetch-branch.sh`, call `fetch_branch`, source the host-side
   finalize block (push + `gh pr create`). The verb is idempotent — if
   the run branch is already pushed (`pushed_at` set), it short-
   circuits with "already finalized."

**`leerie finalize` resolves the run-id directly.** The launcher
resolves `<run-id>` against `$LEERIE_STATE_HOST_DIR/runs/<run-id>/`
locally to pick up `fly-machine.json` and the partial sidecar. Since
the run-id IS the machine ID (DESIGN §6), no fallback lookup is
needed. No-match falls through to an error augmented with a hint to
run `leerie list`.

**`leerie finalize <run-id>`** (non-force) first tries
`fetch_branch` (the normal clean-exit case: orchestrator wrote
`finished_at`). If that fails, the launcher auto-recovers: it calls
`force_finalize_remote` (which checks whether the orchestrator is dead
and patches `finished_at` — see liveness checks below), then
`collect_subtrees_remote` to integrate un-merged subtask branches on
the machine, then retries `fetch_branch`. If the orchestrator is still
alive, the launcher refuses with a hint to use `--force`.

**`leerie finalize <run-id> --force`** extends the recovery to runs
where the orchestrator is still alive. The launcher calls
`force_finalize_remote` with `FORCE_STOP=1`, which SIGTERMs the
orchestrator process *inside the machine* (the process, NOT the
machine — the machine must stay running for the subsequent collection
and fetch steps), waits for it to die (polling `/proc`; escalates to
SIGKILL after 30 s), patches `finished_at`, then falls through.
The launcher then calls `collect_subtrees_remote` and `fetch_branch`.

**Liveness checks** (`scripts/remote/force-finalize.sh`):

1. Lists `/work/.leerie/runs/` for the single run dir
   (fails clearly on multi-match).
2. Reads `run.json`; if `finished_at` is already set, no-op (idempotent).
3. Checks orchestrator liveness via two complementary signals:
   - `/proc` cross-check (authoritative): scan `/proc/[0-9]*/cmdline`
     for any process whose NUL-separated argv contains both the
     literal string `orchestrator/leerie.py` AND the run-id. If
     found → orchestrator alive → **REFUSE-ALIVE-SCAN** (or
     **STOPPED** if `FORCE_STOP=1`).
   - `orchestrator.pid` check (defensive, kept for pid-reuse audit):
     - Pid file present + `kill -0 <pid>` succeeds + `/proc/<pid>/cmdline`
       contains `python` → orchestrator alive → **REFUSE-ALIVE** (or
       **STOPPED** if `FORCE_STOP=1`). (`cmdline` not `comm`
       because `comm` is the basename of the script-launcher binary —
       for a pip-installed `pytest` shim it is `"pytest"`, which does
       not contain `"python"` and would let an alive orchestrator
       slip through the guard. `cmdline` is the full execve argv,
       which always names the interpreter explicitly.)
     - Pid file present + `kill -0` fails (`ESRCH`) + `/proc` scan
       also empty → orchestrator dead; safe to proceed.
     - Pid file missing → refuse; tell the user to inspect manually
       via `leerie resume <run-id> --shell --runtime fly`.

   The `/proc` scan exists because `orchestrator.pid` is not a
   reliable liveness oracle on its own: the launcher writes it
   *between* `Popen` and the child's `State.__init__`, so a
   stillborn flock-loser stamps its dead pid before the winner can
   claim authority (see DESIGN §6 *Single owner per run dir* —
   stale-pid contagion). The pid-file branch is retained because
   when it speaks (pid-reuse + matching cmdline) it is more precise
   than the scan, and a `REFUSE-ALIVE` distinct from
   `REFUSE-ALIVE-SCAN` makes the source of the refusal observable
   in audit logs.
4. Patches `run.json` in-place with `finished_at = <now>`,
   `no_push = false`, `recovered_at = <now>`,
   `recovered_via = "force-finalize"`, and falls through to the normal
   `fetch_branch` flow.

Sentinels: `OK:<run_id>`, `STOPPED:<run_id>:<pid>` (killed then
patched), `STOP-FAILED:<run_id>:<pid>`, `REFUSE-ALIVE-SCAN:*`,
`REFUSE-ALIVE:*`, `REFUSE-NOPID:*`, `REFUSE-MULTI:*`, `REFUSE-NONE`,
`ERROR:*`.

**Subtree collection** (`scripts/remote/collect-subtrees.sh`):
`collect_subtrees_remote` SSHes a bash payload that discovers
un-integrated subtask branches on the machine and merges them into the
run branch via `setup-run.sh` (idempotent) + `integrate.sh`.
Conflicts are resolved by spawning `claude -p` with the integrator
prompt and schema (same invocation as `integrate_wave()` in the
orchestrator). That direct invocation puts this script **outside** the
`--dangerously-force-strict-output` path: `collect_subtrees_remote` runs only
after the orchestrator — which owns the proxy — has exited, so there is no
listener left to reach. Output is still schema-validated by the script's
embedded `SCHEMAS["integrator"]` copy; it is not constrained during
generation. See DESIGN §7 *Forcing constrained decoding*. The integrator runs in the staging worktree with the
merge left in-progress. On success, the merge commit is verified
(`MERGE_HEAD` must not exist, no staged-but-uncommitted changes). On
failure, the merge is aborted and the branch is skipped. Wave ordering from
`state.json` is used when available (earlier waves first); falls back
to alphabetical. Sentinels: `COLLECTED-ALL:<run_id>:<count>`,
`COLLECTED:<run_id>:<integrated>:<skipped>:<skipped_sids>`,
`COLLECTED-NONE:<run_id>`, `COLLECT-ERROR:<message>`.

The synthesized audit fields (`recovered_at`, `recovered_via`) preserve
provenance of forced recoveries so post-mortems can distinguish them
from naturally-finalized runs.

`finalize` logs the action it took before SSHing in:
`finalize: machine=<id> run=<id> action=<fetch|force-stop+collect+fetch|already-synced>`
so post-mortems of future failures are shorter.

This matches the convention that destructive and side-effecting actions
are explicit verbs (DESIGN §6 *The user-visible verb surface*) rather
than implicit consequences of stream timing.

Optional convenience: `leerie resume <run-id> --auto-finalize`
runs `leerie finalize` automatically when the pid-watch detects
clean exit, for users who want zero-touch finalization when they
happen to be watching. The same plumbing also applies to the
fresh-launch tail (`leerie "task" --runtime fly --auto-finalize`).

Maps to `DESIGN.md`: §6 *Detached orchestrator (remote mode)*,
*Finalization* (recovery sub-paragraph).

#### Chain orchestration (cross-reference)

The chain orchestration code surface is documented in
[**§7 *Chain verbs***](#chain-verbs) earlier in this file (the launcher
verbs, coordinator endpoints, state schema, and worker-side hooks).
DESIGN.md §19 holds the architecture rationale.


---

## 8. Coordination directory layout

State lives under the resolved state root — by default
`$HOME/.leerie/<basename>/`, or the path set via `LEERIE_STATE_DIR` /
`--state-dir` / `leerie.toml state_dir` (see §2 *State directory* for the
full resolution order). The state root is always outside the target repo,
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
        ├── task.md                  the task document verbatim, as plain
        │                            markdown. `_task_ref` in every subtask
        │                            spec points here (N6) — NOT at plan.json,
        │                            which also carries every subtask body and
        │                            so exceeds the CLI's Read cap on a large
        │                            task where this does not
        ├── subtasks/<id>.json       per-subtask spec handed to each implementer
        ├── criteria/<id>.md         informational success-criteria notes (DESIGN §9)
        ├── artifacts/<id>.json      structured deliverables returned by an
        │                            implementer's `artifacts` result field
        │                            (DESIGN §5 *Artifact passing between
        │                            subtasks*). Orchestrator-owned: written
        │                            by `_settle_subtask` on a successful
        │                            `complete` result with non-empty
        │                            `artifacts`, read by `_run_implementer`
        │                            to inject upstream deliverables into the
        │                            prompts of subtasks whose predecessor
        │                            graph names this subtask. Absent for
        │                            code-implementation subtasks.
        ├── checkpoints/<id>.md      handoff checkpoints (7-section schema)
        ├── logs/<sid>.log           per-worker raw stream-json event log (one file
        │                            per claude_p invocation by sid; always written
        │                            regardless of verbosity; append-only across
        │                            handoffs / clarifications)
        ├── worktrees/staging        the run-branch worktree
        ├── worktrees/<id>           per-subtask worktrees
        ├── pending-questions.json   written when clarification needs a non-interactive relay
        ├── pending-clarifications.json  written when an implementer hits a §11
        │                                mid-execution clarification (non-interactive)
        ├── answers.json             written by the plugin skill when relaying
        │                            clarification answers; passed back via --answers
        ├── calls.ndjson             per-run NDJSON telemetry — one JSON object per
        │                            line, one line per claude_p call; opened for
        │                            append at run start; written immediately after
        │                            each call returns (DESIGN §14)
        ├── memory.ndjson            orchestrator memory telemetry — one JSON object
        │                            per line, one line per ~30 s while _orchestrate()
        │                            is alive; written by `_memory_sampler`. Keys per
        │                            line: `ts`, `rss_kb`, `phase` (mirrors
        │                            `state.current_phase`), `worker_count`, `open_fds`
        │                            (from `/proc/self/fd`; `-1` off Linux), `thread_count`
        │                            (from `threading.active_count`). Final sample is
        │                            flushed on sampler cancellation, so the file always
        │                            captures last-known state at orchestrator exit.
        │                            Used to distinguish a natural heavy run from a
        │                            real orchestrator memory leak post-mortem
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

`run.json` fields (a minimal sidecar enabling `leerie list` and resume
discovery without parsing the full `state.json`):

| Field | Shape | Notes |
|-------|-------|-------|
| `run_id` | str | the run identifier (matches the directory name and the branch suffix) |
| `branch` | str | the run branch — always `leerie/runs/<run_id>` |
| `working_branch` | str | the branch HEAD-at-run-start; the diff fork-point (leerie does not merge into it locally). Also the PR base by default — see `pr_base_branch` below for the override. |
| `pr_base_branch` | str | the final branch this run's PR merges into; defaults to `working_branch`, overridable via `--pr-base-branch` / `LEERIE_PR_BASE_BRANCH` / `pr_base_branch` in `leerie.toml` (see "PR base branch override" above). `scripts/host-finalize.sh` reads this field for `gh pr create --base`, falling back to `working_branch` when absent (a run finalized before this field existed). |
| `started_at` | ISO-8601 str | wall-clock start time (also mirrored in `state.json`) |
| `finished_at` | ISO-8601 str \| null | wall-clock end time. Set at finalize success on the normal path; also set by the `except SystemExit` handler in `main()` for `die()` exits that fire after the run directory exists (on Fly, the tail wrapper propagates the orchestrator's exit code via `orchestrator.exit_code` when present, falling back to 0 when absent; either way `fetch_branch`'s discovery script needs `finished_at` to find the run). Idempotent on `resume` — `phase_finalize` overwrites it with the real completion time if the run succeeds on retry. |
| `task` | str | the task description (mirrored from `state.json`) |
| `task_sha256` | str | sha256 of the resolved task text, written at run start. `run_id` is the container id, so two launches of byte-identical task text are otherwise invisible to each other, and can produce architecturally incompatible branches (docs/POSTMORTEM-2026-08-14.md, F10). `_live_duplicate_runs` scans sibling sidecars for the same hash on a run that has not finished, paused or been killed, and `_run_phases` refuses before the first worker. Set `LEERIE_ALLOW_DUPLICATE_TASK=1` to run the same brief twice deliberately — an env var rather than a CLI flag, so it is stated rather than discovered. |
| `pushed_at` | ISO-8601 str \| null | when the run branch was pushed to `origin`; null until push runs |
| `push_error` | str \| null | captured `git push` output if the push failed — stderr plus any pre-push hook **stdout** under a `--- pre-push hook output (stdout) ---` marker, tail-bounded to 32 KiB (see the `scripts/host-finalize.sh` row: the value is a single `jq --arg` argument and cannot approach `MAX_ARG_STRLEN`); mutually exclusive with `pushed_at` being set |
| `pr_url` | str \| null | the PR URL `gh` returned; null until PR creation succeeds |
| `pr_error` | str \| null | captured `gh` stderr if PR creation failed; logical invariant — `pr_error` can be set only after `pushed_at` is set |
| `fly_machine_id` | str \| null | Fly Machine ID for a remote (`--runtime fly`) run; written by `scripts/remote/provision.sh` immediately after `flyctl machine run` succeeds, so a launcher that crashes before classifying still leaves a recoverable pointer. Null for local runs. |
| `paused_at` | ISO-8601 str \| null | when the remote run was paused — either on failure (set by the launcher's EXIT trap on the pause branch) or by explicit user request (`leerie stop <run-id>`). Null for successful runs, killed runs, and runs the user merely detached from. **Cleared at finalize**: `fetch_branch`'s `tar -xC` (scripts/remote/fetch-branch.sh:225) overwrites the host sidecar with the machine's `run.json`, which has no `paused_at` set because the machine isn't aware of the user's pause action. Intentional — the post-finalize status should be `done-pushed-pr`, not `paused`. Pause/resume forensics are not preserved across finalize. |
| `pause_reason` | str \| null | short tag identifying which path set `paused_at` (`worker-error`, `orchestrator-exception`, `finalize-failed`, `user-requested`). Null when `paused_at` is null. Cleared with `paused_at` at finalize (see above). |
| `killed_at` | ISO-8601 str \| null | when the remote run was explicitly destroyed by `leerie kill <run-id>`. The Fly Machine has been destroyed and the run is no longer resumable. Null for any other terminal state. |
| `sync_failed_at` | ISO-8601 str \| null | when the clean-exit branch of `decide_teardown` ran `fetch_branch` and it failed. The orchestrator finished cleanly on the machine, but the run branch + state directory could not be pulled back to the host. The machine is LEFT RUNNING (not stopped) so the user can recover manually via `leerie finalize --runtime fly` (retry sync + push), `leerie resume --runtime fly` (inspect — tails the log by default, `--shell` opens a bash session), or `leerie kill --runtime fly` (destroy only after work is safely on host). Orthogonal to `paused_at`/`pushed_at`/`killed_at` — the machine is neither paused nor destroyed. Mutex-checked against `pushed_at` (a successfully pushed run can't be sync-failed) and `killed_at` (a destroyed machine can't be sync-failed). Requires `fly_machine_id` to be set (the running machine needs a pointer). |
| `sync_fail_reason` | str \| null | short tag accompanying `sync_failed_at` (currently always `sync-failed-on-clean-exit`). Null when `sync_failed_at` is null. |
| `recovered_at` | ISO-8601 str \| null | when `leerie finalize <run-id> --force` patched this run's `finished_at` after the orchestrator died before its natural finalize. Set by `scripts/remote/force-finalize.sh` together with `finished_at` and `no_push=false`. A non-null value means the run reached host-side finalize via the recovery path rather than the natural one. Orthogonal to all terminal-state fields. Written **once** on the first successful `--force` run; subsequent `--force` invocations short-circuit on the now-set `finished_at` and leave `recovered_at` unchanged (the recovery timestamp records the original recovery, not the most recent verb invocation). |
| `recovered_via` | str \| null | short tag accompanying `recovered_at`; currently always `"force-finalize"`. Null when `recovered_at` is null. |
| `volume_id` | str \| null | Fly volume ID (e.g. `vol_…`) when the machine was provisioned with a volume (the default on `--runtime fly` since `FLY_VM_DISK_GB` defaults to `8`). Mounted at `/work` on the machine (the path that holds the seeded repo, `.leerie/runs/<id>/` state, and per-subtask worktrees). Destroyed when the machine is destroyed (clean exit or `leerie kill`). Null for local-runtime runs or legacy Fly runs created before the default was introduced. If non-null, `fly_machine_id` must also be non-null — a volume without a machine to attach it to is invalid (enforced by `_validate_run_json`). |
| `image_tag` | str \| null | Full Fly registry image tag (e.g. `registry.fly.io/leerie:0.6.7`) recorded at provision time. Used by `resume_machine()` to detect version drift: if the current `$FLY_IMAGE_TAG` differs from the stored value (or the stored value is absent), the machine's image is updated via `flyctl machine update --image --skip-start` before starting. Updated in place on successful image update. Null for local-runtime runs or legacy Fly runs provisioned before the field was introduced (legacy machines always get the update on resume since empty != current). |
| `pr_title` | str \| null | LLM-written PR title from the `pr_writer` worker (omits the `leerie: ` prefix — the launcher prepends it before `gh pr create`). Null when the worker errored, was skipped because the user opted out of pushing (`push_will_happen(no_push, host_no_push)` is False — local `--no-push` or Fly `host_no_push=true`), or had not yet run; `host_finalize` uses its deterministic fallback in that case. |
| `pr_body` | str \| null | LLM-written PR body (markdown) from the `pr_writer` worker. Null on the same conditions as `pr_title`. |
| `pr_template_used` | str \| null | repo-relative path of the PR template the worker filled out (e.g. `.github/pull_request_template.md`). Null when the worker produced its no-template default structure. |
| `rebase_disposition_status` | str \| null | set to `"unusable"` by `scripts/host-finalize.sh`'s rebase case statement `*)` fallback arm (reachable when the rebaser python seam returns rc=0 but the JSON is empty, unparseable, or lacks a usable `status` field). Null when the rebase never reached that arm (worktree-add failure, a resolved `rebased`/`irreconcilable`/`failed` status, or no rebase attempted at all). |
| `rebase_disposition_jq_rc` | str \| null | the `jq -e` exit code from attempting to parse `$_rebaser_json` in that same fallback arm — non-zero means the payload itself was unparseable JSON, not merely missing `.status`. Null under the same conditions as `rebase_disposition_status`. |
| `rebase_disposition_raw_json` | str \| null | `$_rebaser_json` (the contents of the seam's verdict file — **not** its stdout, which carries the worker's log stream), **tail**-truncated to 2000 bytes, from the same fallback arm — the artifact that identifies why the rebase degraded. Null under the same conditions as `rebase_disposition_status`. |
| `chain_id` | str \| null | UUID of the chain this run is part of. Written twice: (1) early-write by the child process immediately after `provision_machine` succeeds (so chain-scoped verbs can discover the run while the orchestrator is still running); (2) re-written by the parent's post-wait tagging loop after `fetch_branch` overwrites run.json with the orchestrator's copy. Null for runs not spawned as part of a chain. Used by chain-scoped verbs (`list chains`, `status`, `kill`, `attach`, `resume`) to discover chain runs. |
| `wave_idx` | int \| null | Zero-based wave index within the chain (set alongside `chain_id`). Used by the chain wave-sequencer to group runs by wave for synth-merge between waves. Null when `chain_id` is null. |
| `health` | dict \| null | Advisory run-health signals (DESIGN §9). Written by two seams and merged, never mutually exclusive: (1) `_capture_conformance_baseline` writes `base_suite` `{status: "green"\|"red", red_axes: list[str]}` at the start of `phase_execute` — the build/lint/test exit-code verdict on the unmodified base tree; (2) `_record_run_health` writes `slowest_worker_sid` (str \| null), `slowest_worker_min` (float — the largest summed per-worker `duration_ms`, in minutes), and `truncated_worker_count` (int — worker logs that ended a result with `terminal_reason="max_turns"`) at finalize, preserving any existing `base_suite`. Purely informational — never gates; `_validate_run_json` imposes no invariant on it. Null when neither seam ran (e.g. a no-work run, or `--skip-base-baseline` on a run that also never reached finalize). |

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
| `sync-failed` | `sync_failed_at` set (and no `killed_at`) | the orchestrator finished but `fetch_branch` failed; the Fly machine is still running with un-synced work. Run `leerie finalize <id>` to retry sync + push, or `leerie resume <id>` to inspect manually (default tails the log; `--shell` opens a bash session); only `leerie kill <id>` once work is safely on host. (DESIGN §6 *Remote pause-on-failure* — sync-before-destroy contract.) |
| `done` | `finished_at` set, no `pushed_at` | the user passed `--no-push`, or the orchestrator exited via `die()` after the run directory was created (e.g. unresolved subtasks). In the latter case, `resume` re-enters `phase_execute` normally — `finished_at` is overwritten on success. |
| `paused` | `paused_at` is set | inspect/attach to the Fly Machine, then `leerie resume <id> --runtime fly` (DESIGN §6 *Remote pause-on-failure*) |
| `killed` | `killed_at` is set | terminal state — the machine was destroyed by `leerie kill`. Not resumable; start a new run instead. |
| `in-progress` | none of the above | the run is still active (or died very early); resume with `leerie resume <id>` |

`RUN_STATUSES` in `leerie.py` declares the ten values; a test coupling check asserts the tuple matches every value `_derive_run_status` can return.

`leerie list status <state>` filters the table to runs whose derived status matches. `<state>` accepts any value in `RUN_STATUSES`; invalid values produce an argparse error listing the allowed set. `list` short-circuits before any git/CLI preflight.

`state.json` fields. This table is canonical: every field the orchestrator
writes to `st.data` must appear here, and every field listed here must be
written somewhere in `orchestrator/leerie.py`. The coupling test in
`tests/test_state_fields.py` enforces parity in both directions against the
`STATE_FIELDS` tuple in `leerie.py`.

| Field | Shape | Purpose |
|-------|-------|---------|
| `task` | str | the task description passed on the command line |
| `started_at` | ISO-8601 str | wall-clock time at run start |
| `finished_at` | ISO-8601 str | wall-clock time at successful finalize |
| `plan_snapshot` | dict | `{subtasks, waves}` captured immediately after `_schedule()` returns and **before** `check_budget_feasibility` / `_validate_plan` — both of which `die()`. Without it a plan that fails either gate is lost entirely (`_write_plan` never runs), discarding the planner/fit_judge/splitter spend that produced it. It is deliberately *not* `_write_plan`, which would also emit per-subtask spec files and seed the execution scaffolding for a run that cannot start. `resume` reads this back to rehydrate `subtasks`/`waves` and re-run only the budget check when a run stopped at the post-`_schedule()` budget-feasibility gate (DESIGN §6 "Budget-check resume"), instead of dying "Plans are not persisted." |
| `decompose_snapshot` | dict | `plan_snapshot`'s sibling for §5½ (P1) recursive decomposition: `phase_plan` writes the accumulated leaves after each top-level subtask finishes expanding under `_recursive_decompose`, so a mid-decomposition `WorkerError` (from either the `fit_judge` call or the coupled-minority `splitter` call — an auth failure, PID exhaustion) does not discard fit/split judgments already paid for on subtasks that already finished expanding — decomposition is routinely a large share of a run's total planning spend (DESIGN §6 *Credential strategy*). Since the M2 perf fix (top-level subtasks' `_recursive_decompose` calls run under bounded concurrency, `asyncio.Semaphore(caps["max_parallel"])` + `_gather_or_cancel`, mirroring `_filter_satisfied_subtasks`), completion order across subtasks — and therefore the order of successive snapshot writes — is nondeterministic; the invariant (a finished subtask's leaves are captured before a later crash) is unaffected. Diagnostic/audit only: mid-decomposition rehydration on `resume` is out of scope for the per-phase checkpoint cursor (DESIGN §6 "Resumable planning"), which re-enters at `phase_plan` as a whole via `plans_after_plan` rather than resuming inside a single phase's recursive decomposition. |
| `plans_after_classify` | list[dict] | per-phase planning checkpoint (DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a `waves` gate"): the `plans` list as it stood immediately after `phase_classify` completed and `st.save()`'d. `resume` treats the *presence* of this key — not `current_phase` — as proof the phase's output is safely persisted, and skips re-invoking `phase_classify` when present, reusing this value as the next phase's input. Absent for a run that has not yet completed classification, or has already progressed past this checkpoint to a later `plans_after_*` key / `waves`. Every `plans_after_*` assignment stores a **`copy.deepcopy(plans)`**, never the live list: later phases (`phase_reconcile`'s renames, `phase_overlap_judge`'s merges/drops, both phase-3 soft-drop filters) mutate `plans` **in place**, so storing the reference would make the next `st.save()` retroactively rewrite every earlier checkpoint with the post-mutation plan. |
| `plans_after_plan` | list[dict] | the per-phase planning checkpoint for `phase_plan` (post-recursive-decompose `plans`, DESIGN §6). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_reconcile` | list[dict] | the per-phase planning checkpoint for `phase_reconcile` (reconciled `plans`, DESIGN §6). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_overlap_judge` | list[dict] | the per-phase planning checkpoint for `phase_overlap_judge` (post-collision-resolution `plans`, DESIGN §6 *Cross-domain surface overlap*). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_adherence_gate` | list[dict] | the per-phase planning checkpoint for `phase_adherence_gate` (post-instruction-adherence-gate `plans`, DESIGN §6, §12 sibling). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_coverage_gate` | list[dict] | the per-phase planning checkpoint for `phase_planning_coverage_gate` (post-task-coverage-gate `plans`, DESIGN §8 *Independent adversarial verification*). Same absence/presence and resume-cursor semantics as `plans_after_classify`. |
| `plans_after_filters` | list[dict] | the per-phase planning checkpoint written after the off-tree (`_filter_offtree_subtasks`) and already-satisfied (`_filter_satisfied_subtasks`) phase-3 filters both complete — the filtered `plans` immediately before `_schedule()`. Same absence/presence and resume-cursor semantics as `plans_after_classify`; this is the last `plans_after_*` checkpoint before `plan_snapshot`/`waves` take over as the resume cursor. |
| `satisfied_probe_cache` | dict[str, dict] | per-subtask `satisfied_probe` verdicts (DESIGN §6 "The satisfied-probe sweep needs finer-than-phase granularity"; §8 *Already-satisfied subtask elimination*), keyed by subtask id. Each value is `{satisfied: bool, evidence: str, checked: [str], base_sha: str}` — `base_sha` is the base commit sha (`git rev-parse HEAD`) recorded at probe time. Written by `probe_one` as soon as its own verdict returns, for both `satisfied` and not-satisfied outcomes — not only in aggregate after the whole sweep's `gather` completes, so a pause mid-sweep does not lose already-decided subtasks. **Correctness-critical:** on `resume`, a cached entry whose `base_sha` no longer matches the current `HEAD` is treated as absent and that subtask is re-probed — the base tree can move between a pause and a resume (e.g. a sibling run merging its own PR into the same base branch), and a stale hit could wrongly keep a subtask that is no longer satisfied or drop one that now is. A probe that crashes (`WorkerError`) is deliberately never cached — no verdict was actually reached, and caching "kept" for a crash would wrongly skip re-probing a subtask that was never really judged. |
| `planning_worktree` | str | absolute path to this run's disposable judgment-worker worktree (DESIGN §12 *Judgment-worker isolation*), created and reset by `scripts/planning-worktree.sh` via `_ensure_planning_worktree()`. Every worker in `PLANNING_WORKER_TYPES` runs with this as its `cwd`; `_judgment_cwd()` is the single accessor and raises rather than falling back to the real checkout. Written before `phase_classify` and again immediately before the satisfied-probe sweep (the probe judges whatever tree its cwd points at, so a tree an earlier judgment worker dirtied would produce false `satisfied=true` drops). Deliberately **not** used as a resume skip check — the worktree is a filesystem fact, so it is re-established unconditionally on every entry; the field exists for attribution (which tree a worker's Bash calls landed in), the same argument `leerie_commit` carries. |
| `repo_state_before_planning` | dict | `{head: str, porcelain: [str], refs: [str]}` for the USER'S REAL CHECKOUT, captured once before `phase_classify` and re-checked after every planning phase **and after every execute wave** by `_assert_repo_unchanged()`. The execute-phase call passes `porcelain_only=True`, dropping the HEAD and ref axes: that phase runs for hours (p90 285 min, n=87) and an operator legitimately pulls their own checkout mid-run, so a HEAD-sensitive check there would kill runs over an innocent act. Its blind spot is a gitignored write — `git status --porcelain` never lists `node_modules/`, so in-checkout dependency installs stay invisible. This is the mechanical half of the §12 judgment-worker guarantee: the worktree makes an escape unlikely, this makes it loud, and it `die()`s naming the changed paths/refs and the phase window. Includes untracked files on purpose — `_preflight_repo`'s clean-tree gate filters `??` lines, so a worker *creating* files is exactly what that gate cannot see. Paths under `.leerie/` and refs under `refs/heads/leerie/` are exempt (leerie's own bookkeeping). Persisted so a resume compares against the ORIGINAL baseline rather than a tree an earlier planning pass may already have moved. |
| `active_oauth_token` | str \| None | the raw `CLAUDE_CODE_OAUTH_TOKEN` value currently selected for this run's `claude -p` spawns (DESIGN §6 *Multi-token rotation*; IMPLEMENTATION.md §3 *Multi-token rotation*). Set by `_select_active_oauth_token` (the start-of-run probe/ranking sweep, run only when `CLAUDE_CODE_OAUTH_TOKENS` is present) and mutated by `claude_p`'s mid-run failover on a rate-limited active token. Absent/`None` when only the singular `CLAUDE_CODE_OAUTH_TOKEN` is in play — no rotation, and `_invoke`'s `active_token` param stays `None` (behavior byte-identical to before this feature). **The one sanctioned exception to this feature's secrets-hygiene rule**: the raw token is never written to `calls.ndjson`, `run.json`, or any log line (only its fingerprint is), but `state.json` is local-orchestrator-owned and already carries other operational data, so this field is the one place the raw active token persists at rest. |
| `waves` | list[list[str]] | scheduled subtask ids per wave (from `_schedule`) |
| `completed_waves` | int | index of the next wave to run (resume cursor) |
| `subtask_status` | dict[str, str] | per-subtask terminal status |
| `accepted_blocked` | dict[str, dict] | one entry per subtask waived via `leerie accept-blocked`, written by the LAUNCHER mutator rather than by the orchestrator: `{at, previous_status, blocker, forced}`. The `die()` that sends an operator to that verb says "See ... state.json", and the mutation used to set `complete`, pop the `blocked` registry and write nothing else -- leaving a waived subtask byte-indistinguishable from one that genuinely succeeded, with the blocker it was waived for deleted (docs/POSTMORTEM-2026-08-14.md, F16). `accept-integration` records the same way, on its own `integration_gate` entry (`accepted_at`, `accepted_defects`). |
| `blocked` | dict[str, str] | per-subtask blocker reason when a wave aborts |
| `worker_count` | int | running total of `claude -p` invocations against `max_total_workers` |
| `decompose_worker_count` | int | running total of `claude -p` invocations spent inside `_recursive_decompose` (fit_judge + splitter, including the label-only migration splitter), against `decompose_budget_share * max_total_workers`. Bumped by `_bump_decompose_workers` alongside `worker_count` (which it also bumps). N3+N4: decomposition is a subset of the total worker budget, so this field never exceeds `worker_count` |
| `decompose_share` | float | decomposition's share of the run's realized spend (`decompose_worker_count / worker_count`), recorded by `_warn_decomposition_share` after `phase_plan`'s expansion loop completes. **Not once per run**: each re-plan (adherence/wiring gate feedback) re-enters `phase_plan` and overwrites it with that pass's figure, and a resume past `plans_after_plan` skips it entirely, keeping the last completed pass's value. **Advisory telemetry, never a gate.** The hard gate in `_bump_decompose_workers` is sized against `max_total_workers` and does not fire on observed workloads; this records the figure the 40% was originally derived from — share of *realized* spend — which cannot itself be a live gate, since during planning the denominator is still tiny and the ratio starts near 1.0. Exists so a future threshold can be derived from unbiased data: runs that die during planning never write a `plan.json`, so the leaf-count corpus is survivor-biased |
| `current_phase` | str | the orchestrator's active phase string (e.g. `"phase 2: planning"`, `"phase 4-5: implementing"`); written at each phase entry and read by `_memory_sampler` so each `memory.ndjson` sample can be correlated with the phase that produced it. Empty string before phase 1 fires |
| `telemetry` | dict | calls, cost_usd, input_tokens, output_tokens — printed at run end |
| `categories` | list[str] | classifier output, post-whitelist filtering |
| `classifier_questions` | list[dict] | intent questions the classifier surfaced |
| `prescribed_procedure` | dict | classifier's language→JSON signal declaring whether the user prescribed an explicit procedure/command-sequence: `{is_prescribed, commands, forbid_manual, evidence}`. Empty dict when the classifier omitted the field. `phase_plan` injects this dict verbatim into the planner `ctx_dict` under the same key — but only when `is_prescribed` is true, so a goal-only task carries no false framing — mirroring the conditional `repo_map` injection in the same function (the PREVENT half of the instruction-adherence gate; DESIGN §12 sibling) |
| `required_items` | list[dict] | classifier's language→JSON signal declaring the task's explicit, enumerable requirements: `[{item, source_ref}]`. Empty list when the classifier found nothing genuinely enumerable (the common case). **Nothing gates on it.** It fed `check_required_items_coverage`, the task-coverage gate's PRIMARY deterministic floor, until that floor was deleted on 2026-08-04 for passing 0 of 102 items across every run that ever carried this field — a 100% false-positive rate, and a violation of the *Language-to-JSON* rule since the items are LLM-written sentences. Also injected verbatim into the planner `ctx_dict` under the same key — but only when non-empty, so a task with no enumerable requirements carries no false framing — mirroring the conditional `prescribed_procedure` injection in the same function, since `check_required_items_coverage` cannot be satisfied by a planner that never saw the checklist |
| `likely_already_satisfied` | bool | classifier's additive signal that the task's deliverable already appears present on HEAD (DESIGN §8). Written on every `phase_classify` invocation, default `False`. OR-accumulated (not overwritten) across `phase_classification_gate`'s re-classify rounds within one gate call: a fresh `True` + evidence always wins, but a round that comes back `False`/absent does not clear a prior `True` — only a genuinely fresh contradicting claim with its own evidence overrides it. Consulted by `phase_classification_gate` on retry-loop exhaustion to route to `_finish_no_work_run` instead of `die()` |
| `likely_already_satisfied_evidence` | str | required non-empty whenever `likely_already_satisfied` is `True` (`EMPTY_EVIDENCE` check); default `""` |
| `answers` | dict[str, str] | user answers to classifier questions (and source-of-truth) |
| `artifact_registry` | list[dict] | shared artifact vocabulary (DESIGN §5 *Artifact-registry worker*). Written once by `phase_artifact_registry` after classify, before `phase_plan`; each entry is `{description, tag, path}` — a canonical capability tag + file path for an artifact the task will plainly create. `phase_plan` injects it verbatim into every planner's `ctx_dict` under the same key (only when non-empty), so blind parallel planners prefer the same tag/path and the exact-string `requires`↔`provides` matcher wires the cross-domain edge. Advisory: planners are asked to prefer it, never forced. `[]` is a valid completed state (empty registry / worker degraded), not a resume-redo signal — its own resume checkpoint keys on key presence, mirroring `plans_after_*`. |
| `needs_source_of_truth` | bool | whether classifier asked for source-of-truth disambiguation. **Recorded only, with no readers** — nothing in `orchestrator/`, `chain/`, `scripts/` or `prompts/` consumes it; it survives as an audit record of the classifier's judgement and does not gate delivery of the value. `gather_answers` writes `answers["source_of_truth"]` on every run regardless (DESIGN §11: the question is skipped, never the setting) |
| `source_of_truth_pref` | str | resolved preference (`codebase` / `research` / `both`) |
| `clarify` | bool | whether asking the user is allowed for this run (resolved from `--clarify` / `LEERIE_CLARIFY` / `leerie.toml` / default `False`) |
| `dangerously_skip_permissions` | bool | the operator's tooling escape hatch. Resolved from `--dangerously-skip-permissions` / `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` / `leerie.toml` / default `False`. It no longer grants judgment workers the CLI flag of the same name — that is unreachable for them (DESIGN §12 *Judgment-worker isolation*, L1). When `True` it instead WIDENS their allowlist via `_widen_inspect_tools()` with the leading verbs of the repo's declared build/lint/test commands, so a planner can run `pnpm`/`tsc`/`vitest` without gaining write access. Acting workers carry the CLI flag regardless, from `autonomous=True`. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `dangerously_force_strict_output` | bool | whether this run forced constrained decoding via the per-run loopback proxy (`--dangerously-force-strict-output` / `LEERIE_DANGEROUSLY_FORCE_STRICT_OUTPUT` / `dangerously_force_strict_output` in leerie.toml). Mirrored from `caps["force_strict_output"]`. Recorded because the flag changes worker behaviour invisibly — it owns `ANTHROPIC_BASE_URL`, which makes the CLI treat the session as gateway-routed and apply a conservative client-side context ceiling instead of the model's native window (see `_model_arg`). Originally attribution-only ("without this field a run's failure cannot be attributed to the flag after the fact"); now also load-bearing — `run_rebaser` and `run_recapture_deps` read it back from `st.data` to decide whether to wire their own per-call proxy instance, since they run in a separate process the original CLI flag never reaches (§ *Forced constrained decoding*). |
| `skip_overlap_judge` | bool | whether the phase 2¾ `plan_overlap_judge` worker is suppressed even on multi-planner runs (DESIGN §5 *Cross-domain surface overlap*). Resolved from `--skip-overlap-judge` / `LEERIE_SKIP_OVERLAP_JUDGE` / `leerie.toml` / default `False`. The cheap-skip on single-planner / <2-subtask runs is automatic and not gated by this field — this flag only affects runs where the worker would otherwise fire. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `skip_adherence_check` | bool | whether the instruction-adherence gate (the deterministic prescribed-command-coverage floor + the `adherence_judge` worker in the planner check loop) is suppressed. Resolved from `--skip-adherence-check` / `LEERIE_SKIP_ADHERENCE_CHECK` / `skip_adherence_check` in `leerie.toml` / default `False`. When True, a plan that diverges from an explicitly prescribed procedure is not caught before `phase_execute` spends. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `skip_coverage_check` | bool | whether the phase 2⅞½ task-coverage gate (a single advisory `task_coverage_judge` invocation since 2026-08-04; the deterministic `check_required_items_coverage` floor it used to compose with was deleted) is suppressed. Resolved from `--skip-coverage-check` / `LEERIE_SKIP_COVERAGE_CHECK` / `skip_coverage_check` in `leerie.toml` / default `False`. When True, the review does not run at all — no gap is surfaced before `phase_execute` spends. Since the gate is advisory, this suppresses the report and its worker call, not a block — the escape hatch for a task item the judge counts as `missing_work` when the task itself deferred it, which is unsatisfiable by any planner with no operator override otherwise. Re-resolved fresh on every run, including `resume` |
| `skip_completeness_check` | bool | whether the conformer's gating `solution_defects` completeness axis (DESIGN §9 *The one gating axis: solution completeness*) is demoted to advisory. Resolved from `--skip-completeness-check` / `LEERIE_SKIP_COMPLETENESS_CHECK` / `skip_completeness_check` in `leerie.toml` / default `False`. When True, `_settle_subtask` and `_run_final_conformance` surface found defects as warnings but never re-drive the implementer, block a subtask, or `die()` the final-tree pass — the escape hatch for a hallucinated completeness defect blocking finalize on every `resume`. Re-resolved fresh on every run, including `resume` |
| `skip_integration_check` | bool | whether `integrate_wave`'s `integration_judge` behavioral-defect gate (DESIGN §8 *Independent adversarial verification*) is suppressed entirely — no worker spawn for any subtask in this run. Resolved from `--skip-integration-check` / `LEERIE_SKIP_INTEGRATION_CHECK` / `skip_integration_check` in `leerie.toml` / default `False`. Independent of the accept-integration/audit-key mechanism, which only accepts a finding the judge already produced — this is a full-phase skip, not a per-finding acceptance. `_run_integration_judge_gate` checks it first and logs "integration gate skipped" without invoking the worker when set. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `skip_budget_check` | bool | whether `check_budget_feasibility()` (DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*) is suppressed. Resolved from `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml` / default `False`. The runtime backstop in `State.bump_workers()` is independent of this field — it always fires when the counter actually exceeds `max_total_workers`; this flag only suppresses the *early* die() that catches mathematically-unwinnable runs at the plan/execute boundary. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state. On `resume` the preflight is moot regardless — the resume path enters past `_schedule()` so the check has nothing to gate |
| `skip_satisfied_check` | bool | whether `_filter_satisfied_subtasks()` (DESIGN §8 *Already-satisfied subtask elimination*) is suppressed. Resolved from `--skip-satisfied-check` / `LEERIE_SKIP_SATISFIED_CHECK` / `leerie.toml` / default `False`. When set, no `satisfied_probe` worker spawns and every subtask proceeds to `_schedule()`; the mechanical `check_branch_has_commits` backstop then still catches an already-satisfied subtask post-execution — on a no-commits `complete`, `_settle_subtask` re-probes the criteria against the run-branch HEAD (`_probe_criteria_satisfied_on_head`) and settles it `complete` if met (DESIGN §8 *The mid-run sibling case*), rather than failing it as a retryable no-op. Re-resolved fresh on every run; on `resume` the phase-3 filter is past, so the flag only affects fresh runs. |
| `strict_conformer` | bool | whether the conformer phase is blocking instead of advisory (DESIGN §9 *Post-work conformance*, "Opt-in strict mode" paragraph). Resolved from `--strict-conformer` / `LEERIE_STRICT_CONFORMER` / `leerie.toml` / default `False`. When True, conformer residuals (failed build/lint/test axes or unresolved rule violations) cause the subtask to return `blocked` instead of `complete`; the final-tree pass also blocks the run if residuals remain. The user fixes the residuals and runs `resume`. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `subtask_tests` | str | How much of the repo's suite each per-subtask conformance round measures (DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*). One of `scoped` (default) / `full` / `off`, resolved from `--subtask-tests` / `LEERIE_SUBTASK_TESTS` / `leerie.toml` via `resolve_subtask_tests`. `scoped` uses a diff-scoped proxy for any axis whose template resolves (`test_scoped` / `build_scoped` in `.leerie/config.toml`, else the two narrow inferences in `resolve_blt_scoped`) and the canonical command otherwise — an axis is never silently skipped. A declared template may use `{test_files}` instead of `{files}` on a runner with no impact analysis (pytest); when the diff carries no test file that template renders nothing and the axis falls back to canonical, so the narrowing is never silent. The canonical command always runs at the base-health baseline and on the final integrated tree regardless of this setting, since the final pass exists for cross-subtask interaction breakage that no diff-scoped selection can see. Re-resolved fresh on every run including `resume`, and seeded in BOTH `_run_phases` branches (a resume-only seed is the `skip_coverage_check` defect) |
| `skip_base_baseline` | bool | whether the base-tree health baseline (DESIGN §9 *Base-tree health baseline*) is suppressed. Resolved from `--skip-base-baseline` / `LEERIE_SKIP_BASE_BASELINE` / `leerie.toml` / default `False`. When True, `_capture_conformance_baseline` does not run at the start of `phase_execute`, so no `conformance._baseline` is recorded and the conformer receives no `BASELINE:` context (falling back to self-judging "pre-existing" failures). Skips the once-per-run install-into-staging + full-suite-run cost. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `skip_repo_map` | bool | whether the P6 repo-map structural context (DESIGN §5½ (P6) *Codebase structural map*) is suppressed. Resolved from `--skip-repo-map` / `LEERIE_SKIP_REPO_MAP` / `skip_repo_map` in `leerie.toml` / default `False`. When True, `_build_repo_map()` is not called and the planner/splitter receive no ranked-subgraph injection, degrading gracefully to the prior grep/glob-only planning path. Use on repos where tree-sitter cannot parse the primary language, or to opt out of structural context. Re-resolved fresh on every run, including `resume`, so the user can flip it without editing state |
| `cgroup_containment` | dict | recorded by the fail-closed gate (`_enforce_and_record_cgroup_containment`, in `_run_phases` just before the first worker spawns) (DESIGN §6 *Memory containment*): `{enforced: bool, hierarchy: "v2"\|"v1"\|null}`. `enforced` is the result of the root-broker probe round-trip (create+enroll+destroy of a throwaway cgroup); `hierarchy` is the cgroup version the broker detected. When `enforced` is `False` the run only proceeds if `--dangerously-allow-uncapped` was set (else the gate `die()`s). Persisted so the containment state is visible in `state.json` — the crash that motivated the broker left no artifact of the silent containment failure |
| `verbosity` | str | resolved verbosity level (`quiet` / `normal` / `stream` / `debug`); re-resolved fresh on every run, including `resume`, so the user can dial up or down without editing state |
| `inspect_dirs` | list[str] | extra absolute paths granted to inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) via `--add-dir`. Resolved from `--inspect-dir` / `LEERIE_INSPECT_DIRS` / `inspect_dirs` in `leerie.toml`; re-resolved fresh on every run, including `resume`, so the user can add or remove paths without editing state. Empty list when nothing is configured |
| `integrator_warnings` | dict[str, str] | non-fatal commit warnings from `integrate_wave` (non-fatal signal log) |
| `scope_warnings` | dict[str, dict] | oversized-diff warnings from `check_diff_scope` (non-fatal signal log) |
| `conformance` | dict[str, dict] | per-subtask conformer output and `conformance_warnings` (non-fatal signal log). Keys are subtask ids *or* the literal `_final` sentinel; values are `{result, warnings}` where `result` is the last conformer payload (or null on crash) and `warnings` is the list of advisory strings produced across all conformance rounds. The `_final` entry holds the post-integration whole-tree conformer pass's output (DESIGN §6 *Worktree and integration model*, final-tree pass paragraph); the leading-underscore convention guarantees no collision with subtask ids, which always start with a `<verb>-` prefix per `_ID_PREFIXES`. The per-subtask entries are populated only on subtasks whose implementer reached `status: "complete"`; the `_final` entry is populated whenever `_run_final_conformance` ran (skipped only when the staging worktree or `working_branch` is absent, or on `resume` after the pass already recorded a result). See DESIGN §9 *Post-work conformance* |
| `blt_results` | dict[str, dict] | Per-run memo of orchestrator-measured build/lint/test verdicts (DESIGN §9). Key: `_blt_memo_key(axis, cmd, tree_sha)` = `sha256(axis \| cmd \| tree_sha)[:32]`, where `tree_sha` is `git rev-parse HEAD^{tree}` of the measured worktree — content-addressed, so an empty conformer commit, a rebase, or two worktrees that converge all hit. Value: the axis dict `{ran, measured, passed, command, summary}`. Deliberately carries **no** dependency fingerprint: lockfiles are tracked files and already inside `tree_sha`, and the provision recipe and image are constant within a run. An entry is written only when it describes a reproducible fact — never for a dirty worktree (`_worktree_tree_sha` returns None, meaning neither serve nor store, since `HEAD^{tree}` cannot see uncommitted changes and the conformance phase tolerates them), and never for a crash, a timeout, or a `measured: False` runner-missing result, mirroring `satisfied_probe_cache`'s refusal to cache a crashed probe. Exists because measuring an axis before and after a conformer round would otherwise double the cost: measured, 182 of 224 conformer rounds (81%) committed nothing, so the post-round tree is usually identical to the pre-round one. No eviction: the dict grows one entry per (axis, command, tree sha) for the life of the run, each carrying a ≤400-char summary — a 91-subtask run accumulates a few hundred (~100 KB), re-serialised on every `State.save()`. Bounded in practice by the number of distinct trees a run produces; if that ever stops holding, cap it rather than widening the key |
| `unreviewed_subtasks` | list[str] | subtask ids whose conformer produced no result at all (worker crash, or the 5400 s timeout), so a subtask that was never reviewed is distinguishable from one that passed. Written beside the `conformance` entry, which also gains a `reviewed` bool. Recorded rather than promoted to a blocking status: the phase is advisory by design (DESIGN §9), so an unreviewed subtask is usually fine — what it must not be is invisible. See DESIGN §9 *Post-work conformance* |
| `symptom_findings` | dict[str, list[str]] | subtask id -> `check_symptom_evidence` findings, for subtasks whose plan entry declares `fixes_reported_symptom: true` (NOT those whose id begins `bugfix-`: ids are re-homed by plan merges and synthesised for verification-only work, so the prefix is not evidence a symptom exists) (DESIGN §9 *A stale finding is not a bug*). Written on the success path beside `unreviewed_subtasks`, and cleared for a sid whose later attempt reports cleanly, so a re-driven subtask does not carry a stale entry. Persisted rather than left on the result because `phase_execute` keeps results in memory and writes only `blocked` reasons out of them — and `SYMPTOM_DID_NOT_REPRODUCE` ("this bugfix may be re-fixing something an earlier change already fixed") is precisely what belongs in the run record rather than in a 600 KB log. `phase_finalize` surfaces only that finding, not `NO_SYMPTOM_EVIDENCE`, which is worker hygiene and would fire on most runs until the field is adopted. |
| `provision` | dict | output of `phase_provision` (DESIGN §6½). Keys: `source` (`table` / `llm` / `skipped-docs-only`), `recipe` (list of validated install entries, persisted for worker prompt injection — NOT executed by the orchestrator), `sh_hook_ran` (bool, set by `_run_setup_hook`), `mise_versions` (raw blob from `mise ls --current --json`), `override_file` (absolute path to a synthesized mise override when `phase_provision` had to bridge a polyglot Go repo; `None` otherwise — re-exported as `MISE_OVERRIDE_CONFIG_FILENAMES` on `resume`). Read by `_format_provision_recipe_section()` so implementer/conformer prompts can inject the recipe as a `PROVISION_RECIPE:` advisory block. |
| `external_preconditions` | list[dict] | planner-declared `extent: external` `requires` entries collected during `phase_reconcile` (DESIGN §5 `requires.extent`). Each item is `{tag, reasons: [{sid, reason}, …], originating_subtasks: [sid, …]}`, deduped by tag. Read by `_write_plan()` and persisted as the `preconditions` section of `plan.json`. Empty list when no planner declared any external requirement (the common case). |
| `dropped_subtasks` | dict[str, dict] | subtasks soft-dropped pre-schedule. Two producers, distinguished by shape: `_filter_offtree_subtasks()` drops subtasks whose `files_likely_touched` resolved outside the run's repo root (value `{reasons: [str], files: [str]}`); `_filter_satisfied_subtasks()` (DESIGN §8 *Already-satisfied subtask elimination*) drops subtasks the `satisfied_probe` judged already met on the base tree (value `{reason: "already_satisfied", evidence: str, checked: [str]}`); and the post-execution no-commits re-probe in `_settle_subtask` records a subtask whose criteria are already met on the run-branch HEAD (value `{reason: "already_satisfied_mid_run", evidence: str, checked: [str]}` — same shape, judged against the run-branch HEAD instead of the base tree; DESIGN §8 *The mid-run sibling case*). The `mid_run` label names the moment the rescue fires (post-execution, this run), not the provenance: it covers both a sibling committing the deliverable this run and a subtask already satisfied on the base tree (DESIGN §8 *Scope*). A fourth producer, `_settle_subtask`'s **pre-spawn** probe of a `provider_subset_sids`-flagged subtask, records the same shape with `reason: "already_satisfied_pre_spawn"` (DESIGN §8 *Probing a flagged subtask before it spends*) — same verdict as the mid-run rescue, reached before any implementer ran, and kept distinct so the audit shows which settlements cost a probe and which cost a worker first. Absent when no drop fired. Audit trail only — the run proceeds with the surviving subtasks; no orchestrator code reads back from this field. |
| `provider_subset_sids` | list[str] | sids flagged at plan time by `_warn_provider_subset_subtasks()` — every file in the subtask's `files_likely_touched` is already owned by an ordered predecessor it depends on (DESIGN §5 *Provider-subset subtasks*). Still advisory and never a drop, but persisted rather than only logged because `_settle_subtask` reads it: a flagged subtask gets one read-only `satisfied_probe` against the run-branch HEAD **before** its implementer is spawned (DESIGN §8 *Probing a flagged subtask before it spends*), so a redundancy that only became real when the predecessor committed costs a probe instead of a full implementer. Empty list when nothing was flagged. |
| `conditional_drops` | dict[str, dict] | planner-emitted consumer subtasks dropped by the reconciler's `conditional_drop` resolution op (DESIGN §5) — i.e. the planner authored the subtask as "no-op if X" and X turned out to be unresolvable. Each value is `{reason: str, from_unresolved_tag: str}` where `reason` quotes the consumer's conditional intent + names why the precondition is false (the reconciler emits this) and `from_unresolved_tag` records which unresolved tag's resolution motivated the drop (looked up from the unresolved set at apply time). Absent when no conditional_drop fired. Distinct audit field from `dropped_subtasks` (off-tree soft drops, phase 3) so the two causes stay separately auditable. |
| `external_twin_demotions` | list[dict] | `unresolvable` entries rescued by `_demote_unresolvable_with_external_twin` (DESIGN §5 *The external twin*) — a consumer declared a tag `in_plan` while another subtask declared the same capability `extent: external`. Each item is `{sid, tag, match: "exact"｜"singularized", twin_tag, twin_subtasks}`. Recorded so a wrong singularized pairing is auditable rather than a silent reshaping of the dependency graph. Absent when no demotion fired. |
| `speculative_collapse_drops` | list[str] | subtask sids mechanically pruned by dead-subtask elimination (DESIGN §5) — fully-speculative subtasks whose every `in_plan` requires was unresolvable because the provider domain returned 0 subtasks. Recorded before `_check_unresolvable` runs so the audit trail survives even when `die()` fires for remaining unresolvable entries. Absent when no dead-subtask elimination fired. Distinct from `conditional_drops` (LLM-judged, based on conditional prose in intent) and `dropped_subtasks` (off-tree soft drops, phase 3). |
| `overlap_replan_done` | bool | set once when `phase_overlap_judge` answers an `unresolvable` collision with a **scoped re-plan** instead of `die()`ing (DESIGN §5). Written **after** `check_replan_affordable` passes, never before: the flag records an ATTEMPT, not an intention. Setting it first persisted it while the `plans_after_overlap_judge` checkpoint was never written, so `resume --max-workers N` — the remedy that die() recommends — re-entered the gate, saw the flag and died immediately without ever attempting the re-plan the raised budget afforded. Pinned by `tests/test_scoped_replan.py::test_budget_die_does_not_consume_the_recovery`. Bounds that recovery to a single attempt: a second unresolvable verdict after re-planning dies, since the contradiction is then not something re-planning resolves. Absent on runs that never hit an unresolvable collision — which is 88% of runs reaching the judge (5 of 43 hit one). |
| `plan_overlap_judge` | dict | full output of the phase 2¾ `plan_overlap_judge` worker (DESIGN §5 *Cross-domain surface overlap*) — `{collisions: [{a_sid, b_sid, artifact, resolution, reason, merge_feasibility?}, …]}`. Persisted before the apply step (so if a `die()` fires on `unresolvable` or the merge-feasibility backstop the audit record survives). Absent when `phase_overlap_judge` cheap-skipped (single-planner / <2-subtask runs / `--skip-overlap-judge`) or when the judge returned `{collisions: []}`. |
| `plan_overlap_applied` | list[dict] | post-apply mutation summary for the phase 2¾ judge. Each entry is either `{action: merge|drop_a|drop_b, artifact: str, surviving_sid: str, dropped_sid: str, reason: str}` recording a mutation against the plan, or `{action: skipped_redundant, artifact: str, collapsed_to: str, original_a_sid: str, original_b_sid: str, merge_feasibility: str, reason: str}` recording a redundant pair whose endpoints had already collapsed to the same survivor via an earlier resolution (the closing edge of a connected cluster — kept in the audit trail so resume-time inspection sees every collision the judge emitted). The anchor-survivor rule may make the `surviving_sid` differ from `_apply_overlap_merge`'s default lex-smaller pick when the merge participates in a cluster — see "Phase 2¾ checks" above. Useful for resume-time replay debugging — `state.data["plan_overlap_judge"]` records what the judge said, this records what the orchestrator did. Empty list when the judge returned no collisions; absent when the phase cheap-skipped. |
| `duplicate_provider_merge_applied` | list[dict] | post-apply mutation summary for merges the deterministic `check_duplicate_providers` floor synthesized and applied via `_duplicate_provider_merge_collisions` + `_apply_overlap_collisions` (M11 DECISION — see "Phase 2¾ checks" above). Same entry shapes as `plan_overlap_applied` (`merge` / `skipped_redundant` / `skipped_would_cycle`), but independent of it: this key is written even on paths where `plan_overlap_judge` itself never ran (single-planner plans, `--skip-overlap-judge`). Absent when the floor found nothing to merge. |
| `adherence_gate` | dict | audit record from the phase 2⅞ instruction-adherence gate (`phase_adherence_gate` — see "Instruction-adherence gate" above) — `{judge: <adherence_judge output>, floor_issues: list[str]}`. Written once the gate clears (either immediately, or after re-planning). Absent when the gate cheap-skipped (`skip_adherence_check` / no prescribed procedure) or when the judge crashed every round (the degrade path returns without persisting this key). |
| `coverage_gate` | dict | audit record from the phase 2⅞½ task-coverage gate (`phase_planning_coverage_gate`, DESIGN §8 *Independent adversarial verification*) — the final `task_coverage_judge` output `{task_covered, coverage_gaps, rationale}`. Written once, immediately after the gate's single direct `task_coverage_judge` invocation — the gate is advisory and never re-plans. Absent when that invocation raised `WorkerError` or an `OSError` from process spawn (the degrade path returns without persisting this key); any other exception propagates rather than degrading. Replaces the planner's self-graded `task_understanding` confidence axis, which no longer gates in `check_planner_output`. |
| `classification_coverage_gate` | dict | audit record from `phase_classification_gate` (DESIGN §8 *Independent adversarial verification*) — the final `classification_judge` output `{categories_reviewed, miscategorizations, rationale}`. Written once the gate clears (immediately, or after re-classifying). Absent when the judge crashed every round (degrade path returns without persisting). |
| `wiring_gate` | dict | audit record from `phase_wiring_gate` (DESIGN §5 *A wiring re-check on the fully-merged plan*, §8) — the final `wiring_judge` output `{plan_reviewed, wiring_defects, rationale}` plus a `repairs` array of `{sid, tag, provider, channel}` for every edge the gate added (`channel` is `"tag"`, `"id"`, or `"cofile_cluster"`; on the id channel `tag` and `provider` are both the named subtask id). Single pass (no `make_feedback_prompt` — see §5½ *Mechanical-feedback loops*): the judge's defects are passed through `_repair_missing_requires` and only the unrepaired residual `die()`s. Written only when the gate CLEARS (no residual), which is also what makes it the correct resume key — `plan_snapshot` is written before the gate runs and is therefore present even on a run the gate killed, so keying the skip on it silently bypassed a failed gate. `repairs` is `[]` on a plan that needed none. Absent when the judge crashed every round, or when the gate died. The deterministic `check_plan_wiring` that runs alongside it does not persist — it `die()`s or passes silently. |
| `provision_recipe_gate` | dict | audit record from `phase_provision_gate` (DESIGN §8, §6½) — the final `provision_judge` output `{recipe_reviewed, recipe_failures, rationale}`. Detect-and-die, single pass (no `make_feedback_prompt`): written once the judge's first round clears; a found recipe failure `die()`s immediately instead of re-provisioning. Absent when no recipe was detected (`kind: none`) or the judge crashed every round. |
| `integration_gate` | dict[str, dict] | per-sid audit record from `integrate_wave`'s `integration_judge` gate — `{sid: {defects: list[str], advisories: list[str], merge_commit_sha: str, accepted: bool}}`. Unlike `wiring_gate`, written BEFORE `die()`ing, not only on a clean pass — `accepted` is `not defects` on a fresh verdict (True for clean, False for a gating finding) and is flipped to `True` by `leerie accept-integration <run-id> <sid>`. `integrate_wave` consults this key BEFORE re-driving `integrate.sh`/the integrator for a sid: a present, not-yet-`accepted` entry re-invokes the judge directly against the already-committed merge (`integrate.sh` alone is idempotent and would just see the branch already merged, short-circuiting past the judge entirely); a present, `accepted` entry skips straight to `integrated.append`. Absent for a sid whose merge never needed the integrator (the judge only runs post-integrator-merge, on a conflict) and for a sid the judge has never reviewed at all. |
| `integration_defects` | dict[str, list[str]] | per-sid flat mirror of `integration_gate[sid]["defects"]` for the sids with a currently-gating (not-yet-accepted) finding — the record `accept-integration` clears (popped, along with the whole key when it empties, once accepted or once a re-invoked judge comes back clean). Kept as a separate key alongside `integration_gate` per the audit-key contract this field pair was specified against. Absent when no sid currently has a gating defect. |
| `no_work_required` | bool | set to `True` by `_finish_no_work_run` when every planner returns `status: "ready"` with `subtasks: []` (DESIGN §8 *The cleared-but-empty terminal state*). When `True`, the orchestrator wrote `finished_at`, skipped phases 3–6, and exited 0 — the task was already satisfied on HEAD, no run branch was materialized, no PR will be opened. `leerie list` renders the run as `done` (no push, no PR, distinct from `done-pushed-no-pr` and `done-pushed-pr`). Absent on every normal run. |
| `no_work_reasons` | dict[str, str] | per-domain `confidence.basis` quoted from each planner's empty-but-ready output, recorded alongside `no_work_required` for audit. Keys are domain names (e.g. `"bug-fixing"`, `"testing"`); values are the `basis` string the planner emitted explaining why no work was needed. Absent on every normal run. |
| `working_branch` | str | the user's branch at the moment `phase_classify` runs (`git rev-parse --abbrev-ref HEAD`). Captured once and mirrored to three locations: `run.json.working_branch`, `<state-root>/runs/<id>/working-branch` (written later by `setup-run.sh`), and `state.json` via this field. Read by `_compose_pr_via_llm` as the `git diff` base for the PR-writer payload and by `_run_final_conformance` as the `DIFF_BASE` for the post-integration whole-tree pass. Empty string when the host `git` invocation failed (interactive fallback path); the readers tolerate this. |
| `pr_base_branch` | str | the final branch this run's PR merges into — overridable via `--pr-base-branch` / `LEERIE_PR_BASE_BRANCH` / `pr_base_branch` in `leerie.toml` (resolved by `resolve_pr_base_branch`, CLI > env > file precedence, mirroring `resolve_pr_template`). Defaults to `working_branch` when unset (`resolve_pr_base_branch(...) or working_branch`, computed once at run start alongside `working_branch`). Mirrored to `run.json.pr_base_branch`. This is the PR base ONLY — never the diff fork-point, which stays `working_branch` (`rev_range = working_branch..run_branch`; `_run_final_conformance`'s `DIFF_BASE`); overloading `working_branch` for both roles would corrupt the diff base if the override branch isn't the actual fork point. |
| `leerie_version` | str | the leerie version string from `.claude-plugin/plugin.json`, seeded once at the run's original start and **immutable across resumes** (N38) — a resume no longer overwrites it with whatever is installed at resume time, since doing so made a resumed run's failures read as attributable to the wrong release. Persisted so the PR footer and Run metadata block can show the exact version that produced the run. |
| `leerie_commit` | str \| null | short sha of `$LEERIE_REPO`'s HEAD, forwarded by the launcher as `LEERIE_COMMIT`, seeded once at the run's original start and **immutable across resumes** (N38), same rationale as `leerie_version`. `null` when leerie was installed from a tarball rather than a git checkout — a normal state, never an error (the launcher's `rev-parse` is local-only and its failure can never fail a run). Recorded because `leerie_version` alone cannot attribute a run: `plugin.json` only moves on a `chore(release):` commit while `install.sh` tracks `main` (`DEFAULT_REF`), so every run between releases reports the same version whether or not it carries a given fix. Rendered beside the version in the PR footer / Run-metadata block as `v0.11.1 (abc1234)`. |
| `leerie_versions` | list[dict] | append-only resume history (N38), distinct from the immutable `leerie_version`/`leerie_commit` pair above. Seeded as a one-entry list at run start and gets one `{version, commit, at}` entry appended on every `resume` — the actual install seen at that moment, so a failure that only reproduces after an install upgrade between resumes is still attributable. |
| `dep_capture_done` | bool | set to `True` in `state.json` by `capture_repo_deps` after a successful write. Combined with the sibling sentinel file `<run_dir>/dep_capture.done`, this makes the next-run backstop idempotent: the backstop skips runs whose sentinel file is present, and the cancel-arm capture skips already-captured runs. Absent on runs where capture was skipped or has not yet run. |

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
unknowns*. `_validate_checkpoint()` enforces three layers: (a) every section
header must be present; (b) every section must carry non-whitespace content; (c)
the five "must carry handoff context" sections reject single-token
placeholder content (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`) — the two
"nothing-to-report-is-OK" sections (*Decisions made*, *Open unknowns*)
accept these. Trailing punctuation (`.`/`!`/`?`/`…`) is stripped before
the comparison and repeated `?` is collapsed, so `None.`, `TBD!`, and
`???` are caught alongside the bare tokens. When a `worktree_root` is passed, `_validate_checkpoint()`
also runs a freshness check: every path listed under *Files touched* must
either still exist in the worktree or carry a `[deleted]` annotation,
catching stale checkpoints whose paths were removed by partial work after
the snapshot was written.

`claude_p()`'s CLI-reported `num_turns` is unreliable as a comparison against
`--max-turns` — the CLI computes it from two different counters depending on
whether the cap-enforcement path or the success path was taken, and only one
is commensurable with the flag. The `terminal_reason` the CLI reports is the
trustworthy cap signal; `num_turns` is still printed alongside it but never
compared against the cap. `tests/test_turn_cap_signal.py` enforces that no
code re-introduces the comparison.

Maps to `DESIGN.md`: §10 (handoff, coordination-artifact location), §9 (criteria
locking).

---

## 9. Structured-output schemas

`claude_p()` validates each worker's payload against a schema keyed by worker
type. `confidence` is optional on every worker schema (declared in
`properties`, not required — required object shape would corrupt payloads
under anthropics/claude-code#49747) but when present follows
`_confidence_schema(...)`: axis score(s) 1–10, `basis` (string, required),
`falsifiers_tested` / `contradictions_reconciled` (arrays of strings,
optional). There is no `gap_to_close` field; a low score's gap is stated in
`basis`.

Required fields, current shape:

- **classifier** — required: `categories` (array). Optional: `questions`
  (array of `{id, question, why_underivable?}` — only `id`/`question`
  required), `source_of_truth_question` (bool — the classifier only flags
  relevance; the orchestrator's preference resolution supplies the value,
  default `both`). Optional: `prescribed_procedure`
  (`{is_prescribed (bool), commands (array of strings), forbid_manual (bool),
  evidence (string)}`) — a language→JSON signal for whether the task
  prescribes an explicit procedure vs. a goal description;
  `check_classifier_output` enforces non-empty `evidence` when
  `is_prescribed` is true (`EMPTY_EVIDENCE`). `phase_classify` persists it to
  `st.data["prescribed_procedure"]` (default `{}`). Optional:
  `likely_already_satisfied` (bool), `likely_already_satisfied_evidence`
  (string, required non-empty when the bool is true) — signals the task's
  deliverable already appears present on HEAD (DESIGN §8); OR-preserved
  across re-classify rounds within `phase_classification_gate` so a later
  round's silence never clears an earlier round's true finding.
- **planner** — required: `domain`, `subtasks`, `status` (enum `ready` /
  `blocked` — DESIGN §8 planner gate). A `ready` plan may carry an empty
  `subtasks` list (the cleared-but-empty terminal state). `confidence`
  required keys when present: `task_understanding` (1–10),
  `decomposition_quality` (1–10), `basis`. Each subtask:
  `{id, title, success_criteria_seed (all required), intent, scope_note,
  files_likely_touched, depends_on, requires, provides, size,
  investigation_notes, runs_commands}`. `requires` is an array of
  `{tag (required string), extent (required enum: "in_plan" | "external"),
  reason (string, required and non-empty when extent == "external")}`.
  `extent: in_plan` is satisfied by another subtask's `provides` (a graph
  edge); `extent: external` is a planner-declared prerequisite outside *this
  run's* graph — either outside the build graph entirely (another repo, ops
  runbook, manual step), or producible by code but owned by another run the
  task names (sibling phase document, earlier phase), or fenced off by the
  task itself (the task declares a surface out of scope and the capability's
  only implementation site lies on it) — and surfaces in `plan.json` as a
  `preconditions` entry. In every case `reason` must name the owner; the
  discriminating test is "is it in this run's graph?", not "could any code
  produce it?". See DESIGN §5 `requires.extent`.
  `provides` is an array of bare strings. `size` is an enum `small` /
  `medium` / `large` — `large` triggers the size-resolution retry loop and,
  if it survives, `_validate_plan` dies with an OVERSIZED error. `runs_commands`
  (array of strings, optional) declares every command a subtask actually
  invokes — structured data feeding
  `check_prescribed_command_coverage(prescribed_procedure, subtasks) ->
  list[str]`, the deterministic PRIMARY layer of the instruction-adherence
  gate: `prescribed.commands − ⋃(subtask.runs_commands)` under normalized
  (lowercased, stopword-filtered) token-subset matching, returning a
  `PRESCRIBED_CMD_UNRUN: ...` string per uncovered command; short-circuits to
  `[]` when `prescribed_procedure` is absent/falsy/empty. Tested in
  `tests/test_prescribed_cmd_coverage.py` and (advisory-vs-gating outcome)
  `tests/test_check_functions.py::TestAdherenceGateAdvisoryVsGating`. Wired
  into `phase_adherence_gate` (see "Instruction-adherence gate" above).
- **implementer** — required: `subtask_id`, `status` (`complete` /
  `incomplete-handoff` / `blocked` / `failed` / `needs-clarification`).
  `confidence` shape when present: `root_cause`, `solution` (1–10), `basis`.
  Optional: `branch`, `criteria_results` (array of `{criterion, met,
  evidence}` — recorded for telemetry, does not gate), `checkpoint_path`,
  `blocker`, `summary`, `clarification_question` (DESIGN §11 mid-execution
  exception: `{id, question, why_underivable}`, all three required when
  present, requires `checkpoint_path` too), `artifacts` (DESIGN §5 *Artifact
  passing between subtasks*: array of `{name, kind (enum "markdown" | "json"
  | "text"), content, summary?}` — structured deliverables for downstream
  subtasks).
- **integrator** — required: `incoming_subtask`, `status` (`resolved` /
  `design-conflict` / `failed`). `confidence` shape when present:
  `_confidence_schema(["resolution"])`. Optional: `resolution_summary`,
  `diagnosis` (fallback for `resolution_summary` on a non-`resolved`
  outcome).
- **rebaser** — required: `status` (`rebased` / `irreconcilable` / `failed`),
  `final_branch_state`. `confidence` shape when present:
  `_confidence_schema(["resolution"])`, mirroring `integrator`. Optional:
  `resolution_summary`, `diagnosis` (required in practice when `status` is
  `irreconcilable`). DESIGN §6 *Finalization* "Rebase-onto-base before push":
  a scoped, fully-agentic exception to §12 — the worker performs the whole
  rebase workflow itself. `check_rebaser_worktree_state()` mechanically
  re-verifies the claimed `status` against the worktree's actual git state
  before `run_rebaser()` returns it.
- **conformer** — required: `subtask_id`, `rules_files_read` (array of
  strings, empty when none found), `rule_violations` (array of `{status:
  enum[fixed, residual], rule, fix, evidence, why_not_fixed}` — `status`
  discriminates which optional fields are populated), `file_updates` (array
  of `{kind: enum[docs, tests], path, reason}`), `build`, `lint`, `tests`
  (each `{ran (bool), passed (bool), command (string), summary (optional)}`
  — `ran: false` when not applicable to the repo), `summary`, and
  `solution_defects` (array of `{kind: enum[unhandled_input, unhandled_path,
  missing_guard, sibling_site_unedited, wrong_selector, decoy_or_shortcut],
  concrete_case (minLength 1), where (minLength 1), why_ships_a_defect
  (minLength 1)}`). `solution_defects` is the **gating** axis (DESIGN §9 *The
  one gating axis: solution completeness*) — the conformer's independent
  adversarial attack on the implementer's committed diff; non-empty retries
  the implementer with the defects folded in as mandatory criteria (bounded
  by `completeness_retry_rounds`), or blocks on exhaustion.

  `rule_violations`/`file_updates` are wire-flattened discriminated arrays
  (mirroring `SCHEMAS["reconciler"]`'s `tag_ops` technique) rather than four
  separate arrays, to keep the schema small enough for the strict-output
  proxy's grammar compiler. `_expand_conformer_output()` fans the wire shape
  back into the four original arrays (`rule_violations_fixed`,
  `rule_violations_residual`, `docs_updates`, `tests_updates`) immediately
  after the worker call, at both call sites; an entry with an unrecognised
  `status`/`kind` is dropped. Pinned by `tests/test_conformer_schema_shrink.py`.

  Cross-field invariants enforced by `_validate_conformance_result()` against
  the expanded shape: residuals require non-empty `rules_files_read`, every
  `rule_violations_fixed` item cites a non-empty `rule`, every
  `docs_updates`/`tests_updates` `path` exists in the worktree, and every
  `solution_defects` item carries non-empty `concrete_case` and `where`.
  `confidence` shape when present: `conformance` (1–10), `basis`.
- **judge** — required: `passed` (bool, true only when all three dimensions
  are true), `dimensions` (`{schema_ok, factual_ok, hallucination_ok}`,
  booleans), `rationale` (1–3 sentences), `suggested_fixes` (array of
  strings, empty when `passed: true`). Used by `phase_judge()` /
  `_judge_capture()` — post-run, not the main workflow. `prompts/judge.md`
  carries the rubric.
- **patch_generator** — required: `anchor` (exact substring of the current
  system prompt the patch replaces — validated against the live prompt text
  before applying), `replacement`. Optional: `strategy`, `pivot_reason` (str
  | null). Used by the self-heal skill's patch-generation worker; post-run,
  not the main `claude_p()` loop.

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
prints:

- a header (status, duration, `state.json`'s `telemetry` aggregate — calls,
  `$cost`, in/out tokens);
- a per-`call_type` breakdown from `calls.ndjson` — count, input/output
  tokens, average latency, failure count — sorted by call count descending
  (`_aggregate_calls`);
- a `failures by kind` rollup of `failure_kind` values, when any failed; and
- a memory-peak line (peak `rss_kb`, max `open_fds`/`thread_count`) from
  `memory.ndjson`, via `_memory_peak`.

All inputs already exist on disk; `--report` adds no new telemetry.

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

**Tested.** A pytest suite under `tests/` exercises the deterministic
enforcement functions. No coverage target is set. Selected files, grouped by
subsystem:

**Core resolvers and validation:** `test_resolve_leerie_root.py`,
`test_resolve_source_of_truth.py`, `test_resolve_runtime.py`,
`test_resolve_models.py`, `test_resolve_dep_capture_model.py`,
`test__read_toml_key.py`, `test_gather_answers_validation.py`,
`test_retryable_failure.py`, `test_state_fields.py` (`STATE_FIELDS` parity
against both the §8 field table and every `st.data[...]` write —
the mechanism §8's "this table is canonical" claim relies on),
`test_blocked_clear_on_complete.py`, `test_validate_plan.py`,
`test_validate_result.py`, `test_check_merge_committed.py`.

**Planning — P6 repo map, P1 recursive decomposition:**
`test_rank_repo_map.py`, `test_repo_map.py`, `test_build_repo_map.py`
(HAS_TREESITTER-gated), `test_tree_sitter_probe.py`,
`test_resolve_fit_judge_model.py`, `test_resolve_fit_judge_splitter_model.py`,
`test_fit_judge_schema.py`, `test_splitter_schema.py`,
`test_recursive_decompose.py` (`_partition_files()` coverage/overlap
invariants; `_recursive_decompose()` leaf/split/depth-cap/no-progress
behavior; migration-path label-only splitter use; `claude_p` full-signature
call sites), `test_phase_plan_repo_map_ctx.py`,
`test_phase_plan_prescribed_procedure_ctx.py`,
`test_phase_plan_recursion_wiring.py`, `test_recursive_decompose_parallel.py`
(bounded-concurrency expansion loop, `decompose_snapshot` per-completion
persistence).

**Worker isolation, prompts, tool scoping:** `test_inspect_tools.py`
(`INSPECT_TOOLS` grants `Bash(<verb>:*)` patterns but never `Write`/`Edit`/bare
`Bash`), `test_resolve_inspect_dirs.py`, `test_resolve_prompt.py`,
`test_judgment_worker_isolation.py` (judgment workers never receive
`--dangerously-skip-permissions`; `claude_p` refuses a judgment-worker cwd
resolving to `st.repo_root`), `test_work_sentinel.py` (HEAD/porcelain/refs
snapshot before/after planning phases), `test_planning_worktree_script.py`,
`test_ensure_planning_worktree.py`.

**Orchestrator wiring:** `test_orchestrate_call_sites.py` (source-text
coupling guards for `_run_phases`/`_settle_subtask` call ordering),
`test_claude_p_call_sites.py` (every `claude_p` call site statically checked
against the real signature — all-keyword, every required param present, no
unknown keyword, `model=` never a defaultless `.get()`), `test_no_dead_functions.py`,
`test_no_undefined_names.py` (whole-module `symtable` scan for undefined
names — ruff F821 without the dependency).

**Conformance (DESIGN §9):** `test_discover_rules_files.py`,
`test_validate_conformance_result.py`, `test_run_conformance_phase.py`,
`test_run_final_conformance.py`, `test_infer_build_lint_test.py`,
`test_conformance_clean_delta.py` (`_conformance_clean` / `_baseline_red_axes`
delta-not-verdict discipline), `test_measure_blt.py`,
`test_ensure_worktree_deps.py`, `test_blt_memo.py` (memo hit issues zero
subprocess calls), `test_scoped_axes.py`, `test_orchestrator_owns_blt.py`,
`test_round_axis_regressions.py`, `test_resolve_blt.py`, and the three
`{test_files}`-proxy files detailed below.

The `{test_files}`-proxy files carry the per-file detail a reader scans to
find what is already covered:

| Test file | What it covers |
|---|---|
| `test_test_files_proxy.py` | `_is_test_file` / `_render_scoped`'s `{test_files}` tier / `_select_subtask_axes`' fallback (DESIGN §9). The load-bearing case is the empty-AFTER-filter one: `files` is NON-empty so the pre-existing empty-list guard does not fire, yet every member is a non-test path — rendering there yields a bare `pytest`, which runs EVERYTHING, the same inversion the `{files}` rule forbids reached by a different route. Also pins that the shipped vitest/jest `{files}` templates take SOURCE files on purpose, the `lstrip("./")`-vs-`removeprefix` case, and declared `test_file_globs` REPLACING rather than extending the built-ins. |
| `test_scoped_proxy_corpus.py` | The measured basis for the `{test_files}` tier, frozen against `tests/fixtures/scoped_proxy_corpus/corpus.json` — 36 REAL per-subtask diffs recovered from leerie's own run branches. Exists because the ratio was first taken from the planner's `files_likely_touched` and was badly wrong (40% test-touching predicted vs 94% real). Each row must be ONE subtask's work (an integration merge's FIRST-PARENT diff, not a cumulative two-dot diff), and the fixture must retain its source-only rows or the canonical-fallback safety property goes untested. |
| `test_scoped_degrade_warning.py` | `_warn_scoped_degraded_once` (DESIGN §9): `scoped` is the default and an unresolvable proxy falls back to canonical, so a pytest repo paid the full oracle once per subtask with nothing saying so. The anti-vacuity partner `test_silent_when_a_proxy_resolves` is mandatory: without it a warning that fired unconditionally would pass, turning the signal into noise on the ~99% of repos where scoping works. Two wiring guards — the call precedes the baseline block, and an AST check that it is NOT nested under the `skip_base_baseline` guard (sentinel-skipped on resume, i.e. silent on exactly the runs that most need telling). |

**Container image / provisioning:** `test_resolve_repo_image_tag.py`,
`test_launcher_cache_mounts.py`, `test_launcher_per_repo_image.py`,
`test_dockerfile_autogen.py` (no trailing `USER leerie` — PID-1 stays root,
DESIGN §6), `test_dockerfile_bake_from_capture.py`,
`test_base_dockerfile_chromium.py`.

**`leerie config` verb:** `test_config_verb.py`, `test_config_recapture.py`.

**Judge/heal skills:** `test_replay_capture.py`, `test_phase_judge.py`,
`test_heal_loop.py`.

**Group verb (DESIGN §20):** `test_group_launcher.py`,
`test_group_launcher_verbs.py`, `test_group_launcher_fanout.py`,
`test_group_run_json.py`, `test_group_state_dir_guard.py`.

**Finalize / host-side bash:** `test_host_finalize_sh.py` (`host_finalize`
contract — no-push/already-pushed idempotency, PR-base-branch override,
⚠ Deploy-ordering fallback rendering).

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
