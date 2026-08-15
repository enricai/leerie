"""A waiver must not delete the finding it waives.

Both accept verbs are reached from a `die()` that tells the operator to consult
`state.json`. `accept-blocked` then set `subtask_status[sid] = "complete"`,
popped the `blocked` registry and wrote nothing else — so afterwards a waived
subtask was byte-indistinguishable from one that genuinely succeeded, and the
blocker it was waived for was gone. Following the tool's own advice destroyed
the evidence that advice pointed at. In one real run,
`grep -c blocked state.json` is 0 for a subtask that was blocked and waived.

`accept-integration` had the same shape, popping `integration_defects`.

Both also wrote through `NamedTemporaryFile` + `os.replace`, which keeps the
SOURCE's mode — silently tightening `state.json` from 0644 to 0600, differing
from how the orchestrator writes the same file.

See docs/POSTMORTEM-2026-08-14.md, F16.
"""
from __future__ import annotations

import json
import os
import re
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _mutator(name: str) -> str:
    """Extract a mutator's python body from the launcher.

    Extracted rather than reproduced: a hand-copied body is blind by
    construction, so no change to the launcher could reach these tests.
    """
    src = LAUNCHER.read_text()
    m = re.search(rf"{name}='\n(.*?)\n'\n", src, re.S)
    assert m, f"could not find the {name} mutator in the launcher"
    return m.group(1)


def _run(body: str, *argv: str) -> subprocess.CompletedProcess:
    return subprocess.run(["python3", "-c", body, *argv],
                          capture_output=True, text=True)


def _state(tmp_path: Path, data: dict, mode: int = 0o644) -> Path:
    p = tmp_path / "state.json"
    p.write_text(json.dumps(data, indent=2))
    os.chmod(p, mode)
    return p


class TestAcceptBlocked:
    def _body(self):
        return _mutator("_ab_mutate")

    def test_records_what_it_waived(self, tmp_path):
        st = _state(tmp_path, {
            "subtask_status": {"bugfix-004": "blocked"},
            "blocked": {"bugfix-004": "completeness gate: 1 unhandled case"},
            "waves": [["bugfix-004"]],
        })
        r = _run(self._body(), str(st), "bugfix-004")
        assert r.returncode == 0, r.stderr
        after = json.loads(st.read_text())
        assert after["subtask_status"]["bugfix-004"] == "complete"
        rec = after["accepted_blocked"]["bugfix-004"]
        assert rec["previous_status"] == "blocked"
        assert "completeness gate" in str(rec["blocker"]), (
            "the blocker must survive the waiver — it is the one thing the "
            "operator will want afterwards")
        assert rec["at"].endswith("Z")
        assert rec["forced"] is False

    def test_a_waived_subtask_is_distinguishable_from_a_clean_one(self, tmp_path):
        """The property the missing record cost.

        Without `accepted_blocked` the two states below are byte-identical.
        """
        common = {"waves": [["bugfix-004"]]}
        d = tmp_path / "a"; d.mkdir()
        st = _state(d, {**common,
                        "subtask_status": {"bugfix-004": "blocked"},
                        "blocked": {"bugfix-004": "x"}})
        assert _run(self._body(), str(st), "bugfix-004").returncode == 0
        after = json.loads(st.read_text())
        clean = {**common, "subtask_status": {"bugfix-004": "complete"}}
        assert after != clean
        assert "accepted_blocked" in after

    def test_preserves_the_file_mode(self, tmp_path):
        st = _state(tmp_path, {
            "subtask_status": {"bugfix-004": "blocked"},
            "blocked": {"bugfix-004": "x"}, "waves": [["bugfix-004"]],
        }, mode=0o644)
        assert _run(self._body(), str(st), "bugfix-004").returncode == 0
        assert stat.S_IMODE(os.stat(st).st_mode) == 0o644, (
            "NamedTemporaryFile creates 0600 and os.replace keeps the source "
            "mode, so writing this way silently tightened state.json")

    def test_a_noop_is_still_a_noop(self, tmp_path):
        st = _state(tmp_path, {
            "subtask_status": {"bugfix-004": "complete"}, "waves": [["bugfix-004"]]})
        r = _run(self._body(), str(st), "bugfix-004")
        assert r.returncode == 0 and "NOOP" in r.stdout
        assert "accepted_blocked" not in json.loads(st.read_text()), (
            "an already-complete subtask was not waived, so nothing should be "
            "recorded")


class TestAcceptIntegration:
    def _body(self):
        return _mutator("_ai_mutate")

    def test_records_what_it_waived(self, tmp_path):
        st = _state(tmp_path, {
            "integration_gate": {"feat-008": {"defects": [], "accepted": False}},
            "integration_defects": {"feat-008": [{"kind": "behavioral"}]},
        })
        r = _run(self._body(), str(st), "feat-008")
        assert r.returncode == 0, r.stderr
        entry = json.loads(st.read_text())["integration_gate"]["feat-008"]
        assert entry["accepted"] is True
        assert entry["accepted_at"].endswith("Z")
        assert entry["accepted_defects"] == [{"kind": "behavioral"}], (
            "the accepted finding must survive on the entry that accepted it")

    def test_preserves_the_file_mode(self, tmp_path):
        st = _state(tmp_path, {
            "integration_gate": {"feat-008": {"accepted": False}},
        }, mode=0o644)
        assert _run(self._body(), str(st), "feat-008").returncode == 0
        assert stat.S_IMODE(os.stat(st).st_mode) == 0o644


@pytest.mark.parametrize("name", ["_ab_mutate", "_ai_mutate"])
def test_no_apostrophes_in_the_mutator_bodies(name):
    """These live inside SINGLE-QUOTED shell strings.

    One apostrophe anywhere — including in a prose comment — closes the string
    and the launcher stops parsing. That is not hypothetical: adding a comment
    containing "the tool's" broke `bash -n leerie` while writing this change.
    """
    assert "'" not in _mutator(name), (
        f"{name} is embedded in a single-quoted shell string; an apostrophe "
        "anywhere in it breaks the launcher")
