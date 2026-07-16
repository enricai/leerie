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
| `.claude-plugin/plugin.json` | Existing plugin manifest (commands, skills, metadata). The `version` field is the single source of truth for `leerie --version`. |
| `scripts/install.sh` | The `curl \| bash` shell installer. Preflight (git/claude/curl) → runtime preflight (colima on macOS, nerdctl+containerd on Linux) → clone → symlink → verify. Self-contained bash; deps: `bash`, `curl`, `git`. |
| `leerie` (launcher) | Portable bash. Symlink-walks to its own location, runs the per-OS runtime preflight, builds the leerie image once per version, and execs `nerdctl run` with TTY flags adapted via `[ -t 0 ]` (see §0.5). Passes `--cgroupns=host` so the container shares the host VM's cgroup namespace — required for cgroup v2 process enrollment (nerdctl's default `--cgroupns=private` + `nsdelegate` blocks non-root `cgroup.procs` writes; see DESIGN §6 *Memory containment*). Fast paths for `--version` and `config` skip container startup. Per-run auth/config is staged into a fresh `mktemp -d "$HOME/.cache/leerie/cfg-XXXXXX"` (`$STAGE`); a `rm -rf "$STAGE"` EXIT trap is registered **immediately after the mktemp** (before the ~250-line stage-assembly block, which contains an `exit 1`) so an early exit can't leak the dir, and a best-effort startup sweep (`find "$HOME/.cache/leerie" -maxdepth 1 -type d -name 'cfg-*' -mtime +1 -exec rm -rf`) reclaims dirs leaked by trap-bypassing exits (SIGKILL / OOM / `nerdctl kill`). Because `-mtime` tests the cfg dir's own top-level mtime — which freezes at staging-completion (the running container's writes into `$STAGE/.claude/*` do NOT bump it) — a background keepalive (`while :; do touch "$STAGE"; sleep 3600; done &`, killed by the same EXIT trap) freshens the live dir hourly so a genuinely long-running run (e.g. one auto-resuming across rate-limit backoffs) is never mistaken for stale and deleted by a concurrent launch's sweep. |
| `Dockerfile` | Image recipe (Debian 13 + Node + pnpm + claude CLI + baked orchestrator source). Built locally on first run, tagged `leerie:<VERSION>`. |
| `scripts/container-entry.sh` | Container PID 1. Runs as **root** (rootful runtimes) or the **rootlesskit-mapped host UID** (rootless containerd — see DESIGN §6 *Rootless exception*); the Dockerfile intentionally omits `USER leerie` so the entrypoint can set up cgroup containment before privilege drop. It resolves `CGROUP_ROOT` (the literal `/sys/fs/cgroup`, or — rootless — the systemd-delegated `user.slice/user-$HOST_UID.slice/user@$HOST_UID.service` subtree), creates `$CGROUP_ROOT/leerie.slice` and enables the memory+pids controllers (needed for the aggregate `memory.max` cap), then — the load-bearing step — launches the **cgroup broker** (`LEERIE_CGROUP_V2_ROOT="$CGROUP_ROOT" python3 /opt/leerie-image/scripts/cgroup-broker.py &`) at the same identity, before privilege drop, because worker cgroup enrollment and limit-setting cannot be done by the dropped-privilege orchestrator (see `scripts/cgroup-broker.py` below and DESIGN §6). `ulimit -c 0`, the cgroup slice setup, the broker launch, `cd /work`, `chown leerie: /work`, and `chown -R leerie: /tmp/.cache` all run at this pre-drop identity (the two `chown`s and the `runuser` drop itself are skipped in rootless mode). The `/tmp/.cache` chown is a runtime safety net for the same fix applied in the Dockerfile (after `useradd`, since the leerie user must exist): the build-time `mise install --system` creates `/tmp/.cache/mise/` as root (via `XDG_CACHE_HOME=/tmp/.cache`), and on Fly Machines the rootfs preserves this ownership (unlike local nerdctl where `/tmp` is an ephemeral overlay), causing `mise install` to fail with EACCES when a repo pins a runtime version not pre-baked in the image. The final exec drops to leerie via `runuser -u leerie -- env HOME=/home/leerie USER=leerie LOGNAME=leerie ...`: if invoked with no argv (remote/Fly path — the launcher exec's the orchestrator via `flyctl ssh console -C "python3 -"` separately, which also drops via `Popen(user="leerie")`), the runuser exec wraps `sleep infinity` to keep the namespace alive; otherwise it wraps `python3 /opt/leerie-image/orchestrator/leerie.py "$@"` (local path — nerdctl always passes argv). The explicit `env` form is used instead of `runuser --login` because the login form would chdir to `/home/leerie` and override the `cd /work` invariant. |
| `scripts/cgroup-broker.py` | Cgroup broker (DESIGN §6 *Memory containment*). Launched by `container-entry.sh` at PID 1 before the privilege drop; the dropped-privilege orchestrator drives it over a Unix socket at `/run/leerie-cgroup.sock`. Handles `ping` / `probe` / `create <sid> <mem> <pids>` / `enroll <sid> <pid>` / `destroy <sid>` / `stat <sid>` (read-only → `OK <pids.current> <pids.max> <pids.events.max> <memory.events.oom_kill>`, used by `_cgroup_stat` — a 4-tuple client mirroring the wire response — for both PID-exhaustion detection (DESIGN §6 *Detecting PID exhaustion*) and memory-OOM naming (DESIGN §6 *Detecting memory OOM*)). The `memory.events` `oom_kill` counter (v2's `<sid-dir>/memory.events`, v1's memory-controller `mdir/memory.events`, both parsed by `_memory_events_oom`; missing/unreadable file degrades to 0) is the definitive memory-OOM signal, mirroring the `pids.events` `max` counter's role for fork denial. Exists because worker cgroup enrollment and limit-setting cannot be done from the orchestrator's own dropped-privilege identity (a subtree merely `chown`ed after creation keeps its controller limit files root-owned, and cross-scope task migration needs write on the common-ancestor cgroup the leerie user doesn't own — both reproduced live). Detects and handles cgroup **v2** (unified `<V2_ROOT>/leerie.slice/leerie-w-<sid>/{pids,memory}.max` — `V2_ROOT` defaults to the literal `/sys/fs/cgroup` but is overridden via `LEERIE_CGROUP_V2_ROOT` to the systemd-delegated user slice under rootless containerd, DESIGN §6 *Rootless exception*) vs **v1/hybrid** (split `pids/`+`memory/` hierarchies at the fixed `V1_ROOT`, observed on Fly Firecracker VMs, never rootless). Validates every `<sid>` against `^[A-Za-z0-9._-]+$` (no path traversal) and requires integer pids/limits — it is the single most-privileged surface, so it is kept minimal and auditable. |
| `scripts/remote/build-push.sh` | Build and push a self-contained leerie image to Fly.io's registry. The baked source at `/opt/leerie-image/` lets the image run on Fly Machines without any bind mount. Default mode is Fly's remote builder (no host Docker daemon required); the local-build path (nerdctl/docker on the host) is opt-in via `--local-build` or `LEERIE_LOCAL_BUILD=1`. The remote builder uses a tmp fly.toml with the `[build] image = ...` line stripped to avoid flyctl#1686 (where flyctl skips the build step in favor of fetching the pre-pinned image). |
| `scripts/remote/provision.sh` | Fly.io machine lifecycle helper (sourced by the `leerie` launcher's `RUNTIME=fly` branch). Exports `provision_machine()` (create → wait-started → register `decide_teardown` trap), `stop_machine()`, `destroy_machine()`, `destroy_volume()`, `_try_fetch_branch_for_teardown()`, and `decide_teardown()`. `destroy_volume()` reaps `$LEERIE_VOLUME_ID` **independently of any machine id** and is deliberately not nested inside `destroy_machine`'s `[ -z "$mid" ]` early return: Fly volumes outlive their machines by design (*"a Machine can be destroyed without destroying its volume"* — Fly docs; the leftover is a documented "unattached volume"), and there is no platform-side lifecycle hook, so the machine already being gone is precisely when a known volume still needs reaping. `destroy_machine` calls it last, preserving the machine-then-volume order (Fly refuses to destroy an attached volume: *"in use by machine X"*). Best-effort: a failed volume destroy logs a warning and returns 0 — an orphan volume is a billing issue, a teardown that aborts is a correctness issue. The trap fires on EXIT, INT, and TERM; `decide_teardown` classifies `$LEERIE_REMOTE_EXIT_RC` and routes to one of three dispositions: **sync-then-finalize-then-destroy** (genuine terminal exits: 0, EXIT_NEEDS_ANSWERS=10, EX_TEMPFAIL=75 — note: `EXIT_LOCKED=75` from the orchestrator is remapped to `container_rc=130` by the launcher's rc=75 branch before `LEERIE_REMOTE_EXIT_RC` is exported, so the only `rc=75` that *does* reach `decide_teardown` is genuine EX_TEMPFAIL from worker rate-limit / parse-fail surfaces, not the single-owner-per-run-dir refusal; see §Single-owner-per-run-dir enforcement below — `_try_fetch_branch_for_teardown` runs `fetch_branch` FIRST; on success, source `scripts/host-finalize.sh` and call `host_finalize <run-dir>` to push + open the PR with the host's auth; **only if push succeeds** does `destroy_machine` run; on push failure leave the machine RUNNING with a recovery banner pointing at `leerie --finalize <run-id> --runtime fly`; on sync failure same recovery pattern with `sync_failed_at` written to the sidecar), **detach** (host-side SIGINT=130/SIGTERM=143: user stopped watching, orchestrator on the machine is still running — leave machine alone, print reattach hints), or **pause-on-failure** (other non-zero rc: sync run state directory from machine to host via tar-pipe bounded by a 60 s timeout, then stop machine, write `paused_at`/`pause_reason` to the run sidecar; the state sync is best-effort — failure is logged but does not block the pause, and the machine-side state is preserved on the volume). With the tail wrapper now propagating the orchestrator's exit code via `orchestrator.exit_code`, `die()` exits (rc=1) reach the pause branch rather than the clean-exit branch, so partial-failure runs are paused (machine stopped, filesystem preserved) rather than destroyed. |
| `scripts/remote/lib.sh` | Shared bash helpers sourced by `provision.sh`, `resume-machine.sh`, `re-seed.sh`, `fetch-branch.sh`, `seed-repo.sh`. Exports `_extract_flyctl_remote_rc()` (parses the actual remote exit code from a captured `flyctl ssh console` stderr file — flyctl returns 1 for any non-zero remote exit; the real code is in stderr as `Error: ssh shell: Process exited with status <N>`; falls back to the original flyctl rc when the pattern is absent), `update_run_json()` (atomic merge of fields into `$LEERIE_STATE_HOST_DIR/runs/<run-id>/run.json` on the host), `wait_for_started()` (poll `flyctl machine status` until the machine reaches `started`, with timeout), `require_flyctl()` (detect `flyctl` on PATH; if missing AND not `--no-runtime-install`, prompt to install via `brew install flyctl` on macOS or `curl -L https://fly.io/install.sh | sh` on Linux; check `flyctl auth status` and prompt for `flyctl auth login` if unauthenticated), `render_tail_wrapper()` (emits a POSIX-sh wrapper script that tails `orchestrator.log` and watches orchestrator liveness via OR of pid-file `kill -0` and `/proc/[0-9]*/cmdline` scan for `orchestrator/leerie.py`+run-id — the cross-check closes the stale-pid contagion of DESIGN §6 *Single owner per run dir*; when the orchestrator exits, the wrapper reads `orchestrator.exit_code` from the run directory — written by `main()`'s `except SystemExit` handler before every controlled exit — and uses it as its own exit code so `decide_teardown` can route failed runs to the pause branch; when the file is absent (OOM, SIGKILL, crash before the handler ran), the wrapper falls back to exit 0 for backward compatibility), and `tail_with_optional_autofinalize()` (wraps `render_tail_wrapper` + `flyctl ssh console` with optional `AUTO_FINALIZE_TOKEN` plumbing: on clean exit, captures stderr through `tee`, greps for the token to extract the final run-id, then `exec`s `leerie --finalize <id>` on the host — used by both the fresh-launch tail and the `--resume` rc=75 pivot). Replaces four duplicated detection blocks across the remote scripts. |
| `scripts/remote/resume-machine.sh` | Resume helper for paused remote runs (sourced by the launcher's `RUNTIME=fly` branch — the run-id IS the machine ID, so no lookup is needed). Exports `resume_machine()`: compares the `image_tag` stored in the run sidecar against the current `$FLY_IMAGE_TAG` — if they differ (leerie was upgraded between provision and resume), runs `flyctl machine update --image $FLY_IMAGE_TAG --skip-start -y` to update the stopped machine's image before starting it (volumes at `/work` survive the update; `seed_auth` re-provisions the ephemeral rootfs on every resume); fail-open — if the update fails, logs a warning and proceeds with the old image. Then runs `flyctl machine start` (idempotent on already-running machines via the `flyctl machine status` fallback), waits for `started`, and clears `paused_at`/`pause_reason` from `run.json` if it exists. The launcher then runs the orchestrator inside the resumed machine with `--resume <id>`. When `image_tag` is absent from `run.json` (runs provisioned before the field existed), the update always fires (empty stored tag != current tag), ensuring legacy machines pick up the latest image on resume. |
| `scripts/remote/re-seed.sh` | Mid-run re-rsync helper (Phase 4). Exports `re_seed()`: reads `fly_machine_id` from the run sidecar, wakes the machine via `flyctl machine start` if stopped, runs a safety check that refuses re-seed when machine-side `/work` has uncommitted tracked changes (unless `LEERIE_RE_SEED_FORCE=1`), then calls `seed_repo_dirty` from `seed-repo.sh`. Invoked by the launcher's `--re-seed <run-id>` fast-path and by the auto-re-seed step in the `--resume <run-id> --runtime fly` flow. |
| `scripts/remote/seed-auth.sh` | Seeds Claude config + git identity into the provisioned Fly Machine. Tar-pipes the host's `$STAGE` (Keychain-extracted OAuth credentials + projects-stripped `~/.claude.json` + `.claude/` subdirs, with `.claude/local`, `.claude/plugins/cache`, and `.claude/plugins/marketplaces` skipped; `~/.aws/` also included when Bedrock mode is enabled — see `$STAGE/.aws` mount row above; `.gitconfig`, `.gitconfig.local`, `.gitignore`, `.gitignore_global`, `.git-credentials`, `.netrc`, `.ssh`, `.gnupg`, and `.config` are explicitly excluded from the tar — those are git/push auth that lives on the host per DESIGN §6 *Finalization* — ~408 MB host npm install duplicated by the Dockerfile's globally-installed claude binary, plus the bulky plugin cache that's rebuilt on the remote post-tar via `claude plugin marketplace add` + `claude plugin install` from the seeded `installed_plugins.json` / `known_marketplaces.json`) to `/home/leerie/` via `flyctl ssh console -C "tar -xzC /home/leerie"` (gzip on both ends). The tar pipe is wrapped with `$(_seed_timeout_prefix)` (`timeout --kill-after=5 ${LEERIE_SEED_TIMEOUT_S:-600}` on hosts that have GNU `timeout`; no-op fallback otherwise) so a stalled `flyctl ssh console` session — observed mode where flyctl never exits even though the remote tar made progress — produces a clean rc 124/137 instead of hanging forever. rc 124/137 triggers a one-shot `flyctl agent restart` retry; if the retry also stalls, the function returns 1 and leerie's existing PAUSED-on-failure path takes over (DESIGN §6 *Pause on failure*). A background heartbeat (`_seed_progress_bg`) logs "seed_auth: still streaming (Ns elapsed)" every `LEERIE_PROGRESS_INTERVAL_S` seconds (default 10) so the user sees activity rather than a silent multi-minute wait. Writes git identity to `/home/leerie/.gitconfig` (not `--global`, which would land in `/root/.gitconfig` under the ssh-console session's default root user). Pre-warms `claude --version` once as the leerie user so the orchestrator's preflight call hits warm caches (the FIRST claude invocation on a cold Fly machine takes ~17 s — Node + statsig cold start — and would otherwise exceed the orchestrator's preflight timeout). |
| `scripts/remote/seed-repo.sh` | Two-phase bundle + delta repo seeding helper (sourced by the `leerie` launcher after `provision_machine()` succeeds). Exports `seed_repo_clone` (wipe `/work` contents but preserve the inode; create `git bundle` for the parent and each submodule; pipe each bundle via `flyctl ssh console -C "sh -c 'cat > /tmp/...'"` — `sh -c` is required because bare `cat > ...` fails on flyctl's `-C`; have the machine `git clone` from the parent bundle, wire submodule URLs to their per-submodule bundles, run `git -c protocol.file.allow=always submodule update --recursive` — `protocol.file.allow` is required by git 2.38+ for file://-style submodule URLs per CVE-2022-39253 — then chown to leerie; clean up the bundle tmpfiles), `seed_repo_dirty` (rsync the dirty/untracked delta plus force-included `.claude/`, used by both fresh-seed delta and the Phase 4 `re-seed.sh` flow), and the wrapper `seed_repo`. Bundles sidestep macOS BSD tar's NFC→NFD filename normalization, which corrupted submodule working trees containing non-ASCII filenames on the Linux receiver. No in-machine `git clone` from origin — Fly machines deliberately receive no GitHub credentials. The parent-bundle pipe is wrapped with `$(_seed_timeout_prefix)` (`timeout --kill-after=5 ${LEERIE_SEED_TIMEOUT_S:-600}` on hosts with GNU `timeout`; no-op fallback otherwise) and surrounded by a `_seed_progress_bg` background heartbeat; on rc 124/137 (timeout fired) the function returns 1 with a "flyctl ssh console likely stalled" diagnosis so leerie's PAUSED-on-failure path takes over (DESIGN §6 *Pause on failure*) matching the seed_auth pattern. A second `_seed_progress_bg` covers the submodule-bundle `git submodule foreach --recursive` batch so the user sees activity across multi-submodule transfers instead of a silent pause. **Shallow-seed path (heavy repos, DESIGN §6 *Shallow seeding for heavy repos*):** when the host repo's `.git` exceeds `LEERIE_SEED_SHALLOW_THRESHOLD_MB` (default `200`) AND the resolved seed depth is non-zero, `seed_repo_clone` skips the full `--all` bundle for the *parent* and instead: makes a throwaway `git clone --depth="$LEERIE_SEED_DEPTH" --no-local --branch <cur-branch> "file://$USER_REPO"`; `tar -cf -`s **only that clone's `.git`**; pipes the tar over the identical `$(_seed_timeout_prefix)`-wrapped `flyctl ssh console -C "sh -c 'cat > /tmp/leerie-seed-git.tar'"` channel (same heartbeat, same `PIPESTATUS[1]` + rc 124/137 handling); and the machine-side heredoc script (same pattern as `_seed_one_inspect_dir_clone`) empties `/work` inode-preservingly, untars `.git`, `git checkout -f`s the branch, `git remote remove origin` (the stale `file://<laptop>` origin is inert but removed defensively), runs the **unchanged** per-submodule bundle wiring, and `chown -R leerie: /work` last. Tarring `.git`-only (never the working tree) preserves the NFC→NFD safety property. `git bundle` cannot ship a shallow repo (grafted parents), which is why the shallow path uses tar rather than a shallow bundle. `LEERIE_SEED_DEPTH=0` (or a `.git` under threshold) keeps the full-bundle path. The shallow checkout yields a byte-identical tracked tree to the bundle clone, so `seed_repo_dirty` layers on unchanged. The shallow path additionally requires a shell-safe working-branch name (`_seed_branch_shallow_safe`: `^[A-Za-z0-9/._-]+$`, no placeholder tokens) because the branch is interpolated into the machine-side `git checkout -f <branch>` inside a `sh -c '…'` wrapper; a branch with `'`/`$`/backtick/space falls back to the full-bundle path (which never interpolates the branch). Detached HEAD likewise falls back. |
| `scripts/remote/fetch-branch.sh` | Post-run stream-back helper (sourced by `decide_teardown` BEFORE `destroy_machine` on clean exit, and by the `leerie --finalize` fast-path). Exports `fetch_branch()`: (1) discovers the completed run-id by scanning `.leerie/runs/*/run.json` on the machine for a `finished_at`-bearing, unpushed entry (stderr is captured to a tmpfile, NOT merged via `2>&1`, because `flyctl ssh console`'s "Connecting to ..." stderr would shift parsed-line indices and corrupt the discovered branch name); (2) probes whether the run branch actually exists on the machine via `git rev-parse --verify refs/heads/<branch>` — only then bundles; the bundle includes **all `leerie/subtasks/<run-id>/*` branches** present on the machine alongside the run branch (defense-in-depth: if integration never ran — crash, OOM, or `die()` before integration in older images — the raw subtask work is recoverable on the host; `git for-each-ref` discovers subtask branches dynamically; on any bundle failure the script retries with the run branch alone). A missing run branch is the cleared-but-empty terminal-state case (DESIGN §6); when the run branch is absent but subtask branches exist, they are bundled independently. The `no_push` flag on `run.json` is NOT used as a proxy because it's a mechanism flag the launcher forces (the in-Fly orchestrator can't push), not a user-intent flag; (3) tars `.leerie/runs/<run-id>/` from the machine and extracts it on the host; (4) **defense-in-depth, conditional on branch presence**: when a run branch *was* fetched, strips a stray mechanism-flag `no_push=true` from the host-side `run.json` (defense against in-flight old-image runs that wrote the mechanism flag before the `--host-no-push` intent split). When no branch was fetched (the cleared-but-empty terminal-state case — DESIGN §8), preserves `_finish_no_work_run`'s `no_push=true` intent so `host_finalize` short-circuits cleanly instead of attempting a `git push` against a non-existent ref; (5) **best-effort `.leerie/` stream-back**: iterates `config.toml` and `Dockerfile`; for each, skips if the host target already exists (never clobbers), checks remote existence via `_fetch_machine_exec test -f`, then streams via `_fetch_machine_exec cat` directly to the host target; failure removes any partial write and logs a warning but does not affect the function's return code. The destination root is `$LEERIE_STATE_HOST_DIR` when set, otherwise `$USER_REPO/.leerie`. |
| `scripts/remote/aws-credentials.sh` | Standalone AWS credential/profile/region resolution helper for the EC2 runtime. Exports `resolve_aws_credentials [--profile NAME] [--region NAME]`: resolves credentials and region host-side in the same precedence order the AWS CLI/SDKs use — explicit `AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY` (+ optional `AWS_SESSION_TOKEN`) env vars first, then a named profile (`--profile` / `AWS_PROFILE` / `default`) via static credentials in `~/.aws/credentials` or a cached SSO token in `~/.aws/sso/cache/*.json` (both the modern `sso_session`-reference form and the legacy inline `sso_start_url`/`sso_region` form), ending with an actionable `aws sso login --profile <p>` / `aws configure` hint rather than a silent fallthrough (no IMDS instance-role fallback — this runs on the operator's host, not on an EC2 instance). Region resolves `AWS_REGION` > `AWS_DEFAULT_REGION` > profile `region` > die-with-hint; a `--region`/`--profile` CLI flag is treated as explicit-wins over its env-var equivalent. On success prints `export KEY=value` lines to stdout for sourcing; on failure prints nothing to stdout and an actionable error to stderr, returning 1. Pure file I/O against `~/.aws/config`/`~/.aws/credentials`/`~/.aws/sso/cache/*.json` + bash/python3 stdlib — no `aws` binary or boto3 dependency, mirroring the existing `detect_bedrock_mode()`/`bedrock_preflight()` precedent (see the `$STAGE/.aws` mount row in the Bind-mount table below, "Staged when `detect_bedrock_mode()`..."). Wired into the launcher's `RUNTIME=ec2` branch: the branch sources this file, calls `resolve_aws_credentials` (passing `--profile`/`--region` when `LEERIE_AWS_PROFILE`/`LEERIE_AWS_REGION` are set) and `eval`s its `export` lines into the launcher's environment, *before* sourcing `ec2-lib.sh` and calling `require_aws` — so the resolved identity is what `require_aws`'s `sts get-caller-identity` probe and every subsequent `aws ec2 ...` call inherit. An unresolvable credential chain aborts with this script's own `aws sso login --profile <p>` hint and never reaches `require_aws` (`tests/test_ec2_e2e_provision.py` pins the ordering and the fail-closed abort; `tests/test_ec2_launcher_credentials.py` pins the region axis and the `--profile` argv seam). The branch also resolves the `--aws-region`/`--aws-profile` and `--ec2-*` instance-shape knobs (config-001/002). EC2 *provisioning* dispatch is still absent: after preflight passes, the branch `die()`s with "instance provisioning is not yet wired into the launcher" (`leerie:6105`) rather than dispatching to `ec2-provision.sh`'s `provision_instance()` — that script has since shipped independently (see its row below), but nothing calls it. This credential helper (config-004) and the AWS SDK dependency pin (config-005) have also shipped. |
| `scripts/remote/ec2-lib.sh` | Shared bash helpers for the EC2 lifecycle, parallel to `scripts/remote/lib.sh`'s role for the Fly path. Exports `require_aws()`: the host-side preflight the launcher's `RUNTIME=ec2` branch calls before provisioning, modeled directly on `require_flyctl()`'s two-stage shape (binary-present? → authenticated?). Checks `command -v aws`; if missing, prints an actionable AWS CLI v2 install hint and returns 1 (no auto-install — unlike `require_flyctl`, the AWS CLI's official installers commonly need `sudo`, which is out of scope for an unattended preflight). If present, resolves a profile (`--profile`-equivalent precedence: `LEERIE_AWS_PROFILE` > `AWS_PROFILE` > unset, matching the Python-side `AWS_PROFILE_ENV` knob at `orchestrator/leerie.py:513`) and probes `aws sts get-caller-identity` (with `--profile` when resolved); on failure prints the `aws sso login --profile <profile>` (or bare `aws sso login`) recovery hint and returns 1 — reusing `bedrock_preflight()`'s exact credential-error vocabulary (`leerie:4903-4907`) rather than inventing a second one. Also exports `resolve_ami()` / `resolve_instance_type()` / `resolve_key_name()` / `resolve_security_group()` / `resolve_subnet_id()`, one per `LEERIE_EC2_*` var (see "EC2 instance-lifecycle vars" below): each a thin required-var read (`_resolve_ec2_var`) that prints the value on success, or an actionable error naming the missing var on stderr and returns 1 (not a bare `${VAR:?}`, which would kill the whole sourcing shell with bash's generic "parameter null or not set" message under `set -u`). These stay in `ec2-lib.sh` (shared) rather than `ec2-provision.sh` (lifecycle-specific) because `ec2-ssm.sh`'s transport helpers also need `resolve_key_name`/`resolve_security_group` for the SSH-fallback path (DESIGN §6 "SSH ... remains available as a fallback transport"). |
| `scripts/remote/ec2-seed-repo.sh` | EC2 counterpart to `scripts/remote/seed-repo.sh` (DESIGN §6 *EC2 runtime lifecycle*, "Seed" row: "same two steps, transport substituted"). The payload logic — `.gitignore`-aware content via the bundle (committed tracked files) plus the porcelain-filtered dirty-delta rsync, unconditional `.leerie/` exclusion except the three whitelisted config files, the shallow-vs-full-bundle decision, submodule bundling — is IDENTICAL to `seed-repo.sh`; only the wire transport differs: `ec2_tar_pipe` (plain `ssh`, from `ec2-lib.sh`) for bulk data (the parent bundle/shallow `.git` tar and each submodule bundle) instead of `flyctl ssh console -C "sh -c 'cat > ...'"`, and `ec2_remote_exec` (SSM Session Manager, the default transport) for small instance-side commands (the `/work` reset, the machine-side clone/checkout script, `chown`) instead of the same `flyctl ssh console -C` calls. Since `ec2_tar_pipe`'s receiver is `tar -xzC <dir>` (not a bare `cat > file`), each bundle/tar payload is wrapped in a one-entry gzipped tar by the private helper `_ec2_pipe_file_via_tar` before going over the wire. Exports `ec2_seed_repo_clone` (same wipe-`/work`-preserve-inode step; full `git bundle create - --all` for the parent, or — above `LEERIE_SEED_SHALLOW_THRESHOLD_MB` with a non-zero `LEERIE_SEED_DEPTH` and a shell-safe branch name, gated by the same `_seed_use_shallow`/`_seed_branch_shallow_safe` functions duplicated verbatim from `seed-repo.sh` — a `git clone --depth=N --no-local` tarred `.git`-only; per-submodule bundles; instance-side `git clone`/untar+`checkout`, submodule URL rewiring, `git -c protocol.file.allow=always submodule update --recursive`, `chown -R leerie: /work`), `ec2_seed_repo_dirty` (the dirty-set computation and `.leerie/`-whitelist/`.claude/`-force-include filter are BYTE-IDENTICAL to `seed_repo_dirty`'s Python filter; transport is plain `rsync -e <ssh-wrapper>` directly against the resolved `LEERIE_EC2_SSH_TARGET` — no `flyctl`-console-tunneled `rsync --server` indirection needed, since SSH is a real, directly-usable transport for EC2 per DESIGN §6), and the wrapper `ec2_seed_repo`. New env var `LEERIE_EC2_SSH_TARGET`: the `ssh`(1) destination for the instance (e.g. `ec2-user@<public-ip>` or an `ssh_config` Host alias) that `ec2_tar_pipe`/the dirty-delta rsync consume verbatim — resolving an `LEERIE_EC2_INSTANCE_ID` to a reachable address is `ec2-provision.sh`'s job, populated by `provision_instance()`. Preflight (`_ec2_seed_repo_preflight`) requires `LEERIE_EC2_INSTANCE_ID`, `LEERIE_EC2_SSH_TARGET`, `USER_REPO`, and `require_aws` (from `ec2-lib.sh`). |
| `scripts/remote/ec2-provision.sh` | The `provision.sh` counterpart for the EC2 lifecycle (DESIGN §6 *EC2 runtime lifecycle*, "Stage mapping" table). The `leerie` launcher's `RUNTIME=ec2` branch sources `aws-credentials.sh` and `ec2-lib.sh` and gates on `resolve_aws_credentials`/`require_aws()` before anything else, but does not yet source this file or dispatch to `provision_instance()` — the branch `die()`s right after preflight passes (see "Runtime mode" below; that dispatch wiring is a separate subtask). All EC2 API calls in this file go through the **`aws` CLI** (see "boto3 usage boundary" below), mirroring how `provision.sh` shells out to the `flyctl` binary rather than importing a Go SDK; JSON responses are parsed with inline `python3 -c` (not `aws ... --query/--output text`) so the same parsing works uniformly against the real CLI and against test stubs that ignore `--query`. Sources `lib.sh` (for `remote_log`/`update_run_json`/`iso_now`, which are Fly-agnostic pure functions despite `lib.sh`'s file-level Fly-specific docstring) and `ec2-lib.sh` (for `require_aws`/`resolve_*`). Exports, one per DESIGN §6 stage-mapping row: `provision_instance()` (`aws ec2 run-instances` with no explicit block-device mapping — AMI/instance-type/key-name/security-group/subnet from the `resolve_*` helpers in `ec2-lib.sh`; registers the EXIT/INT/TERM teardown trap only *after* a successful create, mirroring `provision.sh:700-704`; writes the crash-recovery sidecar `ec2-instance.json` unconditionally (instance id, region, created-at — the EC2 analog of `provision.sh`'s `fly-machine.json`) plus `ec2_instance_id`/`ec2_ami` onto `run.json` when `LEERIE_RUN_ID` is set, mirroring `provision.sh`'s `fly_machine_id` sidecar-write timing — before any orchestrator code runs, so `--resume` survives a Ctrl-C during seed); `wait_for_instance_ready()` (poll `describe-instances` for `State.Name == running`, then `describe-instance-status` for both `InstanceStatus.Status` and `SystemStatus.Status == ok` — DESIGN §6 is explicit that `running` alone is not SSH/SSM-reachable, unlike Fly's `started`); `stop_instance()` / `terminate_instance()` (`aws ec2 stop-instances` / `terminate-instances`; both idempotent no-ops on an empty `LEERIE_EC2_INSTANCE_ID`); `decide_ec2_teardown()` (the same three-disposition classification `decide_teardown()` in `provision.sh` implements — sync-then-terminate / detach / pause — reusing `LEERIE_REMOTE_EXIT_RC` and the `LEERIE_TEARDOWN_DONE` idempotency guard unchanged, since DESIGN §6 states the exit-code classification table is runtime-agnostic by construction; the clean-exit branch calls `_try_fetch_state_for_ec2_teardown()` — a hook, overridable by tests, that sources `scripts/remote/ec2-fetch-branch.sh` and calls its `fetch_state_ec2()` (fails closed — leaves the instance running — if that file is absent, e.g. an older checkout) — BEFORE `terminate_instance()`, mirroring `provision.sh:262-272`'s one-way-ratchet ordering: destroy-then-fetch would make paid-for LLM work unrecoverable). No auto-finalize (push + PR) integration yet — unlike `provision.sh`'s `decide_teardown`, `decide_ec2_teardown`'s clean-exit branch only syncs-then-terminates or leaves-running-on-sync-failure; wiring `host_finalize` in is deferred to a later subtask. Root-EBS-volume lifecycle needs no dedicated reap function (DESIGN §6 "EBS volume lifecycle" case 1: `DeleteOnTermination=true` is AWS's default and this design adopts it as-is — no `destroy_volume()` counterpart exists or is needed, unlike Fly; a failed `run-instances` call creates nothing to orphan-clean, unlike Fly's pre-create volume window). |
| `scripts/remote/ec2-resume-instance.sh` | Resume helper for paused EC2 runs (the EC2 counterpart to `scripts/remote/resume-machine.sh`), designed to be sourced by the launcher's `RUNTIME=ec2` resume path alongside `ec2-provision.sh` (for `wait_for_instance_ready`/`_aws_region_profile_args`/`decide_ec2_teardown`) — not yet wired in: like `ec2-provision.sh` (see its row above), the launcher's `RUNTIME=ec2` branch `die()`s right after preflight and never sources this file (that dispatch wiring is a separate subtask). Exports `resume_instance(<instance-id>)`: describes the instance's current state; if already `running`, logs and skips straight to the readiness/ssh-target steps (idempotent — no `start-instances` call); if `stopped`/`stopping`/`pending`, issues `aws ec2 start-instances`; if `terminated`/absent, fails with the same "no longer recoverable" hint `resume_machine()` gives for a destroyed Fly machine. Then calls `wait_for_instance_ready()` (unchanged, from `ec2-provision.sh`) so the same `running` + `InstanceStatus`/`SystemStatus` `ok` gate applies as on first provision. Re-resolves `LEERIE_EC2_SSH_TARGET` from the instance's current `PublicIpAddress` via `describe-instances` — EC2 assigns a new public IP on every stop/start cycle absent an attached Elastic IP, so the address from provision time cannot be reused. Re-arms the `decide_ec2_teardown` EXIT/INT/TERM trap (the launcher process is fresh on resume, mirroring `resume_machine()`'s trap re-arm) and clears `paused_at`/`pause_reason` on the run.json sidecar when one is resolvable. Never calls `terminate-instances` or `delete-volume` on any path — resume is a pure wake path, honoring the one-way-ratchet invariant `decide_ec2_teardown()` already encodes elsewhere. |
| `scripts/remote/ec2-fetch-branch.sh` | EC2 counterpart to `scripts/remote/fetch-branch.sh` — the stream-back half of DESIGN §6 "Transport substitution for `flyctl ssh console`", sourced by `ec2-provision.sh`'s `_try_fetch_state_for_ec2_teardown()` hook. Exports `fetch_state_ec2()`, porting `fetch_branch()`'s four steps (run discovery; branch-existence probe + git-bundle stream-back with the same subtask-branch defense-in-depth bundling and run-branch-only retry fallback; `.leerie/runs/<run-id>/` tar stream-back with the same `no_push`-stripper conditional on branch presence; best-effort, never-clobbering `.leerie/config.toml` + `.leerie/Dockerfile` stream-back) verbatim, with the transport substituted: short text commands (run discovery, `git rev-parse --verify`, `git for-each-ref`, `.leerie/` file existence probes) go over `ec2_remote_exec` (SSM, from `ec2-lib.sh`) since their output is small and command-substitution-safe; binary bulk data (the git bundle, the run-state tar, each streamed `.leerie/` file's bytes) goes over a private local helper, `_ec2_fetch_ssh` — a plain `ssh $LEERIE_EC2_SSH_TARGET "<cmd>"` invocation whose raw stdout is redirected straight to a host-side file/pipe, never captured via bash command substitution (unlike `ec2_remote_exec`, which drops trailing newlines/NUL bytes and would silently corrupt a bundle). `ec2_tar_pipe` itself is not reused here because it is upload-only (host stdin → instance `tar -x`); `_ec2_fetch_ssh` is `ec2-fetch-branch.sh`'s own download-direction counterpart, mirroring `fetch-branch.sh`'s `_fetch_machine_exec ... > host_bundle` binary-safety pattern one-for-one. Preflight requires `LEERIE_EC2_INSTANCE_ID`, `LEERIE_EC2_SSH_TARGET`, `USER_REPO`, and `require_aws` (from `ec2-lib.sh`). |
| `scripts/remote/ec2-ssm.sh` (spec — not yet implemented) | Transport substitution for `flyctl ssh console`'s *launch/attach* roles (DESIGN §6 "Transport substitution for `flyctl ssh console`"; the stream-back role is `ec2-fetch-branch.sh`, already shipped — see above), sourced alongside `ec2-provision.sh`. Default transport is **SSM Session Manager**, not SSH — DESIGN §6 states this explicitly (no inbound security-group rule, no key-pair distribution, no public IP; auth flows through the same AWS credential chain as the rest of the EC2 runtime). Planned exports: `ec2_launch_detached()` (the `aws ssm start-session --target <instance-id> --document-name AWS-StartInteractiveCommand --parameters command="python3 -"` analog of `flyctl ssh console --pty=false -C "python3 -"`, piping the same detached-launch wrapper `render_tail_wrapper()` in `lib.sh` already renders); `ec2_attach()` (same analog with `command="tail -F orchestrator.log"`, or a bare interactive session for `--shell`). An SSH fallback (`LEERIE_EC2_KEY_NAME` + an inbound security-group rule on port 22) is documented in DESIGN §6 as available for operators whose IAM policy disallows SSM, but is not the default and is not required for this spec's baseline. |
| `scripts/host-finalize.sh` | Host-side push + PR creation block, sourced by three call sites: the local-runtime post-run code path in `leerie`, `decide_teardown` in `scripts/remote/provision.sh` (Fly clean-exit auto-finalize), and the `leerie --finalize <run-id>` recovery fast-path. Exports `host_finalize <run-dir>`: honors `run.json.no_push` (skip — this is the **intent** flag, written by the orchestrator's `phase_finalize` from `push_will_happen(no_push, host_no_push)`, not the launcher-forced mechanism flag), short-circuits when `pushed_at` is already set **by branch position, not mere presence** (DESIGN §6 *Finalization*): compares the local run-branch tip against the pushed origin tip via `git rev-parse` / `git ls-remote` — equal tips → no-op (the idempotent common case, including fully-pushed chain waves); origin a strict ancestor of the local tip via `git merge-base --is-ancestor`, or origin absent (a prior finalize pushed a *partial* branch — e.g. a mid-wave `die()` stamped `finished_at` before the completion gate) → falls through to a fast-forward re-push + re-open PR, still behind the completion gate so only a `completed_waves == len(waves)` run can re-push (the gate itself fails open on a missing/unreadable `state.json`, so that check only applies when the signal exists); a *diverged* origin (has commits the local branch lacks) keeps the idempotent short-circuit instead, since its push could not fast-forward; on success keeps `pushed_at` set and sets `pr_url` (invariant `pr_url ⇒ pushed_at` preserved), **defense-in-depth**: when the run branch named in `run.json` does not exist locally (`git rev-parse --verify refs/heads/<branch>` fails — the cleared-but-empty terminal-state case where no `setup-run.sh` ran), logs "run branch absent locally; treating as no-op" and returns 0 rather than attempting a push that would error with `src refspec ... does not match any`, runs `git push -u origin <run-branch>` (with `--no-verify` if `NO_VERIFY_PUSH=true`). Before PR creation, validates that `working_branch` still exists on origin via `git ls-remote --exit-code --heads`; if deleted (common when a stacked run's parent was squash-merged while this run was in flight), falls back to the repo's default branch. Then `gh pr create` (using `pr_title`/`pr_body` from `run.json` if the pr_writer worker populated them, otherwise the deterministic fallback), wrapped in a bounded retry (`0 5 10 20 30`s backoff, ~68 s total) to ride out GitHub's post-push ref-indexing lag ("No commits between" / "Head sha can't be blank"). PR-creation failure is non-fatal (push already succeeded); the error message suggests a retry command using the resolved base (original or fallback). Replaces ~140 lines of inline launcher code with a single function call so the three callers stay in sync. |

### Python runtime — provisioned inside the container

Leerie requires Python 3.10+. The container image installs Debian 13's
`python3` (currently 3.13), which satisfies the requirement. The host
needs no Python at all. The orchestrator's source is baked into the
image at `/opt/leerie-image/` via the Dockerfile's `COPY` instructions.
On local runs the launcher's bind mount (`-v $LEERIE_REPO:/opt/leerie-image:ro`)
shadows the baked copy, so iterating on `orchestrator/leerie.py` still
does not require an image rebuild — the host file is used on the next run.

The orchestrator prefers stdlib. Third-party runtime libraries are
permitted when they (a) replace non-trivial logic with a widely-used,
stable implementation, (b) earn their distribution cost (image size,
build time, dependency tracking), and (c) are documented here. Pins
live in `requirements.txt` at the repo root; the Dockerfile runs
`pip3 install --break-system-packages --no-cache-dir -r requirements.txt`
once per image build. There is no `pyproject.toml` and no PyPI release.

Current runtime deps:

- `tenacity` — exponential backoff for transient `claude -p` envelope
  errors (auth / rate-limit). See §3 *Auth/quota backoff*.
- `tree-sitter` — incremental parser core, required by the P6 repo-map
  (`build_repo_map`). Deliberate exception to the stdlib-preferred
  policy: tree-sitter's mtime-cached symbol/reference graph is the
  structural foundation that prevents shallow planner splits
  (DESIGN §5½ *P6 — codebase structural map*). Ships a
  prebuilt manylinux wheel; no C build needed.
- `tree-sitter-language-pack` — prebuilt grammar collection (Python,
  TypeScript, JavaScript, Ruby, Go, Rust, …) for `tree-sitter`. Paired
  with the `tree-sitter` pin; the `cp310-abi3` ABI tag means one wheel
  covers Python 3.10 through 3.13 (the container's Debian 13 Python).
- `boto3` / `botocore` — the maintained AWS SDK for Python. Deliberate
  exception to the stdlib-preferred policy: EC2 provisioning
  (the `--runtime ec2` counterpart to the existing Fly.io runtime)
  needs AWS's own credential-resolution chain (env vars → shared
  config/credentials files → SSO → EC2 instance profile/IMDS →
  container credentials), which is maintained upstream as AWS's
  auth surface evolves; hand-parsing `aws` CLI output would mean
  reimplementing and re-chasing that chain. `botocore` is pinned
  explicitly alongside `boto3` (rather than left as an implicit
  transitive resolve) to match how `tree-sitter-language-pack` is
  pinned alongside `tree-sitter` — an exact pin on both the
  high-level SDK and the library that actually implements
  credential/region resolution and request signing. See "Remote
  execution mode" below for the `--runtime local|fly|ec2` resolution
  order and `scripts/remote/aws-credentials.sh`'s row above for the
  operator-host credential precedence chain the SDK mirrors.

  **boto3 usage boundary (DESIGN §6 *EC2 runtime lifecycle*).** This
  pin is installed by the Dockerfile's `pip3 install -r requirements.txt`
  step and is therefore available only **inside the container image** —
  the host has no pip/venv surface at all (§0 above: "the host needs
  neither Python nor `uv`"; host-side `python3` invocations across
  `scripts/remote/*.sh` are stdlib-only by design, e.g.
  `aws-credentials.sh`'s explicit "no `aws` binary or boto3 dependency").
  DESIGN §6's stage-mapping table names `boto3` as the API surface for
  `RunInstances` / `describe_instances` / SSM `start_session`, but those
  calls are the outer host-side lifecycle — created and polled by the
  launcher *before* any container or instance exists, the same ordering
  constraint that puts `flyctl machine run` in the bash launcher rather
  than a Fly Go-SDK import. Since the host cannot run boto3, the code
  surface that actually implements DESIGN §6's stage table
  (`scripts/remote/ec2-provision.sh` / `ec2-ssm.sh`, spec'd in the Files
  table above) shells out to the **`aws` CLI** for every host-side EC2 API
  call — mirroring how the Fly path shells out to the `flyctl` binary
  rather than importing a Go SDK, and reusing `require_aws()`'s existing
  host dependency (`ec2-lib.sh`) rather than introducing a host-side pip
  install that would break the "portable bash, stdlib-only host python3"
  invariant. `boto3`/`botocore` remain reserved for future in-container
  orchestrator-side AWS calls (none exist yet); this pin and the
  credential helper (`aws-credentials.sh`) are the currently-landed
  pieces, and `scripts/remote/ec2-provision.sh` / `ec2-ssm.sh` are the
  next ones a provisioning subtask must build per the Files-table spec
  above.

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

1. **Preflight**: verifies `git`, `claude`, and `curl` are on `PATH`.
   Missing deps print a platform-specific remediation hint and the
   script exits non-zero.
2. **Runtime preflight**: per `uname -s`. On macOS: verifies `colima`
   is installed and the VM is running. On Linux: verifies `nerdctl`
   is installed and reaches containerd. Prints copy-pasteable install
   hints on failure (`brew install colima` / distro package commands).
   Does NOT auto-install brew/apt packages — that's the user's choice.
3. **Clones** `enricai/leerie` to `$LEERIE_HOME` (default `~/.leerie`).
   `git clone --depth 1` for fresh installs; `git pull --ff-only` for
   upgrades.
4. **Symlinks** `$LEERIE_HOME/leerie` → `~/.local/bin/leerie`. Creates
   `~/.local/bin` if missing. Does not touch system directories.
5. **PATH check**: if `~/.local/bin` is not in `$PATH`, prints (does
   not silently edit) the exact shell-rc line to add, based on `$SHELL`.
6. **Verifies** by invoking `leerie --version` (the launcher's fast path
   answers without spinning up a container — see below).

Supports `--dry-run` (prints actions without executing) and
`--prefix DIR` (overrides `LEERIE_HOME`).

### `--version`

`leerie --version` reads `.claude-plugin/plugin.json`'s `version`
field — single source of truth. Two parallel readers:

- **Orchestrator** (`_read_version()` in `leerie.py`): stdlib `json` load.
  Exercised by `tests/test_version_flag.py`.
- **Launcher** (bash `awk` extraction): used by the fast path that
  short-circuits container startup. Both readers return the same value
  on the same `plugin.json`, and `tests/test_version_flag.py` guards
  the canonical surface.

`install.sh` uses `leerie --version` as its end-to-end smoke test — and
because the fast path doesn't require a running container, the smoke
test runs the moment the symlink is in place.

Maps to `DESIGN.md`: §2 (no plugin-spawned subagents — the launcher is
plain process exec, not in-session orchestration). §6 *Worker subtree
termination* and §0.5 of this document describe what runs inside the
container the launcher starts.

---

### `config`

Launcher bash case arm: `config)`. `leerie config` is a host-only fast
path — it exits before `nerdctl run` and never starts a container. It is
listed alongside `--version` in the ownership-guard skip-list so it never
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
- **`leerie config --recapture [--force]`**: host-only (no container). Calls
  `run_recapture_deps()` from the orchestrator module, which consolidates
  across **all** finished runs with `logs/` under the state dir (not just the
  newest — each run's commands inform the dep decision). With an explicit
  `--run-id`, only that run is targeted. Without `--force`, runs that already
  have a `dep_capture.done` sentinel are skipped and the write is a never-clobber
  **union** (only new packages/managers added). `--force` drops the sentinel on
  each target run so the worker re-fires unconditionally **and** switches the
  write to a wholesale **replace** (`capture_repo_deps(replace=True)`) — the
  fresh capture is authoritative and deps no longer captured are dropped; an
  empty capture leaves the existing config untouched. Each run's `State` is
  flocked (skipped, not fatal, on
  `StateLockedError`). Exits 1 if no runs directory or no finished run found.
  The seam `exec_module()`s `orchestrator/leerie.py` on the **host**, whose
  `python3` is not guaranteed to have `requirements.txt` deps (§0), so the
  orchestrator's sole third-party import (`tenacity`) is deferred into
  `claude_p()` rather than module scope — the run-discovery guards above are
  pure pathlib checks and print their diagnostic even when `tenacity` is absent.

All four sub-modes share an inline BLT inferrer (`_config_read_key`,
`_infer_axis`, `_axis_source`) implemented directly in the launcher bash
so the verb requires no container and no orchestrator import. `_infer_axis`
mirrors `_infer_build_lint_test()`'s precedence and family coverage
(§4 *Phase walkthrough*, below) by hand, since the verb cannot import the
orchestrator. `tests/test_config_verb.py`'s per-mode unit tests still run
against a self-contained bash harness (kept in sync with `_infer_axis` by
hand) for speed and isolation, but a separate parity guard in that file
extracts the real `config)` case arm verbatim from the shipped launcher
and diffs its inference output against `_infer_build_lint_test()` across
a fixture matrix, so the launcher inferrer can no longer silently diverge
from the Python table.

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
| Linux (any distro with containerd) | containerd native | `nerdctl` from distro or upstream | No |

The launcher detects `uname -s` and runs the right preflight. On macOS:
require `colima` on `PATH`, check `colima status`, auto-install the
`nerdctl` shim if missing (via `colima nerdctl install`), then check
`nerdctl info` reaches the runtime. On Linux: require `nerdctl` on
`PATH` and `nerdctl info` succeeds. Both paths print a copy-pasteable
install hint on failure and exit non-zero — leerie does not invoke
`brew`, `apt`, `dnf`, or `pacman` itself.

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
  `default-libmysqlclient-dev`). The build tools and
  dev headers cover native-extension compilation: `node-gyp` (sharp,
  bcrypt), Ruby C gems (`nokogiri`, `pg`, `sqlite3`, `mysql2`, `ffi`), and
  Python C extensions.
- `libc6` + `chromium` + `chromium-driver` + `fonts-liberation` — headless
  Chrome for browser-based testing (Selenium, Capybara, Playwright, Puppeteer,
  or any tool that drives a real browser). Installed from Debian's own repos at
  image build time so the browser and chromedriver versions are always in sync;
  Selenium Manager has nothing to download at runtime.
  `libc6` is listed explicitly so apt upgrades it in the same transaction as
  chromium: the `debian:13-slim` base image snapshot can lag the current trixie
  glibc, causing chromium to fail at load time with
  `undefined symbol: localtime64_r (fatal)` — a glibc ABI mismatch that
  produces a SIGTRAP before Chrome executes a single instruction.
  `/home/leerie/.cache/selenium` is pre-created and chowned to `leerie` so
  Selenium Manager cache writes don't fail even if a download is attempted.
  Workers run as the non-root `leerie` user — Chrome's SUID sandbox won't work
  in this container configuration; the required flags are baked in via
  `/etc/chromium.d/leerie-container-flags` (see *Browser-based testing* note
  below).
- Node.js LTS, arch-aware via `TARGETARCH` / `dpkg --print-architecture`
  → `arm64` → `linux-arm64` tarball, `amd64` → `linux-x64`. Pinned via
  `ARG NODE_VERSION` so the version is reproducible across builds.
- `pnpm` (pinned), `npm install -g @anthropic-ai/claude-code` (the
  `claude` CLI workers invoke; leerie enforces ≥ 2.1.22 at runtime).
- Non-root `leerie` user created with `--build-arg HOST_UID/HOST_GID`
  matching the host user. This is what makes files the container
  writes into `/work` (worktrees) and `/leerie-state` (run state) keep
  the host user's ownership.
- `git config --system --add safe.directory '*'` is set in the image
  (writes to `/etc/gitconfig`). The container is single-tenant (one
  user) and `/work` is its only repo, so blanket-allow is the standard
  mitigation — Colima/virtiofs presents `/work`'s mount-root inode with
  a gid that does not match the in-container `leerie` user, which trips
  git's CVE-2022-24765 check on worker bash tools that run
  `git -C <worktree-subdir> ...`. Without the relaxation, those calls
  return non-zero with
  `fatal: detected dubious ownership in repository at '/work/.leerie/...'`.
  System-wide config (vs. per-user `--global`) avoids any HOME-handling
  risk from `su leerie -c "git config --global"` and matches the posture
  of every major CI image.
- `WORKDIR /work`, `ENTRYPOINT ["/opt/leerie-image/scripts/container-entry.sh"]`.
  **No `USER leerie` directive** — ENTRYPOINT runs as PID 1 at the
  slice-owning identity (real root rootful; the rootlesskit-mapped host
  UID rootless) so the entrypoint can create the
  `/sys/fs/cgroup/leerie.slice` cgroup and launch the **cgroup broker**
  (which performs per-worker enrollment/limit-setting the dropped-privilege
  orchestrator cannot) before dropping privilege via
  `runuser -u leerie -- ...` to invoke the orchestrator (the `runuser`
  drop is skipped in rootless mode — DESIGN §6 *Rootless exception*).
  See DESIGN §6 *Memory containment* for the full mechanism.

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

**Rebuild triggers** (checked in order): (1) `nerdctl image inspect "$REPO_IMAGE_TAG"` fails, OR (2) `<LEERIE_VERSION>:<sha256>` of the current Dockerfile differs from the stored hash. Second run with unchanged Dockerfile hits the skip path ("per-repo image up-to-date; skipping build"). Before the build fires, `ensure_base_in_buildkit_ns` copies the base into the `buildkit` namespace (idempotent) so the derived `FROM $BASE_IMAGE` resolves against the local image store rather than the registry.

**Auto-generation from `setup_packages`**: when `.leerie/config.toml` declares `setup_packages` and no `.leerie/Dockerfile` exists, the launcher generates an apt-install Dockerfile at `.leerie/Dockerfile` (atomic write via temp file + `mv`) before the build-decision block. A committed Dockerfile always takes precedence — `setup_packages` is ignored when both exist.

**`nerdctl run` image arg**: `"${REPO_IMAGE_TAG:-$IMAGE_TAG}"` — falls back to the base image transparently when no repo Dockerfile is present.

### Registry publish path (fly.io / remote Machines)

Fly.io Machines pull an image from a registry rather than using a
locally-built image. The `HOST_UID/HOST_GID` coupling exists only for
local bind-mounts (so files written by the container into `/work` keep
the host user's ownership). Remote Machines have no such bind-mount, so
the Dockerfile's defaults (`ARG HOST_UID=501 / HOST_GID=20`) are used
as-is — no UID matching required.

**Baked source.** The Dockerfile's `COPY` instructions bake
`orchestrator/`, `scripts/`, `prompts/`, and `.claude-plugin/` into the
image at `/opt/leerie-image/`. A Fly Machine that pulls this image can
run the orchestrator without any bind mount — the ENTRYPOINT
(`/opt/leerie-image/scripts/container-entry.sh`) and the orchestrator
(`/opt/leerie-image/orchestrator/leerie.py`) are already present. On
local runs the launcher's `-v $LEERIE_REPO:/opt/leerie-image:ro` bind
mount shadows the baked copy, so development iteration (edit a file,
run leerie) still works without rebuilding the image.

`scripts/remote/build-push.sh` provides the build-and-push path. By
default it uses Fly's remote builder (no host Docker daemon required):

```bash
# Default: Fly's remote builder builds + pushes (recommended):
./scripts/remote/build-push.sh --app <fly-app-name> --push

# Verify the baked source works inside a Machine:
flyctl machine run registry.fly.io/<fly-app-name>:<VERSION> \
  --app <fly-app-name> \
  -- python3 /opt/leerie-image/orchestrator/leerie.py --version
```

Internally, the remote-builder path runs:

```bash
flyctl deploy --build-only --push --remote-only \
  --app <fly-app-name> \
  --config <tmp-fly.toml> \
  --dockerfile <DOCKERFILE> \
  [--build-arg KEY=VAL ...] \
  --image-label <VERSION>
```

`<DOCKERFILE>` defaults to `$LEERIE_REPO/Dockerfile`; pass `--dockerfile
<path>` to override (used by `ensure_image()` for per-repo images). Pass
`--build-arg KEY=VAL` one or more times to forward build arguments to
flyctl; this flag is repeatable and accumulated before forwarding.

The `<tmp-fly.toml>` is a copy of the repo's `fly.toml` with the
`[build] image = "..."` line stripped. That line is correct for
`flyctl machine run` (leerie uses it elsewhere) but wrong for
`flyctl deploy --build-only`: it tells flyctl "the image already
exists, fetch it" → flyctl skips the build step → deploy fails with
"Could not find image" ([flyctl#1686](https://github.com/superfly/flyctl/issues/1686)).
The awk-based strip works around it.

**Opt-in: `--local-build`** (or `LEERIE_LOCAL_BUILD=1`). Builds with
host `nerdctl`/`docker` and pushes from the host. Requires a working
Docker daemon authenticated to `registry.fly.io`. Does NOT work with
nerdctl-in-Colima on macOS — nerdctl reads `~/.docker/config.json`
but cannot resolve `credsStore: desktop` (no access to macOS
Keychain from inside the Lima VM). Documented in INSTALL.md for
completeness; most users should leave it off.

#### Auto-publish on first remote run (`ensure_image()` in the launcher)

A remote run requires the image at `$FLY_IMAGE_TAG` to already exist in
`registry.fly.io`. Without auto-publish the operator must run
`scripts/remote/build-push.sh --push` once before the first remote run,
and again after every version bump — otherwise `flyctl machine run`
fails at provision time with an unfriendly "manifest unknown" error.

The launcher closes that gap with `ensure_image()` in the `RUNTIME=fly`
branch, run before `provision_machine`. Two variants:

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
detects `.leerie/Dockerfile`, computes a 12-character hex hash of its
content, and sets `LEERIE_FLY_IMAGE=registry.fly.io/$APP:$VERSION-$HASH`.
`resolve_fly_image_tag()` returns that value (via the existing
`LEERIE_FLY_IMAGE` override hook). `ensure_image()` then:

1. Cache check on the per-repo tag — skip if already in
   `published-tags.txt`.
2. Ensure the base image is published: check the cache for the base tag
   (`registry.fly.io/$APP:$VERSION`); on miss, invoke build-push.sh for
   the base image first and cache the result.
3. Build and push the per-repo image: invoke build-push.sh with
   `--dockerfile $USER_REPO/.leerie/Dockerfile --build-arg
   BASE_IMAGE=registry.fly.io/$APP:$VERSION --tag <per-repo-tag>`.
4. Append the per-repo tag to the positive cache.

The per-repo tag format is `registry.fly.io/$APP:$VERSION-$HASH` where
`$HASH` is the first 12 hex characters of `sha256($LEERIE_DOCKERFILE)`.
A rebuild fires automatically when the Dockerfile content or the leerie
version changes — the hash changes, a cache miss occurs, and
ensure_image re-runs build-push.sh.

Results are cached at `$XDG_CACHE_HOME/leerie/published-tags.txt` (default
`~/.cache/leerie/published-tags.txt`), one line per `<tag>` known to be
present. Cache hits skip the probe entirely; cache misses fall through
to the probe and on success append the tag. The cache is a *positive*
list only — a missing entry means "probe", not "absent" — so manual
`flyctl image` deletions are self-healing on the next run.

Flags:

| Flag | Env | Default | Effect |
|---|---|---|---|
| `--no-auto-publish` | `LEERIE_NO_AUTO_PUBLISH=1` | off | Skip the probe entirely; trust the operator to have published the image. The run still proceeds; if the tag is missing, `provision_machine` fails as before. |

The flag is consumed by the launcher and not forwarded to the
orchestrator (same convention as `--no-runtime-install`).

Note the key paths inside the container:

- **`/leerie-state/`** is the run-state directory (state.json, logs,
  worktrees, telemetry). It lives on the host filesystem via the
  `/leerie-state` bind mount (`LEERIE_STATE_HOST_DIR`) and persists
  across container runs. In *local mode*, worktrees land under
  `/leerie-state/runs/<run-id>/worktrees/` — outside `/work`.
- **`/opt/leerie-image/`** is the orchestrator source tree. On local
  runs it is a read-only bind mount of `$LEERIE_HOME` on the host; on
  Fly Machines it is the baked copy from the Dockerfile's `COPY`
  instructions. Both paths resolve identically at runtime — the
  ENTRYPOINT and orchestrator code always live at
  `/opt/leerie-image/{scripts,orchestrator}/`.

The container's PID 1 (the entry script) reads from `.leerie-image/`
and writes to `/leerie-state/` (the state bind mount). Confusing the
two would either break runs (writing to the read-only mount) or
corrupt the install (writing to the source tree).

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
  (`enforce_and_record_cgroup_containment`) stops the run unless
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
| `$(pwd -P)` (user repo) | `/work` | rw | The repo leerie operates on. Git worktrees live here. Writes flow back to the host so `--resume` works across container runs. Run state (`.leerie/`) is mounted separately via `/leerie-state` (see below). |
| `$LEERIE_STATE_HOST_DIR` (resolved host state dir) | `/leerie-state` | rw | *Local mode only.* Leerie run state (state.json, runs/, logs/, worktrees/). Mounted at a top-level container path distinct from `/work` so the repo checkout stays pristine — no `.leerie/` dir accumulates inside the project. The orchestrator reads the container path from `LEERIE_STATE_DIR=/leerie-state` (passed as `-e` in the same `nerdctl run` invocation). `LEERIE_STATE_HOST_DIR` is resolved on the host by the launcher before launch; see §2 "Host-side per-repo state directory". |
| `$LEERIE_HOME` (leerie install dir) | `/opt/leerie-image` | ro | *Local mode only.* Orchestrator source + Dockerfile + prompts. Read-only because the container has no business mutating the install. Shadows the baked COPY layer so edits to `orchestrator/leerie.py` take effect without an image rebuild. Absent in registry / fly.io mode — the baked COPY layer is used directly. |
| `$STAGE/.claude.json` (per-run host scratch) | `/home/leerie/.claude.json` | rw | Per-container copy of `~/.claude.json` with the `projects[]` block stripped. The host file is never directly mounted into a container: the shared mount is a documented `claude-code` corruption race (anthropics/claude-code issues #28847, #29217, #29395, #40226 — all open) that hangs workers in a recovery loop with no backoff. Each container writes only its private copy. |
| `$STAGE/.claude` (per-run host scratch) | `/home/leerie/.claude` | rw | Per-container copy of `~/.claude/` with bulky, prior-session, and history paths skipped (`history.jsonl`, `projects/`, `sessions/`, `tasks/`, `plans/`, `todos/`, `file-history/`, `paste-cache/`, `shell-snapshots/`, `session-env/`, `telemetry/`, `stats-cache.json`, `debug/`, `downloads/`, `backups/`, `chrome/`, `ralph-state/`, `.last-cleanup`, `settings.json.*`, `plugins/cache/`, `plugins/marketplaces/`). CLI capability dirs (`agents/`, `skills/`, `commands/`, `hooks/`, `plugins/installed_plugins.json` + sibling JSON, `mcp-needs-auth-cache.json`, `settings.json`, `local/`, `statsig/`, `cache/`, `package.json`, `policy-limits.json`) ride along. `plugins/cache/` and `plugins/marketplaces/` are rebuilt on the remote in the fly runtime; see `scripts/remote/seed-auth.sh` step 4 (`# --- 4. Rebuild plugin cache`). |
| `_extract_claude_credentials_json` → `$STAGE/.claude/.credentials.json` | `/home/leerie/.claude/.credentials.json` | rw | The launcher's `_extract_claude_credentials_json` helper resolves "where do Claude OAuth credentials live on this host" via a single fallback chain — Keychain (service `Claude Code-credentials`, via `security find-generic-password -w`, macOS only), then `$HOME/.claude/.credentials.json` on disk, then `$CLAUDE_CODE_OAUTH_TOKEN` synthesized into the same JSON shape — and writes it to the staged path with mode 600. The Linux CLI reads exactly that path, so both platforms use the same file-based auth flow inside the container. Single source of truth: the same helper is called from the `--chain` arm to populate `LEERIE_WORKER_ENV_JSON`'s `LEERIE_CLAUDE_CREDS_B64` key (base64-encoded), so chain workers receive identical credentials without a separate Keychain probe. |
| `$STAGE/.gitconfig`, `.gitconfig.local`, `.gitignore`, `.gitignore_global`, `.git-credentials`, `.netrc` (per-run host scratch) | `/home/leerie/.<same>` | rw | Per-container copies of each present host `~/.git*` sibling and `~/.netrc`. Worker can `git config --local` / mutate freely without affecting host state. |
| `$STAGE/.config/git` (per-run host scratch) | `/home/leerie/.config/git` | rw | XDG-style git config (`~/.config/git/config`, `~/.config/git/ignore`) copied per-container. |
| `$STAGE/.ssh` (per-run host scratch) | `/home/leerie/.ssh` | rw | Per-container copy of `~/.ssh/` with `agent/`, `S.*`, and `*.sock` excluded — host UNIX sockets aren't reachable from inside the container and `cp -a` on them is pointless. Keys and `known_hosts` ride along so workers can SSH-push if needed. Permissions set to `0700`. |
| `$STAGE/.gnupg` (per-run host scratch) | `/home/leerie/.gnupg` | rw | Per-container copy of `~/.gnupg/` with agent socket files (`S.gpg-agent*`, `S.scdaemon`, `S.keyboxd`) excluded and `use-keyboxd` stripped from `common.conf` (the container cannot reach the host keyboxd daemon; stripping the directive makes gpg fall back to file-based `pubring.kbx` lookup — on keyboxd-only hosts signing keys become unfindable, which is acceptable since commit signing is best-effort). Keyrings + `trustdb.gpg` ride along so workers can `git commit -S` if signing is configured. Permissions set to `0700`. |
| `$STAGE/.aws` (per-run host scratch, **Bedrock mode only**) | `/home/leerie/.aws` | **ro** | Staged when `detect_bedrock_mode()` finds `CLAUDE_CODE_USE_BEDROCK` set to a truthy value (`1`, `true`, `yes`, or `on`, case-insensitive — matching Claude CLI's `isEnvTruthy`) in the `env` block of any of the three settings files the Claude CLI merges (`~/.claude/settings.json` (userSettings), `<USER_REPO>/.claude/settings.json` (projectSettings), `<USER_REPO>/.claude/settings.local.json` (localSettings)). The Claude CLI's AWS SDK resolves credentials via pure file I/O — reads `~/.aws/config` (profile + SSO session config) and `~/.aws/sso/cache/*.json` (SSO access tokens, ~12 h TTL) directly; no `aws` binary is needed inside the container. `~/.aws/cli/cache` is excluded (CLI result cache; large, irrelevant to auth). Mounted **read-only** because workers never write credentials. The `aws` binary (`awsAuthRefresh`) is a host-only concern: `aws sso login` requires an interactive TTY/browser and cannot run inside a non-interactive container; `bedrock_preflight()` catches an expired SSO token on the host before the container starts and prints the recovery hint (`aws sso login --profile <profile>`). On the Fly.io path, `$STAGE/.aws/` is included in the tar pipe to `seed_auth` automatically (`.aws` is not in the seed-auth exclude list) and lands at `/home/leerie/.aws/` on the remote machine. Belt-and-suspenders: when Bedrock mode is active, the launcher also injects `CLAUDE_CODE_USE_BEDROCK=1`, `AWS_PROFILE`, and `AWS_REGION` as explicit env vars — via `AUTH_MOUNTS` `-e` flags on the local nerdctl path and via `child_env` in the Fly detached-launch heredoc — so workers activate Bedrock through `process.env` independently of how the in-container claude binary handles `settings.json` env blocks. |

The four host-auth mounts (`~/.config/gh`, `~/.git-credentials`, `~/.ssh`,
`$SSH_AUTH_SOCK`) that earlier versions of leerie bind-mounted **no longer
exist** — finalize moved to the host (DESIGN §6 *Finalization*), so
`git push` and `gh pr create` run with the host's working auth state and
don't need to be forwarded into the container. The macOS-only "SSH agent
forwarding is not available" note is gone for the same reason.
| `~/.cache/leerie/mise-data` | `/home/leerie/.local/share/mise` | rw | Mise's `MISE_DATA_DIR` (per-repo runtime installs, plugins, cache). Lives in the user dir so the resolver checks it first then falls through to the image-baked `MISE_SYSTEM_DATA_DIR=/usr/local/share/mise` for the LTS fallback (DESIGN §6½). |
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
unworkable for non-trivial trees. Empirically (2026-06-02), a
~1.7 GB / 120k-file working tree (`~/src/enric/stackpulse` with
`node_modules`, `.next`, `.pnpm-store`) hung indefinitely under v1's
plain rsync. The same repo's bundle is ~600 KB and ships in one pipe
in under a second. Gitignored build artifacts stay on the host; the
inspect-bucket workers only need source.

Resume probe: before the bundle phase, `seed_inspect_dirs` runs one
`flyctl ssh console -C "test -d /inspect/<base>/.git"` per inspect
dir. If the directory was already seeded on a prior run, the bundle
is skipped and only the dirty delta refreshes — typical resume cost
is a few seconds per inspect dir, not a few minutes. New inspect
dirs added at `--resume` time take the full fresh path.

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
   dirs are re-resolved fresh on every run including `--resume`
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
(`/home/leerie/.cache/selenium`) is pre-created and chowned to `leerie` so
Selenium Manager cache writes succeed if it ever runs.

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
  clarification path (`surface_clarification()`) both detect this and trigger
  the canonical no-TTY signal: write `<state-root>/runs/<run-id>/pending-questions.json`
  to disk and `sys.exit(EXIT_NEEDS_ANSWERS)` (= 10).
- `<state-root>/runs/<run-id>/pending-questions.json` is visible on the
  host because `/leerie-state` is bind-mounted from `LEERIE_STATE_HOST_DIR`.
  The plugin agent at `commands/leerie.md` reads it directly, asks the
  user via the chat UI, writes the matching `<state-root>/answers.json`,
  and re-runs the container with `--answers <state-root>/answers.json`
  and `--resume`.
- Stdout/stderr stream back through the Bash tool to the agent's
  chat session — possibly in 30s-ish chunks per the harness's
  buffering, which is acceptable for the streaming UX.
- The kernel teardown guarantee applies the same way as in terminal
  mode: when the orchestrator exits (clean exit, exit 10, or any
  signal the harness sends), PID 1 dies and the namespace is reaped.

Common to both modes:

- `--rm` removes the stopped container automatically so they don't
  accumulate. Worktrees and state on the bind-mounted host
  filesystem survive for `--resume`.
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
run-dir flock — every later `--resume` then exits `EXIT_LOCKED=75`
(DESIGN §6). Two launcher mechanisms close this:

- **Kill-on-exit trap.** INT/TERM traps on the local run path
  `nerdctl kill` the container (via its run-id, which equals the
  container ID — see *Single-owner enforcement*) before the launcher
  exits, and the EXIT trap performs the same kill *before* it removes
  the cidfile. Reliable for Ctrl-C/SIGTERM; does NOT help under
  SIGKILL/OOM (uncatchable) — that is the reaper's job.
- **Stale-container reaper.** On the local `--resume` path, before the
  `nerdctl run` spawn, the launcher looks up any container whose ID
  equals the resume run-id (`nerdctl inspect`), and if it is still
  running but its owning launcher (`leerie.launcher_pid` label) is dead
  (`kill -0` fails), `nerdctl kill`s it first — making `--resume`
  self-heal the orphaned-flock wedge instead of returning 75.
- **Decoupled output streaming (piped mode only).** In piped mode
  (`leerie … | tee log`, i.e. `TTY_FLAGS="-i"` and stdout is not a TTY),
  the launcher does NOT let `nerdctl run` write straight to its stdout
  pipe. Colima's persistent SSH ControlMaster forwards the run's stdout
  and holds a copy of the launcher's pipe write-end; on an abnormal
  container exit it retains that copy, so `tee` never gets EOF and the
  launcher hangs (orphaning the container). Instead the launcher points
  `nerdctl run > "$_run_log" 2>&1` (a regular file — the mux does not
  retain a plain-file fd) and starts `tail -n +1 -f "$_run_log"` in the
  background, streaming the file to its own stdout. `_reap_tail` (called
  after the run and from all three EXIT/INT/TERM traps) briefly sleeps so
  `tail` drains the final write, then `kill`s it and `rm`s the log — no
  post-kill `cat`, which would duplicate the whole log. The `nerdctl`
  argv is assembled once into a `_run_argv` array and invoked in two
  spelled-out branches (redirected vs. direct) because bash cannot build
  a redirection through variable expansion. Container exit-code capture
  (`|| container_rc=$?`) is unaffected — `> file` is not a pipe.
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
│   │                              load_prompt's {{include: …}} expansion
│   ├── classifier.md              Phase 1 worker system prompt
│   ├── planner.md                 Phase 2 worker system prompt
│   ├── reconciler.md              Phase 2½ worker — resolve cross-domain
│   │                              capability-tag drift between planners
│   ├── provision.md               §6½ LLM-fallback install-recipe worker
│   ├── implementer.md             Phase 5 implementer worker system prompt
│   ├── conformer.md               Phase 5 post-work conformance worker (DESIGN §9)
│   ├── integrator.md              conflict-resolution worker system prompt
│   ├── pr_writer.md               Phase 6 PR title + body author worker
│   ├── patch_generator.md         post-run self-heal worker — proposes minimal
│   │                              system-prompt patches against failing call_types
│   └── judge.md                   LLM judge worker — 3-dimensional rubric for
│                                  reviewing captured call records
├── scripts/
│   ├── setup-run.sh               create per-run branch + worktree (idempotent)
│   ├── new-worktree.sh            create/reuse a per-subtask worktree (per-run scoped)
│   ├── integrate.sh               merge a subtask branch into the per-run branch
│   ├── finalize.sh                verify the run branch exists and is non-empty; ready for push
│   ├── host-finalize.sh           host-side push + PR creation block; sourced by
│   │                              the local-runtime post-run path in leerie,
│   │                              decide_teardown's Fly clean-exit branch, and
│   │                              `leerie --finalize <run-id>` (§7 Host-side finalize)
│   ├── cgroup-broker.py           cgroup broker, runs at the slice-owning identity (create/enroll/destroy over a Unix socket; v1+v2); the dropped-privilege orchestrator drives it
│   ├── cleanup.sh                 remove worktrees / branches (default: scoped to one run)
│   ├── container-entry.sh         container PID 1 (root rootful / mapped-UID rootless): create leerie.slice + launch cgroup broker + cd /work + drop to leerie via runuser (rootful)
│   ├── install.sh                 one-command installer (curl | bash); preflight git/claude/curl +
│   │                               runtime preflight (colima / nerdctl) + clones + symlinks
│   ├── runtime-install.sh         per-OS auto-install of the container runtime (Colima on macOS;
│   │                              containerd + nerdctl on Debian / Fedora / Arch). Sourced by
│   │                              install.sh and the launcher.
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
│       │                           `leerie --re-seed <run-id>` and auto on `--resume`
│       ├── seed-auth.sh           Worker auth + config seeding (sourced by launcher after
│       │                           provision_machine() returns); seed_auth() tar-pipes
│       │                           ~/.claude.json + ~/.claude/ (minus .claude/local) + git identity
│       │                           to /home/leerie/ via `flyctl ssh console -C "tar -xC ..."`,
│       │                           then pre-warms `claude --version` for orchestrator preflight
│       ├── seed-repo.sh           Two-phase bundle + delta repo seeding (sourced by launcher after
│       │                           provision); seed_repo(): git bundle parent + submodules
│       │                           piped via ssh-console → machine clones from bundles on disk,
│       │                           then rsync's dirty delta + .claude/ — no in-machine git clone
│       ├── collect-subtrees.sh     Subtree collection (sourced by `leerie --finalize`);
│       │                           collect_subtrees_remote(): SSHes a bash payload that runs
│       │                           setup-run.sh + integrate.sh for un-merged subtask branches
│       │                           on the machine; conflicts are skipped and reported via sentinels
│       └── fetch-branch.sh        Post-run stream-back (sourced by decide_teardown BEFORE
│                                   destroy_machine on clean exit, and by `leerie --finalize`);
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
│   │                              `--chain` arm. The laptop drives everything; no Fly
│   │                              coordinator machine.
│   ├── __init__.py                exports __version__ = "0.1.0"
│   ├── _log.py                    log()/die() helpers — shared with git_ops.
│   └── git_ops.py                 synth_merge_branches (used between waves) +
│                                  clone_target / fetch_branch / push_branch / open_pr /
│                                  finalize_run / write_audit_artifact (kept for tests
│                                  and future automated paths).
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
# pass the run-id otherwise (see `leerie --list`).
leerie --resume
leerie --resume bugfix-login-timeout-bug-b81e90

# List in-flight and completed runs in this repository:
leerie --list

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

# Choose the model. Without overrides: judgment workers (classifier,
# planner, reconciler, plan_overlap_judge, provision, integrator) default
# to opus; acting workers (implementer, conformer) default to sonnet.
# Use the env var
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

# Judge and heal model overrides (default: sonnet for throughput):
leerie "task" --judge-model opus --heal-model opus
export LEERIE_MODEL_JUDGE=sonnet
export LEERIE_MODEL_HEAL=sonnet

# Heal-loop convergence knobs (defaults shown):
leerie "task" --heal-max-rounds 10 --heal-success-threshold 0.9
export LEERIE_HEAL_MAX_ROUNDS=10
export LEERIE_HEAL_SUCCESS_THRESHOLD=0.9

# Diagnostic toggle for the next silent-hang reproduction. When set,
# every `claude -p` worker subprocess inherits DEBUG=* and
# ANTHROPIC_LOG=debug so its internal state surfaces on stderr — the
# idle watchdog (worker_idle_warn_sec, see §Caps) then flushes a tail
# of that stderr alongside its silence warning. Off by default because
# verbose CLI logging is noisy on healthy runs.
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
leerie --chain \
  --wave prompts/fetch.txt,prompts/lint.txt \
  --wave prompts/publish.txt

# ID-dispatched verbs (UUID → chain scope; Fly machine id → run scope).
# Chain-scope verbs iterate $LEERIE_STATE_HOST_DIR/runs/*/run.json
# filtered by chain_id, dispatching the existing single-run verb per
# discovered run.
leerie --status   <chain-id>        # render per-run states from run.json
leerie --attach   <chain-id>        # poll run.json files every 5s
leerie --stop     <chain-id>        # pause every running chain run
leerie --kill     <chain-id>        # destroy every chain run's machine
leerie --resume   <chain-id>        # resume paused + list running chain runs
leerie --finalize <chain-id>        # push + open PR for every unpushed run
leerie --list --chains              # group runs by chain_id

# Deprecated chain-prefixed aliases shim to the new verbs:
#   --chain-submit → --chain
#   --chain-status → --status
#   --chain-kill   → --kill
#   --chain-attach → --attach
#   --list-chains  → --list --chains
```

Requirements: the `claude` CLI on `PATH` and logged in interactively (no API
key — subscription auth); `git`; a git repository with `user.email` and
`user.name` configured; a container runtime (colima on macOS, nerdctl +
containerd on Linux — see `docs/INSTALL.md`). Python is provisioned inside
the container by the image (Debian 13's `python3` 3.13); the host does not
need Python. The launcher's `--version` fast path returns without starting
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

By default, judgment workers (classifier, planner, reconciler,
plan_overlap_judge, provision) run in the real repo cwd with a narrow
Bash allowlist (`INSPECT_TOOLS`) and **without**
`--dangerously-skip-permissions`.
This mechanically prevents them from mutating state — the §12
enforcement that a planner cannot run `pnpm run typecheck`,
`tsc --noEmit`, or any other side-effecting subprocess. Acting workers
(implementer, conformer, integrator) run in isolated worktrees with
the broader `ACT_TOOLS` allowlist and the skip-permissions flag —
their blast radius is bounded by the worktree.

`--dangerously-skip-permissions` is the escape hatch. When set, every
`claude -p` invocation — including judgment workers in the real repo
cwd — is invoked with `--dangerously-skip-permissions`. This waives
the §12 mechanical read-only enforcement on judgment workers and
shifts trust onto their prompts. Use it on repositories where the
planner needs to observe build/test tooling that the narrow inspect
allowlist excludes — Node/TS repos where the planner reflexively
reaches for `pnpm`/`tsc`/`biome`/`vitest`/`npx` and currently
~18-19% of its Bash calls fail with "requires approval" in headless
mode. See DESIGN §12 and §15 *Known limitations* (the "unattended
execution requires broad write permission" paragraph) for the
guarantee being waived.

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
completed/no-work resumes are not gated), `enforce_and_record_cgroup_containment`
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
immediately after `schedule()` returns its `(subtasks, waves)` pair
and before `write_plan()` persists anything. It estimates the
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
judge), so the only free variable is the per-subtask multiplier.
Calibration corpus (six runs on disk as of 2026-06-03; three completed
runs cluster at 2.0–2.31 calls/subtask, summarizer's lint-fighting
inflator pushed it to 2.59 — see prompts/{implementer,conformer}.md
§"Environmental issues are out of scope"). Default `2.5` is honest:
covers the worst observed real ratio, and the explicit `1.15` safety
multiplier on top means the guaranteed headroom against the cap is
~1.44×.

Resolution order for the opt-out (highest priority first):

1. **`--skip-budget-check`** CLI flag (action=`store_true`).
2. **`LEERIE_SKIP_BUDGET_CHECK`** environment variable
   (`_parse_bool_envtoml`).
3. **`leerie.toml` at the repo root** with `skip_budget_check = true`.
4. **Default `False`.** The check runs.

Skipped on `--resume`: the resume path enters `_run_phases` past
`schedule()` (the `waves` field is loaded from `state.json`), so the
preflight has nothing to gate. A run that died on the preflight is
not resumable — `--resume` would re-fire the same check with the same
inputs and die identically. The user re-runs with the recommended
`--max-workers` value or splits the task.

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
value" — distinct from the code-10 hint which suggests `--finalize`,
because a budget-infeasible run has no work to finalize and `--resume`
would die at the resume guard (no `waves` field in `state.json`).
This routing keeps the user from paying for a Fly volume indefinitely
on a structurally-unrecoverable run.

### Single-owner-per-run-dir enforcement

DESIGN §6 *Single owner per run dir*. The orchestrator refuses to
start a second instance against a run directory that another
orchestrator already owns. Two code-surface elements implement this:

- `EXIT_LOCKED = 75` constant in `orchestrator/leerie.py`. Emitted
  via `sys.exit(EXIT_LOCKED)` (not `die()`) so the prefix is not
  `leerie: error:` — same shape as `EXIT_NEEDS_ANSWERS`'s
  structured non-error exit, since refused-resume is a routing
  signal, not an error. Caller-side handlers print a
  `leerie --resume <run-id>` hint via `log()` before exiting (the
  launcher's smart-router will then attach to the live stream
  rather than spawn a duplicate).
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
- `State.save` is unchanged in body. The flock is on the run
  directory inode, not the state.json inode, so the
  `tmp.replace(self.path)` swap inside `save()` does not affect the
  lock. Docstring updated to make this explicit.
Two checked construction sites that catch `StateLockedError`:

- `main()` at the `State(leerie_root, run_id, repo_root=repo_root)` call:
  logs the message + `sys.exit(EXIT_LOCKED)`.
- `--phase judge|heal` at the `phase_st = State(...)` call: same
  pattern, since `--phase` mutates state and would race the same
  way `--resume` would.

The launcher heredoc (`leerie:2679-2696`) takes a fast-path flock
probe on `run_dir` before invoking the orchestrator subprocess. On
`BlockingIOError` the probe exits 75. The probe is advisory — the
orchestrator's `State.__init__` flock acquire is the load-bearing
enforcement that catches any path bypassing the launcher (manual
`python3 leerie.py --resume`, future verbs, debugging).

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

The `--resume` smart router's auto-discovery scan honors
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

The check is skipped for `--version`, `config`, and the `--chain-*` verbs (those
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
`fly` routes each worker through Fly.io machines. `ec2` is accepted as a
resolvable enum value (AWS credentials resolve the same way the AWS
CLI/SDKs do — see `scripts/remote/aws-credentials.sh` and `ec2-lib.sh`'s
`require_aws()` preflight); the instance create/wait-ready/teardown
lifecycle itself has shipped (`scripts/remote/ec2-provision.sh` — see
the Files table above). The launcher's `RUNTIME=ec2` branch now exists
and sources both `aws-credentials.sh` and `ec2-lib.sh`, first calling
`resolve_aws_credentials` (`--profile`/`--region` from
`LEERIE_AWS_PROFILE`/`LEERIE_AWS_REGION` when set) and exporting its
resolved credentials/region into the launcher's environment, then
gating on `require_aws()` — mirroring the `RUNTIME=fly` branch's
`require_flyctl` sequencing (`tests/test_ec2_e2e_provision.py` pins
the ordering: `resolve_aws_credentials` precedes `require_aws`'s `sts
get-caller-identity` call, which in turn precedes any `ec2
run-instances` call; a failing credential probe — including an
unresolvable chain caught by `resolve_aws_credentials` itself, e.g. an
expired SSO token — aborts with the `aws sso login --profile <p>`
hint before any AWS resource is created) — but the branch does not yet
dispatch to `ec2-provision.sh`'s
`provision_instance()`/instance lifecycle, and the SSM/SSH transport
(`ec2-ssm.sh`, for seeding and detached-orchestrate) has not shipped —
DESIGN §6 *EC2 runtime lifecycle* is the canonical architecture, and
"EC2 instance-lifecycle vars" below is the code-surface spec the
provisioning-wiring subtask implements against. Default is `local` so
existing behavior is unchanged for users who have not opted in.

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

Code counterparts: `resolve_aws_region()` / `resolve_aws_profile()` in
`leerie.py`, both built on the same `_resolve_str_pref` helper as
`resolve_pr_template`. Constants: `AWS_REGION_ENV`, `AWS_REGION_FILE`,
`AWS_PROFILE_ENV`, `AWS_PROFILE_FILE`. Argparse flags: `--aws-region`,
`--aws-profile` (both free-form strings, no `choices=`).

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
deny-list — the CLI/TOML tier below feeds the same launcher-only
vars, it does not change who consumes them). No Python-side
`resolve_*()` counterpart exists or is planned — unlike
`AWS_REGION`/`AWS_PROFILE` above, which the orchestrator's
`resolve_aws_region()`/`resolve_aws_profile()` read because they're
leerie-level knobs consulted from inside the container too, these six
are consumed exclusively by the host-side launcher/`ec2-provision.sh`
before any container or instance exists.

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

### Fly app name

Fly.io app names are globally unique. `LEERIE_FLY_APP` is required when
`RUNTIME=fly`; the launcher `die()`s with setup instructions when unset.

Resolution order (highest priority first):

1. **`--fly-app NAME`** / `--fly-app=NAME` CLI flag. Launcher-only
   (stripped from `REWRITTEN_ARGS`; the orchestrator never sees it).

2. **`$LEERIE_FLY_APP`** environment variable.

3. **(none)** — no default, no `leerie.toml` key. Required.

The resolved value is exported as `LEERIE_FLY_APP` and assigned to
`FLY_APP` before any remote script is sourced. Verb paths (`--stop`,
`--kill`, `--finalize`, `--list --runtime fly`, `--re-seed`) validate
independently since they exit before the main resolution gate.

### Prompt loading and the shared filter fragment

Worker prompts are loaded by `load_prompt(name)` in
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
  the conformance dict is written by `settle_subtask` exactly when the
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
(`summarizer`, `pr_writer`, `run_final_conformance`) also emit no prefix:
`_get_progress` returns `None` once `completed_waves >= len(waves)`,
since there is no in-flight wave to count.

`_invoke` takes `progress` as a callable (`Callable[[], tuple[...] |
None] | None`), not a spawn-time snapshot, and calls it per stream
event. This is so a long-running worker's prefix advances as siblings
complete — two workers logging at the same wall-clock instant agree on
the count instead of carrying frozen snapshots from their respective
spawn moments.

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
re-resolved fresh on every run, including `--resume` — the user
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
including `--resume`, so the user can add or remove paths without
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
batch of captured calls. The judge does not require broad-context judgment like
the orchestrator's core workers — `sonnet` is the right default for throughput.

Resolution order (highest priority first):

1. **`--judge-model MODEL`** CLI flag.
2. **`LEERIE_MODEL_JUDGE`** environment variable.
3. **`leerie.toml`**, `model_judge = "sonnet"`.
4. **Default `"sonnet"`** (`MODEL_DEFAULT_PER_WORKER["judge"]`).

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

### PR-writer payload caps

The `pr_writer` worker is invoked by passing its entire user prompt
(task text, classification, subtask titles, full commit log, diff
stat/dirstat, sampled diff, and the PR template body — all serialized
as one JSON string) as a single argv element to `claude -p`. Linux
`ARG_MAX` in the leerie container (Debian 13) defaults to ~128 KB; a
degenerate run with thousands of commits, a huge template, or a
sprawling diff would silently fail with `E2BIG`.

Three constants in `orchestrator/leerie.py` cap the unbounded fields so
the total payload stays well under that ceiling. Each capped field
gets an in-band `... [<label> truncated at ~N KB; remainder omitted —
rely on the commit log] ...` sentinel so the worker can see the
truncation and avoid fabricating detail past the cut-off.

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
etc.). The PR-writer caps are internal protocol limits defending a
single subprocess invocation against an OS-imposed argv ceiling:
lowering them silently degrades summaries and raising them risks
`E2BIG`. `tests/test_pr_writer_payload_cap.py::test_pr_writer_byte_budgets_defined`
pins the values so any future change goes through code review.

Multi-byte UTF-8 safety: `_cap_text` slices at the byte boundary,
then back-decodes with `errors="ignore"` so the trimmed prefix never
ends mid-codepoint. Tested with rocket emojis (U+1F680, four UTF-8
bytes) that would naively split at the chosen byte boundary.

**`final_conformance` payload field** — when `run_final_conformance`
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
mention the cut-off honestly. The cap defends a payload already
sized close to the ~128 KB Linux ARG_MAX limit on the leerie
container.

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
subprocess is resolved per worker type, so the same run can use `opus` for
judgment work and `sonnet` for high-throughput implementation. Valid values:
`sonnet` | `opus` | `haiku` (aliases — the `claude` CLI resolves them to the
current model version).

**Per-worker defaults: Opus for judgment, Sonnet for implementation, post-run analysis, and finalize-time composition.**
Workers that exercise broad-context judgment (classify the task, decompose
into subtasks, reconcile cross-domain coupling, detect cross-planner surface
overlap, resolve merge conflicts behaviorally, check criteria) default to
Opus. The implementer, conformer, judge, heal, and pr_writer workers —
which execute concrete tasks with high throughput requirements (implementer,
conformer) or run as one-shot post-run / finalize calls (judge, heal,
pr_writer) — default to Sonnet.

| Worker       | Default | Why |
|--------------|---------|-----|
| classifier   | opus    | global judgment over the task description |
| planner      | opus    | decomposition is the load-bearing judgment step |
| reconciler   | opus    | cross-domain tag equivalence is judgment |
| plan_overlap_judge | opus | surface-overlap detection over the reconciled plan is judgment (two planners independently extracting the same artifact with incompatible APIs — DESIGN §5 *Cross-domain surface overlap*) |
| satisfied_probe | sonnet | per-subtask "is this already met on the base tree?" check (DESIGN §8 *Already-satisfied subtask elimination*); runs once per subtask so throughput/cost dominates — same profile as conformer/judge. The false-positive risk is contained by the base-tree-only tool scope + conservative-default prompt, not by model tier |
| provision    | opus    | fallback when the deterministic lockfile-detection table returns empty (DESIGN §6½); reads README + configs to emit an install recipe — judgment over arbitrary repo shapes |
| integrator   | opus    | behavioral conflict resolution; a wrong merge silently corrupts integrated state |
| implementer  | sonnet  | concrete subtask execution; Sonnet's throughput is the right tradeoff |
| conformer    | sonnet  | reads a diff and runs commands; same throughput-first profile as implementer; the phase is advisory so a borderline judgment call costs at most a warning |
| judge        | sonnet  | scoring a batch of captured calls; throughput matters more than broad judgment |
| heal (patch) | sonnet  | patch generation and replay; throughput matters more than broad judgment |
| pr_writer    | sonnet  | finalize-time PR title + body; fills repo template when present, summarizes commits otherwise; throughput-shaped one-shot call |
| dep_capture  | opus    | finalize-time dep inference from worker logs; broad judgment over arbitrary shell command sets warrants full-tier reasoning |
| fit_judge    | opus    | P1 Task-Context Fit scoring is judgment; absent from `MODEL_DEFAULT_PER_WORKER` — opus default comes from the global `MODEL_DEFAULT` fallback |
| splitter     | opus    | LLM-driven structural partition (coupled-minority path) is judgment; absent from `MODEL_DEFAULT_PER_WORKER` — opus default comes from the global `MODEL_DEFAULT` fallback |

`MODEL_DEFAULT` is the global default (`opus`); `MODEL_DEFAULT_PER_WORKER`
overrides it for specific workers (`implementer`, `conformer`, `judge`,
`heal`, `pr_writer`, and `satisfied_probe` all default to `sonnet`).
`dep_capture`, `fit_judge`, and `splitter` are **absent** from
`MODEL_DEFAULT_PER_WORKER` — their `opus` defaults come from the global
`MODEL_DEFAULT` fallback.

Resolution order for each worker type `W` (highest priority first):

1. **`--model-<W>`** CLI flag (e.g. `--model-implementer opus`)
2. **`--model`** CLI flag (sets the global default for this run)
3. **`LEERIE_MODEL_<W>`** env var (e.g. `LEERIE_MODEL_IMPLEMENTER=opus`)
4. **`LEERIE_MODEL`** env var (sets the global default)
5. **`model_<w>`** key in `leerie.toml`
6. **`model`** key in `leerie.toml`
7. **Per-worker default** from `MODEL_DEFAULT_PER_WORKER`
8. **Global default `MODEL_DEFAULT`** (`opus`)

Fifteen worker types (plus the global override), each independently overridable:

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

**Cost note:** Opus is materially more expensive than Sonnet. A user who
wants the old all-Sonnet behavior sets `LEERIE_MODEL=sonnet` (or
`--model sonnet`). Per-worker overrides (`--model-planner sonnet`) let
users selectively de-escalate individual workers.

Models are not persisted in `<state-root>/state.json`. On `--resume`, models are
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

**Per-worker defaults: `high` for judgment workers, unset for acting workers.**
Judgment workers (classifier, planner, reconciler, plan_overlap_judge,
provision, integrator) default to `high`. The acting workers (implementer,
conformer) and post-run skill workers (judge, heal) default to *unset* —
when no effort is resolved,
no `--effort` flag is passed and the worker inherits Claude's default. This
keeps acting workers' reasoning bounded by their own evidence gates
(DESIGN §8) rather than by a global dial.

| Worker       | Default | Why |
|--------------|---------|-----|
| classifier   | high    | category choice is judgment over the whole task |
| planner      | high    | decomposition granularity is the load-bearing judgment step (DESIGN §8 planner gate) |
| reconciler   | high    | cross-domain tag equivalence is judgment |
| plan_overlap_judge | high | surface-overlap detection over the reconciled plan is judgment (DESIGN §5 *Cross-domain surface overlap*); merge-feasibility discipline rewards pinning reasoning depth |
| satisfied_probe | unset | per-subtask advisory prune (DESIGN §8 *Already-satisfied subtask elimination*); runs once per subtask, same unset profile as conformer/judge — the base-tree-only tool scope and conservative default carry the correctness, not pinned depth |
| provision    | high    | recipe synthesis over arbitrary repo shapes is judgment |
| integrator   | high    | behavioral conflict resolution; a wrong merge corrupts state |
| implementer  | unset   | bounded by §8 evidence gate; pinning would override the gate's adaptive depth |
| conformer    | unset   | advisory phase; same reasoning as implementer |
| judge        | unset   | post-run scoring; no need to pin |
| heal         | unset   | post-run patch generation; no need to pin |
| pr_writer    | high    | one-shot finalize call; pin reasoning to keep template-fill discipline (preserve HTML comments, do not invent ticked checkboxes) consistent across runs |
| dep_capture  | high    | finalize-time dep inference; broad judgment over shell command sets benefits from pinned reasoning depth |
| fit_judge    | high    | P1 Task-Context Fit score is judgment over scope+context co-minimization; calibrated threshold (0.70) makes pinned depth the reproducibility dial |
| splitter     | high    | LLM-driven structural partition (coupled-minority path) is judgment over seam detection; wrong split corrupts downstream implementer context |

`EFFORT_DEFAULT` is `None` (meaning "don't pass `--effort`");
`EFFORT_DEFAULT_PER_WORKER` overrides it to `"high"` for the six judgment
workers above and for the finalize-time `pr_writer` and `dep_capture` workers,
and for the P1 decomposition workers `fit_judge` and `splitter`.

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

Efforts are not persisted in `<state-root>/state.json`. Like models, on `--resume`
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

The primary verb is `leerie --chain`. Chain-scoped verbs
(`--status`, `--stop`, `--kill`, `--resume`, `--finalize`,
`--attach`) detect UUID-formatted positional arguments and dispatch
by iterating `$LEERIE_STATE_HOST_DIR/runs/*/run.json` for runs with
matching `chain_id`.

| Verb | Behavior |
|------|----------|
| `leerie --chain [--chain-id <uuid>] [<per-job-flags>] --wave <files> [--wave <files>] ...` (alias: `--chain-submit`) | Wave-sequencer loop. Any flags not consumed by `--chain`'s own parser (`--wave`, `--chain-id`, `--target`) are collected into a passthrough array and forwarded to each per-job `./leerie` invocation — so `--effort high`, `--model opus`, `--dangerously-skip-permissions`, etc. work the same as on a single run. Mints a fresh `chain_id` (UUID) unless `--chain-id <prior-uuid>` is supplied (in which case the prior chain's `chain_id` is reused so the wave-loop idempotency check skips already-pushed waves — see "Chain helpers" subsection below). For each wave N: if every wave-N run is already pushed (`_wave_already_done`), skip fan-out; else checks out `current_base` in `$USER_REPO` and fans out N background `./leerie "$prompt" --runtime fly <passthrough> --chain-id <id>` per prompt file, waits for all to finalize on the laptop (existing single-run path: `provision_machine` → `seed-auth.sh` → `seed-repo.sh` → orchestrator → `decide_teardown` trap → `fetch_branch` → `host_finalize` → `destroy_machine`), tags each finalized `run.json` with `chain_id` + `wave_idx` via `update_run_json`. Either way, gathers wave-N branches via `_wave_branches`, synth-merges into `leerie/stage/<chain-id>-wave-<N+1>` via `chain.git_ops.synth_merge_branches`, pushes the stage branch to origin, advances `current_base`. Trap handler `_ch_kill_wave` propagates SIGINT/SIGTERM to all in-flight wave children. |
| `leerie --status <chain-id>` (alias: `--chain-status`) | Iterates run.json files, filters by `chain_id`, renders one row per matched run (wave, run_id, status, branch, notes). Status derived from run.json fields (`pushed_at` / `paused_at` / `killed_at` / `finished_at`). |
| `leerie --attach <chain-id>` (alias: `--chain-attach`) | Polls run.json files every 5s; exits 0 when every chain run is in a terminal state (`pushed_at` / `paused_at` / `killed_at` / `sync_failed_at`). |
| `leerie --kill <chain-id>` (alias: `--chain-kill`) | Enumerates run.json files with matching `chain_id` whose machines aren't already destroyed (`killed_at` is null), invokes `leerie --kill <run-id>` per discovered run. Idempotent. |
| `leerie --stop <chain-id>` | Enumerates runs that are actively running (have `fly_machine_id`, no terminal state), invokes `leerie --stop <run-id>` per discovered run. |
| `leerie --resume <chain-id>` | Two tiers: (1) auto-resumes paused runs (`paused_at` set, not `killed_at`) by invoking `leerie --resume <run-id>` per discovered run; (2) lists still-running runs (have `fly_machine_id` + `chain_id`, no terminal state) with machine IDs so the user can reattach via `leerie --resume <machine-id>`. Running runs are discoverable because the child writes `chain_id` into host-side `run.json` immediately after provisioning (early-write), before the orchestrator starts. After paused runs complete, the user re-invokes `leerie --chain --chain-id <chain-id> --wave ...` to continue the wave loop from where it stopped. |
| `leerie --finalize <chain-id>` | Enumerates runs that haven't been pushed yet (`pushed_at` null, not `killed_at`), invokes `leerie --finalize <run-id>` per discovered run. |
| `leerie --list --chains` (alias: `--list-chains`) | Iterates run.json files, groups by `chain_id`, renders one row per chain (chain_id, status, pushed/total, wave count, started_at). |

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

The `chain_id` (UUID minted by `--chain`) is written into each
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
| `_wave_already_done <chain_id> <wave_idx> <n_expected>` | UUID, integer, integer | Exits 0 iff `n_expected` runs are tagged with `chain_id` + `wave_idx` AND every matching run has `pushed_at` set. Used by the `--chain` wave loop to skip fan-out on a resume submission. |
| `_wave_branches <chain_id> <wave_idx>` | UUID, integer | Emits one branch-name per line for every matching run. Used by the wave loop to gather wave-N branches for synth-merge (works for both the just-fanned path and the resume path). |
| `_resolve_volume_id_from_run_dir <run-dir>` | Run directory | Emits the run's `volume_id` (or nothing). Reads `fly-machine.json` then `run.json`, **continuing when a file exists but carries no `volume_id`** — `provision.sh` writes `volume_id` to `fly-machine.json` only conditionally (`if vol_id:`) while always writing it to `run.json`, so returning on mere file existence skipped `run.json` and leaked the volume. |
| `_resolve_volume_id_from_fly <machine-id> <app>` | Machine id + Fly app | Emits the volume mounted by that machine, by asking Fly: `flyctl machine list --app <app> --json` → `.[] \| select(.id==<mid>) \| .config.mounts[].volume`. The fallback for `--kill --machine-id <id>` when no sidecar exists (the orphan path the usage hint advertises); without it the machine is destroyed and its volume bills forever. **Must be called before `destroy_machine`** — the volume→machine link (`attached_machine_id`, and the machine's own `config.mounts`) vanishes with the machine. Uses `machine list --json` because `machine status` has **no** `--json` flag (only `-d/--display-config`, which embeds JSON in prose); verified to keep reporting mounts while the machine is `stopped` (the `--stop`-then-`--kill` path). Best-effort: any failure emits nothing and returns 0, so `--kill` still destroys the machine. |
| `_chain_runs_filter <chain_id> <verb>` | UUID, one of `stop`/`kill`/`finalize`/`resume`/`running` | Emits matching run-ids one per line. The `verb` parameter selects a hardcoded filter inside the heredoc (`stop`: machine running; `kill`: not yet destroyed; `finalize`: not yet pushed; `resume`: paused; `running`: active with chain_id, no terminal state). Used by the chain-scoped verb arms (`--stop`/`--kill`/`--finalize`/`--resume`) to enumerate runs for per-run dispatch. Returns rc=2 + `remote_log` error on unknown verb (bash-side assert; Python heredoc has its own `sys.exit(2)` backstop). |

The wave loop's tag-write step (`update_run_json … chain_id "$_ch_id"
wave_idx "$_wave_idx"`) fires BEFORE the failure-pause check so
runs that paused on failure still get tagged and are therefore
discoverable by `leerie --resume <chain-id>` / `--kill <chain-id>` /
etc. The `_ch_wave_pids` / `_ch_wave_child_pids` arrays reset at
the top of every wave iteration (above the `_wave_already_done`
check) so the SIGINT trap handler never sees stale entries from a
prior wave.

##### Resuming a chain via `--chain --chain-id <uuid>`

`leerie --chain --chain-id <prior-uuid> --wave …` pins the chain_id
to a prior chain's UUID instead of minting fresh. The wave loop's
`_wave_already_done` check then matches the prior chain's runs and
skips fan-out for already-pushed waves, advancing `current_base`
through any wave-staging branches already pushed to origin. This is
the load-bearing recovery path after `leerie --resume <chain-id>`
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

1. `leerie --resume <chain-id>` resumes every paused run (existing
   single-run resume per discovered run).
2. After paused runs complete, the user re-invokes
   `leerie --chain --wave ...`. The wave loop's idempotency check
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
| `clone_target(url, pat, dest)`, `fetch_branch`, `push_branch`, `open_pr`, `finalize_run`, `write_audit_artifact` | Kept for compatibility with existing tests and any future automated paths; the wave loop MVP uses only `synth_merge_branches`. |

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
by the `--group` launcher arm after all members complete, via
`update_run_json … group_id "$_group_id"` (the tag-back step in
`leerie`). The `chain_id` field follows the same pattern.
`_validate_run_json`
(`orchestrator/leerie.py:1994`) does not add any invariant check on
`group_id` — it is informational and orthogonal to the push/pause/kill
state machine. The field is accepted by the validator without error
because validators only check fields they know about (unknown keys pass
through).

#### Launcher `--group` verb

```
leerie --group \
  --repo <path> "<prompt>" \
  --repo <path> "<prompt>" \
  [--brief <file>] \
  [<per-member-flags>]
```

Modeled on the `--chain` arm (`leerie:2033`). The `--group` arm:

1. Parses repeated `--repo <path> "<prompt>"` pairs and an optional
   `--brief <file>`.
2. Fails fast if any repo path is not a git repository (mirrors
   the chain prompt-file check at `leerie:2136`).
3. Mints a `_group_id` (UUID, same mechanism as `--chain`'s `_ch_id`).
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
| **Local** | After `wait` on a member, scan `~/.leerie/<member-basename>/runs/*/run.json` for the newest file with `finished_at` set (the `--group` arm's tag-back loop in `leerie`). No cidfile read, no `--rm` race — the `run.json` is durably on disk by the time `wait` returns. |
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
| `leerie --group --repo <path> "<prompt>" [--repo ...] [--brief <file>] [--group-id <uuid>]` | Fan-out launcher. Mints a fresh `group_id` unless `--group-id <prior-uuid>` is supplied. Fans out one backgrounded member invocation per `--repo`, waits, then runs group tag-back. |
| `leerie --status <group-id>` | Iterates member state dirs (derived from the group's member repos or a group-manifest the launcher drops), filters `run.json` by `group_id`, renders one row per matched run (run_id, status, branch, notes). Same field-derived status as chain `--status`. |
| `leerie --stop <group-id>` | Discovers running Fly members across all member state dirs; invokes `leerie --stop <run-id>` per discovered run. Fly-runtime only (pauses machines). |
| `leerie --resume <group-id>` | Discovers paused members across all member state dirs; invokes `leerie --resume <run-id>` per discovered paused run. |
| `leerie --kill <group-id>` | Discovers non-destroyed members across all member state dirs; invokes `leerie --kill <run-id>` per discovered run. Idempotent. |
| `leerie --finalize <group-id>` | Discovers members not yet pushed across all member state dirs; invokes `leerie --finalize <run-id>` per discovered run. |
| `leerie --list --groups` | Iterates across all leerie state dirs under `$HOME/.leerie/`, groups `run.json` files by `group_id`, renders one row per group (group_id, status, member count). |

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
format-identical **except** for one residual edge: an exact half-cent
`cost_usd` (e.g. `2.675`) rounds up in `jq` (`round` is half-up) but
down in Python (IEEE-754 repr of `2.675` is `2.67499…`), a sub-cent
difference that never arises on a real summed cost. Like the deploy
note, no `run.json` persistence is needed — the `telemetry` block is a
`STATE_FIELDS` key.

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
(`filter_offtree_subtasks`), not the prompt. The instruction lifts
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
- `filter_offtree_subtasks` (DESIGN.md §12) — the existing guard
  enforces write-confinement for siblings seeded as inspect-dirs,
  unchanged.
- Branch helpers (`new-worktree.sh`, `setup-run.sh`, `integrate.sh`,
  `finalize.sh`, `host-finalize.sh`) — each member's finalize runs
  its own existing `host_finalize` against its own repo.

Maps to `DESIGN.md`: §20 *Run groups (multi-repo)*.

---

## 3. Worker invocation contract

Each worker is one `claude -p` headless process. Flags used:

| Flag | Purpose |
|------|---------|
| `-p` | non-interactive single-shot |
| `--output-format stream-json --verbose` | streams one JSON event per stdout line as the worker runs; the final `result` event is the envelope (same shape as `--output-format json`'s single output — `cost`, `usage`, `terminal_reason`, `structured_output`). `_invoke` writes raw events to `<state-root>/logs/<sid>.log` and emits per-event inline summaries gated by `state.json["verbosity"]` |
| `--json-schema <inline>` | the payload schema; serialized inline as a JSON string — a file path is silently ignored (verified against Claude Code 2.1.143) |
| `--append-system-prompt` | injects the worker's role prompt — read from `prompts/*.md` for classifier/planner/reconciler/plan_overlap_judge/satisfied_probe/provision/implementer/integrator/conformer, plus the post-run / finalize workers pr_writer, judge, and patch_generator |
| `--allowedTools` | tool allowlist (soft — permission-tier pre-approval only); three profiles — **inspect** (`INSPECT_TOOLS`: read set + allowlisted `Bash(ls:*)` / `Bash(find:*)` / `Bash(cat:*)` / … for cross-cwd read-only inspection, **no Write/Edit**) for classifier, planner, reconciler, plan_overlap_judge, and provision; **acting** (`ACT_TOOLS`: read set + Bash/Write/Edit) for implementer, integrator, and conformer; and **base-tree-only** (`SATISFIED_PROBE_TOOLS`: read set + read-only Bash verbs + HEAD-scoped git only — `Bash(git show HEAD:*)`/`Bash(git diff:*)`/`Bash(git status)`, deliberately **no** history-spanning git) for the satisfied_probe (DESIGN §8 *Already-satisfied subtask elimination*). The acting bucket keeps Bash unrestricted because its workers run with `--dangerously-skip-permissions`; the inspect and satisfied-probe profiles use `Bash(<verb>:*)` prefix patterns to pre-approve specific read-only verbs at the CLI level — no Write/Edit so the prompt's "you do not modify code" rule is enforced mechanically per DESIGN §12. **Note:** `--allowedTools` is bypassed entirely by `--dangerously-skip-permissions` (it is a permission pre-approval, not a visibility restriction). The hard-deny layer below compensates |
| `--disallowedTools` | hard-deny list (`DISALLOWED_TOOLS`); removes tools from the model's context entirely — the model cannot see or call them regardless of permission mode.  Survives `--dangerously-skip-permissions`.  Denies: `Agent`, `SendMessage`, `ScheduleWakeup`, `CronCreate`, `CronDelete`, `CronList`, `RemoteTrigger`, `PushNotification` — tools that spawn untracked parallel work or set timers the orchestrator cannot track |
| `--max-turns` | per-worker turn cap (values in §6) |
| `--model` | model alias for this worker — `sonnet` / `opus` / `haiku`. Value comes from per-worker resolution (see §2 *Model selection*) |
| `--add-dir` | repeated per entry in `state.json["inspect_dirs"]` (forwarded by `claude_p`'s `add_dirs` param). Used only by inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) so their sandboxed Read/Grep/Glob and allowlisted Bash verbs can reach sibling repos referenced in the task. See §2 *Inspect directories* |
| `--dangerously-skip-permissions` | acting workers (implementer, integrator, conformer) — suppresses all permission prompts for unattended Bash and file writes. **Not** applied to inspect workers — they run in the real repo cwd (no worktree isolation), so the blast-radius assumption that justifies skip-permissions doesn't hold. The `Bash(<verb>:*)` patterns in `INSPECT_TOOLS` pre-approve listed verbs at the CLI level; anything else (e.g. `rm`, redirect-to-file) falls through and is rejected in non-interactive mode |

`claude_p()` is `async`; every caller awaits it. Internally it awaits
`_invoke()`, which spawns the worker via the `run_proc` helper
(`asyncio.create_subprocess_exec` + `communicate()` with an optional timeout).
Shell scripts in `scripts/*.sh` are invoked via `run_script()`, a thin async
wrapper that resolves the script path and forwards to `run_proc`.

The validated payload is read from `structured_output` on the envelope. On a
missing or schema-invalid payload, `claude_p()` retries once with the violation
quoted into the prompt; a second failure raises `WorkerError`.

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
pause-and-surface arm (worktree cleanup, `--resume` hint, `EXIT_LOCKED`;
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
cap and instructing the user to re-run with `--resume` once the window
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

`WorkerError` handling by worker type — per DESIGN §7's salvage rule
("salvage if there is something to salvage; abort cleanly otherwise"):
- **implementer** — `run_implementer()` catches it, converts to an
  `incomplete-handoff` result; a fresh implementer continues from the checkpoint.
- **conformer** — `run_conformer()` catches it and returns `None`;
  `settle_subtask` records a `conformer crashed` entry in
  `conformance_warnings` and the subtask still returns `complete` (DESIGN §9
  *Post-work conformance*: the phase is advisory and never fails the subtask).
- **classifier, planner, reconciler, plan_overlap_judge, provision,
  integrator** — not caught locally; propagates to `main()`, which
  aborts with state saved for `--resume`.

`claude_p()` logs a non-fatal warning when the envelope `terminal_reason` is not
`"completed"` (e.g. `"max_turns"`).

Maps to `DESIGN.md`: §7 (worker contract), §2 (CLI subprocess form).

---

## 4. Phase walkthrough (`leerie.py`)

| Phase | Function(s) | What it does |
|-------|-------------|--------------|
| Preflight | `preflight` | git identity, clean working tree, external `leerie` branch collision (DESIGN §3 *External collision hazard*), `claude` CLI version, live `claude -p` smoke test. Run-id collisions are detected at two points: filesystem side in `State.__init__` (the run dir is created at container start since the run-id is the container/machine ID); git side in `setup-run.sh`'s branch-creation step. `setup-run.sh` repeats the external-branch check as defense-in-depth for `--resume`. Smoke test bypassed by `--skip-smoke`; preflight skipped entirely on `--resume` |
| 1 Classify | `phase_classify` | one classifier worker → categories + questions. Returned categories are filtered against the 9-name whitelist in `CATEGORIES` (mirrors DESIGN §4); `die()` if none survive |
|   • Provision | `phase_provision` | per-repo dep **detection** (DESIGN §6½ "Worker-driven install"). Always runs; runs after classify so a docs-only run can short-circuit to `kind: none`. Five steps: `.leerie-setup.sh` hook if present → `synth_mise_go_override()` if `go.mod` lacks a `.go-version` / mise.toml go pin → `mise install` at the repo root (reads `.tool-versions` natively; `.nvmrc` / `.python-version` / `.ruby-version` / `rust-toolchain.toml` via image-set `MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS`) → version capture via `mise ls --current --json` → `detect_recipe_from_lockfiles()` table-first, falls back to a `provision` worker on table miss. The recipe is **persisted to `st.data["provision"]["recipe"]` and injected into implementer/conformer prompts as a `PROVISION_RECIPE:` block** — workers run install commands themselves in their own worktrees (not the orchestrator at `repo_root`, which would clobber the host's bind-mounted checkout). The synth-go-pin env var `MISE_OVERRIDE_CONFIG_FILENAMES` is exported to `os.environ` so all downstream worker subprocesses inherit it. `mise install` and `.leerie-setup.sh` run through `run_streaming` so their output is visible live. Skipped on `--resume` (whole fresh-run else-branch is); the env var is re-exported from persisted state on resume. |
|   • Clarify *(optional)* | `gather_answers` | source-of-truth is satisfied non-interactively from the resolved preference (default `both`). Intent questions from the classifier are dropped by default; pass `--clarify` to surface them. With `--clarify` + interactive: collect; with `--clarify` + non-interactive: write `pending-questions.json`, exit code 10 (DESIGN §11) |
| 2 Plan | `phase_plan` | one planner worker per category, awaited concurrently via `gather_or_cancel` (a small wrapper around `asyncio.gather` defined in `leerie.py`) under an `asyncio.Semaphore(max_parallel)`; the first worker exception cancels its siblings and propagates to `main()`. After all `plan_one` results are collected, P1 Layer C runs: each first-pass subtask in each plan is expanded through `recursive_decompose(subtask, depth=0, …)` and `plan["subtasks"]` is replaced with the union of all returned leaves (DESIGN §5½ *Wire-in to phase_plan*). A plan with no subtasks is left untouched. Expansion vanishes each split parent's id, so the loop records `{parent_id: [leaf_ids]}` for every parent absent from its own leaves and then calls `_remap_vanished_deps(all_leaves, expansion)` **once over every plan's leaves after every plan has expanded** — a dependent may live in a different category's plan than the parent it names (DESIGN §5 *Id-vanishing operations*). The downstream path (reconcile → overlap_judge → schedule → validate_plan → write_plan) receives this expanded flat leaf set unchanged. |
|   • Reconcile *(when needed)* | `phase_reconcile` | compute set of `requires` capability tags with no matching `provides` across merged planner output. **Before matching, two mechanical passes run: (a) `_promote_external_collisions(plans)` rewrites any `extent: external` entry whose tag is in some plan's `provides` to `extent: in_plan` (the in-plan producer wins); (b) `_collect_external_preconditions(plans)` extracts every remaining `extent: external` entry into a deduped list `{tag, reasons[], originating_subtasks[]}` that bypasses the reconciler and is persisted by `write_plan`. Both passes are re-run after `_apply_reconciler_output` so any `extent: external` entries on reconciler-added connector subtasks also flow through the same machinery (collision-promoted if a provider now exists; otherwise added to the persisted preconditions list). The second collection idempotently replaces `st.data["external_preconditions"]` — the helper returns the full deduped set so a re-run is a refresh, not an append.** Only `extent: in_plan` entries with no matching `provides` enter the unresolved set. If empty: short-circuit (no worker spawn, plan unchanged). Else: spawn one reconciler worker that emits eight arrays — five *resolution* (renames / added_provides / added_subtasks / conditional_drops / dropped_requires), two *cycle-breaking-only* (dependency_edges / merged_subtasks; `dropped_requires` also plays a cycle-breaking role), and one *escape hatch* (unresolvable). If `unresolvable` is non-empty, dead-subtask elimination (`_prune_dead_subtasks`) first removes fully-speculative subtasks whose every `in_plan` requires is unresolvable when ≥1 domain has 0 subtasks (see "Phase 2½ checks" below); if entries remain after pruning, `die()` with the reconciler's diagnosis (DESIGN §5). Otherwise, the orchestrator applies the seven action arrays mechanically. After applying, runs an **acyclicity gate** (Tarjan's SCC over the post-mutation graph); on cycle, deep-copies the pre-mutation plans, computes a recommended cycle-resolution per SCC from structural signals, respawns the reconciler once with a structured retry prompt + bounded "must-include" set of acceptable operations, and re-runs the gate. If still cyclic, `die()` with the SCC + offending mutations enumerated. See "Phase 2½ checks" and "Cycle-resolution retry loop" below. |
|   • Overlap judge *(when 2+ planners)* | `phase_overlap_judge` | spawn one `plan_overlap_judge` worker against the reconciled plan to detect cross-planner **surface collisions** — two subtasks producing the same exported artifact (same component / function / primitive) with incompatible APIs. Schema in `SCHEMAS["plan_overlap_judge"]`. Output: zero or more `collisions`, each with `resolution ∈ {merge, drop_a, drop_b, unresolvable}` and (when `resolution=merge`) a non-empty `merge_feasibility` statement that becomes the merged subtask's unified intent. Orchestrator applies actions mechanically through `_apply_overlap_collisions` with the **anchor-survivor rule**: when one sid appears in 2+ non-`unresolvable` collisions (computed by `_compute_overlap_anchors`), it is the structural anchor of the cluster and survives every merge it participates in — overriding `_apply_overlap_merge`'s default lex-smaller rule (the default is a determinism device with no semantic content). Rationale: the anchor is by construction the broader subtask that overlaps with each partner; absorbing each partner *into* the anchor matches the judge's pairwise intent. Pairs that lack a shared endpoint use the lex-smaller default unchanged. Per-pair: `merge` → `_apply_overlap_merge` (with optional `survivor_hint=anchor_sid` when applicable; union of fields, intent concatenation, downstream `depends_on` rewrites); `drop_*` → `_apply_overlap_drop` (mirrors `conditional_drops` apply step); `unresolvable` → `die()` at plan time with both sids + artifact + judge's reason. The validator also die()s on the drop-of-anchor contradiction (a `drop_*` whose dropped sid is an anchor — judge contradicting itself by asking to delete the subtask other collisions claim absorbs them). **Per-resolution cycle avoidance:** before applying each `merge` / `drop_*`, `_apply_overlap_collisions` tentatively applies it to a copy (`_would_cycle_after`) and, if it would introduce a dependency cycle, skips it (`skipped_would_cycle`) — keeping both subtasks separate for the integrator instead of `die()`ing the run; the final post-merge Tarjan gate is retained only as a never-fires backstop. **Cheap-skip** when fewer than 2 planners produced subtasks, or total subtask count < 2 (no possible cross-planner collision). **Python backstop** asserts every `merge` carries non-empty `merge_feasibility` — caught at `_validate_overlap_judge_output` before any apply. Opt-out via `--skip-overlap-judge` (mirrors `--skip-smoke`; env `LEERIE_SKIP_OVERLAP_JUDGE`; `leerie.toml` `skip_overlap_judge`). Persists full judge output to `state.data["plan_overlap_judge"]` and post-apply mutations to `state.data["plan_overlap_applied"]` for audit. See "Phase 2¾ checks" below. |
| 3 Schedule | `detect_no_work`, `warn_cross_planner_file_overlap`, `warn_layer_gaps`, `warn_provider_subset_subtasks`, `filter_offtree_subtasks`, `filter_satisfied_subtasks`, `schedule`, `validate_plan` | **First: `detect_no_work(plans)` short-circuits when every plan has `status: "ready"` and empty `subtasks` (DESIGN §8 *The cleared-but-empty terminal state*) — `_finish_no_work_run` records `no_work_required=true` + per-domain bases in state.json, writes `finished_at` to state.json + run.json (with `no_push=True` so the host launcher does not attempt to push a non-existent branch), logs the no-work summary, and returns without scheduling. Phases 4–6 are skipped entirely.** Otherwise: warn on cross-planner file overlap; warn on layer gaps (DESIGN §5 *Migration-surface completeness* — DB-without-seed and env-provider-without-template); warn on provider-subset subtasks (DESIGN §5 *Provider-subset subtasks* — a subtask whose entire `files_likely_touched` is a subset of an ordered predecessor's, reusing `_build_predecessor_graph`; advisory only, the mid-run satisfied rescue is the actual safety net); **soft-drop subtasks whose `files_likely_touched` resolves outside the run's repo root (most commonly into an inspect-dir mount) — recorded in `state.data["dropped_subtasks"]`**; **then `filter_satisfied_subtasks(plans, repo_root, st, caps, models, efforts)` spawns one read-only `satisfied_probe` worker per surviving subtask (bounded by `max_parallel`), each evaluating that subtask's `success_criteria_seed` against the base tree, and soft-drops those the probe marks `satisfied` — recorded in `state.data["dropped_subtasks"]` with `reason: "already_satisfied"` + the probe's evidence (DESIGN §8 *Already-satisfied subtask elimination*). Skipped when `state.data["skip_satisfied_check"]`. If this empties every `status: "ready"` plan, the gate synthesizes a `no_work_map` from the drop evidence and routes to `_finish_no_work_run` (same terminal state as native cleared-but-empty; a `status: "blocked"` plan with 0 subtasks still falls through to `schedule`'s all-blocked `die`). Both soft-drop filters vanish subtask ids, so each calls `_remap_vanished_deps(surviving, {sid: [] for sid in dropped})` after rewriting its plan's survivors, pruning any inbound `depends_on` reference to a dropped sid (DESIGN §5 *Id-vanishing operations*). Without it a dependent's edge dangles: `schedule()` drops it silently and `validate_plan` then die()s the run. The probe's tool allowlist is a base-tree-only subset (NOT full `INSPECT_TOOLS`), illustratively `Bash(git show HEAD:*)`, `Bash(git diff:*)`, `Bash(git status)`, the `_READ_BASE` read set (`Read`/`Grep`/`Glob`/`WebSearch`/`WebFetch`), and read-only Bash verbs (`ls`/`cat`/`head`/`wc`/`grep`/`rg`/`file`/`stat`/`pwd`/`echo`) — but **no** `git log` / bare `git show:*` / non-HEAD ref, because a worktree shares the repo's full ref DB and history-spanning git false-positives on code present only on other branches. The exact list is `SATISFIED_PROBE_TOOLS` in `orchestrator/leerie.py`. Advisory/soft — subordinate to the `check_branch_has_commits` backstop per DESIGN §12;** merge plans, build the global DAG via `_build_predecessor_graph` (shared with the phase 2½ acyclicity gate), Kahn topological sort into waves. Cycles are expected to be caught upstream by the phase 2½ gate; if one slips through, `die()` with the full SCC report. |
| 4 Setup | `phase_execute` head → `setup-run.sh` → `capture_conformance_baseline` | create the run branch `leerie/runs/<run-id>` and its worktree (per-run, isolated from any other run). After `setup-run.sh`, `git worktree prune` clears stale `.git/worktrees/` metadata that a prior SIGKILL'd invocation may have left behind (on Fly, `machine stop` SIGKILLs the orchestrator — the `finally`-block cleanup never runs; the stale metadata persists on the volume and crashes `git worktree list --porcelain` in `new-worktree.sh` on the next resume). Then, unless `state.data["skip_base_baseline"]`, `capture_conformance_baseline(leerie_dir, st, caps)` runs once (DESIGN §9 *Base-tree health baseline*): the staging worktree is now an unmodified snapshot of the base HEAD, so it installs the persisted provision recipe into staging and runs each resolved build/lint/test command there directly via `run_streaming`, recording the exit-code verdict per axis at `st.data["conformance"]["_baseline"]`. Each axis records a `measured` bool alongside `ran`/`passed`: a non-zero exit whose output matches `_runner_missing` (`command not found` / `No such file or directory` — the runner itself is absent, e.g. the recipe's `pip install` failed so `pytest` is missing) is recorded `measured: False` and is **excluded from `red_axes`** — it is "could not measure," not "base is RED," and a false-RED here is what provoked the conformer to re-derive the base destructively (`git checkout <base> -- .`). Deterministic (no LLM); advisory (never raises — a glue error logs and proceeds with no baseline); idempotent (the `_baseline` key is the resume sentinel). A RED base logs a loud provisioning warning and writes `run.json.health.base_suite`. Every axis dict carries `measured` (there is no legacy default; `red_axes`, `_format_baseline_section`, and `_base_health_payload` all treat it as mandatory). The baseline is threaded into every conformer prompt (`_format_baseline_section` → `BASELINE:` line, which surfaces unmeasurable axes as an explicit "could not measure — attribute failures yourself" line) so the conformer scopes build/lint/test residuals to the delta rather than re-deriving "pre-existing" |
| 5 Execute | `phase_execute`, `settle_subtask`, `integrate_wave`, `run_final_conformance` | per wave: subtasks whose `subtask_status` is already `"complete"` are skipped (they were integrated in a prior invocation); when every subtask in a wave is already complete the wave is skipped entirely and `completed_waves` is advanced. Before dispatch, stale `subtask_status` entries for retried subtasks (failed/blocked from the prior invocation) are deleted so `_get_progress` counts them as running (absent = running per the progress-prefix convention above). Remaining implementers are awaited concurrently via `gather_or_cancel` under a fresh `asyncio.Semaphore(max_parallel)` (separate instance from Phase 2's), then integrate, then run a deterministic conflict-marker scan on the integrated worktree. `settle_subtask` runs the **post-work conformance phase** (DESIGN §9 *Post-work conformance*) on the success path before returning — `discover_rules_files` → `run_conformer` loop (≤ `conformance_rounds`) → re-run the per-subtask mechanical-precondition gates (`check_branch_has_commits`, dirty-worktree, `check_diff_scope`) against the conformer's commits → attach `conformance_warnings` to the result. The phase is advisory: residuals, build/lint/test failures, gate violations on conformer commits, and `WorkerError` all surface as warnings, never as `failed`/`blocked`. If any subtask in the wave ends `blocked` or `failed`, `phase_execute` still calls `integrate_wave` for the successful subtasks (partial-wave integration — DESIGN §3) and runs the conflict-marker scan on the staging worktree, then aborts the run — the blocker is recorded in `state.json`, the successful subtasks' work is on the run branch, and the run resumes with `--resume`. There is no LLM wave-level re-validation between waves; the §8 confidence gate is the load-bearing per-subtask signal, and `scan_conflict_markers` is the deterministic post-integration safety net. **After every wave has integrated**, `_run_phases` calls `run_final_conformance(leerie_dir, st, caps, models, efforts)` once on the staging worktree (DESIGN §6 *Worktree and integration model*, final-tree pass paragraph) — same `run_conformer` loop with `cwd = <state-root>/runs/<id>/worktrees/staging`, `DIFF_BASE = st.data["working_branch"]` (the PR's base, captured by `phase_classify`), no subtask spec / criteria inputs, same `conformance_rounds` cap, same protected-path rollback discipline. Output lands at `st.data["conformance"]["_final"] = {result, warnings}` and is threaded into the `pr_writer` payload as `final_conformance`. Advisory: any failure mode (WorkerError, malformed result, exhausted rounds) surfaces as a warning; `phase_finalize` always runs |
| 6 Finalize | `phase_finalize` → `finalize.sh`, `cleanup.sh`, post-cleanup branch verification; launcher then pushes on host | verify the run branch is non-empty; run `cleanup.sh --subtask-branches` to delete per-subtask branches; **post-cleanup branch verification** (`git show-ref --verify` on the run branch — if the branch disappeared after cleanup, `die()` routes to the pause branch to preserve the machine for recovery); record `finished_at` in `run.json`; delete the per-subtask branches `leerie/subtasks/<run-id>/*` (the run branch is **kept** as the PR head; state dir is kept as audit). **The push + PR step has moved to the host launcher** (DESIGN §6 *Finalization*). A successfully finalized run (`finished_at` set AND `current_phase` == "phase 6: finalize") is **terminal on resume** — the orchestrator returns immediately without re-executing phases 4→5→6, preventing a concurrent `decide_teardown` race. |
| Post-run Judge | `phase_judge`, `judge_capture` | standalone post-run phase (not part of main orchestrate flow): reads `calls.ndjson`, runs one `judge_capture()` per record in parallel under `asyncio.Semaphore(max_parallel)`, writes per-record verdicts to `<judge-dir>/<call_id>.json` and a summary `INDEX.json`; uses `prompts/judge.md` rubric |
| Post-run Heal | `HealState`, `heal_baseline`, `heal_apply_patch`, `heal_replay_patched`, `request_patch`, `phase_heal` | heal-loop phases: `HealState` persists failing_samples / baseline / history / best_so_far at `<heal-dir>/<call_type>/state.json`; `heal_baseline(call_type, failing_records, n, heal_dir, caps, st, models)` runs n unpatched replays per record + judge, writes baseline verdicts + state; `heal_apply_patch(call_type, iter_n, patch_text, anchor_match, heal_dir, failing_records)` materialises patched prompts under `iter-<N>/patched-prompts/`; `heal_replay_patched(call_type, iter_n, n, heal_dir, caps, st, models)` runs n patched replays per record + judge, appends iteration record to state.history; `request_patch(state, iter_n, st, caps, models)` invokes the `patch_generator` worker (schema `SCHEMAS["patch_generator"]`, SID `heal-patch-<call_type>-iter<N>`, prompt from `prompts/patch_generator.md`) and returns `(anchor, replacement)` — raises `ValueError` if the returned anchor is not a literal substring of the resolved prompt body (code-enforced per the prompts-are-advisory principle); `phase_heal(call_type, failing_records, heal_dir, caps, st, models, request_patch_fn=None, n, config)` drives the full baseline→loop→report cycle; `request_patch_fn` defaults to the real `request_patch` when `None`, or accepts a sync/async 2-arg stub for testing |

`phase_classify` runs before `gather_answers` because the question set depends
on the classification.

Between Phase 3 and Phase 4, `write_plan()` persists the merged plan
(`<state-root>/runs/<run-id>/plan.json`) and per-subtask spec files
(`<state-root>/runs/<run-id>/subtasks/<id>.json`). The conformance
phase derives its advisory build/lint/test commands separately via
`_infer_build_lint_test(repo_root)`, which performs best-effort
discovery by checking for configuration files and lockfiles. Supported
families (checked in this order; first match wins per axis via
`out[axis] = out[axis] or "..."`):

- **Makefile** → `make` (build)
- **Node/JS** (`package.json`) → `<pm> run build` (build), `<pm> run test`
  (test), where `<pm>` is detected from lockfiles: `pnpm-lock.yaml` → `pnpm`,
  `yarn.lock` → `yarn`, `bun.lockb`/`bun.lock` → `bun`, else `npm`.
  Precedence mirrors `detect_recipe_from_lockfiles()`. All PMs use the
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
  `run_final_conformance` — neither calls `_infer_build_lint_test` directly.

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
| reconciler's `unresolvable` array non-empty → `die()` with the worker's diagnosis | genuine gaps where no planner produced a needed capability *in the build graph* and no plausible connector subtask can be inferred. Restricted to `extent: in_plan` entries — `extent: external` entries are filtered out before the unresolved set is computed and surface as `preconditions` in `plan.json` rather than as failures. Each unresolved `(sid, tag)` pair is annotated with the consuming subtask's producing planner-domain (from `_compute_unresolved_requires`) so the abort message can render `domain/sid` — naming the planner-domain whose plan held the dangling dependency, which is the primary remediation lever for the user. |
| reconciler output validated against `SCHEMAS["reconciler"]` | malformed reconciler response (caught by `claude_p`'s schema gate; structurally invalid output is retried once, then escalated) |
| **size gate** on `added_subtasks` (runs *before* the acyclicity gate) | a reconciler-added subtask emitted with `size: large`. The reconciler-authored subtasks carry `_added_by_reconciler: true` (set in `_apply_reconciler_output`); `_find_oversized_added_subtasks` collects every offender. On detection, leerie tries one size-resolution retry (see "Size-resolution retry loop" below); if the retry still emits `size: large`, `die()` with the offending sids enumerated. The downstream `validate_plan` size check (line "no `size: large` subtasks" under "Plan validation") is the final backstop and only fires for planner-authored `large` after this retry exhausts; its error message names "planner" vs "reconciler" via the `_added_by_reconciler` flag so the user knows which prompt misbehaved. |
| **acyclicity gate** (Tarjan SCC over the post-mutation graph; runs *before* the unresolved-requires re-check) | a rename / added_subtask / dependency_edge that closes a dependency cycle. Each individual reconciler mutation can be locally correct yet jointly cycle-creating — e.g. two renames whose targets each provide what the other side requires. Tarjan localizes the SCC; edge attribution names which mutation closed each edge. On detection, leerie tries one cycle-resolution retry (see "Cycle-resolution retry loop" below); if the retry still cycles, `die()` with the SCC + offending mutations enumerated. |
| **must-include constraint** (apply-step enforcement on retry output) | a retried reconciler output that omits any operation from the bounded set leerie required for each named cycle. The retry prompt lists the legal operations per cycle (`dropped_requires` on either rename, `dependency_edges` in either direction, `merged_subtasks` in either direction); if the revised output doesn't include at least one for each cycle, `die()` with the missing-cycle diagnostic — surfaces "model defied a structural constraint" cleanly, never a silent cycle. |
| **unresolved-requires retry loop** (recompute unresolved set after applying reconciler output) | the reconciler's renames/added_subtasks/added_provides didn't actually close every gap. Common cause: model invented a new tag in `added_subtasks` and forgot to rename the original consumer's tag to match (captured run `075210`: `deps-008` required `cdk-stacks-authored`; reconciler created `config-011` providing `infra-stacks-authored` and never renamed deps-008's tag). On first detection, leerie tries one retry with a structured prompt that surfaces string-similarity hints from the post-mutation `provides` namespace. If the retry still leaves unresolved tags, `die()` with the structured report. |
| **unresolved-retry must-include constraint** (apply-step enforcement on retry output) | a retried output that omits any operation addressing the named unresolved entries. Legal addressing: `rename` on the (sid, tag), `added_provides` covering the tag, `added_subtask` whose provides includes the tag, `conditional_drops` on the consumer sid, `dropped_requires` on the (sid, tag) (consumer's `requires` is over-specified — an aggregate or coarser synonym of what the consumer itself provides, not a real cross-subtask dependency; the consumer stays in the plan, only the bad edge goes), or `unresolvable` on the (sid, tag). |
| **conditional_drops** apply step (DESIGN §5 resolution action) | a planner-emitted consumer subtask whose own `intent` declares it conditional on an unresolvable `extent: in_plan` precondition (signals like "no-op if X", "conditionally add", "drop if Y", "otherwise this subtask is dropped"). The apply step removes the named sid from its plan, prunes downstream `depends_on` references to that sid, and records the drop in `state.data["conditional_drops"]` (keyed by sid → `{reason, from_unresolved_tag}`). Distinct from `state.data["dropped_subtasks"]`, which records off-tree soft-drops from `filter_offtree_subtasks` (phase 3) — same shape of audit signal, different cause. The apply step `die()`s if the target sid carries `_added_by_reconciler: true` (the op is restricted to planner-authored consumers — a reconciler-added subtask has no planner prose to convert into a structured drop). |
| **dropped_requires** apply step (DESIGN §5 resolution action — also a cycle-breaking op) | a consumer's `requires` entry that was over-specified by its planner — an aggregate, coarser synonym, or authoring-time decision the same subtask itself records, rather than a code artifact another subtask produces. The apply step removes the named `(sid, tag)` `extent: in_plan` entry from the consumer's `requires` list. The consumer itself stays in the plan (unlike `conditional_drops`, which removes the whole subtask) — only the bad edge goes. Apply mechanics are identical whether the op is emitted as a resolution (unresolved-tag retry, addressing an over-specified self-reference) or a cycle-breaker (the over-specified entry was what closed the cycle). Silent no-op on missing sid/entry, mirroring `renames`. |
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
mechanical guarantee is "split it into N subtasks each providing one
`provides` tag," which is rendered directly into the retry prompt. The
reconciler's prompt (`prompts/reconciler.md`) also documents the rule on
first attempt; the retry is the enforcement.

The size gate runs *before* the acyclicity gate because oversize
authoring is an upstream defect: a `large` subtask bundling four
capabilities is also more likely to produce a cycle than four small
single-capability subtasks. Splitting first lets the cycle gate evaluate
the cleaner graph.

**Retry composition (snapshot refresh).** When multiple retries fire on
the same run (e.g., size retry succeeds and then the cycle gate fires),
each successful retry refreshes `pre_plans_snapshot` to the post-retry
state. The next retry's revert therefore restores the most recent good
state, not the original pre-mutation state. Without this refresh, a
cycle retry firing after a successful size retry would undo the size
split — the oversized subtask would return and reach `validate_plan`,
producing a misleading "size-retry exhausted" error even though the
size retry actually succeeded. The unresolved retry doesn't refresh
because it's the last gate before `phase_reconcile` returns.

**Cycle-resolution retry loop.** When the acyclicity gate fires on the first
reconciler attempt, `phase_reconcile` deep-copies the pre-mutation plans,
reverts the failed mutations, computes a *recommended* operation per SCC
from structural signals (in `_recommend_cycle_resolution`), builds a
retry prompt (in `_build_cycle_retry_prompt`) that names the SCC, the
mutations that closed each edge, the structural signals, the
recommendation, and the bounded "must-include" set of acceptable
operations, then respawns the reconciler worker once with that prompt.
Maximum two attempts total — mirrors the schema-fail retry shape at
`leerie.py: claude_p()`. Cost: one extra reconciler spawn (~$1–2, ~1 min)
on cycling runs only; non-cycling runs pay nothing extra.

The recommendation heuristic is deterministic:

1. **Exactly one edge in the SCC is a planner-declared `depends_on`** →
   recommend `dropped_requires` on the rename that closes the reverse
   direction. (Planner ordering wins; the reconciler's rename is the drift.)
2. **Else SCC members share `files_likely_touched`** → recommend
   `merged_subtasks(into, from)` where `into` is the smaller subtask by
   `success_criteria_seed` length (tie-break: lexicographic sid). The
   subtasks are authoring the same file; one commit will do both pieces of
   work; the shorter-criterion subtask becomes the canonical home.
3. **Else** → recommend `dropped_requires` on the rename whose `from` tag
   had no planner-declared producer in the pre-reconcile graph. (The
   rename was speculative — the tag was never going to resolve to a real
   producer; dropping the requirement is structurally honest.)
4. **Tie-breaker of last resort** → drop the lexicographically later rename.

The retry prompt presents the recommendation as the answer (not as one of
several options) and explicitly forbids `unresolvable` for cycle
resolution — the model must commit to one of the bounded operations or
echo the recommendation. The mechanical floor (gate + must-include) is
the guarantee; the recommendation primes the model toward the
structurally-correct answer.

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
(not the answer) because the underlying signal is textual string
similarity — which can produce false friends (a textually-close-but-
semantically-distinct synonym). Two guards filter candidates before
scoring: a **self-loop guard** skips candidates whose provider includes
the consumer's own sid (would create a self-edge), and an
**extent-aware** guard ensures only `extent: in_plan` entries are
considered. Cases (first match after guards wins):

1. **Unique top match with Jaccard ≥ 0.5** → recommend
   `rename(sid, from=tag, to=top.tag)`. (Verified on captured run
   075210: fires correctly with `j=0.5` for `cdk-stacks-authored` →
   `infra-stacks-authored`.)
2. **Top match with Jaccard ≥ 0.7 (even if not unique)** → same.
3. **Else** → no recommendation; model picks unaided (the common case
   per historical scan; ~88% of post-mutation unresolved entries lack
   a strong-similarity candidate).

`unresolvable` IS valid for this retry (unlike the cycle retry's
strict forbid) — if no real producer exists for the tag, surfacing
that cleanly is the right answer. The mechanical floor (must-include
validator + post-retry unresolved + cycle re-check) catches every
malformed revision; the recommendation is best-effort.

### Phase 2¾ checks — `phase_overlap_judge`
| Check | Catches |
|-------|---------|
| **cheap-skip when impossible** (fewer than 2 planners contributed subtasks, OR total subtask count < 2) | spurious worker spawn on single-planner / trivial runs. No `plan_overlap_judge` call in `calls.ndjson`; log line `phase 2¾: overlap-judge skipped (single planner)` or `… (< 2 subtasks)` at normal verbosity. |
| judge output validated against `SCHEMAS["plan_overlap_judge"]` | malformed judge response (caught by `claude_p`'s schema gate; structurally invalid output retried once, then escalated per the standard policy). |
| **merge-feasibility backstop** (`_validate_overlap_judge_output`) — every collision with `resolution == "merge"` must carry non-empty `merge_feasibility` | the judge skipping the merge-feasibility discipline section in `prompts/plan_overlap_judge.md`. Per `DESIGN.md §12` (prompts advisory, code enforces): the prompt asks for `merge_feasibility` whenever `merge` is emitted, and Python rejects a `merge` without it. `die()` with the offending pair (`a_sid`/`b_sid`/`artifact`). |
| **`merge` apply step** (`_apply_overlap_merge`) | collapse the two subtasks: surviving sid is the lexicographically smaller id by default (a determinism device — same merged plan regardless of pair argument order) OR the value of the optional `survivor_hint` parameter when the caller is applying the anchor-survivor rule for a cluster. Surviving subtask gets the union of `files_likely_touched`, `provides`, `requires`, `depends_on` (with self-references removed); `title` becomes `"{survivor.title} + {dropped.title}"`; `intent` is the concatenation of the survivor's existing intent, the absorbed subtask's full existing intent (under a `--- Absorbed intent from {dropped.id} ---` marker), and a trailing `"Merged with {dropped.id} by plan-overlap-judge:\n{judge.merge_feasibility}"` note. Carrying the absorbed subtask's intent is required by the DESIGN §5 *merge_feasibility carry-forward* invariant: any merge_feasibility statement previously appended to the absorbed subtask's intent (from an earlier merge where it was a survivor) must be preserved. `success_criteria_seed` becomes `"{survivor.criteria} AND {dropped.criteria}"`. Downstream subtasks whose `depends_on` referenced the dropped sid are rewritten to point at the surviving sid. Records the mutation in `state.data["plan_overlap_applied"]`. |
| **`drop_a` / `drop_b` apply step** (`_apply_overlap_drop`) | remove the dropped sid; union the dropped subtask's `provides` tags into the survivor's `provides` (deduped, order-preserving — without this union, any downstream `requires` that matched the dropped subtask's tags would orphan into a confusing `validate_plan` error rather than resolving cleanly against the survivor); drop any survivor `extent: in_plan` requires whose tag is now in the post-union provides (would-be graph self-loop, mirrors `_apply_overlap_merge`); rewrite downstream `depends_on` references from the dropped sid to the survivor. Title / intent / success_criteria_seed are NOT copied from the dropped subtask — the judge said one intent supersedes the other, so the survivor's intent is the intent that wins; only the capability-graph wiring is unioned. |
| **anchor-survivor rule** (`_apply_overlap_collisions` + `_compute_overlap_anchors`) — pairwise collisions resolve into a coherent cluster decision | shared-endpoint clusters where one subtask appears in 2+ non-`unresolvable` collisions (an *anchor*, e.g. judge emits both `merge(A, B)` and `merge(A, C)` because A overlaps with B on one artifact and with C on another). The anchor is structurally the broader subtask that overlaps with each partner; the apply loop passes `survivor_hint=anchor_sid` into `_apply_overlap_merge` when exactly one of the pair's endpoints is in the anchor set, so the anchor survives that merge (overriding the default lex-smaller rule, which is a determinism device with no semantic content). When both endpoints are anchors — legitimate within a single connected cluster, e.g. the closing edge of a triangle — the rule falls through to lex-smaller; the merged subtask still carries forward every prior `merge_feasibility` via the absorbed-intent block (DESIGN §5 carry-forward invariant). A `survivor_of: dict[str, str]` map rewrites later pairs against earlier survivors so a partner already absorbed into the anchor isn't looked up as a stale endpoint. Pairs whose endpoints have both rewritten to the same survivor (the redundant closing edge of a connected cluster) are recorded as `skipped_redundant` entries in `state.data["plan_overlap_applied"]` so the audit trail reflects every collision the judge emitted. The pairwise judge protocol stays simple; cluster decisions are enforced in code, not in the prompt (DESIGN §12). `_apply_overlap_drop` has a `dropped_sid == surviving_sid` self-loop guard as defense in depth against future callers reaching it with a self-collapsed pair. |
| **anchor consistency gate** (`_validate_overlap_judge_output`) — drop-of-anchor contradiction die()s before any mutation | a `drop_*` whose `dropped_sid` is an anchor — the judge is asking to delete the same subtask other collisions claim absorbs them, directly self-contradictory. die() with the anchor sid, the partner sid, the artifact, and the suggested resolution (refine task or downgrade to `unresolvable`). (Earlier iterations also gated `merge`-between-two-anchors, but the apply loop's natural semantics — fall-through to lex-smaller with absorbed-intent carry-forward — handles every observed multi-anchor shape cleanly, so the check was removed as over-aggressive.) |
| **`unresolvable` → `die()`** at plan time | genuine API contradictions the judge correctly refuses to silently auto-merge. The abort message names both sids, the colliding artifact, the judge's reason, and the suggested next step (revise the task or manually delete one of the colliding subtask specs and `--resume`). Strictly better than the multi-hour wave-N integrator design-conflict crash this phase exists to prevent. |
| **per-resolution cycle avoidance** (`_would_cycle_after` inside `_apply_overlap_collisions`) — checked before each `merge` / `drop_a` / `drop_b` apply | a collision resolution's dependency-union (survivor inherits the absorbed subtask's `provides`/`requires`/`depends_on` plus downstream `depends_on` rewrites) can introduce a transitive cycle absent from the post-reconcile graph (phase 2½'s acyclicity gate passed before these resolutions ran). Before applying each resolution, `_would_cycle_after(plans, apply_fn)` deep-copies `plans`, applies the resolution to the copy, rebuilds the predecessor graph via `_build_predecessor_graph`, and runs `_tarjan_sccs`. If the resolution *would* cycle, it is skipped (`skipped_would_cycle`; see next row) and both subtasks are kept separate for the integrator. The check is side-effect-free (operates on the copy) and runs against the *current live* `plans` so it sees every earlier-applied resolution. Covers `drop_*` too, because `_apply_overlap_drop` also unions `provides` and rewrites `depends_on`. |
| **post-merge acyclicity backstop** — Tarjan SCC on the final post-merge graph, immediately after `_apply_overlap_collisions` returns | with per-resolution avoidance above in place, this gate must never fire. It rebuilds the predecessor graph via `_build_predecessor_graph`, runs `_tarjan_sccs`, and on a surviving cycle `die()`s with `_format_cycle_diagnostic` output — but framed as an **orchestrator logic bug** (the tentative check and the real apply path disagreed), *not* a user-recoverable condition (per-resolution skipping already exhausted the `--skip-overlap-judge` lever). Retained as defense-in-depth against future drift between `_would_cycle_after` and the real apply, mirroring `_apply_overlap_merge`'s defensive missing-sid `die()`. |
| **`skipped_would_cycle` audit action** (`_apply_overlap_collisions`) | a `merge` / `drop_*` whose apply would close a dependency cycle. Recorded in `state.data["plan_overlap_applied"]` as `{"action": "skipped_would_cycle", …}` with both sids, the artifact, and `resolution`; logged at normal verbosity. Crucially, `survivor_of` is **not** updated on a skip — both endpoints stay live, so later collisions referencing either endpoint resolve against a present sid (mirrors how a merge would otherwise repoint them). The judge is not re-prompted; the cycle is a global-graph property outside its pairwise-surface competence (DESIGN §5 *Cross-domain surface overlap* → *Post-merge acyclicity*). |
| **state persistence** | full judge output written to `state.data["plan_overlap_judge"]` (for audit / replay); post-apply mutation summary written to `state.data["plan_overlap_applied"]`. Persisted before `phase_overlap_judge` returns; visible in `state.json` for resume-time replay debugging. |

The judge's empirical recall on the test corpus (5 runs / 38 subtasks)
was 100% — every observed surface collision was flagged including the
`0c4bab` AuthShell pair that motivated this phase, with the merge-
feasibility discipline correctly downgrading the AuthShell case to
`drop_a` (legitimate plan-time resolution) rather than `merge` (the
v1-prompt failure mode that would have produced a frankenstein
implementer spec).

The complementary `warn_cross_planner_file_overlap()` check at phase 3
is **kept as-is** — it now serves as a complementary signal for file-
overlap that *doesn't* indicate surface collision (the deliberately-
permissive same-file-different-surface class).

### Plan validation — `validate_plan` (after scheduling, before persisting the plan)
| Check | Catches |
|-------|---------|
| **budget feasibility** — `check_budget_feasibility()` runs at the same layer as `validate_plan`, immediately after `schedule()` returns and before `write_plan()` persists. Estimates remaining `claude -p` calls (implementers + conformers + integrators per wave + finalize) added to `worker_count` already spent on upstream phases, multiplied by `budget_safety_margin`, compared to `max_total_workers`. | a planner output that is mathematically too large to fit the configured `--max-workers` cap. The pre-existing runtime backstop is `State.bump_workers()` which raises `WorkerError` partway through execution; this earlier check `die()`s with `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` value at the cheapest possible moment (no implementer has spawned yet, only the integrated commits from upstream judgment phases are sunk). Opt-out via `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml`. See §"Budget feasibility preflight" above and DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*. |
| ids match domain prefix (`bugfix-`, `feat-`, `refactor-`, `perf-`, `test-`, `deps-`, `config-`, `docs-`) | cross-domain collisions, audit ambiguity. The planner's user prompt receives the prefix directly as `ID_PREFIX = CATEGORY_ABBREV[domain] + "-"`, so the prompt cannot drift from the validator's allowlist — both derive from the same `CATEGORY_ABBREV` map (in `leerie.py`). |
| no `size: large` subtasks | planner OR reconciler violated the sizing constraint. The error message names the actual author via the `_added_by_reconciler` flag — "planner must split it further" for planner-authored, "reconciler must split it further (size-retry exhausted)" for reconciler-added subtasks that survived the size-resolution retry loop. The reconciler path is exercised through the phase 2½ size gate first; this row is the post-merge backstop for the planner case and the exhaustion case. |
| no empty `success_criteria_seed` | implementer has no criteria starting point |
| every `depends_on` id exists | dangling edges silently dropped by the scheduler |
| every `requires` entry is an object `{tag, extent, reason?}`; `extent ∈ {in_plan, external}`; `reason` non-empty when `extent: external` | malformed planner output (caught at JSON-schema validation in `claude_p`; this row is the post-merge defensive re-check) |
| every `requires` entry with `extent: in_plan` has a provider in some subtask's `provides` | unresolvable cross-domain dependency (only `in_plan` is checked; `external` entries are explicitly out-of-graph by planner declaration) |
| no `files_likely_touched` entry matches `is_protected_path()` (`.leerie/`, `.git/`, or top-level `.claude/` outside the deliverable subtrees) | planner named a protected meta-directory as an implementer deliverable — the implementer would either fail `check_diff_scope` mid-run or work around the gitignore and still be rejected. Catching this at plan-validation time gives the planner a corrective-retry round instead of burning an implementer invocation. For coordination artifacts (research specs, design summaries) the planner should use `provides`/`depends_on` and the implementer's `artifacts` result field — see DESIGN §5 *Artifact passing between subtasks* — not `files_likely_touched`. |

`warn_cross_planner_file_overlap()` runs immediately after
`phase_reconcile` (before `validate_plan` and the scheduler) and **logs a
warning, never fails**, when two planners' subtasks both list the same
path in `files_likely_touched`. Empirically (May 2026, n=3 historical
runs) failed runs had ≥9 cross-planner overlaps each while the
successful run had zero; the warning surfaces that risk at plan time.
The reconciler now consumes the same shared-files signal as one input to
the recommendation heuristic (above) when a cycle requires resolution
— SCC members that share `files_likely_touched` get a `merged_subtasks`
recommendation. The warning itself remains as runtime visibility for the
user; it complements the recommendation heuristic rather than replacing
it.

`warn_layer_gaps(plans)` runs at the same layer and surfaces two
heuristic warnings (DESIGN §5 *Migration-surface completeness*):
(1) any subtask's `files_likely_touched` includes a `schema.prisma`
path but no subtask across the full plan touches seed or migration
files — database-initialization gap; (2) any subtask's `provides`
tags contain env/bootstrap/secret/credential keywords but no subtask
touches `.env.example` or env documentation — env-contract gap.

`filter_offtree_subtasks()` runs at the same layer (after
`warn_cross_planner_file_overlap`, before `schedule()`) and **soft-drops
any subtask whose `files_likely_touched` contains a path that does not
resolve under the run's primary repo root** — the common case is a leak
into an inspect-dir mount (`/inspect/<repo>/...`), where the planner
named a file the implementer cannot modify because the mount is
read-only. Drops are recorded in `state.data["dropped_subtasks"]` and
logged per-subtask. The drop must run before `schedule()` because
`phase_execute` iterates `state.data["waves"]` (not the in-memory
`subtasks` dict), and `waves` is computed by `schedule()` — a drop
after that point leaves `waves` referencing a sid with no spec on disk.
A soft drop is the right shape because a `die()` here is unrecoverable:
the resume branch in `_run_phases` does not re-run the planner pipeline
and requires `state.data["waves"]` (only written by `write_plan` after
this point). When a dropped subtask provides a tag a survivor requires,
`validate_plan`'s existing unresolvable-requires check (above) catches
it and dies with `<sid>: requires '<tag>' but nothing provides it —
dependency is unresolvable and will be silently dropped` — the user
sees both messages and re-frames the task.

### Per-subtask checks — in `settle_subtask`, every worker result
| Check | Catches | On failure |
|-------|---------|-----------|
| `validate_result()` — `incomplete-handoff` with missing checkpoint file | session-limit no-op; `--max-turns` with no checkpoint written; **worker reaped mid-turn** (e.g. it backgrounded an expensive final step like a build that OOM-died, so `claude -p` was killed before writing a checkpoint) | **Rescued when the worktree holds commits, else Retryable** (`failure_kind="empty_handoff"`). Before failing, `settle_subtask` calls `branch_has_commits_ahead` (a positive-polarity bool — True only when the worktree exists, git succeeds, and there are commits; distinct from the `check_branch_has_commits` no-op gate, whose indeterminate states return `None`); **if the branch has commits ahead of the run branch the worker produced a real deliverable** — it is settled as `complete` (with the advisory conformance phase recording whatever verification step didn't finish) instead of being discarded. `fail()` would `_reset_subtask_worktree` and destroy the committed diff, then burn `failed_retries` until the run dies; the positive commit-proof keeps green work while a gone worktree / git failure is **not** rescued (never mistaken for a real deliverable). Only when there are **no** commits (a genuine no-op) does it stay retryable. The confidence gate and dirty-worktree fail are skipped for a rescued result (a reaped worker returned no confidence envelope and may have left uncommitted debris, which is discarded). See DESIGN §9. |
| `validate_result()` — other cross-field invariants | `handoff` with null `checkpoint_path`; `blocked` with no blocker; `failed` with no summary; `needs-clarification` with no `clarification_question` / invalid `checkpoint_path` | **Terminal** (`failure_kind="broken"`) |
| `check_branch_has_commits()` | `complete` claim, nothing committed *and* no `artifacts` returned. A non-empty `artifacts` array on the result is a substitute deliverable (DESIGN §5 *Artifact passing between subtasks*) — research-style subtasks whose only output is structured data for downstream subtasks pass this gate without commits. | **Rescued when the criteria are already met on the run-branch HEAD, else Retryable.** Before failing a no-commits `complete`, `settle_subtask` re-runs the `satisfied_probe` (same prompt/schema as `filter_satisfied_subtasks`) against the subtask's `success_criteria_seed`, this time on the **run-branch HEAD** (`compute_run_branch(st.run_id)`), not the base tree — because a sibling subtask in an earlier wave may have committed this subtask's entire deliverable during the run (DESIGN §8 *The mid-run sibling case*). (this also covers a subtask already satisfied on the base tree — the probe judges *whether* the criteria are met, not *who* met them; DESIGN §8 *Scope*). If the probe returns `satisfied`, the subtask is settled `complete` and recorded in `state.data["dropped_subtasks"]` with `reason: "already_satisfied_mid_run"` (evidence + `checked` list, same shape as the pre-schedule drop); `settle_subtask` also writes a `state.data["conformance"][sid]` sentinel (`{result: None, warnings: [...]}`) so `_get_progress` classifies the rescued subtask as `done` rather than perpetually `in_conformer` (the real conformer is correctly skipped — a zero-commit subtask has no diff to conform). Only a subtask with a non-empty `success_criteria_seed` is probed; without a criterion there is nothing to judge, so it stays retryable. If the probe is not satisfied (a genuine lazy/broken no-op) the existing `"no_commits"` retryable path is unchanged. The probe is subordinate to the mechanical gate per DESIGN §12 and defaults to *not satisfied* on any error/uncertainty, so it can only *rescue* a real no-op, never mask one. |
| dirty worktree check | uncommitted changes that vanish on integration | **Retryable** |
| `check_diff_scope()` | `.leerie/` or `.git/` in the diff; any `.claude/` path *except* `.claude/agents/`, `.claude/commands/`, `.claude/skills/` (the documented Claude Code user-deliverable subtrees — implementers may write a subagent/command/skill file there as a legitimate deliverable, but never `settings.json` or any top-level `.claude/` file) | **Terminal** (protected path); scope-volume warning is non-fatal (triggered when `files_likely_touched` is non-empty *and* touched > max(3× expected, 5), or when touched > 15 regardless of the planner's estimate) |
| `validate_checkpoint()` — on `incomplete-handoff` | required section missing; required section empty/whitespace; required section contains only a placeholder token (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`, trailing `.`/`!`/`?`/`…` ignored and repeated `?` collapsed); a path listed under `## Files touched` no longer exists in the worktree and is not flagged `[deleted]` | returns `blocked` |
| `_retryable_failure(kind)` — on `status='failed'` returned by the worker itself | worker self-report of failure | routed through the retry policy with `failure_kind="broken"` (worker self-report has no producer to tag a more specific kind, and a self-reported failure is broken-worker territory by default); **terminal** on first occurrence |

`validate_result()` accepts a `complete` status regardless of what
`criteria_results` carries — empty, missing, or with `met:false`
entries are all valid. Per DESIGN §8 the criteria file is
informational, not a gate. A worker's unmet-criterion self-report is
recorded on the result for telemetry and surfaces as a warning in
`state.json["conformance"]` alongside the conformance-phase residuals,
but does not affect the subtask's terminal status. The criteria-file
lock (`lock_criteria` / `verify_criteria_lock`) and the
worker-initiated `criteria_revision_proposal` channel were both removed
when the criteria file's load-bearing role retired — see DESIGN §9.

### Per-subtask post-work conformance — in `settle_subtask`, success path only

Triggered only when an implementer's `status: "complete"` has already cleared
every check above (commits present, worktree clean, no protected path
written). None of the other terminal statuses (`incomplete-handoff`,
`needs-clarification`, `blocked`, `failed`) invoke the conformer.
Implements DESIGN §9 *Post-work conformance*.

| Step | Function | Behavior |
|------|----------|----------|
| Discover rules files | `discover_rules_files(repo_root)` | Returns existing paths from a fixed, capped allowlist (`CLAUDE.md`, `AGENTS.md`, `.agent.md`, `.cursorrules`, `.windsurfrules`, `docs/CLAUDE.md`, `docs/AGENTS.md`, `docs/CONVENTIONS.md`, `docs/STYLE.md`, `docs/DESIGN-SYSTEM.md`, `docs/DESIGN_SYSTEM.md`, `docs/UI.md`, `README.md`, `CONTRIBUTING.md`, `docs/DESIGN.md`, `docs/IMPLEMENTATION.md`), deterministic order, never raises. Empty list when nothing matches. The design-system candidates (`docs/DESIGN-SYSTEM.md` and spelling variants) exist so a repo's component/color/banner conventions reach both the conformer and the implementer (DESIGN §9). |
| Run conformer | `run_conformer()` | One `claude -p` invocation with `ACT_TOOLS`, `--dangerously-skip-permissions`, `SCHEMAS["conformer"]`. Accepts optional `extra_feedback: str \| None` — when non-None, appended to the user prompt (used for Pattern B backgrounding-retry feedback from prior round). Catches `WorkerError` and returns `None` (surfaced as a warning). |
| Validate output | `validate_conformance_result()` | Cross-field invariants — `rule_violations_residual` non-empty requires `rules_files_read` non-empty; each `rule_violations_fixed` item must cite a non-empty `rule` string; each `docs_updates` / `tests_updates` item must cite a `path` that exists. On failure → warning, loop breaks. |
| Re-run gates | `check_branch_has_commits`, dirty-worktree check, `check_diff_scope` | Same functions used on the implementer, re-applied to any new commits the conformer added. A scope-protected-path violation triggers `rollback_conformer_commits()` (reset to `before_sha`) and is recorded as a warning, **not** as `failed` / `blocked`. |
| Clobber-survival check | `clobbered_owned_files(worktree, run_branch, impl_head_sha)` + `_blob_sha` | DESIGN §9 *No clobbering the implementer's work*. `impl_head_sha` is snapshotted **once before the round loop** (a per-round HEAD would fold in prior conformer commits and miss a round-0 clobber). Owned set = `git diff --name-only <run_branch>..<impl_head_sha>`; for each owned file, a clobber is a deletion at HEAD or a blob reverted to the base version (three-way blob compare via `_blob_sha`, which uses `git rev-parse --verify -q` to avoid the bare-`rev-parse` missing-path footgun) while a legit conformer edit leaves a distinct third blob and is not flagged. Warns **always**; under `--strict-conformer` also `rollback_conformer_commits()` to the implementer HEAD **and blocks** — a `clobbered_files` flag threaded to the post-loop `blocked_reason` (per-subtask) / `final_blocked` (final) sets a block even when `_conformance_clean(last_res)` is True, so a clobber is never silently completed. Not auto-rolled-back in advisory mode — a legitimate revert-to-base is git-indistinguishable from a clobber. The final-tree pass applies the same guard with `base=run_branch`, `impl_head=staging HEAD snapshotted before that pass`. |
| Loop bound | `caps["conformance_rounds"]` (default 3) | Re-runs the conformer if its output is malformed or residuals remain. Exhausting the cap with residuals still present is a warning, not a failure. |
| BLT-axis observability + feedback | `_emit_bash_axis_warnings()` | After each round, parses the per-worker JSONL log at `<state-root>/runs/<id>/logs/<sid>-conformer.log` (or `final-conformer-r<N>.log` for the final pass) and surfaces two types: (1) **multi-invocation** (advisory only) — `conformer round N: ran <AXIS>_CMD K times in one round` — legitimate progressive testing (targeted → full suite → grep) is the common cause; surfaced for observability. (2) **retry-after-backgrounded** (feedback-injected) — `conformer round N: <AXIS>_CMD auto-backgrounded (bash_id=<id>) and was followed by another <AXIS>_CMD invocation` — the "retry-instead-of-recover" pattern. These Pattern B warnings are collected after each round and, if non-empty, formatted via `_format_check_feedback()` and passed as `extra_feedback` to the next round's `run_conformer()` call so the conformer can correct the behavior. Helpers `_count_bash_axis_invocations()` and `_count_orphaned_bg_axis()` are pure log-parsing — never raise. `_BLT_AXIS_RES` is a `dict[str, re.Pattern[str]]` containing compiled regexes for the test, build, and lint axes: test matches `pnpm/npm/yarn/bun/npx test` (and `vitest`), `vitest run`, `bin/rails test`; build matches `pnpm/npm/yarn/bun build`, `tsc`, `next build`; lint matches `pnpm/npm/yarn/bun lint`, `biome check`, `eslint`, `rubocop`. The `_count_orphaned_bg_axis` detection logic also accepts `BashOutput shell_id=<id>` polls as a valid recovery path — forward-compatible with future tool-surface changes. |
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
| `scan_conflict_markers()` | unresolved `<<<<<<<` markers in the run-branch worktree after integration — deterministic safety net |

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

### Resume integrity — `validate_resume_state()`
Enforces (one half of) DESIGN §6's "the run branch is the resume contract"
invariant — state.json's `waves`/`completed_waves` say *which* wave to
resume; the never-reset `leerie/runs/<run-id>` branch holds *the work*
every prior wave produced. Both must be coherent for resume to be safe.

On `--resume`: asserts `task` is present and non-empty; asserts `waves`,
`completed_waves`, `subtask_status` are well-formed *if present*. `waves` is
intentionally optional — a run interrupted before scheduling has none, and
`main()` handles that case with a clearer message. Rejects corrupt or
hand-edited state without rejecting a legitimately-early interruption.

The `except SystemExit` handler in `main()` guards `st.save()` behind
`st.data.get("task")` so that a failed `--resume` (which `die()`s before
state was loaded) does not poison the host-side `state.json` with a bare
`{"finished_at": …}` stub — that would block subsequent resume attempts
with "no usable task" instead of the clearer "no state.json".

`orchestrate()` also re-resolves the source-of-truth preference on every
`--resume` and overwrites `state.json`'s `source_of_truth_pref` with the
fresh value, so a change to `leerie.toml` or `LEERIE_SOURCE_OF_TRUTH`
between runs takes effect on resume.

Per-worker models are likewise re-resolved on every `--resume` from the
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
`gather_or_cancel` — a small `asyncio.gather` wrapper that, on the first
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
   `_reparented_orphans(self._seen)` to obtain the killable set and sends
   `SIGKILL` oldest-first (via the existing `_signal_pids`), stopping as
   soon as the ratio drops below `_PID_REAP_LOW_WATER = 0.75`. Killed PIDs
   are pruned from `_seen`; the exit-time `stop_and_reap` path is unchanged.
   `_reparented_orphans(seen: set[int]) -> list[int]` runs one
   `ps -eo pid,ppid,etimes` snapshot and returns, sorted oldest-first, the
   PIDs from `seen` that are simultaneously alive, reparented to init
   (`ppid == 1`), and at least `_PID_REAP_MIN_AGE_SEC = 60` seconds old.
   Module-level constants (placed next to `_DESCENDANT_POLL_SEC` /
   `_PID_EXHAUSTION_WINDOW`): `_PID_REAP_HIGH_WATER = 0.90`,
   `_PID_REAP_LOW_WATER = 0.75`, `_PID_REAP_MIN_AGE_SEC = 60`.

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
   is a background asyncio task spawned in `orchestrate()` next to `sampler_task`
   and cancelled in the same `finally` — mirroring `_memory_sampler`'s lifecycle.
   It is an **allowlist, never a `/proc` scan**: the reaper
   `os.waitpid(pid, WNOHANG)`s only PIDs in `_REAPABLE_PIDS` (~1 s;
   `ChildProcessError`/`OSError` benign → the PID is discarded, since it can
   never become reapable). `_mark_reapable(pids)` populates that set, minus
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
`worker_pids_max = 1024`, resolved by `resolve_worker_pids_max`:
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

**Memory-OOM naming (`_invoke`'s no-envelope path + `settle_subtask`,
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
`run_implementer`'s existing `except WorkerError` handler threads that
text into the synthesized `incomplete-handoff` envelope's `summary`
unchanged. `settle_subtask`'s `empty_handoff` handling (the rescue branch
that keeps committed work, and the no-commits branch that calls `fail()`)
both now prefer `res.get("summary")` — the worker's own diagnostic, when
present — over `validate_result`'s generic "checkpoint ... does not
exist" `message`, so a named OOM survives even when the subtask
ultimately terminates via the retry cap.

### Abnormal exit and rate-limit contract (DESIGN §6 *Cleanup on abnormal exit*)

All abnormal exits — Ctrl-C, SIGTERM/SIGHUP, WorkerError, unhandled
exception, or `RateLimitedExit` — route through
`_cleanup_on_abnormal_exit(st, full_purge=False)`. **State.json, the
run branch, per-subtask branches, and implementer checkpoints all
survive**; only worktrees are removed (and re-created idempotently on
`--resume` via `scripts/new-worktree.sh`).

Per-worktree removal has a 240s timeout — calibrated against a real
868 MB / 41k-file worktree (npm install + Next.js build) which takes
~45-90s uncontested, with several-fold growth under N-way concurrent
disk contention. Per-worktree failures (timeout or OS error) are
non-fatal and counted; if any failed, the cleanup emits one closing
log line pointing the user at `scripts/cleanup.sh --run-id <id>` to
finish manually. The pass is best-effort: a stale worktree on disk is
the worst case, not a corrupted run.

Per-worker `subprocess.TimeoutExpired` from `_invoke` (raised when the
worker hits `worker_timeout_sec`, default 5400s / 90 min) is caught
by both `run_implementer` (returns an `incomplete-handoff` envelope,
matching the WorkerError handoff path so settle_subtask's existing
machinery handles it) and `run_conformer` (logs + returns None,
matching the WorkerError advisory-phase semantics). Without these
catches the timeout escapes through the asyncio cancellation chain
into `main()`'s catch-all and dumps a multi-KB traceback — including
the entire `claude -p` command line — to the user's terminal.

`RateLimitedExit` is raised by `detect_session_limit(text)` inside
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
`os.execv(sys.executable, [sys.executable, __file__, "--resume",
"--run-id", <id>])` to re-exec the orchestrator itself (NOT the
launcher — the launcher is not baked into the container image and
its `--resume` path would attempt to spawn a new container; the
orchestrator already runs inside the container with state on disk
and accepts `--resume --run-id`). The `--max-workers` budget is NOT
reset across the re-exec: `worker_count` persists in state.json,
so a run that repeatedly hits the rate-limit still respects the
user's cap;
when `reset_at` is None because of an unparseable session-limit
message, sleep a fixed `RATE_LIMIT_RETRY_BACKOFF_SEC` (300 s) and
re-exec `--resume` the same way — we can't compute a wake time, so we
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
directly, logs a `leerie --resume <id>` hint, sets `exit_code =
EXIT_LOCKED` and `abnormal = False`, and falls through to the `finally`
(which must not re-run cleanup). This is checked *before* the
`reset_at` branch. `_sleep_then_reexec` is never called for this case.
The old `reset_at=None → exit 75 manual-resume` behavior is gone for
rate-limits (they auto-resume), but out-of-credits deliberately
preserves the surface-and-pause semantics for the reason above.

**Auto-resume override persistence.** The re-exec passes only
`--resume <id>` as argv — any CLI overrides on the original
launch (`--model`, `--max-workers`, `--max-parallel`, `--confidence-rounds`,
`--source-of-truth`, `--clarify`, `--no-push`) are **not** propagated
to the fresh process. They fall back to env vars (`LEERIE_*`) and
`leerie.toml` settings, which are re-resolved on every `--resume`
(see "Resume integrity" above). Users who rely on a non-default
setting should configure it via env or `leerie.toml` rather than a
single CLI flag, so an auto-resume preserves it. A manual `--resume`
(invoked by the user after they Ctrl-C the auto-resume wait, or after
the rare interrupt/execv-failure exit) can re-supply CLI overrides as
needed.

Ctrl-C (SIGINT) is **resumable** — same contract as every other
abnormal exit. The explicit "throw this away" gesture is
`scripts/cleanup.sh --run-id <id> --branches`, not Ctrl-C. This was a
behavior change from earlier versions of leerie where Ctrl-C ran a full
purge; the old design conflated user intent ("stop this run") with
run lifecycle ("nuke the artifacts").

---

## 5½. Mechanical-feedback loops (CRITIC pattern)

Every worker except the PR writer runs inside `_run_checked_loop` — a
generic async function that calls the worker, runs deterministic
structural checks on the output, and re-invokes with formatted
feedback if issues are found. The pattern is grounded in the CRITIC
framework (ICLR 2024): self-correction works only with external
tool-verified feedback, not intrinsic self-review.

### Core functions

| Function | Purpose |
|----------|---------|
| `_run_checked_loop(invoke, check, name, max_rounds, make_feedback_prompt)` | Generic loop: call → check → feedback → retry. Returns `(result, warnings)`. |
| `_confidence_axes_clear(conf, axes, threshold)` | Pure predicate: True when every named axis in `conf` is a number ≥ threshold. Used by the loop and by `settle_subtask`'s implementer confidence check. |
| `_format_check_feedback(issues, rnd, max_rounds)` | Formats issue list into the structured feedback block injected on re-invocation. |
| `_confidence_schema(axes)` | DRY helper: builds the §8 confidence sub-schema for the given score axes. Used by 9 worker schemas (including `fit_judge`; **not** `splitter`, whose output — required `children` only — carries no confidence axis). |

### Per-worker mechanical checks

Each returns `list[str]` — empty when clean. Pure Python, no LLM.

| Worker | Check function | Issue codes | Max rounds cap |
|--------|---------------|-------------|----------------|
| Classifier | `check_classifier_output(result, repo_root)` | `CATEGORY_NO_DIR`, `EMPTY_WHY`, `MANY_CATEGORIES`, `SAME_WORK_RISK`, `LOW_CONFIDENCE` | `judgment_check_rounds` (3) |
| Planner | `check_planner_output(result, repo_root, domain)` | `PHANTOM_PATH`, `DANGLING_DEP`, `EMPTY_CRITERIA`, `OVERSIZED`, `INTRA_DOMAIN_OVERLAP`, `PROTECTED_PATH`, `INTRA_DOMAIN_CYCLE`, `UNCOVERED_MIGRATION_SURFACE`, `LOW_CONFIDENCE` | `planner_check_rounds` (3) |
| Reconciler | `check_reconciler_output(output, plans)` | `RENAME_TO_NOWHERE`, `BAD_PREFIX`, `SELF_DEP`, `LOW_CONFIDENCE` | `judgment_check_rounds` (3) |
| Overlap judge | `check_overlap_judge_output(output, plans, repo_root)` | `PHANTOM_ARTIFACT`, `NO_FILE_OVERLAP`, `DROP_BREAKS_GRAPH`, `LOW_CONFIDENCE` | `judgment_check_rounds` (3) |
| Provision | `check_provision_output(result, repo_root)` | `WRONG_PM`, `MISSING_WORKDIR`, `EMPTY_RECIPE`, `LOW_CONFIDENCE` | `judgment_check_rounds` (3) |
| Implementer | `check_implementer_output(result, subtask, actual_files)` | `NO_PLANNED_FILES_TOUCHED`, `UNMET_CRITERION` | `implementer_confidence_retries` (2) |
| Integrator | `check_integrator_output(result)` | `LOW_CONFIDENCE` | `judgment_check_rounds` (3) |
| Conformer | (unchanged: `_conformance_clean` on observable signals) | — | `conformance_rounds` (3) |

`LOW_CONFIDENCE` is emitted by `_confidence_issues(conf, axes, threshold=9.0)`,
a helper that returns one issue string per axis below threshold. Each check
function calls it with its worker's schema-defined axes: classifier
`["classification"]`, planner `["task_understanding", "decomposition_quality"]`,
reconciler `["reconciliation"]`, overlap judge `["judgment"]`, provision
`["recipe_correctness"]`, integrator `["resolution"]`.

The reconciler's size-gate and cycle-gate retry paths also run
`check_reconciler_output` after each retry's `_apply_reconciler_output`,
logging warnings for any structural issues.

### Task-referenced file extraction

When the task string references files (detected by
`glob_task_references`), the orchestrator extracts structural elements
and injects them as an external coverage checklist into the planner's
prompt. This is a novel technique inspired by but distinct from the
executable-specification architecture of arxiv 2603.25773 (see
DESIGN.md §8 for the distinction).

| Function | Purpose |
|----------|---------|
| `_expand_braces(pattern)` | Pre-expands `{a,b}` brace groups that Python's `glob.glob` does not handle. Recursive for nested braces. |
| `glob_task_references(task, repo_root)` | Scans the task string for file-path tokens, expands braces, globs each pattern. Returns deduplicated `list[Path]`. |
| `extract_task_file_structure(task, repo_root)` | Extracts H3+ headings from `.md`/`.txt` (regex: `#{3,6}`; H1/H2 skipped as structural), numbered items (excluding TOC anchor links `[...](#...)`), list-item IDs from `.yaml`/`.yml` (regex: `^- id:`), and top-level mapping keys. Stdlib-only (no PyYAML). Returns `list[str]` or `None`. |
| `check_task_file_coverage(extracted, subtasks)` | Checks which extracted items are not referenced by any subtask. Returns `LOW_COVERAGE` issue when >50% uncovered AND item count ≤ `_MAX_COVERAGE_ITEMS` (50). Above the cap, returns empty (too dilute for meaningful gating). |
| `_format_task_file_structure(items)` | Formats extracted items as a prompt section for the planner. |
| `_MAX_COVERAGE_ITEMS` | 50. A planner with 5–15 subtasks can realistically cover ~50 items; above this the 50% threshold becomes unrealistic. |

No-op when the task doesn't reference files.

### P6 repo-map — `build_repo_map` + `rank_repo_map`

Implements DESIGN §5½ (P6) *Codebase structural map*. Both functions are
deterministic, lazy-import tree-sitter (so the module loads on a bare host
Python that lacks the package), and call no LLM.

| Symbol | Purpose |
|--------|---------|
| `_repo_map_cache_key(path)` | Returns `"<abs_path>@<mtime_ns>"` — a stable cache key that changes when a file is touched. |
| `_walk_calls(node)` | Walks a tree-sitter CST recursively, collecting bare-name identifiers from `call` expression function positions. Returns `list[str]`. Attribute callees (e.g. `obj.method`) are skipped — only bare-name callees become ref edges. |
| `_parse_repo_file(path)` | Parses one source file with `tree_sitter_language_pack.process()` (for defs/structure) and a tree-sitter CST walk (for call-site refs). Returns `(defs: list[str], refs: list[str])`. Returns `([], [])` on unsupported language or any error (graceful degrade). |
| `build_repo_map(repo_root, leerie_root)` | Walks all source files under `repo_root` (skipping `.git`, `node_modules`, `__pycache__`, etc.), parses each with `_parse_repo_file`, and builds `{"files": {rel_path: [def_sym, ...]}, "refs": {def_sym: {rel_path, ...}}}`. mtime-caches per-file parse results under `<leerie_root>/<REPO_MAP_CACHE_DIR>/<sha256(abs_path)>.pkl` — only files whose `mtime_ns` changed since the last call are re-parsed (Aider diskcache pattern). Cache dir created on first use. Always returns a valid dict; never raises. **Silent-degrade visibility (DESIGN §12):** if the repo contains source files (by extension, `_SOURCE_EXTS`) but the graph comes back empty, `_warn_repo_map_empty_once()` runs a functional probe (`_tree_sitter_extraction_works()` — parses a known snippet via `_parse_repo_file`) and emits exactly one warning per process **only if the probe confirms tree-sitter cannot extract symbols** (unavailable or API-incompatible → P6 silently a no-op). A genuinely non-code repo (no source files) or a working parser on a legitimately symbol-less repo stays quiet — no false positives. |
| `_pagerank(graph, personalization, damping, max_iter, tol)` | Personalized PageRank on a directed `dict[str, set[str]]` graph. Pure stdlib (no networkx). Handles dangling nodes (no out-edges) via a dangling-mass redistribution term. Converges when sum of per-node rank deltas < `tol`. Returns `dict[str, float]` (node → rank score). |
| `_render_repo_map_subgraph(repo_map, ranked_files, max_files)` | Renders the top `max_files` files from `ranked_files` as a compact text block: one line per file listing its defined symbols (`path: Sym1, Sym2, ...`). Files with no defs are omitted. |
| `_count_tokens_approx(text)` | Approximate token count: `max(1, len(text.encode()) // 4)` — ~4 bytes per token (GPT/Claude typical). Used by `rank_repo_map`'s binary-search budget fit. |
| `rank_repo_map(repo_map, seed_files, seed_symbols, token_budget)` | Builds a file→file edge graph via shared symbols (definer → referencing files), runs personalized PageRank biased toward `seed_files` and files that define/reference `seed_symbols`, then binary-searches the largest prefix of the ranked-file list that fits within `token_budget` tokens (default `DEFAULT_CAPS["repo_map_tokens"]`). Returns the ranked subgraph as a plain text string. Returns `""` when the map is empty. |

**Edge direction:** `build_repo_map` tracks `refs[sym] = {files that call sym}`. `rank_repo_map` builds a file→file edge from the definer of `sym` to each file that references it — so widely-referenced utility files accumulate high in-degree and surface as structural backbone.

**Personalization in `rank_repo_map`:** seed files get weight 1.0; files defining a seed symbol get 1.0; files *referencing* a seed symbol get 0.5. When no seed resolves to a known file, uniform personalization is used (scores all files equally, falling back to link structure only).

**Skip flag:** `resolve_skip_repo_map` (see §2 "Skip flags") gates the call; when `True`, `build_repo_map` is not called and the planner degrades to the prior grep/glob-only path.

### Phantom-path check

`PHANTOM_PATH` fires when a `files_likely_touched` entry does not exist
and no ancestor directory between the file and `repo_root` exists
either.  This catches hallucinated paths (e.g.
`src/totally/invented/dir/file.ts` when `src/totally/` does not exist)
while tolerating greenfield features that create new subdirectories
under an existing parent (e.g. `src/components/features/social/post.tsx`
when `src/components/features/` already exists).

### Migration-surface check

`check_planner_output` includes an `UNCOVERED_MIGRATION_SURFACE` check
(DESIGN §5 *Migration-surface completeness*). For each subtask whose
`intent` or `investigation_notes` matches a migration-signal regex
(phrases like "replaces direct `X`", "extract `X` as", "migrate from
`X`"), the check greps `repo_root` for the old-pattern string `X`,
collects files containing it, cross-references against
`files_likely_touched` across all subtasks in the domain, and emits the
issue when > 5 files are uncovered. The threshold avoids false positives
from comments, type definitions, and test fixtures. The CRITIC loop
feeds the issue back as structured feedback; multi-sample selection
deprioritizes samples that miss the migration surface.

| Symbol | Value |
|--------|-------|
| `_MIGRATION_SIGNAL_RE` | Compiled regex matching migration-signal phrases in subtask text |
| `_MIGRATION_SURFACE_THRESHOLD` | 5 — uncovered file count below which the check stays silent |
| `_grep_old_pattern(pattern, repo_root)` | `subprocess.run` grep for the pattern; returns set of file paths |

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

### Cap resolvers

Same resolution pattern as existing resolvers (CLI → env → TOML →
default): `resolve_judgment_check_rounds`,
`resolve_planner_check_rounds`,
`resolve_implementer_confidence_retries`, `resolve_planner_samples`.
Env vars: `LEERIE_JUDGMENT_CHECK_ROUNDS`,
`LEERIE_PLANNER_CHECK_ROUNDS`,
`LEERIE_IMPLEMENTER_CONFIDENCE_RETRIES`, `LEERIE_PLANNER_SAMPLES`.

---

## 6. Caps and their values

Defaults in `DEFAULT_CAPS` and the per-worker `claude_p` call sites.

### Code-enforced caps (the orchestrator counts these)
| Loop | Cap | On cap |
|------|-----|--------|
| subtask continuations (re-spawns of an implementer for the same subtask — both context-exhaustion handoffs *and* mid-execution clarifications consume from the same budget) | 3 (`subtask_continuations`) | return `blocked`; fatal at wave boundary |
| corrective retries of a *retryable* failure per subtask (`failed_retries`) | 1 | return `failed` |
| orchestrator-level conformer rounds per subtask (`conformance_rounds`) | 3 | exit the conformance loop; any residuals become `conformance_warnings` on the subtask result — never `failed` / `blocked` (DESIGN §9 *Post-work conformance*). Backgrounding-retry (Pattern B) warnings from round N are injected as structured CRITIC-pattern feedback into round N+1. |
| total worker invocations per run | 200 (`--max-workers`, also `LEERIE_MAX_WORKERS` env or `max_workers` in `leerie.toml`) | the cheap, runtime backstop in `State.bump_workers()`: raises `WorkerError`, abort, state saved for `--resume`. The complementary early check is `check_budget_feasibility()` at the plan/execute boundary (after `schedule()`, before `write_plan()`) — it estimates remaining `claude -p` calls from the planner output and `die()`s with `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` value before any implementer spawns, so a run that is mathematically unwinnable fails at the cheapest moment rather than mid-wave. See DESIGN §13 *Budget feasibility — fail fast at the cheapest moment* and §"Budget feasibility preflight" above. |
| per-subtask call-estimate (for the feasibility preflight) | 2.5 (`subtask_call_estimate`) | not a runtime gate; consumed by `check_budget_feasibility()` as the per-subtask multiplier in its remaining-call estimate. Default calibrated from successful runs at 2.0–2.31; the safety margin (next row) absorbs the lint-fighting inflator that pushes the ratio above 2.5 on environments-heavy repos. |
| budget-preflight safety margin | 1.15 (`budget_safety_margin`) | not a runtime gate; consumed by `check_budget_feasibility()` as the multiplier on `total_estimate` before comparison to `max_total_workers`. With the default `subtask_call_estimate=2.5`, the guaranteed cap headroom is ~1.44×. |
| concurrent workers within a wave | 5 (`--max-parallel`, also `LEERIE_MAX_PARALLEL` env or `max_parallel` in `leerie.toml`) | throughput throttle. Per-worker cgroup memory containment (see row below) keeps an OOM inside one worker's cgroup, so the wave-level parallelism can be high without risking cascade to sshd / lima-guestagent. Users on smaller VMs can opt down via `--max-parallel`. |
| turns per `claude -p` call | per worker (below) | worker stops; implementer → `incomplete-handoff` |
| per-worker wall-clock (`worker_timeout_sec`) | 5400 s (90 min) | worker killed; implementer → `incomplete-handoff` |
| per-worker idle-event warning (`worker_idle_warn_sec`) | 300 s (5 min) | log a `no stdout events in <gap>s` warning naming the worker, its PID, and any stderr tail. Observation-only — the worker is NOT killed; `worker_timeout_sec` remains the only kill. Surfaces silent-hang failures (a worker that never emits its first `system/init` event) so the user is not left with zero feedback between phase start and the 90-min hard kill. |
| per-worker cgroup memory cap (`worker_memory_max_bytes`) | auto-derived from `/proc/meminfo` via `_auto_worker_memory_max` (VM RAM split across `max_parallel + 1` slots, **floored** at 8 GiB — no upper clamp; the prior 4 GiB clamp sat below the measured ~6.3 GiB build+resident-claude peak, so no VM size could auto-derive enough for a build-running worker, see DESIGN §6 *Memory containment*), or `--worker-memory-max SIZE` / `LEERIE_WORKER_MEMORY_MAX` / `worker_memory_max` in `leerie.toml`. Suffixes K/M/G/T accepted. The aggregate `leerie.slice/memory.max` cap (row below) is the real VM-OOM backstop, so this per-worker floor can be generous — but build-heavy waves should pair it with a lower `--max-parallel` so concurrent capped workers don't collectively exceed the slice cap | the kernel OOM-kills inside the worker's cgroup; sibling workers, the orchestrator, and host-side services (sshd, lima-guestagent) are not eligible victims. Enforcement goes through the **cgroup broker** (`scripts/cgroup-broker.py`), which the dropped-privilege orchestrator drives over a Unix socket — worker enrollment and limit-setting cannot be done from the orchestrator's own identity (a subtree merely `chown`ed after creation keeps its controller limit files root-owned; cross-scope migration needs common-ancestor write; both reproduced). The broker creates `<V2_ROOT>/leerie.slice/leerie-w-<sid>` (cgroup **v2** — `V2_ROOT` is the literal `/sys/fs/cgroup` rootful/Fly, or the systemd-delegated user slice under rootless containerd via `LEERIE_CGROUP_V2_ROOT`, DESIGN §6 *Rootless exception*) or the split `pids/`+`memory/` hierarchies at the fixed `V1_ROOT` (cgroup **v1/hybrid**, e.g. some Fly VMs, never rootless) and sets its `memory.max`. Local nerdctl needs the launcher's cgroup bind-mount — `bind-propagation=rshared` rootful, a plain bind (no propagation flag) rootless — + `--cgroupns=host` (default `--cgroupns=private` + `nsdelegate` blocks migration even for the broker); Fly's microVM exposes cgroupfs directly. `_cgroup_probe` asks the broker to round-trip a create+enroll+destroy — the true test of the worker path — and `enforce_and_record_cgroup_containment` `die()`s before the first worker if it fails (unless `--dangerously-allow-uncapped`). See DESIGN §6 *Memory containment*. |
| per-worker cgroup PIDs cap (`worker_pids_max`) | 1024, or `--worker-pids-max N` / `LEERIE_WORKER_PIDS_MAX` / `worker_pids_max` in `leerie.toml` (positive integer; `resolve_worker_pids_max` `die()`s on bad input) | kernel rejects further `fork()` from any process in the worker cgroup once the count is reached. Catches runaway fork-bomb behavior in tool subtrees while still admitting a legitimate heavy conformance run (a subprocess-heavy full test suite bursts past a too-low cap in seconds — faster than the mid-run reaper's 60 s min-age gate). Raise it per-repo for suites heavier than the 1024 default. |
| aggregate container memory cap (`leerie.slice/memory.max`) | auto-derived in `scripts/container-entry.sh` (PID 1) from VM `MemTotal` in `/proc/meminfo`: `MemTotal - max(1 GiB, 12.5%)`, reserving headroom for PID 1 + VM daemons (sshd, lima-guestagent, containerd). Overridable via `LEERIE_CONTAINER_MEMORY_MAX_BYTES` (raw bytes); `0`/`max` opts out. **Intentional provenance deviation:** unlike the per-worker cap, there is *no* CLI flag / `leerie.toml` key / `DEFAULT_CAPS` entry — the cap is applied by the shell entrypoint *before* the Python orchestrator (and its resolver machinery) starts, so a Python-side resolver could not set it in time; the env var is the single override knob. Best-effort: any read/write failure leaves the slice uncapped (prior behavior). Sets `memory.max` (RAM) only, not `memory.swap.max` — a capped slice may swap before the cgroup OOM fires, which still contains the pressure to the slice (no global OOM); bounding total RAM+swap via `memory.swap.max` is a possible future refinement. | when the slice's aggregate RSS exceeds the cap the kernel triggers a *cgroup-scoped* OOM (`CONSTRAINT_MEMCG`) that kills a process *inside the container* (per-worker `-998` protection is only relative within the slice), instead of a VM-wide *global* OOM that would kill unprotected host-session processes — the `nerdctl` client especially — and orphan the container (wedging the run-dir flock). See DESIGN §6 *container boundary's hidden precondition*. |
| auth/quota backoff budget (`auth_retry_max_sec`) | 300 s (5 min) | `claude_p()` retries the worker with `tenacity` exponential backoff (initial 15 s, max 120 s, ±5 s jitter) on 401/429/529/auth-message envelopes. Budget exhausted → `WorkerError` naming the subscription cap (401/429/auth-text) or the transient overload (529). See §3 *Auth/quota backoff*. |
| mechanical-feedback rounds for judgment workers (`judgment_check_rounds`) | 3 | classifier, reconciler, provision, overlap judge, integrator. The orchestrator runs deterministic checks (file existence, graph cycles, lockfile consistency) on each worker's output and re-invokes with structured feedback if issues are found. On exhaustion, proceed with best result + warnings. CRITIC pattern (ICLR 2024). |
| mechanical-feedback rounds for planner (`planner_check_rounds`) | 3 | Same CRITIC pattern, but higher default because the planner has richer checks (phantom paths, dangling deps, intra-domain cycles, protected paths, task-file coverage). |
| implementer confidence retries (`implementer_confidence_retries`) | 2 | Separate from `subtask_continuations`. Orchestrator checks confidence scores + scope drift + unmet criteria on complete results and re-invokes as a continuation if issues found. |
| planner samples (`planner_samples`) | 3 | Independent parallel invocations per domain. Mechanical selection: fewest issues, tiebreak on subtask count. Set to 1 to disable. Also `LEERIE_PLANNER_SAMPLES` env or `planner_samples` in `leerie.toml`. CLI: `--planner-samples`. |
| P6 repo-map token budget (`repo_map_tokens`) | 1000 | Token budget for the personalized-PageRank-ranked subgraph injected into the planner/splitter (DESIGN §5½ (P6) *Codebase structural map*). The subgraph is binary-searched to fit within this many tokens. Not user-tunable via CLI / env / toml — internal to `build_repo_map()` / `rank_repo_map()`. |
| P1 recursive decompose max depth (`decompose_max_depth`) | 5 | Maximum recursion depth for `recursive_decompose()` (DESIGN §5½ (P1) *Recursive judge + splitter*). Recursion terminates at depth ≥ 5 even if `fit_judge` still scores below `decompose_fit_threshold`. A depth-5 tree can represent up to 32 leaves from one subtask. Not user-tunable via CLI / env / toml. |
| P1 fit-judge pass threshold (`decompose_fit_threshold`) | 0.70 | `fit_judge` confidence score at or above which a subtask is accepted as a leaf (well-fit). MEASURED on n=24 telemetry-labeled subtasks: oversized mean 0.26 vs well-fit mean 0.84 — 0.57 separation, 88% accuracy at 0.70. Not user-tunable via CLI / env / toml. |
| P1 no-progress guard (`decompose_noprogress_rounds`) | 2 | Consecutive recursion rounds that produce no child with a fit score above the parent's before the subtask is accepted as a leaf with a warning. Prevents a degenerate splitter from looping to `decompose_max_depth`. Not user-tunable via CLI / env / toml. |

### P1 recursive decomposition surface (DESIGN §5½ (P1))

`partition_files(files: list[str], chunk_size: int) -> list[list[str]]`
Deterministic chunker for the migration-sweep path. Splits `files` into
non-overlapping chunks of at most `chunk_size` (default 8). 100% coverage
and 0 overlap are guaranteed by construction (no LLM). When `chunk_size < 1`,
returns `[list(files)]` (degenerate guard). Used by `recursive_decompose()`
when `len(files) > 8` so the code — not the LLM — decides the file partition
(measured correction: LLM splitter dropped 14/29 migration files in testing);
the splitter worker then only *labels* the pre-computed chunks.

`_remap_vanished_deps(subtasks: list[dict], mapping: dict[str, list[str]]) -> None`
Mutates `subtasks` in place: rewrites every `depends_on` reference to an id that
vanished from the plan, per DESIGN §5 *Id-vanishing operations*. `mapping` is
`{vanished_id: [successor_ids]}`; fan-out (expansion — parent → N leaves) and prune
(drop — id → `[]`) are the same operation over it. Dedups after the rewrite and skips
self-references, mirroring `_apply_overlap_merge`'s discipline. An empty `mapping` is
a no-op; a dep absent from `mapping` passes through untouched. Called from four sites:
`recursive_decompose()` (intra-generation sibling edges), `phase_plan()` (cross-subtask
edges after expansion), and both phase-3 soft-drop filters (`filter_offtree_subtasks`,
`filter_satisfied_subtasks`) with an all-empty mapping to prune dropped ids.

`recursive_decompose(subtask, depth, st, caps, models, efforts, repo_root, *, repo_map=None, _parent_score, _noprogress_count) -> list[dict]`
Async recursive function implementing DESIGN §5½ (P1) *Task-Context Fit*. For each
subtask: calls `fit_judge` to score Task-Context Fit (0–1); returns `[subtask]`
if score ≥ 0.70 (threshold from `caps["decompose_fit_threshold"]`) or depth ≥
`caps["decompose_max_depth"]` (5); checks the no-progress guard
(`caps["decompose_noprogress_rounds"]` consecutive rounds of no improvement
accept the subtask as leaf); then splits via either:
  - **Migration path** (≥ 9 files): `partition_files()` owns the file→chunk
    partition (deterministic, 100% coverage). The `splitter` worker is then
    invoked in **label-only mode** (`_label_migration_chunks()`) to write a
    distinct `title` + `success_criteria_seed` per pre-computed chunk — it must
    not move files. §12 code-enforces distinctness: on splitter failure or a
    mismatched label set, every chunk falls back to a distinct deterministic
    label (`_deterministic_chunk_label()`), never an identical parent-copy.
  - **Coupled path** (≤ 8 files): `splitter` LLM worker — structural seam detection

After recursing into a generation's children, the function calls
`_remap_vanished_deps()` over the flattened leaves with `{child_id: [its_leaf_ids]}`
for every child whose own id did not survive its expansion. This is the only frame
that observes an intermediate id: the splitter may give a child `depends_on` on a
*sibling* child (`prompts/splitter.md`), and if that sibling later splits, its id
vanishes mid-tree — invisible to `phase_plan`, which only ever sees flattened leaves
(DESIGN §5½ *Wire-in to phase_plan*). On the migration path the map is always empty
(`_migration_child` builds children in code; none can name a sibling), so the call is
a no-op there.

`repo_map` (the once-built global symbol graph passed from `phase_plan`) is
re-ranked per node via `rank_repo_map(repo_map, node_files, [])` and injected
into each `fit_judge`/`splitter` prompt as a "RANKED REPO-MAP SUBGRAPH" section
(DESIGN §5½ P6 — "feed the same to the splitter, re-ranked to each node's
files"). `None` (skip_repo_map or build failure) omits the injection.

Every `fit_judge` and `splitter` invocation calls `st.bump_workers(caps)` before
`claude_p()`, which is passed the full required signature
(`cwd=str(repo_root)`, `autonomous=False`, `caps=caps`). Both workers use
`INSPECT_TOOLS` (read-only).

`SCHEMAS["fit_judge"]` — required fields: `score` (number 0–1), `rationale`
(string), `diffuse` (string, narrates the diffuse coupling when score < 0.70),
`confidence` (sub-schema via `_confidence_schema(["fit"])`).

`SCHEMAS["splitter"]` — required field: `children` (array, `minItems: 1`). Each
child mirrors the planner subtask shape: required `id`, `title`,
`success_criteria_seed`; optional `intent`, `scope_note`, `files_likely_touched`,
`depends_on`, `requires`, `provides`, `size`, `investigation_notes`.

Both workers are registered in `WORKER_TYPES` and `EFFORT_DEFAULT_PER_WORKER`
(both default to `"high"`). Both are absent from `MODEL_DEFAULT_PER_WORKER`
(default opus via the global `MODEL_DEFAULT` fallback).

`--max-turns` by worker: classifier 60, planner 100, reconciler 30,
plan_overlap_judge 30, provision 30, integrator 60, implementer 120,
conformer 60, judge 40, heal patch_generator 40, pr_writer 20, fit_judge 30,
splitter 30. For
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
every retryable-path return from a producer (`validate_result`,
`check_branch_has_commits`, the inline dirty-worktree check) carries a
`failure_kind` in `_RETRYABLE_FAILURE_KINDS`. When adding a new
retryable failure mode, extend the enum and update the producer in the
same change.

| Failure | Tier | `failure_kind` / source |
|---------|------|-----------------|
| branch has no commits ahead of the run branch | Retryable *unless* the success criteria are already met on the run-branch HEAD (a sibling subtask committed this deliverable this run, or it was already on the base tree — DESIGN §8 *The mid-run sibling case* + *Scope*), in which case `settle_subtask` settles it `complete` (`dropped_subtasks` reason `"already_satisfied_mid_run"`) and it never reaches this tier | `"no_commits"` from `check_branch_has_commits` |
| worktree left dirty | Retryable | `"dirty_worktree"` from the inline dirty-worktree check in `settle_subtask` |
| `incomplete-handoff` worker produced no checkpoint on disk | Retryable | `"empty_handoff"` from `validate_result`'s incomplete-handoff branch when the checkpoint file is missing. Triggers in two known cases: (1) Claude Code session-limit / rate-limit no-op workers leave no checkpoint (primarily caught by `detect_session_limit()` upstream; this is the safety net for a message-format change), and (2) a worker that hit `--max-turns` with no checkpoint written, which `run_implementer`'s WorkerError handler synthesizes into the same envelope. Both are corrective-note cases. |
| cross-field invariant violation (other) | Terminal | `"broken"` from `validate_result` |
| diff touched a protected path | Terminal | `"broken"` from `check_diff_scope` |
| worker-level error (timeout, schema-invalid twice) | Terminal | `"broken"` from `WorkerError` path |

`settle_subtask` routes every failure through `_retryable_failure` via the
`fail(kind, reason)` helper. Retryable consumes the retry cap; terminal ends
the subtask on first occurrence.

On a retryable failure that will loop, `fail()` calls
`_reset_subtask_worktree(sid, leerie_dir, run_id)` to remove the leftover
per-subtask worktree directory and its branch
(`leerie/subtasks/<run-id>/<sid>`), then `git worktree prune` to clear the
stale `.git/worktrees/<sid>/` metadata entry, so the retry's `new-worktree.sh`
reaches its "fresh subtask" path on the next iteration. Without this reset the
retry re-runs the script against a still-registered worktree and an existing
branch — the second `git worktree add -b` fails with
`fatal: a branch ... already exists`, the `WorkerError` escapes
`settle_subtask`, and `gather_or_cancel` takes down the rest of the wave.

---

## 6½. Per-repo dependency provisioning

Implements DESIGN §6½. The provision phase fires once per fresh run,
between classify and plan; on `--resume` the whole fresh-run else-branch
of `orchestrate()` is skipped, so no re-fire check is needed.

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

`detect_recipe_from_lockfiles(repo_root) -> list[dict]` is the
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

`validate_provision_recipe(recipe) -> None` enforces (raises `ValueError`
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

Insertion point in `orchestrate()`: inside the `else:` (fresh-run)
branch, after the `_write_run_json(...)` block and before
`gather_answers(st, supplied)`. Step order:

1. **Docs-only short-circuit.** If the categories from classify
   contain no code-touching category (only `documentation`, etc.),
   record `kind: none` and return.
2. **Setup hook.** `run_setup_hook(repo_root, log_dir, st)` execs
   `<repo>/.leerie-setup.sh` if present (10-min timeout, streams to
   `<state-root>/runs/<id>/logs/setup-hook.log`). Idempotent via
   `st.data["provision"]["sh_hook_ran"]`. Nonzero exit → `die()`.
   **Runs as the non-root `leerie` container user; no sudo.** The hook
   can install user-space tooling (`mise install <lang>@<version>`,
   anything writing to `~/.local/bin`) and pre-populate fixtures, but
   cannot `apt-get install` or write to system directories. Repos
   that need root-level system packages maintain a fork of the leerie
   Dockerfile and override `IMAGE_TAG`; out of scope for the hook.
3. **Mise go-override synthesis.** `synth_mise_go_override(
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
4. **Mise install.** `run_mise_install(repo_root, log_dir, st)`:
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
   `tools[name][0].version` is the value rendered in `leerie --list`
   and one-line log summaries.
6. **Table-first detection.** `detect_recipe_from_lockfiles(
   repo_root)`. Non-empty result is the recipe (marked
   `source: "table"` in state).
7. **LLM fallback.** Empty table result → `gather_provision_fixtures(
   repo_root)` assembles inputs (see below), `claude_p("provision",
   prompt, fixtures, SCHEMAS["provision"], model)` returns a
   recipe (marked `source: "llm"` in state).
8. **Validate.** `validate_provision_recipe(recipe)`. Reject →
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
   every recipe consumer (most visibly `capture_conformance_baseline`,
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
10. **Export env.** If `synth_mise_go_override()` created an override
    file, `os.environ["MISE_OVERRIDE_CONFIG_FILENAMES"]` is set to
    its path so every downstream worker subprocess inherits it.

### Helper functions

| Function | Purpose |
|---|---|
| `gather_provision_fixtures(repo_root) -> dict` | Assembles the LLM-worker input set under a 24KB total ceiling. README extracted by `extract_readme_sections()`; root manifests (`package.json`, `pyproject.toml`, `go.mod`, `Cargo.toml`, `Gemfile`, `Makefile`, `pom.xml`, `build.gradle*`) included if present; workspace child manifests capped at 3 (1KB each) for monorepos; up to 2 `.github/workflows/*.yml` files matching `(?i)ci\|test\|build\|release` (skip `codeql\|stale\|dependabot`); optional `CONTRIBUTING.md` / `docs/DEVELOPMENT.md` capped at 4KB. |
| `extract_readme_sections(text) -> str` | Header-aware extractor. Strips leading emoji/punctuation before keyword match. Three header styles: ATX (`## ...`), setext (`...\n===` / `...\n---`), asciidoc (`== ...`). Keeps ≤1KB intro + matched sections (8KB post-extract budget). Section-match regex: `(?i)install\|getting[\s-]?started\|quick[\s-]?start\|setup\|usage\|\brun\b\|develop\|build(ing)?( from source\| instructions)?\|compil(e\|ing)( from source)?\|download\|from source\|requirements\|prerequisites\|dependenc(y\|ies)`. Fallback chain on no header match: code-fence detector (`pip install`, `npm install`, `cargo`, `brew`, `go install`, `apt-get`, `make` patterns, ±10 lines) → final top-6KB fallback. |
| `run_setup_hook(repo_root, log_dir, st)` | Execs `<repo>/.leerie-setup.sh` if present with a 10-min timeout via `run_streaming` (live output to terminal + persistent log at `<log_dir>/setup-hook.log`); sets `st.data["provision"]["sh_hook_ran"] = True` on success. |
| `synth_mise_go_override(repo_root, run_dir) -> Path \| None` | See step 3 above. Returns the absolute path to the override file or `None` if no synthesis was needed. |
| `run_mise_install(repo_root, log_dir, st)` | Runs `mise install` + `mise ls --current --json` at `repo_root`. The install streams via `run_streaming` so the user sees per-tool progress on a first-run Python/Ruby/Rust install. |
| `_format_provision_recipe_section(recipe, *, audience) -> str \| None` | Renders the persisted recipe as a `PROVISION_RECIPE:` block for injection into implementer or conformer prompts. Audience-specific framing ("decide whether your subtask needs them" vs "ensure deps before BUILD/LINT/TEST"). Returns None when the recipe is empty or all-`none`. |
| `phase_provision(repo_root, st, models)` | Orchestrates all of the above. Detects + persists the recipe; does NOT execute it (workers run installs in their worktrees per DESIGN §6½). Exports `MISE_OVERRIDE_CONFIG_FILENAMES` to `os.environ` if a synth override was created, so all downstream worker subprocesses inherit it. |
| `run_streaming(cmd, ..., log_path, verbosity, ...)` | Async subprocess helper with live-streamed stdout+stderr, persistent log file, bounded tail deque, and `TimeoutExpired` carrying the tail in `.output`. Used by `run_mise_install` and `run_setup_hook`; replaces the previous `run_proc` calls that buffered output for the entire run duration. |

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

### Worker-driven install (replaces per-worktree replay)

`scripts/new-worktree.sh` does just the `git worktree add` and prints
the worktree path. There is **no orchestrator-driven install** after
that — the implementer runs the install itself from its own worktree
via its Bash tool, against the shared package-manager caches. The
conformer does the same before running BUILD/LINT/TEST.

How the recipe reaches the worker:

1. `git worktree add` checks out the worktree (tracked files only).
   It starts with no `node_modules/` / `.venv/` / `target/`, by
   design.
2. The orchestrator parses the worktree path from the script's stdout.
3. `run_implementer` (and later `run_conformer`) read
   `st.data["provision"]["recipe"]` and inject it as a
   `PROVISION_RECIPE:` block in the worker's user prompt via
   `_format_provision_recipe_section(...)`.
4. The worker's prompt (see `prompts/implementer.md` §2 and
   `prompts/conformer.md` §Input) instructs it to decide whether the
   subtask needs the install and to run the command from its
   worktree if yes. The shared store / cache makes re-runs across
   worktrees fast.
5. If the recipe is missing or empty (docs-only run), no
   `PROVISION_RECIPE:` block is injected and the worker proceeds
   without one.
6. Install failures inside a worker surface through the worker's
   normal exit machinery — a hard-failing build/test in the
   implementer becomes a `failed` or `blocked` status; in the
   conformer it surfaces as a `tests-failed: …` advisory warning
   (DESIGN §9).

Why this shape (vs. an orchestrator-driven install at `repo_root` or
per-worktree replay):

- The host's repo is bind-mounted at `repo_root`, so an
  orchestrator-driven install there writes linux-arm64 native
  binaries into the host's darwin `node_modules`, corrupting the
  host's checkout.
- Per-worktree pre-install is wasted work for subtasks that don't
  need built deps (config-only, doc-only, pure-code refactors that
  don't run tests). The barnacle reference run showed ~half of
  implementer subtasks correctly skip install when given the choice.
- `claude -p`'s built-in stream-event plumbing surfaces Bash tool
  I/O to the orchestrator log live, so an install running inside a
  worker is visible to the user without any special orchestrator
  streaming code.

The `MISE_OVERRIDE_CONFIG_FILENAMES` env var that `phase_provision`
synthesizes for polyglot Go repos (go.mod with no `.go-version`
sibling) is exported to `os.environ` once in `phase_provision` (and
re-exported from persisted state on `--resume`); worker subprocesses
inherit it without any per-worker plumbing because `_invoke` does
not pass an explicit `env=` to `create_subprocess_exec`.

**Convention-doc injection (`CONVENTION_DOCS:` block).** Alongside the
recipe, `run_implementer` injects the repo's authoritative convention
docs so the implementer writes UI to the repo's design conventions on
the first try rather than drifting and relying on a post-hoc conformer
catch (DESIGN §9). It calls `discover_rules_files(st.repo_root)` — the
same discovery the conformer uses — and renders the surviving paths
(relative to `repo_root`) as a `CONVENTION_DOCS:` line in the user
prompt, using the same relative-path formatting as `run_conformer`'s
`RULES_FILES:` line. Paths only, not contents: the implementer runs in
a full worktree checkout and opens the docs relevant to its subtask, so
inlining a large design-system doc into every prompt is avoided. When
discovery returns nothing, no block is injected. `st.repo_root` is
already in scope in `run_implementer` (set at `State` construction), so
this needs no new parameter or call-site change. The `prompts/implementer.md`
§3 evidence gate and §4 Implement step name this block so the worker
reconciles the pattern it followed against the discovered conventions.

### Auto-capture of repo dependencies

Implements DESIGN §6½ *Auto-capture of repo dependencies*. All Python
surface lives in `orchestrator/leerie.py`.

#### Capture functions

| Function / Constant | Signature / Value | Role |
|---------------------|-------------------|------|
| `_DEPCAP_TOTAL_BUDGET` | `307200` (bytes) | Byte ceiling for the install-command hint fed to the dep_capture worker (~300 KB ≈ 75k tokens). Mirrors the `gather_provision_fixtures` add_bytes/hit_ceiling idiom. |
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
| `capture_repo_deps` | `async (repo_root: Path, st: State, caps: dict \| None, models: dict[str, str] \| None, efforts: dict[str, str \| None] \| None, replace: bool = False) -> None` | Main entry point. Guards: `resolve_capture_deps`, caps/models/efforts availability, `log_dir` existence, committed `.leerie/Dockerfile` skip. Builds a **manifests-first** corpus: `_gather_dep_manifests` (primary) + `_extract_depcap_commands` (secondary install-command hint); if BOTH are empty, returns. Checks worker budget; invokes `claude_p(schema_key='dep_capture', ...)` with `load_prompt("dep_capture")`, composing a two-section user prompt (manifests, then install-command hint). **`replace=False` (default, every automatic seam):** writes `setup_packages` (via `_merge_setup_packages`, never-clobber) and `language_installs` (new managers only, keyed by `manager` field, never-clobber) to `.leerie/config.toml`. **`replace=True` (only the operator-driven `--recapture --force` path):** wholesale-replaces both keys from the fresh capture (drops deps no longer captured); an empty capture leaves the existing config untouched. Writes via `_write_config_toml_keys`. Non-fatality is enforced at each call site's `try/except`, not inside the function. |
| `resolve_capture_deps` | `(repo_root: Path) -> bool` | env `LEERIE_CAPTURE_DEPS` > `.leerie/config.toml` `capture_deps` key; default `True`. No CLI flag and no `leerie.toml` tier (env → config → default only). |

#### dep_capture worker

`dep_capture` is a non-WORKER_TYPES worker (like `pr_writer`) registered in
`SCHEMAS`, `_allowed_schema_keys`, `EFFORT_DEFAULT_PER_WORKER` (high), and
`resolve_models`/`resolve_efforts`. It is **absent** from
`MODEL_DEFAULT_PER_WORKER`; its `opus` default comes from the global
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
| `setup-run.sh <run-id>` | Creates `leerie/runs/<run-id>` **only if absent** — never force-resets it (an existing branch carries completed waves; resetting it would destroy resume state). Records the working branch (HEAD-at-run-start) to `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/working-branch` on first run only. Adds the run-branch worktree at `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/staging` if missing. Safe on `--resume`. |
| `new-worktree.sh <id> <run-id>` | Creates `leerie/subtasks/<run-id>/<id>` worktree at `${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/<id>` branched off the current `leerie/runs/<run-id>` tip. Canonicalizes the worktree path to absolute (`pwd -P`) before comparing against `git worktree list --porcelain` output (which always uses absolute, symlink-resolved paths); reuses an existing worktree/branch if present (resume after handoff). Prints the absolute worktree path. The run-branch (`leerie/runs/…`) and subtask-branch (`leerie/subtasks/…`) prefixes are deliberately disjoint so neither is an ancestor ref of the other — git's loose ref store cannot hold a ref AT a path and another ref UNDER that same path simultaneously. |
| `integrate.sh <id> <run-id>` | From repo root, inside the run-branch worktree (`${LEERIE_STATE_DIR:-.leerie}/runs/<run-id>/worktrees/staging`): `git merge --no-ff leerie/subtasks/<run-id>/<id>`. Exit 0 clean; exit 1 on conflict, leaving the worktree mid-merge for an integrator; exit 2 on precondition failure (run-branch worktree or subtask branch missing) — `integrate_wave` treats exit 2 as fatal via `die()` and does *not* spawn an integrator, since the worktree-less case would fail in confusing ways. |
| `finalize.sh <run-id>` | Run-branch verifier. Exits 0 if `refs/heads/leerie/runs/<run-id>` exists and contains at least one commit beyond the working branch; exits non-zero with a diagnosis otherwise. The working branch is **never** modified — leerie does not merge into it locally; the PR is the proposed integration. The push and PR step lives in the **host launcher** (`leerie` bash script), not in the container — it runs after `nerdctl run` exits cleanly, using the host's own `git push` + `gh pr create` against the host's auth state. See "Host-side finalize" below. |
| `cleanup.sh [--run-id <id> \| --all-runs] [--branches \| --subtask-branches]` | Default (no flag): scans `<state-root>/runs/*/state.json` for the most-recently-failed run (most recent without `finished_at`), confirms y/N, then removes only that run's worktrees + prunes git metadata. State dir stays as audit. `--run-id <id>` is an explicit single-run cleanup (worktrees only). `--all-runs` runs the same per-run cleanup across every run dir under `<state-root>/runs/`. `--branches` (combinable with `--run-id` or `--all-runs`) additionally deletes the matching run branches *and* subtask branches (`leerie/runs/<id>` and `leerie/subtasks/<id>/*`). `--subtask-branches` deletes only the subtask branches and keeps `leerie/runs/<id>` (the post-finalize default — the run branch is the PR head and must outlive the orchestrator). Without either flag, all branches are kept as an audit trail. State dirs are always preserved by `cleanup.sh`. Ctrl-C and every other abnormal exit in the orchestrator also preserve state — they call `_cleanup_on_abnormal_exit(full_purge=False)`. There is no `full_purge=True` call site today; the flag is retained as a future hook for an explicit-purge gesture, but no current code path uses it. |

A run branch `leerie/runs/<run-id>` is never reset once created — this is the invariant `--resume` depends on. See `DESIGN.md` §6 ("the run branch is the resume contract").

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
   working branch, captured stderr, exact retry command), update
   `run.json` with `push_error`, exit non-zero.
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
`_auto_detect_fly_runtime(run_id, explicit_runtime)` helper checks for
`$LEERIE_STATE_HOST_DIR/runs/$run_id/fly-machine.json`. If
present and `explicit_runtime` is empty, it returns 0 and the caller
promotes the local runtime variable to `fly`. Applied to `--resume` (which
also appends `--runtime fly` to `REWRITTEN_ARGS` and tracks the
`_RUNTIME_EXPLICIT` flag), `--stop`, `--kill`, and `--finalize`. When the
user explicitly passes `--runtime local` on a Fly-originated run, `--resume`
warns but respects the choice; the fast-path verbs reject it as before.

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
  version drift on `--resume` and update the machine's image before
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
    volume so the user can attach to inspect and then `leerie --resume`. On the
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
| `FLY_VM_DISK_GB` | `8` (on Fly runtime) | Per-machine Fly volume size in GB, mounted at `/work` — the path where the seeded repo, `.leerie/runs/<id>/` state, and per-subtask worktrees all live (and grow). The launcher defaults to `8` when `RUNTIME=fly` and no explicit value is given (CLI / env / toml). The volume is destroyed when the machine is destroyed (clean exit or `--kill`). Override to a larger value for runs that hit ENOSPC (the rootfs is hard-capped at 2,000 IOPS / 8 MiB/s — N-wide worktree fans-out hit both the IOPS ceiling and the size cap fast). `/home/leerie` (caches + the `.claude` auth bundle) stays on the rootfs — `seed_auth` runs unconditionally on every resume, so the auth bundle is refreshed from host `$STAGE` regardless of pause length. |
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

   The launcher's `$STAGE` build skips `.claude/local` (~408 MB
   host npm install of `@anthropic-ai/claude-code`) — the leerie
   image installs claude globally via the Dockerfile, so shipping
   the host's local install is pure dead weight — plus
   `.claude/plugins/cache/` and `.claude/plugins/marketplaces/`
   (hundreds of MB of installed plugin contents, dominated by
   per-plugin `node_modules/` like vercel's ~150 MB; rebuilt on
   the remote in step 6 from the small JSON metadata files that
   ride along). Empirically the stage drops from ~642 MB to
   ~30 MB pre-gzip / ~15 MB on the wire. This is load-bearing: at
   ~640 MB the `ssh console -C` stdin pipe was hitting EOFs in
   live testing.

   On transient "tunnel unavailable" failure from a freshly-spawned
   flyctl agent, the seed retries once after `flyctl agent restart`.

3. **Token fallback.** If `$STAGE/.claude/.credentials.json` was
   not written (Linux, or macOS Keychain extraction failure) but
   `$CLAUDE_CODE_OAUTH_TOKEN` is set, `seed_auth()` writes a
   minimal credentials JSON
   `{"claudeAiOauth":{"accessToken":"<token>"}}` directly to
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
The host pushes the run branch via `leerie --finalize` after `fetch_branch`
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
child_env["TZ"] = "${_host_tz}"
# Belt-and-suspenders Bedrock activation (when _BEDROCK_ACTIVE=true on
# the host): variables substituted host-side before the script is piped
# to the machine (same pattern as USER_REPO and TZ above).
if "${_BEDROCK_ACTIVE}" == "true":
    child_env["CLAUDE_CODE_USE_BEDROCK"] = "1"
    if "${_BEDROCK_PROFILE}":
        child_env["AWS_PROFILE"] = "${_BEDROCK_PROFILE}"
    if "${_BEDROCK_REGION}":
        child_env["AWS_REGION"] = "${_BEDROCK_REGION}"
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
path — via `leerie --finalize <run-id>` after reattach.

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

**Pre-classify resume — `--resume` is host-only, task is recovered
from `task.txt`.** `leerie --resume` on the host means "wake the paused
Fly machine"; the in-machine orchestrator interprets the same flag as
"resume state from disk." Since the run-id is the machine ID from the
start (DESIGN §6), the launcher always has a valid run-id at resume time.
If classify never ran (no `state.json` exists), the orchestrator's
`--resume` branch needs a `task` positional, which is gone from the
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
`leerie --finalize`, not the machine).

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

**Why bundles instead of tar (the previous mechanism):** macOS BSD
`tar -c` normalizes filenames NFC → NFD when archiving (libarchive
behavior). The Linux receiver wrote NFD bytes to disk; git's index
from macOS still held NFC bytes (because the index was built on
APFS, which normalizes at the syscall layer). Result: filenames
with `ó`, `ñ`, emoji, etc. showed as untracked + missing on the
machine, and a single such file inside a submodule made the parent
flag ` M`, failing preflight. Verified empirically on the live api
repo's `📄Plan de implementación.pdf` (NFC `c3 b3` on host → NFD
`6f cc 81` on machine after BSD tar transport). Bundles sidestep
the issue entirely because the bundle file stores pack-format
binary objects; filenames are materialized natively on the receiving
Linux git from raw bytes in tree objects — no transport-layer
normalization decision ever happens.

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
- **`leerie --finalize`** (user-driven recovery / re-attempt). The
  launcher's `--finalize` handler also detects "already synced to
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

#### Smart resume (`leerie --resume`)

`leerie --resume [<run-id>] [--shell] [--auto-finalize]
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

1. `leerie --resume <id>` → look up
   `$LEERIE_STATE_HOST_DIR/runs/<id>/fly-machine.json` first, then
   `$LEERIE_STATE_HOST_DIR/runs/<id>/run.json` (which carries
   `fly_machine_id` per Phase 2). If neither yields a value, exit
   with the per-id "no Fly machine pointer found" error.
2. `leerie --resume` (no run-id) → scan
   `$LEERIE_STATE_HOST_DIR/remote/*.json` for active records (records
   whose filename is a launcher PID that still exists). Exactly one
   → resolve the run-id from the record and continue. Multiple →
   print the list and exit 1. None → fall through to the existing
   per-id "no Fly machine pointer found" error path.

`provision.sh` writes the PID-keyed record at
`$LEERIE_STATE_HOST_DIR/remote/$$.json` immediately after creating the
machine, and also writes the run-keyed pointer
`$LEERIE_STATE_HOST_DIR/runs/$LEERIE_REMOTE_RUN_ID/fly-machine.json`
in the same call — before returning to the launcher — so `--resume`
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
| `--auto-finalize` | On clean orchestrator exit (alive→dead during tail), automatically `exec leerie --finalize <run-id>` on the host. Plumbed via `tail_with_optional_autofinalize` and the `AUTO_FINALIZE_TOKEN` sentinel emitted by `render_tail_wrapper`. |

Both flags are launcher-only — the filter loop strips them from
`REWRITTEN_ARGS` before exec into the orchestrator (same convention as
`--no-re-seed`, `--no-runtime-install`).

Local-runtime `--resume` is unaffected by this smart router. Local
runs are synchronous foreground processes (`nerdctl run --rm` with no
backgrounding), so there is no detached container to attach to; local
`--resume` keeps its existing inline-re-exec behavior. The smart
router branches live inside the `RUNTIME=fly` guard.

Maps to `DESIGN.md`: §6 *Smart resume in remote mode*.

#### Mid-run re-rsync (`scripts/remote/re-seed.sh`)

Two user-visible surfaces share one mechanism:

1. **`leerie --re-seed <run-id> [--force]`** — explicit fast-path
   before runtime preflight. Wakes the machine if stopped, runs the
   safety check, runs `seed_repo_dirty`, exits. No orchestrator
   exec — for the case where the user wants to attach via Phase 3
   to inspect before resuming.
2. **Auto-re-seed on `leerie --resume <run-id> --runtime fly`** —
   inside the `RUNTIME=fly` branch, when `resume_machine` runs
   (i.e., the dual-file resolver — `fly-machine.json` first, then
   `run.json` — yielded a `fly_machine_id` for the run-id), the
   launcher calls `re_seed` between `seed_auth` and the orchestrator
   exec. `--no-re-seed` opts out (rate-limit case where nothing
   changed host-side). `--force` bypasses the safety check.

   The dispatch is strict on `--resume`: if no machine pointer is
   found in either sidecar, the launcher dies with a diagnostic
   pointing at `leerie --list` rather than silently provisioning a
   fresh machine (which would orphan the original on Fly). Likewise,
   if `resume_machine` returns non-zero (machine destroyed or
   unstart-able), the launcher exits with the failure instead of
   falling through to `provision_machine`. Without `--resume`,
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
   listing the first 10 paths and pointing at `leerie --resume
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
| `--no-re-seed` | — | off | Skip the auto-re-seed during `--resume`. |
| `--force` | `LEERIE_RE_SEED_FORCE=1` | off | Bypass the safety check that refuses re-seed against machine-side dirty tracked files. |

Both flags are consumed by the launcher and not forwarded to the
orchestrator (same convention as `--no-runtime-install`,
`--no-auto-publish`).

Maps to `DESIGN.md`: §6 *Mid-run re-seed (remote mode)*.

#### Explicit pause and destroy verbs (`leerie --stop`, `leerie --kill`)

The detached orchestrator (DESIGN §6 *Detached orchestrator (remote
mode)*) decouples the user's local terminal from the run's lifetime.
Ctrl-C no longer means "destroy" — it means "stop watching." So the
destructive and pause actions need explicit verbs.

Two new launcher flags, routed at the top of `leerie` alongside
`--resume` (line ~63):

- **`leerie --stop <run-id>`** — clean pause. Runtime detection:
  (1) `_auto_detect_fly_runtime` checks for `fly-machine.json` →
  Fly path; (2) `_is_local_container` probes `nerdctl inspect
  <run-id>` → local path; (3) neither → error.
  - **Fly path:** sources `provision.sh`, exports `LEERIE_MACHINE_ID`
    and `FLY_APP`, calls `stop_machine()`.
  - **Local path:** sources `lib.sh`, calls `nerdctl stop <run-id>`
    (SIGTERM first — the orchestrator's `InterruptedBySignal` handler
    saves state before exit — then SIGKILL after grace period; `--rm`
    on the original `nerdctl run` auto-removes the container).
  - Both paths call `update_run_json` to set `paused_at = <iso_now>`
    and `pause_reason = "user-requested"` on the sidecar. The run is
    resumable via `leerie --resume <id>`.
- **`leerie --kill <run-id> [--force]`** — destroy. Same runtime
  detection as `--stop`. Prompts the user to type the run-id to
  confirm (unless `--force` / `LEERIE_FORCE_KILL=1`).
  - **Fly path:** calls `destroy_machine()`, sets `killed_at` and
    `fly_machine_id` on the sidecar.
  - **Local path:** calls `nerdctl kill <run-id>` (immediate SIGKILL),
    sets `killed_at` on the sidecar.
  - The run is no longer resumable.

  Recovery path for the orphan case: `leerie --kill --machine-id <id>
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
  added to the derived-status set and is a valid `--list --status`
  filter value. The cleared-but-empty terminal state
  (`no_work_required`, `waves == []`) is exempt — `completed_waves (0)
  < len([]) (0)` is false, so it still reads `done`. This gates only the
  `--list` *display*, not the push.
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
  (`leerie`), the `leerie --finalize <id>` verb, and Fly's
  `decide_teardown` (`scripts/remote/provision.sh`) — so this one gate
  covers them all. Fail-open: absent/non-numeric wave fields skip the
  gate so a legitimately complete run is never blocked over a missing
  file. Without this gate, `_derive_run_status` and `phase_finalize`
  alone would still let a stray `--finalize` push a partial branch (the
  PR-#22 incident).

#### Accept-blocked verb (`leerie --accept-blocked`)

When a subtask returns `status: blocked` due to unsatisfied `extent:
external` prerequisites (DESIGN §5), `--resume` retries it — which
blocks again indefinitely. The `--accept-blocked` verb lets the
operator acknowledge the external block so `--resume` skips that
subtask.

- **`leerie --accept-blocked <run-id> <subtask-id> [--runtime fly|local]`**
  — sets `subtask_status[sid]` to `"complete"` in state.json and removes
  the sid from the `blocked` dict (if present). On `--resume`,
  `phase_execute`'s wave-skip filters subtasks whose `subtask_status` is
  `"complete"`, so the accepted subtask never re-dispatches.
  - **Input validation (both runtimes):** both positionals are checked
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
  - The mutation program validates that the subtask's current status is
    `"blocked"` or `"failed"` before mutating (atomic temp-file +
    `os.replace`). No-ops with a `NOOP:` sentinel if already `"complete"`.
  - **Test coverage:** `tests/test_accept_blocked.py` — local-path tests
    (mutation, no-op, error paths, blocked-dict cleanup), Fly-path tests
    with a stubbed `flyctl` that parses both `-C` positionals and routes
    the stdin-piped `python3 -` to a local fixture, and injection-rejection
    tests asserting a metacharacter-bearing run-id/sid is refused with a
    nonzero exit and no mutation.

Maps to `DESIGN.md`: §5 *`requires.extent` — in-graph vs. external
prerequisites*, *Accepting external-blocked subtasks*.

Maps to `DESIGN.md`: §6 *Detached orchestrator (remote mode)*, *The
user-visible verb surface*.

#### Unified `leerie --list` (cost column + `--status` + `--runtime` filters)

`list_runs()` in `orchestrator/leerie.py` is extended to surface remote
runs alongside local runs in a single table. Status and runtime are
**orthogonal axes**: status describes lifecycle (`paused`, `killed`,
`done`, `sync-failed`, `in-progress`, `done-pushed-pr`,
`done-pushed-no-pr`, `push-failed`, `pr-failed`, `corrupt-sidecar`,
`seed-failed`); runtime describes where the run executed (`local` or
`fly`). The `seed-failed` status covers run dirs that have a
`fly-machine.json` (launcher wrote it the moment Fly provision
succeeded) but no `state.json` (the orchestrator never wrote one,
typically because `seed_auth` aborted before `phase_classify`).
`discover_runs()` synthesizes a row dict with `_orphan=True` and
`started_at` from the fly sidecar; `_derive_run_status()` returns
`seed-failed` for them (earliest precedence, before the run.json
corrupt-sidecar check). `resolve_run_id()` accepts orphan ids
transitively (no special-casing needed once `discover_runs` returns
them), so `./leerie --resume <orphan-id> --runtime fly` works against a
seed-failed run. An **explicit** id is exempt from the resumable-status
filter below, so this keeps working; `seed-failed` is excluded from the
bare-`--resume` *auto-pick* only (it needs an operator decision first,
and its rows carry no `started_at` to rank by).

**EC2 counterpart (spec — `discover_runs()` widening not yet
implemented).** DESIGN §6 *EC2 runtime lifecycle* ("Run identifier")
flags that `discover_runs()`'s orphan scan is hardcoded to the literal
filename `fly-machine.json` and will need widening to also check for
`ec2-instance.json` (the sidecar `ec2-provision.sh`'s
`provision_instance()` now writes unconditionally — see the Files-table
row above; this part has shipped) so a crashed pre-`state.json` EC2 run
is discoverable the same way a crashed Fly run is today. DESIGN §6 is
explicit that this widening is out of scope for the provisioning
subtask itself and should not repurpose or rename `fly-machine.json`,
which stays exactly as-is for Fly runs — a future subtask adds the
additional `ec2-instance.json` check alongside it, not instead of it.

Changes:

- `_collect_run_rows()` returns a per-run tuple
  `(run_id, started_at, status, branch, is_fly, cost)`. `is_fly` is a
  bool derived from `fly_machine_id` in `run.json` or a present
  `fly-machine.json` — it is **filter-only** (consumed by the
  `--runtime` filter), never rendered as a column. `cost` is the run's
  aggregate `$X.XX` from `state.json`'s `telemetry.cost_usd` (present
  in the state summary `discover_runs` passes through — no extra disk
  read), or `—` when telemetry is absent (orphans / pre-classify runs).
- `_render_run_table()` renders columns in the order `run_id,
  started_at, status, cost, branch` (the filter-only `is_fly` is not a
  column). The `cost` column is right-aligned; widths auto-size.
- `--status <state>` argparse flag on `--list` filters rows to only
  those whose derived status matches. `<state>` accepts any value in
  `RUN_STATUSES` (see list above). Invalid values produce an
  argparse error listing the allowed set.
- `--list --runtime fly` is intercepted by the launcher (bash) before
  the orchestrator dispatch and queries Fly directly via `flyctl
  machines list --app <FLY_APP> --json`. Renders a `machine_id |
  state | region | created_at | run_id (local)` table covering every
  machine under the app, regardless of which host repo launched them.
  `run_id` is best-effort filled by scanning `<state-root>/runs/*/{fly-
  machine.json,run.json}` for the current repo; machines launched from
  another repo show `run_id=?`. Falls back to the orchestrator-side
  local-sidecar list when `flyctl` is missing or auth fails. Plain
  `--list` (no `--runtime fly`) is unchanged.

Verbs `--stop`, `--kill`, `--finalize` accept an optional
`--runtime <local|fly>` flag — validated by the launcher (bash)
against only `local`/`fly`, rejecting any other value (including
`ec2`) with an error; this is narrower than the `RUNTIME_VALUES`
enum (`local|fly|ec2` — see "Remote execution mode" below) that
gates the top-level `--runtime` flag for launching a new run.
`ec2` machine lifecycle (the `--stop`/`--kill --runtime ec2`
counterpart) has not shipped — `stop_instance()`/`terminate_instance()`
exist in `ec2-provision.sh` (see the Files table above) but the
launcher's `--stop`/`--kill` verbs do not yet dispatch to them, since
that requires the same launcher-side `RUNTIME=ec2` wiring the "Runtime
mode" section above notes is still pending; DESIGN §6 *EC2 runtime
lifecycle*'s teardown row (`decide_ec2_teardown()`, shipped — see the
Files table above) is the intended `--stop`/`--kill` counterpart once
that launcher-dispatch subtask lands. `--stop` and
`--kill` support both `local`/`fly`: Fly runs route to `flyctl
machine stop`/`flyctl machine destroy`; local runs route to
`nerdctl stop`/`nerdctl kill` via the `_is_local_container` probe
(`nerdctl inspect <run-id>`). `--stop`
uses `nerdctl stop` (SIGTERM first, allowing graceful state save);
`--kill` uses `nerdctl kill` (immediate SIGKILL). `--finalize
--runtime local` still errors — local finalization is inline. Without
the flag, the verbs infer the runtime from the sidecar
(`fly-machine.json` presence for Fly, `nerdctl inspect` for local).
`--resume` accepts `--runtime` directly (the smart router branches by
runtime: fly takes the smart-attach path, local takes the inline
re-exec path).

Maps to `DESIGN.md`: §6 *The user-visible verb surface*.

#### Detached run finalization (`leerie --finalize <run-id>`)

With the detached orchestrator, the launcher cannot synchronously wait
for orchestrator completion and call `fetch_branch` — the tail's exec
session ends before (or independent of) the orchestrator's actual exit.
Two surfaces address this together:

1. **`orchestrator.pid` on the machine.** The detached-launch sh wrapper
   records the orchestrator's pid in
   `/work/.leerie/runs/<run-id>/orchestrator.pid` after the post-`Popen`
   poll has cleared the flock-loser case (see the launcher
   `_launch_script` listing above and DESIGN §6 *Single owner per
   run dir*). `leerie --resume`'s in-machine tail watcher checks
   liveness via two ORed signals — pid-file `kill -0` and a
   `/proc/[0-9]*/cmdline` scan for `orchestrator/leerie.py` + run-id
   — alongside the `tail -F`. Both must agree the orchestrator is
   dead before the watcher prints
   `<ISO-8601> [leerie] remote: orchestrator exited — syncing run branch + state to host...`.
   The tail then exits. The `/proc` scan is what closes the
   stale-pid contagion described in DESIGN §6: even if the pid file
   went stale (concurrent-spawn race, future cause), the scan finds
   the real orchestrator and the watcher keeps tailing.
2. **`leerie --finalize <run-id>`** — new launcher fast-path that runs the
   post-orchestrator block the launcher used to run inline: source
   `fetch-branch.sh`, call `fetch_branch`, source the host-side
   finalize block (push + `gh pr create`). The verb is idempotent — if
   the run branch is already pushed (`pushed_at` set), it short-
   circuits with "already finalized."

**`leerie --finalize` resolves the run-id directly.** The launcher
resolves `<run-id>` against `$LEERIE_STATE_HOST_DIR/runs/<run-id>/`
locally to pick up `fly-machine.json` and the partial sidecar. Since
the run-id IS the machine ID (DESIGN §6), no fallback lookup is
needed. No-match falls through to an error augmented with a hint to
run `leerie --list`.

**`leerie --finalize <run-id>`** (non-force) first tries
`fetch_branch` (the normal clean-exit case: orchestrator wrote
`finished_at`). If that fails, the launcher auto-recovers: it calls
`force_finalize_remote` (which checks whether the orchestrator is dead
and patches `finished_at` — see liveness checks below), then
`collect_subtrees_remote` to integrate un-merged subtask branches on
the machine, then retries `fetch_branch`. If the orchestrator is still
alive, the launcher refuses with a hint to use `--force`.

**`leerie --finalize <run-id> --force`** extends the recovery to runs
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
       via `leerie --resume <run-id> --shell --runtime fly`.

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
orchestrator). The integrator runs in the staging worktree with the
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

`--finalize` logs the action it took before SSHing in:
`finalize: machine=<id> run=<id> action=<fetch|force-stop+collect+fetch|already-synced>`
so post-mortems of future failures are shorter.

This matches the convention that destructive and side-effecting actions
are explicit verbs (DESIGN §6 *The user-visible verb surface*) rather
than implicit consequences of stream timing.

Optional convenience: `leerie --resume <run-id> --auto-finalize`
runs `leerie --finalize` automatically when the pid-watch detects
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
        ├── subtasks/<id>.json       per-subtask spec handed to each implementer
        ├── criteria/<id>.md         informational success-criteria notes (DESIGN §9)
        ├── artifacts/<id>.json      structured deliverables returned by an
        │                            implementer's `artifacts` result field
        │                            (DESIGN §5 *Artifact passing between
        │                            subtasks*). Orchestrator-owned: written
        │                            by `settle_subtask` on a successful
        │                            `complete` result with non-empty
        │                            `artifacts`, read by `run_implementer`
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
        │                            per line, one line per ~30 s while orchestrate()
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

`run.json` fields (a minimal sidecar enabling `leerie --list` and resume
discovery without parsing the full `state.json`):

| Field | Shape | Notes |
|-------|-------|-------|
| `run_id` | str | the run identifier (matches the directory name and the branch suffix) |
| `branch` | str | the run branch — always `leerie/runs/<run_id>` |
| `working_branch` | str | the branch HEAD-at-run-start; used as the PR base (leerie does not merge into it locally) |
| `started_at` | ISO-8601 str | wall-clock start time (also mirrored in `state.json`) |
| `finished_at` | ISO-8601 str \| null | wall-clock end time. Set at finalize success on the normal path; also set by the `except SystemExit` handler in `main()` for `die()` exits that fire after the run directory exists (on Fly, the tail wrapper propagates the orchestrator's exit code via `orchestrator.exit_code` when present, falling back to 0 when absent; either way `fetch_branch`'s discovery script needs `finished_at` to find the run). Idempotent on `--resume` — `phase_finalize` overwrites it with the real completion time if the run succeeds on retry. |
| `task` | str | the task description (mirrored from `state.json`) |
| `pushed_at` | ISO-8601 str \| null | when the run branch was pushed to `origin`; null until push runs |
| `push_error` | str \| null | captured `git push` stderr if the push failed; mutually exclusive with `pushed_at` being set |
| `pr_url` | str \| null | the PR URL `gh` returned; null until PR creation succeeds |
| `pr_error` | str \| null | captured `gh` stderr if PR creation failed; logical invariant — `pr_error` can be set only after `pushed_at` is set |
| `fly_machine_id` | str \| null | Fly Machine ID for a remote (`--runtime fly`) run; written by `scripts/remote/provision.sh` immediately after `flyctl machine run` succeeds, so a launcher that crashes before classifying still leaves a recoverable pointer. Null for local runs. |
| `paused_at` | ISO-8601 str \| null | when the remote run was paused — either on failure (set by the launcher's EXIT trap on the pause branch) or by explicit user request (`leerie --stop <run-id>`). Null for successful runs, killed runs, and runs the user merely detached from. **Cleared at finalize**: `fetch_branch`'s `tar -xC` (scripts/remote/fetch-branch.sh:225) overwrites the host sidecar with the machine's `run.json`, which has no `paused_at` set because the machine isn't aware of the user's pause action. Intentional — the post-finalize status should be `done-pushed-pr`, not `paused`. Pause/resume forensics are not preserved across finalize. |
| `pause_reason` | str \| null | short tag identifying which path set `paused_at` (`worker-error`, `orchestrator-exception`, `finalize-failed`, `user-requested`). Null when `paused_at` is null. Cleared with `paused_at` at finalize (see above). |
| `killed_at` | ISO-8601 str \| null | when the remote run was explicitly destroyed by `leerie --kill <run-id>`. The Fly Machine has been destroyed and the run is no longer resumable. Null for any other terminal state. |
| `sync_failed_at` | ISO-8601 str \| null | when the clean-exit branch of `decide_teardown` ran `fetch_branch` and it failed. The orchestrator finished cleanly on the machine, but the run branch + state directory could not be pulled back to the host. The machine is LEFT RUNNING (not stopped) so the user can recover manually via `leerie --finalize --runtime fly` (retry sync + push), `leerie --resume --runtime fly` (inspect — tails the log by default, `--shell` opens a bash session), or `leerie --kill --runtime fly` (destroy only after work is safely on host). Orthogonal to `paused_at`/`pushed_at`/`killed_at` — the machine is neither paused nor destroyed. Mutex-checked against `pushed_at` (a successfully pushed run can't be sync-failed) and `killed_at` (a destroyed machine can't be sync-failed). Requires `fly_machine_id` to be set (the running machine needs a pointer). |
| `sync_fail_reason` | str \| null | short tag accompanying `sync_failed_at` (currently always `sync-failed-on-clean-exit`). Null when `sync_failed_at` is null. |
| `recovered_at` | ISO-8601 str \| null | when `leerie --finalize <run-id> --force` patched this run's `finished_at` after the orchestrator died before its natural finalize. Set by `scripts/remote/force-finalize.sh` together with `finished_at` and `no_push=false`. A non-null value means the run reached host-side finalize via the recovery path rather than the natural one. Orthogonal to all terminal-state fields. Written **once** on the first successful `--force` run; subsequent `--force` invocations short-circuit on the now-set `finished_at` and leave `recovered_at` unchanged (the recovery timestamp records the original recovery, not the most recent verb invocation). |
| `recovered_via` | str \| null | short tag accompanying `recovered_at`; currently always `"force-finalize"`. Null when `recovered_at` is null. |
| `volume_id` | str \| null | Fly volume ID (e.g. `vol_…`) when the machine was provisioned with a volume (the default on `--runtime fly` since `FLY_VM_DISK_GB` defaults to `8`). Mounted at `/work` on the machine (the path that holds the seeded repo, `.leerie/runs/<id>/` state, and per-subtask worktrees). Destroyed when the machine is destroyed (clean exit or `leerie --kill`). Null for local-runtime runs or legacy Fly runs created before the default was introduced. If non-null, `fly_machine_id` must also be non-null — a volume without a machine to attach it to is invalid (enforced by `_validate_run_json`). |
| `image_tag` | str \| null | Full Fly registry image tag (e.g. `registry.fly.io/leerie:0.6.7`) recorded at provision time. Used by `resume_machine()` to detect version drift: if the current `$FLY_IMAGE_TAG` differs from the stored value (or the stored value is absent), the machine's image is updated via `flyctl machine update --image --skip-start` before starting. Updated in place on successful image update. Null for local-runtime runs or legacy Fly runs provisioned before the field was introduced (legacy machines always get the update on resume since empty != current). |
| `pr_title` | str \| null | LLM-written PR title from the `pr_writer` worker (omits the `leerie: ` prefix — the launcher prepends it before `gh pr create`). Null when the worker errored, was skipped because the user opted out of pushing (`push_will_happen(no_push, host_no_push)` is False — local `--no-push` or Fly `host_no_push=true`), or had not yet run; `host_finalize` uses its deterministic fallback in that case. |
| `pr_body` | str \| null | LLM-written PR body (markdown) from the `pr_writer` worker. Null on the same conditions as `pr_title`. |
| `pr_template_used` | str \| null | repo-relative path of the PR template the worker filled out (e.g. `.github/pull_request_template.md`). Null when the worker produced its no-template default structure. |
| `chain_id` | str \| null | UUID of the chain this run is part of. Written twice: (1) early-write by the child process immediately after `provision_machine` succeeds (so chain-scoped verbs can discover the run while the orchestrator is still running); (2) re-written by the parent's post-wait tagging loop after `fetch_branch` overwrites run.json with the orchestrator's copy. Null for runs not spawned as part of a chain. Used by chain-scoped verbs (`--list --chains`, `--status`, `--kill`, `--attach`, `--resume`) to discover chain runs. |
| `wave_idx` | int \| null | Zero-based wave index within the chain (set alongside `chain_id`). Used by the chain wave-sequencer to group runs by wave for synth-merge between waves. Null when `chain_id` is null. |
| `health` | dict \| null | Advisory run-health signals (DESIGN §9). Written by two seams and merged, never mutually exclusive: (1) `capture_conformance_baseline` writes `base_suite` `{status: "green"\|"red", red_axes: list[str]}` at the start of `phase_execute` — the build/lint/test exit-code verdict on the unmodified base tree; (2) `_record_run_health` writes `slowest_worker_sid` (str \| null), `slowest_worker_min` (float — the largest summed per-worker `duration_ms`, in minutes), and `truncated_worker_count` (int — worker logs that ended a result with `terminal_reason="max_turns"`) at finalize, preserving any existing `base_suite`. Purely informational — never gates; `_validate_run_json` imposes no invariant on it. Null when neither seam ran (e.g. a no-work run, or `--skip-base-baseline` on a run that also never reached finalize). |

`_validate_run_json(data)` enforces these invariants on read:
- `pushed_at` and `push_error` are mutually exclusive (at most one is non-null).
- `pr_url` and `pr_error` are mutually exclusive.
- If `pr_url` is set, `pushed_at` must be set (cannot have a PR without a push).
- `paused_at`, `pushed_at`, and `killed_at` are mutually exclusive (a run cannot be in more than one terminal-or-paused state). If `paused_at` is set, `fly_machine_id` must also be set (you cannot pause a run without knowing where to resume it). If `killed_at` is set, `fly_machine_id` must also be set (you cannot have destroyed a machine you don't have a pointer to).
- `sync_failed_at` is mutex-checked against `pushed_at` (a successfully pushed run can't be sync-failed) and against `killed_at` (a destroyed machine can't be sync-failed). When `sync_failed_at` is set, `fly_machine_id` must also be set — the running machine needs a pointer for the user to recover via `--finalize`/`--kill`.
- If `volume_id` is set, `fly_machine_id` must also be set — a Fly volume without a machine to attach it to is a corrupt sidecar (provision.sh always writes the two together).
- `killed_at` runs are not resumable; `--resume` against a killed run errors with "run was killed at <ts>; start a new run instead."

A corrupt sidecar is flagged but does not block the rest of the system; `leerie --list` will render that run with `status=corrupt-sidecar` and the user can inspect or delete the file.

`leerie --list` derives a single status per run via `_derive_run_status(run_json, state_json)`. The taxonomy is checked in priority order — earlier rows fire first:

| Status | When it fires | Typical next step |
|--------|---------------|-------------------|
| `corrupt-sidecar` | `run.json` violates one of the four invariants above | inspect the file under `<state-root>/runs/<id>/run.json` |
| `push-failed` | `push_error` is set | re-run `git push -u origin leerie/<id>` after fixing the access issue |
| `pr-failed` | `pr_error` is set (and push succeeded) | re-run `gh pr create` manually using the command logged at finalize |
| `done-pushed-pr` | `pr_url` is set | the happy path: PR open, work merged locally |
| `done-pushed-no-pr` | `pushed_at` set but `pr_url` not | rare: push succeeded, PR wasn't attempted (e.g., gh removed between push and PR) |
| `sync-failed` | `sync_failed_at` set (and no `killed_at`) | the orchestrator finished but `fetch_branch` failed; the Fly machine is still running with un-synced work. Run `leerie --finalize <id>` to retry sync + push, or `leerie --resume <id>` to inspect manually (default tails the log; `--shell` opens a bash session); only `leerie --kill <id>` once work is safely on host. (DESIGN §6 *Remote pause-on-failure* — sync-before-destroy contract.) |
| `done` | `finished_at` set, no `pushed_at` | the user passed `--no-push`, or the orchestrator exited via `die()` after the run directory was created (e.g. unresolved subtasks). In the latter case, `--resume` re-enters `phase_execute` normally — `finished_at` is overwritten on success. |
| `paused` | `paused_at` is set | inspect/attach to the Fly Machine, then `leerie --resume <id> --runtime fly` (DESIGN §6 *Remote pause-on-failure*) |
| `killed` | `killed_at` is set | terminal state — the machine was destroyed by `leerie --kill`. Not resumable; start a new run instead. |
| `in-progress` | none of the above | the run is still active (or died very early); resume with `--resume <id>` |

`RUN_STATUSES` in `leerie.py` declares the ten values; a test coupling check asserts the tuple matches every value `_derive_run_status` can return.

`leerie --list --status <state>` filters the table to runs whose derived status matches. `<state>` accepts any value in `RUN_STATUSES`; invalid values produce an argparse error listing the allowed set. `--list` short-circuits before any git/CLI preflight.

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
| `plan_snapshot` | dict | `{subtasks, waves}` captured immediately after `schedule()` returns and **before** `check_budget_feasibility` / `validate_plan` — both of which `die()`. Without it a plan that fails either gate is lost entirely (`write_plan` never runs), discarding the planner/fit_judge/splitter spend that produced it. Diagnostic/audit only: no orchestrator code reads it back, and it is deliberately *not* `write_plan`, which would also emit per-subtask spec files and seed the execution scaffolding for a run that cannot start. |
| `waves` | list[list[str]] | scheduled subtask ids per wave (from `schedule`) |
| `completed_waves` | int | index of the next wave to run (resume cursor) |
| `subtask_status` | dict[str, str] | per-subtask terminal status |
| `blocked` | dict[str, str] | per-subtask blocker reason when a wave aborts |
| `worker_count` | int | running total of `claude -p` invocations against `max_total_workers` |
| `current_phase` | str | the orchestrator's active phase string (e.g. `"phase 2: planning"`, `"phase 4-5: implementing"`); written at each phase entry and read by `_memory_sampler` so each `memory.ndjson` sample can be correlated with the phase that produced it. Empty string before phase 1 fires |
| `telemetry` | dict | calls, cost_usd, input_tokens, output_tokens — printed at run end |
| `categories` | list[str] | classifier output, post-whitelist filtering |
| `classifier_questions` | list[dict] | intent questions the classifier surfaced |
| `answers` | dict[str, str] | user answers to classifier questions (and source-of-truth) |
| `needs_source_of_truth` | bool | whether classifier asked for source-of-truth disambiguation |
| `source_of_truth_pref` | str | resolved preference (`codebase` / `research` / `both`) |
| `clarify` | bool | whether asking the user is allowed for this run (resolved from `--clarify` / `LEERIE_CLARIFY` / `leerie.toml` / default `False`) |
| `dangerously_skip_permissions` | bool | whether every `claude -p` worker — including the judgment workers running in the real repo cwd — is invoked with `--dangerously-skip-permissions`. Resolved from `--dangerously-skip-permissions` / `LEERIE_DANGEROUSLY_SKIP_PERMISSIONS` / `leerie.toml` / default `False`. When `True`, waives the DESIGN §12 mechanical read-only enforcement on the classifier / planner / reconciler / plan_overlap_judge / provision workers; trust shifts onto their prompts. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state |
| `skip_overlap_judge` | bool | whether the phase 2¾ `plan_overlap_judge` worker is suppressed even on multi-planner runs (DESIGN §5 *Cross-domain surface overlap*). Resolved from `--skip-overlap-judge` / `LEERIE_SKIP_OVERLAP_JUDGE` / `leerie.toml` / default `False`. The cheap-skip on single-planner / <2-subtask runs is automatic and not gated by this field — this flag only affects runs where the worker would otherwise fire. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state |
| `skip_budget_check` | bool | whether `check_budget_feasibility()` (DESIGN §13 *Budget feasibility — fail fast at the cheapest moment*) is suppressed. Resolved from `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` / `leerie.toml` / default `False`. The runtime backstop in `State.bump_workers()` is independent of this field — it always fires when the counter actually exceeds `max_total_workers`; this flag only suppresses the *early* die() that catches mathematically-unwinnable runs at the plan/execute boundary. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state. On `--resume` the preflight is moot regardless — the resume path enters past `schedule()` so the check has nothing to gate |
| `skip_satisfied_check` | bool | whether `filter_satisfied_subtasks()` (DESIGN §8 *Already-satisfied subtask elimination*) is suppressed. Resolved from `--skip-satisfied-check` / `LEERIE_SKIP_SATISFIED_CHECK` / `leerie.toml` / default `False`. When set, no `satisfied_probe` worker spawns and every subtask proceeds to `schedule()`; the mechanical `check_branch_has_commits` backstop then still catches an already-satisfied subtask post-execution — on a no-commits `complete`, `settle_subtask` re-probes the criteria against the run-branch HEAD (`probe_criteria_satisfied_on_head`) and settles it `complete` if met (DESIGN §8 *The mid-run sibling case*), rather than failing it as a retryable no-op. Re-resolved fresh on every run; on `--resume` the phase-3 filter is past, so the flag only affects fresh runs. |
| `strict_conformer` | bool | whether the conformer phase is blocking instead of advisory (DESIGN §9 *Post-work conformance*, "Opt-in strict mode" paragraph). Resolved from `--strict-conformer` / `LEERIE_STRICT_CONFORMER` / `leerie.toml` / default `False`. When True, conformer residuals (failed build/lint/test axes or unresolved rule violations) cause the subtask to return `blocked` instead of `complete`; the final-tree pass also blocks the run if residuals remain. The user fixes the residuals and runs `--resume`. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state |
| `skip_base_baseline` | bool | whether the base-tree health baseline (DESIGN §9 *Base-tree health baseline*) is suppressed. Resolved from `--skip-base-baseline` / `LEERIE_SKIP_BASE_BASELINE` / `leerie.toml` / default `False`. When True, `capture_conformance_baseline` does not run at the start of `phase_execute`, so no `conformance._baseline` is recorded and the conformer receives no `BASELINE:` context (falling back to self-judging "pre-existing" failures). Skips the once-per-run install-into-staging + full-suite-run cost. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state |
| `skip_repo_map` | bool | whether the P6 repo-map structural context (DESIGN §5½ (P6) *Codebase structural map*) is suppressed. Resolved from `--skip-repo-map` / `LEERIE_SKIP_REPO_MAP` / `skip_repo_map` in `leerie.toml` / default `False`. When True, `build_repo_map()` is not called and the planner/splitter receive no ranked-subgraph injection, degrading gracefully to the prior grep/glob-only planning path. Use on repos where tree-sitter cannot parse the primary language, or to opt out of structural context. Re-resolved fresh on every run, including `--resume`, so the user can flip it without editing state |
| `cgroup_containment` | dict | recorded by the fail-closed gate (`enforce_and_record_cgroup_containment`, in `_run_phases` just before the first worker spawns) (DESIGN §6 *Memory containment*): `{enforced: bool, hierarchy: "v2"\|"v1"\|null}`. `enforced` is the result of the root-broker probe round-trip (create+enroll+destroy of a throwaway cgroup); `hierarchy` is the cgroup version the broker detected. When `enforced` is `False` the run only proceeds if `--dangerously-allow-uncapped` was set (else the gate `die()`s). Persisted so the containment state is visible in `state.json` — the crash that motivated the broker left no artifact of the silent containment failure |
| `verbosity` | str | resolved verbosity level (`quiet` / `normal` / `stream` / `debug`); re-resolved fresh on every run, including `--resume`, so the user can dial up or down without editing state |
| `inspect_dirs` | list[str] | extra absolute paths granted to inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) via `--add-dir`. Resolved from `--inspect-dir` / `LEERIE_INSPECT_DIRS` / `inspect_dirs` in `leerie.toml`; re-resolved fresh on every run, including `--resume`, so the user can add or remove paths without editing state. Empty list when nothing is configured |
| `integrator_warnings` | dict[str, str] | non-fatal commit warnings from `integrate_wave` (non-fatal signal log) |
| `scope_warnings` | dict[str, dict] | oversized-diff warnings from `check_diff_scope` (non-fatal signal log) |
| `conformance` | dict[str, dict] | per-subtask conformer output and `conformance_warnings` (non-fatal signal log). Keys are subtask ids *or* the literal `_final` sentinel; values are `{result, warnings}` where `result` is the last conformer payload (or null on crash) and `warnings` is the list of advisory strings produced across all conformance rounds. The `_final` entry holds the post-integration whole-tree conformer pass's output (DESIGN §6 *Worktree and integration model*, final-tree pass paragraph); the leading-underscore convention guarantees no collision with subtask ids, which always start with a `<verb>-` prefix per `_ID_PREFIXES`. The per-subtask entries are populated only on subtasks whose implementer reached `status: "complete"`; the `_final` entry is populated whenever `run_final_conformance` ran (skipped only when the staging worktree or `working_branch` is absent, or on `--resume` after the pass already recorded a result). See DESIGN §9 *Post-work conformance* |
| `provision` | dict | output of `phase_provision` (DESIGN §6½). Keys: `source` (`table` / `llm` / `skipped-docs-only`), `recipe` (list of validated install entries, persisted for worker prompt injection — NOT executed by the orchestrator), `sh_hook_ran` (bool, set by `run_setup_hook`), `mise_versions` (raw blob from `mise ls --current --json`), `override_file` (absolute path to a synthesized mise override when `phase_provision` had to bridge a polyglot Go repo; `None` otherwise — re-exported as `MISE_OVERRIDE_CONFIG_FILENAMES` on `--resume`). Read by `_format_provision_recipe_section()` so implementer/conformer prompts can inject the recipe as a `PROVISION_RECIPE:` advisory block. |
| `external_preconditions` | list[dict] | planner-declared `extent: external` `requires` entries collected during `phase_reconcile` (DESIGN §5 `requires.extent`). Each item is `{tag, reasons: [{sid, reason}, …], originating_subtasks: [sid, …]}`, deduped by tag. Read by `write_plan()` and persisted as the `preconditions` section of `plan.json`. Empty list when no planner declared any external requirement (the common case). |
| `dropped_subtasks` | dict[str, dict] | subtasks soft-dropped pre-schedule. Two producers, distinguished by shape: `filter_offtree_subtasks()` drops subtasks whose `files_likely_touched` resolved outside the run's repo root (value `{reasons: [str], files: [str]}`); `filter_satisfied_subtasks()` (DESIGN §8 *Already-satisfied subtask elimination*) drops subtasks the `satisfied_probe` judged already met on the base tree (value `{reason: "already_satisfied", evidence: str, checked: [str]}`); and the post-execution no-commits re-probe in `settle_subtask` records a subtask whose criteria are already met on the run-branch HEAD (value `{reason: "already_satisfied_mid_run", evidence: str, checked: [str]}` — same shape, judged against the run-branch HEAD instead of the base tree; DESIGN §8 *The mid-run sibling case*). The `mid_run` label names the moment the rescue fires (post-execution, this run), not the provenance: it covers both a sibling committing the deliverable this run and a subtask already satisfied on the base tree (DESIGN §8 *Scope*). Absent when no drop fired. Audit trail only — the run proceeds with the surviving subtasks; no orchestrator code reads back from this field. |
| `conditional_drops` | dict[str, dict] | planner-emitted consumer subtasks dropped by the reconciler's `conditional_drops` resolution op (DESIGN §5) — i.e. the planner authored the subtask as "no-op if X" and X turned out to be unresolvable. Each value is `{reason: str, from_unresolved_tag: str}` where `reason` quotes the consumer's conditional intent + names why the precondition is false (the reconciler emits this) and `from_unresolved_tag` records which unresolved tag's resolution motivated the drop (looked up from the unresolved set at apply time). Absent when no conditional_drop fired. Distinct audit field from `dropped_subtasks` (off-tree soft drops, phase 3) so the two causes stay separately auditable. |
| `speculative_collapse_drops` | list[str] | subtask sids mechanically pruned by dead-subtask elimination (DESIGN §5) — fully-speculative subtasks whose every `in_plan` requires was unresolvable because the provider domain returned 0 subtasks. Recorded before `_check_unresolvable` runs so the audit trail survives even when `die()` fires for remaining unresolvable entries. Absent when no dead-subtask elimination fired. Distinct from `conditional_drops` (LLM-judged, based on conditional prose in intent) and `dropped_subtasks` (off-tree soft drops, phase 3). |
| `plan_overlap_judge` | dict | full output of the phase 2¾ `plan_overlap_judge` worker (DESIGN §5 *Cross-domain surface overlap*) — `{collisions: [{a_sid, b_sid, artifact, resolution, reason, merge_feasibility?}, …]}`. Persisted before the apply step (so if a `die()` fires on `unresolvable` or the merge-feasibility backstop the audit record survives). Absent when `phase_overlap_judge` cheap-skipped (single-planner / <2-subtask runs / `--skip-overlap-judge`) or when the judge returned `{collisions: []}`. |
| `plan_overlap_applied` | list[dict] | post-apply mutation summary for the phase 2¾ judge. Each entry is either `{action: merge|drop_a|drop_b, artifact: str, surviving_sid: str, dropped_sid: str, reason: str}` recording a mutation against the plan, or `{action: skipped_redundant, artifact: str, collapsed_to: str, original_a_sid: str, original_b_sid: str, merge_feasibility: str, reason: str}` recording a redundant pair whose endpoints had already collapsed to the same survivor via an earlier resolution (the closing edge of a connected cluster — kept in the audit trail so resume-time inspection sees every collision the judge emitted). The anchor-survivor rule may make the `surviving_sid` differ from `_apply_overlap_merge`'s default lex-smaller pick when the merge participates in a cluster — see "Phase 2¾ checks" above. Useful for resume-time replay debugging — `state.data["plan_overlap_judge"]` records what the judge said, this records what the orchestrator did. Empty list when the judge returned no collisions; absent when the phase cheap-skipped. |
| `no_work_required` | bool | set to `True` by `_finish_no_work_run` when every planner returns `status: "ready"` with `subtasks: []` (DESIGN §8 *The cleared-but-empty terminal state*). When `True`, the orchestrator wrote `finished_at`, skipped phases 3–6, and exited 0 — the task was already satisfied on HEAD, no run branch was materialized, no PR will be opened. `leerie --list` renders the run as `done` (no push, no PR, distinct from `done-pushed-no-pr` and `done-pushed-pr`). Absent on every normal run. |
| `no_work_reasons` | dict[str, str] | per-domain `confidence.basis` quoted from each planner's empty-but-ready output, recorded alongside `no_work_required` for audit. Keys are domain names (e.g. `"bug-fixing"`, `"testing"`); values are the `basis` string the planner emitted explaining why no work was needed. Absent on every normal run. |
| `working_branch` | str | the user's branch at the moment `phase_classify` runs (`git rev-parse --abbrev-ref HEAD`). Captured once and mirrored to three locations: `run.json.working_branch`, `<state-root>/runs/<id>/working-branch` (written later by `setup-run.sh`), and `state.json` via this field. Read by `_compose_pr_via_llm` as the `git diff` base for the PR-writer payload and by `run_final_conformance` as the `DIFF_BASE` for the post-integration whole-tree pass. Empty string when the host `git` invocation failed (interactive fallback path); the readers tolerate this. |
| `leerie_version` | str | the leerie version string from `.claude-plugin/plugin.json` at the time the run started (or resumed). Persisted so the PR footer and Run metadata block can show the exact version that produced the run, which aids debugging when a run was produced by an older release. |
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
`validate_checkpoint()`: *Frozen success criteria*, *Current status*, *Files
touched*, *Decisions made*, *Evidence gate status*, *Next action*, *Open
unknowns*. `validate_checkpoint()` enforces three layers: (a) every section
header must be present; (b) every section must carry non-whitespace content; (c)
the five "must carry handoff context" sections reject single-token
placeholder content (`none`/`n/a`/`na`/`tbd`/`nothing`/`unknown`/`todo`/`pending`/`—`/`--`/`-`/`?`) — the two
"nothing-to-report-is-OK" sections (*Decisions made*, *Open unknowns*)
accept these. Trailing punctuation (`.`/`!`/`?`/`…`) is stripped before
the comparison and repeated `?` is collapsed, so `None.`, `TBD!`, and
`???` are caught alongside the bare tokens. When a `worktree_root` is passed, `validate_checkpoint()`
also runs a freshness check: every path listed under *Files touched* must
either still exist in the worktree or carry a `[deleted]` annotation,
catching stale checkpoints whose paths were removed by partial work after
the snapshot was written.

In the same vein, `claude_p()` logs a context-decay warning when a worker
returns at ≥80% of its `--max-turns` budget (`num_turns` from the CLI
envelope). This is a proxy, not a hard guard: the schema only validates
the *shape* of the worker's final output, not whether the reasoning
chain that produced it ran against a healthy context. A 9.x confidence
score from a near-cap worker should be read with appropriate scepticism.
The warning sits alongside the existing `terminal_reason` warning at the
`claude_p` return path.

Maps to `DESIGN.md`: §10 (handoff, coordination-artifact location), §9 (criteria
locking).

---

## 9. Structured-output schemas

`claude_p()` validates each worker's payload against a schema keyed by worker
type. Required fields, current shape:

- **classifier** — required: `categories` (array), `confidence`
  (worker-internal self-gate via `_confidence_schema(["classification"])`).
  Optional: `questions`
  (array of `{id, question, why_underivable?}` — only `id` and `question`
  are required on each question), `source_of_truth_question` (bool). The
  classifier only flags whether the source-of-truth question is relevant;
  the orchestrator's preference resolution (see §2) supplies the value
  (default `both`).
- **planner** — required: `domain`, `subtasks`, `status`, `confidence`.
  `status` is the enum `ready` / `blocked` (DESIGN §8 planner gate): when
  the planner's evidence gate could not clear within `confidence_rounds`,
  it emits `blocked` with an empty subtasks list and the gap analysis in
  `confidence.gap_to_close`. A `ready` plan may also legitimately carry
  an empty `subtasks` list (the cleared-but-empty terminal state — "I
  understand the task, I investigated this domain, the work is already
  satisfied on HEAD"); see DESIGN §8 and the phase 3 `detect_no_work`
  short-circuit above. `confidence` is the worker-internal self-gate
  object: required keys `task_understanding` (number 1–10),
  `decomposition_quality` (number 1–10), `basis` (string), `falsifiers_tested`
  (array of strings — what would-disprove probes were run and what they
  showed), `contradictions_reconciled` (array of strings — any contradictions
  with the worker's own prior statements, named with the kept version's
  evidence), `gap_to_close` (object with optional `task_understanding` and
  `decomposition_quality` strings — populated when either score is below
  9.0). Each subtask is `{id, title,
  success_criteria_seed (all required), intent, scope_note,
  files_likely_touched, depends_on, requires, provides, size,
  investigation_notes}`. **`requires` is an array of objects, not bare
  strings: `{tag (required string), extent (required enum: "in_plan" |
  "external"), reason (string, required and non-empty when extent ==
  "external")}`. `extent: in_plan` is satisfied by another subtask's
  `provides` (a graph edge); `extent: external` is a planner-declared
  out-of-graph prerequisite (other repo, ops runbook, manual step) that
  bypasses the reconciler and surfaces in `plan.json` as a `preconditions`
  entry — see DESIGN §5 `requires.extent`.** `provides` remains an array of
  bare strings. `size` is `small` or `medium` — `large` is
  rejected by `validate_plan`. The schema's required-ness of `confidence`
  and `status` is the structural part of DESIGN §8's discipline: a worker
  that skipped self-gating fails its own JSON schema before the orchestrator
  reads the payload.
- **implementer** — required: `subtask_id`, `status` (`complete` /
  `incomplete-handoff` / `blocked` / `failed` / `needs-clarification`),
  `confidence` (worker-internal self-gate via
  `_confidence_schema(["root_cause", "solution"])`, not consumed by the
  orchestrator: required keys are
  `root_cause` and `solution` (numbers 1–10), `basis` (string),
  `falsifiers_tested` (array of strings), `contradictions_reconciled`
  (array of strings), and `gap_to_close` (object with optional
  `root_cause` and `solution` strings — populated when either score is
  below 9.0); see DESIGN §8 for the disciplines these fields make
  mechanically required — the schema requires the object itself, so a
  worker that skipped self-gating fails validation before the
  orchestrator reads the payload).
  Optional: `branch`, `criteria_results` (array of
  `{criterion, met, evidence}`), `checkpoint_path`, `blocker`, `summary`,
  `clarification_question` (DESIGN §11 mid-execution exception channel:
  `{id, question, why_underivable}` — all three required when the
  object is present; emitted only with `status: "needs-clarification"`,
  required to carry `checkpoint_path` as well so the work-in-progress
  survives the question to the user; orchestrator surfaces the question
  through the same interactive/non-interactive paths used by the
  Phase-1 classifier), `artifacts` (DESIGN §5 *Artifact passing between
  subtasks*: array of `{name (required string), kind (required enum
  "markdown" | "json" | "text"), content (required string),
  summary (optional string)}` — structured deliverables for downstream
  subtasks whose predecessor graph names this subtask; absent or empty
  for code-implementation subtasks). The criteria file is informational
  per DESIGN §9; `criteria_results` is recorded for telemetry but does
  not gate the subtask. The retired `criteria_revision_proposal` field
  is no longer in the schema.
- **integrator** — required: `incoming_subtask`, `status` (`resolved` /
  `design-conflict` / `failed`), `confidence` (worker-internal self-gate
  via `_confidence_schema(["resolution"])`). Optional: `resolution_summary`,
  `diagnosis` (read as a fallback for `resolution_summary` when
  diagnosing a non-`resolved` outcome).
- **conformer** — required: `subtask_id`, `rules_files_read` (array of
  strings — paths the conformer was handed by `discover_rules_files`; empty
  list when none were found), `rule_violations_fixed` (array of
  `{rule, fix, evidence}` — `rule` is the verbatim line from a rules file
  that was being honored, `fix` describes the change made, `evidence` cites
  the file/lines touched), `rule_violations_residual` (array of
  `{rule, why_not_fixed}` — violations the conformer spotted but did not
  resolve, with the reason), `docs_updates` (array of `{path, reason}` —
  documentation files updated to reflect the diff), `tests_updates` (array
  of `{path, reason}` — tests added or amended to cover the diff), `build`,
  `lint`, `tests` (each an object `{ran (bool), passed (bool), command
  (string), summary (string)}` — `ran: false` when the tool is not
  applicable to the repo; `passed` is irrelevant when `ran: false`),
  `summary` (string — one-line description of what the conformance pass
  did), `confidence` (worker-internal self-gate, not consumed by the
  orchestrator: required keys `conformance` (number 1–10), `basis`
  (string), `falsifiers_tested` (array of strings), `contradictions_reconciled`
  (array of strings), `gap_to_close` (object — populated when conformance
  is below 9.0); see DESIGN §8 for the disciplines these fields make
  mechanically required). The schema enforces the structural part of
  DESIGN §9 *Post-work conformance*: a conformer that skipped its own
  honesty discipline (e.g. wrote `passed: true` without a `command`, or
  omitted the self-gate block) fails the schema before the orchestrator
  reads it. The cross-field invariants — residuals require a non-empty
  `rules_files_read`, every `rule_violations_fixed` item cites a non-empty
  `rule`, every `docs_updates` / `tests_updates` `path` exists in the
  worktree — are enforced by `validate_conformance_result()`.
- **judge** — required: `passed` (bool — aggregate verdict, true only when all
  three dimensions are true), `dimensions` (object with required boolean fields
  `schema_ok`, `factual_ok`, `hallucination_ok`), `rationale` (str — 1–3
  sentence explanation for the verdict), `suggested_fixes` (array of strings —
  empty when `passed: true`). One verdict object per `judge_capture()` call.
  Used by `phase_judge()` / `judge_capture()` — not by the orchestrator's main
  workflow workers. `prompts/judge.md` carries the rubric.
- **patch_generator** — required: `anchor` (str — the exact substring of the
  current system prompt that the patch should replace; the heal loop validates
  this against the actual prompt text before applying), `replacement` (str —
  the new text to substitute for `anchor`). Optional: `strategy` (str — a
  one-line description of what the patch changes and why), `pivot_reason`
  (str \| null — why this iteration pivots from the prior strategy, or null if
  this is the first iteration or no pivot). The `patch_generator` schema is used
  by the self-heal skill's patch-generation worker; like `judge`, it is
  post-run and not used by the orchestrator's main `claude_p()`.

Schemas are embedded as Python dicts in `leerie.py` and serialized inline.

Maps to `DESIGN.md`: §7, §14.

---

## 10. Telemetry — NDJSON envelope and call_type mapping

Maps to `DESIGN.md`: §14.

### NDJSON envelope schema

Every `claude_p()` invocation appends one JSON object (one line) to
`<state-root>/runs/<run-id>/calls.ndjson` immediately after the call returns.
The file is opened for append at run start and is never truncated — it is
always a valid NDJSON file through the last complete line even under a hard
kill. It is never read by the orchestrator at runtime; reading is a
post-run operation performed by the judge and heal skills.

| Field | Type | Notes |
|-------|------|-------|
| `call_id` | str (UUID v4) | unique identifier for this invocation; referenced by judge verdicts |
| `run_id` | str | the run identifier — matches the directory name under `<state-root>/runs/` |
| `call_type` | str | one of the schema keys `claude_p()` accepts: the eleven `WORKER_TYPES` (`classifier`, `planner`, `reconciler`, `plan_overlap_judge`, `satisfied_probe`, `provision`, `implementer`, `integrator`, `conformer`, `fit_judge`, `splitter`) plus the four post-run / finalize workers (`pr_writer`, `judge`, `patch_generator`, `dep_capture`) |
| `model` | str | the model alias passed to `--model` for this invocation (e.g. `opus`, `sonnet`) |
| `system_prompt` | str | the full system prompt injected via `--append-system-prompt` |
| `user_content` | str | the user-turn content passed to the worker |
| `response_content` | str | the worker's raw text response (before schema parsing) |
| `parsed_ok` | bool | whether `structured_output` was present and schema-valid |
| `input_tokens` | int | `usage.input_tokens` from the CLI envelope |
| `output_tokens` | int | `usage.output_tokens` from the CLI envelope |
| `latency_ms` | int | wall-clock milliseconds from subprocess start to return |
| `success` | bool | whether the call produced a schema-valid result (false on WorkerError or schema retry exhaustion) |
| `failure_kind` | str \| null | why a call failed, or `null` on success. Derived at the capture site by `_classify_failure_kind` from the returned envelope: `api_error` (with `:auth`/`:quota`/`:overload` suffix for `api_error_status` 401/429/529), `incomplete` (non-`completed` `terminal_reason`, e.g. `--max-turns`), or `schema_parse_failed` (returned but output failed schema validation — the dominant case). **Known gap:** rate-limit / out-of-credits / hard-crash failures raise `RateLimitedExit`/`WorkerError` past the capture block, so no record is written for them and `failure_kind` cannot cover them. |
| `cgroup_applied` | bool | whether per-worker cgroup memory/PID containment was active for this spawn (`_CGROUP_PROBE_RESULT`); a run with this consistently `false` means the writable `/sys/fs/cgroup` mount did not propagate |
| `ts` | str (ISO-8601) | UTC timestamp at the moment the line is written |

The judge skill consumes `system_prompt`, `user_content`, `response_content`,
and `parsed_ok` to evaluate quality. The heal loop uses `system_prompt` and
`user_content` to replay a call against a patched prompt. The `call_type`
field partitions calls for per-type analysis; judge and heal always operate
on one `call_type` at a time.

### Capture file path

```
<state-root>/runs/<run-id>/calls.ndjson
```

One file per run. Written by the orchestrator; the judge and heal skills
read it as a post-run harvest.

### Reporting — the `--report` verb

`leerie --report [RUN_ID]` is a read-only telemetry report for a single run;
like `--list` it exits without running orchestrate. Run selection reuses
`resolve_run_id` (exact-match a passed id, else auto-pick the most recent
run, else die with the available list). `--report` deliberately does **not**
pass `resumable_only=True` — unlike `--resume` it is read-only, and
reporting on a *finished* run is the normal case. It prints:

- a header (status, duration, and the `state.json` `telemetry` aggregate —
  calls, `$cost`, in/out tokens);
- a per-`call_type` breakdown from `calls.ndjson` — count, input/output
  tokens, average latency, failure count — sorted by call count descending,
  built by `_aggregate_calls`;
- a `failures by kind` rollup of `failure_kind` values, when any failed; and
- a memory-peak line (peak `rss_kb`, max `open_fds`/`thread_count`) from
  `memory.ndjson`, via `_memory_peak`.

All inputs already exist on disk; `--report` adds no new telemetry.

### call_type → prompt-resolution table

Each `call_type` maps to exactly one system-prompt source. The table below
is the complete, canonical mapping — no call_type is ever spawned without
a system prompt, and no system prompt is shared between call types.

| call_type        | Prompt source | Notes |
|------------------|---------------|-------|
| `classifier`     | `prompts/classifier.md` | read from disk by the orchestrator |
| `planner`        | `prompts/planner.md` | read from disk |
| `reconciler`     | `prompts/reconciler.md` | read from disk |
| `satisfied_probe`| `prompts/satisfied_probe.md` | per-subtask base-tree already-satisfied probe, phase 3 (DESIGN §8 *Already-satisfied subtask elimination*) |
| `provision`      | `prompts/provision.md` | LLM fallback when the lockfile table misses (DESIGN §6½) |
| `implementer`    | `prompts/implementer.md` | read from disk |
| `integrator`     | `prompts/integrator.md` | read from disk |
| `conformer`      | `prompts/conformer.md` | read from disk |
| `pr_writer`      | `prompts/pr_writer.md` | invoked by `phase_finalize` when `push_will_happen` is true (DESIGN §6 *Finalization*) |
| `judge`          | `prompts/judge.md` | post-run skill; not used by the main orchestrate loop |
| `patch_generator`| `prompts/patch_generator.md` | post-run heal-loop worker |

Every `call_type` resolves to a file under `prompts/`. The heal loop's
patch-generator worker calls
`resolve_prompt(call_type: str) -> tuple[str, str, str]` to load a
worker's system prompt: given any member of `WORKER_TYPES` (the
self-heal target set is the eleven main-loop workers, not the post-run
workers), it returns `(source_kind, content, location_hint)` where
`source_kind` is `"file"`, `content` is the prompt body, and
`location_hint` is the relative path `"prompts/<call_type>.md"`.
Raises `ValueError` for an unknown `call_type`. (Earlier iterations of leerie also exposed a `validator`
call type whose prompt lived as a `VALIDATOR_SYSTEM` constant inside
`leerie.py`; that worker was retired when the criteria file became
informational, and `resolve_prompt` no longer carries a
file-or-constant branch.)

### replay_capture — primitive for judge and heal-loop replays

```python
async def replay_capture(
    record: dict,
    *,
    override_system_prompt: str | None = None,
    cwd: str | None = None,
) -> tuple[dict, dict]:
```

Given one NDJSON record from `calls.ndjson`, reconstructs the `claude_p()`
invocation with the captured `system_prompt`, `user_content`, `call_type`
(used as `schema_key`), and `model`, and returns `(envelope, structured_output)`
from the new invocation.

`override_system_prompt` lets the heal loop replay with a patched prompt in
place of the originally captured one.

Replays use a throw-away in-memory `_ReplayState` and `_suppress_capture=True`
so they **never write to any `calls.ndjson`**. The capture stream is the ground
truth; replay results are ephemeral scoring artifacts.

Both judge (n=1 replay, then score) and heal (n=N replays, baseline vs patched)
build on this primitive.

---

## 11. Verification status of the code

Mirrors `DESIGN.md` §15, at the code level.

**Tested.** A pytest suite under `tests/` exercises the deterministic
enforcement functions:

| Test file | Function under test |
|-----------|----------------------|
| `test_resolve_leerie_root.py` | `resolve_leerie_root()` — `LEERIE_STATE_DIR` set → custom path; unset/empty/whitespace → `<repo_root>/.leerie`; always absolute |
| `test_resolve_source_of_truth.py` | `resolve_source_of_truth()` |
| `test_resolve_runtime.py` | `resolve_runtime()` — CLI > env > TOML > default `local` precedence, both valid values, invalid-value die() paths, empty/whitespace env handling |
| `test_resolve_models.py` | `resolve_models()` — per-worker precedence (CLI > env > TOML), defaults, validation, empty/whitespace handling |
| `test_resolve_dep_capture_model.py` | `resolve_models()` / `resolve_efforts()` for `dep_capture` — full per-worker and global override precedence chain; `MODEL_DEP_CAPTURE_ENV` constant; `dep_capture` absent from `MODEL_DEFAULT_PER_WORKER` (falls through to `MODEL_DEFAULT`); `dep_capture` in `EFFORT_DEFAULT_PER_WORKER` at `"high"`; isolation (override doesn't bleed to other workers); structural wiring guards |
| `test_rank_repo_map.py` | `rank_repo_map()` P6 ranking contract: seed-adjacent nodes rank above unrelated nodes (direct seed file, 1-hop neighbor via callee→caller edge, seed symbol biases definer, all connected-chain files before any island file); token-budget enforcement (explicit budget, `DEFAULT_CAPS["repo_map_tokens"]` when `None`, `None` == cap value, empty map returns `""`); binary-search shrink (lower budget → shorter output and fewer files, increasing budgets → non-decreasing lengths, tight budgets respected). Fixture built directly (no `build_repo_map`); no LLM calls; deterministic. |
| `test_resolve_fit_judge_model.py` | `resolve_models()` / `resolve_efforts()` for `fit_judge` and `splitter` — both in `WORKER_TYPES`; both absent from `MODEL_DEFAULT_PER_WORKER` (opus via `MODEL_DEFAULT`); both in `EFFORT_DEFAULT_PER_WORKER` at `"high"`; per-worker CLI/env/TOML override chains; isolation (override doesn't bleed to other workers); structural wiring guards |
| `test_resolve_fit_judge_splitter_model.py` | `resolve_models()` / `resolve_efforts()` for `fit_judge` and `splitter` — full per-worker and global override precedence chain (CLI > env > TOML > default); default model `opus` (via `MODEL_DEFAULT` fallback, absent from `MODEL_DEFAULT_PER_WORKER`); default effort `high` (via `EFFORT_DEFAULT_PER_WORKER`); both workers in `WORKER_TYPES`; isolation (per-worker override doesn't bleed to planner or implementer) |
| `test_fit_judge_schema.py` | `SCHEMAS["fit_judge"]` — required fields (`score`, `rationale`, `diffuse`, `confidence`); `score` has `minimum:0`/`maximum:1`; `confidence` uses `"fit"` axis; valid/invalid instances; JSON serializable; wiring (`fit_judge` in `WORKER_TYPES`, NOT in `MODEL_DEFAULT_PER_WORKER`, `EFFORT_DEFAULT_PER_WORKER["fit_judge"] == "high"`, prompt file exists) |
| `test_splitter_schema.py` | `SCHEMAS["splitter"]` — `children` required, `minItems:1`, child required fields (`id`, `title`, `success_criteria_seed`), optional child fields; valid/invalid instances; JSON serializable; wiring (`splitter` in `WORKER_TYPES`, NOT in `MODEL_DEFAULT_PER_WORKER`, `EFFORT_DEFAULT_PER_WORKER["splitter"] == "high"`, prompt file exists); no top-level `files` field (splitter never decides partition — `test_splitter_no_top_level_files_required`); child `requires` array uses `_REQUIRES_ITEM` shape with tag + extent enum (`test_splitter_child_requires_item_shape`) |
| `test_recursive_decompose.py` | `partition_files()` — empty, single chunk, exact multiple, partial last chunk, 100% coverage, 0 overlap, chunk_size=1, order preserved, chunk_size<1; `recursive_decompose()` — well-fit is leaf (score ≥ 0.70), oversized recurses (split then children judged), depth cap terminates, no-progress guard terminates + emits "no-progress guard" warning to stdout (asserted via capsys), migration path partitions files via `partition_files()` and invokes the splitter only in label-only mode to title each chunk (distinct titles; distinct deterministic fallback on splitter failure), both `claude_p` call sites pass the full required signature (`cwd`/`autonomous`/`caps` — C0 regression guard), a passed `repo_map` is injected into fit_judge/splitter prompts and omitted when `None` (G2), bump_workers called before every claude_p |
| `test_phase_plan_repo_map_ctx.py` | P6 Layer A wiring (`phase_plan` ctx injection, DESIGN §5½ (P6)): repo-map enabled path (ctx contains `repo_map` key, non-empty string, JSON-serializable, known symbol names present, seed_files seeded from `task_file_items`); skip_repo_map=True path (ctx omits `repo_map`, baseline keys `task`/`source_of_truth`/`clarification_answers`/`confidence_rounds` present, values match inputs); empty-repo degrade (`rank_repo_map` returns `""` → key omitted); exception-swallow degrade (`build_repo_map` raises → caught silently, ctx emitted without `repo_map`) |
| `test_phase_plan_recursion_wiring.py` | P1 Layer C wiring (`phase_plan` recursion expansion, DESIGN §5½ *Wire-in to phase_plan*): source-coupling guard (`phase_plan` source contains `recursive_decompose(` at depth=0, reassigns `plan["subtasks"] = leaves`, expansion loop precedes final logging); integration — one oversized subtask (stubbed `recursive_decompose` → two leaves) → `plan["subtasks"]` has 2 entries; two first-pass subtasks → `recursive_decompose` called once per subtask; well-fit leaf pass-through (stub returns input unchanged → single-element `plan["subtasks"]`); empty-subtasks plan not touched (`recursive_decompose` never called, subtasks stays `[]`) |
| `test__read_toml_key.py` | `_read_toml_key()` — the shared `leerie.toml` line parser used by both resolvers |
| `test_gather_answers_validation.py` | the source-of-truth validation gate in `gather_answers()` |
| `test_retryable_failure.py` | `_retryable_failure()`, **including a coupling test** that every producer's retryable-path return tags a `failure_kind` in `_RETRYABLE_FAILURE_KINDS` (`validate_result`, `check_branch_has_commits`, the inline dirty-worktree check in `settle_subtask`) |
| `test_state_fields.py` | `STATE_FIELDS` tuple parity, in both directions: against the §8 field table, and against every `st.data[...] = …` / `setdefault(...)` write in `leerie.py`. This is the mechanism §8's "this table is canonical" claim relies on |
| `test_blocked_clear_on_complete.py` | Fix 2 state semantics: `settle_subtask` pops `blocked[sid]` when terminal status is `"complete"` and leaves it untouched on `"failed"` / `"blocked"`; safe no-op when no `blocked` key present; only the completing sid is removed from a multi-sid dict; coupling test that `settle_subtask` source contains the exact `st.data.get("blocked", {}).pop(sid, None)` expression |
| `test_validate_plan.py` | `validate_plan()` (every rule in §5) |
| `test_validate_result.py` | `validate_result()` (every status-branch invariant) |
| `test_check_merge_committed.py` | `check_merge_committed()` (real-git fixtures) |
| `test_inspect_tools.py` | `INSPECT_TOOLS` composition and the inspect-callsite wirings (classifier, planner, reconciler, plan_overlap_judge, provision) — pins that the inspect bucket grants `Bash(<verb>:*)` patterns but never `Write`/`Edit` or bare `Bash`, the same DESIGN §12 enforcement applied to workers that don't get `--dangerously-skip-permissions` |
| `test_resolve_inspect_dirs.py` | `resolve_inspect_dirs()` precedence (CLI → env → TOML → `[]`), `~` expansion, dedup, and `STATE_FIELDS` membership |
| `test_resolve_prompt.py` | `resolve_prompt()` — every `WORKER_TYPES` member returns a `("file", content, "prompts/<call_type>.md")` triple; parity/coupling test; unknown call_type raises |
| `test_orchestrate_call_sites.py` | Source-text coupling guards for load-bearing call sites in `_run_phases` and `settle_subtask`: `absorb_supplied_answers` on the resume path (P5-1 regression guard), `phase_reconcile` between `phase_plan` and `schedule` (order-sensitive), `plans = await phase_reconcile(plans, ...)` rebind (guards silent discard), `status == "needs-clarification"` branch calling `surface_clarification`, that branch consuming `caps["subtask_continuations"]` (unified cap, not a separate clarification counter), and `resolve_blt(repo_root)` call sites in both `_run_conformance_phase` and `run_final_conformance` (guards against regression to direct `_infer_build_lint_test()` calls that would silently ignore `.leerie/config.toml` overrides) |
| `test_discover_rules_files.py`, `test_validate_conformance_result.py`, `test_run_conformance_phase.py`, `test_run_final_conformance.py`, `test_infer_build_lint_test.py` | the post-work conformance phase (DESIGN §9) and the post-integration whole-tree conformance pass (DESIGN §6 *Worktree and integration model*, final-tree pass paragraph): rule-file discovery against the fixed capped allowlist, schema cross-field invariants including path-traversal rejection, the orchestrator-level loop covering clean / malformed / crashed / rolled-back / cap-exhausted paths, the commit-prefix observability check, the dirty-state warning before rollback, the worker-budget-exhausted advisory path, the outer `settle_subtask` contract (never escalates to `failed`/`blocked` even on `FileNotFoundError`, unless `--strict-conformer` is on), and `_infer_build_lint_test` across the supported package-manager families (Node/JS, Python, Rust, Go, Java/Maven, Gradle, C#/.NET, PHP, Ruby/Rails including rubocop detection, `bin/rails test` inference, and the `_is_rails_repo` two-file guard against Sinatra/Grape false positives). `test_run_final_conformance.py` additionally covers the staging-worktree skip path, the working_branch-absent skip path, the resume-idempotence short-circuit, the `_final_conformance_payload` PR-writer surfacing helper, and a coupling test that the call site lives between `phase_execute` and `phase_finalize` in `_run_phases` |
| `test_resolve_blt.py` | `_load_blt_config()` and `resolve_blt()`: no config → full inference fallthrough; all-3-keys → declared values used; partial config → declared key wins others inferred; empty-string value treated as "not applicable" not a fallthrough; config overrides inference even when inference would return a value; `_load_blt_config` returns None when file absent and dict of only-present keys otherwise, including `setup_packages` |
| `test_resolve_repo_image_tag.py` | `resolve_repo_image_tag()` and `_leerie_repo_id()` — embedded-harness bash tests: no Dockerfile + no setup_packages → empty string; Dockerfile present → tag `leerie-repo/<repo-id>:<version>`; repo-id from HTTPS/SSH remote or basename fallback; uppercase sanitized; rebuild matrix (image absent, hash mismatch, base version changed, all-match no rebuild); coupling test that sentinel lines co-occur in harness and live launcher |
| `test_launcher_cache_mounts.py` | Coupling test for the Ruby bundle cache in `CACHE_MOUNTS`: asserts `-v ...:/home/leerie/.cache/leerie/bundle` volume mount and `-e BUNDLE_PATH=.../bundle` env entry appear inside the `CACHE_MOUNTS=(...)` array; companion assertion confirms `$HOME/.cache/leerie/bundle` is `mkdir -p`'d before the array. Reads launcher source text directly — no subprocess — so a future refactor that drops the bundle lines causes immediate test failure (guards the regression where every `bundle install` recompiles gems from scratch). |
| `test_launcher_per_repo_image.py` | Per-repo derived image bash-harness tests: `resolve_repo_image_tag()` with/without Dockerfile and with setup_packages only; `_leerie_repo_id()` from HTTPS/SSH remote and basename fallback; `build_repo_image` success/failure/error-message sentinel; rebuild-skip when image present and hash matches; rebuild fires on hash mismatch or image absence |
| `test_dockerfile_autogen.py` | Auto-generation of `.leerie/Dockerfile` from `setup_packages`: generated content contains ARG BASE_IMAGE, FROM \$BASE_IMAGE, USER root, apt-get install, declared packages in order; **the generated Dockerfile does NOT end with a trailing `USER leerie` — the last `USER` directive is `USER root` so PID-1 stays root (DESIGN §6)**; log message emitted during auto-gen; existing Dockerfile preserved verbatim and suppresses auto-gen; no setup_packages + no Dockerfile → REPO_IMAGE_TAG empty, no build fires; coupling tests that sentinel strings exist verbatim in launcher source and that the generator does NOT printf a trailing `.../lists/*\nUSER leerie` |
| `test_dockerfile_bake_from_capture.py` | Bake-from-persisted-installs path (distinct from `test_dockerfile_autogen.py`): launcher emits apt layer from `setup_packages` plus a `COPY`+`RUN` layer per `language_installs` entry with embedded `copy-input-shas` comment; `p.exists()` guard silently drops hallucinated `copy_input` paths from COPY while always emitting the RUN; all copy_inputs absent → RUN emitted, no COPY; multi-manager (pnpm+pip) → two COPY+RUN layers each with their own sha comment; pip install with only `requirements.txt` (no lockfile) → baked via persisted path, not lockfile fallback; identical regen → bit-identical Dockerfile (sha stable, no needless rebuild); changing `copy_input` file content → sha comment changes; committed `.leerie/Dockerfile` left untouched even when `language_installs` present; **node-ancillary augmentation on the persisted path — a pnpm entry whose `copy_inputs` omit `patches/` still emits `COPY patches/ ./patches/` (its own path-preserving line, never flattened into `./`) with the patch sha folded into the rebuild comment; a workspace child `packages/a/package.json` is COPYed to `./packages/a/package.json` (no basename clobber); a non-node (poetry) install ignores a `patches/` dir; the apt layer has no trailing `USER leerie`**; coupling tests that `persisted_installs`, `.exists()`, `copy-input-shas`, and `language_installs` sentinel strings exist in extracted launcher block |
| `test_base_dockerfile_chromium.py` | Base image `./Dockerfile` (not the per-repo autogen one): asserts `chromium` and `chromium-driver` are installed and the three container flags (`--no-sandbox`, `--disable-setuid-sandbox`, `--disable-dev-shm-usage`) are baked into `/etc/chromium.d/leerie-container-flags` after the chromium install (ordering guard) — see *Browser-based testing* above |
| `test_config_verb.py` | `leerie config` verb (Phase 3 bash-harness tests): `--init` creates `.leerie/config.toml` with auto-detected BLT values (uncommented) and a commented `setup_packages` example, prints the path, suggests `git add .leerie/`, and does NOT invoke `nerdctl run`; bare mode prints each axis with provenance (`[config]` vs `[inference]`) and shows `leerie.toml` keys when present; `--chat` invokes `claude --system-prompt-file prompts/config_chat.md --add-dir <USER_REPO>` (NOT `claude -p`) without `nerdctl run`; `--recapture` exits 1 with diagnostic when no runs directory or no finished run found, and dispatches to the python3 seam (`run_recapture_deps`) when a finished run exists; `--recapture --force` triggers wholesale replace; content assertions on `prompts/config_chat.md` (exists, mentions `build`/`lint`/`test`/`setup_packages` keys, `ARG BASE_IMAGE`, `config.toml`, `Dockerfile`; instructs ending at `USER root` and does NOT instruct a trailing `USER leerie`) and a parallel guard that `docs/USAGE.md`'s hand-authored `.leerie/Dockerfile` example likewise has no trailing `USER leerie`; coupling tests that verify the `config)` arm exists in the live launcher and exits before `nerdctl run`. Also covers the Dockerfile language-layer bake-from-persisted-installs logic (extracted Python heredoc invoked directly): persisted `language_installs` → COPY+RUN emitted per manager; hallucinated copy_inputs silently dropped while RUN always emitted; all copy_inputs missing → RUN without COPY; multi-manager → multiple COPY+RUN layers; no persisted installs → lockfile-detection fallback; empty `language_installs` list → lockfile fallback; no lockfile and no persisted installs → empty output; identical inputs → identical output (hash stability). |
| `test_config_recapture.py` | `leerie config --recapture` LLM-seam dispatch and multi-run consolidation: launcher `--recapture` arm dispatches to the python3 seam; exits 1 with diagnostic when no runs directory or no finished run found; `--force` flag passed to the seam; python3 failure exits 1; no `nerdctl` invoked; `--recapture` arm within extraction boundary; `run_recapture_deps` consolidates across ≥2 finished runs (all captured, not just newest); `--force` drops sentinel on each run (wholesale replace semantics); without `--force` already-captured runs are skipped (never-clobber union); Dockerfile survivor tests (committed and generated) |
| `test_replay_capture.py` | `replay_capture()` — args reconstructed from capture record, `override_system_prompt` plumbed through, no `calls.ndjson` written during replay, return-value shape `(envelope, structured_output)` |
| `test_phase_judge.py` | `phase_judge()` / `judge_capture()` — 3 verdicts written for 3-record NDJSON, INDEX.json content, schema validation, max_parallel semaphore bound, call_type filtering, empty/missing NDJSON edge cases |
| `test_heal_loop.py` | `HealState` save/load round-trip + atomic write; `heal_baseline()` — state.json + 6 verdict files for 2 samples n=3; `heal_apply_patch()` — patched prompts written per sample under iter-1/; `heal_replay_patched()` — history + best_so_far updated in state.json |
| `test_group_launcher.py` | `--group` fan-out arm and group-scoped ID-dispatched verbs: state-dir guard rejects `--state-dir` arg and `LEERIE_STATE_DIR` env before fan-out; each member child receives `--group-id <uuid>` and `--inspect-dir` for all sibling repos; brief content prepended to each member prompt; distinct per-member state dirs even with inherited env; non-git repo path rejected; `--status <group-id>` finds members across separate state dirs and excludes non-members; `--resume/--kill/--finalize <group-id>` dispatch across member state dirs; `--list --groups` groups `run.json` files by `group_id` across all `~/.leerie/*/` dirs; chain regressions: `--kill/--resume <chain-id>` still route to chain scope. Modeled on `tests/test_chain_launcher_id_dispatch.py`. |
| `test_group_launcher_verbs.py` | Group-scoped verb dispatch across two separate state dirs using a combined fixture (member A: paused/unpushed fly member; member B: pushed/done local member): `--status <group-id>` lists both run-ids and excludes non-group runs; `--list --groups` shows the shared group_id and member count; `--resume` dispatches only to the paused member; `--finalize` only to the unpushed member; `--kill` only to fly members without `killed_at`; `--stop` only to running fly members (no terminal state); dual-purpose-verb fallback: a group_id correctly routes via the group path when chain lookup returns empty; chain_id still routes via the chain path. Fills the `--stop` dispatch gap not covered by `test_group_launcher.py`. |
| `test_group_launcher_fanout.py` | `--group` fan-out core contract: one child per member is spawned with `cwd=<member-repo>`, `--group-id <uuid>` in argv, `--inspect-dir <sibling>` for every other member (not itself), and the shared brief text prepended to the member's prompt. Uses LEERIE_SELF_CMD stub-recorder to assert both cwd and argv per child. Complements `test_group_launcher.py` with focused assertions on the fan-out mechanics. |
| `test_group_run_json.py` | `group_id` in run.json Python-layer contract (DESIGN §20): `_validate_run_json` accepts `group_id`-bearing sidecars in every push/pause/kill state; `_write_run_json` persists and preserves `group_id` across incremental writes; `_derive_run_status` produces correct status for `group_id`-tagged runs (local-stub member without `fly_machine_id` and fly-stub member with `fly_machine_id`). |
| `test_group_state_dir_guard.py` | State-dir isolation for group members (DESIGN §20 *State isolation is free*): two members in repos with distinct basenames resolve to distinct `~/.leerie/<basename>/` dirs even when the parent's `LEERIE_STATE_HOST_DIR` is inherited; `--group` arm rejects `LEERIE_STATE_DIR` env and `--state-dir` CLI arg before any child spawns, preventing the `.owner`-collision failure mode. |
| `test_host_finalize_sh.py` | `scripts/host-finalize.sh` `host_finalize` contract via a bash-harness with stubbed `git`/`gh`: no-push / already-pushed idempotency, tip-aware re-push vs diverged-origin guard, completion gate (PR-#22), and the LLM-less **⚠ Deploy-ordering** fallback — the bash `jq` renderer emits the section from `state.json.external_preconditions` byte-identically to the Python `compose_pr_body` (DESIGN §20), and nothing when the field is absent/empty. |
| `test_repo_map.py` | `build_repo_map` (symbol/def extraction, class methods, ref edges, relative-path keys, empty-repo, skip-.git/node_modules), mtime cache (dir created, unchanged served from sentinel, changed re-parsed, only-changed re-parsed), `rank_repo_map` (string result, token-budget fits, seed-file/seed-symbol bias, empty map, determinism, tight budget), `_parse_repo_file` (unsupported extension, markdown, Python defs + refs), `_walk_calls` (bare call extracted, attribute call not extracted), `_pagerank` (dangling node, personalization, empty). |
| `test_build_repo_map.py` | HAS_TREESITTER-gated supplement to `test_repo_map.py`: symbol graph (defs, class defs, ref edge, keys shape, relative-path invariant), mtime cache (cache dir created, sentinel cache hit, changed file re-parsed, only-changed file re-parsed with sentinel for unchanged), graceful degrade (empty file, binary file, empty repo, skip-.git/node_modules). Uses a `pytestmark` module-level skip gate so CI without tree-sitter-language-pack skips all tests cleanly. |
| `test_tree_sitter_probe.py` | `_tree_sitter_extraction_works()` two-branch contract — the functional probe the `HAS_TREESITTER` skip gates and `_warn_repo_map_empty_once()` degrade warning both delegate to: True branch (real, unstubbed `_parse_repo_file` on a working tree-sitter host) gated on `HAS_TREESITTER` so it skips rather than fails on an incompatible host; the two False branches — `_parse_repo_file` raising (simulates an installed-but-incompatible language-pack version lacking `process()`) and `_parse_repo_file` returning `([], [])` (extracts nothing) — are host-independent and always run, since they are the load-bearing proof that the probe fails closed regardless of the local tree-sitter install state. |
| `test_repo_map_gate_wiring.py` | Source-coupling pins for the `HAS_TREESITTER` gate wiring itself (distinct from `test_tree_sitter_probe.py`'s probe-contract coverage): `conftest._has_treesitter()`'s source (`inspect.getsource`) references `_tree_sitter_extraction_works` — proving delegation to the functional probe rather than a bare `ImportError` check; `conftest` exposes a module-level `HAS_TREESITTER` bool; each of `test_build_repo_map.py`, `test_repo_map.py`, `test_phase_plan_repo_map_ctx.py` both imports `HAS_TREESITTER` from `tests.conftest` and contains a `skipif` referencing it (module- or class-level — `test_phase_plan_repo_map_ctx.py` gates only its `TestRepoMapEnabled` class). Guards against a silent regression (reverting to an ImportError-only gate, or dropping the skipif from one file) re-introducing the 19-test host-sensitive failure with no other signal. |

Run with `pytest tests/` from the repo root. The full suite (~1700
tests across the deterministic-enforcement, bash-harness, and remote
lifecycle tiers) completes in roughly a minute end to end. The table
above lists a representative subset; the live count under `tests/`
is the authoritative inventory.

**CI surface.** GitHub Actions runs four independent workflows: three on
every pull request to `main` (and on pushes to `main`), plus `release.yml`
on pushes to `main` only:

| Workflow | What it does |
|----------|--------------|
| `.github/workflows/test.yml` | `pytest tests/ -ra` across Python 3.10 / 3.11 / 3.12, with `pytest-cov` reporting line coverage to the job summary (no gate per CLAUDE.md). Coverage XML is uploaded as a 7-day artifact from the 3.12 job. Dev dependencies (`pytest`, `pytest-cov`) installed inline per CLAUDE.md's "pytest is the only dev dependency" stance. |
| `.github/workflows/syntax.yml` | The AST parse from CLAUDE.md's task-completion checklist, plus the same parse over every file under `tests/`. Path-filtered to `orchestrator/**/*.py` and `tests/**/*.py` for fast feedback ahead of the full pytest matrix. |
| `.github/workflows/shellcheck.yml` | `shellcheck -x scripts/*.sh` — the worktree mechanics scripts are load-bearing (DESIGN §6). Path-filtered to `scripts/**/*.sh`. |
| `.github/workflows/release.yml` | On a `chore(release): X.Y.Z` commit subject landing on `main`, creates the `vX.Y.Z` tag and a matching GitHub Release, or fails loudly (`::error::` + exit 1) if either is missing at job end. Both idempotency checks (tag-exists, release-exists) gate independently so a pre-existing tag or release never silently skips the other. |

Most workflows have a `concurrency:` block keyed on `github.ref` with
`cancel-in-progress: true`, so a force-push or rapid pushes do not leave
superseded jobs in flight; `release.yml` is the exception —
`cancel-in-progress: false`, since cancelling a release mid-flight (after
the tag push but before the release is created) is worse than letting it
finish. Dependabot (`.github/dependabot.yml`) tracks the GitHub-Actions
ecosystem on a weekly cadence.

**Not tested.** No worker has run against a live `claude -p`. The flag
contract in §3 is from CLI documentation, not from observed runs. The
worker invocation function (`claude_p`) is not unit-tested because
meaningful testing requires a stub or live `claude` binary — that's a
separate end-to-end tier.

First real step: one run on a throwaway repo with a small, fully-specified
task.
