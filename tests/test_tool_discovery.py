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
from pathlib import Path

import pytest

from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark as _landlock_mark, run)

def _tools_toml(text: str) -> dict:
    """The `[tools]` table of a tools file, read line by line (the file is
    `"<name>" = "<version>"` lines; `tomllib` is not in Python 3.10)."""
    out: dict = {}
    section = None
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("["):
            section = line.strip("[]")
            out.setdefault(section, {})
            continue
        k, v = (x.strip().strip('"') for x in line.split("=", 1))
        out[section][k] = v
    return out


async def _registry(*_a, **_k):
    return REGISTRY


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
    entries = leerie._tools_file_entries(
        [{"tool": "go", "version": "1.22.3", "evidence": "go.mod:3"},
         {"tool": "go", "version": "1.21", "evidence": "dup"},
         {"tool": "pyhton", "version": "3", "evidence": "typo"},
         {"tool": "uv", "version": "latest", "evidence": "uv.lock"},
         {"tool": "zig", "version": ">=0.13", "evidence": "a range"}],
        REGISTRY)
    assert entries == {"go": "1.22.3", "uv": "latest"}
    assert _tools_toml(leerie._mise_tools_toml(
        {"go": "1.22.3", "uv": "0.9.1"})) == {
            "tools": {"go": "1.22.3", "uv": "0.9.1"}}


# What mise resolves each stated version to, as `mise latest` prints it.
# Every stated version differs from its resolution except the exact one, so
# a tools file holding the stated versions cannot pass for the resolved.
_RESOLVED = {"python@3": "3.15.2", "uv@latest": "0.9.1",
             "poetry@latest": "2.1.4", "go@1.22.3": "1.22.3"}


def _fake_latest(asked: list[str]):
    async def _latest(st, tool, version):
        asked.append(f"{tool}@{version}")
        return _RESOLVED.get(f"{tool}@{version}", ""), "no such version"
    return _latest


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
    monkeypatch.setattr(leerie, "_mise_registry_names", _registry)
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
    asked: list[str] = []
    monkeypatch.setattr(leerie, "_mise_latest", _fake_latest(asked))
    caps = dict(leerie.DEFAULT_CAPS)
    asyncio.run(leerie._discover_tools(st, caps, {}, {}))
    assert len(prompts) == 2 and "pyhton" in prompts[1]
    path = st.run_dir / leerie.MISE_TOOLS_NAME
    assert asked == ["python@3", "uv@latest"]
    assert _tools_toml(path.read_text()) == {
        "tools": {"python": "3.15.2", "uv": "0.9.1"}}
    assert os.environ["MISE_GLOBAL_CONFIG_FILE"] == str(path)
    prov = st.data["provision"]
    assert prov["tools_file"] == str(path)
    assert [t["tool"] for t in prov["tools"]] == ["python", "uv"]


def test_the_tools_file_holds_the_exact_version_mise_resolves(
        leerie, tmp_path, monkeypatch, no_global_config):
    """DESIGN §6½ *A tool the run names is installed at an exact version*:
    mise runs a request that is not exact (`latest`, `3`) from its alias
    directory, and Poetry's own environment built there reads as one it is
    already inside. The file names the version mise resolved, never the
    request; an exact one is resolved too and stays as it was."""
    st = _make_state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_mise_registry_names", _registry)

    async def _fake(**kw):
        return {"tools": [
            {"tool": "python", "version": "3", "evidence": "pyproject"},
            {"tool": "poetry", "version": "latest", "evidence": "poetry.lock"},
            {"tool": "go", "version": "1.22.3", "evidence": "go.mod"}]}

    monkeypatch.setattr(leerie, "claude_p", _fake)
    asked: list[str] = []
    monkeypatch.setattr(leerie, "_mise_latest", _fake_latest(asked))
    asyncio.run(leerie._discover_tools(st, dict(leerie.DEFAULT_CAPS), {}, {}))
    text = (st.run_dir / leerie.MISE_TOOLS_NAME).read_text()
    assert _tools_toml(text) == {"tools": {
        "python": "3.15.2", "poetry": "2.1.4", "go": "1.22.3"}}
    assert asked == ["python@3", "poetry@latest", "go@1.22.3"]
    # The worker's answer is recorded as it stated it.
    assert [t["version"] for t in st.data["provision"]["tools"]] == [
        "3", "latest", "1.22.3"]


def test_a_version_mise_resolves_to_nothing_stops_the_run(
        leerie, tmp_path, monkeypatch, no_global_config, capsys):
    st = _make_state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_mise_registry_names", _registry)

    async def _fake(**kw):
        return {"tools": [
            {"tool": "python", "version": "3", "evidence": "a"},
            {"tool": "zig", "version": "9.99", "evidence": "b"}]}

    monkeypatch.setattr(leerie, "claude_p", _fake)
    monkeypatch.setattr(leerie, "_mise_latest", _fake_latest([]))
    with pytest.raises(SystemExit) as exc:
        asyncio.run(leerie._discover_tools(
            st, dict(leerie.DEFAULT_CAPS), {}, {}))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "zig@9.99" in err and "no such version" in err, err
    assert not (st.run_dir / leerie.MISE_TOOLS_NAME).exists()
    assert "MISE_GLOBAL_CONFIG_FILE" not in os.environ


@_landlock_mark
def test_mise_latest_runs_confined_and_reads_what_mise_prints(
        leerie, run, tmp_path, monkeypatch):
    """The real `_mise_latest` through the real shim, with a `mise` that
    answers as mise 2026.5.4 does: the version for a request it resolves,
    and nothing, exit 0, for one it cannot."""
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    mise = bindir / "mise"
    mise.write_text('#!/bin/sh\n'
                    'case "$1 $2" in\n'
                    '  "latest python@3") echo 3.15.2 ;;\n'
                    '  "latest zig@9.99") ;;\n'
                    '  *) echo "unexpected: $*" >&2; exit 2 ;;\n'
                    'esac\n')
    mise.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}:{os.environ['PATH']}")
    monkeypatch.setenv("MISE_DATA_DIR", str(tmp_path / "mise-data"))
    (tmp_path / "mise-data").mkdir()
    st = _St(run["user"], run["run_dir"])
    st.data["planning_worktree"] = str(
        run["wts"][f"{run['run_dir'].name}-view-staging"])
    assert asyncio.run(leerie._mise_latest(st, "python", "3"))[0] == "3.15.2"
    assert asyncio.run(leerie._mise_latest(st, "zig", "9.99"))[0] == ""
    got, err = asyncio.run(leerie._mise_latest(st, "go", "1"))
    assert got == "" and "unexpected: latest go@1" in err, err


def test_a_failed_worker_leaves_the_repositorys_own_pins(
        leerie, tmp_path, monkeypatch, no_global_config):
    st = _make_state(leerie, tmp_path)
    monkeypatch.setattr(leerie, "_mise_registry_names", _registry)

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
    st.data["provision"] = {"tools_file": "/r/mise-tools.toml"}
    env: dict[str, str] = {}
    leerie._resume_provision_env(st, env)
    assert env == {"MISE_GLOBAL_CONFIG_FILE": "/r/mise-tools.toml"}
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
