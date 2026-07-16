"""Source-coupling pins for the HAS_JQ gate wiring.

Pins the coupling that makes the 23 host-only tests skip cleanly inside the
leerie container instead of failing there:
  1. conftest exposes a module-level HAS_JQ bool, computed from a real PATH
     lookup (not, say, a platform check — jq's presence is the actual
     dependency, and it is absent in the image while present on dev hosts
     and CI runners alike).
  2. Each of the four host-only test modules imports HAS_JQ from
     tests.conftest AND gates on it via a module-level skipif.

Why these four are host-only: they source bash the *host* owns —
`scripts/host-finalize.sh`, `provision.sh`'s `decide_teardown`, and the
launcher's own `--finalize` / `no_push` paths — all of which parse run.json
with real `jq`. The harnesses stub `git` and `gh` onto PATH but not `jq`, so
jq is silently inherited from whichever machine runs pytest. Per DESIGN §6
*Finalization* those scripts could never succeed in-container anyway (gh
auth, ssh-agent, and Keychain are host-side), so skipping is honest rather
than lossy.

A silent regression here — dropping the skipif from one file — re-introduces
that file's failures inside the container with no other signal. This trap is
not hypothetical: the HAS_TREESITTER gate exists because exactly that
happened to 19 tree-sitter tests, and `test_host_finalize_sh.py` alone
accounts for 19 of the 23 here.

Mirrors tests/test_repo_map_gate_wiring.py.
"""
from __future__ import annotations

from pathlib import Path

import tests.conftest as conftest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Measured, not inferred: these are the modules that fail inside
# leerie:<version> with "jq: command not found". A grep for "jq" does NOT
# reproduce this list — test_decide_teardown_auto_finalize.py and
# test_launcher_finalize_no_work.py never mention jq themselves; they fail
# because the script under test shells out to it.
GATED_MODULES = [
    "test_host_finalize_sh.py",
    "test_decide_teardown_auto_finalize.py",
    "test_launcher_finalize_no_work.py",
    "test_launcher_no_push_skips.py",
]


def test_conftest_exposes_has_jq_bool():
    """The flag must exist and be a plain bool evaluated at import time, so
    modules can use it in a module-level skipif at collection."""
    assert hasattr(conftest, "HAS_JQ"), (
        "tests/conftest.py must expose a module-level HAS_JQ")
    assert isinstance(conftest.HAS_JQ, bool)


def test_has_jq_reflects_a_real_path_lookup():
    """HAS_JQ must be derived from an actual PATH probe.

    Pinning the mechanism, not the value: the value legitimately differs by
    machine (True on a dev host and in CI, False in the leerie image), which
    is the entire point of the gate."""
    import shutil

    assert conftest.HAS_JQ == (shutil.which("jq") is not None), (
        "HAS_JQ must agree with a live `shutil.which('jq')` lookup")


def test_gated_modules_import_and_use_has_jq():
    """Each host-only module must BOTH import HAS_JQ and gate on it.

    Importing without gating, or gating on a locally-recomputed flag, both
    silently re-open the container failures this exists to prevent."""
    for name in GATED_MODULES:
        src = (TESTS_DIR / name).read_text()
        assert "from tests.conftest import HAS_JQ" in src, (
            f"{name} must import HAS_JQ from tests.conftest")
        assert "skipif" in src and "HAS_JQ" in src.split("skipif", 1)[1][:200], (
            f"{name} must carry a skipif referencing HAS_JQ")


def test_gated_modules_exist():
    """Guard the guard: a renamed or deleted module must not silently drop
    out of GATED_MODULES coverage."""
    for name in GATED_MODULES:
        assert (TESTS_DIR / name).is_file(), (
            f"{name} is listed in GATED_MODULES but does not exist — update "
            "this list if the module was renamed")
