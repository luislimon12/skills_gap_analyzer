# Job Posting Skills Gap Analyzer

Upload a resume, pick a target role, and see which in-demand skills you're
missing. Works for any job: tech, healthcare, skilled trades, retail,
hospitality, logistics, office, finance, education, and more.

A scheduled **ingestion worker** collects postings from Adzuna every hour into
a database (SQLite locally, PostgreSQL in production), tracks which postings
are still listed, and hides closed ones. The **web app** only reads that
database: it parses a PDF or DOCX resume, matches it against a skill taxonomy,
and shows posting-based demand, skill gaps, a fit score, salary context,
recurring requirements the taxonomy doesn't cover, and an O*NET occupation
baseline. Resume data is stored as extracted skills only and deleted after 30 days.

**Start here:** [`docs/Skills Gap Analyzer - Complete Guide.md`](<docs/Skills Gap Analyzer - Complete Guide.md>)
(Obsidian note with process diagrams, tree map, setup, scoring, and maintenance).
SVG diagrams are in [`docs/diagrams/`](docs/diagrams/).
Production setup: [`deploy/README.md`](deploy/README.md). Plan progress:
[`docs/PRODUCTION_PLAN_STATUS.md`](docs/PRODUCTION_PLAN_STATUS.md).

See [`docs/sparknotes_main.md`](docs/sparknotes_main.md) for the architecture,
data flow, and scoring formulas, and [`docs/TEAM_HANDOFF.md`](docs/TEAM_HANDOFF.md)
for module contracts.

## Quick start

1. Clone or download the repo.
2. Copy `.env.example` to `.env` and add your Adzuna keys from
   <https://developer.adzuna.com/> (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`).
3. Fill the database once (later the scheduler does this every hour):
   ```bash
   python -m venv .venv
   # Windows: .venv\Scripts\activate
   # macOS/Linux: source .venv/bin/activate
   pip install -r requirements.txt
   python -m src.worker run
   python -m src.worker status
   ```
4. Launch the app with one click: `run.bat` (Windows), `run.command` (macOS),
   or `./run.sh` (Linux), or run `streamlit run src/dashboard/app.py`.

In the sidebar, pick a role and upload your resume. There is no "fetch" button:
postings come from the database the worker fills.

## Ingestion worker

```bash
python -m src.worker run        # one cycle; schedule it hourly (deploy/README.md)
python -m src.worker status     # jobs, backfill progress, request use
python -m src.worker report --days 1   # failed runs
python -m src.worker cleanup    # delete resumes past their deletion date
```

Each cycle loads pending work, queries the source, normalises and upserts the
results, and writes a `fetch_runs` row (the ingestion audit log). Settings are
in [`config/ingestion.yaml`](config/ingestion.yaml):

- **Slices**: one per country and search term, highest value first. Each is
  backfilled newest-first, page by page, and marked complete when a page comes
  back short. Completed slices switch to incremental mode (new postings only).
- **Request budget**: the worker stops before the daily, weekly or monthly
  quota is used up. Backfill gets about 70% of the daily quota; the rest is for
  hourly updates and retries. Each run logs where backfill stopped.
- **Lifecycle**: every fetch updates `last_seen_at`. After each daily full
  re-scan, unseen jobs become `stale`; after `stale_days_before_closed` days
  they become `closed`. A capped daily link check closes postings whose URL
  returns 404/410. Closed jobs that reappear are reactivated and logged.
- **Retries**: transient errors are retried with backoff; rejected keys fail the
  run cleanly and send an alert email (if SMTP is configured).
- **Sources**: every board implements `JobSource.fetch(criteria)` and returns
  normalised `Job` objects (`src/sources/`). Adzuna is live; LoopCV is a stub
  until its API is confirmed. With `loopcv.enabled`, LoopCV is primary and
  Adzuna is the fallback when LoopCV fails, times out, or returns nothing.
- Raw API responses are kept 7 days in `data/raw/api/` for debugging.

Schema changes go through Alembic (`alembic revision -m "..."`, then
`python -m src.db.migrate`). The worker and the app migrate on start.

### Legacy multi-source pull

`python -m src.pipeline.run_pipeline` still pulls Adzuna, the monthly Canada
Job Bank CSV, and Greenhouse/Lever boards into the older `postings` table for
experiments. The web app no longer reads that table.

Use the **Country** and **Province / state** selectors above the results to
filter the stored posting sample before analysis. These filters do not fetch
extra pages; they filter postings already in the database.

## What the dashboard shows

- **Job market sample**: postings in the sample, Adzuna's live listing count,
  the median advertised salary in the sample, and Adzuna's mean salary for the
  search.
- **Matching occupation**: the role is matched automatically to O*NET
  occupations using ~61,000 O*NET job titles (and common abbreviations such as
  PSW, RPN, ECE, AZ driver). You can add or remove occupations in the picker.
- **Posting-based skills**: role fit (cosine similarity), recommended skills to
  learn next ranked by GapScore, a demand chart with 95% Wilson intervals, the
  full skill table, and a CSV download.
- **Other recurring requirements**: short phrases that recur across the
  postings but aren't in the taxonomy (e.g. "steel-toe boots", "cross-border
  routes"), flagged when your resume doesn't mention them. Hints only: they
  don't affect scores.
- **O*NET occupation baseline**: software/technology and tools/equipment
  O*NET lists for the selected occupations, and which of them your resume
  mentions. Reference only: it does not change scores.
- **Related postings**: experimental TF-IDF similarity and keyword hits for
  browsing descriptions.

## Skill taxonomy (Lightcast replaced)

Lightcast Open Skills needs an approved account, so the taxonomy comes from
free sources:

| File | What it is |
|---|---|
| `src/scoring/taxonomy/starter_skills.csv` | Curated tech, data and professional skills (~255). Edited by hand. |
| `src/scoring/taxonomy/general_skills.csv` | Curated non-tech skills (~240): healthcare, trades, health & safety, retail, food service, logistics, admin, accounting, sales, HR, education, legal, social services, security, manufacturing, languages, and Canadian licences/certifications (G/AZ licence, Red Seal, WHMIS, Smart Serve, CPR, PSW, ECE, CPA...). Edited by hand. |
| `src/scoring/taxonomy/onet_skills.csv` | ~440 O*NET technologies across all occupations ("Hot Technology" or used by 5+ occupations) not covered by the curated lists. Generated. |
| `src/scoring/taxonomy/onet_mapping.csv.gz` | Every O*NET occupation → its technologies and tools, for the occupation baseline. Generated. |
| `src/scoring/taxonomy/onet_titles.csv.gz` | ~61,000 O*NET job titles → occupation, for automatic role matching. Generated. |

The curated files are authoritative: an O*NET term already claimed by a
curated skill is merged into it. In total the matcher knows ~930 skills.

Regenerate the O*NET files (downloads once, cached in `data/cache/onet/`):

```bash
python -m src.scoring.taxonomy.setup_taxonomy
```

To add or fix a skill, edit `starter_skills.csv` (tech) or
`general_skills.csv` (everything else). Columns: `skill_name`,
`category`, `aliases` (pipe-delimited) and `case_sensitive_terms` for short or
ambiguous terms that must match exact casing with strict boundaries. For
example, `R` doesn't match "R&D", and `Excel` doesn't match "you will excel".
Other terms tolerate a plural "s"/"es" ("reach trucks" matches "reach truck").
Categories group related skills for display without merging their matches or scores;
for example, Artificial Intelligence and Machine Learning remain separate skills
under `AI & Machine Learning`.
Avoid aliases that appear in benefits boilerplate ("compensation", "health
insurance", "flexible scheduling"); `tests/test_taxonomy.py` checks for these.

Roles are matched to O*NET occupations automatically. To pin specific
occupations for a role, add it under `onet_occupations` in
`config/settings.yaml` (codes from <https://www.onetonline.org/>).

## Scoring

`analyze_role(resume_text, postings)` returns:

- matched resume skills
- per-skill posting demand (`x/N`) with unweighted 95% Wilson intervals
- weighted demand: recency (1.0 / 0.7 / 0.4) × required/preferred mention (1.0 / 0.5); unknown dates use 0.7
- `GapScore = weighted_demand × (1 − has_skill)`
- a 0–100 cosine FitScore
- dated experience months per skill, reported as evidence only (it does not change GapScore)

Adzuna returns description excerpts, and the Canada Job Bank open-data CSV
does not include full job descriptions, so skills demand from these sources
may be lower than demand in complete posting text. The dashboard notes the
source limitation and warns when the sample is under 20 postings.

## Tests

```bash
python -m pytest                                   # SQLite
TEST_DATABASE_URL=postgresql+psycopg://user:pass@localhost/skills_gap_test python -m pytest   # PostgreSQL
```

The suite (about 75 tests in `tests/`) never calls a real API: `tests/conftest.py`
blocks HTTP and removes API keys, and the Adzuna adapter is tested against a
recorded response. It covers the adapters and fallback rule, deduplication,
the stale/closed/reactivated lifecycle, the request budget and backfill, retries,
the single-instance lock, 30-day resume deletion, upload checks, rate limits,
the rendered web pages, and a full worker run against a copy of the database.

GitHub Actions (`.github/workflows/tests.yml`) runs it on every push twice:
once on SQLite and once on PostgreSQL 16, matching the Oracle VM setup.

## Known limits

- Adzuna descriptions are excerpts (see above). Adding Greenhouse/Lever boards
  in `config_postings.yaml` gives full-text postings for those companies.
- The Canada Job Bank dataset is monthly, not real-time, and only covers jobs
  advertised on Job Bank or shared through eligible external contributors; it
  is not a complete feed of every Canadian vacancy. The CSV omits employer
  names and full descriptions, so cross-source deduplication against it is
  intentionally conservative. Adzuna remains a separate live Canada sample.
- Deduplication first uses a source's stable posting ID or canonical URL.
  Cross-source fallback matching requires the same normalized title,
  employer, country/region/location, and nearby posting dates; separate
  source IDs from the same provider are kept as distinct vacancies.
- Required/preferred status is inferred from headings and cue words.
- O*NET is U.S. data. Its occupations and tools fit Canadian jobs well, but
  provincial licences and certifications come from the curated list, so add
  any that are missing for your region.
- `title_filter` keeps postings whose title contains every word of the role.
  Use the wording employers use ("AZ Driver", not "AZ Truck Driver"), or set
  `title_filter: false` in `config_postings.yaml` for broad roles.
- Two-column PDF resumes can come out interleaved.
- Results are estimates from a posting sample, not career advice.

## API keys — where they go

Put your real keys in a file called `.env` in the project root (this file is gitignored, never commit it).
Use `.env.example` as the template. It lists the variables you need, with the values left blank.
Error messages from Adzuna requests are redacted so the keys never show up in
the dashboard or logs.

DO NOT COMMIT THE KEYS FOR THE LOVE OF GOD

## Team ownership

| Role | Owns | Folder |
|---|---|---|
| 1 — Data Pipeline | Postings fetch, dedup, storage | `src/pipeline/` |
| 2 — Resume Ingestion | PDF/DOCX parsing, upload validation | `src/resume/` |
| 3 — Scoring Engine | Taxonomy matcher, scoring math | `src/scoring/` |
| 4 — Dashboard | Streamlit UI, wiring it together | `src/dashboard/` |

Never commit `.env` files, resume data, generated databases, virtual
environments, or raw/processed API output. The existing `.gitignore` is the
source of truth for those exclusions.

## Data attribution

- Job postings: [Jobs by Adzuna](https://www.adzuna.ca), [Canada Job Bank
  Open Government dataset](https://open.canada.ca/data/en/dataset/ea639e28-c0fc-48bf-b5dd-b8899bd43072),
  (legacy pull only).
- This project includes information from the
  [O*NET 30.0 Database](https://www.onetcenter.org/database.html) by the U.S.
  Department of Labor, Employment and Training Administration (USDOL/ETA).
  Used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  license. O*NET® is a trademark of USDOL/ETA. The curated taxonomy and the
  cleaning of O*NET names into skill terms are modifications by this project
  and are not endorsed by USDOL/ETA.
