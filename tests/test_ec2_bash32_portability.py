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

Skips cleanly where there is no bash 3.2 to test against (Linux CI), so
it is a macOS-developer guard, never a CI flake.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts" / "remote"

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
# by the other two, so it is covered transitively as well as directly.
_EC2_SCRIPTS = [
    "ec2-lib.sh",
    "ec2-provision.sh",
    "ec2-seed-repo.sh",
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
