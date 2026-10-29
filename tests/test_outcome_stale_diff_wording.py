"""Review round 8 L4: the outcome reason for a confirmed-new failure on a
stale diff read "newly failing on the an earlier tree (the delivery
re-drive's final tree could not be measured)". Since phase 5's own refresh
also marks a diff stale (DESIGN §8 *The final diff describes the tree that
ships*), the parenthetical named the wrong stage."""
from __future__ import annotations


class _St:
    def __init__(self, diff: dict, run_dir) -> None:
        self.data = {"test_id_diff": diff, "subtask_status": {}}
        self.run_dir = run_dir

    def save(self) -> None:
        pass


def _reason(leerie, tmp_path, diff: dict) -> str:
    st = _St(diff, tmp_path)
    assert leerie._decide_outcome(st, any_commits=True) == "unconverged"
    (reason,) = [r for r in st.data["outcome_reasons"] if "newly" in r]
    return reason


def test_a_stale_diff_names_the_tree_that_ships(leerie, tmp_path):
    r = _reason(leerie, tmp_path, {"new": ["t::x"], "tree_sha": "a",
                                      "stale_for_tree": "b"})
    assert r == ("1 test(s) newly failing on an earlier tree (the tree that "
                 "ships could not be measured), confirmed against the base")


def test_a_current_diff_names_the_final_tree(leerie, tmp_path):
    r = _reason(leerie, tmp_path, {"new": ["t::x"], "tree_sha": "a"})
    assert r == ("1 test(s) newly failing on the final tree, confirmed "
                 "against the base")
