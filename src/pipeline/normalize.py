"""
Role 1 - Component C: turn raw Adzuna / Greenhouse / Lever responses into
one posting shape with clean plain text.

Every normalize_* function returns a list of posting dicts with these keys
(this is the contract Role 3 reads from the postings table):

    source            "adzuna" | "greenhouse" | "lever"
    source_id         the job's id at that source, as a string
    role              target role this posting was pulled for (e.g. "Data Analyst")
    title             job title as posted
    company           company display name
    location          free-text location, "" if unknown
    posted_date       "YYYY-MM-DD", or None if the source didn't say
    url               link to the posting
    full_text         clean plain text of the description (see src/common/text.py)
    is_truncated      True when full_text is only an excerpt (Adzuna)
    requirement_level None - left for Role 3 (required vs. preferred is per skill mention)
    unique_hash       see dedup_postings.make_posting_hash
    salary_min        advertised annual salary lower bound (Adzuna only), else None
    salary_max        advertised annual salary upper bound (Adzuna only), else None
    salary_is_predicted True when Adzuna estimated the salary instead of the employer posting it
    contract_time     "full_time" | "part_time" | None
    category          Adzuna job category label, "" if unknown
"""

import csv
import io
import re
from datetime import datetime, timezone

from src.common.text import clean_text, html_to_text
from src.pipeline.dedup_postings import make_posting_hash
from src.pipeline.geography import normalize_geography


def title_matches_role(title: str, role: str) -> bool:
    """True when every word of the role appears in the title, in any order.

    "Senior Data Analyst, Growth" and "Analyst, Data & Insights" both match
    "Data Analyst"; "Business Analyst" does not.
    """
    title_words = set(re.findall(r"[a-z0-9]+", title.lower()))
    role_words = re.findall(r"[a-z0-9]+", role.lower())
    return bool(role_words) and all(w in title_words for w in role_words)


def _iso_to_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return None


def _job_bank_date(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return datetime.strptime(value.strip(), "%Y/%m/%d").date().isoformat()
    except ValueError:
        return _iso_to_date(value)


def _epoch_ms_to_date(value: int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()


def _salary(value) -> float | None:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _job_bank_salary(value) -> float | None:
    if not value or str(value).strip().casefold() in {"na", "n/a"}:
        return None
    try:
        return float(str(value).replace("$", "").replace(",", "").strip())
    except ValueError:
        return None


def _finish(posting: dict) -> dict:
    for key in ("salary_min", "salary_max", "contract_time"):
        posting.setdefault(key, None)
    posting.setdefault("salary_is_predicted", False)
    posting.setdefault("category", "")
    posting.setdefault("country", "")
    posting.setdefault("region", "")
    posting["requirement_level"] = None
    posting["unique_hash"] = make_posting_hash(
        posting["title"], posting["company"], posting["posted_date"] or "",
        location=posting.get("location") or "", source=posting.get("source") or "",
        source_id=posting.get("source_id") or "", url=posting.get("url") or "",
    )
    return posting


def normalize_adzuna(raw: dict, role: str, country: str = "ca") -> list[dict]:
    """Adzuna search response -> postings. Descriptions are ~500-char excerpts."""
    postings = []
    for job in raw.get("results", []):
        location = clean_text((job.get("location") or {}).get("display_name"))
        country_name, region = normalize_geography(location, country_hint=country)
        postings.append(_finish({
            "source": "adzuna",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": clean_text(job.get("title")),
            "company": clean_text((job.get("company") or {}).get("display_name")),
            "location": location,
            "country": country_name,
            "region": region,
            "posted_date": _iso_to_date(job.get("created")),
            "url": job.get("redirect_url", ""),
            # Adzuna wraps matched query terms in <strong>, so it still needs HTML stripping
            "full_text": html_to_text(job.get("description")),
            "is_truncated": True,
            "salary_min": _salary(job.get("salary_min")),
            "salary_max": _salary(job.get("salary_max")),
            "salary_is_predicted": str(job.get("salary_is_predicted", "0")) == "1",
            "contract_time": job.get("contract_time"),
            "category": clean_text((job.get("category") or {}).get("label")),
        }))
    return postings


def normalize_greenhouse(raw: dict, board_token: str, role: str) -> list[dict]:
    """Greenhouse board response (fetched with content=true) -> postings matching the role."""
    postings = []
    for job in raw.get("jobs", []):
        title = clean_text(job.get("title"))
        if not title_matches_role(title, role):
            continue
        location = clean_text((job.get("location") or {}).get("name"))
        country, region = normalize_geography(location)
        postings.append(_finish({
            "source": "greenhouse",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": title,
            "company": clean_text(job.get("company_name")) or board_token,
            "location": location,
            "country": country,
            "region": region,
            "posted_date": _iso_to_date(job.get("first_published") or job.get("updated_at")),
            "url": job.get("absolute_url", ""),
            "full_text": html_to_text(job.get("content")),
            "is_truncated": False,
        }))
    return postings


def _lever_text(job: dict) -> str:
    parts = [job.get("descriptionPlain") or html_to_text(job.get("description"))]
    for section in job.get("lists") or []:
        parts.append(clean_text(section.get("text")))
        parts.append(html_to_text(section.get("content")))
    parts.append(job.get("additionalPlain") or html_to_text(job.get("additional")))
    return clean_text("\n\n".join(p for p in parts if p))


def normalize_lever(raw: list, company: str, role: str) -> list[dict]:
    """Lever postings response -> postings matching the role."""
    postings = []
    for job in raw:
        title = clean_text(job.get("text"))
        if not title_matches_role(title, role):
            continue
        location = clean_text((job.get("categories") or {}).get("location"))
        country, region = normalize_geography(location)
        postings.append(_finish({
            "source": "lever",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": title,
            "company": company,
            "location": location,
            "country": country,
            "region": region,
            "posted_date": _epoch_ms_to_date(job.get("createdAt")),
            "url": job.get("hostedUrl", ""),
            "full_text": _lever_text(job),
            "is_truncated": False,
        }))
    return postings


def _usajobs_text(job: dict) -> str:
    parts = [
        job.get("PositionSummary"),
        job.get("QualificationSummary"),
        job.get("JobSummary"),
    ]
    for section in job.get("PositionFormattedDescription") or []:
        if section.get("Label"):
            parts.append(section["Label"])
        if section.get("Content"):
            parts.append(html_to_text(section["Content"]))
    detail_rows = (job.get("UserArea") or {}).get("Details") or []
    if isinstance(detail_rows, dict):
        detail_rows = [
            {"Key": key, "Value": value} for key, value in detail_rows.items()
        ]
    relevant_detail_keys = (
        "dut", "responsib", "qualif", "require", "education", "experience",
        "skill", "assessment", "condition",
    )
    for detail in detail_rows:
        if not isinstance(detail, dict):
            continue
        key = str(detail.get("Key") or "")
        if any(part in key.casefold() for part in relevant_detail_keys):
            value = detail.get("Value")
            if isinstance(value, list):
                parts.extend(html_to_text(str(item)) for item in value)
            elif value:
                parts.append(html_to_text(str(value)))
    return clean_text("\n\n".join(str(part) for part in parts if part))


def normalize_usajobs(raw: dict, role: str) -> list[dict]:
    """USAJOBS Search API response -> postings matching the requested role."""
    search_result = raw.get("SearchResult") or {}
    postings = []
    for result in search_result.get("SearchResultItems") or []:
        job = result.get("MatchedObjectDescriptor") or {}
        title = clean_text(job.get("PositionTitle"))
        if not title_matches_role(title, role):
            continue
        locations = job.get("PositionLocation") or []
        location_names = [
            clean_text(item.get("LocationName") or item.get("CityName"))
            for item in locations
        ]
        location = "; ".join(name for name in location_names if name)
        first_location = locations[0] if locations else {}
        country, region = normalize_geography(
            location,
            country_hint=first_location.get("CountryCode") or "us",
            region_hint=first_location.get("CountrySubDivisionCode"),
        )
        postings.append(_finish({
            "source": "usajobs",
            "source_id": str(job.get("PositionID") or ""),
            "role": role,
            "title": title,
            "company": clean_text(job.get("OrganizationName")),
            "location": location,
            "country": country,
            "region": region,
            "posted_date": _iso_to_date(job.get("PublicationStartDate")),
            "url": job.get("PositionURI") or "",
            "full_text": _usajobs_text(job),
            "is_truncated": False,
        }))
    return postings


def normalize_canada_job_bank(raw: dict, role: str) -> list[dict]:
    """Official Job Bank monthly open-data CSV -> normalized postings."""
    csv_text = raw.get("csv_text") or ""
    delimiter = "\t" if "\t" in csv_text.partition("\n")[0] else ","
    reader = csv.DictReader(io.StringIO(csv_text), delimiter=delimiter)
    required = {"WIC Job Location Snapshot ID", "Job Title", "Province/Territory"}
    if not reader.fieldnames or not required.issubset(reader.fieldnames):
        raise ValueError(
            "The Job Bank CSV is missing expected columns: "
            + ", ".join(sorted(required - set(reader.fieldnames or [])))
        )

    postings = []
    for row in reader:
        title = clean_text(row.get("Job Title"))
        if not title_matches_role(title, role):
            continue
        city = clean_text(row.get("City"))
        region_hint = clean_text(row.get("Province/Territory"))
        location = ", ".join(part for part in (city, region_hint) if part and part.casefold() != "na")
        country, region = normalize_geography(
            location,
            country_hint="ca",
            region_hint=region_hint,
        )
        text_fields = (
            ("Original job title", row.get("Original Job Title")),
            ("Occupation", row.get("NOC21 Code Name") or row.get("NOC 2016 Code Name")),
            ("NOC code", row.get("NOC21 Code") or row.get("NOC 2016 Code")),
            ("Vacancies", row.get("Vacancy Count")),
            ("Education", row.get("Education LOS")),
            ("Experience", row.get("Experience Level")),
            ("Employment type", row.get("Employment Type")),
            ("Employment term", row.get("Employment Term")),
            ("Work hours", row.get("Work Hours")),
            ("Salary", row.get("Salary Condition Detail")),
            ("Benefits", row.get("Salary Condition Other Benefits")),
            ("Telework", row.get("Employment Term Telework")),
        )
        full_text = clean_text("\n".join(
            f"{label}: {value.strip()}"
            for label, value in text_fields
            if value and value.strip().casefold() not in {"na", "n/a"}
        ))
        salary_per = (row.get("Salary Per") or "").strip().casefold()
        contract = (row.get("Employment Type") or "").strip().casefold()
        postings.append(_finish({
            "source": "canada_job_bank",
            "source_id": str(row.get("WIC Job Location Snapshot ID") or "").strip(),
            "role": role,
            "title": title,
            "company": "",
            "location": location,
            "country": country,
            "region": region,
            "posted_date": _job_bank_date(row.get("First Posting Date")),
            "url": "",
            "full_text": full_text,
            "is_truncated": True,
            "salary_min": _job_bank_salary(row.get("Salary Minimum")) if salary_per == "year" else None,
            "salary_max": _job_bank_salary(row.get("Salary Maximum")) if salary_per == "year" else None,
            "salary_is_predicted": False,
            "contract_time": (
                "full_time" if "full time" in contract
                else "part_time" if "part time" in contract
                else None
            ),
            "category": clean_text(row.get("NOC21 Code Name") or row.get("NOC 2016 Code Name")),
        }))
    return postings
