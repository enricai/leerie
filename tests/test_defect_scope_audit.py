"""Tests for the pre-planning defect-scope audit (DESIGN §5 *Defect-scope
audit*): `phase_defect_scope_audit`, the planner-ctx injection, and the
mechanical floor `_warn_defect_sites_uncovered`.

Motivation (measured, 2026-09-22..25): a multi-site defect was re-planned
as "the one remaining gap" run after run — 7 of 11 production commits
re-edited the same ~120-line region, each faithful to its own narrow
plan, while the live symptom survived. The full site enumeration was two
greps away on day one; no one was asked to produce it before planning.

Coverage discipline: the phase function and the warn are executed (with a
stubbed `claude_p` dispatching on schema_key); the ctx test drives the
REAL `phase_plan` and asserts the site text lands in the planner's
prompt. Values, not key presence.
"""
from __future__ import annotations

import asyncio
import inspect

import pytest

MODELS = {"defect_scope_auditor": "sonnet", "planner": "sonnet",
          "fit_judge": "sonnet", "splitter": "sonnet"}
EFFORTS = {"defect_scope_auditor": "medium", "planner": "medium",
           "fit_judge": "medium", "splitter": "medium"}

AUDIT = {
    "applicable": True,
    "defect_shape": "candidate matching keys on positional index",
    "sites": [
        {"file": "src/example_module.py", "symbol": "merge_candidates",
         "line_hint": 120, "role": "decision_site"},
        {"file": "src/example_module.py", "symbol": "collect_pending_rows",
         "line_hint": 480, "role": "bypass"},
    ],
    "chokepoint": {"exists": True, "file": "src/example_module.py",
                   "symbol": "resolve_identity_key",
                   "rationale": "sole producer of the comparison key"},
}


def _state(leerie, tmp_path, **overrides):
    leerie_root = tmp_path / ".leerie"
    run_id = "test-defect-scope"
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "fix the bug", "worker_count": 0,
               "categories": ["bug-fixing"],
               "classifier_questions": [],
               "needs_source_of_truth": False,
               "source_of_truth_pref": "both",
               "skip_repo_map": True,
               "planning_worktree": str(run_dir / "worktrees" / "planning")}
    st.data.update(overrides)
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


def _patch_auditor(leerie, monkeypatch, result):
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        assert kwargs.get("schema_key") == "defect_scope_auditor"
        calls.append(kwargs)
        if result == "CRASH":
            raise leerie.WorkerError("auditor boom")
        return dict(result)

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


# === the phase, executed ===================================================

def test_bug_fixing_task_gets_the_enumeration(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    calls = _patch_auditor(leerie, monkeypatch, AUDIT)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1
    assert scope["applicable"] is True
    assert [s["symbol"] for s in scope["sites"]] == [
        "merge_candidates", "collect_pending_rows"]
    assert scope["chokepoint"]["symbol"] == "resolve_identity_key"
    # Judgment-worker scope pins.
    assert calls[0]["autonomous"] is False
    assert calls[0]["allowed_tools"] == leerie.INSPECT_TOOLS


def test_non_bug_fixing_task_pays_nothing(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, categories=["documentation"])
    calls = _patch_auditor(leerie, monkeypatch, AUDIT)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "write docs", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}
    assert calls == []


def test_not_applicable_verdict_is_respected(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch,
                   {"applicable": False, "sites": []})
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}


def test_crash_every_round_degrades(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path)
    calls = _patch_auditor(leerie, monkeypatch, "CRASH")
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}
    assert len(calls) == 3, "bounded retry, then degrade — never die"


def test_malformed_sites_are_dropped(leerie, tmp_path, monkeypatch):
    _patch_auditor(leerie, monkeypatch, {
        "applicable": True,
        "sites": [{"file": "src/a.py", "symbol": "f", "role": "consumer"},
                  {"file": "", "symbol": "g", "role": "consumer"},
                  {"symbol": "h", "role": "consumer"},
                  "not-a-dict"],
    })
    st = _state(leerie, tmp_path)
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert [s["file"] for s in scope["sites"]] == ["src/a.py"]
    assert scope["chokepoint"] == {"exists": False}


# === ctx delivery, through the real phase_plan =============================

def _drive_phase_plan(leerie, monkeypatch, st):
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        return {"domain": "bug-fixing", "status": "ready", "subtasks": [],
                "confidence": {"task_understanding": 9.0,
                               "decomposition_quality": 9.0,
                               "basis": "stub"}}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie.phase_plan(
        "t", st, dict(leerie.DEFAULT_CAPS), MODELS, EFFORTS))
    assert calls, "phase_plan never reached claude_p"
    return calls


def test_scope_reaches_the_planner_prompt(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, defect_scope=dict(AUDIT))
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "collect_pending_rows" in prompt, (
        "the bypass site — historically the missed one — must reach "
        "the planner")
    assert "resolve_identity_key" in prompt
    assert '"defect_scope"' in prompt


@pytest.mark.parametrize("scope", [
    None,
    {"applicable": False},
    {"applicable": True, "sites": []},
])
def test_inapplicable_scope_is_omitted(leerie, tmp_path, monkeypatch,
                                       scope):
    overrides = {} if scope is None else {"defect_scope": scope}
    st = _state(leerie, tmp_path, **overrides)
    calls = _drive_phase_plan(leerie, monkeypatch, st)
    prompt = calls[0].get("user_prompt") or ""
    assert "defect_scope" not in prompt


# === the mechanical floor ==================================================

def _plans_touching(*files):
    return [{"domain": "bug-fixing", "subtasks": [
        {"id": "bugfix-001", "files_likely_touched": list(files)}]}]


def test_uncovered_site_warns_with_file_and_symbol(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(
        _plans_touching("src/other.py"), AUDIT)
    joined = "\n".join(lines)
    assert "src/example_module.py" in joined
    assert "collect_pending_rows" in joined
    assert "bypass" in joined


def test_covered_sites_are_silent(leerie, monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(
        _plans_touching("./src/example_module.py"), AUDIT)
    assert lines == [], "dot-prefixed paths must normalize, not warn"


@pytest.mark.parametrize("scope", [
    {}, {"applicable": False, "sites": AUDIT["sites"]},
    {"applicable": True, "sites": []},
])
def test_inapplicable_scope_never_warns(leerie, monkeypatch, scope):
    lines: list[str] = []
    monkeypatch.setattr(leerie, "log", lines.append)
    leerie._warn_defect_sites_uncovered(_plans_touching(), scope)
    assert lines == []


# === wiring pins ===========================================================

class TestWiring:
    def test_run_phases_checkpoints_the_audit(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert ('if ("defect_scope" not in st.data\n'
                '                and "plans_after_plan" not in st.data):'
                ) in src, (
            "the audit must be gated on BOTH its own checkpoint and "
            "planning not being checkpointed — a resume of a "
            "pre-feature state past planning must not spawn the "
            "auditor (CI: worker_count changed on a free re-entry)")
        assert "phase_defect_scope_audit(" in src
        # After the registry, before planning.
        assert (src.index("phase_artifact_registry(")
                < src.index("phase_defect_scope_audit(")
                < src.index("phase_plan("))

    def test_schedule_calls_the_floor(self, leerie):
        src = inspect.getsource(leerie._run_phases)
        assert "_warn_defect_sites_uncovered(" in src

    def test_planner_prompt_documents_the_key(self, leerie):
        text = leerie._load_prompt("planner")
        assert "defect_scope" in text
        assert "chokepoint" in text

    def test_worker_registered(self, leerie):
        assert "defect_scope_auditor" in leerie.WORKER_TYPES
        assert "defect_scope_auditor" in leerie.PLANNING_WORKER_TYPES
        assert "defect_scope_auditor" not in leerie.MODEL_DEFAULT_PER_WORKER
        assert (leerie.EFFORT_DEFAULT_PER_WORKER["defect_scope_auditor"]
                == "medium")
        assert "defect_scope_auditor" not in leerie.TIMEOUT_DEFAULT_PER_WORKER
        assert "defect_scope_auditor" in leerie.SCHEMAS


def test_applicable_with_zero_sites_names_itself(
        leerie, tmp_path, monkeypatch, capsys):
    """A paid-but-empty audit (applicable:true, zero enumerable sites)
    was silent — measured live 2026-09-29 — while planners flew without
    enumeration. Behavior is unchanged (nothing injectable); the
    outcome must now log its own line."""
    st = _state(leerie, tmp_path)
    _patch_auditor(leerie, monkeypatch,
                   {"applicable": True, "defect_shape": "the contract",
                    "sites": []})
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope["applicable"] is True and scope["sites"] == []
    out = capsys.readouterr().out
    assert "applicable but zero enumerable" in out


# === one-shot empty-enumeration re-ask + report-named ground truth (S-4,
# === DESIGN §5 *An applicable audit with zero sites is re-asked once* /
# === *Report-named ground truth is extracted and mechanically checked*)

def _patch_auditor_seq(leerie, monkeypatch, results):
    """Sequence-dispatching auditor stub: one queued result per call,
    so the re-ask's extra spawn (and ONLY one) is observable."""
    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        assert kwargs.get("schema_key") == "defect_scope_auditor"
        calls.append(kwargs)
        assert results, "auditor spawned more calls than the test queued"
        r = results.pop(0)
        if r == "CRASH":
            raise leerie.WorkerError("auditor boom")
        return dict(r)

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    return calls


MISFILED = {"applicable": True, "sites": [],
            "defect_shape": 'contract prose with the enumeration embedded '
                            'as text: [{"file": "src/example_module.py"}]'}


def test_empty_sites_reask_recovers_the_enumeration(
        leerie, tmp_path, monkeypatch):
    """The measured misfiling shape: applicable with sites=[] while the
    enumeration sits in defect_shape prose. Exactly one corrective
    re-ask runs — carrying the previous answer and the correction —
    and its recovered sites are the scope's sites."""
    st = _state(leerie, tmp_path)
    calls = _patch_auditor_seq(leerie, monkeypatch, [dict(MISFILED),
                                                     dict(AUDIT)])
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 2
    assert [s["symbol"] for s in scope["sites"]] == [
        "merge_candidates", "collect_pending_rows"]
    reask = calls[1]
    assert reask["sid"] == "defect_scope_auditor-reask"
    assert "CORRECTION REQUIRED" in reask["user_prompt"]
    # the previous answer rides along, so the worker corrects rather
    # than re-derives
    assert "enumeration embedded" in reask["user_prompt"]


def test_empty_sites_reask_crash_keeps_the_empty_answer(
        leerie, tmp_path, monkeypatch, capsys):
    st = _state(leerie, tmp_path)
    calls = _patch_auditor_seq(leerie, monkeypatch, [dict(MISFILED),
                                                     "CRASH"])
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 2, "the re-ask is one-shot: a crash never retries"
    assert scope["applicable"] is True and scope["sites"] == []
    out = capsys.readouterr().out
    assert "re-ask crashed" in out
    assert "applicable but zero enumerable" in out


def test_empty_sites_reask_not_applicable_is_honored(
        leerie, tmp_path, monkeypatch):
    """The second answer is honored either way — including a flip to
    not-applicable (the corrective look can conclude the first
    answer's applicable was wrong)."""
    st = _state(leerie, tmp_path)
    _patch_auditor_seq(leerie, monkeypatch, [
        dict(MISFILED), {"applicable": False, "sites": []}])
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope == {"applicable": False}


def test_nonempty_sites_never_reask(leerie, tmp_path, monkeypatch):
    """The re-ask exists for the empty-enumeration shape only."""
    st = _state(leerie, tmp_path)
    calls = _patch_auditor_seq(leerie, monkeypatch, [dict(AUDIT)])
    asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert len(calls) == 1


def test_check_ground_truth_inputs_flags_presence(leerie, tmp_path,
                                                  monkeypatch):
    """present is a REAL filesystem answer per input, with ~ expanded
    (the flags disagree across the two inputs, so a bypass that
    hardcodes either value fails)."""
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "flow.json").write_text("{}")
    gt = leerie._check_ground_truth_inputs({
        "data_dependent": True,
        "inputs": [
            {"path": "~/flow.json", "kind": "file", "role": "spec"},
            {"path": str(tmp_path / "gone-dir"), "kind": "directory",
             "role": "archive"},
        ],
        "repro_command": "node generate.js"})
    assert gt["data_dependent"] is True
    assert gt["repro_command"] == "node generate.js"
    assert [(i["path"], i["present"]) for i in gt["inputs"]] == [
        ("~/flow.json", True),
        (str(tmp_path / "gone-dir"), False)]


def test_check_ground_truth_inputs_tolerates_malformed(leerie):
    assert leerie._check_ground_truth_inputs(None) is None
    assert leerie._check_ground_truth_inputs("prose") is None
    gt = leerie._check_ground_truth_inputs(
        {"data_dependent": True,
         "inputs": [{"kind": "file"}, "junk", None]})
    assert gt["inputs"] == []


def test_audit_persists_ground_truth_and_logs_remediation(
        leerie, tmp_path, monkeypatch, capsys):
    """The phase carries the checked ground_truth into the scope, and
    a data-dependent report with an absent input logs the loud
    operator remediation naming --inspect-dir — the measured
    alternative was learning the gap from telemetry ten runs in."""
    st = _state(leerie, tmp_path)
    absent = tmp_path / "missing-archive"
    _patch_auditor(leerie, monkeypatch, {
        **AUDIT,
        "ground_truth": {"data_dependent": True, "inputs": [
            {"path": str(absent), "kind": "directory", "role": "archive"}],
            "repro_command": None}})
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    assert scope["ground_truth"]["inputs"][0]["present"] is False
    out = capsys.readouterr().out
    assert "NOT available in this environment" in out
    assert "--inspect-dir" in out
    assert str(absent) in out


# === resolved_path: resolution is the auditor's judgment, existence the
# === orchestrator's check (S-5, DESIGN §5 *Resolution is the auditor's
# === judgment*)

def test_check_ground_truth_resolution_arms(leerie, tmp_path):
    """Four arms whose answers all differ, against the real
    filesystem: (a) container view — verbatim absent, resolved
    exists → present at the resolution; (b) host view — resolved
    dangling, verbatim exists → present at the verbatim path (the
    winning probe is recorded); (c) both absent with a dangling
    resolution → absent, the auditor's claim KEPT on the record for
    the gate's re-probe; (d) no resolution, absent → absent, null."""
    mounted = tmp_path / "inspect" / "dataset"
    mounted.mkdir(parents=True)
    hostfile = tmp_path / "host.json"
    hostfile.write_text("{}")

    def one(path, rp):
        gt = leerie._check_ground_truth_inputs(
            {"data_dependent": True,
             "inputs": [{"path": str(path), "kind": "directory",
                         "role": "r", "resolved_path": rp}],
             "repro_command": None}, log_missing=False)
        i = gt["inputs"][0]
        return i["present"], i["resolved_path"]

    assert one(tmp_path / "gone", str(mounted)) == (True, str(mounted))
    assert one(hostfile, str(tmp_path / "dangling")) == \
        (True, str(hostfile))
    assert one(tmp_path / "gone", str(tmp_path / "dangling")) == \
        (False, str(tmp_path / "dangling"))
    assert one(tmp_path / "gone", None) == (False, None)


def test_audit_persists_resolved_path(leerie, tmp_path, monkeypatch):
    """The phase carries the auditor's resolution through to state,
    normalized by the mechanical check (the resolved location exists,
    so present flips true even though the verbatim path is absent —
    a container-view audit)."""
    st = _state(leerie, tmp_path)
    mounted = tmp_path / "inspect" / "archive"
    mounted.mkdir(parents=True)
    _patch_auditor(leerie, monkeypatch, {
        **AUDIT,
        "ground_truth": {"data_dependent": True, "inputs": [
            {"path": "/host-only/archive", "kind": "directory",
             "role": "archive", "resolved_path": str(mounted)}],
            "repro_command": "python3 scripts/example_repro.py"}})
    scope = asyncio.run(leerie.phase_defect_scope_audit(
        "fix the bug", st, _caps(leerie), MODELS, EFFORTS))
    i = scope["ground_truth"]["inputs"][0]
    assert i["present"] is True
    assert i["resolved_path"] == str(mounted)
    assert scope["ground_truth"]["repro_command"] == \
        "python3 scripts/example_repro.py"


def test_prompts_document_resolution_and_acceptance(leerie):
    """Documentation pins: the auditor prompt defines resolved_path
    as a VERIFIED claim (never a guess); the planner prompt carries
    the acceptance-subtask directive for resolved-present inputs;
    the judge prompt carries the repro-decides converse rule."""
    auditor = leerie._load_prompt("defect_scope_auditor")
    assert "resolved_path" in auditor
    assert "claim you verified, never a guess" in auditor
    planner = leerie._load_prompt("planner")
    assert "EXECUTES that repro" in planner
    assert "resolved_path" in planner
    judge = leerie._load_prompt("delivery_judge")
    assert "repro was not executed against the" in judge
