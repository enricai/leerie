#!/usr/bin/env python3
"""Print the worker guard for a `claude -p` command line built in bash.

Usage: worker-guard.py <allowed_tools> <repo_root> [<state.json>]

The Fly recovery integrator (`scripts/remote/collect-subtrees.sh`) builds its
own `claude` argv in bash, after the orchestrator has exited. It takes the
deny rules, notices, PreToolUse hook and script-shell environment from the
same construction every orchestrator-spawned worker gets
(`_worker_guard` in orchestrator/leerie.py, imported from the sibling
`orchestrator/` directory as `forbidden-command-hook.py` does), so it is not
the one worker without them (DESIGN §9 *A worker never kills a process by
name*). Output: shell assignments for `eval` —
  guard_disallowed  DISALLOWED_TOOLS plus the guard's deny rules
  guard_notice      each notice as a "\\nNOTE FROM ORCHESTRATOR: ..." line
  guard_settings    the hook's `--settings` JSON, or empty
  guard_env         a bash array of NAME=value words for `env`
Exits non-zero on any error; the caller then falls back to its embedded
base deny list."""
import json
import os
import shlex
import sys


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4):
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    allowed_tools, repo_root = argv[1], argv[2]
    state: dict = {}
    if len(argv) == 4:
        try:
            with open(argv[3]) as f:
                loaded = json.load(f)
            state = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            # No run state (collection after a crash before the first save):
            # the guard then carries no task-specific forbidden commands.
            state = {}
    sys.path.insert(0, os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "orchestrator"))
    import leerie
    guard = leerie._worker_guard(allowed_tools, repo_root, state,
                                 worker="integrator")
    disallowed = leerie.DISALLOWED_TOOLS + (
        "," + guard["deny"] if guard["deny"] else "")
    notice = "".join(f"\nNOTE FROM ORCHESTRATOR: {n}"
                     for n in guard["notices"])
    env_words = " ".join(shlex.quote(f"{k}={v}")
                         for k, v in sorted(guard["env"].items()))
    sys.stdout.write(
        f"guard_disallowed={shlex.quote(disallowed)}\n"
        f"guard_notice={shlex.quote(notice)}\n"
        f"guard_settings={shlex.quote(guard['settings'] or '')}\n"
        f"guard_env=({env_words})\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
