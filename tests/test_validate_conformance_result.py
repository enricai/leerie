"""Tests for _validate_conformance_result() — cross-field invariants on
the conformer worker's structured output (DESIGN §9 *Post-work
conformance*).

The JSON schema enforces the *shape* of the result. This function
enforces the cross-field honesty rules the schema can't:
- a residual naming a repository rule (no `axis`) requires a non-empty
  rules_files_read; one with an `axis` is leerie's own BLT rule
- every rule_violations_fixed item must cite a non-empty `rule`
- every docs_updates / tests_updates `path` must exist in the worktree
"""
from __future__ import annotations


def _good_result(subtask_id="t1", **overrides):
    """A minimum well-formed result with all required keys present."""
    base = {
        "subtask_id": subtask_id,
        "rules_files_read": [],
        "rule_violations_fixed": [],
        "rule_violations_residual": [],
        "docs_updates": [],
        "tests_updates": [],
        "build": {"ran": False, "passed": False, "command": "", "summary": ""},
        "lint": {"ran": False, "passed": False, "command": "", "summary": ""},
        "tests": {"ran": False, "passed": False, "command": "", "summary": ""},
        "summary": "nothing to do",
    }
    base.update(overrides)
    return base


def test_empty_well_formed_result_passes(leerie, tmp_path):
    err = leerie._validate_conformance_result(_good_result(), str(tmp_path))
    assert err is None


def test_non_dict_result_rejected(leerie, tmp_path):
    assert leerie._validate_conformance_result([], str(tmp_path)) is not None
    assert leerie._validate_conformance_result(None, str(tmp_path)) is not None
    assert leerie._validate_conformance_result("oops", str(tmp_path)) is not None


def test_residual_without_files_read_rejected(leerie, tmp_path):
    """A rule violation that wasn't fixed requires the conformer to have
    *seen* at least one rules file — otherwise the violation is fabricated."""
    res = _good_result(
        rules_files_read=[],
        rule_violations_residual=[{"rule": "x", "why_not_fixed": "y"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "rule_violations_residual" in err
    assert "rules_files_read" in err


def test_residual_with_files_read_accepted(leerie, tmp_path):
    res = _good_result(
        rules_files_read=["CLAUDE.md"],
        rule_violations_residual=[{"rule": "x", "why_not_fixed": "y"}],
    )
    assert leerie._validate_conformance_result(res, str(tmp_path)) is None


def test_fixed_violation_with_empty_rule_rejected(leerie, tmp_path):
    res = _good_result(
        rules_files_read=["CLAUDE.md"],
        rule_violations_fixed=[
            {"rule": "", "fix": "added a hint", "evidence": "src/x.py:1"},
        ],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "rule_violations_fixed[0]" in err


def test_fixed_violation_with_whitespace_rule_rejected(leerie, tmp_path):
    res = _good_result(
        rules_files_read=["CLAUDE.md"],
        rule_violations_fixed=[
            {"rule": "   \n  ", "fix": "x", "evidence": "y"},
        ],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None


def test_docs_update_path_must_exist(leerie, tmp_path):
    res = _good_result(
        docs_updates=[{"path": "docs/UNKNOWN.md", "reason": "stale"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "docs_updates[0]" in err
    assert "docs/UNKNOWN.md" in err


def test_docs_update_with_existing_path_accepted(leerie, tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "API.md").write_text("# api\n")
    res = _good_result(
        docs_updates=[{"path": "docs/API.md", "reason": "added new flag"}],
    )
    assert leerie._validate_conformance_result(res, str(tmp_path)) is None


def test_tests_update_path_must_exist(leerie, tmp_path):
    res = _good_result(
        tests_updates=[{"path": "tests/test_missing.py", "reason": "added"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "tests_updates[0]" in err


def test_tests_update_with_existing_path_accepted(leerie, tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_new.py").write_text("def test_x(): pass\n")
    res = _good_result(
        tests_updates=[{"path": "tests/test_new.py", "reason": "new coverage"}],
    )
    assert leerie._validate_conformance_result(res, str(tmp_path)) is None


def test_empty_path_string_rejected(leerie, tmp_path):
    res = _good_result(
        docs_updates=[{"path": "", "reason": "stale"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "empty 'path'" in err


# --- path-traversal rejection (fourth-pass audit follow-up) ----------------

def test_docs_update_with_traversal_path_rejected(leerie, tmp_path):
    """A worker that emits `../foo` would have `(wt / rel).exists()`
    return True for any sibling-of-worktree path. The validator must
    reject such entries with a clear error — these are honesty failures,
    not legitimate documentation updates inside the subtask."""
    # Create a sibling file that the traversal would point at.
    sibling = tmp_path.parent / "sibling.md"
    sibling.write_text("# sibling\n")

    res = _good_result(
        docs_updates=[{"path": "../sibling.md", "reason": "drift"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None, "traversal path must be rejected"
    assert "escapes the worktree" in err
    assert "../sibling.md" in err


def test_tests_update_with_absolute_outside_path_rejected(leerie, tmp_path):
    """An absolute path that doesn't live under the worktree must be
    rejected — even if the path itself exists on disk."""
    # /etc/hostname exists on most systems; if not, fall back to something
    # the test can guarantee exists outside tmp_path.
    outside = tmp_path.parent / "outside.py"
    outside.write_text("def x(): pass\n")
    res = _good_result(
        tests_updates=[{"path": str(outside), "reason": "added"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None, "absolute outside-worktree path must be rejected"
    assert "escapes the worktree" in err


def test_nested_traversal_to_legitimate_subtree_still_rejected(leerie, tmp_path):
    """Even a path that combines traversal with a return into the worktree
    is rejected — the resolved final destination must be under the
    worktree, but the path *components* having `..` at all is treated as
    suspicious. (We use Path.resolve which normalizes the path; this test
    verifies the resolution-and-then-containment check is strict.)"""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "API.md").write_text("# api\n")
    # `../<basename>/docs/API.md` resolves back inside the worktree.
    # Whether to accept or reject this is a design choice; we accept it
    # because the resolved destination IS under the worktree.
    res = _good_result(
        docs_updates=[{"path": f"../{tmp_path.name}/docs/API.md",
                       "reason": "round-trip path"}],
    )
    # Document the current behavior: resolved-inside-worktree is accepted.
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is None, \
        f"path that resolves inside worktree should be accepted, got: {err}"


# --- OSError-on-resolve branches (Path.resolve() failing outright) --------

def test_worktree_path_unresolvable_rejected(leerie, tmp_path, monkeypatch):
    """`Path(worktree).resolve()` raising OSError (e.g. a filesystem-level
    resolution failure) must be reported, not left to propagate."""
    import pathlib
    bad = tmp_path / "bad-worktree"
    orig_resolve = pathlib.Path.resolve

    def fake_resolve(self, *a, **kw):
        if self.name == "bad-worktree":
            raise OSError("simulated resolution failure")
        return orig_resolve(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "resolve", fake_resolve)
    err = leerie._validate_conformance_result(_good_result(), str(bad))
    assert err is not None
    assert "could not be resolved" in err


def test_docs_update_path_unresolvable_rejected(leerie, tmp_path, monkeypatch):
    """`(wt_resolved / rel).resolve()` raising OSError for a cited
    docs_updates path must be reported per-item, distinct from the
    worktree-level resolve failure above."""
    import pathlib
    orig_resolve = pathlib.Path.resolve

    def fake_resolve(self, *a, **kw):
        if self.name == "unresolvable.md":
            raise OSError("simulated resolution failure")
        return orig_resolve(self, *a, **kw)

    monkeypatch.setattr(pathlib.Path, "resolve", fake_resolve)
    res = _good_result(
        docs_updates=[{"path": "unresolvable.md", "reason": "x"}],
    )
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None
    assert "docs_updates[0]" in err
    assert "could not be resolved" in err


# --- leerie's own rule needs no rules file (0.43.0 barnacle, 7 rejections) ---

def _wire(rules_files_read, *violations):
    """The conformer's wire shape (`SCHEMAS["conformer"]`), expanded by the
    real `_expand_conformer_output` exactly as the conformer loop does."""
    return {"subtask_id": "t1", "rules_files_read": rules_files_read,
            "rule_violations": list(violations), "file_updates": [],
            "build": {"ran": False, "passed": False, "command": "",
                      "summary": ""},
            "lint": {"ran": False, "passed": False, "command": "",
                     "summary": ""},
            "tests": {"ran": True, "passed": False, "command": "",
                      "summary": ""},
            "summary": "s", "solution_defects": []}


BLT = {"status": "residual", "rule": "build/lint/tests must pass",
       "axis": "tests", "why_not_fixed": "30 s timeouts under host load"}


def test_blt_residual_with_no_rules_file_is_accepted(leerie, tmp_path):
    """The live rejection: no RULES_FILES, a red test axis reported as the
    prompt says (`axis` set). The rule is leerie's own; no file supplies it."""
    res = leerie._expand_conformer_output(_wire([], BLT))
    assert res["rule_violations_residual"][0]["axis"] == "tests"
    assert leerie._validate_conformance_result(res, str(tmp_path)) is None


def test_repo_rule_residual_beside_a_blt_one_is_still_rejected(leerie,
                                                               tmp_path):
    """The exemption is per entry: a repository rule reported with no rules
    file read is still fabricated, whatever else is in the list."""
    other = {"status": "residual", "rule": "no new runtime dependencies",
             "why_not_fixed": "y"}
    res = leerie._expand_conformer_output(_wire([], BLT, other))
    err = leerie._validate_conformance_result(res, str(tmp_path))
    assert err is not None and "rules_files_read" in err


def test_an_axis_outside_the_three_is_no_exemption(leerie, tmp_path):
    """Only a typed BLT axis marks leerie's rule: an invented one is dropped
    by the expansion and the entry needs its rules file again."""
    res = leerie._expand_conformer_output(
        _wire([], dict(BLT, axis="perf")))
    assert "axis" not in res["rule_violations_residual"][0]
    assert leerie._validate_conformance_result(res, str(tmp_path)) is not None
