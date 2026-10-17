"""O*NET occupation baseline: which technologies and tools an occupation uses.

The posting sample can be thin (or empty) for a role. O*NET's occupation-level
technology and tool lists are a stable, independent reference point, so the
dashboard shows them next to the posting-based demand. They never change
GapScore or FitScore, which stay purely posting-based.

Any typed role is mapped to O*NET occupations through ~60k O*NET job titles
(``match_occupations``), so the baseline works for every kind of job, not only
roles configured in settings.yaml.
"""

import csv
import gzip
import re
from functools import lru_cache
from pathlib import Path

from src.scoring.skill_matcher import TAXONOMY_DIR

ONET_MAPPING_PATH = TAXONOMY_DIR / "onet_mapping.csv.gz"
ONET_TITLES_PATH = TAXONOMY_DIR / "onet_titles.csv.gz"

_WORD = re.compile(r"[a-z0-9]+")
# Words that say little about the occupation itself.
_TITLE_NOISE = {"senior", "junior", "sr", "jr", "lead", "head", "i", "ii", "iii", "iv", "entry", "level",
                "the", "and", "of", "for", "a", "an", "to", "in", "full", "time", "part", "remote"}
# Common Canadian/role abbreviations that O*NET's U.S. titles don't spell out.
ROLE_ABBREVIATIONS = {
    "psw": "personal support worker", "rpn": "licensed practical nurse", "rn": "registered nurse",
    "lpn": "licensed practical nurse", "ece": "early childhood educator", "csr": "customer service representative",
    "az driver": "tractor trailer truck driver", "dz driver": "truck driver", "hr": "human resources",
    "it": "information technology", "ea": "educational assistant", "dsw": "developmental services worker",
    "cna": "certified nursing assistant", "pca": "personal care aide", "bi": "business intelligence",
}


def _read_rows(path: Path) -> list[dict]:
    path = Path(path)
    if not path.is_file():
        return []
    opener = gzip.open if path.suffix == ".gz" else open
    with opener(path, "rt", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


@lru_cache(maxsize=2)
def _cached_rows(path: str) -> tuple[dict, ...]:
    return tuple(_read_rows(Path(path)))


def load_onet_mapping(path: str | Path = ONET_MAPPING_PATH) -> list[dict]:
    """Read the occupation -> technology/tool mapping; [] when the file is missing."""
    return [row for row in _cached_rows(str(path)) if row.get("skill_name")]


def _singular(word: str) -> str:
    return word[:-1] if len(word) > 3 and word.endswith("s") and not word.endswith("ss") else word


def _title_words(title: str) -> tuple[str, ...]:
    """Lowercase, singular title words without seniority/filler words."""
    return tuple(_singular(w) for w in _WORD.findall(title.lower()) if w not in _TITLE_NOISE)


@lru_cache(maxsize=2)
def _title_index(path: str):
    """Title words -> occupations, plus an exact-title lookup."""
    exact: dict[str, set[str]] = {}
    entries: list[tuple[frozenset, str, str]] = []
    occupation_names: dict[str, str] = {}
    for row in _cached_rows(path):
        soc, title = row["onet_soc_code"], row["title"]
        if row.get("kind") == "occupation":
            occupation_names[soc] = title
        words = _title_words(title)
        if not words:
            continue
        exact.setdefault(" ".join(words), set()).add(soc)
        entries.append((frozenset(words), soc, title))
    return exact, entries, occupation_names


def match_occupations(role: str, limit: int = 3, titles_path: str | Path = ONET_TITLES_PATH) -> list[dict]:
    """Map a free-text role to the most likely O*NET occupations.

    An exact (normalized) title match wins. Otherwise titles are scored by word
    overlap with the role (Dice coefficient), and each occupation keeps its best
    title. Returns ``[{"soc": ..., "occupation": ..., "matched_title": ..., "score": ...}]``.
    """
    normalized = " ".join(_WORD.findall(role.lower()))
    for abbreviation, expansion in ROLE_ABBREVIATIONS.items():
        normalized = re.sub(rf"\b{re.escape(abbreviation)}\b", expansion, normalized)
    words = _title_words(normalized)
    if not words:
        return []
    exact, entries, names = _title_index(str(titles_path))
    role_set = frozenset(words)

    best: dict[str, tuple[float, str]] = {}
    # How many of an occupation's titles contain every role word: breaks ties
    # toward the occupation the role is most typical of.
    support: dict[str, int] = {}
    for title_words, soc, title in entries:
        overlap = len(role_set & title_words)
        if not overlap:
            continue
        if overlap == len(role_set):
            support[soc] = support.get(soc, 0) + 1
        score = 2 * overlap / (len(role_set) + len(title_words))
        if score > best.get(soc, (0.0, ""))[0]:
            best[soc] = (score, title)
    exact_socs = exact.get(" ".join(words), set())
    if exact_socs:
        best = {soc: (1.0, role) for soc in exact_socs}

    ranked = sorted(best.items(), key=lambda item: (-item[1][0], -support.get(item[0], 0), item[0]))
    if not ranked:
        return []
    top_score = ranked[0][1][0]
    return [
        {"soc": soc, "occupation": names.get(soc, soc), "matched_title": title, "score": round(score, 3)}
        for soc, (score, title) in ranked[:limit]
        if score >= 0.5 and score >= top_score - 0.2
    ]


def occupation_codes_for_role(role: str, role_map: dict | None = None, limit: int = 3) -> list[str]:
    """O*NET-SOC codes for a role: settings.yaml override first, else title matching."""
    for configured_role, codes in (role_map or {}).items():
        if configured_role.casefold() == role.strip().casefold():
            return [str(code) for code in (codes or [])]
    return [match["soc"] for match in match_occupations(role, limit=limit)]


def occupation_choices(soc_codes: list[str] | None = None) -> dict[str, str]:
    """All O*NET occupations as {soc: title} (for a picker), optionally limited to codes."""
    _, _, names = _title_index(str(ONET_TITLES_PATH))
    if soc_codes is None:
        return dict(sorted(names.items(), key=lambda item: item[1]))
    return {soc: names.get(soc, soc) for soc in soc_codes}


def _mentions(term: str, text: str) -> bool:
    """Literal, plural-tolerant mention check ("Forklifts" matches "forklift")."""
    words = term.lower().split()
    if not words or not text:
        return False
    last = words[-1]
    stem = last[:-1] if last.endswith("s") and len(last) > 3 else last
    pattern = r"(?<!\w)" + r"\s+".join(map(re.escape, words[:-1] + [stem])) + r"(?:s|es)?(?!\w)"
    return re.search(pattern, text.lower()) is not None


def onet_baseline(
    soc_codes: list[str],
    resume_skills: list[str] | None = None,
    mapping: list[dict] | None = None,
    resume_text: str = "",
    kind: str | None = "technology",
) -> list[dict]:
    """Aggregate the O*NET technologies (or tools) listed for the given occupations.

    Returns one row per item with how many of the selected occupations list
    it, whether O*NET flags it as Hot Technology / In Demand, and whether the
    resume shows it (taxonomy match, or a literal mention in ``resume_text``).
    ``kind`` is "technology", "tool", or None for both. Rows are ordered by
    occupation coverage, then In Demand, then Hot Technology, then name.
    """
    if not soc_codes:
        return []
    mapping = load_onet_mapping() if mapping is None else mapping
    wanted = set(soc_codes)
    have = {skill.casefold() for skill in resume_skills or []}

    by_skill: dict[str, dict] = {}
    for row in mapping:
        if row["onet_soc_code"] not in wanted:
            continue
        row_kind = row.get("kind") or "technology"
        if kind is not None and row_kind != kind:
            continue
        key = row["skill_name"].casefold()
        record = by_skill.setdefault(
            key,
            {
                "skill": row["skill_name"],
                "kind": row_kind,
                "occupations": [],
                "hot_technology": False,
                "in_demand": False,
                "has_skill": key in have or _mentions(row["skill_name"], resume_text),
            },
        )
        title = row.get("occupation_title") or row["onet_soc_code"]
        if title not in record["occupations"]:
            record["occupations"].append(title)
        record["hot_technology"] |= row.get("hot_technology") == "Y"
        record["in_demand"] |= row.get("in_demand") == "Y"

    for record in by_skill.values():
        record["occupation_share"] = len(record["occupations"]) / len(wanted)
    return sorted(
        by_skill.values(),
        key=lambda r: (-r["occupation_share"], not r["in_demand"], not r["hot_technology"], r["skill"].casefold()),
    )
