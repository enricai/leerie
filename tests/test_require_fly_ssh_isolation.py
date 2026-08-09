"""Tests for `_leerie_fly_agent_ensure` and `require_fly_ssh` in lib.sh.

The Fly auth flow uses a leerie-private ssh-agent at
`~/.cache/leerie/agent/ssh-agent.sock` so that Fly-issued certs don't
land in the user's main ssh-agent (where OpenSSH would offer them to
github.com on every git operation, triggering rate-limits). These tests
verify that isolation invariant and the idempotency of cert issuance.

flyctl is stubbed; no real Fly.io calls are made. A real `ssh-agent`
and `ssh-add` are used (they are stdlib OpenSSH tools available on any
darwin/linux dev box).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB_SH = REPO_ROOT / "scripts" / "remote" / "lib.sh"


@pytest.fixture
def short_home():
    """Provide a HOME path short enough for Unix-socket sun_path on
    macOS (104 bytes). Pytest's default tmp_path is under
    /private/var/folders/... which often exceeds the limit when an
    ssh-agent socket path is built underneath it; use /tmp directly.
    """
    home = Path("/tmp") / f"leerie-test-{uuid.uuid4().hex[:8]}"
    home.mkdir(parents=True, exist_ok=False)
    try:
        yield home
    finally:
        # Kill every process this test spawned under this unique HOME, then
        # remove the tree. `_leerie_fly_agent_ensure` spawns `ssh-agent -a
        # <HOME>/.cache/leerie/agent/ssh-agent.sock`, which daemonizes and
        # reparents to PID 1 — the test's subprocess.run parent never reaps
        # it, so it must be killed here or it leaks. Match on the unique HOME
        # substring (contains the per-test uuid) rather than the exact
        # `ssh-agent -a <sock>` argv: a daemonized agent's argv spacing is
        # not guaranteed to match the launch string, and the HOME prefix also
        # catches any other stray child (ssh-add, etc.) under this test's tree.
        subprocess.run(["pkill", "-f", str(home)], check=False)
        shutil.rmtree(home, ignore_errors=True)


def _run_bash(
    script: str, env: dict | None = None, tmp_home: Path | None = None
) -> subprocess.CompletedProcess:
    base_env = {k: v for k, v in os.environ.items()}
    # Strip the parent's SSH_AUTH_SOCK so the test doesn't accidentally
    # poke at the developer's real agent.
    base_env.pop("SSH_AUTH_SOCK", None)
    if tmp_home is not None:
        base_env["HOME"] = str(tmp_home)
        base_env["XDG_CACHE_HOME"] = str(tmp_home / ".cache")
    if env:
        base_env.update(env)
    return subprocess.run(
        ["bash", "-c", script],
        env=base_env,
        capture_output=True,
        text=True,
    )


def _stub_flyctl(tmp_path: Path) -> Path:
    """Write a flyctl stub that succeeds on `ssh issue --agent` by
    adding a synthetic cert-shaped identity to whatever agent
    SSH_AUTH_SOCK points at. Records each invocation to a counter file.
    """
    counter = tmp_path / "issue-count"
    counter.write_text("0\n")
    # Generate a real ed25519 cert via ssh-keygen so it looks like a Fly
    # cert when `ssh-add -l` lists it.
    cakey = tmp_path / "ca"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(cakey)],
        check=True,
    )
    userkey = tmp_path / "user"
    subprocess.run(
        ["ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-f", str(userkey)],
        check=True,
    )
    subprocess.run(
        [
            "ssh-keygen",
            "-s", str(cakey),
            "-I", "leerie-test",
            "-n", "root",
            "-V", "+24h",
            str(userkey) + ".pub",
        ],
        check=True,
    )
    stub = tmp_path / "flyctl"
    stub.write_text(
        f"""#!/usr/bin/env bash
if [ "$1" = "ssh" ] && [ "$2" = "issue" ]; then
  n=$(cat "{counter}")
  echo $((n+1)) > "{counter}"
  ssh-add "{userkey}" >/dev/null 2>&1
  exit 0
fi
exit 0
"""
    )
    stub.chmod(0o755)
    return counter


def test_lib_sh_exists():
    assert LIB_SH.exists(), "scripts/remote/lib.sh is missing"


def test_agent_ensure_creates_socket_dir_at_0700(short_home):
    """`_leerie_fly_agent_ensure` creates the socket dir with mode 0700."""
    result = _run_bash(
        f"source {LIB_SH}; _leerie_fly_agent_ensure; echo $SSH_AUTH_SOCK",
        tmp_home=short_home,
    )
    assert result.returncode == 0, result.stderr
    agent_dir = short_home / ".cache" / "leerie" / "agent"
    assert agent_dir.is_dir(), f"agent dir not created: {result.stderr}"
    mode = agent_dir.stat().st_mode & 0o777
    assert mode == 0o700, f"agent dir mode is {oct(mode)}, expected 0o700"
    sock_path = agent_dir / "ssh-agent.sock"
    assert sock_path.exists(), f"agent socket not created: stderr={result.stderr}"
    # Confirm SSH_AUTH_SOCK was exported to that socket.
    assert result.stdout.strip() == str(sock_path)


def test_agent_ensure_reuses_live_socket(short_home):
    """Second call to `_leerie_fly_agent_ensure` reuses an existing agent."""
    sock = short_home / ".cache" / "leerie" / "agent" / "ssh-agent.sock"
    script = f"""
        source {LIB_SH}
        _leerie_fly_agent_ensure
        first_pid=$(pgrep -f "ssh-agent -a {sock}" | head -1)
        _leerie_fly_agent_ensure
        second_pid=$(pgrep -f "ssh-agent -a {sock}" | head -1)
        echo "first=$first_pid second=$second_pid"
    """
    result = _run_bash(script, tmp_home=short_home)
    assert result.returncode == 0, result.stderr
    # Same agent PID across two calls = reuse, not respawn.
    line = [l for l in result.stdout.splitlines() if l.startswith("first=")][0]
    first, second = line.replace("first=", "").replace("second=", "").split(" ")
    assert first and first == second, (
        f"agent was respawned: first={first} second={second}"
    )


def test_require_fly_ssh_isolates_from_user_agent(short_home):
    """require_fly_ssh sets SSH_AUTH_SOCK to the private socket, not
    whatever the parent shell had set.
    """
    counter = _stub_flyctl(short_home)
    # Pretend the parent shell has its own ssh-agent at a different
    # path. If require_fly_ssh fails to override SSH_AUTH_SOCK, the
    # flyctl stub would write to the bogus path and the cert wouldn't
    # land in the private agent.
    bogus_user_sock = short_home / "fake-user-ssh-agent.sock"
    script = f"""
        export PATH="{short_home}:$PATH"
        export SSH_AUTH_SOCK="{bogus_user_sock}"
        source {LIB_SH}
        require_fly_ssh
        echo "exit=$?"
        echo "final_sock=$SSH_AUTH_SOCK"
    """
    result = _run_bash(script, tmp_home=short_home)
    assert "exit=0" in result.stdout, f"require_fly_ssh failed: {result.stderr}"
    # SSH_AUTH_SOCK MUST be redirected to the leerie-private path, not
    # the bogus user path.
    private_sock = short_home / ".cache" / "leerie" / "agent" / "ssh-agent.sock"
    assert f"final_sock={private_sock}" in result.stdout, (
        f"SSH_AUTH_SOCK not redirected to private socket; got: {result.stdout}"
    )
    # And the flyctl stub was invoked exactly once.
    assert counter.read_text().strip() == "1", (
        f"expected exactly 1 flyctl ssh issue call, got {counter.read_text().strip()}"
    )


def test_agent_ensure_reuses_keyless_but_reachable_agent(short_home):
    """A live agent with no keys yet (ssh-add -l exit code 1) must be
    reused, not respawned. Respawning on rc 1 unlinks the live socket
    (rm -f) out from under the still-running agent process, orphaning
    it permanently — the exact defect this subtask fixes.
    """
    sock = short_home / ".cache" / "leerie" / "agent" / "ssh-agent.sock"
    script = f"""
        source {LIB_SH}
        _leerie_fly_agent_ensure
        # No identities added: `ssh-add -l` against this agent returns
        # exit code 1 (reachable, no keys) — not exit code 2.
        SSH_AUTH_SOCK="{sock}" ssh-add -l >/dev/null 2>&1
        echo "add_rc=$?"
        before=$(pgrep -x ssh-agent | sort | tr '\\n' ',')
        _leerie_fly_agent_ensure
        after=$(pgrep -x ssh-agent | sort | tr '\\n' ',')
        echo "before=$before"
        echo "after=$after"
    """
    result = _run_bash(script, tmp_home=short_home)
    assert result.returncode == 0, result.stderr
    assert "add_rc=1" in result.stdout, (
        f"expected ssh-add -l to return rc 1 (keyless, reachable) against a "
        f"freshly-spawned agent with no keys added; got: {result.stdout}"
    )
    before_line = [l for l in result.stdout.splitlines() if l.startswith("before=")][0]
    after_line = [l for l in result.stdout.splitlines() if l.startswith("after=")][0]
    before = before_line.replace("before=", "")
    after = after_line.replace("after=", "")
    # The exact same process set must still be alive, and no additional
    # ssh-agent process may have been spawned alongside it (a leaked
    # orphan would show up here as an extra pid).
    assert before and before == after, (
        f"agent was respawned/orphaned on a keyless-but-reachable agent: "
        f"before={before!r} after={after!r}"
    )


def test_agent_ensure_respawns_on_unreachable_agent(short_home):
    """A dead/unreachable agent (ssh-add -l exit code 2 — stale socket,
    no listener) must still trigger rm -f + respawn.
    """
    agent_dir = short_home / ".cache" / "leerie" / "agent"
    sock = agent_dir / "ssh-agent.sock"
    agent_dir.mkdir(parents=True)
    # Fabricate a stale socket inode with nothing listening on it, which
    # is exactly what ssh-add -l reports as exit code 2.
    subprocess.run(
        ["python3", "-c", f"""
import socket
s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
s.bind({str(sock)!r})
s.close()
"""],
        check=True,
    )
    script = f"""
        source {LIB_SH}
        SSH_AUTH_SOCK="{sock}" ssh-add -l >/dev/null 2>&1
        echo "add_rc=$?"
        _leerie_fly_agent_ensure
        pid=$(pgrep -x ssh-agent | head -1)
        echo "pid=$pid"
    """
    result = _run_bash(script, tmp_home=short_home)
    assert result.returncode == 0, result.stderr
    assert "add_rc=2" in result.stdout, (
        f"expected ssh-add -l to return rc 2 against an unreachable/stale "
        f"socket; got: {result.stdout}"
    )
    pid_line = [l for l in result.stdout.splitlines() if l.startswith("pid=")][0]
    assert pid_line != "pid=", (
        f"expected a fresh agent to be spawned on rc 2 (stale socket); "
        f"got no pid: {result.stdout}"
    )


def test_agent_ensure_spawns_new_agent_with_idle_timeout(short_home):
    """A newly-spawned agent must carry a `-t` idle timeout, so an
    orphan that does still occur (e.g. a future regression) expires
    rather than leaking indefinitely.
    """
    sock = short_home / ".cache" / "leerie" / "agent" / "ssh-agent.sock"
    script = f"""
        source {LIB_SH}
        _leerie_fly_agent_ensure
        ps -eo args= | grep '^ssh-agent -a'
    """
    result = _run_bash(script, tmp_home=short_home)
    assert result.returncode == 0, result.stderr
    cmdline = result.stdout.strip()
    assert " -t " in f" {cmdline} ", (
        f"expected the spawned ssh-agent to carry a -t idle-timeout flag; "
        f"got argv: {cmdline!r}"
    )


def test_require_fly_ssh_is_idempotent(short_home):
    """Calling require_fly_ssh twice in a row issues only one cert.
    Spans a single bash process so the private agent persists.
    """
    counter = _stub_flyctl(short_home)
    script = f"""
        export PATH="{short_home}:$PATH"
        source {LIB_SH}
        require_fly_ssh
        require_fly_ssh
    """
    result = _run_bash(script, tmp_home=short_home)
    assert result.returncode == 0, result.stderr
    assert counter.read_text().strip() == "1", (
        f"expected exactly 1 issuance across two calls, got {counter.read_text().strip()}"
    )
