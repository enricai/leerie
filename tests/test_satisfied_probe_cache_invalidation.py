"""Pin the base-tree sha invalidation of `satisfied_probe_cache` against a
real, moving git repo (DESIGN §6 "the mid-run sibling case" — the
`satisfied_probe` worker judges the CURRENT working tree / HEAD only, so a
cached verdict is valid only while HEAD is unchanged).

Scope note: `tests/test_filter_satisfied_subtasks.py` already covers
hit/miss/no-cache-on-crash under a FIXED tree (one commit, one sha) plus a
`test_stale_sha_invalidates_cache_and_reprobes` case using a synthetic
`"deadbeef-not-current"` sha that never corresponds to a real commit. This
file is deliberately narrower and uses a *moving* real repo — HEAD actually
advances from sha A to sha B via a second commit, mirroring a sibling run
merging (or reverting) the deliverable between a pause and a resume — so a
regression that silently reintroduces the invalidation check (e.g. an
accidental delete of the `base_sha` comparison) is caught against the exact
temporal scenario DESIGN §8 describes, not just a hand-written bogus sha.

Both stale directions are asserted, matching the function's documented
fail-safe bias (its docstring: "the probe is tuned to prefer [re-probing]
over a false-positive that would silently delete real work"):
  - a stale `satisfied=True` cache hit must not silently drop a subtask
    that is no longer satisfied on the new tree (silent lost work)
  - a stale `satisfied=False` cache hit must not silently keep a subtask
    that is now satisfied on the new tree (wasted spend, but also asserted
    here since the fix path is the same code)

A cache entry with a missing/malformed `base_sha` is treated as a miss and
re-probed (fail-safe-to-reprobe), the same discipline as an unrelated sha.
"""
from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-satisfied-probe-cache-invalidation"
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


def _git(args: list[str], cwd: Path) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, check=True,
                          capture_output=True, text=True)
    return out.stdout.strip()


def _init_git_repo(path: Path) -> str:
    """Create a minimal real git repo at `path` and return HEAD's sha."""
    path.mkdir(parents=True, exist_ok=True)
    _git(["init", "-q"], path)
    _git(["config", "user.email", "t@example.com"], path)
    _git(["config", "user.name", "t"], path)
    (path / "a.py").write_text("x = 1\n")
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", "init"], path)
    return _git(["rev-parse", "HEAD"], path)


def _commit_more(path: Path, filename: str = "b.py") -> str:
    """Advance HEAD in an already-initialized repo with a new commit.
    Returns the new HEAD sha."""
    (path / filename).write_text("y = 2\n")
    _git(["add", "-A"], path)
    _git(["commit", "-q", "-m", f"add {filename}"], path)
    return _git(["rev-parse", "HEAD"], path)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# A cache entry recorded at sha A is honored while HEAD is still A.
# ---------------------------------------------------------------------------

def test_cache_honored_while_head_unchanged(leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha_a = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "already on HEAD",
                     "checked": ["a.py"], "base_sha": sha_a},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    async def unreached(**_kw):
        raise AssertionError("claude_p must not be called on a live-sha hit")
    monkeypatch.setattr(leerie, "claude_p", unreached)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    # HEAD never moved (still sha_a) — the cached verdict is honored: the
    # subtask is dropped with zero claude_p calls. The single "ready" plan
    # is emptied by the drop, so the no-work route fires (res is a
    # domain -> basis map, not None).
    assert res is not None and "d" in res
    assert [s["id"] for s in plans[0]["subtasks"]] == []
    assert st.data["dropped_subtasks"]["feat-001"]["reason"] == \
        "already_satisfied"
    assert st.data["satisfied_probe_cache"]["feat-001"]["base_sha"] == sha_a


# ---------------------------------------------------------------------------
# After a new commit moves HEAD from A to B, the same entry (recorded at A)
# is discarded and the subtask IS re-probed — a stale `satisfied=True` must
# not silently drop a subtask that is no longer satisfied on the new tree.
# ---------------------------------------------------------------------------

def test_stale_satisfied_entry_reprobed_after_head_moves(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha_a = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "was satisfied at A",
                     "checked": ["a.py"], "base_sha": sha_a},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    sha_b = _commit_more(repo)
    assert sha_b != sha_a

    calls: list[str] = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        # On the new tree the subtask is genuinely no longer satisfied —
        # the stale cached "satisfied=True" would have wrongly dropped it
        # (silent lost work) had invalidation not fired.
        return {"satisfied": False, "evidence": "not satisfied on new tree"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert res is None
    assert calls == ["satisfied_probe-feat-001"], (
        "a cache entry recorded at the old HEAD must trigger exactly one "
        "re-probe once HEAD has moved")
    # The subtask survives — the stale drop did NOT happen.
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001"]
    # The rewritten entry carries the NEW sha, not the stale one.
    rewritten = st.data["satisfied_probe_cache"]["feat-001"]
    assert rewritten["base_sha"] == sha_b
    assert rewritten["satisfied"] is False
    assert rewritten["evidence"] == "not satisfied on new tree"


# ---------------------------------------------------------------------------
# The other stale direction: a stale `satisfied=False` entry must not
# silently keep a subtask that is now satisfied on the new tree.
# ---------------------------------------------------------------------------

def test_stale_unsatisfied_entry_reprobed_after_head_moves(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha_a = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": False, "evidence": "not satisfied at A",
                     "base_sha": sha_a},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    sha_b = _commit_more(repo)

    calls: list[str] = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        # A sibling merged the deliverable — now satisfied on the new tree.
        return {"satisfied": True, "evidence": "satisfied on new tree",
                "checked": ["b.py"]}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert calls == ["satisfied_probe-feat-001"], (
        "a stale not-satisfied entry must also be discarded and re-probed, "
        "not assumed still valid")
    # The fresh probe found it satisfied — dropped for real this time.
    assert [s["id"] for s in plans[0]["subtasks"]] == []
    dropped = st.data["dropped_subtasks"]["feat-001"]
    assert dropped["reason"] == "already_satisfied"
    assert dropped["evidence"] == "satisfied on new tree"
    rewritten = st.data["satisfied_probe_cache"]["feat-001"]
    assert rewritten["base_sha"] == sha_b
    assert rewritten["satisfied"] is True


# ---------------------------------------------------------------------------
# A cache entry with a missing/malformed sha is treated as a miss.
# ---------------------------------------------------------------------------

def test_missing_base_sha_treated_as_miss_and_reprobed(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "no sha recorded"},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    calls: list[str] = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        return {"satisfied": False, "evidence": "freshly probed"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert calls == ["satisfied_probe-feat-001"], (
        "a cache entry with no base_sha at all must not be trusted")
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001"]


def test_malformed_base_sha_treated_as_miss_and_reprobed(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "malformed sha",
                     "base_sha": None},
        "feat-002": {"satisfied": True, "evidence": "malformed sha",
                     "base_sha": 12345},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]

    calls: list[str] = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        return {"satisfied": False, "evidence": "freshly probed"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    assert sorted(calls) == ["satisfied_probe-feat-001",
                             "satisfied_probe-feat-002"], (
        "None or non-string base_sha must never equality-match a real sha")
    assert sorted(s["id"] for s in plans[0]["subtasks"]) == \
        ["feat-001", "feat-002"]
