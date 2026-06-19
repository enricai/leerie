import asyncio
import json
from pathlib import Path

import pytest


class _MiniState:
    def __init__(self, tmp: Path):
        self.run_dir = tmp
        self.run_id = "r1"
        self.data: dict = {}

    def bump_workers(self, caps):
        pass


def _seed_run(leerie_root: Path, run_id: str) -> None:
    run_dir = leerie_root / "runs" / run_id
    run_dir.mkdir(parents=True)
    rows = [
        {"call_id": "id-good-1", "call_type": "classifier", "model": "opus",
         "system_prompt": "p", "user_content": "u1", "response_content": "r1",
         "parsed_ok": True, "success": True},
        {"call_id": "id-bad-1", "call_type": "classifier", "model": "opus",
         "system_prompt": "p", "user_content": "u2", "response_content": "r2",
         "parsed_ok": False, "success": False},   # filtered out
        {"call_id": "id-good-2", "call_type": "planner", "model": "opus",
         "system_prompt": "p", "user_content": "u3", "response_content": "r3",
         "parsed_ok": True, "success": True},
    ]
    (run_dir / "calls.ndjson").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n")


def test_corpus_capture_promotes_good_records_and_pins_baseline(
        leerie, tmp_path, monkeypatch):
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"

    async def fake_phase_regress(corpus_dir, out_dir, caps, st, models,
                                 efforts, tier="all", call_types=None,
                                 tolerance=None):
        # Pretend every selected call_type measured 0.9.
        manifest = leerie._load_corpus_manifest(corpus_dir)
        per = {ct: {"current": 0.9, "baseline": 0.9, "tolerance": 0.15,
                    "passes": 0, "total": 0, "verdict": "OK"}
               for ct in manifest["call_types"]}
        return {"overall": "OK", "per_call_type": per, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)

    st = _MiniState(leerie_root / "runs" / "r1")
    report = asyncio.run(leerie.corpus_capture(
        "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="text"))

    # Only success && parsed_ok records were promoted.
    assert (corpus / "cases" / "classifier" / "classifier-001.json").exists()
    assert (corpus / "cases" / "planner" / "planner-001.json").exists()
    assert not (corpus / "cases" / "classifier" / "classifier-002.json").exists()

    manifest = json.loads((corpus / "manifest.json").read_text())
    leerie._validate_corpus_manifest(manifest)
    assert manifest["call_types"]["classifier"]["baseline_pass_rate"] == 0.9
    assert manifest["call_types"]["classifier"]["tier"] == "text"
    assert manifest["call_types"]["classifier"]["n"] == leerie.REGRESS_N_TEXT_DEFAULT
    assert "prompt_sha" in manifest["call_types"]["classifier"]
    assert manifest["judge_prompt_sha"] == leerie._prompt_sha("judge")


def test_corpus_capture_call_type_filter(leerie, tmp_path, monkeypatch):
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"

    async def fake_phase_regress(*a, **k):
        manifest = leerie._load_corpus_manifest(a[0])
        return {"overall": "OK", "warnings": [],
                "per_call_type": {ct: {"current": 1.0} for ct in
                                  manifest["call_types"]}}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)
    st = _MiniState(leerie_root / "runs" / "r1")
    asyncio.run(leerie.corpus_capture(
        "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
        call_types=["planner"], tier="text"))
    assert (corpus / "cases" / "planner" / "planner-001.json").exists()
    assert not (corpus / "cases" / "classifier").exists()


def test_corpus_capture_rejects_unknown_tier(leerie, tmp_path, monkeypatch):
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    st = _MiniState(leerie_root / "runs" / "r1")
    with pytest.raises(SystemExit):
        asyncio.run(leerie.corpus_capture(
            "r1", tmp_path / "corpus", leerie_root, dict(leerie.DEFAULT_CAPS),
            st, {}, {}, tier="bogus"))


def test_corpus_capture_env_tier_dies_before_increment_b(leerie, tmp_path,
                                                         monkeypatch):
    # _ENV_CAPTURE_READY is False until Increment B (Task B2) flips it.
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    st = _MiniState(leerie_root / "runs" / "r1")
    if leerie._ENV_CAPTURE_READY:
        import pytest as _pt
        _pt.skip("env capture is ready; guard no longer applies")
    with pytest.raises(SystemExit):
        asyncio.run(leerie.corpus_capture(
            "r1", tmp_path / "corpus", leerie_root, dict(leerie.DEFAULT_CAPS),
            st, {}, {}, tier="env"))
