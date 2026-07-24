"""Tests for _format_owned_region_section() (DESIGN §5½ (P1) *Sub-file*).

The sub-file splitter gives each region child an `owned_region` field on its
on-disk spec; run_implementer surfaces it as an OWNED_REGION prompt section so
the implementer stays inside its line range. These tests cover the deterministic
render: None when there is no region, the rendered section when there is, and
graceful degrade on a missing/corrupt spec (mirrors test_artifact_passing.py's
coverage of the sibling _format_upstream_artifacts_for_sid helper).
"""
from __future__ import annotations

import json


def _write_spec(leerie_dir, sid, spec):
    sub = leerie_dir / "subtasks"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / f"{sid}.json").write_text(json.dumps(spec))


def test_none_when_no_owned_region(leerie, tmp_path):
    """A normal subtask (no owned_region) yields no section — the common case."""
    _write_spec(tmp_path, "feat-001", {
        "id": "feat-001", "title": "t", "success_criteria_seed": "c",
        "files_likely_touched": ["a.ts"],
    })
    assert leerie._format_owned_region_section(tmp_path, "feat-001") is None


def test_renders_section_with_lines_and_symbols(leerie, tmp_path):
    _write_spec(tmp_path, "feat-005-r1", {
        "id": "feat-005-r1", "title": "region 1",
        "success_criteria_seed": "c",
        "files_likely_touched": ["src/big.ts"],
        "owned_region": {"file": "src/big.ts", "start": 1, "end": 700,
                         "symbols": ["foo", "bar"]},
    })
    section = leerie._format_owned_region_section(tmp_path, "feat-005-r1")
    assert section is not None
    assert "OWNED_REGION:" in section
    assert "lines 1-700 of src/big.ts" in section
    assert "foo, bar" in section
    # No mid-sentence hard break (D2 regression guard): the flowing sentence
    # about the sibling stays on one logical line.
    assert "otherwise\n" not in section


def test_renders_placeholder_when_no_symbols(leerie, tmp_path):
    _write_spec(tmp_path, "feat-005-r2", {
        "id": "feat-005-r2", "title": "region 2",
        "success_criteria_seed": "c",
        "files_likely_touched": ["src/big.ts"],
        "owned_region": {"file": "src/big.ts", "start": 701, "end": 1400,
                         "symbols": []},
    })
    section = leerie._format_owned_region_section(tmp_path, "feat-005-r2")
    assert section is not None
    assert "(no named symbols in range)" in section


def test_none_when_spec_missing(leerie, tmp_path):
    # No spec file on disk (subtasks/ dir absent) → graceful None, no crash.
    assert leerie._format_owned_region_section(tmp_path, "nope") is None


def test_none_on_corrupt_spec(leerie, tmp_path):
    sub = tmp_path / "subtasks"
    sub.mkdir(parents=True, exist_ok=True)
    (sub / "feat-009.json").write_text("{ not valid json")
    assert leerie._format_owned_region_section(tmp_path, "feat-009") is None
