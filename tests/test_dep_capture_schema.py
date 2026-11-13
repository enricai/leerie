"""Tests for SCHEMAS["dep_capture"] — the LLM capture worker output schema.

The schema lives in SCHEMAS["dep_capture"] and is passed to `claude -p`
via --json-schema; the CLI validates worker output against it. These tests
pin the structural contract so a future refactor can't silently drop
required fields. Since 2026-11-12 the worker names apt packages only: the
repository image holds no dependency (DESIGN §6½ *The repository image
holds no dependency*), so the `language_installs` field is gone.

Mirrors test_pr_writer_schema.py / test_provision_schema.py: uses a
HAS_JSONSCHEMA gate with a manual structural fallback so CI without
jsonschema installed still catches drift.
"""
from __future__ import annotations

import json
import pytest

try:
    import jsonschema  # type: ignore
except ImportError:
    jsonschema = None  # type: ignore

from tests.conftest import HAS_JSONSCHEMA, validate_or_fallback_required


def _validate(leerie, instance: dict) -> None:
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests
    must pass in both modes so CI without jsonschema installed still
    catches regressions."""
    schema = leerie.SCHEMAS["dep_capture"]
    if validate_or_fallback_required(schema, instance):
        return
    # Manual structural check matching the schema's `required`.
    if "setup_packages" in instance:
        assert isinstance(instance["setup_packages"], list)
        for pkg in instance["setup_packages"]:
            assert isinstance(pkg, str)
    if "dockerfile_notes" in instance:
        assert instance["dockerfile_notes"] is None or isinstance(
            instance["dockerfile_notes"], str)


# --- existence and shape ---------------------------------------------------

def test_dep_capture_schema_exists(leerie):
    """SCHEMAS["dep_capture"] must exist — it's the code-side contract for
    the capture worker output (DESIGN §6½)."""
    assert "dep_capture" in leerie.SCHEMAS
    schema = leerie.SCHEMAS["dep_capture"]
    assert schema["type"] == "object"


def test_dep_capture_schema_required_fields(leerie):
    """required is exactly {setup_packages}. dockerfile_notes is in
    properties but intentionally NOT required — the worker may omit it
    when there are no extra notes."""
    schema = leerie.SCHEMAS["dep_capture"]
    assert set(schema["required"]) == {"setup_packages"}
    assert set(schema["properties"]) == {"setup_packages", "dockerfile_notes"}


def test_dep_capture_setup_packages_is_array_of_strings(leerie):
    """setup_packages is the apt package list for the warm apt layer."""
    prop = leerie.SCHEMAS["dep_capture"]["properties"]["setup_packages"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_dep_capture_dockerfile_notes_is_string_or_null(leerie):
    """dockerfile_notes is optional freeform text for the Dockerfile emitter."""
    prop = leerie.SCHEMAS["dep_capture"]["properties"]["dockerfile_notes"]
    allowed = prop.get("type", [])
    assert "string" in allowed and "null" in allowed


# --- valid instance acceptance ----------------------------------------------

def test_dep_capture_schema_accepts_minimal_valid_instance(leerie):
    """The required field present, an empty list — no apt package needed."""
    _validate(leerie, {"setup_packages": []})


def test_dep_capture_schema_accepts_apt_packages(leerie):
    _validate(leerie, {
        "setup_packages": ["python3-dev", "build-essential", "libpq-dev"],
        "dockerfile_notes": None,
    })


def test_dep_capture_schema_accepts_dockerfile_notes_null(leerie):
    _validate(leerie, {
        "setup_packages": [],
        "dockerfile_notes": None,
    })


def test_dep_capture_schema_accepts_dockerfile_notes_string(leerie):
    _validate(leerie, {
        "setup_packages": [],
        "dockerfile_notes": "No extra steps needed; all deps from apt.",
    })


# --- invalid instance rejection --------------------------------------------

def test_dep_capture_schema_rejects_missing_setup_packages(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"dockerfile_notes": None},
            leerie.SCHEMAS["dep_capture"],
        )


# --- empty-string rejection (minLength) ------------------------------------
# Regression guard: an empty-string package would render to ""
# and, under `--recapture --force` (replace path), could blank a good config.
# minLength:1 enforces "no empty names" at the schema layer (DESIGN §12).

def test_dep_capture_schema_declares_nonempty_string_fields(leerie):
    """Structural pin (works without jsonschema): setup_packages items carry
    a pattern no empty string matches (the Debian package-name pattern,
    DESIGN §6½ *A capture names packages, never commands*)."""
    import re
    pkg = leerie.SCHEMAS["dep_capture"]["properties"]["setup_packages"]["items"]
    assert pkg.get("pattern") == leerie.DEBIAN_PACKAGE_PATTERN
    assert not re.search(pkg["pattern"], "")


def test_dep_capture_schema_rejects_empty_setup_package(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; minLength check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"setup_packages": [""]},
            leerie.SCHEMAS["dep_capture"],
        )


# --- round-trip and serialization -----------------------------------------

def test_dep_capture_schema_is_json_serializable(leerie):
    """The schema is passed to `claude -p` as --json-schema <inline-json>;
    any non-serializable surface would blow up at runtime, not import."""
    json.dumps(leerie.SCHEMAS["dep_capture"])


def test_dep_capture_schema_round_trips(leerie):
    """Schema survives json.dumps → json.loads with identical content."""
    schema = leerie.SCHEMAS["dep_capture"]
    assert json.loads(json.dumps(schema)) == schema


# --- wiring checks --------------------------------------------------------

def test_dep_capture_in_allowed_schema_keys(leerie):
    """claude_p() rejects unknown schema_key values — dep_capture must be
    in its allowlist or capture_repo_deps would die at runtime."""
    src = (leerie.__file__ and open(leerie.__file__).read()) or ""
    assert '"dep_capture"' in src


def test_dep_capture_not_in_worker_types(leerie):
    """dep_capture is a post-run skill worker (like pr_writer), not a
    main-loop worker — it must NOT appear in WORKER_TYPES."""
    assert "dep_capture" not in leerie.WORKER_TYPES


def test_dep_capture_effort_default_is_medium(leerie):
    """dep_capture is a judgment worker; its effort default is 'medium'
    (in EFFORT_DEFAULT_PER_WORKER) matching the other judgment workers
    (lowered from 'high' post-Opus-5; see IMPLEMENTATION.md §2)."""
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("dep_capture") == "medium"


def test_dep_capture_model_defaults_to_sonnet(leerie):
    """dep_capture is absent from MODEL_DEFAULT_PER_WORKER, so it falls
    through to MODEL_DEFAULT ('sonnet')."""
    assert "dep_capture" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "sonnet"
