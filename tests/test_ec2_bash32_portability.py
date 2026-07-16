"""Guard: the EC2 shell surface must run on bash 3.2 (macOS default).

CI is ubuntu-latest (bash 5.x), so it **structurally cannot** catch a
bash-4-only construct. The EC2 scripts accumulated two of them, and they
only ever surfaced as 33 failing tests on a developer's Mac:

  - `"${arr[@]}"` on an empty array is `unbound variable` under `set -u`
    in bash 3.2; bash 4.4+ expands it to nothing. The repo's own idiom
    for this is `${arr[@]+"${arr[@]}"}` (see the launcher's nerdctl argv
    assembly, which documents the same rationale).
  - `local -n` (nameref) is bash 4.3+; on 3.2 it is a hard
    `local: -n: invalid option`.

These fire in the real EC2 runtime on any macOS host, not just in tests.

The guard is deliberately about the *class*, not the two instances: it
sources each script under a real bash 3.2 with `set -u` and no
LEERIE_AWS_*/AWS_* set (the configuration that leaves every optional-arg
array empty) and asserts the shell does not complain. A new
`"${arr[@]}"` added tomorrow fails here rather than silently breaking
macOS.

Every EC2 launcher arm wired after this guard was first written
(test-001..test-005: `--stop`, `--kill`, `--accept-blocked`, `--resume`,
and the full `RUNTIME=ec2` dispatch) builds its own `LEERIE_AWS_PROFILE`/
`LEERIE_AWS_REGION`-derived optional-arg array
(`_ab_aws_creds_args`/`_stop_aws_creds_args`/`_kill_ec2_creds_args`/
`_leerie_aws_creds_args` in `leerie` itself) and expands it into
`resolve_aws_credentials`. Each of those call sites is new bash on the
same surface this module already guards, so this module extends the
"call the function, don't just source the script" discipline to the
`leerie` launcher binary itself, not only to `scripts/remote/ec2-*.sh`.
(This surfaced a real instance of the class during this extension: all
four call sites used a bare `"${arr[@]}"` rather than the
`${arr[@]+"${arr[@]}"}` guard; fixed alongside these tests.)

Skips cleanly where there is no bash 3.2 to test against (Linux CI), so
it is a macOS-developer guard, never a CI flake.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.ec2_stub import _stub_aws, read_state

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts" / "remote"
LAUNCHER = REPO_ROOT / "leerie"

# The system bash on macOS is 3.2 (Apple has not shipped a newer one
# since the GPLv3 relicense). Homebrew's 5.x lives elsewhere, so this
# path is specifically the old one.
SYSTEM_BASH = Path("/bin/bash")


def _bash_major_minor(bash: Path) -> tuple[int, int] | None:
    try:
        out = subprocess.run([str(bash), "-c", "echo $BASH_VERSINFO"],
                             capture_output=True, text=True, timeout=10)
        major = int(out.stdout.strip())
        out2 = subprocess.run([str(bash), "-c", "echo ${BASH_VERSINFO[1]}"],
                              capture_output=True, text=True, timeout=10)
        return major, int(out2.stdout.strip())
    except (OSError, ValueError, subprocess.SubprocessError):
        return None


def _requires_bash32():
    if not SYSTEM_BASH.exists():
        pytest.skip(f"{SYSTEM_BASH} not present (not a macOS host)")
    ver = _bash_major_minor(SYSTEM_BASH)
    if ver is None:
        pytest.skip(f"could not determine {SYSTEM_BASH} version")
    if ver >= (4, 3):
        pytest.skip(
            f"{SYSTEM_BASH} is {ver[0]}.{ver[1]} — nameref and empty-array "
            f"expansion both work there, so this guard cannot fail. It is "
            f"meaningful only against bash < 4.3 (macOS's 3.2).")


# Every EC2 shell file that the runtime sources. `ec2-lib.sh` is sourced
# by the others, so it is covered transitively as well as directly.
# `ec2-resume-instance.sh` is sourced by the launcher's `--stop`/`--kill`/
# `--accept-blocked`/`--resume` arms (all wired by test-001..test-005);
# `ec2-fetch-branch.sh` and `ec2-seed-auth.sh` are sourced by the full
# `RUNTIME=ec2` dispatch path.
_EC2_SCRIPTS = [
    "ec2-lib.sh",
    "ec2-provision.sh",
    "ec2-resume-instance.sh",
    "ec2-seed-repo.sh",
    "ec2-seed-auth.sh",
    "ec2-fetch-branch.sh",
    "ec2-ssm.sh",
]


@pytest.mark.parametrize("script", _EC2_SCRIPTS)
def test_ec2_script_sources_cleanly_under_bash32(script):
    """Sourcing must not trip `set -u` or use a bash-4-only builtin.

    No LEERIE_AWS_* / AWS_* in the environment: that is the common case
    (let the aws CLI's own credential chain resolve region/profile) and
    the one that leaves every optional-arg array empty.
    """
    _requires_bash32()
    path = SCRIPTS / script
    assert path.exists(), f"{script} is missing"

    result = subprocess.run(
        [str(SYSTEM_BASH), "-c", f"set -u; . '{path}'"],
        capture_output=True, text=True, timeout=60,
        # Scrub the AWS knobs so the arrays stay empty. PATH must stay —
        # the scripts probe for `aws`/`timeout` at source time.
        env={"PATH": "/usr/bin:/bin", "HOME": str(REPO_ROOT)},
        cwd=str(REPO_ROOT),
    )
    combined = result.stdout + result.stderr
    assert "unbound variable" not in combined, (
        f"{script} expands a possibly-empty array without the "
        f"${{arr[@]+\"${{arr[@]}}\"}} guard — breaks under `set -u` on "
        f"bash 3.2 (macOS default):\n{combined}"
    )
    assert "invalid option" not in combined, (
        f"{script} uses a bash-4-only builtin (e.g. `local -n`), which "
        f"does not exist on bash 3.2 (macOS default):\n{combined}"
    )


# Sourcing alone is not enough: an unguarded `"${arr[@]}"` lives *inside*
# a function body, which the shell never evaluates until the function is
# called. The functions below are the ones that build an optional-arg
# array and expand it; each is invoked with a stubbed `aws` so the
# expansion actually executes. (Verified: without this, reverting the
# guard on ec2-lib.sh:58 leaves the source-only test passing.)
#
# (script, function-call, needs-aws-stub)
_EXPANSION_CALLSITES = [
    ("ec2-lib.sh", "require_aws", True),
    ("ec2-provision.sh", "stop_instance", True),
    ("ec2-provision.sh", "terminate_instance", True),
    # resume_instance calls _describe_instance_state first (two guarded
    # array expansions in ec2-resume-instance.sh), which is reached
    # regardless of how the stubbed `aws` responds.
    ("ec2-resume-instance.sh", "resume_instance i-0123456789abcdef0", True),
]


@pytest.mark.parametrize("script,func,needs_aws", _EXPANSION_CALLSITES)
def test_ec2_function_runs_cleanly_under_bash32(script, func, needs_aws,
                                                tmp_path):
    """Call the functions that expand an optional-arg array.

    The array is empty exactly when no region/profile is configured —
    the default — so this is the common path, not an edge case.
    """
    _requires_bash32()
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    if needs_aws:
        stub = bin_dir / "aws"
        stub.write_text("#!/usr/bin/env bash\nexit 0\n")
        stub.chmod(0o755)

    path = SCRIPTS / script
    # Only the function's *stdout* is discarded. Its stderr must reach
    # us — that is where bash reports "unbound variable", and it is the
    # whole assertion. (`>/dev/null 2>&1` here silently defeated this
    # test: it passed with the bug reintroduced.) `|| true` keeps a
    # non-zero rc from a stubbed aws call out of the way; the assertion
    # is on the text, not the rc.
    result = subprocess.run(
        [str(SYSTEM_BASH), "-c",
         f"set -u; . '{path}'; {func} >/dev/null || true"],
        capture_output=True, text=True, timeout=60,
        env={
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "HOME": str(REPO_ROOT),
            # Present-but-empty: stop_instance/terminate_instance read it
            # under `set -u` and return early on empty, which is fine —
            # the aws_args expansion above that is what we're exercising.
            "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
        },
        cwd=str(REPO_ROOT),
    )
    combined = result.stdout + result.stderr
    assert "unbound variable" not in combined, (
        f"{script}::{func} expands a possibly-empty array without the "
        f"${{arr[@]+\"${{arr[@]}}\"}} guard — this fires on bash 3.2 "
        f"(macOS default) whenever no AWS region/profile is set:\n"
        f"{combined}"
    )
    assert "invalid option" not in combined, (
        f"{script}::{func} uses a bash-4-only builtin:\n{combined}"
    )


def test_no_namerefs_in_ec2_scripts():
    """`local -n` / `declare -n` are bash 4.3+.

    A source-level guard as well as the runtime one above: a nameref in a
    function that never runs at source time would slip past the sourcing
    test, and this is cheap and exact.
    """
    for script in _EC2_SCRIPTS:
        text = (SCRIPTS / script).read_text()
        for decl in ("local -n ", "declare -n "):
            assert decl not in text, (
                f"{script} uses `{decl.strip()}` (a bash 4.3+ nameref); "
                f"macOS's /bin/bash is 3.2 and fails with "
                f"'local: -n: invalid option'. Echo the values instead — "
                f"see _aws_region_profile_args in ec2-provision.sh."
            )


def test_no_namerefs_in_launcher():
    """Same nameref ban, extended to the `leerie` launcher binary itself.

    test-001..test-005 wired real bash into `leerie` (not just
    scripts/remote/ec2-*.sh) for the EC2 arms below; a nameref there
    would be just as fatal on macOS.
    """
    text = LAUNCHER.read_text()
    for decl in ("local -n ", "declare -n "):
        assert decl not in text, (
            f"leerie uses `{decl.strip()}` (a bash 4.3+ nameref); "
            f"macOS's /bin/bash is 3.2 and fails with "
            f"'local: -n: invalid option'. Echo the values instead."
        )


# --- Launcher-arm coverage --------------------------------------------------
#
# test-001..test-005 wired real bash directly into the `leerie` launcher
# for the EC2 arms (`--stop`, `--kill`, `--accept-blocked`, and the full
# `RUNTIME=ec2` dispatch), each building its own optional-arg array from
# LEERIE_AWS_PROFILE/LEERIE_AWS_REGION and expanding it into
# resolve_aws_credentials. That is new bash on the same class of surface
# this module already guards for scripts/remote/ec2-*.sh — sourcing
# `leerie` proves nothing (it runs full CLI dispatch on invocation, not
# passive sourcing), so these tests invoke the real launcher binary end
# to end via `bash <launcher> <args>`, swapping in bash 3.2 as the
# interpreter, with LEERIE_AWS_PROFILE/LEERIE_AWS_REGION left unset (the
# default — the condition that leaves each array empty). A minimal
# stubbed `aws` and `tests/ec2_stub.py`'s resource-tracking state machine
# stand in for AWS so each arm can reach its own credential-array
# expansion without a real account.
#
# (Found live during this extension: all four call sites in `leerie`
# used a bare `"${arr[@]}"` instead of the `${arr[@]+"${arr[@]}"}` guard
# — the exact class this module exists to catch. Fixed alongside these
# tests.)

RUN_ID = "ec2-run-bash32"


def _launcher_env(tmp_path: Path, aws_dir: Path) -> tuple[dict, Path]:
    """Minimal env to run a `leerie` EC2 verb end to end against a
    stubbed `aws`. Deliberately omits LEERIE_AWS_PROFILE/LEERIE_AWS_REGION
    — the default config — so every creds-args array stays empty. Sets
    AWS_ACCESS_KEY_ID/AWS_SECRET_ACCESS_KEY/AWS_REGION (the SDK
    credential-chain vars, distinct from LEERIE_AWS_*) so
    resolve_aws_credentials succeeds and each arm runs past the
    credential-resolution step it is here to exercise.
    """
    state_dir = tmp_path / ".leerie" / "myrepo"
    home = tmp_path / "home"
    home.mkdir(parents=True, exist_ok=True)
    env = {
        "PATH": f"{aws_dir}:/usr/bin:/bin",
        "USER_REPO": str(tmp_path),
        "LEERIE_REPO": str(REPO_ROOT),
        "HOME": str(home),
        "LEERIE_STATE_HOST_DIR": str(state_dir),
        "LEERIE_STATE_DIR": str(state_dir),
        "AWS_ACCESS_KEY_ID": "AKIASTUBFIXTURE",
        "AWS_SECRET_ACCESS_KEY": "stubfixturesecret",
        "AWS_REGION": "us-east-1",
    }
    return env, state_dir


def _seed_running_instance(aws_dir: Path) -> str:
    state = read_state(aws_dir)
    iid = "i-" + format(len(state["instances"]), "017x")
    state["instances"][iid] = {"state": "running", "public_ip": "203.0.113.20",
                                "status_ok": True}
    (aws_dir / "state.json").write_text(json.dumps(state))
    return iid


def _write_ec2_sidecar(run_dir: Path, run_id: str, instance_id: str) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "ec2-instance.json").write_text(json.dumps({
        "ec2_instance_id": instance_id,
        "region": "us-east-1",
        "started_at": "2026-07-01T00:00:00+00:00",
        "run_id": run_id,
        "launcher_pid": 12345,
    }))
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": run_id,
        "branch": f"leerie/runs/{run_id}",
        "ec2_instance_id": instance_id,
    }))


def _run_launcher_under_bash32(args: list[str], env: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(SYSTEM_BASH), str(LAUNCHER)] + args,
        env=env, capture_output=True, text=True, timeout=30,
    )


@pytest.mark.parametrize("verb_args", [
    pytest.param(["--stop", RUN_ID], id="stop"),
    pytest.param(["--kill", RUN_ID, "--force"], id="kill"),
    pytest.param(["--accept-blocked", RUN_ID, "feat-001"], id="accept-blocked"),
])
def test_ec2_launcher_verb_runs_cleanly_under_bash32(verb_args, tmp_path):
    """Run each newly wired EC2 launcher verb end to end under bash 3.2.

    LEERIE_AWS_PROFILE/LEERIE_AWS_REGION unset is the common case (let
    the aws CLI's own credential chain resolve region/profile) and the
    one that leaves each verb's creds-args array empty — exactly the
    condition under which the bare `"${arr[@]}"` bug fires.
    """
    _requires_bash32()
    aws_dir = tmp_path / "bin"
    aws_dir.mkdir()
    _stub_aws(aws_dir)
    iid = _seed_running_instance(aws_dir)

    env, state_dir = _launcher_env(tmp_path, aws_dir)
    run_dir = state_dir / "runs" / RUN_ID
    _write_ec2_sidecar(run_dir, RUN_ID, iid)

    result = _run_launcher_under_bash32(verb_args, env)
    combined = result.stdout + result.stderr
    assert "unbound variable" not in combined, (
        f"leerie {' '.join(verb_args)} expands a possibly-empty "
        f"creds-args array without the ${{arr[@]+\"${{arr[@]}}\"}} guard "
        f"— breaks under `set -u` on bash 3.2 (macOS default) whenever "
        f"LEERIE_AWS_PROFILE/LEERIE_AWS_REGION are unset:\n{combined}"
    )
    assert "invalid option" not in combined, (
        f"leerie {' '.join(verb_args)} uses a bash-4-only builtin:\n{combined}"
    )
