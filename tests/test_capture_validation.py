"""A capture names packages, never commands; and only the operator applies
one (DESIGN §6½ *A capture names packages, never commands*, *The operator
applies a capture*; review round 28, isolation E2).

Review round 28's E2 proof, turned around: a `dep_capture` answer holding
`setup_packages: ["libvips-dev", "&&", "/bin/sh", "-c", …]` was recorded,
applied to the operator's `.leerie/config.toml` by the host's EXIT trap, and
turned by the next launch into an apt `RUN` holding `&& /bin/sh -c …`. Here
each step refuses: the schema, the recorder, the apply and the launcher's
apt layer; and nothing but `leerie config --apply-capture <run>` applies.
Since 2026-11-12 a capture names no manager at all (DESIGN §6½ *The
repository image holds no dependency*)."""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.config_arm_extract import extract_config_arm
from tests.conftest import LOG_SH_SOURCE

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
# What an earlier leerie's capture carried beside the packages.
HOSTILE_INSTALL = {"manager": "make",
                   "command": "make bootstrap; echo r28 > /r28-run",
                   "copy_inputs": []}


# --- the schema --------------------------------------------------------------


def _schema_ok(value: dict) -> bool:
    jsonschema = pytest.importorskip("jsonschema")
    try:
        jsonschema.validate(value, M.SCHEMAS["dep_capture"])
    except jsonschema.ValidationError:
        return False
    return True


def test_schema_refuses_a_word_that_is_not_a_package():
    assert not _schema_ok({"setup_packages": HOSTILE_PKGS})
    assert _schema_ok({"setup_packages": ["libvips-dev", "g++", "libc6.1"]})
    assert not _schema_ok({"setup_packages": [""]})


def test_schema_names_no_manager_and_no_command():
    """The capture's only field a build reads is a package list: no
    manager, no command, no input path (DESIGN §6½ *The repository image
    holds no dependency*)."""
    props = M.SCHEMAS["dep_capture"]["properties"]
    assert set(props) == {"setup_packages", "dockerfile_notes"}
    assert M.SCHEMAS["dep_capture"]["required"] == ["setup_packages"]


# --- the code ----------------------------------------------------------------


def test_capture_problems_name_every_bad_item():
    probs = M._capture_problems(HOSTILE_PKGS)
    text = "; ".join(probs)
    for word in ("'&&'", "'/bin/sh'", "'-c'"):
        assert word in text, text
    assert "'libvips-dev'" not in text
    assert M._capture_problems(["libvips-dev"]) == []
    assert M._capture_problems("libvips-dev") == [
        "setup_packages is not a list"]


def test_a_hostile_capture_is_never_recorded(tmp_path):
    M._record_capture(tmp_path, "run-x", HOSTILE_PKGS, False)
    M._record_capture(tmp_path, "run-x", [], False)
    assert not (tmp_path / M.CAPTURED_DEPS_FILE).exists()


def test_a_recorded_capture_holds_packages_only(tmp_path):
    M._record_capture(tmp_path, "run-x", ["libvips-dev"], False)
    (entry,) = json.loads((tmp_path / M.CAPTURED_DEPS_FILE).read_text())
    assert entry == {"from_run": "run-x", "setup_packages": ["libvips-dev"],
                     "replace": False}


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
    assert M._merge_capture_into_config(cfg, HOSTILE_PKGS, False) == {}
    assert cfg.read_text() == 'build = "make"\n'


@pytest.mark.parametrize("held, named", [
    # barnacle's shape: an earlier auto-apply wrote key names as packages.
    ('setup_packages = "tini dockerfile_notes language_installs"\n',
     "'dockerfile_notes'"),
    ('setup_packages = "curl && id"\n', "'&&'"),
])
def test_apply_refuses_a_merge_into_a_value_that_already_fails(
        tmp_path, capsys, held, named):
    """The capture is valid, but a merge keeps what the file holds: a
    merged value carrying an entry that is not a capture leerie records is
    refused, naming it, and nothing is written (review round 29, execution
    L4)."""
    repo, run = _checkout(tmp_path), tmp_path / "run"
    run.mkdir()
    cfg = repo / ".leerie" / "config.toml"
    cfg.write_text(held)
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "x", "setup_packages": ["libpq-dev"],
        "replace": False}]))
    with pytest.raises(SystemExit):
        M.apply_captured_deps(run, repo)
    cap = capsys.readouterr()
    assert named in cap.out + cap.err
    assert cfg.read_text() == held
    assert (run / M.CAPTURED_DEPS_FILE).exists()


def test_apply_logs_the_change_as_a_diff(tmp_path, capsys):
    repo, run = _checkout(tmp_path), tmp_path / "run"
    run.mkdir()
    (run / M.CAPTURED_DEPS_FILE).write_text(json.dumps([{
        "from_run": "x", "setup_packages": ["libvips-dev"],
        "replace": False}]))
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
              + LOG_SH_SOURCE +
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
        "replace": False}]))
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
        "replace": False}]))
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
              + LOG_SH_SOURCE +
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
