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
from pathlib import Path

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


def _run(root: Path, inst: Path, pre: str = "") -> tuple[int, str, str]:
    """The snapshot span in a launcher-shaped shell; prints the snapshot's
    path and LEERIE_COMMIT. `pre`: bash run first (a stand-in, by name)."""
    home = root / "home"
    home.mkdir(exist_ok=True)
    script = ("set -euo pipefail\n"
              f'HOME="{home}"; LEERIE_REPO="{inst}"; '
              f'LEERIE_VERSION="{VERSION}"\n'
              'remote_log() { printf "LOG: %s\\n" "$*" >&2; }\n'
              + pre + _span()
              + 'printf "%s\\n%s\\n" "$_code_snapshot" "$LEERIE_COMMIT"\n'
              # The EXIT trap would remove it; the test reads it first.
              + "trap - EXIT\n")
    r = subprocess.run(["/bin/bash", "-c", script], capture_output=True,
                       text=True, timeout=120,
                       env={"PATH": "/usr/bin:/bin",
                            "GIT_CEILING_DIRECTORIES": str(root)})
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
        # Whole, and every file the same commit's.
        assert got["orchestrator/leerie.py"] == f"# {letter}\n" * 200000, i
        assert got["prompts/planner.md"] == f"# {letter}\n", i
        ref = commit.partition("-dirty.")[2] or commit
        assert got == _archive(inst, ref, tmp_path / f"chk{i}"), i
    assert accepted, "no snapshot was taken"
