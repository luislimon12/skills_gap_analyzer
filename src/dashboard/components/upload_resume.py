"""Resume upload widget wired to the shared Role 2 parser."""

import streamlit as st

from src.resume.parse_resume import ResumeParseError, parse_resume


def render_upload() -> str | None:
    """Upload a PDF/DOCX resume and return its parsed plain text."""
    uploaded_file = st.file_uploader(
        "Upload your resume",
        type=["pdf", "docx"],
        help="Google Docs resumes can be downloaded as PDF or DOCX before uploading.",
    )
    if uploaded_file is None:
        return None

    try:
        return parse_resume(uploaded_file)
    except ResumeParseError as error:
        st.error(str(error))
        return None
