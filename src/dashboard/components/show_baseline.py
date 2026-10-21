"""O*NET occupation baseline: technologies and tools an occupation is known to use."""

import pandas as pd
import streamlit as st


def _coverage_caption(rows: list[dict], noun: str, has_resume: bool) -> str:
    occupations = ", ".join(sorted({o for row in rows for o in row["occupations"]}))
    if not has_resume:
        return f"O*NET lists {len(rows)} {noun} for {occupations}."
    covered = sum(row["has_skill"] for row in rows)
    return f"Your resume mentions {covered} of the {len(rows)} {noun} O*NET lists for {occupations}."


def render_onet_baseline(
    technologies: list[dict], tools: list[dict] | None = None, has_resume: bool = True, limit: int = 40
) -> None:
    """Show O*NET technologies and tools for the selected occupations, with resume coverage."""
    tools = tools or []
    if not technologies and not tools:
        st.info("Pick one or more O*NET occupations above to see what the occupation typically uses.")
        return

    st.caption(
        "A reference list for the occupation, not posting demand. It does not change GapScore or fit."
    )
    if technologies:
        st.markdown("#### Software and technology")
        st.caption(_coverage_caption(technologies, "technologies", has_resume))
        st.dataframe(
            pd.DataFrame(
                {
                    "Technology": row["skill"],
                    **({"On resume": "Yes" if row["has_skill"] else "No"} if has_resume else {}),
                    "O*NET In Demand": "Yes" if row["in_demand"] else "",
                    "O*NET Hot Technology": "Yes" if row["hot_technology"] else "",
                    "Listed for": ", ".join(row["occupations"]),
                }
                for row in technologies[:limit]
            ),
            width="stretch",
            hide_index=True,
        )
    if tools:
        st.markdown("#### Tools and equipment")
        st.caption(_coverage_caption(tools, "tools", has_resume))
        ordered = sorted(tools, key=lambda row: (not row["has_skill"], -row["occupation_share"], row["skill"]))
        st.dataframe(
            pd.DataFrame(
                {
                    "Tool or equipment": row["skill"],
                    **({"On resume": "Yes" if row["has_skill"] else "No"} if has_resume else {}),
                    "Listed for": ", ".join(row["occupations"]),
                }
                for row in ordered[:limit]
            ),
            width="stretch",
            hide_index=True,
        )
