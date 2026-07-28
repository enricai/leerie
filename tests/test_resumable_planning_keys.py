"""Guard-the-guard pins for the resumable-planning checkpoint keys
(DESIGN §6 "Resumable planning — a per-phase checkpoint cursor, not a
`waves` gate").

`tests/test_state_fields.py` already enforces STATE_FIELDS <-> IMPLEMENTATION.md
§8 parity generically, for every field. This module pins the specific new
keys by name — mirroring `test_plan_snapshot_wiring.py`'s
`TestSnapshotIsDeclaredAndDocumented` — so a future refactor that silently
drops one of these keys (rather than renaming/removing it deliberately in
both places at once) fails loudly here, not just in the generic parity
test's diff.

This subtask (bugfix-002) registers the keys only; no checkpoint-writing
code exists yet (that's bugfix-003/004/005). These tests are therefore
scoped to declaration + documentation, not to any runtime write.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPL_MD = REPO_ROOT / "docs" / "IMPLEMENTATION.md"

NEW_CHECKPOINT_KEYS = (
    "plans_after_classify",
    "plans_after_plan",
    "plans_after_reconcile",
    "plans_after_overlap_judge",
    "plans_after_adherence_gate",
    "plans_after_filters",
    "satisfied_probe_cache",
)


class TestResumablePlanningKeysDeclaredAndDocumented:
    def test_declared_in_state_fields(self, leerie):
        missing = [k for k in NEW_CHECKPOINT_KEYS if k not in leerie.STATE_FIELDS]
        assert not missing, (
            f"checkpoint keys missing from leerie.STATE_FIELDS: {missing}"
        )

    def test_documented_in_impl_field_table(self):
        text = IMPL_MD.read_text()
        missing = [
            k for k in NEW_CHECKPOINT_KEYS
            if not re.search(rf"^\|\s*`{k}`\s*\|", text, re.MULTILINE)
        ]
        assert not missing, (
            f"checkpoint keys missing a row in IMPLEMENTATION.md §8's "
            f"state.json field table: {missing} (CLAUDE.md: update the "
            f"spec in the same change)."
        )


def test_budget_preflight_no_longer_claims_unresumable():
    """DESIGN §6 'Budget-check resume': once plans are checkpointed per
    phase, a run that stopped at the budget-feasibility gate IS
    resumable via plan_snapshot. IMPLEMENTATION.md must not carry the
    old, now-false claim that such a run cannot be resumed."""
    text = IMPL_MD.read_text()
    assert "A run that died on the preflight is\nnot resumable" not in text
    assert "A run that died on the preflight is not resumable" not in text
