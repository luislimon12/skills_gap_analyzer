"""Streamlit entry point for the Job Posting Skills Gap Analyzer."""

import sys
from pathlib import Path

# `streamlit run src/dashboard/app.py` puts src/dashboard on sys.path, not the project root.
PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from src.dashboard.components.select_role import render_role_selector  # noqa: E402
from src.dashboard.components.show_baseline import render_onet_baseline  # noqa: E402
from src.dashboard.components.show_phrases import render_discovered_phrases  # noqa: E402
from src.dashboard.components.show_gaps import render_gap_table  # noqa: E402
from src.dashboard.components.show_recommendations import render_recommendations  # noqa: E402
from src.dashboard.components.skill_explorer import render_skill_explorer  # noqa: E402
from src.dashboard.components.upload_resume import render_upload  # noqa: E402
from src.dashboard.utils import (  # noqa: E402
    analysis_rows_for_export,
    load_role_postings,
    load_settings,
    resolve_database_path,
    salary_summary,
    similar_stored_roles,
)
from src.pipeline.fetch_postings import adzuna_keys_configured  # noqa: E402
from src.pipeline.run_pipeline import load_config as load_pipeline_config  # noqa: E402
from src.pipeline.run_pipeline import run as run_postings_pipeline  # noqa: E402
from src.pipeline.store_postings import load_latest_market_snapshot, load_postings  # noqa: E402
from src.resume.extract_sections import skill_evidence_text  # noqa: E402
from src.scoring.analyze import analyze_role, resume_skill_names  # noqa: E402
from src.scoring.onet_baseline import occupation_choices, occupation_codes_for_role, onet_baseline  # noqa: E402
from src.scoring.phrase_discovery import discover_phrases  # noqa: E402
from src.scoring.skill_matcher import SkillMatcher  # noqa: E402
from src.scoring.text_similarity import search_postings  # noqa: E402

# The everyday controls live on the page itself; the sidebar only holds settings
# for whoever runs this copy of the app, so it starts closed (it covers the page on phones).
st.set_page_config(page_title="Job Posting Skills Gap Analyzer", layout="wide", initial_sidebar_state="collapsed")


@st.cache_resource
def get_matcher() -> SkillMatcher:
    return SkillMatcher()


@st.cache_data(show_spinner="Comparing your resume with the postings...")
def cached_analysis(resume_text: str, postings: list[dict]) -> dict:
    return analyze_role(resume_text, postings)


@st.cache_data(show_spinner=False)
def cached_phrases(resume_text: str, postings: list[dict], role: str, background: list[dict]) -> list[dict]:
    return discover_phrases(postings, get_matcher(), resume_text=resume_text, role=role, background=background)


def money(value: float | None, currency: str = "$") -> str:
    return f"{currency}{value:,.0f}" if value else "n/a"


def render_occupation_baseline(role: str, resume_text: str, resume_skills: list[str]) -> None:
    """O*NET's reference list for the closest official occupation(s), with resume coverage."""
    default_codes = occupation_codes_for_role(role, settings.get("onet_occupations"))
    all_occupations = occupation_choices()
    soc_codes = st.multiselect(
        "Closest official occupations",
        options=list(all_occupations),
        default=[code for code in default_codes if code in all_occupations],
        format_func=lambda code: all_occupations.get(code, code),
        key=f"occupations_{role}",
        help="Matched automatically from O*NET's ~60,000 job titles. Add or remove occupations to adjust.",
    )
    if not default_codes:
        st.caption("No official occupation matched this job title automatically; search the list above to pick one.")
    evidence_text = skill_evidence_text(resume_text)  # hobbies and references aren't job skills
    render_onet_baseline(
        onet_baseline(soc_codes, resume_skills, resume_text=evidence_text, kind="technology"),
        onet_baseline(soc_codes, resume_skills, resume_text=evidence_text, kind="tool"),
        has_resume=bool(resume_text),
    )


st.title("Job Posting Skills Gap Analyzer")
st.write(
    "Pick the job you want and add your resume. You'll see which skills employers ask for "
    "in real job postings, which ones you already have, and which jobs fit you best. "
    "Works for any job: tech, healthcare, trades, retail, logistics, office, and more."
)

try:
    settings = load_settings()
    pipeline_config = load_pipeline_config()
except (OSError, ValueError) as error:
    st.error(f"Dashboard settings could not be loaded: {error}")
    st.stop()

database_path = resolve_database_path(settings)
adzuna_cfg = pipeline_config.get("adzuna") or {}
can_fetch = adzuna_keys_configured() or bool(
    pipeline_config.get("greenhouse_boards") or pipeline_config.get("lever_boards")
)

with st.sidebar:
    st.header("Settings")
    st.caption("For whoever runs this copy of the app.")
    if adzuna_keys_configured():
        st.caption(f"Live job search: on (Adzuna, country {adzuna_cfg.get('country', 'ca').upper()}).")
    else:
        st.caption(
            "Live job search: off. Add ADZUNA_APP_ID and ADZUNA_APP_KEY to a .env file "
            "(see .env.example) to search Adzuna."
        )
    max_pages = st.slider(
        "Pages of postings per search (50 postings and 1 Adzuna API call per page)",
        min_value=1, max_value=10, value=int(adzuna_cfg.get("max_pages_per_role", 5)),
    )

role_col, resume_col = st.columns(2)
with role_col:
    role = render_role_selector(settings["target_roles"])
with resume_col:
    resume_text = render_upload()
st.caption("Your resume is only read in memory to compare it with the postings. It is never saved.")

fetch_postings = can_fetch and st.button(f"Get the latest {role} postings", type="primary")
if fetch_postings:
    with st.spinner(f"Searching for {role} postings..."):
        summary = run_postings_pipeline(roles=[role], max_pages=max_pages, db_path=database_path)
    st.session_state["fetch_summary"] = (role, summary)
    cached_analysis.clear()
    st.rerun()

fetched = st.session_state.pop("fetch_summary", None)
if fetched:
    fetched_role, summary = fetched
    if summary["fetched"]:
        st.success(f"Found {summary['fetched']} {fetched_role} postings ({summary['written']} new).")
    elif not summary["errors"]:
        st.warning(f"No postings were found for “{fetched_role}”. Try a broader or more common job title.")
    for error in summary["errors"]:
        st.warning(f"A job board couldn't be reached, so results may be incomplete ({error}).")

postings = load_role_postings(role, database_path)
resume_text = resume_text or ""
matcher = get_matcher()
resume_skills = resume_skill_names(resume_text, matcher)

if not postings:
    message = f"There are no job postings for **{role}** yet."
    if can_fetch:
        message += f" Click **Get the latest {role} postings** above to search for them."
    else:
        message += " Live job search isn't set up on this copy of the app, so only jobs with saved postings can be analyzed."
    st.info(message)
    suggestions = similar_stored_roles(role, database_path)
    if suggestions:
        st.write(
            "Saved postings are available for: "
            + ", ".join(f"**{name}** ({count})" for name, count in suggestions)
            + ". Pick one from the job list to see its results."
        )
    if resume_skills:
        st.write("**Skills found on your resume:** " + ", ".join(resume_skills))
    st.subheader(f"What a {role} typically uses")
    st.caption("A general reference list from O*NET, the U.S. occupation database, while there are no postings to analyze.")
    render_occupation_baseline(role, resume_text, resume_skills)
    st.stop()

market = load_latest_market_snapshot(role, database_path)
salaries = salary_summary(postings)

st.subheader(f"Job market: {role}")
sample_col, live_col, salary_col, mean_col = st.columns(4)
sample_col.metric("Postings analyzed", len(postings))
live_col.metric(
    "Open listings on Adzuna",
    f"{market['total_count']:,}" if market and market.get("total_count") is not None else "n/a",
    help="Adzuna's total match count for this search at the last fetch.",
)
salary_col.metric(
    "Typical advertised salary",
    money(salaries["median"]) if salaries else "n/a",
    help=(
        f"The middle value across {salaries['count']} postings that list a salary "
        f"(half pay between {money(salaries['p25'])} and {money(salaries['p75'])}); "
        f"{salaries['predicted_share']:.0%} of these are Adzuna estimates."
        if salaries else "None of these postings list a salary."
    ),
)
mean_col.metric(
    "Average salary (all Adzuna listings)",
    money(market.get("mean_salary")) if market else "n/a",
    help="Adzuna's average advertised salary for this search at the last fetch.",
)
if len(postings) < 20:
    st.warning(
        f"Only {len(postings)} postings so far, so the percentages below are rough. "
        "Get more postings or try a broader job title."
    )
with st.expander(f"Browse the {len(postings)} postings"):
    st.dataframe(
        pd.DataFrame(
            {
                "Title": posting["title"],
                "Open": posting["url"],
                "Company": posting["company"],
                "Location": posting["location"],
                "Posted": posting["posted_date"],
                "Salary min": posting.get("salary_min"),
                "Salary max": posting.get("salary_max"),
                "Source": posting["source"],
            }
            for posting in postings
        ),
        width="stretch",
        hide_index=True,
        column_config={"Open": st.column_config.LinkColumn("Open", display_text="Open")},
    )
    if any(posting["is_truncated"] for posting in postings):
        st.caption(
            "Adzuna only shares the start of each job description, so a skill mentioned later in a "
            "posting can be missed. Real demand may be a little higher than shown."
        )

if resume_text:
    st.subheader("Your resume")
    if resume_skills:
        st.write("**Skills found on your resume:** " + ", ".join(resume_skills))
    else:
        st.warning(
            "We couldn't find any known skills on this resume. Open the preview below to check the "
            "text came through; scanned PDFs often can't be read."
        )
    with st.expander(f"Preview the text we read from your resume ({len(resume_text):,} characters)"):
        st.text(resume_text)
else:
    st.info("Add your resume above to see how well you match and which skills to learn next. "
            "Until then, the sections below show what this job asks for.")

# Big labelled buttons instead of tabs: people didn't notice tab labels were clickable.
SECTIONS = {
    "skills": (":material/bar_chart: Skills employers ask for",
               "Which skills these postings want, what to learn next, and jobs that match your skills."),
    "phrases": (":material/forum: Other things postings mention",
                "Phrases that keep coming up in the postings but aren't in our skill list, like shift types or equipment."),
    "occupation": (":material/work: Occupation reference (O*NET)",
                   "The software and tools this kind of job typically uses, from the official U.S. occupation database."),
    "similar": (":material/description: Postings most like your resume",
                "The postings whose wording overlaps most with your resume."),
}
st.markdown("### Choose what to look at")
section = st.segmented_control(
    "Choose what to look at", list(SECTIONS), default="skills", key="section",
    format_func=lambda key: SECTIONS[key][0], label_visibility="collapsed", width="stretch",
) or "skills"  # clicking the selected button again deselects it; keep showing something
st.caption(SECTIONS[section][1])

if section == "skills":
    analysis = cached_analysis(resume_text, postings)
    if resume_text:
        fit_col, matched_col, gaps_col = st.columns(3)
        fit_col.metric(
            "How well you match", f"{analysis['fit_score']:.0f}%",
            help="Cosine similarity between your skills and recency/requirement-weighted posting demand.",
        )
        matched_col.metric(
            "Asked-for skills you have",
            f"{sum(row['has_skill'] for row in analysis['skills'])} / {len(analysis['skills'])}",
        )
        gaps_col.metric("Skills to work on", len(analysis["gaps"]))
        st.caption(
            "Your match is higher when you have the skills that the most postings require. "
            "It's based on these postings only, so treat it as a rough guide."
        )

    if analysis["skills"]:
        if resume_text and analysis["gaps"]:
            render_recommendations(analysis["gaps"])
        elif resume_text:
            st.success("You already have every skill these postings ask for.")
        st.markdown("#### Explore skills and find matching postings")
        render_skill_explorer(
            analysis["skills"], postings, resume_skills, key=f"{role}:{'|'.join(resume_skills)}",
            matcher=matcher,
        )
        with st.expander("All skills found in the postings (table and download)"):
            render_gap_table(analysis["skills"])
            st.download_button(
                "Download results as CSV",
                pd.DataFrame(analysis_rows_for_export(analysis["skills"])).to_csv(index=False),
                file_name=f"skills_gap_{role.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )
    else:
        st.info("No known skills were found in these postings. "
                "Click **Other things postings mention** above for phrases that come up often.")

elif section == "phrases":
    # Other roles' postings show which phrases are job-ad filler rather than this role's requirements.
    background = [p for p in load_postings(db_path=database_path) if p["role"] != role]
    render_discovered_phrases(
        cached_phrases(resume_text, postings, role, background), has_resume=bool(resume_text)
    )

elif section == "occupation":
    render_occupation_baseline(role, resume_text, resume_skills)

elif section == "similar":
    if not resume_text:
        st.info("Add your resume to see the postings whose wording is closest to it.")
    else:
        st.caption(
            "Ranked by how much each posting's wording overlaps with your resume (TF-IDF similarity). "
            "This is a browsing aid; it doesn't change your match score."
        )
        for result in search_postings(resume_text, postings, keywords=resume_skills, limit=5):
            posting = result["posting"]
            link = f" · [open]({posting['url']})" if posting.get("url") else ""
            st.markdown(
                f"**{posting['title']} — {posting['company']}** "
                f"({result['similarity']:.0%} similar wording){link}"
            )
            if result["keyword_matches"]:
                st.caption("Mentions your skills: " + ", ".join(result["keyword_matches"]))
            description = posting.get("full_text") or ""
            if description:
                st.write(description[:500] + ("…" if len(description) > 500 else ""))

st.divider()
st.caption(
    "Job postings: [Jobs by Adzuna](https://www.adzuna.ca) and configured Greenhouse/Lever boards. "
    "Occupation data (technologies, tools, job titles) includes information from the "
    "[O*NET 30.0 Database](https://www.onetcenter.org/database.html) by the U.S. Department of Labor, "
    "Employment and Training Administration (USDOL/ETA), used under the "
    "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. "
    "Results are estimates from a posting sample and are not career advice."
)
