#!/usr/bin/env bash
# scripts/remote/build-push.sh — build and push a self-contained leerie image
# for Fly.io Machines (or any registry-pull environment).
#
# Unlike the local `leerie` build (which bind-mounts $LEERIE_REPO at runtime),
# the image produced here has the orchestrator source baked in at
# /opt/leerie-image/ via the Dockerfile's COPY instructions. A Fly Machine
# that pulls this image can run the orchestrator without any bind mount.
#
# Usage:
#   ./scripts/remote/build-push.sh [OPTIONS]
#
#   --app NAME            fly.io app name (default: LEERIE_FLY_APP env, required)
#   --registry REG        registry prefix (default: registry.fly.io/<APP>)
#   --tag TAG             override the full image tag (default: <REGISTRY>:<VERSION>);
#                         the image is pushed under exactly this tag, so the
#                         remote builder takes only registry.fly.io/<APP>:<label>
#   --dockerfile PATH     Dockerfile to build (default: $LEERIE_REPO/Dockerfile)
#   --build-arg KEY=VAL   build argument forwarded to flyctl/nerdctl (repeatable)
#   --context DIR         build context (default: $LEERIE_REPO); the launcher
#                         passes the committed tree for a repository image
#   --target STAGE        build only up to STAGE (the repository image's final
#                         stage, DESIGN §6 *The orchestrator's root is leerie's*)
#   --digest-file PATH    after the push, write the digest the pushed tag names
#                         (sha256:<hex>), read from the registry
#   --layout-base REF     after the push, require the pushed image to be REF's
#                         layers plus one, with REF's configuration (the
#                         repository image's layout); exit 1 when it is not
#   --push                build and push in one step (implied by both modes below)
#   --local-build         use local nerdctl/docker build+push (opt-in; see below)
#   --dry-run             print commands without executing
#   --help                show this message
#
# Default: Fly's remote builder (no local container runtime required).
# The remote builder runs Docker inside Fly's infrastructure, authenticates
# as the flyctl-logged-in user, and pushes to registry.fly.io implicitly.
# Works for everyone with `flyctl auth login` succeeded. No host-side
# Docker / Colima / Keychain dance.
#
# --local-build (opt-in): build locally with nerdctl or docker, then push.
# This path requires a working Docker daemon that can authenticate to
# registry.fly.io. In practice that means EITHER:
#   - Docker Desktop on macOS (with `flyctl auth docker` having run within
#     the last 5 minutes — the token expires fast)
#   - Linux with Docker installed via apt/dnf + `flyctl auth docker`
# It does NOT work with nerdctl-in-Colima on macOS, because nerdctl reads
# ~/.docker/config.json but cannot resolve the `credsStore: desktop`
# helper (it can't reach macOS Keychain from inside the Lima VM).
# Most leerie users should NOT pass --local-build.
#
# Verification after push (either mode):
#   flyctl machine run registry.fly.io/<APP>:<VERSION> \
#     --app <APP> \
#     -- python3 /opt/leerie-image/orchestrator/leerie.py version
#
# The version fast path reads /opt/leerie-image/.claude-plugin/plugin.json
# without starting the full orchestrator; success confirms the source is
# present at the expected path inside the image.
set -euo pipefail

# --- resolve script location ---------------------------------------------
SRC="${BASH_SOURCE[0]}"
hops=0
while [ -L "$SRC" ]; do
  hops=$((hops + 1))
  if [ "$hops" -gt 20 ]; then
    echo "build-push: refusing to resolve symlink chain deeper than 20 hops" >&2
    exit 1
  fi
  TARGET="$(readlink "$SRC")"
  case "$TARGET" in
    /*) SRC="$TARGET" ;;
    *)  SRC="$(cd -P "$(dirname "$SRC")" && pwd)/$TARGET" ;;
  esac
done
LEERIE_REPO="$(cd -P "$(dirname "$SRC")/../.." && pwd)"

# --- version (single source of truth: .claude-plugin/plugin.json) --------
LEERIE_VERSION="$(awk -F'"' '/"version"/ {print $4; exit}' \
                  "$LEERIE_REPO/.claude-plugin/plugin.json" 2>/dev/null || echo dev)"

# --- defaults -------------------------------------------------------------
FLY_APP="${LEERIE_FLY_APP:-}"
REGISTRY=""      # resolved below after --app is parsed
TAG_OVERRIDE=""
PUSH=false
DRY_RUN=false
DOCKERFILE_OVERRIDE=""
BUILD_ARGS=()
CONTEXT_DIR=""
BUILD_TARGET=""
DIGEST_FILE=""
LAYOUT_BASE=""
# BUILD_MODE: remote (default, via Fly's remote builder) or local
# (--local-build, via nerdctl/docker on the host). The launcher exports
# BUILD_MODE based on --local-build / LEERIE_LOCAL_BUILD; the --local-build
# flag below is the standalone-CLI equivalent.
BUILD_MODE="${BUILD_MODE:-remote}"
[ "${LEERIE_LOCAL_BUILD:-0}" = "1" ] && BUILD_MODE=local

# --- parse args ----------------------------------------------------------
while [ "$#" -gt 0 ]; do
  case "$1" in
    --app)
      shift; FLY_APP="$1" ;;
    --app=*)
      FLY_APP="${1#--app=}" ;;
    --registry)
      shift; REGISTRY="$1" ;;
    --registry=*)
      REGISTRY="${1#--registry=}" ;;
    --tag)
      shift; TAG_OVERRIDE="$1" ;;
    --tag=*)
      TAG_OVERRIDE="${1#--tag=}" ;;
    --push)
      PUSH=true ;;
    --local-build)
      BUILD_MODE=local ;;
    --dockerfile)
      shift; DOCKERFILE_OVERRIDE="$1" ;;
    --dockerfile=*)
      DOCKERFILE_OVERRIDE="${1#--dockerfile=}" ;;
    --build-arg)
      shift; BUILD_ARGS+=("$1") ;;
    --build-arg=*)
      BUILD_ARGS+=("${1#--build-arg=}") ;;
    --context)
      shift; CONTEXT_DIR="$1" ;;
    --target)
      shift; BUILD_TARGET="$1" ;;
    --digest-file)
      shift; DIGEST_FILE="$1" ;;
    --layout-base)
      shift; LAYOUT_BASE="$1" ;;
    --dry-run)
      DRY_RUN=true ;;
    --help|-h)
      sed -n '/^# Usage:/,/^[^#]/{ /^#/{ s/^# \?//; p }; /^[^#]/q }' "$0"
      exit 0
      ;;
    *)
      echo "build-push: unknown argument: $1" >&2
      exit 1 ;;
  esac
  shift
done

# Validate: FLY_APP must be set (either via LEERIE_FLY_APP env or --app flag).
if [ -z "$FLY_APP" ]; then
  echo "build-push: LEERIE_FLY_APP must be set (or pass --app <name>)" >&2
  exit 1
fi

# Resolve registry and tag now that --app may have been overridden.
if [ -z "$REGISTRY" ]; then
  REGISTRY="registry.fly.io/$FLY_APP"
fi
if [ -z "$TAG_OVERRIDE" ]; then
  IMAGE_TAG="$REGISTRY:$LEERIE_VERSION"
else
  IMAGE_TAG="$TAG_OVERRIDE"
fi

# Fly's remote builder pushes registry.fly.io/<APP>:<label> and nothing else
# (`flyctl deploy --image-label`). A tag outside that cannot be honoured, and
# pushing it anywhere else — the base tag, as an ignored --tag once did —
# overwrites an image something else runs (review round 29, isolation F1).
IMAGE_LABEL="${IMAGE_TAG##*:}"
if [ "$BUILD_MODE" = "remote" ]; then
  if [ "${IMAGE_TAG%:*}" != "registry.fly.io/$FLY_APP" ] \
     || ! [[ "$IMAGE_LABEL" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]]; then
    echo "build-push: Fly's remote builder pushes only registry.fly.io/$FLY_APP:<label>; it cannot push $IMAGE_TAG" >&2
    exit 1
  fi
fi

if { [ -n "$DIGEST_FILE" ] || [ -n "$LAYOUT_BASE" ]; } && [ "$PUSH" = "false" ]; then
  echo "build-push: --digest-file and --layout-base read the pushed image; pass --push" >&2
  exit 1
fi

echo "[build-push] leerie version: $LEERIE_VERSION"
echo "[build-push] image tag:    $IMAGE_TAG"
echo "[build-push] build mode:   $BUILD_MODE"
echo "[build-push] push:         $PUSH"
[ "$DRY_RUN" = "true" ] && echo "[build-push] DRY RUN — commands printed, not executed"

# --- run (or print) -------------------------------------------------------
run() {
  if [ "$DRY_RUN" = "true" ]; then
    echo "[dry-run] $*"
  else
    "$@"
  fi
}

# --- remote-builder path (default) ---------------------------------------
# Uses Fly's remote builder. The fly.toml in LEERIE_REPO has a
# `[build] image = "..."` line which is correct for `flyctl machine run`
# (leerie uses this elsewhere) but wrong for `flyctl deploy --build-only`:
# it tells flyctl "the image already exists, fetch it" → flyctl skips the
# build step → deploy fails with "Could not find image". flyctl#1686
# documents this confusing interaction. Workaround: cp fly.toml to a tmp
# file with the `image = ...` line stripped from the [build] section,
# pass config $tmp.
_build_push_remote() {
  local tmp_toml
  tmp_toml="$(mktemp -t leerie-build.XXXXXX.toml)"
  # awk strips the `image = ...` line only while inside the [build] section.
  awk '
    /^\[build\]/ { in_build=1; print; next }
    /^\[/ && !/^\[build\]/ { in_build=0; print; next }
    in_build && /^[[:space:]]*image[[:space:]]*=/ { next }
    { print }
  ' "$LEERIE_REPO/fly.toml" > "$tmp_toml"

  echo "[build-push] using temp fly.toml at $tmp_toml ([build] image stripped)" >&2

  # `flyctl deploy --build-only --push --remote-only` is the canonical
  # build-and-push-without-deploying invocation (per fly community thread
  # https://community.fly.io/t/how-to-build-and-push-docker-image-without-deploying/25746/1).
  # --image-label is the tag's own label, so the result is exactly
  # $IMAGE_TAG (registry.fly.io/$FLY_APP:<label>, checked above).
  #
  # --depot=false: force flyctl to use the legacy remote builder rather
  # than depot.dev. Depot caches the resolved tag-to-digest mapping per
  # `--image-label`, which means a rebuild with the same label (e.g.
  # 0.2.1 → 0.2.1) returns the OLD digest even after we push new
  # layers. The result: machines end up running the prior image. Legacy
  # builder doesn't have this issue. Documented at
  # https://community.fly.io/t/when-i-run-fly-deploy-with-the-image-label-flag-why-does-it-deploy-an-older-version-of-my-code/26151
  #
  # We run from a subshell with cwd=$LEERIE_REPO because `flyctl deploy`
  # uses the cwd (or the directory containing --dockerfile) as the
  # build context, and the Dockerfile's COPY instructions reference
  # paths relative to that context (orchestrator/, scripts/, etc.).
  # Without the cd, calling leerie from another repo (e.g. `cd ~/myrepo
  # && leerie ... --runtime fly`) would upload the user's repo as the
  # build context instead of leerie's, and the build fails with
  # "orchestrator: not found".
  local dockerfile="${DOCKERFILE_OVERRIDE:-$LEERIE_REPO/Dockerfile}"
  local build_arg_flags=()
  for arg in "${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}"; do
    build_arg_flags+=(--build-arg "$arg")
  done

  local target_flags=()
  [ -n "$BUILD_TARGET" ] && target_flags+=(--build-target "$BUILD_TARGET")

  local rc=0
  (
    cd "${CONTEXT_DIR:-$LEERIE_REPO}"
    run flyctl deploy \
      --build-only --push --remote-only --depot=false \
      --app "$FLY_APP" \
      --config "$tmp_toml" \
      --dockerfile "$dockerfile" \
      "${build_arg_flags[@]+"${build_arg_flags[@]}"}" \
      "${target_flags[@]+"${target_flags[@]}"}" \
      --image-label "$IMAGE_LABEL"
  ) || rc=$?
  rm -f "$tmp_toml"
  if [ "$rc" -ne 0 ]; then
    echo "build-push: remote build failed. If you have a working Docker daemon" >&2
    echo "  with valid registry.fly.io auth (Docker Desktop + flyctl auth docker)," >&2
    echo "  retry with --local-build." >&2
    return 1
  fi
  echo "[build-push] remote build complete: $IMAGE_TAG"
}

# --- local-build path (opt-in: --local-build) ----------------------------
# Builds with nerdctl or docker on the host, then pushes. Requires a
# working Docker daemon authenticated to registry.fly.io. See the file
# header for the (narrow) conditions under which this path works.
_build_push_local() {
  local build_cmd=""
  if command -v nerdctl >/dev/null 2>&1; then
    build_cmd="nerdctl"
  elif command -v docker >/dev/null 2>&1; then
    build_cmd="docker"
  elif [ "$DRY_RUN" = "false" ]; then
    echo "build-push: --local-build requires nerdctl or docker on PATH." >&2
    echo "  Without it, drop --local-build to use the remote builder." >&2
    return 1
  else
    build_cmd="nerdctl"  # dry-run: assume nerdctl
  fi
  echo "[build-push] local build tool: $build_cmd"

  local dockerfile_flags=()
  if [ -n "$DOCKERFILE_OVERRIDE" ]; then
    dockerfile_flags+=(-f "$DOCKERFILE_OVERRIDE")
  fi
  local build_arg_flags=()
  for arg in "${BUILD_ARGS[@]+"${BUILD_ARGS[@]}"}"; do
    build_arg_flags+=(--build-arg "$arg")
  done

  # Build without HOST_UID/HOST_GID: the Dockerfile ARG defaults (501/20)
  # apply. Source is baked in via COPY instructions — no bind mount required.
  local target_flags=()
  [ -n "$BUILD_TARGET" ] && target_flags+=(--target "$BUILD_TARGET")
  run "$build_cmd" build \
    "${dockerfile_flags[@]+"${dockerfile_flags[@]}"}" \
    "${build_arg_flags[@]+"${build_arg_flags[@]}"}" \
    "${target_flags[@]+"${target_flags[@]}"}" \
    -t "$IMAGE_TAG" \
    "${CONTEXT_DIR:-$LEERIE_REPO}"

  echo "[build-push] local build complete: $IMAGE_TAG"

  # Verify entrypoint + smoke test version on the built image.
  if [ "$DRY_RUN" = "false" ]; then
    local entry
    entry="$("$build_cmd" inspect "$IMAGE_TAG" \
              --format '{{join .Config.Entrypoint " "}}' 2>/dev/null || true)"
    local expected="/opt/leerie-image/scripts/container-entry.sh"
    if [ "$entry" = "$expected" ]; then
      echo "[build-push] entrypoint OK: $entry"
    else
      echo "build-push: WARNING — entrypoint mismatch" >&2
      echo "  expected: $expected" >&2
      echo "  got:      $entry" >&2
    fi

    echo "[build-push] smoke: leerie version (baked source, no bind mount) ..."
    if run "$build_cmd" run --rm "$IMAGE_TAG" \
         python3 -I /opt/leerie-image/orchestrator/leerie.py version; then
      echo "[build-push] smoke OK"
    else
      echo "build-push: WARNING — version smoke failed (baked source not working)" >&2
    fi
  fi

  if [ "$PUSH" = "true" ]; then
    echo "[build-push] pushing $IMAGE_TAG ..."
    run "$build_cmd" push "$IMAGE_TAG"
    echo "[build-push] pushed: $IMAGE_TAG"
  fi
}

# --- what was pushed -------------------------------------------------------
# The digest the pushed tag names, and the repository image's layout, read
# from the registry (scripts/remote/registry_image.py). Fly's registry takes
# the flyctl token as its password; another registry is read anonymously.
_pushed_image() {
  local password="" digest
  if [ "${IMAGE_TAG%%/*}" = "registry.fly.io" ] && [ "$DRY_RUN" = "false" ]; then
    password="$(flyctl auth token 2>/dev/null)" || password=""
  fi
  if [ "$DRY_RUN" = "true" ]; then
    echo "[dry-run] python3 -I $LEERIE_REPO/scripts/remote/registry_image.py digest $IMAGE_TAG"
    [ -n "$LAYOUT_BASE" ] && echo "[dry-run] python3 -I $LEERIE_REPO/scripts/remote/registry_image.py layout $LAYOUT_BASE ${IMAGE_TAG%:*}@<digest>"
    return 0
  fi
  digest="$(printf '%s' "$password" | python3 -I "$LEERIE_REPO/scripts/remote/registry_image.py" digest "$IMAGE_TAG")" \
    || { echo "build-push: could not read the digest of $IMAGE_TAG from its registry" >&2; return 1; }
  echo "[build-push] pushed digest: $digest"
  if [ -n "$LAYOUT_BASE" ]; then
    if ! printf '%s' "$password" | python3 -I "$LEERIE_REPO/scripts/remote/registry_image.py" \
           layout "$LAYOUT_BASE" "${IMAGE_TAG%:*}@$digest"; then
      echo "build-push: ${IMAGE_TAG%:*}@$digest is not $LAYOUT_BASE plus its worker root; it is not used" >&2
      return 1
    fi
    echo "[build-push] layout OK: $LAYOUT_BASE plus one layer"
  fi
  if [ -n "$DIGEST_FILE" ]; then
    printf '%s\n' "$digest" > "$DIGEST_FILE"
  fi
}

# --- dispatch -------------------------------------------------------------
case "$BUILD_MODE" in
  remote)
    if [ "$PUSH" = "false" ]; then
      echo "build-push: remote build always pushes (Fly's remote builder pushes inline)." >&2
      echo "  Either pass --push or drop --local-build for the standalone build path." >&2
      exit 1
    fi
    _build_push_remote
    ;;
  local)
    _build_push_local
    ;;
  *)
    echo "build-push: unknown BUILD_MODE: $BUILD_MODE (expected 'remote' or 'local')" >&2
    exit 1
    ;;
esac
if [ -n "$DIGEST_FILE" ] || [ -n "$LAYOUT_BASE" ]; then
  _pushed_image || exit 1
fi

echo ""
echo "To start on Fly.io:"
echo "  flyctl machine run $IMAGE_TAG --app $FLY_APP"
echo ""
echo "To verify inside the machine:"
echo "  flyctl machine run $IMAGE_TAG --app $FLY_APP \\"
echo "    -- python3 -I /opt/leerie-image/orchestrator/leerie.py version"
