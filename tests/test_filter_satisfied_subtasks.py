"""Tests for filter_satisfied_subtasks() — the phase-3 per-subtask
already-satisfied gate (DESIGN §8 *Already-satisfied subtask elimination*).

Covers:
  - a subtask the probe marks satisfied is removed from `plans` and
    recorded in state.data["dropped_subtasks"] with reason
    "already_satisfied" + evidence
  - an unsatisfied subtask survives
  - all-satisfied → returns a no_work_map (domain → basis) so the caller
    routes to _finish_no_work_run; the map's basis is drop-derived, NOT
    the planner's confidence.basis
  - a blocked plan with 0 subtasks does NOT trigger the no-work route
  - --skip-satisfied-check short-circuits (no probe, no drops, returns None)
  - a subtask with no success_criteria_seed is never probed (survives)
  - a probe crash (WorkerError) fails safe — subtask survives, no drop
  - SCHEMAS["satisfied_probe"] validates good / rejects malformed payloads

The schema tests use a HAS_JSONSCHEMA gate with a manual structural fallback
(mirroring test_dep_capture_schema.py / test_fit_judge_schema.py) so CI
without jsonschema installed still catches drift — it is not a declared
dependency.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


_CAPS = {"max_parallel": 4, "max_total_workers": 999}
_MODELS = {"satisfied_probe": "sonnet"}
_EFFORTS = {"satisfied_probe": None}


def _make_state(leerie, run_dir: Path):
    st = leerie.State.__new__(leerie.State)
    st.run_id = "test-run-satisfied"
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


def _patch_probe(leerie, monkeypatch, verdicts: dict):
    """Patch claude_p so each subtask sid returns verdicts[sid] (a dict
    with at least {satisfied}). A sid mapped to the sentinel "CRASH"
    raises WorkerError."""
    async def fake_claude_p(*, user_prompt, sid, **_kw):
        # sid is like "satisfied_probe-<subtask-id>"
        stid = sid.split("satisfied_probe-", 1)[-1]
        v = verdicts.get(stid, {"satisfied": False, "evidence": "n/a"})
        if v == "CRASH":
            raise leerie.WorkerError("probe boom")
        return v

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)


def _run(coro):
    return asyncio.run(coro)


# ---------------------------------------------------------------------------
# drop / survive
# ---------------------------------------------------------------------------

def test_satisfied_subtask_dropped_and_recorded(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "already on HEAD",
                     "checked": ["a.py"]},
        "feat-002": {"satisfied": False, "evidence": "missing"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    # not all dropped → None (proceed to schedule)
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    assert surviving == ["feat-002"]
    dropped = st.data["dropped_subtasks"]
    assert "feat-001" in dropped
    assert dropped["feat-001"]["reason"] == "already_satisfied"
    assert dropped["feat-001"]["evidence"] == "already on HEAD"
    assert "feat-002" not in dropped


def test_unsatisfied_all_survive_no_drop_key(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": False, "evidence": "x"},
        "feat-002": {"satisfied": False, "evidence": "y"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 2
    assert "dropped_subtasks" not in st.data


# ---------------------------------------------------------------------------
# all-dropped → no_work_map routing
# ---------------------------------------------------------------------------

def test_all_satisfied_returns_no_work_map(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              # confidence.basis is the planner's "I found work" rationale;
              # the returned map must NOT echo it.
              "confidence": {"basis": "I found real work to do"},
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "done"},
        "feat-002": {"satisfied": True, "evidence": "done"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is not None
    assert "feature-implementation" in res
    basis = res["feature-implementation"]
    assert "already satisfied" in basis.lower()
    assert basis != "I found real work to do"
    assert plans[0]["subtasks"] == []


def test_blocked_empty_plan_does_not_route_no_work(leerie, tmp_path,
                                                   monkeypatch):
    # A ready plan fully dropped, but another plan is blocked with 0
    # subtasks → must NOT route to no-work (blocked must reach schedule's
    # all-blocked die). Mirrors detect_no_work's ready-only guard.
    st = _make_state(leerie, tmp_path / "run")
    plans = [
        {"domain": "feature-implementation", "status": "ready",
         "subtasks": [_sub("feat-001")]},
        {"domain": "testing", "status": "blocked", "subtasks": []},
    ]
    _patch_probe(leerie, monkeypatch,
                 {"feat-001": {"satisfied": True, "evidence": "done"}})
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None  # blocked plan present → no no-work route


# ---------------------------------------------------------------------------
# skip flag / no-criteria / crash-safety
# ---------------------------------------------------------------------------

def test_skip_flag_short_circuits(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    st.data["skip_satisfied_check"] = True
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]

    async def boom(**_kw):
        raise AssertionError("probe must not be spawned when skip is set")
    monkeypatch.setattr(leerie, "claude_p", boom)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 1


def test_no_criteria_subtask_never_probed(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [{"id": "feat-001", "title": "no crit",
                            "success_criteria_seed": "  "}]}]

    async def boom(**_kw):
        raise AssertionError("subtask with blank criteria must not be probed")
    monkeypatch.setattr(leerie, "claude_p", boom)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert len(plans[0]["subtasks"]) == 1


def test_probe_crash_fails_safe_keeps_subtask(leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": "CRASH",
        "feat-002": {"satisfied": True, "evidence": "done"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    # feat-001 crashed → kept (fail-safe); feat-002 satisfied → dropped
    assert surviving == ["feat-001"]
    assert "feat-002" in st.data["dropped_subtasks"]
    assert "feat-001" not in st.data.get("dropped_subtasks", {})


# ---------------------------------------------------------------------------
# Fix 5 — sibling-invalidation context (DESIGN §8 *The sibling-invalidation
# case*). The probe payload must carry every OTHER subtask's declared surface
# (provides + files_likely_touched), keyed so a subtask never sees itself, and
# a probe that returns satisfied=false on an invalidation must keep the subtask.
# ---------------------------------------------------------------------------

def test_probe_payload_carries_surviving_siblings_excluding_self(
        leerie, tmp_path, monkeypatch):
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-007", files_likely_touched=["src/tests/nav-parity.test.ts"],
             provides=["nav-parity-guarded"]),
        _sub("feat-001", files_likely_touched=["messages/en.json"],
             provides=["nav-keys-added"]),
    ]}]
    seen: dict[str, dict] = {}

    async def capturing_claude_p(*, user_prompt, sid, **_kw):
        stid = sid.split("satisfied_probe-", 1)[-1]
        seen[stid] = json.loads(user_prompt.split("SUBTASK:\n", 1)[1]
                                .rsplit("\n\n", 1)[0])
        return {"satisfied": False, "evidence": "n/a"}
    monkeypatch.setattr(leerie, "claude_p", capturing_claude_p)

    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))

    # Each probe sees the OTHER subtask as a surviving sibling, never itself.
    t007_sibs = {s["id"] for s in seen["test-007"]["surviving_siblings"]}
    assert t007_sibs == {"feat-001"}
    feat_sibs = {s["id"] for s in seen["feat-001"]["surviving_siblings"]}
    assert feat_sibs == {"test-007"}
    # The sibling surface carries provides + files (for the keep judgment).
    fe = seen["test-007"]["surviving_siblings"][0]
    assert fe["provides"] == ["nav-keys-added"]
    assert fe["files_likely_touched"] == ["messages/en.json"]


def test_sibling_invalidation_verdict_keeps_the_dropped_test(
        leerie, tmp_path, monkeypatch):
    """The funeralworks shape: test-007 passes on the base tree (would be
    dropped) but feat-001 will invalidate it. When the probe returns
    satisfied=false (its sibling-invalidation guard firing), the test subtask
    is KEPT, not silently dropped."""
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready", "subtasks": [
        _sub("test-007", files_likely_touched=["src/tests/nav-parity.test.ts"]),
        _sub("feat-001", files_likely_touched=["messages/en.json"],
             provides=["nav-keys-added"]),
    ]}]
    _patch_probe(leerie, monkeypatch, {
        # Probe declines the drop because feat-001 would invalidate the test.
        "test-007": {"satisfied": False,
                     "evidence": "feat-001 adds keys this parity test guards"},
        "feat-001": {"satisfied": False, "evidence": "keys missing"},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    assert "test-007" in surviving  # kept, not silently dropped
    assert "test-007" not in st.data.get("dropped_subtasks", {})


def test_budget_exhaustion_propagates_not_swallowed(leerie, tmp_path,
                                                    monkeypatch):
    """bump_workers' WorkerError (budget exhaustion) is the hard backstop —
    it must propagate OUT of filter_satisfied_subtasks, NOT be swallowed by
    the probe-crash fail-safe. Otherwise a run past its worker budget would
    silently continue. (Contrast: a claude_p crash IS caught → subtask
    survives.)"""
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    # max_total_workers=0 → the first bump_workers() raises WorkerError.
    caps = {"max_parallel": 4, "max_total_workers": 0}
    # claude_p should never be reached (bump_workers raises first); make it
    # loud if it is.
    async def unreached(**_kw):
        raise AssertionError("claude_p reached despite budget exhaustion")
    monkeypatch.setattr(leerie, "claude_p", unreached)

    with pytest.raises(leerie.WorkerError):
        _run(leerie.filter_satisfied_subtasks(
            plans, tmp_path, st, caps, _MODELS, _EFFORTS))
    # No drops recorded — the run aborts rather than silently keeping/dropping.
    assert "dropped_subtasks" not in st.data


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------

def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests must
    pass in both modes so CI without jsonschema installed still catches
    drift."""
    schema = leerie.SCHEMAS["satisfied_probe"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["satisfied"], bool)
    assert isinstance(instance["evidence"], str)
    if "checked" in instance:
        assert isinstance(instance["checked"], list)
        for path in instance["checked"]:
            assert isinstance(path, str)


def test_schema_accepts_good_payload(leerie):
    _validate(leerie, {"satisfied": True, "evidence": "cited",
                       "checked": ["a.py"]})
    # checked is optional
    _validate(leerie, {"satisfied": False, "evidence": "missing"})


def test_schema_rejects_malformed(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; rejection requires a validator")
    schema = leerie.SCHEMAS["satisfied_probe"]
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"evidence": "no satisfied field"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate({"satisfied": "yes", "evidence": "x"}, schema)
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"satisfied": True, "evidence": "x", "extra": 1}, schema)


# ---------------------------------------------------------------------------
# dropped ids must not leave dangling depends_on
# (DESIGN §5 *Id-vanishing operations*)
# ---------------------------------------------------------------------------

def test_dropped_subtask_dep_is_pruned(leerie, tmp_path, monkeypatch):
    """A dropped id can no longer satisfy any dependent, so inbound
    `depends_on` references to it must be pruned.

    Without the prune this is a live crash: schedule() drops the edge
    silently and validate_plan then die()s the run — the reported failure,
    reachable via this filter independently of the recursion.
    """
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [
                  _sub("feat-001"),
                  _sub("feat-002", depends_on=["feat-001"]),
                  _sub("feat-003", depends_on=["feat-001", "feat-002"]),
              ]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "merged by sibling PR",
                     "checked": ["a.py"]},
    })
    res = _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None

    surv = plans[0]["subtasks"]
    ids = {s["id"] for s in surv}
    assert ids == {"feat-002", "feat-003"}

    by_id = {s["id"]: s for s in surv}
    assert by_id["feat-002"]["depends_on"] == []
    # The non-dropped dep survives; only the dropped id is removed.
    assert by_id["feat-003"]["depends_on"] == ["feat-002"]

    # No dangling edge survives to validate_plan.
    assert not [d for s in surv
                for d in (s.get("depends_on") or []) if d not in ids]


def test_validate_plan_survives_a_satisfied_drop(leerie, tmp_path, monkeypatch):
    """End-to-end pin: the surviving plan passes the exact gate that killed
    the reported run."""
    st = _make_state(leerie, tmp_path / "run")
    full = dict(size="small", files_likely_touched=["orchestrator/leerie.py"],
                provides=[], requires=[])
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [
                  _sub("feat-001", **full),
                  _sub("feat-002", depends_on=["feat-001"], **full),
              ]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "done", "checked": []},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))

    surv = plans[0]["subtasks"]
    leerie.validate_plan({s["id"]: s for s in surv})   # must not die()


def _req(*tags):
    return [{"tag": t, "extent": "in_plan"} for t in tags]


def test_dropped_provider_orphans_requires_tag_is_pruned(
        leerie, tmp_path, monkeypatch):
    """Regression: the navegando 2026-07-17 failure. A consolidation subtask
    `requires` a tag provided ONLY by a dropped subtask; the drop must prune
    that inbound `requires` (the tag channel), not just `depends_on` (the id
    channel). Without the tag prune validate_plan die()s with
    `requires 'X' but nothing provides it` — after the full planner spend.
    """
    st = _make_state(leerie, tmp_path / "run")
    full = dict(files_likely_touched=["docs/audit-findings.md"])
    plans = [{"domain": "documentation", "status": "ready", "subtasks": [
        _sub("docs-001", provides=["a1"], requires=[], **full),
        _sub("docs-002", provides=["a2"], requires=[], **full),   # dropped
        _sub("docs-010", provides=["artifact"],
             requires=_req("a1", "a2"),
             depends_on=["docs-001", "docs-002"], **full),
    ]}]
    _patch_probe(leerie, monkeypatch, {
        "docs-002": {"satisfied": True, "evidence": "section already on disk",
                     "checked": ["docs/audit-findings.md"]},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))

    surv = plans[0]["subtasks"]
    by_id = {s["id"]: s for s in surv}
    # id channel pruned (existing behavior)
    assert by_id["docs-010"]["depends_on"] == ["docs-001"]
    # tag channel pruned (the fix): the orphaned 'a2' is gone, 'a1' kept
    tags = [r["tag"] for r in by_id["docs-010"]["requires"]]
    assert tags == ["a1"], tags
    # end-to-end: the gate that killed the run must now pass
    leerie.validate_plan({s["id"]: s for s in surv})


def test_requires_tag_with_surviving_provider_is_kept(
        leerie, tmp_path, monkeypatch):
    """A required tag also provided by a SURVIVING subtask must NOT be pruned
    just because a dropped subtask happened to provide it too."""
    st = _make_state(leerie, tmp_path / "run")
    full = dict(files_likely_touched=["docs/audit-findings.md"])
    plans = [{"domain": "documentation", "status": "ready", "subtasks": [
        _sub("docs-001", provides=["shared"], requires=[], **full),  # survives, provides 'shared'
        _sub("docs-002", provides=["shared"], requires=[], **full),  # dropped, also provides 'shared'
        _sub("docs-010", provides=["artifact"], requires=_req("shared"), **full),
    ]}]
    _patch_probe(leerie, monkeypatch, {
        "docs-002": {"satisfied": True, "evidence": "dup", "checked": []},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    by_id = {s["id"]: s for s in plans[0]["subtasks"]}
    assert [r["tag"] for r in by_id["docs-010"]["requires"]] == ["shared"]
    leerie.validate_plan({s["id"]: s for s in plans[0]["subtasks"]})


def test_never_provided_requires_tag_is_not_masked(
        leerie, tmp_path, monkeypatch):
    """A `requires` tag no subtask ever provided is a genuine planner error;
    the drop's tag-prune must NOT swallow it — validate_plan must still die()
    on it. Guards against a naive 'prune any requires-tag not in surviving
    provides' implementation."""
    st = _make_state(leerie, tmp_path / "run")
    full = dict(files_likely_touched=["docs/audit-findings.md"])
    plans = [{"domain": "documentation", "status": "ready", "subtasks": [
        _sub("docs-001", provides=["a1"], requires=[], **full),
        _sub("docs-002", provides=["a2"], requires=[], **full),   # dropped
        _sub("docs-010", provides=["artifact"],
             requires=_req("a1", "never-provided-by-anyone"), **full),
    ]}]
    _patch_probe(leerie, monkeypatch, {
        "docs-002": {"satisfied": True, "evidence": "x", "checked": []},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))
    by_id = {s["id"]: s for s in plans[0]["subtasks"]}
    # the never-provided tag is preserved (a1 kept too — it has a surviving provider)
    tags = sorted(r["tag"] for r in by_id["docs-010"]["requires"])
    assert tags == ["a1", "never-provided-by-anyone"], tags
    # and validate_plan STILL flags the genuine error
    with pytest.raises(SystemExit):
        leerie.validate_plan({s["id"]: s for s in plans[0]["subtasks"]})


def test_cross_plan_surviving_provider_keeps_requires_tag(
        leerie, tmp_path, monkeypatch):
    """Cross-domain regression: a required tag provided by a SURVIVING subtask
    in a DIFFERENT plan must NOT be pruned when a same-tag provider is dropped
    from another plan.

    Capability tags are cross-domain (DESIGN §5): `requires` in plan A can be
    satisfied by `provides` in plan B, and validate_plan checks provider
    existence globally over the merged plan. A per-plan prune would wrongly drop
    the tag because it only sees plan A's provides. The prune must operate over
    all plans at once.
    """
    st = _make_state(leerie, tmp_path / "run")
    full = dict(files_likely_touched=["x"])
    # Plan A: bugfix-001 provides 'shared' (DROPPED); bugfix-002 requires 'shared'.
    plan_a = {"domain": "bug-fixing", "status": "ready", "subtasks": [
        _sub("bugfix-001", provides=["shared"], requires=[], **full),
        _sub("bugfix-002", provides=[], requires=_req("shared"), **full),
    ]}
    # Plan B: feat-001 also provides 'shared' and SURVIVES.
    plan_b = {"domain": "feature-implementation", "status": "ready", "subtasks": [
        _sub("feat-001", provides=["shared"], requires=[], **full),
    ]}
    plans = [plan_a, plan_b]
    _patch_probe(leerie, monkeypatch, {
        "bugfix-001": {"satisfied": True, "evidence": "dup provider", "checked": []},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))

    by_id = {s["id"]: s for p in plans for s in p["subtasks"]}
    # 'shared' still provided by feat-001 (plan B) → must be KEPT on bugfix-002
    assert [r["tag"] for r in by_id["bugfix-002"]["requires"]] == ["shared"]
    # end-to-end over the merged plan
    merged = {s["id"]: s for p in plans for s in p["subtasks"]}
    leerie.validate_plan(merged)   # must not die()


def test_no_drop_leaves_deps_untouched(leerie, tmp_path, monkeypatch):
    """Nothing satisfied → no mapping → depends_on byte-identical."""
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [_sub("feat-001"),
                           _sub("feat-002", depends_on=["feat-001"])]}]
    _patch_probe(leerie, monkeypatch, {})     # all unsatisfied
    _run(leerie.filter_satisfied_subtasks(
        plans, tmp_path, st, _CAPS, _MODELS, _EFFORTS))

    surv = plans[0]["subtasks"]
    assert [s["id"] for s in surv] == ["feat-001", "feat-002"]
    assert surv[1]["depends_on"] == ["feat-001"]


# ---------------------------------------------------------------------------
# satisfied_probe_cache — per-subtask resume checkpoint (bugfix-005)
# ---------------------------------------------------------------------------

def _init_git_repo(path: Path) -> str:
    """Create a minimal real git repo at `path` and return HEAD's sha."""
    import subprocess
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


def test_cache_hit_skips_semaphore_and_claude_p(leerie, tmp_path, monkeypatch):
    """A cached verdict under the CURRENT base sha is consulted before
    `async with sem:` and claude_p is never invoked for that subtask."""
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "already on HEAD",
                     "checked": ["a.py"], "base_sha": sha},
        "feat-002": {"satisfied": False, "evidence": "still needed",
                     "base_sha": sha},
    }
    plans = [{"domain": "feature-implementation", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]

    async def unreached(**_kw):
        raise AssertionError("claude_p must not be called on a cache hit")
    monkeypatch.setattr(leerie, "claude_p", unreached)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    assert surviving == ["feat-002"]
    assert st.data["dropped_subtasks"]["feat-001"]["reason"] == "already_satisfied"
    assert st.data["dropped_subtasks"]["feat-001"]["evidence"] == "already on HEAD"


def test_verdicts_persisted_for_both_outcomes(leerie, tmp_path, monkeypatch):
    """A fresh probe (no cache entry) persists its verdict to
    satisfied_probe_cache for BOTH satisfied and not-satisfied outcomes,
    keyed by sid, carrying satisfied/evidence/checked + base_sha."""
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002")]}]
    _patch_probe(leerie, monkeypatch, {
        "feat-001": {"satisfied": True, "evidence": "done",
                     "checked": ["a.py"]},
        "feat-002": {"satisfied": False, "evidence": "not yet"},
    })
    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))

    cache = st.data["satisfied_probe_cache"]
    assert cache["feat-001"] == {
        "satisfied": True, "evidence": "done",
        "checked": ["a.py"], "base_sha": sha,
    }
    assert cache["feat-002"] == {
        "satisfied": False, "evidence": "not yet",
        "checked": [], "base_sha": sha,
    }


def test_crash_path_writes_no_cache_entry(leerie, tmp_path, monkeypatch):
    """Regression: the WorkerError crash-keep path must write NO cache
    entry — a crashed probe is re-probed on resume, not treated as
    decided."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]
    _patch_probe(leerie, monkeypatch, {"feat-001": "CRASH"})
    _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert "feat-001" not in st.data.get("satisfied_probe_cache", {})


def test_stale_sha_invalidates_cache_and_reprobes(leerie, tmp_path,
                                                   monkeypatch):
    """A cached verdict whose recorded sha differs from the current HEAD
    is invalidated and the subtask is re-probed (correctness test —
    DESIGN §6 the mid-run-sibling-adjacent hazard: HEAD can move between
    a pause and a resume)."""
    repo = tmp_path / "repo"
    _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": True, "evidence": "stale verdict",
                     "checked": [], "base_sha": "deadbeef-not-current"},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001")]}]
    called = {"count": 0}

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        called["count"] += 1
        return {"satisfied": False, "evidence": "fresh probe result"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    assert called["count"] == 1, "stale-sha cache entry must trigger a re-probe"
    surviving = [s["id"] for s in plans[0]["subtasks"]]
    assert surviving == ["feat-001"]
    assert st.data["satisfied_probe_cache"]["feat-001"]["evidence"] == \
        "fresh probe result"


def test_resume_reprobes_only_uncached_subtasks(leerie, tmp_path,
                                                 monkeypatch):
    """THE REPORTED FAILURE PINNED: a partial satisfied_probe_cache
    (some subtasks already decided under the current sha) resumes and
    re-probes only the uncached subtasks, reaching scheduling — asserted
    by claude_p call count. Reverting the fix must fail this test."""
    repo = tmp_path / "repo"
    sha = _init_git_repo(repo)
    st = _make_state(leerie, tmp_path / "run")
    st.data["satisfied_probe_cache"] = {
        "feat-001": {"satisfied": False, "evidence": "decided pre-pause",
                     "checked": [], "base_sha": sha},
    }
    plans = [{"domain": "d", "status": "ready",
              "subtasks": [_sub("feat-001"), _sub("feat-002"),
                           _sub("feat-003")]}]
    calls = []

    async def fake_claude_p(*, user_prompt, sid, **_kw):
        calls.append(sid)
        return {"satisfied": False, "evidence": "probed after resume"}
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    res = _run(leerie.filter_satisfied_subtasks(
        plans, repo, st, _CAPS, _MODELS, _EFFORTS))
    assert res is None
    # Only the two uncached subtasks trigger a fresh claude_p call.
    assert sorted(calls) == ["satisfied_probe-feat-002",
                             "satisfied_probe-feat-003"]
    assert len(plans[0]["subtasks"]) == 3
