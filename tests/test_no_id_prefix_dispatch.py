"""No check may decide what a subtask IS by parsing its id.

CLAUDE.md's *Language-to-JSON* rule says Python operates on structured fields
and never infers meaning from strings. That is usually read as being about
prose, but a subtask id is a string too, and the same failure follows: a
`bugfix-` prefix was taken as "this subtask fixes a reported symptom", and

  * `_repair_prescribed_commands` mints `{prefix}{900+n:03d}` from the **host**
    subtask's domain, so a verification-only subtask on a bug-fixing host is
    named `bugfix-901`; and
  * a duplicate-provider / overlap merge folds one subtask into another and
    keeps the **survivor's** id, so a `feat-` subtask can end up as `bugfix-002`
    (its own recorded evidence cited `_merged_from: feat-003`).

Measured across the run corpus, `check_symptom_evidence` produced **10 of 10
false positives** — every one a test, coverage or verification subtask that
never had a symptom. See docs/POSTMORTEM-2026-08-14.md, F18.

The structured answers are `_sid_domain_map(plans)` for "what kind of work is
this" and the planner's `fixes_reported_symptom` for "does this fix a reported
symptom".
"""
from __future__ import annotations

import ast
import copy
import inspect
import re
from pathlib import Path

import pytest

_ORCH = Path(__file__).resolve().parent.parent / "orchestrator" / "leerie.py"

# Every abbreviation CATEGORY_ABBREV can put in front of a subtask id.
_PREFIX_RE = re.compile(
    r'startswith\(\s*\(?\s*["\'](?:feat|bugfix|refactor|perf|test|deps|'
    r'config|infra|docs)-'
)


def _strip_docs_and_comments(node: ast.AST) -> str:
    """`node`'s source with comments AND its docstring removed.

    Both are necessary. This region explains the forbidden construct by
    quoting it, in `#` comments and in docstrings alike — so a scan over raw
    source matches the explanation and fails on correct code. `ast.unparse`
    drops comments for free; the docstring has to be removed explicitly.
    This is the trap CLAUDE.md documents for the zombie-reaper guard, which
    strips its docstring for exactly the same reason.
    """
    n = copy.deepcopy(node)
    body = getattr(n, "body", None)
    if (body and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)):
        n.body = body[1:] or [ast.Pass()]
    return ast.unparse(n)


def _functions_dispatching_on_a_prefix() -> list[str]:
    tree = ast.parse(_ORCH.read_text())
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if _PREFIX_RE.search(_strip_docs_and_comments(node)):
            out.append(node.name)
    return out


def test_no_function_dispatches_on_a_subtask_id_prefix():
    offenders = _functions_dispatching_on_a_prefix()
    assert not offenders, (
        "these functions decide what a subtask is by parsing its id, which "
        "survives merges and synthesis and therefore is not evidence of what "
        "the work is. Read the plan's domain via `_sid_domain_map`, or a "
        "declared field, instead:\n  " + "\n  ".join(sorted(offenders))
    )


def test_the_scan_would_catch_a_prefix_dispatch():
    """Falsification control: a scan that matches nothing passes everything."""
    assert _PREFIX_RE.search('if not sid.startswith("bugfix-"):')
    assert _PREFIX_RE.search('x = [s for s in ids if s.startswith("test-")]')
    assert _PREFIX_RE.search('if sid.startswith(("bugfix-", "feat-")):')
    # and must not fire on unrelated string work
    assert not _PREFIX_RE.search('if path.startswith("src/"):')
    assert not _PREFIX_RE.search('if line.startswith("#"):')


def test_symptom_check_reads_the_declaration(leerie):
    """The replacement signal is a planner field, not an id."""
    sig = inspect.signature(leerie.check_symptom_evidence)
    assert "fixes_reported_symptom" in sig.parameters, sig
    fn = next(n for n in ast.walk(ast.parse(_ORCH.read_text()))
              if isinstance(n, ast.FunctionDef)
              and n.name == "check_symptom_evidence")
    src = _strip_docs_and_comments(fn)
    assert "fixes_reported_symptom" in src
    assert 'startswith(' not in src


def test_the_declaration_field_is_optional_and_absence_means_no(leerie):
    """Optional on the schema; the caller coerces absence to False.

    Requiring it would risk the validity-rate collapse a required `severity`
    caused on `wiring_judge` (9 of 66 invalid, all on one field). Absence
    meaning "no" makes the check silent on plans that never fill it, which for
    an advisory check with a measured true-positive count of zero is strictly
    better than noise.
    """
    props = (leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
             ["properties"])
    assert "fixes_reported_symptom" in props
    items = leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
    assert "fixes_reported_symptom" not in items.get("required", [])


def test_synthesised_verification_subtask_declares_false(leerie):
    """The subtask whose id is guaranteed to mislead says so explicitly."""
    fn = next(n for n in ast.walk(ast.parse(_ORCH.read_text()))
              if isinstance(n, ast.FunctionDef)
              and n.name == "_repair_prescribed_commands")
    # Quote-agnostic: ast.unparse normalises string literals to single quotes.
    src = _strip_docs_and_comments(fn).replace('"', "'")
    assert "'fixes_reported_symptom': False" in src, (
        "the synthesised verification subtask must declare that it fixes no "
        "reported symptom — its id is minted from the host subtask's domain "
        "and is exactly the false positive this field exists to prevent")


def test_sid_domain_map_is_invariant_under_a_merge(leerie):
    """The property that makes the domain the right signal.

    A merge keeps the survivor's id, so the id changes meaning. The owning
    plan's domain does not.
    """
    plans = [
        {"domain": "testing", "subtasks": [{"id": "test-001"}]},
        {"domain": "feature-implementation", "subtasks": [{"id": "feat-002"}]},
    ]
    assert leerie._sid_domain_map(plans) == {
        "test-001": "testing", "feat-002": "feature-implementation"}


@pytest.mark.parametrize("bad_plan", [
    {"domain": "testing", "subtasks": None},
    {"domain": None, "subtasks": [{"id": "test-001"}]},
    {"subtasks": [{"id": None}]},
    {},
])
def test_sid_domain_map_tolerates_degenerate_plans(leerie, bad_plan):
    """It feeds advisory checks, so it must never raise on a malformed plan."""
    assert isinstance(leerie._sid_domain_map([bad_plan]), dict)
