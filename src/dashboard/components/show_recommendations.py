"""Show a short ranked list of skills to learn next."""

import streamlit as st

from src.dashboard.utils import rank_gap_scores


def render_recommendations(gap_scores: list[dict], limit: int = 5) -> None:
    """Show the highest-gap skills with available demand/evidence context."""
    if not gap_scores:
        return

    st.subheader("Recommended skills to learn next")
    for rank, item in enumerate(rank_gap_scores(gap_scores, limit), start=1):
        skill = item["skill"]
        score = item["gap_score"]
        context = item.get("rationale") or item.get("evidence")
        if not context:
            context = "Ranked by its current GapScore."
        st.markdown(f"**{rank}. {skill}** — GapScore {score:.3f}. {context}")
