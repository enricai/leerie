"""Tests for the `claude_p`/`main()` routing seam of terminal auth failures
(DESIGN §6 credential strategy), distinct from
`tests/test_terminal_auth_failure.py`'s classifier-only coverage.

That run (b57027d3…) died after 78 workers of successful planning spend
because a terminal auth envelope (`Failed to authenticate: OAuth session
expired and could not be refreshed`) matched none of
`_is_auth_or_quota_failure`'s markers, fell through to the generic
2-attempt schema loop, and raised a WorkerError blaming schema validation
at exit 1 — with no `resume` hint honored, despite `resume` being the
correct recovery.

This file drives `claude_p` directly with a stubbed `_invoke` (the only
way to observe retry counts and elapsed time — a unit test on the
classifier alone, as test-004 already covers, cannot see this) and pins:

  - the terminal-auth envelope causes exactly one `_invoke` call (no
    retry) and completes in well under a second (the 300s tenacity
    auth/quota budget is never entered)
  - the raised exception is `TerminalAuthFailure`, not a generic
    `WorkerError`, and its message does not blame schema validation
  - `main()`'s `except TerminalAuthFailure` handler maps to
    `exit_code = EXIT_LOCKED`, calls
    `_cleanup_on_abnormal_exit(st, full_purge=False)`, and sets
    `abnormal = False` (source-coupling, since the suite does not drive
    `main()` to a real process exit)
  - control: a 401 envelope whose auth/quota backoff budget exhausts
    still raises `WorkerError`, not `TerminalAuthFailure` — this is the
    doc-conformant behavior per `docs/IMPLEMENTATION.md` §3 "Auth/quota
    backoff" (401/429/529 stay transient; only the terminal classifier
    match, checked before this loop, routes to EXIT_LOCKED). Commit
    `18b61b1` briefly rerouted the exhaustion arm too, and `2652319`
    reverted that as an over-application — this test pins the current,
    doc-conformant behavior so the terminal-auth reroute is proven not
    to have swallowed the transient case.
  - source-coupling: `_is_terminal_auth_failure` is consulted before
    `_is_auth_or_quota_failure` inside `claude_p`
"""
from __future__ import annotations

import asyncio
import time

import pytest


# The verbatim envelope shape from the reported incident. api_error_status
# is null and subtype is "success" — a session-level auth failure, not a
# gateway rejection.
_TERMINAL_ENVELOPE = {
    "type": "result",
    "subtype": "success",
    "is_error": True,
    "api_error_status": None,
    "result": ("Failed to authenticate: OAuth session expired and could "
               "not be refreshed"),
    "structured_output": None,
}


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

    Returns (result, exc, elapsed_seconds, invoke_call_count)."""
    seq = list(envelopes)
    calls = {"n": 0}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          **kwargs):
        calls["n"] += 1
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
            sid="fit-judge-terminal-auth-routing",
        )

    start = time.monotonic()
    try:
        result = asyncio.run(run())
        exc = None
    except BaseException as e:  # noqa: BLE001 - want any exception, incl. BaseException subclasses
        result = None
        exc = e
    elapsed = time.monotonic() - start
    return result, exc, elapsed, calls["n"]


# ---------------------------------------------------------------------------
# claude_p(): terminal auth never enters the tenacity loop, exactly one
# _invoke call, sub-second elapsed time
# ---------------------------------------------------------------------------

def test_terminal_auth_invoked_exactly_once(leerie, monkeypatch, tmp_path):
    """No retry, no second attempt — the terminal-auth check fires on the
    very first envelope and raises immediately."""
    _, exc, _, n = _call_claude_p(
        leerie, monkeypatch, [dict(_TERMINAL_ENVELOPE)], tmp_path)
    assert isinstance(exc, leerie.TerminalAuthFailure)
    assert n == 1, f"expected exactly one _invoke call, got {n}"


def test_terminal_auth_completes_in_well_under_a_second(
        leerie, monkeypatch, tmp_path):
    """The 300s tenacity auth/quota budget is never entered — the 300s
    burn IS the bug. A future regression that routes this envelope through
    even a single tenacity sleep (wait_exponential_jitter's initial=15)
    would blow this bound by more than an order of magnitude, so 1s is a
    safe, non-flaky threshold."""
    _, exc, elapsed, _ = _call_claude_p(
        leerie, monkeypatch, [dict(_TERMINAL_ENVELOPE)], tmp_path)
    assert isinstance(exc, leerie.TerminalAuthFailure)
    assert elapsed < 1.0, (
        f"took {elapsed:.3f}s — the auth/quota backoff loop must never "
        f"have run for a terminal auth failure")


def test_terminal_auth_raises_distinct_type_not_generic_worker_error(
        leerie, monkeypatch, tmp_path):
    """The reported bug: a generic WorkerError blaming schema validation.
    The fix raises a distinct exception type whose message does not
    mention schema validation at all."""
    _, exc, _, _ = _call_claude_p(
        leerie, monkeypatch, [dict(_TERMINAL_ENVELOPE)], tmp_path)
    assert isinstance(exc, leerie.TerminalAuthFailure)
    assert not isinstance(exc, leerie.WorkerError)
    assert "schema-valid output twice" not in str(exc)
    assert "schema-valid output twice" not in str(exc.raw_message)


# ---------------------------------------------------------------------------
# main() handler: TerminalAuthFailure -> EXIT_LOCKED, cleanup, abnormal=False
# ---------------------------------------------------------------------------

def _terminal_auth_handler_block(leerie) -> str:
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(leerie.main))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            if getattr(node.type, "id", None) == "TerminalAuthFailure":
                return ast.unparse(node)
    raise AssertionError("no except TerminalAuthFailure handler found")


def test_handler_sets_exit_code_locked(leerie):
    block = _terminal_auth_handler_block(leerie)
    assert "exit_code = EXIT_LOCKED" in block
    assert "exit_code = 1" not in block


def test_handler_calls_cleanup_on_abnormal_exit_worktree_only(leerie):
    """Worktree-only cleanup (full_purge=False) — state and branches must
    survive so resume can pick the run back up."""
    block = _terminal_auth_handler_block(leerie)
    assert "_cleanup_on_abnormal_exit(st, full_purge=False)" in block


def test_handler_sets_abnormal_false(leerie):
    """abnormal=False marks this as a resumable pause, not a hard failure —
    downstream launcher/finalize logic branches on this flag."""
    block = _terminal_auth_handler_block(leerie)
    assert "abnormal = False" in block


def test_handler_gives_a_resume_hint(leerie):
    block = _terminal_auth_handler_block(leerie)
    assert "resume" in block


# ---------------------------------------------------------------------------
# control: 401/429/529 exhaustion still raises WorkerError, not
# TerminalAuthFailure — proves the reroute did not swallow the transient
# case (docs/IMPLEMENTATION.md §3 "Auth/quota backoff")
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [401, 429, 529])
def test_gateway_status_exhaustion_still_raises_worker_error(
        leerie, monkeypatch, tmp_path, status):
    """401/429/529 must still hit the auth/quota tenacity loop and, on
    budget exhaustion, raise WorkerError — not TerminalAuthFailure. The
    terminal-auth short-circuit must never claim this transient case."""
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
            sid="fit-judge-gateway-status",
        )

    with pytest.raises(leerie.WorkerError) as excinfo:
        asyncio.run(run())
    assert not isinstance(excinfo.value, leerie.TerminalAuthFailure)
    # The loop must have actually retried (more than the single initial
    # _spawn) before exhausting — proving it entered tenacity's loop
    # rather than short-circuiting through the terminal-auth path.
    assert len(invoke_calls) > 1


# ---------------------------------------------------------------------------
# source-coupling: terminal classifier consulted before the transient one
# ---------------------------------------------------------------------------

def test_terminal_auth_checked_before_auth_or_quota_in_claude_p(leerie):
    import inspect
    src = inspect.getsource(leerie.claude_p)
    terminal_idx = src.index("_is_terminal_auth_failure(envelope)")
    # The auth/quota + transport-drop backoff branch is now guarded by the
    # combined `_needs_backoff(envelope)` predicate (which ORs
    # `_is_auth_or_quota_failure` and `_is_transient_transport_failure`).
    # Terminal auth must still be checked before it — an expired session must
    # never be routed into the backoff loop.
    backoff_idx = src.index("_needs_backoff(envelope)")
    assert terminal_idx < backoff_idx


# ---------------------------------------------------------------------------
# main() handler: ACTUAL EXECUTION, not just source-coupled assertions.
#
# Every test above this point (and every one in test_terminal_auth_failure.py)
# reads `main()`'s TerminalAuthFailure handler via `ast.unparse` — none of them
# ever run its statements, so the ~18-line handler body (the assignments, the
# nested best-effort cleanup/capture try/excepts, the log calls) shipped with
# zero execution coverage despite being fully pinned structurally. That is
# exactly the gap `tests/test_integration_judge_coverage.py`'s
# `TestGateExecutes` class documents for a different handler: "Source-coupling
# cannot see a runtime error; only running the code can." This lifts the
# handler's statement list out of `main()` by AST (mirroring that file's
# `_extract` staticmethod) and executes it directly against a stubbed
# namespace, rather than driving all of `main()` (argparse, phase dispatch,
# real process exit) end-to-end — no other test in this suite does that,
# for the same reason `main()` is 1300+ lines of orchestration.
# ---------------------------------------------------------------------------

def _extract_terminal_auth_handler_node(leerie):
    import ast
    import inspect
    import textwrap
    src = textwrap.dedent(inspect.getsource(leerie.main))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is not None:
            if getattr(node.type, "id", None) == "TerminalAuthFailure":
                return node
    raise AssertionError("no except TerminalAuthFailure handler found")


def _compile_terminal_auth_handler_body(leerie):
    """Builds an executable module from the handler's real statement list,
    with line numbers shifted to match `orchestrator/leerie.py`'s actual
    lines and compiled against that real filename — so executing it is
    recorded by coverage.py as covering those exact source lines, not merely
    behaviorally equivalent lines in a throwaway `<string>` compile unit."""
    import ast
    import inspect

    node = _extract_terminal_auth_handler_node(leerie)
    _, start_line = inspect.getsourcelines(leerie.main)
    mod = ast.Module(body=node.body, type_ignores=[])
    ast.fix_missing_locations(mod)
    ast.increment_lineno(mod, start_line - 1)
    return compile(mod, leerie.__file__, "exec")


def _run_terminal_auth_handler(leerie, raw_message="OAuth session expired "
                                "and could not be refreshed"):
    """Executes the real handler body (extracted by AST) against a stubbed
    namespace and returns (ns, logs, save_calls, cleanup_calls, capture_calls).
    """
    code = _compile_terminal_auth_handler_body(leerie)

    logs: list[str] = []
    save_calls: list[str] = []
    cleanup_calls: list[bool] = []
    capture_calls: list[tuple] = []

    def fake_save(st, reason):
        save_calls.append(reason)

    def fake_cleanup(st, full_purge=False):
        cleanup_calls.append(full_purge)

    async def fake_capture_repo_deps(repo_root, st, caps, models, efforts):
        capture_calls.append((repo_root, caps, models, efforts))

    class _FakeSt:
        run_id = "r-terminal-auth"

    ns = {
        "e": leerie.TerminalAuthFailure(raw_message),
        "st": _FakeSt(),
        "log": logs.append,
        "_save_state_best_effort": fake_save,
        "_cleanup_on_abnormal_exit": fake_cleanup,
        "capture_repo_deps": fake_capture_repo_deps,
        "asyncio": asyncio,
        "repo_root": "/repo",
        "caps": {"c": 1},
        "models": {"m": 1},
        "efforts": {"e": 1},
        "EXIT_LOCKED": leerie.EXIT_LOCKED,
        "TerminalAuthFailure": leerie.TerminalAuthFailure,
        "RateLimitedExit": leerie.RateLimitedExit,
        "ContextOverflow": leerie.ContextOverflow,
        "DiskLowSpace": leerie.DiskLowSpace,
    }
    exec(code, ns)
    return ns, logs, save_calls, cleanup_calls, capture_calls


def test_extracted_handler_is_found_and_nonempty(leerie):
    """Anti-vacuity: the extraction must actually find the handler and it
    must carry statements, or the execution test below would silently pass
    against nothing."""
    import ast
    node = _extract_terminal_auth_handler_node(leerie)
    assert isinstance(node, ast.ExceptHandler)
    assert len(node.body) > 5


def test_handler_execution_sets_locals_and_calls_cleanup_and_capture(leerie):
    """Runs the real handler statements end to end: full_purge/abnormal/
    exit_code are set correctly, `_save_state_best_effort` and
    `_cleanup_on_abnormal_exit(st, full_purge=False)` are actually invoked
    (not merely present in the source text), the best-effort `capture_repo_deps`
    call receives the real repo_root/caps/models/efforts, and a resume hint is
    logged."""
    ns, logs, save_calls, cleanup_calls, capture_calls = (
        _run_terminal_auth_handler(leerie))

    assert ns["full_purge"] is False
    assert ns["abnormal"] is False
    assert ns["exit_code"] == leerie.EXIT_LOCKED

    assert save_calls == ["TerminalAuthFailure exit"]
    assert cleanup_calls == [False], (
        "the worktree-only cleanup (full_purge=False) must actually run — "
        "state and the run branch must survive so resume can pick it up")
    assert capture_calls == [("/repo", {"c": 1}, {"m": 1}, {"e": 1})]

    joined = "\n".join(logs)
    assert "auth session locked" in joined
    assert "claude /login" in joined
    assert "leerie resume r-terminal-auth" in joined


def test_handler_execution_survives_a_cleanup_that_raises(leerie):
    """The nested `except BaseException as cleanup_err` around
    `_cleanup_on_abnormal_exit` must swallow a cleanup failure (logged,
    non-fatal) rather than letting it escape and skip the exit_code
    assignment below it — mirrors `_save_state_best_effort`'s own
    non-fatal discipline for the sibling save call."""
    code = _compile_terminal_auth_handler_body(leerie)

    logs: list[str] = []

    def fake_save(st, reason):
        pass

    def raising_cleanup(st, full_purge=False):
        raise RuntimeError("cleanup blew up")

    async def fake_capture_repo_deps(repo_root, st, caps, models, efforts):
        pass

    class _FakeSt:
        run_id = "r-cleanup-raises"

    ns = {
        "e": leerie.TerminalAuthFailure("session expired and could not be "
                                         "refreshed"),
        "st": _FakeSt(),
        "log": logs.append,
        "_save_state_best_effort": fake_save,
        "_cleanup_on_abnormal_exit": raising_cleanup,
        "capture_repo_deps": fake_capture_repo_deps,
        "asyncio": asyncio,
        "repo_root": "/repo",
        "caps": {},
        "models": {},
        "efforts": {},
        "EXIT_LOCKED": leerie.EXIT_LOCKED,
        "TerminalAuthFailure": leerie.TerminalAuthFailure,
        "RateLimitedExit": leerie.RateLimitedExit,
        "ContextOverflow": leerie.ContextOverflow,
        "DiskLowSpace": leerie.DiskLowSpace,
    }
    exec(code, ns)

    # Reached the end of the handler despite the cleanup raising — proves
    # the exception was caught, not propagated.
    assert ns["exit_code"] == leerie.EXIT_LOCKED
    assert any("cleanup failed" in m and "cleanup blew up" in m
               for m in logs)


def test_handler_execution_survives_a_capture_repo_deps_that_raises(leerie):
    """`capture_repo_deps` is guaranteed to re-hit the same terminal auth
    failure (it calls claude_p again). The handler's own broad
    `except (Exception, KeyboardInterrupt, TerminalAuthFailure,
    RateLimitedExit, ContextOverflow, DiskLowSpace)` around it must catch
    a re-raised TerminalAuthFailure specifically — a bare `except Exception`
    would miss it, since TerminalAuthFailure subclasses BaseException — and
    still reach the final `exit_code = EXIT_LOCKED` assignment."""
    code = _compile_terminal_auth_handler_body(leerie)

    logs: list[str] = []

    def fake_save(st, reason):
        pass

    def fake_cleanup(st, full_purge=False):
        pass

    async def raising_capture_repo_deps(repo_root, st, caps, models, efforts):
        raise leerie.TerminalAuthFailure("session expired and could not be "
                                          "refreshed")

    class _FakeSt:
        run_id = "r-capture-reraises"

    ns = {
        "e": leerie.TerminalAuthFailure("session expired and could not be "
                                         "refreshed"),
        "st": _FakeSt(),
        "log": logs.append,
        "_save_state_best_effort": fake_save,
        "_cleanup_on_abnormal_exit": fake_cleanup,
        "capture_repo_deps": raising_capture_repo_deps,
        "asyncio": asyncio,
        "repo_root": "/repo",
        "caps": {},
        "models": {},
        "efforts": {},
        "EXIT_LOCKED": leerie.EXIT_LOCKED,
        "TerminalAuthFailure": leerie.TerminalAuthFailure,
        "RateLimitedExit": leerie.RateLimitedExit,
        "ContextOverflow": leerie.ContextOverflow,
        "DiskLowSpace": leerie.DiskLowSpace,
    }
    exec(code, ns)

    assert ns["exit_code"] == leerie.EXIT_LOCKED
    assert any("capture: non-fatal error during auth-locked pause" in m
               for m in logs)
