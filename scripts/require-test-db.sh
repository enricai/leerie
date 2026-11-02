#!/usr/bin/env bash
# require-test-db.sh — run a command only with the test database it was
# promised (DESIGN §8: the writer's REAL DATABASE section follows a database
# that is there, never the repository's shape).
#
#   require-test-db.sh <url> -- CMD...
#
# Runs right after scripts/sandbox-db.sh, which execs its command without a
# database whenever a step fails. Exits 3 (`TEST_DB_ABSENT_EXIT`) with one
# stderr line unless DATABASE_URL is exactly <url> (the URL sandbox-db.sh
# composes, `_test_db_url`); otherwise execs CMD.
set -uo pipefail
if [ "$#" -lt 3 ] || [ "$2" != "--" ]; then
  echo "usage: require-test-db.sh <url> -- CMD..." >&2
  exit 125
fi
want=$1
shift 2
if [ "${DATABASE_URL:-}" != "$want" ]; then
  echo "require-test-db.sh: the test database this command requires is absent (sandbox-db.sh ran it without one)" >&2
  exit 3
fi
exec "$@"
