"""A launch's code is one snapshot of one state of the install, and the run
records exactly that state (DESIGN §6 *A launch runs only the leerie code it
started with*; review round 39, execution E2 and E3).

- E2: `LEERIE_COMMIT` was read at launcher start, the copy taken after every
  image step (minutes later); a same-version update in between ran code the
  record did not name.
- E3: the copy was `cp -pR` of the install's working tree; while `git
  checkout` flipped it, 147 of 300 copies held a partial `leerie.py`, all
  accepted.

The harness runs the launcher's own snapshot span, extracted verbatim
(`_snapshot_leerie_code` and the directory it is taken into), against an
install of each shape, and prints the snapshot's path and `LEERIE_COMMIT`.
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest

from tests.conftest import run_git_out as _git
from tests.launcher_argv_extract import LAUNCHER

DIRS = ("orchestrator", "scripts", "prompts", ".claude-plugin")
VERSION = "9.9.9"


def _span() -> str:
    src = LAUNCHER.read_text()
    start = src.index("# --- the leerie code this launch runs ---")
    end = src.index("# --- refuse a command line before any image step")
    return src[start:end]


def _install(root: Path, letter: str = "A", git: bool = True) -> Path:
    inst = root / "install"
    for d in DIRS:
        (inst / d).mkdir(parents=True, exist_ok=True)
    _write(inst, letter)
    if git:
        _git(inst, "init", "-q", "-b", "main")
        _git(inst, "config", "user.email", "t@t")
        _git(inst, "config", "user.name", "t")
        _commit(inst, letter)
    return inst


def _write(inst: Path, letter: str) -> None:
    (inst / "orchestrator" / "leerie.py").write_text(f"# {letter}\n" * 200000)
    (inst / "scripts" / "landlock_exec.py").write_text(f"# {letter}\n")
    (inst / "prompts" / "planner.md").write_text(f"# {letter}\n")
    (inst / ".claude-plugin" / "plugin.json").write_text(
        '{\n  "name": "leerie",\n  "version": "%s"\n}\n' % VERSION)


def _commit(inst: Path, letter: str) -> str:
    _git(inst, "add", "-A")
    _git(inst, "commit", "-q", "-m", letter)
    _git(inst, "branch", "-f", f"c{letter}")
    return _git(inst, "rev-parse", "HEAD")


def _script(home: Path, inst: Path, pre: str = "") -> str:
    return ("set -euo pipefail\n"
            f'HOME="{home}"; LEERIE_REPO="{inst}"; '
            f'LEERIE_VERSION="{VERSION}"\n'
            'remote_log() { printf "LOG: %s\\n" "$*" >&2; }\n'
            + pre + _span()
            + 'printf "%s\\n%s\\n" "$_code_snapshot" "$LEERIE_COMMIT"\n'
            # The EXIT trap would remove it; the test reads it first.
            + "trap - EXIT\n")


def _run(root: Path, inst: Path, pre: str = "",
         env: dict[str, str] | None = None) -> tuple[int, str, str]:
    """The snapshot span in a launcher-shaped shell; prints the snapshot's
    path and LEERIE_COMMIT. `pre`: bash run first (a stand-in, by name)."""
    home = root / "home"
    home.mkdir(exist_ok=True)
    r = subprocess.run(["/bin/bash", "-c", _script(home, inst, pre)],
                       capture_output=True, text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin",
                            "GIT_CEILING_DIRECTORIES": str(root),
                            **(env or {})})
    return r.returncode, r.stdout, r.stderr


def _files(d: Path) -> dict[str, str]:
    return {str(p.relative_to(d)): p.read_text() for p in sorted(d.rglob("*"))
            if p.is_file()}


def _archive(inst: Path, ref: str, dest: Path) -> dict[str, str]:
    dest.mkdir()
    a = subprocess.run(["git", "-C", str(inst), "archive", ref, "--", *DIRS],
                       check=True, capture_output=True)
    subprocess.run(["tar", "-xf", "-", "-C", str(dest)], input=a.stdout,
                   check=True)
    return _files(dest)


def test_a_clean_checkout_runs_its_commit_and_records_it(tmp_path):
    inst = _install(tmp_path)
    head = _git(inst, "rev-parse", "HEAD")
    rc, out, err = _run(tmp_path, inst)
    assert rc == 0, err
    snap, commit = out.splitlines()
    assert commit == _git(inst, "rev-parse", "--short", head)
    assert _files(Path(snap)) == _archive(inst, head, tmp_path / "chk")


def test_a_contributors_edits_run_and_the_record_names_them(tmp_path):
    """Uncommitted edits (a changed file, a new prompt) still run on the
    next launch, and `LEERIE_COMMIT` names the exact tree that ran."""
    inst = _install(tmp_path)
    short = _git(inst, "rev-parse", "--short", "HEAD")
    (inst / "scripts" / "landlock_exec.py").write_text("# edited\n")
    (inst / "prompts" / "new.md").write_text("# new\n")
    rc, out, err = _run(tmp_path, inst)
    assert rc == 0, err
    snap, commit = out.splitlines()
    got = _files(Path(snap))
    assert got["scripts/landlock_exec.py"] == "# edited\n"
    assert got["prompts/new.md"] == "# new\n"
    prefix, _, tree = commit.partition("-dirty.")
    assert prefix == short and len(tree) == 12, commit
    assert _files(Path(snap)) == _archive(inst, tree, tmp_path / "chk")
    # The checkout itself is untouched: nothing staged, the edits still
    # edits.
    assert _git(inst, "diff", "--cached", "--name-only") == ""


def test_an_install_without_git_is_copied_and_records_no_commit(tmp_path):
    inst = _install(tmp_path, git=False)
    rc, out, err = _run(tmp_path, inst)
    assert rc == 0, err
    snap, commit = (out.splitlines() + [""])[:2]
    assert commit == ""
    assert _files(Path(snap)) == _files(inst)


def test_an_install_without_git_that_changes_under_the_copy_stops(tmp_path):
    """A stand-in `cp` (by name, then the real one by absolute path) changes
    the install once the copy of `scripts` is made: the copy no longer
    equals the install, and the launch stops."""
    inst = _install(tmp_path, git=False)
    pre = ('cp() { /bin/cp "$@"; case "$*" in *"/scripts "*) '
           f'printf "# B\\n" > "{inst}/scripts/landlock_exec.py";; esac; }}\n')
    rc, _out, err = _run(tmp_path, inst, pre)
    assert rc == 1
    assert "changed while this launch copied it" in err, err


def test_a_checkout_an_update_rewrites_during_the_snapshot_never_tears_it(
        tmp_path):
    """E3's harness, smaller: `git checkout` flips the install between two
    same-version commits while the snapshot is taken 25 times. Every
    accepted snapshot is whole, one commit's, and equals the code its
    `LEERIE_COMMIT` names; a snapshot that cannot be taken stops the
    launch. (Measured 2026-11-04 at 300 copies: 0 torn, against 176 of 300
    for the copy it replaces.)"""
    inst = _install(tmp_path, "A")
    _write(inst, "B")
    _commit(inst, "B")
    flip = subprocess.Popen(
        ["/bin/bash", "-c",
         f'while :; do git -C "{inst}" checkout -q -f cA 2>/dev/null; '
         f'git -C "{inst}" checkout -q -f cB 2>/dev/null; done'],
        env={"PATH": "/usr/bin:/bin"}, start_new_session=True)
    results = []
    try:
        for i in range(25):
            root = tmp_path / f"r{i}"
            root.mkdir()
            results.append(_run(root, inst))
    finally:
        os.killpg(flip.pid, signal.SIGKILL)
        flip.wait()
    accepted = 0
    for i, (rc, out, err) in enumerate(results):
        if rc != 0:
            # Stopped by the snapshot itself, naming why.
            assert "launch again" in err, err
            continue
        accepted += 1
        snap, commit = out.splitlines()
        got = _files(Path(snap))
        letter = got["scripts/landlock_exec.py"][2]
        # Whole, and every file the same commit's. Compared to a bool: on a
        # mismatch pytest's diff of two 200,000-line strings ran past the
        # 600 s per-test timeout on CI and hid the finding.
        whole = got["orchestrator/leerie.py"] == f"# {letter}\n" * 200000
        assert whole, (i, commit, len(got["orchestrator/leerie.py"]))
        assert got["prompts/planner.md"] == f"# {letter}\n", i
        ref = commit.partition("-dirty.")[2] or commit
        assert got == _archive(inst, ref, tmp_path / f"chk{i}"), i
    assert accepted, "no snapshot was taken"


def test_a_checkout_flipped_away_and_back_while_it_is_read_stops(tmp_path):
    """A checkout to B stalls mid-write (index lock held, `leerie.py`
    half-written) while both private-index reads run, then completes, and
    the update flips the install back to A before the second look. The
    lock is absent at both looks and HEAD and the blob ids are A's again,
    so a comparison of entries alone accepted a torn `leerie.py` (CI,
    main 8c69b8c0, py3.12). The stat each entry records shows the files
    were rewritten. A stand-in `git` (by name; the real one by absolute
    path) plays the update at each `add`."""
    inst = _install(tmp_path, "A")
    _write(inst, "B")
    _commit(inst, "B")
    _git(inst, "checkout", "-q", "-f", "cA")
    (inst / "prompts" / "new.md").write_text("# new\n")
    lock = inst / ".git" / "index.lock"
    half = inst / "orchestrator" / "leerie.py"
    pre = ('git() { case " $* " in *" add "*) '
           f': > "{lock}"; printf "# B\\n" > "{half}"; '
           '/usr/bin/git "$@"; _r=$?; '
           f'/bin/rm -f "{lock}"; '
           '( unset GIT_INDEX_FILE; '
           f'/usr/bin/git -C "{inst}" checkout -q -f cB; '
           f'/usr/bin/git -C "{inst}" checkout -q -f cA ); '
           'return $_r;; esac; /usr/bin/git "$@"; }\n')
    rc, out, err = _run(tmp_path, inst, pre)
    assert rc == 1, out
    assert "changed while this launch read it; launch again" in err, err


def test_a_checkout_git_cannot_read_is_copied_with_gits_reason(tmp_path):
    """Review round 40, execution F3: an install git refuses to read
    (another user's: git's own safe.directory test hook) stopped every
    launch with "has no commit to run; launch again", which no relaunch
    changes. It is taken as a copy, compared like any non-git install,
    with git's reason named and no commit recorded."""
    inst = _install(tmp_path)
    (inst / "prompts" / "edited.md").write_text("# edited\n")
    # The host's system config is not read: a GitHub runner's /etc/gitconfig
    # carries `safe.directory = *` (actions/runner-images, install-git.sh),
    # which marks every checkout safe, so git read this one and the launch
    # took the git path. The refusal is the subject; whether a host waives
    # it is not.
    rc, out, err = _run(tmp_path, inst,
                        env={"GIT_TEST_ASSUME_DIFFERENT_OWNER": "1",
                             "GIT_CONFIG_NOSYSTEM": "1"})
    assert rc == 0, err
    snap, commit = (out.splitlines() + [""])[:2]
    assert commit == ""
    assert "dubious ownership" in err and "taking it as a copy" in err, err
    assert "launch again" not in err, err
    want = {f"{d}/{k}": v for d in DIRS for k, v in _files(inst / d).items()}
    assert _files(Path(snap)) == want


def _sweep() -> str:
    src = LAUNCHER.read_text()
    return src[src.index("_sweep_stale_stages() {"):
               src.index("( _sweep_stale_stages )")]


@pytest.mark.parametrize("sig", ["SIGINT", "SIGTERM", "SIGKILL"])
def test_a_signal_during_the_snapshot_leaves_nothing_behind(tmp_path, sig):
    """Review round 40, execution F2 and isolation F2: the private index of
    a contributor's snapshot was a directory of its own,
    ~/.cache/leerie/idx-<pid>-*, removed only on the straight path; a
    signal during `git add` left it, and no sweep removed it. A stand-in
    `git` (by name; the real one by absolute path) holds the snapshot in
    `add` until the signal lands. SIGINT and SIGTERM go to the launch's
    process group (a Ctrl-C, a service stop) and its EXIT trap runs;
    SIGKILL leaves the rest to the next launch's sweep of stale stages."""
    inst = _install(tmp_path)
    (inst / "scripts" / "landlock_exec.py").write_text("# edited\n")
    home = tmp_path / "home"
    home.mkdir()
    in_add = tmp_path / "in-add"
    pre = ('git() { case " $* " in *" add "*) '
           f': > "{in_add}"; /usr/bin/sleep 30;; esac; '
           '/usr/bin/git "$@"; }\n')
    p = subprocess.Popen(["/bin/bash", "-c", _script(home, inst, pre)],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         env={"PATH": "/usr/bin:/bin",
                              "GIT_CEILING_DIRECTORIES": str(tmp_path)},
                         start_new_session=True)
    try:
        deadline = time.monotonic() + 60
        while not in_add.exists():
            assert p.poll() is None and time.monotonic() < deadline
            time.sleep(0.02)
        cache = home / ".cache" / "leerie"
        assert any(cache.iterdir())
        os.killpg(p.pid, getattr(signal, sig))
        p.wait(timeout=60)
    finally:
        if p.poll() is None:
            os.killpg(p.pid, signal.SIGKILL)
            p.wait()
    if sig != "SIGKILL":
        assert sorted(e.name for e in cache.iterdir()) == []
    subprocess.run(["/bin/bash", "-c",
                    f'HOME="{home}"\n' + _sweep() + "_sweep_stale_stages\n"],
                   env={"PATH": "/usr/bin:/bin"}, check=True, timeout=60)
    assert sorted(e.name for e in cache.iterdir()) == []
