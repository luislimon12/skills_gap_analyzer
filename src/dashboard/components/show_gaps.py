"""Display ranked skill gaps supplied by the scoring layer."""

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
        use_container_width=True,
        hide_index=True,
    )
