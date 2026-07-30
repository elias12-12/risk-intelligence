"""Score -> band, per subject type, from the score_bands table.

Hardcoding 70/45 is what §6 warns against: a subject scored by three rules is
not comparable to one scored by one, so a single global line will drift as soon
as consolidation is real. The numbers live in a table so Week 4's calibration is
an UPDATE, not a release.
"""
from __future__ import annotations

from decimal import Decimal


def band_for(score: Decimal, subject_type: str, bands: dict[str, list[tuple[str, Decimal]]]) -> str:
    rows = bands.get(subject_type) or bands.get("transaction") or []
    for band, minimum in rows:            # already ordered by min_score DESC
        if score >= minimum:
            return band
    return "low"
