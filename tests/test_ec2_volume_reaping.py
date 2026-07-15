"""Tests pinning EC2 EBS volume safety against Fly's leaked-volume bug class.

Fly volumes leaked in three code paths (fixed in commit 74e0a3a1) because
the platform has no destroy-on-exit hook for a Machine's volume — the
reap had to be leerie's own responsibility, and it was easy to
accidentally nest that reap behind `destroy_machine`'s early return on
an empty machine id (see tests/test_provision_volume.py).

EC2 is architecturally the *opposite* case, and this file exists to pin
that difference rather than reproduce the Fly test shape wholesale.
docs/DESIGN.md's "EC2 runtime lifecycle" -> "EBS volume lifecycle"
explicitly adopts case 1 (root volume only, AWS's own default
`DeleteOnTermination=true`) and states in so many words: "no leerie-side
reap code is needed, and no test_provision_volume.py-style volume-orphan
test surface exists to write, because there is no orphan case to test."
docs/IMPLEMENTATION.md's ec2-provision.sh row confirms the same: "no
destroy_volume() counterpart exists or is needed, unlike Fly."

What actually protects against a leaked EBS volume on this design is
NOT a reap function — it's that `provision_instance()` never passes a
`--block-device-mappings` override to `run-instances`, so the instance
gets a single root volume under AWS's own implicit
`DeleteOnTermination=true` default. An override here (even one that
tries to set `DeleteOnTermination=true` explicitly) would be a
regression risk: it would make the leak-prevention property depend on
someone remembering to set the flag correctly, instead of on doing
nothing at all. So this file pins:

  1. `run-instances` is invoked with no block-device-mapping override
     at all (the actual safeguard for this design).
  2. `terminate_instances` (the sole EC2 reap path — there is no
     separate volume-delete call) is idempotent/safe on an empty
     instance id, the closest analogue of the Fly liveness-independence
     concern now that there's nothing to nest a reap call behind.
  3. A structural regression guard: no destroy_volume/reap_volume
     -shaped function exists in ec2-lib.sh or ec2-provision.sh, so a
     future change can't silently reintroduce Fly's now-inapplicable
     manual-reap pattern.

Uses the stateful `aws` stub (tests/ec2_stub.py) so assertions can check
resource state, not just which commands ran, mirroring
tests/test_ec2_provision.py's harness pattern.

Footgun reminder (CLAUDE.md): the state-dir override consumed by
ec2-provision.sh's sidecar writes is LEERIE_STATE_HOST_DIR, NOT
LEERIE_STATE_DIR -- the latter silently resolves to the real
~/.leerie/... and assertions against it would pass vacuously.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tests.ec2_stub import _stub_aws, leaked_resources, read_log, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
EC2_PROVISION_SH = REPO_ROOT / "scripts" / "remote" / "ec2-provision.sh"
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"

REQUIRED_ENV = {
    "LEERIE_EC2_AMI": "ami-0123456789abcdef0",
    "LEERIE_EC2_INSTANCE_TYPE": "m5.xlarge",
    "LEERIE_EC2_KEY_NAME": "leerie-key",
    "LEERIE_EC2_SECURITY_GROUP": "sg-0123456789abcdef0",
    "LEERIE_EC2_SUBNET_ID": "subnet-0123456789abcdef0",
}


def _run_bash(script: str, env: dict | None = None, cwd: Path | None = None) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.pop("LEERIE_STATE_DIR", None)
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )


def _stub_env(aws_dir: Path, extra: dict | None = None) -> dict:
    env = {
        **REQUIRED_ENV,
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(aws_dir),
    }
    if extra:
        env.update(extra)
    return env


def test_ec2_provision_sh_exists_and_is_executable():
    assert EC2_PROVISION_SH.is_file()
    assert os.access(EC2_PROVISION_SH, os.X_OK)


# --- the actual leak-prevention mechanism: no block-device override --------

def test_run_instances_has_no_block_device_mapping_override(tmp_path):
    """provision_instance's run-instances call must not pass
    --block-device-mappings / --block-device-mapping at all.

    The design's leak-prevention property is that the root volume is
    created entirely under AWS's own implicit DeleteOnTermination=true
    default. An explicit override (even one that tries to set the flag
    to true) reintroduces exactly the class of bug this design avoids:
    a leak now depends on someone getting the override right, rather
    than on there being no override to get wrong.
    """
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}\n"
        "wait_for_instance_ready() { return 0; }\n"
        "provision_instance\n",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    log = read_log(aws_dir)
    run_instances_calls = [line for line in log if "run-instances" in line]
    assert run_instances_calls, f"expected a run-instances call; log={log}"
    for line in run_instances_calls:
        assert "--block-device-mapping" not in line, (
            "run-instances must rely on AWS's implicit root-volume "
            "DeleteOnTermination=true default, not an explicit "
            f"block-device-mapping override.\nargv={line}"
        )


def test_run_instances_output_carries_no_explicit_delete_on_termination_flag(tmp_path):
    """The provisioning script's own source must not set
    DeleteOnTermination anywhere in its run-instances invocation --
    grep-level guard against a future edit smuggling in a block-device
    override that this design deliberately does not need."""
    source = EC2_PROVISION_SH.read_text()
    # Find the run-instances invocation block specifically (not the
    # surrounding prose, which legitimately discusses
    # DeleteOnTermination in comments explaining why no override
    # exists).
    call_match = re.search(
        r"aws ec2 run-instances\b(.*?)(?:\n\s*\n|\Z)", source, re.DOTALL
    )
    assert call_match, "could not locate the run-instances invocation in ec2-provision.sh"
    call_block = call_match.group(1)
    assert "DeleteOnTermination" not in call_block, (
        "the run-instances call itself must not reference "
        "DeleteOnTermination -- that would mean an explicit "
        "block-device-mapping override was added, contradicting the "
        "adopted DESIGN §6 default.\nblock=" + call_block
    )


# --- terminate_instances is the sole reap path; must be liveness-safe -----

def test_terminate_instance_noop_on_empty_instance_id_makes_no_aws_call(tmp_path):
    """terminate_instance must not make any aws call when there is no
    instance id -- mirrors the Fly requirement that a reap-shaped
    function be safe to call unconditionally, without an early return
    hiding real work. On this design there IS no separate volume reap,
    so this is the whole liveness-independence surface: the single EC2
    reap path (terminate_instances) must itself be a true no-op on an
    empty id, not silently fail while looking like a no-op."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)
    env["LEERIE_EC2_INSTANCE_ID"] = ""

    result = _run_bash(
        f"source {EC2_PROVISION_SH}; LEERIE_EC2_INSTANCE_ID=''; terminate_instance",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    log = read_log(aws_dir)
    terminate_calls = [line for line in log if "terminate-instances" in line]
    assert not terminate_calls, f"expected no terminate-instances call on empty id; log={log}"


def test_terminate_instance_reaps_the_instance_and_leaves_no_leak(tmp_path):
    """A full provision -> terminate cycle leaves nothing leaked. Since
    this design has no volumes dict entry to worry about (no
    create-volume call is ever made), leaked_resources' volumes side
    is vacuously empty -- pinned explicitly so a future change that
    DOES start creating a secondary volume (DESIGN §6 case 3, "not
    built now") trips this assertion instead of silently leaking."""
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    env = _stub_env(aws_dir)

    result = _run_bash(
        f"source {EC2_PROVISION_SH}\n"
        "wait_for_instance_ready() { return 0; }\n"
        "provision_instance\n"
        "trap - EXIT INT TERM\n"  # avoid a double-teardown on shell exit
        "terminate_instance\n",
        env=env,
    )

    assert result.returncode == 0, result.stderr
    state = read_state(aws_dir)
    assert state["instances"], "expected an instance to have been created"
    leaked = leaked_resources(state)
    assert leaked == {"instances": {}, "volumes": {}}, (
        f"expected clean teardown with no leaked instances or volumes; state={state}"
    )
    # No create-volume call should ever have happened -- confirms the
    # test above isn't passing vacuously because no volume API was
    # exercised at all.
    log = read_log(aws_dir)
    assert not [line for line in log if "create-volume" in line], log


# --- structural regression guard: no Fly-style reap function exists -------

def test_no_destroy_volume_style_function_exists():
    """Regression guard against reintroducing Fly's manual-reap
    pattern. If a future change adds a secondary (non-root) EBS volume
    (DESIGN §6 case 3), it must also add this discipline back
    deliberately -- this test should be updated at that point, not
    silently defeated by a function slipping in unnoticed."""
    combined_source = EC2_LIB_SH.read_text() + "\n" + EC2_PROVISION_SH.read_text()
    forbidden = re.compile(r"^\s*(destroy_volume|reap_volume)\s*\(\)\s*\{", re.MULTILINE)
    match = forbidden.search(combined_source)
    assert match is None, (
        "found a destroy_volume/reap_volume-shaped function, but DESIGN §6 "
        "EBS volume lifecycle case 1 (the adopted default) states no such "
        "function is needed for EC2's root-only volume shape. If a "
        "secondary volume was intentionally added (case 3), update this "
        f"test's scope alongside it.\nmatch={match}"
    )
