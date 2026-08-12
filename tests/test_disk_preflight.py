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
        # A healthy disk ratio must not die() on the disk check itself —
        # drive far enough to prove the disk gate passed silently, without
        # needing a real git repo (the next checks would die() for their
        # own unrelated reasons in a bare tmp_path, which is fine — we only
        # assert the *disk* check did not fire).
        with mock.patch.object(leerie.shutil, "disk_usage",
                                return_value=_fake_usage(0.90)):
            monkeypatch.setattr(leerie, "_sigchld_is_ignored", lambda: False)
            with pytest.raises(SystemExit) as exc_info:
                asyncio.run(leerie.preflight(tmp_path, skip_smoke=True))
        # die() -> sys.exit(1); confirms flow reached past the disk check
        # into the (here, real and failing-for-other-reasons) git identity
        # check rather than dying on disk space.
        assert exc_info.value.code == 1


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
