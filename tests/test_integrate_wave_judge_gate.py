"""Tests for the integration_judge gate in integrate_wave (DESIGN §8
*Independent adversarial verification*): after a successful integrator merge
commit (post check_merge_committed), invoke integration_judge to attack for
behavioral breakage. Since N3 a finding is repaired by re-driving the
integrator, or the subtask is backed out (DESIGN §6 *When integration cannot
succeed*); the loop itself is executed in tests/test_integration_repair_loop.py.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.conftest import judge_checkout_in_place

# Staging is on the run branch (`_compute_run_branch`): the clean-tree gate
# holds every worker there (DESIGN §8 *A worker leaves its tree clean*).
STAGING_BRANCH = "leerie/runs/test-int-gate-aaa"


def _state(leerie, tmp_path, run_id="test-int-gate-aaa"):
    leerie_root = tmp_path / ".leerie"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test", "worker_count": 0, "run_id": run_id}
    st.save()
    return st


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


MODELS = {"integrator": "sonnet", "integration_judge": "opus"}
EFFORTS = {"integrator": "low", "integration_judge": "medium"}


class TestWiring:
    def test_invokes_integration_judge_after_merge_committed(self, leerie):
        """The gate (via the shared `_gate` closure around
        `_run_integration_judge_gate`) runs after check_merge_committed
        passes, before appending to integrated. Since the unjudged-merge
        resume (DESIGN §6 *A committed resolution is not a judged one*) the
        post-commit sequence is the `_finish_integrator_merge` closure,
        shared with the resume path; executed in
        tests/test_resume_unjudged_integration.py."""
        src = inspect.getsource(leerie.integrate_wave)
        verdict_src = inspect.getsource(leerie._integration_judge_verdict)
        assert 'schema_key="integration_judge"' in verdict_src
        assert "await _run_integration_judge_gate(" in src
        i_finish_def = src.index("async def _finish_integrator_merge(")
        i_gate = src.index("await _gate(sid)", i_finish_def)
        i_merge_check = src.index("check_merge_committed(staging)")
        i_helper_call = src.index(
            "await _finish_integrator_merge(sid, pre_sha)", i_merge_check)
        i_append = src.rindex("integrated.append(sid)")
        assert i_finish_def < i_gate < i_merge_check
        assert i_merge_check < i_helper_call < i_append

    def test_uses_run_checked_loop(self, leerie):
        """integration_judge is invoked via _run_checked_loop for bounded
        fresh-session retry on WorkerError."""
        gate_src = inspect.getsource(leerie._integration_judge_verdict)
        judge_idx = gate_src.index('schema_key="integration_judge"')
        assert "_run_checked_loop(" in gate_src
        loop_idx = gate_src.index("_run_checked_loop(", judge_idx)
        assert loop_idx > judge_idx

    def test_judge_call_passes_no_feedback_prompt(self, leerie):
        """Rewritten for N3. The JUDGE call stays single-pass — it passes no
        make_feedback_prompt (re-asking a judge cannot act on its finding);
        the repair is a separate integrator re-drive followed by a fresh,
        whole re-judge (DESIGN §6 *When integration cannot succeed*)."""
        gate_src = inspect.getsource(leerie._integration_judge_verdict)
        judge_def_idx = gate_src.index("def _check_integration(judge_result: dict)")
        judge_loop_idx = gate_src.index("_run_checked_loop(", judge_def_idx)
        loop_end = gate_src.index(")", judge_loop_idx + 150)
        assert "make_feedback_prompt=" not in gate_src[judge_loop_idx:loop_end]
        round_src = inspect.getsource(leerie._integration_repair_round)
        assert 'schema_key="integrator"' in round_src

    def test_backs_out_on_an_unrepaired_defect(self, leerie):
        """Rewritten for N3: an unrepaired defect no longer die()s — the
        merge is reset to its first parent and the subtask settles
        unconverged. Executed in tests/test_integration_repair_loop.py."""
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        assert "die(" not in gate_src
        assert '"reset", "--hard", f"{merge_sha}^1"' in gate_src
        assert "_settle_unconverged(" in gate_src

    def test_degrades_on_worker_error(self, leerie):
        """A WorkerError on every round degrades: merge preserved (no revert),
        since check_merge_committed already ran."""
        verdict_src = inspect.getsource(leerie._integration_judge_verdict)
        assert "if judge_result is None:" in verdict_src
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        start = gate_src.index("if verdict is None:")
        branch = gate_src[start:gate_src.index("return True", start)]
        assert "degrading" in branch
        assert "reset" not in branch and "merge --abort" not in branch

    def test_self_gate_removed_from_check_integrator_output(self, leerie):
        """check_integrator_output no longer calls _confidence_issues.
        The word 'resolution' may appear in a docstring but not as a
        confidence axis check."""
        src = inspect.getsource(leerie.check_integrator_output)
        assert "_confidence_issues" not in src
        # Check that resolution is not used as an axis (not in a call)
        assert '_confidence_issues(' not in src or 'resolution' not in src

    def test_no_remaining_confidence_issues_callers(self, leerie):
        """After removing the integrator self-gate, _confidence_issues has
        zero callers (definition of done)."""
        import orchestrator.leerie as leerie_mod
        src = inspect.getsource(leerie_mod)
        # Count calls to _confidence_issues (not the definition)
        lines = [l for l in src.split('\n')
                 if '_confidence_issues(' in l and 'def _confidence_issues' not in l]
        assert len(lines) == 0, f"Remaining _confidence_issues calls: {lines}"


def test_clean_integration_passes(leerie, tmp_path, monkeypatch):
    """A clean judge result (empty defects) lets integration proceed."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    # Create a minimal git repo in staging
    import subprocess
    subprocess.run(["git", "init", "-b", STAGING_BRANCH], cwd=str(staging),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(staging), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(staging), check=True, capture_output=True)
    (staging / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=str(staging), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(staging),
                   check=True, capture_output=True)

    results = {"feat-001": {"status": "complete", "intent": "add feature",
                           "criteria_results": []}}

    integrator_called = False
    judge_called = False

    async def fake_claude_p(**kwargs):
        nonlocal integrator_called, judge_called
        if kwargs.get("schema_key") == "integrator":
            integrator_called = True
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean merge"}}
        elif kwargs.get("schema_key") == "integration_judge":
            judge_called = True
            return {"merge_reviewed": True, "defects": [],
                    "rationale": "behavioral integrity preserved"}
        return {}

    async def fake_run_script(script, sid, run_id):
        # Simulate conflict (returncode=1) to trigger integrator
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_run_proc(cmd, cwd=None):
        # Simulate successful git commands
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge commit\n\ndiff content\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    async def fake_check_merge_committed(staging_path):
        # Simulate successful merge commit check (no MERGE_HEAD)
        return None

    async def fake_check_integrator_commit(staging_path):
        # Simulate no commit warnings
        return None

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert integrator_called, "integrator should have been invoked"
    assert judge_called, "integration_judge should have been invoked"
    assert "feat-001" in out, "clean integration should succeed"


SRC_BASE = "def compute():\n    return 1\n"
SRC_OURS = SRC_BASE + "\ndef integrated_helper_function():\n    return 1\n"
SRC_THEIRS = SRC_BASE + "\ndef incoming_helper_function():\n    return 2\n"


def _real_merge_staging(leerie_dir):
    """A staging repo whose HEAD is a real two-parent merge of `incoming`
    that dropped the incoming side's function."""
    import subprocess

    def g(*a):
        return subprocess.run(["git", *a], cwd=str(staging), check=True,
                              capture_output=True, text=True).stdout
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    g("init", "-q", "-b", STAGING_BRANCH)
    g("config", "user.email", "t@x")
    g("config", "user.name", "t")
    (staging / "app.py").write_text(SRC_BASE)
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "incoming")
    (staging / "app.py").write_text(SRC_THEIRS)
    g("commit", "-q", "-am", "incoming")
    g("checkout", "-q", STAGING_BRANCH)
    (staging / "app.py").write_text(SRC_OURS)
    g("commit", "-q", "-am", "integrated")
    p1 = g("rev-parse", "HEAD").strip()
    subprocess.run(["git", "merge", "--no-ff", "-m", "leerie: integrate "
                    "feat-001", "incoming"], cwd=str(staging),
                   capture_output=True)
    (staging / "app.py").write_text(SRC_OURS)
    g("add", "-A")
    g("commit", "-q", "--no-edit")
    return staging, p1, g("rev-parse", "HEAD").strip()


DROPPED = {"kind": "dropped_change",
           "concrete_scenario": "the incoming helper was silently dropped",
           "location": "app.py", "why_broken": "its callers fail"}


def _no_conflict_tests(leerie, monkeypatch):
    async def none(*a, **kw):
        return {"test_failure": [], "stale_by_other_side": [], "scope": [],
                "skipped": "no test command"}
    monkeypatch.setattr(leerie, "_integration_test_failures", none)


def test_concrete_defect_backs_out_and_the_wave_continues(leerie, tmp_path, monkeypatch):
    """Rewritten for N3 (DESIGN §6 *When integration cannot succeed*): a
    concrete defect no longer kills the run. Here the repair integrator
    reports `design-conflict`, so the merge is backed out to its first
    parent, the subtask settles `unconverged`, and the wave CONTINUES —
    the next subtask still integrates."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, p1, merge = _real_merge_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"},
               "feat-003": {"status": "complete"}}
    calls: list[str] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs["sid"])
        if kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [DROPPED],
                    "rationale": "found breakage"}
        if kwargs["sid"].startswith("integrator-repair-"):
            return {"incoming_subtask": "feat-001",
                    "status": "design-conflict", "diagnosis": "both cannot hold"}
        return {"status": "resolved", "resolution_summary": "merged"}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1 if sid == "feat-001" else 0
        mock.stderr = mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "check_merge_committed",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "check_integrator_commit",
                        AsyncMock(return_value=None))
    _no_conflict_tests(leerie, monkeypatch)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001", "feat-003"], results, leerie_dir, _caps(leerie), st,
        MODELS, EFFORTS))
    assert out == ["feat-003"]
    assert calls == ["integrator-feat-001", "integration_judge-feat-001",
                     "integrator-repair-feat-001"]
    import subprocess
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=str(staging),
                          capture_output=True, text=True).stdout.strip()
    assert head == p1
    assert results["feat-001"]["status"] == "unconverged"
    assert st.data["subtask_status"]["feat-001"] == "unconverged"
    assert st.data["integration_gate"]["feat-001"]["outcome"] == "backed_out"

def test_worker_error_degrades_preserves_merge(leerie, tmp_path, monkeypatch):
    """A WorkerError on every round degrades: merge preserved, no die()."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    import subprocess
    subprocess.run(["git", "init", "-b", STAGING_BRANCH], cwd=str(staging),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(staging), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(staging), check=True, capture_output=True)
    (staging / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=str(staging), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(staging),
                   check=True, capture_output=True)

    results = {"feat-001": {"status": "complete", "intent": "add feature",
                           "criteria_results": []}}

    judge_call_count = 0

    async def fake_claude_p(**kwargs):
        nonlocal judge_call_count
        if kwargs.get("schema_key") == "integrator":
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            judge_call_count += 1
            raise leerie.WorkerError("PID exhaustion")
        return {}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge\n\ndiff\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    async def fake_check_merge_committed(staging_path):
        # Simulate successful merge commit check (no MERGE_HEAD)
        return None

    async def fake_check_integrator_commit(staging_path):
        # Simulate no commit warnings
        return None

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    # Should degrade, not die
    out = asyncio.run(_run())

    assert judge_call_count == _caps(leerie)["judgment_check_rounds"]
    assert "feat-001" in out, "should preserve merge on WorkerError degradation"


def test_vague_defect_does_not_gate(leerie, tmp_path, monkeypatch):
    """Anti-gaming: a defect without concrete_scenario or location does not
    gate."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    import subprocess
    subprocess.run(["git", "init", "-b", STAGING_BRANCH], cwd=str(staging),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(staging), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(staging), check=True, capture_output=True)
    (staging / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=str(staging), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(staging),
                   check=True, capture_output=True)

    results = {"feat-001": {"status": "complete", "intent": "add feature",
                           "criteria_results": []}}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            # Return defects missing concrete fields — should not gate
            return {"merge_reviewed": True, "defects": [
                {"kind": "dropped_change", "concrete_scenario": "",
                 "location": "somewhere", "why_broken": "bad"},
                {"kind": "call_site_mismatch", "concrete_scenario": "some issue",
                 "location": "", "why_broken": "broken"}
            ], "rationale": "vague"}
        return {}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge\n\ndiff\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    async def fake_check_merge_committed(staging_path):
        # Simulate successful merge commit check (no MERGE_HEAD)
        return None

    async def fake_check_integrator_commit(staging_path):
        # Simulate no commit warnings
        return None

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    # Should pass — vague defects don't gate
    out = asyncio.run(_run())
    assert "feat-001" in out


# --- integration_gate resume + accept-integration (bugfix-001-1) --------


def _staging_repo(leerie_dir):
    import subprocess
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    subprocess.run(["git", "init", "-b", STAGING_BRANCH], cwd=str(staging),
                   check=True, capture_output=True)
    subprocess.run(["git", "config", "user.email", "test@test.com"],
                   cwd=str(staging), check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test"],
                   cwd=str(staging), check=True, capture_output=True)
    (staging / "file.txt").write_text("content\n")
    subprocess.run(["git", "add", "."], cwd=str(staging), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=str(staging),
                   check=True, capture_output=True)
    return staging


def test_defect_persisted_to_state_before_the_repair(leerie, tmp_path, monkeypatch):
    """(a) The verdict reaches integration_gate/integration_defects[sid] on
    DISK before anything else happens — checked at the moment the repair
    integrator is spawned, so a crash there leaves a record a resume reads."""
    import json as _json
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, _p1, merge = _real_merge_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"}}
    on_disk: list[dict] = []

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [DROPPED],
                    "rationale": "found breakage"}
        if kwargs["sid"].startswith("integrator-repair-"):
            on_disk.append(_json.loads(st.path.read_text()))
            return {"incoming_subtask": "feat-001", "status": "failed",
                    "diagnosis": "x"}
        return {"status": "resolved", "resolution_summary": "merged"}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "check_merge_committed",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "check_integrator_commit",
                        AsyncMock(return_value=None))
    _no_conflict_tests(leerie, monkeypatch)
    asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS,
        EFFORTS))
    entry = on_disk[0]["integration_gate"]["feat-001"]
    assert entry["accepted"] is False
    assert entry["merge_commit_sha"] == merge
    assert any("dropped_change" in d for d in entry["defects"])
    assert on_disk[0]["integration_defects"]["feat-001"] == entry["defects"]
    # Backed out afterwards: the mirror is cleared, the record says why.
    assert "feat-001" not in (st.data.get("integration_defects") or {})
    assert st.data["integration_gate"]["feat-001"]["outcome"] == "backed_out"

def test_resume_reinvokes_the_judge_without_redriving_integrate_sh(
        leerie, tmp_path, monkeypatch):
    """(c) On a resume, a sid with a present-but-unaccepted integration_gate
    entry re-invokes the judge directly — WITHOUT calling integrate.sh (the
    merge is already committed; integrate.sh's `git merge` is idempotent and
    would just see "Already up to date" and skip straight past the judge)."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    _staging_repo(leerie_dir)

    # Simulate the state left behind by a prior invocation that die()d
    # inside the judge gate.
    st.data["integration_gate"] = {
        "feat-001": {"defects": ["INTEGRATION_DEFECT (dropped_change) x"],
                     "advisories": [], "merge_commit_sha": "abc123",
                     "accepted": False}}
    st.data["integration_defects"] = {
        "feat-001": ["INTEGRATION_DEFECT (dropped_change) x"]}
    st.save()

    results = {"feat-001": {"status": "complete"}}

    integrate_sh_called = False
    judge_called = False

    async def fake_claude_p(**kwargs):
        nonlocal judge_called
        if kwargs.get("schema_key") == "integration_judge":
            judge_called = True
            return {"merge_reviewed": True, "defects": [],
                    "rationale": "clean on re-review"}
        return {}

    async def fake_run_script(script, sid, run_id):
        nonlocal integrate_sh_called
        integrate_sh_called = True
        mock = AsyncMock()
        mock.returncode = 0
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = "abc123\n" if "rev-parse" in cmd else "Merge\n\ndiff\n"
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert judge_called, "resume must re-invoke integration_judge"
    assert not integrate_sh_called, (
        "resume must NOT re-drive integrate.sh — it would just see the "
        "branch already merged and short-circuit past the judge")
    assert "feat-001" in out
    assert st.data["integration_gate"]["feat-001"]["accepted"] is True
    assert "feat-001" not in st.data.get("integration_defects", {})


def test_resume_reenters_the_repair_loop(
        leerie, tmp_path, monkeypatch):
    """Rewritten for N3: a resume whose judge returns the SAME defect no
    longer dies — it re-enters the repair loop against the recorded merge
    (no integrate.sh), and a repair that clears the re-judge integrates."""
    import subprocess
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, _p1, merge = _real_merge_staging(leerie_dir)
    st.data["integration_gate"] = {
        "feat-001": {"defects": ["INTEGRATION_DEFECT (dropped_change) x"],
                     "advisories": [], "merge_commit_sha": merge,
                     "accepted": False}}
    st.save()
    results = {"feat-001": {"status": "complete"}}
    (leerie_dir / "subtasks").mkdir(parents=True, exist_ok=True)
    (leerie_dir / "subtasks" / "feat-001.json").write_text(
        '{"id": "feat-001", "intent": "resumed intent"}')
    verdicts = [[DROPPED], []]
    sids: list[str] = []

    async def fake_claude_p(**kwargs):
        sids.append(kwargs["sid"])
        if kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": verdicts.pop(0),
                    "rationale": "r"}
        (staging / "app.py").write_text(SRC_THEIRS + SRC_OURS[len(SRC_BASE):])
        subprocess.run(["git", "commit", "-q", "-am", "repair"],
                       cwd=str(staging), check=True)
        return {"incoming_subtask": "feat-001", "status": "resolved"}

    async def no_script(*a):
        raise AssertionError("resume must not re-drive integrate.sh")

    # A sibling a PRIOR invocation already merged (complete, not in this
    # invocation's results): the resumed judge must be told about it.
    subprocess.run(["git", "branch",
                    f"leerie/subtasks/{st.run_id}/feat-000", f"{merge}^1"],
                   cwd=str(staging), check=True)
    st.data["subtask_status"] = {"feat-000": "complete",
                                 "feat-001": "complete"}
    prompts: list[str] = []
    real_fake = fake_claude_p

    async def recording(**kwargs):
        prompts.append(kwargs["user_prompt"])
        return await real_fake(**kwargs)

    monkeypatch.setattr(leerie, "claude_p", recording)
    monkeypatch.setattr(leerie, "_run_script", no_script)
    _no_conflict_tests(leerie, monkeypatch)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-000", "feat-001"], results, leerie_dir, _caps(leerie), st,
        MODELS, EFFORTS))
    assert out == ["feat-001"]
    assert "Already-integrated subtasks: feat-000\n" in prompts[0]
    assert "Already-integrated subtasks it may conflict with: feat-000\n" \
        in prompts[1]
    # The intent is read from the spec when the stand-in result has none.
    assert "Intent: resumed intent\n" in prompts[0]
    assert sids == ["integration_judge-feat-001",
                    "integrator-repair-feat-001",
                    "integration_judge-feat-001"]
    rec = st.data["integration_gate"]["feat-001"]
    assert rec["accepted"] is True and rec["outcome"] == "repaired"

def test_accepted_finding_skips_the_judge_entirely(leerie, tmp_path, monkeypatch):
    """(d) After `accept-integration` flips `accepted` to True, a subsequent
    resume must not re-invoke the judge (nor integrate.sh) — it just
    advances past the finding."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    _staging_repo(leerie_dir)

    # State as accept-integration would leave it.
    st.data["integration_gate"] = {
        "feat-001": {"defects": ["INTEGRATION_DEFECT (dropped_change) x"],
                     "advisories": [], "merge_commit_sha": "abc123",
                     "accepted": True}}
    st.save()

    results = {"feat-001": {"status": "complete"}}

    calls = {"judge": False, "integrate_sh": False}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            calls["judge"] = True
        return {}

    async def fake_run_script(script, sid, run_id):
        calls["integrate_sh"] = True
        mock = AsyncMock()
        mock.returncode = 0
        mock.stderr = ""
        mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert not calls["judge"], "an accepted finding must not re-invoke the judge"
    assert not calls["integrate_sh"], "an accepted finding must not re-drive integrate.sh"
    assert "feat-001" in out


# --- Coverage: the paths that never involve a clean judge result ----------
# (integrate.sh precondition failure, integrator crash+rescue, a claimed
# "resolved" merge that lied about being committed, a non-fatal commit
# warning, and a design-conflict/failed integrator verdict.)

def test_precondition_failure_dies_and_records_blocked(leerie, tmp_path, monkeypatch):
    """integrate.sh exit 2 is a precondition failure (missing worktree/
    branch), not a conflict — must die() with the script's own message and
    record it in state.data['blocked'] before dying."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    results = {"feat-001": {"status": "complete"}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 2
        mock.stderr = "subtask branch missing"
        mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())

    assert "feat-001" in st.data.get("blocked", {})
    assert "subtask branch missing" in st.data["blocked"]["feat-001"]


def _conflicting_staging(leerie_dir):
    """A staging repo on `main` plus an `incoming` branch that conflicts
    with it on app.py; returns (staging, pre_sha). The fake integrate.sh
    below starts the real merge, so the integrator meets a real
    mid-merge tree."""
    import subprocess

    def g(*a):
        return subprocess.run(["git", *a], cwd=str(staging), check=True,
                              capture_output=True, text=True).stdout
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)
    g("init", "-q", "-b", STAGING_BRANCH)
    g("config", "user.email", "t@x")
    g("config", "user.name", "t")
    (staging / "app.py").write_text("x = 0\n")
    g("add", "-A")
    g("commit", "-q", "-m", "base")
    g("checkout", "-q", "-b", "incoming")
    (staging / "app.py").write_text("x = 2\n")
    g("commit", "-q", "-am", "incoming")
    g("checkout", "-q", STAGING_BRANCH)
    (staging / "app.py").write_text("x = 1\n")
    g("commit", "-q", "-am", "integrated")
    return staging, g("rev-parse", "HEAD").strip()


def _real_conflict_script(staging):
    """integrate.sh stand-in: feat-001 starts a real conflicting merge
    (rc 1); any other sid merges clean (rc 0)."""
    import subprocess

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.stderr = mock.stdout = ""
        if sid == "feat-001":
            r = subprocess.run(["git", "merge", "--no-ff", "-m", "leerie: "
                                "integrate feat-001", "incoming"],
                               cwd=str(staging), capture_output=True)
            mock.returncode = 1 if r.returncode else 0
        else:
            mock.returncode = 0
        return mock
    return fake_run_script


def _git_out(staging, *a):
    import subprocess
    return subprocess.run(["git", *a], cwd=str(staging), capture_output=True,
                          text=True).stdout.strip()


def _rescue_refs(staging, run_id, sid):
    return _git_out(staging, "for-each-ref", "--format=%(refname)",
                    f"refs/leerie/rescue/{run_id}/{sid}/").split()


def _half_resolve(staging, text):
    """What a dying integrator leaves: the conflict hand-edited, nothing
    committed."""
    (staging / "app.py").write_text(text)


def _resolve_and_commit(staging):
    import subprocess
    (staging / "app.py").write_text("x = 3\n")
    subprocess.run(["git", "add", "app.py"], cwd=str(staging), check=True,
                   capture_output=True)
    subprocess.run(["git", "commit", "-q", "--no-edit"], cwd=str(staging),
                   check=True, capture_output=True)
    return {"status": "resolved", "resolution_summary": "kept both",
            "confidence": {"resolution": 9.0, "basis": "clean"}}


def _timeout():
    import subprocess
    return subprocess.TimeoutExpired(["claude", "-p", "SYSTEM PROMPT"], 1770)


def test_integrator_crash_every_time_backs_out_and_the_wave_continues(
        leerie, tmp_path, monkeypatch):
    """Rewritten (was `..._rescues_work_aborts_and_dies`): an integrator that
    crashes on every attempt no longer kills the run. Each crash — a
    WorkerError and a timeout alike — is rescued to a ref of its own,
    staging is reset to the merge's first parent and the merge redone, at
    most `integrator_crash_retries` times; then the subtask is backed out
    `unconverged` with every rescue ref in its reason, the loss is on the
    risk register, and the next subtask of the wave still integrates. Real
    git throughout."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"},
               "feat-003": {"status": "complete"}}
    seen: list[tuple[str, bool]] = []
    errors = [leerie.WorkerError("session killed"), _timeout(),
              leerie.WorkerError("no schema-valid JSON")]

    async def fake_claude_p(**kwargs):
        n = len(seen)
        seen.append(((staging / "app.py").read_text(),
                     (staging / ".git" / "MERGE_HEAD").exists()))
        _half_resolve(staging, f"x = 1{n}\n")
        raise errors[n]

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    caps = _caps(leerie)  # judgment_check_rounds = 3: no in-loop re-run
    assert caps["integrator_crash_retries"] == 2
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001", "feat-003"], results, leerie_dir, caps, st, MODELS,
        EFFORTS))
    assert out == ["feat-003"]
    # 1 + 2 retries, and every attempt met the original conflict — never
    # the previous attempt's half-made edit.
    assert len(seen) == 3
    for text, mid_merge in seen:
        assert mid_merge and "<<<<<<<" in text and "x = 1" in text
    assert _git_out(staging, "rev-parse", "HEAD") == pre
    assert not (staging / ".git" / "MERGE_HEAD").exists()
    assert (staging / "app.py").read_text() == "x = 1\n"
    refs = _rescue_refs(staging, st.run_id, "feat-001")
    assert len(refs) == 3
    assert [_git_out(staging, "show", f"{r}:app.py") for r in refs] == [
        "x = 10", "x = 11", "x = 12"]
    assert results["feat-001"]["status"] == "unconverged"
    assert st.data["subtask_status"]["feat-001"] == "unconverged"
    rec = st.data["integration_gate"]["feat-001"]
    assert rec["outcome"] == "backed_out" and rec["reset_to"] == pre
    for r in refs:
        assert r in rec["fallback_reason"]
    assert "crashed 3 time(s)" in rec["fallback_reason"]
    # The timeout is named briefly, never with the `claude -p` argv.
    assert "SYSTEM PROMPT" not in rec["fallback_reason"]
    loss = st.data["risk_register"]["feat-001"]
    assert [e["kind"] for e in loss] == ["integration_content_loss"]
    assert refs[0] in loss[0]["detail"]
    assert "feat-001" not in (st.data.get("blocked") or {})


@pytest.mark.parametrize("crash", ["worker_error", "timeout"])
def test_integrator_crash_once_retries_from_a_reset_merge_and_integrates(
        leerie, tmp_path, monkeypatch, crash):
    """One crash, then a clean resolution: the retry runs in the run, on a
    merge restarted from the first parent (the dead session's edit is gone,
    the conflict is back), and the subtask integrates. The dead session's
    resolution stays rescued. No die, no back-out, no `blocked`."""
    st = _state(leerie, tmp_path)
    st.data["skip_integration_check"] = True
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"}}
    seen: list[str] = []

    async def fake_claude_p(**kwargs):
        seen.append((staging / "app.py").read_text())
        if len(seen) == 1:
            _half_resolve(staging, "x = 99\n")
            raise (leerie.WorkerError("PID table exhausted")
                   if crash == "worker_error" else _timeout())
        return _resolve_and_commit(staging)

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS,
        EFFORTS))
    assert out == ["feat-001"]
    assert len(seen) == 2
    assert "<<<<<<<" in seen[1] and "x = 99" not in seen[1]
    head_parents = _git_out(staging, "log", "-1", "--format=%P").split()
    assert head_parents[0] == pre and len(head_parents) == 2
    assert (_git_out(staging, "log", "-1", "--format=%s")
            == "leerie: integrate feat-001")
    assert (staging / "app.py").read_text() == "x = 3\n"
    refs = _rescue_refs(staging, st.run_id, "feat-001")
    assert len(refs) == 1
    assert _git_out(staging, "show", f"{refs[0]}:app.py") == "x = 99"
    assert results["feat-001"]["status"] == "complete"
    assert "feat-001" not in (st.data.get("integration_gate") or {})
    assert "feat-001" not in (st.data.get("blocked") or {})


@pytest.mark.parametrize("crash", ["worker_error", "timeout"])
def test_integrator_crash_after_its_merge_commit_retries_from_pre_sha(
        leerie, tmp_path, monkeypatch, crash):
    """A session that committed its merge and then died: the retry must
    start from the merge's first parent, so the fresh integrator meets the
    original conflict — not the dead session's committed merge, which
    `git merge --abort` leaves in place. Without the reset, HEAD stays on
    that merge, so `_restart_integration_merge`'s HEAD check refuses the
    retry ("the reset before a retry left staging at …") before
    `integrate.sh` runs, and the subtask is backed out instead of
    resolved again (the direct test below pins that refusal). This is the
    case the reset before a retry exists for (round-4 claims review
    MEDIUM 4: removing it left every other test green)."""
    import subprocess
    st = _state(leerie, tmp_path)
    st.data["skip_integration_check"] = True
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"}}
    seen: list[tuple[str, bool]] = []

    async def fake_claude_p(**kwargs):
        seen.append(((staging / "app.py").read_text(),
                     (staging / ".git" / "MERGE_HEAD").exists()))
        if len(seen) == 1:
            (staging / "app.py").write_text("x = 77\n")
            subprocess.run(["git", "add", "app.py"], cwd=str(staging),
                           check=True, capture_output=True)
            subprocess.run(["git", "commit", "-q", "--no-edit"],
                           cwd=str(staging), check=True, capture_output=True)
            raise (leerie.WorkerError("session killed after its commit")
                   if crash == "worker_error" else _timeout())
        return _resolve_and_commit(staging)

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS,
        EFFORTS))
    assert out == ["feat-001"]
    assert len(seen) == 2
    # The retry met the original conflict, mid-merge, not the dead
    # session's committed `x = 77`.
    text, mid_merge = seen[1]
    assert mid_merge and "<<<<<<<" in text and "x = 77" not in text
    assert (staging / "app.py").read_text() == "x = 3\n"
    parents = _git_out(staging, "log", "-1", "--format=%P").split()
    assert parents[0] == pre and len(parents) == 2
    # Exactly one merge on top of pre: the dead session's is gone.
    assert _git_out(staging, "rev-list", "--count", f"{pre}..HEAD") == "2"
    assert results["feat-001"]["status"] == "complete"
    assert "feat-001" not in (st.data.get("integration_gate") or {})


@pytest.mark.parametrize("reset_runs", [True, False])
def test_restart_merge_refuses_a_head_the_reset_did_not_move(
        leerie, tmp_path, monkeypatch, reset_runs):
    """The mechanism behind the post-commit-crash retry, driven directly:
    staging holds a dead session's committed merge. With the reset, HEAD
    lands on `pre_sha`, `integrate.sh` runs once and the conflict is back
    (None). Without it, HEAD is still the dead merge, so the HEAD check
    refuses with the exact moved-head reason and `integrate.sh` is never
    called — the retry does not end on an "Already up to date" exit."""
    import subprocess
    st = _state(leerie, tmp_path)
    staging, pre = _conflicting_staging(tmp_path / ".leerie")
    subprocess.run(["git", "merge", "-q", "--no-ff", "-X", "theirs", "-m",
                    "dead session's merge", "incoming"], cwd=str(staging),
                   check=True, capture_output=True)
    dead = _git_out(staging, "rev-parse", "HEAD")
    assert dead != pre
    script_calls: list[str] = []
    real_script = _real_conflict_script(staging)

    async def spy_script(script, sid, run_id):
        script_calls.append(script)
        return await real_script(script, sid, run_id)

    async def no_reset(tree, rev, *, where, branch):
        # The one restore primitive's own failure: the reset did not land.
        return False

    monkeypatch.setattr(leerie, "_run_script", spy_script)
    if not reset_runs:
        monkeypatch.setattr(leerie, "_restore_tree", no_reset)
    end = asyncio.run(leerie._restart_integration_merge(
        staging, st, "feat-001", pre))
    if reset_runs:
        assert end is None
        assert script_calls == ["integrate.sh"]
        assert _git_out(staging, "rev-parse", "HEAD") == pre
        assert (staging / ".git" / "MERGE_HEAD").exists()
    else:
        assert end == (f"the reset before a retry left staging at "
                       f"{dead[:12]}, not {pre[:12]}")
        assert script_calls == []
        assert _git_out(staging, "rev-parse", "HEAD") == dead


def test_orchestrator_killed_mid_retry_resumes_and_integrates(
        leerie, tmp_path, monkeypatch):
    """A crash, then the orchestrator itself is killed during the retry
    (cancellation propagates out of the integrator call, staging left
    mid-merge with a half-made edit). The resume's `setup-run.sh` resets
    the staging that survived (tests/test_resume_staging_is_reset.py), which
    ends the merge and drops the half-made edit, as a Ctrl-C's cleanup
    does; the first crash's rescue ref is kept, and the subtask is
    integrated afresh on the original conflict. (Before, `integrate_wave`
    abandoned the merge on entry and rescued the edit to a second ref:
    919a0ee, superseded.)"""
    import subprocess
    st = _state(leerie, tmp_path)
    st.data["skip_integration_check"] = True
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    subprocess.run(["git", "branch", f"leerie/subtasks/{st.run_id}/feat-001",
                    "incoming"], cwd=str(staging), check=True)
    calls: list[str] = []

    async def killed(**kwargs):
        calls.append((staging / "app.py").read_text())
        if len(calls) == 1:
            _half_resolve(staging, "x = 41\n")
            raise leerie.WorkerError("session killed")
        _half_resolve(staging, "x = 42\n")
        raise asyncio.CancelledError()

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", killed)
    with pytest.raises(asyncio.CancelledError):
        asyncio.run(leerie.integrate_wave(
            ["feat-001"], {"feat-001": {"status": "complete"}}, leerie_dir,
            _caps(leerie), st, MODELS, EFFORTS))
    assert len(calls) == 2 and "<<<<<<<" in calls[1]
    assert (staging / ".git" / "MERGE_HEAD").exists()
    first = _rescue_refs(staging, st.run_id, "feat-001")
    assert len(first) == 1
    first_sha = _git_out(staging, "rev-parse", first[0])

    # The resume: `setup-run.sh`'s reset of a surviving staging.
    subprocess.run(["git", "reset", "-q", "--hard", "HEAD"],
                   cwd=str(staging), check=True)
    subprocess.run(["git", "clean", "-fdq"], cwd=str(staging), check=True)
    assert not (staging / ".git" / "MERGE_HEAD").exists()
    resumed: list[str] = []

    async def clean(**kwargs):
        resumed.append((staging / "app.py").read_text())
        return _resolve_and_commit(staging)

    monkeypatch.setattr(leerie, "claude_p", clean)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], {"feat-001": {"status": "complete"}}, leerie_dir,
        _caps(leerie), st, MODELS, EFFORTS))
    assert out == ["feat-001"]
    assert len(resumed) == 1
    assert "<<<<<<<" in resumed[0] and "x = 42" not in resumed[0]
    refs = _rescue_refs(staging, st.run_id, "feat-001")
    assert refs == first
    assert _git_out(staging, "rev-parse", first[0]) == first_sha
    assert _git_out(staging, "show", f"{first[0]}:app.py") == "x = 41"
    assert _git_out(staging, "log", "-1", "--format=%P").split()[0] == pre
    assert not (staging / ".git" / "MERGE_HEAD").exists()


def test_resolved_but_merge_not_committed_backs_out(leerie, tmp_path,
                                                     monkeypatch):
    """Rewritten for N3 (DESIGN §6 *When integration cannot succeed*): an
    integrator claiming 'resolved' while the worktree is still mid-merge is
    a lie (the integrator-side analogue of check_branch_has_commits). The
    merge is abandoned and the subtask backed out — the run no longer
    dies, and the next subtask of the wave still integrates. Real git:
    the real check_merge_committed sees the real MERGE_HEAD."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"},
               "feat-003": {"status": "complete"}}

    async def fake_claude_p(**kwargs):
        return {"status": "resolved", "resolution_summary": "merged",
                "confidence": {"resolution": 9.0, "basis": "clean"}}

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001", "feat-003"], results, leerie_dir, _caps(leerie), st,
        MODELS, EFFORTS))
    assert out == ["feat-003"]
    assert _git_out(staging, "rev-parse", "HEAD") == pre
    assert not (staging / ".git" / "MERGE_HEAD").exists()
    assert _git_out(staging, "status", "--porcelain") == ""
    assert results["feat-001"]["status"] == "unconverged"
    assert st.data["subtask_status"]["feat-001"] == "unconverged"
    rec = st.data["integration_gate"]["feat-001"]
    assert rec["outcome"] == "backed_out" and rec["accepted"] is False
    assert rec["reset_to"] == pre
    assert "claimed resolved" in rec["fallback_reason"]
    assert (st.data["unconverged_subtasks"]["feat-001"]["kind"]
            == "integration_backed_out")


def test_integrator_commit_warning_is_non_fatal_and_recorded(leerie, tmp_path, monkeypatch):
    """A non-empty check_integrator_commit warning does not abort the
    integration — it's logged and recorded in integrator_warnings, and the
    subtask still gets integrated."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete", "intent": "x",
                            "criteria_results": []}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [],
                    "rationale": "clean"}
        return {}

    async def fake_check_merge_committed(staging_path):
        return None

    async def fake_check_integrator_commit(staging_path):
        return "commit message doesn't mention the resolved conflict"

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge commit\n\ndiff\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert "feat-001" in st.data.get("integrator_warnings", {})
    assert "resolved conflict" in st.data["integrator_warnings"]["feat-001"]


@pytest.mark.parametrize("status,commits", [
    ("design-conflict", False), ("failed", False),
    # A failing integrator that committed a merge anyway: the fallback
    # resets to the merge's first parent, not to whatever it left.
    ("design-conflict", True)])
def test_first_resolution_verdict_backs_out_and_the_wave_continues(
        leerie, tmp_path, monkeypatch, status, commits):
    """Rewritten for N3 (DESIGN §6 *When integration cannot succeed*): an
    integrator verdict of `design-conflict`/`failed` on the FIRST conflict
    resolution takes the repair loop's fallback instead of killing the run —
    staging reset to the merge's first parent, the subtask settled
    `unconverged`, the loss recorded, the wave continued, and a resume
    skipping the record. Real git merges throughout."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"},
               "feat-003": {"status": "complete"}}
    calls: list[str] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs["sid"])
        if commits:
            import subprocess
            (staging / "app.py").write_text("x = 3\n")
            subprocess.run(["git", "commit", "-qam", "half"], cwd=str(staging),
                           capture_output=True, check=True)
        return {"status": status,
                "diagnosis": "the two subtasks disagree on the API shape"}

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001", "feat-003"], results, leerie_dir, _caps(leerie), st,
        MODELS, EFFORTS))
    # The wave continued: feat-003 still integrated, after the back-out.
    assert out == ["feat-003"]
    assert calls == ["integrator-feat-001"]
    assert _git_out(staging, "rev-parse", "HEAD") == pre
    assert not (staging / ".git" / "MERGE_HEAD").exists()
    assert (staging / "app.py").read_text() == "x = 1\n"
    assert results["feat-001"]["status"] == "unconverged"
    assert st.data["subtask_status"]["feat-001"] == "unconverged"
    rec = st.data["integration_gate"]["feat-001"]
    assert rec["outcome"] == "backed_out" and rec["reset_to"] == pre
    assert status in rec["fallback_reason"]
    assert "disagree on the API shape" in rec["fallback_reason"]
    loss = st.data["risk_register"]["feat-001"]
    assert [e["kind"] for e in loss] == ["integration_content_loss"]
    assert "none of feat-001's work is in this PR" in loss[0]["detail"]
    assert "blocked" not in st.data or "feat-001" not in st.data["blocked"]
    # Resume: the backed-out record is skipped — no integrate.sh, no worker.
    calls.clear()
    scripts: list[str] = []

    async def no_script(script, sid, run_id):
        scripts.append(sid)
        raise AssertionError("a backed-out sid must not be re-integrated")
    monkeypatch.setattr(leerie, "_run_script", no_script)
    again = asyncio.run(leerie.integrate_wave(
        ["feat-001"], {"feat-001": {"status": "complete"}}, leerie_dir,
        _caps(leerie), st, MODELS, EFFORTS))
    assert again == [] and calls == [] and scripts == []


def test_first_resolution_back_out_dies_only_when_the_reset_fails(
        leerie, tmp_path, monkeypatch):
    """The one state the fallback cannot step back from: the reset to the
    merge's first parent does not land (a broken repository). That still
    die()s, with the sid recorded blocked."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"}}
    async def broken_reset(tree, rev, *, where, branch):
        # The one restore primitive's reset does not land (a broken
        # repository): HEAD stays where the integrator left it.
        return False

    async def fake_claude_p(**kwargs):
        import subprocess
        (staging / "app.py").write_text("x = 3\n")
        subprocess.run(["git", "commit", "-qam", "half"], cwd=str(staging),
                       capture_output=True, check=True)
        return {"status": "failed", "diagnosis": "gave up"}

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_restore_tree", broken_reset)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS,
            EFFORTS))
    assert "back-out failed" in st.data["blocked"]["feat-001"]
    assert st.data.get("subtask_status", {}).get("feat-001") != "unconverged"


def test_clean_merge_no_conflict_appends_without_integrator(leerie, tmp_path, monkeypatch):
    """integrate.sh returncode 0 (no conflict) appends the sid directly and
    never spawns an integrator or the judge."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    results = {"feat-001": {"status": "complete"}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 0
        mock.stderr = ""
        mock.stdout = ""
        return mock

    claude_p_calls = []

    async def fake_claude_p(**kwargs):
        claude_p_calls.append(kwargs.get("schema_key"))
        return {}

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert claude_p_calls == [], "a clean merge must not spawn an integrator or judge"


def test_integrator_crash_then_recovery_logs_warning_and_still_integrates(
        leerie, tmp_path, monkeypatch):
    """A WorkerError on round 0 that recovers on round 1 is logged as a
    warning (infrastructure retry, not a found-issue retry) and the
    subtask still completes integration normally."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete", "intent": "x",
                            "criteria_results": []}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    call_count = {"integrator": 0}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            call_count["integrator"] += 1
            if call_count["integrator"] == 1:
                raise leerie.WorkerError("PID table exhausted")
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [], "rationale": "clean"}
        return {}

    async def fake_check_merge_committed(staging_path):
        return None

    async def fake_check_integrator_commit(staging_path):
        return None

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge commit\n\ndiff\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)

    caps = _caps(leerie)
    caps["judgment_check_rounds"] = 3

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, caps, st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert call_count["integrator"] == 2


def test_wave_skips_non_complete_sids(leerie, tmp_path, monkeypatch):
    """A sid whose settle status isn't 'complete' (e.g. blocked/failed) is
    skipped entirely by the wave loop — never handed to integrate.sh."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    results = {"feat-001": {"status": "complete"},
               "feat-002": {"status": "blocked"}}

    called = []

    async def fake_run_script(script, sid, run_id):
        called.append(sid)
        mock = AsyncMock()
        mock.returncode = 0
        mock.stderr = ""
        mock.stdout = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001", "feat-002"], results, leerie_dir, _caps(leerie), st,
            MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert called == ["feat-001"], "a non-complete sid must never reach integrate.sh"


def test_advisory_defect_logs_but_does_not_gate(leerie, tmp_path, monkeypatch):
    """A defect whose citation clears (equivalent coverage exists elsewhere in
    the merged tree) is logged as an advisory (the single logging call site
    inside integrate_wave/_run_integration_judge_gate) rather than gating."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)
    # A real file the citation can point to, so _coverage_citation_clears
    # treats it as an existing location in the merged tree.
    (staging / "tests" / "other_test.py").parent.mkdir(exist_ok=True)
    (staging / "tests" / "other_test.py").write_text("def test_x(): pass\n")

    results = {"feat-001": {"status": "complete", "intent": "x",
                            "criteria_results": []}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [{
                "kind": "dropped_change",
                "concrete_scenario": "feat-005's assertions are absent",
                "location": "tests/export_route_test.py",
                "why_broken": "no longer runs from this file",
                "coverage_elsewhere": {"file": "tests/other_test.py",
                                       "assertion": "test_x"},
            }], "rationale": "checked coverage"}
        return {}

    async def fake_check_merge_committed(staging_path):
        return None

    async def fake_check_integrator_commit(staging_path):
        return None

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        if "symbolic-ref" in cmd:
            # on the run branch, as the clean-tree gate requires
            mock.stdout = f"refs/heads/{STAGING_BRANCH}\n"
        elif "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge commit\n\ndiff\n"
        else:
            mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    judge_checkout_in_place(leerie, monkeypatch)
    # _run_integration_judge_gate resolves the citation against
    # Path(os.getcwd()), not `staging` — match that so the citation clears.
    monkeypatch.chdir(staging)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert st.data["integration_gate"]["feat-001"]["accepted"] is True
    assert st.data["integration_gate"]["feat-001"]["advisories"]


@pytest.mark.parametrize("message", [
    "Resolve merge conflict in app.py", "leerie: integrate feat-001"])
def test_an_integrator_merge_carries_the_integration_subject(
        leerie, tmp_path, monkeypatch, message):
    """Review r1 suspicion, proven: an integrator that commits its
    resolution with a message of its own left a merge `_integration_commit`
    (subject `leerie: integrate <sid>`) cannot find, so routing falls back to
    `files_likely_touched` and `_sibling_deliveries` misses the sibling's
    landed work. The subject is code's to set: the merge is re-worded,
    tree and parents unchanged, the integrator's message kept as the
    body."""
    import subprocess
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging, pre = _conflicting_staging(leerie_dir)
    results = {"feat-001": {"status": "complete"}}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            (staging / "app.py").write_text("x = 3\n")
            subprocess.run(["git", "add", "-A"], cwd=str(staging),
                           check=True)
            subprocess.run(["git", "commit", "-q", "-m", message],
                           cwd=str(staging), check=True)
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        return {"merge_reviewed": True, "defects": [], "rationale": "clean"}

    monkeypatch.setattr(leerie, "_run_script", _real_conflict_script(staging))
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    _no_conflict_tests(leerie, monkeypatch)
    incoming = _git_out(staging, "rev-parse", "incoming")
    out = asyncio.run(leerie.integrate_wave(
        ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS,
        EFFORTS))
    assert out == ["feat-001"]
    head = _git_out(staging, "rev-parse", "HEAD")
    assert leerie._integration_commit(staging, "feat-001") == head
    assert _git_out(staging, "log", "-1", "--format=%s") == \
        "leerie: integrate feat-001"
    assert _git_out(staging, "rev-parse", "HEAD^1", "HEAD^2").split() == [
        pre, incoming]
    assert (staging / "app.py").read_text() == "x = 3\n"
    if not message.startswith("leerie:"):
        assert message in _git_out(staging, "log", "-1", "--format=%b")
    assert st.data["integration_gate"]["feat-001"]["merge_commit_sha"] == head
