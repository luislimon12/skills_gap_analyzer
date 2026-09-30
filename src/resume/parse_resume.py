"""
Role 2 - Component D: single entry point for resume uploads.

    text = parse_resume("resume.pdf")
    text = parse_resume(uploaded_file)          # Streamlit UploadedFile (has .name)
    text = parse_resume(file_obj, "resume.docx")

Returns clean plain text (src/common/text.py) ready for extract_sections().
"""

from pathlib import Path
from typing import BinaryIO

from src.common.text import clean_text
from src.resume.parse_docx import parse_docx
from src.resume.parse_pdf import parse_pdf

SUPPORTED_EXTENSIONS = {".pdf", ".docx"}

# Fewer characters than this from a PDF almost always means a scanned image.
_MIN_TEXT_CHARS = 50


class ResumeParseError(ValueError):
    """Raised with a message that is safe to show the student in the dashboard."""


def parse_resume(file: str | Path | BinaryIO, filename: str | None = None) -> str:
    if filename is None:
        filename = str(file) if isinstance(file, (str, Path)) else getattr(file, "name", "")
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ResumeParseError(
            f"Unsupported file type '{ext or filename}'. Upload a PDF or DOCX "
            "(.doc files: re-save as .docx first)."
        )

    source = str(file) if isinstance(file, Path) else file
    try:
        raw = parse_pdf(source) if ext == ".pdf" else parse_docx(source)
    except Exception as e:  # corrupt / password-protected files raise library-specific errors
        raise ResumeParseError(f"Could not read {Path(filename).name}: the file may be corrupt or password-protected.") from e

    text = clean_text(raw)
    if len(text) < _MIN_TEXT_CHARS:
        raise ResumeParseError(
            "No readable text found. If this is a scanned PDF, export your resume "
            "directly from Word/Google Docs (or LinkedIn's 'Save to PDF') instead."
        )
    return text
