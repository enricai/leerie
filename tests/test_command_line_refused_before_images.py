"""A command line is refused before any image step (DESIGN §6 *A command
line is refused before any image step*).

The operator's live 0.44.0 launches: `leerie promptresume <run-id>
--max-workers 2000 …`, a mistyped `resume`, spent about 10 minutes on the
base and repository image steps before the orchestrator's parser, in the
container, printed `leerie: error: unrecognized arguments: <run-id>`. The
launcher now runs that same parser (`check_command_line`) on the host, over
the argv it will give the container, before any image step."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tests.launcher_stub_run import make_world, run_argv, run_to_end

REPO_ROOT = Path(__file__).resolve().parent.parent
RUN_ID = "7859ad30-0000-4000-8000-000000000000"


def _calls(w: dict) -> list[str]:
    f = w["world"] / "nerdctl.calls"
    return f.read_text().splitlines() if f.exists() else []


_IMAGE_STEP = (" build ", " save ", " load ", " pull ", " image ", " run ")


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
@pytest.mark.parametrize("argv,message", [
    (["promptresume", RUN_ID, "--max-workers", "2000", "--no-push"],
     f"leerie: error: unrecognized arguments: {RUN_ID}"),
    (["--max-workers", "0", "--no-push", "a task"],
     "leerie: error: argument --max-workers: '0' is not a positive integer"),
])
def test_a_refused_command_line_runs_no_image_step(tmp_path, argv, message):
    """The real launcher, against a stub `nerdctl`: the line ends with the
    parser's own message and status 2, and no nerdctl command that builds,
    copies, inspects or runs an image ran; it takes no longer than the
    launch's host checks (well under the image steps' minutes). Falsified
    live (2026-11-03): the check removed from the launcher (status 0: the
    stub's container "ran" and the image steps were called)."""
    w = make_world(tmp_path)
    t = time.monotonic()
    rc, out = run_to_end(w, argv)
    elapsed = time.monotonic() - t
    assert rc == 2, out
    assert message in out, out
    assert out.count("unrecognized arguments") <= 1, out
    assert not [c for c in _calls(w) if any(s in f" {c} " for s in _IMAGE_STEP)], \
        _calls(w)
    assert run_argv(w) == []
    assert elapsed < 30, elapsed


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="reads /proc")
def test_an_accepted_command_line_still_runs(tmp_path):
    """Control: a line the parser accepts reaches the container's run."""
    w = make_world(tmp_path)
    rc, out = run_to_end(w, ["--no-push", "--max-workers", "2", "a task"])
    assert rc == 0, out
    assert run_argv(w), out


def _orchestrator():
    spec = importlib.util.spec_from_file_location(
        "leerie_orch_cli", REPO_ROOT / "orchestrator" / "leerie.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_the_check_is_the_orchestrators_own_parser():
    """One parser: `main` and `check_command_line` both parse through
    `_parse_command_line(_leerie_arg_parser(), …)`, so a flag the
    orchestrator gains is accepted by the check with no second list. Run,
    not read: a line using a flag main's parser defines passes, a garbled
    one exits 2."""
    m = _orchestrator()
    assert m.check_command_line(["resume", RUN_ID, "--max-workers", "3"]) == 0
    assert m.check_command_line(["list"]) == 0
    with pytest.raises(SystemExit) as e:
        m.check_command_line(["promptresume", RUN_ID])
    assert e.value.code == 2
    args, run_id, is_resume, is_list = m._parse_command_line(
        m._leerie_arg_parser(), ["resume", RUN_ID, "--max-workers", "3"])
    assert (run_id, is_resume, is_list, args.max_workers) == (
        RUN_ID, True, False, 3)


def test_the_check_reads_no_state_and_starts_nothing(tmp_path):
    """`python3 -I -S` on the checkout's module, as the launcher runs it,
    in an empty directory with an empty HOME: the parse-only path opens no
    file under them and spawns no process, and answers within a few
    seconds."""
    home = tmp_path / "home"
    home.mkdir()
    code = (
        "import importlib.util, os, subprocess, sys\n"
        "spec = importlib.util.spec_from_file_location('o', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(m)\n"
        "def no(*a, **k): raise AssertionError('spawned')\n"
        "subprocess.Popen = no; os.fork = no\n"
        "sys.exit(m.check_command_line(sys.argv[2:]))\n")
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-I", "-S", "-B", "-c", code,
                        str(REPO_ROOT / "orchestrator" / "leerie.py"),
                        "promptresume", RUN_ID], cwd=tmp_path,
                       capture_output=True, text=True, timeout=60,
                       env={"PATH": "/usr/bin:/bin", "HOME": str(home)})
    assert r.returncode == 2, r.stderr
    assert r.stderr.rstrip().endswith(
        f"leerie: error: unrecognized arguments: {RUN_ID}"), r.stderr
    assert time.monotonic() - t < 5
    assert list(tmp_path.iterdir()) == [home] and not list(home.iterdir())


# --- the check loads a kept code object, not a compile per launch ----------
# Review round 39, execution E4: compiling the 3.5 MB leerie.py took 3.16 s
# of every launch (4-7 s per refused line), against "about a second".

def _check_snippet() -> str:
    """The launcher's check program, verbatim."""
    src = (REPO_ROOT / "leerie").read_text()
    block = src[src.index("# --- refuse a command line before any image step"):]
    start = block.index("python3 -I -S -B -c '") + len("python3 -I -S -B -c '")
    return block[start:block.index("\n' ", start)]


def _module(rc: int) -> str:
    return f"def check_command_line(argv):\n    return {rc}\n"


def _check(src: Path, cache: Path) -> int:
    return subprocess.run([sys.executable, "-I", "-S", "-B", "-c",
                           _check_snippet(), str(src), str(cache), "x"],
                          capture_output=True, text=True, timeout=60,
                          env={"PATH": "/usr/bin:/bin"}).returncode


def _key(data: bytes) -> str:
    import hashlib
    return hashlib.sha256(importlib.util.MAGIC_NUMBER + data).hexdigest()


def test_the_check_runs_the_kept_code_object_of_its_source(tmp_path):
    """Substance: the second launch runs the kept code object, not the
    source — an entry swapped for another module's answers with that
    module's status; an edited source is a new entry; a broken entry is
    compiled again."""
    import marshal
    src = tmp_path / "leerie.py"
    cache = tmp_path / "bytecode"
    src.write_text(_module(0))
    assert _check(src, cache) == 0
    entry = cache / _key(src.read_bytes())
    assert entry.is_file()
    entry.write_bytes(marshal.dumps(compile(_module(7), "x", "exec")))
    assert _check(src, cache) == 7
    src.write_text(_module(3))
    assert _check(src, cache) == 3
    assert (cache / _key(src.read_bytes())).is_file()
    (cache / _key(src.read_bytes())).write_bytes(b"\x00broken")
    assert _check(src, cache) == 3


def test_the_check_keeps_four_code_objects(tmp_path):
    src = tmp_path / "leerie.py"
    cache = tmp_path / "bytecode"
    for rc in range(6):
        src.write_text(_module(0) + f"# {rc}\n")
        assert _check(src, cache) == 0
    assert len(list(cache.iterdir())) == 4
    assert (cache / _key(src.read_bytes())).is_file()
