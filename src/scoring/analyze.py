"""End-to-end resume versus role-posting scoring orchestration."""

from datetime import date
from pathlib import Path

from src.resume.experience_dates import extract_experience_entries, total_months
from src.resume.extract_sections import extract_sections
from src.scoring.demand_scorer import demand_profile
from src.scoring.fit_scorer import fit_score
from src.scoring.priority_scorer import priority_score
from src.scoring.skill_matcher import SkillMatcher


def analyze_role(
    resume_text: str,
    postings: list[dict],
    as_of: date | None = None,
    taxonomy_path: str | Path | None = None,
) -> dict:
    """Extract skills and score the resume against a role's posting sample.

    GapScore uses binary skill presence, as defined in the project spec.
    Dated experience is returned as supporting evidence; it does not alter
    GapScore until the team agrees on a years-of-experience weighting rule.
    """
    as_of = as_of or date.today()
    matcher = SkillMatcher(taxonomy_path)
    resume_skills = [skill.name for skill in matcher.match(resume_text)]
    resume_skill_keys = {skill.casefold() for skill in resume_skills}

    sections = extract_sections(resume_text)
    experience_entries = extract_experience_entries(sections["experience"], today=as_of)
    entries_by_skill: dict[str, list[dict]] = {}
    for entry in experience_entries:
        for skill in matcher.match(entry["text"]):
            entries_by_skill.setdefault(skill.name, []).append(entry)
    skill_experience_months = {
        skill: total_months(entries) for skill, entries in entries_by_skill.items()
    }

    demand = demand_profile(postings, as_of=as_of, matcher=matcher)
    demand_vector = {row["skill"]: row["weighted_demand"] for row in demand}
    resume_vector = {skill: float(skill.casefold() in resume_skill_keys) for skill in demand_vector}
    role_fit = fit_score(resume_vector, demand_vector)

    results = []
    for row in demand:
        has_skill = row["skill"].casefold() in resume_skill_keys
        experience_months = skill_experience_months.get(row["skill"], 0)
        results.append(
            {
                **row,
                "sample_size": len(postings),
                "has_skill": has_skill,
                "gap_score": priority_score(row["weighted_demand"], has_skill),
                "experience_months": experience_months,
                "experience_years": round(experience_months / 12, 1),
                "rationale": (
                    f"Found in {row['count']} of {len(postings)} postings "
                    f"({row['demand']:.0%}; weighted demand {row['weighted_demand']:.0%}, "
                    f"95% Wilson CI {row['confidence_low']:.0%}–{row['confidence_high']:.0%})."
                ),
            }
        )

    results.sort(key=lambda row: (-row["gap_score"], -row["weighted_demand"], row["skill"].casefold()))
    return {
        "postings_count": len(postings),
        "resume_skills": resume_skills,
        "experience_entries_count": len(experience_entries),
        "fit_score": role_fit * 100,
        "skills": results,
        "gaps": [row for row in results if row["gap_score"] > 0.0],
    }
