# Deployment runbook

How to run the Skills Gap Analyzer in production: an Oracle Cloud Always Free
ARM VM, PostgreSQL on the same VM, the web app behind Cloudflare Tunnel, and
an hourly ingestion worker. Steps that only a person can do (accounts, DNS,
billing, disk encryption on a home PC) are marked **[manual]**.

Files in this folder:

| File | What it is |
| --- | --- |
| `systemd/skills-gap-web.service` | Streamlit app on 127.0.0.1:8501 |
| `systemd/skills-gap-worker.{service,timer}` | hourly ingestion worker (`Persistent=true` catches up after a missed start) |
| `systemd/skills-gap-cleanup.{service,timer}` | daily 30-day resume deletion (backstop; every worker run also does it) |
| `systemd/skills-gap-report.{service,timer}` | daily email if any `fetch_runs` failed |
| `systemd/skills-gap-backup.{service,timer}` | nightly encrypted backup |
| `windows/register-worker-tasks.ps1` | the same schedules for a Windows home PC (Task Scheduler) |
| `backup/backup.sh`, `backup/restore-test.sh`, `backup/setup-bucket-lifecycle.sh` | backups to OCI Object Storage |
| `backup/backup-home.ps1` | backups to an encrypted external drive (home PC) |
| `postgres/setup.sql` | database and restricted app role |
| `cloudflare/cloudflared-config.yml` | tunnel ingress to the app |
| `cron.example` | cron alternative to the timers |

## 1. Develop locally first

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
copy .env.example .env        # add ADZUNA_APP_ID / ADZUNA_APP_KEY
.venv\Scripts\python -m src.worker run       # one cycle into data/skills_gap.db (SQLite)
.venv\Scripts\python -m src.worker status
.venv\Scripts\python run.py                  # web app at http://localhost:8501
```

Deploy to the VM only once the worker and the app run cleanly.

## 2. Oracle Cloud VM

1. **[manual]** Create and verify an Oracle Cloud account. Verification can take days.
2. **[manual]** Choose the home region carefully (it can't be changed). Have a
   fallback region in mind; free Ampere capacity is often unavailable in busy regions.
3. **[manual]** Create an Always Free **VM.Standard.A1.Flex** instance (e.g. 2 OCPU / 12 GB),
   Ubuntu 24.04. In the subnet's security list, allow **no** inbound ports except
   SSH from your own IP (or none, using the OCI Bastion service).
4. Keep the VM busy enough to avoid idle reclaim: the hourly worker helps; check
   CPU and network use in the OCI console's metrics each week for the first month.
5. Free-tier storage (boot + block volumes) is shared: keep the total under the
   free allowance (`df -h`, and the OCI console's Block Storage page).

On the VM:

```bash
sudo apt update && sudo apt -y upgrade
sudo apt -y install python3-venv python3-pip postgresql age unattended-upgrades
sudo dpkg-reconfigure -plow unattended-upgrades       # automatic security updates
sudo useradd --system --create-home --home-dir /opt/skills_gap_analyzer skillsgap
sudo -u skillsgap git clone https://github.com/luislimon12/skills_gap_analyzer.git /opt/skills_gap_analyzer
cd /opt/skills_gap_analyzer
sudo -u skillsgap python3 -m venv .venv
sudo -u skillsgap .venv/bin/pip install -r requirements.txt
sudo -u skillsgap mkdir -p data/home data/logs
```

## 3. PostgreSQL and secrets

```bash
sudo -u postgres psql -f deploy/postgres/setup.sql
sudo -u postgres psql -c "\password skills_app"
sudo -u skillsgap cp .env.example .env
sudo chmod 600 .env && sudo chown skillsgap:skillsgap .env
sudo -u skillsgap nano .env   # DATABASE_URL, ADZUNA_*, SECRET_KEY, ENCRYPTION_KEY, alert + backup settings
sudo -u skillsgap .venv/bin/python -m src.db.migrate
```

PostgreSQL listens on localhost only by default (`listen_addresses = 'localhost'`); keep it that way.
Optionally move secrets to OCI Vault later.

The VM's boot and block volumes are encrypted at rest by Oracle by default.
Put the database on a block volume if you want it separate from the boot disk.

## 4. Web app and scheduling

```bash
sudo cp deploy/systemd/*.service deploy/systemd/*.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now skills-gap-web.service
sudo systemctl enable --now skills-gap-worker.timer skills-gap-cleanup.timer \
    skills-gap-report.timer skills-gap-backup.timer
systemctl list-timers 'skills-gap-*'
journalctl -u skills-gap-worker -n 50
```

Only one worker can run at a time: the timer won't start a second copy, and the
worker also holds `data/worker.lock`.

For the first two weeks, check failures daily (the report timer emails you, or run
`.venv/bin/python -m src.worker report --days 1`).

### Windows home PC instead

1. **[manual]** Power settings: never sleep; schedule Windows Update restarts outside the hourly runs; add a UPS if power cuts are common.
2. From an elevated PowerShell in the project folder:
   `powershell -ExecutionPolicy Bypass -File deploy\windows\register-worker-tasks.ps1`
3. **[manual]** Turn on BitLocker for the drive holding the database and uploads.
4. **[manual]** Disable Remote Desktop and unused file sharing; keep Windows Firewall and Defender on.
5. **[manual]** Check your ISP's terms allow hosting a public site on a residential line.

## 5. Cloudflare (edge security)

1. **[manual]** Add the domain to Cloudflare; set SSL/TLS mode to **Full (strict)** and turn on **Always Use HTTPS**.
2. Install `cloudflared`, then:
   ```bash
   cloudflared tunnel login
   cloudflared tunnel create skills-gap
   cloudflared tunnel route dns skills-gap skills.example.org
   sudo cp deploy/cloudflare/cloudflared-config.yml /etc/cloudflared/config.yml   # edit hostname
   sudo cloudflared service install
   ```
   The tunnel makes an outbound connection, so **no router or VM ports are opened**. Disable UPnP on a home router.
3. **[manual]** Security > WAF: enable the Cloudflare managed ruleset and Bot Fight Mode.
4. **[manual]** Security > WAF > Rate limiting rules: e.g. 20 requests / 10 s per IP on
   `/_stcore/upload_file` and `/_stcore/stream` (uploads and analysis run over these).
5. **[manual]** Rules > Transform Rules > Modify Response Header, set on all responses:
   * `Strict-Transport-Security: max-age=31536000; includeSubDomains`
   * `X-Content-Type-Options: nosniff`
   * `Referrer-Policy: strict-origin-when-cross-origin`
   * `X-Frame-Options: DENY`
6. **[manual]** Zero Trust > Access: protect any admin hostname (e.g. a separate
   tunnel route to a status page) with an Access policy limited to your email.
   The public app has no admin or debug routes.
7. **[manual]** Review Security > Events weekly for spikes of blocked or failed requests.

## 6. Backups

1. **[manual]** Create a private OCI Object Storage bucket, then `BACKUP_BUCKET=<name> deploy/backup/setup-bucket-lifecycle.sh` (deletes backups after 30 days).
2. **On your own computer**, create a key pair: `age-keygen -o backup-key.txt`. Put the
   **public** key (`age1...`) in the VM's `.env` as `BACKUP_AGE_RECIPIENT`. Keep
   `backup-key.txt` off the server (password manager or offline).
3. The nightly timer runs `deploy/backup/backup.sh`. Resume tables are excluded, so
   backups never hold resume data.
4. Test a full restore before launch and monthly:
   `AGE_IDENTITY_FILE=backup-key.txt BACKUP_BUCKET=<name> deploy/backup/restore-test.sh`

## 7. Before each release

```bash
.venv/bin/pip install -U -r requirements.txt
.venv/bin/pip-audit -r requirements.txt
.venv/bin/python -m pytest
.venv/bin/python -m src.db.migrate
sudo systemctl restart skills-gap-web
```

Check what the public sees on an error: `.streamlit/config.toml` sets
`showErrorDetails = false`, so visitors get a generic message, never a stack trace.
