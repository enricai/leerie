"""A plan-time advisory that changes nothing is not a signal, it is a log line.

`_warn_provider_subset_subtasks` flags, before any spend, every subtask whose
entire `files_likely_touched` surface is already owned by an ordered
predecessor. It was right and inert: measured across the corpus, all three of
run `1b9b52f5`'s flagged subtasks ran a full implementer and committed nothing,
and twelve subtasks corpus-wide reached the post-execution rescue only after
their whole spend had happened.

The flag now reaches execution: `_settle_subtask` runs the *same* HEAD probe
against the staging worktree before spawning the implementer, so a redundancy
that only became real when the predecessor committed costs one read-only probe
instead of a full worker.

What deliberately did NOT change: the flag never settles anything by itself.
The probe decides, against a real tree, and fails safe to None — so anything
short of a confident `satisfied` proceeds to the implementer untouched.

See docs/POSTMORTEM-2026-08-14.md, F8, and DESIGN §8 *Probing a flagged
subtask before it spends*.
"""
from __future__ import annotations

import inspect

import pytest


def _plan(*subtasks: dict) -> list[dict]:
    return [{"domain": "feat", "status": "ready", "subtasks": list(subtasks)}]


def _st(sid: str, **kw) -> dict:
    return {"id": sid, "title": sid, "success_criteria_seed": "it works",
            "files_likely_touched": [], "depends_on": [], "requires": [],
            "provides": [], **kw}


class TestTheAdvisoryReturnsWhatItFlags:
    """It has to hand the sids over; logging them is what made it inert."""

    def test_returns_the_flagged_sid(self, leerie):
        plans = _plan(
            _st("feat-001", files_likely_touched=["src/a.ts", "src/a.test.ts"]),
            _st("test-001", files_likely_touched=["src/a.test.ts"],
                depends_on=["feat-001"]),
        )
        assert leerie._warn_provider_subset_subtasks(plans) == ["test-001"]

    def test_returns_empty_when_nothing_is_flagged(self, leerie):
        plans = _plan(
            _st("feat-001", files_likely_touched=["src/a.ts"]),
            _st("test-001", files_likely_touched=["src/b.test.ts"],
                depends_on=["feat-001"]),
        )
        assert leerie._warn_provider_subset_subtasks(plans) == []

    def test_returns_empty_on_an_empty_plan(self, leerie):
        assert leerie._warn_provider_subset_subtasks([]) == []

    def test_is_sorted_and_deduplicated_by_construction(self, leerie):
        """`_schedule` is documented deterministic; a signal feeding it must
        not depend on dict iteration order."""
        plans = _plan(
            _st("feat-001", files_likely_touched=["src/a.ts"]),
            _st("test-002", files_likely_touched=["src/a.ts"],
                depends_on=["feat-001"]),
            _st("test-001", files_likely_touched=["src/a.ts"],
                depends_on=["feat-001"]),
        )
        out = leerie._warn_provider_subset_subtasks(plans)
        assert out == sorted(out) and len(out) == len(set(out))
        assert out == ["test-001", "test-002"]


class TestItIsPersistedNotJustLogged:
    def test_the_state_key_is_declared(self, leerie):
        assert "provider_subset_sids" in leerie.STATE_FIELDS

    def test_the_caller_persists_the_return_value(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert ('st.data["provider_subset_sids"] = (\n'
                "                _warn_provider_subset_subtasks(plans))") in src, (
            "the advisory's return value must reach state.json; discarding it "
            "is what left the signal inert")

    def test_the_write_is_saved(self, leerie):
        """An in-memory-only write is lost on pause/crash, and the consumer
        runs in a later phase."""
        src = inspect.getsource(leerie._run_phases)
        i = src.index('st.data["provider_subset_sids"]')
        assert "st.save()" in src[i:i + 200]


class TestTheProbeRunsBeforeTheSpend:
    """Ordering is the entire fix — the probe already existed."""

    @staticmethod
    def _src(leerie) -> str:
        return inspect.getsource(leerie._settle_subtask)

    def test_the_flag_is_read_in_settle(self, leerie):
        assert 'st.data.get("provider_subset_sids")' in self._src(leerie)

    def test_the_probe_precedes_the_implementer_spawn(self, leerie):
        src = self._src(leerie)
        flag = src.index('st.data.get("provider_subset_sids")')
        probe = src.index("_probe_criteria_satisfied_on_head(", flag)
        spawn = src.index("await _run_implementer(")
        assert flag < probe < spawn, (
            "probing after the implementer has run is the behaviour this "
            "change exists to replace, not to duplicate")

    def test_it_probes_the_staging_worktree(self, leerie):
        """The subtask's own worktree does not exist yet — `new-worktree.sh`
        runs inside `_run_implementer` — and staging sits at the run-branch
        HEAD, the ref the post-hoc rescue measures against."""
        src = self._src(leerie)
        i = src.index('st.data.get("provider_subset_sids")')
        window = src[i:i + 1400]
        assert '"worktrees" / "staging"' in window
        assert ".is_dir()" in window, (
            "a missing staging worktree must skip the probe, not crash")

    def test_the_two_probes_do_not_share_a_worker_log(self, leerie):
        """Both can fire for one subtask in one run; a shared sid would let
        the second overwrite the first's log."""
        probe_src = inspect.getsource(leerie._probe_criteria_satisfied_on_head)
        assert 'sid=f"satisfied_probe-{label}-{sid}"' in probe_src
        assert 'label="pre"' in self._src(leerie)

    def test_the_settle_goes_through_the_shared_helper(self, leerie):
        src = self._src(leerie)
        i = src.index('st.data.get("provider_subset_sids")')
        assert "_settle_already_satisfied(" in src[i:i + 1400]

    def test_the_audit_distinguishes_the_two_moments(self, leerie):
        src = self._src(leerie)
        i = src.index('st.data.get("provider_subset_sids")')
        assert '"already_satisfied_pre_spawn"' in src[i:i + 1400], (
            "one settlement cost a probe and the other cost a worker first; "
            "the audit should say which")


class TestTheFlagAloneNeverSettles:
    """Anti-vacuity: the advisory must not have become a drop."""

    def test_a_declining_probe_leaves_the_subtask_alone(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        i = src.index('st.data.get("provider_subset_sids")')
        window = src[i:i + 1400]
        assert "if pre_drop is not None:" in window, (
            "the settle must be conditional on the probe's verdict, never on "
            "the flag")

    def test_the_probe_still_fails_safe_to_none(self, leerie):
        """The pre-spawn site adds no new way to settle: a crashed probe
        returns None and the subtask proceeds to its implementer."""
        src = inspect.getsource(leerie._probe_criteria_satisfied_on_head)
        i = src.index("except (WorkerError")
        assert "return None" in src[i:i + 600]

    def test_an_unflagged_subtask_is_never_probed(self, leerie):
        """The guard is membership in the flagged list, not a truthiness test
        that an empty list would still pass per-subtask."""
        src = inspect.getsource(leerie._settle_subtask)
        assert 'if sid in (st.data.get("provider_subset_sids") or []):' in src

    def test_a_run_with_no_flag_key_does_not_crash(self, leerie):
        """Every resume from a state.json written before this key existed."""
        src = inspect.getsource(leerie._settle_subtask)
        assert 'st.data.get("provider_subset_sids") or []' in src


class TestTheSettleHelperIsSharedNotCopied:
    def test_both_sites_call_it(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        assert src.count("_settle_already_satisfied(") == 2, (
            "the pre-spawn probe and the post-execution rescue must reach the "
            "same settle; two copies is how one loses the conformance "
            "sentinel")

    @pytest.mark.parametrize("write", [
        'st.data.setdefault("dropped_subtasks", {})[sid] = drop',
        'st.data.setdefault("subtask_status", {})[sid] = "complete"',
        # the rescue's conformance SENTINEL, identified by its warning text —
        # `_settle_subtask` still writes a real `conformance[sid]` entry for
        # the actual conformer, which is a different thing
        '"settled complete via satisfied rescue; "',
    ])
    def test_the_state_writes_live_only_in_the_helper(self, leerie, write):
        assert write in inspect.getsource(leerie._settle_already_satisfied)
        assert write not in inspect.getsource(leerie._settle_subtask)

    def test_it_saves(self, leerie):
        assert "st.save()" in inspect.getsource(
            leerie._settle_already_satisfied)
