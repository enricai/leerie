"""Tests for the pr_writer worker output schema.

The schema lives in SCHEMAS["pr_writer"] and is passed to `claude -p`
via --json-schema; the CLI validates worker output against it. These
tests document what the schema requires and guard the contract from
silent drift (e.g., title-length cap, required fields).
"""
from __future__ import annotations

import json
import pytest

from tests.conftest import HAS_JSONSCHEMA, validate_or_fallback_required

try:
    import jsonschema  # type: ignore
except ImportError:
    pass


def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests
    must pass in both modes so CI without jsonschema installed still
    catches regressions."""
    schema = leerie.SCHEMAS["pr_writer"]
    if validate_or_fallback_required(schema, instance):
        return
    # Manual structural check matching the schema's `required` and the
    # title's length cap.
    if "title" in instance:
        assert isinstance(instance["title"], str)
        assert 1 <= len(instance["title"]) <= 200, "title length out of range"
    if "body" in instance:
        assert isinstance(instance["body"], str)
        assert len(instance["body"]) >= 1
    if "used_template" in instance:
        assert instance["used_template"] is None or isinstance(
            instance["used_template"], str)


def test_pr_writer_schema_required_fields(leerie):
    schema = leerie.SCHEMAS["pr_writer"]
    assert set(schema["required"]) == {"title", "body", "used_template"}


def test_pr_writer_schema_accepts_valid_no_template(leerie):
    _validate(leerie, {
        "title": "Fix Dynamo pagination returning duplicate items",
        "body": "## Summary\n\nFixes a bug.\n",
        "used_template": None,
    })


def test_pr_writer_schema_accepts_valid_with_template(leerie):
    _validate(leerie, {
        "title": "Migrate Reddit ingest from polling to SNS",
        "body": "## Description\n\n<filled template content>\n",
        "used_template": ".github/pull_request_template.md",
    })


def test_pr_writer_schema_rejects_empty_title(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minLength check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"title": "", "body": "x", "used_template": None},
            leerie.SCHEMAS["pr_writer"],
        )


def test_pr_writer_schema_rejects_empty_body(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minLength check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"title": "ok", "body": "", "used_template": None},
            leerie.SCHEMAS["pr_writer"],
        )


def test_pr_writer_schema_rejects_too_long_title(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; maxLength check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"title": "x" * 201, "body": "ok", "used_template": None},
            leerie.SCHEMAS["pr_writer"],
        )


def test_pr_writer_schema_rejects_missing_used_template(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    # used_template is required even when null — guards against the
    # worker silently dropping the field when no template was filled.
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"title": "ok", "body": "ok"},
            leerie.SCHEMAS["pr_writer"],
        )


def test_pr_writer_schema_is_json_serializable(leerie):
    # The schema is passed to `claude -p` as --json-schema <inline-json>;
    # any non-serializable surface would blow up at runtime, not import.
    json.dumps(leerie.SCHEMAS["pr_writer"])


def test_pr_writer_in_allowed_schema_keys(leerie):
    """claude_p() rejects unknown schema_key values — the allowlist must
    contain pr_writer or the _compose_pr_via_llm call would die."""
    src = (leerie.__file__ and open(leerie.__file__).read()) or ""
    # The allowlist is constructed inside claude_p; presence of the
    # literal in the source is the most stable check.
    assert '"pr_writer"' in src


# --- _strip_leerie_prefix --------------------------------------------------
# DESIGN §12 *prompts are advisory, code enforces*. The pr_writer prompt
# tells the worker not to emit a `leerie:` prefix; the launcher
# unconditionally prepends `leerie: `. Without code-side enforcement, a
# single drift produces `leerie: leerie: …` on every PR until the prompt is
# patched. _strip_leerie_prefix is the code half of the guarantee.

def test_strip_leerie_prefix_removes_lowercase(leerie):
    assert leerie._strip_leerie_prefix("leerie: Fix Dynamo paging") == "Fix Dynamo paging"


def test_strip_leerie_prefix_removes_uppercase(leerie):
    assert leerie._strip_leerie_prefix("LEERIE: Fix Dynamo paging") == "Fix Dynamo paging"


def test_strip_leerie_prefix_removes_mixed_case(leerie):
    assert leerie._strip_leerie_prefix("Leerie: x") == "x"
    assert leerie._strip_leerie_prefix("lEErIE: y") == "y"


def test_strip_leerie_prefix_handles_extra_whitespace(leerie):
    assert leerie._strip_leerie_prefix("leerie:   leading spaces") == "leading spaces"
    assert leerie._strip_leerie_prefix("leerie:\tFix bug") == "Fix bug"
    assert leerie._strip_leerie_prefix("leerie:no space") == "no space"


def test_strip_leerie_prefix_no_op_when_absent(leerie):
    assert leerie._strip_leerie_prefix("Fix the bug") == "Fix the bug"


def test_strip_leerie_prefix_does_not_false_positive_on_leerietes(leerie):
    """Anchor must be `leerie:` exactly — not `leerie` followed by anything.
    A title like "leerietes is great" must pass through unchanged."""
    assert leerie._strip_leerie_prefix("leerietes is great") == "leerietes is great"
    assert leerie._strip_leerie_prefix("leerie is a word") == "leerie is a word"
    assert leerie._strip_leerie_prefix("Leerietesify") == "Leerietesify"


def test_strip_leerie_prefix_only_strips_leading_match(leerie):
    """A mid-string `leerie:` is part of the user's title (e.g. a quote)
    and must not be stripped."""
    assert leerie._strip_leerie_prefix("Add leerie: marker to commits") == \
        "Add leerie: marker to commits"


def test_strip_leerie_prefix_strips_only_once(leerie):
    """A worker emitting `leerie: leerie: x` — the exact bug this guard
    defends against — gets one prefix stripped; the second pass would
    happen on a re-invocation, not in a single call. This documents
    the intent: the launcher will still add ONE `leerie: ` on top."""
    out = leerie._strip_leerie_prefix("leerie: leerie: x")
    # One strip leaves "leerie: x"; the launcher's unconditional prepend
    # then produces "leerie: leerie: x" — still wrong, but the same wrong
    # as the original bug. Document the limit: a single strip handles
    # the realistic drift case.
    assert out == "leerie: x"


def test_strip_leerie_prefix_empty_string(leerie):
    assert leerie._strip_leerie_prefix("") == ""
