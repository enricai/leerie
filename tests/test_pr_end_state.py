"""The PR's end-state section (DESIGN §6 *The PR's end-state section is
rendered by code, from typed state*; plan row G, complaint 2).

`_format_pr_end_state_section` replaced the generic "Residual risks" dump of
every worker's self-reported risk. These tests execute the renderer on a
state carrying every source and assert the rendered values and their order
— not key presence — plus the strip of worker-emitted copies.
"""
from __future__ import annotations

import pytest


def _full_state() -> dict:
    return {
        "outcome": "unconverged",
        "outcome_reasons": ["1 subtask(s) unconverged: feat-003",
                            "1 of the repository's own merge check(s) red "
                            "on the final tree: lint"],
        "subtask_status": {"feat-001": "complete", "feat-002": "complete",
                           "feat-003": "unconverged",
                           "feat-004": "accepted_external"},
        "unconverged_subtasks": {
            "feat-003": {"reason": "integration backed out: conflict in "
                                   "api.ts"}},
        "needs_human_verification": [
            {"source": "required_item", "item_index": 2,
             "item": "Works against the live tenant", "span": "S3",
             "source_ref": ""},
            {"source": "blocker", "subtask": "feat-004",
             "resource": "a staging database", "blocker": "needs a seed"},
        ],
        "risk_register": {
            "feat-001": [
                {"kind": "external_contract_assumption",
                 "detail": "relies on the provider's retry header",
                 "source": "implementer", "gap": "unverified_external",
                 "where_paths": ["src/client.ts"],
                 "resource": "a staging database", "addressed": False},
                {"kind": "unexercised_behavior", "detail": "two writers race",
                 "source": "implementer", "gap": "test_weak",
                 "where_paths": [], "simulated_interleaving": True,
                 "addressed": False},
            ],
            "feat-003": [
                {"kind": "integration_content_loss",
                 "detail": "the merge dropped the retry branch",
                 "where": "src/api.ts"},
                {"kind": "external_contract_assumption",
                 "detail": "never integrated, never rendered",
                 "source": "implementer", "gap": "unverified_external",
                 "where_paths": [], "addressed": False},
            ],
        },
        "site_claims": {"domain": "bug-fixing", "sites": [
            {"site_id": "D2", "file": "src/sync.ts", "symbol": "flush",
             "resolution": "re-run the flush after the lock",
             "status": "claimed", "surface_other": True},
            {"site_id": "D1", "file": "src/a.ts", "symbol": "f",
             "resolution": "x", "status": "claimed",
             "surface_other": False},
        ]},
        "coverage_redrive": {
            "feat-002": {"final": "untested",
                         "uncovered": {"src/parse.ts": [12, 14]},
                         "open_gaps": [{"gap": "test_missing",
                                        "detail": "no test for the parser",
                                        "where_paths": ["src/parse.ts"]}]},
            "feat-001": {"final": "covered", "uncovered": {}},
        },
        "ci_parity": {"final": {"unmet": [
            {"id": "lint", "kind": "lint", "why": "red",
             "argv": ["pnpm", "lint"], "cwd": "."}]}},
        "pr_review": {"findings": [
            {"verified": True, "path": "src/a.ts", "line_start": 3,
             "line_end": 5, "kind": "bug", "explanation": "off by one"},
            {"verified": False, "path": "src/b.ts", "line_start": 1,
             "line_end": 1, "kind": "bug", "explanation": "unverified"},
        ]},
        "contract_literal_checks": {"feat-001#0": {
            "owner_sid": "feat-001", "flag": True, "why": "near_variant",
            "extracted": {"counterpart_sid": "feat-002",
                          "literal": "x-retry-after"}}},
        "site_token_warnings": [{"token": "acme", "file": "src/c.ts",
                                 "added_line": "const host = 'acme';",
                                 "report_example": ""}],
    }


HEADINGS = ["## Unconverged", "## Needs human verification",
            "## Untested after re-drive", "## Integration content loss",
            "## Repository merge checks still red",
            "## Advisory (not verified by this run)"]


def test_every_source_renders_in_order(leerie):
    sec = leerie._format_pr_end_state_section(_full_state())
    pos = [sec.index(h + "\n") for h in HEADINGS]
    assert pos == sorted(pos), sec
    assert sec.startswith("\n## Unconverged\n\nThis PR is a draft")
    for line in (
        "- 1 subtask(s) unconverged: feat-003",
        "- **feat-003**: integration backed out: conflict in api.ts",
        "**Task requirements**\n- [ ] Works against the live tenant",
        "**a staging database**\n- [ ] **feat-004** was not done, waiting "
        "on it: needs a seed\n- [ ] **feat-001**: relies on the provider's "
        "retry header (`src/client.ts`)",
        "**A real concurrent engine**\n- [ ] **feat-001**: two writers race"
        " — tests simulate the interleaving",
        "**Fix sites resolved by an untyped change**\n- [ ] Check "
        "`src/sync.ts` `flush` (D2) is resolved: re-run the flush after "
        "the lock",
        "- **feat-002**: `src/parse.ts` lines 12, 14",
        "- **feat-002**: [test_missing] no test for the parser "
        "(`src/parse.ts`)",
        "- **feat-003**: the merge dropped the retry branch (`src/api.ts`)",
        "- `lint` (lint, red): `pnpm lint`",
        "- `src/a.ts:3-5` [bug] off by one",
        "- **feat-001** → **feat-002**: `x-retry-after` (near_variant)",
        "- `acme` in `src/c.ts`: const host = 'acme';",
    ):
        assert line in sec, (line, sec)
    # Work the PR does not contain, unverified findings, an `other`-free
    # site and a covered subtask stay out.
    for absent in ("never integrated, never rendered", "unverified",
                   "(D1)", "**feat-001**: `"):
        assert absent not in sec, (absent, sec)


def test_integration_loss_is_not_filed_as_a_worker_risk(leerie):
    """The retired dump put integration content loss under "Self-reported
    by workers"; now it has its own heading and nothing else lists it."""
    state = {"risk_register": {"feat-003": [
        {"kind": "integration_content_loss", "detail": "dropped hunk",
         "where": "src/api.ts"}]},
        "subtask_status": {"feat-003": "unconverged"}}
    sec = leerie._format_pr_end_state_section(state)
    assert sec == ("\n## Integration content loss\n\nWork a merge did not "
                   "carry into this branch.\n\n- **feat-003**: dropped hunk "
                   "(`src/api.ts`)")


def test_done_run_has_no_unconverged_section(leerie):
    state = _full_state()
    state["outcome"] = "done"
    state["subtask_status"]["feat-003"] = "complete"
    sec = leerie._format_pr_end_state_section(state)
    assert "## Unconverged" not in sec and "draft" not in sec


def test_advisory_lists_are_capped_and_counted(leerie):
    hits = [{"token": f"t{i}", "file": "f.ts", "added_line": "x"}
            for i in range(13)]
    sec = leerie._format_pr_end_state_section(
        {"site_token_warnings": hits})
    assert sec.count("- `t") == leerie._PR_ADVISORY_SHOWN
    assert "- … 3 more in state.json `site_token_warnings`" in sec


def test_entry_text_cannot_open_a_heading(leerie):
    state = {"outcome": "unconverged",
             "outcome_reasons": ["a\n## Injected\nb"],
             "pr_review": {"findings": [{"verified": True, "path": "a`b",
                                         "line_start": 1, "line_end": 2,
                                         "kind": "bug",
                                         "explanation": "x\n## Also"}]}}
    sec = leerie._format_pr_end_state_section(state)
    assert "\n## Injected" not in sec and "\n## Also" not in sec
    assert "- a ## Injected b" in sec
    assert "`a'b:1-2`" in sec


@pytest.mark.parametrize("bad", [None, [], "x", {"risk_register": 3,
                                                  "coverage_redrive": [],
                                                  "site_claims": "x",
                                                  "pr_review": [],
                                                  "ci_parity": {"final": 1},
                                                  "outcome_reasons": "x",
                                                  "needs_human_verification":
                                                  [1, None]}])
def test_malformed_state_degrades_to_nothing(leerie, bad):
    assert leerie._format_pr_end_state_section(bad) == ""


@pytest.mark.parametrize("heading", HEADINGS + ["## ⚠ Residual risks"])
def test_worker_copy_of_every_code_owned_heading_is_stripped(leerie,
                                                             heading):
    body = (f"## Summary\n\nwork\n\n{heading}\n\n- worker-invented\n\n"
            "## Deploy order\n\nmerge first")
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert stripped
    assert out == "## Summary\n\nwork\n\n## Deploy order\n\nmerge first"


def test_strip_is_whole_line_anchored(leerie):
    """`## Unconverged` must not eat a worker's own `## Unconverged
    migrations` section: the heading matches only as a whole line."""
    body = "## Summary\n\nx\n\n## Unconverged migrations\n\nkept"
    out, stripped = leerie._strip_worker_risk_sections(body)
    assert not stripped and out == body
