# Installing Leerie

Leerie runs entirely inside a container — the Ctrl-C cleanup guarantee
comes from the kernel tearing down the container's PID namespace, not
Python heuristics (`DESIGN.md` §6, `IMPLEMENTATION.md` §0.5). Below:
one-time container-runtime setup per OS, then installing leerie itself.

## macOS

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

Auto-installs and starts Colima, sized to half your host's CPU/RAM
(clamped 2–8 cores / 4–16 GB — Colima's 2-CPU/2-GB default OOMs under
leerie's parallel-worker load). For a pre-installed runtime (CI /
dotfiles), pass `--no-runtime-install` (`LEERIE_NO_RUNTIME_INSTALL=1`):

```bash
brew install colima
colima start --runtime containerd --mount-type virtiofs --cpu 4 --memory 8
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash -s -- --no-runtime-install
```

Don't `brew install nerdctl` (Colima provides it; the launcher runs
`colima nerdctl install`). Resize: `colima stop && colima start --cpu 6
--memory 12 --runtime containerd --mount-type virtiofs`.

### Memory pressure: swap configuration

Colima's default VM has **zero swap**, so parallel workers can exhaust RAM
before the kernel reacts — the OOM killer hits `nerdctl`/`lima-guestagent`
first (`FATA[NNNN] exit status 255`, no diagnostic). **Fix:** 4 GB of swap
plus `vm.swappiness=10` via Colima's `provision:` hook — written
automatically on a fresh install (an existing `colima.yaml` is never
mutated; the installer logs the block to paste in instead):

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

Replace any `provision: null`/`provision: []` line in an existing
`colima.yaml` with the block above, then `colima stop && colima start`
(authoritative copy: `_runtime_colima_swap_yaml` in
`scripts/runtime-install.sh`). Verify: `colima ssh -- free -h` should show
`Swap: 4.0Gi 0B 4.0Gi`; only `colima delete` removes it.

**Bind-mount scope:** Colima auto-shares only `/Users/$USER` (leerie warns
at preflight if outside it). Widen via `mounts:` in `colima.yaml`, then
`colima restart`.

## Linux

Containerd runs natively — no VM, **rootless** (DESIGN §6): runtime,
workers, and cgroup containment live under your user's systemd slice, no
root daemon.

**Debian/Ubuntu** — the one-line installer auto-installs and verifies the
full rootless stack (containerd, RootlessKit, slirp4netns, uidmap, pinned
nerdctl v2.3.1, CNI plugins, BuildKit):

```bash
curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
```

**Fedora/RHEL and Arch** — auto-install isn't wired yet: install
prerequisites by hand, then do **Rootless mode (manual)** below and
re-run with `--no-runtime-install`.

- Fedora/RHEL: `sudo dnf install -y containerd rootlesskit slirp4netns shadow-utils`; Arch: `sudo pacman -S containerd nerdctl rootlesskit slirp4netns buildkit cni-plugins`

### Rootless mode (manual)

Exactly what the Debian/Ubuntu auto-install runs (versions pinned in
`scripts/runtime-install.sh`):

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

# 5. Subuid/subgid (idempotent — skip if already in /etc/subuid)
sudo usermod --add-subuids 100000-165535 --add-subgids 100000-165535 "$(id -un)"

# 6. Rootless containerd + BuildKit worker (as your user, NOT sudo);
#    install-buildkit-containerd is the containerd-worker variant leerie needs.
containerd-rootless-setuptool.sh install
CONTAINERD_NAMESPACE=default containerd-rootless-setuptool.sh install-buildkit-containerd
sudo loginctl enable-linger "$(id -un)"   # survive logout

# 7. Verify (both should succeed: "Hello from Docker!" and a worker row)
nerdctl run --rm hello-world
buildctl --addr unix:///run/user/$(id -u)/buildkit-default/buildkitd.sock debug workers
```

Then re-run the installer with `--no-runtime-install`.

**Optional: `gh`** enables automatic PR creation at the end of a run
(without it, leerie prints a `gh pr create` command to run manually).
Install from https://cli.github.com, then `gh auth login`.

## Fly.io runtime (optional)

`--runtime fly` (`LEERIE_RUNTIME=fly` / `runtime = fly` in `leerie.toml`)
routes each worker through Fly.io Machines instead of local `nerdctl`.
Prerequisites: `flyctl` authenticated (`flyctl auth login` — auto-installed
on first use, or `brew install flyctl`) and a billed Fly.io account (no
free tier). First use handles the rest: creates the Fly app
(`LEERIE_FLY_APP`/`--fly-app`), builds+pushes the image, and provisions a
Machine per worker.

**Disk sizing:** default 8 GB volume at `/work`; on `ENOSPC`, raise via `--fly-disk-gb 50` (or `FLY_VM_DISK_GB`/`fly_disk_gb`). **Orchestrator dies
mid-run:** `leerie finalize <run-id> --force` SSHes in and proceeds with
the normal push+PR flow (`IMPLEMENTATION.md` §7). **`--local-build`**
(rarely needed) builds locally instead — needs a host Docker daemon authed
to `registry.fly.io`; not nerdctl-in-Colima.

## EC2 runtime

`--runtime ec2` provisions an AWS EC2 instance, seeds it, runs the
orchestrator detached, and tears it down on exit — the EC2 counterpart to
`--runtime fly`. **AMI prerequisite:** `LEERIE_EC2_AMI` must name an AMI
with leerie's source baked in at `/opt/leerie-image/` (DESIGN §6);
building it (Packer / EC2 Image Builder) is operator-owned, out-of-band.

Prerequisites: AWS CLI v2 (`brew install awscli`; not auto-installed, it
commonly needs `sudo`) and credentials resolving via `aws sts
get-caller-identity` (preflight prints `aws sso login` if expired/missing)
— same precedence as the AWS CLI/SDKs: env vars > named profile > EC2
instance role; region: `AWS_REGION` > `AWS_DEFAULT_REGION` > profile
`region`. `LEERIE_AWS_REGION`/`LEERIE_AWS_PROFILE` are **leerie's own**
knobs for which region/profile it uses to provision machines.

Five `RunInstances` parameters have no defaults and `--runtime ec2` fails
fast if any is unresolved. **CLI flag > env var > `leerie.toml` key**:

| Var | CLI flag | `leerie.toml` key |
|---|---|---|
| `LEERIE_EC2_AMI` | `--ec2-ami` | `ec2_ami` |
| `LEERIE_EC2_INSTANCE_TYPE` | `--ec2-instance-type` | `ec2_instance_type` |
| `LEERIE_EC2_KEY_NAME` | `--ec2-key-name` | `ec2_key_name` |
| `LEERIE_EC2_SECURITY_GROUP` | `--ec2-security-group` | `ec2_security_group` |
| `LEERIE_EC2_SUBNET_ID` | `--ec2-subnet-id` | `ec2_subnet_id` |

```bash
export LEERIE_AWS_REGION=us-east-1
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
| `$(pwd)` (your repo) | `/work` | rw | Worktrees written under the repo, which stays clean. |
| Resolved host state dir | `/leerie-state` | rw | Default `$HOME/.leerie/<basename>/` (`LEERIE_STATE_DIR`); outside the repo, no `.gitignore` needed. |
| `$LEERIE_HOME` (leerie install) | `/opt/leerie-image` | ro | Edits picked up next run without a rebuild. |
| Scratch `~/.claude.json`/`.claude/`/credential | `/home/leerie/.claude*` | rw | `projects[]`-stripped; avoids a shared-file corruption race. Credential: token > Keychain > disk. |
| Scratch git/SSH/GPG config | `/home/leerie/.<same>` | rw | Private copies vanish on exit. |
| Each `--inspect-dir` path | `/inspect/<basename>` | ro | Read-only context for inspect-bucket workers. |

Container-side writes are lost on exit; `/leerie-state` is the source of
truth for run cost and structure.

## Troubleshooting

- **Skip runtime/CLI auto-install** — `--no-runtime-install` /
  `--no-claude-install` (`LEERIE_NO_RUNTIME_INSTALL=1`/
  `LEERIE_NO_CLAUDE_INSTALL=1`); both print a manual hint and exit 1.
- **Linux rootless install errors** — `buildctl`/`buildkitd` not found:
  install BuildKit (step 4); CNI plugin "bridge" missing: step 3;
  `rootlesskit`/`RootlessKit failed`: step 1, else subuid/subgid (step 5).
- **"Colima VM is not running"** — `colima start --runtime containerd
  --mount-type virtiofs --cpu 4 --memory 8`.
- **"nerdctl cannot reach the container runtime"** — macOS: restart with
  `--runtime containerd` (not `docker`). Linux: check `systemctl --user
  status containerd` and PATH.
- **`FATA[NNNN] exit status 255`** — Colima's `nerdctl`/`lima-guestagent`
  was OOM-killed (`colima ssh -- sudo dmesg | grep oom-killer`); add swap
  (*Memory pressure* above).
- **"worker cgroup containment could not be enabled"** — on rootless
  Linux + systemd + cgroup v2, confirm
  `.../user@$(id -u).service/cgroup.subtree_control` has `pids`/`memory`,
  else `--dangerously-allow-uncapped` (DESIGN §6).
- **"$HOME/.claude not found"** — run `claude --version` once first.
- **Permission denied on `.leerie/`** — UID mismatch after copying the
  image elsewhere; `nerdctl image rm leerie:<version>` and re-run.
- **Slow `npm install`/`vitest` on macOS** — confirm `--mount-type
  virtiofs`; see *bind-mount scope* above if a path appears empty.
- **`gh: command not found` on push** — `~/.gitconfig`'s helper hard-codes
  a macOS Homebrew path; `gh auth setup-git` on the host fixes it.
- **Invoking leerie from a git worktree** — its `.git` points outside
  `/work`; invoke from the main checkout instead.

## Uninstalling

```bash
nerdctl image rm $(nerdctl images -q leerie)
rm -rf ~/.leerie ~/.local/bin/leerie

# Optional: remove the runtime.
brew uninstall colima && rm -rf ~/.colima   # macOS
# Linux: use your distro's package manager to remove containerd + nerdctl.
```
