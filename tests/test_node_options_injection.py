"""Tests for _is_node_repo and the NODE_OPTIONS worker_env injection (P9).

Node/V8 defaults to a ~4.2 GiB heap ceiling regardless of the larger
cgroup memory.max leerie grants each worker, causing spurious build OOM
aborts on Node repos even when the container has ample headroom.
"""
from __future__ import annotations


# ---- _is_node_repo ---------------------------------------------------------

def test_is_node_repo_detects_package_json(leerie, tmp_path):
    (tmp_path / "package.json").write_text("{}")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_detects_pnpm_lock(leerie, tmp_path):
    (tmp_path / "pnpm-lock.yaml").write_text("")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_detects_package_lock(leerie, tmp_path):
    (tmp_path / "package-lock.json").write_text("{}")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_detects_yarn_lock(leerie, tmp_path):
    (tmp_path / "yarn.lock").write_text("")
    assert leerie._is_node_repo(str(tmp_path)) is True


def test_is_node_repo_false_for_non_node_repo(leerie, tmp_path):
    (tmp_path / "requirements.txt").write_text("")
    assert leerie._is_node_repo(str(tmp_path)) is False


def test_is_node_repo_false_for_empty_dir(leerie, tmp_path):
    assert leerie._is_node_repo(str(tmp_path)) is False


# ---- worker_env NODE_OPTIONS injection (via _invoke, stubbed subprocess) --

class _FakeStream:
    async def readline(self):
        return b""

    async def read(self):
        return b""


class _FakeProc:
    def __init__(self):
        self.stdin = None
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.returncode = 0
        self.pid = 12345

    def send_signal(self, sig):
        pass

    async def wait(self):
        return 0


async def _capture_env_and_run(monkeypatch, leerie, cwd, worker_memory_max_bytes):
    captured = {}

    async def fake_create_subprocess_exec(*cmd, **kwargs):
        captured["env"] = kwargs.get("env")
        return _FakeProc()

    monkeypatch.setattr(leerie.asyncio, "create_subprocess_exec",
                         fake_create_subprocess_exec)

    import tempfile
    from pathlib import Path
    with tempfile.TemporaryDirectory() as td:
        leerie_dir = Path(td)
        (leerie_dir / "logs").mkdir()
        try:
            await leerie._invoke(
                ["echo", "hi"], cwd=cwd, timeout=5, sid="test-sid",
                leerie_dir=leerie_dir, verbosity="quiet",
                worker_memory_max_bytes=worker_memory_max_bytes,
            )
        except Exception:
            pass
    return captured.get("env")


def test_invoke_sets_node_options_for_node_repo(leerie, tmp_path, monkeypatch):
    import asyncio
    (tmp_path / "package.json").write_text("{}")
    env = asyncio.run(_capture_env_and_run(
        monkeypatch, leerie, str(tmp_path),
        worker_memory_max_bytes=8 * 1024**3))
    assert env is not None
    # Derived from the constant, not retyped: `_invoke` reserves
    # `_NODE_HEAP_HEADROOM_BYTES` for Node's own non-heap footprint plus the
    # resident `claude` process. A literal here silently goes stale the next
    # time that reserve is re-derived — which is exactly what happened to
    # this line when it still read 6144 (= 8192 - 2048).
    reserve_mb = leerie._NODE_HEAP_HEADROOM_BYTES // (1024 * 1024)
    expected = 8 * 1024 - reserve_mb
    assert env["NODE_OPTIONS"] == f"--max-old-space-size={expected}"


def test_invoke_omits_node_options_for_non_node_repo(leerie, tmp_path, monkeypatch):
    import asyncio
    env = asyncio.run(_capture_env_and_run(
        monkeypatch, leerie, str(tmp_path),
        worker_memory_max_bytes=8 * 1024**3))
    assert env is None or "NODE_OPTIONS" not in env


def test_invoke_omits_node_options_when_memory_max_is_none(leerie, tmp_path, monkeypatch):
    import asyncio
    (tmp_path / "package.json").write_text("{}")
    env = asyncio.run(_capture_env_and_run(
        monkeypatch, leerie, str(tmp_path),
        worker_memory_max_bytes=None))
    assert env is None or "NODE_OPTIONS" not in env


def test_invoke_clamps_node_options_for_small_explicit_memory_max(leerie, tmp_path, monkeypatch):
    # --worker-memory-max / LEERIE_WORKER_MEMORY_MAX can be set below 2 GiB
    # explicitly (resolve_worker_memory_max has no minimum, unlike the
    # auto-derive path's 8 GiB floor) — subtracting the reserve outright
    # would go negative and hand V8 an invalid --max-old-space-size.
    # This asserts the 256 floor literally rather than deriving it: the
    # floor IS the constant under test here, and it only engages while the
    # reserve exceeds the cap (1024 MiB). That makes this test sensitive to
    # the reserve shrinking below ~768 MiB, which is intentional — a reserve
    # that small is itself the bug `test_node_heap_headroom_is_2432_mib`
    # exists to catch.
    import asyncio
    (tmp_path / "package.json").write_text("{}")
    env = asyncio.run(_capture_env_and_run(
        monkeypatch, leerie, str(tmp_path),
        worker_memory_max_bytes=1 * 1024**3))
    assert env is not None
    assert env["NODE_OPTIONS"] == "--max-old-space-size=256"
