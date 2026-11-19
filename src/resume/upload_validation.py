"""Check an uploaded resume before parsing it.

Only PDF and DOCX are accepted, identified by file signature (magic bytes),
never by the extension the browser sends. Files over MAX_UPLOAD_BYTES and
DOCX archives that look like zip bombs or contain macros are rejected.
"""

from __future__ import annotations

import io
import zipfile

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
_MAX_DOCX_UNCOMPRESSED = 50 * 1024 * 1024
_MAX_DOCX_MEMBERS = 2000


class UploadRejected(ValueError):
    """Message is safe to show to the user."""


def detect_kind(data: bytes) -> str | None:
    """'pdf', 'docx' or None, from the file's contents only."""
    if data[:1024].lstrip().startswith(b"%PDF-") or b"%PDF-" in data[:1024]:
        return "pdf"
    if data.startswith(b"PK\x03\x04"):
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                names = set(archive.namelist())
        except zipfile.BadZipFile:
            return None
        if "word/document.xml" in names and "[Content_Types].xml" in names:
            return "docx"
    return None


def _check_docx(data: bytes) -> None:
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) > _MAX_DOCX_MEMBERS:
            raise UploadRejected("This DOCX file has an unusual structure and can't be processed.")
        if sum(member.file_size for member in members) > _MAX_DOCX_UNCOMPRESSED:
            raise UploadRejected("This DOCX file is too large once unpacked.")
        names = {member.filename.lower() for member in members}
        if any(name.endswith("vbaproject.bin") for name in names):
            raise UploadRejected("Files with macros aren't accepted. Save the resume as a plain .docx or PDF.")
        content_types = archive.read("[Content_Types].xml").lower()
        if b"macroenabled" in content_types:
            raise UploadRejected("Files with macros aren't accepted. Save the resume as a plain .docx or PDF.")


def validate_upload(data: bytes, max_bytes: int = MAX_UPLOAD_BYTES) -> str:
    """Return 'pdf' or 'docx', or raise UploadRejected."""
    if not data:
        raise UploadRejected("The file is empty.")
    if len(data) > max_bytes:
        raise UploadRejected(f"The file is larger than {max_bytes // (1024 * 1024)} MB.")
    kind = detect_kind(data)
    if kind is None:
        raise UploadRejected("Only PDF and Word (.docx) resumes are accepted.")
    if kind == "docx":
        _check_docx(data)
    return kind
