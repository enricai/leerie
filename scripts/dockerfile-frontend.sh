# shellcheck shell=bash
# The Dockerfile frontend every build leerie runs is given, as
# `--build-arg BUILDKIT_SYNTAX=$LEERIE_DOCKERFILE_FRONTEND` (DESIGN §6 *A
# repository's parser directives are its own build's; its frontend is
# leerie's*). BuildKit's Dockerfile frontend reads that argument before the
# file's `# syntax=` line, so a repository's file is built byte for byte by
# this frontend and never by one it names. `--no-cache` reaches BuildKit only
# as an option this frontend reads, so it holds only because leerie chose it.
# Also the containerd namespace leerie keeps its images in, and the checks
# that no BuildKit worker reads it (LEERIE_IMAGE_NS, leerie_buildkit_check).
# Sourced by the launcher and by scripts/remote/build-push.sh; it runs nothing.
#
# The digest is the multi-platform image index `docker/dockerfile:1` resolved
# to on 2026-11-03 (`nerdctl pull docker/dockerfile:1`, then
# `nerdctl image ls --digests`). To bump it, resolve the tag again the same
# way and replace the digest; keep `:1`, the release line whose grammar the
# launcher's context reading (_repo_image_dockerfile) follows. A bump
# changes the frontend of every build, leerie's base included, so it ships
# in a leerie release.
# shellcheck disable=SC2034  # read by the files that source this one
LEERIE_DOCKERFILE_FRONTEND="docker/dockerfile:1@sha256:4edf897a3ffa55b89f906fc8cc78afdb3f1834cc9c7083565e611a8a7d5fe99e"

# LEERIE_IMAGE_NS — the containerd namespace leerie keeps its images in and
# runs its containers in, which no BuildKit worker may read (DESIGN §6 *No
# build consumes what another build left in BuildKit*): a build resolves a
# name the registry refuses from the namespace its worker reads, so an image
# there is one any repository's Dockerfile reads by naming it. Every nerdctl
# command leerie runs on its own images and containers names it.
LEERIE_IMAGE_NS="leerie"

# The BuildKit daemon leerie's builds run on and the containerd namespaces
# its workers read (space-separated), set by leerie_buildkit_resolve; and
# why leerie_buildkit_check or leerie_sweep_worker_images refused.
LEERIE_BUILDKIT_HOST=""
LEERIE_BUILDKIT_WORKER_NS=""
LEERIE_BUILDKIT_REFUSAL=""

# _leerie_on_buildkit_side <cmd> [args...] — runs <cmd> where nerdctl runs
# buildctl: on this host, or as root in Colima's VM on macOS, whose nerdctl
# is a shim into the VM (not measured on macOS).
_leerie_on_buildkit_side() {
  if [ "$(uname -s)" = "Darwin" ]; then
    colima ssh -- "$(printf '%q ' sudo "$@")"
  else
    "$@"
  fi
}

# leerie_buildkit_resolve — finds the BuildKit daemon that `nerdctl
# --namespace $LEERIE_IMAGE_NS build` uses, as nerdctl 2.3.5 finds it:
# $BUILDKIT_HOST, else the first of buildkit-<namespace>, buildkit-default
# and buildkit under the runtime directory ($XDG_RUNTIME_DIR rootless, /run
# as root and in Colima's VM) whose `buildctl debug workers` succeeds. Sets
# LEERIE_BUILDKIT_HOST to it and LEERIE_BUILDKIT_WORKER_NS to the
# namespaces its containerd workers read, from each worker's
# org.mobyproject.buildkit.worker.containerd.namespace label. Returns 1 when
# no daemon answers, or the one that does has no containerd worker or a
# label that is not a containerd namespace name. Asks once per process.
leerie_buildkit_resolve() {
  [ -n "$LEERIE_BUILDKIT_HOST" ] && return 0
  local _d _a _out _w _c=()
  if [ -n "${BUILDKIT_HOST:-}" ]; then
    _c=("$BUILDKIT_HOST")
  else
    if [ "$(uname -s)" = "Darwin" ] || [ "$(id -u)" = "0" ]; then
      _d=/run
    else
      _d="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
    fi
    _c=("unix://$_d/buildkit-$LEERIE_IMAGE_NS/buildkitd.sock"
        "unix://$_d/buildkit-default/buildkitd.sock"
        "unix://$_d/buildkit/buildkitd.sock")
  fi
  for _a in "${_c[@]}"; do
    _out="$(_leerie_on_buildkit_side buildctl --addr "$_a" debug workers \
              --format '{{json .}}' 2>/dev/null)" || continue
    # The first daemon that answers is nerdctl's, whatever its workers.
    _w="$(printf '%s' "$_out" | python3 -I -S -c '
import json, re, sys
try:
    ws = json.load(sys.stdin)
except ValueError:
    sys.exit(1)
if not isinstance(ws, list):
    sys.exit(1)
out = []
for w in ws:
    labels = w.get("labels") if isinstance(w, dict) else None
    if not isinstance(labels, dict):
        continue
    ns = labels.get("org.mobyproject.buildkit.worker.containerd.namespace")
    if ns is None:
        continue
    if not isinstance(ns, str) or not re.fullmatch(
            r"[A-Za-z0-9]+(?:[._-][A-Za-z0-9]+)*", ns):
        sys.exit(1)
    if ns not in out:
        out.append(ns)
print(" ".join(out))
')" || return 1
    [ -n "$_w" ] || return 1
    LEERIE_BUILDKIT_HOST="$_a"
    LEERIE_BUILDKIT_WORKER_NS="$_w"
    return 0
  done
  return 1
}

# leerie_buildkit_check — returns 0 when leerie_buildkit_resolve found the
# daemon and none of its workers reads $LEERIE_IMAGE_NS; else 1, with the
# reason in LEERIE_BUILDKIT_REFUSAL. Every build of a repository image is
# preceded by it and runs on $LEERIE_BUILDKIT_HOST: leerie does not build
# one where it cannot tell which images the build can name.
leerie_buildkit_check() {
  local _w
  LEERIE_BUILDKIT_REFUSAL=""
  if ! leerie_buildkit_resolve; then
    LEERIE_BUILDKIT_REFUSAL="could not ask BuildKit which containerd namespace its worker reads (buildctl debug workers): leerie builds a repository's image only where it can tell which images the build can name (DESIGN §6 *No build consumes what another build left in BuildKit*)"
    return 1
  fi
  for _w in $LEERIE_BUILDKIT_WORKER_NS; do
    if [ "$_w" = "$LEERIE_IMAGE_NS" ]; then
      LEERIE_BUILDKIT_REFUSAL="BuildKit's worker at $LEERIE_BUILDKIT_HOST reads the containerd namespace $LEERIE_IMAGE_NS, where leerie keeps its images, so a repository's build could read any of them by name; give buildkitd another --containerd-worker-namespace (DESIGN §6 *No build consumes what another build left in BuildKit*)"
      return 1
    fi
  done
  return 0
}

# leerie_sweep_worker_images — removes, from every namespace in
# LEERIE_BUILDKIT_WORKER_NS, each image named leerie-repo/… (leerie's name
# for a repository image, and nothing else; an earlier leerie tagged them
# in nerdctl's namespace) that no container uses: `nerdctl image rm`, never
# forced. Returns 1, with the reason in LEERIE_BUILDKIT_REFUSAL, when one is
# left, or when a namespace's images cannot be listed. Needs
# leerie_buildkit_resolve first.
leerie_sweep_worker_images() {
  local _w _n _out _left=""
  LEERIE_BUILDKIT_REFUSAL=""
  for _w in $LEERIE_BUILDKIT_WORKER_NS; do
    _out="$(nerdctl --namespace "$_w" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)" \
      || { LEERIE_BUILDKIT_REFUSAL="could not list the images in $_w, the containerd namespace BuildKit's worker reads"; return 1; }
    while IFS= read -r _n; do
      case "$_n" in
        leerie-repo/*|docker.io/leerie-repo/*)
          nerdctl --namespace "$_w" image rm "$_n" >/dev/null 2>&1 || true ;;
      esac
    done <<EOF_IMAGES
$_out
EOF_IMAGES
    _out="$(nerdctl --namespace "$_w" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null)" \
      || { LEERIE_BUILDKIT_REFUSAL="could not list the images in $_w, the containerd namespace BuildKit's worker reads"; return 1; }
    while IFS= read -r _n; do
      case "$_n" in
        leerie-repo/*|docker.io/leerie-repo/*) _left="$_left $_n" ;;
      esac
    done <<EOF_IMAGES
$_out
EOF_IMAGES
    if [ -n "$_left" ]; then
      LEERIE_BUILDKIT_REFUSAL="the containerd namespace BuildKit's worker reads ($_w) still holds an image an earlier leerie built for a repository, which a container still uses:$_left. A repository's build could read it by name; launch again once that run has ended, or remove it with: nerdctl --namespace $_w image rm <name> (DESIGN §6 *No build consumes what another build left in BuildKit*)"
      return 1
    fi
  done
  return 0
}

# ensure_frontend_in_worker_ns — pulls the pinned frontend into each
# containerd namespace BuildKit's workers read (leerie_buildkit_resolve),
# where an image outlives a prune of BuildKit's cache: a later build then
# needs no network for its frontend, while a frontend BuildKit pulled itself
# is gone after a prune (measured; DESIGN §6 *Residuals*). With the
# operator's Docker config: the frontend is leerie's choice, not a
# repository's. Best-effort: without it BuildKit resolves the frontend
# itself, as it did a `# syntax=` line's. Needs remote_log.
ensure_frontend_in_worker_ns() {
  local _w
  if ! leerie_buildkit_resolve; then
    remote_log "warning: could not ask BuildKit which containerd namespace its worker reads; BuildKit will fetch the Dockerfile frontend itself"
    return 0
  fi
  for _w in $LEERIE_BUILDKIT_WORKER_NS; do
    if nerdctl --namespace "$_w" image inspect "$LEERIE_DOCKERFILE_FRONTEND" \
         >/dev/null 2>&1; then
      continue
    fi
    if ! nerdctl --namespace "$_w" pull -q "$LEERIE_DOCKERFILE_FRONTEND" \
         >/dev/null 2>&1; then
      remote_log "warning: could not pull the Dockerfile frontend into $_w, the namespace BuildKit's worker reads; BuildKit will fetch it itself"
    fi
  done
  return 0
}

# leerie_prune_cache_mounts <ns> — removes from the daemon at
# $LEERIE_BUILDKIT_HOST the cache-mount records a build given
# BUILDKIT_CACHE_MOUNT_NS=<ns> left: their description names the mount's id,
# which the pinned frontend prefixes with `<ns>/` (`buildctl prune --filter
# 'type==exec.cachemount,description~="<ns>/"'`; measured 2026-11-03,
# BuildKit 0.31.2: it removed that build's record and no other). No later
# build can name <ns>, so the records are dead once the build ends, and
# BuildKit's own collection did not reclaim them (review round 37,
# execution E4: 13 builds left 838 MB). Returns 1 when the daemon is not
# known or the prune fails; the records are then left to BuildKit's
# collection.
leerie_prune_cache_mounts() {
  [ -n "$LEERIE_BUILDKIT_HOST" ] && [ -n "${1:-}" ] || return 1
  _leerie_on_buildkit_side buildctl --addr "$LEERIE_BUILDKIT_HOST" prune \
    --filter "type==exec.cachemount,description~=\"$1/\"" >/dev/null 2>&1
}

# leerie_cache_mount_ns — prints a fresh cache-mount namespace,
# `leerie-<32 hex>` from 16 bytes of /dev/urandom, for one build's
# `--build-arg BUILDKIT_CACHE_MOUNT_NS=` (DESIGN §6 *No build consumes what
# another build left in BuildKit*). The pinned frontend puts it in front of
# every cache mount's id, so no two builds share a mount even while both run:
# `--no-cache` empties a mount only when its build's steps are loaded, and a
# build running alongside is handed the live one. Returns 1, printing
# nothing, unless it read all 16 bytes: an empty value would be one namespace
# shared by every build, so a caller assigns it first and fails the build.
leerie_cache_mount_ns() {
  local _r
  _r="$(od -An -N16 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n')" || return 1
  case "$_r" in
    *[!0-9a-f]*) return 1 ;;
  esac
  [ "${#_r}" -eq 32 ] || return 1
  printf 'leerie-%s\n' "$_r"
}
