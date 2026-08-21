# Installing Leerie

Leerie runs entirely inside a container. The cleanup guarantee — when you
Ctrl-C, every `claude -p` worker and everything it spawned is reaped — comes
from the kernel tearing down the container's PID namespace, not from Python
heuristics. See [`DESIGN.md` §6 *Worker subtree
termination*](DESIGN.md) and [`IMPLEMENTATION.md` §0.5 *Container
shape*](IMPLEMENTATION.md) for the reasoning.

This document covers one-time container-runtime setup per OS, then
installing leerie itself.

## macOS

The one-line installer auto-installs and starts Colima (`brew install
colima` + `colima start --runtime containerd --mount-type virtiofs`), sized
to half your host's CPU/RAM (clamped 2–8 cores / 4–16 GB — Colima's 2-CPU /
2-GB default OOMs under leerie's parallel-worker load).

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

To install the runtime yourself first (common in CI / dotfiles setups),
pass `--no-runtime-install` (or `LEERIE_NO_RUNTIME_INSTALL=1`):

```bash
brew install colima
# --cpu/--memory: half your host CPU/RAM, bounded 2-8 / 4-16 GB.
colima start --runtime containerd --mount-type virtiofs --cpu 4 --memory 8
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

Notes:

- **Do not** `brew install nerdctl` — it requires Linux. Colima provides
  nerdctl inside its VM and the launcher runs `colima nerdctl install`
  (a host-side shim) automatically.
- `--mount-type virtiofs` gives the fastest mount and correct bind-mount UID
  semantics; it's the default on recent Colima.
- The VM persists across reboots (`colima start` brings it back). Autostart
  at login: `brew services start colima`.
- To resize an already-running VM: `colima stop && colima start --cpu 6
  --memory 12 --runtime containerd --mount-type virtiofs`.

### Memory pressure: swap configuration

Colima's default VM has **zero swap**, so leerie's parallel workers can
exhaust RAM faster than the kernel can react — the OOM killer then hits
`nerdctl`/`lima-guestagent` first, which manifests as `FATA[NNNN] exit
status 255` with no orchestrator diagnostic.

**The fix:** 4 GB of swap plus `vm.swappiness=10`, applied via Colima's
`provision:` hook. On a fresh install the leerie installer writes this for
you automatically (only when `~/.colima/default/colima.yaml` doesn't already
exist — an existing file is never mutated; the installer instead logs the
block to paste in).

To add it to an existing `colima.yaml` by hand, replace any `provision:
null`/`provision: []` line with:

```yaml
# leerie:swap-provision-v1 BEGIN
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

then `colima stop && colima start` to apply. Authoritative copy of this
block lives in `_runtime_colima_swap_yaml` in `scripts/runtime-install.sh` —
it must match byte-for-byte. Verify with:

```bash
colima ssh -- free -h                # Swap: 4.0Gi   0B   4.0Gi
colima ssh -- sysctl vm.swappiness   # vm.swappiness = 10
```

The swapfile lives on Colima's persistent VM disk and survives `colima
stop`/`start`; only `colima delete` removes it.

### macOS-specific: bind-mount scope

Colima auto-shares only paths under `/Users/$USER`. A path outside that
range appears as an *empty* directory inside the container, with no error —
leerie's launcher warns at preflight if `$USER_REPO` or any `--inspect-dir`
falls outside `/Users/$USER`. To widen it: edit
`~/.colima/default/colima.yaml`, add the path under `mounts:`, then `colima
restart`.

## Linux

Containerd runs natively — no VM needed. Leerie runs it **rootless**
(DESIGN §6 *Rootless exception*): runtime, workers, and cgroup containment
all live under your user's systemd slice, no root daemon required.

On **Debian/Ubuntu**, the one-line installer auto-installs and verifies the
full rootless stack (containerd, RootlessKit, slirp4netns, uidmap, pinned
nerdctl v2.3.1, CNI plugins, BuildKit):

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

On **Fedora/RHEL and Arch**, auto-install isn't wired yet — do the
**Rootless mode** steps below by hand (swap `apt-get` for `dnf`/`pacman`),
then re-run with `--no-runtime-install`:

- Fedora/RHEL: `sudo dnf install -y containerd rootlesskit slirp4netns shadow-utils`
- Arch: `sudo pacman -S containerd nerdctl rootlesskit slirp4netns buildkit cni-plugins`

### Rootless mode (manual)

The sequence below is exactly what the Debian/Ubuntu auto-install runs.
Versions are pinned; bump them together with `scripts/runtime-install.sh`.

```bash
# 1. Runtime + rootless prerequisites
sudo apt-get update
sudo apt-get install -y containerd rootlesskit slirp4netns uidmap dbus-user-session

# 2. nerdctl (also ships containerd-rootless-setuptool.sh, used in step 6)
NERDCTL_VERSION=2.3.1
ARCH="$(dpkg --print-architecture 2>/dev/null || uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')"
curl -L "https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${ARCH}.tar.gz" \
  | sudo tar -C /usr/local/bin -xz

# 3. CNI plugins -> /opt/cni/bin (nerdctl run needs these)
CNI_VERSION=v1.9.1
sudo mkdir -p /opt/cni/bin
curl -L "https://github.com/containernetworking/plugins/releases/download/${CNI_VERSION}/cni-plugins-linux-${ARCH}-${CNI_VERSION}.tgz" \
  | sudo tar -C /opt/cni/bin -xz

# 4. BuildKit -> ~/.local (must precede step 6's install-buildkit-containerd)
BUILDKIT_VERSION=v0.31.2
curl -L "https://github.com/moby/buildkit/releases/download/${BUILDKIT_VERSION}/buildkit-${BUILDKIT_VERSION}.linux-${ARCH}.tar.gz" \
  | tar -C "$HOME/.local" -xz
command -v buildkitd   # must print a path before continuing

# 5. Subuid/subgid (idempotent — skip if `grep "^$(id -un):" /etc/subuid` already matches)
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$(id -un)"

# 6. Rootless containerd + BuildKit worker (run as your user, NOT sudo).
#    install-buildkit-containerd is the containerd-worker variant leerie
#    needs: its ensure_base_in_buildkit_ns copies the base image into the
#    buildkit containerd namespace, which the OCI-worker variant can't read.
containerd-rootless-setuptool.sh install
CONTAINERD_NAMESPACE=default containerd-rootless-setuptool.sh install-buildkit-containerd
sudo loginctl enable-linger "$(id -un)"   # survive logout

# 7. Verify
nerdctl run --rm hello-world
buildctl --addr unix:///run/user/$(id -u)/buildkit-default/buildkitd.sock debug workers
```

Both step-7 commands should succeed (a "Hello from Docker!" and a worker
row). Then re-run the installer with `--no-runtime-install`.

## Verifying the runtime

```bash
nerdctl run --rm hello-world
```

You should see "Hello from Docker!" (containerd uses the same image format).
If this fails, leerie will too.

## Optional host tools

**`gh` (GitHub CLI)** — enables automatic PR creation at the end of a run.
Without it, leerie pushes the run branch and prints a `gh pr create`
command to run manually. Install from https://cli.github.com, then `gh auth
login`.

## Fly.io runtime (optional)

`--runtime fly` (or `LEERIE_RUNTIME=fly` / `runtime = fly` in
`leerie.toml`) routes each worker through Fly.io Machines instead of local
`nerdctl` — no local container runtime needed.

Prerequisites:

1. `flyctl` installed and authenticated (`flyctl auth login`) — the
   launcher auto-installs it on first `--runtime fly` invocation if
   missing. Install manually from https://fly.io/docs/flyctl/install/ or
   `brew install flyctl`.
2. A Fly.io account with billing set up (Machines bill per-second; no
   free tier for this workload).

The launcher then handles everything else on first use: creates the Fly app
(set its name via `LEERIE_FLY_APP` / `--fly-app`, globally unique),
builds+pushes the leerie image on Fly's remote builder (~3-5 min first
time, cached after), and provisions a Machine per worker.

**Disk sizing.** Each run gets a per-machine Fly volume (default 8 GB) at
`/work`. On `ENOSPC: no space left on device`, raise it:

```bash
leerie 'task' --runtime fly --fly-disk-gb 50   # or FLY_VM_DISK_GB=50, or fly_disk_gb in leerie.toml
```

The volume survives `machine stop` (so paused runs keep their state) and is
destroyed with the machine on clean exit or `leerie kill`.

**Recovery if the orchestrator dies mid-run.** `leerie finalize <run-id>
--force` SSHes in, verifies the orchestrator is dead, patches
`finished_at`, and proceeds with the normal fetch + push + PR flow. Refuses
if the orchestrator is still alive. See `docs/IMPLEMENTATION.md` §7
*Detached run finalization*.

**`--local-build`** (most users should not use this) builds the image
locally instead of on Fly's remote builder. Only works when your host
Docker daemon can authenticate to `registry.fly.io` — Docker Desktop on
macOS, or Docker-on-Linux with `flyctl auth docker` run. It does **not**
work with nerdctl-in-Colima (no macOS Keychain access). Use the default
remote-builder path unless you have a specific reason not to.

## EC2 runtime

`--runtime ec2` provisions an AWS EC2 instance, seeds it, runs the
orchestrator detached, and tears it down on exit — the EC2 counterpart to
`--runtime fly`.

> **AMI prerequisite:** `LEERIE_EC2_AMI` must name an AMI with the leerie
> orchestrator source already baked in at `/opt/leerie-image/` (DESIGN §6
> *EC2 runtime lifecycle*, "Image delivery"). Building that AMI (Packer /
> EC2 Image Builder) is an operator-owned, out-of-band step — leerie does
> not build or publish one.

### Prerequisites

1. AWS CLI v2 installed (not auto-installed — official installers commonly
   need `sudo`). Install from
   https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html
   or `brew install awscli`.
2. AWS credentials that resolve via `aws sts get-caller-identity`. If
   expired/missing, the preflight prints the `aws sso login` hint and exits.

### Credential resolution

Same precedence as the AWS CLI/SDKs: env vars
(`AWS_ACCESS_KEY_ID`/`AWS_SECRET_ACCESS_KEY`) > named profile
(`AWS_PROFILE`, static or SSO) > EC2 instance role (only meaningful once
already running on an instance). Region: `AWS_REGION` > `AWS_DEFAULT_REGION`
> the profile's `region` key > actionable error.

`LEERIE_AWS_REGION`/`LEERIE_AWS_PROFILE` (also `--aws-region`/
`--aws-profile`, or `leerie.toml`) are **leerie's own** knobs for which
region/profile *leerie* uses to provision `--runtime ec2` machines —
distinct from the SDK's own `AWS_REGION`/`AWS_PROFILE` credential-chain
vars, which resolve independently:

```bash
export LEERIE_AWS_REGION=us-east-1
export LEERIE_AWS_PROFILE=my-aws-profile
leerie "task" --runtime ec2 --aws-region us-east-1 --aws-profile my-aws-profile
```

### Required instance-shape vars

Five vars name the AWS `RunInstances` parameters — no defaults exist, since
these describe AWS account resources leerie cannot choose for you.
`--runtime ec2` fails fast (before any AWS API call) if any is unresolved.
Each follows the usual **CLI flag > env var > `leerie.toml` key** precedence:

| Var | CLI flag | `leerie.toml` key | Meaning |
|---|---|---|---|
| `LEERIE_EC2_AMI` | `--ec2-ami` | `ec2_ami` | AMI id to launch. |
| `LEERIE_EC2_INSTANCE_TYPE` | `--ec2-instance-type` | `ec2_instance_type` | EC2 instance type (e.g. `t3.large`). |
| `LEERIE_EC2_KEY_NAME` | `--ec2-key-name` | `ec2_key_name` | EC2 key-pair name for SSH access. |
| `LEERIE_EC2_SECURITY_GROUP` | `--ec2-security-group` | `ec2_security_group` | Security group id to attach. |
| `LEERIE_EC2_SUBNET_ID` | `--ec2-subnet-id` | `ec2_subnet_id` | Subnet id to launch into. |

```bash
export LEERIE_EC2_AMI=ami-0abcdef1234567890
export LEERIE_EC2_INSTANCE_TYPE=t3.large
export LEERIE_EC2_KEY_NAME=my-ec2-keypair
export LEERIE_EC2_SECURITY_GROUP=sg-0123456789abcdef0
export LEERIE_EC2_SUBNET_ID=subnet-0123456789abcdef0
leerie "task" --runtime ec2
```

## What leerie mounts into the container

| Host path | Container path | Mode | Purpose |
|---|---|---|---|
| `$(pwd)` (your repo) | `/work` | rw | Leerie operates here; worktrees are written under the repo, which itself stays clean. |
| Resolved host state dir | `/leerie-state` | rw | Per-repo run state (`state.json`, `runs/`, `logs/`, worktrees). Defaults to `$HOME/.leerie/<basename>/`; override via `LEERIE_STATE_DIR` / `state_dir` / `--state-dir`. Outside the repo, so no `.gitignore` entry is needed; `resume` works because state persists here across container runs. |
| `$LEERIE_HOME` (leerie install) | `/opt/leerie-image` | ro | Leerie's source and Dockerfile — edits are picked up next run without a rebuild. |
| Per-run scratch copy of `~/.claude.json` | `/home/leerie/.claude.json` | rw | Private, `projects[]`-stripped copy — the shared host file is never mounted directly (avoids a documented `claude-code` corruption race). |
| Per-run scratch copy of `~/.claude/` | `/home/leerie/.claude` | rw | Bulky/history paths skipped; CLI capability dirs (agents, skills, commands, hooks, plugins, settings) ride along. |
| Resolved Claude credential → staged `.claude/.credentials.json` | `/home/leerie/.claude/.credentials.json` | rw | Preferring `$CLAUDE_CODE_OAUTH_TOKEN`, then macOS Keychain, then `~/.claude/.credentials.json` on disk. See `docs/DESIGN.md` §6 *Credential strategy*. |
| Per-run scratch copies of git/SSH/GPG config (`~/.gitconfig*`, `~/.ssh/`, `~/.gnupg/`, etc., agent sockets excluded) | `/home/leerie/.<same>` | rw | Lets a worker push/sign against private copies that vanish on exit. |
| Each `--inspect-dir` path | `/inspect/<basename>` | ro | Extra read-only context for inspect-bucket workers (classifier, planner, reconciler, plan_overlap_judge, provision). |

Every container sees a private copy of your config at the paths the CLI and
git already expect; container-side writes are intentionally lost on exit.
Leerie's own telemetry under `/leerie-state` is the source of truth for run
cost and structure.

## Troubleshooting

- **Skip runtime/CLI auto-install** — `--no-runtime-install` /
  `LEERIE_NO_RUNTIME_INSTALL=1`, or `--no-claude-install` /
  `LEERIE_NO_CLAUDE_INSTALL=1`. Both fall back to printing a manual hint
  and exiting 1.
- **Linux: `buildctl`/`buildkitd` not found** — install BuildKit (step 4
  above) before running `install-buildkit-containerd` (step 6); the
  setuptool refuses unless `buildkitd` is already on PATH.
- **Linux: `needs CNI plugin "bridge"...`** — install CNI plugins (step 3).
- **Linux: `rootlesskit: not found` / `RootlessKit failed`** — install
  `rootlesskit slirp4netns uidmap dbus-user-session` (step 1); if it
  persists, check subuid/subgid ranges (step 5).
- **"Colima VM is not running"** — `colima start --runtime containerd
  --mount-type virtiofs --cpu 4 --memory 8` (sizing per the macOS section).
- **"nerdctl cannot reach the container runtime"** — on macOS, restart
  Colima with `--runtime containerd` (the default `docker` runtime won't
  work). On Linux, check `systemctl --user status containerd` (the *user*
  service, not system) and that `~/.local/bin`/`/usr/local/bin` are on PATH.
- **`FATA[NNNN] exit status 255` with no diagnostic** — Colima's
  `nerdctl`/`lima-guestagent` was OOM-killed in the VM; check `colima ssh --
  sudo dmesg | grep oom-killer`. Fix: add swap (see *Memory pressure* above).
- **"worker cgroup containment could not be enabled"** — leerie refuses to
  run workers without enforceable per-worker memory/PID limits (DESIGN §6
  *Memory containment*), enforced by a broker
  (`scripts/cgroup-broker.py`). Common causes: no usable cgroup hierarchy;
  on rootless Linux + systemd + cgroup v2, the broker anchors at the
  systemd-delegated user slice — no host reconfig needed, but confirm
  `/sys/fs/cgroup/user.slice/user-$(id -u).slice/user@$(id -u).service/cgroup.subtree_control`
  contains `pids` and `memory`; on non-systemd/cgroup v1 hosts there's no
  equivalent, so pass `--dangerously-allow-uncapped` to proceed without
  containment (workers can then exhaust the VM's PID table). `state.json`'s
  `cgroup_containment` field records what was detected/enforced.
- **"$HOME/.claude not found"** — run `claude --version` once first.
- **Permission denied on `.leerie/`** — UID mismatch after copying the
  image from another machine; `nerdctl image rm leerie:<version>` and
  re-run leerie (it rebuilds with your host UID via `--build-arg HOST_UID`).
- **Slow `npm install`/`vitest` on macOS** — confirm Colima uses
  `--mount-type virtiofs`; bump VM RAM if needed.
- **"$path may appear empty in the container" (macOS)** — outside
  Colima's auto-shared `/Users/$USER` scope; add it under `mounts:` in
  `~/.colima/default/colima.yaml` and `colima restart`.
- **Git push fails with `.../gh: command not found`** — `~/.gitconfig`'s
  credential helper hard-codes a macOS Homebrew path for `gh`, which
  doesn't exist in the Debian container. Re-run `gh auth setup-git` on the
  host (writes the `$PATH`-relative form), or edit the helper line by hand.
- **Git errors invoking leerie from a git worktree** — if your repo cwd is
  itself a `git worktree add` checkout, its `.git` file points outside the
  container's `/work` mount. Invoke leerie from the main checkout instead.

## Uninstalling

```bash
nerdctl image rm leerie:<version>   # or: nerdctl image rm $(nerdctl images -q leerie)
rm -rf ~/.leerie
rm -f ~/.local/bin/leerie

# Optional: remove the runtime.
brew uninstall colima && rm -rf ~/.colima   # macOS
# Linux: use your distro's package manager to remove containerd + nerdctl.
```
