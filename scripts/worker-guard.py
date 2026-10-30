#!/usr/bin/env python3
"""Print the worker guard for a `claude -p` command line built in bash.

Usage: worker-guard.py <allowed_tools> <repo_root> [<state.json> [<wid>]]

With `<wid>`, the worker's own CLI configuration and temp directory
(`<run-dir>/workers/<wid>/`, `_worker_dirs`) and the run's shared caches are
made and named in `guard_env`, as `claude_p` does for every worker.

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
  guard_confine     a bash array: the Landlock shim and the worker's grants
                    (`_worker_grants`, DESIGN §6 *A worker writes only where
                    it is granted*), to prefix the `claude` command with
Exits non-zero on any error, or when the kernel cannot confine a worker;
the caller then skips the branch — never runs it unconfined."""
import json
import os
import shlex
import sys


def main(argv: list[str]) -> int:
    if len(argv) not in (3, 4, 5):
        sys.stderr.write(__doc__.split("\n\n")[1] + "\n")
        return 2
    allowed_tools, repo_root = argv[1], argv[2]
    state: dict = {}
    if len(argv) >= 4:
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
    lk = leerie._landlock()
    if lk.abi() < lk.MIN_ABI:
        sys.stderr.write("worker-guard: this kernel cannot confine a worker "
                         f"(Landlock ABI {lk.abi()}, need {lk.MIN_ABI})\n")
        return 1
    run_dir = (os.path.dirname(os.path.abspath(argv[3])) if len(argv) >= 4
               else None)
    if run_dir is None:
        sys.stderr.write("worker-guard: no run directory to confine in\n")
        return 1
    env = dict(guard["env"])
    if len(argv) == 5:
        # The worker's own CLI configuration and temp directory, and the
        # run's shared caches, as `claude_p` gives every worker (DESIGN §6
        # *Each worker's own configuration and temporary directories*).
        from pathlib import Path
        config, tmp = leerie._worker_dirs(Path(run_dir), argv[4], None)
        env.update(CLAUDE_CONFIG_DIR=str(config), TMPDIR=str(tmp),
                   TMP=str(tmp), TEMP=str(tmp))
        cached = leerie._worker_cache_env(os.environ, Path(run_dir))
        env.update({k: v for k, v in cached.items()
                    if os.environ.get(k) != v})
    grants = leerie._worker_grants(
        run_dir=run_dir, state=state, schema_key="integrator", cwd=repo_root,
        allowed_tools=allowed_tools, env={**os.environ, **env})
    confine = " ".join(shlex.quote(w) for w in
                       leerie._confined_argv(grants, [])[:-1])
    disallowed = leerie.DISALLOWED_TOOLS + (
        "," + guard["deny"] if guard["deny"] else "")
    notice = "".join(f"\nNOTE FROM ORCHESTRATOR: {n}"
                     for n in guard["notices"])
    env_words = " ".join(shlex.quote(f"{k}={v}")
                         for k, v in sorted(env.items()))
    sys.stdout.write(
        f"guard_disallowed={shlex.quote(disallowed)}\n"
        f"guard_notice={shlex.quote(notice)}\n"
        f"guard_settings={shlex.quote(guard['settings'] or '')}\n"
        f"guard_env=({env_words})\n"
        f"guard_confine=({confine} --)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
