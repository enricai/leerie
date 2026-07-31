"""Tests for `_invoke`'s per-invocation `active_token` env threading
(DESIGN §6 *Multi-token rotation* — the prerequisite that makes both
start-of-run selection and mid-run failover real: switching which token a
worker authenticates with needs no container restart because the env var
is set fresh on every subprocess spawn).
"""
from __future__ import annotations

import asyncio

import pytest


class _FakeProc:
    """Minimal stand-in for asyncio.create_subprocess_exec's return value —
    just enough for _invoke's streaming/cleanup machinery not to explode
    before we can inspect the captured env kwarg."""

    def __init__(self):
        self.pid = 12345
        self.returncode = 0
        self.stdin = None
        self.stdout = _EmptyStream()
        self.stderr = _EmptyStream()

    async def wait(self):
        return 0

    def kill(self):
        pass

    def send_signal(self, *a):
        pass


class _EmptyStream:
    async def readline(self):
        return b""

    async def read(self, *a):
        return b""


def _patch_invoke_internals(leerie, monkeypatch, captured_envs: list):
    """Stub create_subprocess_exec to record the `env` kwarg and return a
    fake process that immediately looks finished, so _invoke's stream-
    reading loop exits fast without needing a real subprocess."""

    async def fake_create_subprocess_exec(*args, **kwargs):
        captured_envs.append(kwargs.get("env"))
        return _FakeProc()

    monkeypatch.setattr(
        leerie.asyncio, "create_subprocess_exec", fake_create_subprocess_exec)


class TestActiveTokenEnvThreading:
    def test_active_token_overrides_env_var(self, leerie, monkeypatch, tmp_path):
        captured = []
        _patch_invoke_internals(leerie, monkeypatch, captured)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "stale-token")

        async def run():
            try:
                await leerie._invoke(
                    ["claude", "-p"], cwd="/work", timeout=60,
                    sid="test-sid", leerie_dir=tmp_path, verbosity="quiet",
                    active_token="fresh-token",
                )
            except Exception:
                # _invoke's streaming loop may raise once the fake stream
                # dries up with no result event — irrelevant to this test,
                # which only cares about the env kwarg captured above.
                pass

        asyncio.run(run())
        assert len(captured) == 1
        env = captured[0]
        assert env is not None
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fresh-token"

    def test_no_active_token_leaves_env_none(self, leerie, monkeypatch, tmp_path):
        """Byte-identical to pre-feature behavior when active_token is not
        passed — env=None, full os.environ inheritance."""
        captured = []
        _patch_invoke_internals(leerie, monkeypatch, captured)
        monkeypatch.delenv("LEERIE_WORKER_DEBUG", raising=False)

        async def run():
            try:
                await leerie._invoke(
                    ["claude", "-p"], cwd="/work", timeout=60,
                    sid="test-sid", leerie_dir=tmp_path, verbosity="quiet",
                )
            except Exception:
                pass

        asyncio.run(run())
        assert captured[0] is None

    def test_active_token_composes_with_worker_debug(
            self, leerie, monkeypatch, tmp_path):
        """LEERIE_WORKER_DEBUG and active_token are orthogonal — a debug
        env still needs to carry the currently-selected token."""
        captured = []
        _patch_invoke_internals(leerie, monkeypatch, captured)
        monkeypatch.setenv("LEERIE_WORKER_DEBUG", "1")

        async def run():
            try:
                await leerie._invoke(
                    ["claude", "-p"], cwd="/work", timeout=60,
                    sid="test-sid", leerie_dir=tmp_path, verbosity="quiet",
                    active_token="fresh-token",
                )
            except Exception:
                pass

        asyncio.run(run())
        env = captured[0]
        assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "fresh-token"
        assert env["DEBUG"] == "*"
        assert env["ANTHROPIC_LOG"] == "debug"

    def test_switching_active_token_changes_next_spawn_only(
            self, leerie, monkeypatch, tmp_path):
        """The prerequisite claim: switching which token is active changes
        the NEXT spawn's env, not any already-captured one."""
        captured = []
        _patch_invoke_internals(leerie, monkeypatch, captured)

        async def run():
            for tok in ("token-1", "token-2"):
                try:
                    await leerie._invoke(
                        ["claude", "-p"], cwd="/work", timeout=60,
                        sid="test-sid", leerie_dir=tmp_path, verbosity="quiet",
                        active_token=tok,
                    )
                except Exception:
                    pass

        asyncio.run(run())
        assert len(captured) == 2
        assert captured[0]["CLAUDE_CODE_OAUTH_TOKEN"] == "token-1"
        assert captured[1]["CLAUDE_CODE_OAUTH_TOKEN"] == "token-2"
