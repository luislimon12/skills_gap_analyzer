"""Role 1 tests: normalize, dedup, storage, and the end-to-end run with fetchers stubbed out."""

import sqlite3

import pytest

from src.common.text import clean_text, html_to_text
from src.pipeline import fetch_postings, run_pipeline
from src.pipeline.dedup_postings import dedup_postings, make_posting_hash, normalize_company
from src.pipeline.normalize import normalize_adzuna, normalize_greenhouse, normalize_lever, title_matches_role
from src.pipeline.store_postings import init_db, insert_posting, insert_postings, load_postings

ADZUNA_RAW = {
    "count": 2,
    "results": [
        {
            "id": "4012", "title": "Senior Data Analyst",
            "company": {"display_name": "Shopify Inc."},
            "location": {"display_name": "Toronto, Ontario"},
            "created": "2026-09-20T08:15:22Z",
            "redirect_url": "https://www.adzuna.ca/details/4012",
            "description": "We need <strong>SQL</strong> and Python…",
        },
        {
            "id": "4013", "title": "Business Analyst",
            "company": {"display_name": "Acme"}, "location": {"display_name": "Ottawa"},
            "created": "2026-09-21T00:00:00Z", "redirect_url": "", "description": "Excel",
        },
    ],
}

GREENHOUSE_RAW = {
    "jobs": [
        {
            "id": 77, "title": "Senior Data Analyst", "company_name": "Shopify",
            "location": {"name": "Remote - Canada"},
            "first_published": "2026-09-21T10:00:00-04:00",
            "absolute_url": "https://boards.greenhouse.io/shopify/jobs/77",
            "content": "&lt;p&gt;You will use &lt;strong&gt;SQL&lt;/strong&gt; daily.&lt;/p&gt;"
                       "&lt;ul&gt;&lt;li&gt;3+ years of Python&lt;/li&gt;&lt;li&gt;dbt &amp;amp; Airflow&lt;/li&gt;&lt;/ul&gt;",
        },
        {"id": 78, "title": "Software Engineer", "company_name": "Shopify", "content": ""},
    ]
}

LEVER_RAW = [
    {
        "id": "abc-123", "text": "Data Analyst, Growth",
        "categories": {"location": "Vancouver"},
        "createdAt": 1790000000000,
        "hostedUrl": "https://jobs.lever.co/netflix/abc-123",
        "descriptionPlain": "Join the growth team.",
        "lists": [{"text": "Requirements", "content": "<li>SQL</li><li>Tableau</li>"}],
        "additionalPlain": "Benefits included.",
    },
    {"id": "def", "text": "Recruiter", "categories": {}, "createdAt": 1790000000000},
]


# ---------- text cleaning ----------

def test_html_to_text_handles_greenhouse_double_escaping():
    text = html_to_text(GREENHOUSE_RAW["jobs"][0]["content"])
    assert text == "You will use SQL daily.\n\n- 3+ years of Python\n- dbt & Airflow"


def test_clean_text_normalizes_bullets_ligatures_and_whitespace():
    raw = "• Built ﬁnancial dashboards  in  Tableau\r\n\r\n\r\n\r\n▪ SQL"
    assert clean_text(raw) == "- Built financial dashboards in Tableau\n\n- SQL"


# ---------- normalize ----------

@pytest.mark.parametrize("title, expected", [
    ("Senior Data Analyst, Growth", True),
    ("Analyst, Data & Insights", True),
    ("Business Analyst", False),
    ("Database Administrator", False),
])
def test_title_matches_role(title, expected):
    assert title_matches_role(title, "Data Analyst") is expected


def test_normalize_adzuna():
    postings = normalize_adzuna(ADZUNA_RAW, "Data Analyst")
    first = postings[0]
    assert first["source"] == "adzuna"
    assert first["company"] == "Shopify Inc."
    assert first["posted_date"] == "2026-09-20"
    assert first["full_text"] == "We need SQL and Python..."  # NFKC turns the ellipsis char into "..."
    assert first["is_truncated"] is True
    assert first["requirement_level"] is None
    assert len(first["unique_hash"]) == 64


def test_normalize_greenhouse_filters_by_role():
    postings = normalize_greenhouse(GREENHOUSE_RAW, "shopify", "Data Analyst")
    assert [p["source_id"] for p in postings] == ["77"]
    assert postings[0]["posted_date"] == "2026-09-21"
    assert postings[0]["is_truncated"] is False
    assert "- 3+ years of Python" in postings[0]["full_text"]


def test_normalize_lever_combines_description_and_lists():
    postings = normalize_lever(LEVER_RAW, "netflix", "Data Analyst")
    assert len(postings) == 1
    text = postings[0]["full_text"]
    assert text.startswith("Join the growth team.")
    assert "Requirements\n\n- SQL\n- Tableau" in text
    assert text.endswith("Benefits included.")
    assert postings[0]["posted_date"] == "2026-09-21"


# ---------- dedup ----------

def test_hash_ignores_case_punctuation_and_company_suffix():
    assert normalize_company("Shopify Inc.") == normalize_company("shopify") == "shopify"
    assert make_posting_hash("Senior Data Analyst", "Shopify Inc.", "2026-09-20") == \
        make_posting_hash("senior  data analyst", "SHOPIFY", "2026-09-20T12:00:00")


def test_dedup_prefers_full_text_across_sources_within_date_window():
    adzuna = normalize_adzuna(ADZUNA_RAW, "Data Analyst")[:1]            # 2026-09-20, truncated
    greenhouse = normalize_greenhouse(GREENHOUSE_RAW, "shopify", "Data Analyst")  # 2026-09-21, full
    unique = dedup_postings(adzuna + greenhouse)
    assert len(unique) == 1
    assert unique[0]["source"] == "greenhouse"


def test_dedup_keeps_same_title_far_apart_in_time():
    a = {"title": "Data Analyst", "company": "Acme", "posted_date": "2026-01-01", "full_text": "a"}
    b = {**a, "posted_date": "2026-06-01"}
    assert len(dedup_postings([a, b])) == 2


# ---------- storage ----------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "test.db"
    init_db(path)
    return path


def test_insert_is_idempotent(db):
    posting = normalize_greenhouse(GREENHOUSE_RAW, "shopify", "Data Analyst")[0]
    assert insert_posting(posting, db) is True
    assert insert_posting(posting, db) is False
    rows = load_postings("Data Analyst", db)
    assert len(rows) == 1
    assert rows[0]["is_truncated"] is False
    assert rows[0]["fetched_at"]


def test_truncated_row_is_upgraded_by_full_text_copy(db):
    truncated = normalize_adzuna(ADZUNA_RAW, "Data Analyst")[0]
    full = {**truncated, "source": "greenhouse", "full_text": "Much longer full description", "is_truncated": False}
    insert_postings([truncated], db)
    insert_postings([full], db)
    rows = load_postings(db_path=db)
    assert len(rows) == 1
    assert rows[0]["source"] == "greenhouse"
    # ...and a later truncated copy does not overwrite it
    insert_postings([truncated], db)
    assert load_postings(db_path=db)[0]["source"] == "greenhouse"


def test_init_db_migrates_week1_schema(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE postings (id INTEGER PRIMARY KEY, title TEXT, company TEXT, source TEXT, "
                 "posted_date DATE, requirement_level TEXT, full_text TEXT, unique_hash TEXT UNIQUE)")
    conn.commit()
    conn.close()
    init_db(path)
    insert_postings(normalize_adzuna(ADZUNA_RAW, "Data Analyst"), path)
    assert {r["role"] for r in load_postings(db_path=path)} == {"Data Analyst"}


# ---------- end-to-end run ----------

def test_run_pipeline_with_stubbed_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_postings, "adzuna_keys_configured", lambda: True)
    monkeypatch.setattr(fetch_postings, "fetch_adzuna_postings", lambda *a, **k: ADZUNA_RAW)
    monkeypatch.setattr(fetch_postings, "fetch_greenhouse_postings", lambda token: GREENHOUSE_RAW)
    monkeypatch.setattr(fetch_postings, "fetch_lever_postings", lambda company: LEVER_RAW)
    monkeypatch.setattr(fetch_postings, "RAW_DATA_DIR", tmp_path / "raw")

    config = {
        "adzuna": {"results_per_page": 50, "max_pages_per_role": 1, "request_delay_seconds": 0},
        "greenhouse_boards": ["shopify"],
        "lever_boards": ["netflix"],
        "target_roles": ["Data Analyst"],
    }
    db = tmp_path / "run.db"
    summary = run_pipeline.run(config=config, db_path=db, save_raw=False)

    # adzuna: 1 matching title (Business Analyst filtered), greenhouse: 1, lever: 1
    assert summary == {"fetched": 3, "unique": 2, "written": 2, "errors": []}
    assert {p["source"] for p in load_postings(db_path=db)} == {"greenhouse", "lever"}


def test_run_pipeline_skips_adzuna_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_postings, "adzuna_keys_configured", lambda: False)

    def boom(*a, **k):
        raise AssertionError("Adzuna should not be called without keys")

    monkeypatch.setattr(fetch_postings, "fetch_adzuna_postings", boom)
    config = {"target_roles": ["Data Analyst"], "greenhouse_boards": [], "lever_boards": []}
    summary = run_pipeline.run(config=config, db_path=tmp_path / "run.db", save_raw=False)
    assert summary["fetched"] == 0
