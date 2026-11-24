"""A tree already discovered is not discovered again (DESIGN §9 *An
intermittent failure the run introduced is evidence*, *A tree already
discovered is not discovered again*).

Barnacle held-out batch (leerie 0.93.0), #790: the last discovery (80 files x
20, 29.7 min) reran the tree its first had covered — every re-drive round
between them rolled back, and the failing-test-ID diff's memo held only the
latest tree. These drive the real `_intermittent_discovery` over the
`hub` fixture's fake runner; only the tree identity is stubbed.
"""
from __future__ import annotations

import asyncio

from tests.test_discovery_budget import _MEASURED, _caps, hub  # noqa: F401


def _arm(leerie, hub, monkeypatch, tree: dict) -> None:
    async def tree_sha(_s):
        return tree["sha"]
    monkeypatch.setattr(leerie, "_worktree_tree_sha", tree_sha)
    hub["st"].data["test_file_durations"] = {f: 3.0 for f in hub["files"]}
    hub["st"].data["test_run_overhead"] = list(_MEASURED)


def _discover(leerie, hub, *, base="base", partial_ok=True, **caps):
    return asyncio.run(leerie._intermittent_discovery(
        hub["st"], _caps(leerie, **caps), str(hub["staging"]), "x", base,
        log_path=hub["st"].run_dir / "t.log", label="t", clock=hub["clock"],
        partial_ok=partial_ok))


def _repeats(hub) -> int:
    return sum(1 for c in hub["calls"] if "-repeat" in c["label"])


def test_same_tree_is_reused_not_rerun(leerie, hub, monkeypatch, capsys):
    tree = {"sha": "T1"}
    _arm(leerie, hub, monkeypatch, tree)
    first = _discover(leerie, hub)
    assert first["repeats_run"] == 20 and _repeats(hub) == 20
    calls, t = len(hub["calls"]), hub["clock"].t
    again = _discover(leerie, hub)
    assert len(hub["calls"]) == calls and hub["clock"].t == t  # ran nothing
    assert again["reused"] is True and again["_runs"] == []
    assert again["files"] == first["files"]
    assert again["repeats_run"] == 20 and again["failed_ids"] == []
    assert "discovery reused" in capsys.readouterr().out


def test_rollback_to_a_discovered_tree_reuses_it(leerie, hub, monkeypatch):
    # The #790 shape: tree A, then B, then back to A.
    tree = {"sha": "A"}
    _arm(leerie, hub, monkeypatch, tree)
    _discover(leerie, hub)
    tree["sha"] = "B"
    _discover(leerie, hub)
    assert _repeats(hub) == 40
    tree["sha"] = "A"
    back = _discover(leerie, hub)
    assert _repeats(hub) == 40 and back["reused"] is True


def test_another_base_or_budget_is_another_discovery(leerie, hub, monkeypatch):
    _arm(leerie, hub, monkeypatch, {"sha": "T"})
    _discover(leerie, hub)
    _discover(leerie, hub, base="other-base")  # a group's own pre_sha
    assert _repeats(hub) == 40
    _discover(leerie, hub, confirm_budget_sec=3600)
    assert _repeats(hub) == 60


def test_a_discovery_that_failed_an_id_is_not_kept(leerie, hub, monkeypatch):
    _arm(leerie, hub, monkeypatch, {"sha": "T"})
    real = leerie._capture_failing_test_ids

    async def flaky(tree, cmd, st, caps, *, label, **k):
        got = await real(tree, cmd, st, caps, label=label, **k)
        if got is not None and label.endswith("-repeat3"):
            got["ids"] = ["src/t000.test.ts > x"]
        return got
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", flaky)
    d = _discover(leerie, hub)
    assert d["failed_ids"] == ["src/t000.test.ts > x"]
    assert not hub["st"].data.get("discovery_memo")
    _discover(leerie, hub)
    assert _repeats(hub) == 40  # rerun, its failure confirmed again


def test_a_discovery_the_budget_cut_is_not_kept(leerie, hub, monkeypatch):
    _arm(leerie, hub, monkeypatch, {"sha": "T"})
    # Known durations understate the real cost: the budget cuts a repeat.
    hub["st"].data["test_file_durations"] = {f: 2.5 for f in hub["files"]}
    d = _discover(leerie, hub)
    assert d["budget_exhausted"] is True
    assert not hub["st"].data.get("discovery_memo")


def test_a_partial_result_is_not_reused_off_the_shipping_tree(
        leerie, hub, monkeypatch):
    _arm(leerie, hub, monkeypatch, {"sha": "T"})
    part = _discover(leerie, hub)  # partial_ok: 28 of 301 files
    assert part["skipped_files"] and hub["st"].data["discovery_memo"]
    off = _discover(leerie, hub, partial_ok=False)
    # Off the tree that ships the rule decides as before: not every file
    # fits, so it defers instead of taking the partial result.
    assert off.get("deferred") is True and not off.get("reused")


def test_a_dirty_tree_is_neither_served_nor_stored(leerie, hub, monkeypatch):
    _arm(leerie, hub, monkeypatch, {"sha": None})
    _discover(leerie, hub)
    _discover(leerie, hub)
    assert _repeats(hub) == 40
    assert not hub["st"].data.get("discovery_memo")


def test_memo_key_names_every_input(leerie):
    k = leerie._discovery_memo_key
    base = k("t", "b", "c", 20, 1800.0)
    assert base == k("t", "b", "c", 20, 1800)
    assert len({base, k("t2", "b", "c", 20, 1800.0),
                k("t", "b2", "c", 20, 1800.0), k("t", "b", "c2", 20, 1800.0),
                k("t", "b", "c", 10, 1800.0),
                k("t", "b", "c", 20, 900.0)}) == 6
