"""`_run_mise_install`'s `mise ls --current --json` version-capture call
must be bounded (DESIGN §6½): the same unguarded-`communicate()` shape
already fixed for `mise install` itself, at the second and last such
call site in the file.

Without a timeout, a process that never exits on this call would hang
worktree provisioning indefinitely. This exercises the real
`_run_mise_install` coroutine with `mise install` stubbed out (so the
test needs no real mise binary) and `asyncio.create_subprocess_exec`
stubbed to return a process whose `communicate()` never resolves,
proving the surrounding call is wrapped in a bounded wait rather than
a bare `await proc.communicate()`.
"""
from __future__ import annotations

import asyncio
import time

import pytest


def _make_state(leerie, tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "_test-run"
    (leerie_root / "runs" / run_id / "logs").mkdir(parents=True, exist_ok=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test"}
    st.save()
    return st


class _HangingProc:
    """Mimics an `asyncio.subprocess.Process` whose `communicate()` never
    returns on its own — only cancellation (as driven by `asyncio.wait_for`
    inside `run_proc`) unblocks it."""

    returncode = None
    pid = 999999

    async def communicate(self):
        await asyncio.sleep(3600)
        return b"", b""  # pragma: no cover - never reached

    async def wait(self):
        # `_terminate_proc_tree`'s cleanup path reaps the leader after
        # signalling it; a real killed process would exit promptly.
        return -9


@pytest.fixture(autouse=True)
def _stub_mise_install(leerie, monkeypatch):
    async def _fake_run_streaming(cmd, **kwargs):
        return (0, "ok")
    monkeypatch.setattr(leerie, "_run_streaming", _fake_run_streaming)


def test_mise_ls_hang_does_not_block_forever(leerie, tmp_path, monkeypatch):
    (tmp_path / "mise.toml").write_text('[tools]\nnode = "20.11.0"\n')
    st = _make_state(leerie, tmp_path)
    log_dir = st.run_dir / "logs"

    async def _fake_create_subprocess_exec(*args, **kwargs):
        return _HangingProc()
    monkeypatch.setattr(asyncio, "create_subprocess_exec",
                        _fake_create_subprocess_exec)
    # Bound the test itself well below the real timeout so a regression to
    # an unbounded wait fails fast instead of hanging the suite.
    monkeypatch.setattr(leerie, "MISE_LS_TIMEOUT", 0.2)

    logged = []
    monkeypatch.setattr(leerie, "log", lambda msg: logged.append(msg))

    start = time.monotonic()
    asyncio.run(leerie._run_mise_install(tmp_path, log_dir, st, None))
    elapsed = time.monotonic() - start

    assert elapsed < 5, f"provisioning blocked for {elapsed}s on a hung mise ls"
    assert any("skipping version capture" in m for m in logged)
    # The skip-on-timeout path returns before setting `mise_versions` —
    # same shape as the existing non-zero-exit skip a few lines below it.
    assert "mise_versions" not in st.data.get("provision", {})
