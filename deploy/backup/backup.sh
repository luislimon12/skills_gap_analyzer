#!/usr/bin/env bash
# Nightly encrypted PostgreSQL backup to OCI Object Storage (production plan, section 11).
#
# * Resume data (resumes, analyses) is EXCLUDED from backups, so no backup can
#   hold a resume past its 30-day deletion date.
# * The dump is encrypted with `age` to a public key; the private key is NOT on
#   this server (keep it in a password manager / offline).
# * The bucket's lifecycle rule (setup-bucket-lifecycle.sh) deletes objects after 30 days.
#
# Needs: pg_dump, age, oci CLI (configured for the VM's instance principal or a user).
# Env (from /opt/skills_gap_analyzer/.env or the systemd unit):
#   DATABASE_URL           postgresql://...  (the "+psycopg" driver suffix is stripped)
#   BACKUP_AGE_RECIPIENT   age public key, e.g. age1...
#   BACKUP_BUCKET          OCI bucket name
set -euo pipefail

: "${DATABASE_URL:?DATABASE_URL is not set}"
: "${BACKUP_AGE_RECIPIENT:?BACKUP_AGE_RECIPIENT is not set}"
: "${BACKUP_BUCKET:?BACKUP_BUCKET is not set}"

PG_URL="${DATABASE_URL/postgresql+psycopg:/postgresql:}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT
umask 077

pg_dump "$PG_URL" --format=custom --no-owner \
  --exclude-table-data=resumes \
  --exclude-table-data=analyses \
  --file="$WORK/skills_gap.dump"

age --encrypt --recipient "$BACKUP_AGE_RECIPIENT" \
  --output "$WORK/skills_gap_${STAMP}.dump.age" "$WORK/skills_gap.dump"

oci os object put --bucket-name "$BACKUP_BUCKET" \
  --name "backups/skills_gap_${STAMP}.dump.age" \
  --file "$WORK/skills_gap_${STAMP}.dump.age" --no-multipart --force >/dev/null

echo "Backup uploaded: backups/skills_gap_${STAMP}.dump.age"
