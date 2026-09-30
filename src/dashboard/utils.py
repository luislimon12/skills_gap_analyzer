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
