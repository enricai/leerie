#!/usr/bin/env python3
"""Measure what a second worktree of a dependency tree actually costs on disk.

DESIGN §6 explains why a worktree's cost varies by roughly 20x with a mount
topology leerie does not control: package managers like pnpm are
content-addressed and hardlink from a shared store into each `node_modules`,
so a second checkout of the same dependency set is nearly free — but leerie
bind-mounts the store and the state directory as *separate* mounts, and Linux
refuses `link()` across mounts, so the copy is paid in full.

That claim carried figures with no artifact behind them. This is the artifact.
The work order's own rule for N30 was "land the measurement rather than
guessing", which the sibling `worker_durations.py` satisfies for N25; this
does the same for the numbers quoted in DESIGN, so anyone can re-derive them:

    python3 scripts/measure/worktree_bytes.py ~/some-repo/node_modules \\
        > tests/fixtures/worktree_bytes/summary.json

What it reports, and why each number is the one that matters:

  total_bytes      `st_blocks * 512`, de-duplicated by inode. Actual
                   allocated space, not apparent size — sparse files and
                   filesystem block rounding both make `st_size` wrong here.
  hardlinked_bytes The share sitting on inodes with `st_nlink > 1`, i.e.
                   reachable from somewhere outside this walk. Where the
                   store shares a mount, this is nearly everything and a
                   second worktree costs almost none of it.
  private_bytes    The `st_nlink == 1` remainder — the marginal cost of one
                   more worktree in the shared-mount regime.

NOTE the asymmetry this measures, which is the whole point: in leerie's own
container layout the store cannot hardlink in, every file has exactly one
name, and `private_bytes == total_bytes`. The gap between the two columns IS
the variation, so a single number could never have described it.

Deliberately reports rather than decides. leerie does not size a disk check
against these figures — a measured per-worktree bound was tried four times and
withdrawn (see docs/IMPLEMENTATION.md "Disk headroom (N30)"); the shipped rule
is a proportional free-space floor. This exists so the *explanation* in DESIGN
is reproducible, not to feed a gate.
"""
from __future__ import annotations

import json
import os
import sys


def measure(root: str) -> dict:
    """Walk *root*, charging each inode once.

    De-duplication is by `st_ino` alone rather than `(st_dev, st_ino)`: a
    single dependency tree does not span devices, and keying on the pair
    would silently double-count nothing while making the common case slower
    to read. If that ever stops holding, the counts are the thing to check.
    """
    seen: set[int] = set()
    total = hardlinked = 0
    files = dirs = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirs += len(dirnames)
        for name in filenames:
            path = os.path.join(dirpath, name)
            try:
                st = os.lstat(path)
            except OSError:
                # A tree this size is routinely mutated underneath a walk
                # (a package manager pruning, an editor saving). Skipping is
                # right: one missed file cannot change a ratio at this scale,
                # and aborting the whole measurement would.
                continue
            if st.st_ino in seen:
                continue
            seen.add(st.st_ino)
            files += 1
            blocks = st.st_blocks * 512
            total += blocks
            if st.st_nlink > 1:
                hardlinked += blocks
    return {
        "_comment": (
            "Marginal on-disk cost of one more worktree of a dependency "
            "tree, de-duplicated by inode. Regenerate with "
            "scripts/measure/worktree_bytes.py <tree>. Only the basename is "
            "recorded: the absolute path is a local detail, and the figure "
            "that travels is the hardlinked share, not the machine."),
        "tree": os.path.basename(os.path.abspath(root)),
        "unique_inodes": files,
        "directories": dirs,
        "total_bytes": total,
        "hardlinked_bytes": hardlinked,
        "private_bytes": total - hardlinked,
        "hardlinked_share": (hardlinked / total) if total else 0.0,
    }


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} <path-to-dependency-tree>", file=sys.stderr)
        return 2
    root = argv[1]
    if not os.path.isdir(root):
        print(f"not a directory: {root}", file=sys.stderr)
        return 1
    print(json.dumps(measure(root), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
