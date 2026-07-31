"""Tests for the multi-token OAuth probe/ranking surface (DESIGN §6
*Multi-token rotation*).

`_probe_token_usage` tries Probe A (`/api/oauth/usage`, zero inference
cost, requires `user:profile` scope) and falls back to Probe B (a minimal
`/v1/messages` call reading `anthropic-ratelimit-unified-*` headers, for
`user:inference`-scoped setup-tokens) on a 403. Both probes are
undocumented and best-effort — every failure path here must degrade
gracefully, never raise. `_rank_tokens` sorts by remaining runway.

No live network calls: `urllib.request.urlopen` is monkeypatched to
return canned responses/errors shaped like the real endpoints (response
JSON verified against multiple independent external sources during
planning; see the DESIGN §6 section this test file backs).
"""
from __future__ import annotations

import io
import json
import urllib.error

import pytest


class _FakeResponse:
    """Minimal stand-in for the context-manager urlopen() returns."""

    def __init__(self, body: bytes, headers: dict | None = None):
        self._body = body
        self.headers = headers or {}

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _probe_a_body(five_hour_util=30.0, seven_day_util=20.0,
                  resets_at="2026-08-01T05:00:00.000000+00:00",
                  seven_day_opus=None, seven_day_sonnet=None):
    return json.dumps({
        "five_hour": {"utilization": five_hour_util, "resets_at": resets_at},
        "seven_day": {"utilization": seven_day_util,
                      "resets_at": "2026-08-05T05:00:00.000000+00:00"},
        "seven_day_opus": seven_day_opus,
        "seven_day_sonnet": seven_day_sonnet,
    }).encode()


class TestProbeA:
    def test_parses_response_correctly(self, leerie, monkeypatch):
        def fake_urlopen(req, timeout=None):
            assert req.full_url == "https://api.anthropic.com/api/oauth/usage"
            assert req.headers["Authorization"] == "Bearer tok-a"
            assert req.headers["Anthropic-beta"] == "oauth-2025-04-20"
            assert req.headers["User-agent"].startswith("claude-code/")
            return _FakeResponse(_probe_a_body(
                five_hour_util=25.0, seven_day_util=10.0,
                seven_day_opus={"utilization": 5.0, "resets_at": "x"}))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_a("tok-a")
        assert result is not None
        assert result["five_hour_util"] == pytest.approx(0.25)
        assert result["seven_day_util"] == pytest.approx(0.10)
        assert result["seven_day_opus_util"] == pytest.approx(0.05)
        assert result["seven_day_sonnet_util"] is None
        assert result["resets_at"].year == 2026

    def test_403_returns_none_for_fallback(self, leerie, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        assert leerie._probe_token_usage_a("tok-a") is None

    def test_transient_5xx_returns_none_quietly(self, leerie, monkeypatch):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        assert leerie._probe_token_usage_a("tok-a") is None

    def test_missing_field_logs_drift_marker(self, leerie, monkeypatch, capsys):
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(json.dumps({"five_hour": {}}).encode())

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_a("tok-a")
        assert result is None
        out = capsys.readouterr().out
        assert leerie._TOKEN_PROBE_DRIFT_MARKER in out

    def test_transient_failure_does_not_log_drift_marker(
            self, leerie, monkeypatch, capsys):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        leerie._probe_token_usage_a("tok-a")
        out = capsys.readouterr().out
        assert leerie._TOKEN_PROBE_DRIFT_MARKER not in out

    def test_null_opus_sublimit_is_not_penalized(self, leerie, monkeypatch):
        """A null seven_day_opus means 'no Opus usage recorded' (full
        runway), not missing/unknown data."""
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(_probe_a_body(seven_day_opus=None))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_a("tok-a")
        assert result["seven_day_opus_util"] is None


def _headers_for(five_h="0.10", seven_d="0.05", five_h_reset="1785000000"):
    return {
        "anthropic-ratelimit-unified-5h-utilization": five_h,
        "anthropic-ratelimit-unified-7d-utilization": seven_d,
        "anthropic-ratelimit-unified-5h-reset": five_h_reset,
    }


class TestProbeB:
    def test_parses_headers_correctly(self, leerie, monkeypatch):
        def fake_urlopen(req, timeout=None):
            assert req.full_url == "https://api.anthropic.com/v1/messages"
            body = json.loads(req.data)
            assert body["max_tokens"] == 1
            return _FakeResponse(b"{}", headers=_headers_for(
                five_h="0.15", seven_d="0.30"))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_b("tok-b")
        assert result is not None
        assert result["five_hour_util"] == pytest.approx(0.15)
        assert result["seven_day_util"] == pytest.approx(0.30)
        assert result["resets_at"].tzinfo is not None
        assert result["seven_day_opus_util"] is None

    def test_count_tokens_headers_absent_treated_as_drift(
            self, leerie, monkeypatch, capsys):
        """count_tokens does not carry these headers — a response with
        none of them present is contract drift, not a transient miss."""
        def fake_urlopen(req, timeout=None):
            return _FakeResponse(b"{}", headers={})

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_b("tok-b")
        assert result is None
        assert leerie._TOKEN_PROBE_DRIFT_MARKER in capsys.readouterr().out

    def test_401_returns_none_and_logs_dead_token(
            self, leerie, monkeypatch, capsys):
        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", _headers_for(),
                io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage_b("tok-b")
        assert result is None
        out = capsys.readouterr().out
        assert "rejected" in out or "dead" in out


class TestProbeAToBFallback:
    def test_403_on_a_falls_back_to_b(self, leerie, monkeypatch):
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(req.full_url)
            if req.full_url.endswith("/api/oauth/usage"):
                raise urllib.error.HTTPError(
                    req.full_url, 403, "Forbidden", {}, io.BytesIO(b""))
            return _FakeResponse(b"{}", headers=_headers_for())

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        result = leerie._probe_token_usage("setup-token", cache_sec=180)
        assert result is not None
        assert len(calls) == 2


class TestCache:
    def test_second_call_within_window_makes_no_http_call(
            self, leerie, monkeypatch):
        leerie._TOKEN_PROBE_CACHE.clear()
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _FakeResponse(_probe_a_body())

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        leerie._probe_token_usage("tok-cache", cache_sec=180)
        leerie._probe_token_usage("tok-cache", cache_sec=180)
        assert len(calls) == 1

    def test_cache_expires_after_window(self, leerie, monkeypatch):
        leerie._TOKEN_PROBE_CACHE.clear()
        calls = []
        fake_time = [1000.0]

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            return _FakeResponse(_probe_a_body())

        def fake_monotonic():
            return fake_time[0]

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(leerie.time, "monotonic", fake_monotonic)
        leerie._probe_token_usage("tok-expire", cache_sec=180)
        fake_time[0] += 200
        leerie._probe_token_usage("tok-expire", cache_sec=180)
        assert len(calls) == 2

    def test_all_probes_fail_result_still_cached(self, leerie, monkeypatch):
        """A failed probe result (None) is cached too — otherwise a
        persistently-403-then-500 token would be re-probed every call,
        defeating the cache's purpose of respecting the aggressive
        per-token rate limit."""
        leerie._TOKEN_PROBE_CACHE.clear()
        calls = []

        def fake_urlopen(req, timeout=None):
            calls.append(1)
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        leerie._probe_token_usage("tok-fail", cache_sec=180)
        leerie._probe_token_usage("tok-fail", cache_sec=180)
        # Probe A + Probe B fallback = 2 calls on the FIRST invocation;
        # the second invocation must add zero more.
        assert len(calls) == 2


class TestRankTokens:
    def test_picks_lowest_utilization_token(self, leerie, monkeypatch):
        leerie._TOKEN_PROBE_CACHE.clear()

        def fake_urlopen(req, timeout=None):
            token = req.headers["Authorization"].split(" ")[1]
            util = {"low-usage": 5.0, "high-usage": 90.0}[token]
            return _FakeResponse(_probe_a_body(
                five_hour_util=util, seven_day_util=util))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        ranked = leerie._rank_tokens(["high-usage", "low-usage"], cache_sec=180)
        assert ranked[0] == "low-usage"

    def test_furthest_reset_tiebreak(self, leerie, monkeypatch):
        leerie._TOKEN_PROBE_CACHE.clear()

        def fake_urlopen(req, timeout=None):
            token = req.headers["Authorization"].split(" ")[1]
            resets = {
                "resets-soon": "2026-08-01T00:00:00.000000+00:00",
                "resets-later": "2026-08-10T00:00:00.000000+00:00",
            }[token]
            return _FakeResponse(_probe_a_body(
                five_hour_util=50.0, seven_day_util=50.0, resets_at=resets))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        ranked = leerie._rank_tokens(["resets-soon", "resets-later"], cache_sec=180)
        assert ranked[0] == "resets-later"

    def test_opus_sublimit_affects_ranking(self, leerie, monkeypatch):
        """A token near its Opus weekly cap has less usable runway than
        its aggregate utilization alone suggests."""
        leerie._TOKEN_PROBE_CACHE.clear()

        def fake_urlopen(req, timeout=None):
            token = req.headers["Authorization"].split(" ")[1]
            if token == "opus-exhausted":
                return _FakeResponse(_probe_a_body(
                    five_hour_util=10.0, seven_day_util=10.0,
                    seven_day_opus={"utilization": 99.0, "resets_at": "x"}))
            return _FakeResponse(_probe_a_body(
                five_hour_util=10.0, seven_day_util=10.0,
                seven_day_opus={"utilization": 1.0, "resets_at": "x"}))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        ranked = leerie._rank_tokens(["opus-exhausted", "opus-fresh"], cache_sec=180)
        assert ranked[0] == "opus-fresh"

    def test_all_probes_fail_first_token_chosen_no_exception(
            self, leerie, monkeypatch):
        leerie._TOKEN_PROBE_CACHE.clear()

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        ranked = leerie._rank_tokens(["tok-1", "tok-2"], cache_sec=180)
        # No exception raised, and the original first token is still first
        # among the (all-failed, so unranked-by-data) candidates.
        assert ranked[0] == "tok-1"


class TestParseOauthTokenList:
    def test_comma_separated(self, leerie):
        assert leerie._parse_oauth_token_list("a,b,c") == ["a", "b", "c"]

    def test_whitespace_trimmed(self, leerie):
        assert leerie._parse_oauth_token_list(" a , b ") == ["a", "b"]

    def test_empty_entries_dropped(self, leerie):
        assert leerie._parse_oauth_token_list("a,,b,") == ["a", "b"]

    def test_none_returns_empty(self, leerie):
        assert leerie._parse_oauth_token_list(None) == []

    def test_empty_string_returns_empty(self, leerie):
        assert leerie._parse_oauth_token_list("") == []


class TestTokenFingerprint:
    def test_deterministic(self, leerie):
        assert (leerie._token_fingerprint("tok-a")
                == leerie._token_fingerprint("tok-a"))

    def test_different_tokens_differ(self, leerie):
        assert (leerie._token_fingerprint("tok-a")
                != leerie._token_fingerprint("tok-b"))

    def test_never_contains_raw_token(self, leerie):
        assert "tok-a" not in leerie._token_fingerprint("tok-a")

    def test_short(self, leerie):
        assert len(leerie._token_fingerprint("tok-a")) == 12
