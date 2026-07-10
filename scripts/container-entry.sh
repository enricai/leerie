#!/bin/sh
# container-entry.sh — PID 1 of the leerie container.
#
# Bind-mounted from $LEERIE_HOME/scripts/container-entry.sh on the host to
# /opt/leerie-image/scripts/container-entry.sh inside the container, and
# referenced by Dockerfile's ENTRYPOINT.
#
# Runs as root (rootful) or rootlesskit-mapped root (rootless — see below).
# The Dockerfile intentionally does NOT have USER leerie — PID 1 needs to
# be this identity so it can create the leerie.slice cgroup and launch the
# cgroup broker (which does per-worker cgroup enrollment/limit-setting
# that the orchestrator's own dropped-privilege identity cannot; see
# DESIGN §6 *Memory containment*) before privilege drop.
# The orchestrator itself runs as leerie via the `runuser` exec at
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
HOST_UID=""
if awk 'NR==1 && $2 != 0 { exit 0 } { exit 1 }' /proc/self/uid_map 2>/dev/null; then
  ROOTLESS=true
  HOST_UID="$(awk 'NR==1 {print $2}' /proc/self/uid_map 2>/dev/null)"
fi

# Cgroup slice root (cgroup v2). Rootful (Colima, Fly): the top-level
# /sys/fs/cgroup, matching real root's own delegation domain. Rootless:
# rootlesskit maps container "root" to the real host UID, which has no
# privilege over /sys/fs/cgroup itself — but systemd (pam_systemd/logind)
# already delegates a writable subtree per login session at
# /sys/fs/cgroup/user.slice/user-<uid>.slice/user@<uid>.service/, chowned
# to that UID (cgroup.procs, cgroup.subtree_control, cgroup.threads, and
# — critically — any cgroup the UID creates underneath it, whose
# interface files inherit the creator's ownership). Anchoring leerie.slice
# there instead of the top level lets the SAME identity that launches the
# broker below (mapped root == the real host UID) actually create/enroll/
# limit cgroups, without any host-side systemd or rootlesskit
# reconfiguration. Cross-scope worker migration still works from here:
# cgroup v2 only requires write access to the destination and the
# nearest common ancestor cgroup.procs (not the source), and that
# ancestor — user@<uid>.service — is exactly what's delegated. See
# DESIGN §6 *Memory containment* / *Rootless exception*.
if [ "$ROOTLESS" = "true" ] && [ -n "$HOST_UID" ]; then
  CGROUP_ROOT="/sys/fs/cgroup/user.slice/user-${HOST_UID}.slice/user@${HOST_UID}.service"
else
  CGROUP_ROOT="/sys/fs/cgroup"
fi
LEERIE_SLICE="$CGROUP_ROOT/leerie.slice"

# PID 1 creates the leerie.slice cgroup (at CGROUP_ROOT above) and enables
# the memory + pids controllers in its subtree_control so the per-worker
# child cgroups get the controller files. This is required for the
# aggregate memory.max cap written below, which runs before the broker
# launches. Per-worker enforcement itself is done by the root cgroup
# broker (launched further down), NOT by the orchestrator — non-root code
# cannot enroll workers or set controller limits in the rootful case
# (DESIGN §6 *Memory containment*); the broker's _detect() also
# creates/enables this slice idempotently, so this block is a
# belt-and-suspenders prerequisite for the aggregate cap. Best-effort:
# missing controllers, an older kernel without cgroup v2, a cgroup
# v1/hybrid host, or (in rootless mode) a host whose rootless init
# doesn't delegate this subtree (non-systemd, or systemd without
# pids/memory in the session delegate list) all cause these writes to
# fail silently — the broker probe then reports the hierarchy it can (or
# none), and the orchestrator's fail-closed gate decides whether to
# proceed. On Fly, Firecracker microVMs reboot the kernel fresh each
# machine start, so the slice never persists across boots. See DESIGN §6
# *Memory containment*.
if [ -d /sys/fs/cgroup ] && [ ! -d "$LEERIE_SLICE" ]; then
  mkdir -p "$LEERIE_SLICE" 2>/dev/null || true
fi
if [ -d "$LEERIE_SLICE" ]; then
  # Enable memory + pids controllers so child cgroups (per-worker) get
  # the controller files (memory.max, pids.max). Must happen as root
  # (rootful) or as the delegated UID (rootless).
  echo "+memory +pids" > "$LEERIE_SLICE/cgroup.subtree_control" 2>/dev/null || true

  # Aggregate memory cap on the slice itself (DESIGN §6 *container
  # boundary's hidden precondition*; IMPLEMENTATION §0.5). The per-worker
  # cgroups bound each worker; this bounds their SUM so the container as a
  # whole cannot drive the VM to a *global* OOM (which kills unprotected
  # host-session processes — the nerdctl client — orphaning the container
  # and wedging the flock). Capping leerie.slice/memory.max makes an
  # over-budget container trip a cgroup-scoped OOM (kills a worker inside
  # the slice) instead. Derived from VM MemTotal read here, so it is
  # portable across Colima and native Linux (the host launcher cannot read
  # the VM's MemTotal on macOS). Reserve headroom for PID 1 + the VM's own
  # daemons (sshd, lima-guestagent, containerd): cap = MemTotal - max(1GiB,
  # 12.5%). Overridable via LEERIE_CONTAINER_MEMORY_MAX_BYTES (e.g. from a
  # future launcher flag); "0"/"max" disables the cap. Best-effort: any
  # failure leaves the slice uncapped (prior behavior).
  # Sets memory.max (RAM) only, not memory.swap.max: a capped slice may
  # swap before the cgroup OOM fires, but that still contains the pressure
  # to the slice (no global OOM). Bounding total RAM+swap is a possible
  # future refinement, not needed to prevent the host-cascade wedge.
  _cap="${LEERIE_CONTAINER_MEMORY_MAX_BYTES:-}"
  if [ "$_cap" = "0" ] || [ "$_cap" = "max" ]; then
    _cap=""   # explicit opt-out
  elif [ -z "$_cap" ] && [ -r /proc/meminfo ]; then
    _memtotal_kb="$(awk '/^MemTotal:/ {print $2; exit}' /proc/meminfo 2>/dev/null)"
    if [ -n "$_memtotal_kb" ] && [ "$_memtotal_kb" -gt 0 ] 2>/dev/null; then
      _total_bytes=$((_memtotal_kb * 1024))
      _reserve=$((_total_bytes / 8))            # 12.5%
      _min_reserve=$((1024 * 1024 * 1024))      # 1 GiB floor
      [ "$_reserve" -lt "$_min_reserve" ] && _reserve="$_min_reserve"
      if [ "$_total_bytes" -gt "$_reserve" ]; then
        _cap=$((_total_bytes - _reserve))
      fi
    fi
  fi
  if [ -n "$_cap" ] && [ "$_cap" -gt 0 ] 2>/dev/null; then
    echo "$_cap" > "$LEERIE_SLICE/memory.max" 2>/dev/null || true
  fi
fi

# Cgroup broker (DESIGN §6 *Memory containment*). The orchestrator runs
# as non-root leerie, but cgroup enforcement — creating a worker cgroup,
# setting its pids.max/memory.max, and migrating the worker PID into it —
# cannot be done from the orchestrator's own (non-root, or rootless-mapped)
# identity: cross-scope task migration needs write on the common-ancestor
# cgroup leerie doesn't own, and (in the rootful case) the kernel keeps
# controller limit files root-owned in a subtree that was merely chowned
# rather than created by the delegatee (both reproduced). So we launch a
# tiny broker HERE, at PID 1 before the privilege drop, and the
# orchestrator drives it over a Unix socket. In the rootful case PID 1 is
# real root; in the rootless case PID 1 is the rootlesskit-mapped host
# UID, which is exactly the identity CGROUP_ROOT above is delegated to —
# no separate privilege level is needed or available. LEERIE_CGROUP_V2_ROOT
# tells the broker which root to operate under (default /sys/fs/cgroup
# when unset, i.e. every case except rootless). Best-effort: if it can't
# start, the orchestrator's probe round-trip fails and its fail-closed gate
# fires (or --dangerously-allow-uncapped downgrades to a warning). It runs on
# both runtimes; the idle-PID-1 Fly path below still needs it because the
# orchestrator is launched out-of-band via ssh and connects to this socket.
if command -v python3 >/dev/null 2>&1; then
  LEERIE_CGROUP_V2_ROOT="$CGROUP_ROOT" python3 /opt/leerie-image/scripts/cgroup-broker.py &
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
  # The Dockerfile's `mise install --system` creates /tmp/.cache/mise/
  # as root (XDG_CACHE_HOME=/tmp/.cache). On local nerdctl /tmp is an
  # ephemeral overlay so this is never seen; on Fly the rootfs preserves
  # root ownership and `mise install` fails with EACCES when a repo pins
  # a runtime version not pre-baked in the image.
  chown -R leerie: /tmp/.cache 2>/dev/null || true
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
