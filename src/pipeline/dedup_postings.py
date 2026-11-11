"""
Role 1 - Component C: dedup postings across sources.

The same posting can show up in multiple feeds. Stable source IDs and
canonical URLs are strongest; cross-source fallback matching also requires
the normalized title, employer, full location, and nearby posting dates.

When duplicates collide, the copy with full text wins over Adzuna's
truncated excerpt, then the longer text wins.
"""

import hashlib
import re
from datetime import date
from urllib.parse import urlsplit, urlunsplit

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|llp|corp|corporation|co|company|plc|gmbh|ulc)\b\.?"
)


def normalize_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", title.lower()))


def normalize_company(company: str) -> str:
    """"Shopify Inc." and "shopify" -> "shopify"."""
    name = _COMPANY_SUFFIXES.sub(" ", company.lower())
    return " ".join(re.findall(r"[a-z0-9]+", name))


def _canonical_url(url: str | None) -> str:
    if not url:
        return ""
    parsed = urlsplit(url.strip())
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, "", ""))


def _hash(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def make_posting_hash(
    title: str,
    company: str,
    posted_date: str,
    location: str = "",
    source: str = "",
    source_id: str = "",
    url: str = "",
) -> str:
    """Build a stable posting identity without conflating different locations."""
    if source and source_id:
        raw = f"source-id|{source.casefold()}|{source_id.strip()}"
    elif canonical_url := _canonical_url(url):
        raw = f"url|{canonical_url}"
    else:
        raw = (
            f"posting|{normalize_title(title)}|{normalize_company(company)}|"
            f"{' '.join(re.findall(r'[a-z0-9]+', location.casefold()))}|"
            f"{(posted_date or '')[:10]}"
        )
    return _hash(raw)


def _is_better(candidate: dict, current: dict) -> bool:
    if candidate.get("is_truncated") != current.get("is_truncated"):
        return not candidate.get("is_truncated")
    return len(candidate.get("full_text") or "") > len(current.get("full_text") or "")


def _days_apart(a: str | None, b: str | None) -> int:
    if not a or not b:
        return date.max.toordinal()
    try:
        return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)
    except ValueError:
        return date.max.toordinal()


def _same_source_identity(a: dict, b: dict) -> bool:
    return bool(
        a.get("source") and a.get("source") == b.get("source")
        and a.get("source_id") and a.get("source_id") == b.get("source_id")
    )


def _same_url(a: dict, b: dict) -> bool:
    return bool(_canonical_url(a.get("url")) and
                _canonical_url(a.get("url")) == _canonical_url(b.get("url")))


def _location_key(posting: dict) -> str:
    country = (posting.get("country") or "").casefold().strip()
    region = (posting.get("region") or "").casefold().strip()
    location = " ".join(re.findall(r"[a-z0-9]+", (posting.get("location") or "").casefold()))
    return "|".join((country, region, location))


def _same_posting(a: dict, b: dict, date_window_days: int) -> bool:
    if _same_source_identity(a, b) or _same_url(a, b):
        return True

    # Distinct IDs from the same provider are distinct vacancies, even if their
    # titles and locations happen to match.
    if (a.get("source") and a.get("source") == b.get("source")
            and a.get("source_id") and b.get("source_id")):
        return False

    title = normalize_title(a.get("title", ""))
    company = normalize_company(a.get("company", ""))
    if not title or not company:
        return False
    if title != normalize_title(b.get("title", "")) or company != normalize_company(b.get("company", "")):
        return False
    if not any(a.get(field) for field in ("country", "region", "location")):
        return False
    location = _location_key(a)
    if location != _location_key(b):
        return False
    return _days_apart(a.get("posted_date"), b.get("posted_date")) <= date_window_days


def postings_are_duplicates(a: dict, b: dict, date_window_days: int = 3) -> bool:
    """Public identity check shared by batch deduplication and persistent storage."""
    return _same_posting(a, b, date_window_days)


def make_dedup_key(posting: dict) -> str:
    """Index key for looking up possible cross-source matches in the database."""
    title = normalize_title(posting.get("title", ""))
    company = normalize_company(posting.get("company", ""))
    location = _location_key(posting)
    if not title or not company or not any(posting.get(field) for field in ("country", "region", "location")):
        return ""
    return _hash(f"fallback|{title}|{company}|{location}")


def dedup_postings(postings: list[dict], date_window_days: int = 3) -> list[dict]:
    """Filter a list of posting dicts down to unique postings, keeping the best copy of each."""
    kept_postings: list[dict] = []
    for posting in postings:
        for i, existing in enumerate(kept_postings):
            if _same_posting(posting, existing, date_window_days):
                if _is_better(posting, existing):
                    kept_postings[i] = posting
                break
        else:
            kept_postings.append(posting)
    return kept_postings
