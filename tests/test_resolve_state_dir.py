"""Tests for the LEERIE_STATE_HOST_DIR resolution block in the launcher.

The resolution logic lives in the bash launcher (`leerie`); these tests use
a minimal bash harness that mirrors the exact block and echoes the resolved
LEERIE_STATE_HOST_DIR value.

Precedence (lowest → highest):
  default ($HOME/.leerie/state/<sha16>-<basename>)
  → leerie.toml `state_dir = ...`
  → LEERIE_STATE_DIR env var
  → --state-dir CLI flag
"""
from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Bash harness that mirrors the LEERIE_STATE_HOST_DIR resolution block from
# `leerie`. Takes USER_REPO as $1 and HOME as $2; remaining args are CLI.
_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
USER_REPO="$1"
HOME="$2"
export HOME
shift 2   # remaining args are simulated CLI

_state_dir_default() {
  local key
  key="$(python3 -c \
    "import hashlib,sys,os; p=sys.argv[1]; print(hashlib.sha256(p.encode()).hexdigest()[:16]+'-'+os.path.basename(p.rstrip('/')))" \
    "$USER_REPO")"
  echo "$HOME/.leerie/state/$key"
}

LEERIE_STATE_HOST_DIR="$(_state_dir_default)"

if [ -f "$USER_REPO/leerie.toml" ]; then
  _toml_state_dir="$( { grep -E '^[[:space:]]*state_dir[[:space:]]*=' \
                            "$USER_REPO/leerie.toml" 2>/dev/null \
                        || true; } \
                      | head -1 \
                      | sed -E 's/^[[:space:]]*state_dir[[:space:]]*=[[:space:]]*//;
                                s/[[:space:]]*$//;
                                s/^"(.*)"$/\1/;
                                s/^'"'"'(.*)'"'"'$/\1/')"
  if [ -n "$_toml_state_dir" ]; then
    case "$_toml_state_dir" in
      "~")   _toml_state_dir="$HOME" ;;
      "~/"*) _toml_state_dir="$HOME/${_toml_state_dir#"~/"}" ;;
    esac
    LEERIE_STATE_HOST_DIR="$_toml_state_dir"
  fi
  unset _toml_state_dir
fi

if [ -n "${LEERIE_STATE_DIR:-}" ]; then
  LEERIE_STATE_HOST_DIR="$LEERIE_STATE_DIR"
fi

_cli_state_dir=""
_prev_was_state_dir=false
for arg in "$@"; do
  if $_prev_was_state_dir; then
    _cli_state_dir="$arg"
    _prev_was_state_dir=false
    continue
  fi
  case "$arg" in
    --state-dir=*) _cli_state_dir="${arg#--state-dir=}" ;;
    --state-dir)   _prev_was_state_dir=true ;;
  esac
done
if [ -n "$_cli_state_dir" ]; then
  LEERIE_STATE_HOST_DIR="$_cli_state_dir"
fi
unset _cli_state_dir _prev_was_state_dir

case "$LEERIE_STATE_HOST_DIR" in
  "~")   LEERIE_STATE_HOST_DIR="$HOME" ;;
  "~/"*) LEERIE_STATE_HOST_DIR="$HOME/${LEERIE_STATE_HOST_DIR#"~/"}" ;;
esac

echo "$LEERIE_STATE_HOST_DIR"
"""


def _expected_default_key(user_repo: Path) -> str:
    """Compute the expected default key: sha256(abs_path)[:16]-basename."""
    abs_path = str(user_repo)
    sha16 = hashlib.sha256(abs_path.encode()).hexdigest()[:16]
    basename = os.path.basename(abs_path.rstrip("/"))
    return f"{sha16}-{basename}"


def _run(
    user_repo: Path,
    fake_home: Path,
    env: dict,
    cli_args: list[str],
    *,
    expect_fail: bool = False,
) -> tuple[str, str]:
    """Run the harness; return (stdout, stderr). Raises on non-zero exit
    unless expect_fail=True."""
    result = subprocess.run(
        ["bash", "-c", _HARNESS, "--", str(user_repo), str(fake_home)]
        + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    if not expect_fail:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip(), result.stderr.strip()


# ── default resolution ────────────────────────────────────────────────────────


def test_default_is_under_home(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out.startswith(str(fake_home))


def test_default_is_not_inside_user_repo(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert not out.startswith(str(user_repo))


def test_default_contains_state_subdirectory(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert "/.leerie/state/" in out


def test_default_key_contains_basename(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    assert "myproject" in out


def test_default_key_is_stable(tmp_path):
    """Same inputs → same output (deterministic)."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out1, _ = _run(user_repo, fake_home, {}, [])
    out2, _ = _run(user_repo, fake_home, {}, [])
    assert out1 == out2


def test_default_key_differs_by_repo_path(tmp_path):
    """Different repo paths → different resolved dirs."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    repo_a = tmp_path / "repoA"
    repo_a.mkdir()
    repo_b = tmp_path / "repoB"
    repo_b.mkdir()
    out_a, _ = _run(repo_a, fake_home, {}, [])
    out_b, _ = _run(repo_b, fake_home, {}, [])
    assert out_a != out_b


def test_default_path_matches_python_derivation(tmp_path):
    """The bash key derivation matches our Python reference implementation."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {}, [])
    expected_key = _expected_default_key(user_repo)
    expected_path = str(fake_home) + f"/.leerie/state/{expected_key}"
    assert out == expected_path


# ── leerie.toml `state_dir` override ─────────────────────────────────────────


def test_toml_state_dir_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = tmp_path / "custom-state"
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(toml_dir)


def test_toml_state_dir_quoted_value(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "quoted-state")
    (user_repo / "leerie.toml").write_text(f'state_dir = "{toml_dir}"\n')
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == toml_dir


def test_toml_state_dir_tilde_expansion(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    (user_repo / "leerie.toml").write_text("state_dir = ~/mystate\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == str(fake_home) + "/mystate"


def test_toml_unrelated_key_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    (user_repo / "leerie.toml").write_text("runtime = local\n")
    out, _ = _run(user_repo, fake_home, {}, [])
    # Should be the default, not empty or something else.
    assert "/.leerie/state/" in out


# ── LEERIE_STATE_DIR env override ─────────────────────────────────────────────


def test_env_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    env_dir = str(tmp_path / "env-state")
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [])
    assert out == env_dir


def test_env_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    env_dir = str(tmp_path / "env-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [])
    assert out == env_dir


def test_env_empty_leaves_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    out, _ = _run(user_repo, fake_home, {"LEERIE_STATE_DIR": ""}, [])
    assert "/.leerie/state/" in out


# ── CLI --state-dir override ──────────────────────────────────────────────────


def test_cli_equals_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(user_repo, fake_home, {}, [f"--state-dir={cli_dir}"])
    assert out == cli_dir


def test_cli_space_form_overrides_default(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(user_repo, fake_home, {}, ["--state-dir", cli_dir])
    assert out == cli_dir


def test_cli_overrides_env(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    env_dir = str(tmp_path / "env-state")
    cli_dir = str(tmp_path / "cli-state")
    out, _ = _run(
        user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, [f"--state-dir={cli_dir}"]
    )
    assert out == cli_dir


def test_cli_overrides_toml(tmp_path):
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    cli_dir = str(tmp_path / "cli-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")
    out, _ = _run(user_repo, fake_home, {}, [f"--state-dir={cli_dir}"])
    assert out == cli_dir


# ── precedence summary ────────────────────────────────────────────────────────


def test_precedence_cli_beats_env_beats_toml_beats_default(tmp_path):
    """Full precedence ladder: CLI wins over env wins over toml wins over default."""
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    user_repo = tmp_path / "myproject"
    user_repo.mkdir()
    toml_dir = str(tmp_path / "toml-state")
    env_dir = str(tmp_path / "env-state")
    cli_dir = str(tmp_path / "cli-state")
    (user_repo / "leerie.toml").write_text(f"state_dir = {toml_dir}\n")

    # All three set; CLI should win.
    out, _ = _run(
        user_repo,
        fake_home,
        {"LEERIE_STATE_DIR": env_dir},
        [f"--state-dir={cli_dir}"],
    )
    assert out == cli_dir

    # Remove CLI; env should win over toml.
    out, _ = _run(
        user_repo, fake_home, {"LEERIE_STATE_DIR": env_dir}, []
    )
    assert out == env_dir

    # Remove env; toml should win over default.
    out, _ = _run(user_repo, fake_home, {}, [])
    assert out == toml_dir
