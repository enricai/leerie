"""Test-only stand-in for the shim's `--sandbox` on a host that cannot
build one (DESIGN §6 *A held-out evaluation runs alone, in a world that dies
with it (I3)*; `tests/conftest.py::_emulated_heldout_sandbox`).

    heldout_sandbox_emulator.py --view V [--place SRC=REL]...
        [--replace SRC=REL]... [--remove REL]...
        [--report PATH --report-fd N] [--taken-fd N] -- INNER...

It keeps the sandbox's contract with leerie, not its isolation: each placed
file is copied to `V/REL` for the run (not placed, exit 126, the reason on
the taken descriptor, when the view already has one or a link or a
non-directory is on its path; refused, exit 126, for a path that is not
plain), each `--replace` file written over the view's regular file there
and each `--remove` file taken away (both kept aside and put back after; a
link or anything but a regular file there is taken), INNER (the real
shim, without `--sandbox`) runs, the report is copied to descriptor N from
offset 0 and the copy truncated, and then the placed files, the directories
made for them and the report are removed — so what leerie reads comes back
only through the descriptor, as it does from a real sandbox. Product code
never runs this."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    sep = argv.index("--")
    opts, inner = argv[:sep], argv[sep + 1:]
    view = None
    place: list[tuple[str, str]] = []
    over: list[tuple[str, str]] = []
    remove: list[str] = []
    report = None
    report_fd = None
    taken_fd = None
    i = 0
    while i < len(opts):
        k, v = opts[i], opts[i + 1]
        if k == "--view":
            view = Path(v)
        elif k == "--place":
            src, rel = v.split("=", 1)
            place.append((src, rel))
        elif k == "--replace":
            src, rel = v.split("=", 1)
            over.append((src, rel))
        elif k == "--remove":
            remove.append(v)
        elif k == "--report":
            report = Path(v)
        elif k == "--report-fd":
            report_fd = int(v)
        elif k == "--taken-fd":
            taken_fd = int(v)
        else:
            print(f"emulator: unknown option {k}", file=sys.stderr)
            return 126
        i += 2
    assert view is not None
    def _blocked(rel: str) -> bool:
        # The shim resolves each directory with O_NOFOLLOW | O_DIRECTORY: a
        # link or a non-directory on the path leaves no room for the file.
        d = view
        for part in Path(rel).parts[:-1]:
            d = d / part
            if d.is_symlink() or (os.path.lexists(d) and not d.is_dir()):
                return True
        return False

    every = [rel for _src, rel in place + over] + remove
    bad = [rel for rel in every
           if rel.startswith("/") or ".." in Path(rel).parts]
    if bad:
        print("leerie: cannot isolate the command, so it does not run: "
              f"place: the view cannot hold {', '.join(bad)}",
              file=sys.stderr)
        return 126
    def _not_regular(rel: str) -> bool:
        p = view / rel
        return p.is_symlink() or (os.path.lexists(p) and not p.is_file())

    taken = ([rel for _src, rel in place
              if _blocked(rel) or os.path.lexists(view / rel)]
             + [rel for rel in [r for _s, r in over] + remove
                if _blocked(rel) or _not_regular(rel)])
    if taken:
        msg = ("leerie: the held-out file is not placed, so the command "
               f"does not run: place: a set path is taken: "
               f"{', '.join(taken)}")
        print(msg, file=sys.stderr)
        if taken_fd is not None:
            os.write(taken_fd, msg.encode() + b"\n")
        return 126
    made_files: list[Path] = []
    made_dirs: list[Path] = []
    # The view's own files a replacement or removal took away, put back
    # after the run: (path, its bytes, its mode).
    kept: list[tuple[Path, bytes, int]] = []
    try:
        for rel in [r for _s, r in over] + remove:
            p = view / rel
            if p.is_file():
                kept.append((p, p.read_bytes(), p.stat().st_mode))
                p.unlink()
        for src, rel in place + over:
            dst = view / rel
            missing = []
            d = dst.parent
            while not d.exists():
                missing.append(d)
                d = d.parent
            for m in reversed(missing):
                m.mkdir()
                made_dirs.append(m)
            shutil.copyfile(src, dst)
            os.chmod(dst, 0o444)
            made_files.append(dst)
        rc = subprocess.call(inner, pass_fds=(), close_fds=True)
        if report is not None and report_fd is not None:
            try:
                data = report.read_bytes()
            except OSError:
                data = b""
            os.pwrite(report_fd, data, 0)
            os.ftruncate(report_fd, len(data))
            if report.exists():
                report.unlink()
        return rc if rc >= 0 else 128 - rc
    finally:
        for f in made_files:
            try:
                f.unlink()
            except OSError:
                pass
        for d in reversed(made_dirs):
            shutil.rmtree(d, ignore_errors=True)
        for p, data, mode in kept:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(data)
            os.chmod(p, mode)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
