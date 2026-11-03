"""Everything after `--` is positional (IMPLEMENTATION §0, review round 27
O3).

`leerie --no-push -- "-task"` failed with "unrecognized arguments:
--no-push": the launcher appended the `--no-push` it resolved after the
operator's `--`, where argparse reads it as a second positional. And the
launcher's own flag scans read past `--`, so a task spelled like one of its
flags was taken as that flag.

Real launcher, stub `nerdctl`: the argv the container gets has every flag
before `--` and the operator's arguments after it, untouched and last.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.launcher_stub_run import LAUNCHER, make_world, run_argv, run_to_end

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")


def _orchestrator_args(argv: list[str]) -> list[str]:
    """What the orchestrator is given: the argv after the image tag."""
    i = next(k for k, a in enumerate(argv) if a.startswith("leerie:"))
    return argv[i + 1:]


def _run(w: dict, args: list[str]) -> list[str]:
    rc, out = run_to_end(w, args)
    assert rc == 0, out
    return _orchestrator_args(run_argv(w))


@pytest.mark.parametrize("task", ["-task", "--no-push", "--state-dir"])
def test_after_double_dash_is_the_task_and_flags_come_before(tmp_path, task):
    w = make_world(tmp_path)
    args = _run(w, ["--no-push", "--", task])
    assert args[-2:] == ["--", task], args
    assert args.count("--") == 1
    assert "--no-push" in args[:args.index("--")]


def test_a_flag_spelled_task_after_double_dash_is_not_the_flag(tmp_path):
    """`--no-push` after `--` is the task, not the launcher's push choice:
    nothing hands the container a push opt-out the operator did not give."""
    w = make_world(tmp_path)
    # Pushing needs an origin: the push preflight runs, as it should.
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "-C", str(w["repo"]), "remote", "add", "origin",
                    str(origin)], check=True)
    subprocess.run(["git", "-C", str(w["repo"]), "push", "-q", "origin",
                    "HEAD"], check=True)
    args = _run(w, ["--", "--no-push"])
    assert args[-2:] == ["--", "--no-push"], args
    assert "--no-push" not in args[:args.index("--")], args


def test_every_launcher_flag_scan_stops_at_double_dash():
    """The rule, over the source: every top-level scan of "$@" stops at
    `--` (or, for the task extraction and the REWRITTEN_ARGS pass, handles
    it), so none reads an operator's positional as a flag."""
    lines = LAUNCHER.read_text().splitlines()
    loops = [i for i, ln in enumerate(lines) if ln == 'for arg in "$@"; do']
    assert len(loops) >= 15
    bad = []
    for i in loops:
        body = "\n".join(lines[i + 1:i + 14])
        if not re.search(r'\[ "\$arg" = "--" \]', body):
            bad.append(f"leerie:{i + 1}")
    assert not bad, f"flag scans that read past `--`: {bad}"
