"""Regressions for the four defects the 0.28.0 run corpus surfaced.

Each test names the run it was derived from. The corpus lives in
`~/.leerie/<repo>/runs/<id>/`, so these encode the *shape* rather than
depending on it being present.
"""

from __future__ import annotations

import pytest

# The `leerie` fixture comes from tests/conftest.py — session-scoped and
# loaded via importlib from the canonical path. Do not shadow it with a
# local fixture or a sys.path insert: every other module would then be
# asserting against a different module object than this one.


# --------------------------------------------------------------------- #
# Path comparison — run bfba2c88 (repo root IS a subdirectory)
# --------------------------------------------------------------------- #

class TestPathsDesignateSameFile:

    @pytest.mark.parametrize("a,b", [
        ("server/x.ts", "fema-demo/server/x.ts"),
        ("fema-demo/server/x.ts", "server/x.ts"),
        ("./src/a.ts", "src/a.ts"),
        ("a/b/c.ts", "b/c.ts"),
        ("/src/a.ts", "src/a.ts"),
    ])
    def test_equal_modulo_a_repo_root_prefix(self, leerie, a, b):
        assert leerie._paths_designate_same_file(a, b)

    @pytest.mark.parametrize("a,b", [
        ("server/x.ts", "other-server/x.ts"),
        ("x.ts", "prefix-x.ts"),          # component boundary, not substring
        ("a/z.ts", "a/y.ts"),
        ("src/X.ts", "src/x.ts"),         # case is never folded
    ])
    def test_distinct_files_stay_distinct(self, leerie, a, b):
        assert not leerie._paths_designate_same_file(a, b)

    def test_symmetric(self, leerie):
        """Set-intersection callers rely on this; an asymmetric comparison
        would make a flag depend on subtask id sort order."""
        for a, b in [("server/x.ts", "fema-demo/server/x.ts"),
                     ("a/z.ts", "a/y.ts")]:
            assert (leerie._paths_designate_same_file(a, b)
                    == leerie._paths_designate_same_file(b, a))


class TestDeclaredPathCovers:

    @pytest.mark.parametrize("declared,touched", [
        ("server", "fema-demo/server/routes/x.ts"),   # prefix on the diff side
        ("fema-demo/src", "src/pages/y.tsx"),         # prefix on the decl side
        ("scripts", "scripts/build.sh"),
        ("server/routes/x.ts", "server/routes/x.ts"),
    ])
    def test_covers(self, leerie, declared, touched):
        assert leerie._declared_path_covers(declared, touched)

    def test_unrelated_tree_is_not_covered(self, leerie):
        assert not leerie._declared_path_covers("server", "client/api/x.ts")
        assert not leerie._declared_path_covers(
            "fema-demo/server/routes/parcel.ts", "fema-demo/src/lib/chat.ts")


# --------------------------------------------------------------------- #
# Under-scope — run c1f45fd0 feat-005 reported `complete` while
# server/routes/parcel.ts did not exist anywhere in the tree.
# --------------------------------------------------------------------- #

class TestUnderScopeDecision:
    """The predicate `check_diff_scope` applies. Asserts the DECISION on the
    five subtasks measured as landing none of their declared files — three
    true under-deliveries and two detector artifacts."""

    @staticmethod
    def _warns(leerie, declared, touched):
        return not any(leerie._declared_path_covers(d, t)
                       for d in declared for t in touched)

    def test_parcel_route_never_created_warns(self, leerie):
        assert self._warns(
            leerie,
            ["fema-demo/server/index.ts",
             "fema-demo/server/routes/parcel.test.ts"],
            ["fema-demo/src/lib/agentChat.ts",
             "fema-demo/src/pages/AssistantChat.tsx"])

    def test_changelog_never_emitted_warns(self, leerie):
        assert self._warns(leerie,
                           ["fema-demo/docs/changelog.d/G2e.md"],
                           ["fema-demo/src/app/scoring.tsx"])

    def test_directory_shaped_declaration_does_not_warn(self, leerie):
        """Run 5fa2052b config-005 ('run biome across the whole tree')
        declared ['scripts', 'server']. A file-only comparison reads a
        tree-wide reformat as touching nothing it declared."""
        assert not self._warns(leerie, ["scripts", "server"],
                               ["server/routes/vault.ts", "scripts/build.sh"])

    def test_ordinary_subtask_does_not_warn(self, leerie):
        assert not self._warns(leerie, ["src/scraper/flow-runner.ts"],
                               ["src/scraper/flow-runner.ts"])


# --------------------------------------------------------------------- #
# DROP_BREAKS_GRAPH — run bfba2c88 withdrew correct findings because the
# check ignored surviving providers of the same tag.
# --------------------------------------------------------------------- #

def _plan(subs):
    return [{"domain": "feature-implementation", "subtasks": subs}]


def _s(sid, provides=(), requires=(), files=()):
    return {"id": sid, "title": sid, "intent": "", "files_likely_touched":
            list(files), "provides": list(provides),
            "requires": [{"tag": t, "extent": "in_plan"} for t in requires],
            "depends_on": [], "size": "small"}


class TestDropBreaksGraphSurvivorSubtraction:

    def test_no_flag_when_a_survivor_still_provides_the_tag(
            self, leerie, tmp_path):
        """The duplicate-provider case: dropping one half cannot orphan a tag
        the other half provides by definition. Measured 3 of 8 candidate
        drops in the corpus flagged purely this way."""
        plans = _plan([
            _s("feat-002", provides=["perplexity-source"], files=["a.ts"]),
            _s("bugfix-002", provides=["perplexity-source"], files=["b.ts"]),
            _s("feat-003", requires=["perplexity-source"], files=["c.ts"]),
        ])
        out = {"collisions": [{
            "a_sid": "feat-002", "b_sid": "bugfix-002",
            "resolution": "drop_a", "artifact": "perplexity source",
            "artifact_paths": [], "merge_feasibility": "", "reason": "dup"}]}
        issues = leerie.check_overlap_judge_output(out, plans, tmp_path)
        assert not [i for i in issues if i.startswith("DROP_BREAKS_GRAPH")]

    def test_genuine_orphan_still_flags(self, leerie, tmp_path):
        """Run 3e65e793's bugfix-002 provides a tag nothing else provides —
        the checker was RIGHT there, and must stay right."""
        plans = _plan([
            _s("bugfix-002", provides=["poll-fixed"], files=["a.ts"]),
            _s("feat-004", provides=["poll-widened"], files=["a.ts"]),
            _s("bugfix-007", requires=["poll-fixed"], files=["c.ts"]),
        ])
        out = {"collisions": [{
            "a_sid": "bugfix-002", "b_sid": "feat-004",
            "resolution": "drop_a", "artifact": "transition poll",
            "artifact_paths": [], "merge_feasibility": "", "reason": "sub"}]}
        issues = leerie.check_overlap_judge_output(out, plans, tmp_path)
        assert [i for i in issues if i.startswith("DROP_BREAKS_GRAPH")]


class TestNoFileOverlapExemptsSharedTags:

    def test_shared_provides_tag_is_its_own_grounding(self, leerie, tmp_path):
        """Run 3e65e793: the judge saw three subtasks declare one tag under
        three invented filenames and recorded that it was 'withdrawing these
        per the NO_FILE_OVERLAP signal'."""
        plans = _plan([
            _s("feat-007", provides=["primitive-tested"], files=["x.test.ts"]),
            _s("bugfix-006", provides=["primitive-tested"], files=["y.test.ts"]),
        ])
        out = {"collisions": [{
            "a_sid": "feat-007", "b_sid": "bugfix-006",
            "resolution": "merge", "artifact": "primitive test",
            "artifact_paths": [], "merge_feasibility": "both author it",
            "reason": "dup"}]}
        issues = leerie.check_overlap_judge_output(out, plans, tmp_path)
        assert not [i for i in issues if i.startswith("NO_FILE_OVERLAP")]

    def test_unrelated_pair_with_no_shared_tag_still_flags(
            self, leerie, tmp_path):
        plans = _plan([
            _s("feat-001", provides=["alpha"], files=["x.ts"]),
            _s("feat-002", provides=["beta"], files=["y.ts"]),
        ])
        out = {"collisions": [{
            "a_sid": "feat-001", "b_sid": "feat-002",
            "resolution": "merge", "artifact": "something",
            "artifact_paths": [], "merge_feasibility": "", "reason": "?"}]}
        issues = leerie.check_overlap_judge_output(out, plans, tmp_path)
        assert [i for i in issues if i.startswith("NO_FILE_OVERLAP")]


# --------------------------------------------------------------------- #
# Transitive predecessors
# --------------------------------------------------------------------- #

class TestFloorIsTotalOnRawPlannerOutput:
    """`check_duplicate_providers` runs on every path, above every skip flag,
    on planner output `_validate_plan` has not seen yet — so it must not
    raise. Its own field reads are `or []`-guarded for that reason.

    Adding the no-overlap tier's ordering exclusion made it call
    `_build_predecessor_graph`, whose reads are NOT guarded
    (`s.get("depends_on", [])` returns None for a present-but-null key), which
    turned three previously-tolerated shapes into a TypeError that would kill
    the run at phase 2¾. The call site normalizes nulls rather than changing
    the shared builder, which eleven other DAG consumers depend on."""

    @staticmethod
    def _pair(**overrides):
        a = {"id": "a", "title": "a", "provides": ["t"],
             "files_likely_touched": ["x.ts"], "requires": [],
             "depends_on": [], **overrides}
        b = {"id": "b", "title": "b", "provides": ["t"],
             "files_likely_touched": ["y.ts"], "requires": [],
             "depends_on": []}
        return [{"subtasks": [a, b]}]

    @pytest.mark.parametrize("field", ["provides", "depends_on", "requires"])
    def test_null_valued_field_does_not_raise(self, leerie, field):
        leerie.check_duplicate_providers(self._pair(**{field: None}))

    def test_all_three_null_at_once(self, leerie):
        leerie.check_duplicate_providers(
            self._pair(provides=None, depends_on=None, requires=None))

    def test_missing_keys_entirely(self, leerie):
        plans = [{"subtasks": [
            {"id": "a", "provides": ["t"], "files_likely_touched": ["x.ts"]},
            {"id": "b", "provides": ["t"], "files_likely_touched": ["y.ts"]}]}]
        assert len(leerie.check_duplicate_providers(plans)) == 1

    def test_normalization_does_not_defeat_the_ordering_exclusion(
            self, leerie):
        """The null-normalized view must still carry real edges through —
        otherwise the exclusion silently stops working and every ordered
        umbrella-tag chain starts flagging."""
        ordered = [{"subtasks": [
            {"id": "docs-001", "title": "d1", "provides": ["umbrella", "a"],
             "files_likely_touched": ["A.md"], "requires": [],
             "depends_on": []},
            {"id": "docs-002", "title": "d2", "provides": ["umbrella"],
             "files_likely_touched": ["B.md"],
             "requires": [{"tag": "a", "extent": "in_plan"}],
             "depends_on": ["docs-001"]}]}]
        assert leerie.check_duplicate_providers(ordered) == []


class TestTransitivePredecessors:

    def test_closure_is_transitive(self, leerie):
        preds = {"a": set(), "b": {"a"}, "c": {"b"}}
        assert leerie._transitive_predecessors(preds)["c"] == {"a", "b"}

    def test_terminates_on_a_cycle(self, leerie):
        """Phase 2½ is what rejects cycles; this runs before it and must not
        recurse forever on one."""
        preds = {"a": {"b"}, "b": {"a"}}
        out = leerie._transitive_predecessors(preds)
        assert out["a"] == {"a", "b"} and out["b"] == {"a", "b"}
