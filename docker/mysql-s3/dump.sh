#!/bin/bash
set -euo pipefail

# dump.sh - Dump MySQL databases to SQL files, optionally synced to S3
#
# Required env vars:
#   MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD
#
# Optional env vars:
#   MYSQL_DATABASE    - Single database to dump (required unless ALL_DATABASES is set)
#   MYSQL_TABLE       - Single table to dump (requires MYSQL_DATABASE)
#   MYSQL_PORT        - Port (default: 3306)
#   ALL_DATABASES     - Set to any value to dump all user databases
#   IGNORE_DATABASE   - Database name to skip when using ALL_DATABASES
#   MYSQLDUMP_OPTS    - Extra options for mysqldump (default: --set-gtid-purged=OFF)
#   S3_SYNC           - Set to any value to enable S3 sync after dump
#   S3_BUCKET         - S3 bucket name (required if S3_SYNC is set)
#   S3_BUCKET_PREFIX  - S3 key prefix for upload
#   LOCAL_DIR         - Local directory for dump files (default: /tmp/s3_local)

MYSQL_PORT="${MYSQL_PORT:-3306}"
MYSQLDUMP_OPTS="${MYSQLDUMP_OPTS:---set-gtid-purged=OFF}"
LOCAL_DIR="${LOCAL_DIR:-/tmp/s3_local}"

# --- Validation ---

if [[ -z "${MYSQL_HOST:-}" ]]; then
  echo "ERROR: Missing MYSQL_HOST env variable"
  exit 1
fi
if [[ -z "${MYSQL_USER:-}" ]]; then
  echo "ERROR: Missing MYSQL_USER env variable"
  exit 1
fi
if [[ -z "${MYSQL_PASSWORD:-}" ]]; then
  echo "ERROR: Missing MYSQL_PASSWORD env variable"
  exit 1
fi
if [[ -n "${S3_SYNC:-}" && -z "${S3_BUCKET:-}" ]]; then
  echo "ERROR: S3_SYNC is set but missing S3_BUCKET env variable"
  exit 1
fi
if [[ -z "${ALL_DATABASES:-}" && -z "${MYSQL_DATABASE:-}" ]]; then
  echo "ERROR: Missing MYSQL_DATABASE env variable (or set ALL_DATABASES)"
  exit 1
fi

# --- MySQL credentials file (avoids password quoting issues with special chars) ---

MYSQL_CNF=$(mktemp)
chmod 600 "$MYSQL_CNF"
trap 'rm -f "$MYSQL_CNF"' EXIT

_escaped_pw="${MYSQL_PASSWORD//\\/\\\\}"
_escaped_pw="${_escaped_pw//\"/\\\"}"
printf '[client]\npassword="%s"\n' "$_escaped_pw" > "$MYSQL_CNF"

# --- Dump ---

mkdir -p "$LOCAL_DIR"

if [[ -z "${ALL_DATABASES:-}" ]]; then
  # Single database dump
  if [[ -z "${MYSQL_TABLE:-}" ]]; then
    echo "Dumping database: ${MYSQL_DATABASE} to ${LOCAL_DIR}/${MYSQL_DATABASE}.sql"
    # shellcheck disable=SC2086
    mysqldump --defaults-extra-file="$MYSQL_CNF" $MYSQLDUMP_OPTS \
      --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
      "$MYSQL_DATABASE" > "${LOCAL_DIR}/${MYSQL_DATABASE}.sql"
  else
    echo "Dumping table: ${MYSQL_DATABASE}.${MYSQL_TABLE} to ${LOCAL_DIR}/${MYSQL_DATABASE}_${MYSQL_TABLE}.sql"
    # shellcheck disable=SC2086
    mysqldump --defaults-extra-file="$MYSQL_CNF" $MYSQLDUMP_OPTS \
      --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
      "$MYSQL_DATABASE" "$MYSQL_TABLE" > "${LOCAL_DIR}/${MYSQL_DATABASE}_${MYSQL_TABLE}.sql"
  fi
else
  # All databases dump
  echo "Discovering databases..."
  databases=$(mysql --defaults-extra-file="$MYSQL_CNF" \
    --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
    --batch --skip-column-names -e "SHOW DATABASES;")

  for db in $databases; do
    # Skip system databases
    if [[ "$db" == "information_schema" || "$db" == "performance_schema" || \
          "$db" == "mysql" || "$db" == "sys" || "$db" == "tmp" || \
          "$db" == "${IGNORE_DATABASE:-}" || "$db" == _* ]]; then
      continue
    fi
    echo "Dumping database: ${db}"
    # shellcheck disable=SC2086
    mysqldump --defaults-extra-file="$MYSQL_CNF" $MYSQLDUMP_OPTS \
      --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
      --databases "$db" > "${LOCAL_DIR}/${db}.sql"
  done
fi

echo "Dump complete"

# --- S3 sync ---

if [[ -n "${S3_SYNC:-}" ]]; then
  s3_uri="s3://${S3_BUCKET}/${S3_BUCKET_PREFIX:+${S3_BUCKET_PREFIX}/}"
  echo "Syncing files to ${s3_uri}"
  aws s3 sync "$LOCAL_DIR/" "$s3_uri"
  echo "S3 sync complete"
fi
