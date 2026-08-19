"""Tests for `_format_provision_recipe_section` — the prompt-injection
helper that hands the persisted provision recipe to implementer and
conformer workers.

The function is small and pure (string in, string out) but it sits on
the contract between phase_provision (which detects the recipe) and
the workers (which execute it in their worktrees). A subtle drift
here — e.g. silently dropping `build` entries, or rendering the
wrong audience-specific framing — would propagate to every worker.
"""
from __future__ import annotations

import pytest


PNPM_INSTALL = {
    "kind": "install",
    "command": ["pnpm", "install", "--frozen-lockfile"],
    "working_dir": ".",
    "timeout_s": 1800,
}
GO_DOWNLOAD = {
    "kind": "install",
    "command": ["go", "mod", "download"],
    "working_dir": ".",
    "timeout_s": 600,
}
NONE_ENTRY = {"kind": "none", "command": [], "working_dir": ".",
              "timeout_s": 0}


def test_empty_recipe_returns_none(leerie):
    assert leerie._format_provision_recipe_section(
        [], audience="implementer") is None


def test_all_none_recipe_returns_none(leerie):
    """Docs-only recipes only carry a `kind: none` entry; workers
    should see no injected section."""
    assert leerie._format_provision_recipe_section(
        [NONE_ENTRY], audience="implementer") is None
    assert leerie._format_provision_recipe_section(
        [NONE_ENTRY], audience="conformer") is None


def test_implementer_audience_renders_advisory_framing(leerie):
    out = leerie._format_provision_recipe_section(
        [PNPM_INSTALL], audience="implementer")
    assert out is not None
    assert "PROVISION_RECIPE:" in out
    assert "Decide whether your subtask needs them" in out
    # The command itself is verbatim.
    assert "pnpm install --frozen-lockfile" in out
    # The cwd + timeout metadata is rendered.
    assert "(cwd: ., timeout: 1800s)" in out


def test_conformer_audience_emphasizes_pre_build_install(leerie):
    out = leerie._format_provision_recipe_section(
        [PNPM_INSTALL], audience="conformer")
    assert out is not None
    assert "PROVISION_RECIPE:" in out
    # Conformer framing since the handover (DESIGN §9): the orchestrator
    # applies the recipe itself before it measures build/lint/tests, so the
    # block is no longer a "run these before BUILD_CMD" instruction. It is
    # shown because a *targeted* command of the conformer's own may still
    # need the deps present.
    assert "BLT_RESULTS:" in out
    assert "targeted" in out
    assert "BUILD_CMD" not in out and "TEST_CMD" not in out, (
        "the conformer is no longer handed raw axis command names")


def test_polyglot_recipe_renders_every_install_entry(leerie):
    """A polyglot repo (e.g. Rails-with-frontend, Go-with-Node) emits
    multiple install entries. With baked ecosystems, only non-baked entries
    appear (pnpm offline relink is kept; go mod download is filtered)."""
    out = leerie._format_provision_recipe_section(
        [PNPM_INSTALL, GO_DOWNLOAD], audience="implementer")
    assert out is not None
    assert "pnpm install --frozen-lockfile" in out
    # Go is baked, so go mod download should be filtered out
    assert "go mod download" not in out
    # Only pnpm remains, so it's numbered as 1
    assert "1. pnpm install --frozen-lockfile" in out


def test_none_entries_are_skipped_in_mixed_recipe(leerie):
    """A recipe with a `none` entry alongside real installs renders
    only the real installs (and renumbers them)."""
    out = leerie._format_provision_recipe_section(
        [NONE_ENTRY, PNPM_INSTALL], audience="implementer")
    assert out is not None
    assert "1. pnpm install --frozen-lockfile" in out
    # The `none` entry must not appear under any rendering.
    for line in out.splitlines():
        assert "none" not in line.lower() or "PROVISION_RECIPE" in line


def test_unknown_audience_raises(leerie):
    """Defensive check — a typo in the call site shouldn't silently
    fall back to a default framing."""
    with pytest.raises(ValueError, match="unknown audience"):
        leerie._format_provision_recipe_section(
            [PNPM_INSTALL], audience="planner")


def test_all_baked_ecosystem_recipe_returns_none(leerie):
    """A recipe whose only install is a fully-baked ecosystem (Python)
    filters down to nothing — the whole point of the bake, and distinct
    from `test_empty_recipe_returns_none` (which starts with no entries
    at all rather than filtering every entry away)."""
    pip_install = {
        "kind": "install", "command": ["pip", "install", "-r", "requirements.txt"],
        "working_dir": ".", "timeout_s": 300,
    }
    assert leerie._format_provision_recipe_section(
        [pip_install], audience="implementer") is None


# --- _is_baked_ecosystem_command: the filter itself, in isolation --------

def test_is_baked_ecosystem_command_empty_command_is_false(leerie):
    assert leerie._is_baked_ecosystem_command([]) is False


@pytest.mark.parametrize("cmd", [
    ["pip", "install", "-r", "requirements.txt"],
    ["pip3", "install", "foo"],
    ["uv", "sync"],
    ["poetry", "install"],
    ["pipenv", "install"],
    ["python", "-m", "pip", "install", "foo"],
    ["python3", "-m", "pip", "install", "foo"],
])
def test_is_baked_ecosystem_command_python_variants(leerie, cmd):
    assert leerie._is_baked_ecosystem_command(cmd) is True


def test_is_baked_ecosystem_command_python_dash_m_without_pip_is_false(leerie):
    # `-m` present but not followed by `pip` — must not false-positive.
    assert leerie._is_baked_ecosystem_command(
        ["python", "-m", "venv", ".venv"]) is False


def test_is_baked_ecosystem_command_ruby(leerie):
    assert leerie._is_baked_ecosystem_command(["bundle", "install"]) is True


def test_is_baked_ecosystem_command_rust(leerie):
    assert leerie._is_baked_ecosystem_command(["cargo", "fetch"]) is True


def test_is_baked_ecosystem_command_go(leerie):
    assert leerie._is_baked_ecosystem_command(["go", "mod", "download"]) is True


def test_is_baked_ecosystem_command_node_is_false(leerie):
    # Node/pnpm is the documented irreducible residual — never baked.
    assert leerie._is_baked_ecosystem_command(
        ["pnpm", "install", "--frozen-lockfile"]) is False
