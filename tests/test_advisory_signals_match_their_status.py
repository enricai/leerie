"""A signal documented as advisory must not gate, and vice versa.

Three instances, all measured in one corpus (docs/POSTMORTEM-2026-08-14.md, F23):

  * `NO_PLANNED_FILES_TOUCHED` gated on `files_likely_touched`, which
    `_clobbered_owned_files`' own docstring calls "advisory and NOT used
    because the implementer may commit outside it". Re-driving on it asks a
    worker to match a planner's prediction rather than to do the work:
    `1b9b52f5`/test-007 was re-driven into a pure-rename commit whose only
    content was moving a test from the repo convention to the planner's path.

  * every CONTINUATION prompt ordered a read of `checkpoints/<sid>.md`, which
    is written only on `incomplete-handoff` / `needs-clarification` — 11 wasted
    `tool-fail` reads across two runs, on the prompt that also carries the
    completeness gate's mandatory criteria.

  * `LAYER_GAP`'s env heuristic matched "credential" against provides tags, so
    it fired on subtasks that touch no env file and never intended to.
"""
from __future__ import annotations

import inspect

import pytest


class TestPlannedFilesIsAdvisory:
    def _issues(self, leerie, planned, actual):
        return leerie.check_implementer_output(
            {"status": "complete", "criteria_results": []},
            {"id": "feat-001", "files_likely_touched": planned},
            set(actual))

    def test_it_is_still_reported(self, leerie):
        """Demoted, not deleted — the operator should still see it."""
        out = self._issues(leerie, ["src/a.ts"], {"src/b.ts"})
        assert any(i.startswith("NO_PLANNED_FILES_TOUCHED") for i in out)

    def test_it_does_not_gate(self, leerie):
        """Scoped to the label under test: this fixture also trips the
        unrelated production-evidence gate, which is not what is being pinned.
        """
        out = self._issues(leerie, ["src/a.ts"], {"src/b.ts"})
        gating = leerie._gating_issues(out)
        assert not any(i.startswith("NO_PLANNED_FILES_TOUCHED") for i in gating), (
            "a planner's guess about which files a subtask would touch must "
            f"not drive a re-drive; gating set was {gating}")

    def test_a_real_issue_still_gates(self, leerie):
        """Anti-vacuity: the partition must not swallow everything."""
        out = leerie.check_implementer_output(
            {"status": "complete",
             "criteria_results": [{"criterion": "the endpoint returns 201",
                                   "met": False, "evidence": "not done"}]},
            {"id": "feat-001", "files_likely_touched": ["src/a.ts"]},
            {"src/b.ts"})
        gating = leerie._gating_issues(out)
        assert any(i.startswith("UNMET_CRITERION") for i in gating), gating

    def test_the_caller_gates_on_the_subset(self, leerie):
        src = inspect.getsource(leerie._settle_subtask)
        assert "_gating_issues(impl_issues)" in src
        assert "if _gating and confidence_retries" in src, (
            "the retry decision must read the gating subset, not every label")


class TestContinuationCheckpoint:
    def test_the_instruction_is_conditional(self, leerie):
        src = inspect.getsource(leerie._run_implementer)
        assert ".is_file()" in src, (
            "the checkpoint instruction must be conditional on the file "
            "existing — most continuations have none")

    def test_both_branches_exist(self, leerie):
        src = inspect.getsource(leerie._run_implementer)
        assert "There is no checkpoint file" in src, (
            "a continuation without a checkpoint still needs to be told it is "
            "a continuation")


class TestLayerGapEnvKeywords:
    def test_credential_is_not_a_keyword(self, leerie):
        assert "credential" not in leerie._ENV_TAG_KEYWORDS, (
            "'credential' matches a domain concept, not env-file plumbing")

    @pytest.mark.parametrize("tag", [
        "credential-field-docurl-support",   # real: a form-field docUrl
        "passare-credential-check-helper",   # real: a client-side API ping
    ])
    def test_the_real_false_positives_no_longer_match(self, leerie, tag):
        assert not any(k in tag.lower() for k in leerie._ENV_TAG_KEYWORDS)

    @pytest.mark.parametrize("tag", [
        "env-contract-documented", "app-bootstrap-config", "secret-rotation",
        "config-key-registry",
    ])
    def test_genuine_env_tags_still_match(self, leerie, tag):
        """Anti-vacuity: narrowing must not disable the heuristic."""
        assert any(k in tag.lower() for k in leerie._ENV_TAG_KEYWORDS)
