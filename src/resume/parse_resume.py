"""
Role 2 - Component D: single entry point for resume uploads.

    text = parse_resume("resume.pdf")
    text = parse_resume(uploaded_file)          # Streamlit UploadedFile (has .name)
    text = parse_resume(file_obj, "resume.docx")
    text = parse_resume("resume.txt")             # plain text (UTF-8, or Windows-1252 fallback)

Returns clean plain text (src/common/text.py) ready for extract_sections().
"""

from pathlib import Path
from typing import BinaryIO

from src.common.text import clean_text
from src.resume.parse_docx import parse_docx
from src.resume.parse_pdf import parse_pdf

SUPPORTED_EXTENSIONS = {".pdf", ".docx", ".txt"}

# Fewer characters than this from a PDF almost always means a scanned image.
_MIN_TEXT_CHARS = 50


class ResumeParseError(ValueError):
    """Raised with a message that is safe to show the student in the dashboard."""


def parse_txt(source: str | BinaryIO) -> str:
    """Read a plain-text resume from a path or a binary upload."""
    if isinstance(source, str):
        with open(source, "rb") as f:
            data = f.read()
    else:
        data = source.read()
        if isinstance(data, str):
            return data
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("cp1252", errors="replace")


def parse_resume(file: str | Path | BinaryIO, filename: str | None = None) -> str:
    if filename is None:
        filename = str(file) if isinstance(file, (str, Path)) else getattr(file, "name", "")
    ext = Path(filename).suffix.lower()

    if ext not in SUPPORTED_EXTENSIONS:
        raise ResumeParseError(
            f"Unsupported file type '{ext or filename}'. Upload a PDF, DOCX or TXT file "
            "(.doc files: re-save as .docx first)."
        )

    source = str(file) if isinstance(file, Path) else file
    try:
        parser = {".pdf": parse_pdf, ".docx": parse_docx, ".txt": parse_txt}[ext]
        raw = parser(source)
    except Exception as e:  # corrupt / password-protected files raise library-specific errors
        raise ResumeParseError(f"Could not read {Path(filename).name}: the file may be corrupt or password-protected.") from e

    text = clean_text(raw)
    if len(text) < _MIN_TEXT_CHARS:
        raise ResumeParseError(
            "No readable text found. If this is a scanned PDF, export your resume "
            "directly from Word/Google Docs (or LinkedIn's 'Save to PDF') instead."
        )
    return text
