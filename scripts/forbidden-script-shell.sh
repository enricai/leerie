#!/bin/sh
# Runtime guard for a package script the task forbids (DESIGN §9 *A command
# the task forbids is denied in every spelling*). `claude_p` points npm's and
# pnpm's `script-shell` at this file for a worker whose task forbids a
# package script, so every script the package manager runs passes through
# here — including one reached through another script (`pnpm run ci` whose
# body is `pnpm test`), which no command-line matcher can see. Benched as
# P10's runtime layer (bench/f2 tools/guard.sh); unchanged in substance.
# An empty event name (not a package-manager run) or list never matches.
[ -n "$npm_lifecycle_event" ] && [ -n "$LEERIE_FORBIDDEN_SCRIPTS" ] &&
case " $LEERIE_FORBIDDEN_SCRIPTS " in
  *" $npm_lifecycle_event "*)
    echo "leerie: script '$npm_lifecycle_event' is forbidden for this run" >&2
    exit 97 ;;
esac
# Every other script runs with the shell the REPOSITORY configured
# (`script-shell=bash` in .npmrc, `scriptShell` in pnpm-workspace.yaml):
# the environment variable that routed it here outranks that setting, so
# replacing it with /bin/sh broke a repo's bash-syntax scripts. The package
# manager that ran this script (`npm_config_user_agent`) is asked for its
# setting with leerie's override removed; the answer is cached in the
# environment for the scripts this one runs.
pm=${npm_config_user_agent%%/*}
case "$pm" in npm|pnpm|yarn) ;; *) pm= ;; esac
cache=LEERIE_REPO_SCRIPT_SHELL_$pm
eval "shell=\${$cache-}"
if [ -n "$pm" ] && [ -z "$shell" ]; then
  shell=$(env -u npm_config_script_shell -u pnpm_config_script_shell \
          -u NPM_CONFIG_SCRIPT_SHELL "$pm" config get script-shell \
          2>/dev/null </dev/null | tail -n 1)
  case "$shell" in
    ""|undefined|null|"$0") shell=/bin/sh ;;
  esac
  export "$cache=$shell"
fi
exec "${shell:-/bin/sh}" "$@"
