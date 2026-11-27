"""A falsifier probe has one verdict definition — it fails AT its quoted
assertion — at confirmation and at every re-run (DESIGN §8 *Delivery
re-drive*): `_redrive_probe_verdicts`, `_redrive_recheck_items` and
`_redrive_probe_flips` all read a saved probe's re-run through the shared
`_probe_verdict`.

A probe whose run fails in its own setup (a hook that returns a callable the
runner then calls as cleanup) names the setup line, not the assertion line:
that is the environment, not the item, and must never read as `fail`,
unmet or a pass-to-fail flip. Only synthetic placeholders here."""
from __future__ import annotations

import asyncio

import pytest

REL = "probe_case.py"
SOURCE = ("def test_total():\n"
          "    setup_hook(make_mock())\n"
          "    assert total() == 5\n")
QUOTE = "assert total() == 5"
CID = f"{REL} :: test_total"
SETUP_MSG = f"{REL}:2: TypeError: hook result is not callable"
ASSERT_MSG = f"{REL}:3: AssertionError: assert 4 == 5"
FACTS = {"steps": [], "asserts": [{"start": 3, "end": 3}]}


@pytest.fixture
def world(leerie, tmp_path, monkeypatch):
    probe_dir = tmp_path / "saved"
    probe_dir.mkdir()
    (probe_dir / REL).write_text(SOURCE)
    st = leerie.State(tmp_path / "s", "r")
    st.data = {"task": "t", "worker_count": 0}
    box = {"msg": SETUP_MSG}

    async def facts(*a, **k):
        return dict(FACTS)

    async def evaluate(st_, caps, tree, sets, label, rev=None):
        return [{"index": s["index"], "passed": False,
                 "failing_run_messages": {REL: {CID: [box["msg"]]}}}
                for s in sets]
    monkeypatch.setattr(leerie, "_probe_case_facts_placed", facts)
    monkeypatch.setattr(leerie, "_evaluate_acceptance_sets", evaluate)
    return st, probe_dir, box


def _probe(probe_dir):
    return {"item_index": 0, "dir": str(probe_dir), "defect_files": [REL],
            "assertion_quote": QUOTE}


@pytest.mark.parametrize("msg,want", [(SETUP_MSG, "unmeasured"),
                                      (ASSERT_MSG, "fail")])
def test_probe_verdicts_read_the_shared_verdict(leerie, world, tmp_path,
                                                msg, want):
    st, probe_dir, box = world
    box["msg"] = msg
    out = asyncio.run(leerie._redrive_probe_verdicts(
        st, dict(leerie.DEFAULT_CAPS), tmp_path, [_probe(probe_dir)], "l",
        rev="HEAD"))
    assert out == {0: want}


@pytest.mark.parametrize("msg,unmet", [(SETUP_MSG, False),
                                       (ASSERT_MSG, True)])
def test_recheck_items_read_the_shared_verdict(leerie, world, tmp_path,
                                               msg, unmet):
    st, probe_dir, box = world
    box["msg"] = msg
    st.data["required_items"] = [{"item": "total is 5"}]
    st.data["delivery_gate"] = {
        "unmet_final": [{"item_index": 0, "by": ["falsifier"],
                         "evidence": "ev"}],
        "items": [{"item_index": 0, "falsifier": {"samples": [{
            "probe_saved": {"dir": str(probe_dir), "file": REL},
            "assertion_quote": QUOTE, "case_id": CID}]}}]}

    out = asyncio.run(_recheck(leerie, st, tmp_path))
    assert out[0]["unmet"] is unmet


def _recheck(leerie, st, tmp_path):
    async def go():
        orig = leerie._redrive_falsify_met

        async def none(*a, **k):
            return None
        leerie._redrive_falsify_met = none
        try:
            return await leerie._redrive_recheck_items(
                tmp_path, st, dict(leerie.DEFAULT_CAPS), {}, {}, tmp_path,
                [0])
        finally:
            leerie._redrive_falsify_met = orig
    return go()


@pytest.mark.parametrize("msg,post,flip", [(SETUP_MSG, "unmeasured", False),
                                           (ASSERT_MSG, "fail", True)])
def test_probe_flips_read_the_shared_verdict(leerie, world, tmp_path,
                                             monkeypatch, msg, post, flip):
    st, probe_dir, box = world
    box["msg"] = msg
    smp = {"probe_saved": {"dir": str(probe_dir), "file": REL},
           "case_id": CID, "assertion_quote": QUOTE}

    def checkout(st_, name, sha, writer=None):
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        return d

    async def deps(*a, **k):
        return True

    async def capture(tree, cmd, st_, caps, **k):
        if "post" in k["label"]:
            return {"ids": [CID], "passed": [], "messages": {CID: msg}}
        return {"ids": [], "passed": [CID], "messages": {}}

    async def confirm(cands, *a, **k):
        return {"new": list(cands)}
    monkeypatch.setattr(leerie, "_falsifier_checkout", checkout)
    monkeypatch.setattr(leerie, "_ensure_worktree_deps", deps)
    monkeypatch.setattr(leerie, "_acceptance_cmd", lambda *a, **k: ["x"])
    monkeypatch.setattr(leerie, "_capture_failing_test_ids", capture)
    monkeypatch.setattr(leerie, "_confirm_new_failures", confirm)
    monkeypatch.setattr(leerie, "_remove_acceptance_checkout",
                        lambda *a, **k: None)
    out = asyncio.run(leerie._redrive_probe_flips(
        st, dict(leerie.DEFAULT_CAPS), smp, "pre", "post", label="l"))
    assert out["post"] == post and out["flip"] is flip


# --- a fixer's probe objection is kept as its words, never ruled on ---

def test_objection_is_kept_unverified_and_never_ruled_on(leerie, monkeypatch):
    async def no_worker(*a, **k):
        raise AssertionError("no worker may be called to rule")
    monkeypatch.setattr(leerie, "claude_p", no_worker)
    probes = [{"item_index": 0, "defect_files": [REL]}]
    objs = leerie._redrive_probe_objections(
        [{"probe_test_file": "sub/" + REL, "kind": "input_outside_domain",
          "reason": "r"}], probes)
    assert objs == [{"item_index": 0, "probe_test_file": REL,
                     "kind": "input_outside_domain", "reason": "r"}]
    assert "ruling" not in objs[0]
    text = leerie._redrive_objection_text(objs[0])
    assert "not verified by leerie; it exempts nothing" in text
    by_item = leerie._redrive_objections_by_item(objs)
    assert "ruling" not in by_item[0][0]
