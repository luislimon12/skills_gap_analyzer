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
