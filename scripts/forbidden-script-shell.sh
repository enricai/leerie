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
exec /bin/sh "$@"
