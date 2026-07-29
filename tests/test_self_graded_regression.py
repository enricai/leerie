"""Regression fixtures for the four proven-harm cases the independent-
verification change (DESIGN §8) exists to catch. Each freezes the incident
shape and proves the corresponding gate now FAILS it (catches the defect) where
the self-graded ~9/10 confidence previously shipped it `complete`. Each has a
paired clean variant that passes without a false gate.

1. Shallow-falsifier implementer diff (bugfix-002) → conformer solution_defects.
2. 0-vs-10 classification (landing page as documentation) → classification_judge.
3. Tag-channel dangle (dropped provider) → check_plan_wiring.
4. --break-system-packages-omitting recipe → provision_judge.
"""
from __future__ import annotations

import asyncio

import pytest


def _caps(leerie):
    caps = dict(leerie.DEFAULT_CAPS)
    caps["judgment_check_rounds"] = 3
    return caps


def _state(leerie, tmp_path, extra=None, run_id="test-regression-aaa"):
    root = tmp_path / ".leerie"
    (root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(root, run_id)
    st.data = {"task": "test", "worker_count": 0}
    st.data.update(extra or {})
    st.save()
    return st


# === Fixture 1: shallow-falsifier implementer diff (bugfix-002) =============
# The real subtask self-graded solution 9.5 with shallow falsifiers and shipped
# three latent defects. The conformer's independent solution_defects axis now
# catches them.

_BUGFIX_002_DEFECTS = {
    "solution_defects": [
        {"kind": "decoy_or_shortcut",
         "concrete_case": "clicks results[0] (index-0 decoy) instead of the "
                          "ranked candidate",
         "where": "locator.ts:88",
         "why_ships_a_defect": "picks the wrong element when the decoy is "
                               "first"},
        {"kind": "wrong_selector",
         "concrete_case": "selector 'div:has(> .x)' is invalid in this engine",
         "where": "locator.ts:102",
         "why_ships_a_defect": "throws at runtime"},
        {"kind": "missing_guard",
         "concrete_case": "no null-check before .observe() on a blind site",
         "where": "observer.ts:40",
         "why_ships_a_defect": "TypeError on the third call site"},
    ],
}


def test_bugfix_002_shallow_diff_is_caught_by_completeness_gate(leerie):
    """The incident shape produces actionable defects — the gate would send it
    back, not ship it complete."""
    actionable = leerie.actionable_solution_defects(_BUGFIX_002_DEFECTS)
    assert len(actionable) == 3
    # And the retry feedback names each concrete site (mandatory criteria).
    note = leerie._format_solution_defects(actionable)
    assert "locator.ts:88" in note
    assert "observer.ts:40" in note


def test_bugfix_002_clean_variant_does_not_gate(leerie):
    """A genuinely complete diff (empty solution_defects) ships unimpeded."""
    assert leerie.actionable_solution_defects({"solution_defects": []}) == []


# === Fixture 2: 0-vs-10 classification (landing page as documentation) ======

def test_landing_page_as_docs_is_caught_by_classification_gate(
        leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path, {"categories": ["documentation"]})

    async def fake_judge(**kwargs):
        # The independent verifier sees the missing feature category.
        return {"categories_reviewed": ["documentation"],
                "miscategorizations": [{
                    "kind": "missing_category",
                    "category": "feature-implementation",
                    "concrete_work_evidence": "task ships a landing page (UI "
                                              "feature); docs-only set produces "
                                              "no page",
                }], "rationale": "primary deliverable has no category"}

    async def never_converge_classify(*a, **k):
        pass  # classifier keeps returning docs-only → gate exhausts and dies

    monkeypatch.setattr(leerie, "claude_p", fake_judge)
    monkeypatch.setattr(leerie, "phase_classify", never_converge_classify)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_classification_gate(
            "build the landing page", st, _caps(leerie), False,
            {"classification_judge": "opus"},
            {"classification_judge": "medium"}))


def test_correct_classification_does_not_gate(leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path,
                {"categories": ["feature-implementation"]})

    async def fake_judge(**kwargs):
        return {"categories_reviewed": ["feature-implementation"],
                "miscategorizations": [], "rationale": "covers the work"}

    monkeypatch.setattr(leerie, "claude_p", fake_judge)
    asyncio.run(leerie.phase_classification_gate(
        "build the landing page", st, _caps(leerie), False,
        {"classification_judge": "opus"}, {"classification_judge": "medium"}))
    assert st.data["classification_coverage_gate"]["miscategorizations"] == []


# === Fixture 3: tag-channel dangle (dropped provider) =======================

def test_dropped_provider_dangle_is_caught_by_check_plan_wiring(leerie):
    """The satisfied-probe/drop tag-channel dangle: a surviving subtask
    requires a tag whose only provider was dropped. check_plan_wiring catches
    it deterministically before validate_plan burns the plan spend."""
    # feat-001 (the provider of 'shared-schema') was dropped; feat-002 survives
    # requiring it.
    plan = {
        "feat-002": {"id": "feat-002", "depends_on": [],
                     "requires": [{"tag": "shared-schema", "extent": "in_plan"}],
                     "provides": []},
    }
    issues = leerie.check_plan_wiring(plan)
    assert len(issues) == 1
    assert "shared-schema" in issues[0]
    assert "feat-002" in issues[0]


def test_wired_plan_has_no_dangle(leerie):
    plan = {
        "feat-001": {"id": "feat-001", "depends_on": [], "requires": [],
                     "provides": ["shared-schema"]},
        "feat-002": {"id": "feat-002", "depends_on": [],
                     "requires": [{"tag": "shared-schema", "extent": "in_plan"}],
                     "provides": []},
    }
    assert leerie.check_plan_wiring(plan) == []


# === Fixture 4: --break-system-packages-omitting recipe =====================

_BROKEN_PIP_RECIPE = [{"kind": "install",
                       "command": ["pip", "install", "-r", "requirements.txt"],
                       "working_dir": ".", "timeout_s": 300}]


def test_break_system_packages_omission_is_caught_by_provision_gate(
        leerie, tmp_path, monkeypatch):
    st = _state(leerie, tmp_path,
                {"provision": {"recipe": _BROKEN_PIP_RECIPE,
                               "mise_versions": {}}})

    async def fake_judge(**kwargs):
        return {"recipe_reviewed": True, "recipe_failures": [{
            "kind": "missing_break_system_packages",
            "command": "pip install -r requirements.txt",
            "concrete_reason": "Debian 13 externally-managed system Python "
                               "(PEP 668) fails without --break-system-packages",
            "fix": "pip install --break-system-packages -r requirements.txt",
        }], "rationale": "would fail on the image"}

    async def never_fix_provision(*a, **k):
        pass  # recipe keeps omitting the flag → gate exhausts and dies

    monkeypatch.setattr(leerie, "claude_p", fake_judge)
    monkeypatch.setattr(leerie, "phase_provision", never_fix_provision)
    with pytest.raises(SystemExit):
        asyncio.run(leerie.phase_provision_gate(
            tmp_path, st, _caps(leerie),
            {"provision_judge": "opus"}, {"provision_judge": "medium"}))


def test_correct_recipe_does_not_gate(leerie, tmp_path, monkeypatch):
    fixed = [{"kind": "install",
              "command": ["pip", "install", "--break-system-packages", "-r",
                          "requirements.txt"],
              "working_dir": ".", "timeout_s": 300}]
    st = _state(leerie, tmp_path,
                {"provision": {"recipe": fixed, "mise_versions": {}}})

    async def fake_judge(**kwargs):
        return {"recipe_reviewed": True, "recipe_failures": [],
                "rationale": "runs on the image"}

    monkeypatch.setattr(leerie, "claude_p", fake_judge)
    asyncio.run(leerie.phase_provision_gate(
        tmp_path, st, _caps(leerie),
        {"provision_judge": "opus"}, {"provision_judge": "medium"}))
    assert st.data["provision_recipe_gate"]["recipe_failures"] == []
