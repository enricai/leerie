#!/usr/bin/python3 -IS
"""The shell a confined worker's CLI runs each of its commands through
(DESIGN §6 *A worker's commands cannot reach its CLI*).

Every confined worker's CLI carries `CLAUDE_CODE_SHELL_PREFIX` naming this
file (`_invoke`, and leerie's `--settings`), so it runs every Bash-tool
command and every hook as `worker-shell.py <command>`. This enters a
Landlock domain nested inside the worker's (`landlock_exec.nest`: the same
reach, the parent's grants still binding) and then runs the command with
`bash -c`. Landlock refuses a nested domain ptrace-class access to its
parent's processes, so nothing the command starts can open the CLI's
`/proc/<pid>/fd/1` — the stream leerie reads the worker's record from — or
attach to the CLI. Exits 126 without running the command when the domain
cannot be made: never a command the CLI's stream is reachable from.

The interpreter is named absolutely, with `-I -S`: this runs in the CLI's
own domain until it nests, so nothing a worker can write — a `python3`
earlier on `PATH`, a `PYTHONPATH`, a `site` directory — may run here. It
imports only the standard library and the shim beside it, by path."""
import importlib.util
import os
import sys

SHELL = "/bin/bash"


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: worker-shell.py COMMAND [ARGS...]", file=sys.stderr)
        return 2
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        spec = importlib.util.spec_from_file_location(
            "landlock_exec", os.path.join(here, "landlock_exec.py"))
        lk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lk)
        lk.nest()
    except OSError as e:
        print(f"leerie: cannot run the command in its own Landlock domain, "
              f"so it does not run: {e} (DESIGN §6 *A worker's commands "
              "cannot reach its CLI*)", file=sys.stderr)
        return 126
    try:
        os.execv(SHELL, [SHELL, "-c", *argv[1:]])
    except OSError as e:
        print(f"leerie: cannot run {SHELL}: {e}", file=sys.stderr)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
