#!/usr/bin/env bash
# scripts/remote/ec2-bake.sh — runs ON the stock Debian 13 bake instance (as
# root) and reproduces the repo Dockerfile so the resulting AMI matches the
# container image. Shipped by ensure_ami (ec2-ami.sh); never run on the host.
#
# Every package list and pin is read from the Dockerfile shipped alongside it
# (single source of truth) — the Dockerfile's hash is what keys the AMI cache,
# so a hand-copied pin here would silently go stale.
#
# Input: $LEERIE_BAKE_DIR (default /tmp/leerie-bake) holding Dockerfile,
# requirements.txt and the directories the Dockerfile COPYs into
# /opt/leerie-image/. Optional: LEERIE_UID / LEERIE_GID (default 1001), and
# LEERIE_BAKE_REGION (picks the regional SSM-agent bucket).
#
# No SSH key is baked in: ec2-user is made cloud-init's default user, so each
# instance launched from the AMI gets its own launch key pair at boot.
#
# Not reproduced: the crashpad stub (a container-only Chromium workaround) and
# the rootless-containerd ownership workarounds (Dockerfile:235-241). On EC2
# the orchestrator drops privilege with a real uid switch, i.e. the rootful
# semantics, so dirs are owned by leerie literally — and since
# container-entry.sh never runs on an instance, this script applies that
# ownership itself.
set -euo pipefail

BAKE_DIR="${LEERIE_BAKE_DIR:-/tmp/leerie-bake}"
DOCKERFILE="$BAKE_DIR/Dockerfile"
LEERIE_UID="${LEERIE_UID:-1001}"
LEERIE_GID="${LEERIE_GID:-1001}"
export DEBIAN_FRONTEND=noninteractive

log() { printf '[leerie-bake] %s\n' "$*" >&2; }

[ -f "$DOCKERFILE" ] || { log "missing $DOCKERFILE"; exit 1; }

# Packages named by every `apt-get install ... \` continuation block.
dockerfile_apt_packages() {
  awk '
    /apt-get install -y --no-install-recommends[[:space:]]*\\$/ { inblk = 1; next }
    inblk && /^[[:space:]]*&&/ { inblk = 0; next }
    inblk { gsub(/\\/, ""); print }
  ' "$DOCKERFILE" | tr -s ' \t' '\n' | grep -v '^$'
}

# The Dockerfile's ENV lines as KEY=VALUE, $PATH expanded against Debian's
# base PATH. A container inherits these; an instance does not, so the EC2
# launch wrapper merges this file into the orchestrator's environment.
LEERIE_ENV_FILE=/opt/leerie-image/leerie.env
BASE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
dockerfile_env() {
  local line
  sed -n 's/^ENV \([A-Za-z_][A-Za-z0-9_]*\)=\(.*\)$/\1=\2/p' "$DOCKERFILE" \
    | while IFS= read -r line; do printf '%s\n' "${line//\$PATH/$BASE_PATH}"; done
}

MISE_VERSION="$(sed -n 's/^ARG MISE_VERSION=//p' "$DOCKERFILE" | head -n1)"
CLAUDE_SPEC="$(sed -n "s/.*npm install -g '\\(@anthropic-ai\\/claude-code@[^']*\\)'.*/\\1/p" "$DOCKERFILE" | head -n1)"
if [ -z "$MISE_VERSION" ] || [ -z "$CLAUDE_SPEC" ]; then log "could not derive pins from Dockerfile"; exit 1; fi
if [ "$(dockerfile_env | wc -l)" -ne "$(grep -c '^ENV ' "$DOCKERFILE")" ]; then
  log "an ENV line in the Dockerfile is not of the single KEY=VALUE form"; exit 1
fi
mkdir -p /opt/leerie-image
dockerfile_env > "$LEERIE_ENV_FILE"

# --- apt base packages (base + gh prerequisites + chromium blocks) ---
mapfile -t APT_PKGS < <(dockerfile_apt_packages)
log "apt packages: ${APT_PKGS[*]}"
apt-get update
apt-get install -y --no-install-recommends "${APT_PKGS[@]}"

# --- python runtime deps (PEP 668 on Debian 13) ---
pip3 install --break-system-packages --no-cache-dir -r "$BAKE_DIR/requirements.txt"

# --- GitHub CLI apt repo ---
mkdir -p /etc/apt/keyrings
curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
  | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg
chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
  > /etc/apt/sources.list.d/github-cli.list
apt-get update
apt-get install -y --no-install-recommends gh
rm -rf /var/lib/apt/lists/*

# --- chromium flags (line taken verbatim from the Dockerfile) ---
mkdir -p /etc/chromium.d
grep -m1 "^RUN echo 'CHROMIUM_FLAGS=" "$DOCKERFILE" | sed "s/^RUN echo '//; s/' *\\\\\$//" \
  > /etc/chromium.d/leerie-container-flags

# --- leerie user ---
# Before mise: its data dir (MISE_DATA_DIR) is under /home/leerie, and a home
# that already exists makes `useradd -m` skip the skeleton files.
if ! getent group "$LEERIE_GID" >/dev/null 2>&1; then
  groupadd -g "$LEERIE_GID" leerie
fi
useradd -u "$LEERIE_UID" -g "$LEERIE_GID" -m -s /bin/bash leerie

# --- mise + system-wide LTS runtimes ---
curl -fsSL https://mise.run | MISE_VERSION="$MISE_VERSION" sh
mv /root/.local/bin/mise /usr/local/bin/mise
rm -rf /root/.local /root/.config /root/.cache

set -a
# shellcheck disable=SC1090
. "$LEERIE_ENV_FILE"
set +a
mise install --system node@lts python@3.12

node_dir="$(find /usr/local/share/mise/installs/node -mindepth 1 -maxdepth 1 -type d | head -n1)/"
ln -sfn "${node_dir%/}" /usr/local/share/mise/installs/node/lts-current

# Login shells (an operator's `ssh` + `sudo -iu leerie`) get the same set.
printf 'set -a\n. %s\nset +a\n' "$LEERIE_ENV_FILE" > /etc/profile.d/leerie.sh

# --- Claude Code CLI (pinned spec comes from the Dockerfile) ---
export PATH=/usr/local/share/mise/installs/node/lts-current/bin:$PATH
npm install -g "$CLAUDE_SPEC"

git config --system --add safe.directory '*'

mkdir -p /inspect
chown "leerie:$LEERIE_GID" /inspect

mkdir -p /home/leerie/.local/share/mise \
         /home/leerie/.local/state/mise \
         /home/leerie/.cache/leerie/pnpm-store \
         /home/leerie/.cache/leerie/pip \
         /home/leerie/.cache/leerie/go-mod \
         /home/leerie/.cache/leerie/cargo \
         /home/leerie/.cache/leerie/corepack \
         /home/leerie/.cache/rubocop_cache \
         /home/leerie/.cache/selenium \
         /home/leerie/.gnupg
chmod 700 /home/leerie/.gnupg
chown -R leerie: /home/leerie
mkdir -p /tmp/.cache
chown -R leerie: /tmp/.cache
chmod -R a+rwX /tmp/.cache
chmod 1777 /tmp/.cache

# --- /opt/leerie-image: one copy per Dockerfile COPY line targeting it ---
copied=0
while read -r _ src dest; do
  case "$dest" in
    /opt/leerie-image/*)
      mkdir -p "$dest"
      cp -a "$BAKE_DIR/$src." "$dest"
      copied=$((copied + 1))
      ;;
  esac
done < <(grep '^COPY ' "$DOCKERFILE")
[ "$copied" -gt 0 ] || { log "no COPY into /opt/leerie-image found"; exit 1; }
chown -R "leerie:$LEERIE_GID" /opt/leerie-image

mkdir -p /work
chown "leerie:$LEERIE_GID" /work

# --- SSM agent (not in Debian's apt or image; fetched from AWS) ---
# AWS recommends the regional bucket for scripted installs; the global
# URL is the documented fallback.
arch="$(dpkg --print-architecture)"
ssm_deb=/tmp/amazon-ssm-agent.deb
ssm_global="https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_${arch}/amazon-ssm-agent.deb"
if [ -n "${LEERIE_BAKE_REGION:-}" ] \
   && curl -fsSL -o "$ssm_deb" "https://s3.${LEERIE_BAKE_REGION}.amazonaws.com/amazon-ssm-${LEERIE_BAKE_REGION}/latest/debian_${arch}/amazon-ssm-agent.deb"; then
  :
else
  curl -fsSL -o "$ssm_deb" "$ssm_global"
fi
dpkg -i "$ssm_deb"
rm -f "$ssm_deb"
systemctl enable --now amazon-ssm-agent

# --- ec2-user: the SSH login for the bulk transfer ---
# cloud-init's default user, so every instance launched from this AMI
# installs its own launch key pair here at boot (per-instance modules
# re-run on a new instance id). Passwordless sudo: the transports write
# leerie-owned paths (DESIGN §6 "Neither transport lands as root").
leerie_group="$(getent group "$LEERIE_GID" | cut -d: -f1)"
mkdir -p /etc/cloud/cloud.cfg.d
cat > /etc/cloud/cloud.cfg.d/99-leerie.cfg <<CFG
system_info:
  default_user:
    name: ec2-user
    lock_passwd: true
    gecos: leerie transport
    groups: [$leerie_group]
    sudo: ["ALL=(ALL) NOPASSWD:ALL"]
    shell: /bin/bash
CFG
chmod g+w /work

# --- What container-entry.sh does as PID 1, for an instance that has none ---
# The cgroup broker (DESIGN §6 *Memory containment*): without it the
# orchestrator's fail-closed containment gate refuses to start any worker.
# Root on a real host is the rootful case, so leerie.slice sits at the top
# of the hierarchy as in a rootful container. Not a Delegate=yes unit
# cgroup: the broker would occupy it, and cgroup v2 forbids enabling
# controllers on a cgroup that holds processes.
cat > /usr/local/sbin/leerie-instance-setup <<'SETUP'
#!/bin/sh
# Boot-time half of container-entry.sh (scripts/remote/ec2-bake.sh).
SLICE="${LEERIE_CGROUP_V2_ROOT:-/sys/fs/cgroup}/leerie.slice"
mkdir -p "$SLICE" 2>/dev/null || true
echo "+memory +pids" > "$SLICE/cgroup.subtree_control" 2>/dev/null || true
# Aggregate cap, same formula as container-entry.sh: MemTotal minus
# max(1 GiB, 12.5%), so the slice OOMs before the host does.
cap="${LEERIE_CONTAINER_MEMORY_MAX_BYTES:-}"
case "$cap" in 0|max) cap="" ;; "")
  kb="$(awk '/^MemTotal:/ {print $2; exit}' "${LEERIE_MEMINFO:-/proc/meminfo}" 2>/dev/null)"
  if [ -n "$kb" ] && [ "$kb" -gt 0 ] 2>/dev/null; then
    total=$((kb * 1024)); reserve=$((total / 8)); min=$((1024 * 1024 * 1024))
    [ "$reserve" -lt "$min" ] && reserve="$min"
    [ "$total" -gt "$reserve" ] && cap=$((total - reserve))
  fi ;;
esac
if [ -n "$cap" ] && [ "$cap" -gt 0 ] 2>/dev/null; then
  echo "$cap" > "$SLICE/memory.max" 2>/dev/null || true
fi
# The boot empties /tmp; XDG_CACHE_HOME points at /tmp/.cache.
cache="${LEERIE_TMP:-/tmp}/.cache"
mkdir -p "$cache" && chown leerie: "$cache" 2>/dev/null; chmod 1777 "$cache" 2>/dev/null || true
exit 0
SETUP
chmod 755 /usr/local/sbin/leerie-instance-setup
cat > /etc/systemd/system/leerie-cgroup-broker.service <<'UNIT'
[Unit]
Description=leerie cgroup broker (worker containment)
After=local-fs.target

[Service]
Type=simple
ExecStartPre=/usr/local/sbin/leerie-instance-setup
ExecStart=/usr/bin/python3 /opt/leerie-image/scripts/cgroup-broker.py
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
UNIT
systemctl enable leerie-cgroup-broker.service

# Disk-backed /tmp, as in the container. Debian 13 mounts /tmp as a RAM
# tmpfs by default; seed bundles land there and XDG_CACHE_HOME points there,
# so on a tmpfs they would compete with the workers for memory and the
# /tmp/.cache set up above would vanish on every boot.
systemctl mask tmp.mount

# The bootstrap login's key is the bake's, not any run's; the image
# carries none. Then reset cloud-init so the image's first boot is a
# fresh instance (new machine id, host keys, default user, key pair).
rm -f /home/admin/.ssh/authorized_keys
cloud-init clean --logs --machine-id

log "bake provisioning complete"
