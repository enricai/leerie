# chain/Dockerfile — per-chain ephemeral coordinator image.
#
# Each `leerie --chain` submission launches one of these as a tiny Fly
# Machine (shared-cpu-1x, 256MB, persistent 1GB volume at /data).
# It runs chain.coordinator — a stdlib HTTP server that holds the chain's
# SQLite state and decides wave advancement. Workers POST /report,
# /heartbeat, /pause, /unpause to this machine over Fly's private 6PN
# network (no public exposure, no authentication).
#
# When the chain reaches a terminal state (done / failed / cancelled),
# the coordinator pushes an audit artifact to the target repo and POSTs
# its own destroy via the Fly Machines API. There is no `fly.toml` —
# the launcher (leerie --chain) constructs the machine spec inline.
#
# Build:  docker build -f chain/Dockerfile -t leerie-coordinator:dev .
#         (from the repo root so the COPY instructions below resolve)

FROM debian:13-slim

# Base tools:
#   - git for clone + push of worker branches and the audit artifact
#   - gh CLI for opening PRs (target-repo push creds + PAT come in via env)
#   - python3 for the coordinator HTTP server
#   - ca-certificates + curl for HTTPS and installing gh below
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git \
      python3 python3-pip \
    && rm -rf /var/lib/apt/lists/*

# GitHub CLI — chain/git_ops.py shells out to `gh pr create` to open PRs
# for each completed worker's branch.
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
# The launcher (leerie --chain) attaches a Fly volume here at machine
# creation time.
RUN mkdir -p /data && chown leerie:"${HOST_GID}" /data

# Bake the chain package into the image. The coordinator is started as
# `python3 -m chain` from /app, so the package must be importable from there.
COPY chain/ /app/chain/
RUN chown -R leerie:"${HOST_GID}" /app

WORKDIR /app
USER leerie

# Runtime env (set by the launcher when creating the machine):
#   LEERIE_CHAIN_ID       — chain row this coordinator owns (required)
#   FLY_MACHINE_ID        — coordinator's own machine id, used for self-destruct
#                           (set automatically by Fly; required)
#   LEERIE_IMAGE          — worker image to launch for next-wave workers
#   FLY_APP_NAME          — Fly app name (defaults to "leerie")
#   FLY_API_TOKEN         — Fly Machines API token for launching/destroying
#   GH_DISPATCH_PAT       — GitHub PAT for push + PR + audit-artifact commits
#   LEERIE_REGION         — default 'iad'
#   LEERIE_DB_PATH        — default '/data/chain.db'
#   LEERIE_HEARTBEAT_STALENESS_S — default 900 (15 min)
#   LEERIE_ABANDON_TIMEOUT_S     — default 1800 (30 min)
#   LEERIE_WORKER_ENV_JSON       — base env merged into every worker's env
#                                  (Claude OAuth creds, etc.)
#
# chain.__main__ delegates to chain.coordinator.main, which reads all of
# the above and either initialises a fresh chain.db (first boot) or
# resumes from the persistent volume (after a Fly restart).
CMD ["python3", "-m", "chain"]
