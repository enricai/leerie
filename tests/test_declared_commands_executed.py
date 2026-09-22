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
count, a DIFFERING-BINARY `--help` probe must not count (a `--help` probe
of the declared literal itself is a pinned accepted near-miss residual —
see test_near_miss_execution_is_accepted_residual), a pipeline/`cd &&`
prefix must count.
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


def test_paraphrase_declared_matches_wherever_the_command_sits(leerie):
    # The paraphrase shape: the declared entry WRAPS the command's tokens
    # in extra words — the executed segment must appear as a length-≥2
    # CONTIGUOUS ordered sublist of the declared salient-token list,
    # ANYWHERE in it. Mid-string and trailing-prose paraphrases were the
    # suffix rule's false-alarm class: each looped the re-drive into a
    # false `blocked`, whose accept-blocked remedy silently drops a
    # correct subtask's commits.
    for declared in (
        "run the full test suite with pnpm test",              # tail
        "run pnpm test to verify",                             # mid
        "run the full test suite with pnpm test and make sure it is green",
    ):
        assert leerie.check_declared_commands_executed(
            {"runs_commands": [declared]}, ["pnpm test"]) == [], declared
    assert leerie.check_declared_commands_executed(
        {"runs_commands": ["run barnacle recon browser and read the report"]},
        ["barnacle recon browser"]) == []
    assert leerie.check_declared_commands_executed(
        {"runs_commands": ["barnacle recon browser"]},
        ["recon browser"]) == []
    # A declared compound is satisfied by either of its halves — the
    # separator token makes the forward direction unmatchable, so the
    # contiguous rule carries this shape.
    for executed in (["pnpm build"], ["pnpm test"]):
        assert leerie.check_declared_commands_executed(
            {"runs_commands": ["pnpm build && pnpm test"]},
            executed) == [], executed


def test_reverse_direction_rejects_nonadjacent_fragments(leerie):
    # Adversarial rows that defeated the bare-subset reverse rule:
    # NON-ADJACENT words of a paraphrase reassembled into a "command".
    # Contiguity rejects each; a bare single token fails the ≥2 floor.
    long = ("run the full test suite with pnpm test and make sure it "
            "is green")
    for executed in (["pnpm run"], ["make test"], ["run test"],
                     ["sure green"], ["pnpm"]):
        issues = leerie.check_declared_commands_executed(
            {"runs_commands": [long]}, executed)
        assert len(issues) == 1, executed
        assert "DECLARED_CMD_UNRUN" in issues[0]


def test_near_miss_execution_is_accepted_residual(leerie):
    # The DOCUMENTED near-miss residual class (DESIGN §"A declared
    # command must also have been executed"): the gate catches "never
    # touched the declared command", not "ran a variant of it". Each row
    # here is a deliberate design acceptance, not an oversight — a
    # stricter rule (suffix-only) was shipped and withdrawn because its
    # false alarms looped into a false `blocked` that silently drops
    # correct commits, strictly worse than these near-miss passes; and
    # the forward direction always passed the mirror shapes (e.g.
    # `pnpm lint --fix --dry-run` for declared "pnpm lint --fix"), so
    # rejecting only these was incoherent. Deliberate gaming is the same
    # §9 concession that declines to gate on test content.
    for declared, executed in (
        ("barnacle recon browser --headless", "barnacle recon"),
        ("pnpm lint --fix", "pnpm lint"),
        ("pnpm test", "pnpm test --help"),
        ("run the full test suite with pnpm test and make sure it is "
         "green", "test suite"),
    ):
        assert leerie.check_declared_commands_executed(
            {"runs_commands": [declared]}, [executed]) == [], (declared,
                                                               executed)


def test_glued_punctuation_still_false_alarms(leerie):
    # The remaining documented FALSE-ALARM residual: a declared entry
    # quoting the command keeps the quote characters glued to the
    # tokens, so neither direction matches. Worst case is a re-drive
    # naming the declared string, then the operator-adjudicated blocked
    # terminal.
    for declared in ("run `pnpm test`", "please run pnpm test."):
        issues = leerie.check_declared_commands_executed(
            {"runs_commands": [declared]}, ["pnpm test"])
        assert len(issues) == 1, declared
        assert "DECLARED_CMD_UNRUN" in issues[0]


def test_no_cross_segment_union_gaming(leerie):
    # Tokens scattered across DIFFERENT commands of one compound
    # invocation must not be credited — each shape was a working bypass
    # of the union rule this replaces.
    for executed in (
        ["pnpm install && ls test"],
        ["echo pnpm; ls test"],
    ):
        issues = leerie.check_declared_commands_executed(
            {"runs_commands": ["pnpm test"]}, executed)
        assert len(issues) == 1, executed
        assert "DECLARED_CMD_UNRUN" in issues[0]
    issues = leerie.check_declared_commands_executed(
        {"runs_commands": ["barnacle recon browser"]},
        ["echo barnacle; ls recon; which browser"])
    assert len(issues) == 1 and "DECLARED_CMD_UNRUN" in issues[0]


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


def test_unexecuted_declared_command_blocks_after_redrive(
        env, monkeypatch, capsys):  # noqa: F811
    """The full escalation: re-drive with feedback naming the command,
    then convert to `blocked` (never `complete`) when the budget
    exhausts with the command still unexecuted — with the N21
    accept-blocked remedy logged at the moment of the status write."""
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
    out = capsys.readouterr().out
    assert f"accept-blocked {env['st'].run_id} {env['sid']}" in out


def test_empty_handoff_rescue_completes_with_persisted_warning(
        env, monkeypatch, capsys):  # noqa: F811
    """The empty_handoff rescue with an unrun declared command settles
    COMPLETE — blocking would strand the very commits the rescue exists
    to keep (blocked → accept-blocked marks complete → resume excludes
    the sid → integrate_wave never merges the branch) — but NEVER
    silently: the warning is logged and persisted to state, so the skip
    is visible in the run record."""
    leerie_mod = env["leerie"]
    _declare(env, ["pnpm build"])
    calls: list = []

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
        calls.append(note)
        return {"subtask_id": sid_, "status": "complete",
                **_COMPLETE_RES_EXTRA}
    monkeypatch.setattr(leerie_mod, "_run_implementer", _stub_impl)
    # Drive the REAL rescue branch: the result invariant tags
    # empty_handoff and the worktree provably has committed work.
    monkeypatch.setattr(
        leerie_mod, "_validate_result",
        lambda res: ("empty_handoff", "no checkpoint"))

    async def _has_commits(worktree, run_branch):
        return True
    monkeypatch.setattr(leerie_mod, "_branch_has_commits_ahead",
                        _has_commits)

    # The rescued result then flows the NORMAL complete path; settle it
    # via the HEAD-reprobe rescue (the established harness shape from
    # test_settle_subtask_branch_coverage.py) so no conformer spawns.
    async def _stub_probe(subtask, worktree_, st, caps, models, efforts,
                          label="post"):
        return {"satisfied": True, "evidence": "on the run branch",
                "checked": ["src.py"]}
    monkeypatch.setattr(leerie_mod, "_probe_criteria_satisfied_on_head",
                        _stub_probe)

    res = _settle(leerie_mod, env, implementer_confidence_retries=2,
                  failed_retries=0)

    assert res["status"] == "complete"
    assert len(calls) == 1  # no re-drive on the rescue path
    # The skip is persisted in state — value, not key presence.
    warnings = env["st"].data["declared_unrun_warnings"][env["sid"]]
    assert len(warnings) == 1 and "pnpm build" in warnings[0]
    out = capsys.readouterr().out
    assert "WARNING" in out and "pnpm build" in out


def test_clean_later_attempt_clears_the_stale_warning(env, monkeypatch):  # noqa: F811
    """Lifecycle: a persisted warning from an earlier rescued attempt is
    POPPED when a later complete attempt of the same sid has no unrun
    declared commands — a lingering warning would contradict the run
    record, the same hazard the symptom_findings pop and the
    blocked-dict clear guard against."""
    leerie_mod = env["leerie"]
    _declare(env, ["pnpm build"])
    _write_worker_log(env["run_dir"] / "logs" / f"{env['sid']}.log",
                      ["pnpm run build"])
    # Stale warning from a (simulated) earlier rescued attempt.
    env["st"].data["declared_unrun_warnings"] = {
        env["sid"]: ["DECLARED_CMD_UNRUN: stale"]}
    env["st"].save()

    async def _stub_impl(sid_, leerie_dir, caps, st, models, efforts,
                         continuation=False, note=""):
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
    assert env["sid"] not in env["st"].data.get(
        "declared_unrun_warnings", {})


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
