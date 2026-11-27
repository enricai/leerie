"""Unmet-item offending locations are typed and code-verified, never parsed
from prose (CLAUDE.md language-to-JSON): the `delivery_judge` schema carries
`{path, line_start, line_end}` per cite, `_delivery_unmet_parts` drops cites
that do not verify on the tree, and `_delivery_unmet_locations` drops
RECORD/ABSENT cites. Location only in `evidence` prose yields none."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from stub_helpers import (DELIVERY_CITED_FILE, DELIVERY_CITED_LINE,  # noqa: E402
                          _delivery_tree)

PROMPT = Path(__file__).resolve().parent.parent / "prompts" / "delivery_judge.md"


def _cite(path, ls, le, quote=DELIVERY_CITED_LINE):
    return {"path": path, "line_start": ls, "line_end": le, "quote": quote}


def _locations(leerie, tree, verdict):
    parts = leerie._delivery_unmet_parts(verdict, tree, "", {})
    return leerie._delivery_unmet_locations(parts)


def test_schema_types_cite_location(leerie):
    defs = leerie.SCHEMAS["delivery_judge"]["$defs"]
    cite = defs["cite"]
    for k, t in (("path", "string"), ("line_start", "integer"),
                 ("line_end", "integer")):
        assert cite["properties"][k]["type"] == t
        assert k in cite["required"]
    assert defs["part"]["properties"]["cites"]["items"] == {
        "$ref": "#/$defs/cite"}
    assert "unmet" in defs["part"]["properties"]["status"]["enum"]


def test_verified_cite_becomes_typed_location(leerie, tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    v = {"parts": [{"requirement": "r", "kind": "static", "status": "unmet",
                    "cites": [_cite(DELIVERY_CITED_FILE, 1, 1)]}]}
    assert _locations(leerie, tree, v) == [
        {"path": DELIVERY_CITED_FILE, "start_line": 1, "end_line": 1}]


def test_unverifiable_cites_are_dropped(leerie, tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    v = {"parts": [{"requirement": "r", "kind": "static", "status": "unmet",
                    "cites": [
                        _cite("src/missing.py", 1, 1),           # absent path
                        _cite(DELIVERY_CITED_FILE, 40, 41, "no such text"),
                        _cite(DELIVERY_CITED_FILE, 1, 1, "not in the file"),
                        _cite("ABSENT", 0, 0, "zzz_never_present_zzz"),
                        _cite("RECORD", 0, 0, "x" * 40),
                    ]}]}
    assert _locations(leerie, tree, v) == []


def test_record_and_absent_cites_name_no_file(leerie):
    parts = [{"cites": [{"path": "RECORD", "line_start": 0},
                        {"path": "ABSENT:src", "line_start": 0},
                        {"path": "a.py", "line_start": 2, "line_end": 3}]}]
    assert leerie._delivery_unmet_locations(parts) == [
        {"path": "a.py", "start_line": 2, "end_line": 3}]


def test_prose_only_location_yields_none(leerie, tmp_path):
    tree = _delivery_tree(tmp_path / "t")
    v = {"met": False, "evidence": f"{DELIVERY_CITED_FILE}:1 is wrong",
         "parts": [{"requirement": f"see {DELIVERY_CITED_FILE} line 1",
                    "kind": "static", "status": "unmet", "cites": []}]}
    assert _locations(leerie, tree, v) == []


def test_prompt_states_locations_are_typed():
    text = " ".join(PROMPT.read_text().split())
    assert "typed cite" in text
    assert "`path`, `line_start`, `line_end`" in text
    assert "never only in prose" in text


def test_real_quote_outside_range_is_drift_not_fabrication(leerie, tmp_path):
    """By design (`_DELIVERY_CITE_OK`) a real quote at the wrong lines is
    `drift`: still verified, so its location is kept."""
    tree = _delivery_tree(tmp_path / "t")
    c = _cite(DELIVERY_CITED_FILE, 40, 41)
    assert leerie._verify_delivery_cite(c, tree, "", {}) == "drift"
