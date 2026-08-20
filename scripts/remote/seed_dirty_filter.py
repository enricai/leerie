#!/usr/bin/env python3
"""scripts/remote/seed_dirty_filter.py — shared dirty-file transfer filter.

Single-owner implementation of the filter both seed-repo.sh (Fly) and
ec2-seed-repo.sh (EC2) apply to the NUL/newline-delimited candidate file
list before handing it to rsync's --files-from. Invoked from
seed-common.sh's _seed_dirty_filter() (the single definition site both
transports source transitively — see seed-common.sh's header comment).

Contract: reads newline-delimited paths on stdin, writes the surviving
paths NUL-delimited to stdout. USER_REPO in the environment anchors the
lexists() vanished-entry check; if unset, that check is skipped.
"""
import os
import re
import sys

# Editor-temp filename patterns — these are per-process editor state
# (Emacs lock files, backups, Vim swap files) that should never be
# shipped. Emacs locks in particular are dangling symlinks of the form
# .#NAME -> user@host.pid:timestamp that vanish as buffers close;
# letting them into the rsync file list produces "stat: No such file
# or directory" failures (exit 23).
_VIM_SWAP_RE = re.compile(r"^\..*\.sw[a-z]$")


def _is_editor_temp(path: str) -> bool:
    base = path.rsplit("/", 1)[-1]
    return (
        base.startswith(".#")             # Emacs lock
        or base.endswith("~")             # backup
        or bool(_VIM_SWAP_RE.match(base))  # Vim swap
    )


def main() -> None:
    repo_root = os.environ.get("USER_REPO", "")

    for line in sys.stdin.read().splitlines():
        if not line:
            continue
        # .git/ and .leerie/ are coordination state, never transported here.
        # .git/ → bundle path creates it natively on the machine.
        # .leerie/ → host-side run state; the machine writes its own.
        # Exception: committed config files (.leerie/config.toml,
        # .leerie/Dockerfile, .leerie/.leerie-setup.sh) are repo-owned
        # declarations that workers need.
        if line.startswith(".git/") or line == ".git":
            continue
        if line.startswith(".leerie/"):
            if line not in (".leerie/config.toml", ".leerie/Dockerfile",
                            ".leerie/.leerie-setup.sh"):
                continue
        elif line == ".leerie":
            continue
        # Defensive: drop worktree paths if they ever surface.
        if "/.leerie/runs/" in line and "/worktrees/" in line:
            continue
        if _is_editor_temp(line):
            continue
        # Drop entries that vanished between `git status` and now (Emacs
        # closing a buffer, a build tool cleaning a temp file, etc).
        # lexists() is True for a symlink whose target is missing — we
        # want to ship those (rsync -a preserves the link, target irrelevant
        # on the sender). It is False only when the path itself is gone.
        if repo_root and not os.path.lexists(os.path.join(repo_root, line)):
            continue
        sys.stdout.buffer.write(line.encode() + b"\x00")


if __name__ == "__main__":
    main()
