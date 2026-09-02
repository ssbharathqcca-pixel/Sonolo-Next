"""C8 Journey Map assembly.

Lock, current-unit, and completion states come from
``user_unit_progress.unit_test_passed`` on the sequential F1→A6 graph.
This is not C2 ``check_band_completion`` (band test + skill levels),
not ``users.sonolo_level``, and never ``users.current_level``.

Published content existence does not mark a unit complete.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import Unit, UserUnitProgress
from app.services.content_service import get_unit_document
from app.services.mastery_service import BANDS, SKILLS

BandId = Literal["foundation", "middle", "advanced"]
BandStatus = Literal["locked", "active", "completed"]
UnitStatus = Literal["locked", "current", "completed"]
SkillStatus = Literal["locked", "complete", "in_progress", "not_started"]

#: §14.3: Advanced at the top, Foundation at the bottom.
BAND_DISPLAY_ORDER: tuple[BandId, ...] = ("advanced", "middle", "foundation")

BAND_COPY: dict[BandId, dict[str, str]] = {
    "foundation": {
        "title": "Foundation Band",
        "subtitle": "First Steps",
        "icon": "🌱",
        "unlock_condition": "Complete the previous band to unlock",
    },
    "middle": {
        "title": "Middle Band",
        "subtitle": "Finding Your Voice",
        "icon": "🌿",
        "unlock_condition": "Complete Foundation Band to unlock",
    },
    "advanced": {
        "title": "Advanced Band",
        "subtitle": "Speaking with Power",
        "icon": "🌲",
        "unlock_condition": "Complete Middle Band to unlock",
    },
}

#: Canonical unit titles from Learning Architecture §3.
#: Published unit JSON overlays the title when present (F3).
UNIT_TITLES: dict[str, str] = {
    "F1": "Arrival Day",
    "F2": "Finding Home",
    "F3": "First Week",
    "F4": "Getting Help",
    "F5": "Money Matters",
    "F6": "Community",
    "M1": "First Job",
    "M2": "Workplace Life",
    "M3": "Government & Services",
    "M4": "Social Confidence",
    "M5": "Canadian Culture",
    "M6": "Health & Safety",
    "A1": "PR Readiness",
    "A2": "Professional Growth",
    "A3": "Complex Situations",
    "A4": "Academic English",
    "A5": "Media & Persuasion",
    "A6": "Life in Canada Mastery",
}


def unit_sequence() -> tuple[str, ...]:
    """Learning order: Foundation → Middle → Advanced, 6 units each."""
    codes: list[str] = []
    for band in ("foundation", "middle", "advanced"):
        units = BANDS[band]["units"]
        assert isinstance(units, tuple)
        codes.extend(str(code) for code in units)
    return tuple(codes)


UNIT_SEQUENCE: tuple[str, ...] = unit_sequence()


def band_units(band: BandId) -> tuple[str, ...]:
    units = BANDS[band]["units"]
    assert isinstance(units, tuple)
    return tuple(str(code) for code in units)


@dataclass(frozen=True)
class UnitProgressSnapshot:
    """C0 ``user_unit_progress`` flags for one unit code."""

    speaking_complete: bool = False
    listening_complete: bool = False
    reading_complete: bool = False
    writing_complete: bool = False
    unit_test_passed: bool = False

    def skill_complete(self, skill: str) -> bool:
        return bool(getattr(self, f"{skill}_complete"))

    def any_skill_complete(self) -> bool:
        return any(self.skill_complete(skill) for skill in SKILLS)


def unit_title(unit_code: str) -> str:
    """Prefer published catalog title; otherwise the canonical §3 title."""
    document = get_unit_document(unit_code)
    if document is not None:
        title = document.get("title")
        if title:
            return str(title)
    return UNIT_TITLES[unit_code]


def _passed(progress: Mapping[str, UnitProgressSnapshot], unit_code: str) -> bool:
    snapshot = progress.get(unit_code)
    return snapshot.unit_test_passed if snapshot is not None else False


def _unlocked(progress: Mapping[str, UnitProgressSnapshot], unit_code: str) -> bool:
    """F1 has no prerequisite. Every later unit requires the previous unit test."""
    sequence = UNIT_SEQUENCE
    index = sequence.index(unit_code)
    if index == 0:
        return True
    return _passed(progress, sequence[index - 1])


def _skill_status(
    *,
    unit_locked: bool,
    is_current: bool,
    snapshot: UnitProgressSnapshot,
    skill: str,
) -> SkillStatus:
    if unit_locked:
        return "locked"
    if snapshot.skill_complete(skill):
        return "complete"
    if is_current or snapshot.any_skill_complete():
        return "in_progress"
    return "not_started"


def current_unit_id(progress: Mapping[str, UnitProgressSnapshot]) -> str | None:
    """First unlocked unit whose unit test is not passed. None if all 18 passed."""
    for code in UNIT_SEQUENCE:
        if _unlocked(progress, code) and not _passed(progress, code):
            return code
    return None


def build_journey(
    progress: Mapping[str, UnitProgressSnapshot] | None = None,
) -> dict[str, Any]:
    """Pure Journey Map view. ``progress`` keyed by unit code (F1–A6)."""
    snapshots = progress or {}
    current = current_unit_id(snapshots)
    bands_out: list[dict[str, Any]] = []
    for band_id in BAND_DISPLAY_ORDER:
        codes = band_units(band_id)
        all_passed = all(_passed(snapshots, code) for code in codes)
        first_locked = not _unlocked(snapshots, codes[0])
        if all_passed:
            status: BandStatus = "completed"
        elif first_locked:
            status = "locked"
        else:
            status = "active"
        copy = BAND_COPY[band_id]
        units_out: list[dict[str, Any]] = []
        for code in codes:
            locked = not _unlocked(snapshots, code)
            passed = _passed(snapshots, code)
            if locked:
                unit_status: UnitStatus = "locked"
            elif passed:
                unit_status = "completed"
            elif code == current:
                unit_status = "current"
            else:
                unit_status = "locked"
            snapshot = snapshots.get(code) or UnitProgressSnapshot()
            is_current = unit_status == "current"
            units_out.append(
                {
                    "id": code,
                    "title": unit_title(code),
                    "status": unit_status,
                    "skills": [
                        {
                            "skill": skill,
                            "status": _skill_status(
                                unit_locked=locked,
                                is_current=is_current,
                                snapshot=snapshot,
                                skill=skill,
                            ),
                        }
                        for skill in SKILLS
                    ],
                }
            )
        bands_out.append(
            {
                "id": band_id,
                "title": copy["title"],
                "subtitle": copy["subtitle"],
                "icon": copy["icon"],
                "status": status,
                "expanded": status == "active",
                "unlock_condition": (
                    copy["unlock_condition"] if status == "locked" else None
                ),
                "units": units_out,
            }
        )
    return {"current_unit_id": current, "bands": bands_out}


async def load_journey_for_user(db: AsyncSession, user_id: UUID) -> dict[str, Any]:
    """Assemble the Journey Map from C0 progress rows. Does not persist catalog."""
    rows = (
        await db.execute(
            select(UserUnitProgress, Unit).join(
                Unit, UserUnitProgress.unit_id == Unit.id
            ).where(UserUnitProgress.user_id == user_id)
        )
    ).all()
    progress: dict[str, UnitProgressSnapshot] = {}
    for progress_row, unit in rows:
        progress[unit.unit_code] = UnitProgressSnapshot(
            speaking_complete=progress_row.speaking_complete,
            listening_complete=progress_row.listening_complete,
            reading_complete=progress_row.reading_complete,
            writing_complete=progress_row.writing_complete,
            unit_test_passed=progress_row.unit_test_passed,
        )
    return build_journey(progress)
