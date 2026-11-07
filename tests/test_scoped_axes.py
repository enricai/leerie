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
        ["src/app/[locale]/(app)/general/page.test.tsx", "a b.ts",
         "x;rm -rf .ts"],
        "base")
    assert "'src/app/[locale]/(app)/general/page.test.tsx'" in out
    assert "'a b.ts'" in out
    assert "'x;rm -rf .ts'" in out


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


def test_a_python_repo_infers_pytest_over_test_files(leerie, tmp_path):
    """Rewritten (A4): pytest IS inferred now, but only over `{test_files}`.
    `{files}` would render `pytest orchestrator/leerie.py`, which collects
    nothing; `{test_files}` renders nothing for that diff and the axis
    defers to the whole-tree pass, and runs the touched tests otherwise."""
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    scoped = leerie.resolve_blt_scoped(tmp_path)
    assert scoped == {"test": "pytest {test_files}"}
    assert leerie._render_scoped(scoped["test"], ["orchestrator/leerie.py"],
                                 "HEAD") is None
    assert leerie._render_scoped(
        scoped["test"], ["orchestrator/leerie.py", "tests/test_x.py"],
        "HEAD") == "pytest tests/test_x.py"


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


def test_only_the_tests_axis_is_ever_deferred(leerie):
    """DESIGN §9: absence means deferred, never full -- for `tests`. `build`
    has no proxy here and keeps its canonical STRING (a compiled repo's
    cheapest per-subtask signal); `lint` has no proxy tier and keeps its
    own. `tests`, with a resolvable proxy, is scoped."""
    axes, _ = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"},
        ["a.ts"], "base", "scoped")
    assert axes["build"] == "next build"
    assert axes["lint"] == "biome check"
    assert axes["tests"].startswith("npx vitest related")
    assert leerie._deferred_axes(axes, BLT, "scoped") == []


def test_scope_reports_deferred_when_no_proxy_actually_applied(leerie):
    """The label must not claim a narrowing that did not happen — the
    conformer prompt renders it verbatim. Nothing narrowed and the tests
    were deferred, so the honest label is `deferred`; build and lint still
    ran their canonical commands, which the per-axis lines state."""
    axes, scope = leerie._select_subtask_axes(BLT, {}, ["a.ts"], "base",
                                              "scoped")
    assert scope == "deferred"
    assert "tests" not in axes
    assert axes["build"] == "next build"
    assert axes["lint"] == "biome check"


def test_empty_diff_defers_rather_than_running_everything(leerie):
    """An empty diff must neither render a bare runner nor fall back to the
    canonical suite."""
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "npx vitest related --run {files}"}, [], "base",
        "scoped")
    assert "tests" not in axes
    assert "vitest run" not in axes.values()
    assert scope == "deferred"


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


# --------------------------------------------------------------------------
# An impact-analysis runner is handed only its own module sources (DESIGN §9)
# --------------------------------------------------------------------------

RELATED = {"test": "npx vitest related --run {files} --passWithNoTests"}
DIRECT = "npx vitest run {test_files} --passWithNoTests"
# The live barnacle diff that ran all 1,299 test files for 4,716 s.
CONFIG_ONLY = [".github/workflows/build.yml", ".github/workflows/test.yml",
               ".nvmrc", "Dockerfile", "package.json"]


@pytest.mark.parametrize("files", [
    CONFIG_ONLY, ["package.json"], [".github/workflows/build.yml"],
    ["pnpm-lock.yaml", "docs/README.md"]])
def test_a_diff_with_no_module_source_runs_no_related_selection(leerie,
                                                                files):
    axes, scope = leerie._select_subtask_axes(
        BLT, RELATED, files, "base", "scoped", direct=DIRECT)
    assert "tests" not in axes, axes
    assert scope == "deferred"
    assert not any("related" in c or c == BLT["test"] for c in axes.values())


def test_an_impact_runner_runs_only_the_named_tests_per_subtask(leerie):
    """DESIGN §9 *A subtask runs the tests it names*: the tests a change
    affects run once, on the integrated tree. On barnacle `related` was
    400-890 of 1,092 files on every round of every subtask."""
    axes, scope = leerie._select_subtask_axes(
        BLT, RELATED, CONFIG_ONLY + ["src/a.ts", "src/b.test.tsx",
                                     "web/c.vue"], "base", "scoped",
        direct=DIRECT, named_tests=["src/n.test.ts", "src/a.ts"])
    assert axes["tests"] == ("npx vitest run src/b.test.tsx src/n.test.ts "
                             "--passWithNoTests")
    assert scope == "scoped"


def test_a_source_only_diff_naming_no_test_is_deferred(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, RELATED, ["src/a.ts"], "base", "scoped", direct=DIRECT)
    assert "tests" not in axes
    assert scope == "deferred"
    assert leerie._deferred_axes(axes, BLT, "scoped") == ["tests"]


def test_a_non_impact_runner_is_as_it_was(leerie):
    """pytest's template is over test files already; nothing changes."""
    axes, scope = leerie._select_subtask_axes(
        BLT, {"test": "pytest {test_files}"}, ["a.py", "tests/test_a.py"],
        "base", "scoped", direct="pytest {test_files}")
    assert axes["tests"] == "pytest tests/test_a.py"
    assert scope == "scoped"


def test_jest_impact_template_is_filtered_too(leerie):
    out = leerie._render_scoped(
        "npx jest --findRelatedTests {files} --passWithNoTests",
        ["package.json", "src/a.js"], "base")
    assert out == "npx jest --findRelatedTests src/a.js --passWithNoTests"


def test_a_non_impact_files_template_keeps_every_file(leerie):
    """Only a runner whose module graph decides what runs is filtered: a
    declared `{files}` template for any other command gets the diff."""
    assert leerie._render_scoped("lint-staged {files}", ["package.json"],
                                 "base") == "lint-staged package.json"


@pytest.mark.parametrize("config", ["vitest.config.ts", "vite.config.mjs",
                                    "pkg/vitest.workspace.ts"])
def test_a_runner_config_change_runs_only_the_named_test_files(leerie,
                                                               config):
    axes, scope = leerie._select_subtask_axes(
        BLT, RELATED, [config, "src/a.ts", "src/a.test.ts"], "base",
        "scoped", direct=DIRECT, named_tests=["src/b.test.ts", "src/x.ts"])
    assert scope == "runner_config_changed"
    assert axes["tests"] == ("npx vitest run src/a.test.ts src/b.test.ts "
                             "--passWithNoTests")


def test_a_runner_config_change_with_no_named_test_is_deferred(leerie):
    axes, scope = leerie._select_subtask_axes(
        BLT, RELATED, ["vitest.config.ts"], "base", "scoped",
        direct=DIRECT, named_tests=[])
    assert "tests" not in axes
    assert scope == "runner_config_changed"
    assert leerie._deferred_axes(axes, BLT, "scoped") == ["tests"]
