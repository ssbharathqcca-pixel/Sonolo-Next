"""C0 four-skill curriculum schema (Part XVI §16.2).

NEW: units, user_skill_levels, user_unit_progress, learning_sessions,
reading_exercises, writing_exercises, user_certificates.

MODIFY: users.sonolo_level, scenarios.unit_id, scenarios.sonolo_level.

Backfill users.sonolo_level and scenarios.sonolo_level from botanical
current_level / level using Part II §2.3:

    seed→1, sprout→2, branch→4, bloom→6, canopy→7, summit→9

Does not drop or alter legacy columns. Does not replace user_skills
or sessions.

Revision ID: 0004_four_skill_curriculum
Revises: 0003_scenario_pack_id
Create Date: 2026-09-02

Targets PostgreSQL 16; also renders on SQLite for Alembic --sql tests.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004_four_skill_curriculum"
down_revision: Union[str, None] = "0003_scenario_pack_id"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())

#: Part II §2.3 — applied to users.current_level and scenarios.level.
_SONOLO_LEVEL_BACKFILL = """
CASE {column}
    WHEN 'seed' THEN 1
    WHEN 'sprout' THEN 2
    WHEN 'branch' THEN 4
    WHEN 'bloom' THEN 6
    WHEN 'canopy' THEN 7
    WHEN 'summit' THEN 9
    ELSE 1
END
"""


def upgrade() -> None:
    op.create_table(
        "units",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("unit_code", sa.String(length=10), nullable=False),
        sa.Column("band", sa.String(length=20), nullable=False),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("story_chapter", sa.String(length=255), nullable=False, server_default=sa.text("''")),
        sa.Column("theme", sa.String(length=100), nullable=False, server_default=sa.text("''")),
        sa.Column("icon", sa.String(length=64), nullable=False, server_default=sa.text("''")),
        sa.Column("level_target", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("cultural_context", sa.Text(), nullable=False, server_default=sa.text("''")),
        sa.Column("vocabulary_targets", JSONB, nullable=False),
        sa.Column("grammar_targets", JSONB, nullable=False),
        sa.Column("prerequisites", JSONB, nullable=False),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.PrimaryKeyConstraint("id", name="pk_units"),
        sa.UniqueConstraint("unit_code", "language", name="uq_units_unit_code_language"),
    )

    op.create_table(
        "user_skill_levels",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("skill", sa.String(length=20), nullable=False),
        sa.Column("sonolo_level", sa.Integer(), nullable=False, server_default=sa.text("1")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_skill_levels_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_user_skill_levels"),
        sa.UniqueConstraint("user_id", "skill", name="uq_user_skill_levels_user_skill"),
    )

    op.create_table(
        "user_unit_progress",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("speaking_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("listening_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("reading_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("writing_complete", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("unit_test_score", sa.Float(), nullable=True),
        sa.Column("unit_test_passed", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_unit_progress_user_id_users"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_user_unit_progress_unit_id_units"),
        sa.PrimaryKeyConstraint("id", name="pk_user_unit_progress"),
        sa.UniqueConstraint("user_id", "unit_id", name="uq_user_unit_progress_user_unit"),
    )

    op.create_table(
        "learning_sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("skill", sa.String(length=20), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column("result_json", JSONB, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_learning_sessions_user_id_users"),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_learning_sessions_unit_id_units"),
        sa.PrimaryKeyConstraint("id", name="pk_learning_sessions"),
    )
    op.create_index(
        op.f("ix_learning_sessions_user_id"),
        "learning_sessions",
        ["user_id"],
        unique=False,
    )

    op.create_table(
        "reading_exercises",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.String(length=128), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("text_content", sa.Text(), nullable=False),
        sa.Column("text_source", sa.String(length=32), nullable=False, server_default=sa.text("'original'")),
        sa.Column("word_count", sa.Integer(), nullable=True),
        sa.Column("sonolo_level", sa.Integer(), nullable=True),
        sa.Column("text_type", sa.String(length=32), nullable=False, server_default=sa.text("''")),
        sa.Column("questions", JSONB, nullable=False),
        sa.Column("vocabulary_targets", JSONB, nullable=False),
        sa.Column("grammar_targets", JSONB, nullable=False),
        sa.Column("cultural_note", sa.Text(), nullable=True),
        sa.Column("reading_time_minutes", sa.Integer(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_reading_exercises_unit_id_units"),
        sa.PrimaryKeyConstraint("id", name="pk_reading_exercises"),
        sa.UniqueConstraint("content_id", name="uq_reading_exercises_content_id"),
    )

    op.create_table(
        "writing_exercises",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("content_id", sa.String(length=128), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("title", sa.String(length=255), nullable=False),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("exercise_type", sa.String(length=30), nullable=False),
        sa.Column("sonolo_level", sa.Integer(), nullable=True),
        sa.Column("prompt", sa.Text(), nullable=False),
        sa.Column("scaffold", sa.Text(), nullable=True),
        sa.Column("model_answer", sa.Text(), nullable=True),
        sa.Column("word_count_target", JSONB, nullable=False),
        sa.Column("rubric", JSONB, nullable=False),
        sa.Column("vocabulary_targets", JSONB, nullable=False),
        sa.Column("grammar_targets", JSONB, nullable=False),
        sa.Column("word_bank", JSONB, nullable=True),
        sa.Column("correct_sentence", sa.Text(), nullable=True),
        sa.Column("error_text", sa.Text(), nullable=True),
        sa.Column("error_count", sa.Integer(), nullable=True),
        sa.Column("corrected_text", sa.Text(), nullable=True),
        sa.Column("is_published", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["unit_id"], ["units.id"], name="fk_writing_exercises_unit_id_units"),
        sa.PrimaryKeyConstraint("id", name="pk_writing_exercises"),
        sa.UniqueConstraint("content_id", name="uq_writing_exercises_content_id"),
    )

    op.create_table(
        "user_certificates",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("certificate_type", sa.String(length=30), nullable=False),
        sa.Column("band", sa.String(length=20), nullable=False),
        sa.Column("sonolo_level", sa.Integer(), nullable=True),
        sa.Column("language", sa.String(length=10), nullable=False),
        sa.Column("skills_snapshot", JSONB, nullable=False),
        sa.Column("pdf_url", sa.Text(), nullable=True),
        sa.Column("earned_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name="fk_user_certificates_user_id_users"),
        sa.PrimaryKeyConstraint("id", name="pk_user_certificates"),
        sa.UniqueConstraint(
            "user_id",
            "certificate_type",
            "band",
            "language",
            name="uq_user_certificates_user_type_band_language",
        ),
    )

    op.add_column(
        "users",
        sa.Column(
            "sonolo_level",
            sa.Integer(),
            nullable=False,
            server_default=sa.text("1"),
        ),
    )
    op.execute(
        sa.text(
            "UPDATE users SET sonolo_level = "
            + _SONOLO_LEVEL_BACKFILL.format(column="current_level")
        )
    )

    op.add_column(
        "scenarios",
        sa.Column("unit_id", sa.Uuid(), nullable=True),
    )
    op.create_foreign_key(
        "fk_scenarios_unit_id_units",
        "scenarios",
        "units",
        ["unit_id"],
        ["id"],
    )
    op.add_column(
        "scenarios",
        sa.Column("sonolo_level", sa.Integer(), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE scenarios SET sonolo_level = "
            + _SONOLO_LEVEL_BACKFILL.format(column="level")
        )
    )


def downgrade() -> None:
    op.drop_column("scenarios", "sonolo_level")
    op.drop_constraint("fk_scenarios_unit_id_units", "scenarios", type_="foreignkey")
    op.drop_column("scenarios", "unit_id")
    op.drop_column("users", "sonolo_level")
    op.drop_table("user_certificates")
    op.drop_table("writing_exercises")
    op.drop_table("reading_exercises")
    op.drop_index(op.f("ix_learning_sessions_user_id"), table_name="learning_sessions")
    op.drop_table("learning_sessions")
    op.drop_table("user_unit_progress")
    op.drop_table("user_skill_levels")
    op.drop_table("units")
