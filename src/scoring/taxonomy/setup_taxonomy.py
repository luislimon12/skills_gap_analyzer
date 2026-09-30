"""
Role 3 - Component TAX: cache the skill taxonomy locally.

Week 1 goal: get Lightcast + O*NET skill lists saved to CSV here so the
matcher (week 2) isn't hitting an API on every request.

Lightcast Open Skills requires free registration - see docs/sparknotes_main.md
for the link. Once you have credentials, add them to .env the same way as
the Adzuna keys, then fill in the fetch call below.
"""

import csv
from pathlib import Path

TAXONOMY_DIR = Path(__file__).resolve().parent


def write_lightcast_cache_stub() -> Path:
    """Placeholder cache with the right columns until the real Lightcast pull is wired up."""
    out_path = TAXONOMY_DIR / "lightcast_cache.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["lightcast_id", "skill_name", "category"])
    return out_path


def write_onet_mapping_stub() -> Path:
    """Placeholder O*NET occupation-to-skill baseline file."""
    out_path = TAXONOMY_DIR / "onet_mapping.csv"
    with open(out_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["onet_soc_code", "occupation_title", "skill_name", "importance"])
    return out_path


if __name__ == "__main__":
    lc = write_lightcast_cache_stub()
    onet = write_onet_mapping_stub()
    print(f"Taxonomy stubs created: {lc.name}, {onet.name}")
