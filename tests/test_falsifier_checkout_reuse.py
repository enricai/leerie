"""The falsifier's checkouts are kept for the run and moved to the next
commit with their install kept, the way every other kept tree is (DESIGN §8
*The falsifier's checkouts are kept too*).

Real git, the run's own repository and the real `_falsifier_kept_checkout`
(a ref-less clone the first time, `_reuse_or_rebuild_kept_tree(
writer_checkout=True)` and `_move_view` after), with a recipe whose install
is incremental, as a package manager's is: it writes
`node_modules/dep/index.js` from `package.json` only when that differs, and
counts its runs under its own output. A falsifier worker writes its lane
with every permission, so what one may leave there — files, an install
file changed, commits, refs and configuration in the clone's own `.git` —
is left by hand between calls, and the next call must see a checkout a
fresh clone would be.
"""
from __future__ import annotations

import asyncio
import os
import shutil
import socket
import struct
import subprocess
import sys
from pathlib import Path

import pytest

from tests.test_eval_tree_reuse import INSTALL, _commit, _count
from tests.test_run_repo import _git, _setup_run, _St, _user_repo

pytestmark = pytest.mark.real_planning_worktree


@pytest.fixture()
def env(leerie, tmp_path, monkeypatch):
    repo = _user_repo(tmp_path)
    (repo / "src").mkdir()
    (repo / "src" / "a.txt").write_text("1")
    (repo / "package.json").write_text('{"name": "x"}\n')
    (repo / ".gitignore").write_text("node_modules/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "app")
    state = tmp_path / "state"
    monkeypatch.chdir(repo)
    monkeypatch.setenv("LEERIE_STATE_DIR", str(state))
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.delenv("FAIL_INSTALL", raising=False)
    st = _St(repo, state)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL],
         "working_dir": "."}]}
    staging = _setup_run(leerie, st)
    caps = dict(leerie.DEFAULT_CAPS)
    caps["worker_timeout_sec"] = 60
    yield st, staging, caps
    for d in (leerie._VIEWS, leerie._VIEW_INSTALLS, leerie._KEPT_TREES,
              leerie._KEPT_INSTALLS, leerie._DEPS_KEPT,
              leerie._INSTALL_OUTPUTS, leerie._CLONE_CACHES,
              leerie._CLONE_TIMES):
        d.clear()


def _prepare(leerie, st, caps, name: str, tip: str, *,
             writer: str | None) -> Path:
    """What `_falsify_items` does for one checkout: keep it at `tip`, then
    run the recipe there."""
    async def go() -> Path:
        wt = await leerie._falsifier_kept_checkout(st, name, tip,
                                                   writer=writer)
        assert wt is not None
        assert await leerie._ensure_worktree_deps(
            str(wt), st, caps, log_path=st.run_dir / "falsifier-setup.log",
            verbosity="quiet", label_prefix=name,
            log_prefix="delivery detection")
        return wt
    return asyncio.run(go())


def _attrs(p: Path) -> list[str]:
    """The extended attributes of `p` a worker could set: a security label
    (SELinux) is listed on every file and never anyone's to remove."""
    return [a for a in os.listxattr(p)
            if a.startswith(("user.", "system.posix_acl_"))]


def _dep(wt: Path) -> Path:
    return wt / "node_modules" / "dep" / "index.js"


def _own(wt: Path, *args: str) -> subprocess.CompletedProcess:
    """git in the clone's own `.git`, the one its worker writes."""
    return subprocess.run(["git", "-C", str(wt), *args], capture_output=True,
                          text=True, check=False)


def _like_a_fresh_clone(leerie, wt: Path, tip: str) -> None:
    """What a fresh `_ref_less_clone` gives the next worker: its own `.git`
    detached at `tip`, no ref and no remote, a clean tree; and leerie's own
    git directory for it at `tip` too."""
    assert (wt / ".git").is_dir()
    assert _own(wt, "rev-parse", "HEAD").stdout.strip() == tip
    assert _own(wt, "symbolic-ref", "-q", "HEAD").returncode != 0
    assert _own(wt, "for-each-ref").stdout == ""
    assert _own(wt, "remote").stdout == ""
    assert _own(wt, "status", "--porcelain").stdout == ""
    gd = leerie._clone_git_dir(wt)
    assert subprocess.run(
        ["git", "--git-dir", str(gd), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=False).stdout.strip() == tip


@pytest.mark.parametrize("name,writer", [("falsifier-confirm", None),
                                         ("falsifier-lane0", "falsifier")])
def test_a_later_call_keeps_the_install(pin, leerie, env, name, writer):
    """A later call at a new commit with the same install inputs moves the
    checkout there with its install in place (the same file, the recipe
    rerun incrementally over it), as a fresh clone of the new commit would
    present it, with the lane's worker cache recorded again."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, name, a, writer=writer)
    assert leerie._run_tree_location(wt)[1].parent.name == \
        leerie.CLONE_GITS_NAME                       # a clone, not a worktree
    same = pin(_dep(wt))
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    wt2 = _prepare(leerie, st, caps, name, b, writer=writer)
    assert wt2 == wt
    assert same()            # the install was kept
    assert _count(wt) == 2                            # rerun over it
    assert (wt / "src" / "a.txt").read_text() == "3"
    _like_a_fresh_clone(leerie, wt, b)
    key = os.path.realpath(wt)
    if writer is None:
        assert key not in leerie._CLONE_CACHES
    else:
        assert leerie._CLONE_CACHES[key] == leerie._worker_cache_dir(
            st.run_dir, writer)


def test_what_a_writer_left_is_gone(pin, leerie, env):
    """Everything a falsifier worker may leave in its lane that the install
    did not make — a file anywhere, one under the install's outputs, a
    commit, a branch, a remote, a hook and configuration in the clone's own
    `.git` — is gone at the next call, and the install is still the one it
    made."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    (wt / "src" / "a.txt").write_text("edited")
    (wt / "falsify_probe").mkdir()
    (wt / "falsify_probe" / "probe.test.ts").write_text("x")
    (wt / "node_modules" / "left.js").write_text("x")
    for args in (("-c", "user.name=w", "-c", "user.email=w@w", "commit",
                  "-qam", "worker"),
                 ("branch", "mine"), ("remote", "add", "o", "/nowhere"),
                 ("config", "core.hooksPath", "/nowhere")):
        assert _own(wt, *args).returncode == 0, args
    (wt / ".git" / "hooks" / "post-checkout").write_text("#!/bin/sh\n")
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()            # reused, not remade
    assert (wt / "src" / "a.txt").read_text() == "2"
    assert not (wt / "falsify_probe").exists()
    assert not (wt / "node_modules" / "left.js").exists()
    assert _own(wt, "config", "core.hooksPath").stdout == ""
    assert not (wt / ".git" / "hooks" / "post-checkout").exists()
    _like_a_fresh_clone(leerie, wt, b)


def test_a_writer_that_changed_the_install_makes_it_afresh(pin, leerie, env):
    """A file the install made that the writer changed refuses the reuse:
    the checkout is made again and installed from nothing."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    _dep(wt).write_text(_dep(wt).read_text())         # same bytes, touched
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert not same()
    assert _count(wt) == 1
    _like_a_fresh_clone(leerie, wt, b)


def test_a_changed_manifest_reinstalls(leerie, env):
    """A commit whose install inputs changed is a different install: the
    checkout is made afresh, never moved."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-confirm", a, writer=None)
    b = _commit(leerie, staging, {"package.json": '{"name": "y"}\n'})
    _prepare(leerie, st, caps, "falsifier-confirm", b, writer=None)
    assert _count(wt) == 1
    assert _dep(wt).read_text() == '{"name": "y"}\n'
    _like_a_fresh_clone(leerie, wt, b)


def test_a_moved_checkout_measures_with_fresh_stores(pin, leerie, env):
    """The move gives the confirm checkout (whose stores are its own) a new
    measurement id, so what the last commit's code left in its stores never
    reaches the next measurement."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-confirm", a, writer=None)
    same = pin(_dep(wt))
    dirs = leerie._run_tree_location(wt)
    first = leerie._measure_dir_at(st.run_dir, wt, dirs)
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    _prepare(leerie, st, caps, "falsifier-confirm", b, writer=None)
    assert same()                                  # moved, not remade
    assert leerie._measure_dir_at(
        st.run_dir, wt, leerie._run_tree_location(wt)) != first


def test_a_checkout_kept_for_another_writer_is_made_afresh(pin, leerie, env):
    """A checkout installed on one writer's cache set is not the one another
    writer, or leerie alone, would get fresh: it is made again, its install
    run from nothing, and the cache recorded is the new writer's."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    assert os.path.realpath(wt) in leerie._CLONE_CACHES
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer=None)
    assert not same()
    assert _count(wt) == 1
    assert os.path.realpath(wt) not in leerie._CLONE_CACHES
    _like_a_fresh_clone(leerie, wt, b)


def test_what_git_would_not_undo_is_undone(pin, leerie, env, tmp_path):
    """What a writer may leave that `git checkout --force` and `git clean`
    do not undo is undone too: a repository inside a tracked directory (git
    lists no `.git` as untracked), a tracked file git reads as unchanged
    only through attributes the writer added (CRLF line ends under its own
    `eol=crlf`), the modes it took away from a tracked file and
    directory, an extended attribute on one, and a FIFO named as a file
    git would read (`.gitignore`), which would block a git command opening
    it. A tracked directory it removed, or made a link to one outside, is
    the checkout's again, and nothing outside is touched."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "line\n", "docs/x.txt": "d",
                                  "lib/y.txt": "l"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    assert _own(wt, "-C", "src", "init", "-q").returncode == 0
    (wt / "src" / "c.txt").write_bytes(b"line\r\n")
    (wt / "src" / ".gitattributes").write_text("c.txt text eol=crlf\n")
    os.chmod(wt / "src" / "a.txt", 0o444)
    # Entries `git clean` passes over in a tracked directory.
    os.mkfifo(wt / "src" / "pipe")
    os.mkfifo(wt / "src" / ".gitignore")
    try:
        os.setxattr(wt / "src", "user.left", b"x")
        xattr = True
    except OSError:               # a file system without user attributes
        xattr = False
    sock = socket.socket(socket.AF_UNIX)
    here = os.getcwd()
    os.chdir(wt / "src")              # a socket's path is bounded in length
    try:
        sock.bind("app.sock")
    finally:
        os.chdir(here)
        sock.close()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.txt").write_text("theirs")
    os.chmod(outside, 0o700)
    shutil.rmtree(wt / "docs")
    os.symlink(outside, wt / "docs")
    shutil.rmtree(wt / "lib")
    # An untracked directory it left without its write bit: the clean
    # removes what is in it once the owner's bits are back.
    (wt / "kept").mkdir()
    (wt / "kept" / "f.txt").write_text("x")
    os.chmod(wt / "kept", 0o500)
    # Bits a fresh checkout never gives, on the tracked root.
    os.chmod(wt, 0o2777)
    fresh = os.stat(wt / "src").st_mode & 0o7777
    # A file a directory without its read bit hides from `git clean`.
    (wt / "src" / "planted.txt").write_text("x")
    os.chmod(wt / "src", 0o311)
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert not (wt / "src" / "planted.txt").exists()
    assert not os.path.lexists(wt / "src" / "pipe")
    assert not os.path.lexists(wt / "src" / "app.sock")
    assert not os.path.lexists(wt / "src" / ".gitignore")
    assert not os.path.lexists(wt / "kept")
    assert not (wt / "docs").is_symlink()
    assert (wt / "docs" / "x.txt").read_text() == "d"
    assert (wt / "lib" / "y.txt").read_text() == "l"
    assert (outside / "x.txt").read_text() == "theirs"
    assert os.stat(outside).st_mode & 0o7777 == 0o700
    assert not xattr or _attrs(wt / "src") == []
    assert os.stat(wt / "src").st_mode & 0o7777 == fresh
    assert os.stat(wt).st_mode & 0o7777 == fresh
    assert not os.path.lexists(wt / "src" / ".git")
    assert (wt / "src" / "c.txt").read_bytes() == b"line\n"
    assert not (wt / "src" / ".gitattributes").exists()
    assert os.stat(wt / "src" / "a.txt").st_mode & 0o200
    assert os.stat(wt / "src").st_mode & 0o200
    _like_a_fresh_clone(leerie, wt, b)


def test_an_attribute_on_an_install_directory_is_removed(pin, leerie, env):
    """A default ACL its writer set on a directory the install made moves
    neither its mode nor its inode, yet decides the mode of what the next
    probe makes under it: the move removes it, and every other attribute
    there, and keeps the install."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "1"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    dep = wt / "node_modules" / "dep"
    # Owner r-x, group and other nothing: `ACL_USER_OBJ`, `ACL_GROUP_OBJ`,
    # `ACL_OTHER`, after the version-2 header.
    acl = struct.pack("<I", 2) + b"".join(
        struct.pack("<HHI", tag, perm, 0xFFFFFFFF)
        for tag, perm in ((0x01, 5), (0x04, 0), (0x20, 0)))
    try:
        os.setxattr(dep, "system.posix_acl_default", acl)
        os.setxattr(wt / "node_modules", "user.left", b"x")
    except OSError:
        pytest.skip("this file system keeps no ACL or user attribute")
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert _attrs(dep) == []
    assert _attrs(wt / "node_modules") == []
    (dep / "made").mkdir()
    (dep / "made" / "f").write_text("x")           # writable, as when fresh
    _like_a_fresh_clone(leerie, wt, b)


def test_an_attribute_above_a_nested_output_is_removed(leerie, tmp_path):
    """An output below a directory the clean keeps for it (`build/cache`
    under an untracked `build`): that directory's attributes go too, as do
    those under the output, and a link on the way is never followed."""
    wt = tmp_path / "wt"
    (wt / "build" / "cache" / "sub").mkdir(parents=True)
    away = tmp_path / "away"
    away.mkdir()
    (wt / "gen").symlink_to(away)
    try:
        for d in ("build", "build/cache", "build/cache/sub"):
            os.setxattr(wt / d, "user.left", b"x")
        os.setxattr(away, "user.theirs", b"x")
    except OSError:
        pytest.skip("this file system keeps no user attribute")
    assert leerie._strip_output_xattrs(wt, ["build/cache", "gen/x"])
    for d in ("build", "build/cache", "build/cache/sub"):
        assert _attrs(wt / d) == []
    assert _attrs(away) == ["user.theirs"]


def test_an_input_the_commit_did_not_change_stays_older(pin, leerie, env):
    """Every tracked file is written anew, then each the commit did not
    change is given back the time it had when leerie last wrote it: an
    unchanged input is still older than what the install made from it, as
    after a fresh clone and install, and a file the commit changed has a
    time of its own, which it keeps while it stays unchanged (a cache keyed
    on a file's size and time never sees two contents under one key). No
    time its writer set is kept."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "1"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))

    def mt(rel: str) -> int:
        return os.stat(wt / rel).st_mtime_ns
    pkg, first = mt("package.json"), mt("src/c.txt")
    assert pkg < mt("node_modules/dep/index.js")
    # Its writer sets times of its own: one far ahead, one far back.
    os.utime(wt / "package.json", (4_000_000_000, 4_000_000_000))
    os.utime(wt / "src" / "c.txt", (1, 1))
    b = _commit(leerie, staging, {"src/c.txt": "2"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert mt("package.json") == pkg
    assert pkg < mt("node_modules/dep/index.js")
    assert (wt / "src" / "c.txt").read_text() == "2"
    second = mt("src/c.txt")
    assert second > first
    os.utime(wt / "src" / "c.txt", (1, 1))
    c = _commit(leerie, staging, {"src/d.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", c, writer="falsifier")
    assert same()
    assert mt("src/c.txt") == second               # its own, kept
    assert mt("package.json") == pkg


def test_changed_attributes_keep_no_time(pin, leerie, env):
    """A commit that changes only a `.gitattributes` can write a file it
    did not change as other bytes (line ends here): no file keeps its time,
    so that one is newer than before."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "one\ntwo\n"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    was = os.stat(wt / "src" / "c.txt").st_mtime_ns
    b = _commit(leerie, staging,
                {"src/.gitattributes": "*.txt text eol=crlf\n"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert (wt / "src" / "c.txt").read_bytes() == b"one\r\ntwo\r\n"
    assert os.stat(wt / "src" / "c.txt").st_mtime_ns > was


def test_a_move_that_fails_leaves_a_checkout_made_afresh(
        pin, leerie, env, monkeypatch):
    """A step of the move that fails is no move: the checkout is removed
    and made afresh at the new commit, and a later call moves that one."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "1"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    real = leerie._rewrite_clone_files
    calls: list[str] = []

    def refuse(wt_: Path, tip: str) -> bool:
        calls.append(tip)
        return False
    monkeypatch.setattr(leerie, "_rewrite_clone_files", refuse)
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert calls == [b]                       # the move got that far
    assert not same()
    _like_a_fresh_clone(leerie, wt, b)
    monkeypatch.setattr(leerie, "_rewrite_clone_files", real)
    again = pin(_dep(wt))
    c = _commit(leerie, staging, {"src/d.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", c, writer="falsifier")
    assert again()
    _like_a_fresh_clone(leerie, wt, c)


def test_an_output_named_like_a_pattern_keeps_only_itself(pin, leerie, env):
    """The clean keeps each output by its literal name: an output `gen?`
    does not keep a `genX` its writer left beside it."""
    st, staging, caps = env
    (staging / ".gitignore").write_text("node_modules/\ngen?/\n")
    a = _commit(leerie, staging, {"src/c.txt": "1"})
    st.data["provision"]["recipe"].append(
        {"kind": "install", "working_dir": ".", "command": [
            sys.executable, "-c",
            "import pathlib; p = pathlib.Path('gen?'); p.mkdir(exist_ok=True)"
            "; (p / 'x').write_text('1')"]})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    assert (wt / "gen?" / "x").exists()
    same = pin(_dep(wt))
    (wt / "genX").mkdir()
    (wt / "genX" / "left.txt").write_text("x")
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert (wt / "gen?" / "x").exists()
    assert not os.path.lexists(wt / "genX")


def test_only_what_an_owner_can_set_is_stripped(leerie, monkeypatch):
    """A security label is listed on every file under SELinux and no one
    may remove it: only an ACL and a `user.` attribute are a worker's, so
    only those are stripped or count as left."""
    monkeypatch.setattr(leerie.os, "listxattr", lambda fd: [
        "security.selinux", "user.left", "system.posix_acl_default",
        "trusted.x"])
    assert leerie._worker_xattrs(0) == ["user.left",
                                        "system.posix_acl_default"]


@pytest.mark.skipif(os.geteuid() == 0, reason="root reads any directory")
def test_an_untracked_directory_it_cannot_read_is_never_kept(
        pin, leerie, env):
    """An untracked directory its writer left without its read bit hides
    what is in it from the clean: the checkout is made afresh, and nothing
    in it is left."""
    st, staging, caps = env
    a = _commit(leerie, staging, {"src/c.txt": "1"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    same = pin(_dep(wt))
    (wt / "left").mkdir()
    (wt / "left" / "f.txt").write_text("x")
    os.chmod(wt / "left", 0o311)
    b = _commit(leerie, staging, {"src/b.txt": "1"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert not same()          # today the clean cannot: made afresh
    assert not os.path.lexists(wt / "left")
    _like_a_fresh_clone(leerie, wt, b)


# Its run count kept under its own output; its input the repository's
# `calc.py`.
INSTALL_CALC = INSTALL.replace("package.json", "calc.py")


def test_falsify_items_keeps_its_checkouts_across_calls(
        pin, leerie, tmp_path, monkeypatch):
    """The real `_falsify_items`, called twice on one commit (phase 3's
    drop check, then detection): the second call finds both checkouts it
    left, moves them and reruns the install over the one already there."""
    from tests.test_delivery_detection import (
        EFFORTS, ITEM, MODELS, _claim, _falsifier_stub, _repo, _st, _staging)
    repo, _ = _repo(tmp_path)
    (repo / ".gitignore").write_text(".leerie/runs/\nnode_modules/\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "ignore installs")
    head = _git(repo, "rev-parse", "HEAD")
    st = _st(leerie, tmp_path, repo, head)
    _staging(st, repo, head)
    st.data["provision"] = {"recipe": [
        {"kind": "install", "command": [sys.executable, "-c", INSTALL_CALC],
         "working_dir": "."}]}
    monkeypatch.setattr(leerie, "_cgroup_create", lambda *a, **k: None)
    monkeypatch.setattr(leerie, "_ts_resolvable", lambda tree, **_kw: True)
    _falsifier_stub(leerie, monkeypatch, st, lambda kw, n: _claim(
        verdict="could_not_demonstrate", probe_path=""))
    caps = dict(leerie.DEFAULT_CAPS)
    caps.update(max_parallel=1, falsifier_samples=1, worker_timeout_sec=60)

    def call() -> dict:
        return asyncio.run(leerie._falsify_items(
            st, caps, MODELS, EFFORTS, sha=head,
            normalized=[{"item": ITEM, "span": "S1",
                         "verifiability": "tree"}],
            indices=[0], parts_by_idx={0: []}, progress={},
            persist=lambda st_, p: None))
    try:
        assert call()["enabled"] is True
        trees = [leerie._helper_worktree_path(st, n)
                 for n in ("falsifier-confirm", "falsifier-lane0")]
        pins = [pin(_dep(t)) for t in trees]
        assert [_count(t) for t in trees] == [1, 1]
        assert call()["enabled"] is True
        assert all(p() for p in pins)              # the installs were kept
        assert [_count(t) for t in trees] == [2, 2]
        for t in trees:
            _like_a_fresh_clone(leerie, t, head)
    finally:
        for d in (leerie._KEPT_TREES, leerie._KEPT_INSTALLS,
                  leerie._DEPS_KEPT, leerie._INSTALL_OUTPUTS,
                  leerie._CLONE_CACHES, leerie._CLONE_TIMES):
            d.clear()


def test_a_submodules_path_is_empty_again(pin, leerie, env):
    """A submodule's path (a gitlink: no checkout or clean enters one) holds
    nothing a writer left there — not a repository of its own, whose
    configuration a status inside it would read, nor any file — but the
    empty directory a fresh checkout makes."""
    st, staging, caps = env

    def commit(msg: str) -> str:
        # Only what is staged: `git add -A` would drop the gitlink, whose
        # path staging has no checkout of.
        _git(staging, "-c", "user.name=w", "-c", "user.email=w@w", "commit",
             "-qm", msg)
        leerie._take_tree_branch(staging)
        return _git(staging, "rev-parse", "HEAD")
    sha = _git(staging, "rev-parse", "HEAD")
    _git(staging, "update-index", "--add", "--cacheinfo",
         f"160000,{sha},src/sub")
    a = commit("a submodule")
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    assert (wt / "src" / "sub").is_dir()
    assert not any((wt / "src" / "sub").iterdir())  # as a clone makes it
    same = pin(_dep(wt))
    assert _own(wt, "-C", "src/sub", "init", "-q").returncode == 0
    (wt / "src" / "sub" / "left.txt").write_text("x")
    (staging / "src").mkdir(exist_ok=True)
    (staging / "src" / "b.txt").write_text("1")
    _git(staging, "add", "src/b.txt")
    b = commit("work")
    assert "160000" in _git(staging, "ls-tree", "HEAD", "src/sub")
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert same()                                  # reused, not remade
    assert (wt / "src" / "sub").is_dir()
    assert not any((wt / "src" / "sub").iterdir())
    _like_a_fresh_clone(leerie, wt, b)


def test_a_lane_that_fell_back_to_a_worktree_is_made_afresh(
        pin, leerie, env, monkeypatch):
    """A writer's checkout that could not be a clone fell back to a detached
    worktree, which the clone's move does not cover: the next call makes it
    afresh rather than move it."""
    st, staging, caps = env
    real = leerie._ref_less_clone
    calls: list[int] = []

    def first_fails(*a, **k):
        calls.append(1)
        return "no clone" if len(calls) == 1 else real(*a, **k)
    monkeypatch.setattr(leerie, "_ref_less_clone", first_fails)
    a = _commit(leerie, staging, {"src/a.txt": "2"})
    wt = _prepare(leerie, st, caps, "falsifier-lane0", a,
                  writer="falsifier")
    assert (wt / ".git").is_file()                 # a worktree's
    same = pin(_dep(wt))
    b = _commit(leerie, staging, {"src/a.txt": "3"})
    _prepare(leerie, st, caps, "falsifier-lane0", b, writer="falsifier")
    assert not same()
    assert _count(wt) == 1
    _like_a_fresh_clone(leerie, wt, b)
