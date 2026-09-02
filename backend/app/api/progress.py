"""C9 Progress skill-level API. C2 is the formula authority."""

from __future__ import annotations

from pydantic import BaseModel

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import User
from app.services.progress_service import load_skill_progress_for_user

router = APIRouter(prefix="/progress", tags=["progress"])


class SkillLevelOut(BaseModel):
    skill: str
    level: int


class ImbalanceOut(BaseModel):
    priority: str
    skill: str | None
    message: str
    daily_mix_weight: float | None


class SkillProgressOut(BaseModel):
    skills: list[SkillLevelOut]
    display_level: int
    readiness_level: int
    imbalance: ImbalanceOut


@router.get("/skills", response_model=SkillProgressOut)
async def get_skill_progress(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SkillProgressOut:
    """Four-skill levels from ``user_skill_levels`` + C2 display/readiness/§5.6."""
    payload = await load_skill_progress_for_user(db, current_user.id)
    return SkillProgressOut.model_validate(payload)
