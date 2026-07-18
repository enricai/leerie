# Installing Leerie

Leerie runs entirely inside a container. The cleanup guarantee — when you
Ctrl-C, every `claude -p` worker and every test runner / build / dev
server they spawned is reaped — comes from the Linux kernel tearing down
the container's PID namespace, not from heuristics in Python. See
[`DESIGN.md` §6 *Worker subtree termination*](DESIGN.md) for the
architectural reasoning and [`IMPLEMENTATION.md` §0.5 *Container
shape*](IMPLEMENTATION.md) for the launcher / image / mount details.

This document covers one-time setup of the container runtime per OS,
then how to install leerie itself.

## macOS

The one-line installer **auto-installs and starts** the container runtime
for you (`brew install colima` + `colima start --runtime containerd
--mount-type virtiofs --cpu N --memory M`). The `--cpu` / `--memory`
values are auto-detected from your host: half of the host's logical
cores (clamped to 2–8) and half of the host's RAM in GB (clamped to
4–16). On an 8-core / 16-GB Mac you'd get a 4-CPU / 8-GB VM; on a
16-core / 64-GB Mac you'd get the 8-CPU / 16-GB ceiling.

This replaces Colima's 2-CPU / 2-GB default, which is not enough for
leerie's parallel-worker workload (concurrent `claude -p` workers plus
toolchain processes blow through 2 GB, triggering a kernel OOM in the
VM that manifests as `exit 255` on the launcher with no diagnostic).

If you'd rather install the runtime yourself — common in CI or with
dotfiles managers — pass `--no-runtime-install` or set
`LEERIE_NO_RUNTIME_INSTALL=1` and the installer will print the manual
commands and exit 1.

```bash
# One-line installer — auto-installs Colima + starts the VM, then installs leerie.
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

Or, to do the runtime install by hand:

```bash
brew install colima
# Pick `--cpu N --memory M` matching half your host CPU/RAM (bounds
# 2..8 / 4..16 GB — same as the installer's auto-sizing above). On an
# 8/16 host: --cpu 4 --memory 8. Colima's 2/2 default is not enough.
# Also paste the swap-provision YAML block from "Memory pressure: swap
# configuration" (below) into ~/.colima/default/colima.yaml BEFORE the
# first `colima start` — that way swap is live on the first boot
# without a follow-up restart.
colima start --runtime containerd --mount-type virtiofs --cpu 4 --memory 8

# Then run the installer with the opt-out flag (or env var):
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

Notes:

- **Do not** `brew install nerdctl`. The Homebrew formula has
  `Requires: Linux` because the nerdctl binary itself talks to a
  containerd Unix socket — which doesn't exist on macOS. Colima provides
  nerdctl *inside its VM* and ships a host-side shim
  (`colima nerdctl install`) that proxies every invocation to the VM.
  Leerie's launcher auto-runs `colima nerdctl install` on first use, so
  you don't have to run it yourself.
- `--mount-type virtiofs` is the fastest mount and gives correct UID
  semantics for bind mounts. It's the default on recent Colima.
- The Colima VM persists across reboots — `colima start` again is
  enough to bring it back up. To autostart at login:
  `brew services start colima`.
- The installer auto-sizes the VM (half-of-host CPU/RAM, bounded
  2–8 cores / 4–16 GB; see the macOS section above). To override —
  e.g. you want more or less than the auto-sized default:
  `colima stop && colima start --cpu 6 --memory 12 --runtime containerd --mount-type virtiofs`.
- If you have Colima already running with a smaller-than-recommended
  VM, re-running the installer will leave the VM alone but log a
  one-line hint with the resize command.

### Memory pressure: swap configuration

Colima's default VM has **zero swap**. Under leerie's parallel-implementer
workload — concurrent `claude -p` workers (~300 MB each) plus toolchain
processes (Vitest workers can spike to 2 GB RSS, `tsc`/`pnpm install`
add another GB each) — RAM exhausts faster than the kernel can react.
With no swap, the OOM killer fires immediately, and it tends to hit the
host-side `nerdctl` / `lima-guestagent` daemons first. That collapses
the Mac launcher's connection to the VM and you see `FATA[NNNN] exit
status 255` with no orchestrator diagnostic — the container's stdout
just stops mid-stream.

**The fix:** add 4 GB of swap at `/var/swapfile` and tune
`vm.swappiness=10` so the kernel uses swap only under genuine memory
pressure (default 60 reaches for swap eagerly). Colima's `provision:`
hook runs on every VM boot with root privileges; we drop an idempotent
script in there.

**On a fresh install**, the leerie installer writes this for you — the
`scripts/install.sh` path detects an absent `~/.colima/default/colima.yaml`
and writes the canonical block before the first `colima start`, so
swap is live on day 1. If a `colima.yaml` already exists, the installer
deliberately does NOT mutate it (it might contain your custom mounts /
CPU type / disk size). Instead, the next `bash scripts/install.sh`
invocation logs a one-line hint with the YAML block to paste in.

**On an existing setup**, paste this into `~/.colima/default/colima.yaml`
(replace any existing `provision: null` / `provision: []` line with the
whole block) and run `colima stop && colima start` to apply:

```yaml
# leerie:swap-provision-v1 BEGIN
# Auto-managed by leerie's installer (scripts/runtime-install.sh).
# Adds 4 GB of swap at /var/swapfile and tunes vm.swappiness to 10
# so the kernel uses swap only under real memory pressure (default
# 60 is too eager for our safety-net use). Provision scripts run
# every VM boot; the script is idempotent.
provision:
  - mode: system
    script: |
      set -eu
      SWAPFILE=/var/swapfile
      SWAPSIZE_GB=4
      if [ ! -f "$SWAPFILE" ]; then
        fallocate -l "${SWAPSIZE_GB}G" "$SWAPFILE"
        chmod 600 "$SWAPFILE"
        mkswap "$SWAPFILE"
      fi
      if ! swapon --show=NAME --noheadings | grep -qx "$SWAPFILE"; then
        swapon "$SWAPFILE"
      fi
      sysctl -w vm.swappiness=10
# leerie:swap-provision-v1 END
```

The block above must match what the installer writes byte-for-byte —
authoritative copy lives in `_runtime_colima_swap_yaml` in
`scripts/runtime-install.sh`. To verify after restart:

```bash
colima ssh -- free -h           # Swap: 4.0Gi   0B   4.0Gi
colima ssh -- sysctl vm.swappiness   # vm.swappiness = 10
colima ssh -- ls -lh /var/swapfile   # -rw------- 1 root root 4.0G
```

The 4 GB swapfile lives on Colima's persistent VM disk and survives
`colima stop`/`colima start`. Only `colima delete` removes it — and
the next `colima start` would re-create it via the provision script.

### macOS-specific: bind-mount scope

Colima auto-shares only paths under `/Users/$USER` into the VM by
default. Any path outside that range (an external volume, a system
path) appears as an *empty* directory inside the container — with no
error. Leerie's launcher warns at preflight if `$USER_REPO` or any
`--inspect-dir` falls outside `/Users/$USER`.

To allow paths outside the default scope: edit
`~/.colima/default/colima.yaml`, add the path under `mounts:`, then
`colima restart`.

## Linux

Containerd and nerdctl run natively — no VM needed. The one-line
installer **auto-installs and starts** the runtime per distro (Debian/
Ubuntu via `apt-get`, Fedora/RHEL via `dnf`, Arch via `pacman`; nerdctl
binary pinned to v2.3.1 from upstream). Unknown distros fall back to a
hint and exit 1 — install manually then re-run with
`--no-runtime-install`.

```bash
# One-line installer — auto-installs containerd + nerdctl, then installs leerie.
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

Or, to do the runtime install by hand (sections below show the per-distro
commands), then pass `--no-runtime-install`:

```bash
# After running the per-distro setup below:
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

### Debian / Ubuntu

```bash
sudo apt-get install -y containerd
# nerdctl: install the pinned static binary from upstream. Arch is detected
# so the same line works on x86_64 (amd64) and arm64 (Asahi, Graviton, Pi).
NERDCTL_VERSION=2.3.1
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
curl -L "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" \
  | sudo tar -C /usr/local/bin -xz nerdctl
sudo systemctl enable --now containerd

curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

### Fedora / RHEL

```bash
sudo dnf install -y containerd
# nerdctl: install the pinned static binary from upstream (arch-detected).
NERDCTL_VERSION=2.3.1
ARCH="$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
curl -L "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" \
  | sudo tar -C /usr/local/bin -xz nerdctl
sudo systemctl enable --now containerd

curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

### Arch

```bash
sudo pacman -S containerd nerdctl rootlesskit buildkit cni-plugins fuse-overlayfs
sudo systemctl enable --now containerd

curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

### Rootless mode (recommended)

Running containerd as root is unnecessary for leerie — it doesn't need
privileged operations. To set up rootless containerd:

```bash
containerd-rootless-setuptool.sh install
```

After that, the user's default nerdctl context points at the rootless
socket (`unix:///run/user/$UID/containerd/containerd.sock`). Leerie's
launcher uses whatever context nerdctl resolves to, so once rootless is
set up no extra flags are needed.

## Verifying the runtime

Before running leerie, confirm the runtime works:

```bash
nerdctl run --rm hello-world
```

You should see "Hello from Docker!" (containerd uses the same image).
If that fails, leerie will too.

## Optional host tools

These are not required to run leerie, but unlock additional automation:

**`gh` (GitHub CLI)** — enables automatic PR creation at the end of each
run. Without it, leerie pushes the run branch and prints a `gh pr create`
command for you to run manually. Install from https://cli.github.com, then
authenticate:

```bash
gh auth login
```

## Fly.io runtime (optional)

By default leerie runs workers locally via `nerdctl`. Passing `--runtime fly`
(or setting `LEERIE_RUNTIME=fly` or `runtime = fly` in `leerie.toml`) routes
each worker through Fly.io Machines instead — useful when you want to off-load
worker compute from your local machine.

Prerequisites for the fly runtime:

1. **`flyctl` installed and authenticated** — `flyctl auth login` must
   succeed. Install from https://fly.io/docs/flyctl/install/ (or
   `brew install flyctl` on macOS). The launcher auto-installs `flyctl`
   on first `--runtime fly` invocation if it's missing.
2. **A Fly.io account with billing set up** — Fly Machines bill
   per-second; you need a credit card on file. There is no free tier
   for the kind of always-on compute leerie spins up.

That's it. The launcher handles everything else automatically on first
`--runtime fly` invocation:

- **Auto-creates the Fly app** (`flyctl apps create $LEERIE_FLY_APP`)
  if it doesn't exist yet. Fly app names are globally unique — set yours
  via `export LEERIE_FLY_APP=my-app` or `--fly-app my-app`. Idempotent.
- **Builds the leerie image on Fly's remote builder** (no host Docker
  daemon required) and pushes it to `registry.fly.io/$LEERIE_FLY_APP`.
  This step takes ~3-5 min the first time per leerie version; subsequent
  runs reuse the cached tag.
- **Provisions a Fly Machine** per worker, seeds repo + Claude auth,
  and runs the orchestrator detached.

The local `nerdctl` setup above is **not required** when using
`--runtime fly`; leerie's launcher skips the local container preflight
and delegates the entire worker lifecycle to the Fly.io API. The
local container runtime (Colima on macOS, containerd on Linux) is
only needed for the default `local` runtime.

**Disk sizing.** Every `--runtime fly` run gets a per-machine Fly
volume (default 8 GB) mounted at `/work` — the path that holds the
seeded repo, `.leerie/runs/<id>/` state, and the per-subtask worktrees
that dominate disk growth. The volume survives `machine stop` so the
pause-on-failure contract holds across arbitrarily long pauses. If a
run errors out with `ENOSPC: no space left on device` (typically
during parallel waves when many `claude -p` workers accumulate
session-env state and per-subtask worktrees at once), increase the
volume size via `FLY_VM_DISK_GB` (or `--fly-disk-gb N`, or
`fly_disk_gb = N` in `leerie.toml`):

```bash
FLY_VM_DISK_GB=30 leerie 'task' --runtime fly
# or:
leerie 'task' --runtime fly --fly-disk-gb 50
```

The volume is created at machine-provision time and destroyed when the
machine is destroyed (clean exit or `leerie --kill`), so steady-state
storage cost is zero. While a paused run is on its volume, Fly charges
per-GB-month — minimal at typical sizes but non-zero.

**Recovery if an orchestrator dies mid-run.** If a run errors out and
the post-run sync fails (e.g. the machine ran out of disk before the
orchestrator could write `finished_at`), `leerie --finalize <run-id>
--force` recovers the work. The launcher SSHes into the machine,
verifies the orchestrator process is dead, patches `finished_at`, and
proceeds with the normal fetch + push + PR flow. `--force` refuses if
the orchestrator is still alive, so it is safe to use proactively when
in doubt. See `docs/IMPLEMENTATION.md` §7 *Detached run finalization*
for the full semantics.

**`--local-build` opt-in** (most users should NOT use this). Pass
`--local-build` or set `LEERIE_LOCAL_BUILD=1` to build the leerie image
locally with `nerdctl`/`docker` and push to Fly's registry from your
host. This path only works when your host's Docker daemon can
authenticate to `registry.fly.io` — practically, that means **Docker
Desktop on macOS** (with `flyctl auth docker` having run within the
last 5 minutes — the token expires) or **Linux with Docker installed
via apt/dnf** + `flyctl auth docker`. **It does NOT work with
nerdctl-in-Colima on macOS** because nerdctl cannot reach macOS
Keychain (where Docker Desktop's `credsStore: desktop` helper stores
the credential). The default remote-builder path works for everyone
with `flyctl auth login` succeeded and avoids this auth dance
entirely; use `--local-build` only if you have a specific reason
(e.g. you need to build an image variant the remote builder can't
produce, or you're testing the build pipeline locally).

## EC2 runtime

`--runtime ec2` provisions an AWS EC2 instance, seeds it, runs the
orchestrator on it (detached), and tears it down on exit — the EC2
counterpart to `--runtime fly`. This section documents credential
resolution and the required instance-shape vars.

> **AMI prerequisite:** `LEERIE_EC2_AMI` must name an AMI with the
> leerie orchestrator source already baked in at `/opt/leerie-image/`
> (DESIGN §6 *EC2 runtime lifecycle*, "Image delivery" — the adopted
> default is bake-into-AMI, the same artifact shape
> `scripts/remote/build-push.sh` produces for Fly, not a stock AMI).
> Building that AMI is an operator-owned, out-of-band step (Packer /
> EC2 Image Builder); leerie does not build or publish one for you.

Passing `--runtime ec2` (or setting `LEERIE_RUNTIME=ec2` or
`runtime = ec2` in `leerie.toml`) selects the EC2 execution backend as
an alternative to the default `local` runtime and to `--runtime fly`
above.

### Prerequisites

1. **AWS CLI v2 installed.** Unlike the Fly runtime, leerie does not
   auto-install this — the official installers commonly need `sudo`,
   which is out of scope for an unattended preflight. Install from
   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
   or `brew install awscli` on macOS.
2. **AWS credentials that resolve successfully**, checked via `aws sts
   get-caller-identity`. If credentials are expired or missing, the
   preflight prints `aws sso login --profile <profile>` (or bare `aws
   sso login` when no profile is set) and exits.

### Credential resolution order

Leerie resolves AWS credentials/region using the same precedence order
the AWS CLI and SDKs use, so the EC2 runtime authenticates as the
identity you already expect from `aws` commands run in the same shell:

1. **`AWS_ACCESS_KEY_ID` + `AWS_SECRET_ACCESS_KEY`** (+ optional
   `AWS_SESSION_TOKEN`) environment variables — always wins when set.
2. **Named profile** (`AWS_PROFILE`, else `default`) in
   `~/.aws/config` / `~/.aws/credentials`:
   - Static `aws_access_key_id`/`aws_secret_access_key` in
     `~/.aws/credentials`, or
   - SSO (`sso_session` reference or legacy inline `sso_start_url`),
     resolved via the cached token in `~/.aws/sso/cache/*.json`. An
     expired or never-logged-in SSO session produces the
     `aws sso login --profile <profile>` hint above.
3. **EC2 instance role via IMDS** — only meaningful once code is
   running *on* an EC2 instance. On your host (the only place this
   preflight runs today) there is no instance role, so the chain ends
   here with an actionable error rather than silently failing.

Region resolves separately: `AWS_REGION` > `AWS_DEFAULT_REGION` >
the profile's `region` key in `~/.aws/config` > an actionable error.

### `LEERIE_AWS_REGION` / `LEERIE_AWS_PROFILE` vs. `AWS_REGION` / `AWS_PROFILE`

These are two distinct things — leerie does not collapse them:

- **`LEERIE_AWS_REGION`** / **`LEERIE_AWS_PROFILE`** (also
  `--aws-region` / `--aws-profile`, or `aws_region` / `aws_profile` in
  `leerie.toml`) are **leerie's own knobs** for which AWS region/profile
  *leerie itself* uses when provisioning `--runtime ec2` machines.
  Free-form strings, no validation. Unset by default — leaving
  region/profile selection entirely to the credential chain above.
- **`AWS_REGION`** / **`AWS_PROFILE`** are the **AWS SDK's own
  credential-chain env vars**, resolved independently by the standard
  AWS precedence order described above.

```bash
export LEERIE_AWS_REGION=us-east-1
export LEERIE_AWS_PROFILE=my-aws-profile
leerie "task" --runtime ec2 --aws-region us-east-1 --aws-profile my-aws-profile
# …or commit a leerie.toml at the repo root with:
#   aws_region = us-east-1
#   aws_profile = my-aws-profile
```

### Required instance-shape vars

Five `LEERIE_EC2_*` vars name the AWS `RunInstances` parameters the
provisioning step needs. Unlike Fly, where
`FLY_VM_CPUS`/`FLY_VM_MEMORY_MB` have working defaults, these describe
AWS account resources leerie cannot choose on your behalf — there is
no default tier. `--runtime ec2` without all five resolved fails the
same way `--runtime fly` without `LEERIE_FLY_APP` fails: an actionable
`die()` naming the missing var, before any AWS API call.

Each resolves via the same **CLI flag > env var > `leerie.toml` key**
precedence as every other leerie knob:

```bash
export LEERIE_EC2_AMI=ami-0abcdef1234567890
export LEERIE_EC2_INSTANCE_TYPE=t3.large
export LEERIE_EC2_KEY_NAME=my-ec2-keypair
export LEERIE_EC2_SECURITY_GROUP=sg-0123456789abcdef0
export LEERIE_EC2_SUBNET_ID=subnet-0123456789abcdef0
leerie "task" --runtime ec2
# …or commit a leerie.toml at the repo root with:
#   ec2_ami = ami-0abcdef1234567890
#   ec2_instance_type = t3.large
#   ec2_key_name = my-ec2-keypair
#   ec2_security_group = sg-0123456789abcdef0
#   ec2_subnet_id = subnet-0123456789abcdef0
# …or pass them as CLI flags per run:
leerie "task" --runtime ec2 \
  --ec2-ami ami-0abcdef1234567890 --ec2-instance-type t3.large \
  --ec2-key-name my-ec2-keypair --ec2-security-group sg-0123456789abcdef0 \
  --ec2-subnet-id subnet-0123456789abcdef0
```

| Var | CLI flag | `leerie.toml` key | Meaning |
|---|---|---|---|
| `LEERIE_EC2_AMI` | `--ec2-ami` | `ec2_ami` | AMI id to launch. |
| `LEERIE_EC2_INSTANCE_TYPE` | `--ec2-instance-type` | `ec2_instance_type` | EC2 instance type (e.g. `t3.large`). |
| `LEERIE_EC2_KEY_NAME` | `--ec2-key-name` | `ec2_key_name` | EC2 key-pair name for SSH access. |
| `LEERIE_EC2_SECURITY_GROUP` | `--ec2-security-group` | `ec2_security_group` | Security group id to attach. |
| `LEERIE_EC2_SUBNET_ID` | `--ec2-subnet-id` | `ec2_subnet_id` | Subnet id to launch into. |

## What leerie mounts into the container

When the container starts, the launcher mounts the following:

| Host path | Container path | Mode | Purpose |
|---|---|---|---|
| `$(pwd)` (your repo) | `/work` | rw | Leerie operates on the repo here. Worktrees are written under the repo; the repo itself stays clean — no `.leerie/` directory accumulates inside it. |
| `$LEERIE_STATE_HOST_DIR` (resolved host state dir) | `/leerie-state` | rw | Per-repo run state (`state.json`, `runs/`, `logs/`, worktrees). Defaults to `$HOME/.leerie/<basename>/`; overridable via `LEERIE_STATE_DIR` env var, `state_dir =` in `leerie.toml`, or `--state-dir`. Cross-repo basename collisions are caught at use time via an `.owner` sidecar inside the dir. Lives outside the repo so target projects need no `.gitignore` entry. `--resume` works across container runs because state persists on the host at this path. |
| `$LEERIE_HOME` (leerie install) | `/opt/leerie-image` | ro | Leerie's source and Dockerfile. Edit `orchestrator/leerie.py` on the host; next run picks it up without rebuilding the image. |
| Per-run host scratch dir (`~/.cache/leerie/cfg-…/.claude.json`) | `/home/leerie/.claude.json` | rw | Per-container copy of `~/.claude.json` with `projects[]` stripped. The shared host file is never directly mounted — it's a documented `claude-code` corruption race (anthropics/claude-code issues #28847, #29217, #29395, #40226) that hangs workers in a recovery loop. Each container writes only its private copy. |
| Per-run host scratch dir (`~/.cache/leerie/cfg-…/.claude/`) | `/home/leerie/.claude` | rw | Per-container copy of `~/.claude/` with bulky, prior-session, and history paths skipped (`history.jsonl`, `projects/`, `sessions/`, `tasks/`, `plans/`, `todos/`, `file-history/`, `paste-cache/`, `shell-snapshots/`, `session-env/`, `telemetry/`, `debug/`, `downloads/`, `backups/`, `chrome/`, `ralph-state/`). CLI capability dirs (`agents/`, `skills/`, `commands/`, `hooks/`, `plugins/`, `settings.json`, `mcp-needs-auth-cache.json`, `local/`, `statsig/`, `cache/`) ride along. |
| `$CLAUDE_CODE_OAUTH_TOKEN` / Keychain / `~/.claude/.credentials.json` → staged `.claude/.credentials.json` | `/home/leerie/.claude/.credentials.json` | rw | The launcher resolves whichever credential is available, preferring the long-lived `$CLAUDE_CODE_OAUTH_TOKEN` (`claude setup-token`) first when set, since a container can't refresh a copied subscription token; otherwise Keychain on macOS (an IPC service the container can't reach — extracted via `security find-generic-password -s "Claude Code-credentials" -w`), then falls back to `~/.claude/.credentials.json` on disk. All three resolve to the same JSON shape written to the staged credentials file — the same path the Linux CLI reads — so authentication works identically on both platforms. See `docs/DESIGN.md` §6 *Credential strategy*. |
| Per-run host scratch copies of `~/.gitconfig`, `~/.gitconfig.local`, `~/.gitignore`, `~/.gitignore_global`, `~/.git-credentials`, `~/.netrc`, `~/.config/git/`, `~/.ssh/`, `~/.gnupg/` | `/home/leerie/.<same>` | rw | Per-container copies of every present host config / auth file the worker might need. SSH and GPG copies exclude agent sockets (`agent/`, `S.*`, `*.sock`) — sockets are host-bound and not reachable from the container. Workers can `git config --local`, push over SSH, or `git commit -S` if signing is configured, all against private copies that vanish on container exit. |
| Each `--inspect-dir` path | `/inspect/<basename>` | ro | Extra directories the inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision) need read access to. |

Per-container isolation is the key design choice: each container sees
a private copy of your Claude + git + SSH + GPG config at the default
paths the CLI and git already look at, so nothing inside the container
knows or cares that the files are private rather than the shared host
originals. Container-side writes (incremented startup counters, new
session transcripts, refreshed auth state) are intentionally lost when
the container exits — leerie's own telemetry (`<state-root>/runs/<id>/`,
mounted at `/leerie-state`) is the source of truth for run cost and
structure. The host scratch dir is
reaped on container exit; your host `~/.claude.json` and `~/.claude/`
are never modified by a worker.

## Troubleshooting

**Skip auto-install of the container runtime** — pass
`--no-runtime-install` to `install.sh`, or set
`LEERIE_NO_RUNTIME_INSTALL=1`. The installer falls back to printing the
manual hint and exits 1 if the runtime is missing. Useful for CI,
dotfiles managers, or any environment where package installs are
tracked elsewhere.

**"Colima VM is not running"** (macOS) — start it:
`colima start --runtime containerd --mount-type virtiofs --cpu 4 --memory 8`
(pick `--cpu` / `--memory` matching half your host CPU/RAM; see the
auto-sizing section at the top of this doc for the bounds). If this
is your first start after editing `~/.colima/default/colima.yaml`
to add the swap-provision block from "Memory pressure: swap
configuration", the provision script runs automatically on boot.

**"nerdctl cannot reach the container runtime"** — on macOS, you
probably started Colima with the default `docker` runtime. Restart
with containerd: `colima stop && colima start --runtime containerd
--mount-type virtiofs --cpu 4 --memory 8` (carry your half-of-host
sizing through the restart; the bare command would re-default to
2/2). The swap-provision YAML block in `~/.colima/default/colima.yaml`
re-applies on every boot, so swap is preserved through this restart.
On Linux, check `systemctl status containerd`.

**`FATA[NNNN] exit status 255` with no orchestrator diagnostic** — the
leerie run died because Colima's host-side `nerdctl` / `lima-guestagent`
daemon was OOM-killed inside the VM (not your container's PID 1). Check
`colima ssh -- sudo dmesg | grep oom-killer` for evidence. The fix is
to add 4 GB of swap via the YAML block in "Memory pressure: swap
configuration" above.

**"worker cgroup containment could not be enabled"** — leerie stops
before running any worker when it cannot enforce per-worker memory/PID
limits (DESIGN §6 *Memory containment*). Enforcement is done by a broker
(`scripts/cgroup-broker.py`) launched by the container entrypoint; the
run dies if that broker can't operate. Common causes:
- **no usable cgroup hierarchy** — the broker handles both a cgroup v2
  unified mount (Colima, and rootless containerd via the delegated user
  slice below) and cgroup v1/hybrid split `pids/` + `memory/` controller
  mounts (some Fly.io Firecracker VMs), but fails if none is present —
  e.g. a host exposing only a partial/hybrid layout without both
  controllers, or an unusual container cgroup setup.
- **rootless containerd (Linux) on a systemd + cgroup v2 host** — leerie
  anchors the broker's cgroup slice at the systemd-delegated user slice
  (`/sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/`)
  rather than the top-level `/sys/fs/cgroup` (which the rootlesskit-mapped
  host UID has no privilege over). No host reconfiguration is needed —
  this delegation already exists on any systemd host with cgroup v2. If
  you hit this gate on a rootless host anyway, check that
  `/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control`
  contains `pids` and `memory` (`systemctl --user show containerd
  -p Slice` confirms the daemon lives under that tree).
- **rootless containerd on a non-systemd init, or cgroup v1** — there is
  no equivalent delegated subtree, so containment genuinely cannot be
  enabled; pass `--dangerously-allow-uncapped` (below) to proceed anyway.
- **read-only or missing `/sys/fs/cgroup`** — a read-only cgroupfs or an
  absent mount leaves the broker unable to create cgroups.
- **broker didn't start** — check the container log for a
  `[cgroup-broker] listening` line; its absence means PID 1 couldn't
  launch `python3 /opt/leerie-image/scripts/cgroup-broker.py`.

To run anyway *without* containment (workers can then exhaust the VM's
thread/PID table — the failure this gate prevents), pass
`--dangerously-allow-uncapped` (or set `LEERIE_DANGEROUSLY_ALLOW_UNCAPPED=1`,
or `dangerously_allow_uncapped = true` in `leerie.toml`). The
`cgroup_containment` field in the run's `state.json` records whether
containment was enforced and which hierarchy (`v2`/`v1`) was detected.

**"$HOME/.claude not found"** — you haven't run `claude` yet on this
machine. Run `claude --version` at least once so the directory is
created.

**Permission denied on `.leerie/`** — UID mismatch. The launcher passes
`--build-arg HOST_UID=$(id -u)` so the in-container `leerie` user matches
your host user. If you copied the image from another machine with a
different UID, rebuild: `nerdctl image rm leerie:<version>` and re-run
leerie.

**Slow `npm install` / `vitest`** on macOS — ensure Colima is using
VirtioFS (the documented setup uses `--mount-type virtiofs`). Bump the
VM's RAM if needed: `colima stop && colima start --cpu 6 --memory 12
--runtime containerd --mount-type virtiofs`.

**"$path may appear empty in the container"** warning (macOS) — Colima
only auto-shares paths under `/Users/$USER`. Edit
`~/.colima/default/colima.yaml`, add the path under `mounts:`, then
`colima restart`.

**Git push fails with `/opt/homebrew/bin/gh: command not found`** —
your `~/.gitconfig` has a credential helper line that hard-codes the
macOS Homebrew path for `gh`, but inside the Debian container `gh` is
at `/usr/bin/gh`. Older `gh auth setup-git` versions wrote the absolute
path; recent versions write the relative form `helper = !gh auth
git-credential` (uses `$PATH`). To fix, either re-run `gh auth
setup-git` on the host (overwrites with the relative form), or
manually edit `~/.gitconfig` to drop the `/opt/homebrew/bin/`
prefix from the `helper = !... gh auth git-credential` line.

**Git errors at run start when invoking leerie from a git worktree** —
if your repo cwd is itself a `git worktree add`-created worktree (not
the main checkout), the worktree's `.git` file points at a parent
path that lives outside the container's `/work` bind mount. Setup
fails with a "cannot access path" git error. Workaround: invoke leerie
from the main checkout, not from a worktree. (Leerie itself creates
worktrees under `.leerie/runs/<run-id>/worktrees/` inside the bind
mount — those work normally; this limitation only affects leerie being
*invoked from* a host-side worktree.)

## Uninstalling

```bash
# Remove the cached leerie image.
nerdctl image rm leerie:<version>   # or: nerdctl image rm $(nerdctl images -q leerie)

# Remove leerie itself.
rm -rf ~/.leerie
rm -f ~/.local/bin/leerie

# Optional: remove the runtime.
# macOS:
brew uninstall colima
rm -rf ~/.colima
# Linux: use your distro's package manager to remove containerd + nerdctl.
```
