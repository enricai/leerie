"""Tests for `.github/workflows/release.yml`.

Nothing in `tests/` previously touched `.github/workflows/`, and
`shellcheck.yml` lints only `scripts/*.sh` — the embedded `run:` shell in
this workflow was untested, which is how it shipped two silent-skip bugs
(see CLAUDE.md's central principle: guarantees that matter and can be
checked mechanically live in code, not in a workflow prompt/comment).

No YAML parser is used (pyyaml isn't a dependency — CLAUDE.md: "pytest is
the only dev dependency"). Instead this file works against the raw text of
the shipped workflow, either by re-running the exact regex embedded in the
`run:` block (regex table) or by pinning structural properties of the YAML
text via string search (gate independence, end-state step).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
WORKFLOW_PATH = REPO_ROOT / ".github" / "workflows" / "release.yml"

# The exact pattern embedded in release.yml's "Parse release version from
# commit subject" step. Bash [[:space:]] is equivalent to Python's \s for
# ASCII whitespace, which is all that appears in real commit subjects.
_RELEASE_SUBJECT_RE = re.compile(
    r"^chore\(release\):[ \t]+([0-9]+\.[0-9]+\.[0-9]+)"
    r"([ \t]+\(#[0-9]+\))?[ \t]*$"
)


def _workflow_text() -> str:
    return WORKFLOW_PATH.read_text()


# ---------------------------------------------------------------------------
# Regex table: the widened pattern must match every real release subject
# (including the v0.9.62 squash-merge casualty) while still rejecting
# malformed subjects.
# ---------------------------------------------------------------------------

_SHOULD_MATCH = [
    "chore(release): 0.9.65",
    "chore(release): 0.9.64",
    "chore(release): 0.9.63",
    "chore(release): 0.9.62 (#62)",  # the squash-merge casualty
    "chore(release): 0.3.15",
    "chore(release): 1.0.0 (#1)",
    "chore(release): 0.9.9 (#999)",
]

_SHOULD_NOT_MATCH = [
    "chore(release): 0.9.62(#62)",  # no space before "(#" — still rejected
    "chore(release): 0.3.15 plus follow-up",
    "chore(release): (#abc)",
    "chore(release):0.3.15",  # no space after colon
    "chore(release): 0.3",  # not three components
    "feat: chore(release): 0.9.62",
    "chore(release): 0.9.62 (62)",  # missing '#'
    "chore(release): 0.9.62 extra (#62)",
]


def test_regex_source_matches_shipped_workflow():
    """Pin that the module-level test regex is byte-identical (modulo the
    bash->python [[:space:]]->[ \\t] translation) to the pattern actually
    shipped in release.yml, so this file cannot silently drift from the
    real workflow."""
    text = _workflow_text()
    marker = "if [[ \"$subject\" =~ "
    start = text.index(marker) + len(marker)
    end = text.index(" ]]; then", start)
    shipped_pattern = text[start:end]
    expected = (
        r"^chore\(release\):[[:space:]]+([0-9]+\.[0-9]+\.[0-9]+)"
        r"([[:space:]]+\(#[0-9]+\))?[[:space:]]*$"
    )
    assert shipped_pattern == expected, (
        f"release.yml regex changed without updating this test's mirror: "
        f"{shipped_pattern!r}"
    )


def test_regex_matches_valid_release_subjects():
    for subject in _SHOULD_MATCH:
        assert _RELEASE_SUBJECT_RE.match(subject), (
            f"expected match: {subject!r}"
        )


def test_regex_rejects_invalid_release_subjects():
    for subject in _SHOULD_NOT_MATCH:
        assert not _RELEASE_SUBJECT_RE.match(subject), (
            f"expected no match: {subject!r}"
        )


def test_regex_extracts_version_ignoring_pr_suffix():
    m = _RELEASE_SUBJECT_RE.match("chore(release): 0.9.62 (#62)")
    assert m is not None
    assert m.group(1) == "0.9.62"


def test_regex_matches_every_historical_release_subject_on_main():
    """Executed against the repo's real history: every `chore(release):`
    subject ever merged to main must match the widened regex. This is the
    131/131 (measured) claim — run live rather than pinned to a stale
    count, so it keeps holding as new releases land."""
    # Resolve a ref that carries full history in whichever environment we run:
    # `origin/main` exists in a full-history PR checkout (detached HEAD, but the
    # remote-tracking ref is present), `main` in a normal local clone, and
    # `HEAD` is the universal fallback — a PR branch descended from main still
    # contains every historical `chore(release):` subject. (CI's default
    # fetch-depth: 1 has no `main` ref at all; test.yml sets fetch-depth: 0.)
    subjects = None
    for ref in ("origin/main", "main", "HEAD"):
        result = subprocess.run(
            ["git", "log", ref, "--pretty=%s"],
            cwd=REPO_ROOT, capture_output=True, text=True,
        )
        if result.returncode == 0:
            subjects = [
                line for line in result.stdout.splitlines()
                if line.startswith("chore(release):")
            ]
            break
    assert subjects is not None, (
        "no release-history ref resolved (origin/main, main, HEAD)"
    )
    assert len(subjects) > 100, (
        "expected the repo's real release history (100+ commits); got "
        f"{len(subjects)} — is this running against a shallow clone?"
    )
    unmatched = [s for s in subjects if not _RELEASE_SUBJECT_RE.match(s)]
    assert unmatched == [], f"regex failed to match real subjects: {unmatched}"


def test_old_regex_misses_the_v0_9_62_subject():
    """Documents the bug this subtask fixes: the pre-fix regex (no optional
    PR-suffix group) rejects the exact subject that shipped v0.9.62's PR
    merge, which is why that release has no tag and no release to this
    day."""
    old_re = re.compile(
        r"^chore\(release\):[ \t]+([0-9]+\.[0-9]+\.[0-9]+)[ \t]*$"
    )
    assert not old_re.match("chore(release): 0.9.62 (#62)")
    assert _RELEASE_SUBJECT_RE.match("chore(release): 0.9.62 (#62)")


# ---------------------------------------------------------------------------
# Gate independence: tag creation and release creation must not share a
# single `if:` condition (Bug 1 — a pre-existing tag silently skipped both).
# ---------------------------------------------------------------------------

def _step_block(text: str, step_name: str) -> str:
    """Return the YAML text of a single step, from its `- name:` line up to
    (not including) the next `- name:` line, or end-of-file if this is the
    last step."""
    marker = f"- name: {step_name}\n"
    start = text.index(marker)
    try:
        next_step = text.index("- name:", start + len(marker))
    except ValueError:
        next_step = len(text)
    return text[start:next_step]


def test_tag_and_release_steps_have_different_if_gates():
    text = _workflow_text()
    tag_step = _step_block(text, "Create and push annotated tag")
    release_step = _step_block(text, "Create GitHub Release")

    tag_if = re.search(r"if: (.+)", tag_step).group(1).strip()
    release_if = re.search(r"if: (.+)", release_step).group(1).strip()

    assert tag_if != release_if, (
        "tag and release steps share one `if:` gate again — a pre-existing "
        "tag will silently skip release creation too (the v0.9.64 bug)"
    )


def test_release_step_does_not_reference_tagcheck():
    text = _workflow_text()
    release_step = _step_block(text, "Create GitHub Release")
    assert "tagcheck" not in release_step, (
        "release step must gate on relcheck, not tagcheck — gating on "
        "tagcheck ties release creation to tag existence again"
    )


def test_tagcheck_step_emits_exists_output():
    text = _workflow_text()
    tagcheck_step = _step_block(text, "Check whether the tag exists (idempotent)")
    assert 'id: tagcheck' in tagcheck_step
    assert "exists=true" in tagcheck_step
    assert "exists=false" in tagcheck_step
    # The old `skip` output name conflated "tag present" (a fact) with
    # "don't release" (a policy) — must not reappear.
    assert "skip=" not in tagcheck_step


def test_tagcheck_uses_nonempty_string_check_not_grep():
    """Unanchored `grep -q "$tag"` matches "v0.9.6" inside "v0.9.64"; the
    fix must use an exact-ref-presence check instead. (The step's comment
    is allowed to mention `grep -q` when explaining why it was replaced —
    only the executable `run:` body must not use it.)"""
    text = _workflow_text()
    tagcheck_step = _step_block(text, "Check whether the tag exists (idempotent)")
    run_body = tagcheck_step.split("run: |", 1)[1]
    executable_lines = [
        line for line in run_body.splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    executable_body = "\n".join(executable_lines)
    assert "grep -q" not in executable_body
    assert '[ -n "$(git ls-remote' in executable_body


def test_relcheck_step_exists_and_probes_via_gh_release_view():
    text = _workflow_text()
    relcheck_step = _step_block(text, "Check whether the release exists (idempotent)")
    assert "id: relcheck" in relcheck_step
    assert "gh release view" in relcheck_step
    assert "exists=true" in relcheck_step
    assert "exists=false" in relcheck_step


def test_tag_step_gates_on_tagcheck_exists():
    text = _workflow_text()
    tag_step = _step_block(text, "Create and push annotated tag")
    assert "steps.tagcheck.outputs.exists != 'true'" in tag_step


def test_release_step_gates_on_relcheck_exists():
    text = _workflow_text()
    release_step = _step_block(text, "Create GitHub Release")
    assert "steps.relcheck.outputs.exists != 'true'" in release_step


def test_release_create_carries_verify_tag():
    text = _workflow_text()
    release_step = _step_block(text, "Create GitHub Release")
    assert "gh release create" in release_step
    assert "--verify-tag" in release_step
    # --generate-notes must be preserved as-is (the --notes-start-tag idea
    # was investigated and cut — the live GitHub API already picks the
    # right prior tag in this repo).
    assert "--generate-notes" in release_step
    assert "--notes-start-tag" not in release_step


# ---------------------------------------------------------------------------
# Final end-state guard: half-broken releases must fail loudly.
# ---------------------------------------------------------------------------

def test_end_state_step_exists_and_gates_on_default_success():
    text = _workflow_text()
    step = _step_block(text, "Verify both tag and release exist")
    # No explicit `if:` beyond the is_release guard means it inherits the
    # default `success()` gate. `always()` would double-report a manifest
    # failure and fire on cancellation — must not appear here.
    assert "always()" not in step
    assert "::error::" in step
    assert re.search(r"\bexit 1\b", step)


def test_end_state_step_checks_both_tag_and_release():
    text = _workflow_text()
    step = _step_block(text, "Verify both tag and release exist")
    assert "git ls-remote" in step
    assert "gh release view" in step


def test_end_state_step_is_last_step_in_the_job():
    text = _workflow_text()
    end_state_marker = "- name: Verify both tag and release exist\n"
    assert text.count(end_state_marker) == 1
    idx = text.index(end_state_marker)
    remaining = text[idx + len(end_state_marker):]
    assert "- name:" not in remaining, (
        "the end-state guard must be the final step so it observes the "
        "true end state of the job"
    )


# ---------------------------------------------------------------------------
# Ordering and untouched-surface pins.
# ---------------------------------------------------------------------------

def test_manifest_check_precedes_tagcheck():
    text = _workflow_text()
    manifest_idx = text.index("- name: Verify both manifests agree with subject version")
    tagcheck_idx = text.index("- name: Check whether the tag exists")
    assert manifest_idx < tagcheck_idx, (
        "the manifest version check must run before tagcheck — a "
        "version-mismatched release must never reach a tag"
    )


def test_checkout_concurrency_permissions_untouched():
    text = _workflow_text()
    assert "actions/checkout@v7" in text
    assert "cancel-in-progress: false" in text
    assert "contents: write" in text


def test_manifest_python_step_untouched():
    text = _workflow_text()
    step = _step_block(text, "Verify both manifests agree with subject version")
    assert "plugin.json" in step
    assert "marketplace.json" in step
    assert "sys.exit(1)" in step
