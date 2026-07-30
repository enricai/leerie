"""Named pin + round-trip proof for the seven resumable-planning
checkpoint keys (DESIGN §6 "Resumable planning — a per-phase checkpoint
cursor, not a `waves` gate").

`tests/test_state_fields.py` already enforces STATE_FIELDS <->
IMPLEMENTATION.md §8 parity generically, for every field, and
`tests/test_resumable_planning_keys.py` already pins these same seven
keys' declaration + documentation by name. This module adds the one
check neither of those covers: a real `State.save()` / on-disk JSON
reload round-trip with all seven keys populated at once — mirroring
`test_plan_snapshot_wiring.py`'s `TestSnapshotRoundTrips`. Kept as its
own file (rather than folded into either of the above) per this
subtask's scope note: pure state-surface assertions, no phase control
flow, no stubbed workers, no async.

Deliberately NOT added to `test_state_fields.py`: that module is generic
by design (it parses `STATE_FIELDS` and the spec table without naming
any single field), and hard-coding field names there would break its
shape.
"""
from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IMPL_MD = REPO_ROOT / "docs" / "IMPLEMENTATION.md"

CHECKPOINT_KEYS = (
    "plans_after_classify",
    "plans_after_plan",
    "plans_after_reconcile",
    "plans_after_overlap_judge",
    "plans_after_adherence_gate",
    "plans_after_coverage_gate",
    "plans_after_filters",
    "satisfied_probe_cache",
)


class TestCheckpointKeysDeclaredAndDocumented:
    def test_declared_in_state_fields(self, leerie):
        missing = [k for k in CHECKPOINT_KEYS if k not in leerie.STATE_FIELDS]
        assert not missing, (
            f"checkpoint keys missing from leerie.STATE_FIELDS: {missing}"
        )

    def test_documented_in_impl_field_table(self):
        text = IMPL_MD.read_text()
        missing = [
            k for k in CHECKPOINT_KEYS
            if not re.search(rf"^\|\s*`{k}`\s*\|", text, re.MULTILINE)
        ]
        assert not missing, (
            f"checkpoint keys missing a row in IMPLEMENTATION.md §8's "
            f"state.json field table: {missing} (CLAUDE.md: update the "
            f"spec in the same change)."
        )


class TestCheckpointKeysRoundTrip:
    """Behavioral: all seven keys survive a real State.save() to disk,
    byte-equal via json. A checkpoint that cannot be read back post-mortem
    is worthless — this is the guarantee `--resume` rehydration depends on."""

    def test_all_checkpoint_keys_survive_state_save(self, leerie):
        plans_payload = [
            {"id": "feat-a-1", "title": "example subtask",
             "success_criteria_seed": "example criterion"},
        ]
        probe_cache_payload = {
            "feat-a-1": {
                "satisfied": False,
                "evidence": "no matching commit on HEAD",
                "checked": ["src/foo.py"],
                "base_sha": "deadbeef" * 5,
            },
        }

        with tempfile.TemporaryDirectory() as d:
            st = leerie.State(leerie_root=Path(d) / ".leerie",
                              run_id="checkpoint-roundtrip")
            st.run_dir.mkdir(parents=True, exist_ok=True)

            for key in CHECKPOINT_KEYS:
                st.data[key] = (
                    probe_cache_payload if key == "satisfied_probe_cache"
                    else plans_payload
                )
            st.save()

            on_disk = json.loads(st.path.read_text())
            for key in CHECKPOINT_KEYS:
                expected = (
                    probe_cache_payload if key == "satisfied_probe_cache"
                    else plans_payload
                )
                assert key in on_disk, f"{key} missing after save()/reload"
                assert on_disk[key] == expected, (
                    f"{key} round-tripped with a different value than "
                    f"was written: {on_disk[key]!r} != {expected!r}"
                )

    def test_reloaded_state_is_byte_equal_via_load(self, leerie):
        """State.load() (not just a bare json.loads of the file) must
        reproduce the exact same in-memory dict for every checkpoint key —
        this is the real `--resume` path, not just the on-disk artifact."""
        payload = {k: {"n": i} for i, k in enumerate(CHECKPOINT_KEYS)}

        with tempfile.TemporaryDirectory() as d:
            leerie_root = Path(d) / ".leerie"
            st = leerie.State(leerie_root=leerie_root, run_id="checkpoint-load")
            st.run_dir.mkdir(parents=True, exist_ok=True)
            st.data.update(payload)
            st.save()
            st.release_lock()

            reloaded = leerie.State(leerie_root=leerie_root, run_id="checkpoint-load")
            assert reloaded.load() is True
            for key in CHECKPOINT_KEYS:
                assert reloaded.data[key] == payload[key]
