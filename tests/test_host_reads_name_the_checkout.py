"""DESIGN §6 *The host never runs a program the checkout chose*, part 1:
leaving the checkout must not change what any host command means (review
round 27, exec M1, L1, L3).

The launcher now changes to `/` before it runs anything, so every value the
host used to take from its working directory has to name the checkout
instead:
- M1: `seed_auth` / `ec2_seed_auth` read the git identity with a bare
  `git config`, which from `/` no longer sees a repository-local identity.
  The run shipped the global identity, or died when the identity existed only
  in the repository.
- L1: `log()` labels a line with `USER_REPO` or the cwd's name; the host
  seams that load the orchestrator had neither, so every line read `[?]`.
- L3: dash's `pwd -P` in a deleted directory prints nothing and exits 0, so
  the prologue's guard never fired and `USER_REPO` was empty.

Each fix is tested by running the shipped code from `/` with the value only
in the checkout, plus a rule over the class: no host file falls back to the
working directory for the checkout, and every host seam that loads the
orchestrator is given `USER_REPO`.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from tests.conftest import run_bash
from tests.test_host_python_is_isolated import _host_files, _logical_lines

REPO = Path(__file__).resolve().parent.parent
LAUNCHER = REPO / "leerie"
SEED_AUTH = REPO / "scripts" / "remote" / "seed-auth.sh"
EC2_SEED_AUTH = REPO / "scripts" / "remote" / "ec2-seed-auth.sh"
EC2_LIB = REPO / "scripts" / "remote" / "ec2-lib.sh"

_ID = ("R27 Repo-Local Identity", "repo-local@r27.invalid")


def _repo_with_local_identity(tmp_path: Path) -> tuple[Path, dict]:
    """A checkout whose identity is set only in its own config, and an
    environment whose global and system config hold none."""
    co = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(co)], check=True, timeout=60)
    subprocess.run(["git", "-C", str(co), "config", "user.name", _ID[0]],
                   check=True, timeout=60)
    subprocess.run(["git", "-C", str(co), "config", "user.email", _ID[1]],
                   check=True, timeout=60)
    empty = tmp_path / "empty-global"
    empty.write_text("")
    return co, {"GIT_CONFIG_GLOBAL": str(empty), "GIT_CONFIG_NOSYSTEM": "1",
                "HOME": str(tmp_path)}


def _stage(tmp_path: Path) -> Path:
    stage = tmp_path / "stage"
    (stage / ".claude").mkdir(parents=True)
    (stage / ".claude" / ".credentials.json").write_text(
        '{"claudeAiOauth":{"accessToken":"tok"}}')
    return stage


def test_fly_seed_auth_ships_the_checkouts_identity_from_root(
        tmp_path: Path) -> None:
    co, genv = _repo_with_local_identity(tmp_path)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    # Builtins and an absolute path only: drains the transport's stdin.
    (stubs / "flyctl").write_text("#!/bin/sh\nexec /bin/cat >/dev/null\n")
    (stubs / "flyctl").chmod(0o755)
    r = subprocess.run(
        ["/bin/bash", "-c", f"source {SEED_AUTH}; seed_auth"], cwd="/",
        capture_output=True, text=True, timeout=120,
        env={**genv, "PATH": f"{stubs}:/usr/bin:/bin",
             "LEERIE_MACHINE_ID": "m-1", "USER_REPO": str(co),
             "STAGE": str(_stage(tmp_path)), "CLAUDE_CODE_OAUTH_TOKEN": ""})
    assert r.returncode == 0, r.stderr[-1500:]
    assert f"git identity set ({_ID[0]} <{_ID[1]}>)" in r.stderr, \
        r.stderr[-1500:]


def test_ec2_seed_auth_ships_the_checkouts_identity_from_root(
        tmp_path: Path) -> None:
    from tests.stub_helpers import _make_stub_timeout
    from tests.test_ec2_seed_auth import _make_stub_aws, _make_stub_ssh
    co, genv = _repo_with_local_identity(tmp_path)
    stubs = tmp_path / "stubs"
    stubs.mkdir()
    dest = tmp_path / "instance"
    dest.mkdir()
    log = tmp_path / "exec.log"
    _make_stub_aws(stubs / "aws", log, dest)
    _make_stub_ssh(stubs / "ssh", log, dest)
    _make_stub_timeout(stubs)
    r = subprocess.run(
        ["/bin/bash", "-c",
         f"source {EC2_LIB}; source {EC2_SEED_AUTH}; ec2_seed_auth"],
        cwd="/", capture_output=True, text=True, timeout=120,
        env={**genv, "PATH": f"{stubs}:/usr/bin:/bin",
             "LEERIE_EC2_INSTANCE_ID": "i-0123456789abcdef0",
             "LEERIE_EC2_SSH_TARGET": "ec2-user@1.2.3.4",
             "LEERIE_AWS_REGION": "us-east-1",
             "USER_REPO": str(co), "STAGE": str(_stage(tmp_path)),
             "CLAUDE_CODE_OAUTH_TOKEN": ""})
    assert f"({_ID[0]} <{_ID[1]}>)" in r.stderr, r.stderr[-1500:]


def test_seed_auth_refuses_without_the_checkout() -> None:
    """No `USER_REPO`: refused, never read from the working directory."""
    for script, fn in ((SEED_AUTH, "seed_auth"),
                       (EC2_SEED_AUTH, "ec2_seed_auth")):
        text = script.read_text()
        body = text[text.index(f"{fn}() {{"):]
        assert 'git -C "$USER_REPO" config user.name' in body
        assert 'git -C "$USER_REPO" config user.email' in body
        assert re.search(r'if \[ -z "\$\{USER_REPO:-\}" \]; then\n\s*'
                         r'remote_log "' + fn + r': USER_REPO is not set',
                         body)


def _cwd_reads(paths: list[Path]) -> list[str]:
    bad = []
    for p in paths:
        for n, s in _logical_lines(p):
            if p == LAUNCHER and s.startswith("LEERIE_LAUNCHER_CWD=$(pwd -P)"):
                continue
            if re.search(r"\$\(pwd\b|\$PWD\b|\$\{PWD\b|`pwd`|"
                         r"USER_REPO:-[$`]", s):
                bad.append(f"{p.name}:{n}: {s}")
            if re.search(r'"\$\(git config ', s):
                bad.append(f"{p.name}:{n}: {s}")
    return bad


def test_no_host_file_takes_the_checkout_from_its_cwd() -> None:
    """The class: no `${USER_REPO:-$PWD}`-style fallback, no `$(pwd)` or
    `$PWD` outside the launcher's prologue, and no bare `git config` read
    (a local git command names the checkout with `-C`)."""
    assert _cwd_reads(_host_files()) == []


def test_the_cwd_rule_catches_a_fallback(tmp_path: Path) -> None:
    f = tmp_path / "x.sh"
    f.write_text('USER_REPO="${USER_REPO:-$PWD}"\n'
                 'n="$(git config user.name)"\n'
                 '# a comment naming $(pwd)\n')
    assert len(_cwd_reads([f])) == 2


def _seams() -> list[str]:
    """Every host `python3` start that loads the orchestrator."""
    out = []
    for p in _host_files():
        for n, s in _logical_lines(p):
            # A python3 inside the image (`/opt/leerie-image`) runs in a
            # container, not on the host.
            if re.search(r"(?<![\w/.-])python3 -I", s) and (
                    "orchestrator/leerie.py" in s or "_rebaser_py" in s) \
                    and "/opt/leerie-image" not in s:
                out.append(f"{p.name}:{n}: {s}")
    return out


def test_every_host_seam_is_told_the_checkout() -> None:
    seams = _seams()
    # --help, the read-only verbs, recapture, the resume pick, the capture
    # apply and the rebaser.
    assert len(seams) == 6, seams
    bad = [s for s in seams if not re.search(r'USER_REPO="\$\w+"', s)]
    assert not bad, bad


def test_a_seam_names_the_checkout_in_its_log_lines(tmp_path: Path) -> None:
    """The seam's own `log()`, started from `/` as the launcher starts it."""
    co = tmp_path / "my-checkout"
    co.mkdir()
    prog = ("import importlib.util, sys\n"
            "s = importlib.util.spec_from_file_location('o', sys.argv[1])\n"
            "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
            "m.log('hello')\n")
    run = lambda env: subprocess.run(  # noqa: E731
        ["/usr/bin/python3", "-I", "-c", prog,
         str(REPO / "orchestrator" / "leerie.py")], cwd="/", env=env,
        capture_output=True, text=True, timeout=120).stdout
    base = {"PATH": os.environ["PATH"], "HOME": str(tmp_path)}
    assert "[leerie] [my-checkout] hello" in run({**base,
                                                  "USER_REPO": str(co)})
    # Control: without it the label is the cwd's, which from `/` is none.
    assert "[leerie] [?] hello" in run(base)


def test_the_launcher_refuses_a_deleted_cwd(tmp_path: Path) -> None:
    """dash's `pwd -P` in a deleted directory exits 0 and prints nothing."""
    gone = tmp_path / "gone"
    gone.mkdir()
    r = run_bash(
        f'cd "{gone}" && rmdir "{gone}" && exec /bin/sh "{LAUNCHER}" list',
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path),
             "LEERIE_STATE_DIR": str(tmp_path / "state")})
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "cannot resolve the current directory" in r.stderr
