"""
Role 1 - Component C: dedup postings across sources.

Week 2 task, not week 1. Left as a stub with the planned approach
so the rest of the team can see what's coming.

Plan: hash-based dedup on (title + company + posted_date), since the same
posting often shows up on both Adzuna and a company's own Greenhouse/Lever board.
"""

import hashlib


def make_posting_hash(title: str, company: str, posted_date: str) -> str:
    """Build the unique_hash used in the postings table to catch duplicates."""
    raw = f"{title.strip().lower()}|{company.strip().lower()}|{posted_date}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def dedup_postings(postings: list[dict]) -> list[dict]:
    """TODO (week 2): filter a list of posting dicts down to unique postings."""
    raise NotImplementedError("Dedup logic - build in week 2")
