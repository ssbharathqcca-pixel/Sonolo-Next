"""Listening Gym API (SN-050) plus C5 dictation/unit_id extensions.

Existing endpoints and EvaluateOut fields for MC quizzes are preserved.
C5 adds optional dictation answers, unit linkage, transcript-after-submit,
and evidence recording without changing SN-050 mock MC scoring.
"""

import zlib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import SUBSCRIPTION_PREMIUM, User
from app.services.content_service import (
    ListeningDialogue,
    content_unit_id,
    get_unit_document,
    load_listening_dialogues,
    persist_curriculum,
)
from app.services.evidence_service import (
    finalize_practice_attempt,
    latest_submitted_attempt,
    orchestrate_after_practice,
    refresh_listening_complete,
    start_practice_attempt,
)
from app.services.listening_service import (
    ACTIVITY_LISTENING_EXERCISE,
    FULL_REPLAY_MAX,
    SPEEDS_ALL_LEVELS,
    SPEED_L4_PLUS,
    mean_listening_scores,
    score_dictation_segment,
    score_multiple_choice,
    score_sequence,
    score_true_false,
)
from app.models.curriculum import Unit
from sqlalchemy import select

router = APIRouter(prefix="/listening", tags=["listening"])

#: Deterministic mock evaluator version (SN-050).
ENGINE_VERSION = "sn050-mock-listening-v1"


class DialogueSummaryOut(BaseModel):
    """One dialogue as shown in the Learn tab's Listening Gym rail."""

    id: str
    title: str
    context: str
    level: str
    difficulty: float
    listening_focus: str
    is_premium: bool
    #: True when the dialogue is premium and the caller is on the free tier.
    is_locked: bool = False
    theme_color: str
    icon: str
    unit_id: str | None = None
    sonolo_level: int | None = None


class DialogueListResponse(BaseModel):
    """The listening dialogue catalog."""

    dialogues: list[DialogueSummaryOut]


class ListeningTurnOut(BaseModel):
    """One spoken turn of a dialogue."""

    role: str
    text: str
    pause_after_ms: int


class ListeningQuestionOut(BaseModel):
    """One comprehension question."""

    prompt: str
    choices: list[str]
    correct_index: int
    explanation: str


class DictationPromptOut(BaseModel):
    """Dictation item without the answer text (transcript after submit)."""

    turn_index: int
    key_word_count: int


class DialogueOut(BaseModel):
    """The full dialogue body for the mobile player."""

    id: str
    title: str
    context: str
    level: str
    difficulty: float
    listening_focus: str
    is_premium: bool
    turns: list[ListeningTurnOut]
    questions: list[ListeningQuestionOut]
    vocab_targets: list[str]
    pack_id: str
    theme_color: str
    icon: str
    unit_id: str | None = None
    sonolo_level: int | None = None
    dictation_prompts: list[DictationPromptOut] = Field(default_factory=list)
    transcript: list[str] | None = None
    transcript_available: bool = False
    replay_full_max: int = FULL_REPLAY_MAX
    speeds: list[float] = Field(default_factory=lambda: list(SPEEDS_ALL_LEVELS))


class EvaluateRequest(BaseModel):
    """Body for POST /listening/dialogues/{id}/evaluate."""

    answers: list[int]
    time_seconds: int
    dictation: list[str] | None = None
    sequence: list[list[int]] | None = None
    full_replays: int = 0


class MissedOut(BaseModel):
    """One incorrectly answered question."""

    prompt: str
    your_answer: str
    correct_answer: str
    explanation: str


class EvaluateOut(BaseModel):
    """The deterministic mock listening evaluation."""

    correct_count: int
    total: int
    score: int
    missed: list[MissedOut]
    time_seconds: int
    engine_version: str
    unit_id: str | None = None
    dictation_scores: list[float] = Field(default_factory=list)
    transcript: list[str] | None = None
    full_replays: int = 0


def _is_locked(dialogue: ListeningDialogue, user: User) -> bool:
    return dialogue.is_premium and user.subscription_tier != SUBSCRIPTION_PREMIUM


def mock_listening_evaluation(
    dialogue: ListeningDialogue, answers: list[int], time_seconds: int
) -> EvaluateOut:
    """Deterministic mock scoring for a listening quiz.

    All numbers derive from `zlib.crc32(dialogue_id + sorted(answers))`
    so the same dialogue and answers always score identically. The
    correct_count comes from comparing answers to the dialogue's real
    correct_index values; the crc seed is used only for the missed-list
    order, which stays stable for identical input.
    """
    total = len(dialogue.questions)
    correct = 0
    missed: list[MissedOut] = []
    for answer, question in zip(answers, dialogue.questions):
        if answer == question.correct_index:
            correct += 1
        else:
            missed.append(
                MissedOut(
                    prompt=question.prompt,
                    your_answer=question.choices[answer],
                    correct_answer=question.choices[question.correct_index],
                    explanation=question.explanation,
                )
            )
    # The crc seed exists to keep the payload derivable from input;
    # correct_count and score already derive deterministically from the
    # answers, and the missed list stays in question order.
    zlib.crc32(dialogue.id.encode() + str(sorted(answers)).encode())
    score = round((correct / total) * 100) if total else 0
    return EvaluateOut(
        correct_count=correct,
        total=total,
        score=score,
        missed=missed,
        time_seconds=time_seconds,
        engine_version=ENGINE_VERSION,
    )


@router.get("/dialogues", response_model=DialogueListResponse)
async def list_dialogues(
    current_user: User = Depends(get_current_user),
) -> DialogueListResponse:
    """Return every Listening Gym dialogue with lock state (SN-050)."""
    dialogues = load_listening_dialogues()
    return DialogueListResponse(
        dialogues=[
            DialogueSummaryOut(
                id=dialogue.id,
                title=dialogue.title,
                context=dialogue.context,
                level=dialogue.level,
                difficulty=dialogue.difficulty,
                listening_focus=dialogue.listening_focus,
                is_premium=dialogue.is_premium,
                is_locked=_is_locked(dialogue, current_user),
                theme_color=dialogue.theme_color,
                icon=dialogue.icon,
                unit_id=dialogue.unit_id,
                sonolo_level=dialogue.sonolo_level,
            )
            for dialogue in dialogues
        ]
    )


@router.get("/dialogues/{dialogue_id}", response_model=DialogueOut)
async def get_dialogue(
    dialogue_id: str,
    current_user: User = Depends(get_current_user),
) -> DialogueOut:
    """Return one full dialogue, or 404 for an unknown id.

    A premium dialogue requested by a free-tier caller is a 403 (SN-041
    enforcement) — the mobile client shows the paywall instead.
    """
    for dialogue in load_listening_dialogues():
        if dialogue.id == dialogue_id:
            if _is_locked(dialogue, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This dialogue requires a premium subscription.",
                )
            return DialogueOut(
                id=dialogue.id,
                title=dialogue.title,
                context=dialogue.context,
                level=dialogue.level,
                difficulty=dialogue.difficulty,
                listening_focus=dialogue.listening_focus,
                is_premium=dialogue.is_premium,
                turns=[
                    ListeningTurnOut(
                        role=turn.role,
                        text=turn.text,
                        pause_after_ms=turn.pause_after_ms,
                    )
                    for turn in dialogue.turns
                ],
                questions=[
                    ListeningQuestionOut(
                        prompt=question.prompt,
                        choices=question.choices,
                        correct_index=question.correct_index,
                        explanation=question.explanation,
                    )
                    for question in dialogue.questions
                ],
                vocab_targets=dialogue.vocab_targets,
                pack_id=dialogue.pack_id,
                theme_color=dialogue.theme_color,
                icon=dialogue.icon,
                unit_id=dialogue.unit_id,
                sonolo_level=dialogue.sonolo_level,
                dictation_prompts=[
                    DictationPromptOut(
                        turn_index=segment.turn_index,
                        key_word_count=len(segment.key_words),
                    )
                    for segment in (dialogue.dictation_segments or [])
                ],
                transcript=None,
                transcript_available=False,
                replay_full_max=FULL_REPLAY_MAX,
                speeds=list(SPEEDS_ALL_LEVELS)
                + (
                    [SPEED_L4_PLUS]
                    if dialogue.sonolo_level is not None
                    and dialogue.sonolo_level >= 4
                    else []
                ),
            )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Listening dialogue not found.",
    )


def _curriculum_evaluation(
    dialogue: ListeningDialogue, payload: EvaluateRequest
) -> EvaluateOut:
    """MC/TF/sequence + dictation mean (Part XI). Used when dictation is sent."""
    question_scores: list[float] = []
    missed: list[MissedOut] = []
    sequence_iter = iter(payload.sequence or [])
    for index, question in enumerate(dialogue.questions):
        submitted = payload.answers[index] if index < len(payload.answers) else -1
        qtype = question.type
        if qtype == "true_false":
            points = score_true_false(question.correct_index, submitted)
        elif qtype == "sequence":
            order = question.correct_order or []
            got = next(sequence_iter, [])
            points = score_sequence(order, got)
        else:
            points = score_multiple_choice(question.correct_index, submitted)
        question_scores.append(points)
        if points < 100 and qtype != "sequence" and 0 <= submitted < len(question.choices):
            missed.append(
                MissedOut(
                    prompt=question.prompt,
                    your_answer=question.choices[submitted],
                    correct_answer=question.choices[question.correct_index],
                    explanation=question.explanation,
                )
            )
    dictation_scores: list[float] = []
    segments = dialogue.dictation_segments or []
    submitted_dictation = payload.dictation or []
    for index, segment in enumerate(segments):
        typed = submitted_dictation[index] if index < len(submitted_dictation) else ""
        dictation_scores.append(score_dictation_segment(segment.text, typed))
    all_scores = question_scores + dictation_scores
    score = round(mean_listening_scores(all_scores)) if all_scores else 0
    correct_count = sum(1 for item in question_scores if item == 100)
    transcript = [segment.text for segment in segments] if segments else None
    return EvaluateOut(
        correct_count=correct_count,
        total=len(dialogue.questions),
        score=score,
        missed=missed,
        time_seconds=payload.time_seconds,
        engine_version=ENGINE_VERSION,
        unit_id=dialogue.unit_id,
        dictation_scores=dictation_scores,
        transcript=transcript,
        full_replays=payload.full_replays,
    )


async def _record_listening_evidence(
    db: AsyncSession,
    user: User,
    dialogue: ListeningDialogue,
    payload: EvaluateRequest,
    result: EvaluateOut,
) -> None:
    fingerprint = {
        "answers": payload.answers,
        "dictation": payload.dictation,
        "sequence": payload.sequence,
    }
    latest = await latest_submitted_attempt(db, user.id, dialogue.id)
    if latest is not None and (latest.result_json or {}).get("fingerprint") == fingerprint:
        return
    await persist_curriculum(db)
    unit_pk = None
    if dialogue.unit_id:
        unit_row = (
            await db.execute(
                select(Unit).where(
                    Unit.unit_code == dialogue.unit_id,
                    Unit.language.in_(("en-CA", "en")),
                )
            )
        ).scalar_one_or_none()
        unit_pk = unit_row.id if unit_row is not None else content_unit_id(
            dialogue.unit_id, "en-CA"
        )
    attempt = await start_practice_attempt(
        db,
        user_id=user.id,
        unit_id=unit_pk,
        skill="listening",
        activity_type=ACTIVITY_LISTENING_EXERCISE,
        content_id=dialogue.id,
        sonolo_level=dialogue.sonolo_level,
    )
    await finalize_practice_attempt(
        db,
        attempt,
        score=float(result.score),
        result_json={"fingerprint": fingerprint, "score": result.score},
    )
    await orchestrate_after_practice(db, attempt)
    if unit_pk is not None:
        unit_doc = get_unit_document(dialogue.unit_id or "")
        required = list((unit_doc or {}).get("listening_ids") or [])
        await refresh_listening_complete(
            db,
            user_id=user.id,
            unit_id=unit_pk,
            required_content_ids=required,
        )
    await db.commit()


@router.post("/dialogues/{dialogue_id}/evaluate", response_model=EvaluateOut)
async def evaluate_dialogue(
    dialogue_id: str,
    payload: EvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EvaluateOut:
    """Score one quiz take. MC-only path stays the SN-050 mock evaluator."""
    dialogues = load_listening_dialogues()
    if not any(dialogue.id == dialogue_id for dialogue in dialogues):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Listening dialogue not found.",
        )
    for dialogue in dialogues:
        if dialogue.id == dialogue_id and _is_locked(dialogue, current_user):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="This dialogue requires a premium subscription.",
            )
        if dialogue.id == dialogue_id:
            if payload.dictation is not None or payload.sequence:
                result = _curriculum_evaluation(dialogue, payload)
            else:
                result = mock_listening_evaluation(
                    dialogue, payload.answers, payload.time_seconds
                )
                result.unit_id = dialogue.unit_id
                result.full_replays = payload.full_replays
            await _record_listening_evidence(db, current_user, dialogue, payload, result)
            return result
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Listening dialogue not found.",
    )
