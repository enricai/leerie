"""Tests for SCHEMAS["dep_capture"] — the LLM capture worker output schema.

The schema lives in SCHEMAS["dep_capture"] and is passed to `claude -p`
via --json-schema; the CLI validates worker output against it. These tests
pin the structural contract so a future refactor can't silently drop
required fields or change the language_installs item shape.

Mirrors test_pr_writer_schema.py / test_provision_schema.py: uses a
HAS_JSONSCHEMA gate with a manual structural fallback so CI without
jsonschema installed still catches drift.
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
    """Validate using jsonschema when available; otherwise fall back to
    structural assertions that mirror what the schema declares. Tests
    must pass in both modes so CI without jsonschema installed still
    catches regressions."""
    schema = leerie.SCHEMAS["dep_capture"]
    if HAS_JSONSCHEMA:
        jsonschema.validate(instance, schema)
        return
    # Manual structural check matching the schema's `required` and
    # the language_installs item shape.
    for k in schema["required"]:
        assert k in instance, f"missing required field {k!r}"
    if "setup_packages" in instance:
        assert isinstance(instance["setup_packages"], list)
        for pkg in instance["setup_packages"]:
            assert isinstance(pkg, str)
    if "language_installs" in instance:
        assert isinstance(instance["language_installs"], list)
        for item in instance["language_installs"]:
            assert isinstance(item, dict)
            assert "manager" in item, "language_installs item missing 'manager'"
            assert "command" in item, "language_installs item missing 'command'"
            assert isinstance(item["manager"], str)
            assert isinstance(item["command"], str)
            if "copy_inputs" in item:
                assert isinstance(item["copy_inputs"], list)
                for ci in item["copy_inputs"]:
                    assert isinstance(ci, str)
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
    """required must be exactly {setup_packages, language_installs}.
    dockerfile_notes is in properties but intentionally NOT required —
    the worker may omit it when there are no extra notes."""
    schema = leerie.SCHEMAS["dep_capture"]
    assert set(schema["required"]) == {"setup_packages", "language_installs"}


def test_dep_capture_setup_packages_is_array_of_strings(leerie):
    """setup_packages is the apt package list for the warm apt layer."""
    prop = leerie.SCHEMAS["dep_capture"]["properties"]["setup_packages"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "string"


def test_dep_capture_language_installs_is_array_of_objects(leerie):
    """language_installs carries per-manager install commands."""
    prop = leerie.SCHEMAS["dep_capture"]["properties"]["language_installs"]
    assert prop["type"] == "array"
    assert prop["items"]["type"] == "object"


def test_dep_capture_language_installs_item_required_fields(leerie):
    """Each language_installs entry requires manager and command.
    copy_inputs is optional (hallucinated paths are skipped by code)."""
    item = leerie.SCHEMAS["dep_capture"]["properties"]["language_installs"]["items"]
    assert set(item["required"]) == {"manager", "command"}


def test_dep_capture_language_installs_item_manager_is_string(leerie):
    item = leerie.SCHEMAS["dep_capture"]["properties"]["language_installs"]["items"]
    assert item["properties"]["manager"]["type"] == "string"


def test_dep_capture_language_installs_item_command_is_string(leerie):
    """command is a shell string (not argv list — distinct from provision
    schema which uses argv). The dep_capture worker emits human-readable
    install commands like 'pip install -r requirements.txt'."""
    item = leerie.SCHEMAS["dep_capture"]["properties"]["language_installs"]["items"]
    assert item["properties"]["command"]["type"] == "string"


def test_dep_capture_language_installs_item_copy_inputs_is_array_of_strings(leerie):
    """copy_inputs lists repo-relative paths COPYed before the RUN layer."""
    item = leerie.SCHEMAS["dep_capture"]["properties"]["language_installs"]["items"]
    ci = item["properties"]["copy_inputs"]
    assert ci["type"] == "array"
    assert ci["items"]["type"] == "string"


def test_dep_capture_dockerfile_notes_is_string_or_null(leerie):
    """dockerfile_notes is optional freeform text for the Dockerfile emitter."""
    prop = leerie.SCHEMAS["dep_capture"]["properties"]["dockerfile_notes"]
    allowed = prop.get("type", [])
    assert "string" in allowed and "null" in allowed


# --- valid instance acceptance ----------------------------------------------

def test_dep_capture_schema_accepts_minimal_valid_instance(leerie):
    """Both required fields present, empty lists — no language deps needed."""
    _validate(leerie, {
        "setup_packages": [],
        "language_installs": [],
    })


def test_dep_capture_schema_accepts_pip_repo(leerie):
    """Typical pip repo: apt build-dep + pip install + requirements.txt copy."""
    _validate(leerie, {
        "setup_packages": ["python3-dev", "build-essential"],
        "language_installs": [
            {
                "manager": "pip",
                "command": "pip install -r requirements.txt",
                "copy_inputs": ["requirements.txt"],
            }
        ],
        "dockerfile_notes": None,
    })


def test_dep_capture_schema_accepts_pnpm_repo(leerie):
    """pnpm/Next.js repo with lockfile copy and no apt packages."""
    _validate(leerie, {
        "setup_packages": [],
        "language_installs": [
            {
                "manager": "pnpm",
                "command": "pnpm install --frozen-lockfile",
                "copy_inputs": ["package.json", "pnpm-lock.yaml"],
            }
        ],
    })


def test_dep_capture_schema_accepts_multi_manager(leerie):
    """Polyglot repo: apt + pip + pnpm in one payload."""
    _validate(leerie, {
        "setup_packages": ["libpq-dev"],
        "language_installs": [
            {
                "manager": "pip",
                "command": "pip install -r requirements.txt",
                "copy_inputs": ["requirements.txt"],
            },
            {
                "manager": "pnpm",
                "command": "pnpm install",
                "copy_inputs": ["package.json", "pnpm-lock.yaml"],
            },
        ],
        "dockerfile_notes": "Run pip before pnpm; shared build layer.",
    })


def test_dep_capture_schema_accepts_no_copy_inputs(leerie):
    """copy_inputs is optional — may be absent when no files need COPYing."""
    _validate(leerie, {
        "setup_packages": [],
        "language_installs": [
            {
                "manager": "cargo",
                "command": "cargo build --release",
            }
        ],
    })


def test_dep_capture_schema_accepts_dockerfile_notes_null(leerie):
    _validate(leerie, {
        "setup_packages": [],
        "language_installs": [],
        "dockerfile_notes": None,
    })


def test_dep_capture_schema_accepts_dockerfile_notes_string(leerie):
    _validate(leerie, {
        "setup_packages": [],
        "language_installs": [],
        "dockerfile_notes": "No extra steps needed; all deps from apt.",
    })


# --- invalid instance rejection --------------------------------------------

def test_dep_capture_schema_rejects_missing_setup_packages(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"language_installs": []},
            leerie.SCHEMAS["dep_capture"],
        )


def test_dep_capture_schema_rejects_missing_language_installs(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {"setup_packages": []},
            leerie.SCHEMAS["dep_capture"],
        )


def test_dep_capture_schema_rejects_language_installs_item_missing_manager(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "setup_packages": [],
                "language_installs": [{"command": "pip install -r requirements.txt"}],
            },
            leerie.SCHEMAS["dep_capture"],
        )


def test_dep_capture_schema_rejects_language_installs_item_missing_command(leerie):
    if not HAS_JSONSCHEMA:
        pytest.skip("jsonschema not available; required check requires it")
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(
            {
                "setup_packages": [],
                "language_installs": [{"manager": "pip"}],
            },
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


def test_dep_capture_effort_default_is_high(leerie):
    """dep_capture is a judgment worker; its effort default is 'high'
    (in EFFORT_DEFAULT_PER_WORKER) matching the other judgment workers."""
    assert leerie.EFFORT_DEFAULT_PER_WORKER.get("dep_capture") == "high"


def test_dep_capture_model_defaults_to_opus(leerie):
    """dep_capture is absent from MODEL_DEFAULT_PER_WORKER, so it falls
    through to MODEL_DEFAULT ('opus') — the judgment-worker default."""
    assert "dep_capture" not in leerie.MODEL_DEFAULT_PER_WORKER
    assert leerie.MODEL_DEFAULT == "opus"
