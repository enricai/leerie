"""Tests for run_id derivation primitives — DESIGN §6 "The run identifier".

Covers:
- `CATEGORY_ABBREV` coverage of every entry in `CATEGORIES` (drift guard).
- `_ID_PREFIXES` derivation from `CATEGORY_ABBREV`.
- `_compute_run_branch` and `_compute_subtask_branch` shape.
"""
from __future__ import annotations


# --- CATEGORY_ABBREV coverage ----------------------------------------------

def test_category_abbrev_covers_every_category(leerie):
    """Every category in CATEGORIES must have an abbreviation. If a future
    change adds a new category, this test fails until the abbrev is added."""
    missing = [c for c in leerie.CATEGORIES if c not in leerie.CATEGORY_ABBREV]
    assert not missing, (
        f"CATEGORIES has entries with no CATEGORY_ABBREV: {missing}. "
        "Add abbreviations alongside any new category."
    )


def test_category_abbrev_has_no_extras(leerie):
    """CATEGORY_ABBREV should not contain abbreviations for categories that
    don't exist — catches typos in the dict keys."""
    extras = [k for k in leerie.CATEGORY_ABBREV if k not in leerie.CATEGORIES]
    assert not extras, (
        f"CATEGORY_ABBREV has keys not in CATEGORIES (typos?): {extras}"
    )


def test_category_abbrev_values_are_short_and_safe(leerie):
    """Abbreviations are embedded in git branch names; they must be
    ASCII alphanumeric (with `-` allowed) and short enough that the full
    run_id stays under typical branch-name length limits."""
    for cat, abbrev in leerie.CATEGORY_ABBREV.items():
        assert 1 <= len(abbrev) <= 8, (
            f"{cat!r} → {abbrev!r}: abbrev should be 1-8 chars"
        )
        assert all(c.isalnum() or c == "-" for c in abbrev), (
            f"{cat!r} → {abbrev!r}: abbrev has non-[a-zA-Z0-9-] chars"
        )
        assert abbrev == abbrev.lower(), (
            f"{cat!r} → {abbrev!r}: abbrev must be lowercase (matches "
            "the rest of the slug shape)"
        )


def test_id_prefixes_derive_from_category_abbrev(leerie):
    """The validator's allowlist must equal `{abbrev + "-"}` for every
    category. Drift between these two maps is what aborted the
    bug-fixing run that motivated commit 6b824c4's follow-up: the
    planner faithfully used the injected `ID_PREFIX` (`fix-`) but
    _validate_plan only accepted `bugfix-`. They are now derived from
    one source; this test fails if a future contributor restates the
    set by hand and forgets a category."""
    expected = frozenset(f"{v}-" for v in leerie.CATEGORY_ABBREV.values())
    assert leerie._ID_PREFIXES == expected


# --- _compute_run_branch ----------------------------------------------------

def test_compute_run_branch_shape(leerie):
    assert leerie._compute_run_branch("feat-foo-abc123") == "leerie/runs/feat-foo-abc123"


def test_compute_run_branch_is_pure(leerie):
    """Same input → same output. Trivial but pinning the contract."""
    rid = "fix-bar-def456"
    assert leerie._compute_run_branch(rid) == leerie._compute_run_branch(rid)


def test_compute_subtask_branch_shape(leerie):
    assert (leerie._compute_subtask_branch("feat-foo-abc123", "feat-001")
            == "leerie/subtasks/feat-foo-abc123/feat-001")


def test_compute_subtask_branch_is_pure(leerie):
    rid, sid = "fix-bar-def456", "fix-002"
    assert (leerie._compute_subtask_branch(rid, sid)
            == leerie._compute_subtask_branch(rid, sid))
