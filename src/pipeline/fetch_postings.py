"""
Role 1 - Component A/C: pull postings from Adzuna, government feeds,
Greenhouse, and Lever.

Fetchers return raw source data. Turning it into postings happens in
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
CANADA_JOB_BANK_DATASET_ID = "ea639e28-c0fc-48bf-b5dd-b8899bd43072"


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


class USAJobsError(requests.RequestException):
    """USAJOBS request failed without exposing credentials."""


_KEY_PARAMS = re.compile(r"(app_id|app_key)=[^&\s'\"]+")


def _redact(message: str) -> str:
    """requests puts the full URL (query string included) in its error text."""
    return _KEY_PARAMS.sub(r"\1=REDACTED", message)


def adzuna_keys_configured() -> bool:
    return bool(os.getenv("ADZUNA_APP_ID") and os.getenv("ADZUNA_APP_KEY"))


def usajobs_credentials_configured() -> bool:
    return bool(os.getenv("USAJOBS_API_KEY") and os.getenv("USAJOBS_USER_AGENT"))


def fetch_usajobs_postings(role: str, page: int = 1, results_per_page: int = 50) -> dict:
    """Fetch one page from the official USAJOBS Search API."""
    api_key = os.getenv("USAJOBS_API_KEY")
    user_agent = os.getenv("USAJOBS_USER_AGENT")
    if not api_key or not user_agent:
        raise RuntimeError(
            "USAJOBS requires USAJOBS_API_KEY and USAJOBS_USER_AGENT. "
            "Register at https://developer.usajobs.gov/."
        )

    headers = {
        "Host": "data.usajobs.gov",
        "User-Agent": user_agent,
        "Authorization-Key": api_key,
    }
    params = {
        "Keyword": role,
        "ResultsPerPage": results_per_page,
        "Page": page,
    }
    try:
        response = _SESSION.get(
            "https://data.usajobs.gov/api/Search",
            headers=headers,
            params=params,
            timeout=TIMEOUT,
        )
    except requests.RequestException as error:
        message = str(error).replace(api_key, "REDACTED").replace(user_agent, "REDACTED")
        raise USAJobsError(f"Could not reach the USAJOBS API: {message}") from None
    if response.status_code in (401, 403):
        raise USAJobsError(
            f"USAJOBS rejected the API credentials (HTTP {response.status_code}). "
            "Check USAJOBS_API_KEY and USAJOBS_USER_AGENT in .env."
        )
    if not response.ok:
        raise USAJobsError(f"USAJOBS returned HTTP {response.status_code} for '{role}' page {page}.")
    return response.json()


class CanadaJobBankError(requests.RequestException):
    """Official Job Bank open-data feed failed or has an unexpected schema."""


def fetch_canada_job_bank_postings() -> dict:
    """Fetch the newest monthly CSV resource from the official Open Government dataset."""
    metadata_url = (
        "https://open.canada.ca/data/api/action/package_show"
        f"?id={CANADA_JOB_BANK_DATASET_ID}"
    )
    try:
        metadata_response = _SESSION.get(metadata_url, timeout=TIMEOUT)
        metadata_response.raise_for_status()
        metadata = metadata_response.json()
    except (requests.RequestException, ValueError) as error:
        raise CanadaJobBankError(f"Could not load Job Bank dataset metadata: {error}") from None

    resources = (metadata.get("result") or {}).get("resources") or []
    csv_resources = [
        resource for resource in resources
        if (
            str(resource.get("format", "")).casefold() == "csv"
            and "en" in (resource.get("language") or [])
            and resource.get("url")
        )
    ]
    if not metadata.get("success") or not csv_resources:
        raise CanadaJobBankError(
            "The official Job Bank dataset metadata did not contain a downloadable English CSV."
        )
    resource = max(csv_resources, key=lambda item: item.get("last_modified") or "")
    try:
        response = _SESSION.get(resource["url"], timeout=TIMEOUT)
        response.raise_for_status()
    except requests.RequestException as error:
        raise CanadaJobBankError(f"Could not download the Job Bank CSV: {error}") from None

    try:
        csv_text = (
            response.content.decode("utf-16")
            if response.content.startswith((b"\xff\xfe", b"\xfe\xff"))
            else response.content.decode("utf-8-sig")
        )
    except UnicodeDecodeError:
        raise CanadaJobBankError(
            "The Job Bank CSV encoding is not supported (expected UTF-8 or UTF-16)."
        ) from None

    return {
        "dataset_id": CANADA_JOB_BANK_DATASET_ID,
        "resource_name": resource.get("name") or "Job Bank monthly posting dataset",
        "resource_url": resource["url"],
        "csv_text": csv_text,
    }


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


def save_raw(data: dict | list | str, filename: str, raw_dir: Path = RAW_DATA_DIR) -> Path:
    """Save raw API response or source text to data/raw/ for reproducibility."""
    raw_dir.mkdir(parents=True, exist_ok=True)
    out_path = raw_dir / filename
    if isinstance(data, str):
        out_path.write_text(data, encoding="utf-8")
    else:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    return out_path


if __name__ == "__main__":
    # Week 1 smoke test: pull one page for one role, save it, print a count.
    result = fetch_adzuna_postings("data analyst")
    path = save_raw(result, "adzuna_data_analyst_page1.json")
    print(f"Pulled {result.get('count', '?')} total matches, saved sample to {path}")
