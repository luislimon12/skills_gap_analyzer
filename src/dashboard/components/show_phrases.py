"""Recurring posting phrases that the skill taxonomy doesn't cover."""

import pandas as pd
import streamlit as st


def render_discovered_phrases(phrases: list[dict], has_resume: bool = True) -> None:
    """Show recurring phrases from the postings, flagging ones missing from the resume."""
    if not phrases:
        st.info("No other phrases recur often enough in this posting sample.")
        return
    st.caption(
        "Short phrases that recur across these postings but aren't in the skill taxonomy. "
        "They come straight from the posting text, so read them as hints. They don't affect "
        "GapScore or fit."
    )
    if has_resume:
        missing = [row["phrase"] for row in phrases if not row["in_resume"]][:8]
        if missing:
            st.markdown("**Recurring in postings, not found in your resume:** " + ", ".join(missing))
    # A static table: interactive dataframes can size to zero inside a hidden tab.
    st.table(
        pd.DataFrame(
            {
                "Phrase": row["phrase"],
                "Postings": row["count"],
                "Share of postings": f"{row['share']:.0%}",
                **({"In your resume": "Yes" if row["in_resume"] else "No"} if has_resume else {}),
            }
            for row in phrases
        ).set_index("Phrase")
    )
