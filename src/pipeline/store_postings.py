"""
Role 1 - Component E: write postings to the shared SQLite database.

Rows follow the posting shape documented in normalize.py. unique_hash is
UNIQUE, so re-running the pipeline never duplicates a row; if a later pull
brings a better copy of a stored posting (full text instead of Adzuna's
excerpt), the stored row is upgraded in place.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

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
    role TEXT,
    location TEXT,
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
    "role": "TEXT",
    "location": "TEXT",
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
    "unique_hash", "source_id", "role", "location", "url", "is_truncated", "fetched_at",
    "salary_min", "salary_max", "salary_is_predicted", "contract_time", "category",
]

_UPSERT = f"""
INSERT INTO postings ({", ".join(COLUMNS)})
VALUES ({", ".join("?" for _ in COLUMNS)})
ON CONFLICT(unique_hash) DO UPDATE SET
    source = excluded.source,
    source_id = excluded.source_id,
    url = excluded.url,
    full_text = excluded.full_text,
    is_truncated = excluded.is_truncated,
    fetched_at = excluded.fetched_at
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
        conn.execute("CREATE INDEX IF NOT EXISTS idx_postings_role ON postings(role)")
        conn.execute(CREATE_MARKET_TABLE)
        conn.commit()
    finally:
        conn.close()


def _row_values(posting: dict, fetched_at: str) -> list:
    row = {
        **posting,
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
            written += conn.execute(_UPSERT, _row_values(posting, fetched_at)).rowcount
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
        return [
            {**dict(r), "is_truncated": bool(r["is_truncated"]),
             "salary_is_predicted": bool(r["salary_is_predicted"])}
            for r in rows
        ]
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


def stored_role_counts(db_path: Path = DB_PATH) -> dict[str, int]:
    """How many postings are stored per role, e.g. {"Welder": 30}."""
    conn = sqlite3.connect(db_path)
    try:
        return dict(conn.execute("SELECT role, COUNT(*) FROM postings GROUP BY role").fetchall())
    finally:
        conn.close()
