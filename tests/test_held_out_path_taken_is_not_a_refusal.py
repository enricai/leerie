"""A held-out file the tree leaves no room for is a fact about the tree, not a
confinement failure (DESIGN §6 *A confinement failure stops the run; it is
never a result*; review round 39, isolation R1 and D2).

The attack (r39i `place_link_refusal.py`): a worker commits `tests ->
spec_real`, or a regular file `tests`, where a held-out file is placed at
`tests/acceptance/acc.test.js`. The orchestrator's old check
(`dst.exists() or dst.is_symlink()`) looked at the final path only and
passed; the shim's `_place_one`, following no link, could not place it and
wrote its refusal descriptor; `main()` exited 78, and every `resume` redid
the same evaluation over the same tree.

- every placing caller checks the path by the shim's own rule
  (`_lexists_beneath`): the file is recorded `path_in_use`, never handed to
  the shim;
- the shim reports a taken path on `--taken-fd`, never on the refusal
  descriptor; `_run_streaming` raises `HeldOutPathTaken`, an `OSError`;
- the shim is non-dumpable before the command starts, so the command cannot
  reopen the refusal descriptor through `/proc/<shim>/fd/N` (r39i D2's
  forge, which succeeded without `--isolate`).
"""
from __future__ import annotations

import asyncio
import errno
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_worker_confinement import _St, run  # noqa: F401 (fixture)

SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"
REL = "tests/acceptance/acc.test.js"


def _shim():
    spec = importlib.util.spec_from_file_location("lk_taken", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_HAS_LANDLOCK = _shim().abi() >= 3


def _read_all(fd: int) -> bytes:
    os.set_blocking(fd, False)
    out = b""
    try:
        while chunk := os.read(fd, 65536):
            out += chunk
    except BlockingIOError:
        pass
    return out


def _tree(root: Path, shape: str) -> Path:
    """A worker's committed tree with `tests` shaped as `shape`."""
    view = root / "view"
    (view / "spec_real").mkdir(parents=True)
    (view / "spec_real" / "own.test.js").write_text("// the repo's own\n")
    if shape == "link":
        os.symlink("spec_real", view / "tests")
    elif shape == "file":
        (view / "tests").write_text("not a directory\n")
    elif shape == "taken":
        (view / "tests" / "acceptance").mkdir(parents=True)
        (view / REL).write_text("// the tree's own acc.test.js\n")
    else:
        (view / "tests").mkdir()
    return view


SHAPES = ["link", "file", "taken"]


# --- the shim ---------------------------------------------------------------

@pytest.mark.parametrize("shape", SHAPES)
def test_the_shim_finds_the_path_taken_and_writes_nothing(tmp_path, shape):
    m = _shim()
    view = _tree(tmp_path, shape)
    held = tmp_path / "acc.test.js"
    held.write_text("throw new Error('defect still there')\n")
    with pytest.raises(m.PlacementTaken):
        m.place([(str(held), REL)], str(view))
    assert not (view / "spec_real" / "acceptance").exists()
    if shape == "taken":
        assert (view / REL).read_text() == "// the tree's own acc.test.js\n"


def test_control_a_plain_directory_is_placed(tmp_path):
    m = _shim()
    view = _tree(tmp_path, "dir")
    held = tmp_path / "acc.test.js"
    held.write_text("x\n")
    m.place([(str(held), REL)], str(view))
    assert (view / REL).read_text() == "x\n"


def test_an_environment_failure_while_placing_is_still_a_refusal(
        tmp_path, monkeypatch):
    m = _shim()
    view = _tree(tmp_path, "dir")

    def _full(view_fd, src, rel, **_kw):
        raise OSError(errno.ENOSPC, "No space left on device")
    monkeypatch.setattr(m, "_place_one", _full)
    with pytest.raises(m.IsolationError):
        m.place([(str(tmp_path / "x"), REL)], str(view))


@pytest.mark.parametrize("shape", SHAPES)
def test_the_sandbox_reports_a_taken_path_on_its_own_descriptor(
        tmp_path, monkeypatch, shape):
    """`run_sandbox` with the real `place` (the namespaces, which this host
    may not build, stand aside): the refusal descriptor stays empty, the
    taken descriptor names the path, the command does not run."""
    m = _shim()
    view = _tree(tmp_path, shape)
    held = tmp_path / "acc.test.js"
    held.write_text("x\n")

    def _isolate(rw, ro, *, sandbox, view, place_pairs, worker_root, **_kw):
        m.place(list(place_pairs), view)
        return 0, []
    monkeypatch.setattr(m, "isolate", _isolate)
    rr, rw_ = os.pipe()
    tr, tw = os.pipe()
    m._REFUSAL_FD, m._TAKEN_FD = rw_, tw
    try:
        rc = m.run_sandbox(["/bin/true"], [], ["/"], str(view),
                           [(str(held), REL)], None, None)
    finally:
        os.close(rw_)
        os.close(tw)
    refused, taken = _read_all(rr), _read_all(tr)
    os.close(rr)
    os.close(tr)
    assert rc == 126
    assert refused == b""
    assert REL.encode() in taken and b"not placed" in taken


# --- the orchestrator -------------------------------------------------------

def _set(run, rel: str) -> dict:
    d = run["run_dir"] / "acceptance" / "set-9"
    (d / rel).parent.mkdir(parents=True, exist_ok=True)
    (d / rel).write_text("throw new Error('defect still there')\n")
    return {"index": 9, "dir": str(d), "defect_files": [rel],
            "control_files": [], "support_files": [], "cases": {}}


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


@pytest.mark.parametrize("shape", ["link", "file"])
def test_the_acceptance_gate_records_path_in_use_and_never_places_it(
        leerie, run, monkeypatch, shape):
    """The r39i attack through `_acceptance_run_placed`, with the real
    `_run_streaming`: the file is never handed to the shim, the set is
    unmeasured with `path_in_use`, and nothing stops the run."""
    st, view = _St(run["user"], run["run_dir"]), _view(run)
    (view / "spec_real").mkdir()
    if shape == "link":
        os.symlink("spec_real", view / "tests")
    else:
        (view / "tests").write_text("not a directory\n")
    s_ = _set(run, REL)
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda _st, rels, _tree=None, placed=None:
                        f"cat {rels[0]}")
    spawned = []
    real = leerie._run_streaming

    async def _spy(cmd, **kw):
        spawned.append(kw.get("sandbox"))
        return await real(cmd, **kw)
    monkeypatch.setattr(leerie, "_run_streaming", _spy)
    (res,) = asyncio.run(leerie._acceptance_run_placed(
        st, dict(leerie.DEFAULT_CAPS), view, [s_], "t", confirm=False))
    assert res["unmeasured"] and res["unmeasured_files"] == {
        REL: "path_in_use"}, res
    assert spawned == []
    assert list((view / "spec_real").iterdir()) == []


@pytest.mark.parametrize("shape", ["link", "file"])
def test_the_set_audit_and_the_parse_check_find_the_path_taken(
        leerie, run, shape):
    st, view = _St(run["user"], run["run_dir"]), _view(run)
    (view / "spec_real").mkdir()
    if shape == "link":
        os.symlink("spec_real", view / "tests")
    else:
        (view / "tests").write_text("not a directory\n")
    out = asyncio.run(leerie._acceptance_audit_in(
        st, dict(leerie.DEFAULT_CAPS), {}, {}, view, _set(run, REL),
        label="t", harness_rev="", tree_note=""))
    assert out.get("skipped"), out
    test_rel = "tests/acceptance/app.test.py"
    assert leerie._acceptance_probe_rel(st, test_rel).startswith(
        "tests/acceptance/")
    got = asyncio.run(leerie._acceptance_parse_probe(
        st, dict(leerie.DEFAULT_CAPS), str(view), test_rel, [],
        run["run_dir"] / "probe.log", "t"))
    assert got == (None, "the parse check's name is taken in the tree")


def test_a_taken_path_the_shim_reports_is_an_oserror_not_a_refusal(
        leerie, run):
    """Where no caller checked first, `_run_streaming` reads the taken
    descriptor (here written by the sandbox stand-in, which keeps the shim's
    contract) and raises `HeldOutPathTaken`: an `OSError`, which every
    placing caller reads as the run it could not make."""
    view = _view(run)
    os.symlink(".", view / "tests")
    held = run["run_dir"] / "held.js"
    held.write_text("x\n")
    with pytest.raises(leerie.HeldOutPathTaken) as ei:
        asyncio.run(leerie._run_streaming(
            ["/bin/true"], grants=leerie._heldout_grants(
                _St(run["user"], run["run_dir"]), view),
            cwd=str(view), timeout=60, verbosity="quiet", label="acc",
            sandbox=leerie.HeldOutSandbox(view=str(view),
                                          place=((str(held), REL),))))
    assert isinstance(ei.value, OSError)
    assert not isinstance(ei.value, leerie.ConfinementRefused)
    assert REL in str(ei.value)


_BOTH_SHIM = '''
import os, sys
a = sys.argv[1:]
for opt in ("--taken-fd", "--refusal-fd"):
    if opt in a:
        os.write(int(a[a.index(opt) + 1]), (opt + " written\\n").encode())
sys.exit(126)
'''


@pytest.mark.real_heldout_sandbox
def test_a_refusal_outranks_a_taken_path(leerie, tmp_path, monkeypatch):
    shim = tmp_path / "both_shim.py"
    shim.write_text(_BOTH_SHIM)
    monkeypatch.setattr(leerie, "LANDLOCK_SCRIPT", shim)
    held = tmp_path / "held.js"
    held.write_text("x\n")
    with pytest.raises(leerie.ConfinementRefused):
        asyncio.run(leerie._run_streaming(
            ["/bin/true"], grants=leerie.WorkerGrants(rw=(str(tmp_path),),
                                                      ro=("/",)),
            cwd=str(tmp_path), timeout=60, verbosity="quiet",
            sandbox=leerie.HeldOutSandbox(view=str(tmp_path),
                                          place=((str(held), REL),))))


# --- the refusal descriptor cannot be forged through /proc -----------------

_FORGE = (
    "import os, sys\n"
    "n = open(f'/proc/{os.getppid()}/cmdline', 'rb').read().split(b'\\0')\n"
    "fd = n[n.index(b'--refusal-fd') + 1].decode()\n"
    "try:\n"
    "    w = os.open(f'/proc/{os.getppid()}/fd/{fd}', os.O_WRONLY)\n"
    "except OSError as e:\n"
    "    print('refused', e.errno)\n"
    "    sys.exit(3)\n"
    "os.write(w, b'leerie: cannot isolate the command, so it does not run: "
    "FORGED\\n')\n"
    "print('forged')\n")


@pytest.mark.skipif(not _HAS_LANDLOCK,
                    reason="the host kernel has no Landlock ABI 3")
def test_the_command_cannot_reopen_the_refusal_descriptor_through_proc():
    """r39i D2's forge, without `--isolate`, where it succeeded: the command
    reads `--refusal-fd N` from its parent's cmdline and opens
    `/proc/<shim>/fd/N`. The non-dumpable shim refuses the open."""
    r, w = os.pipe()
    try:
        p = subprocess.run(
            [sys.executable, "-I", "-S", str(SHIM), "--refusal-fd", str(w),
             "--rw", "/dev/null", "--ro", "/", "--", sys.executable, "-I",
             "-S", "-c", _FORGE],
            pass_fds=(w,), capture_output=True, text=True, timeout=60,
            stdin=subprocess.DEVNULL)
    finally:
        os.close(w)
    got = _read_all(r)
    os.close(r)
    assert got == b""
    assert p.returncode == 3, (p.stdout, p.stderr)
    assert p.stdout.strip() == f"refused {errno.EACCES}"
