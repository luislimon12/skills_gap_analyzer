# Team Handoff

## Scope

This is a Week 1 scaffold, not a finished or supported application. The
folders establish ownership boundaries so work can proceed in parallel.

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

## Not implemented yet

The current placeholders intentionally leave parsing, matching, scoring, storage
insertion, live API ingestion, and dashboard wiring for the assigned team
members. A clean import or a passing smoke placeholder does not mean those
features are complete.