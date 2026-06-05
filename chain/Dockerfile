# chain/Dockerfile — leerie-chain orchestrator app image.
#
# Intentionally leaner than the repo-root Dockerfile: this image runs
# chain/server.py (the persistent HTTP API), which only needs git, gh,
# flyctl, and stdlib Python3 to clone repos, open PRs, and launch
# worker Fly Machines. It does NOT run `claude -p` worker tasks, so
# mise, claude-code, build-essential, and the /inspect/ cache mounts
# are all omitted.
#
# Build:  docker build -f chain/Dockerfile -t leerie-chain:dev .
#   (from the repo root so the COPY instructions below resolve)
# Or from the chain/ subdirectory:
#   docker build -f Dockerfile -t leerie-chain:dev ..

FROM debian:13-slim

# Base tools: git + openssh for clone/branch/push ops, python3 for the
# HTTP server, ca-certificates + curl for HTTPS and package-list fetches.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git openssh-client \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — chain/git_ops.py calls `gh pr create` to open PRs after
# integrating Wave A/B branches. Installed from the official Debian apt
# repo to stay on a current release without pinning a URL.
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

# flyctl — used by chain/fly_client.py to launch and stop Fly Machines
# for each leerie run. Installed from Fly's official install script.
RUN curl -fsSL https://fly.io/install.sh | sh \
    && mv /root/.fly/bin/flyctl /usr/local/bin/flyctl \
    && rm -rf /root/.fly

# Relax git's safe.directory check so `git -C <clone-dir> ...` ops in
# git_ops.py work regardless of UID/GID mismatches inside the container.
RUN git config --system --add safe.directory '*'

# Non-root user matching the host UID/GID (same ARG pattern as root Dockerfile).
ARG HOST_UID=501
ARG HOST_GID=20
RUN if ! getent group "${HOST_GID}" >/dev/null 2>&1; then \
      groupadd -g "${HOST_GID}" leerie; \
    fi; \
    useradd -u "${HOST_UID}" -g "${HOST_GID}" -m -s /bin/bash leerie

# Persistent volume mount point for the SQLite chain state DB.
# The fly.toml [mounts] section attaches a Fly volume here at runtime.
RUN mkdir -p /data && chown leerie:"${HOST_GID}" /data

# Bake the chain package and its dependencies into the image.
# The server is started as `python3 -m chain.server` from /app, so the
# package must be importable from there.
COPY chain/ /app/chain/
RUN chown -R leerie:"${HOST_GID}" /app

WORKDIR /app
USER leerie

# chain.__main__ is the entry point: reads env vars for port/host,
# initialises ChainState at /data/chain.db, then serves forever.
CMD ["python3", "-m", "chain"]
