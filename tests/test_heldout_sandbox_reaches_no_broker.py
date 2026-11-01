"""A held-out evaluation cannot reach the cgroup broker (DESIGN §6 *No
service of leerie's is reachable by a path*; review round 21, isolation R3).

The broker runs as the container's root, outside every namespace the
sandbox makes. It listened on `/run/leerie-cgroup.sock`, mode 0666, which
the sandbox reached through the filesystem: the evaluated code sent
`create <the set's text, hex>`, the cgroup outlived the sandbox, and a
confined implementer read its name from `/sys/fs/cgroup`. The broker now
serves only the descriptor the orchestrator holds, which nothing it starts
inherits.

Needs a real sandbox and a real broker started by `cgroup-broker.py
--spawn` with this process as the orchestrator it hands the descriptor to,
its v2 root at `$R21_CG_SLICE` (lane E's container harness does that as
container root, then drops to leerie the way `container-entry.sh` does);
skips otherwise."""
from __future__ import annotations

import asyncio
import os
import subprocess
import uuid
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _grants, pytestmark as _landlock_mark, run)

pytestmark = [_landlock_mark, pytest.mark.real_heldout_sandbox]
OLD_PATH = "/run/leerie-cgroup.sock"


@pytest.fixture()
def broker(leerie, monkeypatch):
    lk = leerie._landlock()
    why = lk.isolation_error() if hasattr(lk, "isolation_error") else "old"
    if why is not None:
        pytest.skip(f"this host cannot build a sandbox: {why}")
    if not os.environ.get("R21_CG_SLICE"):
        pytest.skip("no cgroup broker handed to this process")
    if leerie._CGROUP_BROKER is None:
        leerie._adopt_cgroup_broker()
    if leerie._CGROUP_BROKER is None:
        pytest.skip("no cgroup broker descriptor")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")


def test_heldout_code_cannot_reach_the_broker(leerie, run, broker,
                                              monkeypatch):
    st = _St(run["user"], run["run_dir"])
    view = run["wts"][f"{run['run_dir'].name}-view-staging"]
    secret = f"HELDOUT{uuid.uuid4().hex[:10]}"
    hexed = secret.encode().hex()
    # The orchestrator still reaches it, through its descriptor.
    own = f"r21i-own-{uuid.uuid4().hex[:8]}"
    assert leerie._cgroup_create(own, 256 << 20, 64) == own
    leerie._cgroup_destroy(own)
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda _st, rels, _tree=None, placed=None:
                        f"python3 {rels[0]}")
    d = run["run_dir"] / "acceptance" / "set-7"
    d.mkdir(parents=True, exist_ok=True)
    # The evaluated code: every way it has to the broker — the old path, and
    # any socket it holds.
    (d / "held.py").write_text(
        f"# {secret}\n"
        "import os, socket\n"
        f"req = b'create r21i-{hexed} 0 0\\n'\n"
        "try:\n"
        "    s = socket.socket(socket.AF_UNIX); s.settimeout(2)\n"
        f"    s.connect({OLD_PATH!r}); s.sendall(req)\n"
        "    print('PATH', s.recv(99))\n"
        "except OSError as e:\n"
        "    print('PATH refused', type(e).__name__)\n"
        "for fd in os.listdir('/proc/self/fd'):\n"
        "    t = os.readlink(f'/proc/self/fd/{fd}') if os.path.exists(\n"
        "        f'/proc/self/fd/{fd}') else ''\n"
        "    if t.startswith('socket:'):\n"
        "        a, b = socket.socketpair()\n"
        "        try:\n"
        "            socket.send_fds(socket.socket(fileno=int(fd)), [req],\n"
        "                            [b.fileno()])\n"
        "            a.settimeout(2); print('FD', fd, a.recv(99))\n"
        "        except OSError as e:\n"
        "            print('FD', fd, 'refused', type(e).__name__)\n"
        "print('SCANNED')\n"
        "raise SystemExit(1)\n")
    log = run["run_dir"] / "logs" / "acc.log"
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(view), "held.py", log, "acc",
        place=((str(d / "held.py"), "held.py"),)))
    assert v is False
    out = log.read_text()
    assert "SCANNED" in out, out[-2000:]          # the code ran
    assert "PATH refused" in out, out[-2000:]
    assert all("refused" in ln for ln in out.splitlines()
               if ln.startswith("FD ")), out[-2000:]
    # After the sandbox is gone: a confined implementer lists the slice.
    slice_dir = Path(os.environ["R21_CG_SLICE"]) / "leerie.slice"
    gi = _grants(leerie, run, schema_key="implementer")
    r = subprocess.run(
        leerie._confined_argv(gi, ["bash", "-c", f"ls {slice_dir}"]),
        capture_output=True, text=True, env={**os.environ, **run["env"]},
        cwd=str(run["wts"]["wt-a"]))
    assert r.returncode == 0, r.stderr
    assert not [n for n in r.stdout.split() if n.startswith("leerie-w-r21i-")
                and n != f"leerie-w-{own}"], r.stdout


def test_no_confined_process_holds_the_descriptor(leerie, run, broker):
    """A worker started through the shim, isolated, holds no socket of the
    orchestrator's: it cannot even name the broker."""
    sock = os.readlink(f"/proc/self/fd/{leerie._CGROUP_BROKER.fileno()}")
    gi = _grants(leerie, run, schema_key="implementer")
    r = subprocess.run(
        leerie._confined_argv(gi, ["bash", "-c",
                                   'for f in /proc/$$/fd/*; do '
                                   'readlink "$f"; done; true']),
        capture_output=True, text=True, env={**os.environ, **run["env"]},
        cwd=str(run["wts"]["wt-a"]), close_fds=False)
    assert r.returncode == 0, r.stderr
    assert sock.startswith("socket:[")
    assert sock not in r.stdout.split()
    assert not os.path.exists(OLD_PATH)
