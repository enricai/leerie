#!/usr/bin/env bash
# scripts/remote/ec2-bake-provision.sh — runs ON the stock Debian 13 bake
# instance (as root) and reproduces the repo Dockerfile so the resulting AMI
# matches the container image. Shipped by ensure_ami; never run on the host.
#
# Every package list and pin is read from the Dockerfile shipped alongside it
# (single source of truth) — the Dockerfile's hash is what keys the AMI cache,
# so a hand-copied pin here would silently go stale.
#
# Input: $LEERIE_BAKE_DIR (default /tmp/leerie-bake) holding Dockerfile,
# requirements.txt and the directories the Dockerfile COPYs into
# /opt/leerie-image/. Optional: LEERIE_UID / LEERIE_GID (default 1001), and
# LEERIE_BAKE_SSH_PUBKEY (else the bootstrap login's authorized_keys is reused).
#
# Not reproduced: the crashpad stub (a container-only Chromium workaround) and
# the rootless-containerd ownership workarounds (Dockerfile:235-241). On EC2
# the orchestrator drops privilege with a real uid switch, i.e. the rootful
# semantics, so dirs are owned by leerie literally.
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

MISE_VERSION="$(sed -n 's/^ARG MISE_VERSION=//p' "$DOCKERFILE" | head -n1)"
CLAUDE_SPEC="$(sed -n "s/.*npm install -g '\\(@anthropic-ai\\/claude-code@[^']*\\)'.*/\\1/p" "$DOCKERFILE" | head -n1)"
[ -n "$MISE_VERSION" ] && [ -n "$CLAUDE_SPEC" ] || { log "could not derive pins from Dockerfile"; exit 1; }

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

# --- mise + system-wide LTS runtimes ---
curl -fsSL https://mise.run | MISE_VERSION="$MISE_VERSION" sh
mv /root/.local/bin/mise /usr/local/bin/mise
rm -rf /root/.local /root/.config /root/.cache

export MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS=node,python,ruby,rust
export MISE_NODE_COREPACK=true
export MISE_RUBY_COMPILE=false
export MISE_DATA_DIR=/home/leerie/.local/share/mise
export MISE_SYSTEM_DATA_DIR=/usr/local/share/mise
export XDG_CACHE_HOME=/tmp/.cache
export MISE_STATE_DIR=/tmp/.mise-state
mise install --system node@lts python@3.12

node_dir="$(find /usr/local/share/mise/installs/node -mindepth 1 -maxdepth 1 -type d | head -n1)/"
ln -sfn "${node_dir%/}" /usr/local/share/mise/installs/node/lts-current

# Login-shell environment mirroring the Dockerfile ENV lines.
cat > /etc/profile.d/leerie.sh <<'PROFILE'
export MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS=node,python,ruby,rust
export MISE_NODE_COREPACK=true
export MISE_RUBY_COMPILE=false
export MISE_DATA_DIR=/home/leerie/.local/share/mise
export MISE_SYSTEM_DATA_DIR=/usr/local/share/mise
export XDG_CACHE_HOME=/tmp/.cache
export MISE_STATE_DIR=/tmp/.mise-state
export PATH=/usr/local/share/mise/shims:/usr/local/share/mise/installs/node/lts-current/bin:/home/leerie/.local/share/mise/shims:$PATH:/home/leerie/.local/bin
PROFILE

# --- Claude Code CLI (pinned spec comes from the Dockerfile) ---
export PATH=/usr/local/share/mise/installs/node/lts-current/bin:$PATH
npm install -g "$CLAUDE_SPEC"

# --- leerie user ---
if ! getent group "$LEERIE_GID" >/dev/null 2>&1; then
  groupadd -g "$LEERIE_GID" leerie
fi
useradd -u "$LEERIE_UID" -g "$LEERIE_GID" -m -s /bin/bash leerie

git config --system --add safe.directory '*'

mkdir -p /inspect
chown "leerie:$LEERIE_GID" /inspect

# Root-owned here, chowned at runtime by container-entry.sh's rootful guard.
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
mkdir -p /tmp/.cache
chown -R leerie: /tmp/.cache
chmod -R a+rwX /tmp/.cache
chmod 1777 /tmp/.cache

# --- /opt/leerie-image: one copy per Dockerfile COPY line targeting it ---
mkdir -p /opt/leerie-image
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

# --- SSM agent (not in Debian's apt; fetched from AWS) ---
arch="$(dpkg --print-architecture)"
curl -fsSL -o /tmp/amazon-ssm-agent.deb \
  "https://s3.amazonaws.com/ec2-downloads-windows/SSMAgent/latest/debian_${arch}/amazon-ssm-agent.deb"
dpkg -i /tmp/amazon-ssm-agent.deb
rm -f /tmp/amazon-ssm-agent.deb
systemctl enable amazon-ssm-agent

# --- ec2-user: SSH login for the bulk transfer; group-writable /work ---
id ec2-user >/dev/null 2>&1 || useradd -m -s /bin/bash -G "$LEERIE_GID" ec2-user
usermod -aG "$LEERIE_GID" ec2-user
chmod g+w /work
install -d -m 700 -o ec2-user -g ec2-user /home/ec2-user/.ssh
if [ -n "${LEERIE_BAKE_SSH_PUBKEY:-}" ]; then
  printf '%s\n' "$LEERIE_BAKE_SSH_PUBKEY" > /home/ec2-user/.ssh/authorized_keys
else
  cat /home/admin/.ssh/authorized_keys > /home/ec2-user/.ssh/authorized_keys
fi
chown ec2-user:ec2-user /home/ec2-user/.ssh/authorized_keys
chmod 600 /home/ec2-user/.ssh/authorized_keys

log "bake provisioning complete"
