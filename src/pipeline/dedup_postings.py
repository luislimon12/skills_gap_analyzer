"""
Role 1 - Component C: dedup postings across sources.

The same posting often shows up on both Adzuna and a company's own
Greenhouse/Lever board. Postings are the same job when their normalized
title + company match and their posted dates are within a few days of
each other (sources disagree on the date by a day or two).

When duplicates collide, the copy with full text wins over Adzuna's
truncated excerpt, then the longer text wins.
"""

import hashlib
import re
from datetime import date

_COMPANY_SUFFIXES = re.compile(
    r"\b(inc|incorporated|ltd|limited|llc|llp|corp|corporation|co|company|plc|gmbh|ulc)\b\.?"
)


def normalize_title(title: str) -> str:
    return " ".join(re.findall(r"[a-z0-9+#]+", title.lower()))


def normalize_company(company: str) -> str:
    """"Shopify Inc." and "shopify" -> "shopify"."""
    name = _COMPANY_SUFFIXES.sub(" ", company.lower())
    return " ".join(re.findall(r"[a-z0-9]+", name))


def make_posting_hash(title: str, company: str, posted_date: str) -> str:
    """Build the unique_hash used in the postings table to catch duplicates."""
    raw = f"{normalize_title(title)}|{normalize_company(company)}|{(posted_date or '')[:10]}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _is_better(candidate: dict, current: dict) -> bool:
    if candidate.get("is_truncated") != current.get("is_truncated"):
        return not candidate.get("is_truncated")
    return len(candidate.get("full_text") or "") > len(current.get("full_text") or "")


def _days_apart(a: str | None, b: str | None) -> int:
    if not a or not b:
        return 0  # unknown date: don't let it block a title+company match
    return abs((date.fromisoformat(a[:10]) - date.fromisoformat(b[:10])).days)


def dedup_postings(postings: list[dict], date_window_days: int = 3) -> list[dict]:
    """Filter a list of posting dicts down to unique postings, keeping the best copy of each."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for posting in postings:
        key = (normalize_title(posting["title"]), normalize_company(posting["company"]))
        kept = groups.setdefault(key, [])
        for i, existing in enumerate(kept):
            if _days_apart(posting.get("posted_date"), existing.get("posted_date")) <= date_window_days:
                if _is_better(posting, existing):
                    kept[i] = posting
                break
        else:
            kept.append(posting)
    return [p for kept in groups.values() for p in kept]
