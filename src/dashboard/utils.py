"""Shared configuration and data-loading helpers for the Streamlit dashboard."""

import hashlib
import os
import threading
import weakref
from pathlib import Path

import yaml
from sqlalchemy.engine import Engine

from src.db.engine import get_engine
from src.db.jobs_repo import job_status_counts, latest_market_stats, load_jobs_for_role
from src.db.migrate import upgrade

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

_MIGRATED: "weakref.WeakSet[Engine]" = weakref.WeakSet()
_MIGRATE_LOCK = threading.Lock()


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


def database_engine(database: "Path | str | Engine | None" = None) -> Engine:
    """Engine for DATABASE_URL, an explicit URL, or a SQLite file path. Schema is migrated once."""
    if isinstance(database, Engine):
        engine = database
    else:
        url = None
        if database is None:
            url = os.getenv("DATABASE_URL") or None
        elif "://" in str(database):
            url = str(database)
        else:
            path = Path(database)
            path.parent.mkdir(parents=True, exist_ok=True)
            url = f"sqlite:///{path.as_posix()}"
        engine = get_engine(url)
    # Tracked per engine object, not per URL: two sqlite:///:memory: engines share a URL
    # but are separate databases. The lock stops two first sessions migrating at once.
    if engine not in _MIGRATED:
        with _MIGRATE_LOCK:
            if engine not in _MIGRATED:
                upgrade(engine)
                _MIGRATED.add(engine)
    return engine


def settings_engine(settings: dict) -> Engine:
    """DATABASE_URL when set (production), otherwise the SQLite file in settings.database_path."""
    if os.getenv("DATABASE_URL"):
        return database_engine(None)
    return database_engine(resolve_database_path(settings))


def load_role_postings(role: str, database, include_closed: bool = False) -> list[dict]:
    """Jobs collected by the worker for one role. Closed jobs are hidden unless asked for."""
    engine = database_engine(database)
    with engine.connect() as conn:
        return load_jobs_for_role(conn, role, include_closed=include_closed)


def postings_fingerprint(postings: list[dict]) -> str:
    """Short hash of the postings that feed an analysis (ids and content)."""
    digest = hashlib.sha256()
    for posting in sorted(postings, key=lambda p: (str(p.get("source")), str(p.get("source_id")))):
        for field in ("source", "source_id", "title", "full_text", "posted_date"):
            digest.update(str(posting.get(field) or "").encode("utf-8"))
            digest.update(b"\x1f")
    return digest.hexdigest()[:16]


def database_job_counts(database) -> dict[str, int]:
    with database_engine(database).connect() as conn:
        return job_status_counts(conn)


def role_market_stats(role: str, database) -> list[dict]:
    with database_engine(database).connect() as conn:
        return latest_market_stats(conn, role)


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


def salary_summaries_by_country(postings: list[dict]) -> dict[str, dict | None]:
    """Return separate salary summaries so currencies from different countries aren't mixed."""
    countries = sorted({posting.get("country") or "Unknown country" for posting in postings})
    return {
        country: salary_summary([
            posting for posting in postings
            if (posting.get("country") or "Unknown country") == country
        ])
        for country in countries
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
