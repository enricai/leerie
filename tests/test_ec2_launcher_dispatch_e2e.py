"""Pins the RUNTIME=ec2 dispatch continuing past preflight into the full
create -> seed -> orchestrate -> teardown lifecycle, with the old
not-yet-wired abort gone (leerie:6064 historically).

tests/test_ec2_provision.py, tests/test_ec2_seed_repo.py, and
tests/test_ec2_decide_teardown.py already cover provision_instance(),
ec2_seed_repo(), and decide_ec2_teardown() standalone. tests/
test_ec2_e2e_provision.py already exercises the real, verbatim-extracted
`RUNTIME=ec2` dispatch block end to end (including a full lifecycle test
that reaches decide_ec2_teardown's clean-exit terminate arm, and the
not-yet-wired grep guard). This module is the dedicated pin for the
wiring *seam* itself — reusing that harness rather than reinventing it,
per this repo's sibling-module import convention (tests/
test_ec2_launcher_credentials.py imports the same helpers and carries the
same harness-sanity guard this module adds).

Harness: reuses tests/test_ec2_e2e_provision.py's
`extract_ec2_dispatch_block` / `run_ec2_dispatch` / `stub_aws_env`
helpers and tests/ec2_stub.py's resource-tracking `aws` stub — never
source `leerie` directly (it runs full preflight + CLI dispatch on
source), and never hand-reproduce the dispatch block (it would drift
silently from the launcher).
"""
from __future__ import annotations

from pathlib import Path

from tests.ec2_stub import leaked_resources, read_log, read_state
from tests.test_ec2_e2e_provision import (
    REQUIRED_PROVISION_ENV,
    extract_ec2_dispatch_block,
    run_ec2_dispatch,
    stub_aws_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


# ---------------------------------------------------------------------------
# Harness sanity
# ---------------------------------------------------------------------------


def test_dispatch_block_extraction_reused_from_e2e_provision_module():
    """Sanity check that this module exercises the same real,
    verbatim-extracted block as test_ec2_e2e_provision.py — not a
    hand-copied reproduction that could silently drift from the launcher.
    Mirrors tests/test_ec2_launcher_credentials.py's identical guard."""
    block = extract_ec2_dispatch_block()
    assert 'RUNTIME" = "ec2"' in block
    assert "require_aws" in block
    assert "provision_instance" in block


# ---------------------------------------------------------------------------
# The not-yet-wired abort must be gone (CLAUDE.md checklist: confirm no
# stragglers of a removed string)
# ---------------------------------------------------------------------------


def test_not_yet_wired_abort_string_is_gone_from_launcher():
    src = LAUNCHER.read_text()
    assert "instance provisioning is not yet wired" not in src
    assert "not yet wired" not in src


# ---------------------------------------------------------------------------
# A full launch: provision -> seed -> orchestrate -> teardown, zero leaks
# ---------------------------------------------------------------------------


def test_full_launch_provisions_seeds_and_reaches_teardown_with_no_leaks(tmp_path):
    """With valid credentials, the dispatch block must provision exactly
    one instance, seed the repo (the stubbed ec2_seed_repo is invoked —
    its own behavior is covered standalone by test_ec2_seed_repo.py), and
    reach decide_ec2_teardown's EXIT trap, leaving zero leaked instances
    and zero leaked volumes on a clean exit."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(
        env,
        extra_trailer="_try_fetch_state_for_ec2_teardown() { return 0; }",
    )
    assert result.returncode == 0, result.stderr

    combined = result.stdout + result.stderr
    assert "stub: ec2_seed_repo" in combined, (
        f"expected the dispatch block to reach ec2_seed_repo; output={combined}"
    )

    state = read_state(aws_dir)
    assert len(state["instances"]) == 1, (
        f"expected exactly one provisioned instance; state={state}"
    )
    (_iid, rec), = state["instances"].items()
    assert rec["state"] == "terminated", (
        f"a clean exit with a successful teardown-time state sync must "
        f"terminate the instance; state={state}"
    )
    assert state["volumes"] == {}, (
        f"root EBS is implicit via run-instances' own "
        f"DeleteOnTermination=true default; state={state}"
    )
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}, (
        f"a terminated instance with no tracked volume is not a leak; "
        f"state={state}"
    )


def test_preflight_precedes_provisioning_in_full_launch(tmp_path):
    """require_aws's sts get-caller-identity must still precede any `aws
    ec2 run-instances` call by call index in the full launch->teardown
    lifecycle, not just the provision-only path pinned in
    test_ec2_e2e_provision.py."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(
        env,
        extra_trailer="_try_fetch_state_for_ec2_teardown() { return 0; }",
    )
    assert result.returncode == 0, result.stderr

    log = read_log(aws_dir)
    identity_calls = [
        i for i, line in enumerate(log)
        if line.startswith("sts get-caller-identity")
    ]
    run_instances_calls = [
        i for i, line in enumerate(log)
        if line.startswith("ec2 run-instances")
    ]
    assert identity_calls, f"expected an sts get-caller-identity call; log={log}"
    assert run_instances_calls, f"expected an ec2 run-instances call; log={log}"
    assert min(identity_calls) < min(run_instances_calls), (
        f"require_aws's sts get-caller-identity must precede any "
        f"ec2 run-instances call; log={log}"
    )


def test_failing_credential_probe_aborts_with_zero_tracked_resources(tmp_path):
    """A failing credential probe must still abort non-zero with the `aws
    sso login --profile <p>`-shaped hint and leave zero tracked
    instances/volumes, exercised through the same full-lifecycle harness
    this module otherwise uses for the happy path."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=False,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(env)

    assert result.returncode != 0, (
        f"a failing credential probe must abort non-zero; "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "aws sso login" in combined, combined

    log = read_log(aws_dir)
    assert not [l for l in log if l.startswith("ec2 run-instances")], (
        f"no ec2 run-instances call should happen when the preflight "
        f"fails; log={log}"
    )

    state = read_state(aws_dir)
    assert state["instances"] == {}, state
    assert state["volumes"] == {}, state
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}
