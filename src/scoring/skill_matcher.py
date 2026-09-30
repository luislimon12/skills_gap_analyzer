"""Case-insensitive, boundary-aware matching against the local skill taxonomy."""

import csv
import re
from dataclasses import dataclass
from pathlib import Path

from src.common.text import clean_text

TAXONOMY_DIR = Path(__file__).resolve().parent / "taxonomy"
STARTER_TAXONOMY_PATH = TAXONOMY_DIR / "starter_skills.csv"
LIGHTCAST_TAXONOMY_PATH = TAXONOMY_DIR / "lightcast_cache.csv"


@dataclass(frozen=True)
class Skill:
    """Canonical skill name and equivalent terms used to recognize it."""

    name: str
    category: str = ""
    aliases: tuple[str, ...] = ()


def _read_taxonomy(path: Path) -> list[Skill]:
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
            aliases = tuple(
                alias.strip()
                for alias in (row.get("aliases") or "").split("|")
                if alias.strip()
            )
            skills.append(Skill(name, clean_text(row.get("category")), aliases))
    return skills


def load_taxonomy(path: str | Path | None = None) -> list[Skill]:
    """Load the curated starter taxonomy, optionally supplemented by Lightcast.

    Passing ``path`` loads only that CSV, which is useful for controlled tests
    and for deployments with a downloaded taxonomy file.
    """
    if path is not None:
        return _read_taxonomy(Path(path))

    skills = _read_taxonomy(STARTER_TAXONOMY_PATH)
    if LIGHTCAST_TAXONOMY_PATH.is_file():
        skills.extend(_read_taxonomy(LIGHTCAST_TAXONOMY_PATH))

    unique: dict[str, Skill] = {}
    for skill in skills:
        key = skill.name.casefold()
        if key in unique:
            existing = unique[key]
            unique[key] = Skill(
                existing.name,
                existing.category or skill.category,
                tuple(dict.fromkeys((*existing.aliases, *skill.aliases))),
            )
        else:
            unique[key] = skill
    return list(unique.values())


def _compile_patterns(skills: list[Skill]) -> list[tuple[Skill, re.Pattern]]:
    patterns = []
    owners: dict[str, str] = {}
    for skill in skills:
        terms = tuple(dict.fromkeys((skill.name, *skill.aliases)))
        for term in terms:
            key = term.casefold()
            owner = owners.get(key)
            if owner is not None and owner != skill.name:
                raise ValueError(
                    f"Ambiguous taxonomy term {term!r} belongs to both {owner!r} and {skill.name!r}."
                )
            owners[key] = skill.name
            patterns.append(
                (
                    skill,
                    re.compile(rf"(?<!\w){re.escape(term)}(?!\w)", re.IGNORECASE),
                )
            )
    return patterns


def _matched_skills(text: str, patterns: list[tuple[Skill, re.Pattern]]) -> list[Skill]:
    normalized = clean_text(text)
    matches = {
        skill.name.casefold(): skill
        for skill, pattern in patterns
        if pattern.search(normalized)
    }
    return list(matches.values())


class SkillMatcher:
    """Reusable compiled matcher for scoring multiple postings efficiently."""

    def __init__(self, taxonomy_path: str | Path | None = None):
        self.skills = load_taxonomy(taxonomy_path)
        self.patterns = _compile_patterns(self.skills)

    def match(self, text: str) -> list[Skill]:
        """Return matched taxonomy entries in their configured order."""
        return _matched_skills(text, self.patterns)


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
    patterns = SkillMatcher(taxonomy_path).patterns
    found: dict[str, dict] = {}
    for skill, pattern in patterns:
        match = pattern.search(normalized)
        if match and skill.name.casefold() not in found:
            found[skill.name.casefold()] = {
                "skill": skill.name,
                "category": skill.category,
                "evidence": normalized[max(0, match.start() - 45):match.end() + 45],
            }
    return list(found.values())
