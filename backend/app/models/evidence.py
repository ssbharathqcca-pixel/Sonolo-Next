"""Normalized mastery evidence (D-018). Not learning_sessions.

Practice last-20 scores live in skill_exercise_attempts.
Unit-test per-skill scores live in unit_test_skill_evidence.
EMA lives on user_skill_levels.ema_score.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.base import Base, JSONB

ATTEMPT_STARTED = "started"
ATTEMPT_SUBMITTED = "submitted"
ATTEMPT_REJECTED_LATE = "rejected_late"


class SkillExerciseAttempt(Base):
    """One practice activity attempt (reading exercise, hunt, …)."""

    __tablename__ = "skill_exercise_attempts"
    __table_args__ = (
        Index("ix_skill_exercise_attempts_user_id", "user_id"),
        Index(
            "ix_skill_exercise_attempts_user_skill_level",
            "user_id",
            "skill",
            "sonolo_level",
        ),
        Index(
            "uq_skill_exercise_attempts_active",
            "user_id",
            "content_id",
            unique=True,
            sqlite_where=text("status = 'started'"),
            postgresql_where=text("status = 'started'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id"), nullable=True
    )
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    activity_type: Mapped[str] = mapped_column(String(40), nullable=False)
    content_id: Mapped[str] = mapped_column(String(128), nullable=False)
    sonolo_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, server_default=text("'started'")
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UnitTestSkillEvidence(Base):
    """One skill-section score from one unit-test sitting."""

    __tablename__ = "unit_test_skill_evidence"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "unit_id",
            "skill",
            "sitting_id",
            name="uq_unit_test_skill_evidence_sitting",
        ),
        Index("ix_unit_test_skill_evidence_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id"), nullable=False
    )
    sitting_id: Mapped[uuid.UUID] = mapped_column(nullable=False)
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    score: Mapped[float] = mapped_column(Float, nullable=False)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
