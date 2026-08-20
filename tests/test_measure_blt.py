"""`_measure_blt` — the single place leerie executes a repo's own BLT command.

Extracted from `_capture_conformance_baseline` (DESIGN §9) so the baseline and
every later measurement share one implementation. This file covers each branch
of the verdict, and pins the argv contract that the extraction carried with it.

The verdict is exit-code based on purpose: 100% reliable, and it needs no
per-framework output parsing.
"""
from __future__ import annotations

import inspect
import subprocess

import pytest

from tests.conftest import _run



def _call(leerie, cmd="pytest", tree="/tmp/x", timeout=60.0, **kw):
    return _run(leerie._measure_blt(
        "tests", cmd, tree, timeout=timeout,
        log_path=kw.pop("log_path", None), verbosity="quiet", **kw))


@pytest.fixture
def streaming(leerie, monkeypatch):
    """Install a stubbed `_run_streaming` and hand back its call log."""
    calls = []

    def install(result):
        async def _fake(cmd, **kwargs):
            calls.append((cmd, kwargs))
            if isinstance(result, BaseException):
                raise result
            return result
        monkeypatch.setattr(leerie, "_run_streaming", _fake)
        return calls
    return install


# --------------------------------------------------------------------------
# The five verdict branches
# --------------------------------------------------------------------------

def test_exit_zero_is_measured_and_passed(leerie, streaming):
    streaming((0, "42 passed"))
    ax = _call(leerie)
    assert (ax["ran"], ax["measured"], ax["passed"]) == (True, True, True)
    assert ax["command"] == "pytest"
    assert ax["summary"] == "42 passed"


def test_real_failure_is_measured_and_not_passed(leerie, streaming):
    """ANTI-VACUITY PARTNER for the runner-missing test below.

    Without this, an implementation that demotes EVERY non-zero exit to
    `measured: False` passes the demotion test — and then a genuinely red
    axis silently stops counting as red anywhere.
    """
    streaming((1, "2 failed, 9838 passed"))
    ax = _call(leerie)
    assert (ax["ran"], ax["measured"], ax["passed"]) == (True, True, False)


def test_runner_missing_is_unmeasurable_not_red(leerie, streaming):
    """"could not measure" is not "RED". Recording a missing runner as red
    hands the conformer a useless delta and provokes it to re-derive the
    baseline destructively (git stash / checkout <base> -- .)."""
    streaming((127, "bash: line 1: pytest: command not found"))
    ax = _call(leerie)
    assert (ax["ran"], ax["measured"], ax["passed"]) == (True, False, None)


def test_timeout_is_measured_and_failed(leerie, streaming):
    streaming(subprocess.TimeoutExpired(cmd="pytest", timeout=60))
    ax = _call(leerie, timeout=60.0)
    assert (ax["ran"], ax["measured"], ax["passed"]) == (True, True, False)
    assert ax["summary"] == "timed out after 60s"


def test_unexpected_exception_did_not_run(leerie, streaming):
    streaming(OSError("no such cwd"))
    ax = _call(leerie)
    assert (ax["ran"], ax["measured"], ax["passed"]) == (False, False, None)
    assert "OSError" in ax["summary"]


@pytest.mark.parametrize("cmd", ["", "   ", None])
def test_absent_command_is_not_applicable(leerie, streaming, cmd):
    """A repo with no command for an axis. `ran=False` means 'not
    applicable', and no subprocess is spawned at all."""
    calls = streaming((0, "unreachable"))
    ax = _run(leerie._measure_blt("lint", cmd, "/tmp/x", timeout=1.0,
                                  log_path=None, verbosity="quiet"))
    assert (ax["ran"], ax["measured"], ax["passed"]) == (False, False, None)
    assert ax["command"] == ""
    assert calls == [], "an absent command must not spawn a subprocess"


# --------------------------------------------------------------------------
# Shape and argv contract
# --------------------------------------------------------------------------

def test_every_branch_carries_the_full_axis_shape(leerie, streaming):
    """`measured` is mandatory on every axis dict — no consumer supplies a
    default, and a missing key would read as falsy (i.e. unmeasurable)."""
    keys = {"ran", "measured", "passed", "command", "summary"}
    for result in [(0, "ok"), (1, "boom"), (127, "command not found"),
                   subprocess.TimeoutExpired(cmd="c", timeout=1),
                   OSError("x")]:
        streaming(result)
        assert keys <= set(_call(leerie)), result


def test_argv_is_bash_dash_c_exactly(leerie, streaming):
    calls = streaming((0, "ok"))
    _call(leerie, cmd="pnpm run test")
    assert calls[0][0] == ["bash", "-c", "pnpm run test"]


def test_source_never_uses_a_login_shell(leerie):
    """N8. A login shell sources /etc/profile and ~/.bash_profile and
    DISCARDS Docker-ENV-only PATH additions (e.g. mise's shims dir), so a
    `-lc` invocation reports `command not found` for a runner that resolves
    fine under `-c`."""
    src = inspect.getsource(leerie._measure_blt)
    assert '"-lc"' not in src and "'-lc'" not in src
    assert '["bash", "-c", cmd]' in src


def test_summary_is_tail_truncated(leerie, streaming):
    """The tail is prompt context and a log line, not a transcript."""
    streaming((1, "x" * 5000))
    assert len(_call(leerie)["summary"]) == 400


def test_runs_in_the_requested_tree(leerie, streaming):
    calls = streaming((0, "ok"))
    _call(leerie, tree="/work/subtask-a")
    assert calls[0][1]["cwd"] == "/work/subtask-a"
