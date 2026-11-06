"""No test installs a runtime over the network, and no test prompts.

`require_flyctl` (scripts/remote/lib.sh) offers to install flyctl when it is
absent, and the offer defaults to YES on an empty read: `read -r ans` followed
by `case "${ans:-Y}"`. `tests/conftest.py`'s `run_bash` passes `input=None`,
so the child inherits pytest's stdin, which is at EOF -- the read returns
empty and the installing arm runs. That is how
`tests/test_seed_repo_sh.py::test_seed_repo_fails_when_flyctl_missing`, a test
whose whole premise is that flyctl is MISSING, came to run
`curl -L https://fly.io/install.sh | sh` on CI: flyctl was then present,
`_seed_repo_preflight` passed, control reached `require_fly_ssh`, and the
private ssh-agent it spawns daemonized and outlived the session, failing the
run at `pytest_sessionfinish` with `14188 passed` and exit 1.

Fixing that one test did not fix the class: any test reaching `require_flyctl`
without the knobs repeats it. conftest now sets `LEERIE_NONINTERACTIVE=1` and
`LEERIE_NO_RUNTIME_INSTALL=1` for the whole session, and this file is what
keeps them honest.

Substance over structure (CLAUDE.md): asserting the variables are set would
pass on a value the product does not read, and asserting lib.sh *contains* a
guard says nothing about whether it is reached. So the decisive test EXECUTES
`require_flyctl` with flyctl genuinely absent and pins that it reports the
hint and returns non-zero WITHOUT running an installer -- with a `curl` stub
on PATH that fails loudly if anything invokes it, so a regression shows up as
a named failure rather than as a network call nobody notices.
"""
from __future__ import annotations

import os
from pathlib import Path

from tests.conftest import REPO_ROOT, run_bash

LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"


def _no_network_stubs(d: Path) -> None:
    """`curl`/`wget`/`sh` tripwires: invoking one is the defect, so each
    records the call and fails. Builtins only -- a stub never runs a program
    found on PATH."""
    for name in ("curl", "wget"):
        s = d / name
        s.write_text(
            "#!/bin/sh\n"
            f'echo "TRIPWIRE: {name} $*" >> "$TRIPWIRE_LOG"\n'
            "exit 97\n")
        s.chmod(0o755)


def test_require_flyctl_takes_the_hint_arm_with_the_session_defaults(tmp_path):
    """The real guarantee: flyctl absent -> hint + non-zero, no installer.

    Runs `require_flyctl` with a PATH that has no flyctl and tripwire
    `curl`/`wget`, inheriting the session's env exactly as a test would.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    _no_network_stubs(bin_dir)
    log = tmp_path / "tripwire.log"

    result = run_bash(
        f"source {LIB_SH}; require_flyctl; echo \"RC=$?\"",
        env={
            # No flyctl on PATH; the tripwires come first.
            "PATH": f"{bin_dir}:/usr/bin:/bin",
            "TRIPWIRE_LOG": str(log),
        },
    )

    out = result.stdout + result.stderr
    assert "RC=1" in out, out
    # The hint arm's own words (lib.sh:482-485), not merely a non-zero rc.
    assert "flyctl not found on PATH" in out, out
    # The decisive assertion: nothing tried to fetch an installer.
    assert not log.exists(), (
        f"a network fetch was attempted: {log.read_text()!r}. "
        "require_flyctl reached its installing arm despite the session "
        "defaults in tests/conftest.py.")
    # And the installer's own command never appears.
    assert "fly.io/install.sh" not in out, out


def test_the_session_arms_both_gates_not_just_one():
    """Both knobs, because lib.sh gates on them SEPARATELY.

    `LEERIE_NO_RUNTIME_INSTALL` only skips the install (lib.sh:481);
    `LEERIE_NONINTERACTIVE` also declines the `flyctl auth login` prompt
    (:503). A session with just the first still prompts. Parametrising would
    hide that, so both are named.
    """
    assert os.environ.get("LEERIE_NONINTERACTIVE") == "1"
    assert os.environ.get("LEERIE_NO_RUNTIME_INSTALL") == "1"


def test_a_child_bash_inherits_them():
    """The knobs must reach the SUBPROCESS, which is where lib.sh runs.

    `run_bash` merges onto `os.environ`, so this is the property that makes
    the defaults load-bearing rather than decorative.
    """
    result = run_bash(
        'echo "NI=${LEERIE_NONINTERACTIVE:-unset} '
        'NRI=${LEERIE_NO_RUNTIME_INSTALL:-unset}"')
    assert "NI=1 NRI=1" in result.stdout, result.stdout


def test_no_test_asserts_the_prompting_arm():
    """The session default is safe only while nothing depends on prompting.

    If a future test needs the install/prompt arm it must set the knob in its
    own subprocess env rather than rely on the session's -- and this guard is
    where that shows up, naming the file so the reader knows what to do.
    """
    offenders = []
    for path in sorted(Path(REPO_ROOT / "tests").glob("test_*.py")):
        text = path.read_text()
        if "Run it now" in text and path.name != Path(__file__).name:
            # Prose mentioning the prompt is fine; an assertion on it is not.
            for line in text.splitlines():
                if "Run it now" in line and "assert" in line:
                    offenders.append(f"{path.name}: {line.strip()}")
    assert not offenders, (
        "these tests assert the interactive install prompt, which the "
        "session-wide LEERIE_NONINTERACTIVE=1 in tests/conftest.py now "
        f"suppresses: {offenders}")
