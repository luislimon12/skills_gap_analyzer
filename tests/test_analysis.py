"""Resume parsing and skills-gap scoring, including scoring jobs straight from the database."""

import io
from datetime import date, timedelta

import docx
import pytest

from src.db.jobs_repo import load_jobs_for_role
from src.resume.parse_resume import ResumeParseError, parse_resume
from src.scoring.analyze import analyze_role
from src.scoring.demand_scorer import wilson_interval
from src.scoring.fit_scorer import fit_score
from src.scoring.priority_scorer import priority_score
from src.scoring.skill_matcher import match_skills
from src.scoring.utils import recency_weight
from tests.helpers import T0, make_job, upsert

AS_OF = date(2026, 9, 30)

RESUME = """Jordan Lee

SUMMARY
Data analyst experienced with Python, SQL, and retail reporting.

WORK EXPERIENCE
Data Analyst, Northwind Analytics
Jan 2024 - Present
- Built Python data pipelines and automated SQL reporting.
- Collaborated with business stakeholders.

SKILLS
Python, SQL
"""


def test_resume_parser_reads_docx_tables_and_pdf_uploads(tmp_path):
    document = docx.Document()
    document.add_paragraph("Jordan Lee")
    document.add_paragraph("SKILLS")
    table = document.add_table(rows=1, cols=2)
    table.rows[0].cells[0].text = "Python"
    table.rows[0].cells[1].text = "SQL"
    document.add_paragraph("EXPERIENCE")
    document.add_paragraph("Data Analyst, Acme Retail — Jan 2024 – Present, building Tableau dashboards for stores")
    buffer = io.BytesIO()
    document.save(buffer)
    upload = io.BytesIO(buffer.getvalue())
    upload.name = "My Resume.DOCX"  # Streamlit uploads expose .name like this
    assert parse_resume(upload).splitlines()[:4] == ["Jordan Lee", "SKILLS", "Python | SQL", "EXPERIENCE"]

    from reportlab.pdfgen import canvas

    path = tmp_path / "resume.pdf"
    pdf = canvas.Canvas(str(path))
    for i, line in enumerate(["Jordan Lee", "SKILLS", "Python, SQL, Tableau and Excel for reporting work"]):
        pdf.drawString(72, 750 - 20 * i, line)
    pdf.save()
    assert parse_resume(path).splitlines() == ["Jordan Lee", "SKILLS", "Python, SQL, Tableau and Excel for reporting work"]


@pytest.mark.parametrize(
    ("data", "filename", "message"),
    [(b"x", "resume.doc", "Unsupported file type"),
     (b"not really a pdf", "resume.pdf", "corrupt")],
)
def test_resume_parser_gives_friendly_errors(data, filename, message):
    with pytest.raises(ResumeParseError, match=message):
        parse_resume(io.BytesIO(data), filename)


def test_skill_matcher_is_case_insensitive_and_uses_aliases():
    assert match_skills("python, PY and PostgreSQL; Excel via JavaScript") == [
        "Python", "JavaScript", "PostgreSQL", "Microsoft Excel"]
    assert match_skills("Java developer") == ["Java"]
    assert "Java" not in match_skills("javascript")


def test_scoring_math_recency_wilson_fit_and_priority():
    assert recency_weight("2026-09-15", AS_OF) == 1.0
    assert recency_weight("2026-08-20", AS_OF) == 0.7
    assert recency_weight("2026-05-20", AS_OF) == 0.4
    assert recency_weight(None, AS_OF) == 0.7
    low, high = wilson_interval(0, 10)
    assert low == 0.0 and high == pytest.approx(0.2775, abs=0.001)
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert priority_score(0.8, False) == 0.8 and priority_score(0.8, True) == 0.0
    assert fit_score({"Python": 1, "SQL": 1}, {"Python": 1, "SQL": 1}) == pytest.approx(1.0)
    assert fit_score({"Python": 1}, {"SQL": 1}) == pytest.approx(0.0)
    with pytest.raises(ValueError):
        fit_score([1], [1, 0])


def test_jobs_stored_by_the_worker_can_be_scored_end_to_end(engine):
    upsert(engine, [
        make_job(1, description="Requirements:\nPython and SQL.\nNice to have:\nTableau.",
                 posted_date=T0.date()),
        make_job(2, description="Requirements:\nPython and Microsoft Excel.",
                 posted_date=(T0 - timedelta(days=40)).date()),
    ])
    with engine.connect() as conn:
        postings = load_jobs_for_role(conn, "Data Analyst")
    analysis = analyze_role(RESUME, postings, as_of=T0.date())
    skills = {row["skill"]: row for row in analysis["skills"]}

    assert analysis["postings_count"] == 2
    assert {"Python", "SQL"} <= set(analysis["resume_skills"])
    assert 0 < analysis["fit_score"] <= 100
    assert skills["Python"]["has_skill"] is True and skills["Python"]["gap_score"] == 0.0
    assert skills["Python"]["count"] == 2
    assert skills["Tableau"]["has_skill"] is False and skills["Tableau"]["gap_score"] > 0
    assert analysis["gaps"] == sorted(analysis["gaps"], key=lambda row: -row["gap_score"])


def test_analysis_without_a_resume_still_profiles_demand(engine):
    upsert(engine, [make_job(1, description="Requirements:\nPython and SQL.", posted_date=T0.date())])
    with engine.connect() as conn:
        postings = load_jobs_for_role(conn, "Data Analyst")
    analysis = analyze_role("", postings, as_of=T0.date())
    assert analysis["resume_skills"] == []
    assert {"Python", "SQL"} <= {row["skill"] for row in analysis["skills"]}
    assert all(row["has_skill"] is False for row in analysis["skills"])
