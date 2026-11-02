"""A kill during provisioning is redone on resume (review round 19 M3;
DESIGN §6 *Resumable planning*, "A checkpoint covers only what ran before
it is saved").

`plans_after_classify` is saved right after classify. Provisioning used to
sit inside the block that checkpoint guards, after the save, so a kill
anywhere in it resumed past the block with no setup hook run and no recipe,
and the run ended `done` having installed nothing. Provisioning now runs
after that block, each step keyed on its own result: `phase_provision` on
`provision.recipe`, the gate on `provision.gate`.

Driven through the real `_run_phases` on the resume harness of
`tests/test_resume_planning_reentry.py`, every phase stubbed and counted.
"""
from __future__ import annotations

from tests.test_resume_planning_reentry import (_args, _caps, _drive,
                                                _make_state, _stub_common,
                                                run_dirs)  # noqa: F401


def _resume(leerie, monkeypatch, run_dirs, data: dict) -> dict:
    calls: dict = {}
    _stub_common(leerie, monkeypatch, calls)

    async def gate(*a, **kw):
        calls["phase_provision_gate"] = calls.get(
            "phase_provision_gate", 0) + 1
    monkeypatch.setattr(leerie, "phase_provision_gate", gate)
    st = _make_state(leerie, run_dirs, {
        "task": "test task", "worker_count": 3,
        "categories": ["bug-fixing"], "plans_after_classify": [], **data})
    _drive(leerie, _args(), _caps(leerie), run_dirs, st)
    return calls


def test_a_kill_before_the_recipe_provisions_on_resume(
        leerie, monkeypatch, run_dirs):
    """The round's case: killed before the setup hook, so no recipe."""
    calls = _resume(leerie, monkeypatch, run_dirs, {})
    assert "phase_classify" not in calls
    assert calls.get("phase_provision") == 1
    assert calls.get("phase_provision_gate") == 1


def test_a_kill_after_the_recipe_runs_only_the_gate(
        leerie, monkeypatch, run_dirs):
    calls = _resume(leerie, monkeypatch, run_dirs, {
        "provision": {"recipe": [{"kind": "pip", "argv": ["pip"]}],
                      "sh_hook_ran": True}})
    assert "phase_provision" not in calls
    assert calls.get("phase_provision_gate") == 1


def test_finished_provisioning_is_not_redone(leerie, monkeypatch, run_dirs):
    calls = _resume(leerie, monkeypatch, run_dirs, {
        "provision": {"recipe": [], "gate": "skipped"}})
    assert "phase_provision" not in calls
    assert "phase_provision_gate" not in calls


def test_the_gate_records_its_result(leerie, tmp_path):
    """Every way the gate ends but the fatal one leaves a result, so the
    resume above can skip it."""
    class _St:
        def __init__(self):
            self.data = {"provision": {"recipe": []}}

        def save(self):
            pass
    import asyncio
    st = _St()
    asyncio.run(leerie.phase_provision_gate(tmp_path, st, {}, {}, {}))
    assert st.data["provision"]["gate"] == "skipped"
    assert st.data["provision"]["recipe"] == []
