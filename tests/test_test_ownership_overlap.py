"""The deterministic test-ownership floor beneath `TEST_OWNERSHIP_RISK`
(`check_classifier_output`, leerie.py:5798) and `phase_overlap_judge`
(DESIGN §5 *Cross-domain surface overlap*).

`check_test_ownership_overlap` flags a `test-`-domain subtask and a code-
domain subtask (`bugfix-`/`feat-`/`refactor-`) whose `files_likely_touched`
intersect — running blind to each other, both can author the same test
file with incompatible contracts. Pure set logic over structured planner
fields; mirrors `check_duplicate_providers`
(`tests/test_duplicate_providers.py`).
"""
from __future__ import annotations


def _sub(sid, *, files=(), provides=(), requires=(), depends_on=()):
    return {
        "id": sid, "title": sid, "intent": f"intent {sid}",
        "success_criteria_seed": "c", "runs_commands": [],
        "files_likely_touched": list(files), "provides": list(provides),
        "requires": list(requires), "depends_on": list(depends_on),
        "size": "small",
    }


def _plans(*subs_by_domain):
    return [{"domain": d, "status": "ready", "subtasks": list(ss)}
            for d, ss in subs_by_domain]


class TestFiresOnSharedFile:

    def test_test_and_bugfix_domain_share_a_file(self, leerie):
        plans = _plans(
            ("testing", [
                _sub("test-001", files=["src/lib/foo.test.ts"]),
            ]),
            ("bug-fixing", [
                _sub("bugfix-001", files=["src/lib/foo.test.ts"]),
            ]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert len(issues) == 1
        assert "test-001" in issues[0] and "bugfix-001" in issues[0]
        assert "foo.test.ts" in issues[0]
        assert "TEST_OWNERSHIP_OVERLAP" in issues[0]

    def test_test_and_feat_domain_share_a_file(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-002", files=["src/x.ts"])]),
            ("feature-implementation", [_sub("feat-002", files=["src/x.ts"])]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert len(issues) == 1
        assert "test-002" in issues[0] and "feat-002" in issues[0]

    def test_test_and_refactor_domain_share_a_file(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-003", files=["src/y.ts"])]),
            ("refactoring", [_sub("refactor-003", files=["src/y.ts"])]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert len(issues) == 1
        assert "test-003" in issues[0] and "refactor-003" in issues[0]

    def test_normalizes_leading_slash(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-004", files=["/src/z.ts"])]),
            ("bug-fixing", [_sub("bugfix-004", files=["src/z.ts"])]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert len(issues) == 1

    def test_multiple_shared_files_reported_together(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-005", files=["a.ts", "b.ts"])]),
            ("bug-fixing", [_sub("bugfix-005", files=["a.ts", "b.ts"])]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert len(issues) == 1
        assert "a.ts" in issues[0] and "b.ts" in issues[0]


class TestSilentWhenDisjoint:

    def test_disjoint_files_stay_silent(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-001", files=["src/lib/foo.test.ts"])]),
            ("bug-fixing", [_sub("bugfix-001", files=["src/lib/foo.ts"])]),
        )
        assert leerie.check_test_ownership_overlap(plans) == []

    def test_no_testing_domain_stays_silent(self, leerie):
        plans = _plans(
            ("bug-fixing", [_sub("bugfix-001", files=["a.ts"])]),
            ("feature-implementation", [_sub("feat-001", files=["a.ts"])]),
        )
        assert leerie.check_test_ownership_overlap(plans) == []

    def test_no_code_domain_stays_silent(self, leerie):
        plans = _plans(
            ("testing", [
                _sub("test-001", files=["a.ts"]),
                _sub("test-002", files=["a.ts"]),
            ]),
        )
        assert leerie.check_test_ownership_overlap(plans) == []

    def test_empty_files_stays_silent(self, leerie):
        plans = _plans(
            ("testing", [_sub("test-001", files=[])]),
            ("bug-fixing", [_sub("bugfix-001", files=[])]),
        )
        assert leerie.check_test_ownership_overlap(plans) == []

    def test_infrastructure_domain_not_flagged_as_code(self, leerie):
        # `infra-` is not one of the three TEST_OWNERSHIP_RISK code
        # categories (bug-fixing / feature-implementation / refactoring).
        plans = _plans(
            ("testing", [_sub("test-001", files=["a.ts"])]),
            ("infrastructure", [_sub("infra-001", files=["a.ts"])]),
        )
        assert leerie.check_test_ownership_overlap(plans) == []

    def test_empty_plans_stays_silent(self, leerie):
        assert leerie.check_test_ownership_overlap([]) == []


class TestFalsification:

    def test_disabling_the_check_fails_the_positive_case(self, leerie):
        """The floor as shipped must actually fire on the collision shape —
        this is the same assertion as TestFiresOnSharedFile, kept separate
        per the subtask's own falsification instruction: disable the check
        (return []) and confirm the test would fail."""
        plans = _plans(
            ("testing", [_sub("test-001", files=["src/lib/foo.test.ts"])]),
            ("bug-fixing", [_sub("bugfix-001", files=["src/lib/foo.test.ts"])]),
        )
        issues = leerie.check_test_ownership_overlap(plans)
        assert issues != []
