"""A repository's pinned runtime is the one its commands run (DESIGN §6½ *A
repository's pinned runtime is the one its commands run*).

The image's PATH puts `MISE_DATA_DIR/shims` ahead of every concrete
install's `bin`, the baked LTS Node's `bin` after them as the unpinned
fallback, and leerie's own CLI, `claude`, alone in `/opt/leerie-vendor/bin`
ahead of all of them. Until then the baked Node 24's `bin` came first, so a
confined command in a tree whose `.nvmrc` said 22.19.0 ran bare `node` as
v24.22.2.

The source pins below read the Dockerfile and the EC2 bake. The real-
container test runs only when LEERIE_TEST_CONTAINER_IMAGE names a leerie
image (`nerdctl` on PATH, CONTAINERD_NAMESPACE naming its namespace) and the
network can reach nodejs.org: it takes the PATH and the `claude` link from
this checkout's Dockerfile, so it measures the source on that image's
installs, and drives leerie's real `_run_mise_install` and confined
`_run_streaming`.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from tests.conftest import REAL_HOME, XDG_BASE_DIRS

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCKERFILE = REPO_ROOT / "Dockerfile"
EC2_BAKE = REPO_ROOT / "scripts" / "remote" / "ec2-bake.sh"
VENDOR_BIN = "/opt/leerie-vendor/bin"
USER_SHIMS = "/leerie-toolchains/mise/shims"
LTS_BIN = "/usr/local/share/mise/installs/node/lts-current/bin"
LTS_CLAUDE = f"{LTS_BIN}/claude"
BASE_PATH = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"


def _image_path() -> list[str]:
    text = DOCKERFILE.read_text()
    m = re.search(r"^ENV PATH=(\S+)$", text, re.M)
    assert m, "no `ENV PATH=` line in the Dockerfile"
    return m.group(1).split(":")


def _claude_install_step() -> str:
    """The Dockerfile's RUN that installs the CLI, continuation lines joined."""
    text = DOCKERFILE.read_text()
    start = text.index("RUN npm install -g '@anthropic-ai/claude-code")
    end = start
    while text[end:text.index("\n", end)].endswith("\\"):
        end = text.index("\n", end) + 1
    return text[start:text.index("\n", end)].replace("\\\n", " ")


def _link_commands() -> str:
    """The commands of that step after the install: the vendor link."""
    step = _claude_install_step()
    return "&&".join(step.split("&&")[1:]).strip()


# ------------------------------------------------------------ the PATH, pinned

def test_the_pinned_version_is_found_before_any_concrete_install():
    path = _image_path()
    assert path.index(USER_SHIMS) < path.index(LTS_BIN)


def test_the_baked_lts_node_stays_on_path_as_the_unpinned_fallback():
    path = _image_path()
    assert path.index(LTS_BIN) < path.index("$PATH")


def test_leeries_own_cli_comes_first_and_alone():
    """`claude` resolves outside every mise shim; the directory holds the
    CLI only, so it shadows no tool a repository pins."""
    path = _image_path()
    assert path[0] == VENDOR_BIN
    links = _link_commands()
    assert re.findall(rf"{re.escape(VENDOR_BIN)}/(\S+)", links) == [
        "claude", "claude"]           # the ln target and the test -x
    assert f"ln -s {LTS_CLAUDE} {VENDOR_BIN}/claude" in links


def test_no_system_shims_directory_is_on_path():
    """`mise install --system` makes no `/usr/local/share/mise/shims`
    (measured on leerie 0.44.1); the entry named a directory that does not
    exist."""
    assert "/usr/local/share/mise/shims" not in _image_path()


def test_the_ec2_bake_makes_the_same_link():
    body = EC2_BAKE.read_text()
    install = body.index('npm install -g "$CLAUDE_SPEC"')
    link = body.index(f"ln -sfn {LTS_CLAUDE} {VENDOR_BIN}/claude")
    assert install < link


# ------------------------------------------------------ the real container

_DRIVER = r'''
import asyncio, importlib.util, json, os, subprocess, sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("leerie", sys.argv[1])
leerie = importlib.util.module_from_spec(spec)
spec.loader.exec_module(leerie)
RUN = "pinnedrt0"


class _St:
    def __init__(s, user, run_dir):
        s.repo_root, s.run_id, s.run_dir = user, RUN, run_dir
        s.data = {"working_branch": "main", "verbosity": "quiet"}

    def save(s):
        pass


base = Path(sys.argv[2])
user = base / "user"
user.mkdir(parents=True)


def g(*a, cwd=user):
    return subprocess.run(["git", *a], cwd=cwd, check=True,
                          capture_output=True, text=True).stdout.strip()


commit = ["-c", "user.email=t@x", "-c", "user.name=t", "commit", "-qm"]
g("init", "-q", "-b", "main")
(user / "README").write_text("x\n")
g("add", "-A"); g(*commit, "nothing pinned")
shas = {"nopin": g("rev-parse", "HEAD")}
(user / ".nvmrc").write_text("20.19.0\n")
g("add", "-A"); g(*commit, "pins a version never installed")
shas["absent"] = g("rev-parse", "HEAD")
(user / ".nvmrc").write_text("22.19.0\n")
g("add", "-A"); g(*commit, "pins 22.19.0")
shas["pin"] = g("rev-parse", "HEAD")
state_root = base / "state"
run_dir = state_root / "runs" / RUN
run_dir.mkdir(parents=True)
st = _St(user, run_dir)
repo = leerie._ensure_run_repo(st)
wts = {}
for name, sha in shas.items():
    wt = run_dir / "worktrees" / f"{RUN}-{name}"
    g("branch", f"b-{name}", sha, cwd=repo)
    g("worktree", "add", "-q", str(wt), f"b-{name}", cwd=repo)
    wts[name] = wt
leerie._require_confinement(run_dir, state_root, user)
asyncio.run(leerie._run_mise_install(wts["pin"], run_dir / "logs", st))
out = {}
for name, wt in wts.items():
    rc, tail = asyncio.run(leerie._run_streaming(
        ["bash", "-c", 'echo "which-node=$(command -v node)"; '
         'echo "which-claude=$(command -v claude)"; '
         'echo "node=$(node -v 2>/dev/null)"; '
         'echo "lts=$(/usr/local/share/mise/installs/node/lts-current/bin/'
         'node -v)"; '
         'echo "claude=$(claude --version 2>/dev/null | head -1)"'],
        grants=leerie._measurement_grants(st, wt, leerie.MEASURE_KIND),
        cwd=str(wt), timeout=300, verbosity="quiet", tail_lines=20))
    out[name] = dict(ln.split("=", 1) for ln in tail.splitlines()
                     if "=" in ln)
print("RESULT " + json.dumps(out), flush=True)
'''


def _users_env() -> dict[str, str]:
    """The environment with the user's own HOME and XDG directories:
    rootless nerdctl finds its network configuration there, and the
    session's test home has none."""
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
def test_the_real_container_runs_the_pinned_node(tmp_path):
    """In a tree pinning 22.19.0, after leerie's `mise install`, a confined
    command's bare `node` is v22.19.0; in a tree pinning nothing it is the
    baked LTS; `claude` is the vendor link and runs in every tree, including
    one pinning a version that is not installed (where a mise shim would
    try, and fail, to install it first)."""
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    (tmp_path / "driver.py").write_text(_DRIVER)
    for p in (tmp_path, tmp_path / "driver.py"):     # read as `leerie`
        p.chmod(p.stat().st_mode | 0o055)
    path = ":".join(_image_path()).replace("$PATH", BASE_PATH)
    links = _link_commands()
    prep = f"[ -e {VENDOR_BIN}/claude ] || {{ {links}; }}" if links else "true"
    rootless = Path(f"/run/user/{os.getuid()}/containerd-rootless/child_pid")
    sec = (["--security-opt", "seccomp=unconfined"]
           if rootless.is_file() else [])
    # The sandbox's mounts need the profile the launcher passes.
    if Path("/etc/apparmor.d/leerie-container").is_file():
        sec += ["--security-opt", "apparmor=leerie-container"]
    tmpfs = "rw,exec,size=2g,uid=1000,gid=1000"
    r = subprocess.run(
        ["nerdctl", "run", "--rm", *sec, "--user", "0",
         "--entrypoint", "bash",
         "-v", f"{REPO_ROOT}:/src:ro", "-v", f"{tmp_path}:/out:ro",
         "--tmpfs", f"/leerie-state:{tmpfs}",
         "--tmpfs", f"/leerie-toolchains/mise:{tmpfs}",
         image, "-c",
         f"{prep} && exec runuser -u leerie -- env HOME=/home/leerie "
         f"PATH='{path}' /usr/bin/python3 /out/driver.py "
         "/src/orchestrator/leerie.py /leerie-state/w"],
        capture_output=True, text=True, timeout=1500, env=_users_env())
    line = [ln for ln in r.stdout.splitlines() if ln.startswith("RESULT ")]
    assert line, r.stdout[-4000:] + r.stderr[-4000:]
    got = json.loads(line[0][len("RESULT "):])
    assert got["pin"]["lts"] != "v22.19.0", got   # the pin is not the LTS
    assert got["pin"]["node"] == "v22.19.0", got
    assert got["nopin"]["node"] == got["nopin"]["lts"], got
    for name in ("pin", "nopin", "absent"):
        assert got[name]["claude"].endswith("(Claude Code)"), got
        assert got[name]["which-claude"] == f"{VENDOR_BIN}/claude", got
