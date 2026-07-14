"""Tests for warn_provider_subset_subtasks() — the plan-time advisory
warning that flags a subtask whose entire `files_likely_touched` surface is
owned by an ordered predecessor it depends on (DESIGN §5, §8 *The mid-run
sibling case*).

This is the planner-time defense-in-depth for the failure mode where a
code subtask bundles a shared file's edit in its own commit and a later
test-only subtask — whose whole surface is that same file — reaches its
worker with nothing to commit. The mid-run satisfied rescue in settle_subtask
catches it; this warning surfaces the redundancy one phase earlier.

Warning only, never a drop. Pure function; no LLM, no async. Mirrors
test_warn_cross_planner_file_overlap.py.
"""
from __future__ import annotations

import re


def _capture_logs(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda msg: lines.append(msg))
    return lines


def _req(tag):
    return {"tag": tag, "extent": "in_plan", "reason": ""}


def test_no_predecessor_is_silent(leerie, monkeypatch):
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py"]},
        {"id": "feat-002", "files_likely_touched": ["b.py"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert lines == []


def test_full_subset_via_depends_on_warns(leerie, monkeypatch):
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py", "b.py"]},
        {"id": "feat-002", "files_likely_touched": ["a.py"],
         "depends_on": ["feat-001"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert any("provider-subset subtask" in l for l in lines)
    assert any("feat-002" in l and "feat-001" in l for l in lines)


def test_full_subset_via_requires_provides_warns(leerie, monkeypatch):
    """The exact test-003/bugfix-003 shape: the dependent subtask's only
    file is one the provider it `requires` also touches."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "bugfix", "subtasks": [
        {"id": "bugfix-003",
         "files_likely_touched": ["orchestrator/leerie.py",
                                   "tests/test_cgroup_helpers.py"],
         "provides": ["cgroup-stat-returns-oom-kill"]},
        {"id": "test-003",
         "files_likely_touched": ["tests/test_cgroup_helpers.py"],
         "requires": [_req("cgroup-stat-returns-oom-kill")],
         "provides": ["cgroup-stat-client-oom-tests-updated"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert any("provider-subset subtask" in l for l in lines)
    detail = [l for l in lines if "test-003" in l]
    assert detail, "expected a per-subtask detail line for test-003"
    assert "bugfix-003" in detail[0]
    assert "tests/test_cgroup_helpers.py" in detail[0]


def test_partial_overlap_does_not_warn(leerie, monkeypatch):
    """The dependent touches a file the predecessor does NOT — it makes a
    genuinely distinct edit, so it must NOT be flagged."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py"]},
        {"id": "feat-002", "files_likely_touched": ["a.py", "new.py"],
         "depends_on": ["feat-001"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert lines == []


def test_subset_but_no_dependency_does_not_warn(leerie, monkeypatch):
    """Same files, but no predecessor edge between them → not a
    provider-subset (they are independent). Only an ORDERED predecessor's
    files count."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py"]},
        {"id": "feat-002", "files_likely_touched": ["a.py"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert lines == []


def test_no_files_is_silent(leerie, monkeypatch):
    """A subtask with no files (e.g. a research/artifact subtask) has no
    surface to be a subset of → never flagged, never crashes."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py"]},
        {"id": "feat-002", "depends_on": ["feat-001"]},  # no files
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert lines == []


def test_external_requires_is_not_a_predecessor(leerie, monkeypatch):
    """An `extent: external` requires does NOT create a graph edge
    (mirrors _build_predecessor_graph), so a subtask whose files match an
    external provider is not flagged."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py"],
         "provides": ["cap-x"]},
        {"id": "feat-002", "files_likely_touched": ["a.py"],
         "requires": [{"tag": "cap-x", "extent": "external", "reason": ""}]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    assert lines == []


def test_reports_count(leerie, monkeypatch):
    lines = _capture_logs(leerie, monkeypatch)
    plans = [{"domain": "feat", "subtasks": [
        {"id": "feat-001", "files_likely_touched": ["a.py", "b.py"]},
        {"id": "feat-002", "files_likely_touched": ["a.py"],
         "depends_on": ["feat-001"]},
        {"id": "feat-003", "files_likely_touched": ["b.py"],
         "depends_on": ["feat-001"]},
    ]}]
    leerie.warn_provider_subset_subtasks(plans)
    summary = [l for l in lines if "provider-subset subtask" in l]
    assert summary, "expected a summary line"
    assert re.search(r"2 subtask\(s\)", summary[0])


def test_empty_plans_is_silent(leerie, monkeypatch):
    lines = _capture_logs(leerie, monkeypatch)
    leerie.warn_provider_subset_subtasks([])
    assert lines == []


def test_cross_domain_predecessor_warns(leerie, monkeypatch):
    """The predecessor edge can cross domains (requires/provides is the
    cross-domain wiring); a subset across domains must still warn."""
    lines = _capture_logs(leerie, monkeypatch)
    plans = [
        {"domain": "api", "subtasks": [
            {"id": "api-001", "files_likely_touched": ["shared/types.ts"],
             "provides": ["types-defined"]},
        ]},
        {"domain": "web", "subtasks": [
            {"id": "web-001", "files_likely_touched": ["shared/types.ts"],
             "requires": [_req("types-defined")]},
        ]},
    ]
    leerie.warn_provider_subset_subtasks(plans)
    assert any("web-001" in l and "api-001" in l for l in lines)
