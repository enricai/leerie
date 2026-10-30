"""One restore primitive (DESIGN §8 *One restore*; review round 15 M2).

"Back to the committed state" had three implementations of different
reach: one restored no write bits, none removed a nested repository (`git
clean -fd` skips them; `-ffd` does not), and none checked that I1's
checker then read nothing. So I1's designed exhaustion path — roll the
round back and record it — ended in the staging-clean assertion's `die()` (since removed),
and the nested-repo case died again on every resume. `_restore_tree` and
`leerie_reset_worktree` are the one primitive; the drivers are the
reviewer's proofs, turned to assert the designed outcome."""
from __future__ import annotations

import asyncio
import os
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.test_run_final_conformance import _clean_result, env  # noqa: F401


def _porcelain(tree) -> str:
    return subprocess.run(["git", "status", "--porcelain",
                           "--untracked-files=all"], cwd=str(tree),
                          capture_output=True, text=True).stdout


@pytest.mark.parametrize("shape", ["nested_repo", "readonly_dir"])
def test_an_unclean_final_conformer_is_rolled_back_not_died_on(
        env, monkeypatch, shape):  # noqa: F811
    leerie = env["leerie"]
    staging = env["staging"]

    async def fake(**kw):
        if kw["schema_key"] == "conformer" and not kw.get("resume_session"):
            cwd = Path(kw["cwd"])
            if shape == "nested_repo":
                subprocess.run(["git", "init", "-q", str(cwd / "upstream")],
                               check=True)
                (cwd / "upstream" / "lib.py").write_text("x = 1\n")
            else:
                d = cwd / "fixtures-ro"
                d.mkdir()
                (d / "snap.txt").write_text("x\n")
                d.chmod(0o555)
        return _clean_result()
    monkeypatch.setattr(leerie, "claude_p", fake)
    monkeypatch.setattr(leerie, "_log_session_id", lambda p: "sess-1")
    try:
        asyncio.run(leerie._run_final_conformance(
            env["run_dir"], env["st"], env["caps"], env["models"],
            env["efforts"]))
        assert "final conformer:round 0" in env["st"].data["unclean_trees"]
        assert _porcelain(staging) == ""
        assert leerie._decide_outcome(env["st"], any_commits=True) == \
            "unconverged"
    finally:
        ro = staging / "fixtures-ro"
        if ro.exists():
            os.chmod(ro, 0o755)


def test_a_nested_repo_does_not_survive_the_resume(leerie, tmp_path,
                                                   monkeypatch):
    def _git(cwd, *a):
        return subprocess.run(["git", *a], cwd=cwd, check=True,
                              capture_output=True, text=True).stdout.strip()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@t")
    _git(repo, "config", "user.name", "t")
    (repo / "f.txt").write_text("base\n")
    (repo / ".gitignore").write_text(".leerie/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    root = repo / ".leerie"
    monkeypatch.setenv("LEERIE_STATE_DIR", str(root))
    monkeypatch.chdir(repo)
    run_id = "20261030-nested"

    def setup():
        subprocess.run(["bash", str(REPO_ROOT / "scripts" / "setup-run.sh"),
                        run_id, "main"], cwd=repo, check=True,
                       capture_output=True)
    setup()
    staging = root / "runs" / run_id / "worktrees" / "staging"
    subprocess.run(["git", "init", "-q", str(staging / "upstream")],
                   check=True)
    (staging / "upstream" / "lib.py").write_text("x = 1\n")
    setup()                                       # the resume
    assert not (staging / "upstream").exists()
    assert asyncio.run(leerie._tree_dirt(staging)) == []
    assert asyncio.run(leerie._committed_tree_sha(str(staging))) == _git(
        staging, "rev-parse", "HEAD^{tree}")


def test_the_i2_bracket_removes_a_nested_repo_its_command_made(
        leerie, tmp_path):
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    (repo / "a").write_text("a\n")
    subprocess.run(["git", "-C", str(repo), "add", "a"], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-qm", "i"], check=True)

    async def go():
        async with leerie._leaves_tree_as_found(repo, "tests", "t") as rec:
            subprocess.run(["git", "init", "-q", str(repo / "fx-repo")],
                           check=True)
            (repo / "fx-repo" / "f").write_text("x\n")
        return rec
    rec = asyncio.run(go())
    assert rec["removed"] == ["fx-repo/"]
    assert not (repo / "fx-repo").exists() and _porcelain(repo) == ""


def test_what_the_restore_cannot_remove_stops_the_run_named(
        leerie, tmp_path, monkeypatch, capsys):
    """The postcondition is I1's own checker: dirt still read after a reset
    that landed stops the run there, with the lines."""
    repo = tmp_path / "r"
    subprocess.run(["git", "init", "-q", "-b", "main", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c",
                    "user.name=t", "commit", "-q", "--allow-empty", "-m",
                    "i"], check=True)
    head = subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()

    async def stuck(tree, **kw):
        return ["?? root-owned/x"]
    monkeypatch.setattr(leerie, "_tree_dirt", stuck)
    with pytest.raises(SystemExit):
        asyncio.run(leerie._restore_tree(repo, head, where="a rollback",
                                         branch="main"))
    err = capsys.readouterr().err
    assert "a rollback:" in err and "?? root-owned/x" in err


def test_no_reset_hard_outside_the_primitive():
    """Derived (review round 16 claims H2: five raw `git reset --hard` calls
    in staging bypassed the primitive and its postcondition): the only
    `reset --hard` in leerie.py is inside `_restore_tree`, and the only one
    in the scripts is inside `leerie_reset_worktree`."""
    import ast
    tree = ast.parse((REPO_ROOT / "orchestrator" / "leerie.py").read_text())
    owner: dict[int, str] = {}
    for fn in ast.walk(tree):
        if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for n in ast.walk(fn):
                owner.setdefault(id(n), fn.name)
    hits = []
    for n in ast.walk(tree):
        if isinstance(n, ast.List):
            vals = [e.value for e in n.elts if isinstance(e, ast.Constant)]
            if "reset" in vals and "--hard" in vals:
                hits.append(owner.get(id(n)))
    assert hits == ["_restore_tree"]
    shell = []
    for p in sorted((REPO_ROOT / "scripts").rglob("*.sh")):
        fn = None
        for line in p.read_text().splitlines():
            m = __import__("re").match(r"^(\w+)\(\)\s*\{", line)
            if m:
                fn = m.group(1)
            code = line.split("#", 1)[0]
            if "reset" in code and "--hard" in code and "git" in code:
                shell.append((p.name, fn))
    assert shell == [("worktree-lib.sh", "leerie_reset_worktree")]


def test_a_back_out_whose_reset_does_not_land_never_says_the_work_left(
        leerie, tmp_path, monkeypatch):
    """`_run_integration_judge_gate`'s `_back_out` recorded "none of
    <sid>'s work is in this PR" without checking its reset landed. The
    primitive's False now stops the run with the sid `blocked`, and the
    loss is never recorded."""
    from tests.test_integrate_wave_judge_gate import (
        _conflicting_staging, _state)
    st = _state(leerie, tmp_path)
    staging, pre = _conflicting_staging(tmp_path / ".leerie")
    subprocess.run(["git", "merge", "-q", "--no-ff", "-X", "theirs", "-m",
                    "merge", "incoming"], cwd=str(staging), check=True,
                   capture_output=True)

    async def no_reset(tree, rev, *, where, branch):
        return False

    monkeypatch.setattr(leerie, "_restore_tree", no_reset)
    # A back-out the gate already decided (the write-ahead marker): the
    # resumed gate finishes it.
    st.data["integration_gate"] = {"feat-001": {
        "backing_out": "the judge found a defect",
        "merge_commit_sha": subprocess.run(
            ["git", "-C", str(staging), "rev-parse", "HEAD"],
            capture_output=True, text=True).stdout.strip()}}
    with pytest.raises(SystemExit):
        asyncio.run(leerie._run_integration_judge_gate(
            "feat-001", staging, {"status": "complete"}, [],
            dict(leerie.DEFAULT_CAPS), st, {}, {}))
    assert "back-out failed" in st.data["blocked"]["feat-001"]
    assert not [e for e in (st.data.get("risk_register") or {}).get(
        "feat-001") or [] if e.get("kind") == "integration_content_loss"]


def test_the_shell_reset_never_chmods_through_a_symlink(leerie, tmp_path):
    """Review round 16 exec L1, the reviewer's proof turned: `chmod -R`
    follows a link named on its command line, so an untracked link to the
    user's checkout had its target's modes rewritten. The Python half
    already skipped links; now both do."""
    import stat
    outside = tmp_path / "outside"
    (outside / "sub").mkdir(parents=True)
    f = outside / "sub" / "ro.txt"
    f.write_text("x")
    f.chmod(0o444)
    (outside / "sub").chmod(0o555)
    r = tmp_path / "r"
    r.mkdir()
    for a in (["init", "-q", "-b", "main"], ["config", "user.email", "t@x"],
              ["config", "user.name", "t"]):
        subprocess.run(["git", *a], cwd=r, check=True)
    (r / "a").write_text("1")
    subprocess.run(["git", "add", "-A"], cwd=r, check=True)
    subprocess.run(["git", "commit", "-qm", "b"], cwd=r, check=True)
    (r / "ref").symlink_to(outside)
    try:
        lib = REPO_ROOT / "scripts" / "worktree-lib.sh"
        p = subprocess.run(["bash", "-c", f'source "{lib}"; '
                            f'leerie_reset_worktree "{r}" main'],
                           capture_output=True, text=True)
        assert p.returncode == 0, p.stderr
        assert not (r / "ref").is_symlink()
        assert not stat.S_IMODE(f.stat().st_mode) & 0o200
        assert not stat.S_IMODE((outside / "sub").stat().st_mode) & 0o200
        leerie._restore_write_bits(r)          # the Python half, same rule
    finally:
        (outside / "sub").chmod(0o755)
