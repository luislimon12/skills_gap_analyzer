"""Rank postings by how heavily they lean on a chosen set of skills.

The dashboard's skill explorer lets the user click the skills they have (or
want to be judged on). Each posting's skills are weighted the same way as
demand scoring: 1.0 for a required mention, 0.5 when every mention is
preferred/nice-to-have. A posting then gets two shares:

- ``coverage``: the share of the posting's skill weight that falls on the
  chosen skills (how much of what it asks for you bring);
- ``uses``: the share of the chosen skills the posting asks for, weighted by
  how strongly it asks (how much of your skill set the job would use).

``match`` is their harmonic mean, so a posting ranks high only when it both
centres on your skills and asks for many of them. Postings that mention none
of the chosen skills are left out.
"""

from src.scoring.demand_scorer import posting_skill_weights
from src.scoring.skill_matcher import SkillMatcher


def rank_postings_by_skills(
    postings: list[dict],
    chosen_skills: list[str],
    matcher: SkillMatcher | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Postings ordered by ``match`` (then coverage, then title).

    Returns ``[{"posting", "match", "coverage", "uses", "matched", "missing"}]``
    where ``matched``/``missing`` are skill names the posting asks for that are
    in/not in ``chosen_skills``, required ones first.
    """
    chosen = {skill.casefold() for skill in chosen_skills}
    if not postings or not chosen:
        return []
    matcher = matcher or SkillMatcher()

    results = []
    for posting in postings:
        weights = posting_skill_weights(posting, matcher)
        focus = sum(weight for key, (_, weight) in weights.items() if key in chosen)
        if not focus:
            continue
        coverage = focus / sum(weight for _, weight in weights.values())
        uses = focus / len(chosen)
        ordered = sorted(weights.values(), key=lambda item: (-item[1], item[0].name.casefold()))
        results.append(
            {
                "posting": posting,
                "match": 2 * coverage * uses / (coverage + uses),
                "coverage": coverage,
                "uses": uses,
                "matched": [skill.name for skill, _ in ordered if skill.name.casefold() in chosen],
                "missing": [skill.name for skill, _ in ordered if skill.name.casefold() not in chosen],
            }
        )
    results.sort(
        key=lambda row: (-row["match"], -row["coverage"], (row["posting"].get("title") or "").casefold())
    )
    return results if limit is None else results[:limit]
