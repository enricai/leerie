"""Tests for the `leerie config` launcher verb (bare / --init / --chat).

Phase 3: the `config` verb is a fast-path dispatch that exits before the
container path — no `nerdctl run` is invoked for any config sub-mode.

Sub-behaviours under test:
  (a) `leerie config --init`  — creates .leerie/config.toml with auto-detected
      BLT values (uncommented) and a commented setup_packages example; prints
      the created path; does NOT start nerdctl run.
  (b) `leerie config`         — prints each effective build/lint/test key with
      its provenance (config / inference / leerie.toml).
  (c) `leerie config --chat`  — execs `claude --system-prompt-file
      <LEERIE_REPO>/prompts/config_chat.md --add-dir <USER_REPO>` (NOT `claude
      -p`); does NOT start nerdctl run.
  (d) prompts/config_chat.md  — exists and mentions the .leerie/config.toml
      keys (build/lint/test/setup_packages) and the ARG BASE_IMAGE / USER
      leerie Dockerfile guidance.

Strategy: the per-mode tests (a)-(d) use a self-contained bash harness that
implements the spec and records observable side-effects (argv log for
claude, nerdctl call log for the no-container assertion) — this keeps those
tests fast and independent of the exact launcher wiring. A separate parity
guard (`test_config_inference_matches_infer_build_lint_test` and friends,
below) extracts and runs the REAL `config)` case arm out of the launcher
(following the extract-from-launcher pattern in
test_launcher_per_repo_image.py) and diffs its inference output against
orchestrator/leerie.py::_infer_build_lint_test() across a fixture matrix, so
the launcher's inline inferrer can never silently diverge from the Python
table again without a red test.

Precedent for this pattern: test_launcher_runtime_knob.py (standalone
harness for a launcher case arm), test_ensure_image.py (function harness),
test_launcher_per_repo_image.py (extract-real-block-from-launcher harness).
"""
from __future__ import annotations

import json
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

# Directory of the running interpreter — put on PATH for the end-to-end
# recapture tests so the real orchestrator's runtime deps (tenacity, …)
# resolve when the seam imports orchestrator/leerie.py.
_PYBIN = str(Path(sys.executable).parent)

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Shared harness
# ---------------------------------------------------------------------------
# The harness mirrors the `config` case arm in the launcher's case dispatch
# at leerie line ~558.  Key invariants the harness encodes:
#
#  1. `config` is listed in the ownership-short-circuit guard (alongside
#     --version) so it never claims a state directory.
#  2. After the case arm runs, `exit 0` fires before any container path.
#  3. Bare mode shells out to python3 inline (no subprocess to leerie.py).
#  4. --init writes .leerie/config.toml via python3 inline snippet.
#  5. --chat execs interactive `claude` (NOT `claude -p`).
#
# The harness exposes two externally observable channels:
#   $NERDCTL_LOG  — each `nerdctl` invocation appends "nerdctl <args>" here
#   $CLAUDE_LOG   — each `claude` invocation appends JSON-encoded argv here
#
# Stubs for nerdctl and claude are injected on PATH via a temp bin dir.

_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail

# Inputs via env (all optional with sensible defaults):
#   USER_REPO      — path to the simulated user repo
#   LEERIE_REPO    — path to the leerie repo (defaults to repo root)
#   NERDCTL_LOG    — file to append nerdctl invocations to (touch before run)
#   CLAUDE_LOG     — file to append claude invocations to (touch before run)
#
# argv:  config [--init|--chat]

remote_log() { echo "[leerie] $*" >&2; }

# Stub nerdctl: log every invocation; never actually run a container.
nerdctl() {
  echo "nerdctl $*" >> "${NERDCTL_LOG:-/dev/null}"
  return 0
}

# Stub claude: log argv as a space-joined string; fake an interactive session.
claude() {
  printf '%s\n' "$*" >> "${CLAUDE_LOG:-/dev/null}"
  return 0
}

# Make stubs visible to exec inside --chat arm.
export -f nerdctl claude

USER_REPO="${USER_REPO:-$(pwd)}"
LEERIE_REPO="${LEERIE_REPO:-$(cd "$(dirname "$0")/.." && pwd)}"

# Inline BLT inferrer: returns declared values from .leerie/config.toml,
# else falls through to pattern-based inference. This mirrors the REAL
# launcher's `config)` arm inferrer (leerie:565-658), which in turn mirrors
# orchestrator/leerie.py::_infer_build_lint_test() by hand (DESIGN §6½).
# Kept in sync manually for the unit-level (per-mode) tests below; the
# parity guard further down in this file drives the REAL launcher block
# directly and compares it against _infer_build_lint_test() in-process, so
# any future divergence between this copy and the launcher/orchestrator is
# caught even if this copy is not updated.
_config_read_key() {
  local key="$1" file="$USER_REPO/.leerie/config.toml"
  [ -f "$file" ] || return 0
  { grep -E "^[[:space:]]*${key}[[:space:]]*=" "$file" 2>/dev/null \
    | head -1 \
    | sed -E "s/^[[:space:]]*${key}[[:space:]]*=[[:space:]]*//;
              s/[[:space:]]*$//;
              s/^\"(.*)\"$/\1/;
              s/^'(.*)'$/\1/"; } || true
}

_is_rails_repo() {
  local repo="$1"
  [ -f "$repo/Gemfile.lock" ] && [ -f "$repo/bin/rails" ]
}

_infer_axis() {
  local axis="$1"
  # Declared value wins.
  local declared
  declared="$(_config_read_key "$axis")"
  if [ -n "${declared+x}" ] && { [ -n "$declared" ] || \
      grep -qE "^[[:space:]]*${axis}[[:space:]]*=" "$USER_REPO/.leerie/config.toml" 2>/dev/null; }; then
    echo "$declared"
    return
  fi
  local build="" lint="" test=""
  if [ -f "$USER_REPO/Makefile" ]; then
    build="make"
  fi
  if [ -f "$USER_REPO/package.json" ]; then
    local _pm="npm"
    if [ -f "$USER_REPO/pnpm-lock.yaml" ]; then
      _pm="pnpm"
    elif [ -f "$USER_REPO/yarn.lock" ]; then
      _pm="yarn"
    elif [ -f "$USER_REPO/bun.lockb" ] || [ -f "$USER_REPO/bun.lock" ]; then
      _pm="bun"
    fi
    [ -n "$build" ] || build="$_pm run build"
    [ -n "$test" ] || test="$_pm run test"
  fi
  if [ -f "$USER_REPO/pyproject.toml" ] || [ -f "$USER_REPO/pytest.ini" ] || \
     [ -f "$USER_REPO/setup.cfg" ]; then
    [ -n "$test" ] || test="pytest"
  fi
  if [ -f "$USER_REPO/Cargo.toml" ]; then
    [ -n "$build" ] || build="cargo build"
    [ -n "$test" ] || test="cargo test"
  fi
  if [ -f "$USER_REPO/go.mod" ]; then
    [ -n "$build" ] || build="go build ./..."
    [ -n "$test" ] || test="go test ./..."
  fi
  if [ -f "$USER_REPO/pom.xml" ]; then
    [ -n "$build" ] || build="mvn package"
    [ -n "$test" ] || test="mvn test"
  fi
  if [ -f "$USER_REPO/build.gradle" ] || [ -f "$USER_REPO/build.gradle.kts" ]; then
    if [ -f "$USER_REPO/gradlew" ]; then
      [ -n "$build" ] || build="./gradlew build"
      [ -n "$test" ] || test="./gradlew test"
    else
      [ -n "$build" ] || build="gradle build"
      [ -n "$test" ] || test="gradle test"
    fi
  fi
  if [ -f "$USER_REPO/.eslintrc" ] || [ -f "$USER_REPO/.eslintrc.json" ] || \
     [ -f "$USER_REPO/.eslintrc.js" ] || [ -f "$USER_REPO/.eslintrc.cjs" ] || \
     [ -f "$USER_REPO/.eslintrc.yaml" ] || [ -f "$USER_REPO/.eslintrc.yml" ]; then
    [ -n "$lint" ] || lint="npx eslint ."
  fi
  if [ -f "$USER_REPO/.ruff.toml" ] || [ -f "$USER_REPO/ruff.toml" ]; then
    [ -n "$lint" ] || lint="ruff check ."
  fi
  if [ -f "$USER_REPO/.rubocop.yml" ] || [ -f "$USER_REPO/.rubocop.yaml" ]; then
    [ -n "$lint" ] || lint="bundle exec rubocop"
  fi
  if [ -f "$USER_REPO/detekt.yml" ] || [ -f "$USER_REPO/detekt.yaml" ]; then
    [ -n "$lint" ] || lint="detekt"
  fi
  if [ -n "$(find "$USER_REPO" -maxdepth 1 -name '*.sln' -print -quit 2>/dev/null)" ]; then
    [ -n "$build" ] || build="dotnet build"
    [ -n "$test" ] || test="dotnet test"
  elif [ -n "$(find "$USER_REPO" -maxdepth 1 -name '*.csproj' -print -quit 2>/dev/null)" ]; then
    [ -n "$build" ] || build="dotnet build"
    [ -n "$test" ] || test="dotnet test"
  fi
  if [ -f "$USER_REPO/phpunit.xml" ] || [ -f "$USER_REPO/phpunit.xml.dist" ]; then
    [ -n "$test" ] || test="vendor/bin/phpunit"
  fi
  if [ -f "$USER_REPO/phpstan.neon" ] || [ -f "$USER_REPO/phpstan.neon.dist" ]; then
    [ -n "$lint" ] || lint="vendor/bin/phpstan analyse"
  fi
  if _is_rails_repo "$USER_REPO"; then
    [ -n "$test" ] || test="bin/rails test"
  fi
  case "$axis" in
    build) echo "$build" ;;
    lint) echo "$lint" ;;
    test) echo "$test" ;;
  esac
}

_axis_source() {
  local axis="$1"
  local config_file="$USER_REPO/.leerie/config.toml"
  if [ -f "$config_file" ] && \
     grep -qE "^[[:space:]]*${axis}[[:space:]]*=" "$config_file" 2>/dev/null; then
    echo "config"
  else
    echo "inference"
  fi
}

case "${1:-}" in
  --init)
    shift
    # Create .leerie/ directory and write config.toml with auto-detected values.
    mkdir -p "$USER_REPO/.leerie"
    config_path="$USER_REPO/.leerie/config.toml"
    if [ -f "$config_path" ]; then
      remote_log "error: $config_path already exists; delete it first to re-init"
      exit 1
    fi
    _build_val="$(_infer_axis build)"
    _lint_val="$(_infer_axis lint)"
    _test_val="$(_infer_axis test)"
    cat > "$config_path" <<TOML
# leerie per-repo configuration — commit this file to version-control.
# Generated by: leerie config --init
# See: https://leerie.enric.ai/docs/config

# Shell command leerie runs to build the project.
build = "$_build_val"

# Shell command leerie runs as the lint check.
lint = "$_lint_val"

# Shell command leerie runs to execute the test suite.
test = "$_test_val"

# Space- or comma-separated apt package names to install at the system level.
# Uncomment and fill in if your project needs system libraries.
# setup_packages = "libvips-dev fonts-noto"
TOML
    echo "Created $config_path"
    echo "  Suggested next step: git add .leerie/ && git commit -m 'chore: add leerie config'"
    exit 0
    ;;

  --chat)
    shift
    system_prompt="$LEERIE_REPO/prompts/config_chat.md"
    if [ ! -f "$system_prompt" ]; then
      remote_log "error: $system_prompt not found (leerie installation may be incomplete)"
      exit 1
    fi
    # Interactive claude session — NOT claude -p.
    # claude records its argv to CLAUDE_LOG for test inspection.
    claude \
      --system-prompt-file "$system_prompt" \
      --add-dir "$USER_REPO" \
      "Help me configure leerie for this repo."
    exit 0
    ;;

  "")
    # Bare: print effective config with provenance.
    echo "Effective leerie config for: $USER_REPO"
    echo ""
    for axis in build lint test; do
      val="$(_infer_axis "$axis")"
      src="$(_axis_source "$axis")"
      printf '  %-8s = %-40s  [%s]\n' "$axis" "${val:-(not set)}" "$src"
    done
    # Also show leerie.toml keys if present.
    leerie_toml="$USER_REPO/leerie.toml"
    if [ -f "$leerie_toml" ]; then
      echo ""
      echo "leerie.toml (operational knobs):"
      grep -v '^[[:space:]]*#' "$leerie_toml" 2>/dev/null \
        | grep '=' \
        | while IFS= read -r line; do
          printf '  %s\n' "$line"
        done
    fi
    exit 0
    ;;

  *)
    echo "leerie config: unknown sub-command '$1'" >&2
    echo "Usage: leerie config [--init | --chat]" >&2
    exit 1
    ;;
esac
"""


def _make_stub_bin(tmp_path: Path) -> tuple[Path, Path, Path]:
    """Create a bin dir with stub nerdctl + git binaries; return (bin_dir, nerdctl_log, claude_log)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    nerdctl_log = tmp_path / "nerdctl.log"
    nerdctl_log.touch()
    claude_log = tmp_path / "claude.log"
    claude_log.touch()

    # nerdctl stub: log every invocation
    nerdctl_stub = bin_dir / "nerdctl"
    nerdctl_stub.write_text(
        "#!/bin/sh\n"
        f'echo "nerdctl $*" >> "{nerdctl_log}"\n'
        "exit 0\n"
    )
    nerdctl_stub.chmod(0o755)

    # claude stub: log argv joined by space
    claude_stub = bin_dir / "claude"
    claude_stub.write_text(
        "#!/bin/sh\n"
        f'printf \'%s\\n\' "$*" >> "{claude_log}"\n'
        "exit 0\n"
    )
    claude_stub.chmod(0o755)

    # git stub: minimal — only `git -C <path> remote get-url origin` needed
    git_stub = bin_dir / "git"
    git_stub.write_text(
        "#!/bin/sh\n"
        'if [ "${1:-}" = "-C" ]; then shift 2; fi\n'
        'if [ "${1:-}" = "remote" ] && [ "${2:-}" = "get-url" ]; then\n'
        '  echo "${FAKE_GIT_REMOTE:-}"\n'
        '  exit 0\n'
        "fi\n"
        'command git "$@"\n'
    )
    git_stub.chmod(0o755)

    return bin_dir, nerdctl_log, claude_log


def _run_config(
    user_repo: Path,
    args: list[str],
    tmp_path: Path,
    *,
    extra_env: dict | None = None,
    expect_fail: bool = False,
) -> tuple[str, str, Path, Path]:
    """Run the config verb harness; return (stdout, stderr, nerdctl_log, claude_log)."""
    bin_dir, nerdctl_log, claude_log = _make_stub_bin(tmp_path)
    env = {
        "PATH": str(bin_dir) + ":/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path / "home"),
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "NERDCTL_LOG": str(nerdctl_log),
        "CLAUDE_LOG": str(claude_log),
        "FAKE_GIT_REMOTE": "",
    }
    if extra_env:
        env.update(extra_env)

    result = subprocess.run(
        ["bash", "-c", _HARNESS, "--"] + args,
        env=env,
        capture_output=True,
        text=True,
    )
    if not expect_fail:
        assert result.returncode == 0, (
            f"config harness exited {result.returncode}\n"
            f"stdout: {result.stdout}\n"
            f"stderr: {result.stderr}"
        )
    return result.stdout, result.stderr, nerdctl_log, claude_log


# ---------------------------------------------------------------------------
# (a) leerie config --init
# ---------------------------------------------------------------------------


def test_init_creates_config_toml(tmp_path):
    """--init creates .leerie/config.toml in the target repo."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _run_config(user_repo, ["--init"], tmp_path)
    config = user_repo / ".leerie" / "config.toml"
    assert config.exists(), "config.toml was not created"


def test_init_config_toml_has_blt_keys(tmp_path):
    """--init writes uncommented build/lint/test keys to config.toml."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert "build =" in config_text
    assert "lint =" in config_text
    assert "test =" in config_text


def test_init_config_toml_has_commented_setup_packages(tmp_path):
    """--init includes a commented setup_packages example."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    # Must be a comment (# setup_packages), not an active assignment
    assert "# setup_packages" in config_text
    # Sanity: not accidentally writing an active key
    uncommented = [
        line for line in config_text.splitlines()
        if "setup_packages" in line and not line.lstrip().startswith("#")
    ]
    assert not uncommented, f"setup_packages must be commented; found active line(s): {uncommented}"


def test_init_prints_path(tmp_path):
    """--init prints the path of the created config file."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    stdout, _, _, _ = _run_config(user_repo, ["--init"], tmp_path)
    assert ".leerie/config.toml" in stdout or "config.toml" in stdout


def test_init_suggests_git_add(tmp_path):
    """--init suggests `git add .leerie/` in its output."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    stdout, _, _, _ = _run_config(user_repo, ["--init"], tmp_path)
    assert "git add .leerie/" in stdout


def test_init_no_nerdctl_run(tmp_path):
    """--init must NOT invoke nerdctl run (no container)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, nerdctl_log, _ = _run_config(user_repo, ["--init"], tmp_path)
    nerdctl_calls = nerdctl_log.read_text()
    run_calls = [
        line for line in nerdctl_calls.splitlines() if line.startswith("nerdctl run")
    ]
    assert not run_calls, f"nerdctl run was invoked: {run_calls}"


def test_init_uses_inferred_blt_from_pyproject(tmp_path):
    """--init infers `pytest` for the test axis when pyproject.toml is present
    (matches _infer_build_lint_test(): a bare tests/ dir alone is NOT a
    signal — pyproject.toml / pytest.ini / setup.cfg are)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'test = "pytest"' in config_text


def test_init_uses_inferred_blt_from_makefile(tmp_path):
    """--init uses `make` as the build command when Makefile is present."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Makefile").write_text("build:\n\techo build\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "make"' in config_text


def test_init_uses_inferred_blt_from_rails(tmp_path):
    """--init detects Rails (Gemfile.lock + bin/rails) and Rubocop lint."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Gemfile.lock").write_text("GEM\n")
    (user_repo / "bin").mkdir()
    (user_repo / "bin" / "rails").write_text("#!/usr/bin/env ruby\n")
    (user_repo / ".rubocop.yml").write_text("AllCops:\n  NewCops: enable\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'lint = "bundle exec rubocop"' in config_text
    assert 'test = "bin/rails test"' in config_text


def test_init_uses_inferred_blt_from_cargo(tmp_path):
    """--init detects Cargo.toml and infers cargo build/test."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Cargo.toml").write_text("[package]\nname = \"x\"\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "cargo build"' in config_text
    assert 'test = "cargo test"' in config_text


def test_init_uses_inferred_blt_from_go_mod(tmp_path):
    """--init detects go.mod and infers go build/test."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "go.mod").write_text("module example.com/x\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "go build ./..."' in config_text
    assert 'test = "go test ./..."' in config_text


def test_init_uses_inferred_blt_from_gradle(tmp_path):
    """--init detects build.gradle (no gradlew) and infers gradle build/test."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "build.gradle").write_text("apply plugin: 'java'\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "gradle build"' in config_text
    assert 'test = "gradle test"' in config_text


def test_init_uses_inferred_blt_from_gradlew(tmp_path):
    """--init prefers ./gradlew over bare gradle when gradlew is present."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "build.gradle.kts").write_text("plugins { java }\n")
    (user_repo / "gradlew").write_text("#!/bin/sh\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "./gradlew build"' in config_text
    assert 'test = "./gradlew test"' in config_text


def test_init_uses_inferred_blt_from_dotnet(tmp_path):
    """--init detects a *.csproj file and infers dotnet build/test."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "MyApp.csproj").write_text("<Project Sdk=\"Microsoft.NET.Sdk\" />\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "dotnet build"' in config_text
    assert 'test = "dotnet test"' in config_text


def test_init_uses_inferred_blt_from_php(tmp_path):
    """--init detects phpunit.xml and phpstan.neon for test/lint."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "phpunit.xml").write_text("<phpunit></phpunit>\n")
    (user_repo / "phpstan.neon").write_text("parameters:\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'test = "vendor/bin/phpunit"' in config_text
    assert 'lint = "vendor/bin/phpstan analyse"' in config_text


def test_init_uses_inferred_blt_from_eslint(tmp_path):
    """--init detects .eslintrc.json and infers npx eslint for lint."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / ".eslintrc.json").write_text("{}\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'lint = "npx eslint ."' in config_text


def test_init_uses_inferred_blt_from_ruff(tmp_path):
    """--init detects ruff.toml and infers ruff check for lint."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "ruff.toml").write_text("line-length = 100\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'lint = "ruff check ."' in config_text


def test_init_fails_if_config_already_exists(tmp_path):
    """--init exits non-zero if .leerie/config.toml already exists."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir()
    existing = leerie_dir / "config.toml"
    existing.write_text("# existing\n")
    stdout, stderr, _, _ = _run_config(
        user_repo, ["--init"], tmp_path, expect_fail=True
    )
    assert "already exists" in stderr or "already exists" in stdout


# ---------------------------------------------------------------------------
# (b) leerie config (bare) — print effective config with provenance
# ---------------------------------------------------------------------------


def test_bare_prints_build_lint_test(tmp_path):
    """Bare config prints build, lint, and test lines."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    stdout, _, _, _ = _run_config(user_repo, [], tmp_path)
    assert "build" in stdout
    assert "lint" in stdout
    assert "test" in stdout


def test_bare_shows_provenance_inference(tmp_path):
    """Bare config shows [inference] provenance when no .leerie/config.toml exists."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    stdout, _, _, _ = _run_config(user_repo, [], tmp_path)
    assert "inference" in stdout


def test_bare_shows_provenance_config(tmp_path):
    """Bare config shows [config] provenance for axes declared in .leerie/config.toml."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('build = "make"\n')
    stdout, _, _, _ = _run_config(user_repo, [], tmp_path)
    assert "config" in stdout


def test_bare_uses_declared_blt_value(tmp_path):
    """Bare config uses the declared value, not the inferred one."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir()
    (leerie_dir / "config.toml").write_text('build = "cargo build --release"\n')
    stdout, _, _, _ = _run_config(user_repo, [], tmp_path)
    assert "cargo build --release" in stdout


def test_bare_shows_leerie_toml_keys_when_present(tmp_path):
    """Bare config shows leerie.toml keys when that file exists."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "leerie.toml").write_text("runtime = fly\n")
    stdout, _, _, _ = _run_config(user_repo, [], tmp_path)
    assert "leerie.toml" in stdout
    assert "runtime" in stdout


def test_bare_no_nerdctl_run(tmp_path):
    """Bare config must NOT invoke nerdctl run (no container)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, nerdctl_log, _ = _run_config(user_repo, [], tmp_path)
    nerdctl_calls = nerdctl_log.read_text()
    run_calls = [
        line for line in nerdctl_calls.splitlines() if line.startswith("nerdctl run")
    ]
    assert not run_calls, f"nerdctl run was invoked: {run_calls}"


# ---------------------------------------------------------------------------
# (c) leerie config --chat
# ---------------------------------------------------------------------------


def test_chat_invokes_claude(tmp_path):
    """--chat invokes `claude` (the stub records its argv)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, _, claude_log = _run_config(user_repo, ["--chat"], tmp_path)
    assert claude_log.read_text().strip(), "claude was not invoked"


def test_chat_uses_system_prompt_file(tmp_path):
    """--chat passes --system-prompt-file pointing at prompts/config_chat.md."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, _, claude_log = _run_config(user_repo, ["--chat"], tmp_path)
    argv_line = claude_log.read_text().strip()
    assert "--system-prompt-file" in argv_line
    assert "config_chat.md" in argv_line


def test_chat_system_prompt_file_points_to_leerie_repo(tmp_path):
    """--chat's --system-prompt-file arg contains the LEERIE_REPO path."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, _, claude_log = _run_config(user_repo, ["--chat"], tmp_path)
    argv_line = claude_log.read_text().strip()
    assert str(REPO_ROOT) in argv_line or "prompts/config_chat.md" in argv_line


def test_chat_passes_add_dir(tmp_path):
    """--chat passes --add-dir <USER_REPO> so claude can read the repo."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, _, claude_log = _run_config(user_repo, ["--chat"], tmp_path)
    argv_line = claude_log.read_text().strip()
    assert "--add-dir" in argv_line
    assert str(user_repo) in argv_line


def test_chat_does_not_pass_p_flag(tmp_path):
    """`leerie config --chat` must NOT invoke `claude -p` (interactive, not headless)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, _, claude_log = _run_config(user_repo, ["--chat"], tmp_path)
    argv_line = claude_log.read_text().strip()
    # "-p" must not appear as a standalone flag
    tokens = argv_line.split()
    assert "-p" not in tokens, f"claude was called with -p: {argv_line}"


def test_chat_no_nerdctl_run(tmp_path):
    """--chat must NOT invoke nerdctl run (no container)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _, _, nerdctl_log, _ = _run_config(user_repo, ["--chat"], tmp_path)
    nerdctl_calls = nerdctl_log.read_text()
    run_calls = [
        line for line in nerdctl_calls.splitlines() if line.startswith("nerdctl run")
    ]
    assert not run_calls, f"nerdctl run was invoked: {run_calls}"


# ---------------------------------------------------------------------------
# (d) prompts/config_chat.md — content assertions
# ---------------------------------------------------------------------------


def _config_chat_md() -> str:
    path = REPO_ROOT / "prompts" / "config_chat.md"
    assert path.exists(), f"prompts/config_chat.md not found at {path}"
    return path.read_text()


def test_config_chat_md_exists():
    """prompts/config_chat.md must exist."""
    path = REPO_ROOT / "prompts" / "config_chat.md"
    assert path.exists(), "prompts/config_chat.md does not exist"


def test_config_chat_md_mentions_build_key():
    """prompts/config_chat.md mentions the `build` config key."""
    text = _config_chat_md()
    assert "build" in text


def test_config_chat_md_mentions_lint_key():
    """prompts/config_chat.md mentions the `lint` config key."""
    text = _config_chat_md()
    assert "lint" in text


def test_config_chat_md_mentions_test_key():
    """prompts/config_chat.md mentions the `test` config key."""
    text = _config_chat_md()
    assert "test" in text


def test_config_chat_md_mentions_setup_packages():
    """prompts/config_chat.md mentions the `setup_packages` key."""
    text = _config_chat_md()
    assert "setup_packages" in text


def test_config_chat_md_mentions_arg_base_image():
    """prompts/config_chat.md mentions `ARG BASE_IMAGE` for .leerie/Dockerfile guidance."""
    text = _config_chat_md()
    assert "ARG BASE_IMAGE" in text


def test_config_chat_md_instructs_end_at_user_root():
    """prompts/config_chat.md must instruct ending at `USER root` (PID-1 must
    stay root — DESIGN §6) and NOT tell the model to append a trailing
    `USER leerie` (which would break the container-entry root invariant)."""
    text = _config_chat_md()
    assert "USER root" in text
    # The example Dockerfile block must not end with a `USER leerie` line.
    assert "\nUSER leerie\n" not in text, (
        "config_chat.md must not instruct a trailing USER leerie"
    )


def test_usage_md_dockerfile_example_has_no_trailing_user_leerie():
    """docs/USAGE.md's hand-authored `.leerie/Dockerfile` example must not end
    with `USER leerie` (same root-PID-1 invariant as config_chat.md — DESIGN
    §6). A user copying the documented example verbatim must not reintroduce
    the container-exits-1 defect."""
    path = REPO_ROOT / "docs" / "USAGE.md"
    assert path.exists(), "docs/USAGE.md not found"
    text = path.read_text()
    assert "\nUSER leerie\n" not in text, (
        "docs/USAGE.md Dockerfile example must not end with a trailing USER leerie"
    )


def test_config_chat_md_mentions_config_toml():
    """prompts/config_chat.md references .leerie/config.toml."""
    text = _config_chat_md()
    assert "config.toml" in text


def test_config_chat_md_mentions_dockerfile():
    """prompts/config_chat.md references .leerie/Dockerfile."""
    text = _config_chat_md()
    assert "Dockerfile" in text


# ---------------------------------------------------------------------------
# Coupling test: config arm exists in the real launcher
# ---------------------------------------------------------------------------


def test_config_arm_exists_in_launcher():
    """The real launcher must contain a `config)` case arm."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    # The case arm in the main dispatch must match `config)` as a case pattern.
    assert "config)" in launcher_text, (
        "The `config` verb case arm was not found in the launcher."
    )


def test_config_arm_exits_before_nerdctl_run():
    """The config case arm must `exit 0` before the nerdctl run line."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    # The exit 0 in the config arm must appear before `nerdctl run`
    config_pos = launcher_text.index("config)")
    nerdctl_run_pos = launcher_text.index("nerdctl run", config_pos)
    # There must be an `exit 0` between the config arm and the nerdctl run line
    exit_pos = launcher_text.find("exit 0", config_pos)
    assert exit_pos < nerdctl_run_pos, (
        "config) arm does not exit before nerdctl run — container path reached"
    )


# ---------------------------------------------------------------------------
# Parity guard: the REAL launcher `config)` arm's inference must match
# orchestrator/leerie.py::_infer_build_lint_test() exactly.
#
# Unlike the per-mode tests above (which run against the harness's own copy
# of the inferrer for speed/isolation), these tests extract the actual
# `config)` case-arm body out of the shipped `leerie` launcher — the same
# extract-from-launcher pattern test_launcher_per_repo_image.py uses for the
# per-repo-image block — wrap it in a minimal dispatcher, run
# `config --init` against a fixture repo, and diff the written build/lint/
# test values against _infer_build_lint_test()'s output for that same
# fixture (obtained in-process via the `leerie` fixture from conftest.py).
#
# If the launcher's `_infer_axis` is ever reverted to the old thin table
# (Makefile/package.json/pyproject+pytest only), these tests fail on every
# fixture outside that thin table's coverage (Rails, Cargo, go.mod, gradle,
# dotnet, php, eslint, ruff) — the drift can no longer land silently.
# ---------------------------------------------------------------------------


def _extract_config_arm() -> str:
    """Return the real `config)` case-arm body (including the `config)`
    pattern and trailing `;;`) verbatim from the shipped launcher."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    start_marker = "  config)\n"
    end_marker = "\n  --list)"
    s = launcher_text.index(start_marker)
    e = launcher_text.index(end_marker, s)
    return launcher_text[s:e]


def _run_real_config_arm(
    user_repo: Path, args: list[str], tmp_path: Path
) -> subprocess.CompletedProcess:
    """Run the REAL launcher's `config)` arm (extracted verbatim) against
    `user_repo`, wrapped in a minimal dispatcher that supplies the
    `remote_log` and `claude` helpers the arm expects from its enclosing
    launcher scope."""
    block = _extract_config_arm()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'remote_log() { echo "[leerie] $*" >&2; }\n'
        "claude() {\n"
        "  printf '%s\\n' \"$*\" >> \"${CLAUDE_LOG:-/dev/null}\"\n"
        "  return 0\n"
        "}\n"
        "export -f claude\n"
        "\n"
        'case "${1:-}" in\n'
        f"{block}\n"
        "esac\n"
    )
    claude_log = tmp_path / "claude.log"
    claude_log.touch()
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path / "home"),
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "CLAUDE_LOG": str(claude_log),
    }
    return subprocess.run(
        ["bash", "-c", script, "--", "config"] + args,
        env=env,
        capture_output=True,
        text=True,
    )


def _infer_via_real_launcher(user_repo: Path, tmp_path: Path) -> dict[str, str]:
    """Drive the real launcher's `config --init` arm against `user_repo`
    and return the build/lint/test values it wrote to config.toml."""
    result = _run_real_config_arm(user_repo, ["--init"], tmp_path)
    assert result.returncode == 0, (
        f"real config arm exited {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    out: dict[str, str] = {}
    for key in ("build", "lint", "test"):
        for line in config_text.splitlines():
            stripped = line.strip()
            if stripped.startswith(f"{key} ="):
                val = stripped[len(f"{key} ="):].strip()
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                out[key] = val
                break
    return out


# One fixture-builder per stack the Python inferrer covers (mirrors the
# `if` chain in _infer_build_lint_test / _is_rails_repo, orchestrator/
# leerie.py:12525-12600). Each entry writes exactly the marker files for
# one stack so the parity assertion is unambiguous about which axis each
# stack is expected to drive.
_PARITY_FIXTURES: dict[str, Callable[[Path], None]] = {
    "makefile": lambda repo: (repo / "Makefile").write_text("build:\n\techo hi\n"),
    "package_json": lambda repo: (repo / "package.json").write_text("{}\n"),
    "pyproject_pytest": lambda repo: (repo / "pyproject.toml").write_text(
        "[tool.pytest.ini_options]\n"
    ),
    "cargo": lambda repo: (repo / "Cargo.toml").write_text('[package]\nname = "x"\n'),
    "go_mod": lambda repo: (repo / "go.mod").write_text("module example.com/x\n"),
    "maven": lambda repo: (repo / "pom.xml").write_text("<project></project>\n"),
    "gradle_no_wrapper": lambda repo: (repo / "build.gradle").write_text(
        "apply plugin: 'java'\n"
    ),
    "gradle_kts_with_wrapper": lambda repo: (
        (repo / "build.gradle.kts").write_text("plugins { java }\n"),
        (repo / "gradlew").write_text("#!/bin/sh\n"),
    ),
    "eslint": lambda repo: (repo / ".eslintrc.json").write_text("{}\n"),
    "ruff": lambda repo: (repo / "ruff.toml").write_text("line-length = 100\n"),
    "rubocop": lambda repo: (repo / ".rubocop.yml").write_text("AllCops:\n"),
    "detekt": lambda repo: (repo / "detekt.yml").write_text("complexity:\n"),
    "dotnet_csproj": lambda repo: (repo / "App.csproj").write_text(
        '<Project Sdk="Microsoft.NET.Sdk" />\n'
    ),
    "dotnet_sln": lambda repo: (repo / "App.sln").write_text(
        "Microsoft Visual Studio Solution File\n"
    ),
    "phpunit": lambda repo: (repo / "phpunit.xml").write_text("<phpunit></phpunit>\n"),
    "phpstan": lambda repo: (repo / "phpstan.neon").write_text("parameters:\n"),
    "rails": lambda repo: (
        (repo / "Gemfile.lock").write_text("GEM\n"),
        (repo / "bin").mkdir(),
        (repo / "bin" / "rails").write_text("#!/usr/bin/env ruby\n"),
    ),
}


@pytest.mark.parametrize("fixture_name", sorted(_PARITY_FIXTURES))
def test_config_inference_matches_infer_build_lint_test(fixture_name, tmp_path, leerie):
    """For every stack _infer_build_lint_test() covers, the REAL launcher's
    `config --init` arm must produce the identical build/lint/test values."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    _PARITY_FIXTURES[fixture_name](user_repo)

    expected = leerie._infer_build_lint_test(user_repo)
    actual = _infer_via_real_launcher(user_repo, tmp_path)

    assert actual.get("build", "") == expected["build"], (
        f"[{fixture_name}] build mismatch: launcher={actual.get('build', '')!r} "
        f"vs _infer_build_lint_test={expected['build']!r}"
    )
    assert actual.get("lint", "") == expected["lint"], (
        f"[{fixture_name}] lint mismatch: launcher={actual.get('lint', '')!r} "
        f"vs _infer_build_lint_test={expected['lint']!r}"
    )
    assert actual.get("test", "") == expected["test"], (
        f"[{fixture_name}] test mismatch: launcher={actual.get('test', '')!r} "
        f"vs _infer_build_lint_test={expected['test']!r}"
    )


def test_config_inference_matches_on_polyglot_repo(tmp_path, leerie):
    """A repo matching several stacks at once (first-set-wins per axis) must
    still resolve identically between the launcher and the Python table."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Makefile").write_text("build:\n\techo hi\n")
    (user_repo / "package.json").write_text("{}\n")
    (user_repo / "pyproject.toml").write_text("[tool.pytest.ini_options]\n")
    (user_repo / "ruff.toml").write_text("line-length = 100\n")

    expected = leerie._infer_build_lint_test(user_repo)
    actual = _infer_via_real_launcher(user_repo, tmp_path)

    assert actual.get("build", "") == expected["build"]
    assert actual.get("lint", "") == expected["lint"]
    assert actual.get("test", "") == expected["test"]


def test_config_inference_matches_on_empty_repo(tmp_path, leerie):
    """A repo with no recognizable markers infers empty strings on every
    axis in both the launcher and the Python table."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()

    expected = leerie._infer_build_lint_test(user_repo)
    actual = _infer_via_real_launcher(user_repo, tmp_path)

    assert actual.get("build", "") == expected["build"] == ""
    assert actual.get("lint", "") == expected["lint"] == ""
    assert actual.get("test", "") == expected["test"] == ""


def test_config_inference_parity_detects_reverted_launcher_inferrer(tmp_path, leerie):
    """Sanity-check the parity guard itself: if the launcher's `_infer_axis`
    is reverted to the pre-config-001 thin table (Makefile/package.json/
    pyproject+pytest only), this test must fail on a stack the thin table
    never covered — proving the guard is load-bearing, not a tautology."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Cargo.toml").write_text('[package]\nname = "x"\n')

    expected = leerie._infer_build_lint_test(user_repo)
    assert expected["build"] == "cargo build"
    assert expected["test"] == "cargo test"

    # Simulate the pre-config-001 thin table's blindness to Cargo: it would
    # have inferred nothing for a Cargo-only repo.
    reverted_actual = {"build": "", "lint": "", "test": ""}
    assert reverted_actual["build"] != expected["build"], (
        "the pre-config-001 thin table would have (wrongly) agreed with "
        "_infer_build_lint_test() on Cargo — the parity guard would not "
        "have caught the regression it exists to catch"
    )


# ---------------------------------------------------------------------------
# config --recapture tests
# ---------------------------------------------------------------------------
# These tests verify the dispatch logic of the --recapture arm: argument
# parsing, state-dir probe, and that it stays within the parity-guard
# extraction boundaries.  They stub out the python3 seam so they don't
# require feat-002's orchestrator functions.
# ---------------------------------------------------------------------------

def _run_real_config_arm_with_state(
    user_repo: Path,
    args: list[str],
    tmp_path: Path,
    state_dir: Path | None = None,
    extra_env: dict | None = None,
) -> subprocess.CompletedProcess:
    """Variant of _run_real_config_arm that injects LEERIE_STATE_HOST_DIR
    and optionally stubs python3 to a no-op for recapture tests."""
    block = _extract_config_arm()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        'remote_log() { echo "[leerie] $*" >&2; }\n'
        "claude() {\n"
        "  printf '%s\\n' \"$*\" >> \"${CLAUDE_LOG:-/dev/null}\"\n"
        "  return 0\n"
        "}\n"
        "export -f claude\n"
        "\n"
        'case "${1:-}" in\n'
        f"{block}\n"
        "esac\n"
    )
    env = {
        # _PYBIN first so the --recapture python3 seam resolves to the
        # interpreter that has runtime deps (tenacity), matching the sibling
        # seam tests; system python3 may lack them.
        "PATH": f"{_PYBIN}:/usr/bin:/bin:/usr/local/bin",
        "HOME": str(tmp_path / "home"),
        "USER_REPO": str(user_repo),
        "LEERIE_REPO": str(REPO_ROOT),
        "CLAUDE_LOG": str(tmp_path / "claude.log"),
    }
    if state_dir is not None:
        env["LEERIE_STATE_HOST_DIR"] = str(state_dir)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", "-c", script, "--", "config"] + args,
        env=env,
        capture_output=True,
        text=True,
    )


def test_recapture_no_runs_dir_exits_1(tmp_path):
    """--recapture exits 1 with a diagnostic when no runs directory exists."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    # No runs/ subdir — should fail gracefully.
    result = _run_real_config_arm_with_state(user_repo, ["--recapture"], tmp_path,
                                             state_dir=state_dir)
    assert result.returncode == 1
    # run_recapture_deps uses log() → stdout; the bash wrapper echoes
    # "python3 seam failed" to stderr. Check both.
    combined = result.stdout + result.stderr
    assert "no runs directory" in combined.lower()


def test_recapture_no_finished_run_exits_1(tmp_path):
    """--recapture exits 1 when there are runs but none are finished."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    runs_dir = state_dir / "runs" / "run-abc"
    runs_dir.mkdir(parents=True)
    # run.json has no finished_at.
    (runs_dir / "run.json").write_text('{"started_at": "2026-01-01T00:00:00"}')
    logs_dir = runs_dir / "logs"
    logs_dir.mkdir()

    result = _run_real_config_arm_with_state(user_repo, ["--recapture"], tmp_path,
                                             state_dir=state_dir)
    assert result.returncode == 1
    # run_recapture_deps uses log() → stdout; check both stdout and stderr.
    combined = result.stdout + result.stderr
    assert "no completed run" in combined.lower()


def test_recapture_dispatches_to_python3(tmp_path):
    """--recapture finds a finished run and dispatches to python3 seam."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    runs_dir = state_dir / "runs" / "run-abc"
    runs_dir.mkdir(parents=True)
    # A finished run with logs dir.
    (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')
    logs_dir = runs_dir / "logs"
    logs_dir.mkdir()

    # Stub python3 to record the call and exit 0.
    python3_stub = tmp_path / "python3"
    python3_stub.write_text(
        "#!/bin/sh\n"
        "echo 'python3-stub: config --recapture OK'\n"
        "exit 0\n"
    )
    python3_stub.chmod(0o755)

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path,
        state_dir=state_dir,
        extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert "python3-stub" in result.stdout or "recapture" in result.stderr.lower()


def test_recapture_force_flag_passed_correctly(tmp_path):
    """--recapture --force passes 'true' as the force argument to the python3 seam."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    runs_dir = state_dir / "runs" / "run-abc"
    runs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')
    (runs_dir / "logs").mkdir()

    # Stub python3 to echo its argv so we can verify force=true is passed.
    python3_stub = tmp_path / "python3"
    python3_stub.write_text(
        "#!/bin/sh\n"
        "echo \"python3 args: $*\"\n"
        "exit 0\n"
    )
    python3_stub.chmod(0o755)

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture", "--force"], tmp_path,
        state_dir=state_dir,
        extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    # The stub echoes its args; the force argument should be 'true'.
    assert "true" in result.stdout


def test_recapture_python3_failure_exits_1(tmp_path):
    """--recapture exits 1 when the python3 seam (orchestrator call) fails."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    runs_dir = state_dir / "runs" / "run-abc"
    logs_dir = runs_dir / "logs"
    logs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')

    # Stub python3 to fail (the new launcher makes a single python3 call
    # that handles both run-discovery and the orchestrator seam).
    python3_stub = tmp_path / "python3"
    python3_stub.write_text(
        "#!/bin/sh\n"
        "exit 1\n"
    )
    python3_stub.chmod(0o755)

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path,
        state_dir=state_dir,
        extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 1
    assert "python3 seam failed" in result.stderr or "failed" in result.stderr.lower()


def test_recapture_arm_within_parity_guard_boundaries():
    """The --recapture arm must be inside the config) ... --list) extraction
    boundary used by the parity guard (and thus by the real config arm tests)."""
    arm = _extract_config_arm()
    assert "--recapture" in arm, (
        "--recapture arm is missing from the config) extraction boundary; "
        "it must be placed between 'config)' and '--list)' in the launcher"
    )


def test_recapture_no_nerdctl(tmp_path):
    """--recapture must NOT invoke nerdctl (host-side only, no container)."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    state_dir = tmp_path / "state"
    runs_dir = state_dir / "runs" / "run-abc"
    runs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')
    (runs_dir / "logs").mkdir()

    nerdctl_log = tmp_path / "nerdctl.log"
    # Stub nerdctl to record calls and succeed; stub python3 to succeed.
    python3_stub = tmp_path / "python3"
    python3_stub.write_text("#!/bin/sh\necho ok\nexit 0\n")
    python3_stub.chmod(0o755)
    nerdctl_stub = tmp_path / "nerdctl"
    nerdctl_stub.write_text(
        f"#!/bin/sh\necho \"nerdctl $*\" >> {nerdctl_log}\nexit 0\n"
    )
    nerdctl_stub.chmod(0o755)

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path,
        state_dir=state_dir,
        extra_env={"PATH": f"{tmp_path}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert not nerdctl_log.exists() or nerdctl_log.read_text() == "", (
        "config --recapture must not invoke nerdctl"
    )


def _make_finished_run_with_apt_log(state_dir: Path, command: str) -> None:
    """Create a finished run whose logs contain a Bash apt-install command,
    in the _iter_log_tool_use JSONL shape the real orchestrator seam parses."""
    runs_dir = state_dir / "runs" / "run-abc"
    logs_dir = runs_dir / "logs"
    logs_dir.mkdir(parents=True)
    (runs_dir / "run.json").write_text('{"finished_at": "2026-01-01T12:00:00"}')
    event = {
        "message": {
            "content": [
                {"type": "tool_use", "name": "Bash", "id": "t-0",
                 "input": {"command": command}}
            ]
        }
    }
    (logs_dir / "worker-001.log").write_text(json.dumps(event) + "\n")


def _make_claude_stub(stub_dir: Path, structured_output: dict) -> None:
    """Write a claude stub that emits a valid dep_capture stream-json result."""
    import json as _json
    payload = _json.dumps({
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "structured_output": structured_output,
    })
    stub = stub_dir / "claude"
    stub.write_text(
        "#!/bin/sh\n"
        f"printf '%s\\n' '{payload}'\n"
        "exit 0\n"
    )
    stub.chmod(0o755)


def test_recapture_reports_corpus_end_to_end(tmp_path):
    """--recapture with a finished run invokes dep_capture and writes deps.
    A stub claude returning empty lists leaves config.toml unchanged.
    Does NOT remove the generated Dockerfile. Uses the real python3 seam."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text("")
    sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
    (leerie_dir / "Dockerfile").write_text(sentinel + "\nARG BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    _make_finished_run_with_apt_log(state_dir, "apt-get install -y postgresql")
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    _make_claude_stub(stub_dir, {"setup_packages": [], "language_installs": []})

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
        extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    # Empty dep_capture result → no new deps → config.toml unchanged.
    assert (leerie_dir / "config.toml").read_text() == ""
    # Generated Dockerfile is left untouched (capture_repo_deps never removes files).
    assert (leerie_dir / "Dockerfile").exists()


def test_recapture_keeps_committed_dockerfile_end_to_end(tmp_path):
    """--recapture must NOT remove a hand-committed Dockerfile (no sentinel) —
    it is authoritative and capture_repo_deps never removes files."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text("")
    committed = "ARG BASE_IMAGE\nFROM $BASE_IMAGE\nRUN echo hand-written\n"
    (leerie_dir / "Dockerfile").write_text(committed)
    state_dir = tmp_path / "state"
    _make_finished_run_with_apt_log(state_dir, "apt-get install -y postgresql")
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    _make_claude_stub(stub_dir, {"setup_packages": [], "language_installs": []})

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
        extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert (leerie_dir / "Dockerfile").read_text() == committed, (
        "committed Dockerfile must survive recapture"
    )


def test_recapture_noop_keeps_generated_dockerfile(tmp_path):
    """--recapture must not remove an existing generated Dockerfile.
    When the LLM returns packages already declared, merge is a no-op and
    the generated Dockerfile is left untouched."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    # Already declares postgresql; stub returns postgresql again → no-op merge.
    (leerie_dir / "config.toml").write_text('setup_packages = "postgresql"\n')
    sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
    (leerie_dir / "Dockerfile").write_text(sentinel + "\nARG BASE_IMAGE\n")
    state_dir = tmp_path / "state"
    _make_finished_run_with_apt_log(state_dir, "apt-get install -y postgresql")
    stub_dir = tmp_path / "stubs"
    stub_dir.mkdir()
    # Stub returns postgresql; _merge_setup_packages sees it already present → no write.
    _make_claude_stub(stub_dir, {"setup_packages": ["postgresql"], "language_installs": []})

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
        extra_env={"PATH": f"{stub_dir}:{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}\nstdout: {result.stdout}"
    assert (leerie_dir / "Dockerfile").exists(), (
        "no-op recapture must not remove the generated Dockerfile"
    )


def test_recapture_keeps_committed_generated_dockerfile_with_sentinel(tmp_path):
    """--recapture must NOT remove a COMMITTED (git-tracked) generated Dockerfile
    even though it carries the sentinel — a committed Dockerfile is authoritative
    (DESIGN §6½). The seam never removes any files."""
    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    (leerie_dir / "config.toml").write_text("")
    sentinel = "# leerie-generated: do not edit (regenerated from .leerie/config.toml)"
    dockerfile = sentinel + "\nARG BASE_IMAGE\nRUN echo MY-CUSTOM\n"
    (leerie_dir / "Dockerfile").write_text(dockerfile)
    # Commit config.toml + Dockerfile so the Dockerfile is git-tracked.
    subprocess.run(["git", "init", "-q"], cwd=user_repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.co"], cwd=user_repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=user_repo, check=True)
    subprocess.run(["git", "add", ".leerie/Dockerfile", ".leerie/config.toml"],
                   cwd=user_repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "add"], cwd=user_repo, check=True)

    state_dir = tmp_path / "state"
    _make_finished_run_with_apt_log(state_dir, "apt-get install -y postgresql")

    result = _run_real_config_arm_with_state(
        user_repo, ["--recapture"], tmp_path, state_dir=state_dir,
        extra_env={"PATH": f"{_PYBIN}:/usr/bin:/bin:/usr/local/bin"},
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert (leerie_dir / "Dockerfile").read_text() == dockerfile, (
        "committed (git-tracked) generated Dockerfile must survive recapture"
    )


# ---------------------------------------------------------------------------
# Cross-readability: Python _write_config_toml_keys <-> launcher _config_read_key
# ---------------------------------------------------------------------------
# Guards the format contract between the Python writer (used by capture and
# --recapture) and the bash reader (used by the config arm and the per-repo
# image block).  A format change in either side that breaks the other will
# fail this test before the divergence ships.
# ---------------------------------------------------------------------------


def _extract_config_read_key_fn() -> str:
    """Return the real `_config_read_key` function verbatim from the launcher."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    # The function is defined inside the config) arm.  Extract from the
    # function header through the closing brace.
    start_marker = "    _config_read_key() {\n"
    end_marker = "\n    }\n"
    s = launcher_text.index(start_marker)
    e = launcher_text.index(end_marker, s) + len(end_marker)
    return launcher_text[s:e]


def test_write_config_toml_keys_round_trips_via_launcher_read(tmp_path):
    """Python _write_config_toml_keys writes a config.toml whose values are
    read back identically by the launcher's _config_read_key bash function.

    This is the format-contract guard: if either writer or reader changes its
    quoting / spacing convention the test fails before divergence ships.
    """
    import sys
    sys.path.insert(0, str(REPO_ROOT / "orchestrator"))
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "leerie_orch", REPO_ROOT / "orchestrator" / "leerie.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]

    user_repo = tmp_path / "repo"
    leerie_dir = user_repo / ".leerie"
    leerie_dir.mkdir(parents=True)
    cfg_path = leerie_dir / "config.toml"

    # Write a value via the Python writer.
    test_value = "postgresql libfoo-dev"
    mod._write_config_toml_keys(cfg_path, {"setup_packages": test_value})

    # Read it back using the real launcher's _config_read_key bash function.
    fn_text = _extract_config_read_key_fn()
    script = (
        "#!/usr/bin/env bash\n"
        "set -euo pipefail\n"
        f'USER_REPO="{user_repo}"\n'
        f"{fn_text}\n"
        'echo "$(_config_read_key setup_packages)"\n'
    )
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin:/usr/local/bin", "HOME": str(tmp_path)},
    )
    assert result.returncode == 0, f"bash reader failed: {result.stderr}"
    read_back = result.stdout.strip()
    assert read_back == test_value, (
        f"round-trip mismatch: Python wrote {test_value!r}, "
        f"launcher _config_read_key returned {read_back!r}"
    )


# ---------------------------------------------------------------------------
# Dockerfile language-layer bake-from-persisted-installs tests
#
# The launcher's Python heredoc (the _lang_layer block, leerie ~4016) now
# reads `language_installs` from config.toml as its PRIMARY source, falling
# back to lockfile detection when none are persisted.  These tests extract
# that Python script verbatim from the launcher and invoke it directly so
# the logic under test is always the live launcher code.
# ---------------------------------------------------------------------------


def _extract_lang_layer_script() -> str:
    """Return the Python heredoc body for the _lang_layer block verbatim.

    The launcher writes this body to a temp file via ``cat >"$_dep_pyf" <<'PY'``
    (rather than piping it inline through ``"$(python3 - ... <<'PY')"``) to avoid
    a bash 3.2 parser bug with a quoted heredoc nested inside ``"$(...)"``; the
    body is then run as ``python3 "$_dep_pyf" "$USER_REPO" "$_leerie_config_toml"``,
    so extracting the body and running it with (repo, config_toml) args stays a
    faithful mirror of the live launcher path.
    """
    launcher_text = (REPO_ROOT / "leerie").read_text()
    start_marker = 'cat >"$_dep_pyf" <<\'PY\'\n'
    end_marker = "\nPY\n"
    s = launcher_text.index(start_marker) + len(start_marker)
    e = launcher_text.index(end_marker, s)
    return launcher_text[s:e]


def _run_lang_layer(
    tmp_path: Path,
    repo_files: dict[str, str] | None = None,
    config_toml_content: str | None = None,
) -> subprocess.CompletedProcess:
    """Run the lang-layer Python script against a synthetic repo.

    repo_files: {relative_path: content} to write into the fake repo.
    config_toml_content: content for .leerie/config.toml (None = no file).
    """
    repo = tmp_path / "repo"
    repo.mkdir(exist_ok=True)
    for rel, content in (repo_files or {}).items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)

    if config_toml_content is not None:
        leerie_dir = repo / ".leerie"
        leerie_dir.mkdir(exist_ok=True)
        (leerie_dir / "config.toml").write_text(config_toml_content)
        config_toml_arg = str(leerie_dir / "config.toml")
    else:
        config_toml_arg = str(repo / ".leerie" / "config.toml")  # non-existent

    script = _extract_lang_layer_script()
    script_file = tmp_path / "lang_layer.py"
    script_file.write_text(script)

    return subprocess.run(
        ["python3", str(script_file), str(repo), config_toml_arg],
        capture_output=True,
        text=True,
    )


def test_lang_layer_persisted_installs_copy_and_run(tmp_path):
    """With language_installs in config.toml, emits COPY + RUN from persisted data."""
    import json

    installs = [{"manager": "pip", "command": "pip install -r requirements.txt",
                 "copy_inputs": ["requirements.txt"]}]
    result = _run_lang_layer(
        tmp_path,
        repo_files={"requirements.txt": "pytest\n"},
        config_toml_content=f'language_installs = "{json.dumps(installs, separators=(",", ":"))}\"\n',
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "COPY requirements.txt" in output
    assert "RUN pip install -r requirements.txt" in output
    assert "copy-input-shas:" in output


def test_lang_layer_persisted_installs_hallucinated_copy_input_dropped(tmp_path):
    """Hallucinated copy_inputs (non-existent files) are dropped from COPY but RUN remains."""
    import json

    installs = [
        {
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["requirements.txt", "hallucinated-lockfile.txt"],
        }
    ]
    # Only requirements.txt exists; hallucinated-lockfile.txt does not.
    result = _run_lang_layer(
        tmp_path,
        repo_files={"requirements.txt": "pytest\n"},
        config_toml_content=f'language_installs = "{json.dumps(installs, separators=(",", ":"))}\"\n',
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "hallucinated-lockfile.txt" not in output, "hallucinated path must be dropped"
    assert "requirements.txt" in output, "existing copy_input must be kept"
    assert "RUN pip install -r requirements.txt" in output, "RUN must always be emitted"


def test_lang_layer_persisted_installs_all_copy_inputs_missing_run_still_emitted(tmp_path):
    """When all copy_inputs are hallucinated, RUN is still emitted without COPY."""
    import json

    installs = [
        {
            "manager": "pip",
            "command": "pip install -r requirements.txt",
            "copy_inputs": ["no-such-file.txt"],
        }
    ]
    result = _run_lang_layer(
        tmp_path,
        repo_files={},
        config_toml_content=f'language_installs = "{json.dumps(installs, separators=(",", ":"))}\"\n',
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "RUN pip install -r requirements.txt" in output, "RUN must appear even with no valid COPY inputs"
    assert "no-such-file.txt" not in output


def test_lang_layer_persisted_installs_multi_manager(tmp_path):
    """Multiple managers each get their own COPY+RUN layer."""
    import json

    installs = [
        {"manager": "pip", "command": "pip install -r requirements.txt",
         "copy_inputs": ["requirements.txt"]},
        {"manager": "npm", "command": "npm ci",
         "copy_inputs": ["package-lock.json", "package.json"]},
    ]
    result = _run_lang_layer(
        tmp_path,
        repo_files={
            "requirements.txt": "pytest\n",
            "package-lock.json": "{}",
            "package.json": '{"name":"x"}',
        },
        config_toml_content=f'language_installs = "{json.dumps(installs, separators=(",", ":"))}\"\n',
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "RUN pip install -r requirements.txt" in output
    assert "RUN npm ci" in output
    assert "COPY requirements.txt" in output
    assert "COPY package-lock.json" in output or "package-lock.json" in output


def test_lang_layer_fallback_to_lockfile_detection_when_no_persisted(tmp_path):
    """Without language_installs in config.toml, lockfile detection fires."""
    result = _run_lang_layer(
        tmp_path,
        repo_files={
            "requirements.txt": "pytest\n",
            "uv.lock": "# uv lockfile\n",
            "pyproject.toml": "[project]\nname='x'\n",
        },
        config_toml_content=None,  # no config.toml → lockfile detection
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "RUN uv sync" in output
    assert "COPY uv.lock" in output


def test_lang_layer_fallback_when_language_installs_empty(tmp_path):
    """Empty language_installs list → falls through to lockfile detection."""
    result = _run_lang_layer(
        tmp_path,
        repo_files={
            "pnpm-lock.yaml": "lockfileVersion: 6\n",
            "package.json": '{"name":"x"}',
        },
        config_toml_content='language_installs = "[]"\n',
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    output = result.stdout
    assert "RUN pnpm install --frozen-lockfile" in output


def test_lang_layer_no_lockfile_and_no_persisted_exits_0_no_output(tmp_path):
    """No config.toml language_installs and no lockfile → exits 0 with no output."""
    result = _run_lang_layer(
        tmp_path,
        repo_files={},  # empty repo
        config_toml_content=None,
    )
    assert result.returncode == 0, f"stderr: {result.stderr}"
    assert result.stdout.strip() == "", "no output when nothing detected"


def test_lang_layer_hash_stable_on_identical_regen(tmp_path):
    """Identical inputs produce identical output (hash stability for .dockerfile-hash)."""
    import json

    installs = [{"manager": "pip", "command": "pip install -r requirements.txt",
                 "copy_inputs": ["requirements.txt"]}]
    cfg = f'language_installs = "{json.dumps(installs, separators=(",", ":"))}\"\n'
    kwargs = dict(
        repo_files={"requirements.txt": "pytest\n"},
        config_toml_content=cfg,
    )
    r1 = _run_lang_layer(tmp_path, **kwargs)
    r2 = _run_lang_layer(tmp_path, **kwargs)
    assert r1.stdout == r2.stdout, "identical inputs must produce identical output"
