"""Tests for _update_baseline().

`leerie --regress --update-baseline` re-pins the corpus manifest after an
INTENTIONAL prompt change (DESIGN §14): for every call_type present in BOTH
the fresh report and the manifest it overwrites baseline_pass_rate, prompt_sha,
and baseline_captured_at, and refreshes judge_prompt_sha. Round-trips through
_load_corpus_manifest / _atomic_write_manifest, so a pass here also proves the
written manifest re-validates.
"""
from __future__ import annotations

import json


def _seed_manifest(leerie, corpus_dir, entries):
    manifest = {
        "version": leerie.CORPUS_MANIFEST_VERSION,
        "defaults": {},
        "call_types": entries,
    }
    leerie._validate_corpus_manifest(manifest)  # guard the fixture itself
    (corpus_dir / "manifest.json").write_text(json.dumps(manifest))


def test_update_baseline_repins_only_reported_existing_call_types(leerie, tmp_path):
    corpus = tmp_path
    _seed_manifest(leerie, corpus, {
        "classifier": {"tier": "text", "cases": ["classifier-001"],
                       "baseline_pass_rate": 0.50, "n": 5, "tolerance": 0.15},
        "planner": {"tier": "text", "cases": ["planner-001"],
                    "baseline_pass_rate": 0.60, "n": 5, "tolerance": 0.15},
    })

    report = {"per_call_type": {
        "classifier": {"current": 0.85},
        # in the report but NOT in the manifest → must be ignored, not added.
        "reconciler": {"current": 0.90},
    }}
    leerie._update_baseline(corpus, report)

    out = leerie._load_corpus_manifest(corpus)
    cls = out["call_types"]["classifier"]
    # classifier: re-pinned from the report, sha + timestamp refreshed.
    assert cls["baseline_pass_rate"] == 0.85
    assert cls["prompt_sha"] == leerie._prompt_sha("classifier")
    assert cls["baseline_captured_at"]  # non-empty ISO timestamp
    # planner: present in manifest but absent from report → left untouched.
    assert out["call_types"]["planner"]["baseline_pass_rate"] == 0.60
    assert "prompt_sha" not in out["call_types"]["planner"]
    # reconciler: in the report but not the manifest → not introduced.
    assert "reconciler" not in out["call_types"]
    # judge sha is (re)pinned at the top level on every update.
    assert out["judge_prompt_sha"] == leerie._prompt_sha("judge")


def test_update_baseline_warns_on_degenerate_repin(leerie, tmp_path, monkeypatch):
    # Re-pinning a baseline at/below its tolerance leaves the gate un-failable
    # (current < baseline - tolerance can never hold), so it must warn loudly.
    corpus = tmp_path
    _seed_manifest(leerie, corpus, {
        "classifier": {"tier": "text", "cases": ["classifier-001"],
                       "baseline_pass_rate": 0.90, "n": 5, "tolerance": 0.15},
    })
    warnings: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda m: warnings.append(m))

    leerie._update_baseline(corpus, {"per_call_type": {"classifier": {"current": 0.10}}})

    joined = "\n".join(warnings)
    assert "classifier" in joined and "un-failable" in joined
    assert leerie._load_corpus_manifest(corpus)["call_types"]["classifier"][
        "baseline_pass_rate"] == 0.10
