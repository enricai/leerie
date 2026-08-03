"""`_extract_readme_sections` bounds the README fixture's SIZE, nothing more.

Which parts of a README describe installation is a judgment about prose, so
the provision worker makes it. This function only decides how much text the
worker gets to see.

It used to decide for the worker: each header was keyword-matched against
`_README_SECTION_RE` (`install|setup|usage|build|getting started|…`) and only
matching sections were forwarded, with a code-fence scanner and a top-6KB
slice as fallbacks when nothing matched. That is regex over prose to classify
it — CLAUDE.md *Language-to-JSON*. It was also a standing maintenance tax:
any project naming its section something the keyword list did not anticipate
silently dropped to a cruder fallback.

The tests below therefore assert size and shape, never selection. The old
suite's keyword-coverage sweep (`install`/`setup`/`usage`/… × ATX/setext/
asciidoc/emoji/bullet header styles) is deleted with the mechanism it covered;
`TestKeywordSelectionAbsent` pins that it cannot return.
"""
from __future__ import annotations

import pytest


def _long(n_lines: int = 3000) -> str:
    return "# Project\n\nPitch.\n\n" + ("filler line\n" * n_lines)


# --------------------------------------------------------------------- #
# Size bounding
# --------------------------------------------------------------------- #

def test_short_readme_is_returned_verbatim(leerie):
    """Under budget, the worker sees the whole file — no selection, no
    reordering, no truncation."""
    text = ("# Project\n\nPitch.\n\n## Install\n\n```\npip install x\n```\n"
            "\n## Licence\n\nMIT\n")
    assert leerie._extract_readme_sections(text) == text


def test_over_budget_readme_is_truncated_to_budget(leerie):
    out = leerie._extract_readme_sections(_long())
    assert 0 < len(out) <= leerie._README_EXTRACT_BUDGET


def test_truncation_is_a_prefix_of_the_original(leerie):
    """Bounding only: whatever survives is the head of the document, in
    document order. Nothing is selected out of the middle."""
    text = _long()
    out = leerie._extract_readme_sections(text)
    assert text.startswith(out)


def test_headerless_document_still_yields_content(leerie):
    """A README with no markdown headers in its first 8KB must not be
    emptied by the section-boundary backoff."""
    out = leerie._extract_readme_sections("x" * 20000)
    assert len(out) > leerie._README_EXTRACT_BUDGET * 0.75


def test_boundary_backoff_never_discards_most_of_the_budget(leerie):
    """The cut backs up to a section boundary so the worker never gets a
    header with its body sheared off — but a single early header must not
    cost the rest of the slice."""
    text = "# Project\n\n" + ("filler line\n" * 3000)
    out = leerie._extract_readme_sections(text)
    assert len(out) >= leerie._README_EXTRACT_BUDGET * 0.75


# --------------------------------------------------------------------- #
# Degenerate inputs
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("text", ["", "\n", "   ", "#", "# Only a header\n"])
def test_degenerate_inputs_do_not_crash(leerie, text):
    out = leerie._extract_readme_sections(text)
    assert isinstance(out, str)
    assert len(out) <= max(len(text), leerie._README_EXTRACT_BUDGET)


def test_empty_text_returns_empty(leerie):
    assert leerie._extract_readme_sections("") == ""


# --------------------------------------------------------------------- #
# The content the old keyword filter used to drop must now survive
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("header", [
    "## Cómo empezar",          # not English — the keyword list never matched
    "## 使い方",                  # nor this
    "## Bootstrapping the thing",
    "## Local development environment",
])
def test_unconventionally_named_sections_are_kept(leerie, header):
    """The whole point of removing the keyword filter: a project that names
    its install section something the list did not anticipate is no longer
    penalised for it."""
    text = f"# Project\n\nPitch.\n\n{header}\n\n```\npip install x\n```\n"
    out = leerie._extract_readme_sections(text)
    assert header in out
    assert "pip install x" in out


def test_sections_the_old_filter_called_irrelevant_are_kept(leerie):
    """Licence/contributing/badges used not to match and were dropped. Under
    budget they now reach the worker, which decides what matters."""
    text = ("# Project\n\nPitch.\n\n## Licence\n\nMIT\n\n"
            "## Code of Conduct\n\nBe nice.\n")
    out = leerie._extract_readme_sections(text)
    assert "Licence" in out and "Code of Conduct" in out


# --------------------------------------------------------------------- #
# Absence guard
# --------------------------------------------------------------------- #

class TestKeywordSelectionAbsent:
    """Mirrors `TestRegexPathAbsent` in tests/test_capture_deps.py — the
    precedent CLAUDE.md names for a migration off hand-parsing."""

    @pytest.mark.parametrize("sym", [
        "_README_SECTION_RE",
        "_HEADER_DECOR_RE",
        "_is_install_section",
        "_slice_code_fences_with_install_hints",
        "_INSTALL_CMD_HINT_RE",
        "_README_FALLBACK_BUDGET",
    ])
    def test_deleted_symbols_stay_deleted(self, leerie, sym):
        assert not hasattr(leerie, sym), (
            f"{sym} is back — the keyword-selection path can silently "
            "resume pre-filtering the README for the provision worker")

    def test_extractor_classifies_no_header(self, leerie):
        """Banned by shape, not just by name: the function must not test a
        header's text against anything."""
        import inspect
        src = inspect.getsource(leerie._extract_readme_sections)
        body = src[src.index('"""', src.index('"""') + 3) + 3:]
        for banned in ("install", "setup", "usage", "getting started",
                       "search(", "match(", "fullmatch("):
            assert banned not in body.lower(), (
                f"_extract_readme_sections looks at {banned!r} — deciding "
                "which sections matter is the worker's job")

    def test_provision_prompt_owns_the_selection(self):
        """The mechanism only moves if the worker is told to do the job."""
        from pathlib import Path
        text = (Path(__file__).resolve().parent.parent
                / "prompts" / "provision.md").read_text().lower()
        assert "readme" in text


# --------------------------------------------------------------------- #
# Budget sizing — measured against this repo's own README
#
# The 8KB budget the migration originally shipped with was too small, and
# the failure was silent. Measured against this repo's README (75KB, 89
# sections): `## Install` survived by a ~600-byte margin, but
# `### Manual container-runtime setup` (byte 8701) and the
# `brew install colima` prerequisite (9009) were cut — content the keyword
# filter this replaced would have reached at any depth. Removing a real
# CLAUDE.md violation should not cost a real capability.
#
# 16KB was tried and rejected: it pushes the fixture set past
# `_FIXTURE_TOTAL_BUDGET` and silently drops CONTRIBUTING, a higher-signal
# install source than a long README's tail.
# --------------------------------------------------------------------- #

def _repo_readme() -> str:
    from pathlib import Path
    return (Path(__file__).resolve().parent.parent / "README.md").read_text()


class TestBudgetSizing:
    def test_deep_install_content_survives(self, leerie):
        """The regression that motivated the budget change, pinned against
        the real document rather than a synthetic one."""
        text = _repo_readme()
        if len(text) < leerie._README_EXTRACT_BUDGET:
            pytest.skip("this repo's README no longer exceeds the budget")
        out = leerie._extract_readme_sections(text)
        for marker in ("### Manual container-runtime setup",
                       "brew install colima"):
            at = text.find(marker)
            if at < 0:
                continue
            assert at < len(out), (
                f"{marker!r} sits at byte {at} and falls outside the "
                f"{len(out)}-byte slice — the provision worker cannot see "
                "an install prerequisite it needs")

    def test_whole_fixture_set_still_fits_beside_the_readme(self, leerie):
        """Budget step 3: a bigger README must not silently evict the
        other fixtures. CONTRIBUTING is the one that goes first, and it is
        a better install source than a long README's tail."""
        from pathlib import Path
        fixtures = leerie._gather_provision_fixtures(
            Path(__file__).resolve().parent.parent)
        assert fixtures["total_bytes"] <= leerie._FIXTURE_TOTAL_BUDGET
        assert not fixtures["hit_ceiling"], (
            "the README budget now crowds the fixture ceiling; lower the "
            "README budget rather than raising the ceiling — manifests and "
            "CONTRIBUTING outrank a README tail")
        assert fixtures["contributing"], (
            "CONTRIBUTING was evicted by the README budget")

    def test_budget_is_not_silently_lowered(self, leerie):
        """A future edit that shrinks this back below ~9KB reintroduces the
        measured regression."""
        assert leerie._README_EXTRACT_BUDGET >= 10240


class TestTruncationIsReported:
    """A slice the worker cannot tell is a slice is worse than a small
    one: it looks like the whole README."""

    def test_unseen_counts_are_reported(self, leerie):
        from pathlib import Path
        fixtures = leerie._gather_provision_fixtures(
            Path(__file__).resolve().parent.parent)
        text = _repo_readme()
        if len(text) <= len(fixtures["readme"]):
            pytest.skip("README fits; nothing to report")
        assert fixtures["readme_bytes_unseen"] > 0
        assert fixtures["readme_sections_unseen"] > 0

    def test_unseen_sections_count_is_accurate_past_the_raw_read_cap(
            self, leerie, tmp_path):
        """`readme_sections_unseen` must count against the WHOLE file, not
        the `_README_EXTRACT_BUDGET * 4`-capped raw read `raw` uses for the
        extract — otherwise any section past that cap (48KB at the current
        12KB budget) is invisible to the count entirely, silently
        understating how much content the worker cannot see. Regression:
        this repo's own 75KB/89-section README previously reported 47
        unseen (81-header-in-the-48KB-cap minus 34-in-the-extract) instead
        of the true 55 (89 total minus 34 in the extract)."""
        budget = leerie._README_EXTRACT_BUDGET
        cap = budget * 4
        # Build a README with a header every ~600 bytes so the true
        # section count comfortably exceeds what a `cap`-truncated read
        # would see, and well past `cap` in total size.
        sections = []
        n = (cap // 600) + 40
        for i in range(n):
            sections.append(f"### Section {i}\n\n" + ("x" * 550) + "\n\n")
        text = "# Big project\n\n" + "".join(sections)
        assert len(text) > cap, "fixture must exceed the raw-read cap"

        (tmp_path / "README.md").write_text(text)
        fixtures = leerie._gather_provision_fixtures(tmp_path)

        true_total = len(leerie._split_readme_headers(text))
        extract_sections = len(
            leerie._split_readme_headers(fixtures["readme"]))
        true_unseen = true_total - extract_sections
        assert fixtures["readme_sections_unseen"] == true_unseen, (
            f"reported {fixtures['readme_sections_unseen']} unseen "
            f"sections, but the true count is {true_unseen} "
            f"({true_total} total - {extract_sections} in the extract)")

    def test_short_readme_reports_nothing_unseen(self, leerie, tmp_path):
        (tmp_path / "README.md").write_text("# P\n\n## Install\n\npip x\n")
        fixtures = leerie._gather_provision_fixtures(tmp_path)
        assert fixtures["readme_bytes_unseen"] == 0
        assert fixtures["readme_sections_unseen"] == 0

    def test_prompt_tells_the_worker_what_it_cannot_see(self, leerie):
        """Anti-vacuity: the counts are inert unless they reach the
        worker."""
        from pathlib import Path
        fixtures = leerie._gather_provision_fixtures(
            Path(__file__).resolve().parent.parent)
        prompt = leerie._format_provision_user_prompt(fixtures, "task")
        assert "UNFILTERED" in prompt, (
            "the worker must be told nothing pre-selected the slice")
        if fixtures["readme_bytes_unseen"]:
            assert "TRUNCATED" in prompt
            assert str(fixtures["readme_bytes_unseen"]) in prompt
