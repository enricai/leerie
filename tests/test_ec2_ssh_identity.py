"""ec2_ssh_identity_args / _ec2_ssh_identity_string emit -i only for a managed key."""
import subprocess
from pathlib import Path

LIB = Path(__file__).resolve().parent.parent / "scripts" / "remote" / "ec2-lib.sh"


def _run(fn: str, key: str | None) -> str:
    env = {"PATH": "/usr/bin:/bin"}
    if key is not None:
        env["LEERIE_EC2_SSH_KEY_FILE"] = key
    return subprocess.run(
        ["bash", "-c", f'source "{LIB}"; {fn}'],
        capture_output=True, text=True, env=env,
    ).stdout


def test_set_key_emits_identity(tmp_path):
    k = tmp_path / "k"
    k.write_text("x")
    assert _run("ec2_ssh_identity_args", str(k)).split("\n")[:4] == [
        "-i", str(k), "-o", "IdentitiesOnly=yes"]
    assert f"-i '{k}' -o IdentitiesOnly=yes" == _run("_ec2_ssh_identity_string", str(k))


def test_unset_or_missing_key_emits_nothing(tmp_path):
    for key in (None, "", str(tmp_path / "absent")):
        assert _run("ec2_ssh_identity_args", key) == ""
        assert _run("_ec2_ssh_identity_string", key) == ""
