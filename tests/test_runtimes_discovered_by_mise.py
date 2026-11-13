"""A repository's runtimes are the ones mise discovers (DESIGN §6½ *A
repository's runtimes are the ones mise discovers*).

Which files pin which runtime is mise's knowledge, never a list of
leerie's: the image enables the idiomatic version files of every tool mise
ships (`scripts/mise-system-config.sh`, from `mise registry`), the run runs
`mise install` whatever files the tree holds, and the image bakes no
runtime but leerie's own Node.

The real-container test runs only when LEERIE_TEST_CONTAINER_IMAGE names a
leerie image built from this checkout's Dockerfile (`nerdctl` on PATH,
CONTAINERD_NAMESPACE naming its namespace). It needs no network: `mise ls
--current --offline` reports a pin whether or not it is installed.
"""
from __future__ import annotations

import asyncio
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
CONFIG_SCRIPT = REPO_ROOT / "scripts" / "mise-system-config.sh"

# A registry as `mise registry` prints it (name, then its backends): core
# tools, and tools whose first backend is not core.
_REGISTRY = (
    "bun core:bun\n"
    "go core:go\n"
    "java core:java\n"
    "jq aqua:jqlang/jq ubi:jqlang/jq\n"
    "node core:node\n"
    "ruby core:ruby\n"
    "terraform aqua:hashicorp/terraform asdf:asdf-community/asdf-hashicorp\n"
    "zig core:zig\n"
)


def _fake_mise(tmp_path: Path, registry: str) -> Path:
    """A `mise` that prints `registry` for `mise registry`, with shell
    builtins only (it never resolves a program through PATH)."""
    d = tmp_path / "bin"
    d.mkdir()
    (tmp_path / "registry.txt").write_text(registry)
    m = d / "mise"
    m.write_text("#!/bin/sh\n"
                 f'while IFS= read -r l; do printf "%s\\n" "$l"; done '
                 f'< "{tmp_path}/registry.txt"\n')
    m.chmod(0o755)
    return d


def _run_config(tmp_path: Path, registry: str) -> subprocess.CompletedProcess:
    bindir = _fake_mise(tmp_path, registry)
    return subprocess.run(
        ["/bin/sh", str(CONFIG_SCRIPT), str(tmp_path / "etc" / "config.toml")],
        env={"PATH": f"{bindir}:/usr/bin:/bin"},
        capture_output=True, text=True, timeout=60)


def test_config_enables_every_tool_mise_ships_and_no_other(tmp_path):
    r = _run_config(tmp_path, _REGISTRY)
    assert r.returncode == 0, r.stderr
    text = (tmp_path / "etc" / "config.toml").read_text()
    assert text == ('[settings]\nidiomatic_version_file_enable_tools = '
                    '["bun", "go", "java", "node", "ruby", "zig"]\n')


def test_config_refuses_a_registry_without_node(tmp_path):
    """An empty or unreadable registry stops the build rather than leave
    every idiomatic file ignored."""
    r = _run_config(tmp_path, "jq aqua:jqlang/jq\n")
    assert r.returncode != 0
    assert not (tmp_path / "etc" / "config.toml").exists()


def test_the_image_writes_the_config_and_names_no_tool_list_of_its_own():
    text = DOCKERFILE.read_text()
    assert "MISE_IDIOMATIC_VERSION_FILE_ENABLE_TOOLS" not in text
    run = text.index("RUN /usr/local/lib/leerie/mise-system-config.sh")
    install = text.index("RUN mise install --system")
    assert run < install
    # leerie's own Node is the one runtime the image bakes.
    line = text[install:text.index("\n", install)]
    assert line == "RUN mise install --system node@lts"


def test_the_ec2_bake_writes_the_same_config_before_its_install():
    body = EC2_BAKE.read_text()
    run = body.index('sh "$BAKE_DIR/scripts/mise-system-config.sh"')
    install = body.index("mise install --system node@lts")
    assert run < install
    line = body[install:body.index("\n", install)]
    assert line == "mise install --system node@lts"


def _make_state(leerie, tmp_path):
    leerie_root = tmp_path / ".leerie"
    run_id = "_test-run"
    (leerie_root / "runs" / run_id / "logs").mkdir(parents=True, exist_ok=True)
    st = leerie.State(leerie_root, run_id)
    st.data = {"task": "test"}
    st.save()
    return st


def test_mise_install_runs_in_a_tree_holding_no_known_pin_file(
        leerie, tmp_path, monkeypatch):
    """A tree whose only pin is a file no list of leerie's ever named
    (`.zig-version`): `mise install` still runs, and what mise reports is
    recorded."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".zig-version").write_text("0.13.0\n")
    st = _make_state(leerie, tmp_path)
    ran: list[list[str]] = []

    async def _fake_run_streaming(cmd, **kwargs):
        ran.append(list(cmd))
        return (0, "ok")

    class _Done:
        returncode = 0
        stdout = (b'{"zig": [{"version": "0.13.0", "requested_version": '
                  b'"0.13.0", "installed": true}]}')
        stderr = b""

    async def _fake_run_proc(argv, **kwargs):
        return _Done()

    monkeypatch.setattr(leerie, "_run_streaming", _fake_run_streaming)
    monkeypatch.setattr(leerie, "_run_proc", _fake_run_proc)
    asyncio.run(leerie._run_mise_install(repo, st.run_dir / "logs", st))
    assert ["mise", "install"] in ran
    assert st.data["provision"]["mise_versions"]["zig"][0]["version"] \
        == "0.13.0"


def _users_env() -> dict[str, str]:
    env = {**os.environ, "HOME": REAL_HOME}
    for var in XDG_BASE_DIRS:
        real = os.environ.get(f"PYTEST_LEERIE_REAL_{var}", "")
        if real:
            env[var] = real
        else:
            env.pop(var, None)
    return env


_PINS = {".ruby-version": ("ruby", "3.3.9"), ".go-version": ("go", "1.23.4"),
         ".java-version": ("java", "21"), ".bun-version": ("bun", "1.1.30"),
         ".zig-version": ("zig", "0.13.0"),
         "global.json": ("dotnet", "8.0.100")}


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_the_real_image_reads_every_tools_own_version_file(tmp_path):
    """In the image, as `leerie`, mise reports the pin of each of six
    tools from that tool's own file — four of which no list of leerie's
    named."""
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    tree = tmp_path / "tree"
    tree.mkdir()
    for name, (_tool, ver) in _PINS.items():
        (tree / name).write_text(
            '{"sdk":{"version":"%s"}}\n' % ver if name == "global.json"
            else ver + "\n")
    for p in (tmp_path, tree, *tree.iterdir()):
        p.chmod(p.stat().st_mode | 0o055)
    r = subprocess.run(
        ["nerdctl", "run", "--rm", "--user", "0", "--entrypoint", "bash",
         "-v", f"{tree}:/tree:ro", image, "-c",
         "cd /tree && exec runuser -u leerie -- env HOME=/home/leerie "
         "mise ls --current --offline"],
        capture_output=True, text=True, timeout=300, env=_users_env())
    got = {}
    for line in r.stdout.splitlines():
        parts = line.split()
        if len(parts) >= 2:
            got[parts[0]] = re.sub(r"[^0-9.]", "", parts[1])
    want = {tool: ver for tool, ver in _PINS.values()}
    assert got == want, r.stdout + r.stderr


@pytest.mark.skipif(not os.environ.get("LEERIE_TEST_CONTAINER_IMAGE")
                    or shutil.which("nerdctl") is None,
                    reason="set LEERIE_TEST_CONTAINER_IMAGE to a leerie image")
def test_mise_trusts_the_runs_trees_and_reads_its_tools_file(leerie,
                                                            tmp_path):
    """A tree beneath the run directory pinning in `mise.toml`, and the
    run's tools file there, are read by mise with the environment the run
    gives every process; without the trust mise refuses the tree's file
    and exits 1."""
    image = os.environ["LEERIE_TEST_CONTAINER_IMAGE"]
    run = tmp_path / "run"
    tree = run / "worktrees" / "t"
    tree.mkdir(parents=True)
    (tree / "mise.toml").write_text('[tools]\nzig = "0.13.0"\n')
    (run / leerie.MISE_TOOLS_NAME).write_text('[tools]\ngo = "1.23.4"\n')
    for p in (tmp_path, run, run / "worktrees", tree, tree / "mise.toml",
              run / leerie.MISE_TOOLS_NAME):
        p.chmod(p.stat().st_mode | 0o055)
    env: dict[str, str] = {}
    leerie._trust_run_mise_configs(env, Path("/run-dir"))

    def _ls(extra: list[str]) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["nerdctl", "run", "--rm", "--user", "0", "--entrypoint", "bash",
             "-v", f"{run}:/run-dir:ro", image, "-c",
             "cd /run-dir/worktrees/t && exec runuser -u leerie -- env "
             "HOME=/home/leerie " + " ".join(extra) +
             f" MISE_GLOBAL_CONFIG_FILE=/run-dir/"
             f"{leerie.MISE_TOOLS_NAME} mise ls --current --offline"],
            capture_output=True, text=True, timeout=300, env=_users_env())

    trusted = _ls([f"{k}={v}" for k, v in env.items()])
    assert trusted.returncode == 0, trusted.stdout + trusted.stderr
    assert "go" in trusted.stdout.split(), trusted.stdout
    assert "zig" in trusted.stdout.split(), trusted.stdout
    untrusted = _ls([])
    assert untrusted.returncode != 0
    assert "not trusted" in untrusted.stderr


def test_the_run_trusts_its_own_directory_and_nothing_else(leerie, tmp_path):
    env = {"MISE_TRUSTED_CONFIG_PATHS": "/elsewhere"}
    leerie._trust_run_mise_configs(env, tmp_path / "run")
    assert env["MISE_TRUSTED_CONFIG_PATHS"] == str(tmp_path / "run")
