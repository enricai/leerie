"""A confinement failure stops the run; it is never a result (DESIGN §6 *A
confinement failure stops the run; it is never a result*).

On 2026-11-03 at 22:41, the shim refused every confined command and worker
of five running runs ("cannot isolate the command, so it does not run: tmpfs
/dev: EACCES"). Each caller read the bare exit 126 as its own kind of
outcome and saved it: `build-failed: 'pnpm run build': leerie: cannot
isolate…` in the final conformance, a conformer "worker crashed; phase
surfaced as advisory", "dependency install failed … no evidence either way",
a "non-fatal error" of the dependency capture; `resume` then skipped the
phase as already complete.

The signal is the shim's own descriptor (`--refusal-fd`), never the status
or the text:
- the real shim writes it on a refusal and never lets the command hold it,
  so a command's own exit 126 writes nothing and cannot forge it;
- every confined spawn kind (a measurement / an install / a store fetch
  through `_run_streaming`, a worker or the dependency capture through
  `_invoke`, an AST script through `_ts_script_run`) raises
  `ConfinementRefused` on it, which no `except Exception` reads;
- `main()` stops the run with `EXIT_CONFINEMENT_REFUSED`, the step's record
  untouched.
"""
from __future__ import annotations

import ast
import asyncio
import importlib.util
import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (REPO_ROOT, needs_landlock,
                            skip_without_landlock)

SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
REASON = ("leerie: cannot isolate the command, so it does not run: tmpfs "
          "/dev: EACCES")

# A shim that refuses, the way the real one does: its reason on stderr and on
# the refusal descriptor, exit 126. Imports nothing but os and sys and runs
# no program.
_REFUSING_SHIM = f'''
import os, sys
a = sys.argv[1:]
msg = {REASON!r} + "\\n"
sys.stderr.write(msg)
if "--refusal-fd" in a:
    os.write(int(a[a.index("--refusal-fd") + 1]), msg.encode())
sys.exit(126)
'''


def _shim_module():
    spec = importlib.util.spec_from_file_location("lk_refusal", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


_HAS_LANDLOCK = _shim_module().abi() >= 3


def _read_all(fd: int) -> bytes:
    os.set_blocking(fd, False)
    out = b""
    try:
        while chunk := os.read(fd, 65536):
            out += chunk
    except BlockingIOError:
        pass
    return out


# --- the real shim ----------------------------------------------------------

# The refusal this asserts is the shim's `/dev/console` check, which only a
# kernel that can confine ever reaches: without Landlock the shim refuses
# earlier, naming the missing `prctl` instead.
@needs_landlock
@skip_without_landlock
def test_the_shims_refusal_is_written_to_its_refusal_descriptor():
    """A real refusal of the real shim (a terminal on one of its
    descriptors, the first check it makes, needing no kernel feature)."""
    r, w = os.pipe()
    master, slave = os.openpty()
    try:
        p = subprocess.run(
            [sys.executable, "-I", "-S", str(SHIM), "--refusal-fd", str(w),
             "--rw", "/dev/null", "--ro", "/", "--", "/bin/true"],
            pass_fds=(w, slave), capture_output=True, text=True, timeout=60)
    finally:
        os.close(w)
        os.close(slave)
        os.close(master)
    got = _read_all(r).decode()
    os.close(r)
    assert p.returncode == 126
    assert "is a terminal" in got and got.strip() in p.stderr


@pytest.mark.skipif(not _HAS_LANDLOCK,
                    reason="the host kernel has no Landlock ABI 3")
def test_a_commands_own_126_writes_nothing_and_cannot_forge_it():
    """The inverse: the command exits 126 itself (an exec refused by
    permissions in the repository's own script), and it tries to write the
    descriptor first. The shim made it close-on-exec, so the command never
    holds it, and the status alone is not a refusal."""
    r, w = os.pipe()
    try:
        p = subprocess.run(
            [sys.executable, "-I", "-S", str(SHIM), "--refusal-fd", str(w),
             "--rw", "/dev/null", "--ro", "/", "--", sys.executable, "-I",
             "-S", "-c",
             "import os, sys\n"
             "try:\n"
             f"    os.write({w}, b'forged')\n"
             "except OSError:\n"
             "    pass\n"
             "sys.exit(126)"],
            pass_fds=(w,), capture_output=True, text=True, timeout=60)
    finally:
        os.close(w)
    got = _read_all(r)
    os.close(r)
    assert p.returncode == 126, p.stderr
    assert got == b""


def test_every_refusal_in_the_shim_goes_through_refused():
    """Structure, paired with the two tests above: no exit 126 is made but
    by `_refused`, so none can bypass the descriptor — or by `_taken`, a
    held-out path the tree has, which is not a refusal and writes its own
    descriptor (tests/test_held_out_path_taken_is_not_a_refusal.py)."""
    tree = ast.parse(SHIM.read_text())
    bad = []
    for fn in ast.walk(tree):
        if not isinstance(fn, ast.FunctionDef) or fn.name in ("_refused",
                                                              "_taken"):
            continue
        for n in ast.walk(fn):
            if isinstance(n, ast.Return) and isinstance(n.value, ast.Constant) \
                    and n.value.value == 126:
                bad.append(f"{fn.name}:{n.lineno}")
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) \
                    and n.func.attr == "_exit" and n.args \
                    and isinstance(n.args[0], ast.Constant) \
                    and n.args[0].value == 126:
                bad.append(f"{fn.name}:{n.lineno}")
    assert bad == []


def test_isolation_failure_writes_the_descriptor():
    m = _shim_module()
    r, w = os.pipe()
    m._REFUSAL_FD = w
    try:
        assert m._isolation_failed(OSError("tmpfs /dev: EACCES")) == 126
    finally:
        os.close(w)
    got = _read_all(r).decode()
    os.close(r)
    assert "cannot isolate the command" in got and "tmpfs /dev" in got


# --- every spawn kind -------------------------------------------------------

@pytest.fixture
def refusing(leerie, tmp_path, monkeypatch):
    shim = tmp_path / "refusing_shim.py"
    shim.write_text(_REFUSING_SHIM)
    monkeypatch.setattr(leerie, "LANDLOCK_SCRIPT", shim)
    return shim


def _grants(leerie, tmp_path: Path):
    return leerie.WorkerGrants(rw=(str(tmp_path),), ro=("/",))


def test_a_measurement_or_install_refused_stops_instead_of_failing(
        leerie, refusing, tmp_path):
    """`_run_streaming` carries every measurement, install and store fetch:
    the caller never sees `rc=126` to record as `build-failed` or "the
    install failed"."""
    with pytest.raises(leerie.ConfinementRefused) as ei:
        asyncio.run(leerie._run_streaming(
            ["/bin/true"], grants=_grants(leerie, tmp_path),
            cwd=str(tmp_path), timeout=60, verbosity="quiet",
            label="pnpm run build"))
    assert ei.value.what == "pnpm run build"
    assert ei.value.reason == REASON
    # Not an Exception: no caller's `except Exception` can record it.
    assert not isinstance(ei.value, Exception)


def test_a_worker_refused_stops_instead_of_crashing(leerie, refusing,
                                                    tmp_path):
    """`_invoke` carries every confined worker, the dependency capture's
    included: the caller never sees the `WorkerError` it downgraded to "worker
    crashed; phase surfaced as advisory" or a non-fatal capture error."""
    with pytest.raises(leerie.ConfinementRefused) as ei:
        asyncio.run(leerie._invoke(
            ["/bin/true"], str(tmp_path), 60, "dep-capture", tmp_path,
            "quiet", grants=_grants(leerie, tmp_path)))
    assert ei.value.what == "worker dep-capture"
    assert ei.value.reason == REASON


def test_an_ast_script_refused_stops(leerie, refusing, tmp_path,
                                     monkeypatch):
    monkeypatch.setattr(leerie, "_exec_lock_held", lambda: True)
    with pytest.raises(leerie.ConfinementRefused):
        leerie._ts_script_run(["/bin/true"], tree=tmp_path,
                              grants=_grants(leerie, tmp_path), timeout=60)


@pytest.mark.skipif(not _HAS_LANDLOCK,
                    reason="the host kernel has no Landlock ABI 3")
def test_a_commands_own_126_is_still_an_ordinary_failure(leerie, tmp_path):
    """The real shim, a command that exits 126 by itself: `_run_streaming`
    returns the status, as for any failing command."""
    rc, _tail = asyncio.run(leerie._run_streaming(
        ["/bin/sh", "-c", "exit 126"], grants=_grants(leerie, tmp_path),
        cwd=str(tmp_path), timeout=60, verbosity="quiet"))
    assert rc == 126


def test_every_confined_spawn_names_its_refusal_descriptor():
    """Every `_confined_argv` call in the orchestrator names `refusal_fd`:
    a spawn that omitted it would read a refusal as its command's status."""
    tree = ast.parse(LEERIE_PY.read_text())
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_confined_argv"]
    assert len(calls) >= 6
    missing = [n.lineno for n in calls
               if "refusal_fd" not in {k.arg for k in n.keywords}]
    assert missing == []


# --- the run stops ------------------------------------------------------------

@needs_landlock
@skip_without_landlock
def test_main_stops_the_run_with_the_typed_exit_and_records_nothing(
        leerie, monkeypatch, tmp_path):
    from tests.test_main_exception_arms import _run_main, _state_json
    from tests.conftest import fake_claude_on_path

    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "-q", "--allow-empty", "-m", "init"],
                   cwd=r, check=True)
    monkeypatch.chdir(r)
    monkeypatch.delenv("LEERIE_STATE_DIR", raising=False)
    fake_claude_on_path(tmp_path, monkeypatch)
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    captured = []

    async def _no_capture(*a, **kw):
        captured.append(a)
    monkeypatch.setattr(leerie, "capture_repo_deps", _no_capture)

    async def _boom(args, caps, run_dir, st, *a, **kw):
        st.data["final_conformance_done"] = False
        st.save()
        # The step's result would be recorded after this spawn returned.
        raise leerie.ConfinementRefused("pnpm run build", REASON)

    exit_code, run_dir = _run_main(leerie, monkeypatch, r, "main-refused",
                                   orchestrate_stub=_boom)
    assert exit_code == leerie.EXIT_CONFINEMENT_REFUSED == 78
    assert (run_dir / "orchestrator.exit_code").read_text().strip() == "78"
    data = _state_json(run_dir)
    assert data.get("final_conformance_done") is False
    assert "finished_at" not in data
    # No dependency capture: it is a confined worker and would refuse again.
    assert captured == []
