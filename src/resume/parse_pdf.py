"""
Role 2 - Component D: extract text from an uploaded PDF resume.

Week 1 goal: get raw text out of a PDF. Splitting it into sections
(education/experience/skills) happens in extract_sections.py, week 2.
"""

import pdfplumber


def parse_pdf(file_path: str) -> str:
    """Return all text from a PDF resume as one string."""
    text_parts = []
    with pdfplumber.open(file_path) as pdf:
        for page in pdf.pages:
            page_text = page.extract_text()
            if page_text:
                text_parts.append(page_text)
    return "\n".join(text_parts)


if __name__ == "__main__":
    # Week 1 smoke test - point this at a sample resume in test_resumes/
    sample = "test_resumes/sample_resume_1.pdf"
    print(parse_pdf(sample)[:500])
