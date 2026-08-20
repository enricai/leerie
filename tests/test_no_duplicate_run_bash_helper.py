"""Guards the single-owner `run_bash` helper in tests/conftest.py.

Once migration subtasks land, no file under tests/ should define its own
`def _run_bash(` — every call site should import the shared `run_bash`
fixture/helper from conftest instead. See tests/conftest.py::run_bash.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

_DEF_RE = re.compile(r"^\s*def _run_bash\(", re.MULTILINE)

# Legacy call sites not yet migrated to the shared `run_bash` helper. This
# is a shrink-only allowlist: a migration subtask removes a file's local
# `_run_bash` def, drops the file's name from this set in the same
# commit, and CI catches any new local `_run_bash` def outside this set.
_KNOWN_UNMIGRATED = frozenset(
    {
        "tests/test_collect_subtrees_sh.py",
        "tests/test_ec2_decide_teardown.py",
        "tests/test_ec2_ssm.py",
        "tests/test_ec2_volume_reaping.py",
        "tests/test_fetch_branch_sh.py",
        "tests/test_force_stop.py",
        "tests/test_pause_on_failure.py",
        "tests/test_provision_sh_lifecycle.py",
        "tests/test_provision_volume.py",
        "tests/test_re_seed.py",
        "tests/test_require_fly_ssh_isolation.py",
    }
)


def test_run_bash_is_defined_in_conftest():
    import tests.conftest as conftest

    assert callable(conftest.run_bash)


def test_run_bash_matches_the_common_call_shape():
    import inspect

    import tests.conftest as conftest

    sig = inspect.signature(conftest.run_bash)
    params = sig.parameters
    assert list(params) == ["script", "env", "cwd", "input"]
    assert params["env"].default is None
    assert params["cwd"].default is None
    assert params["input"].default is None
    assert params["cwd"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["input"].kind == inspect.Parameter.KEYWORD_ONLY


def test_no_local_run_bash_reimplementations_outside_the_allowlist():
    """No file under tests/ defines its own `def _run_bash(` unless it is
    a still-unmigrated legacy site named in `_KNOWN_UNMIGRATED`. A new
    local `_run_bash` def outside that set is a fresh duplication and
    must import the shared `run_bash` helper from conftest.py instead."""
    offenders = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        text = path.read_text()
        if _DEF_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    unexpected = sorted(set(offenders) - _KNOWN_UNMIGRATED)
    assert unexpected == [], (
        "The following files define a NEW local `_run_bash` helper not in "
        "the legacy allowlist; import `run_bash` from tests/conftest.py "
        "instead:\n" + "\n".join(unexpected)
    )


def test_allowlist_has_no_stale_entries():
    """Every entry in `_KNOWN_UNMIGRATED` must still define a local
    `_run_bash`. When a migration subtask removes a file's local def, it
    must also drop that file's name from this allowlist in the same
    commit -- otherwise this shrink-only list silently stops shrinking."""
    offenders = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        text = path.read_text()
        if _DEF_RE.search(text):
            offenders.add(str(path.relative_to(REPO_ROOT)))
    stale = sorted(_KNOWN_UNMIGRATED - offenders)
    assert stale == [], (
        "The following files no longer define a local `_run_bash` but are "
        "still in the allowlist; remove them from _KNOWN_UNMIGRATED:\n"
        + "\n".join(stale)
    )
