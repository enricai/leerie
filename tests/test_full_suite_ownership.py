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

from tests.conftest import run_git_repo_first


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
# ONE rule list: the matcher must agree with what the CLI actually denied
# ---------------------------------------------------------------------------

# Every command here was run against the real CLI with the rules
# `_full_suite_denials` emits for a pytest repo with a `tests/` root (host
# claude 2.1.310 and the image's 2.1.302, `--dangerously-skip-permissions`),
# and the CLI's verdict recorded. The strip and the settle-time exemption
# must reach the SAME verdict, or a worker is required to run what it is
# denied -- the defect the first version shipped (`-k` and `cd &&` forms).
LIVE_PROBED = [
    ("pytest", True),
    ("pytest tests/", True),
    ("timeout 590 python3 -m pytest tests/ -q -x -n 4 2>&1 | tail -15", True),
    ("timeout 9 python3 -m pytest tests/ -q -x 2>&1 | tail -2", True),
    ("python3 -m pytest -q -p no:cacheprovider tests/ > /dev/null 2>&1; "
     "echo done4", True),
    ("cd . && pytest tests/ -q", True),
    ("pytest tests/ -k foo", True),
    ("pytest -m 'not slow' tests/", True),
    # Round 2: shapes the CLI denied that a split on operators alone missed.
    ("(cd sub && pytest tests/)", True),
    ("true && (pytest tests/ -q)", True),
    ("echo $(pytest tests/)", True),
    ("x=$(pytest tests/ -q); echo ok", True),
    ("pytest tests/>out.txt", True),
    ("pytest tests/<in.txt", True),
    ("(pytest tests/)", True),
    # Round 3: quoted brackets, separators and substitutions are literal to
    # the CLI; a backslash-newline continues the line; a brace GROUP is a
    # subcommand while brace EXPANSION is part of a word.
    ('pytest -k "not (slow)" tests/', True),
    ('pytest -m "(unit or fast)" tests/', True),
    ('pytest -k "{a}" tests/', True),
    ('pytest -k "$(cat k)" tests/', True),
    ("pytest \\\n  tests/", True),
    ('pytest -k "a|b" tests/', True),
    ('pytest -k "x;y" tests/', True),
    ('pytest --deselect "tests/test_a.py::t[1|2]" tests/', True),
    ("{ pytest tests/; }", True),
    ("if true; then pytest tests/; fi", True),
    ("pytest tests/{test_a,test_b}.py", False),
    ("pytest tests/test_a.py::test_x>out", False),
    ('pytest -k "a>b" tests/test_a.py', False),
    # Round 4: `$(...)` and backticks stay live inside double quotes; a
    # double-quote backslash escape keeps the quote open; a quoted redirect
    # target is skipped whole; `$(` glued to a word is no boundary of its
    # own (the `(` is).
    ('echo "$(pytest tests/)"', True),
    ('x="$(pytest tests/ -q)"; echo ok', True),
    ('out="$(pytest tests/ -q 2>&1)"; echo "$out" | tail -5', True),
    ('echo "`pytest tests/`"', True),
    ('echo "result: `pytest tests/ -q`"', True),
    ('pytest -k "a\\"|b" tests/', True),
    ('pytest -k "a\\"; echo \\"b" tests/', True),
    ('echo x > "a b"; pytest tests/; echo "c d"', True),
    ("pytest tests/$(echo)", False),
    # An unquoted substitution leaves the outer command whole too, and a
    # single-quoted `|` is literal.
    ("pytest $(cat k) tests/", True),
    ("pytest `cat k` tests/", True),
    ("echo 'a|pytest tests/'", False),
    ("python3 -m pytest tests/test_resolve_task_argument.py -q", False),
    ("python3 -m pytest -q -p no:cacheprovider tests/test_a.py "
     "tests/test_b.py", False),
    ("for f in a b; do python3 -m pytest -q tests/acc/test_$f.py; done",
     False),
    ("python3 -m pytest tests/test_x.py -q 2>&1 | tail -2", False),
    ("pytest -q", False),
]


@pytest.mark.parametrize("cmd,denied", LIVE_PROBED)
def test_matcher_agrees_with_the_live_cli(leerie, tmp_path, cmd, denied):
    pats = leerie._full_suite_rule_patterns(_repo(tmp_path))
    assert leerie._denied_by_full_suite_rules(cmd, pats) is denied


@pytest.mark.parametrize("cmd", [
    "run pytest tests/ to verify",
    "`pytest tests/`",
    "pytest tests/ && shellcheck x.sh",
    "pytest tests/ --lf",
    "pytest tests/ -k=foo",
    "pytest tests/ --deselect=tests/test_a.py::t",
    "nice -n 5 nohup pytest tests/",
])
def test_paraphrases_and_compounds_only_a_denied_run_could_satisfy(
        leerie, tmp_path, cmd):
    """Beyond the CLI: a declared paraphrase or compound the declared-command
    check would satisfy only with a denied run must be stripped too."""
    pats = leerie._full_suite_rule_patterns(_repo(tmp_path))
    assert leerie._denied_by_full_suite_rules(cmd, pats)


@pytest.mark.parametrize("cmd,denied", [
    ("pnpm run test", True),
    ("pnpm test", True),
    ("pnpm test flow-runner", False),
    ("pnpm test src/site-agnostic.test.ts", False),
    ("pnpm run lint", False),
])
def test_npm_family_matcher(leerie, tmp_path, cmd, denied):
    pats = leerie._full_suite_rule_patterns(
        _repo(tmp_path, test="pnpm run test", tests_dir=False))
    assert leerie._denied_by_full_suite_rules(cmd, pats) is denied


def test_the_emitted_rules_are_exactly_the_patterns(leerie, tmp_path):
    """The deny string and the matcher's patterns are one list."""
    root = _repo(tmp_path)
    assert leerie._full_suite_denials(root) == ",".join(
        f"Bash({p})" for p in leerie._full_suite_rule_patterns(root))


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

    def test_a_prescribed_whole_suite_command_lifts_strip_deny_and_notice(
            self, leerie, tmp_path):
        """An explicit user instruction outranks the default (DESIGN §9).
        Keeping the entry while still denying it would make the prescribed
        command impossible to run -- so the whole mechanism stands down."""
        root = _repo(tmp_path)
        prescribed = {"is_prescribed": True, "commands": ["pytest  tests/"]}
        plans = _incident_plans()
        assert leerie._strip_canonical_suite_commands(
            plans, root, prescribed) == []
        assert plans[0]["subtasks"][0]["runs_commands"][0] == "pytest tests/"
        assert leerie._full_suite_denials(root, prescribed) == ""
        assert leerie._full_suite_notice(root, prescribed) is None

    def test_an_unrelated_prescribed_command_keeps_the_deny(self, leerie,
                                                            tmp_path):
        root = _repo(tmp_path)
        prescribed = {"is_prescribed": True,
                      "commands": ["pytest tests/test_x.py"]}
        assert "Bash(pytest tests/ *)" in leerie._full_suite_denials(
            root, prescribed)

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

def _spawn(leerie, monkeypatch, repo_root, *, schema_key="implementer",
           allowed_tools=None, data=None):
    """`(deny value, user prompt sent over stdin)` from the REAL `claude_p`
    with only `_invoke` stubbed."""
    captured: dict = {}

    async def fake_invoke(cmd, cwd, timeout, sid, leerie_dir, verbosity,
                          stdin_data=None, **_kw):
        captured["cmd"] = list(cmd)
        captured["stdin"] = stdin_data
        return {"type": "result", "subtype": "success", "is_error": False,
                "result": "{}", "structured_output": {"categories": []}}

    st = types.SimpleNamespace(
        path=pathlib.Path("/tmp/leerie-test-nonexistent/state.json"),
        run_dir=pathlib.Path("/tmp/leerie-test-nonexistent"),
        repo_root=repo_root, data={"verbosity": "quiet", **(data or {})},
        run_id="r1", bump_workers=lambda *a, **k: None,
        add_telemetry=lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_invoke", fake_invoke)
    monkeypatch.setattr(leerie, "_capture_call", lambda *a, **k: None)
    monkeypatch.setattr(
        leerie, "_append_system_prompt_file_supported", lambda: False)
    asyncio.run(leerie.claude_p(
        "u", "s", schema_key=schema_key,
        cwd="/leerie-state/runs/r1/worktrees/w",
        allowed_tools=allowed_tools or leerie.ACT_TOOLS,
        max_turns=40, autonomous=True, caps=dict(leerie.DEFAULT_CAPS),
        st=st, model="sonnet", sid="t"))
    argv = captured["cmd"]
    return argv[argv.index("--disallowedTools") + 1], captured["stdin"]


def _argv_for(leerie, monkeypatch, repo_root):
    return _spawn(leerie, monkeypatch, repo_root)[0]


def test_the_deny_reaches_the_argv(leerie, monkeypatch, tmp_path):
    deny = _argv_for(leerie, monkeypatch, _repo(tmp_path))
    assert ",Bash(pytest tests/ *)," in deny
    assert deny.startswith("Agent,Task,"), "base denials must be preserved"


def test_no_deny_when_the_repo_has_no_test_command(leerie, monkeypatch,
                                                   tmp_path):
    deny = _argv_for(leerie, monkeypatch, _repo(tmp_path, test=""))
    assert "Bash(" not in deny


def _notice_lines(prompt):
    return [ln for ln in prompt.splitlines()
            if ln.startswith("NOTE FROM ORCHESTRATOR:")
            and "whole test suite" in ln]


@pytest.mark.parametrize("schema_key", [
    "implementer", "conformer", "integrator", "acceptance_writer",
    "rebaser"])
def test_every_bash_capable_worker_gets_deny_and_notice_together(
        leerie, monkeypatch, tmp_path, schema_key):
    """Decided once in `claude_p`, so no caller can send the deny without
    its reason. Behavioural probe on the prose, not a bare substring: the
    line names the canonical command, says it is denied, and gives the exact
    `not_applicable` exit the replay validated."""
    deny, prompt = _spawn(leerie, monkeypatch, _repo(tmp_path),
                          schema_key=schema_key)
    assert "Bash(pytest tests/ *)" in deny
    (line,) = _notice_lines(prompt)
    assert "(`pytest`)" in line
    assert "denied to you" in line
    assert "not_applicable: true" in line
    assert "orchestrator's whole-tree pass owns this" in line
    assert "once every wave has merged" in line


def test_a_worker_that_cannot_reach_the_runner_gets_no_notice(
        leerie, monkeypatch, tmp_path):
    """Judgment workers hold a fixed verb allowlist with no runner: the deny
    is inert for them and the notice would be noise."""
    _deny, prompt = _spawn(leerie, monkeypatch, _repo(tmp_path),
                           schema_key="classifier",
                           allowed_tools=leerie.INSPECT_TOOLS)
    assert _notice_lines(prompt) == []


def test_a_widened_runner_grant_does_get_the_notice(leerie, monkeypatch,
                                                    tmp_path):
    _deny, prompt = _spawn(
        leerie, monkeypatch, _repo(tmp_path), schema_key="classifier",
        allowed_tools=leerie.INSPECT_TOOLS + ",Bash(pytest:*)")
    assert len(_notice_lines(prompt)) == 1


def test_no_deny_means_no_notice(leerie, monkeypatch, tmp_path):
    deny, prompt = _spawn(leerie, monkeypatch, _repo(tmp_path, test=""))
    assert "Bash(" not in deny
    assert _notice_lines(prompt) == []


def test_a_prescribed_whole_suite_lifts_both_at_spawn(leerie, monkeypatch,
                                                      tmp_path):
    deny, prompt = _spawn(
        leerie, monkeypatch, _repo(tmp_path),
        data={"prescribed_procedure": {"is_prescribed": True,
                                       "commands": ["pytest tests/"]}})
    assert "Bash(pytest" not in deny
    assert _notice_lines(prompt) == []


# ---------------------------------------------------------------------------
# substance: a docs-only subtask never measures the canonical suite, and the
# conformer's self-reported tests verdict does not survive
# ---------------------------------------------------------------------------

def test_docs_only_subtask_defers_tests_end_to_end(leerie, monkeypatch,
                                                   tmp_path):
    repo = _repo(tmp_path, test_scoped="python3 -m pytest {test_files} -q")
    run_git_repo_first(repo, "init", "-q", "-b", "main")
    run_git_repo_first(repo, "config", "user.email", "t@t")
    run_git_repo_first(repo, "config", "user.name", "t")
    (repo / "docs").mkdir()
    (repo / "docs" / "DESIGN.md").write_text("# design\n")
    run_git_repo_first(repo, "add", "-A")
    run_git_repo_first(repo, "commit", "-q", "-m", "init")
    run_id = "r-docs"
    run_branch = f"leerie/runs/{run_id}"
    run_git_repo_first(repo, "checkout", "-q", "-b", run_branch)
    run_dir = repo / ".leerie" / "runs" / run_id
    for d in ("subtasks", "criteria", "logs", "worktrees"):
        (run_dir / d).mkdir(parents=True)
    sid = "docs-002"
    wt = run_dir / "worktrees" / sid
    run_git_repo_first(repo, "worktree", "add", "-q", "-b",
                       f"leerie/subtasks/{run_id}/{sid}", str(wt), run_branch)
    (wt / "docs" / "DESIGN.md").write_text("# design\n\nnew section\n")
    run_git_repo_first(wt, "add", "-A")
    run_git_repo_first(wt, "commit", "-q", "-m", "docs only")
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


# ---------------------------------------------------------------------------
# hostile repo files: the deny runs inside EVERY claude_p call, so it must
# never raise -- a raise here would kill every worker spawn
# ---------------------------------------------------------------------------

class TestHostileRepoFiles:
    @pytest.mark.parametrize("name,content", [
        ("pytest.ini", b"[pytest]\ntestpaths = tests\n# \xff\xfe\n"),
        ("pytest.ini", b"[pytest]\ntestpaths = 100%tests\n"),
        ("setup.cfg", b"[tool:pytest]\ntestpaths = %(x)s\n"),
        ("tox.ini", b"[pytest\ntestpaths = tests\n"),
    ])
    def test_unreadable_or_odd_ini_never_raises(self, leerie, tmp_path,
                                                name, content):
        root = _repo(tmp_path)
        (root / name).write_bytes(content)
        rules = leerie._full_suite_denials(root)
        assert "Bash(pytest)" in rules, "falls back to the default root"
        assert leerie._full_suite_notice(root)

    def test_a_percent_in_testpaths_is_literal(self, leerie, tmp_path):
        root = _repo(tmp_path, tests_dir=False,
                     ini="[pytest]\ntestpaths = 100%tests\n")
        assert "Bash(pytest 100%tests/ *)" in leerie._full_suite_denials(root)

    def test_a_root_that_would_corrupt_the_rule_list_is_dropped(
            self, leerie, tmp_path):
        root = _repo(tmp_path, tests_dir=False,
                     ini="[pytest]\ntestpaths = a,b c(d) e\n")
        rules = leerie._full_suite_denials(root)
        assert "Bash(pytest e/ *)" in rules
        assert "a,b" not in rules and "c(d)" not in rules

    def test_a_derivation_failure_degrades_to_no_deny(self, leerie, tmp_path,
                                                      monkeypatch):
        def boom(_root):
            raise OSError("unreadable checkout")
        monkeypatch.setattr(leerie, "_canonical_test_command", boom)
        monkeypatch.setattr(leerie, "_full_suite_deny_skip_warned", False)
        root = _repo(tmp_path)
        assert leerie._full_suite_denials(root) == ""
        assert leerie._full_suite_notice(root) is None


# ---------------------------------------------------------------------------
# settle-time exemption: the choke point that also covers specs written
# before the strip existed (a resume past `plan_snapshot`)
# ---------------------------------------------------------------------------

from tests.test_declared_commands_executed import (  # noqa: E402
    _COMPLETE_RES_EXTRA, _declare, _settle, _write_worker_log)
from tests.test_oom_naming import env  # noqa: E402,F401  (pytest fixture)


def _settle_env(env, monkeypatch, *, declared, prescribed=None):  # noqa: F811
    leerie_mod = env["leerie"]
    repo = env["repo"]
    (repo / ".leerie").mkdir(exist_ok=True)
    (repo / ".leerie" / "config.toml").write_text('test = "pytest"\n')
    (repo / "tests").mkdir(exist_ok=True)
    env["st"].repo_root = repo
    if prescribed is not None:
        env["st"].data["prescribed_procedure"] = prescribed
    _declare(env, declared)
    _write_worker_log(env["run_dir"] / "logs" / f"{env['sid']}.log",
                      ["python3 -m pytest tests/test_x.py -q"])
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
    res = _settle(leerie_mod, env, failed_retries=0,
                  implementer_confidence_retries=1)
    return res, calls


def test_settle_exempts_a_denied_declared_command(
        env, monkeypatch):  # noqa: F811
    """An OLD spec still declaring `pytest tests/` (never stripped) settles
    complete on targeted runs alone, with no corrective re-drive."""
    res, calls = _settle_env(
        env, monkeypatch,
        declared=["pytest tests/", "python3 -m pytest tests/test_x.py -q"])
    assert res["status"] == "complete"
    assert len(calls) == 1


def test_settle_still_demands_a_prescribed_whole_suite_run(
        env, monkeypatch):  # noqa: F811
    """Control: when the user PRESCRIBED the whole suite there is no deny,
    so there is no exemption -- an unrun prescribed command still blocks."""
    res, calls = _settle_env(
        env, monkeypatch, declared=["pytest tests/"],
        prescribed={"is_prescribed": True, "commands": ["pytest tests/"]})
    assert res["status"] == "blocked"
    assert "pytest tests/" in res["blocker"]
    assert len(calls) == 2


# ---------------------------------------------------------------------------
# remaining components, each pinned so its removal is visible
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("mode", ["full", "off"])
def test_nothing_is_deferred_outside_scoped(leerie, mode):
    blt = {"build": "make", "lint": "ruff", "test": "pytest"}
    axes, _ = leerie._select_subtask_axes(blt, {}, ["a.py"], "base", mode)
    assert leerie._deferred_axes(axes, blt, mode) == []


def test_build_without_a_proxy_keeps_its_canonical_command(leerie):
    """Only `tests` is deferred: a compiled repo keeps its per-subtask
    compile signal (DESIGN §9)."""
    blt = {"build": "go build ./...", "lint": "", "test": "go test ./..."}
    axes, label = leerie._select_subtask_axes(blt, {}, ["a.go"], "base",
                                              "scoped")
    assert axes["build"] == "go build ./..."
    assert "tests" not in axes
    assert leerie._deferred_axes(axes, blt, "scoped") == ["tests"]
    assert label == "deferred"


def test_the_strip_never_raises_on_a_malformed_plan(leerie, tmp_path):
    root = _repo(tmp_path)
    assert leerie._strip_canonical_suite_commands(
        [None], root, {}) == []
    assert leerie._strip_canonical_suite_commands(
        [{"subtasks": [{"id": "x", "runs_commands": "pytest tests/"}]}],
        root, {}) == []


# ---------------------------------------------------------------------------
# round 2: anchoring for the prescribed check, the is_prescribed gate, and
# newline-separated declared commands
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("cmd", [
    "pytest tests/{test_a,test_b}.py",                  # CLI: ran
    "timeout 60 pytest tests/test_a.py",
    "pip show pytest",                                  # CLI: ran
    "python3 -m pytest tests/test_a.py -k pytest",      # CLI: ran
    "pip install pytest",
    "which pytest",
])
def test_anchored_match_ignores_a_runner_word_that_is_not_a_run(
        leerie, tmp_path, cmd):
    pats = leerie._full_suite_rule_patterns(_repo(tmp_path))
    assert not leerie._denied_by_full_suite_rules(cmd, pats, anchored=True)


@pytest.mark.parametrize("cmd", [
    "pytest tests/", "timeout 5 nohup pytest tests/ -q", "(pytest tests/)",
    # Round 3: prefixes the CLI strips, each denied live in prefixed form.
    "PYTHONPATH=. pytest tests/", "env X=1 pytest tests/",
    "FOO=1 pytest tests/", "timeout -s KILL 60 pytest tests/",
    "timeout --signal KILL 50 pytest tests/",
    "if true; then pytest tests/; fi", "{ pytest tests/; }",
    'pytest -k "not (slow)" tests/', "pytest \\\n  tests/",
    # Round 4: a quoted assignment value with a space, and `env -u NAME`.
    'PYTEST_ADDOPTS="-x -q" pytest tests/',
    'PYTHONPATH="src lib" pytest tests/', "env -u HOME pytest tests/"])
def test_anchored_match_still_sees_a_whole_suite_run(leerie, tmp_path, cmd):
    pats = leerie._full_suite_rule_patterns(_repo(tmp_path))
    assert leerie._denied_by_full_suite_rules(cmd, pats, anchored=True)


@pytest.mark.parametrize("prescribed", [
    {"is_prescribed": True, "commands": ["pip install pytest"]},
    {"is_prescribed": True,
     "commands": ["python3 -m pytest tests/test_a.py -k pytest"]},
    {"is_prescribed": False, "commands": ["pytest tests/"]},
    {"commands": ["pytest tests/"]},
    # Round 2 (claims review): the unanchored match read the suffix
    # `pytest on the touched tests` as `pytest * tests`.
    {"is_prescribed": True, "commands": ["run pytest on the touched tests"]},
])
def test_only_a_prescribed_whole_suite_run_lifts_the_deny(leerie, tmp_path,
                                                          prescribed):
    """A drifted classifier (commands filled while `is_prescribed` is false)
    or a prescribed command that merely names the runner must not switch
    the mechanism off for the whole run."""
    assert "Bash(pytest tests/ *)" in leerie._full_suite_denials(
        _repo(tmp_path), prescribed)


def test_newline_separated_commands_are_separate_segments(leerie, tmp_path):
    pats = leerie._full_suite_rule_patterns(_repo(tmp_path))
    assert not leerie._denied_by_full_suite_rules(
        "python3 -m pytest tests/test_x.py -q\nls tests", pats)
    assert leerie._denied_by_full_suite_rules(
        "ls\npytest tests/ -q", pats)


def test_the_strip_skips_malformed_entries_and_keeps_going(leerie, tmp_path):
    plans = [None, {"subtasks": [None, {"id": "t-1",
                                         "runs_commands": ["pytest tests/"]}]}]
    assert leerie._strip_canonical_suite_commands(
        plans, _repo(tmp_path), {}) == ["t-1"]
    assert plans[1]["subtasks"][1]["runs_commands"] == []


def test_a_sole_trailing_wildcard_also_matches_the_bare_command(leerie):
    """The CLI's documented glob rule (`Bash(ls *)` matches `ls`), mirrored
    for any pattern -- not only ones that happen to have an exact sibling."""
    assert leerie._rule_matches("pnpm test", "pnpm test *")
    assert not leerie._rule_matches("pnpm testx", "pnpm test *")
    assert not leerie._rule_matches("pytest", "* --help *")


def test_testpaths_are_read_as_utf8_whatever_the_locale(leerie, tmp_path,
                                                        monkeypatch):
    """`configparser` otherwise decodes with the locale's encoding; a
    non-ASCII root must not turn into mojibake on a non-UTF-8 host."""
    import configparser
    monkeypatch.setattr(configparser.io, "text_encoding",
                        lambda enc, *a: enc or "latin-1")
    root = _repo(tmp_path, tests_dir=False, ini="")
    (root / "pytest.ini").write_bytes(
        "[pytest]\ntestpaths = tëst\n".encode("utf-8"))
    assert leerie._test_roots(root) == ["tëst"]


def test_the_strip_survives_a_non_iterable_plan_list(leerie, tmp_path):
    """The catch-all is for what the per-entry skips cannot see: the strip
    runs on the scheduling path and must never take it down."""
    assert leerie._strip_canonical_suite_commands(
        None, _repo(tmp_path), {}) == []


# ---------------------------------------------------------------------------
# round 3: a prescribed command is never stripped or exempted, so the two
# match modes cannot disagree about it silently
# ---------------------------------------------------------------------------

def test_a_prescribed_prefixed_suite_run_lifts_the_deny(leerie, tmp_path):
    prescribed = {"is_prescribed": True,
                  "commands": ["PYTHONPATH=. pytest tests/"]}
    assert leerie._full_suite_denials(_repo(tmp_path), prescribed) == ""


def test_a_prescribed_command_the_wide_match_would_strip_is_kept(
        leerie, tmp_path):
    """`run pytest on the touched tests` reads as `pytest * tests` to the
    wide match but is no suite run to the anchored one, so the deny stays --
    and the entry must stay too: dropping it would discard the user's
    instruction without a word."""
    cmd = "run pytest on the touched tests"
    prescribed = {"is_prescribed": True, "commands": [cmd]}
    root = _repo(tmp_path)
    assert leerie._full_suite_denials(root, prescribed)
    plans = [{"subtasks": [{"id": "t-1", "runs_commands": [
        cmd, "pytest tests/"]}]}]
    assert leerie._strip_canonical_suite_commands(
        plans, root, prescribed) == ["t-1"]
    assert plans[0]["subtasks"][0]["runs_commands"] == [cmd]


def test_settle_keeps_requiring_a_prescribed_command(
        env, monkeypatch):  # noqa: F811
    """Settle-side twin: the exemption must not drop a prescribed entry the
    wide match would refuse -- the subtask ends `blocked`, loudly, rather
    than `complete` without the user's command ever running."""
    cmd = "run pytest on the touched tests"
    res, calls = _settle_env(
        env, monkeypatch, declared=[cmd],
        prescribed={"is_prescribed": True, "commands": [cmd]})
    assert res["status"] == "blocked"
    assert cmd in res["blocker"]


def test_a_paraphrased_prescribed_command_is_resynthesised(
        leerie, monkeypatch, tmp_path):
    """Round 4: the planner covered a prescribed `uv run pytest tests/` with a
    paraphrase (`… -q`); the paraphrase is not immune, so the strip removed
    the only entry carrying the user's command. `_run_phases` re-runs the
    adherence floor's repair, which synthesises the EXACT command (immune)."""
    from tests.test_resume_planning_reentry import (
        _StopAtExecute, _args, _caps, _plan, _stub_common, EFFORTS, MODELS)
    repo = _repo(tmp_path)
    leerie_root = tmp_path / ".leerie-state"
    run_id = "strip-resynth-aaa111"
    run_dir = leerie_root / "runs" / run_id
    (run_dir / "subtasks").mkdir(parents=True)
    sub = {"id": "test-001", "depends_on": [], "requires": [],
           "provides": [], "runs_commands": ["uv run pytest tests/ -q"]}
    plans = [_plan("testing", sub)]
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)
    st = leerie.State(leerie_root, run_id, repo_root=repo)
    st.data = {"task": "t", "worker_count": 3, "categories": ["testing"],
               "prescribed_procedure": {"is_prescribed": True,
                                        "commands": ["uv run pytest tests/"]},
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
    snap = st.data["plan_snapshot"]["subtasks"]
    carriers = [sid for sid, s in snap.items()
                if "uv run pytest tests/" in (s.get("runs_commands") or [])]
    assert carriers, "the exact prescribed command must survive scheduling"
    assert leerie._full_suite_denials(
        repo, st.data["prescribed_procedure"]), "and the deny stays on"
