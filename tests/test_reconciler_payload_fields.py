"""Every field `prompts/reconciler.md` names as a signal must be shipped.

`prompts/reconciler.md`'s `conditional_drop` rule scopes itself to "Signals
in the consumer's `intent`/`scope_note`" — but the payload builder in
`phase_reconcile` shipped `id, title, intent, depends_on,
files_likely_touched, provides, requires` and **not `scope_note`**. The rule
was therefore blind to whichever half of its own signal surface the planner
happened to use.

Measured across 3033 planner subtasks in the local run corpus, more carried
conditional phrasing in `scope_note` alone than in `intent` alone — so this
was not a theoretical gap. (That count came from a crude regex and is only
indicative; the structural drift needs no statistics.)

Shipping the field is the cheaper side of the disagreement to fix: the
alternative is narrowing a documented resolution channel to whichever half of
its signal surface happens to arrive. (Note prompts are **not** a layer in
CLAUDE.md's three-layer rule — that is DESIGN → IMPLEMENTATION → code — and
CLAUDE.md's own principle runs the other way, "prompts are advisory, code
enforces". This fix is justified on its merits, not on a layer ordering.)

**Derived for the shipped-fields check, enumerated in the guard-the-guard.**
`test_every_declared_signal_field_is_shipped` parses the field set out of the
prompt at test time rather than hardcoding it, so the payload must keep up with
the prompt. Its companions deliberately pin the set at `{intent, scope_note}`,
so adding a third signal field fails
`test_conditional_drop_rule_still_names_both_halves` and forces the decision to
be explicit rather than silently widening the requirement.
A hardcoded list would pin today's drift and be blind to tomorrow's — the
same lesson `tests/launcher_blocks.py` and
`tests/test_collect_subtrees_integrator_schema.py` record.
"""
from __future__ import annotations

import ast
import inspect
import pathlib
import re
import textwrap

import pytest

REPO = pathlib.Path(__file__).resolve().parent.parent

# The prompt sentence that defines the signal surface. Captured rather than
# assumed so the test fails loudly if the rule is reworded, instead of
# silently checking nothing.
_SIGNAL_SENTENCE = re.compile(
    r"Signals in the consumer's ([^.:]+?):", re.IGNORECASE)


@pytest.fixture(scope="module")
def reconciler_md() -> str:
    return (REPO / "prompts" / "reconciler.md").read_text()


def _declared_signal_fields(md: str) -> set[str]:
    """The backticked field names the prompt says carry the signal."""
    m = _SIGNAL_SENTENCE.search(md)
    assert m, (
        "could not find the 'Signals in the consumer's ...' sentence in "
        "prompts/reconciler.md — if the rule was reworded, update this "
        "derivation rather than deleting it")
    return set(re.findall(r"`([A-Za-z_][A-Za-z0-9_]*)`", m.group(1)))


def _subtask_view_keys(leerie) -> set[str]:
    """The keys of the per-subtask dict `phase_reconcile` ships.

    Extracted STRUCTURALLY, by walking to the `subtask_views.append({...})`
    call and reading the dict literal's keys — not by substring search. Two
    reasons. The comment introduced alongside this fix necessarily names
    `scope_note` while explaining it, so a text scan matches the prose
    describing the thing it checks (the zombie-reaper trap CLAUDE.md
    records). And a scan over `ast.unparse` output has to guess the quote
    style the unparser chose, which is how the first draft of this file
    silently failed to match correct code.
    """
    src = textwrap.dedent(inspect.getsource(leerie.phase_reconcile))
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        if (isinstance(f, ast.Attribute) and f.attr == "append"
                and isinstance(f.value, ast.Name)
                and f.value.id == "subtask_views"
                and node.args and isinstance(node.args[0], ast.Dict)):
            return {
                k.value for k in node.args[0].keys
                if isinstance(k, ast.Constant) and isinstance(k.value, str)
            }
    raise AssertionError(
        "could not locate `subtask_views.append({...})` in phase_reconcile; "
        "if the payload builder moved, update this extraction rather than "
        "deleting the guard")


def test_derivation_finds_the_signal_fields(reconciler_md):
    """Anti-vacuity: a derivation that yields nothing would make every
    assertion below pass forever."""
    fields = _declared_signal_fields(reconciler_md)
    assert len(fields) >= 2, (
        f"expected the prompt to name at least two signal fields, got {fields}")
    assert "intent" in fields and "scope_note" in fields, (
        f"unexpected signal-field set {fields}; the guard below is only "
        "meaningful if it reflects the rule actually shipped")


def test_extraction_finds_the_payload_keys(leerie):
    """Anti-vacuity for the other half: an extraction returning an empty
    set would make the subset check below vacuously true."""
    keys = _subtask_view_keys(leerie)
    assert {"id", "title", "intent", "provides", "requires"} <= keys, (
        f"payload keys look wrong ({keys}); the extraction probably matched "
        "the wrong call site")


def test_every_declared_signal_field_is_shipped(leerie, reconciler_md):
    """The defect: the prompt named `scope_note`, the payload omitted it."""
    declared = _declared_signal_fields(reconciler_md)
    missing = sorted(declared - _subtask_view_keys(leerie))
    assert not missing, (
        f"prompts/reconciler.md tells the reconciler to read {missing} but "
        "phase_reconcile's subtask_views never ships them — the worker is "
        "instructed to use a field it cannot see")


def test_scope_note_reaches_the_worker_payload(leerie):
    """Named pin for the field that actually shipped broken, so a
    regression fails with a message naming it rather than a generic diff.

    Mirrors the discipline in `tests/test_resumable_planning_keys.py`,
    which keeps named pins alongside a generic parity sweep for the same
    reason.
    """
    assert "scope_note" in _subtask_view_keys(leerie)


def test_conditional_drop_rule_still_names_both_halves(reconciler_md):
    """Guard-the-guard: if the rule is narrowed to `intent` only, this
    fix becomes unnecessary and the derivation above would silently stop
    requiring `scope_note`. Force that decision to be explicit."""
    fields = _declared_signal_fields(reconciler_md)
    assert fields == {"intent", "scope_note"}, (
        f"the conditional_drop signal surface changed to {fields}; "
        "re-examine whether phase_reconcile's payload still matches")


# --- the value, not just the key ------------------------------------------

def test_scope_note_VALUE_reaches_the_worker_prompt(leerie, monkeypatch,
                                                    tmp_path):
    """The key-presence tests above are necessary and NOT sufficient.

    `_subtask_view_keys` reads the dict's keys and never its values, so
    `"scope_note": ""` — the key shipped with the planner's text thrown
    away — passes every one of them. Verified by mutation: replacing
    `s.get("scope_note", "")` with `""` leaves all five green while the
    reconciler is blind exactly as it was before the fix.

    That is CLAUDE.md's "presence is not evaluation" trap, the one that let
    a `NameError` ship past a key-presence AST walk. So this test drives the
    REAL `phase_reconcile` with a stubbed worker and asserts a planner's
    literal `scope_note` string survives into the rendered
    `RECONCILER INPUT:` payload the worker actually receives.

    Harness mirrors
    `test_reconciler_cycle_gate.py::test_unresolved_retry_loop_integration_with_stubbed_reconciler`.
    """
    import asyncio
    import json

    # >120 chars each, so a silent `[:120]` truncation changes them. Short
    # markers made the truncation mutant a no-op — verified.
    marker = ("test-001-scope-note-" + "x" * 120 + "-END-OF-test-001-NOTE")
    sibling = ("test-002-scope-note-" + "y" * 120 + "-END-OF-test-002-NOTE")
    other = ("config-001-scope-note-" + "z" * 120 + "-END-OF-config-001-NOTE")
    plans = [
        {"domain": "testing", "status": "ready", "subtasks": [
            {"id": "test-001", "title": "t", "intent": "i",
             "scope_note": marker,
             "success_criteria_seed": "s", "size": "small",
             "files_likely_touched": [], "depends_on": [],
             "provides": [], "requires": [
                 {"tag": "unprovided-cap", "extent": "in_plan"}]},
            # A SECOND subtask in the SAME plan. With one per plan, "every
            # subtask ships subtask-0's note" is a no-op because subtask-0
            # is itself — verified: the mutant passed.
            {"id": "test-002", "title": "t2", "intent": "i2",
             "scope_note": sibling,
             "success_criteria_seed": "s", "size": "small",
             "files_likely_touched": [], "depends_on": [],
             "provides": [], "requires": []}]},
        {"domain": "configuration-build", "status": "ready", "subtasks": [{
            "id": "config-001", "title": "c", "intent": "i2",
            "scope_note": other, "success_criteria_seed": "s",
            "size": "small", "files_likely_touched": [], "depends_on": [],
            "provides": ["something-else"], "requires": []}]},
    ]

    calls: list[dict] = []

    async def fake_claude_p(**kwargs):
        calls.append(kwargs)
        # Must ADDRESS the named unresolved entry or phase_reconcile
        # die()s on the must-include validator before returning — which
        # would make this test fail for a reason unrelated to its subject.
        return {"renames": [],
                "tag_ops": [{"op": "drop_require", "sid": "test-001",
                             "tag": "unprovided-cap",
                             "reason": "over-specified for this fixture"}],
                "added_requires": [], "added_subtasks": [],
                "dependency_edges": [], "merged_subtasks": []}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    leerie_root = tmp_path / ".leerie"
    run_id = "test-scope-note-value"
    (leerie_root / "runs" / run_id).mkdir(parents=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "t", "worker_count": 0}
    st.save()

    asyncio.run(leerie.phase_reconcile(
        plans, "t", st, dict(leerie.DEFAULT_CAPS),
        {"reconciler": "sonnet"}, {"reconciler": "medium"}))

    assert calls, (
        "the reconciler was never spawned — the fixture must leave at least "
        "one unresolved in_plan requires, or this test proves nothing")
    prompt = calls[0].get("user_prompt") or ""
    assert "RECONCILER INPUT:" in prompt

    # Parse the payload and check each note against ITS OWN subtask. Asserting
    # the marker appears *somewhere* is not enough: verified by mutation, both
    # `s.get("scope_note", "")[:120]` (every note silently truncated) and
    # shipping subtask-0's note for every subtask leave a "somewhere" check
    # green — the second being the same cross-pairing defect
    # `test_every_unresolvable_entry_is_named` guards against in the die
    # message.
    payload = json.loads(prompt.split("RECONCILER INPUT:", 1)[1]
                         .rsplit("}", 1)[0] + "}")
    views = {v["id"]: v for v in payload["subtasks"]}
    assert set(views) == {"test-001", "test-002", "config-001"}, (
        f"unexpected subtask views {sorted(views)}")
    for sid, expected in (("test-001", marker), ("test-002", sibling),
                          ("config-001", other)):
        got = views[sid]["scope_note"]
        assert got == expected, (
            f"{sid}'s scope_note is missing, truncated, or belongs to another "
            f"subtask: {got[:60]!r}... (len {len(got)}, expected "
            f"{len(expected)})")
