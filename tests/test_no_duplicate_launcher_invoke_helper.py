"""Guards the single-owner `run_launcher` invoke helper in tests/ec2_stub.py.

tests/ec2_stub.py, not tests/conftest.py, is the established convention for
EC2-launcher-test scaffolding (see its own docstring on `make_ssm_stub_aws`
et al.: "Single-owner discipline (same precedent as the state-machine stub
above and tests/launcher_blocks.py)"). `run_launcher` there already
replaced the near-identical `_run_launcher` previously redefined in
tests/test_auto_detect_run_runtime.py, tests/test_ec2_launcher_kill.py, and
tests/test_ec2_launcher_resume.py.

Mirrors tests/test_no_duplicate_run_bash_helper.py's shrink-only-allowlist
pattern: no file under tests/ should define its own `def _run_launcher(`
(or same-shaped invoke helper) unless it is either a still-unmigrated
legacy site named in `_KNOWN_UNMIGRATED`, or the permanently-excluded
tests/test_group_state_dir_guard.py, whose own `_run_launcher` is a
materially different, 5-parameter stub-binary-injection helper that
happens to share a name and is not a duplicate of this one.
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS_DIR = REPO_ROOT / "tests"

_HELPER_NAME = "_run_launcher"

# The (args, env) two-positional-parameter shape that
# tests/ec2_stub.py::run_launcher replaces. This is what distinguishes a
# reintroduced duplicate from tests/test_group_state_dir_guard.py's
# unrelated 5-parameter `_run_launcher(tmp_path, args, env_extra, stub,
# stub_log)`, which shares only the name.
_DUPLICATE_SHAPE_PARAM_COUNT = 2

# Permanently excluded: not a legacy unmigrated site, and never intended to
# migrate onto tests.ec2_stub.run_launcher -- see the module docstring and
# tests/ec2_stub.py::run_launcher's own docstring.
_PERMANENT_EXCEPTIONS: frozenset[str] = frozenset(
    {"tests/test_group_state_dir_guard.py"}
)

# Shrink-only allowlist for genuinely unmigrated legacy sites. refactor-002
# already consolidated the three known duplicates
# (test_ec2_launcher_kill.py, test_ec2_launcher_resume.py,
# test_auto_detect_run_runtime.py) into tests.ec2_stub.run_launcher, so
# this starts empty. A future migration subtask removes a file's local
# duplicate-shaped `_run_launcher` def and, if it cannot land the same
# commit as the def's removal, adds the file's name here temporarily --
# then drops it again once the def is gone.
_KNOWN_UNMIGRATED: frozenset[str] = frozenset()


def _duplicate_shaped_run_launcher_files() -> set[str]:
    """Files under tests/ defining a `_run_launcher` with the duplicate
    (args, env) two-positional-parameter shape, excluding
    _PERMANENT_EXCEPTIONS."""
    offenders: set[str] = set()
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        rel = str(path.relative_to(REPO_ROOT))
        if rel in _PERMANENT_EXCEPTIONS:
            continue
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.FunctionDef) and node.name == _HELPER_NAME:
                args = node.args
                param_count = len(args.posonlyargs) + len(args.args)
                if param_count == _DUPLICATE_SHAPE_PARAM_COUNT:
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
    assert list(params) == ["args", "env", "launcher", "timeout", "use_bash"]
    assert params["launcher"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["use_bash"].kind == inspect.Parameter.KEYWORD_ONLY
    assert params["timeout"].default == 30
    assert params["use_bash"].default is False


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
    param count different from the duplicate shape this guard scans for,
    or the permanent-exception carve-out is silently hiding a real
    duplicate. If this ever fails because that helper's signature
    changed to match ec2_stub.run_launcher's shape, it should be migrated
    and dropped from _PERMANENT_EXCEPTIONS instead of exempted."""
    rel = "tests/test_group_state_dir_guard.py"
    assert rel in _PERMANENT_EXCEPTIONS
    tree = ast.parse((REPO_ROOT / rel).read_text())
    found = False
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == _HELPER_NAME:
            found = True
            args = node.args
            param_count = len(args.posonlyargs) + len(args.args)
            assert param_count != _DUPLICATE_SHAPE_PARAM_COUNT, (
                f"{rel}'s `_run_launcher` now matches the duplicate "
                "(args, env) shape -- it should migrate onto "
                "tests.ec2_stub.run_launcher and be removed from "
                "_PERMANENT_EXCEPTIONS rather than staying exempted."
            )
    assert found, f"{rel} no longer defines `_run_launcher` at all"


def test_scan_finds_a_planted_reproduction():
    """Anti-vacuity: the AST scan must actually detect a duplicate-shaped
    `_run_launcher` when one exists, proving a return of `[]` elsewhere
    reflects a clean tree rather than a scan that matches nothing."""
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
            isinstance(node, ast.FunctionDef)
            and node.name == _HELPER_NAME
            and (len(node.args.posonlyargs) + len(node.args.args))
            == _DUPLICATE_SHAPE_PARAM_COUNT
            for node in ast.walk(tree)
        )
    assert found, "the scan failed to detect a planted duplicate-shaped def"
