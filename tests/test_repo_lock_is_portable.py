"""`leerie_repo_lock` holds a real kernel lock, on every host leerie's
scripts run on.

The lock was taken by the `flock` binary, which macOS does not ship: it
exited 127 and took every `setup-run.sh` with it, so 9 of
`tests/test_collect_subtrees_sh.py`'s cases failed on a developer's Mac
while CI (Linux) stayed green. It is now `fcntl.flock` on the descriptor
the shell itself opened (`exec 201>`), which keeps the two properties the
lock is for: unbounded exclusion, and release by the kernel when the holder
dies however it dies (DESIGN §6 *Never a repository-global git operation*).

These run on any kernel: nothing here needs Landlock or the container.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "worktree-lib.sh"


def _holder(lock: Path, script: str) -> subprocess.Popen:
    """A shell holding the lock through `leerie_repo_lock`'s own form."""
    return subprocess.Popen(
        ["bash", "-c",
         f'exec 201>"{lock}"\n'
         "/usr/bin/python3 -I -S -c 'import fcntl; "
         "fcntl.flock(201, fcntl.LOCK_EX)' 201>&201\n" + script],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _held(lock: Path) -> bool:
    """True when some other process holds the lock."""
    r = subprocess.run(
        [sys.executable, "-c",
         "import fcntl, os, sys\n"
         f"fd = os.open({str(lock)!r}, os.O_RDWR | os.O_CREAT, 0o600)\n"
         "try:\n"
         "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
         "    sys.exit(0)\n"
         "except BlockingIOError:\n"
         "    sys.exit(3)\n"],
        capture_output=True, text=True)
    assert r.returncode in (0, 3), r.stderr
    return r.returncode == 3


def test_the_lock_excludes_another_process(tmp_path):
    """The property the lock exists for, read as an outcome and not as a
    call: while one shell holds it, nobody else can take it."""
    lock = tmp_path / "leerie-worktree.lock"
    p = _holder(lock, "echo held; read -r _ || sleep 30")
    try:
        assert p.stdout.readline().strip() == "held"
        assert _held(lock), "the lock did not exclude a second taker"
    finally:
        p.kill()
        p.wait(timeout=30)


def test_the_lock_outlives_the_helper_that_took_it(tmp_path):
    """The helper is short-lived by design; the lock lives on the shell's
    own descriptor, so it must still be held once the helper has exited."""
    lock = tmp_path / "leerie-worktree.lock"
    # `wait` makes the helper's exit observable before the probe runs.
    p = _holder(lock, "wait; echo helper-gone; read -r _ || sleep 30")
    try:
        assert p.stdout.readline().strip() == "helper-gone"
        assert _held(lock), "the lock died with the helper that took it"
    finally:
        p.kill()
        p.wait(timeout=30)


def test_the_kernel_releases_the_lock_when_the_holder_is_killed(tmp_path):
    """A Fly `machine stop` SIGKILLs a run mid-`worktree add`; a lock that
    outlived it would wedge every later run. This is the property a
    mkdir-as-mutex would not have.
    """
    lock = tmp_path / "leerie-worktree.lock"
    p = _holder(lock, "echo held; exec sleep 30")
    assert p.stdout.readline().strip() == "held"
    assert _held(lock)
    p.kill()
    p.wait(timeout=30)
    assert not _held(lock), "the lock survived a SIGKILLed holder"


def test_the_lock_serialises_concurrent_holders(tmp_path):
    """Mutual exclusion as an outcome: N shells each read-modify-write a
    counter under the lock. Without a real lock the increments interleave
    and the total comes up short.
    """
    lock = tmp_path / "leerie-worktree.lock"
    counter = tmp_path / "n"
    counter.write_text("0")
    workers, each = 6, 12
    procs = [
        subprocess.Popen(
            ["bash", "-c",
             f'for _ in $(seq {each}); do\n'
             f'  ( exec 201>"{lock}"\n'
             "    /usr/bin/python3 -I -S -c 'import fcntl; "
             "fcntl.flock(201, fcntl.LOCK_EX)' 201>&201\n"
             f'    n=$(cat "{counter}"); echo $((n+1)) > "{counter}" )\n'
             "done"],
            stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True)
        for _ in range(workers)]
    for q in procs:
        _, err = q.communicate(timeout=300)
        assert q.returncode == 0, err
    assert counter.read_text().strip() == str(workers * each)


def test_the_lock_needs_no_flock_binary(tmp_path):
    """The defect itself: `leerie_repo_lock` must work where no `flock`
    binary exists, since macOS ships none. Driven through the real
    function, on a PATH holding only the coreutils it needs.
    """
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", "."], cwd=repo, check=True)
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("git", "bash", "cat", "dirname", "sed", "grep", "env",
                 "uname", "mkdir", "rm", "ls"):
        src = shutil.which(tool)
        if src:
            (bin_dir / tool).symlink_to(src)
    assert shutil.which("flock", path=str(bin_dir)) is None
    r = subprocess.run(
        ["bash", "-c",
         f'. "{LIB}"\nleerie_repo_lock\necho locked'],
        cwd=repo, capture_output=True, text=True,
        env={**os.environ, "PATH": str(bin_dir)}, timeout=120)
    assert r.returncode == 0, r.stderr
    assert "locked" in r.stdout
    assert "flock" not in r.stderr, r.stderr
