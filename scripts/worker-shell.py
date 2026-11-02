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
imports only the standard library and the shim beside it, by path.

When the shim gave the CLI a worker view (`--worker-root`, DESIGN §6 *The
orchestrator's root is leerie's; a repository's root is only a confined
process's*), the command then runs in it: `chroot` into the view, every
ambient capability dropped, the worker stage's environment laid over the
CLI's. This file and the CLI stay in leerie's root, and so does leerie's
own PreToolUse hook (`forbidden-command-hook.py` beside this file, run by
the interpreter running this): it is leerie's code."""
import importlib.util
import os
import sys

SHELL = "/bin/bash"


_B64 = frozenset("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"
                 "0123456789+/=")


def _is_leeries_hook(words: list[str], here: str) -> bool:
    """The command is leerie's PreToolUse hook as the orchestrator writes it
    (`_forbidden_command_hook_settings`): this interpreter, `-I`, the hook
    beside this file and one base64 payload. Anything else is the
    worker's."""
    if len(words) != 1:
        return False
    hook = os.path.join(here, "forbidden-command-hook.py")
    head = f"{sys.executable} -I {hook} "
    rest = words[0][len(head):]
    return words[0].startswith(head) and bool(rest) and set(rest) <= _B64


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
    view = os.environ.pop(lk.VIEW_ENV, None)
    root = os.environ.pop(lk.WORKER_ROOT_ENV, None)
    if view and root and not _is_leeries_hook(argv[1:], here):
        try:
            lk.enter_worker_view(view, lk.worker_env_changes(root))
        except OSError as e:
            print(f"leerie: cannot run the command in the repository's "
                  f"root, so it does not run: {e} (DESIGN §6 *The "
                  "orchestrator's root is leerie's; a repository's root is "
                  "only a confined process's*)", file=sys.stderr)
            return 126
    # Marks the command as the worker's own for the CLI's `git` on PATH
    # (`scripts/cli-bin/git`): inside this domain the real git runs as the
    # worker asked (DESIGN §6 *The CLI's own git runs on leerie's terms*).
    env = {**os.environ, "LEERIE_WORKER_COMMAND": "1"}
    try:
        os.execve(SHELL, [SHELL, "-c", *argv[1:]], env)
    except OSError as e:
        print(f"leerie: cannot run {SHELL}: {e}", file=sys.stderr)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
