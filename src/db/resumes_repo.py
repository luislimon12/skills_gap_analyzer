"""Resume records, their analyses, and 30-day deletion.

Default (production plan option a): the uploaded file is parsed in memory and
discarded; only the extracted skill names are stored. With
``resumes.store_file: true`` the file is also kept, encrypted with
ENCRYPTION_KEY, under a random name outside the web root.

Nothing here logs resume text or personal details: only random resume IDs.
"""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.engine import Connection

from src.db.engine import PROJECT_ROOT, as_utc, utcnow
from src.db.schema import analyses, resume_deletions, resumes

log = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 30
DEFAULT_STORAGE_DIR = PROJECT_ROOT / "data" / "private" / "resumes"


class EncryptionKeyMissing(RuntimeError):
    pass


def _fernet():
    from cryptography.fernet import Fernet  # imported lazily: only needed when storing files

    key = os.getenv("ENCRYPTION_KEY")
    if not key:
        raise EncryptionKeyMissing("ENCRYPTION_KEY must be set to store resume files.")
    return Fernet(key.encode())


def store_encrypted_file(data: bytes, storage_dir: Path = DEFAULT_STORAGE_DIR) -> str:
    """Encrypt ``data`` and write it under a random file name. Returns the path."""
    storage_dir = Path(storage_dir)
    storage_dir.mkdir(parents=True, exist_ok=True)
    path = storage_dir / f"{uuid.uuid4().hex}.bin"
    path.write_bytes(_fernet().encrypt(data))
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass
    return str(path)


def read_encrypted_file(path: str) -> bytes:
    return _fernet().decrypt(Path(path).read_bytes())


def create_resume(
    conn: Connection,
    *,
    session_id: str,
    skills: list[str],
    file_path: str | None = None,
    retention_days: int = DEFAULT_RETENTION_DAYS,
    now: datetime | None = None,
) -> dict:
    now = now or utcnow()
    resume_id = str(uuid.uuid4())
    delete_after = now + timedelta(days=retention_days)
    conn.execute(resumes.insert().values(
        id=resume_id, session_id=session_id, skills=json.dumps(sorted(set(skills))),
        file_path=file_path, uploaded_at=now, delete_after=delete_after,
    ))
    return {"id": resume_id, "uploaded_at": now, "delete_after": delete_after}


def get_resume(conn: Connection, resume_id: str) -> dict | None:
    row = conn.execute(select(resumes).where(resumes.c.id == resume_id)).mappings().first()
    if row is None:
        return None
    record = dict(row)
    record["skills"] = json.loads(record["skills"] or "[]")
    record["delete_after"] = as_utc(record["delete_after"])
    record["uploaded_at"] = as_utc(record["uploaded_at"])
    return record


def save_analysis(
    conn: Connection,
    *,
    resume_id: str,
    role: str,
    fit_score: float | None,
    postings_count: int,
    skills: list[dict],
    now: datetime | None = None,
) -> int:
    """Store the scored skills (no resume text) for one resume and role."""
    results = [
        {
            "skill": row["skill"],
            "has_skill": bool(row.get("has_skill")),
            "demand": round(float(row.get("demand", 0.0)), 4),
            "weighted_demand": round(float(row.get("weighted_demand", 0.0)), 4),
            "gap_score": round(float(row.get("gap_score", 0.0)), 4),
        }
        for row in skills
    ]
    return conn.execute(analyses.insert().values(
        resume_id=resume_id, role=role, created_at=now or utcnow(),
        fit_score=fit_score, postings_count=postings_count, results=json.dumps(results),
    )).inserted_primary_key[0]


def analyses_for_resume(conn: Connection, resume_id: str) -> list[dict]:
    rows = conn.execute(
        select(analyses).where(analyses.c.resume_id == resume_id).order_by(analyses.c.created_at)
    ).mappings().all()
    return [{**dict(row), "results": json.loads(row["results"] or "[]")} for row in rows]


def delete_resume(conn: Connection, resume_id: str, *, reason: str, now: datetime | None = None) -> bool:
    """Delete one resume: its encrypted file, its analyses and its row, together.

    The file is removed first; if that fails the rows stay so the next cleanup
    run retries. Returns True if a resume was deleted.
    """
    row = conn.execute(select(resumes.c.file_path).where(resumes.c.id == resume_id)).first()
    if row is None:
        return False
    file_path = row[0]
    if file_path:
        try:
            Path(file_path).unlink(missing_ok=True)
        except OSError:
            log.error("Could not delete the stored file for resume %s; will retry", resume_id)
            raise
    conn.execute(delete(analyses).where(analyses.c.resume_id == resume_id))
    conn.execute(delete(resumes).where(resumes.c.id == resume_id))
    conn.execute(resume_deletions.insert().values(
        resume_id=resume_id, reason=reason, deleted_at=now or utcnow(),
    ))
    log.info("Deleted resume %s (%s)", resume_id, reason)
    return True


def delete_expired_resumes(conn: Connection, now: datetime | None = None) -> int:
    """Delete every resume past ``delete_after``. Returns the number deleted."""
    now = now or utcnow()
    expired = [row[0] for row in conn.execute(select(resumes.c.id).where(resumes.c.delete_after <= now))]
    deleted = 0
    for resume_id in expired:
        try:
            deleted += delete_resume(conn, resume_id, reason="expired", now=now)
        except OSError:
            continue
    return deleted


def orphaned_files(conn: Connection, storage_dir: Path = DEFAULT_STORAGE_DIR) -> list[Path]:
    """Encrypted files on disk with no resume row (e.g. a crash between steps)."""
    storage_dir = Path(storage_dir)
    if not storage_dir.is_dir():
        return []
    known = {
        Path(path).name for (path,) in conn.execute(select(resumes.c.file_path).where(resumes.c.file_path.isnot(None)))
    }
    return [path for path in storage_dir.glob("*.bin") if path.name not in known]
