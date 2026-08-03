"""No module-level function in the orchestrator is defined but never used.

Three had accumulated, all pre-existing, found by AST call-graph analysis
during the 2026-08-01 audit:

  _confidence_issues       every gate that consumed it was replaced by an
                           independent adversarial verifier (DESIGN §8).
                           IMPLEMENTATION.md even recorded that it had
                           "zero remaining callers" — and it was left in
                           place, with its unit tests the only callers.
  _repo_map_cache_key      described a cache key nothing computed.
  _is_node_offline_relink  superseded by `_filter_residual_deps`, which
                           tests the same condition inline and
                           deliberately more broadly. A test pinned its
                           *existence*, which only guaranteed it stayed
                           dead.

Dead code in a single-file orchestrator is worse than dead code elsewhere:
CLAUDE.md's stated design goal is that you can read the whole control flow
top-to-bottom in one sitting, and an unused helper reads as live. Two of
these three were also gates that were deliberately removed — leaving the
helper behind is an invitation to wire it back up.

This guard is deliberately whole-module rather than a list of the three
names: pinning names would catch a regression on exactly these and nothing
else, which is the same mistake as the existence-pin it replaces.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"

# Dunder protocol methods are invoked by the interpreter, never by name.
_PROTOCOL = {"__getattr__", "__del__", "__init__", "__enter__", "__exit__",
             "__aenter__", "__aexit__", "__repr__", "__str__", "__eq__",
             "__hash__", "__iter__", "__next__", "__len__", "__contains__"}

# Deliberately uncalled from Python, with a documented reason. Keep this tiny
# and make every entry justify itself — the whole point of the sweep is that
# an unused helper reads as live control flow.
_INTENTIONALLY_UNCALLED = {
    # A parity anchor, not dead code: `scripts/new-worktree.sh` and
    # `scripts/integrate.sh` build the same `leerie/subtasks/<run-id>/<sid>`
    # string, and this helper exists so that shape is grep-able from Python
    # and any future Python call site goes through one function. Its own
    # docstring says so, and `tests/test_branch_namespaces_dont_collide.py`
    # asserts the Python and bash forms agree. Deleting it would remove the
    # anchor that test checks against.
    "_compute_subtask_branch",
}


@pytest.fixture(scope="module")
def tree() -> ast.Module:
    return ast.parse(_ORCH.read_text())


def _referenced(tree: ast.Module) -> set[str]:
    """Every name used in any way — called, passed, patched, compared."""
    out: set[str] = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Name):
            out.add(n.id)
        elif isinstance(n, ast.Attribute):
            out.add(n.attr)
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            # A name reached only by getattr()/monkeypatch string is still
            # a use; counting string constants keeps this from firing on
            # indirection rather than on genuine death.
            out.add(n.value)
    return out


def test_no_private_module_level_function_is_unreferenced(tree: ast.Module):
    """Private functions only.

    A *public* name is API surface: `run_rebaser` is invoked from
    `scripts/host-finalize.sh`, `run_recapture_deps` from the launcher's
    `config --recapture` arm, and `compose_pr_body` from bash. None is
    referenced inside `leerie.py` itself, so a module-scoped scan would call
    them dead — which is why this checks only the underscore-prefixed
    helpers. Privacy is exactly the claim "nothing outside this file calls
    me".

    This docstring previously also listed `compute_subtask_branch` and
    `resolve_token_probe_cache_sec` as bash-called. That was wrong — neither
    had any caller outside this repo's tests. Renaming both private brought
    them under this sweep, which caught them immediately:

    - `resolve_token_probe_cache_sec` was a genuine wiring bug — nothing
      called it, so its documented `LEERIE_TOKEN_PROBE_CACHE_SEC` env var and
      `leerie.toml` key silently did nothing. It is now wired, and back to
      being **public**: it belongs to the `resolve_*` cap-resolver family
      alongside 41 public siblings, so privatising it was itself an
      over-application (see
      `test_helper_naming_convention.py::test_caps_wiring_uses_public_resolvers`).
    - `_compute_subtask_branch` is a deliberate parity anchor, now in
      `_INTENTIONALLY_UNCALLED`."""
    defined = {
        n.name: n.lineno
        for n in tree.body                       # module level only
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
        and n.name.startswith("_")
    }
    used = _referenced(tree)
    dead = sorted(
        (ln, nm) for nm, ln in defined.items()
        if nm not in used and nm not in _PROTOCOL
        and nm not in _INTENTIONALLY_UNCALLED
    )
    assert not dead, (
        "these functions are defined but never referenced anywhere in "
        "orchestrator/leerie.py: "
        + ", ".join(f"{nm} (line {ln})" for ln, nm in dead)
        + ". Delete it, or wire it up — a helper nothing calls reads as "
          "live control flow to the next person through the file."
    )


def test_the_guard_can_actually_fail(tree: ast.Module):
    """Anti-vacuity: the analysis must find real functions to check, and a
    planted unreferenced one must be caught."""
    defined = [n for n in tree.body
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    private = [n for n in defined if n.name.startswith("_")]
    assert len(private) > 100, (
        "the private-function scan found almost nothing — the guard would "
        "pass vacuously")

    planted = ast.parse("def _guard_probe_never_called():\n    return 1\n")
    merged = ast.Module(body=tree.body + planted.body, type_ignores=[])
    used = _referenced(merged)
    assert "_guard_probe_never_called" not in used
