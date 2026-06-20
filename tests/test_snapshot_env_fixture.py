import json
import subprocess
from pathlib import Path


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _make_repo(p: Path) -> str:
    p.mkdir(parents=True)
    _git("init", "-q", cwd=p)
    _git("config", "user.email", "t@t", cwd=p)
    _git("config", "user.name", "t", cwd=p)
    (p / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "-A", cwd=p)
    _git("commit", "-qm", "init", cwd=p)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=p,
                          capture_output=True, text=True).stdout.strip()


def test_snapshot_produces_clonable_bundle_and_env(leerie, tmp_path):
    repo = tmp_path / "repo"
    base = _make_repo(repo)
    leerie_root = tmp_path / "state"
    run_dir = leerie_root / "runs" / "r1"
    (run_dir / "subtasks").mkdir(parents=True)
    (run_dir / "subtasks" / "task-1.json").write_text('{"id": "task-1"}')
    (run_dir / "working-branch").write_text("main")
    # The worktree the worker ran in (its base is `base`).
    (run_dir / "worktrees" / "task-1").mkdir(parents=True)

    corpus = tmp_path / "corpus"
    record = {
        "call_id": "imp-1", "call_type": "implementer", "model": "sonnet",
        "system_prompt": "p",
        "user_content": f"LEERIE_DIR={run_dir} ... implement task-1",
        "response_content": "{}", "parsed_ok": True, "success": True,
    }
    # Point the snapshotter at the source repo via env/state as Step 1 dictates;
    # here we exercise the bundle + env.json contract.
    ptr = leerie._snapshot_env_fixture(corpus, "implementer-010", record,
                                       leerie_root, "r1",
                                       src_repo=repo, base_sha=base)
    assert ptr == "fixtures/implementer-010/"
    fdir = corpus / "fixtures" / "implementer-010"
    assert (fdir / "repo.bundle").exists()
    assert (fdir / "leerie_dir" / "subtasks" / "task-1.json").exists()
    env = json.loads((fdir / "env.json").read_text())
    assert "diff_base" in env and "leerie_dir_abs" in env and "autonomous" in env
    # REGR-06: replay_in_env never reads build/lint/test commands, so the
    # Tier-2 snapshot must not carry these dead fields (verdict is
    # judge-on-output, not build/test results).
    assert "build_cmd" not in env and "lint_cmd" not in env \
        and "test_cmd" not in env
    # The bundle clones cleanly and contains the base commit.
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(fdir / "repo.bundle"),
                    str(clone)], check=True, capture_output=True)
    assert (clone / "calc.py").exists()
