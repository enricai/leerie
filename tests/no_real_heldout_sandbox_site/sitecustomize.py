"""`conftest._no_real_heldout_sandbox`, for a Python process a test starts.

The fixture stubs `_require_heldout_sandbox` in every copy of the
orchestrator loaded in the pytest process; a test that runs the real
`main()` in a child process of its own (a `python -c` driver, to observe
what its exit leaves) loads a copy the fixture never sees, and on a host
that restricts unprivileged user namespaces (a developer host, CI's
ubuntu-latest) `main()`'s `_require_confinement` dies there before anything
the test is about. The fixture puts this directory first on `PYTHONPATH`
(unless the test is marked `real_heldout_sandbox`), so `site` imports this
module at the child's start: every `orchestrator/leerie.py` executed in the
child, however it is loaded (`import leerie`, its own spec), gets the same
stub. A child started with `-I` or `-S` (leerie's own helpers, the shim)
ignores it."""
import importlib.machinery
import os

_LEERIE_PY = os.path.realpath(os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "..", "..", "orchestrator",
    "leerie.py"))
_exec_module = importlib.machinery.SourceFileLoader.exec_module


def _exec_module_without_the_sandbox_gate(self, module):
    _exec_module(self, module)
    if (os.path.realpath(getattr(module, "__file__", None) or "")
            == _LEERIE_PY and hasattr(module, "_require_heldout_sandbox")):
        module._require_heldout_sandbox = lambda run_dir: None


importlib.machinery.SourceFileLoader.exec_module = \
    _exec_module_without_the_sandbox_gate
