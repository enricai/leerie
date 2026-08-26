"""Sub-file split must gate on the parent's declared `change_shape`, must give
exactly one region child new-file ownership, and must have its `owned_region`
enforced mechanically (DESIGN §5½ (P1) *Sub-file*).

All three pins derive from run `719c2a26`, where a ONE-EXPRESSION fix in a
9,140-line file was tiled into 14 regions. Replayed against that run's actual
subtask branches: 11 of 12 region children touched a file outside their region,
7 of 12 edited lines outside their range, and exactly one complied. Two children
independently created the SAME new test file -- an add/add conflict on a path
absent from the merge base, which no merge strategy can resolve without
discarding one side.

Each test here asserts SUBSTANCE, not structure (CLAUDE.md *A test asserting
STRUCTURE must be paired with one asserting SUBSTANCE*): the gate is driven and
its return value asserted; the criteria string is read for the clause's actual
text; the check is driven with a real escaping file set and its issue is
asserted to be gating rather than merely present.
"""
from __future__ import annotations

import pytest


# --- helpers -----------------------------------------------------------------

def _owns(child) -> bool:
    """Structural read of the new-file designation. Preferred over grepping the
    criteria prose: the clause wording is ours to change, the field is the
    contract a re-split actually resolves against."""
    return child.get("_newfile_owner") is True


def _clause_count(leerie, child) -> int:
    """How many ownership clauses the criteria carries. MUST be 1 — the
    re-entry bug was two, and they contradicted each other."""
    return child["success_criteria_seed"].count(leerie._NEWFILE_CLAUSE_MARKER)


def _owners(children) -> list:
    return [c for c in children if _owns(c)]


def _dense_file(tmp_path, lines=2100):
    """A file comfortably over `subfile_split_max_span` (700), so the ONLY
    thing that can keep it from being tiled is the change_shape gate."""
    p = tmp_path / "dense.ts"
    p.write_text("\n".join(f"const x{i} = {i};" for i in range(lines)) + "\n")
    return p


def _subtask(change_shape, *, file="dense.ts"):
    return {
        "id": "bugfix-001",
        "title": "Stop foldReturn being dropped when a paging signal exists",
        "success_criteria_seed": (
            "A new runtime e2e test reproduces the flow and asserts the "
            "generated contract emits the drill call."),
        "intent": "Restore additive fold behavior.",
        "files_likely_touched": [file],
        **({} if change_shape is None else {"change_shape": change_shape}),
    }


# --- C1: the change_shape gate ----------------------------------------------

@pytest.mark.parametrize("shape", ["point", "localized"])
def test_non_sweep_change_shape_is_not_tiled(leerie, tmp_path, shape):
    """The regression itself: a point fix in a huge file must stay a leaf.

    Asserts the RETURN VALUE (no children), not that the constant is consulted
    -- a presence check would pass against a gate that computed the right
    answer and ignored it.
    """
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask(shape), tmp_path, 700)
    assert children == [], (
        f"change_shape={shape!r} was tiled into {len(children)} region "
        "children; a non-sweep change must stay a leaf regardless of file size")


def test_sweep_on_the_same_file_is_still_tiled(leerie, tmp_path):
    """Anti-vacuity: the gate must not be disabling the mechanism outright.

    Same file, same span cap, same everything -- only `change_shape` differs.
    Without this, a `_subfile_split` that returned [] unconditionally would
    pass the test above.
    """
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    assert len(children) > 1, (
        "a declared sweep over an over-cap file must still tile; the gate "
        "must discriminate on change_shape, not suppress all splitting")


def test_absent_change_shape_preserves_pre_gate_behavior(leerie, tmp_path):
    """`reconciler.added_subtasks` and `splitter.children` do not carry the
    field. Absence means "not declared", NOT "refuse to split" -- otherwise the
    gate silently disables the mechanism for those two call sites."""
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask(None), tmp_path, 700)
    assert len(children) > 1


def test_gate_reads_the_named_constant(leerie):
    """The enum's non-sweep members and the gate's constant must not drift
    apart: a value added to the schema but not the frozenset would be tiled."""
    enum = set(leerie._subtask_item_schema(include_change_shape=True)
               ["properties"]["change_shape"]["enum"])
    assert leerie._NON_SWEEP_CHANGE_SHAPES == enum - {"sweep"}


def test_change_shape_is_required_on_planner_subtasks(leerie):
    """Required, not optional -- a planner must not be able to skip an
    attestation a gate depends on by omitting the field (same reasoning as
    `migration_targets.is_real_identifier`)."""
    items = leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
    assert "change_shape" in items["required"]
    assert "change_shape" not in leerie._subtask_item_schema().get(
        "required", []), "must stay absent from the narrower call sites"


def test_migration_child_carries_change_shape_forward(leerie):
    """The peel path builds a `_migration_child` for the dense file, and that
    child re-enters `_subfile_split`. Dropping the field there routes the
    dominant dense-file shape (file + its test file) straight past the gate.

    Asserts the VALUE survives, not that the key exists.
    """
    parent = _subtask("point")
    child = leerie._migration_child(
        parent, ["dense.ts"], "bugfix-001-f1", "t", "c")
    assert child["change_shape"] == "point"


def test_peel_path_child_still_hits_the_gate(leerie, tmp_path):
    """End-to-end for the above: peel a point-fix subtask whose files are the
    dense file plus its test file, then feed the peeled dense child back into
    `_subfile_split`. It must not tile."""
    _dense_file(tmp_path)
    (tmp_path / "dense.test.ts").write_text("test\n")
    parent = _subtask("point")
    parent["files_likely_touched"] = ["dense.ts", "dense.test.ts"]
    peeled = leerie._peel_oversized_file(parent, tmp_path, 700, 8)
    assert peeled, "expected a peel for one oversized file in a small set"
    dense_child = next(c for c in peeled
                       if c["files_likely_touched"] == ["dense.ts"])
    assert leerie._subfile_split(dense_child, tmp_path, 700) == []


# --- C2: exactly one new-file owner -----------------------------------------

def test_exactly_one_region_child_owns_new_files(leerie, tmp_path):
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    owners = _owners(children)
    assert len(owners) == 1, (
        f"{len(owners)} of {len(children)} children claim new-file ownership; "
        "exactly one is what makes the add/add collision impossible")
    assert owners[0]["id"].endswith("-r1")
    assert all(_clause_count(leerie, c) == 1 for c in children)


def test_non_owner_children_are_told_which_sibling_owns_it(leerie, tmp_path):
    """Substance, not presence: the clause must name the OWNER'S ACTUAL ID, so
    a non-owner can declare an edge instead of racing it."""
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    owner_id = children[0]["id"]
    for c in children[1:]:
        seed = c["success_criteria_seed"]
        assert not _owns(c)
        assert "create no new files" in seed.lower()
        assert owner_id in seed, (
            f"{c['id']} is forbidden from creating files but is not told that "
            f"{owner_id} owns them")


def test_children_do_not_all_carry_a_byte_identical_criteria_seed(
        leerie, tmp_path):
    """The direct shape of the incident: in 719c2a26 every region child's
    criteria hashed identically, so all 13 pursued one whole-file mandate."""
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    seeds = {c["success_criteria_seed"] for c in children}
    assert len(seeds) == len(children), (
        "region children share a criteria seed; each must be region-scoped")


# --- C3: owned_region is enforced, and correctively --------------------------

def _complete_result():
    return {
        "subtask_id": "bugfix-001-f1-r13",
        "status": "complete",
        "summary": "done",
        "criteria_results": [],
        "production_evidence": {
            "exercised": True, "how": "ran it", "observed": "ok"},
    }


def test_owned_region_file_escape_is_detected(leerie):
    """r13's real shape: it committed ONE file, and that file was not the one
    it owned -- zero lines of `owned_region["file"]`."""
    subtask = {
        "id": "bugfix-001-f1-r13",
        "files_likely_touched": ["src/scripts/recon-generate.ts"],
        "owned_region": {"file": "src/scripts/recon-generate.ts",
                         "start": 7824, "end": 8396, "symbols": []},
    }
    issues = leerie.check_implementer_output(
        _complete_result(), subtask,
        {"src/scripts/recon-generate-graphql-paginated-primary-fold-"
         "runtime-e2e.test.ts"})
    escapes = [i for i in issues if i.startswith("OWNED_REGION_FILE_ESCAPE")]
    assert len(escapes) == 1, issues
    assert "7824-8396" in escapes[0]
    assert "runtime-e2e.test.ts" in escapes[0], (
        "the feedback must name the offending path, or the implementer cannot "
        "act on it")


def test_owned_region_file_escape_is_gating_not_advisory(leerie):
    """It must reach the retry loop. `NO_PLANNED_FILES_TOUCHED` is advisory
    because `files_likely_touched` is a planner guess; `owned_region["file"]`
    is code-computed by `_subfile_split`, so there is no false-positive
    surface."""
    assert leerie._gating_issues(["OWNED_REGION_FILE_ESCAPE: x"]) == [
        "OWNED_REGION_FILE_ESCAPE: x"]


def test_owned_region_file_escape_is_not_fatal(leerie):
    """RETRACTED-BY-MEASUREMENT pin. Routing this through `check_diff_scope`'s
    return would reach `fail("broken", ...)`, which `_retryable_failure`
    treats as NON-retryable; replayed against 719c2a26 that kills 11 of 12
    subtasks. The check must live in `check_implementer_output`, whose issues
    are corrective."""
    import inspect
    assert not leerie._retryable_failure("broken"), (
        "premise of this pin changed: 'broken' is now retryable")
    src = inspect.getsource(leerie.check_diff_scope)
    assert "OWNED_REGION_FILE_ESCAPE" not in src, (
        "owned_region enforcement must not live in check_diff_scope -- its "
        "return routes to a non-retryable fail('broken', ...)")
    assert "OWNED_REGION_FILE_ESCAPE" in inspect.getsource(
        leerie.check_implementer_output)


def test_compliant_region_child_is_not_flagged(leerie):
    """Anti-vacuity: r7, the one subtask in 719c2a26 that stayed in scope."""
    subtask = {
        "id": "bugfix-001-f1-r7",
        "owned_region": {"file": "src/scripts/recon-generate.ts",
                         "start": 3999, "end": 4198, "symbols": []},
    }
    issues = leerie.check_implementer_output(
        _complete_result(), subtask, {"src/scripts/recon-generate.ts"})
    assert not [i for i in issues
                if i.startswith("OWNED_REGION_FILE_ESCAPE")], issues


def test_subtask_without_owned_region_is_unaffected(leerie):
    """Ordinary subtasks carry no `owned_region`; the check must be inert for
    them, or every non-region subtask starts failing on its own test files."""
    subtask = {"id": "feat-001", "files_likely_touched": ["a.py"]}
    issues = leerie.check_implementer_output(
        _complete_result(), subtask, {"a.py", "a_test.py", "README.md"})
    assert not [i for i in issues
                if i.startswith("OWNED_REGION_FILE_ESCAPE")], issues


# --- C2 extended: the peel and migration-chunk paths carry the same rule -----
#
# Not a region-child path, and therefore NOT backstopped by
# OWNED_REGION_FILE_ESCAPE (these children carry no `owned_region`) -- the
# criteria clause is the whole mechanism, which is why it is pinned here.
#
# Measured on 719c2a26: `bugfix-001-f2` is a PEEL child, not a region child,
# and it carries the same criteria hash (8b372445) as all 12 region children.
# So the peel pair is the identical collision shape at 2-way instead of 14-way,
# and gating the tiling alone would have narrowed the defect rather than fixed
# it.

def test_peel_pair_has_exactly_one_new_file_owner(leerie, tmp_path):
    _dense_file(tmp_path)
    (tmp_path / "dense.test.ts").write_text("test\n")
    parent = _subtask("sweep")
    parent["files_likely_touched"] = ["dense.ts", "dense.test.ts"]

    children = leerie._peel_oversized_file(parent, tmp_path, 700, 8)
    assert len(children) == 2

    owners = _owners(children)
    assert len(owners) == 1
    assert owners[0]["files_likely_touched"] == ["dense.ts"], (
        "the dense-file child must own new files -- it holds the file the work "
        "is about; the sibling exists only to carry the leftovers")

    other = next(c for c in children if c is not owners[0])
    assert "create no new files" in other["success_criteria_seed"].lower()
    assert owners[0]["id"] in other["success_criteria_seed"], (
        "the non-owner must be told which sibling owns new files, by id")


def test_migration_chunks_have_exactly_one_new_file_owner(leerie):
    """Chunk 0 owns them; every other chunk names it. Arbitrary but
    deterministic, matching `_subfile_child`'s rule."""
    parent = _subtask("sweep")
    ids = [f"feat-001-{i + 1}" for i in range(4)]
    children = [
        leerie._migration_child(parent, [f"m{i}.py"], cid, "t",
                                f"criteria for {cid}",
                                newfile_owner_id=ids[0])
        for i, cid in enumerate(ids)
    ]
    assert [c["id"] for c in _owners(children)] == [ids[0]]
    for c in children[1:]:
        assert "create no new files" in c["success_criteria_seed"].lower()
        assert ids[0] in c["success_criteria_seed"]
        assert _clause_count(leerie, c) == 1


def test_migration_child_without_a_designation_is_unchanged(leerie):
    """Anti-vacuity, and a real contract: `newfile_owner_id=None` must leave
    the criteria byte-identical, so a caller with no sibling set to speak of
    keeps the pre-existing behaviour."""
    parent = _subtask("sweep")
    child = leerie._migration_child(parent, ["a.py"], "feat-001-1", "t",
                                    "exact criteria text")
    assert child["success_criteria_seed"] == "exact criteria text"


def test_the_clause_reaches_llm_written_chunk_labels_too(leerie):
    """The label-only splitter composes its OWN `success_criteria_seed` per
    chunk. Applying the rule in `_migration_child` (rather than inside the
    deterministic-label helper) is what makes it reach those labels as well —
    otherwise the dominant migration path would carry no clause at all."""
    parent = _subtask("sweep")
    llm_authored = "Worker-written criteria that never mention file ownership."
    child = leerie._migration_child(parent, ["a.py"], "feat-001-2", "t",
                                    llm_authored,
                                    newfile_owner_id="feat-001-1")
    assert llm_authored in child["success_criteria_seed"]
    assert "create no new files" in child["success_criteria_seed"].lower()
    assert "feat-001-1" in child["success_criteria_seed"]


def test_deterministic_chunk_labels_stay_distinct_per_chunk(leerie):
    """The pre-existing guarantee must survive the clause: identical criteria
    across chunks is the very shape this whole change exists to prevent."""
    parent = _subtask("sweep")
    ids = [f"feat-001-{i + 1}" for i in range(3)]
    chunks = [["a.py"], ["b.py"], ["c.py"]]
    seeds = {
        leerie._migration_child(
            parent, chunk, cid,
            *leerie._deterministic_chunk_label(parent, chunk, i, len(chunks)),
            newfile_owner_id=ids[0])["success_criteria_seed"]
        for i, (cid, chunk) in enumerate(zip(ids, chunks))
    }
    assert len(seeds) == len(chunks)


# --- RE-ENTRY: the one-owner invariant must hold at every depth ---------------
#
# Round 1 of this suite tested depth-1 splits only, and that gap shipped a real
# bug: each helper appends its clause to the PARENT's criteria, and a re-split
# child's parent criteria already carries one. A non-owner's child received its
# parent's "create no new files" AND its own "you are the owner" -- directly
# contradictory, and two subtasks claiming ownership is the exact multi-owner
# condition that produced the add/add collision this whole change prevents.
#
# Re-entry is not exotic: tier 2 exists for it (a single function larger than
# the cap), and run 719c2a26 re-entered three times (r8, r12, r14 each produced
# -r1/-r2 children), which would have yielded four claimed owners instead of one.

def _reenterable(child, lo, hi):
    """A region child whose own span is over the cap, so `_subfile_split`
    re-enters on it via the tier-2 line-window branch."""
    out = dict(child)
    out["owned_region"] = {"file": "dense.ts", "start": lo, "end": hi,
                           "symbols": []}
    return out


def test_resplitting_a_non_owner_mints_no_new_owner(leerie, tmp_path):
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    non_owner = next(c for c in children if not _owns(c))
    original_owner = _owners(children)[0]["id"]

    sub = leerie._subfile_split(_reenterable(non_owner, 1, 2100), tmp_path, 700)
    assert len(sub) > 1, "fixture must actually re-split for this to mean anything"
    assert _owners(sub) == [], (
        "a non-owner's children claimed ownership; a non-owner's whole subtree "
        "must stay ownerless")
    for c in sub:
        assert _clause_count(leerie, c) == 1, (
            "inherited clause was stacked rather than replaced — the two "
            "contradict each other")
        assert original_owner in c["success_criteria_seed"], (
            "children of a non-owner must keep naming the ORIGINAL owner, not "
            "a freshly-minted sibling")


def test_resplitting_the_owner_passes_ownership_to_exactly_one_child(
        leerie, tmp_path):
    """Anti-vacuity twin: the rule must not be 'never designate on re-entry'.
    An owner that splits has to hand ownership down, or nothing owns new files."""
    _dense_file(tmp_path)
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    owner = _owners(children)[0]

    sub = leerie._subfile_split(_reenterable(owner, 1, 2100), tmp_path, 700)
    assert len(_owners(sub)) == 1
    assert _owners(sub)[0]["id"].endswith("-r1")
    for c in sub:
        assert _clause_count(leerie, c) == 1


def test_peel_of_a_non_owner_chunk_mints_no_new_owner(leerie, tmp_path):
    """Same shape on the migration side: a labelled non-owner chunk that then
    hits the oversized-file peel runs through `_migration_child` a second time."""
    _dense_file(tmp_path)
    (tmp_path / "small.py").write_text("x\n")
    parent = _subtask("sweep")
    chunk = leerie._migration_child(parent, ["dense.ts", "small.py"],
                                    "feat-001-2", "chunk 2", "Seed.",
                                    newfile_owner_id="feat-001-1")
    assert not _owns(chunk)

    peeled = leerie._peel_oversized_file(chunk, tmp_path, 700, 8)
    assert len(peeled) == 2
    assert _owners(peeled) == []
    for c in peeled:
        assert _clause_count(leerie, c) == 1
        assert "feat-001-1" in c["success_criteria_seed"]


def test_whole_tree_has_exactly_one_new_file_owner(leerie, tmp_path):
    """The invariant itself, measured rather than asserted per-level: recurse
    the split to leaves and count. This is the check that would have caught the
    re-entry bug in round 1."""
    _dense_file(tmp_path, lines=2100)

    def walk(node, depth=0):
        # Force a re-split on the first child of each level so the tree is
        # genuinely deeper than one, independent of how the fixture tiles.
        kids = leerie._subfile_split(node, tmp_path, 700)
        if not kids or depth >= 2:
            return [node]
        deepened = [_reenterable(kids[0], 1, 2100)] + list(kids[1:])
        out = []
        for k in deepened:
            out.extend(walk(k, depth + 1))
        return out

    leaves = walk(_subtask("sweep"))
    owners = _owners(leaves)
    assert len(owners) == 1, (
        f"{len(owners)} of {len(leaves)} leaves claim new-file ownership: "
        f"{[c['id'] for c in owners]}")
    assert all(_clause_count(leerie, c) == 1 for c in leaves)


# --- tier 1 (tree-sitter symbol spans) — host-independent by construction -----
#
# `HAS_TREESITTER` is False on some dev hosts but TRUE on CI (requirements.txt
# pins tree-sitter and .github/workflows/test.yml installs it), so a suite that
# only ever exercised the tier-2 fallback locally would first meet tier 1 on CI
# — the green-locally/red-on-CI shape CLAUDE.md documents from PR #211. Stubbing
# the extractor pins the tier-1 partitioning path on EVERY host instead of
# leaving it to whether a native parser happens to be installed.

def _stub_ranges(monkeypatch, leerie, ranges):
    monkeypatch.setattr(leerie, "_extract_symbol_ranges", lambda _p: ranges)


def test_tier1_symbol_tiling_keeps_one_owner(leerie, tmp_path, monkeypatch):
    _dense_file(tmp_path, lines=2100)
    _stub_ranges(monkeypatch, leerie,
                 [(f"fn{i}", i * 300 + 1, (i + 1) * 300) for i in range(7)])
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    assert len(children) > 1
    assert len(_owners(children)) == 1
    assert all(_clause_count(leerie, c) == 1 for c in children)
    assert any(c["owned_region"]["symbols"] for c in children), (
        "expected the tier-1 path — symbol names should reach owned_region")


def test_tier1_oversized_symbol_reenters_with_one_owner(leerie, tmp_path,
                                                        monkeypatch):
    """The real re-entry trigger: ONE function larger than the cap. This is why
    719c2a26's r8/r12/r14 produced -r1/-r2 children, and uniform line-windows
    never would."""
    _dense_file(tmp_path, lines=2100)
    _stub_ranges(monkeypatch, leerie,
                 [("small", 1, 200), ("huge", 201, 2100)])
    children = leerie._subfile_split(_subtask("sweep"), tmp_path, 700)
    oversized = [c for c in children
                 if c["owned_region"]["end"] - c["owned_region"]["start"] + 1
                 > 700]
    assert oversized, "expected an over-cap region from the huge symbol"

    total_owners = len(_owners(children))
    for c in oversized:
        sub = leerie._subfile_split(c, tmp_path, 700)
        assert sub, "an over-cap region must re-enter tier 2"
        total_owners += len(_owners(sub)) - (1 if _owns(c) else 0)
        assert all(_clause_count(leerie, x) == 1 for x in sub)
    assert total_owners == 1
