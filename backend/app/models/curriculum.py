"""C0 four-skill curriculum tables (Part XVI §16.2) plus C1 loaders.

ORM was deferred during C0 (migrations only). C1/C3 need these models
to persist units, exercises, and per-user skill/progress rows.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column
from uuid6 import uuid7

from app.db.base import Base, JSONB


class Unit(Base):
    """One curriculum unit (F1–A6) in one language."""

    __tablename__ = "units"
    __table_args__ = (
        UniqueConstraint("unit_code", "language", name="uq_units_unit_code_language"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    unit_code: Mapped[str] = mapped_column(String(10), nullable=False)
    band: Mapped[str] = mapped_column(String(20), nullable=False)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    story_chapter: Mapped[str] = mapped_column(
        String(255), nullable=False, server_default=text("''")
    )
    theme: Mapped[str] = mapped_column(
        String(100), nullable=False, server_default=text("''")
    )
    icon: Mapped[str] = mapped_column(
        String(64), nullable=False, server_default=text("''")
    )
    level_target: Mapped[int] = mapped_column(Integer, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    cultural_context: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("''")
    )
    vocabulary_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    grammar_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    prerequisites: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserSkillLevel(Base):
    """Per-user per-macro-skill Sonolo level and persisted EMA (D-018)."""

    __tablename__ = "user_skill_levels"
    __table_args__ = (
        UniqueConstraint("user_id", "skill", name="uq_user_skill_levels_user_skill"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    sonolo_level: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    #: Running EMA; NULL until the first valid session score (D-022).
    ema_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class UserUnitProgress(Base):
    """Per-user completion flags for one unit."""

    __tablename__ = "user_unit_progress"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "unit_id", name="uq_user_unit_progress_user_unit"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False
    )
    unit_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("units.id"), nullable=False
    )
    speaking_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    listening_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    reading_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    writing_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    unit_test_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    unit_test_passed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class LearningSession(Base):
    """Four-skill session envelope (P1-01). Not the last-20 exercise ledger."""

    __tablename__ = "learning_sessions"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id"), nullable=False, index=True
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id"), nullable=True
    )
    skill: Mapped[str] = mapped_column(String(20), nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    ended_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    result_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class ReadingExercise(Base):
    """Published reading passage plus comprehension questions."""

    __tablename__ = "reading_exercises"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    content_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    text_content: Mapped[str] = mapped_column(Text, nullable=False)
    text_source: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("'original'")
    )
    word_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    sonolo_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    text_type: Mapped[str] = mapped_column(
        String(32), nullable=False, server_default=text("''")
    )
    questions: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    vocabulary_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    grammar_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    cultural_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    reading_time_minutes: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class WritingExercise(Base):
    """Published writing exercise (C1 catalog; C4 scores it)."""

    __tablename__ = "writing_exercises"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    content_id: Mapped[str] = mapped_column(
        String(128), nullable=False, unique=True
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(10), nullable=False)
    exercise_type: Mapped[str] = mapped_column(String(30), nullable=False)
    sonolo_level: Mapped[int | None] = mapped_column(Integer, nullable=True)
    prompt: Mapped[str] = mapped_column(Text, nullable=False)
    scaffold: Mapped[str | None] = mapped_column(Text, nullable=True)
    model_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    word_count_target: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False
    )
    rubric: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    vocabulary_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    grammar_targets: Mapped[list[Any]] = mapped_column(JSONB, nullable=False)
    word_bank: Mapped[list[Any] | None] = mapped_column(JSONB, nullable=True)
    correct_sentence: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    error_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    corrected_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_published: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
