"""Find recurring requirement phrases in postings that the taxonomy doesn't cover.

No skill list covers every job. This module looks for short phrases (1-4
content words, split at stop words and punctuation, RAKE-style) that recur
across the posting sample, drops anything the taxonomy already recognizes,
and reports how often each appears and whether the resume mentions it.

Results are unverified text patterns, shown separately from taxonomy skills.
They never change GapScore or FitScore.
"""

import re

from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS

from src.common.text import clean_text
from src.scoring.skill_matcher import SkillMatcher

# Job-ad boilerplate that recurs everywhere and says nothing about the job.
_BOILERPLATE = {
    "ability", "able", "apply", "applicant", "applicants", "applications", "asset", "assets", "benefit",
    "benefits", "bonus", "candidate", "candidates", "career", "company", "competitive", "contract",
    "day", "days", "duties", "employee", "employees", "employer", "employment", "environment",
    "equal", "excellent", "experience", "experienced", "full", "good", "great", "hour", "hourly",
    "hours", "including", "job", "jobs", "join", "knowledge", "location", "looking", "minimum",
    "month", "months", "new", "offer", "opportunities", "opportunity", "paid", "part", "pay",
    "permanent", "plus", "position", "positions", "preferred", "qualifications", "related",
    "required", "requirements", "responsibilities", "role", "salary", "schedule", "skills",
    "strong", "team", "teams", "time", "today", "type", "week", "weekly", "work", "working",
    "year", "years", "yr", "yrs", "etc", "e.g", "i.e", "ideal", "including", "successful",
    "seeking", "hiring", "help", "include", "includes", "provide", "providing", "ensure",
    "support", "various", "related", "relevant", "per", "must", "will", "within", "well",
    "highly", "proven", "demonstrated", "previous", "current", "currently", "please", "click",
    "canada", "ontario", "toronto", "remote", "hybrid", "onsite", "site", "office", "based",
    "inc", "ltd", "llc", "corp", "us", "our", "we", "you", "your", "they", "their", "valid", "nice", "lbs", "kg", "lift", "must-have",
    "weekend", "weekends", "evenings", "nights", "training", "operate", "operating", "perform",
    "performing", "maintain", "maintaining", "assist", "assisting", "handle", "handling", "manage",
    "managing", "follow", "following", "use", "using", "responsible", "duties", "tasks", "task",
    "level", "levels", "high", "school", "diploma", "degree", "equivalent", "certificate", "asset",
    # Posting headings and listing furniture ("Job Description", "Overview", "Req ID").
    "description", "descriptions", "overview", "summary", "about", "details", "detail", "title",
    "posting", "postings", "posted", "reference", "req", "requisition", "id", "number", "apply",
    "applying", "application", "deadline", "status", "category", "categories", "listing", "vacancy",
    "openings", "opening", "immediate", "immediately", "start", "date", "now", "asap", "urgent",
    "urgently", "multiple", "available", "availability",
    # Employer-branding words ("world-class", "industry-leading", "passionate people").
    "world", "world-class", "worldwide", "global", "globally", "international", "national", "leading",
    "leader", "leaders", "industry", "industry-leading", "organization", "organizations", "organisation",
    "organisations", "businesses", "people", "culture", "mission", "vision", "values",
    "passionate", "passion", "dynamic", "exciting", "innovative", "innovation", "growing", "growth",
    "fast-paced", "diverse", "diversity", "inclusion", "inclusive", "equity", "accommodation",
    "accommodations", "accessibility", "disability", "disabilities", "life", "lives", "impact",
    "difference", "success", "successful", "best", "top", "key", "free", "make", "making", "better",
    "clients", "customers", "community", "communities", "family", "families",
    "proud", "rewarding", "unique", "exceptional", "outstanding", "award-winning", "premier",
    "largest", "large", "small", "local", "class", "member", "members", "staff", "individual",
    "individuals", "person", "someone", "everyone", "things", "thing", "way", "ways", "kind", "real",
    "real-world", "fun", "love", "like", "want", "need", "needs", "get", "make", "take", "come",
    "across", "around", "together", "every", "everyday", "daily", "also", "ideal", "ideally",
    "self", "today's", "tomorrow", "future", "world's", "north", "america", "american", "canadian",
    "canadians", "city", "region", "regional", "area", "areas", "gta", "province", "provincial",
    "ontario's", "vancouver", "montreal", "calgary", "ottawa", "edmonton", "mississauga",
    "problems", "problem", "scale", "model", "challenges", "challenging", "solutions", "level",
}
_STOP = frozenset(ENGLISH_STOP_WORDS) | _BOILERPLATE
_SPLIT = re.compile(r"[\n.;:!?,()\[\]{}|•·\"]+")
_WORD = re.compile(r"[a-z][a-z0-9+#'&/-]*")


def candidate_phrases(text: str, max_words: int = 4) -> set[str]:
    """Runs of 1..max_words content words between stop words and punctuation."""
    phrases = set()
    for chunk in _SPLIT.split(clean_text(text).lower()):
        run: list[str] = []
        for word in _WORD.findall(chunk) + [""]:  # sentinel flushes the last run
            word = word.strip("'/-&")
            if word and word not in _STOP and len(word) > 2:
                run.append(word)
                continue
            if 0 < len(run) <= max_words:
                phrases.add(" ".join(run))
            run = []
    return phrases


def _mentions(phrase: str, text: str) -> bool:
    return re.search(rf"(?<!\w){re.escape(phrase)}(?!\w)", text, re.IGNORECASE) is not None


def _posting_phrases(posting: dict, matcher: SkillMatcher) -> set[str]:
    """Candidate phrases of one description, minus the posting's own company and location.

    An employer's name or city recurs in every one of its postings
    ("Northwind Health", "Greater Toronto"), which says nothing about the job.
    """
    # Descriptions only: titles ("General Warehouse Associate") are not requirements.
    text = matcher.mask(posting.get("full_text") or "")
    for own in (posting.get("company"), posting.get("location")):
        for part in re.split(r"[,/|]", own or ""):
            part = part.strip()
            if len(part) > 2:
                text = re.sub(rf"(?<!\w){re.escape(part)}(?!\w)", " | ", text, flags=re.IGNORECASE)
    return candidate_phrases(text)


def discover_phrases(
    postings: list[dict],
    matcher: SkillMatcher | None = None,
    resume_text: str = "",
    role: str = "",
    min_share: float = 0.08,
    min_count: int = 3,
    limit: int = 25,
    background: list[dict] | None = None,
    min_lift: float = 2.0,
) -> list[dict]:
    """Recurring phrases in the posting sample that are not taxonomy skills.

    Returns ``[{"phrase", "count", "share", "in_resume"}]`` sorted by count,
    keeping phrases found in at least ``max(min_count, min_share * N)``
    postings. Words of the role title itself are ignored. When postings name
    their company, a phrase must recur across at least two employers.

    ``background`` is postings for other roles. When it holds at least 20
    postings, a phrase must be ``min_lift`` times more common in this role's
    sample than in the background, which drops job-ad filler ("description",
    "world-class team") that recurs whatever the job is.
    """
    if not postings:
        return []
    matcher = matcher or SkillMatcher()
    role_words = set(_WORD.findall(role.lower()))
    counts: dict[str, int] = {}
    companies: dict[str, set[str]] = {}
    for posting in postings:
        company = (posting.get("company") or "").strip().casefold()
        for phrase in _posting_phrases(posting, matcher):
            counts[phrase] = counts.get(phrase, 0) + 1
            if company:
                companies.setdefault(phrase, set()).add(company)

    threshold = max(min_count, min_share * len(postings))
    named_employers = len({(p.get("company") or "").strip().casefold() for p in postings} - {""})
    frequent = {
        phrase: count
        for phrase, count in counts.items()
        if count >= threshold
        and not set(phrase.split()) <= role_words
        and (named_employers < 2 or len(companies.get(phrase, ())) >= 2)
    }

    background = background or []
    if len(background) >= 20 and frequent:
        background_counts: dict[str, int] = {}
        for posting in background:
            for phrase in _posting_phrases(posting, matcher) & frequent.keys():
                background_counts[phrase] = background_counts.get(phrase, 0) + 1
        frequent = {
            phrase: count
            for phrase, count in frequent.items()
            # +1 smoothing so a phrase absent from the background still needs real support here.
            if (count / len(postings)) >= min_lift * (background_counts.get(phrase, 0) + 1) / (len(background) + 1)
        }
    # Taxonomy terms were masked out above; this catches split-up leftovers.
    frequent = {p: c for p, c in frequent.items() if not matcher.match(p)}
    # Drop a phrase when a longer phrase containing it is about as frequent.
    kept = {
        phrase: count
        for phrase, count in frequent.items()
        if not any(
            other != phrase and f" {phrase} " in f" {other} " and other_count >= 0.8 * count
            for other, other_count in frequent.items()
        )
    }
    rows = [
        {
            "phrase": phrase,
            "count": count,
            "share": count / len(postings),
            "in_resume": _mentions(phrase, resume_text),
        }
        for phrase, count in kept.items()
    ]
    rows.sort(key=lambda r: (-r["count"], -len(r["phrase"].split()), r["phrase"]))
    return rows[:limit]
