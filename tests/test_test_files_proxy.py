"""The `{test_files}` delta-proxy tier (DESIGN §9).

`vitest related` / `jest --findRelatedTests` take SOURCE files and resolve
dependents through the runner's own module graph, so `{files}` is handed to
them whole. pytest has no such mechanism: it collects under the paths it is
given, where a non-test path is an ERROR rather than a no-op. Measured against
this repo:

    pytest orchestrator/leerie.py                      -> rc 5 (nothing collected)
    pytest docs/DESIGN.md                              -> rc 4 (no collectors)
    pytest docs/DESIGN.md tests/test_blt_semaphore.py  -> rc 4  <-- decides the design
    pytest tests/test_blt_semaphore.py                 -> rc 0

That third line is why filtering, not abandoning, is the fix: one non-test
path poisons an otherwise-valid invocation, and a real subtask diff nearly
always mixes docs/source with its tests.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re

import pytest


# --------------------------------------------------------------------------
# _is_test_file
# --------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "tests/test_x.py",
    "tests/subdir/test_x.py",
    "test/foo.py",
    "spec/models/user_spec.rb",
    "src/components/Button.test.tsx",
    "src/lib/thing.spec.ts",
    "internal/handler_test.go",
    "./tests/test_x.py",                      # a leading ./ must not defeat it
])
def test_recognised_as_tests(leerie, path):
    assert leerie._is_test_file(path) is True


@pytest.mark.parametrize("path", [
    "docs/DESIGN.md",
    "orchestrator/leerie.py",
    "scripts/remote/collect-subtrees.sh",
    "README.md",
    "src/lib/thing.ts",
    "requirements.txt",
    ".leerie/config.toml",
])
def test_not_recognised_as_tests(leerie, path):
    """The negative controls ARE the point: every one of these appeared in a
    real recovered subtask diff alongside a test file, and each would have
    poisoned the invocation."""
    assert leerie._is_test_file(path) is False


def test_hidden_file_is_not_mangled_by_prefix_stripping(leerie):
    """`lstrip("./")` strips ANY leading '.' or '/' character, so `.hidden_test.py`
    becomes `hidden_test.py` and `../x` becomes `x`. Only a literal `./` prefix
    may be removed."""
    assert leerie._is_test_file(".config.yml") is False
    assert leerie._is_test_file("..hidden/notes.md") is False


def test_declared_globs_REPLACE_the_builtins(leerie):
    """A repo that has to override is saying the built-in guess is WRONG, not
    incomplete — so a declared list must not silently keep matching the
    built-in shapes underneath it."""
    globs = ["src/**/__tests__/*"]
    assert leerie._is_test_file("src/a/__tests__/x.ts", globs) is True
    # built-in shape, deliberately NOT matched once globs are declared
    assert leerie._is_test_file("tests/test_x.py", globs) is False


# --------------------------------------------------------------------------
# _render_scoped: the {test_files} tier
# --------------------------------------------------------------------------

def test_filters_a_mixed_diff_to_tests_only(leerie):
    """The shape of a real recovered subtask diff (bugfix-001 of run
    7aa46cce): docs + source + script + one test file."""
    out = leerie._render_scoped(
        "python3 -m pytest {test_files} -q",
        ["docs/IMPLEMENTATION.md", "orchestrator/leerie.py",
         "scripts/remote/collect-subtrees.sh", "tests/test_strict_mcp_config.py"],
        "base")
    assert out == "python3 -m pytest tests/test_strict_mcp_config.py -q"
    for poison in ("docs/IMPLEMENTATION.md", "orchestrator/leerie.py",
                   "collect-subtrees.sh"):
        assert poison not in out


def test_no_test_file_returns_None_not_a_bare_runner(leerie):
    """THE LOAD-BEARING CASE. `files` is NON-empty, so the existing
    empty-list guard does not fire — but every member is a non-test path.
    Rendering here yields a bare `pytest`, which runs EVERYTHING: the exact
    inversion the `{files}` rule already forbids, arrived at by a different
    route. None makes `_select_subtask_axes` fall back to canonical instead.

    This is the shape of the 2-in-36 real subtask diffs that carried no test
    file: bugfix-002 of run 7aa46cce (docs + orchestrator), and bugfix-004 of
    the same run (orchestrator/leerie.py alone)."""
    out = leerie._render_scoped(
        "python3 -m pytest {test_files} -q",
        ["docs/DESIGN.md", "docs/IMPLEMENTATION.md", "orchestrator/leerie.py"],
        "base")
    assert out is None


def test_empty_changed_set_returns_None(leerie):
    assert leerie._render_scoped(
        "python3 -m pytest {test_files} -q", [], "base") is None


def test_test_files_are_shell_quoted(leerie):
    """Same falsifiable case the `{files}` tier carries: a naive
    `" ".join` passes every other assertion here."""
    out = leerie._render_scoped(
        "python3 -m pytest {test_files}",
        ["src/app/[locale]/(app)/page.test.tsx", "tests/a b_test.py"],
        "base")
    assert "'src/app/[locale]/(app)/page.test.tsx'" in out
    assert "'tests/a b_test.py'" in out


def test_base_is_still_substituted(leerie):
    out = leerie._render_scoped(
        "pytest {test_files} --base {base}", ["tests/test_x.py"], "origin/main")
    assert out.endswith("--base origin/main")


def test_declared_globs_reach_the_render(leerie):
    """Wiring guard: the globs must actually be consulted by `_render_scoped`,
    not merely accepted as a parameter."""
    files = ["src/a/__tests__/x.ts", "src/a/x.ts"]
    assert leerie._render_scoped("pytest {test_files}", files, "b") is None
    out = leerie._render_scoped(
        "pytest {test_files}", files, "b", ["src/**/__tests__/*"])
    assert out == "pytest src/a/__tests__/x.ts"


# --------------------------------------------------------------------------
# regression guard: the {files} tier must be byte-identical
# --------------------------------------------------------------------------

@pytest.mark.parametrize("tmpl", [
    "npx vitest related --run {files} --passWithNoTests",
    "npx jest --findRelatedTests {files} --passWithNoTests",
])
def test_shipped_templates_are_unchanged_by_the_new_tier(leerie, tmpl):
    """The two inferred templates take SOURCE files on purpose. If
    `{test_files}` filtering leaked into `{files}`, vitest/jest would be
    handed test paths and resolve a different (much smaller) set."""
    files = ["src/lib/thing.ts", "src/lib/other.ts"]
    out = leerie._render_scoped(tmpl, files, "base")
    assert "src/lib/thing.ts" in out and "src/lib/other.ts" in out


def test_both_placeholders_in_one_template(leerie):
    out = leerie._render_scoped(
        "run {files} -- only {test_files}",
        ["src/x.ts", "tests/test_x.py"], "base")
    assert out == "run src/x.ts tests/test_x.py -- only tests/test_x.py"


# --------------------------------------------------------------------------
# _select_subtask_axes: the fallback that makes the narrowing non-silent
# --------------------------------------------------------------------------

def test_axis_falls_back_to_canonical_when_no_test_file_changed(leerie):
    """An unrenderable proxy must yield the CANONICAL command string, not be
    dropped and not be left empty — asserting `!= None` would pass against a
    silently skipped axis."""
    axes, scope = leerie._select_subtask_axes(
        {"build": "", "lint": "", "test": "pytest"},
        {"test": "python3 -m pytest {test_files} -q"},
        ["orchestrator/leerie.py"], "base", "scoped")
    assert axes["tests"] == "pytest"
    assert scope == "full"          # nothing actually narrowed


def test_axis_uses_the_proxy_when_a_test_file_changed(leerie):
    axes, scope = leerie._select_subtask_axes(
        {"build": "", "lint": "", "test": "pytest"},
        {"test": "python3 -m pytest {test_files} -q"},
        ["orchestrator/leerie.py", "tests/test_x.py"], "base", "scoped")
    assert axes["tests"] == "python3 -m pytest tests/test_x.py -q"
    assert scope == "scoped"


# --------------------------------------------------------------------------
# spec parity
# --------------------------------------------------------------------------

def test_documented_in_the_spec_table(leerie):
    """Both halves of the new surface need an IMPLEMENTATION.md mention in the
    same commit as the code (the convention `test_blt_memo.py` and
    `test_plan_snapshot_wiring.py` carry). The placeholder is what a repo
    types into `test_scoped`; the config key is how it overrides the built-in
    test-path shapes. Either drifting silently is what this guards."""
    impl = (pathlib.Path(leerie.__file__).resolve().parent.parent
            / "docs" / "IMPLEMENTATION.md").read_text(encoding="utf-8")
    for token in ("{test_files}", "test_file_globs", "resolve_test_file_globs",
                  "_warn_unknown_placeholder_once"):
        assert token in impl, token


def test_the_three_new_test_files_have_a_table_row(leerie):
    """Every sibling covering this area has one (`test_scoped_axes.py`,
    `test_blt_semaphore.py`, `test_conformance_clean_delta.py`). A file with
    no row is invisible to anyone reading the spec to find out what is
    already covered — which is how duplicate test files get written."""
    impl = (pathlib.Path(leerie.__file__).resolve().parent.parent
            / "docs" / "IMPLEMENTATION.md").read_text(encoding="utf-8")
    for name in ("test_test_files_proxy.py", "test_scoped_proxy_corpus.py",
                 "test_scoped_degrade_warning.py"):
        assert re.search(rf"^\| `{re.escape(name)}` \|", impl, re.M), name


# --------------------------------------------------------------------------
# unknown placeholder: fail safe rather than shipping a literal brace
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _reset_placeholder_latch(leerie):
    leerie._unknown_placeholder_warned = False
    yield
    leerie._unknown_placeholder_warned = False


def test_unknown_placeholder_returns_None(leerie):
    """THE LOAD-BEARING CASE. Version skew is the realistic cause:
    `.leerie/config.toml` is committed to the repo while the orchestrator runs
    from the install clone, so a config naming a placeholder added after that
    install reaches an older renderer. Without this guard the brace survives
    to the shell and `pytest '{test_files}'` exits 4 — every subtask RED."""
    assert leerie._render_scoped(
        "python3 -m pytest {future_thing} -q", ["tests/test_x.py"], "b") is None


@pytest.mark.parametrize("cmd", [
    "npx vitest related --run {files} --passWithNoTests",
    "python3 -m pytest {test_files} -q",
    "run {files} --base {base}",
])
def test_known_placeholders_still_render(leerie, cmd):
    """Anti-vacuity: a guard that rejected everything would pass the test
    above while disabling every working proxy."""
    assert leerie._render_scoped(cmd, ["tests/test_x.py"], "b") is not None


@pytest.mark.parametrize("cmd", [
    "pytest {test_files} && echo ${HOME}/done",     # shell var, uppercase
    "pytest {test_files} && echo ${home}/done",     # shell var, lowercase
    "pytest {test_files}; for i in {1..3}; do :; done",   # brace expansion
    "pytest {test_files} | awk '{print $1}'",       # awk with a field ref
])
def test_legitimate_braces_are_not_mistaken_for_placeholders(leerie, cmd):
    """Each of these is a real shape a proxy command can carry. A guard that
    tripped on them would silently disable the axis it was meant to protect —
    the same over-correction the empty-list rule avoids."""
    assert leerie._render_scoped(cmd, ["tests/test_x.py"], "b") is not None


@pytest.mark.parametrize("tmpl,files,want", [
    ("npx vitest related --run {files}",
     ["src/{locale}/page.test.ts"], "src/{locale}/page.test.ts"),
    ("python3 -m pytest {test_files} -q",
     ["tests/{id}_test.py"], "tests/{id}_test.py"),
])
def test_a_changed_path_containing_braces_still_renders(leerie, tmpl, files, want):
    """REGRESSION. The guard must scan the author's TEMPLATE, never the
    rendered command. A changed-file path may legitimately contain braces —
    `src/{locale}/…` is the brace-routing analogue of the
    `src/app/[locale]/(app)/…` path `shlex.quote` exists for in this very
    function — and a post-substitution scan reads it as an unknown
    placeholder: the proxy is disabled AND the warning misdiagnoses it as
    install skew, sending the operator to re-run install.sh for nothing."""
    out = leerie._render_scoped(tmpl, files, "base")
    assert out is not None
    assert want in out


def test_a_brace_path_does_not_emit_the_skew_warning(leerie, monkeypatch):
    """The misdiagnosis half, pinned separately: rendering correctly is not
    enough if the operator is still told their install is stale."""
    msgs = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: msgs.append(str(m)))
    leerie._render_scoped("npx vitest related --run {files}",
                          ["src/{locale}/page.test.ts"], "base")
    assert not [m for m in msgs if "does not know how to substitute" in m]


def test_the_warning_fires_once_and_names_the_token(leerie, monkeypatch):
    msgs = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: msgs.append(str(m)))
    for _ in range(4):
        leerie._render_scoped("pytest {future_thing}", ["tests/test_x.py"], "b")
    hits = [m for m in msgs if "does not know how to substitute" in m]
    assert len(hits) == 1
    assert "{future_thing}" in hits[0]
    assert "install.sh" in hits[0], "must name the remedy for the skew case"


def test_a_fully_substituted_template_does_not_warn(leerie, monkeypatch):
    """The partner without which the warning could fire on every render and
    still pass the test above."""
    msgs = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: msgs.append(str(m)))
    leerie._render_scoped("pytest {test_files} -q", ["tests/test_x.py"], "b")
    assert not [m for m in msgs if "does not know how to substitute" in m]


# --------------------------------------------------------------------------
# this repo's own committed declaration
# --------------------------------------------------------------------------

def test_this_repos_declared_proxy_actually_renders(leerie):
    """A typo'd placeholder in our OWN .leerie/config.toml is otherwise only
    discovered by a run going RED, which is the most expensive possible place
    to find it."""
    repo = pathlib.Path(leerie.__file__).resolve().parent.parent
    tmpl = leerie.resolve_blt_scoped(repo).get("test")
    assert tmpl, "this repo declares test_scoped; resolve_blt_scoped lost it"
    out = leerie._render_scoped(
        tmpl, ["orchestrator/leerie.py", "tests/test_scoped_axes.py"], "base")
    assert out is not None, f"declared template does not render: {tmpl!r}"
    assert "tests/test_scoped_axes.py" in out
    assert "orchestrator/leerie.py" not in out


def test_this_repos_proxy_falls_back_on_a_source_only_diff(leerie):
    repo = pathlib.Path(leerie.__file__).resolve().parent.parent
    tmpl = leerie.resolve_blt_scoped(repo).get("test")
    assert leerie._render_scoped(tmpl, ["orchestrator/leerie.py"], "base") is None


def test_documented_signature_matches_the_real_one(leerie):
    """The drift this file's audit found was invisible because nothing
    compared the two: IMPLEMENTATION.md spelled out
    `_select_subtask_axes(blt, scoped, files, base_ref, mode)` for months after
    the function grew a sixth parameter. Same discipline as
    `test_collect_subtrees_integrator_schema.py`, which whole-object-compares a
    schema this repo is forced to keep in two places — a spelled-out signature
    is a second copy of an interface and drifts exactly the same way."""
    impl = (pathlib.Path(leerie.__file__).resolve().parent.parent
            / "docs" / "IMPLEMENTATION.md").read_text(encoding="utf-8")
    m = re.search(r"`_select_subtask_axes\(([^`)]*)\)`", impl)
    assert m, "IMPLEMENTATION.md no longer spells out the signature"
    documented = [p.strip() for p in m.group(1).split(",") if p.strip()]
    real = list(inspect.signature(leerie._select_subtask_axes).parameters)
    assert documented == real, (
        f"IMPLEMENTATION.md documents {documented} but the function takes "
        f"{real} — update the spec in the same commit as the signature")


def test_substituted_placeholders_match_the_declared_set(leerie):
    """The probe loop and the substitutions below it are two views of one
    list. Derive the substituted set from the AST rather than restating it —
    PRs #180-#183 each replaced an enumeration with a derivation *after* a
    missed instance shipped, and this is the same shape.

    Both drift directions are harmful: a placeholder missing from
    `_SCOPED_PLACEHOLDERS` is rejected as unknown (with a warning blaming
    install skew), and one left in it after the substitution is removed
    reaches the shell literally — the failure the guard exists to prevent."""
    src = inspect.getsource(leerie._render_scoped)
    substituted = {
        n.value for n in ast.walk(ast.parse(src.lstrip()))
        if isinstance(n, ast.Constant) and isinstance(n.value, str)
        and re.fullmatch(r"\{[a-z_]+\}", n.value)
    }
    assert substituted, "the AST walk found no placeholder literals at all"
    assert substituted == set(leerie._SCOPED_PLACEHOLDERS), (
        f"_render_scoped substitutes {sorted(substituted)} but "
        f"_SCOPED_PLACEHOLDERS declares {sorted(leerie._SCOPED_PLACEHOLDERS)}")


def test_the_declared_set_is_consumed_by_the_probe(leerie):
    """Anti-vacuity partner: the constant existing is not the fix — the loop
    has to read it. A restored hand-written tuple would keep the test above
    passing while reintroducing the duplication."""
    src = inspect.getsource(leerie._render_scoped)
    assert "for known in _SCOPED_PLACEHOLDERS:" in src
