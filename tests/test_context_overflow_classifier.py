"""`Prompt is too long` is terminal, not a schema failure (S2).

Claude Code enforces a context ceiling client-side: it emits a synthetic
assistant message (`model=<synthetic>`, usage all zeros) and ends the session
WITHOUT issuing an API call. Retrying identical input therefore cannot succeed.

Before `_is_context_overflow` existed the envelope fell through to `claude_p`'s
generic 2-attempt loop and surfaced as `worker failed schema-valid output twice:
Prompt is too long` — blaming schema validation for a context refusal. That
mislabelling is what made the 2026-08-06 incident unreadable: three separate
diagnoses were proposed and falsified before the cause was measured.

Envelope fixtures below are verbatim `result` events from that probe.
"""
import inspect
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402


# Measured: arm C, leerie's real strict proxy, plain `sonnet`.
OVERFLOW = {
    "type": "result",
    "subtype": "success",          # misleading -- must NOT be keyed on
    "is_error": True,
    "terminal_reason": "blocking_limit",
    "api_error_status": None,
    "result": "Prompt is too long",
    "num_turns": 11,
}

# Measured: arm A, direct. Same is_error, different terminal_reason.
MAX_TURNS = {
    "type": "result", "subtype": "success", "is_error": True,
    "terminal_reason": "max_turns", "api_error_status": None,
    "result": None, "num_turns": 7,
}

# Measured: arm E, strict proxy + sonnet[1m] -- the healthy outcome.
COMPLETED = {
    "type": "result", "subtype": "success", "is_error": False,
    "terminal_reason": "completed", "api_error_status": None,
    "result": '{"domain":"documentation","status":"ready"}', "num_turns": 21,
}


class TestClassifier:
    def test_measured_overflow_envelope_matches(self):
        assert leerie._is_context_overflow(OVERFLOW) is True

    def test_max_turns_does_not_match(self):
        # `blocking_limit` is the discriminator against this: both are
        # is_error=True, and keying on is_error alone would swallow every
        # max-turns exhaustion as a context overflow.
        assert leerie._is_context_overflow(MAX_TURNS) is False

    def test_successful_envelope_never_matches(self):
        assert leerie._is_context_overflow(COMPLETED) is False

    def test_success_envelope_discussing_the_phrase_does_not_match(self):
        # A worker may legitimately write about context limits in its own
        # correct output. Gating on is_error is what keeps that inert.
        env = dict(COMPLETED, result="the planner prompt is too long, so split it")
        assert leerie._is_context_overflow(env) is False

    def test_terminal_reason_alone_is_insufficient(self):
        env = dict(OVERFLOW, result="some other blocking limit")
        assert leerie._is_context_overflow(env) is False

    def test_text_alone_is_insufficient(self):
        env = dict(OVERFLOW, terminal_reason="api_error")
        assert leerie._is_context_overflow(env) is False

    def test_synthetic_envelope_is_exempt(self):
        # `_leerie_synthetic` interpolates the worker's raw stderr into
        # `result`, so it can carry the phrase without the CLI having refused
        # anything. Same exemption `_is_terminal_auth_failure` carries, and for
        # the same measured reason.
        env = dict(OVERFLOW, _leerie_synthetic="no_result_event")
        assert leerie._is_context_overflow(env) is False

    def test_case_insensitive(self):
        assert leerie._is_context_overflow(dict(OVERFLOW, result="PROMPT IS TOO LONG"))

    def test_missing_and_non_string_fields_are_safe(self):
        assert leerie._is_context_overflow({}) is False
        assert leerie._is_context_overflow({"is_error": True}) is False
        assert leerie._is_context_overflow(dict(OVERFLOW, result=None)) is False
        assert leerie._is_context_overflow(dict(OVERFLOW, result=12345)) is False


class TestDisjointFromTheOtherClassifiers:
    """Overflow must not be absorbed by the auth/quota or transport paths."""

    def test_overflow_is_not_an_auth_failure(self):
        assert leerie._is_terminal_auth_failure(OVERFLOW) is False
        assert leerie._is_auth_or_quota_failure(OVERFLOW) is False

    def test_auth_failure_is_not_an_overflow(self):
        auth = {"is_error": True, "terminal_reason": "api_error",
                "result": "Failed to authenticate: OAuth session expired"}
        assert leerie._is_context_overflow(auth) is False


class TestExceptionContract:
    def test_inherits_baseexception_not_workererror(self):
        # Must survive asyncio.gather and broad `except Exception`. Critically
        # it must NOT be a WorkerError: `_run_checked_loop` deliberately
        # RETRIES WorkerError across its whole round budget, which for a
        # deterministic refusal is pure waste.
        assert issubclass(leerie.ContextOverflow, BaseException)
        assert not issubclass(leerie.ContextOverflow, Exception)
        assert not issubclass(leerie.ContextOverflow, leerie.WorkerError)

    def test_carries_raw_message(self):
        assert leerie.ContextOverflow("Prompt is too long").raw_message == \
            "Prompt is too long"


class TestWiring:
    """Classifier and handler are both inert unless actually called."""

    def test_claude_p_raises_before_the_retry_loop(self):
        src = inspect.getsource(leerie.claude_p)
        assert "_is_context_overflow(envelope)" in src
        assert "raise ContextOverflow(" in src
        # Must precede the generic 2-attempt failure, or the retry happens
        # anyway and the operator still sees a schema-failure message.
        assert (src.index("_is_context_overflow")
                < src.index("worker failed schema-valid output twice"))

    def test_main_handles_it_as_a_resumable_pause(self):
        src = inspect.getsource(leerie.main)
        assert "except ContextOverflow as e:" in src
        # Split on the next TOP-LEVEL handler (4-space indent). A bare
        # "except " would truncate at the inner `except Exception:` guarding
        # the cleanup call, hiding the `exit_code` assignment that follows it.
        arm = src.split("except ContextOverflow as e:", 1)[1].split("\n    except ", 1)[0]
        assert "EXIT_LOCKED" in arm, "must pause resumably, not exit(1)"
        assert "resume" in arm, "must tell the operator how to continue"

    def test_message_does_not_blame_schema_validation(self):
        src = inspect.getsource(leerie.main)
        # Split on the next TOP-LEVEL handler (4-space indent). A bare
        # "except " would truncate at the inner `except Exception:` guarding
        # the cleanup call, hiding the `exit_code` assignment that follows it.
        arm = src.split("except ContextOverflow as e:", 1)[1].split("\n    except ", 1)[0]
        assert "schema" not in arm.lower()

    def test_arm_runs_a_guarded_dep_capture_like_its_siblings(self):
        """Every other terminating arm makes a best-effort `capture_repo_deps`
        call (DESIGN §6½); this one must too, and it must be guarded against
        the whole exit-signal family.

        `capture_repo_deps` invokes `claude_p` again, so an unguarded call can
        re-raise a BaseException that escapes `main()`, skips the `exit_code`
        assignment below it, and crashes the run with exit 1 instead of pausing
        resumably — the verbatim 2026-07-19 incident that
        `tests/test_capture_swallows_exit_signals.py` exists to prevent.
        """
        src = inspect.getsource(leerie.main)
        arm = src.split("except ContextOverflow as e:", 1)[1] \
                 .split("\n    except ", 1)[0]
        assert "capture_repo_deps(" in arm, (
            "the ContextOverflow arm skips the best-effort dep capture its "
            "sibling terminating arms all perform")
        # Membership, not literal tuple text: the order of these names is not
        # the property under test, and pinning the exact prefix made this fail
        # when KeyboardInterrupt was added to every terminal arm's guard.
        assert "except (" in arm
        guard = arm.split("except (", 1)[1].split(") as", 1)[0]
        for required in ("Exception", "ContextOverflow", "KeyboardInterrupt"):
            assert required in guard, (
                f"the arm's own capture guard must catch {required} — "
                "capture_repo_deps calls claude_p, which can raise it again, "
                f"and a Ctrl-C during that capture escapes main(). guard: {guard}")
        # The capture must precede the exit_code assignment; an escape past an
        # unguarded call skips it. The ordering *is* the property under test.
        assert arm.index("capture_repo_deps(") < arm.index("exit_code =")
