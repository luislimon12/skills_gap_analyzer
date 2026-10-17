"""Shared configuration and data-loading helpers for the Streamlit dashboard."""

from pathlib import Path

import yaml

from src.pipeline.store_postings import init_db, load_postings

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"


def load_settings(path: Path = SETTINGS_PATH) -> dict:
    """Load and validate the shared dashboard settings."""
    with open(path, encoding="utf-8") as settings_file:
        settings = yaml.safe_load(settings_file) or {}

    if not isinstance(settings, dict):
        raise ValueError(f"Settings in {path} must be a YAML mapping.")

    roles = settings.get("target_roles")
    if not isinstance(roles, list) or not roles or not all(
        isinstance(role, str) and role.strip() for role in roles
    ):
        raise ValueError(f"Settings in {path} must define at least one target role.")

    database_path = settings.get("database_path")
    if not isinstance(database_path, str) or not database_path.strip():
        raise ValueError(f"Settings in {path} must define database_path.")

    return settings


def resolve_database_path(settings: dict, project_root: Path = PROJECT_ROOT) -> Path:
    """Resolve a configured database path relative to the project root."""
    database_path = Path(settings["database_path"])
    if not database_path.is_absolute():
        database_path = project_root / database_path
    return database_path


def load_role_postings(role: str, database_path: Path) -> list[dict]:
    """Ensure the shared database exists, then load one role's postings."""
    init_db(database_path)
    return load_postings(role=role, db_path=database_path)


def rank_gap_scores(gap_scores: list[dict], limit: int | None = None) -> list[dict]:
    """Return scored skills in descending gap-score order."""
    ranked = sorted(gap_scores, key=lambda row: row["gap_score"], reverse=True)
    return ranked if limit is None else ranked[:limit]


def salary_summary(postings: list[dict]) -> dict | None:
    """Summarize advertised salaries in the sample (midpoint of each min/max range).

    Returns None when no posting carries a salary. ``predicted_share`` is the
    fraction of salaries that Adzuna estimated rather than the employer posted.
    """
    midpoints, predicted = [], 0
    for posting in postings:
        low, high = posting.get("salary_min"), posting.get("salary_max")
        values = [float(v) for v in (low, high) if v not in (None, "")]
        if not values:
            continue
        midpoints.append(sum(values) / len(values))
        predicted += bool(posting.get("salary_is_predicted"))
    if not midpoints:
        return None
    midpoints.sort()

    def quantile(q: float) -> float:
        position = (len(midpoints) - 1) * q
        lower = int(position)
        upper = min(lower + 1, len(midpoints) - 1)
        return midpoints[lower] + (midpoints[upper] - midpoints[lower]) * (position - lower)

    return {
        "count": len(midpoints),
        "median": quantile(0.5),
        "p25": quantile(0.25),
        "p75": quantile(0.75),
        "predicted_share": predicted / len(midpoints),
    }


def analysis_rows_for_export(skills: list[dict]) -> list[dict]:
    """Flatten scored skills into CSV-friendly rows for the download button."""
    return [
        {
            "skill": row["skill"],
            "category": row["category"],
            "on_resume": row["has_skill"],
            "postings_with_skill": row["count"],
            "sample_size": row["sample_size"],
            "demand": round(row["demand"], 4),
            "wilson_low": round(row["confidence_low"], 4),
            "wilson_high": round(row["confidence_high"], 4),
            "weighted_demand": round(row["weighted_demand"], 4),
            "gap_score": round(row["gap_score"], 4),
            "required_mentions": row["required_count"],
            "preferred_mentions": row["preferred_count"],
            "experience_years": row["experience_years"],
        }
        for row in rank_gap_scores(skills)
    ]
