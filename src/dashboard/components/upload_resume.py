"""Resume upload: validate, rate-limit, parse, store skills only, and allow deletion.

The resume text stays in this browser session's memory. The database keeps
only the extracted skill names (and, if resumes.store_file is on, the file
encrypted), and deletes them after the retention period or when the user
clicks "Delete my resume".
"""

from __future__ import annotations

import io
import logging
from typing import Callable

import streamlit as st
from sqlalchemy.engine import Engine

from src.dashboard.security import RateLimiter, rate_limit_keys
from src.db.resumes_repo import create_resume, delete_resume, store_encrypted_file
from src.resume.parse_resume import ResumeParseError, parse_resume
from src.resume.upload_validation import MAX_UPLOAD_BYTES, UploadRejected, validate_upload
from src.worker.config import project_path

log = logging.getLogger(__name__)

STATE_KEY = "resume_record"
NONCE_KEY = "resume_upload_nonce"

DATA_USE_NOTE = (
    "**How your data is used:** your resume is read to find your skills. We keep {kept}, "
    "linked to an anonymous ID, and delete {them} after {days} days (or right away with "
    "**Delete my resume**). The text is never logged or shared."
)


def data_use_note(retention_days: int, store_file: bool) -> str:
    if store_file:
        kept = "an encrypted copy of the file, the list of skills and your results"
    else:
        kept = "only the list of skills and your results (the file itself is discarded)"
    return DATA_USE_NOTE.format(kept=kept, them="them", days=retention_days)


def _privacy_link() -> None:
    try:
        st.page_link("pages/1_Privacy_Policy.py", label="Privacy policy", icon=":material/policy:")
    except Exception:  # noqa: BLE001 - page links need the multipage layout
        st.caption("See the Privacy policy page in the sidebar.")


def render_upload(
    *,
    engine: Engine,
    resume_cfg: dict,
    limiter: RateLimiter,
    session_id: str,
    client_ip: str,
    extract_skills: Callable[[str], list[str]],
) -> str | None:
    """Show the uploader and return the parsed resume text (or None)."""
    retention_days = int(resume_cfg.get("retention_days", 30))
    nonce = st.session_state.setdefault(NONCE_KEY, 0)
    uploaded = st.file_uploader(
        "Upload your resume (PDF or Word .docx, up to 5 MB)",
        type=["pdf", "docx"],
        key=f"resume_upload_{nonce}",
        help="Google Docs resumes can be downloaded as PDF or DOCX before uploading.",
    )
    st.caption(data_use_note(retention_days, bool(resume_cfg.get("store_file", False))))
    _privacy_link()

    record = st.session_state.get(STATE_KEY)
    if uploaded is None:
        return None
    if record and record.get("file_id") == uploaded.file_id:
        return record["text"]

    if not limiter.allow("upload", *rate_limit_keys(client_ip, session_id)):
        st.error("Too many uploads. Please wait a while and try again.")
        return None

    data = uploaded.getvalue()
    try:
        kind = validate_upload(data, MAX_UPLOAD_BYTES)
        text = parse_resume(io.BytesIO(data), f"resume.{kind}")
    except (UploadRejected, ResumeParseError) as error:
        st.error(str(error))
        return None

    file_path = None
    if resume_cfg.get("store_file", False):
        file_path = store_encrypted_file(data, project_path(resume_cfg.get("storage_dir", "data/private/resumes")))
    with engine.begin() as conn:
        if record:  # replacing an earlier upload: delete the old one now
            delete_resume(conn, record["id"], reason="replaced")
        saved = create_resume(
            conn, session_id=session_id, skills=extract_skills(text),
            file_path=file_path, retention_days=retention_days,
        )
    st.session_state[STATE_KEY] = {
        "file_id": uploaded.file_id,
        "text": text,
        "id": saved["id"],
        "delete_after": saved["delete_after"],
        "saved_analyses": set(),
    }
    return text


def render_resume_controls(*, engine: Engine, on_deleted: Callable[[], None]) -> None:
    """Deletion date and the 'Delete my resume' button, once a resume is stored."""
    record = st.session_state.get(STATE_KEY)
    if not record:
        return
    st.info(f"Your resume data will be deleted automatically on "
            f"**{record['delete_after']:%B %d, %Y}**.")
    if st.button("Delete my resume", type="secondary", icon=":material/delete:"):
        with engine.begin() as conn:
            delete_resume(conn, record["id"], reason="user_request")
        st.session_state.pop(STATE_KEY, None)
        st.session_state[NONCE_KEY] = st.session_state.get(NONCE_KEY, 0) + 1
        on_deleted()
        st.toast("Your resume and its results were deleted.")
        st.rerun()
