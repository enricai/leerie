"""Log lines name what failed (live, 2026-11-05).

Run 80e66fce's `tests-failed:` advisory was 78,009 characters: the scoped
command naming all 1,037 files it ran, then the outcome, and the failing
files nowhere. Run ab17a3a2's Ctrl-C logged `cleanup failed (non-fatal): `
with nothing after it."""
from __future__ import annotations


def test_a_failing_test_axis_names_its_outcome_and_failing_files(leerie):
    cmd = "npx vitest run " + " ".join(f"src/t{i}.test.ts" for i in range(1037))
    ids = [f"src/t{i}.test.ts :: case {i}" for i in (7, 3, 900)]
    res = {"tests": {"ran": True, "passed": False, "command": cmd,
                     "summary": "Test Files  3 failed | 1034 passed (1037)",
                     "failing_ids": ids}}
    (line,) = leerie._summarize_residuals(res)
    # The outcome first, then the shortened command, then the files.
    assert line.startswith("tests-failed: Test Files  3 failed | 1034 passed")
    assert f"… ({len(cmd)} characters)" in line
    assert line.endswith(
        "; failing: src/t3.test.ts, src/t7.test.ts, src/t900.test.ts")
    assert len(line) < 500


def test_the_failing_files_come_from_the_report_and_are_bounded(leerie):
    files = {f"id{i}": f"src/f{i:02d}.test.ts" for i in range(13)}
    res = {"tests": {"ran": True, "passed": False, "command": "npx vitest run",
                     "summary": "13 failed", "failing_id_files": files}}
    (line,) = leerie._summarize_residuals(res)
    assert "src/f00.test.ts" in line and "src/f09.test.ts" in line
    assert "src/f10.test.ts" not in line
    assert line.endswith("(+3 more)")


def test_a_short_command_and_no_report_are_kept_as_they_were(leerie):
    res = {"build": {"ran": True, "passed": False,
                     "command": "pnpm run build", "summary": "exit 1"}}
    assert leerie._summarize_residuals(res) == [
        "build-failed: exit 1 — 'pnpm run build'"]


def test_an_exception_with_no_text_is_named_by_its_type(leerie):
    assert leerie._exc_text(KeyboardInterrupt()) == "KeyboardInterrupt"
    assert leerie._exc_text(OSError("disk")) == "OSError: disk"


def test_every_cleanup_failure_line_names_its_cause(leerie):
    import inspect
    src = inspect.getsource(leerie)
    assert 'cleanup failed (non-fatal): {cleanup_err}' not in src
    assert src.count('cleanup failed (non-fatal): {_exc_text(cleanup_err)}') == 5
