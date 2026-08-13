"""N30: disk headroom preflight + periodic mid-run check (DESIGN's N30 finding).

Two call sites reuse one helper, `_disk_free_ratio`, against
`DISK_MIN_FREE_RATIO`:

  - `preflight()` — near-zero free space die()s before any worker spawns.
  - `phase_execute`'s wave loop — a mid-run drop raises `DiskLowSpace`
    (a BaseException, same shape as ContextOverflow/RateLimitedExit) so
    main() can pause resumably via EXIT_LOCKED instead of crashing on an
    unhandled OSError from underneath the next write.

A healthy disk is a no-op at both checkpoints.
"""
import asyncio
import shutil
import sys
import textwrap
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "orchestrator"))
import leerie  # noqa: E402


def _fake_usage(free_ratio: float, total: int = 100 * 1024 ** 3):
    free = int(total * free_ratio)
    used = total - free
    return shutil._ntuple_diskusage(total=total, used=used, free=free)


class TestDiskFreeRatio:
    def test_healthy_disk_ratio(self, tmp_path):
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.50)):
            assert leerie._disk_free_ratio(tmp_path) == pytest.approx(0.50)

    def test_low_disk_ratio(self, tmp_path):
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.01)):
            assert leerie._disk_free_ratio(tmp_path) == pytest.approx(0.01)

    def test_walks_up_to_nearest_existing_ancestor(self, tmp_path):
        missing = tmp_path / "does" / "not" / "exist"
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.50)) as m:
            leerie._disk_free_ratio(missing)
        # Called against tmp_path (the nearest existing ancestor), not the
        # missing leaf path — shutil.disk_usage raises FileNotFoundError on
        # a nonexistent path, so the walk-up is load-bearing.
        m.assert_called_once_with(tmp_path)


class TestDiskHeadroomMessage:
    def test_message_names_ratio_and_path(self, tmp_path):
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.02)):
            msg = leerie._disk_headroom_message(tmp_path, 0.02)
        assert "2.0%" in msg
        assert str(tmp_path) in msg
        assert f"{leerie.DISK_MIN_FREE_RATIO:.0%}" in msg


class TestPreflightDiskCheck:
    """Near-zero free space dies before any worker spawns; a healthy disk
    is a no-op and preflight proceeds to its next check."""

    def test_low_disk_dies_before_git_checks(self, tmp_path, monkeypatch):
        # die() calls sys.exit — assert it fires by catching SystemExit,
        # and that no subprocess (git config, smoke test) ran first.
        called = []

        async def _fake_run_proc(*a, **k):
            called.append(a)
            raise AssertionError("run_proc must not run when disk is low")

        monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)
        monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.01)):
            with pytest.raises(SystemExit):
                asyncio.run(leerie.preflight(tmp_path, skip_smoke=True))
        assert not called

    def test_healthy_disk_is_a_noop_at_preflight(self, tmp_path, monkeypatch):
        # A healthy disk ratio must not die() on the disk check itself.
        # Stub every downstream check to a clean pass so preflight runs to
        # completion — proving control flow reached and passed every check
        # after the disk gate, rather than merely dying somewhere later for
        # an unrelated reason (which would tell us nothing about the disk
        # check specifically).
        async def _fake_run_proc(argv, *a, **k):
            if argv[:2] == ["git", "config"]:
                return mock.Mock(returncode=0, stdout="ok\n")
            if argv[:2] == ["git", "status"]:
                return mock.Mock(returncode=0, stdout="")
            if argv[:2] == ["git", "show-ref"]:
                return mock.Mock(returncode=1, stdout="")
            raise AssertionError(f"unexpected run_proc call: {argv}")

        monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
        monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)
        monkeypatch.setattr(leerie, "_check_claude_cli_version", lambda: None)
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.90)):
            # skip_smoke=True skips the live claude -p call; no SystemExit
            # anywhere means every check, including the disk gate, passed.
            asyncio.run(leerie.preflight(tmp_path, skip_smoke=True))


class TestPhaseExecuteDiskLowSpace:
    """The mid-run headroom check (once per wave, before wave-entry work
    begins) raises DiskLowSpace rather than letting a subsequent write
    crash with an unhandled OSError."""

    def test_low_disk_raises_disklowspace_before_wave_work(self, tmp_path):
        st = mock.Mock()
        st.data = {"waves": [["feat-001"]], "completed_waves": 0,
                   "subtask_status": {}, "integration_gate": {}}
        st.save = mock.Mock()

        caps = {"max_parallel": 5}

        def _fail_if_called(*a, **k):
            raise AssertionError("wave work must not start when disk is low")

        with mock.patch.object(leerie, "_run_script",
                                new=mock.AsyncMock(
                                    return_value=mock.Mock(returncode=0, stderr=""))), \
             mock.patch.object(leerie, "run_proc",
                                new=mock.AsyncMock(return_value=mock.Mock(returncode=0))), \
             mock.patch.object(leerie, "_capture_conformance_baseline",
                                new=mock.AsyncMock()), \
             mock.patch.object(leerie, "_degrade_max_parallel_for_wave",
                                side_effect=_fail_if_called), \
             mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.01)):
            st.data["skip_base_baseline"] = True
            with pytest.raises(leerie.DiskLowSpace) as exc_info:
                asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))
        assert "1.0%" in exc_info.value.raw_message

    def test_healthy_disk_does_not_raise_disklowspace(self, tmp_path):
        st = mock.Mock()
        st.data = {"waves": [["feat-001"]], "completed_waves": 0,
                   "subtask_status": {"feat-001": "complete"},
                   "integration_gate": {}, "skip_base_baseline": True}
        st.save = mock.Mock()
        caps = {"max_parallel": 5}

        with mock.patch.object(leerie, "_run_script",
                                new=mock.AsyncMock(
                                    return_value=mock.Mock(returncode=0, stderr=""))), \
             mock.patch.object(leerie, "run_proc",
                                new=mock.AsyncMock(return_value=mock.Mock(returncode=0))), \
             mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.90)):
            # feat-001 is already complete for wave 0, so the loop takes the
            # "all subtasks already complete" shortcut immediately after the
            # disk check and returns cleanly instead of raising.
            asyncio.run(leerie.phase_execute(tmp_path, st, caps, {}, {}))
        assert st.data["completed_waves"] == 1


class TestDiskLowSpaceExceptionClass:
    def test_is_a_base_exception_not_worker_error(self):
        assert issubclass(leerie.DiskLowSpace, BaseException)
        assert not issubclass(leerie.DiskLowSpace, leerie.WorkerError)

    def test_carries_raw_message(self):
        exc = leerie.DiskLowSpace("only 1.0% free")
        assert exc.raw_message == "only 1.0% free"


class TestMainHandlesDiskLowSpace:
    """main()'s `except DiskLowSpace as e:` arm must pause resumably
    (EXIT_LOCKED, a `resume` hint, and a `st.save()` so the run stays
    recoverable) rather than letting the mid-run raise crash the process.

    Source-coupling only, mirroring
    `test_context_overflow_classifier.py::TestWiring` — the suite does not
    drive `main()` to a real process exit, so this pins the handler's
    control flow directly."""

    def _arm(self) -> str:
        import inspect
        src = inspect.getsource(leerie.main)
        assert "except DiskLowSpace as e:" in src
        # Split on the next TOP-LEVEL handler (4-space indent). A bare
        # "except " would truncate at the inner `except Exception:` guarding
        # the cleanup call, hiding the `exit_code` assignment after it —
        # the same trap test_context_overflow_classifier.py documents.
        return src.split("except DiskLowSpace as e:", 1)[1] \
                  .split("\n    except ", 1)[0]

    def test_pauses_with_exit_locked_not_a_crash(self):
        arm = self._arm()
        assert "EXIT_LOCKED" in arm, "must pause resumably, not exit(1)/raise"
        assert "resume" in arm, "must tell the operator how to continue"

    def test_persists_state_before_pausing(self):
        # The run is only resumable if state.json reflects the pause —
        # an in-memory-only pause would resume from stale disk state.
        # Via the best-effort helper, never a bare st.save(): see
        # test_survives_a_save_that_is_still_failing.
        arm = self._arm()
        assert "_save_state_best_effort(" in arm

    def test_survives_a_save_that_is_still_failing(self):
        """The save-origin path must SURVIVE this arm, not merely reach it.

        This replaces a test that asserted only
        `issubclass(DiskLowSpace, BaseException)` — already asserted at the
        top of this file — and concluded from it that "no separate
        save()-specific handler is required". That inference is what let the
        defect ship: reaching the arm was never in doubt; surviving it was.

        `State.save()` converts ENOSPC into DiskLowSpace, so on that path the
        disk is at zero and the arm's own `st.save()` re-enters the call that
        just failed, raising DiskLowSpace a SECOND time from inside the
        handler. A sibling `except` of the same `try` does not see an
        exception raised in another arm's body, so it escapes `main()`,
        skipping the cleanup, the dep capture and the EXIT_LOCKED assignment
        — an exit-1 traceback instead of a resumable pause.

        Two properties, both checkable in the source: the arm never calls
        `st.save()` directly — every terminating arm now routes through
        `_save_state_best_effort`, which cannot raise — and `exit_code` is
        assigned before it, so nothing below can cost the run its
        disposition. The helper is deliberately broader than an
        `except DiskLowSpace` around the save: a read-only run dir raises
        `PermissionError` (measured), which no conversion touches and which
        a narrower guard let escape.
        """
        import ast
        import textwrap
        tree = ast.parse(textwrap.dedent(self._arm()))

        bare = [n for n in ast.walk(tree)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and n.func.attr == "save"
                and ast.unparse(n) == "st.save()"]
        assert not bare, (
            f"{len(bare)} bare st.save() call(s) in the DiskLowSpace arm. On "
            "an out-of-space or read-only filesystem such a call raises from "
            "inside the handler, escapes main(), and the run exits 1 instead "
            "of pausing resumably — use _save_state_best_effort")

        helper = [n for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                  and n.func.id == "_save_state_best_effort"]
        assert helper, "the arm no longer persists state at all"

        exit_assign = min(
            (n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Assign)
             and any(isinstance(t, ast.Name) and t.id == "exit_code"
                     for t in n.targets)),
            default=None)
        assert exit_assign is not None, "the arm never assigns exit_code"
        assert exit_assign < min(n.lineno for n in helper), (
            "`exit_code` is assigned after the save — if the save raises, "
            "the run loses its EXIT_LOCKED disposition")

    def test_does_not_re_raise(self):
        # The generic `except BaseException as e:` arm above re-raises
        # (crashing the process with a traceback) — DiskLowSpace's own arm
        # must be a distinct, earlier handler that does not fall through
        # to that behavior.
        import inspect
        src = inspect.getsource(leerie.main)
        assert src.index("except DiskLowSpace as e:") < \
            src.index("except BaseException as e:")
        arm = self._arm()
        # A bare "raise" (no exception name) re-raises the currently
        # handled exception; comments elsewhere in the arm legitimately
        # discuss re-raising ("re-raise escaping here"), so only a real
        # `raise` statement (start-of-line, ignoring indentation) counts.
        assert not any(line.strip() == "raise" for line in arm.splitlines())

    def test_runs_a_guarded_dep_capture_like_its_siblings(self):
        arm = self._arm()
        assert "capture_repo_deps(" in arm, (
            "the DiskLowSpace arm skips the best-effort dep capture its "
            "sibling terminating arms all perform")


class TestSaveBestEffortHelper:
    """`_save_state_best_effort` must never raise, and every terminating arm
    in `main()` must use it.

    The arm-local fix shipped in #203 was not enough: eight other handlers
    carried the same bare `st.save()`, and the catch-all
    `except BaseException` arm is the worst of them — a raise there REPLACES
    the unhandled exception, so the operator sees a save error while the real
    bug survives only as `__context__`, which nothing prints.
    """

    def _failing_state(self, exc: BaseException):
        st = mock.Mock()
        st.save = mock.Mock(side_effect=exc)
        return st

    def test_swallows_disklowspace(self, capsys):
        st = self._failing_state(leerie.DiskLowSpace("no space"))
        leerie._save_state_best_effort(st, "unit")   # must not raise
        assert "could not persist state" in (capsys.readouterr().out
                                             + capsys.readouterr().err)

    def test_swallows_a_permission_error(self):
        """Not every save failure is a disk failure — a read-only run dir
        raises PermissionError, which no conversion turns into DiskLowSpace,
        and which an `except DiskLowSpace` guard would have let escape."""
        st = self._failing_state(PermissionError(13, "Permission denied"))
        leerie._save_state_best_effort(st, "unit")

    def test_swallows_a_baseexception(self):
        """The catch-all arm must survive anything, including the
        BaseException family that an `except Exception` guard misses."""
        st = self._failing_state(KeyboardInterrupt())
        leerie._save_state_best_effort(st, "unit")

    def test_a_healthy_save_is_not_swallowed_silently(self, capsys):
        st = mock.Mock()
        st.save = mock.Mock()
        leerie._save_state_best_effort(st, "unit")
        st.save.assert_called_once()
        out = capsys.readouterr()
        assert "could not persist" not in (out.out + out.err), (
            "a successful save must not warn")

    def test_every_main_handler_uses_the_helper(self):
        """No terminating arm may reintroduce a bare `st.save()`.

        Source-coupled because `main()` cannot be driven to a real exit here.
        Counting rather than naming the arms: a NEW handler added later gets
        the same treatment without anyone remembering to extend a list.
        """
        import ast
        import inspect
        tree = ast.parse(textwrap.dedent(inspect.getsource(leerie.main)))
        offenders = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Try):
                continue
            for h in node.handlers:
                name = ast.unparse(h.type) if h.type else "bare except"
                for n in ast.walk(h):
                    if (isinstance(n, ast.Call)
                            and isinstance(n.func, ast.Attribute)
                            and n.func.attr == "save"
                            and ast.unparse(n) == "st.save()"):
                        offenders.append(f"{name} (line {n.lineno})")
        assert not offenders, (
            "these main() handlers still call st.save() directly; a raise "
            "from inside an except block escapes main() and skips the "
            "exit_code assignment:\n  " + "\n  ".join(offenders))

    def test_the_helper_is_actually_used(self):
        """Anti-vacuity for the sweep above: it passes trivially if nothing
        saves at all, so require the helper to be present in quantity."""
        import inspect
        src = inspect.getsource(leerie.main)
        assert src.count("_save_state_best_effort(") >= 7, (
            "main() has far fewer best-effort saves than it has terminating "
            "arms — the sweep above may be passing because the saves were "
            "removed rather than guarded")


class TestStateSaveCatchesENOSPC:
    """N30's mitigation is not exclusively the proactive `_disk_free_ratio`
    checks in `preflight()`/`phase_execute` (covered above) — `State.save()`
    itself also wraps its `tmp.write_text()`/`os.replace()` pair, since a
    disk can cross zero between one periodic check and the next write. An
    `OSError(errno.ENOSPC, ...)` from either half is reraised as
    `DiskLowSpace`, the same exception class/pause path the proactive
    checks use, rather than propagating as an unhandled `OSError`.

    This reproduces the work order's evidence shape directly against a
    real `State` instance: falsified against the unfixed code (confirmed by
    grep, see `test_pre_fix_shape_is_documented` below), a genuine
    `OSError(28, "No space left on device")` from the write step used to
    propagate out of `save()` unhandled; now it is caught and surfaces as
    `DiskLowSpace`, which `main()`'s handler (see `TestMainHandlesDiskLowSpace`
    above) turns into a resumable EXIT_LOCKED pause rather than a crash."""

    def test_enospc_during_write_text_is_caught_as_disklowspace(self, tmp_path, monkeypatch):
        leerie_root = tmp_path / "state-root"
        st = leerie.State(leerie_root, "run-enospc")
        try:
            st.data = {"task": "x"}

            def _raise_enospc(self, *a, **k):
                raise OSError(28, "No space left on device")

            monkeypatch.setattr(leerie.Path, "write_text", _raise_enospc)
            with pytest.raises(leerie.DiskLowSpace) as exc_info:
                st.save()
            assert "no space" in exc_info.value.raw_message.lower()
        finally:
            st.release_lock()

    def test_enospc_during_replace_is_caught_as_disklowspace(self, tmp_path, monkeypatch):
        # The atomic-rename half (os.replace) is the other half of the
        # write path and fails the same way under ENOSPC on some
        # filesystems/journal configurations (e.g. the destination
        # directory's own metadata write, distinct from the temp file's
        # content write above).
        leerie_root = tmp_path / "state-root"
        st = leerie.State(leerie_root, "run-enospc-2")
        try:
            st.data = {"task": "x"}

            def _raise_enospc(*a, **k):
                raise OSError(28, "No space left on device")

            monkeypatch.setattr(leerie.os, "replace", _raise_enospc)
            with pytest.raises(leerie.DiskLowSpace):
                st.save()
        finally:
            st.release_lock()

    def test_non_enospc_oserror_still_propagates_as_oserror(self, tmp_path, monkeypatch):
        # Only ENOSPC is reinterpreted as a disk-space pause; any other
        # I/O failure (permissions, a read-only mount unrelated to
        # capacity, etc.) must not be misreported as "disk full".
        leerie_root = tmp_path / "state-root"
        st = leerie.State(leerie_root, "run-eacces")
        try:
            st.data = {"task": "x"}

            def _raise_eacces(self, *a, **k):
                raise OSError(13, "Permission denied")

            monkeypatch.setattr(leerie.Path, "write_text", _raise_eacces)
            with pytest.raises(OSError) as exc_info:
                st.save()
            assert exc_info.value.errno == 13
            assert not isinstance(exc_info.value, leerie.DiskLowSpace)
        finally:
            st.release_lock()

    def test_pre_fix_shape_is_documented(self):
        # Regression guard for the historical gap this test class replaced:
        # State.save() must now actually reference DiskLowSpace/ENOSPC —
        # a revert back to the silent-propagation shape fails here.
        import inspect
        src = inspect.getsource(leerie.State.save)
        assert "ENOSPC" in src
        assert "DiskLowSpace" in src


class TestDiskCheckThresholdIsProportionalNotAFixedByteCount:
    """The N30 finding's own DESIGN note (leerie.py's comment directly above
    `DISK_MIN_FREE_RATIO`) explicitly considered and rejected a fixed-byte
    threshold: the "right" threshold scales with per-worktree cost *
    remaining subtasks, a quantity that needs external-repo measurement
    (pnpm store-sharing across worktrees, etc.) unavailable to a
    codebase-only investigation. The shipped fallback — a *fraction* of the
    filesystem's total capacity — is that finding's own documented
    fallback: it scales with disk size instead of pretending to know a
    per-run byte cost. This class pins that the shipped constant is that
    proportional shape (not a hard-coded byte/GB constant), and that both
    call sites (preflight + mid-run) share the one constant rather than
    each hard-coding their own number."""

    def test_constant_is_a_fraction_not_a_byte_count(self):
        assert 0 < leerie.DISK_MIN_FREE_RATIO < 1

    def test_message_scales_with_total_disk_size(self, tmp_path):
        # A 10 GB disk and a 1000 GB disk at the same ratio both cross the
        # threshold at the same fraction, not the same absolute byte count.
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.01, total=10 * 1024 ** 3)):
            small = leerie._disk_free_ratio(tmp_path)
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.01, total=1000 * 1024 ** 3)):
            large = leerie._disk_free_ratio(tmp_path)
        assert small == pytest.approx(large) == pytest.approx(0.01)
        assert small < leerie.DISK_MIN_FREE_RATIO
        assert large < leerie.DISK_MIN_FREE_RATIO

    def test_preflight_and_midrun_share_the_one_constant(self):
        import inspect
        preflight_src = inspect.getsource(leerie.preflight)
        execute_src = inspect.getsource(leerie.phase_execute)
        assert "DISK_MIN_FREE_RATIO" in preflight_src
        assert "DISK_MIN_FREE_RATIO" in execute_src

    def test_the_ratio_is_the_whole_rule(self):
        """The proportional floor is the only disk rule, by decision.

        A per-worktree measured bound sat on top of this and was wrong four
        separate ways before being withdrawn: it was unreachable dead code,
        then measured the wrong quantity (total rather than marginal), then
        scaled by the wrong count twice over, then broke its own
        "nothing to measure" sentinel by counting directory blocks. See
        IMPLEMENTATION.md's "Disk headroom (N30)" section.

        This asserts the withdrawal held, so a fifth attempt has to be a
        deliberate act that fails here first rather than an accretion.
        """
        import inspect
        src = inspect.getsource(leerie.phase_execute)
        assert "DISK_MIN_FREE_RATIO" in src, "the proportional floor was dropped"
        for gone in ("_disk_required_bytes", "_measure_worktree_bytes"):
            assert gone not in src, (
                f"{gone} is back in phase_execute. It was withdrawn after four "
                "failed revisions; reintroducing it needs the measurement the "
                "work order asked for (a df delta against a real second "
                "checkout), not another st_blocks predicate.")
        assert not hasattr(leerie, "_measure_worktree_bytes")
        assert not hasattr(leerie, "_disk_required_bytes")


class TestDepCaptureGuardsIncludeDiskLowSpace:
    """The dep_capture best-effort guards elsewhere in main()/backstop
    capture already catch the whole BaseException exit-signal family
    (TerminalAuthFailure, RateLimitedExit, ContextOverflow); DiskLowSpace
    must be part of that family too, or a disk-low pause mid-capture would
    escape as an unhandled BaseException instead of a non-fatal log line."""

    def test_every_such_guard_includes_disklowspace(self):
        import inspect
        src = inspect.getsource(leerie)
        # Every site of the shared 4-exception dep_capture guard tuple
        # must include DiskLowSpace alongside its siblings.
        old_shape = ("except (Exception, TerminalAuthFailure, RateLimitedExit,\n"
                     "                ContextOverflow)")
        assert old_shape not in src
        assert "ContextOverflow, DiskLowSpace)" in src


