"""DESIGN §6 *Leerie names its own Python by path*.

In the image, PATH holds mise's user shims ahead of `/usr/bin`, and a shim
picks its interpreter from the version files of its working directory and
every directory above it. `container-entry.sh` changes to `/work`, the
repository, and ran the cgroup broker and the orchestrator as `python3 …`:
measured 2026-11-04 in `leerie:0.44.1`, a checkout whose `.python-version`
held `3.11` made PID 1 (root) download and install CPython 3.11.18, and the
broker, the orchestrator and every helper it starts as `sys.executable` ran
on it. The Fly/EC2 launch wrapper started the orchestrator as `python3` with
`cwd="/work"`, and Fly's subtree collection ran its Python below `/work`.

The rule: where leerie starts its own Python in leerie's root from a
directory a repository's files reach, it names the interpreter by path.
Checked over
- the entry script's command lines,
- the machine payload of `collect-subtrees.sh`,
- the Fly/EC2 launch wrapper as `seed-common.sh` renders it (AST),
- every subprocess or `exec` call in leerie's own Python sources (AST),
with controls proving each scan can fail.
"""
from __future__ import annotations

import ast
import re
import shlex
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENTRY = REPO / "scripts" / "container-entry.sh"
COLLECT = REPO / "scripts" / "remote" / "collect-subtrees.sh"
SEED_COMMON = REPO / "scripts" / "remote" / "seed-common.sh"

# A Python interpreter named by a bare name: what PATH, and so a shim,
# resolves. A mechanical program name, never prose.
_BARE_PY = re.compile(r"python[0-9.]*")

# Calls whose first argument is the program to start, as an argv list
# (`subprocess.*`) or as the file name (`os.exec*`, `os.spawn*p`,
# `os.posix_spawn*`, `asyncio.create_subprocess_exec`).
_ARGV_CALLS = frozenset({"run", "Popen", "call", "check_call",
                         "check_output"})
_FILE_CALLS = frozenset({"execv", "execve", "execvp", "execvpe", "execl",
                         "execle", "execlp", "execlpe", "spawnv", "spawnve",
                         "spawnvp", "spawnvpe", "spawnl", "spawnle",
                         "spawnlp", "spawnlpe", "posix_spawn",
                         "posix_spawnp", "create_subprocess_exec"})


def _call_name(func: ast.expr) -> str | None:
    if isinstance(func, ast.Attribute):
        return func.attr
    if isinstance(func, ast.Name):
        return func.id
    return None


def _bare_python_starts(tree: ast.AST) -> list[int]:
    """Line numbers of calls that start a Python interpreter named by a
    bare name."""
    out: list[int] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not node.args:
            continue
        name = _call_name(node.func)
        first = node.args[0]
        prog: ast.expr | None = None
        if name in _ARGV_CALLS and isinstance(first, (ast.List, ast.Tuple)):
            prog = first.elts[0] if first.elts else None
        elif name in _FILE_CALLS:
            # spawn* take a mode first, the file second.
            if name.startswith("spawn") and len(node.args) > 1:
                first = node.args[1]
            prog = first
        if (isinstance(prog, ast.Constant) and isinstance(prog.value, str)
                and _BARE_PY.fullmatch(prog.value)):
            out.append(node.lineno)
    return out


def _logical_lines(text: str) -> list[tuple[int, str]]:
    """Shell text as (first line number, logical line): comment lines
    dropped, backslash continuations joined."""
    out: list[tuple[int, str]] = []
    buf, start = "", 0
    for n, line in enumerate(text.splitlines(), 1):
        if not buf and line.lstrip().startswith("#"):
            continue
        if not buf:
            start = n
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((start, buf + line))
        buf = ""
    if buf:
        out.append((start, buf))
    return out


def _bare_python_words(text: str) -> list[tuple[int, str]]:
    """(line, logical line) of every shell line holding a bare Python
    interpreter name as a word. Lines shlex cannot split (an open quote
    spanning a `-c` program) are split on whitespace up to the quote."""
    out: list[tuple[int, str]] = []
    for n, line in _logical_lines(text):
        try:
            words = shlex.split(line, comments=True)
        except ValueError:
            words = line.split("'", 1)[0].split()
        if any(_BARE_PY.fullmatch(w.lstrip("$(<")) for w in words):
            out.append((n, line))
    return out


def _collect_payload() -> str:
    text = COLLECT.read_text()
    m = re.search(r"payload=\$\(cat <<'BASH_EOF'\n(.*?)\nBASH_EOF\n",
                  text, re.S)
    assert m, "collect-subtrees.sh's machine payload heredoc not found"
    return m.group(1)


def _rendered_launch_wrapper() -> str:
    script = (f". {shlex.quote(str(SEED_COMMON))}\n"
              "_render_launch_prefix fly '[\"rid\"]'\n"
              "printf 'child_env = dict(os.environ)\\n'\n"
              "_render_launch_suffix\n")
    return subprocess.run(["/bin/bash", "-c", script], check=True,
                          capture_output=True, text=True, timeout=30).stdout


def _leerie_python_sources() -> list[Path]:
    return ([REPO / "orchestrator" / "leerie.py"]
            + sorted((REPO / "scripts").glob("*.py"))
            + sorted((REPO / "scripts" / "remote").glob("*.py"))
            + sorted((REPO / "scripts" / "measure").glob("*.py"))
            + sorted((REPO / "chain").glob("*.py")))


# --- the rule, on the shipped code -----------------------------------------

def test_entry_script_names_python_by_path() -> None:
    assert _bare_python_words(ENTRY.read_text()) == []


def test_entry_script_execs_the_image_interpreter() -> None:
    """Substance, not only absence: both privilege-drop arms exec the
    broker and the orchestrator as /usr/bin/python3."""
    execs = [line for _, line in _logical_lines(ENTRY.read_text())
             if " exec " in f" {line} " and "$BROKER" in line]
    assert len(execs) == 2, execs
    for line in execs:
        words = shlex.split(line)
        assert words[words.index("exec") + 1] == "/usr/bin/python3", line
        orch = [i for i, w in enumerate(words) if w.endswith("leerie.py")]
        assert orch and words[orch[0] - 1] == "/usr/bin/python3", line


def test_collect_subtrees_payload_names_python_by_path() -> None:
    payload = _collect_payload()
    assert "/usr/bin/python3" in payload
    assert _bare_python_words(payload) == []


def test_launch_wrapper_names_python_by_path() -> None:
    src = _rendered_launch_wrapper()
    tree = ast.parse(src)
    assert _bare_python_starts(tree) == []
    popens = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
              and _call_name(n.func) == "Popen"]
    assert len(popens) == 1
    argv0 = popens[0].args[0].elts[0]
    assert isinstance(argv0, ast.Constant) and argv0.value == "/usr/bin/python3"


def test_leerie_python_sources_start_no_bare_python() -> None:
    bad = {str(p.relative_to(REPO)): lines
           for p in _leerie_python_sources()
           if (lines := _bare_python_starts(ast.parse(p.read_text())))}
    assert bad == {}


# --- controls: each scan can fail -------------------------------------------

def test_ast_scan_flags_bare_python_and_passes_named() -> None:
    flagged = ast.parse(
        "import os, subprocess, asyncio\n"
        "subprocess.Popen(['python3', 'x.py'], cwd='/work')\n"
        "subprocess.run(('python', '-c', 'pass'))\n"
        "os.execvp('python3.12', ['python3.12', 'x'])\n"
        "os.spawnvp(os.P_WAIT, 'python3', ['python3'])\n"
        "asyncio.create_subprocess_exec('python3', 'x')\n")
    assert _bare_python_starts(flagged) == [2, 3, 4, 5, 6]
    named = ast.parse(
        "import os, subprocess, sys\n"
        "subprocess.Popen(['/usr/bin/python3', 'x.py'])\n"
        "subprocess.run([sys.executable, '-I', 'x'])\n"
        "os.execv(sys.executable, [sys.executable, 'x'])\n"
        "subprocess.run(['git', 'status'])\n"
        "cmd = ['python3', '-m', 'pytest']\n")
    assert _bare_python_starts(named) == []


def test_shell_scan_flags_bare_python_and_passes_named() -> None:
    text = ("# python3 in a comment is prose\n"
            'exec python3 "$BROKER" --spawn \\\n'
            "  -- runuser -u leerie -- \\\n"
            "  /usr/bin/python3 leerie.py\n"
            'exec /usr/bin/python3 "$BROKER" -- env \\\n'
            '  python3 leerie.py "$@"\n'
            "  done < <(python3 -I -c '\n"
            "x = 1\n"
            "' arg)\n"
            "x=$(/usr/bin/python3 -I -c 'import sys')\n")
    assert [n for n, _ in _bare_python_words(text)] == [2, 5, 7]
