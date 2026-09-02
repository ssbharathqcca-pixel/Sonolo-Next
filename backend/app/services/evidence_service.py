"""Normalized mastery evidence persistence (D-018).

C2 stays a pure calculator. This layer stores last-20 practice scores,
unit-test per-skill scores, and EMA, then optionally calls C2.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from uuid6 import uuid7

from app.core.time import utc_now
from app.models.curriculum import LearningSession, Unit, UserSkillLevel, UserUnitProgress
from app.models.evidence import (
    ATTEMPT_REJECTED_LATE,
    ATTEMPT_STARTED,
    ATTEMPT_SUBMITTED,
    SkillExerciseAttempt,
    UnitTestSkillEvidence,
)
from app.services.mastery_service import (
    EXERCISE_WINDOW,
    SKILLS,
    MasteryUnavailable,
    UnitTestEvidence,
    check_level_advancement,
    compute_mastery_score,
    update_ema,
)

SKILL_READING = "reading"
SKILL_SPEAKING = "speaking"
ACTIVITY_SPEAKING_SESSION = "speaking_session"
ACTIVITY_PRONUNCIATION_DRILL = "pronunciation_drill"


def _aware(dt: datetime) -> datetime:
    """SQLite returns naive datetimes; treat them as UTC (N4)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


async def get_or_create_skill_level(
    db: AsyncSession, user_id: UUID, skill: str
) -> UserSkillLevel:
    result = await db.execute(
        select(UserSkillLevel).where(
            UserSkillLevel.user_id == user_id,
            UserSkillLevel.skill == skill,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = UserSkillLevel(user_id=user_id, skill=skill, sonolo_level=1, ema_score=None)
    db.add(row)
    await db.flush()
    return row


async def get_or_create_unit_progress(
    db: AsyncSession, user_id: UUID, unit_id: UUID
) -> UserUnitProgress:
    result = await db.execute(
        select(UserUnitProgress).where(
            UserUnitProgress.user_id == user_id,
            UserUnitProgress.unit_id == unit_id,
        )
    )
    row = result.scalar_one_or_none()
    if row is not None:
        return row
    row = UserUnitProgress(user_id=user_id, unit_id=unit_id)
    db.add(row)
    await db.flush()
    return row


async def find_active_attempt(
    db: AsyncSession, user_id: UUID, content_id: str
) -> SkillExerciseAttempt | None:
    result = await db.execute(
        select(SkillExerciseAttempt).where(
            SkillExerciseAttempt.user_id == user_id,
            SkillExerciseAttempt.content_id == content_id,
            SkillExerciseAttempt.status == ATTEMPT_STARTED,
        )
    )
    return result.scalar_one_or_none()


async def start_practice_attempt(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID | None,
    skill: str,
    activity_type: str,
    content_id: str,
    sonolo_level: int | None,
    now: datetime | None = None,
) -> SkillExerciseAttempt:
    """Idempotent start: reuse an in-flight attempt for the same content."""
    existing = await find_active_attempt(db, user_id, content_id)
    if existing is not None:
        return existing
    started = now or utc_now()
    attempt = SkillExerciseAttempt(
        user_id=user_id,
        unit_id=unit_id,
        skill=skill,
        activity_type=activity_type,
        content_id=content_id,
        sonolo_level=sonolo_level,
        score=None,
        status=ATTEMPT_STARTED,
        started_at=started,
        submitted_at=None,
        result_json={},
    )
    db.add(attempt)
    await db.flush()
    return attempt


async def finalize_practice_attempt(
    db: AsyncSession,
    attempt: SkillExerciseAttempt,
    *,
    score: float,
    result_json: dict[str, Any],
    now: datetime | None = None,
) -> SkillExerciseAttempt:
    """Record a scored submit. Already-finalized attempts are returned as-is."""
    if attempt.status == ATTEMPT_SUBMITTED:
        return attempt
    submitted = now or utc_now()
    attempt.score = score
    attempt.status = ATTEMPT_SUBMITTED
    attempt.submitted_at = submitted
    attempt.result_json = result_json
    attempt.updated_at = submitted
    await _write_session_envelope(db, attempt, submitted)
    await apply_session_ema(db, attempt.user_id, attempt.skill, score, submitted)
    await db.flush()
    return attempt


async def reject_late_attempt(
    db: AsyncSession,
    attempt: SkillExerciseAttempt,
    *,
    result_json: dict[str, Any],
    now: datetime | None = None,
) -> SkillExerciseAttempt:
    """Late reject writes no score and no mastery evidence."""
    if attempt.status != ATTEMPT_STARTED:
        return attempt
    rejected = now or utc_now()
    attempt.status = ATTEMPT_REJECTED_LATE
    attempt.submitted_at = rejected
    attempt.score = None
    attempt.result_json = result_json
    attempt.updated_at = rejected
    await db.flush()
    return attempt


async def _write_session_envelope(
    db: AsyncSession,
    attempt: SkillExerciseAttempt,
    ended_at: datetime,
) -> None:
    duration = int((_aware(ended_at) - _aware(attempt.started_at)).total_seconds())
    db.add(
        LearningSession(
            user_id=attempt.user_id,
            unit_id=attempt.unit_id,
            skill=attempt.skill,
            started_at=attempt.started_at,
            ended_at=ended_at,
            duration_seconds=max(duration, 0),
            score=attempt.score,
            result_json={
                "attempt_id": str(attempt.id),
                "content_id": attempt.content_id,
                "activity_type": attempt.activity_type,
            },
        )
    )


async def apply_session_ema(
    db: AsyncSession,
    user_id: UUID,
    skill: str,
    session_score: float,
    now: datetime,
) -> float:
    """First session score initializes EMA; later scores use C2 update_ema."""
    row = await get_or_create_skill_level(db, user_id, skill)
    if row.ema_score is None:
        row.ema_score = round(float(session_score), 2)
    else:
        row.ema_score = update_ema(row.ema_score, session_score)
    row.updated_at = now
    return row.ema_score


async def last_exercise_scores(
    db: AsyncSession,
    user_id: UUID,
    skill: str,
    sonolo_level: int | None,
    limit: int = EXERCISE_WINDOW,
) -> list[float]:
    stmt = select(SkillExerciseAttempt).where(
        SkillExerciseAttempt.user_id == user_id,
        SkillExerciseAttempt.skill == skill,
        SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
        SkillExerciseAttempt.score.is_not(None),
    )
    if sonolo_level is not None:
        stmt = stmt.where(SkillExerciseAttempt.sonolo_level == sonolo_level)
    stmt = stmt.order_by(SkillExerciseAttempt.submitted_at.asc())
    rows = (await db.execute(stmt)).scalars().all()
    # Writing revisions: progression uses the highest score per content_id
    # (Part X §10.5). Other activity types keep every submitted score.
    writing_best: dict[str, float] = {}
    writing_order: list[str] = []
    other: list[float] = []
    for row in rows:
        if row.score is None:
            continue
        if row.activity_type == "writing_exercise":
            previous = writing_best.get(row.content_id)
            if previous is None:
                writing_order.append(row.content_id)
                writing_best[row.content_id] = float(row.score)
            else:
                writing_best[row.content_id] = max(previous, float(row.score))
        else:
            other.append(float(row.score))
    scores = other + [writing_best[content_id] for content_id in writing_order]
    return scores[-limit:]


async def unit_test_skill_scores(
    db: AsyncSession, user_id: UUID, skill: str
) -> list[float]:
    stmt = (
        select(UnitTestSkillEvidence.score)
        .where(
            UnitTestSkillEvidence.user_id == user_id,
            UnitTestSkillEvidence.skill == skill,
        )
        .order_by(UnitTestSkillEvidence.submitted_at.asc())
    )
    return [float(score) for score in (await db.execute(stmt)).scalars().all()]


async def record_unit_test_skill_score(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    skill: str,
    score: float,
    sitting_id: UUID | None = None,
    result_json: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> UnitTestSkillEvidence:
    submitted = now or utc_now()
    row = UnitTestSkillEvidence(
        user_id=user_id,
        unit_id=unit_id,
        sitting_id=sitting_id or uuid7(),
        skill=skill,
        score=score,
        result_json=result_json or {},
        submitted_at=submitted,
    )
    db.add(row)
    await db.flush()
    return row


async def submitted_content_ids(
    db: AsyncSession, user_id: UUID, content_ids: list[str]
) -> set[str]:
    if not content_ids:
        return set()
    stmt = select(SkillExerciseAttempt.content_id).where(
        SkillExerciseAttempt.user_id == user_id,
        SkillExerciseAttempt.content_id.in_(content_ids),
        SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
    )
    return set((await db.execute(stmt)).scalars().all())


async def count_submitted_attempts(
    db: AsyncSession, user_id: UUID, content_id: str
) -> int:
    result = await db.execute(
        select(SkillExerciseAttempt).where(
            SkillExerciseAttempt.user_id == user_id,
            SkillExerciseAttempt.content_id == content_id,
            SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
        )
    )
    return len(result.scalars().all())


async def latest_submitted_attempt(
    db: AsyncSession, user_id: UUID, content_id: str
) -> SkillExerciseAttempt | None:
    result = await db.execute(
        select(SkillExerciseAttempt)
        .where(
            SkillExerciseAttempt.user_id == user_id,
            SkillExerciseAttempt.content_id == content_id,
            SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
        )
        .order_by(SkillExerciseAttempt.submitted_at.desc())
    )
    return result.scalars().first()


async def refresh_writing_complete(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    required_content_ids: list[str],
    now: datetime | None = None,
) -> bool:
    """True iff every unit writing_id has an accepted submit."""
    progress = await get_or_create_unit_progress(db, user_id, unit_id)
    if not required_content_ids:
        progress.writing_complete = False
        return False
    done = await submitted_content_ids(db, user_id, required_content_ids)
    complete = set(required_content_ids) <= done
    progress.writing_complete = complete
    progress.updated_at = now or utc_now()
    return complete


async def refresh_listening_complete(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    required_content_ids: list[str],
    now: datetime | None = None,
) -> bool:
    """True iff every unit listening_id has an accepted submit."""
    progress = await get_or_create_unit_progress(db, user_id, unit_id)
    if not required_content_ids:
        progress.listening_complete = False
        return False
    done = await submitted_content_ids(db, user_id, required_content_ids)
    complete = set(required_content_ids) <= done
    progress.listening_complete = complete
    progress.updated_at = now or utc_now()
    return complete


async def refresh_reading_complete(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    required_content_ids: list[str],
    now: datetime | None = None,
) -> bool:
    """D-019: true iff every required reading activity has an accepted submit."""
    progress = await get_or_create_unit_progress(db, user_id, unit_id)
    if not required_content_ids:
        progress.reading_complete = False
        return False
    done = await submitted_content_ids(db, user_id, required_content_ids)
    complete = set(required_content_ids) <= done
    progress.reading_complete = complete
    progress.updated_at = now or utc_now()
    return complete


async def refresh_speaking_complete(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    required_content_ids: list[str],
    now: datetime | None = None,
) -> bool:
    """True iff every unit speaking_id has an accepted submit.

    D-036: completion is the set of authored F3 SpeakUp activities, not
    merely that a speaking session exists.
    """
    progress = await get_or_create_unit_progress(db, user_id, unit_id)
    if not required_content_ids:
        progress.speaking_complete = False
        return False
    done = await submitted_content_ids(db, user_id, required_content_ids)
    complete = set(required_content_ids) <= done
    progress.speaking_complete = complete
    progress.updated_at = now or utc_now()
    return complete


async def record_speaking_practice(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_code: str | None,
    content_id: str,
    activity_type: str,
    score: float,
    result_json: dict[str, Any],
    sonolo_level: int | None,
    fingerprint: Any | None = None,
) -> bool:
    """Persist normalized speaking evidence and refresh speaking_complete.

    Gym content (no unit_code) does not write curriculum completion.
    Identical fingerprints reuse the latest submit (idempotent replay).
    """
    from app.services.content_service import (
        content_unit_id,
        get_unit_document,
        persist_curriculum,
    )

    payload = dict(result_json)
    if fingerprint is not None:
        payload["fingerprint"] = fingerprint
        latest = await latest_submitted_attempt(db, user_id, content_id)
        if latest is not None and (latest.result_json or {}).get("fingerprint") == fingerprint:
            if not unit_code:
                return False
            await persist_curriculum(db)
            unit_pk = content_unit_id(unit_code, "en-CA")
            required = list((get_unit_document(unit_code) or {}).get("speaking_ids") or [])
            return await refresh_speaking_complete(
                db,
                user_id=user_id,
                unit_id=unit_pk,
                required_content_ids=required,
            )
    if not unit_code:
        return False
    await persist_curriculum(db)
    unit_pk = content_unit_id(unit_code, "en-CA")
    attempt = await start_practice_attempt(
        db,
        user_id=user_id,
        unit_id=unit_pk,
        skill=SKILL_SPEAKING,
        activity_type=activity_type,
        content_id=content_id,
        sonolo_level=sonolo_level,
    )
    await finalize_practice_attempt(
        db,
        attempt,
        score=float(score),
        result_json=payload,
    )
    await orchestrate_after_practice(db, attempt)
    required = list((get_unit_document(unit_code) or {}).get("speaking_ids") or [])
    return await refresh_speaking_complete(
        db,
        user_id=user_id,
        unit_id=unit_pk,
        required_content_ids=required,
    )


async def orchestrate_after_practice(
    db: AsyncSession,
    attempt: SkillExerciseAttempt,
) -> dict[str, Any]:
    """Feed C2 with persisted evidence. Missing sources stay unavailable."""
    skill_row = await get_or_create_skill_level(db, attempt.user_id, attempt.skill)
    exercises = await last_exercise_scores(
        db, attempt.user_id, attempt.skill, attempt.sonolo_level or skill_row.sonolo_level
    )
    tests = await unit_test_skill_scores(db, attempt.user_id, attempt.skill)
    payload: dict[str, Any] = {
        "ema_score": skill_row.ema_score,
        "exercise_count": len(exercises),
        "mastery_available": False,
        "mastery_score": None,
        "advanced": False,
        "sonolo_level": skill_row.sonolo_level,
    }
    try:
        mastery = compute_mastery_score(exercises, tests, skill_row.ema_score)
        payload["mastery_available"] = True
        payload["mastery_score"] = mastery
        decision = check_level_advancement(
            skill=attempt.skill,
            current_level=skill_row.sonolo_level,
            exercise_scores=exercises,
            unit_test_skill_scores=tests,
            ema_session_score=skill_row.ema_score,
            unit_tests=await unit_test_evidence_for_user(db, attempt.user_id),
            band_tests_passed=[],
        )
        skill_row.sonolo_level = decision.new_level
        payload["advanced"] = decision.advanced
        payload["sonolo_level"] = decision.new_level
    except MasteryUnavailable:
        pass
    return payload


async def unit_test_evidence_for_user(
    db: AsyncSession, user_id: UUID
) -> list[UnitTestEvidence]:
    """Rebuild C2 UnitTestEvidence from persisted sittings (no formula copy)."""
    rows = (
        await db.execute(
            select(UnitTestSkillEvidence, Unit.unit_code)
            .join(Unit, UnitTestSkillEvidence.unit_id == Unit.id)
            .where(UnitTestSkillEvidence.user_id == user_id)
            .order_by(UnitTestSkillEvidence.submitted_at.asc())
        )
    ).all()
    sittings: dict[UUID, dict[str, Any]] = {}
    for row, unit_code in rows:
        bucket = sittings.setdefault(
            row.sitting_id,
            {"unit_code": unit_code, "skills": {}, "overall": None},
        )
        bucket["skills"][row.skill] = float(row.score)
        overall = (row.result_json or {}).get("overall")
        if overall is not None:
            bucket["overall"] = float(overall)
    evidence: list[UnitTestEvidence] = []
    for bucket in sittings.values():
        skills = bucket["skills"]
        if any(skill not in skills for skill in SKILLS):
            continue
        evidence.append(
            UnitTestEvidence(
                unit_code=str(bucket["unit_code"]),
                overall_score=bucket["overall"],
                skill_scores=skills,
            )
        )
    return evidence


async def record_unit_test_sitting(
    db: AsyncSession,
    *,
    user_id: UUID,
    unit_id: UUID,
    overall: float,
    skill_scores: dict[str, float],
    passed: bool,
    result_json: dict[str, Any],
    sitting_id: UUID | None = None,
    now: datetime | None = None,
) -> UUID:
    """Persist four skill rows + pass/fail on user_unit_progress."""
    submitted = now or utc_now()
    sitting = sitting_id or uuid7()
    payload = dict(result_json)
    payload["overall"] = overall
    payload["passed"] = passed
    for skill in SKILLS:
        await record_unit_test_skill_score(
            db,
            user_id=user_id,
            unit_id=unit_id,
            skill=skill,
            score=float(skill_scores[skill]),
            sitting_id=sitting,
            result_json=payload,
            now=submitted,
        )
    progress = await get_or_create_unit_progress(db, user_id, unit_id)
    progress.unit_test_score = overall
    if passed:
        progress.unit_test_passed = True
        progress.completed_at = submitted
    progress.updated_at = submitted
    return sitting


async def orchestrate_after_unit_test(
    db: AsyncSession,
    *,
    user_id: UUID,
    sonolo_level: int | None,
) -> dict[str, Any]:
    """Run C2 advancement using persisted unit-test + practice evidence."""
    unit_tests = await unit_test_evidence_for_user(db, user_id)
    by_skill: dict[str, Any] = {}
    for skill in SKILLS:
        skill_row = await get_or_create_skill_level(db, user_id, skill)
        exercises = await last_exercise_scores(
            db, user_id, skill, sonolo_level or skill_row.sonolo_level
        )
        tests = await unit_test_skill_scores(db, user_id, skill)
        item: dict[str, Any] = {
            "ema_score": skill_row.ema_score,
            "exercise_count": len(exercises),
            "mastery_available": False,
            "mastery_score": None,
            "advanced": False,
            "sonolo_level": skill_row.sonolo_level,
        }
        try:
            mastery = compute_mastery_score(exercises, tests, skill_row.ema_score)
            item["mastery_available"] = True
            item["mastery_score"] = mastery
            decision = check_level_advancement(
                skill=skill,
                current_level=skill_row.sonolo_level,
                exercise_scores=exercises,
                unit_test_skill_scores=tests,
                ema_session_score=skill_row.ema_score,
                unit_tests=unit_tests,
                band_tests_passed=[],
            )
            skill_row.sonolo_level = decision.new_level
            item["advanced"] = decision.advanced
            item["sonolo_level"] = decision.new_level
        except MasteryUnavailable:
            pass
        by_skill[skill] = item
    return by_skill


async def latest_unit_test_sitting(
    db: AsyncSession, user_id: UUID, unit_id: UUID
) -> UnitTestSkillEvidence | None:
    result = await db.execute(
        select(UnitTestSkillEvidence)
        .where(
            UnitTestSkillEvidence.user_id == user_id,
            UnitTestSkillEvidence.unit_id == unit_id,
        )
        .order_by(UnitTestSkillEvidence.submitted_at.desc())
    )
    return result.scalars().first()
