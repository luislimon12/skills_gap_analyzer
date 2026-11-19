"""Target-role selector.

Jobs are collected by the scheduled worker, so only the roles it tracks
(config/ingestion.yaml ``queries``) can be analysed.
"""

import streamlit as st

from src.dashboard.utils import load_settings


def render_role_selector(roles: list[str] | None = None) -> str:
    """Render the target-role selector and return the selected role."""
    if roles is None:
        roles = load_settings()["target_roles"]
    return st.selectbox(
        "Target role",
        roles,
        help="Job postings for these roles are collected automatically every hour.",
    )
