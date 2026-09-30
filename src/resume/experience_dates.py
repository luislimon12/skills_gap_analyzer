"""
Role 2 - split the experience section into job entries with date ranges.

Feeds years-of-experience weighting: Role 3 matches skills inside each
entry's text, then calls total_months() on that skill's entries so
overlapping jobs aren't double counted.

Recognized ranges: "Jan 2022 – Present", "January 2021 to Mar 2023",
"05/2020 - 08/2021", "2019 - 2021", "Sept. 2023 – Current".
Year-only dates are treated as June (a midpoint guess), so "2019 - 2021" is 25 months.
"""

import re
from datetime import date

_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_MONTH = (r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?")
_DATE = rf"(?:{_MONTH}\s*,?\s*\d{{4}}|\d{{1,2}}\s*/\s*\d{{4}}|\d{{4}})"
_CURRENT = r"(?:present|current|now|today|ongoing)"
DATE_RANGE = re.compile(
    rf"(?<![\w/$])(?P<start>{_DATE})\s*(?:-|–|—|to|until)\s*(?P<end>{_DATE}|{_CURRENT})(?![\w/])",
    re.IGNORECASE,
)
_BULLET = re.compile(r"^\s*[-*•]\s")


def parse_month(text: str) -> tuple[int, int] | None:
    """"Mar 2023" / "03/2023" / "2023" -> (2023, 3). Year-only -> June."""
    text = text.strip().lower()
    if m := re.fullmatch(r"(\d{1,2})\s*/\s*(\d{4})", text):
        month, year = int(m.group(1)), int(m.group(2))
        return (year, month) if 1 <= month <= 12 else None
    if m := re.fullmatch(r"(\d{4})", text):
        return int(m.group(1)), 6
    if m := re.fullmatch(r"([a-z]+)\.?\s*,?\s*(\d{4})", text):
        month = _MONTHS.get(m.group(1)[:3])
        return (int(m.group(2)), month) if month else None
    return None


def months_between(start: tuple[int, int], end: tuple[int, int]) -> int:
    """Inclusive month count: Jan 2022 - Jan 2022 is 1 month."""
    return max(0, (end[0] - start[0]) * 12 + (end[1] - start[1]) + 1)


def _parse_range(match: re.Match, today: date) -> dict | None:
    start = parse_month(match.group("start"))
    is_current = bool(re.fullmatch(_CURRENT, match.group("end"), re.IGNORECASE))
    end = (today.year, today.month) if is_current else parse_month(match.group("end"))
    now = (today.year, today.month)
    # years outside 1950..this year rule out things like "2000 - 3000 units"
    if not start or not end or start[0] < 1950 or end[0] > now[0]:
        return None
    end = min(end, now)  # "2023 - 2026" read as June 2026 shouldn't run past today
    if start > now or end < start:
        return None
    return {
        "start": f"{start[0]:04d}-{start[1]:02d}",
        "end": f"{end[0]:04d}-{end[1]:02d}",
        "is_current": is_current,
        "months": months_between(start, end),
    }


def _looks_like_title(line: str) -> bool:
    line = line.strip()
    return bool(line) and not _BULLET.match(line) and len(line.split()) <= 12 and not DATE_RANGE.search(line)


def extract_experience_entries(experience_text: str, today: date | None = None) -> list[dict]:
    """Split an experience section into entries: [{header, start, end, is_current, months, text}].

    A line containing a date range starts a new entry. The line just above it is
    pulled into the entry too when it looks like a job title line (short, not a
    bullet) - the common "Data Analyst, Acme Corp" / "Jan 2022 - Present" layout.
    Text before the first dated line is dropped.
    """
    today = today or date.today()
    lines = [line.strip() for line in experience_text.splitlines()]
    entries: list[dict] = []
    current: dict | None = None
    preamble: list[str] = []  # lines before the first dated line

    for line in lines:
        match = DATE_RANGE.search(line)
        dates = _parse_range(match, today) if match else None
        if dates is None:
            (current["lines"] if current else preamble).append(line)
            continue

        header = [line]
        # The previous line can only be taken as this entry's title if it isn't
        # part of the current entry's own header.
        owner = current["lines"] if current else preamble
        if owner and len(owner) > (current["header_len"] if current else 0) and _looks_like_title(owner[-1]):
            header.insert(0, owner.pop())
        if current:
            entries.append(current)
        current = {**dates, "lines": header, "header_len": len(header)}

    if current:
        entries.append(current)

    return [
        {
            "header": " | ".join(e["lines"][:e["header_len"]]),
            "start": e["start"], "end": e["end"], "is_current": e["is_current"], "months": e["months"],
            "text": "\n".join(e["lines"]).strip(),
        }
        for e in entries
    ]


def _ym(value: str) -> tuple[int, int]:
    return int(value[:4]), int(value[5:7])


def _next_month(ym: tuple[int, int]) -> tuple[int, int]:
    year, month = ym
    return (year + 1, 1) if month == 12 else (year, month + 1)


def total_months(entries: list[dict]) -> int:
    """Total months covered by entries, merging overlaps (two concurrent jobs count once)."""
    total, cur_start, cur_end = 0, None, None
    for start, end in sorted((_ym(e["start"]), _ym(e["end"])) for e in entries):
        if cur_end is not None and start <= _next_month(cur_end):
            cur_end = max(cur_end, end)
            continue
        if cur_end is not None:
            total += months_between(cur_start, cur_end)
        cur_start, cur_end = start, end
    if cur_end is not None:
        total += months_between(cur_start, cur_end)
    return total
