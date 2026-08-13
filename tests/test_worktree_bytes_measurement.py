"""The EXDEV/hardlink figures DESIGN quotes must come from a committed
measurement, not from prose (N30).

DESIGN §6 explains why a worktree's cost varies by roughly 20x with a mount
topology leerie does not control, and quotes three numbers to make the case.
Those numbers existed only as sentences: no script, no fixture, and one of
them (the hardlinked share) was silently revised from 95.4% to 95.18% with
no artifact behind either value.

The work order's rule for this item was explicit — land the measurement
rather than guessing — and `scripts/measure/worker_durations.py` plus
`tests/fixtures/worker_duration/summary.json` are the house pattern for
satisfying it. `scripts/measure/worktree_bytes.py` and this file are the
same pattern for N30.

Note what this does NOT do: it does not feed a gate. A measured per-worktree
disk bound was attempted four times and withdrawn (docs/IMPLEMENTATION.md
"Disk headroom (N30)"); the shipped rule is a proportional free-space floor.
The measurement exists so the *explanation* is reproducible.
"""
from __future__ import annotations

import json
import pathlib
import re
import subprocess
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "measure" / "worktree_bytes.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "worktree_bytes" / "summary.json"
DESIGN = REPO_ROOT / "docs" / "DESIGN.md"


@pytest.fixture(scope="module")
def summary() -> dict:
    return json.loads(FIXTURE.read_text())


def test_script_and_fixture_both_exist():
    """Anti-vacuity: every assertion below reads one of these two."""
    assert SCRIPT.exists(), "the measurement script is gone; DESIGN's figures "\
                            "would be back to unreproducible prose"
    assert FIXTURE.exists()


def test_fixture_is_internally_consistent(summary):
    """total = hardlinked + private, and the share is the ratio of the two.

    Catches a hand-edited fixture, which is the failure mode a committed
    number invites — the same reason the sibling duration test re-executes
    its derivation rule rather than trusting the file.
    """
    assert summary["hardlinked_bytes"] + summary["private_bytes"] == \
        summary["total_bytes"]
    assert summary["hardlinked_share"] == pytest.approx(
        summary["hardlinked_bytes"] / summary["total_bytes"])


def test_design_hardlinked_share_matches_the_measurement(summary):
    """The headline figure DESIGN quotes, pinned to the artifact.

    This is the number that was revised once with nothing behind it.
    """
    measured = f"{summary['hardlinked_share'] * 100:.2f}%"
    text = DESIGN.read_text()
    assert measured in text, (
        f"DESIGN does not quote the measured hardlinked share ({measured}); "
        "either the fixture was regenerated against a different tree and "
        "DESIGN was not updated, or DESIGN's figure is invented again")


def test_design_private_remainder_matches_the_measurement(summary):
    """The marginal-cost figure, which is the one that actually drove the
    withdrawn sizing attempts — it is what a per-worktree bound would have
    had to charge."""
    mib = summary["private_bytes"] / 2 ** 20
    text = DESIGN.read_text()
    quoted = re.findall(r"(\d+)\s*MiB", text)
    assert quoted, "DESIGN quotes no MiB figure at all any more"
    assert any(abs(int(q) - mib) < 1.0 for q in quoted), (
        f"DESIGN quotes {sorted(set(quoted))} MiB; the measured private "
        f"remainder is {mib:.1f} MiB")


def test_the_two_regimes_are_both_represented(summary):
    """The point of the measurement is the GAP, not either column.

    Where the store shares a mount, almost everything is hardlinked and the
    next worktree is nearly free; inside leerie's own container layout the
    store is a separate bind mount, `link()` fails with EXDEV, and the same
    tree costs its full size. A fixture showing only one regime would make
    the ~20x claim unsupportable, so assert the shared-mount side is real
    and that the full-copy side is the total.
    """
    assert summary["hardlinked_share"] > 0.5, (
        "this tree shows no meaningful hardlinking, so it cannot support "
        "DESIGN's shared-store half of the argument")
    ratio = summary["total_bytes"] / summary["private_bytes"]
    assert ratio > 10, (
        f"the two regimes differ by only {ratio:.1f}x here; DESIGN claims "
        "roughly 20x, which this fixture would no longer support")


def test_script_runs_and_reproduces_its_own_shape(tmp_path):
    """Execute the real script against a tiny synthetic tree.

    Pins the contract rather than the numbers: same keys, self-consistent
    arithmetic, and — the property the whole measurement rests on — a
    hardlink is charged once, not twice.
    """
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    payload = b"x" * 8192
    (tree / "a.bin").write_bytes(payload)
    (tree / "sub" / "b.bin").write_bytes(payload)
    # A second name for a.bin's inode, inside the walked tree.
    (tree / "sub" / "a-link.bin").hardlink_to(tree / "a.bin")

    out = subprocess.run([sys.executable, str(SCRIPT), str(tree)],
                         capture_output=True, text=True, check=True)
    got = json.loads(out.stdout)

    assert set(got) == set(json.loads(FIXTURE.read_text())), (
        "the script's output shape drifted from the committed fixture")
    assert got["unique_inodes"] == 2, (
        "the hardlinked second name was counted as a separate inode — "
        "de-duplication is the entire basis of the measurement")
    assert got["hardlinked_bytes"] + got["private_bytes"] == got["total_bytes"]
    assert got["hardlinked_bytes"] > 0, (
        "a file with two in-tree names was not charged as hardlinked")


def test_script_rejects_a_non_directory(tmp_path):
    """It is a measurement tool an operator runs by hand; a typo'd path must
    fail loudly rather than report a confident zero."""
    missing = tmp_path / "nope"
    out = subprocess.run([sys.executable, str(SCRIPT), str(missing)],
                         capture_output=True, text=True)
    assert out.returncode != 0
    assert not out.stdout.strip(), "emitted a result for a path that is absent"
