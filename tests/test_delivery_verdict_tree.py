"""The delivery verdict the outcome reads is of the tree that ships (DESIGN §6
*Every statement about the result is keyed to the commit it was made on*).

Driven for real over a real run repository and staging, on the replay of
tests/test_met_items_refalsified_after_kept_rounds.py: the real
`_resolve_in_flight_redrive`, `_run_delivery_detection`,
`_run_delivery_redrive`, `_acceptance_item_flips`, `_delivery_latest_verdict`
and `_decide_outcome`; only `claude_p` (and the judge, in two cases) is
scripted. Every case asserts the outcome never gives the reason that the
required items were not read on the tree that ships."""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import run_git_out as _git
from tests.test_met_items_refalsified_after_kept_rounds import (  # noqa: F401
    EFFORTS, MODELS, Replay, _fix_mul, _quiet_host)

MISMATCH = "not read on the tree that ships"
MISMATCH_PREFIX = "the required items were not read on the tree that ships: "
UNVERIFIED = "1 required item(s) not re-verified on the tree that ships"
DROPPED = (MISMATCH_PREFIX + "staging's HEAD moved after the delivery verdict "
           "and the items were not read there")
UNTESTED = ("the tests were not measured on the tree that ships (no "
            "failing-test-ID diff was taken of it)")


def _head(r) -> str:
    return _git(r.staging, "rev-parse", "HEAD")


def _assert_stamp_is_shipped(leerie, r) -> None:
    """Whatever verdict stands is of the tree that ships, or none stands."""
    v = leerie._delivery_latest_verdict(r.st.data.get("delivery_gate"))
    if v is not None:
        assert v[1] == leerie._shipping_tree(r.st), v


def _assert_no_mismatch(leerie, r) -> None:
    _assert_stamp_is_shipped(leerie, r)
    leerie._decide_outcome(r.st, any_commits=True)
    reasons = " | ".join(map(str, [r.st.data.get("outcome"),
                                   r.st.data.get("outcome_reasons"),
                                   r.st.data.get("outcome_reason")]))
    assert MISMATCH not in reasons, reasons


def _restamp(leerie, r) -> None:
    asyncio.run(leerie._restamp_verdict_on_head(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS, r.staging))


def _reasons(leerie, r) -> list:
    leerie._decide_outcome(r.st, any_commits=True)
    return list(r.st.data.get("outcome_reasons") or [])


def _assert_restamped(leerie, r) -> None:
    """The verdict stands on staging's HEAD with its items re-read there."""
    gate = r.st.data["delivery_gate"]
    assert gate["final_sha"] == _head(r) != r.head
    assert gate["final_tree"] == leerie._shipping_tree(r.st)
    assert gate["final_tree"] == _git(r.staging, "rev-parse", "HEAD^{tree}")
    assert "verdict_dropped" not in gate
    rows = {x["item_index"]: x for x in gate["items"]}
    assert rows[0]["verdict"] == rows[1]["verdict"] == "met"
    assert all(x["redrive_recheck"]["sha"] == _head(r) for x in rows.values())
    assert gate["unmet_final"] == []


def _detect(leerie, r) -> None:
    asyncio.run(leerie._run_delivery_detection(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS))


def test_plain_run_kept_group_with_unverified_item_stamps_shipped_tree(
        leerie, tmp_path, monkeypatch):
    """A kept group moves HEAD while the ratchet leaves an item unverified:
    the re-drive does not re-key (the item was not read there). The phase's
    exit re-reads the items on HEAD; the judge is down, so the read fails and
    the fallback drops the final verdict in its own words."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul,
               judge_crash_after_drive=True)
    r.run()
    assert _head(r) != r.head
    assert r.st.data["delivery_gate"]["final_sha"] == r.head

    async def down(_ld, _st, _c, _m, _e, _s, idx):
        # The unverified (unknown) row is read too, and that read fails.
        assert 0 in idx
        return {i: {"unmet": True, "by": [], "evidence": "",
                    "crashed": i == 0} for i in idx}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", down)
    _restamp(leerie, r)
    gate = r.st.data["delivery_gate"]
    assert gate["verdict_dropped"] == ["unmet_final"]
    for k in leerie._FINAL_VERDICT_KEYS:
        assert k not in gate, k
    assert _reasons(leerie, r) == [UNVERIFIED, DROPPED], r.st.data


def test_plain_run_unverified_item_read_on_head_is_no_longer_unverified(
        leerie, tmp_path, monkeypatch):
    """The same kept group with an item left unverified; the phase's exit
    reads every item on HEAD and the read succeeds: the item is settled, so
    the outcome no longer says it was not re-verified."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul,
               judge_crash_after_drive=True)
    r.run()
    assert leerie._ratchet_record(r.st).get("unverified")

    async def met(_ld, _st, _c, _m, _e, _s, idx):
        assert 0 in idx
        return {i: {"unmet": False, "by": [], "evidence": ""} for i in idx}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", met)
    asyncio.run(leerie._settle_delivery_verdict(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS))
    assert not leerie._ratchet_record(r.st).get("unverified")
    gate = r.st.data["delivery_gate"]
    assert gate["final_sha"] == _head(r) != r.head
    assert gate["unmet_final"] == []
    assert UNVERIFIED not in _reasons(leerie, r)
    _assert_no_mismatch(leerie, r)


def test_a_pass_that_raises_mid_group_is_undone_before_any_read(
        leerie, tmp_path, monkeypatch):
    """The re-drive raises while a group is in flight on a moved HEAD. The
    phase's exit undoes that round first: staging returns to the group's
    `pre_sha`, and no item is read on the half-done tree."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul)
    # Die on the first save after the kept group moved HEAD.
    real_save = type(r.st).save
    state = {"armed": True}

    def save(self):
        real_save(self)
        if state["armed"] and _head(r) != r.head:
            state["armed"] = False
            raise RuntimeError("pass died")
    r.st.save = save.__get__(r.st)
    with pytest.raises(RuntimeError):
        r.run()
    r.st.save = real_save.__get__(r.st)
    half_done = _head(r)
    assert half_done != r.head
    flight = r.st.data["delivery_redrive"]["in_flight"]
    reads: list = []

    async def no_read(_ld, _st, _c, _m, _e, _s, idx):
        reads.append((_head(r), list(idx)))
        return {i: {"unmet": False, "by": [], "evidence": ""} for i in idx}
    monkeypatch.setattr(leerie, "_redrive_recheck_items", no_read)
    asyncio.run(leerie._settle_delivery_verdict(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS))
    assert "in_flight" not in r.st.data["delivery_redrive"]
    assert _head(r) == flight["pre_sha"] != half_done
    assert all(h != half_done for h, _ in reads), reads
    gate = r.st.data["delivery_gate"]
    assert gate["final_sha"] == _head(r)
    assert "verdict_dropped" not in gate
    _assert_no_mismatch(leerie, r)


def test_detection_that_cannot_judge_the_moved_head_leaves_no_old_verdict(
        leerie, tmp_path, monkeypatch):
    """Staging's HEAD moved past every recorded verdict and detection cannot
    judge it (the commit is unreadable): the read fails, so the final verdict
    is dropped in its own words; the earlier trees' verdicts stay."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul)
    (r.staging / "x.txt").write_text("x\n")
    _git(r.staging, "add", "-A")
    _git(r.staging, "commit", "-qm", "moved")
    r.st.data["delivery_gate"]["unmet_after"] = []
    r.st.data["delivery_gate"]["judged_after"] = {"sha": r.head,
                                                  "tree": "old"}

    async def unreadable(_p):
        return None
    monkeypatch.setattr(leerie, "_branch_head_sha", unreadable)
    _detect(leerie, r)
    gate = r.st.data["delivery_gate"]
    assert gate["verdict_dropped"] == ["unmet_final"]
    for k in leerie._FINAL_VERDICT_KEYS:
        assert k not in gate, k
    assert gate["unmet_after"] == [] and gate["judged_after"]["sha"] == r.head
    reasons = _reasons(leerie, r)
    assert reasons == [DROPPED, UNTESTED]


def test_a_flip_check_that_returns_early_re_stamps_the_moved_head(
        leerie, tmp_path, monkeypatch):
    """`_acceptance_item_flips` returns early (nothing kept / not the
    verdict's head): the verdict of another commit than staging's HEAD is
    re-read there and re-stamped."""
    r = Replay(leerie, tmp_path, monkeypatch, edit=_fix_mul)
    (r.staging / "x.txt").write_text("x\n")
    _git(r.staging, "add", "-A")
    _git(r.staging, "commit", "-qm", "moved")
    r.st.data["acceptance"] = {"gate": {"rounds": []}, "sets": []}
    r.st.save()
    asyncio.run(leerie._acceptance_item_flips(
        r.st.run_dir, r.st, r.caps, MODELS, EFFORTS))
    _assert_restamped(leerie, r)
    _assert_no_mismatch(leerie, r)
    assert _reasons(leerie, r) == [UNTESTED]
