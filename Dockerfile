# leerie container image — see docs/IMPLEMENTATION.md §0.5 "Container shape".
#
# Built locally on first `leerie` run; tagged `leerie:<VERSION>` so a leerie upgrade
# rebuilds once and reuses layers thereafter.
#
# LOCAL MODE: the launcher bind-mounts $LEERIE_HOME → /opt/leerie-image:ro at
# runtime, shadowing the baked-in COPY layers. Editing orchestrator/leerie.py
# on the host takes effect on the next run without an image rebuild.
#
# REGISTRY / FLY MODE: the COPY instructions below bake orchestrator/,
# scripts/, prompts/, and .claude-plugin/ into /opt/leerie-image/ so the
# image is self-contained without a bind-mount. An image rebuild IS required
# after source changes.
#
# Lives at /opt/leerie-image/ (NOT /work/.leerie-image/) so the remote-seed
# phase can `rm -rf /work` without clobbering the orchestrator code.

FROM debian:13-slim

# Base tools leerie + claude -p + typical worker tasks need.
# build-essential + dev libraries cover native-extension compilation:
# node-gyp (sharp, bcrypt), Ruby C gems (nokogiri, pg, sqlite3, mysql2, ffi),
# and Python C extensions. The -dev packages provide headers that
# `bundle install` / `pip install` need for gems/wheels with C code.
# default-libmysqlclient-dev provides both the headers for mysql2 gem
# compilation and libmariadb.so.3 for runtime linking.
# procps provides `ps`, which the orchestrator's PPID-walk fast-cleanup path
# (leerie.py:925) calls between waves. Without it the walk silently degrades
# to no-op via the OSError catch — correctness is fine, but the documented
# fast-happy-path is gone. ~1MB image cost.
# rsync is the receiver for the dirty-delta phase of seed-repo.sh
# (`seed_repo_dirty` rsync's uncommitted edits + untracked files + the
# repo-local `.claude/` directory after the bundle clone completes).
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client \
      python3 python3-pip \
      procps \
      build-essential \
      rsync \
      tzdata \
      zlib1g-dev libyaml-dev libreadline-dev libffi-dev libssl-dev \
      libpq-dev libsqlite3-dev libgdbm-dev \
      default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/*

# Python runtime deps. See docs/IMPLEMENTATION.md §0 "Python runtime"
# for the policy (stdlib-preferred; libs allowed when they earn it) and
# the current dep list. --break-system-packages is required on Debian
# 13 (PEP 668); the container's Python is owned by the orchestrator so
# system-wide install is correct here.
COPY requirements.txt /tmp/requirements.txt
RUN pip3 install --break-system-packages --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# GitHub CLI — the finalize phase pushes the run branch and opens a PR via
# `gh pr create` (leerie.py:5828). Without this, default-mode runs die at the
# preflight `shutil.which("gh")` check (leerie.py:1282). GitHub publishes a
# Debian apt repo with arch-aware packages; install from there.
RUN apt-get update && apt-get install -y --no-install-recommends \
      gnupg \
    && mkdir -p /etc/apt/keyrings \
    && curl -fsSL https://cli.github.com/packages/githubcli-archive-keyring.gpg \
         | gpg --dearmor -o /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && chmod go+r /etc/apt/keyrings/githubcli-archive-keyring.gpg \
    && echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/githubcli-archive-keyring.gpg] https://cli.github.com/packages stable main" \
         > /etc/apt/sources.list.d/github-cli.list \
    && apt-get update && apt-get install -y --no-install-recommends gh \
    && rm -rf /var/lib/apt/lists/*

# Chromium + matching chromedriver, baked at image build time so workers that
# need a real browser (system/integration tests, visual assertions, CSP checks,
# Selenium/Capybara/Playwright/Puppeteer scenarios, etc.) have one available
# without any runtime apt-get. Installing from Debian's own repos guarantees
# the browser and driver versions are always in sync — no Selenium Manager
# download needed at runtime. X11/GL/NSS/NSPR/GBM are pulled in automatically
# as Chromium deps. fonts-liberation prevents glyph-fallback rendering
# artifacts in screenshot-based assertions.
#
# libc6 is listed explicitly to force an upgrade if the debian:13-slim base
# image snapshot lags the current trixie repos. Without it, the chromium binary
# (compiled against the current trixie glibc) fails at load time with:
#   undefined symbol: localtime64_r (fatal)
# ld.so aborts before Chrome executes a single instruction, producing a
# SIGTRAP/core-dump that looks like a sandbox crash but is actually a glibc
# ABI mismatch. Listing libc6 here makes apt upgrade it in the same
# transaction as chromium, keeping the versions in sync.
#
# The required container flags (--no-sandbox, --disable-setuid-sandbox,
# --disable-dev-shm-usage) are baked into /etc/chromium.d/leerie-container-flags
# below so callers need no Chrome-specific configuration. See
# docs/IMPLEMENTATION.md §0.5 "Browser-based testing" for details.
RUN apt-get update && apt-get install -y --no-install-recommends \
      libc6 \
      chromium \
      chromium-driver \
      fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

# Bake container-appropriate Chrome flags into /etc/chromium.d/ so every
# Chromium invocation from this image works correctly without callers needing
# to know about rootless-container specifics.
#
# --no-sandbox:              disable Chrome's user-namespace sandbox (not
#                            available in unprivileged containers).
# --disable-setuid-sandbox:  suppress the SUID sandbox-helper lookup.
# --disable-dev-shm-usage:   redirect shared-memory to /tmp; /dev/shm is
#                            typically 64 MB in containers and Chrome's
#                            renderer can exceed that under load.
RUN echo 'CHROMIUM_FLAGS="$CHROMIUM_FLAGS --no-sandbox --disable-setuid-sandbox --disable-dev-shm-usage"' \
    > /etc/chromium.d/leerie-container-flags

# Replace the Debian chrome_crashpad_handler with a protocol-compatible stub.
#
# Root cause: in this container environment Chrome spawns the crashpad handler
# with --initial-client-fd=<fd> but WITHOUT --database (Debian's packaging
# changed the invocation flow). The real handler exits immediately with
# "database is required", Chrome detects the handler died before completing the
# IPC handshake, and calls __builtin_trap() → SIGTRAP → exit 133.
#
# The handshake protocol (discovered by instrumenting the socket):
#   1. Chrome sends a 40-byte hello through --initial-client-fd
#   2. Handler must respond with 8 bytes + a server-socket FD via SCM_RIGHTS
#   3. Chrome unblocks and continues startup; the server socket receives crash
#      notifications for the lifetime of the browser process
#
# The stub below implements this protocol, stays alive as a monitor, and
# silently discards crash reports (we have no crash database in-container).
RUN cat > /usr/lib/chromium/chrome_crashpad_handler << 'STUB'
#!/usr/bin/env python3
"""
Crashpad IPC stub for Debian Chromium in rootless containers.
Chrome expects: read 40-byte hello → reply 8 bytes + server socket FD via
SCM_RIGHTS. Without this handshake Chrome SIGTRAPs on startup.
"""
import sys, os, socket, struct, time

def main():
    fd = None
    for arg in sys.argv[1:]:
        if arg.startswith('--initial-client-fd='):
            fd = int(arg.split('=', 1)[1])
            break

    if fd is None:
        # Called as an exception handler (post-crash) without a client FD —
        # just sleep so we don't loop-crash Chrome's crash-reporting path.
        time.sleep(86400)
        return

    try:
        os.read(fd, 4096)   # consume Chrome's 40-byte hello
    except OSError:
        time.sleep(86400)
        return

    # Create the Unix-domain server socket that Chrome will send crashes to.
    sock_path = f'/tmp/.cpstub_{os.getpid()}.sock'
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        if os.path.exists(sock_path):
            os.unlink(sock_path)
        srv.bind(sock_path)
        srv.listen(10)

        # Send 8-byte ack + server FD back to Chrome via SCM_RIGHTS.
        client = socket.fromfd(fd, socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            anc = [(socket.SOL_SOCKET, socket.SCM_RIGHTS,
                    struct.pack('i', srv.fileno()))]
            client.sendmsg([b'\x00' * 8], anc)
        finally:
            client.detach()

        # Stay alive as the crash monitor; discard crash reports.
        srv.settimeout(1.0)
        while True:
            try:
                conn, _ = srv.accept()
                conn.close()
            except socket.timeout:
                pass
            except Exception:
                break
            time.sleep(0.1)
    finally:
        try: srv.close()
        except: pass
        try: os.unlink(sock_path)
        except: pass

main()
STUB
RUN chmod +x /usr/lib/chromium/chrome_crashpad_handler

# mise — polyglot version manager (formerly rtx). Owns the per-repo
# runtime version selection (DESIGN §6½). Reads .tool-versions natively
# and .nvmrc / .python-version / .ruby-version / rust-toolchain.toml
# via the MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS env below (those
# files are opt-in in current mise; without the env they are silently
# ignored). go.mod's `go 1.X` line is NOT parsed by mise; leerie
# synthesizes a .go-version override at provision time.
#
# The mise.run script reads $MISE_VERSION; pin for reproducibility.
ARG MISE_VERSION=v2026.5.4
RUN curl -fsSL https://mise.run | MISE_VERSION="${MISE_VERSION}" sh \
    && mv /root/.local/bin/mise /usr/local/bin/mise \
    && rm -rf /root/.local /root/.config /root/.cache

# mise env directives. ENV survives across RUNs and into the runtime
# container (a shell `export` inside a RUN would not).
#
# IDIOMATIC_VERSION_FILE_ENABLE_TOOLS is load-bearing: mise treats
# .nvmrc / .python-version / .ruby-version / rust-toolchain.toml as
# "idiomatic" files that are disabled by default. Without this flag,
# a repo with `.nvmrc: 20.11.0` would silently run on baked LTS.
#
# NODE_COREPACK activates corepack so package.json's `packageManager`
# field is honored — repo's pinned pnpm version wins, no global pin
# needed.
#
# RUBY_COMPILE=false uses precompiled Ruby binaries instead of building
# from source. Avoids needing the full ruby-build toolchain (autoconf,
# bison, etc.) inside the container. Becomes the mise default in 2026.8.0.
#
# DATA_DIR is where mise installs per-repo runtime versions; the
# launcher bind-mounts this from ~/.cache/leerie/mise-data so installs
# survive across runs. SYSTEM_DATA_DIR is where the LTS fallback
# lives (baked below). The resolver checks DATA_DIR first then falls
# through to SYSTEM_DATA_DIR (mise.jdx.dev/mise-cookbook/docker.html).
ENV MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS=node,python,ruby,rust
ENV MISE_NODE_COREPACK=true
ENV MISE_RUBY_COMPILE=false
ENV MISE_DATA_DIR=/home/leerie/.local/share/mise
ENV MISE_SYSTEM_DATA_DIR=/usr/local/share/mise
# Rootless containerd (Linux + nerdctl without setuid) runs the orchestrator
# inside `unshare --user --map-user=leerie_uid`, which maps outer UID 0 →
# inner UID leerie_uid but leaves outer UID leerie_uid unmapped (appears as
# overflow UID 65534/nobody). Image-layer dirs pre-created and chowned to
# leerie therefore appear unwritable to the process. /tmp is root-owned
# (outer 0 → inner leerie_uid) and always writable. Redirect tools that
# otherwise write to ~/ into /tmp so they don't fail with EACCES.
#
# XDG_CACHE_HOME: rubocop reads this for its cache root
#   (falls back to ~/.cache/rubocop_cache without it).
# MISE_STATE_DIR: mise reads this for tracked-configs and other state
#   (falls back to ~/.local/state/mise without it).
# The explicitly bind-mounted caches (pip, pnpm, go, cargo, mise-data) all
# have their own env vars and are unaffected by XDG_CACHE_HOME.
ENV XDG_CACHE_HOME=/tmp/.cache
ENV MISE_STATE_DIR=/tmp/.mise-state

# Pre-install LTS Node + Python via `mise install --system`. Lands
# binaries under /usr/local/share/mise/installs/<tool>/<version>.
# At runtime mise's resolver falls through to these from the user
# dir if a repo declares no version. ~150-200 MB image cost.
RUN mise install --system node@lts python@3.12

# Stable PATH symlink for the LTS Node bin. `mise install --system
# node@lts` resolves to a concrete version directory under
# /usr/local/share/mise/installs/node/<version>/ — we symlink it to a
# stable name so PATH and the claude global-install below don't have
# to know the concrete version. The wildcard expansion is safe
# because exactly one node version is installed in this layer.
RUN set -eux; \
    node_dir="$(ls -d /usr/local/share/mise/installs/node/*/ | head -n1)"; \
    ln -s "${node_dir%/}" /usr/local/share/mise/installs/node/lts-current

# PATH order:
#   1. mise's system shims (mise install --system populates these).
#   2. LTS Node bin (image-baked claude lives here).
#   3. (then the pre-existing PATH)
#   4. /home/leerie/.local/bin — where `pip install --user` puts console
#      scripts (e.g. pre-commit). Deliberately LAST: image-baked tooling
#      must win, so a user-installed package can never shadow a baked-in
#      binary. Pinned by tests/test_dockerfile_path.py.
# At runtime the user dir's shims at /home/leerie/.local/share/mise/shims
# don't appear here because they're added by `mise activate` or by
# wrapping commands with `mise exec --` — both of which the
# orchestrator does explicitly when invoking install commands.
ENV PATH=/usr/local/share/mise/shims:/usr/local/share/mise/installs/node/lts-current/bin:$PATH:/home/leerie/.local/bin

# Claude Code CLI. Leerie enforces ≥ 2.1.22 at runtime (leerie.py:1245).
# Installs globally against the LTS Node — lands at
# /usr/local/share/mise/installs/node/lts-current/lib/node_modules
# with a bin shim at .../bin/claude (on PATH via the line above).
RUN npm install -g @anthropic-ai/claude-code

# Non-root user matching the host UID/GID so bind-mounted files keep their
# host ownership. Defaults are macOS-typical; the launcher overrides them
# via --build-arg HOST_UID=$(id -u) --build-arg HOST_GID=$(id -g).
ARG HOST_UID=501
ARG HOST_GID=20
RUN if ! getent group "${HOST_GID}" >/dev/null 2>&1; then \
      groupadd -g "${HOST_GID}" leerie; \
    fi; \
    useradd -u "${HOST_UID}" -g "${HOST_GID}" -m -s /bin/bash leerie

# Relax git's CVE-2022-24765 safe.directory check inside the container.
# Under Colima/virtiofs the /work bind-mount root reports a gid that
# does not match the leerie user (stat shows /work as 501:1000 vs the
# leerie user's 501:20), which trips the check when worker bash tools
# run `git -C <worktree-subdir> ...` against per-subtask worktree
# subdirs. The container is single-tenant (leerie user only) and /work
# is its only repo, so the system-wide /etc/gitconfig is the cleanest
# mitigation — it applies to every user inside the container with no
# HOME-handling risk (matches the posture of every major CI image).
RUN git config --system --add safe.directory '*'

# /inspect/ holds read-only bind mounts the launcher creates per
# --inspect-dir flag. Pre-created (and owned by leerie) so the mount targets
# exist when nerdctl creates them at runtime.
RUN mkdir -p /inspect && chown leerie:"${HOST_GID}" /inspect

# Pre-create the leerie user's MISE_DATA_DIR and the per-tool cache
# mount targets so the launcher's bind-mounts attach cleanly.
#
# Left root-owned (no chown to leerie here): rootless containerd's
# `unshare --user --map-user=$(id -u leerie)` maps only outer UID 0 ->
# inner leerie. A dir owned by leerie's literal UID has no entry in that
# map and appears as nobody/65534 to the remapped process — traversable,
# not writable. Root-owned dirs map correctly (outer 0 -> inner leerie),
# the same mechanism that already makes bind mounts like /work writable
# with no chown. container-entry.sh's rootful branch chowns these to
# leerie at runtime instead, since the rootful `runuser -u leerie` drop
# is a real uid switch with no remap to rely on.
RUN mkdir -p /home/leerie/.local/share/mise \
             /home/leerie/.local/state/mise \
             /home/leerie/.cache/leerie/pnpm-store \
             /home/leerie/.cache/leerie/pip \
             /home/leerie/.cache/leerie/go-mod \
             /home/leerie/.cache/leerie/cargo \
             /home/leerie/.cache/leerie/corepack \
             /home/leerie/.cache/rubocop_cache \
             /home/leerie/.cache/selenium \
             /home/leerie/.gnupg \
    && chmod 700 /home/leerie/.gnupg
# mise install --system (above, before useradd) runs as root and creates
# /tmp/.cache/mise/ (via XDG_CACHE_HOME=/tmp/.cache) owned by root:root.
# On Fly Machines the rootfs preserves this ownership; the leerie user
# then gets EACCES when `mise install` tries to write its download cache.
#
# Chowning to leerie is not enough on its own, and is actively wrong under
# rootless containerd: container-entry.sh's privilege drop there is
# `unshare --user --map-user=$(id -u leerie) ...`, which remaps only
# outer UID 0 -> inner leerie. A directory explicitly chowned to leerie's
# own (non-zero) UID is NOT covered by that remap, so it appears owned by
# nobody/65534 to the remapped process — traversable via its mode-755
# "other" bits (read/execute), but not writable (no write bit for
# "other"). That silently breaks every OTHER tool that tries to create
# its own subdir under XDG_CACHE_HOME here (observed for corepack —
# see COREPACK_HOME below — and for tree-sitter-language-pack's
# download-cache lock). Chasing each offender down with its own
# dedicated, separately-mounted cache dir doesn't scale.
#
# Fix it once, generally: make /tmp/.cache itself world-writable with the
# sticky bit — the same posture /tmp itself already has (`drwxrwxrwt`) —
# so any UID, real or remapped, can create new entries under it
# regardless of who "owns" the directory. This container is single-tenant
# (leerie's own processes only), so there is no security cost to the
# permissive mode; the chown is kept alongside it for the plain
# rootful/Fly path where UIDs aren't remapped at all.
RUN chown -R leerie: /tmp/.cache \
    && chmod -R a+rwX /tmp/.cache \
    && chmod 1777 /tmp/.cache

# Bake the orchestrator source into the image at /opt/leerie-image/ so the
# image is self-contained on Fly.io Machines (no host bind mount available).
# Lives OUTSIDE /work so the remote-seed phase can `rm -rf /work` without
# clobbering the orchestrator code. Local runs bind-mount the host leerie
# repo at /opt/leerie-image:ro (see launcher) — same path inside the
# container, so /opt/leerie-image/orchestrator/leerie.py works identically
# in both modes. COPY runs as root; chown transfers ownership to leerie.
COPY orchestrator/ /opt/leerie-image/orchestrator/
COPY scripts/ /opt/leerie-image/scripts/
COPY prompts/ /opt/leerie-image/prompts/
COPY .claude-plugin/ /opt/leerie-image/.claude-plugin/
RUN chown -R leerie:"${HOST_GID}" /opt/leerie-image
# /work must be writable by leerie for the orchestrator to create
# /work/.leerie on remote (Fly Machine) runs. Local runs bind-mount
# $(pwd):/work which masks the image's /work permission; remote runs
# use the image's /work directly, so it must be owned by leerie.
RUN mkdir -p /work && chown leerie:"${HOST_GID}" /work

# Intentionally NO `USER leerie` directive — ENTRYPOINT runs as root so
# scripts/container-entry.sh can create /sys/fs/cgroup/leerie.slice and
# launch the root cgroup broker (scripts/cgroup-broker.py) before dropping
# privilege via `runuser -u leerie -- ...`. The broker does per-worker
# cgroup enrollment/limit-setting, which non-root code cannot (DESIGN §6
# *Memory containment*): the kernel keeps controller limit files root-owned
# in delegated subtrees, and cross-scope task migration needs write on the
# common-ancestor cgroup the leerie user doesn't own. Without root at PID 1
# the broker can't run and per-worker memory/PID containment is off — the
# orchestrator's fail-closed gate then stops the run. The orchestrator
# itself runs as leerie via that runuser drop (local nerdctl) or via
# Popen(user="leerie") in the launcher's ssh-console wrapper (Fly).
WORKDIR /work

ENTRYPOINT ["/opt/leerie-image/scripts/container-entry.sh"]
