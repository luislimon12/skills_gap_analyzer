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


# Job-list filters: (button label, what it means, what to say when nothing matches).
_MODES = {
    "only": (
        "Only my picked skills",
        "Postings that ask for nothing beyond your picked skills, the ones using most of them first.",
        "No posting asks for only these skills. Try **Mostly my picked skills**, or pick more skills.",
    ),
    "mostly": (
        "Mostly my picked skills",
        "Postings where your picked skills make up more than half of what they ask for, best coverage first.",
        "No posting is mostly about these skills. Try **Any of my picked skills**, or pick more skills.",
    ),
    "any": (
        "Any of my picked skills",
        "Every posting that mentions at least one picked skill, best overall match first.",
        "None of these postings mention the picked skills.",
    ),
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
        "Click or tap a bar to add or remove it; double-click the chart to undo your clicks."
    )

    st.markdown("#### Postings that lean on your picked skills")
    if not picked:
        st.info("Click one or more bars above to pick skills.")
        return
    st.write("**Picked:** " + ", ".join(picked))
    mode = st.segmented_control(
        "Show jobs that ask for", list(_MODES), default="mostly", key=f"explorer_mode:{key}",
        format_func=lambda m: _MODES[m][0],
    ) or "mostly"
    st.caption(_MODES[mode][1])
    ranked = rank_postings_by_skills(postings, picked, matcher, mode=mode)
    if not ranked:
        st.info(_MODES[mode][2])
        return
    st.caption(f"{len(ranked)} of {len(postings)} postings. Required skills count double nice-to-haves.")
    view = st.radio("Show postings as", ["Cards", "Table"], index=0 if _is_phone() else 1,
                    horizontal=True, key=f"explorer_view:{key}")
    if view == "Cards":
        _render_cards(ranked)
    else:
        _render_table(ranked)


def _is_phone() -> bool:
    """Best guess from the browser's user agent; the viewer can switch views either way."""
    try:
        agent = st.context.headers.get("User-Agent") or ""
    except Exception:  # no browser context (tests, bare mode)
        return False
    return "Mobi" in agent or "Android" in agent


def _render_cards(ranked: list[dict], limit: int = 10) -> None:
    """One bordered card per posting: easy to read and tap on a phone."""
    for row in ranked[:limit]:
        posting = row["posting"]
        with st.container(border=True):
            title = f"[{posting['title']}]({posting['url']})" if posting.get("url") else posting["title"]
            st.markdown(f"**{title}** · {round(row['match'] * 100)}% match")
            st.caption(" · ".join(part for part in (posting.get("company"), posting.get("location"),
                                                    posting.get("posted_date")) if part))
            st.markdown("**Uses your:** " + ", ".join(row["matched"]))
            if row["missing"]:
                st.markdown("**Also wants:** " + ", ".join(row["missing"]))
    if len(ranked) > limit:
        st.caption(f"Showing the top {limit} of {len(ranked)} postings. Switch to Table to see them all.")


def _render_table(ranked: list[dict]) -> None:
    st.dataframe(
        pd.DataFrame(
            {
                "Match": round(row["match"] * 100),
                "Title": row["posting"]["title"],
                "Open": row["posting"]["url"],
                "Company": row["posting"]["company"],
                "Your skills it asks for": ", ".join(row["matched"]),
                "Also asks for": ", ".join(row["missing"]),
                "Covers": f"{row['coverage']:.0%}",
                "Location": row["posting"]["location"],
                "Posted": row["posting"]["posted_date"],
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
            "Open": st.column_config.LinkColumn("Open", display_text="Open"),
        },
    )
