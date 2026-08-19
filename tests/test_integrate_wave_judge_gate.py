"""Tests for the integration_judge gate in integrate_wave (DESIGN §8
*Independent adversarial verification*): after a successful integrator merge
commit (post check_merge_committed), invoke integration_judge to attack for
behavioral breakage. Detect-and-die single pass: an integrator cannot
mechanically fix a semantic finding without re-resolving the conflict.
"""
from __future__ import annotations

import asyncio
import inspect
from pathlib import Path
from unittest.mock import AsyncMock

import pytest


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
        """The judge (via the shared `_run_integration_judge_gate` helper —
        see the "Integration gate resume" refactor) is invoked after
        check_merge_committed passes, before appending to integrated."""
        src = inspect.getsource(leerie.integrate_wave)
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        assert 'schema_key="integration_judge"' in gate_src
        # The helper call must sit between check_merge_committed and the
        # LAST integrated.append(sid) line (after the judge block) in
        # integrate_wave's own source.
        i_merge_check = src.index("check_merge_committed(staging)")
        i_helper_call = src.index("await _run_integration_judge_gate(",
                                   i_merge_check)
        i_append = src.rindex("integrated.append(sid)")
        assert i_merge_check < i_helper_call < i_append

    def test_uses_run_checked_loop(self, leerie):
        """integration_judge is invoked via _run_checked_loop for bounded
        fresh-session retry on WorkerError."""
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        judge_idx = gate_src.index('schema_key="integration_judge"')
        assert "_run_checked_loop(" in gate_src
        # The loop should be nearby the judge invocation
        loop_idx = gate_src.index("_run_checked_loop(", judge_idx)
        assert loop_idx > judge_idx

    def test_is_detect_and_die_no_re_drive(self, leerie):
        """The integration gate is detect-and-die, single pass — it does NOT
        re-drive the integrator (cannot mechanically fix a semantic finding),
        i.e. it passes no make_feedback_prompt to _run_checked_loop for the
        judge invocation."""
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        # There is exactly one _run_checked_loop call in this helper (the
        # judge's — the integrator's own loop lives in integrate_wave, which
        # DOES pass make_feedback_prompt).
        judge_def_idx = gate_src.index("def _check_integration(judge_result: dict)")
        judge_loop_idx = gate_src.index("_run_checked_loop(", judge_def_idx)
        # Find the end of that _run_checked_loop call
        loop_end = gate_src.index(")", judge_loop_idx + 500)
        judge_loop_block = gate_src[judge_loop_idx:loop_end]
        # This loop should NOT have make_feedback_prompt
        assert "make_feedback_prompt=" not in judge_loop_block

    def test_dies_on_concrete_defect(self, leerie):
        """A non-empty concrete-defect result die()s with the defect named."""
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        # After the judge invocation, there must be a die() on defects
        judge_idx = gate_src.index('schema_key="integration_judge"')
        after_judge = gate_src[judge_idx:]
        assert "die(" in after_judge
        assert "integration gate found behavioral defect" in after_judge

    def test_degrades_on_worker_error(self, leerie):
        """A WorkerError on every round degrades: merge preserved (no revert),
        since check_merge_committed already ran."""
        gate_src = inspect.getsource(leerie._run_integration_judge_gate)
        judge_idx = gate_src.index('schema_key="integration_judge"')
        after_judge = gate_src[judge_idx:]
        # Must check for judge_result is None and degrade
        assert "if judge_result is None:" in after_judge
        assert "degrading" in after_judge
        # Must NOT revert/abort the merge on None. The None branch is a
        # single `if` block that returns immediately — bound it precisely
        # by that `return`, not by the next `else:`/`die(` text (both of
        # which appear later, in this helper's clean/gating-defect path,
        # and a prose comment mentioning `die()ing` also falls inside a
        # loosely-bounded window).
        none_branch_start = after_judge.index("if judge_result is None:")
        none_branch_end = after_judge.index("return", none_branch_start)
        none_branch = after_judge[none_branch_start:none_branch_end]
        assert "merge --abort" not in none_branch
        assert "die(" not in none_branch

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
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                   capture_output=True)
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
        if "rev-parse" in cmd:
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
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert integrator_called, "integrator should have been invoked"
    assert judge_called, "integration_judge should have been invoked"
    assert "feat-001" in out, "clean integration should succeed"


def test_concrete_defect_dies(leerie, tmp_path, monkeypatch):
    """A non-empty concrete defect result die()s with the defect named."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    # Create a minimal git repo
    import subprocess
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                   capture_output=True)
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
            return {"merge_reviewed": True, "defects": [{
                "kind": "dropped_change",
                "concrete_scenario": "side A's validation was silently dropped",
                "location": "src/auth.py:42",
                "why_broken": "input no longer validated, security hole"
            }], "rationale": "found breakage"}
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
        if "rev-parse" in cmd:
            mock.stdout = "abc123\n"
        elif "show" in cmd:
            mock.stdout = "Merge commit\n\ndiff\n"
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
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "check_integrator_commit", fake_check_integrator_commit)

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())


def test_worker_error_degrades_preserves_merge(leerie, tmp_path, monkeypatch):
    """A WorkerError on every round degrades: merge preserved, no die()."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = leerie_dir / "worktrees" / "staging"
    staging.mkdir(parents=True)

    import subprocess
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                   capture_output=True)
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
        if "rev-parse" in cmd:
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
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                   capture_output=True)
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
        if "rev-parse" in cmd:
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
    subprocess.run(["git", "init"], cwd=str(staging), check=True,
                   capture_output=True)
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


def test_defect_persisted_to_state_before_die(leerie, tmp_path, monkeypatch):
    """(a) integrate_wave writes integration_gate/integration_defects[sid]
    BEFORE die()ing — the incident shape: a committed merge, a behavioral
    defect, and a state.json that must survive the die() so a resume can
    read it back."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete", "intent": "add feature",
                           "criteria_results": []}}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integrator":
            return {"status": "resolved", "resolution_summary": "merged",
                    "confidence": {"resolution": 9.0, "basis": "clean"}}
        elif kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [{
                "kind": "dropped_change",
                "concrete_scenario": "side A's validation was silently dropped",
                "location": "src/auth.py:42",
                "why_broken": "input no longer validated"
            }], "rationale": "found breakage"}
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
        mock.stdout = "abc123\n" if "rev-parse" in cmd else "Merge\n\ndiff\n"
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)
    monkeypatch.setattr(leerie, "check_merge_committed",
                        AsyncMock(return_value=None))
    monkeypatch.setattr(leerie, "check_integrator_commit",
                        AsyncMock(return_value=None))

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, tmp_path / ".leerie", _caps(leerie),
            st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())

    # The state was persisted BEFORE the die() fired.
    entry = st.data["integration_gate"]["feat-001"]
    assert entry["accepted"] is False
    assert any("dropped_change" in d for d in entry["defects"])
    assert st.data["integration_defects"]["feat-001"] == entry["defects"]


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


def test_resume_still_dies_when_defect_not_accepted(
        leerie, tmp_path, monkeypatch):
    """A resume that re-invokes the judge and gets the SAME defect back
    must still die() — accept-integration is the only way past it."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    _staging_repo(leerie_dir)

    st.data["integration_gate"] = {
        "feat-001": {"defects": ["INTEGRATION_DEFECT (dropped_change) x"],
                     "advisories": [], "merge_commit_sha": "abc123",
                     "accepted": False}}
    st.save()

    results = {"feat-001": {"status": "complete"}}

    async def fake_claude_p(**kwargs):
        if kwargs.get("schema_key") == "integration_judge":
            return {"merge_reviewed": True, "defects": [{
                "kind": "dropped_change",
                "concrete_scenario": "still broken",
                "location": "src/auth.py:42",
                "why_broken": "still not validated"
            }], "rationale": "still broken"}
        return {}

    async def fake_run_proc(cmd, cwd=None):
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = "abc123\n" if "rev-parse" in cmd else "Merge\n\ndiff\n"
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())
    assert st.data["integration_gate"]["feat-001"]["accepted"] is False


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


def test_integrator_crash_rescues_work_aborts_and_dies(leerie, tmp_path, monkeypatch):
    """An integrator that crashes every round (WorkerError) is infrastructure,
    not a verdict: its in-progress resolution is rescued to a ref BEFORE the
    merge is aborted, `blocked[sid]` names the rescue, and the run dies
    naming the rescue ref."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete"}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1  # conflict -> spawn integrator
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_claude_p(**kwargs):
        # Every round of the integrator's _run_checked_loop raises
        # WorkerError, so ires ends up None.
        raise leerie.WorkerError("session killed")

    rescue_calls = []

    async def fake_rescue(staging_path, sid, run_id):
        rescue_calls.append(sid)
        return "refs/leerie/rescue/test-int-gate-aaa/feat-001"

    abort_calls = []

    async def fake_run_proc(cmd, cwd=None):
        if cmd[:2] == ["git", "merge"] and "--abort" in cmd:
            abort_calls.append(True)
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "_rescue_integrator_work", fake_rescue)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    caps = _caps(leerie)
    caps["judgment_check_rounds"] = 1

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, caps, st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())

    assert rescue_calls == ["feat-001"]
    assert abort_calls, "merge must be aborted after the rescue capture"
    assert "feat-001" in st.data.get("blocked", {})
    assert "rescued" in st.data["blocked"]["feat-001"]


def test_resolved_but_merge_not_committed_aborts_and_dies(leerie, tmp_path, monkeypatch):
    """An integrator claiming 'resolved' while the worktree is still
    mid-merge is a lie (the integrator-side analogue of
    check_branch_has_commits) — the merge is aborted and the run dies."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete"}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_claude_p(**kwargs):
        return {"status": "resolved", "resolution_summary": "merged",
                "confidence": {"resolution": 9.0, "basis": "clean"}}

    async def fake_check_merge_committed(staging_path):
        return "MERGE_HEAD still present — merge was never committed"

    abort_calls = []

    async def fake_run_proc(cmd, cwd=None):
        if cmd[:2] == ["git", "merge"] and "--abort" in cmd:
            abort_calls.append(True)
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "check_merge_committed", fake_check_merge_committed)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())

    assert abort_calls


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
        if "rev-parse" in cmd:
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

    async def _run():
        return await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    out = asyncio.run(_run())

    assert out == ["feat-001"]
    assert "feat-001" in st.data.get("integrator_warnings", {})
    assert "resolved conflict" in st.data["integrator_warnings"]["feat-001"]


def test_design_conflict_verdict_aborts_and_dies(leerie, tmp_path, monkeypatch):
    """An integrator verdict of 'design-conflict' (could not produce a
    correct merge) aborts the in-progress merge and terminates, naming the
    diagnosis."""
    st = _state(leerie, tmp_path)
    leerie_dir = tmp_path / ".leerie"
    staging = _staging_repo(leerie_dir)

    results = {"feat-001": {"status": "complete"}}

    async def fake_run_script(script, sid, run_id):
        mock = AsyncMock()
        mock.returncode = 1
        mock.stderr = ""
        mock.stdout = ""
        return mock

    async def fake_claude_p(**kwargs):
        return {"status": "design-conflict",
                "diagnosis": "the two subtasks disagree on the API shape"}

    abort_calls = []

    async def fake_run_proc(cmd, cwd=None):
        if cmd[:2] == ["git", "merge"] and "--abort" in cmd:
            abort_calls.append(True)
        mock = AsyncMock()
        mock.returncode = 0
        mock.stdout = ""
        mock.stderr = ""
        return mock

    monkeypatch.setattr(leerie, "_run_script", fake_run_script)
    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)
    monkeypatch.setattr(leerie, "run_proc", fake_run_proc)

    async def _run():
        await leerie.integrate_wave(
            ["feat-001"], results, leerie_dir, _caps(leerie), st, MODELS, EFFORTS)

    with pytest.raises(SystemExit):
        asyncio.run(_run())

    assert abort_calls


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
        if "rev-parse" in cmd:
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
        if "rev-parse" in cmd:
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
