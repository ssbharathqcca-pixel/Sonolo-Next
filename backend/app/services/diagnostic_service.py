"""C7 diagnostic / placement (Part VI). Does not alter C2 formulas or XP."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, timedelta
from pathlib import Path
from statistics import mean
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import utc_now
from app.models.curriculum import LearningSession, UserSkillLevel
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.models.user import User
from app.services.content_service import REPO_ROOT
from app.services.evidence_service import get_or_create_skill_level
from app.services.mastery_service import SKILLS

DIAGNOSTIC_SKILL = "diagnostic"
ITEMS_PER_SKILL = 6
TIERS = ("easy", "medium", "hard")
SKIPPABLE = frozenset({"speaking", "writing"})
CORRECTION_WINDOW_DAYS = 7
CORRECTION_SAMPLE = 5
DIAGNOSTIC_PATH = REPO_ROOT / "content" / "diagnostic" / "placement-v1.json"


@dataclass(frozen=True)
class DiagnosticItem:
    id: str
    skill: str
    tier: str
    prompt: str
    options: list[str]
    correct_index: int


def load_diagnostic_items() -> list[DiagnosticItem]:
    raw = json.loads(DIAGNOSTIC_PATH.read_text(encoding="utf-8"))
    items: list[DiagnosticItem] = []
    for entry in raw["items"]:
        items.append(
            DiagnosticItem(
                id=str(entry["id"]),
                skill=str(entry["skill"]),
                tier=str(entry["tier"]),
                prompt=str(entry["prompt"]),
                options=[str(choice) for choice in entry["options"]],
                correct_index=int(entry["correct_index"]),
            )
        )
    return items


def items_by_id() -> dict[str, DiagnosticItem]:
    return {item.id: item for item in load_diagnostic_items()}


def public_item(item: DiagnosticItem) -> dict[str, Any]:
    return {
        "id": item.id,
        "skill": item.skill,
        "tier": item.tier,
        "prompt": item.prompt,
        "options": item.options,
    }


def calculate_placement(answers: list[tuple[str, float]]) -> int:
    """Part VI §6.3. answers: (difficulty_tier, score 0.0–1.0) → Sonolo Level 1–9."""
    easy = [score for tier, score in answers if tier == "easy"]
    medium = [score for tier, score in answers if tier == "medium"]
    hard = [score for tier, score in answers if tier == "hard"]
    easy_avg = mean(easy) if easy else 0.0
    medium_avg = mean(medium) if medium else 0.0
    hard_avg = mean(hard) if hard else 0.0
    if hard_avg >= 0.70:
        return 7
    if hard_avg >= 0.50 and medium_avg >= 0.70:
        return 6
    if medium_avg >= 0.70:
        return 5
    if medium_avg >= 0.50 and easy_avg >= 0.80:
        return 4
    if easy_avg >= 0.80:
        return 3
    if easy_avg >= 0.60:
        return 2
    return 1


def apply_skipped_skills(
    placements: dict[str, int], skipped: set[str]
) -> dict[str, int]:
    """Part VI §6.4."""
    result = dict(placements)
    if skipped >= set(SKILLS):
        return {skill: 1 for skill in SKILLS}
    speaking_skipped = "speaking" in skipped
    writing_skipped = "writing" in skipped
    others = [
        result[skill]
        for skill in SKILLS
        if skill not in skipped and skill in result
    ]
    if speaking_skipped and writing_skipped:
        baseline = min(result.get("listening", 1), result.get("reading", 1))
        result["speaking"] = max(1, baseline - 1)
        result["writing"] = max(1, baseline - 1)
    elif speaking_skipped:
        result["speaking"] = max(1, min(others) - 1) if others else 1
    elif writing_skipped:
        result["writing"] = max(1, min(others) - 1) if others else 1
    for skill in SKILLS:
        result.setdefault(skill, 1)
    return result


def score_item(item: DiagnosticItem, submitted: int) -> float:
    return 1.0 if submitted == item.correct_index else 0.0


def _empty_state() -> dict[str, Any]:
    return {
        "status": "in_progress",
        "answers": {skill: {} for skill in SKILLS},
        "skipped": [],
        "placement": None,
        "correction_applied": {skill: False for skill in SKILLS},
        "messages": [],
    }


async def _open_session(db: AsyncSession, user_id: UUID) -> LearningSession | None:
    result = await db.execute(
        select(LearningSession)
        .where(
            LearningSession.user_id == user_id,
            LearningSession.skill == DIAGNOSTIC_SKILL,
            LearningSession.ended_at.is_(None),
        )
        .order_by(LearningSession.started_at.desc())
    )
    return result.scalars().first()


async def _completed_session(
    db: AsyncSession, user_id: UUID
) -> LearningSession | None:
    result = await db.execute(
        select(LearningSession)
        .where(
            LearningSession.user_id == user_id,
            LearningSession.skill == DIAGNOSTIC_SKILL,
            LearningSession.ended_at.is_not(None),
        )
        .order_by(LearningSession.ended_at.desc())
    )
    return result.scalars().first()


async def start_diagnostic(db: AsyncSession, user: User) -> dict[str, Any]:
    completed = await _completed_session(db, user.id)
    if completed is not None:
        return _session_view(completed, include_items=True, completed=True)
    existing = await _open_session(db, user.id)
    if existing is not None:
        return _session_view(existing, include_items=True)
    now = utc_now()
    session = LearningSession(
        user_id=user.id,
        unit_id=None,
        skill=DIAGNOSTIC_SKILL,
        started_at=now,
        ended_at=None,
        duration_seconds=None,
        score=None,
        result_json=_empty_state(),
    )
    db.add(session)
    await db.flush()
    return _session_view(session, include_items=True)


def _session_view(
    session: LearningSession, *, include_items: bool, completed: bool = False
) -> dict[str, Any]:
    state = dict(session.result_json or _empty_state())
    catalog = load_diagnostic_items()
    answered_ids = {
        item_id
        for skill_answers in (state.get("answers") or {}).values()
        for item_id in skill_answers
    }
    items = [public_item(item) for item in catalog] if include_items else []
    return {
        "session_id": str(session.id),
        "status": "completed" if completed or state.get("status") == "completed" else "in_progress",
        "skipped": list(state.get("skipped") or []),
        "answered_ids": sorted(answered_ids),
        "items": items,
        "placement": state.get("placement"),
        "messages": list(state.get("messages") or []),
    }


async def submit_answer(
    db: AsyncSession, user: User, item_id: str, submitted: int
) -> dict[str, Any]:
    session = await _open_session(db, user.id)
    if session is None:
        raise ValueError("No in-progress diagnostic.")
    catalog = items_by_id()
    item = catalog.get(item_id)
    if item is None:
        raise KeyError(item_id)
    state = dict(session.result_json or _empty_state())
    skipped = set(state.get("skipped") or [])
    if item.skill in skipped:
        raise ValueError(f"{item.skill} was skipped.")
    answers = dict(state.get("answers") or {})
    skill_answers = dict(answers.get(item.skill) or {})
    skill_answers[item_id] = {
        "submitted": submitted,
        "score": score_item(item, submitted),
        "tier": item.tier,
    }
    answers[item.skill] = skill_answers
    state["answers"] = answers
    session.result_json = state
    await db.flush()
    return {
        "item_id": item_id,
        "accepted": True,
        "answered_count": sum(len(block) for block in answers.values()),
    }


async def skip_skills(db: AsyncSession, user: User, skills: list[str]) -> dict[str, Any]:
    session = await _open_session(db, user.id)
    if session is None:
        raise ValueError("No in-progress diagnostic.")
    state = dict(session.result_json or _empty_state())
    skipped = set(state.get("skipped") or [])
    for skill in skills:
        if skill not in SKIPPABLE and skill != "all":
            raise ValueError(f"{skill} cannot be skipped individually.")
        if skill == "all":
            skipped = set(SKILLS)
        else:
            skipped.add(skill)
    state["skipped"] = sorted(skipped)
    session.result_json = state
    await db.flush()
    return _session_view(session, include_items=False)


async def skip_all_and_place(db: AsyncSession, user: User) -> dict[str, Any]:
    view = await start_diagnostic(db, user)
    if view["status"] == "completed":
        return view
    await skip_skills(db, user, ["all"])
    return await complete_diagnostic(db, user)


def _placements_from_state(state: dict[str, Any]) -> dict[str, int]:
    skipped = set(state.get("skipped") or [])
    catalog = items_by_id()
    placements: dict[str, int] = {}
    if skipped >= set(SKILLS):
        return {skill: 1 for skill in SKILLS}
    for skill in SKILLS:
        if skill in skipped:
            continue
        skill_answers = (state.get("answers") or {}).get(skill) or {}
        pairs: list[tuple[str, float]] = []
        for item_id, payload in skill_answers.items():
            item = catalog.get(item_id)
            if item is None:
                continue
            pairs.append((item.tier, float(payload.get("score") or 0.0)))
        placements[skill] = calculate_placement(pairs)
    return apply_skipped_skills(placements, skipped)


async def _apply_placement(
    db: AsyncSession, user: User, placements: dict[str, int]
) -> None:
    now = utc_now()
    for skill in SKILLS:
        row = await get_or_create_skill_level(db, user.id, skill)
        row.sonolo_level = placements[skill]
        row.updated_at = now
    user.sonolo_level = min(placements[skill] for skill in SKILLS)


async def complete_diagnostic(db: AsyncSession, user: User) -> dict[str, Any]:
    completed = await _completed_session(db, user.id)
    if completed is not None and await _open_session(db, user.id) is None:
        return _session_view(completed, include_items=False, completed=True)
    session = await _open_session(db, user.id)
    if session is None:
        raise ValueError("No in-progress diagnostic.")
    state = dict(session.result_json or _empty_state())
    skipped = set(state.get("skipped") or [])
    if skipped < set(SKILLS):
        required = [skill for skill in ("listening", "reading") if skill not in skipped]
        answers = state.get("answers") or {}
        for skill in required:
            if len(answers.get(skill) or {}) < ITEMS_PER_SKILL:
                raise ValueError(f"{skill} needs {ITEMS_PER_SKILL} answers.")
        for skill in ("speaking", "writing"):
            if skill not in skipped and len(answers.get(skill) or {}) < ITEMS_PER_SKILL:
                raise ValueError(f"{skill} needs {ITEMS_PER_SKILL} answers or skip.")
    placements = _placements_from_state(state)
    await _apply_placement(db, user, placements)
    now = utc_now()
    state["status"] = "completed"
    state["placement"] = placements
    session.result_json = state
    session.ended_at = now
    started = session.started_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=UTC)
    session.duration_seconds = int((now - started).total_seconds())
    session.score = float(mean(placements.values()))
    await db.flush()
    return _session_view(session, include_items=False, completed=True)


async def check_placement_correction(
    db: AsyncSession, user: User, skill: str
) -> dict[str, Any]:
    """Part VI §6.5 one-time first-week correction. Not C2 advancement."""
    if skill not in SKILLS:
        raise ValueError(skill)
    session = await _completed_session(db, user.id)
    if session is None or session.ended_at is None:
        return {"adjusted": False, "reason": "no_placement"}
    ended = session.ended_at
    if ended.tzinfo is None:
        ended = ended.replace(tzinfo=utc_now().tzinfo)
    if utc_now() - ended > timedelta(days=CORRECTION_WINDOW_DAYS):
        return {"adjusted": False, "reason": "window_elapsed"}
    state = dict(session.result_json or {})
    applied = dict(state.get("correction_applied") or {})
    if applied.get(skill):
        return {"adjusted": False, "reason": "already_applied"}
    rows = (
        await db.execute(
            select(SkillExerciseAttempt)
            .where(
                SkillExerciseAttempt.user_id == user.id,
                SkillExerciseAttempt.skill == skill,
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
                SkillExerciseAttempt.score.is_not(None),
            )
            .order_by(SkillExerciseAttempt.submitted_at.desc())
        )
    ).scalars().all()
    scores = [float(row.score) for row in rows[:CORRECTION_SAMPLE] if row.score is not None]
    if len(scores) < CORRECTION_SAMPLE:
        return {"adjusted": False, "reason": "insufficient_evidence"}
    avg = mean(scores)
    level_row = await get_or_create_skill_level(db, user.id, skill)
    current = level_row.sonolo_level
    message = None
    new_level = current
    if avg > 90 and current < 9:
        new_level = min(current + 1, 9)
        message = (
            f"We've moved you up! Your {skill} seems stronger than we thought."
        )
    elif avg < 40 and current > 1:
        new_level = max(current - 1, 1)
        message = (
            f"We've adjusted your {skill} level to better match your current ability."
        )
    else:
        applied[skill] = True
        state["correction_applied"] = applied
        session.result_json = state
        await db.flush()
        return {
            "adjusted": False,
            "reason": "within_band",
            "average": avg,
            "level": current,
        }
    level_row.sonolo_level = new_level
    level_row.updated_at = utc_now()
    applied[skill] = True
    messages = list(state.get("messages") or [])
    if message:
        messages.append(message)
    state["correction_applied"] = applied
    state["messages"] = messages
    session.result_json = state
    levels = (
        await db.execute(
            select(UserSkillLevel).where(UserSkillLevel.user_id == user.id)
        )
    ).scalars().all()
    if levels:
        user.sonolo_level = min(row.sonolo_level for row in levels)
    await db.flush()
    return {
        "adjusted": True,
        "skill": skill,
        "previous_level": current,
        "new_level": new_level,
        "average": avg,
        "message": message,
    }
