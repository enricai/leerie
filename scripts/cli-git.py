#!/usr/bin/python3 -IS
"""The CLI's own git, on leerie's terms (DESIGN §6 *The CLI's own git runs on
leerie's terms*; review round 22 isolation H1).

A confined worker's CLI runs git itself — `status`, `log`, `ls-files`,
`config`, `remote` at the start of every session (measured, CLI 2.1.312) —
outside the domain its Bash-tool commands are nested in, and it strips
`GIT_DIR`/`GIT_WORK_TREE` and sets its own `GIT_CONFIG_*` for those calls
(measured), so no environment pin reaches them. It finds git on its `PATH`,
where `scripts/cli-bin/git` is first; that runs this for every call that is
not one of the worker's own commands.

This (1) enters a Landlock domain nested in the CLI's (`landlock_exec.nest`),
so nothing git runs — a filter, a textconv — can open the CLI's
`/proc/<pid>/fd/1`; then (2) runs the real git (`LEERIE_REAL_GIT`) with the
tree's repository named from leerie's layout and leerie's settings
(`LEERIE_CLI_GIT`: the arguments and environment `_git_view` gives leerie's
own reads of the tree), so the tree's `.git` file is never followed. Exits
126 without running git when the domain cannot be made."""
import importlib.util
import json
import os
import sys


def main(argv: list[str]) -> int:
    here = os.path.dirname(os.path.realpath(__file__))
    try:
        spec = importlib.util.spec_from_file_location(
            "landlock_exec", os.path.join(here, "landlock_exec.py"))
        lk = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lk)
        lk.nest()
    except OSError as e:
        print(f"leerie: cannot run git in its own Landlock domain, so it does "
              f"not run: {e} (DESIGN §6 *The CLI's own git runs on leerie's "
              "terms*)", file=sys.stderr)
        return 126
    real = os.environ.get("LEERIE_REAL_GIT") or "/usr/bin/git"
    env = dict(os.environ)
    args: list[str] = []
    spec_json = env.pop("LEERIE_CLI_GIT", "")
    if spec_json:
        try:
            view = json.loads(spec_json)
            args = [str(a) for a in view["args"]]
            env.update({str(k): str(v) for k, v in view["env"].items()})
        except (ValueError, KeyError, TypeError) as e:
            print(f"leerie: unreadable LEERIE_CLI_GIT ({e}); git does not "
                  "run", file=sys.stderr)
            return 126
    try:
        os.execve(real, [real, *args, *argv[1:]], env)
    except OSError as e:
        print(f"leerie: cannot run {real}: {e}", file=sys.stderr)
    return 127


if __name__ == "__main__":
    sys.exit(main(sys.argv))
