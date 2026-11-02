"""A worker cannot change the environment of another worker or of leerie
(DESIGN §6 *Each worker's own configuration and temporary directories*).

Every worker used to share `~/.claude` and `/tmp`. The configuration
directory runs hooks: measured on the host (CLI 2.1.312, haiku), hooks a
`settings.json` there carries ran in the next session. So each worker now
gets `<run-dir>/workers/<wid>/config/` (`CLAUDE_CONFIG_DIR`, a copy of the
run's snapshot of the operator's configuration) and `.../tmp/` (`TMPDIR`),
the latter removed when the call returns.

Driven through the real `claude_p` and `_invoke`, with a stub `claude` on
PATH that behaves as the CLI does where it matters here: it fails without
credentials in its configuration directory, runs the `SessionStart` hooks
that directory's `settings.json` names, writes its session transcript under
`projects/`, and, asked to, plants a hook there the way a worker could.
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
from pathlib import Path

import pytest

STUB = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys, tempfile
# As the CLI: the configuration directory defaults to ~/.claude.
cfg = pathlib.Path(os.environ.get("CLAUDE_CONFIG_DIR")
                   or pathlib.Path.home() / ".claude")
# As Node's os.tmpdir(), which the CLI uses: TMPDIR, else /tmp.
tmp = pathlib.Path(os.environ.get("TMPDIR") or "/tmp")
prompt = sys.stdin.read()
if not prompt.strip():   # leerie's capability probe: no session starts
    sys.exit("Error: Input must be provided either through stdin or as a "
             "prompt argument when using --print")
argv = sys.argv[1:]
resume = argv[argv.index("--resume") + 1] if "--resume" in argv else None
try:
    creds = json.loads((cfg / ".credentials.json").read_text())
except OSError:
    print("Not logged in", file=sys.stderr)
    sys.exit(1)
settings = cfg / "settings.json"
if settings.exists():
    for h in json.loads(settings.read_text()).get("hooks", {}).get(
            "SessionStart", []):
        for c in h.get("hooks", []):
            subprocess.run(c["command"], shell=True)
session = resume or os.environ.get("STUB_SESSION", "s-default")
proj = cfg / "projects" / "-enc-cwd"
proj.mkdir(parents=True, exist_ok=True)
(proj / f"{session}.jsonl").write_text("{}\n")
tempfile.mkstemp(prefix="stub-scratch-", dir=tmp)
if "PLANT" in prompt:
    log = os.environ["STUB_LOG"]
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [
        {"type": "command",
         "command": f"echo PLANTED_HOOK_RAN >> '{log}'"}]}]}}))
planted_skill = None
if "SKILL" in prompt:      # into a directory the template shares
    try:
        (cfg / "skills" / "evil.md").write_text("planted")
        planted_skill = "written"
    except OSError as e:
        planted_skill = type(e).__name__
with open(os.environ["STUB_LOG"], "a") as f:
    f.write(json.dumps({"config": str(cfg), "tmp": str(tmp),
                        "skill": planted_skill,
                        "skills": sorted(os.listdir(cfg / "skills"))
                        if (cfg / "skills").is_dir() else None,
                        "token": creds.get("token"),
                        "op": json.loads((cfg / "settings.json").read_text()
                                         ).get("op") if settings.exists()
                        else None}) + "\n")
print(json.dumps({"type": "result", "subtype": "success",
                  "is_error": False, "result": "{}", "session_id": session,
                  "structured_output": {"title": "t", "body": "b",
                                        "used_template": None},
                  "usage": {"input_tokens": 1, "output_tokens": 1}}))
'''


@pytest.fixture
def world(leerie, tmp_path, monkeypatch):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "claude").write_text(STUB)
    (bindir / "claude").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ['PATH']}")
    home = tmp_path / "home"
    (home / ".claude" / "projects" / "old").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text(
        json.dumps({"token": "OPERATOR-TOKEN"}))
    (home / ".claude" / "settings.json").write_text(json.dumps({"op": 1}))
    (home / ".claude" / "skills").mkdir()
    (home / ".claude" / "skills" / "op.md").write_text("operator's")
    (home / ".claude.json").write_text(json.dumps({"hasCompletedOnboarding":
                                                   True}))
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("CLAUDE_CONFIG_DIR", raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    st = leerie.State(tmp_path / "state", "run-w", repo_root=repo)
    (st.run_dir / "logs").mkdir(parents=True, exist_ok=True)
    # The stub worker is confined (DESIGN §6 *A worker writes only where it
    # is granted*): its log goes where every worker of the run may write,
    # the run's shared cache directory.
    log = st.run_dir / "workers" / "cache" / "stub.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("STUB_LOG", str(log))
    st.data = {"verbosity": "quiet", "worker_count": 0}
    st.save()
    return st, log, home


def _call(leerie, st, sid, prompt="hello", resume=None):
    return asyncio.run(leerie.claude_p(
        user_prompt=prompt, system_prompt="sys", schema_key="pr_writer",
        cwd=str(st.repo_root), allowed_tools="Read", max_turns=1,
        autonomous=False, caps=dict(leerie.DEFAULT_CAPS), st=st,
        model="haiku", sid=sid, resume_session=resume))


def _lines(log: Path) -> list:
    return [json.loads(x) if x.startswith("{") else x
            for x in log.read_text().splitlines()]


def test_a_hook_one_worker_plants_never_runs_in_another(leerie, world,
                                                        monkeypatch):
    st, log, home = world
    monkeypatch.setenv("STUB_SESSION", "sess-a")
    _call(leerie, st, "worker-a", prompt="PLANT a hook")
    monkeypatch.setenv("STUB_SESSION", "sess-b")
    _call(leerie, st, "worker-b")
    a, b = _lines(log)
    assert "PLANTED_HOOK_RAN" not in log.read_text()
    assert a["config"] != b["config"]
    assert a["config"] == str(st.run_dir / "workers" / "worker-a" / "config")
    # The operator's own settings and credentials reached both copies; the
    # planted settings stayed in worker-a's, and the operator's config and
    # the run's template are as they were.
    assert a["token"] == b["token"] == "OPERATOR-TOKEN"
    assert b["op"] == 1
    assert json.loads((home / ".claude" / "settings.json").read_text()) == {
        "op": 1}
    tmpl = st.run_dir / "claude-config"
    assert json.loads((tmpl / "settings.json").read_text()) == {"op": 1}
    assert (tmpl / ".claude.json").is_file()
    assert not (tmpl / "projects").exists()       # per-session state skipped
    assert "hooks" in json.loads(
        (st.run_dir / "workers" / "worker-a" / "config" / "settings.json"
         ).read_text())


def test_directories_are_shared_read_only_not_copied(leerie, world,
                                                     monkeypatch):
    """The template's directories (plugins, skills and the like) reach every
    worker as links to one read-only copy: a worker cannot write into them,
    so the next worker sees only what the operator had."""
    st, log, _home = world
    _call(leerie, st, "worker-a", prompt="SKILL")
    _call(leerie, st, "worker-b")
    a, b = _lines(log)
    assert a["skill"] == "PermissionError"
    assert a["skills"] == b["skills"] == ["op.md"]
    skills = st.run_dir / "workers" / "worker-a" / "config" / "skills"
    assert skills.is_symlink()
    assert skills.resolve() == (st.run_dir / "claude-config" / "skills")
    settings = st.run_dir / "workers" / "worker-a" / "config" / "settings.json"
    assert not settings.is_symlink() and os.access(settings, os.W_OK)


def test_each_worker_has_its_own_tmpdir_removed_after_the_call(
        leerie, world):
    st, log, _home = world
    _call(leerie, st, "worker-a")
    _call(leerie, st, "worker-b")
    a, b = _lines(log)
    assert a["tmp"] == str(st.run_dir / "workers" / "worker-a" / "tmp")
    assert a["tmp"] != b["tmp"]
    # The stub wrote a scratch file there; the directory is gone now.
    assert not Path(a["tmp"]).exists() and not Path(b["tmp"]).exists()


def test_a_resume_runs_in_the_directories_that_hold_its_session(
        leerie, world, monkeypatch):
    st, log, _home = world
    monkeypatch.setenv("STUB_SESSION", "sess-a")
    _call(leerie, st, "worker-a")
    _call(leerie, st, "worker-a-clean1", resume="sess-a")
    a, again = _lines(log)
    assert again["config"] == a["config"]
    assert not (st.run_dir / "workers" / "worker-a-clean1").exists()


def test_auth_fails_without_credentials_in_the_config(leerie, world):
    """The stub's credential check is real: with none in the operator's
    configuration, the worker's copy has none, and the call fails."""
    st, log, home = world
    (home / ".claude" / ".credentials.json").unlink()
    with pytest.raises(Exception):
        _call(leerie, st, "worker-a")
    assert not log.exists() or log.read_text() == ""


def test_finalize_removes_every_workers_directories(leerie, world):
    st, _log, _home = world
    _call(leerie, st, "worker-a")
    assert (st.run_dir / "workers" / "worker-a" / "config").is_dir()
    leerie._remove_worker_dirs(st.run_dir)
    assert not (st.run_dir / "workers").exists()
    # The template holds the credentials, read-only: it goes too.
    assert not (st.run_dir / "claude-config").exists()
