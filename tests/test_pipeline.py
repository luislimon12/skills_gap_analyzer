"""Role 1 tests: normalize, dedup, storage, and the end-to-end run with fetchers stubbed out."""

import sqlite3

import pytest

from src.common.text import clean_text, html_to_text
from src.pipeline import fetch_postings, run_pipeline
from src.pipeline.dedup_postings import dedup_postings, make_posting_hash, normalize_company
from src.pipeline.geography import normalize_geography
from src.pipeline.normalize import (
    normalize_adzuna,
    normalize_canada_job_bank,
    normalize_greenhouse,
    normalize_lever,
    normalize_usajobs,
    title_matches_role,
)
from src.pipeline.store_postings import (
    backfill_posting_geography,
    init_db, insert_posting, insert_postings, load_latest_market_snapshot, load_postings,
)

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


USAJOBS_RAW = {
    "SearchResult": {
        "SearchResultItems": [{
            "MatchedObjectDescriptor": {
                "PositionID": "123456",
                "PositionTitle": "Data Analyst",
                "OrganizationName": "Department of Example",
                "PositionURI": "https://www.usajobs.gov/job/123456",
                "PublicationStartDate": "2026-10-01T00:00:00.0000",
                "PositionLocation": [{
                    "LocationName": "Washington, District of Columbia",
                    "CountryCode": "US",
                    "CountrySubDivisionCode": "DC",
                }],
                "PositionSummary": "Analyze public data.",
                "QualificationSummary": "Experience with SQL.",
                "PositionFormattedDescription": [{
                    "Label": "Duties",
                    "LabelDescription": "<p>Build dashboards.</p>",
                }],
            },
        }],
    },
}

JOB_BANK_CSV = """WIC Job Location Snapshot ID,Job Title,Original Job Title,NOC 2016 Code,NOC 2016 Code Name,NOC21 Code,NOC21 Code Name,External Indicator,First Posting Date,Vacancy Count,Official Language,Education LOS,Experience Level,Government Type,Placement Agency,NAICS,Province/Territory,City,Work Location Postal Code,Economic  Region,Various Location,Employment Type,Employment Term,Employment Term Start Date,Employment Term End Date,Employment Term Oncall,Employment Term Overtime,Employment Term Day,Employment Term Evening,Employment Term Shift,Employment Term Weekend,Employment Term Night,Employment Term Telework,Employment Term Early,Employment Term Flex,Employment Term Morning,Employment Term TBD,Salary Condition Detail,Salary Per,Salary Minimum,Salary Maximum,Salary Condition Collective,Salary Condition Bonus,Salary Condition Disability,Salary Condition Gratuity,Salary Condition Medical,Salary Condition Mileage,Salary Condition Piecework,Salary Condition Commission,Salary Condition RESP,Salary Condition Dental,Salary Condition Group Insurance,Salary Condition Life Insurance,Salary Condition Pension,Salary Condition RRSP,Condition Vision Care,Salary Condition Other Benefits,Commission PER,Commission Type,Hours Per,Hours Minimum,Hours Maximum,Work Hours,Work Hours From Time,Work Hours To Time
15925021,bartender,Barmaid ou Barman - Poste occasionnel,6512,Bartenders,64301,Bartenders,1,2026/10/02,1,English or French,NA,NA,NA,NA,NA,Alberta,Calgary,NA,NA,No,Full time,Permanent,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,$20.51 hourly,Hour,20.51,20.51,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,No,NA,NA
15925022,Data Analyst,Analyste de données,21223,Database analysts and data administrators,21223,Database analysts and data administrators,1,2026/10/03,2,English,University degree,1 to 2 years,NA,NA,NA,Ontario,Toronto,NA,NA,No,Full time,Permanent,NA,NA,NA,NA,NA,NA,NA,NA,NA,Yes,NA,NA,NA,NA,$80000.00 annually,Year,80000,95000,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,NA,No,NA,NA
""".replace(",", "\t")


def test_normalize_usajobs():
    posting = normalize_usajobs(USAJOBS_RAW, "Data Analyst")[0]
    assert posting["source"] == "usajobs"
    assert posting["source_id"] == "123456"
    assert posting["country"] == "United States"
    assert posting["region"] == "District of Columbia"
    assert posting["posted_date"] == "2026-10-01"
    assert "Experience with SQL." in posting["full_text"]
    assert "Build dashboards." in posting["full_text"]


def test_normalize_canada_job_bank_csv():
    postings = normalize_canada_job_bank(
        {"csv_text": JOB_BANK_CSV, "resource_name": "October CSV"},
        "Data Analyst",
    )
    assert len(postings) == 1
    posting = postings[0]
    assert posting["source"] == "canada_job_bank"
    assert posting["source_id"] == "15925022"
    assert posting["country"] == "Canada"
    assert posting["region"] == "Ontario"
    assert posting["location"] == "Toronto, Ontario"
    assert posting["posted_date"] == "2026-10-03"
    assert (posting["salary_min"], posting["salary_max"]) == (80000.0, 95000.0)
    assert posting["contract_time"] == "full_time"
    assert "Occupation: Database analysts and data administrators" in posting["full_text"]
    assert posting["url"] == ""


def test_normalize_canada_job_bank_does_not_treat_hourly_pay_as_annual():
    postings = normalize_canada_job_bank({"csv_text": JOB_BANK_CSV}, "Bartender")
    assert len(postings) == 1
    assert postings[0]["salary_min"] is None
    assert postings[0]["salary_max"] is None


@pytest.mark.parametrize(
    "location, country_hint, expected",
    [
        ("Toronto, ON", None, ("Canada", "Ontario")),
        ("Ottawa, Ontario, Canada", None, ("Canada", "Ontario")),
        ("Seattle, WA", None, ("United States", "Washington")),
        ("Remote", None, ("", "")),
    ],
)
def test_normalize_geography(location, country_hint, expected):
    assert normalize_geography(location, country_hint) == expected


# ---------- dedup ----------

def test_hash_ignores_case_punctuation_and_company_suffix():
    assert normalize_company("Shopify Inc.") == normalize_company("shopify") == "shopify"
    assert make_posting_hash("Senior Data Analyst", "Shopify Inc.", "2026-09-20") == \
        make_posting_hash("senior  data analyst", "SHOPIFY", "2026-09-20T12:00:00")


def test_dedup_prefers_full_text_across_sources_within_date_window():
    adzuna = normalize_adzuna(ADZUNA_RAW, "Data Analyst")[:1]            # 2026-09-20, truncated
    same_location = {
        **GREENHOUSE_RAW,
        "jobs": [{
            **GREENHOUSE_RAW["jobs"][0],
            "location": {"name": "Toronto, Ontario"},
        }],
    }
    greenhouse = normalize_greenhouse(same_location, "shopify", "Data Analyst")  # 2026-09-21, full
    unique = dedup_postings(adzuna + greenhouse)
    assert len(unique) == 1
    assert unique[0]["source"] == "greenhouse"


def test_dedup_keeps_same_title_at_different_locations():
    first = {
        "source": "usajobs", "source_id": "job-a", "title": "Data Analyst",
        "company": "Agency", "country": "United States", "region": "Virginia",
        "location": "Richmond, Virginia", "posted_date": "2026-10-01", "full_text": "SQL",
    }
    second = {
        **first, "source": "adzuna", "source_id": "job-b", "region": "Maryland",
        "location": "Baltimore, Maryland",
    }
    assert len(dedup_postings([first, second])) == 2


def test_dedup_keeps_distinct_ids_from_same_source():
    first = {
        "source": "usajobs", "source_id": "job-a", "title": "Data Analyst",
        "company": "Agency", "location": "Washington, DC",
        "posted_date": "2026-10-01", "full_text": "SQL",
    }
    second = {**first, "source_id": "job-b"}
    assert len(dedup_postings([first, second])) == 2


def test_dedup_uses_same_source_id_even_if_metadata_changes():
    first = {
        "source": "usajobs", "source_id": "job-a", "title": "Data Analyst",
        "company": "Agency", "location": "Washington, DC", "full_text": "SQL",
        "is_truncated": True,
    }
    second = {
        **first, "title": "Senior Data Analyst", "location": "Remote",
        "full_text": "Full SQL description", "is_truncated": False,
    }
    unique = dedup_postings([first, second])
    assert len(unique) == 1
    assert unique[0]["title"] == "Senior Data Analyst"


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
    full = {
        **truncated,
        "source": "greenhouse",
        "source_id": "77",
        "full_text": "Much longer full description",
        "is_truncated": False,
    }
    insert_postings([truncated], db)
    insert_postings([full], db)
    rows = load_postings(db_path=db)
    assert len(rows) == 1
    assert rows[0]["source"] == "greenhouse"
    # ...and a later truncated copy of the same source identity does not overwrite it
    insert_postings([truncated], db)
    assert load_postings(db_path=db)[0]["source"] == "greenhouse"


def test_stored_cross_source_duplicate_upgrades_existing_row(db):
    adzuna = normalize_adzuna(ADZUNA_RAW, "Data Analyst")[0]
    same_location = {
        **GREENHOUSE_RAW,
        "jobs": [{
            **GREENHOUSE_RAW["jobs"][0],
            "location": {"name": "Toronto, Ontario"},
        }],
    }
    greenhouse = normalize_greenhouse(same_location, "shopify", "Data Analyst")[0]
    insert_postings([adzuna], db)
    insert_postings([greenhouse], db)
    rows = load_postings(db_path=db)
    assert len(rows) == 1
    assert rows[0]["source"] == "greenhouse"
    assert rows[0]["is_truncated"] is False


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
    greenhouse_raw = {
        **GREENHOUSE_RAW,
        "jobs": [{
            **GREENHOUSE_RAW["jobs"][0],
            "location": {"name": "Toronto, Ontario"},
        }, GREENHOUSE_RAW["jobs"][1]],
    }
    monkeypatch.setattr(fetch_postings, "fetch_greenhouse_postings", lambda token: greenhouse_raw)
    monkeypatch.setattr(fetch_postings, "fetch_lever_postings", lambda company: LEVER_RAW)
    monkeypatch.setattr(fetch_postings, "RAW_DATA_DIR", tmp_path / "raw")

    config = {
        "adzuna": {"results_per_page": 50, "max_pages_per_role": 1, "request_delay_seconds": 0},
        "greenhouse_boards": ["shopify"],
        "lever_boards": ["netflix"],
        "target_roles": ["Data Analyst"],
        "usajobs": {"enabled": False},
        "canada_job_bank": {"enabled": False},
    }
    db = tmp_path / "run.db"
    summary = run_pipeline.run(config=config, db_path=db, save_raw=False)

    # adzuna: 1 matching title (Business Analyst filtered), greenhouse: 1, lever: 1
    assert summary == {"fetched": 3, "unique": 2, "written": 2, "errors": [],
                       "market": {"Data Analyst": {"total_count": 2, "mean_salary": None}}}
    assert load_latest_market_snapshot("Data Analyst", db)["total_count"] == 2
    assert {p["source"] for p in load_postings(db_path=db)} == {"greenhouse", "lever"}


def test_run_pipeline_skips_adzuna_without_keys(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_postings, "adzuna_keys_configured", lambda: False)

    def boom(*a, **k):
        raise AssertionError("Adzuna should not be called without keys")

    monkeypatch.setattr(fetch_postings, "fetch_adzuna_postings", boom)
    config = {
        "target_roles": ["Data Analyst"], "greenhouse_boards": [], "lever_boards": [],
        "usajobs": {"enabled": False},
        "canada_job_bank": {"enabled": False},
    }
    summary = run_pipeline.run(config=config, db_path=tmp_path / "run.db", save_raw=False)
    assert summary["fetched"] == 0


def test_run_pipeline_fetches_usajobs_when_configured(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_postings, "adzuna_keys_configured", lambda: False)
    monkeypatch.setattr(fetch_postings, "usajobs_credentials_configured", lambda: True)
    monkeypatch.setattr(fetch_postings, "fetch_usajobs_postings", lambda *args: USAJOBS_RAW)
    config = {
        "target_roles": ["Data Analyst"],
        "greenhouse_boards": [],
        "lever_boards": [],
        "usajobs": {"enabled": True, "max_pages_per_role": 1, "results_per_page": 50},
        "canada_job_bank": {"enabled": False},
    }
    summary = run_pipeline.run(config=config, db_path=tmp_path / "usajobs.db", save_raw=False)
    assert summary["fetched"] == summary["unique"] == summary["written"] == 1
    assert load_postings(db_path=tmp_path / "usajobs.db")[0]["source"] == "usajobs"


def test_run_pipeline_fetches_job_bank_once_for_configured_roles(tmp_path, monkeypatch):
    monkeypatch.setattr(fetch_postings, "adzuna_keys_configured", lambda: False)
    monkeypatch.setattr(fetch_postings, "usajobs_credentials_configured", lambda: False)
    calls = []

    def fake_fetch_job_bank():
        calls.append(True)
        return {"csv_text": JOB_BANK_CSV, "resource_name": "October"}

    monkeypatch.setattr(fetch_postings, "fetch_canada_job_bank_postings", fake_fetch_job_bank)
    config = {
        "target_roles": ["Data Analyst", "Bartender"],
        "greenhouse_boards": [],
        "lever_boards": [],
        "usajobs": {"enabled": False},
        "canada_job_bank": {"enabled": True},
    }
    db = tmp_path / "canada.db"
    summary = run_pipeline.run(config=config, db_path=db, save_raw=False)
    assert calls == [True]
    assert summary["fetched"] == 2
    assert {posting["source"] for posting in load_postings(db_path=db)} == {"canada_job_bank"}


def test_fetch_usajobs_uses_registered_api_credentials(monkeypatch):
    monkeypatch.setenv("USAJOBS_API_KEY", "test-api-key")
    monkeypatch.setenv("USAJOBS_USER_AGENT", "user@example.com")
    captured = {}

    class Response:
        status_code = 200
        ok = True

        @staticmethod
        def json():
            return USAJOBS_RAW

    def fake_get(url, headers, params, timeout):
        captured.update(url=url, headers=headers, params=params, timeout=timeout)
        return Response()

    monkeypatch.setattr(fetch_postings._SESSION, "get", fake_get)
    assert fetch_postings.fetch_usajobs_postings("Data Analyst", 2, 40) == USAJOBS_RAW
    assert captured["url"] == "https://data.usajobs.gov/api/Search"
    assert captured["headers"]["Authorization-Key"] == "test-api-key"
    assert captured["headers"]["User-Agent"] == "user@example.com"
    assert captured["params"] == {"Keyword": "Data Analyst", "ResultsPerPage": 40, "Page": 2}


@pytest.mark.parametrize("encoding", ["utf-8", "utf-16"])
def test_fetch_canada_job_bank_uses_latest_official_csv(monkeypatch, encoding):
    calls = []

    class Response:
        status_code = 200
        ok = True

        def __init__(self, value=None, content=b""):
            self.value = value
            self.content = content

        def raise_for_status(self):
            return None

        def json(self):
            return self.value

    metadata = {
        "success": True,
        "result": {
            "resources": [
                {"format": "CSV", "name": "Older", "last_modified": "2026-10-01",
                 "language": ["en"], "url": "old.csv"},
                {"format": "CSV", "name": "Latest", "last_modified": "2026-11-04",
                 "language": ["en"], "url": "latest.csv"},
                {"format": "CSV", "name": "Latest French", "last_modified": "2026-11-05",
                 "language": ["fr"], "url": "latest-fr.csv"},
            ],
        },
    }

    def fake_get(url, timeout):
        calls.append(url)
        if "package_show" in url:
            return Response(value=metadata)
        return Response(content=JOB_BANK_CSV.encode(encoding))

    monkeypatch.setattr(fetch_postings._SESSION, "get", fake_get)
    result = fetch_postings.fetch_canada_job_bank_postings()
    assert result["resource_name"] == "Latest"
    assert result["resource_url"] == "latest.csv"
    assert result["csv_text"] == JOB_BANK_CSV
    assert calls[-1] == "latest.csv"


# ---------- Adzuna salary, market data, and key safety ----------

ADZUNA_WITH_SALARY = {
    "count": 1234,
    "mean": 81000.5,
    "results": [
        {
            "id": "9001", "title": "Data Analyst",
            "company": {"display_name": "Maple Data"}, "location": {"display_name": "Toronto"},
            "created": "2026-09-25T00:00:00Z", "redirect_url": "https://www.adzuna.ca/details/9001",
            "description": "SQL and Tableau", "salary_min": 70000, "salary_max": 90000,
            "salary_is_predicted": "1", "contract_time": "full_time",
            "category": {"label": "IT Jobs"},
        },
    ],
}


def test_normalize_adzuna_keeps_salary_fields():
    posting = normalize_adzuna(ADZUNA_WITH_SALARY, "Data Analyst")[0]
    assert (posting["salary_min"], posting["salary_max"]) == (70000.0, 90000.0)
    assert posting["salary_is_predicted"] is True
    assert posting["contract_time"] == "full_time"
    assert posting["category"] == "IT Jobs"
    # non-Adzuna sources get the same keys, empty
    lever = normalize_lever(LEVER_RAW, "netflix", "Data Analyst")[0]
    assert lever["salary_min"] is None and lever["salary_is_predicted"] is False


def test_salary_fields_round_trip_through_sqlite(db):
    insert_postings(normalize_adzuna(ADZUNA_WITH_SALARY, "Data Analyst"), db)
    stored = load_postings(db_path=db)[0]
    assert stored["salary_max"] == 90000.0
    assert stored["salary_is_predicted"] is True


def test_geography_fields_round_trip_through_sqlite(db):
    posting = normalize_usajobs(USAJOBS_RAW, "Data Analyst")[0]
    insert_posting(posting, db)
    stored = load_postings(db_path=db)[0]
    assert stored["country"] == "United States"
    assert stored["region"] == "District of Columbia"


def test_backfill_posting_geography_for_existing_rows(db):
    posting = normalize_adzuna(ADZUNA_RAW, "Data Analyst")[0]
    insert_posting(posting, db)
    conn = sqlite3.connect(db)
    conn.execute(
        "UPDATE postings SET country = '', region = '', dedup_key = '' WHERE source_id = ?",
        (posting["source_id"],),
    )
    conn.commit()
    conn.close()
    backfill_posting_geography(db, {"adzuna": "ca"})
    stored = load_postings(db_path=db)[0]
    assert stored["country"] == "Canada"
    assert stored["region"] == "Ontario"


def test_pull_adzuna_returns_market_snapshot_and_passes_filters(monkeypatch):
    calls = []

    def fake_fetch(role, country, per_page, page, max_days_old=None, where=None):
        calls.append((role, country, page, max_days_old, where))
        return ADZUNA_WITH_SALARY

    monkeypatch.setattr(fetch_postings, "fetch_adzuna_postings", fake_fetch)
    cfg = {"country": "ca", "results_per_page": 50, "max_pages_per_role": 3,
           "max_days_old": 30, "where": "Toronto"}
    postings, market = run_pipeline.pull_adzuna("Data Analyst", cfg, save_raw=False)
    assert len(postings) == 1
    assert market == {"total_count": 1234, "mean_salary": 81000.5}
    assert calls == [("Data Analyst", "ca", 1, 30, "Toronto")]  # short page -> stop


def test_adzuna_errors_never_leak_api_keys(monkeypatch):
    import requests

    monkeypatch.setenv("ADZUNA_APP_ID", "my-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "super-secret-key")

    def broken_get(url, params=None, timeout=None):
        raise requests.ConnectionError(
            f"Max retries exceeded with url: /v1/api/jobs/ca/search/1?app_id={params['app_id']}"
            f"&app_key={params['app_key']}&what=x"
        )

    monkeypatch.setattr(fetch_postings._SESSION, "get", broken_get)
    with pytest.raises(fetch_postings.AdzunaError) as error:
        fetch_postings.fetch_adzuna_postings("Data Analyst")
    assert "super-secret-key" not in str(error.value)
    assert "my-id" not in str(error.value)
    assert isinstance(error.value, requests.RequestException)  # run_pipeline still catches it


def test_adzuna_bad_keys_give_a_clear_message(monkeypatch):
    class Response:
        status_code = 401
        ok = False

    monkeypatch.setenv("ADZUNA_APP_ID", "my-id")
    monkeypatch.setenv("ADZUNA_APP_KEY", "wrong")
    monkeypatch.setattr(fetch_postings._SESSION, "get", lambda *a, **k: Response())
    with pytest.raises(fetch_postings.AdzunaError, match="rejected the API keys"):
        fetch_postings.fetch_adzuna_postings("Data Analyst")
