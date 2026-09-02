"""Evidence persistence layer (D-018) — not learning_sessions as a ledger."""

import subprocess
import sys

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.curriculum import LearningSession, Unit, UserSkillLevel
from app.models.evidence import (
    ATTEMPT_REJECTED_LATE,
    ATTEMPT_SUBMITTED,
    SkillExerciseAttempt,
    UnitTestSkillEvidence,
)
from app.models.user import User
from app.services.evidence_service import (
    apply_session_ema,
    finalize_practice_attempt,
    last_exercise_scores,
    record_unit_test_skill_score,
    reject_late_attempt,
    start_practice_attempt,
    unit_test_skill_scores,
)
from app.services.mastery_service import update_ema

def test_alembic_sql_creates_evidence_tables() -> None:
    result = subprocess.run(
        [sys.executable, "-m", "alembic", "upgrade", "head", "--sql"],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    assert "CREATE TABLE skill_exercise_attempts" in sql
    assert "CREATE TABLE unit_test_skill_evidence" in sql
    assert "ema_score" in sql
    assert "uq_skill_exercise_attempts_active" in sql


pytestmark = pytest.mark.asyncio


async def _user(db: AsyncSession) -> User:
    user = User(
        name="Evidence",
        native_language="en",
        target_language="en-CA",
        learning_goal="casual",
        current_level="sprout",
        email="evidence@example.com",
        hashed_password="x",
    )
    db.add(user)
    await db.flush()
    return user


async def _unit(db: AsyncSession) -> Unit:
    unit = Unit(
        unit_code="F3",
        band="foundation",
        title="First Week",
        level_target=2,
        sort_order=3,
        language="en-CA",
        vocabulary_targets=["aisle"],
        grammar_targets=["articles"],
        prerequisites=[],
        is_published=True,
    )
    db.add(unit)
    await db.flush()
    return unit


async def test_start_is_idempotent_for_active_attempt(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    first = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="reading-F3-grocery-flyer",
        sonolo_level=2,
    )
    second = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="reading-F3-grocery-flyer",
        sonolo_level=2,
    )
    assert first.id == second.id
    rows = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.user_id == user.id
            )
        )
    ).scalars().all()
    assert len(rows) == 1


async def test_finalize_records_score_and_session_envelope(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    unit = await _unit(db_session)
    attempt = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=unit.id,
        skill="reading",
        activity_type="reading_exercise",
        content_id="reading-F3-grocery-flyer",
        sonolo_level=2,
    )
    await finalize_practice_attempt(
        db_session, attempt, score=80.0, result_json={"question_scores": []}
    )
    await db_session.commit()
    assert attempt.status == ATTEMPT_SUBMITTED
    assert attempt.score == 80.0
    scores = await last_exercise_scores(db_session, user.id, "reading", 2)
    assert scores == [80.0]
    sessions = (
        await db_session.execute(
            select(LearningSession).where(LearningSession.user_id == user.id)
        )
    ).scalars().all()
    assert len(sessions) == 1
    assert sessions[0].score == 80.0
    skill = (
        await db_session.execute(
            select(UserSkillLevel).where(
                UserSkillLevel.user_id == user.id,
                UserSkillLevel.skill == "reading",
            )
        )
    ).scalar_one()
    assert skill.ema_score == 80.0


async def test_duplicate_finalize_does_not_duplicate_evidence(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    attempt = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="ex-1",
        sonolo_level=2,
    )
    await finalize_practice_attempt(
        db_session, attempt, score=100.0, result_json={}
    )
    await finalize_practice_attempt(
        db_session, attempt, score=0.0, result_json={}
    )
    scores = await last_exercise_scores(db_session, user.id, "reading", 2)
    assert scores == [100.0]
    sessions = (
        await db_session.execute(
            select(LearningSession).where(LearningSession.user_id == user.id)
        )
    ).scalars().all()
    assert len(sessions) == 1


async def test_retry_creates_new_attempt_after_submit(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    first = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="ex-1",
        sonolo_level=2,
    )
    await finalize_practice_attempt(db_session, first, score=50.0, result_json={})
    second = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="ex-1",
        sonolo_level=2,
    )
    assert second.id != first.id
    await finalize_practice_attempt(db_session, second, score=90.0, result_json={})
    scores = await last_exercise_scores(db_session, user.id, "reading", 2)
    assert scores == [50.0, 90.0]


async def test_late_reject_writes_no_score(db_session: AsyncSession) -> None:
    user = await _user(db_session)
    attempt = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=None,
        skill="reading",
        activity_type="reading_exercise",
        content_id="ex-late",
        sonolo_level=4,
    )
    await reject_late_attempt(db_session, attempt, result_json={"reason": "late"})
    assert attempt.status == ATTEMPT_REJECTED_LATE
    assert attempt.score is None
    scores = await last_exercise_scores(db_session, user.id, "reading", 4)
    assert scores == []
    skill = (
        await db_session.execute(
            select(UserSkillLevel).where(UserSkillLevel.user_id == user.id)
        )
    ).scalar_one_or_none()
    assert skill is None or skill.ema_score is None


async def test_ema_initializes_then_follows_c2_formula(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    now = datetime(2026, 9, 2, tzinfo=UTC)
    first = await apply_session_ema(db_session, user.id, "reading", 80.0, now)
    assert first == 80.0
    second = await apply_session_ema(
        db_session, user.id, "reading", 100.0, now + timedelta(minutes=1)
    )
    assert second == update_ema(80.0, 100.0)


async def test_unit_test_skill_evidence_is_separate_from_practice(
    db_session: AsyncSession,
) -> None:
    user = await _user(db_session)
    unit = await _unit(db_session)
    attempt = await start_practice_attempt(
        db_session,
        user_id=user.id,
        unit_id=unit.id,
        skill="reading",
        activity_type="reading_exercise",
        content_id="ex-1",
        sonolo_level=2,
    )
    await finalize_practice_attempt(db_session, attempt, score=70.0, result_json={})
    await record_unit_test_skill_score(
        db_session,
        user_id=user.id,
        unit_id=unit.id,
        skill="reading",
        score=88.0,
    )
    assert await last_exercise_scores(db_session, user.id, "reading", 2) == [70.0]
    assert await unit_test_skill_scores(db_session, user.id, "reading") == [88.0]
    tests = (
        await db_session.execute(select(UnitTestSkillEvidence))
    ).scalars().all()
    assert len(tests) == 1
