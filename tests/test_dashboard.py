"""Role 4 tests: dashboard settings and display-data helpers."""

import pytest

from src.dashboard.utils import load_role_postings, load_settings, rank_gap_scores, resolve_database_path


def test_load_settings_reads_configured_roles_and_database(tmp_path):
    path = tmp_path / "settings.yaml"
    path.write_text(
        "database_path: data/example.db\ntarget_roles:\n  - Data Analyst\n",
        encoding="utf-8",
    )

    settings = load_settings(path)

    assert settings["target_roles"] == ["Data Analyst"]
    assert resolve_database_path(settings, tmp_path) == tmp_path / "data" / "example.db"


@pytest.mark.parametrize(
    "contents",
    [
        "database_path: data/example.db\ntarget_roles: []\n",
        "database_path: data/example.db\ntarget_roles: Data Analyst\n",
        "target_roles:\n  - Data Analyst\n",
    ],
)
def test_load_settings_rejects_incomplete_configuration(tmp_path, contents):
    path = tmp_path / "settings.yaml"
    path.write_text(contents, encoding="utf-8")

    with pytest.raises(ValueError, match="must define"):
        load_settings(path)


def test_load_role_postings_initializes_an_empty_database(tmp_path):
    database = tmp_path / "nested" / "postings.db"

    assert load_role_postings("Data Analyst", database) == []
    assert database.exists()


def test_rank_gap_scores_orders_highest_gap_first_and_limits_results():
    scores = [
        {"skill": "SQL", "gap_score": 0.4},
        {"skill": "Python", "gap_score": 0.8},
        {"skill": "Tableau", "gap_score": 0.2},
    ]

    assert [row["skill"] for row in rank_gap_scores(scores, limit=2)] == ["Python", "SQL"]


def test_salary_summary_uses_range_midpoints_and_skips_missing():
    from src.dashboard.utils import salary_summary

    postings = [
        {"salary_min": 60000, "salary_max": 80000, "salary_is_predicted": True},
        {"salary_min": 90000, "salary_max": None, "salary_is_predicted": False},
        {"salary_min": None, "salary_max": None},
        {},
    ]
    summary = salary_summary(postings)
    assert summary["count"] == 2
    assert summary["median"] == 80000
    assert summary["predicted_share"] == 0.5
    assert salary_summary([{}]) is None


def test_analysis_rows_for_export_are_sorted_and_flat():
    from src.dashboard.utils import analysis_rows_for_export

    row = {
        "skill": "SQL", "category": "Databases", "has_skill": False, "count": 2, "sample_size": 3,
        "demand": 2 / 3, "confidence_low": 0.2, "confidence_high": 0.9, "weighted_demand": 0.6,
        "gap_score": 0.6, "required_count": 2, "preferred_count": 0, "experience_years": 0.0,
    }
    rows = analysis_rows_for_export([{**row, "skill": "Excel", "gap_score": 0.1}, row])
    assert [r["skill"] for r in rows] == ["SQL", "Excel"]
    assert rows[0]["demand"] == 0.6667
