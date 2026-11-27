"""A confirmed-unmet process item whose verdict names offending code reaches
the subtasks whose diff touches it, in any language, from typed fields only.

Before the fix a judge-only process item (`by=["judge"]`, no probe) ended
`no_code_target` even when the judge's verdict carried typed `locations`.
The evidence is a synthetic sentence about a blanket catch; the files are
Python and Go, so no JS import graph can be what routes them.
"""
from __future__ import annotations

import os

EVIDENCE = "the handler wraps its body in a blanket catch that hides errors"
ITEM = "no change swallows errors"
SUBFILES = {"s1": ["svc/handler.py", "svc/util.py"],
            "s2": ["cmd/main.go"]}
CLAIMS = {0: {"kind": "process", "claimed_by": ["s1", "s2"]}}
LOCS = [{"path": "svc/handler.py", "start_line": 10, "end_line": 14}]


def _owners(locs):
    paths = {os.path.normpath(str(x.get("path"))) for x in locs}
    return [s for s, fs in SUBFILES.items()
            if paths & {os.path.normpath(f) for f in fs}]


def _u(**kw):
    return {"item_index": 0, "item": ITEM, "evidence": EVIDENCE,
            "by": ["judge"], **kw}


def _notes(leerie, u, claims=CLAIMS, owners=_owners, **kw):
    return leerie._redrive_item_notes([u], claims, 1, [],
                                      location_owners=owners, **kw)


def test_judge_only_process_item_with_locations_reaches_the_owner(leerie):
    notes, recs = _notes(leerie, _u(locations=LOCS))
    assert recs == [] and len(notes) == 1
    n = notes[0]
    assert n["kind"] == "unmet_item" and n["process"] is True
    assert n["claimed_by"] == ["s1"]
    assert n["locations"] == LOCS
    assert n["evidence"] == EVIDENCE and n["by"] == ["judge"]
    assert n["evidence_keys"] == ["judge"]
    assert "probe" not in n


def test_locations_mapping_to_no_subtask_are_no_code_target(leerie):
    notes, recs = _notes(leerie, _u(locations=[{"path": "other/x.rs"}]))
    assert notes == []
    assert [(r["reason"], r["item_index"]) for r in recs] == [
        ("no_code_target", 0)]


def test_judge_only_process_item_without_locations_is_no_code_target(leerie):
    notes, recs = _notes(leerie, _u())
    assert notes == [] and recs[0]["reason"] == "no_code_target"


def test_no_owner_function_cannot_route_locations(leerie):
    notes, recs = _notes(leerie, _u(locations=LOCS), owners=None)
    assert notes == [] and recs[0]["reason"] == "no_code_target"


def test_non_process_item_ignores_locations_and_keeps_claimers(leerie):
    cl = {0: {"kind": "deliverable", "claimed_by": ["s2"]}}
    notes, recs = _notes(leerie, _u(locations=LOCS), claims=cl)
    assert recs == [] and notes[0]["claimed_by"] == ["s2"]
    assert "locations" not in notes[0] and "process" not in notes[0]
    assert notes[0]["evidence_keys"] == ["judge"]
    assert notes[0]["keys"] == notes[0]["evidence_keys"]


def test_key_is_stable_when_locations_change(leerie):
    a = _notes(leerie, _u(locations=LOCS))[0][0]
    b = _notes(leerie, _u(locations=[{"path": "cmd/main.go",
                                      "start_line": 3}]))[0][0]
    assert a["evidence_keys"] == b["evidence_keys"] == ["judge"]
    assert b["claimed_by"] == ["s2"]


def test_probe_routed_process_item_unchanged_by_locations(leerie, tmp_path):
    (tmp_path / "p.py").write_text("x\n")
    row = {"item_index": 0, "verdict": "unmet", "by": ["falsifier"],
           "falsifier": {"samples": [{
               "confirmed": True, "lead_sets": [0],
               "probe_saved": {"dir": str(tmp_path), "file": "p.py"},
               "case_name": "c", "assertion_quote": "a", "reason": "r",
               "item_span_quote": "q"}]}}
    u = _u(by=["falsifier"], locations=LOCS)
    notes, recs = leerie._redrive_item_notes(
        [u], CLAIMS, 1, [row], shown_idx=[0],
        probe_owners=lambda p: ["s2"], location_owners=_owners)
    assert recs == [] and notes[0]["claimed_by"] == ["s2"]
    assert notes[0]["by"] == ["falsifier"] and "probe" in notes[0]
    assert "locations" not in notes[0]       # judge is not a source here


def test_unrouted_accounting_sees_the_located_note_as_driven(leerie):
    notes, recs = _notes(leerie, _u(locations=LOCS))
    ents = leerie._redrive_unrouted_entries(notes, recs)
    handed = [f"0:{k}" for k in notes[0]["evidence_keys"]]
    assert leerie._redrive_unrouted_items(ents, handed, [], False) == []
    # An unlocated item is typed no_code_target.
    n2, r2 = _notes(leerie, _u())
    out = leerie._redrive_unrouted_items(
        leerie._redrive_unrouted_entries(n2, r2), [], [], False)
    assert [o["reason"] for o in out] == ["no_code_target"]


def test_detection_helper_drops_record_and_absent_cites(leerie):
    parts = [{"cites": [
        {"path": "RECORD", "line_start": 1},
        {"path": "ABSENT", "quote": "x"},
        {"path": "ABSENT:svc/*.py", "quote": "y"},
        {"path": "svc/handler.py", "line_start": 10, "line_end": 14},
        {"path": "svc/handler.py", "line_start": 10, "line_end": 14},
        {"path": "cmd/main.go"}]}]
    assert leerie._delivery_unmet_locations(parts) == [
        {"path": "svc/handler.py", "start_line": 10, "end_line": 14},
        {"path": "cmd/main.go"}]
    assert leerie._delivery_unmet_locations(None) == []


def test_apply_recheck_replaces_stale_locations(leerie, tmp_path):
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"required_items": [ITEM], "delivery_gate": {
        "unmet_final": [_u(locations=[{"path": "old.py"}])], "items": []}}
    verdict = {0: {"unmet": True, "by": ["judge"], "evidence": EVIDENCE,
                   "locations": LOCS}}
    leerie._redrive_apply_item_verdicts(st, verdict, "sha")
    assert st.data["delivery_gate"]["unmet_final"][0]["locations"] == LOCS


def test_second_hand_of_same_item_and_key_grants_no_swing(leerie):
    a = _notes(leerie, _u(locations=LOCS))[0]
    assert leerie._redrive_never_handed(a, [])
    handed = [f"0:{k}" for k in a[0]["evidence_keys"]]
    b = _notes(leerie, _u(locations=[{"path": "cmd/main.go",
                                      "start_line": 3}]))[0]
    assert not leerie._redrive_never_handed(b, handed)


# --- line-level ownership against real git ---------------------------------

def _run_repo(tmp_path):
    from tests.conftest import init_git_repo, run_git_repo_first as g
    r = init_git_repo(tmp_path / "r")
    (r / "svc").mkdir()
    (r / "svc/handler.py").write_text("old1\nold2\nold3\n")
    (r / "tests").mkdir()
    (r / "tests/test_h.py").write_text("t\n")
    g(r, "add", ".")
    g(r, "commit", "-q", "-m", "pre-run")
    g(r, "checkout", "-q", "-b", "sub")
    (r / "svc/handler.py").write_text("old1\nold2\nold3\nnew4\nnew5\n")
    (r / "tests/test_h.py").write_text("t\nnew\n")
    g(r, "commit", "-qam", "s1 work")
    g(r, "checkout", "-q", "main")
    g(r, "merge", "-q", "--no-ff", "sub", "-m", "leerie: integrate s1")
    return r


def _lo(leerie, r, locs):
    return leerie._redrive_location_owners(
        r, {}, {"s1": ["svc/handler.py"], "s2": []}, locs)


def test_real_git_run_written_line_routes(leerie, tmp_path):
    r = _run_repo(tmp_path)
    assert _lo(leerie, r, [{"path": "svc/handler.py", "start_line": 4,
                            "end_line": 5}]) == ["s1"]


def test_real_git_pre_run_line_in_touched_file_does_not_route(leerie,
                                                              tmp_path):
    r = _run_repo(tmp_path)
    locs = [{"path": "svc/handler.py", "start_line": 1, "end_line": 3}]
    assert _lo(leerie, r, locs) == []
    notes, recs = _notes(leerie, _u(locations=locs),
                         owners=lambda ls: _lo(leerie, r, ls))
    assert notes == [] and recs[0]["reason"] == "no_code_target"


def test_real_git_location_without_lines_falls_back_to_whole_file(
        leerie, tmp_path):
    r = _run_repo(tmp_path)
    assert _lo(leerie, r, [{"path": "svc/handler.py"}]) == ["s1"]


def test_real_git_test_file_citation_never_routes(leerie, tmp_path):
    r = _run_repo(tmp_path)
    assert _lo(leerie, r, [{"path": "tests/test_h.py", "start_line": 2,
                            "end_line": 2}]) == []
    assert _lo(leerie, r, [{"path": "tests/test_h.py"}]) == []


def test_run_delivery_detection_unmet_final_carries_verified_locations(
        leerie, tmp_path, monkeypatch):
    """Producer side: a judge-unmet item whose part carries a verified cite
    ends in `unmet_final[0]["locations"]` with that path and line range;
    RECORD / ABSENT cites name no file and are dropped."""
    import asyncio
    from tests import test_delivery_detection as dd
    _repo, head, st, caps = dd._detection_env(leerie, tmp_path, monkeypatch)
    cites = [{"path": "calc.py", "line_start": 1, "line_end": 2,
              "quote": "def add(a, b):"},
             {"path": "RECORD", "line_start": 0, "line_end": 0,
              "quote": "x"},
             {"path": "ABSENT", "line_start": 0, "line_end": 0,
              "quote": "x"}]
    gate = st.data["delivery_gate"]
    gate["judged_after"]["unmet"] = [{
        "item_index": 0, "evidence": "add subtracts"}]
    gate["judged_after"]["details"][0]["unmet_parts"] = [{
        "requirement": "add of two positives", "kind": "static",
        "cites": cites}]
    st.save()
    dd._falsifier_stub(leerie, monkeypatch, st,
                       lambda kw, n: dd._claim(
                           verdict="could_not_demonstrate", probe_path=""))
    asyncio.run(leerie._run_delivery_detection(
        st.run_dir, st, caps, dd.MODELS, dd.EFFORTS))
    u = st.data["delivery_gate"]["unmet_final"][0]
    assert u["item_index"] == 0 and "judge" in u["by"]
    assert u["locations"] == [
        {"path": "calc.py", "start_line": 1, "end_line": 2}]


def test_real_git_bracketed_path_blames_literally(leerie, tmp_path):
    from tests.conftest import init_git_repo, run_git_repo_first as g
    r = init_git_repo(tmp_path / "b")
    (r / "app").mkdir()
    (r / "app/[id].py").write_text("old\n")
    g(r, "add", ".")
    g(r, "commit", "-q", "-m", "pre-run")
    g(r, "checkout", "-q", "-b", "sub")
    (r / "app/[id].py").write_text("old\nnew\n")
    g(r, "commit", "-qam", "s1 work")
    g(r, "checkout", "-q", "main")
    g(r, "merge", "-q", "--no-ff", "sub", "-m", "leerie: integrate s1")
    got = leerie._redrive_location_owners(
        r, {}, {"s1": ["app/[id].py"]},
        [{"path": "app/[id].py", "start_line": 2, "end_line": 2}])
    assert got == ["s1"]


# --- direct ownership cases: agnostic filter, outside commits, SHA-256 ------

def _g(r, *a):
    from tests.conftest import run_git_repo_first as g
    return g(r, *a).stdout.strip()


def _lang_repo(tmp_path, name="l", *extra):
    """pre-run commit, then s1 integrates edits to a .c and a .sh file and
    vendored/test files, then a staging commit outside any integrate merge
    adds a line to lib/core.c."""
    from tests.conftest import init_git_repo
    r = init_git_repo(tmp_path / name)
    for f in ("lib/core.c", "bin/run.sh", "node_modules/p/i.js",
              ".venv/v.c", "tests/t.c"):
        (r / f).parent.mkdir(parents=True, exist_ok=True)
        (r / f).write_text("old\n")
    _g(r, "add", ".")
    _g(r, "commit", "-q", "-m", "pre-run")
    base = _g(r, "rev-parse", "HEAD")
    _g(r, "checkout", "-q", "-b", "sub")
    for f in ("lib/core.c", "bin/run.sh", "node_modules/p/i.js",
              ".venv/v.c", "tests/t.c"):
        (r / f).write_text("old\nnew\n")
    _g(r, "commit", "-qam", "s1 work")
    _g(r, "checkout", "-q", "main")
    _g(r, "merge", "-q", "--no-ff", "sub", "-m", "leerie: integrate s1")
    return r, base


def _own(leerie, r, locs, base=None, rec=None, files=None):
    return leerie._redrive_location_owners(
        r, rec or {}, files or {"s1": ["lib/core.c", "bin/run.sh"],
                                "s2": []}, locs)


def test_real_git_unlisted_language_cite_routes_by_blame(leerie, tmp_path):
    r, base = _lang_repo(tmp_path)
    assert _own(leerie, r, [{"path": "lib/core.c", "start_line": 2,
                             "end_line": 2}], base) == ["s1"]
    assert _own(leerie, r, [{"path": "lib/core.c", "start_line": 1,
                             "end_line": 1}], base) == []
    assert _own(leerie, r, [{"path": "bin/run.sh", "start_line": 2}],
                base) == ["s1"]


def test_real_git_unlisted_language_whole_file_fallback(leerie, tmp_path):
    r, base = _lang_repo(tmp_path)
    assert _own(leerie, r, [{"path": "bin/run.sh"}]) == ["s1"]
    # Through a kept re-drive group's diff rather than the subfiles.
    (r / "bin/run.sh").write_text("old\nnew\nkept\n")
    _g(r, "commit", "-qam", "group")
    post = _g(r, "rev-parse", "HEAD")
    rec = {"rounds": [{"groups": [{"pre_sha": base, "post_sha": post,
                                   "members": ["s2"]}]}]}
    assert _own(leerie, r, [{"path": "bin/run.sh"}], rec=rec,
                files={"s1": [], "s2": []}) == ["s2"]


def test_real_git_vendored_and_test_paths_never_route(leerie, tmp_path):
    r, base = _lang_repo(tmp_path)
    files = {"s1": ["node_modules/p/i.js", ".venv/v.c", "tests/t.c"]}
    for p in ("node_modules/p/i.js", ".venv/v.c", "tests/t.c"):
        assert _own(leerie, r, [{"path": p, "start_line": 2}], base,
                    files=files) == []
        assert _own(leerie, r, [{"path": p}], base, files=files) == []


def test_real_git_outside_subtask_commit_routes_to_no_one(leerie, tmp_path):
    r, base = _lang_repo(tmp_path)
    (r / "lib/core.c").write_text("old\nnew\nfix\n")
    _g(r, "commit", "-qam", "conformer fix")
    cite = lambda n: [{"path": "lib/core.c", "start_line": n, "end_line": n}]
    # Line 3 was written by a commit in no subtask's set (a conformer's):
    # no subtask wrote it, so it has no owner, though s1 touched the file.
    assert _own(leerie, r, cite(3), base) == []
    # s1's own line in the same file still routes to s1.
    assert _own(leerie, r, cite(2), base) == ["s1"]
    # A pre-run line of the same file routes to no one.
    assert _own(leerie, r, cite(1), base) == []


def test_real_git_line_of_an_unrouted_subtask_routes_to_no_one(leerie,
                                                              tmp_path):
    r, base = _lang_repo(tmp_path)
    # s2 wrote line 3 through its own integrate merge, but it is not among
    # the routed subtasks (blocked or accepted): it is never handed to s1.
    _g(r, "checkout", "-q", "-b", "s2work")
    (r / "lib/core.c").write_text("old\nnew\ns2\n")
    _g(r, "commit", "-qam", "s2 work")
    _g(r, "checkout", "-q", "main")
    _g(r, "merge", "-q", "--no-ff", "s2work", "-m", "leerie: integrate s2")
    cite = [{"path": "lib/core.c", "start_line": 3, "end_line": 3}]
    assert _own(leerie, r, cite, base,
                files={"s1": ["lib/core.c"]}) == []
    assert _own(leerie, r, cite, base,
                files={"s1": ["lib/core.c"], "s2": ["lib/core.c"]}) == ["s2"]


def test_real_git_sha256_repo_routes(leerie, tmp_path):
    import subprocess
    import pytest
    d = tmp_path / "h"
    d.mkdir()
    p = subprocess.run(["git", "init", "-q", "-b", "main",
                        "--object-format=sha256", str(d)],
                       capture_output=True, text=True)
    if p.returncode != 0:
        pytest.skip("git lacks --object-format=sha256")
    _g(d, "config", "user.email", "t@x")
    _g(d, "config", "user.name", "t")
    (d / "x.c").write_text("old\n")
    _g(d, "add", ".")
    _g(d, "commit", "-q", "-m", "pre-run")
    base = _g(d, "rev-parse", "HEAD")
    assert len(base) == 64
    _g(d, "checkout", "-q", "-b", "sub")
    (d / "x.c").write_text("old\nnew\n")
    _g(d, "commit", "-qam", "s1 work")
    _g(d, "checkout", "-q", "main")
    _g(d, "merge", "-q", "--no-ff", "sub", "-m", "leerie: integrate s1")
    got = leerie._redrive_location_owners(
        d, {}, {"s1": ["x.c"]},
        [{"path": "x.c", "start_line": 2, "end_line": 2}])
    assert got == ["s1"]
    assert leerie._redrive_location_owners(
        d, {}, {"s1": ["x.c"]},
        [{"path": "x.c", "start_line": 1, "end_line": 1}]) == []
