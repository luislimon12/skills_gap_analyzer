# Job Posting Skills Gap Analyzer

Team scaffold for a resume and job-posting skills-gap analyzer. The project is
intentionally non-runnable at this stage: each team member can build inside a
separate ownership folder without waiting for the full pipeline to exist.

See [`docs/sparknotes_main.md`](docs/sparknotes_main.md) for the architecture,
planned data flow, scoring formulas, and timeline. See
[`docs/TEAM_HANDOFF.md`](docs/TEAM_HANDOFF.md) for the current boundaries and
handoff expectations.

## Current status

This repository contains the Week 1 file structure and dependency list only.
Several modules deliberately contain placeholders or `NotImplementedError`.
The Streamlit entry point is a wiring placeholder, and the test suite contains
only a smoke placeholder until each role adds its own tests.

Do not treat the current scaffold as a finished application or as a stable API.

## Planned setup

1. Clone the repo
2. `python -m venv venv && source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in your API keys (see below)
5. Run the dashboard after the dashboard and integration work is complete:
	`streamlit run src/dashboard/app.py`

## API keys — where they go

Put your real keys in a file called `.env` in the project root (this file is gitignored, never commit it).
Use `.env.example` as the template — it shows which variables are needed but keeps the values blank.

```
ADZUNA_APP_ID=your_actual_id_here
ADZUNA_APP_KEY=your_actual_key_here
DATABASE_URL=sqlite:///data/skills_gap.db
```

Get Adzuna keys free at https://developer.adzuna.com/ (register, no cost).
Greenhouse and Lever APIs don't need keys.

## Team ownership

| Role | Owns | Folder |
|---|---|---|
| 1 — Data Pipeline | Postings fetch, dedup, storage | `src/pipeline/` |
| 2 — Resume Ingestion | PDF/DOCX parsing | `src/resume/` |
| 3 — Scoring Engine | Taxonomy matcher, scoring math | `src/scoring/` |
| 4 — Dashboard | Streamlit UI, wiring it together | `src/dashboard/` |

This directory is the canonical project root for the team handoff.

Never commit `.env` files, resume data, generated databases, virtual
environments, or raw/processed API output. The existing `.gitignore` is the
source of truth for those exclusions.
