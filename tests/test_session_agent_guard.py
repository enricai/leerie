"""The session's ssh-agent guard sees the agent the product starts.

`tests/conftest.py` gives each pytest session its own HOME and fails a
session that leaves leerie's private ssh-agent running. The product puts that
agent under `${XDG_CACHE_HOME:-$HOME/.cache}/leerie/agent`
(`_leerie_fly_agent_ensure`, scripts/remote/lib.sh), so with an
`XDG_CACHE_HOME` inherited from the developer's or CI's environment the agent
lived outside the session HOME: the guard passed a leaking session and the
agent survived it. Here a child session, given an `XDG_CACHE_HOME` outside
its HOME, starts the agent and does not stop it; the guard must fail it, and
no agent may survive.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT, ssh_agents_under, stop_ssh_agents_under

LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"

_INNER = f'''
import os
import subprocess


def test_starts_leeries_agent():
    r = subprocess.run(
        ["bash", "-c",
         "source {LIB_SH}; _leerie_fly_agent_ensure; echo \\"$SSH_AUTH_SOCK\\""],
        capture_output=True, text=True,
        env={{**os.environ, "SSH_AUTH_SOCK": ""}})
    assert r.returncode == 0, r.stderr
    print("SOCK=" + r.stdout.strip())
'''


@pytest.mark.skipif(shutil.which("ssh-agent") is None,
                    reason="needs OpenSSH's ssh-agent")
def test_a_leaked_agent_fails_the_session_whatever_xdg_cache_home_held():
    outside = Path(tempfile.mkdtemp(prefix="x"))
    work = Path(tempfile.mkdtemp(prefix="g"))
    try:
        (work / "test_inner.py").write_text(_INNER)
        (work / "pytest.ini").write_text("[pytest]\n")
        env = {k: v for k, v in os.environ.items()
               if not k.startswith(("PYTEST_", "XDG_"))}
        env["XDG_CACHE_HOME"] = str(outside)
        env["PYTHONPATH"] = str(REPO_ROOT)
        r = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-s",
             "-p", "no:cacheprovider", "-p", "no:xdist",
             "-p", "tests.conftest", "-c", str(work / "pytest.ini"),
             "--rootdir", str(work), str(work / "test_inner.py")],
            cwd=work, env=env, capture_output=True, text=True, timeout=300)
        out = r.stdout + r.stderr
        sock = next(line[len("SOCK="):] for line in out.splitlines()
                    if line.startswith("SOCK="))
        assert not Path(sock).is_relative_to(outside), sock
        assert r.returncode == 1, out
        assert "conftest: ssh-agent(s)" in out, out
        assert ssh_agents_under(Path(sock).parent) == []
        assert ssh_agents_under(outside) == []
    finally:
        stop_ssh_agents_under(outside)
        shutil.rmtree(outside, ignore_errors=True)
        shutil.rmtree(work, ignore_errors=True)
