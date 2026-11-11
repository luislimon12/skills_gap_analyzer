"""
Role 1 - end-to-end postings pull: fetch -> save raw -> normalize -> dedup -> store.

Run from the project root:
    python -m src.pipeline.run_pipeline
    python -m src.pipeline.run_pipeline --roles "Data Analyst" --max-pages 1

Settings come from src/pipeline/config_postings.yaml. Adzuna and USAJOBS are
skipped with a warning when their credentials are missing; Greenhouse/Lever
boards do not need keys.
"""

import argparse
import logging
import re
import time
from pathlib import Path

import requests
import yaml

from src.pipeline import fetch_postings as fetch
from src.pipeline.dedup_postings import dedup_postings
from src.pipeline.normalize import (
    normalize_adzuna,
    normalize_canada_job_bank,
    normalize_greenhouse,
    normalize_lever,
    normalize_usajobs,
    title_matches_role,
)
from src.pipeline.store_postings import (
    DB_PATH,
    backfill_posting_geography,
    init_db,
    insert_market_snapshot,
    insert_postings,
)

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config_postings.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def pull_adzuna(role: str, cfg: dict, save_raw: bool) -> tuple[list[dict], dict]:
    """Pull up to max_pages_per_role pages for a role.

    Returns (postings, market) where market holds Adzuna's total live count and
    mean advertised salary for the search, taken from the first page.
    """
    postings = []
    market: dict = {}
    per_page = cfg.get("results_per_page", 50)
    for page in range(1, cfg.get("max_pages_per_role", 1) + 1):
        raw = fetch.fetch_adzuna_postings(
            role, cfg.get("country", "ca"), per_page, page,
            max_days_old=cfg.get("max_days_old") or None,
            where=cfg.get("where") or None,
        )
        if page == 1:
            market = {"total_count": raw.get("count"), "mean_salary": raw.get("mean")}
        if save_raw:
            fetch.save_raw(raw, f"adzuna_{_slug(role)}_page{page}.json")
        batch = normalize_adzuna(raw, role, country=cfg.get("country", "ca"))
        if cfg.get("title_filter", True):
            batch = [p for p in batch if title_matches_role(p["title"], role)]
        postings.extend(batch)
        if len(raw.get("results", [])) < per_page:
            break  # last page
        time.sleep(cfg.get("request_delay_seconds", 1))
    return postings, market


def pull_usajobs(
    role: str,
    cfg: dict,
    save_raw: bool,
    errors: list[str] | None = None,
) -> list[dict]:
    """Fetch and normalize the configured USAJOBS search pages for one role."""
    postings = []
    per_page = cfg.get("results_per_page", 50)
    for page in range(1, cfg.get("max_pages_per_role", 2) + 1):
        try:
            raw = fetch.fetch_usajobs_postings(role, page, per_page)
        except requests.RequestException as error:
            message = f"usajobs:{role}: page {page}: {error}"
            if errors is not None:
                errors.append(message)
            log.error("USAJOBS page fetch failed for %s: %s", role, message)
            break
        if save_raw:
            fetch.save_raw(raw, f"usajobs_{_slug(role)}_page{page}.json")
        postings.extend(normalize_usajobs(raw, role))
        items = (raw.get("SearchResult") or {}).get("SearchResultItems") or []
        if len(items) < per_page:
            break
        time.sleep(cfg.get("request_delay_seconds", 1))
    return postings


def run(roles: list[str] | None = None, max_pages: int | None = None, save_raw: bool = True,
        skip_adzuna: bool = False, db_path: Path = DB_PATH, config: dict | None = None) -> dict:
    """Pull every configured source for every role and store the deduped result.

    Returns a summary: {"fetched": n, "unique": n, "written": n, "errors": [...],
    "market": {role: {"total_count": n, "mean_salary": x}}} (market only for Adzuna pulls).
    """
    config = config if config is not None else load_config()
    roles = roles or config.get("target_roles", [])
    adzuna_cfg = dict(config.get("adzuna") or {})
    if max_pages is not None:
        adzuna_cfg["max_pages_per_role"] = max_pages

    use_adzuna = not skip_adzuna and fetch.adzuna_keys_configured()
    if not skip_adzuna and not use_adzuna:
        log.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in .env - skipping Adzuna")
    usajobs_cfg = dict(config.get("usajobs") or {})
    use_usajobs = usajobs_cfg.get("enabled", True) and fetch.usajobs_credentials_configured()
    if usajobs_cfg.get("enabled", True) and not use_usajobs:
        log.warning("USAJOBS_API_KEY / USAJOBS_USER_AGENT not set in .env - skipping USAJOBS")
    canada_job_bank_cfg = dict(config.get("canada_job_bank") or {})

    postings: list[dict] = []
    errors: list[str] = []
    market: dict[str, dict] = {}
    init_db(db_path)
    backfill_posting_geography(
        db_path,
        source_country_hints={
            "adzuna": adzuna_cfg.get("country", "ca"),
            "usajobs": "us",
            "canada_job_bank": "ca",
        },
    )

    job_bank_raw = None
    if canada_job_bank_cfg.get("enabled", True):
        try:
            job_bank_raw = fetch.fetch_canada_job_bank_postings()
            if save_raw:
                fetch.save_raw(
                    job_bank_raw["csv_text"],
                    f"canada_job_bank_{_slug(job_bank_raw['resource_name'])}.csv",
                )
        except requests.RequestException as e:
            errors.append(f"canada_job_bank: {e}")
            log.error("Failed to fetch the Canada Job Bank dataset: %s", e)

    # Boards return every job at the company, so fetch each once and filter per role.
    boards = []
    for token in config.get("greenhouse_boards") or []:
        boards.append(("greenhouse", token, fetch.fetch_greenhouse_postings, normalize_greenhouse))
    for company in config.get("lever_boards") or []:
        boards.append(("lever", company, fetch.fetch_lever_postings, normalize_lever))

    board_raw = {}
    for source, name, fetcher, _ in boards:
        try:
            board_raw[(source, name)] = fetcher(name)
            if save_raw:
                fetch.save_raw(board_raw[(source, name)], f"{source}_{_slug(name)}.json")
        except requests.RequestException as e:
            errors.append(f"{source}:{name}: {e}")
            log.error("Failed to fetch %s board %s: %s", source, name, e)

    for role in roles:
        if use_adzuna:
            try:
                role_postings, role_market = pull_adzuna(role, adzuna_cfg, save_raw)
                postings.extend(role_postings)
                if role_market:
                    market[role] = role_market
                    insert_market_snapshot(role, adzuna_cfg.get("country", "ca"),
                                           role_market["total_count"], role_market["mean_salary"], db_path)
            except requests.RequestException as e:
                errors.append(f"adzuna:{role}: {e}")
                log.error("Adzuna pull failed for %s: %s", role, e)
        for source, name, _, normalizer in boards:
            if (source, name) in board_raw:
                postings.extend(normalizer(board_raw[(source, name)], name, role))
        if job_bank_raw is not None:
            try:
                postings.extend(normalize_canada_job_bank(job_bank_raw, role))
            except ValueError as e:
                errors.append(f"canada_job_bank:{role}: {e}")
                log.error("Could not normalize Canada Job Bank data for %s: %s", role, e)
        if use_usajobs:
            postings.extend(pull_usajobs(role, usajobs_cfg, save_raw, errors))

    unique = dedup_postings(postings)
    written = insert_postings(unique, db_path)
    summary = {"fetched": len(postings), "unique": len(unique), "written": written, "errors": errors,
               "market": market}
    log.info("Postings pull done: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull job postings into the shared SQLite database.")
    parser.add_argument("--roles", nargs="+", help="Override target_roles from config_postings.yaml")
    parser.add_argument("--max-pages", type=int, help="Adzuna pages per role (each page = 1 API call)")
    parser.add_argument("--skip-adzuna", action="store_true", help="Skip Adzuna; other configured sources still run")
    parser.add_argument("--no-save-raw", action="store_true", help="Don't write raw JSON to data/raw/")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    summary = run(roles=args.roles, max_pages=args.max_pages, save_raw=not args.no_save_raw,
                  skip_adzuna=args.skip_adzuna)
    print(f"Fetched {summary['fetched']}, {summary['unique']} unique, {summary['written']} rows written to {DB_PATH}")
    for err in summary["errors"]:
        print(f"  error: {err}")


if __name__ == "__main__":
    main()
