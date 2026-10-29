"""Named test files run through the runner's direct form, a diff through its
impact analysis (DESIGN §9 *Operands that are already test files take the
runner's direct form*).

One scoped template once served both kinds of operand, so every caller
holding test files — acceptance sets, the re-drive self-check, B* reruns,
discovery, the coupled tests replacing a forbidden suite — ran `vitest
related --run <test files>`, which walks the whole suite's module graph
before running the files it was handed (measured 1.9-5x slower on barnacle).
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import types
from pathlib import Path

import pytest

VITEST_RUN = "npx vitest run {test_files} --passWithNoTests"
VITEST_RELATED = "npx vitest related --run {files} --passWithNoTests"
JEST_RUN = "npx jest --runTestsByPath {test_files} --passWithNoTests"
JEST_RELATED = "npx jest --findRelatedTests {files} --passWithNoTests"


def _repo(tmp_path: Path, config: str, toml: str | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "src").mkdir(parents=True)
    (root / config).write_text("export default {};\n")
    (root / "src" / "a.ts").write_text("export const a = 1;\n")
    (root / "src" / "a.test.ts").write_text('import { a } from "./a";\n')
    if toml is not None:
        (root / ".leerie").mkdir()
        (root / ".leerie" / "config.toml").write_text(toml)
    return root


def _st(root: Path, data: dict | None = None):
    return types.SimpleNamespace(
        repo_root=root, run_dir=root / ".run", run_id="r1",
        data={"verbosity": "quiet", **(data or {})}, save=lambda: None)


@pytest.mark.parametrize("config,direct,affected", [
    ("vitest.config.ts", VITEST_RUN, VITEST_RELATED),
    ("vitest.workspace.ts", VITEST_RUN, VITEST_RELATED),
    ("jest.config.js", JEST_RUN, JEST_RELATED)])
def test_inferred_js_runner_has_a_direct_and_an_affected_form(
        leerie, tmp_path, config, direct, affected):
    root = _repo(tmp_path, config)
    assert leerie.resolve_test_files_scoped(root) == direct
    # The per-subtask proxy keeps the impact analysis: its operands are a
    # diff, and the tests a source change affects are what it wants.
    assert leerie.resolve_blt_scoped(root)["test"] == affected


def test_declared_templates_resolve_in_order(leerie, tmp_path):
    """Inputs disagree, so a read of the wrong key cannot pass."""
    both = _repo(tmp_path / "1", "vitest.config.ts",
                 'test_scoped = "x related {files}"\n'
                 'test_files_scoped = "y run {test_files}"\n')
    assert leerie.resolve_test_files_scoped(both) == "y run {test_files}"
    assert leerie.resolve_blt_scoped(both)["test"] == "x related {files}"
    # A declared test_scoped is the operator's command, used as written
    # over test files too — never swapped for an inferred form.
    only = _repo(tmp_path / "2", "vitest.config.ts",
                 'test_scoped = "x related {files}"\n')
    assert leerie.resolve_test_files_scoped(only) == "x related {files}"


def test_runners_without_impact_analysis_share_one_template(
        leerie, tmp_path):
    root = tmp_path / "repo"
    (root / ".leerie").mkdir(parents=True)
    (root / ".leerie" / "config.toml").write_text('test = "pytest -q"\n')
    assert leerie.resolve_test_files_scoped(root) == "pytest -q {test_files}"
    assert leerie.resolve_blt_scoped(root)["test"] == "pytest -q {test_files}"
    assert leerie.resolve_test_files_scoped(tmp_path / "nothing") is None


def test_acceptance_cmd_renders_the_direct_form(leerie, tmp_path):
    root = _repo(tmp_path, "vitest.config.ts")
    st = _st(root)
    assert leerie._acceptance_cmd(st, ["src/a.test.ts", "src/b.test.ts"]) \
        == "npx vitest run src/a.test.ts src/b.test.ts --passWithNoTests"
    # A source file never reaches the direct form, where vitest would read
    # it as a filter rather than "tests importing it".
    assert leerie._acceptance_cmd(st, ["src/a.ts"]) is None
    assert leerie._acceptance_cmd(st, ["src/a.test.ts", "src/a.ts"]) is None
    jroot = _repo(tmp_path / "j", "jest.config.js")
    assert leerie._acceptance_cmd(_st(jroot), ["src/a.test.ts"]) == (
        "npx jest --runTestsByPath src/a.test.ts --passWithNoTests")


# A whole-suite forbid of the runner the direct form names.
VITEST_SUITE = [{"command": "npx vitest run", "scope": "whole_suite",
                 "test_roots": ["src"]}]
JEST_SUITE = [{"command": "npx jest", "scope": "whole_suite",
               "test_roots": ["src"]}]


@pytest.mark.parametrize("cmd,forb,hit", [
    ("npx vitest run src/a.test.ts --passWithNoTests", VITEST_SUITE, False),
    ("npx vitest run --passWithNoTests", VITEST_SUITE, True),
    ("npx vitest --passWithNoTests", VITEST_SUITE, True),
    ("npx jest --runTestsByPath src/a.test.ts --passWithNoTests",
     JEST_SUITE, False),
    ("npx jest --runTestsByPath --passWithNoTests", JEST_SUITE, True)])
def test_forbidden_gate_reads_the_direct_form_as_scoped(leerie, cmd, forb,
                                                         hit):
    """`vitest run <files>` names its files as operands: scoped, not a way
    around a whole-suite forbid; the same words with no file are the suite."""
    assert (leerie._forbidden_command_hit(cmd, forb) is not None) is hit


def _capture_runs(leerie, monkeypatch) -> list[str]:
    ran: list[str] = []

    async def fake(argv, **kw):
        ran.append(argv[-1] if argv[:2] == ["bash", "-c"] else " ".join(argv))
        return 1, ""
    monkeypatch.setattr(leerie, "_run_streaming", fake)
    return ran


def test_acceptance_file_runs_the_direct_form_past_a_suite_forbid(
        leerie, tmp_path, monkeypatch):
    """Call site `_run_acceptance_file`, executed: the command it launches is
    the direct form, and the task's whole-suite forbid lets it through."""
    root = _repo(tmp_path, "vitest.config.ts")
    st = _st(root, {"process_constraints": {"entries": VITEST_SUITE}})
    ran = _capture_runs(leerie, monkeypatch)
    v = asyncio.run(leerie._run_acceptance_file(
        st, dict(leerie.DEFAULT_CAPS), str(root), "src/a.test.ts",
        tmp_path / "l.log", "t"))
    assert v is False and len(ran) == 1, ran
    assert ran[0].startswith("npx vitest run src/a.test.ts"), ran
    assert "related" not in ran[0]


def test_capture_failing_ids_over_files_runs_the_direct_form(
        leerie, tmp_path, monkeypatch):
    """Call site `_capture_failing_test_ids(files=…)` — B* reruns, discovery
    repeats: the canonical command is replaced by the direct form."""
    root = _repo(tmp_path, "vitest.config.ts")
    st = _st(root)
    ran = _capture_runs(leerie, monkeypatch)
    asyncio.run(leerie._capture_failing_test_ids(
        str(root), "npx vitest run", st, dict(leerie.DEFAULT_CAPS),
        label="t", log_path=tmp_path / "l.log", timeout=5.0,
        files=["src/a.test.ts"]))
    assert ran and all(r.startswith("npx vitest run src/a.test.ts")
                       for r in ran), ran


def test_parse_probe_runs_the_direct_form(leerie, tmp_path, monkeypatch):
    """Call site `_acceptance_parse_probe`, executed."""
    root = _repo(tmp_path, "vitest.config.ts")
    st = _st(root)
    ran = _capture_runs(leerie, monkeypatch)
    asyncio.run(leerie._acceptance_parse_probe(
        st, dict(leerie.DEFAULT_CAPS), str(root), "src/a.test.ts",
        ["src/a.test.ts"], tmp_path / "l.log", "t"))
    assert len(ran) == 1 and ran[0].startswith("npx vitest run "), ran
    assert "related" not in ran[0]


def test_only_the_diff_proxy_reads_the_affected_template(leerie):
    """Every other call site holds test files and must render through
    `_acceptance_cmd` / `resolve_test_files_scoped`. Derived from the
    source: a new caller of `resolve_blt_scoped` must be one whose operands
    are a diff, and has to be added here deliberately."""
    tree = ast.parse(inspect.getsource(leerie))
    callers: set[str] = set()
    for fn in ast.walk(tree):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "resolve_blt_scoped"):
                callers.add(fn.name)
    assert callers == {"_run_conformance_phase", "phase_execute",
                       "resolve_test_files_scoped"}, callers
