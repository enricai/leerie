"""Source-text pin: rootless containerd gets a plain (non-rshared) cgroup
bind-mount.

DESIGN §6 *Rootless exception*: rootlesskit's --propagation=rslave demotes
/sys/fs/cgroup to a slave mount, so bind-propagation=rshared would fail —
but leerie doesn't need propagation of new mount events, only read/write
visibility into the already-mounted cgroupfs so container-entry.sh can
create/manage cgroups under the systemd-delegated user slice. A plain
bind-mount provides exactly that.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = REPO_ROOT / "leerie"


def _cgroup_mount_block() -> str:
    """Return the launcher source for the CGROUP_MOUNT_ARG assignment."""
    src = LAUNCHER.read_text()
    start = src.index("CGROUP_MOUNT_ARG=()")
    end = src.index("\nfi\n", start) + len("\nfi\n")
    return src[start:end]


def test_rootless_branch_gated_on_sentinel():
    """The rootless case must be its own branch, gated on the same
    containerd-rootless sentinel used elsewhere (not id -u, which
    over-fires on macOS/Colima; not the old shared-propagation probe)."""
    block = _cgroup_mount_block()
    assert "containerd-rootless/child_pid" in block, (
        "rootless cgroup-mount branch must be gated on "
        "containerd-rootless/child_pid"
    )


def test_rootless_branch_omits_rshared():
    """The rootless branch's mount must NOT request bind-propagation=rshared
    — rootlesskit's rslave propagation makes that fail/no-op, and it isn't
    needed for directory/file ops on an already-mounted cgroupfs."""
    block = _cgroup_mount_block()
    start = block.index("containerd-rootless/child_pid")
    # The rootless branch runs from its `elif` up to the next `elif`/`fi`.
    next_elif = block.index("elif", start + 1)
    rootless_branch = block[start:next_elif]
    assert "--mount" in rootless_branch, (
        "rootless branch must still add a cgroup bind-mount, just without "
        "rshared propagation"
    )
    assert "bind-propagation=rshared" not in rootless_branch, (
        "rootless branch must not request bind-propagation=rshared — "
        "rootlesskit's rslave propagation makes this fail"
    )


def test_rootful_branches_keep_rshared():
    """Darwin (Colima) and native rootful Linux must keep requesting
    bind-propagation=rshared — only the rootless branch changed."""
    block = _cgroup_mount_block()
    assert block.count("bind-propagation=rshared") == 2, (
        "expected exactly two rshared mounts: the Darwin/Colima branch and "
        "the native rootful Linux branch"
    )


def test_rootless_branch_precedes_generic_linux_branch():
    """The rootless elif must come before the generic cgroup.controllers
    elif, so a rootless host takes the plain-mount path rather than
    falling through to the rshared one."""
    block = _cgroup_mount_block()
    rootless_idx = block.index("containerd-rootless/child_pid")
    generic_idx = block.rindex("elif [ -f /sys/fs/cgroup/cgroup.controllers ]")
    assert rootless_idx < generic_idx, (
        "rootless branch must be checked before the generic rootful "
        "Linux branch"
    )
