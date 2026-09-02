"""Pronunciation Lab API (SN-049): Canadian-speech drills.

Three endpoints, all authenticated:
- GET  /pronunciation/drills          -> summaries with is_locked (SN-026
                                        pattern: premium drills lock on the
                                        free tier).
- GET  /pronunciation/drills/{id}     -> full drill; 403 for a premium
                                        drill on the free tier (SN-041).
- POST /pronunciation/drills/{id}/evaluate -> deterministic mock phoneme
                                        scoring, derived from
                                        zlib.crc32(drill_id) so identical
                                        input always yields identical
                                        output (tests assert exact values).
"""

import zlib

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from sqlalchemy.ext.asyncio import AsyncSession

from app.api.dependencies import get_current_user
from app.db.session import get_db
from app.models.user import SUBSCRIPTION_PREMIUM, User
from app.services.content_service import (
    PronunciationDrill,
    get_pronunciation_drill,
    gym_pronunciation_drills,
    load_pronunciation_drills,
)
from app.services.evidence_service import (
    ACTIVITY_PRONUNCIATION_DRILL,
    record_speaking_practice,
)

router = APIRouter(prefix="/pronunciation", tags=["pronunciation"])

#: Deterministic mock evaluator version (SN-049); mirrors the
#: sn011-deterministic-v1 convention for mock evaluations.
ENGINE_VERSION = "sn049-mock-phoneme-v1"

#: IPA phoneme symbols used by the mock scoring; picks are seeded by the
#: drill id's crc32 so every evaluation is stable and replayable.
PHONEME_SYMBOLS = [
    "aʊ", "ʌ", "ə", "æ", "eɪ", "ɛ", "ɪ", "i", "ɒ", "ɔ", "oʊ", "u",
    "θ", "ð", "ɾ", "ŋ", "t", "d", "k", "ɡ", "r", "l", "w", "j", "h",
]

PHONEME_TIPS = [
    "Keep the vowel short and crisp — don't let it slide.",
    "Push the sound to the front of your mouth.",
    "Let the air flow freely — the tongue stays relaxed.",
    "Round your lips just a touch for this one.",
    "Listen for the rhythm, then mirror it back.",
    "Exaggerate the shape once, then bring it to natural size.",
]

SUMMARY_TIPS = [
    "Nice work — your mouth is finding the Canadian shapes.",
    "Solid take. Try the target sentence once more with the tip in mind.",
    "Good rhythm overall — the tricky sound just needs one more pass.",
    "You're close! Slow it down and let each sound land.",
    "Great effort — that's how the accent becomes yours.",
]


class DrillSummaryOut(BaseModel):
    """One drill as shown in the Learn tab's Pronunciation Lab rail."""

    id: str
    title: str
    focus: str
    level: str
    is_premium: bool
    #: True when the drill is premium and the caller is on the free tier.
    is_locked: bool = False
    theme_color: str
    icon: str


class DrillListResponse(BaseModel):
    """The pronunciation drill catalog."""

    drills: list[DrillSummaryOut]


class DrillOut(BaseModel):
    """The full drill body for the player screen."""

    id: str
    title: str
    focus: str
    target_sentence: str
    target_words: list[str]
    ipa_hint: str
    tip: str
    level: str
    is_premium: bool
    pack_id: str
    theme_color: str
    icon: str


class EvaluateRequest(BaseModel):
    """Body for POST /pronunciation/drills/{id}/evaluate."""

    duration_seconds: int


class PhonemeOut(BaseModel):
    """One phoneme with its deterministic mock score and tip."""

    symbol: str
    score: int
    tip: str


class EvaluateOut(BaseModel):
    """The deterministic mock phoneme evaluation."""

    overall: int
    phonemes: list[PhonemeOut]
    fluency_score: int
    tip_summary: str
    engine_version: str


def _is_locked(drill: PronunciationDrill, user: User) -> bool:
    return drill.is_premium and user.subscription_tier != SUBSCRIPTION_PREMIUM


def mock_phoneme_evaluation(drill_id: str, duration_seconds: int) -> EvaluateOut:
    """Deterministic mock scoring derived from the drill id.

    All numbers come from `zlib.crc32(drill_id.encode())` so the same
    drill always scores identically — the mobile tests and the backend
    tests assert exact values. `duration_seconds` is accepted for the
    contract but deliberately does not influence the score.
    """
    crc = zlib.crc32(drill_id.encode())
    overall = 55 + (crc % 41)  # 55..95
    fluency = 50 + ((crc >> 4) % 46)  # 50..95
    count = 3 + (crc % 3)  # 3..5 phonemes
    phonemes: list[PhonemeOut] = []
    for index in range(count):
        symbol = PHONEME_SYMBOLS[(crc + index * 7) % len(PHONEME_SYMBOLS)]
        score = 45 + ((crc >> (index * 3 + 2)) % 51)  # 45..95
        tip = PHONEME_TIPS[(crc + index * 5) % len(PHONEME_TIPS)]
        phonemes.append(PhonemeOut(symbol=symbol, score=score, tip=tip))
    summary = SUMMARY_TIPS[crc % len(SUMMARY_TIPS)]
    return EvaluateOut(
        overall=overall,
        phonemes=phonemes,
        fluency_score=fluency,
        tip_summary=summary,
        engine_version=ENGINE_VERSION,
    )


@router.get("/drills", response_model=DrillListResponse)
async def list_drills(
    current_user: User = Depends(get_current_user),
) -> DrillListResponse:
    """Return every Pronunciation Lab drill with lock state (SN-049)."""
    drills = gym_pronunciation_drills()
    return DrillListResponse(
        drills=[
            DrillSummaryOut(
                id=drill.id,
                title=drill.title,
                focus=drill.focus,
                level=drill.level,
                is_premium=drill.is_premium,
                is_locked=_is_locked(drill, current_user),
                theme_color=drill.theme_color,
                icon=drill.icon,
            )
            for drill in drills
        ]
    )


@router.get("/drills/{drill_id}", response_model=DrillOut)
async def get_drill(
    drill_id: str,
    current_user: User = Depends(get_current_user),
) -> DrillOut:
    """Return one full drill, or 404 for an unknown id.

    A premium drill requested by a free-tier caller is a 403 (SN-041
    enforcement) — the mobile client shows the paywall instead.
    """
    for drill in load_pronunciation_drills():
        if drill.id == drill_id:
            if _is_locked(drill, current_user):
                raise HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="This drill requires a premium subscription.",
                )
            return DrillOut(
                id=drill.id,
                title=drill.title,
                focus=drill.focus,
                target_sentence=drill.target_sentence,
                target_words=drill.target_words,
                ipa_hint=drill.ipa_hint,
                tip=drill.tip,
                level=drill.level,
                is_premium=drill.is_premium,
                pack_id=drill.pack_id,
                theme_color=drill.theme_color,
                icon=drill.icon,
            )
    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail="Pronunciation drill not found.",
    )


@router.post("/drills/{drill_id}/evaluate", response_model=EvaluateOut)
async def evaluate_drill(
    drill_id: str,
    payload: EvaluateRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> EvaluateOut:
    """Score one take of a drill with the deterministic mock evaluator."""
    drill = get_pronunciation_drill(drill_id)
    if drill is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Pronunciation drill not found.",
        )
    if _is_locked(drill, current_user):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This drill requires a premium subscription.",
        )
    result = mock_phoneme_evaluation(drill_id, payload.duration_seconds)
    if drill.unit_id:
        await record_speaking_practice(
            db,
            user_id=current_user.id,
            unit_code=drill.unit_id,
            content_id=drill.id,
            activity_type=ACTIVITY_PRONUNCIATION_DRILL,
            score=float(result.overall),
            result_json={
                "overall": result.overall,
                "engine_version": result.engine_version,
                "fluency_score": result.fluency_score,
            },
            sonolo_level=drill.sonolo_level,
            fingerprint={"drill_id": drill.id, "overall": result.overall},
        )
        await db.commit()
    return result
