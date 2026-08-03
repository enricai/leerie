"""Rejected-payload diagnostic for schema-mismatch worker failures.

The commonest worker failure signature in a planning run is a
`StructuredOutput` submission the CLI rejects on schema grounds. The error
names the offending fields but never echoes what was actually SENT, and the
payload lives in a preceding stream event the error text cannot reach — so
the failure was undiagnosable from a log. Recovering those payloads for the
2026-08-03 investigation required re-running workers by hand under
`--output-format stream-json`, and doing so is what disproved that
investigation's leading hypothesis about their cause (the payloads turned
out to be valid partial JSON, not the malformation that had been assumed).

`InputValidationError` (unparseable JSON) already logged its payload; this
closes the gap for the parseable-but-invalid case.

Two halves are tested here:

- `_is_schema_rejection` — the narrow gate that decides whether a given
  errored tool_result is a schema rejection at all. Its narrowness is the
  point: an ordinary tool failure must never drag an unrelated structured
  payload into the log beside it.
- `_format_payload_for_log` — the renderer, which must never raise, since a
  diagnostic that crashes `_read_stream` would convert a recoverable schema
  failure into a dead run.

Event shapes are taken from real `claude -p --output-format stream-json`
captures, matching the convention in `test_summarize_stream_event.py`.
"""
from __future__ import annotations

import json

import pytest


def _rejection_event(text: str) -> dict:
    return {"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": True, "content": text,
         "tool_use_id": "tu_1"},
    ]}}


# The verbatim rejection text measured in run d8a764f3 — the shape this
# diagnostic exists to make readable.
_REAL_SCHEMA_ERROR = (
    "Output does not match required schema: "
    "/confidence/falsifiers_tested/3: must NOT have more than 500 characters"
)
_REAL_MISSING_FIELDS_ERROR = (
    "Output does not match required schema: must have required property "
    "'domain', must have required property 'subtasks', must have required "
    "property 'status'"
)


# ----- _is_schema_rejection -------------------------------------------------

@pytest.mark.parametrize("text", [
    _REAL_SCHEMA_ERROR,
    _REAL_MISSING_FIELDS_ERROR,
    "InputValidationError: input could not be parsed as JSON",
])
def test_real_schema_rejections_are_detected(leerie, text):
    assert leerie._is_schema_rejection(_rejection_event(text)) is True


def test_marker_match_is_case_insensitive(leerie):
    """The CLI's casing is not a stable contract; the classifier lowercases
    before comparing so a casing change upstream cannot silently disable the
    diagnostic."""
    assert leerie._is_schema_rejection(
        _rejection_event("OUTPUT DOES NOT MATCH REQUIRED SCHEMA: nope")) is True


@pytest.mark.parametrize("text", [
    "Exit code 1",
    "Error: ENOENT: no such file or directory, open 'src/missing.ts'",
    "FAIL src/foo.test.ts\n  ● renders › matches snapshot",
    "Command timed out after 120s",
])
def test_ordinary_tool_failures_are_not_schema_rejections(leerie, text):
    """The load-bearing negative. If this gate widened, every failing test or
    missing file would print the worker's last structured payload beside it —
    noise attached to precisely the errors a human is already reading
    closely."""
    assert leerie._is_schema_rejection(_rejection_event(text)) is False


def test_successful_tool_result_is_not_a_rejection(leerie):
    """`is_error` is required, not incidental: a SUCCESSFUL tool result whose
    text happens to discuss schemas (a worker grepping for the phrase, or
    reading this very file) must not trip the gate."""
    event = {"type": "user", "message": {"content": [
        {"type": "tool_result", "is_error": False, "tool_use_id": "tu_1",
         "content": "match: Output does not match required schema"},
    ]}}
    assert leerie._is_schema_rejection(event) is False


def test_non_user_events_are_never_rejections(leerie):
    for event in ({"type": "assistant", "message": {"content": []}},
                  {"type": "system"},
                  {"type": "rate_limit_event"},
                  {"type": "result", "result": "does not match required schema"}):
        assert leerie._is_schema_rejection(event) is False


def test_user_event_without_tool_result_is_not_a_rejection(leerie):
    assert leerie._is_schema_rejection(
        {"type": "user", "message": {"content": [
            {"type": "text", "text": "does not match required schema"}]}}
    ) is False


def test_malformed_events_do_not_raise(leerie):
    """`_read_stream` calls this on every event of a live stream. Raising
    here would kill a run over a malformed event."""
    for event in ({}, {"type": "user"}, {"type": "user", "message": None},
                  {"type": "user", "message": {"content": None}},
                  {"type": "user", "message": {"content": [None]}}):
        assert leerie._is_schema_rejection(event) is False


# ----- _format_payload_for_log ---------------------------------------------

def test_none_payload_renders_none_not_the_string(leerie):
    """The caller guards with `and`, so None must skip the log line entirely
    rather than printing the text "None"."""
    assert leerie._format_payload_for_log(None) is None


def test_partial_payload_is_rendered_whole(leerie):
    """The real captured failure. The diagnostic's entire value is showing
    WHICH fields are absent, so a partial object must survive intact."""
    out = leerie._format_payload_for_log({"domain": "bug-fixing"})
    assert json.loads(out) == {"domain": "bug-fixing"}


def test_keys_are_sorted_for_stable_diffing(leerie):
    a = leerie._format_payload_for_log({"b": 1, "a": 2})
    b = leerie._format_payload_for_log({"a": 2, "b": 1})
    assert a == b == '{"a": 2, "b": 1}'


def test_oversized_payload_is_truncated_and_says_so(leerie):
    out = leerie._format_payload_for_log(
        {"subtasks": ["x" * 200 for _ in range(200)]})
    assert len(out) < 200 * 200
    assert "truncated at" in out
    assert str(leerie._REJECTED_PAYLOAD_LOG_MAX) in out


def test_payload_at_the_cap_is_not_truncated(leerie):
    """Boundary: the cap is the max KEPT length, not the max input length."""
    payload = {"k": "x" * (leerie._REJECTED_PAYLOAD_LOG_MAX - 20)}
    out = leerie._format_payload_for_log(payload)
    assert len(json.dumps(payload, sort_keys=True)) <= \
        leerie._REJECTED_PAYLOAD_LOG_MAX
    assert "truncated at" not in out


def test_unserializable_payload_degrades_to_repr_instead_of_raising(leerie):
    """A worker payload is arbitrary; `json.dumps` can raise on it. Degrading
    to `repr` keeps a schema failure recoverable instead of killing the run
    from inside the diagnostic meant to explain it."""
    class Opaque:
        def __repr__(self) -> str:
            return "<Opaque>"

    out = leerie._format_payload_for_log({"x": Opaque()})
    assert "Opaque" in out


def test_non_dict_payloads_render_without_raising(leerie):
    for payload in ("a string", 42, ["a", "list"], True):
        assert leerie._format_payload_for_log(payload) is not None


# ----- wiring ---------------------------------------------------------------

def test_read_stream_latches_and_emits_the_payload(leerie):
    """Source-coupling guard. Both halves are inert without the wiring, and
    the wiring lives inside `_invoke`'s nested `_read_stream` closure, which
    cannot be driven without spawning a real subprocess.

    Pinned: the latch reads `StructuredOutput` submissions, the emit is gated
    on `_is_schema_rejection`, and the latch is CLEARED after printing — so a
    later unrelated error cannot re-print a stale payload from an earlier
    submission.
    """
    import inspect
    src = inspect.getsource(leerie._invoke)
    assert "last_structured_payload" in src
    assert '"StructuredOutput"' in src, "latch must key on the tool name"
    assert "_format_payload_for_log(" in src
    assert "_is_schema_rejection(" in src, "emit must be gated, not unconditional"
    emit = src.index("_is_schema_rejection(")
    tail = src[emit:emit + 600]
    assert "last_structured_payload = None" in tail, (
        "the latch must be cleared after printing, or a later unrelated "
        "failure re-prints a stale payload")


def test_declared_nonlocal_so_the_latch_survives_events(leerie):
    """Without the `nonlocal`, the assignment would bind a fresh local per
    call and the latch would always read None at emit time — the diagnostic
    would silently never fire."""
    import inspect
    src = inspect.getsource(leerie._invoke)
    assert "nonlocal" in src and "last_structured_payload" in src
    nl = [ln for ln in src.splitlines()
          if ln.strip().startswith("nonlocal")
          and "last_structured_payload" in ln]
    assert nl, "last_structured_payload must be declared nonlocal in _read_stream"
