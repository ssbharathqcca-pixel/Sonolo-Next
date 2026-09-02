"""Session completion orchestration (SN-014).

Coordinates persistence, skill EMA updates, XP, streaks, quests, and
badges in ONE transaction. The API layer commits; this service only
flushes. Idempotency is keyed on (user_id, client_session_id).
"""

import asyncio
import logging
import math
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.time import get_local_date_for_user
from app.learning.evaluator import SessionEvaluator
from app.learning.schemas import (
    EvaluationRequest as EvaluatorRequest,
    ScenarioTargets as EvaluatorTargets,
)
from app.learning.schemas import TranscriptTurn as EvaluatorTurn
from app.models.gamification import DailyQuest, UserBadge
from app.models.scenario import Scenario
from app.models.session import SpeakingSession
from app.models.user import User, UserSkill
from app.schemas.gamification import (
    BadgeOut,
    EvaluationScores,
    QuestOut,
    SessionCompleteRequest,
    SessionCompleteResponse,
    SkillUpdateOut,
)
from app.services.analytics import (
    EVENT_QUEST_XP_AWARDED,
    EVENT_SESSION_COMPLETED,
    EVENT_SESSION_XP_AWARDED,
    record_event,
)
from app.services.gamification_service import (
    GamificationService,
    badge_display_title,
)
from app.services.quest_service import QuestCompletionResult, QuestService

logger = logging.getLogger(__name__)

MIN_ELIGIBLE_DURATION_SECONDS = 15
SESSION_XP_CAP = 100
DIFFICULTY_BONUS_DEFAULT = 5
DIFFICULTY_BONUS_CAP = 25
STREAK_BONUS = 5
STREAK_BONUS_THRESHOLD = 3
STALE_TOLERANCE_SECONDS = 5.0

DIMENSION_COLUMNS: dict[str, str] = {
    "fluency": "fluency_score",
    "pronunciation": "pronunciation_score",
    "grammar": "grammar_score",
    "vocabulary": "vocabulary_score",
    "coherence": "coherence_score",
    "task_completion": "task_completion_score",
}


class SessionConflictError(Exception):
    """A different session already exists for this client_session_id."""


class ScenarioNotFoundError(Exception):
    """The referenced scenario does not exist."""


def _is_duplicate_session_error(exc: IntegrityError) -> bool:
    """True when the unique constraint on (user_id, client_session_id) hit."""
    message = str(exc.orig) if exc.orig is not None else str(exc)
    return (
        "uq_sessions_user_client_session" in message
        or "sessions.user_id, sessions.client_session_id" in message
    )


def is_session_xp_eligible(payload: SessionCompleteRequest) -> bool:
    """Structural XP eligibility per SN-014 rules."""
    has_user_turn = any(turn.role == "user" for turn in payload.transcript)
    long_enough = payload.duration_seconds >= MIN_ELIGIBLE_DURATION_SECONDS
    return has_user_turn and long_enough


def calculate_session_xp(
    *,
    xp_eligible: bool,
    duration_seconds: int,
    overall_score: float,
    scenario_difficulty: int | None,
    updated_streak: int,
) -> int:
    """Deterministic session XP (capped at 100)."""
    if not xp_eligible:
        return 0
    base_xp = 20
    duration_xp = min(math.floor(duration_seconds / 60), 10)
    proficiency_xp = math.floor(overall_score / 10)
    if scenario_difficulty is None:
        difficulty_bonus = DIFFICULTY_BONUS_DEFAULT
    else:
        difficulty_bonus = min(
            max(scenario_difficulty, 1) * 5, DIFFICULTY_BONUS_CAP
        )
    streak_bonus = STREAK_BONUS if updated_streak >= STREAK_BONUS_THRESHOLD else 0
    return min(
        base_xp + duration_xp + proficiency_xp + difficulty_bonus + streak_bonus,
        SESSION_XP_CAP,
    )


class SessionService:
    """Complete-session unit of work on an external transaction."""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def complete_session(
        self, user: User, payload: SessionCompleteRequest, now: datetime
    ) -> SessionCompleteResponse:
        """Run the full completion flow (idempotent per client_session_id)."""
        user_id = user.id  # Captured before any rollback can expire it.
        gamification = GamificationService(self._db)
        locked_user = await gamification.lock_user_for_update(user_id)

        existing = await self.get_session_by_client_session_id(
            user_id, payload.client_session_id
        )
        if existing is not None:
            self._assert_replay_consistent(existing, payload)
            return await self._build_replay_response(locked_user, existing, now)

        scenario = await self._db.get(Scenario, payload.scenario_id)
        if scenario is None:
            raise ScenarioNotFoundError(str(payload.scenario_id))

        try:
            return await self._create_session_flow(
                locked_user, payload, scenario, now
            )
        except IntegrityError as exc:
            if not _is_duplicate_session_error(exc):
                raise
            # A concurrent duplicate won the insert: discard this attempt
            # entirely and replay the stored outcome — never double-award.
            await self._db.rollback()
            stored = await self._wait_for_stored_session(
                user_id, payload.client_session_id
            )
            if stored is None:
                raise
            refreshed = await gamification.lock_user_for_update(user_id)
            return await self._build_replay_response(refreshed, stored, now)

    async def _wait_for_stored_session(
        self, user_id: UUID, client_session_id: UUID
    ) -> SpeakingSession | None:
        """Wait briefly for a concurrent winner's row to become visible."""
        for _ in range(5):
            stored = await self.get_session_by_client_session_id(
                user_id, client_session_id
            )
            if stored is not None:
                return stored
            await asyncio.sleep(0.02)
        return None

    async def _create_session_flow(
        self,
        locked_user: User,
        payload: SessionCompleteRequest,
        scenario: Scenario,
        now: datetime,
    ) -> SessionCompleteResponse:
        """Insert the session and award skills, XP, streaks, quests, badges."""
        gamification = GamificationService(self._db)
        local_date = get_local_date_for_user(now, locked_user.timezone)
        xp_eligible = is_session_xp_eligible(payload)
        scores = payload.evaluation.scores

        if xp_eligible:
            # Server-side cross-check of the client-reported evaluation:
            # MVP trusts the client, but significant divergence is logged.
            try:
                own = await SessionEvaluator().evaluate(
                    EvaluatorRequest(
                        session_id=payload.client_session_id,
                        transcript=[
                            EvaluatorTurn(
                                role=(
                                    "user"
                                    if turn.role == "user"
                                    else "tutor"
                                ),
                                text=turn.text,
                            )
                            for turn in payload.transcript
                        ],
                        duration_seconds=float(payload.duration_seconds),
                        scenario_targets=EvaluatorTargets(
                            vocabulary=list(scenario.vocabulary_targets or []),
                            grammar=list(scenario.grammar_targets or []),
                        ),
                    )
                )
                if (
                    abs(own.speaking_power_score - payload.evaluation.overall_score)
                    > 10.0
                ):
                    logger.warning(
                        "Evaluation divergence: client=%s server=%s session=%s",
                        payload.evaluation.overall_score,
                        own.speaking_power_score,
                        payload.client_session_id,
                    )
            except Exception:  # noqa: BLE001 - cross-check must not block
                logger.debug("Evaluator cross-check skipped.", exc_info=True)

            await gamification.update_streak(locked_user, local_date, now)

        session_xp = calculate_session_xp(
            xp_eligible=xp_eligible,
            duration_seconds=payload.duration_seconds,
            overall_score=payload.evaluation.overall_score,
            scenario_difficulty=scenario.difficulty,
            updated_streak=locked_user.streak_count,
        )

        quest_results: list[QuestCompletionResult] = []
        quests: list[DailyQuest] = []
        quest_xp = 0
        skill_updates: list[SkillUpdateOut] = []

        if xp_eligible:
            quest_service = QuestService(self._db)
            quests = await quest_service.ensure_daily_quests(
                locked_user.id, local_date
            )
            quest_results = await quest_service.progress_session_quests(
                locked_user.id, local_date, now
            )
            quest_xp = sum(
                result.reward_xp_awarded for result in quest_results
            )
            for result in quest_results:
                if result.reward_xp_awarded > 0:
                    await record_event(
                        self._db,
                        locked_user.id,
                        EVENT_QUEST_XP_AWARDED,
                        {
                            "quest_code": result.code,
                            "xp": result.reward_xp_awarded,
                            "local_date": local_date.isoformat(),
                        },
                    )

            skill_updates = await self.update_skills(locked_user, scores, now)

        await gamification.apply_session_xp(
            locked_user, session_xp + quest_xp, local_date, now
        )

        session_row = self.persist_session(
            user=locked_user,
            payload=payload,
            scenario=scenario,
            xp_eligible=xp_eligible,
            session_xp=session_xp,
            quest_xp=quest_xp,
            now=now,
        )

        newly_awarded: list[UserBadge] = []
        if xp_eligible:
            await self._db.flush()  # Make the new row visible to the count.
            eligible_count = await gamification.count_eligible_sessions(
                locked_user.id
            )
            quests = await QuestService(self._db).ensure_daily_quests(
                locked_user.id, local_date
            )
            newly_awarded = await gamification.award_badges(
                locked_user,
                local_date,
                now,
                eligible_session_count=eligible_count,
                quests_for_date=quests,
            )

        locked_user.total_speaking_seconds += payload.duration_seconds

        await record_event(
            self._db,
            locked_user.id,
            EVENT_SESSION_COMPLETED,
            {
                "session_id": str(session_row.id),
                "scenario_id": str(payload.scenario_id),
                "client_session_id": str(payload.client_session_id),
                "xp_eligible": xp_eligible,
                "duration_seconds": payload.duration_seconds,
                "overall_score": payload.evaluation.overall_score,
                "local_date": local_date.isoformat(),
            },
        )
        if session_xp + quest_xp > 0:
            await record_event(
                self._db,
                locked_user.id,
                EVENT_SESSION_XP_AWARDED,
                {
                    "session_id": str(session_row.id),
                    "session_xp": session_xp,
                    "quest_xp": quest_xp,
                    "xp": session_xp + quest_xp,
                },
            )

        await self._db.flush()
        if xp_eligible:
            await self._record_unit_speaking_evidence(
                locked_user, payload, scenario
            )

        xp_award = gamification.build_xp_award(
            locked_user, session_xp, quest_xp
        )

        return SessionCompleteResponse(
            session_id=session_row.id,
            idempotent_replayed=False,
            xp_eligible=xp_eligible,
            xp=xp_award,
            skills=skill_updates,
            streak_current=locked_user.streak_count,
            streak_longest=locked_user.longest_streak,
            quests=[self._quest_out(quest) for quest in quests],
            newly_awarded_badges=[
                BadgeOut(
                    code=badge.badge_id,
                    title=badge_display_title(badge),
                    description=badge.description,
                    awarded_at=badge.earned_at,
                )
                for badge in newly_awarded
            ],
            completed_at=now,
        )

    async def get_session_by_client_session_id(
        self, user_id: UUID, client_session_id: UUID
    ) -> SpeakingSession | None:
        """Fetch a session by its idempotency key."""
        result = await self._db.execute(
            select(SpeakingSession).where(
                SpeakingSession.user_id == user_id,
                SpeakingSession.client_session_id == client_session_id,
            )
        )
        return result.scalar_one_or_none()

    def persist_session(
        self,
        *,
        user: User,
        payload: SessionCompleteRequest,
        scenario: Scenario,
        xp_eligible: bool,
        session_xp: int,
        quest_xp: int,
        now: datetime,
    ) -> SpeakingSession:
        """Insert the durable session row."""
        scores = payload.evaluation.scores
        session = SpeakingSession(
            user_id=user.id,
            scenario_id=scenario.id,
            client_session_id=payload.client_session_id,
            session_type="voice",
            started_at=payload.started_at,
            ended_at=payload.ended_at,
            duration_seconds=payload.duration_seconds,
            turns_count=len(payload.transcript),
            fluency_score=scores.fluency,
            pronunciation_score=scores.pronunciation,
            grammar_score=scores.grammar,
            vocabulary_score=scores.vocabulary,
            coherence_score=scores.coherence,
            task_completion_score=scores.task_completion,
            composite_score=payload.evaluation.overall_score,
            overall_score=payload.evaluation.overall_score,
            xp_earned=session_xp,
            errors_detected=[],
            transcript=[
                turn.model_dump(mode="json") for turn in payload.transcript
            ],
            evaluation_json=payload.evaluation.model_dump(mode="json"),
            audio_stored=False,
            is_xp_eligible=xp_eligible,
            session_xp=session_xp,
            quest_xp=quest_xp,
            total_xp=session_xp + quest_xp,
        )
        self._db.add(session)
        return session

    async def update_skills(
        self, user: User, scores: EvaluationScores, now: datetime
    ) -> list[SkillUpdateOut]:
        """Apply the 70/30 EMA to each of the six dimensions."""
        session_values: dict[str, float] = {
            "fluency": scores.fluency,
            "pronunciation": scores.pronunciation,
            "grammar": scores.grammar,
            "vocabulary": scores.vocabulary,
            "coherence": scores.coherence,
            "task_completion": scores.task_completion,
        }
        # Query directly (async-safe) rather than touching the lazy
        # relationship attribute, which would do implicit IO under asyncio.
        skill_row = (
            await self._db.execute(
                select(UserSkill).where(UserSkill.user_id == user.id)
            )
        ).scalar_one_or_none()
        if skill_row is None:
            skill_row = UserSkill(user_id=user.id)
            self._db.add(skill_row)
            is_first_score = True
        else:
            is_first_score = False
        updates: list[SkillUpdateOut] = []
        for dimension, session_score in session_values.items():
            column = DIMENSION_COLUMNS[dimension]
            previous = getattr(skill_row, column)
            if is_first_score:
                new_score = round(session_score, 2)
            else:
                new_score = round(0.7 * previous + 0.3 * session_score, 2)
            new_score = max(0.0, min(100.0, new_score))
            setattr(skill_row, column, new_score)
            updates.append(
                SkillUpdateOut(
                    dimension=dimension,
                    previous_score=previous,
                    session_score=session_score,
                    new_score=new_score,
                )
            )
        skill_row.updated_at = now
        return updates

    # ------------------------------------------------------------------

    def _assert_replay_consistent(
        self, existing: SpeakingSession, payload: SessionCompleteRequest
    ) -> None:
        def same_instant(stored: datetime, incoming: datetime) -> bool:
            # SQLite returns naive datetimes; treat them as UTC.
            stored_utc = (
                stored if stored.tzinfo is not None else stored.replace(tzinfo=UTC)
            )
            return stored_utc == incoming

        same_scenario = existing.scenario_id == payload.scenario_id
        if not (
            same_scenario
            and same_instant(existing.started_at, payload.started_at)
            and same_instant(existing.ended_at, payload.ended_at)
        ):
            raise SessionConflictError(str(payload.client_session_id))

    async def _build_replay_response(
        self, user: User, existing: SpeakingSession, now: datetime
    ) -> SessionCompleteResponse:
        """Return the stored outcome without mutating anything."""
        local_date = get_local_date_for_user(now, user.timezone)
        quests = await QuestService(self._db).ensure_daily_quests(
            user.id, local_date
        )
        quest_xp_total = existing.quest_xp
        xp_award = GamificationService(self._db).build_xp_award(
            user, existing.session_xp, quest_xp_total
        )
        return SessionCompleteResponse(
            session_id=existing.id,
            idempotent_replayed=True,
            xp_eligible=existing.is_xp_eligible,
            xp=xp_award,
            skills=[],
            streak_current=user.streak_count,
            streak_longest=user.longest_streak,
            quests=[self._quest_out(quest) for quest in quests],
            newly_awarded_badges=[],
            completed_at=existing.created_at,
        )

    async def _record_unit_speaking_evidence(
        self,
        user: User,
        payload: SessionCompleteRequest,
        scenario: Scenario,
    ) -> None:
        """C13: unit-bound sessions write speaking evidence + completion."""
        from app.services.content_service import get_scenario_seed_by_id
        from app.services.evidence_service import (
            ACTIVITY_SPEAKING_SESSION,
            record_speaking_practice,
        )

        seed = get_scenario_seed_by_id(scenario.id)
        if seed is None or not seed.unit_id:
            return
        await record_speaking_practice(
            self._db,
            user_id=user.id,
            unit_code=seed.unit_id,
            content_id=seed.content_id,
            activity_type=ACTIVITY_SPEAKING_SESSION,
            score=float(payload.evaluation.overall_score),
            result_json={
                "overall_score": payload.evaluation.overall_score,
                "scenario_id": str(scenario.id),
                "client_session_id": str(payload.client_session_id),
                "vocabulary_targets": list(scenario.vocabulary_targets or []),
            },
            sonolo_level=scenario.sonolo_level if scenario.sonolo_level is not None else seed.sonolo_level,
            fingerprint=str(payload.client_session_id),
        )

    @staticmethod
    def _quest_out(quest: DailyQuest) -> QuestOut:
        return QuestOut(
            code=quest.code,
            title=quest.title,
            description=quest.description,
            target_count=quest.target_count,
            progress_count=quest.progress_count,
            reward_xp=quest.xp_reward,
            completed=quest.completed,
            completed_at=quest.completed_at,
        )
