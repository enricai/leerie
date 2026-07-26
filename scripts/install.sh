#!/usr/bin/env bash
# install.sh — one-command installer for Leerie.
#
#   curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash
#
# What this does, in order:
#   1. Verifies `git` + `curl` are on PATH; auto-installs the `claude` CLI if
#      missing via Anthropic's official native installer
#      (`curl -fsSL https://claude.ai/install.sh | bash` — a self-contained
#      binary in ~/.local/bin, no Node/npm). Pass --no-claude-install (or
#      LEERIE_NO_CLAUDE_INSTALL=1) to skip — falls back to a hint and exits 1.
#      Leerie shells out to `claude -p` for every unit of LLM work.
#   2. Runtime: installs the container runtime if missing AND starts it.
#      - macOS:    `brew install colima` + `colima start --runtime containerd --mount-type virtiofs --cpu N --memory M`
#                  (N/M auto-detected: half host CPU/RAM, clamped 2..8 / 4..16 GB — see _runtime_colima_size_flags)
#      - Debian/Ubuntu: a full rootless containerd stack — containerd +
#                  rootlesskit/slirp4netns/uidmap (apt), pinned nerdctl, CNI
#                  plugins, BuildKit, the rootless setuptool + buildkit worker,
#                  and an end-to-end `nerdctl info` reachability check. See
#                  runtime_install_linux in runtime-install.sh.
#      - Fedora/Arch: not auto-installed yet — falls back to a docs hint
#                  (docs/INSTALL.md "Rootless mode") + exit 1.
#      Pass --no-runtime-install (or LEERIE_NO_RUNTIME_INSTALL=1) to skip
#      auto-install — the installer falls back to a hint and exits 1.
#      Unknown distros always fall back to the hint.
#   3. Clones (or fast-forwards) enricai/leerie into $LEERIE_HOME (default ~/.leerie).
#   4. Symlinks $LEERIE_HOME/leerie into ~/.local/bin/leerie.
#   5. Verifies the install with `leerie --version`.
#
# Leerie runs entirely inside a container (DESIGN §6 / IMPLEMENTATION §0.5),
# so Python is provisioned by the image at runtime — the host doesn't need
# Python or `uv` anymore. The launcher's --version fast path returns
# without spinning up a container.
#
# Flags:
#   --dry-run                Print actions without executing.
#   --no-runtime-install     Skip auto-install of the container runtime;
#                            print the manual hint and exit 1 if missing.
#   --no-claude-install      Skip auto-install of the claude CLI;
#                            print the manual hint and exit 1 if missing.
#   --prefix DIR             Install Leerie under DIR (default: $LEERIE_HOME or ~/.leerie).
#   --bin-dir DIR            Symlink dir (default: ~/.local/bin).
#   --ref REF                Git ref to install (default: main).
#   --help                   Show this message and exit.
#
# Env vars:
#   LEERIE_HOME                 Install directory (default ~/.leerie). --prefix overrides.
#   LEERIE_BIN_DIR              Symlink directory (default ~/.local/bin). --bin-dir overrides.
#   LEERIE_REPO_URL             Repo URL to clone (default https://github.com/enricai/leerie.git).
#   LEERIE_NO_RUNTIME_INSTALL   Same as --no-runtime-install when truthy ("1", "true", "yes").
#   LEERIE_NO_CLAUDE_INSTALL    Same as --no-claude-install when truthy ("1", "true", "yes").
set -euo pipefail

# --- defaults ------------------------------------------------------------

# Guard against an unset HOME (some CI containers, broken cron envs, minimal
# Docker images). Without this, $HOME/.leerie expands to /.leerie and the
# install silently tries to write under the root filesystem.
: "${HOME:?HOME is unset; cannot compute install prefix. Set HOME (or LEERIE_HOME + LEERIE_BIN_DIR) and retry.}"

DEFAULT_REPO_URL="https://github.com/enricai/leerie.git"
DEFAULT_REF="main"

PREFIX="${LEERIE_HOME:-$HOME/.leerie}"
BIN_DIR="${LEERIE_BIN_DIR:-$HOME/.local/bin}"
REPO_URL="${LEERIE_REPO_URL:-$DEFAULT_REPO_URL}"
REF="$DEFAULT_REF"
DRY_RUN=false

# Truthy detector: "1" / "true" / "yes" → true; anything else → false.
# Used to interpret LEERIE_NO_RUNTIME_INSTALL.
case "${LEERIE_NO_RUNTIME_INSTALL:-}" in
  1|true|TRUE|yes|YES) NO_RUNTIME_INSTALL=true ;;
  *)                   NO_RUNTIME_INSTALL=false ;;
esac
# Same detector for LEERIE_NO_CLAUDE_INSTALL — opt out of auto-installing the
# claude CLI when it's missing (mirrors --no-runtime-install). When true, a
# missing claude falls back to the printed hint + exit 1 (the pre-auto-install
# behavior).
case "${LEERIE_NO_CLAUDE_INSTALL:-}" in
  1|true|TRUE|yes|YES) NO_CLAUDE_INSTALL=true ;;
  *)                   NO_CLAUDE_INSTALL=false ;;
esac
# Pinned nerdctl version used by the Linux Debian path. Matches the version
# documented in docs/INSTALL.md. Set BEFORE sourcing runtime-install.sh so the
# helper inherits it.
# shellcheck disable=SC2034  # consumed by runtime-install.sh (sourced below)
NERDCTL_VERSION=2.3.1

# --- helpers -------------------------------------------------------------

usage() {
  cat <<'EOF'
install.sh — one-command installer for Leerie.

  curl -fsSL https://raw.githubusercontent.com/enricai/leerie/main/scripts/install.sh | bash

What this does, in order:
  1. Verifies `git` + `curl` are on PATH; auto-installs the `claude` CLI if
     missing (Anthropic's official native installer). Pass --no-claude-install
     to skip (fall back to hint + exit 1).
  2. Runtime: installs the container runtime if missing AND starts it
     (Colima on macOS via brew; a rootless containerd stack on Debian/Ubuntu.
     Fedora/Arch fall back to a docs hint). Pass --no-runtime-install to skip
     auto-install (fall back to hint + exit 1).
  3. Clones (or fast-forwards) enricai/leerie into $LEERIE_HOME (default ~/.leerie).
  4. Symlinks $LEERIE_HOME/leerie into ~/.local/bin/leerie.
  5. Verifies the install with `leerie --version`.

Flags:
  --dry-run                Print actions without executing.
  --no-runtime-install     Skip auto-install of the container runtime.
  --no-claude-install      Skip auto-install of the claude CLI.
  --prefix DIR             Install Leerie under DIR (default: $LEERIE_HOME or ~/.leerie).
  --bin-dir DIR            Symlink dir (default: ~/.local/bin).
  --ref REF                Git ref to install (default: main).
  --help                   Show this message and exit.

Env vars:
  LEERIE_HOME                 Install directory (default ~/.leerie). --prefix overrides.
  LEERIE_BIN_DIR              Symlink directory (default ~/.local/bin). --bin-dir overrides.
  LEERIE_REPO_URL             Repo URL to clone (default https://github.com/enricai/leerie.git).
  LEERIE_NO_RUNTIME_INSTALL   Same as --no-runtime-install when truthy ("1", "true", "yes").
  LEERIE_NO_CLAUDE_INSTALL    Same as --no-claude-install when truthy ("1", "true", "yes").
EOF
}

log() {
  printf 'install: %s\n' "$*"
}

err() {
  printf 'install: error: %s\n' "$*" >&2
}

run() {
  # Print the command, then run it — or just print it under --dry-run.
  printf '  $ %s\n' "$*"
  if [ "$DRY_RUN" = "false" ]; then
    "$@"
  fi
}

have_runnable() {
  # `command -v` returns success for shimmed entries (pyenv) that can't
  # actually exec — invoke `--version` to confirm it really runs.
  "$1" --version >/dev/null 2>&1
}

remediate_git() {
  case "$(uname -s)" in
    Darwin) err "git is missing. Install with: xcode-select --install   (or: brew install git)" ;;
    Linux)  err "git is missing. Install with your distro's package manager (apt install git / dnf install git / pacman -S git)." ;;
    *)      err "git is missing. Install it from https://git-scm.com/" ;;
  esac
}

remediate_claude() {
  err "claude CLI is missing. Install Claude Code from https://claude.ai/code"
  err "Leerie shells out to \`claude -p\` for every unit of LLM work; there is no fallback."
  err "(Auto-install is on by default; this hint prints only when"
  err " --no-claude-install / LEERIE_NO_CLAUDE_INSTALL=1 is set, or the"
  err " auto-install itself failed.)"
}

# Auto-install the claude CLI via Anthropic's official native installer
# (curl -fsSL https://claude.ai/install.sh | bash). It downloads a
# self-contained binary — no Node/npm — installs to ~/.local/bin/claude, and
# registers PATH. It refuses to run under sudo, so we never wrap it in sudo.
# Mirrors the container-runtime auto-install: leerie shells out to `claude -p`
# for every unit of LLM work, so a missing claude is a hard stop; installing it
# for the user removes the single most common first-run wall. Returns the
# installer's exit code; the caller re-verifies runnability regardless.
install_claude() {
  log "installing the claude CLI via the official native installer"
  run bash -c 'curl -fsSL https://claude.ai/install.sh | bash'
}

remediate_curl() {
  case "$(uname -s)" in
    Darwin) err "curl is missing. Install with: brew install curl   (macOS ships curl by default; reinstall if it's gone.)" ;;
    Linux)  err "curl is missing. Install with your distro's package manager (apt install curl / dnf install curl / pacman -S curl)." ;;
    *)      err "curl is missing. Install it from https://curl.se/" ;;
  esac
}

# Runtime-install helpers (runtime_install_macos, runtime_install_linux,
# _runtime_detect_distro, _runtime_nerdctl_arch) live in
# scripts/runtime-install.sh — sourced below after argument parsing so
# DRY_RUN / NERDCTL_VERSION are already set in the environment.

# --- argument parsing ----------------------------------------------------

while [ $# -gt 0 ]; do
  case "$1" in
    --dry-run)              DRY_RUN=true; shift ;;
    --no-runtime-install)   NO_RUNTIME_INSTALL=true; shift ;;
    --no-claude-install)    NO_CLAUDE_INSTALL=true; shift ;;
    --prefix)               PREFIX="${2:?--prefix needs an argument}"; shift 2 ;;
    --bin-dir)              BIN_DIR="${2:?--bin-dir needs an argument}"; shift 2 ;;
    --ref)                  REF="${2:?--ref needs an argument}"; shift 2 ;;
    -h|--help)              usage; exit 0 ;;
    *)
      err "unrecognized arg: $1"
      usage >&2
      exit 2
      ;;
  esac
done

# --- source runtime-install helpers --------------------------------------
# DRY_RUN and NERDCTL_VERSION are already set above; runtime-install.sh
# inherits them. The helper defines underscore-prefixed log/err/run
# functions so they don't shadow this installer's own helpers.
#
# Three-tier resolution so `curl | bash` works (where $0 is "bash", not a
# file path): (1) adjacent to this script (local checkout / direct exec),
# (2) already-cloned repo at $PREFIX, (3) download from GitHub.
_source_runtime_helpers() {
  # Tier 1: script is a real file on disk (local checkout / direct exec).
  local script_dir
  script_dir="$(cd "$(dirname "$0")" 2>/dev/null && pwd)" || script_dir=""
  if [ -n "$script_dir" ] && [ -f "$script_dir/runtime-install.sh" ]; then
    # shellcheck disable=SC1091
    . "$script_dir/runtime-install.sh"
    return 0
  fi
  # Tier 2: repo already cloned at $PREFIX (re-run / update).
  if [ -f "$PREFIX/scripts/runtime-install.sh" ]; then
    . "$PREFIX/scripts/runtime-install.sh"
    return 0
  fi
  # Tier 3: download from GitHub (first-time curl|bash).
  local tmp
  tmp="$(mktemp "${TMPDIR:-/tmp}/leerie-runtime-install.XXXXXX")"
  # Use a fixed path in the trap so it works after the local goes out of scope.
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" EXIT
  local raw_url="https://raw.githubusercontent.com/enricai/leerie/${REF}/scripts/runtime-install.sh"
  if curl -fsSL "$raw_url" -o "$tmp"; then
    # shellcheck source=/dev/null
    . "$tmp"
    return 0
  fi
  err "cannot locate runtime-install.sh (tried script dir, $PREFIX, and GitHub download)"
  return 1
}
_source_runtime_helpers || exit 1

# --- 1. preflight: git + claude + curl -----------------------------------
# curl is required to download the repo (and for the runtime preflight's
# nerdctl-from-upstream guidance on Linux).

log "preflight: checking git + curl on PATH; auto-installing claude if missing"
missing=0
# git and curl are OS-package prerequisites leerie doesn't own — check only,
# with a distro-appropriate hint. curl in particular is needed *by* the claude
# auto-install below (and by _source_runtime_helpers), so verify it first.
if ! have_runnable git; then
  remediate_git
  missing=1
fi
if ! have_runnable curl; then
  remediate_curl
  missing=1
fi
# claude is leerie-owned in the sense that every unit of work shells out to it,
# so we auto-install it (like the container runtime) unless the user opted out.
# The official native installer lands in ~/.local/bin, which may not be on the
# current PATH yet — re-verify against that path before failing.
if ! have_runnable claude; then
  if [ "$NO_CLAUDE_INSTALL" = "true" ]; then
    remediate_claude
    missing=1
  elif ! have_runnable curl; then
    # curl already failed above; the claude installer needs it. Fall back to
    # the manual hint rather than attempting an install that can't run.
    remediate_claude
    missing=1
  else
    install_claude || true
    # Re-verify. The native installer commonly lands in ~/.local/bin; add it
    # to PATH for this check so a just-installed claude is found even if the
    # user's PATH doesn't include it yet (the PATH-check step below hints the
    # user to add ~/.local/bin permanently). Skipped under --dry-run since
    # nothing was actually installed.
    if [ "$DRY_RUN" = "false" ] \
       && ! PATH="$HOME/.local/bin:$PATH" have_runnable claude; then
      err "claude auto-install ran but claude is still not runnable."
      remediate_claude
      missing=1
    fi
  fi
fi
if [ "$missing" -ne 0 ]; then
  exit 1
fi

# --- 2. runtime: install if missing AND start ---------------------------
# Auto-install Colima on macOS (via brew) and containerd + a pinned nerdctl
# on Linux (via the distro package manager + an upstream binary). Pass
# --no-runtime-install (or LEERIE_NO_RUNTIME_INSTALL=1) to skip auto-install
# and fall back to a printed hint + exit 1 — preserves the pre-auto-install
# behavior for CI, dotfiles managers, and users who track their own
# package installs. Unknown distros always fall back to the hint regardless.

log "preflight: checking container runtime"
runtime_ok=true
case "$(uname -s)" in
  Darwin)
    if ! have_runnable colima; then
      if [ "$NO_RUNTIME_INSTALL" = "true" ]; then
        err "colima is missing. Install with: brew install colima"
        # Same auto-sizing rationale as the install path: suggest the
        # half-of-host sizing so the user doesn't OOM under leerie's
        # parallel-worker workload (Colima's 2-cpu / 2-GB default is
        # not enough). Also add the swap-provision YAML block to
        # ~/.colima/default/colima.yaml — see docs/INSTALL.md "Memory
        # pressure: swap configuration".
        err "Then start the VM:           colima start --runtime containerd --mount-type virtiofs $(_runtime_colima_size_flags)"
        err "Also add 4 GB of swap (paste the YAML block from docs/INSTALL.md"
        err "  'Memory pressure: swap configuration' into ~/.colima/default/colima.yaml,"
        err "  then colima stop && colima start)."
        err "(Do NOT 'brew install nerdctl' on macOS — the formula requires Linux."
        err " Colima provides nerdctl inside its VM and installs a host-side shim;"
        err " leerie auto-runs 'colima nerdctl install' on first launch if needed.)"
        runtime_ok=false
      else
        runtime_install_macos || runtime_ok=false
      fi
    elif [ "$DRY_RUN" = "false" ] && ! colima status >/dev/null 2>&1; then
      # Already installed but VM not running — install swap config if
      # no colima.yaml exists yet, then start with auto-sized resources
      # (see _runtime_colima_size_flags / _runtime_install_colima_swap_yaml
      # for the rationale).
      _runtime_install_colima_swap_yaml
      size_flags="$(_runtime_colima_size_flags)"
      log "starting Colima VM (first start may take 30-60s, sizing: ${size_flags:-default})"
      # shellcheck disable=SC2086  # intentional word-split of flag string
      run colima start --runtime containerd --mount-type virtiofs $size_flags
    elif [ "$DRY_RUN" = "false" ]; then
      # Already installed AND running — leave it alone, but hint if the
      # existing sizing is below the auto-recommendation or the config
      # is missing leerie's swap provisioning.
      _runtime_check_colima_sizing
      _runtime_check_colima_swap
    fi
    ;;
  Linux)
    if ! have_runnable nerdctl; then
      if [ "$NO_RUNTIME_INSTALL" = "true" ]; then
        err "nerdctl is missing (auto-install skipped via --no-runtime-install)."
        err "Set up the rootless containerd stack manually — see docs/INSTALL.md"
        err "\"Rootless mode\" (containerd + rootlesskit/slirp4netns/uidmap, nerdctl,"
        err "CNI plugins, BuildKit, the rootless setuptool), then re-run."
        runtime_ok=false
      else
        runtime_install_linux || runtime_ok=false
      fi
    elif [ "$DRY_RUN" = "false" ] && ! nerdctl info >/dev/null 2>&1; then
      # nerdctl is present but can't reach containerd. Under leerie's rootless
      # model this is a misconfigured/stopped user service, NOT a case for
      # enabling the rootful system daemon (which an unprivileged nerdctl can't
      # reach anyway). Point at the rootless recovery steps.
      err "nerdctl is installed but cannot reach containerd."
      err "For rootless containerd, check the user service:"
      err "  systemctl --user status containerd"
      err "and ensure ~/.local/bin + /usr/local/bin are on your PATH."
      err "See docs/INSTALL.md \"Rootless mode\"."
      runtime_ok=false
    fi
    ;;
  *)
    err "unsupported OS: $(uname -s) (need macOS or Linux)"
    runtime_ok=false
    ;;
esac
if [ "$runtime_ok" = "false" ]; then
  exit 1
fi

# --- 3. clone or update --------------------------------------------------

if [ -d "$PREFIX/.git" ]; then
  log "updating existing Leerie checkout at $PREFIX"
  run git -C "$PREFIX" fetch origin
  run git -C "$PREFIX" checkout "$REF"
  run git -C "$PREFIX" pull --ff-only origin "$REF"
elif [ -e "$PREFIX" ]; then
  err "$PREFIX exists and is not a git checkout — refusing to overwrite."
  err "Pass --prefix DIR to choose a different install directory."
  exit 1
else
  log "cloning $REPO_URL into $PREFIX"
  run git clone --depth 1 --branch "$REF" "$REPO_URL" "$PREFIX"
fi

# --- 4. symlink launcher into bin dir ------------------------------------

log "symlinking $PREFIX/leerie into $BIN_DIR/leerie"
run mkdir -p "$BIN_DIR"
LAUNCHER="$PREFIX/leerie"
LINK="$BIN_DIR/leerie"
# Clobber any pre-existing file/symlink at $LINK so re-runs are idempotent.
# $BIN_DIR/leerie is a path this installer owns by virtue of installing
# Leerie; if a user wants a custom file there, --bin-dir is the escape hatch.
if [ -L "$LINK" ] || [ -f "$LINK" ]; then
  run rm -f "$LINK"
fi
run ln -s "$LAUNCHER" "$LINK"

# --- 5. PATH check + verify ----------------------------------------------

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *)
    log "WARNING: $BIN_DIR is not in your PATH."
    case "${SHELL##*/}" in
      zsh)  rcfile="$HOME/.zshrc" ;;
      bash) rcfile="$HOME/.bashrc" ;;
      fish) rcfile="$HOME/.config/fish/config.fish" ;;
      *)    rcfile="your shell rc file" ;;
    esac
    if [ "${SHELL##*/}" = "fish" ]; then
      log "Add to $rcfile:                      set -gx PATH $BIN_DIR \$PATH"
      log "Or for the current shell session:    set -gx PATH $BIN_DIR \$PATH"
    else
      log "Add to $rcfile:                      export PATH=\"$BIN_DIR:\$PATH\""
      log "Or for the current shell session:    export PATH=\"$BIN_DIR:\$PATH\""
    fi
    log "(rc-file change takes effect after restarting your shell.)"
    ;;
esac

log "verifying install"
if [ "$DRY_RUN" = "false" ]; then
  # Run the launcher we just symlinked, not whatever `leerie` already
  # exists on PATH — proves *this* install works end-to-end.
  if "$LINK" --version; then
    log "done. Run \`leerie \"your task\"\` from any git repository to start."
  else
    err "leerie --version failed. The install completed but the binary is not runnable."
    exit 1
  fi
else
  printf '  $ %s\n' "$LINK --version"
  log "dry-run complete."
fi
