#!/usr/bin/env python3
"""Print the worker guard for a `claude -p` command line built in bash.

Usage: worker-guard.py <allowed_tools> <repo_root> [<state.json> [<wid>]]

The worker's own CLI configuration and temp directory
(`<run-dir>/workers/<wid>/`, `_confined_cli_env`, the builder every confined
CLI spawn uses) and the run's shared caches are made and named in
`guard_env`; without `<wid>` (or `<state.json>`) it exits 1, since no
confined CLI runs without its own directories.

The Fly recovery integrator (`scripts/remote/collect-subtrees.sh`) builds its
own `claude` argv in bash, after the orchestrator has exited. It takes the
deny rules, notices, PreToolUse hook and script-shell environment from the
same construction every orchestrator-spawned worker gets
(`_worker_guard` in orchestrator/leerie.py, imported from the sibling
`orchestrator/` directory as `forbidden-command-hook.py` does), so it is not
the one worker without them (DESIGN §9 *A worker never kills a process by
name*). Output: shell assignments for `eval` —
  guard_disallowed  DISALLOWED_TOOLS plus the guard's deny rules
  guard_notice      each notice as a "\\nNOTE FROM ORCHESTRATOR: ..." line,
                    the last one where its grant lets it write
  guard_settings    the worker's whole `--settings` JSON
                    (`_confined_cli_settings`: the operator's, the user's
                    checkout's and leerie's own, the shell prefix among
                    them), passed with `--setting-sources ""` (DESIGN §6
                    *A worker's commands cannot reach its CLI*)
  guard_env         a bash array of NAME=value words for `env`, the
                    CLI's own git put on leerie's terms among them
                    (`_cli_git_env`)
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
    # Every command and hook of the CLI in a Landlock domain nested in its
    # own (DESIGN §6 *A worker's commands cannot reach its CLI*).
    env[leerie.SHELL_PREFIX_ENV] = str(leerie.WORKER_SHELL)
    if len(argv) != 5:
        # A confined CLI runs only with its own directories (DESIGN §6
        # *Every confined CLI is given its own directories, by one
        # builder*): an inherited `TMPDIR` outside its grant stops it at
        # start.
        sys.stderr.write("worker-guard: no worker id to make the worker's "
                         "own directories for\n")
        return 1
    from pathlib import Path
    # The worker's own CLI configuration and temp directories, from the
    # builder every confined spawn uses, and the run's shared caches.
    own = leerie._confined_cli_env(Path(run_dir), argv[4])
    # The integrator runs after this process exits: its temp directory is
    # the collection's to remove (`guard_tmp`), not this exit's.
    leerie._PRIVATE_TMPS.pop(own["TMPDIR"], None)
    env.update(own)
    cached = leerie._worker_cache_env(os.environ, Path(run_dir))
    env.update({k: v for k, v in cached.items()
                if os.environ.get(k) != v})
    genv = {**os.environ, **env}
    grants = leerie._worker_grants(
        run_dir=run_dir, state=state, schema_key="integrator", cwd=repo_root,
        allowed_tools=allowed_tools, env=genv)
    # The CLI's own git on leerie's terms, as `_invoke` gives every confined
    # worker (DESIGN §6 *The CLI's own git runs on leerie's terms*): the
    # tree's settings and `.git` put back, and the CLI's `git` first on its
    # PATH. After the grants, which read the PATH the CLI is found on.
    leerie._ready_tree_for_worker(repo_root)
    env.update(leerie._cli_git_env(repo_root, genv.get("PATH", "")))
    notices = list(guard["notices"])
    if leerie._bash_unrestricted(allowed_tools):
        # Where it can write, from its own grant, as `claude_p` tells it.
        notices.append(leerie._confined_writes_notice(
            grants, cwd=repo_root, env=genv))
    # After `_worker_dirs`, which makes the template the operator's settings
    # are read from. The project settings from the user's checkout, as
    # `State` derives it from the state root (the run directory's
    # grandparent's parent); never the staging tree, which a worker writes.
    settings = leerie._confined_cli_settings(
        guard["settings"], run_dir=run_dir,
        repo_root=os.path.dirname(os.path.dirname(os.path.dirname(run_dir))))
    # Leerie's values on top of the settings' env, as `_invoke` pins them.
    settings = leerie._pin_cli_settings(
        ["--settings", settings],
        leerie._cli_pinned_env({**genv, **env}))[1]
    confine = " ".join(shlex.quote(w) for w in
                       leerie._confined_argv(grants, [], cli=True)[:-1])
    disallowed = leerie.DISALLOWED_TOOLS + (
        "," + guard["deny"] if guard["deny"] else "")
    notice = "".join(f"\nNOTE FROM ORCHESTRATOR: {n}" for n in notices)
    env_words = " ".join(shlex.quote(f"{k}={v}")
                         for k, v in sorted(env.items()))
    sys.stdout.write(
        f"guard_disallowed={shlex.quote(disallowed)}\n"
        f"guard_notice={shlex.quote(notice)}\n"
        f"guard_settings={shlex.quote(settings)}\n"
        f"guard_env=({env_words})\n"
        f"guard_confine=({confine} --)\n"
        f"guard_tmp={shlex.quote(own['TMPDIR'])}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
