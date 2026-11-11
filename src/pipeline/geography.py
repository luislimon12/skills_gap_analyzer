"""Normalize country and state/province values from posting locations."""

import re

CANADIAN_REGIONS = {
    "AB": ("Alberta", "AB"),
    "BC": ("British Columbia", "BC"),
    "MB": ("Manitoba", "MB"),
    "NB": ("New Brunswick", "NB"),
    "NL": ("Newfoundland and Labrador", "NL"),
    "NS": ("Nova Scotia", "NS"),
    "NT": ("Northwest Territories", "NT"),
    "NU": ("Nunavut", "NU"),
    "ON": ("Ontario", "ON"),
    "PE": ("Prince Edward Island", "PE"),
    "QC": ("Quebec", "QC"),
    "SK": ("Saskatchewan", "SK"),
    "YT": ("Yukon", "YT"),
}
US_REGIONS = {
    "AL": ("Alabama", "AL"), "AK": ("Alaska", "AK"), "AZ": ("Arizona", "AZ"),
    "AR": ("Arkansas", "AR"), "CA": ("California", "CA"), "CO": ("Colorado", "CO"),
    "CT": ("Connecticut", "CT"), "DE": ("Delaware", "DE"),
    "DC": ("District of Columbia", "DC"), "FL": ("Florida", "FL"),
    "GA": ("Georgia", "GA"), "HI": ("Hawaii", "HI"), "ID": ("Idaho", "ID"),
    "IL": ("Illinois", "IL"), "IN": ("Indiana", "IN"), "IA": ("Iowa", "IA"),
    "KS": ("Kansas", "KS"), "KY": ("Kentucky", "KY"), "LA": ("Louisiana", "LA"),
    "ME": ("Maine", "ME"), "MD": ("Maryland", "MD"), "MA": ("Massachusetts", "MA"),
    "MI": ("Michigan", "MI"), "MN": ("Minnesota", "MN"), "MS": ("Mississippi", "MS"),
    "MO": ("Missouri", "MO"), "MT": ("Montana", "MT"), "NE": ("Nebraska", "NE"),
    "NV": ("Nevada", "NV"), "NH": ("New Hampshire", "NH"),
    "NJ": ("New Jersey", "NJ"), "NM": ("New Mexico", "NM"), "NY": ("New York", "NY"),
    "NC": ("North Carolina", "NC"), "ND": ("North Dakota", "ND"),
    "OH": ("Ohio", "OH"), "OK": ("Oklahoma", "OK"), "OR": ("Oregon", "OR"),
    "PA": ("Pennsylvania", "PA"), "RI": ("Rhode Island", "RI"),
    "SC": ("South Carolina", "SC"), "SD": ("South Dakota", "SD"),
    "TN": ("Tennessee", "TN"), "TX": ("Texas", "TX"), "UT": ("Utah", "UT"),
    "VT": ("Vermont", "VT"), "VA": ("Virginia", "VA"), "WA": ("Washington", "WA"),
    "WV": ("West Virginia", "WV"), "WI": ("Wisconsin", "WI"), "WY": ("Wyoming", "WY"),
}

_COUNTRIES = {"ca": "Canada", "canada": "Canada", "us": "United States",
              "usa": "United States", "united states": "United States"}


def _region_from_location(location: str, country: str) -> str:
    regions = CANADIAN_REGIONS if country == "Canada" else US_REGIONS
    lower = location.casefold()
    for name, code in regions.values():
        if re.search(rf"\b{re.escape(name.casefold())}\b", lower):
            return name
    # Abbreviations are only recognized as a location suffix to avoid treating
    # ordinary words such as "on" as province codes.
    for code, (name, _) in regions.items():
        if re.search(rf"(?:,|\s)\s*{code}\s*(?:,|$)", location, flags=re.IGNORECASE):
            return name
    return ""


def normalize_geography(
    location: str,
    country_hint: str | None = None,
    region_hint: str | None = None,
) -> tuple[str, str]:
    """Return canonical country and region names, leaving unknown values blank."""
    country = _COUNTRIES.get((country_hint or "").strip().casefold(), "")
    lower = location.casefold()
    if not country:
        if re.search(r"\b(canada|canadian)\b", lower):
            country = "Canada"
        elif re.search(r"\b(united states|u\.s\.a?\.?|america)\b", lower):
            country = "United States"
        elif any(re.search(rf"\b{re.escape(name.casefold())}\b", lower)
                 for name, _ in CANADIAN_REGIONS.values()):
            country = "Canada"
        elif any(re.search(rf"\b{re.escape(name.casefold())}\b", lower)
                 for name, _ in US_REGIONS.values()):
            country = "United States"
        else:
            for code in set(CANADIAN_REGIONS) | set(US_REGIONS):
                if not re.search(rf"(?:,|\s)\s*{code}\s*(?:,|$)", location, flags=re.IGNORECASE):
                    continue
                in_canada = code in CANADIAN_REGIONS
                in_us = code in US_REGIONS
                if in_canada != in_us:
                    country = "Canada" if in_canada else "United States"
                    break

    region = ""
    if country:
        regions = CANADIAN_REGIONS if country == "Canada" else US_REGIONS
        hint = (region_hint or "").strip().upper()
        if hint in regions:
            region = regions[hint][0]
        else:
            region = _region_from_location(location, country)
    return country, region
