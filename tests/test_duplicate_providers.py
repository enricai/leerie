"""The deterministic duplicate-provider floor beneath `phase_overlap_judge`
(DESIGN §5 *A deterministic floor underneath the judge*).

`check_duplicate_providers` flags two subtasks that declare the SAME
`provides` tag and whose `files_likely_touched` intersect — two subtasks
doing the same work to the same file. Pure set logic over structured planner
fields; no prose is read (CLAUDE.md *Language-to-JSON*).

This file mirrors `tests/test_prescribed_cmd_coverage.py`: the floor in
isolation, driven by synthetic plans plus the two real incident shapes, with
the committed wiring corpus used as the false-positive control.

The `_cofile_cluster` exclusion gets its own class because it is the single
load-bearing decision in the rule, not a refinement — see
`TestCofileClusterExclusion`.
"""
from __future__ import annotations

import copy
import inspect
import json
from pathlib import Path

import pytest

_CORPUS = (Path(__file__).resolve().parent
           / "fixtures" / "wiring_repair_corpus" / "corpus.json")


def _sub(sid, *, provides=(), files=(), cluster=None, depends_on=()):
    s = {
        "id": sid, "title": sid, "intent": f"intent {sid}",
        "success_criteria_seed": "c", "runs_commands": [],
        "files_likely_touched": list(files), "provides": list(provides),
        "requires": [], "depends_on": list(depends_on), "size": "small",
    }
    if cluster is not None:
        s["_cofile_cluster"] = cluster
    return s


def _plans(*subs_by_domain):
    return [{"domain": d, "status": "ready", "subtasks": list(ss)}
            for d, ss in subs_by_domain]


# --------------------------------------------------------------------- #
# The two real incidents this floor exists for
# --------------------------------------------------------------------- #

class TestIncidentShapes:

    def test_392b5e7f_shape_fires(self, leerie):
        """funeralworks run `392b5e7f`: a coverage-gate re-plan reintroduced a
        `bugfix-` domain duplicating work the `feat-` domain already owned.
        Nothing re-detected it and the run died at the phase-3 wiring gate."""
        plans = _plans(
            ("bug-fixing", [
                _sub("bugfix-005", provides=["aftercare-flag-hook"],
                     files=["src/lib/billing/use-aftercare-flag.ts"]),
            ]),
            ("feature-implementation", [
                _sub("feat-007", provides=["aftercare-flag-hook"],
                     files=["src/lib/billing/use-aftercare-flag.ts"]),
            ]),
        )
        issues = leerie.check_duplicate_providers(plans)
        assert len(issues) == 1
        assert "bugfix-005" in issues[0] and "feat-007" in issues[0]
        assert "aftercare-flag-hook" in issues[0]
        assert "use-aftercare-flag.ts" in issues[0]

    def test_19a70d96_shape_fires(self, leerie):
        """`_self` run `19a70d96`: two subtasks did the same migration, the
        integrator merged both DESIGN.md paragraphs, and the integration gate
        refused — after 4.7 hours and 164 workers. The `wiring_judge` scored
        that run CLEAN, which is exactly why a mechanical floor is needed."""
        plans = _plans(
            ("refactoring", [
                _sub("refactor-001", provides=["task-file-coverage-worker"],
                     files=["docs/DESIGN.md", "docs/IMPLEMENTATION.md"]),
            ]),
            ("feature-implementation", [
                _sub("feat-001", provides=["task-file-coverage-worker"],
                     files=["docs/DESIGN.md", "docs/IMPLEMENTATION.md"]),
            ]),
        )
        issues = leerie.check_duplicate_providers(plans)
        assert len(issues) == 1
        assert "refactor-001" in issues[0] and "feat-001" in issues[0]

    def test_a_pair_colliding_on_two_tags_reports_both(self, leerie):
        """`19a70d96`'s refactor-001/feat-001 pair shared TWO tags. Collapsing
        them into one message would understate the overlap."""
        plans = _plans(
            ("refactoring", [
                _sub("refactor-001",
                     provides=["task-file-coverage-worker",
                               "task-file-coverage-schema"],
                     files=["docs/DESIGN.md"]),
            ]),
            ("feature-implementation", [
                _sub("feat-001",
                     provides=["task-file-coverage-worker",
                               "task-file-coverage-schema"],
                     files=["docs/DESIGN.md"]),
            ]),
        )
        assert len(leerie.check_duplicate_providers(plans)) == 2


# --------------------------------------------------------------------- #
# The load-bearing exclusion
# --------------------------------------------------------------------- #

class TestCofileClusterExclusion:
    """Sub-file region splits share a tag and a path BY CONSTRUCTION (DESIGN
    §5½ (P1) *Sub-file*). Without this exclusion the rule is unusable, not
    merely noisy."""

    def test_split_siblings_are_never_flagged(self, leerie):
        plans = _plans(("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], files=["big.ts"],
                 cluster="feat-001"),
            _sub("feat-001-r2", provides=["baked"], files=["big.ts"],
                 cluster="feat-001"),
            _sub("feat-001-r3", provides=["baked"], files=["big.ts"],
                 cluster="feat-001"),
        ]))
        assert leerie.check_duplicate_providers(plans) == []

    def test_the_same_plan_without_the_marker_fires_on_every_pair(
            self, leerie):
        """Anti-vacuity: proves the silence above comes from the exclusion,
        not from the shape being unmatchable."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], files=["big.ts"]),
            _sub("feat-001-r2", provides=["baked"], files=["big.ts"]),
            _sub("feat-001-r3", provides=["baked"], files=["big.ts"]),
        ]))
        assert len(leerie.check_duplicate_providers(plans)) == 3  # 3 choose 2

    def test_different_clusters_still_fire(self, leerie):
        """The exclusion is 'the same cluster', not 'has a cluster'."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001-r1", provides=["baked"], files=["big.ts"],
                 cluster="feat-001"),
            _sub("feat-002-r1", provides=["baked"], files=["big.ts"],
                 cluster="feat-002"),
        ]))
        assert len(leerie.check_duplicate_providers(plans)) == 1

    def test_none_cluster_is_absence_not_a_shared_group(self, leerie):
        """Two subtasks whose `_cofile_cluster` is explicitly None must not
        read as members of one cluster named None."""
        a = _sub("feat-001", provides=["baked"], files=["big.ts"])
        b = _sub("feat-002", provides=["baked"], files=["big.ts"])
        a["_cofile_cluster"] = None
        b["_cofile_cluster"] = None
        assert len(leerie.check_duplicate_providers(
            _plans(("feature-implementation", [a, b])))) == 1


# --------------------------------------------------------------------- #
# Silence — the property that makes it shippable
# --------------------------------------------------------------------- #

class TestStaysSilent:

    def test_same_tag_but_disjoint_files(self, leerie):
        """A shared capability tag with no file overlap is ordinary
        cross-domain vocabulary, not duplicated work."""
        plans = _plans(
            ("feature-implementation", [
                _sub("feat-001", provides=["api-ready"], files=["a.ts"])]),
            ("testing", [
                _sub("test-001", provides=["api-ready"], files=["b.ts"])]),
        )
        assert leerie.check_duplicate_providers(plans) == []

    def test_same_files_but_different_tags(self, leerie):
        """Two subtasks editing one file for different reasons is the
        deliberately-permissive case `_warn_cross_planner_file_overlap`
        already covers advisorily."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["header"], files=["page.tsx"]),
            _sub("feat-002", provides=["footer"], files=["page.tsx"]),
        ]))
        assert leerie.check_duplicate_providers(plans) == []

    def test_single_provider_per_tag(self, leerie):
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["a"], files=["x.ts"]),
            _sub("feat-002", provides=["b"], files=["x.ts"]),
        ]))
        assert leerie.check_duplicate_providers(plans) == []

    @pytest.mark.parametrize("plans", [
        [],
        [{"domain": "d", "status": "ready", "subtasks": []}],
        [{"domain": "d", "status": "ready"}],
    ], ids=["no-plans", "no-subtasks", "missing-subtasks-key"])
    def test_degenerate_plans(self, leerie, plans):
        assert leerie.check_duplicate_providers(plans) == []

    def test_ordered_pairs_are_still_flagged(self, leerie):
        """Deliberately NOT exempted: measured zero ordered pairs across the
        corpus, so an exemption would be untested speculation widening the
        rule's escape hatches (DESIGN §5). If this ever changes, it is a
        decision to make with evidence — not a bug in this test."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["hook"], files=["h.ts"]),
            _sub("feat-002", provides=["hook"], files=["h.ts"],
                 depends_on=["feat-001"]),
        ]))
        assert len(leerie.check_duplicate_providers(plans)) == 1


# --------------------------------------------------------------------- #
# Input hygiene
# --------------------------------------------------------------------- #

class TestInputHandling:

    def test_paths_are_normalized_before_comparison(self, leerie):
        """`./src/x.ts` and `src/x.ts` are the same file; both sides are
        planner-authored strings, so they must not read as disjoint."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["hook"], files=["./src/x.ts"]),
            _sub("feat-002", provides=["hook"], files=["src/x.ts"]),
        ]))
        assert len(leerie.check_duplicate_providers(plans)) == 1

    def test_a_leading_slash_does_not_hide_a_duplicate(self, leerie):
        """Regression pin for the normalizer swap. `os.path.normpath` KEEPS a
        leading `/`, so this pair read as disjoint and the duplicate was
        missed. `_normalize_artifact_path` — the helper the sibling
        `NO_FILE_OVERLAP` check already uses — strips it."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["hook"], files=["/src/x.ts"]),
            _sub("feat-002", provides=["hook"], files=["src/x.ts"]),
        ]))
        assert len(leerie.check_duplicate_providers(plans)) == 1

    def test_empty_provides_tags_are_ignored(self, leerie):
        """A blank tag is not a shared capability. Mirrors the same guard in
        `_repair_missing_requires`, whose schema also permits an empty
        `tag_or_dep`."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=[""], files=["x.ts"]),
            _sub("feat-002", provides=[""], files=["x.ts"]),
        ]))
        assert leerie.check_duplicate_providers(plans) == []

    def test_case_is_not_folded(self, leerie):
        """Container checkouts are case-sensitive — matching
        `_normalize_artifact_path`'s documented behavior."""
        plans = _plans(("feature-implementation", [
            _sub("feat-001", provides=["hook"], files=["src/X.ts"]),
            _sub("feat-002", provides=["hook"], files=["src/x.ts"]),
        ]))
        assert leerie.check_duplicate_providers(plans) == []

    def test_blank_and_non_string_entries_are_tolerated(self, leerie):
        a = _sub("feat-001", files=["  ", "", "x.ts"])
        a["provides"] = ["", "   ", "hook", 7, None]
        b = _sub("feat-002", files=[None, 3, "x.ts"])
        b["provides"] = ["hook"]
        issues = leerie.check_duplicate_providers(
            _plans(("feature-implementation", [a, b])))
        assert len(issues) == 1 and "hook" in issues[0]

    def test_subtasks_without_an_id_are_skipped(self, leerie):
        a = _sub("feat-001", provides=["hook"], files=["x.ts"])
        b = _sub("feat-002", provides=["hook"], files=["x.ts"])
        del b["id"]
        assert leerie.check_duplicate_providers(
            _plans(("feature-implementation", [a, b]))) == []

    def test_output_is_deterministic(self, leerie):
        """`_schedule()` is documented deterministic; a floor whose message
        order depended on dict iteration would make run logs non-reproducible."""
        plans = _plans(("feature-implementation", [
            _sub("feat-003", provides=["b", "a"], files=["x.ts"]),
            _sub("feat-001", provides=["a", "b"], files=["x.ts"]),
            _sub("feat-002", provides=["a", "b"], files=["x.ts"]),
        ]))
        first = leerie.check_duplicate_providers(copy.deepcopy(plans))
        for _ in range(5):
            assert leerie.check_duplicate_providers(
                copy.deepcopy(plans)) == first


# --------------------------------------------------------------------- #
# False-positive control, against the committed corpus
# --------------------------------------------------------------------- #

class TestCorpusFalsePositives:
    """The measured zero-false-positive property, reproducible offline.

    Measured 2026-08-03 across all 52 runs with a usable plan: 9 flagged
    pairs in exactly 2 runs, both destroyed by duplicate work. The six runs
    committed under `fixtures/wiring_repair_corpus/` are all clean, and two
    of them are the reason the cluster exclusion exists.
    """

    def _runs(self):
        return json.loads(_CORPUS.read_text())

    @pytest.mark.parametrize("run", sorted(json.loads(_CORPUS.read_text())))
    def test_every_corpus_run_is_clean(self, leerie, run):
        plans = copy.deepcopy(self._runs()[run]["plans"])
        assert leerie.check_duplicate_providers(plans) == []

    @pytest.mark.parametrize("run,expected", [("62a19deb", 1752),
                                              ("ad69057f", 165)])
    def test_stripping_the_cluster_marker_floods_these_runs(
            self, leerie, run, expected):
        """The exclusion's whole justification, as a number. Both runs are
        heavy sub-file splits; without the marker the rule buries a real
        finding under four orders of magnitude of noise."""
        plans = copy.deepcopy(self._runs()[run]["plans"])
        for p in plans:
            for s in p.get("subtasks", []) or []:
                s.pop("_cofile_cluster", None)
        assert len(leerie.check_duplicate_providers(plans)) == expected


# --------------------------------------------------------------------- #
# Wiring — verifiable only by source inspection
# --------------------------------------------------------------------- #

class TestWiring:

    def test_floor_runs_before_every_skip_in_phase_overlap_judge(self, leerie):
        """A mechanical check a skip flag can switch off is not a floor.

        The call must precede the `--skip-overlap-judge` opt-out AND the
        single-planner / <2-subtask cheap-skips, because those are exactly
        the paths on which the judge never runs.
        """
        src = inspect.getsource(leerie.phase_overlap_judge)
        call = src.index("check_duplicate_providers(")
        for marker in ('st.data.get("skip_overlap_judge")',
                       "contributing_domains) < 2",
                       "total_subtasks < 2"):
            assert call < src.index(marker), (
                f"the floor must run before {marker!r} — that skip is one of "
                "the paths the floor exists to cover")

    def test_floor_is_advisory_not_gating(self, leerie):
        """Shipped advisory pending confirmation across live runs (DESIGN §5).
        Promoting it to a `die()` is a deliberate follow-up, not a drive-by."""
        src = inspect.getsource(leerie.phase_overlap_judge)
        after = src[src.index("check_duplicate_providers("):]
        window = after[:400]
        assert "die(" not in window
        assert "log(" in window
