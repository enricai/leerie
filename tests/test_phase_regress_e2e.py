import asyncio
import json
from pathlib import Path

import pytest


class _MiniState:
    """Minimal State-alike: phase_regress only passes it through to the
    (stubbed) judge_capture, so it needs nothing real."""
    def __init__(self, tmp: Path):
        self.run_dir = tmp
        self.run_id = "regress-test"
        self.data: dict = {}

    def bump_workers(self, caps):  # never reached (judge is stubbed)
        pass


def _write_corpus(tmp: Path) -> Path:
    corpus = tmp / "corpus"
    (corpus / "cases" / "classifier").mkdir(parents=True)
    case = {
        "case_id": "classifier-001",
        "call_type": "classifier",
        "captured_from_run": "r1",
        "fixture": None,
        "record": {
            "call_id": "abc12345",
            "call_type": "classifier",
            "model": "opus",
            "system_prompt": "old prompt",
            "user_content": "classify this task",
            "response_content": "{\"categories\": []}",
            "parsed_ok": True,
            "success": True,
        },
    }
    (corpus / "cases" / "classifier" / "classifier-001.json").write_text(
        json.dumps(case))
    manifest = {
        "version": 1,
        "call_types": {
            "classifier": {
                "tier": "text", "cases": ["classifier-001"],
                "baseline_pass_rate": 0.8, "n": 3, "tolerance": 0.15,
                "prompt_sha": "deadbeef",
            }
        },
        "defaults": {},
    }
    (corpus / "manifest.json").write_text(json.dumps(manifest))
    return corpus


def test_phase_regress_text_all_pass_is_ok(leerie, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path)

    async def fake_replay(record, *, override_system_prompt=None, cwd=None):
        # The gate must swap in the CURRENT prompt, not the captured one.
        assert override_system_prompt == leerie.load_prompt("classifier")
        return ({"result": "fresh output", "is_error": False}, {"ok": True})

    async def fake_judge(record, models, efforts, caps, st):
        assert record["response_content"] == "fresh output"
        return {"passed": True, "dimensions": {}, "rationale": "",
                "suggested_fixes": []}

    monkeypatch.setattr(leerie, "replay_capture", fake_replay)
    monkeypatch.setattr(leerie, "judge_capture", fake_judge)

    st = _MiniState(tmp_path)
    out = tmp_path / "regress-out"
    report = asyncio.run(leerie.phase_regress(
        corpus, out, dict(leerie.DEFAULT_CAPS), st, {}, {}, tier="text"))

    assert report["overall"] == "OK"
    assert report["per_call_type"]["classifier"]["passes"] == 3
    assert report["per_call_type"]["classifier"]["total"] == 3
    assert (out / "REPORT.json").exists()


def test_phase_regress_text_all_fail_is_regressed(leerie, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path)

    async def fake_replay(record, *, override_system_prompt=None, cwd=None):
        return ({"result": "bad", "is_error": False}, {})

    async def fake_judge(record, models, efforts, caps, st):
        return {"passed": False, "dimensions": {}, "rationale": "",
                "suggested_fixes": []}

    monkeypatch.setattr(leerie, "replay_capture", fake_replay)
    monkeypatch.setattr(leerie, "judge_capture", fake_judge)

    st = _MiniState(tmp_path)
    report = asyncio.run(leerie.phase_regress(
        corpus, tmp_path / "out", dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="text"))
    assert report["overall"] == "REGRESSED"


def test_phase_regress_warns_on_unchanged_prompt(leerie, tmp_path, monkeypatch):
    corpus = _write_corpus(tmp_path)
    # Pin the manifest prompt_sha to the CURRENT sha → "unchanged" warning.
    manifest = json.loads((corpus / "manifest.json").read_text())
    manifest["call_types"]["classifier"]["prompt_sha"] = \
        leerie._prompt_sha("classifier")
    (corpus / "manifest.json").write_text(json.dumps(manifest))

    async def fake_replay(record, *, override_system_prompt=None, cwd=None):
        return ({"result": "x", "is_error": False}, {})

    async def fake_judge(record, models, efforts, caps, st):
        return {"passed": True, "dimensions": {}, "rationale": "",
                "suggested_fixes": []}

    monkeypatch.setattr(leerie, "replay_capture", fake_replay)
    monkeypatch.setattr(leerie, "judge_capture", fake_judge)

    st = _MiniState(tmp_path)
    report = asyncio.run(leerie.phase_regress(
        corpus, tmp_path / "out", dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="text"))
    assert any("unchanged" in w for w in report["warnings"])


def test_phase_regress_env_tier_uses_replay_in_env(leerie, tmp_path,
                                                    monkeypatch):
    corpus = tmp_path / "corpus"
    (corpus / "cases" / "implementer").mkdir(parents=True)
    case = {
        "case_id": "implementer-010", "call_type": "implementer",
        "captured_from_run": "r1", "fixture": "fixtures/implementer-010/",
        "record": {"call_id": "imp-1", "call_type": "implementer",
                   "model": "sonnet", "system_prompt": "old",
                   "user_content": "do it", "response_content": "{}",
                   "parsed_ok": True, "success": True},
    }
    (corpus / "cases" / "implementer" / "implementer-010.json").write_text(
        json.dumps(case))
    (corpus / "manifest.json").write_text(json.dumps({
        "version": 1, "defaults": {},
        "call_types": {"implementer": {
            "tier": "env", "cases": ["implementer-010"],
            "baseline_pass_rate": 0.8, "n": 3, "tolerance": 0.20,
            "prompt_sha": "x"}}}))

    called = {"env": 0, "text": 0}

    def fake_load_fixture(corpus_dir, c):
        return {"dir": corpus_dir / "fixtures" / "implementer-010",
                "env": {"diff_base": "HEAD"}}

    async def fake_replay_in_env(record, fixture, *, override_system_prompt):
        called["env"] += 1
        return ({"result": "env output", "is_error": False}, {})

    async def fake_replay_capture(record, *, override_system_prompt=None,
                                  cwd=None):
        called["text"] += 1
        return ({"result": "text", "is_error": False}, {})

    async def fake_judge(record, models, efforts, caps, st):
        return {"passed": True, "dimensions": {}, "rationale": "",
                "suggested_fixes": []}

    monkeypatch.setattr(leerie, "_load_fixture", fake_load_fixture)
    monkeypatch.setattr(leerie, "replay_in_env", fake_replay_in_env)
    monkeypatch.setattr(leerie, "replay_capture", fake_replay_capture)
    monkeypatch.setattr(leerie, "judge_capture", fake_judge)

    st = _MiniState(tmp_path)
    report = asyncio.run(leerie.phase_regress(
        corpus, tmp_path / "out", dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="all"))
    assert called["env"] == 3 and called["text"] == 0   # env path, n=3
    assert report["overall"] == "OK"
