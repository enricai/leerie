"""The whole suite is orchestrator-owned (DESIGN §9).

Two mechanisms, each pinned by executing it rather than by reading source:

1. *Absence means deferred, never full* -- a subtask diff with no proxy must
   not pay the canonical suite, and the deferred axis must be RECORDED so the
   conformer's self-report cannot survive `_apply_measured_axes`.
2. *Strip + deny + notice* -- a planner-declared whole-suite `runs_commands`
   entry is removed before scheduling, the CLI denies the invocation, and the
   implementer is told why. Deny alone was measured to end replayed workers
   `blocked` (2/2), so the notice is asserted alongside the deny every time.

Inputs are taken from the 2026-10-26 incident (leerie's own runs) wherever a
real value exists, and from barnacle-shaped npm configs for the negative
controls, so a matcher that only works for pytest fails here.
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import subprocess
import types

import pytest


def _git(cmd, cwd):
    r = subprocess.run(["git", *cmd], cwd=cwd, capture_output=True, text=True)
    assert r.returncode == 0, f"git {cmd} failed: {r.stderr}"


def _repo(tmp_path, *, test="pytest", test_scoped=None, tests_dir=True,
          ini=None):
    root = tmp_path / "repo"
    root.mkdir()
    cfg = [f'test = "{test}"']
    if test_scoped:
        cfg.append(f'test_scoped = "{test_scoped}"')
    (root / ".leerie").mkdir()
    (root / ".leerie" / "config.toml").write_text("\n".join(cfg) + "\n")
    if tests_dir:
        (root / "tests").mkdir()
    if ini is not None:
        (root / "pytest.ini").write_text(ini)
    return root


# ---------------------------------------------------------------------------
# deny rules: exact strings (the forms probed live against the CLI)
# ---------------------------------------------------------------------------

class TestFullSuiteDenials:
    def test_pytest_repo_gets_every_probed_form(self, leerie, tmp_path):
        rules = leerie._full_suite_denials(_repo(tmp_path)).split(",")
        for r in ("pytest", "python3 -m pytest"):
            for shape in (f"Bash({r})", f"Bash({r} tests)",
                          f"Bash({r} tests/)", f"Bash({r} tests *)",
                          f"Bash({r} tests/ *)", f"Bash({r} * tests)",
                          f"Bash({r} * tests/)"):
                assert shape in rules, shape

    def test_never_a_rule_that_would_catch_a_targeted_file(self, leerie,
                                                           tmp_path):
        """`Bash(pytest tests/*)` (no space) would deny
        `pytest tests/test_x.py` -- the targeted run the notice tells the
        worker to use instead. Every root rule must be space-delimited."""
        rules = leerie._full_suite_denials(_repo(tmp_path)).split(",")
        assert not any(r.endswith("tests/*)") or r.endswith("tests*)")
                       for r in rules)
        assert not any(r.endswith(" -*)") for r in rules), \
            "a flags-only wildcard would also deny `pytest -q tests/test_x.py`"

    def test_testpaths_replace_the_default_root(self, leerie, tmp_path):
        root = _repo(tmp_path, tests_dir=False,
                     ini="[pytest]\ntestpaths = spec/unit\n")
        rules = leerie._full_suite_denials(root)
        assert "Bash(pytest spec/unit/ *)" in rules
        assert " tests" not in rules

    def test_npm_family_denies_both_spellings_and_nothing_filtered(
            self, leerie, tmp_path):
        root = _repo(tmp_path, test="pnpm run test", tests_dir=False)
        assert leerie._full_suite_denials(root) == \
            "Bash(pnpm run test),Bash(pnpm test)"

    def test_no_test_command_means_no_rules_and_no_notice(self, leerie,
                                                          tmp_path):
        root = _repo(tmp_path, test="")
        assert leerie._full_suite_denials(root) == ""
        assert leerie._full_suite_notice(root) is None

    def test_an_unexpressible_canonical_command_is_declined(self, leerie,
                                                            tmp_path):
        """A comma or operator would split or corrupt the comma-joined
        `--disallowedTools` value; declining is safer than a malformed list."""
        root = _repo(tmp_path, test="make deps && pytest")
        assert leerie._full_suite_denials(root) == ""


# ---------------------------------------------------------------------------
# the matcher the strip relies on
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd,whole", [
    ("pytest tests/", True),
    ("pytest", True),
    ("python3 -m pytest tests/ -q -x -n 4", True),
    ("timeout 590 python3 -m pytest tests/ -q", True),
    ("python3 -m pytest -q -p no:cacheprovider tests/", True),
    ("pytest tests/test_no_undefined_names.py", False),
    ("python3 -m pytest -q tests/ -k resolve", False),
    ("pytest tests/ -m slow", False),
    ("pytest tests/ && shellcheck x.sh", False),
    ("shellcheck scripts/remote/ec2-*.sh", False),
])
def test_is_canonical_suite_command(leerie, cmd, whole):
    runners = leerie._full_suite_runners("pytest")
    assert leerie._is_canonical_suite_command(cmd, runners, ["tests"]) is whole


@pytest.mark.parametrize("cmd,whole", [
    ("pnpm run test", True),
    ("pnpm test", True),
    ("pnpm test flow-runner", False),
    ("pnpm test src/site-agnostic.test.ts", False),
    ("pnpm run lint", False),
])
def test_npm_family_matcher(leerie, cmd, whole):
    runners = leerie._full_suite_runners("pnpm run test")
    assert leerie._is_canonical_suite_command(cmd, runners, []) is whole


# ---------------------------------------------------------------------------
# plan-time strip: the four real incident subtasks, and what must survive
# ---------------------------------------------------------------------------

def _incident_plans():
    """`runs_commands` exactly as the 2026-10-26 planners emitted them."""
    shared = ["pytest tests/", "pytest tests/test_no_undefined_names.py",
              "shellcheck scripts/remote/ec2-*.sh"]
    return [{"domain": "testing", "subtasks": [
        {"id": "test-003", "runs_commands":
            ["pytest tests/", "pytest tests/test_no_undefined_names.py"]},
        {"id": "infra-013", "runs_commands": list(shared)},
        {"id": "test-002", "runs_commands": []},
        {"id": "feat-001"},
    ]}]


class TestStrip:
    def test_strips_only_the_whole_suite_entry(self, leerie, tmp_path):
        plans = _incident_plans()
        touched = leerie._strip_canonical_suite_commands(
            plans, _repo(tmp_path), {})
        assert touched == ["test-003", "infra-013"]
        subs = {s["id"]: s for s in plans[0]["subtasks"]}
        assert subs["test-003"]["runs_commands"] == \
            ["pytest tests/test_no_undefined_names.py"]
        assert subs["infra-013"]["runs_commands"] == \
            ["pytest tests/test_no_undefined_names.py",
             "shellcheck scripts/remote/ec2-*.sh"]
        assert "runs_commands" not in subs["feat-001"]

    def test_a_prescribed_command_is_never_stripped(self, leerie, tmp_path):
        """An explicit user instruction outranks the default (DESIGN §9)."""
        plans = _incident_plans()
        touched = leerie._strip_canonical_suite_commands(
            plans, _repo(tmp_path),
            {"is_prescribed": True, "commands": ["pytest  tests/"]})
        assert touched == []
        assert plans[0]["subtasks"][0]["runs_commands"][0] == "pytest tests/"

    def test_npm_filtered_runs_survive(self, leerie, tmp_path):
        """Barnacle's plans declare `pnpm test <filter>` on almost every
        verification subtask; none of them may be touched."""
        plans = [{"subtasks": [{"id": "test-005", "runs_commands": [
            "pnpm test click-settle", "pnpm test src/site-agnostic.test.ts",
            "pnpm run typecheck", "pnpm run test"]}]}]
        touched = leerie._strip_canonical_suite_commands(
            plans, _repo(tmp_path, test="pnpm run test", tests_dir=False), {})
        assert touched == ["test-005"]
        assert plans[0]["subtasks"][0]["runs_commands"] == [
            "pnpm test click-settle", "pnpm test src/site-agnostic.test.ts",
            "pnpm run typecheck"]

    def test_after_the_strip_the_declared_check_no_longer_demands_it(
            self, leerie, tmp_path):
        """The reason the strip exists: before it, a worker that ran only
        targeted files was re-driven, then blocked, by DECLARED_CMD_UNRUN."""
        executed = ["python3 -m pytest tests/test_no_undefined_names.py -q"]
        spec = _incident_plans()[0]["subtasks"][0]
        assert leerie.check_declared_commands_executed(spec, executed), \
            "control: without the strip the check must fire"
        plans = _incident_plans()
        leerie._strip_canonical_suite_commands(plans, _repo(tmp_path), {})
        assert leerie.check_declared_commands_executed(
            plans[0]["subtasks"][0], executed) == []

    def test_is_idempotent(self, leerie, tmp_path):
        plans = _incident_plans()
        root = _repo(tmp_path)
        leerie._strip_canonical_suite_commands(plans, root, {})
        assert leerie._strip_canonical_suite_commands(plans, root, {}) == []


# ---------------------------------------------------------------------------
# deny + notice reach the worker together (executed, not source-read)
# ---------------------------------------------------------------------------

def _argv_for(leerie, monkeypatch, repo_root, schema_key="implementer"):
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        repo_root=repo_root, data={"verbosity": "quiet"}, run_id="r1",
        bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key=schema_key,
        cwd="/leerie-state/runs/r1/worktrees/w", allowed_tools="Read",
        max_turns=40, autonomous=True, caps=dict(leerie.DEFAULT_CAPS),
        st=st, model="sonnet", sid="t"))
    argv = captured["cmd"]
    return argv[argv.index("--disallowedTools") + 1]


def test_the_deny_reaches_the_argv(leerie, monkeypatch, tmp_path):
    deny = _argv_for(leerie, monkeypatch, _repo(tmp_path))
    assert ",Bash(pytest tests/ *)," in deny
    assert deny.startswith("Agent,Task,"), "base denials must be preserved"


def test_no_deny_when_the_repo_has_no_test_command(leerie, monkeypatch,
                                                   tmp_path):
    deny = _argv_for(leerie, monkeypatch, _repo(tmp_path, test=""))
    assert "Bash(" not in deny


@pytest.fixture
def impl_env(leerie, tmp_path):
    repo = _repo(tmp_path)
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    run_dir = repo / ".leerie" / "runs" / "r1"
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "checkpoints").mkdir()
    (run_dir / "artifacts").mkdir()
    sid = "test-003"
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(
        {"id": sid, "title": "verify", "success_criteria_seed":
         "`pytest tests/` passes", "files_likely_touched": []}))
    (run_dir / "plan.json").write_text(json.dumps(
        {"task": "x", "waves": [], "subtasks": {
            sid: {"id": sid, "depends_on": [], "requires": []}}}))
    st = leerie.State(repo / ".leerie", "r1", repo_root=repo)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"}}
    st.save()
    return repo, run_dir, st, sid


def _implementer_prompt(leerie, monkeypatch, impl_env):
    repo, run_dir, st, sid = impl_env

    async def fake_script(name, *args):
        return subprocess.CompletedProcess(
            args=["bash"], returncode=0, stdout=f"{repo / 'wt'}\n", stderr="")

    calls: list = []

    async def fake_claude_p(user_prompt, system_prompt, **kw):
        calls.append(user_prompt)
        return {"subtask_id": sid, "status": "complete", "branch": "b"}

    monkeypatch.setattr(leerie, "_run_script", fake_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    asyncio.run(leerie._run_implementer(
        sid, run_dir, dict(leerie.DEFAULT_CAPS), st,
        {w: "sonnet" for w in leerie.WORKER_TYPES},
        {w: None for w in leerie.WORKER_TYPES}))
    return calls[0]


def test_the_implementer_is_told_whenever_the_deny_is_in_force(
        leerie, monkeypatch, impl_env):
    """Behavioural probe on the prose, not a substring: the notice must name
    the canonical command, say the worker may not run it, and give the exact
    `not_applicable` exit the replay validated."""
    prompt = _implementer_prompt(leerie, monkeypatch, impl_env)
    line = next(ln for ln in prompt.splitlines()
                if ln.startswith("NOTE FROM ORCHESTRATOR:")
                and "whole test suite" in ln)
    assert "(`pytest`)" in line
    assert "denied to you" in line
    assert "not_applicable: true" in line
    assert "orchestrator's whole-tree pass owns this" in line


def test_no_notice_without_a_deny(leerie, monkeypatch, impl_env):
    repo = impl_env[0]
    (repo / ".leerie" / "config.toml").write_text('test = ""\n')
    prompt = _implementer_prompt(leerie, monkeypatch, impl_env)
    assert "whole test suite" not in prompt


# ---------------------------------------------------------------------------
# substance: a docs-only subtask never measures the canonical suite, and the
# conformer's self-reported tests verdict does not survive
# ---------------------------------------------------------------------------

def test_docs_only_subtask_defers_tests_end_to_end(leerie, monkeypatch,
                                                   tmp_path):
    repo = _repo(tmp_path, test_scoped="python3 -m pytest {test_files} -q")
    _git(["init", "-q", "-b", "main"], repo)
    _git(["config", "user.email", "t@t"], repo)
    _git(["config", "user.name", "t"], repo)
    (repo / "docs").mkdir()
    (repo / "docs" / "DESIGN.md").write_text("# design\n")
    _git(["add", "-A"], repo)
    _git(["commit", "-q", "-m", "init"], repo)
    run_id = "r-docs"
    run_branch = f"leerie/runs/{run_id}"
    _git(["checkout", "-q", "-b", run_branch], repo)
    run_dir = repo / ".leerie" / "runs" / run_id
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    sid = "docs-002"
    wt = run_dir / "worktrees" / sid
    _git(["worktree", "add", "-q", "-b", f"leerie/subtasks/{run_id}/{sid}",
          str(wt), run_branch], repo)
    (wt / "docs" / "DESIGN.md").write_text("# design\n\nnew section\n")
    _git(["add", "-A"], wt)
    _git(["commit", "-q", "-m", "docs only"], wt)
    (run_dir / "criteria" / f"{sid}.md").write_text("# Criteria\n- x\n")
    subtask = {"id": sid, "files_likely_touched": ["docs/DESIGN.md"]}
    (run_dir / "subtasks" / f"{sid}.json").write_text(json.dumps(subtask))
    st = leerie.State(repo / ".leerie", run_id, repo_root=repo)
    st.data = {"task": "x", "answers": {"source_of_truth": "codebase"},
               "subtask_tests": "scoped"}
    st.save()

    measured_cmds: list[str] = []

    async def fake_measure_axes(tree, axes, st_, caps, **kw):
        measured_cmds.extend(axes.values())
        return {a: {"ran": bool(c), "measured": bool(c), "passed": True,
                    "command": c, "summary": ""} for a, c in axes.items()}

    seen_blt: list = []

    async def fake_conformer(sid_, leerie_dir, worktree, caps, st_, models,
                             efforts, *, rules_files, diff_base,
                             extra_feedback=None, blt_results=None, **_kw):
        seen_blt.append(blt_results)
        # The conformer CLAIMS a full green suite it never ran.
        return {"subtask_id": sid_, "rules_files_read": [],
                "rule_violations_fixed": [], "rule_violations_residual": [],
                "docs_updates": [], "tests_updates": [],
                "build": {"ran": False, "passed": False, "command": "",
                          "summary": ""},
                "lint": {"ran": False, "passed": False, "command": "",
                         "summary": ""},
                "tests": {"ran": True, "passed": True, "command": "pytest",
                          "summary": "9373 passed"},
                "summary": "ok"}

    monkeypatch.setattr(leerie, "_measure_axes", fake_measure_axes)
    monkeypatch.setattr(leerie, "_run_conformer", fake_conformer)
    res, _warnings, _blocked = asyncio.run(leerie._run_conformance_phase(
        sid, run_dir, str(wt), subtask, dict(leerie.DEFAULT_CAPS), st,
        {w: "sonnet" for w in leerie.WORKER_TYPES}, {}))

    assert "pytest" not in measured_cmds, \
        "a docs-only diff must never measure the canonical suite"
    assert seen_blt and seen_blt[0]["tests"].get("deferred") is True
    section = leerie._format_blt_results_section(seen_blt[0], "deferred")
    assert "tests: deferred to the whole-tree pass" in section
    # The self-reported green suite did not survive the overwrite.
    assert res["tests"]["ran"] is False
    assert res["tests"]["deferred"] is True
    assert res["tests"]["command"] == ""


# ---------------------------------------------------------------------------
# wiring: the strip runs inside the real `_run_phases`, before `_schedule()`
# ---------------------------------------------------------------------------

def test_run_phases_strips_before_scheduling(leerie, monkeypatch, tmp_path):
    """Drives the real `_run_phases` from a `plans_after_filters` checkpoint
    through the real `_schedule()`, so the assertion reads what the spec
    files would carry -- not the helper in isolation."""
    from tests.test_resume_planning_reentry import (
        _StopAtExecute, _args, _caps, _plan, _stub_common, EFFORTS, MODELS)
    repo = _repo(tmp_path)
    leerie_root = tmp_path / ".leerie-state"
    run_id = "strip-wiring-aaa111"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    sub = {"id": "test-003", "depends_on": [], "requires": [],
           "provides": [], "runs_commands":
               ["pytest tests/", "pytest tests/test_no_undefined_names.py"]}
    plans = [_plan("testing", sub)]
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = leerie.State(leerie_root, run_id, repo_root=repo)
    st.data = {"task": "t", "worker_count": 3, "categories": ["testing"],
               "plans_after_classify": [],
               **{k: plans for k in (
                   "plans_after_plan", "plans_after_reconcile",
                   "plans_after_overlap_judge", "plans_after_adherence_gate",
                   "plans_after_coverage_gate", "plans_after_filters")}}
    st.save()
    with pytest.raises(_StopAtExecute):
        asyncio.run(leerie._run_phases(
            _args(), _caps(leerie), run_dir, st, "codebase", "normal",
            MODELS, EFFORTS))
    assert st.data["plan_snapshot"]["subtasks"]["test-003"]["runs_commands"] \
        == ["pytest tests/test_no_undefined_names.py"]
