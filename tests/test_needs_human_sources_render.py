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

from tests.conftest import source_segments

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
    # Split once (`source_segments`): this runs at collection, once per
    # function of leerie.py, and `ast.get_source_segment` re-splits the whole
    # module per call — about 26 minutes of collection on Python 3.11.
    segment = source_segments(LEERIE_PY.read_text())
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if "needs_human_verification" not in (segment(fn) or ""):
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
    # Not a count of today's sources (I3 removed one): the special shapes
    # and the constants, both found.
    assert SPECIAL <= set(SOURCES)
    assert len(set(SOURCES) - SPECIAL) >= 5, SOURCES


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


def _section(body: str, heading: str) -> str:
    """The text under `heading`, up to the next `## ` heading."""
    if heading + "\n" not in body:
        return ""
    return body.split(heading + "\n", 1)[1].split("\n## ", 1)[0]


NHV, NOT_VERIFIED = "## Needs human verification", "## Not verified by this run"


@pytest.mark.parametrize("source", SOURCES)
def test_an_entry_of_every_source_renders_in_the_pr(leerie, source):
    entry, text = _entry(source)
    body = leerie._format_pr_end_state_section(
        {"needs_human_verification": [entry]})
    # The two typed external shapes (the classifier's external item, a
    # blocker naming its resource) are a human's; every other source is a
    # limit on what the run measured.
    where = NHV if source in SPECIAL else NOT_VERIFIED
    assert text in _section(body, where), (source, body)


def test_only_a_typed_external_resource_reaches_needs_human(leerie):
    """The guard (DESIGN §6 *Only a typed external resource reaches "Needs
    human verification"*): one state holding an entry of every source the
    PR renders — each `needs_human_verification` source derived from the
    code above, a blocker and an external risk each with and without a
    resource, a simulated interleaving, an unmeasured held-out set, an
    intermittent failure and an unsettled untyped fix site. Every line
    under "Needs human verification" must be one of the typed external
    entries, and every other entry must be under "Not verified by this
    run". Driven by the user's runs 48c48629 (barnacle#665) and ebe36d49
    (barnacle#670)."""
    external, other = [], []
    nhv_entries = []
    for src in SOURCES:
        entry, text = _entry(src)
        nhv_entries.append(entry)
        (external if src in SPECIAL else other).append(text)
    nhv_entries.append({"source": "blocker", "subtask": "s2",
                        "resource": "", "blocker": "waiting on nobody named"})
    other.append("waiting on nobody named")
    risk = {"kind": "unexercised_behavior", "source": "implementer",
            "where_paths": [], "addressed": False}
    state = {
        "needs_human_verification": nhv_entries,
        "risk_register": {"s1": [
            {**risk, "detail": "live tenant behaviour",
             "gap": "unverified_external", "resource": "the live tenant"},
            {**risk, "detail": "external with no resource",
             "gap": "unverified_external"},
            {**risk, "detail": "two writers race", "gap": "test_weak",
             "simulated_interleaving": True}]},
        "acceptance": {"sets": [{"index": 1, "valid": True,
                                 "defect_files": ["src/a.test.ts"],
                                 "control_files": []}],
                       "gate": {}},
        "test_id_diff": {"intermittent_regression": [
            {"id": "src/x.test.ts :: flaky", "final_failed": 1,
             "final_runs": 24, "base_failed": 0, "base_runs": 24,
             "imports_changed": ["src/x.ts"]}]},
        "site_claims": {"sites": [
            {"site_id": "D8", "file": "src/s.ts", "symbol": "m",
             "resolution": "share one helper", "surface_other": True}]},
    }
    external.append("live tenant behaviour")
    other += ["two writers race", "held-out set(s) 1",
              "src/x.test.ts :: flaky", "share one helper"]
    body = leerie._format_pr_end_state_section(state)
    nhv, rest = _section(body, NHV), _section(body, NOT_VERIFIED)
    lines = [x for x in nhv.splitlines() if x.startswith("- ")]
    for line in lines:
        assert any(t in line for t in external), (line, body)
    for t in external:
        assert t in nhv, (t, body)
    for t in other:
        assert t not in nhv and t in rest, (t, body)
    # The entry typed external without a resource names nothing a human
    # could bring, and is not rendered as one.
    assert "external with no resource" not in nhv


def test_the_two_round_14_sources_render_as_task_requirements(leerie):
    for src in (leerie.DELIVERY_JUDGE_GAP_SOURCE,
                leerie.RATCHET_UNVERIFIED_GAP_SOURCE):
        groups = leerie._pr_not_verified(
            {"needs_human_verification": [
                {"source": src, "detail": f"Check by hand: {src}"}]},
            lambda _s: True)
        assert groups == {"Task requirements": [
            f"- [ ] Check by hand: {src}"]}


def test_a_source_no_table_names_still_renders(leerie):
    """The catch-all: an entry is never silent, even from a source a later
    change forgets to place."""
    groups = leerie._pr_not_verified(
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
