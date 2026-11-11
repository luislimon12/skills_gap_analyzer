"""
Role 1 - Component E: write postings to the shared SQLite database.

Rows follow the posting shape documented in normalize.py. unique_hash is
UNIQUE, so re-running the pipeline never duplicates a row; if a later pull
brings a better copy of a stored posting (full text instead of Adzuna's
excerpt), the stored row is upgraded in place.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from src.pipeline.dedup_postings import make_dedup_key, make_posting_hash, postings_are_duplicates
from src.pipeline.geography import normalize_geography

DB_PATH = Path(__file__).resolve().parents[2] / "data" / "skills_gap.db"

CREATE_POSTINGS_TABLE = """
CREATE TABLE IF NOT EXISTS postings (
    id INTEGER PRIMARY KEY,
    title TEXT,
    company TEXT,
    source TEXT,
    posted_date DATE,
    requirement_level TEXT,
    full_text TEXT,
    unique_hash TEXT UNIQUE,
    source_id TEXT,
    source_identities TEXT,
    role TEXT,
    location TEXT,
    country TEXT,
    region TEXT,
    dedup_key TEXT,
    url TEXT,
    is_truncated INTEGER DEFAULT 0,
    fetched_at TEXT,
    salary_min REAL,
    salary_max REAL,
    salary_is_predicted INTEGER DEFAULT 0,
    contract_time TEXT,
    category TEXT
);
"""

# One row per Adzuna pull: Adzuna's total live-posting count and mean salary for the search.
CREATE_MARKET_TABLE = """
CREATE TABLE IF NOT EXISTS market_snapshots (
    id INTEGER PRIMARY KEY,
    role TEXT,
    country TEXT,
    total_count INTEGER,
    mean_salary REAL,
    fetched_at TEXT
);
"""

# Columns added after the week 1 schema. init_db adds them to an older database file.
_ADDED_COLUMNS = {
    "source_id": "TEXT",
    "source_identities": "TEXT",
    "role": "TEXT",
    "location": "TEXT",
    "country": "TEXT",
    "region": "TEXT",
    "dedup_key": "TEXT",
    "url": "TEXT",
    "is_truncated": "INTEGER DEFAULT 0",
    "fetched_at": "TEXT",
    "salary_min": "REAL",
    "salary_max": "REAL",
    "salary_is_predicted": "INTEGER DEFAULT 0",
    "contract_time": "TEXT",
    "category": "TEXT",
}

COLUMNS = [
    "title", "company", "source", "posted_date", "requirement_level", "full_text",
    "unique_hash", "source_id", "source_identities", "role", "location", "country", "region", "dedup_key",
    "url", "is_truncated", "fetched_at",
    "salary_min", "salary_max", "salary_is_predicted", "contract_time", "category",
]

_UPSERT = f"""
INSERT INTO postings ({", ".join(COLUMNS)})
VALUES ({", ".join("?" for _ in COLUMNS)})
ON CONFLICT(unique_hash) DO UPDATE SET
    title = COALESCE(NULLIF(excluded.title, ''), postings.title),
    company = COALESCE(NULLIF(excluded.company, ''), postings.company),
    source = excluded.source,
    source_id = excluded.source_id,
    source_identities = excluded.source_identities,
    posted_date = COALESCE(excluded.posted_date, postings.posted_date),
    role = COALESCE(NULLIF(excluded.role, ''), postings.role),
    url = excluded.url,
    location = excluded.location,
    country = excluded.country,
    region = excluded.region,
    dedup_key = excluded.dedup_key,
    full_text = excluded.full_text,
    is_truncated = excluded.is_truncated,
    fetched_at = excluded.fetched_at,
    salary_min = COALESCE(excluded.salary_min, postings.salary_min),
    salary_max = COALESCE(excluded.salary_max, postings.salary_max),
    salary_is_predicted = CASE
        WHEN excluded.salary_min IS NOT NULL OR excluded.salary_max IS NOT NULL
        THEN excluded.salary_is_predicted
        ELSE postings.salary_is_predicted
    END,
    contract_time = COALESCE(NULLIF(excluded.contract_time, ''), postings.contract_time),
    category = COALESCE(NULLIF(excluded.category, ''), postings.category)
WHERE excluded.is_truncated < postings.is_truncated
   OR (excluded.is_truncated = postings.is_truncated
       AND length(excluded.full_text) > length(postings.full_text))
"""


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the postings table if it doesn't exist yet, and add any missing columns."""
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_POSTINGS_TABLE)
        existing = {row[1] for row in conn.execute("PRAGMA table_info(postings)")}
        for name, col_type in _ADDED_COLUMNS.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE postings ADD COLUMN {name} {col_type}")
        rows = conn.execute(
            "SELECT id, title, company, location, country, region FROM postings "
            "WHERE dedup_key IS NULL"
        ).fetchall()
        for row in rows:
            conn.execute(
                "UPDATE postings SET dedup_key = ? WHERE id = ?",
                (make_dedup_key(dict(zip(
                    ("id", "title", "company", "location", "country", "region"), row
                ))), row[0]),
            )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_postings_role ON postings(role)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_postings_dedup_key ON postings(dedup_key)")
        conn.execute(CREATE_MARKET_TABLE)
        conn.commit()
    finally:
        conn.close()


def backfill_posting_geography(
    db_path: Path = DB_PATH,
    source_country_hints: dict[str, str] | None = None,
) -> None:
    """Fill country/region metadata on older rows when the source/location permits."""
    source_country_hints = source_country_hints or {}
    conn = sqlite3.connect(db_path)
    try:
        rows = conn.execute(
            "SELECT id, source, title, company, location, country, region "
            "FROM postings WHERE COALESCE(country, '') = '' OR COALESCE(region, '') = ''"
        ).fetchall()
        for row in rows:
            posting_id, source, title, company, location, old_country, old_region = row
            country, region = normalize_geography(
                location or "",
                country_hint=old_country or source_country_hints.get(source or ""),
                region_hint=old_region,
            )
            country = old_country or country
            region = old_region or region
            if country == old_country and region == old_region:
                continue
            posting = {
                "title": title,
                "company": company,
                "location": location,
                "country": country,
                "region": region,
            }
            conn.execute(
                "UPDATE postings SET country = ?, region = ?, dedup_key = ? WHERE id = ?",
                (country, region, make_dedup_key(posting), posting_id),
            )
        conn.commit()
    finally:
        conn.close()


def _row_values(posting: dict, fetched_at: str) -> list:
    row = {
        **posting,
        "unique_hash": posting.get("unique_hash") or make_posting_hash(
            posting.get("title") or "",
            posting.get("company") or "",
            posting.get("posted_date") or "",
            location=posting.get("location") or "",
            source=posting.get("source") or "",
            source_id=posting.get("source_id") or "",
            url=posting.get("url") or "",
        ),
        "source_identities": json.dumps(
            posting.get("source_identities")
            or ([{"source": posting["source"], "source_id": posting["source_id"]}]
                if posting.get("source") and posting.get("source_id") else [])
        ),
        "dedup_key": make_dedup_key(posting),
        "is_truncated": int(bool(posting.get("is_truncated"))),
        "salary_is_predicted": int(bool(posting.get("salary_is_predicted"))),
        "fetched_at": fetched_at,
    }
    return [row.get(col) for col in COLUMNS]


def insert_postings(postings: list[dict], db_path: Path = DB_PATH) -> int:
    """Insert (or upgrade) deduped postings. Returns the number of rows written."""
    fetched_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    conn = sqlite3.connect(db_path)
    try:
        written = 0
        for posting in postings:
            row = dict(posting)
            dedup_key = make_dedup_key(row)
            conn.row_factory = sqlite3.Row
            candidates_by_hash = {}
            if row.get("source") and row.get("source_id"):
                for candidate_row in conn.execute(
                    "SELECT * FROM postings WHERE source = ? AND source_id = ?",
                    (row["source"], row["source_id"]),
                ).fetchall():
                    candidates_by_hash[candidate_row["unique_hash"]] = candidate_row
            if row.get("url"):
                for candidate_row in conn.execute(
                    "SELECT * FROM postings WHERE COALESCE(url, '') != ''"
                ).fetchall():
                    candidates_by_hash[candidate_row["unique_hash"]] = candidate_row
            if dedup_key:
                candidates = conn.execute(
                    "SELECT * FROM postings WHERE dedup_key = ?", (dedup_key,)
                ).fetchall()
                for candidate_row in candidates:
                    candidates_by_hash[candidate_row["unique_hash"]] = candidate_row
            for candidate_row in candidates_by_hash.values():
                candidate = dict(candidate_row)
                try:
                    candidate["source_identities"] = json.loads(
                        candidate.get("source_identities") or "[]"
                    )
                except (TypeError, ValueError):
                    candidate["source_identities"] = []
                if postings_are_duplicates(row, candidate):
                    row["unique_hash"] = candidate["unique_hash"]
                    identities = {
                        (item.get("source", "").casefold(), str(item.get("source_id", "")).strip())
                        for item in (row.get("source_identities") or [])
                        if item.get("source") and item.get("source_id")
                    }
                    identities.update(
                        (item["source"], item["source_id"])
                        for item in candidate["source_identities"]
                        if item.get("source") and item.get("source_id")
                    )
                    for identity_source, identity_id in (
                        (row.get("source"), row.get("source_id")),
                        (candidate.get("source"), candidate.get("source_id")),
                    ):
                        if identity_source and identity_id:
                            identities.add((identity_source.casefold(), str(identity_id).strip()))
                    row["source_identities"] = [
                        {"source": source, "source_id": source_id}
                        for source, source_id in sorted(identities)
                    ]
                    conn.execute(
                        "UPDATE postings SET source_identities = ? WHERE unique_hash = ?",
                        (json.dumps(row["source_identities"]), row["unique_hash"]),
                    )
                    break
            conn.row_factory = None
            written += conn.execute(_UPSERT, _row_values(row, fetched_at)).rowcount
        conn.commit()
        return written
    finally:
        conn.close()


def insert_posting(posting: dict, db_path: Path = DB_PATH) -> bool:
    """Insert one deduped posting dict. Returns True if a row was written."""
    return insert_postings([posting], db_path) == 1


def load_postings(role: str | None = None, db_path: Path = DB_PATH) -> list[dict]:
    """Read postings back as dicts (all roles, or one). This is what Role 3 consumes."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        if role is None:
            rows = conn.execute("SELECT * FROM postings ORDER BY id").fetchall()
        else:
            rows = conn.execute("SELECT * FROM postings WHERE role = ? ORDER BY id", (role,)).fetchall()
        postings = []
        for result in rows:
            posting = dict(result)
            try:
                posting["source_identities"] = json.loads(posting.get("source_identities") or "[]")
            except (TypeError, ValueError):
                posting["source_identities"] = []
            posting["is_truncated"] = bool(posting["is_truncated"])
            posting["salary_is_predicted"] = bool(posting["salary_is_predicted"])
            postings.append(posting)
        return postings
    finally:
        conn.close()


def insert_market_snapshot(role: str, country: str, total_count: int | None, mean_salary: float | None,
                           db_path: Path = DB_PATH) -> None:
    """Record Adzuna's live-posting count and mean salary for one role search."""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "INSERT INTO market_snapshots (role, country, total_count, mean_salary, fetched_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (role, country, total_count, mean_salary,
             datetime.now(timezone.utc).isoformat(timespec="seconds")),
        )
        conn.commit()
    finally:
        conn.close()


def load_latest_market_snapshot(role: str, db_path: Path = DB_PATH) -> dict | None:
    """Most recent Adzuna market snapshot for a role, or None if never pulled."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT role, country, total_count, mean_salary, fetched_at FROM market_snapshots "
            "WHERE role = ? ORDER BY id DESC LIMIT 1",
            (role,),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
