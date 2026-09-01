"""Execution coverage for `main()`'s top-level try/except dispatch
(orchestrator/leerie.py, roughly :32811-:33457).

Nothing in the suite ever called `main()` before this file — every existing
pin on this region is source-coupling (`inspect.getsource(leerie.main)`,
e.g. `tests/test_terminal_auth_routing.py`, `tests/test_disk_preflight.py`,
`tests/test_warnings_before_die.py`), which proves the handler text exists
but never proves any of it *executes*. That is precisely the trap
CLAUDE.md's "structure vs substance" table documents: a source-coupling
guard can pass unconditionally while the branch it names is dead. This file
drives the real `main()` end to end (real argv, real git repo, a real
`State`) with `_orchestrate` stubbed to raise each of main()'s handled
exceptions in turn, and asserts on the OBSERABLE result — the process exit
code, `state.json` on disk, and the `orchestrator.exit_code` sidecar file —
not on source text.

`_orchestrate` is the seam: everything above it in `main()` (argv parsing,
the ~30 `resolve_*` calls, `State` construction, `_install_run_log_tee`) has
to run for real to reach it, and everything below it (the exception
dispatch under test) runs for real too. Stubbing anything closer to the
try/except (e.g. `asyncio.run` itself) would make the resolver block
between argv and the try inert.
"""
from __future__ import annotations

import json
import subprocess
import sys

import pytest

from tests.conftest import fake_claude_on_path


@pytest.fixture
def repo(leerie, tmp_path, monkeypatch):
    """A real git repo with a clean working tree and configured identity —
    `_preflight_repo()` (called unconditionally on a non-resume run, before
    the try/except under test) requires both.

    Also installs the two prerequisites every `main()` invocation in this
    file needs. They live here rather than in `_run_main` because three
    tests below call `leerie.main()` directly, and a prerequisite attached
    to the harness would silently miss them."""
    r = tmp_path / "repo"
    r.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=r, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=t",
         "commit", "-q", "--allow-empty", "-m", "init"],
        cwd=r, check=True)
    monkeypatch.chdir(r)
    # This process itself runs inside a leerie container, which sets
    # LEERIE_STATE_DIR=/leerie-state so the real orchestrator's own state
    # never lands in a repo checkout. `resolve_leerie_root` honours that env
    # var over the repo-relative default, so without clearing it here every
    # run in this file would try to mint `<run-id>` directories under the
    # REAL state root this run's own orchestrator owns.
    monkeypatch.delenv("LEERIE_STATE_DIR", raising=False)
    # main() gates on `shutil.which("claude")` and die()s (exit 1) before it
    # ever mints the run dir, let alone reaches the try/except under test.
    # The binary is absent on CI runners, so without this stub every exit-code
    # assertion in this file collapses to `1 == <expected>`. See the helper's
    # docstring in tests/conftest.py.
    fake_claude_on_path(tmp_path, monkeypatch)
    # `leerie` is a session-scoped fixture (one module load for the whole
    # suite) and `_CURRENT_RUN_ID` is a module-level global that `die()` reads
    # to annotate its message -- it otherwise leaks a prior test's run id into
    # this one's error text.
    monkeypatch.setattr(leerie, "_CURRENT_RUN_ID", None, raising=False)
    return r


def _run_main(leerie, monkeypatch, repo, run_id, *, orchestrate_stub):
    """Invoke the real `main()` with `_orchestrate` replaced by
    `orchestrate_stub` (an async callable). Returns (exit_code_or_None,
    run_dir)."""
    monkeypatch.setattr(
        sys, "argv", ["leerie", "a trivial task", "--run-id", run_id])
    monkeypatch.setattr(leerie, "_orchestrate", orchestrate_stub)
    # main()'s first two statements (_restore_sigchld_default,
    # _become_subreaper) are left unstubbed so they get exercised for real.
    # They are NOT side-effect-free: _become_subreaper() makes this pytest
    # process a child-subreaper, which changes what process liveness means
    # for every later test. conftest's autouse `_restore_child_subreaper`
    # fixture puts the flag back.
    run_dir = repo / ".leerie" / "runs" / run_id
    exit_code = None
    try:
        leerie.main()
    except SystemExit as e:
        exit_code = e.code
    return exit_code, run_dir


def _state_json(run_dir):
    return json.loads((run_dir / "state.json").read_text())


class TestWorkerError:
    def test_worker_error_exits_1(self, leerie, monkeypatch, repo):
        async def _boom(*a, **kw):
            raise leerie.WorkerError("worker died")

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-worker-error",
            orchestrate_stub=_boom)

        assert exit_code == 1
        assert (run_dir / "orchestrator.exit_code").read_text().strip() == "1"
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)


class TestContextOverflow:
    def test_context_overflow_pauses_resumably(self, leerie, monkeypatch,
                                                repo):
        async def _boom(*a, **kw):
            raise leerie.ContextOverflow("Prompt is too long",
                                         from_worker=True)

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-ctx-overflow",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        assert (run_dir / "orchestrator.exit_code").read_text().strip() == \
            str(leerie.EXIT_LOCKED)
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)

    def test_context_overflow_preflight_smoke_test_message(
            self, leerie, monkeypatch, repo, capsys):
        """`from_worker=False` selects the other remedy message (naming the
        preflight smoke test rather than worker prompt size)."""
        async def _boom(*a, **kw):
            raise leerie.ContextOverflow("Prompt is too long",
                                         from_worker=False)

        exit_code, _run_dir = _run_main(
            leerie, monkeypatch, repo, "main-ctx-overflow-preflight",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        out = capsys.readouterr().out
        assert "preflight smoke test" in out


class TestDiskLowSpace:
    def test_disk_low_space_pauses_resumably(self, leerie, monkeypatch,
                                             repo):
        async def _boom(*a, **kw):
            raise leerie.DiskLowSpace("2.1% free on /leerie-state")

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-disk-low",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)


class TestTerminalAuthFailure:
    def test_terminal_auth_failure_pauses_resumably(self, leerie,
                                                     monkeypatch, repo):
        async def _boom(*a, **kw):
            raise leerie.TerminalAuthFailure("OAuth session expired")

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-auth-locked",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)


class TestRateLimitedExit:
    def test_out_of_credits_pauses_resumably(self, leerie, monkeypatch,
                                             repo):
        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(
                None, "out of credits", out_of_credits=True)

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-out-of-credits",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)

    def test_rate_limit_with_no_reset_time_uses_fixed_backoff(
            self, leerie, monkeypatch, repo):
        """reset_at=None takes the fixed-backoff auto-resume arm, which
        calls `_sleep_then_reexec`. Stub that too — it either os.execv's
        (never returns) or returns an int exit code on interrupt/failure;
        stubbing it to return a sentinel code proves main() reached and
        used its return value rather than the out-of-credits arm."""
        sentinel_rc = 130

        def _fake_sleep_then_reexec(st, wait_seconds, reason):
            assert wait_seconds == leerie.RATE_LIMIT_RETRY_BACKOFF_SEC
            assert "no reset time" in reason
            return sentinel_rc

        monkeypatch.setattr(
            leerie, "_sleep_then_reexec", _fake_sleep_then_reexec)

        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(None, "rate limited",
                                         out_of_credits=False)

        exit_code, _run_dir = _run_main(
            leerie, monkeypatch, repo, "main-rate-limited-no-reset",
            orchestrate_stub=_boom)

        assert exit_code == sentinel_rc

    def test_wait_within_the_ceiling_still_sleeps(
            self, leerie, monkeypatch, repo):
        """A five-hour session limit is bounded and legitimately sleepable —
        the ceiling must not turn every rate-limit into a pause."""
        called = {}

        def _fake_sleep_then_reexec(st, wait_seconds, reason):
            called["wait"] = wait_seconds
            return 130

        monkeypatch.setattr(leerie, "_sleep_then_reexec",
                            _fake_sleep_then_reexec)
        soon = (leerie.datetime.now(leerie.timezone.utc)
                + leerie.timedelta(hours=2))

        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(soon, "rate_limit_event",
                                         limit_type="five_hour")

        exit_code, _ = _run_main(
            leerie, monkeypatch, repo, "main-rate-limited-short",
            orchestrate_stub=_boom)

        assert exit_code == 130
        assert called["wait"] > 0

    def test_wait_beyond_the_ceiling_pauses_instead_of_sleeping(
            self, leerie, monkeypatch, repo):
        """The weekly-limit case. Corpus run e6f7f8c3 computed a 43.7-HOUR
        wait from a `seven_day` rejection and slept on it, holding the
        run-directory flock; the user killed it. Asserts the DISPOSITION
        (EXIT_LOCKED, `_sleep_then_reexec` never reached), not just that
        some code path ran."""
        def _must_not_sleep(st, wait_seconds, reason):
            raise AssertionError(
                f"slept {wait_seconds}s instead of pausing")

        monkeypatch.setattr(leerie, "_sleep_then_reexec", _must_not_sleep)
        far = (leerie.datetime.now(leerie.timezone.utc)
               + leerie.timedelta(hours=43, minutes=42))

        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(far, "rate_limit_event",
                                         limit_type="seven_day")

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-rate-limited-weekly",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED
        assert isinstance(_state_json(run_dir), dict)

    def test_ceiling_is_keyed_on_the_wait_not_the_limit_type(
            self, leerie, monkeypatch, repo):
        """A renamed/unknown `rateLimitType` must not defeat the ceiling —
        the failure shape CLAUDE.md records for `Agent` -> `Task`."""
        def _must_not_sleep(st, wait_seconds, reason):
            raise AssertionError("slept despite exceeding the ceiling")

        monkeypatch.setattr(leerie, "_sleep_then_reexec", _must_not_sleep)
        far = (leerie.datetime.now(leerie.timezone.utc)
               + leerie.timedelta(days=3))

        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(far, "rate_limit_event",
                                         limit_type=None)

        exit_code, _ = _run_main(
            leerie, monkeypatch, repo, "main-rate-limited-unknown-type",
            orchestrate_stub=_boom)

        assert exit_code == leerie.EXIT_LOCKED

    def test_env_override_raises_the_ceiling_enough_to_sleep(
            self, leerie, monkeypatch, repo):
        """The ceiling is a resolved cap, not a frozen constant: a wait that
        pauses at the default must sleep once the operator raises it. Makes
        the two runs DISAGREE on the same input, so a test that ignored the
        override could not pass by coincidence."""
        far = (leerie.datetime.now(leerie.timezone.utc)
               + leerie.timedelta(hours=10))

        async def _boom(*a, **kw):
            raise leerie.RateLimitedExit(far, "rate_limit_event",
                                         limit_type="seven_day")

        # Default ceiling is 6h, so a 10h wait pauses.
        def _must_not_sleep(st, wait_seconds, reason):
            raise AssertionError("slept at the default ceiling")

        monkeypatch.setattr(leerie, "_sleep_then_reexec", _must_not_sleep)
        assert _run_main(
            leerie, monkeypatch, repo, "main-rl-default-ceiling",
            orchestrate_stub=_boom)[0] == leerie.EXIT_LOCKED

        # Same wait, ceiling raised past it → sleeps instead.
        slept = {}

        def _record(st, wait_seconds, reason):
            slept["wait"] = wait_seconds
            return 130

        monkeypatch.setattr(leerie, "_sleep_then_reexec", _record)
        monkeypatch.setenv(leerie.MAX_RATE_LIMIT_WAIT_ENV, str(20 * 3600))
        assert _run_main(
            leerie, monkeypatch, repo, "main-rl-raised-ceiling",
            orchestrate_stub=_boom)[0] == 130
        assert slept["wait"] > 0


class TestRateLimitWaitCapResolution:
    """CLI > env > leerie.toml > DEFAULT_CAPS, the same ladder
    `resolve_worker_timeout_sec` uses."""

    def test_default(self, leerie, tmp_path):
        assert (leerie.resolve_max_rate_limit_wait_sec(tmp_path, None)
                == leerie.DEFAULT_CAPS["max_rate_limit_wait_sec"])

    def test_cli_beats_env_and_file(self, leerie, tmp_path, monkeypatch):
        monkeypatch.setenv(leerie.MAX_RATE_LIMIT_WAIT_ENV, "1800")
        (tmp_path / leerie.MAX_RATE_LIMIT_WAIT_FILE).write_text(
            "max_rate_limit_wait_sec = 3600\n")
        assert leerie.resolve_max_rate_limit_wait_sec(tmp_path, 999) == 999

    def test_env_beats_file(self, leerie, tmp_path, monkeypatch):
        (tmp_path / leerie.MAX_RATE_LIMIT_WAIT_FILE).write_text(
            "max_rate_limit_wait_sec = 3600\n")
        monkeypatch.setenv(leerie.MAX_RATE_LIMIT_WAIT_ENV, "1800")
        assert leerie.resolve_max_rate_limit_wait_sec(tmp_path, None) == 1800

    def test_file_beats_default(self, leerie, tmp_path, monkeypatch):
        monkeypatch.delenv(leerie.MAX_RATE_LIMIT_WAIT_ENV, raising=False)
        (tmp_path / leerie.MAX_RATE_LIMIT_WAIT_FILE).write_text(
            "max_rate_limit_wait_sec = 3600\n")
        assert leerie.resolve_max_rate_limit_wait_sec(tmp_path, None) == 3600


class TestKeyboardInterrupt:
    def test_keyboard_interrupt_exits_130(self, leerie, monkeypatch, repo):
        async def _boom(*a, **kw):
            raise KeyboardInterrupt()

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-keyboard-interrupt",
            orchestrate_stub=_boom)

        assert exit_code == 130
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)


class TestInterruptedBySignal:
    def test_sigterm_exits_143(self, leerie, monkeypatch, repo):
        async def _boom(*a, **kw):
            raise leerie.InterruptedBySignal("SIGTERM")

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-sigterm",
            orchestrate_stub=_boom)

        assert exit_code == 143  # 128 + 15
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)

    def test_sighup_exits_129(self, leerie, monkeypatch, repo):
        async def _boom(*a, **kw):
            raise leerie.InterruptedBySignal("SIGHUP")

        exit_code, _run_dir = _run_main(
            leerie, monkeypatch, repo, "main-sighup",
            orchestrate_stub=_boom)

        assert exit_code == 129  # 128 + 1


class TestSystemExit:
    def test_die_inside_orchestrate_reraises_and_writes_sidecars(
            self, leerie, monkeypatch, repo):
        """die() raises SystemExit; main()'s handler is a clean pass-through
        (not treated as an unhandled crash) but still writes
        finished_at/run.json/orchestrator.exit_code before re-raising, so a
        die() mid-run stays discoverable by `fetch_branch`'s completion
        probe."""
        async def _boom(*a, **kw):
            leerie.die("synthetic mid-run failure", code=7)

        exit_code, run_dir = _run_main(
            leerie, monkeypatch, repo, "main-systemexit",
            orchestrate_stub=_boom)

        assert exit_code == 7
        assert (run_dir / "orchestrator.exit_code").read_text().strip() == "7"
        # state.json itself is only re-saved here when `st.data["task"]` is
        # truthy (guards against poisoning a stub run's state with a bare
        # `{}`) — since `_orchestrate` was stubbed before task-seeding ever
        # runs, that guard is deliberately NOT met here; run.json's
        # `finished_at` is unconditional and is what fetch_branch's
        # discovery script actually depends on.
        run_json = json.loads((run_dir / "run.json").read_text())
        assert run_json.get("finished_at") is not None


class TestUnhandledException:
    def test_unhandled_exception_reraises_and_cleans_up(
            self, leerie, monkeypatch, repo, capsys):
        class _Boom(RuntimeError):
            pass

        async def _boom(*a, **kw):
            raise _Boom("genuinely unexpected")

        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "a trivial task", "--run-id", "main-unhandled"])
        monkeypatch.setattr(leerie, "_orchestrate", _boom)

        with pytest.raises(_Boom):
            leerie.main()

        out = capsys.readouterr().out
        assert "unhandled exception: _Boom" in out
        run_dir = repo / ".leerie" / "runs" / "main-unhandled"
        # Confirms state.json exists and round-trips (the arm reached
        # `_save_state_best_effort`); the task field itself is seeded
        # inside the stubbed `_orchestrate`, so it's absent here.
        data = _state_json(run_dir)
        assert isinstance(data, dict)


class TestCleanExit:
    def test_clean_run_exits_zero_with_no_sys_exit(
            self, leerie, monkeypatch, repo):
        """A normal completion (no exception, exit_code stays 0) never calls
        `sys.exit()` at all — `main()` just returns. This is the one arm
        the exception-focused tests above cannot exercise: every one of
        them raises, so a bare `sys.exit(exit_code)` with exit_code == 0
        would never be observed as skipped without this positive control."""
        async def _ok(*a, **kw):
            return None

        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "a trivial task", "--run-id", "main-clean-exit"])
        monkeypatch.setattr(leerie, "_orchestrate", _ok)

        # No SystemExit raised at all.
        leerie.main()

        run_dir = repo / ".leerie" / "runs" / "main-clean-exit"
        assert (run_dir / "orchestrator.exit_code").read_text().strip() == "0"


class TestPhaseFlagShortCircuit:
    def test_phase_heal_with_no_index_and_no_failures_returns_early(
            self, leerie, monkeypatch, repo):
        """`--phase heal` with no judge INDEX.json runs phase_judge first
        (stubbed to a no-op), finds no index afterward either, and returns
        without ever reaching phase_heal or `_orchestrate` — covering the
        `index_path.exists()` / `failing_by_type` early-return arm at
        :32993-32995 without needing a real judge/heal worker run."""
        run_id = "main-phase-heal"
        run_dir = repo / ".leerie" / "runs" / run_id
        run_dir.mkdir(parents=True)
        (run_dir / "subtasks").mkdir()
        (run_dir / "state.json").write_text(
            json.dumps({"task": "a trivial task"}))
        # `main()`'s own PRIMARY `State(...)` construction (before the
        # `if args.phase:` branch) takes an exclusive flock on this same
        # run_dir for the whole call, because `--phase` reuses one
        # `args.run_id` for both the primary state and the target run to
        # inspect — the SECOND `State(...)` inside the `--phase` branch then
        # self-conflicts with the first, since flock is scoped to the open
        # file description, not the process (verified live: two `os.open()`s
        # of the same directory in one process do NOT share a lock). That
        # conflict is real production behaviour, not a test artifact, and
        # fixing it is outside this subtask's owned line range (the primary
        # `State(...)` call sits above :32811). Disabling the lock here
        # isolates the `--phase` branch itself for coverage purposes without
        # taking on that fix.
        monkeypatch.setattr(leerie.State, "_acquire_lock", lambda self: None)

        judge_calls = []

        async def _fake_phase_judge(*a, **kw):
            judge_calls.append(1)

        async def _orchestrate_unreached(*a, **kw):
            raise AssertionError(
                "--phase heal must not fall through to _orchestrate")

        monkeypatch.setattr(leerie, "phase_judge", _fake_phase_judge)
        monkeypatch.setattr(leerie, "_orchestrate", _orchestrate_unreached)
        monkeypatch.setattr(
            sys, "argv",
            ["leerie", "--phase", "heal", "--run-id", run_id])

        # --phase short-circuits before the try/except under test; main()
        # returns normally (no SystemExit).
        leerie.main()

        assert judge_calls == [1], "phase_judge should run once (no index)"
