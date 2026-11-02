"""`conftest._no_real_heldout_sandbox` reaches a Python child a test starts.

`main()` runs `_require_confinement` on every entry (734bd21c), and a host
that restricts unprivileged user namespaces — a developer host, CI's
ubuntu-latest — cannot build the sandbox it requires. The in-process stub
kept every test driving `main()` in the pytest process green, but not one
driving it in a child of its own (`test_credentials_never_outlive_the_run.py`,
15 tests red on such a host). The child gets the stub through
`tests/no_real_heldout_sandbox_site/sitecustomize.py`; a test marked
`real_heldout_sandbox` keeps the real gate in its children too.
"""
from __future__ import annotations

import os
import subprocess
import sys

import pytest

from tests.conftest import LEERIE_PY

# Loads the orchestrator by its own spec, as a `main()`-driving child does,
# then runs the entry's gate itself.
CHILD = r"""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules["leerie"] = m
spec.loader.exec_module(m)
print(m._require_heldout_sandbox.__name__)
if sys.argv[2] == "call":
    m._require_confinement(m.Path(sys.argv[3]))
    print("ENTRY PASSED")
"""


def _child(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-c", CHILD, str(LEERIE_PY),
                           *args], capture_output=True, text=True,
                          timeout=120, env=os.environ)


def test_a_child_s_entry_does_not_build_the_sandbox(tmp_path):
    run_dir = tmp_path / "state" / "runs" / "r1"
    run_dir.mkdir(parents=True)
    r = _child("call", str(run_dir))
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["<lambda>", "ENTRY", "PASSED"]


@pytest.mark.real_heldout_sandbox
def test_a_gate_test_s_child_keeps_the_real_gate():
    r = _child("load")
    assert r.returncode == 0, r.stderr
    assert r.stdout.split() == ["_require_heldout_sandbox"]
