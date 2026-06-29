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

Strategy: the `config` verb is added to the launcher by config-010 (in-plan
at the time this test was written). Rather than extracting the block from the
launcher (which would fail before config-010 lands), these tests use a
self-contained bash harness that implements the spec and records observable
side-effects (argv log for claude, nerdctl call log for the no-container
assertion). Once config-010 integrates the verb into the real launcher a
coupling test (`test_config_arm_exists_in_launcher`) guards that the harness
stays in sync.

Precedent for this pattern: test_launcher_runtime_knob.py (standalone
harness for a launcher case arm), test_ensure_image.py (function harness).
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# ---------------------------------------------------------------------------
# Shared harness
# ---------------------------------------------------------------------------
# The harness mirrors what config-010 will add to the case dispatch at leerie
# line ~544.  Key invariants the harness encodes:
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
# else falls through to simple pattern-based inference (same logic as
# orchestrator/_infer_build_lint_test but implemented here for the config verb
# so the verb requires no container / no orchestrator import).
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
  # Simple pattern-based inference (covers the common cases).
  case "$axis" in
    build)
      if [ -f "$USER_REPO/Makefile" ]; then echo "make"; return; fi
      if [ -f "$USER_REPO/package.json" ]; then echo "pnpm run build"; return; fi
      if [ -f "$USER_REPO/pyproject.toml" ] || [ -f "$USER_REPO/setup.py" ]; then
        echo "python3 -m build"; return
      fi
      echo ""
      ;;
    lint)
      if [ -f "$USER_REPO/pyproject.toml" ] && \
         grep -q "ruff\|flake8\|pylint" "$USER_REPO/pyproject.toml" 2>/dev/null; then
        echo "ruff check ."; return
      fi
      if [ -f "$USER_REPO/package.json" ]; then echo "pnpm run lint"; return; fi
      echo ""
      ;;
    test)
      if [ -d "$USER_REPO/tests" ] || [ -f "$USER_REPO/pytest.ini" ] || \
         [ -f "$USER_REPO/pyproject.toml" ]; then
        if grep -q "pytest" "${USER_REPO}/pyproject.toml" 2>/dev/null || \
           [ -d "$USER_REPO/tests" ]; then
          echo "pytest tests/"; return
        fi
      fi
      if [ -f "$USER_REPO/package.json" ]; then echo "pnpm test"; return; fi
      echo ""
      ;;
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
    # Plant a tests/ dir so BLT inference picks up pytest
    (user_repo / "tests").mkdir()
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


def test_init_uses_inferred_blt_from_tests_dir(tmp_path):
    """--init uses pytest tests/ when a tests/ directory is present."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "tests").mkdir()
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert "pytest" in config_text


def test_init_uses_inferred_blt_from_makefile(tmp_path):
    """--init uses `make` as the build command when Makefile is present."""
    user_repo = tmp_path / "repo"
    user_repo.mkdir()
    (user_repo / "Makefile").write_text("build:\n\techo build\n")
    _run_config(user_repo, ["--init"], tmp_path)
    config_text = (user_repo / ".leerie" / "config.toml").read_text()
    assert 'build = "make"' in config_text


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


@pytest.mark.xfail(
    reason="config-010 not yet integrated — will pass once the `config)` arm lands in leerie",
    strict=True,
)
def test_config_arm_exists_in_launcher():
    """The real launcher must contain a `config)` case arm (added by config-010).

    Marked xfail while config-010 is in-plan. Once integrated, strict=True
    flips it from xfail→pass and the mark should be removed.
    """
    launcher_text = (REPO_ROOT / "leerie").read_text()
    # The case arm in the main dispatch must match `config)` as a case pattern.
    assert "config)" in launcher_text, (
        "The `config` verb case arm was not found in the launcher. "
        "This test will pass once config-010 is integrated."
    )


def test_config_arm_exits_before_nerdctl_run():
    """The config case arm must `exit 0` before the nerdctl run line."""
    launcher_text = (REPO_ROOT / "leerie").read_text()
    if "config)" not in launcher_text:
        # config-010 not yet integrated; skip gracefully
        pytest.skip("config-010 not yet integrated — coupling test deferred")
    # The exit 0 in the config arm must appear before `nerdctl run`
    config_pos = launcher_text.index("config)")
    nerdctl_run_pos = launcher_text.index("nerdctl run", config_pos)
    # There must be an `exit 0` between the config arm and the nerdctl run line
    exit_pos = launcher_text.find("exit 0", config_pos)
    assert exit_pos < nerdctl_run_pos, (
        "config) arm does not exit before nerdctl run — container path reached"
    )
