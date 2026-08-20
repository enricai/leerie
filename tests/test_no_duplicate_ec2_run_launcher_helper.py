"""Guards the single-owner `run_launcher` helper in tests/ec2_stub.py.

Once refactor-001 landed, no file under tests/ should redefine the
`(args, env) -> subprocess.CompletedProcess` shape of `_run_launcher`
that used to be duplicated across tests/test_auto_detect_run_runtime.py,
tests/test_ec2_launcher_kill.py, and tests/test_ec2_launcher_resume.py --
every call site should import `run_launcher` from tests/ec2_stub.py
instead (optionally wrapping it in a thin local function under a
different name, as those three files now do).

tests/test_group_state_dir_guard.py's own `_run_launcher(tmp_path, args,
env_extra=None, stub=None, stub_log=None)` is a genuinely distinct helper
(different purpose: injects a stub binary and returns a `.stub_log`-
augmented result) and is excluded by construction -- the regex below
matches only the specific two-positional-arg `(args, env)` call shape,
not that five-parameter one.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# Matches `def _run_launcher(args..., env...)` -- i.e. a two-positional-arg
# def whose second parameter is named `env`, which is the shape the three
# migrated duplicates shared. tests/test_group_state_dir_guard.py's
# `_run_launcher(tmp_path, args, env_extra=None, ...)` does not match: its
# second positional parameter is named `args`, not `env`.
_DEF_RE = re.compile(
    r"^\s*def _run_launcher\(\s*args\s*:[^,]*,\s*env\s*:", re.MULTILINE
)

# Legacy call sites not yet migrated to the shared `run_launcher` helper.
# This is a shrink-only allowlist: a migration subtask removes a file's
# local two-positional-arg `_run_launcher` def, drops the file's name from
# this set in the same commit, and CI catches any new local redefinition
# of that shape outside this set.
_KNOWN_UNMIGRATED: frozenset[str] = frozenset()


def test_run_launcher_is_defined_in_ec2_stub():
    import tests.ec2_stub as ec2_stub

    assert callable(ec2_stub.run_launcher)


def test_run_launcher_matches_the_common_call_shape():
    import inspect

    import tests.ec2_stub as ec2_stub

    sig = inspect.signature(ec2_stub.run_launcher)
    params = sig.parameters
    assert list(params) == ["args", "env", "launcher", "timeout", "use_bash"]
    assert params["launcher"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["use_bash"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default == 30
    assert params["use_bash"].default is False


def test_no_local_run_launcher_reimplementations_outside_the_allowlist():
    """No file under tests/ defines its own two-positional-arg
    `def _run_launcher(args, env)` unless it is a still-unmigrated legacy
    site named in `_KNOWN_UNMIGRATED`. A new local def of that shape
    outside that set is a fresh duplication and must import the shared
    `run_launcher` helper from tests/ec2_stub.py instead."""
    offenders = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        text = path.read_text()
        if _DEF_RE.search(text):
            offenders.append(str(path.relative_to(REPO_ROOT)))
    unexpected = sorted(set(offenders) - _KNOWN_UNMIGRATED)
    assert unexpected == [], (
        "The following files define a NEW local two-positional-arg "
        "`_run_launcher(args, env)` helper not in the legacy allowlist; "
        "import `run_launcher` from tests/ec2_stub.py instead:\n"
        + "\n".join(unexpected)
    )


def test_allowlist_has_no_stale_entries():
    """Every entry in `_KNOWN_UNMIGRATED` must still define a local
    two-positional-arg `_run_launcher`. When a migration subtask removes
    a file's local def, it must also drop that file's name from this
    allowlist in the same commit -- otherwise this shrink-only list
    silently stops shrinking."""
    offenders = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        text = path.read_text()
        if _DEF_RE.search(text):
            offenders.add(str(path.relative_to(REPO_ROOT)))
    stale = sorted(_KNOWN_UNMIGRATED - offenders)
    assert stale == [], (
        "The following files no longer define a local two-positional-arg "
        "`_run_launcher` but are still in the allowlist; remove them from "
        "_KNOWN_UNMIGRATED:\n" + "\n".join(stale)
    )


def test_group_state_dir_guard_helper_is_not_flagged():
    """Anti-vacuity control: the regex must not match
    tests/test_group_state_dir_guard.py's genuinely distinct
    `_run_launcher(tmp_path, args, env_extra=None, stub=None,
    stub_log=None)` helper, which this guard is not meant to migrate."""
    path = TESTS_DIR / "test_group_state_dir_guard.py"
    text = path.read_text()
    assert "def _run_launcher(" in text
    assert not _DEF_RE.search(text)
