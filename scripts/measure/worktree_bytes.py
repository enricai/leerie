#!/usr/bin/env python3
"""Measure what a second worktree of a dependency tree actually costs on disk.

DESIGN §6 explains why a worktree's cost varies by roughly 13x with a mount
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

  total_bytes      `st_blocks * 512`, de-duplicated by inode, for files AND
                   directories. Actual allocated space, not apparent size —
                   sparse files and block rounding both make `st_size` wrong.
  shared_bytes     The share on inodes EVERY link of which lives outside the
                   walked tree. Those are the bytes a second worktree gets
                   for free from a shared store.
  private_bytes    The remainder — the marginal cost of one more worktree.

**`st_nlink > 1` is NOT the shared test, and using it is a documented
mistake this repo already made.** `st_nlink` counts names *inside* the tree
too, so a file with two in-tree names looks "shared" while a second worktree
must copy it in full. `docs/IMPLEMENTATION.md`'s "Disk headroom (N30)" table
records that as why the `st_blocks // st_nlink` attempt was withdrawn. This
walk therefore counts how many of each inode's links it saw itself, and
charges the inode unless that count is *fewer* than `st_nlink` — i.e. unless
at least one name lives outside. On the corpus behind DESIGN's figures the
two predicates happen to agree (every multi-linked inode there also has an
out-of-tree name), which is exactly why the wrong one survived review.

**Directories are charged.** A second worktree creates every one of them,
and on the measured corpus they are 37.4 MiB against 64.8 MiB of private
file bytes — a 1.58x difference in the headline number. An earlier revision
of this script omitted them and reported the files-only figure as "the
marginal cost".

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

    De-duplication is by `(st_dev, st_ino)`. An inode number is only unique
    within a device, so keying on `st_ino` alone can silently MERGE two
    distinct files across a mount boundary and under-count — and this tool is
    pointed at exactly the bind-mounted layouts DESIGN discusses. The pair
    cannot double-count, so it is strictly safer.

    Two passes, because "shared" is only decidable once the whole tree is
    seen: the first counts how many links to each inode live inside the
    walk, the second charges every inode whose in-tree link count equals its
    total `st_nlink` — i.e. nothing outside points at it.
    """
    seen_links: dict[tuple[int, int], int] = {}
    # key -> (blocks, nlink, is_dir)
    info: dict[tuple[int, int], tuple[int, int, bool]] = {}
    walk_errors: list[str] = []

    def _record(path: str, is_dir: bool) -> None:
        try:
            st = os.lstat(path)
        except OSError as e:
            # A tree this size is routinely mutated underneath a walk (a
            # package manager pruning, an editor saving). Skipping one entry
            # cannot move a ratio at this scale; aborting would.
            walk_errors.append(f"{path}: {e.strerror}")
            return
        key = (st.st_dev, st.st_ino)
        seen_links[key] = seen_links.get(key, 0) + 1
        info[key] = (st.st_blocks * 512, st.st_nlink, is_dir)

    def _on_error(e: OSError) -> None:
        # os.walk swallows errors by default, which for a measurement tool
        # means confidently reporting a number computed over a subtree it
        # silently skipped.
        walk_errors.append(f"{e.filename}: {e.strerror}")

    for dirpath, dirnames, filenames in os.walk(root, onerror=_on_error):
        for name in dirnames:
            _record(os.path.join(dirpath, name), True)
        for name in filenames:
            _record(os.path.join(dirpath, name), False)

    total = shared = 0
    files = dirs = 0
    for key, (blocks, nlink, is_dir) in info.items():
        total += blocks
        if is_dir:
            # NEVER counted as shared, whatever st_nlink says. A directory's
            # link count is `.` plus its parent's entry plus one `..` per
            # subdirectory — an artefact of the directory tree, not evidence
            # that a store outside the walk points at it. A second worktree
            # has to create every directory, so all of it is marginal cost.
            dirs += 1
            continue
        files += 1
        if seen_links[key] < nlink:
            # At least one name lives outside this walk, so a second checkout
            # can hardlink to it rather than copying.
            shared += blocks

    if walk_errors:
        print(f"warning: {len(walk_errors)} path(s) could not be read; the "
              f"totals below exclude them (first: {walk_errors[0]})",
              file=sys.stderr)

    return {
        "_comment": (
            "Marginal on-disk cost of one more worktree of a dependency "
            "tree: files AND directories, de-duplicated by (st_dev, st_ino). "
            "'shared' means EVERY link to the inode lives outside the walked "
            "tree — NOT st_nlink > 1, which counts in-tree names too and is "
            "the discredited predicate from N30's attempt 3. Regenerate with "
            "scripts/measure/worktree_bytes.py <tree>. Only the basename is "
            "recorded: the absolute path is a local detail, and the figure "
            "that travels is the shared share, not the machine."),
        "tree": os.path.basename(os.path.abspath(root)),
        "unique_inodes": files,
        "directories": dirs,
        "total_bytes": total,
        "shared_bytes": shared,
        "private_bytes": total - shared,
        "shared_share": (shared / total) if total else 0.0,
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
