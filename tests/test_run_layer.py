"""The setup hook writes the run layer, never the host's toolchains (DESIGN §6
*The setup hook writes the run layer, never the host's toolchains*).

Until 2026-11-14 the setup-hook kind was granted `MISE_DATA_DIR` — the host's
`~/.cache/leerie/toolchains/mise`, which every run of every repository on the
host executes from — and the container's real `~/.local/bin`. Measured on both
runtimes through real launches (lane HC2, `CF/W7`): a fixture hook wrote into
`mise/`, `mise/installs/` and `mise/shims/` (on `PATH` ahead of `/usr/bin`),
and the files outlived the run. Now the hook writes `<run-dir>/run-layer.new`
through overlays the shim mounts over `MISE_DATA_DIR` and its home; the layer
is published as `<run-dir>/run-layer` after the hook exits 0, and every later
confined process of the run but the toolchain install reads it.

The grant-shape tests run anywhere. The real-path test builds the overlays,
which needs a user namespace this host's AppArmor may refuse, so it runs in a
leerie container (LEERIE_TEST_CONTAINER_IMAGE, as
`tests/test_confined_home.py` does) and drives leerie's real
`_require_confinement`, `_run_setup_hook`, `_measurement_grants`,
`_worker_grants` and `_run_streaming`.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from tests.conftest import (  # noqa: F401
    REAL_HOME, XDG_BASE_DIRS, leerie_container_drop)
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    _St, pytestmark, run)

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _st(run) -> _St:
    return _St(run["user"], run["run_dir"])


def _view(run) -> Path:
    return run["wts"][f"{run['run_dir'].name}-view-staging"]


@pytest.fixture()
def homes(leerie, tmp_path, monkeypatch) -> tuple[Path, Path, Path]:
    """A home, a toolchain home layer and a `MISE_DATA_DIR` beside them, with
    `/tmp` named elsewhere (the session's paths lie beneath it, and
    `_confined_home` answers "" for a home there)."""
    h, layer, mise = tmp_path / "home", tmp_path / "layer", tmp_path / "mise"
    for d in (h, layer, mise):
        d.mkdir()
    monkeypatch.setattr(leerie, "SHARED_TMP", tmp_path / "no-shared-tmp")
    monkeypatch.setenv("HOME", str(h))
    monkeypatch.setenv("MISE_DATA_DIR", str(mise))
    monkeypatch.setenv("LEERIE_TOOLCHAIN_HOME", str(layer))
    return h, layer, mise


def _home(g):
    """The grant's home overlay (`Overlay.home`)."""
    return next(o for o in g.overlays if o.home)


def _layers(g) -> tuple:
    """The grant's read-only overlays, as `(target, lower)` pairs."""
    return tuple((o.target, *o.lowers) for o in g.overlays
                 if not o.home and not o.upper)


def _uppers(g) -> tuple:
    """The grant's other overlays with an upper, as `(target, upper)`."""
    return tuple((o.target, o.upper) for o in g.overlays
                 if not o.home and o.upper)


def _publish(run_dir: Path) -> Path:
    layer = run_dir / "run-layer"
    (layer / "home").mkdir(parents=True)
    (layer / "mise").mkdir()
    return layer


# ------------------------------------------------------------ grant shape


def test_the_hook_writes_the_new_layer_and_names_no_host_directory(
        leerie, run, homes):
    h, layer, mise = homes
    g = leerie._measurement_grants(_st(run), _view(run),
                                   leerie.SETUP_HOOK_KIND)
    new = run["run_dir"].resolve() / leerie.RUN_LAYER_NEW_NAME
    assert _home(g).upper == str(new / "home")
    assert _uppers(g) == ((str(mise.resolve()), str(new / "mise")),)
    for d in (new / "home", new / "mise"):
        assert d.is_dir() and leerie._home_work(d).is_dir()
    assert str(mise.resolve()) not in g.rw
    assert str((h / ".local" / "bin").resolve()) not in g.rw
    # Its own cache travels with its data, now its overlay.
    assert dict(g.env)["MISE_CACHE_DIR"] == str(mise.resolve() / "cache")
    assert _layers(g) == () and _home(g).lowers == (str(layer.resolve()),)


def test_no_process_reads_a_layer_until_it_is_published(leerie, run, homes):
    h, layer, mise = homes
    leerie._measurement_grants(_st(run), _view(run), leerie.SETUP_HOOK_KIND)
    m = leerie._measurement_grants(_st(run), _view(run))
    assert _layers(m) == () and _home(m).lowers == (str(layer.resolve()),)
    w = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS,
        env={**run["env"], "HOME": str(h)})
    assert _layers(w) == () and _home(w).lowers == (str(layer.resolve()),)


@pytest.mark.parametrize("kind", ["MEASURE_KIND", "INSTALL_KIND"])
def test_every_later_kind_reads_the_published_layer(leerie, run, homes,
                                                    kind):
    h, layer, mise = homes
    rl = _publish(run["run_dir"])
    g = leerie._measurement_grants(_st(run), _view(run),
                                   getattr(leerie, kind))
    assert _layers(g) == ((str(mise.resolve()), str(rl / "mise")),)
    # Beneath any tree home layer, above the toolchain home layer.
    assert _home(g).lowers[-2:] == (str(rl / "home"), str(layer.resolve()))
    assert _uppers(g) == ()


def test_a_worker_reads_the_published_layer(leerie, run, homes):
    h, layer, mise = homes
    rl = _publish(run["run_dir"])
    w = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS,
        env={**run["env"], "HOME": str(h)})
    assert _layers(w) == ((str(mise.resolve()), str(rl / "mise")),)
    assert _home(w).lowers == (str(rl / "home"), str(layer.resolve()))
    assert _uppers(w) == ()


def test_the_toolchain_install_reads_none_of_it(leerie, run, homes):
    h, layer, mise = homes
    _publish(run["run_dir"])
    t = leerie._measurement_grants(_st(run), _view(run),
                                   leerie.TOOLCHAIN_KIND)
    assert _layers(t) == () and _uppers(t) == () and _home(t).lowers == ()


def test_the_shim_is_asked_for_the_layers_only_when_isolated(
        leerie, run, homes, monkeypatch):
    h, layer, mise = homes
    rl = _publish(run["run_dir"])
    m = leerie._measurement_grants(_st(run), _view(run))
    hk = leerie._measurement_grants(_st(run), _view(run),
                                    leerie.SETUP_HOOK_KIND)
    monkeypatch.delenv(leerie.ISOLATE_ENV, raising=False)
    assert "--overlay" not in leerie._confined_argv(m, ["true"])
    assert "--overlay" not in leerie._confined_argv(hk, ["true"])
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    argv = leerie._confined_argv(m, ["true"])
    i = argv.index("--overlay")
    assert argv[i + 1:i + 4] == [str(mise.resolve()), "--lower",
                                 str(rl / "mise")]
    # After the home: its directory may lie beneath it.
    assert argv.index("--home") < i < argv.index("--")
    argv = leerie._confined_argv(hk, ["true"])
    new = run["run_dir"].resolve() / leerie.RUN_LAYER_NEW_NAME
    i = argv.index("--overlay")
    assert argv[i + 1:i + 4] == [str(mise.resolve()), "--upper",
                                 str(new / "mise")]


def test_no_tree_may_hold_the_run_layer(leerie):
    assert {"run-layer", "run-layer.new"} <= set(leerie._MEASURE_PRIVATE)


# ------------------------------------------- leerie's own view of the run


def _exe(p: Path) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("#!/bin/sh\n")
    p.chmod(0o755)


def test_run_view_reads_through_the_layer(leerie, homes, tmp_path):
    h, layer, mise = homes
    run_dir = tmp_path / "run"
    rl = _publish(run_dir)
    _exe(h / ".local" / "bin" / "lower")
    _exe(rl / "home" / ".local" / "bin" / "tool")
    _exe(rl / "mise" / "installs" / "rt" / "1" / "bin" / "rt")
    bin_ = str(h.resolve() / ".local" / "bin")
    rt = str(mise.resolve() / "installs" / "rt" / "1" / "bin")
    path = os.pathsep.join([bin_, rt])
    assert leerie._run_which("tool", run_dir, path) == f"{bin_}/tool"
    assert leerie._run_which("rt", run_dir, path) == f"{rt}/rt"
    # The lower still shows where the layer has nothing.
    assert leerie._run_which("lower", run_dir, path) == f"{bin_}/lower"
    # Without the run, nothing the hook wrote is seen.
    assert leerie._run_which("tool", None, path) is None
    # Unpublished, neither.
    os.rename(rl, run_dir / "run-layer.new")
    assert leerie._run_which("tool", run_dir, path) is None


def test_run_view_hides_what_an_opaque_directory_covers(leerie, homes,
                                                        tmp_path):
    h, layer, mise = homes
    run_dir = tmp_path / "run"
    rl = _publish(run_dir)
    _exe(h / ".local" / "bin" / "lower")
    upper_bin = rl / "home" / ".local" / "bin"
    upper_bin.mkdir(parents=True)
    try:
        os.setxattr(upper_bin, "user.overlay.opaque", b"y")
    except OSError as e:
        pytest.skip(f"no user xattrs here: {e}")
    bin_ = str(h.resolve() / ".local" / "bin")
    assert leerie._run_which("lower", run_dir, bin_) is None
    # A file in the layer where the lower has a directory hides it too.
    _exe(h / "pkg" / "bin" / "x")
    (rl / "home" / "pkg").write_text("not a directory\n")
    assert leerie._run_which("x", run_dir, str(h.resolve() / "pkg" /
                                               "bin")) is None


def test_a_recipe_program_the_hook_installed_counts(leerie, homes,
                                                    tmp_path):
    h, layer, mise = homes
    run_dir = tmp_path / "run"
    rl = _publish(run_dir)
    _exe(rl / "home" / ".local" / "bin" / "hooktool")
    repo = tmp_path / "repo"
    repo.mkdir()
    recipe = [{"kind": "install", "command": ["hooktool", "sync"],
               "working_dir": "."}]
    path = str(h.resolve() / ".local" / "bin")
    assert leerie._recipe_program_issues(recipe, repo, [], path,
                                         run_dir) == []
    issues = leerie._recipe_program_issues(recipe, repo, [], path)
    assert len(issues) == 1 and issues[0].startswith("UNKNOWN_PROGRAM")


# ------------------------------------------------------------ shim options


def _parse(argv):
    sys.path.insert(0, str(SHIM.parent))
    try:
        import landlock_exec  # noqa: PLC0415
    finally:
        sys.path.pop(0)
    return landlock_exec._parse(argv)


def test_the_shim_parses_the_overlay_options():
    o = _parse(["--isolate", "--home", "/h", "--lower", "/l", "--upper",
                "tmpfs:9", "--overlay", "/a", "--lower", "/b/", "--overlay",
                "/c", "--upper", "/d", "--", "true"])
    assert o["overlays"] == [
        {"target": "/h", "lowers": ["/l"], "upper": 9, "home": True},
        {"target": "/a", "lowers": ["/b"], "upper": None, "home": False},
        {"target": "/c", "lowers": [], "upper": "/d", "home": False}]
    assert "need --isolate or --sandbox" in _parse(
        ["--overlay", "/a", "--lower", "/b", "--", "true"])
    assert "an --upper on disk needs --isolate" in _parse(
        ["--sandbox", "--view", "/v", "--overlay", "/a", "--upper", "/b",
         "--", "true"])
    assert "one --home, with its --upper" in _parse(
        ["--isolate", "--home", "/h", "--", "true"])
    assert "one --home, with its --upper" in _parse(
        ["--isolate", "--home", "/h", "--upper", "tmpfs:9", "--home", "/i",
         "--upper", "tmpfs:9", "--", "true"])
    assert "the --home's only" in _parse(
        ["--isolate", "--overlay", "/a", "--upper", "tmpfs:9", "--", "true"])
    assert "follows no overlay" in _parse(
        ["--isolate", "--lower", "/b", "--overlay", "/a", "--", "true"])
    assert "follows no overlay" in _parse(
        ["--isolate", "--overlay", "/a", "--upper", "/b", "--upper", "/c",
         "--", "true"])
    for k, bad in (("--overlay", "a"), ("--lower", "/"), ("--upper", "b"),
                   ("--upper", "tmpfs:0"), ("--upper", "tmpfs:x")):
        assert f"bad {k}" in _parse(["--isolate", "--overlay", "/a", k, bad,
                                     "--", "true"])


def test_the_profile_allows_the_overlay_on_the_toolchains():
    rules = {ln.strip() for ln in (REPO_ROOT / "scripts" / "apparmor" /
                                   "leerie-container").read_text()
             .splitlines()}
    assert "mount fstype=overlay overlay -> /leerie-toolchains/mise/," in \
        rules


# ------------------------------------------------------ the real container

_DRIVER = r'''
import asyncio, importlib.util, json, os, subprocess, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
leerie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leerie)
RUN = "runlayer0"
HOME = Path(os.environ["HOME"])
MISE = Path(os.environ["MISE_DATA_DIR"])


class _St:
    def __init__(s, user, run_dir):
        s.repo_root, s.run_id, s.run_dir = user, RUN, run_dir
        s.data = {"working_branch": "main", "verbosity": "quiet"}
        s.path = run_dir / "state.json"

    def save(s):
        pass


base = Path(sys.argv[2])
user = base / "user"
user.mkdir(parents=True)


def g(*a, cwd=user):
    return subprocess.run(["git", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


g("init", "-q", "-b", "main")
(user / ".leerie-setup.sh").write_text(
    "#!/bin/bash\nset -eu\n"
    "mkdir -p \"$MISE_DATA_DIR/installs/hookrt/1/bin\" \"$MISE_DATA_DIR/shims\""
    " \"$HOME/.local/bin\"\n"
    "printf '#!/bin/sh\\necho HOOKRT\\n' > \"$MISE_DATA_DIR/installs/hookrt/1/bin/hookrt\"\n"
    "chmod +x \"$MISE_DATA_DIR/installs/hookrt/1/bin/hookrt\"\n"
    "echo planted > \"$MISE_DATA_DIR/shims/planted\"\n"
    "echo inst > \"$MISE_DATA_DIR/installs/marker\"\n"
    "printf '#!/bin/sh\\necho HOOKTOOL\\n' > \"$HOME/.local/bin/hooktool\"\n"
    "chmod +x \"$HOME/.local/bin/hooktool\"\n"
    "echo rc > \"$HOME/.hookrc\"\n"
    "cat \"$MISE_DATA_DIR/installs/hostrt\"\n"
    "echo \"HOOKCACHE=$MISE_CACHE_DIR\"\n")
g("add", "-A")
g("-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "base")
state_root = base / "state"
run_dir = state_root / "runs" / RUN
run_dir.mkdir(parents=True)
st = _St(user, run_dir)
repo = leerie._ensure_run_repo(st)
wt = run_dir / "worktrees" / f"{RUN}-view"
g("worktree", "add", "-q", str(wt), "main", cwd=repo)
chk = run_dir / "hook-checkout"
g("worktree", "add", "-q", "--detach", str(chk), "main", cwd=repo)
leerie._require_confinement(run_dir, state_root, user)
out = {"isolated": leerie._isolated()}
(MISE / "installs").mkdir(parents=True, exist_ok=True)
(MISE / "installs" / "hostrt").write_text("HOSTRT\n")


def tree(root):
    return sorted(str(p.relative_to(root)) for p in root.rglob("*"))


host_before = tree(MISE)
home_before = tree(HOME)
asyncio.run(leerie._run_setup_hook(chk, run_dir / "logs", st))
out["hook_log"] = (run_dir / "logs" / "setup-hook.log").read_text()[-2000:]
out["host_unchanged"] = tree(MISE) == host_before
out["home_unchanged"] = tree(HOME) == home_before
rl = run_dir / "run-layer"
out["layer"] = tree(rl) if rl.is_dir() else None
out["new_left"] = (run_dir / "run-layer.new").exists()


def cmd(script, kind=leerie.MEASURE_KIND):
    return asyncio.run(leerie._run_streaming(
        ["bash", "-c", script],
        grants=leerie._measurement_grants(st, wt, kind), cwd=str(wt),
        timeout=120, verbosity="quiet", tail_lines=40))


READ = ('hookrt_path=$MISE_DATA_DIR/installs/hookrt/1/bin; '
        '"$hookrt_path/hookrt"; PATH="$PATH:$HOME/.local/bin" hooktool; '
        'cat ~/.hookrc; cat $MISE_DATA_DIR/installs/hostrt; '
        'ls $MISE_DATA_DIR/shims; '
        '( echo x > $MISE_DATA_DIR/installs/w ) 2>&1 | sed s/^/MISEW:/; '
        'echo mine > ~/.measure-own; echo done')
rc, tail = cmd(READ)
out["measure"] = {"rc": rc, "tail": tail}
out["layer_after_measure"] = tree(rl)
# A worker of the run, through its own grant and the real shim, in a
# directory of its own (a run tree's worker works in a tree repository, I6).
wcwd = base / "worker-cwd"
wcwd.mkdir()
wg = leerie._worker_grants(run_dir=run_dir, state={},
                           schema_key="implementer", cwd=wcwd,
                           allowed_tools=leerie.ACT_TOOLS,
                           env=dict(os.environ))
r = subprocess.run(leerie._confined_argv(wg, ["bash", "-c", READ]),
                   cwd=str(wcwd), capture_output=True, text=True, timeout=120,
                   env=leerie._grant_env(dict(os.environ), wg))
out["worker"] = {"rc": r.returncode, "out": r.stdout + r.stderr}
# The toolchain install sees the host's alone.
rc, tail = cmd('test -e $MISE_DATA_DIR/installs/hookrt && echo SEEN || '
               'echo ABSENT; test -e ~/.hookrc && echo RC || echo NORC',
               kind=leerie.TOOLCHAIN_KIND)
out["toolchain"] = {"rc": rc, "tail": tail}
# Leerie's own check sees what the hook installed.
out["recipe_issues"] = leerie._recipe_program_issues(
    [{"kind": "install", "command": ["hooktool"], "working_dir": "."}],
    wt, [], f"{os.environ['PATH']}:{HOME}/.local/bin", run_dir)
out["host_unchanged_at_end"] = tree(MISE) == host_before
print("RESULT " + json.dumps(out), flush=True)
'''


def _users_env() -> dict[str, str]:
    env = {**os.environ, "HOME": REAL_HOME}
    for var in XDG_BASE_DIRS:
        real = os.environ.get(f"PYTEST_LEERIE_REAL_{var}", "")
        if real:
            env[var] = real
        else:
            env.pop(var, None)
    return env


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_the_real_container_keeps_the_hooks_writes_in_its_run(tmp_path):
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    (tmp_path / "driver.py").write_text(_DRIVER)
    for p in (tmp_path, tmp_path / "driver.py"):     # read as `leerie`
        p.chmod(p.stat().st_mode | 0o055)
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    profile = os.environ.get("LEERIE_TEST_APPARMOR_PROFILE",
                             "leerie-container")
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", f"apparmor={profile}"]
    drop, owner = leerie_container_drop()
    tmpfs = f"rw,exec,size=1g,{owner}"
    r = subprocess.run(
        ["nerdctl", "run", "--rm", *sec, "--user", "0",
         "--entrypoint", "bash",
         "-v", f"{REPO_ROOT}:/src:ro", "-v", f"{tmp_path}:/out:ro",
         "--tmpfs", f"/leerie-state:{tmpfs}",
         image, "-c",
         f"{drop} env HOME=/home/leerie "
         "LEERIE_TOOLCHAIN_HOME=/leerie-toolchains/home "
         "MISE_DATA_DIR=/leerie-toolchains/mise "
         "/usr/bin/python3 /out/driver.py "
         "/src/orchestrator/leerie.py /leerie-state/w"],
        capture_output=True, text=True, timeout=600, env=_users_env())
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, r.stdout[-4000:] + r.stderr[-4000:]
    got = json.loads(line[0][len("RESULT "):])
    print("RESULT", json.dumps(got))
    assert got["isolated"] is True, got
    # The hook read the host's toolchains and wrote none of them, nor the
    # real home; its writes are the run layer's, published whole.
    assert "HOSTRT" in got["hook_log"], got
    assert "HOOKCACHE=/leerie-toolchains/mise/cache" in got["hook_log"], got
    assert got["host_unchanged"] and got["home_unchanged"], got
    layer = got["layer"]
    for p in ("mise/installs/hookrt/1/bin/hookrt", "mise/shims/planted",
              "mise/installs/marker", "home/.local/bin/hooktool",
              "home/.hookrc"):
        assert p in layer, (p, got)
    assert got["new_left"] is False, got
    # Every later process of the run reads it at the path the hook wrote,
    # writes none of it, and keeps its own writes to itself.
    for who, rc, text in (("measure", got["measure"]["rc"],
                           got["measure"]["tail"]),
                          ("worker", got["worker"]["rc"],
                           got["worker"]["out"])):
        assert rc == 0, (who, got)
        for want in ("HOOKRT", "HOOKTOOL", "rc", "HOSTRT", "planted",
                     "done"):
            assert want in text, (who, want, got)
        assert "MISEW:" in text and "Read-only file system" in text, \
            (who, got)
    assert got["layer_after_measure"] == layer, got
    # The toolchain install sees the host's toolchains alone.
    t = got["toolchain"]
    assert t["rc"] == 0 and "ABSENT" in t["tail"] and "NORC" in t["tail"], \
        got
    assert got["recipe_issues"] == [], got
    assert got["host_unchanged_at_end"], got
