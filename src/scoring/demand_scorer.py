"""Per-skill posting demand, Wilson confidence intervals, and weighted demand."""

import re
from datetime import date

from src.scoring.skill_matcher import Skill, SkillMatcher
from src.scoring.utils import REQUIREMENT_WEIGHTS, recency_weight

_PREFERRED_CUES = re.compile(
    r"\b(preferred|preferably|nice to have|bonus|desirable|a plus|an asset|"
    r"would be advantageous|optional)\b",
    re.IGNORECASE,
)
_REQUIRED_HEADER_CUES = re.compile(
    r"\b(required|requirements|must have|qualifications|what you'll need|"
    r"what you will need|what you bring|minimum qualifications|essential)\b",
    re.IGNORECASE,
)
_PREFERRED_HEADER_CUES = re.compile(
    r"\b(preferred|nice to have|bonus|desired|additional qualifications|"
    r"good to have)\b",
    re.IGNORECASE,
)


def wilson_interval(successes: int, trials: int, z: float = 1.96) -> tuple[float, float]:
    """Return the two-sided Wilson score interval for a binomial proportion."""
    if trials < 0 or successes < 0 or successes > trials:
        raise ValueError("Wilson interval requires 0 <= successes <= trials.")
    if trials == 0:
        return 0.0, 0.0
    if z <= 0:
        raise ValueError("z must be positive.")

    proportion = successes / trials
    z_squared = z * z
    denominator = 1 + z_squared / trials
    center = (proportion + z_squared / (2 * trials)) / denominator
    margin = (
        z
        * (
            (proportion * (1 - proportion) / trials)
            + z_squared / (4 * trials * trials)
        ) ** 0.5
        / denominator
    )
    return max(0.0, center - margin), min(1.0, center + margin)


def demand_score(skill: str, postings: list[dict]) -> float:
    """Return the unweighted share of postings that mention a canonical skill."""
    if not postings:
        return 0.0
    matcher = SkillMatcher()
    key = skill.casefold()
    count = sum(
        key in {matched.name.casefold() for matched in matcher.match(_posting_text(posting))}
        for posting in postings
    )
    return count / len(postings)


def _posting_text(posting: dict) -> str:
    return "\n".join(
        part for part in (posting.get("title"), posting.get("full_text")) if part
    )


def _posting_skill_weights(posting: dict, matcher: SkillMatcher) -> dict[str, tuple[Skill, float]]:
    """Match a posting and infer preferred-only mentions from nearby headings/text."""
    requirement_by_skill: dict[str, float] = {}
    global_level = str(posting.get("requirement_level") or "").casefold()
    section_weight = REQUIREMENT_WEIGHTS.get(global_level, REQUIREMENT_WEIGHTS["required"])

    for line in (posting.get("full_text") or "").splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        preferred_header = _PREFERRED_HEADER_CUES.search(stripped)
        required_header = _REQUIRED_HEADER_CUES.search(stripped)
        if preferred_header and len(stripped.split()) <= 12:
            section_weight = REQUIREMENT_WEIGHTS["preferred"]
        elif required_header and len(stripped.split()) <= 12:
            section_weight = REQUIREMENT_WEIGHTS["required"]

        line_weight = (
            REQUIREMENT_WEIGHTS["preferred"]
            if _PREFERRED_CUES.search(stripped) or section_weight == REQUIREMENT_WEIGHTS["preferred"]
            else REQUIREMENT_WEIGHTS["required"]
        )
        for skill in matcher.match(stripped):
            key = skill.name.casefold()
            requirement_by_skill[key] = max(requirement_by_skill.get(key, 0.0), line_weight)

    for skill in matcher.match(posting.get("title") or ""):
        requirement_by_skill[skill.name.casefold()] = max(
            requirement_by_skill.get(skill.name.casefold(), 0.0),
            REQUIREMENT_WEIGHTS["required"],
        )

    return {
        skill.name.casefold(): (skill, requirement_by_skill[skill.name.casefold()])
        for skill in matcher.skills
        if skill.name.casefold() in requirement_by_skill
    }


def demand_profile(
    postings: list[dict],
    as_of: date | None = None,
    matcher: SkillMatcher | None = None,
) -> list[dict]:
    """Calculate sample demand, Wilson bounds, and weighted demand per skill.

    Every posting contributes to the sample denominator, including postings
    with empty descriptions. Unknown posting dates receive a neutral 30–90 day
    recency weight so they are not silently discarded.
    """
    if not postings:
        return []

    matcher = matcher or SkillMatcher()
    total_recency_weight = sum(recency_weight(p.get("posted_date"), as_of) for p in postings)
    by_skill: dict[str, dict] = {}

    for posting in postings:
        posting_recency = recency_weight(posting.get("posted_date"), as_of)
        for key, (skill, requirement_weight) in _posting_skill_weights(posting, matcher).items():
            record = by_skill.setdefault(
                key,
                {
                    "skill": skill.name,
                    "category": skill.category,
                    "count": 0,
                    "required_count": 0,
                    "preferred_count": 0,
                    "weighted_numerator": 0.0,
                },
            )
            record["count"] += 1
            if requirement_weight == REQUIREMENT_WEIGHTS["required"]:
                record["required_count"] += 1
            else:
                record["preferred_count"] += 1
            record["weighted_numerator"] += posting_recency * requirement_weight

    results = []
    for record in by_skill.values():
        lower, upper = wilson_interval(record["count"], len(postings))
        results.append(
            {
                **record,
                "demand": record["count"] / len(postings),
                "confidence_low": lower,
                "confidence_high": upper,
                "weighted_demand": record["weighted_numerator"] / total_recency_weight,
            }
        )
    return sorted(results, key=lambda row: (-row["weighted_demand"], row["skill"].casefold()))
