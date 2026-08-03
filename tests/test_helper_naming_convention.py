"""Module-level helpers with no external callers must be `_`-prefixed.

This exists because the convention kept slipping past human review. In PR #151
alone, `partition_issues_by_severity` was found public-but-internal and made
private in one commit — and then `repair_prescribed_commands` and
`replan_domain_closure` were introduced public-but-internal by two *later
commits of the same PR*. Three instances, one PR, one reviewer noticing each
time. That is a job for CI, not for vigilance.

Why it matters beyond tidiness: `tests/test_no_dead_functions.py` deliberately
scopes its dead-code sweep to underscore-prefixed helpers, because public names
are API surface invoked from outside the module (`run_rebaser` from
`scripts/host-finalize.sh`, `compose_pr_body` from bash, and so on). A helper
that is public *but has no external caller* therefore sits outside that guard
for no reason — it is neither API nor covered by the dead-code check.

The rule: a module-level `def` in `orchestrator/leerie.py` that nothing outside
that file **calls** must start with `_`. Anything genuinely reached from bash,
another module, or the plugin surface stays public and is listed in
`_EXTERNAL_API` below with the caller that justifies it.

"Calls" is literal: only `.py`, `.sh` and extensionless files (the `leerie`
launcher) are scanned. Markdown was scanned in the first version of this
guard, which exempted 26 helpers on the strength of a documentation mention
alone — and since CLAUDE.md documents nearly every symbol in this repo, that
hole would have swallowed most future offenders too. A doc mention is not a
caller.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ORCH = REPO / "orchestrator" / "leerie.py"

# Public names with a caller outside `orchestrator/leerie.py`. Each entry names
# the justification, so adding one is a deliberate act rather than a shrug.
_EXTERNAL_API = {
    # invoked from bash / the launcher
    "run_rebaser": "scripts/host-finalize.sh",
    "run_recapture_deps": "launcher `config --recapture` arm",
    "compose_pr_body": "finalize bash",
    # process entry points
    "main": "console entry point",
}

# EMPTY, and it must stay that way. This started as a 67-name ratchet of
# pre-existing public-but-internal helpers; all 67 were renamed, so there is
# nothing left to grandfather. Anything added here is a new exemption — prefer
# renaming the helper, or `_EXTERNAL_API` if it genuinely has an outside
# caller (which `test_allowlisted_names_have_a_real_code_caller` verifies).
_GRANDFATHERED = frozenset()

# Whole families that are public by documented convention, not by accident.
_PUBLIC_FAMILIES = (
    # IMPLEMENTATION.md documents these as the per-worker mechanical checks.
    re.compile(r"^check_[a-z_]+$"),
    # Phase functions are the orchestrator's documented pipeline surface.
    re.compile(r"^phase_[a-z_0-9]+$"),
    re.compile(r"^resolve_[a-z_]+$"),
)


def _module_level_defs() -> list[str]:
    tree = ast.parse(ORCH.read_text())
    return [n.name for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def _referenced_outside_orchestrator(name: str) -> bool:
    """True when any file other than `orchestrator/leerie.py` names it.

    Tests are excluded deliberately: a test importing a helper does not make it
    API surface, or every internal helper would qualify and the rule would be
    vacuous.
    """
    pat = re.compile(rf"(?<![_\w]){re.escape(name)}(?![_\w])")
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        # Only things that can actually CALL a Python function. Markdown was
        # scanned originally, which exempted 26 helpers on the strength of a
        # doc mention alone — and since CLAUDE.md documents nearly every
        # symbol here, that hole would swallow most future offenders too.
        if path.suffix not in (".py", ".sh", ""):
            continue
        rel = path.relative_to(REPO).as_posix()
        if rel.startswith(("tests/", ".git/", "docs/")):
            continue
        if rel == "orchestrator/leerie.py":
            continue
        try:
            if pat.search(path.read_text(errors="ignore")):
                return True
        except (OSError, UnicodeDecodeError):
            continue
    return False


def test_internal_helpers_are_underscore_prefixed():
    """The guard. A public module-level def with no caller outside
    `orchestrator/leerie.py`, not in a documented public family, and not
    allowlisted, must be renamed with a leading underscore."""
    offenders = []
    for name in _module_level_defs():
        if name.startswith("_"):
            continue
        if name in _EXTERNAL_API or name in _GRANDFATHERED:
            continue
        if any(p.match(name) for p in _PUBLIC_FAMILIES):
            continue
        if not _referenced_outside_orchestrator(name):
            offenders.append(name)
    assert not offenders, (
        "public module-level helper(s) with no caller outside "
        f"orchestrator/leerie.py: {sorted(offenders)}. Rename with a leading "
        "underscore, or add to _EXTERNAL_API naming the external caller. "
        "Public-but-internal names are needless API surface and sit outside "
        "test_no_dead_functions.py's private-only dead-code sweep."
    )


def test_the_three_pr151_offenders_are_now_private(leerie):
    """Regression pin on the specific names that motivated this guard — all
    three were public-but-internal at some point during PR #151."""
    for name in ("_partition_issues_by_severity",
                 "_repair_prescribed_commands",
                 "_replan_domain_closure"):
        assert hasattr(leerie, name), f"{name} missing or still public"
    for name in ("partition_issues_by_severity",
                 "repair_prescribed_commands",
                 "replan_domain_closure"):
        assert not hasattr(leerie, name), f"{name} is still public"


def test_allowlisted_names_actually_exist():
    """Guard-the-guard: a stale `_EXTERNAL_API` entry would silently exempt a
    name that no longer exists, and could mask a future offender sharing it."""
    defined = set(_module_level_defs())
    stale = [n for n in _EXTERNAL_API if n not in defined]
    assert not stale, f"_EXTERNAL_API names no longer defined: {stale}"


def test_allowlisted_names_have_a_real_code_caller():
    """The justification must be TRUE, not just present.

    `_EXTERNAL_API` originally carried two entries — `_compute_subtask_branch`
    ("worktree scripts") and `_resolve_token_probe_cache_sec` ("launcher") —
    whose stated callers did not exist. Both names were lifted from CLAUDE.md
    prose listing public API and given justifications nobody verified; they
    are referenced only in markdown. `test_allowlisted_names_actually_exist`
    could not catch that, because the names *are* defined.

    An exemption whose reason is false is worse than no exemption: it silently
    removes a name from the guard forever."""
    liars = [n for n in _EXTERNAL_API
             if not _referenced_outside_orchestrator(n)]
    assert not liars, (
        f"_EXTERNAL_API entries with no actual code caller: {sorted(liars)}. "
        "Either the justification is wrong (move the name to _GRANDFATHERED "
        "or make it private), or the caller lives in a file type this scan "
        "does not read — in which case widen the scan deliberately."
    )


def test_grandfathered_set_only_shrinks():
    """The ratchet. Every grandfathered name must still be a public
    module-level def — if one was renamed private (good) or deleted, its entry
    must go too, so the set can only shrink toward zero and never silently
    accumulate new exemptions."""
    public = {n for n in _module_level_defs() if not n.startswith("_")}
    stale = sorted(_GRANDFATHERED - public)
    assert not stale, (
        f"grandfathered names no longer public module-level defs: {stale}. "
        "Remove them from _GRANDFATHERED — the set may shrink, never grow.")


def test_guard_would_catch_a_public_internal_helper():
    """Anti-vacuity: prove the rule fires on a synthetic offender rather than
    passing because every name happens to be exempt."""
    name = "totally_internal_helper_xyz"
    assert not name.startswith("_")
    assert name not in _EXTERNAL_API
    assert not any(p.match(name) for p in _PUBLIC_FAMILIES)
    assert not _referenced_outside_orchestrator(name), (
        "the synthetic name must be unreferenced for this control to mean "
        "anything")
