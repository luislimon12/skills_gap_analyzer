"""Display ranked skill gaps supplied by the scoring layer."""

import altair as alt
import pandas as pd
import streamlit as st

from src.dashboard.utils import rank_gap_scores


def render_gap_table(gap_scores: list[dict]) -> None:
    """Render scored skills in descending GapScore order."""
    if not gap_scores:
        st.info("No skill-gap results are available for this resume and role.")
        return

    st.dataframe(
        pd.DataFrame(
            [
                {
                    "Skill": row["skill"],
                    "Category": row["category"],
                    "On resume": "Yes" if row["has_skill"] else "No",
                    "Demand": f"{row['demand']:.0%}",
                    "95% Wilson interval": (
                        f"{row['confidence_low']:.0%}–{row['confidence_high']:.0%}"
                    ),
                    "Weighted demand": f"{row['weighted_demand']:.0%}",
                    "GapScore": round(row["gap_score"], 3),
                    "Postings": f"{row['count']} / {row['sample_size']}",
                    "Experience (years)": row["experience_years"],
                }
                for row in rank_gap_scores(gap_scores)
            ]
        ),
        width="stretch",
        hide_index=True,
    )


def render_demand_chart(
    skills: list[dict],
    limit: int = 20,
    chart_key: str = "demand_skill_chart",
) -> list[str]:
    """Show an interactive demand chart and return the selected skill names."""
    if not skills:
        return []
    ranked = sorted(skills, key=lambda row: (-row["demand"], -row["weighted_demand"]))
    top = ranked[:limit]
    frame = pd.DataFrame(
        {
            "Skill": row["skill"],
            "Share of postings": row["demand"],
            "Weighted demand": row["weighted_demand"],
            "Wilson low": row["confidence_low"],
            "Wilson high": row["confidence_high"],
            "Status": "On your resume" if row["has_skill"] else "Gap",
        }
        for row in top
    )
    order = list(frame["Skill"])
    selection = alt.selection_point(
        name="selected_skills",
        fields=["Skill"],
        toggle=True,
        clear="dblclick",
        empty=False,
    )
    legend_col, hint_col = st.columns([1, 4])
    legend_col.markdown(
        '<span style="color:#1565c0;font-size:1.2em">■</span> Selected',
        unsafe_allow_html=True,
    )
    hint_col.caption("Click a bar to select it; Shift-click to select multiple skills.")
    bars = (
        alt.Chart(frame)
        .mark_bar()
        .add_params(selection)
        .encode(
            x=alt.X("Share of postings:Q", title="Share of postings",
                    axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("Skill:N", sort=order, title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=240)),
            color=alt.condition(
                selection,
                alt.value("#1565c0"),
                alt.Color(
                    "Status:N",
                    scale=alt.Scale(
                        domain=["On your resume", "Gap"],
                        range=["#2e7d5b", "#c8553d"],
                    ),
                    legend=alt.Legend(orient="bottom", title=None),
                ),
            ),
            tooltip=[
                "Skill",
                alt.Tooltip("Share of postings:Q", format=".0%"),
                alt.Tooltip("Weighted demand:Q", format=".0%"),
                alt.Tooltip("Wilson low:Q", format=".0%"),
                alt.Tooltip("Wilson high:Q", format=".0%"),
                "Status",
            ],
        )
    )
    chart_state = st.altair_chart(
        bars.properties(height=26 * len(frame) + 40),
        width="stretch",
        key=chart_key,
        on_select="rerun",
        selection_mode="selected_skills",
    )
    selected = chart_state.selection.get("selected_skills", [])
    selected_names = [item["Skill"] for item in selected if "Skill" in item]
    chart_skills = {row["skill"].casefold() for row in top}
    additional_skills = st.multiselect(
        "Add skills outside the chart",
        options=[row["skill"] for row in ranked if row["skill"].casefold() not in chart_skills],
        key=f"{chart_key}_additional_skills",
        help="Choose lower-ranked skills to include in the posting search.",
    )
    selected_names.extend(skill for skill in additional_skills if skill not in selected_names)
    st.caption(
        "Results prefer postings matching more selected skills, then use each skill's share "
        "of postings as its weight. Double-click the chart to clear."
    )
    return selected_names
