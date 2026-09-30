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
"""

import re
from datetime import datetime, timezone

from src.common.text import clean_text, html_to_text
from src.pipeline.dedup_postings import make_posting_hash


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


def _epoch_ms_to_date(value: int | None) -> str | None:
    if not value:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).date().isoformat()


def _finish(posting: dict) -> dict:
    posting["requirement_level"] = None
    posting["unique_hash"] = make_posting_hash(posting["title"], posting["company"], posting["posted_date"] or "")
    return posting


def normalize_adzuna(raw: dict, role: str) -> list[dict]:
    """Adzuna search response -> postings. Descriptions are ~500-char excerpts."""
    postings = []
    for job in raw.get("results", []):
        postings.append(_finish({
            "source": "adzuna",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": clean_text(job.get("title")),
            "company": clean_text((job.get("company") or {}).get("display_name")),
            "location": clean_text((job.get("location") or {}).get("display_name")),
            "posted_date": _iso_to_date(job.get("created")),
            "url": job.get("redirect_url", ""),
            # Adzuna wraps matched query terms in <strong>, so it still needs HTML stripping
            "full_text": html_to_text(job.get("description")),
            "is_truncated": True,
        }))
    return postings


def normalize_greenhouse(raw: dict, board_token: str, role: str) -> list[dict]:
    """Greenhouse board response (fetched with content=true) -> postings matching the role."""
    postings = []
    for job in raw.get("jobs", []):
        title = clean_text(job.get("title"))
        if not title_matches_role(title, role):
            continue
        postings.append(_finish({
            "source": "greenhouse",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": title,
            "company": clean_text(job.get("company_name")) or board_token,
            "location": clean_text((job.get("location") or {}).get("name")),
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
        postings.append(_finish({
            "source": "lever",
            "source_id": str(job.get("id", "")),
            "role": role,
            "title": title,
            "company": company,
            "location": clean_text((job.get("categories") or {}).get("location")),
            "posted_date": _epoch_ms_to_date(job.get("createdAt")),
            "url": job.get("hostedUrl", ""),
            "full_text": _lever_text(job),
            "is_truncated": False,
        }))
    return postings
