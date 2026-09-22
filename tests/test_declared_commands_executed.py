"""Tests for the settle-time declared-command check (DESIGN §"A declared
command must also have been executed"): `check_declared_commands_executed`,
`_executed_bash_commands`, and their `_settle_subtask` wiring.

Motivation (measured, barnacle 2026-09-22): a subtask declared
`runs_commands` naming the task's one empirical acceptance command, its
worker never issued it, and the subtask settled `complete` — across ten
runs of that task the declared command was executed zero times while every
run finalized green. `check_prescribed_command_coverage` only proves a
subtask *declares* a command; this is the execution half.

Substance discipline: the settle tests drive the real `_settle_subtask`
(reusing tests/test_oom_naming.py's `env` fixture) and assert terminal
VALUES — the blocked blocker names the exact command, the re-drive
feedback note carries it, and the executed/undeclared controls complete
with exactly one implementer spawn. The pure-function cases make declared
and executed DISAGREE in the shapes that matter: quoted-in-grep must not
count, `--help` probing must not count, a pipeline/`cd &&` prefix must.
"""
from __future__ import annotations

import json

from tests.conftest import _run
from tests.test_oom_naming import env  # noqa: F401  (pytest fixture)


# ---------------------------------------------------------------------------
# check_declared_commands_executed (pure)
# ---------------------------------------------------------------------------

def test_nothing_declared_is_free(leerie):
    assert leerie.check_declared_commands_executed(
        {"id": "s1"}, ["pnpm build"]) == []
    assert leerie.check_declared_commands_executed(
        {"id": "s1", "runs_commands": []}, []) == []


def test_verbatim_execution_covers(leerie):
    assert leerie.check_declared_commands_executed(
        {"runs_commands": ["pnpm build"]}, ["pnpm build"]) == []


def test_pipeline_and_cd_prefix_still_match(leerie):
    # The declared entry must match inside a segment of a compound
    # command: `cd`/env prefixes stripped, pipeline split first.
    assert leerie.check_declared_commands_executed(
        {"runs_commands": ["pnpm build"]},
        ["cd /work && NODE_ENV=test pnpm run build 2>&1 | tail -5"]) == []


def test_tokens_split_across_segments_match_via_union(leerie):
    # Declared tokens spread over a compound command's segments are
    # covered by the segments' union set.
    assert leerie.check_declared_commands_executed(
        {"runs_commands": ["build widgets"]},
        ["prep widgets --stage 1 && build --now"]) == []


def test_quoted_in_grep_does_not_count(leerie):
    # The verbatim string appearing inside a quoted grep pattern is not
    # an execution — the quote characters stay glued to the tokens.
    issues = leerie.check_declared_commands_executed(
        {"runs_commands": ["recon-generate --force"]},
        ["grep -rn 'recon-generate --force' src/"])
    assert len(issues) == 1
    assert issues[0].startswith("DECLARED_CMD_UNRUN")
    assert "recon-generate --force" in issues[0]


def test_help_probe_does_not_count(leerie):
    # The real barnacle shape: the only invocation of the declared tool
    # was `--help` startup timing, discarded to /dev/null.
    issues = leerie.check_declared_commands_executed(
        {"runs_commands": ["recon-generate --force"]},
        ["time pnpm exec tsx src/scripts/recon-generate.ts --help "
         ">/dev/null 2>&1"])
    assert len(issues) == 1
    assert "DECLARED_CMD_UNRUN" in issues[0]


def test_each_missing_declared_command_yields_one_issue(leerie):
    issues = leerie.check_declared_commands_executed(
        {"runs_commands": ["pnpm build", "pnpm test"]},
        ["pnpm test src/a.test.ts"])
    assert len(issues) == 1
    assert "pnpm build" in issues[0]


def test_gating_not_advisory(leerie):
    """DECLARED_CMD_UNRUN must re-drive: the advisory allowlist is an
    explicit opt-out, and this issue is not on it."""
    issues = leerie.check_declared_commands_executed(
        {"runs_commands": ["pnpm build"]}, [])
    assert leerie._gating_issues(issues) == issues


# ---------------------------------------------------------------------------
# _executed_bash_commands (JSONL log extraction)
# ---------------------------------------------------------------------------

def _write_worker_log(path, commands):
    lines = []
    for i, cmd in enumerate(commands):
        lines.append(json.dumps({"message": {"content": [
            {"type": "tool_use", "id": f"tu_{i}", "name": "Bash",
             "input": {"command": cmd}}]}}))
    # A non-Bash tool_use and a malformed line must both be ignored.
    lines.append(json.dumps({"message": {"content": [
        {"type": "tool_use", "id": "tu_r", "name": "Read",
         "input": {"file_path": "x"}}]}}))
    lines.append("not json {")
    path.write_text("\n".join(lines) + "\n")


def test_extracts_bash_commands_in_order(leerie, tmp_path):
    log = tmp_path / "w.log"
    _write_worker_log(log, ["git status", "pnpm build"])
    assert leerie._executed_bash_commands(log) == ["git status",
                                                   "pnpm build"]


def test_missing_log_is_empty(leerie, tmp_path):
    assert leerie._executed_bash_commands(tmp_path / "absent.log") == []


# ---------------------------------------------------------------------------
# _settle_subtask wiring (real settle loop, stubbed implementer)
# ---------------------------------------------------------------------------

_COMPLETE_RES_EXTRA = {
    "summary": "done", "criteria_results": [],
    "production_evidence": {"exercised": True, "how": "n/a",
                            "observed": "n/a"},
}


def _declare(env, commands):  # noqa: F811
    spec_path = env["run_dir"] / "subtasks" / f"{env['sid']}.json"
    spec = json.loads(spec_path.read_text())
    spec["runs_commands"] = commands
    spec_path.write_text(json.dumps(spec))


def _settle(leerie_mod, env, **caps_overrides):  # noqa: F811
    caps = dict(env["caps"])
    caps.update(caps_overrides)
    return _run(leerie_mod._settle_subtask(
        env["sid"], env["run_dir"], caps, env["st"],
        env["models"], env["efforts"]))


def test_unexecuted_declared_command_blocks_after_redrive(env, monkeypatch):  # noqa: F811
    """The full escalation: re-drive with feedback naming the command,
    then convert to `blocked` (never `complete`) when the budget
    exhausts with the command still unexecuted."""
    leerie_mod = env["leerie"]
    _declare(env, ["pnpm build"])
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid_, "status": "complete",
                **_COMPLETE_RES_EXTRA}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    res = _settle(leerie_mod, env, implementer_confidence_retries=1)

    assert res["status"] == "blocked"
    assert "pnpm build" in res["blocker"]
    assert "DECLARED_CMD_UNRUN" in res["blocker"]
    assert env["st"].data["subtask_status"][env["sid"]] == "blocked"
    # 1 attempt + 1 corrective re-drive, whose note names the command.
    assert len(calls) == 2
    assert "DECLARED_CMD_UNRUN" in calls[1] and "pnpm build" in calls[1]


def test_executed_declared_command_settles_complete_without_redrive(
        env, monkeypatch):  # noqa: F811
    leerie_mod = env["leerie"]
    _declare(env, ["pnpm build"])
    _write_worker_log(env["run_dir"] / "logs" / f"{env['sid']}.log",
                      ["cd /work && pnpm run build | tail -3"])
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid_, "status": "complete",
                **_COMPLETE_RES_EXTRA}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    async def _stub_probe(subtask, worktree_, st, caps, models, efforts,
                          label="post"):
        return {"satisfied": True, "evidence": "on the run branch",
                "checked": ["src.py"]}
    monkeypatch.setattr(leerie_mod, "_probe_criteria_satisfied_on_head",
                        _stub_probe)

    res = _settle(leerie_mod, env, failed_retries=0)

    assert res["status"] == "complete"
    assert len(calls) == 1


def test_undeclared_subtask_is_untouched(env, monkeypatch):  # noqa: F811
    """Anti-vacuity control: no runs_commands, no log file — the check
    must not fire at all and the pre-change behavior stands."""
    leerie_mod = env["leerie"]
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid_, "status": "complete",
                **_COMPLETE_RES_EXTRA}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)

    async def _stub_probe(subtask, worktree_, st, caps, models, efforts,
                          label="post"):
        return {"satisfied": True, "evidence": "on the run branch",
                "checked": ["src.py"]}
    monkeypatch.setattr(leerie_mod, "_probe_criteria_satisfied_on_head",
                        _stub_probe)

    res = _settle(leerie_mod, env, failed_retries=0)

    assert res["status"] == "complete"
    assert len(calls) == 1
