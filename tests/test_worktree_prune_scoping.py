"""`git worktree prune` must never reach a worktree leerie does not own.

A bare `git worktree prune` is repository-global and has **no grace period**:
the 3-month `gc.worktreePruneExpire` default applies to `git gc`, which calls
`git worktree prune --expire 3.months.ago`, while a bare prune drops every
registration whose directory is missing, immediately.

leerie's container bind-mounts the user's repository whole, so every container
shares the host's `.git`. A worktree the HOST registered at a path that does not
exist inside the container's mount namespace therefore looks stale to a bare
prune and is destroyed. `scripts/host-finalize.sh` creates exactly such a
worktree, at `/tmp/tmp.XXXX/rebase-<run-id>`. During one run's rebase window a
sibling run spawned three workers, each invoking `new-worktree.sh` and each
running a bare prune; the rebaser then reported its git metadata directory "has
vanished … without any destructive action on my part".

See docs/POSTMORTEM-2026-08-14.md, F19.

These tests run the real helper against real git repositories — the mechanism is
git's, so a stubbed one would prove nothing.
"""
from __future__ import annotations

import re
import ast
import io
import subprocess
import tokenize
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
LIB = REPO_ROOT / "scripts" / "worktree-lib.sh"
_SCRIPTS_WITH_PRUNE = ("new-worktree.sh", "setup-run.sh", "cleanup.sh")

# Every file that must not contain a repository-global prune, DERIVED rather
# than named. The first version was a three-filename tuple covering
# `scripts/*.sh` only, so four bare `git worktree prune` calls in
# `orchestrator/leerie.py` — running inside the container against the same
# shared `.git`, i.e. the identical F19 hazard — were invisible to it, and the
# commit that claimed to have swept "all four" had counted the shell ones.
# This is the enumeration-not-derivation class CLAUDE.md records in #180-#183.
_PRUNE_SURFACE = (
    sorted((REPO_ROOT / "scripts").glob("*.sh"))
    + sorted((REPO_ROOT / "orchestrator").glob("*.py"))
    + [REPO_ROOT / "leerie"]
)


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=str(cwd), check=True,
                          capture_output=True, text=True).stdout


def _repo(tmp_path: Path) -> Path:
    d = tmp_path / "repo"
    d.mkdir()
    _git(d, "init", "-q", "-b", "main")
    _git(d, "config", "user.email", "t@leerie.local")
    _git(d, "config", "user.name", "leerie test")
    (d / "f.txt").write_text("x\n")
    _git(d, "add", "-A")
    _git(d, "commit", "-qm", "base")
    return d


def _registered(repo: Path) -> set[str]:
    out = _git(repo, "worktree", "list", "--porcelain")
    return {l[len("worktree "):] for l in out.splitlines()
            if l.startswith("worktree ")}


def _run_prune(repo: Path, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c",
         f'set -euo pipefail; . "{LIB}"; cd "{repo}"; '
         f'prune_leerie_worktrees "{root}"'],
        capture_output=True, text=True)


def _make_stale(repo: Path, path: Path, branch: str) -> None:
    """Register a worktree, then delete its directory — the stale shape."""
    path.parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "worktree", "add", "-q", "-b", branch, str(path))
    subprocess.run(["rm", "-rf", str(path)], check=True)


def test_a_stale_leerie_worktree_is_pruned(tmp_path):
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    wt = root / "runs" / "r1" / "worktrees" / "feat-001"
    _make_stale(repo, wt, "leerie/subtasks/r1/feat-001")
    assert str(wt.resolve()) in _registered(repo)

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(wt.resolve()) not in _registered(repo), (
        "a stale registration under leerie's own root must be dropped — that is "
        "the whole job the bare prune was doing")


def test_a_stale_worktree_OUTSIDE_the_root_survives(tmp_path):
    """The defect, directly: the host's rebase worktree must not be touched."""
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    root.mkdir()
    host_wt = tmp_path / "tmp.HOSTXYZ" / "rebase-abc123"
    _make_stale(repo, host_wt, "leerie/runs/abc123")
    assert str(host_wt.resolve()) in _registered(repo)

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(host_wt.resolve()) in _registered(repo), (
        "a registration outside leerie's state root is not ours to prune; "
        "destroying it is what killed a live rebase mid-operation")


def test_a_bare_prune_would_have_destroyed_it(tmp_path):
    """Falsification control.

    Without this, the test above proves only that the fixture survives
    something — not that the scoping is what saves it.
    """
    repo = _repo(tmp_path)
    host_wt = tmp_path / "tmp.HOSTXYZ" / "rebase-abc123"
    _make_stale(repo, host_wt, "leerie/runs/abc123")
    assert str(host_wt.resolve()) in _registered(repo)

    _git(repo, "worktree", "prune")
    assert str(host_wt.resolve()) not in _registered(repo), (
        "if a bare prune no longer destroys this, git's behaviour changed and "
        "the scoping rationale needs re-deriving")


def test_a_LIVE_leerie_worktree_survives(tmp_path):
    """Dropping a live registration lost a completed subtask once.

    Run 488c42e5 lost `bugfix-009-2` after its implementer had committed,
    because a sibling's prune deregistered a worktree that was still in use.
    """
    repo = _repo(tmp_path)
    root = tmp_path / "state"
    wt = root / "runs" / "r1" / "worktrees" / "feat-002"
    wt.parent.mkdir(parents=True)
    _git(repo, "worktree", "add", "-q", "-b", "leerie/subtasks/r1/feat-002",
         str(wt))

    r = _run_prune(repo, root)
    assert r.returncode == 0, r.stderr
    assert str(wt.resolve()) in _registered(repo)
    assert wt.is_dir()


def test_prune_never_fails_its_caller(tmp_path):
    """All three call sites run under `set -e`; this is housekeeping."""
    repo = _repo(tmp_path)
    for root in (tmp_path / "does-not-exist", tmp_path):
        r = _run_prune(repo, root)
        assert r.returncode == 0, (root, r.stderr)


def test_outside_a_git_repo_is_a_silent_no_op(tmp_path):
    r = _run_prune(tmp_path, tmp_path)
    assert r.returncode == 0, r.stderr




def _shell_code_only(src: str) -> str:
    """Shell source with comments removed, including TRAILING ones.

    The `#`-prefix line heuristic this replaces made the guard depend on where
    a comment sat: `prune_leerie_worktrees "$X"  # never a bare git worktree
    prune here` failed, while the same words on their own line passed. These
    three scripts carry ~10 explanatory comment lines each precisely because
    the construct is forbidden, so that is a guard which fails on correct code.

    Quote-aware rather than a bare `#` split: `${VAR#prefix}` and a `#` inside
    a string are not comments.
    """
    out = []
    for line in src.splitlines():
        q = None
        for i, ch in enumerate(line):
            if q:
                if ch == q:
                    q = None
                continue
            if ch in "'\"":
                q = ch
                continue
            if ch == "#" and (i == 0 or line[i - 1] in " \t"):
                line = line[:i]
                break
        out.append(line)
    return "\n".join(out)


def _code_only(src: str) -> str:
    """Python source with comments and docstrings removed, via `tokenize`."""
    out, last = [], (1, 0)
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type == tokenize.COMMENT:
            continue
        if tok.start[0] > last[0]:
            out.append("\n" * (tok.start[0] - last[0]))
            last = (tok.start[0], 0)
        out.append(" " * max(0, tok.start[1] - last[1]) + tok.string)
        last = tok.end
    text = "".join(out)
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return text
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef, ast.Module)):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                text = text.replace(doc, "", 1)
    return text


def _bare_prune_offenders(code: str) -> list[str]:
    """Lines running a repository-global `git worktree prune`.

    Anchored at `git`, not at the start of a line: the first version required
    `^\\s*git worktree prune`, so `git -C "$USER_REPO" worktree prune`,
    `cd "$repo" && git worktree prune` and `eval "git worktree prune"` all
    reopened the failure while the scan stayed silent. `-C <dir>` and other
    leading options are matched explicitly.
    """
    pat = re.compile(r"\bgit\b(?:\s+-[^\s]+(?:\s+[^\s]+)?)*\s+worktree\s+prune\b")
    out = []
    for l in code.splitlines():
        if not pat.search(l):
            continue
        # `-n`/`--dry-run` ASKS git what it would prune and removes nothing —
        # it is what a scoped implementation is built on, and
        # `scripts/worktree-lib.sh` runs exactly that. Flagging it forbids the
        # fix.
        if re.search(r"\bprune\b[^\n]*(?:\s-n\b|--dry-run\b)", l):
            continue
        out.append(l.strip())
    return out

@pytest.mark.parametrize("script", _SCRIPTS_WITH_PRUNE)
def test_no_script_runs_a_bare_prune(script):
    """The sweep: every call site uses the scoped helper.

    Comments are stripped first — the replacement comments necessarily name the
    construct they forbid, so a raw scan matches the prose explaining it.
    """
    src = (REPO_ROOT / "scripts" / script).read_text()
    code = _shell_code_only(src)
    assert not _bare_prune_offenders(code), (
        f"{script} still runs a repository-global prune; use "
        "prune_leerie_worktrees \"$LEERIE_ROOT\": "
        + "; ".join(_bare_prune_offenders(code)))
    assert "prune_leerie_worktrees" in code, (
        f"{script} must still prune leerie's own stale registrations — "
        "removing the prune entirely reopens the orphaned-directory failure "
        "new-worktree.sh documents")


@pytest.mark.parametrize("script", _SCRIPTS_WITH_PRUNE)
def test_each_script_sources_the_lib(script):
    src = (REPO_ROOT / "scripts" / script).read_text()
    assert "worktree-lib.sh" in src, script


@pytest.mark.parametrize("evasion", [
    "  git worktree prune",
    '  git -C "$USER_REPO" worktree prune',
    '  cd "$repo" && git worktree prune',
    '  git --git-dir=/x/.git worktree prune -v',
])
def test_the_scan_fires_on_every_evasion(evasion):
    """Anti-vacuity, absent from the original.

    Three of these four evaded the start-of-line anchor while running exactly
    the repository-global prune that dropped the host's rebase worktrees.
    """
    assert _bare_prune_offenders(evasion), evasion


@pytest.mark.parametrize("benign", [
    '  prune_leerie_worktrees "$LEERIE_ROOT"',
    '  # git worktree prune would drop the host\'s registrations',
    '  git worktree list',
])
def test_the_scan_leaves_the_replacement_alone(benign):
    """The converse: flagging the scoped helper, or a comment naming the
    forbidden construct, would make the guard fail on correct code."""
    code = "\n".join(l for l in benign.splitlines()
                     if not l.lstrip().startswith("#"))
    assert not _bare_prune_offenders(code), benign


# --- the derived sweep -------------------------------------------------------

def _python_bare_prune_offenders(code: str) -> list[str]:
    """Lines running a repository-global prune from Python.

    `["git", "worktree", "prune"]` as an argv list, with nothing scoping it.
    The `-n`/`-v` dry-run form `_prune_leerie_worktrees` itself uses is what a
    scoped implementation looks like, so it is not an offender.
    """
    out = []
    for line in code.splitlines():
        if '"worktree", "prune"' not in line:
            continue
        if '"-n"' in line or '"--dry-run"' in line:
            continue
        out.append(line.strip())
    return out


@pytest.mark.parametrize("path", _PRUNE_SURFACE, ids=lambda p: p.name)
def test_no_file_anywhere_runs_a_bare_prune(path):
    """The whole surface, not a hand-kept list of three shell scripts."""
    raw = path.read_text()
    code = _code_only(raw) if path.suffix == ".py" else _shell_code_only(raw)
    # Per-language, deliberately. The shell regex matches free prose, and a
    # Python file's docstrings necessarily NAME the construct they forbid — so
    # running it over `.py` flags the very comments explaining the rule.
    offenders = (_python_bare_prune_offenders(code) if path.suffix == ".py"
                 else _bare_prune_offenders(code))
    assert not offenders, (
        f"{path.name} runs a repository-global `git worktree prune` against "
        "the shared, bind-mounted .git — use the scoped helper "
        "(`prune_leerie_worktrees` in shell, `_prune_leerie_worktrees` in "
        "Python):\n  " + "\n  ".join(offenders))


def test_the_derived_surface_is_not_empty():
    """Anti-vacuity: a glob that matches nothing passes every case above."""
    names = {p.name for p in _PRUNE_SURFACE}
    assert len(_PRUNE_SURFACE) > 10, _PRUNE_SURFACE
    for required in ("cleanup.sh", "new-worktree.sh", "setup-run.sh",
                     "leerie.py", "leerie"):
        assert required in names, f"{required} missing from the derived surface"


@pytest.mark.parametrize("evasion", [
    '    subprocess.run(["git", "worktree", "prune"], check=False)',
    '    await run_proc(["git", "worktree", "prune"])',
])
def test_the_python_scan_fires_on_a_bare_prune(evasion):
    """The four real call sites, in the shape they actually had."""
    assert _python_bare_prune_offenders(evasion), evasion


def test_the_python_scan_leaves_the_scoped_helper_alone():
    """The helper itself runs `git worktree prune -n -v` to ASK what git would
    prune. Flagging that would forbid the fix."""
    assert not _python_bare_prune_offenders(
        '    r = subprocess.run(["git", "worktree", "prune", "-n", "-v"],')


def test_the_orchestrator_has_a_scoped_helper():
    """Anti-vacuity for the sweep: passing because the calls were deleted
    outright would be a different (worse) outcome — the prune is what clears
    the stale metadata a SIGKILLed run leaves behind."""
    src = (REPO_ROOT / "orchestrator" / "leerie.py").read_text()
    assert "def _prune_leerie_worktrees(" in src
    assert src.count("_prune_leerie_worktrees(") >= 5, (
        "the definition plus its four call sites")
