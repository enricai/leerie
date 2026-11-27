"""A fixer's probe objection is recorded and changes nothing (DESIGN §8
*Delivery re-drive* rule 4): no routing, no verdict, no withdrawn probe, and
no worker rules on it. The one shared verdict (`_probe_verdict`) reads a
failure away from the quoted assertion as unmeasured and carries every
failing run's messages. Drives the real `_run_delivery_redrive` through the
fakes of tests/test_shown_probe_contest.py; synthetic placeholders only."""
from __future__ import annotations

import pytest

from tests.test_shown_probe_contest import (  # noqa: F401
    PROBE_REL, TASK, World, _groups, _quiet_host)

OBJECTION = {"probe_test_file": PROBE_REL, "kind": "input_outside_domain",
             "reason": "synthetic reason"}


def test_no_objection_worker_exists(leerie):
    assert "probe_objection_judge" not in leerie.SCHEMAS
    assert "probe_objection_judge" not in leerie.WORKER_TYPES
    assert not hasattr(leerie, "_probe_objection_ruling")
    assert not hasattr(leerie, "_redrive_withdraw_probe")
    assert not any(n.startswith("PROBE_OBJECTION_") and n != "PROBE_OBJECTION_KINDS"
                   for n in dir(leerie))


def test_an_objection_is_recorded_and_changes_no_routing(
        leerie, tmp_path, monkeypatch):
    w = World(leerie, tmp_path)
    w.answers = [{"probe_objections": [OBJECTION]},
                 {"probe_objections": [OBJECTION]}]
    rec = w.run(monkeypatch)
    # Still driven again: the objection exempted nothing, withdrew nothing.
    assert len(w.calls) == 2
    assert [o["probe_test_file"] for o in rec["probe_objections"]] == [
        PROBE_REL, PROBE_REL]
    assert all("ruling" not in o for o in rec["probe_objections"])
    assert rec["contested_probes"] == []
    assert not any("withdrawn" in k for k in rec)
    assert leerie._decide_outcome(w.st, any_commits=True) == "unconverged"
    reasons = w.st.data["outcome_reasons"]
    assert not any("unrouted" in r for r in reasons)


def test_objection_of_an_unhanded_probe_is_dropped(leerie):
    probes = [{"item_index": 0, "defect_files": [PROBE_REL]}]
    out = leerie._redrive_probe_objections(
        [OBJECTION, {**OBJECTION, "probe_test_file": "other.py"},
         {**OBJECTION, "kind": "bogus"}], probes)
    assert out == [{"item_index": 0, "probe_test_file": PROBE_REL,
                    "kind": "input_outside_domain",
                    "reason": "synthetic reason"}]


QUOTE = "assert total() == 5"
SRC = "def test_t():\n    setup()\n    assert total() == 5\n"
FACTS = {"steps": [], "asserts": [{"start": 3, "end": 3}]}


@pytest.mark.parametrize("msg,want", [
    ("p.py:2: TypeError: setup broke", "broken"),
    ("p.py:3: AssertionError: assert 4 == 5", "fail")])
def test_probe_verdict_reads_a_non_assertion_failure_as_not_fail(
        leerie, msg, want):
    assert leerie._probe_verdict([msg], "p.py", SRC, QUOTE,
                                 steps=[], asserts=FACTS["asserts"]) == want
    assert leerie._probe_verdict([], "p.py", SRC, QUOTE) == "pass"
