"""The user's checkout is read-only in the local container (DESIGN §6 *The
explicit channel: the user's checkout is mounted read-only*).

The launcher mounts `/work:ro`; nothing in the container writes the user's
checkout, and what a run gives it reaches it through the host after the
container exits. Each writer that used to reach `/work` is tested where it
went: dependency capture (recorded in the run directory, applied by the
host), phase 1½'s setup hook and `mise` (a throwaway checkout), the status
reads (no optional locks), the prune and the abnormal-exit clean-up (no
fallback to the user's registry) and the rootful entry's `chown` (only on a
writable `/work`).

Real git and real bash throughout. The real-container probe runs only when
LEERIE_TEST_CONTAINER_IMAGE names a leerie image (`nerdctl` on PATH).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest

pytestmark = pytest.mark.real_planning_worktree

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
ENTRY = REPO_ROOT / "scripts" / "container-entry.sh"
HOST_FINALIZE = REPO_ROOT / "scripts" / "host-finalize.sh"
RUN_ID = "r0work0ro001"


def _git(cwd: Path, *args: str, check: bool = True) -> str:
    cwd = Path(cwd)
    at = (["--git-dir", str(cwd)]
          if (cwd / "HEAD").is_file() and (cwd / "objects").is_dir()
          else ["-C", str(cwd)])
    return subprocess.run(["git", *at, *args], check=check,
                          capture_output=True, text=True).stdout.strip()


def _user_repo(tmp_path: Path, files: dict[str, str] | None = None) -> Path:
    r = tmp_path / "user"
    r.mkdir()
    _git(r, "init", "-q", "-b", "main")
    _git(r, "config", "user.email", "t@x")
    _git(r, "config", "user.name", "t")
    for rel, text in (files or {"app.py": "x = 1\n"}).items():
        (r / rel).parent.mkdir(parents=True, exist_ok=True)
        (r / rel).write_text(text)
    _git(r, "add", "-A")
    _git(r, "commit", "-qm", "base")
    return r


class _St:
    def __init__(self, repo: Path, state_root: Path, run_id: str = RUN_ID):
        self.repo_root = repo
        self.run_id = run_id
        self.run_dir = state_root / "runs" / run_id
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.data: dict = {"working_branch": "main"}

    def save(self) -> None:
        pass

    def bump_workers(self, caps) -> None:
        self.data["worker_count"] = self.data.get("worker_count", 0) + 1


# ------------------------------------------------------------------ the mount

def _run_argv() -> str:
    src = LAUNCHER.read_text()
    start = src.index("  _run_argv=(\n")
    return src[start:src.index("\n  )\n", start)]


def test_the_checkout_is_mounted_read_only_and_state_lives_outside_it():
    argv = _run_argv()
    mounts = [ln.strip() for ln in argv.splitlines()
              if re.search(r':/work(:|")', ln) and not ln.strip().startswith("#")]
    assert mounts == ['-v "$USER_REPO:/work:ro"']
    # The in-checkout state-root fallback is unreachable in the container.
    assert "-e LEERIE_STATE_DIR=/leerie-state" in argv
    assert '-v "$LEERIE_STATE_HOST_DIR:/leerie-state"' in argv


def _bind_ro_supported() -> bool:
    if shutil.which("unshare") is None:
        return False
    r = subprocess.run(["unshare", "-rm", "true"], capture_output=True)
    return r.returncode == 0


_PROBE = r'''
set -u
mount --bind "$1" "$2" && mount -o remount,bind,ro "$2" || exit 97
cd "$3" || exit 96
w() { if "$@" >/dev/null 2>&1; then echo "$4 ok"; else echo "$4 refused"; fi; }
if ( : > "$2/new-file" ) 2>/dev/null; then echo "write ok"; else echo "write refused"; fi
if ( : > "$2/.git/new-file" ) 2>/dev/null; then echo "dotgit ok"; else echo "dotgit refused"; fi
if git -C "$2" -c user.name=w -c user.email=w@w commit -q --allow-empty -m w >/dev/null 2>&1; then echo "commit ok"; else echo "commit refused"; fi
if git -C "$2" branch worker-branch >/dev/null 2>&1; then echo "branch ok"; else echo "branch refused"; fi
if git -C "$2" rev-parse HEAD >/dev/null 2>&1; then echo "rev-parse ok"; else echo "rev-parse refused"; fi
if git -C "$2" status --porcelain >/dev/null 2>&1; then echo "status ok"; else echo "status refused"; fi
git init -q --bare "$3/run.git"
if git --git-dir "$3/run.git" fetch -q "$2" "+HEAD:refs/leerie/user-head" >/dev/null 2>&1; then echo "fetch-from ok"; else echo "fetch-from refused"; fi
'''


@pytest.mark.skipif(not _bind_ro_supported(),
                    reason="needs an unprivileged user+mount namespace")
def test_a_worker_style_write_to_a_read_only_checkout_fails(tmp_path):
    """A real read-only bind mount of a checkout, as the launcher makes:
    every write a worker could make there is refused, while every read the
    run makes of it works."""
    user = _user_repo(tmp_path)
    head = _git(user, "rev-parse", "HEAD")
    mnt = tmp_path / "work"
    mnt.mkdir()
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    r = subprocess.run(["unshare", "-rm", "bash", "-c", _PROBE, "_",
                        str(user), str(mnt), str(scratch)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    got = dict(ln.split(" ", 1) for ln in r.stdout.split("\n") if ln)
    assert got == {"write": "refused", "dotgit": "refused",
                   "commit": "refused", "branch": "refused",
                   "rev-parse": "ok", "status": "ok", "fetch-from": "ok"}
    assert _git(user, "rev-parse", "HEAD") == head
    assert not (user / "new-file").exists()
    assert "worker-branch" not in _git(user, "branch", "--list")


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_the_real_container_refuses_a_write_to_work(tmp_path):
    """The real `container-entry.sh` (ownership guard, privilege drop), with
    a stand-in for the orchestrator, over a checkout mounted as the
    launcher mounts it: as `leerie`, writes to `/work` fail with EROFS and
    the run's reads work; the checkout's owner is unchanged."""
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    opt = tmp_path / "opt"
    for d in ("scripts", "prompts", "orchestrator"):
        shutil.copytree(REPO_ROOT / d, opt / d)
    (opt / "orchestrator" / "leerie.py").write_text(
        "import json, os, subprocess\n"
        "def run(*a):\n"
        "    return subprocess.run(list(a), capture_output=True).returncode\n"
        "def write(p):\n"
        "    try:\n"
        "        open(p, 'w').write('x')\n"
        "        return 0\n"
        "    except OSError as e:\n"
        "        return e.errno\n"
        "print('PROBE ' + json.dumps({\n"
        "  'write': write('/work/w'), 'dotgit': write('/work/.git/w'),\n"
        "  'commit': run('git', '-C', '/work', '-c', 'user.name=p', '-c',\n"
        "                'user.email=p@p', 'commit', '-q', '--allow-empty',\n"
        "                '-m', 'p'),\n"
        "  'rev_parse': run('git', '-C', '/work', 'rev-parse', 'HEAD'),\n"
        "  'status': run('git', '-C', '/work', 'status', '--porcelain'),\n"
        "}))\n")
    user = _user_repo(tmp_path)
    for p in [tmp_path, *tmp_path.rglob("*")]:
        p.chmod(p.stat().st_mode | 0o055)
    owner = (user.stat().st_uid, user.stat().st_gid)
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    r = subprocess.run(
        ["nerdctl", "run", "--rm", *sec, "-v", f"{user}:/work:ro",
         "-v", f"{opt}:/opt/leerie-image:ro", "-w", "/work", image, "probe"],
        capture_output=True, text=True, timeout=300)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("PROBE ")]
    assert line, r.stdout + r.stderr
    got = json.loads(line[0][len("PROBE "):])
    assert got["write"] == 30 and got["dotgit"] == 30          # EROFS
    assert got["commit"] != 0
    assert got["rev_parse"] == 0 and got["status"] == 0
    assert (user.stat().st_uid, user.stat().st_gid) == owner


# --------------------------------------------------------- the entry's chown

def _work_is_read_only_fn(mounts: Path) -> str:
    src = ENTRY.read_text()
    start = src.index("_work_is_read_only() {")
    body = src[start:src.index("\n}\n", start) + 3]
    assert "/proc/self/mounts" in body
    return body.replace("/proc/self/mounts", str(mounts))


@pytest.mark.parametrize("line,ro", [
    ("tmpfs /work tmpfs ro,nosuid,nodev 0 0", True),
    ("/dev/vdb /work ext4 rw,relatime 0 0", False),
    ("overlay /work overlay rw,rootcontext=x,lowerdir=/a 0 0", False),
    ("tmpfs /workspace tmpfs ro 0 0", False),
])
def test_the_entry_reads_a_read_only_work_from_the_mount_table(
        tmp_path, line, ro):
    mounts = tmp_path / "mounts"
    mounts.write_text("proc /proc proc rw 0 0\n" + line + "\n")
    r = subprocess.run(["sh", "-c", _work_is_read_only_fn(mounts)
                        + "\n_work_is_read_only"], capture_output=True)
    assert (r.returncode == 0) is ro


def test_the_entry_chowns_work_only_when_it_is_writable():
    src = ENTRY.read_text()
    guard = src.index("if ! _work_is_read_only; then")
    chown = src.index("chown leerie: /work")
    assert guard < chown < src.index("fi", guard)
    assert src.count("chown leerie: /work") == 1


# ------------------------------------------------------ dependency capture

def _logs(run_dir: Path) -> None:
    (run_dir / "logs").mkdir(parents=True, exist_ok=True)
    (run_dir / "logs" / "w.log").write_text(json.dumps({"message": {
        "content": [{"type": "tool_use", "name": "Bash", "id": "t",
                     "input": {"command": "sudo apt-get install -y libpq-dev"}}]}})
        + "\n")


def test_capture_in_the_container_records_and_never_writes_the_checkout(
        leerie, tmp_path, monkeypatch):
    user = _user_repo(tmp_path, {"requirements.txt": "psycopg\n"})
    st = _St(user, tmp_path / "state")
    _logs(st.run_dir)
    monkeypatch.delenv("LEERIE_CAPTURE_DEPS", raising=False)

    async def fake(**_kw):
        return {"setup_packages": ["libpq-dev"], "language_installs": [
            {"manager": "pnpm", "command": "pnpm install --offline",
             "copy_inputs": ["package.json"]}], "dockerfile_notes": None}
    monkeypatch.setattr(leerie, "claude_p", fake)
    user.chmod(0o555)                       # as the read-only mount is
    try:
        asyncio.run(leerie.capture_repo_deps(
            user, st, caps={"max_total_workers": 9},
            models={"dep_capture": "sonnet"}, efforts={"dep_capture": None},
            record_in=st.run_dir))
    finally:
        user.chmod(0o755)
    assert not (user / ".leerie").exists()
    rec = json.loads((st.run_dir / "captured-deps.json").read_text())
    assert rec == [{"from_run": RUN_ID, "setup_packages": ["libpq-dev"],
                    "language_installs": [
                        {"manager": "pnpm", "command": "pnpm install --offline",
                         "copy_inputs": ["package.json"]}],
                    "replace": False}]
    assert (st.run_dir / "dep_capture.done").is_file()


def test_every_in_container_seam_records_into_the_current_run(leerie):
    """phase_finalize, the main() arms and the run-start backstop all pass
    `record_in`; only the host's recapture writes the checkout."""
    import inspect
    src = inspect.getsource(leerie)
    calls = [m.start() for m in re.finditer(r"await capture_repo_deps\(", src)]
    assert len(calls) == 4
    host = inspect.getsource(leerie.run_recapture_deps)
    for at in calls:
        call = src[at:src.index(")", src.index("efforts=efforts", at))]
        if call in host:
            assert "record_in" not in call
        else:
            assert "record_in=" in call, call


def test_apply_merges_against_the_users_current_config_once(leerie, tmp_path):
    user = _user_repo(tmp_path)
    run_dir = tmp_path / "state" / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    leerie._record_capture(run_dir, RUN_ID, ["libpq-dev", "curl"], [], False)
    # The user edits the key during the run; the apply merges with it.
    (user / ".leerie").mkdir()
    (user / ".leerie" / "config.toml").write_text(
        '# mine\nsetup_packages = "git curl"\n')
    assert leerie.apply_captured_deps(run_dir, user) == 1
    text = (user / ".leerie" / "config.toml").read_text()
    assert "# mine" in text
    assert leerie._read_toml_key(user / ".leerie" / "config.toml",
                                 "setup_packages") == "git curl libpq-dev"
    assert not (run_dir / "captured-deps.json").exists()
    assert len(json.loads((run_dir / "captured-deps.applied.json")
                          .read_text())) == 1
    assert leerie.apply_captured_deps(run_dir, user) == 0
    assert (user / ".leerie" / "config.toml").read_text() == text


def test_apply_keeps_a_file_it_cannot_read(leerie, tmp_path):
    user = _user_repo(tmp_path)
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "captured-deps.json").write_text("{not json")
    assert leerie.apply_captured_deps(run_dir, user) == 0
    assert (run_dir / "captured-deps.json").read_text() == "{not json"
    assert not (user / ".leerie").exists()


def _host_env(tmp_path: Path, user: Path, state: Path) -> dict:
    return {"PATH": os.environ["PATH"], "HOME": str(tmp_path),
            "USER_REPO": str(user), "LEERIE_REPO": str(REPO_ROOT),
            "LEERIE_STATE_HOST_DIR": str(state)}


def test_host_apply_writes_the_users_config(leerie, tmp_path):
    user = _user_repo(tmp_path)
    state = tmp_path / "state"
    run_dir = state / "runs" / RUN_ID
    run_dir.mkdir(parents=True)
    leerie._record_capture(run_dir, RUN_ID, ["libpq-dev"], [], False)
    r = subprocess.run(
        ["bash", "-c", '. "$1"; host_apply_captured_deps "$2"; echo rc=$?',
         "_", str(HOST_FINALIZE), str(run_dir)],
        env=_host_env(tmp_path, user, state), capture_output=True, text=True)
    assert "rc=0" in r.stdout, r.stderr
    assert leerie._read_toml_key(user / ".leerie" / "config.toml",
                                 "setup_packages") == "libpq-dev"
    assert not (run_dir / "captured-deps.json").exists()


def _launcher_functions() -> str:
    src = LAUNCHER.read_text()
    start = src.index("  _read_ran_run_id() {\n")
    fn = src.index("  _apply_ran_run_captures() {\n", start)
    return src[start:src.index("\n  }\n", fn) + len("\n  }\n")]


def test_the_launcher_exit_applies_the_ran_runs_capture(leerie, tmp_path):
    """The launcher's own functions, verbatim, from its EXIT trap: the run
    the container named in the run-id file has its capture applied; another
    run's is not."""
    user = _user_repo(tmp_path)
    state = tmp_path / "state"
    for rid, pkg in ((RUN_ID, "libpq-dev"), ("other-run", "zlib1g")):
        d = state / "runs" / rid
        d.mkdir(parents=True)
        leerie._record_capture(d, rid, [pkg], [], False)
    rid_file = state / ".runid-test"
    rid_file.write_text(RUN_ID + "\n")
    exit_trap = [ln for ln in LAUNCHER.read_text().splitlines()
                 if ln.strip().startswith("trap '") and ln.endswith("' EXIT")
                 and "_kill_container_from_cidfile" in ln]
    assert len(exit_trap) == 1 and "_apply_ran_run_captures" in exit_trap[0]
    assert exit_trap[0].index("_apply_ran_run_captures") < exit_trap[0].index(
        'rm -f "${_cidfile:-}" "${_run_id_file:-}"')
    r = subprocess.run(
        ["bash", "-c", "set -euo pipefail\nRUNTIME=local\n"
         f'_run_id_file="{rid_file}"\n' + _launcher_functions()
         + "_apply_ran_run_captures\n"],
        env=_host_env(tmp_path, user, state), capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    assert leerie._read_toml_key(user / ".leerie" / "config.toml",
                                 "setup_packages") == "libpq-dev"
    assert (state / "runs" / "other-run" / "captured-deps.json").is_file()


# ------------------------------------------------------------- phase 1½

def test_the_setup_hook_and_mise_run_outside_the_checkout(
        leerie, tmp_path, monkeypatch):
    """phase_provision runs the repo's `.leerie-setup.sh` and `mise` in a
    throwaway checkout of the planning commit; the user's checkout gets
    nothing, and the checkout is gone afterwards."""
    user = _user_repo(tmp_path, {
        "app.py": "x = 1\n",
        ".leerie-setup.sh": 'pwd > "$OUT"; echo made > hook-made.txt\n'})
    state = tmp_path / "state"
    monkeypatch.chdir(user)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    out = tmp_path / "hook-cwd"
    monkeypatch.setenv("OUT", str(out))
    st = _St(user, state)
    st.data["categories"] = ["bug-fixing"]
    asyncio.run(leerie._ensure_planning_worktree(st))
    seen: dict = {}

    async def mise(tree, log_dir, st_, override_file=None):
        seen["mise"] = Path(tree)
        seen["made"] = (Path(tree) / "hook-made.txt").is_file()

    class Stop(Exception):
        pass

    def stop(_root):
        raise Stop
    monkeypatch.setattr(leerie, "_run_mise_install", mise)
    monkeypatch.setattr(leerie, "_detect_recipe_from_lockfiles", stop)
    with pytest.raises(Stop):
        asyncio.run(leerie.phase_provision(user, st, {}, {}, {}))
    hook_cwd = Path(out.read_text().strip())
    assert hook_cwd == seen["mise"] and seen["made"]
    assert st.run_dir.resolve() in hook_cwd.resolve().parents
    assert not hook_cwd.exists()
    assert not (user / "hook-made.txt").exists()
    assert _git(user, "status", "--porcelain") == ""


# ------------------------------------------------------- status reads

def test_the_checkout_snapshot_never_rewrites_the_users_index(
        leerie, tmp_path):
    """A tracked file whose mtime moved makes a plain `git status` refresh
    and rewrite the index; the sentinel's snapshot leaves it untouched."""
    user = _user_repo(tmp_path)
    index = user / ".git" / "index"
    os.utime(user / "app.py", (1_000_000_000, 1_000_000_000))
    os.utime(index, (1_000_000_100, 1_000_000_100))
    before = (index.read_bytes(), index.stat().st_mtime_ns)
    snap = asyncio.run(leerie._snapshot_repo_state(str(user)))
    assert snap["ok"] and snap["porcelain"] == []
    assert (index.read_bytes(), index.stat().st_mtime_ns) == before
    # The control: a plain status does rewrite it.
    subprocess.run(["git", "-C", str(user), "status", "--porcelain"],
                   check=True, capture_output=True)
    assert index.stat().st_mtime_ns != before[1]


# ------------------------------------------- prune and abnormal-exit cleanup

def _registered(repo: Path) -> set[str]:
    return {ln[len("worktree "):] for ln in
            _git(repo, "worktree", "list", "--porcelain").splitlines()
            if ln.startswith("worktree ")}


def _stale_registration(user: Path, run_dir: Path) -> Path:
    wt = run_dir / "worktrees" / "s1"
    wt.parent.mkdir(parents=True)
    _git(user, "worktree", "add", "-q", "-b", "leerie/subtasks/x/s1", str(wt))
    return wt


def test_no_run_repository_means_no_write_to_the_cwds_git(
        leerie, tmp_path, monkeypatch):
    """A run with no repository of its own (made before per-run
    repositories, or dead before making one): the prune and the abnormal-exit
    clean-up remove the run's directories and touch nothing in the cwd's
    repository — the user's checkout in the container."""
    user = _user_repo(tmp_path)
    monkeypatch.chdir(user)
    st = _St(user, tmp_path / "state")
    wt = _stale_registration(user, st.run_dir)
    shutil.rmtree(wt)
    listed = _git(user, "worktree", "list", "--porcelain")
    leerie._prune_leerie_worktrees(st.run_dir)
    assert _git(user, "worktree", "list", "--porcelain") == listed
    assert not (user / ".git" / "leerie-worktree.lock").exists()

    live = st.run_dir / "worktrees" / "s2"
    _git(user, "worktree", "add", "-q", "-b", "leerie/subtasks/x/s2", str(live))
    registered = _registered(user)
    leerie._cleanup_on_abnormal_exit(st, full_purge=False)
    assert not live.exists()
    # Still registered (now prunable), for the host's `leerie prune`.
    assert _registered(user) == registered and str(live) in registered
    assert not (user / ".git" / "leerie-worktree.lock").exists()
