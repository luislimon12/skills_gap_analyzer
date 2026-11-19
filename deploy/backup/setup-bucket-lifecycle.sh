#!/usr/bin/env bash
# One-time: delete backup objects 30 days after upload (production plan, section 11).
set -euo pipefail
: "${BACKUP_BUCKET:?BACKUP_BUCKET is not set}"
oci os object-lifecycle-policy put --bucket-name "$BACKUP_BUCKET" --force --items '[
  {"name": "delete-backups-after-30-days", "action": "DELETE", "isEnabled": true,
   "timeAmount": 30, "timeUnit": "DAYS",
   "objectNameFilter": {"inclusionPrefixes": ["backups/"]}}
]'
echo "Lifecycle rule set on $BACKUP_BUCKET"
