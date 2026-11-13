"""The repository's root is only a confined process's (DESIGN §6 *The
orchestrator's root is leerie's; a repository's root is only a confined
process's*; review round 28, isolation E1).

A repository's image stages are copied to `/opt/leerie-worker-root` in an
image whose own root is leerie's. The shim (`scripts/landlock_exec.py
--worker-root`) builds the worker view from it and `chroot`s a confined
command there only after the command is confined; a worker's CLI
(`--cli`) stays in leerie's root and `worker-shell.py` puts each of its
commands in the view.

The isolation tests build a small worker root here — a shell, `cat`, `env`
and the libraries they load, copied from this root — with a mark only the
worker root has, an environment of its own, and a booby trap: a
`/etc/ld.so.preload` naming a library that does not exist, which the
dynamic loader complains about in every process that starts in that root.
A process of leerie's (the shim, the CLI) that started there would print
it. They need a runtime that can build the isolation (the container under
the `leerie-container` profile); a host that restricts user namespaces
skips them, as `tests/test_landlock_isolation.py` does. The unit tests run
everywhere."""
from __future__ import annotations

import ast
import importlib.util
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"
WORKER_SHELL = REPO_ROOT / "scripts" / "worker-shell.py"


def _shim_module():
    spec = importlib.util.spec_from_file_location("_ll_view", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


LL = _shim_module()
TRAP = "/nonexistent/leerie-trap.so"


# --- the worker stage's environment (unit) ---------------------------------


def _env_file(path: Path, env: dict[str, str]) -> None:
    path.write_bytes(b"".join(f"{k}={v}".encode() + b"\0"
                              for k, v in env.items()))


def test_worker_env_changes_are_what_the_stages_changed(tmp_path):
    _env_file(tmp_path / LL.BASE_ENV_FILE,
              {"PATH": "/usr/bin:/bin", "MISE_X": "1", "SAME": "s"})
    _env_file(tmp_path / LL.WORKER_ENV_FILE,
              {"PATH": "/opt/venv/bin:/usr/bin:/bin:/x/bin", "MISE_X": "1",
               "SAME": "s", "VIRTUAL_ENV": "/opt/venv", "JAVA_HOME": "/j",
               "HOME": "/root", "TMPDIR": "/t", "LEERIE_ISOLATED": "0",
               "CLAUDE_CODE_SHELL_PREFIX": "/x", "ANTHROPIC_BASE_URL": "u",
               "HTTPS_PROXY": "http://evil", "NO_PROXY": "*"})
    changed, added = LL.worker_env_changes(str(tmp_path))
    assert changed == {"VIRTUAL_ENV": "/opt/venv", "JAVA_HOME": "/j"}
    assert added == ["/opt/venv/bin", "/x/bin"]


def test_apply_worker_env_keeps_every_value_the_caller_set(tmp_path):
    """A worker stage's variable is taken only when the confined command's
    environment does not set it: a store variable leerie set (the run's
    `BUNDLE_PATH`) is kept even when the stage points it beneath `/opt/`,
    where an image install would sit read-only, so a subtask that adds a
    dependency installs it into the run's store (DESIGN §6½ *The repository
    image holds no dependency*). A variable the caller did not set is still
    taken, and the stage's `PATH` entries still go first. Falsified against
    0.65.0's shim: `BUNDLE_PATH` became `/opt/bundle` (the `_BAKED_ROOT`
    carve-out)."""
    store = "/leerie-state/runs/r/worktrees/.stores/tree-stores/t/bundle"
    env = {"PATH": "/a:/b", "JAVA_HOME": "/mine", "BUNDLE_PATH": store}
    LL.apply_worker_env(env, ({"JAVA_HOME": "/theirs",
                               "BUNDLE_PATH": "/opt/bundle", "NEW": "1"},
                              ["/opt/venv/bin", "/b"]))
    assert env == {"PATH": "/opt/venv/bin:/b:/a",
                   "JAVA_HOME": "/mine",          # the caller set it
                   "BUNDLE_PATH": store,          # the run's store
                   "NEW": "1"}                    # the caller did not


def test_env_file_is_read_without_following_a_link(tmp_path):
    real = tmp_path / "real"
    _env_file(real, {"A": "1"})
    (tmp_path / LL.WORKER_ENV_FILE).symlink_to(real)
    assert LL._read_env_file(str(tmp_path / LL.WORKER_ENV_FILE)) == {}
    assert LL._read_env_file(str(real)) == {"A": "1"}


@pytest.mark.parametrize("argv, err", [
    (["--worker-root", "/w", "--", "true"], "needs --isolate or --sandbox"),
    (["--isolate", "--cli", "--", "true"], "--cli needs"),
    (["--sandbox", "--view", "/v", "--worker-root", "/w", "--cli", "--",
      "true"], "--cli needs"),
    (["--isolate", "--worker-root", "rel", "--", "true"], "bad --worker-root"),
    (["--isolate", "--worker-root", "/", "--", "true"], "bad --worker-root"),
])
def test_parse_refuses_a_worker_root_it_cannot_use(argv, err):
    assert err in LL._parse(argv)


def test_parse_takes_a_worker_root():
    o = LL._parse(["--isolate", "--worker-root", "/opt/r/", "--cli", "--",
                   "c"])
    assert o["worker_root"] == "/opt/r" and o["cli"] is True


def test_the_shim_enters_the_view_only_in_the_child_it_execs():
    """The shim itself never runs in the worker root: `chroot` happens in
    `enter_worker_view` only, which `_before_exec` calls, which only the
    forked children of `run` and `run_as_init` call, just before `exec` —
    after `_confine_or_126` in `main` and `run_sandbox` (DESIGN: *Confined
    first, then the repository's root*)."""
    tree = ast.parse(SHIM.read_text())
    funcs = {f.name: f for f in ast.walk(tree)
             if isinstance(f, ast.FunctionDef)}

    def callers(name: str) -> set[str]:
        return {f.name for f in funcs.values()
                for n in ast.walk(f) if isinstance(n, ast.Call)
                and isinstance(n.func, (ast.Name, ast.Attribute))
                and getattr(n.func, "id", getattr(n.func, "attr", None))
                == name}

    assert callers("chroot") == {"enter_worker_view"}
    assert callers("enter_worker_view") == {"_before_exec"}
    assert callers("_before_exec") == {"run", "run_as_init"}
    for f in ("run", "run_as_init"):
        body = ast.unparse(funcs[f])
        fork = body.index("os.fork()")
        assert fork < body.index("_before_exec(enter)") \
            < body.index("os.execvp"), f
    main = ast.unparse(funcs["main"])
    assert main.index("_confine_or_126(") < main.index("return run(")
    sb = ast.unparse(funcs["run_sandbox"])
    assert sb.index("_confine_or_126(") < sb.index("run_as_init(")


def test_worker_shell_enters_the_view_only_after_it_nests():
    src = ast.unparse(ast.parse(WORKER_SHELL.read_text()))
    assert src.index("lk.nest()") < src.index("lk.enter_worker_view(")


def _ws():
    spec = importlib.util.spec_from_file_location("_ws", WORKER_SHELL)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_leeries_hook_is_recognised_exactly():
    ws = _ws()
    here = str(WORKER_SHELL.parent)
    hook = f"{sys.executable} -I {here}/forbidden-command-hook.py "
    assert ws._is_leeries_hook([hook + "eyJmb3JiaWRkZW4iOltdfQ=="], here)
    for words in ([hook], [hook + "abc; id"], [hook + "abc", "x"],
                  ["python3 -I " + here + "/forbidden-command-hook.py abc"],
                  [hook.replace(" -I ", " ") + "abc"]):
        assert not ws._is_leeries_hook(words, here), words


# --- the orchestrator's side (unit) -----------------------------------------


@pytest.fixture
def orch():
    spec = importlib.util.spec_from_file_location(
        "_leerie_view", REPO_ROOT / "orchestrator" / "leerie.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_confined_argv_names_the_worker_root_only_where_it_is_used(
        orch, tmp_path, monkeypatch):
    g = orch.WorkerGrants(rw=("/w",), ro=("/r",))
    monkeypatch.setattr(orch, "WORKER_ROOT", tmp_path / "absent")
    monkeypatch.setenv(orch.ISOLATE_ENV, "1")
    assert "--worker-root" not in orch._confined_argv(g, ["c"], cli=True)
    root = tmp_path / "wr"
    root.mkdir()
    monkeypatch.setattr(orch, "WORKER_ROOT", root)
    argv = orch._confined_argv(g, ["c"])
    assert argv[argv.index("--worker-root") + 1] == str(root)
    assert "--cli" not in argv
    cli = orch._confined_argv(g, ["c"], cli=True)
    assert cli[cli.index("--worker-root"):cli.index("--worker-root") + 3] \
        == ["--worker-root", str(root), "--cli"]
    sb = orch._confined_argv(g, ["c"], sandbox=orch.HeldOutSandbox(
        view="/v", place=(), report=None))
    assert "--worker-root" in sb and "--cli" not in sb
    probe = orch._confined_argv(g, ["c"], sandbox=orch.HeldOutSandbox(
        view="/v", place=(), report=None), worker_root=False)
    assert "--worker-root" not in probe
    monkeypatch.delenv(orch.ISOLATE_ENV)
    assert "--worker-root" not in orch._confined_argv(g, ["c"])
    # A link is not a worker root.
    link = tmp_path / "link"
    link.symlink_to(root)
    monkeypatch.setattr(orch, "WORKER_ROOT", link)
    assert orch._worker_root() is None


def test_a_run_started_on_a_repository_image_needs_its_worker_root(
        orch, tmp_path, monkeypatch, capsys):
    """The launcher says the run starts on a repository image; an image
    without a worker root (an older layout reused, review round 29
    execution L2) stops the run before anything runs, isolation or not."""
    monkeypatch.setattr(orch, "WORKER_ROOT", tmp_path / "absent")
    ran = []
    monkeypatch.setattr(orch.subprocess, "run", lambda argv, **kw: (
        ran.append(argv) or subprocess.CompletedProcess(argv, 0, "", "")))
    monkeypatch.delenv(orch.REPO_IMAGE_ENV, raising=False)
    orch._require_worker_view()          # the base image: nothing expected
    monkeypatch.setenv(orch.REPO_IMAGE_ENV, "1")
    with pytest.raises(SystemExit):
        orch._require_worker_view()
    assert "has no repository root" in capsys.readouterr().err
    assert ran == []
    root = tmp_path / "wr"
    root.mkdir()
    monkeypatch.setattr(orch, "WORKER_ROOT", root)
    orch._require_worker_view()          # isolation off: nothing runs
    launcher = (REPO_ROOT / "leerie").read_text()
    assert '-e "LEERIE_REPO_IMAGE=${REPO_IMAGE_REF:+1}"' in launcher
    assert 'child_env["LEERIE_REPO_IMAGE"] = ${_repo_image_json}' in launcher
    assert '"${_FLY_REPO_DF_SHA:+1}")"' in launcher


def test_what_a_repository_installs_in_home_is_named(orch, tmp_path):
    """`/home` is the run's own, so what the repository's Dockerfile put
    beneath `/home/leerie` is missing from every confined command; those
    paths are found by name, the base's own and a mounted place left out
    (review round 29, execution L3)."""
    wr, home = tmp_path / "wr", tmp_path / "home" / "leerie"
    for d in (".local/share/mise", ".cache/leerie/pip"):
        (wr / "home" / "leerie" / d).mkdir(parents=True)
        (home / d).mkdir(parents=True)
    (wr / "home" / "leerie" / ".bun" / "bin").mkdir(parents=True)
    (wr / "home" / "leerie" / ".local" / "bin").mkdir()
    (wr / "home" / "leerie" / ".local" / "share" / "mise" / "x").touch()
    (home / ".local" / "share" / "mise" / "x").touch()
    (wr / "home" / "leerie" / ".profile").symlink_to("/etc/passwd")
    assert orch._worker_home_extras(wr, home) == [
        "/home/leerie/.bun", "/home/leerie/.local/bin", "/home/leerie/.profile"]
    # The base's own home, unchanged: nothing.
    (wr / "home" / "leerie" / ".bun" / "bin").rmdir()
    (wr / "home" / "leerie" / ".bun").rmdir()
    (wr / "home" / "leerie" / ".local" / "bin").rmdir()
    (wr / "home" / "leerie" / ".profile").unlink()
    assert orch._worker_home_extras(wr, home) == []
    # A worker root whose /home is a link is not walked.
    other = tmp_path / "other"
    (other / "leerie" / "x").mkdir(parents=True)
    wr2 = tmp_path / "wr2"
    wr2.mkdir()
    (wr2 / "home").symlink_to(other)
    assert orch._worker_home_extras(wr2, home) == []


def test_the_entry_stops_a_run_whose_worker_root_installs_in_home(
        orch, tmp_path, monkeypatch, capsys):
    root = tmp_path / "wr"
    (root / "home" / "leerie" / "zz-not-in-the-container").mkdir(
        parents=True)
    monkeypatch.setattr(orch, "WORKER_ROOT", root)
    with pytest.raises(SystemExit):
        orch._require_worker_view()
    err = capsys.readouterr().err
    assert "/home/leerie/zz-not-in-the-container" in err
    assert "/opt or /usr/local" in err


def test_the_entry_stops_a_run_whose_commands_miss_the_worker_root(
        orch, tmp_path, monkeypatch):
    root = tmp_path / "wr"
    root.mkdir()
    monkeypatch.setattr(orch, "WORKER_ROOT", root)
    ran = []
    monkeypatch.setattr(orch.subprocess, "run", lambda argv, **kw: (
        ran.append(argv) or subprocess.CompletedProcess(argv, 1, "", "no")))
    orch._require_worker_view()          # isolation off: nothing runs
    assert ran == []
    monkeypatch.setenv(orch.ISOLATE_ENV, "1")
    with pytest.raises(SystemExit):
        orch._require_worker_view()
    assert ran and ran[0][-3:] == ["test", "-e", "/.leerie-worker-env"]
    assert "--worker-root" in ran[0]
    src = ast.unparse(ast.parse(
        (REPO_ROOT / "orchestrator" / "leerie.py").read_text()))
    entry = src[src.index("def _require_confinement("):]
    entry = entry[:entry.index("\ndef ")]
    assert entry.index("_require_heldout_sandbox(") \
        < entry.index("_require_worker_view()")


# --- isolation, for real ---------------------------------------------------

_WHY = LL.isolation_error()
iso = pytest.mark.skipif(
    _WHY is not None,
    reason=f"this host cannot build the isolation ({_WHY}); run in the "
           "container under the leerie-container profile")

_MARK = "only-the-worker-root-has-this"


def _copy_with_libs(prog: str, root: Path) -> None:
    path = shutil.which(prog) or prog
    real = os.path.realpath(path)
    dst = root / "usr" / "bin" / os.path.basename(path)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(real, dst)
    out = subprocess.run(["ldd", real], capture_output=True, text=True).stdout
    for word in out.split():
        if word.startswith("/") and os.path.exists(word):
            lib = root / os.path.realpath(word).lstrip("/")
            lib.parent.mkdir(parents=True, exist_ok=True)
            if not lib.exists():
                shutil.copy2(os.path.realpath(word), lib)
            alias = root / word.lstrip("/")
            if not alias.exists():
                alias.parent.mkdir(parents=True, exist_ok=True)
                os.link(lib, alias)


@pytest.fixture
def wroot(tmp_path):
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("grants live in the state root, not beneath /tmp: run "
                    "with --basetemp outside /tmp")
    root = tmp_path / "worker-root"
    for p in ("sh", "bash", "cat", "env", "grep"):
        _copy_with_libs(p, root)
    for link in ("bin", "sbin"):
        if not (root / link).exists():
            (root / link).symlink_to("usr/bin")
    (root / "etc").mkdir(exist_ok=True)
    (root / "etc" / "mark").write_text(_MARK)
    (root / "etc" / "ld.so.preload").write_text(TRAP + "\n")
    (root / "opt" / "venv" / "bin").mkdir(parents=True)
    (root / "opt" / "leerie-image").mkdir()      # leerie's wins in the view
    _env_file(root / LL.BASE_ENV_FILE, {"PATH": "/usr/bin:/bin"})
    _env_file(root / LL.WORKER_ENV_FILE,
              {"PATH": "/opt/venv/bin:/usr/bin:/bin",
               "VIRTUAL_ENV": "/opt/venv", "LEERIE_X": "1",
               "BUNDLE_PATH": "/opt/bundle"})
    (tmp_path / "w").mkdir()
    return root


def _shim(opts: list[str], cmd: list[str], cwd: Path,
          env: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-I", "-S", str(SHIM), *opts, "--", *cmd],
        cwd=str(cwd), capture_output=True, text=True, timeout=120,
        env=env)


@iso
def test_a_store_the_run_set_wins_over_the_worker_stages(wroot):
    """Through the real shim: the worker stage sets `BUNDLE_PATH=/opt/bundle`
    (an image install, read-only), the confined command's environment sets
    the run's store — the command sees the store; with none set it sees the
    stage's. Falsified against 0.65.0's shim: the first command saw
    `/opt/bundle`."""
    w = wroot.parent / "w"
    opts = ["--isolate", "--worker-root", str(wroot), "--rw", str(w),
            "--rw", "/dev/null", "--ro", "/"]
    base = {"PATH": "/usr/bin:/bin"}
    store = str(w / "bundle")
    r = _shim(opts, ["sh", "-c", "echo B=$BUNDLE_PATH"], w,
              env={**base, "BUNDLE_PATH": store})
    assert r.returncode == 0, r.stderr
    assert f"B={store}\n" in r.stdout, r.stdout
    r = _shim(opts, ["sh", "-c", "echo B=$BUNDLE_PATH"], w, env=base)
    assert r.returncode == 0, r.stderr
    assert "B=/opt/bundle\n" in r.stdout, r.stdout


@iso
def test_a_confined_command_runs_in_the_worker_root(wroot):
    w = wroot.parent / "w"
    r = _shim(["--isolate", "--worker-root", str(wroot), "--rw", str(w),
               "--rw", "/dev/null", "--ro", "/"],
              ["sh", "-c", "cat /etc/mark; echo; echo VE=$VIRTUAL_ENV; "
               "echo P=$PATH; echo X=${LEERIE_X-unset}; echo hi > ok; "
               "cat ok; grep -E '^CapEff' /proc/self/status; "
               "echo no > /etc/x 2>/dev/null || echo etc-ro; "
               "test -d /opt/leerie-image/scripts && echo leerie-image"],
              w)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert _MARK in out
    assert "VE=/opt/venv" in out and "P=/opt/venv/bin:" in out
    assert "X=unset" in out
    assert "hi" in out and (w / "ok").read_text() == "hi\n"
    assert "CapEff:\t0000000000000000" in out
    assert "etc-ro" in out
    # leerie's own /opt/leerie-* comes from the container, over the
    # worker root's.
    if (Path("/opt/leerie-image") / "scripts").is_dir():
        assert "leerie-image" in out
    # The command is the repository's, in the repository's root: its
    # loader reads the worker root's ld.so.preload.
    assert TRAP in r.stderr


@iso
def test_a_held_out_runner_runs_in_the_worker_root(wroot):
    w = wroot.parent / "w"
    r = _shim(["--sandbox", "--view", str(w), "--worker-root", str(wroot),
               "--rw", "/dev/null", "--ro", "/"],
              ["sh", "-c", "cat /etc/mark"], w)
    assert r.returncode == 0, r.stderr
    assert _MARK in r.stdout


@iso
def test_a_workers_cli_stays_in_leeries_root_and_its_commands_do_not(wroot):
    w = wroot.parent / "w"
    here = WORKER_SHELL.parent
    hook = (f"{sys.executable} -I {here}/forbidden-command-hook.py "
            "e30=")
    probe = (
        "import os, subprocess, sys\n"
        "st = open('/proc/self/status').read()\n"
        "print('cli-in-worker-root' if os.path.exists('/etc/mark') "
        "and open('/etc/mark').read() == %r else 'cli-in-leeries-root')\n"
        "print([l for l in st.splitlines() if l.startswith('CapEff')][0])\n"
        "r = subprocess.run([%r, 'cat /etc/mark; echo; grep CapEff "
        "/proc/self/status; echo VE=$VIRTUAL_ENV'], capture_output=True, "
        "text=True)\n"
        "print('CMD', r.returncode, r.stdout)\n"
        "h = subprocess.run([%r, %r], capture_output=True, text=True)\n"
        "print('HOOK', h.returncode, h.stdout, h.stderr)\n"
    ) % (_MARK, str(WORKER_SHELL), str(WORKER_SHELL), hook)
    r = _shim(["--isolate", "--worker-root", str(wroot), "--cli",
               "--rw", str(w), "--rw", "/dev/null", "--ro", "/"],
              [sys.executable, "-I", "-S", "-c", probe], w)
    assert r.returncode == 0, r.stderr
    out = r.stdout
    assert "cli-in-leeries-root" in out
    # Its one capability: CAP_SYS_CHROOT (bit 18), in the shim's namespace.
    assert "CapEff:\t0000000000040000" in out
    cmd = out.split("CMD", 1)[1].split("HOOK", 1)[0]
    assert cmd.lstrip().startswith("0") and _MARK in cmd
    assert "CapEff:\t0000000000000000" in cmd and "VE=/opt/venv" in cmd
    # Leerie's hook ran in leerie's root: its loader read no trap.
    hook_part = out.split("HOOK", 1)[1]
    assert TRAP not in hook_part
    # Nothing of leerie's — the shim, the CLI — started in the worker root:
    # the command's complaints went to the stream the CLI captured, and
    # none reached the shim's.
    assert TRAP not in r.stderr


@iso
def test_a_cli_without_a_view_runs_commands_as_before(tmp_path):
    if str(tmp_path.resolve()).startswith("/tmp/"):
        pytest.skip("run with --basetemp outside /tmp")
    probe = ("import subprocess; r = subprocess.run([%r, 'echo ok; grep "
             "CapEff /proc/self/status'], capture_output=True, text=True); "
             "print(r.stdout)") % str(WORKER_SHELL)
    r = _shim(["--isolate", "--rw", "/dev/null", "--ro", "/"],
              [sys.executable, "-I", "-S", "-c", probe], tmp_path)
    assert r.returncode == 0, r.stderr
    assert "ok" in r.stdout and "CapEff:\t0000000000000000" in r.stdout


# --- the run's name service (review round 29, execution H1) ---------------

def _python_in(root: Path) -> str:
    """This interpreter and its standard library, copied into `root`: the
    worker root's own python, as an image's would be."""
    import sysconfig
    _copy_with_libs(sys.executable, root)
    std = sysconfig.get_paths()["stdlib"]
    shutil.copytree(std, root / std.lstrip("/"), symlinks=True,
                    ignore=shutil.ignore_patterns(
                        "test", "tests", "__pycache__", "idlelib",
                        "tkinter", "turtledemo", "ensurepip",
                        "site-packages", "dist-packages"))
    dyn = sysconfig.get_config_var("DESTSHARED")
    if dyn and os.path.isdir(dyn) and not (root / dyn.lstrip("/")).exists():
        shutil.copytree(dyn, root / dyn.lstrip("/"), symlinks=True)
    return "/usr/bin/" + os.path.basename(sys.executable)


@iso
def test_localhost_resolves_and_answers_in_the_worker_view(wroot):
    """No image snapshot holds `/etc/hosts`, `/etc/hostname` or
    `/etc/resolv.conf` — the runtime mounts them when the container starts
    — so the view takes them from the container: `localhost` resolves in a
    repository's root, and a server bound to it answers a client that dials
    it by name."""
    _copy_with_libs("getent", wroot)
    assert not (wroot / "etc" / "hosts").exists()
    py = _python_in(wroot)
    w = wroot.parent / "w"
    roundtrip = (
        "import socket\n"
        "s = socket.socket(); s.bind(('localhost', 0)); s.listen(1)\n"
        "c = socket.create_connection(('localhost', s.getsockname()[1]))\n"
        "a, _ = s.accept(); c.sendall(b'ping'); print(a.recv(4).decode())\n")
    r = _shim(["--isolate", "--worker-root", str(wroot), "--rw", str(w),
               "--rw", "/dev/null", "--ro", "/"],
              ["sh", "-c", f"cat /etc/mark; echo; getent hosts localhost; "
               f"echo getent=$?; {py} -I -S -c \"$0\"", roundtrip], w)
    assert r.returncode == 0, r.stderr
    assert _MARK in r.stdout                     # the repository's root
    assert "getent=0" in r.stdout and "localhost" in r.stdout
    assert "ping" in r.stdout
