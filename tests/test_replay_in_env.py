import asyncio
import json
import subprocess
from pathlib import Path


def _git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True,
                   capture_output=True, text=True)


def _make_fixture(tmp: Path) -> tuple[Path, dict, str]:
    repo = tmp / "repo"
    repo.mkdir(parents=True)
    _git("init", "-q", cwd=repo)
    _git("config", "user.email", "t@t", cwd=repo)
    _git("config", "user.name", "t", cwd=repo)
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    _git("add", "-A", cwd=repo)
    _git("commit", "-qm", "init", cwd=repo)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo,
                          capture_output=True, text=True).stdout.strip()
    fdir = tmp / "corpus" / "fixtures" / "implementer-010"
    fdir.mkdir(parents=True)
    subprocess.run(["git", "-C", str(repo), "bundle", "create",
                    str(fdir / "repo.bundle"), "HEAD"], check=True,
                   capture_output=True)
    (fdir / "leerie_dir").mkdir()
    (fdir / "leerie_dir" / "marker.txt").write_text("frozen")
    env = {"cwd_rel": "", "allowed_tools": "", "add_dirs_rel": [],
           "diff_base": base, "leerie_dir_abs": "/old/leerie/r1",
           "autonomous": True}
    (fdir / "env.json").write_text(json.dumps(env))
    return fdir, env, base


def test_replay_in_env_reconstructs_and_uses_current_prompt(
        leerie, tmp_path, monkeypatch):
    fdir, env, base = _make_fixture(tmp_path)
    case = {"case_id": "implementer-010", "call_type": "implementer",
            "fixture": "fixtures/implementer-010/"}
    fixture = leerie._load_fixture(tmp_path / "corpus", case)
    assert fixture["dir"] == fdir
    assert fixture["env"]["diff_base"] == base

    seen = {}

    async def fake_claude_p(user_prompt, system_prompt, *, schema_key, cwd,
                            allowed_tools, max_turns, autonomous, caps, st,
                            model, sid, add_dirs=None, effort=None,
                            _suppress_capture=False):
        seen.update(user_prompt=user_prompt, system_prompt=system_prompt,
                    cwd=cwd, schema_key=schema_key,
                    suppress=_suppress_capture)
        st.last_envelope = {"result": "done", "is_error": False}
        # The reconstructed worktree must exist at cwd and contain the repo.
        assert (Path(cwd) / "calc.py").exists()
        return {"ok": True}

    monkeypatch.setattr(leerie, "claude_p", fake_claude_p)

    record = {"call_id": "imp-1", "call_type": "implementer",
              "model": "sonnet", "system_prompt": "OLD",
              "user_content": "work in LEERIE_DIR=/old/leerie/r1 now",
              "response_content": "{}"}
    envelope, structured = asyncio.run(leerie.replay_in_env(
        record, fixture, override_system_prompt="NEW CURRENT PROMPT"))

    assert envelope["result"] == "done"
    assert seen["system_prompt"] == "NEW CURRENT PROMPT"   # current, not OLD
    assert seen["schema_key"] == "implementer"
    assert seen["suppress"] is True
    assert "/old/leerie/r1" not in seen["user_prompt"]      # path rewritten
