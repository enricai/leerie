"""Tests for partition_files() and recursive_decompose() (DESIGN §5½ (P1)).

partition_files: deterministic chunker — 100% coverage + 0 overlap guaranteed
by construction. Tests verify this on small and large inputs.

recursive_decompose: stubbed via monkeypatching claude_p to return fake
fit_judge / splitter responses. Verifies:
  - A well-fit subtask (score >= 0.70) is returned unsplit as a leaf.
  - An oversized subtask recurses until children pass the threshold.
  - depth >= decompose_max_depth (5) terminates recursion even at low score.
  - 2 consecutive no-progress rounds accept the subtask as a leaf.
  - Every judge/split call goes through st.bump_workers (worker_count rises).
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import math
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


@pytest.fixture(scope="session")
def leerie():
    spec = importlib.util.spec_from_file_location("leerie", LEERIE_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# partition_files
# ---------------------------------------------------------------------------

def test_partition_files_empty(leerie):
    assert leerie.partition_files([], 8) == []


def test_partition_files_single_chunk(leerie):
    files = ["a.py", "b.py", "c.py"]
    chunks = leerie.partition_files(files, 8)
    assert chunks == [["a.py", "b.py", "c.py"]]


def test_partition_files_exact_multiple(leerie):
    files = [f"f{i}.py" for i in range(16)]
    chunks = leerie.partition_files(files, 8)
    assert len(chunks) == 2
    assert chunks[0] == files[:8]
    assert chunks[1] == files[8:]


def test_partition_files_partial_last_chunk(leerie):
    files = [f"f{i}.py" for i in range(10)]
    chunks = leerie.partition_files(files, 8)
    assert len(chunks) == 2
    assert len(chunks[0]) == 8
    assert len(chunks[1]) == 2


def test_partition_files_coverage_100_percent(leerie):
    """Every file appears in exactly one chunk."""
    files = [f"src/module{i}.ts" for i in range(29)]
    chunks = leerie.partition_files(files, 8)
    flat = [f for chunk in chunks for f in chunk]
    assert sorted(flat) == sorted(files)


def test_partition_files_zero_overlap(leerie):
    """No file appears in more than one chunk."""
    files = [f"src/f{i}.py" for i in range(25)]
    chunks = leerie.partition_files(files, 8)
    seen: set[str] = set()
    for chunk in chunks:
        for f in chunk:
            assert f not in seen, f"{f!r} appears in multiple chunks"
            seen.add(f)


def test_partition_files_chunk_size_1(leerie):
    """chunk_size=1 produces one file per chunk."""
    files = ["a.py", "b.py", "c.py"]
    chunks = leerie.partition_files(files, 1)
    assert chunks == [["a.py"], ["b.py"], ["c.py"]]


def test_partition_files_preserves_order(leerie):
    files = [f"file_{i:03d}.py" for i in range(20)]
    chunks = leerie.partition_files(files, 7)
    flat = [f for chunk in chunks for f in chunk]
    assert flat == files


def test_partition_files_chunk_size_zero_treated_as_one_chunk(leerie):
    """chunk_size < 1 returns all files in a single chunk (degenerate guard)."""
    files = ["a.py", "b.py"]
    chunks = leerie.partition_files(files, 0)
    assert chunks == [["a.py", "b.py"]]


# ---------------------------------------------------------------------------
# recursive_decompose helpers
# ---------------------------------------------------------------------------

def _make_state(leerie, caps):
    """Build a minimal State-like object with bump_workers tracking."""
    st = MagicMock()
    st.data = {"worker_count": 0}

    def bump(c):
        st.data["worker_count"] += 1
        count = st.data["worker_count"]
        if count > c.get("max_total_workers", 200):
            raise leerie.WorkerError("budget exhausted")

    st.bump_workers = MagicMock(side_effect=bump)
    return st


def _make_caps(leerie, **overrides):
    caps = {
        "max_total_workers": 200,
        "decompose_max_depth": leerie.DEFAULT_CAPS["decompose_max_depth"],
        "decompose_fit_threshold": leerie.DEFAULT_CAPS["decompose_fit_threshold"],
        "decompose_noprogress_rounds": leerie.DEFAULT_CAPS["decompose_noprogress_rounds"],
    }
    caps.update(overrides)
    return caps


def _fit_response(score: float) -> dict:
    return {
        "score": score,
        "rationale": f"score={score}",
        "diffuse": "" if score >= 0.70 else "too broad",
        "confidence": {
            "fit": 8.5, "basis": "test",
            "falsifiers_tested": ["x"], "contradictions_reconciled": [],
            "gap_to_close": {},
        },
    }


def _split_response(parent_id: str, n: int) -> dict:
    return {
        "children": [
            {
                "id": f"{parent_id}-{i + 1}",
                "title": f"child {i + 1}",
                "success_criteria_seed": f"criterion {i + 1}",
                "files_likely_touched": [f"f{i}.py"],
            }
            for i in range(n)
        ],
    }


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# recursive_decompose — well-fit subtask is a leaf
# ---------------------------------------------------------------------------

def test_recursive_decompose_well_fit_is_leaf(leerie):
    """A subtask scoring >= 0.70 is returned as-is without splitting."""
    subtask = {"id": "t-001", "title": "Well-fit task",
               "success_criteria_seed": "crit", "files_likely_touched": ["a.py"]}
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    call_count = 0

    async def fake_claude_p(*args, schema_key, **kwargs):
        nonlocal call_count
        call_count += 1
        if schema_key == "fit_judge":
            return _fit_response(0.85)
        pytest.fail("splitter should not be called for a well-fit subtask")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            subtask, 0, st, caps, models, efforts, Path("/tmp")))

    assert leaves == [subtask]
    assert call_count == 1        # only the fit_judge call
    assert st.bump_workers.call_count == 1


# ---------------------------------------------------------------------------
# recursive_decompose — oversized subtask recurses
# ---------------------------------------------------------------------------

def test_recursive_decompose_oversized_recurses(leerie):
    """An oversized subtask (score < 0.70) is split; children at >= 0.70 are leaves."""
    parent = {
        "id": "big",
        "title": "Big migration",
        "success_criteria_seed": "all files migrated",
        "files_likely_touched": ["a.py", "b.py", "c.py"],
    }
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    judge_calls: list[str] = []
    split_calls: list[str] = []

    async def fake_claude_p(*args, schema_key, sid="", **kwargs):
        if schema_key == "fit_judge":
            judge_calls.append(sid)
            # Children have depth suffix > 0 (e.g. "fit-judge-big-1-d1");
            # the parent sid is "fit-judge-big-d0" with "-d0" suffix.
            if sid.endswith("-d0"):
                return _fit_response(0.30)  # parent: low score → split
            return _fit_response(0.85)       # children: high score → leaf
        elif schema_key == "splitter":
            split_calls.append(sid)
            return _split_response("big", 3)
        pytest.fail(f"unexpected schema_key {schema_key!r}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # One parent judge + 3 child judges = 4 judge calls.
    assert len(judge_calls) == 4
    # One split call for the parent.
    assert len(split_calls) == 1
    # Three leaves from the split.
    assert len(leaves) == 3
    # bump_workers: 1 (parent judge) + 1 (splitter) + 3 (child judges) = 5.
    assert st.bump_workers.call_count == 5


# ---------------------------------------------------------------------------
# recursive_decompose — depth cap terminates recursion
# ---------------------------------------------------------------------------

def test_recursive_decompose_depth_cap(leerie):
    """At depth >= decompose_max_depth (5), subtask is accepted as leaf regardless
    of fit score."""
    subtask = {"id": "deep", "title": "Deep",
               "success_criteria_seed": "crit",
               "files_likely_touched": ["a.py"]}
    caps = _make_caps(leerie, decompose_max_depth=5)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    judge_calls = []

    async def fake_claude_p(*args, schema_key, **kwargs):
        if schema_key == "fit_judge":
            judge_calls.append(1)
            return _fit_response(0.10)  # always low
        pytest.fail("splitter called at depth cap")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            subtask, 5, st, caps, models, efforts, Path("/tmp")))

    assert leaves == [subtask]
    assert len(judge_calls) == 1   # only the judge at depth 5; no split


# ---------------------------------------------------------------------------
# recursive_decompose — no-progress guard
# ---------------------------------------------------------------------------

def test_recursive_decompose_noprogress_guard(leerie, capsys):
    """If noprogress_rounds consecutive rounds produce no improvement, accept as leaf
    and emit a warning via log()."""
    parent = {
        "id": "stuck",
        "title": "Stuck subtask",
        "success_criteria_seed": "crit",
        "files_likely_touched": ["a.py"],  # small — coupled-minority path
    }
    caps = _make_caps(leerie, decompose_noprogress_rounds=2)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    # Parent score 0.30; child (from splitter) also 0.30 (no progress).
    # After 2 no-progress rounds (child at depth 1, grandchild at depth 2),
    # the grandchild is accepted as leaf.
    call_log: list[tuple[str, str]] = []

    async def fake_claude_p(*args, schema_key, sid="", **kwargs):
        call_log.append((schema_key, sid))
        if schema_key == "fit_judge":
            return _fit_response(0.30)  # always 0.30 — never progresses
        elif schema_key == "splitter":
            return _split_response("stuck", 1)  # produces exactly one child
        pytest.fail(f"unexpected schema_key {schema_key!r}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # Must return exactly one leaf (the stuck subtask or one of its descendants).
    assert len(leaves) == 1
    # bump_workers must have been called (judge + splitter at each level).
    assert st.bump_workers.call_count >= 1
    # The no-progress guard must emit a warning via log() (prints to stdout).
    out = capsys.readouterr().out
    assert "no-progress guard" in out, (
        f"Expected 'no-progress guard' warning in stdout; got: {out!r}"
    )


# ---------------------------------------------------------------------------
# recursive_decompose — migration: partition_files owns files, splitter labels
# ---------------------------------------------------------------------------

def test_recursive_decompose_migration_partition_owns_files_splitter_only_labels(leerie):
    """When files_likely_touched > 8, partition_files() owns the file→chunk
    assignment (100% coverage), and the splitter is invoked ONLY in label-only
    mode to title each chunk (DESIGN §5½ "the LLM only labels"). The splitter
    never decides which files go where."""
    big_files = [f"src/migrate_{i:03d}.ts" for i in range(24)]
    parent = {
        "id": "sweep",
        "title": "Date-fns sweep",
        "success_criteria_seed": "all 24 files migrated",
        "files_likely_touched": big_files,
    }
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    splitter_calls: list[str] = []

    async def fake_claude_p(*args, schema_key, sid="", user_prompt="", **kwargs):
        if schema_key == "fit_judge":
            if sid.endswith("-d0"):
                return _fit_response(0.20)   # parent: low → split via partition
            return _fit_response(0.85)        # children: high → leaf
        elif schema_key == "splitter":
            splitter_calls.append(sid)
            # Label-only: echo distinct titles/criteria per pre-assigned id.
            import re
            ids = re.findall(r'"id": "(sweep-\d+)"', user_prompt)
            return {"children": [
                {"id": i, "title": f"Labeled {i}",
                 "success_criteria_seed": f"crit {i}"} for i in ids]}
        pytest.fail(f"unexpected schema_key {schema_key!r}")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # Splitter IS called (label-only), exactly once for the migration parent.
    assert len(splitter_calls) == 1
    assert splitter_calls[0].startswith("splitter-label-")
    # 24 files / 8 per chunk = 3 children.
    assert len(leaves) == 3
    # partition_files owns coverage: every original file in exactly one leaf.
    leaf_files = [f for leaf in leaves for f in leaf.get("files_likely_touched", [])]
    assert sorted(leaf_files) == sorted(big_files)


def test_recursive_decompose_migration_children_have_distinct_labels(leerie):
    """G1 regression: migration chunk children must NOT copy the parent's
    identical title/criteria — each chunk gets a distinct label (from the
    splitter, or the deterministic fallback)."""
    big_files = [f"src/m{i:02d}.ts" for i in range(24)]
    parent = {"id": "mig", "title": "Parent title",
              "success_criteria_seed": "parent criteria",
              "files_likely_touched": big_files}
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    async def fake_claude_p(*args, schema_key, sid="", user_prompt="", **kwargs):
        if schema_key == "fit_judge":
            return _fit_response(0.20 if sid.endswith("-d0") else 0.85)
        # splitter returns per-id labels
        import re
        ids = re.findall(r'"id": "(mig-\d+)"', user_prompt)
        return {"children": [
            {"id": i, "title": f"Distinct {i}",
             "success_criteria_seed": f"c {i}"} for i in ids]}

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    titles = [leaf["title"] for leaf in leaves]
    crits = [leaf["success_criteria_seed"] for leaf in leaves]
    assert len(set(titles)) == len(titles)   # all distinct
    assert len(set(crits)) == len(crits)
    assert "Parent title" not in titles       # not a bare parent-copy


def test_recursive_decompose_migration_label_fallback_on_splitter_failure(leerie):
    """G1 §12 belt-and-suspenders: if the label-only splitter crashes, every
    chunk still gets a DISTINCT deterministic title (never identical, never a
    crash)."""
    big_files = [f"src/m{i:02d}.ts" for i in range(24)]
    parent = {"id": "mig", "title": "Parent",
              "success_criteria_seed": "seed",
              "files_likely_touched": big_files}
    caps = _make_caps(leerie)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    async def fake_claude_p(*args, schema_key, sid="", **kwargs):
        if schema_key == "fit_judge":
            return _fit_response(0.20 if sid.endswith("-d0") else 0.85)
        raise leerie.WorkerError("simulated splitter crash")

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        leaves = _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    titles = [leaf["title"] for leaf in leaves]
    assert len(leaves) == 3
    assert len(set(titles)) == len(titles)   # deterministic fallback is distinct


# ---------------------------------------------------------------------------
# recursive_decompose — C0 regression: claude_p called with the REAL signature
# ---------------------------------------------------------------------------

def test_recursive_decompose_calls_claude_p_with_full_signature(leerie):
    """C0 regression. The two claude_p call sites in recursive_decompose must
    pass every REQUIRED keyword-only arg of the real claude_p (cwd, autonomous,
    caps, ...). A permissive ``**kwargs`` stub hid a missing-arg bug that
    crashed every live run with TypeError. This stub binds each call against
    the real signature, so a missing required arg fails here."""
    import inspect
    real_sig = inspect.signature(leerie.claude_p)

    parent = {"id": "sig", "title": "t", "success_criteria_seed": "c",
              "files_likely_touched": ["a.py"]}  # coupled path exercises splitter
    caps = _make_caps(leerie, decompose_max_depth=1)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    bound_ok: list[str] = []

    async def faithful_claude_p(*args, **kwargs):
        # Raises TypeError if a required kw-only arg is missing — exactly like
        # the real claude_p would.
        real_sig.bind(*args, **kwargs)
        bound_ok.append(kwargs.get("schema_key"))
        if kwargs.get("schema_key") == "fit_judge":
            return _fit_response(0.30)
        return _split_response("sig", 1)

    with patch.object(leerie, "claude_p", new=faithful_claude_p):
        _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))

    # Both a fit_judge and a splitter call bound successfully.
    assert "fit_judge" in bound_ok
    assert "splitter" in bound_ok


# ---------------------------------------------------------------------------
# recursive_decompose — bump_workers called for every judge/split
# ---------------------------------------------------------------------------

def test_recursive_decompose_bump_workers_every_call(leerie):
    """Each claude_p invocation (fit_judge or splitter) is preceded by
    st.bump_workers — the worker budget is decremented for every call."""
    parent = {
        "id": "bw",
        "title": "t",
        "success_criteria_seed": "c",
        "files_likely_touched": ["a.py"],  # coupled path
    }
    caps = _make_caps(leerie, decompose_max_depth=1)  # stop after one split
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    claude_calls = [0]

    async def fake_claude_p(*args, schema_key, **kwargs):
        claude_calls[0] += 1
        # bump_workers must already have been called once more than before.
        assert st.bump_workers.call_count == claude_calls[0]
        if schema_key == "fit_judge":
            return _fit_response(0.30)
        return _split_response("bw", 1)

    with patch.object(leerie, "claude_p", new=fake_claude_p):
        _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp")))


# ---------------------------------------------------------------------------
# recursive_decompose — G2: repo-map reaches fit_judge / splitter
# ---------------------------------------------------------------------------

def test_recursive_decompose_injects_repo_map_into_worker_prompts(leerie):
    """G2: when a repo_map is passed, each fit_judge/splitter prompt is grounded
    with a per-node ranked subgraph (rank_repo_map re-ranked to the node's
    files). Uses a stubbed rank_repo_map so the test is parser-independent."""
    parent = {"id": "g2", "title": "t", "success_criteria_seed": "c",
              "files_likely_touched": ["a.py"]}  # coupled path → splitter too
    caps = _make_caps(leerie, decompose_max_depth=1)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    SENTINEL = "RANKED-SUBGRAPH-SENTINEL-42"
    prompts_seen: dict[str, str] = {}

    async def fake_claude_p(*args, schema_key, user_prompt="", **kwargs):
        prompts_seen[schema_key] = user_prompt
        if schema_key == "fit_judge":
            return _fit_response(0.30)
        return _split_response("g2", 1)

    with patch.object(leerie, "claude_p", new=fake_claude_p), \
         patch.object(leerie, "rank_repo_map", return_value=SENTINEL):
        _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp"),
            repo_map={"files": {"a.py": ["f"]}, "refs": {}}))

    assert SENTINEL in prompts_seen.get("fit_judge", "")
    assert SENTINEL in prompts_seen.get("splitter", "")


def test_recursive_decompose_no_repo_map_when_none(leerie):
    """G2: with repo_map=None (skip_repo_map / build failure), no ranked
    subgraph is injected and rank_repo_map is never called."""
    parent = {"id": "g2b", "title": "t", "success_criteria_seed": "c",
              "files_likely_touched": ["a.py"]}
    caps = _make_caps(leerie, decompose_max_depth=1)
    st = _make_state(leerie, caps)
    models = {"fit_judge": "opus", "splitter": "opus"}
    efforts = {"fit_judge": "high", "splitter": "high"}

    prompts_seen: dict[str, str] = {}

    async def fake_claude_p(*args, schema_key, user_prompt="", **kwargs):
        prompts_seen[schema_key] = user_prompt
        if schema_key == "fit_judge":
            return _fit_response(0.30)
        return _split_response("g2b", 1)

    with patch.object(leerie, "claude_p", new=fake_claude_p), \
         patch.object(leerie, "rank_repo_map",
                      side_effect=AssertionError("must not be called")):
        _run(leerie.recursive_decompose(
            parent, 0, st, caps, models, efforts, Path("/tmp"),
            repo_map=None))

    assert "RANKED REPO-MAP" not in prompts_seen.get("fit_judge", "")
    assert "RANKED REPO-MAP" not in prompts_seen.get("splitter", "")
