#!/bin/sh
# container-entry.sh — PID 1 of the leerie container.
#
# Bind-mounted from $LEERIE_HOME/scripts/container-entry.sh on the host to
# /opt/leerie-image/scripts/container-entry.sh inside the container, and
# referenced by Dockerfile's ENTRYPOINT.
#
# Runs as root. The Dockerfile intentionally does NOT have USER leerie —
# we need PID 1 to be root so the cgroup-v2 delegation block below can
# chown /sys/fs/cgroup/leerie.slice to the leerie user before privilege
# drop. The orchestrator itself runs as leerie via the `runuser` exec at
# the bottom (local nerdctl path) or via the launcher's Popen(user=
# "leerie") inside the Fly orchestrator-launch wrapper (the entrypoint
# on Fly just idles as PID 1 so the namespace stays alive).
#
# PID 1 in a container is what the kernel reaps the namespace under when
# it exits — see docs/DESIGN.md §6 and docs/IMPLEMENTATION.md §0.5.
set -e
# Suppress core dumps from OOM-killed workers — on large codebases
# (e.g. Next.js apps with heavy tsc + bundler memory use), `next build`
# or vitest can be OOM-killed inside Colima and otherwise leave
# multi-GB core files behind in each per-subtask worktree. Setting
# RLIMIT_CORE=0 at PID 1 is inherited by every worker subprocess.
# shellcheck disable=SC3045  # ulimit -c is non-POSIX but supported by dash (Debian's /bin/sh) and bash
ulimit -c 0

# Cgroup v2 delegation. PID 1 runs as root, so chown succeeds; the
# orchestrator subsequently runs as leerie and operates inside the
# delegated slice. Best-effort: missing controllers or an older kernel
# without cgroup v2 cause the chowns to fail silently — leerie keeps
# running, just uncapped (the orchestrator's _cgroup_probe logs one
# warn line and _cgroup_create returns None). On a container restart
# that observes an already-delegated slice (local nerdctl container
# restart on a host VM that kept its cgroupfs state across the
# restart) the mkdir is skipped but the chowns rerun idempotently —
# this is the right behavior if the leerie UID changed across
# restarts. On Fly this re-entry case does not arise: Firecracker
# microVMs reboot the kernel fresh each machine start, so the slice
# never persists across boots. The orchestrator's
# _detect_cgroup_root() picks the slice if this succeeded, else
# falls back to /sys/fs/cgroup. See DESIGN §6 *Memory containment*.
if [ -d /sys/fs/cgroup ] && [ ! -d /sys/fs/cgroup/leerie.slice ]; then
  mkdir -p /sys/fs/cgroup/leerie.slice 2>/dev/null || true
fi
if [ -d /sys/fs/cgroup/leerie.slice ]; then
  chown leerie: /sys/fs/cgroup/leerie.slice 2>/dev/null || true
  chown leerie /sys/fs/cgroup/leerie.slice/cgroup.procs 2>/dev/null || true
  chown leerie /sys/fs/cgroup/leerie.slice/cgroup.subtree_control 2>/dev/null || true
fi

cd /work

# /work ownership fix. On the Fly path, when FLY_VM_DISK_GB is set in
# provision.sh a per-machine Fly volume is mounted at /work — and the
# mount masks the Dockerfile's baked `chown leerie:` layer (the volume
# root is owned by root:root on first attach). The orchestrator runs as
# leerie, so without this chown it would fail to write into its own
# working dir on the first volume-backed boot. Now that PID 1 runs as
# root, this chown actually succeeds rather than silently no-op'ing.
# Trailing-colon form (`chown leerie:`) matches seed-repo.sh and
# seed-auth.sh — it resolves to leerie's primary group by GID, which
# survives the Dockerfile's `groupadd -g $HOST_GID leerie` being
# skipped when the base image already has a group at that GID (so no
# group literally named "leerie" exists). On the no-volume and local
# nerdctl paths the chown is a no-op against an already-correct /work
# (rootfs /work is leerie-owned from image build; local bind-mount
# preserves host ownership).
# (DESIGN §6 *Remote disk policy*; IMPLEMENTATION §0.5 *Container shape*.)
if getent passwd leerie >/dev/null 2>&1; then
  chown leerie: /work 2>/dev/null || true
fi

# Chain-mode path: a chain worker is launched by the per-chain coordinator
# via the Fly Machines API (chain/fly_client.py::launch_machine) which
# POSTs a bare {image, env, guest} spec — no init.cmd, no init.exec, no
# argv. We detect this by LEERIE_CHAIN_ID being set in env and run the
# orchestrator inline as PID 1 rather than idling. (Non-chain Fly runs
# fall through to the `sleep infinity` branch and the launcher's
# `flyctl ssh console -C "python3 -"` wrapper invokes the orchestrator
# out-of-band; see leerie launcher around lines 2541-2611.)
#
# Before exec'ing the orchestrator we background-start the
# leerie-chain-heartbeat.sh loop and trap EXIT to clean it up. The
# heartbeat POSTs /heartbeat to the coordinator every
# LEERIE_CHAIN_HEARTBEAT_INTERVAL_S seconds (default 60); without it
# the coordinator's watchdog would mark this worker presumed-failed
# after 15 minutes.
#
# Required env (set by the coordinator at launch_machine time):
#   LEERIE_CHAIN_ID, LEERIE_RUN_ID, LEERIE_COORDINATOR_HOST, LEERIE_TASK
if [ -n "${LEERIE_CHAIN_ID:-}" ] && [ "$#" -eq 0 ]; then
  if [ -z "${LEERIE_TASK:-}" ]; then
    echo "container-entry: chain-mode requires LEERIE_TASK to be set" >&2
    exit 64
  fi
  if [ -z "${LEERIE_RUN_ID:-}" ]; then
    echo "container-entry: chain-mode requires LEERIE_RUN_ID to be set" >&2
    exit 64
  fi
  # Background-start the heartbeat as the leerie user (matches
  # orchestrator's identity so /proc inspection is consistent). The
  # script tolerates LEERIE_COORDINATOR_HOST being absent (exits cleanly).
  runuser -u leerie -- \
    env HOME=/home/leerie USER=leerie LOGNAME=leerie \
        LEERIE_CHAIN_ID="$LEERIE_CHAIN_ID" \
        LEERIE_RUN_ID="$LEERIE_RUN_ID" \
        LEERIE_COORDINATOR_HOST="${LEERIE_COORDINATOR_HOST:-}" \
        LEERIE_CHAIN_HEARTBEAT_INTERVAL_S="${LEERIE_CHAIN_HEARTBEAT_INTERVAL_S:-60}" \
    /bin/bash /opt/leerie-image/scripts/leerie-chain-heartbeat.sh &
  _heartbeat_pid=$!
  # Reap the heartbeat on any orchestrator exit path — clean exit,
  # signal, or crash — so the worker doesn't linger as a zombie waiting
  # on a dead background process when the Fly machine teardown trap
  # runs.
  trap 'kill "${_heartbeat_pid}" 2>/dev/null || true' EXIT INT TERM

  # Forward the chain run-id as --run-id so the orchestrator writes its
  # state dir under the chain-assigned UUID (so the coordinator can
  # correlate branch names like leerie/runs/<run-id> back to the
  # chain_runs row). The task is the positional final arg.
  exec runuser -u leerie -- \
    env HOME=/home/leerie USER=leerie LOGNAME=leerie \
    python3 /opt/leerie-image/orchestrator/leerie.py \
      --run-id "$LEERIE_RUN_ID" \
      "$LEERIE_TASK"
fi

# Fly path: idle as PID 1 so the machine stays up. The orchestrator is
# invoked out-of-band by the launcher's `flyctl ssh console -C
# "python3 -"` wrapper, which itself runs as root (ssh-console always
# lands as root regardless of the image's USER directive) and then
# drops to leerie via Popen(user="leerie") (see the bash leerie
# launcher around lines 2541-2611). We drop to leerie here too for
# hygiene — any in-container inspection (ps, /proc) sees the idle PID 1
# as leerie, not root. Local nerdctl always passes argv (the task +
# flags), so this branch never fires in local mode.
if [ "$#" -eq 0 ]; then
  exec runuser -u leerie -- \
    env HOME=/home/leerie USER=leerie LOGNAME=leerie \
    sleep infinity
fi

# Local nerdctl path: inject the container ID as --run-id so the
# orchestrator uses it as its run_id. The launcher wrote a cidfile
# at /run/leerie-cidfile via nerdctl --cidfile; nerdctl writes it
# before PID 1 starts, so it's available here.
if [ -f /run/leerie-cidfile ]; then
  _cid="$(cat /run/leerie-cidfile)"
  if [ -n "$_cid" ]; then
    set -- --run-id "$_cid" "$@"
  fi
fi

# Drop to leerie before the orchestrator. We pass HOME/USER/LOGNAME
# explicitly rather than using `runuser --login` — the login form would
# chdir to /home/leerie and override the `cd /work` invariant the
# orchestrator depends on (and would source the user's shell profile,
# which could mutate PATH unpredictably). HOME is load-bearing for
# claude (creds at ~/.claude/.credentials.json); USER/LOGNAME are read
# by tools that introspect identity.
exec runuser -u leerie -- \
  env HOME=/home/leerie USER=leerie LOGNAME=leerie \
  python3 /opt/leerie-image/orchestrator/leerie.py "$@"
