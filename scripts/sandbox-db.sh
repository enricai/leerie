#!/usr/bin/env bash
# sandbox-db.sh — a throwaway PostgreSQL database for ONE command, inside
# that command's own confinement and isolation (DESIGN §6 *No database
# server leerie starts is ever unconfined*).
#
#   sandbox-db.sh <pkg-dir> <db-name> -- CMD...
#
# Run as the confined command itself (under the Landlock shim, inside its
# isolation or held-out sandbox, with its grant). It:
#   1. initdb's a cluster in /tmp/leerie-pg/data — the command's own /tmp;
#   2. starts postgres on 127.0.0.1:5432 — the loopback of the command's
#      own network namespace, which no other process reaches, so concurrent
#      commands never collide on the port; a port already taken there is a
#      failed start, never another server's — its socket in /tmp/leerie-pg;
#   3. creates <db-name>;
#   4. runs <pkg-dir>/node_modules/.bin/prisma db push, with Prisma's
#      AI-agent consent on that one process;
#   5. execs CMD with DATABASE_URL and DIRECT_URL, a plain TCP URL
#      (postgresql://<user>@127.0.0.1:5432/<db-name>): a repository's own
#      tooling that reads host and port from the URL reaches it.
# Every process it starts is the command's: the shim ends the server with
# it. Without server binaries, or as root (postgres refuses), or when a step
# fails, it execs CMD unchanged, saying so on stderr. Everything it prints
# goes to stderr: CMD's stdout is CMD's alone (a worker's is its event
# stream). Leerie composes <db-name> and checks the URL this script composes
# (`_test_db_argv`, `_test_db_url_refusal`).
set -uo pipefail

if [ "$#" -lt 3 ] || [ "$3" != "--" ]; then
  echo "usage: sandbox-db.sh <pkg-dir> <db-name> -- CMD..." >&2
  exit 125
fi
pkg=$1
name=$2
shift 3
if [ "$#" -eq 0 ]; then
  echo "sandbox-db.sh: no command" >&2
  exit 125
fi
if ! [[ "$name" =~ ^leerie_[0-9a-f]{16}_test$ ]]; then
  echo "sandbox-db.sh: refused database name '$name' (not leerie_<16 hex>_test)" >&2
  exit 125
fi

say() { echo "sandbox-db.sh: $*" >&2; }

if [ "$(id -u)" = 0 ]; then
  say "running as root, which postgres refuses — running the command without a database"
  exec "$@"
fi

# The newest complete server install, else the one on PATH.
pgbin=""
best=-1
for d in /usr/lib/postgresql/*/bin; do
  [ -x "$d/initdb" ] && [ -x "$d/pg_ctl" ] && [ -x "$d/createdb" ] || continue
  major=$(basename "$(dirname "$d")")
  [[ "$major" =~ ^[0-9]+$ ]] || major=0
  if [ "$major" -gt "$best" ]; then best=$major; pgbin=$d; fi
done
if [ -z "$pgbin" ] && command -v initdb >/dev/null 2>&1; then
  pgbin=$(dirname "$(command -v initdb)")
  { [ -x "$pgbin/pg_ctl" ] && [ -x "$pgbin/createdb" ]; } || pgbin=""
fi
if [ -z "$pgbin" ]; then
  say "no PostgreSQL server binaries — running the command without a database"
  exec "$@"
fi

user=$(id -un)
dir=/tmp/leerie-pg
url="postgresql://${user}@127.0.0.1:5432/${name}"

if ! mkdir -p "$dir" 2>/dev/null || ! chmod 0700 "$dir" 2>/dev/null; then
  say "cannot make $dir — running the command without a database"
  exec "$@"
fi
if ! "$pgbin/initdb" -D "$dir/data" -U "$user" --auth-local=trust \
    --auth-host=trust \
    >"$dir/initdb.log" 2>&1; then
  say "initdb failed: $(tail -c 300 "$dir/initdb.log" 2>/dev/null) — running the command without a database"
  exec "$@"
fi
if ! "$pgbin/pg_ctl" -D "$dir/data" -l "$dir/server.log" -w \
    -o "-c listen_addresses=127.0.0.1 -c port=5432 -c unix_socket_directories=$dir -c fsync=off" \
    start >&2; then
  say "postgres did not start: $(tail -c 300 "$dir/server.log" 2>/dev/null) — running the command without a database"
  exec "$@"
fi
if ! "$pgbin/createdb" -h "$dir" -U "$user" "$name" >&2; then
  say "createdb $name failed — running the command without a database"
  exec "$@"
fi
prisma="$pkg/node_modules/.bin/prisma"
if [ ! -x "$prisma" ]; then
  say "no installed prisma CLI in $pkg — running the command without a database"
  exec "$@"
fi
if ! (cd "$pkg" && env DATABASE_URL="$url" DIRECT_URL="$url" \
        PRISMA_USER_CONSENT_FOR_DANGEROUS_AI_ACTION="yes, allow prisma db push on throwaway test databases" \
        "$prisma" db push --accept-data-loss) >&2; then
  say "prisma db push to $name failed — running the command without a database"
  exec "$@"
fi
export DATABASE_URL="$url" DIRECT_URL="$url"
exec "$@"
