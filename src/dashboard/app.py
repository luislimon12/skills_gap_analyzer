"""Streamlit entry point for the Job Posting Skills Gap Analyzer."""

import streamlit as st

from src.dashboard.components.select_role import render_role_selector
from src.dashboard.components.show_gaps import render_gap_table
from src.dashboard.components.show_recommendations import render_recommendations
from src.dashboard.components.upload_resume import render_upload
from src.dashboard.utils import load_role_postings, load_settings, resolve_database_path
from src.pipeline.run_pipeline import run as run_postings_pipeline
from src.scoring.analyze import analyze_role
from src.scoring.text_similarity import search_postings

st.set_page_config(page_title="Job Posting Skills Gap Analyzer", layout="wide")

st.title("Job Posting Skills Gap Analyzer")
st.write(
    "Upload your resume, pick a target role, and see which in-demand skills "
    "you're missing."
)

try:
    settings = load_settings()
except (OSError, ValueError) as error:
    st.error(f"Dashboard settings could not be loaded: {error}")
    st.stop()

database_path = resolve_database_path(settings)

with st.sidebar:
    st.header("Your analysis")
    role = render_role_selector(settings["target_roles"])
    resume_text = render_upload()
    fetch_postings = st.button("Fetch latest postings for this role", type="primary")
    st.caption("Resume text is processed in memory and is not saved to the postings database.")

if fetch_postings:
    with st.spinner(f"Fetching postings for {role}..."):
        summary = run_postings_pipeline(roles=[role], db_path=database_path)
    st.success(
        f"Fetched {summary['fetched']} postings; {summary['unique']} unique, "
        f"{summary['written']} new or upgraded in the database."
    )
    for error in summary["errors"]:
        st.warning(error)
    st.rerun()

postings = load_role_postings(role, database_path)
st.subheader(f"Job postings for {role}")
if not postings:
    st.info(
        "No postings are stored for this role yet. Fetch postings above, and configure "
        "Adzuna credentials or Greenhouse/Lever boards if the result is empty."
    )
else:
    st.metric("Postings in the sample", len(postings))
    st.dataframe(
        [
            {
                "Title": posting["title"],
                "Company": posting["company"],
                "Source": posting["source"],
                "Posted": posting["posted_date"],
                "Location": posting["location"],
                "URL": posting["url"],
                "Description is excerpted": posting["is_truncated"],
            }
            for posting in postings
        ],
        use_container_width=True,
        hide_index=True,
    )

if resume_text:
    st.subheader("Resume analysis")
    st.caption(f"Parsed {len(resume_text):,} characters of clean text.")
    with st.expander("Preview parsed resume text"):
        st.text(resume_text)

    if postings:
        analysis = analyze_role(resume_text, postings)
        fit_col, sample_col, skills_col = st.columns(3)
        fit_col.metric("Role fit", f"{analysis['fit_score']:.0f}%")
        sample_col.metric("Posting sample", analysis["postings_count"])
        skills_col.metric("Resume skills matched", len(analysis["resume_skills"]))

        st.caption(
            "Fit is cosine similarity over the skills observed in this posting sample. "
            "A small or biased sample may not represent the wider job market."
        )
        if analysis["resume_skills"]:
            st.write("**Matched resume skills:** " + ", ".join(analysis["resume_skills"]))
        else:
            st.warning(
                "No starter-taxonomy skills matched this resume. Results may be incomplete "
                "until a fuller Lightcast taxonomy is configured."
            )

        st.subheader("Skill demand and gaps")
        if analysis["skills"]:
            render_gap_table(analysis["skills"])
            if analysis["gaps"]:
                render_recommendations(analysis["gaps"])
            else:
                st.success("Every skill detected in this posting sample also appears in the resume.")
        else:
            st.info("No taxonomy skills were detected in the current posting sample.")

        related_postings = search_postings(
            resume_text,
            postings,
            keywords=analysis["resume_skills"],
            limit=3,
        )
        with st.expander("Related job descriptions (experimental text similarity)"):
            st.caption(
                "TF-IDF similarity and exact keyword hits help browse descriptions; "
                "they are not verified skill matches and do not affect FitScore."
            )
            for result in related_postings:
                posting = result["posting"]
                st.markdown(
                    f"**{posting['title']} — {posting['company']}** "
                    f"(text similarity {result['similarity']:.0%})"
                )
                if result["keyword_matches"]:
                    st.caption("Keyword hits: " + ", ".join(result["keyword_matches"]))
                description = posting.get("full_text") or ""
                if description:
                    st.write(description[:500] + ("…" if len(description) > 500 else ""))
