"""N3 upstream prevention: `_schedule` serialises same-wave subtasks whose
planned `files_likely_touched` intersect, within `overlap_extra_waves` extra
waves (DESIGN §5 *Planned file overlap is serialised*)."""
from __future__ import annotations

import ast
import inspect
import pathlib


def _st(id_, files, depends_on=()) -> dict:
    return {"id": id_, "title": id_, "intent": id_,
            "files_likely_touched": list(files), "provides": [],
            "requires": [], "depends_on": list(depends_on)}


def _plans(*subs):
    return [{"domain": "x", "subtasks": list(subs)}]


def test_overlapping_pair_lands_in_different_waves(leerie):
    plans = _plans(_st("a-1", ["src/x.ts"]), _st("b-1", ["src/x.ts"]),
                   _st("c-1", ["src/y.ts"]))
    _s, waves = leerie._schedule(plans, overlap_extra_waves=3)
    assert waves == [["a-1", "c-1"], ["b-1"]]


def test_cap_zero_is_plain_kahn(leerie):
    plans = _plans(_st("a-1", ["src/x.ts"]), _st("b-1", ["src/x.ts"]))
    _s, waves = leerie._schedule(plans, overlap_extra_waves=0)
    assert waves == [["a-1", "b-1"]]
    _s, waves = leerie._schedule(plans)
    assert waves == [["a-1", "b-1"]]


def test_cap_bounds_extra_waves_largest_overlap_first(leerie):
    # Four subtasks all sharing one file would need 3 extra waves. At cap 1
    # the larger overlap (a/b) is kept first; later pairs are kept only
    # while they still fit in that one extra wave (a/c, a/d do; b/c, b/d,
    # c/d would need a second).
    plans = _plans(_st("a-1", ["f", "g"]), _st("b-1", ["f", "g"]),
                   _st("c-1", ["f"]), _st("d-1", ["f"]))
    _s, waves = leerie._schedule(plans, overlap_extra_waves=1)
    assert waves == [["a-1"], ["b-1", "c-1", "d-1"]]
    _s, waves = leerie._schedule(plans, overlap_extra_waves=3)
    assert waves == [["a-1"], ["b-1"], ["c-1"], ["d-1"]]


def test_extra_waves_counted_over_the_whole_plan(leerie):
    # Two independent waves each with one overlapping pair; cap 1 keeps
    # only the first (largest overlap, then wave order).
    plans = _plans(_st("a-1", ["f", "g"]), _st("b-1", ["f", "g"]),
                   _st("c-1", ["h"], depends_on=["a-1", "b-1"]),
                   _st("d-1", ["h"], depends_on=["a-1", "b-1"]))
    _s, waves = leerie._schedule(plans, overlap_extra_waves=1)
    assert waves == [["a-1"], ["b-1"], ["c-1", "d-1"]]


def test_predecessor_order_is_kept(leerie):
    plans = _plans(_st("a-1", ["f"]), _st("b-1", ["f"]),
                   _st("c-1", ["z"], depends_on=["b-1"]))
    subs, waves = leerie._schedule(plans, overlap_extra_waves=3)
    pos = {s: i for i, w in enumerate(waves) for s in w}
    preds, _p, _e = leerie._build_predecessor_graph(subs, quiet=True)
    for sid, ps in preds.items():
        for p in ps:
            assert pos[p] < pos[sid]
    assert waves == [["a-1"], ["b-1"], ["c-1"]]


def test_leading_dot_slash_is_the_same_file(leerie):
    plans = _plans(_st("a-1", ["./src/x.ts"]), _st("b-1", ["src/x.ts"]))
    _s, waves = leerie._schedule(plans, overlap_extra_waves=3)
    assert waves == [["a-1"], ["b-1"]]


def test_run_phases_passes_the_cap_at_both_schedule_sites(leerie):
    """Both `_schedule` calls in `_run_phases` pass the configured cap — a
    call that dropped it would silently plan without serialisation."""
    src = inspect.getsource(leerie._run_phases)
    calls = [n for n in ast.walk(ast.parse(_dedent(src)))
             if isinstance(n, ast.Call)
             and getattr(n.func, "id", None) == "_schedule"]
    assert len(calls) == 2
    for c in calls:
        assert len(c.args) == 2
        assert "overlap_extra_waves" in ast.unparse(c.args[1])


def _dedent(src: str) -> str:
    import textwrap
    return textwrap.dedent(src)


def test_cap_default_is_three(leerie):
    assert leerie.DEFAULT_CAPS["overlap_extra_waves"] == 3
