#!/usr/bin/env bash
# Run Alembic against Neon's DIRECT connection string, read from stdin so it never reaches a shell
# command line, a file, or the shell history.
#
# Why stdin: a Neon URL contains ? and &, which both zsh and bash parse as syntax. Pasting one into a
# command line is a quoting accident waiting to happen, and quoting it correctly still records it in
# history. Read from stdin it is just bytes.
#
# Why the direct string and not the pooled one: the pooled host runs PgBouncer in transaction mode.
# The Lambda wants that; a migration does not. The rule is in SPEC.md's deployment notes.
#
# Usage:  ./scripts/migrate_neon.sh            (prompts, then confirms before migrating)
#         ./scripts/migrate_neon.sh --check    (connect and report the schema; migrate nothing)
set -euo pipefail

cd "$(dirname "$0")/.."

CHECK_ONLY=false
[ "${1:-}" = "--check" ] && CHECK_ONLY=true

printf 'Paste the Neon DIRECT connection string (not the -pooler one), then press Enter.\n'
printf 'It will not be echoed: '
IFS= read -rs RAW_URL
printf '\n'

[ -n "$RAW_URL" ] || { printf 'Nothing was pasted.\n' >&2; exit 1; }

# Normalize the scheme SQLAlchemy needs, leaving every query parameter Neon supplied untouched.
case "$RAW_URL" in
  postgresql+psycopg://*) URL="$RAW_URL" ;;
  postgresql://*)         URL="postgresql+psycopg://${RAW_URL#postgresql://}" ;;
  postgres://*)           URL="postgresql+psycopg://${RAW_URL#postgres://}" ;;
  *) printf 'That does not look like a Postgres URL (expected it to start with postgresql://).\n' >&2; exit 1 ;;
esac

# Everything below reports the host and never the credentials.
HOST_AND_PATH="${URL#*@}"
HOST="${HOST_AND_PATH%%\?*}"
printf 'Target: %s\n' "$HOST"

case "$HOST" in
  *-pooler.*) printf 'That is the POOLED host. Migrations use the direct one; re-run and paste that.\n' >&2; exit 1 ;;
esac

export DATABASE_URL="$URL"

printf 'Connecting... '
# The traceback is swallowed on purpose. SQLAlchemy masks the password in a connection error but still
# prints the username, the full URL and 120 lines of frames; only the reason is useful here.
.venv/bin/python - <<'PY'
import os
import sys

from sqlalchemy import create_engine, text

try:
    with create_engine(os.environ["DATABASE_URL"]).connect() as conn:
        version = conn.execute(text("SHOW server_version")).scalar_one()
        tables = conn.execute(
            text("SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
        ).scalar_one()
        revision = None
        if conn.execute(text("SELECT to_regclass('public.alembic_version')")).scalar_one():
            revision = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one_or_none()
except Exception as error:
    reason = str(getattr(error, "orig", error)).strip().splitlines()
    print("failed.", file=sys.stderr)
    print(f"  {type(error).__name__}: {reason[0] if reason else 'no detail'}", file=sys.stderr)
    sys.exit(1)
print(f"ok. Postgres {version}, {tables} tables in public, alembic revision: {revision or 'none'}")
PY

if [ "$CHECK_ONLY" = true ]; then
  printf 'Checked only; nothing migrated.\n'
  exit 0
fi

printf 'Run "alembic upgrade head" against %s? [y/N] ' "$HOST"
read -r CONFIRM
case "$CONFIRM" in
  y|Y|yes|YES) ;;
  *) printf 'Aborted; nothing migrated.\n'; exit 0 ;;
esac

.venv/bin/alembic upgrade head
printf '\nDone. Schema is at head on %s\n' "$HOST"
