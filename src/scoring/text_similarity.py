"""Small-sample posting similarity and exact keyword search helpers."""

import re

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity


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
