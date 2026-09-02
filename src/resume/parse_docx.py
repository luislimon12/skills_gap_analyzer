"""
Role 2 - Component D: extract text from an uploaded DOCX resume.

Week 1 goal: get raw text out of a DOCX. Splitting it into sections
happens in extract_sections.py, week 2.
"""

import docx


def parse_docx(file_path: str) -> str:
    """Return all paragraph text from a DOCX resume as one string."""
    document = docx.Document(file_path)
    return "\n".join(p.text for p in document.paragraphs if p.text.strip())


if __name__ == "__main__":
    # Week 1 smoke test - point this at a sample resume in test_resumes/
    sample = "test_resumes/sample_resume_2.docx"
    print(parse_docx(sample)[:500])
