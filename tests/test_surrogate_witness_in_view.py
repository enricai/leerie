"""The surrogate witness reads a committed view (DESIGN §8 *Every
measurement reads the committed view*, *A set that keeps failing is
audited, and rewritten once*): leerie commits what the surrogate fixer left
in its ref-less clone — a commit of its own, on no branch — removes the
clone, and runs the witness and the surrogate's set audit in a tree of that
commit installed from its committed manifests. What the surrogate left
ignored (an install, a build output) is not its fix and is not there.

Real git repositories and a real pytest command; only `claude_p` is
stubbed.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from tests.conftest import run_git_cwd_first_stdout as _git
from tests.test_acceptance_gate import FIXED, _commit_fix, _repo, _st, _staging
from tests.test_acceptance_set_audit import (OVERSPECIFIED, Workers, _run,
                                             _set)


def _setup(leerie, tmp_path, defect_text, extra=None):
    repo, _head = _repo(tmp_path)
    if extra:
        extra(repo)
        _git(repo, "add", "-A")
        _git(repo, "commit", "-qm", "more")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head, working_branch="main")
    staging = _staging(st, repo)
    _commit_fix(staging)
    st.data["acceptance"] = {"validity_base": head,
                             "sets": [_set(st, 1, defect_text)]}
    st.save()
    return head, st, staging, _git(staging, "rev-parse", "HEAD")


def _spy_witness(leerie, monkeypatch):
    seen = []
    real = leerie._acceptance_run_placed

    async def spy(st, caps, wt, sets, label, **kw):
        wt = Path(wt)
        seen.append({
            "wt": wt,
            "clone_exists": leerie._helper_worktree_path(
                st, "acceptance-surrogate").exists(),
            "files": sorted(str(p.relative_to(wt)) for p in wt.rglob("*")
                            if p.is_file() and ".git" not in p.parts),
            "head": _git(wt, "rev-parse", "HEAD"),
            "parent": _git(wt, "rev-parse", "HEAD^")})
        res = await real(st, caps, wt, sets, label, **kw)
        seen[-1]["result"] = res
        return res
    monkeypatch.setattr(leerie, "_acceptance_run_placed", spy)
    return seen


def _spy_surrogate(leerie, monkeypatch):
    got = []
    real = leerie._acceptance_surrogate

    async def spy(*a, **kw):
        res = await real(*a, **kw)
        got.append(res)
        return res
    monkeypatch.setattr(leerie, "_acceptance_surrogate", spy)
    return got


def test_the_witness_runs_in_a_view_of_the_surrogate_s_commit(
        leerie, tmp_path, monkeypatch):
    def extra(repo):
        (repo / "notes.txt").write_text("to be removed by the fix\n")
    head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED, extra)

    def fix(kw):
        wt = Path(kw["cwd"])
        (wt / "calc.py").write_text(FIXED)
        (wt / "notes.txt").unlink()
        (wt / "helper_new.py").write_text("X = 1\n")
        # Committed in its own clone's `.git`, which leerie never reads.
        _git(wt, "add", "helper_new.py")
        _git(wt, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
             "-qm", "worker's own commit")
        # Ignored by the repository's own rule: not the fix.
        (wt / "__pycache__").mkdir(exist_ok=True)
        (wt / "__pycache__" / "left.pyc").write_bytes(b"stale")
        return {"summary": "fixed add", "files_changed": ["calc.py"]}
    seen = _spy_witness(leerie, monkeypatch)
    kinds = []
    real_checkout = leerie._acceptance_writer_checkout
    monkeypatch.setattr(leerie, "_acceptance_writer_checkout",
                        lambda *a, **k: kinds.append(real_checkout(*a, **k))
                        or kinds[-1])
    w = Workers(audit=lambda kw, n: {"cases": []}, surrogate=fix)
    _run(leerie, st, staging, sha, w, monkeypatch)
    (sur,) = w.of("surrogate_fixer")
    witness = [s for s in seen if "surrogate" in s["wt"].name]
    assert witness, seen
    got = witness[-1]
    # Leerie's tree of the surrogate's commit, the clone already gone.
    assert got["wt"] == leerie._helper_worktree_path(
        st, "acceptance-surrogate-view")
    assert got["wt"] != Path(sur["cwd"])
    assert got["clone_exists"] is False
    assert got["parent"] == head
    assert "helper_new.py" in got["files"]
    assert "notes.txt" not in got["files"]
    assert "__pycache__/left.pyc" not in got["files"]
    repo = st.run_dir / "repo"
    rev = got["head"]
    assert subprocess.run(
        ["git", "--git-dir", str(repo), "show", f"{rev}:calc.py"],
        capture_output=True, text=True, check=True).stdout == FIXED
    # On no branch: kept for the view only, and the run branch untouched.
    branches = subprocess.run(
        ["git", "--git-dir", str(repo), "branch", "--contains", rev],
        capture_output=True, text=True, check=True).stdout
    assert branches.strip() == ""
    assert _git(staging, "rev-parse", "HEAD") == sha
    # Made in the ref-less clone, fetched from its git directory and kept.
    assert kinds[0] == "clone"
    assert _git(st.run_dir, "--git-dir", str(repo), "rev-parse",
                f"refs/leerie/kept/{rev}") == rev
    # Both trees are gone afterwards, and the clone's git directory.
    assert not got["wt"].exists()
    assert not Path(sur["cwd"]).exists()
    assert not leerie._clone_git_dir(Path(sur["cwd"])).exists()


DEPS_TEST = (
    "import pathlib\n\n"
    "def test_installed_from_the_fix_s_manifest():\n"
    "    root = pathlib.Path(__file__).resolve().parent.parent\n"
    "    assert (root / '.deps' / 'installed').read_text() == 'v2\\n'\n")


def test_a_fix_that_changes_a_manifest_is_installed_from_it(
        leerie, tmp_path, monkeypatch):
    """The surrogate's clone is installed before it works, from the own
    start's manifest; its fix changes the manifest. The witness's tree is
    installed from the committed fix, so the set sees what the fix's
    manifest installs — and passes on the independent fix (no rewrite)."""
    def extra(repo):
        (repo / "deps.txt").write_text("v1\n")
        (repo / ".gitignore").write_text("__pycache__/\n.deps/\n")
    head, st, staging, sha = _setup(leerie, tmp_path, DEPS_TEST, extra)
    st.data["provision"] = {"recipe": [{"kind": "install", "command": [
        "bash", "-c", "mkdir -p .deps && cp deps.txt .deps/installed"]}]}
    st.save()

    def fix(kw):
        wt = Path(kw["cwd"])
        # The clone was installed from v1 before the fixer ran.
        assert (wt / ".deps" / "installed").read_text() == "v1\n"
        (wt / "deps.txt").write_text("v2\n")
        return {"summary": "bump the dependency", "files_changed": ["deps.txt"]}
    seen = _spy_witness(leerie, monkeypatch)
    w = Workers(audit=lambda kw, n: {"cases": []}, surrogate=fix)
    final = _run(leerie, st, staging, sha, w, monkeypatch)
    assert len(w.of("surrogate_fixer")) == 1
    witness = [s for s in seen if "surrogate" in s["wt"].name]
    assert witness and ".deps/installed" in witness[-1]["files"]
    # Measured, and passing there.
    (r,) = witness[-1]["result"]
    assert not r["unmeasured"] and r["failing_files"] == []
    # Passing on the independent fix: a fair set, not rewritten.
    assert w.of("acceptance_writer") == []
    s = st.data["acceptance"]["sets"][0]
    assert "rewrite" not in s
    assert final[0]["passed"] is False


def test_a_fix_that_leaves_only_ignored_files_changed_nothing(
        leerie, tmp_path, monkeypatch):
    head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)

    def fix(kw):
        wt = Path(kw["cwd"])
        (wt / "__pycache__").mkdir(exist_ok=True)
        (wt / "__pycache__" / "only.pyc").write_bytes(b"x")
        return {"summary": "", "files_changed": []}
    seen = _spy_witness(leerie, monkeypatch)
    got = _spy_surrogate(leerie, monkeypatch)
    w = Workers(surrogate=fix)
    _run(leerie, st, staging, sha, w, monkeypatch)
    assert len(w.of("surrogate_fixer")) == 1
    assert got == [{"abstained": "the independent fix changed nothing"}]
    assert [s for s in seen if "surrogate" in s["wt"].name] == []
    assert w.of("acceptance_writer") == []
    assert not leerie._helper_worktree_path(
        st, "acceptance-surrogate").exists()


def test_only_a_ref_less_clone_is_committed_from(leerie, tmp_path):
    """A committing worker never works in a worktree of the run repository
    (`_tree_git_dirs` stops the run), and its index would be the worker's:
    no commit is made from one."""
    head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    wt = leerie._helper_worktree_path(st, "acceptance-surrogate")
    assert leerie._worktree_add_detached(st, wt, head).returncode == 0
    (wt / "calc.py").write_text(FIXED)
    assert leerie._commit_checkout_change(st, wt, head, "m") is None


def _fixes_add(kw):
    (Path(kw["cwd"]) / "calc.py").write_text(FIXED)
    return {"summary": "fixed add", "files_changed": ["calc.py"]}


def test_a_fix_that_cannot_be_committed_witnesses_nothing(
        leerie, tmp_path, monkeypatch):
    head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    monkeypatch.setattr(leerie, "_commit_checkout_change",
                        lambda *a, **k: None)
    seen = _spy_witness(leerie, monkeypatch)
    got = _spy_surrogate(leerie, monkeypatch)
    w = Workers(surrogate=_fixes_add)
    _run(leerie, st, staging, sha, w, monkeypatch)
    assert got == [{"abstained": "the independent fix could not be committed"}]
    assert [s for s in seen if "surrogate" in s["wt"].name] == []
    assert w.of("acceptance_writer") == []
    assert not leerie._helper_worktree_path(
        st, "acceptance-surrogate").exists()


def test_a_fix_whose_tree_cannot_be_set_up_witnesses_nothing(
        leerie, tmp_path, monkeypatch):
    head, st, staging, sha = _setup(leerie, tmp_path, OVERSPECIFIED)
    real_add = leerie._worktree_add_detached_async

    async def add(st_, wt, rev, **kw):
        if Path(wt).name.endswith("acceptance-surrogate-view"):
            return subprocess.CompletedProcess([], 1, "", "no")
        return await real_add(st_, wt, rev, **kw)
    monkeypatch.setattr(leerie, "_worktree_add_detached_async", add)
    logged = []
    monkeypatch.setattr(leerie, "log", lambda m, *a, **k: logged.append(m))
    seen = _spy_witness(leerie, monkeypatch)
    w = Workers(surrogate=_fixes_add)
    _run(leerie, st, staging, sha, w, monkeypatch)
    assert any("the independent fix's tree could not be set up" in m
               for m in logged), logged
    assert [s for s in seen if "surrogate" in s["wt"].name] == []
    assert w.of("acceptance_writer") == []
    assert not leerie._helper_worktree_path(
        st, "acceptance-surrogate-view").exists()
