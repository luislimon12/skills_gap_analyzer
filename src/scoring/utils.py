"""Shared scoring weights and date helpers."""

from datetime import date

RECENCY_WEIGHTS = {
    "under_30_days": 1.0,
    "30_to_90_days": 0.7,
    "over_90_days": 0.4,
}

REQUIREMENT_WEIGHTS = {
    "required": 1.0,
    "preferred": 0.5,
}

UNKNOWN_DATE_WEIGHT = 0.7


def recency_weight(posted_date: str | None, as_of: date | None = None) -> float:
    """Return the configured recency weight; unknown dates use the middle band."""
    if not posted_date:
        return UNKNOWN_DATE_WEIGHT
    try:
        posted = date.fromisoformat(str(posted_date)[:10])
    except ValueError as error:
        raise ValueError(f"Invalid posting date {posted_date!r}; expected YYYY-MM-DD.") from error

    age_days = ((as_of or date.today()) - posted).days
    if age_days < 30:
        return RECENCY_WEIGHTS["under_30_days"]
    if age_days <= 90:
        return RECENCY_WEIGHTS["30_to_90_days"]
    return RECENCY_WEIGHTS["over_90_days"]
