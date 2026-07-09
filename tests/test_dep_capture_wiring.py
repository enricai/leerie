"""Source-coupling pins for the dep_capture wiring seams.

Pins three orchestrator seams that are only verifiable by source inspection:
  1. main()'s KeyboardInterrupt arm invokes capture_repo_deps inside its own
     asyncio.run() wrapped in a non-fatal try/except.
  2. main()'s InterruptedBySignal arm does the same (same asyncio.run pattern).
  3. _run_phases() calls _backstop_capture_prior_runs before phase_classify at
     run-start (covers SIGKILL / crash where no cancel-arm window existed).
  4. dep_capture is enumerated in the §12 prompts-advisory table — its prompt
     file exists and IMPLEMENTATION.md's worker-invocation table names it.

Mirrors test_phase_finalize_capture_hook.py's inspect.getsource approach:
behavioral driving of main() through a real signal is not something the suite
does; source-coupling is the correct tier for these wiring seams.
"""
from __future__ import annotations

import inspect
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------------------
# 1 + 2. Signal-arm wiring: asyncio.run(capture_repo_deps) + try/except
# ---------------------------------------------------------------------------

class TestKeyboardInterruptArm:
    """main()'s KeyboardInterrupt arm must call capture_repo_deps inside its
    own asyncio.run() and wrap the call in a non-fatal try/except Exception.

    The cancel-arm capture is inert without this wiring (DESIGN §6½).
    Mirrors the existing TestCancelArmWiring check but pins the asyncio.run
    and try/except structure that makes the call non-fatal and self-contained."""

    def _arm_src(self, leerie) -> str:
        """Extract source slice from 'except KeyboardInterrupt:' to exit_code = 130."""
        src = inspect.getsource(leerie.main)
        ki_idx = src.find("except KeyboardInterrupt:")
        assert ki_idx != -1, "main() must have a KeyboardInterrupt handler"
        exit_130_idx = src.find("exit_code = 130", ki_idx)
        assert exit_130_idx != -1, (
            "KeyboardInterrupt arm must assign exit_code = 130")
        return src[ki_idx:exit_130_idx]

    def test_keyboard_interrupt_arm_uses_asyncio_run(self, leerie):
        """The call must be wrapped in asyncio.run() — the arm runs after the
        event loop has exited, so a bare await would fail at runtime."""
        arm = self._arm_src(leerie)
        assert "asyncio.run(" in arm, (
            "KeyboardInterrupt arm must invoke capture_repo_deps via asyncio.run(); "
            "the event loop has already exited at this point so a bare await fails."
        )

    def test_keyboard_interrupt_arm_calls_capture_inside_asyncio_run(self, leerie):
        """asyncio.run must contain capture_repo_deps (not some other coroutine)."""
        arm = self._arm_src(leerie)
        asyncio_run_idx = arm.find("asyncio.run(")
        assert asyncio_run_idx != -1
        # capture_repo_deps must appear on or after the asyncio.run( call.
        capture_idx = arm.find("capture_repo_deps(", asyncio_run_idx)
        assert capture_idx != -1, (
            "asyncio.run() in the KeyboardInterrupt arm must call capture_repo_deps"
        )

    def test_keyboard_interrupt_arm_has_nonfatal_try_except(self, leerie):
        """The asyncio.run(capture_repo_deps) call must be inside a
        try/except Exception block so a capture failure never blocks the exit."""
        arm = self._arm_src(leerie)
        try_idx = arm.find("try:")
        assert try_idx != -1, (
            "KeyboardInterrupt arm must contain a try: block around "
            "asyncio.run(capture_repo_deps()) to keep capture non-fatal"
        )
        capture_idx = arm.find("capture_repo_deps(", try_idx)
        assert capture_idx != -1, (
            "capture_repo_deps must appear after the try: in the "
            "KeyboardInterrupt arm"
        )
        except_idx = arm.find("except Exception", capture_idx)
        assert except_idx != -1, (
            "KeyboardInterrupt arm must have 'except Exception' after "
            "capture_repo_deps() to keep capture errors non-fatal"
        )


class TestInterruptedBySignalArm:
    """main()'s InterruptedBySignal arm (SIGTERM/SIGHUP) must call
    capture_repo_deps inside its own asyncio.run() wrapped in try/except.

    Same non-fatal contract as the KeyboardInterrupt arm; same wiring pin."""

    def _arm_src(self, leerie) -> str:
        """Extract source slice from 'except InterruptedBySignal' to
        'signum = getattr(signal'."""
        src = inspect.getsource(leerie.main)
        ibs_idx = src.find("except InterruptedBySignal")
        assert ibs_idx != -1, "main() must have an InterruptedBySignal handler"
        # The signum resolution line follows the capture block.
        signum_idx = src.find("signum = getattr(signal", ibs_idx)
        assert signum_idx != -1, (
            "InterruptedBySignal arm must resolve signum (after capture)")
        return src[ibs_idx:signum_idx]

    def test_interrupted_by_signal_arm_uses_asyncio_run(self, leerie):
        """Must use asyncio.run() — the event loop is gone at this point."""
        arm = self._arm_src(leerie)
        assert "asyncio.run(" in arm, (
            "InterruptedBySignal arm must invoke capture_repo_deps via asyncio.run()"
        )

    def test_interrupted_by_signal_arm_calls_capture_inside_asyncio_run(self, leerie):
        """asyncio.run must contain capture_repo_deps."""
        arm = self._arm_src(leerie)
        asyncio_run_idx = arm.find("asyncio.run(")
        assert asyncio_run_idx != -1
        capture_idx = arm.find("capture_repo_deps(", asyncio_run_idx)
        assert capture_idx != -1, (
            "asyncio.run() in the InterruptedBySignal arm must call capture_repo_deps"
        )

    def test_interrupted_by_signal_arm_has_nonfatal_try_except(self, leerie):
        """Non-fatal try/except Exception must wrap the capture call."""
        arm = self._arm_src(leerie)
        try_idx = arm.find("try:")
        assert try_idx != -1, (
            "InterruptedBySignal arm must contain a try: block around "
            "asyncio.run(capture_repo_deps())"
        )
        capture_idx = arm.find("capture_repo_deps(", try_idx)
        assert capture_idx != -1, (
            "capture_repo_deps must appear after try: in the "
            "InterruptedBySignal arm"
        )
        except_idx = arm.find("except Exception", capture_idx)
        assert except_idx != -1, (
            "InterruptedBySignal arm must have 'except Exception' after "
            "capture_repo_deps() so capture errors are non-fatal"
        )


# ---------------------------------------------------------------------------
# 3. Run-start backstop: _backstop_capture_prior_runs before phase_classify
# ---------------------------------------------------------------------------

class TestRunStartBackstop:
    """_run_phases must call _backstop_capture_prior_runs before phase_classify.

    This is the SIGKILL / crash recovery path (DESIGN §6½ *Run-start backstop*):
    a run that was killed without a Python window still gets its dep_capture run
    on the NEXT leerie invocation, automatically, before any new classify work."""

    def test_backstop_called_in_run_phases(self, leerie):
        """_run_phases must call _backstop_capture_prior_runs."""
        src = inspect.getsource(leerie._run_phases)
        assert "_backstop_capture_prior_runs(" in src, (
            "_run_phases must call _backstop_capture_prior_runs() "
            "(DESIGN §6½ run-start backstop — covers SIGKILL / crash)"
        )

    def test_backstop_called_before_phase_classify(self, leerie):
        """_backstop_capture_prior_runs must appear before phase_classify in
        _run_phases so prior-run capture completes before new classify work."""
        src = inspect.getsource(leerie._run_phases)
        backstop_idx = src.find("_backstop_capture_prior_runs(")
        assert backstop_idx != -1, (
            "_run_phases must contain _backstop_capture_prior_runs()")
        classify_idx = src.find("phase_classify(", backstop_idx)
        assert classify_idx != -1, (
            "phase_classify must appear after _backstop_capture_prior_runs "
            "in _run_phases — the backstop must run before any new classify work"
        )

    def test_backstop_is_awaited(self, leerie):
        """The backstop call must be awaited (it is async)."""
        src = inspect.getsource(leerie._run_phases)
        backstop_idx = src.find("_backstop_capture_prior_runs(")
        assert backstop_idx != -1
        # Look for 'await' in the few characters before the call.
        prefix = src[max(0, backstop_idx - 20):backstop_idx]
        assert "await" in prefix, (
            "_backstop_capture_prior_runs must be awaited in _run_phases"
        )


# ---------------------------------------------------------------------------
# 4. §12 advisory enumeration: dep_capture prompt + IMPLEMENTATION.md listing
# ---------------------------------------------------------------------------

class TestDepCaptureSection12Enumeration:
    """dep_capture must be enumerated in the §12 advisory table.

    §12 principle: prompts are advisory, code enforces. The dep_capture worker
    follows this — its prompt asks for a dependency list, the schema validates
    the output, and the code writes the result deterministically. The prompt
    file must exist (confirming the advisory half is in place) and IMPLEMENTATION.md
    must name it among the post-run / finalize workers (confirming the §12
    boundary is documented)."""

    def test_dep_capture_prompt_file_exists(self):
        """prompts/dep_capture.md must exist — the advisory half of the §12
        prompts-advisory-code-enforces split for the dep_capture worker."""
        prompt = REPO_ROOT / "prompts" / "dep_capture.md"
        assert prompt.is_file(), (
            "prompts/dep_capture.md is missing; "
            "dep_capture worker has no system prompt (§12 advisory half absent)"
        )

    def test_dep_capture_named_in_implementation_md(self):
        """IMPLEMENTATION.md must name dep_capture in the worker enumeration
        so the §12 boundary for the capture worker is documented alongside
        the other post-run / finalize workers."""
        impl_md = REPO_ROOT / "docs" / "IMPLEMENTATION.md"
        assert impl_md.is_file(), "docs/IMPLEMENTATION.md not found"
        content = impl_md.read_text()
        assert "dep_capture" in content, (
            "dep_capture must appear in docs/IMPLEMENTATION.md "
            "(the §12 catalogue); the capture worker is undocumented"
        )

    def test_dep_capture_schema_key_in_leerie_py(self, leerie):
        """SCHEMAS['dep_capture'] is the code-enforces half of §12 for the
        capture worker — its structured output is validated before any write."""
        assert "dep_capture" in leerie.SCHEMAS, (
            "SCHEMAS['dep_capture'] is missing; "
            "dep_capture output is not schema-validated (§12 code-enforces half absent)"
        )
