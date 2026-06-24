"""Tests for the Fix C helpers in `_run_conformance_phase` /
`run_final_conformance` — `_count_bash_axis_invocations` and
`_count_orphaned_bg_axis`.

These helpers parse the per-worker JSONL log (the same one the
orchestrator already writes to `.leerie/runs/<id>/logs/<sid>.log`) and
surface advisory warnings when the conformer worker (a) invoked a
single BLT axis more than once in one round, or (b) fired a fresh
test/build command in response to a Bash-tool auto-backgrounded prior
one (the retry-instead-of-recover antipattern Fix A is designed to
prevent — see conformer.md §4).

The helpers are advisory: a parsing failure on a single log line must
not break the run, so the tests cover malformed-line tolerance
explicitly.
"""
from __future__ import annotations

import json
import re

import pytest


def _write_log(path, events):
    """Write `events` as one-per-line JSONL. Each `event` is a dict that
    represents the `body` portion of a leerie per-worker log line — the
    helpers ignore the timestamp prefix lines the orchestrator writes
    above each body, so the test fixtures can be body-only."""
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def _bash_event(tid, cmd, timeout=None):
    inp = {"command": cmd}
    if timeout is not None:
        inp["timeout"] = timeout
    return {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Bash", "input": inp}
    ]}}


def _bash_output_event(tid, shell_id):
    return {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "BashOutput",
         "input": {"shell_id": shell_id}}
    ]}}


def _read_event(tid, file_path):
    return {"message": {"content": [
        {"type": "tool_use", "id": tid, "name": "Read",
         "input": {"file_path": file_path}}
    ]}}


def _result_event(tid, text):
    return {"message": {"content": [
        {"type": "tool_result", "tool_use_id": tid, "content": text}
    ]}}


def _bg_text(bg_id):
    return (f"Command running in background with ID: {bg_id}. Output is "
            f"being written to: /tmp/leerie-test/{bg_id}.output")


# ---------------------------------------------------------------------------
# _count_bash_axis_invocations
# ---------------------------------------------------------------------------

def test_count_zero_when_log_missing(leerie, tmp_path):
    """A missing log file is not an error; helpers are advisory and run
    on best-effort basis."""
    assert leerie._count_bash_axis_invocations(
        tmp_path / "no.log", leerie._BLT_AXIS_RES["test"]) == 0


def test_count_zero_when_no_matching_invocations(leerie, tmp_path):
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "git log --oneline -5"),
        _result_event("a", "abc123 something"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 0


def test_count_one_npm_test(leerie, tmp_path):
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", "Tests passed"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 1


def test_count_multiple_blt_shapes(leerie, tmp_path):
    """The 4 conformer-004 invocations from the original telemetry —
    `npm test`, `pnpm test`, `npx vitest run`, `npx vitest run --reporter`
    — must all match the test regex."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1 | tail -40"),
        _result_event("a", _bg_text("b1")),
        _bash_event("b", "pnpm test 2>&1 | tail -30"),
        _result_event("b", _bg_text("b2")),
        _bash_event("c", "npx vitest run 2>&1 | tail -40"),
        _result_event("c", _bg_text("b3")),
        _bash_event("d", "npx vitest run --reporter=verbose 2>&1"),
        _result_event("d", _bg_text("b4")),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 4


def test_count_bin_rails_test(leerie, tmp_path):
    """bin/rails test must match the test axis regex."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "bin/rails test test/models/ 2>&1"),
        _result_event("a", "1 runs, 1 assertions, 0 failures"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 1


def test_count_rubocop_matches_lint_axis(leerie, tmp_path):
    """rubocop (bare and via bundle exec) must match the lint axis regex."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "bundle exec rubocop app/ 2>&1"),
        _result_event("a", "no offenses"),
        _bash_event("b", "rubocop --only Style 2>&1"),
        _result_event("b", "no offenses"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["lint"]) == 2


def test_count_build_matches_tsc_and_next_build(leerie, tmp_path):
    """Build regex must catch `pnpm build`, `tsc`, `next build`."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "pnpm build"),
        _result_event("a", "ok"),
        _bash_event("b", "tsc --noEmit"),
        _result_event("b", "ok"),
        _bash_event("c", "next build"),
        _result_event("c", "ok"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["build"]) == 3


def test_count_lint_matches_biome_and_eslint(leerie, tmp_path):
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "pnpm run lint"),
        _result_event("a", "ok"),
        _bash_event("b", "biome check src"),
        _result_event("b", "ok"),
        _bash_event("c", "eslint src/foo.ts"),
        _result_event("c", "ok"),
    ])
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["lint"]) == 3


def test_count_tolerates_malformed_lines(leerie, tmp_path):
    """Helpers must skip non-JSON / wrong-shape lines silently."""
    log = tmp_path / "w.log"
    log.write_text(
        "this is not json\n"
        + json.dumps(_bash_event("a", "npm test")) + "\n"
        + "{ also not json{\n"
        + json.dumps({"type": "system", "no": "message"}) + "\n"
        + json.dumps(_result_event("a", "ok")) + "\n"
    )
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 1


# ---------------------------------------------------------------------------
# _count_orphaned_bg_axis
# ---------------------------------------------------------------------------

def test_orphan_when_bg_followed_by_another_same_axis_bash(leerie, tmp_path):
    """The pattern from the failing run: `npm test` auto-backgrounds,
    next Bash is another `pnpm test`. The bash_id of the orphaned bg
    job must be returned."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b44wb370b")),
        _bash_event("b", "pnpm test 2>&1"),  # retry
        _result_event("b", _bg_text("bzzwqb0b3")),
    ])
    orphans = leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"])
    assert orphans == ["b44wb370b"]


def test_not_orphan_when_polled_via_bashoutput(leerie, tmp_path):
    """Recovery path 1: model calls `BashOutput shell_id=<id>` after
    the auto-background. No orphan."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b44wb370b")),
        _bash_output_event("p1", "b44wb370b"),
        _result_event("p1", "<status>completed</status>"),
    ])
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == []


def test_not_orphan_when_temp_file_read(leerie, tmp_path):
    """Recovery path 2 (the most common in the failing run telemetry —
    ~33% of recoveries): model uses `Read file_path=<tmp>/<bg_id>.output`
    to retrieve the result. No orphan."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b44wb370b")),
        _read_event("r1", "/tmp/leerie-test/b44wb370b.output"),
        _result_event("r1", "Tests passed"),
    ])
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == []


def test_foreground_command_is_never_orphan(leerie, tmp_path):
    """A test command that ran in-band (no `Command running in
    background` result) cannot be an orphan."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1", timeout=600000),
        _result_event("a", "Tests: 100 passed, 0 failed"),
        _bash_event("b", "npm test 2>&1"),  # second test, also fg
        _result_event("b", "Tests: 100 passed, 0 failed"),
    ])
    # Two test invocations but neither backgrounded — no orphans
    # (the *count* helper would flag this case as a multi-invocation
    # warning; the *orphan* helper does not).
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == []


def test_bg_id_extraction_regex(leerie):
    """The bash_id extraction depends on the exact tool wording. If
    Claude Code changes the message, the helper silently underreports,
    so the wording is anchored by this test."""
    text = ("Command running in background with ID: b44wb370b. Output is "
            "being written to: /tmp/foo.output")
    m = leerie._BG_ID_RE.search(text)
    assert m is not None
    assert m.group(1) == "b44wb370b"


def test_unrelated_bash_between_bg_and_recovery_doesnt_break_recovery(
        leerie, tmp_path):
    """Realistic shape: model fires test, sees bg, runs `cat` on the
    tmp file to inspect, then either retries or moves on. A `cat
    /tmp/.../<bg_id>.output` between the bg and a subsequent test
    invocation should count as recovery (shell-inspected the bg
    job output), not orphan."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b44wb370b")),
        _bash_event("b", "cat /tmp/leerie-test/b44wb370b.output"),
        _result_event("b", "Tests passed"),
    ])
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == []


# ---------------------------------------------------------------------------
# _emit_bash_axis_warnings — the warning shapes
# ---------------------------------------------------------------------------

def test_emit_no_warnings_on_well_behaved_round(leerie, tmp_path):
    """A round where TEST_CMD ran exactly once and didn't background
    emits zero warnings."""
    log = tmp_path / "w-conformer.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1", timeout=600000),
        _result_event("a", "Tests passed"),
    ])
    warnings: list[str] = []
    leerie._emit_bash_axis_warnings(log, "conformer round 0", warnings)
    assert warnings == []


def test_emit_warns_on_multi_invocation_in_one_round(leerie, tmp_path):
    log = tmp_path / "w-conformer.log"
    _write_log(log, [
        _bash_event("a", "npm test", timeout=600000),
        _result_event("a", "ok"),
        _bash_event("b", "pnpm test"),
        _result_event("b", "ok"),
        _bash_event("c", "npx vitest run --reporter=verbose"),
        _result_event("c", "ok"),
    ])
    warnings: list[str] = []
    leerie._emit_bash_axis_warnings(log, "conformer round 0", warnings)
    assert any(
        "ran TEST_CMD 3 times in one round" in w
        and "conformer round 0" in w
        and "conformer.md §4" in w
        for w in warnings), warnings


def test_emit_warns_on_retry_after_background(leerie, tmp_path):
    log = tmp_path / "w-conformer.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("bxyz123")),
        _bash_event("b", "pnpm test 2>&1"),  # retry
        _result_event("b", _bg_text("bxyz456")),
    ])
    warnings: list[str] = []
    leerie._emit_bash_axis_warnings(log, "conformer round 0", warnings)
    assert any(
        "TEST_CMD auto-backgrounded (bash_id=bxyz123)" in w
        and "retry-instead-of-recover" in w
        and "timeout: 600000" in w
        for w in warnings), warnings


def test_emit_silently_skips_when_log_missing(leerie, tmp_path):
    """A missing log file does not raise — advisory by design."""
    warnings: list[str] = []
    leerie._emit_bash_axis_warnings(
        tmp_path / "no-such.log", "conformer round 0", warnings)
    assert warnings == []


def test_tolerates_non_dict_message_field(leerie, tmp_path):
    """A JSONL line where `message` is a non-dict value (e.g. a string
    or null) must be skipped silently. Without the isinstance(msg, dict)
    guard the helper crashed with AttributeError on `msg.get("content")`
    — breaking the docstring's malformed-line tolerance contract."""
    log = tmp_path / "w.log"
    log.write_text(
        json.dumps({"message": "this is a string, not a dict"}) + "\n"
        + json.dumps({"message": None}) + "\n"
        + json.dumps(_bash_event("a", "npm test")) + "\n"
        + json.dumps(_result_event("a", "ok")) + "\n"
    )
    assert leerie._count_bash_axis_invocations(
        log, leerie._BLT_AXIS_RES["test"]) == 1


def test_bg_followed_by_end_of_log_is_not_orphan(leerie, tmp_path):
    """An auto-backgrounded TEST that the worker never recovers from
    AND never retries (the turn simply ended) is not flagged as an
    orphan-by-retry — the orphan warning targets the retry antipattern
    specifically, not abandonment."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b44wb370b")),
    ])
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == []


def test_only_first_of_two_consecutive_bg_same_axis_is_orphan(
        leerie, tmp_path):
    """When two test commands both auto-background back-to-back with
    nothing between them, only the *first* is flagged as orphaned —
    the second is itself the retry that orphans the first, and isn't
    yet itself a retry of anything."""
    log = tmp_path / "w.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("b1")),
        _bash_event("b", "pnpm test 2>&1"),
        _result_event("b", _bg_text("b2")),
    ])
    assert leerie._count_orphaned_bg_axis(
        log, leerie._BLT_AXIS_RES["test"]) == ["b1"]


def test_warning_text_recommends_read_not_bashoutput(leerie, tmp_path):
    """The retry-after-backgrounded warning must recommend temp-file
    `Read` for recovery, not `BashOutput shell_id=<id>`. The conformer
    runs with `--allowedTools ACT_TOOLS`, and `ACT_TOOLS` does not
    include `BashOutput` — recommending it would tell the worker to
    call a tool it doesn't have. (Detection of BashOutput as recovery
    in `_count_orphaned_bg_axis` stays — it's forward-compatible with
    future tool-surface changes — but the recommendation does not.)"""
    log = tmp_path / "w-conformer.log"
    _write_log(log, [
        _bash_event("a", "npm test 2>&1"),
        _result_event("a", _bg_text("bxyz")),
        _bash_event("b", "pnpm test 2>&1"),
        _result_event("b", _bg_text("bxyz2")),
    ])
    warnings: list[str] = []
    leerie._emit_bash_axis_warnings(log, "conformer round 0", warnings)
    msg = next(w for w in warnings if "auto-backgrounded" in w)
    assert "Read file_path" in msg
    assert "BashOutput" not in msg
    assert "shell_id" not in msg
