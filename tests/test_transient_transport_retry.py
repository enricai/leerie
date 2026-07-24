"""Tests for the transient-transport-disconnect backoff path (DESIGN §6
*Cleanup on abnormal exit*, IMPLEMENTATION §3 "Transient transport
disconnect").

When the network connection carrying a worker's streaming response drops
mid-answer, `claude -p` surfaces a result envelope with `is_error` set,
`terminal_reason == "api_error"`, a *null* `api_error_status` (the drop
preceded any HTTP status), and result text "API Error: Connection closed
mid-response. The response above may be incomplete." (measured verbatim
from a real feat-005 worker log).

leerie used to fall this through the generic `is_error` arm — an immediate
corrective-note retry with a nonsensical "conform to the schema" nudge,
then a terminal `WorkerError("worker failed schema-valid output twice")`.
It is now classified by `_is_transient_transport_failure` and routed
through the SAME auth/quota tenacity backoff loop (a fresh session after a
short wait — the same remedy 529 already gets).

Covers, mirroring test_auth_backoff.py (classifier corpus) and
test_terminal_auth_routing.py (stubbed-_invoke routing + ast coupling):
  - classifier positive/negative corpus (the measured envelope + siblings;
    successful / synthetic / 401-429-529 / prose-only negatives)
  - a drop envelope enters the backoff loop, not the immediate schema loop
  - budget exhaustion raises a transport-named WorkerError with --resume
  - the two backoff classes partition cleanly (401/429/529 stay auth/quota)
  - source-coupling: the transport classifier is consulted in claude_p
    after terminal-auth, via the combined `_needs_backoff` predicate
"""
from __future__ import annotations

import asyncio
import time

import pytest


# The measured envelope shape from a real drop (feat-005.log). subtype is
# "success" on purpose — the classifier must NOT key on it.
_DROP = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "terminal_reason": "api_error",
    "api_error_status": None,
    "result": ("API Error: Connection closed mid-response. "
               "The response above may be incomplete."),
    "structured_output": None,
}

_VALID = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "{}",
    "structured_output": {"score": 0.9, "rationale": "ok",
                          "diffuse": False,
                          "confidence": {"fit": "high"}},
}


# ---------------------------------------------------------------------------
# classifier corpus  (template: tests/test_auth_backoff.py)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("envelope", [
    _DROP,
    # terminal_reason api_error + null status, no text marker at all
    {"is_error": True, "terminal_reason": "api_error",
     "api_error_status": None, "result": ""},
    # text-marker path (no terminal_reason): sibling transport shapes
    {"is_error": True, "result": "API Error: connection reset by peer"},
    {"is_error": True, "result": "connection error while streaming"},
    {"is_error": True, "result": "timeout while waiting for response"},
    {"is_error": True,
     "result": "the stream died mid-response unexpectedly"},
])
def test_transport_envelopes_match(leerie, envelope):
    assert leerie._is_transient_transport_failure(envelope) is True


@pytest.mark.parametrize("envelope", [
    # a successful, schema-valid envelope never matches, whatever its text
    {"is_error": False,
     "result": "I closed the connection and reset the mid-response buffer"},
    # synthetic no-result envelope is exempt (raw stderr in `result`)
    {"is_error": True, "_leerie_synthetic": "no_result_event",
     "result": "claude -p produced no result event "
               "(stderr: connection closed mid-response)"},
    # 401/429/529 belong to _is_auth_or_quota_failure, not here
    {"is_error": True, "api_error_status": 401, "terminal_reason": "api_error",
     "result": "connection closed"},
    {"is_error": True, "api_error_status": 429, "terminal_reason": "api_error"},
    {"is_error": True, "api_error_status": 529, "terminal_reason": "api_error"},
    # a genuine schema failure — no transport marker, no api_error reason
    {"is_error": True, "terminal_reason": "completed",
     "result": "output did not match schema"},
    # is_error false but text mentions a marker (the exact false-positive
    # the is_error gate exists to prevent)
    {"is_error": False, "result": "connection closed mid-response"},
    # empty / non-string result, no api_error terminal_reason
    {"is_error": True, "result": None},
])
def test_non_transport_envelopes_do_not_match(leerie, envelope):
    assert leerie._is_transient_transport_failure(envelope) is False


def test_transport_and_auth_quota_partition_cleanly(leerie):
    """A drop envelope is transport-not-auth; a 401 is auth-not-transport;
    a 529 is auth-not-transport. The two classifiers never both fire."""
    assert leerie._is_transient_transport_failure(_DROP) is True
    assert leerie._is_auth_or_quota_failure(_DROP) is False
    for status in (401, 429, 529):
        env = {"is_error": True, "api_error_status": status,
               "terminal_reason": "api_error"}
        assert leerie._is_auth_or_quota_failure(env) is True
        assert leerie._is_transient_transport_failure(env) is False


def test_marker_set_is_narrow(leerie):
    """A generic 'error' must not match — only transport-level markers."""
    assert leerie._is_transient_transport_failure(
        {"is_error": True, "result": "some unrelated error occurred"}) is False


# ---------------------------------------------------------------------------
# routing  (template: tests/test_terminal_auth_routing.py)
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


def _call_claude_p(leerie, monkeypatch, envelopes, tmp_path, caps=None):
    """Drive claude_p with a stubbed _invoke yielding `envelopes` in order.

    Returns (result, exc, elapsed_seconds, invoke_call_count)."""
    seq = list(envelopes)
    calls = {"n": 0}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        calls["n"] += 1
        return seq.pop(0) if seq else envelopes[-1]

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
            caps=caps or dict(leerie.DEFAULT_CAPS),
            st=_FakeState(tmp_path),
            model="opus",
            sid="fit-judge-transport-routing",
        )

    start = time.monotonic()
    try:
        result = asyncio.run(run())
        exc = None
    except BaseException as e:  # noqa: BLE001
        result = None
        exc = e
    elapsed = time.monotonic() - start
    return result, exc, elapsed, calls["n"]


def test_drop_enters_backoff_then_recovers(leerie, monkeypatch, tmp_path):
    """A transport drop followed by a valid envelope: claude_p must retry
    (more than one _invoke) and return the recovered structured output.

    tenacity does not sleep before its first iteration, so a drop→valid
    sequence exits the loop before any wait — the test stays sub-second
    while still proving the loop was entered (invoke count > 1, and the
    result is the recovered one)."""
    result, exc, elapsed, n = _call_claude_p(
        leerie, monkeypatch, [_DROP, _VALID], tmp_path)
    assert exc is None, f"unexpected exception: {exc!r}"
    assert result == _VALID["structured_output"]
    assert n > 1, "claude_p did not retry the transport drop"
    assert elapsed < 5.0, (
        f"took {elapsed:.3f}s — a drop→recover sequence must not sleep")


def test_drop_does_not_fail_as_schema_error(leerie, monkeypatch, tmp_path):
    """The whole point: a drop must NOT reach the generic is_error arm and
    raise 'worker failed schema-valid output twice'. Two drops then a valid
    recovery proves the backoff loop (not the 2-attempt schema loop) owns
    it — the 2-attempt schema loop would have raised after the 2nd drop."""
    result, exc, _, n = _call_claude_p(
        leerie, monkeypatch, [_DROP, _DROP, _VALID], tmp_path)
    assert exc is None, f"drop was treated as a terminal schema error: {exc!r}"
    assert result == _VALID["structured_output"]
    assert n >= 3


def test_drop_budget_exhaustion_raises_transport_worker_error(
        leerie, monkeypatch, tmp_path):
    """When the drop persists past the backoff budget, claude_p raises a
    WorkerError that names the transport disconnect (not a subscription cap)
    and points at --resume. Uses auth_retry_max_sec=1 to exhaust after one
    ~15s sleep (same convention as test_terminal_auth_routing.py)."""
    tiny_caps = dict(leerie.DEFAULT_CAPS)
    tiny_caps["auth_retry_max_sec"] = 1
    result, exc, _, n = _call_claude_p(
        leerie, monkeypatch, [_DROP] * 4, tmp_path, caps=tiny_caps)
    assert result is None
    assert isinstance(exc, leerie.WorkerError)
    assert not isinstance(exc, leerie.TerminalAuthFailure)
    msg = str(exc).lower()
    assert "connection" in msg or "transport" in msg, (
        f"exhaustion message must name the transport cause, got: {exc!r}")
    assert "subscription" not in msg, (
        f"a transport drop must not be labeled a subscription cap: {exc!r}")
    assert "--resume" in str(exc)
    assert n > 1, "must have retried before exhausting"


# ---------------------------------------------------------------------------
# source-coupling
# ---------------------------------------------------------------------------

def test_transport_checked_after_terminal_auth_in_claude_p(leerie):
    """Terminal auth (expired session) must be checked before the backoff
    branch, so an expired session is never mistaken for a transport blip."""
    import inspect
    src = inspect.getsource(leerie.claude_p)
    terminal_idx = src.index("_is_terminal_auth_failure(envelope)")
    backoff_idx = src.index("_needs_backoff(envelope)")
    assert terminal_idx < backoff_idx


def test_needs_backoff_ors_both_classifiers(leerie):
    """The combined predicate must consult BOTH the auth/quota and the
    transport classifier — the fix is inert if it drops either."""
    import inspect
    src = inspect.getsource(leerie.claude_p)
    # locate the nested _needs_backoff definition body
    start = src.index("def _needs_backoff(")
    body = src[start:start + 300]
    assert "_is_auth_or_quota_failure" in body
    assert "_is_transient_transport_failure" in body


# ---------------------------------------------------------------------------
# telemetry: a transport drop is tagged api_error:transport in calls.ndjson so
# the failure_kind taxonomy matches the retry taxonomy that now backs it off
# (docs/IMPLEMENTATION.md §3 "Transient transport disconnect").
# ---------------------------------------------------------------------------

def test_transport_drop_failure_kind_is_api_error_transport(leerie):
    """`_classify_failure_kind` sub-tags the measured drop envelope
    `api_error:transport`, distinct from a generic gateway `api_error` and from
    the numeric-status auth/quota/overload sub-tags — reusing
    `_is_transient_transport_failure` so the two taxonomies can't drift."""
    assert leerie._classify_failure_kind(_DROP, parsed_ok=False) == "api_error:transport"


def test_failure_kind_taxonomy_partitions_cleanly(leerie):
    """The transport sub-tag must not perturb the existing failure_kind tags."""
    # numeric statuses keep their category sub-tags
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 401}, parsed_ok=False) == "api_error:auth"
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 429}, parsed_ok=False) == "api_error:quota"
    assert leerie._classify_failure_kind(
        {"is_error": True, "api_error_status": 529}, parsed_ok=False) == "api_error:overload"
    # a bare non-transport is_error stays "api_error"
    assert leerie._classify_failure_kind(
        {"is_error": True, "result": "some unrelated gateway error"},
        parsed_ok=False) == "api_error"
    # a synthetic no-result envelope whose stderr mentions the marker is exempt
    # (matches the retry-path exemption) — stays bare "api_error", not transport
    assert leerie._classify_failure_kind(
        {"is_error": True, "_leerie_synthetic": "no_result_event",
         "result": "claude -p produced no result event (stderr: connection closed)"},
        parsed_ok=False) == "api_error"
    # non-error paths unchanged
    assert leerie._classify_failure_kind({"is_error": False}, parsed_ok=True) is None
    assert leerie._classify_failure_kind(
        {"is_error": False}, parsed_ok=False) == "schema_parse_failed"
