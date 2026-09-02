"""C9 Progress skill-level assembly.

Reads C0 ``user_skill_levels`` and calls C2 for display level, readiness
level, and §5.6 imbalance. Does not reimplement those formulas.
Does not use ``users.current_level``, XP, or speaking sub-dimensions.
"""

from __future__ import annotations

from typing import Any, Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import UserSkillLevel
from app.services.mastery_service import (
    SKILLS,
    display_level,
    get_skill_recommendation,
    readiness_level,
)


def skill_levels_or_default(rows: Mapping[str, int]) -> dict[str, int]:
    """C0 default level is 1 when a skill row has not been written yet.

    Same missing-row behaviour as C6 Daily Mix. Does not persist rows.
    """
    return {skill: int(rows.get(skill, 1)) for skill in SKILLS}


def build_skill_progress(levels: Mapping[str, int]) -> dict[str, Any]:
    """Pure C2 view of four-skill progress. ``levels`` keyed by skill name."""
    complete = skill_levels_or_default(levels)
    recommendation = get_skill_recommendation(complete)
    return {
        "skills": [
            {"skill": skill, "level": complete[skill]} for skill in SKILLS
        ],
        "display_level": display_level(complete),
        "readiness_level": readiness_level(complete),
        "imbalance": {
            "priority": recommendation.priority,
            "skill": recommendation.skill,
            "message": recommendation.message,
            "daily_mix_weight": recommendation.daily_mix_weight,
        },
    }


async def load_skill_progress_for_user(
    db: AsyncSession, user_id: UUID
) -> dict[str, Any]:
    rows = (
        await db.execute(
            select(UserSkillLevel).where(UserSkillLevel.user_id == user_id)
        )
    ).scalars().all()
    levels = {row.skill: row.sonolo_level for row in rows if row.skill in SKILLS}
    return build_skill_progress(levels)
