"""
Role 1 - Component E: write postings to the shared SQLite database.

Week 1 goal: create the database file and the postings table so everyone
else has something to point at. Actual insert logic (from real pulls)
comes with dedup in week 2.
"""

import sqlite3
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
    unique_hash TEXT UNIQUE
);
"""


def init_db(db_path: Path = DB_PATH) -> None:
    """Create the postings table if it doesn't exist yet."""
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(CREATE_POSTINGS_TABLE)
        conn.commit()
    finally:
        conn.close()


def insert_posting(posting: dict, db_path: Path = DB_PATH) -> None:
    """TODO (week 2): insert one deduped posting dict into the table."""
    raise NotImplementedError("Insert logic - build once dedup_postings.py is ready")


if __name__ == "__main__":
    init_db()
    print(f"Database ready at {DB_PATH}")
