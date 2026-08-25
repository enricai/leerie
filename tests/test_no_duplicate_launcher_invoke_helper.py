"""Guards the single-owner `run_launcher` invoke helper in tests/ec2_stub.py.

tests/ec2_stub.py, not tests/conftest.py, is the established convention for
EC2-launcher-test scaffolding (see its own docstring on `make_ssm_stub_aws`
et al.: "Single-owner discipline (same precedent as the state-machine stub
above and tests/launcher_blocks.py)"). `run_launcher` there already
replaced the near-identical `_run_launcher` previously redefined in
tests/test_auto_detect_run_runtime.py, tests/test_ec2_launcher_kill.py, and
tests/test_ec2_launcher_resume.py.

Mirrors tests/test_no_duplicate_run_bash_helper.py's shrink-only-allowlist
pattern: no file under tests/ should define its own `(args, env) ->
CompletedProcess` invoke helper, under ANY name, unless it is either a
still-unmigrated legacy site named in `_KNOWN_UNMIGRATED`, or the
permanently-excluded tests/test_group_state_dir_guard.py, whose own
`_run_launcher` is a materially different, 5-parameter stub-binary-
injection helper that happens to share a name and is not a duplicate of
this one.

The scan is deliberately NAME-INDEPENDENT (matches any `def` whose two
positional parameters are `args`/`env`-shaped, not just one literally
named `_run_launcher`) -- test-002 widened it after
tests/test_ec2_launcher_readonly_verbs.py's own `_run(args, env)` (a
byte-for-byte duplicate of this shape under a different name) evaded the
original name-keyed scan entirely.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

# The (args, env) two-positional-parameter shape that
# tests/ec2_stub.py::run_launcher replaces. This is what distinguishes a
# reintroduced duplicate from tests/test_group_state_dir_guard.py's
# unrelated 5-parameter `_run_launcher(tmp_path, args, env_extra, stub,
# stub_log)`, which shares only a name (and would in any case be excluded
# by param count alone even under the name-independent scan below).
_DUPLICATE_SHAPE_PARAM_COUNT = 2

# The two positional parameter names the duplicate shape shares across
# every migrated site (`_run_launcher`, `_run`, ...): first parameter
# `args`, second parameter `env`. Requiring the names (not just the count)
# keeps the scan from flagging an unrelated two-positional-arg helper that
# happens to share nothing but arity.
_DUPLICATE_SHAPE_PARAM_NAMES = ("args", "env")


def _delegates_to_shared_run_launcher(body: list[ast.stmt]) -> bool:
    """True when a function body is a single `return <call>(...)` whose
    call target names `run_launcher` (directly, aliased, or as an
    attribute e.g. `ec2_stub.run_launcher` / `_run_launcher_shared`).
    This is the "thin same-behavior local alias" shape the success
    criteria explicitly sanctions (test_ec2_launcher_kill.py,
    test_ec2_launcher_resume.py) -- it must NOT be flagged as a
    duplicate, since it delegates to the single owner rather than
    reimplementing it."""
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
    args = node.args
    params = list(args.posonlyargs) + list(args.args)
    if len(params) != _DUPLICATE_SHAPE_PARAM_COUNT:
        return False
    if tuple(p.arg for p in params) != _DUPLICATE_SHAPE_PARAM_NAMES:
        return False
    return not _delegates_to_shared_run_launcher(node.body)

# Permanently excluded: not a legacy unmigrated site, and never intended to
# migrate onto tests.ec2_stub.run_launcher -- see the module docstring and
# tests/ec2_stub.py::run_launcher's own docstring.
_PERMANENT_EXCEPTIONS: frozenset[str] = frozenset(
    {"tests/test_group_state_dir_guard.py"}
)

# Shrink-only allowlist for genuinely unmigrated legacy sites. refactor-002
# already consolidated the three known duplicates
# (test_ec2_launcher_kill.py, test_ec2_launcher_resume.py,
# test_auto_detect_run_runtime.py) into tests.ec2_stub.run_launcher. A
# future migration subtask removes a file's local duplicate-shaped
# invoke-helper def and, if it cannot land the same commit as the def's
# removal, adds the file's name here temporarily -- then drops it again
# once the def is gone.
#
# test-002 widened this scan from a literal `_run_launcher` name match to
# the structural (args, env) -> CompletedProcess shape regardless of
# function name, and migrated tests/test_ec2_launcher_readonly_verbs.py's
# own differently-named `_run` duplicate onto the shared helper in the
# same change. The widening also surfaced two pre-existing, differently-
# named duplicates the old name-keyed scan could never see
# (`_run` in test_ec2_launcher_finalize.py,
# `_run_launcher_under_bash32` in test_ec2_bash32_portability.py) -- both
# are genuinely unmigrated and out of this subtask's scope, so they are
# recorded here rather than silently passing the widened guard.
_KNOWN_UNMIGRATED: frozenset[str] = frozenset(
    {
        "tests/test_ec2_launcher_finalize.py",
        "tests/test_ec2_bash32_portability.py",
    }
)


def _duplicate_shaped_run_launcher_files() -> set[str]:
    """Files under tests/ defining ANY `def` (regardless of name) with the
    duplicate (args, env) two-positional-parameter shape, excluding
    _PERMANENT_EXCEPTIONS. Name-independent by design -- see the module
    docstring for why a name-keyed scan is insufficient."""
    offenders: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if rel in _PERMANENT_EXCEPTIONS:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and _is_duplicate_shaped(node):
                offenders.add(rel)
    return offenders


def test_run_launcher_is_importable_from_ec2_stub():
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
    # NOTE: tests/test_no_duplicate_ec2_run_launcher_helper.py asserts this
    # same shape. Both must move together; a change to one that misses the
    # other lands red.
    assert list(params) == [
        "args", "env", "launcher", "timeout", "use_bash", "cwd"]
    for name in ("launcher", "timeout", "use_bash", "cwd"):
        assert params[name].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default == 30
    assert params["use_bash"].default is False
    assert params["cwd"].default is None


def test_no_local_invoke_helper_reimplementations_outside_the_allowlist():
    """No file under tests/ defines its own duplicate-shaped `_run_launcher`
    unless it is a still-unmigrated legacy site named in
    `_KNOWN_UNMIGRATED`. A new local duplicate-shaped `_run_launcher` def
    outside that set is a fresh duplication and must import the shared
    `run_launcher` helper from tests/ec2_stub.py instead."""
    offenders = _duplicate_shaped_run_launcher_files()
    unexpected = sorted(offenders - _KNOWN_UNMIGRATED)
    assert unexpected == [], (
        "The following files define a NEW local duplicate-shaped "
        "`_run_launcher` helper not in the legacy allowlist; import "
        "`run_launcher` from tests/ec2_stub.py instead:\n"
        + "\n".join(unexpected)
    )


def test_allowlist_has_no_stale_entries():
    """Every entry in `_KNOWN_UNMIGRATED` must still define a duplicate-
    shaped local `_run_launcher`. When a migration subtask removes a
    file's local def, it must also drop that file's name from this
    allowlist in the same commit -- otherwise this shrink-only list
    silently stops shrinking."""
    offenders = _duplicate_shaped_run_launcher_files()
    stale = sorted(_KNOWN_UNMIGRATED - offenders)
    assert stale == [], (
        "The following files no longer define a duplicate-shaped local "
        "`_run_launcher` but are still in the allowlist; remove them from "
        "_KNOWN_UNMIGRATED:\n" + "\n".join(stale)
    )


def test_migrated_files_import_the_shared_helper():
    """The three files refactor-002 consolidated must import
    tests.ec2_stub.run_launcher rather than defining their own -- this is
    the positive control proving _KNOWN_UNMIGRATED is empty because they
    migrated, not because the scan is blind to them."""
    migrated = [
        "tests/test_ec2_launcher_kill.py",
        "tests/test_ec2_launcher_resume.py",
        "tests/test_auto_detect_run_runtime.py",
    ]
    for rel in migrated:
        text = (REPO_ROOT / rel).read_text()
        assert "from tests.ec2_stub import" in text or "tests.ec2_stub" in text, (
            f"{rel} no longer appears to import tests.ec2_stub -- did it "
            "regain a local duplicate-shaped `_run_launcher`?"
        )


def test_permanent_exception_still_has_a_different_shape():
    """tests/test_group_state_dir_guard.py's `_run_launcher` must keep a
    shape different from the duplicate shape this guard scans for, or the
    permanent-exception carve-out is silently hiding a real duplicate. If
    this ever fails because that helper's signature changed to match
    ec2_stub.run_launcher's shape, it should be migrated and dropped from
    _PERMANENT_EXCEPTIONS instead of exempted."""
    helper_name = "_run_launcher"
    rel = "tests/test_group_state_dir_guard.py"
    assert rel in _PERMANENT_EXCEPTIONS
    tree = ast.parse((REPO_ROOT / rel).read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == helper_name:
            found = True
            assert not _is_duplicate_shaped(node), (
                f"{rel}'s `_run_launcher` now matches the duplicate "
                "(args, env) shape -- it should migrate onto "
                "tests.ec2_stub.run_launcher and be removed from "
                "_PERMANENT_EXCEPTIONS rather than staying exempted."
            )
    assert found, f"{rel} no longer defines `_run_launcher` at all"


def test_scan_finds_a_planted_reproduction():
    """Anti-vacuity: the AST scan must actually detect a duplicate-shaped
    invoke helper when one exists, proving a return of `[]` elsewhere
    reflects a clean tree rather than a scan that matches nothing. Named
    `_run_launcher`, matching the historically observed name."""
    import tempfile

    src = (
        "import subprocess\n\n"
        "def _run_launcher(args, env):\n"
        "    return subprocess.run(['leerie'] + args, env=env)\n"
    )
    with tempfile.TemporaryDirectory() as tmp:
        planted = Path(tmp) / "test_planted_reproduction.py"
        planted.write_text(src)
        tree = ast.parse(planted.read_text())
        found = any(
            isinstance(node, ast.FunctionDef) and _is_duplicate_shaped(node)
            for node in ast.walk(tree)
        )
    assert found, "the scan failed to detect a planted duplicate-shaped def"


def test_scan_finds_a_differently_named_planted_reproduction():
    """Regression for the evasion class this subtask fixes:
    tests/test_ec2_launcher_readonly_verbs.py's own `_run(args, env)` was
    a byte-for-byte duplicate of the `run_launcher` shape under a
    different name, and the pre-fix name-keyed scan (`node.name ==
    '_run_launcher'`) could not see it. Plants a `_run`-named duplicate
    and asserts the widened, name-independent scan still flags it."""
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
