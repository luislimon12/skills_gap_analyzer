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
from src.dashboard.components.upload_resume import (  # noqa: E402
    STATE_KEY as RESUME_STATE_KEY,
    render_resume_controls,
    render_upload,
)
from src.dashboard.security import RateLimiter, client_ip, new_session_id  # noqa: E402
from src.dashboard.utils import (  # noqa: E402
    analysis_rows_for_export,
    database_job_counts,
    load_role_postings,
    load_settings,
    role_market_stats,
    salary_summary,
    salary_summaries_by_country,
    settings_engine,
)
from src.db.resumes_repo import save_analysis  # noqa: E402
from src.pipeline.geography import normalize_geography  # noqa: E402
from src.scoring.analyze import analyze_role  # noqa: E402
from src.scoring.onet_baseline import occupation_choices, occupation_codes_for_role, onet_baseline  # noqa: E402
from src.scoring.phrase_discovery import (  # noqa: E402
    PHRASE_DISCOVERY_CACHE_VERSION,
    discover_phrases,
)
from src.scoring.skill_matcher import SkillMatcher  # noqa: E402
from src.scoring.text_similarity import rank_postings_by_skills, search_postings  # noqa: E402
from src.worker.config import load_ingestion_config  # noqa: E402

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


@st.cache_resource
def get_rate_limiter() -> RateLimiter:
    return RateLimiter()


# Caches that take resume text expire after an hour and are cleared when a
# user deletes their resume, so resume content isn't kept in server memory.
@st.cache_data(show_spinner="Scoring your resume against the postings...", ttl=3600, max_entries=100)
def cached_analysis(resume_text: str, postings: list[dict]) -> dict:
    return analyze_role(resume_text, postings)


@st.cache_data(show_spinner=False, ttl=3600, max_entries=100)
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
    ingestion_config = load_ingestion_config()
    engine = settings_engine(settings)
except (OSError, ValueError) as error:
    st.error(f"Dashboard settings could not be loaded: {error}")
    st.stop()
except Exception:  # noqa: BLE001 - never show connection strings or stack traces to the public
    st.error("The job database is unavailable right now. Please try again in a few minutes.")
    st.stop()

if "anon_session_id" not in st.session_state:
    st.session_state["anon_session_id"] = new_session_id()
session_id = st.session_state["anon_session_id"]
try:
    visitor_ip = client_ip(st.context.headers)
except Exception:  # noqa: BLE001 - st.context is missing on older Streamlit versions
    visitor_ip = ""
limiter = get_rate_limiter()


def clear_resume_caches() -> None:
    cached_analysis.clear()
    cached_phrases.clear()
    cached_skill_postings.clear()


tracked_roles = ingestion_config["queries"] or settings["target_roles"]

with st.sidebar:
    st.header("Your analysis")
    role = render_role_selector(tracked_roles)
    resume_text = render_upload(
        engine=engine,
        resume_cfg=ingestion_config.get("resumes") or {},
        limiter=limiter,
        session_id=session_id,
        client_ip=visitor_ip,
        extract_skills=lambda text: [skill.name for skill in get_matcher().match(text)],
    )
    render_resume_controls(engine=engine, on_deleted=clear_resume_caches)

    st.divider()
    st.subheader("Job postings")
    st.caption(
        "Postings are collected automatically every hour from Jobs by Adzuna "
        "and kept up to date. Postings that are no longer listed are hidden."
    )

job_counts = database_job_counts(engine)
if sum(job_counts.values()) == 0:
    st.info(
        "No job postings have been collected yet. The data collector fills the database "
        "automatically in the background; please check back soon."
    )
    st.stop()

postings = load_role_postings(role, engine)
for posting in postings:
    if not posting.get("country") or not posting.get("region"):
        country_hint = posting.get("country_code") or posting.get("country")
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

market_rows = role_market_stats(role, engine)
market = None
if market_rows:
    counts = [row["total_count"] for row in market_rows if row["total_count"] is not None]
    means = [row["mean_salary"] for row in market_rows if row["mean_salary"]]
    market = {
        "total_count": sum(counts) if counts else None,
        # Averages from different countries use different currencies; only show a single-country mean.
        "mean_salary": means[0] if len(market_rows) == 1 and means else None,
    }
all_countries_selected = selected_country == "All countries"
salaries = None if all_countries_selected else salary_summary(postings)
country_salaries = salary_summaries_by_country(postings) if all_countries_selected else {}
has_country_salaries = any(summary is not None for summary in country_salaries.values())

st.subheader(f"Job market sample: {role}")
if len(postings) != all_postings_count:
    st.caption(f"Showing {len(postings)} of {all_postings_count} postings after location filters.")
sample_col, live_col, salary_col, mean_col = st.columns(4)
sample_col.metric("Postings in the sample", len(postings))
live_col.metric(
    "Live Adzuna listings",
    f"{market['total_count']:,}" if market and market.get("total_count") is not None else "n/a",
    help="Adzuna's total match count for this search at the last collection run.",
)
salary_col.metric(
    "Median advertised salary (sample)",
    (
        "See country summaries below"
        if all_countries_selected and has_country_salaries
        else money(salaries["median"]) if salaries else "n/a"
    ),
    help=(
        f"Midpoint of each posting's range, {salaries['count']} postings with salary; "
        f"middle half {money(salaries['p25'])}–{money(salaries['p75'])}; "
        f"{salaries['predicted_share']:.0%} marked as predicted by source."
        if salaries else (
            "Separate salary summaries are shown by country; no currencies are combined."
            if all_countries_selected and has_country_salaries
            else "No postings in the sample list a salary."
        )
    ),
)
mean_col.metric(
    "Adzuna mean salary (all listings)",
    money(market.get("mean_salary")) if market else "n/a",
    help="Adzuna's average advertised salary at the last collection run (shown for a single country only, since currencies differ).",
)
if all_countries_selected and has_country_salaries:
    salary_groups = [
        (country, summary)
        for country, summary in country_salaries.items()
        if summary is not None
    ]
    if salary_groups:
        country_salary_cols = st.columns(len(salary_groups))
        for column, (country, country_summary) in zip(country_salary_cols, salary_groups):
            currency = {
                "Canada": "CAD",
                "United States": "USD",
            }.get(country, "currency unknown")
            column.metric(
                f"{country} median ({currency})",
                money(country_summary["median"]),
                help=(
                    f"{country_summary['count']} postings with salary; "
                    f"middle half {money(country_summary['p25'])}–"
                    f"{money(country_summary['p75'])}; "
                    f"{country_summary['predicted_share']:.0%} marked as predicted by source."
                ),
            )

if not postings:
    st.info(
        f"No postings have been collected for {role} yet. The data collector adds postings "
        "for every tracked role in the background; please check back soon."
    )
else:
    if len(postings) < 20:
        st.warning(
            f"Only {len(postings)} postings in the sample: demand percentages have wide "
            "confidence intervals. More postings are collected every hour."
        )
    with st.expander(f"Browse the {len(postings)} postings"):
        show_unlisted = st.toggle(
            "Also show postings that are no longer listed",
            key=f"show_unlisted_{role}",
            help="Postings that disappeared from the job board or whose link no longer works. "
                 "They are never used in the analysis.",
        )
        browse = list(postings)
        if show_unlisted:
            listed_ids = {posting["id"] for posting in postings}
            browse += [
                posting for posting in load_role_postings(role, engine, include_closed=True)
                if posting["id"] not in listed_ids
                and (selected_country == "All countries"
                     or (posting.get("country") or "Unknown") == selected_country)
                and (selected_region == "All provinces/states"
                     or (posting.get("region") or "Unknown") == selected_region)
            ]
        st.dataframe(
            pd.DataFrame(
                {
                    "Title": posting["title"],
                    "Status": "No longer listed" if posting.get("no_longer_listed") else "Listed",
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
                for posting in browse
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
    st.info("Upload your resume (PDF or DOCX) in the sidebar to see your fit and skill gaps. "
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
        st.info("No postings have been collected for this role yet.")
    else:
        resume_record = st.session_state.get(RESUME_STATE_KEY)
        analysis_key = (
            resume_record["id"] if resume_record else "", role, selected_country, selected_region, len(postings),
        )
        seen_analyses = st.session_state.setdefault("analysis_keys", set())
        if resume_text and analysis_key not in seen_analyses:
            if not limiter.allow("analysis", f"ip:{visitor_ip}", f"session:{session_id}"):
                st.error("Too many analyses in a short time. Please wait a while and try again.")
                st.stop()
            seen_analyses.add(analysis_key)
        analysis = cached_analysis(resume_text, postings)
        if resume_text and resume_record and analysis_key not in resume_record["saved_analyses"]:
            try:
                with engine.begin() as conn:
                    save_analysis(
                        conn, resume_id=resume_record["id"], role=role,
                        fit_score=analysis["fit_score"], postings_count=len(postings),
                        skills=analysis["skills"],
                    )
                resume_record["saved_analyses"].add(analysis_key)
            except Exception:  # noqa: BLE001 - e.g. the resume was already deleted
                pass
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
        st.info("No postings have been collected for this role yet.")
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
        st.info("No postings have been collected for this role yet.")
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
    "Job postings: [Jobs by Adzuna](https://www.adzuna.ca), collected hourly. "
    "Occupation data (technologies, tools, job titles) includes information from the "
    "[O*NET 30.0 Database](https://www.onetcenter.org/database.html) by the U.S. Department of Labor, "
    "Employment and Training Administration (USDOL/ETA), used under the "
    "[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/) license. "
    "Results are estimates from a posting sample and are not career advice."
)
