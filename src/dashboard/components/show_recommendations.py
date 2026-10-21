"""Show a short ranked list of skills to learn next."""

import streamlit as st

from src.dashboard.utils import rank_gap_scores


def plain_reason(item: dict) -> str:
    """'Asked for in 12 of 30 postings (40%), usually as a requirement.'"""
    count, total = item.get("count"), item.get("sample_size")
    if not count or not total:
        return item.get("rationale") or "Ranked by how often postings ask for it."
    required, preferred = item.get("required_count", 0), item.get("preferred_count", 0)
    how = "usually as a requirement" if required >= preferred else "mostly as a nice-to-have"
    return f"Asked for in {count} of {total} postings ({count / total:.0%}), {how}."


def render_recommendations(gap_scores: list[dict], limit: int = 5) -> None:
    """Show the highest-gap skills in plain language, with the scoring details tucked in an expander."""
    if not gap_scores:
        return

    st.subheader("Skills to learn next")
    st.caption(
        "Skills you don't show yet, ranked by how often recent postings ask for them "
        "(required skills count more than nice-to-haves)."
    )
    for rank, item in enumerate(rank_gap_scores(gap_scores, limit), start=1):
        st.markdown(f"**{rank}. {item['skill']}** — {plain_reason(item)}")
    with st.expander("How these are scored"):
        st.caption(
            "GapScore = weighted demand × (1 − on resume). Weighted demand counts recent postings more "
            "(1.0 / 0.7 / 0.4 by age) and required mentions double nice-to-haves. Ranges are 95% Wilson intervals."
        )
        for item in rank_gap_scores(gap_scores, limit):
            st.caption(f"{item['skill']}: GapScore {item['gap_score']:.3f}. {item.get('rationale') or ''}")
