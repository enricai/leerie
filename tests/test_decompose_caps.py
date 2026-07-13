"""Pin the four new DEFAULT_CAPS values introduced by the F1 P6+P1 work.

Guards against accidental revert of the empirically-measured values,
in particular decompose_fit_threshold=0.70 — the 0.95 value it replaced
was measured to over-split 100% of well-fit subtasks (see F1-build-measure.md).
Mirrors the test_default_cap_is_eight pattern from test_resolve_confidence_rounds.py.
"""
from __future__ import annotations


def test_repo_map_tokens(leerie):
    assert leerie.DEFAULT_CAPS["repo_map_tokens"] == 1000


def test_decompose_max_depth(leerie):
    assert leerie.DEFAULT_CAPS["decompose_max_depth"] == 5


def test_decompose_fit_threshold(leerie):
    # 0.70, NOT 0.95. Measured on n=24 telemetry-labeled subtasks:
    # 0.95 over-splits 100% of well-fit subtasks (scores cluster at
    # 0.82–0.93); 0.70 achieves 88% accuracy with 0.57 separation
    # between oversized (mean 0.26) and well-fit (mean 0.84).
    # See F1-build-measure.md for the measured distribution.
    assert leerie.DEFAULT_CAPS["decompose_fit_threshold"] == 0.70


def test_decompose_noprogress_rounds(leerie):
    assert leerie.DEFAULT_CAPS["decompose_noprogress_rounds"] == 2
