"""Offline scoring tests using representative Adzuna/Greenhouse response samples."""

from datetime import date

import pytest

from src.pipeline.normalize import normalize_adzuna, normalize_greenhouse
from src.scoring.analyze import analyze_role
from src.scoring.demand_scorer import demand_profile, demand_score, wilson_interval
from src.scoring.fit_scorer import fit_score
from src.scoring.priority_scorer import priority_score
from src.scoring.skill_matcher import find_skill_mentions, match_skills
from src.scoring.text_similarity import search_postings
from src.scoring.utils import recency_weight

AS_OF = date(2026, 9, 30)

ADZUNA_SAMPLE = {
    "results": [
        {
            "id": "sample-a1",
            "title": "Senior Data Analyst",
            "company": {"display_name": "Northwind Analytics"},
            "location": {"display_name": "Toronto, Ontario"},
            "created": "2026-09-20T09:00:00Z",
            "redirect_url": "https://example.test/jobs/sample-a1",
            "description": (
                "Requirements:\nPython and SQL.\n"
                "Nice to have:\nTableau and JavaScript."
            ),
        },
    ],
}

GREENHOUSE_SAMPLE = {
    "jobs": [
        {
            "id": 2001,
            "title": "Data Analyst, Reporting",
            "company_name": "Contoso",
            "location": {"name": "Remote"},
            "first_published": "2026-08-20T10:00:00Z",
            "absolute_url": "https://example.test/jobs/2001",
            "content": (
                "&lt;p&gt;Requirements&lt;/p&gt;"
                "&lt;ul&gt;&lt;li&gt;Python and SQL&lt;/li&gt;&lt;/ul&gt;"
                "&lt;p&gt;Nice to have&lt;/p&gt;"
                "&lt;ul&gt;&lt;li&gt;Tableau and Microsoft Excel&lt;/li&gt;&lt;/ul&gt;"
            ),
        },
        {
            "id": 2002,
            "title": "Data Analyst, Operations",
            "company_name": "Fabrikam",
            "location": {"name": "Ottawa"},
            "first_published": "2026-05-20T10:00:00Z",
            "absolute_url": "https://example.test/jobs/2002",
            "content": (
                "<p>Requirements</p><ul><li>Python and Microsoft Excel</li></ul>"
                "<p>Preferred qualifications</p><ul><li>Power BI</li></ul>"
            ),
        },
    ],
}

SAMPLE_POSTINGS = (
    normalize_adzuna(ADZUNA_SAMPLE, "Data Analyst")
    + normalize_greenhouse(GREENHOUSE_SAMPLE, "sample-board", "Data Analyst")
)

SAMPLE_RESUME = """Jordan Lee

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


def test_matcher_is_case_insensitive_boundary_aware_and_uses_aliases():
    assert match_skills("python, PY and PostgreSQL; Excel via JavaScript") == [
        "Python",
        "JavaScript",
        "PostgreSQL",
        "Microsoft Excel",
    ]
    assert match_skills("JavaScript") == ["JavaScript"]
    assert match_skills("Java developer") == ["Java"]
    assert "Java" not in match_skills("javascript")


def test_matcher_reports_useful_evidence():
    mentions = find_skill_mentions("Built dashboards with Tableau.")
    assert [mention["skill"] for mention in mentions] == ["Tableau"]
    assert "Tableau" in mentions[0]["evidence"]


def test_recency_weights_and_unknown_date_fallback():
    assert recency_weight("2026-09-15", AS_OF) == 1.0
    assert recency_weight("2026-08-20", AS_OF) == 0.7
    assert recency_weight("2026-05-20", AS_OF) == 0.4
    assert recency_weight(None, AS_OF) == 0.7


def test_wilson_interval_handles_small_samples_and_empty_samples():
    low, high = wilson_interval(0, 10)
    assert low == 0.0
    assert high == pytest.approx(0.2775, abs=0.001)
    assert wilson_interval(0, 0) == (0.0, 0.0)
    assert wilson_interval(10, 10)[1] == 1.0
    with pytest.raises(ValueError):
        wilson_interval(11, 10)


def test_demand_profile_uses_sample_shaped_adzuna_and_greenhouse_postings():
    profile = {row["skill"]: row for row in demand_profile(SAMPLE_POSTINGS, as_of=AS_OF)}

    assert len(SAMPLE_POSTINGS) == 3
    assert profile["Python"]["count"] == 3
    assert profile["Python"]["demand"] == 1.0
    assert profile["Python"]["weighted_demand"] == pytest.approx(1.0)
    assert profile["SQL"]["count"] == 2
    assert profile["SQL"]["demand"] == pytest.approx(2 / 3)
    assert profile["SQL"]["weighted_demand"] == pytest.approx(1.7 / 2.1)
    assert profile["Tableau"]["required_count"] == 0
    assert profile["Tableau"]["preferred_count"] == 2
    assert profile["Tableau"]["weighted_demand"] == pytest.approx(0.85 / 2.1)
    assert profile["Microsoft Excel"]["weighted_demand"] == pytest.approx(0.75 / 2.1)
    assert profile["Power BI"]["weighted_demand"] == pytest.approx(0.2 / 2.1)
    assert 0 <= profile["SQL"]["confidence_low"] <= profile["SQL"]["confidence_high"] <= 1


def test_demand_score_and_empty_postings():
    assert demand_score("Python", SAMPLE_POSTINGS) == 1.0
    assert demand_score("Tableau", []) == 0.0
    assert demand_profile([], as_of=AS_OF) == []


def test_gap_score_and_fit_cosine_vectors():
    assert priority_score(0.8, False) == 0.8
    assert priority_score(0.8, True) == 0.0
    assert fit_score({"Python": 1, "SQL": 1}, {"Python": 1, "SQL": 1}) == pytest.approx(1.0)
    assert fit_score({"Python": 1}, {"SQL": 1}) == pytest.approx(0.0)
    assert fit_score([0, 0], [1, 0]) == 0.0
    with pytest.raises(ValueError):
        fit_score([1], [1, 0])


def test_end_to_end_analysis_returns_gaps_fit_and_experience_evidence():
    analysis = analyze_role(SAMPLE_RESUME, SAMPLE_POSTINGS, as_of=AS_OF)
    skills = {row["skill"]: row for row in analysis["skills"]}

    assert analysis["postings_count"] == 3
    assert analysis["resume_skills"] == ["Python", "SQL"]
    assert 0 < analysis["fit_score"] < 100
    assert skills["Python"]["has_skill"] is True
    assert skills["Python"]["gap_score"] == 0.0
    assert skills["Python"]["experience_months"] == 33
    assert skills["Tableau"]["has_skill"] is False
    assert skills["Tableau"]["gap_score"] > 0
    assert analysis["gaps"][0]["gap_score"] >= analysis["gaps"][-1]["gap_score"]


def test_tfidf_similarity_ranks_relevant_postings_and_keyword_search_is_bounded():
    postings = [
        {"title": "Data Analyst", "full_text": "Python SQL pipelines and reporting."},
        {"title": "Frontend Engineer", "full_text": "JavaScript React CSS development."},
    ]

    results = search_postings(
        "Build Python and SQL data pipelines.",
        postings,
        keywords=["Java", "JavaScript", "Python"],
    )

    assert results[0]["posting"]["title"] == "Data Analyst"
    assert results[0]["similarity"] > results[1]["similarity"]
    assert results[0]["keyword_matches"] == ["Python"]
    assert results[1]["keyword_matches"] == ["JavaScript"]
