"""
Role 2 - Component D: split parsed resume text into sections.

A line is a section header when, after dropping punctuation and case, it is
exactly one of the known header phrases below ("TECHNICAL SKILLS", "Work
Experience:", "E X P E R I E N C E"). "Skills: Python, SQL" on one line is
split into the header and its content. Text before the first header (name,
contact line) goes to "contact". Unknown headers don't start a new section,
so their text stays with the section above them.
"""

import re

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "professional profile",
                "objective", "career objective", "about me"],
    "education": ["education", "academic background", "education and training", "academics"],
    "experience": ["experience", "work experience", "professional experience", "employment",
                   "employment history", "work history", "relevant experience", "career history"],
    "skills": ["skills", "technical skills", "core competencies", "key skills", "competencies",
               "skills and abilities", "technologies", "tools and technologies", "skills summary",
               "technical proficiencies"],
    "projects": ["projects", "personal projects", "academic projects", "key projects",
                 "selected projects"],
    "certifications": ["certifications", "certificates", "licenses and certifications",
                       "certifications and licenses", "courses and certifications"],
    "volunteer": ["volunteer experience", "volunteering", "volunteer work"],
    "awards": ["awards", "honors", "awards and honors", "honors and awards", "achievements"],
}

SECTION_KEYS = ["contact", *SECTION_ALIASES]

_HEADER_LOOKUP = {alias: key for key, aliases in SECTION_ALIASES.items() for alias in aliases}
_LONGEST_ALIAS_WORDS = max(len(a.split()) for a in _HEADER_LOOKUP)

# "Skills: Python, SQL" / "Technical Skills - Python, SQL"
_INLINE_HEADER = re.compile(
    r"^\s*(?P<header>" + "|".join(sorted(map(re.escape, _HEADER_LOOKUP), key=len, reverse=True))
    + r")\s*[:\-–—|]\s*(?P<rest>\S.*)$",
    re.IGNORECASE,
)


def _normalize_header(line: str) -> str:
    line = line.strip()
    # "E X P E R I E N C E" -> "EXPERIENCE" (single letters separated by single spaces)
    if re.fullmatch(r"(?:[A-Za-z] )+[A-Za-z]", line):
        line = line.replace(" ", "")
    line = line.lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z]+", line))


def section_for_header(line: str) -> str | None:
    """Return the section key if this line is a header on its own, else None."""
    normalized = _normalize_header(line)
    if not normalized or len(normalized.split()) > _LONGEST_ALIAS_WORDS:
        return None
    return _HEADER_LOOKUP.get(normalized)


def extract_sections(resume_text: str) -> dict:
    """Return {'contact': ..., 'summary': ..., 'education': ..., 'experience': ..., 'skills': ..., ...}.

    Every key in SECTION_KEYS is always present ("" when the resume has no such
    section). A section that appears twice is concatenated.
    """
    lines_by_section: dict[str, list[str]] = {key: [] for key in SECTION_KEYS}
    current = "contact"
    for line in resume_text.splitlines():
        key = section_for_header(line)
        if key:
            current = key
            continue
        inline = _INLINE_HEADER.match(line)
        if inline:
            current = _HEADER_LOOKUP[_normalize_header(inline.group("header"))]
            lines_by_section[current].append(inline.group("rest").strip())
            continue
        lines_by_section[current].append(line)
    return {key: "\n".join(lines).strip() for key, lines in lines_by_section.items()}
