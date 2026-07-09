"""Tests for resolve_prompt(call_type) -> (source_kind, content, location_hint).

Covers:
- Every WORKER_TYPES member returns a valid (kind, content, hint) triple.
- Parity/coupling: WORKER_TYPES and resolve_prompt have consistent coverage.
- Every worker resolves to a file under prompts/.
- Unknown call_type raises ValueError.
"""
from __future__ import annotations

import pytest


@pytest.mark.parametrize("call_type", [
    "classifier", "planner", "reconciler", "plan_overlap_judge", "provision",
    "implementer", "integrator", "conformer",
])
def test_resolve_prompt_returns_valid_triple(leerie, call_type):
    kind, content, hint = leerie.resolve_prompt(call_type)
    assert kind == "file", f"unexpected kind {kind!r} for {call_type}"
    assert content and content.strip(), f"empty content for {call_type}"
    assert hint == f"prompts/{call_type}.md"


def test_resolve_prompt_covers_all_worker_types(leerie):
    """Parity: every WORKER_TYPES member must be handled without error."""
    for call_type in leerie.WORKER_TYPES:
        kind, content, hint = leerie.resolve_prompt(call_type)
        assert kind == "file"
        assert content


def test_resolve_prompt_unknown_raises(leerie):
    with pytest.raises((ValueError, KeyError)):
        leerie.resolve_prompt("nonexistent_worker")


def test_load_prompt_dep_capture(leerie):
    """dep_capture is a non-WORKER_TYPES worker; load_prompt must find its file."""
    content = leerie.load_prompt("dep_capture")
    assert content and content.strip(), "dep_capture prompt must be non-empty"
    # All three schema fields must be documented (per success criteria §3).
    for field in ("setup_packages", "language_installs", "dockerfile_notes"):
        assert field in content, f"dep_capture prompt missing schema field: {field}"
