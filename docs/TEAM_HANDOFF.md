# Team Handoff

## Scope

This is a working prototype, not a production-ready application. The folders
retain ownership boundaries so work can proceed in parallel.

## Ownership

- `src/pipeline/`: job-source clients, normalization, deduplication, and SQLite storage
- `src/resume/`: PDF/DOCX parsing and resume section extraction
- `src/scoring/`: taxonomy loading, skill matching, demand, gap, fit, and priority scoring
- `src/dashboard/`: Streamlit components and integration wiring
- `tests/`: tests owned by each role, plus integration coverage as contracts settle

## Working agreement

- Keep public function names and shared data shapes documented when they become real.
- Add tests with each implementation; do not rely on the placeholder smoke test.
- Keep API keys in `.env`, which must never be committed.
- Keep resumes, generated databases, and raw or processed pulls out of version control.
- Coordinate changes to `config/`, `requirements.txt`, and shared SQLite schema.
- This directory is the canonical project root. Keep parallel work inside this copy.

## Current implementation status

Roles 1 and 2 are implemented (see below). Role 3 now has an offline-testable
starter taxonomy matcher and demand, gap, and fit scoring. Role 4 wires resume
uploads and role postings to the scoring output. This is a prototype, not a
market-grade skill model: the starter taxonomy is limited, and results depend
on the volume and quality of the posting sample.

## Contracts from Roles 1–2

- **Postings** — `src.pipeline.store_postings.load_postings(role=None)` returns
  dicts in the shape documented at the top of `src/pipeline/normalize.py`.
  `full_text` is clean plain text; `is_truncated` is True for Adzuna excerpts.
  `requirement_level` is left `None` for Role 3 (required vs. preferred is per skill mention).
- **Resumes** — `src.resume.parse_resume.parse_resume(file, filename=None)` takes a
  path or an upload (Streamlit `UploadedFile` works) and returns clean text, or raises
  `ResumeParseError` with a message safe to show the student.
  `src.resume.extract_sections.extract_sections(text)` returns every key in
  `SECTION_KEYS` (`""` when missing).
  `src.resume.experience_dates.extract_experience_entries(sections["experience"])`
  returns job entries with `start`/`end`/`months`/`text`; `total_months(entries)`
  merges overlaps for per-skill years.
- **Shared text cleaning** — `src.common.text.clean_text` / `html_to_text`.
  The scoring matcher uses `clean_text` before matching.
- **Schema** — the `postings` table gained `source_id`, `role`, `location`, `url`,
  `is_truncated`, `fetched_at`. `init_db()` adds them to an existing week 1 database.
- **Scoring** — `src.scoring.analyze.analyze_role(resume_text, postings)` returns
  resume skill names, sample size, 0–100 cosine FitScore, all detected posting
  skills, and missing skills ranked by GapScore. Demand is `x/N`, Wilson bounds
  are unweighted 95% intervals, and weighted demand applies recency (`1.0`,
  `0.7`, `0.4`) times required/preferred mention weights (`1.0`, `0.5`). Unknown
  dates use `0.7`. Preferred headings apply until a required heading. Experience
  months are reported as evidence but do not modify the specified binary-presence
  GapScore.
- **Taxonomy** — `src/scoring/taxonomy/starter_skills.csv` is a small curated
  taxonomy with pipe-delimited aliases. `lightcast_cache.csv` remains an optional
  downloaded taxonomy input and is currently empty; these starter skills are
  not represented as official Lightcast records. `onet_mapping.csv` is also a
  header-only stub.
- **Text similarity** — `src.scoring.text_similarity.search_postings()` ranks a
  modest posting sample against resume text with local TF-IDF cosine similarity
  and can report exact keyword hits. The dashboard labels it experimental and
  keeps it separate from skill and fit scoring.
- **Offline sample tests** — `tests/test_scoring.py` normalizes synthetic
  Adzuna- and Greenhouse-shaped responses through the real posting normalizers,
  then scores a sample resume with a fixed reference date. No API keys or
  network access are required.

## Extra TODO feasibility

- **Description vectors / keywords** — implemented as experimental local TF-IDF
  similarity and exact keyword search. It remains a separate similarity signal,
  not a verified skill match or scoring input.
- **Experience-based weighting** — Role 3 now reports dated months of experience
  for each matched skill, merging overlapping entries. Applying it to GapScore
  still needs a team-approved rule; current scores follow the documented binary
  `has(skill)` formula.
- **Java vs. JavaScript disambiguation** — the starter matcher is case-insensitive
  and token-boundary-aware, so `Java` does not match inside `JavaScript`; `JS`
  is an alias for JavaScript. Wider context-based disambiguation can be added
  for genuinely ambiguous abbreviations.
- **Case normalization** — lowercasing for matching is appropriate and should
  not make capitalization a score signal. Preserve canonical taxonomy spelling
  for display.
- **Resume file formats** — PDF and DOCX work now. Plain text is a small,
  straightforward addition; JSON needs a defined input schema and validation.
  Google Docs is best supported by asking students to download as PDF/DOCX;
  direct Docs integration would add OAuth/API scope and is unnecessary for the
  prototype. LinkedIn's Save to PDF export follows the existing PDF path.
- **Production taxonomy** — Lightcast cache is still empty, so the app uses a
  small curated starter set. Register/download and validate a fuller taxonomy
  before relying on results for real students.
- **Real-data evaluation** — automated scoring fixtures are synthetic
  Adzuna/Greenhouse-shaped examples. Run and inspect real postings across
  configured boards and roles, verify extraction/requirement labels, and assess
  representativeness and thin-sample behavior.
- **Requirement classification** — required/preferred status is inferred from
  description cues/headings. These heuristics need evaluation across varied
  employer formatting before weighted demand is treated as authoritative.
- **Privacy/fairness and product readiness** — define consent and retention
  practices for student resumes; document fit-score limits; and add robust
  thin-data UX and user-facing interpretation guidance.

## Known limits

- Dedup runs within one pull. Across pulls the UNIQUE hash catches exact repeats and
  upgrades Adzuna excerpts to full text, but a repost whose date differs by a day
  from the stored copy is stored twice.
- Adzuna descriptions may be excerpts. A skill omitted from an excerpt is treated
  as absent from that posting, which can understate its observed demand.
- The curated starter taxonomy is incomplete; unmatched skill names, emerging
  tools, and alternate phrases can be missed until taxonomy aliases are expanded.
- Two-column PDF resumes can come out interleaved (pdfplumber reads across columns).
- No Kaggle resume test set is checked in yet; tests use generated DOCX/PDF files.