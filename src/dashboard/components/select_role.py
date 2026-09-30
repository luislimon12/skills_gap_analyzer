"""Target-role selector populated from the shared settings file."""

import streamlit as st

from src.dashboard.utils import load_settings


def render_role_selector(roles: list[str] | None = None) -> str:
    """Render the target-role selector and return the selected role."""
    if roles is None:
        roles = load_settings()["target_roles"]
    return st.selectbox("Target role", roles)
