"""Shipped prompts must not name another project's code.

`prompts/*.md` is product surface: every one of them is sent verbatim to a
worker on **whatever repository leerie is running against**. An identifier
from some other codebase in there is not a cosmetic wart — it teaches the
model that another project's domain model is the canonical example, on a
repo where that symbol does not exist.

The failure is easy to make and hard to see in review. It happened here
(2026-08-01): `auth.user.funeralHomeId` / `getActiveTenantId()` were lifted
out of `tests/test_migration_surface.py`, where they are a harmless fixture,
and promoted into `prompts/planner.md`, where they ship. A test fixture is
contained; a prompt is not. Nothing had shipped, but nothing in the repo
would have stopped it.

Two independent rules, because neither is sufficient alone:

1. **Repo-existence.** A backticked identifier in a prompt must exist
   somewhere in this repository outside `prompts/`, or be an explicit
   `GENERIC_PLACEHOLDERS` entry. A genuinely foreign symbol fails by
   construction — it is not in this codebase — and adding it to the
   allowlist is a visible act a reviewer can question.

2. **Known-foreign denylist.** Rule 1 misses a foreign name that also
   leaked into a test fixture (`AuthShell` did exactly that). The denylist
   catches those regardless of where else they appear, and reads on prose
   as well as identifiers, so `"just like stackpulse/navegando"` is caught
   too.

Neither rule is a proof. A foreign identifier that happens to collide with
a real leerie symbol passes both. This is mechanical enforcement for the
class plus a documented rule (CLAUDE.md), not a guarantee of impossibility.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SHIPPED = ("prompts", "commands")

# Names belonging to projects that are not this one. Seeded from the audit
# of 2026-08-01. Matched case-insensitively as substrings, against the whole
# prompt text — prose included — so a narrative mention is caught too.
FOREIGN_PROJECT_MARKERS = (
    "funeralhome", "funeralworks",
    "navegando", "stackpulse",
    "barnacle", "cruiselines",
    "slm-capture", "capture-slm",
    "authshell", "auth-shell",
    "emptystate", "empty-state",
    "getactivetenantid",
    "dashboard.tsx", "statcard", "sparkline",
)

# Identifiers that legitimately appear in a prompt without existing in this
# repository: external tool names, error codes, illustrative placeholders,
# and schema field paths whose dotted form is never written in the source.
# Adding an entry here is the deliberate act rule 1 exists to force.
GENERIC_PLACEHOLDERS = frozenset({
    # External tooling / OS surface a prompt may legitimately name.
    "EISDIR", "Justfile", "flake.nix", "default.nix", "rerere",
    # Claude Code's own env var, set by the plugin host, not by this repo.
    "CLAUDE_PLUGIN_ROOT",
    # Schema field paths — the leaf names exist; the dotted form does not.
    "confidence.contradictions_reconciled", "confidence.falsifiers_tested",
    "confidence.fit", "template.path", "template.truncated",
    # Invented names for the worked examples in plan_overlap_judge.md. A
    # collision example has to be concrete to teach anything, and these
    # replaced identifiers lifted from a real frontend project (2026-08-01
    # sweep). Keep them obviously synthetic: if a future example needs a
    # new name, invent one and add it here rather than reaching for a
    # symbol you saw in some other codebase.
    # (`WidgetFrame` / `PlaceholderPanel` are deliberately absent: the same
    # sweep put them in test fixtures too, so they satisfy rule 1 on their
    # own and an exemption here would be dead weight.)
    "Callout", "MetricTile", "TrendLine", "SectionPanel",
    "Panel.tsx", "overview.tsx",
})

_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_.]*(?:\(\))?$")
_BACKTICKED = re.compile(r"`([^`\n]{2,60})`")


def _shipped_files() -> list[Path]:
    # git-scoped, not a bare filesystem glob: an untracked scratch .md
    # (e.g. a task-brief file dropped into prompts/ while working on a
    # leerie change) is never shipped product surface, and scanning it
    # here produces a false failure unrelated to what actually ships.
    tracked = set(subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True,
        check=True).stdout.split())
    out = []
    for d in _SHIPPED:
        out.extend(
            p for p in sorted((_REPO / d).glob("*.md"))
            if p.relative_to(_REPO).as_posix() in tracked)
    assert out, "no shipped prompt files found — the guard would be vacuous"
    return out


def _repo_corpus_excluding_prompts() -> str:
    tracked = subprocess.run(
        ["git", "ls-files"], cwd=_REPO, capture_output=True, text=True,
        check=True).stdout.split()
    chunks = []
    for rel in tracked:
        if rel.split("/", 1)[0] in _SHIPPED:
            continue
        # This file lists every allowlisted placeholder and an invented
        # probe name. Counting itself as "the identifier exists in the
        # repo" would make both rules self-satisfying — the allowlist
        # would justify itself and the probe would find its own mention.
        if rel == "tests/" + Path(__file__).name:
            continue
        try:
            chunks.append((_REPO / rel).read_text(errors="replace"))
        except (OSError, UnicodeDecodeError):
            continue
    return "\n".join(chunks)


@pytest.fixture(scope="module")
def corpus() -> str:
    return _repo_corpus_excluding_prompts()


# --------------------------------------------------------------------- #
# Rule 2 — known-foreign names, anywhere in the text
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "path", _shipped_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_no_known_foreign_project_names(path: Path):
    text = path.read_text().lower()
    hits = sorted({m for m in FOREIGN_PROJECT_MARKERS if m in text})
    assert not hits, (
        f"{path.relative_to(_REPO)} names another project's code: {hits}. "
        "Prompts ship to every repo leerie runs against — use this "
        "repository's own symbols, or a generic placeholder."
    )


# --------------------------------------------------------------------- #
# Rule 1 — identifiers must exist here, or be declared generic
# --------------------------------------------------------------------- #

@pytest.mark.parametrize(
    "path", _shipped_files(), ids=lambda p: f"{p.parent.name}/{p.name}")
def test_backticked_identifiers_exist_in_this_repo(path: Path, corpus: str):
    unknown = set()
    for m in _BACKTICKED.finditer(path.read_text()):
        tok = m.group(1).strip()
        if not _IDENTIFIER.fullmatch(tok):
            continue                      # prose, flags, paths with slashes
        bare = tok.rstrip("()")
        if bare in GENERIC_PLACEHOLDERS or bare in corpus:
            continue
        unknown.add(bare)
    assert not unknown, (
        f"{path.relative_to(_REPO)} names identifiers that exist nowhere "
        f"else in this repository: {sorted(unknown)}. Either use a real "
        "symbol from this codebase, or add the name to "
        "GENERIC_PLACEHOLDERS in this test with a reason."
    )


# --------------------------------------------------------------------- #
# Guard the guard
# --------------------------------------------------------------------- #

def test_the_incident_shape_would_be_caught(tmp_path):
    """The exact edit that motivated this test must fail rule 2."""
    probe = tmp_path / "probe.md"
    probe.write_text(
        'Example: `{"old_pattern": "auth.user.funeralHomeId", '
        '"replacement": "getActiveTenantId()"}`\n')
    text = probe.read_text().lower()
    assert [m for m in FOREIGN_PROJECT_MARKERS if m in text]


def test_a_novel_foreign_identifier_is_caught_by_rule_one(corpus: str):
    """Rule 2 only knows today's names. A brand-new foreign symbol must
    still fail, via rule 1 — that is the half that generalises."""
    invented = "AcmeWidgetRegistry"
    assert invented not in corpus
    assert invented not in GENERIC_PLACEHOLDERS


def test_allowlist_entries_are_actually_absent_from_the_repo(corpus: str):
    """An allowlist entry that has since become a real symbol is dead
    weight and should be removed, so the list stays short enough to
    review."""
    stale = sorted(p for p in GENERIC_PLACEHOLDERS if p in corpus)
    assert not stale, (
        f"these GENERIC_PLACEHOLDERS now exist in the repo and no longer "
        f"need an exemption: {stale}")
