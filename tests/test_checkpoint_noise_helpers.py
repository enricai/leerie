"""Tests for the checkpoint-parsing helpers `_split_checkpoint_sections`,
`_normalize_for_noise`, and `_strip_bullet` (leerie.py:12437, 12508, 12523).

These back checkpoint-file structural validation
(`_validate_checkpoint`'s placeholder-token rejection). The one
non-obvious invariant is `_normalize_for_noise`'s documented ordering:
a pure run of `?` must collapse to a single `?` BEFORE trailing
punctuation is stripped, otherwise `???` is eaten down to the empty
string and never matches the bare `?` noise token.
"""
from __future__ import annotations


class TestSplitCheckpointSections:
    def test_buckets_lines_under_most_recent_header_in_order(self, leerie):
        content = (
            "# Checkpoint: feat-001\n"
            "## Frozen success criteria\n"
            "- [x] criterion 1\n"
            "- [ ] criterion 2\n"
            "## Current status\n"
            "half done\n"
        )
        sections = leerie._split_checkpoint_sections(content)
        assert list(sections.keys()) == [
            "## Frozen success criteria",
            "## Current status",
        ]
        assert sections["## Frozen success criteria"] == [
            "- [x] criterion 1",
            "- [ ] criterion 2",
        ]
        assert sections["## Current status"] == ["half done"]

    def test_drops_blank_lines(self, leerie):
        content = "## Next action\n\n  \nreal content\n\n"
        sections = leerie._split_checkpoint_sections(content)
        assert sections["## Next action"] == ["real content"]

    def test_strips_whitespace_from_lines(self, leerie):
        content = "## Open unknowns\n   - padded line   \n"
        sections = leerie._split_checkpoint_sections(content)
        assert sections["## Open unknowns"] == ["- padded line"]

    def test_lines_before_any_header_are_ignored(self, leerie):
        content = "preamble text\nmore preamble\n## Current status\nreal\n"
        sections = leerie._split_checkpoint_sections(content)
        assert sections == {"## Current status": ["real"]}

    def test_header_with_no_content_yields_empty_bucket(self, leerie):
        content = "## Decisions made\n## Open unknowns\n- one thing\n"
        sections = leerie._split_checkpoint_sections(content)
        assert sections["## Decisions made"] == []
        assert sections["## Open unknowns"] == ["- one thing"]

    def test_only_double_hash_space_starts_a_new_header(self, leerie):
        # A line starting with "###" or "##" (no trailing space) must not
        # be treated as a section header — it stays bucketed under the
        # current section instead.
        content = "## Current status\n### a sub-heading, not a new section\n"
        sections = leerie._split_checkpoint_sections(content)
        assert sections == {
            "## Current status": ["### a sub-heading, not a new section"]
        }

    def test_empty_content_yields_empty_dict(self, leerie):
        assert leerie._split_checkpoint_sections("") == {}


class TestStripBullet:
    def test_strips_dash_bullet(self, leerie):
        assert leerie._strip_bullet("- none") == "none"

    def test_strips_star_bullet(self, leerie):
        assert leerie._strip_bullet("* none") == "none"

    def test_strips_plus_bullet(self, leerie):
        assert leerie._strip_bullet("+ none") == "none"

    def test_strips_numbered_list_prefix(self, leerie):
        assert leerie._strip_bullet("1. none") == "none"

    def test_strips_multi_digit_numbered_list_prefix(self, leerie):
        assert leerie._strip_bullet("12. none") == "none"

    def test_leading_whitespace_before_bullet_is_handled(self, leerie):
        assert leerie._strip_bullet("   - none") == "none"

    def test_no_bullet_marker_returns_stripped_line_unchanged(self, leerie):
        assert leerie._strip_bullet("just plain text") == "just plain text"

    def test_bare_dash_with_no_space_is_not_a_bullet(self, leerie):
        # "-none" has no space after the marker, so it isn't stripped.
        assert leerie._strip_bullet("-none") == "-none"

    def test_digit_not_followed_by_dot_space_is_not_a_numbered_list(self, leerie):
        assert leerie._strip_bullet("1x none") == "1x none"

    def test_digit_alone_is_not_a_numbered_list(self, leerie):
        assert leerie._strip_bullet("1") == "1"


class TestNormalizeForNoise:
    def test_bare_none_normalizes_to_none(self, leerie):
        assert leerie._normalize_for_noise("none") == "none"

    def test_none_with_trailing_period_normalizes_to_bare_token(self, leerie):
        assert leerie._normalize_for_noise("None.") == "none"

    def test_tbd_with_trailing_bang_normalizes_to_bare_token(self, leerie):
        assert leerie._normalize_for_noise("TBD!") == "tbd"

    def test_pure_question_marks_collapse_to_single_question_mark(self, leerie):
        # The documented load-bearing invariant: the `?`-run collapse must
        # run BEFORE the trailing-punctuation strip. `?` is itself in the
        # trailing-punctuation-adjacent noise set conceptually, but is not
        # in the strip charset (".!…") — the real risk this guards is that
        # collapsing after stripping would leave nothing to collapse.
        assert leerie._normalize_for_noise("???") == "?"

    def test_single_question_mark_normalizes_to_itself(self, leerie):
        assert leerie._normalize_for_noise("?") == "?"

    def test_ordering_invariant_directly(self, leerie):
        """If the ?-collapse ran AFTER the trailing-punctuation strip,
        '???' would have every '?' peeled off (were '?' in the strip
        charset) or would fail the `set(s) == {"?"}` check post-strip in
        some other rewrite — either way risking landing on the empty
        string rather than the bare '?' token. Pin the actual documented
        outcome directly against the noise-token set."""
        result = leerie._normalize_for_noise("???")
        assert result != ""
        assert result in leerie._NOISE_TOKENS

    def test_strips_bullet_before_noise_comparison(self, leerie):
        assert leerie._normalize_for_noise("- none") == "none"

    def test_lowercases(self, leerie):
        assert leerie._normalize_for_noise("NONE") == "none"

    def test_strips_ellipsis(self, leerie):
        assert leerie._normalize_for_noise("pending…") == "pending"

    def test_strips_multiple_trailing_punctuation_chars(self, leerie):
        assert leerie._normalize_for_noise("todo!.") == "todo"

    def test_none_and_tbd_land_in_noise_tokens(self, leerie):
        assert leerie._normalize_for_noise("None.") in leerie._NOISE_TOKENS
        assert leerie._normalize_for_noise("TBD!") in leerie._NOISE_TOKENS

    def test_non_noise_content_is_untouched_besides_normalization(self, leerie):
        result = leerie._normalize_for_noise("- Added new retry logic.")
        assert result == "added new retry logic"
        assert result not in leerie._NOISE_TOKENS
