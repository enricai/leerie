"""No `args.X = resolve_Y(...)` whose result nothing reads.

`main()` resolves ~23 preferences into `args.<name>` with the CLI > env >
leerie.toml ladder. A resolution whose result is never read back is not
harmless dead code: the ladder's lower tiers are *documented user surface*,
so the flag and the leerie.toml key silently do nothing while the env var
(read directly by whoever actually consumes the value) keeps working. The
failure is invisible — nothing errors, the docs stay plausible, and only
two of three documented tiers work.

That is exactly what happened to `--aws-region` / `--aws-profile`. They are
consumed host-side by the launcher when provisioning `--runtime ec2`
machines, never by the orchestrator, which runs inside the container. The
orchestrator resolved them anyway and discarded the result, so the CLI flag
and the `leerie.toml` keys were inert from the day they were documented.
Resolution now lives in the launcher, beside the consumers.

`tests/test_no_dead_functions.py` cannot see this class **by construction**:
it scans for unreferenced module-level functions, and `resolve_aws_region`
*was* referenced — by the dead assignment itself. This file is its sibling
for the assignment layer.

Derived, not enumerated: a hand-kept list of "resolutions known to be live"
stops covering reality the moment one is added, which is the defect class
CLAUDE.md records being closed four times over (PRs #180-#183, and the
state-init branch walk).
"""
from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _tree() -> ast.Module:
    return ast.parse(LEERIE_PY.read_text())


def _resolution_assignments(tree: ast.Module) -> dict[str, ast.Assign]:
    """Every `args.<name> = resolve_<something>(...)` inside `main()`."""
    main = next(n for n in ast.walk(tree)
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
                and n.name == "main")
    found: dict[str, ast.Assign] = {}
    for node in ast.walk(main):
        if not (isinstance(node, ast.Assign) and len(node.targets) == 1):
            continue
        tgt = node.targets[0]
        if (isinstance(tgt, ast.Attribute)
                and isinstance(tgt.value, ast.Name) and tgt.value.id == "args"
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id.startswith("resolve_")):
            found[tgt.attr] = node
    return found


def _reader_count(tree: ast.Module, attr: str, assignment: ast.Assign) -> int:
    """Reads of `args.<attr>` anywhere, EXCLUDING the assignment's own RHS.

    The exclusion is load-bearing and not defensive. Every one of these
    assignments passes the current value into its own resolver as the CLI
    tier — `resolve_aws_region(repo_root, getattr(args, "aws_region", None))`
    — so a naive reader count sees that `getattr` and concludes the value is
    consumed. The guard would then never fire on the very shape it exists to
    catch. Verified: without this exclusion the sweep reports zero dead
    resolutions on the tree that contained two.
    """
    excluded = {id(n) for n in ast.walk(assignment.value)}
    count = 0
    for node in ast.walk(tree):
        if id(node) in excluded:
            continue
        # args.<attr> in a load context
        if (isinstance(node, ast.Attribute) and node.attr == attr
                and isinstance(node.value, ast.Name) and node.value.id == "args"
                and isinstance(node.ctx, ast.Load)):
            count += 1
        # getattr(args, "<attr>", ...)
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "getattr" and len(node.args) >= 2
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "args"
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == attr):
            count += 1
    return count


def test_no_resolution_result_is_discarded() -> None:
    tree = _tree()
    assignments = _resolution_assignments(tree)
    dead = sorted(attr for attr, node in assignments.items()
                  if _reader_count(tree, attr, node) == 0)
    assert not dead, (
        f"main() resolves {dead} into args.<name> but nothing reads the "
        f"result. The CLI flag and the leerie.toml key for each are then "
        f"silently inert while the env var keeps working, because whoever "
        f"actually consumes the value reads that env var directly. Either "
        f"consume the resolved value, or move resolution to whichever layer "
        f"owns the consumer (the launcher, for host-side knobs) and drop "
        f"the orchestrator's copy."
    )


def test_the_sweep_actually_finds_resolutions() -> None:
    """Anti-vacuity: a sweep that matches nothing passes the test above.

    If `main()` is renamed, the assignment shape changes, or the walk simply
    breaks, `_resolution_assignments` returns `{}` and the guard certifies a
    clean tree forever. The count is a floor, not a pin — it must not need
    updating every time a preference is added.
    """
    assignments = _resolution_assignments(_tree())
    assert len(assignments) >= 15, (
        f"expected main() to carry many `args.X = resolve_Y(...)` "
        f"resolutions, found {len(assignments)} — the sweep is broken, so "
        f"its result above is meaningless")


def test_a_known_live_resolution_is_seen_as_live() -> None:
    """Second anti-vacuity control, on the reader side.

    `_reader_count` returning 0 for everything would also pass the guard
    trivially — an all-dead verdict is impossible to distinguish from a
    broken reader scan when the assertion only checks emptiness. `runtime`
    is resolved and unambiguously read, so it must count as live.
    """
    tree = _tree()
    assignments = _resolution_assignments(tree)
    assert "runtime" in assignments, "expected `args.runtime` to be resolved"
    assert _reader_count(tree, "runtime", assignments["runtime"]) > 0, (
        "the reader scan is broken — args.runtime is known to be read")


def test_own_rhs_is_excluded_from_the_reader_count() -> None:
    """The exclusion in `_reader_count` is what makes this guard work.

    Each resolution passes its own current value in as the CLI tier, so
    every assignment contains a `getattr(args, "<attr>", ...)` referring to
    the attribute being assigned. Counting that as a reader makes the guard
    inert. This pins the exclusion directly rather than trusting it: with
    the assignment's RHS NOT excluded, a resolution that is otherwise dead
    must still register at least one apparent read.
    """
    tree = _tree()
    assignments = _resolution_assignments(tree)
    # Pick any resolution whose RHS references its own attribute.
    for attr, node in assignments.items():
        rhs = ast.unparse(node.value)
        if f'"{attr}"' in rhs or f".{attr}" in rhs:
            naive = _reader_count(tree, attr, ast.Assign(
                targets=node.targets, value=ast.Constant(value=None)))
            excluded = _reader_count(tree, attr, node)
            assert naive > excluded, (
                f"excluding the RHS changed nothing for {attr} — the "
                f"exclusion is not actually taking effect")
            return
    raise AssertionError(
        "no resolution passes its own attribute into its resolver; the "
        "exclusion this guard depends on may no longer be needed, but "
        "verify before deleting it")
