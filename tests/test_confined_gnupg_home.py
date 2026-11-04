"""A confined command has a gpg home of its own (DESIGN §6 *A confined
command's scratch is short and private*).

mise verifies a Node download by importing Node's release keys with gpg.
Confined as the toolchain kind, `mise install` could write `MISE_DATA_DIR`
and its temp directory but not `~/.gnupg`, so every Node version not yet
installed died in provisioning: "gpg: no writable keyring found" (barnacle,
2026-11-04, Node floor raised to 22.19.0). Each grant now names a new 0700
`GNUPGHOME` inside the command's private temp directory; `~/.gnupg` stays
unwritable, and mise's verification stays on."""
from __future__ import annotations

import asyncio
import os
import shutil
import stat
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


@pytest.fixture()
def home(tmp_path, monkeypatch) -> Path:
    """A home with the image's `~/.gnupg` (0700, empty) and `MISE_DATA_DIR`
    outside it, so the toolchain grant names neither the real home's."""
    h = tmp_path / "home"
    (h / ".gnupg").mkdir(parents=True, mode=0o700)
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    return h


def _keyring() -> Path | None:
    for d in (Path("/usr/share/keyrings"),):
        if d.is_dir():
            for p in sorted(d.glob("*-archive-keyring.gpg")):
                if p.stat().st_size:
                    return p
    return None


@pytest.mark.parametrize("kind", ["TOOLCHAIN_KIND", "SETUP_HOOK_KIND",
                                  "INSTALL_KIND", "MEASURE_KIND"])
def test_every_kind_names_a_new_private_gnupg_home(leerie, run, home, kind):
    st, view = _st(run), _view(run)
    k = getattr(leerie, kind)
    env = dict(leerie._measurement_grants(st, view, k).env)
    again = dict(leerie._measurement_grants(st, view, k).env)
    g = Path(env["GNUPGHOME"])
    assert g.parent == Path(env["TMPDIR"]), env
    assert g.is_dir() and stat.S_IMODE(os.lstat(g).st_mode) == 0o700
    assert list(g.iterdir()) == []
    # A new one per grant: nothing one command imported is another's.
    assert Path(again["GNUPGHOME"]) != g
    assert not Path(again["GNUPGHOME"]).is_relative_to(home)


@pytest.mark.skipif(shutil.which("gpg") is None or _keyring() is None,
                    reason="no gpg or no system keyring to import")
def test_a_toolchain_command_imports_a_key_and_not_into_home(leerie, run,
                                                             home):
    """Under the real shim and the toolchain grant: gpg imports a key from
    stdin, as mise does, into the grant's `GNUPGHOME`; `~/.gnupg` is still
    refused."""
    view = _view(run)
    g = leerie._measurement_grants(_st(run), view, leerie.TOOLCHAIN_KIND)
    script = (
        f'gpg --batch --import < {_keyring()} && echo IMPORT-OK\n'
        'ls "$GNUPGHOME" | grep -q pubring && echo KEYRING-OK\n'
        f'if echo x > {home}/.gnupg/probe 2>/dev/null; '
        'then echo HOME-WRITTEN; else echo HOME-REFUSED; fi\n')
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", script], grants=g, cwd=str(view), timeout=60,
        verbosity="quiet", tail_lines=50))
    assert "IMPORT-OK" in tail and "KEYRING-OK" in tail, tail
    assert "HOME-REFUSED" in tail and "HOME-WRITTEN" not in tail, tail
    assert list((home / ".gnupg").iterdir()) == []
