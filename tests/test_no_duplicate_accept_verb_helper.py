"""Guards the single-owner `_run_accept`/`_make_state` test harness helpers
for the accept-verb family (accept-blocked, accept-integration).

tests/test_accept_blocked.py is the sole owner. tests/test_accept_integration.py
already imports both names from it (`from tests.test_accept_blocked import
_run_accept, _make_state`) rather than redefining them -- this is the
location refactor-001 establishes and the codebase already implements, not
tests/conftest.py. See tests/test_no_duplicate_run_bash_helper.py for the
shrink-only-allowlist pattern this mirrors.

Scoped to the accept-verb family only: `_make_state` is a generic name
reused by ~25 unrelated test files for unrelated State-object builders, so
this file's scan is restricted to files that also reference `_run_accept(`
(the accept-verb call shape) -- never a blanket `^def _make_state(` sweep
across tests/.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

_OWNER = "tests/test_accept_blocked.py"

_DEF_RUN_ACCEPT_RE = re.compile(r"^\s*def _run_accept\(", re.MULTILINE)
_CALLS_RUN_ACCEPT_RE = re.compile(r"_run_accept\(")

# Legacy call sites not yet migrated onto the shared helpers. Shrink-only:
# a migration subtask removes a file's local def, drops the file's name
# from this set in the same commit, and CI catches any new local def
# outside this set.
_KNOWN_UNMIGRATED: frozenset[str] = frozenset()


def _accept_verb_family_files() -> list[Path]:
    """Files under tests/ that reference the `_run_accept(` call shape --
    the scope signal that distinguishes the accept-verb family from the
    ~25 unrelated files that define their own generically-named
    `_make_state` for unrelated purposes."""
    return [
        path
        for path in sorted(TESTS_DIR.rglob("test_*.py"))
        if _CALLS_RUN_ACCEPT_RE.search(path.read_text())
    ]


def test_run_accept_and_make_state_importable_from_test_accept_blocked():
    import tests.test_accept_blocked as owner

    assert callable(owner._run_accept)
    assert callable(owner._make_state)


def test_test_accept_integration_imports_rather_than_redefines():
    text = (TESTS_DIR / "test_accept_integration.py").read_text()
    assert "from tests.test_accept_blocked import" in text
    assert "_run_accept" in text
    assert "_make_state" in text
    assert not _DEF_RUN_ACCEPT_RE.search(text), (
        "test_accept_integration.py defines a local `_run_accept` instead "
        "of importing the shared one from tests.test_accept_blocked"
    )


def test_no_local_run_accept_reimplementations_outside_the_owner():
    """No file that calls `_run_accept(...)` may define its own local
    `_run_accept` unless it is the owner (tests/test_accept_blocked.py) or
    a still-unmigrated legacy site named in `_KNOWN_UNMIGRATED`."""
    offenders = []
    for path in _accept_verb_family_files():
        rel = str(path.relative_to(REPO_ROOT))
        if rel == _OWNER:
            continue
        if _DEF_RUN_ACCEPT_RE.search(path.read_text()):
            offenders.append(rel)
    unexpected = sorted(set(offenders) - _KNOWN_UNMIGRATED)
    assert unexpected == [], (
        "The following files define a NEW local `_run_accept` helper "
        "instead of importing it from tests.test_accept_blocked:\n"
        + "\n".join(unexpected)
    )


def test_allowlist_has_no_stale_entries():
    offenders = set()
    for path in _accept_verb_family_files():
        rel = str(path.relative_to(REPO_ROOT))
        if rel == _OWNER:
            continue
        if _DEF_RUN_ACCEPT_RE.search(path.read_text()):
            offenders.add(rel)
    stale = sorted(_KNOWN_UNMIGRATED - offenders)
    assert stale == [], (
        "The following files no longer define a local `_run_accept` but "
        "are still in the allowlist; remove them from _KNOWN_UNMIGRATED:\n"
        + "\n".join(stale)
    )
