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


def render_demand_chart(skills: list[dict], limit: int = 20) -> None:
    """Horizontal bars of the most-demanded skills, coloured by resume coverage."""
    if not skills:
        return
    top = sorted(skills, key=lambda row: (-row["demand"], -row["weighted_demand"]))[:limit]
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
    bars = (
        alt.Chart(frame)
        .mark_bar()
        .encode(
            x=alt.X("Share of postings:Q", title="Share of postings",
                    axis=alt.Axis(format="%"), scale=alt.Scale(domain=[0, 1])),
            y=alt.Y("Skill:N", sort=order, title=None,
                    axis=alt.Axis(labelOverlap=False, labelLimit=240)),
            color=alt.Color(
                "Status:N",
                scale=alt.Scale(domain=["On your resume", "Gap"], range=["#2e7d5b", "#c8553d"]),
                legend=alt.Legend(orient="bottom", title=None),
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
    interval = (
        alt.Chart(frame)
        .mark_rule(color="#555")
        .encode(x=alt.X("Wilson low:Q", title="Share of postings"), x2="Wilson high:Q",
                y=alt.Y("Skill:N", sort=order))
    )
    st.altair_chart((bars + interval).properties(height=26 * len(frame) + 40), width="stretch")
    st.caption("Bars show the share of sampled postings that mention each skill; lines show the 95% Wilson interval.")
