"""The remote teardown traps, as provisioning registers them (DESIGN §6
*Finalization parity*): the EXIT trap passes `exit`, so only that pass runs
the launcher's STAGE cleanup; INT/TERM register the bare teardown, because
the launcher can survive that pass and the rest of the run still needs
$STAGE. Drives the real provision_machine / provision_instance and reads the
traps they leave behind."""
from __future__ import annotations

import os

from tests.conftest import run_bash
from tests.ec2_stub import _stub_aws
from tests.test_ec2_provision import EC2_PROVISION_SH, _stub_env
from tests.test_provision_volume import (
    PROVISION_SH, REPO_ROOT, _make_recording_flyctl, _make_run_dir,
)

PRINT_TRAPS = "trap -p EXIT; trap -p INT; trap -p TERM; trap - EXIT INT TERM\n"


def _traps(stdout: str) -> dict[str, str]:
    out = {}
    for line in stdout.splitlines():
        if line.startswith("trap -- "):
            action, sig = line[len("trap -- "):].rsplit(" ", 1)
            out[sig.replace("SIG", "")] = action.strip("'")
    return out


def test_fly_provision_registers_exit_with_the_cleanup_pass(tmp_path):
    stub, _ = _make_recording_flyctl(tmp_path)
    run_id = "feat-trap-001"
    _make_run_dir(tmp_path, run_id)
    env = {
        "LEERIE_REPO": str(REPO_ROOT),
        "PATH": f"{stub.parent}:{os.environ['PATH']}",
        "FLY_IMAGE_TAG": "registry.fly.io/leerie:test",
        "LEERIE_FLY_APP": "leerie", "FLY_APP": "leerie",
        "USER_REPO": str(tmp_path / "user-repo"),
        "LEERIE_RUN_ID": run_id,
        "LEERIE_MACHINE_START_TIMEOUT": "1",
    }
    r = run_bash(f"source {PROVISION_SH}\nwait_for_started() {{ return 0; }}\n"
                 "provision_machine >/dev/null 2>&1\n" + PRINT_TRAPS, env=env)
    assert _traps(r.stdout) == {"EXIT": "decide_teardown exit",
                                "INT": "decide_teardown", "TERM": "decide_teardown"}, r.stdout


def test_ec2_provision_registers_exit_with_the_cleanup_pass(tmp_path):
    aws_dir = tmp_path / "bin"
    _stub_aws(aws_dir)
    r = run_bash(f"source {EC2_PROVISION_SH}; provision_instance >/dev/null 2>&1\n" + PRINT_TRAPS,
                 env=_stub_env(aws_dir))
    assert _traps(r.stdout) == {"EXIT": "decide_ec2_teardown exit",
                                "INT": "decide_ec2_teardown", "TERM": "decide_ec2_teardown"}, r.stdout


def test_resume_paths_register_the_same_split():
    # resume_machine / resume_instance re-arm the trap for a fresh launcher;
    # pinned as text because driving them needs a paused machine fixture.
    for rel, fn in (("scripts/remote/resume-machine.sh", "decide_teardown"),
                    ("scripts/remote/ec2-resume-instance.sh", "decide_ec2_teardown")):
        src = (REPO_ROOT / rel).read_text()
        assert f"trap '{fn} exit' EXIT\n" in src and f"trap '{fn}' INT TERM\n" in src, rel
        assert f"trap '{fn}' EXIT INT TERM" not in src, rel
