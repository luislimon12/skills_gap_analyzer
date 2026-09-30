"""
Role 4 - Component H: Streamlit dashboard entry point.

Week 1 goal: hello world - prove the app runs and pages are wired up.
Real upload/scoring/results pages get filled in weeks 2-4.

Run with: streamlit run src/dashboard/app.py
"""

import streamlit as st

st.set_page_config(page_title="Job Posting Skills Gap Analyzer", layout="wide")

st.title("Job Posting Skills Gap Analyzer")
st.write(
    "Upload your resume, pick a target role, and see which in-demand skills "
    "you're missing."
)

st.info("Week 1: hello world. Upload + role selector + results come next.")

# Week 2+: wire these in
# from components.upload_resume import render_upload
# from components.select_role import render_role_selector
# from components.show_gaps import render_gap_table
# from components.show_recommendations import render_recommendations
