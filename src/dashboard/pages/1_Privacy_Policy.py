"""Privacy policy page (production plan, section 12).

Review the wording against the laws that apply to your users before a public
launch (PIPEDA in Canada, U.S. state laws, GDPR if EU users sign up). Set the
contact address in config/settings.yaml (privacy_contact).
"""

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import streamlit as st  # noqa: E402

from src.dashboard.utils import load_settings  # noqa: E402
from src.worker.config import load_ingestion_config  # noqa: E402

st.set_page_config(page_title="Privacy policy - Skills Gap Analyzer", layout="centered")

try:
    settings = load_settings()
except (OSError, ValueError):
    settings = {}
resume_cfg = load_ingestion_config().get("resumes") or {}
retention_days = int(resume_cfg.get("retention_days", 30))
contact = settings.get("privacy_contact") or "the project team (contact address to be added)"
if resume_cfg.get("store_file", False):
    file_sentence = (
        "Your resume file is kept **encrypted**, under a random file name, so the results can be "
        "re-checked, and is deleted together with your skills and results."
    )
else:
    file_sentence = "Your resume file is read in memory to find skill names, then discarded."

st.title("Privacy policy")
st.caption("Last updated: November 2026")

st.markdown(f"""
### What this site does
You upload a resume, pick a target role, and the site compares the skills on
your resume with the skills asked for in current job postings.

### What we store
* **Your skills and results.** {file_sentence} We store the list of skills we
  found and your results (which in-demand skills you have and which you're
  missing). The resume text itself is never saved to the database.
* **An anonymous ID.** Stored data is linked to a random ID for your browser
  session, not to your name, email or account. We don't ask you to sign up.
* **No resume text in logs.** Server logs never contain resume text or personal
  details.
* **Job postings** are public listings from Jobs by Adzuna and are not personal data.

### How long we keep it
Your skills and results are deleted automatically **{retention_days} days** after
you upload. Backups are encrypted and do not include resume data.

### How to delete it sooner
Click **Delete my resume** in the sidebar after uploading. Your skills and
results are deleted immediately. If you closed the page, the data is still
deleted automatically after {retention_days} days. You can also contact us
(below) to ask about your data.

### Who can see it
Only the people running this project, for running and fixing the site. We don't
sell or share your data, and we don't use it to train AI models.

### Security
Traffic is encrypted (HTTPS). The server is not directly reachable from the
internet; requests pass through Cloudflare, which also filters abusive traffic.
The database and any stored files are encrypted at rest.

### Contact
Questions or requests: {contact}.
""")
