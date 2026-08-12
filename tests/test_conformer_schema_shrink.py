"""N29 — SCHEMAS['conformer'] shrunk below the observed grammar-compile
size threshold (docs/task investigation: source bytes measured across all
23 SCHEMAS entries correlate with `--dangerously-force-strict-output`
grammar-compiler rejections — `conformer` at 6,236 bytes and `planner` at
6,152 were rejected; `plan_overlap_judge` at 4,858 was never rejected).

Two things are pinned:
- the shrunk schema's SOURCE size sits at or below the observed-safe line
  `plan_overlap_judge` already sits on, using the same measurement the
  investigation used (`ast.get_source_segment` on the literal dict, which
  is what the "schema source bytes" table in the work order measured);
- `_expand_conformer_output` restores the pre-flatten shape losslessly for
  every field `_validate_conformance_result`/`check_production_evidence`
  (via `_summarize_residuals`/`_final_conformance_payload`) reads, so
  restructuring the wire schema drops nothing those checks consume.
"""
from __future__ import annotations

import ast
import copy
import json

import pytest


def _schemas_dict_node(src: str) -> ast.expr:
    tree = ast.parse(src)
    for node in ast.walk(tree):
        targets = (
            node.targets if isinstance(node, ast.Assign)
            else [node.target] if isinstance(node, ast.AnnAssign)
            else None)
        if targets is None:
            continue
        for t in targets:
            if isinstance(t, ast.Name) and t.id == "SCHEMAS":
                return node.value
    raise AssertionError("SCHEMAS assignment not found")


def _schema_source_bytes(leerie_src_path) -> dict[str, int]:
    src = leerie_src_path.read_text()
    d = _schemas_dict_node(src)
    out = {}
    for k, v in zip(d.keys, d.values):
        if isinstance(k, ast.Constant):
            seg = ast.get_source_segment(src, v)
            out[k.value] = len(seg.encode())
    return out


@pytest.fixture(scope="module")
def leerie_src_path():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[1] / "orchestrator" / "leerie.py"


def test_conformer_source_bytes_under_plan_overlap_judge_line(leerie_src_path):
    sizes = _schema_source_bytes(leerie_src_path)
    assert sizes["conformer"] <= sizes["plan_overlap_judge"], (
        f"conformer ({sizes['conformer']} bytes) must sit at or below the "
        f"observed-safe line plan_overlap_judge ({sizes['plan_overlap_judge']} "
        "bytes) already sits on, unrejected, per the N29 investigation")


def test_conformer_hardened_wire_bytes_shrank(leerie):
    """The strict-output proxy's `strict:true` rewrite (`_strictify_schema`)
    is what actually reaches the grammar compiler. Pin that the flattened
    schema's hardened wire form is meaningfully smaller than the pre-N29
    shape would have been (measured pre-fix: 3,178 bytes) — the
    restructuring must show up on the wire, not just in source-comment
    trimming."""
    node = copy.deepcopy(leerie.SCHEMAS["conformer"])
    leerie._strictify_schema(node)
    hardened_bytes = len(json.dumps(node))
    assert hardened_bytes < 3178, (
        f"hardened conformer wire form is {hardened_bytes} bytes; "
        "expected a measurable reduction from the pre-N29 3,178-byte shape")


def test_conformer_schema_no_longer_carries_isomorphic_pairs(leerie):
    """The two isomorphic array-pair shapes that inflated the schema are
    gone from the wire schema; a single discriminated array replaces each."""
    props = leerie.SCHEMAS["conformer"]["properties"]
    for removed in ("rule_violations_fixed", "rule_violations_residual",
                    "docs_updates", "tests_updates"):
        assert removed not in props, f"{removed} should be flattened away"
    assert "rule_violations" in props
    assert "file_updates" in props


# --- _expand_conformer_output: lossless round-trip for every consumed field

def _wire_payload(**overrides) -> dict:
    base = {
        "subtask_id": "feat-001",
        "rules_files_read": ["CLAUDE.md"],
        "rule_violations": [
            {"status": "fixed", "rule": "no bare except", "fix": "narrowed",
             "evidence": "src/x.py:10"},
            {"status": "residual", "rule": "no print statements",
             "why_not_fixed": "user-facing CLI output"},
        ],
        "file_updates": [
            {"kind": "docs", "path": "docs/API.md", "reason": "new flag"},
            {"kind": "tests", "path": "tests/test_x.py", "reason": "coverage"},
        ],
        "build": {"ran": True, "passed": True, "command": "make", "summary": ""},
        "lint": {"ran": True, "passed": True, "command": "ruff", "summary": ""},
        "tests": {"ran": True, "passed": True, "command": "pytest", "summary": ""},
        "summary": "clean",
        "solution_defects": [],
    }
    base.update(overrides)
    return base


def test_expand_restores_fixed_and_residual_arrays(leerie):
    out = leerie._expand_conformer_output(_wire_payload())
    assert out["rule_violations_fixed"] == [
        {"rule": "no bare except", "fix": "narrowed", "evidence": "src/x.py:10"}]
    assert out["rule_violations_residual"] == [
        {"rule": "no print statements",
         "why_not_fixed": "user-facing CLI output"}]


def test_expand_restores_docs_and_tests_updates(leerie):
    out = leerie._expand_conformer_output(_wire_payload())
    assert out["docs_updates"] == [
        {"path": "docs/API.md", "reason": "new flag"}]
    assert out["tests_updates"] == [
        {"path": "tests/test_x.py", "reason": "coverage"}]


def test_expand_drops_unrecognised_discriminator_rather_than_guessing(leerie):
    payload = _wire_payload(
        rule_violations=[{"status": "unknown", "rule": "x"}],
        file_updates=[{"kind": "unknown", "path": "y", "reason": "z"}],
    )
    out = leerie._expand_conformer_output(payload)
    assert out["rule_violations_fixed"] == []
    assert out["rule_violations_residual"] == []
    assert out["docs_updates"] == []
    assert out["tests_updates"] == []


def test_expand_does_not_mutate_input(leerie):
    payload = _wire_payload()
    before = copy.deepcopy(payload)
    leerie._expand_conformer_output(payload)
    assert payload == before


def test_expand_tolerates_missing_or_non_list_flattened_arrays(leerie):
    payload = _wire_payload(rule_violations=None, file_updates="oops")
    out = leerie._expand_conformer_output(payload)
    assert out["rule_violations_fixed"] == []
    assert out["rule_violations_residual"] == []
    assert out["docs_updates"] == []
    assert out["tests_updates"] == []


def test_expand_then_validate_round_trips_a_real_wire_payload(leerie, tmp_path):
    """Same real conformer payload shape that would have validated against
    the OLD schema still validates (via _validate_conformance_result) after
    going through the new wire shape + expansion."""
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "API.md").write_text("# api\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text("def test_x(): pass\n")
    payload = _wire_payload(
        file_updates=[
            {"kind": "docs", "path": "docs/API.md", "reason": "new flag"},
            {"kind": "tests", "path": "tests/test_x.py", "reason": "coverage"},
        ],
    )
    out = leerie._expand_conformer_output(payload)
    err = leerie._validate_conformance_result(out, str(tmp_path))
    assert err is None


def test_expand_then_summarize_residuals_reports_the_residual(leerie):
    out = leerie._expand_conformer_output(_wire_payload())
    lines = leerie._summarize_residuals(out)
    assert any("no print statements" in line for line in lines)


def test_jsonschema_accepts_the_flattened_wire_shape(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    jsonschema.validate(_wire_payload(), leerie.SCHEMAS["conformer"])
