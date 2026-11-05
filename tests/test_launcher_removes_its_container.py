"""A container a launch starts never outlives that launch (DESIGN §6 *A
container a launch starts never outlives that launch*).

The operator's host, 2026-11-05: seven running launches Ctrl-C'd at once
left four containers `Created` in leerie's namespace, their launchers dead,
two on images of 0.44.11 and one of 0.44.10. The next launches' image
reaper kept those versions' images ("a container still uses"), about 15 GB,
for good. nerdctl's `--rm` is done by the client after the container exits,
and the client died with the launcher; the launcher's EXIT trap killed the
container but never removed it.

The real launcher, against the harness's stub `nerdctl`, whose container
store models the client-side `--rm`: done only by a client still alive once
its container exits.
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.launcher_stub_run import (LAUNCHER, containers, launch, make_world,
                                     marked_pids, reap, run_argv, run_to_end,
                                     seed_container)

pytestmark = pytest.mark.skipif(not sys.platform.startswith("linux"),
                                reason="reads /proc")

PROMPT_S = 30.0


def _own_name(w: dict) -> str:
    argv = run_argv(w)
    return argv[argv.index("--name") + 1]


def _dead_pid() -> int:
    """A PID no process has: a child that exited and was reaped."""
    p = subprocess.Popen(["/bin/sh", "-c", "exit 0"])
    p.wait()
    assert not os.path.exists(f"/proc/{p.pid}")
    return p.pid


def _left_after(w: dict, grace: float) -> dict[int, str]:
    """The marked processes still alive `grace` seconds on, by command line
    (a process on its way out is not one left behind)."""
    deadline = time.monotonic() + grace
    while marked_pids(w["mark"]) and time.monotonic() < deadline:
        time.sleep(0.1)
    out = {}
    for pid in marked_pids(w["mark"]):
        try:
            out[pid] = Path(f"/proc/{pid}/cmdline").read_bytes().replace(
                b"\0", b" ").decode(errors="replace")
        except OSError:
            pass
    return out


@pytest.mark.parametrize("sig,rc", [(signal.SIGINT, 130),
                                    (signal.SIGTERM, 143),
                                    (signal.SIGHUP, 129)])
def test_a_signalled_launch_leaves_no_container(tmp_path: Path, sig, rc):
    """The launcher's trap stops the client before the client's `--rm`
    (STUB_RM_LAG holds the client between its container's exit and its
    `--rm`, where the trap's TERM finds it, as Ctrl-C finds a real one), so
    only the launcher can remove the container. Falsified: the EXIT trap's
    `_remove_launch_container` dropped (all three keep an `exited`
    container); the HUP trap dropped (bash still runs the EXIT trap, which
    removes the container, but nothing stops the client, which is left
    running)."""
    w = make_world(tmp_path)
    try:
        proc = launch(w, ["--no-push", "a task"],
                      env_extra={"STUB_RUN_SECONDS": "300",
                                 "STUB_RM_LAG": "60"},
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                      stdin=subprocess.DEVNULL)
        pidfile = w["world"] / "container.pid"
        deadline = time.monotonic() + 60
        while not pidfile.exists() and time.monotonic() < deadline:
            assert proc.poll() is None, "launcher exited before the run"
            time.sleep(0.05)
        assert pidfile.exists(), "the run never started"
        name = _own_name(w)
        assert containers(w) == {name: "running"}
        time.sleep(0.3)
        proc.send_signal(sig)
        assert proc.wait(timeout=PROMPT_S) == rc
        assert containers(w) == {}, containers(w)
        calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
        assert "--namespace leerie rm -f stubcid" in calls, calls
        left = _left_after(w, 5.0)
        assert left == {}, left
    finally:
        reap(w["mark"])


@pytest.mark.parametrize("client_rm", ["1", "0"])
def test_a_launch_that_ends_leaves_no_container(tmp_path: Path, client_rm):
    """A run that ends: the client's `--rm` removes the container, and the
    launcher's removal finds nothing; a client whose `--rm` did not happen
    (STUB_CLIENT_RM=0) leaves it to the launcher, which removes it by the
    ID in the cidfile. Falsified: `_remove_launch_container` dropped from
    the EXIT trap (the `0` case keeps an `exited` container)."""
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "a task"],
                         env_extra={"STUB_CLIENT_RM": client_rm})
    assert rc == 0, out
    assert run_argv(w), out
    assert containers(w) == {}, containers(w)
    calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
    assert "--namespace leerie rm -f stubcid" in calls, calls


def test_the_exit_trap_removes_before_the_cidfile_goes(tmp_path: Path):
    """No ID in the cidfile (a client stopped before nerdctl wrote it): the
    launcher removes its container by the name it gave it. And the EXIT
    trap removes the container after its kill and before the cidfile, the
    ID it removes by, is deleted; the bring-back, which reads the state
    root and never the container, follows. Falsified: the name fallback
    dropped (`rm -f` never names the container)."""
    src = LAUNCHER.read_text()
    exit_trap = [ln for ln in src.splitlines()
                 if ln.strip().startswith("trap '") and ln.endswith("' EXIT")
                 and "_remove_launch_container" in ln]
    assert len(exit_trap) == 1
    t = exit_trap[0]
    assert (t.index("_kill_container_from_cidfile")
            < t.index("_remove_launch_container")
            < t.index("_bring_back_ran_run")
            < t.index('rm -f "${_cidfile:-}"')), t
    start = src.index("  _remove_launch_container() {\n")
    fn = src[start:src.index("\n  }\n", start) + len("\n  }\n")]
    stub = tmp_path / "nerdctl"
    calls = tmp_path / "calls"
    stub.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{calls}"\n')
    stub.chmod(0o755)
    for cid_content, want in (("", "leerie-1793883015-4242"),
                              ("abc123\n", "abc123")):
        cidfile = tmp_path / "cidfile"
        cidfile.write_text(cid_content)
        calls.write_text("")
        r = subprocess.run(
            ["bash", "-c", "set -euo pipefail\nLEERIE_IMAGE_NS=leerie\n"
             f'_cidfile="{cidfile}"\n_container_name=leerie-1793883015-4242\n'
             f'nerdctl() {{ "{stub}" "$@"; }}\n' + fn
             + "_remove_launch_container\n"],
            capture_output=True, text=True, timeout=30)
        assert r.returncode == 0, r.stderr
        assert calls.read_text().splitlines() == [
            f"--namespace leerie rm -f {want}"], calls.read_text()


def test_finalize_still_reads_the_run_the_container_ran(tmp_path: Path):
    """The post-exit steps read the run-id file the container wrote, and
    the container is gone at the launcher's end: removing it reads nothing
    the steps after the container's exit need (here the run fails, and the
    skip line names the run the file named)."""
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "a task"],
                         env_extra={"STUB_RUN_ID": "stubrun01",
                                    "STUB_RUN_RC": "3",
                                    "STUB_CLIENT_RM": "0"})
    assert run_argv(w), out
    assert ("finalize: skipped — container exited with code 3 "
            "(run stubrun01)") in out, out
    assert rc == 3, out
    assert containers(w) == {}, containers(w)


def test_the_next_launch_removes_only_what_a_dead_launch_left(tmp_path: Path):
    """SIGKILL runs no trap. The next launch removes each container a dead
    launch left: leerie's own name `leerie-<ts>-<pid>`, its
    `leerie.launcher_pid` label that same `<pid>`, `Created` or `Exited`,
    and that launcher dead. Every other container stays: one running or
    paused, one whose launcher lives, one not named as leerie names its
    containers, one with no label or another PID in it. Falsified, one at a
    time: the sweep's call removed (both residues stay); its status check
    dropped (`rm` is called on the running and the paused one, which the
    stub, as nerdctl, refuses without `-f`); its liveness check dropped
    (the live launch's goes); its label check dropped (the unlabelled one
    and the one labelled with another PID go); its digits check dropped
    (`leerie-x1-<dead>` goes)."""
    w = make_world(tmp_path)
    dead, dead2 = _dead_pid(), _dead_pid()
    live = os.getpid()
    gone = {f"leerie-1793883015-{dead}": "created",
            f"leerie-1793880586-{dead}": "exited"}
    kept = {f"leerie-1793845709-{dead}": "running",
            f"leerie-1793836494-{dead}": "paused",
            f"leerie-1793836495-{live}": "created",
            "leerie-web": "exited",
            f"postgres-{dead}": "exited",
            f"leerie-1793836496-{dead2}": "exited",
            f"leerie-x1-{dead}": "exited"}
    for n, st in gone.items():
        seed_container(w, n, st, str(dead))
    for n, st in kept.items():
        pid = str(live) if n.endswith(f"-{live}") else str(dead)
        seed_container(w, n, st, pid)
    seed_container(w, f"leerie-1793845710-{dead}", "exited", "")
    kept[f"leerie-1793845710-{dead}"] = "exited"
    rc, out = run_to_end(w, ["--no-push", "a task"])
    assert rc == 0, out
    after = containers(w)
    assert after == kept, after
    for n in gone:
        assert n in out, out
    calls = (w["world"] / "nerdctl.calls").read_text().splitlines()
    # Never forced: a container that started meanwhile is refused.
    assert [c for c in calls if " rm " in f" {c} " and "stubcid" not in c] \
        == [f"--namespace leerie rm {n}" for n in gone], calls
    # Before the run: the sweep is the launch's, not its exit's.
    first_rm = next(i for i, c in enumerate(calls) if c.startswith("--namespace leerie rm leerie-"))
    first_run = next(i for i, c in enumerate(calls) if c.startswith("--namespace leerie run "))
    assert first_rm < first_run


def test_a_dead_launchs_container_no_longer_holds_its_image(tmp_path: Path):
    """The leak's cost: a dead launch's container keeps an older version's
    image in use, and the image reaper keeps it. The sweep runs before the
    reaper, so the image goes in the same launch. Falsified: the sweep
    called after `leerie_reap_old_images` (the image is kept)."""
    from tests.test_old_images_reaped import _store, _world
    dead = _dead_pid()
    old = "leerie-repo/acme-web-0123456789ab:0.0.1"
    w = _world(tmp_path, [old], inuse=[old])
    seed_container(w, f"leerie-1793883015-{dead}", "created", str(dead), old)
    rc, out = run_to_end(w, ["--no-push", "a task"])
    assert rc == 0, out
    assert containers(w) == {}, containers(w)
    assert old not in _store(w), out
