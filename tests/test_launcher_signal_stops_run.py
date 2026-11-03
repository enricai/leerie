"""DESIGN §6 *container boundary's hidden precondition*, mechanism 2: a TERM
or INT to the launcher stops the run promptly (review round 27 O1).

bash runs a trap only once the foreground command returns. With
`nerdctl run` in the foreground, `kill -TERM <launcher>` waited for the
whole run (354 s on a real one) before the trap killed anything. The
launcher now runs the client in the background and `wait`s for it.

Real launcher, stub `nerdctl` whose container runs for minutes: the signal
ends the launcher within seconds with 128+signal, the container is killed
(`nerdctl kill`), the client has exited, and the EXIT trap's clean-up ran.

Review round 28 B1: a TERM after nerdctl wrote the cidfile (container
created) but before the task started found `nerdctl kill` failing and, the
cidfile being non-empty, left the client unsignalled; the launcher waited
for the whole run (24.3 s on the reviewer's 2 s window and 20 s run).
Inverted with the stub's created window.
"""
from __future__ import annotations

import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.launcher_stub_run import launch, make_world, marked_pids, reap

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")

CONTAINER_SECONDS = "300"
PROMPT_S = 20.0


@pytest.mark.parametrize("sig,rc", [(signal.SIGTERM, 143),
                                    (signal.SIGINT, 130)])
def test_a_signal_stops_the_run_promptly(tmp_path: Path, sig, rc):
    w = make_world(tmp_path)
    try:
        proc = launch(w, ["--no-push", "a task"],
                      env_extra={"STUB_RUN_SECONDS": CONTAINER_SECONDS},
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL)
        pidfile = w["world"] / "container.pid"
        deadline = time.monotonic() + 60
        while not pidfile.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "launcher exited before the run"
            time.sleep(0.05)
        assert pidfile.exists(), "the run never started"
        container = int(pidfile.read_text())
        time.sleep(0.3)
        sent = time.monotonic()
        proc.send_signal(sig)
        try:
            got = proc.wait(timeout=PROMPT_S)
        except subprocess.TimeoutExpired:
            pytest.fail(f"the launcher was still running {PROMPT_S}s after "
                        f"{sig.name}: its trap waits for the run to finish")
        took = time.monotonic() - sent
        assert got == rc, got
        assert took < PROMPT_S
        calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
        assert "--namespace leerie kill stubcid" in calls, calls
        # The container and its client are gone, not left behind.
        assert container not in marked_pids(w["mark"])
        assert marked_pids(w["mark"]) == []
        # The EXIT trap ran after the signal trap: the cidfile is removed.
        assert not list(w["state"].glob(".cidfile-*"))
    finally:
        reap(w["mark"])


def test_a_signal_in_the_created_window_stops_the_run_promptly(
        tmp_path: Path):
    w = make_world(tmp_path)
    state = w["world"] / "container.state"
    try:
        proc = launch(w, ["--no-push", "a task"],
                      env_extra={"STUB_RUN_SECONDS": CONTAINER_SECONDS,
                                 "STUB_CREATED_SECONDS": "3"},
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL)
        deadline = time.monotonic() + 60
        while not state.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "launcher exited before the run"
            time.sleep(0.05)
        assert state.read_text().strip() == "created"
        sent = time.monotonic()
        proc.send_signal(signal.SIGTERM)
        try:
            got = proc.wait(timeout=PROMPT_S)
        except subprocess.TimeoutExpired:
            pytest.fail("the launcher waited for the run after a TERM in "
                        "the created window")
        assert got == 143, got
        assert time.monotonic() - sent < 5.0
        # The client was stopped before it started the container.
        time.sleep(3.5)
        assert state.read_text().strip() == "created"
        assert not (w["world"] / "container.pid").exists()
    finally:
        reap(w["mark"])
