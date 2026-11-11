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
from src.dashboard.components.show_gaps import render_demand_chart, render_gap_table  # noqa: E402
from src.dashboard.components.show_recommendations import render_recommendations  # noqa: E402
from src.dashboard.components.upload_resume import render_upload  # noqa: E402
from src.dashboard.utils import (  # noqa: E402
    analysis_rows_for_export,
    load_role_postings,
    load_settings,
    resolve_database_path,
    salary_summary,
)
from src.pipeline.fetch_postings import (  # noqa: E402
    adzuna_keys_configured,
    usajobs_credentials_configured,
)
from src.pipeline.geography import normalize_geography  # noqa: E402
from src.pipeline.run_pipeline import load_config as load_pipeline_config  # noqa: E402
from src.pipeline.run_pipeline import run as run_postings_pipeline  # noqa: E402
from src.pipeline.store_postings import load_latest_market_snapshot  # noqa: E402
from src.scoring.analyze import analyze_role  # noqa: E402
from src.scoring.onet_baseline import occupation_choices, occupation_codes_for_role, onet_baseline  # noqa: E402
from src.scoring.phrase_discovery import (  # noqa: E402
    PHRASE_DISCOVERY_CACHE_VERSION,
    discover_phrases,
)
from src.scoring.skill_matcher import SkillMatcher  # noqa: E402
from src.scoring.text_similarity import rank_postings_by_skills, search_postings  # noqa: E402

st.set_page_config(page_title="Job Posting Skills Gap Analyzer", layout="wide")

dark_mode = st.toggle("Dark mode", key="dark_mode_toggle")
if dark_mode:
    page_background = "#0e1117"
    sidebar_background = "#161b22"
    text_color = "#f0f2f6"
    muted_text_color = "#b8c0cc"
    control_background = "#262b36"
    border_color = "#3b4350"
else:
    page_background = "#ffffff"
    sidebar_background = "#f0f2f6"
    text_color = "#262730"
    muted_text_color = "#626a78"
    control_background = "#ffffff"
    border_color = "#d6d9df"

st.markdown(
    f"""
    <style>
    :root {{ color-scheme: {"dark" if dark_mode else "light"}; }}
    [data-testid="stAppViewContainer"],
    [data-testid="stMain"] {{
        background-color: {page_background};
        color: {text_color};
    }}
    [data-testid="stHeader"] {{ background-color: {page_background}; }}
    [data-testid="stSidebar"] {{ background-color: {sidebar_background}; }}
    .stApp h1, .stApp h2, .stApp h3, .stApp h4, .stApp p,
    .stApp label, [data-testid="stCaptionContainer"] {{
        color: {text_color};
    }}
    [data-testid="stCaptionContainer"] {{ color: {muted_text_color}; }}
    [data-baseweb="input"] > div,
    [data-baseweb="select"] > div,
    [data-testid="stTextInput"] input,
    [data-testid="stNumberInput"] input,
    [data-testid="stTextArea"] textarea {{
        background-color: {control_background};
        color: {text_color};
        border-color: {border_color};
    }}
    [data-testid="stDataFrame"], [data-testid="stTable"] {{
        border-color: {border_color};
    }}
    </style>
    """,
    unsafe_allow_html=True,
)


@st.cache_resource
def get_matcher() -> SkillMatcher:
    return SkillMatcher()


@st.cache_data(show_spinner="Scoring your resume against the postings...")
def cached_analysis(resume_text: str, postings: list[dict]) -> dict:
    return analyze_role(resume_text, postings)


@st.cache_data(show_spinner=False)
def cached_phrases(
    resume_text: str,
    postings: list[dict],
    role: str,
    cache_version: int,
) -> list[dict]:
    """Cache phrases by filter version so old exclusion results are not reused."""
    return discover_phrases(postings, get_matcher(), resume_text=resume_text, role=role)


@st.cache_data(show_spinner="Finding matching job postings...")
def cached_skill_postings(
    postings: list[dict],
    include_skills: tuple[str, ...],
    exclude_skills: tuple[str, ...],
    skill_weights: tuple[tuple[str, float], ...],
) -> list[dict]:
    """Cache each explicit skill-search request by postings and selected filters."""
    return rank_postings_by_skills(
        postings,
        list(include_skills),
        exclude_skills=list(exclude_skills),
        skill_weights=dict(skill_weights),
        matcher=get_matcher(),
    )


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
    if usajobs_credentials_configured():
        st.caption("USAJOBS API credentials found in .env.")
    else:
        st.info(
            "To fetch U.S. federal jobs, get an API key from "
            "[USAJOBS Developer](https://developer.usajobs.gov/) and set "
            "`USAJOBS_API_KEY` and `USAJOBS_USER_AGENT` in `.env`."
        )
    if (pipeline_config.get("canada_job_bank") or {}).get("enabled", True):
        st.caption(
            "Canada Job Bank's monthly open-data CSV is enabled "
            "(not a real-time feed; descriptions are not included)."
        )
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
for posting in postings:
    if not posting.get("country") or not posting.get("region"):
        country_hint = (
            adzuna_cfg.get("country", "ca")
            if posting.get("source") == "adzuna"
            else "us" if posting.get("source") == "usajobs"
            else posting.get("country")
        )
        country, region = normalize_geography(
            posting.get("location") or "",
            country_hint=country_hint,
            region_hint=posting.get("region"),
        )
        posting["country"] = posting.get("country") or country
        posting["region"] = posting.get("region") or region

all_postings_count = len(postings)
country_values = sorted({p["country"] for p in postings if p.get("country")})
country_options = ["All countries"]
if any(not p.get("country") for p in postings):
    country_options.append("Unknown")
country_options.extend(country_values)
selected_country = st.selectbox(
    "Country",
    country_options,
    key=f"posting_country_{role}",
)
country_postings = postings
if selected_country == "Unknown":
    country_postings = [p for p in postings if not p.get("country")]
elif selected_country != "All countries":
    country_postings = [p for p in postings if p.get("country") == selected_country]
region_values = sorted({p["region"] for p in country_postings if p.get("region")})
region_options = ["All provinces/states"]
if any(not p.get("region") for p in country_postings):
    region_options.append("Unknown")
region_options.extend(region_values)
selected_region = st.selectbox(
    "Province / state",
    region_options,
    key=f"posting_region_{role}",
)
if selected_country != "All countries":
    if selected_country == "Unknown":
        postings = [p for p in postings if not p.get("country")]
    else:
        postings = [p for p in postings if p.get("country") == selected_country]
if selected_region != "All provinces/states":
    if selected_region == "Unknown":
        postings = [p for p in postings if not p.get("region")]
    else:
        postings = [p for p in postings if p.get("region") == selected_region]

market = load_latest_market_snapshot(role, database_path)
salaries = salary_summary(postings)

st.subheader(f"Job market sample: {role}")
if len(postings) != all_postings_count:
    st.caption(f"Showing {len(postings)} of {all_postings_count} postings after location filters.")
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
                    "Country": posting.get("country") or "Unknown",
                    "Province / state": posting.get("region") or "Unknown",
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
        limited_sources = {
            posting["source"] for posting in postings if posting["is_truncated"]
        }
        limitations = []
        if "adzuna" in limited_sources:
            limitations.append("Adzuna returns description excerpts")
        if "canada_job_bank" in limited_sources:
            limitations.append("the monthly Job Bank CSV has structured fields but no full descriptions")
        st.caption(
            f"{'; '.join(limitations)}. Skills mentioned only in missing description text "
            "may not be detected, so demand from those sources is a lower bound."
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
resume_skills = [skill.name for skill in matcher.match(resume_text)] if resume_text else []

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

selected_view = st.segmented_control(
    "Choose a results section",
    [
        "Posting-based skills",
        "Other recurring requirements",
        "O*NET occupation baseline",
        "Related postings",
    ],
    default="Posting-based skills",
    selection_mode="single",
    label_visibility="visible",
)

if selected_view == "Posting-based skills":
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
            st.markdown("#### Most-demanded skills")
            chart_reset_key = f"demand_chart_reset_{role}"
            if chart_reset_key not in st.session_state:
                st.session_state[chart_reset_key] = 0
            selected_skills = render_demand_chart(
                analysis["skills"],
                chart_key=f"demand_skill_chart_{st.session_state[chart_reset_key]}",
            )
            st.markdown("#### Find postings by skills")
            skill_names = [row["skill"] for row in analysis["skills"]]
            excluded_skills = st.multiselect(
                "Avoid skills",
                options=skill_names,
                key=f"excluded_posting_skills_{role}",
                help="Postings mentioning any selected skill are excluded.",
            )
            reset_col, refresh_col, selected_col = st.columns([1, 1.5, 3])
            if reset_col.button("Clear chart selection", key=f"clear_skill_chart_{role}"):
                st.session_state[chart_reset_key] += 1
                st.rerun()
            search_state_key = f"skill_posting_search_{role}"
            refresh_search = refresh_col.button(
                "Refresh matching postings",
                key=f"refresh_matching_postings_{role}",
                type="primary",
                disabled=not selected_skills,
                help="Re-run the posting search with the current selected and avoided skills.",
            )
            selected_col.caption(
                "Selected skills: " + (", ".join(selected_skills) if selected_skills else "none")
            )
            if refresh_search and selected_skills:
                include_snapshot = tuple(selected_skills)
                exclude_snapshot = tuple(excluded_skills)
                weights_snapshot = tuple(
                    sorted(
                        (row["skill"].casefold(), row["demand"])
                        for row in analysis["skills"]
                    )
                )
                matching_postings = cached_skill_postings(
                    postings,
                    include_snapshot,
                    exclude_snapshot,
                    weights_snapshot,
                )
                st.session_state[search_state_key] = {
                    "include": include_snapshot,
                    "exclude": exclude_snapshot,
                    "country": selected_country,
                    "region": selected_region,
                    "results": matching_postings,
                }

            search_state = st.session_state.get(search_state_key)
            if search_state:
                current_include = tuple(selected_skills)
                current_exclude = tuple(excluded_skills)
                if (
                    current_include != search_state["include"]
                    or current_exclude != search_state["exclude"]
                    or selected_country != search_state.get("country", "All countries")
                    or selected_region != search_state.get("region", "All provinces/states")
                ):
                    st.info(
                        "Your skill selections changed. Click **Refresh matching postings** "
                        "to update; the previous results remain visible until then."
                    )
                matching_postings = search_state["results"]
                st.markdown("#### Matching job postings")
                st.caption(
                    f"{len(matching_postings)} postings match at least one selected skill"
                    + (
                        f", excluding those with {', '.join(search_state['exclude'])}."
                        if search_state["exclude"] else "."
                    )
                    + " Current search: "
                    + ", ".join(search_state["include"])
                )
                if not matching_postings:
                    st.info("No postings match the selected skills and avoid list.")
                else:
                    for result in matching_postings[:50]:
                        posting = result["posting"]
                        st.markdown(
                            f"**{posting.get('title') or 'Untitled role'} — "
                            f"{posting.get('company') or 'Unknown company'}**"
                        )
                        st.caption(
                            f"Matched {result['matched_count']} of {result['total_skills']} selected skills "
                            f"({result['weighted_score']:.0%} weighted score): "
                            f"{', '.join(result['matched_skills'])} · {posting.get('location') or 'Location not listed'}"
                        )
                        if posting.get("url"):
                            st.link_button("Open posting", posting["url"])
                    if len(matching_postings) > 50:
                        st.caption(f"Showing the top 50 of {len(matching_postings)} matching postings.")
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

elif selected_view == "Other recurring requirements":
    if not postings:
        st.info("Fetch postings for this role to find recurring requirements.")
    else:
        render_discovered_phrases(
            cached_phrases(
                resume_text, postings, role, PHRASE_DISCOVERY_CACHE_VERSION
            ),
            has_resume=bool(resume_text),
        )

elif selected_view == "O*NET occupation baseline":
    render_onet_baseline(
        onet_baseline(soc_codes, resume_skills, resume_text=resume_text, kind="technology"),
        onet_baseline(soc_codes, resume_skills, resume_text=resume_text, kind="tool"),
        has_resume=bool(resume_text),
    )

elif selected_view == "Related postings":
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
