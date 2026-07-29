"""Dedicated pin for `satisfied_probe_cache` read/write seams inside
`filter_satisfied_subtasks`'s `probe_one` (DESIGN §6 "The satisfied-probe
sweep needs finer-than-phase granularity" / bugfix-005).

Scope is deliberately narrow — a single checkable condition: does
`probe_one` honor and populate the cache correctly? This does NOT
duplicate `tests/test_filter_satisfied_subtasks.py`'s broader resume /
state-surface-parity coverage (stale-sha invalidation, the reported
partial-cache resume scenario, drop/dependency-pruning semantics) — see
that file for those. This file's four cases:

  1. a cached `satisfied` verdict drops the subtask with ZERO claude_p
     calls for that sid
  2. a cached not-satisfied verdict keeps the subtask with ZERO calls
  3. an uncached sid is probed exactly once and the verdict (both
     satisfied and not-satisfied outcomes) lands in
     st.data["satisfied_probe_cache"][sid] with satisfied/evidence/checked
  4. a claude_p raising WorkerError keeps the subtask and writes NO
     cache entry for it

Anti-vacuity discipline (CLAUDE.md checklist item, from the zombie-reaper
harness lesson): the uncached-subtask test does NOT pre-seed a cache entry
for the sid it asserts is freshly probed, and the crash test asserts the
cache key is ABSENT (not merely that the subtask survived) — otherwise
both would pass against code that never consults the cache. All claude_p
assertions are per-sid call counts, never aggregate.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
from pathlib import Path

_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-satisfied-probe-cache"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {
        "telemetry": {"calls": 0, "cost_usd": 0.0,
                      "input_tokens": 0, "output_tokens": 0},
        "verbosity": "quiet",
        "worker_count": 0,
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    st.path.write_text("{}")
    return st


def _sub(sid, **kw):
    s = {"id": sid, "title": sid, "success_criteria_seed": f"{sid} done"}
    s.update(kw)
    return s


def _init_git_repo(path: Path) -> str:
    """Create a minimal real git repo at `path` and return HEAD's sha —
    `filter_satisfied_subtasks` scopes the cache to `_branch_head_sha`."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                    cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path, check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          check=True, capture_output=True, text=True)
    return out.stdout.strip()


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# 1 & 2: cache hit skips the semaphore and claude_p entirely, for both
# a satisfied and a not-satisfied cached verdict.
# ---------------------------------------------------------------------------

def test_cached_satisfied_drops_with_zero_claude_p_calls(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "already on HEAD",
                     "checked": ["a.py"], "base_sha": sha},
    }
    # A second, uncached-but-unsatisfied subtask keeps the plan non-empty so
    # this test stays scoped to the cache mechanism, not no_work_map routing
    # (out of scope per this file's scope_note).
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]

    calls: list[str] = []

    async def counting_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        return {"satisfied": False, "evidence": "still needed"}
    monkeypatch.setattr(leerie, "claude_p", counting_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    # claude_p was invoked only for the UNCACHED sid — never for feat-001.
    assert calls == ["satisfied_probe-feat-002"]
    assert res is None
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-002"]
    assert st.data["dropped_subtasks"]["feat-001"]["reason"] == "already_satisfied"
    assert st.data["dropped_subtasks"]["feat-001"]["evidence"] == "already on HEAD"


def test_cached_not_satisfied_keeps_with_zero_claude_p_calls(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-002": {"satisfied": False, "evidence": "still needed",
                     "base_sha": sha},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-002")]}]

    calls: list[str] = []

    async def counting_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        raise AssertionError("claude_p must not be called on a cache hit")
    monkeypatch.setattr(leerie, "claude_p", counting_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert calls == []
    assert res is None
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-002"]
    assert "dropped_subtasks" not in st.data


# ---------------------------------------------------------------------------
# 3: an uncached sid is probed exactly once and the resulting verdict is
# persisted for BOTH outcomes — asserted per-sid, not in aggregate.
# ---------------------------------------------------------------------------

def test_uncached_sid_probed_once_and_verdict_persisted_both_outcomes(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    # Deliberately no pre-seeded cache entry for either sid — anti-vacuity:
    # if the cache read were deleted from probe_one, this test must still
    # exercise a genuine first-time probe (it already does, by construction),
    # but the per-sid call-count assertions below are what actually catch a
    # regression that removes the *write*.
    assert "satisfied_probe_cache" not in st.data
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-sat"), _sub("feat-unsat")]}]

    calls: dict[str, int] = {}

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        stid = sid.split("satisfied_probe-", 1)[-1]
        calls[stid] = calls.get(stid, 0) + 1
        if stid == "feat-sat":
            return {"satisfied": True, "evidence": "done", "checked": ["a.py"]}
        return {"satisfied": False, "evidence": "not yet"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert calls == {"feat-sat": 1, "feat-unsat": 1}

    cache = st.data["satisfied_probe_cache"]
    assert cache["feat-sat"] == {
        "satisfied": True, "evidence": "done",
        "checked": ["a.py"], "base_sha": sha,
    }
    assert cache["feat-unsat"] == {
        "satisfied": False, "evidence": "not yet",
        "checked": [], "base_sha": sha,
    }


# ---------------------------------------------------------------------------
# 4: a claude_p crash (WorkerError) keeps the subtask and writes NO cache
# entry — the branch that must stay uncached so a resume re-probes it.
# ---------------------------------------------------------------------------

def test_crash_keeps_subtask_and_writes_no_cache_entry(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-crash")]}]

    calls: dict[str, int] = {}

    async def crashing_claude_p(*, user_prompt, sid, **_kw):
        stid = sid.split("satisfied_probe-", 1)[-1]
        calls[stid] = calls.get(stid, 0) + 1
        raise leerie.WorkerError("probe boom")
    monkeypatch.setattr(leerie, "claude_p", crashing_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert calls == {"feat-crash": 1}
    assert res is None
    # subtask kept (fail-safe)
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-crash"]
    # anti-vacuity: assert the cache KEY is absent, not merely that the
    # subtask survived — a version that both keeps AND caches the crash as
    # a false "kept" verdict would still pass a survives-only assertion.
    assert "feat-crash" not in st.data.get("satisfied_probe_cache", {})


# ---------------------------------------------------------------------------
# 5: the mid-sweep durability guarantee (Fix 1 / the leerie run's bugfix-001).
# probe_one must st.save() each verdict AS IT RETURNS, not only at the
# post-gather flush — otherwise a pause between one probe finishing and the
# whole sweep completing discards that verdict and resume re-probes it.
#
# The falsifier: removing the `st.save()` after the `cache[sid]={...}` write
# in probe_one makes this test fail — with the save gone, the on-disk
# state.json carries no verdict until gather_or_cancel completes, so the
# in-flight read below sees an empty cache.
# ---------------------------------------------------------------------------

def test_verdict_reaches_disk_before_the_sweep_completes(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-fast"), _sub("feat-slow")]}]

    # feat-slow blocks until feat-fast's verdict has been SAVED to disk, then
    # inspects the on-disk state.json. Because feat-slow's coroutine has not
    # returned at that point, the sweep-final flush (after gather_or_cancel)
    # cannot have run yet — so any verdict seen on disk got there via the
    # per-verdict save inside probe_one.
    #
    # The event is fired from a wrapped st.save() (not from inside claude_p),
    # so it signals AFTER probe_one has written cache[sid] and saved — the
    # exact seam under test. It fires only once feat-fast's verdict is present
    # in st.data, ignoring the pre-sweep and bump_workers saves.
    fast_saved = asyncio.Event()
    inspected = asyncio.Event()
    seen_on_disk: dict[str, dict] = {}
    real_save = st.save

    def wrapped_save():
        real_save()
        if "feat-fast" in st.data.get("satisfied_probe_cache", {}):
            fast_saved.set()
    monkeypatch.setattr(st, "save", wrapped_save)

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        stid = sid.split("satisfied_probe-", 1)[-1]
        if stid == "feat-fast":
            return {"satisfied": False, "evidence": "still needed",
                    "checked": ["a.py"]}
        # feat-slow: wait until feat-fast's verdict is durable, then read disk.
        await fast_saved.wait()
        on_disk = json.loads(st.path.read_text())
        seen_on_disk.update(on_disk.get("satisfied_probe_cache", {}))
        inspected.set()
        return {"satisfied": False, "evidence": "slow", "checked": []}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert inspected.is_set()
    # feat-fast's verdict was on disk while feat-slow's probe was still in
    # flight — i.e. before the post-gather flush ran.
    assert seen_on_disk.get("feat-fast") == {
        "satisfied": False, "evidence": "still needed",
        "checked": ["a.py"], "base_sha": sha,
    }
