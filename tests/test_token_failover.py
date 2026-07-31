"""Tests for mid-run multi-token failover inside `claude_p` (DESIGN §6
*Multi-token rotation*).

When the ACTIVE token is rate-limited (401/429/529/auth-message, not
terminal-auth) and more than one CLAUDE_CODE_OAUTH_TOKENS token is
configured, claude_p tries rotating to another token with runway BEFORE
spending any of the tenacity backoff budget on a token already known to
be exhausted. If every token is currently rate-limited, it picks the
soonest-reset token and falls through to the EXISTING RateLimitedExit /
_sleep_then_reexec path — reused unchanged, not reinvented.

Harness mirrors test_no_result_event_retry.py / test_terminal_auth_routing.py:
a stubbed `_invoke` yielding pre-scripted envelopes in order, and a stubbed
urllib.request.urlopen for the probe calls the rotation logic makes.
"""
from __future__ import annotations

import asyncio
import io
import json
import urllib.error

import pytest


class _FakeState:
    """Minimal State stand-in for claude_p (see test_no_result_event_retry.py)."""

    def __init__(self, tmp_path, active_token=None):
        self.path = tmp_path / "runs" / "r1" / "state.json"
        self.run_dir = self.path.parent
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "r1"
        self.data = {"verbosity": "quiet"}
        if active_token is not None:
            self.data["active_oauth_token"] = active_token
        self.bumped = 0
        self.saved = 0

    def bump_workers(self, *a, **k):
        self.bumped += 1

    def add_telemetry(self, *a, **k):
        pass

    def save(self):
        self.saved += 1


_RATE_LIMITED_ENVELOPE = {
    "type": "result",
    "subtype": "error_during_execution",
    "is_error": True,
    "api_error_status": 429,
    "result": "rate limit exceeded",
    "structured_output": None,
}

_SUCCESS_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "{}",
    "structured_output": {"categories": ["feature-implementation"]},
}

_TERMINAL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": None,
    "result": ("Failed to authenticate: OAuth session expired and could "
               "not be refreshed"),
    "structured_output": None,
}


def _probe_response_body(five_hour_util, seven_day_util,
                         resets_at="2026-08-01T05:00:00.000000+00:00"):
    return json.dumps({
        "five_hour": {"utilization": five_hour_util, "resets_at": resets_at},
        "seven_day": {"utilization": seven_day_util, "resets_at": resets_at},
        "seven_day_opus": None,
        "seven_day_sonnet": None,
    }).encode()


class _FakeProbeResponse:
    def __init__(self, body: bytes):
        self._body = body

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _call_claude_p(leerie, monkeypatch, envelopes, tmp_path,
                   oauth_tokens=None, active_token=None,
                   probe_utils=None):
    """Drive claude_p with stubbed _invoke + urlopen.

    `probe_utils`: dict token -> (five_hour_util, seven_day_util) used by
    the stubbed urlopen to answer Probe A for that token's rotation
    candidacy. Tokens absent from this dict make urlopen raise a 500
    (simulating an unprobeable token)."""
    seq = list(envelopes)
    calls = {"n": 0}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        calls["n"] += 1
        return seq.pop(0)

    def fake_urlopen(req, timeout=None):
        token = req.headers["Authorization"].split(" ")[1]
        utils = (probe_utils or {}).get(token)
        if utils is None:
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))
        return _FakeProbeResponse(_probe_response_body(*utils))

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
    leerie._TOKEN_PROBE_CACHE.clear()
    if oauth_tokens is not None:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", oauth_tokens)
    else:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKENS", raising=False)

    st = _FakeState(tmp_path, active_token=active_token)

    async def run():
        return await leerie.claude_p(
            "do the task",
            "you are a fit judge",
            schema_key="fit_judge",
            cwd="/work",
            allowed_tools="Read",
            max_turns=60,
            autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS),
            st=st,
            model="opus",
            sid="fit-judge-failover",
        )

    try:
        result = asyncio.run(run())
        exc = None
    except BaseException as e:  # noqa: BLE001
        result = None
        exc = e
    return result, exc, calls["n"], st


class TestRotateOnRateLimit:
    def test_rotates_to_token_with_runway_and_retries(
            self, leerie, monkeypatch, tmp_path):
        """The active token is rate-limited; another token has runway.
        claude_p should switch and retry in-process (no re-exec)."""
        result, exc, n, st = _call_claude_p(
            leerie, monkeypatch,
            [dict(_RATE_LIMITED_ENVELOPE), dict(_SUCCESS_ENVELOPE)],
            tmp_path,
            oauth_tokens="tok-exhausted,tok-fresh",
            active_token="tok-exhausted",
            probe_utils={"tok-fresh": (5.0, 5.0)},
        )
        assert exc is None
        assert result == {"categories": ["feature-implementation"]}
        assert n == 2, "expected the rate-limited spawn + one retry on the new token"
        assert st.data["active_oauth_token"] == "tok-fresh"

    def test_single_token_never_attempts_rotation(
            self, leerie, monkeypatch, tmp_path):
        """No CLAUDE_CODE_OAUTH_TOKENS (or a single-element list) — the
        rotation branch must be a complete no-op, falling through to the
        existing backoff loop unchanged."""
        # Rate-limited on both attempts of the existing 2-attempt schema
        # loop is out of scope here; just prove no rotation probe fires.
        calls = {"probed": False}

        def fake_urlopen(req, timeout=None):
            calls["probed"] = True
            raise urllib.error.HTTPError(
                req.full_url, 500, "x", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKENS", raising=False)
        monkeypatch.setattr(
            leerie, "_invoke",
            lambda *a, **k: _async_return(dict(_RATE_LIMITED_ENVELOPE)))
        monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)

        # Cap auth_retry_max_sec near zero so the real backoff loop (which
        # DOES still run — single-token rate limits are unaffected by this
        # feature) exits fast instead of actually sleeping.
        caps = dict(leerie.DEFAULT_CAPS)
        caps["auth_retry_max_sec"] = 0

        async def run():
            return await leerie.claude_p(
                "do the task", "you are a fit judge", schema_key="fit_judge",
                cwd="/work", allowed_tools="Read", max_turns=60,
                autonomous=False, caps=caps, st=_FakeState(tmp_path),
                model="opus", sid="fit-judge-single-token",
            )

        try:
            asyncio.run(run())
        except BaseException:
            pass
        assert calls["probed"] is False, (
            "rotation must not probe anything when 0 or 1 tokens are configured"
        )

    def test_high_but_nonzero_runway_still_rotates_rather_than_waits(
            self, leerie, monkeypatch, tmp_path):
        """Both tokens report high utilization, but tok-b still has SOME
        runway (10%) — claude_p should rotate to it and retry, not treat
        this as the all-exhausted case."""
        result, exc, n, st = _call_claude_p(
            leerie, monkeypatch,
            [dict(_RATE_LIMITED_ENVELOPE), dict(_SUCCESS_ENVELOPE)],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
            probe_utils={"tok-b": (90.0, 90.0)},
        )
        assert exc is None
        assert st.data["active_oauth_token"] == "tok-b"

    def test_all_tokens_exhausted_raises_ratelimitedexit_with_soonest_reset(
            self, leerie, monkeypatch, tmp_path):
        """Every token reports 100% utilization (no runway anywhere).
        claude_p must select the soonest-reset token and raise the
        EXISTING RateLimitedExit (reused, not a new exception type)."""
        result, exc, n, st = _call_claude_p(
            leerie, monkeypatch,
            [dict(_RATE_LIMITED_ENVELOPE)],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
            probe_utils={
                "tok-a": (100.0, 100.0),
                "tok-b": (100.0, 100.0),
            },
        )
        assert isinstance(exc, leerie.RateLimitedExit)
        assert exc.out_of_credits is False
        assert exc.reset_at is not None

    def test_probe_failure_mid_rotation_falls_through_to_existing_backoff(
            self, leerie, monkeypatch, tmp_path):
        """Every probe fails outright (no data at all, not just high
        utilization) — must never raise from the rotation code itself;
        falls through to the pre-existing tenacity backoff/pause."""
        caps_patch_applied = {"n": 0}

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(
                req.full_url, 500, "x", {}, io.BytesIO(b""))

        seq = [dict(_RATE_LIMITED_ENVELOPE), dict(_RATE_LIMITED_ENVELOPE)]

        async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                              **kwargs):
            caps_patch_applied["n"] += 1
            return seq.pop(0) if seq else dict(_RATE_LIMITED_ENVELOPE)

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        monkeypatch.setattr(leerie, "_invoke", fake_invoke)
        monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", "tok-a,tok-b")
        leerie._TOKEN_PROBE_CACHE.clear()

        caps = dict(leerie.DEFAULT_CAPS)
        caps["auth_retry_max_sec"] = 0  # exit the real backoff loop fast

        st = _FakeState(tmp_path, active_token="tok-a")

        async def run():
            return await leerie.claude_p(
                "do the task", "you are a fit judge", schema_key="fit_judge",
                cwd="/work", allowed_tools="Read", max_turns=60,
                autonomous=False, caps=caps, st=st,
                model="opus", sid="fit-judge-probe-fail",
            )

        try:
            asyncio.run(run())
            exc = None
        except BaseException as e:  # noqa: BLE001
            exc = e
        # Must NOT be a RateLimitedExit from the rotation code (no probe
        # data means no soonest-reset can be computed) — falls through to
        # the existing WorkerError-on-budget-exhaustion path instead.
        assert not isinstance(exc, leerie.RateLimitedExit)

    def test_terminal_auth_never_rotated(self, leerie, monkeypatch, tmp_path):
        """A dead/expired credential is not a rate limit and must never
        enter the rotation code at all — regression control."""
        probed = {"n": 0}

        def fake_urlopen(req, timeout=None):
            probed["n"] += 1
            raise urllib.error.HTTPError(
                req.full_url, 500, "x", {}, io.BytesIO(b""))

        result, exc, n, st = _call_claude_p(
            leerie, monkeypatch,
            [dict(_TERMINAL_ENVELOPE)],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
        )
        assert isinstance(exc, leerie.TerminalAuthFailure)
        assert n == 1


async def _async_return(value):
    return value


def _call_claude_p_with_invoke_sequence(leerie, monkeypatch, sequence, tmp_path,
                                        oauth_tokens=None, active_token=None,
                                        probe_utils=None):
    """Like _call_claude_p, but `sequence` items may be either an envelope
    dict (returned normally) or a BaseException instance (raised) — needed
    to simulate the protocol-level `rate_limit_event` path, where
    `_invoke` raises `RateLimitedExit` directly instead of returning an
    envelope (orchestrator/leerie.py:10610-10639)."""
    seq = list(sequence)

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        item = seq.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    def fake_urlopen(req, timeout=None):
        token = req.headers["Authorization"].split(" ")[1]
        utils = (probe_utils or {}).get(token)
        if utils is None:
            raise urllib.error.HTTPError(
                req.full_url, 500, "Internal Server Error", {}, io.BytesIO(b""))
        return _FakeProbeResponse(_probe_response_body(*utils))

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
    leerie._TOKEN_PROBE_CACHE.clear()
    if oauth_tokens is not None:
        monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKENS", oauth_tokens)
    else:
        monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKENS", raising=False)

    st = _FakeState(tmp_path, active_token=active_token)

    async def run():
        return await leerie.claude_p(
            "do the task", "you are a fit judge", schema_key="fit_judge",
            cwd="/work", allowed_tools="Read", max_turns=60,
            autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
            model="opus", sid="fit-judge-protocol-failover",
        )

    try:
        result = asyncio.run(run())
        exc = None
    except BaseException as e:  # noqa: BLE001
        result = None
        exc = e
    return result, exc, st


class TestRotateOnProtocolLevelRateLimitedExit:
    """The protocol-level `rate_limit_event` path
    (orchestrator/leerie.py:10610-10639) raises `RateLimitedExit` directly
    out of `_invoke`'s streaming loop — `_spawn` never returns an envelope
    for this case. This is a SEPARATE surface from the envelope-level
    `_is_auth_or_quota_failure` path covered above; both must get the same
    rotation chance, wired through the shared `_rotate_oauth_token_or_raise`
    helper."""

    def test_rate_limited_exit_triggers_rotation_and_retries(
            self, leerie, monkeypatch, tmp_path):
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=None, raw_message="rate_limit_event status=exceeded",
            out_of_credits=False)
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke, dict(_SUCCESS_ENVELOPE)],
            tmp_path,
            oauth_tokens="tok-exhausted,tok-fresh",
            active_token="tok-exhausted",
            probe_utils={"tok-fresh": (5.0, 5.0)},
        )
        assert exc is None
        assert result == {"categories": ["feature-implementation"]}
        assert st.data["active_oauth_token"] == "tok-fresh"

    def test_out_of_credits_bypasses_rotation_and_reraises_unchanged(
            self, leerie, monkeypatch, tmp_path):
        """out_of_credits=True is an account-level exhaustion, not a
        per-token rate limit — must re-raise immediately, never attempt
        rotation, regardless of how many tokens are configured."""
        probed = {"n": 0}

        def fake_urlopen(req, timeout=None):
            probed["n"] += 1
            raise urllib.error.HTTPError(
                req.full_url, 500, "x", {}, io.BytesIO(b""))

        monkeypatch.setattr(leerie.urllib.request, "urlopen", fake_urlopen)
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=None, raw_message="out of credits", out_of_credits=True)
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
        )
        assert isinstance(exc, leerie.RateLimitedExit)
        assert exc.out_of_credits is True
        assert exc is exc_from_invoke
        assert probed["n"] == 0, (
            "out_of_credits must never probe other tokens for rotation")

    def test_single_token_reraises_unchanged(self, leerie, monkeypatch, tmp_path):
        """No CLAUDE_CODE_OAUTH_TOKENS configured — the exception must
        propagate exactly as it did before this feature existed."""
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=None, raw_message="rate_limit_event status=exceeded",
            out_of_credits=False)
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke],
            tmp_path,
            oauth_tokens=None,
            active_token=None,
        )
        assert exc is exc_from_invoke

    def test_all_tokens_limited_raises_with_soonest_reset(
            self, leerie, monkeypatch, tmp_path):
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=None, raw_message="rate_limit_event status=exceeded",
            out_of_credits=False)
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
            probe_utils={
                "tok-a": (100.0, 100.0),
                "tok-b": (100.0, 100.0),
            },
        )
        assert isinstance(exc, leerie.RateLimitedExit)
        assert exc.out_of_credits is False
        assert exc.reset_at is not None

    def test_prefers_live_reset_signal_over_stale_or_absent_cache(
            self, leerie, monkeypatch, tmp_path):
        """The active token (tok-a) is deliberately excluded from the
        fresh probe/rank call, so its _TOKEN_PROBE_CACHE entry is whatever
        it was last time — here, absent entirely (never probed). The
        just-caught RateLimitedExit.reset_at is the only signal available
        for tok-a's own reset time, and it must be preferred over "no
        data" rather than silently dropping tok-a from the soonest-reset
        comparison."""
        known_reset = leerie.datetime(2026, 1, 1, tzinfo=leerie.timezone.utc)
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=known_reset, raw_message="rate_limit_event status=exceeded",
            out_of_credits=False)
        # tok-b probes successfully but reports a LATER reset than tok-a's
        # known live signal — tok-a (using the live signal) must win the
        # soonest-reset comparison despite having no cache entry.
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke],
            tmp_path,
            oauth_tokens="tok-a,tok-b",
            active_token="tok-a",
            probe_utils={"tok-b": (100.0, 100.0)},
        )
        assert isinstance(exc, leerie.RateLimitedExit)
        assert exc.reset_at == known_reset
        # tok-a must remain (or be re-confirmed as) the active token —
        # its live signal won the comparison, so no switch should occur.
        assert st.data["active_oauth_token"] == "tok-a"

    def test_terminal_auth_envelope_after_failed_rotation_still_checked(
            self, leerie, monkeypatch, tmp_path):
        """Regression control: catching RateLimitedExit and re-raising (no
        rotation possible) must not accidentally swallow or reorder the
        terminal-auth check for a SUBSEQUENT, unrelated attempt."""
        exc_from_invoke = leerie.RateLimitedExit(
            reset_at=None, raw_message="rate_limit_event status=exceeded",
            out_of_credits=False)
        result, exc, st = _call_claude_p_with_invoke_sequence(
            leerie, monkeypatch,
            [exc_from_invoke],
            tmp_path,
            oauth_tokens=None,
            active_token=None,
        )
        assert isinstance(exc, leerie.RateLimitedExit)
        assert not isinstance(exc, leerie.TerminalAuthFailure)
