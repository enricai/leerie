"""Execution coverage for the finalize-region helpers that were previously
pinned only via `inspect.getsource` (see e.g. test_phase_finalize_*.py,
test_orchestrate_call_sites.py): `_compose_pr_via_llm`, `phase_finalize`
and `_orchestrate` itself were never actually *called* anywhere in the
suite — every existing test asserts a call site or a branch condition
exists in source text, never that the branch runs and does the right
thing.

This file drives all three end to end against a real git repo and a real
`State`, stubbing only the genuinely external edges (`claude_p`,
`_run_script`, `run_proc`'s `git show-ref` verification, `capture_repo_deps`)
so the surrounding orchestration logic — payload assembly, truncation,
budget gating, log-line branches, the strict-output-proxy summary — runs
for real.
"""
from __future__ import annotations

import asyncio
import json
import subprocess
import types

import pytest


def _run(cmd, cwd, check=True):
    r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True)
    if check:
        assert r.returncode == 0, f"{cmd} failed in {cwd}: {r.stderr}"
    return r


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    _run(["git", "init", "-q", "-b", "main"], root)
    _run(["git", "config", "user.email", "t@t"], root)
    _run(["git", "config", "user.name", "t"], root)
    (root / "README.md").write_text("hi\n")
    _run(["git", "add", "-A"], root)
    _run(["git", "commit", "-qm", "init"], root)
    return root


@pytest.fixture
def st(leerie, repo, tmp_path):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / "r1").mkdir(parents=True)
    s = leerie.State(leerie_root, "r1", repo_root=repo)
    yield s
    s.release_lock()


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["max_total_workers"] = 100
    return caps


# ------------------------------------------------------------------
# _compose_pr_via_llm
# ------------------------------------------------------------------

def test_compose_pr_via_llm_happy_path(leerie, monkeypatch, st, repo, tmp_path):
    st.data.update({
        "working_branch": "main",
        "started_at": "2026-01-01T00:00:00+00:00",
        "worker_count": 3,
        "leerie_version": "1.2.3",
        "conformance": {
            "_final": {"result": {
                "rule_violations_residual": [{"rule": "x", "why_not_fixed": "y"}],
                "build": {"ran": True, "passed": False, "command": "make",
                          "summary": "broke"},
            }, "warnings": ["a warning"]},
            "_baseline": {"axes": {
                "build": {"ran": True, "measured": True, "passed": True},
                "lint": {"ran": True, "measured": True, "passed": True},
                "tests": {"ran": True, "measured": True, "passed": True},
            }},
        },
        "external_preconditions": ["deploy api first"],
    })
    (st.run_dir / "plan.json").write_text(json.dumps(
        {"subtasks": {"feat-001": {"title": "Add the thing"}}}))

    async def _fake_claude_p(**kwargs):
        assert kwargs["schema_key"] == "pr_writer"
        payload = json.loads(kwargs["user_prompt"])
        assert payload["subtask_titles"] == ["Add the thing"]
        assert payload["final_conformance"]["residuals"]
        assert payload["base_health"]["base_status"] == "green"
        assert payload["external_preconditions"] == ["deploy api first"]
        return {"title": "leerie: Add the thing", "body": "the body",
                "used_template": None}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)

    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, None))

    run_json = json.loads((st.run_dir / "run.json").read_text())
    # The leading "leerie: " must be stripped (DESIGN §12 prompts-advisory).
    assert run_json["pr_title"] == "Add the thing"
    assert run_json["pr_body"] == "the body"


def test_compose_pr_via_llm_budget_exhausted_skips(leerie, monkeypatch, st, repo):
    st.data["working_branch"] = "main"
    st.data["worker_count"] = 100
    caps = _caps(leerie)
    caps["max_total_workers"] = 100

    called = False

    async def _fake_claude_p(**kwargs):
        nonlocal called
        called = True
        return {"title": "t", "body": "b"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(st, caps, {}, {}, repo, None))
    assert not called
    assert not (st.run_dir / "run.json").exists() or \
        "pr_title" not in json.loads((st.run_dir / "run.json").read_text() or "{}")


def test_compose_pr_via_llm_empty_result_skips_write(leerie, monkeypatch, st, repo):
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        return {"title": "", "body": ""}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(st, _caps(leerie), {}, {}, repo, None))
    assert not (st.run_dir / "run.json").exists()


def test_compose_pr_via_llm_swallows_worker_exception(leerie, monkeypatch, st,
                                                       repo):
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    # Must not raise — fail-open contract.
    asyncio.run(leerie._compose_pr_via_llm(st, _caps(leerie), {}, {}, repo, None))
    assert not (st.run_dir / "run.json").exists()


def test_compose_pr_via_llm_malformed_plan_json_is_tolerated(
        leerie, monkeypatch, st, repo):
    st.data["working_branch"] = "main"
    (st.run_dir / "plan.json").write_text("{not json")

    async def _fake_claude_p(**kwargs):
        payload = json.loads(kwargs["user_prompt"])
        assert payload["subtask_titles"] == []
        return {"title": "t", "body": "b"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(st, _caps(leerie), {}, {}, repo, None))
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["pr_title"] == "t"


def test_compose_pr_via_llm_template_read_failure_falls_back(
        leerie, monkeypatch, st, repo, tmp_path):
    st.data["working_branch"] = "main"

    missing = tmp_path / "does-not-exist" / "TEMPLATE.md"
    monkeypatch.setattr(
        leerie, "_find_pr_template",
        lambda repo_root, override: (missing, "TEMPLATE.md"))

    async def _fake_claude_p(**kwargs):
        payload = json.loads(kwargs["user_prompt"])
        assert payload["template"] is None
        return {"title": "t", "body": "b"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(st, _caps(leerie), {}, {}, repo, None))
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["pr_title"] == "t"


def test_compose_pr_via_llm_reads_a_real_template(
        leerie, monkeypatch, st, repo, tmp_path):
    """The successful `tpl_path.read_text()` branch (as opposed to the
    OSError fallback above)."""
    tpl_path = tmp_path / "TEMPLATE.md"
    tpl_path.write_text("## Summary\n")
    monkeypatch.setattr(
        leerie, "_find_pr_template",
        lambda repo_root, override: (tpl_path, "TEMPLATE.md"))
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        payload = json.loads(kwargs["user_prompt"])
        assert payload["template"]["content"] == "## Summary\n"
        assert payload["template"]["truncated"] is False
        return {"title": "t", "body": "b", "used_template": "TEMPLATE.md"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(st, _caps(leerie), {}, {}, repo, None))
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["pr_template_used"] == "TEMPLATE.md"


def test_compose_pr_via_llm_warns_on_unmatched_template_override(
        leerie, monkeypatch, st, repo, capfd):
    """--pr-template=<name> that matches nothing must log a warning but
    still proceed in no-template mode."""
    monkeypatch.setattr(
        leerie, "_find_pr_template", lambda repo_root, override: None)
    st.data["working_branch"] = "main"

    async def _fake_claude_p(**kwargs):
        return {"title": "t", "body": "b"}

    monkeypatch.setattr(leerie, "claude_p", _fake_claude_p)
    asyncio.run(leerie._compose_pr_via_llm(
        st, _caps(leerie), {}, {}, repo, "nonexistent-template"))
    out = capfd.readouterr().out
    assert "did not match any template" in out


# ------------------------------------------------------------------
# _final_conformance_payload — both lists truncated
# ------------------------------------------------------------------

def test_final_conformance_payload_trims_both_lists_when_oversized(leerie):
    st_stub = types.SimpleNamespace(data={"conformance": {"_final": {
        "result": {
            "rule_violations_residual": [
                {"rule": f"rule-{i}", "why_not_fixed": "x" * 500}
                for i in range(30)
            ],
        },
        "warnings": [f"warning {i}: " + ("y" * 500) for i in range(30)],
    }}})
    out = leerie._final_conformance_payload(st_stub)
    assert out is not None
    assert out["truncated"] is True
    # Both lists were trimmed from their original length of 30.
    assert len(out["residuals"]) < 30
    assert len(out["warnings"]) < 30
    # At least one element survives in each so the worker still sees drift.
    assert len(out["residuals"]) >= 1
    assert len(out["warnings"]) >= 1
    size = len(json.dumps(out, separators=(",", ":")).encode("utf-8"))
    assert size <= leerie.PR_WRITER_FINAL_CONFORMANCE_MAX_BYTES + 200


# ------------------------------------------------------------------
# _record_run_health — malformed / unreadable inputs
# ------------------------------------------------------------------

def test_record_run_health_skips_malformed_log_lines(leerie, tmp_path):
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "feat-001.log").write_text(
        'not json but contains "result"\n'
        '{"type": "other", "result": "x"}\n'
        '{"type": "result", "duration_ms": 5000}\n')
    st_stub = types.SimpleNamespace(run_dir=run_dir, data={})
    leerie._record_run_health(st_stub)
    health = json.loads((run_dir / "run.json").read_text())["health"]
    assert health["slowest_worker_sid"] == "feat-001"
    assert health["truncated_worker_count"] == 0


def test_record_run_health_skips_lines_with_no_result_marker(leerie, tmp_path):
    """The cheap pre-filter (`'"result"' not in line`) must skip a line
    that never mentions "result" at all, distinct from the
    malformed-JSON-but-contains-"result" case above."""
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "feat-001.log").write_text(
        "an unrelated stream-json event line\n"
        + json.dumps({"type": "result", "duration_ms": 2000}) + "\n")
    st_stub = types.SimpleNamespace(run_dir=run_dir, data={})
    leerie._record_run_health(st_stub)
    health = json.loads((run_dir / "run.json").read_text())["health"]
    assert health["slowest_worker_min"] == round(2000 / 60000.0, 1)


def test_record_run_health_tolerates_unreadable_log_file(leerie, tmp_path):
    """A per-worker log that raises OSError on open (e.g. a directory
    named `*.log` from a prior crash) must be skipped, not blow up the
    whole sweep."""
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "bad.log").mkdir()  # a directory, not a file -> IsADirectoryError
    (logs / "feat-001.log").write_text(
        json.dumps({"type": "result", "duration_ms": 3000}) + "\n")
    st_stub = types.SimpleNamespace(run_dir=run_dir, data={})
    leerie._record_run_health(st_stub)  # must not raise
    health = json.loads((run_dir / "run.json").read_text())["health"]
    assert health["slowest_worker_sid"] == "feat-001"


def test_record_run_health_tolerates_unreadable_sidecar(leerie, tmp_path):
    """A run.json that exists but isn't valid JSON must not blow up the
    base_suite-preservation read; it degrades to an empty existing dict."""
    run_dir = tmp_path / "run"
    logs = run_dir / "logs"
    logs.mkdir(parents=True)
    (logs / "feat-001.log").write_text(
        json.dumps({"type": "result", "duration_ms": 1000}) + "\n")
    (run_dir / "run.json").write_text("not json")
    st_stub = types.SimpleNamespace(run_dir=run_dir, data={})
    leerie._record_run_health(st_stub)  # must not raise
    health = json.loads((run_dir / "run.json").read_text())["health"]
    assert "base_suite" not in health
    assert health["slowest_worker_sid"] == "feat-001"


# ------------------------------------------------------------------
# phase_finalize — full execution
# ------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _stub_finalize_subprocesses(leerie, monkeypatch):
    """phase_finalize shells out to finalize.sh/cleanup.sh and verifies the
    run branch via `git show-ref`. None of that is this subtask's concern —
    stub the process boundary so the surrounding Python logic runs for
    real."""
    async def _fake_run_script(name, *args):
        return subprocess.CompletedProcess(["bash", name, *args], 0, "", "")

    async def _fake_run_proc(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(leerie, "_run_script", _fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)

    async def _noop_capture(*a, **k):
        return None

    monkeypatch.setattr(leerie, "capture_repo_deps", _noop_capture)


def _finalize_ready_state(st):
    st.data["waves"] = [["feat-001"]]
    st.data["completed_waves"] = 1
    st.data["worker_count"] = 2
    st.data["subtask_status"] = {"feat-001": "complete"}
    st.data["working_branch"] = "main"


@pytest.fixture(scope="session")
def pristine_run_proc(leerie):
    """leerie's genuine `run_proc`, captured before this module's autouse
    stub can replace it.

    Session-scoped on purpose: higher-scoped fixtures are instantiated
    before the function-scoped autouse `_stub_finalize_subprocesses`, so
    this sees the real attribute. A test that wants the real git call back
    re-installs this over the stub.
    """
    return leerie.run_proc


class TestEmptyRunBranchAgainstRealGit:
    """The same disposition as `TestEmptyRunBranchIsNoWorkNotAnError`, but
    with the REAL `git rev-list` running against a REAL repository.

    That distinction is the whole point of this class. The sibling class
    stubs `run_proc` wholesale, so the git command never executes — and the
    command is precisely where the defect lived: the guard originally ran in
    `leerie_dir` (the STATE root, not a git repo at all), which made it fail
    open and never fire, except when the state root happened to sit inside
    the repo. No stubbed test can catch that, because a stub answers
    regardless of the directory it was asked from.
    """

    @staticmethod
    def _make_run_branch(repo, ahead: int):
        """Create `leerie/runs/r1` either equal to main or `ahead` commits
        past it, using real git."""
        _run(["git", "branch", "-f", "leerie/runs/r1", "main"], repo)
        for i in range(ahead):
            _run(["git", "checkout", "-q", "leerie/runs/r1"], repo)
            (repo / f"landed{i}.txt").write_text(f"work {i}\n")
            _run(["git", "add", "-A"], repo)
            _run(["git", "commit", "-qm", f"work {i}"], repo)
            _run(["git", "checkout", "-q", "main"], repo)

    @staticmethod
    def _install_real_git(monkeypatch, leerie, pristine_run_proc, scripts,
                          repo):
        """Real `run_proc`; only the shell scripts stay stubbed.

        `chdir(repo)` mirrors production: leerie runs with the process cwd
        set to the target repo, which is what `_run_script` relies on
        (`cwd=os.getcwd()`) and what phase_finalize's post-cleanup
        `git show-ref` — which passes no explicit cwd — resolves against.
        Without it the real git calls would resolve against pytest's own
        cwd and fail for reasons that have nothing to do with the guard.
        """
        monkeypatch.chdir(repo)

        async def _fake_run_script(name, *args):
            scripts.append(name)
            return subprocess.CompletedProcess(["bash", name], 0, "", "")

        async def _fake_compose(*a, **k):
            return None

        monkeypatch.setattr(leerie, "run_proc", pristine_run_proc)
        monkeypatch.setattr(leerie, "_run_script", _fake_run_script)
        monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

    def test_real_empty_branch_routes_to_no_work(
            self, leerie, monkeypatch, st, repo, pristine_run_proc):
        self._make_run_branch(repo, ahead=0)
        scripts = []
        self._install_real_git(monkeypatch, leerie, pristine_run_proc,
                               scripts, repo)
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert st.data["current_phase"] == "done: no work required"
        assert st.data["no_work_required"] is True
        assert "finalize.sh" not in scripts
        assert "cleanup.sh" in scripts

    def test_real_nonempty_branch_finalizes_normally(
            self, leerie, monkeypatch, st, repo, pristine_run_proc):
        """The dangerous direction: a false positive here would discard a
        completed run as 'no work required'."""
        self._make_run_branch(repo, ahead=2)
        scripts = []
        self._install_real_git(monkeypatch, leerie, pristine_run_proc,
                               scripts, repo)
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert st.data["current_phase"] == "phase 6: finalize"
        assert not st.data.get("no_work_required")
        assert "finalize.sh" in scripts

    def test_guard_reads_the_repo_not_the_state_root(
            self, leerie, monkeypatch, st, repo, pristine_run_proc):
        """Pins the cwd fix directly. `st.leerie_root` is a plain directory
        under tmp_path with no `.git`, so a guard that ran there would get a
        non-zero returncode, fail open, and reach `finalize.sh` even though
        the branch is genuinely empty. Asserting the no-work disposition on
        an empty branch therefore proves the count was taken in the repo.
        """
        self._make_run_branch(repo, ahead=0)
        assert not (st.leerie_root / ".git").exists(), (
            "precondition: the state root must NOT be a git repo, or this "
            "test cannot distinguish the two directories")
        scripts = []
        self._install_real_git(monkeypatch, leerie, pristine_run_proc,
                               scripts, repo)
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert st.data["no_work_required"] is True, (
            "guard did not see the empty branch — it likely ran outside "
            "st.repo_root and failed open")


class TestEmptyRunBranchIsNoWorkNotAnError:
    """Corpus runs `d1987e8b` ($9.68) and `05f65221` ($3.03) reached finalize
    with every subtask `complete` and a run branch carrying no commits, and
    died on `finalize.sh`'s "has no commits beyond <base> — nothing to push".
    That is the cleared-but-empty terminal state, not a failure."""

    @staticmethod
    def _ahead(monkeypatch, leerie, count, script_calls=None):
        async def _fake_run_proc(cmd, **kwargs):
            if "rev-list" in cmd:
                return subprocess.CompletedProcess(cmd, 0, count, "")
            return subprocess.CompletedProcess(cmd, 0, "", "")

        async def _fake_run_script(name, *args):
            if script_calls is not None:
                script_calls.append(name)
            return subprocess.CompletedProcess(["bash", name], 0, "", "")

        monkeypatch.setattr(leerie, "run_proc", _fake_run_proc)
        monkeypatch.setattr(leerie, "_run_script", _fake_run_script)

    def test_empty_branch_routes_to_the_no_work_terminal(
            self, leerie, monkeypatch, st):
        scripts = []
        self._ahead(monkeypatch, leerie, "0", scripts)
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        # The disposition, not merely that something was written.
        assert st.data["current_phase"] == "done: no work required"
        assert st.data["no_work_required"] is True
        assert "finalize.sh" not in scripts

    def test_subtask_branches_and_worktrees_are_still_reaped(
            self, leerie, monkeypatch, st):
        """Asserting only that `finalize.sh` is SKIPPED would pass against a
        version that also skips cleanup — and this run's waves did execute,
        so `leerie/subtasks/<run-id>/*` branches and their worktrees exist.
        Skipping cleanup would leak one of each per subtask, invisibly
        (`.leerie/` is gitignored), and would be a regression against the
        `die()` this path replaces, which at least reached
        `_cleanup_on_abnormal_exit`."""
        scripts = []
        self._ahead(monkeypatch, leerie, "0", scripts)
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert "cleanup.sh" in scripts

    def test_the_executed_plan_record_survives(self, leerie, monkeypatch, st):
        """`reset_plan_state=False` is load-bearing here: the wave/subtask
        record is the evidence of what was checked. Clearing it would make a
        run that executed a plan indistinguishable from one that never
        planned — which is exactly what the planning-time callers want and
        this caller must not do."""
        self._ahead(monkeypatch, leerie, "0")
        _finalize_ready_state(st)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert st.data["waves"] == [["feat-001"]]
        assert st.data["subtask_status"] == {"feat-001": "complete"}

    def test_a_nonempty_branch_finalizes_normally(
            self, leerie, monkeypatch, st):
        """The guard must not swallow real runs."""
        scripts = []
        self._ahead(monkeypatch, leerie, "3", scripts)
        _finalize_ready_state(st)

        async def _fake_compose(*a, **k):
            return None
        monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert st.data["current_phase"] == "phase 6: finalize"
        assert not st.data.get("no_work_required")
        assert "finalize.sh" in scripts

    def test_unknown_ahead_count_proceeds_to_finalize(
            self, leerie, monkeypatch, st):
        """A failed/empty `rev-list` must fall through to `finalize.sh`
        rather than being read as zero — the safe direction, since
        finalize.sh re-checks it authoritatively."""
        scripts = []
        self._ahead(monkeypatch, leerie, "", scripts)
        _finalize_ready_state(st)

        async def _fake_compose(*a, **k):
            return None
        monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False,
            caps=_caps(leerie), models={}, efforts={}))

        assert "finalize.sh" in scripts
        assert not st.data.get("no_work_required")


def test_phase_finalize_no_push_skips_pr_and_records_health(
        leerie, monkeypatch, st):
    _finalize_ready_state(st)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (st.run_dir / "logs" / "feat-001.log").write_text(
        json.dumps({"type": "result", "duration_ms": 1000}) + "\n")

    compose_called = False

    async def _fake_compose(*a, **k):
        nonlocal compose_called
        compose_called = True

    monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=True, no_verify=False,
        caps=_caps(leerie), models={}, efforts={}))

    assert not compose_called
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["no_push"] is True
    assert run_json["finished_at"]
    assert run_json["health"]["slowest_worker_sid"] == "feat-001"
    assert st.data["finished_at"]


def test_phase_finalize_pushes_and_composes_pr(leerie, monkeypatch, st):
    _finalize_ready_state(st)
    st.data["unreviewed_subtasks"] = ["feat-002"]
    st.data["symptom_findings"] = {
        "bugfix-003": ["SYMPTOM_DID_NOT_REPRODUCE: could not reproduce"]}
    st.data["telemetry"] = {"calls": 5, "cost_usd": 1.23,
                            "input_tokens": 100, "output_tokens": 50}

    compose_calls = []

    async def _fake_compose(*a, **k):
        compose_calls.append(1)

    monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=False, no_verify=True,
        caps=_caps(leerie), models={}, efforts={}))

    assert compose_calls == [1]
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["no_push"] is False
    assert run_json["no_verify"] is True


def test_phase_finalize_host_no_push_overrides_intent_on_fly(
        leerie, monkeypatch, st):
    """On the Fly runtime `no_push` is a mechanism flag; `host_no_push`
    carries the user's actual intent and must win."""
    _finalize_ready_state(st)

    compose_calls = []

    async def _fake_compose(*a, **k):
        compose_calls.append(1)

    monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=True, no_verify=False,
        caps=_caps(leerie), models={}, efforts={},
        host_no_push=False))

    assert compose_calls == [1]
    run_json = json.loads((st.run_dir / "run.json").read_text())
    assert run_json["no_push"] is False


def test_phase_finalize_dies_when_finalize_sh_fails(leerie, monkeypatch, st):
    _finalize_ready_state(st)

    async def _failing_run_script(name, *args):
        if name == "finalize.sh":
            return subprocess.CompletedProcess(
                ["bash", name, *args], 1, "", "run branch is empty")
        return subprocess.CompletedProcess(["bash", name, *args], 0, "", "")

    monkeypatch.setattr(leerie, "_run_script", _failing_run_script)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False))


def test_phase_finalize_dies_when_branch_disappears_after_cleanup(
        leerie, monkeypatch, st):
    _finalize_ready_state(st)

    async def _failing_run_proc(cmd, **kwargs):
        # The `git show-ref --verify` post-cleanup check fails.
        return subprocess.CompletedProcess(cmd, 1, "", "")

    monkeypatch.setattr(leerie, "run_proc", _failing_run_proc)

    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False))


def test_phase_finalize_refuses_when_waves_incomplete(leerie, st):
    st.data["waves"] = [["feat-001"], ["feat-002"]]
    st.data["completed_waves"] = 1
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_finalize(
            st.leerie_root, st, no_push=True, no_verify=False))


def test_phase_finalize_capture_hook_exception_is_swallowed(
        leerie, monkeypatch, st):
    """capture_repo_deps raising must not derail a clean finalize."""
    _finalize_ready_state(st)

    async def _boom(*a, **k):
        raise RuntimeError("capture blew up")

    monkeypatch.setattr(leerie, "capture_repo_deps", _boom)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=True, no_verify=False))
    assert st.data["finished_at"]


def test_phase_finalize_record_run_health_exception_is_swallowed(
        leerie, monkeypatch, st):
    _finalize_ready_state(st)

    def _boom(_st):
        raise RuntimeError("health blew up")

    monkeypatch.setattr(leerie, "_record_run_health", _boom)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=True, no_verify=False))
    assert st.data["finished_at"]


def test_phase_finalize_without_caps_skips_pr_writer(leerie, monkeypatch, st):
    """When caps/models/efforts are not threaded through (no-work short
    path), pr_writer must not run even when push would otherwise happen."""
    _finalize_ready_state(st)

    called = False

    async def _fake_compose(*a, **k):
        nonlocal called
        called = True

    monkeypatch.setattr(leerie, "_compose_pr_via_llm", _fake_compose)

    asyncio.run(leerie.phase_finalize(
        st.leerie_root, st, no_push=False, no_verify=False))

    assert not called


# ------------------------------------------------------------------
# _orchestrate — the async entry point itself
# ------------------------------------------------------------------

def _orchestrate_args(leerie):
    return types.SimpleNamespace(resume=True, task=None)


def test_orchestrate_runs_phases_and_tears_down_background_tasks(
        leerie, monkeypatch, st):
    """Drives the real `_orchestrate()` with `_run_phases` stubbed to a
    no-op — covers the sampler/reaper task lifecycle and the non-strict
    (force_strict_output off) path with no proxy summary."""
    ran = []

    async def _fake_run_phases(*a, **k):
        ran.append(1)

    monkeypatch.setattr(leerie, "_run_phases", _fake_run_phases)

    caps = _caps(leerie)
    caps["force_strict_output"] = False

    asyncio.run(leerie._orchestrate(
        _orchestrate_args(leerie), caps, st.leerie_root, st,
        "both", "quiet", {}, {}))

    assert ran == [1]


def test_orchestrate_propagates_phase_exception_after_cleanup(
        leerie, monkeypatch, st):
    """A crash inside _run_phases must still cancel the sampler/reaper
    tasks (the `finally` block) rather than leaking them, and must
    propagate rather than being swallowed."""
    async def _boom(*a, **k):
        raise RuntimeError("phase blew up")

    monkeypatch.setattr(leerie, "_run_phases", _boom)

    caps = _caps(leerie)
    caps["force_strict_output"] = False

    with pytest.raises(RuntimeError, match="phase blew up"):
        asyncio.run(leerie._orchestrate(
            _orchestrate_args(leerie), caps, st.leerie_root, st,
            "both", "quiet", {}, {}))


def test_orchestrate_strict_proxy_start_failure_dies(leerie, monkeypatch, st):
    """A port-bind failure on the strict-output proxy must fail closed
    with die(), not silently continue unconstrained."""
    async def _fake_run_phases(*a, **k):
        return None

    monkeypatch.setattr(leerie, "_run_phases", _fake_run_phases)

    async def _boom_start(self):
        raise OSError("address already in use")

    monkeypatch.setattr(leerie._StrictOutputProxy, "start", _boom_start)

    caps = _caps(leerie)
    caps["force_strict_output"] = True
    caps["max_parallel"] = 2

    with pytest.raises(SystemExit):
        asyncio.run(leerie._orchestrate(
            _orchestrate_args(leerie), caps, st.leerie_root, st,
            "both", "quiet", {}, {}))
    assert leerie._STRICT_PROXY is None


def test_orchestrate_strict_proxy_summary_reports_every_category(
        leerie, monkeypatch, st):
    """Drive every conditional summary line in the strict-proxy teardown
    (renamed-tool, unexpected shape, fallback, schema error, transient) by
    mutating the live proxy's counters from inside the stubbed
    `_run_phases`, before the `finally` block reads them."""
    async def _fake_run_phases(*a, **k):
        p = leerie._STRICT_PROXY
        p.rewritten = 0
        p.passed_through = 3
        p.unexpected_tool_shape = 1
        p.fell_back = 2
        p.schema_errors = 4
        p.transient_errors = 5

    monkeypatch.setattr(leerie, "_run_phases", _fake_run_phases)

    caps = _caps(leerie)
    caps["force_strict_output"] = True
    caps["max_parallel"] = 2

    asyncio.run(leerie._orchestrate(
        _orchestrate_args(leerie), caps, st.leerie_root, st,
        "both", "quiet", {}, {}))

    assert leerie._STRICT_PROXY is None


def test_orchestrate_strict_output_proxy_summary_on_clean_run(
        leerie, monkeypatch, st):
    """force_strict_output=True must start the proxy, let it summarize
    at teardown, and stop it — with nothing rewritten/passed-through the
    summary still logs without raising."""
    async def _fake_run_phases(*a, **k):
        return None

    monkeypatch.setattr(leerie, "_run_phases", _fake_run_phases)

    caps = _caps(leerie)
    caps["force_strict_output"] = True
    caps["max_parallel"] = 2

    asyncio.run(leerie._orchestrate(
        _orchestrate_args(leerie), caps, st.leerie_root, st,
        "both", "quiet", {}, {}))

    assert leerie._STRICT_PROXY is None
