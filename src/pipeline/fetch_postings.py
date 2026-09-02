"""
Role 1 - Component A/C: pull job postings from Adzuna, Greenhouse, Lever.

Week 1 goal: prove the API connection works and get a first raw pull saved.
Dedup logic comes in dedup_postings.py (week 2), not here.

API keys: loaded from .env via python-dotenv. Never hardcode keys in this file.
See .env.example in the project root for the variable names.
"""

import os
import json
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv()  # reads .env in project root

ADZUNA_APP_ID = os.getenv("ADZUNA_APP_ID")
ADZUNA_APP_KEY = os.getenv("ADZUNA_APP_KEY")

RAW_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "raw"


def fetch_adzuna_postings(role: str, country: str = "ca", results_per_page: int = 50, page: int = 1) -> dict:
    """Pull one page of postings for a role from Adzuna. Returns raw JSON."""
    if not ADZUNA_APP_ID or not ADZUNA_APP_KEY:
        raise RuntimeError(
            "Missing Adzuna API keys. Copy .env.example to .env and fill in "
            "ADZUNA_APP_ID and ADZUNA_APP_KEY (free at https://developer.adzuna.com/)."
        )

    url = f"https://api.adzuna.com/v1/api/jobs/{country}/search/{page}"
    params = {
        "app_id": ADZUNA_APP_ID,
        "app_key": ADZUNA_APP_KEY,
        "what": role,
        "results_per_page": results_per_page,
        "content-type": "application/json",
    }

    response = requests.get(url, params=params, timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_greenhouse_postings(board_token: str) -> dict:
    """Pull all postings for one company's Greenhouse board. No API key needed."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def fetch_lever_postings(company: str) -> list:
    """Pull all postings for one company's Lever board. No API key needed."""
    url = f"https://api.lever.co/v0/postings/{company}?mode=json"
    response = requests.get(url, timeout=15)
    response.raise_for_status()
    return response.json()


def save_raw(data: dict | list, filename: str) -> Path:
    """Dump raw API response to data/raw/ so later steps don't re-hit the API."""
    RAW_DATA_DIR.mkdir(parents=True, exist_ok=True)
    out_path = RAW_DATA_DIR / filename
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    return out_path


if __name__ == "__main__":
    # Week 1 smoke test: pull one page for one role, save it, print a count.
    result = fetch_adzuna_postings("data analyst")
    path = save_raw(result, "adzuna_data_analyst_page1.json")
    print(f"Pulled {result.get('count', '?')} total matches, saved sample to {path}")
