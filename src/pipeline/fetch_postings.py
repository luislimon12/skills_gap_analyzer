"""
Role 1 - Component A/C: pull job postings from Adzuna, Greenhouse, Lever.

These functions return raw JSON only. Turning it into postings happens in
normalize.py, dedup in dedup_postings.py, and run_pipeline.py wires it all up.

API keys: loaded from .env via python-dotenv. Never hardcode keys in this file.
See .env.example in the project root for the variable names.
"""

import os
import json
import re
from pathlib import Path

import requests
from dotenv import load_dotenv
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

load_dotenv()  # reads .env in project root

RAW_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"
TIMEOUT = 15


def _session() -> requests.Session:
    """Session that retries rate limits (429) and server errors with backoff."""
    retry = Retry(total=3, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=["GET"])
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    return session


_SESSION = _session()


class AdzunaError(requests.RequestException):
    """Adzuna request failed. The message never contains the app_id/app_key."""


_KEY_PARAMS = re.compile(r"(app_id|app_key)=[^&\s'\"]+")


def _redact(message: str) -> str:
    """requests puts the full URL (query string included) in its error text."""
    return _KEY_PARAMS.sub(r"\1=REDACTED", message)


def adzuna_keys_configured() -> bool:
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def fetch_adzuna_postings(role: str, country: str = "ca", results_per_page: int = 50, page: int = 1,
                          max_days_old: int | None = None, where: str | None = None) -> dict:
    """Pull one page of postings for a role from Adzuna. Returns raw JSON.

    The response also carries ``count`` (total live matches) and ``mean``
    (Adzuna's average advertised salary for the search), which the pipeline
    stores as a market snapshot.
    """
    if not adzuna_keys_configured():
        raise RuntimeError(
            "Missing Adzuna API keys. Copy .env.example to .env and fill in "
            "ADZUNA_APP_ID and ADZUNA_APP_KEY (free at https://developer.adzuna.com/)."
        )

    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
    params = {
        "app_id": os.getenv("ADZUNA_APP_ID"),
        "app_key": os.getenv("ADZUNA_APP_KEY"),
        "what": role,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }
    if max_days_old:
        params["max_days_old"] = int(max_days_old)
    if where:
        params["where"] = where

    try:
        response = _SESSION.get(url, params=params, timeout=TIMEOUT)
    except requests.RequestException as e:
        raise AdzunaError(f"Could not reach Adzuna: {_redact(str(e))}") from None
    if response.status_code in (401, 403):
        raise AdzunaError(
            f"Adzuna rejected the API keys (HTTP {response.status_code}). "
            "Check ADZUNA_APP_ID and ADZUNA_APP_KEY in .env."
        )
    if not response.ok:
        raise AdzunaError(f"Adzuna returned HTTP {response.status_code} for '{role}' page {page}.")
    return response.json()


def fetch_greenhouse_postings(board_token: str) -> dict:
    """Pull all postings for one company's Greenhouse board. No API key needed.

    content=true is required, otherwise Greenhouse omits the job description.
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    response = _SESSION.get(url, params={"content": "true"}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def fetch_lever_postings(company: str) -> list:
    """Pull all postings for one company's Lever board. No API key needed."""
    url = f"https://api.lever.co/v0/postings/{company}"
    response = _SESSION.get(url, params={"mode": "json"}, timeout=TIMEOUT)
    response.raise_for_status()
    return response.json()


def save_raw(data: dict | list, filename: str, raw_dir: Path = RAW_DATA_DIR) -> Path:
    """Dump raw API response to data/raw/ so later steps don't re-hit the API."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return out_path


if __name__ == "__main__":
    # Week 1 smoke test: pull one page for one role, save it, print a count.
    result = fetch_adzuna_postings("data analyst")
    path = save_raw(result, "adzuna_data_analyst_page1.json")
    print(f"Pulled {result.get('count', '?')} total matches, saved sample to {path}")
