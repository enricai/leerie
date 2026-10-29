"""Every "Needs human verification" entry the run records renders in the PR
(DESIGN §6 *The PR's end-state section*, review round 14 H1).

The renderer used to be a hand-kept chain of one branch per source, so the
two sources round 14 added (`delivery_judge_unknown`, `ratchet_unverified`)
were written to state and shown nowhere. The sources here are derived from
the code — every module `*_GAP_SOURCE` constant and every literal `source`
of a dict built in a function that writes `needs_human_verification` — never
listed by hand.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

LEERIE_PY = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"
SPECIAL = {"required_item", "blocker"}


def _written_sources() -> set[str]:
    tree = ast.parse(LEERIE_PY.read_text())
    consts: dict[str, str] = {}
    for node in tree.body:
        if (isinstance(node, ast.Assign) and len(node.targets) == 1
                and isinstance(node.targets[0], ast.Name)
                and node.targets[0].id.endswith("_GAP_SOURCE")
                and isinstance(node.value, ast.Constant)
                and isinstance(node.value.value, str)):
            consts[node.targets[0].id] = node.value.value
    out = set(consts.values())
    src = LEERIE_PY.read_text()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "needs_human_verification" not in (ast.get_source_segment(src, fn)
                                              or ""):
            continue
        for d in ast.walk(fn):
            if not isinstance(d, ast.Dict):
                continue
            for k, v in zip(d.keys, d.values):
                if not (isinstance(k, ast.Constant) and k.value == "source"):
                    continue
                if isinstance(v, ast.Constant) and isinstance(v.value, str):
                    out.add(v.value)
                elif isinstance(v, ast.Name) and v.id in consts:
                    out.add(consts[v.id])
    return out


SOURCES = sorted(_written_sources())


def test_the_derivation_finds_the_sources():
    # A derivation that silently finds nothing would pass everything below.
    assert SPECIAL <= set(SOURCES)
    assert len(SOURCES) >= 10, SOURCES


def test_every_written_source_has_a_deliberate_group(leerie):
    groups = leerie._needs_human_detail_groups()
    missing = [s for s in SOURCES if s not in groups and s not in SPECIAL]
    assert not missing, (
        f"needs_human_verification source(s) {missing} have no PR group in "
        "_needs_human_detail_groups — add one")


def _entry(source: str) -> tuple[dict, str]:
    text = f"check {source} by hand"
    if source == "required_item":
        return {"source": source, "item_index": 0, "item": text}, text
    if source == "blocker":
        return ({"source": source, "subtask": "s1", "resource": "a token",
                 "blocker": text}, text)
    return {"source": source, "detail": text}, text


@pytest.mark.parametrize("source", SOURCES)
def test_an_entry_of_every_source_renders_in_the_pr(leerie, source):
    entry, text = _entry(source)
    body = leerie._format_pr_end_state_section(
        {"needs_human_verification": [entry]})
    assert "## Needs human verification" in body
    assert text in body


def test_the_two_round_14_sources_render_as_task_requirements(leerie):
    for src in (leerie.DELIVERY_JUDGE_GAP_SOURCE,
                leerie.RATCHET_UNVERIFIED_GAP_SOURCE):
        groups = leerie._pr_needs_human(
            {"needs_human_verification": [
                {"source": src, "detail": f"Check by hand: {src}"}]},
            lambda _s: True)
        assert groups == {"Task requirements": [
            f"- [ ] Check by hand: {src}"]}


def test_a_source_no_table_names_still_renders(leerie):
    """The catch-all: an entry is never silent, even from a source a later
    change forgets to place."""
    groups = leerie._pr_needs_human(
        {"needs_human_verification": [
            {"source": "some_new_gap", "detail": "look at this"}]},
        lambda _s: True)
    assert groups == {"Other checks the run could not make":
                      ["- [ ] look at this"]}


def test_a_crashed_final_judge_reaches_the_pr_end_to_end(leerie):
    """Through the writer, not a hand-made entry: the entry
    `_set_delivery_unknown_gap` writes renders."""
    class St:
        data: dict = {}

        def save(self):
            pass
    st = St()
    st.data = {}
    leerie._set_delivery_unknown_gap(
        st, leerie.DELIVERY_JUDGE_GAP_SOURCE, ["ship the export button"],
        "the final delivery judge crashed on every attempt")
    body = leerie._format_pr_end_state_section(st.data)
    assert "ship the export button" in body
    assert "crashed on every attempt" in body
