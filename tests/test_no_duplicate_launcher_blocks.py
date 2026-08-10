"""No test file may REPRODUCE a launcher function or block that another
test already extracts verbatim.

This is the general form of `tests/test_no_duplicate_ec2_knob.py`, which
guards one such name. N13 is what makes the general rule necessary: five
bash-harness files each hand-copied a launcher block into a string literal,
so no change to `leerie` could fail them. Converting those five fixed the
instances; it did not stop the next one. Three MORE reproductions were
found afterwards outside the original list, and two had already drifted:

- `tests/test_launcher_state_mount.py` reproduced the `nerdctl run` argv,
  missing `--cidfile`, `--cgroupns=host`, `ROOTLESS_SECOPT`, the `LEERIE_*`
  auto-forward and `${REPO_IMAGE_TAG:-$IMAGE_TAG}`, and inlining one of
  three `CGROUP_MOUNT_ARG` branches as a literal.
- `tests/test_launcher_runtime_knob.py` reproduced the RUNTIME block with
  `_RUNTIME_EXPLICIT` missing entirely — a flag set at six sites and read
  by the resume auto-detect, with zero coverage anywhere in the suite.
- `tests/test_group_state_dir_guard.py` reproduced `_state_dir_default`
  with nine stale launcher line citations in its own docstring.

None of those produced a WRONG answer. They were blind — they would have
passed identically if the launcher deleted the behaviour under test, which
is the failure mode worth a standing guard.

**Markers are anchored to the start of a line.** A reproduction opens the
body at column 0 (or at the block's own indent); a legitimate reference
inside an extractor is always quoted mid-line, e.g.
`src.index("_state_dir_default() {")`. Matching the bare token would flag
the very extractors this guard exists to encourage — see the same reasoning
in `tests/test_no_duplicate_ec2_knob.py` and CLAUDE.md.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
TESTS_DIR = REPO_ROOT / "tests"

# (label, start-of-line marker). Each must appear exactly once in the
# launcher and never at the start of a line in any test file.
#
# `_resolve_ec2_knob` is also covered by tests/test_no_duplicate_ec2_knob.py,
# which additionally pins its extractor list. Kept here so the table is the
# complete statement of the rule rather than "the rule, minus one row you
# have to know about" — two guards asserting one property fail together and
# cost nothing, unlike two copies of an extraction, which drift.
_BLOCKS = [
    ("_resolve_ec2_knob", "_resolve_ec2_knob() {"),
    ("_state_dir_default", "_state_dir_default() {"),
    ("_resolve_seed_knob", "_resolve_seed_knob() {"),
    ("ensure_image", "ensure_image() {"),
    ("resolve_repo_image_tag", "resolve_repo_image_tag() {"),
    # Not a `name() {` shape — a case arm, matched at its own indent.
    ("config) case arm", "  config)"),
    ("runtime-mode knob block", "# --- runtime-mode knob ---"),
    ("nerdctl run argv", "  _run_argv=("),
]


def _line_start_re(marker: str) -> re.Pattern[str]:
    return re.compile("^" + re.escape(marker), re.MULTILINE)


@pytest.mark.parametrize("label,marker", _BLOCKS,
                         ids=[b[0] for b in _BLOCKS])
def test_launcher_defines_it_exactly_once(label, marker):
    """Anti-vacuity: a marker that matches nothing in the launcher would
    certify "no duplicates" forever. If this fails, the launcher was
    refactored and the table needs updating — not the guard deleting."""
    hits = _line_start_re(marker).findall(LAUNCHER.read_text())
    assert len(hits) == 1, (
        f"expected exactly one launcher definition of {label} at a line "
        f"start, found {len(hits)}; the marker in _BLOCKS is stale")


@pytest.mark.parametrize("label,marker", _BLOCKS,
                         ids=[b[0] for b in _BLOCKS])
def test_no_test_file_reproduces_it(label, marker):
    pattern = _line_start_re(marker)
    offenders = []
    for path in sorted(TESTS_DIR.glob("*.py")):
        if path.name == Path(__file__).name:
            continue
        if pattern.search(path.read_text()):
            offenders.append(path.name)
    assert not offenders, (
        f"{offenders} reproduce the launcher's {label} instead of "
        f"extracting it. Read it out of `leerie` at test time (see "
        f"tests/test_resolve_state_dir.py::_extract_state_dir_block or "
        f"tests/test_launcher_env_forwarding.py::_extract_run_argv) so a "
        f"change to the launcher can actually fail the test.")


def test_the_scan_can_find_a_reproduction():
    """Guard-the-guard: prove the predicate fires on a planted copy. A scan
    that silently matched nothing would pass every test above."""
    planted = "_state_dir_default() {\n  echo nope\n}\n"
    assert _line_start_re("_state_dir_default() {").search(planted)
    # ...and does NOT fire on a legitimate quoted reference, which is the
    # whole reason the marker is line-anchored.
    quoted = '    start = src.index("_state_dir_default() {")\n'
    assert not _line_start_re("_state_dir_default() {").search(quoted)


def test_converted_files_actually_extract():
    """The counterpart to the absence checks: absence of a reproduction is
    also satisfied by a file that tests nothing at all. Each converted
    harness must read the launcher AND use what it read.

    Reading alone is too weak — a file could call `LAUNCHER.read_text()`
    and discard the result. So walk the AST and require the extraction to
    reach the harness the tests actually execute, which is the property
    that makes the file coupled rather than merely adjacent."""
    for name in ("test_launcher_state_mount.py",
                 "test_launcher_runtime_knob.py",
                 "test_group_state_dir_guard.py",
                 "test_resolve_state_dir.py"):
        src = (TESTS_DIR / name).read_text()
        assert ("LAUNCHER.read_text()" in src
                or "_extract_state_dir_block" in src), (
            f"{name} no longer reads the launcher — it cannot be coupled "
            f"to it")
        tree = ast.parse(src)
        # An extraction is "used" when its result is concatenated into a
        # harness string or assigned to a module-level name the tests run.
        used = False
        for node in ast.walk(tree):
            if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
                if any(isinstance(n, ast.Call) and _extract_name(n)
                       for n in ast.walk(node)):
                    used = True
            if isinstance(node, ast.Assign):
                for n in ast.walk(node.value):
                    if isinstance(n, ast.Call) and _extract_name(n):
                        used = True
        assert used, (
            f"{name} reads the launcher but never feeds the result into a "
            f"harness — the extraction is decorative")


def _extract_name(call: ast.Call) -> bool:
    """True when `call` is one of the extraction helpers.

    A bare `read_text` is deliberately NOT enough. Every one of these files
    calls `(TESTS_DIR / name).read_text()`, `path.read_text()` or similar
    for reasons unrelated to the launcher, so accepting the method by name
    let any assignment anywhere in the file satisfy the "extraction is
    used" check — which is most of the strength the caller's docstring
    claims. Only `LAUNCHER.read_text()` counts: that is the receiver that
    makes the read an extraction of the file under guard.
    """
    fn = call.func
    name = getattr(fn, "id", None) or getattr(fn, "attr", None) or ""
    if name.startswith("_extract"):
        return True
    if name == "read_text":
        recv = getattr(fn, "value", None)
        return isinstance(recv, ast.Name) and recv.id == "LAUNCHER"
    return False
