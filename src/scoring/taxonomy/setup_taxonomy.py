"""
Role 3 - Component TAX: build the local skill taxonomy from O*NET (no Lightcast needed).

Lightcast Open Skills requires an approved account, so the taxonomy is built
from free sources instead:

1. ``starter_skills.csv`` and ``general_skills.csv`` - curated skills and
   hand-checked aliases (edited by hand, authoritative).
2. The O*NET database - the U.S. Department of Labor's occupation-level data
   for all ~1,000 occupations, licensed CC BY 4.0. This script downloads it
   and writes:

   - ``onet_skills.csv``     - O*NET technologies the curated lists do not cover
                               ("Hot Technology" or used by 5+ occupations)
   - ``onet_mapping.csv.gz`` - occupation -> technologies and physical tools,
                               used for the occupation baseline
   - ``onet_titles.csv.gz``  - occupation titles plus ~60k alternate and
                               reported job titles, used to map any typed
                               role ("PSW", "Forklift Operator") to occupations

Run from the project root (needs network access once; downloads are cached
in data/cache/onet/):

    python -m src.scoring.taxonomy.setup_taxonomy

Attribution: includes information from the O*NET 30.0 Database by the U.S.
Department of Labor, Employment and Training Administration (USDOL/ETA), used
under the CC BY 4.0 license. O*NET(R) is a trademark of USDOL/ETA.
"""

import argparse
import csv
import gzip
import re
from collections import defaultdict
from pathlib import Path

import requests

from src.scoring.skill_matcher import CURATED_TAXONOMY_PATHS, _read_taxonomy

TAXONOMY_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = TAXONOMY_DIR.parents[2]
CACHE_DIR = PROJECT_ROOT / "data" / "cache" / "onet"
ONET_SKILLS_PATH = TAXONOMY_DIR / "onet_skills.csv"
ONET_MAPPING_PATH = TAXONOMY_DIR / "onet_mapping.csv.gz"
ONET_TITLES_PATH = TAXONOMY_DIR / "onet_titles.csv.gz"

DEFAULT_VERSION = "30.0"
DOWNLOAD_URL = "https://www.onetcenter.org/dl_files/database/db_{version}_text/{name}"

# Prefix match on the O*NET-SOC code; "" means every occupation.
DEFAULT_SOC_PREFIXES = ("",)
# A non-"Hot" technology joins the matching taxonomy when this many occupations use it.
DEFAULT_MIN_OCCUPATIONS = 5
# Tools listed for more occupations than this are generic (computers, phones, printers).
MAX_TOOL_OCCUPATIONS = 60

ONET_SKILLS_COLUMNS = ["skill_name", "category", "aliases", "case_sensitive_terms", "onet_commodity_code"]
ONET_MAPPING_COLUMNS = [
    "onet_soc_code", "occupation_title", "kind", "skill_name", "onet_example",
    "hot_technology", "in_demand", "in_taxonomy",
]
ONET_TITLES_COLUMNS = ["onet_soc_code", "title", "kind"]

# Vendor prefixes that can be dropped to make a shorter alias ("Apache Kafka" -> "Kafka").
_VENDOR_PREFIXES = (
    "The MathWorks ", "Microsoft ", "Apache ", "Atlassian ", "IBM ", "Oracle ", "Google ",
    "Amazon ", "AWS ", "Adobe ", "Autodesk ", "Red Hat ", "Cisco ", "SAP ", "Apple ", "Eclipse ",
    "Intuit ", "Dassault Systemes ", "Trimble ", "Bentley ", "ESRI ",
)

# Leftovers that are ordinary words, so they would match ordinary prose.
_COMMON_WORDS = {
    "access", "analytics", "ads", "box", "chef", "cloud", "docs", "drive", "dynamics", "edge",
    "excel", "forms", "go", "jersey", "maps", "notes", "office", "outlook", "pig", "project",
    "publisher", "puppet", "search", "sheets", "shell", "sites", "slack", "spring", "storm",
    "teams", "windows", "word", "zoom", "database", "server", "studio", "visual", "basic",
    "primavera", "server pages", "workspace", "illustrator", "acrobat", "bridge", "hub",
    "canvas", "director", "general store", "store", "practice", "central", "pro", "one",
}
# Consumer apps and generic names that are not useful job skills.
_SKIP_EXAMPLES = {
    "facebook", "linkedin", "twitter", "instagram", "youtube", "zoom", "slack", "microsoft teams",
    "cisco webex", "google docs", "google sheets", "google drive", "microsoft outlook", "email software",
    "tiktok", "mozilla firefox", "apple safari", "google chrome", "microsoft edge",
    "microsoft internet explorer", "the general store", "web browser software",
}
_ACRONYM = re.compile(r"^[A-Z][A-Z0-9.&/\-]{1,}$")
_SHORT_ACRONYM = re.compile(r"^[A-Z][A-Z0-9&/\-]{1,4}$")


def _case_sensitive(terms: list[str]) -> str:
    """Short all-caps terms ("MRP", "FEA") only count with exact casing."""
    return "|".join(t for t in terms if _SHORT_ACRONYM.match(t))


def _abbreviates(acronym: str, phrase: str) -> bool:
    """True when the acronym's letters appear in order in the phrase ("SVN" in "Subversion").

    Keeps real abbreviations ("Structured query language SQL") apart from
    product suffixes that are not abbreviations ("eClinicalWorks EHR").
    """
    letters = re.sub(r"[^a-z]", "", acronym.lower())
    if not letters:
        return False
    remaining = iter(phrase.lower())
    return all(ch in remaining for ch in letters)


def download_onet_file(name: str, version: str = DEFAULT_VERSION, cache_dir: Path = CACHE_DIR) -> Path:
    """Download one O*NET text file once and return the cached path."""
    cache_dir = Path(cache_dir) / version
    path = cache_dir / name
    if path.is_file() and path.stat().st_size > 0:
        return path
    cache_dir.mkdir(parents=True, exist_ok=True)
    url = DOWNLOAD_URL.format(version=version.replace(".", "_"), name=requests.utils.quote(name))
    response = requests.get(url, timeout=60)
    response.raise_for_status()
    path.write_bytes(response.content)
    return path


def read_tsv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def clean_onet_example(
    example: str, generic_words: set[str] | frozenset = frozenset()
) -> tuple[str, list[str]] | None:
    """Turn an O*NET technology example into ``(skill_name, aliases)``.

    "Amazon Web Services AWS software" -> ("Amazon Web Services", ["AWS"])
    "Structured query language SQL"    -> ("SQL", ["Structured query language"])
    "Apache Kafka"                     -> ("Apache Kafka", ["Kafka"])
    "Database reporting software"      -> None (a category, not a product)
    "Tax software"                     -> None when "tax" is in ``generic_words``
    """
    text = example.replace("®", "").replace("™", "")
    text = text.replace("Amazon Web Services AWS ", "AWS ")  # "... AWS CloudFormation" -> "AWS CloudFormation"
    tokens = text.split()
    if len(tokens) > 1 and tokens[-1].lower() == "software":
        tokens = tokens[:-1]
    if not tokens:
        return None

    acronym = None
    if len(tokens) > 1 and _ACRONYM.match(tokens[-1]) and _abbreviates(tokens[-1], " ".join(tokens[:-1])):
        acronym, tokens = tokens[-1], tokens[:-1]

    phrase = " ".join(tokens)
    if len(tokens) == 1 and not acronym and tokens[0].lower() in generic_words:
        return None
    is_generic_phrase = len(tokens) > 1 and all(t.islower() for t in tokens[1:])
    if is_generic_phrase and not acronym:
        return None
    if phrase.casefold() in _SKIP_EXAMPLES or example.casefold() in _SKIP_EXAMPLES:
        return None

    if acronym and is_generic_phrase:
        name, aliases = acronym, [phrase]
    else:
        name, aliases = phrase, [acronym] if acronym else []

    for prefix in _VENDOR_PREFIXES:
        if name.startswith(prefix):
            short = name[len(prefix):].strip()
            if len(short) >= 3 and short.casefold() not in _COMMON_WORDS:
                aliases.append(short)
            break

    if example.strip() != name and example.strip() not in aliases:
        aliases.append(example.strip())
    if len(name) < 3 or name.casefold() in _COMMON_WORDS:
        return None
    aliases = [a for a in aliases if len(a) >= 2 and a.casefold() not in _COMMON_WORDS]
    return name, list(dict.fromkeys(aliases))


def _category(commodity_title: str) -> str:
    title = re.sub(r"\s+software$", "", commodity_title.strip(), flags=re.IGNORECASE)
    title = title.replace("Data base", "Database")
    return title[:1].upper() + title[1:]


def generic_vocabulary(tech_rows: list[dict]) -> set[str]:
    """Lowercase words used in O*NET commodity titles ("Tax preparation software").

    A one-word example that is just one of these words ("Tax software") names
    a category, not a product.
    """
    return {
        word
        for row in tech_rows
        for word in re.findall(r"[a-z]+", row.get("Commodity Title", "").lower())
        if len(word) > 2
    }


def _curated_owner(curated_path) -> dict[str, str]:
    if curated_path is None:
        paths = list(CURATED_TAXONOMY_PATHS)
    elif isinstance(curated_path, (str, Path)):
        paths = [curated_path]
    else:
        paths = list(curated_path)
    owner: dict[str, str] = {}
    for path in paths:
        if Path(path).is_file():
            for skill in _read_taxonomy(Path(path)):
                for term in skill.terms:
                    owner.setdefault(term.casefold(), skill.name)
    return owner


def build_onet_taxonomy(
    tech_rows: list[dict],
    occupation_titles: dict[str, str],
    curated_path: str | Path | list | None = None,
    soc_prefixes: tuple[str, ...] = DEFAULT_SOC_PREFIXES,
    hot_only: bool = True,
    min_occupations: int = DEFAULT_MIN_OCCUPATIONS,
) -> tuple[list[dict], list[dict]]:
    """Return ``(skill_rows, mapping_rows)`` built from O*NET Technology Skills rows.

    The matching taxonomy gets technologies that are O*NET "Hot Technology" or
    used by at least ``min_occupations`` occupations (every technology when
    ``hot_only`` is False). Entries already covered by a curated skill name or
    alias map to that curated skill, so the curated lists stay authoritative.
    The mapping keeps every recognizable technology of every selected
    occupation, flagged with ``in_taxonomy``.
    """
    owner = _curated_owner(curated_path)
    generic = generic_vocabulary(tech_rows)
    rows = [r for r in tech_rows if r.get("O*NET-SOC Code", "").startswith(tuple(soc_prefixes))]
    occupations_by_example: dict[str, set[str]] = defaultdict(set)
    hot_examples = set()
    for row in rows:
        occupations_by_example[row["Example"]].add(row["O*NET-SOC Code"])
        if row.get("Hot Technology") == "Y":
            hot_examples.add(row["Example"])

    def selected(example: str) -> bool:
        return (not hot_only or example in hot_examples
                or len(occupations_by_example[example]) >= min_occupations)

    skills: dict[str, dict] = {}
    in_taxonomy: set[str] = set(owner.values())
    canonical_by_example: dict[str, str] = {}
    # Selected examples first, so they claim their terms before niche ones.
    for row in sorted(rows, key=lambda r: not selected(r["Example"])):
        example = row["Example"]
        if example in canonical_by_example:
            continue
        cleaned = clean_onet_example(example, generic)
        if cleaned is None:
            continue
        name, aliases = cleaned
        terms = [name, *aliases]
        canonical = next((owner[t.casefold()] for t in terms if t.casefold() in owner), None)
        if canonical is None:
            canonical = name
            if selected(example):
                in_taxonomy.add(name)
                skills[name.casefold()] = {
                    "skill_name": name,
                    "category": _category(row.get("Commodity Title", "")),
                    "aliases": "|".join(aliases),
                    "case_sensitive_terms": _case_sensitive(terms),
                    "onet_commodity_code": row.get("Commodity Code", ""),
                }
        elif canonical.casefold() in skills and selected(example):
            existing = skills[canonical.casefold()]
            free = [a for a in aliases if owner.get(a.casefold(), canonical) == canonical]
            merged = dict.fromkeys([*filter(None, existing["aliases"].split("|")), *free])
            existing["aliases"] = "|".join(a for a in merged if a.casefold() != canonical.casefold())
            existing["case_sensitive_terms"] = _case_sensitive([canonical, *merged])
        for term in terms:
            owner.setdefault(term.casefold(), canonical)
        canonical_by_example[example] = canonical

    mapping: dict[tuple[str, str], dict] = {}
    for row in rows:
        canonical = canonical_by_example.get(row["Example"])
        if canonical is None:
            continue
        soc = row["O*NET-SOC Code"]
        key = (soc, canonical.casefold())
        previous = mapping.get(key) or {"hot_technology": "N", "in_demand": "N"}
        mapping[key] = {
            "onet_soc_code": soc,
            "occupation_title": occupation_titles.get(soc, ""),
            "kind": "technology",
            "skill_name": canonical,
            "onet_example": row["Example"],
            "hot_technology": "Y" if row.get("Hot Technology") == "Y" or previous["hot_technology"] == "Y" else "N",
            "in_demand": "Y" if row.get("In Demand") == "Y" or previous["in_demand"] == "Y" else "N",
            "in_taxonomy": "Y" if canonical in in_taxonomy else "N",
        }

    skill_rows = sorted(skills.values(), key=lambda r: r["skill_name"].casefold())
    mapping_rows = sorted(mapping.values(), key=lambda r: (r["onet_soc_code"], r["skill_name"].casefold()))
    return skill_rows, mapping_rows


def build_tool_mapping(
    tool_rows: list[dict],
    occupation_titles: dict[str, str],
    soc_prefixes: tuple[str, ...] = DEFAULT_SOC_PREFIXES,
    max_occupations: int = MAX_TOOL_OCCUPATIONS,
) -> list[dict]:
    """Occupation -> physical tool rows ("Forklifts", "Stethoscopes") for the baseline.

    Tools are a reference list only, not matching terms: O*NET tool names
    ("Claw hammers") rarely appear verbatim in postings.
    """
    rows = [r for r in tool_rows if r.get("O*NET-SOC Code", "").startswith(tuple(soc_prefixes))]
    occupations_by_tool: dict[str, set[str]] = defaultdict(set)
    for row in rows:
        occupations_by_tool[row["Example"]].add(row["O*NET-SOC Code"])
    mapping = {}
    for row in rows:
        tool = row["Example"].strip()
        if not tool or len(occupations_by_tool[row["Example"]]) > max_occupations:
            continue
        soc = row["O*NET-SOC Code"]
        mapping[(soc, tool.casefold())] = {
            "onet_soc_code": soc,
            "occupation_title": occupation_titles.get(soc, ""),
            "kind": "tool",
            "skill_name": tool,
            "onet_example": tool,
            "hot_technology": "N",
            "in_demand": "N",
            "in_taxonomy": "N",
        }
    return sorted(mapping.values(), key=lambda r: (r["onet_soc_code"], r["skill_name"].casefold()))


def build_title_index(
    occupation_rows: list[dict], alternate_rows: list[dict], reported_rows: list[dict]
) -> list[dict]:
    """All job titles O*NET knows for each occupation, deduplicated."""
    seen = set()
    titles = []
    sources = (
        (occupation_rows, "Title", "occupation"),
        (alternate_rows, "Alternate Title", "alternate"),
        (alternate_rows, "Short Title", "alternate"),
        (reported_rows, "Reported Job Title", "reported"),
    )
    for rows, column, kind in sources:
        for row in rows:
            title = " ".join((row.get(column) or "").split())
            soc = row.get("O*NET-SOC Code", "")
            if not title or title.lower() == "n/a" or (soc, title.casefold()) in seen:
                continue
            seen.add((soc, title.casefold()))
            titles.append({"onet_soc_code": soc, "title": title, "kind": kind})
    return titles


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> Path:
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wt", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="Build the O*NET taxonomy, mapping and title files.")
    parser.add_argument("--version", default=DEFAULT_VERSION, help="O*NET database version, e.g. 30.0")
    parser.add_argument("--soc-prefix", action="append", dest="soc_prefixes",
                        help="O*NET-SOC code prefix to include (repeatable). Default: every occupation.")
    parser.add_argument("--min-occupations", type=int, default=DEFAULT_MIN_OCCUPATIONS,
                        help="Add a non-Hot technology to the matching taxonomy when this many occupations use it.")
    parser.add_argument("--all-technologies", action="store_true",
                        help="Add every recognizable technology to the matching taxonomy (large, noisier).")
    args = parser.parse_args()
    prefixes = tuple(args.soc_prefixes or DEFAULT_SOC_PREFIXES)

    occupation_rows = read_tsv(download_onet_file("Occupation Data.txt", args.version))
    tech_rows = read_tsv(download_onet_file("Technology Skills.txt", args.version))
    tool_rows = read_tsv(download_onet_file("Tools Used.txt", args.version))
    alternate_rows = read_tsv(download_onet_file("Alternate Titles.txt", args.version))
    reported_rows = read_tsv(download_onet_file("Sample of Reported Titles.txt", args.version))
    titles = {row["O*NET-SOC Code"]: row["Title"] for row in occupation_rows}

    skill_rows, tech_mapping = build_onet_taxonomy(
        tech_rows, titles, soc_prefixes=prefixes,
        hot_only=not args.all_technologies, min_occupations=args.min_occupations,
    )
    tool_mapping = build_tool_mapping(tool_rows, titles, soc_prefixes=prefixes)
    title_rows = build_title_index(
        [r for r in occupation_rows if r["O*NET-SOC Code"].startswith(prefixes)],
        [r for r in alternate_rows if r["O*NET-SOC Code"].startswith(prefixes)],
        [r for r in reported_rows if r["O*NET-SOC Code"].startswith(prefixes)],
    )

    write_csv(ONET_SKILLS_PATH, ONET_SKILLS_COLUMNS, skill_rows)
    write_csv(ONET_MAPPING_PATH, ONET_MAPPING_COLUMNS, tech_mapping + tool_mapping)
    write_csv(ONET_TITLES_PATH, ONET_TITLES_COLUMNS, title_rows)
    print(f"Wrote {len(skill_rows)} new O*NET skills to {ONET_SKILLS_PATH.name}, "
          f"{len(tech_mapping)} technology + {len(tool_mapping)} tool rows to {ONET_MAPPING_PATH.name}, "
          f"and {len(title_rows)} job titles to {ONET_TITLES_PATH.name}.")


if __name__ == "__main__":
    main()
