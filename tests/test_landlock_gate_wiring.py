"""Every `needs_landlock` test also carries the skip, and the gate is wired.

Two mistakes this pins, both made and measured while the gate was written:

1. `pytest.mark.needs_landlock(pytest.mark.skipif(...))` looks like it
   composes two marks. It does not: the skipif becomes an *argument* of the
   outer mark, no skip is ever applied, and 15 confined tests ran against a
   kernel that cannot confine. The two must be applied as separate marks.
2. A test marked `needs_landlock` but not `skip_without_landlock` runs its
   body on a host with no Landlock and no container, which is the same
   failure by a different route.

Mirrors `tests/test_jq_gate_wiring.py`, which guards the same shape for
`HAS_JQ`: the flag and the skip must travel together.
"""
from __future__ import annotations

import ast
import importlib.util
import os
from pathlib import Path

import pytest

from tests.conftest import (CAN_RUN_IN_CONTAINER, LANDLOCK_ABI,
                            MIN_LANDLOCK_ABI, needs_landlock,
                            skip_without_landlock)

TESTS = Path(__file__).resolve().parent


def _marks(dec: ast.expr) -> str:
    """The dotted name a decorator expression names, e.g. `needs_landlock`."""
    while isinstance(dec, ast.Call):
        dec = dec.func
    parts = []
    while isinstance(dec, ast.Attribute):
        parts.append(dec.attr)
        dec = dec.value
    if isinstance(dec, ast.Name):
        parts.append(dec.id)
    return ".".join(reversed(parts))


def test_the_two_marks_are_distinct_marks_not_one_wrapping_the_other() -> None:
    """The exact defect: a skipif passed to the named mark applies no skip."""
    assert needs_landlock.mark.name == "needs_landlock"
    assert needs_landlock.mark.args == ()
    assert skip_without_landlock.mark.name == "skipif"
    # And the antipattern really is silent, which is why it needs a guard.
    swallowed = pytest.mark.needs_landlock(pytest.mark.skipif(True, reason="r"))
    assert swallowed.mark.name == "needs_landlock"
    assert swallowed.mark.args and isinstance(
        swallowed.mark.args[0], pytest.MarkDecorator)


def test_the_abi_is_the_shims_own_reading() -> None:
    """`LANDLOCK_ABI` is the kernel's, read through the shim -- not a
    constant. Independent source: load `abi()` again, by path."""
    spec = importlib.util.spec_from_file_location(
        "lk_wiring", TESTS.parent / "scripts" / "landlock_exec.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert LANDLOCK_ABI == m.abi()
    assert MIN_LANDLOCK_ABI == m.MIN_ABI


def _should_skip(abi: int, image: str, runtime_ok: bool) -> bool:
    """What the gate must decide, written out independently of conftest: skip
    only when this kernel cannot confine AND the test cannot be handed to a
    container that can."""
    return abi < MIN_LANDLOCK_ABI and not (bool(image) and runtime_ok)


@pytest.mark.parametrize(
    "abi,image,runtime_ok,skip",
    [(0, "", False, True),      # no kernel, no image: nothing can run it
     (0, "", True, True),       # a runtime but no image is still nothing
     (0, "img", False, True),   # an image but a stopped runtime, likewise
     (0, "img", True, False),   # no kernel, but a container that has one
     (3, "", False, False),     # exactly the minimum ABI: runs here
     (4, "", False, False),     # a newer kernel: runs here
     (4, "img", True, False)])  # a kernel of its own: the image is moot
def test_the_gate_decides_each_case_the_way_it_must(
        abi, image, runtime_ok, skip) -> None:
    """Parametrized so the inputs DISAGREE: a flipped comparison or a dropped
    `not` changes at least one row. Two equal sources of one value could not
    tell a correct read from a bypass (CLAUDE.md), so the expectation is
    spelled out per case rather than recomputed from conftest's own names.
    """
    assert _should_skip(abi, image, runtime_ok) is skip


def test_the_live_gate_matches_that_decision() -> None:
    """conftest's own mark agrees with the table for THIS host.

    The image comes from the environment, not from conftest's copy of it, so
    a gate that ignored the variable would show up here.
    """
    image = os.environ.get("LEERIE_TEST_CONTAINER_IMAGE", "")
    # `CAN_RUN_IN_CONTAINER` already folds in the runtime probe; with no image
    # it is False for want of the image, which the table takes separately.
    runtime_ok = CAN_RUN_IN_CONTAINER if image else True
    assert skip_without_landlock.mark.args[0] is _should_skip(
        LANDLOCK_ABI, image, runtime_ok)


def test_every_needs_landlock_test_also_carries_the_skip() -> None:
    """A test that can reach the container must also be able to skip."""
    bad: list[str] = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text())
        # A module-level `pytestmark` carrying both covers every test in it.
        module_marks = {
            _marks(e)
            for node in tree.body
            if isinstance(node, ast.Assign)
            and any(getattr(t, "id", "") == "pytestmark" for t in node.targets)
            for e in (node.value.elts
                      if isinstance(node.value, (ast.List, ast.Tuple))
                      else [node.value])}
        # Only a module that carries BOTH is covered. A `pytestmark` with the
        # named mark and no skip is the very defect this guard pins, so it
        # must not be an exemption from it.
        if module_marks & {"skip_without_landlock", "skipif",
                           "pytest.mark.skipif"}:
            if "needs_landlock" in module_marks:
                continue
        elif "needs_landlock" in module_marks:
            bad.append(f"{path.name}::<module pytestmark>")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            names = {_marks(d) for d in node.decorator_list}
            if "needs_landlock" in names and not (
                    names & {"skip_without_landlock", "skipif"}):
                bad.append(f"{path.name}::{node.name}")
    assert bad == [], (
        "marked needs_landlock without a skip: on a host with no Landlock "
        "and no container these run their body against a kernel that "
        "cannot confine")
