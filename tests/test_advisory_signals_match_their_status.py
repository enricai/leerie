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

import ast
import inspect
import textwrap

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
    """Pinned via `ast`, not substring presence.

    The first version asserted `".is_file()" in src` — true of any of the
    eleven other `is_file()` calls in the module — and that the else-branch
    string appeared *somewhere*. Both survive `if _ckpt.is_file() or
    continuation:`, which makes the instruction unconditional again and
    restores the 11 wasted `tool-fail` reads.
    """

    @staticmethod
    def _checkpoint_if(leerie) -> ast.If:
        """The `if` whose body emits the read-the-checkpoint instruction."""
        fn = ast.parse(textwrap.dedent(
            inspect.getsource(leerie._run_implementer))).body[0]
        for node in ast.walk(fn):
            if not isinstance(node, ast.If):
                continue
            body = "".join(ast.unparse(s) for s in node.body)
            if "checkpoint" in body.lower() and "_ckpt" in ast.unparse(node.test):
                return node
        raise AssertionError("no checkpoint-conditional found")

    def test_the_test_is_exactly_the_file_existing(self, leerie):
        node = self._checkpoint_if(leerie)
        assert ast.unparse(node.test) == "_ckpt.is_file()", (
            "the instruction must be gated on the checkpoint EXISTING and "
            f"nothing else; got {ast.unparse(node.test)!r} — an `or` here "
            "makes it unconditional again")

    def test_the_else_branch_is_a_real_else(self, leerie):
        node = self._checkpoint_if(leerie)
        assert node.orelse, "a continuation with no checkpoint still needs telling"
        assert "There is no checkpoint file" in "".join(
            ast.unparse(s) for s in node.orelse)


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
