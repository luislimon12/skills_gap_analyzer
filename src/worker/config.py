"""Load config/ingestion.yaml with defaults for anything missing."""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

from src.db.engine import PROJECT_ROOT

INGESTION_CONFIG_PATH = PROJECT_ROOT / "config" / "ingestion.yaml"
SETTINGS_PATH = PROJECT_ROOT / "config" / "settings.yaml"

DEFAULTS: dict = {
    "sources": {
        "adzuna": {
            "countries": ["ca"],
            "results_per_page": 50,
            "title_filter": True,
            "request_delay_seconds": 3,
            "timeout_seconds": 15,
        },
        "loopcv": {"enabled": False},
    },
    "queries": [],
    "quota": {
        "daily_requests": 250,
        "weekly_requests": 1000,
        "monthly_requests": 2500,
        "safety_margin": 0.95,
        "backfill_share": 0.70,
    },
    "backfill": {"max_days_old": 90, "max_pages_per_slice": 20, "rescan_interval_hours": 24},
    "incremental": {"interval_hours": 1, "max_pages": 2},
    "lifecycle": {"stale_days_before_closed": 3},
    "link_check": {"enabled": True, "daily_cap": 100, "timeout_seconds": 10, "delay_seconds": 1},
    "retry": {"attempts": 3, "backoff_seconds": 2},
    "raw_responses": {"keep": True, "retention_days": 7, "dir": "data/raw/api"},
    "resumes": {"retention_days": 30, "store_file": False, "storage_dir": "data/private/resumes"},
    "alerts": {"disk_free_min_percent": 10, "min_hours_between_emails": 6},
}


def _merge(base: dict, override: dict) -> dict:
    merged = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigError(ValueError):
    """config/ingestion.yaml has the wrong shape. The message names the bad setting."""


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ConfigError(f"config/ingestion.yaml: {message}")


def validate_config(config: dict) -> dict:
    """Check the structure before any quota is spent. Returns the config."""
    for section, value in DEFAULTS.items():
        if isinstance(value, dict):
            _require(isinstance(config.get(section), dict), f"'{section}' must be a mapping")
    for name in ("adzuna", "loopcv"):
        _require(isinstance(config["sources"].get(name), dict), f"'sources.{name}' must be a mapping")
    adzuna = config["sources"]["adzuna"]
    countries = adzuna.get("countries")
    _require(isinstance(countries, list) and countries and all(isinstance(c, str) and c.strip() for c in countries),
             "'sources.adzuna.countries' must be a non-empty list of country codes, e.g. [\"ca\", \"us\"]")
    queries = config.get("queries")
    _require(isinstance(queries, list) and all(isinstance(q, str) for q in queries),
             "'queries' must be a list of search terms (one '- \"Data Analyst\"' line each)")
    numbers = {
        "sources.adzuna.results_per_page": adzuna.get("results_per_page"),
        "quota.daily_requests": config["quota"].get("daily_requests"),
        "quota.weekly_requests": config["quota"].get("weekly_requests"),
        "quota.monthly_requests": config["quota"].get("monthly_requests"),
        "backfill.max_pages_per_slice": config["backfill"].get("max_pages_per_slice"),
        "lifecycle.stale_days_before_closed": config["lifecycle"].get("stale_days_before_closed"),
        "resumes.retention_days": config["resumes"].get("retention_days"),
    }
    for name, value in numbers.items():
        _require(isinstance(value, int) and not isinstance(value, bool) and value > 0,
                 f"'{name}' must be a positive whole number")
    for name in ("safety_margin", "backfill_share"):
        value = config["quota"].get(name)
        _require(isinstance(value, (int, float)) and not isinstance(value, bool) and 0 < value <= 1,
                 f"'quota.{name}' must be a number between 0 and 1")
    return config


def load_ingestion_config(path: Path = INGESTION_CONFIG_PATH, settings_path: Path = SETTINGS_PATH) -> dict:
    data = {}
    if Path(path).is_file():
        with open(path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    _require(isinstance(data, dict), "the file must be a YAML mapping")
    config = _merge(DEFAULTS, data)
    if not config.get("queries") and Path(settings_path).is_file():
        with open(settings_path, encoding="utf-8") as handle:
            settings = yaml.safe_load(handle) or {}
        config["queries"] = list(settings.get("target_roles") or [])
    validate_config(config)
    config["queries"] = [q.strip() for q in config["queries"] if q.strip()]
    return config


def project_path(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else PROJECT_ROOT / path
