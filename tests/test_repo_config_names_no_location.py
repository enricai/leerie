"""No host path comes from a repository (DESIGN §6 *No host path comes from a
repository*; review round 27, isolation I6).

`leerie.toml` is repository content: a cloned repository's, or a run
branch's the operator merged (a worker may commit a root `leerie.toml`).
Five of its keys used to name a location below only the operator's flag and
environment. `log_file = "~/.bashrc"` made the launcher create that file and
append the run's whole output to it, worker text included. `state_dir =
".lstate"` put the state root inside the checkout, where every worker's read
of `/work` reached it under a second name. `inspect_dirs` chose a host
directory to mount, and `judge_dir` / `heal_dir` a path joined to the run
directory.

The reviewer's proof ran the launcher's resolution block and tee-target check
against such a checkout and got TEE = `$HOME/.bashrc` (created) and STATE =
`<checkout>/.lstate`. Inverted here:

- the real launcher refuses a `leerie.toml` setting any of the five, names
  each key's operator-side replacement, and creates nothing;
- the resolution block, run on its own, reads none of them;
- the launcher refuses a state root at, inside or above the checkout, from
  any tier;
- the launcher's list is the orchestrator's `REPO_CONFIG_LOCATION_KEYS`,
  `_read_toml_key` refuses those names, and nothing reads them from the file.
"""
from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT

LAUNCHER = REPO_ROOT / "leerie"
LEERIE_PY = REPO_ROOT / "orchestrator" / "leerie.py"


def _launcher_keys() -> list[str]:
    src = LAUNCHER.read_text()
    body = src[src.index("_checkout_config_location_keys() {"):]
    body = body[:body.index("\n}\n")]
    m = re.search(r"for _k in ([a-z_ ]+); do", body)
    assert m, "the launcher's location-key loop moved"
    return m.group(1).split()


def _env(home: Path, **extra: str) -> dict[str, str]:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith("LEERIE_")}
    env.update(HOME=str(home), **extra)
    return env


def _launch(checkout: Path, home: Path, *args: str,
            **extra: str) -> subprocess.CompletedProcess:
    """The real launcher, started from the checkout as an operator does."""
    return subprocess.run([str(LAUNCHER), *args], cwd=checkout,
                          env=_env(home, **extra), capture_output=True,
                          text=True, timeout=120)


@pytest.fixture
def site(tmp_path: Path) -> tuple[Path, Path]:
    home = tmp_path / "home"
    home.mkdir()
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    return checkout, home


def test_the_launcher_list_is_the_orchestrators(leerie):
    assert _launcher_keys() == list(leerie.REPO_CONFIG_LOCATION_KEYS)


@pytest.mark.parametrize("key,value,replacement", [
    ("log_file", "~/.bashrc", "LEERIE_LOG_FILE"),
    ("state_dir", ".lstate", "LEERIE_STATE_DIR"),
    ("inspect_dirs", "~/.ssh", "LEERIE_INSPECT_DIRS"),
    ("judge_dir", "../../escape", "LEERIE_JUDGE_DIR"),
    ("heal_dir", "/leerie-host/x", "LEERIE_HEAL_DIR"),
])
def test_a_location_key_in_leerie_toml_stops_the_launcher(
        site, key, value, replacement):
    checkout, home = site
    (checkout / "leerie.toml").write_text(f'{key} = "{value}"\n')
    r = _launch(checkout, home, "list")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert f"leerie.toml sets {key}" in r.stderr
    assert re.search(rf"^\s*{key}\s+→\s+.*{replacement}", r.stderr, re.M), \
        r.stderr
    assert not (home / ".bashrc").exists()
    assert not (checkout / ".lstate").exists()
    assert not (home / ".leerie").exists()


def test_every_key_set_is_named(site):
    checkout, home = site
    (checkout / "leerie.toml").write_text(
        'state_dir = ".lstate"\n# log_file = "x"\nlog_file = "~/.bashrc"\n')
    r = _launch(checkout, home, "list")
    assert r.returncode == 1
    assert "leerie.toml sets state_dir log_file." in r.stderr


def test_other_keys_and_comments_pass(site):
    """Control: a leerie.toml without a location key does not stop `list`,
    and a commented-out key is not a setting."""
    checkout, home = site
    (checkout / "leerie.toml").write_text(
        'runtime = "local"\n# state_dir = ".lstate"\n')
    r = _launch(checkout, home, "list")
    assert r.returncode == 0, r.stderr
    assert (home / ".leerie" / "checkout").is_dir()


def test_the_resolution_block_reads_no_location_from_the_file(tmp_path):
    """The reviewer's harness: the launcher's own resolution block, from
    `_state_dir_default` through `export LEERIE_LOG_FILE_RESOLVED`, and its
    tee-target check, against a checkout whose leerie.toml names
    `~/.bashrc` and a state root inside it. Neither is taken."""
    src = LAUNCHER.read_text().splitlines()
    start = next(i for i, ln in enumerate(src)
                 if ln.startswith("_state_dir_default() {"))
    end = next(i for i, ln in enumerate(src)
               if ln.startswith("export LEERIE_LOG_FILE_RESOLVED"))
    block = "\n".join(src[start:end + 1]) + r'''
_log_tee_target=""
if : >> "$LEERIE_LOG_FILE_RESOLVED" 2>/dev/null; then
  _log_tee_target="$LEERIE_LOG_FILE_RESOLVED"
fi
printf 'STATE=%s\nTEE=%s\n' "$LEERIE_STATE_HOST_DIR" "$_log_tee_target"
'''
    home = tmp_path / "home"
    (home / ".leerie" / "checkout" / "logs").mkdir(parents=True)
    co = tmp_path / "checkout"
    co.mkdir()
    (co / "leerie.toml").write_text(
        'log_file = "~/.bashrc"\nstate_dir = ".lstate"\n')
    out = subprocess.run(
        ["/bin/bash", "-c", "set -euo pipefail\n" + block, "leerie"],
        cwd="/", env={**_env(home), "USER_REPO": str(co)},
        capture_output=True, text=True, check=True).stdout
    got = dict(ln.split("=", 1) for ln in out.splitlines())
    assert got["STATE"] == str(home / ".leerie" / "checkout")
    assert got["TEE"].startswith(str(home / ".leerie" / "checkout" / "logs"))
    assert not (home / ".bashrc").exists()


@pytest.mark.parametrize("where", ["inside", "equal", "above"])
@pytest.mark.parametrize("tier", ["env", "flag"])
def test_the_launcher_refuses_a_state_root_overlapping_the_checkout(
        site, where, tier):
    checkout, home = site
    target = {"inside": checkout / ".lstate", "equal": checkout,
              "above": checkout.parent}[where]
    if tier == "env":
        r = _launch(checkout, home, "list", LEERIE_STATE_DIR=str(target))
    else:
        r = _launch(checkout, home, "list", f"--state-dir={target}")
    assert r.returncode == 1, (r.stdout, r.stderr)
    assert "overlaps the checkout" in r.stderr
    assert "LEERIE_STATE_DIR" in r.stderr
    assert not (checkout / ".lstate").exists()
    assert not (target / ".owner").exists()


def test_a_relative_state_root_is_anchored_at_the_checkout_and_refused(site):
    checkout, home = site
    r = _launch(checkout, home, "list", LEERIE_STATE_DIR="sub/state")
    assert r.returncode == 1
    assert "overlaps the checkout" in r.stderr


def test_a_link_into_the_checkout_is_refused_by_real_path(site, tmp_path):
    checkout, home = site
    (checkout / "s").mkdir()
    link = tmp_path / "elsewhere"
    link.symlink_to(checkout / "s")
    r = _launch(checkout, home, "list", LEERIE_STATE_DIR=str(link))
    assert r.returncode == 1
    assert "overlaps the checkout" in r.stderr


def test_a_sibling_state_root_passes(site, tmp_path):
    """Control: a name that merely starts with the checkout's is not
    beneath it."""
    checkout, home = site
    r = _launch(checkout, home, "list",
                LEERIE_STATE_DIR=str(tmp_path / "checkout-state"))
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize("key", ["state_dir", "log_file", "inspect_dirs",
                                 "judge_dir", "heal_dir"])
def test_the_orchestrator_never_reads_a_location_key(leerie, tmp_path, key):
    (tmp_path / "leerie.toml").write_text(f"{key} = x\n")
    with pytest.raises(ValueError, match="names a location"):
        leerie._read_toml_key(tmp_path / "leerie.toml", key)


def test_no_resolver_names_a_location_key_as_its_file_key():
    src = LEERIE_PY.read_text()
    for key in ("state_dir", "log_file", "inspect_dirs", "judge_dir",
                "heal_dir"):
        assert f'file_key="{key}"' not in src
        assert not re.search(rf'_read_toml_key\([^)]*"{key}"', src)


def test_the_launcher_reads_no_location_key_from_leerie_toml():
    """Every launcher read of a leerie.toml key spells it as a grep/awk/sed
    pattern `[[:space:]]*<key>` or hands it to a CLI>env>toml helper; no
    location key appears in either shape."""
    src = LAUNCHER.read_text()
    for key in _launcher_keys():
        assert not re.search(rf"\[\[:space:\]\]\*{key}\b", src), key
        assert not re.search(
            rf"_resolve_(?:ec2|seed)_knob\s+\S+\s+\S+\s+{key}\b", src), key
