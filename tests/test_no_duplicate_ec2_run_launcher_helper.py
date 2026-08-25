"""Guards the single-owner `run_launcher` helper in tests/ec2_stub.py.

Once refactor-001 landed, no file under tests/ should redefine the
`(args, env) -> subprocess.CompletedProcess` shape of `_run_launcher`
that used to be duplicated across tests/test_auto_detect_run_runtime.py,
tests/test_ec2_launcher_kill.py, and tests/test_ec2_launcher_resume.py --
every call site should import `run_launcher` from tests/ec2_stub.py
instead (optionally wrapping it in a thin local function under a
different name, as those three files now do).

tests/test_group_state_dir_guard.py's own `_run_launcher(tmp_path, args,
env_extra=None, stub=None, stub_log=None)` is a genuinely distinct helper
(different purpose: injects a stub binary and returns a `.stub_log`-
augmented result) and is excluded by construction -- the scan below
matches only the specific two-positional-arg `(args, env)` shape, not
that five-parameter one.

The scan is NAME-INDEPENDENT (an AST walk over `def`s whose two
positional parameters are named `args`/`env`, not merely a literal
`_run_launcher(` match). test-002 widened it after
tests/test_ec2_launcher_readonly_verbs.py's own `_run(args, env)` -- a
byte-for-byte duplicate of the run_launcher shape under a different name
-- evaded the original literal-name regex entirely. A thin function whose
body is a single `return <call naming run_launcher>(...)` is exempted:
that is the "wraps it in a thin local function" delegation the module
docstring already sanctions (test_ec2_launcher_kill.py,
test_ec2_launcher_resume.py), not a reimplementation.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

_DUPLICATE_SHAPE_PARAM_NAMES = ("args", "env")


def _delegates_to_shared_run_launcher(body: list[ast.stmt]) -> bool:
    """True when a function body is a single `return <call>(...)` whose
    call target names `run_launcher` -- the sanctioned thin-alias shape,
    which must not be flagged as a duplicate."""
    if len(body) != 1:
        return False
    stmt = body[0]
    if not isinstance(stmt, ast.Return) or stmt.value is None:
        return False
    call = stmt.value
    if not isinstance(call, ast.Call):
        return False
    func = call.func
    if isinstance(func, ast.Name):
        name = func.id
    elif isinstance(func, ast.Attribute):
        name = func.attr
    else:
        return False
    return "run_launcher" in name


def _is_duplicate_shaped(node: ast.FunctionDef) -> bool:
    params = list(node.args.posonlyargs) + list(node.args.args)
    if len(params) != len(_DUPLICATE_SHAPE_PARAM_NAMES):
        return False
    if tuple(p.arg for p in params) != _DUPLICATE_SHAPE_PARAM_NAMES:
        return False
    return not _delegates_to_shared_run_launcher(node.body)


def _duplicate_shaped_files() -> set[str]:
    offenders: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _is_duplicate_shaped(node):
                offenders.add(rel)
    return offenders


# Legacy call sites not yet migrated to the shared `run_launcher` helper.
# This is a shrink-only allowlist: a migration subtask removes a file's
# local two-positional-arg duplicate-shaped helper, drops the file's name
# from this set in the same commit, and CI catches any new local
# redefinition of that shape outside this set.
#
# test-002 widened the scan above from a literal `_run_launcher` name
# match to the structural shape regardless of function name, and migrated
# tests/test_ec2_launcher_readonly_verbs.py's own differently-named
# `_run` duplicate onto the shared helper in the same change. The
# widening also surfaced two pre-existing, differently-named duplicates
# the old name-keyed regex could never see (`_run` in
# test_ec2_launcher_finalize.py, `_run_launcher_under_bash32` in
# test_ec2_bash32_portability.py) -- both are genuinely unmigrated and
# out of this subtask's scope, so they are recorded here rather than
# silently passing the widened guard.
_KNOWN_UNMIGRATED: frozenset[str] = frozenset(
    {
        "tests/test_ec2_launcher_finalize.py",
        "tests/test_ec2_bash32_portability.py",
    }
)


def test_run_launcher_is_defined_in_ec2_stub():
    import tests.ec2_stub as ec2_stub

    assert callable(ec2_stub.run_launcher)


def test_run_launcher_matches_the_common_call_shape():
    import inspect

    import tests.ec2_stub as ec2_stub

    sig = inspect.signature(ec2_stub.run_launcher)
    params = sig.parameters
    # `cwd` joined the shape deliberately: `leerie:39` assigns
    # `USER_REPO="$(pwd -P)"` unconditionally, so a test that needs the
    # launcher to act on a scratch repo can only say so through the child's
    # working directory — setting `USER_REPO` in `env` is silently ignored.
    # It defaults to None so every existing caller is unaffected.
    #
    # NOTE: tests/test_no_duplicate_launcher_invoke_helper.py asserts this
    # same shape. Both must move together; a change to one that misses the
    # other lands red.
    assert list(params) == [
        "args", "env", "launcher", "timeout", "use_bash", "cwd"]
    for name in ("launcher", "timeout", "use_bash", "cwd"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default == 30
    assert params["use_bash"].default is False
    assert params["cwd"].default is None


def test_no_local_run_launcher_reimplementations_outside_the_allowlist():
    """No file under tests/ defines its own two-positional-arg
    `(args, env)` invoke helper, under any name, unless it is a still-
    unmigrated legacy site named in `_KNOWN_UNMIGRATED`. A new local def
    of that shape outside that set is a fresh duplication and must import
    the shared `run_launcher` helper from tests/ec2_stub.py instead."""
    offenders = _duplicate_shaped_files()
    unexpected = sorted(offenders - _KNOWN_UNMIGRATED)
    assert unexpected == [], (
        "The following files define a NEW local two-positional-arg "
        "`(args, env)` invoke helper not in the legacy allowlist; "
        "import `run_launcher` from tests/ec2_stub.py instead:\n"
        + "\n".join(unexpected)
    )


def test_allowlist_has_no_stale_entries():
    """Every entry in `_KNOWN_UNMIGRATED` must still define a local
    two-positional-arg duplicate-shaped invoke helper. When a migration
    subtask removes a file's local def, it must also drop that file's
    name from this allowlist in the same commit -- otherwise this
    shrink-only list silently stops shrinking."""
    offenders = _duplicate_shaped_files()
    stale = sorted(_KNOWN_UNMIGRATED - offenders)
    assert stale == [], (
        "The following files no longer define a local two-positional-arg "
        "duplicate-shaped invoke helper but are still in the allowlist; "
        "remove them from _KNOWN_UNMIGRATED:\n" + "\n".join(stale)
    )


def test_group_state_dir_guard_helper_is_not_flagged():
    """Anti-vacuity control: the scan must not match
    tests/test_group_state_dir_guard.py's genuinely distinct
    `_run_launcher(tmp_path, args, env_extra=None, stub=None,
    stub_log=None)` helper, which this guard is not meant to migrate."""
    path = TESTS_DIR / "test_group_state_dir_guard.py"
    text = path.read_text()
    assert "def _run_launcher(" in text
    tree = ast.parse(text)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_run_launcher":
            assert not _is_duplicate_shaped(node)


def test_scan_flags_a_differently_named_duplicate():
    """Regression for the evasion class this subtask fixes: the pre-fix
    literal-name regex (`^\\s*def _run_launcher\\(...`) could never see
    tests/test_ec2_launcher_readonly_verbs.py's own `_run(args, env)`, a
    byte-for-byte duplicate of the run_launcher shape under a different
    name. Plants a `_run`-named duplicate-shaped def and asserts the
    widened, name-independent scan still flags it."""
    import tempfile

    src = (
        "import subprocess\n\n"
        "def _run(args, env):\n"
        "    return subprocess.run(['leerie'] + args, env=env)\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        planted = Path(tmp) / "test_planted_differently_named.py"
        planted.write_text(src)
        tree = ast.parse(planted.read_text())
        found = any(
            isinstance(node, ast.FunctionDef) and _is_duplicate_shaped(node)
            for node in ast.walk(tree)
        )
    assert found, (
        "the scan failed to detect a differently-named "
        "(non-'_run_launcher') duplicate-shaped def -- the name-"
        "independent widening has regressed"
    )
