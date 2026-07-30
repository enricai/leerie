"""Tests for SCHEMAS["task_coverage_judge"] — the independent plan-vs-task
coverage verifier (DESIGN §8 *Independent adversarial verification*).

Mirrors test_classification_judge_schema.py: a HAS_JSONSCHEMA gate with a
manual structural fallback. The load-bearing assertion is that this worker
carries NO _confidence_schema sub-object (it IS the independent check) and
gates on a non-empty coverage_gaps array, not a score.
"""
from __future__ import annotations

import json
import pytest

try:
    import jsonschema  # type: ignore
    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False


def _validate(leerie, instance: dict) -> None:
    schema = leerie.SCHEMAS["task_coverage_judge"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    assert isinstance(instance["task_covered"], bool)
    assert isinstance(instance["coverage_gaps"], list)
    for g in instance["coverage_gaps"]:
        for k in ("kind", "description", "concrete_evidence"):
            assert k in g, f"coverage_gaps item missing {k!r}"
    assert isinstance(instance["rationale"], str)


# --- existence and shape ---------------------------------------------------

def test_schema_exists(leerie):
    assert "task_coverage_judge" in leerie.SCHEMAS
    assert leerie.SCHEMAS["task_coverage_judge"]["type"] == "object"


def test_required_fields(leerie):
    schema = leerie.SCHEMAS["task_coverage_judge"]
    assert set(schema["required"]) == {
        "task_covered", "coverage_gaps", "rationale",
    }


def test_coverage_gap_item_shape(leerie):
    item = (leerie.SCHEMAS["task_coverage_judge"]["properties"]
            ["coverage_gaps"]["items"])
    assert item["type"] == "object"
    assert set(item["required"]) == {
        "kind", "description", "concrete_evidence"}
    assert set(item["properties"]["kind"]["enum"]) == {
        "missing_work", "off_task_subtask"}


def test_no_confidence_subobject(leerie):
    """The load-bearing mirror-of-adherence_judge assertion: an independent
    verifier must NOT carry a _confidence_schema self-grade."""
    schema = leerie.SCHEMAS["task_coverage_judge"]
    assert "confidence" not in schema.get("properties", {})
    assert "confidence" not in schema["required"]


# --- valid / invalid instances ---------------------------------------------

def test_accepts_clean_empty_coverage_gaps(leerie):
    _validate(leerie, {
        "task_covered": True,
        "coverage_gaps": [],
        "rationale": "The subtask set covers every piece of work the "
                     "task asked for; no off-task subtasks present.",
    })


def test_accepts_missing_work_defect(leerie):
    _validate(leerie, {
        "task_covered": False,
        "coverage_gaps": [{
            "kind": "missing_work",
            "description": "Task asks for a regression test but no "
                            "testing subtask exists.",
            "concrete_evidence": "Task text: 'add a regression test'; "
                                  "no subtask has category testing.",
        }],
        "rationale": "The regression-test requirement has no owning "
                     "subtask.",
    })


def test_accepts_off_task_defect(leerie):
    _validate(leerie, {
        "task_covered": False,
        "coverage_gaps": [{
            "kind": "off_task_subtask",
            "description": "Subtask feat-003 refactors an unrelated "
                            "module the task never mentions.",
            "concrete_evidence": "feat-003 touches auth.py; task text "
                                  "is entirely about the login timeout.",
        }],
        "rationale": "feat-003 is unrelated to the requested work.",
    })


def test_rejects_missing_required_field(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"coverage_gaps": [], "rationale": "x"},
            leerie.SCHEMAS["task_coverage_judge"],
        )


def test_rejects_bad_coverage_gap_kind(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "task_covered": False,
                "coverage_gaps": [{
                    "kind": "not_a_kind",
                    "description": "x",
                    "concrete_evidence": "x",
                }],
                "rationale": "x",
            },
            leerie.SCHEMAS["task_coverage_judge"],
        )


# --- serializability --------------------------------------------------------

def test_schema_round_trips(leerie):
    schema = leerie.SCHEMAS["task_coverage_judge"]
    assert json.loads(json.dumps(schema)) == schema


# --- wiring -----------------------------------------------------------------

def test_in_worker_types(leerie):
    assert "task_coverage_judge" in leerie.WORKER_TYPES


def test_not_in_model_default_per_worker(leerie):
    """Defaults to sonnet via the global MODEL_DEFAULT fallback."""
    assert "task_coverage_judge" not in leerie.MODEL_DEFAULT_PER_WORKER


def test_effort_default_is_medium(leerie):
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get(
        "task_coverage_judge") == "medium"


def test_prompt_file_exists(leerie):
    from pathlib import Path
    prompt = (Path(leerie.__file__).parent.parent / "prompts"
              / "task_coverage_judge.md")
    assert prompt.exists(), f"not found at {prompt}"
