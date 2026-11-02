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


def test_system_prompt_is_the_frozen_one_but_for_the_leads_dir(leerie):
    shipped = (ROOT / "prompts" / "falsifier.md").read_text()
    lead = leerie.FALSIFIER_LEADS_DIR + "/"
    assert shipped.count(lead) == 2         # the only listed edit
    frozen = shipped.replace(lead, ".q2ctx/")
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
