"""Tests for the launcher's `RUNTIME=ec2` region-resolution seam.

tests/test_aws_credentials.py already pins resolve_aws_credentials()'s
internal precedence (env vars > named profile > SSO cached token, and
AWS_REGION > AWS_DEFAULT_REGION > profile `region`) standalone, against a
fake $HOME. tests/test_ec2_lib_sh.py already pins require_aws()'s own
profile precedence (LEERIE_AWS_PROFILE > AWS_PROFILE) standalone.
tests/test_ec2_e2e_provision.py already pins the launcher-to-script
*profile* seam (LEERIE_AWS_PROFILE wins over a configured [default],
explicit env creds win over a fully-configured SSO profile, an expired
SSO token aborts with the "aws sso login --profile <p>" hint before
require_aws ever runs) through the real extracted dispatch block.

The one part of the documented seam none of those files exercises is
region: `require_aws`'s `aws sts get-caller-identity` call
(scripts/remote/ec2-lib.sh:46-69) never passes a `--region` flag — the
resolved region reaches that call *only* through the `AWS_REGION`
env var that the dispatch block `eval`s from resolve_aws_credentials's
`export` lines (leerie:6083-6098) before require_aws runs. So the only
way to observe which region "won" is to inspect the effective AWS_REGION
env value visible at sts get-caller-identity call time, not argv. This
module closes that gap, plus the CLAUDE.md-flagged distinction that
leerie's own LEERIE_AWS_REGION knob is a different layer from the AWS
SDK's own AWS_REGION credential-chain var — both must be exercised
through the real launcher block, not reproduced by hand.

Harness: reuses tests/test_ec2_e2e_provision.py's
`extract_ec2_dispatch_block` / `run_ec2_dispatch` / `stub_aws_env`
helpers (same verbatim-extraction discipline — never source `leerie`
directly, since that runs full preflight + CLI dispatch) rather than
reimplementing them, per this repo's sibling-module import convention
(tests/test_ec2_seed_repo_shallow.py imports from its seed-repo sibling
the same way).
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.ec2_stub import read_log
from tests.test_ec2_e2e_provision import (
    REQUIRED_PROVISION_ENV,
    extract_ec2_dispatch_block,
    run_ec2_dispatch,
    stub_aws_env,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


def _stub_aws_recording_region(aws_dir: Path) -> None:
    """A minimal stub (mirroring test_ec2_lib_sh.py's argv-only stub)
    that additionally records the effective AWS_REGION env value seen by
    each invocation — the only way to observe which region `require_aws`'s
    `sts get-caller-identity` call inherited, since require_aws never
    passes --region on argv."""
    aws_dir.mkdir(parents=True, exist_ok=True)
    stub = aws_dir / "aws"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        f'echo "$@|region=${{AWS_REGION:-<unset>}}" >> {aws_dir}/aws.log\n'
        'if [ "$1" = "sts" ] && [ "$2" = "get-caller-identity" ]; then\n'
        "  exit 0\n"
        "fi\n"
        "exit 0\n"
    )
    stub.chmod(0o755)


def _region_seen_for(aws_dir: Path, argv_prefix: str) -> str | None:
    log_path = aws_dir / "aws.log"
    if not log_path.exists():
        return None
    for line in log_path.read_text().splitlines():
        if not line.startswith(argv_prefix):
            continue
        argv, _, region_field = line.rpartition("|region=")
        return region_field
    return None


def _env_credentials_env(aws_dir: Path, home: Path, **extra: str) -> dict:
    """A minimal env: explicit AWS_ACCESS_KEY_ID/SECRET credentials (so
    resolve_aws_credentials's static-credential branch is skipped and no
    ~/.aws fixture is needed), isolated PATH/HOME, with `extra` layered
    on top for the region knobs under test."""
    env = {k: v for k, v in os.environ.items()
            if not k.startswith("AWS_") and k not in ("LEERIE_AWS_PROFILE", "LEERIE_AWS_REGION")}
    env["PATH"] = f"{aws_dir}:{env.get('PATH', '')}"
    env["LEERIE_REPO"] = str(REPO_ROOT)
    env.pop("LEERIE_STATE_DIR", None)
    home.mkdir(parents=True, exist_ok=True)
    env["HOME"] = str(home)
    env["AWS_ACCESS_KEY_ID"] = "AKIASTUBFIXTURE"
    env["AWS_SECRET_ACCESS_KEY"] = "stubfixturesecret"
    env.update(extra)
    return env


# ---------------------------------------------------------------------------
# Region precedence, through the real dispatch block
# ---------------------------------------------------------------------------


def test_leerie_aws_region_wins_over_aws_region(tmp_path):
    """LEERIE_AWS_REGION (leerie's own knob, CLAUDE.md-distinguished from
    the SDK's own AWS_REGION credential-chain var) must win when both are
    set, exercised through the real launcher dispatch block."""
    aws_dir = tmp_path / "bin"
    home = tmp_path / "home"
    _stub_aws_recording_region(aws_dir)
    env = _env_credentials_env(
        aws_dir, home,
        AWS_REGION="us-west-1",
        LEERIE_AWS_REGION="eu-west-1",
    )

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode == 0, result.stderr
    seen = _region_seen_for(aws_dir, "sts get-caller-identity")
    assert seen == "eu-west-1", (
        f"expected LEERIE_AWS_REGION to win over AWS_REGION; "
        f"region seen at sts get-caller-identity call time was {seen!r}"
    )


def test_aws_region_used_when_leerie_aws_region_unset(tmp_path):
    """With no LEERIE_AWS_REGION override, the ambient AWS_REGION must
    still reach require_aws's probe unchanged."""
    aws_dir = tmp_path / "bin"
    home = tmp_path / "home"
    _stub_aws_recording_region(aws_dir)
    env = _env_credentials_env(aws_dir, home, AWS_REGION="ap-southeast-2")

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode == 0, result.stderr
    seen = _region_seen_for(aws_dir, "sts get-caller-identity")
    assert seen == "ap-southeast-2", (
        f"expected the ambient AWS_REGION to reach require_aws unchanged; "
        f"region seen was {seen!r}"
    )


def test_unresolvable_region_aborts_before_require_aws_runs(tmp_path):
    """No AWS_REGION, no AWS_DEFAULT_REGION, and no profile `region` key
    must abort the dispatch block non-zero via resolve_aws_credentials's
    own die-with-hint — before require_aws's sts get-caller-identity call
    ever runs (mirroring test_ec2_e2e_provision.py's expired-SSO-token
    ordering pin, but for the region axis instead of credentials)."""
    aws_dir = tmp_path / "bin"
    home = tmp_path / "home"
    _stub_aws_recording_region(aws_dir)
    env = _env_credentials_env(aws_dir, home)
    env.pop("AWS_REGION", None)
    env.pop("AWS_DEFAULT_REGION", None)

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode != 0, (
        f"an unresolvable region must abort non-zero; "
        f"stdout={result.stdout} stderr={result.stderr}"
    )
    combined = result.stdout + result.stderr
    assert "no AWS region resolved" in combined, combined

    log_path = aws_dir / "aws.log"
    if log_path.exists():
        calls = [l for l in log_path.read_text().splitlines()
                  if l.startswith("sts get-caller-identity")]
        assert not calls, (
            f"require_aws's sts get-caller-identity must not run when "
            f"resolve_aws_credentials already failed closed on region; "
            f"log={calls}"
        )


# ---------------------------------------------------------------------------
# Resolved profile reaches sts get-caller-identity argv, through the real
# dispatch block (test_ec2_lib_sh.py pins require_aws standalone; this
# confirms the launcher's own env-setup actually drives it end to end).
# ---------------------------------------------------------------------------


def test_resolved_profile_appears_in_sts_get_caller_identity_argv(tmp_path):
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(
        aws_dir, identity_succeeds=True,
        extra={**REQUIRED_PROVISION_ENV, "LEERIE_AWS_PROFILE": "ci-deploy"},
    )

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode == 0, result.stderr
    log = read_log(aws_dir)
    identity_calls = [l for l in log if l.startswith("sts get-caller-identity")]
    assert identity_calls, f"expected an sts get-caller-identity call; log={log}"
    assert "--profile ci-deploy" in identity_calls[0], (
        f"expected the resolved LEERIE_AWS_PROFILE to appear as --profile "
        f"in the sts get-caller-identity argv; call={identity_calls[0]!r}"
    )


def test_no_profile_flag_when_neither_var_set(tmp_path):
    """Matches require_aws's own documented behavior (ec2-lib.sh:33-36):
    with neither LEERIE_AWS_PROFILE nor AWS_PROFILE set, no --profile
    flag is passed at all — let the CLI use its own default-profile
    resolution — exercised end to end through the dispatch block."""
    aws_dir = tmp_path / "bin"
    env = stub_aws_env(aws_dir, identity_succeeds=True,
                        extra=REQUIRED_PROVISION_ENV)

    result = run_ec2_dispatch(env, run_provision=False)

    assert result.returncode == 0, result.stderr
    log = read_log(aws_dir)
    identity_calls = [l for l in log if l.startswith("sts get-caller-identity")]
    assert identity_calls, f"expected an sts get-caller-identity call; log={log}"
    assert "--profile" not in identity_calls[0], (
        f"no --profile flag should be passed when neither LEERIE_AWS_PROFILE "
        f"nor AWS_PROFILE is set; call={identity_calls[0]!r}"
    )


# ---------------------------------------------------------------------------
# Harness sanity
# ---------------------------------------------------------------------------


def test_dispatch_block_extraction_reused_from_e2e_provision_module():
    """Sanity check that this module is exercising the same real,
    verbatim-extracted block as test_ec2_e2e_provision.py — not a
    hand-copied reproduction that could silently drift from the launcher."""
    block = extract_ec2_dispatch_block()
    assert "resolve_aws_credentials" in block
    assert "require_aws" in block
