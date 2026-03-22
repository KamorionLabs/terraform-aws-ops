#!/bin/bash
set -euo pipefail

# import.sh - Import SQL files into MySQL, optionally synced from S3
#
# Required env vars:
#   MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD
#
# Optional env vars:
#   MYSQL_DATABASE  - Target database (if unset, SQL files must contain USE statements)
#   MYSQL_PORT      - Port (default: 3306)
#   S3_SYNC         - Set to any value to enable S3 sync before import
#   S3_BUCKET       - S3 bucket name (required if S3_SYNC is set)
#   S3_PREFIX       - S3 key prefix (optional, stripped of trailing slash)
#   LOCAL_DIR       - Local directory for SQL files (default: /tmp/s3_local)
#   CONTINUE_ON_ERROR - Set to any value to continue on SQL import errors

MYSQL_PORT="${MYSQL_PORT:-3306}"
S3_PREFIX="${S3_PREFIX%/}"
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

# --- MySQL credentials file (avoids password quoting issues with special chars) ---

MYSQL_CNF=$(mktemp)
chmod 600 "$MYSQL_CNF"
trap 'rm -f "$MYSQL_CNF"' EXIT

# Escape backslashes and double quotes for MySQL config file format
_escaped_pw="${MYSQL_PASSWORD//\\/\\\\}"
_escaped_pw="${_escaped_pw//\"/\\\"}"
printf '[client]\npassword="%s"\n' "$_escaped_pw" > "$MYSQL_CNF"

# --- S3 sync ---

mkdir -p "$LOCAL_DIR"

if [[ -n "${S3_SYNC:-}" ]]; then
  s3_uri="s3://${S3_BUCKET}/${S3_PREFIX:+${S3_PREFIX}/}"
  echo "Syncing files from ${s3_uri} to ${LOCAL_DIR}"
  aws s3 sync "$s3_uri" "$LOCAL_DIR"
  echo "S3 sync complete"
fi

# --- Import ---

file_count=$(find "$LOCAL_DIR" -maxdepth 1 -name '*.sql' -type f | wc -l)
if [[ "$file_count" -eq 0 ]]; then
  echo "WARNING: No .sql files found in ${LOCAL_DIR}"
  exit 0
fi

echo "Importing ${file_count} SQL file(s) to ${MYSQL_HOST}:${MYSQL_PORT}/${MYSQL_DATABASE:-<no database>}"

errors=0
for file in "$LOCAL_DIR"/*.sql; do
  echo "Importing $(basename "$file") ..."
  if [[ -n "${MYSQL_DATABASE:-}" ]]; then
    if ! mysql --defaults-extra-file="$MYSQL_CNF" \
         --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
         "$MYSQL_DATABASE" < "$file"; then
      echo "ERROR: Failed to import $(basename "$file")"
      errors=$((errors + 1))
      [[ -z "${CONTINUE_ON_ERROR:-}" ]] && exit 1
    fi
  else
    if ! mysql --defaults-extra-file="$MYSQL_CNF" \
         --host="$MYSQL_HOST" --port="$MYSQL_PORT" --user="$MYSQL_USER" \
         < "$file"; then
      echo "ERROR: Failed to import $(basename "$file")"
      errors=$((errors + 1))
      [[ -z "${CONTINUE_ON_ERROR:-}" ]] && exit 1
    fi
  fi
done

if [[ "$errors" -gt 0 ]]; then
  echo "WARNING: ${errors} file(s) failed to import"
  exit 1
fi

echo "Import complete"
