"""DESIGN §9 *Self-reported risk is routed, not read* — the risk-routing
helpers:

- `_typed_risks` / `_collect_subtask_risks` — the pure routing from worker
  structured output to `risk_register` entries, including the positional
  `risk_dispositions` answers and the verifiable-act co-occurrence checks.
- `_format_self_reported_risks` — the conformer-facing challenge block
  (whose item order IS the disposition-index contract).
- `_format_risk_register_section` — the PR-body renderer appended by code
  on every composition path.

The incident these pin: an implementer disclosed, in typed fields, that its
new path was unexercised and keyed on an external API's message wording —
`check_production_evidence` passed it silently (exercised:false WITH a
reason is a clean pass by design) and nothing downstream consumed the
disclosure. These tests assert the VALUE flows (the reason text, the detail
text), not key presence — a key can ship with its content discarded
(CLAUDE.md: structure must be paired with substance).
"""
from __future__ import annotations

import pytest


def _impl(reason="needs a live billing API", risks=None):
    """An implementer result in the exact shape that passes
    check_production_evidence silently today: exercised:false WITH a
    non-empty unexercisable_reason."""
    res = {
        "subtask_id": "feat-x",
        "status": "complete",
        "production_evidence": {"exercised": False,
                                "unexercisable_reason": reason},
    }
    if risks is not None:
        res["self_reported_risks"] = risks
    return res


_CONTRACT_RISK = {"kind": "external_contract_assumption",
                  "detail": "matches the provider's error-message wording",
                  "where": "app/services/x.rb"}


# --- the incident pin ------------------------------------------------------

def test_silently_passing_evidence_still_lands_in_the_register(leerie):
    """The exact shape that passes check_production_evidence with zero
    issues must STILL produce a risk entry — the whole point of the
    change. Assert the reason text as the value, not the key."""
    res = _impl(reason="requires a live provider API and DB")
    assert leerie.check_production_evidence(res) == []  # silent pass today
    out = leerie._collect_subtask_risks(res, None)
    assert len(out) == 1
    assert out[0]["kind"] == "unexercised_behavior"
    assert out[0]["detail"] == "requires a live provider API and DB"
    assert out[0]["source"] == "implementer"
    assert out[0]["addressed"] is False


def test_typed_risks_union_with_tier_two(leerie):
    out = leerie._collect_subtask_risks(_impl(risks=[_CONTRACT_RISK]), None)
    assert [e["kind"] for e in out] == ["unexercised_behavior",
                                       "external_contract_assumption"]
    assert out[1]["detail"] == "matches the provider's error-message wording"
    assert out[1]["where"] == "app/services/x.rb"


def test_malformed_tier_two_entries_are_dropped_not_guessed(leerie):
    res = _impl(risks=[
        {"kind": "not_a_kind", "detail": "x"},     # unknown kind
        {"kind": "untested_change", "detail": ""},  # empty detail
        "just a string",                            # not a dict
        {"kind": "untested_change", "detail": "no tests exercise the branch"},
    ])
    out = leerie._collect_subtask_risks(res, None)
    details = [e["detail"] for e in out if e["kind"] == "untested_change"]
    assert details == ["no tests exercise the branch"]


# --- dispositions: the fail-safe union -------------------------------------

def test_conformer_silence_does_not_clear_a_risk(leerie):
    """A conformer that ran, reported cleanly, and said nothing about the
    risks leaves every entry unaddressed — inputs disagree on purpose: the
    conformer result is present and empty-handed, which must be
    indistinguishable from no answer."""
    conf = {"subtask_id": "feat-x", "solution_defects": [],
            "file_updates": [], "risk_dispositions": []}
    out = leerie._collect_subtask_risks(_impl(risks=[_CONTRACT_RISK]), conf)
    assert all(e["addressed"] is False for e in out)
    assert all("disposition" not in e for e in out)


def test_accepted_is_recorded_but_never_clears(leerie):
    conf = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": ["accepted", "accepted"]}
    out = leerie._collect_subtask_risks(_impl(risks=[_CONTRACT_RISK]), conf)
    assert [e.get("disposition") for e in out] == ["accepted", "accepted"]
    assert all(e["addressed"] is False for e in out)


def test_confirmed_defect_never_reads_as_addressed(leerie):
    """'confirmed_defect' records escalation into the gating channel and
    NEVER sets addressed — even with an actionable entry filed. A
    confirmed entry surviving to a finalized PR is structurally a
    gate-didn't-deliver case (skip-flag or accept-blocked), so an
    "addressed" annotation there would be anti-correlated with reality
    (a pre-merge review finding). The anchoring measurement behind the disposition
    channel itself: disclosure without a typed answer channel suppressed
    detection 0/3 vs 2/3 (DESIGN §9 *Self-reported risk is routed, not
    read*)."""
    bare = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": ["confirmed_defect"]}
    out = leerie._collect_subtask_risks(_impl(), bare)
    assert out[0]["disposition"] == "confirmed_defect"
    assert out[0]["addressed"] is False

    with_act = {"solution_defects": [
        {"kind": "external_contract_assumption", "concrete_case": "c",
         "where": "w", "why_ships_a_defect": "y"}],
        "file_updates": [], "risk_dispositions": ["confirmed_defect"]}
    out = leerie._collect_subtask_risks(_impl(), with_act)
    assert out[0]["disposition"] == "confirmed_defect"
    assert out[0]["addressed"] is False


def test_mitigated_requires_a_recorded_repair(leerie):
    bare = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": ["mitigated"]}
    assert leerie._collect_subtask_risks(_impl(), bare)[0]["addressed"] is False

    with_act = {"solution_defects": [],
                "file_updates": [{"kind": "tests", "path": "t.rb",
                                  "reason": "covers the new branch"}],
                "risk_dispositions": ["mitigated"]}
    assert leerie._collect_subtask_risks(_impl(), with_act)[0]["addressed"] is True


def test_unrecognised_disposition_degrades_to_unanswered(leerie):
    """The wire schema deliberately carries no enum (grammar budget); the
    value set is enforced here, and a typo must fail SAFE."""
    conf = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": ["confirmed"]}  # not a recognised value
    out = leerie._collect_subtask_risks(_impl(), conf)
    assert "disposition" not in out[0]
    assert out[0]["addressed"] is False


def test_short_disposition_array_leaves_the_tail_unanswered(leerie):
    conf = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": ["accepted"]}  # answers risk 0 only
    out = leerie._collect_subtask_risks(_impl(risks=[_CONTRACT_RISK]), conf)
    assert out[0].get("disposition") == "accepted"
    assert "disposition" not in out[1]


def test_positional_mapping_matches_challenge_block_order(leerie):
    """Entry i of risk_dispositions must answer the i-th item of the
    challenge block — inputs disagree (different kinds, different
    dispositions) so a swapped mapping cannot pass."""
    res = _impl(risks=[_CONTRACT_RISK])  # risk 0 = unexercised, 1 = contract
    block = leerie._format_self_reported_risks(res)
    assert block.index("unexercised_behavior") < \
        block.index("external_contract_assumption")
    conf = {"solution_defects": [], "file_updates": [
        {"kind": "tests", "path": "t.rb", "reason": "r"}],
        "risk_dispositions": ["mitigated", "accepted"]}
    out = leerie._collect_subtask_risks(res, conf)
    assert out[0]["kind"] == "unexercised_behavior"
    assert out[0]["disposition"] == "mitigated" and out[0]["addressed"]
    assert out[1]["kind"] == "external_contract_assumption"
    assert out[1]["disposition"] == "accepted" and not out[1]["addressed"]


def test_conformer_own_unexercised_evidence_joins_unchallenged(leerie):
    """The conformer's own exercised:false rides the register too, but is
    never challenged and so never addressed."""
    conf = {"production_evidence": {"exercised": False,
                                    "unexercisable_reason": "needs live API"},
            "solution_defects": [], "file_updates": [],
            "risk_dispositions": []}
    out = leerie._collect_subtask_risks(None, conf)
    assert out == [{"kind": "unexercised_behavior",
                    "detail": "needs live API", "source": "conformer",
                    "addressed": False}]


def test_no_risks_means_empty_register_entry(leerie):
    res = {"subtask_id": "s", "status": "complete",
           "production_evidence": {"exercised": True, "how": "ran it",
                                   "observed": "worked"}}
    assert leerie._collect_subtask_risks(res, None) == []
    assert leerie._format_self_reported_risks(res) is None


# --- the challenge block ---------------------------------------------------

def test_challenge_block_carries_each_detail_verbatim(leerie):
    res = _impl(reason="requires a live provider API",
                risks=[_CONTRACT_RISK])
    block = leerie._format_self_reported_risks(res)
    assert "requires a live provider API" in block
    assert "matches the provider's error-message wording" in block
    assert "risk_dispositions" in block  # names the answer channel
    for value in ("confirmed_defect", "mitigated", "accepted"):
        assert value in block  # schema has no enum; the block documents them


# --- the PR section --------------------------------------------------------

def test_pr_section_renders_details_verbatim_with_status(leerie):
    state = {"risk_register": {"feat-x": [
        {"kind": "external_contract_assumption",
         "detail": "matches the provider's error-message wording",
         "where": "app/services/x.rb", "source": "implementer",
         "disposition": "accepted", "addressed": False},
        {"kind": "unexercised_behavior", "detail": "needs live API",
         "source": "implementer", "disposition": "mitigated",
         "addressed": True},
        {"kind": "untested_change", "detail": "escalated to the gate",
         "source": "implementer", "disposition": "confirmed_defect",
         "addressed": False},
    ]}}
    sec = leerie._format_risk_register_section(state)
    assert "## ⚠ Residual risks" in sec
    assert ("- **feat-x** [external_contract_assumption] matches the "
            "provider's error-message wording (app/services/x.rb) — "
            "accepted, not cleared") in sec
    assert ("- **feat-x** [unexercised_behavior] needs live API — "
            "addressed (mitigated)") in sec
    assert ("- **feat-x** [untested_change] escalated to the gate — "
            "confirmed_defect, not cleared") in sec


def test_pr_section_empty_register_renders_nothing(leerie):
    assert leerie._format_risk_register_section({}) == ""
    assert leerie._format_risk_register_section({"risk_register": {}}) == ""
    assert leerie._format_risk_register_section(
        {"risk_register": {"s": []}}) == ""


def test_pr_section_skips_still_blocked_subtasks(leerie):
    """A sid still `blocked` at finalize was excluded from integration —
    its risks describe code the PR does not contain. An accept-blocked'ed
    sid is likewise skipped, via the `accepted_blocked` registry (its
    status reads `complete` but its branch is never merged — see
    test_pr_section_skips_accept_blocked_sids)."""
    entry = [{"kind": "untested_change", "detail": "orphan risk",
              "source": "implementer", "addressed": False}]
    state = {"risk_register": {"s1": entry, "s2": entry},
             "subtask_status": {"s1": "blocked", "s2": "complete"}}
    sec = leerie._format_risk_register_section(state)
    assert "**s2**" in sec
    assert "**s1**" not in sec
    only_blocked = {"risk_register": {"s1": entry},
                    "subtask_status": {"s1": "blocked"}}
    assert leerie._format_risk_register_section(only_blocked) == ""


# --- schema substance ------------------------------------------------------

def test_implementer_schema_rejects_an_unknown_risk_kind(leerie):
    """The enum is real, not decorative: a payload carrying an unknown kind
    must fail validation (the planner-invented-vocabulary case)."""
    jsonschema = pytest.importorskip("jsonschema")
    good = {"subtask_id": "s", "status": "complete",
            "self_reported_risks": [
                {"kind": "untested_change", "detail": "d"}]}
    jsonschema.validate(good, leerie.SCHEMAS["implementer"])
    bad = {"subtask_id": "s", "status": "complete",
           "self_reported_risks": [{"kind": "spooky_vibes", "detail": "d"}]}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(bad, leerie.SCHEMAS["implementer"])


def test_conformer_schema_accepts_positional_dispositions(leerie):
    jsonschema = pytest.importorskip("jsonschema")
    payload = {
        "subtask_id": "s", "rules_files_read": [], "rule_violations": [],
        "file_updates": [], "summary": "ok", "solution_defects": [],
        "build": {"ran": False, "passed": False, "command": ""},
        "lint": {"ran": False, "passed": False, "command": ""},
        "tests": {"ran": False, "passed": False, "command": ""},
        "risk_dispositions": ["accepted", "mitigated"],
    }
    jsonschema.validate(payload, leerie.SCHEMAS["conformer"])


# --- the production call site (settle path), executed ----------------------
# The PR #271 round-1 class: every helper test above stays green if the
# `impl_res=res` threading or the register write is deleted. This drives
# the REAL `_settle_subtask` success path (harness mirrors
# tests/test_oom_naming.py's env) and asserts the VALUES land in state.

import asyncio
import json as _json


from tests.conftest import run_git_cwd_kw


@pytest.fixture
def settle_env(leerie, tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git_cwd_kw("init", "-q", "-b", "main", cwd=repo)
    run_git_cwd_kw("config", "user.email", "t@t", cwd=repo)
    run_git_cwd_kw("config", "user.name", "t", cwd=repo)
    (repo / "README.md").write_text("# repo\n")
    run_git_cwd_kw("add", "-A", cwd=repo)
    run_git_cwd_kw("commit", "-q", "-m", "initial", cwd=repo)

    run_id = "risk-001-abcdef"
    run_branch = f"leerie/runs/{run_id}"
    run_git_cwd_kw("checkout", "-q", "-b", run_branch, cwd=repo)

    sid = "t1"
    leerie_root = repo / ".leerie"
    run_dir = leerie_root / "runs" / run_id
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True, exist_ok=True)
    worktree = run_dir / "worktrees" / sid
    run_git_cwd_kw("worktree", "add", "-q", "-b",
          f"leerie/subtasks/{run_id}/{sid}", str(worktree), run_branch, cwd=repo)
    # The implementer's committed work, so check_branch_has_commits passes.
    (worktree / "src.py").write_text("def f():\n    return 1\n")
    run_git_cwd_kw("add", "-A", cwd=worktree)
    run_git_cwd_kw("commit", "-q", "-m", "implementer: add f()", cwd=worktree)

    (run_dir / "criteria" / f"{sid}.md").write_text("# Criteria\n- f()\n")
    subtask = {"id": sid, "files_likely_touched": ["src.py"]}
    (run_dir / "subtasks" / f"{sid}.json").write_text(_json.dumps(subtask))

    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()
    return {"leerie": leerie, "sid": sid, "run_dir": run_dir, "st": st,
            "caps": dict(leerie.DEFAULT_CAPS),
            "models": {w: "sonnet" for w in leerie.WORKER_TYPES},
            "efforts": {}}


def _complete_impl_result(sid, risks=True):
    res = {
        "subtask_id": sid,
        "status": "complete",
        "summary": "did the work",
        "confidence": {"root_cause": 9.5, "solution": 9.5,
                       "basis": "ran it", "falsifiers_tested": ["x"],
                       "contradictions_reconciled": []},
        "production_evidence":
            ({"exercised": False,
              "unexercisable_reason": "needs a live provider API"}
             if risks else
             {"exercised": True, "how": "ran it", "observed": "ok"}),
    }
    if risks:
        res["self_reported_risks"] = [
            {"kind": "untested_change",
             "detail": "no test exercises the new branch"}]
    return res


def test_settle_success_path_populates_and_pops_the_register(
        settle_env, monkeypatch):
    c = settle_env["leerie"]
    sid = settle_env["sid"]
    seen = {}

    async def _impl_stub(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        return _complete_impl_result(sid_, risks=_impl_stub.risky)
    _impl_stub.risky = True
    monkeypatch.setattr(c, "_run_implementer", _impl_stub)

    async def _conf_stub(sid_, leerie_dir, worktree, subtask, caps, st,
                         models, efforts, impl_res=None):
        seen["impl_res"] = impl_res
        return ({"subtask_id": sid_, "solution_defects": [],
                 "file_updates": [],
                 "risk_dispositions": ["accepted"]}, [], None)
    monkeypatch.setattr(c, "_run_conformance_phase", _conf_stub)

    res = asyncio.run(c._settle_subtask(
        sid, settle_env["run_dir"], settle_env["caps"], settle_env["st"],
        settle_env["models"], settle_env["efforts"]))
    assert res["status"] == "complete", res

    # The threading: the REAL settle passed the implementer result through.
    assert seen["impl_res"]["self_reported_risks"][0]["detail"] == \
        "no test exercises the new branch"

    # The register: persisted through State with the VALUES, not just keys.
    reg = settle_env["st"].data["risk_register"][sid]
    details = {e["detail"] for e in reg}
    assert "needs a live provider API" in details
    assert "no test exercises the new branch" in details
    # And the positional disposition applied to entry 0 (tier-1).
    assert reg[0]["disposition"] == "accepted"
    assert all(e["addressed"] is False for e in reg)

    # Re-settle with a clean result: the stale entry must POP (mirrors
    # symptom_findings — a later clean attempt must not keep reporting).
    _impl_stub.risky = False
    res2 = asyncio.run(c._settle_subtask(
        sid, settle_env["run_dir"], settle_env["caps"], settle_env["st"],
        settle_env["models"], settle_env["efforts"]))
    assert res2["status"] == "complete", res2
    assert sid not in settle_env["st"].data.get("risk_register", {})


# --- pre-merge adversarial-review pins (fix-round regressions) -------------

def test_docs_only_update_does_not_clear_mitigated(leerie):
    """One unrelated docs touch must not clear a risk — a docs update
    is not a mitigation; a fixed defect or a tests update is."""
    docs_only = {"solution_defects": [],
                 "file_updates": [{"kind": "docs", "path": "README.md",
                                   "reason": "typo"}],
                 "risk_dispositions": ["mitigated"]}
    assert leerie._collect_subtask_risks(
        _impl(), docs_only)[0]["addressed"] is False

    fixed_defect = {"solution_defects": [
        {"kind": "missing_guard", "concrete_case": "c", "where": "w",
         "why_ships_a_defect": "y", "fixed": True}],
        "file_updates": [], "risk_dispositions": ["mitigated"]}
    assert leerie._collect_subtask_risks(
        _impl(), fixed_defect)[0]["addressed"] is True


def test_fixed_or_nonactionable_defect_does_not_clear_confirmed(leerie):
    """No solution_defects shape of any kind affects a 'confirmed_defect'
    answer — it never reads as addressed (see
    test_confirmed_defect_never_reads_as_addressed for the design
    rationale); these two shapes (fixed:true, case-less) are the inputs
    that gamed an earlier draft where an entry's mere presence cleared
    the confirmation, kept as disagreeing inputs."""
    fixed_only = {"solution_defects": [
        {"kind": "missing_guard", "concrete_case": "c", "where": "w",
         "why_ships_a_defect": "y", "fixed": True}],
        "file_updates": [], "risk_dispositions": ["confirmed_defect"]}
    assert leerie._collect_subtask_risks(
        _impl(), fixed_only)[0]["addressed"] is False

    non_actionable = {"solution_defects": [
        {"kind": "missing_guard", "concrete_case": "", "where": "w",
         "why_ships_a_defect": "y"}],
        "file_updates": [], "risk_dispositions": ["confirmed_defect"]}
    assert leerie._collect_subtask_risks(
        _impl(), non_actionable)[0]["addressed"] is False


def test_dict_disposition_degrades_instead_of_raising(leerie):
    """A disposition item in the superseded object shape must not TypeError the settle
    success path — it degrades to unanswered."""
    conf = {"solution_defects": [], "file_updates": [],
            "risk_dispositions": [{"disposition": "accepted"}, ["x"]]}
    out = leerie._collect_subtask_risks(_impl(risks=[_CONTRACT_RISK]), conf)
    assert all("disposition" not in e for e in out)
    assert all(e["addressed"] is False for e in out)


def test_nine_item_challenge_is_fully_answerable(leerie):
    """Tier-1 + 8 tier-2 risks = 9 challenge items; the dispositions
    schema must admit 9 answers so the ninth risk is clearable."""
    jsonschema = pytest.importorskip("jsonschema")
    risks = [{"kind": "untested_change", "detail": f"risk {i}"}
             for i in range(8)]
    res = _impl(risks=risks)
    block = leerie._format_self_reported_risks(res)
    assert "  8. " in block  # 9 items, 0-indexed
    payload = {
        "subtask_id": "s", "rules_files_read": [], "rule_violations": [],
        "file_updates": [], "summary": "ok", "solution_defects": [],
        "build": {"ran": False, "passed": False, "command": ""},
        "lint": {"ran": False, "passed": False, "command": ""},
        "tests": {"ran": False, "passed": False, "command": ""},
        "risk_dispositions": ["accepted"] * 9,
    }
    jsonschema.validate(payload, leerie.SCHEMAS["conformer"])
    out = leerie._collect_subtask_risks(res, payload)
    assert out[8].get("disposition") == "accepted"


def test_long_and_multiline_details_are_normalized_not_rejected(leerie):
    """Honesty must not cost a retry — a 301-char detail VALIDATES
    (no schema maxLength) and lands trimmed; newlines collapse so a
    disclosure cannot open a markdown heading in the PR section."""
    jsonschema = pytest.importorskip("jsonschema")
    long_detail = "x" * 301
    payload = {"subtask_id": "s", "status": "complete",
               "self_reported_risks": [
                   {"kind": "untested_change", "detail": long_detail}]}
    jsonschema.validate(payload, leerie.SCHEMAS["implementer"])
    out = leerie._collect_subtask_risks(payload, None)
    assert len(out[0]["detail"]) == 300

    sneaky = _impl(reason="line one\n## Injected heading\nline two")
    out = leerie._collect_subtask_risks(sneaky, None)
    assert "\n" not in out[0]["detail"]
    sec = leerie._format_risk_register_section(
        {"risk_register": {"s": out}})
    assert "\n## Injected heading" not in sec
    assert "line one ## Injected heading line two" in sec


def test_malformed_register_degrades_to_no_section(leerie):
    """A garbage register must render nothing, never raise — a raise
    inside _compose_pr_via_llm's fail-open discards the accepted body."""
    assert leerie._format_risk_register_section(
        {"risk_register": ["not", "a", "dict"]}) == ""
    assert leerie._format_risk_register_section(
        {"risk_register": {"s": "not-a-list"}}) == ""


# --- round-3 pins: rollback-neutralized repairs, accepted_blocked skip, ----
# statuses guard, confirmed-without-filing advisory ------------------------

def test_rolled_back_repair_never_counts_as_mitigation(leerie):
    """A conformer result whose round the phase rolled back carries
    repair records for commits that no longer exist — measured, they
    rendered 'addressed (mitigated)' for a repair absent from the PR.
    The orchestrator-set flag neutralizes the act."""
    conf = {"solution_defects": [
        {"kind": "missing_guard", "concrete_case": "c", "where": "w",
         "why_ships_a_defect": "y", "fixed": True}],
        "file_updates": [{"kind": "tests", "path": "t.py", "reason": "r"}],
        "risk_dispositions": ["mitigated"],
        "conformer_repair_rolled_back": True}
    out = leerie._collect_subtask_risks(_impl(), conf)
    assert out[0]["disposition"] == "mitigated"
    assert out[0]["addressed"] is False  # both acts present, both reverted


def test_pr_section_skips_accept_blocked_sids(leerie):
    """An accept-blocked'ed sid's status is rewritten to `complete` by
    the launcher mutator but its branch is NEVER merged (the verified
    chain at the completeness gate) — so a status filter alone misses
    it; the accepted_blocked registry is the discriminator."""
    entry = [{"kind": "untested_change", "detail": "orphan risk",
              "source": "implementer", "addressed": False}]
    state = {"risk_register": {"s1": entry, "s2": entry},
             "subtask_status": {"s1": "complete", "s2": "complete"},
             "accepted_blocked": {"s1": {"at": "2026-10-01"}}}
    sec = leerie._format_risk_register_section(state)
    assert "**s2**" in sec
    assert "**s1**" not in sec


def test_malformed_statuses_degrade_instead_of_raising(leerie):
    """subtask_status/accepted_blocked of the wrong type must degrade to
    'no filter', never raise — a raise here is swallowed fail-open and
    discards the accepted pr_writer body."""
    entry = [{"kind": "untested_change", "detail": "d",
              "source": "implementer", "addressed": False}]
    state = {"risk_register": {"s1": entry},
             "subtask_status": ["s1"], "accepted_blocked": "nope"}
    sec = leerie._format_risk_register_section(state)
    assert "**s1**" in sec  # rendered; filters degraded, no crash


def test_confirmed_without_filing_draws_settle_warning(
        settle_env, monkeypatch):
    """§12: the prompt rule ('also file it') gets a code check — a
    confirmed_defect answer with no actionable filing is the third way
    a confirmed entry survives, and it must be named, not silent."""
    c = settle_env["leerie"]
    sid = settle_env["sid"]

    async def _impl_stub(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        return _complete_impl_result(sid_, risks=True)
    monkeypatch.setattr(c, "_run_implementer", _impl_stub)

    async def _conf_stub(sid_, leerie_dir, worktree, subtask, caps, st,
                         models, efforts, impl_res=None):
        return ({"subtask_id": sid_, "solution_defects": [],
                 "file_updates": [],
                 "risk_dispositions": ["confirmed_defect",
                                       "confirmed_defect"]}, [], None)
    monkeypatch.setattr(c, "_run_conformance_phase", _conf_stub)

    res = asyncio.run(c._settle_subtask(
        sid, settle_env["run_dir"], settle_env["caps"], settle_env["st"],
        settle_env["models"], settle_env["efforts"]))
    assert res["status"] == "complete", res
    warnings = res.get("conformance_warnings") or []
    assert any("confirmed_defect" in w and "no actionable" in w
               for w in warnings), warnings
    reg = settle_env["st"].data["risk_register"][sid]
    assert all(e["addressed"] is False for e in reg)


def test_nonlist_dispositions_neither_raise_nor_warn_at_settle(
        settle_env, monkeypatch):
    """The advisory predicate sits on the settle success path outside
    the advisory try/except: a non-list risk_dispositions (int raises
    on `in`; a string substring-matches) must degrade silently."""
    c = settle_env["leerie"]
    sid = settle_env["sid"]

    async def _impl_stub(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        return _complete_impl_result(sid_, risks=True)
    monkeypatch.setattr(c, "_run_implementer", _impl_stub)

    async def _conf_stub(sid_, leerie_dir, worktree, subtask, caps, st,
                         models, efforts, impl_res=None):
        return ({"subtask_id": sid_, "solution_defects": [],
                 "file_updates": [],
                 "risk_dispositions": 7}, [], None)
    monkeypatch.setattr(c, "_run_conformance_phase", _conf_stub)

    res = asyncio.run(c._settle_subtask(
        sid, settle_env["run_dir"], settle_env["caps"], settle_env["st"],
        settle_env["models"], settle_env["efforts"]))
    assert res["status"] == "complete", res
    assert not any("confirmed_defect" in w
                   for w in res.get("conformance_warnings") or [])


def test_no_disclosed_risks_means_no_confirmed_warning(
        settle_env, monkeypatch):
    """A schema-valid but pointless 'confirmed_defect' answering NO
    disclosed risk must not generate advisory noise."""
    c = settle_env["leerie"]
    sid = settle_env["sid"]

    async def _impl_stub(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        return _complete_impl_result(sid_, risks=False)
    monkeypatch.setattr(c, "_run_implementer", _impl_stub)

    async def _conf_stub(sid_, leerie_dir, worktree, subtask, caps, st,
                         models, efforts, impl_res=None):
        return ({"subtask_id": sid_, "solution_defects": [],
                 "file_updates": [],
                 "risk_dispositions": ["confirmed_defect"]}, [], None)
    monkeypatch.setattr(c, "_run_conformance_phase", _conf_stub)

    res = asyncio.run(c._settle_subtask(
        sid, settle_env["run_dir"], settle_env["caps"], settle_env["st"],
        settle_env["models"], settle_env["efforts"]))
    assert res["status"] == "complete", res
    assert not any("confirmed_defect" in w
                   for w in res.get("conformance_warnings") or [])
