"""A file the run left as the base had it is its own base form (DESIGN §8
*What an existing test pins on the base holds on the tree that ships*).

Barnacle 788868f5 (leerie 0.100.0, issue #748): five tests in three files the
run never touched were confirmed newly failing on the tree the delivery
re-drive started from. The base-form check ran only files the run changed, so
the re-drive read "0 existing test file(s) failing in their base form", and
an item typed `test_id_diff` never enters `unmet_final`: nothing drove them,
and the run ended `unconverged` on them.

Real git, a real confined pytest and real B* through the final ID diff, the
base-form check and the re-drive's notes, as in
`test_existing_behaviour_base_form.py`, whose `World` this uses. No worker
runs.
"""
from __future__ import annotations

import asyncio

from tests.test_existing_behaviour_base_form import (EXPORT,  # noqa: F401
                                                     LIB, World, _quiet)

BROKEN = LIB.replace("def helper():\n    return 1", "def helper():\n    return 2")
EXPORT_FILE = "tests/test_export.py"


def _final_diff(w: World) -> dict:
    # The baseline phase 4 records before any change: the base's failing IDs
    # (the base's own red test, `test_red.py`).
    w.st.data["conformance"] = {"_baseline": {"sha": w.base}}
    w.st.data["baseline_failing_ids"] = [
        "tests/test_red.py::test_always_red"]
    w.st.save()
    asyncio.run(w.leerie._refresh_shipping_test_id_diff(
        w.st.run_dir, w.st, w.caps, label="final"))
    return w.st.data["test_id_diff"]


def _existing(w: World) -> dict:
    asyncio.run(w.leerie._check_existing_behaviour(
        w.st.run_dir, w.st, w.caps, {}, {}))
    return w.st.data["existing_behaviour"]


def _broken_untouched(w: World) -> None:
    """`lib.helper` changes; `tests/test_export.py`, which pins it, is left
    as the base had it."""
    w.commit({"lib.py": BROKEN})


def test_the_final_diff_records_each_new_failures_file(leerie, tmp_path):
    w = World(leerie, tmp_path)
    _broken_untouched(w)
    tid = _final_diff(w)
    (new,) = tid["new"]
    assert "test_helper" in new
    assert tid["new_files"] == {new: EXPORT_FILE}


def test_an_untouched_tests_new_failure_is_a_base_form_case(leerie, tmp_path):
    w = World(leerie, tmp_path)
    _broken_untouched(w)
    _final_diff(w)
    rec = _existing(w)
    assert EXPORT_FILE in rec["files"]
    assert [r["file"] for r in rec["changed"]] == [EXPORT_FILE]
    assert "test_helper" in rec["changed"][0]["id"]
    assert rec["changed"][0]["message"]
    notes = asyncio.run(leerie._redrive_base_form_notes(w.st, w.staging))
    assert [n["file"] for n in notes] == [EXPORT_FILE]
    assert notes[0]["kind"] == "base_form_case"
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    assert any("existing_behaviour_changed" in r and "test_helper" in r
               for r in w.st.data["outcome_reasons"])


def test_a_round_that_fixes_the_untouched_test_is_counted_and_one_that_does_not_is_not(
        leerie, tmp_path):
    """The re-drive's check of a note runs the note's file, unchanged or not:
    a case it never ran would read as fixed."""
    w = World(leerie, tmp_path)
    _broken_untouched(w)
    _final_diff(w)
    _existing(w)
    notes = asyncio.run(leerie._redrive_base_form_notes(w.st, w.staging))
    log = w.st.run_dir / "logs" / "rd.log"
    w.commit({"calc.py": "def add(a, b):\n    return a + b\n"})
    assert asyncio.run(leerie._redrive_base_form_fixed(
        w.st, w.caps, w.staging, notes, label="g0", log_path=log)) == []
    w.commit({"lib.py": LIB})
    assert asyncio.run(leerie._redrive_base_form_fixed(
        w.st, w.caps, w.staging, notes, label="g1", log_path=log)) == \
        notes[0]["cases"]


def test_a_diff_of_another_tree_adds_no_file(leerie, tmp_path):
    w = World(leerie, tmp_path)
    _broken_untouched(w)
    _final_diff(w)
    w.commit({"calc.py": "def add(a, b):\n    return a + b\n"})
    tree = asyncio.run(leerie._view_tree_sha(w.st, w.staging))
    assert leerie._unchanged_new_failure_files(w.st, tree) == []


def test_only_a_file_the_base_had_and_the_tree_left_is_its_own_base_form(
        leerie, tmp_path):
    w = World(leerie, tmp_path)
    head = w.commit({"tests/test_loose.py": "def test_x():\n    assert 1\n",
                     "tests/test_new.py": "def test_y():\n    assert 1\n"})
    repo = leerie._run_repo(w.st)
    got = leerie._base_form_unchanged(
        repo, w.base, head, [EXPORT_FILE, "tests/test_loose.py",
                             "tests/test_new.py", "tests/helpers.py"])
    assert got == [EXPORT_FILE]
