"""The spec's enumeration of `_effective_source_of_truth` readers is DERIVED.

`docs/IMPLEMENTATION.md` states that every consumer holding a `State` reads the
source-of-truth through `_effective_source_of_truth(st)`, and then names them.
That universal claim is only as good as the list defining it — and the list was
hand-maintained, so it drifted **inside the commit that introduced it**: PR #215
added a fourth reader (`phase_reconcile`, feeding the phase-2½ abort message) as
a deliberate design step, and shipped prose naming three.

Nothing caught it. The behaviour was correct and covered; only the spec was
short, and a code-surface spec that under-describes the code is a defect under
CLAUDE.md's three-layer rule.

**This file exists so the enumeration cannot silently drift again.** Same move
CLAUDE.md records four times over (#180–#183): replace an enumeration with a
derivation, because two copies of a list drift and a derived list cannot. Prior
art for the shape: `tests/launcher_blocks.py`, `_child_env_blocks()` in
`tests/test_leerie_commit.py`, `tests/test_no_dead_functions.py`.

Two traps, both hit while writing this file and both worth keeping:

**(1) Scope the check to the enumeration, not the document.** The first draft
asserted each derived name appeared anywhere in the file and passed against the
unfixed prose — both documents mention every phase function many times, so
co-occurrence proved nothing. The span is extracted by regex instead, the same
idiom `tests/test_reconciler_payload_fields.py` uses for the reconciler's signal
sentence.

**(2) Only IMPLEMENTATION's claim is exhaustive.** CLAUDE.md's sentence
describes what `tests/test_source_of_truth_delivery.py` source-couples, which is
legitimately three of the four — the fourth is pinned by
`tests/test_unresolvable_die_message.py` instead. Requiring four there would
force a false statement. So CLAUDE.md gets the property that actually serves a
reader: if it names a subset, it must say where the rest is covered.
"""
from __future__ import annotations

import ast
import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
LEERIE_PY = REPO / "orchestrator" / "leerie.py"

HELPER = "_effective_source_of_truth"

# The exhaustive claim in the canonical layer. Captured rather than assumed, so
# a rewording fails loudly instead of silently checking nothing.
_SPEC_ENUMERATION = re.compile(
    r"Every consumer \*\*that holds a `State`\*\* reads the value through "
    r"`_effective_source_of_truth\(st\)`(.*?)and none may fall back to a "
    r"hardcoded tier\.", re.S)

# CLAUDE.md's subset claim. Anchored on a phrase that occurs once; a bare
# "source-couples" matches an unrelated earlier paragraph (verified).
_CLAUDE_CLAUSE = re.compile(
    r"The delivery file also source-couples(.*?)the \*\*deleted\*\*", re.S)


def _consumers() -> dict[str, int]:
    """Every module-level function containing a `_effective_source_of_truth(...)` call.

    Resolved to the ENCLOSING MODULE-LEVEL function, not the immediate scope, so
    a call inside a nested closure is attributed to the phase that owns it —
    which is how the docs refer to them, and the case the hand-written list
    missed. Returns {function_name: call_lineno} so a failure is actionable.
    """
    tree = ast.parse(LEERIE_PY.read_text())
    found: dict[str, int] = {}
    for top in tree.body:
        if not isinstance(top, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for sub in ast.walk(top):          # walk() descends into nested defs
            if (isinstance(sub, ast.Call) and isinstance(sub.func, ast.Name)
                    and sub.func.id == HELPER):
                found.setdefault(top.name, sub.lineno)
    return found


# --- anti-vacuity: a derivation that finds nothing certifies everything ------

def test_the_derivation_finds_the_readers():
    """A walk returning {} would make every assertion below vacuously true."""
    consumers = _consumers()
    assert len(consumers) >= 4, (
        f"expected at least 4 {HELPER} readers, derived {consumers}; the walk "
        "probably stopped matching (renamed helper? moved call sites?) — fix "
        "the derivation rather than lowering this bound")


def test_the_derivation_sees_a_reader_nested_in_a_closure():
    """THE load-bearing test. `phase_reconcile`'s reader lives inside the
    `_check_unresolvable` closure, and a walk over direct `def` bodies only
    would miss it — silently reproducing the drift this file prevents.

    Falsified live: a walk that stops at nested `def`s derives exactly
    `{phase_plan, _write_plan, _compose_pr_via_llm}` — the original wrong list,
    reproduced — and fails **two** tests, this one and the `>= 4` bound above.
    Two nets rather than one is deliberate; the bound catches a shrunken set
    generically, this test says which reader went missing and why.
    """
    assert "phase_reconcile" in _consumers(), (
        "the derivation missed the reader nested inside `_check_unresolvable` — "
        "it is resolving only direct function bodies, not nested closures")


# --- the guard: the canonical layer's list must be exhaustive ---------------

def test_the_spec_enumeration_names_every_derived_consumer():
    """The defect: the spec named 3 of the 4 readers and read as exhaustive."""
    text = (REPO / "docs" / "IMPLEMENTATION.md").read_text()
    m = _SPEC_ENUMERATION.search(text)
    assert m, (
        "could not find the 'Every consumer that holds a `State`' sentence in "
        "docs/IMPLEMENTATION.md — if it was reworded, update this regex rather "
        "than deleting the guard")
    span = m.group(1)
    missing = sorted(n for n in _consumers() if f"`{n}`" not in span)
    assert not missing, (
        f"docs/IMPLEMENTATION.md claims to enumerate every `State`-holding "
        f"{HELPER} reader but omits {missing}. The sentence reads as "
        f"exhaustive; the enumeration is: {span.strip()!r}")


def test_the_spec_enumeration_is_scoped_to_the_sentence():
    """Guard-the-guard for trap (1). The span must be the enumeration itself,
    not the document — a whole-file search passes against the original defect,
    because IMPLEMENTATION.md names every phase function elsewhere many times.
    """
    text = (REPO / "docs" / "IMPLEMENTATION.md").read_text()
    span = _SPEC_ENUMERATION.search(text).group(1)
    assert len(span) < 400, (
        f"the extracted span is {len(span)} chars — too wide to be the "
        "enumeration, so the guard would pass on unrelated mentions")
    assert "phase_reconcile" in text.replace(span, ""), (
        "sanity: IMPLEMENTATION.md should mention phase_reconcile outside this "
        "sentence too — that ambient mention is exactly why the check must be "
        "scoped to the span")


# --- CLAUDE.md: a subset claim must say where the rest is covered -----------

def test_claude_md_subset_claim_points_at_the_remaining_coverage():
    """CLAUDE.md (now docs/TESTING.md, after the docs-001 CLAUDE.md/TESTING.md
    split) describes what one test file source-couples, which is legitimately
    a subset. What must not happen is a reader concluding the unlisted reader
    is unguarded, so the passage has to name where it IS guarded. Asserting
    four names here would force a false statement instead.
    """
    text = (REPO / "docs" / "TESTING.md").read_text()
    m = _CLAUDE_CLAUSE.search(text)
    assert m, (
        "could not find the 'The delivery file also source-couples' passage "
        "in docs/TESTING.md — if reworded or moved, update this anchor "
        "rather than dropping the guard")
    clause = m.group(1)
    coupled = {n for n in _consumers() if f"`{n}`" in clause}
    uncoupled = sorted(set(_consumers()) - coupled)
    if uncoupled:
        assert "test_unresolvable_die_message.py" in clause, (
            f"CLAUDE.md source-couples only {sorted(coupled)} but {uncoupled} "
            "also read the helper, and the passage does not say where those "
            "are covered — a reader is left to conclude they are unguarded")
