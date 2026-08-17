"""Unit tests for _command_tokens — the lowercasing/stopword-filtering
primitive underneath check_prescribed_command_coverage (which already has
exhaustive coverage in tests/test_prescribed_cmd_coverage.py, but not of
this tokenizer's own boundary cases).
"""
from __future__ import annotations


def test_lowercases_input(leerie):
    assert leerie._command_tokens("RECON Browser") == frozenset(
        {"recon", "browser"})


def test_splits_on_whitespace(leerie):
    assert leerie._command_tokens("recon   browser\tnow") == frozenset(
        {"recon", "browser", "now"})


def test_drops_every_stopword(leerie):
    for word in leerie._STOPWORDS:
        assert leerie._command_tokens(word) == frozenset()


def test_keeps_non_stopword_tokens_alongside_stopwords(leerie):
    assert leerie._command_tokens("barnacle recon the browser") == frozenset(
        {"barnacle", "recon", "browser"})


def test_returns_frozenset_type(leerie):
    result = leerie._command_tokens("recon browser")
    assert isinstance(result, frozenset)


def test_dedup_by_construction(leerie):
    assert leerie._command_tokens("recon recon browser") == frozenset(
        {"recon", "browser"})


def test_order_independent(leerie):
    assert leerie._command_tokens("recon browser") == leerie._command_tokens(
        "browser recon")


def test_empty_string(leerie):
    assert leerie._command_tokens("") == frozenset()


def test_all_stopwords_input(leerie):
    assert leerie._command_tokens(
        "the a an to and or of in on for with") == frozenset()
