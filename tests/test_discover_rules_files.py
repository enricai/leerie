"""Tests for _discover_rules_files() — repo-agnostic discovery of rule
files for the post-work conformance phase (DESIGN §9 *Post-work
conformance*).

The function checks a fixed, capped allowlist of paths and returns
existing ones in declaration order. It never raises and never recurses;
the location of rules files varies across repos so discovery is broad
on the allowlist axis and narrow on the search axis.
"""
from __future__ import annotations


def test_returns_empty_when_no_rules_files_exist(leerie, tmp_path):
    assert leerie._discover_rules_files(tmp_path) == []


def test_finds_claude_md_at_root(leerie, tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# rules\n")
    out = leerie._discover_rules_files(tmp_path)
    assert out == [tmp_path / "CLAUDE.md"]


def test_finds_agents_md_at_root(leerie, tmp_path):
    (tmp_path / "AGENTS.md").write_text("# rules\n")
    out = leerie._discover_rules_files(tmp_path)
    assert out == [tmp_path / "AGENTS.md"]


def test_finds_readme_when_present(leerie, tmp_path):
    (tmp_path / "README.md").write_text("# readme\n")
    out = leerie._discover_rules_files(tmp_path)
    assert out == [tmp_path / "README.md"]


def test_finds_docs_files(leerie, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "CONVENTIONS.md").write_text("c\n")
    (docs / "STYLE.md").write_text("s\n")
    out = leerie._discover_rules_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in out]
    assert "docs/CONVENTIONS.md" in rels
    assert "docs/STYLE.md" in rels


def test_returns_priority_order_not_filesystem_order(leerie, tmp_path):
    """CLAUDE.md is declared before AGENTS.md in the allowlist — so when
    both exist, CLAUDE.md comes first regardless of mtime / creation."""
    (tmp_path / "AGENTS.md").write_text("a\n")
    (tmp_path / "CLAUDE.md").write_text("c\n")
    out = leerie._discover_rules_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in out]
    assert rels.index("CLAUDE.md") < rels.index("AGENTS.md")


def test_capped_at_allowlist_length(leerie, tmp_path):
    """Even if every candidate exists, the output is bounded by the
    allowlist — discovery never recurses or globs."""
    # Touch every candidate (using internal allowlist constant)
    for rel in leerie._RULES_FILE_CANDIDATES:
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x\n")
    # Make a random extra file that is NOT in the allowlist — it should
    # not appear in the output.
    (tmp_path / "RANDOM_RULES.md").write_text("nope\n")
    out = leerie._discover_rules_files(tmp_path)
    assert len(out) == len(leerie._RULES_FILE_CANDIDATES)
    rels = [p.relative_to(tmp_path).as_posix() for p in out]
    assert "RANDOM_RULES.md" not in rels


def test_directory_with_candidate_name_is_skipped(leerie, tmp_path):
    """If a candidate path happens to be a directory (e.g. someone made
    `CLAUDE.md/` a directory by mistake), discovery silently skips it."""
    (tmp_path / "CLAUDE.md").mkdir()
    assert leerie._discover_rules_files(tmp_path) == []


def test_nonexistent_repo_root_returns_empty(leerie, tmp_path):
    """A repo root that doesn't exist returns [] without raising — the
    contract is "never raises."""
    out = leerie._discover_rules_files(tmp_path / "does-not-exist")
    assert out == []


def test_design_system_doc_is_a_candidate(leerie):
    """Guard the load-bearing addition (DESIGN §9): the repo's design-system
    doc must remain in the discovery allowlist so its component/banner
    conventions reach both the conformer and the implementer. A future
    refactor that silently drops it should fail this test."""
    assert "docs/DESIGN-SYSTEM.md" in leerie._RULES_FILE_CANDIDATES
    # spelling variants also covered
    assert "docs/DESIGN_SYSTEM.md" in leerie._RULES_FILE_CANDIDATES
    assert "docs/UI.md" in leerie._RULES_FILE_CANDIDATES


def test_finds_design_system_doc(leerie, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "DESIGN-SYSTEM.md").write_text("# banners use bg-destructive\n")
    out = leerie._discover_rules_files(tmp_path)
    rels = [p.relative_to(tmp_path).as_posix() for p in out]
    assert "docs/DESIGN-SYSTEM.md" in rels


def test_format_rules_paths_empty_is_none_sentinel(leerie, tmp_path):
    """The shared formatter renders `(none)` for an empty list — the same
    sentinel the conformer's RULES_FILES line uses."""
    assert leerie._format_rules_paths([], tmp_path) == "(none)"


def test_format_rules_paths_renders_relative(leerie, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    p = docs / "DESIGN-SYSTEM.md"
    p.write_text("x\n")
    out = leerie._format_rules_paths([p], tmp_path)
    assert out == "docs/DESIGN-SYSTEM.md"


def test_convention_docs_section_none_when_no_docs(leerie, tmp_path):
    """No discoverable docs → no CONVENTION_DOCS block injected (so the
    implementer prompt gets no empty section)."""
    assert leerie._format_convention_docs_section(tmp_path) is None


def test_discover_rules_files_skips_candidate_that_raises_oserror(
        leerie, tmp_path, monkeypatch):
    """A candidate whose `is_file()` raises OSError (e.g. a permission
    error walking a symlinked path) is skipped rather than propagating —
    discovery must never raise."""
    (tmp_path / "README.md").write_text("r\n")
    real_is_file = leerie.Path.is_file

    def flaky_is_file(self):
        if self.name == "CLAUDE.md":
            raise OSError("simulated stat failure")
        return real_is_file(self)

    monkeypatch.setattr(leerie.Path, "is_file", flaky_is_file)
    out = leerie._discover_rules_files(tmp_path)
    assert out == [tmp_path / "README.md"]


def test_convention_docs_section_lists_discovered_paths(leerie, tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "DESIGN-SYSTEM.md").write_text("x\n")
    (tmp_path / "CLAUDE.md").write_text("y\n")
    section = leerie._format_convention_docs_section(tmp_path)
    assert section is not None
    assert section.startswith("CONVENTION_DOCS")
    # both discovered docs named, in allowlist priority order (CLAUDE.md first)
    assert "CLAUDE.md" in section
    assert "docs/DESIGN-SYSTEM.md" in section
    assert section.index("CLAUDE.md") < section.index("docs/DESIGN-SYSTEM.md")
