"""Prompt-content pin for the re-drive fixer's `breaks_existing_test` behaviour
(DESIGN §8 *Delivery re-drive* rule 4).

Motivation (measured, run-2 case b738): a required item's falsifier probe
conflicted with an existing test; the fixer REVERTED its production change and
emitted a `breaks_existing_test` objection instead of committing the passing
change. With no committed change (`post_sha == pre_sha`) the measure->judge block
never ran, so the contradiction judge never adjudicated and the item died on
budget. The judge/base-form net is already regression-safe once a commit makes
the probe pass; the only hole was the fixer withholding the commit.

The fix forces the fixer to COMMIT the passing change for a `breaks_existing_test`
and let leerie adjudicate (change the test if the task requires it, else roll
back), while leaving the "probe is itself wrong" kinds (`input_outside_domain` /
`contradicts_task`) free to decline. It lives in two mirrored places: the
standing instruction in `prompts/implementer.md` and the per-round PROBES note
`_redrive_note` renders. Substance check: assert the load-bearing clauses, not
key presence.
"""
from __future__ import annotations


def _objections_para(text: str) -> str:
    """The `probe_objections` paragraph of implementer.md, whitespace-normalised
    (so a reflow does not break the match) and sliced from its opening to its
    own last phrase, so a clause elsewhere in the (include-expanded) prompt
    cannot satisfy the check."""
    t = " ".join(text.split())
    start = t.index("`probe_objections` is optional")
    end = t.index("conflict recorded for a person", start) + len(
        "conflict recorded for a person")
    return t[start:end]


def test_breaks_existing_test_forces_commit_not_revert(leerie):
    p = _objections_para(leerie._load_prompt("implementer"))
    # A breaks_existing_test is not a reason to stop: make the change and commit.
    assert "that is not a reason to stop" in p
    assert "make the production change that passes the probe and commit it" in p
    assert "do not revert to keep the old test green" in p
    assert "commit the passing change regardless" in p
    # The change/keep decision is leerie's (it may change the test), not the
    # fixer's; the fixer never edits the existing test itself.
    assert "not yours to decide" in p
    assert "never edit or weaken an existing test" in p


def test_wrong_probe_kinds_may_still_decline(leerie):
    p = _objections_para(leerie._load_prompt("implementer"))
    # input_outside_domain / contradicts_task: the probe is itself wrong, so the
    # fixer must NOT force it — revert-and-object stays legitimate (and required)
    # for these, else the item is marked met on a meaningless test.
    assert "a probe you believe is itself wrong" in p
    assert "you must NOT force it" in p
    assert "revert your change and object" in p
    assert "mark the item met on a meaningless test" in p
    assert "input_outside_domain" in p
    assert "contradicts_task" in p


def test_objection_stays_report_only(leerie):
    p = _objections_para(leerie._load_prompt("implementer"))
    assert "exempts nothing" in p


def test_probes_note_mirrors_the_prompt(leerie):
    """The per-round PROBES note `_redrive_note` renders to the fixer must carry
    the same rule, or the standing prompt and the note the fixer actually reads
    disagree."""
    note = leerie._redrive_note(
        [{"kind": "unmet_item", "item": "an item",
          "probe": {"file": "probe.test.ts", "case_name": "c",
                    "assertion_quote": "a", "reason": "r"}}],
        rnd=1, rounds=2, merged=[], held_names=[], defect_shape="",
        selfcheck=None)
    n = " ".join(note.split())
    assert "that is not a reason to withhold the change: commit it anyway" in n
    assert "Do not revert to keep the old test green" in n
    assert "optional commentary and never a reason to revert" in n
