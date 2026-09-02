"""
Role 3 - shared helpers for weight lookups etc. Filled in alongside the
scorers in week 3.
"""

RECENCY_WEIGHTS = {
    "under_30_days": 1.0,
    "30_to_90_days": 0.7,
    "over_90_days": 0.4,
}

REQUIREMENT_WEIGHTS = {
    "required": 1.0,
    "preferred": 0.5,
}
