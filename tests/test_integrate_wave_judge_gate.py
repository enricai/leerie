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
