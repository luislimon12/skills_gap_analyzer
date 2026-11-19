# Production plan (v2): status

Status key: [x] done in this repo, [ ] still to do (mostly manual: accounts,
servers, Cloudflare), [?] waiting on a decision. "Where" points at the code or
runbook step.

## 1. Decisions still open

- [?] **LoopCV**: adapter stub in `src/sources/loopcv.py`, off by default (`sources.loopcv.enabled` in `config/ingestion.yaml`). App runs on Adzuna alone.
- [?] **Adzuna plan**: quota, results per page and max page depth are config values marked CONFIRM in `config/ingestion.yaml` (defaults: 250/day, 1,000/week, 2,500/month, 50 per page, 20 pages).
- [?] **Resume handling**: implemented both. Default is option (a), skills only (`resumes.store_file: false`). Option (b) encrypts the file with `ENCRYPTION_KEY`.
- [?] **Home ISP**: manual check (deploy/README.md, Windows section).
- [?] **Launch scope**: no code impact.
- [?] **Stale window**: `lifecycle.stale_days_before_closed: 3`, with one full re-scan per slice per day (`backfill.rescan_interval_hours: 24`).

## 2. Hosting
- [ ] Oracle account, region, A1 VM, idle-reclaim monitoring: manual, see deploy/README.md §2.
- [x] Develop locally first, then deploy: SQLite by default, PostgreSQL via `DATABASE_URL`.
- [ ] Cloudflare DNS/WAF/HTTPS: manual, deploy/README.md §5; tunnel config in `deploy/cloudflare/`.

## 3. Setup and environment
- [ ] Python 3.11+, venv, PostgreSQL install: deploy/README.md §2–3; `deploy/postgres/setup.sql` creates a restricted role.
- [x] `.env.example` lists `ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `DATABASE_URL`, `SECRET_KEY`, `ENCRYPTION_KEY` (+ alert and backup settings).
- [x] `.env` is gitignored. Git history checked: only placeholder values (`your_key`, etc.) were ever committed, so no key needs rotating.
- [ ] Home PC power/UPS: manual, deploy/README.md (Windows section).

## 4. Codebase changes
- [x] `JobSource` interface with `fetch(criteria)` → `src/sources/base.py`.
- [x] `Job` schema (title, company, location, country, salary range, description summary, URL, source, external ID, posted date, fetched date) → `src/sources/base.py`.
- [x] Adzuna adapter → `src/sources/adzuna.py`.
- [x] LoopCV stub → `src/sources/loopcv.py`.
- [x] USAJOBS removed: fetcher, normaliser, config block, `.env` entries, dashboard references, test fixtures, README and guide.
- [x] Old tests replaced by a new suite of about 75 tests (`tests/`), run in GitHub Actions on SQLite and PostgreSQL with no API calls.

## 5. Database and storage
- [x] `jobs` with unique (source, external_id) → `src/db/schema.py`.
- [x] Lifecycle fields `first_seen_at`, `last_seen_at`, `status`, `closed_at`, `last_checked_at` (+ `stale_since`).
- [x] `fetch_runs` (start, end, source, found, inserted, requests used, status, error, + updated/reactivated/stale/closed counts).
- [x] `backfill_tasks` (source, country, query, page, status, last run time, + mode and scan times).
- [x] `resumes` (id, anonymous session ID, extracted skills or encrypted file path, upload time, `delete_after` = upload + 30 days).
- [x] `analyses` linked to a resume, deleted with it.
- [x] Alembic migrations → `alembic.ini`, `migrations/versions/0001_production_schema.py`; `python -m src.db.migrate`.
- [x] Jobs live in PostgreSQL on the VM (or SQLite locally).
- [x] Optional resume files: encrypted, random names, under `data/private/resumes` (outside any web root).
- [x] No third-party managed database: config only supports your own PostgreSQL/SQLite.
- [x] Database and worker on the same VM (systemd units).
- [ ] Track free-tier storage: manual, deploy/README.md §2.

## 6. Ingestion worker
- [x] One cycle: pending work → query → normalise/upsert → `fetch_runs` row → `src/worker/cycle.py`.
- [x] Fallback rule LoopCV → Adzuna, source used is logged and stored in `fetch_runs.sources_used` → `src/sources/fallback.py`.
- [x] Request budget (daily, weekly, monthly) → `src/worker/budget.py`.
- [x] Retry with backoff; permanent errors fail the run cleanly → `fetch_with_retry`.
- [x] Highest-value terms and countries first (list order = priority).
- [x] Raw responses kept 7 days → `src/worker/raw_store.py`.

## 7. Backfill and lifecycle
- [x] Slices per country × search term × time window (`backfill.max_days_old`).
- [x] Backfill share 70% of the daily quota.
- [x] Runs take pending pages until the budget runs out, then log where they stopped.
- [x] Newest-first (`sort_by=date`, priority order).
- [x] Slice complete on a short page; switches to incremental mode.
- [x] `last_seen_at` updated on every fetch.
- [x] Unseen jobs marked `stale` after a full re-scan (skipped if the scan hit the page-depth limit).
- [x] `closed` after the stale window.
- [x] Capped daily link check (404/410 → closed) → `src/worker/link_check.py`.
- [x] Closed jobs reactivated when they reappear, logged.
- [x] Closed jobs hidden from results; "No longer listed" label when shown.

## 8. Scheduling
- [x] Windows Task Scheduler (hourly, run whether logged on or not, start after missed run) → `deploy/windows/register-worker-tasks.ps1`.
- [x] systemd timer with `Persistent=true` (+ cron example) → `deploy/systemd/`.
- [x] Single instance: OS file lock `data/worker.lock` → `src/worker/lock.py`.
- [x] Daily failure check: `python -m src.worker report --days 1 --email` on a daily timer.

## 9. Security
- [ ] Router ports closed, UPnP off, Cloudflare Tunnel, Access, WAF, bot protection, edge rate limits, header Transform Rule: manual, deploy/README.md §5.
- [x] App binds to 127.0.0.1 (`.streamlit/config.toml`, systemd unit).
- [x] Non-root service user and hardened systemd units; non-superuser database role.
- [ ] Firewall, RDP/file sharing off, BitLocker, automatic updates, antivirus: manual (OCI volumes are encrypted by default).
- [x] PDF/DOCX only, checked by file signature, 5 MB cap, macro DOCX rejected → `src/resume/upload_validation.py`.
- [x] Files that fail to parse are rejected.
- [x] SQLAlchemy parameterised queries everywhere in the new code (legacy pipeline uses `?` placeholders).
- [x] Keys kept out of code and logs (Adzuna errors redacted, urllib3 debug logging silenced).
- [x] No resume text or personal details logged; analyses store scores only.
- [x] Rate limits per IP and per anonymous session on upload and analysis → `src/dashboard/security.py`.
- [x] Debug details off for the public (`showErrorDetails = false`), generic DB error message.
- [x] `pip-audit` added for the pre-release dependency check (deploy/README.md §7).
- [x] Alert email on worker failure and low disk space → `src/worker/alerts.py`.
- [x] `fetch_runs` is the ingestion audit log.
- [ ] Weekly review of Cloudflare security events: manual.

## 10. Resume privacy and 30-day deletion
- [x] Stored files encrypted at rest; key in `.env`, not the database.
- [x] Daily cleanup (and every hourly run) deletes expired resumes: files, analyses and rows together; cache entries expire after 1 hour and are cleared on delete; deletions logged by random ID only.
- [x] Backups exclude resume tables.
- [x] "Delete my resume" button and the deletion date after upload.

## 11. Backups
- [x] Encrypted with `age` to a public key; private key kept off the server → `deploy/backup/backup.sh`.
- [x] OCI Object Storage upload (or `backup-home.ps1` to an encrypted drive).
- [x] 30-day bucket lifecycle rule → `deploy/backup/setup-bucket-lifecycle.sh`.
- [x] Nightly backup timer.
- [ ] Test a full restore before launch and monthly: `deploy/backup/restore-test.sh` (needs the real server).

## 12. Web app
- [x] Reads jobs from the database only; the "Fetch latest postings" button is gone.
- [x] Rate limiting on upload and analysis.
- [x] Privacy policy page → `src/dashboard/pages/1_Privacy_Policy.py` (set `privacy_contact` in `config/settings.yaml`).
- [x] "How your data is used" note on the upload control.
- [x] Clear message when the database has no jobs yet.

## 13. Testing
- [x] Adapter tests with a recorded Adzuna response.
- [x] Deduplication tests.
- [x] Lifecycle tests (stale, closed, reactivated).
- [x] 30-day deletion tests (files and rows together).
- [x] Fallback tests.
- [x] Full worker run against a copy of the database.
- [ ] Manual check of upload, analysis and delete in the browser.

## 14–15. Presentation and after
- [ ] Load a realistic sample before the demo (`python -m src.worker run` repeatedly over a few days, budget permitting).
- [ ] Screen recording backup, data-policy talking points, feedback.
- [ ] After: review logs and quota, add LoopCV, legal review of the privacy policy, move fully to the VM.
