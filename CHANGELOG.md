# Changelog

All notable changes to Leerie will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.9.52]

### Fixed

- **Local per-repo derived image build no longer fails with "pull access
  denied `leerie:<version>`".** Under Colima, buildkitd's containerd worker is
  bound to the `buildkit` containerd namespace while the base image is built and
  stored in `default` (where `nerdctl build`/`inspect`/`run` operate). The two
  namespaces are isolated, so the derived build's `FROM $BASE_IMAGE` could not
  see the local base and fell back to the registry, where the never-pushed tag
  401s. The launcher now runs `ensure_base_in_buildkit_ns` — an idempotent,
  best-effort `nerdctl save "$IMAGE_TAG" | nerdctl --namespace buildkit load` —
  before the derived and language-dep-probe builds, so `FROM` resolves against
  the local image store. The builds themselves stay in `default`, where
  `nerdctl run` reads the result. Reproduced byte-identically and fixed
  end-to-end; pinned by two new tests in `test_build_repo_image.py` (copy fires
  and precedes the build; idempotent skip when the base is already present).

- **`dep_capture` no longer writes prose as apt packages.** The worker was fed
  the *complete* set of shell commands a run logged (~96% noise: `git`, `grep`,
  `pytest`, `python3 -c` one-liners) and could degenerate into echoing prompt
  text as `setup_packages`. Redesigned to a **manifests-first** corpus (DESIGN
  §6½): `_gather_dep_manifests` reads the repo's dependency-manifest files
  (`requirements.txt`, `package.json` + lockfile, `pyproject.toml`, `go.mod`,
  `Cargo.toml`, `Gemfile`, `composer.json`, …) as primary ground truth, and
  `_extract_depcap_commands` now keeps only install-shaped commands
  (`_is_install_command`, evaluated per shell segment so chained/multi-line
  installs survive while `grep "apt-get install …"`-style leaks are dropped) as a
  secondary hint for system/native (apt) deps. Verified live via `claude -p`
  opus on Python-only and Node + native-dep repos.

- **`language_installs` in `.leerie/config.toml` is now valid TOML.** The
  JSON-encoded value (`[{"manager":"pip",…}]`) was written inside a basic
  (`"…"`) string, whose inner quotes broke the TOML. `_toml_value` now renders
  quote-containing values as TOML *literal* (single-quoted) strings, and
  `_dump_language_installs` escapes any single quote in a shell-quoted install
  command (`pip install 'requests[security]'`) so the value round-trips
  losslessly through strict `tomllib`, `_read_toml_key`, and the launcher reader.

- **The install-subtree state-dir validator now protects the `.leerie/`
  directory.** `.leerie/config.toml` (and `Dockerfile`) are now committed to
  version control (run artifacts under `.leerie/` stay git-ignored), which makes
  `.leerie` a tracked top-level directory. `_validate_state_ownership`'s guard
  against a basename-keyed state dir landing inside the installer clone now
  includes `.leerie`, keeping the drift-detection invariant
  (`test_install_subtree_list_matches_repo_top_level_dirs`) satisfied.

### Changed

- **`leerie config --recapture` gathers manifests + install-filtered commands.**
  The recapture seam (already wired to `run_recapture_deps` →
  `capture_repo_deps`) now benefits from the manifests-first corpus, so
  `--recapture`/`--recapture --force` produce correct structured
  `language_installs`/`setup_packages` for the repo.

## [0.9.51]

### Fixed

- **Runs no longer falsely pause with "out of credits" when the account
  has plenty of quota** (#44). The out-of-credits mid-stream-kill detector
  latched on `overageStatus:"rejected"`, which is a *standing config state*
  emitted by **every** `rate_limit_event` from an org that has extra-usage
  (overage) disabled at the org level (`overageDisabledReason:
  "org_level_disabled"`, `status:"allowed"`) — not a moment of credit
  exhaustion. The latch armed on the first rate-limit event and stayed set,
  so any later *unrelated* mid-stream truncation (a crash, a kill, a reaped
  subprocess) was misreported as out-of-credits and routed into a bogus
  300s "sleeping then auto-resuming" pause. Evidence: one navegando run had
  96/96 rate-limit events in this shape while workers succeeded. The latch
  now keys only on `overageDisabledReason ∈ {out_of_credits, out_of_overage}`
  (actual exhaustion); an `org_level_disabled` truncation takes the ordinary
  `WorkerError` path (retryable / advisory), never a pause. Pinned by
  `test_invoke_org_level_disabled_truncation_raises_workererror`.

### Changed

- **A genuine out-of-credits kill now pauses-and-surfaces instead of
  auto-resuming** (#44). Out-of-credits has no reset clock — it clears only
  on a top-up or billing cycle, not by waiting — so the fixed 300s
  auto-resume loop introduced in 0.9.4x (right for *rate limits*, which do
  reset on a clock) only spun against the wall and burned the worker budget.
  `RateLimitedExit` gains an `out_of_credits: bool` flag; when set, `main()`
  runs worktree-only cleanup, prints an `out of credits — leerie --resume
  <id>` hint, and exits `EXIT_LOCKED` (75). Clock-based rate limits (session
  limit / terminal `status`) still auto-resume via `_sleep_then_reexec`
  unchanged. The README rate-limit-recovery section is corrected to describe
  the current auto-resume behavior and adds a distinct out-of-credits bullet.

## [0.9.50]

### Changed

- **`leerie config --recapture --force` now performs a true wholesale
  replace** (#43 follow-up). Previously `--force` only dropped the
  `dep_capture.done` sentinel so the worker re-fired over already-captured
  runs, but the write still union-merged — the "wholesale replace" the
  launcher comment advertised was never implemented. `capture_repo_deps`
  gains a `replace: bool = False` parameter that `run_recapture_deps` threads
  as `replace=force`: under `--force` the persisted `setup_packages` +
  `language_installs` are overwritten from the fresh capture (deps no longer
  captured are dropped), while every automatic seam (finalize / cancel /
  backstop) stays union / never-clobber. An empty capture under `--force`
  leaves the existing config untouched, so a bad run can never blank a good
  config.
- **The `dep_capture` model override is now env-var-only.** `resolve_models`
  carried inert `--model-dep-capture` (`args.dep_capture_model`) and
  `model_dep_capture` `leerie.toml` slots that no CLI flag or documented key
  ever populated; both are removed. `dep_capture` is overridable via
  `LEERIE_MODEL_DEP_CAPTURE` (and the global `--model` / `LEERIE_MODEL` /
  `model` key) only, matching what CLAUDE.md already documented. Its `opus`
  default comes from the global `MODEL_DEFAULT` fallback — it is intentionally
  absent from `MODEL_DEFAULT_PER_WORKER`, and the stale IMPLEMENTATION.md /
  README.md claims that it was registered there (or honored a `model_dep_capture`
  TOML key) are corrected.

### Fixed

- **`--recapture --force` could blank a good `.leerie/config.toml`.** A
  schema-valid empty-item capture — `setup_packages: [""]` or a
  `language_installs` entry with an empty `manager`/`command` — passed the
  `if setup_packages:` list-truthiness guard, rendered to `""`, and was
  persisted by `_write_config_toml_keys`, wiping the existing config under the
  replace path (the default union path was unaffected). Fixed two ways
  (DESIGN §12 — guarantees that can be checked mechanically live in code): the
  replace path now gates each write on the *rendered* value being non-empty
  (and filters empty-manager `language_installs` entries), and the
  `dep_capture` schema gains `minLength: 1` on `setup_packages` items and on
  each `language_installs` `manager`/`command` (mirroring `pr_writer`). Pinned
  by a regression test that fails against the pre-fix code.

## [0.9.49]

### Changed

- **Per-repo dependency capture is now decided by an LLM `dep_capture`
  worker instead of a regex scanner** (#43). The old regex path only
  persisted `apt-get install` packages and discarded every language
  install (pip/pnpm/uv/cargo/go/…), so pip/pnpm repos got no `.leerie/`
  at all. The new `dep_capture` worker reads the Bash command corpus from
  each run's `logs/` (bounded by `_DEPCAP_TOTAL_BUDGET`), decides the
  dependencies the repo genuinely needs across all languages, and emits
  `setup_packages` + `language_installs` via schema-validated structured
  output; the code writes them deterministically to `.leerie/config.toml`
  (union-merged, never-clobber) and the launcher bakes per-manager
  `COPY`+`RUN` layers into the generated Dockerfile. One worker funnels
  three triggers — in-run finalize, the cancel/SIGTERM exit arms, and a
  SIGKILL/crash backstop (next-run + `--recapture`) — with a
  `dep_capture.done` idempotency sentinel. The regex helpers
  (`_capture_installs_from_logs`, `_parse_apt_intents`, `_LANG_INSTALL_RE`,
  `_APT_INSTALL_RE`, and their apt sub-regexes) are deleted.

### Fixed

- **The language-dep Dockerfile bake now parses under bash 3.2 (macOS
  `/bin/bash`), unbreaking `pytest tests/`.** #43 enlarged the Python
  heredoc the launcher piped inline via
  `_lang_layer="$(python3 - … <<'PY' … PY )"`. bash 3.2 mis-scans a quoted
  heredoc nested inside a `"$(…)"` command substitution once the body
  grows past a threshold and aborts with `unexpected EOF while looking for
  matching ''`. The Dockerfile-bake test harnesses extract that block and
  run it under the system bash, so 58 tests failed — including #43's own
  `test_dockerfile_bake_from_capture.py`. The launcher now writes the
  heredoc body to a temp file (`cat >"$_dep_pyf" <<'PY'`, outside any
  command substitution) and runs `python3 "$_dep_pyf" …`; the Python body
  and its argv indices are unchanged. Cleanup is guarded with
  `|| _lang_rc=$?` (mirroring the `_launch_stderr`/`_early_probe_stderr`
  sites) so `set -euo pipefail` never aborts before `rm -f` runs, and the
  original abort-on-python-crash contract is preserved by re-raising the
  exit code.

## [0.9.48]

### Changed

- **Documentation-only follow-up to 0.9.47** (no code or behavior change).
  Corrected three residual references to the retired "reset-time-unparseable →
  exit 75 / manual resume" behavior that the 0.9.47 edits missed:
  `docs/IMPLEMENTATION.md` still described `_sleep_then_reexec` with its old
  `bool` return ("returns True on Ctrl-C") rather than the shipped
  `-> int | None` contract (130 / 128+signum / 75 / None); `docs/DESIGN.md` §6
  documented only the SIGINT→130 sleep-interrupt case and now also covers
  SIGTERM/SIGHUP→128+signum and `os.execv`-failure→75; and a stale docstring in
  `tests/test_invoke_streaming.py` described the removed exit-75 path.

## [0.9.47]

### Fixed

- **The `cfg-*` startup sweep can no longer delete a live run's staging dir.**
  The sweep keys on each `cfg-*` dir's top-level mtime, which freezes at
  staging-completion — nested writes by the running container's rw bind-mount do
  not bump it. A run whose container stayed up past the `-mtime +1` threshold
  (now plausible given 0.9.46's auto-resume loop holding the mount open across
  backoffs) had a stale-*looking*-but-live cfg dir that a concurrent launch's
  sweep would `rm -rf`, corrupting the live container's `.claude`/creds. Each run
  now runs a background keepalive that `touch`es `$STAGE` hourly for the run's
  lifetime, so a live dir never looks stale to `-mtime +1`; the keepalive is
  torn down by the same EXIT traps that remove `$STAGE`.
- **`_sleep_then_reexec` now returns a correct exit code on interrupt/execv
  failure.** SIGTERM/SIGHUP during the rate-limit backoff wait now exits
  `128+signum` (e.g. 143) instead of a bare 1, and an `os.execv` failure exits
  `EXIT_LOCKED` (75, EX_TEMPFAIL) instead of falling through — matching the
  documented signal/temp-fail contract. State is preserved either way (cleanup
  runs before the sleep).

### Changed

- **Doc-drift cleanup for the 0.9.46 auto-resume change.** Three stale references
  to the retired "reset-time-unparseable → exit 75 / manual resume" behavior were
  rewritten to the shipped auto-resume contract: the `_invoke` overage comment
  and the rate-limit doc-comment in `orchestrator/leerie.py`, and the exit-75
  teardown-table row in `docs/DESIGN.md` (exit 75 remains legit for `EXIT_LOCKED`
  and genuine EX_TEMPFAIL worker surfaces).

## [0.9.46]

### Changed

- **Rate-limit with no reset time now auto-resumes instead of exiting.** When a
  run hit an out-of-credits mid-stream kill (or a session-limit message whose
  reset time couldn't be parsed), it previously printed a manual `--resume` hint
  and exited 75. It now sleeps a fixed `RATE_LIMIT_RETRY_BACKOFF_SEC` (5 min) and
  auto-resumes via the same `os.execv --resume` path the parsed-reset-time case
  uses — so a run self-heals once the limit/credits refresh. Bounded by the
  persisted `max_total_workers` budget; Ctrl-C during the wait still drops to a
  manual resume (exit 130). Both rate-limit arms now share
  `_sleep_then_reexec(st, wait_seconds, reason)`.

### Fixed

- **Per-run staging dirs (`~/.cache/leerie/cfg-*`) no longer leak unbounded.**
  The `rm -rf "$STAGE"` EXIT trap is now registered immediately after the
  `mktemp` (previously ~250 lines later, past an `exit 1`, so early exits leaked
  the dir), and a best-effort startup sweep removes `cfg-*` dirs older than a day
  to reclaim leaks from trap-bypassing exits (SIGKILL / OOM / `nerdctl kill`).
  (A backlog of 500+ dirs / ~31 GB had accumulated.)

## [0.9.45]

### Fixed

- **Harden the zombie-reaper's worker-exclusion invariant.** `_invoke` now
  registers a worker's PID in `_ASYNCIO_MANAGED_PIDS` as the first statement
  inside the `try` whose `finally` discards it, so the discard genuinely runs on
  every post-registration exit path (previously the `add` sat just outside that
  try — a theoretical, fail-safe leak). Also corrected the accompanying comment
  and a flaky test: `_ASYNCIO_MANAGED_PIDS` is **load-bearing**, not
  belt-and-suspenders — a just-exited, not-yet-watcher-reaped asyncio child is
  briefly a `state==Z, ppid==getpid()` zombie indistinguishable from a true
  orphan, so only the registration set tells the reaper to leave it alone. The
  race test now registers its child (mirroring `_invoke`) and is deterministic
  (10/10 in the Linux container).

## [0.9.44]

### Fixed

- **Zombie reaper could steal a live worker's exit status (race with asyncio).**
  The 0.9.43 `_zombie_reaper` used a blanket `os.waitpid(-1, WNOHANG)`, which
  races asyncio's own child watcher: if the reaper reaped a live `claude -p`
  worker's PID first, asyncio reported the worker's exit as returncode 255 and
  logged a spurious "Unknown child process" warning every run (and mis-classified
  a genuinely-failed worker's error). The reaper is now **targeted** — it scans
  `/proc` for its own zombie children (`state == Z`, `PPid == getpid()`) that are
  not in the set of asyncio-managed worker PIDs (`_invoke` registers each worker
  at spawn, discards after its await) and `waitpid`s each individually, so it
  never touches a PID asyncio is awaiting. Also: `_reparented_orphans` now accepts
  `ppid in (1, os.getpid())` since the subreaper reparents orphans to the
  orchestrator rather than PID 1. Verified in the Linux/Python-3.13 container: a
  live child that exits 7 keeps its true exit code (not 255) with the reaper
  running hot across its exit.

## [0.9.43]

### Fixed

- **Worker PID-exhaustion from unreaped zombie subprocesses.** A worker could
  wedge with `pids.current=N/N` (every `fork()` returning `EAGAIN`) not from a
  live process burst but from **zombies**: the container's PID 1 is `runuser`/the
  entrypoint (or an idle `sleep infinity` on Fly), not a reaping init, so
  orphaned `git`/`ssh-agent` descendants reparented to PID 1 and were never
  `wait()`ed — accumulating as `<defunct>` tasks that still count against the
  cgroup `pids.max` (observed live: 453 defunct `git`, all ppid==1). The
  orchestrator now installs itself as a child-subreaper
  (`prctl(PR_SET_CHILD_SUBREAPER)`, early in `main()`) so orphaned descendants
  reparent to it, and runs a background `os.waitpid(-1, WNOHANG)` reaper
  (lifecycle mirrors the memory sampler) that clears them within ~1s — keeping
  `pids.current` flat. Covers both the local (nerdctl) and Fly runtimes; chosen
  over `nerdctl run --init` (which would not cover Fly and would perturb the
  PID-1 namespace-teardown contract). Validated end-to-end: the run that
  previously pegged 1024/1024 now stays at ≤17 tasks per worker.
- **Leaked ssh-agent in the Fly SSH-isolation test.**
  `tests/test_require_fly_ssh_isolation.py` spawned a daemonized `ssh-agent`
  (via `_leerie_fly_agent_ensure`) whose fragile `pkill -f "ssh-agent -a <sock>"`
  teardown could miss it; the teardown now kills every process under the unique
  per-test HOME.

## [0.9.42]

### Fixed

- **Worker PID-exhaustion loop on subprocess-heavy test suites.** A worker
  running an un-scoped full test suite (e.g. leerie's own ~193-module suite,
  run by the conformer) burst past the per-worker cgroup `pids.max=256` in
  seconds, wedging every subsequent `fork()` with `EAGAIN`. The
  PID-exhaustion detector then killed and retried the worker, but the retry
  re-ran the same suite and re-blew the cap — a deterministic loop that
  failed the wave. Raised the default per-worker PID cap to 1024 and added a
  per-repo override (`--worker-pids-max` / `LEERIE_WORKER_PIDS_MAX` /
  `worker_pids_max` in `leerie.toml`). There is no reliable way to detect and
  refuse "a full test suite run", so the cap value is the enforcement surface
  (DESIGN §6 / §12).
- **`LEERIE_*` environment overrides never reached the orchestrator.** The
  orchestrator runs inside a container and reads its overrides from
  `os.environ`, but the launcher's `nerdctl run` forwarded only
  `LEERIE_STATE_DIR` and `LEERIE_INSPECT_DIRS`. Every other documented host
  override (`LEERIE_MAX_WORKERS`, `LEERIE_SOURCE_OF_TRUTH`, `LEERIE_MODEL`,
  `LEERIE_EFFORT`, `LEERIE_VERBOSITY`, `LEERIE_WORKER_PIDS_MAX`, the
  per-worker `LEERIE_MODEL_<WORKER>` / `LEERIE_EFFORT_<WORKER>` families, …)
  died at the container boundary and silently did nothing. The launcher now
  forwards every `LEERIE_*` variable in its environment except a deny-list of
  launcher/host-only vars; a coupling test guards against a future override
  being silently stranded.

## [0.9.22]

### Fixed

- **`new-worktree.sh` idempotency broken on Fly runtime.** The worktree
  reuse check compared a relative path (`$WT`) against the absolute
  paths from `git worktree list --porcelain`, so the match never fired
  when `LEERIE_STATE_DIR` was unset (the default on Fly machines). On
  continuation retries (confidence gate, mechanical check), the script
  crashed with `fatal: '...' already exists`, making the run
  unrecoverable even after `--resume`. Fixed by canonicalizing `$WT`
  to an absolute path via `pwd -P` before the comparison.

## [0.9.14]

### Fixed

- **`DISALLOWED_TOOLS` hard-deny prevents workers from spawning subagents.**
  Workers running under `--dangerously-skip-permissions` could call `Agent`,
  `ScheduleWakeup`, and other tools outside the `--allowedTools` list because
  `--allowedTools` is a permission pre-approval (soft), not a visibility
  restriction (hard). A classifier on a real run spawned an Explore subagent,
  set a 60-second wakeup timer, and exited without calling `StructuredOutput`
  — crashing the run. A new `DISALLOWED_TOOLS` constant is now passed via
  `--disallowedTools` on every `claude -p` invocation. Unlike `--allowedTools`,
  `--disallowedTools` removes tools from the model's context entirely, surviving
  `--dangerously-skip-permissions`. Denies: `Agent`, `SendMessage`,
  `ScheduleWakeup`, `CronCreate`/`Delete`/`List`, `RemoteTrigger`,
  `PushNotification`.

## [0.9.13]

### Added

- **Per-repo `.leerie/` configuration directory (DESIGN §6½).** Repos may
  now commit a `.leerie/` directory to declare build/lint/test commands,
  system dependencies, and a custom container image — eliminating the
  per-run re-discovery overhead reported by power users.

  - **Declared BLT commands via `.leerie/config.toml`.** Add `build`,
    `lint`, and/or `test` keys to opt specific axes out of
    `_infer_build_lint_test()` auto-detection. Missing keys fall through
    to inference; an empty string means "not applicable." Implemented by
    new `_load_blt_config()` and `resolve_blt()` functions in
    `orchestrator/leerie.py`; both conformer call sites updated.

  - **Per-repo container image from `.leerie/Dockerfile`.** A Dockerfile
    that extends `ARG BASE_IMAGE` / `FROM $BASE_IMAGE` is built into a
    derived image tagged `leerie-repo/<repo-id>:<version>` (local) or
    `registry.fly.io/$APP:<version>-<hash>` (Fly). The image is cached and
    rebuilt only when the base version or Dockerfile content changes. When
    `.leerie/config.toml` declares `setup_packages` (comma-separated apt
    package names) and no Dockerfile exists, one is auto-generated. Implemented
    in the launcher (`resolve_repo_image_tag`, `build_repo_image`); the Fly
    path extends `build-push.sh` with `--dockerfile` and `--build-arg`
    forwarding.

  - **Ruby `BUNDLE_PATH` cache mount.** The launcher now mounts
    `~/.cache/leerie/bundle` as the Bundler gem cache
    (`BUNDLE_PATH=/home/leerie/.cache/leerie/bundle`,
    `BUNDLE_CACHE_ALL=1`), so `bundle install` reuses downloaded gems
    across worktrees and runs — matching the existing pnpm/pip/cargo cache
    mounts.

  - **`leerie config` onramp verb.** A fast-path launcher verb (no
    container required) with three modes: bare (print effective config
    for each BLT axis with `[config]` vs `[inference]` provenance);
    `--init` (generate `.leerie/config.toml` with auto-detected values
    and a commented `setup_packages` example, then suggest
    `git add .leerie/`); `--chat` (launch an interactive `claude`
    session with `prompts/config_chat.md` as the system prompt so the
    user can configure leerie conversationally against their own repo).

  - **`.leerie/` seed-transport whitelist.** `scripts/remote/seed-repo.sh`
    previously dropped all `.leerie/` paths before rsync to Fly machines
    (to avoid shipping host-side run state). The filter now whitelists
    `.leerie/config.toml`, `.leerie/Dockerfile`, and
    `.leerie/.leerie-setup.sh` so committed config declarations reach
    the machine unchanged.

## [0.9.5]

### Fixed

- **Local container: strip `use-keyboxd` from staged `.gnupg/common.conf`.**
  macOS GnuPG 2.4+ sets `use-keyboxd` in `~/.gnupg/common.conf`. The
  launcher copied this into the container staging dir, but excluded the
  `S.keyboxd` socket. Inside the container, `gpg` tried to reach the
  host keyboxd daemon and crashed with "SQL library used incorrectly",
  killing `mise install` during phase 1½. The directive is now stripped
  after staging so gpg falls back to file-based `pubring.kbx` lookup.
  Fly was unaffected (`seed-auth.sh` excludes `.gnupg` entirely).

## [0.9.4]

### Fixed

- **Rootless seccomp: tighten detection to containerd-rootless sentinel.**
  The `--security-opt seccomp=unconfined` flag (required for
  `unshare(CLONE_NEWUSER)` inside the container) was gated on `id -u != 0`,
  which fired on every macOS/Colima run where containerd is rootful and no
  seccomp lift is needed. Now gated on the `containerd-rootless/child_pid`
  sentinel — the same signal the cgroup probe already uses.

## [0.9.3]

### Fixed

- **Rootless containerd: user-namespace remap at UID 0.** Claude Code
  rejects `--dangerously-skip-permissions` from UID 0. The rootless
  entrypoint now uses `unshare --user --map-user --map-group` to remap
  outer UID 0 to the `leerie` user in a nested user namespace. The
  orchestrator runs as non-root and the flag is accepted. Bind-mounted
  host dirs remain accessible via the UID mapping.

## [0.9.2]

### Fixed

- Superseded by 0.9.3.

## [0.9.1]

### Fixed

- Superseded by 0.9.2.

## [0.9.0]

### Added

- **Rootless containerd support** via PR #18: rootless privilege drop,
  cgroup probe, Linux `stat` compatibility, Ruby dev libraries.
- **`--fly-app` CLI flag** for setting the Fly app name on the command line.
- **`libssl-dev`** added to the container image for Ruby OpenSSL extension
  and gems linking against OpenSSL.

### Changed

- **`LEERIE_FLY_APP` is now required when `--runtime fly`** (Fly.io app
  names are globally unique; the old `leerie` default silently failed for
  other users). Set via `--fly-app <name>` or `export LEERIE_FLY_APP=<name>`.

## [0.8.3]

### Fixed

- **`--chain` now forwards per-job flags to wave invocations.** Flags
  like `--effort high`, `--model opus`, and
  `--dangerously-skip-permissions` are collected into a passthrough
  array and appended to each per-job `./leerie` invocation. Previously
  these were rejected with "unknown flag" and required env var
  workarounds. The chain launcher uses a DRY catch-all (no flag
  enumeration); the per-job child's own argparse validates.

## [0.8.2]

### Fixed

- **Dockerfile: remove chmod for non-existent chain scripts.** The
  `chain/` subpackage no longer has shell scripts after the v0.8.0
  rewrite to a laptop-side wave sequencer; the Dockerfile's `chmod`
  step failed on build.

## [0.8.1]

### Added

- **Migration-surface completeness checks (DESIGN §5).** When a planner
  subtask introduces a new pattern replacing an old one (e.g., a new
  accessor replacing direct field reads), `check_planner_output()` now
  greps the repo for old-pattern call sites and emits
  `UNCOVERED_MIGRATION_SURFACE` when > 5 files are uncovered. The CRITIC
  loop feeds this back as structured feedback; multi-sample selection
  deprioritizes samples that miss the surface. Motivated by a
  funeralworks multi-tenant task that required 4 identical runs because
  the planner designed a seam but nobody audited the ~200 routes still
  using the old pattern.

- **`warn_layer_gaps()` advisory cross-domain check.** Runs on the
  reconciled plan before scheduling and surfaces two heuristic warnings:
  (1) `schema.prisma` modified but no subtask touches seed or migration
  files; (2) subtask `provides` tags contain env/bootstrap/secret
  keywords but no subtask touches `.env.example`. Zero false positives
  across 44 historical runs.

- **Planner prompt migration-sweep guidance.** The planner prompt now
  instructs planners to grep for old-pattern call sites when introducing
  a replacement pattern and batch-plan the full migration surface.

## [0.8.0]

### Changed

- **`chain.git_ops.synth_merge_branches` passes a bot identity to
  `git merge` defensively.** The function now invokes
  `git -c user.email=leerie-chain@bot.invalid
  -c user.name=leerie-chain merge --no-ff --no-edit
  origin/<branch>` so the merge commit succeeds even when the
  laptop's (or CI runner's) global git identity is unset. Without
  this, the merge failed with "Committer identity unknown" and the
  wave-loop's synth-merge step aborted with a misleading
  `SynthMergeConflict` on the FIRST branch instead of the actually-
  conflicting one. Closes a real production gap on laptops without
  `git config --global user.email/name` and fixes the six PR-#10 CI
  failures on Ubuntu runners (Python 3.10/3.11/3.12). DESIGN/spec
  surface unchanged; the bot identity matches the existing
  `write_audit_artifact` convention. Documented in IMPLEMENTATION.md
  §1966.

### Fixed

- **`tests/test_chain_git_ops.py` fixtures pin HEAD to `main`.**
  The `origin_repo` and `seeded_origin` fixtures now pass `-b main`
  to `git init --bare` and `git init` so the bare repo's HEAD
  symbolic ref points at `refs/heads/main` regardless of the
  runner's `init.defaultBranch` config (CI Ubuntu defaults to
  `master`). Without this, the fixture pushed
  `HEAD:main` but the bare HEAD still pointed at the absent
  `master`; downstream `git clone` then checked out an EMPTY
  working tree, and the `test_write_audit_artifact_commits_chain_json`
  verification `assert artifact.exists()` returned False even though
  the file was correctly committed and pushed.

- **`tests/test_chain_credential_transport.py` skips Darwin-only
  tests on Linux.** Two `*_on_darwin` tests exercise the macOS
  Keychain code path that the launcher's
  `_extract_claude_credentials_json` helper gates on
  `uname -s = Darwin`; on Linux the stub `security` binary is never
  invoked, so the assertions never see the blob. The tests now skip
  with `@pytest.mark.skipif(sys.platform != "darwin", ...)`. The
  other three tests in the file already work cross-platform.

- **CI workflow sets a default git identity.** `.github/workflows/test.yml`
  gains a "Set git identity" step (`git config --global user.email
  ci@leerie.invalid && git config --global user.name "Leerie CI"`)
  before `pytest tests/` runs. Defense-in-depth catch-all so any
  future test that forgets to set identity per-operation doesn't
  silently fail on a runner that has no global config.

- **Test consistency: `tests/test_cleanup_run_scoped.py` `git init`
  calls.** Four `git init -q` sites (lines 105/197/227/262) now
  include `-b main`, matching line 143's existing convention. These
  sites were not at risk of the HEAD-symbolic-ref bug above (no
  downstream clone-and-verify pattern), so this is consistency
  hygiene rather than a bug fix.

## [0.4.21]

### Fixed

- **Documentation alignment: `plan_overlap_judge` added to all worker
  enumerations.** Every doc, code comment, argparse help string, and
  test that listed judgment or inspect-bucket workers now includes
  `plan_overlap_judge`. Also fixes stale "seven worker types" → "eight"
  counts and a "Twelve" → "Eleven" miscount in the model-override table.

## [0.4.20]

### Changed

- **`max_parallel` default lowered from 10 to 5.** Reduces the default
  concurrent-workers-per-wave cap. Users on larger VMs can opt back up
  via `--max-parallel`.

## [Unreleased]

### Fixed

- **Per-repo derived image no longer forces PID 1 to run as `leerie`.**
  The auto-generated `.leerie/Dockerfile` ended with a trailing
  `USER leerie`, which overrode the base image's deliberate no-`USER`
  (root) default. Since the derived image inherits the base
  `ENTRYPOINT` (`scripts/container-entry.sh`), PID 1 then ran as
  `leerie` instead of root, so the cgroup-slice writes, the root
  cgroup broker's socket `bind()`, and the final `runuser` all failed
  EACCES and the container exited 1 before the orchestrator ever
  started (`runuser: may not be used by non-root user`). Both
  generator sites now end the apt layer at `USER root`; the base
  entrypoint drops to `leerie` itself via `runuser`, per DESIGN §6
  *Memory containment*. The user-facing hand-authored-`.leerie/Dockerfile`
  examples in `docs/USAGE.md` and `prompts/config_chat.md` were corrected
  to match (they had shown a trailing `USER leerie`). Latent since the
  per-repo derived-image feature (#26); triggered once a repo acquired a
  `.leerie/config.toml` with `setup_packages`/`language_installs`.
- **Language-dep bake now copies `patches/` and workspace manifests
  on the persisted-config path.** A repo using
  `pnpm.patchedDependencies` (or yarn/npm workspaces) baked without its
  `patches/` dir, so `pnpm install --frozen-lockfile` failed `ENOENT`
  on a patch file and the launcher silently degraded to an apt-only
  image (no baked `node_modules`). The persisted `language_installs`
  path now augments its `copy_inputs` via `_node_ancillary` for node
  managers (matching the auto-detect path), and `_emit_layer` emits
  each directory or nested-path source as its own path-preserving
  `COPY dir/ ./dir/` line instead of flattening it into `./` (which
  also fixes a latent workspace-child `package.json` basename clobber).
- **Launcher no longer hangs after an abnormal container exit.** In
  piped mode (`leerie … | tee log`), Colima's persistent SSH
  ControlMaster holds a copy of the launcher's stdout-pipe write-end
  for the run; on an abnormal container exit (PID-1 crash, OOM
  SIGKILL, mid-run kill) it retained that copy, so `tee` never
  received EOF, the launcher never returned, and the `--rm` container
  was orphaned (wedging the run-dir flock and later `--resume`). The
  launcher now points `nerdctl run` at a run-log file (the mux does
  not retain a plain-file fd) and streams it to its own stdout via a
  `tail -f` it owns and reaps in its EXIT/INT/TERM traps — zero blast
  radius on concurrent group/chain runs. Gated to the piped, non-TTY
  case; interactive `-it` runs are unchanged. (DESIGN §6 *Launcher
  hang on abnormal container exit*.)

### Added

- **Chain orchestration: laptop-side wave sequencer (`leerie --chain`,
  DESIGN §19).** Submit a multi-wave chain of leerie runs with
  `--wave <prompt-file[,prompt-file,...]>` (repeatable per wave). Each
  wave fans out N parallel `./leerie --runtime fly` invocations on the
  laptop; the wave loop waits for all to finalize via the existing
  single-run path (provision → seed-auth + seed-repo → orchestrator →
  decide_teardown trap → fetch_branch → host_finalize →
  destroy_machine), then runs `chain.git_ops.synth_merge_branches` in
  `$USER_REPO` to build `leerie/stage/<chain-id>-wave-<N+1>` and
  pushes it to origin before advancing to the next wave. The laptop is
  the sequencer; **no Fly coordinator machine, no per-chain SQLite,
  no 6PN HTTP, and zero Fly machines hold GitHub credentials at any
  point** — each wave job's `host_finalize` runs on the laptop using
  the user's existing `gh auth`. Chain-scoped verbs (`--status`,
  `--stop`, `--kill`, `--resume`, `--finalize`, `--attach`,
  `--list --chains`) operate by iterating
  `$LEERIE_STATE_HOST_DIR/runs/*/run.json` filtered by the new
  `chain_id` field. Resume after a wave failure via
  `leerie --resume <chain-id>` then
  `leerie --chain --chain-id <prior-uuid> --wave …`; the wave loop's
  idempotency check (against `pushed_at` + the synth-merge probe via
  `git ls-remote --exit-code origin <stage>`) skips already-completed
  waves and synth-merge transitions whose stage branch is already on
  origin.

### Changed

- **`--attach` folded into `--resume`.** The two verbs collapsed into a
  single smart router: `leerie --resume <run-id>` now wakes a paused
  machine, attaches to a still-alive orchestrator, or relaunches against
  an alive-but-orphaned machine — automatically based on the launcher's
  observed state. Detection reuses the rc=75 signal from the existing
  in-machine flock probe (DESIGN §6 *Single owner per run dir*).
  Default behavior on the "alive orchestrator" branch is to tail the
  orchestrator log; new sub-mode flags `--shell` (bash at `/work`) and
  `--auto-finalize` (exec `leerie --finalize` on clean exit) carry
  forward the prior `--attach` capabilities. The old `--attach
  --all-logs` (per-worker log glob) is deferred to a follow-up; it
  needs new plumbing in `render_tail_wrapper` and was scoped out of v1
  to keep the diff focused. `--resume` without `--run-id` auto-discovers from
  `$LEERIE_STATE_HOST_DIR/remote/*.json` (lifted from `attach.sh`
  Strategy B). `scripts/remote/attach.sh` is removed; `--attach` is no
  longer a valid launcher flag (the launcher's unknown-flag error
  catches muscle-memory invocations). Hint strings across
  `provision.sh`, `re-seed.sh`, `force-finalize.sh`, and the orchestrator's
  `StateLockedError` handler updated. The new shared helper
  `tail_with_optional_autofinalize` in `lib.sh` is used by the
  `--resume` rc=75 pivot; the existing fresh-launch tail at
  `leerie:2724-2754` keeps its inline payload for now (a small
  follow-up could route it through the helper to gain
  `--auto-finalize` support on fresh runs too). DESIGN.md §6 *The
  user-visible verb surface* collapsed from 5 rows to 4;
  *Interactive attach over PTY in remote mode* renamed *Smart resume
  in remote mode*. (DESIGN §6 *Smart resume in remote mode*.)

### Added

- **Planner-output budget feasibility preflight (DESIGN §13 *Budget
  feasibility — fail fast at the cheapest moment*).** A new pure-Python
  gate `check_budget_feasibility()` runs immediately after `schedule()`
  returns and before `write_plan()` persists. When the estimated
  `claude -p` calls (per-subtask multiplier × subtask count + per-wave
  integrator + 1 `pr_writer`, added to `worker_count` already spent on
  upstream phases, multiplied by the safety margin) exceeds
  `--max-workers`, the orchestrator `die()`s with a new exit code
  `EXIT_BUDGET_INFEASIBLE=11` and a recommended `--max-workers` value
  — at the cheapest possible moment, before any implementer has
  spawned. Closes the failure mode where a too-large plan was only
  detected mid-execution by `State.bump_workers()` after $50+ of work
  had been sunk.

  Calibrated from six on-disk runs (three completed cluster at 2.0–2.31
  calls/subtask; one died-mid-execution logged 2.59 with the
  lint-fighting inflator). Default `subtask_call_estimate = 2.5`,
  `budget_safety_margin = 1.15`. Backward-validated: the estimator
  would have caught the 29-subtask summarizer run at its bootstrap
  with a recommended `--max-workers 106`, and would have passed each
  of the three observed successful runs at default cap 60. Opt-out via
  `--skip-budget-check` / `LEERIE_SKIP_BUDGET_CHECK` /
  `skip_budget_check = true` in `leerie.toml`. Fly runtime's
  `decide_teardown` trap routes exit 11 to destroy (not pause) and
  prints a budget-specific recovery hint, so a structurally
  unrecoverable run does not leave a paid-for Fly volume hanging.

- **Implementer + conformer prompts gained an "environmental issues
  are out of scope" subsection.** Documents the rule that pre-existing
  `lint` / `typecheck` / `test` failures in files outside the subtask's
  `files_likely_touched` list are environmental noise — record once
  in evidence, do not spend tool calls fixing them, do not run
  auto-fixers that will touch them, and revert any side-effect changes
  from `lint:fix`/`prettier --write`. Lowers the per-subtask call
  ratio (the inflator that pushed the dying summarizer run from a
  baseline of 2.0–2.3 to 2.59), reducing wasted budget on
  unrelated technical debt.

### Changed

- **Hallpass cold-start probe is now invoked exactly once per run from
  `seed_auth`.** Previously the post-`seed_auth` flow re-probed in
  `seed_repo_clone`, `seed_repo_dirty`, and `seed_inspect_dirs` — three
  extra `wait_for_fly_ssh_ready` calls against a channel that
  `seed_auth` had already exercised by transferring ~15 MB of config
  and running a multi-minute plugin install. The re-probes had no
  diagnostic value (the bundle/rsync transports' own
  `LEERIE_SEED_TIMEOUT_S` wrappers are the authoritative failure
  detector) but manufactured false-positive failures: ~175 s of
  silent wait per re-probe followed by a misleading "did not accept
  SSH within 60s" warning before the actual transport proceeded
  successfully. The 2026-06-05 investigation traced the entire
  confusing log sequence to this. The surviving probe in `seed_auth`
  is hardened: success emits `remote: hallpass ready on <machine>`;
  the misleading "60s" bound string is corrected to "~175s" (the
  real bound is 12 attempts × 10 s timeout + 11 × 5 s sleep); the
  probe call is wrapped in a subshell with `2>/dev/null` to suppress
  bash's `Killed: 9` job-control noise when `timeout` is SIGKILL'd
  by an external process (most commonly macOS Jetsam under
  concurrent-run host memory pressure); on loop exhaustion, a last
  probe with rc 137 is identified in the warning as "killed
  externally — possible host memory pressure on this client" so the
  operator can distinguish client-side pressure from a real Fly
  outage.

- **Per-repo state directory layout: `$HOME/.leerie/state/<sha16>-<basename>/` →
  `$HOME/.leerie/<basename>/`** (DESIGN §10 *Where coordination artifacts live*,
  IMPLEMENTATION §2 *Host-side per-repo state directory*). The launcher's `_state_dir_default` no
  longer SHA-256-prefixes the directory name; the default is just the
  basename of the repo. Browsability wins (`ls ~/.leerie/` shows
  repo-named subtrees instead of opaque hex blobs) at the cost of a
  basename collision when two distinct abs_paths share a basename
  (e.g. `~/src/myproject` and `~/work/myproject`).

  Cross-repo basename collisions are caught at use time by a new
  `_validate_state_ownership` check in the launcher. The check runs
  once per invocation, before the verb dispatch (skipped for
  `--version` and the `--chain-*` verbs, which don't touch local
  state). On first claim of a fresh directory, the launcher writes
  `<state-root>/.owner` containing the resolved repo path. On
  subsequent runs the recorded owner must match the current repo —
  mismatch is fatal, with a stderr message naming both paths and the
  three override knobs (`--state-dir`, `LEERIE_STATE_DIR`,
  `leerie.toml: state_dir = ...`). The same check also refuses to
  write into the leerie install directory: a target with `.git/` or a
  `leerie` executable at top level and no `runs/` subdir is treated
  as an installer mistake, not a state dir. Two follow-up refinements
  extend the install-collision defenses: (1) a parent-scan that
  catches the case where the target is a *subdirectory* of the
  installer's clone (a user repo named e.g. `docs` resolving to
  `$HOME/.leerie/docs/`, which IS the installer's tracked docs
  subtree); and (2) a file-vs-directory check that catches the case
  where the basename matches an installer-tracked *file* (e.g. a
  user repo named `Dockerfile` or `LICENSE`). Both produce
  actionable error messages with the same three override knobs
  instead of raw bash errors. A defensive test guards the
  parent-scan's hardcoded basename list against drift when future
  PRs add new top-level directories to this repo.

  **No automatic migration.** Operators with state at the
  pre-cutover path (`$HOME/.leerie/state/<sha16>-<basename>/`) will
  not have their runs surfaced by `leerie --list` or `leerie --resume`
  under the new launcher. To migrate manually:

  ```bash
  mv $HOME/.leerie/state/<old-key>/ $HOME/.leerie/<basename>/
  printf '%s\n' "$(pwd -P)" > $HOME/.leerie/<basename>/.owner
  ```

  Pre-existing state dirs that have a `runs/` or `worktrees/` subdir
  but no `.owner` are backfilled silently — the operator's own `mv`
  recipe above doesn't need the `printf` step on a dir that already
  contains run state.

  This repo dogfoods leerie on itself; its basename is `leerie`,
  which sits adjacent to the installer's clone at `$HOME/.leerie/`.
  A committed `leerie.toml` at the repo root pins this repo's state
  to `~/.leerie/_self/` so `./leerie "task"` works zero-config for
  maintainers.

### Fixed

- **Fly `fetch_branch` no longer fails after orchestrator `die()` exits.**
  `die()` raises `SystemExit`; the `except SystemExit` handler in
  `main()` now writes `finished_at` to both `state.json` and `run.json`
  (best-effort, guarded by `st is not None`) before re-raising. On Fly,
  the tail wrapper always exits 0 (it polls the orchestrator pid and has
  no channel for the exit code), so `decide_teardown` takes the
  clean-exit branch and `fetch_branch`'s discovery script requires
  `finished_at` to find the run. Without this write, every post-setup
  `die()` (e.g. "wave 1 has unresolved subtasks") triggered the
  sync-failure banner and required manual `--finalize --force` recovery.
  The `--force` path remains for uncontrolled exits (SIGKILL, OOM,
  power loss) where the handler never ran. Idempotent on `--resume` —
  `phase_finalize` overwrites `finished_at` on success.

- **Bootstrap resume copies `fly-machine.json` to promoted dir.** When
  `mv` was skipped during bootstrap-to-final rename (promoted dir
  already existed from state sync), `fly-machine.json` was left behind
  in the bootstrap dir, causing `re_seed` to fail to resolve the
  machine id on the next resume.

- **`scripts/finalize.sh` and `scripts/cleanup.sh` now honor
  `LEERIE_STATE_DIR`.** Both scripts hardcoded `.leerie/runs/<id>/`
  relative to CWD, while `scripts/setup-run.sh:25` correctly resolved
  the run dir via `${LEERIE_STATE_DIR:-.leerie}`. The asymmetry was
  invisible until phase 6 ran — wave 4 of a resumed Fly run was the
  first time finalize.sh executed inside the container, and it
  immediately aborted with `working-branch missing — run setup-run.sh
  … first` (the file was at `/leerie-state/runs/<id>/working-branch`
  where setup-run.sh wrote it; finalize.sh looked at
  `/work/.leerie/runs/<id>/working-branch`). The orchestrator died
  with rc=1, which is NOT in `decide_teardown`'s auto-sync rc set
  (`0|10|11|75`), so wave 4's commits remained stranded on the Fly
  machine. cleanup.sh had the same hardcoded paths, silently no-op'ing
  the post-finalize subtask-branch cleanup invoked from
  `orchestrator/leerie.py:13085` because `/work/.leerie/runs/` doesn't
  exist on Fly machines. Both scripts now derive
  `LEERIE_ROOT="${LEERIE_STATE_DIR:-.leerie}"` and reference
  `${LEERIE_ROOT}/runs/...` throughout — mirrors the same pattern in
  `setup-run.sh` and the `scripts/remote/re-seed.sh` fix shipped in
  `ed1dae1`. Companion source-text tests in
  `tests/test_setup_run_script_paths.py` and
  `tests/test_cleanup_run_scoped.py` pin the new precedence.

- **Rate-limit auto-resume no longer crashes with `FileNotFoundError`.**
  When a worker hit a Claude 5-hour rate limit and the orchestrator
  scheduled an auto-resume after the reset window, `main()` called
  `os.execvp("leerie", [...])` to re-launch a fresh orchestrator
  process. But the `leerie` launcher script is not baked into the
  container image (`Dockerfile:182–185` copies only `orchestrator/`,
  `scripts/`, `prompts/`, `.claude-plugin/`), so on Fly the exec died
  with `FileNotFoundError: [Errno 2]` — strictly worse than no
  auto-resume, since the orchestrator crashed instead of leaving the
  run resumable. On local nerdctl the launcher exists by accident via
  the host-repo bind-mount but would loop infinitely (the launcher's
  `--resume` path calls `nerdctl run` again, with no inside-container
  sentinel to break the recursion). The fix re-execs the orchestrator
  itself — `os.execv(sys.executable, [sys.executable, __file__,
  "--resume", "--run-id", st.run_id])` — which works identically on
  both runtimes, preserves UID/GID/CWD/env across the exec, and uses
  the orchestrator's own `--resume --run-id` argparse (line 13398) so
  no launcher round-trip is needed. The `--max-workers` budget still
  persists across the re-exec (lives in `state.json`). DESIGN.md §6
  and IMPLEMENTATION.md §5 updated to match.

- **`leerie --finalize` no longer silently drops typo'd flags.** The
  argparse loop at `leerie:682–693` was a `case` statement matching
  only `--force`, `--runtime`, `--runtime=*`; any unknown flag fell
  through with no error. A user retrying `--force` recovery as
  `--foorce` would see `_FINALIZE_FORCE` stay `false`, the launcher
  would log `action=fetch` instead of `action=force-patch+fetch`,
  `force_finalize_remote` would never run, and `fetch_branch` would
  correctly report "no completed unpushed run on machine" — leaving
  the user convinced their recovery command had failed for an
  unrelated reason. The fix adds an explicit `--*) error; exit 1`
  catch-all plus a strict extra-positional check. Valid flags
  (`--force`, `--runtime[=]`, `--no-verify`, `--no-push`) are
  recognized explicitly. Companion tests in
  `tests/test_launcher_finalize_no_work.py` cover the typo path, the
  extra-positional path, and the no-false-positive guards for the
  real flags.

- **`scripts/remote/re-seed.sh` now honors `LEERIE_STATE_HOST_DIR`.**
  Line 60 hardcoded `$USER_REPO/.leerie/runs/$LEERIE_RUN_ID`, ignoring
  the state-host-dir override. On the default install
  (`LEERIE_STATE_DIR=$HOME/.leerie/<basename>/`) the launcher writes
  `fly-machine.json` to the state dir, but `re_seed` looked in the
  repo dir, found nothing, and aborted with
  `re_seed: no fly_machine_id at <repo>/.leerie/runs/<id>/...`. The
  symptom was an infinite resume loop: each `--resume` invocation
  re-ran `seed_auth` (~90 s of plugin install) then died at `re_seed`
  and paused the machine. The fix mirrors `fetch-branch.sh:263–267`'s
  precedence: prefer `$LEERIE_STATE_HOST_DIR/runs` when set, fall
  back to `$USER_REPO/.leerie/runs` otherwise. Companion tests in
  `tests/test_re_seed.py` cover both the precedence and the fallback.

- **Per-worker cgroup v2 memory containment now actually works on both
  runtimes (Fly + local nerdctl).** The Dockerfile previously had
  `USER leerie`, which caused ENTRYPOINT to run as the leerie user
  (UID 501) — so `chown /sys/fs/cgroup/leerie.slice` in
  `scripts/container-entry.sh` silently failed with EPERM, the
  orchestrator's `_cgroup_probe` returned False, and every worker has
  been running uncapped since the feature shipped (29f61c7,
  2026-05-30). This silently defeated DESIGN §6's OOM-cascade
  protection: a runaway vitest / tsc / webpack worker could OOM the
  whole container instead of being killed inside its own cgroup. The
  fix drops `USER leerie` from the Dockerfile so PID 1 runs as root,
  performs the cgroup-v2 delegation chown in container-entry.sh, then
  drops privilege via `runuser -u leerie -- env HOME=/home/leerie
  USER=leerie LOGNAME=leerie ...` before exec'ing the orchestrator
  (local nerdctl path) or `sleep infinity` (Fly — where the
  orchestrator is started out-of-band by the launcher's ssh-console
  wrapper that also drops via `Popen(user="leerie")`). The orchestrator
  gains a `_detect_cgroup_root()` helper that prefers
  `/sys/fs/cgroup/leerie.slice` and falls back to `/sys/fs/cgroup`
  only if the delegation step somehow didn't run, and the probe-
  failure log now names the attempted root so the operator can tell
  whether the entrypoint's delegation actually ran. The explicit `env`
  form is used instead of `runuser --login` because the login form
  would chdir to `/home/leerie` and override the `cd /work` invariant
  the orchestrator depends on.

- **`FLY_VM_DISK_GB` now actually absorbs run-time disk growth.** The
  opt-in Fly volume previously mounted at `/home/leerie`, which only
  ever holds bounded caches (`.cache/leerie/{pnpm,pip,go-mod,cargo}`,
  `.local/share/mise`) and the staged `.claude` auth bundle. The
  dominant growth source — the seeded repo at `/work`, the per-run
  state under `/work/.leerie/runs/<id>/`, and the N-wide parallel
  worktree fan-out under `/work/.leerie/runs/<id>/worktrees/<sid>/` —
  was on the ephemeral rootfs and capped at Fly's 8 GiB rootfs default
  (and 2,000 IOPS / 8 MiB/s throughput). Grafana panels showed the
  symptom directly: rootfs climbing into multi-GiB during a run while
  the `/home/leerie` volume sat flat near 0. The volume now mounts at
  `/work` instead — durability and ENOSPC headroom land on the path
  that actually grows, and the workload picks up the volume's higher
  per-machine throughput tier (4k–32k IOPS depending on machine class)
  as a free side-effect. Caches and the auth bundle revert to rootfs;
  `seed_auth` re-runs on every resume so the auth bundle is refreshed
  regardless of pause length. See DESIGN.md §6 *Remote disk policy*.

- **`leerie --resume --runtime fly` no longer silently provisions a
  duplicate Fly machine.** The run_id is now the Fly machine ID
  (DESIGN §6), so the resume dispatch uses it directly — no sidecar
  lookup needed, no ambiguity possible. Explicit `--resume` is
  strict-fail: a missing or unresumable machine aborts with a
  diagnostic pointing at `leerie --list` instead of falling through
  to provision. Fresh runs (no `--resume`) keep their existing
  behavior — first call still provisions.

### Removed

- **All legacy backwards-compatibility shims.** Removed `--list-paused`
  (use `--list --status paused`), `--remote` / `LEERIE_REMOTE` /
  `leerie.toml remote=true` (use `--runtime fly` / `LEERIE_RUNTIME` /
  `runtime=fly`), and `--runs` chain-submit alias (use `--wave-a-runs`).
  Deleted `_check_gh_cli` tombstone comment, tightened the
  `attempt_1_output` parameter on `_validate_unresolved_must_include`
  from optional to required, and reworded "legacy" labels on the
  `.leerie/` fallback to accurately describe it as the
  non-containerized invocation path.

### Fixed

- Removed stale docstring reference to deleted
  `test_launcher_remote_knob.py` in `test_launcher_no_push_skips.py`.

- **Worker cgroup containment was silently OFF; a runaway subtree could
  exhaust the VM thread/PID table (Bun `EAGAIN` crash).** Per-worker
  memory/PID limits were never enforced: the non-root orchestrator cannot
  enroll workers into cgroups or set controller limits (the kernel keeps
  limit files root-owned in delegated subtrees, and cross-scope task
  migration needs common-ancestor write the leerie user doesn't have —
  both reproduced live on Colima **and** Fly). The old direct-write probe
  passed while the actual per-worker enroll failed, so every worker ran
  uncapped. A conformer re-running backgrounded `pytest tests/` then
  accumulated threads until fork/thread-create failed for every worker.

  Fixed with a **root cgroup broker** (`scripts/cgroup-broker.py`),
  launched by `container-entry.sh` at PID 1 before the privilege drop; the
  orchestrator's `_cgroup_*` helpers drive it over a Unix socket. The
  broker performs create/enroll/destroy as root (the only level that
  works) and handles cgroup **v2** (Colima) and **v1/hybrid** (some Fly
  Firecracker VMs). A fail-closed gate
  (`enforce_and_record_cgroup_containment`) runs in `_run_phases` just
  before the first worker spawns — past the resume short-circuits, so an
  already-completed / no-work `--resume` (which spawns no workers) is not
  gated. It probes the broker end-to-end, records `{enforced, hierarchy}`
  into `state.json` (merged into the already-loaded run state), and
  `die()`s if containment can't be enabled — override with
  `--dangerously-allow-uncapped` / `LEERIE_DANGEROUSLY_ALLOW_UNCAPPED` /
  `dangerously_allow_uncapped` in `leerie.toml`. This also restores the
  per-worker memory (OOM) protection, which was off for the same reason.

  **Behavior change (rootless containerd on Linux):** rootless "root" is the
  unprivileged host user and cannot enforce cgroup containment, so rootless
  runs now `die()` at the fail-closed gate by default — previously they
  continued silently with uncapped workers. Pass `--dangerously-allow-uncapped`
  (or set the env/`leerie.toml` key) to run rootless without containment.

## [0.2.6] - 2026-06-01

This release fixes a hard failure on `--runtime fly` against any repo
whose tracked or submodule content contains non-ASCII filenames. Live
validation: clean preflight + bundle clone with intact NFC bytes on
`~/src/enric/api` (the `📄Plan de implementación.pdf` inside the
`data` submodule).

### Fixed

- **Seed transport for `--runtime fly` no longer corrupts UTF-8
  filenames in submodules.** macOS BSD `tar -c` normalizes filenames
  NFC → NFD when archiving (libarchive behavior). The Linux receiver
  on the Fly machine stored NFD bytes; git's index from macOS still
  held NFC bytes. Filenames with `ó`, `ñ`, emoji, etc. showed as
  untracked + missing on the machine; a single such file inside a
  submodule made the parent flag ` M`, failing preflight. Discovered
  on the api repo's `📄Plan de implementación.pdf` (NFC `c3 b3` on
  host → NFD `6f cc 81` on machine after BSD tar transport).

- **Repo-local `.claude/` correctly transported to the Fly
  machine when `.gitignore` excludes it.** The bundle-based seed
  introduced earlier in this release (commit `c68991e`) inadvertently
  dropped the force-include of gitignored `.claude/` that the prior
  tar pipeline did via `find .claude -print0`. Bundles only carry
  committed state; `seed_repo_dirty` only shipped `git status
  --porcelain` output (which omits gitignored entries). Net result
  before this follow-up fix: any leerie user with `.claude/` in
  `.gitignore` lost their repo-local hooks/agents/skills/commands on
  `--runtime fly`. Restored by walking `find .claude -type f`
  host-side in `seed_repo_dirty` and appending entries to the dirty
  file list (so the existing rsync delivers them). Shipped in commit
  `94cfbc7`.

- **`.leerie/` no longer leaks to the machine from `git status
  --porcelain` output.** The python filter in `seed_repo_dirty` was
  missing the equivalent of the prior pipeline's `f.startswith(b".leerie/")
  or f == b".leerie"` guard. Untracked `.leerie/` (the host-side run
  state, present on every `leerie` invocation) would have rsync'd to
  the machine on the dirty-delta step. Restored alongside the
  `.claude/` force-include (`94cfbc7`).

- **Test coverage restored**: `test_seed_repo_respects_gitignore_and_force_includes_claude`
  ports the prior `test_seed_repo_git_aware_excludes_and_includes`
  test forward to the bundle+rsync world (asserts gitignored
  `.claude/` IS shipped, gitignored build artifacts are NOT, `.leerie/`
  is NOT). Bundle-pipe assertions also strengthened to mechanically
  enforce the `sh -c '...'` wrapper and the `protocol.file.allow=always`
  flag (a revert of either would now break tests instead of only
  surfacing as a live-run failure).

### Changed

- **`scripts/remote/seed-repo.sh` `seed_repo_clone` rewritten** to use
  `git bundle` instead of `tar` for committed state. Bundles store
  pack-format binary objects; filenames are materialized natively on
  the Linux receiver from raw bytes in tree objects, with no
  transport-layer normalization possible. Two-phase seed:
  (1) host creates a `git bundle` for the parent and each submodule,
      pipes each to the machine via `flyctl ssh console -C "sh -c 'cat > ...'"`;
      machine `git clone`s from the parent bundle, wires each
      submodule's URL (in `.git/config`, NOT `.gitmodules`) to its
      respective bundle file, and runs `git -c protocol.file.allow=always
      submodule update --recursive` (the `protocol.file.allow` flag is
      required by git 2.38+ for file://-style submodule URLs per
      CVE-2022-39253).
  (2) host then rsync's the dirty/untracked delta plus the forced-in
      `.claude/` directory (same code path as `seed_repo_dirty` for
      `re-seed.sh`).

  Same `flyctl ssh console` pipe leerie already uses elsewhere (Claude
  home stage, `fetch-branch.sh`). The `cat > ...` receiver MUST be
  wrapped in `sh -c '...'`; bare `cat > ...` fails with `cat: invalid
  option -- 'c'` because flyctl's `-C` arg isn't shell-evaluated by
  default.

- **Dockerfile adds `rsync` to the base apt install layer.**
  `seed_repo_dirty` rsync's the dirty delta to the machine; without
  rsync installed, the remote `rsync --server` invocation fails with
  `executable file not found in $PATH`. ~1 MB image cost; comparable
  to `procps`/`build-essential`.

## [0.2.5] - 2026-06-01

This release makes `leerie --runtime fly` work end-to-end. Validated by
a live remote run on `enricai/tab-groups-focus` that opened
[PR #1](https://github.com/enricai/tab-groups-focus/pull/1). Three
themes: (1) catch up to current flyctl's interface, (2) make the
in-machine environment correct for the orchestrator + workers, and
(3) guarantee the user's work is never lost on machine teardown.

### Added

- **`sync_failed_at` + `sync_fail_reason` sidecar fields on
  `run.json`** — set by `decide_teardown`'s clean-exit branch when
  `fetch_branch` fails. Orthogonal to `paused_at`/`pushed_at`/
  `killed_at` (the machine is neither paused nor destroyed; it's
  running with un-synced work). Mutex-checked against `pushed_at`
  and `killed_at` by `_validate_run_json`. Surfaces in `leerie --list`
  as new derived status `sync-failed-running`.
- **`host_no_push` field on `fly-machine.json`** — captures the
  user's actual `--no-push` intent at launch time. Separate from
  `run.json.no_push` (which is the mechanism flag the launcher
  always forces on the in-Fly orchestrator, since the machine can't
  push). `leerie --finalize` consults `host_no_push` to decide whether
  to skip the push.
- **`leerie --finalize` already-synced fast-path** — when the host
  already has `run.json` with `finished_at`, `state.json`, and the
  local run branch (i.e. the auto-sync-on-teardown already ran),
  `--finalize` short-circuits past `fetch_branch` straight to
  `host_finalize`. This is the path the user hits after a clean
  remote-run completion; the Fly machine is already destroyed by
  then, so attempting to `fetch_branch` again would fail.
- **`seed-auth.sh` pre-warms `claude --version`** — the FIRST
  invocation of `claude --version` on a cold Fly machine takes
  ~17 s (Node + statsig cold start); subsequent calls return in
  <0.2 s. seed-auth runs it once as the leerie user immediately
  after credentials are in place, so the orchestrator's preflight
  call hits warm caches.

### Changed

- **Orchestrator source baked at `/opt/leerie-image/`** (was
  `/work/.leerie-image/`). Lives outside `/work` so the remote-seed
  phase can `rm -rf /work` (or empty-its-contents equivalent) freely
  without clobbering the orchestrator code. Local runs bind-mount
  `$LEERIE_REPO:/opt/leerie-image:ro` — same path inside the container
  in both modes.
- **Repo seeding is now single-channel git-aware**, not two-channel
  clone-plus-rsync. The host has the full repo and Fly machines
  deliberately receive no GitHub credentials (DESIGN §6
  *Finalization*: workers commit on the machine, the host pushes
  via `leerie --finalize`), so the right design is to ship the
  working tree directly. `seed_repo_clone` now tar-pipes a payload
  built from `git ls-files -z --cached --others --exclude-standard`
  (honors `.gitignore` at any depth) + `.git/` verbatim + the
  repo's local `.claude/` verbatim (force-included even if
  gitignored — workers need hooks/settings/plugins) − `.leerie/`
  always (host-side run state).
- **`decide_teardown` on clean exit (rc=0/10/75) now syncs before
  destroying.** Earlier behavior was destroy-then-rely-on-the-user-
  to-finalize, which lost work if `fetch_branch` later failed.
  New behavior: source `fetch-branch.sh`, run `fetch_branch` BEFORE
  `destroy_machine`. On sync success, destroy and print the
  finalize hint. On sync failure, leave the machine RUNNING (not
  stopped) with `sync_failed_at` written to the sidecar and a
  multi-line WARNING listing recovery commands (`leerie --finalize`
  to retry, `leerie --attach` to inspect, `leerie --kill` to destroy
  only after work is safely on host). Matches DESIGN §6's "destroy
  *after stream-back*" intent.
- **Container entrypoint idles when invoked with no argv.** Fly
  starts the entrypoint as PID 1; previously it tried to exec the
  orchestrator with no task and exited 1, crash-looping the
  machine. Now `cd /work && exec sleep infinity` when `$#` is 0;
  the launcher exec's the orchestrator separately via
  `flyctl ssh console -C "python3 -"`. Local nerdctl always
  passes argv, so the idle branch never fires there.
- **In-Fly orchestrator runs as the leerie user with explicit env.**
  The detached-launch wrapper passes `user="leerie"`,
  `group=<leerie gid>`, `env={HOME=/home/leerie, USER=leerie, PATH
  includes mise bin}`, `cwd="/work"` to `subprocess.Popen`. The
  hallpass ssh-console session lands as root with `HOME=/root`,
  which made claude look for credentials in the wrong place; this
  fixes that and ensures files created by the orchestrator are
  owned by leerie.
- **Claude home stage shrunk 642 MB → 234 MB** by adding
  `.claude/local` to `CLAUDE_SKIP` in the launcher. That directory
  is the host's npm install of `@anthropic-ai/claude-code` (~408
  MB), which the leerie image already installs globally via the
  Dockerfile — pure dead weight on the seed transfer.
- **`fetch_branch` discover step captures stderr to a separate
  tmpfile** rather than merging via `2>&1`. flyctl's "Connecting
  to fdaa:..." stderr was being parsed as the first stdout line,
  shifting the discovered run_id / branch / no_push by one and
  corrupting the bundle target. The runtime symptom was a silent
  "empty bundle" error against a non-existent branch.
- **`fetch_branch` skips the bundle by probing branch existence**
  (`git rev-parse --verify refs/heads/<branch>`) rather than by
  reading `run.json.no_push`. The latter is the mechanism flag the
  launcher forces on the in-Fly orchestrator (not a user-intent
  flag); using it as a proxy for "no branch was materialized"
  conflated the two cases. After tar-extracting the run state dir
  to the host, the mechanism flag is stripped from the host-side
  `run.json` so `host_finalize` doesn't conflate intent either.
- **`/work` is emptied via `find /work -mindepth 1 -maxdepth 1
  -exec rm -rf {} +`** rather than `rm -rf /work && mkdir -p /work`.
  The latter replaces the directory's inode; any process holding a
  prior fd (the ssh-console shell about to spawn the orchestrator)
  ends up with a stale cwd, producing `getcwd: ENOENT` cascades
  inside child processes.
- **All `flyctl machine exec ... -- argv` call sites migrated to
  `flyctl ssh console -C "<cmd>"`.** Current flyctl dropped both
  `--stdin` on `machine exec` and the post-`--` argv form. ssh
  console is the only flyctl transport that (a) takes the command
  as a single string and (b) forwards host stdin. Affects
  seed-auth, seed-repo, fetch-branch, re-seed, the launcher's
  detached-orchestrator launch, the handover-cat for bootstrap
  resolution, and the tail-wrapper stream.
- **`_check_claude_cli_version` timeout bumped 10 s → 30 s**
  (orchestrator preflight). Combined with the seed-auth pre-warm
  this is belt-and-braces — the warm cache makes the call return
  in <0.2 s, but the larger timeout protects against transient
  network slowness on the statsig call.

### Fixed

- **Force fresh image build by bumping leerie version from 0.2.1 to
  0.2.5** (skipping 0.2.2/0.2.3/0.2.4 because those tags were
  already consumed by Depot's tag→digest cache during development
  iteration). Fly's Depot remote builder caches the resolved
  tag-to-digest mapping per `--image-label` value. Rebuilds that
  push new layers but reuse the same label still resolve to the
  stale digest on the registry, so machines run the prior image.
  The community thread
  [flyctl deploy with --image-label deploys older code](https://community.fly.io/t/when-i-run-fly-deploy-with-the-image-label-flag-why-does-it-deploy-an-older-version-of-my-code/26151)
  documents the issue; the workaround Fly recommends is
  `--depot=false`, now applied in `scripts/remote/build-push.sh`'s
  remote-builder invocation. Belt-and-braces: the version bump
  alone forces a brand-new tag with no cached digest history.
- **Dockerfile: chown /work to leerie.** Local runs bind-mount
  `$(pwd):/work` which masks the image's `/work` permission; remote
  (Fly) runs use the baked image's `/work` directly. Previously
  `/work` was root-owned, so the orchestrator (running as `leerie`)
  crashed at `leerie_root.mkdir('/work/.leerie')` with `PermissionError`.
  Added `RUN mkdir -p /work && chown leerie:${HOST_GID} /work` before
  the `USER leerie` directive.
- **provision.sh: parse `flyctl machine run` text output, not
  `--json`.** `flyctl machine run` does NOT accept `--json`; the
  call exited non-zero with "unknown flag: --json", `machine_id`
  remained unset, and `set -u` killed the shell at the empty-check.
  Now parses `Machine ID: <id>` from text output via awk; defensively
  initializes `machine_id=""`.
- **wait_for_started / resume_machine / re_seed: parse `flyctl
  machine status` text output, not `--json`.** Same root cause as
  above (`flyctl machine status` doesn't accept `--json` either).
  Parses `State: <state>` from text via the same sed+awk pipeline.
- **`/work/.leerie`, `/work/.leerie/runs`, and the run dir are all
  chowned to leerie** by the detached-orchestrator launch wrapper.
  Previously only the deepest run dir was chowned; the orchestrator
  (as leerie) then failed when promoting a bootstrap-id dir to its
  final id with `PermissionError` against the root-owned parent.
- **`require_fly_ssh` skips `flyctl ssh issue` when the ssh-agent
  already has a Fly cert.** Detected via `ssh-add -l` showing any
  `(ED25519-CERT)` entry (regular SSH key authentication doesn't
  put certs in the agent — only Fly's hallpass does for this user).
  Survives Fly cert-API outages where the issue endpoint returns
  500 even though the existing cert is still valid for hours.
- **Hallpass cold-start wait (`wait_for_fly_ssh_ready`)** polls
  `flyctl ssh console --pty=false -C true` against the target
  machine until success (bounded by ~60 s). Without this, the
  first ssh-console call against a freshly-started machine would
  EOF with "handshake failed" — hallpass takes 5-30 s to come up
  after `flyctl machine start` reports "started".
- **Retry on transient `tunnel unavailable`.** A cold flyctl agent
  occasionally returns "Error: tunnel unavailable: Error contacting
  Fly.io API when probing 'personal': timed out (context deadline
  exceeded)" within ~7 s on its first ssh-console call.
  `flyctl agent restart` reliably clears it; the launcher does
  this once on the documented transient pattern, then retries.
- **`COPYFILE_DISABLE=1` on host-side `tar -c`** silences macOS BSD
  tar's `LIBARCHIVE.xattr.com.apple.provenance` warnings the remote
  GNU tar otherwise emits per-file. Cosmetic; no-op on Linux hosts.
- **Git identity written to `/home/leerie/.gitconfig`** (was
  `--global`, which under su-as-root would land in `/root/.gitconfig`
  where the leerie user can't read it).
- **`chown leerie:` (numeric primary group)** in seed-auth and
  seed-repo, not `chown leerie:leerie`. The leerie user's primary GID is
  numeric (HOST_GID, defaults to 20 / staff on macOS hosts) and is
  not necessarily a group literally named `leerie`.

### Added

- **Cleared-but-empty terminal state — leerie exits cleanly when every
  planner confirms "task already satisfied on HEAD".** A captured
  finalmemoriam run (`bugfix-fix-two-pre-existing-repo-b5b64a`) died
  at phase 3 with `leerie: error: planners produced no subtasks`
  immediately after all 3 planners (bug-fixing, testing,
  configuration-build) cleared their evidence gates and emitted
  `status: "ready"` with `subtasks: []`. The user had asked leerie to
  fix two defects, but commit `ff2e97f` (made before this leerie run
  started) had already shipped both fixes — and each planner
  independently verified this: the bug-fixing planner cited the
  StatCard binding diff, the testing planner found the regression
  test file, and the configuration-build planner re-ran
  `pnpm run typecheck` and confirmed exit 0. The planner prompt
  (`prompts/planner.md:205-208`) explicitly permits empty `subtasks`
  with `status: "ready"` — "an empty plan is a legitimate outcome of
  a cleared evidence gate" — but the orchestrator had no code branch
  to express that answer. `schedule()` (leerie.py:9548) ran an
  unconditional `die("planners produced no subtasks")` on any empty
  scheduling input, treating "task already done" identically to
  "everyone failed." This is **not** a regression from the recent
  reconciler work; `git blame` puts the offending die at
  `a388602` (May 26), before the conditional_drops + retry-loop
  features. Phase 2½ never even runs in this case because there are
  no subtasks to reconcile.

  Two new helpers in `orchestrator/leerie.py` add the missing branch.
  `detect_no_work(plans) -> dict[str, str] | None` is a pure
  predicate that returns a `{domain: confidence.basis}` map iff
  *every* plan satisfies `status == "ready"` and `subtasks == []`;
  it returns `None` on any mixed outcome (one or more domains have
  subtasks → normal run proceeds) and on any blocked-only outcome
  (the existing all-blocked die at leerie.py:9543 still fires so the
  user knows a gate failed). `_finish_no_work_run(st, no_work_map)`
  is the terminal handler: it logs the no-work summary with each
  planner's basis quoted, records `no_work_required=True` +
  `no_work_reasons={domain: basis}` in state.json, writes
  `finished_at` to state.json + run.json (with `no_push=True`
  propagated so the launcher knows there's no branch to push), sets
  `current_phase = "done: no work required"`, and emits the same
  `run weight: …` telemetry line as `phase_finalize` so the user
  sees what the run cost (classifier + N planners is non-trivial).
  The call site sits in `_run_phases` between `phase_reconcile` and
  `warn_cross_planner_file_overlap` / `filter_offtree_subtasks` /
  `schedule()` — after the reconciler so a planner that emits empty
  subtasks but the reconciler later adds connector subtasks is not
  misclassified as no-work, and before scheduling so the bare die
  at leerie.py:9548 is unreachable on the happy path (it remains as a
  defensive backstop for direct `schedule()` callers, e.g. tests).
  When `detect_no_work` fires, `_run_phases` returns early — phases
  4–6 are skipped entirely; no run branch is materialized, no
  `setup-run.sh` is called, no PR is opened.

  Two new `STATE_FIELDS` entries (`no_work_required`,
  `no_work_reasons`) record the outcome for `leerie --list` and any
  downstream tooling that cares. No new terminal status in
  `RUN_STATUSES` — `_derive_run_status` already keys "done" off
  `finished_at`, so a no-work run renders as `done-local` (no push,
  no PR — distinct from `done-pushed-no-pr` / `done-pushed-pr`).
  Adding a 9th status would have forced churn across the table
  formatter, every `_derive_run_status` test, and the launcher;
  reusing the existing derivation keeps the change small.

  The launcher (`leerie` bash script) is patched to honor
  `run.json.no_push` after the container exits. The launcher's own
  `NO_PUSH` variable comes from CLI/env (`--no-push` /
  `LEERIE_NO_PUSH`) — for the normal-finalize case both container and
  launcher see the same CLI flag so no propagation is needed. The
  no-work case breaks that symmetry: the container short-circuits
  via "task already done," not via "user opted out of push." Without
  the launcher patch, the launcher would try `git push -u origin
  leerie/runs/<run-id>` on a branch that was never created, fail with
  "src refspec does not match any," write `push_error` to run.json,
  and `leerie --list` would render the run as `push-failed` instead of
  `done-local`. The patch reads `jq '.no_push // false' run.json`
  and exits 0 with a skip message before the push step (and before
  the empty-branch guard, since a no-push run may legitimately have
  no branch info). The same path now also handles any future
  container-side short-circuit that wants to opt out of push.

  Resume-of-finished-no-work-run gets its own guard at the top of
  `_run_phases`'s resume branch: if `st.data.get("no_work_required")`,
  log "already completed with no work required at …; nothing to
  resume" and `return`. Without it, the resume branch would fall
  through to `phase_execute` (running `setup-run.sh` and creating a
  fresh empty run branch), then `phase_finalize` → `finalize.sh`
  would fail its non-empty-branch check.

  Three-layer documentation: DESIGN.md §8 *The planner gate* gains a
  new paragraph *The cleared-but-empty terminal state* describing
  the contract symmetrically with the existing blocked-exit
  paragraph. IMPLEMENTATION.md's phase 3 row enumerates
  `detect_no_work` as the first step; the planner schema description
  notes that `ready` plans may legitimately carry an empty subtasks
  array; the state.json field table adds rows for the two new
  fields.

  Verified by 1606 tests pass (was 1589 pre-feature; +17 net: 5
  `detect_no_work` unit tests, 5 `_finish_no_work_run` integration
  tests including the telemetry-line and stale-fields guards, 5
  launcher bash-harness tests for the `no_push` honoring, and 2
  source-text coupling tests pinning the new call site and the
  resume guard).

- **Reconciler gains an eighth action: `conditional_drops` for
  planner-declared conditional subtasks.** A captured summarizer run
  (`deps-we-need-to-migrate-this-repo-787f7a`) died at phase 2½ with
  `deps-004 requires 'email-provider-is-ses'` left unresolvable —
  the planner had emitted `deps-004` as a *conditional* subtask
  with `intent: "Add the SES client only if the email-delivery
  migration replaces the current Resend provider with Amazon SES;
  otherwise this subtask is a no-op the orchestrator can drop."` and
  used `requires: [{tag: email-provider-is-ses, extent: in_plan}]`
  as a runtime gate. No subtask in any planner-domain produced that
  tag (this repo keeps Resend), so the reconciler correctly emitted
  `unresolvable` — and `_check_unresolvable` (leerie.py) fail-closed
  the run. The reconciler's `unresolvable.reason` *quoted* the
  planner's own "no-op the orchestrator should drop" language
  verbatim: the model knew the right answer was "drop the consumer
  subtask" but had no operation in its vocabulary to express that.
  The capability graph has no semantics for conditional subtasks.

  The reconciler's action vocabulary expands to eight arrays — four
  *resolution* (renames / added_provides / added_subtasks /
  conditional_drops), three *cycle-breaking* (dropped_requires /
  dependency_edges / merged_subtasks), and one *escape hatch*
  (unresolvable). `conditional_drops` is restricted to planner-
  authored consumers (the apply step `die()`s if the target carries
  `_added_by_reconciler: true` — a reconciler-added subtask has no
  planner prose to convert into a structured drop). When the model
  uses it, `_apply_reconciler_output` removes the named sid from its
  plan, prunes downstream `depends_on` references, and the audit
  trail (sid → `{reason, from_unresolved_tag}`) lands in
  `st.data["conditional_drops"]` — distinct field from
  `st.data["dropped_subtasks"]` (off-tree soft drops from phase 3)
  so the two soft-drop causes stay separately auditable.

  `unresolvable` is now reserved for *unconditional* consumers whose
  required capability genuinely cannot be produced; planner-declared
  conditional consumers route through `conditional_drops` instead.
  The unresolved-retry must-include validator
  (`_validate_unresolved_must_include`) accepts `conditional_drops`
  on the consumer sid as a fifth legal addressing op alongside
  rename/added_provides/added_subtask/unresolvable. Apply order
  positions `conditional_drops` after `added_subtasks` (so the
  `_added_by_reconciler` guard catches reconciler-targeted drops)
  and before the cycle-breaking ops (so they see the post-drop
  graph and their missing-id guards fire correctly on
  `depends_on`/`merge` to a dropped sid).

  The cycle gate is unchanged: `conditional_drops` is *not* a
  cycle-breaking op, so the cycle-retry's bounded set stays at
  `dropped_requires / dependency_edges / merged_subtasks`. The model
  picks `conditional_drops` from prose signals (the consumer's own
  `intent`/`scope_note` admitting conditional emission) — no
  structural recommender, because reading prose is the model's job
  not the code's, per CLAUDE.md's central principle.

  Verified by n=3-vs-n=3 replay against the captured deps-004
  failure (using captured prompts + the live production
  reconciler.md + SCHEMAS["reconciler"]): baseline reproduces the
  failure deterministically (3/3 emit `unresolvable: [deps-004]`);
  patched resolves cleanly (3/3 emit `conditional_drops: [deps-004]`
  with `unresolvable: []`, reasoning that quotes the planner's
  conditional language AND the structural ground that feat-010
  keeps Resend). Total cost ~$1.85, ~3 minutes wall-clock. 7 new
  tests in `tests/test_reconciler_cycle_gate.py` cover the apply
  branch (removal + depends_on pruning, silent no-op on unknown sid,
  die() on reconciler-added target, summarizer deps-004 replay
  shape, must-include validator accept/reject, `_check_unresolvable`
  precedence over conditional_drops), plus an added
  `conditional_drops` shape test and updated all-arrays-empty
  fixture in `tests/test_reconciler_schema.py`.

- **Reconciler now retries on unresolved-requires after the first
  attempt, symmetric with the cycle-resolution retry.** A captured
  summarizer run (`deps-we-need-to-migrate-this-repo-075210`) died
  at phase 2½ with `deps-008 requires 'cdk-stacks-authored'` left
  unresolved — the reconciler had invented a connector providing
  `infra-stacks-authored` (a synonym) and forgot to rename deps-008's
  tag to match. The cycle-resolution work didn't help (the failure
  isn't a cycle). Phase 2½ now wraps the post-mutation
  `_compute_unresolved_requires` check in a retry loop: on first
  detection, deep-copy the pre-mutation plans, compute a
  string-similarity recommendation per unresolved entry (Jaccard over
  hyphen-tokens, with a self-loop guard skipping candidates provided
  by the consumer's own sid), build a retry prompt surfacing the
  top-3 candidate provides + the recommendation as a *hint* (string
  similarity can produce false friends — narrow synonyms for broader
  concepts), respawn the reconciler once. The retry's must-include
  validator accepts `rename`/`added_provides`/`added_subtasks`/
  `unresolvable` per unresolved entry; a post-retry cycle gate
  re-runs to catch reintroduced cycles. Verified in plan-mode against
  the captured failure: the Jaccard heuristic produces the correct
  recommendation (`rename(sid='deps-008',
  from='cdk-stacks-authored', to='infra-stacks-authored')`)
  with no model judgment required.
  Historical scan across 3 runs surfaced two real defects that the
  design now addresses: a self-loop trap (the heuristic would
  recommend renames TO tags the consumer itself provides), and
  textual-vs-semantic mismatch (j≥0.5 candidates that are narrower
  synonyms than the model's correct choice). Both mitigated: guard
  for the first, softened "hint" framing for the second. 11 new
  tests cover the Jaccard function, recommendation heuristic
  (075210 case, self-loop guard, no-match, multi-strong-abstain),
  must-include validator (4 acceptance shapes + 1 rejection), retry
  prompt builder.

- **Reconciler now resolves dependency cycles instead of letting them
  crash the scheduler.** Previously, when the reconciler's renames
  closed a cross-domain dependency cycle (each rename locally correct,
  jointly cycle-creating), the failure surfaced two phases later as
  `leerie: error: dependency cycle among subtasks: <29 of 41 sorted
  ids>` from `schedule()`'s Kahn pass — Tarjan never ran, edges were
  never attributed, the user got noise. Two captured failures on a
  real run (`~/src/enric/summarizer/.leerie/runs/`) made this concrete:
  run 2's cycle was a 2-node SCC `config-005 ↔ feat-001` (both edges
  reconciler renames); run 1's was `feat-008 ↔ feat-009` (one
  planner-declared `depends_on` + one reconciler rename). The
  reconciler's action vocabulary expanded by three cycle-breaking
  ops (`dropped_requires`, `dependency_edges`, `merged_subtasks`),
  bringing the schema to seven arrays total (three resolution + three
  cycle-breaking + one `unresolvable` escape hatch). Phase 2½ now runs
  Tarjan's SCC over the post-mutation graph, and on cycle detection
  reverts the mutations (deep-copy snapshot), computes a deterministic
  recommendation per SCC from structural signals (planner-declared
  `depends_on` direction; `files_likely_touched` overlap), and
  respawns the reconciler once with a structured retry prompt naming
  the SCC, offending mutations, recommendation, and bounded
  must-include set of legal cycle-breaking ops. The model never has
  to detect cycles itself — leerie does that in Python and hands back
  structured feedback — which sidesteps the captured reconciler
  showing zero spontaneous cycle-awareness across 10k chars of
  reasoning. If attempt 2 still cycles, the run aborts with the SCC
  + offending mutations enumerated (informative, not the prior
  29-of-41-sids dump). New helpers: `_tarjan_sccs`,
  `_attribute_cycle_edges`, `_shared_files_in_scc`,
  `_format_cycle_diagnostic`, `_recommend_cycle_resolution`,
  `_format_recommendation`, `_format_must_include`,
  `_build_cycle_retry_prompt`, `_matches_recommendation`,
  `_validate_must_include`. Shared `_build_predecessor_graph`
  extracted from `schedule()` so the gate and scheduler cannot
  drift in what counts as an edge. Reconciler payload now includes
  `depends_on` and `files_likely_touched` per subtask (the model
  needs them to reason about ordering and merge candidates).
  A comprehensive test corpus in
  `tests/test_reconciler_cycle_gate.py` covers both captured runs
  verbatim, 3- and 4-node synthetic cycles, connector cycles, all
  three apply-step ops with edge cases (extent-preservation, chain
  merges, override fields, self-loop fail-loud), recommendation
  correctness on all four heuristic cases (planner-edge keeper,
  shared-files merge, speculative-rename drop, lexicographic
  tiebreaker), direct unit tests for the two render functions
  (`_format_recommendation`, `_matches_recommendation`),
  must-include validation including a negative case (op on a
  non-SCC sid does not credit the cycle), post-retry cycle
  detection, mutation reversion cleanness, and 5 regression
  fixtures from successful historical runs (false-positive guard
  — the gate is silent on every known-acyclic plan in the
  cross-repo canvass: centella feat-rebrand, barnacle telemetry,
  navegando bugfix, leerie feat-please-read, finalmemoriam bugfix).

### Changed

- **`max_parallel` default lowered from 4 to 2.** Subprocess fan-out
  *inside* each `claude -p` worker is unbounded — the Bash tool,
  the Task background-job pattern, and toolchain children like
  vitest pools / webpack workers / `tsc` are all uncapped from
  leerie's view. The orchestrator-side knob that bounds total
  in-flight memory load is the worker count itself. At
  `max_parallel=4`, a typical Next.js repo can run 3+ concurrent
  Node toolchain processes (each 1-2 GiB RSS) before leerie even
  notices — exactly the load profile that OOM'd the
  finalmemoriam run. Lowering to 2 keeps the worst-case peak
  within reach of a 16 GiB VM. Users with larger VMs or lighter
  toolchains can opt up via `--max-parallel`. Pairs with the
  cgroup containment shipped in the same release.

### Added

- **Per-worker cgroup v2 memory containment.** Each `claude -p`
  worker is now enrolled in its own child cgroup at
  `/sys/fs/cgroup/leerie-w-<sid>/` with `memory.max`,
  `memory.swap.max=0`, and `pids.max` set. When a worker's tool
  subtree (vitest pools, `tsc --noEmit`, `npm run build`'s
  webpack workers, etc.) overshoots its memory budget, the kernel
  OOM-kills inside the worker's cgroup — sibling workers, the
  orchestrator, and host-side services (`sshd`,
  `lima-guestagent`) in different cgroups are not eligible
  victims. Fixes the OOM-cascade failure mode observed in a
  finalmemoriam run where four concurrent implementers ran 3
  heavy Node toolchain processes simultaneously, blew through
  the 11 GiB Colima VM, took out sshd, and surfaced as
  `FATA[NNNN] exit status 255` on the Mac launcher with no
  orchestrator-side diagnostic. Leerie's own RSS sat at 36.8 MiB
  through the whole event (no orchestrator leak); the cascade
  was purely due to all processes sharing one container memcg.
  Mechanism is purely cgroup v2 file-permission delegation — no
  `--privileged`, no `--cap-add`, no `systemd-run`. The launcher
  gains one mount flag
  (`--mount type=bind,source=/sys/fs/cgroup,
   target=/sys/fs/cgroup,bind-propagation=rshared`); the
  orchestrator's `_cgroup_probe` verifies delegation at startup
  and falls back to uncapped behavior with one warn-line if
  unavailable (older launcher, kernel < 5.x). New CLI knob
  `--worker-memory-max SIZE` (also `LEERIE_WORKER_MEMORY_MAX` env
  and `worker_memory_max` in `leerie.toml`); when unset, auto-
  derives a per-worker cap from `/proc/meminfo` by splitting VM
  RAM across `max_parallel + 1` slots, clamped to ≤ 4 GiB.
  Suffixes K/M/G/T accepted. New telemetry field
  `cgroup_applied` (bool) on every `calls.ndjson` record so a
  post-mortem can confirm containment was active.

### Fixed

- **Memory sampler re-resolves `st.run_dir` on every tick.** The
  sampler previously captured `out = st.run_dir / "memory.ndjson"`
  once outside its tick loop, which could silently no-op if
  `st.run_dir` ever changed. Fix moves the path resolution inside
  the loop. The run_id is now the container/machine ID (stable from
  creation, no rename), so the original bug cannot recur — but the
  defensive re-resolution is kept as defense-in-depth.

### Added

- **Orchestrator memory-leak telemetry: `.leerie/runs/<id>/memory.ndjson`.**
  After three OOM cascades across different parallel-run combinations,
  we narrowed the diagnostic question to "is the orchestrator itself
  leaking, or is a 2+ GB peak just the natural shape of a heavy 24-
  subtask run?" Rather than guessing — bumping Colima further, adding a
  run-lock, or launching a `tracemalloc` hunt blind — we ship the
  measurement first. A new background coroutine `_memory_sampler`
  writes one ndjson line per ~30 s to `memory.ndjson` under each run
  directory while `orchestrate()` is alive. Each sample records four
  axes: `rss_kb` (from `resource.getrusage(RUSAGE_SELF).ru_maxrss`),
  `phase` (a new `current_phase` field on `state.json`, set at each
  phase entry), `worker_count` (already tracked), `open_fds` (from
  `/proc/self/fd`), and `thread_count` (from
  `threading.active_count()`). The sampler is exception-swallowing so a
  telemetry bug cannot crash the orchestrator, and the cancellation
  path writes one final sample so the file captures last-known state
  at exit. After a fresh run, a few lines of `python3 -c "import json;
  …"` over `memory.ndjson` distinguishes the three diagnostic outcomes:
  flat RSS (no leak — bump Colima or add a run-lock), linear growth
  (real leak — escalate to `tracemalloc` localized to whichever phase
  the by-phase breakdown implicates), or step-function growth tied to
  `worker_count` (subprocess-handle leak — audit `_invoke`'s cleanup
  paths). At ~50 bytes/line × 30 s intervals a 4-hour run produces
  ~25 KB, so we leave the file unbounded.

### Fixed

- **Installer provisions Colima with 4 GB of swap on fresh installs.**
  The auto-sizing fix that landed earlier in this release raised the
  Colima VM's RAM but didn't add swap — with `Swap: 0B` the kernel's
  OOM killer fires immediately when RAM is exhausted, with no
  breathing room for transient spikes. Under leerie's parallel-
  implementer workload (concurrent `claude -p` plus Vitest workers
  spiking to 2 GB RSS each plus `tsc`/`pnpm` toolchain overhead) we
  observed the OOM killer hitting the host-side `nerdctl` /
  `lima-guestagent` daemons inside the VM, which collapses the Mac
  launcher's connection and surfaces as `FATA[NNNN] exit status 255`
  with no orchestrator diagnostic. The installer now writes an
  idempotent `provision:` block to `~/.colima/default/colima.yaml`
  on fresh installs (no existing config) — the block uses sentinel
  markers (`# leerie:swap-provision-v1 BEGIN/END`) so re-runs are
  no-ops. The provision script `fallocate`s `/var/swapfile`,
  `mkswap`s it, `swapon`s it, and sets `vm.swappiness=10` so the
  kernel uses swap only under real pressure (default 60 is too
  eager for our safety-net use). For users with an existing
  `colima.yaml`, the installer deliberately does NOT mutate it —
  too risky to clobber custom mounts / CPU type / disk size —
  instead it logs a one-line hint with the exact YAML block to
  paste in plus `colima stop && colima start`. The 4 GB swapfile
  persists on Colima's VM disk across `colima stop/start`; only
  `colima delete` removes it (and the next start re-creates it
  via the provision script). See
  `_runtime_colima_swap_yaml` in `scripts/runtime-install.sh` for
  the authoritative YAML and `docs/INSTALL.md` "Memory pressure:
  swap configuration" for the user-facing docs.

- **Installer auto-sizes the Colima VM to half the host's CPU/RAM
  instead of using Colima's 2-CPU / 2-GB defaults.** The 2/2 defaults
  were not enough for parallel leerie runs — concurrent `claude -p`
  workers (~300 MB each) plus toolchain processes (`tsc`, `vitest`,
  etc.) blew through 2 GB in minutes, triggering a kernel OOM in the
  Colima VM. The OOM killer hit the host-side `nerdctl` daemons (not
  the container's PID 1), so the failure manifested on the Mac
  launcher as `exit 255` with no orchestrator diagnostic — the
  container's stdout just stopped mid-stream. The installer now
  detects host resources via `sysctl hw.ncpu` / `hw.memsize` and
  starts Colima with `--cpu N --memory M` sized at half-of-host,
  clamped to CPU 2..8 and RAM 4..16 GB. The Linux path is untouched
  (Linux runs containerd natively, no VM to size). Already-running
  VMs are left alone, but a one-line hint is logged if the current
  sizing is below the auto-recommendation. See
  `_runtime_colima_size_flags` in `scripts/runtime-install.sh` for
  the bounds rationale.

- **Per-container Claude config isolation eliminates the silent-hang
  race.** Concurrent leerie containers used to share a single host
  `~/.claude.json` via bind mount, which exposed the well-documented
  `claude-code` corruption race (anthropics/claude-code issues #28847,
  #29217, #29395, #40226 — all open). When the file went missing
  mid-rewrite, the CLI entered a "recovery loop with no backoff" —
  `claude -p` never exited, leerie's existing retry path never fired,
  and the worker hung silently until the 90-min hard kill. The
  launcher now stages a per-run scratch dir on the host with a
  private copy of `~/.claude.json` (with `projects[]` stripped),
  `~/.claude/` (with bulky / prior-session paths blacklisted), all
  present `~/.git*` siblings, `~/.config/git/`, `~/.netrc`, `~/.ssh/`
  (sockets filtered), and `~/.gnupg/` (sockets filtered). Each piece
  mounts at its default in-container path so the CLI and git see
  normal locations with private contents — no shared host state to
  race on, no env-var redirection needed. On macOS the OAuth token
  is now extracted from Keychain (`security find-generic-password`)
  and written to the staged `~/.claude/.credentials.json` — the same
  file-based path the Linux CLI reads — so authentication works
  identically on both platforms without an env-var bridge. The host
  scratch dir is reaped on container exit; container-side writes
  (`numStartups++`, new session transcripts) are intentionally lost.
- **Live stderr streaming surfaces worker failures in seconds, not
  minutes.** `_invoke()`'s `_drain_stderr` used to silently buffer
  every stderr byte into an in-memory list and surface it only on
  exit (or when the idle watchdog flushed the last ~40 KB at 300 s).
  When `claude -p` hit the recovery loop above, its repeated "Claude
  configuration file not found" stderr lines were invisible to the
  user for the full 5 minutes before the watchdog fired. Stderr now
  streams line-by-line to the per-sid log file (with a `[ts] stderr`
  header) and echoes to the orchestrator log at `stream` / `debug`
  verbosity. Stderr activity also refreshes the watchdog clock, so a
  worker that emits only stderr (recovery-loop scenarios) doesn't
  falsely trip the idle watchdog. `stderr_chunks` is still populated
  for the existing exit-time `WorkerError` message at `leerie.py:4195`.

- **Provisioning no longer mutates the host's repo.** Phase 1½ used
  to execute `pnpm install` / `pip install` / etc. against
  `repo_root`, which is bind-mounted from the host. On
  darwin-host + linux-container setups (the common Colima case)
  this clobbered the host's `node_modules` with linux-arm64 native
  binaries — host `pnpm dev` would then crash with
  "wrong architecture" until the developer ran `pnpm install` on
  the host again to restore darwin-arm64 binaries. Phase 1½ now
  only *detects* the install recipe; each worker runs the install
  in its own worktree against the shared package-manager cache
  (DESIGN §6½ "Worker-driven install"). Side effects: the
  `replay_provision_in_worktree` function and its
  `wrap_with_mise_exec` helper are removed; the recipe is now
  injected into implementer and conformer prompts as a
  `PROVISION_RECIPE:` advisory block.
- **pnpm store cache mount was inert.** The launcher exported
  `PNPM_STORE_PATH=/home/leerie/.cache/leerie/pnpm-store`, but that
  env var does not exist in pnpm — pnpm reads `npm_config_store_dir`
  (env), `store-dir` (`.npmrc`), or `--store-dir` (CLI). pnpm
  silently fell back to its default
  (`/home/leerie/.local/share/pnpm/store`), which was NOT
  bind-mounted, so every container run paid full registry cost on
  every package. Fixed by setting `npm_config_store_dir` instead.
  The host cache (`~/.cache/leerie/pnpm-store`) now warms across
  runs as intended.
- **`mise install` and `.leerie-setup.sh` no longer hang silently.**
  Both ran through `run_proc` which buffered stdout/stderr until
  the process exited — on a first-run Python 3.12 / Ruby 3.2 / Rust
  install that meant the user could stare at one log line for
  10+ minutes before seeing anything. A new `run_streaming()`
  helper (next to `run_proc`) streams output line-by-line to both
  the terminal and the persistent log, keeps a bounded tail for
  error reporting, and on timeout populates `TimeoutExpired.output`
  with the captured tail so callers can include it in their
  diagnostic.

## [0.2.1] - 2026-05-29

### Fixed

- **`/home/leerie` is now writable by the runtime user inside the
  container.** Observed images had `/home/leerie` owned by `root:root`
  (despite `useradd -m -u $HOST_UID -g $HOST_GID leerie` having run),
  which meant the runtime `leerie` user couldn't create any new dotfile
  under its own `$HOME`. The visible failure was `gpg: Fatal: can't
  create directory '/home/leerie/.gnupg': Permission denied` during
  `phase 1½` when mise tried to verify a Node download. The
  Dockerfile now explicitly `chown leerie:${HOST_GID} /home/leerie`,
  pre-creates `/home/leerie/.gnupg` at mode 0700 (which GPG requires),
  and chowns it to the leerie user. Other dotfile-writing tools (npm,
  ssh known_hosts, cargo, etc.) also benefit. Version bumped to
  0.2.1 to force a rebuild of the cached image (the launcher's
  image-presence check would otherwise skip the rebuild).

### Changed

- **Finalize moved to the host launcher.** `git push` and `gh pr create`
  now run on the host after the container exits cleanly, not inside the
  container. Removes the entire bind-mount of `~/.config/gh`,
  `~/.git-credentials`, `~/.ssh`, and `$SSH_AUTH_SOCK` — those auth
  states live in host processes (Keychain, ssh-agent under launchd,
  gh's local token store) that don't cross the Lima/Colima VM boundary
  cleanly on macOS. The container's job is the LLM work + deterministic
  integration into `leerie/runs/<run-id>`; the host's job is everything
  network-y, using its own working auth. Side effects: the macOS-only
  "SSH agent forwarding is not available" note is gone (irrelevant
  now); `gh auth status` runs as a host preflight before the container
  starts (fast-fails in milliseconds, not after a 60-second cold
  container launch); SSH push works on macOS via the host's
  `ssh-agent`; the `_check_gh_cli` and `push_and_open_pr` Python
  functions and the in-container cwd-is-git-repo check are removed.
  `compose_pr_body` is kept as the canonical reference for the PR body
  shape; the launcher reimplements its body composition in bash + jq.
  New host dependency: `jq` (brew/apt/dnf/pacman). DESIGN §6
  *Finalization* and IMPLEMENTATION §0.5 + §7 updated.

- **Auto-install the container runtime on first run.** If Colima
  (macOS) or nerdctl (Linux) is missing when the launcher runs, the
  launcher now installs it instead of erroring out with a hint.
  Behavior mirrors `scripts/install.sh` exactly: `brew install colima`
  + `colima start --runtime containerd --mount-type virtiofs` on
  macOS; distro-appropriate `apt-get`/`dnf`/`pacman` + pinned upstream
  nerdctl binary on Linux. A new shared helper at
  `scripts/runtime-install.sh` defines the install functions; both
  `install.sh` and the `leerie` launcher source it (DRY). Opt-out via
  `--no-runtime-install` (CLI) or `LEERIE_NO_RUNTIME_INSTALL=1` (env) —
  same flag/env as the installer. TTY-guarded: when stdin is not a
  terminal (Claude Code plugin mode), the launcher prints a clear
  "run from a terminal once" message instead of hanging on a sudo
  prompt. `print_install_hint` gains a brew-detection branch on macOS
  so users without Homebrew get the right two-step path
  (install brew → re-run leerie).

### Added

- **Per-repo dependency provisioning — Phase 1½** (DESIGN §6½). The
  orchestrator now installs each target repo's dependencies (and
  selects the right runtime versions) inside the container before any
  worker runs. Five layered steps: (1) optional `.leerie-setup.sh` hook
  for user-space tooling install (additional `mise install
  <lang>@<version>` for languages beyond the LTS bake, CLI tools into
  `~/.local/bin`, pre-populated fixtures) — runs as the non-root
  `leerie` user, so root-level system packages need a forked Dockerfile;
  (2) **`mise`** resolves runtime versions
  from `.nvmrc` / `.python-version` / `.tool-versions` /
  `rust-toolchain.toml` (image-set
  `MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS` flips the opt-in), with
  `.go-version` synthesized from `go.mod` via
  `MISE_OVERRIDE_CONFIG_FILENAMES` — and because that env var REPLACES
  rather than merges discovery, leerie copies any idiomatic-file pins
  forward into the synthesized override so polyglot repos (e.g. Go +
  `.nvmrc`) don't silently drop their non-Go pins; (3) a deterministic
  **lockfile-detection table** emits install commands (pnpm > yarn >
  npm precedence; uv > poetry > pipenv; Go modules, Cargo, Bundler);
  polyglot repos like Rails-with-frontend emit **all** matching
  commands, not just the first match; (4) a `provision` LLM worker
  fires only when the table abstains (Java/Gradle, bare
  `pyproject.toml`, polyglot Makefile) and is structurally bounded by
  a schema + argv allowlist (the one documented §12 carve-out, see
  DESIGN §6½); (5) **per-worktree replay** via `mise exec --` so each
  fresh worktree's implementer sees the same toolchain.
- **Image-baked LTS fallbacks via `mise install --system`.** Node LTS
  and Python 3.12 land at `/usr/local/share/mise/installs/` so repos
  that declare no version still get a predictable runtime. The
  resolver checks the per-run user dir first then falls through to
  the system layer (verified against
  https://mise.jdx.dev/mise-cookbook/docker.html). A repo with zero
  version pins (no `mise.toml`, no idiomatic file, no synthesized
  override) skips `mise install` entirely and runs directly on the
  image-baked LTS — avoids depending on mise's implementation-defined
  behavior when no tools are declared.
- **Five host-side caches** mounted into the container — `mise-data`
  (so a Node 20.11.0 install survives across runs), `pnpm-store`,
  `pip` cache, `GOMODCACHE`, and the whole `CARGO_HOME`. Concurrency
  safety verdicts and the pip warm-once-then-replay pattern that
  sidesteps pypa/pip#9034 are documented in IMPLEMENTATION §6½.
- **`.leerie-setup.sh`** at the repo root is the user-space escape
  hatch the language layer can't install — `mise install
  <lang>@<version>` for additional runtimes, CLI tools under
  `~/.local/bin`, fixture pre-population. Runs as the non-root `leerie`
  user once per fresh run before mise; idempotent via state. Root-
  level system packages (anything needing `apt-get install` or
  writes to `/usr/*`) are out of scope: the container intentionally
  ships no sudo. Workaround: maintain a fork of the leerie Dockerfile
  and override `IMAGE_TAG`.
- **New `provision` worker type** (defaults to Opus). Independently
  overridable via `--model-provision` / `LEERIE_MODEL_PROVISION` /
  `model_provision` in `leerie.toml` like every other worker.

### Fixed

- **Claude Code auth now works inside the container on macOS.** Claude
  Code stores its OAuth token in macOS Keychain (an IPC service the
  container can't reach), not in the bind-mounted `~/.claude/` files —
  so `claude -p` inside the container failed preflight with "Not logged
  in" even when the host was logged in. The launcher now forwards
  `CLAUDE_CODE_OAUTH_TOKEN` to the container when it's set in the
  invoking shell, with explicit `=value` form. On macOS, if the var is
  unset, the launcher prints a one-line note with the
  `security find-generic-password` extraction command. On Linux native
  the file-based `~/.claude/credentials.json` continues to ride the
  existing bind mount; no behavior change. Note: the previous attempt
  used the bare `-e VAR` pass-through form (no `=value`), which works
  under Docker but does NOT work under Colima/nerdctl — the container
  receives an empty string. The fix expands the value at launcher exec
  time, accepting a brief `ps -ef` argv-visibility window (single-user
  macOS dev box is the supported trust domain; multi-user host is out
  of scope, same as the existing `~/.claude/` bind mount).

- **Worker timeout no longer dumps a 50-KB traceback.** When a worker
  hit `worker_timeout_sec` (default 5400s / 90 min), `_invoke` raised
  `subprocess.TimeoutExpired` which escaped `run_implementer`'s
  `except WorkerError` catch and bubbled all the way to `main()`'s
  catch-all, dumping the entire `claude -p` command line as a Python
  traceback to the user's terminal. `run_implementer` now catches
  `subprocess.TimeoutExpired` and returns an `incomplete-handoff`
  envelope (same shape as the existing WorkerError path), so the
  timeout becomes a routine handoff that `--resume` picks up cleanly.
  `run_conformer` gets the same shield — a timed-out conformer
  becomes a logged warning + returns None, matching the existing
  WorkerError advisory-phase semantics. Observed three times in real
  runs on 2026-05-28 (stackpulse × 2, navegando × 1) before the fix.

- **Worktree-removal timeout raised 30s → 240s.** A real worker that
  ran `npm install` left a 868 MB / 41k-file worktree; `git worktree
  remove --force` did `rm -rf` on it, which took longer than the 30s
  cap. The new 240s value is calibrated against that worktree
  (~45-90s uncontested) with margin for N-way concurrent disk
  contention (a six-worktree wave was observed timing out
  concurrently). Still bounded so a genuinely hung git command
  doesn't block cleanup indefinitely. Per-worktree failures are
  still non-fatal, and the cleanup now emits a closing recovery
  hint when any removal timed out: `cleanup: N worktree(s) not
  removed within 240s — run scripts/cleanup.sh --run-id <id> to
  finish manually`.

- **`--resume` disambiguation now shows status + last-activity.**
  When multiple in-flight runs exist in the same repo, the previous
  error message listed only `run_id  (started <iso-timestamp>)` — no
  hint which run was alive. Each row now includes `status=<derived>`
  (from the same `_derive_run_status` `leerie --list` uses) and
  `last-activity=<age>` (humanized state.json mtime: e.g. `12s ago`,
  `2h05m ago`, `1d4h ago`). Zero new shell-outs — both signals come
  from data already in scope. The disambiguation stays a hint, not
  an auto-pick; user still passes `--run-id`.

### Changed

- **Leerie now runs inside a container per run.** Cleanup of `claude -p`
  workers + every test runner / build / dev server they spawned is now
  a Linux PID-namespace teardown rather than a heuristic PPID-walk in
  Python. Ctrl-C reliably reaps everything; SIGKILL or hard crashes do
  too (cgroup release is a kernel guarantee, not a Python signal
  handler). New host requirement: a container runtime — Colima on
  macOS, containerd + nerdctl natively on Linux. Setup per OS:
  `docs/INSTALL.md`. The orchestrator code (`orchestrator/leerie.py`)
  is unchanged; the container/process-isolation work lives in the new
  `leerie` launcher, `Dockerfile`, and `scripts/container-entry.sh`.
  See DESIGN.md §6 *Worker subtree termination* and IMPLEMENTATION.md
  §0.5 *Container shape* for the architecture and code surface.

- **`scripts/install.sh` now auto-installs the container runtime.** On
  macOS the installer runs `brew install colima` and `colima start
  --runtime containerd --mount-type virtiofs`. On Linux it dispatches
  to the matching package manager (Debian/Ubuntu via `apt-get`,
  Fedora/RHEL via `dnf`, Arch via `pacman`) for `containerd`, then
  downloads the pinned `nerdctl` v2.3.1 binary from upstream (arch-aware
  amd64/arm64), then `sudo systemctl enable --now containerd`. Pass
  `--no-runtime-install` (or `LEERIE_NO_RUNTIME_INSTALL=1`) to keep the
  pre-rollout behavior: detect the runtime, print the manual install
  hint, and exit 1. Unknown distros fall back to the hint regardless.
  Existing Docker Desktop / podman installs coexist with Colima/nerdctl
  — no conflict detection is attempted.

- **Default-mode runs now require the `gh` CLI on the host.** The
  container image installs `gh`, but auth state at `~/.config/gh/` is
  bind-mounted from the host. Run `gh auth login` on the host once
  before running leerie, or pass `--no-push` to skip the finalize PR
  step. `git push` for HTTPS remotes uses bind-mounted
  `~/.git-credentials`; for SSH remotes it uses bind-mounted `~/.ssh`.
  *macOS caveat*: SSH agent forwarding is not available on Colima —
  AF_UNIX sockets don't traverse the Lima VM boundary, and
  `$SSH_AUTH_SOCK` typically sits under `/private/tmp/` (outside
  Colima's auto-share scope). Passphrase-protected SSH keys won't work
  inside the container on macOS; switch the remote to HTTPS (via
  `gh auth setup-git`) or pass `--no-push`. Linux native users get the
  agent socket mounted normally. The launcher detects `Darwin` and
  skips the mount with a one-line note pointing at this workaround.

- **Plugin mode (`/leerie` from inside Claude Code) and terminal mode
  share one container model.** The launcher detects `[ -t 0 ]` and
  passes `-it` (terminal) or `-i` only (plugin/no-TTY). Plugin mode
  reuses the existing `EXIT_NEEDS_ANSWERS=10` clarification dance
  (write `.leerie/pending-questions.json`, exit 10; the plugin agent
  reads the file through the `/work` bind mount, asks the user in
  chat, re-runs with `--answers`). No new mechanism — the container
  is transparent.

- **Ctrl-C is now resumable.** Earlier versions treated SIGINT as an
  explicit "throw this away" gesture and ran a full purge — worktrees,
  branches, and the run dir all deleted, `--resume` impossible.
  Ctrl-C now follows the same conservative contract as every other
  abnormal exit: worktrees are torn down (re-created idempotently on
  resume), state.json + branches + checkpoints all survive. The
  explicit full-purge gesture is `scripts/cleanup.sh --run-id <id>
  --branches`. README, DESIGN.md §6, IMPLEMENTATION.md §5, and the
  signal-cleanup pin test are updated to match.
- **`max_total_workers` default 40 → 60.** Empirically (May 2026)
  18-subtask runs hit the cap mid-conformance, aborting with
  `worker budget exhausted`. Structural budget for an 18-subtask plan
  is ≈ 1 classifier + 2 planners + 1 reconciler + 18 implementers +
  ~18 conformers + a few continuations / integrators ≈ 45–55 workers
  worst-case; the new default leaves margin without inviting runaway
  cost. `LEERIE_MAX_WORKERS` env var and `max_workers` in
  `leerie.toml` are new escape hatches (same precedence as
  `--confidence-rounds`: CLI > env > TOML > default).
- **Protected-path scope narrowed.** The diff-scope check that gates
  implementers and conformers previously rejected any write under
  `.claude/` wholesale. It now protects only `.leerie/`, `.git/`,
  and top-level `.claude/` files (`settings.json`,
  `settings.local.json`); the three documented Claude Code
  user-deliverable subtrees (`.claude/agents/`, `.claude/commands/`,
  `.claude/skills/`) are exempt. Leerie's own self-healing skill
  instructs downstream consumers to write subagent files at
  `.claude/agents/<name>.md`; the over-broad protection previously
  blocked the very pattern the skill teaches. DESIGN.md §9,
  IMPLEMENTATION.md, and `prompts/conformer.md` are updated to match.
- **`--no-clarify` is now `--clarify`; no-questions is the new
  default.** The flag's polarity is inverted: by default leerie runs
  without surfacing intent questions to the user. The classifier's
  codebase→research filter still runs and the implementer applies the
  same filter before any mid-execution decision — "no questions" never
  means "skip the rigor." Pass `--clarify` (or set
  `LEERIE_CLARIFY=true` / `clarify = true` in `leerie.toml`) to
  opt into surfacing the questions that survive the filter.
- **Clarification filter is DRY-ed across the prompts.** The wording
  shown to workers now lives in a single shared fragment
  (`prompts/_clarification_filter.md`), included into
  `prompts/classifier.md` and `prompts/implementer.md` at load time
  by a new `load_prompt()` helper in `orchestrator/leerie.py`.
  Previously the same filter was restated three times and could
  drift. Worker-facing text now also pushes back explicitly on the
  base model's training prior to ask questions liberally — ~90% of
  apparent intent questions are closable by deeper investigation.

### Added

- **Rate-limit-aware hard exit with optional auto-resume.** Leerie now
  detects the Claude Code subscription session-limit message
  (`"You've hit your session limit · resets <time> (<tz>)"`) in worker
  output, and the protocol-level `rate_limit_event` whose `status`
  field falls outside the known-allowed set
  `{"allowed", "allowed_warning"}` (defensive match against future
  terminal status strings — Anthropic's terminal value is
  internal/unobserved). Either signal raises a new `RateLimitedExit`;
  main() runs the worktree-only cleanup (state + branches preserved)
  and, when the reset clause parses unambiguously (text path:
  wall-clock + IANA tz; protocol path: Unix `resetsAt` timestamp),
  sleeps until the reset moment + 30s margin then `os.execvp`'s the
  launcher with `--resume --run-id <id>` for a fresh orchestrator
  process. The `--max-workers` budget is NOT reset across the re-exec
  (it persists via state.json's `worker_count`) so a run that
  repeatedly hits the rate-limit still respects the user's cap. When
  the parse fails (malformed
  time, unknown timezone, future format change), leerie exits with code
  75 and prints the manual resume command — never a wrong-time sleep.
  CLI-only overrides on the original launch (`--model`,
  `--max-workers`, etc.) are *not* propagated across the re-exec; set
  them via env (`LEERIE_*`) or `leerie.toml` if you want them to survive.
  Empirical anchor: the verbatim message text matched identically
  across three independent runs in May 2026, and the broad
  `"rate-limit"` pattern false-matches legitimate worker text
  discussing rate-limit code, so the detector keys only on the
  literal marketing-copy prefix.
- **Belt-and-suspenders retry for the
  `incomplete-handoff`-with-missing-checkpoint case.** When the
  rate-limit detector misses (e.g. Anthropic changes the message
  format), the worker's empty-checkpoint envelope previously hit
  `_retryable_failure` and was classified terminal. The retry
  classifier now treats the validate_result line-2314 wording
  (`checkpoint_path '...' does not exist on disk`) as retryable via a
  prefix-match — tight enough that the sibling needs-clarification
  case (line 2350) which shares both substrings stays terminal.
- **Cross-planner file-overlap warning at plan-validation time.** When
  two planners both list the same path in `files_likely_touched`,
  leerie now logs a warning right after reconciliation (before the
  scheduler builds the DAG) instead of waiting for the integrator to
  crash mid-wave. Empirically (n=3 historical runs) the signal is
  clean: the one successful run had zero overlaps; both failed runs
  had ≥9. The warning is non-fatal — same-file overlap is sometimes
  legitimate (one planner adds scaffolding the other consumes) — but
  it surfaces the structural risk early. The full autonomous
  resolution (extending the reconciler's action vocabulary to handle
  file-claim conflicts the same way it handles capability-tag
  vocabulary drift) is tracked as follow-up work.
- `LEERIE_MAX_WORKERS` env var and `max_workers` key in
  `leerie.toml` resolve through the new `resolve_max_workers()`
  helper, mirroring `resolve_confidence_rounds()`'s precedence.
  `--max-workers` argparse type is now `_positive_int` (was `int`):
  bad values (0, -1, "nope") are rejected at parse time with a clean
  argparse error instead of falling through to a downstream default.
- `is_protected_path(path)` module-level helper in
  `orchestrator/leerie.py` is the new single source of truth for
  what the diff-scope check rejects. `check_diff_scope()` and
  documentation reference it; the previous inline tuple is gone.
- `LEERIE_CLARIFY` env var and `clarify` key in `leerie.toml`
  (same precedence as `--source-of-truth`: CLI > env > file > default
  `False`). New helper `_resolve_bool_pref` factors the resolution
  shape shared with `--no-push` to keep them from drifting.

### Removed

- **The `uv`-based Python provisioning install path is gone.** The host
  no longer needs Python. The `leerie` launcher is now a portable bash
  script that shells out to `nerdctl run`. The `scripts/install.sh`
  runtime preflight on macOS checks for `colima` and on Linux checks
  for `nerdctl`; it no longer installs `uv` or provisions Python 3.12.
  Existing users upgrading: there is no migration — install the
  container runtime per `docs/INSTALL.md` and re-run the installer.

- **All legacy / backwards-compat code paths.** Leerie now has **no
  migration path from prior versions** — start fresh. Specifically:
  the `cleanup.sh --legacy` mode and the `.leerie/state.json`
  detection guard in `main()` (which together migrated installations
  off the pre-per-run layout) are deleted; the `validate_resume_state`
  check that rejected pre-inversion `no_clarify` state files is
  deleted (legacy state's orphan key now does nothing); the
  `ask`-value-specific rejection tests and doc sentences are deleted
  (the underlying validation gates still reject any unknown value —
  they are not legacy-specific).
- **`ask` source-of-truth value.** The four-value preference
  (`codebase` / `research` / `both` / `ask`) collapses to three.
  Default is now `both` (codebase first; research as fallback) — the
  preference is never surfaced as an interactive question, because
  setting `--source-of-truth` / `LEERIE_SOURCE_OF_TRUTH` /
  `source_of_truth` in `leerie.toml` already expresses an explicit
  intent, and an unset preference implicitly accepts `both`.
  `gather_answers` no longer prompts for source-of-truth or emits the
  `source_of_truth` / `source_of_truth_hint` fields in
  `pending-questions.json`.

### Added

- `reconciler` worker. Spawned by the orchestrator between `phase_plan`
  and `schedule` when parallel planners disagree on capability-tag
  vocabulary across domains. The reconciler resolves the mismatch via
  renames, added `provides`, or new connector subtasks; genuinely
  unresolvable gaps abort the run with the worker's diagnosis instead
  of the prior opaque "nothing provides X" error. Short-circuits with
  no worker invocation when planners already agreed (DESIGN.md §5,
  §14). Reconciler-emitted subtask `id` collisions — both with
  existing subtasks and with other reconciler-emitted ids — now fail
  loud; the prior silent-overwrite path through `schedule()`'s
  dict-flatten would have lost a subtask from the DAG.

### Changed

- **Finalize no longer merges the run branch into the working branch
  locally.** Phase 6 now verifies the run branch is non-empty, pushes it
  to `origin`, and opens a PR via `gh pr create --base <working-branch>
  --head leerie/runs/<run-id>`. The working branch is **not** modified
  locally; the PR is the proposed integration. Previously, a successful
  run landed a `leerie: integrate completed run into <working-branch>`
  merge commit on the working branch *and* opened a PR with the same
  base, duplicating the same change in two places. `--no-push` still
  skips the push + PR step (the run branch is left local-only; the
  working branch is unchanged). The `scripts/finalize.sh` script is now
  a thin verifier (no `git checkout`, no `git merge`); the two
  post-merge sanity checks in `phase_finalize` are removed (they
  assumed a merge had just happened on HEAD).

- **Per-subtask branches are auto-deleted at finalize.** A new
  `cleanup.sh --subtask-branches` flag (mutually exclusive with
  `--branches`) is now invoked from `phase_finalize` after push+PR. It
  deletes every `leerie/subtasks/<run-id>/*` branch and keeps the
  run branch `leerie/runs/<run-id>` (the PR head must outlive the
  orchestrator). The per-subtask commits remain reachable from the run
  branch's `--no-ff` merge graph; the per-worker audit trail is now
  `git log leerie/runs/<run-id> --graph`. Previously every successful
  run left ~17–20 orphan subtask branches that the user had to delete
  by hand.

- **Model defaults flipped to a judgment-vs-implementation split.**
  Judgment workers (`classifier`, `planner`, `reconciler`,
  `integrator`, `validator`) now default to `opus`; `implementer`
  defaults to `sonnet`. Previously every worker defaulted to `sonnet`.
  The split prioritizes Opus-grade reasoning on the steps where a
  wrong call is most costly (decomposition, conflict resolution,
  cross-domain wiring, criterion judgment) while keeping the
  most-frequently-invoked worker on the cheaper model. **Cost note:**
  Opus is materially more expensive per token than Sonnet; a typical
  run is meaningfully more expensive than before. To restore the
  pre-0.3 all-sonnet behavior in one knob, set `--model sonnet`,
  `LEERIE_MODEL=sonnet`, or `model = sonnet` in `leerie.toml`.
  Per-worker overrides (`--model-<worker>`, `LEERIE_MODEL_<WORKER>`,
  `model_<worker>`) let you dial individual workers independently.

- `validate_checkpoint()` rejects a wider set of placeholder tokens.
  The single-token noise list now includes `nothing`, `unknown`, `todo`,
  and `pending`, and a normalization step strips trailing `.`/`!`/`…`
  and collapses pure-`?` runs before the membership check — so `None.`,
  `TBD!`, and `???` are caught alongside the bare forms. The two
  "nothing-to-report-is-OK" sections (`Decisions made`, `Open unknowns`)
  continue to accept these. Effect: a previously-accepted thin handoff
  that used any of the new variants now fails the checkpoint validation
  and the orchestrator routes the subtask to `blocked` per the existing
  rule.

### Deprecated

### Removed

### Fixed

- **`phase_finalize` now passes `--run-id` to `cleanup.sh`.** The previous
  bare `cleanup.sh` invocation hit the script's interactive no-arg path,
  which scans for the most-recently-failed run and prompts y/N on stdin.
  The orchestrator runs cleanup non-interactively, so `read -r answer`
  silently saw EOF, the script exited 0 without doing anything, and the
  orchestrator continued past it. Every successful run was leaving its
  full set of subtask worktrees on disk under
  `.leerie/runs/<run-id>/worktrees/` despite the "cleanup ran" log
  line. A defense-in-depth pin in `phase_finalize` now asserts the
  invocation includes the run id.

### Security

## [0.2.0] - 2026-05-24

### Added

- Initial public release. Deterministic Python orchestrator for Claude Code;
  six-phase classify → clarify → plan → schedule → execute → finalize
  pipeline; per-wave parallel implementers in isolated git worktrees;
  evidence-gated implement/validate loop; JSON-schema-validated worker
  outputs; resumable state; pytest suite covering deterministic
  enforcement functions.
- Per-worker model selection. Default `sonnet`; override with `--model`
  (sets all five workers) or `--model-<worker>` (per-worker; values:
  `sonnet` / `opus` / `haiku`). Env equivalents `LEERIE_MODEL` and
  `LEERIE_MODEL_<WORKER>`; TOML keys `model` and `model_<worker>` in
  `leerie.toml`. Resolution order, highest first: per-worker CLI →
  global CLI → per-worker env → global env → per-worker TOML → global
  TOML → default. Invalid values rejected at startup. Models are
  re-resolved on `--resume` (not persisted in state).
- `--source-of-truth` CLI flag for one-off overrides of the
  `LEERIE_SOURCE_OF_TRUTH` env var and `leerie.toml`.

### Changed

- Source-of-truth resolution precedence flipped: env var now beats
  `leerie.toml` (and the new `--source-of-truth` flag beats both).
  CLI/env are session-scoped knobs; `leerie.toml` is the committed
  repo default.

[Unreleased]: https://github.com/enricai/leerie/compare/v0.2.6...HEAD
[0.2.6]: https://github.com/enricai/leerie/releases/tag/v0.2.6
[0.2.5]: https://github.com/enricai/leerie/releases/tag/v0.2.5
[0.2.1]: https://github.com/enricai/leerie/releases/tag/v0.2.1
[0.2.0]: https://github.com/enricai/leerie/releases/tag/v0.2.0
