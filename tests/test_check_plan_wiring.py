"""Unit tests for `check_plan_wiring` (DESIGN §5 *A wiring re-check on the
fully-merged plan*, §8).

Pure-function structural dangle detector that replays validate_plan's two
id/tag-channel checks on the merged post-drop plan (sid->subtask dict) and
returns a list of wiring-specific messages ([] when clean). It front-runs
validate_plan's generic die with an actionable one; validate_plan stays the
backstop.

Mirrors test_remap_vanished_deps.py's pure-function style.
"""
from __future__ import annotations


def _req(tag: str, extent: str = "in_plan") -> dict:
    return {"tag": tag, "extent": extent}


def _sub(sid: str, *, deps=None, requires=None, provides=None) -> dict:
    return {
        "id": sid,
        "depends_on": list(deps or []),
        "requires": list(requires or []),
        "provides": list(provides or []),
    }


def _plan(*subs: dict) -> dict:
    return {s["id"]: s for s in subs}


class TestClean:
    def test_empty_plan_is_clean(self, leerie):
        assert leerie.check_plan_wiring({}) == []

    def test_independent_subtasks_are_clean(self, leerie):
        plan = _plan(_sub("feat-001"), _sub("feat-002"))
        assert leerie.check_plan_wiring(plan) == []

    def test_resolved_depends_on_is_clean(self, leerie):
        plan = _plan(_sub("feat-001"), _sub("feat-002", deps=["feat-001"]))
        assert leerie.check_plan_wiring(plan) == []

    def test_resolved_requires_tag_is_clean(self, leerie):
        plan = _plan(
            _sub("feat-001", provides=["schema"]),
            _sub("feat-002", requires=[_req("schema")]),
        )
        assert leerie.check_plan_wiring(plan) == []

    def test_external_requires_is_not_checked(self, leerie):
        """extent: external is declared out-of-graph — no provider needed."""
        plan = _plan(_sub("feat-001", requires=[_req("k8s", "external")]))
        assert leerie.check_plan_wiring(plan) == []


class TestDependsOnDangle:
    def test_dangling_depends_on_is_flagged(self, leerie):
        """A merge/rename/drop vanished feat-001 without rewriting the
        inbound depends_on."""
        plan = _plan(_sub("feat-002", deps=["feat-001"]))
        issues = leerie.check_plan_wiring(plan)
        assert len(issues) == 1
        assert "feat-002" in issues[0]
        assert "feat-001" in issues[0]
        assert "depends_on" in issues[0]

    def test_multiple_dangles_all_flagged(self, leerie):
        plan = _plan(
            _sub("feat-002", deps=["gone-1"]),
            _sub("feat-003", deps=["gone-2"]),
        )
        assert len(leerie.check_plan_wiring(plan)) == 2


class TestRequiresDangle:
    def test_requires_with_no_provider_is_flagged(self, leerie):
        """The satisfied-probe/drop tag-channel dangle: the only provider was
        dropped without pruning the inbound requires."""
        plan = _plan(_sub("feat-002", requires=[_req("dropped-cap")]))
        issues = leerie.check_plan_wiring(plan)
        assert len(issues) == 1
        assert "feat-002" in issues[0]
        assert "dropped-cap" in issues[0]
        assert "requires" in issues[0]

    def test_provider_in_another_subtask_resolves_globally(self, leerie):
        """Provider-existence is a global (cross-domain) property."""
        plan = _plan(
            _sub("feat-002", requires=[_req("cap")]),
            _sub("infra-009", provides=["cap"]),
        )
        assert leerie.check_plan_wiring(plan) == []

    def test_malformed_requires_entry_is_ignored(self, leerie):
        """Shape errors are validate_plan's job, not wiring's — a bare-string
        or tagless entry must not crash the wiring scan."""
        plan = _plan(_sub("feat-001", requires=["not-an-object",
                                                {"extent": "in_plan"}]))
        assert leerie.check_plan_wiring(plan) == []


class TestMixed:
    def test_both_channels_dangle(self, leerie):
        plan = _plan(
            _sub("feat-002", deps=["gone"], requires=[_req("nope")]))
        issues = leerie.check_plan_wiring(plan)
        assert len(issues) == 2

    def test_clean_and_dangling_mix(self, leerie):
        plan = _plan(
            _sub("feat-001", provides=["ok"]),
            _sub("feat-002", requires=[_req("ok")]),          # clean
            _sub("feat-003", requires=[_req("missing")]),     # dangle
        )
        issues = leerie.check_plan_wiring(plan)
        assert len(issues) == 1
        assert "missing" in issues[0]
