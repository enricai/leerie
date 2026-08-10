"""Coupling tests for STATE_FIELDS — the canonical list of keys the
orchestrator writes to `st.data`.

Three parities are enforced:

1. spec ↔ code: the field table in IMPLEMENTATION.md §8 lists exactly
   the names in `STATE_FIELDS`.
2. code ↔ runtime: every `st.data["x"] = ...`, `st.data.setdefault("x", ...)`,
   and key in the run-init dict literal in `_run_phases()` uses a name
   that appears in `STATE_FIELDS`.
3. branch ↔ branch: no key is seeded on the resume path but missing from
   the fresh-run path (see `test_no_resume_only_state_keys`).

If a future change adds a new state field, both this test and the spec
table must be updated in the same commit. That is the point.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
IMPL_MD = REPO_ROOT / "docs" / "IMPLEMENTATION.md"


def _spec_fields() -> set[str]:
    """Parse field names out of the `state.json` markdown table in
    IMPLEMENTATION.md §8. The table is identified by its header row
    `| Field | Shape | Purpose |` and ends at the next blank line."""
    text = IMPL_MD.read_text()
    header = re.search(
        r"^\|\s*Field\s*\|\s*Shape\s*\|\s*Purpose\s*\|\s*$",
        text, re.MULTILINE,
    )
    assert header, "could not locate state.json field table in IMPLEMENTATION.md"
    # Skip the header line and the |---|---|---| separator.
    start = header.end()
    block = text[start:].split("\n\n", 1)[0]
    fields: set[str] = set()
    for line in block.splitlines():
        m = re.match(r"\|\s*`([a-z_]+)`\s*\|", line)
        if m:
            fields.add(m.group(1))
    assert fields, "extracted no field names from the IMPLEMENTATION.md table"
    return fields


def _is_st_data(node: ast.AST) -> bool:
    """`st.data` exactly — not `bst.data`, not `other.data`.

    A text match on `st\\.data` also matches `bst.data`, the
    `_BackstopState` stub in `_backstop_capture_prior_runs`. That is not a
    hypothetical: it is what silently disabled the dict-literal half of
    `_runtime_field_writes` (see that function's note).
    """
    return (isinstance(node, ast.Attribute) and node.attr == "data"
            and isinstance(node.value, ast.Name) and node.value.id == "st")


def _runtime_field_writes() -> set[str]:
    """Every name used as a key on `st.data` in leerie.py — whether via
    `st.data["x"] = ...`, `st.data.setdefault("x", ...)`, or as a key in
    the run-init dict literal in `_run_phases()`.

    AST rather than regex, because the regex version silently covered only
    two of those three forms for its whole life. It matched the literal
    with `re.search(r"st\\.data\\s*=\\s*\\{(.*?)\\}", ...)`, and two bugs
    compounded: `bst.data = {}` (a stub, `orchestrator/leerie.py`) matched
    `st\\.data` for want of a word boundary, and `re.search` returns that
    FIRST match, whose non-greedy body captured zero characters. Measured:
    an undeclared key injected into the run-init literal was not detected,
    while the same key in a subscript write was — so the guarantee
    CLAUDE.md records for this test held for one write form only.
    """
    tree = ast.parse(LEERIE_PY.read_text())
    found: set[str] = set()
    for node in ast.walk(tree):
        # st.data["x"] = ...
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if (isinstance(tgt, ast.Subscript) and _is_st_data(tgt.value)
                        and isinstance(tgt.slice, ast.Constant)
                        and isinstance(tgt.slice.value, str)):
                    found.add(tgt.slice.value)
                # st.data = {"x": ..., ...}
                if _is_st_data(tgt) and isinstance(node.value, ast.Dict):
                    found |= {k.value for k in node.value.keys
                              if isinstance(k, ast.Constant)
                              and isinstance(k.value, str)}
        # st.data.setdefault("x", ...)
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "setdefault"
                and _is_st_data(node.func.value)
                and node.args and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)):
            found.add(node.args[0].value)
    return found


def _state_init_branch_keys(leerie) -> tuple[set[str], set[str]]:
    """`st.data` keys seeded by each arm of `_run_phases`' `if args.resume:`,
    as `(resume_keys, fresh_keys)`.

    The resume arm seeds with subscript assignments and the fresh arm with a
    dict literal, so this walks the AST rather than matching text — a
    source-order or whole-file regex cannot tell which arm it covered, which
    is the exact trap documented in `tests/test_leerie_commit.py`.

    THIS IS THE SINGLE OWNER of the branch-split derivation.
    `tests/test_leerie_commit.py` and
    `tests/test_phase_planning_coverage_gate.py` import it;
    `tests/test_no_duplicate_state_walks.py` enforces that nothing
    re-implements it. Two copies of a rule drift exactly the way two copies
    of a list do — see `tests/launcher_blocks.py` for the same lesson.
    """
    import inspect
    import textwrap

    tree = ast.parse(textwrap.dedent(inspect.getsource(leerie._run_phases)))
    # Match the test EXACTLY. `"args.resume" in ast.unparse(...)` also
    # matches the later `if not args.resume:` guard, and `ast.walk` yields
    # breadth-first — not source order — so picking `[0]` from a substring
    # match selected the right node by luck, not by construction.
    nodes = [n for n in ast.walk(tree)
             if isinstance(n, ast.If) and ast.unparse(n.test) == "args.resume"]
    assert len(nodes) == 1, (
        f"expected exactly one `if args.resume:` node in _run_phases, found "
        f"{len(nodes)} — a second one makes `nodes[0]` ambiguous, which is "
        f"how this walk would silently start analysing the wrong branch")
    node = nodes[0]
    assert node.orelse, "the fresh-run `else:` branch was not found"

    def keys(body: list) -> set[str]:
        found: set[str] = set()
        for stmt in body:
            for n in ast.walk(stmt):
                # Fail loudly on a write form this walk cannot see, rather
                # than under-reporting. An unseen `st.data.update({...})`
                # would shrink `resume_keys` and make the guard below pass
                # vacuously — the failure mode it exists to prevent.
                if (isinstance(n, ast.Call)
                        and isinstance(n.func, ast.Attribute)
                        and _is_st_data(n.func.value)
                        and n.func.attr in ("update", "setdefault")):
                    raise AssertionError(
                        f"`st.data.{n.func.attr}()` in a state-init branch is "
                        f"a write form this walk does not collect. Teach "
                        f"`_state_init_branch_keys` about it — leaving it "
                        f"unseen makes the symmetry guard pass vacuously.")
                if isinstance(n, ast.AugAssign) and _is_st_data(n.target):
                    raise AssertionError(
                        "augmented assignment to st.data in a state-init "
                        "branch is not collected by this walk")
                if not isinstance(n, ast.Assign):
                    continue
                for tgt in n.targets:
                    # st.data["x"] = ...
                    if (isinstance(tgt, ast.Subscript)
                            and _is_st_data(tgt.value)
                            and isinstance(tgt.slice, ast.Constant)
                            and isinstance(tgt.slice.value, str)):
                        found.add(tgt.slice.value)
                    # st.data = {"x": ..., ...}
                    if _is_st_data(tgt) and isinstance(n.value, ast.Dict):
                        found |= {k.value for k in n.value.keys
                                  if isinstance(k, ast.Constant)
                                  and isinstance(k.value, str)}
        return found

    return keys(node.body), keys(node.orelse)


def test_no_resume_only_state_keys(leerie):
    """No key may be seeded on the resume path but absent from the fresh one.

    A key written only under `if args.resume:` is absent from every FRESH
    run — the common path. What that costs depends on how it is read:
    `dangerously_force_strict_output` is a record, so the run merely became
    unattributable; `skip_coverage_check` is read straight off `st.data` by
    `phase_planning_coverage_gate`, so `.get()` returned None and the flag
    was silently inert on every fresh run from the commit that added it.

    This is DERIVED rather than a list of key names on purpose. The first
    fix here parametrised an AST guard over a hand-maintained tuple; that
    tuple had two entries and this rule immediately found a third defect it
    could never have caught. CLAUDE.md records the same lesson three times
    over (PRs #180-#183), each an enumeration replaced by a derivation only
    after a missed instance shipped.

    The reverse direction is deliberately NOT asserted: `task`,
    `started_at` and `worker_count` are legitimately fresh-only, since a
    resumed run already has them.

    This is also what makes hand-seeded flag tests safe everywhere else.
    Several files set `st.data["skip_*"] = True` directly and assert on the
    consumer — a shape that verifies a reader, not a feature, and is
    structurally blind to whether anything ever writes the key. This guard
    is the producer half for all of them at once.
    """
    resume_keys, fresh_keys = _state_init_branch_keys(leerie)
    resume_only = resume_keys - fresh_keys
    assert not resume_only, (
        f"state keys seeded on the resume path but NOT on the fresh-run "
        f"path: {sorted(resume_only)}. Every fresh run would be missing "
        f"them. Add each to the `st.data = {{...}}` literal in "
        f"`_run_phases`, mirroring its resume-branch assignment."
    )


def test_state_init_branch_walk_is_not_vacuous(leerie):
    """Anti-vacuity control for the guard above.

    An empty or wrongly-located `resume_keys` makes `resume_keys -
    fresh_keys` trivially empty, so the guard would pass forever against a
    broken walk. `leerie_version` is known to be seeded on both paths, and
    the fresh arm is known to carry the three fresh-only keys — if the walk
    cannot see those, it is the walk that is broken, not the code.
    """
    resume_keys, fresh_keys = _state_init_branch_keys(leerie)
    assert "leerie_version" in resume_keys and "leerie_version" in fresh_keys, (
        "the branch walk is broken — leerie_version is seeded on both paths")
    assert {"task", "started_at", "worker_count"} <= fresh_keys, (
        "the branch walk is broken — the fresh arm seeds these three")
    # Both arms must be non-trivial, or a subset test could pass by accident.
    assert len(resume_keys) > 5 and len(fresh_keys) > 5


def test_state_fields_matches_spec_table(leerie):
    """STATE_FIELDS and the IMPLEMENTATION.md field table must list the
    same names. Symmetric: catches drift in either direction."""
    code = set(leerie.STATE_FIELDS)
    spec = _spec_fields()

    missing_from_spec = code - spec
    missing_from_code = spec - code
    assert not missing_from_spec and not missing_from_code, (
        f"STATE_FIELDS vs IMPLEMENTATION.md §8 field table drift:\n"
        f"  in STATE_FIELDS but not in spec table: "
        f"{sorted(missing_from_spec)}\n"
        f"  in spec table but not in STATE_FIELDS: "
        f"{sorted(missing_from_code)}"
    )


def test_every_st_data_write_is_declared(leerie):
    """Every key the orchestrator writes to `st.data` (directly,
    via setdefault, or in the run-init dict literal) must appear in
    STATE_FIELDS. Catches the case where a new write is added without
    updating the canonical tuple."""
    declared = set(leerie.STATE_FIELDS)
    written = _runtime_field_writes()

    undeclared = written - declared
    assert not undeclared, (
        f"leerie.py writes state keys that are not in STATE_FIELDS: "
        f"{sorted(undeclared)}. Add them to STATE_FIELDS and to the "
        f"IMPLEMENTATION.md §8 field table in the same change."
    )


def test_state_fields_has_no_duplicates(leerie):
    """STATE_FIELDS is a tuple, so a stray duplicate would not be caught
    by the set-equality test above. Check explicitly."""
    fields = leerie.STATE_FIELDS
    assert len(fields) == len(set(fields)), (
        f"STATE_FIELDS contains duplicates: "
        f"{sorted(f for f in fields if fields.count(f) > 1)}"
    )
