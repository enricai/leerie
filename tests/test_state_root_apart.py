"""The state root has no second name through the checkout (DESIGN §6 *No host
path comes from a repository*, part 3; review round 27, isolation I6).

Every grant is carved by path: `_around` takes the state root out of a grant
whose path contains it. In the container the checkout is `/work` and the
state root `/leerie-state`. When the state root was a directory inside the
checkout (`state_dir = ".lstate"` in leerie.toml), the two were one directory
under two mounts, and `_around(["/work"], Path("/leerie-state"))` stayed
`["/work"]`: every worker's read of `/work` reached the held-out sets, the
notes and the credentials' copies. The reviewer's proof showed that mechanism;
here the entry every invocation runs refuses that layout, by the place each
path has on disk (`/proc/self/mountinfo`), and passes the layouts whose
nesting is the paths' own.
"""
from __future__ import annotations

from pathlib import Path

import pytest

# mountinfo: id parent major:minor root mount-point options - fstype src opts
ROOT_LINE = "20 1 0:30 / / rw - overlay overlay rw"


def _mounts(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "mountinfo"
    p.write_text("\n".join((ROOT_LINE,) + lines) + "\n")
    return p


def _bind(i: int, dev: str, root: str, point: str) -> str:
    return f"{30 + i} 20 {dev} {root} {point} rw - ext4 /dev/sda2 rw"


@pytest.fixture
def mountinfo(leerie, monkeypatch, tmp_path):
    def use(*lines: str) -> None:
        monkeypatch.setattr(leerie, "MOUNTINFO", _mounts(tmp_path, *lines))
    return use


def test_a_state_root_inside_the_checkout_under_a_second_mount_stops(
        leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/repo", "/work"),
              _bind(2, "8:2", "/home/u/repo/.lstate", "/leerie-state"))
    # The mechanism the reviewer showed: no grant carves it out by path.
    assert leerie._around(["/work"], Path("/leerie-state")) == ["/work"]
    with pytest.raises(SystemExit):
        leerie._require_state_root_apart(Path("/leerie-state"),
                                         Path("/work"))


def test_a_state_root_holding_the_checkout_stops(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/src/repo", "/work"),
              _bind(2, "8:2", "/home/u", "/leerie-state"))
    with pytest.raises(SystemExit):
        leerie._require_state_root_apart(Path("/leerie-state"),
                                         Path("/work"))


def test_the_same_directory_under_two_mounts_stops(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/repo", "/work"),
              _bind(2, "8:2", "/home/u/repo", "/leerie-state"))
    with pytest.raises(SystemExit):
        leerie._require_state_root_apart(Path("/leerie-state"),
                                         Path("/work"))


def test_the_default_layout_passes(leerie, mountinfo):
    """`$HOME/.leerie/<repo>` beside the checkout: siblings on one disk."""
    mountinfo(_bind(1, "8:2", "/home/u/repo", "/work"),
              _bind(2, "8:2", "/home/u/.leerie/repo", "/leerie-state"))
    leerie._require_state_root_apart(Path("/leerie-state"), Path("/work"))


def test_a_name_sharing_the_checkouts_prefix_passes(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/repo", "/work"),
              _bind(2, "8:2", "/home/u/repo-state", "/leerie-state"))
    leerie._require_state_root_apart(Path("/leerie-state"), Path("/work"))


def test_another_device_passes(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/repo", "/work"),
              _bind(2, "8:3", "/home/u/repo/.lstate", "/leerie-state"))
    leerie._require_state_root_apart(Path("/leerie-state"), Path("/work"))


def test_octal_escapes_are_decoded(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/home/u/my\\040repo", "/work"),
              _bind(2, "8:2", "/home/u/my\\040repo/.l", "/leerie-state"))
    assert leerie._disk_place(Path("/work/x")) == ("8:2", "/home/u/my repo/x")
    with pytest.raises(SystemExit):
        leerie._require_state_root_apart(Path("/leerie-state"),
                                         Path("/work"))


def test_the_mount_on_top_wins(leerie, mountinfo):
    mountinfo(_bind(1, "8:2", "/a", "/work"),
              _bind(2, "8:5", "/b", "/work"))
    assert leerie._disk_place(Path("/work/s")) == ("8:5", "/b/s")


def test_nesting_by_path_is_carved_and_passes(leerie, tmp_path):
    """The direct-call default `<repo>/.leerie`, on the real mounts: nested
    on disk exactly as by path, so `_around` carves it."""
    repo = tmp_path / "repo"
    (repo / ".leerie").mkdir(parents=True)
    leerie._require_state_root_apart(repo / ".leerie", repo)
    assert leerie._around([str(repo)], repo / ".leerie") != [str(repo)]


def test_separate_directories_pass_on_the_real_mounts(leerie, tmp_path):
    (tmp_path / "repo").mkdir()
    (tmp_path / "state").mkdir()
    leerie._require_state_root_apart(tmp_path / "state", tmp_path / "repo")

