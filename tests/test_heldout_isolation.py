"""Review round 20's isolation proofs and the measured design's F1, turned
round: each asserts its channel CLOSED, in a real sandbox (DESIGN §6 *A
held-out evaluation runs alone, in a world that dies with it (I3)*, *Every
confined process has its own `/tmp`, `/dev/shm` and IPC*, *No database
server leerie starts is ever unconfined*).

They need the shim's `--isolate`/`--sandbox` (`scripts/landlock_exec.py`,
with `isolation_error()`) and a host whose user namespaces may mount: a
developer host or CI with Ubuntu's
`kernel.apparmor_restrict_unprivileged_userns` cannot, and every test here
skips there, naming why. Inside the `leerie-container` profile they run.

- H1 (round 20): a held-out run left the set's text in `/dev/shm` and in a
  System V segment, where a later confined worker read it.
- H2 (round 20): a held-out run appended the set's text to a pre-existing
  `/tmp` entry, which the re-drive's AST gate — the implementer's own
  `typescript` — relayed into the committing tree.
- F1: the throwaway cluster, started unconfined, ran `COPY … TO PROGRAM`
  for a confined process that could not read `acceptance/` itself.
- A repository's fixed `/tmp/<name>` (barnacle's `/tmp/recon`) is writable
  by an isolated command and gone with it."""
from __future__ import annotations

import asyncio
import ctypes
import json
import os
import shutil
import subprocess
import sys
import textwrap
import uuid
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, _grants, pytestmark as _landlock_mark, run)

pytestmark = [_landlock_mark, pytest.mark.real_heldout_sandbox]


@pytest.fixture()
def isolated(leerie, monkeypatch):
    """Skip unless this host can build the sandbox; then every confined
    spawn is isolated, as after preflight."""
    lk = leerie._landlock()
    if not hasattr(lk, "isolation_error"):
        pytest.skip("needs the shim's --isolate/--sandbox "
                    "(scripts/landlock_exec.py, lane A)")
    why = lk.isolation_error()
    if why is not None:
        pytest.skip(f"this host cannot build a sandbox: {why}")
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _held_out(leerie, run, body: str, monkeypatch) -> bool | None:
    """Run `body` as a held-out set file through the real
    `_run_acceptance_file`: placed by the shim, never on disk."""
    monkeypatch.setattr(leerie, "_acceptance_cmd",
                        lambda _st, rels, _tree=None, placed=None:
                        f"bash {rels[0]}")
    d = run["run_dir"] / "acceptance" / "set-7"
    d.mkdir(parents=True, exist_ok=True)
    (d / "held.sh").write_text(body)
    return asyncio.run(leerie._run_acceptance_file(
        _st(run), dict(leerie.DEFAULT_CAPS), str(_view(run)), "held.sh",
        run["run_dir"] / "logs" / "acc.log", "acc",
        place=((str(d / "held.sh"), "held.sh"),)))


def test_h1_held_out_content_reaches_no_worker_via_shm_or_sysv_ipc(
        leerie, run, isolated, monkeypatch):
    nonce = uuid.uuid4().hex[:12]
    tag = f"r20i-{nonce}"
    key = 0x2000000 + int(nonce[:6], 16)
    secret = f"HELD_OUT_ASSERTION_{nonce}"
    view = _view(run)
    (view / "app.py").write_text(textwrap.dedent(f"""\
        import ctypes, shutil, sys
        src = open(sys.argv[1], "rb").read()
        shutil.copy(sys.argv[1], "/dev/shm/{tag}")
        libc = ctypes.CDLL(None, use_errno=True)
        libc.shmat.restype = ctypes.c_void_p
        sid = libc.shmget({key}, 4096, 0o1000 | 0o600)
        addr = libc.shmat(sid, None, 0)
        ctypes.memmove(addr, src[:4000], len(src[:4000]))
        libc.shmdt(ctypes.c_void_p(addr))
        """))
    v = _held_out(leerie, run,
                  f"# {secret}\npython3 app.py \"$0\"\nexit 1\n", monkeypatch)
    assert v is False
    assert not (view / "held.sh").exists()
    assert not Path(f"/dev/shm/{tag}").exists()
    libc = ctypes.CDLL(None, use_errno=True)
    assert libc.shmget(key, 0, 0) == -1
    g = _grants(leerie, run, schema_key="implementer")
    reader = textwrap.dedent(f"""\
        import ctypes
        try:
            print(open("/dev/shm/{tag}").read())
        except OSError:
            print("SHM-ABSENT")
        libc = ctypes.CDLL(None, use_errno=True)
        print("SYSV-ABSENT" if libc.shmget({key}, 0, 0) == -1 else "SYSV")
        """)
    r = subprocess.run(leerie._confined_argv(g, [sys.executable, "-c",
                                                 reader]),
                       capture_output=True, text=True,
                       env={**os.environ, **run["env"]},
                       cwd=str(run["wts"]["wt-a"]))
    assert r.returncode == 0, r.stderr
    assert secret not in r.stdout
    assert "SHM-ABSENT" in r.stdout and "SYSV-ABSENT" in r.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="no node")
def test_h2_the_ast_gate_relays_nothing_of_a_held_out_run(
        leerie, run, isolated, monkeypatch):
    staging = run["wts"]["wt-a"]
    secret = f"HELD_OUT_ASSERTION_{uuid.uuid4().hex[:12]}"
    cache = Path(f"/tmp/laneB-r20i-{uuid.uuid4().hex[:8]}")
    cache.write_text("")
    try:
        v = _held_out(leerie, run,
                      f"# {secret}\ncat \"$0\" >> {cache}\nexit 1\n",
                      monkeypatch)
        assert v is False
        assert secret not in cache.read_text()
        ts = staging / "node_modules" / "typescript"
        ts.mkdir(parents=True)
        (ts / "package.json").write_text(json.dumps({"name": "typescript",
                                                     "main": "index.js"}))
        leak = staging / "leak.txt"
        (ts / "index.js").write_text(
            "const fs = require('fs');\nlet got = 'TMP-DENIED';\n"
            f"try {{ got = fs.readFileSync({json.dumps(str(cache))}, 'utf8');"
            " } catch (e) {}\n"
            f"fs.writeFileSync({json.dumps(str(leak))}, got);\n"
            "module.exports = { createSourceFile() {} };\n")

        async def gate():
            async with leerie._exec_lock().shared():
                return leerie._redrive_ast_available(
                    staging,
                    grants=leerie._measurement_grants(_st(run), staging))
        asyncio.run(gate())
        assert secret not in leak.read_text()
    finally:
        cache.unlink(missing_ok=True)


def test_an_isolated_command_has_a_fixed_tmp_path_of_its_own(
        leerie, run, isolated):
    st, view = _st(run), _view(run)
    g = leerie._measurement_grants(st, view)
    name = f"/tmp/recon-{uuid.uuid4().hex[:8]}"

    def stream(script: str) -> tuple[int, str]:
        return asyncio.run(leerie._run_streaming(
            ["bash", "-c", script], grants=g, cwd=str(view), timeout=60,
            verbosity="quiet"))
    rc, tail = stream(f"mkdir -p {name}/graphql && exec 9>{name}/pg.lock "
                      "&& echo LOCKED")
    assert rc == 0 and "LOCKED" in tail, tail
    assert not Path(name).exists()
    rc, tail = stream(f"test -e {name} && echo SEEN || echo FRESH")
    assert "FRESH" in tail, tail


@pytest.mark.skipif(
    not (any(Path("/usr/lib/postgresql").glob("*/bin/initdb"))
         or shutil.which("initdb")),
    reason="no PostgreSQL server binaries on this host")
def test_f1_a_commands_database_runs_nothing_outside_its_grant(
        leerie, run, isolated):
    """The measured F1, turned round: the command's own server runs inside
    its confinement, so `COPY … TO PROGRAM` cannot read `acceptance/` any
    more than the command can, and it listens on no TCP address."""
    st, view = _st(run), _view(run)
    held = run["run_dir"] / "acceptance" / "set-1" / "test_held.py"
    pkg = view / "node_modules" / ".bin"
    pkg.mkdir(parents=True)
    (pkg / "prisma").write_text("#!/bin/sh\nexit 0\n")
    (pkg / "prisma").chmod(0o755)
    name = leerie._test_db_name("f1")
    leak = view / "leak.txt"
    psql = shutil.which("psql") or "psql"
    script = (
        f"{psql} \"$DATABASE_URL\" -c \"COPY (SELECT 1) TO PROGRAM "
        f"'cat {held} > {leak}'\"; "
        "cat /proc/net/tcp /proc/net/tcp6 2>/dev/null | awk '$4==\"0A\"' "
        "| wc -l")
    g = leerie._measurement_grants(st, view)
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", str(leerie.SANDBOX_DB_SCRIPT), str(view), name, "--",
         "bash", "-c", script], grants=g, cwd=str(view), timeout=120,
        verbosity="quiet"))
    assert not leak.exists() or "held" not in leak.read_text(), tail
    assert tail.strip().splitlines()[-1].strip() == "0", tail
