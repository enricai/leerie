#!/usr/bin/env bash
# runtime-install.sh — container-runtime install helpers (DESIGN §6).
#
# Shared between scripts/install.sh (the one-command installer) and the
# `leerie` launcher (which auto-installs on first run when missing). The
# logic was previously inlined in install.sh; extracting it lets the
# launcher invoke the same code path without duplicating it.
#
# Contract for callers:
#   - Source this file (do not exec).
#   - Caller has already detected the runtime is missing (and decided to
#     auto-install — i.e. NO_RUNTIME_INSTALL is false and `[ -t 0 ]` for
#     the launcher's TTY guard).
#   - Caller sets DRY_RUN (true/false). Defaults to false here.
#   - Caller invokes one of:
#       runtime_install_macos
#       runtime_install_linux
#     Returns 0 on success, 1 on failure with a printed error.
#   - Function names use an underscore prefix on the helpers so they
#     don't collide if the caller has its own `log`/`err`/`run` named
#     functions.

# --- defaults ------------------------------------------------------------

# Pinned nerdctl version used by the Linux Debian path. Matches the version
# documented in docs/INSTALL.md and previously hard-coded in install.sh.
NERDCTL_VERSION="${NERDCTL_VERSION:-2.3.1}"

# Pinned CNI plugins + BuildKit versions for the Debian rootless path. The
# rootless containerd stack these complete (containerd + rootlesskit come from
# apt; nerdctl from the pinned tarball above) is not fully installed by the
# rootless setuptool — CNI plugins and BuildKit binaries must be placed on the
# host separately (see runtime_install_linux). All three release URLs were
# verified to resolve. Update in lockstep with docs/INSTALL.md.
CNI_VERSION="${CNI_VERSION:-v1.9.1}"
BUILDKIT_VERSION="${BUILDKIT_VERSION:-v0.31.2}"

# DRY_RUN may be set by the caller (install.sh's --dry-run). Default off.
DRY_RUN="${DRY_RUN:-false}"

# --- helpers (underscore-prefixed to avoid caller collisions) -----------

_runtime_log() {
  printf 'runtime-install: %s\n' "$*"
}

_runtime_err() {
  printf 'runtime-install: error: %s\n' "$*" >&2
}

_runtime_run() {
  # Print the command, then run it — or just print it under --dry-run.
  printf '  $ %s\n' "$*"
  if [ "$DRY_RUN" = "false" ]; then
    "$@"
  fi
}

_runtime_have_runnable() {
  # `command -v` returns success for shimmed entries (pyenv) that can't
  # actually exec — invoke `--version` to confirm it really runs.
  "$1" --version >/dev/null 2>&1
}

# Emit one of debian | fedora | arch | unknown by reading /etc/os-release.
# Uses ID first, falls through to ID_LIKE so derivatives map to their
# parents (Pop!_OS → debian, AlmaLinux → fedora, Manjaro → arch).
_runtime_detect_distro() {
  if [ -r /etc/os-release ]; then
    # shellcheck disable=SC1091
    . /etc/os-release
    case " ${ID:-} ${ID_LIKE:-} " in
      *' debian '*|*' ubuntu '*) echo debian ;;
      *' fedora '*|*' rhel '*|*' centos '*) echo fedora ;;
      *' arch '*) echo arch ;;
      *) echo unknown ;;
    esac
  else
    echo unknown
  fi
}

# Map host architecture to nerdctl's release-asset suffix (amd64 | arm64).
_runtime_nerdctl_arch() {
  if command -v dpkg >/dev/null 2>&1; then
    dpkg --print-architecture
  else
    case "$(uname -m)" in
      x86_64|amd64) echo amd64 ;;
      aarch64|arm64) echo arm64 ;;
      *) echo unknown ;;
    esac
  fi
}

# Detect host CPU + RAM on macOS and emit `--cpu N --memory M` flags
# sized at half-of-host, clamped to [floor, ceiling]. Emits nothing on
# non-macOS (Colima is macOS-only — Linux runs containerd natively).
#
# Bounds rationale:
#   CPU 2..8 — leerie needs ≥2 cores for parallel workers; >8 is wasted
#     on dev workstations (the VM doesn't scale linearly past that).
#   RAM 4..16 GB — the Colima default of 2 GB OOMs leerie under concurrent
#     `claude -p` workers (~300 MB each) plus toolchain processes;
#     16 GB has ~60% headroom over the observed working-set of two
#     parallel leerie containers running ~8 implementers between them.
#
# Caller is expected to expand the result with intentional word-
# splitting: `colima start ... $size_flags`.
_runtime_colima_size_flags() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  local host_cpu host_mem_bytes host_mem_gb cpu mem
  host_cpu="$(sysctl -n hw.ncpu 2>/dev/null || echo 0)"
  host_mem_bytes="$(sysctl -n hw.memsize 2>/dev/null || echo 0)"
  host_mem_gb=$(( host_mem_bytes / 1073741824 ))   # bytes → GiB
  cpu=$(( host_cpu / 2 ))
  [ "$cpu" -lt 2 ] && cpu=2
  [ "$cpu" -gt 8 ] && cpu=8
  mem=$(( host_mem_gb / 2 ))
  [ "$mem" -lt 4 ] && mem=4
  [ "$mem" -gt 16 ] && mem=16
  printf -- "--cpu %d --memory %d" "$cpu" "$mem"
}

# Check the currently-configured Colima sizing against the auto-
# recommendation. If the running VM is materially undersized for
# parallel leerie workloads, log a one-line hint with the exact resize
# command. No-op on non-macOS or if config can't be read. Called only
# when the launcher decides to leave an already-running VM alone.
_runtime_check_colima_sizing() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  local cfg cur_cpu cur_mem rec_flags rec_cpu rec_mem
  cfg="$HOME/.colima/default/colima.yaml"
  [ -f "$cfg" ] || return 0
  # Authoritative: the YAML the user (or installer) set. Grep + awk
  # avoids a yq/python dependency; the two fields are flat top-level.
  cur_cpu="$(awk -F': *' '/^cpu:/{print $2; exit}' "$cfg" 2>/dev/null)"
  cur_mem="$(awk -F': *' '/^memory:/{print $2; exit}' "$cfg" 2>/dev/null)"
  rec_flags="$(_runtime_colima_size_flags)"
  [ -n "$rec_flags" ] || return 0
  # Parse "--cpu N --memory M" back into integers for comparison.
  # shellcheck disable=SC2086  # intentional word-split of flag string
  set -- $rec_flags
  rec_cpu="$2"
  rec_mem="$4"
  # Only warn if BOTH cpu and mem are below recommendation — otherwise
  # the user may have deliberately tuned one knob and we don't want
  # to be naggy about a half-match.
  if [ -n "$cur_cpu" ] && [ -n "$cur_mem" ] \
     && [ "$cur_cpu" -lt "$rec_cpu" ] && [ "$cur_mem" -lt "$rec_mem" ]; then
    _runtime_log "Colima is running with ${cur_cpu} cpu / ${cur_mem} GB."
    _runtime_log "  Parallel leerie runs benefit from ≥${rec_cpu} cpu / ${rec_mem} GB. To resize:"
    _runtime_log "    colima stop && colima start --runtime containerd --mount-type virtiofs ${rec_flags}"
  fi
}

# Emit the canonical leerie swap-provision YAML block. Single source of
# truth: both the fresh-install path (writes it into colima.yaml before
# first start) and the already-running hint path (paste this into the
# user's existing colima.yaml + restart) consume this function.
# Sentinel markers (`leerie:swap-provision-v1`) make the block idempotent
# across installer re-runs — grep for the marker and skip if present.
#
# The 4 GB swap is a safety net for transient memory spikes, not active
# paging — `vm.swappiness=10` (vs Linux default 60) keeps the kernel
# from reaching for swap until real RAM is genuinely exhausted. The
# guards inside the script are required because colima's `provision:`
# entries run on every boot.
_runtime_colima_swap_yaml() {
  cat <<'YAML'
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
YAML
}

# Ensure ~/.colima/default/colima.yaml carries leerie's swap-provision
# block. Called by the macOS install path *before* the first
# `colima start` so swap is live on the first boot.
#
# Decision: write only if the file does NOT exist (fresh user). If the
# file exists we don't mutate it — modifying a user's colima.yaml
# without their consent risks clobbering their tuning (custom mounts,
# CPU type, disk size, etc.). The hint path
# (_runtime_check_colima_swap) handles the existing-user case by
# logging the YAML block for them to paste in manually.
_runtime_install_colima_swap_yaml() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  local cfg cfg_dir
  cfg="$HOME/.colima/default/colima.yaml"
  cfg_dir="$(dirname "$cfg")"
  if [ -f "$cfg" ]; then
    return 0   # existing config — don't mutate
  fi
  _runtime_log "writing initial Colima config with swap provisioning (4 GB swap, swappiness 10)"
  if [ "$DRY_RUN" = "true" ]; then
    return 0
  fi
  mkdir -p "$cfg_dir"
  _runtime_colima_swap_yaml > "$cfg"
}

# Check whether the running Colima's config carries leerie's swap
# provisioning. If absent, log a one-line hint with the exact YAML
# block to paste in and the restart command. No automatic mutation,
# no automatic restart — running containers would die mid-flight.
# Called only when colima is already running (mirrors
# _runtime_check_colima_sizing's call pattern).
_runtime_check_colima_swap() {
  [ "$(uname -s)" = "Darwin" ] || return 0
  local cfg
  cfg="$HOME/.colima/default/colima.yaml"
  [ -f "$cfg" ] || return 0
  if grep -q "leerie:swap-provision-v1" "$cfg" 2>/dev/null; then
    return 0   # already provisioned
  fi
  _runtime_log "Colima is running without leerie's swap provisioning."
  _runtime_log "  Leerie's parallel-implementer workload can OOM the VM under heavy"
  _runtime_log "  test/build load. To add 4 GB of swap (recommended), paste the"
  _runtime_log "  block below into ~/.colima/default/colima.yaml replacing any"
  _runtime_log "  existing 'provision:' line, then 'colima stop && colima start':"
  _runtime_colima_swap_yaml | sed 's/^/    /' >&2
}

# --- public: macOS install (Colima via brew) ----------------------------

# Returns 0 on success, 1 on failure. Caller is responsible for the TTY
# guard before invoking (brew install may run sudo).
runtime_install_macos() {
  local size_flags
  size_flags="$(_runtime_colima_size_flags)"
  if _runtime_have_runnable colima; then
    # Already installed.
    if [ "$DRY_RUN" = "false" ] && ! colima status >/dev/null 2>&1; then
      # VM not running — install swap config (no-op if a colima.yaml
      # already exists; we don't mutate user configs) then start with
      # auto-sized resources. First boot runs the provision block.
      _runtime_install_colima_swap_yaml
      _runtime_log "starting Colima VM (first start may take 30-60s, sizing: ${size_flags:-default})"
      # shellcheck disable=SC2086  # intentional word-split of flag string
      _runtime_run colima start --runtime containerd --mount-type virtiofs $size_flags || return 1
    elif [ "$DRY_RUN" = "false" ]; then
      # VM is already running — leave it alone, but hint if undersized
      # or missing swap provisioning.
      _runtime_check_colima_sizing
      _runtime_check_colima_swap
    fi
    return 0
  fi
  if ! _runtime_have_runnable brew; then
    _runtime_err "Homebrew is needed to install Colima but isn't on PATH."
    _runtime_err "Install Homebrew from https://brew.sh, then re-run leerie."
    return 1
  fi
  _runtime_log "installing Colima via Homebrew"
  _runtime_run brew install colima || return 1
  if [ "$DRY_RUN" = "false" ]; then
    # Fresh install: drop our colima.yaml in place before first start
    # so the provision block runs on the first boot and swap is live
    # from day 1 with no follow-up restart.
    _runtime_install_colima_swap_yaml
    _runtime_log "starting Colima VM (first start may take 30-60s, sizing: ${size_flags:-default})"
    # shellcheck disable=SC2086  # intentional word-split of flag string
    _runtime_run colima start --runtime containerd --mount-type virtiofs $size_flags || return 1
  fi
  return 0
}

# --- public: Linux install (rootless containerd, Debian/Ubuntu) ---------
#
# Design (DESIGN §6; INSTALL.md "Rootless mode"): stand up a *working,
# reachable* rootless runtime, not just an enabled rootful daemon. The prior
# version installed only `containerd` (apt) + the bare `nerdctl` binary and
# reported success without ever verifying `nerdctl info` — which left a fresh
# host with an unreachable rootful socket, no CNI plugins (so `nerdctl run`
# failed on the missing bridge plugin), and no BuildKit (so leerie's image
# build failed on a missing `buildctl`/`buildkitd`). Those are the three walls
# a real fresh-Ubuntu install hit, in order.
#
# The `containerd-rootless-setuptool.sh install` flow needs a set of host
# prerequisites it does NOT install itself (it errors and exits if they're
# absent): rootlesskit + a port driver (slirp4netns), newuidmap/newgidmap
# (uidmap) plus /etc/subuid+/etc/subgid entries for the user, and a systemd
# user session (dbus-user-session). And the setuptool never installs CNI
# plugins or the BuildKit binaries at all. So the Debian path installs all of
# these explicitly, runs the rootless setup as the non-root user, and verifies
# reachability end-to-end.
#
# Scope: Debian/Ubuntu only. Fedora/Arch are not auto-installed here — their
# package-name equivalents are unverified, so they fall back to a docs hint
# rather than a possibly-half-working auto-install that reports false success.

# Install the runtime + rootless prerequisites via apt (the exact package set
# proven to bring rootless containerd up on Ubuntu):
#   containerd        — the container runtime (rootless mode reuses the binary)
#   rootlesskit       — the user-namespace "fake root" the rootless daemon runs in
#   slirp4netns       — userspace networking for the rootless netns
#   uidmap            — newuidmap/newgidmap (setuid) for the subuid/subgid map
#   dbus-user-session — the per-user systemd/D-Bus session `systemctl --user` needs
_runtime_linux_apt_prereqs() {
  _runtime_log "installing containerd + rootless prerequisites via apt-get"
  _runtime_run sudo apt-get update || return 1
  _runtime_run sudo apt-get install -y \
    containerd rootlesskit slirp4netns uidmap dbus-user-session || return 1
  return 0
}

# Install the pinned upstream nerdctl binary into /usr/local/bin. The tarball
# also carries containerd-rootless-setuptool.sh + containerd-rootless.sh, which
# the rootless setup step below needs on PATH.
_runtime_linux_install_nerdctl() {
  local arch="$1" url
  if [ "$arch" = "unknown" ]; then
    _runtime_err "could not detect host arch for the nerdctl binary download."
    _runtime_err "Install nerdctl manually from https://github.com/containerd/nerdctl/releases"
    return 1
  fi
  url="https://github.com/containerd/nerdctl/releases/download/v${NERDCTL_VERSION}/nerdctl-${NERDCTL_VERSION}-linux-${arch}.tar.gz"
  _runtime_log "installing nerdctl ${NERDCTL_VERSION} (linux-${arch}) from upstream"
  if [ "$DRY_RUN" = "false" ]; then
    curl -L "$url" | sudo tar -C /usr/local/bin -xz || return 1
  else
    printf '  $ curl -L %s | sudo tar -C /usr/local/bin -xz\n' "$url"
  fi
  return 0
}

# Install the CNI plugins into /opt/cni/bin — the CNI_PATH nerdctl looks in.
# The rootless setuptool never installs these; without them `nerdctl run`
# fails with `needs CNI plugin "bridge" to be installed in CNI_PATH`.
_runtime_linux_install_cni() {
  local arch="$1" url
  if [ "$arch" = "unknown" ]; then
    _runtime_err "could not detect host arch for the CNI plugins download."
    return 1
  fi
  url="https://github.com/containernetworking/plugins/releases/download/${CNI_VERSION}/cni-plugins-linux-${arch}-${CNI_VERSION}.tgz"
  _runtime_log "installing CNI plugins ${CNI_VERSION} (linux-${arch}) into /opt/cni/bin"
  if [ "$DRY_RUN" = "false" ]; then
    sudo mkdir -p /opt/cni/bin || return 1
    curl -L "$url" | sudo tar -C /opt/cni/bin -xz || return 1
  else
    printf '  $ sudo mkdir -p /opt/cni/bin\n'
    printf '  $ curl -L %s | sudo tar -C /opt/cni/bin -xz\n' "$url"
  fi
  return 0
}

# Install the BuildKit binaries into ~/.local (bin/buildctl, bin/buildkitd) so
# they land on the user's PATH. `containerd-rootless-setuptool.sh
# install-buildkit-containerd` refuses (exit 1) unless `buildkitd` is already
# on PATH, so this must precede the rootless-buildkit step.
_runtime_linux_install_buildkit() {
  local arch="$1" url
  if [ "$arch" = "unknown" ]; then
    _runtime_err "could not detect host arch for the BuildKit download."
    return 1
  fi
  url="https://github.com/moby/buildkit/releases/download/${BUILDKIT_VERSION}/buildkit-${BUILDKIT_VERSION}.linux-${arch}.tar.gz"
  _runtime_log "installing BuildKit ${BUILDKIT_VERSION} (linux-${arch}) into ~/.local"
  if [ "$DRY_RUN" = "false" ]; then
    curl -L "$url" | tar -C "$HOME/.local" -xz || return 1
  else
    printf '  $ curl -L %s | tar -C %s/.local -xz\n' "$url" "$HOME"
  fi
  return 0
}

# Ensure the invoking user has subordinate uid/gid ranges. RootlessKit (which
# the rootless setuptool exercises) needs /etc/subuid + /etc/subgid entries of
# at least 65536 ids for the user; Ubuntu does not guarantee they're populated
# on every image. Idempotent: a no-op when an entry already exists.
_runtime_linux_ensure_subid() {
  local user
  user="$(id -un)"
  if [ "$DRY_RUN" = "false" ] && grep -q "^${user}:" /etc/subuid 2>/dev/null; then
    return 0  # already configured
  fi
  _runtime_log "ensuring subuid/subgid ranges for ${user} (RootlessKit prerequisite)"
  _runtime_run sudo usermod \
    --add-subuids 100000-165535 --add-subgids 100000-165535 "$user" || return 1
  return 0
}

# Run the rootless containerd setup as the invoking (non-root) user — the
# setuptool installs a `systemctl --user` unit and must NOT run under sudo.
# Then install the BuildKit containerd-worker (the variant leerie needs: its
# ensure_base_in_buildkit_ns copies the base image into the `buildkit`
# containerd namespace, which the OCI-worker variant can't read). Finally
# enable linger so the user services survive logout.
_runtime_linux_rootless_setup() {
  _runtime_log "setting up rootless containerd (containerd-rootless-setuptool.sh install)"
  _runtime_run containerd-rootless-setuptool.sh install || return 1
  _runtime_log "installing the rootless BuildKit containerd-worker"
  _runtime_run env CONTAINERD_NAMESPACE=default \
    containerd-rootless-setuptool.sh install-buildkit-containerd || return 1
  _runtime_log "enabling linger so rootless services survive logout"
  _runtime_run sudo loginctl enable-linger "$(id -un)" || return 1
  return 0
}

# Returns 0 on success, 1 on failure. Caller is responsible for the TTY
# guard before invoking (apt-get / sudo all prompt for the sudo password).
runtime_install_linux() {
  if _runtime_have_runnable nerdctl && nerdctl info >/dev/null 2>&1; then
    return 0  # already set up and reachable
  fi
  local distro arch
  distro="$(_runtime_detect_distro)"
  arch="$(_runtime_nerdctl_arch)"

  case "$distro" in
    debian)
      _runtime_linux_apt_prereqs || return 1
      _runtime_linux_install_nerdctl "$arch" || return 1
      _runtime_linux_install_cni "$arch" || return 1
      _runtime_linux_install_buildkit "$arch" || return 1
      _runtime_linux_ensure_subid || return 1
      _runtime_linux_rootless_setup || return 1
      ;;
    fedora|arch)
      # Not auto-installed yet — the apt-equivalent package names for the
      # rootless prerequisites are unverified on these distros, and a
      # half-working auto-install that reports success is worse than an honest
      # hint. See docs/INSTALL.md "Rootless mode" for the manual steps.
      _runtime_err "auto-install of the rootless runtime is currently"
      _runtime_err "Debian/Ubuntu-only (detected: ${distro})."
      _runtime_err "Set up rootless containerd manually — see docs/INSTALL.md"
      _runtime_err "'Rootless mode' — or pass --no-runtime-install to skip."
      return 1
      ;;
    *)
      _runtime_err "unsupported Linux distro (detected: ${distro}). Auto-install"
      _runtime_err "of the rootless runtime currently supports debian/ubuntu only."
      _runtime_err "Set up rootless containerd manually (see docs/INSTALL.md) or"
      _runtime_err "pass --no-runtime-install to skip the auto-install step."
      return 1
      ;;
  esac

  # Verify end-to-end. The whole point of this rework: never report success on
  # an unreachable runtime. Under --dry-run we can't actually probe, so skip.
  if [ "$DRY_RUN" = "false" ]; then
    _runtime_log "verifying the runtime is reachable (nerdctl info)"
    if ! nerdctl info >/dev/null 2>&1; then
      _runtime_err "rootless containerd was set up but 'nerdctl info' still fails."
      _runtime_err "Ensure ~/.local/bin and /usr/local/bin are on your PATH, then"
      _runtime_err "check: systemctl --user status containerd"
      _runtime_err "See docs/INSTALL.md 'Rootless mode' for the manual steps."
      return 1
    fi
  fi
  return 0
}
