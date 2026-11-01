#!/usr/bin/env python3
"""Preflight's probe, run inside a held-out sandbox by
`_require_heldout_sandbox` (DESIGN §6 *A held-out evaluation runs alone, in a
world that dies with it (I3)*). Writes `report.json` in its working directory
(the overlaid view) with each property the orchestrator checks.

Usage: sandbox_probe.py RO_FILE PLACED_REL TMP_MARK SHM_MARK

Stdlib only; it never decides anything, it reports what it observed."""
from __future__ import annotations

import ctypes
import errno
import json
import os
import socket
import sys

_ADD_KEY = {"x86_64": 248, "aarch64": 217}
_KEY_SPEC_SESSION_KEYRING = -3


def _add_key() -> bool | str:
    nr = _ADD_KEY.get(os.uname().machine)
    if nr is None:
        return "no syscall number"
    libc = ctypes.CDLL(None, use_errno=True)
    libc.syscall.restype = ctypes.c_long
    rc = libc.syscall(ctypes.c_long(nr), b"user", b"leerie-probe", b"x",
                      ctypes.c_size_t(1), ctypes.c_long(_KEY_SPEC_SESSION_KEYRING))
    if rc >= 0:
        return "added"
    e = ctypes.get_errno()
    return True if e == errno.EPERM else errno.errorcode.get(e, str(e))


def _erofs(path: str) -> bool | str:
    try:
        os.utime(path, (1234567890, 1234567890))
    except OSError as e:
        return True if e.errno == errno.EROFS else (
            errno.errorcode.get(e.errno or 0, str(e)))
    return "changed"


def main(argv: list[str]) -> int:
    ro_file, rel, tmp_mark, shm_mark = argv
    try:
        with open(rel, encoding="utf-8") as f:
            placed = f.read() == "held-out\n"
    except OSError:
        placed = False
    report = {
        "ipc_ns": os.readlink("/proc/self/ns/ipc"),
        "interfaces": [name for _i, name in socket.if_nameindex()],
        "tmp_private": not os.path.lexists(tmp_mark),
        "shm_private": not os.path.lexists(shm_mark),
        "erofs": _erofs(ro_file),
        "add_key": _add_key(),
        "placed": placed,
    }
    with open("report.json", "w", encoding="utf-8") as f:
        json.dump(report, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
