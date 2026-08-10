"""Tests for the launcher's shallow-seed knob resolution.

The launcher resolves two bash-only knobs consumed by
`scripts/remote/seed-repo.sh` on fresh remote provisions
(DESIGN §6 *Shallow seeding for heavy repos*):

  - LEERIE_SEED_DEPTH (default 50; 0 = full history / disable shallow)
  - LEERIE_SEED_SHALLOW_THRESHOLD_MB (default 200)

Resolution precedence mirrors FLY_VM_DISK_GB: CLI flag > env var >
`leerie.toml` flat key > default. Unlike `confidence_rounds`, these are
NOT read by the Python orchestrator — they live entirely in bash — so
the resolution is tested at the launcher layer.

The block under test is extracted verbatim from the launcher (mirroring
`tests/test_resolve_ec2_vars.py`'s `_extract_resolve_ec2_knob`), not
hand-copied. A hand-copied reproduction is body-blind by construction —
the tests would run a string literal defined in this file, and no change
to the launcher's actual logic could affect them. `_log.sh` is sourced
for real so `remote_log` (the validation-failure emitter the extracted
block calls) is the genuine implementation, not a stand-in.

Verified live per N13's documented trap: an inert sabotage (e.g.
removing the `[ -f "$USER_REPO/leerie.toml" ]` guard) is not a valid
falsification, since a missing toml file already makes the subsequent
`grep` fail silently either way — it passes both with and without the
guard and proves nothing. The discriminating falsification used here is
inverting the CLI/env precedence inside `_resolve_seed_knob` (swapping
which branch returns first): with the extraction,
`test_cli_wins_over_env_and_toml` and `test_env_wins_over_toml` fail;
against the old hand-copied block they could not have, since that block
never read the launcher's source at all.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"


def _extract_seed_knob_block() -> str:
    """The REAL shallow-seed resolution block, lifted out of the launcher:
    `_resolve_seed_knob()`, its CLI-arg scan, and the depth/threshold
    validation, ending at the `export` line. See module docstring for the
    body-blindness rationale and the live falsification result."""
    src = LAUNCHER.read_text()
    start = src.index("_resolve_seed_knob() {")
    end_marker = "export LEERIE_SEED_DEPTH LEERIE_SEED_SHALLOW_THRESHOLD_MB\n"
    end = src.index(end_marker, start) + len(end_marker)
    return src[start:end]


def _run(user_repo: Path, *args: str, env_extra: dict | None = None,
         ) -> subprocess.CompletedProcess:
    """Source the launcher's REAL seed-knob block in a subshell with the
    given CLI args and USER_REPO. Prints the two resolved values on
    success. `_log.sh` is sourced first so `remote_log` (called on
    validation failure) is the genuine implementation."""
    script = (
        "set -euo pipefail\n"
        f"USER_REPO={user_repo!s}\n"
        f"source {LOG_SH}\n"
        f"{_extract_seed_knob_block()}\n"
        'printf "%s %s\\n" "$LEERIE_SEED_DEPTH" "$LEERIE_SEED_SHALLOW_THRESHOLD_MB"\n'
    )
    env = {**os.environ}
    env.pop("LEERIE_SEED_DEPTH", None)
    env.pop("LEERIE_SEED_SHALLOW_THRESHOLD_MB", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True, text=True, env=env,
    )


def _resolve(user_repo: Path, *args: str, env_extra: dict | None = None,
             ) -> tuple[str, str]:
    result = _run(user_repo, *args, env_extra=env_extra)
    assert result.returncode == 0, f"unexpected failure: {result.stderr}"
    depth, thresh = result.stdout.strip().split()
    return depth, thresh


def test_defaults(tmp_path):
    """No CLI, no env, no toml → depth=50, threshold=200."""
    assert _resolve(tmp_path) == ("50", "200")


def test_cli_wins(tmp_path):
    """--seed-depth / --seed-shallow-threshold-mb on the CLI win."""
    assert _resolve(tmp_path, "--seed-depth", "10",
                    "--seed-shallow-threshold-mb", "500") == ("10", "500")


def test_cli_equals_form(tmp_path):
    """The =form is accepted too."""
    assert _resolve(tmp_path, "--seed-depth=25") == ("25", "200")


def test_env_wins_over_toml(tmp_path):
    """Env beats leerie.toml."""
    (tmp_path / "leerie.toml").write_text(
        "seed_depth = 33\nseed_shallow_threshold_mb = 300\n")
    assert _resolve(tmp_path, env_extra={"LEERIE_SEED_DEPTH": "77"}) == ("77", "300")


def test_cli_wins_over_env_and_toml(tmp_path):
    """CLI beats both env and toml."""
    (tmp_path / "leerie.toml").write_text("seed_depth = 33\n")
    assert _resolve(tmp_path, "--seed-depth", "5",
                    env_extra={"LEERIE_SEED_DEPTH": "77"}) == ("5", "200")


def test_toml_resolution(tmp_path):
    """leerie.toml flat keys are read when no CLI/env."""
    (tmp_path / "leerie.toml").write_text(
        "seed_depth = 12\nseed_shallow_threshold_mb = 150\n")
    assert _resolve(tmp_path) == ("12", "150")


def test_depth_zero_accepted(tmp_path):
    """depth=0 (full history / disable shallow) is a valid value."""
    assert _resolve(tmp_path, "--seed-depth", "0") == ("0", "200")


@pytest.mark.parametrize("bad", ["abc", "-1", "5.5", "1e3"])
def test_garbage_depth_rejected(tmp_path, bad):
    """A non-integer depth is rejected at startup, not silently ignored."""
    result = _run(tmp_path, "--seed-depth", bad)
    assert result.returncode != 0, f"garbage depth {bad!r} should be rejected"
    assert "LEERIE_SEED_DEPTH" in result.stderr


def test_threshold_zero_rejected(tmp_path):
    """threshold=0 is invalid (a 0 MB threshold would make every repo
    shallow, defeating the small-repo carve-out; use depth=0 to disable)."""
    result = _run(tmp_path, "--seed-shallow-threshold-mb", "0")
    assert result.returncode != 0
    assert "LEERIE_SEED_SHALLOW_THRESHOLD_MB" in result.stderr


@pytest.mark.parametrize("bad", ["abc", "-5", "2.0"])
def test_garbage_threshold_rejected(tmp_path, bad):
    result = _run(tmp_path, "--seed-shallow-threshold-mb", bad)
    assert result.returncode != 0
    assert "LEERIE_SEED_SHALLOW_THRESHOLD_MB" in result.stderr


def test_block_stripped_from_rewritten_args():
    """Coupling test: the two flags must be stripped from REWRITTEN_ARGS
    (launcher-only), so they never reach the orchestrator's strict
    parse_args()."""
    src = LAUNCHER.read_text()
    assert "--seed-depth|--seed-shallow-threshold-mb)" in src, (
        "Launcher must strip --seed-depth / --seed-shallow-threshold-mb "
        "from REWRITTEN_ARGS (they are host-only; the orchestrator uses "
        "strict parse_args and would error 'unrecognized arguments')."
    )
