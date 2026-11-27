"""A validity-tree build failure never discards a set (DESIGN §8): driven
through the real `_write_acceptance_set`, `_acceptance_tree` and
`phase_acceptance_write`; only the git add / install / runner seams and
`claude_p` are replaced. Synthetic names only."""
from __future__ import annotations

import asyncio

import pytest

from tests.test_acceptance_gate import (CONTROL_TEST, DEFECT_TEST, EFFORTS,
                                        MODELS, _caps, _repo, _st,
                                        _writer_stub)


@pytest.fixture(autouse=True)
def _clear_memos(leerie, monkeypatch):
    leerie._DEPS_INSTALLED.clear()
    leerie._DEPS_FAILED.clear()
    monkeypatch.setattr(leerie, "_host_cpu_psi", lambda *a, **k: 0.0)
    yield
    leerie._DEPS_INSTALLED.clear()
    leerie._DEPS_FAILED.clear()


def _setup(leerie, tmp_path, monkeypatch):
    repo, head = _repo(tmp_path)
    st = _st(leerie, tmp_path, repo, head)
    _writer_stub(leerie, monkeypatch, {1: {
        "acc/test_defect_sum.py": ("defect", DEFECT_TEST, ["sums"]),
        "acc/test_control_zero.py": ("control", CONTROL_TEST, ["zero"]),
    }})
    return st, head


def _stub_runner(leerie, monkeypatch):
    """The runner's verdicts (defect fails, control passes) stand in for
    the sandboxed pytest, which not every host can run; the tree build
    under test stays real."""
    async def run(st_, caps, tree, rel, *a, **kw):
        obs = kw.get("observed")
        if obs is not None:
            obs["failing_cases"] = []
        return "defect" not in rel
    monkeypatch.setattr(leerie, "_run_acceptance_file", run)


def _caps_r(leerie, retries):
    caps = _caps(leerie, 1)
    caps["deps_install_retries"] = retries
    return caps


def _write_set(leerie, st, head, retries=1):
    return asyncio.run(leerie._write_acceptance_set(
        1, st.data["task"], st, _caps_r(leerie, retries), MODELS, EFFORTS,
        head))


def _phase(leerie, st, retries=1):
    return asyncio.run(leerie.phase_acceptance_write(
        st.data["task"], st, _caps_r(leerie, retries), MODELS, EFFORTS))


def _failed_add(calls, stderr="fatal: synthetic-add-failure"):
    async def add(st_, wt, rev):
        calls.append(rev)
        return type("R", (), {"returncode": 128, "stderr": stderr,
                              "stdout": ""})()
    return add


def _assert_unmeasured(acc, needle):
    assert acc["sets"] == []
    assert acc["discarded"] == []
    (u,) = acc["unmeasured"]
    assert u["index"] == 1
    assert needle in u["error"]
    assert leerie_all_fixed(acc) is False


_LEERIE = None


def leerie_all_fixed(acc):
    return _LEERIE._acceptance_all_fixed_by_execution(acc)


@pytest.fixture(autouse=True)
def _bind(leerie):
    global _LEERIE
    _LEERIE = leerie


def test_a_persistent_add_failure_is_unmeasured_with_the_stderr(
        leerie, tmp_path, monkeypatch):
    st, head = _setup(leerie, tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(leerie, "_worktree_add_detached_async",
                        _failed_add(calls))
    s = _write_set(leerie, st, head, retries=2)
    assert len(calls) == 3                  # 1 + 2 bounded retries
    assert s["valid"] is False
    assert "fatal: synthetic-add-failure" in s["infrastructure"]
    assert "reason" not in s
    acc = _phase(leerie, st, retries=0)
    _assert_unmeasured(acc, "fatal: synthetic-add-failure")


def test_a_persistent_install_failure_is_unmeasured_with_the_reason(
        leerie, tmp_path, monkeypatch):
    st, head = _setup(leerie, tmp_path, monkeypatch)
    real_deps = leerie._ensure_worktree_deps

    async def deps(path, *a, **k):
        if not str(path).endswith("-view"):
            return await real_deps(path, *a, **k)
        import os
        leerie._DEPS_FAIL_REASON[os.path.realpath(path)] = \
            "synthetic-install-failure"
        return False
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", deps)
    acc = _phase(leerie, st, retries=1)
    _assert_unmeasured(acc, "synthetic-install-failure")


def test_a_transient_add_failure_is_retried_to_a_valid_set(
        leerie, tmp_path, monkeypatch):
    st, head = _setup(leerie, tmp_path, monkeypatch)
    _stub_runner(leerie, monkeypatch)
    real_add = leerie._worktree_add_detached_async
    calls = []

    async def flaky(st_, wt, rev):
        calls.append(rev)
        if len(calls) == 1:
            return type("R", (), {"returncode": 1, "stderr": "transient",
                                  "stdout": ""})()
        return await real_add(st_, wt, rev)
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", flaky)
    acc = _phase(leerie, st, retries=1)
    assert len(calls) == 2
    assert len(acc["sets"]) == 1
    assert acc["discarded"] == []
    assert not acc.get("unmeasured")


def test_a_failed_build_is_not_memoised_for_the_next_build(
        leerie, tmp_path, monkeypatch):
    st, head = _setup(leerie, tmp_path, monkeypatch)
    _stub_runner(leerie, monkeypatch)
    real_add = leerie._worktree_add_detached_async
    calls = []

    async def first_fails(st_, wt, rev):
        calls.append(rev)
        if len(calls) == 1:
            return type("R", (), {"returncode": 9, "stderr": "synthetic-first",
                                  "stdout": ""})()
        return await real_add(st_, wt, rev)
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", first_fails)
    first = _write_set(leerie, st, head, retries=0)
    assert "synthetic-first" in first["infrastructure"]
    second = _write_set(leerie, st, head, retries=0)
    assert len(calls) == 2              # built again, not a cached failure
    assert second["valid"] is True
    assert "infrastructure" not in second
