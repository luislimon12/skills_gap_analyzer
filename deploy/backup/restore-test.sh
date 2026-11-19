#!/usr/bin/env bash
# Restore the newest backup into a throwaway database and sanity-check it.
# Run before launch and once a month (production plan, section 11).
#
# Usage: AGE_IDENTITY_FILE=~/backup-key.txt BACKUP_BUCKET=... ./restore-test.sh
# The age private key file is only needed for this test; don't leave it on the server.
set -euo pipefail

: "${AGE_IDENTITY_FILE:?path to the age private key}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is not set}"
TEST_DB="${TEST_DB:-skills_gap_restore_test}"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

LATEST="$(oci os object list --bucket-name "$BACKUP_BUCKET" --prefix backups/ --all \
  --query 'sort_by(data, &"time-created")[-1].name' --raw-output)"
echo "Restoring $LATEST into $TEST_DB"
oci os object get --bucket-name "$BACKUP_BUCKET" --name "$LATEST" --file "$WORK/backup.age" >/dev/null
age --decrypt --identity "$AGE_IDENTITY_FILE" --output "$WORK/backup.dump" "$WORK/backup.age"

dropdb --if-exists "$TEST_DB"
createdb "$TEST_DB"
pg_restore --no-owner --dbname="$TEST_DB" "$WORK/backup.dump"

psql --dbname="$TEST_DB" --tuples-only --command "
  SELECT 'jobs: '        || count(*) FROM jobs
  UNION ALL SELECT 'fetch_runs: '  || count(*) FROM fetch_runs
  UNION ALL SELECT 'resumes (expect 0): ' || count(*) FROM resumes
  UNION ALL SELECT 'schema version: ' || version_num FROM alembic_version;"

dropdb "$TEST_DB"
echo "Restore test passed."
