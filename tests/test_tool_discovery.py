"""The tools a repository needs are read from it (DESIGN §6½ *The tools a
repository needs are read from it*).

mise installed what a repository pinned and nothing else, so a tool its
steps ran that no pin named was missing: a uv repository pinning nothing
ended unconverged on "`uv` is not on PATH" (lane LG, 0.65.0 and 0.67.0).
The `tool_discovery` worker reads the repository and names the tools; code
holds every name to mise's registry, writes the run's tools file and points
`MISE_GLOBAL_CONFIG_FILE` at it, beneath the repository's own pins.
"""
from __future__ import annotations

import asyncio
import os
import tomllib
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark as _landlock_mark, run)

REGISTRY = frozenset({"python", "uv", "go", "zig", "node", "poetry",
                      "erlang", "elixir"})


def _make_state(leerie, tmp_path):
    root = tmp_path / ".leerie"
    (root / "runs" / "_t" / "logs").mkdir(parents=True)
    st = leerie.State(root, "_t")
    st.data = {"task": "t", "worker_count": 0,
               "planning_worktree": str(tmp_path)}
    st.save()
    return st


@pytest.fixture()
def no_global_config(monkeypatch):
    monkeypatch.setenv("MISE_GLOBAL_CONFIG_FILE", "x")
    monkeypatch.delenv("MISE_GLOBAL_CONFIG_FILE")


def test_check_names_every_tool_the_registry_does_not_hold(leerie):
    issues = leerie._check_tool_discovery_output(
        {"tools": [{"tool": "uv", "version": "latest", "evidence": "a"},
                   {"tool": "pyhton", "version": "3", "evidence": "b"}]},
        REGISTRY)
    assert len(issues) == 1 and "'pyhton'" in issues[0], issues
    assert leerie._check_tool_discovery_output(
        {"tools": [{"tool": "go", "version": "1.22.3", "evidence": "x"}]},
        REGISTRY) == []


def test_tools_file_holds_registry_names_first_wins(leerie):
    text = leerie._mise_tools_toml(
        [{"tool": "go", "version": "1.22.3", "evidence": "go.mod:3"},
         {"tool": "go", "version": "1.21", "evidence": "dup"},
         {"tool": "pyhton", "version": "3", "evidence": "typo"},
         {"tool": "uv", "version": "latest", "evidence": "uv.lock"},
         {"tool": "zig", "version": ">=0.13", "evidence": "a range"}],
        REGISTRY)
    assert tomllib.loads(text) == {"tools": {"go": "1.22.3",
                                             "uv": "latest"}}


@pytest.mark.parametrize("version,ok", [
    ("1.22.3", True), ("3", True), ("latest", True), ("lts", True),
    ("0.13.0", True), ("^3.11", False), (">=3.11", False),
    ("~> 1.17", False), ("1.2 || 1.3", False), ("", False)])
def test_schema_version_is_mises_syntax_never_a_range(leerie, version, ok):
    import re
    assert bool(re.match(leerie.TOOL_VERSION_PATTERN, version)) is ok


def test_discovery_asks_again_and_writes_only_known_tools(
        leerie, tmp_path, monkeypatch, no_global_config):
    st = _make_state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_mise_registry_names", lambda: REGISTRY)
    prompts: list[str] = []
    answers = [
        {"tools": [{"tool": "pyhton", "version": "3", "evidence": "x"},
                   {"tool": "uv", "version": "latest", "evidence": "uv.lock"}]},
        {"tools": [{"tool": "python", "version": "3", "evidence": "x"},
                   {"tool": "uv", "version": "latest", "evidence": "uv.lock"}]},
    ]

    async def _fake(**kw):
        assert kw["schema_key"] == "tool_discovery"
        assert kw["autonomous"] is False
        assert kw["allowed_tools"] == leerie.INSPECT_TOOLS
        prompts.append(kw["user_prompt"])
        return answers[len(prompts) - 1]

    monkeypatch.setattr(leerie, "claude_p", _fake)
    caps = dict(leerie.DEFAULT_CAPS)
    asyncio.run(leerie._discover_tools(st, caps, {}, {}))
    assert len(prompts) == 2 and "pyhton" in prompts[1]
    path = st.run_dir / leerie.MISE_TOOLS_NAME
    assert tomllib.loads(path.read_text()) == {
        "tools": {"python": "3", "uv": "latest"}}
    assert os.environ["MISE_GLOBAL_CONFIG_FILE"] == str(path)
    prov = st.data["provision"]
    assert prov["tools_file"] == str(path)
    assert [t["tool"] for t in prov["tools"]] == ["python", "uv"]


def test_a_failed_worker_leaves_the_repositorys_own_pins(
        leerie, tmp_path, monkeypatch, no_global_config):
    st = _make_state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_mise_registry_names", lambda: REGISTRY)

    async def _boom(**kw):
        raise leerie.WorkerError("crashed")

    monkeypatch.setattr(leerie, "claude_p", _boom)
    asyncio.run(leerie._discover_tools(st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert st.data["provision"]["tools"] == []
    assert "tools_file" not in st.data["provision"]
    assert "MISE_GLOBAL_CONFIG_FILE" not in os.environ
    assert not (st.run_dir / leerie.MISE_TOOLS_NAME).exists()


def test_resume_points_mise_at_the_recorded_tools_file(leerie,
                                                       no_global_config):
    env: dict[str, str] = {}
    leerie._use_run_tools_file(env, "/r/mise-tools.toml")
    assert env == {"MISE_GLOBAL_CONFIG_FILE": "/r/mise-tools.toml"}
    leerie._use_run_tools_file(env, None)
    assert env == {"MISE_GLOBAL_CONFIG_FILE": "/r/mise-tools.toml"}


def test_resume_lays_the_recorded_mise_configuration(leerie, tmp_path,
                                                     no_global_config):
    st = _make_state(leerie, tmp_path)
    st.data["provision"] = {"tools_file": "/r/mise-tools.toml",
                            "override_file": "/r/mise-overrides.toml"}
    env: dict[str, str] = {}
    leerie._resume_provision_env(st, env)
    assert env == {"MISE_GLOBAL_CONFIG_FILE": "/r/mise-tools.toml",
                   "MISE_OVERRIDE_CONFIG_FILENAMES": "/r/mise-overrides.toml"}
    st.data["provision"] = {"recipe": []}
    env = {}
    leerie._resume_provision_env(st, env)
    assert env == {}


@_landlock_mark
def test_a_confined_command_and_a_worker_can_read_the_tools_file(
        leerie, run):
    """Under the real shim: the measurement grant and a worker's grant both
    let mise read `<run>/mise-tools.toml`."""
    run_dir = run["run_dir"]
    (run_dir / leerie.MISE_TOOLS_NAME).write_text('[tools]\n"uv" = "x"\n')
    view = run["wts"][f"{run_dir.name}-view-staging"]
    st = _St(run["user"], run_dir)
    g = leerie._measurement_grants(st, view)
    rc, tail = asyncio.run(leerie._run_streaming(
        ["cat", str(run_dir / leerie.MISE_TOOLS_NAME)], grants=g,
        cwd=str(view), timeout=60, verbosity="quiet", tail_lines=20))
    assert rc == 0 and '"uv" = "x"' in tail, tail
    wg = leerie._worker_grants(
        run_dir=run_dir, state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS,
        env=run["env"])
    assert str(run_dir / leerie.MISE_TOOLS_NAME) in wg.ro
