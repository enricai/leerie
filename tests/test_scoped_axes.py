"""Delta-proxy resolution for per-subtask conformance rounds.

DESIGN §9 *Per-subtask scope: a delta proxy, not the suite*. A proxy is a
cheap falsifier run once per subtask, backed by the canonical command run at
the base-health baseline and on the final integrated tree — it is deliberately
NOT a subset of the canonical command.

Measured on the run that motivated this: the median subtask touches 1 source
file, yet each conformer round ran all 990 test files at ~499 s. A scoped run
of the same repo averaged 23 s.
"""
from __future__ import annotations

import asyncio

import pytest

from tests.conftest import run_git_cwd_kw as _git


# --------------------------------------------------------------------------
# _render_scoped
# --------------------------------------------------------------------------

def test_files_are_shell_quoted(leerie):
    """THE FALSIFIABLE CASE. A naive `" ".join(files)` passes every other
    assertion in this file. Real paths in the motivating repo look like
    `src/app/[locale]/(app)/settings/general/page.test.tsx`."""
    out = leerie._render_scoped(
        "npx vitest related --run {files}",
        ["src/app/[locale]/(app)/general/page.test.tsx", "a b.ts", "x;rm -rf /"],
        "base")
    assert "'src/app/[locale]/(app)/general/page.test.tsx'" in out
    assert "'a b.ts'" in out
    assert "'x;rm -rf /'" in out


def test_base_is_substituted(leerie):
    assert leerie._render_scoped("npx vitest --changed {base}", [], "leerie/runs/x") \
        == "npx vitest --changed leerie/runs/x"


def test_empty_file_list_with_a_files_template_is_a_hard_skip(leerie):
    """Rendering the bare runner would run EVERYTHING — the exact inversion
    of the feature."""
    assert leerie._render_scoped("npx vitest related --run {files}", [],
                                 "base") is None


def test_non_empty_file_list_renders(leerie):
    """ANTI-VACUITY PARTNER: without this, the test above passes against an
    implementation that always returns None."""
    assert leerie._render_scoped("npx vitest related --run {files}",
                                 ["a.ts"], "base") is not None


@pytest.mark.parametrize("tmpl", ["", None])
def test_absent_template_yields_none(leerie, tmpl):
    assert leerie._render_scoped(tmpl, ["a.ts"], "base") is None


# --------------------------------------------------------------------------
# resolve_blt_scoped
# --------------------------------------------------------------------------

def test_vitest_is_inferred_from_its_config(leerie, tmp_path):
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    assert "related" in leerie.resolve_blt_scoped(tmp_path)["test"]


def test_jest_is_inferred_from_its_config(leerie, tmp_path):
    (tmp_path / "jest.config.js").write_text("module.exports={}\n")
    assert "--findRelatedTests" in leerie.resolve_blt_scoped(tmp_path)["test"]


def test_tsconfig_infers_a_build_proxy(leerie, tmp_path):
    (tmp_path / "tsconfig.json").write_text("{}\n")
    assert leerie.resolve_blt_scoped(tmp_path)["build"] == "npx tsc --noEmit"


def test_a_python_repo_infers_nothing(leerie, tmp_path):
    """No pytest inference on purpose: `{files}` for a repo whose changed
    file is `orchestrator/leerie.py` renders `pytest orchestrator/leerie.py`,
    which collects nothing. Such repos declare nothing and fall back to the
    canonical command — the honest outcome, rather than a template that looks
    right and selects nothing."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    assert leerie.resolve_blt_scoped(tmp_path) == {}


def test_config_declaration_beats_inference(leerie, tmp_path):
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    (tmp_path / ".leerie").mkdir()
    (tmp_path / ".leerie" / "config.toml").write_text(
        'test_scoped = "mytest {files}"\nbuild_scoped = "mybuild"\n')
    got = leerie.resolve_blt_scoped(tmp_path)
    assert got["test"] == "mytest {files}"
    assert got["build"] == "mybuild"


def test_no_lint_tier(leerie, tmp_path):
    """Lint was measured at 0.4h across a 51h run. Scoping it buys nothing
    and only adds a way to be wrong."""
    (tmp_path / "vitest.config.ts").write_text("export default {}\n")
    (tmp_path / "tsconfig.json").write_text("{}\n")
    assert "lint" not in leerie.resolve_blt_scoped(tmp_path)


# --------------------------------------------------------------------------
# _select_subtask_axes
# --------------------------------------------------------------------------

BLT = {"build": "next build", "lint": "biome check", "test": "vitest run"}


def test_scoped_mode_uses_the_proxy_and_labels_itself(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"},
        ["a.ts"], "base", "scoped")
    assert axes["tests"] == "npx vitest related --run a.ts"
    assert scope == "scoped"


def test_an_axis_without_a_proxy_falls_back_to_the_canonical_command(leerie):
    """Never silently skipped — assert the canonical STRING, not merely
    'not None'."""
    axes, _ = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"},
        ["a.ts"], "base", "scoped")
    assert axes["build"] == "next build"
    assert axes["lint"] == "biome check"


def test_scope_reports_full_when_no_proxy_actually_applied(leerie):
    """The label must not claim a narrowing that did not happen — the
    conformer prompt renders it verbatim."""
    axes, scope = leerie._select_subtask_axes(BLT, {}, ["a.ts"], "base",
                                              "scoped")
    assert scope == "full"
    assert axes["tests"] == "vitest run"


def test_empty_diff_falls_back_rather_than_running_everything(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"}, [], "base",
        "scoped")
    assert axes["tests"] == "vitest run"
    assert scope == "full"


def test_full_mode_ignores_available_proxies(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"}, ["a.ts"], "base",
        "full")
    assert axes["tests"] == "vitest run"
    assert scope == "full"


def test_off_mode_measures_nothing(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "x {files}"}, ["a.ts"], "base", "off")
    assert axes == {}
    assert scope == "off"


def test_the_tests_axis_reads_the_singular_command_key(leerie):
    """`resolve_blt` keys it `test`; the conformer keys it `tests`. A bare
    `blt.get("tests")` is always None and silently skips the suite."""
    axes, _ = leerie._select_subtask_axes(BLT, {}, [], "base", "full")
    assert axes["tests"] == "vitest run"


# --------------------------------------------------------------------------
# _changed_files
# --------------------------------------------------------------------------

def test_changed_files_survives_awkward_paths(leerie, tmp_path):
    """`-z`, not `splitlines()`: git C-quotes any path with a space or a
    non-ASCII byte in its default output, so the naive form returns a quoted
    literal that does not exist on disk. Asserting the path EXISTS is what
    discriminates."""
    d = tmp_path / "wt"
    d.mkdir()
    _git("init", "-q", cwd=d)
    _git("config", "user.email", "t@e.com", cwd=d)
    _git("config", "user.name", "t", cwd=d)
    (d / "base.txt").write_text("x\n")
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "base", cwd=d)
    _git("branch", "basebranch", cwd=d)
    for name in ["a file.ts", "café.ts", "plain.ts"]:
        (d / name).write_text("y\n")
    _git("add", "-A", cwd=d)
    _git("commit", "-qm", "add", cwd=d)

    got = asyncio.run(leerie._changed_files(str(d), "basebranch"))
    assert set(got) == {"a file.ts", "café.ts", "plain.ts"}
    for p in got:
        assert (d / p).exists(), f"{p!r} does not exist — path was C-quoted"


def test_changed_files_on_a_non_repo_is_empty(leerie, tmp_path):
    assert asyncio.run(leerie._changed_files(str(tmp_path), "base")) == []
