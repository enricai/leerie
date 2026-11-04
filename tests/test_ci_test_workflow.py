"""`.github/workflows/test.yml` bounds each test, not only the job.

The job ran the suite serially with no per-test bound, so a suite that grew
past `timeout-minutes` and a single hung test looked the same: every leg
"cancelled", no test named (runs 41732738003, 41756388905, 41777003910's
py3.10 leg at 96%). The workflow now runs the suite on `pytest-xdist` and
bounds every test with `pytest-timeout`. These pins read the workflow's raw
text (no YAML parser: pytest and its plugins are the only dev dependencies)
and then EXECUTE the run line's options, since a flag the installed plugins
do not own is a usage error that fails the job before a test runs.
"""
from __future__ import annotations

import importlib.util
import re
import shlex
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW = REPO_ROOT / ".github" / "workflows" / "test.yml"


def _text() -> str:
    return WORKFLOW.read_text()


def _run_argv() -> list[str]:
    lines = [ln.strip() for ln in _text().splitlines()
             if re.match(r"\s*run: pytest\b", ln)]
    assert len(lines) == 1, lines
    return shlex.split(lines[0][len("run: "):])


def _install_packages() -> set[str]:
    pkgs: set[str] = set()
    for ln in _text().splitlines():
        m = re.match(r"\s*pip install (?!-r)(.*)$", ln)
        if m:
            pkgs.update(shlex.split(m.group(1)))
    return pkgs


def _option(argv: list[str], name: str) -> str | None:
    for i, tok in enumerate(argv):
        if tok == name and i + 1 < len(argv):
            return argv[i + 1]
        if tok.startswith(name + "="):
            return tok.split("=", 1)[1]
    return None


def test_the_suite_runs_on_four_workers_with_the_plugin_installed():
    argv = _run_argv()
    assert _option(argv, "-n") == "4"
    assert "pytest-xdist" in _install_packages()


def test_every_test_is_bounded_below_the_job():
    """A per-test bound at or above the job's leaves a hang to the job's
    cancel again, which names no test."""
    argv = _run_argv()
    per_test = _option(argv, "--timeout")
    assert per_test is not None and int(per_test) > 0
    assert "pytest-timeout" in _install_packages()
    job = re.search(r"^\s*timeout-minutes:\s*(\d+)", _text(), re.M)
    assert job is not None
    assert int(per_test) < int(job.group(1)) * 60


@pytest.mark.skipif(
    importlib.util.find_spec("xdist") is None
    or importlib.util.find_spec("pytest_timeout") is None,
    reason="pytest-xdist and pytest-timeout are not installed here")
def test_the_run_lines_options_are_accepted(tmp_path):
    """Execute, don't read: the run line's own options, minus coverage (a
    CI-only plugin), against a one-test directory. An option no installed
    plugin owns exits 4 before collection."""
    argv = _run_argv()
    opts: list[str] = []
    it = iter(argv[1:])
    for tok in it:
        if tok == "tests/" or tok.startswith("--cov"):
            continue
        opts.append(tok)
        if tok == "-n":
            opts.append(next(it))
    (tmp_path / "test_one.py").write_text("def test_one():\n    pass\n")
    r = subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *opts,
         "-o", "addopts=", str(tmp_path / "test_one.py")],
        cwd=tmp_path, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1 passed" in r.stdout
