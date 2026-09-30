"""
Role 2 - Component D: extract text from an uploaded PDF resume.

Splitting the text into sections (education/experience/skills) happens in
extract_sections.py. Use parse_resume.parse_resume() rather than calling this
directly - it also cleans the text and rejects image-only PDFs.
"""

from typing import BinaryIO

import pdfplumber


def parse_pdf(file: str | BinaryIO) -> str:
    """Return all text from a PDF resume as one string. Accepts a path or a file-like object."""
    text_parts = []
    with pdfplumber.open(file) as pdf:
        for page in pdf.pages:
            # x_tolerance=1.5 keeps words from gluing together in tightly kerned resume templates
            page_text = page.extract_text(x_tolerance=1.5)
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


if __name__ == "__main__":
    # Smoke test - point this at a sample resume in test_resumes/
    sample = "test_resumes/sample_resume_1.pdf"
    print(parse_pdf(sample)[:500])
