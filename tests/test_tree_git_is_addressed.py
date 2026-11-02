"""Leerie's git never takes a tree's repository from the tree (DESIGN §6
*Leerie's git never takes a tree's repository from the tree*; review round
19 isolation H1).

Confined code — a measurement's install in a committed view, a worker in its
own tree — may write everything in its tree, the tree's `.git` file
included, and a committing worker its whole tree repository. Leerie's own
git on that tree runs unconfined, so it must neither follow the tree's
`.git` file nor read configuration confined code wrote: every leerie git
command on a run tree names the repository from leerie's layout
(`_tree_git_args`, `leerie_tgit`) and carries leerie's pins and settings.

Three parts: the round's proof, rewritten to assert the protection; a
general check that every leerie read of a tampered tree reads the real
repository and runs nothing from the tree; and guards derived from the
source that no leerie git command on a tree is addressed any other way.
"""
from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from tests.conftest import run_git_cwd_first_stdout as _git

LIB = REPO_ROOT / "scripts" / "worktree-lib.sh"
RUN_ID = "r19tg00001"


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


def _sh(*cmd: str, env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", "-c", 'set -uo pipefail; . "$1"; shift; '
                           '"$@"', "_", str(LIB), *cmd],
                          capture_output=True, text=True,
                          env={**os.environ, **(env or {})})


@pytest.fixture()
def run(leerie, tmp_path, monkeypatch):
    """A run repository with a committed view (a worktree of it) and a
    subtask tree (a worktree of its own tree repository), each with one
    tracked edit leerie must see."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / "a.txt").write_text("base\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    run_dir = tmp_path / "state" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    view = run_dir / "worktrees" / f"{RUN_ID[:12]}-view-x"
    _git(repo, "worktree", "add", "-q", "--detach", str(view), "main")
    _git(repo, "branch", "b-sub", "main")
    sub = run_dir / "worktrees" / f"{RUN_ID[:12]}-sub"
    subprocess.run(["bash", "-c", '. "$1"; leerie_tree "$2" "$3" "$4"', "_",
                    str(LIB), str(run_dir / "tree-repos" / sub.name),
                    "b-sub", str(sub)],
                   env={**os.environ, "LEERIE_GIT_DIR": str(repo)},
                   check=True, capture_output=True, text=True)
    for t in (view, sub):
        (t / "a.txt").write_text("edited\n")
    head = _git(repo, "rev-parse", "main")
    return {"run_dir": run_dir, "repo": repo, "view": view, "sub": sub,
            "head": head, "tmp": tmp_path}


def _plant(tree: Path, marker: Path, how: str) -> None:
    """What confined code could leave in `tree`: a repository of its own
    whose config names programs git runs (fsmonitor, a filter, a textconv,
    hooks), each recording that it ran; and `tree`'s repository pointed at
    it — by rewriting the `.git` file, or by a `.git` directory in its
    place."""
    inner = tree / ".inner"
    inner.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main", str(inner)],
                   check=True)
    (inner / "f").write_text("x\n")
    subprocess.run(["git", "-C", str(inner), "-c", "user.email=w@w", "-c",
                    "user.name=w", "commit", "-qm", "c", "--allow-empty"],
                   check=True, capture_output=True)
    prog = inner / ".git" / "prog.sh"
    prog.write_text(f"#!/bin/sh\necho ran >> '{marker}'\ncat\n")
    prog.chmod(0o755)
    hooks = inner / ".git" / "planted-hooks"
    hooks.mkdir()
    for h in ("pre-commit", "post-checkout", "post-merge", "reference-"
              "transaction", "post-index-change"):
        (hooks / h).write_text(f"#!/bin/sh\necho ran >> '{marker}'\n")
        (hooks / h).chmod(0o755)
    for k, v in (("core.fsmonitor", str(prog)),
                 ("filter.x.clean", str(prog)),
                 ("filter.x.smudge", str(prog)),
                 ("filter.x.process", str(prog)),
                 ("diff.x.textconv", str(prog)),
                 ("core.hooksPath", str(hooks))):
        subprocess.run(["git", "-C", str(inner), "config", k, v], check=True)
    (inner / ".git" / "info").mkdir(exist_ok=True)
    (inner / ".git" / "info" / "attributes").write_text(
        "* filter=x diff=x\n")
    dot = tree / ".git"
    if how == "file":
        dot.unlink()
        dot.write_text(f"gitdir: {inner / '.git'}\n")
    else:
        dot.unlink()
        os.rename(inner / ".git", dot)


@pytest.mark.parametrize("which", ["view", "sub"])
@pytest.mark.parametrize("how", ["file", "dir"])
def test_every_read_of_a_tampered_tree_reads_the_real_repository(
        leerie, run, which, how):
    t = run[which]
    marker = run["tmp"] / "RAN"
    _plant(t, marker, how)
    args = leerie._tree_git_args(t)
    # The repository named, from leerie's layout.
    gd = Path(args[args.index("--git-dir") + 1])
    common = (run["repo"] if which == "view"
              else run["run_dir"] / "tree-repos" / t.name)
    assert gd.parent.parent == common
    # Every way leerie reads or acts on the tree.
    assert subprocess.run(["git", *args, "rev-parse", "HEAD"],
                          capture_output=True, text=True
                          ).stdout.strip() == run["head"]
    assert leerie._tracked_content(t) == {
        "a.txt": leerie._tracked_content(t)["a.txt"]}
    # The tracked edit, and what was planted as the untracked dirt it is.
    planted = {"?? .inner/", "?? .inner/f"}
    lines = asyncio.run(leerie._tree_dirt(t))
    assert lines[0] == " M a.txt" and set(lines[1:]) <= planted, lines
    assert asyncio.run(leerie._committed_tree_sha(str(t))) == _git(
        run["repo"], "rev-parse", "main^{tree}")
    assert asyncio.run(leerie._branch_head_sha(str(t))) == run["head"]
    subprocess.run(["git", *args, "diff", "HEAD"], capture_output=True)
    sh = _sh("leerie_tree_dirt", str(t))
    assert sh.returncode == 0 and sh.stdout.splitlines() == lines, \
        sh.stderr
    assert _sh("leerie_tgit", str(t), "rev-parse", "HEAD"
               ).stdout.strip() == run["head"]
    before = leerie._tracked_content(t, write=True)
    (t / "a.txt").write_text("changed by a command\n")
    assert leerie._restore_tracked_content(t, before) == ["a.txt"]
    assert (t / "a.txt").read_text() == "edited\n"
    # The bracket's own restore, then the shell's reset, both acting.
    assert _sh("leerie_tgit", str(t), "checkout", "-q", "--", "a.txt"
               ).returncode == 0
    assert (t / "a.txt").read_text() == "base\n"
    # Nothing confined code planted ran, and nothing was written there.
    assert not marker.exists()


def test_a_tree_repository_that_names_another_repository_is_refused(
        leerie, run):
    """A committing worker may write its tree repository, its worktree's
    `commondir` included; leerie then reads nothing rather than follow
    it."""
    t = run["sub"]
    marker = run["tmp"] / "RAN"
    _plant(t, marker, "file")
    admin = leerie._admin_dir_of(run["run_dir"] / "tree-repos" / t.name, t)
    (admin / "commondir").write_text(str(t / ".inner" / ".git") + "\n")
    assert leerie._run_tree_location(t) is False
    assert asyncio.run(leerie._tree_dirt(t)) is None
    assert _sh("leerie_tgit", str(t), "status").returncode == 128
    assert not marker.exists()


def _abi() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "lk", REPO_ROOT / "scripts" / "landlock_exec.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.abi()


@pytest.mark.skipif(_abi() < 3, reason="the host kernel has no Landlock "
                    "ABI 3")
def test_the_rounds_measurement_proof_runs_nothing_of_the_view(leerie, run):
    """The round's proof, asserting the protection: a confined command in
    a view rewrites the view's `.git` file to a repository of its own whose
    `core.fsmonitor` program would read a held-back set, move the run
    branch and rewrite `state.json`. Leerie's bracket around the command
    (`_leaves_tree_as_found` → `_tracked_content`) then reads the view's
    real repository, and the program never runs."""
    rd, repo, view = run["run_dir"], run["repo"], run["view"]
    (rd / "acceptance" / "heldback").mkdir(parents=True)
    secret = rd / "acceptance" / "heldback" / "secret.txt"
    secret.write_text("HELD\n")
    (rd / "workers" / "cache").mkdir(parents=True)
    (rd / "state.json").write_text('{"gate": "pending"}')
    run_ref = f"refs/heads/leerie/runs/{RUN_ID}"
    _git(repo, "update-ref", run_ref, "main")
    leak = rd / "workers" / "cache" / "leaked.txt"
    st = _St(run["repo"].parent.parent.parent.parent / "user", rd)
    grants = leerie._measurement_grants(st, view)
    script = f'''
        set -e
        cd "{view}"
        rm -f .git
        mkdir -p .inner && git -C .inner init -q -b main
        git -C .inner -c user.email=w@w -c user.name=w commit -q \\
            --allow-empty -m c
        cat > .inner/.git/fsmon.sh <<EOS
#!/bin/sh
cat "{secret}" > "{leak}" 2>/dev/null
git --git-dir="{repo}" update-ref {run_ref} HEAD 2>/dev/null
printf '{{"gate": "passed"}}' > "{rd}/state.json"
printf "0\\0"
EOS
        chmod +x .inner/.git/fsmon.sh
        git -C .inner config core.fsmonitor "{view}/.inner/.git/fsmon.sh"
        printf 'gitdir: {view}/.inner/.git\\n' > .git
    '''
    sb = subprocess.run(leerie._confined_argv(grants, ["bash", "-c",
                                                        script]),
                        capture_output=True, text=True)
    assert sb.returncode == 0, sb.stderr[-500:]
    got = leerie._tracked_content(view)
    assert got is not None and "a.txt" in got     # the real repository
    assert not leak.exists()
    assert _git(repo, "rev-parse", run_ref) == run["head"]
    assert (rd / "state.json").read_text() == '{"gate": "pending"}'


# --- the guards, derived from the source --------------------------------

_ADDRESSED = {"_tree_git_args", "_git_at", "_git_view"}


def _git_lists(tree: ast.AST):
    for n in ast.walk(tree):
        if (isinstance(n, ast.List) and n.elts
                and isinstance(n.elts[0], ast.Constant)
                and n.elts[0].value == "git"):
            yield n


def _addressed(lst: ast.List) -> bool:
    for e in lst.elts[1:]:
        if isinstance(e, ast.Constant) and e.value in ("--git-dir",
                                                       "--no-index"):
            return True
        if (isinstance(e, ast.Starred) and isinstance(e.value, ast.Call)
                and getattr(e.value.func, "id", "") in _ADDRESSED):
            return True
    return False


def test_no_leerie_git_command_names_a_tree_with_minus_C():
    """`-C <tree>` lets git find the repository from the tree. Only the
    one helper spells it, beside the repository it names."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    tree = ast.parse(src)
    helper = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                  and n.name == "_tree_git_args")
    inside = {id(m) for m in ast.walk(helper)}
    bad = []
    for n in ast.walk(tree):
        if not isinstance(n, ast.List) or id(n) in inside:
            continue
        consts = [e.value for e in n.elts if isinstance(e, ast.Constant)]
        is_git = consts[:1] == ["git"]
        if is_git and "-C" in consts:
            bad.append(n.lineno)
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and getattr(n.func, "id", "") in
                ("_git_sync", "_git_z_output") and n.args
                and isinstance(n.args[0], ast.List)):
            if "-C" in [e.value for e in n.args[0].elts
                        if isinstance(e, ast.Constant)]:
                bad.append(n.lineno)
    assert not bad, f"git -C outside _tree_git_args at lines {bad}"


def test_every_git_command_run_in_a_directory_names_its_repository():
    """A git command run with `cwd=` finds its repository from that
    directory unless it names one: every such call names it."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    tree = ast.parse(src)
    bad = []
    for n in ast.walk(tree):
        if (isinstance(n, ast.Call) and n.args
                and isinstance(n.args[0], ast.List)
                and n.args[0] in list(_git_lists(n.args[0]))
                and any(k.arg == "cwd" for k in n.keywords)
                and not _addressed(n.args[0])):
            bad.append(n.lineno)
    assert not bad, f"git run by cwd, repository not named, at {bad}"


def test_every_script_that_runs_git_on_a_tree_uses_the_one_helper():
    """The scripts that source `worktree-lib.sh` are the ones that act on a
    run's trees: none runs `git -C <dir>` or `cd <dir> && git` but the
    helpers themselves."""
    scripts = [p for p in sorted((REPO_ROOT / "scripts").rglob("*.sh"))
               if "worktree-lib.sh" in p.read_text() or p == LIB]
    assert LIB in scripts and len(scripts) > 3
    raw = re.compile(r'\bgit -C\b|cd "\$[^"]+"\s*&&\s*git\b')
    bad = []
    for p in scripts:
        fn = None
        for i, line in enumerate(p.read_text().splitlines(), 1):
            m = re.match(r"^([a-z_]+)\(\) \{", line)
            if m:
                fn = m.group(1)
            code = line.split("#", 1)[0]
            if raw.search(code) and fn not in ("leerie_tgit",):
                bad.append(f"{p.relative_to(REPO_ROOT)}:{i}")
    assert not bad, bad
