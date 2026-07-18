"""Coupling tests for INSPECT_TOOLS — the tool bucket for classifier,
planner, reconciler, plan_overlap_judge, and provision.

These workers run in the real repo cwd (no worktree isolation), so they
cannot use --dangerously-skip-permissions. INSPECT_TOOLS preserves the
DESIGN §12 "read-only worker" contract mechanically: read tools plus
allowlisted Bash(<verb>:*) patterns for cross-cwd inspection, no
Write/Edit. Anything outside the allowlist falls through and is rejected
in non-interactive mode.

These tests pin both halves so a future edit that adds Write/Edit or
swaps in a bare Bash wildcard (which would defeat the allowlist) fails
loudly.
"""
from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _entries(bucket: str) -> list[str]:
    """Split the bucket string on commas — but not commas inside Bash(...)
    parens. The current INSPECT_TOOLS has no comma inside a Bash pattern
    (commas are between entries, spaces or colons inside patterns), so a
    plain split is correct today. Guarded with a sanity assertion below."""
    out: list[str] = []
    depth = 0
    cur = ""
    for ch in bucket:
        if ch == "(":
            depth += 1
            cur += ch
        elif ch == ")":
            depth -= 1
            cur += ch
        elif ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return [e.strip() for e in out if e.strip()]


def test_inspect_tools_has_bash_patterns(leerie):
    """At least one Bash(<verb>:*) pattern must be present — that's the
    whole point of the bucket. Without it, classifier/planner/reconciler/plan_overlap_judge/provision
    can't run ls/find/cat without per-call permission prompts (which are
    never granted in -p mode)."""
    entries = _entries(leerie.INSPECT_TOOLS)
    bash_patterns = [e for e in entries if e.startswith("Bash(")]
    assert bash_patterns, (
        "INSPECT_TOOLS must contain at least one Bash(...) pattern so the "
        "inspect-bucket workers can run read-only shell commands without "
        "per-call permission prompts"
    )


def test_inspect_tools_excludes_write_and_edit(leerie):
    """No Write/Edit — the §12 read-only-worker contract."""
    entries = set(_entries(leerie.INSPECT_TOOLS))
    assert "Write" not in entries, (
        "INSPECT_TOOLS must not grant Write — DESIGN §12 read-only contract"
    )
    assert "Edit" not in entries, (
        "INSPECT_TOOLS must not grant Edit — DESIGN §12 read-only contract"
    )


def test_inspect_tools_excludes_bare_bash(leerie):
    """A bare `Bash` entry would auto-approve ANY shell command, defeating
    the allowlist. Patterns only — Bash(<verb>:*) form."""
    entries = set(_entries(leerie.INSPECT_TOOLS))
    assert "Bash" not in entries, (
        "INSPECT_TOOLS must use Bash(<verb>:*) patterns, not bare Bash — "
        "a wildcard would defeat the read-only-shell allowlist"
    )


def test_inspect_tools_verbs_all_carry_wildcard(leerie):
    """Every Bash verb pattern carries `:*` except argument-less `Bash(pwd)`.

    Distinct from test_inspect_tools_excludes_bare_bash above: that guards a
    bare `Bash` *entry* (the whole tool, unrestricted). This guards a bare
    `Bash(verb)` *pattern*, which the Claude Code permission docs define as an
    EXACT-STRING match ("Bash(npm run build) matches the exact command npm run
    build") — so it permits only the literal zero-argument form and denies
    every real invocation.

    INSPECT_TOOLS shipped `Bash(git status)` this way: every sibling git verb
    (`git log:*`, `git show:*`, `git diff:*`, `git branch:*`, `git ls-files:*`)
    already had the suffix, so `git status` was the lone outlier, silently
    denying `git status --porcelain` etc. for the classifier, planner,
    reconciler, overlap-judge, and provision workers.
    """
    import re
    bare = [p for p in re.findall(r"Bash\([^)]*\)", leerie.INSPECT_TOOLS)
            if ":*" not in p]
    assert bare == ["Bash(pwd)"], (
        f"unexpected bare (exact-match) Bash patterns in INSPECT_TOOLS: "
        f"{bare} — every verb that takes arguments needs the `:*` "
        "trailing wildcard"
    )


def test_inspect_tools_includes_read_tools(leerie):
    """Read/Grep/Glob still need to be in the bucket — they're the
    primary tools and the Bash patterns are a fallback for cross-cwd
    inspection."""
    entries = set(_entries(leerie.INSPECT_TOOLS))
    for name in ("Read", "Grep", "Glob"):
        assert name in entries, f"INSPECT_TOOLS must include {name}"


def test_classifier_call_site_uses_inspect_tools():
    """Source-text check: the phase_classify worker invocation must pass
    allowed_tools=INSPECT_TOOLS, not READ_TOOLS (removed) or ACT_TOOLS
    (would grant Write/Edit)."""
    src = LEERIE_PY.read_text()
    start = src.index("async def phase_classify(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "allowed_tools=INSPECT_TOOLS" in body, (
        "phase_classify must pass allowed_tools=INSPECT_TOOLS to claude_p"
    )
    assert "allowed_tools=ACT_TOOLS" not in body
    assert "allowed_tools=RUN_TOOLS" not in body


def test_planner_call_site_uses_inspect_tools():
    """plan_one is a closure inside phase_plan; check the enclosing
    function's body for the call site."""
    src = LEERIE_PY.read_text()
    start = src.index("async def phase_plan(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "allowed_tools=INSPECT_TOOLS" in body, (
        "phase_plan's plan_one must pass allowed_tools=INSPECT_TOOLS"
    )
    assert "allowed_tools=ACT_TOOLS" not in body
    assert "allowed_tools=RUN_TOOLS" not in body


def test_reconciler_call_site_uses_inspect_tools():
    src = LEERIE_PY.read_text()
    start = src.index("async def phase_reconcile(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "allowed_tools=INSPECT_TOOLS" in body, (
        "phase_reconcile must pass allowed_tools=INSPECT_TOOLS to claude_p"
    )
    assert "allowed_tools=ACT_TOOLS" not in body
    assert "allowed_tools=RUN_TOOLS" not in body


def test_overlap_judge_call_site_uses_inspect_tools():
    """phase_overlap_judge (DESIGN §5 *Cross-domain surface overlap*)
    runs in the real repo cwd like the other judgment workers, so it
    must inherit the same INSPECT_TOOLS allowlist — no Write/Edit, only
    allowlisted Bash verbs. A future edit swapping in ACT_TOOLS would
    silently waive the §12 read-only contract for the overlap judge."""
    src = LEERIE_PY.read_text()
    start = src.index("async def phase_overlap_judge(")
    end = src.index("\nasync def ", start + 1)
    body = src[start:end]
    assert "allowed_tools=INSPECT_TOOLS" in body, (
        "phase_overlap_judge must pass allowed_tools=INSPECT_TOOLS to claude_p"
    )
    assert "allowed_tools=ACT_TOOLS" not in body
    assert "allowed_tools=RUN_TOOLS" not in body


def test_satisfied_probe_call_site_uses_satisfied_probe_tools():
    """The satisfied_probe worker (DESIGN §8 *Already-satisfied subtask
    elimination*) must pass allowed_tools=SATISFIED_PROBE_TOOLS — the
    base-tree-only bucket. A regression to INSPECT_TOOLS would re-grant
    history-spanning git (`git log --all`, non-HEAD `git show <ref>:`),
    which lets the probe find a deliverable on some OTHER branch and
    report satisfied=true — silently deleting real work (calibration
    measured 12/12 false-positives with full INSPECT_TOOLS latitude).
    ACT_TOOLS/RUN_TOOLS would grant Write/Edit. No other test guards this
    call site: the behavioral stub in test_filter_satisfied_subtasks.py
    swallows allowed_tools via **_kw, and test_resolve_skip_satisfied_check
    checks the CONSTANT's content but not that the call site uses it.

    Boundary note: unlike the sibling call-site tests above (which slice to
    the next `async def`), filter_satisfied_subtasks is followed by a large
    NON-async block (the provisioning helpers), so the next-`async def`
    bound would span ~40KB of unrelated code and make the negative asserts
    scoped far too broadly. Slice to the next top-level `def `/`async def`
    instead — just this function's body.
    """
    import re
    src = LEERIE_PY.read_text()
    start = src.index("async def filter_satisfied_subtasks(")
    m = re.search(r"\n(?:async def |def )", src[start + 10:])
    end = start + 10 + m.start() if m else len(src)
    body = src[start:end]
    # Guard: the slice must actually contain the worker invocation, so a
    # future refactor that moves the claude_p call out of this function
    # can't make the tool-scope asserts vacuously pass.
    assert "claude_p(" in body, (
        "filter_satisfied_subtasks body no longer contains the claude_p "
        "call — this test's boundary or the worker wiring changed"
    )
    assert "allowed_tools=SATISFIED_PROBE_TOOLS" in body, (
        "filter_satisfied_subtasks must pass "
        "allowed_tools=SATISFIED_PROBE_TOOLS to claude_p"
    )
    assert "allowed_tools=INSPECT_TOOLS" not in body
    assert "allowed_tools=ACT_TOOLS" not in body
    assert "allowed_tools=RUN_TOOLS" not in body
