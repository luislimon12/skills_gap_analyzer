"""End-to-end dashboard tests: run the real Streamlit script headless with AppTest.

Each test points the app at a temporary database seeded with Adzuna-shaped
postings, so nothing touches the network or the real data/skills_gap.db.
The resume upload widget is replaced by a stub (AppTest can't drive file
uploads), and the fetch pipeline by a fake that records its calls.
"""

from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

import src.dashboard.components.upload_resume as upload_module
import src.dashboard.utils as dashboard_utils
import src.pipeline.fetch_postings as fetch_module
import src.pipeline.run_pipeline as pipeline_module
from src.pipeline.normalize import normalize_adzuna
from src.pipeline.store_postings import init_db, insert_postings

APP = str(Path(__file__).resolve().parents[1] / "src" / "dashboard" / "app.py")
TIMEOUT = 60

COMPANIES = ["Northwind Health", "Contoso Bank", "Fabrikam Retail", "Litware", "Adatum", "Proseware"]
REQUIREMENTS = {
    "Data Analyst": [
        ["SQL", "Python", "Tableau"], ["SQL", "Power BI", "Microsoft Excel"], ["Python", "Snowflake", "SQL"],
        ["Tableau", "Looker", "stakeholder management"], ["SQL", "ETL", "data visualization"],
    ],
    "Personal Support Worker": [
        ["PSW certificate", "CPR", "First Aid"], ["dementia care", "personal care", "WHMIS"],
        ["vital signs", "PSW certificate", "driver's licence"], ["palliative care", "CPR", "infection control"],
    ],
    "Welder": [
        ["MIG welding", "blueprint reading", "WHMIS"], ["TIG welding", "Red Seal", "power tools"],
        ["MIG welding", "forklift", "working at heights"], ["blueprint reading", "fabrication", "Red Seal"],
    ],
}
FILLER = (
    "Job Description: {company} is a world-class, industry-leading organization in {city}. "
    "Make an impact around the world on real-world problems. "
)


def _postings(role: str, count: int = 24) -> list[dict]:
    results = []
    for i in range(count):
        required = REQUIREMENTS[role][i % len(REQUIREMENTS[role])]
        company = COMPANIES[i % len(COMPANIES)]
        results.append({
            "id": f"{role}-{i}",
            "title": ("Senior " if i % 3 == 0 else "") + role,
            "company": {"display_name": company},
            "location": {"display_name": "Toronto, Ontario"},
            "created": f"2026-10-{10 + i % 15:02d}T09:00:00Z",
            "redirect_url": f"https://example.test/{role.replace(' ', '-')}/{i}",
            "description": FILLER.format(company=company, city="Toronto")
            + "Requirements:\n" + ", ".join(required) + ".\nNice to have:\nbilingual French.",
            "salary_min": 50000 + 1000 * i, "salary_max": 70000 + 1000 * i,
        })
    return normalize_adzuna({"results": results}, role)


@pytest.fixture
def app_env(tmp_path, monkeypatch):
    """Temporary settings + database, fake fetch, and a settable resume."""
    db = tmp_path / "postings.db"
    init_db(db)
    real_settings = dashboard_utils.load_settings()
    settings_path = tmp_path / "settings.yaml"
    settings_path.write_text(yaml.safe_dump({**real_settings, "database_path": str(db)}), encoding="utf-8")
    original_load = dashboard_utils.load_settings
    monkeypatch.setattr(dashboard_utils, "load_settings", lambda path=settings_path: original_load(path))

    env = {"db": db, "resume": None, "fetches": [], "keys": False, "fetch_result": None}
    monkeypatch.setattr(upload_module, "render_upload", lambda: env["resume"])
    monkeypatch.setattr(fetch_module, "adzuna_keys_configured", lambda: env["keys"])

    def fake_run(roles=None, max_pages=None, db_path=None, **_):
        env["fetches"].append((roles, max_pages))
        if env["fetch_result"] is not None:
            return env["fetch_result"]
        written = insert_postings(_postings(roles[0]) if roles[0] in REQUIREMENTS else [], db_path)
        return {"fetched": written, "unique": written, "written": written, "errors": []}

    monkeypatch.setattr(pipeline_module, "run", fake_run)
    return env


def _run(env, role: str | None = None, resume: str | None = None) -> AppTest:
    env["resume"] = resume
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    if role is not None:
        at.selectbox[0].select(role).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def _text(at) -> str:
    """Visible text of an AppTest run, or of one part of it (``at.main``, ``at.sidebar``)."""
    parts = [el.value for kind in ("markdown", "caption", "info", "warning", "success", "error")
             for el in getattr(at, kind)]
    parts += [str(m.value) for m in at.metric] + [m.label for m in at.metric]
    return "\n".join(str(p) for p in parts)


STUDENT_RESUME = """Alex Morgan
alex@example.com

SUMMARY
Computer programming student, framing business questions as data problems.

EXPERIENCE
IT Help Desk Assistant, Seneca Polytechnic
Sep 2024 - Present
- Meet weekly to review ticket reports; built Power BI dashboards.
- Wrote SQL and Python scripts to clean ticket data.

SKILLS
Python, SQL, Power BI, Microsoft Excel

INTERESTS
Wood carving, painting, cooking, hiking
"""


# ---------- loading ----------

def test_main_controls_are_on_the_page_not_hidden_in_the_sidebar(app_env):
    at = _run(app_env)
    assert at.title[0].value == "Job Posting Skills Gap Analyzer"
    assert at.selectbox[0].label == "Job you want"
    assert not at.sidebar.selectbox  # the sidebar only holds operator settings


def test_without_live_search_there_is_no_fetch_button_and_a_plain_explanation(app_env):
    at = _run(app_env)
    text = _text(at.main)
    assert not at.button  # nothing to click that can't work
    assert "There are no job postings for **Data Analyst** yet." in text
    assert "Live job search isn't set up on this copy of the app" in text
    for jargon in (".env", "ADZUNA_APP_ID", "config_postings.yaml", "API call"):
        assert jargon not in text, jargon  # setup details stay in the sidebar
    assert ".env" in "\n".join(c.value for c in at.sidebar.caption)


def test_empty_role_shows_one_message_and_the_occupation_reference(app_env):
    at = _run(app_env, role="Registered Nurse")
    assert len(at.info) == 1
    assert not at.tabs
    assert at.subheader[0].value == "What a Registered Nurse typically uses"


def test_role_search_labels_include_abbreviations():
    from src.dashboard.components.select_role import role_label

    assert role_label("Personal Support Worker") == "Personal Support Worker (PSW)"
    assert "mechanic" in role_label("Automotive Technician")
    assert role_label("Welder") == "Welder"


def test_every_configured_role_renders_without_postings(app_env):
    at = AppTest.from_file(APP, default_timeout=TIMEOUT)
    at.run()
    roles = dashboard_utils.load_settings()["target_roles"]
    assert len(roles) >= 60 and len(at.selectbox[0].options) == len(roles) + 1
    for role in roles:
        at.selectbox[0].select(role).run()
        assert not at.exception, (role, [e.value for e in at.exception])
        assert at.subheader[0].value == f"What a {role} typically uses"


def test_custom_typed_role_is_used(app_env):
    at = _run(app_env)
    at.selectbox[0].set_value("Other (type a job title)...").run()
    at.text_input[0].input("  Dental   Hygienist ").run()
    assert not at.exception
    assert "There are no job postings for **Dental Hygienist** yet." in _text(at)


def test_typed_role_without_postings_suggests_close_saved_roles(app_env):
    insert_postings(_postings("Welder"), app_env["db"])
    at = _run(app_env)
    at.selectbox[0].set_value("Other (type a job title)...").run()
    at.text_input[0].input("Welder Fabricator").run()
    suggestion = next(m.value for m in at.markdown if m.value.startswith("Saved postings are available for"))
    assert "**Welder** (24)" in suggestion


# ---------- fetching ----------

def test_fetch_button_runs_pipeline_for_selected_role_and_reports(app_env):
    app_env["keys"] = True
    at = _run(app_env, role="Welder")
    at.sidebar.slider[0].set_value(2).run()
    assert at.button[0].label == "Get the latest Welder postings"
    at.button[0].click().run()
    assert not at.exception
    assert app_env["fetches"] == [(["Welder"], 2)]
    assert "Found 24 Welder postings (24 new)." in [s.value for s in at.success]
    assert at.metric[0].value == "24"


def test_fetch_that_finds_nothing_warns_instead_of_reporting_success(app_env):
    app_env["keys"] = True
    app_env["fetch_result"] = {"fetched": 0, "unique": 0, "written": 0, "errors": []}
    at = _run(app_env, role="Welder")
    at.button[0].click().run()
    assert not at.success
    assert any("No postings were found" in w.value for w in at.warning)


# ---------- analysis ----------

@pytest.mark.parametrize("role", list(REQUIREMENTS))
def test_role_analysis_without_resume(app_env, role):
    insert_postings(_postings(role), app_env["db"])
    at = _run(app_env, role=role)
    text = _text(at)
    assert at.metric[0].value == "24"
    assert "Add your resume above" in text
    assert "Explore skills and find matching postings" in text
    assert "Click one or more bars above to pick skills." in text  # nothing picked without a resume


def test_resume_analysis_recognizes_real_skills_only(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    recognized = next(m.value for m in at.markdown if "Skills found on your resume" in m.value)
    for skill in ("Python", "SQL", "Power BI", "Microsoft Excel"):
        assert skill in recognized
    for wrong in ("Carpentry", "Painting", "Cooking", "Google Meet", "Oracle Reports"):
        assert wrong not in recognized, recognized
    labels = {m.label: m.value for m in at.metric}
    assert labels["How well you match"].endswith("%")
    assert "Skills to learn next" in [s.value for s in at.subheader]


def test_recommendations_are_in_plain_language(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    first = next(m.value for m in at.markdown if m.value.startswith("**1. "))
    assert "Asked for in" in first and "of 24 postings" in first
    assert "GapScore" not in first and "Wilson" not in first


def test_skill_explorer_starts_from_resume_skills_and_ranks_postings(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    picked = next(m.value for m in at.markdown if m.value.startswith("**Picked:**"))
    assert "Python" in picked and "SQL" in picked
    ranked = next(d.value for d in at.dataframe if "Your skills it asks for" in d.value.columns)
    assert list(ranked.columns[:3]) == ["Match", "Title", "Open"]  # the link stays visible on narrow screens
    assert len(ranked) > 0
    assert ranked["Match"].is_monotonic_decreasing


def test_postings_can_be_shown_as_cards(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    at.radio(key="explorer_view:Data Analyst:" + _resume_key(at)).set_value("Cards").run()
    assert not at.exception
    cards = [m.value for m in at.markdown if m.value.startswith("**Uses your:**")]
    assert 0 < len(cards) <= 10


def test_explorer_sort_options_rerun_cleanly(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    for option in ("My skills first", "Biggest gaps", "Most demanded"):
        at.radio(key="explorer_sort:Data Analyst:" + _resume_key(at)).set_value(option).run()
        assert not at.exception, option
    at.button(key="explorer_reset:Data Analyst:" + _resume_key(at)).click().run()
    assert not at.exception


def _resume_key(at: AppTest) -> str:
    radio = next(r for r in at.radio if r.key and r.key.startswith("explorer_sort:"))
    return radio.key.split(":", 2)[2]


def test_recurring_requirements_tab_has_no_job_ad_filler(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    insert_postings(_postings("Welder"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    tables = [t.value for t in at.table]
    phrases = {p.lower() for table in tables for p in table.index}
    filler = {"description", "world", "world-class", "make", "impact", "toronto", "northwind health"}
    assert not phrases & filler, phrases


def test_csv_download_is_offered(app_env):
    insert_postings(_postings("Data Analyst"), app_env["db"])
    at = _run(app_env, role="Data Analyst", resume=STUDENT_RESUME)
    assert any(el.proto.label == "Download results as CSV" for el in at.get("download_button"))
