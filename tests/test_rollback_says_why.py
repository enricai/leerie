"""Every rollback of a worker's round says why, in state, in the log and in
the PR (DESIGN §8 *A rollback says why*).

Run d7c06fe3's only re-drive group fixed six held-back cases and was reset
with no log line and no reason field: what voided it had to be reconstructed
by replaying the check. The drivers here run the real re-drive over a real
repository, stubbing only the worker and the evaluation; the other rollback
sites are pinned next to the tests that already drive them."""
from __future__ import annotations

import ast
import os
import sys

from tests.conftest import REPO_ROOT

sys.path.insert(0, os.path.dirname(__file__))
from test_delivery_redrive import CONTROL, DEFECT, World, _git  # noqa: E402

GROUP = "Work the run tried and rolled back"
HELD_SOURCE = ("import { a } from './a';\n"
               "const client = makeClient();\n"
               "it('the case only this set holds', () => {\n"
               "  const got = client.fetch(a);\n"
               "  expect(got.status).toBe(200);\n"
               "});\n")


def _logged(leerie, monkeypatch) -> list[str]:
    lines: list[str] = []
    real = leerie.log

    def spy(msg, *a, **k):
        lines.append(str(msg))
        return real(msg, *a, **k)
    monkeypatch.setattr(leerie, "log", spy)
    return lines


def _group(w) -> dict:
    return w.st.data["delivery_redrive"]["rounds"][0]["groups"][0]


def _commit_new(w, rel: str, text: str) -> None:
    (w.staging / rel).write_text(text)
    _git(w.staging, "add", rel)
    _git(w.staging, "commit", "-qm", "add a test")


def test_a_group_voided_by_the_span_check_says_so_everywhere(
        leerie, tmp_path, monkeypatch):
    """The d7c06fe3 shape: the group fixed what it was sent to fix and is
    voided only by a span of a shown set's source in its commits."""
    logged = _logged(leerie, monkeypatch)
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    (tmp_path / "set1" / DEFECT).write_text(HELD_SOURCE)
    start = _git(w.staging, "rev-parse", "HEAD")

    def fix_with_a_copy(w):
        w.write("export const a = 1; // FIXED\n")
        _commit_new(w, "src/mine.test.ts", HELD_SOURCE)
        fix_with_a_copy.head = _git(w.staging, "rev-parse", "HEAD")
    w.edits = [fix_with_a_copy]
    w.caps["subtask_redrive_rounds"] = 1
    w.run()
    ge = _group(w)
    assert ge["fixed_cases"] and not ge["worse"] and not ge["zero_progress"]
    assert ge["committed_staged"] == ["src/mine.test.ts"]
    assert ge["rolled_back"] is True
    assert _git(w.staging, "rev-parse", "HEAD") == start
    why = ge["rollback_reason"]
    assert "src/mine.test.ts" in why and "held-out" in why
    (row,) = w.st.data["round_rollbacks"]
    assert row["reason"] == why and row["rolled_back"] is True
    assert (row["pre_sha"], row["post_sha"]) == (start, fix_with_a_copy.head)
    assert any("r1g0" in x and why in x and start[:12] in x for x in logged)
    body = leerie._format_pr_end_state_section(w.st.data)
    _, rest = body.split("## Not verified by this run", 1)
    assert GROUP in rest and "delivery re-drive r1g0" in rest
    assert "src/mine.test.ts" in rest


def test_every_reason_a_group_can_be_rolled_back_for_is_named(leerie):
    reason = leerie._redrive_rollback_reason
    assert "fixed no failing case" in reason({"zero_progress": True})
    assert "1 held-out control" in reason({"ctl_new": ["1:c"]})
    got = reason({"new_failures": ["t::x"], "broke_cases": ["a", "b"]})
    assert "t::x" in got and "broke 2 shown and 0 held-back" in got
    got = reason({"gate": {"void": True, "weakened": ["w.test.ts"]},
                  "ratchet_regressed": [3]})
    assert "w.test.ts" in got and "[3]" in got
    assert "could not run" in reason({"gate": {"void": True, "ast": None}})
    # Several conditions: all of them, none hidden by the first.
    got = reason({"committed_staged": ["p"], "zero_progress": True})
    assert "p" in got and "fixed no failing case" in got
    # `worse` from the held-back rule alone has no list of its own to read.
    assert "held-back" in reason({"worse": True})


def test_a_zero_progress_group_says_why(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // nothing\n")]
    w.caps["subtask_redrive_rounds"] = 1
    w.run()
    ge = _group(w)
    assert ge["rolled_back"] is True and ge["zero_progress"] is True
    assert "fixed no failing case" in ge["rollback_reason"]
    assert ge["rollback_reason"] in str(w.st.data["needs_human_verification"])


def test_a_trade_names_the_control_it_broke(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED BROKE\n")]
    w.caps["subtask_redrive_rounds"] = 1
    w.run()
    ge = _group(w)
    assert ge["ctl_new"] == [f"1:{CONTROL}"]
    assert "held-out control" in ge["rollback_reason"]


def test_a_kept_group_records_no_rollback(leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.install(monkeypatch)
    w.edits = [lambda w: w.write("export const a = 1; // FIXED\n")]
    w.run()
    assert "rollback_reason" not in _group(w)
    assert "round_rollbacks" not in w.st.data
    assert GROUP not in leerie._format_pr_end_state_section(w.st.data)


class _St:
    def __init__(self) -> None:
        self.data: dict = {}

    def save(self) -> None:
        pass


def test_a_resume_finishing_the_same_reset_lists_it_once(leerie):
    st = _St()
    leerie._record_round_rollback(st, "acceptance repair", "round 1", "why",
                                  pre_sha="a" * 40, post_sha="b" * 40)
    leerie._record_round_rollback(st, "acceptance repair", "round 1", "why",
                                  pre_sha="a" * 40)
    (row,) = st.data["round_rollbacks"]
    assert row["post_sha"] == "b" * 40      # the first read of the head
    assert len(st.data["needs_human_verification"]) == 1
    # A different round is a different row.
    leerie._record_round_rollback(st, "acceptance repair", "round 2", "why",
                                  pre_sha="c" * 40)
    assert len(st.data["round_rollbacks"]) == 2
    assert len(st.data["needs_human_verification"]) == 2


def test_a_reset_that_failed_says_the_commits_remain(leerie, monkeypatch):
    logged = _logged(leerie, monkeypatch)
    st = _St()
    leerie._record_round_rollback(st, "conformer", "s1 round 0", "why",
                                  pre_sha="a" * 40, landed=False)
    assert st.data["round_rollbacks"][0]["rolled_back"] is False
    assert "STILL ON THE BRANCH" in \
        st.data["needs_human_verification"][0]["detail"]
    assert any(x.lstrip().startswith("WARNING") for x in logged)


def test_what_to_do_survives_the_pr_entry_cap(leerie):
    st = _St()
    leerie._record_round_rollback(st, "conformer", "s1 round 0", "x" * 2000,
                                  pre_sha="a" * 40)
    body = leerie._format_pr_end_state_section(st.data)
    (line,) = [ln for ln in body.splitlines() if "s1 round 0" in ln]
    assert "its commits are not in the PR" in line
    assert len(line) < leerie._PR_ENTRY_MAX + 20


# What the direct callers of `_restore_tree` take away, by enclosing function.
# None of it is a worker's round; a new entry here is a claim to check.
NOT_A_ROUND = {
    "_roll_back_round": "the one way to roll a round back",
    "_undo_interrupted_redrives": "records it: `_record_round_rollback`, "
                                  "since it may move a ref instead",
    "_sample": "the falsifier's disposable checkout, between samples",
    "_settle_subtask": "a reaped implementer's uncommitted debris",
    "_back_out_unmerged": "a merge no integrator produced; its own record",
    "_back_out": "a merge the gate gave up on; its own record",
    "_reintegrate_settled_head": "a merge restarted, no worker ran",
    "_restart_integration_merge": "a merge restarted for a retried worker",
    "_run_integration_judge_gate": "a resume dropping dirt or restoring the "
                                   "recorded merge",
}


def test_only_known_callers_reset_a_tree_without_a_reason():
    """Derived from the code, both ways: a new direct caller fails here until
    someone says it takes away no worker's round, and an entry whose caller
    is gone fails too."""
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    callers: set[str] = set()

    def walk(node, fn):
        for ch in ast.iter_child_nodes(node):
            name = (ch.name if isinstance(
                ch, (ast.FunctionDef, ast.AsyncFunctionDef)) else fn)
            if (isinstance(ch, ast.Call) and isinstance(ch.func, ast.Name)
                    and ch.func.id == "_restore_tree"):
                callers.add(fn)
            walk(ch, name)
    walk(tree, "<module>")
    assert callers == set(NOT_A_ROUND)
