"""Target-role selector populated from the shared settings file."""

import streamlit as st

from src.dashboard.utils import load_settings

OTHER_ROLE = "Other (type a job title)..."

# Other names people search for, shown next to the role so typing them finds it
# ("PSW" finds Personal Support Worker). The value returned is still the role itself.
ROLE_SEARCH_HINTS = {
    "Personal Support Worker": "PSW",
    "Registered Nurse": "RN",
    "Registered Practical Nurse": "RPN, LPN",
    "Early Childhood Educator": "ECE",
    "Educational Assistant": "EA",
    "Customer Service Representative": "CSR, call centre",
    "IT Support": "help desk, technician",
    "QA Analyst": "quality assurance, software tester",
    "UX Designer": "user experience, UI",
    "HR Coordinator": "human resources",
    "Cybersecurity Analyst": "information security",
    "AZ Driver": "truck driver, Class 1",
    "Delivery Driver": "courier, DZ",
    "Warehouse Associate": "warehouse worker, picker",
    "Line Cook": "kitchen, prep cook",
    "Chef": "cook",
    "Server": "waiter, waitress",
    "Custodian": "janitor, cleaner",
    "Automotive Technician": "mechanic",
    "HVAC Technician": "heating, refrigeration",
    "Software Developer": "programmer, software engineer",
    "Full Stack Developer": "web",
    "Medical Office Assistant": "medical receptionist, MOA",
    "Bookkeeper": "accounting clerk",
    "Teacher": "OCT",
}


def role_label(role: str) -> str:
    hint = ROLE_SEARCH_HINTS.get(role)
    return f"{role} ({hint})" if hint else role


def render_role_selector(roles: list[str] | None = None) -> str:
    """Render the target-role selector and return the selected role.

    Any role can be typed in; Adzuna searches it like the configured ones.
    """
    if roles is None:
        roles = load_settings()["target_roles"]
    choice = st.selectbox(
        "Job you want",
        [*roles, OTHER_ROLE],
        format_func=role_label,
        help="Start typing to search, e.g. “PSW” or “mechanic”. Not listed? Pick the last option.",
    )
    if choice != OTHER_ROLE:
        return choice
    custom = st.text_input("Job title", placeholder="e.g. Dental Hygienist").strip()
    return " ".join(custom.split()) or roles[0]
