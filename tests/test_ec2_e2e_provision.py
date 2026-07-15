"""Tests for the launcher's `RUNTIME=ec2` dispatch preflight ordering.

DESIGN §6 *EC2 runtime lifecycle* + IMPLEMENTATION.md "Runtime mode"
establish require_aws() (scripts/remote/ec2-lib.sh) as the host-side
preflight that must run — and succeed — before any `aws ec2 ...` call.
tests/test_ec2_lib_sh.py already pins require_aws() as a standalone
function; this module pins the seam a unit test cannot see: that the
`leerie` launcher's `RUNTIME=ec2` branch actually calls it, in the right
order, before any resource is created, and that a failing credential
probe leaves zero AWS resources behind.

Harness: this module extracts the launcher's `elif [ "$RUNTIME" = "ec2"
]` block verbatim (mirroring tests/test_launcher_env_forwarding.py's
`_extract_forwarding_loop` / tests/test_build_repo_image.py's
`_extract_block` approach) so the test exercises the real dispatch
code, not a hand-copied reproduction — we do not source `leerie`
directly because it runs preflight + full CLI dispatch on source
(tests/test_ensure_image.py's docstring explains the same constraint).
The extracted block is combined with ec2-provision.sh's real,
already-shipped `provision_instance()` (called immediately after, the
same way ec2-provision.sh's own module docstring says the launcher
will invoke it once instance-provisioning wiring lands) so the ordering
assertion below is exercised against a real `run-instances` call
rather than an empty, vacuously-true log.

The stub `aws` is tests/ec2_stub.py's resource-tracking state machine
(not the argv-only stub in test_ec2_lib_sh.py) so the credential-
failure case can assert zero tracked instances/volumes, not just which
commands were invoked.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, leaked_resources, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"

REQUIRED_PROVISION_ENV = {
    "LEERIE_EC2_AMI": "ami-0123456789abcdef0",
    "LEERIE_EC2_INSTANCE_TYPE": "m5.xlarge",
    "LEERIE_EC2_KEY_NAME": "leerie-key",
    "LEERIE_EC2_SECURITY_GROUP": "sg-0123456789abcdef0",
    "LEERIE_EC2_SUBNET_ID": "subnet-0123456789abcdef0",
}


# ---------------------------------------------------------------------------
# Shared bash harness — sibling children (test-008-2-2, test-008-2-3) import
# these helpers.
# ---------------------------------------------------------------------------


def extract_ec2_dispatch_block() -> str:
    """Pull the launcher's `RUNTIME=ec2` elif arm verbatim so the test
    exercises the real dispatch code, not a hand-copied reproduction."""
    src = LAUNCHER.read_text()
    start_marker = 'elif [ "$RUNTIME" = "ec2" ]; then'
    end_marker = "\nelse\n  # --- local container execution path"
    s = src.index(start_marker)
    e = src.index(end_marker, s)
    assert s != -1 and e != -1, "could not locate the RUNTIME=ec2 dispatch block in the launcher"
    return src[s:e]


def stub_aws_env(aws_dir: Path, *, identity_succeeds: bool = True,
                  extra: dict | None = None) -> dict:
    """Write the resource-tracking `aws` stub into aws_dir and build the
    env dict that runs it. When identity_succeeds is False, `sts
    get-caller-identity` is made to fail without disturbing the stub's
    state-machine behavior for any other subcommand."""
    _stub_aws(aws_dir)
    if not identity_succeeds:
        _break_sts_get_caller_identity(aws_dir)
    env = {k: v for k, v in os.environ.items()}
    env["PATH"] = f"{aws_dir}:{env.get('PATH', '')}"
    env["USER_REPO"] = str(aws_dir)
    env["LEERIE_REPO"] = str(REPO_ROOT)
    env.pop("LEERIE_STATE_DIR", None)
    if extra:
        env.update(extra)
    return env


def _break_sts_get_caller_identity(aws_dir: Path) -> None:
    """Rewrite the stub so `aws sts get-caller-identity` exits 1 while
    every other subcommand keeps working normally. Done as a thin bash
    wrapper around the real (Python) stub rather than editing the stub
    source, so the resource-tracking behavior stays exactly as shipped
    for any call that does get through."""
    real_stub = aws_dir / "aws"
    real_stub_impl = aws_dir / "_aws_impl.py"
    real_stub_impl.write_text(real_stub.read_text())
    real_stub_impl.chmod(0o755)
    real_stub.write_text(
        "#!/usr/bin/env bash\n"
        'if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then\n'
        f'  echo "$@" >> "{aws_dir}/aws.log"\n'
        "  exit 1\n"
        "fi\n"
        f'exec python3 "{real_stub_impl}" "$@"\n'
    )
    real_stub.chmod(0o755)


def run_ec2_dispatch(env: dict, *, run_provision: bool = True) -> subprocess.CompletedProcess:
    """Run the launcher's real ec2-dispatch block (extracted verbatim)
    against `env`. When run_provision is True (the happy-path shape),
    also sources ec2-provision.sh and calls provision_instance() right
    after the dispatch block, mirroring the "Create" stage that
    immediately follows the preflight per DESIGN §6's stage-mapping
    table — exercising a real `run-instances` call so the ordering
    assertion is non-vacuous even though the launcher itself does not
    yet wire provisioning in (that lands in a later subtask)."""
    dispatch_block = extract_ec2_dispatch_block()
    script_lines = [
        "#!/usr/bin/env bash",
        f"RUNTIME=ec2",
        f"LEERIE_REPO={REPO_ROOT}",
        f". {LOG_SH}",
        # The real launcher's block is one `elif` arm of a larger
        # if/elif/elif/else chain (RUNTIME=fly / RUNTIME=ec2 / local).
        # Wrap it in a throwaway `if false; then :; ` prefix so the
        # extracted text parses standalone while still executing
        # exactly the same statements the launcher runs for RUNTIME=ec2.
        "if false; then\n  :\n" + dispatch_block,
        "fi",
    ]
    if run_provision:
        # Reached only when the dispatch block above did not `exit 1`
        # (i.e. require_aws succeeded) — a failing preflight aborts the
        # whole script before this line via the block's own `exit 1`.
        script_lines.append(f". {EC2_PROVISION_SH}")
        script_lines.append("provision_instance")
    script = "\n".join(script_lines)
    return subprocess.run(
        ["bash", "-c", script],
        env=env,
        capture_output=True,
        text=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_preflight_dispatch_block_extractable():
    """Sanity check on the harness itself: the marker-based extraction
    finds a non-empty block containing the require_aws call."""
    block = extract_ec2_dispatch_block()
    assert "require_aws" in block
    assert 'RUNTIME" = "ec2"' in block


def test_preflight_precedes_run_instances_by_call_index(tmp_path):
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(env)

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
    assert max(identity_calls) < min(run_instances_calls), (
        f"require_aws's sts get-caller-identity must precede any "
        f"ec2 run-instances call; log={log}"
    )
    assert result.returncode == 0, result.stderr

    state = read_state(aws_dir)
    assert len(state["instances"]) == 1
    (_iid, rec), = state["instances"].items()
    assert rec["state"] == "running"


def test_successful_provision_leaves_exactly_one_instance_and_no_orphaned_volume(tmp_path):
    """DESIGN §6 "EBS volume lifecycle" case 1: provision_instance() never
    calls `aws ec2 create-volume` — the root EBS volume is created
    implicitly by run-instances with AWS's own DeleteOnTermination=true
    default. So on a clean happy-path launch the stub's tracked state
    must show exactly one instance (not zero — a no-op regression; not
    two — a double-provision regression) and zero tracked volumes (an
    owning-instance-less volume would be an orphan). Assertions read the
    stub's tracked state, not argv/log line counts, so a call that is
    retried but converges on one tracked instance can never misread as
    two provisions."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(env)
    assert result.returncode == 0, result.stderr

    state = read_state(aws_dir)
    assert len(state["instances"]) == 1, (
        f"expected exactly one tracked instance after a happy-path "
        f"provision; state={state}"
    )
    assert state["volumes"] == {}, (
        f"no volume should be tracked independently of the instance "
        f"that owns it (root EBS is implicit via run-instances, "
        f"DeleteOnTermination=true); state={state}"
    )
    assert leaked_resources(state) == {
        "instances": state["instances"],
        "volumes": {},
    }, (
        "the single tracked instance is expected (still running, not "
        "torn down on this happy-path provision-only run); no volume "
        "should ever appear as a leak"
    )

    log = read_log(aws_dir)
    assert not [l for l in log if l.startswith("ec2 create-volume")], (
        f"provision_instance must never call create-volume directly; log={log}"
    )


def test_failing_preflight_aborts_before_any_ec2_call(tmp_path):
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
    run_instances_calls = [l for l in log if l.startswith("ec2 run-instances")]
    assert not run_instances_calls, (
        f"no ec2 run-instances call should happen when the preflight "
        f"fails; log={log}"
    )

    state = read_state(aws_dir)
    assert state["instances"] == {}, state
    assert state["volumes"] == {}, state
    assert leaked_resources(state) == {"instances": {}, "volumes": {}}


def test_failing_preflight_does_not_source_provisioning_helpers(tmp_path):
    """The dispatch block itself (with the provisioning call omitted)
    must exit non-zero on a failing preflight — pins that require_aws
    gates the branch on its own, independent of whatever the caller
    does afterward."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=False,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "aws sso login" in combined, combined
    state = read_state(aws_dir)
    assert state == {"instances": {}, "volumes": {}}
