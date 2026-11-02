"""The session fails when a test writes the user's real `~/.leerie`.

`tests/conftest.py` gives the session its own HOME, but a test can still
reach the user's: `REAL_HOME`, or a launcher run with the user's HOME and
no pinned state root, which makes `<state root>/logs` before any verb. Empty
`logs/` directories under names like `ci-old` and `sub` were found in a
user's real `~/.leerie`. Here a child session whose "user's home" is a
scratch directory runs one test that writes there, and one that does not.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

_INNER = '''
import os
from pathlib import Path


def test_writes_the_users_leerie():
    if os.environ.get("INNER_WRITE") == "1":
        (Path(os.environ["PYTEST_LEERIE_REAL_HOME"]) / ".leerie" / "ci-old"
         / "logs").mkdir(parents=True)
'''


def _child(tmp_path: Path, write: bool) -> subprocess.CompletedProcess:
    user = tmp_path / "user"
    (user / ".leerie" / "barnacle").mkdir(parents=True)
    work = tmp_path / "w"
    work.mkdir()
    (work / "test_inner.py").write_text(_INNER)
    (work / "pytest.ini").write_text("[pytest]\n")
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("PYTEST_", "XDG_"))}
    env.update(HOME=str(user), PYTHONPATH=str(REPO_ROOT),
               INNER_WRITE="1" if write else "0")
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider",
         "-p", "no:xdist", "-p", "tests.conftest",
         "-c", str(work / "pytest.ini"), "--rootdir", str(work),
         str(work / "test_inner.py")],
        cwd=work, env=env, capture_output=True, text=True, timeout=300)


def test_a_session_that_writes_the_users_leerie_fails(tmp_path):
    r = _child(tmp_path, write=True)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "gained ['ci-old']" in out
    # Read only: what the test made is left for the user to see.
    assert (tmp_path / "user" / ".leerie" / "ci-old" / "logs").is_dir()


def test_a_session_that_does_not_passes(tmp_path):
    r = _child(tmp_path, write=False)
    assert r.returncode == 0, r.stdout + r.stderr


def test_an_xdg_dir_at_the_users_home_fails_and_a_tmp_path_one_does_not(
        tmp_path, monkeypatch):
    """The per-test check refuses the user's own places, not a test's own
    tmp_path (tests that set XDG_CONFIG_HOME beneath it errored at teardown
    under the first, equality-based form)."""
    import tests.conftest as c
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    c._session_places_are_the_sessions()
    if c.REAL_HOME:
        monkeypatch.setenv("XDG_CONFIG_HOME", c.REAL_HOME + "/.config")
        with pytest.raises(AssertionError):
            c._session_places_are_the_sessions()
    monkeypatch.undo()


def test_a_temp_root_beneath_the_users_home_is_the_sessions(
        tmp_path, monkeypatch):
    """With TMPDIR beneath the user's home (as on hosts that keep scratch
    out of /tmp), the session HOME and a test's tmp_path live there; they
    are the session's places, not the user's."""
    import tempfile

    import tests.conftest as c
    if not c.REAL_HOME:
        pytest.skip("no user's home")
    root = os.path.join(c.REAL_HOME, ".cache", "x-tmp")
    monkeypatch.setattr(tempfile, "tempdir", root)
    assert not c._is_users_place(os.path.join(root, "pytest-1", "xdg"))
    assert c._is_users_place(os.path.join(c.REAL_HOME, ".config"))
