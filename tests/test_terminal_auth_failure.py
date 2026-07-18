"""Tests for the terminal auth-failure classifier and its routing through
`claude_p` into a resumable EXIT_LOCKED pause (DESIGN §6 credential
strategy, bugfix-003).

Run b57027d3… (funeralworks) died after 78 workers of successful planning
spend with:

    leerie: error: worker failed schema-valid output twice:
    Failed to authenticate: OAuth session expired and could not be refreshed

The container's OAuth session had expired mid-run. That envelope carries
`api_error_status: null`, `subtype: "success"`, `is_error: true` — matching
none of `_is_auth_or_quota_failure`'s markers — so it fell through to the
generic 2-attempt schema loop, burned no time there (good), but then raised
a WorkerError blaming schema validation (bad) instead of being recognized
as an unrecoverable auth state and routed to a resumable pause.

Covers:
  - `_is_terminal_auth_failure` table-driven over the measured corpus (4
    real auth strings positive; 8 "API Error: …" strings + empty string
    negative)
  - a `_leerie_synthetic` envelope and a successful OAuth-mentioning
    envelope are both rejected
  - the verbatim b57027d3 envelope replayed through `claude_p` (stubbed
    `_invoke`): never enters the tenacity backoff loop, raises
    TerminalAuthFailure (not the generic "failed schema-valid output
    twice" WorkerError)
  - main()'s TerminalAuthFailure handler exits EXIT_LOCKED (75), not 1
  - 401/429/529 envelopes still route to the existing backoff loop
    unchanged, and still raise WorkerError -> exit 1 on budget
    exhaustion (IMPLEMENTATION.md §3 "Auth/quota backoff" — unchanged
    by this fix; only the terminal classifier match routes to
    EXIT_LOCKED)
"""
from __future__ import annotations

import asyncio
import time

import pytest


# The verbatim envelope shape from
# ~/.leerie/funeralworks/runs/b57027d3.../logs/fit-judge-bugfix-016-1-d1.log
# (quoted verbatim in the task brief). api_error_status is null and
# subtype is "success" — this is a session-level auth failure, not a
# gateway rejection.
_REAL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": None,
    "result": ("Failed to authenticate: OAuth session expired and could "
               "not be refreshed"),
    "structured_output": None,
}

# --- positives: the measured corpus of 4 real terminal-auth strings -------

_AUTH_STRINGS = [
    "Failed to authenticate: OAuth session expired and could not be refreshed",
    "OAuth session expired and could not be refreshed",
    "Failed to authenticate: not logged in",
    "session expired and could not be refreshed",
]

# --- negatives: measured "API Error: ..." corpus + empty string -----------

_API_ERROR_STRINGS = [
    "API Error: 401 Invalid authentication credentials",
    "API Error: 429 Too Many Requests",
    "API Error: 529 Overloaded",
    "API Error: 500 Internal Server Error",
    "API Error: 503 Service Unavailable",
    "API Error: 400 Bad Request",
    "API Error: connection reset by peer",
    "API Error: timeout while waiting for response",
    "",
]


@pytest.mark.parametrize("msg", _AUTH_STRINGS)
def test_terminal_auth_strings_classify_true(leerie, msg):
    assert leerie._is_terminal_auth_failure(
        {"is_error": True, "result": msg}) is True


@pytest.mark.parametrize("msg", [s.upper() for s in _AUTH_STRINGS])
def test_terminal_auth_strings_classify_true_mixed_case(leerie, msg):
    """All four markers must match regardless of case — the classifier
    lowercases the result message before comparing."""
    assert leerie._is_terminal_auth_failure(
        {"is_error": True, "result": msg}) is True


@pytest.mark.parametrize("msg", _API_ERROR_STRINGS)
def test_api_error_strings_do_not_classify(leerie, msg):
    assert leerie._is_terminal_auth_failure(
        {"is_error": True, "result": msg}) is False


def test_real_envelope_classifies_true(leerie):
    assert leerie._is_terminal_auth_failure(_REAL_ENVELOPE) is True


def test_not_shortened_to_bare_oauth(leerie):
    """Guard the investigation note's explicit warning: "oauth" alone
    appears 2919 times in worker tool_result blocks, so the marker must
    be the fuller phrase, not a bare substring match on "oauth"."""
    assert leerie._is_terminal_auth_failure(
        {"is_error": True,
         "result": "the OAuth callback handler validates state params"}
    ) is False


def test_gated_on_is_error(leerie):
    """A successful, schema-valid envelope must never match, no matter
    what its result text says — mirrors _is_auth_or_quota_failure's own
    is_error gate."""
    for msg in _AUTH_STRINGS:
        assert leerie._is_terminal_auth_failure(
            {"is_error": False, "result": msg,
             "structured_output": {"ok": True}}) is False


def test_missing_is_error_key(leerie):
    """is_error absent entirely (real successful-envelope shape) — get()
    returns None, falsy, short-circuits before the text markers."""
    assert leerie._is_terminal_auth_failure(
        {"result": "Failed to authenticate: OAuth session expired and "
                   "could not be refreshed",
         "structured_output": {"ok": True}}) is False


def test_synthetic_envelope_rejected(leerie):
    """The no-result-event envelope interpolates the worker's raw stderr
    into `result`; a worker whose stderr merely mentions these markers
    (without the session actually being locked) must not trip this
    classifier."""
    envelope = {
        "is_error": True,
        "result": "claude -p produced no result event (stderr: Failed to "
                   "authenticate: OAuth session expired and could not be "
                   "refreshed)",
        "structured_output": None,
        "_leerie_synthetic": "no_result_event",
    }
    assert leerie._is_terminal_auth_failure(envelope) is False


def test_successful_envelope_mentioning_oauth_rejected(leerie):
    """A worker legitimately planning/discussing an OAuth flow in its own
    correct output must not trip the classifier."""
    envelope = {
        "is_error": False,
        "result": "Implemented the OAuth session expired and could not "
                   "be refreshed error path per the ticket.",
        "structured_output": {"status": "ready"},
    }
    assert leerie._is_terminal_auth_failure(envelope) is False


def test_classifier_tolerates_non_string_result(leerie):
    assert leerie._is_terminal_auth_failure(
        {"is_error": True, "result": None}) is False


# ---------------------------------------------------------------------------
# 401/429/529 still route to the existing backoff loop, unchanged
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 429, 529])
def test_numeric_gateway_statuses_do_not_classify_as_terminal(leerie, status):
    """401/429/529 are the transient/rolling-cap case
    (_is_auth_or_quota_failure's territory) — they must not be swept up
    by the terminal classifier, or they'd skip backoff entirely."""
    assert leerie._is_terminal_auth_failure(
        {"is_error": True, "api_error_status": status,
         "result": "API Error"}) is False
    # ...and still classify as the existing auth/quota case.
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "api_error_status": status,
         "result": "API Error"}) is True


# ---------------------------------------------------------------------------
# claude_p() routing: the verbatim envelope never enters the tenacity loop
# ---------------------------------------------------------------------------

class _FakeState:
    """Minimal State stand-in for claude_p (see test_no_result_event_retry.py)."""

    def __init__(self, tmp_path):
        self.path = tmp_path / "runs" / "r1" / "state.json"
        self.run_dir = self.path.parent
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = "r1"
        self.data = {"verbosity": "quiet"}
        self.bumped = 0

    def bump_workers(self, *a, **k):
        self.bumped += 1

    def add_telemetry(self, *a, **k):
        pass


def _call_claude_p(leerie, monkeypatch, envelopes, tmp_path):
    """Drive claude_p with a stubbed _invoke yielding `envelopes` in order.

    Returns (result_or_exc, elapsed_seconds)."""
    seq = list(envelopes)

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        return seq.pop(0)

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)

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
            st=_FakeState(tmp_path),
            model="opus",
            sid="fit-judge-bugfix-016-1-d1",
        )

    start = time.monotonic()
    try:
        result = asyncio.run(run())
        exc = None
    except BaseException as e:  # noqa: BLE001 - want any exception, incl. BaseException subclasses
        result = None
        exc = e
    elapsed = time.monotonic() - start
    return result, exc, elapsed


def test_real_envelope_never_enters_tenacity_loop(leerie, monkeypatch, tmp_path):
    """The whole point of the fix: this must complete ~instantly, not burn
    the ~300s auth_retry_max_sec backoff budget. If the classifier failed
    to catch it before the backoff loop, this test would hang for minutes
    (tenacity's stop_after_delay would eventually fire, but only after
    real sleeps — wait_exponential_jitter's `initial=15` alone would make
    this test far exceed a sane bound)."""
    result, exc, elapsed = _call_claude_p(
        leerie, monkeypatch, [dict(_REAL_ENVELOPE)], tmp_path)
    assert isinstance(exc, leerie.TerminalAuthFailure), (
        f"expected TerminalAuthFailure, got {exc!r} (result={result!r})")
    assert elapsed < 5, (
        f"took {elapsed:.1f}s — the auth/quota backoff loop must never "
        f"have run for a terminal auth failure")


def test_real_envelope_exits_terminal_auth_failure_not_worker_error(
        leerie, monkeypatch, tmp_path):
    """The reported bug: exit 1 blaming schema validation. The fix routes
    through a distinct exception, not the generic 2-attempt WorkerError."""
    _, exc, _ = _call_claude_p(
        leerie, monkeypatch, [dict(_REAL_ENVELOPE)], tmp_path)
    assert isinstance(exc, leerie.TerminalAuthFailure)
    assert not isinstance(exc, leerie.WorkerError)
    assert "worker failed schema-valid output twice" not in str(exc.raw_message)
    assert "OAuth session expired" in exc.raw_message


@pytest.mark.parametrize("status", [401, 429, 529])
def test_numeric_gateway_status_still_enters_backoff(
        leerie, monkeypatch, tmp_path, status):
    """401/429/529 must still hit the auth/quota tenacity loop, not the
    terminal-auth short-circuit. `auth_retry_max_sec` is set to tenacity's
    real minimum wait (~15s, jittered) so this proves the loop actually
    slept and retried at least once before exhausting, at the cost of one
    bounded real sleep per parametrized case."""
    envelope = {"is_error": True, "api_error_status": status,
                "result": "API Error"}

    seq = [envelope] * 4  # plenty to exhaust the tiny budget
    invoke_calls = []

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        invoke_calls.append(1)
        return seq.pop(0) if seq else envelope

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)

    tiny_caps = dict(leerie.DEFAULT_CAPS)
    tiny_caps["auth_retry_max_sec"] = 1  # exhausts after the first ~15s sleep

    async def run():
        return await leerie.claude_p(
            "do the task",
            "you are a fit judge",
            schema_key="fit_judge",
            cwd="/work",
            allowed_tools="Read",
            max_turns=60,
            autonomous=False,
            caps=tiny_caps,
            st=_FakeState(tmp_path),
            model="opus",
            sid="fit-judge-numeric",
        )

    # Budget exhaustion still raises WorkerError (unchanged by this fix —
    # see IMPLEMENTATION.md §3 "Auth/quota backoff"): only the terminal
    # classifier match (checked before this loop) routes to
    # TerminalAuthFailure / EXIT_LOCKED. 401/429/529 is the transient
    # case; a retry has a real chance of success once the window clears.
    with pytest.raises(leerie.WorkerError):
        asyncio.run(run())
    # The backoff loop must have actually retried (more than the single
    # initial _spawn) before exhausting — proving it entered tenacity's
    # loop rather than short-circuiting through the terminal-auth path.
    assert len(invoke_calls) > 1


# ---------------------------------------------------------------------------
# main()'s exit-handler routing: TerminalAuthFailure -> EXIT_LOCKED (75)
# ---------------------------------------------------------------------------

def test_terminal_auth_failure_maps_to_exit_locked(leerie):
    """Source-coupling guard: main()'s except TerminalAuthFailure arm sets
    exit_code = EXIT_LOCKED, mirroring the RateLimitedExit
    out_of_credits=True arm it was copied from.

    Matches the real assignment statement (`exit_code = EXIT_LOCKED`) via
    AST rather than a bare substring check on the block text — a bare
    `"EXIT_LOCKED" in block` check is satisfied by this arm's own
    comments/docstrings (which reference EXIT_LOCKED by name) even if
    the actual assignment were mutated to a different value, so it
    would not catch that regression."""
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(leerie.main))
    tree = ast.parse(src)
    handler = None
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            name = getattr(node.type, "id", None)
            if name == "TerminalAuthFailure":
                handler = node
                break
    assert handler is not None, "no except TerminalAuthFailure handler found"
    block = ast.unparse(handler)
    assert "exit_code = EXIT_LOCKED" in block
    assert "exit_code = 1" not in block
    assert "--resume" in block


def test_exit_locked_is_75(leerie):
    assert leerie.EXIT_LOCKED == 75


def test_terminal_auth_failure_checked_before_auth_or_quota_in_claude_p(leerie):
    """Source-coupling guard: _is_terminal_auth_failure must be checked
    before _is_auth_or_quota_failure inside claude_p's retry loop, else
    the terminal case falls into the tenacity backoff first."""
    import inspect
    src = inspect.getsource(leerie.claude_p)
    terminal_idx = src.index("_is_terminal_auth_failure(envelope)")
    quota_idx = src.index("_is_auth_or_quota_failure(envelope)")
    assert terminal_idx < quota_idx


def test_terminal_auth_failure_is_a_base_exception(leerie):
    """Must inherit BaseException (not just RuntimeError/WorkerError) so
    it propagates through asyncio's gather and broad `except Exception`
    handlers scattered through the orchestrator without being swallowed —
    same discipline as RateLimitedExit."""
    assert issubclass(leerie.TerminalAuthFailure, BaseException)
    assert not issubclass(leerie.TerminalAuthFailure, leerie.WorkerError)
