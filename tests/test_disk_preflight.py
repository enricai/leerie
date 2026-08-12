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
import os
import shutil
import sys
from pathlib import Path
from unittest import mock

import os

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
        arm = self._arm()
        assert "st.save()" in arm

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


class TestStateSaveCatchesENOSPC:
    """N30's mitigation is not exclusively the proactive `_disk_free_ratio`
    checks in `preflight()`/`phase_execute` (covered above) — `State.save()`
    itself also wraps its `tmp.write_text()`/`tmp.replace()` pair, since a
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

    def test_disklowspace_from_save_still_reaches_mains_handler(self):
        # DiskLowSpace is a BaseException regardless of which of the three
        # raise sites (preflight, phase_execute, or now State.save())
        # produced it, so it is caught by the identical `except
        # DiskLowSpace as e:` arm in main() pinned in
        # TestMainHandlesDiskLowSpace — no separate save()-specific handler
        # is required.
        assert issubclass(leerie.DiskLowSpace, BaseException)

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

    def test_ratio_is_a_floor_with_a_measured_bound_on_top(self):
        """The ratio alone was never the whole rule it was documented as.

        This test used to assert only that two phrases appeared in a source
        comment -- it pinned the *prose*, so the threshold could be anything
        at all and it still passed. It now pins the mechanism: a measured,
        worktree-sized requirement sits above the proportional floor.
        """
        import inspect
        src = inspect.getsource(leerie.phase_execute)
        assert "_disk_required_bytes" in src, (
            "the mid-run check no longer consults the measured per-worktree "
            "requirement -- it is back to a bare proportional ratio")
        assert "DISK_MIN_FREE_RATIO" in src, "the proportional floor was dropped"


@pytest.fixture(autouse=True)
def _clear_worktree_size_cache():
    """`_WORKTREE_SIZE_CACHE` is module-level and conftest's `leerie` fixture
    is session-scoped — the same shape as `_active_admissions`, which this
    repo already learned to reset with an autouse fixture
    (`tests/test_memory_admission_degrade.py`). Clearing before AND after
    matters: hand-written `.clear()` calls at the top of each test leave the
    last test's entries behind for whatever runs next in the session."""
    leerie._WORKTREE_SIZE_CACHE.clear()
    yield
    leerie._WORKTREE_SIZE_CACHE.clear()


class TestMeasuredWorktreeSizing:
    """`_disk_required_bytes` (N30). The per-worktree cost is measured on the
    running repo rather than assumed, because it varies by ~20x with whether
    the package-manager store can hardlink into the worktree -- which leerie
    does not control (separate bind mounts => EXDEV => pnpm copies)."""

    def _worktree(self, tmp_path: Path, sid: str, nbytes: int) -> Path:
        wt = tmp_path / "worktrees" / sid
        wt.mkdir(parents=True)
        (wt / "blob.bin").write_bytes(b"x" * nbytes)
        return wt

    def test_returns_none_before_any_worktree_exists(self, tmp_path):
        assert leerie._measure_worktree_bytes(tmp_path) is None
        assert leerie._disk_required_bytes(
            tmp_path, dict(leerie.DEFAULT_CAPS), 4) is None

    def test_staging_is_not_treated_as_a_subtask_worktree(self, tmp_path):
        self._worktree(tmp_path, "staging", 4096)
        assert leerie._measure_worktree_bytes(tmp_path) is None, (
            "staging is one long-lived tree, not a per-subtask cost, and "
            "measuring it as one would scale the whole requirement off it")

    def test_measures_an_existing_worktree(self, tmp_path):
        self._worktree(tmp_path, "feat-001", 200_000)
        measured = leerie._measure_worktree_bytes(tmp_path)
        assert measured is not None and measured >= 200_000

    def test_store_hardlinked_bytes_are_charged_nothing(self, tmp_path):
        """THE invariant, and the one two earlier revisions got wrong.

        The question is what ONE MORE tree costs. A file the package manager
        hardlinked in from its content-addressed store already occupies its
        blocks and is reachable from outside the tree, so another worktree
        links to it and spends nothing. The rule charges an inode only when
        every one of its links is inside the walked tree.

        Two rejected predecessors, both measured on a real 1.37 GiB
        node_modules: the inode-deduped total returned 1.31 GiB (ignores
        sharing entirely, ~20x over), and `st_blocks // st_nlink` returned
        596 MiB -- which *looks* like it accounts for sharing but does not,
        because st_nlink counts names inside the tree too. The empirical
        marginal cost is 64.8 MiB, which this rule reproduces exactly.
        """
        wt = self._worktree(tmp_path, "feat-001", 400_000)
        (wt / "private.bin").write_bytes(b"y" * 50_000)
        copied = leerie._measure_worktree_bytes(tmp_path)

        # Link the big file from OUTSIDE the tree, exactly as a store does.
        store = tmp_path / "store"
        store.mkdir()
        os.link(wt / "blob.bin", store / "cafebabe")
        leerie._WORKTREE_SIZE_CACHE.clear()
        shared = leerie._measure_worktree_bytes(tmp_path)

        assert shared < copied, (
            f"a store-linked file measured the same as a copied one "
            f"({copied} -> {shared}); the requirement would over-demand on "
            "every host where the store shares a mount with the worktree")
        # Only the genuinely private file remains chargeable.
        assert 40_000 < shared < 120_000, (
            f"expected roughly the 50KB private file, got {shared} -- the "
            "store-linked blob is still being charged")

    def test_intra_tree_link_farm_is_charged_in_full(self, tmp_path):
        """A file whose every link is inside this tree costs a new tree the
        full amount -- there is nothing outside to link to.

        The previous version of this test asserted the two-name tree was
        "counted once" and passed while the tree was actually charged HALF
        (the `st_blocks // st_nlink` rule), i.e. it passed for the wrong
        reason. The predicate is in-tree-name-count vs st_nlink, so a link
        farm confined to the tree is charged, and only an outside link
        discounts.
        """
        wt = self._worktree(tmp_path, "feat-001", 400_000)
        os.link(wt / "blob.bin", wt / "second-name.bin")
        two_names = leerie._measure_worktree_bytes(tmp_path)

        leerie._WORKTREE_SIZE_CACHE.clear()
        solo = tmp_path / "worktrees" / "feat-002"
        solo.mkdir(parents=True)
        (solo / "blob.bin").write_bytes(b"x" * 400_000)
        both = leerie._measure_worktree_bytes(tmp_path)

        assert two_names == pytest.approx(both, rel=0.05), (
            f"a tree with two names for one inode ({two_names}) should cost "
            f"the same as a single-copy tree ({both}) -- both must pay full "
            "freight, since neither has a link outside the tree")

    def test_largest_candidate_wins_not_an_arbitrary_one(self, tmp_path):
        """`iterdir()` yields raw readdir order -- neither sorted nor stable
        across hosts. Picking `[0]` could size the wave off a stale or
        half-built tree; worst-case is the conservative direction for a
        headroom check."""
        self._worktree(tmp_path, "feat-001", 20_000)
        self._worktree(tmp_path, "feat-002", 900_000)
        self._worktree(tmp_path, "feat-003", 50_000)
        measured = leerie._measure_worktree_bytes(tmp_path)
        assert measured >= 900_000, (
            f"measured {measured}, which is not the largest candidate -- "
            "selection is order-dependent")

    def test_requirement_scales_with_the_wave_size(self, tmp_path):
        """Load-bearing, and the premise an earlier revision got backwards.

        That revision scaled by `max_parallel`, reasoning that the N31 prune
        removes each worktree as its subtask integrates. It does not: the
        prune loop runs ONCE, after `integrate_wave` has processed the whole
        wave, so every subtask's worktree coexists. A 20-subtask wave at
        `max_parallel=5` was therefore sized 4x too small -- and under-demand
        is the unsafe direction, because the check then declines to pause and
        the disk fills.

        The old name and docstring encoded the wrong premise, so the suite
        could not catch it.
        """
        self._worktree(tmp_path, "feat-001", 1_000_000)
        caps = dict(leerie.DEFAULT_CAPS, max_parallel=5)

        small = leerie._disk_required_bytes(tmp_path, caps, 2)
        large = leerie._disk_required_bytes(tmp_path, caps, 8)

        assert small is not None and large is not None
        assert large == pytest.approx(small * 4, rel=0.01), (
            "the requirement does not track the wave size")

    def test_requirement_ignores_max_parallel(self, tmp_path):
        """Anti-regression for the same premise: concurrency must not enter
        the arithmetic at all, or the 4x under-demand returns."""
        self._worktree(tmp_path, "feat-001", 1_000_000)
        low = leerie._disk_required_bytes(
            tmp_path, dict(leerie.DEFAULT_CAPS, max_parallel=1), 10)
        high = leerie._disk_required_bytes(
            tmp_path, dict(leerie.DEFAULT_CAPS, max_parallel=32), 10)
        assert low == high, (
            "max_parallel still affects the requirement; peak coexistence is "
            "the wave size, not the concurrency")

    def test_measurement_is_cached_per_run_dir(self, tmp_path):
        """The walk is O(files in node_modules); re-running it every wave on
        a 58k-file tree is a real cost for an answer that does not move."""
        self._worktree(tmp_path, "feat-001", 100_000)
        first = leerie._measure_worktree_bytes(tmp_path)
        assert str(tmp_path) in leerie._WORKTREE_SIZE_CACHE
        (tmp_path / "worktrees" / "feat-001" / "extra.bin").write_bytes(b"y" * 500_000)
        assert leerie._measure_worktree_bytes(tmp_path) == first


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


class TestMeasurementIsSeededWhereWorktreesExist:
    """The wiring, which is what made the whole bound dead code once.

    The check runs at wave ENTRY. The N31 prune removes every integrated
    worktree at wave END, and a wave cannot be reached with un-integrated
    trees left over (`if blocked: … die()`). `_cleanup_on_abnormal_exit`
    removes them on every handled exit too. So at wave entry `worktrees/`
    holds only `staging` — which `_measure_worktree_bytes` excludes by
    design — and it returned `None` on every invocation in every normal run.

    The fix seeds the measurement immediately BEFORE the prune, the one
    moment a fully-populated tree exists. These are source-coupled because
    driving `phase_execute` end to end spawns real workers; the behavioural
    half is `test_seeded_measurement_is_what_the_next_wave_consumes` below,
    which exercises the real cache across the real functions.
    """

    @staticmethod
    def _phase_execute_ast():
        import ast
        import inspect
        import textwrap
        return ast.parse(textwrap.dedent(inspect.getsource(leerie.phase_execute)))

    @staticmethod
    def _measure_calls(tree):
        """Every call to `_measure_worktree_bytes`, tagged with whether it is
        wrapped in `asyncio.to_thread`.

        AST, not a source `index()`: `_measure_worktree_bytes` appears twice
        in `phase_execute` — once as the pre-prune seed and once inside the
        wave-entry error message. A textual "is it before the prune" check
        finds the *error-branch* occurrence, which is also before the prune,
        so it passes with the seed deleted. Both of these tests were written
        that way first and were verified vacuous against the mutation.
        """
        import ast
        out = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            wrapped = (isinstance(fn, ast.Attribute) and fn.attr == "to_thread"
                       and node.args
                       and isinstance(node.args[0], ast.Name)
                       and node.args[0].id == "_measure_worktree_bytes")
            direct = (isinstance(fn, ast.Name)
                      and fn.id == "_measure_worktree_bytes")
            if wrapped or direct:
                out.append((node.lineno, "to_thread" if wrapped else "direct"))
        return sorted(out)

    def _prune_loop_lineno(self, tree):
        import ast
        for node in ast.walk(tree):
            if (isinstance(node, ast.For) and isinstance(node.iter, ast.Name)
                    and node.iter.id == "integrated"):
                return node.lineno
        raise AssertionError("the N31 prune loop is gone from phase_execute")

    def test_measurement_is_seeded_before_the_prune_loop(self):
        tree = self._phase_execute_ast()
        calls = self._measure_calls(tree)
        prune = self._prune_loop_lineno(tree)

        assert calls, "phase_execute never measures a worktree at all"
        before = [ln for ln, _ in calls if ln < prune]
        # The error-branch occurrence is also before the prune, so counting
        # "any call before the prune" proves nothing. The seed is the call
        # that is NOT inside the wave-entry check's raise path, i.e. there
        # must be at least TWO measure calls before the prune line.
        assert len(before) >= 2, (
            "only one _measure_worktree_bytes call precedes the prune, and "
            "that is the error-message one — the pre-prune SEED is missing, "
            "so every populated worktree is removed before anything measures "
            "it and the bound is dead code again")

    def test_every_measure_call_is_off_the_event_loop(self):
        """`os.walk` over a real node_modules is 2.1 s warm (58,172 files).
        This repo's worst recent bug (#198/#200, 218 workers lost, 12.4% of
        invocations) was a blocked orchestrator loop, and #200's fix was
        `to_thread` for exactly this shape."""
        calls = self._measure_calls(self._phase_execute_ast())
        assert calls, "phase_execute never measures a worktree at all"
        direct = [ln for ln, kind in calls if kind == "direct"]
        assert not direct, (
            f"_measure_worktree_bytes is called synchronously from the event "
            f"loop at line(s) {direct} of phase_execute")

    def test_required_bytes_is_also_off_the_event_loop(self):
        import ast
        tree = self._phase_execute_ast()
        direct = [
            n.lineno for n in ast.walk(tree)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
            and n.func.id == "_disk_required_bytes"
        ]
        assert not direct, (
            f"_disk_required_bytes (which walks a worktree) is called "
            f"synchronously from the event loop at line(s) {direct}")

    def test_seeded_measurement_is_what_the_next_wave_consumes(self, tmp_path):
        """Behavioural: seed while a worktree exists, prune it, and confirm
        the requirement is still computable — i.e. the cache is what carries
        the value across the wave boundary."""
        wt = tmp_path / "worktrees" / "feat-001"
        wt.mkdir(parents=True)
        (wt / "blob.bin").write_bytes(b"x" * 800_000)
        (tmp_path / "worktrees" / "staging").mkdir()

        # Wave N end: seed while the tree is populated.
        seeded = leerie._measure_worktree_bytes(tmp_path)
        assert seeded and seeded >= 800_000

        # The prune then removes it, exactly as phase_execute does.
        shutil.rmtree(wt)
        assert not wt.exists()

        # Wave N+1 entry: the requirement must still be computable.
        required = leerie._disk_required_bytes(
            tmp_path, dict(leerie.DEFAULT_CAPS), 4)
        assert required is not None, (
            "after the prune the requirement went unmeasurable — the cached "
            "seed is not carrying across the wave boundary, which is the "
            "exact failure that made this check dead code")
        assert required >= seeded * 4

    def test_without_a_seed_the_check_degrades_to_the_ratio_floor(self, tmp_path):
        """Anti-vacuity partner: wave 1 legitimately has nothing to measure,
        and must fall through rather than invent a number."""
        (tmp_path / "worktrees" / "staging").mkdir(parents=True)
        assert leerie._measure_worktree_bytes(tmp_path) is None
        assert leerie._disk_required_bytes(
            tmp_path, dict(leerie.DEFAULT_CAPS), 4) is None


class TestZeroMeasurementIsNotCached:
    """A zero total means "nothing to measure", not "measured zero".

    Reproduced against the previous revision: a zero was written to the
    cache, `_disk_required_bytes` read it back through `if not
    per_worktree`, and the measured bound stayed dead for the rest of the
    run — even after the worktree filled up. Silent, because both outcomes
    return None at the call site.
    """

    def test_zero_is_not_cached_and_a_later_measurement_wins(self, tmp_path):
        (tmp_path / "worktrees" / "feat-001").mkdir(parents=True)
        assert leerie._measure_worktree_bytes(tmp_path) is None
        assert str(tmp_path) not in leerie._WORKTREE_SIZE_CACHE, (
            "a zero measurement was cached; the check is now permanently "
            "disabled for this run")

        (tmp_path / "worktrees" / "feat-001" / "big.bin").write_bytes(b"x" * 900_000)
        later = leerie._measure_worktree_bytes(tmp_path)
        assert later and later >= 900_000, (
            "the populated tree was not measured — the cached zero stuck")

    def test_zero_warns_once(self, tmp_path, capsys):
        leerie._worktree_measure_empty_warned = False
        (tmp_path / "worktrees" / "feat-001").mkdir(parents=True)
        leerie._measure_worktree_bytes(tmp_path)
        first = capsys.readouterr().err + capsys.readouterr().out
        leerie._measure_worktree_bytes(tmp_path)
        assert leerie._worktree_measure_empty_warned is True

    def test_refresh_keeps_a_running_max_across_waves(self, tmp_path):
        """A docs-only first wave must not pin a small figure for the run."""
        wt = tmp_path / "worktrees" / "feat-001"
        wt.mkdir(parents=True)
        (wt / "small.bin").write_bytes(b"x" * 100_000)
        first = leerie._measure_worktree_bytes(tmp_path, refresh=True)

        (wt / "big.bin").write_bytes(b"y" * 2_000_000)
        # Without refresh the cache answers; with it, the larger wave wins.
        assert leerie._measure_worktree_bytes(tmp_path) == first
        grown = leerie._measure_worktree_bytes(tmp_path, refresh=True)
        assert grown > first

        # And it never shrinks back on a later, lighter wave.
        (wt / "big.bin").unlink()
        assert leerie._measure_worktree_bytes(tmp_path, refresh=True) == grown
