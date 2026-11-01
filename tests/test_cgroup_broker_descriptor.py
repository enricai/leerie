"""The cgroup broker is reachable only through the orchestrator's descriptor
(DESIGN §6 *No service of leerie's is reachable by a path*; review round 21,
isolation R3).

The broker used to listen on `/run/leerie-cgroup.sock`, mode 0666: any
confined process — a held-out evaluation in its sandbox included — could
connect and create cgroups that outlived it. These run the real
`scripts/cgroup-broker.py --spawn` chain, the real `serve()` loop and the
real orchestrator client (`_adopt_cgroup_broker`, `_cgroup_request`,
`_hand_cgroup_broker_to_exec`), on a throwaway v2-shaped directory: the
protocol and the descriptor's reach, not the kernel's cgroup semantics
(which `test_heldout_sandbox_reaches_no_broker.py` measures in a
container)."""
from __future__ import annotations

import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

BROKER = REPO_ROOT / "scripts" / "cgroup-broker.py"
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _load_broker(name: str = "cgroup_broker_descriptor"):
    spec = importlib.util.spec_from_file_location(name, BROKER)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# What the orchestrator does with what the entry handed it. Runs as the
# process `--spawn` execs, so the broker is its child, as in the container.
_ORCH = r"""
import importlib.util, json, os, socket, subprocess, sys
spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
L = importlib.util.module_from_spec(spec); sys.modules["leerie"] = L
spec.loader.exec_module(L)
out = {"env_before": os.environ.get(L._CGROUP_BROKER_FD_ENV)}
L._adopt_cgroup_broker()
fd = L._CGROUP_BROKER.fileno()
out["env_after"] = os.environ.get(L._CGROUP_BROKER_FD_ENV)
out["inheritable"] = os.get_inheritable(fd)
out["ping"] = L._cgroup_request("ping")
# What anything this process starts sees, close_fds or not.
# Every descriptor a child holds, by what it points at.
lister = "for f in /proc/$$/fd/*; do readlink \"$f\"; done"
for name, kw in (("closed", {}), ("open", {"close_fds": False})):
    r = subprocess.run(["sh", "-c", lister], capture_output=True,
                       text=True, **kw)
    out["child_fds_" + name] = r.stdout.split()
out["fd"] = os.readlink(f"/proc/self/fd/{fd}")
# The broker: this process's child, and every socket it holds unnamed.
kids = open(f"/proc/self/task/{os.getpid()}/children").read().split()
out["broker_pids"] = kids
names = {}
for line in open("/proc/net/unix").read().splitlines()[1:]:
    f = line.split()
    names[f[6]] = f[7] if len(f) > 7 else ""
held = []
for pid in kids:
    for e in os.listdir(f"/proc/{pid}/fd"):
        t = os.readlink(f"/proc/{pid}/fd/{e}")
        if t.startswith("socket:["):
            held.append(names.get(t[8:-1], "?"))
out["broker_socket_names"] = held
if len(sys.argv) > 2 and sys.argv[2] == "reexec":
    L._hand_cgroup_broker_to_exec()
    print(json.dumps(out), flush=True)
    os.execv(sys.executable, [sys.executable, "-c", sys.argv[3], sys.argv[1]])
print(json.dumps(out), flush=True)
"""

# The re-exec'd image: adopts again, as `main()` does after
# `_sleep_then_reexec`.
_AFTER_EXEC = r"""
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
L = importlib.util.module_from_spec(spec); spec.loader.exec_module(L)
L._adopt_cgroup_broker()
print(json.dumps({"after_exec_ping": L._cgroup_request("ping")}), flush=True)
"""


def _spawn_chain(tmp_path: Path, *orch_args: str) -> tuple[list[dict], str]:
    root = tmp_path / "cg"
    root.mkdir()
    env = {**os.environ, "LEERIE_CGROUP_V2_ROOT": str(root)}
    env.pop("LEERIE_CGROUP_BROKER_FD", None)
    log = tmp_path / "broker.log"
    r = subprocess.run(
        [sys.executable, str(BROKER), "--spawn", "--log", str(log), "--",
         sys.executable, "-c", _ORCH, str(LEERIE_PY), *orch_args],
        capture_output=True, text=True, env=env, timeout=60)
    assert r.returncode == 0, (r.stdout, r.stderr)
    return [json.loads(x) for x in r.stdout.splitlines() if x.startswith("{")], \
        log.read_text() if log.exists() else ""


def test_the_orchestrator_reaches_the_broker_through_its_descriptor_alone(
        tmp_path):
    (out,), log = _spawn_chain(tmp_path)
    assert out["env_before"] is not None
    assert out["ping"] == "OK", log
    # Taken before anything is started, and never told onward.
    assert out["env_after"] is None
    assert out["inheritable"] is False
    assert out["fd"].startswith("socket:[")
    assert out["fd"] not in out["child_fds_closed"]
    assert out["fd"] not in out["child_fds_open"]
    # No name to connect to: every socket the broker holds is unbound.
    assert len(out["broker_pids"]) == 1
    assert out["broker_socket_names"], "the broker holds no socket"
    assert all(n == "" for n in out["broker_socket_names"]), \
        out["broker_socket_names"]


def test_the_broker_stops_when_the_orchestrator_is_gone(tmp_path):
    (out,), _log = _spawn_chain(tmp_path)
    pid = int(out["broker_pids"][0])
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        try:
            st = Path(f"/proc/{pid}/stat").read_text().split()[2]
        except FileNotFoundError:
            break
        if st == "Z":
            break
        time.sleep(0.05)
    else:
        pytest.fail(f"broker {pid} still running after its orchestrator "
                    "exited")


def test_an_exec_of_the_orchestrator_keeps_the_broker(tmp_path):
    """`_sleep_then_reexec` replaces the process image: the new one adopts
    the same descriptor and reaches the broker."""
    outs, log = _spawn_chain(tmp_path, "reexec", _AFTER_EXEC)
    assert outs[0]["ping"] == "OK"
    assert outs[1] == {"after_exec_ping": "OK"}, log


@pytest.fixture()
def served(leerie, monkeypatch):
    """The real `serve()` on one end of a pair, the orchestrator's client on
    the other."""
    mod = _load_broker()
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    t = threading.Thread(target=mod.serve, args=(theirs,), daemon=True)
    t.start()
    ours.settimeout(5)
    monkeypatch.setattr(leerie, "_CGROUP_BROKER", ours)
    yield mod
    ours.close()
    t.join(5)
    theirs.close()
    assert not t.is_alive(), "serve() did not return once its peer closed"


def test_concurrent_requests_each_get_their_own_answer(leerie, served,
                                                        monkeypatch):
    def handle(line: str) -> str:
        return "OK " + line.split()[1]
    monkeypatch.setattr(served, "_handle", handle)
    results: dict[int, str] = {}

    def ask(i: int) -> None:
        results[i] = leerie._cgroup_request(f"stat s{i}")
    threads = [threading.Thread(target=ask, args=(i,)) for i in range(16)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert results == {i: f"OK s{i}" for i in range(16)}


def test_a_timed_out_request_leaves_nothing_for_the_next(leerie, served,
                                                         monkeypatch):
    def handle(line: str) -> str:
        if line.startswith("slow"):
            time.sleep(0.6)
        return "OK " + line.split()[0]
    monkeypatch.setattr(served, "_handle", handle)
    with pytest.raises(OSError):
        leerie._cgroup_request("slow", timeout=0.2)
    assert leerie._cgroup_request("ping", timeout=5) == "OK ping"


def test_no_descriptor_is_an_unreachable_broker(leerie, monkeypatch):
    monkeypatch.setattr(leerie, "_CGROUP_BROKER", None)
    with pytest.raises(OSError):
        leerie._cgroup_request("ping")
    monkeypatch.setattr(leerie, "_CGROUP_PROBE_RESULT", None)
    assert leerie._cgroup_probe() is False


def test_a_descriptor_that_is_not_the_brokers_is_not_used(leerie,
                                                          monkeypatch):
    r, w = os.pipe()
    try:
        monkeypatch.setattr(leerie, "_CGROUP_BROKER", None)
        monkeypatch.setenv(leerie._CGROUP_BROKER_FD_ENV, str(r))
        leerie._adopt_cgroup_broker()
        assert leerie._CGROUP_BROKER is None
        assert leerie._CGROUP_BROKER_FD_ENV not in os.environ
        os.fstat(r)   # left open, not taken over
    finally:
        os.close(r)
        os.close(w)
