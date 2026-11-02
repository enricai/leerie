"""DESIGN §6 *The host never runs a program the checkout chose*, part 3: the
machine reads the submodule paths as the host does (review round 28, exec L2).

The host lists submodules NUL-safely and names each bundle by the hex of its
display path, but the three machine-side scripts (the Fly seed, the Fly
inspect-dir seed, the EC2 seed) listed them with
`git submodule status | awk '{print $2}' | while read sm`. A path with a space
became its first word, matched no bundle, so its own (unreachable) URL was
cloned and the seed failed. A nested submodule was uploaded but never checked
out: the list was one level deep and `submodule update --recursive` ran
without `--init`.

The reviewer's proof, inverted: each shipped seed runs against a repository
holding a whitespace path that is also a command substitution and a quote, a
plain path, and a submodule with a nested one, with every submodule's own URL
deleted before the seed, as on the machine. Each machine script runs locally
through the transport stubs the seed tests already use.
"""
from __future__ import annotations

import functools
import re
import subprocess
from pathlib import Path

import pytest

from tests.conftest import run_bash, run_git_out
from tests.stub_helpers import _make_stub_timeout
from tests.test_ec2_seed_repo import (_base_env, _make_stub_aws,
                                      _make_stub_ssh)
from tests.test_seed_inspect_dirs import \
    _make_stub_flyctl as _make_inspect_flyctl
from tests.test_seed_repo_sh import _make_stub_flyctl

REPO = Path(__file__).resolve().parent.parent
REMOTE = REPO / "scripts" / "remote"
WEIRD = "libs/we ird\t$(touch PWNED)'q\""

_git = functools.partial(run_git_out, Path("/"), config=(
    "user.email=v@x.invalid", "user.name=v", "protocol.file.allow=always"))


def _repo(path: Path, marker: str) -> Path:
    _git("init", "-q", "-b", "main", str(path))
    (path / "marker").write_text(marker)
    _git("-C", str(path), "add", "marker")
    _git("-C", str(path), "commit", "-q", "-m", marker)
    return path


def _gitmodules(entries: dict[str, Path]) -> str:
    out = ""
    for i, (path, url) in enumerate(entries.items()):
        quoted = path.replace("\\", "\\\\").replace("\t", "\\t") \
            .replace('"', '\\"')
        out += f'[submodule "m{i}"]\n\tpath = "{quoted}"\n\turl = {url}\n'
    return out


def _add(top: Path, entries: dict[str, Path]) -> None:
    (top / ".gitmodules").write_text(_gitmodules(entries))
    _git("-C", str(top), "add", ".gitmodules")
    for path, url in entries.items():
        sha = _git("-C", str(url), "rev-parse", "HEAD")
        _git("-C", str(top), "update-index", "--add", "--cacheinfo",
             f"160000,{sha},{path}")
    _git("-C", str(top), "commit", "-q", "-m", "submodules")


@pytest.fixture
def checkout(tmp_path: Path) -> tuple[Path, dict[str, str]]:
    """The checkout and each submodule's display path -> its HEAD. The
    submodules' own URLs are deleted once the checkout is populated."""
    src = tmp_path / "src"
    plain = _repo(src / "plain", "plain")
    weird = _repo(src / "weird", "weird")
    deep = _repo(src / "deep", "deep")
    mid = _repo(src / "mid", "mid")
    _add(mid, {"deep": deep})
    top = _repo(tmp_path / "top", "top")
    _add(top, {"libs/plain": plain, WEIRD: weird, "libs/c": mid})
    _git("-C", str(top), "submodule", "update", "--init", "--recursive")
    heads = {p: _git("-C", str(top / p), "rev-parse", "HEAD")
             for p in ("libs/plain", WEIRD, "libs/c", "libs/c/deep")}
    subprocess.run(["/bin/rm", "-rf", str(src)], check=True, timeout=60)
    return top, heads


def _assert_checked_out(tree: Path, heads: dict[str, str],
                        tmp_path: Path) -> None:
    for path, head in heads.items():
        assert (tree / path / "marker").is_file(), path
        assert _git("-C", str(tree / path), "rev-parse", "HEAD") == head, path
    assert not list(tmp_path.rglob("PWNED"))


def test_the_fly_seed_checks_out_every_submodule(
        tmp_path: Path, checkout: tuple[Path, dict[str, str]]) -> None:
    top, heads = checkout
    dest = tmp_path / "machine"
    dest.mkdir()
    _make_stub_flyctl(tmp_path / "flyctl", tmp_path / "log", dest)
    _make_stub_timeout(tmp_path)
    r = run_bash(f"source {REMOTE / 'seed-repo.sh'}; seed_repo_clone",
                 env={"LEERIE_MACHINE_ID": "m", "LEERIE_FLY_APP": "leerie",
                      "USER_REPO": str(top),
                      "PATH": f"{tmp_path}:/usr/bin:/bin"}, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    _assert_checked_out(dest / "work", heads, tmp_path)


def test_the_ec2_seed_checks_out_every_submodule(
        tmp_path: Path, checkout: tuple[Path, dict[str, str]]) -> None:
    top, heads = checkout
    dest = tmp_path / "instance"
    dest.mkdir()
    _make_stub_aws(tmp_path / "aws", tmp_path / "log", dest)
    _make_stub_ssh(tmp_path / "ssh", tmp_path / "log", dest)
    _make_stub_timeout(tmp_path)
    r = run_bash(f"source {REMOTE / 'ec2-seed-repo.sh'}; ec2_seed_repo_clone",
                 env=_base_env(tmp_path, top), cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    _assert_checked_out(dest / "work", heads, tmp_path)


def test_the_inspect_dir_seed_checks_out_every_submodule(
        tmp_path: Path, checkout: tuple[Path, dict[str, str]]) -> None:
    top, heads = checkout
    dest = tmp_path / "machine"
    dest.mkdir()
    _make_inspect_flyctl(tmp_path / "flyctl", tmp_path / "log", dest)
    _make_stub_timeout(tmp_path)
    r = run_bash(f"source {REMOTE / 'seed-repo.sh'}; seed_inspect_dirs",
                 env={"LEERIE_MACHINE_ID": "m", "LEERIE_FLY_APP": "leerie",
                      "USER_REPO": str(tmp_path),
                      "LEERIE_INSPECT_HOST_TARGETS": f"{top}\t/inspect/top",
                      "PATH": f"{tmp_path}:/usr/bin:/bin"}, cwd=tmp_path)
    assert r.returncode == 0, r.stderr
    _assert_checked_out(dest / "inspect" / "top", heads, tmp_path)


def test_a_submodule_without_a_bundle_is_never_fetched(tmp_path: Path) -> None:
    """The snippet itself: a submodule the host did not bundle stays
    uninitialised rather than being cloned from its own URL."""
    other = _repo(tmp_path / "other", "other")
    top = _repo(tmp_path / "top", "top")
    _add(top, {"x": other})
    clone = tmp_path / "clone"
    _git("clone", "-q", str(top), str(clone))
    subs = tmp_path / "subs"
    subs.mkdir()
    snippet = run_bash(f"source {REMOTE / 'seed-common.sh'}; "
                       f"_seed_machine_submodules {subs}").stdout
    r = subprocess.run(["/bin/bash", "-c", "set -e\n" + snippet], cwd=clone,
                       capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert not (clone / "x" / "marker").exists()


def test_every_machine_script_runs_the_shared_snippet() -> None:
    """Three machine scripts, one reader; none parses `submodule status`."""
    texts = [(REMOTE / f).read_text()
             for f in ("seed-repo.sh", "ec2-seed-repo.sh")]
    assert sum(t.count("$(_seed_machine_submodules ") for t in texts) == 3
    for t in texts:
        assert "git submodule status" not in t
        assert "while read sm" not in t
    fly = texts[0]
    assert "-C \"bash -c '$_machine_script'\"" in fly
    assert "-C \"bash -c '$clone_script'\"" in fly
    snippet = run_bash(f"source {REMOTE / 'seed-common.sh'}; "
                       "_seed_machine_submodules /s").stdout
    assert "'" not in snippet
    assert re.search(r"git config -z --file \.gitmodules --get-regexp", snippet)
