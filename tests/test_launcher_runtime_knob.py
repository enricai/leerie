"""Tests for the --runtime / LEERIE_RUNTIME / leerie.toml `runtime` launcher knob.

The parsing logic lives in the bash launcher (`leerie`), so these tests use a
minimal bash harness that mirrors the exact resolution block and echoes the
resolved RUNTIME value.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Bash harness that mirrors the RUNTIME resolution block from `leerie`.
# Precedence (lowest → highest): default → TOML → env → CLI.
_HARNESS = r"""
#!/usr/bin/env bash
set -euo pipefail
USER_REPO="$1"
shift   # remaining args are simulated CLI

RUNTIME=local

if [ -f "$USER_REPO/leerie.toml" ]; then
  toml_runtime="$(awk '/^[[:space:]]*runtime[[:space:]]*=/ {
                         gsub(/^[[:space:]]*runtime[[:space:]]*=[[:space:]]*/, "", $0);
                         gsub(/^"|"$/, "", $0);
                         print; exit
                       }' "$USER_REPO/leerie.toml" 2>/dev/null || true)"
  case "${toml_runtime:-}" in
    local|fly|ec2) RUNTIME="${toml_runtime}" ;;
    "")        : ;;
    *)
      echo "leerie: leerie.toml: runtime=${toml_runtime} is not one of local|fly|ec2" >&2
      exit 1
      ;;
  esac
fi

case "${LEERIE_RUNTIME:-}" in
  local|fly|ec2) RUNTIME="${LEERIE_RUNTIME}" ;;
  "")        : ;;
  *)
    echo "leerie: LEERIE_RUNTIME=${LEERIE_RUNTIME} is not one of local|fly|ec2" >&2
    exit 1
    ;;
esac

# CLI (--runtime=VALUE form)
for arg in "$@"; do
  case "$arg" in
    --runtime=local)   RUNTIME=local ;;
    --runtime=fly)     RUNTIME=fly ;;
    --runtime=ec2)     RUNTIME=ec2 ;;
    --runtime=*)
      echo "leerie: --runtime=${arg#--runtime=} is not one of local|fly|ec2" >&2
      exit 1
      ;;
  esac
done

# two-arg form --runtime VALUE
prev_was_runtime=false
for arg in "$@"; do
  if $prev_was_runtime; then
    case "$arg" in
      local|fly|ec2) RUNTIME="$arg" ;;
      *)
        echo "leerie: --runtime $arg is not one of local|fly|ec2" >&2
        exit 1
        ;;
    esac
    prev_was_runtime=false
    continue
  fi
  if [ "$arg" = "--runtime" ]; then
    prev_was_runtime=true
  fi
done

echo "$RUNTIME"
"""


def _run(
    repo_root: Path,
    env: dict,
    cli_args: list[str],
    *,
    expect_fail: bool = False,
) -> tuple[str, str]:
    """Run the harness; return (stdout, stderr).  Raises on non-zero exit
    unless expect_fail=True."""
    result = subprocess.run(
        ["bash", "-c", _HARNESS, "--", str(repo_root)] + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    if not expect_fail:
        assert result.returncode == 0, result.stderr
    return result.stdout.strip(), result.stderr.strip()


# ── defaults ──────────────────────────────────────────────────────────────────


def test_default_is_local(tmp_path):
    out, _ = _run(tmp_path, {}, [])
    assert out == "local"


# ── canonical env LEERIE_RUNTIME ───────────────────────────────────────────────


def test_leerie_runtime_fly(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "fly"}, [])
    assert out == "fly"


def test_leerie_runtime_local_explicit(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "local"}, [])
    assert out == "local"


def test_leerie_runtime_empty_treated_as_unset(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": ""}, [])
    assert out == "local"


def test_leerie_runtime_invalid_exits_nonzero(tmp_path):
    _, err = _run(tmp_path, {"LEERIE_RUNTIME": "nope"}, [], expect_fail=True)
    assert "is not one of local|fly|ec2" in err
    assert "nope" in err


def test_leerie_runtime_ec2(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "ec2"}, [])
    assert out == "ec2"


# ── canonical TOML `runtime` ─────────────────────────────────────────────────


def test_toml_runtime_fly(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = fly\n")
    out, _ = _run(tmp_path, {}, [])
    assert out == "fly"


def test_toml_runtime_local_explicit(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = local\n")
    out, _ = _run(tmp_path, {}, [])
    assert out == "local"


def test_toml_runtime_ec2(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = ec2\n")
    out, _ = _run(tmp_path, {}, [])
    assert out == "ec2"


def test_toml_runtime_invalid_exits_nonzero(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = bogus\n")
    _, err = _run(tmp_path, {}, [], expect_fail=True)
    assert "is not one of local|fly|ec2" in err
    assert "bogus" in err


def test_toml_runtime_unrelated_key_stays_local(tmp_path):
    (tmp_path / "leerie.toml").write_text("source_of_truth = codebase\n")
    out, _ = _run(tmp_path, {}, [])
    assert out == "local"


# ── canonical CLI --runtime ───────────────────────────────────────────────────


def test_cli_runtime_equals_fly(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime=fly"])
    assert out == "fly"


def test_cli_runtime_equals_local(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime=local"])
    assert out == "local"


def test_cli_runtime_equals_ec2(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime=ec2"])
    assert out == "ec2"


def test_cli_runtime_space_fly(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime", "fly"])
    assert out == "fly"


def test_cli_runtime_space_local(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime", "local"])
    assert out == "local"


def test_cli_runtime_space_ec2(tmp_path):
    out, _ = _run(tmp_path, {}, ["--runtime", "ec2"])
    assert out == "ec2"


def test_cli_runtime_invalid_exits_nonzero(tmp_path):
    _, err = _run(tmp_path, {}, ["--runtime=bad"], expect_fail=True)
    assert "is not one of local|fly|ec2" in err


# ── precedence: CLI > env > TOML ──────────────────────────────────────────────


def test_cli_wins_over_env(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "fly"}, ["--runtime=local"])
    assert out == "local"


def test_cli_ec2_wins_over_env(tmp_path):
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "fly"}, ["--runtime=ec2"])
    assert out == "ec2"


def test_env_wins_over_toml(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = fly\n")
    out, _ = _run(tmp_path, {"LEERIE_RUNTIME": "local"}, [])
    assert out == "local"


def test_cli_wins_over_toml(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = fly\n")
    out, _ = _run(tmp_path, {}, ["--runtime=local"])
    assert out == "local"
