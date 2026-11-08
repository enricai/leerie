"""A launch's host-side work starts when its run has ended, not when the
container runtime says the container exited (DESIGN §6 *A launch's host-side
work starts when its run has ended*).

Run a41a09fd, 2026-11-07, host load 110-175: the orchestrator recorded its
end (`orchestrator.exit_code`, 0) at 22:56:12 and exited. 55 s later the
runtime's shim stopped answering containerd (`get state ... context deadline
exceeded`, 144 times), `nerdctl ps` listed the container `Unknown`, and the
launcher's `nerdctl run --rm -i` client sat in a futex wait for 42 minutes,
until the shim went away. The client then returned non-zero, so the
launcher skipped the push and the PR of a run that had ended done.

The real launcher, against the harness's stub `nerdctl` and, with
`STUB_SHIM=1`, a stub shim: the parent of the container's init, which the
launcher finds in the host's /proc as it finds the real one
(`tests/launcher_stub_run.py`, `_SHIM`).
"""
from __future__ import annotations

import json
import signal
import subprocess
import sys
import time
import uuid
from pathlib import Path

import pytest

from tests.conftest import HAS_JQ
from tests.launcher_stub_run import launch, make_world, reap

pytestmark = [
    pytest.mark.skipif(not sys.platform.startswith("linux"),
                       reason="reads /proc"),
    pytest.mark.skipif(not HAS_JQ, reason="the local finalize reads run.json "
                       "with jq, which the container image lacks"),
]

RID = "run-ended-0001"
GRACE = "2"


def _world(tmp_path: Path) -> tuple[dict, Path]:
    """A world with an `origin`, whose state root holds the run the container
    runs: finished and already pushed, so a finalize that reaches it says so
    and pushes nothing."""
    w = make_world(tmp_path)
    origin = w["world"] / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(origin)], check=True)
    subprocess.run(["git", "-C", str(w["repo"]), "remote", "add", "origin",
                    str(origin)], check=True)
    run_dir = w["state"] / "runs" / RID
    run_dir.mkdir(parents=True)
    (run_dir / "run.json").write_text(json.dumps({
        "run_id": RID, "branch": f"leerie/runs/{RID}",
        "working_branch": "main", "finished_at": "2026-11-07T22:55:43Z",
        "pushed_at": "2026-11-07T22:56:30Z"}))
    return w, run_dir


def _env(run_dir: Path, **extra: str) -> dict:
    env = {"STUB_SHIM": "1", "STUB_RUN_ID": RID,
           "STUB_END_FILE": str(run_dir / "orchestrator.exit_code"),
           "LEERIE_RUN_END_GRACE_S": GRACE}
    env.update(extra)
    return env


def _start(w: dict, env: dict) -> tuple[subprocess.Popen, Path]:
    log = w["world"] / f"launch-{uuid.uuid4().hex}.log"
    with open(log, "wb") as f:
        p = launch(w, ["a task"], env_extra=env, stdout=f,
                   stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    return p, log


def _init_pid(w: dict, timeout: float = 30.0) -> int:
    f = w["world"] / "container.pid"
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if f.exists() and f.read_text().strip():
            return int(f.read_text())
        time.sleep(0.05)
    raise AssertionError("the container never started")


def _alive(pid: int) -> bool:
    """Running, not a zombie."""
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat.rsplit(")", 1)[1].split()[0] != "Z"


@pytest.mark.parametrize("reap_init", ["1", "0"])
def test_an_ended_run_is_finished_though_the_runtime_never_answers(
        tmp_path: Path, reap_init: str):
    """The run records its end; the shim never exits, so the client never
    returns, and the runtime's `rm -f` never returns either. The launcher
    stops waiting once the grace has run out and no process of the
    container is left (an init the shim reaped, or one left a zombie), does
    its finalize with the run's code, and names the container it could not
    remove. Falsified: `_wait_run` back to a bare `wait` (the launcher is
    still waiting at the deadline); the zombie arm of
    `_launch_container_is_empty` dropped (the `0` case waits for good);
    `_remove_launch_container`'s bound dropped (the EXIT trap waits on the
    hung `rm -f`)."""
    w, run_dir = _world(tmp_path)
    try:
        p, log = _start(w, _env(run_dir, STUB_RUN_SECONDS="1",
                                STUB_END_CODE="0", STUB_SHIM_HANG="1",
                                STUB_SHIM_REAP=reap_init, STUB_RM_HANG="1"))
        try:
            rc = p.wait(timeout=60)
        except subprocess.TimeoutExpired:
            raise AssertionError("the launcher is still waiting on the "
                                 "runtime:\n" + log.read_text())
        out = log.read_text(errors="replace")
        assert rc == 0, out
        assert "no process of its container" in out, out
        assert "finishing from the run directory" in out, out
        # The finalize ran, for this run, with the run's code: a skip would
        # say "finalize: skipped".
        assert f"finalize: run {RID} is already pushed" in out, out
        assert "finalize: skipped" not in out, out
        assert ("could not remove container" in out
                and "nerdctl --namespace leerie rm -f stubcid" in out), out
        calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
        # Its processes are gone: no kill waits on the runtime.
        assert not any(c.startswith("--namespace leerie kill") for c in calls), calls
    finally:
        reap(w["mark"])


@pytest.mark.parametrize("run_seconds", ["300", "1"])
def test_a_run_that_has_not_ended_is_waited_on(tmp_path: Path,
                                               run_seconds: str):
    """No end recorded: the launcher waits on the client however long it
    takes, the grace long past, and a TERM still stops it at once. Both
    while the container still runs (`300`) and once it has ended without a
    record (`1`: a crash, whose container is empty and whose runtime does
    not answer), which keeps the old behaviour. Falsified: `_wait_run`
    starting the grace without the run's end record (the `1` case
    finishes)."""
    w, run_dir = _world(tmp_path)
    try:
        p, log = _start(w, _env(run_dir, STUB_RUN_SECONDS=run_seconds,
                                STUB_SHIM_HANG="1"))
        init = _init_pid(w)
        time.sleep(int(GRACE) + 6)
        assert p.poll() is None, log.read_text()
        assert _alive(init) == (run_seconds == "300")
        assert "finishing from the run directory" not in log.read_text()
        p.send_signal(signal.SIGTERM)
        assert p.wait(timeout=45) == 143, log.read_text()
    finally:
        reap(w["mark"])


def test_an_ended_run_whose_container_still_runs_is_waited_on(
        tmp_path: Path):
    """The run records its end, but the container's processes go on (its
    init lives on after the record): past the grace the launcher still
    waits, says why, and finishes only once they are gone. Falsified:
    `_launch_container_is_empty` returning success without reading the
    shim's children (the launcher finishes while the init still runs)."""
    w, run_dir = _world(tmp_path)
    try:
        p, log = _start(w, _env(run_dir, STUB_RUN_SECONDS="1",
                                STUB_END_CODE="0", STUB_LINGER="10",
                                STUB_SHIM_HANG="1", STUB_RM_HANG="1"))
        init = _init_pid(w)
        deadline = time.monotonic() + 30
        while (not (run_dir / "orchestrator.exit_code").exists()
               and time.monotonic() < deadline):
            time.sleep(0.1)
        time.sleep(int(GRACE) + 4)
        out = log.read_text(errors="replace")
        assert _alive(init), "the stub's init ended early"
        assert p.poll() is None, out
        assert "cannot show that none of the container's processes" in out, out
        assert "finishing from the run directory" not in out, out
        rc = p.wait(timeout=60)
        assert not _alive(init)
        out = log.read_text(errors="replace")
        assert rc == 0, out
        assert f"finalize: run {RID} is already pushed" in out, out
    finally:
        reap(w["mark"])


@pytest.mark.parametrize("end_code,client_rc,rc,line", [
    ("0", "255", 0, f"finalize: run {RID} is already pushed"),
    ("", "3", 3, "finalize: skipped — container exited with code 3"),
])
def test_the_run_finishes_with_its_own_code(tmp_path: Path, end_code,
                                            client_rc, rc, line):
    """A runtime that does answer, with a code that is not the run's (the
    client returned non-zero once the shim went away, for a run that
    ended 0): the launch finishes with the run's recorded code. With no
    record (a crash), the runtime's code stands. Falsified: the override
    after the three run forms dropped (the `0` case is skipped)."""
    w, run_dir = _world(tmp_path)
    try:
        env = _env(run_dir, STUB_RUN_SECONDS="1", STUB_RUN_RC=client_rc)
        if end_code:
            env["STUB_END_CODE"] = end_code
        p, log = _start(w, env)
        got = p.wait(timeout=60)
        out = log.read_text(errors="replace")
        assert got == rc, out
        assert line in out, out
    finally:
        reap(w["mark"])
