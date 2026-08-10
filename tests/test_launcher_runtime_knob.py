"""Tests for the --runtime / LEERIE_RUNTIME / leerie.toml `runtime` launcher knob.

The parsing logic lives in the bash launcher (`leerie`). These tests EXTRACT
that block verbatim from the launcher and run it, rather than reproducing it
— see `_extract_runtime_block` for why the reproduction this replaced could
never fail.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Precedence (lowest → highest): default → TOML → env → CLI.
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"

_BLOCK_START = "# --- runtime-mode knob ---"
_BLOCK_END = "# --- auto-detect runtime on resume ---"


def _extract_runtime_block() -> str:
    """Lift the RUNTIME resolution block out of the real launcher.

    This file used to run a hand-written reproduction of that block, which
    made it body-blind BY CONSTRUCTION: it never read or executed `leerie`,
    so no change to the launcher could fail any of its tests. The copy had
    already drifted twice over — it was missing `_RUNTIME_EXPLICIT`
    entirely (set at five sites and consumed by the resume auto-detect and
    the "resuming a Fly run with --runtime local" warning, so nothing in
    the suite covered that flag at all), and it emitted errors with a bare
    `echo "leerie: ..."` where the launcher uses `remote_log`. The one
    assertion about that output matched only by coincidence, since both
    spellings happen to contain the substring being asserted."""
    src = LAUNCHER.read_text()
    i = src.index(_BLOCK_START)
    j = src.index(_BLOCK_END, i)
    return src[i:j]


# The extracted block calls `remote_log` and reads `USER_REPO` and `"$@"`.
# Source the real logger rather than stubbing it, so the launcher's actual
# message format is what the assertions see.
_HARNESS = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    'USER_REPO="$1"\n'
    "shift\n"
    f'source "{LOG_SH}"\n'
    + _extract_runtime_block()
    # Two lines: the resolved runtime, then whether it was set explicitly.
    # `_RUNTIME_EXPLICIT` gates the resume auto-detect and the "resuming a
    # Fly run with --runtime local" warning, and had NO coverage anywhere in
    # the suite — deleting every assignment left all tests green.
    + '\necho "$RUNTIME"\necho "$_RUNTIME_EXPLICIT"\n'
)


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
    # First line is RUNTIME; `_run_explicit` reads the second.
    first = result.stdout.strip().splitlines()[0] if result.stdout.strip() else ""
    return first, result.stderr.strip()


def _run_explicit(repo_root: Path, env: dict, cli_args: list[str]) -> str:
    """The harness's second line: `_RUNTIME_EXPLICIT` as the launcher left
    it. Separate helper so the ~20 tests above keep comparing against a
    bare runtime string."""
    result = subprocess.run(
        ["bash", "-c", _HARNESS, "--", str(repo_root)] + cli_args,
        env={**{"PATH": "/usr/bin:/bin"}, **env},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[1]


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


# ── _RUNTIME_EXPLICIT ─────────────────────────────────────────────────────────
#
# The flag distinguishes "runtime is local because nobody said otherwise"
# from "runtime is local because the operator asked for it". The launcher
# consumes it twice: the resume auto-detect only promotes RUNTIME to fly/ec2
# when it is false, and the "resuming a Fly run with --runtime local"
# warning only fires when it is true. Before this block, `grep -rn
# _RUNTIME_EXPLICIT tests/` returned nothing — deleting every assignment in
# the launcher left the whole suite green.

def test_runtime_explicit_false_by_default(tmp_path):
    assert _run_explicit(tmp_path, {}, []) == "false"


def test_runtime_explicit_true_for_cli_equals_form(tmp_path):
    assert _run_explicit(tmp_path, {}, ["--runtime=fly"]) == "true"


def test_runtime_explicit_true_for_cli_two_arg_form(tmp_path):
    assert _run_explicit(tmp_path, {}, ["--runtime", "fly"]) == "true"


def test_runtime_explicit_true_for_env(tmp_path):
    assert _run_explicit(tmp_path, {"LEERIE_RUNTIME": "fly"}, []) == "true"


def test_runtime_explicit_true_for_toml(tmp_path):
    (tmp_path / "leerie.toml").write_text("runtime = fly\n")
    assert _run_explicit(tmp_path, {}, []) == "true"


def test_runtime_explicit_true_even_when_value_is_the_default(tmp_path):
    """The discriminating case: `--runtime local` resolves to the same value
    as no flag at all, so only the flag records that the operator chose it.
    This is exactly what the resume auto-detect keys on — without it, an
    explicit `--runtime local` on a resumed Fly run would be silently
    promoted back to fly."""
    assert _run_explicit(tmp_path, {}, ["--runtime", "local"]) == "true"
    assert _run_explicit(tmp_path, {}, []) == "false"
