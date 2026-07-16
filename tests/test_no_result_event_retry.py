"""Tests for the no-result-event retry path (DESIGN §6).

`claude -p` intermittently exits 0 having streamed a full session but
never emitting its terminal `result` event (anthropics/claude-code #8126,
#1920, #74761 — upstream, unfixed, no public repro). leerie used to raise
a bare WorkerError there, which propagated past claude_p's corrective
retry loop and die()d the run non-resumably — losing a worker's entire
completed reasoning to a transient upstream fault.

`_invoke` now returns a synthetic error envelope for that case instead,
which routes into the existing 2-attempt loop and buys one fresh session.

Covers:
  - the synthetic envelope never matches `_is_auth_or_quota_failure`
    (a false match would burn the whole auth backoff budget)
  - attempt 1 no-result + attempt 2 valid => claude_p returns the result
  - the attempt-2 prompt names the StructuredOutput tool
  - two no-result attempts still raise WorkerError (worst case unchanged)
  - a schema-mismatch retry keeps its own (non-StructuredOutput) wording
  - the named/terminal branches above it still RAISE, not return:
    nonzero rc (covers leerie's own deliberate kills) and OOM
"""
from __future__ import annotations

import asyncio

import pytest


_SYNTHETIC = {
    "is_error": True,
    "result": "claude -p produced no result event (stderr: (empty))",
    "structured_output": None,
    "_leerie_synthetic": "no_result_event",
}

_VALID = {
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "{}",
    "structured_output": {"categories": ["feature-implementation"]},
}


class _FakeState:
    """Minimal State stand-in for claude_p: it reads `path` (for
    leerie_dir), `data` (verbosity / skip-permissions) and bumps the
    worker budget."""

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

    Returns (result_or_exc, prompts_seen)."""
    prompts: list[str] = []
    seq = list(envelopes)

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        # The user prompt is the argv token after `-p`.
        prompts.append(cmd[cmd.index("-p") + 1] if "-p" in cmd else "")
        return seq.pop(0)

    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)

    async def run():
        return await leerie.claude_p(
            "classify this task",
            "you are a classifier",
            schema_key="classifier",
            cwd="/work",
            allowed_tools="Read",
            max_turns=60,
            autonomous=False,
            caps=dict(leerie.DEFAULT_CAPS),
            st=_FakeState(tmp_path),
            model="opus",
            sid="classifier",
        )

    return asyncio.run(run()), prompts


# ---------------------------------------------------------------------------
# the landmine: synthetic must not look like an auth/quota failure
# ---------------------------------------------------------------------------

def _real_synthetic_message(leerie) -> str:
    """The `result` text the SHIPPING code puts on the synthetic envelope.

    Extracted from `_invoke`'s source rather than copied into this file: a
    hand-copied fixture cannot catch someone editing the real message to
    contain a text marker, which is the entire failure this pins.
    """
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(leerie._invoke))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        # the synthetic dict literal: find it by its marker key
        if not isinstance(node, ast.Dict):
            continue
        keys = [k.value for k in node.keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)]
        if "_leerie_synthetic" not in keys:
            continue
        idx = keys.index("result")
        # `result` is a JoinedStr/implicit concat — collect its literal parts
        val = node.values[idx]
        parts = []
        for sub in ast.walk(val):
            if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                parts.append(sub.value)
        return " ".join(parts)
    raise AssertionError("synthetic envelope literal not found in _invoke")


def test_synthetic_envelope_is_not_an_auth_or_quota_failure(leerie):
    """`_is_auth_or_quota_failure` is gated on is_error then falls back to
    text markers on `result` ("rate limit", "invalid authentication"). The
    synthetic envelope sets is_error=True, so a message containing any of
    those markers would divert it into tenacity's backoff and burn the
    entire auth_retry_max_sec budget on a non-auth failure.

    Pins the REAL message from the shipping source, not a copy of it.
    """
    real_msg = _real_synthetic_message(leerie)
    probe = {"is_error": True, "result": real_msg,
             "structured_output": None,
             "_leerie_synthetic": "no_result_event"}
    assert leerie._is_auth_or_quota_failure(probe) is False, (
        f"the synthetic no-result message trips the auth/quota text "
        f"markers and would burn the whole backoff budget: {real_msg!r}")


def test_synthetic_message_contains_no_auth_text_markers(leerie):
    """Direct, readable statement of the constraint above."""
    msg = _real_synthetic_message(leerie).lower()
    for marker in ("invalid authentication", "rate limit", "rate-limit"):
        assert marker not in msg, (
            f"synthetic no-result message must not contain {marker!r}")


@pytest.mark.parametrize("stderr", [
    "Error: Invalid authentication credentials",
    "warning: approaching rate limit",
    "connection reset (rate-limit proxy)",
])
def test_worker_stderr_cannot_trip_the_auth_classifier(leerie, stderr):
    """The no-result envelope interpolates the worker's **raw stderr** into
    `result`, and `_is_auth_or_quota_failure` text-matches that field. So a
    worker whose stderr merely mentions auth or rate limiting — without the
    request ever having been auth-rejected — would divert its retry into
    the tenacity backoff and burn the entire auth_retry_max_sec budget.

    Controlling leerie's own message is not enough: stderr is attacker-,
    or rather upstream-, controlled text. `_is_auth_or_quota_failure`
    exempts `_leerie_synthetic` envelopes from the text markers entirely —
    the markers exist to sniff a *gateway* message out of an envelope of
    unknown provenance, and leerie knows what its own envelopes mean.
    """
    envelope = {
        "is_error": True,
        "result": f"claude -p produced no result event (stderr: {stderr})",
        "structured_output": None,
        "_leerie_synthetic": "no_result_event",
    }
    assert leerie._is_auth_or_quota_failure(envelope) is False, (
        f"worker stderr {stderr!r} tripped the auth/quota classifier — the "
        f"retry would burn the full backoff budget on a non-auth failure")


def test_synthetic_with_a_real_api_status_still_backs_off(leerie):
    """The exemption must not swallow a genuine gateway rejection: the
    numeric `api_error_status` check runs before it."""
    envelope = {
        "is_error": True,
        "api_error_status": 401,
        "result": "claude -p produced no result event (stderr: (empty))",
        "structured_output": None,
        "_leerie_synthetic": "no_result_event",
    }
    assert leerie._is_auth_or_quota_failure(envelope) is True


def test_real_envelopes_still_match_the_text_markers(leerie):
    """The exemption is scoped to synthetic envelopes only — a real
    gateway envelope with no numeric status still matches on text."""
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "result": "Invalid authentication"}) is True
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "result": "you hit a rate limit"}) is True


def test_auth_classifier_still_matches_real_auth_failures(leerie):
    """Guard the negative above against over-correction."""
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "api_error_status": 401}) is True
    assert leerie._is_auth_or_quota_failure(
        {"is_error": True, "result": "hit a rate limit"}) is True


# ---------------------------------------------------------------------------
# the retry itself
# ---------------------------------------------------------------------------

def test_no_result_then_valid_returns_the_valid_result(leerie, monkeypatch, tmp_path):
    """The whole point: a transient no-result no longer kills the run."""
    result, _ = _call_claude_p(leerie, monkeypatch, [_SYNTHETIC, _VALID], tmp_path)
    assert result == {"categories": ["feature-implementation"]}


def test_retry_prompt_names_the_structured_output_tool(leerie, monkeypatch, tmp_path):
    """The nudge is cheap insurance: the model rarely *chooses* to skip the
    finalizer, but if it did, naming the tool is the corrective signal."""
    _, prompts = _call_claude_p(leerie, monkeypatch, [_SYNTHETIC, _VALID], tmp_path)
    assert len(prompts) == 2
    assert "StructuredOutput" in prompts[1]
    assert "YOUR PREVIOUS ATTEMPT FAILED" in prompts[1]
    # Attempt 1 is the unmodified prompt.
    assert "StructuredOutput" not in prompts[0]


def test_two_no_results_still_raise_worker_error(leerie, monkeypatch, tmp_path):
    """Worst case is unchanged from before the fix — bounded, not infinite."""
    with pytest.raises(leerie.WorkerError, match="failed schema-valid output twice"):
        _call_claude_p(leerie, monkeypatch, [_SYNTHETIC, _SYNTHETIC], tmp_path)


def test_schema_mismatch_retry_keeps_its_own_wording(leerie, monkeypatch, tmp_path):
    """A missing-structured_output envelope is a *schema* failure, not a
    session failure: it must not get the StructuredOutput nudge."""
    schema_miss = {"type": "result", "is_error": False, "result": "{}",
                   "structured_output": None}
    _, prompts = _call_claude_p(leerie, monkeypatch, [schema_miss, _VALID], tmp_path)
    assert len(prompts) == 2
    assert "conforms exactly to the required schema" in prompts[1]
    assert "StructuredOutput" not in prompts[1]


# ---------------------------------------------------------------------------
# scope: only the unnamed rc=0 fallthrough returns; named causes still raise
# ---------------------------------------------------------------------------

def test_only_the_no_result_branch_is_synthetic(leerie):
    """Source-coupling guard on the branch ordering in `_invoke`.

    The synthetic return must be the LAST arm of the `envelope is None`
    block. Every arm above it is a named, non-retryable condition that
    still raises — in particular the nonzero-rc arm, which covers leerie's
    own deliberate kills (SIGTERM/SIGKILL). Retrying a worker leerie
    intentionally killed would be wrong.
    """
    import inspect
    src = inspect.getsource(leerie._invoke)
    block = src[src.index("if envelope is None:"):]
    # The three named arms keep raising.
    assert "raise RateLimitedExit(" in block
    assert "was OOM-killed on" in block
    assert "claude -p exited " in block
    # …and the synthetic return comes after the nonzero-rc raise.
    assert block.index("claude -p exited ") < block.index("_leerie_synthetic")
