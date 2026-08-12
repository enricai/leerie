"""N32: NO_VERIFY_PUSH must be seeded from the environment, not clobbered.

leerie:2798 (the Fly-fetch finalize path) and leerie:4696 (the pre-trap Fly
launch path) both used to unconditionally set NO_VERIFY_PUSH=false before
scanning argv for --no-verify, destroying any inherited environment value.
scripts/host-finalize.sh documents (and actively advises on a push failure)
NO_VERIFY_PUSH=true as an escape hatch, so that inheritance must survive.
"""

import re
import subprocess
from pathlib import Path

import pytest

LAUNCHER = Path(__file__).resolve().parent.parent / "leerie"


def _extract_block(marker: str, start_after: str, end_before: str) -> str:
    """Extract a self-contained NO_VERIFY_PUSH-seeding block from the launcher."""
    src = LAUNCHER.read_text()
    start = src.index(start_after) + len(start_after)
    end = src.index(end_before, start) + len(end_before)
    block = src[start:end]
    assert marker in block, f"expected {marker!r} in extracted block"
    return block


def _site_a_block() -> str:
    # The fetch_branch finalize path (inside the "detect --no-verify" comment).
    return _extract_block(
        'NO_VERIFY_PUSH="${NO_VERIFY_PUSH:-false}"',
        "# Detect --no-verify and --no-push from $@ for consistency",
        "export NO_VERIFY_PUSH",
    )


def _site_b_block() -> str:
    return _extract_block(
        'NO_VERIFY_PUSH="${NO_VERIFY_PUSH:-false}"',
        "# Parse --no-verify and export NO_VERIFY_PUSH here, BEFORE",
        "export NO_VERIFY_PUSH",
    )


def _run_block(block: str, argv: list[str], env_value: str | None) -> str:
    script = block + '\nexport NO_VERIFY_PUSH\necho "$NO_VERIFY_PUSH"\n'
    env = {"PATH": "/usr/bin:/bin"}
    if env_value is not None:
        env["NO_VERIFY_PUSH"] = env_value
    result = subprocess.run(
        ["bash", "-c", 'set -- "$@"; ' + script, "_", *argv],
        capture_output=True,
        text=True,
        env=env,
        timeout=10,
    )
    assert result.returncode == 0, result.stderr
    return result.stdout.strip().splitlines()[-1]


@pytest.mark.parametrize("get_block", [_site_a_block, _site_b_block])
def test_no_reset_source_shape(get_block):
    block = get_block()
    assert 'NO_VERIFY_PUSH="${NO_VERIFY_PUSH:-false}"' in block
    assert re.search(r'^\s*NO_VERIFY_PUSH=false\s*$', block, re.MULTILINE) is None


@pytest.mark.parametrize("get_block", [_site_a_block, _site_b_block])
def test_env_value_survives_with_no_argv_override(get_block):
    assert _run_block(get_block(), [], env_value="true") == "true"


@pytest.mark.parametrize("get_block", [_site_a_block, _site_b_block])
def test_default_false_when_unset(get_block):
    assert _run_block(get_block(), [], env_value=None) == "false"


@pytest.mark.parametrize("get_block", [_site_a_block, _site_b_block])
def test_argv_no_verify_overrides_unset_env(get_block):
    assert _run_block(get_block(), ["--no-verify"], env_value=None) == "true"


@pytest.mark.parametrize("get_block", [_site_a_block, _site_b_block])
def test_argv_no_verify_still_wins_regardless_of_env(get_block):
    # Even a "false"-seeded env plus the argv flag still yields true.
    assert _run_block(get_block(), ["--no-verify"], env_value="false") == "true"
