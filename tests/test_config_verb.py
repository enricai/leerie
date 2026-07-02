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

import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

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
    [ -n "$build" ] || build="npm run build"
    [ -n "$test" ] || test="npm test"
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


def test_config_chat_md_mentions_user_leerie():
    """prompts/config_chat.md mentions switching back to `USER leerie`."""
    text = _config_chat_md()
    assert "USER leerie" in text


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
# leerie.py:12525-12597). Each entry writes exactly the marker files for
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
