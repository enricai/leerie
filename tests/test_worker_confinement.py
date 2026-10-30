"""I5: a worker writes only where it is granted, and reads nothing of the
run's state it is not given (DESIGN §6 *A worker writes only where it is
granted (I5)*). A real child confined by `scripts/landlock_exec.py` with the
grants `_worker_grants` derives, on the run directory layout leerie makes."""
from __future__ import annotations

import ast
import asyncio
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

RUN_ID = "i5confine000"
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _git(cwd: Path, *a: str) -> str:
    return subprocess.run(["git", "-C", str(cwd), *a], check=True,
                          capture_output=True, text=True).stdout.strip()


class _St:
    def __init__(self, user: Path, run_dir: Path):
        self.repo_root = user
        self.run_id = RUN_ID
        self.run_dir = run_dir
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass


def _abi() -> int:
    import importlib.util
    spec = importlib.util.spec_from_file_location("lk", SHIM)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.abi()


pytestmark = pytest.mark.skipif(
    _abi() < 3, reason="the host kernel has no Landlock ABI 3")


@pytest.fixture()
def run(leerie, tmp_path, monkeypatch):
    """`<state>/runs/<id>/` with the run's repository, staging, a view, two
    subtask worktrees, a held-out set, the spec and state files; the user's
    checkout outside the state root; each worker directory its env names."""
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "gitconfig"))
    user = tmp_path / "user"
    user.mkdir()
    _git(user, "init", "-q", "-b", "main")
    (user / "app.py").write_text("X = 1\n")
    _git(user, "add", "-A")
    _git(user, "-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm",
         "base")
    state_root = tmp_path / "state"
    run_dir = state_root / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    repo = leerie._ensure_run_repo(_St(user, run_dir))
    wts = {}
    for name in ("staging", "wt-a", "wt-b", f"{RUN_ID}-view-staging"):
        _git(repo, "branch", f"b-{name}", "main")
        wts[name] = run_dir / "worktrees" / name
        _git(repo, "worktree", "add", "-q", str(wts[name]), f"b-{name}")
    (wts["wt-b"] / "secret.py").write_text("sibling\n")
    (run_dir / "state.json").write_text("{}\n")
    (run_dir / "acceptance" / "set-1").mkdir(parents=True)
    (run_dir / "acceptance" / "set-1" / "test_held.py").write_text("held\n")
    (run_dir / "subtasks").mkdir()
    (run_dir / "subtasks" / "wt-a.json").write_text("{}\n")
    (run_dir / "task.md").write_text("the task\n")
    own = run_dir / "workers" / "wt-a"
    env = {"TMPDIR": str(own / "tmp"),
           "CLAUDE_CONFIG_DIR": str(own / "config")}
    for var in leerie._WORKER_ENV_DIRS:
        env.setdefault(var, str(own / "cache" / var.lower()))
    for p in env.values():
        Path(p).mkdir(parents=True, exist_ok=True)
    return {"user": user, "state_root": state_root, "run_dir": run_dir,
            "repo": repo, "wts": wts, "env": env}


def _grants(leerie, run, *, schema_key="implementer", cwd=None,
            tools=None, **kw):
    return leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key=schema_key,
        cwd=cwd or run["wts"]["wt-a"],
        allowed_tools=tools or leerie.ACT_TOOLS, env=run["env"], **kw)


def _probe(leerie, grants, script: str, env: dict) -> dict[str, bool]:
    """Run `script` (lines `name|command`) in a confined bash: name ->
    whether the command succeeded."""
    lines = [ln.split("|", 1) for ln in script.strip().splitlines()]
    body = "\n".join(
        f"if ( {cmd} ) >/dev/null 2>&1; then echo 'OK {name}'; "
        f"else echo 'NO {name}'; fi" for name, cmd in lines)
    r = subprocess.run(leerie._confined_argv(grants, ["bash", "-c", body]),
                       capture_output=True, text=True,
                       env={**os.environ, **env})
    assert r.returncode == 0, r.stderr
    out = {}
    for ln in r.stdout.splitlines():
        ok, name = ln.split(" ", 1)
        out[name] = ok == "OK"
    assert set(out) == {name for name, _c in lines}, r.stdout + r.stderr
    return out


def test_an_implementer_writes_its_own_and_nothing_else(leerie, run):
    rd, wt, w = run["run_dir"], run["wts"]["wt-a"], run["wts"]
    g = _grants(leerie, run, notes_id="wt-a")
    got = _probe(leerie, g, f"""
own worktree|echo x > {wt}/f.txt
own commit|cd {wt} && git add f.txt && git -c user.email=t@x -c user.name=t commit -qm w
own tmp|echo x > $TMPDIR/f
own cross-dir rename|mkdir -p {wt}/d1 {wt}/d2 && echo z > {wt}/d1/m && mv {wt}/d1/m {wt}/d2/m
own notes|echo x > {rd}/notes/wt-a/criteria.md
own cli config|echo x > $CLAUDE_CONFIG_DIR/f
dev null|echo x > /dev/null
shm|python3 -c 'import multiprocessing as m; m.Lock()'
state.json|echo x >> {rd}/state.json
repo-settings|echo x > {rd}/repo-settings/x
run repo config|echo x >> {run['repo']}/config
staging|echo x > {w['staging']}/f
view|echo x > {w[f'{RUN_ID}-view-staging']}/f
sibling worktree|echo x > {w['wt-b']}/f
user checkout|echo x > {run['user']}/f
shared /tmp|echo x > /tmp/i5-probe-$$
another note|echo x > {rd}/notes/x.md
symlink escape|ln -s {rd}/state.json {wt}/ln && echo y >> {wt}/ln
hardlink escape|ln {rd}/state.json {wt}/hl
rename in|mv {rd}/state.json {wt}/
rename out|mv {wt}/app.py {rd}/stolen.py
""", run["env"])
    allowed = {"own worktree", "own commit", "own tmp", "own notes",
               "own cross-dir rename",
               "own cli config", "dev null", "shm"}
    assert {k for k, v in got.items() if v} == allowed, got
    assert (rd / "state.json").read_text() == "{}\n"


def test_the_runs_shared_caches_and_config_template(leerie, run):
    """The run's download caches are written by every worker; the CLI
    configuration template each worker's configuration links is read and
    never written; leerie's own stores are neither."""
    rd = run["run_dir"]
    (rd / "workers" / "cache").mkdir(parents=True, exist_ok=True)
    (rd / "claude-config").mkdir()
    (rd / "claude-config" / "settings.json").write_text("{}\n")
    stores = run["state_root"] / ".stores" / "leerie" / "pnpm-store"
    stores.mkdir(parents=True)
    g = _grants(leerie, run)
    got = _probe(leerie, g, f"""
cache write|echo x > {rd}/workers/cache/f
template read|cat {rd}/claude-config/settings.json
template write|echo x > {rd}/claude-config/settings.json
leerie store|echo x > {stores}/f
""", run["env"])
    assert got == {"cache write": True, "template read": True,
                   "template write": False, "leerie store": False}


def test_reads_inside_the_state_root_are_only_its_own(leerie, run):
    rd, w = run["run_dir"], run["wts"]
    g = _grants(leerie, run, notes_id="wt-a")
    got = _probe(leerie, g, f"""
held-out set|cat {rd}/acceptance/set-1/test_held.py
state.json|cat {rd}/state.json
sibling worktree|cat {w['wt-b']}/secret.py
sibling listing|ls {w['wt-b']}
own spec|cat {rd}/subtasks/wt-a.json
the task|cat {rd}/task.md
own tree|cat {w['wt-a']}/app.py
git log|git -C {w['wt-a']} log --oneline -1
user checkout|cat {run['user']}/app.py
proc root escape|cat /proc/$$/root{rd}/state.json
""", run["env"])
    assert {k for k, v in got.items() if v} == {
        "own spec", "the task", "own tree", "git log", "user checkout"}, got


def test_a_judge_in_the_planning_worktree_writes_nothing_of_the_run(
        leerie, run):
    planning = run["wts"]["staging"]
    g = leerie._worker_grants(
        run_dir=run["run_dir"], state={"planning_worktree": str(planning)},
        schema_key="planner", cwd=planning,
        allowed_tools=leerie.INSPECT_TOOLS, env=run["env"])
    got = _probe(leerie, g, f"""
its cwd|echo x > {planning}/f
read its cwd|cat {planning}/app.py
own tmp|echo x > $TMPDIR/f
subtask specs|cat {run['run_dir']}/subtasks/wt-a.json
""", run["env"])
    assert {k for k, v in got.items() if v} == {"read its cwd", "own tmp"}


def test_a_payload_only_worker_reads_nothing_of_the_run(leerie, run):
    payload = run["run_dir"] / "payload-only"
    payload.mkdir()
    g = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="pr_writer",
        cwd=payload, allowed_tools=leerie.PAYLOAD_ONLY_TOOLS, env=run["env"])
    got = _probe(leerie, g, f"""
its cwd|ls {payload}
run repo|ls {run['repo']}
staging|cat {run['wts']['staging']}/app.py
own tmp|echo x > $TMPDIR/f
""", run["env"])
    assert {k for k, v in got.items() if v} == {"its cwd", "run repo",
                                               "own tmp"}


def test_a_temp_dir_that_holds_the_state_root_never_grants_it(
        leerie, run, tmp_path):
    """`TMPDIR=/tmp` with the state root beneath it (pytest's layout, and a
    Python fallback) grants nothing — it is not beneath `workers/` —, and
    no grant ever covers the state root whole (`_around`): measured, a real
    `claude -p` wrote `state.json` through such a grant before."""
    env = dict(run["env"], TMPDIR=str(tmp_path))
    g = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS, env=env)
    root = run["state_root"].resolve()
    assert not [p for p in g.rw + g.ro
                if root.is_relative_to(Path(os.path.realpath(p)))]
    split = leerie._around([str(tmp_path)], root)
    assert str(tmp_path) not in split and str(tmp_path / "user") in split
    got = _probe(leerie, g, f"""
beside the root|echo x > {tmp_path}/user/f
state.json|echo x >> {run['run_dir']}/state.json
""", env)
    assert got == {"beside the root": False, "state.json": False}


# --- fail closed --------------------------------------------------------------

def test_preflight_stops_a_kernel_without_landlock(leerie, monkeypatch):
    monkeypatch.setattr(leerie._landlock(), "abi", lambda: 2)
    with pytest.raises(SystemExit):
        leerie._require_landlock()


def test_the_shim_never_runs_its_command_unconfined(tmp_path):
    marker = tmp_path / "ran"
    code = ("import sys; sys.path.insert(0, sys.argv[1]); "
            "import landlock_exec as m; m.abi = lambda: 2; "
            "sys.exit(m.main(sys.argv[2:]))")
    r = subprocess.run([sys.executable, "-c", code, str(SHIM.parent),
                        "--rw", str(tmp_path), "--", "touch", str(marker)],
                       capture_output=True, text=True)
    assert r.returncode == 126
    assert "cannot confine the worker" in r.stderr
    assert not marker.exists()


def test_invoke_cannot_be_called_without_grants(leerie, tmp_path):
    """`grants` is keyword-only with no default: a spawn path that forgets
    it does not run at all."""
    with pytest.raises(TypeError, match="grants"):
        asyncio.run(leerie._invoke(["true"], str(tmp_path), 10, "w",
                                   tmp_path, "quiet"))


def test_only_a_worker_dir_is_granted_from_the_environment(leerie, run):
    """A variable left at a shared default — `/tmp`, `~/.claude`, the
    image's mise data, `/opt` — grants nothing; one beneath
    `<run-dir>/workers/` grants that directory."""
    shared = {"TMPDIR": "/tmp", "CLAUDE_CONFIG_DIR": os.path.expanduser(
        "~/.claude"), "MISE_STATE_DIR": "/tmp/.mise-state",
        "XDG_CACHE_HOME": "/tmp/.cache", "BUNDLE_PATH": "/opt/bundle"}
    assert leerie._worker_own_dirs(shared, run["run_dir"]) == []
    assert leerie._worker_own_dirs(run["env"], run["run_dir"]) == [
        os.path.realpath(run["env"][v]) for v in leerie._WORKER_ENV_DIRS
        if v in run["env"]]


def test_a_tree_repository_is_granted_whole_and_the_run_repository_read(
        leerie, run):
    """With lane D's repository per committing tree (DESIGN §6 *A
    repository per committing tree*): the tree's own repository written,
    the run repository's objects read, nothing else of it."""
    rd = run["run_dir"]
    tr = rd / "tree-repos" / "t1"
    _git(rd.parent, "init", "-q", "--bare", str(tr))
    (tr / "objects" / "info" / "alternates").write_text(
        str(run["repo"] / "objects") + "\n")
    _git(tr, "fetch", "-q", str(run["repo"]), "+main:refs/heads/t1")
    wt = rd / "worktrees" / "t1"
    _git(tr, "worktree", "add", "-q", str(wt), "t1")
    g = _grants(leerie, run, cwd=wt)
    assert str(tr) in g.rw
    assert str(run["repo"] / "objects") in g.ro
    assert str(run["repo"]) not in g.ro + g.rw
    got = _probe(leerie, g, f"""
commit|cd {wt} && echo y > n.txt && git add n.txt && git -c user.email=t@x -c user.name=t commit -qm n
own branch|git -C {wt} rev-parse HEAD
run repo ref|git --git-dir={run['repo']} update-ref refs/heads/b-wt-b HEAD
run repo config|echo x >> {run['repo']}/config
""", run["env"])
    assert got == {"commit": True, "own branch": True, "run repo ref": False,
                   "run repo config": False}


# --- derived guards: every worker goes through the confined path -------------

def _tree() -> ast.Module:
    return ast.parse(LEERIE_PY.read_text())


def _enclosing(tree: ast.Module) -> dict[int, str]:
    out: dict[int, str] = {}
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for i in range(n.lineno, (n.end_lineno or n.lineno) + 1):
                out[i] = n.name
    return out


def _spans(tree: ast.Module, name: str) -> list[tuple[int, int]]:
    return [(n.lineno, n.end_lineno or n.lineno) for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == name]


def _within(line: int, spans: list[tuple[int, int]]) -> bool:
    return any(a <= line <= b for a, b in spans)


def test_every_claude_session_argv_comes_from_the_one_builder():
    """A `claude -p` argv literal exists only in `_contained_claude_argv`
    (every worker and the smoke test) and the option probe
    `_append_system_prompt_file_supported`, which starts no session (empty
    stdin, no prompt: the CLI parses its options and exits). The builder is
    called only by `claude_p` and `preflight`, both started by `_invoke`."""
    tree = _tree()
    owner = _enclosing(tree)
    sites = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.List) and n.elts and isinstance(
                n.elts[0], ast.Constant) and n.elts[0].value == "claude":
            words = [e.value for e in n.elts if isinstance(e, ast.Constant)]
            if "-p" in words:
                sites.add(owner.get(n.lineno))
    assert sites == {"_contained_claude_argv",
                     "_append_system_prompt_file_supported"}
    callers = [n.lineno for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_contained_claude_argv"]
    spans = _spans(tree, "claude_p") + _spans(tree, "preflight")
    assert callers and all(_within(ln, spans) for ln in callers)


def test_every_invoke_call_passes_grants():
    tree = _tree()
    # A function that defines its own `_invoke` (a closure over `claude_p`)
    # calls that, not the module's.
    shadowed = [(f.lineno, f.end_lineno or f.lineno) for f in ast.walk(tree)
                if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef))
                and any(isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef))
                        and c.name == "_invoke" and c is not f
                        for c in ast.walk(f))]
    calls = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id in ("_invoke", "_invoke_admitted")
             and not (n.func.id == "_invoke" and _within(n.lineno, shadowed))]
    assert calls
    assert [n.lineno for n in calls
            if not any(k.arg == "grants" for k in n.keywords)] == []
    admitted = [n.lineno for n in calls if n.func.id == "_invoke_admitted"]
    assert admitted and all(_within(ln, _spans(tree, "claude_p"))
                            for ln in admitted)
    direct = [n.lineno for n in calls if n.func.id == "_invoke"]
    assert all(_within(ln, _spans(tree, "_invoke_admitted")
                       + _spans(tree, "preflight")) for ln in direct)


def test_invoke_spawns_through_the_shim():
    src = LEERIE_PY.read_text()
    body = src[src.index("async def _invoke(cmd"):]
    body = body[:body.index("\nasync def ") if "\nasync def " in body
                else len(body)]
    spawn = body.index("asyncio.create_subprocess_exec(")
    assert body.index("_confined_argv(grants, list(cmd))") < spawn


def test_only_the_host_seam_runs_unconfined():
    """`grants=None` (unconfined) comes only from `claude_p` on caps the
    host seam marked, and only `_resolve_host_seam_settings` marks them."""
    tree = _tree()
    owner = _enclosing(tree)
    sets = [owner.get(n.lineno) for n in ast.walk(tree)
            if isinstance(n, ast.Assign) and any(
                isinstance(t, ast.Subscript) and isinstance(t.slice, ast.Name)
                and t.slice.id == "HOST_SEAM_CAP" for t in n.targets)]
    assert sets == ["_resolve_host_seam_settings"]
    reads = [n.lineno for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
             and n.func.attr == "get" and n.args
             and isinstance(n.args[0], ast.Name)
             and n.args[0].id == "HOST_SEAM_CAP"]
    assert reads and all(_within(ln, _spans(tree, "claude_p"))
                         for ln in reads)
    callers = {owner.get(n.lineno) for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
               and n.func.id == "_resolve_host_seam_settings"}
    assert callers == {"run_rebaser", "run_recapture_deps"}


def test_the_fly_integrator_is_confined():
    sh = (REPO_ROOT / "scripts" / "remote" / "collect-subtrees.sh").read_text()
    launches = [ln for ln in sh.splitlines()
                if re.search(r"\bclaude -p\b", ln.split("#", 1)[0])]
    assert launches and all('"${guard_confine[@]}" claude -p' in ln
                            for ln in launches)



def test_a_worker_with_a_shell_is_told_where_it_may_write(leerie, tmp_path):
    """The notice is documentation; the kernel is the guarantee. Told to
    every worker with bare `Bash`, not to one without a shell."""
    act = leerie._worker_guard(leerie.ACT_TOOLS, tmp_path, {},
                               worker="implementer")
    judge = leerie._worker_guard(leerie.INSPECT_TOOLS, tmp_path, {},
                                 worker="planner")
    assert leerie.CONFINED_WRITES_NOTICE in act["notices"]
    assert leerie.CONFINED_WRITES_NOTICE not in judge["notices"]


def test_the_staged_prompt_is_in_the_run_directory(leerie, tmp_path,
                                                    monkeypatch):
    """Never in the shared `/tmp`, where another worker could read or
    rewrite it before this one's CLI reads it."""
    seen: dict = {}

    async def stop(*argv, **kw):
        seen["stdin"] = kw["stdin"].name
        raise RuntimeError("stop at spawn")
    monkeypatch.setattr(leerie.asyncio, "create_subprocess_exec", stop)
    (tmp_path / "logs").mkdir()
    with pytest.raises(Exception):
        asyncio.run(leerie._invoke(["claude", "-p"], str(tmp_path), 10, "w",
                                   tmp_path, "quiet", stdin_data="prompt",
                                   grants=leerie.WorkerGrants(("/",), ())))
    assert Path(seen["stdin"]).parent == tmp_path / "stdin"
