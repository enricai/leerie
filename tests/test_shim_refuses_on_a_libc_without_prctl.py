"""`install_seccomp` raises OSError when libc has no `prctl`, so the shim
refuses rather than tracebacks.

The docstring has always promised "Raises OSError naming the step", but the
`prctl` lookup is a `ctypes` attribute access: on a libc without the symbol
it raised `AttributeError`, which `_confine_or_126`'s `except OSError` does
not catch. The shim then exited 1 with a traceback instead of writing its
refusal and exiting 126 — and a caller read "no verdict from this run" where
the shim meant "I will not run this" (DESIGN §6 *A worker writes only where
it is granted*). That silence hid the cause of five failing tests for a
whole debugging session.

Driven by injection, so the contract is checked on every kernel, including
the Linux one where the symbol is present.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim():
    spec = importlib.util.spec_from_file_location("lk_prctl_test", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


class _LibcWithoutPrctl:
    """A libc whose `prctl` is missing, as ctypes reports it."""
    def __getattr__(self, name: str):
        if name == "prctl":
            raise AttributeError(
                "dlsym(RTLD_DEFAULT, prctl): symbol not found")
        raise AttributeError(name)


def test_a_libc_without_prctl_raises_oserror_not_attributeerror(monkeypatch):
    """The documented contract, as the caller's `except OSError` needs it."""
    lk = _shim()
    monkeypatch.setattr(lk, "_libc", lambda: _LibcWithoutPrctl())
    with pytest.raises(OSError) as got:
        lk.install_seccomp()
    # Names the step, so the refusal says which one failed.
    assert "prctl" in str(got.value)
    assert not isinstance(got.value, AttributeError)


def test_the_refusal_names_the_step_and_the_shim_exits_126(monkeypatch,
                                                           tmp_path):
    """Substance: the whole path, not just the raise — `_confine_or_126`
    catches it, writes the operator-facing refusal and exits 126.
    """
    lk = _shim()
    monkeypatch.setattr(lk, "_libc", lambda: _LibcWithoutPrctl())
    said = []
    monkeypatch.setattr(lk, "_refused", lambda msg: (said.append(msg), 126)[1])
    rc = lk._confine_or_126([str(tmp_path)], ["/"])
    assert rc == 126, rc
    assert said and "cannot confine the worker" in said[0]
    assert "prctl" in said[0], said[0]


@pytest.mark.skipif(sys.platform.startswith("linux"),
                    reason="this libc has prctl; the end-to-end exit is the "
                           "same contract the injected tests pin")
def test_the_real_shim_refuses_with_126_where_libc_has_no_prctl():
    """End-to-end on a host whose libc really lacks the symbol: the shim
    says why and exits 126, never a traceback.
    """
    r = subprocess.run([sys.executable, "-I", "-S", str(SHIM),
                        "--ro", "/", "--", "/bin/echo", "hi"],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 126, (r.returncode, r.stderr)
    assert "cannot confine the worker" in r.stderr
    assert "prctl" in r.stderr
    assert "Traceback" not in r.stderr, r.stderr
