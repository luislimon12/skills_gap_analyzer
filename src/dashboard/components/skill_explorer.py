"""Clickable demand chart: pick your skills, then find postings that lean on them."""

import altair as alt
import pandas as pd
import streamlit as st

from src.scoring.job_search import rank_postings_by_skills
from src.scoring.skill_matcher import SkillMatcher

_SORTS = {
    "Most demanded": lambda row, picked: (-row["demand"], -row["weighted_demand"]),
    "My skills first": lambda row, picked: (row["skill"] not in picked, -row["demand"]),
    "Biggest gaps": lambda row, picked: (row["skill"] in picked, -row["weighted_demand"]),
}


def _picked_from_chart(event, base: set[str]) -> set[str]:
    """Each clicked bar flips that skill relative to the starting picks."""
    clicked = {point["Skill"] for point in (event.selection.get("picked") or []) if "Skill" in point}
    return base ^ clicked


def render_skill_explorer(
    skills: list[dict],
    postings: list[dict],
    resume_skills: list[str],
    key: str,
    matcher: SkillMatcher | None = None,
) -> None:
    """Demand chart where clicking a bar adds/removes a skill, plus postings ranked by those skills.

    ``skills`` is ``analyze_role(...)["skills"]``. Picks start as the resume
    skills found in the sample; they're kept per ``key`` (one role) in session state.
    """
    if not skills:
        return
    names = {row["skill"] for row in skills}
    state = st.session_state.setdefault(
        f"explorer:{key}", {"picked": {s for s in resume_skills if s in names}, "version": 0}
    )

    sort_col, size_col, reset_col = st.columns([3, 2, 1])
    sort = sort_col.radio("Sort skills by", list(_SORTS), horizontal=True, key=f"explorer_sort:{key}")
    shown = len(skills)
    if len(skills) > 5:
        shown = size_col.slider("Skills shown", 5, min(60, len(skills)), min(25, len(skills)),
                                key=f"explorer_size:{key}")
    if reset_col.button("Reset picks", key=f"explorer_reset:{key}",
                        help="Start again from the skills found on your resume."):
        state["picked"] = {s for s in resume_skills if s in names}
        state["version"] += 1

    # The chart's own click state only lives as long as its spec, so picks are
    # stored here and the chart is re-keyed (fresh clicks) whenever the spec changes.
    spec = (sort, shown, state["version"])
    if state.get("spec") != spec:
        state["spec"], state["base"] = spec, set(state["picked"])
    base = state["base"]

    top = sorted(skills, key=lambda row: _SORTS[sort](row, base))[:shown]
    frame = pd.DataFrame(
        {
            "Skill": row["skill"],
            "Share of postings": row["demand"],
            "Weighted demand": row["weighted_demand"],
            "Postings": f"{row['count']} / {row['sample_size']}",
            "On resume": "Yes" if row["has_skill"] else "No",
            "Base": row["skill"] in base,
        }
        for row in top
    )
    picked_param = alt.selection_point(name="picked", fields=["Skill"], toggle="true", on="click",
                                       clear="dblclick", empty=False)
    # Picked = started picked XOR clicked since. Written as an expression so the
    # spec (and so the chart's click state) doesn't change between reruns.
    is_picked = "datum.Base != (length(data('picked_store')) > 0 && vlSelectionTest('picked_store', datum))"
    chart = (
        alt.Chart(frame)
        .mark_bar(cursor="pointer")
        .encode(
            x=alt.X("Share of postings:Q", axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("Skill:N", sort=list(frame["Skill"]), title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=240)),
            color=alt.condition(is_picked, alt.value("#2e7d5b"), alt.value("#c9ccd1")),
            tooltip=[
                "Skill",
                alt.Tooltip("Share of postings:Q", format=".0%"),
                alt.Tooltip("Weighted demand:Q", format=".0%"),
                "Postings",
                "On resume",
            ],
        )
        .add_params(picked_param)
        .properties(height=24 * len(frame) + 30)
    )
    event = st.altair_chart(chart, width="stretch", on_select="rerun", selection_mode="picked",
                            key=f"explorer_chart:{key}:{'|'.join(map(str, spec))}")
    state["picked"] = _picked_from_chart(event, base)
    picked = sorted(state["picked"], key=lambda s: [r["skill"] for r in skills].index(s))
    st.caption(
        "Green bars are the skills you're searching with: they start as the skills on your resume. "
        "Click a bar to add or remove it; double-click the chart to undo your clicks."
    )

    st.markdown("#### Postings that lean on your picked skills")
    if not picked:
        st.info("Click one or more bars above to pick skills.")
        return
    st.write("**Picked:** " + ", ".join(picked))
    ranked = rank_postings_by_skills(postings, picked, matcher)
    if not ranked:
        st.info("None of the sampled postings mention the picked skills.")
        return
    st.caption(
        "Match balances how much of a posting's skill list you cover with how many of your picked "
        "skills it asks for. Required mentions count double preferred ones."
    )
    st.dataframe(
        pd.DataFrame(
            {
                "Match": round(row["match"] * 100),
                "Title": row["posting"]["title"],
                "Company": row["posting"]["company"],
                "Location": row["posting"]["location"],
                "Posted": row["posting"]["posted_date"],
                "Covers": f"{row['coverage']:.0%}",
                "Your skills it asks for": ", ".join(row["matched"]),
                "Also asks for": ", ".join(row["missing"]),
                "URL": row["posting"]["url"],
            }
            for row in ranked
        ),
        width="stretch",
        hide_index=True,
        column_config={
            "Match": st.column_config.ProgressColumn("Match", min_value=0, max_value=100, format="%d"),
            "Covers": st.column_config.TextColumn(
                "Covers", help="Share of the posting's skill weight that falls on your picked skills."
            ),
            "URL": st.column_config.LinkColumn("URL", display_text="Open"),
        },
    )
