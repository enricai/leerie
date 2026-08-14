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

REPO_ROOT = Path(__file__).resolve().parent.parent
_ORCH = REPO_ROOT / "orchestrator" / "leerie.py"

# Every abbreviation CATEGORY_ABBREV can put in front of a subtask id.
_DOMAINS = ("feat|bugfix|refactor|perf|test|deps|config|infra|docs")

# Every spelling that decides what a subtask IS from the text of its id.
# `.startswith(` alone was the first version, and `sid[:7] == "bugfix-"`,
# `sid.split("-")[0] == "bugfix"` and `re.match(r"^bugfix-", sid)` all evade it
# while doing exactly the thing the rule forbids.
_PREFIX_RE = re.compile(
    r'startswith\(\s*\(?\s*["\'](?:' + _DOMAINS + r')-'
    r'|\[\s*:\s*\d+\s*\]\s*==\s*["\'](?:' + _DOMAINS + r')-'
    r'|\.split\(["\']-["\']\)\s*\[\s*0\s*\]\s*==\s*["\'](?:' + _DOMAINS + r')'
    r'|re\.(?:match|fullmatch|search)\(\s*r?["\']\^?(?:' + _DOMAINS + r')-'
    r'|\.partition\(["\']-["\']\)\s*\[\s*0\s*\]'
    r'|\[\s*:\s*\d+\s*\]\s*(?:!=|==)\s*["\'](?:' + _DOMAINS + r')-'
    r'|["\'](?:' + _DOMAINS + r')-["\']\s*(?:!=|==)\s*\w+\s*\['
    r'|\bnot\s+in\s*\(\s*["\'](?:' + _DOMAINS + r')'
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
    # Spellings the `.startswith(`-only version let through. Each does exactly
    # what the rule forbids, so the control has to probe them or the widening
    # is unverified.
    assert _PREFIX_RE.search('if sid[:7] == "bugfix-":')
    assert _PREFIX_RE.search('if sid.split("-")[0] == "bugfix":')
    assert _PREFIX_RE.search('if re.match(r"^bugfix-", sid):')
    assert _PREFIX_RE.search('if re.search(r"bugfix-", sid):')
    # Three more the widened version still let through until now.
    assert _PREFIX_RE.search('if sid.partition("-")[0] == "bugfix":')
    assert _PREFIX_RE.search('if sid.split("-")[0] not in ("bugfix",):')
    assert _PREFIX_RE.search('if "bugfix-" != sid[:7]:')
    # and must not fire on unrelated string work
    assert not _PREFIX_RE.search('if path.startswith("src/"):')
    assert not _PREFIX_RE.search('if line.startswith("#"):')
    assert not _PREFIX_RE.search('if ref[:4] == "refs":')
    assert not _PREFIX_RE.search('if re.match(r"^\\d+\\.\\d+", version):')


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


def test_the_planner_prompt_asks_for_the_declaration():
    """The advisory half of the §12 split.

    `check_symptom_evidence` is scoped entirely by `fixes_reported_symptom`,
    which only a planner can set. Code honours the field; the PROMPT is the
    only thing that gets it filled in. Drop that instruction and the check goes
    silently inert on every run, with no test failing and no log line — the
    same class as `artifact_paths`, where a pathless collision silently
    disabled a check for months.
    """
    src = (REPO_ROOT / "prompts" / "planner.md").read_text()
    assert "fixes_reported_symptom" in src, (
        "the planner must be told to set this; nothing else can")
    assert "Do not infer this from the subtask id" in src, (
        "the instruction must also say the id is not evidence — re-homed ids "
        "are what produced 10 of 10 false positives under the old scoping")


def test_the_implementer_prompt_reads_the_same_field():
    """The implementer's reproduce-the-symptom step keys on the same
    declaration, and the field reaches it: `_write_plan` passes the whole
    subtask dict through to `subtasks/<sid>.json`."""
    src = (REPO_ROOT / "prompts" / "implementer.md").read_text()
    assert "fixes_reported_symptom" in src


def test_the_field_is_on_the_subtask_schema(leerie):
    """Without this the planner cannot emit it however hard the prompt asks."""
    props = (leerie.SCHEMAS["planner"]["properties"]["subtasks"]["items"]
             ["properties"])
    assert props["fixes_reported_symptom"]["type"] == "boolean"
