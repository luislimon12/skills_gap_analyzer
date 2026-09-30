"""
Role 1 - end-to-end postings pull: fetch -> save raw -> normalize -> dedup -> store.

Run from the project root:
    python -m src.pipeline.run_pipeline
    python -m src.pipeline.run_pipeline --roles "Data Analyst" --max-pages 1

Settings come from src/pipeline/config_postings.yaml. Adzuna is skipped with a
warning if its keys aren't in .env, so the no-key Greenhouse/Lever boards still run.
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
from src.pipeline.normalize import normalize_adzuna, normalize_greenhouse, normalize_lever, title_matches_role
from src.pipeline.store_postings import DB_PATH, init_db, insert_postings

log = logging.getLogger(__name__)

CONFIG_PATH = Path(__file__).resolve().parent / "config_postings.yaml"


def load_config(path: Path = CONFIG_PATH) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def pull_adzuna(role: str, cfg: dict, save_raw: bool) -> list[dict]:
    postings = []
    per_page = cfg.get("results_per_page", 50)
    for page in range(1, cfg.get("max_pages_per_role", 1) + 1):
        raw = fetch.fetch_adzuna_postings(role, cfg.get("country", "ca"), per_page, page)
        if save_raw:
            fetch.save_raw(raw, f"adzuna_{_slug(role)}_page{page}.json")
        batch = normalize_adzuna(raw, role)
        if cfg.get("title_filter", True):
            batch = [p for p in batch if title_matches_role(p["title"], role)]
        postings.extend(batch)
        if len(raw.get("results", [])) < per_page:
            break  # last page
        time.sleep(cfg.get("request_delay_seconds", 1))
    return postings


def run(roles: list[str] | None = None, max_pages: int | None = None, save_raw: bool = True,
        skip_adzuna: bool = False, db_path: Path = DB_PATH, config: dict | None = None) -> dict:
    """Pull every configured source for every role and store the deduped result.

    Returns a summary: {"fetched": n, "unique": n, "written": n, "errors": [...]}.
    """
    config = config if config is not None else load_config()
    roles = roles or config.get("target_roles", [])
    adzuna_cfg = dict(config.get("adzuna") or {})
    if max_pages is not None:
        adzuna_cfg["max_pages_per_role"] = max_pages

    use_adzuna = not skip_adzuna and fetch.adzuna_keys_configured()
    if not skip_adzuna and not use_adzuna:
        log.warning("ADZUNA_APP_ID / ADZUNA_APP_KEY not set in .env - skipping Adzuna")

    postings: list[dict] = []
    errors: list[str] = []

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
                postings.extend(pull_adzuna(role, adzuna_cfg, save_raw))
            except requests.RequestException as e:
                errors.append(f"adzuna:{role}: {e}")
                log.error("Adzuna pull failed for %s: %s", role, e)
        for source, name, _, normalizer in boards:
            if (source, name) in board_raw:
                postings.extend(normalizer(board_raw[(source, name)], name, role))

    unique = dedup_postings(postings)
    init_db(db_path)
    written = insert_postings(unique, db_path)
    summary = {"fetched": len(postings), "unique": len(unique), "written": written, "errors": errors}
    log.info("Postings pull done: %s", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Pull job postings into the shared SQLite database.")
    parser.add_argument("--roles", nargs="+", help="Override target_roles from config_postings.yaml")
    parser.add_argument("--max-pages", type=int, help="Adzuna pages per role (each page = 1 API call)")
    parser.add_argument("--skip-adzuna", action="store_true", help="Only pull Greenhouse/Lever boards")
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
