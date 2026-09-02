"""SQLAlchemy models for Sonolo.

Importing this package registers every model's table on the shared
Base.metadata (required for Alembic autogenerate and create_all).
"""

from app.db.base import Base
from app.models.analytics import AnalyticsEvent
from app.models.curriculum import (
    LearningSession,
    ReadingExercise,
    Unit,
    UserSkillLevel,
    UserUnitProgress,
    WritingExercise,
)
from app.models.evidence import SkillExerciseAttempt, UnitTestSkillEvidence
from app.models.gamification import DailyQuest, UserBadge
from app.models.scenario import Scenario
from app.models.session import SpeakingSession
from app.models.user import User, UserSkill
from app.models.vocabulary import VocabularyCard

__all__ = [
    "AnalyticsEvent",
    "Base",
    "DailyQuest",
    "LearningSession",
    "ReadingExercise",
    "Scenario",
    "SkillExerciseAttempt",
    "SpeakingSession",
    "Unit",
    "UnitTestSkillEvidence",
    "User",
    "UserBadge",
    "UserSkill",
    "UserSkillLevel",
    "UserUnitProgress",
    "VocabularyCard",
    "WritingExercise",
]
