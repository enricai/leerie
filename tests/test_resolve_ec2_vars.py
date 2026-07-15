"""Tests for the launcher's EC2 instance-shape var resolution.

Brings the five `RunInstances`-shape params (AMI, instance type, key name,
security group, subnet id) up to the same CLI > env > `leerie.toml` >
(no default) precedence every other leerie knob already has (`FLY_VM_DISK_GB`,
the shallow-seed knobs). Resolution mirrors `_resolve_seed_knob` exactly,
minus a default tier — there is no sensible AMI/instance-type/key-pair/
security-group/subnet leerie can pick on the operator's behalf
(docs/IMPLEMENTATION.md "EC2 instance-lifecycle vars"), so an all-unset var
falls through to `scripts/remote/ec2-lib.sh`'s `_resolve_ec2_var`, which
still raises its existing actionable "not set — required for --runtime ec2"
error rather than a bare `${VAR:?}`.

Two things are under test, mirroring the two existing precedent files:

  - The launcher's CLI/env/toml resolution ladder (reproduced here, like
    `tests/test_launcher_seed_depth_resolution.py` reproduces
    `_resolve_seed_knob`, plus a coupling test pinning the real launcher
    source so a refactor that changes parsing surfaces a failure).
  - `ec2-lib.sh`'s `_resolve_ec2_var` required-var-read contract, sourced
    directly (mirroring `tests/test_ec2_lib_sh.py`'s `source <path>` idiom)
    to prove the launcher's resolved (or unresolved) env var flows through
    correctly into the existing required-var error path.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
EC2_LIB_SH = REPO_ROOT / "scripts" / "remote" / "ec2-lib.sh"
LOG_SH = REPO_ROOT / "scripts" / "remote" / "_log.sh"

# One (cli-flag, env-var, toml-key) triple per RunInstances param.
_EC2_VARS = [
    ("--ec2-ami", "LEERIE_EC2_AMI", "ec2_ami"),
    ("--ec2-instance-type", "LEERIE_EC2_INSTANCE_TYPE", "ec2_instance_type"),
    ("--ec2-key-name", "LEERIE_EC2_KEY_NAME", "ec2_key_name"),
    ("--ec2-security-group", "LEERIE_EC2_SECURITY_GROUP", "ec2_security_group"),
    ("--ec2-subnet-id", "LEERIE_EC2_SUBNET_ID", "ec2_subnet_id"),
]

# The launcher's EC2-knob resolution helper + CLI-scan, reproduced so the
# tests exercise the launcher's logic and a refactor that changes parsing
# makes the coupling test (test_block_present_in_launcher) fail.
_LAUNCHER_EC2_BLOCK = r"""
_resolve_ec2_knob() {
  local _cli="$1" _envname="$2" _tomlkey="$3" _envval _tomlval
  if [ -n "$_cli" ]; then printf '%s' "$_cli"; return; fi
  eval "_envval=\"\${$_envname:-}\""
  if [ -n "$_envval" ]; then printf '%s' "$_envval"; return; fi
  if [ -f "$USER_REPO/leerie.toml" ]; then
    _tomlval="$( { grep -E "^[[:space:]]*${_tomlkey}[[:space:]]*=" \
                        "$USER_REPO/leerie.toml" 2>/dev/null \
                      | head -1 \
                      | sed -E "s/^[[:space:]]*${_tomlkey}[[:space:]]*=[[:space:]]*//; s/[[:space:]]*\$//; s/^\"//; s/\"\$//" ; } || true)"
    if [ -n "$_tomlval" ]; then printf '%s' "$_tomlval"; return; fi
  fi
}
_cli_ec2_ami=""
_cli_ec2_instance_type=""
_cli_ec2_key_name=""
_cli_ec2_security_group=""
_cli_ec2_subnet_id=""
_prev_ec2_arg=""
for arg in "$@"; do
  if [ -n "$_prev_ec2_arg" ]; then
    case "$_prev_ec2_arg" in
      ami)             _cli_ec2_ami="$arg" ;;
      instance-type)   _cli_ec2_instance_type="$arg" ;;
      key-name)        _cli_ec2_key_name="$arg" ;;
      security-group)  _cli_ec2_security_group="$arg" ;;
      subnet-id)       _cli_ec2_subnet_id="$arg" ;;
    esac
    _prev_ec2_arg=""
    continue
  fi
  case "$arg" in
    --ec2-ami=*)             _cli_ec2_ami="${arg#--ec2-ami=}" ;;
    --ec2-ami)               _prev_ec2_arg="ami" ;;
    --ec2-instance-type=*)   _cli_ec2_instance_type="${arg#--ec2-instance-type=}" ;;
    --ec2-instance-type)     _prev_ec2_arg="instance-type" ;;
    --ec2-key-name=*)        _cli_ec2_key_name="${arg#--ec2-key-name=}" ;;
    --ec2-key-name)          _prev_ec2_arg="key-name" ;;
    --ec2-security-group=*)  _cli_ec2_security_group="${arg#--ec2-security-group=}" ;;
    --ec2-security-group)    _prev_ec2_arg="security-group" ;;
    --ec2-subnet-id=*)       _cli_ec2_subnet_id="${arg#--ec2-subnet-id=}" ;;
    --ec2-subnet-id)         _prev_ec2_arg="subnet-id" ;;
  esac
done
LEERIE_EC2_AMI="$(_resolve_ec2_knob "$_cli_ec2_ami" LEERIE_EC2_AMI ec2_ami)"
LEERIE_EC2_INSTANCE_TYPE="$(_resolve_ec2_knob "$_cli_ec2_instance_type" LEERIE_EC2_INSTANCE_TYPE ec2_instance_type)"
LEERIE_EC2_KEY_NAME="$(_resolve_ec2_knob "$_cli_ec2_key_name" LEERIE_EC2_KEY_NAME ec2_key_name)"
LEERIE_EC2_SECURITY_GROUP="$(_resolve_ec2_knob "$_cli_ec2_security_group" LEERIE_EC2_SECURITY_GROUP ec2_security_group)"
LEERIE_EC2_SUBNET_ID="$(_resolve_ec2_knob "$_cli_ec2_subnet_id" LEERIE_EC2_SUBNET_ID ec2_subnet_id)"
"""

_ALL_ENV_VARS = [envname for _, envname, _ in _EC2_VARS]


def _run(user_repo: Path, *args: str, env_extra: dict | None = None,
          ) -> subprocess.CompletedProcess:
    """Source the launcher's EC2-knob block in a subshell with the given
    CLI args and USER_REPO. Prints all five resolved values on success."""
    script = (
        "set -euo pipefail\n"
        f"USER_REPO={user_repo!s}\n"
        f"{_LAUNCHER_EC2_BLOCK}\n"
        'printf "%s|%s|%s|%s|%s\\n" '
        '"$LEERIE_EC2_AMI" "$LEERIE_EC2_INSTANCE_TYPE" "$LEERIE_EC2_KEY_NAME" '
        '"$LEERIE_EC2_SECURITY_GROUP" "$LEERIE_EC2_SUBNET_ID"\n'
    )
    env = {**os.environ}
    for name in _ALL_ENV_VARS:
        env.pop(name, None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        ["bash", "-c", script, "bash", *args],
        capture_output=True, text=True, env=env,
    )


def _resolve(user_repo: Path, *args: str, env_extra: dict | None = None,
             ) -> tuple[str, str, str, str, str]:
    result = _run(user_repo, *args, env_extra=env_extra)
    assert result.returncode == 0, f"unexpected failure: {result.stderr}"
    return tuple(result.stdout.strip().split("|"))  # type: ignore[return-value]


def test_all_unset_resolves_empty(tmp_path):
    """No CLI, no env, no toml → every var resolves empty (no default —
    ec2-lib.sh's _resolve_ec2_var is what turns this into the required-var
    error, not this ladder)."""
    assert _resolve(tmp_path) == ("", "", "", "", "")


@pytest.mark.parametrize("cli_flag,env_name,toml_key", _EC2_VARS)
def test_cli_wins_over_env_and_toml(tmp_path, cli_flag, env_name, toml_key):
    (tmp_path / "leerie.toml").write_text(f'{toml_key} = "from-toml"\n')
    values = _resolve(
        tmp_path, cli_flag, "from-cli", env_extra={env_name: "from-env"},
    )
    idx = [v[1] for v in _EC2_VARS].index(env_name)
    assert values[idx] == "from-cli"


@pytest.mark.parametrize("cli_flag,env_name,toml_key", _EC2_VARS)
def test_cli_equals_form(tmp_path, cli_flag, env_name, toml_key):
    values = _resolve(tmp_path, f"{cli_flag}=from-cli-eq")
    idx = [v[1] for v in _EC2_VARS].index(env_name)
    assert values[idx] == "from-cli-eq"


@pytest.mark.parametrize("cli_flag,env_name,toml_key", _EC2_VARS)
def test_env_wins_over_toml(tmp_path, cli_flag, env_name, toml_key):
    (tmp_path / "leerie.toml").write_text(f'{toml_key} = "from-toml"\n')
    values = _resolve(tmp_path, env_extra={env_name: "from-env"})
    idx = [v[1] for v in _EC2_VARS].index(env_name)
    assert values[idx] == "from-env"


@pytest.mark.parametrize("cli_flag,env_name,toml_key", _EC2_VARS)
def test_toml_honored_when_cli_and_env_unset(tmp_path, cli_flag, env_name, toml_key):
    (tmp_path / "leerie.toml").write_text(f'{toml_key} = "from-toml"\n')
    values = _resolve(tmp_path)
    idx = [v[1] for v in _EC2_VARS].index(env_name)
    assert values[idx] == "from-toml"


def test_per_var_isolation_cli(tmp_path):
    """Overriding one var's CLI flag must not bleed into the others."""
    values = _resolve(tmp_path, "--ec2-ami", "ami-only")
    assert values == ("ami-only", "", "", "", "")


def test_per_var_isolation_env(tmp_path):
    """Overriding one var's env var must not bleed into the others."""
    values = _resolve(tmp_path, env_extra={"LEERIE_EC2_KEY_NAME": "key-only"})
    assert values == ("", "", "key-only", "", "")


def test_per_var_isolation_toml(tmp_path):
    """Overriding one var's toml key must not bleed into the others."""
    (tmp_path / "leerie.toml").write_text('ec2_subnet_id = "subnet-only"\n')
    values = _resolve(tmp_path)
    assert values == ("", "", "", "", "subnet-only")


def test_all_five_resolved_independently_at_once(tmp_path):
    """Each var can be set through a different tier simultaneously without
    cross-contamination."""
    (tmp_path / "leerie.toml").write_text(
        'ec2_security_group = "sg-toml"\nec2_subnet_id = "subnet-toml"\n'
    )
    values = _resolve(
        tmp_path,
        "--ec2-ami", "ami-cli",
        "--ec2-key-name=key-cli-eq",
        env_extra={"LEERIE_EC2_INSTANCE_TYPE": "t3.env"},
    )
    assert values == ("ami-cli", "t3.env", "key-cli-eq", "sg-toml", "subnet-toml")


def test_block_present_in_launcher():
    """Coupling test: the reproduced block must stay in lockstep with the
    launcher. Pin the key helper name, flag names, and toml keys so a
    drift surfaces here."""
    src = LAUNCHER.read_text()
    assert "_resolve_ec2_knob()" in src, (
        "Launcher's _resolve_ec2_knob helper is missing or renamed — "
        "update this test in lockstep."
    )
    for cli_flag, env_name, toml_key in _EC2_VARS:
        assert cli_flag in src, f"Launcher must scan the {cli_flag} CLI flag."
        assert f'{env_name} {toml_key})"' in src or f"{env_name} {toml_key})" in src, (
            f"Launcher's {env_name} resolution (toml key '{toml_key}') has drifted."
        )
    # The five flags must be stripped from REWRITTEN_ARGS (launcher-only),
    # so they never reach the orchestrator's strict parse_args().
    assert (
        "--ec2-ami|--ec2-instance-type|--ec2-key-name|--ec2-security-group|--ec2-subnet-id)"
        in src
    ), (
        "Launcher must strip the five --ec2-* flags from REWRITTEN_ARGS "
        "(they are host-only; the orchestrator uses strict parse_args and "
        "would error 'unrecognized arguments')."
    )


def test_vars_still_denylisted_from_container_env_forwarding():
    """The five vars must stay on the env-forwarding deny-list — they are
    launcher-side-only like LEERIE_FLY_APP, and a coupling guard in
    tests/test_launcher_env_forwarding.py fails if a var the orchestrator
    reads is deny-listed, so this pins the inverse: these vars are NOT
    orchestrator-read and must remain denied."""
    src = LAUNCHER.read_text()
    denylist_match = re.search(
        r'_leerie_env_denylist="(.*?)"\n', src, re.DOTALL,
    )
    assert denylist_match, "Could not locate _leerie_env_denylist assignment."
    denylist_body = denylist_match.group(1)
    for _, env_name, _ in _EC2_VARS:
        assert env_name in denylist_body, (
            f"{env_name} must remain in the env-forwarding deny-list."
        )


# --- ec2-lib.sh's _resolve_ec2_var: the required-var-read tail of the
# ladder, sourced directly (mirroring tests/test_ec2_lib_sh.py). ----------

def _run_resolve_ami(env: dict) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    base_env.pop("LEERIE_EC2_AMI", None)
    base_env.update(env)
    script = f"source {LOG_SH}; source {EC2_LIB_SH}; resolve_ami"
    return subprocess.run(
        ["bash", "-c", script], env=base_env, capture_output=True, text=True,
    )


def test_resolve_ec2_var_prints_value_when_set():
    result = _run_resolve_ami({"LEERIE_EC2_AMI": "ami-0123456789abcdef0"})
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ami-0123456789abcdef0"


def test_resolve_ec2_var_actionable_error_and_rc1_when_unset():
    """All-unset still produces the existing actionable error naming the
    var and returns 1 — never a bare ${VAR:?} that kills the sourcing
    shell under set -u."""
    result = _run_resolve_ami({})
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "LEERIE_EC2_AMI" in combined
    assert "not set" in combined
    assert "required for --runtime ec2" in combined
