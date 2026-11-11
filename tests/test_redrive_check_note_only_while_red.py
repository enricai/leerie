"""A red merge check's note goes to a re-drive group only while the check is
still red on that group's start (DESIGN §8 *Delivery re-drive* rule 3).

In barnacle 39c4c5ad (leerie 0.60.0) lint was red on the final tree and
named no file, so rule 2 routed its note to every group. Round 1's group 0
turned it green (`1a6d3d8`, measured green by its own ratchet); groups 1-3,
handed only that note, each spent a round on it and reported they could not
reproduce it. Here the same shape runs for real: a real git repo, the real
re-drive, a real lint script, the real final pass and its B* reruns, the
real per-group ratchet measurement. Only `claude_p` (the workers), the
broker cgroup and the host-pressure gate are stubbed, as in
`tests/test_kept_rounds_keep_merge_checks_green.py`.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_delivery_redrive import World
from tests.test_kept_rounds_keep_merge_checks_green import _quiet_ci

# The lint the repository's CI runs: red while `src/a.ts` carries the
# offending token, green otherwise. It names no file, as barnacle's did.
LINT = r'''
import pathlib, sys
if "LINTBAD" in pathlib.Path(sys.argv[1]).read_text():
    print("src/a.ts lint/style/noVar: error")
    sys.exit(1)
'''

ITEMS = [{"item": "a is fixed", "span": "S1", "verifiability": "tree"},
         {"item": "b is fixed", "span": "S2", "verifiability": "tree"}]


def _unmet(idx: list[int]) -> list[dict]:
    return [{"item_index": i, "item": ITEMS[i]["item"], "evidence": "missing",
             "by": ["judge"]} for i in idx]


def _world(leerie, tmp_path, monkeypatch, *, s2_item: bool) -> World:
    real_final = leerie._run_ci_parity_final
    claims = {"s1": [{"item_index": 0, "kind": "deliverable"}]}
    if s2_item:
        claims["s2"] = [{"item_index": 1, "kind": "deliverable"}]
    w = World(leerie, tmp_path, n_sets=0, items=ITEMS,
              unmet=_unmet([0, 1] if s2_item else [0]), claims=claims)
    w.install(monkeypatch)
    monkeypatch.setattr(leerie, "_run_ci_parity_final", real_final)
    _quiet_ci(leerie, monkeypatch, w.caps)
    w.caps["subtask_redrive_rounds"] = 1
    script = tmp_path / "lint.py"
    script.write_text(LINT)
    # The run's own commit broke lint; the base is green.
    w.write("export const a = 1; // LINTBAD\n", "s1 work, unlinted")
    head = _git(w.staging, "rev-parse", "HEAD")
    w.st.data["delivery_gate"]["final_sha"] = head
    w.st.data["delivery_gate"]["final_tree"] = _git(
        w.staging, "rev-parse", "HEAD^{tree}")
    w.st.data["ci_parity"] = {"baseline_done": True, "base_sha": w.base,
                              "checks": [{
                                  "id": "lint", "kind": "lint", "cwd": ".",
                                  "env": [], "prerequisites": [],
                                  "requires": [], "strict_argv": [],
                                  "argv": [sys.executable, str(script),
                                           "src/a.ts"],
                                  "selected": True, "gate": "plain",
                                  "base": {"rc": 0}}]}
    w.st.save()
    # The final pass on the tree the re-drive starts from: lint red,
    # confirmed under B* against the base.
    unmet = asyncio.run(leerie._run_ci_parity_final(w.dir, w.st, w.caps))
    assert [u["id"] for u in unmet] == ["lint"]

    async def met(task, its, staging, st, caps, models, efforts,
                  phase_label, **kw):
        return [], 1, None
    monkeypatch.setattr(leerie, "_delivery_judge_unmet", met)
    monkeypatch.setattr(leerie, "_delivery_record", lambda *a, **k: "")
    monkeypatch.setattr(leerie, "_delivery_typed_evidence",
                        lambda *a, **k: ([], {}))

    async def no_falsifier(st, caps, models, efforts, staging, out):
        return out
    monkeypatch.setattr(leerie, "_redrive_falsify_met", no_falsifier)
    return w


def _write_b(w: World, text: str) -> None:
    (w.staging / "src" / "b.ts").write_text(text)
    _git(w.staging, "commit", "-qam", "fix b")


def _fix_a_and_lint(w: World) -> None:
    w.write("export const a = 1; // FIXED\n", "fix a, lint clean")


CI_NOTE = '"kind": "ci_check"'


def test_a_check_an_earlier_group_turned_green_is_not_handed_on(
        leerie, tmp_path, monkeypatch):
    # 39c4c5ad's shape: group 0 fixes lint; the next group has a finding of
    # its own. It is driven on that, never on the stale lint note.
    w = _world(leerie, tmp_path, monkeypatch, s2_item=True)
    w.edits = [_fix_a_and_lint,
               lambda w: _write_b(w, "export const b = 2; // FIXED\n")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert g0["members"] == ["s1"] and g1["members"] == ["s2"]
    assert len(w.calls) == 2
    # Group 0 was handed the red check and turned it green: its own ratchet
    # measured its commit, and that measurement is what group 1 reads.
    assert CI_NOTE in w.calls[0]["prompt"]
    assert "lint.py src/a.ts" in w.calls[0]["prompt"]
    assert g0["ratchet"]["merge_checks"]["broke"] == []
    memo = w.st.data["ci_parity"]["measured"][g0["post_sha"]]
    assert memo["results"]["lint"]["passed"] is True
    # Group 1 is driven on its own item, without the check.
    assert g1["checks_cleared"] == ["lint"]
    assert g1["notes"] == ["unmet_item"]
    assert CI_NOTE not in w.calls[1]["prompt"]
    assert '"kind": "unmet_item"' in w.calls[1]["prompt"]
    assert w.st.data["redrive_rounds"] == {"s1": 1, "s2": 1}
    # What ships is green, read from the last kept group's measurement.
    fin = w.st.data["ci_parity"]["final"]
    assert fin["sha"] == _git(w.staging, "rev-parse", "HEAD")
    assert fin["unmet"] == []


def test_a_group_left_only_a_green_check_is_skipped_and_not_charged(
        leerie, tmp_path, monkeypatch):
    w = _world(leerie, tmp_path, monkeypatch, s2_item=False)
    w.edits = [_fix_a_and_lint]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert g1["members"] == ["s2"]
    assert g1["checks_cleared"] == ["lint"]
    assert g1["skipped"] == "nothing_left"
    assert "pre_sha" not in g1
    assert len(w.calls) == 1
    assert w.st.data["redrive_rounds"] == {"s1": 1}
    assert w.st.data["ci_parity"]["final"]["unmet"] == []


def test_a_check_no_group_turned_green_still_reaches_every_group(
        leerie, tmp_path, monkeypatch):
    # Group 0 commits work that leaves lint red (its ratchet measures the
    # commit red, red on its start too: kept). The check is still open on
    # group 1's start, so group 1 is handed it and draws its round.
    w = _world(leerie, tmp_path, monkeypatch, s2_item=False)
    w.edits = [lambda w: w.write("export const a = 1; // LINTBAD FIXED\n",
                                 "fix a, lint still red")]
    w.run()
    rec = w.st.data["delivery_redrive"]
    g0, g1 = rec["rounds"][0]["groups"]
    assert not g0.get("rolled_back")
    assert g0["ratchet"]["merge_checks"]["pre_existing"] == ["lint"]
    memo = w.st.data["ci_parity"]["measured"][g0["post_sha"]]
    assert memo["results"]["lint"]["passed"] is False
    assert "checks_cleared" not in g1
    assert g1["notes"] == ["ci_check"]
    assert len(w.calls) == 2
    assert CI_NOTE in w.calls[1]["prompt"]
    assert "lint.py src/a.ts" in w.calls[1]["prompt"]
    assert w.st.data["redrive_rounds"] == {"s1": 1, "s2": 1}
    assert [u["id"] for u in w.st.data["ci_parity"]["final"]["unmet"]] == [
        "lint"]


# --- each kind of red check, read from a kept measurement -------------------

def _st_with(leerie, tmp_path, rec: dict, results: dict | None):
    st = leerie.State(tmp_path / "state", "run-c")
    st.data = {"ci_parity": {"checks": [rec], "base_sha": "b" * 40,
                             "measured": ({"h" * 40: {
                                 "label": "repair redrive-r1g0",
                                 "results": {rec["id"]: results}}}
                                 if results is not None else {})}}
    return st


GATE = {"id": "lint", "kind": "lint", "cwd": ".", "argv": ["x"],
        "selected": True, "gate": "plain", "base": {"rc": 0}}
DIAG = {"id": "eslint", "kind": "lint", "cwd": ".", "argv": ["x"],
        "strict_argv": ["x", "--max-warnings=0"], "selected": True,
        "ratchet": "diagnostic", "base": {"diagnostics": {"src/a.ts\tno-var": 1}}}
COV = {"id": "cov", "kind": "coverage", "cwd": ".", "argv": ["x"],
       "selected": True, "ratchet": "coverage",
       "coverage": {"thresholds": [{"glob": "src/**", "metric": "lines",
                                    "min": 80}]},
       "base": {"coverage": {"src/**|lines": 90.0}}}


@pytest.mark.parametrize("rec,why,results,cleared", [
    (GATE, "red", {"passed": True, "rc": 0}, True),
    (GATE, "red", {"passed": False, "rc": 1}, False),
    (GATE, "red", None, False),
    (DIAG, "new_diagnostics", {"diagnostics": {"src/a.ts\tno-var": 1}}, True),
    (DIAG, "new_diagnostics", {"diagnostics": {"src/a.ts\tno-var": 2}}, False),
    (COV, "coverage_ratchet", {"coverage": {"src/**|lines": 91.0}}, True),
    (COV, "coverage_ratchet", {"coverage": {"src/**|lines": 50.0}}, False),
])
def test_a_check_is_cleared_only_by_a_measurement_reading_it_green(
        leerie, tmp_path, rec, why, results, cleared):
    st = _st_with(leerie, tmp_path, rec, results)
    assert leerie._ci_gating_recs(st), "the record must gate"
    note = {"kind": "ci_check", "key": f"ci-{rec['id']}", "id": rec["id"],
            "why": why}
    got = leerie._redrive_checks_cleared(
        st, dict(leerie.DEFAULT_CAPS), [note], "h" * 40, ["src/a.ts"])
    assert got == ({note["key"]} if cleared else set())
    # Another commit's measurement never clears it.
    assert leerie._redrive_checks_cleared(
        st, dict(leerie.DEFAULT_CAPS), [note], "c" * 40, ["src/a.ts"]) == set()
