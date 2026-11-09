"""The falsifier ships the prompt and payload bench Q2 F+ measured (DESIGN §8
*What ships is what was measured*): the frozen `falsifier_prompt.md` +
`fplus_addendum.md`, verbatim but for the leads directory, and the frozen
user prompt line for line. The Q2 numbers describe only that text, so any
other edit must first be listed in DESIGN — and this pin updated with it."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# sha256 of the frozen falsifier_prompt.md + fplus_addendum.md, concatenated
# as the bench's fplus.py did (bench q2/fp/frozen, 2026-10-26T18:06).
FROZEN_SHA256 = (
    "2e6bbf4db44f68a3132ec1452a6c0e21170914e5d549993e78709586950923df")


# Step 1 as F+ froze it, and as bench SW measured it with the item's parts
# (DESIGN §8 *The falsifier gets the item whole*).
F_PLUS_STEP_1_TAIL = """   Quote the exact span of the item text that states it (`item_span_quote`,
   copied character-for-character from the item)."""
SW_STEP_1_TAIL = """   When the item carries `parts`, each part is one thing it requires, and
   the item is not delivered when any part is not; a part that covers many
   places ("every page", "all the strings", "each caller") is not delivered
   when any one of those places falls short. Quote the exact span of the
   item text that states it (`item_span_quote`, copied
   character-for-character from the item's sentence or from one of its
   parts)."""


def test_system_prompt_is_the_frozen_one_but_for_the_listed_edits(leerie):
    """The frozen F+ text but for the leads directory and bench SW's step-1
    sentence on the item's parts — each edit present exactly once."""
    shipped = (ROOT / "prompts" / "falsifier.md").read_text()
    lead = leerie.FALSIFIER_LEADS_DIR + "/"
    assert shipped.count(lead) == 2
    assert shipped.count(SW_STEP_1_TAIL) == 1
    frozen = shipped.replace(lead, ".q2ctx/").replace(SW_STEP_1_TAIL,
                                                      F_PLUS_STEP_1_TAIL)
    assert hashlib.sha256(frozen.encode()).hexdigest() == FROZEN_SHA256


def _benched(task, item, own, stat, ctx, refs, parts):
    """fplus.py's user prompt (frozen), with the item already mapped."""
    return "\n".join([
        "TASK:", task, "", "Files the task references:",
        *[f"- {r}" for r in refs], "",
        "THE ITEM TO CHECK:", json.dumps(item, indent=1), "",
        f"START COMMIT of this run: {own}. Diff stat of the run's own "
        f"changes (`git diff {own}..HEAD`):",
        stat[-12000:] or "(empty)", "",
        ("HELD-OUT ACCEPTANCE FILES of this run (read-only leads):\n"
         + "\n".join(f"- {x}" for x in ctx[:60]))
        if ctx else "HELD-OUT ACCEPTANCE FILES: none for this run.", "",
        ("A REVIEWER'S OPEN PARTS for this item (hypotheses to test by "
         "execution):\n" + json.dumps(parts, indent=1)[:6000])
        if parts else "", "",
        "Your working directory is a disposable copy of the FINISHED tree. "
        "Try to demonstrate this item is NOT delivered per your rules, and "
        "return the JSON object per your schema."])


def test_an_items_parts_ride_in_the_item_and_nothing_else_moves(leerie):
    """Bench SW's one input edit: the item's classifier parts, in order,
    inside THE ITEM TO CHECK; every other line is F+'s."""
    parts = ["the sum is returned", "every caller gets the sum"]
    got = leerie._falsifier_user_prompt(
        "Fix add.", {"item": "add returns the sum", "span": "task ¶2",
                     "parts": [*parts, 7, " "]},
        "abc123", "", [], [], [])
    assert got == _benched(
        "Fix add.", {"item": "add returns the sum", "source_ref": "task ¶2",
                     "parts": parts}, "abc123", "", [], [], [])


def test_user_prompt_is_the_frozen_one(leerie):
    item = {"item": "add returns the sum", "source_ref": "task ¶2"}
    parts = [{"requirement": "sum", "status": "unmet"}]
    for refs, ctx, prt, stat in (
            ([], [], [], ""),
            (["docs/spec.md"], [".leerie-falsifier/acceptance/set-1/a.test.ts"],
             parts, " src/add.ts | 2 +-\n")):
        got = leerie._falsifier_user_prompt(
            "Fix add.", {"item": item["item"], "span": item["source_ref"]},
            "abc123", stat, ctx, refs, prt)
        assert got == _benched("Fix add.", item, "abc123", stat, ctx, refs,
                               prt)
