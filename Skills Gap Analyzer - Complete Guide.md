---
title: Job Posting Skills Gap Analyzer, Complete Guide
aliases:
  - Skills Gap Analyzer Guide
  - Complete Guide
tags:
  - project/skills-gap-analyzer
  - python
  - streamlit
  - adzuna
  - onet
  - postgresql
created: 2026-10-08
updated: 2026-11-20
status: pre-launch
---

# Job Posting Skills Gap Analyzer: Complete Guide

> [!summary] TL;DR
> Upload a resume, pick a tracked role, and the app shows which skills employers ask for that your resume doesn't show.
> - **Postings:** an hourly **ingestion worker** (`src/worker/`) pulls them from the Adzuna API into a database (SQLite locally, PostgreSQL in production), tracks which ones are still listed, and hides closed ones. The web app only reads that database.
> - **Skills:** ~**934** skills from two hand-curated lists plus **O\*NET 30.0** (free, CC BY 4.0).
> - **Any job:** roles are matched to O\*NET occupations through ~**61,000** job titles (tech, healthcare, trades, retail, logistics, office, and more).
> - **Output:** role fit %, ranked skill gaps with confidence intervals, salary context, recurring requirements the taxonomy misses, and an occupation baseline of tools and technologies.
> - **Privacy:** resumes are stored as extracted skills only and deleted after 30 days (or on "Delete my resume").
> - **Quality:** 90 offline tests, run in GitHub Actions on SQLite and PostgreSQL. No API keys or network needed.

Related notes: [[TEAM_HANDOFF]] · [[PRODUCTION_PLAN_STATUS]] · [[sparknotes_main]] · [[Job Posting Skills Gap Analyzer - SparkNotes]]

---

## 1. Process overview

### 1.1 End-to-end flow

```mermaid
flowchart TD
    U([Student]) -->|picks a tracked role| ROLE[Target role]
    U -->|uploads PDF or DOCX| UPV

    subgraph ING["1 · Ingestion worker, hourly (src/worker, src/sources)"]
        direction TB
        CFG["config/ingestion.yaml<br/>search terms, countries, quota"]
        SRC["JobSource adapters<br/>Adzuna (live), LoopCV (stub, off)"]
        BUD["budget.py<br/>daily, weekly, monthly caps"]
        CYC["cycle.py<br/>incremental updates, backfill,<br/>daily re-scans, retries"]
        LIFE["Lifecycle<br/>active, stale, closed,<br/>daily link check"]
        CFG --> CYC
        SRC --> CYC
        BUD --> CYC
        CYC --> LIFE
    end

    subgraph DBX["2 · Database (src/db: SQLAlchemy + Alembic)"]
        direction TB
        JOBS[("jobs, job_queries,<br/>market_stats")]
        OPS[("fetch_runs, api_requests,<br/>backfill_tasks")]
        PRIV[("resumes, analyses,<br/>resume_deletions")]
    end

    subgraph RES["3 · Resume ingestion (src/resume)"]
        direction TB
        UPV["upload_validation.py<br/>file signature, 5 MB cap,<br/>macro DOCX rejected"]
        PARSE["parse_resume.py<br/>pdfplumber, python-docx, txt"]
        SECT["extract_sections.py<br/>skills, experience, education"]
        DATES["experience_dates.py<br/>date ranges to months"]
        UPV --> PARSE --> SECT --> DATES
    end

    subgraph TAX["Skill taxonomy (src/scoring/taxonomy)"]
        direction TB
        CUR["starter_skills.csv (~255)<br/>general_skills.csv (~240)"]
        ONET["onet_skills.csv (~440)"]
        MATCH["SkillMatcher<br/>aliases, case-sensitive terms,<br/>plurals, token prefilter"]
        CUR --> MATCH
        ONET --> MATCH
    end

    subgraph SCO["4 · Scoring (src/scoring)"]
        direction TB
        DEM["demand_scorer.py<br/>share of postings, Wilson CI,<br/>recency x required/preferred"]
        GAP["priority_scorer.py<br/>GapScore"]
        FIT["fit_scorer.py<br/>cosine FitScore"]
        PHR["phrase_discovery.py<br/>recurring phrases not in taxonomy"]
        OCC["onet_baseline.py<br/>role to O*NET occupations,<br/>technologies and tools"]
        DEM --> GAP
        DEM --> FIT
    end

    subgraph WEB["5 · Dashboard (src/dashboard, Streamlit, reads jobs from the database only)"]
        direction TB
        M1["Job market sample<br/>postings, live count, salary"]
        M2["Posting-based skills<br/>fit, recommendations, chart, CSV"]
        M3["Other recurring requirements"]
        M4["O*NET occupation baseline<br/>software and tools"]
        M5["Related postings<br/>TF-IDF similarity"]
    end

    CYC --> JOBS
    CYC --> OPS
    LIFE --> JOBS
    LIFE -->|"deletes expired resumes"| PRIV
    ROLE --> OCC
    SECT --> MATCH
    DATES --> DEM
    JOBS --> DEM
    JOBS --> PHR
    JOBS --> M1
    JOBS --> M5
    MATCH --> DEM
    MATCH --> PHR
    OCC --> M4
    DEM --> M2
    GAP --> M2
    FIT --> M2
    PHR --> M3
    M2 -.->|"saves skills and scores only"| PRIV
    M2 --> OUT([Ranked skills to learn next])
```

### 1.2 One worker cycle

The worker runs once an hour (`python -m src.worker run`). It never goes over the request quota, because every request is checked against the budget and logged before it is sent.

```mermaid
flowchart TD
    T(["Hourly timer runs<br/>python -m src.worker run"]) --> L{"Another run active?<br/>data/worker.lock"}
    L -->|yes| X([Skip this hour])
    L -->|no| M["Apply Alembic migrations<br/>open a fetch_runs row"]
    M --> K{"API keys set?"}
    K -->|no| F["Run marked failed<br/>alert email"]
    K -->|yes| S1["1. Queue the daily re-scan<br/>for finished slices that are due"]
    S1 --> S2["2. Incremental updates<br/>newest postings, up to 2 pages per slice,<br/>within the hourly allowance"]
    S2 --> S3["3. Backfill and re-scans<br/>newest first, highest priority slice first"]
    S3 --> P1["Check the budget, then log the<br/>request in api_requests before sending"]
    P1 --> P2["Fetch with retries<br/>2s, 4s, 8s backoff"]
    P2 --> P3["Title filter, then upsert into jobs<br/>(source + external_id is unique)"]
    P3 --> P4["Save market_stats on backfill page 1<br/>keep the raw response for 7 days"]
    P4 --> E{"Short page,<br/>page limit,<br/>or budget used up?"}
    E -->|"no, next page"| P1
    E -->|"short page"| C["Slice complete: jobs missed by<br/>two full scans become stale"]
    E -->|"budget used up"| W["Log where it stopped<br/>resume there next run"]
    C --> CL
    W --> CL
    F --> CL["4. Close jobs that stayed stale for<br/>stale_days_before_closed days"]
    CL --> H["Housekeeping<br/>delete expired resumes, prune raw files,<br/>link check (404 or 410 closes), disk check"]
    H --> Z["Finish the fetch_runs row<br/>success, partial or failed"]
```

### 1.3 What happens in one dashboard session

```mermaid
sequenceDiagram
    participant W as Worker (hourly)
    participant A as Adzuna API
    participant DB as Database
    actor S as Student
    participant D as Dashboard
    participant SC as Scoring

    loop every hour, within the request budget
        W->>A: GET /jobs/{country}/search/{page} (newest first)
        A-->>W: results, count, mean salary
        W->>DB: upsert jobs, log fetch_runs and api_requests
        W->>DB: stale and closed updates, daily link check
    end
    S->>D: Pick a role, upload a resume (PDF or DOCX)
    D->>D: Check file type and size, rate limit, parse text
    D->>DB: Save extracted skills only (deleted after 30 days)
    D->>DB: Load listed jobs for the role, latest market stats
    D->>SC: match_occupations(role)
    SC-->>D: O*NET occupations (editable picker)
    D->>SC: analyze_role(resume, postings)
    SC-->>D: fit, demand, Wilson CI, GapScores
    D->>DB: Save the analysis scores (no resume text)
    D->>SC: discover_phrases(...), onet_baseline(...)
    SC-->>D: recurring phrases, tools and technologies
    D-->>S: Recommendations, chart, tables, CSV download
    S->>D: Delete my resume (optional)
    D->>DB: Delete the resume and its analyses
```

### 1.4 Job lifecycle

```mermaid
stateDiagram-v2
    [*] --> active: first seen in a search
    active --> active: seen again, last_seen_at updated
    active --> stale: missed by two full re-scans
    active --> closed: daily link check gets 404 or 410
    stale --> active: seen again
    stale --> closed: still missing after stale_days_before_closed
    closed --> active: listed again (a dead link only if its URL changed)
```

- **Stale** means a job was missed by two full re-scans in a row. A single scan can miss a live job because results shift between pages.
- Stale jobs still appear in results. **Closed** jobs are hidden (the posting browser can show them, labelled "No longer listed").
- The daily link check only looks at active jobs. A job closed by a dead link stays closed while the feed repeats the same URL.

### 1.5 Project tree map

```mermaid
mindmap
  root((Skills Gap Analyzer))
    config
      settings.yaml
        fallback roles
        O*NET overrides
        privacy contact
      ingestion.yaml
        search terms
        quota and backfill
        lifecycle
        resume retention
    src
      sources
        base JobSource and Job
        adzuna
        loopcv stub
        fallback
      worker
        cycle
        budget
        link_check
        lock
        alerts
        raw_store
      db
        schema
        jobs_repo
        runs_repo
        resumes_repo
        migrate Alembic
      resume
        upload_validation
        parse_resume PDF DOCX TXT
        extract_sections
        experience_dates
      scoring
        skill_matcher
        demand_scorer
        priority_scorer GapScore
        fit_scorer FitScore
        analyze
        onet_baseline
        phrase_discovery
        text_similarity TF-IDF
        taxonomy CSVs
      dashboard
        app.py Streamlit
        components
        security
        privacy policy page
      pipeline legacy
        fetch_postings
        normalize
        run_pipeline CLI
      common
        text.py
    deploy
      systemd timers
      Windows tasks
      PostgreSQL setup
      Cloudflare tunnel
      encrypted backups
    migrations
      Alembic versions
    tests
      90 offline tests
    docs
      this guide
```

### 1.6 Deployment

```mermaid
flowchart LR
    U([Students]) --> CF["Cloudflare<br/>HTTPS, WAF, rate limits,<br/>Tunnel + Access"]
    AD["Adzuna API"] --> WK

    subgraph HOST["One host: Oracle Always Free ARM VM (or a Windows home PC)"]
        direction TB
        TM["systemd timers<br/>(Windows Task Scheduler at home)"]
        WK["Worker, hourly<br/>python -m src.worker run"]
        MAINT["Daily resume cleanup,<br/>daily failure report,<br/>nightly backup"]
        WEB["Streamlit app<br/>127.0.0.1:8501"]
        PG[("PostgreSQL<br/>(SQLite for local dev)")]
        TM --> WK
        TM --> MAINT
        WK --> PG
        MAINT --> PG
        WEB --> PG
    end

    CF --> WEB
    MAINT --> BK[("Encrypted backups (age)<br/>OCI Object Storage,<br/>30-day lifecycle")]
    WK -.->|"failure and low-disk alerts"| MAIL["Alert email (SMTP)"]
```

Local development needs none of this: SQLite file, worker run by hand, `python run.py` for the app. See `deploy/README.md` for the full runbook.

> [!tip]- Diagram image files (for slides or viewing outside Obsidian)
> The diagrams above are live Mermaid. The same diagrams are exported as SVG in `docs/diagrams/`:
> - [process-flow.svg](diagrams/process-flow.svg)
> - [worker-cycle.svg](diagrams/worker-cycle.svg)
> - [session-sequence.svg](diagrams/session-sequence.svg)
> - [job-lifecycle.svg](diagrams/job-lifecycle.svg)
> - [project-tree-map.svg](diagrams/project-tree-map.svg)
> - [deployment.svg](diagrams/deployment.svg)
> - [db-schema.svg](diagrams/db-schema.svg)
> - [onet-import.svg](diagrams/onet-import.svg)
>
> ![[process-flow.svg]]

### 1.7 Folder tree

```text
skills_gap_analyzer/
├── .env.example                 # Adzuna keys, DATABASE_URL, secrets, alert + backup settings
├── .github/workflows/tests.yml  # CI: tests on SQLite and PostgreSQL 16
├── README.md
├── requirements.txt
├── pytest.ini
├── alembic.ini
├── run.py, run.bat, run.command, run.sh   # one-click launch of the dashboard
├── config/
│   ├── ingestion.yaml           # worker: search terms, countries, quota, backfill, lifecycle, retention
│   └── settings.yaml            # database path, fallback roles, O*NET overrides, privacy contact
├── data/                        # gitignored: skills_gap.db, raw API responses, O*NET cache, logs
├── deploy/                      # systemd, Windows tasks, PostgreSQL, Cloudflare, backups (see its README)
├── migrations/versions/         # Alembic: 0001 production schema, 0002 request log + scan history
├── docs/
│   ├── Skills Gap Analyzer - Complete Guide.md   # this note
│   ├── PRODUCTION_PLAN_STATUS.md
│   ├── TEAM_HANDOFF.md
│   ├── sparknotes_main.md
│   └── diagrams/                # SVG exports of the diagrams
├── src/
│   ├── common/text.py           # clean_text / html_to_text (shared by postings and resumes)
│   ├── sources/                 # JobSource interface, Job record, Adzuna, LoopCV stub, fallback
│   ├── worker/                  # cycle, budget, link_check, lock, alerts, raw_store, CLI (__main__)
│   ├── db/                      # engine, schema, jobs_repo, runs_repo, resumes_repo, migrate
│   ├── resume/                  # upload_validation, parse_resume, extract_sections, experience_dates
│   ├── scoring/                 # skill_matcher, demand/priority/fit scorers, analyze, onet_baseline,
│   │                            # phrase_discovery, text_similarity, taxonomy/
│   ├── dashboard/               # app.py, components/, security.py, utils.py, pages/1_Privacy_Policy.py
│   └── pipeline/                # LEGACY multi-source pull (Adzuna, Job Bank CSV, Greenhouse, Lever)
└── tests/                       # test_analysis, test_db, test_sources, test_web, test_worker
```

---

## 2. Setup and running

> [!tip] Requirements
> Python 3.10 or newer (CI runs 3.12). Adzuna API credentials are needed for the worker only.

1. Clone the project.
2. Copy `.env.example` to `.env` and add your Adzuna keys. The worker needs them; the dashboard never calls an API:
   ```bash
   ADZUNA_APP_ID=your_id
   ADZUNA_APP_KEY=your_key
   ```
   Get them at https://developer.adzuna.com/.
3. Install and fill the database once (afterwards a scheduler runs the worker every hour, see `deploy/README.md`):
   ```bash
   python -m venv .venv
   .venv\Scripts\activate            # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   python -m src.worker run
   python -m src.worker status
   ```
4. Start the dashboard: double-click `run.bat` (Windows) or `run.command` (macOS), run `./run.sh` (Linux), or use `streamlit run src/dashboard/app.py`.
5. In the sidebar, pick a tracked role and upload a resume (PDF or DOCX). There is no fetch button: postings come from the database the worker fills.

Other commands:

```bash
python -m src.worker report --days 1               # failed or partial runs (exit code 3 if any)
python -m src.worker cleanup                       # delete resumes past their deletion date
python -m src.db.migrate                           # apply Alembic migrations (worker and app also do this on start)
python -m src.scoring.taxonomy.setup_taxonomy      # rebuild O*NET files (optional)
python -m src.pipeline.run_pipeline --roles "Registered Nurse" --max-pages 2   # legacy pull, local SQLite only
python -m pytest                                   # offline test suite
```

> [!warning] API keys
> `.env` is gitignored. **Never commit it.** Adzuna error messages are redacted so keys never appear in the dashboard or logs.

---

## 3. Configuration reference

### `config/ingestion.yaml` (worker)

| Key | Default | Meaning |
|---|---|---|
| `sources.adzuna.countries` | `["ca", "us"]` | Countries searched, highest value first |
| `sources.adzuna.results_per_page` | `50` | Postings per API call |
| `sources.adzuna.title_filter` | `true` | Drop results whose title lacks a word of the search term |
| `sources.adzuna.request_delay_seconds` | `3` | Pause between pages (Adzuna allows about 25 calls a minute) |
| `sources.loopcv.enabled` | `false` | When on, LoopCV is primary and Adzuna is the fallback |
| `queries` | 12 roles | Search terms, highest value first. These are also the roles in the dashboard dropdown |
| `quota.daily_requests` / `weekly_requests` / `monthly_requests` | `250` / `1000` / `2500` | Plan limits. Confirm against your Adzuna plan |
| `quota.safety_margin` | `0.95` | Use at most 95% of each quota |
| `quota.backfill_share` | `0.70` | Share of the daily quota for backfill and re-scans. The rest is for hourly updates and retries |
| `backfill.max_days_old` | `90` | Time window of each slice |
| `backfill.max_pages_per_slice` | `20` | Page depth limit. A slice that hits it never marks jobs stale |
| `backfill.rescan_interval_hours` | `24` | Full re-scan per slice (needed to notice removed jobs) |
| `incremental.interval_hours` / `max_pages` | `1` / `2` | How often finished slices are checked for new postings |
| `lifecycle.stale_days_before_closed` | `3` | Days a job can stay stale before it is closed |
| `link_check.enabled` / `daily_cap` | `true` / `100` | Daily 404/410 check on active jobs |
| `retry.attempts` / `backoff_seconds` | `3` / `2` | Transient errors retry with 2s, 4s, 8s backoff |
| `raw_responses.retention_days` | `7` | Raw API responses kept in `data/raw/api/` |
| `resumes.retention_days` | `30` | Resume deletion date |
| `resumes.store_file` | `false` | `false` keeps extracted skills only. `true` also keeps the file, encrypted with `ENCRYPTION_KEY` |
| `alerts.disk_free_min_percent` | `10` | Low-disk alert threshold |

A **slice** is one country plus one search term. Removing a term from `queries` stops its slice (it becomes inactive).

### `config/settings.yaml`

| Key | Meaning |
|---|---|
| `database_path` | Local SQLite file (default `data/skills_gap.db`). Ignored when `DATABASE_URL` is set |
| `target_roles` | Fallback role list, used only if `queries` is empty |
| `onet_occupations` | **Optional** role to O\*NET-SOC code overrides. Other roles are matched automatically |
| `privacy_contact` | Contact shown on the Privacy policy page. Use a team address |

### `.env`

`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`, `DATABASE_URL` (empty means local SQLite), `SECRET_KEY`, `ENCRYPTION_KEY`, optional `LOOPCV_API_KEY`, SMTP alert settings, and backup settings. `.env.example` lists them all.

### `src/pipeline/config_postings.yaml` (legacy pull only)

Adzuna country, pages, `max_days_old`, `where`, `title_filter`, plus `greenhouse_boards` / `lever_boards` for full-text postings. The web app does not read the table this pull writes.

---

## 4. Data sources

| Source | Used for | Access | License / terms |
|---|---|---|---|
| **Adzuna API** | Live postings, total live count, mean salary, salary ranges | Free key (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`) | Show "Jobs by Adzuna" attribution (done in the footer) |
| LoopCV | Planned primary source | Adapter stub, off until its API is confirmed | n/a |
| **O\*NET 30.0** (Technology Skills, Tools Used, Occupation Data, Alternate Titles, Reported Titles) | Technologies, tools, ~61k job titles for all 1,016 occupations | Free download | **CC BY 4.0**, attribution in README and dashboard footer |
| Curated CSVs | Authoritative skills, aliases, Canadian licences and certs | In repo | Project-owned |
| Canada Job Bank, Greenhouse, Lever | Legacy pull only | No key | Job Bank CSV has no full descriptions |
| ~~Lightcast Open Skills~~ | *Replaced* | Needed an approved account | n/a |

> [!quote] Required O\*NET attribution
> This project includes information from the O\*NET 30.0 Database by the U.S. Department of Labor, Employment and Training Administration (USDOL/ETA). Used under the CC BY 4.0 license. O\*NET® is a trademark of USDOL/ETA. Modifications (curated taxonomy, name cleaning) are by this project and not endorsed by USDOL/ETA.

---

## 5. Skill taxonomy

### 5.1 Composition (934 skills, 124 categories)

```mermaid
pie showData
    title Skills by source
    "Curated tech & professional (starter_skills.csv)" : 255
    "Curated non-tech & certifications (general_skills.csv)" : 238
    "O*NET technologies (onet_skills.csv)" : 441
```

| File | Edited | Contents |
|---|---|---|
| `starter_skills.csv` | by hand | Programming, data, cloud, BI, ML, DevOps, web, business systems, professional skills |
| `general_skills.csv` | by hand | Customer service and retail, food and hospitality, healthcare, skilled trades, health and safety, transportation and logistics, administrative, accounting and finance, sales and marketing, HR, education and childcare, legal, community and social services, security, cleaning and facilities, manufacturing and engineering, science and lab, personal services, agriculture, languages, background checks |
| `onet_skills.csv` | generated | O\*NET technologies that are "Hot Technology" or used by 5+ occupations, not already curated |
| `onet_mapping.csv.gz` | generated | Every occupation to its technologies (`kind=technology`) and tools (`kind=tool`) |
| `onet_titles.csv.gz` | generated | ~61k titles to occupation (occupation, alternate, reported) |

### 5.2 CSV format

```csv
skill_name,category,aliases,case_sensitive_terms
Microsoft Excel,Data Analysis,MS Excel|advanced Excel,Excel
Forklift Operation,Transportation & Logistics,forklift|reach truck|lift truck,
R,Programming Languages,R programming|RStudio,R
```

### 5.3 Matching rules

- **Case-insensitive, word-boundary** matching on name and aliases (`Java` does not match `JavaScript`).
- **Plural-tolerant**: `reach trucks` matches `reach truck`.
- **`case_sensitive_terms`** for short or ambiguous words, with strict edges:
  - `R` doesn't match "R&D"; `C` doesn't match "C-suite", "C++" or "C#"
  - `Excel` doesn't match "you will excel"; `Spark`, `Swift`, `Workday`, `Bootstrap` likewise
  - short O\*NET acronyms (`MRP`, `SSO`, `FEA`) only in capitals
- **Curated wins**: an O\*NET term already used by a curated skill is merged into it, never duplicated.
- **Boilerplate-safe**: "competitive compensation", "health insurance", "flexible scheduling" are not skills. Avoid such aliases.
- Curly quotes are normalized.
- **Speed**: the taxonomy is compiled once per process and patterns are prefiltered by first token.
- The matcher is regex-based. spaCy is listed in `requirements.txt` but the code does not use it.

### 5.4 How O\*NET is imported

```mermaid
flowchart LR
    DL[Download O*NET 30.0 text files<br/>cached in data/cache/onet] --> CLEAN[Clean names<br/>'Amazon Web Services AWS software' → AWS<br/>'Apache Kafka' → alias 'Kafka'<br/>drop categories like 'Tax software']
    CLEAN --> SEL{Hot Technology<br/>or ≥5 occupations?}
    SEL -->|yes, not curated| SK[onet_skills.csv]
    SEL -->|already curated| MERGE[mapped to curated skill]
    CLEAN --> MAP[onet_mapping.csv.gz<br/>technologies + tools per occupation]
    TITLES[Alternate + reported titles] --> TI[onet_titles.csv.gz]
```

---

## 6. Scoring

For each skill $s$ found in a sample of $N$ postings:

**Demand**: share of postings mentioning the skill, with a 95% **Wilson** interval:

$$\text{demand}(s) = \frac{x_s}{N}, \qquad
\text{CI} = \frac{\hat p + \frac{z^2}{2N} \pm z\sqrt{\frac{\hat p(1-\hat p)}{N} + \frac{z^2}{4N^2}}}{1 + \frac{z^2}{N}},\; z = 1.96$$

**Weighted demand**: recent and required mentions count more:

$$\text{wd}(s) = \frac{\sum_{p \ni s} r_p \cdot q_{p,s}}{\sum_{p} r_p}$$

| Weight | Value |
|---|---|
| Recency $r_p$: < 30 days / 30 to 90 days / > 90 days / unknown | 1.0 / 0.7 / 0.4 / 0.7 |
| Requirement $q_{p,s}$: required / preferred ("nice to have", "asset", "bonus") | 1.0 / 0.5 |

**GapScore** (what to learn next):

$$\text{GapScore}(s) = \text{wd}(s) \times \big(1 - \text{has}(s)\big)$$

**FitScore**: cosine similarity between the resume vector (0/1 per skill) and the weighted-demand vector, times 100.

> [!info] Evidence, not score
> Months of dated experience per skill are shown in the table but don't change GapScore. The O\*NET baseline, recurring phrases and TF-IDF similarity are also shown separately and **never** change GapScore or FitScore.

---

## 7. Dashboard tour

| Section | What it shows |
|---|---|
| **Sidebar** | Role selector (the roles the worker collects), resume upload with a "how your data is used" note, "Delete my resume" and the deletion date |
| **Country and Province / state** | Filters on postings already in the database. They do not fetch anything |
| **Job market sample** | Postings in sample, Adzuna live listings, median advertised salary (per country when several are mixed), Adzuna mean salary. A posting browser with links, plus a toggle to include postings no longer listed. Warnings for fewer than 20 postings and for Adzuna excerpts |
| **Matching occupation** | Auto-picked O\*NET occupations (editable, searchable across all 1,016) |
| **Posting-based skills** | Role fit, in-demand skills you have, gaps, top-5 recommendations, demand chart with Wilson intervals, "Find postings by skills" (select chart bars, avoid skills), full table, CSV download |
| **Other recurring requirements** | Frequent phrases not in the taxonomy (e.g. "steel-toe boots") and which your resume lacks |
| **O\*NET occupation baseline** | Software and technology (Hot / In-Demand flags) and tools and equipment, with resume coverage |
| **Related postings** | Postings most similar to your resume (TF-IDF) plus keyword hits |
| **Privacy policy page** | Retention and deletion explained (`src/dashboard/pages/1_Privacy_Policy.py`) |

It also works **without a resume**: it shows what the role asks for. There is a dark mode toggle.

**Safeguards in the web app:** uploads are identified by file signature (PDF or DOCX only), capped at 5 MB, and macro DOCX files are rejected. Uploads are limited to 5 an hour and new analyses to 30 an hour, per IP and per anonymous session. Errors shown to visitors never include connection strings or stack traces.

---

## 8. Database schema

```mermaid
erDiagram
    JOBS {
        int id PK
        text source "adzuna"
        text external_id "unique with source"
        text title
        text company
        text location
        text country_code
        text region
        real salary_min
        real salary_max
        text description "excerpt for Adzuna"
        text url
        date posted_date
        text status "active, stale or closed"
        datetime first_seen_at
        datetime last_seen_at
        datetime stale_since
        datetime closed_at
        text closed_reason "stale or link_dead"
    }
    JOB_QUERIES {
        int job_id PK
        text country_code PK
        text query PK "search term, drives role filtering"
        datetime last_seen_at
    }
    BACKFILL_TASKS {
        int id PK
        text source
        text country
        text query
        int page "next page to fetch"
        text mode "backfill, rescan or incremental"
        text status "pending, complete or inactive"
        datetime last_full_scan_at
        datetime previous_scan_started_at
    }
    FETCH_RUNS {
        int id PK
        datetime started_at
        text source
        int jobs_found
        int jobs_inserted
        int jobs_marked_stale
        int jobs_closed
        int requests_used
        text status "running, success, partial or failed"
        text error_message
    }
    API_REQUESTS {
        int id PK
        text source
        text kind "backfill or incremental"
        datetime requested_at
        int run_id
    }
    MARKET_STATS {
        int id PK
        text query
        text country
        int total_count "Adzuna live matches"
        real mean_salary
        datetime fetched_at
    }
    RESUMES {
        text id PK "random UUID"
        text session_id "anonymous"
        text skills "JSON list, no resume text"
        text file_path "only if store_file is on"
        datetime uploaded_at
        datetime delete_after "upload + 30 days"
    }
    ANALYSES {
        int id PK
        text resume_id FK
        text role
        real fit_score
        int postings_count
        text results "JSON scores"
    }
    RESUME_DELETIONS {
        int id PK
        text resume_id "random ID only"
        text reason "expired or user_request"
        datetime deleted_at
    }
    JOBS ||--o{ JOB_QUERIES : "found by"
    RESUMES ||--o{ ANALYSES : "deleted together"
    FETCH_RUNS ||..o{ API_REQUESTS : "run_id"
    BACKFILL_TASKS ||..o{ JOB_QUERIES : "same country and query"
    MARKET_STATS }o..o{ JOB_QUERIES : "same country and query"
```

- Jobs are public data and can always be rebuilt from the source. Resume tables are personal data, deleted after `delete_after` and excluded from backups.
- `job_queries` records which search found each job. The app filters by role through it, and the worker uses it to decide what a re-scan did not see.
- `api_requests` is written **before** each request is sent. The budget counts it by request time, so a run that crosses midnight charges each request to the right day.
- `fetch_runs` is the ingestion audit log. `resume_deletions` holds only the random resume ID, never content.
- Schema changes go through Alembic (`alembic revision -m "..."`, then `python -m src.db.migrate`). `tests/test_db.py` checks that `schema.py` and the migrations match.
- The legacy pipeline's `postings` and `market_snapshots` tables live in the local SQLite file only and are not part of this schema.

---

## 9. Module contracts (for developers)

| Function | Returns |
|---|---|
| `JobSource.fetch(criteria)` (`src/sources/base.py`) | `FetchResult` with normalised `Job` objects, `total_available`, `mean_salary`, `requests_used`. Raises `SourceError` (`permanent=True` when a retry won't help) |
| `src.sources.build_source(config)` | Adzuna alone, or LoopCV with Adzuna as fallback when `loopcv.enabled` |
| `src.worker.cycle.run_cycle(engine, config)` | `CycleSummary` (status, counts, requests, where backfill stopped) |
| `src.worker.cycle.run_housekeeping(engine, config)` | Result of resume cleanup, raw-file pruning, link check, disk check |
| `src.db.jobs_repo.load_jobs_for_role(conn, role, include_closed=False)` | Posting dicts the scoring code reads |
| `src.db.jobs_repo.latest_market_stats(conn, query)` | Latest Adzuna count and mean salary per country |
| `src.dashboard.utils.load_role_postings(role, database, include_closed=False)` | Same, for the dashboard |
| `src.resume.parse_resume.parse_resume(file, filename=None)` | Clean text, or `ResumeParseError` (safe to show) |
| `src.resume.upload_validation.validate_upload(...)` | Raises `UploadRejected` (safe to show) |
| `src.scoring.skill_matcher.SkillMatcher().match(text)` / `.mask(text)` | Matched `Skill`s / text with known terms removed |
| `src.scoring.analyze.analyze_role(resume_text, postings)` | `{postings_count, resume_skills, fit_score, skills, gaps, ...}` |
| `src.scoring.onet_baseline.match_occupations(role)` | `[{soc, occupation, matched_title, score}]` |
| `src.scoring.onet_baseline.onet_baseline(codes, resume_skills, resume_text=, kind=)` | Technologies or tools with `has_skill`, `in_demand`, `occupations` |
| `src.scoring.phrase_discovery.discover_phrases(postings, matcher, resume_text, role)` | `[{phrase, count, share, in_resume}]` |

---

## 10. Tests

```mermaid
pie showData
    title 90 tests by file
    "test_worker: cycle, budget, backfill, retries, link check, lock, alerts, CLI" : 30
    "test_db: schema, migrations, job lifecycle, resume retention, encryption" : 20
    "test_web: dashboard, uploads, rate limits, privacy page" : 17
    "test_sources: Adzuna adapter, fallback rule" : 16
    "test_analysis: resume parsing, matcher, scoring, end to end" : 7
```

Nothing calls a real API: `tests/conftest.py` blocks HTTP and removes API keys, and the Adzuna adapter is tested against a recorded response.

```bash
python -m pytest                                                                              # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/skills_gap_test python -m pytest   # PostgreSQL
```

GitHub Actions runs both on every push.

---

## 11. Maintenance how-tos

> [!example]- Add or fix a skill
> 1. Edit `src/scoring/taxonomy/starter_skills.csv` (tech) or `general_skills.csv` (everything else).
> 2. Add aliases with `|`. Put short or ambiguous terms in `case_sensitive_terms`.
> 3. Avoid aliases that appear in benefits text ("compensation", "insurance", "scheduling").
> 4. Run `python -m pytest`.

> [!example]- Track a new role
> Add the term to `queries` in `config/ingestion.yaml` (earlier means higher priority). The worker creates its slices on the next run and backfills them within the daily budget. Use the wording employers use ("AZ Driver", not "AZ Truck Driver").

> [!example]- Check on the worker
> `python -m src.worker status` shows jobs by status, today's request use against the quota, unfinished slices, and the last 10 runs. `python -m src.worker report --days 1` lists failures.

> [!example]- Pin occupations for a role
> Add under `onet_occupations` in `config/settings.yaml`:
> ```yaml
> "Medical Office Assistant": ["43-6013.00"]
> ```
> Codes: https://www.onetonline.org/

> [!example]- Refresh O\*NET data (new release)
> ```bash
> python -m src.scoring.taxonomy.setup_taxonomy --version 30.1
> ```
> Then update the version in the attribution text (README and dashboard footer).

> [!example]- Turn on LoopCV
> Set `LOOPCV_API_KEY` in `.env` and `sources.loopcv.enabled: true`. The adapter is still a stub, so this waits on LoopCV confirming its API.

---

## 12. Known limits

> [!warning] Interpret results as estimates
> - **Adzuna excerpts**: descriptions are about 500 characters, so demand from Adzuna-only samples is a **lower bound**.
> - **Small samples**: fewer than about 20 postings means wide intervals (the dashboard warns).
> - **Title filter**: use employer wording, or set `title_filter: false` for broad roles.
> - **O\*NET is U.S. data**: occupations and tools transfer well. Provincial licences come from the curated list.
> - **Broad titles** ("Associate", "Coordinator") can map to several occupations. Adjust in the picker.
> - **Required vs preferred** is inferred from headings and cue words.
> - **Two-column PDFs** may parse out of order.
> - **Quotas** in `ingestion.yaml` are marked CONFIRM until checked against the real Adzuna plan.
> - Not career advice.

---

## 13. Troubleshooting

| Symptom | Fix |
|---|---|
| Dashboard says no postings have been collected | Run `python -m src.worker run`, then `status` |
| Worker run "failed", "keys are not set" | Create `.env` from `.env.example` in the project root |
| "Adzuna rejected the API keys (HTTP 401)" | Check the ID and key at developer.adzuna.com |
| `status` shows slices not complete | Normal early on. Backfill uses at most 70% of the daily quota and resumes where it stopped |
| 0 postings for a role | Wording too specific for the title filter. Try employer wording or `title_filter: false` |
| "Too many analyses/uploads" | Per-visitor rate limit. Wait and retry |
| Resume "No readable text found" | Scanned PDF. Export from Word or Google Docs as PDF or DOCX |
| A skill is missed or wrongly matched | Edit the curated CSV aliases (see section 11) |
| Wrong occupation in baseline | Change it in the **Matching occupation** picker, or pin it in `settings.yaml` |

---

## 14. Change log

| Phase | Changes |
|---|---|
| **Original prototype** | Adzuna/Greenhouse/Lever pipeline, PDF/DOCX parsing, 43-skill starter taxonomy, demand, GapScore, FitScore, Streamlit dashboard, 50 tests. Blocked on Lightcast |
| **Completion** | Lightcast replaced by curated (~255) plus O\*NET taxonomy, case-sensitive terms, O\*NET baseline, Adzuna salary and market snapshots, API-key redaction, custom roles, demand chart, CSV export, 83 tests |
| **All jobs** | `general_skills.csv` (~240 non-tech and Canadian certs), O\*NET for all 1,016 occupations, tools baseline, automatic role to occupation matching (~61k titles), recurring-phrase discovery, plural tolerance, fast token-indexed matcher, 145 tests |
| **Production (v2)** | Hourly ingestion worker with request budget, backfill, retries, stale/closed lifecycle and link check. `JobSource` adapters (Adzuna live, LoopCV stub, fallback rule). SQLAlchemy + Alembic database (SQLite or PostgreSQL). Dashboard reads the database only (fetch button removed). Resume privacy: file checks, rate limits, skills-only storage, 30-day deletion, privacy page. Deployment runbook, encrypted backups, CI on SQLite and PostgreSQL. Old pipeline tests replaced by 90 new ones. Legacy pipeline kept for experiments |
