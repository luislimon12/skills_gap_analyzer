"""Case-insensitive, boundary-aware matching against the local skill taxonomy.

The taxonomy is built from checked-in CSVs:

- ``starter_skills.csv`` — curated tech/data/professional skills.
- ``general_skills.csv`` — curated skills for non-tech work: healthcare,
  trades, retail, hospitality, logistics, finance, admin, HR, education,
  plus Canadian licences and certifications.
  Both curated files are authoritative: their names and aliases always win.
- ``onet_skills.csv`` — technology skills imported from the free O*NET
  database by ``python -m src.scoring.taxonomy.setup_taxonomy``. Terms that
  are already claimed by a curated skill are dropped during the merge, so the
  supplement can only add coverage, never change curated matches.

CSV columns: ``skill_name``, ``category``, ``aliases`` (pipe-delimited) and an
optional ``case_sensitive_terms`` column (pipe-delimited) for short or
ambiguous terms such as ``R`` or ``C`` that must match with exact casing and
stricter boundaries (``R`` must not match inside ``R&D``).

An optional ``ambiguous_terms`` column (pipe-delimited) marks everyday words
that only mean the skill in the right context: "framing" is Carpentry on a
job site but not in "framing business questions". An ambiguous term counts
only when the same text also has an unambiguous match for another skill in
the same category (another trade skill, for Carpentry).
"""

import csv
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from src.common.text import clean_text

TAXONOMY_DIR = Path(__file__).resolve().parent / "taxonomy"
STARTER_TAXONOMY_PATH = TAXONOMY_DIR / "starter_skills.csv"
GENERAL_TAXONOMY_PATH = TAXONOMY_DIR / "general_skills.csv"
CURATED_TAXONOMY_PATHS = (STARTER_TAXONOMY_PATH, GENERAL_TAXONOMY_PATH)
ONET_TAXONOMY_PATH = TAXONOMY_DIR / "onet_skills.csv"

# Case-sensitive terms are short (R, C, Go...), so they get stricter edges:
# "R&D", "C-suite", "C++" and "C#" must not count as R or C.
_STRICT_BEFORE = r"(?<![\w&+#.\-])"
_STRICT_AFTER = r"(?![\w&+#'\-])"

# O*NET product names and aliases that are everyday English words. Matching
# them case-insensitively reads "meet deadlines" as Google Meet and "weekly
# reports" as Oracle Reports, so they are dropped from the O*NET supplement
# (a skill whose only terms are listed here is dropped altogether).
GENERIC_ONET_TERMS = frozenset(
    """
    ada ant appletree audition bamboo billing blink blogging calibration canu captivate classroom
    concur deployment distiller domino eiffel eko estimating exchange flex groovy inventor keynote
    litchi loom maya meet middleware optimization paint pascal recordkeeping reports seesaw
    simulation slides snort statistical struts tadpoles timekeeping videoconferencing
    """.split()
)


@dataclass(frozen=True)
class Skill:
    """Canonical skill name and equivalent terms used to recognize it."""

    name: str
    category: str = ""
    aliases: tuple[str, ...] = ()
    case_sensitive_terms: tuple[str, ...] = ()
    source: str = "curated"
    ambiguous_terms: tuple[str, ...] = ()

    @property
    def terms(self) -> tuple[str, ...]:
        """Every term that identifies this skill (name, aliases, exact-case and ambiguous terms)."""
        return tuple(
            dict.fromkeys((self.name, *self.aliases, *self.case_sensitive_terms, *self.ambiguous_terms))
        )


def _split_terms(value: str | None) -> tuple[str, ...]:
    return tuple(term.strip() for term in (value or "").split("|") if term.strip())


def _read_taxonomy(path: Path, source: str = "curated") -> list[Skill]:
    if not path.is_file():
        raise FileNotFoundError(f"Skill taxonomy file not found: {path}")

    skills = []
    with open(path, newline="", encoding="utf-8-sig") as taxonomy_file:
        reader = csv.DictReader(taxonomy_file)
        if not reader.fieldnames or "skill_name" not in reader.fieldnames:
            raise ValueError(f"Taxonomy file {path} must include a skill_name column.")
        for row in reader:
            name = clean_text(row.get("skill_name"))
            if not name:
                continue
            case_sensitive = _split_terms(row.get("case_sensitive_terms"))
            aliases = tuple(
                alias for alias in _split_terms(row.get("aliases")) if alias not in case_sensitive
            )
            skills.append(
                Skill(
                    name, clean_text(row.get("category")), aliases, case_sensitive, source,
                    _split_terms(row.get("ambiguous_terms")),
                )
            )
    return skills


def merge_taxonomies(primary: list[Skill], supplement: list[Skill]) -> list[Skill]:
    """Combine taxonomies; the primary list owns every term it defines.

    Supplement skills with the same canonical name contribute new,
    unclaimed aliases. Other supplement skills keep only unclaimed terms and
    are dropped when their own name is already claimed by another skill.
    """
    merged: dict[str, Skill] = {}
    owners: dict[str, str] = {}

    def claim(skill: Skill) -> None:
        for term in skill.terms:
            owners.setdefault(term.casefold(), skill.name.casefold())

    for skill in primary:
        key = skill.name.casefold()
        if key in merged:
            existing = merged[key]
            skill = Skill(
                existing.name,
                existing.category or skill.category,
                tuple(dict.fromkeys((*existing.aliases, *skill.aliases))),
                tuple(dict.fromkeys((*existing.case_sensitive_terms, *skill.case_sensitive_terms))),
                existing.source,
                tuple(dict.fromkeys((*existing.ambiguous_terms, *skill.ambiguous_terms))),
            )
        merged[key] = skill
        claim(skill)

    for skill in supplement:
        key = skill.name.casefold()
        free_aliases = tuple(a for a in skill.aliases if owners.get(a.casefold(), key) == key)
        if key in merged:
            existing = merged[key]
            merged[key] = Skill(
                existing.name,
                existing.category or skill.category,
                tuple(dict.fromkeys((*existing.aliases, *free_aliases))),
                existing.case_sensitive_terms,
                existing.source,
                existing.ambiguous_terms,
            )
        elif owners.get(key, key) == key:
            free_exact = tuple(
                t for t in skill.case_sensitive_terms if owners.get(t.casefold(), key) == key
            )
            merged[key] = Skill(skill.name, skill.category, free_aliases, free_exact, skill.source)
        else:
            continue
        claim(merged[key])

    return list(merged.values())


def load_taxonomy(path: str | Path | None = None) -> list[Skill]:
    """Load the curated taxonomies supplemented by the O*NET technology skills.

    Passing ``path`` loads only that CSV, which is useful for controlled tests
    and for deployments with a different taxonomy file.
    """
    if path is not None:
        return merge_taxonomies(_read_taxonomy(Path(path)), [])

    supplement = (
        _read_taxonomy(ONET_TAXONOMY_PATH, source="onet") if ONET_TAXONOMY_PATH.is_file() else []
    )
    return merge_taxonomies(load_curated(), [s for s in map(_without_generic_terms, supplement) if s])


def _without_generic_terms(skill: Skill) -> Skill | None:
    """Drop everyday-word terms (GENERIC_ONET_TERMS) from an O*NET skill; None if nothing is left."""
    def keep(term: str) -> bool:
        return term.casefold() not in GENERIC_ONET_TERMS

    aliases = tuple(filter(keep, skill.aliases))
    exact = tuple(filter(keep, skill.case_sensitive_terms))
    if keep(skill.name):
        return Skill(skill.name, skill.category, aliases, exact, skill.source, skill.ambiguous_terms)
    if not aliases:
        return None
    # The name is an everyday word; a specific alias still identifies the skill.
    return Skill(aliases[0], skill.category, aliases[1:], exact, skill.source, skill.ambiguous_terms)


def load_curated() -> list[Skill]:
    """All curated skills (tech first, then general), in file order."""
    return [skill for path in CURATED_TAXONOMY_PATHS if path.is_file() for skill in _read_taxonomy(path)]


def _term_pattern(term: str, case_sensitive: bool) -> re.Pattern:
    if case_sensitive:
        return re.compile(rf"{_STRICT_BEFORE}{re.escape(term)}{_STRICT_AFTER}")
    # Plural-tolerant: "reach trucks" matches the alias "reach truck".
    plural = "(?:s|es)?" if term[-1:].isalpha() and len(term) >= 4 else ""
    return re.compile(rf"(?<!\w){re.escape(term)}{plural}(?!\w)", re.IGNORECASE)


def _compile_patterns(skills: list[Skill]) -> list[tuple[Skill, re.Pattern]]:
    patterns = []
    owners: dict[str, str] = {}
    for skill in skills:
        for term in skill.terms:
            key = term.casefold()
            owner = owners.get(key)
            if owner is not None and owner != skill.name:
                raise ValueError(
                    f"Ambiguous taxonomy term {term!r} belongs to both {owner!r} and {skill.name!r}."
                )
            owners[key] = skill.name
            patterns.append((skill, _term_pattern(term, term in skill.case_sensitive_terms)))
    return patterns


def _ambiguous(skills: list[Skill]) -> frozenset[int]:
    """Positions in ``_compile_patterns(skills)`` whose term is one of its skill's ambiguous_terms."""
    terms = [(skill, term) for skill in skills for term in skill.terms]
    return frozenset(
        i for i, (skill, term) in enumerate(terms)
        if term.casefold() in {t.casefold() for t in skill.ambiguous_terms}
    )


_TOKEN = re.compile(r"[a-z0-9]+")


def _first_token(term: str) -> str:
    """First alphanumeric run of a term: the key used to prefilter candidate terms."""
    found = _TOKEN.search(term.lower())
    return found.group(0) if found else ""


def _index_patterns(
    skills: list[Skill], patterns: list[tuple[Skill, re.Pattern]]
) -> dict[str, list[tuple[int, Skill, re.Pattern]]]:
    """Group patterns by their term's first token.

    A term can only match where the text contains that token as a whole
    alphanumeric run, so each text is checked against a handful of
    candidate patterns instead of the whole taxonomy. ``patterns`` must be
    the output of ``_compile_patterns(skills)`` (one pattern per term, in order);
    each entry keeps its position in that list.
    """
    terms = [term for skill in skills for term in skill.terms]
    index: dict[str, list[tuple[int, Skill, re.Pattern]]] = {}
    for position, (term, (skill, pattern)) in enumerate(zip(terms, patterns, strict=True)):
        index.setdefault(_first_token(term), []).append((position, skill, pattern))
    return index


def _matched_skills(
    text: str,
    patterns: list[tuple[Skill, re.Pattern]],
    index: dict[str, list[tuple[int, Skill, re.Pattern]]] | None = None,
    order: dict[str, int] | None = None,
    ambiguous: frozenset[int] = frozenset(),
    corroborate: bool = True,
) -> list[Skill]:
    normalized = clean_text(text)
    if index is None:
        candidates = [(i, skill, pattern) for i, (skill, pattern) in enumerate(patterns)]
    else:
        tokens = set(_TOKEN.findall(normalized.lower()))
        candidates = [item for token in tokens & index.keys() for item in index[token]]
    matches: dict[str, Skill] = {}
    weak: dict[str, Skill] = {}
    for position, skill, pattern in candidates:
        key = skill.name.casefold()
        if key in matches or not pattern.search(normalized):
            continue
        if corroborate and position in ambiguous:
            weak[key] = skill
        else:
            matches[key] = skill
    # An ambiguous term needs an unambiguous match for another skill of the same category.
    categories = {skill.category for skill in matches.values() if skill.category}
    for key, skill in weak.items():
        if key not in matches and skill.category in categories:
            matches[key] = skill
    if order is not None:
        return sorted(matches.values(), key=lambda skill: order[skill.name.casefold()])
    return list(matches.values())


class SkillMatcher:
    """Reusable compiled matcher for scoring multiple postings efficiently."""

    def __init__(self, taxonomy_path: str | Path | None = None):
        self.skills, self.patterns, self._index, self._ambiguous = _compiled(
            str(taxonomy_path) if taxonomy_path is not None else None
        )
        self._order = {skill.name.casefold(): i for i, skill in enumerate(self.skills)}

    def match(self, text: str, corroborate: bool = True) -> list[Skill]:
        """Return matched taxonomy entries in their configured order.

        With ``corroborate=False`` ambiguous terms count on their own. Use it
        only on a fragment (one posting line) whose whole text was already
        matched with corroboration.
        """
        return _matched_skills(
            text, self.patterns, self._index, self._order, self._ambiguous, corroborate
        )

    def mask(self, text: str, replacement: str = " | ") -> str:
        """Return clean text with every taxonomy term occurrence replaced.

        Used to find what remains once known skills are removed.
        """
        normalized = clean_text(text)
        tokens = set(_TOKEN.findall(normalized.lower()))
        spans = [
            match.span()
            for token in tokens & self._index.keys()
            for _, _, pattern in self._index[token]
            for match in pattern.finditer(normalized)
        ]
        merged: list[list[int]] = []
        for start, end in sorted(spans):
            if merged and start <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], end)
            else:
                merged.append([start, end])
        for start, end in reversed(merged):
            normalized = normalized[:start] + replacement + normalized[end:]
        return normalized


@lru_cache(maxsize=8)
def _compiled(taxonomy_path: str | None):
    """Load and compile a taxonomy once per process (it is read-only)."""
    skills = load_taxonomy(taxonomy_path)
    patterns = _compile_patterns(skills)
    return skills, patterns, _index_patterns(skills, patterns), _ambiguous(skills)


def match_skills(
    text: str,
    taxonomy_path: str | Path | None = None,
) -> list[str]:
    """Return canonical taxonomy names found in text, independent of casing."""
    return [skill.name for skill in SkillMatcher(taxonomy_path).match(text)]


def find_skill_mentions(
    text: str,
    taxonomy_path: str | Path | None = None,
) -> list[dict]:
    """Return one match per skill with its first evidence text and category."""
    normalized = clean_text(text)
    matcher = SkillMatcher(taxonomy_path)
    matched = {skill.name.casefold() for skill in matcher.match(normalized)}
    found: dict[str, dict] = {}
    for skill, pattern in matcher.patterns:
        if skill.name.casefold() not in matched:
            continue
        match = pattern.search(normalized)
        if match and skill.name.casefold() not in found:
            found[skill.name.casefold()] = {
                "skill": skill.name,
                "category": skill.category,
                "evidence": normalized[max(0, match.start() - 45):match.end() + 45],
            }
    return list(found.values())
