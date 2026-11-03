"""DESIGN §6 *Nothing the launcher starts outlives it or keeps its output
open* (review round 26 M1).

The `$STAGE` keepalive's `sleep 3600` inherited the launcher's stdout and
stderr, and the EXIT trap killed only the keepalive's shell. The `sleep`
outlived the launcher, holding the pipe, so a reader of leerie's output
(`leerie … | tee`, `$(leerie …)`) waited up to an hour after leerie exited.

Real launcher, stub `nerdctl`, stdout on a pipe: the reader gets EOF within
seconds of the launcher's exit, and no process the launch started is left.
"""
from __future__ import annotations

import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import pytest

from tests.launcher_stub_run import launch, make_world, marked_pids, reap

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")

EOF_CEILING_S = 15.0


def test_the_output_pipe_closes_when_the_launcher_exits(tmp_path: Path):
    w = make_world(tmp_path)
    try:
        proc = launch(w, ["--no-push", "a task"], stdout=subprocess.PIPE,
                      stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        out: list[bytes] = []
        reader = threading.Thread(target=lambda: out.append(proc.stdout.read()),
                                  daemon=True)
        reader.start()
        rc = proc.wait(timeout=120)
        exited = time.monotonic()
        reader.join(timeout=EOF_CEILING_S)
        held = reader.is_alive()
        left = marked_pids(w["mark"])
        assert rc == 0, out
        assert not held, (
            f"the launcher exited but its stdout stayed open for over "
            f"{EOF_CEILING_S}s; still holding it: {left}")
        assert time.monotonic() - exited < EOF_CEILING_S
        # Substance: the run really reached the container, so the keepalive
        # really was started (it is created before staging).
        assert (w["world"] / "run.argv").exists()
        assert left == [], f"processes outlived the launcher: {left}"
    finally:
        reap(w["mark"])


def test_the_keepalive_ends_its_sleep_when_stopped(tmp_path: Path):
    """The keepalive as shipped, stopped the way the EXIT trap stops it:
    TERM to the subshell. Its `sleep` must go with it."""
    src = (Path(__file__).resolve().parent.parent / "leerie").read_text()
    start = src.index("_stage_keepalive() {\n")
    end = src.index("\n}\n", start) + 3
    stage = tmp_path / "stage"
    stage.mkdir()
    script = (
        "set -euo pipefail\n"
        f'STAGE="{stage}"\n' + src[start:end]
        + '( _stage_keepalive "$$" ) </dev/null >/dev/null 2>&1 &\n'
        "k=$!\n"
        # Until the keepalive has started its sleep.
        'until [ -n "$(ps -o pid= --ppid "$k")" ]; do sleep 0.05; done\n'
        'echo "SLEEP $(ps -o pid= --ppid "$k" | tr -d " ")"\n'
        'kill "$k"; wait "$k" || true\n')
    mark = uuid.uuid4().hex
    try:
        r = subprocess.run(["bash", "-c", script], capture_output=True,
                           text=True, timeout=60,
                           env={"PATH": "/usr/bin:/bin",
                                "LEERIE_TEST_MARK": mark})
        assert r.returncode == 0, r.stderr
        sleep_pid = int(r.stdout.split("SLEEP ")[1].split()[0])
        deadline = time.monotonic() + 5
        while sleep_pid in marked_pids(mark) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert sleep_pid not in marked_pids(mark), (
            "the keepalive's sleep outlived the keepalive")
    finally:
        reap(mark)
