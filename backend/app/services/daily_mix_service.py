"""C6 Daily Mix v2 (Part XII). Selection only — C2 owns mastery, quests own XP."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from random import Random
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import get_local_date_for_user, utc_now
from app.models.curriculum import UserSkillLevel, UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.models.scenario import Scenario
from app.models.user import SUBSCRIPTION_PREMIUM, User
from app.models.vocabulary import VocabularyCard
from app.services.content_service import (
    load_listening_dialogues,
    load_reading_documents,
    load_unit_documents,
    load_vocabulary_hunt_documents,
    load_writing_documents,
    persist_curriculum,
)
from app.services.mastery_service import SKILLS, get_skill_recommendation

PRIORITY_HIGH = 0
PRIORITY_MEDIUM = 1
PRIORITY_NORMAL = 2

GOAL_MODIFIERS: dict[str, dict[str, float]] = {
    "pr_readiness": {"speaking": 0.05, "writing": 0.05},
    "workplace": {"speaking": 0.05, "writing": 0.05, "reading": 0.03},
    "casual": {"speaking": 0.08, "listening": 0.05},
    "academic": {"reading": 0.08, "writing": 0.08},
    "social": {"speaking": 0.10, "listening": 0.05},
}

LEGACY_GOAL_MAP = {
    "travel": "casual",
    "love": "casual",
}

DURATION_BY_SKILL = {
    "speaking": 10,
    "listening": 8,
    "reading": 8,
    "writing": 8,
}

XP_PER_SKILL_ITEM = 15
XP_VOCAB_ITEM = 15


@dataclass(frozen=True)
class MixCandidate:
    id: str
    skill: str
    title: str
    duration_minutes: int
    unit_code: str | None
    sonolo_level: int | None
    is_premium: bool
    source: str  # "unit" | "level" | "standalone"


@dataclass
class MixItem:
    type: str
    skill: str | None
    title: str
    duration_minutes: int
    priority: int
    content_id: str | None


@dataclass
class DailyMix:
    date: date
    items: list[MixItem]
    estimated_minutes: int
    focus_skill: str
    xp_possible: int
    weights: dict[str, float]
    welcome_back: bool
    imbalance: dict[str, Any]


def normalize_goal(goal: str | None) -> str:
    raw = (goal or "casual").strip().lower()
    return LEGACY_GOAL_MAP.get(raw, raw)


def deterministic_seed(user_id: UUID, local_date: date) -> int:
    digest = hashlib.sha256(f"{user_id}:{local_date.isoformat()}".encode()).digest()
    return int.from_bytes(digest[:8], "big")


def calculate_skill_weights(
    skill_levels: dict[str, int],
    goal: str,
    unfinished_blocks: list[str] | None = None,
    recent_errors: dict[str, int] | None = None,
) -> dict[str, float]:
    """Part XII §12.3. unfinished_blocks reserved for mix construction, not weights."""
    del unfinished_blocks
    weights = {skill: 0.25 for skill in SKILLS}
    levels = {skill: int(skill_levels.get(skill, 1)) for skill in SKILLS}
    min_level = min(levels.values())
    max_level = max(levels.values())
    gap = max_level - min_level
    for skill, level in levels.items():
        if level == min_level and gap >= 2:
            weights[skill] += 0.15
        elif level == min_level and gap >= 1:
            weights[skill] += 0.08
    for skill, boost in GOAL_MODIFIERS.get(normalize_goal(goal), {}).items():
        if skill in weights:
            weights[skill] += boost
    if recent_errors:
        error_skill = max(recent_errors, key=recent_errors.get)
        if error_skill in weights:
            weights[error_skill] += 0.05
    total = sum(weights.values()) or 1.0
    return {skill: value / total for skill, value in weights.items()}


def weighted_random_choice(weights: dict[str, float], rng: Random) -> str:
    skills = list(SKILLS)
    return rng.choices(skills, weights=[weights.get(skill, 0.0) for skill in skills], k=1)[0]


def _focus_skill(skill_levels: dict[str, int]) -> str:
    min_level = min(skill_levels[skill] for skill in SKILLS)
    return next(skill for skill in SKILLS if skill_levels[skill] == min_level)


def select_content_for_skill(
    skill: str,
    skill_level: int,
    current_unit: str | None,
    rng: Random,
    exclude: set[str],
    catalog: list[MixCandidate],
    allow_premium: bool,
    prefer_easier: bool = False,
) -> MixCandidate | None:
    """Part XII §12.5 fallback: unit → level → standalone."""

    def pick(pool: list[MixCandidate]) -> MixCandidate | None:
        eligible = [
            item
            for item in pool
            if item.id not in exclude
            and item.skill == skill
            and (allow_premium or not item.is_premium)
        ]
        if not eligible:
            return None
        if prefer_easier:
            eligible = sorted(
                eligible,
                key=lambda item: (item.sonolo_level or 99, item.duration_minutes),
            )
            return eligible[0]
        return rng.choice(eligible)

    if current_unit:
        found = pick(
            [item for item in catalog if item.source == "unit" and item.unit_code == current_unit]
        )
        if found:
            return found
    found = pick(
        [
            item
            for item in catalog
            if item.sonolo_level == skill_level or (item.source == "level" and item.sonolo_level == skill_level)
        ]
    )
    if found:
        return found
    found = pick([item for item in catalog if item.source == "standalone"])
    if found:
        return found
    return pick(catalog)


def generate_daily_mix(
    *,
    local_date: date,
    skill_levels: dict[str, int],
    goal: str,
    overdue_reviews: int,
    unfinished_blocks: list[str],
    recent_errors: dict[str, int],
    catalog: list[MixCandidate],
    rng: Random,
    allow_premium: bool,
    available_time_minutes: int = 20,
    welcome_back: bool = False,
    current_unit: str | None = None,
) -> DailyMix:
    levels = {skill: int(skill_levels.get(skill, 1)) for skill in SKILLS}
    weights = calculate_skill_weights(levels, goal, unfinished_blocks, recent_errors)
    if current_unit is None:
        current_unit = next(
            (item.unit_code for item in catalog if item.source == "unit" and item.unit_code),
            None,
        )
    mix: list[MixItem] = []

    if overdue_reviews > 0:
        mix.append(
            MixItem(
                type="vocab_review",
                skill=None,
                title=f"Vocabulary Review ({min(overdue_reviews, 20)} words)",
                duration_minutes=3,
                priority=PRIORITY_HIGH,
                content_id=None,
            )
        )

    prefer_easier = welcome_back
    for block in unfinished_blocks:
        if len(mix) >= 5:
            break
        content = select_content_for_skill(
            block,
            levels[block],
            current_unit,
            rng,
            {item.content_id for item in mix if item.content_id},
            catalog,
            allow_premium,
            prefer_easier=prefer_easier,
        )
        if content:
            mix.append(
                MixItem(
                    type=block,
                    skill=block,
                    title=content.title,
                    duration_minutes=content.duration_minutes,
                    priority=PRIORITY_MEDIUM,
                    content_id=content.id,
                )
            )

    remaining_slots = 5 - len(mix)
    for _ in range(remaining_slots):
        skill = weighted_random_choice(weights, rng)
        content = select_content_for_skill(
            skill,
            levels[skill],
            current_unit,
            rng,
            {item.content_id for item in mix if item.content_id},
            catalog,
            allow_premium,
            prefer_easier=prefer_easier,
        )
        if content:
            mix.append(
                MixItem(
                    type=skill,
                    skill=skill,
                    title=content.title,
                    duration_minutes=content.duration_minutes,
                    priority=PRIORITY_NORMAL,
                    content_id=content.id,
                )
            )

    skill_items = [item for item in mix if item.skill]
    if skill_items and all(item.skill == "speaking" for item in skill_items):
        replacement = select_content_for_skill(
            "listening",
            levels["listening"],
            current_unit,
            rng,
            {item.content_id for item in mix if item.content_id},
            catalog,
            allow_premium,
            prefer_easier=prefer_easier,
        ) or select_content_for_skill(
            "reading",
            levels["reading"],
            current_unit,
            rng,
            {item.content_id for item in mix if item.content_id},
            catalog,
            allow_premium,
            prefer_easier=prefer_easier,
        )
        if replacement:
            for index in range(len(mix) - 1, -1, -1):
                if mix[index].skill == "speaking":
                    mix[index] = MixItem(
                        type=replacement.skill,
                        skill=replacement.skill,
                        title=replacement.title,
                        duration_minutes=replacement.duration_minutes,
                        priority=PRIORITY_NORMAL,
                        content_id=replacement.id,
                    )
                    break

    mix.sort(key=lambda item: (item.priority, -weights.get(item.skill or "", 0.0)))
    total = sum(item.duration_minutes for item in mix)
    while total > available_time_minutes and len(mix) > 3:
        mix.pop()
        total = sum(item.duration_minutes for item in mix)

    rec = get_skill_recommendation(levels)
    xp_possible = sum(XP_PER_SKILL_ITEM for item in mix if item.skill) + (
        XP_VOCAB_ITEM if overdue_reviews else 0
    )
    return DailyMix(
        date=local_date,
        items=mix,
        estimated_minutes=total,
        focus_skill=_focus_skill(levels),
        xp_possible=xp_possible,
        weights=weights,
        welcome_back=welcome_back,
        imbalance={
            "priority": rec.priority,
            "skill": rec.skill,
            "message": rec.message,
        },
    )


def _published_unit_codes() -> list[str]:
    return [
        str(document["id"])
        for document in load_unit_documents()
        if document.get("is_published")
    ]


def build_content_catalog() -> list[MixCandidate]:
    catalog: list[MixCandidate] = []
    units = {str(document["id"]): document for document in load_unit_documents()}
    for document in load_reading_documents():
        if not document.get("is_published", False):
            continue
        unit_code = str(document.get("unit_id") or "") or None
        catalog.append(
            MixCandidate(
                id=str(document["id"]),
                skill="reading",
                title=str(document["title"]),
                duration_minutes=int(document.get("reading_time_minutes") or 8),
                unit_code=unit_code,
                sonolo_level=int(document["level"]) if document.get("level") is not None else None,
                is_premium=False,
                source="unit" if unit_code else "standalone",
            )
        )
    for hunt in load_vocabulary_hunt_documents():
        if not hunt.get("is_published", False):
            continue
        unit_code = str(hunt.get("unit_id") or "") or None
        catalog.append(
            MixCandidate(
                id=str(hunt["id"]),
                skill="reading",
                title=str(hunt.get("title") or "Vocabulary Hunt"),
                duration_minutes=5,
                unit_code=unit_code,
                sonolo_level=int(hunt["level"]) if hunt.get("level") is not None else None,
                is_premium=False,
                source="unit" if unit_code else "standalone",
            )
        )
    for document in load_writing_documents():
        if not document.get("is_published", False):
            continue
        unit_code = str(document.get("unit_id") or "") or None
        catalog.append(
            MixCandidate(
                id=str(document["id"]),
                skill="writing",
                title=str(document["title"]),
                duration_minutes=8,
                unit_code=unit_code,
                sonolo_level=int(document["level"]) if document.get("level") is not None else None,
                is_premium=False,
                source="unit" if unit_code else "standalone",
            )
        )
    for dialogue in load_listening_dialogues():
        unit_code = dialogue.unit_id
        catalog.append(
            MixCandidate(
                id=dialogue.id,
                skill="listening",
                title=dialogue.title,
                duration_minutes=8,
                unit_code=unit_code,
                sonolo_level=dialogue.sonolo_level,
                is_premium=dialogue.is_premium,
                source="unit" if unit_code else "standalone",
            )
        )
    del units
    return catalog


async def _skill_levels(db: AsyncSession, user_id: UUID) -> dict[str, int]:
    rows = (
        await db.execute(select(UserSkillLevel).where(UserSkillLevel.user_id == user_id))
    ).scalars().all()
    levels = {skill: 1 for skill in SKILLS}
    for row in rows:
        if row.skill in levels:
            levels[row.skill] = row.sonolo_level
    return levels


async def _overdue_reviews(db: AsyncSession, user_id: UUID, now: datetime) -> int:
    count = (
        await db.execute(
            select(func.count())
            .select_from(VocabularyCard)
            .where(
                VocabularyCard.user_id == user_id,
                VocabularyCard.due_date <= now,
                VocabularyCard.state < 3,
            )
        )
    ).scalar_one()
    return int(count)


async def _unfinished_blocks(
    db: AsyncSession, user_id: UUID, unit_code: str | None
) -> list[str]:
    if not unit_code:
        return list(SKILLS)
    progress_rows = (
        await db.execute(
            select(UserUnitProgress).where(UserUnitProgress.user_id == user_id)
        )
    ).scalars().all()
    if not progress_rows:
        return list(SKILLS)
    row = progress_rows[0]
    unfinished: list[str] = []
    if not row.speaking_complete:
        unfinished.append("speaking")
    if not row.listening_complete:
        unfinished.append("listening")
    if not row.reading_complete:
        unfinished.append("reading")
    if not row.writing_complete:
        unfinished.append("writing")
    return unfinished or list(SKILLS)


async def _recent_errors(db: AsyncSession, user_id: UUID, now: datetime) -> dict[str, int]:
    cutoff = now - timedelta(days=7)
    rows = (
        await db.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.user_id == user_id,
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
                SkillExerciseAttempt.submitted_at >= cutoff,
            )
        )
    ).scalars().all()
    counts: dict[str, int] = {}
    for row in rows:
        if row.score is not None and row.score < 70:
            counts[row.skill] = counts.get(row.skill, 0) + 1
    return counts


async def _speaking_catalog(db: AsyncSession) -> list[MixCandidate]:
    rows = (
        await db.execute(select(Scenario).where(Scenario.is_published.is_(True)))
    ).scalars().all()
    unit_codes: dict = {}
    catalog: list[MixCandidate] = []
    for row in rows:
        unit_code = None
        if row.unit_id is not None:
            if row.unit_id not in unit_codes:
                from app.models.curriculum import Unit

                unit = await db.get(Unit, row.unit_id)
                unit_codes[row.unit_id] = unit.unit_code if unit is not None else None
            unit_code = unit_codes[row.unit_id]
        catalog.append(
            MixCandidate(
                id=str(row.id),
                skill="speaking",
                title=row.title,
                duration_minutes=10,
                unit_code=unit_code,
                sonolo_level=row.sonolo_level,
                is_premium=row.is_premium,
                source="unit" if unit_code else "standalone",
            )
        )
    return catalog


async def build_daily_mix_for_user(
    db: AsyncSession,
    user: User,
    *,
    now: datetime | None = None,
    available_time_minutes: int = 20,
) -> DailyMix:
    await persist_curriculum(db)
    current = now or utc_now()
    local_date = get_local_date_for_user(current, user.timezone)
    rng = Random(deterministic_seed(user.id, local_date))
    levels = await _skill_levels(db, user.id)
    overdue = await _overdue_reviews(db, user.id, current)
    unit_code = _published_unit_codes()[0] if _published_unit_codes() else None
    unfinished = await _unfinished_blocks(db, user.id, unit_code)
    errors = await _recent_errors(db, user.id, current)
    catalog = build_content_catalog() + await _speaking_catalog(db)
    last = user.last_activity_at
    welcome_back = False
    if last is not None:
        last_utc = last if last.tzinfo is not None else last.replace(tzinfo=current.tzinfo)
        welcome_back = (current - last_utc).days >= 30
    allow_premium = user.subscription_tier == SUBSCRIPTION_PREMIUM
    return generate_daily_mix(
        local_date=local_date,
        skill_levels=levels,
        goal=user.learning_goal,
        overdue_reviews=overdue,
        unfinished_blocks=unfinished,
        recent_errors=errors,
        catalog=catalog,
        rng=rng,
        allow_premium=allow_premium,
        available_time_minutes=available_time_minutes,
        welcome_back=welcome_back,
        current_unit=unit_code,
    )
