"""User and per-user skill models.

Column value domains (validated at the application layer for now):
- users.learning_goal: 'pr_readiness' | 'casual' | 'workplace' | 'travel' | 'love'
- users.current_level: 'seed' | 'sprout' | 'branch' | 'bloom' | 'canopy' | 'summit'
- users.subscription_tier: 'free' | 'premium'
- users.preferred_language: 'en' | 'fr'
"""

import uuid
from datetime import date, datetime
from enum import Enum
from typing import TYPE_CHECKING

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.db.base import Base

if TYPE_CHECKING:
    from app.models.gamification import DailyQuest, UserBadge
    from app.models.session import SpeakingSession
    from app.models.vocabulary import VocabularyCard


#: Allowed values for `users.subscription_tier` (SN-026), validated at the
#: application layer until payments introduce expiry/downgrade flows.
SUBSCRIPTION_FREE = "free"
SUBSCRIPTION_PREMIUM = "premium"
SUBSCRIPTION_TIERS = (SUBSCRIPTION_FREE, SUBSCRIPTION_PREMIUM)


class PreferredLanguage(str, Enum):
    """Content language a learner wants to practice (SN-020)."""

    ENGLISH = "en"
    FRENCH = "fr"


class User(Base):
    """A Sonolo learner."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    email: Mapped[str | None] = mapped_column(String(255), unique=True)
    #: bcrypt hash; empty string means the user has no password yet.
    hashed_password: Mapped[str] = mapped_column(
        String(255), default="", server_default=text("''")
    )
    name: Mapped[str] = mapped_column(String(255))
    native_language: Mapped[str] = mapped_column(String(10))
    target_language: Mapped[str] = mapped_column(String(10))
    learning_goal: Mapped[str] = mapped_column(String(50))
    current_level: Mapped[str] = mapped_column(String(20))
    subscription_tier: Mapped[str] = mapped_column(
        String(20), default="free", server_default=text("'free'")
    )
    subscription_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    #: Content language used to filter the scenario catalog (SN-020);
    #: regional codes such as "en-CA" still match the "en" preference.
    preferred_language: Mapped[str] = mapped_column(
        String(10), default=PreferredLanguage.ENGLISH.value,
        server_default=text("'en'"),
    )
    #: Four-skill numeric level (C0 / Part II §2.3). Parallel to current_level.
    sonolo_level: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    streak_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    streak_last_date: Mapped[date | None] = mapped_column(Date)
    total_xp: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    xp_today: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    xp_today_date: Mapped[date | None] = mapped_column(Date)
    longest_streak: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    last_activity_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    timezone: Mapped[str] = mapped_column(
        String(64),
        default="America/Toronto",
        server_default=text("'America/Toronto'"),
    )
    total_speaking_seconds: Mapped[int] = mapped_column(
        Integer, default=0, server_default=text("0")
    )
    onboarding_completed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    skills: Mapped["UserSkill | None"] = relationship(
        back_populates="user", lazy="selectin"
    )
    sessions: Mapped[list["SpeakingSession"]] = relationship(
        back_populates="user", lazy="selectin"
    )
    vocabulary_cards: Mapped[list["VocabularyCard"]] = relationship(
        back_populates="user", lazy="selectin"
    )
    badges: Mapped[list["UserBadge"]] = relationship(
        back_populates="user", lazy="selectin"
    )
    daily_quests: Mapped[list["DailyQuest"]] = relationship(
        back_populates="user", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"User(id={self.id!r}, name={self.name!r}, "
            f"current_level={self.current_level!r}, "
            f"subscription_tier={self.subscription_tier!r})"
        )


class UserSkill(Base):
    """Latest speaking-readiness skill scores for a user (one row per user)."""

    __tablename__ = "user_skills"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), unique=True)
    fluency_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    pronunciation_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    grammar_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    vocabulary_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    coherence_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    task_completion_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    composite_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    canada_ready_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    confidence_score: Mapped[float] = mapped_column(
        Float, default=0.0, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="skills", lazy="selectin")

    def __repr__(self) -> str:
        return (
            f"UserSkill(id={self.id!r}, user_id={self.user_id!r}, "
            f"composite_score={self.composite_score!r}, "
            f"canada_ready_score={self.canada_ready_score!r})"
        )
