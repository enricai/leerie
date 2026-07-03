"""Tests for `_is_auth_or_quota_failure` — the classifier that routes
`claude -p` envelopes into the auth/quota backoff loop in `claude_p()`.

The integration of the backoff loop itself (tenacity's AsyncRetrying
wrapping `_spawn`) is not unit-tested here — per CLAUDE.md the worker
invocation path stays integration-tested. We cover the classifier so
the routing decision is locked down independently.
"""
from __future__ import annotations

import pytest


# --- positives: should classify as auth/quota -----------------------------

@pytest.mark.parametrize("envelope", [
    {"is_error": True, "api_error_status": 401,
     "result": "Failed to authenticate."},
    {"is_error": True, "api_error_status": "401", "result": ""},
    {"is_error": True, "api_error_status": 429,
     "result": "Too Many Requests"},
    {"is_error": True, "api_error_status": "429", "result": ""},
    {"is_error": True, "api_error_status": None,
     "result": "API Error: 401 Invalid authentication credentials"},
    {"is_error": True, "api_error_status": None,
     "result": "rate limit exceeded; try again later"},
    {"is_error": True, "api_error_status": None,
     "result": "Anthropic returned a rate-limit error."},
    # mixed case — classifier lowercases the result message
    {"is_error": True, "api_error_status": None,
     "result": "INVALID AUTHENTICATION provided"},
])
def test_auth_or_quota_envelopes_match(leerie, envelope):
    assert leerie._is_auth_or_quota_failure(envelope) is True


# --- negatives: should NOT classify as auth/quota -------------------------

@pytest.mark.parametrize("envelope", [
    # plain success envelope
    {"api_error_status": None, "result": "ok",
     "structured_output": {"status": "ready"}},
    # generic error that isn't auth/quota
    {"is_error": True, "api_error_status": 500,
     "result": "Internal server error"},
    {"is_error": True, "api_error_status": "500", "result": ""},
    # missing fields entirely
    {},
    # message mentions "auth" but not the specific markers we key on
    {"is_error": True, "api_error_status": None,
     "result": "build failed: unauthorized?"},
    # schema-error class — handled by the existing 2-attempt loop
    {"is_error": False, "api_error_status": None,
     "result": "the run produced no structured_output"},
    # regression: a *successful*, schema-valid worker whose own output
    # legitimately discusses API rate limiting / auth (e.g. planning a
    # rate-limited endpoint) must not be mistaken for a gateway rejection —
    # is_error=False short-circuits before the text markers are even
    # consulted. See the "phoenix" api-server-creation.md false-positive.
    {"is_error": False, "api_error_status": None,
     "result": "scope/rate-limit/auth idioms + RATE_LIMIT constants",
     "structured_output": {"status": "ready"}},
    {"is_error": False, "api_error_status": None,
     "result": "Invalid authentication is tested by this endpoint's 401 case.",
     "structured_output": {"status": "ready"}},
])
def test_non_auth_envelopes_do_not_match(leerie, envelope):
    assert leerie._is_auth_or_quota_failure(envelope) is False


def test_classifier_tolerates_non_string_result(leerie):
    """`result` is normally a string, but `str(None)` is `'None'` — the
    classifier coerces via str() so a missing key never raises."""
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "result": None}) is False


def test_classifier_requires_is_error_even_with_401_status(leerie):
    """`api_error_status` is a passthrough field from the `claude -p`
    envelope; even so, the classifier only trusts it once `is_error`
    confirms the call actually failed — defense in depth against any
    envelope shape where the two could disagree."""
    assert leerie._is_auth_or_quota_failure(
        {"is_error": False, "api_error_status": 401,
         "result": "ok"}) is False


# --- cap is wired into DEFAULT_CAPS ---------------------------------------

def test_auth_retry_max_sec_is_in_default_caps(leerie):
    """The backoff budget lives in DEFAULT_CAPS per CLAUDE.md
    'caps are real Python counters' rule."""
    assert "auth_retry_max_sec" in leerie.DEFAULT_CAPS
    assert isinstance(leerie.DEFAULT_CAPS["auth_retry_max_sec"], int)
    assert leerie.DEFAULT_CAPS["auth_retry_max_sec"] > 0
