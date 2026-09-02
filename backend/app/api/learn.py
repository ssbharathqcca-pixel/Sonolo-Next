"""Curriculum learn APIs: units (C1), reading/writing, Daily Mix, diagnostic, journey."""

from __future__ import annotations

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.curriculum import ReadingExercise, Unit, WritingExercise
from app.models.vocabulary import VocabularyCard
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.models.user import User
from app.services.ai import LLMProvider, get_ai_bundle
from app.services.content_service import (
    content_vocabulary_card_id,
    get_grammar_document_for_unit,
    get_hunt_document,
    get_reading_document,
    get_unit_document,
    get_unit_test_document,
    get_vocabulary_seed,
    get_writing_document,
    persist_curriculum,
    required_reading_activity_ids,
)
from app.services.evidence_service import (
    count_submitted_attempts,
    find_active_attempt,
    finalize_practice_attempt,
    get_or_create_unit_progress,
    latest_submitted_attempt,
    latest_unit_test_sitting,
    orchestrate_after_practice,
    orchestrate_after_unit_test,
    record_unit_test_sitting,
    refresh_reading_complete,
    refresh_writing_complete,
    reject_late_attempt,
    start_practice_attempt,
    submitted_content_ids,
)
from app.services.reading_service import (
    ACTIVITY_READING_EXERCISE,
    ACTIVITY_VOCABULARY_HUNT,
    is_late,
    normalize_answer,
    score_reading_submission,
    score_vocabulary_hunt,
    time_limit_minutes,
)
from app.services.daily_mix_service import build_daily_mix_for_user
from app.core.time import utc_now
from app.services.journey_service import load_journey_for_user
from app.services.unit_test_service import (
    RETRY_COOLDOWN,
    RETRY_MESSAGE,
    grade_unit_test,
    public_unit_test,
    submission_fingerprint,
)
from app.services.diagnostic_service import (
    check_placement_correction,
    complete_diagnostic,
    skip_all_and_place,
    skip_skills,
    start_diagnostic,
    submit_answer,
)
from app.services.writing_service import (
    ACTIVITY_WRITING_EXERCISE,
    MAX_REVISIONS,
    score_writing_submission,
)

router = APIRouter(prefix="/learn", tags=["learn"])


def get_llm_provider() -> LLMProvider:
    """Existing AI bundle (SN-016). Tests override this dependency."""
    return get_ai_bundle().llm


class ReadingQuestionPublic(BaseModel):
    id: str
    type: str
    question: str
    options: list[str] | None = None
    skill_tested: str | None = None


class ReadingExerciseOut(BaseModel):
    id: str
    unit_id: str | None
    title: str
    language: str
    text_content: str
    text_type: str
    level: int | None
    word_count: int | None
    questions: list[ReadingQuestionPublic]
    cultural_note: str | None = None
    time_limit_minutes: int | None = None


class StartAttemptOut(BaseModel):
    attempt_id: UUID
    content_id: str
    started_at: str
    time_limit_minutes: int | None
    reused: bool


class ReadingSubmitRequest(BaseModel):
    attempt_id: UUID
    answers: dict[str, Any]


class HuntSubmitRequest(BaseModel):
    attempt_id: UUID
    found_words: list[str] = Field(default_factory=list)


class QuestionScoreOut(BaseModel):
    id: str
    type: str | None
    score: float


class WordResultOut(BaseModel):
    word: str
    found: bool


class ReadingSubmitOut(BaseModel):
    attempt_id: UUID
    score: float
    question_scores: list[QuestionScoreOut]
    activity_complete: bool
    reading_complete: bool
    required_remaining: list[str]
    mastery: dict[str, Any]
    idempotent_replayed: bool = False


class HuntOut(BaseModel):
    id: str
    unit_id: str
    title: str
    language: str
    level: int
    reading_exercise_id: str
    target_word_count: int
    text_content: str


class HuntSubmitOut(BaseModel):
    attempt_id: UUID
    score: float
    word_results: list[WordResultOut]
    activity_complete: bool
    reading_complete: bool
    required_remaining: list[str]
    mastery: dict[str, Any]
    idempotent_replayed: bool = False


class UnitOut(BaseModel):
    id: str
    band: str
    title: str
    story_chapter: str
    theme: str
    icon: str
    level_target: int
    language: str
    vocabulary_targets: list[str]
    grammar_targets: list[str]
    reading_ids: list[str]
    writing_ids: list[str]
    listening_ids: list[str]
    speaking_ids: list[str]
    vocab_primer_ids: list[str] = Field(default_factory=list)
    grammar_spotlight_id: str | None = None
    reading_required_activities: list[dict[str, str]]
    reading_optional_activities: list[dict[str, str]]
    unit_test_id: str | None
    is_published: bool


async def _ensure_catalog(db: AsyncSession) -> None:
    await persist_curriculum(db)


def _public_questions(questions: list[dict[str, Any]]) -> list[ReadingQuestionPublic]:
    public: list[ReadingQuestionPublic] = []
    for question in questions:
        public.append(
            ReadingQuestionPublic(
                id=str(question["id"]),
                type=str(question["type"]),
                question=str(question["question"]),
                options=question.get("options"),
                skill_tested=question.get("skill_tested"),
            )
        )
    return public


async def _unit_row(db: AsyncSession, unit_code: str, language: str) -> Unit | None:
    result = await db.execute(
        select(Unit).where(Unit.unit_code == unit_code, Unit.language == language)
    )
    return result.scalar_one_or_none()


async def _reading_row(db: AsyncSession, content_id: str) -> ReadingExercise | None:
    result = await db.execute(
        select(ReadingExercise).where(ReadingExercise.content_id == content_id)
    )
    return result.scalar_one_or_none()


def _progress_fields(
    required_ids: list[str], completed: set[str]
) -> tuple[bool, list[str]]:
    remaining = [item for item in required_ids if item not in completed]
    return (not remaining), remaining


@router.get("/units/{unit_code}", response_model=UnitOut)
async def get_unit(
    unit_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> UnitOut:
    """C1: published unit with linked content IDs (N9)."""
    del current_user
    await _ensure_catalog(db)
    document = get_unit_document(unit_code)
    if document is None or not document.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    return UnitOut(
        id=str(document["id"]),
        band=str(document["band"]),
        title=str(document["title"]),
        story_chapter=str(document.get("story_chapter") or ""),
        theme=str(document.get("theme") or ""),
        icon=str(document.get("icon") or ""),
        level_target=int(document["level_target"]),
        language=str(document["language"]),
        vocabulary_targets=list(document.get("vocabulary_targets") or []),
        grammar_targets=list(document.get("grammar_targets") or []),
        reading_ids=list(document.get("reading_ids") or []),
        writing_ids=list(document.get("writing_ids") or []),
        listening_ids=list(document.get("listening_ids") or []),
        speaking_ids=list(document.get("speaking_ids") or []),
        vocab_primer_ids=list(document.get("vocab_primer_ids") or []),
        grammar_spotlight_id=document.get("grammar_spotlight_id") or None,
        reading_required_activities=list(
            document.get("reading_required_activities") or []
        ),
        reading_optional_activities=list(
            document.get("reading_optional_activities") or []
        ),
        unit_test_id=document.get("unit_test_id"),
        is_published=True,
    )


def _published_unit(unit_code: str) -> dict[str, Any]:
    document = get_unit_document(unit_code)
    if document is None or not document.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    return document


@router.get("/vocab-primer/{unit_code}")
async def get_vocab_primer(
    unit_code: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """F3 Vocab Primer: the unit's 20 flashcard targets. File-backed.

    Does not write FSRS state. Cards enter the existing review pool when
    `/api/review/due` materializes vocabulary packs (same content ids).
    """
    del current_user
    document = _published_unit(unit_code)
    primer_ids = [str(item) for item in (document.get("vocab_primer_ids") or [])]
    if not primer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vocab primer not found.")
    items: list[dict[str, Any]] = []
    for content_id in primer_ids:
        seed = get_vocabulary_seed(content_id)
        if seed is None or not seed.is_published:
            continue
        items.append(
            {
                "id": seed.content_id,
                "word": seed.word,
                "phonetic": seed.phonetic,
                "translations": seed.translations,
                "example_sentences": list(seed.example_sentences),
                "unit_id": seed.unit_id,
                "sonolo_level": seed.sonolo_level,
                "language": seed.language,
            }
        )
    if not items:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Vocab primer not found.")
    return {
        "unit_id": str(document["id"]),
        "language": str(document["language"]),
        "level": int(document["level_target"]),
        "ids": [item["id"] for item in items],
        "items": items,
    }


@router.get("/grammar/{unit_code}")
async def get_grammar_spotlight(
    unit_code: str,
    current_user: User = Depends(get_current_user),
) -> dict[str, Any]:
    """F3 Grammar Spotlight. File-backed; no mastery engine, no LLM."""
    del current_user
    _published_unit(unit_code)
    document = get_grammar_document_for_unit(unit_code)
    if document is None or not document.get("is_published", False):
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, detail="Grammar spotlight not found."
        )
    return {
        "id": str(document["id"]),
        "unit_id": str(document.get("unit_id") or unit_code),
        "language": str(document.get("language")),
        "level": document.get("level"),
        "title": str(document.get("title") or ""),
        "read_minutes": document.get("read_minutes"),
        "grammar_targets": list(document.get("grammar_targets") or []),
        "explanation": str(document.get("explanation") or ""),
        "sections": list(document.get("sections") or []),
        "examples": list(document.get("examples") or []),
        "try_it": document.get("try_it"),
        "is_published": True,
    }


@router.get("/review/{unit_code}")
async def get_unit_review(
    unit_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """F3 Review & Reinforce: unit-bound projection over existing FSRS cards.

    Does not schedule, score, or complete the unit. Answers still go
    through POST /api/review/answer. Generic GET /api/review/due is unchanged.
    """
    document = _published_unit(unit_code)
    primer_ids = [str(item) for item in (document.get("vocab_primer_ids") or [])]
    if not primer_ids:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit review not found.")
    cards: list[dict[str, Any]] = []
    for content_id in primer_ids:
        seed = get_vocabulary_seed(content_id)
        if seed is None or not seed.is_published:
            continue
        card_pk = content_vocabulary_card_id(current_user.id, content_id)
        stored = await db.get(VocabularyCard, card_pk)
        cards.append(
            {
                "content_id": seed.content_id,
                "word": seed.word,
                "translations": seed.translations,
                "card_id": str(stored.id) if stored is not None else None,
                "state": stored.state if stored is not None else None,
                "due_date": stored.due_date.isoformat() if stored is not None else None,
                "reps": stored.reps if stored is not None else None,
            }
        )
    return {
        "unit_id": str(document["id"]),
        "type": "unit_review",
        "fsrs": True,
        "vocabulary_ids": [item["content_id"] for item in cards],
        "vocabulary_targets": list(document.get("vocabulary_targets") or []),
        "grammar_spotlight_id": document.get("grammar_spotlight_id") or None,
        "situation": str(document.get("story_chapter") or ""),
        "theme": str(document.get("theme") or ""),
        "cards": cards,
        "completes_unit": False,
    }


class JourneySkillOut(BaseModel):
    skill: str
    status: str


class JourneyUnitOut(BaseModel):
    id: str
    title: str
    status: str
    skills: list[JourneySkillOut]


class JourneyBandOut(BaseModel):
    id: str
    title: str
    subtitle: str
    icon: str
    status: str
    expanded: bool
    unlock_condition: str | None = None
    units: list[JourneyUnitOut]


class JourneyOut(BaseModel):
    current_unit_id: str | None
    bands: list[JourneyBandOut]


@router.get("/journey", response_model=JourneyOut)
async def get_journey(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> JourneyOut:
    """C8 Journey Map. Progress from ``user_unit_progress`` only."""
    payload = await load_journey_for_user(db, current_user.id)
    return JourneyOut.model_validate(payload)


class UnitTestSubmitRequest(BaseModel):
    listening: dict[str, Any] = Field(default_factory=dict)
    reading: dict[str, Any] = Field(default_factory=dict)
    speaking: dict[str, Any] = Field(default_factory=dict)
    writing: dict[str, Any] = Field(default_factory=dict)


def _unit_test_unlocked(progress) -> bool:
    return bool(
        progress.speaking_complete
        and progress.listening_complete
        and progress.reading_complete
        and progress.writing_complete
    )


@router.get("/unit-test/{unit_code}")
async def get_unit_test(
    unit_code: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Published unit test without answer keys (N9)."""
    del current_user
    await persist_curriculum(db)
    document = get_unit_test_document(unit_code)
    if document is None or not document.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit test not found.")
    return public_unit_test(document)


@router.post("/unit-test/{unit_code}/submit")
async def submit_unit_test(
    unit_code: str,
    payload: UnitTestSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Grade F3 unit test, persist evidence, and invoke C2 orchestration."""
    await persist_curriculum(db)
    document = get_unit_test_document(unit_code)
    if document is None or not document.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit test not found.")
    unit = await _unit_row(db, unit_code, str(document.get("language") or "en-CA"))
    if unit is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Unit not found.")
    progress = await get_or_create_unit_progress(db, current_user.id, unit.id)
    if not _unit_test_unlocked(progress):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Unit test is locked until all four skill blocks are complete.",
        )
    answers = payload.model_dump()
    fingerprint = submission_fingerprint(answers)
    latest = await latest_unit_test_sitting(db, current_user.id, unit.id)
    if latest is not None:
        stored = latest.result_json or {}
        if stored.get("fingerprint") == fingerprint:
            await db.commit()
            return {
                "overall": stored.get("overall"),
                "per_skill": stored.get("per_skill"),
                "passed": stored.get("passed"),
                "fail_message": stored.get("fail_message"),
                "idempotent_replayed": True,
                "mastery": {},
            }
        submitted_at = latest.submitted_at
        if submitted_at.tzinfo is None:
            submitted_at = submitted_at.replace(tzinfo=utc_now().tzinfo)
        if utc_now() - submitted_at < RETRY_COOLDOWN:
            raise HTTPException(status.HTTP_409_CONFLICT, detail=RETRY_MESSAGE)
    result = await grade_unit_test(document, answers)
    result["fingerprint"] = fingerprint
    await record_unit_test_sitting(
        db,
        user_id=current_user.id,
        unit_id=unit.id,
        overall=float(result["overall"]),
        skill_scores=dict(result["per_skill"]),
        passed=bool(result["passed"]),
        result_json=result,
    )
    mastery = await orchestrate_after_unit_test(
        db,
        user_id=current_user.id,
        sonolo_level=int(document.get("level") or 2),
    )
    await db.commit()
    return {
        "overall": result["overall"],
        "per_skill": result["per_skill"],
        "passed": result["passed"],
        "sections": result["sections"],
        "fail_message": result["fail_message"],
        "idempotent_replayed": False,
        "mastery": mastery,
    }


@router.get("/reading/{content_id}", response_model=ReadingExerciseOut)
async def get_reading(
    content_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadingExerciseOut:
    del current_user
    await _ensure_catalog(db)
    row = await _reading_row(db, content_id)
    if row is None or not row.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reading not found.")
    unit_code = None
    if row.unit_id is not None:
        unit = await db.get(Unit, row.unit_id)
        unit_code = unit.unit_code if unit is not None else None
    return ReadingExerciseOut(
        id=row.content_id,
        unit_id=unit_code,
        title=row.title,
        language=row.language,
        text_content=row.text_content,
        text_type=row.text_type,
        level=row.sonolo_level,
        word_count=row.word_count,
        questions=_public_questions(list(row.questions)),
        cultural_note=row.cultural_note,
        time_limit_minutes=time_limit_minutes(row.sonolo_level),
    )


@router.post("/reading/{content_id}/start", response_model=StartAttemptOut)
async def start_reading(
    content_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StartAttemptOut:
    await _ensure_catalog(db)
    row = await _reading_row(db, content_id)
    if row is None or not row.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reading not found.")
    existing = await find_active_attempt(db, current_user.id, content_id)
    reused = existing is not None
    attempt = await start_practice_attempt(
        db,
        user_id=current_user.id,
        unit_id=row.unit_id,
        skill="reading",
        activity_type=ACTIVITY_READING_EXERCISE,
        content_id=content_id,
        sonolo_level=row.sonolo_level,
    )
    await db.commit()
    return StartAttemptOut(
        attempt_id=attempt.id,
        content_id=content_id,
        started_at=attempt.started_at.isoformat(),
        time_limit_minutes=time_limit_minutes(row.sonolo_level),
        reused=reused,
    )


@router.post("/reading/{content_id}/submit", response_model=ReadingSubmitOut)
async def submit_reading(
    content_id: str,
    payload: ReadingSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ReadingSubmitOut:
    await _ensure_catalog(db)
    row = await _reading_row(db, content_id)
    if row is None or not row.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Reading not found.")
    attempt_row = await db.get(SkillExerciseAttempt, payload.attempt_id)
    if (
        attempt_row is None
        or attempt_row.user_id != current_user.id
        or attempt_row.content_id != content_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Attempt not found.")

    unit_doc = None
    required_ids: list[str] = []
    if row.unit_id is not None:
        unit = await db.get(Unit, row.unit_id)
        if unit is not None:
            unit_doc = get_unit_document(unit.unit_code)
            if unit_doc is not None:
                required_ids = required_reading_activity_ids(unit_doc)

    if attempt_row.status == ATTEMPT_SUBMITTED:
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        complete, remaining = _progress_fields(required_ids, completed)
        details = list((attempt_row.result_json or {}).get("question_scores") or [])
        return ReadingSubmitOut(
            attempt_id=attempt_row.id,
            score=float(attempt_row.score or 0),
            question_scores=[QuestionScoreOut(**item) for item in details],
            activity_complete=True,
            reading_complete=complete,
            required_remaining=remaining,
            mastery={},
            idempotent_replayed=True,
        )

    if is_late(attempt_row.started_at, row.sonolo_level):
        await reject_late_attempt(
            db, attempt_row, result_json={"reason": "late_submission"}
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Late submission rejected.",
        )

    document = get_reading_document(content_id) or {}
    questions = list(document.get("questions") or row.questions)
    for question in questions:
        if question.get("type") == "short_answer":
            question.setdefault("sonolo_level", row.sonolo_level)
            if row.sonolo_level is not None and row.sonolo_level >= 4:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail="L4+ short-answer scoring is deferred.",
                )
    score, details = score_reading_submission(questions, payload.answers)
    await finalize_practice_attempt(
        db,
        attempt_row,
        score=score,
        result_json={"question_scores": details},
    )
    mastery = await orchestrate_after_practice(db, attempt_row)
    reading_complete = False
    remaining = required_ids
    if row.unit_id is not None:
        reading_complete = await refresh_reading_complete(
            db,
            user_id=current_user.id,
            unit_id=row.unit_id,
            required_content_ids=required_ids,
        )
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        _, remaining = _progress_fields(required_ids, completed)
    await db.commit()
    return ReadingSubmitOut(
        attempt_id=attempt_row.id,
        score=score,
        question_scores=[QuestionScoreOut(**item) for item in details],
        activity_complete=True,
        reading_complete=reading_complete,
        required_remaining=remaining,
        mastery=mastery,
        idempotent_replayed=False,
    )


@router.get("/vocabulary-hunt/{content_id}", response_model=HuntOut)
async def get_hunt(
    content_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HuntOut:
    del current_user
    await _ensure_catalog(db)
    hunt = get_hunt_document(content_id)
    if hunt is None or not hunt.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hunt not found.")
    reading = await _reading_row(db, str(hunt["reading_exercise_id"]))
    if reading is None or not reading.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hunt not found.")
    return HuntOut(
        id=str(hunt["id"]),
        unit_id=str(hunt["unit_id"]),
        title=str(hunt["title"]),
        language=str(hunt["language"]),
        level=int(hunt["level"]),
        reading_exercise_id=str(hunt["reading_exercise_id"]),
        target_word_count=len(hunt.get("target_words") or []),
        text_content=reading.text_content,
    )


@router.post("/vocabulary-hunt/{content_id}/start", response_model=StartAttemptOut)
async def start_hunt(
    content_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StartAttemptOut:
    await _ensure_catalog(db)
    hunt = get_hunt_document(content_id)
    if hunt is None or not hunt.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hunt not found.")
    unit = await _unit_row(db, str(hunt["unit_id"]), str(hunt["language"]))
    existing = await find_active_attempt(db, current_user.id, content_id)
    reused = existing is not None
    attempt = await start_practice_attempt(
        db,
        user_id=current_user.id,
        unit_id=unit.id if unit is not None else None,
        skill="reading",
        activity_type=ACTIVITY_VOCABULARY_HUNT,
        content_id=content_id,
        sonolo_level=int(hunt["level"]),
    )
    await db.commit()
    return StartAttemptOut(
        attempt_id=attempt.id,
        content_id=content_id,
        started_at=attempt.started_at.isoformat(),
        time_limit_minutes=time_limit_minutes(int(hunt["level"])),
        reused=reused,
    )


@router.post("/vocabulary-hunt/{content_id}/submit", response_model=HuntSubmitOut)
async def submit_hunt(
    content_id: str,
    payload: HuntSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> HuntSubmitOut:
    await _ensure_catalog(db)
    hunt = get_hunt_document(content_id)
    if hunt is None or not hunt.get("is_published", False):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Hunt not found.")
    attempt_row = await db.get(SkillExerciseAttempt, payload.attempt_id)
    if (
        attempt_row is None
        or attempt_row.user_id != current_user.id
        or attempt_row.content_id != content_id
    ):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Attempt not found.")

    unit_doc = get_unit_document(str(hunt["unit_id"]))
    required_ids = required_reading_activity_ids(unit_doc) if unit_doc else []
    unit = await _unit_row(db, str(hunt["unit_id"]), str(hunt["language"]))

    if attempt_row.status == ATTEMPT_SUBMITTED:
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        complete, remaining = _progress_fields(required_ids, completed)
        words = list((attempt_row.result_json or {}).get("word_results") or [])
        return HuntSubmitOut(
            attempt_id=attempt_row.id,
            score=float(attempt_row.score or 0),
            word_results=[WordResultOut(**item) for item in words],
            activity_complete=True,
            reading_complete=complete,
            required_remaining=remaining,
            mastery={},
            idempotent_replayed=True,
        )

    if is_late(attempt_row.started_at, int(hunt["level"])):
        await reject_late_attempt(
            db, attempt_row, result_json={"reason": "late_submission"}
        )
        await db.commit()
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Late submission rejected.",
        )

    score, word_results = score_vocabulary_hunt(
        list(hunt.get("target_words") or []), payload.found_words
    )
    await finalize_practice_attempt(
        db,
        attempt_row,
        score=score,
        result_json={"word_results": word_results},
    )
    mastery = await orchestrate_after_practice(db, attempt_row)
    reading_complete = False
    remaining = required_ids
    if unit is not None:
        reading_complete = await refresh_reading_complete(
            db,
            user_id=current_user.id,
            unit_id=unit.id,
            required_content_ids=required_ids,
        )
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        _, remaining = _progress_fields(required_ids, completed)
    await db.commit()
    return HuntSubmitOut(
        attempt_id=attempt_row.id,
        score=score,
        word_results=[WordResultOut(**item) for item in word_results],
        activity_complete=True,
        reading_complete=reading_complete,
        required_remaining=remaining,
        mastery=mastery,
        idempotent_replayed=False,
    )


class WritingExerciseOut(BaseModel):
    id: str
    unit_id: str | None
    title: str
    language: str
    exercise_type: str
    level: int | None
    prompt: str
    scaffold: str | None = None
    word_bank: list[str] | None = None
    error_text: str | None = None
    error_count: int | None = None
    word_count_target: dict[str, Any] = Field(default_factory=dict)
    grammar_targets: list[str] = Field(default_factory=list)
    vocabulary_targets: list[str] = Field(default_factory=list)
    rubric_criteria: list[str] = Field(default_factory=list)


class WritingSubmitRequest(BaseModel):
    text: str = ""
    found_errors: list[dict[str, str]] | None = None


class WritingSubmitOut(BaseModel):
    attempt_id: UUID
    score: float
    exercise_type: str
    revision: int
    revisions_remaining: int
    activity_complete: bool
    writing_complete: bool
    required_remaining: list[str]
    details: dict[str, Any]
    mastery: dict[str, Any]
    idempotent_replayed: bool = False


async def _writing_row(db: AsyncSession, content_id: str) -> WritingExercise | None:
    result = await db.execute(
        select(WritingExercise).where(WritingExercise.content_id == content_id)
    )
    return result.scalar_one_or_none()


def _writing_document(row: WritingExercise) -> dict[str, Any]:
    document = get_writing_document(row.content_id) or {}
    if document:
        return document
    return {
        "id": row.content_id,
        "exercise_type": row.exercise_type,
        "prompt": row.prompt,
        "scaffold": row.scaffold,
        "model_answer": row.model_answer,
        "word_count_target": row.word_count_target,
        "rubric": row.rubric,
        "vocabulary_targets": row.vocabulary_targets,
        "grammar_targets": row.grammar_targets,
        "word_bank": row.word_bank,
        "correct_sentence": row.correct_sentence,
        "error_text": row.error_text,
        "error_count": row.error_count,
        "corrected_text": row.corrected_text,
        "language": row.language,
        "level": row.sonolo_level,
    }


@router.get("/writing/{content_id}", response_model=WritingExerciseOut)
async def get_writing(
    content_id: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> WritingExerciseOut:
    del current_user
    await _ensure_catalog(db)
    row = await _writing_row(db, content_id)
    if row is None or not row.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Writing not found.")
    unit_code = None
    if row.unit_id is not None:
        unit = await db.get(Unit, row.unit_id)
        unit_code = unit.unit_code if unit is not None else None
    rubric = row.rubric or {}
    return WritingExerciseOut(
        id=row.content_id,
        unit_id=unit_code,
        title=row.title,
        language=row.language,
        exercise_type=row.exercise_type,
        level=row.sonolo_level,
        prompt=row.prompt,
        scaffold=row.scaffold,
        word_bank=list(row.word_bank) if row.word_bank else None,
        error_text=row.error_text,
        error_count=row.error_count,
        word_count_target=dict(row.word_count_target or {}),
        grammar_targets=list(row.grammar_targets or []),
        vocabulary_targets=list(row.vocabulary_targets or []),
        rubric_criteria=list(rubric.get("criteria") or []),
    )


@router.post("/writing/{content_id}/submit", response_model=WritingSubmitOut)
async def submit_writing(
    content_id: str,
    payload: WritingSubmitRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    llm: LLMProvider = Depends(get_llm_provider),
) -> WritingSubmitOut:
    await _ensure_catalog(db)
    row = await _writing_row(db, content_id)
    if row is None or not row.is_published:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Writing not found.")
    document = _writing_document(row)
    unit_doc = None
    required_ids: list[str] = []
    if row.unit_id is not None:
        unit = await db.get(Unit, row.unit_id)
        if unit is not None:
            unit_doc = get_unit_document(unit.unit_code)
            if unit_doc is not None:
                required_ids = [str(item) for item in (unit_doc.get("writing_ids") or [])]

    latest = await latest_submitted_attempt(db, current_user.id, content_id)
    if latest is not None and normalize_answer(
        str((latest.result_json or {}).get("text") or "")
    ) == normalize_answer(payload.text):
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        remaining = [item for item in required_ids if item not in completed]
        count = await count_submitted_attempts(db, current_user.id, content_id)
        return WritingSubmitOut(
            attempt_id=latest.id,
            score=float(latest.score or 0),
            exercise_type=row.exercise_type,
            revision=count,
            revisions_remaining=max(MAX_REVISIONS - count, 0),
            activity_complete=True,
            writing_complete=not remaining,
            required_remaining=remaining,
            details=dict(latest.result_json or {}),
            mastery={},
            idempotent_replayed=True,
        )

    count = await count_submitted_attempts(db, current_user.id, content_id)
    if count >= MAX_REVISIONS:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail="Maximum 3 revisions reached.",
        )

    previous_text = None
    if latest is not None:
        previous_text = str((latest.result_json or {}).get("text") or "") or None

    details = await score_writing_submission(
        exercise=document,
        text=payload.text,
        found_errors=payload.found_errors,
        sonolo_level=row.sonolo_level,
        llm=llm,
        previous_text=previous_text,
    )
    attempt = await start_practice_attempt(
        db,
        user_id=current_user.id,
        unit_id=row.unit_id,
        skill="writing",
        activity_type=ACTIVITY_WRITING_EXERCISE,
        content_id=content_id,
        sonolo_level=row.sonolo_level,
    )
    await finalize_practice_attempt(
        db,
        attempt,
        score=float(details["score"]),
        result_json=details,
    )
    mastery = await orchestrate_after_practice(db, attempt)
    writing_complete = False
    remaining = required_ids
    if row.unit_id is not None:
        writing_complete = await refresh_writing_complete(
            db,
            user_id=current_user.id,
            unit_id=row.unit_id,
            required_content_ids=required_ids,
        )
        completed = await submitted_content_ids(db, current_user.id, required_ids)
        remaining = [item for item in required_ids if item not in completed]
    await db.commit()
    revision_number = count + 1
    return WritingSubmitOut(
        attempt_id=attempt.id,
        score=float(details["score"]),
        exercise_type=row.exercise_type,
        revision=revision_number,
        revisions_remaining=max(MAX_REVISIONS - revision_number, 0),
        activity_complete=True,
        writing_complete=writing_complete,
        required_remaining=remaining,
        details=details,
        mastery=mastery,
        idempotent_replayed=False,
    )


class MixItemOut(BaseModel):
    type: str
    skill: str | None
    title: str
    duration_minutes: int
    priority: int
    content_id: str | None


class DailyMixOut(BaseModel):
    date: str
    items: list[MixItemOut]
    estimated_minutes: int
    focus_skill: str
    xp_possible: int
    weights: dict[str, float]
    welcome_back: bool
    imbalance: dict[str, Any]


@router.get("/daily-mix", response_model=DailyMixOut)
async def get_daily_mix(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> DailyMixOut:
    """C6 Daily Mix v2. Does not award XP or change C2."""
    mix = await build_daily_mix_for_user(db, current_user)
    await db.commit()
    return DailyMixOut(
        date=mix.date.isoformat(),
        items=[
            MixItemOut(
                type=item.type,
                skill=item.skill,
                title=item.title,
                duration_minutes=item.duration_minutes,
                priority=item.priority,
                content_id=item.content_id,
            )
            for item in mix.items
        ],
        estimated_minutes=mix.estimated_minutes,
        focus_skill=mix.focus_skill,
        xp_possible=mix.xp_possible,
        weights=mix.weights,
        welcome_back=mix.welcome_back,
        imbalance=mix.imbalance,
    )


class DiagnosticAnswerRequest(BaseModel):
    item_id: str
    answer: int


class DiagnosticSkipRequest(BaseModel):
    skills: list[str]


@router.post("/diagnostic/start")
async def diagnostic_start(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Start or resume the 4-skill placement diagnostic. Does not complete onboarding."""
    view = await start_diagnostic(db, current_user)
    await db.commit()
    return view


@router.get("/diagnostic")
async def diagnostic_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    view = await start_diagnostic(db, current_user)
    await db.commit()
    return view


@router.post("/diagnostic/answer")
async def diagnostic_answer(
    payload: DiagnosticAnswerRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await submit_answer(db, current_user, payload.item_id, payload.answer)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Item not found.") from exc
    await db.commit()
    return result


@router.post("/diagnostic/skip")
async def diagnostic_skip(
    payload: DiagnosticSkipRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        view = await skip_skills(db, current_user, payload.skills)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return view


@router.post("/diagnostic/skip-all")
async def diagnostic_skip_all(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    view = await skip_all_and_place(db, current_user)
    await db.commit()
    return view


@router.post("/diagnostic/complete")
async def diagnostic_complete(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        view = await complete_diagnostic(db, current_user)
    except ValueError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    await db.commit()
    return view


@router.post("/diagnostic/correction/{skill}")
async def diagnostic_correction(
    skill: str,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    try:
        result = await check_placement_correction(db, current_user, skill)
    except ValueError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    await db.commit()
    return result
