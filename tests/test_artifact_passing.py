"""Tests for the artifact-passing contract between subtasks.

Covers DESIGN §5 *Artifact passing between subtasks*: a producer
subtask returns structured deliverables on its result's `artifacts`
field; the orchestrator persists them under
`.leerie/runs/<id>/artifacts/<sid>.json`; consumer subtasks whose
predecessor graph names the producer receive the artifacts in their
prompt under a `## Artifacts from upstream subtasks` section.

These tests cover the deterministic pieces — disk persistence,
graph-walking, and prompt rendering. The implementer-result schema
validation is covered structurally by `_RE-USE_validate_result()` /
the JSON schema and is exercised end-to-end by the integration tier.
"""
from __future__ import annotations

import json
from pathlib import Path


# ---- _write_subtask_artifacts: orchestrator-owned persistence ----


def test_write_subtask_artifacts_creates_dir_and_file(leerie, tmp_path):
    """The helper creates the artifacts/ subdirectory and writes the
    JSON payload atomically. We assert the on-disk shape directly —
    other helpers consume this file."""
    artifacts = [
        {"name": "spec", "kind": "markdown", "content": "# Hello\nworld"},
    ]
    leerie._write_subtask_artifacts(tmp_path, "feat-001", artifacts)

    out = tmp_path / "artifacts" / "feat-001.json"
    assert out.exists()
    payload = json.loads(out.read_text())
    assert payload == {"subtask_id": "feat-001", "artifacts": artifacts}


def test_write_subtask_artifacts_overwrites_on_resume(leerie, tmp_path):
    """A second call with new content replaces the first. On resume the
    result is the source of truth — a partial first attempt should not
    leak into the final."""
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "spec", "kind": "markdown", "content": "old"}])
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "spec", "kind": "markdown", "content": "new"}])

    payload = json.loads((tmp_path / "artifacts" / "feat-001.json").read_text())
    assert payload["artifacts"][0]["content"] == "new"


def test_write_subtask_artifacts_no_partial_files_on_disk(leerie, tmp_path):
    """The atomic write must not leave a `.tmp` sibling behind."""
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "spec", "kind": "markdown", "content": "x"}])

    art_dir = tmp_path / "artifacts"
    assert {p.name for p in art_dir.iterdir()} == {"feat-001.json"}


# ---- _read_upstream_artifacts: graph-walking input ----


def test_read_upstream_artifacts_returns_in_input_order(leerie, tmp_path):
    """Preserve the predecessor-id order the caller supplied. The
    scheduler order matters: a consumer may rely on the earlier
    upstream appearing first in the prompt."""
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "a", "kind": "markdown", "content": "first"}])
    leerie._write_subtask_artifacts(
        tmp_path, "feat-002",
        [{"name": "b", "kind": "markdown", "content": "second"}])

    out = leerie._read_upstream_artifacts(tmp_path, ["feat-001", "feat-002"])
    assert [p["subtask_id"] for p in out] == ["feat-001", "feat-002"]

    rev = leerie._read_upstream_artifacts(tmp_path, ["feat-002", "feat-001"])
    assert [p["subtask_id"] for p in rev] == ["feat-002", "feat-001"]


def test_read_upstream_artifacts_skips_missing(leerie, tmp_path):
    """A predecessor that produced no artifacts is the common code-
    implementation case — silently skipped, no error."""
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "a", "kind": "markdown", "content": "x"}])

    out = leerie._read_upstream_artifacts(
        tmp_path, ["feat-000-no-artifacts", "feat-001"])
    assert [p["subtask_id"] for p in out] == ["feat-001"]


def test_read_upstream_artifacts_handles_unreadable(leerie, tmp_path):
    """A malformed artifacts file is treated as absent. The orchestrator
    should never crash on garbled coordination state — degraded
    operation is better than a wave abort."""
    (tmp_path / "artifacts").mkdir()
    (tmp_path / "artifacts" / "feat-001.json").write_text("not json{{{")

    out = leerie._read_upstream_artifacts(tmp_path, ["feat-001"])
    assert out == []


# ---- _format_upstream_artifacts_section: prompt rendering ----


def test_format_upstream_artifacts_section_inlines_content(leerie):
    """The artifact's `content` lands verbatim in the rendered section.
    Tight-context discipline: a consumer sees only its declared
    predecessors' content, but it sees that content in full."""
    payloads = [{
        "subtask_id": "feat-001",
        "artifacts": [{
            "name": "redesign-spec",
            "kind": "markdown",
            "content": "# Redesign\n\nUse indigo for primary.",
            "summary": "Token mapping for dashboard regions.",
        }],
    }]
    rendered = leerie._format_upstream_artifacts_section(payloads)
    assert rendered is not None
    assert "## Artifacts from upstream subtasks" in rendered
    assert "feat-001" in rendered
    assert "redesign-spec" in rendered
    assert "markdown" in rendered
    assert "Token mapping for dashboard regions." in rendered  # summary
    assert "Use indigo for primary." in rendered  # content body verbatim


def test_format_upstream_artifacts_section_empty_returns_none(leerie):
    """No upstream artifacts → no section at all. The implementer
    prompt should not carry a header that introduces nothing."""
    assert leerie._format_upstream_artifacts_section([]) is None


# ---- _format_upstream_artifacts_for_sid: end-to-end through plan.json ----


def _write_plan(leerie_dir: Path, subtasks: dict) -> None:
    """Persist a minimal plan.json matching what `_write_plan` produces.
    Only the keys read by `_format_upstream_artifacts_for_sid` need to
    be present."""
    leerie_dir.mkdir(parents=True, exist_ok=True)
    (leerie_dir / "plan.json").write_text(json.dumps(
        {"task": "test", "waves": [], "subtasks": subtasks,
         "preconditions": []}, indent=2))


def _stub_subtask(sid: str, **overrides) -> dict:
    base = {
        "id": sid,
        "title": "stub",
        "intent": "x",
        "scope_note": "x",
        "files_likely_touched": [],
        "depends_on": [],
        "requires": [],
        "provides": [],
        "success_criteria_seed": "x",
        "size": "small",
        "investigation_notes": "",
    }
    base.update(overrides)
    return base


def test_format_for_sid_routes_via_depends_on(leerie, tmp_path):
    """A consumer that declares `depends_on: ["feat-001"]` receives
    feat-001's artifacts. This is the most direct routing path — the
    edge is explicit."""
    _write_plan(tmp_path, {
        "feat-001": _stub_subtask("feat-001"),
        "feat-002": _stub_subtask("feat-002", depends_on=["feat-001"]),
    })
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "spec", "kind": "markdown",
          "content": "REDESIGN_SPEC_BODY"}])

    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-002")
    assert rendered is not None
    assert "REDESIGN_SPEC_BODY" in rendered


def test_format_for_sid_routes_via_requires_provides(leerie, tmp_path):
    """A consumer that declares `requires: [{tag: T, extent: in_plan}]`
    receives the artifacts of any subtask whose `provides` lists T.
    This is the indirect routing path — the edge is derived from
    capability tags. Both routing channels must work; the planner
    uses whichever feels natural for the domain."""
    _write_plan(tmp_path, {
        "feat-001": _stub_subtask("feat-001", provides=["spec"]),
        "feat-002": _stub_subtask(
            "feat-002",
            requires=[{"tag": "spec", "extent": "in_plan"}]),
    })
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "spec", "kind": "markdown", "content": "PROVIDED"}])

    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-002")
    assert rendered is not None
    assert "PROVIDED" in rendered


def test_format_for_sid_isolates_non_predecessors(leerie, tmp_path):
    """A subtask that did not declare the producer as a predecessor
    MUST NOT receive that producer's artifacts. This is the tight-
    context discipline guarantee — workers see only what they
    declared they need."""
    _write_plan(tmp_path, {
        "feat-001": _stub_subtask("feat-001"),
        "feat-002": _stub_subtask("feat-002"),  # no depends_on, no requires
    })
    leerie._write_subtask_artifacts(
        tmp_path, "feat-001",
        [{"name": "secret", "kind": "markdown",
          "content": "SHOULD_NOT_LEAK"}])

    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-002")
    assert rendered is None


def test_format_for_sid_no_plan_returns_none(leerie, tmp_path):
    """Missing plan.json (e.g. the bootstrap phase before _write_plan)
    is silently treated as 'no upstream artifacts'."""
    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-001")
    assert rendered is None


def test_format_for_sid_predecessor_without_artifacts(leerie, tmp_path):
    """A declared predecessor that produced no artifacts (the common
    code-implementation case) yields no injection — even when the
    graph edge exists."""
    _write_plan(tmp_path, {
        "feat-001": _stub_subtask("feat-001"),
        "feat-002": _stub_subtask("feat-002", depends_on=["feat-001"]),
    })
    # No artifacts file for feat-001.
    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-002")
    assert rendered is None


def test_format_for_sid_corrupt_plan_returns_none(leerie, tmp_path):
    """A plan.json that fails to parse (e.g. a torn write during a crash)
    degrades to 'no upstream artifacts' rather than raising."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "plan.json").write_text("{ not valid json")
    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-001")
    assert rendered is None


def test_format_for_sid_unknown_sid_returns_none(leerie, tmp_path):
    """A sid absent from the plan's subtasks map (or a malformed,
    non-dict `subtasks` field) is treated as having no predecessors."""
    _write_plan(tmp_path, {"feat-001": _stub_subtask("feat-001")})
    rendered = leerie._format_upstream_artifacts_for_sid(tmp_path, "feat-999")
    assert rendered is None
