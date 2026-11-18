"""One writer of `needs_human_verification`, with each caller's own settings.

`_replace_gaps` is the only code that writes the list the PR's "Needs human
verification" and "Not verified by this run" sections read (DESIGN §6 *The
PR's end-state section is rendered by code, from typed state*). Its callers
differ in what they replace, when they save and whether a failed save stops
them; those differences are per-caller settings, pinned here through the
real writers and a real `State` on disk (IMPLEMENTATION `_replace_gaps`).

A save is seen on disk: `State.save` replaces `state.json` with a new file,
so its inode changes. A save fails for real when a directory stands where
its temp file goes."""
from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

NHV = "needs_human_verification"


def _state(leerie, tmp_path: Path, entries: list) -> object:
    st = leerie.State(tmp_path / "root", "run-g")
    st.run_dir.mkdir(parents=True, exist_ok=True)
    st.data = {NHV: entries, "required_items": []}
    st.save()
    return st


def _on_disk(st) -> list:
    return json.loads(st.path.read_text())[NHV]


def _inode(st) -> int:
    return st.path.stat().st_ino


def _break_save(st) -> None:
    st.path.with_suffix(".tmp").mkdir()


def test_a_writer_replaces_only_its_own_site(leerie, tmp_path):
    other = {"source": "confirm_budget", "site": "CI parity", "detail": "x"}
    rollback = {"source": "round_rolled_back", "site": "s", "detail": "y"}
    st = _state(leerie, tmp_path, [other, rollback, "junk"])
    leerie._set_confirm_budget_gap(st, "failing-test-ID diff", ["a.ts"])
    got = _on_disk(st)
    assert got[:3] == [other, rollback, "junk"]
    assert got[3]["site"] == "failing-test-ID diff"
    leerie._set_confirm_budget_gap(st, "failing-test-ID diff", [])
    assert _on_disk(st) == [other, rollback, "junk"]


def test_item_writers_replace_only_the_items_judged(leerie, tmp_path):
    kept = {"source": "record_worker_only", "item_index": 1, "detail": "old"}
    dropped = {"source": "record_worker_only", "item_index": 2,
               "detail": "old"}
    st = _state(leerie, tmp_path, [kept, dropped])
    leerie._set_record_worker_gap(st, [2, 3], {3: {"item": "item three",
                                                   "commands": ["npm t"]}})
    got = _on_disk(st)
    assert got[0] == kept
    assert [e["item_index"] for e in got] == [1, 3]
    assert "`npm t`" in got[1]["detail"]


def test_an_unchanged_list_is_not_saved_again(leerie, tmp_path):
    st = _state(leerie, tmp_path, [])
    leerie._set_regression_gap(st, "regression check could not run")
    before = _inode(st)
    leerie._set_regression_gap(st, "regression check could not run")
    assert _inode(st) == before


def test_a_rollback_is_saved_even_when_its_entry_is_already_there(
        leerie, tmp_path):
    """A resume that finishes the same reset writes its row again."""
    st = _state(leerie, tmp_path, [])
    for _ in range(2):
        before = _inode(st)
        leerie._record_round_rollback(st, "implementer", "round 1",
                                      "broke a test", pre_sha="abc")
        assert _inode(st) != before
    assert [e["source"] for e in _on_disk(st)] == ["round_rolled_back"]


def test_a_failed_save_stops_the_test_outcome_writer_only(leerie, tmp_path):
    st = _state(leerie, tmp_path, [])
    _break_save(st)
    # Suppressed: the regression-check writer goes on.
    leerie._set_regression_gap(st, "could not run")
    assert st.data[NHV][-1]["source"] == "regression_check"
    # Raised: the test-outcome and delivery-unknown writers do not.
    with pytest.raises(OSError):
        leerie._set_test_outcome_gap(st, ["item 0"], "no diff")
    with pytest.raises(OSError):
        leerie._set_delivery_unknown_gap(st, "delivery_judge_unknown",
                                         ["item 1"], "the judge crashed")


def test_classify_rewrites_only_its_items_and_drops_malformed_entries(
        leerie, tmp_path):
    """phase_classify's write: its own entries are replaced, a blocker
    accepted as external survives a re-classify, and an entry that is not a
    dict is dropped there (only there)."""
    blocker = {"source": "blocker", "subtask": "s1", "resource": "db",
               "blocker": "b"}
    st = _state(leerie, tmp_path, [
        {"source": "required_item", "item_index": 0, "item": "old"},
        blocker, "junk"])
    leerie._replace_gaps(
        st, "required_item", leerie._d1_needs_human_verification(
            [{"item": "local"}, {"item": "needs a phone",
                                 "verifiability": "external"}]),
        save="none", guard=False, drop_malformed=True)
    assert st.data[NHV] == [blocker, {
        "source": "required_item", "item_index": 1, "item": "needs a phone",
        "span": None, "source_ref": ""}]


def test_an_accepted_external_dependent_keeps_its_other_resources(
        leerie, tmp_path):
    st = _state(leerie, tmp_path, [])
    st.data["blocked"] = {"s2": {}}
    leerie._settle_accepted_external(st, "s2", "db", "needs db", via=["s1"])
    leerie._settle_accepted_external(st, "s2", "api", "needs api",
                                     via=["s1"])
    leerie._settle_accepted_external(st, "s2", "db", "needs db again",
                                     via=["s1"])
    got = _on_disk(st)
    assert [(e["resource"], e["blocker"]) for e in got] == [
        ("api", "needs api"), ("db", "needs db again")]
    # Without `via` every entry of the subtask is replaced.
    leerie._settle_accepted_external(st, "s2", "phone", "needs phone")
    assert [e["resource"] for e in _on_disk(st)] == ["phone"]
    assert "blocked" not in st.data


def test_no_other_code_writes_the_list():
    """Every assignment to `...["needs_human_verification"]` is in
    `_replace_gaps`: a writer with its own list surgery is how the settings
    drifted apart."""
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    writers = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target] if isinstance(
                           node, (ast.AugAssign, ast.AnnAssign)) else [])
            for t in targets:
                if (isinstance(t, ast.Subscript)
                        and isinstance(t.slice, ast.Constant)
                        and t.slice.value == NHV):
                    writers.add(fn.name)
    assert writers == {"_replace_gaps"}
