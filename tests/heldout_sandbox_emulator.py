"""Test-only stand-in for the shim's `--sandbox` on a host that cannot
build one (DESIGN §6 *A held-out evaluation runs alone, in a world that dies
with it (I3)*; `tests/conftest.py::_emulated_heldout_sandbox`).

    heldout_sandbox_emulator.py --view V [--place SRC=REL]...
        [--report PATH --report-fd N] -- INNER...

It keeps the sandbox's contract with leerie, not its isolation: each placed
file is copied to `V/REL` for the run (refused, exit 126, when the view
already has one), INNER (the real shim, without `--sandbox`) runs, the
report is copied to descriptor N from offset 0 and the copy truncated, and
then the placed files, the directories made for them and the report are
removed — so what leerie reads comes back only through the descriptor, as
it does from a real sandbox. Product code never runs this."""
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
    report = None
    report_fd = None
    i = 0
    while i < len(opts):
        k, v = opts[i], opts[i + 1]
        if k == "--view":
            view = Path(v)
        elif k == "--place":
            src, rel = v.split("=", 1)
            place.append((src, rel))
        elif k == "--report":
            report = Path(v)
        elif k == "--report-fd":
            report_fd = int(v)
        else:
            print(f"emulator: unknown option {k}", file=sys.stderr)
            return 126
        i += 2
    assert view is not None
    taken = [rel for _src, rel in place
             if os.path.lexists(view / rel) or rel.startswith("/")
             or ".." in Path(rel).parts]
    if taken:
        print("leerie: cannot isolate the command, so it does not run: "
              f"place: a set path is taken: {', '.join(taken)}",
              file=sys.stderr)
        return 126
    made_files: list[Path] = []
    made_dirs: list[Path] = []
    try:
        for src, rel in place:
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


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
