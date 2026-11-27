"""An unbuilt validity tree is reported as unmeasured, not a discard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "orchestrator"))
import leerie as l  # noqa: E402


def test_unmeasured_line_rendered():
    out = l._acceptance_unmeasured_lines(
        {"acceptance": {"unmeasured": [{"index": 2, "error": "boom"}], "sets": {}}})
    assert any("never measured" in x and "boom" in x for x in out)


def test_unmeasured_blocks_all_fixed_by_execution():
    acc = {"unmeasured": [{"index": 1, "error": "x"}], "written": 1,
           "discarded": [{"index": 2, "reason": "fixed_on_head_by_execution"}]}
    assert l._acceptance_all_fixed_by_execution(acc) is False
