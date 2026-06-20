import asyncio
import json
import subprocess
from pathlib import Path

import pytest


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _make_repo(p: Path) -> str:
    """Real-git fixture (init + commit) returning the base SHA — mirrors
    tests/test_snapshot_env_fixture.py:_make_repo."""
    p.mkdir(parents=True)
    _git("init", "-q", cwd=p)
    _git("config", "user.email", "t@t", cwd=p)
    _git("config", "user.name", "t", cwd=p)
    (p / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "-A", cwd=p)
    _git("commit", "-qm", "init", cwd=p)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=p,
                          capture_output=True, text=True).stdout.strip()


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
        # Acting-worker record — promoted to an env-tier case when tier="env".
        {"call_id": "id-good-3", "call_type": "implementer", "model": "sonnet",
         "system_prompt": "p", "user_content": "u4", "response_content": "{}",
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


def test_corpus_capture_env_tier_snapshots_fixture_and_pins_env_defaults(
        leerie, tmp_path, monkeypatch):
    """Live env-tier coverage (REGR-08): an acting-worker record drives
    _snapshot_env_fixture, a fixture is snapshotted, and the manifest entry
    is env-tier with n == REGRESS_N_ENV_DEFAULT and tolerance ==
    REGRESS_ENV_TOLERANCE_DEFAULT."""
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"

    # Real-git source repo for the env snapshot; the run's working-branch is
    # `main` so _snapshot_env_fixture resolves the base SHA from it.
    repo = tmp_path / "repo"
    base = _make_repo(repo)
    run_dir = leerie_root / "runs" / "r1"
    branch = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"],
                            cwd=repo, capture_output=True,
                            text=True).stdout.strip()
    (run_dir / "working-branch").write_text(branch)
    # _snapshot_env_fixture resolves src_repo from USER_REPO when not given.
    monkeypatch.setenv("USER_REPO", str(repo))

    async def fake_phase_regress(corpus_dir, out_dir, caps, st, models,
                                 efforts, tier="all", call_types=None,
                                 tolerance=None):
        # Report a healthy current for every freshly-promoted call_type.
        manifest = leerie._load_corpus_manifest(corpus_dir)
        per = {ct: {"current": 0.9, "baseline": 0.9, "tolerance": 0.20,
                    "passes": 0, "total": 0, "verdict": "OK"}
               for ct in manifest["call_types"]}
        return {"overall": "OK", "per_call_type": per, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)

    st = _MiniState(run_dir)
    asyncio.run(leerie.corpus_capture(
        "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
        call_types=["implementer"], tier="env"))

    # A fixture was snapshotted for the implementer case.
    case_path = corpus / "cases" / "implementer" / "implementer-001.json"
    assert case_path.exists()
    case = json.loads(case_path.read_text())
    fixture_ptr = case["fixture"]
    assert fixture_ptr == "fixtures/implementer-001/"
    fdir = corpus / fixture_ptr
    assert (fdir / "repo.bundle").exists()
    assert (fdir / "env.json").exists()

    # The manifest entry is env-tier with the env n/tolerance defaults.
    manifest = leerie._load_corpus_manifest(corpus)
    leerie._validate_corpus_manifest(manifest)
    entry = manifest["call_types"]["implementer"]
    assert entry["tier"] == "env"
    assert entry["n"] == leerie.REGRESS_N_ENV_DEFAULT
    assert entry["tolerance"] == leerie.REGRESS_ENV_TOLERANCE_DEFAULT


# --- REGR-03: degenerate-baseline warning at pin time ----------------------

def test_degenerate_baseline_warns(leerie, tmp_path, monkeypatch):
    """A baseline pinned at/below its tolerance makes compare_to_baseline
    un-failable for that call_type; corpus_capture must warn loudly naming
    the call_type. A baseline well above tolerance must stay silent."""
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"

    # classifier measures 0.0 (<= default tolerance 0.15) → degenerate;
    # planner measures 1.0 (> tolerance) → healthy.
    async def fake_phase_regress(corpus_dir, out_dir, caps, st, models,
                                 efforts, tier="all", call_types=None,
                                 tolerance=None):
        manifest = leerie._load_corpus_manifest(corpus_dir)
        per = {}
        for ct in manifest["call_types"]:
            cur = 0.0 if ct == "classifier" else 1.0
            per[ct] = {"current": cur, "baseline": cur, "tolerance": 0.15,
                       "passes": 0, "total": 0, "verdict": "OK"}
        return {"overall": "OK", "per_call_type": per, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)

    captured: list[str] = []
    monkeypatch.setattr(leerie, "log", lambda msg: captured.append(msg))

    st = _MiniState(leerie_root / "runs" / "r1")
    asyncio.run(leerie.corpus_capture(
        "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="text"))

    # The degenerate (classifier) baseline triggers a warning naming the
    # call_type and the un-failable / tolerance condition.
    degenerate = [m for m in captured
                  if "classifier" in m
                  and ("tolerance" in m or "un-failable" in m)]
    assert degenerate, (
        f"expected a degenerate-baseline warning for classifier; "
        f"got log lines: {captured}")

    # The healthy (planner) baseline produces no such warning.
    healthy = [m for m in captured
               if "planner" in m
               and ("tolerance" in m or "un-failable" in m)]
    assert not healthy, (
        f"healthy planner baseline must not warn; got: {healthy}")


# --- REGR-04: crash-safe manifest writes -----------------------------------

def test_capture_crash_leaves_no_degenerate_manifest(leerie, tmp_path,
                                                     monkeypatch):
    """A phase_regress crash mid-capture must not leave the host
    manifest.json with a freshly-captured call_type pinned at 0.0
    (the provisional value). The host manifest must be unchanged from
    before capture (here: absent, since there was no prior manifest)."""
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"
    manifest_path = corpus / "manifest.json"
    assert not manifest_path.exists()

    async def boom_phase_regress(*a, **k):
        raise RuntimeError("simulated mid-capture crash")

    monkeypatch.setattr(leerie, "phase_regress", boom_phase_regress)

    st = _MiniState(leerie_root / "runs" / "r1")
    with pytest.raises(RuntimeError):
        asyncio.run(leerie.corpus_capture(
            "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
            tier="text"))

    # The host manifest is either absent or carries no newly-captured
    # call_type pinned at the degenerate provisional 0.0.
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text())
        for ct, cfg in manifest.get("call_types", {}).items():
            assert cfg.get("baseline_pass_rate") != 0.0, (
                f"{ct} left at degenerate baseline_pass_rate 0.0 after crash")


def test_capture_crash_restores_prior_manifest(leerie, tmp_path, monkeypatch):
    """When a prior healthy manifest exists, a phase_regress crash during a
    fresh capture must restore the host manifest to its pre-capture bytes
    (the prior baseline must survive, no new 0.0 entry leaks)."""
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"
    corpus.mkdir(parents=True)
    manifest_path = corpus / "manifest.json"

    # Seed a prior healthy manifest with an unrelated call_type.
    prior = {
        "version": leerie.CORPUS_MANIFEST_VERSION,
        "call_types": {
            "reconciler": {
                "tier": "text", "cases": ["reconciler-001"],
                "baseline_pass_rate": 0.9,
                "n": leerie.REGRESS_N_TEXT_DEFAULT,
                "tolerance": leerie.REGRESS_TOLERANCE_DEFAULT,
            }
        },
        "defaults": {},
    }
    leerie._validate_corpus_manifest(prior)
    prior_bytes = json.dumps(prior, indent=2)
    manifest_path.write_text(prior_bytes)

    async def boom_phase_regress(*a, **k):
        raise RuntimeError("simulated mid-capture crash")

    monkeypatch.setattr(leerie, "phase_regress", boom_phase_regress)

    st = _MiniState(leerie_root / "runs" / "r1")
    with pytest.raises(RuntimeError):
        asyncio.run(leerie.corpus_capture(
            "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
            tier="text"))

    # The host manifest is byte-for-byte the prior one; no new entries.
    assert manifest_path.read_text() == prior_bytes
    restored = json.loads(manifest_path.read_text())
    assert set(restored["call_types"]) == {"reconciler"}


def test_manifest_write_is_atomic(leerie, tmp_path, monkeypatch):
    """After a normal capture, no leftover manifest.json.tmp remains."""
    leerie_root = tmp_path / "state"
    _seed_run(leerie_root, "r1")
    corpus = tmp_path / "corpus"

    async def fake_phase_regress(corpus_dir, out_dir, caps, st, models,
                                 efforts, tier="all", call_types=None,
                                 tolerance=None):
        manifest = leerie._load_corpus_manifest(corpus_dir)
        per = {ct: {"current": 0.9} for ct in manifest["call_types"]}
        return {"overall": "OK", "per_call_type": per, "warnings": []}

    monkeypatch.setattr(leerie, "phase_regress", fake_phase_regress)
    st = _MiniState(leerie_root / "runs" / "r1")
    asyncio.run(leerie.corpus_capture(
        "r1", corpus, leerie_root, dict(leerie.DEFAULT_CAPS), st, {}, {},
        tier="text"))

    assert (corpus / "manifest.json").exists()
    assert not (corpus / "manifest.json.tmp").exists()
