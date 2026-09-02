"""Scenario content model.

Column value domains:
- category: 'housing' | 'healthcare' | 'banking' | 'workplace' | 'education' |
  'shopping' | 'government' | 'social' | 'casual'
- mode: 'immigration' | 'casual' | 'both'
- level: 'seed' | 'sprout' | 'branch' | 'bloom' | 'canopy' | 'summit'
"""

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    false,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from uuid6 import uuid7

from app.db.base import Base, JSONB

if TYPE_CHECKING:
    from app.models.gamification import DailyQuest
    from app.models.session import SpeakingSession


class Scenario(Base):
    """A published or draft conversation scenario tutored by the AI."""

    __tablename__ = "scenarios"

    __table_args__ = (
        Index(
            "idx_scenarios_level_category",
            "level",
            "category",
            postgresql_where=text("is_published = TRUE"),
            sqlite_where=text("is_published = TRUE"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid7)
    title: Mapped[str] = mapped_column(String(255))
    description: Mapped[str] = mapped_column(Text, default="")
    category: Mapped[str] = mapped_column(String(50))
    mode: Mapped[str] = mapped_column(String(20))
    level: Mapped[str] = mapped_column(String(20))
    difficulty: Mapped[int | None] = mapped_column(
        Integer, default=None, server_default=text("NULL")
    )
    target_language: Mapped[str] = mapped_column(
        String(10), default="en-CA", server_default=text("'en-CA'")
    )
    #: Manifest pack this row came from (SN-035); nullable so rows seeded
    #: before pack tracking remain valid until their next upsert.
    pack_id: Mapped[str | None] = mapped_column(
        String(64), default=None, server_default=text("NULL"), index=True
    )
    unit_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("units.id"), default=None, server_default=text("NULL")
    )
    sonolo_level: Mapped[int | None] = mapped_column(
        Integer, default=None, server_default=text("NULL")
    )
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    opening_line: Mapped[str] = mapped_column(Text, default="")
    expected_turns: Mapped[int] = mapped_column(Integer)
    success_criteria: Mapped[dict[str, Any]] = mapped_column(
        JSONB, default=dict
    )
    vocabulary_targets: Mapped[list[str]] = mapped_column(JSONB, default=list)
    grammar_targets: Mapped[list[str]] = mapped_column(JSONB, default=list)
    cultural_notes: Mapped[str] = mapped_column(Text, default="")
    is_premium: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=false()
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    sessions: Mapped[list["SpeakingSession"]] = relationship(
        back_populates="scenario", lazy="selectin"
    )
    daily_quests: Mapped[list["DailyQuest"]] = relationship(
        back_populates="scenario", lazy="selectin"
    )

    def __repr__(self) -> str:
        return (
            f"Scenario(id={self.id!r}, title={self.title!r}, "
            f"category={self.category!r}, level={self.level!r}, "
            f"is_published={self.is_published!r})"
        )
