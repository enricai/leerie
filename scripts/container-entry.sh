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

# Rootless containerd detection. rootlesskit maps host UID → container
# UID 0, so /proc/self/uid_map's first line has a non-zero host-start
# field (e.g. "0 1000 1"). In rootless mode, root inside the container
# IS the unprivileged host user — no privilege escalation. We must skip
# the privilege drop to leerie (whose mapped UID can't access bind-
# mounted host dirs) and the /work chown (which would reassign host
# ownership to the subuid range, breaking host-side access).
ROOTLESS=false
if awk 'NR==1 && $2 != 0 { exit 0 } { exit 1 }' /proc/self/uid_map 2>/dev/null; then
  ROOTLESS=true
fi

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
if [ "$ROOTLESS" != "true" ] && getent passwd leerie >/dev/null 2>&1; then
  chown leerie: /work 2>/dev/null || true
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
  if [ "$ROOTLESS" = "true" ]; then
    exec env HOME=/home/leerie USER=leerie LOGNAME=leerie \
      sleep infinity
  fi
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
# In rootless mode root IS the host user, so bind-mounted dirs (outer
# UID 0) are ours — but claude refuses --dangerously-skip-permissions
# when getuid()==0. We remap to the leerie user's UID/GID (baked in at
# image build time from HOST_UID/HOST_GID) in a nested user namespace
# via unshare: outer UID 0 → inner UID leerie (explicit), so all
# bind-mounts remain owned by us. Image dirs (outer UID leerie) are
# traversed via their mode-755 bits, not by ownership.
if [ "$ROOTLESS" = "true" ]; then
  _leerie_uid="$(id -u leerie)"
  _leerie_gid="$(id -g leerie)"
  exec unshare --user --map-user="$_leerie_uid" --map-group="$_leerie_gid" \
    env HOME=/home/leerie USER=leerie LOGNAME=leerie \
    python3 /opt/leerie-image/orchestrator/leerie.py "$@"
fi
exec runuser -u leerie -- \
  env HOME=/home/leerie USER=leerie LOGNAME=leerie \
  python3 /opt/leerie-image/orchestrator/leerie.py "$@"
