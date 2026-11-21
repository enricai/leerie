"""A repository command gets nothing the operator handed leerie for itself
(DESIGN §6 *A repository command gets nothing the operator handed leerie for
itself*).

Measured at 3f73fd78 in the leerie image: a confined command of every kind
(measurement, install, setup hook, toolchain) printed the workers' OAuth
token from its environment, the CLI's login from `~/.claude/`, and both
again from the staged directory `/opt/leerie-home-src`. Three channels, one
rule: what the launcher hands the container says who it is for where it is
handed, and a repository command's world is built from the repository's
class only.

The grant-shape tests run anywhere. The real-path test starts the leerie
image the way the launcher does (the real `container-entry.sh`, the staged
classes, the `-e`s named by the launcher's own loop), with the entry's exec
target swapped for a driver that runs leerie's real `_require_confinement`,
`_measurement_grants` and `_run_streaming`; it needs a leerie container
(LEERIE_TEST_CONTAINER_IMAGE, `nerdctl` on PATH), as
`tests/test_confined_home.py` does.
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.conftest import leerie_container_drop
from tests.test_confined_home import (  # noqa: F401  (fixtures)
    KINDS, _home, _st, _view, _users_env, homes)
from tests.test_worker_confinement import (  # noqa: F401  (fixture)
    pytestmark, run)

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
SHIM = REPO_ROOT / "scripts" / "landlock_exec.py"


def _shim():
    spec = importlib.util.spec_from_file_location("_ll_cmdhome", SHIM)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _naming_loop() -> str:
    """The launcher's loop that names every `-e` of its run argv, as it
    ships."""
    lines = LAUNCHER.read_text().splitlines()
    start = lines.index('  _launcher_env=""')
    end = lines.index("  unset _with_env _prev _named _launcher_env")
    return "\n".join(lines[start:end + 1])


# ------------------------------------------------------------ the launcher


def test_the_launcher_names_every_variable_it_passes():
    """The loop names each `-e`'s variable, bare or with a value, a value
    holding `=` included, and inserts the list before the working
    directory: every other argument keeps its place."""
    script = ("_run_argv=(--rm -e A=1 -v /x:/y -e B -e 'C=x=y' --cgroupns=host "
              "-w /work image task -e notmine)\n" + _naming_loop() +
              '\nprintf "%s\\n" "${_run_argv[@]}"')
    out = subprocess.run(["bash", "-c", script], capture_output=True,
                         text=True, check=True).stdout.splitlines()
    assert out == ["--rm", "-e", "A=1", "-v", "/x:/y", "-e", "B", "-e",
                   "C=x=y", "--cgroupns=host", "-e",
                   "LEERIE_LAUNCHER_ENV=A,B,C", "-w", "/work", "image",
                   "task", "-e", "notmine"]


def test_every_variable_the_run_passes_comes_before_its_working_directory():
    """The loop names the `-e`s before the first `-w`: one the run argv
    passed after it would reach the orchestrator unnamed."""
    text = LAUNCHER.read_text()
    block = text[text.index("  _run_argv=(\n"):]
    block = block[:block.index("\n  )\n")]
    lines = [ln.strip() for ln in block.splitlines()
             if ln.strip() and not ln.strip().startswith("#")]
    w = lines.index("-w /work")
    after = lines[w + 1:]
    assert not [ln for ln in after if ln.startswith("-e")], after
    # What follows is the image and the task, never an env-carrying array.
    assert all("AUTH" not in ln and "_env_args" not in ln for ln in after)


def test_each_remote_wrapper_names_what_it_sets():
    """Fly and EC2 start the orchestrator from a wrapper that sets its
    environment itself: each names every key it set after the identity."""
    text = LAUNCHER.read_text()
    assert text.count("_identity = dict(child_env)") == 2
    assert text.count('child_env["LEERIE_LAUNCHER_ENV"] = ",".join(') == 2
    script = ('import os\nchild_env = dict(os.environ)\n'
              'child_env["HOME"] = "/home/leerie"\n'
              '_identity = dict(child_env)\n'
              'child_env["CLAUDE_CODE_OAUTH_TOKEN"] = "t"\n'
              'child_env["USER_REPO"] = "r"\n'
              + text[text.index('child_env["LEERIE_LAUNCHER_ENV"] = '):]
              .split("\nPY", 1)[0] +
              '\nprint(child_env["LEERIE_LAUNCHER_ENV"])')
    got = subprocess.run(["python3", "-I", "-c", script], capture_output=True,
                         text=True, check=True).stdout.strip()
    assert got.split(",") == ["CLAUDE_CODE_OAUTH_TOKEN", "USER_REPO"]


def test_the_launcher_stages_each_file_in_its_class():
    """The CLI's login and settings are staged in `cli/`, what the
    repository's commands may use in `home/`; nothing is staged at the
    stage's top, where neither class would take it."""
    text = LAUNCHER.read_text()
    assert 'STAGE_CLI="$STAGE/cli"' in text
    assert 'STAGE_HOME="$STAGE/home"' in text
    for cli in ('"$STAGE_CLI/.claude.json"', '"$STAGE_CLI/.claude/.credentials.json"',
                '"$STAGE_CLI/.aws"'):
        assert cli in text, cli
    for home in ('"$STAGE_HOME/.ssh"', '"$STAGE_HOME/.gnupg"',
                 '"$STAGE_HOME/.netrc"', '"$STAGE_HOME/"'):
        assert home in text, home
    code = [ln for ln in text.splitlines()
            if not ln.lstrip().startswith("#")]
    assert not [ln for ln in code if '"$STAGE/.' in ln or "$STAGE/." in ln
                and "$STAGE/cli" not in ln], "a file staged in no class"


# ------------------------------------------------------- the orchestrator


def test_the_launcher_list_is_read_with_its_own_name(leerie, monkeypatch):
    monkeypatch.delenv(leerie.LAUNCHER_ENV_ENV, raising=False)
    assert leerie._launcher_env_names() == ()
    monkeypatch.setenv(leerie.LAUNCHER_ENV_ENV, "A,B, C\tD,,A")
    assert leerie._launcher_env_names() == (
        "A", "B", "C", "D", "LEERIE_LAUNCHER_ENV")


@pytest.mark.parametrize("kind", KINDS)
def test_a_repository_command_starts_with_none_of_them(
        leerie, run, homes, monkeypatch, kind):
    """Every kind drops every name the launcher passed, and the list: the
    command keeps the container's own environment and what leerie sets."""
    h, layer = homes
    monkeypatch.setenv(leerie.LAUNCHER_ENV_ENV,
                       "CLAUDE_CODE_OAUTH_TOKEN,LEERIE_STATE_DIR,USER_REPO")
    g = leerie._measurement_grants(_st(run), _view(run),
                                   getattr(leerie, kind))
    base = {"CLAUDE_CODE_OAUTH_TOKEN": "secret", "LEERIE_STATE_DIR": "/s",
            "USER_REPO": "r", leerie.LAUNCHER_ENV_ENV: "x",
            "PATH": "/usr/bin", "HOME": str(h), "LANG": "C.UTF-8"}
    env = leerie._grant_env(base, g)
    assert not {"CLAUDE_CODE_OAUTH_TOKEN", "LEERIE_STATE_DIR", "USER_REPO",
                leerie.LAUNCHER_ENV_ENV} & set(env), env
    assert env["PATH"] == "/usr/bin" and env["LANG"] == "C.UTF-8"
    assert env["HOME"] == (str(layer.resolve()) if kind == "TOOLCHAIN_KIND"
                           else str(h))


def test_a_worker_keeps_them(leerie, run, homes, monkeypatch):
    """A worker is the CLI: it authenticates by what the launcher passed."""
    h, _ = homes
    monkeypatch.setenv(leerie.LAUNCHER_ENV_ENV, "CLAUDE_CODE_OAUTH_TOKEN")
    g = leerie._worker_grants(
        run_dir=run["run_dir"], state={}, schema_key="implementer",
        cwd=run["wts"]["wt-a"], allowed_tools=leerie.ACT_TOOLS,
        env={**run["env"], "HOME": str(h)})
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in g.drop
    assert _home(g).base == ""


@pytest.mark.parametrize("kind", KINDS)
def test_a_repository_commands_home_is_built_on_the_commands_home(
        leerie, run, homes, monkeypatch, tmp_path, kind):
    """The home overlay's bottom layer is the commands' pristine home, in
    place of the workers' (`--base`), for every kind: the toolchain kind's
    `HOME` is its layer, but `/home/leerie` beneath it is overlaid too."""
    h, layer = homes
    cmd_home = tmp_path / "command-home"
    cmd_home.mkdir()
    monkeypatch.setenv(leerie.COMMAND_HOME_ENV, str(cmd_home))
    monkeypatch.setenv(leerie.ISOLATE_ENV, "1")
    g = leerie._measurement_grants(_st(run), _view(run),
                                   getattr(leerie, kind))
    assert _home(g).target == str(h.resolve())
    assert _home(g).base == str(cmd_home.resolve())
    argv = leerie._confined_argv(g, ["true"])
    i = argv.index("--home")
    assert argv[i:i + 4] == ["--home", str(h.resolve()), "--base",
                             str(cmd_home.resolve())]
    monkeypatch.setenv(leerie.COMMAND_HOME_ENV, "relative")
    assert leerie._command_home() == ""


def test_the_shim_takes_a_base_for_the_home_only():
    sh = _shim()
    o = sh._parse(["--isolate", "--home", "/h", "--base", "/b/", "--lower",
                   "/l", "--upper", "tmpfs:9", "--", "true"])
    assert o["overlays"] == [{"target": "/h", "lowers": ["/l"], "upper": 9,
                              "home": True, "base": "/b"}]
    for bad in (["--isolate", "--base", "/b", "--home", "/h", "--upper",
                 "tmpfs:9"],
                ["--isolate", "--overlay", "/a", "--base", "/b"],
                ["--isolate", "--home", "/h", "--base", "/b", "--base", "/c",
                 "--upper", "tmpfs:9"],
                ["--isolate", "--home", "/h", "--base", "b", "--upper",
                 "tmpfs:9"],
                ["--isolate", "--home", "/h", "--base", "/", "--upper",
                 "tmpfs:9"]):
        assert "bad --base" in sh._parse([*bad, "--", "true"]), bad


def test_no_confined_process_reads_the_staged_directory(
        leerie, tmp_path, monkeypatch):
    """The reads outside the state root leave the staged directory out,
    and only it: its siblings are still read."""
    # The session's paths lie beneath /tmp on CI (pytest's tmp_path), which
    # `_reads_outside(tmp=False)` drops wholesale as the shim's own /tmp; so
    # SHARED_TMP is pointed off the test tree, as test_confined_home's
    # `homes` fixture does, or the sibling below would be dropped with it.
    monkeypatch.setattr(leerie, "SHARED_TMP", tmp_path / "no-shared-tmp")
    top = tmp_path / "opt"
    src = top / "leerie-home-src"
    (src / "cli").mkdir(parents=True)
    (top / "other").mkdir()
    monkeypatch.setattr(leerie, "HOME_SRC", src)
    state_root = tmp_path / "state"
    state_root.mkdir()
    reads = leerie._reads_outside(state_root, tmp=False)
    real = src.resolve()
    assert not [p for p in reads
                if real.is_relative_to(os.path.realpath(p))
                or Path(os.path.realpath(p)).is_relative_to(real)], reads
    assert str((top / "other").resolve()) in {os.path.realpath(p)
                                              for p in reads}


def test_the_entry_builds_each_home_from_its_classes():
    """The entry copies the image's home to the commands' home first, then
    `home/` into both and `cli/` into the workers' only; the rootful guard
    owns both homes to leerie."""
    text = (REPO_ROOT / "scripts" / "container-entry.sh").read_text()
    order = ['_copy_dots /home/leerie "$LEERIE_COMMAND_HOME"',
             "_copy_dots /opt/leerie-home-src/home /home/leerie",
             '_copy_dots /opt/leerie-home-src/home "$LEERIE_COMMAND_HOME"',
             "_copy_dots /opt/leerie-home-src/cli /home/leerie"]
    at = [text.index(s) for s in order]
    assert at == sorted(at), at
    assert text.count("/opt/leerie-home-src/cli") == 2   # the test, the copy
    assert 'chown -R leerie: "$LEERIE_COMMAND_HOME"' in text
    docker = (REPO_ROOT / "Dockerfile").read_text()
    assert "ENV LEERIE_COMMAND_HOME=/leerie-command-home" in docker


def test_the_remote_seeds_ship_the_cli_class_only():
    for name in ("seed-auth.sh", "ec2-seed-auth.sh"):
        text = (REPO_ROOT / "scripts" / "remote" / name).read_text()
        assert 'tar -czC "$STAGE/cli" \\' in text, name
        assert 'tar -czC "$STAGE" \\' not in text, name


# ------------------------------------------------------ the real container

NEEDLE = "PX-OPERATOR-SECRET-7f3a"

_DRIVER = r'''
import asyncio, importlib.util, json, os, subprocess, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
leerie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leerie)
NEEDLE = sys.argv[2]
RUN = "cmdhome0"


class _St:
    def __init__(s, user, run_dir):
        s.repo_root, s.run_id, s.run_dir = user, RUN, run_dir
        s.data = {"working_branch": "main", "verbosity": "quiet"}

    def save(s):
        pass


base = Path("/leerie-state/w")
user = base / "user"
user.mkdir(parents=True)


def g(*a, cwd=user):
    return subprocess.run(["git", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


g("init", "-q", "-b", "main")
(user / "README").write_text("x\n")
g("add", "-A")
g("-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm", "base")
state_root = Path("/leerie-state")
run_dir = state_root / "runs" / RUN
run_dir.mkdir(parents=True)
st = _St(user, run_dir)
repo = leerie._ensure_run_repo(st)
wt = run_dir / "worktrees" / f"{RUN}-view"
g("worktree", "add", "-q", str(wt), "main", cwd=repo)
leerie._require_confinement(run_dir, state_root, user)
PROBE = r"""
{ echo "ENV=${CLAUDE_CODE_OAUTH_TOKEN+set}"; echo "LIST=${LEERIE_LAUNCHER_ENV+set}"
  env; echo ---
  cat ~/.claude/.credentials.json /home/leerie/.claude/.credentials.json
  cat ~/.claude.json /home/leerie/.claude.json
  cat /opt/leerie-home-src/cli/.claude/.credentials.json
  ls -A /opt/leerie-home-src && echo SRC=LISTED
  cat /home/leerie/.ssh/id_px.pub /home/leerie/.gitconfig
  echo w >> /home/leerie/.gitconfig && echo HOMEWRITE=ok
  touch /home/leerie/new-file && echo NEWFILE=ok
  # The orchestrator (and the entry) still hold the token in their own
  # environment; a confined command must not recover it from any process's
  # /proc/<pid>/environ. Its own is dropped, so a non-empty
  # CLAUDE_CODE_OAUTH_TOKEN anywhere here is a leak across /proc.
  echo "PROCENV=$(cat /proc/[0-9]*/environ 2>/dev/null | tr '\0' '\n' | grep -c '^CLAUDE_CODE_OAUTH_TOKEN=.')"
} > probe.out 2>&1
"""
out = {"isolated": leerie._isolated(),
       "orchestrator_env": "CLAUDE_CODE_OAUTH_TOKEN" in os.environ,
       "workers_home": NEEDLE in Path(
           "/home/leerie/.claude/.credentials.json").read_text(),
       "command_home_uid": os.stat("/leerie-command-home").st_uid,
       "uid": os.getuid(), "kinds": {}}
for k in (leerie.MEASURE_KIND, leerie.INSTALL_KIND, leerie.SETUP_HOOK_KIND,
          leerie.TOOLCHAIN_KIND):
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", PROBE], grants=leerie._measurement_grants(st, wt, k),
        cwd=str(wt), timeout=120, verbosity="quiet", tail_lines=20))
    txt = (wt / "probe.out").read_text()
    (wt / "probe.out").unlink()
    lines = txt.splitlines()
    procenv = next((int(l.split("=", 1)[1]) for l in lines
                    if l.startswith("PROCENV=")), -1)
    out["kinds"][k] = {
        "rc": rc, "env": "ENV=set" in lines, "list": "LIST=set" in lines,
        "secret": NEEDLE in txt, "cli": "PX-CLI-MARK" in txt,
        "src": "SRC=LISTED" in lines, "ssh": "PX-SSH-MARK" in txt,
        "git": "PX-GIT-MARK" in txt, "write": "HOMEWRITE=ok" in lines,
        "new": "NEWFILE=ok" in lines, "procenv": procenv}
print("RESULT " + json.dumps(out), flush=True)
'''


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_the_real_container_keeps_the_operators_credentials_from_commands(
        tmp_path):
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    out = tmp_path / "out"
    out.mkdir()
    (out / "driver.py").write_text(_DRIVER)
    st = tmp_path / "stage"
    (st / "cli" / ".claude").mkdir(parents=True)
    (st / "home" / ".ssh").mkdir(parents=True)
    creds = {"claudeAiOauth": {"accessToken": NEEDLE,
                               "scopes": ["user:inference"]}}
    (st / "cli" / ".claude" / ".credentials.json").write_text(
        json.dumps(creds))
    (st / "cli" / ".claude.json").write_text('{"px": "PX-CLI-MARK"}')
    (st / "home" / ".ssh" / "id_px.pub").write_text("ssh-ed25519 AAAA PX-SSH-MARK\n")
    (st / "home" / ".gitconfig").write_text("[user]\n\tname = PX-GIT-MARK\n")
    work = tmp_path / "work"
    work.mkdir()
    for p in [tmp_path, out, *out.iterdir(), work, *st.rglob("*"), st]:
        p.chmod(p.stat().st_mode | (0o055 if p.is_dir() else 0o044))
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = ["--security-opt", "seccomp=unconfined"]
    profile = os.environ.get("LEERIE_TEST_APPARMOR_PROFILE",
                             "leerie-container")
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", f"apparmor={profile}"]
    _, owner = leerie_container_drop()
    tmpfs = f"rw,exec,size=1g,{owner}"
    swap = ("s#/usr/bin/python3 /opt/leerie-image/orchestrator/leerie.py#"
            "/usr/bin/python3 /out/driver.py "
            "/opt/leerie-image/orchestrator/leerie.py#")
    script = ("_run_argv=(--rm -e LEERIE_STATE_DIR=/leerie-state "
              "-e CLAUDE_CODE_OAUTH_TOKEN " + " ".join(sec) +
              f" --network none -v {work}:/work:ro -v {REPO_ROOT}:/opt/leerie-image:ro"
              f" -v {out}:/out:ro -v {st}:/opt/leerie-home-src:ro"
              f" --tmpfs /leerie-state:{tmpfs} --tmpfs /leerie-toolchains:{tmpfs}"
              " --user 0 --entrypoint sh -w /work " + image + " -c "
              "\"mkdir -p /leerie-toolchains/mise /leerie-toolchains/home && "
              f"sed '{swap}' /opt/leerie-image/scripts/container-entry.sh "
              "> /run/px-entry.sh && exec sh /run/px-entry.sh "
              f"{NEEDLE}\")\n" + _naming_loop() +
              '\nexec nerdctl run "${_run_argv[@]}"')
    env = {**_users_env(), "CLAUDE_CODE_OAUTH_TOKEN": NEEDLE}
    r = subprocess.run(["bash", "-c", script], capture_output=True,
                       text=True, timeout=900, env=env)
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, r.stdout[-4000:] + r.stderr[-4000:]
    got = json.loads(line[0][len("RESULT "):])
    print("RESULT", json.dumps(got), "rootless", rootless.is_file())
    assert got["isolated"] is True, got
    # The workers' world keeps both credentials.
    assert got["orchestrator_env"] and got["workers_home"], got
    assert got["command_home_uid"] == got["uid"], got
    for kind, k in got["kinds"].items():
        assert k["rc"] == 0, (kind, k)
        assert not (k["env"] or k["list"] or k["secret"] or k["cli"]
                    or k["src"]), (kind, k)
        # No process's /proc/<pid>/environ leaks the token to the command.
        assert k["procenv"] == 0, (kind, k)
        # The repository's class is the command's, and its home is its own.
        assert k["ssh"] and k["git"] and k["write"] and k["new"], (kind, k)
