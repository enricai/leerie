"""Tests for the finding-level delivery gate's non-judge halves
(DESIGN §8 *The gate judges the finding, not only the items* and
*Execution-shaped items are judged from the run's own records*):
`_executed_commands_digest`, `_prior_delivery_residual`, and the
cross-run `prior_delivery_residual` planner-ctx injection.

The judge-side contract flow (schema, escalation, majority, routing,
recheck) lives in tests/test_delivery_gate.py with that file's
existing harness. Coverage discipline as everywhere: execute the real
consumer, assert the VALUE delivered.
"""
from __future__ import annotations

import asyncio
import json

MODELS = {"planner": "sonnet", "fit_judge": "sonnet", "splitter": "sonnet"}
EFFORTS = {"planner": "medium", "fit_judge": "medium", "splitter": "medium"}

TASK = "fix the reported defect exactly as the report demands"


# --- synthetic per-worker JSONL logs (the SDK shape _iter_log_tool_use
# --- parses: message.content blocks, tool_result keyed by tool_use_id)

def _log_line_use(uid, cmd):
    return json.dumps({"message": {"content": [
        {"type": "tool_use", "id": uid, "name": "Bash",
         "input": {"command": cmd}}]}})


def _log_line_result(uid, text):
    return json.dumps({"message": {"content": [
        {"type": "tool_result", "tool_use_id": uid, "content": text}]}})


def _write_log(logs_dir, sid, pairs):
    logs_dir.mkdir(parents=True, exist_ok=True)
    lines = []
    for i, (cmd, result) in enumerate(pairs):
        uid = f"{sid}-u{i}"
        lines.append(_log_line_use(uid, cmd))
        if result is not None:
            lines.append(_log_line_result(uid, result))
    (logs_dir / f"{sid}.log").write_text("\n".join(lines) + "\n")


# === _executed_commands_digest =============================================

def test_digest_extracts_blt_commands_with_result_tails(
        leerie, tmp_path, monkeypatch):
    """Substance: the digest carries the BLT command AND its verbatim
    result tail; a non-BLT command is excluded."""
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    _write_log(tmp_path / "logs", "bugfix-001", [
        ("pnpm test src/x.test.ts", "Test Files  1 passed (1)"),
        ("git status --short", "M src/x.ts"),
    ])
    d = leerie._executed_commands_digest(tmp_path, tmp_path)
    assert "[bugfix-001] $ pnpm test src/x.test.ts" in d
    assert "Test Files  1 passed (1)" in d
    assert "git status" not in d


def test_digest_no_result_and_missing_logs(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    _write_log(tmp_path / "logs", "s1", [("pnpm typecheck", None)])
    d = leerie._executed_commands_digest(tmp_path, tmp_path)
    assert "(no result recorded)" in d
    assert leerie._executed_commands_digest(
        tmp_path / "absent", tmp_path) == ""


def test_digest_empty_without_blt_verbs(leerie, tmp_path, monkeypatch):
    """No declared BLT commands → no digest (the filter is the verbs;
    without them everything or nothing would be arbitrary)."""
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: [])
    _write_log(tmp_path / "logs", "s1", [("pnpm test", "ok")])
    assert leerie._executed_commands_digest(tmp_path, tmp_path) == ""


def test_digest_entry_cap(leerie, tmp_path, monkeypatch):
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    _write_log(tmp_path / "logs", "s1", [
        (f"pnpm test f{i}.ts", "ok") for i in range(5)])
    d = leerie._executed_commands_digest(tmp_path, tmp_path,
                                         max_entries=2)
    assert d.count("$ pnpm test") == 2


# === _prior_delivery_residual ==============================================

def _run_state(leerie, runs_root, run_id, task, gate=None,
               mtime=None, exit_code="0", extra=None):
    """exit_code: "0" = completed; "1" = died through die() (which
    ALSO writes finished_at — the discovery sentinel that must not be
    mistaken for completion); None = in-flight/killed (no file).
    `extra` merges additional top-level state keys (e.g. the
    defect_scope the unverifiable-residual arm reads paths from)."""
    d = runs_root / run_id
    d.mkdir(parents=True, exist_ok=True)
    data = {"task": task, "finished_at": "2026-09-30T00:00:00+00:00"}
    if gate is not None:
        data["delivery_gate"] = gate
    if extra:
        data.update(extra)
    (d / "state.json").write_text(json.dumps(data))
    if exit_code is not None:
        (d / "orchestrator.exit_code").write_text(exit_code)
    if mtime is not None:
        import os
        os.utime(d, (mtime, mtime))
    return d


def _current(leerie, tmp_path, task=TASK):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "current-run").mkdir(parents=True)
    st = leerie.State(leerie_root, "current-run")
    st.data = {"task": task}
    return st


RESIDUAL_GATE = {
    "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
    "samples_before": 3,
    "unmet_after": [{"item_index": 0,
                     "item": "the contract's second variant",
                     "evidence": "site Y still fails the shape"}],
    "samples_after": 3,
}


def test_prior_residual_found_from_newest_same_task_run(
        leerie, tmp_path):
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "older-run", TASK, RESIDUAL_GATE,
               mtime=1_000_000)
    _run_state(leerie, runs, "other-task-run", "different task",
               RESIDUAL_GATE, mtime=2_000_000)
    r = leerie._prior_delivery_residual(st)
    assert r is not None
    assert r["run_id"].startswith("older-run")
    assert r["unmet_after"][0]["evidence"] == \
        "site Y still fails the shape"


def test_prior_residual_newest_clean_run_stops_the_lookback(
        leerie, tmp_path):
    """A later run with a clean gate means the residual was resolved —
    looking further back would resurrect stale steering."""
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "older-run", TASK, RESIDUAL_GATE,
               mtime=1_000_000)
    _run_state(leerie, runs, "newest-clean", TASK,
               {"unmet_before": [], "samples_before": 1},
               mtime=2_000_000)
    assert leerie._prior_delivery_residual(st) is None


def test_prior_residual_contract_unmet_and_conflict(leerie, tmp_path):
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "r1", TASK, {
        "unmet_before": [], "samples_before": 3,
        "contract_before": {"verdict": "unmet", "evidence": "gap"},
        "unmet_after": [], "samples_after": 3,
        "contract_after": {"verdict": "unmet",
                           "evidence": "variant B still reproducible"},
    }, mtime=1_000_000)
    r = leerie._prior_delivery_residual(st)
    assert r["contract_unmet"]["evidence"] == \
        "variant B still reproducible"
    # conflict carried from contract_before — a conflict ALONE never
    # buys a recheck, so contract_after has no verdict here (one
    # forced by unmet items CAN re-judge and record contract_after —
    # test_pre_pass_conflict_with_unmet_items_rechecks_the_contract
    # in test_delivery_gate.py)
    _run_state(leerie, runs, "r2", TASK, {
        "unmet_before": [], "samples_before": 3,
        "contract_before": {
            "verdict": "conflict", "evidence": "both pinned",
            "conflicting_contracts": ["contract A", "contract B"]},
        "unmet_after": [], "samples_after": 1,
    }, mtime=2_000_000)
    r2 = leerie._prior_delivery_residual(st)
    assert r2["contract_conflict"]["conflicting_contracts"] == \
        ["contract A", "contract B"]


def test_prior_residual_none_without_siblings_or_task(leerie, tmp_path):
    st = _current(leerie, tmp_path)
    assert leerie._prior_delivery_residual(st) is None
    st.data["task"] = ""
    assert leerie._prior_delivery_residual(st) is None


def test_prior_residual_contract_unverifiable_names_missing_inputs(
        leerie, tmp_path):
    """An unverifiable residual steers with WHAT was absent, not just
    that something was: missing_inputs lists exactly the sibling
    audit's absent paths (the present one is excluded), alongside the
    recorded evidence (S-4)."""
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "r-unv", TASK, {
        "unmet_before": [], "samples_before": 1,
        "contract_before": {"verdict": "unverifiable",
                            "judge_claimed": "met",
                            "evidence": "archive absent; fixtures only"},
    }, mtime=1_000_000, extra={"defect_scope": {
        "applicable": True,
        "ground_truth": {"data_dependent": True, "inputs": [
            {"path": "/data/archive-dir", "kind": "directory",
             "role": "archive", "present": False},
            {"path": "/data/spec.json", "kind": "file",
             "role": "spec", "present": True},
        ], "repro_command": None}}})
    r = leerie._prior_delivery_residual(st)
    assert r is not None
    cu = r["contract_unverifiable"]
    assert cu["evidence"] == "archive absent; fixtures only"
    assert cu["missing_inputs"] == ["/data/archive-dir"]


def test_prior_residual_recheck_met_supersedes_unverifiable_before(
        leerie, tmp_path):
    """Same after-supersedes-before rule as conflict: a recheck that
    reached met (forced by unmet items) outranks a pre-pass
    unverifiable, leaving nothing to steer with."""
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "r-sup", TASK, {
        "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
        "samples_before": 3,
        "contract_before": {"verdict": "unverifiable", "evidence": "pre"},
        "unmet_after": [], "samples_after": 1,
        "contract_after": {"verdict": "met", "evidence": "grounded now"},
    }, mtime=1_000_000)
    assert leerie._prior_delivery_residual(st) is None


# === planner ctx injection (executed through the real phase_plan) ==========

def _drive_phase_plan(leerie, monkeypatch, st):
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"domain": "testing", "status": "ready", "subtasks": [],
                "confidence": {"task_understanding": 9.0,
                               "decomposition_quality": 9.0,
                               "basis": "stub"}}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_plan(
        "t", st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert calls, "phase_plan never reached claude_p"
    return calls


def _plan_state(leerie, tmp_path, **overrides):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "ctx-run").mkdir(parents=True)
    st = leerie.State(leerie_root, "ctx-run")
    st.data = {
        "task": TASK,
        "categories": ["testing"],
        "classifier_questions": [],
        "needs_source_of_truth": False,
        "source_of_truth_pref": "both",
        "skip_repo_map": True,
    }
    st.data.update(overrides)
    return st


def test_prior_residual_text_reaches_the_planner_prompt(
        leerie, tmp_path, monkeypatch):
    st = _plan_state(leerie, tmp_path)
    _run_state(leerie, st.run_dir.parent, "prev-run", TASK,
               RESIDUAL_GATE, mtime=1_000_000)
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert '"prior_delivery_residual"' in prompt
    assert "site Y still fails the shape" in prompt


def test_no_prior_residual_means_no_key(leerie, tmp_path, monkeypatch):
    st = _plan_state(leerie, tmp_path)
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    assert "prior_delivery_residual" not in (
        calls[0].get("user_prompt") or "")


def test_planner_prompt_documents_the_key(leerie):
    text = leerie._load_prompt("planner")
    assert "prior_delivery_residual" in text
    assert "contract_conflict" in text or "conflict" in text


def test_prior_residual_skips_crashed_newer_run(leerie, tmp_path):
    """A newer same-task run that never COMPLETED resolved nothing:
    it is SKIPPED, not a lookback stop. The die() crash shape is the
    decisive arm (review round 2): die() WRITES finished_at (the
    discovery sentinel) and a nonzero exit code — a filter keyed on
    finished_at lets exactly this run hide the older residual, which
    is how the round-1 fix was falsified against live telemetry."""
    st = _current(leerie, tmp_path)
    runs = st.run_dir.parent
    _run_state(leerie, runs, "completed-residual", TASK, RESIDUAL_GATE,
               mtime=1_000_000)
    # die()-shaped crash: state carries finished_at, exit code is "1"
    _run_state(leerie, runs, "died-newer", TASK, None,
               mtime=2_000_000, exit_code="1")
    # killed/in-flight shape: no exit-code file at all
    _run_state(leerie, runs, "inflight-newest", TASK, None,
               mtime=3_000_000, exit_code=None)
    r = leerie._prior_delivery_residual(st)
    # run_id is truncated to 16 chars in the record
    assert r is not None and r["run_id"] == "completed-residual"[:16]


def test_prior_residual_recheck_met_supersedes_pre_pass_conflict(
        leerie, tmp_path):
    """An after-verdict of met (the recheck saw the remediated tree)
    supersedes a pre-pass conflict — a resolved conflict must not
    steer the next run (review round 1)."""
    st = _current(leerie, tmp_path)
    _run_state(leerie, st.run_dir.parent, "r-resolved", TASK, {
        "unmet_before": [{"item_index": 0, "item": "x", "evidence": "e"}],
        "samples_before": 3,
        "contract_before": {"verdict": "conflict", "evidence": "was",
                            "conflicting_contracts": ["A", "B"]},
        "unmet_after": [], "samples_after": 1,
        "contract_after": {"verdict": "met", "evidence": "resolved"},
    }, mtime=1_000_000)
    assert leerie._prior_delivery_residual(st) is None


def test_digest_strips_timeout_and_env_prefixes(
        leerie, tmp_path, monkeypatch):
    """`timeout 600 pnpm test` and `NODE_ENV=test pnpm test` must be IN
    the record — the judge is told an absent command was never run, so
    a dropped prefix turns a real execution into a confident false
    unmet (review round 1: the lead-strip regex must apply to the
    segment, never to an already-split token)."""
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    _write_log(tmp_path / "logs", "s1", [
        ("timeout 600 pnpm test src/a.test.ts", "1 passed"),
        ("NODE_ENV=test pnpm test src/b.test.ts", "2 passed"),
    ])
    d = leerie._executed_commands_digest(tmp_path, tmp_path)
    assert "timeout 600 pnpm test src/a.test.ts" in d
    assert "NODE_ENV=test pnpm test src/b.test.ts" in d


def test_digest_failure_degrades_to_items_only_judging(
        leerie, tmp_path, monkeypatch):
    """A digest crash must cost the RECORD, not the gate (review
    round 2: the arm existed only as code)."""
    import asyncio as _a

    def boom(*_a2, **_k):
        raise UnicodeDecodeError("utf-8", b"", 0, 1, "torn log")

    monkeypatch.setattr(leerie, "_executed_commands_digest", boom)
    leerie_root = tmp_path / ".leerie"
    run_id = "digest-degrade"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "worktrees" / "staging").mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "t", "worker_count": 0, "working_branch": "main",
               "required_items": [{"item": "a required thing",
                                   "source_ref": "task"}]}
    st.save()
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"verdicts": [{"item_index": 0, "met": True,
                              "evidence": "ok"}]}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    unmet = _a.run(leerie._run_delivery_prejudge(
        run_dir, st, dict(leerie.DEFAULT_CAPS),
        {"delivery_judge": "sonnet"}, {"delivery_judge": "medium"}))
    assert unmet == [] and len(calls) == 1
    assert "EXECUTED COMMANDS RECORD" not in calls[0]["user_prompt"]


def test_formatter_contract_only_header_claims_no_unmet_items(leerie):
    """With zero unmet items the section must not claim 'the
    following NOT met' items (review round 2: the two headers were
    indistinguishable to the existing formatter test)."""
    only_contract = leerie._format_unmet_required_items_section(
        [], {"verdict": "unmet", "evidence": "E"})
    assert "no unmet required items" in only_contract
    assert "found the following NOT met" not in only_contract
    with_items = leerie._format_unmet_required_items_section(
        [{"item_index": 0, "item": "x", "evidence": "e"}],
        {"verdict": "unmet", "evidence": "E"})
    assert "found the following NOT met" in with_items


# === repro-verb widening (S-5 round 1, HIGH): the acceptance run must
# === be admissible to the record the repro-decides rule reads

def test_digest_admits_repro_led_commands_only_with_repro(
        leerie, tmp_path, monkeypatch):
    """The measured failure shape: a node-led repro on a pnpm-verbed
    repo. Without repro_command the execution is EXCLUDED from the
    digest (the round-1 defect — the judge would see 'never run'
    forever); with it, included with its verbatim tail. The two calls
    disagree, so a widening that ignores the parameter fails."""
    monkeypatch.setattr(leerie, "_blt_verbs", lambda _root: ["pnpm"])
    _write_log(tmp_path / "logs", "accept-001", [
        ("EXAMPLE_RUN_ID=example-run node scripts/example_repro.py "
         "--force", "classification: REST; output compiles"),
        ("pnpm test src/x.test.ts", "1 passed"),
    ])
    without = leerie._executed_commands_digest(tmp_path, tmp_path)
    assert "example_repro" not in without
    assert "pnpm test src/x.test.ts" in without
    with_repro = leerie._executed_commands_digest(
        tmp_path, tmp_path,
        repro_command="EXAMPLE_RUN_ID=example-run node "
                      "scripts/example_repro.py --force")
    assert "[accept-001] $ EXAMPLE_RUN_ID=example-run node " \
           "scripts/example_repro.py --force" in with_repro
    assert "classification: REST; output compiles" in with_repro
    assert "pnpm test src/x.test.ts" in with_repro


def test_repro_verbs_lead_strip_and_garbage(leerie):
    """_repro_verbs mirrors the digest's own segment idiom: env
    prefixes and timeout wrappers are stripped to the real verb;
    multi-segment commands contribute each segment's verb; garbage
    shapes yield the empty set (digest then behaves exactly as
    before)."""
    assert leerie._repro_verbs(
        "EXAMPLE_VAR=x timeout 600 node scripts/gen.js --force") == {"node"}
    assert leerie._repro_verbs(
        "node a.js && python3 b.py") == {"node", "python3"}
    assert leerie._repro_verbs(None) == set()
    assert leerie._repro_verbs("") == set()
    assert leerie._repro_verbs(123) == set()
