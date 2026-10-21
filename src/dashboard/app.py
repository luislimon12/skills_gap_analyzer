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

st.set_page_config(page_title="Job Posting Skills Gap Analyzer", layout="wide")


@st.cache_resource
def get_matcher() -> SkillMatcher:
    return SkillMatcher()


@st.cache_data(show_spinner="Scoring your resume against the postings...")
def cached_analysis(resume_text: str, postings: list[dict]) -> dict:
    return analyze_role(resume_text, postings)


@st.cache_data(show_spinner=False)
def cached_phrases(resume_text: str, postings: list[dict], role: str, background: list[dict]) -> list[dict]:
    return discover_phrases(postings, get_matcher(), resume_text=resume_text, role=role, background=background)


def money(value: float | None, currency: str = "$") -> str:
    return f"{currency}{value:,.0f}" if value else "n/a"


st.title("Job Posting Skills Gap Analyzer")
st.write(
    "Upload your resume, pick a target role, and see which in-demand skills "
    "you're missing, based on live job postings. Works for any job: tech, healthcare, "
    "trades, retail, logistics, office, and more."
)

try:
    settings = load_settings()
    pipeline_config = load_pipeline_config()
except (OSError, ValueError) as error:
    st.error(f"Dashboard settings could not be loaded: {error}")
    st.stop()

database_path = resolve_database_path(settings)
adzuna_cfg = pipeline_config.get("adzuna") or {}

with st.sidebar:
    st.header("Your analysis")
    role = render_role_selector(settings["target_roles"])
    resume_text = render_upload()

    st.divider()
    st.subheader("Job postings")
    if adzuna_keys_configured():
        st.caption(f"Adzuna keys found in .env (country: {adzuna_cfg.get('country', 'ca').upper()}).")
    else:
        st.warning("Adzuna keys are not set. Copy .env.example to .env and add ADZUNA_APP_ID / ADZUNA_APP_KEY.")
    max_pages = st.slider(
        "Adzuna pages to fetch (50 postings per page, 1 API call each)",
        min_value=1, max_value=10, value=int(adzuna_cfg.get("max_pages_per_role", 5)),
    )
    fetch_postings = st.button("Fetch latest postings for this role", type="primary")
    st.caption("Resume text is processed in memory and is not saved to the postings database.")

if fetch_postings:
    with st.spinner(f"Fetching postings for {role}..."):
        summary = run_postings_pipeline(roles=[role], max_pages=max_pages, db_path=database_path)
    st.session_state["fetch_summary"] = summary
    cached_analysis.clear()
    st.rerun()

summary = st.session_state.pop("fetch_summary", None)
if summary:
    st.success(
        f"Fetched {summary['fetched']} postings; {summary['unique']} unique, "
        f"{summary['written']} new or upgraded in the database."
    )
    for error in summary["errors"]:
        st.warning(error)

postings = load_role_postings(role, database_path)
market = load_latest_market_snapshot(role, database_path)
salaries = salary_summary(postings)

st.subheader(f"Job market sample: {role}")
sample_col, live_col, salary_col, mean_col = st.columns(4)
sample_col.metric("Postings in the sample", len(postings))
live_col.metric(
    "Live Adzuna listings",
    f"{market['total_count']:,}" if market and market.get("total_count") is not None else "n/a",
    help="Adzuna's total match count for this search at the last fetch.",
)
salary_col.metric(
    "Median advertised salary (sample)",
    money(salaries["median"]) if salaries else "n/a",
    help=(
        f"Midpoint of each posting's range, {salaries['count']} postings with salary; "
        f"middle half {money(salaries['p25'])}–{money(salaries['p75'])}; "
        f"{salaries['predicted_share']:.0%} estimated by Adzuna."
        if salaries else "No postings in the sample list a salary."
    ),
)
mean_col.metric(
    "Adzuna mean salary (all listings)",
    money(market.get("mean_salary")) if market else "n/a",
    help="Adzuna's average advertised salary for this search at the last fetch.",
)

if not postings:
    st.info(
        "No postings are stored for this role yet. Use **Fetch latest postings** in the sidebar "
        "(Adzuna keys in .env, and/or Greenhouse/Lever boards in src/pipeline/config_postings.yaml)."
    )
else:
    if len(postings) < 20:
        st.warning(
            f"Only {len(postings)} postings in the sample: demand percentages have wide "
            "confidence intervals. Fetch more pages or try a broader role title."
        )
    with st.expander(f"Browse the {len(postings)} postings"):
        st.dataframe(
            pd.DataFrame(
                {
                    "Title": posting["title"],
                    "Company": posting["company"],
                    "Location": posting["location"],
                    "Posted": posting["posted_date"],
                    "Salary min": posting.get("salary_min"),
                    "Salary max": posting.get("salary_max"),
                    "Source": posting["source"],
                    "Excerpt only": posting["is_truncated"],
                    "URL": posting["url"],
                }
                for posting in postings
            ),
            width="stretch",
            hide_index=True,
            column_config={"URL": st.column_config.LinkColumn("URL", display_text="Open")},
        )
    if any(posting["is_truncated"] for posting in postings):
        st.caption(
            "Adzuna returns description excerpts, so a skill mentioned only later in a full "
            "posting is missed. Demand from Adzuna-only samples is a lower bound."
        )

st.subheader("Matching occupation")
default_codes = occupation_codes_for_role(role, settings.get("onet_occupations"))
all_occupations = occupation_choices()
soc_codes = st.multiselect(
    "O*NET occupations used for the occupation baseline",
    options=list(all_occupations),
    default=[code for code in default_codes if code in all_occupations],
    format_func=lambda code: f"{all_occupations.get(code, code)} ({code})",
    key=f"occupations_{role}",
    help="Picked automatically from O*NET's ~60,000 job titles. Add or remove occupations to adjust.",
)
if not default_codes:
    st.caption("No O*NET occupation matched this role title automatically; search the list above to pick one.")

resume_text = resume_text or ""
matcher = get_matcher()
resume_skills = resume_skill_names(resume_text, matcher)

st.subheader("Resume analysis" if resume_text else "Role analysis")
if resume_text:
    with st.expander(f"Preview parsed resume text ({len(resume_text):,} characters)"):
        st.text(resume_text)
    if resume_skills:
        st.write("**Skills recognized on your resume:** " + ", ".join(resume_skills))
    else:
        st.warning(
            "No taxonomy skills matched this resume. Check that the text preview above is readable; "
            "the recurring-requirements and occupation tabs still compare your resume text directly."
        )
else:
    st.info("Upload your resume (PDF, DOCX or TXT) in the sidebar to see your fit and skill gaps. "
            "Until then, the tabs below show what this role asks for.")

postings_tab, phrases_tab, baseline_tab, related_tab = st.tabs(
    ["Posting-based skills", "Other recurring requirements", "O*NET occupation baseline", "Related postings"]
)

with postings_tab:
    if not postings:
        st.info("Fetch postings for this role to score demand and gaps.")
    else:
        analysis = cached_analysis(resume_text, postings)
        if resume_text:
            fit_col, matched_col, gaps_col = st.columns(3)
            fit_col.metric("Role fit", f"{analysis['fit_score']:.0f}%")
            matched_col.metric(
                "In-demand skills you have",
                f"{sum(row['has_skill'] for row in analysis['skills'])} / {len(analysis['skills'])}",
            )
            gaps_col.metric("Skill gaps found", len(analysis["gaps"]))
            st.caption(
                "Fit is cosine similarity between your skills and recency/requirement-weighted "
                "demand over the skills seen in this sample. A small or biased sample may not "
                "represent the wider job market."
            )

        if analysis["skills"]:
            if resume_text and analysis["gaps"]:
                render_recommendations(analysis["gaps"])
            elif resume_text:
                st.success("Every skill detected in this posting sample also appears in your resume.")
            st.markdown("#### Explore skills and find matching postings")
            render_skill_explorer(
                analysis["skills"], postings, resume_skills, key=f"{role}:{'|'.join(resume_skills)}",
                matcher=matcher,
            )
            st.markdown("#### All detected skills")
            render_gap_table(analysis["skills"])
            st.download_button(
                "Download results as CSV",
                pd.DataFrame(analysis_rows_for_export(analysis["skills"])).to_csv(index=False),
                file_name=f"skills_gap_{role.lower().replace(' ', '_')}.csv",
                mime="text/csv",
            )
        else:
            st.info("No taxonomy skills were detected in the current posting sample. "
                    "See **Other recurring requirements** for phrases found in the postings.")

with phrases_tab:
    if not postings:
        st.info("Fetch postings for this role to find recurring requirements.")
    else:
        # Other roles' postings show which phrases are job-ad filler rather than this role's requirements.
        background = [p for p in load_postings(db_path=database_path) if p["role"] != role]
        render_discovered_phrases(
            cached_phrases(resume_text, postings, role, background), has_resume=bool(resume_text)
        )

with baseline_tab:
    evidence_text = skill_evidence_text(resume_text)  # hobbies and references aren't job skills
    render_onet_baseline(
        onet_baseline(soc_codes, resume_skills, resume_text=evidence_text, kind="technology"),
        onet_baseline(soc_codes, resume_skills, resume_text=evidence_text, kind="tool"),
        has_resume=bool(resume_text),
    )

with related_tab:
    if not postings:
        st.info("Fetch postings for this role to browse related descriptions.")
    elif not resume_text:
        st.info("Upload your resume to rank postings by similarity to it.")
    else:
        st.caption(
            "TF-IDF similarity and exact keyword hits help browse descriptions; "
            "they are not verified skill matches and do not affect FitScore."
        )
        for result in search_postings(resume_text, postings, keywords=resume_skills, limit=5):
            posting = result["posting"]
            link = f" · [open]({posting['url']})" if posting.get("url") else ""
            st.markdown(
                f"**{posting['title']} — {posting['company']}** "
                f"(text similarity {result['similarity']:.0%}){link}"
            )
            if result["keyword_matches"]:
                st.caption("Keyword hits: " + ", ".join(result["keyword_matches"]))
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
