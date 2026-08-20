"""Tests for the shared jsonschema-validate-or-fallback helper in conftest.py.

Exercises both branches of `validate_or_fallback_required`: the real
`jsonschema.validate` path (when installed) and the manual required-field
fallback (simulated via monkeypatching `HAS_JSONSCHEMA` to False), which is
the only way to exercise the fallback deterministically regardless of
whether jsonschema happens to be installed on the host running the suite.
"""
from __future__ import annotations

import pytest

import tests.conftest as conftest_mod


_SCHEMA = {
    "type": "object",
    "required": ["score", "rationale"],
    "properties": {
        "score": {"type": "number"},
        "rationale": {"type": "string"},
    },
}


@pytest.mark.skipif(not conftest_mod.HAS_JSONSCHEMA, reason="jsonschema not installed")
class TestJsonschemaPresent:
    def test_valid_instance_returns_true(self):
        result = conftest_mod.validate_or_fallback_required(
            _SCHEMA, {"score": 0.5, "rationale": "ok"}
        )
        assert result is True

    def test_invalid_instance_raises(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            conftest_mod.validate_or_fallback_required(
                _SCHEMA, {"score": "not-a-number", "rationale": "ok"}
            )

    def test_missing_required_field_raises(self):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            conftest_mod.validate_or_fallback_required(_SCHEMA, {"score": 0.5})


class TestJsonschemaAbsentFallback:
    def test_fallback_returns_false_on_valid_instance(self, monkeypatch):
        monkeypatch.setattr(conftest_mod, "HAS_JSONSCHEMA", False)
        result = conftest_mod.validate_or_fallback_required(
            _SCHEMA, {"score": 0.5, "rationale": "ok"}
        )
        assert result is False

    def test_fallback_raises_on_missing_required_key(self, monkeypatch):
        monkeypatch.setattr(conftest_mod, "HAS_JSONSCHEMA", False)
        with pytest.raises(AssertionError, match="missing required field"):
            conftest_mod.validate_or_fallback_required(_SCHEMA, {"score": 0.5})

    def test_fallback_ignores_extra_unrelated_fields(self, monkeypatch):
        monkeypatch.setattr(conftest_mod, "HAS_JSONSCHEMA", False)
        result = conftest_mod.validate_or_fallback_required(
            _SCHEMA,
            {"score": 0.5, "rationale": "ok", "unexpected_extra_field": 123},
        )
        assert result is False
