import pytest


def _minimal_manifest(**overrides) -> dict:
    base = {
        "version": 1,
        "captured_from": [{"run_id": "r1", "ts": "2026-06-18T12:00:00Z"}],
        "defaults": {"tolerance": 0.15, "n_text": 5, "n_env": 3},
        "judge_prompt_sha": "a" * 64,
        "call_types": {
            "classifier": {
                "tier": "text",
                "cases": ["classifier-001"],
                "baseline_pass_rate": 0.95,
                "n": 5,
                "tolerance": 0.15,
                "baseline_captured_at": "2026-06-18T12:00:00Z",
                "prompt_sha": "b" * 64,
            }
        },
    }
    base.update(overrides)
    return base


def _ct(**overrides) -> dict:
    """Build a manifest whose single call_type entry has overrides applied."""
    m = _minimal_manifest()
    m["call_types"]["classifier"].update(overrides)
    return m


def test_accepts_minimal_text_manifest(leerie):
    leerie._validate_corpus_manifest(_minimal_manifest())


def test_accepts_env_tier_entry(leerie):
    leerie._validate_corpus_manifest(_ct(tier="env", tolerance=0.20, n=3))


def test_accepts_missing_judge_sha(leerie):
    m = _minimal_manifest()
    del m["judge_prompt_sha"]
    leerie._validate_corpus_manifest(m)


def test_rejects_non_dict(leerie):
    with pytest.raises(ValueError, match="JSON object"):
        leerie._validate_corpus_manifest([])


def test_rejects_wrong_version(leerie):
    with pytest.raises(ValueError, match="version"):
        leerie._validate_corpus_manifest(_minimal_manifest(version=2))


def test_rejects_bad_tier(leerie):
    with pytest.raises(ValueError, match="tier"):
        leerie._validate_corpus_manifest(_ct(tier="bogus"))


def test_rejects_empty_cases(leerie):
    with pytest.raises(ValueError, match="cases"):
        leerie._validate_corpus_manifest(_ct(cases=[]))


def test_rejects_pass_rate_out_of_range(leerie):
    with pytest.raises(ValueError, match="baseline_pass_rate"):
        leerie._validate_corpus_manifest(_ct(baseline_pass_rate=1.5))


def test_rejects_n_below_one(leerie):
    with pytest.raises(ValueError, match="n must be"):
        leerie._validate_corpus_manifest(_ct(n=0))


def test_rejects_n_bool(leerie):
    with pytest.raises(ValueError, match="n must be"):
        leerie._validate_corpus_manifest(_ct(n=True))


def test_rejects_pass_rate_bool(leerie):
    with pytest.raises(ValueError, match="baseline_pass_rate"):
        leerie._validate_corpus_manifest(_ct(baseline_pass_rate=True))


def test_rejects_tolerance_bool(leerie):
    with pytest.raises(ValueError, match="tolerance"):
        leerie._validate_corpus_manifest(_ct(tolerance=True))


def test_rejects_tolerance_out_of_range(leerie):
    with pytest.raises(ValueError, match="tolerance"):
        leerie._validate_corpus_manifest(_ct(tolerance=-0.1))
