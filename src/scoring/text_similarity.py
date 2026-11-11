"""Small-sample posting similarity and exact keyword search helpers."""

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

from src.scoring.skill_matcher import SkillMatcher


def rank_postings_by_skills(
    postings: list[dict],
    include_skills: list[str],
    exclude_skills: list[str] | None = None,
    skill_weights: dict[str, float] | None = None,
    matcher: SkillMatcher | None = None,
) -> list[dict]:
    """Rank postings by selected skill coverage, excluding any avoided skills.

    The weighted score is the fraction of selected-skill weight found in a
    posting. Results prioritize the number of matched skills, then that score.
    """
    if not postings or not include_skills:
        return []

    matcher = matcher or SkillMatcher()
    include = list(dict.fromkeys(skill.casefold() for skill in include_skills))
    exclude = {skill.casefold() for skill in (exclude_skills or [])}
    normalized_weights = {
        key.casefold(): value for key, value in (skill_weights or {}).items()
    }
    weights = {
        skill: max(0.0, float(normalized_weights.get(skill, 0.0)))
        for skill in include
    }
    if not any(weights.values()):
        weights = {skill: 1.0 for skill in include}
    total_weight = sum(weights.values())
    canonical_names = {skill.name.casefold(): skill.name for skill in matcher.skills}
    results = []

    for posting in postings:
        text = "\n".join(
            part for part in (posting.get("title"), posting.get("full_text")) if part
        )
        matched = {
            skill.name.casefold(): skill.name
            for skill in matcher.match(text)
        }
        if exclude.intersection(matched):
            continue
        matched_selected = [skill for skill in include if skill in matched]
        if not matched_selected:
            continue
        results.append(
            {
                "posting": posting,
                "matched_skills": [
                    canonical_names.get(skill, matched[skill]) for skill in matched_selected
                ],
                "matched_count": len(matched_selected),
                "total_skills": len(include),
                "weighted_score": sum(weights[skill] for skill in matched_selected) / total_weight,
            }
        )

    results.sort(
        key=lambda result: (
            -result["matched_count"],
            -result["weighted_score"],
            (result["posting"].get("title") or "").casefold(),
            (result["posting"].get("company") or "").casefold(),
        )
    )
    return results

def search_postings(
    query_text: str,
    postings: list[dict],
    keywords: list[str] | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Rank postings by TF-IDF cosine similarity and report exact keyword hits.

    Similarity is a separate text signal, not a skill match or a contribution
    to FitScore. Computation is local and intended for modest posting samples.
    """
    if not postings:
        return []

    descriptions = [
        " ".join(part for part in (posting.get("title"), posting.get("full_text")) if part)
        for posting in postings
    ]
    documents = [query_text or "", *descriptions]
    vectorizer = TfidfVectorizer(
        lowercase=True,
        strip_accents="unicode",
        stop_words="english",
        ngram_range=(1, 2),
        max_features=10_000,
        token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z0-9+#.]*\b",
    )
    analyzer = vectorizer.build_analyzer()
    if not any(analyzer(document) for document in documents):
        similarities = [0.0] * len(postings)
    else:
        matrix = vectorizer.fit_transform(documents)
        similarities = cosine_similarity(matrix[0:1], matrix[1:]).flatten().tolist()

    keyword_patterns = [
        (keyword, re.compile(rf"(?<!\w){re.escape(keyword)}(?!\w)", re.IGNORECASE))
        for keyword in (keywords or [])
        if keyword.strip()
    ]
    results = []
    for posting, similarity, description in zip(postings, similarities, descriptions):
        hits = [
            keyword
            for keyword, pattern in keyword_patterns
            if pattern.search(description)
        ]
        results.append(
            {
                "posting": posting,
                "similarity": float(similarity),
                "keyword_matches": hits,
            }
        )
    results.sort(
        key=lambda result: (
            -result["similarity"],
            -len(result["keyword_matches"]),
            (result["posting"].get("title") or "").casefold(),
        )
    )
    return results if limit is None else results[:limit]
