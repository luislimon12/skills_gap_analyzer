"""Target-role selector populated from the shared settings file."""

import streamlit as st

from src.dashboard.utils import load_settings

OTHER_ROLE = "Other (type a role)..."


def render_role_selector(roles: list[str] | None = None) -> str:
    """Render the target-role selector and return the selected role.

    Any role can be typed in; Adzuna searches it like the configured ones.
    """
    if roles is None:
        roles = load_settings()["target_roles"]
    choice = st.selectbox("Target role", [*roles, OTHER_ROLE])
    if choice != OTHER_ROLE:
        return choice
    custom = st.text_input("Role to analyze", placeholder="e.g. Cloud Engineer").strip()
    return " ".join(custom.split()) or roles[0]
