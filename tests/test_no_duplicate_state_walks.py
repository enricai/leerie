"""The state-init branch-split derivation lives in exactly one place.

`tests/test_state_fields.py::_state_init_branch_keys` walks `_run_phases`'
`if args.resume:` node and returns the `st.data` keys each arm seeds. Several
guards consume it, and every one must derive rather than enumerate — a
hand-kept list of keys stops covering reality the moment one is added, which
is how `--skip-coverage-check` shipped inert on every fresh run while a
two-entry `_BOTH_BRANCH_KEYS` tuple passed.

The derivation was then briefly written twice, once in the file that owns it
and once in `test_leerie_commit.py`. Two copies of a rule drift exactly the
way two copies of a list do: change how `_run_phases` splits and one copy
gets fixed while the other keeps passing — with the *silent* direction being
an under-reported `resume_keys`, which makes the symmetry guard pass
vacuously rather than fail. This guard makes a third copy impossible to add
quietly.

Directly modelled on `tests/test_no_duplicate_launcher_splitters.py`, which
exists for the identical reason one layer over (PRs #180-#183).
"""
from __future__ import annotations

from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
OWNER = "test_state_fields.py"

# The load-bearing token of the derivation. Prose describing the concept
# writes it plainly (`if args.resume:`); only an actual implementation
# compares the unparsed test against it. Matching the plain phrase instead
# would report every file that merely *documents* the split as if it
# re-implemented it — and the natural response to a false-positive guard is
# to weaken it, which is how guards die.
_MARKER = 'ast.unparse(n.test) == "args.resume"'

# The symbol consumers import instead of re-walking.
_SHARED = "_state_init_branch_keys"


def _files_matching_marker() -> dict[str, int]:
    """Every file under tests/ containing the walk marker, and its count."""
    hits: dict[str, int] = {}
    for path in sorted(TESTS_DIR.rglob("*.py")):
        if path.name == Path(__file__).name:
            continue  # this file necessarily names the marker to check it
        try:
            text = path.read_text(errors="replace")
        except OSError:
            continue
        n = text.count(_MARKER)
        if n:
            hits[path.name] = n
    return hits


def test_only_the_owner_derives_the_state_init_split() -> None:
    hits = _files_matching_marker()
    strays = {name: n for name, n in hits.items() if name != OWNER}
    assert not strays, (
        f"{sorted(strays)} re-implement the state-init branch-split walk that "
        f"tests/{OWNER} owns. Import `{_SHARED}` from it instead — two copies "
        f"of this rule drift the same way two copies of a key list do, and "
        f"the silent direction (an under-reported resume branch) makes the "
        f"symmetry guard pass vacuously rather than fail."
    )


def test_the_owner_actually_contains_it() -> None:
    """Anti-vacuity: a scan that matches nothing passes the test above.

    If the marker ever stops appearing in the owner — renamed, refactored, or
    the scan simply broken — this fails as a broken scan rather than silently
    certifying "no duplicates found".
    """
    hits = _files_matching_marker()
    assert OWNER in hits, (
        f"tests/{OWNER} does not contain {_MARKER!r} — the duplicate scan is "
        f"matching nothing, so its result above is meaningless")


def test_scan_actually_detects_a_planted_stray(tmp_path, monkeypatch) -> None:
    """`test_only_the_owner_derives_the_state_init_split` never fires its
    `strays` branch on this repo's real tests/ tree (there are, by design,
    zero unauthorized copies right now), so nothing proves
    `_files_matching_marker` would actually catch a reintroduced duplicate
    walk. Drive it against a temp tree holding a planted second copy of
    the marker under a non-owner filename."""
    monkeypatch.setattr("tests.test_no_duplicate_state_walks.TESTS_DIR",
                         tmp_path)
    (tmp_path / OWNER).write_text(f"# owner\n{_MARKER}\n")
    (tmp_path / "test_fake_reimplementation.py").write_text(
        f"# stray copy\n{_MARKER}\n")
    (tmp_path / "test_fake_clean.py").write_text("def test_noop():\n    pass\n")

    hits = _files_matching_marker()

    assert hits[OWNER] == 1
    assert hits["test_fake_reimplementation.py"] == 1
    assert "test_fake_clean.py" not in hits


def test_the_shared_walk_is_actually_used() -> None:
    """A single owner nobody imports is dead code, not deduplication."""
    importers = [
        p.name for p in sorted(TESTS_DIR.rglob("*.py"))
        if p.name not in (OWNER, Path(__file__).name)
        and _SHARED in p.read_text(errors="replace")
    ]
    assert len(importers) >= 2, (
        f"expected at least the two known consumers (test_leerie_commit.py, "
        f"test_phase_planning_coverage_gate.py) to import `{_SHARED}`, found "
        f"{importers}")
