"""Rebuilds the 2026-07-19 incident's payload shape from shape.json.

The real task file (an internal product audit, 51,142 bytes) is
deliberately not committed here. This module reconstructs a synthetic,
shape-matched stand-in from the measured per-field byte distribution in
`shape.json` — same total sizes, same subtask count, same
reconciler-payload structure (`task` / `categories` / `subtasks`), and the
same CLAUDE.md-shaped coverage-gate heading text — so the incident's two
root causes stay reproducible without the original file.

Pure stdlib, no leerie import (this file is not collected as a test
module — pytest.ini's `python_files = test_*.py` only picks up
`test_incident_2026_07_19.py`, which imports the functions below).
"""
from __future__ import annotations

import json
from pathlib import Path

_SHAPE_PATH = Path(__file__).parent / "shape.json"


def load_shape() -> dict:
    return json.loads(_SHAPE_PATH.read_text())


def build_task(shape: dict | None = None) -> str:
    """A synthetic task string of the measured incident task_bytes,
    including one incidental `CLAUDE.md` token (root cause A's trigger)
    plus a Verification-section sentence in the same shape the incident
    note quotes, padded to the exact measured byte length."""
    shape = shape or load_shape()
    target = shape["reconciler_payload"]["task_bytes"]
    prefix = (
        "Add pagination to the orders list and wire up the export "
        "button.\n\nVerification:\n"
        "- `pnpm run lint:fix` + `pnpm run build` + `pnpm test <touched>` "
        "per CLAUDE.md checklist.\n\n"
    )
    assert len(prefix.encode()) <= target, (
        "shape.json task_bytes shrunk below the fixed incident prefix")
    filler = "X" * (target - len(prefix.encode()))
    task = prefix + filler
    assert len(task.encode()) == target
    return task


def build_subtask_views(shape: dict | None = None) -> list[dict]:
    """Synthetic subtask_views matching the reconciler payload's real
    dict shape (id/title/intent/depends_on/files_likely_touched/provides/
    requires — see orchestrator/leerie.py's reconciler payload builder),
    count and json.dumps(indent=2) byte size matched to the measured
    incident values via a padding field on the final entry."""
    shape = shape or load_shape()
    n = shape["reconciler_payload"]["subtask_count"]
    indent = shape["reconciler_payload"]["json_indent"]
    target = shape["reconciler_payload"]["subtask_views_bytes"]

    views = []
    for i in range(n):
        views.append({
            "id": f"feat-{i:03d}",
            "title": f"Synthetic incident-shaped subtask {i}",
            "intent": (
                "Implement a slice of the synthetic incident workload "
                f"for shape reproduction, item {i}."
            ),
            "depends_on": [],
            "files_likely_touched": [f"src/module_{i}.py"],
            "provides": [f"capability-{i}"],
            "requires": [],
        })

    def _size() -> int:
        return len(json.dumps(views, indent=indent).encode())

    # Pad the last entry's intent so the serialized byte count matches
    # the measured target exactly, without perturbing n or the shape of
    # every other entry.
    current = _size()
    if current > target:
        raise ValueError(
            f"synthetic subtask_views ({current}B) already exceeds "
            f"target ({target}B) before padding — reduce n or shrink "
            "the per-entry template")
    views[-1]["intent"] += "X" * (target - current)
    assert _size() == target
    return views


def build_reconciler_payload(shape: dict | None = None) -> dict:
    """The exact {"task", "categories", "subtasks"} shape claude_p's
    reconciler caller serializes (orchestrator/leerie.py, phase 2¾ /
    reconciler payload builder) at the incident's measured total size."""
    shape = shape or load_shape()
    task = build_task(shape)
    subtask_views = build_subtask_views(shape)
    categories = ["bug-fixing", "test-coverage"]
    payload = {
        "task": task,
        "categories": categories,
        "subtasks": subtask_views,
        "unresolved_requires": [],
    }
    return payload


def build_user_prompt(shape: dict | None = None) -> str:
    """The reconciler's user_prompt string exactly as claude_p receives
    it (payload serialized with the incident's json.dumps(indent=2))."""
    shape = shape or load_shape()
    payload = build_reconciler_payload(shape)
    return (
        "RECONCILER INPUT:\n" +
        json.dumps(payload, indent=shape["reconciler_payload"]["json_indent"]) +
        "\n\nResolve every unresolved_requires entry per your "
        "instructions and emit the eight-array JSON output."
    )


def build_system_prompt(shape: dict | None = None) -> str:
    """A synthetic reconciler.md-shaped appended system prompt at the
    measured byte size."""
    shape = shape or load_shape()
    target = shape["system_prompt"]["bytes"]
    return "S" * target


def build_coverage_task(shape: dict | None = None) -> str:
    """A task string that mentions CLAUDE.md once (root cause A's
    trigger token) so glob_task_references/extract_task_file_structure
    harvest it exactly as the incident run did."""
    return build_task(shape)


def build_claude_md_text(shape: dict | None = None) -> str:
    """Reconstructs a CLAUDE.md-shaped file whose H3 headings match the
    incident's harvested item list: 3 backtick+MUST convention
    imperatives (uncoverable by construction, root cause A) plus the
    other genuinely-harvested headings, in the same order shape.json
    records."""
    shape = shape or load_shape()
    gate = shape["coverage_gate"]
    headings = (
        gate["uncoverable_backtick_must_headings"] +
        gate["other_headings"]
    )
    assert len(headings) == gate["harvested_item_count"]
    lines = ["# CLAUDE.md\n", "## Verification\n"]
    for h in headings:
        lines.append(f"### {h}\n")
    return "\n".join(lines)


def build_non_matching_plan_text() -> str:
    """A plan_text that cannot substring-match any harvested heading —
    mirrors the incident's 80,339-char concatenated plan text, where
    every backtick+MUST item stayed uncovered across all 112 recovered
    subtasks."""
    return (
        "add pagination to the orders list and wire up the export button"
    )
