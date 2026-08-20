"""Concurrency harness for scripts/new-worktree.sh (N19).

The races the per-run flock exists to eliminate — CDFAIL (an `add` reports
success but a sibling's concurrent `git worktree prune` deregisters it out
from under the caller, so the directory or registration is gone moments
later) and incorrect RM (the orphan-cleanup guard's check-then-act `rm -rf`
removes a directory a concurrent `git worktree add` just registered) — both
require the prune -> orphan-check -> rm -rf -> add sequence of TWO
concurrent invocations to interleave. The flock's whole job is to make that
interleaving impossible: once one worker holds it, every step of that
sequence for every other worker of the same run is blocked until it
finishes.

That mutual-exclusion property is what this harness measures directly,
rather than racing real git internals for a CDFAIL under a few seconds of
wall-clock (git's own admin-entry write is fast enough that reproducing it
naturally, without instrumentation, is unreliable in a short test run — the
finding's own harness needed a much larger corpus to land ~2.5%/~8% rates).
Instrumented copies of the script record an [enter, exit] timestamp
interval around the exact prune -> ... -> add sequence for every
invocation, with a deliberately widened critical section (an injected sleep)
so overlapping invocations are easy to observe within a single test run:

  * the LOCKED copy (the real script, unmodified except for the injected
    sleep) must show ZERO overlapping intervals across concurrent
    siblings — the flock serializes them by construction.
  * the UNLOCKED copy (the flock lines stripped) must show at least one
    overlapping interval — the falsification control proving the harness
    would have caught the race if the fix were reverted.

An overlapping interval is precisely the window in which a sibling's prune
can deregister another sibling's in-flight worktree, or a sibling's rm -rf
can remove a directory another sibling's add just registered — i.e. it is
the necessary condition for CDFAIL / incorrect RM, and the flock's contract
is that this window never opens between siblings of the same run.
"""
import os
import re
import subprocess
import threading
from pathlib import Path

from tests.conftest import run_git_cwd_kw as _git

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "new-worktree.sh"

ROUNDS = 4
WORKERS_PER_ROUND = 8
# Widens the critical section so concurrent invocations are very likely to
# overlap in wall-clock time within a short test run, on the unlocked copy.
INJECTED_DELAY_SEC = "0.05"


def _make_repo(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    _git("init", "-q", "-b", "main", cwd=repo)
    _git("config", "user.email", "test@leerie.local", cwd=repo)
    _git("config", "user.name", "leerie-test", cwd=repo)
    _git("config", "commit.gpgsign", "false", cwd=repo)
    (repo / "file.txt").write_text("initial\n")
    _git("add", "file.txt", cwd=repo)
    _git("commit", "-q", "-m", "initial", cwd=repo)
    _git("branch", "leerie/runs/run-race", "main", cwd=repo)
    return repo


def _instrumented_script(tmp_path: Path, name: str, log: Path,
                          strip_lock: bool) -> Path:
    """Copy of new-worktree.sh that logs `enter <sid> <epoch>` right before
    the prune -> ... -> add sequence and `exit <sid> <epoch>` right after,
    with an injected sleep inside that window to widen it. When
    `strip_lock` is True, the flock lines are dropped — the falsification
    control.
    """
    src = SCRIPT.read_text()
    lines = src.splitlines(keepends=True)
    out = []
    lock_markers = ("LOCK_DIR=", 'mkdir -p "$LOCK_DIR"',
                     "exec 200>", "flock 200")
    enter_snippet = (
        f'echo "enter $ID $(date +%s.%N)" >> "{log}"\n'
        f'sleep {INJECTED_DELAY_SEC}\n'
    )
    for line in lines:
        stripped = line.strip()
        if strip_lock and any(stripped.startswith(m) for m in lock_markers):
            if stripped.startswith("LOCK_DIR="):
                # Same instrumentation point in source order, minus the lock
                # itself: the enter-marker must still land here so both
                # copies log "enter" at the same point relative to the
                # prune -> ... -> add sequence that follows.
                out.append(enter_snippet)
            continue
        out.append(line)
        if stripped == "flock 200" and not strip_lock:
            out.append(enter_snippet)
    text = "".join(out)
    # Log the exit right before the final `(cd "$WT" && pwd)` — i.e. after
    # the whole prune/orphan-check/add sequence has completed.
    marker = '(cd "$WT" && pwd)'
    assert marker in text, "harness bug: exit-instrumentation point not found"
    text = text.replace(
        marker,
        f'echo "exit $ID $(date +%s.%N)" >> "{log}"\n{marker}',
        1,
    )
    script = tmp_path / name
    script.write_text(text)
    script.chmod(0o755)
    # The script sources scripts/worktree-lib.sh relative to its own location,
    # so a harness that COPIES the script must copy that dependency too --
    # otherwise the copy dies on `prune_leerie_worktrees: command not found`
    # and the instrumentation log stays empty, which surfaces as the
    # deliberately-loud "harness bug: no instrumentation events recorded".
    lib = tmp_path / "worktree-lib.sh"
    if not lib.exists():
        lib.write_text((REPO_ROOT / "scripts" / "worktree-lib.sh").read_text())
        lib.chmod(0o755)
    return script


def _run_workers(script: Path, repo: Path, run_id: str, rnd: int) -> None:
    threads = []
    for i in range(WORKERS_PER_ROUND):
        sid = f"sub-{rnd:03d}-{i:03d}"

        def _invoke(sid=sid):
            subprocess.run(
                ["bash", str(script), sid, run_id],
                cwd=str(repo), capture_output=True, text=True, check=False,
            )
        t = threading.Thread(target=_invoke)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()


def _reset_worktrees(repo: Path) -> None:
    listing = _git("worktree", "list", "--porcelain", cwd=repo).stdout
    for path in re.findall(r"^worktree (.+)$", listing, re.M):
        if path == str(repo):
            continue
        _git("worktree", "remove", "--force", path, cwd=repo)
    _git("worktree", "prune", cwd=repo)


def _count_overlaps(log: Path) -> int:
    """Parses the enter/exit log into per-sid [enter, exit] intervals and
    counts how many pairs of DIFFERENT sids' intervals overlap in time."""
    if not log.exists():
        return 0
    enters: dict = {}
    intervals = []
    for line in log.read_text().splitlines():
        parts = line.split()
        if len(parts) != 3:
            continue
        kind, sid, ts = parts
        ts = float(ts)
        if kind == "enter":
            enters[sid] = ts
        elif kind == "exit" and sid in enters:
            intervals.append((enters.pop(sid), ts, sid))

    overlaps = 0
    intervals.sort()
    for i in range(len(intervals)):
        s1, e1, sid1 = intervals[i]
        for j in range(i + 1, len(intervals)):
            s2, e2, sid2 = intervals[j]
            if s2 >= e1:
                break
            if sid1 != sid2:
                overlaps += 1
    return overlaps


def _sweep(script: Path, repo: Path, run_id: str) -> None:
    for rnd in range(ROUNDS):
        _run_workers(script, repo, run_id, rnd)
        _reset_worktrees(repo)


def test_locked_script_never_overlaps_the_critical_section(
        tmp_path: Path) -> None:
    """With the flock in place, no two siblings' prune -> ... -> add
    sequences may overlap in time — the necessary condition for both
    CDFAIL and incorrect RM never occurs."""
    repo = _make_repo(tmp_path, "repo-locked")
    log = tmp_path / "locked.log"
    script = _instrumented_script(tmp_path, "locked.sh", log,
                                   strip_lock=False)
    os.environ["LEERIE_STATE_DIR"] = str(tmp_path / "state-locked")
    try:
        _sweep(script, repo, "run-race")
    finally:
        os.environ.pop("LEERIE_STATE_DIR", None)

    assert log.exists(), "harness bug: no instrumentation events recorded"
    overlaps = _count_overlaps(log)
    assert overlaps == 0, (
        f"locked script allowed {overlaps} overlapping critical-section "
        "interval(s) between siblings — the flock did not serialize them")


def test_unlocked_script_allows_overlap(tmp_path: Path) -> None:
    """Falsification control: the SAME harness against the SAME script with
    the flock lines stripped must show overlapping critical sections —
    proving the harness actually detects the condition the flock
    eliminates, and that removing the flock reintroduces it."""
    repo = _make_repo(tmp_path, "repo-unlocked")
    log = tmp_path / "unlocked.log"
    script = _instrumented_script(tmp_path, "unlocked.sh", log,
                                   strip_lock=True)
    assert "flock" not in script.read_text(), (
        "harness bug: unlocked copy still contains the lock")
    os.environ["LEERIE_STATE_DIR"] = str(tmp_path / "state-unlocked")
    try:
        _sweep(script, repo, "run-race")
    finally:
        os.environ.pop("LEERIE_STATE_DIR", None)

    assert log.exists(), "harness bug: no instrumentation events recorded"
    overlaps = _count_overlaps(log)
    assert overlaps > 0, (
        "the unlocked harness produced zero overlapping critical sections "
        "— the harness no longer detects the condition the flock exists "
        "to prevent")
