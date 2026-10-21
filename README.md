# Job Posting Skills Gap Analyzer

Upload a resume, pick a target role, and see which in-demand skills you're
missing. Works for any job: tech, healthcare, skilled trades, retail,
hospitality, logistics, office, finance, education, and more. The app pulls
live postings from Adzuna (plus optional Greenhouse/Lever company boards),
parses a PDF/DOCX/TXT resume, matches both against a skill taxonomy, and shows
posting-based demand, skill gaps, a fit score, salary context, recurring
requirements the taxonomy doesn't cover, and an O*NET occupation baseline.

**Start here:** [`docs/Skills Gap Analyzer - Complete Guide.md`](<docs/Skills Gap Analyzer - Complete Guide.md>)
(Obsidian note with process diagrams, tree map, setup, scoring, and maintenance).
SVG diagrams are in [`docs/diagrams/`](docs/diagrams/).

See [`docs/sparknotes_main.md`](docs/sparknotes_main.md) for the architecture,
data flow, and scoring formulas, and [`docs/TEAM_HANDOFF.md`](docs/TEAM_HANDOFF.md)
for module contracts.

## Quick start

1. Clone the repo.
2. Create a virtual environment and install dependencies:
   ```bash
   python -m venv venv
   venv\Scripts\activate          # Windows  (macOS/Linux: source venv/bin/activate)
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your Adzuna keys from
   <https://developer.adzuna.com/> (`ADZUNA_APP_ID`, `ADZUNA_APP_KEY`).
4. Start the dashboard:
   ```bash
   streamlit run src/dashboard/app.py
   ```
5. At the top of the page: pick the job you want (type to search, including
   abbreviations like "PSW"; or choose **Other** and type any title), add your
   resume, and click **Get the latest ... postings**. The button only appears
   when Adzuna keys (or Greenhouse/Lever boards) are configured; the sidebar
   holds the operator settings (live-search status, pages per search).

You can also pull postings from the command line:

```bash
python -m src.pipeline.run_pipeline --roles "Data Analyst" --max-pages 2
```

Each Adzuna page is one API call (50 postings). The free tier allows roughly
1,000 calls a month; `max_pages_per_role`, `max_days_old`, `country`, and an
optional `where` location filter live in `src/pipeline/config_postings.yaml`.

## What the dashboard shows

- **Job market sample**: postings in the sample, Adzuna's live listing count,
  the median advertised salary in the sample, and Adzuna's mean salary for the
  search.
- **Matching occupation**: the role is matched automatically to O*NET
  occupations using ~61,000 O*NET job titles (and common abbreviations such as
  PSW, RPN, ECE, AZ driver). You can add or remove occupations in the picker.
- **Posting-based skills**: role fit (cosine similarity), recommended skills to
  learn next ranked by GapScore, the full skill table, and a CSV download.
- **Skill explorer**: a clickable demand chart. Green bars are the skills you
  search with; they start as the skills on your resume. Click a bar to add or
  remove a skill (double-click to undo your clicks), sort by demand, your skills
  or biggest gaps. Below it, postings are ranked by how heavily they lean on the
  picked skills: Match balances the share of a posting's skill list you cover
  with how many of your picked skills it asks for (required mentions count
  double preferred ones).
- **Other recurring requirements**: short phrases that recur across the
  postings but aren't in the taxonomy (e.g. "steel-toe boots", "cross-border
  routes"), flagged when your resume doesn't mention them. Job-ad filler
  ("Job Description", "world-class team"), the posting's own company and city,
  and phrases from a single employer are dropped. Once other roles have been
  fetched, a phrase must also be at least twice as common for this role as for
  the others. Hints only: they don't affect scores.
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
An optional `ambiguous_terms` column marks everyday words that only mean the
skill in context: "framing" counts as Carpentry only when the same text has
another Skilled Trades skill, so "framing business questions" doesn't. O*NET
product aliases that are plain English words ("Meet", "Reports", "Exchange")
are ignored; the full names ("Google Meet") still match.

Resume skills are matched against every section except hobbies/interests and
references, so "wood carving" under Interests is not read as a job skill.
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

Adzuna returns description excerpts, so demand from Adzuna-only samples is a
lower bound. The dashboard says so, and warns when the sample is under 20
postings.

## Tests

```bash
python -m pytest
```

Tests need neither API keys nor network access. They use synthetic Adzuna-,
Greenhouse- and Lever-shaped responses and run them through the real
pipeline code.

## Known limits

- Adzuna descriptions are excerpts (see above). Adding Greenhouse/Lever boards
  in `config_postings.yaml` gives full-text postings for those companies.
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
| 2 — Resume Ingestion | PDF/DOCX/TXT parsing | `src/resume/` |
| 3 — Scoring Engine | Taxonomy matcher, scoring math | `src/scoring/` |
| 4 — Dashboard | Streamlit UI, wiring it together | `src/dashboard/` |

Never commit `.env` files, resume data, generated databases, virtual
environments, or raw/processed API output. The existing `.gitignore` is the
source of truth for those exclusions.

## Data attribution

- Job postings: [Jobs by Adzuna](https://www.adzuna.ca).
- This project includes information from the
  [O*NET 30.0 Database](https://www.onetcenter.org/database.html) by the U.S.
  Department of Labor, Employment and Training Administration (USDOL/ETA).
  Used under the [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/)
  license. O*NET® is a trademark of USDOL/ETA. The curated taxonomy and the
  cleaning of O*NET names into skill terms are modifications by this project
  and are not endorsed by USDOL/ETA.
