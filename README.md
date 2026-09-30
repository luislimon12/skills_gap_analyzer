# Job Posting Skills Gap Analyzer

Prototype for analyzing a resume against job postings for a target role. It can
fetch configured postings, parse PDF/DOCX resumes, match skills against a
curated starter taxonomy, and display sample-based demand, gaps, fit, and related
posting text. Results are illustrative until the taxonomy and live-data workflow
are validated more broadly.

See [`docs/sparknotes_main.md`](docs/sparknotes_main.md) for the architecture,
planned data flow, scoring formulas, and timeline. See
[`docs/TEAM_HANDOFF.md`](docs/TEAM_HANDOFF.md) for the current boundaries and
handoff expectations.

## Current status

- **Role 1 (pipeline)** — implemented: Adzuna/Greenhouse/Lever fetch, normalization
  to one posting shape (`src/pipeline/normalize.py`), cross-source dedup, SQLite
  storage. Run it with `python -m src.pipeline.run_pipeline` (Greenhouse/Lever
  boards go in `src/pipeline/config_postings.yaml`; Adzuna needs keys in `.env`).
- **Role 2 (resume)** — implemented: `parse_resume()` for PDF/DOCX paths or uploads,
  section splitting, and experience entries with date ranges.
- **Role 3 (scoring)** — implemented prototype: case-insensitive taxonomy matching,
  posting demand and Wilson intervals, recency/preferred weighting, GapScore,
  cosine FitScore, and dated experience evidence. The checked-in taxonomy is a
  curated starter set, not the full Lightcast taxonomy.
- **Role 4 (dashboard)** — integrated resume upload, role selection, postings
  fetch/view, and score display/recommendations.

Run all tests from the project root with `python -m pytest`. The offline scoring
examples can be tested specifically with `python -m pytest tests/test_scoring.py`.

## Planned setup

1. Clone the repo
2. `python -m venv venv && source venv/bin/activate` (or `venv\Scripts\activate` on Windows)
3. `pip install -r requirements.txt`
4. Copy `.env.example` to `.env` and fill in your API keys (see below)
5. Start the dashboard with `streamlit run src/dashboard/app.py`. Configure
	Adzuna credentials and/or company boards before fetching postings.

## Scoring prototype

`src/scoring/taxonomy/starter_skills.csv` is a small, curated starter taxonomy
with aliases; it is included so matching and scoring can be tested without
credentials or network access. `analyze_role(resume_text, postings)` returns the
matched resume skills, per-skill posting demand and 95% Wilson intervals,
weighted demand, GapScores, dated experience evidence, and a 0–100 cosine fit
score. Unknown posting dates use a neutral 0.7 recency weight. GapScore follows
the project formula exactly (`weighted_demand * (1 - has_skill)`); experience
months are evidence only and do not change the score.

The dashboard also shows an experimental TF-IDF text-similarity ranking and
exact keyword hits for browsing related descriptions. This is separate from
verified taxonomy matches and does not affect FitScore or GapScore.

Tests use synthetic examples shaped like Adzuna and Greenhouse API responses and
normalize them through the real pipeline code. They need neither API keys nor
network access, and are not a representative market sample.

## Remaining work

1. **Populate and validate the production taxonomy.** The checked-in starter set
   is intentionally small. `lightcast_cache.csv` and `onet_mapping.csv` are not
   populated official taxonomies; download, license-check, normalize, and test
   an appropriate taxonomy before relying on results beyond the demo.
2. **Validate scoring on real samples.** Configure several target roles and
   multiple boards, inspect matches against complete descriptions, and evaluate
   role/title coverage, false positives, and Adzuna excerpt bias. Keep sample
   size and uncertainty visible to users.
3. **Review requirement classification.** Required/preferred status currently
   uses posting text cues and section headings. Test variations across sources
   and refine the rules before treating weighted demand as authoritative.
4. **Decide whether experience affects scores.** Skill-specific dated
   experience is reported as evidence; the project’s binary GapScore formula
   remains unchanged until the team agrees on a separate, transparent rule.
5. **Add remaining input formats if required.** Plain text is a small extension;
   JSON requires a defined schema. Google Docs can be downloaded as PDF/DOCX;
   direct integration needs separate OAuth/API work.
6. **Improve robustness and evaluation.** Add representative, consented resume
   fixtures or a privacy-safe evaluation set, test thin/no-data roles, and
   document fairness, privacy, and interpretation limits. Two-column PDF text
   order and cross-run repost deduplication remain known limitations.

## API keys — where they go

Put your real keys in a file called `.env` in the project root (this file is gitignored, never commit it).
Use `.env.example` as the template — it shows which variables are needed but keeps the values blank.

DO NOT COMMIT THE KEYS FOR THE LOVE OF GOD

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
