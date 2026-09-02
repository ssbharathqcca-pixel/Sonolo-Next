"""Scenario catalog API (SN-015): the mobile session launcher's data."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.scenario import Scenario
from app.models.user import (
    SUBSCRIPTION_FREE,
    PreferredLanguage,
    User,
)

router = APIRouter(prefix="/scenarios", tags=["scenarios"])


class ScenarioOut(BaseModel):
    """One practice scenario as shown in the mobile catalog."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    title: str
    description: str
    category: str
    #: BCP-47 content language of the scenario's pack; the mobile Learn
    #: tab matches it against pack languages for card badges (SN-030).
    target_language: str
    #: Manifest pack the scenario belongs to; the Learn tab filters the
    #: catalog to one pack with it (SN-035).
    pack_id: str | None
    difficulty: int | None
    #: True when the scenario is premium and the caller is on the free
    #: tier (SN-026) — the mobile client renders a paywall for these.
    is_locked: bool = False


class ScenarioListResponse(BaseModel):
    """The published scenario catalog."""

    scenarios: list[ScenarioOut]


@router.get("", response_model=ScenarioListResponse)
async def list_scenarios(
    current_user: User = Depends(get_current_user),
    limit: int = Query(default=110, ge=1, le=110),
    language: PreferredLanguage | None = Query(default=None),
    db: AsyncSession = Depends(get_db),
) -> ScenarioListResponse:
    """Return published scenarios for one content language (SN-020).

    An explicit `language` query param wins; otherwise the caller's
    `preferred_language` applies. Codes match exact packs ("fr") and
    regional variants ("en" also matches "en-CA"). SN-026 premium
    gating is applied after filtering, so it holds in every language.
    """
    selected = language.value if language is not None else current_user.preferred_language
    normalized = selected.strip().lower()
    result = await db.execute(
        select(Scenario)
        .where(Scenario.is_published.is_(True))
        .where(Scenario.unit_id.is_(None))
        .where(
            or_(
                func.lower(Scenario.target_language) == normalized,
                func.lower(Scenario.target_language).like(f"{normalized}-%"),
            )
        )
        .order_by(Scenario.title.asc())
        .limit(limit)
    )
    scenarios = list(result.scalars().all())
    is_free_tier = current_user.subscription_tier == SUBSCRIPTION_FREE
    return ScenarioListResponse(
        scenarios=[
                ScenarioOut(
                    id=scenario.id,
                    title=scenario.title,
                    description=scenario.description,
                    category=scenario.category,
                    target_language=scenario.target_language,
                    pack_id=scenario.pack_id,
                    difficulty=scenario.difficulty,
                    is_locked=bool(scenario.is_premium and is_free_tier),
                )
            for scenario in scenarios
        ]
    )
