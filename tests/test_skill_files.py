"""Asserts that the judge-llm-batch and llm-self-heal SKILL.md files exist
with the correct frontmatter name slugs."""
from __future__ import annotations

import re
from pathlib import Path

import pytest


_REPO_ROOT = Path(__file__).parent.parent


def _parse_frontmatter_name(path: Path) -> str:
    text = path.read_text()
    if not text.startswith("---"):
        raise ValueError(f"{path}: missing opening ---")
    end = text.index("---", 3)
    block = text[3:end]
    for line in block.splitlines():
        if line.startswith("name:"):
            return line.split(":", 1)[1].strip().strip('"').strip("'")
    raise ValueError(f"{path}: no name: field in frontmatter")


def test_judge_llm_batch_skill_exists():
    path = _REPO_ROOT / "skills" / "judge-llm-batch" / "SKILL.md"
    assert path.is_file(), f"Missing: {path}"


def test_llm_self_heal_skill_exists():
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    assert path.is_file(), f"Missing: {path}"


def test_judge_llm_batch_frontmatter_name():
    path = _REPO_ROOT / "skills" / "judge-llm-batch" / "SKILL.md"
    assert _parse_frontmatter_name(path) == "judge-llm-batch"


def test_llm_self_heal_frontmatter_name():
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    assert _parse_frontmatter_name(path) == "llm-self-heal"


def test_judge_llm_batch_has_nonempty_body():
    path = _REPO_ROOT / "skills" / "judge-llm-batch" / "SKILL.md"
    text = path.read_text()
    # find the closing --- of frontmatter
    second_dash = text.index("---", 3)
    body = text[second_dash + 3:].strip()
    assert body, "skills/judge-llm-batch/SKILL.md has an empty body"


def test_llm_self_heal_has_nonempty_body():
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    text = path.read_text()
    second_dash = text.index("---", 3)
    body = text[second_dash + 3:].strip()
    assert body, "skills/llm-self-heal/SKILL.md has an empty body"


def test_judge_llm_batch_has_description():
    path = _REPO_ROOT / "skills" / "judge-llm-batch" / "SKILL.md"
    text = path.read_text()
    end = text.index("---", 3)
    block = text[3:end]
    has_desc = any(
        line.lstrip().startswith("description:") for line in block.splitlines()
    )
    assert has_desc, "skills/judge-llm-batch/SKILL.md frontmatter missing description:"


def test_llm_self_heal_has_description():
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    text = path.read_text()
    end = text.index("---", 3)
    block = text[3:end]
    has_desc = any(
        line.lstrip().startswith("description:") for line in block.splitlines()
    )
    assert has_desc, "skills/llm-self-heal/SKILL.md frontmatter missing description:"


def test_skill_files_do_not_hardcode_stale_cwd_relative_leerie_path():
    """calls.ndjson lives under the resolved state root
    (<state-root>/runs/<run-id>/), not a CWD-relative .leerie/runs/ path.
    Guards against the drift fixed by the '.leerie/runs' -> '<state-root>/runs'
    rewrite from silently reappearing."""
    stale = ".leerie/runs"
    for skill_dir in ("judge-llm-batch", "llm-self-heal"):
        path = _REPO_ROOT / "skills" / skill_dir / "SKILL.md"
        text = path.read_text()
        assert stale not in text, (
            f"{path} hardcodes the stale CWD-relative path '{stale}'; "
            "calls.ndjson lives under <state-root>/runs/<run-id>/, not a "
            "repo-relative .leerie/runs/ directory"
        )


def test_llm_self_heal_skill_does_not_reintroduce_agent_subagent_patch_step():
    """The patch-generation step must be a direct `claude -p` call to
    prompts/patch_generator.md (DESIGN.md §2 Constraint 1 — 'subagents
    cannot spawn subagents': workers are headless `claude -p` subprocess
    invocations, not in-session subagents; the Claude Code Agent tool is
    not available to the orchestrator and not used anywhere in this
    repo). Guards against the Agent-tool / subagent_type patch-generation
    drift fixed by bugfix-001 from silently reappearing."""
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    text = path.read_text()

    assert "subagent_type" not in text, (
        f"{path} references 'subagent_type'; the patch step must invoke "
        "prompts/patch_generator.md via a direct `claude -p` call, not an "
        "Agent-tool subagent (DESIGN.md §2 Constraint 1)"
    )

    end = text.index("---", 3)
    frontmatter = text[3:end]
    allowed_tools_block = frontmatter[frontmatter.index("allowed-tools:"):]
    assert "Agent" not in allowed_tools_block, (
        f"{path} lists 'Agent' in its allowed-tools block; this skill must "
        "not spawn an Agent-tool subagent for patch generation (DESIGN.md §2 Constraint 1)"
    )

    assert "patch_generator.md" in text, (
        f"{path} does not reference prompts/patch_generator.md; the patch "
        "step must be a `claude -p` call against that worker prompt "
        "(DESIGN.md §2 Constraint 1)"
    )


def test_llm_self_heal_skill_defaults_match_code_constants(leerie):
    """SKILL.md documents each heal-loop default annotated with its code
    constant name and claims 'All default values match IMPLEMENTATION.md
    §2'. Guards against a code-side default change (e.g.
    HEAL_SUCCESS_THRESHOLD_DEFAULT) silently leaving the skill documenting
    — and users expecting — behavior the loop no longer performs."""
    path = _REPO_ROOT / "skills" / "llm-self-heal" / "SKILL.md"
    text = path.read_text()

    # Numeric/plain-identifier defaults: "(default <val>,\s*`<CONST>`)"
    numeric_pattern = re.compile(
        r"\(default\s+([0-9.]+),\s*`([A-Z_]+)`\)"
    )
    matches = numeric_pattern.findall(text)
    expected_constants = {
        "HEAL_MAX_ROUNDS_DEFAULT",
        "HEAL_N_REPLAYS_DEFAULT",
        "HEAL_SUCCESS_THRESHOLD_DEFAULT",
        "HEAL_PLATEAU_WINDOW_DEFAULT",
        "HEAL_PLATEAU_DELTA_DEFAULT",
    }
    found_constants = {const for _, const in matches}
    assert found_constants == expected_constants, (
        f"{path}: expected default annotations for {expected_constants}, "
        f"found {found_constants}"
    )

    for raw_value, const_name in matches:
        assert hasattr(leerie, const_name), (
            f"{path} documents `{const_name}` but orchestrator/leerie.py "
            "has no such constant"
        )
        documented_value = float(raw_value)
        code_value = getattr(leerie, const_name)
        assert documented_value == pytest.approx(code_value), (
            f"{path} documents {const_name}=default {raw_value}, but "
            f"orchestrator/leerie.py defines {const_name}={code_value!r}"
        )

    # Model default: "(default `sonnet`, `MODEL_DEFAULT_PER_WORKER[\"heal\"]`)"
    model_match = re.search(
        r'\(default\s+`([a-z0-9_-]+)`,\s*'
        r'`MODEL_DEFAULT_PER_WORKER\["heal"\]`\)',
        text,
    )
    assert model_match, (
        f"{path}: expected a `(default `<model>`, "
        '`MODEL_DEFAULT_PER_WORKER["heal"]`)` annotation'
    )
    documented_model = model_match.group(1)
    code_model = leerie.MODEL_DEFAULT_PER_WORKER["heal"]
    assert documented_model == code_model, (
        f"{path} documents MODEL_DEFAULT_PER_WORKER[\"heal\"]=default "
        f"`{documented_model}`, but orchestrator/leerie.py defines it as "
        f"{code_model!r}"
    )
