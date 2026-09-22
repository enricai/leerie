"""Tests for the typed not-satisfied probe verdict (DESIGN §8 *A "not
satisfied" verdict carries a typed reason*).

Motivation (measured, 2026-09-22): 58% of all not-satisfied
satisfied-probe verdicts across the barnacle corpus (433/753 on
v0.29/v0.30) rested solely on "the exact file the planner named does not
exist" — the planner invents a fresh artifact path each run, so a re-run
of an already-satisfied task structurally never comes up empty. The fix
types the verdict: `unsatisfied_reason` + `equivalent_coverage_exists`,
with `_probe_drop_reason` as the single pure consumer.

Covers:
  - `_probe_drop_reason` truth table: only `satisfied: true` (→
    "already_satisfied") and `artifact_missing` ∧
    `equivalent_coverage_exists` (→ "equivalent_coverage") drop; every
    other reason × coverage combination, and absent fields, keep
  - executing `_filter_satisfied_subtasks` (not reading its source): an
    equivalent-coverage verdict removes the subtask from `plans` and
    records reason "equivalent_coverage" + evidence; disagreeing
    combinations survive
  - the typed fields round-trip through `satisfied_probe_cache`: a
    cached equivalent-coverage verdict replays the drop with zero
    claude_p calls
  - `SCHEMAS["satisfied_probe"]` accepts the new fields and rejects an
    out-of-enum `unsatisfied_reason`
  - `_filter_provably_false_wiring_defects` predicate 2 treats an
    `equivalent_coverage` drop's tags as satisfied-on-base, same as
    `already_satisfied`

Substance discipline (CLAUDE.md "structure vs substance"): the
parametrized keep-cases make the two fields DISAGREE (right reason /
wrong coverage flag, and vice versa), so a consumer that read only one
field — or none — fails; the drop assertions check the recorded reason
VALUE, not key presence.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.conftest import _run, HAS_JSONSCHEMA, validate_or_fallback_required

try:
    import jsonschema  # type: ignore
except ImportError:
    jsonschema = None  # type: ignore


_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    # claude_p derives the checkout write-denial from this
    # (_repo_write_denials); State.__new__ skips __init__, so it
    # must be set explicitly or both that and the §12 cwd guard
    # silently no-op.
    st.repo_root = "/leerie-test-user-repo"
    st.run_id = "test-run-typed-reason"
    st.run_dir = run_dir
    st.path = run_dir / "state.json"
    st.data = {
        # Judgment workers run in a disposable worktree (DESIGN §12
        # *Judgment-worker isolation*); `_judgment_cwd` raises rather
        # than silently falling back to the real checkout.
        "planning_worktree": str(run_dir / "worktrees" / "planning"),
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


def _patch_probe(leerie, monkeypatch, verdicts: dict):
    calls: dict[str, int] = {}

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        stid = sid.split("satisfied_probe-", 1)[-1]
        calls[stid] = calls.get(stid, 0) + 1
        return verdicts.get(stid, {"satisfied": False, "evidence": "n/a"})

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


def _init_git_repo(path: Path) -> str:
    """Minimal real git repo — `_filter_satisfied_subtasks` scopes its
    cache to `_branch_head_sha`."""
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@example.com"],
                   cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    (path / "a.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "init"], cwd=path,
                   check=True)
    out = subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                         check=True, capture_output=True, text=True)
    return out.stdout.strip()


# ---------------------------------------------------------------------------
# _probe_drop_reason truth table
# ---------------------------------------------------------------------------

def test_satisfied_true_maps_to_already_satisfied(leerie):
    assert leerie._probe_drop_reason(
        {"satisfied": True, "evidence": "on HEAD"}) == "already_satisfied"


def test_artifact_missing_with_coverage_maps_to_equivalent_coverage(leerie):
    assert leerie._probe_drop_reason({
        "satisfied": False, "evidence": "named file absent, covered",
        "unsatisfied_reason": "artifact_missing",
        "equivalent_coverage_exists": True,
    }) == "equivalent_coverage"


@pytest.mark.parametrize("verdict", [
    # right reason, wrong/absent coverage flag
    {"satisfied": False, "unsatisfied_reason": "artifact_missing",
     "equivalent_coverage_exists": False},
    {"satisfied": False, "unsatisfied_reason": "artifact_missing"},
    # coverage flag set, wrong reason — a consumer reading only
    # equivalent_coverage_exists would wrongly drop these
    {"satisfied": False, "unsatisfied_reason": "behavior_gap",
     "equivalent_coverage_exists": True},
    {"satisfied": False, "unsatisfied_reason": "partially_met",
     "equivalent_coverage_exists": True},
    {"satisfied": False, "unsatisfied_reason": "cannot_verify",
     "equivalent_coverage_exists": True},
    # old-shaped verdict: no typed fields at all
    {"satisfied": False, "evidence": "missing"},
    # coverage flag truthy-but-not-True must not drop (typed comparison)
    {"satisfied": False, "unsatisfied_reason": "artifact_missing",
     "equivalent_coverage_exists": "yes"},
])
def test_every_other_combination_keeps(leerie, verdict):
    assert leerie._probe_drop_reason(verdict) is None


# ---------------------------------------------------------------------------
# consumer executed: drop vs survive through _filter_satisfied_subtasks
# ---------------------------------------------------------------------------

def test_equivalent_coverage_verdict_drops_and_records(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("test-001", provides=["cov-tag"]),
                           _sub("feat-001")]}]
    _patch_probe(leerie, monkeypatch, {
        "test-001": {"satisfied": False,
                     "evidence": "named spec absent; equivalent suite cited",
                     "unsatisfied_reason": "artifact_missing",
                     "equivalent_coverage_exists": True},
        "feat-001": {"satisfied": False, "evidence": "missing",
                     "unsatisfied_reason": "behavior_gap"},
    })
    res = _run(leerie._filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None  # feat-001 survives → not the no-work route
    assert [s["id"] for s in plans[0]["subtasks"]] == ["feat-001"]
    rec = st.data["dropped_subtasks"]["test-001"]
    assert rec["reason"] == "equivalent_coverage"
    assert rec["evidence"] == "named spec absent; equivalent suite cited"
    assert rec["provides"] == ["cov-tag"]


def test_disagreeing_fields_survive_through_consumer(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("test-001"), _sub("test-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "test-001": {"satisfied": False, "evidence": "absent, not covered",
                     "unsatisfied_reason": "artifact_missing",
                     "equivalent_coverage_exists": False},
        "test-002": {"satisfied": False, "evidence": "behavior wrong",
                     "unsatisfied_reason": "behavior_gap",
                     "equivalent_coverage_exists": True},
    })
    res = _run(leerie._filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert [s["id"] for s in plans[0]["subtasks"]] == ["test-001",
                                                       "test-002"]
    assert "dropped_subtasks" not in st.data or not st.data.get(
        "dropped_subtasks")


def test_all_equivalent_coverage_routes_no_work(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("test-001")]}]
    _patch_probe(leerie, monkeypatch, {
        "test-001": {"satisfied": False, "evidence": "covered elsewhere",
                     "unsatisfied_reason": "artifact_missing",
                     "equivalent_coverage_exists": True},
    })
    res = _run(leerie._filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is not None and "d" in res  # no_work_map → no-work route


# ---------------------------------------------------------------------------
# cache round-trip
# ---------------------------------------------------------------------------

def test_typed_fields_cached_and_replayed_without_reprobe(
        leerie, tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("test-001"), _sub("feat-001")]}]
    calls = _patch_probe(leerie, monkeypatch, {
        "test-001": {"satisfied": False, "evidence": "covered elsewhere",
                     "unsatisfied_reason": "artifact_missing",
                     "equivalent_coverage_exists": True},
        "feat-001": {"satisfied": False, "evidence": "missing"},
    })
    _run(leerie._filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    cached = st.data["satisfied_probe_cache"]["test-001"]
    assert cached["unsatisfied_reason"] == "artifact_missing"
    assert cached["equivalent_coverage_exists"] is True
    assert cached["base_sha"] == sha

    # Second sweep (resume shape): same base_sha → cached verdict must
    # replay the drop with zero fresh probe calls for that sid.
    calls.clear()
    st2 = _make_state(leerie, tmp_path / "run2")
    st2.data["satisfied_probe_cache"] = {"test-001": dict(cached)}
    plans2 = [{"domain": "d", "status": "ready",
               "subtasks": [_sub("test-001"), _sub("feat-001")]}]
    _run(leerie._filter_satisfied_subtasks(
        plans2, repo, st2, _CAPS, _MODELS, _EFFORTS))
    assert calls.get("test-001", 0) == 0
    assert st2.data["dropped_subtasks"]["test-001"]["reason"] == (
        "equivalent_coverage")


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def test_schema_accepts_typed_fields(leerie):
    schema = leerie.SCHEMAS["satisfied_probe"]
    good = {"satisfied": False, "evidence": "e",
            "unsatisfied_reason": "artifact_missing",
            "equivalent_coverage_exists": True}
    assert validate_or_fallback_required(schema, good)


@pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")
def test_schema_rejects_out_of_enum_reason(leerie):
    schema = leerie.SCHEMAS["satisfied_probe"]
    bad = {"satisfied": False, "evidence": "e",
           "unsatisfied_reason": "file_not_found"}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, schema)


# ---------------------------------------------------------------------------
# wiring-defect falsifier predicate 2
# ---------------------------------------------------------------------------

def test_wiring_falsifier_treats_equivalent_coverage_as_satisfied(leerie):
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [{"id": "feat-001", "provides": [],
                            "requires": [], "depends_on": []}]}]
    dropped = {"test-001": {"reason": "equivalent_coverage",
                            "provides": ["cov-tag"], "evidence": "e"}}
    defects = [{"kind": "broken_by_drop", "sid": "feat-001",
                "tag_or_dep": "cov-tag", "why": "provider dropped"}]
    surviving, notes = leerie._filter_provably_false_wiring_defects(
        plans, defects, dropped)
    assert surviving == []
    assert notes  # the discard is explained, not silent
