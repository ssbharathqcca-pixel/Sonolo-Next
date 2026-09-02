"""Skill evidence layer for C2 orchestration (D-018).

NEW: skill_exercise_attempts, unit_test_skill_evidence.
MODIFY: user_skill_levels.ema_score.

Does not alter C2 formulas. Does not reuse learning_sessions as an
exercise ledger.

Revision ID: 0005_skill_evidence
Revises: 0004_four_skill_curriculum
Create Date: 2026-09-02
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005_skill_evidence"
down_revision: Union[str, None] = "0004_four_skill_curriculum"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column(
        "user_skill_levels",
        sa.Column("ema_score", sa.Float(), nullable=True),
    )

    op.create_table(
        "skill_exercise_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=True),
        sa.Column("skill", sa.String(length=20), nullable=False),
        sa.Column("activity_type", sa.String(length=40), nullable=False),
        sa.Column("content_id", sa.String(length=128), nullable=False),
        sa.Column("sonolo_level", sa.Integer(), nullable=True),
        sa.Column("score", sa.Float(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'started'"),
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("result_json", JSONB, nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"], ["users.id"], name="fk_skill_exercise_attempts_user_id_users"
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"], ["units.id"], name="fk_skill_exercise_attempts_unit_id_units"
        ),
        sa.PrimaryKeyConstraint("id", name="pk_skill_exercise_attempts"),
    )
    op.create_index(
        "ix_skill_exercise_attempts_user_id",
        "skill_exercise_attempts",
        ["user_id"],
        unique=False,
    )
    op.create_index(
        "ix_skill_exercise_attempts_user_skill_level",
        "skill_exercise_attempts",
        ["user_id", "skill", "sonolo_level"],
        unique=False,
    )
    op.create_index(
        "uq_skill_exercise_attempts_active",
        "skill_exercise_attempts",
        ["user_id", "content_id"],
        unique=True,
        sqlite_where=sa.text("status = 'started'"),
        postgresql_where=sa.text("status = 'started'"),
    )

    op.create_table(
        "unit_test_skill_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("unit_id", sa.Uuid(), nullable=False),
        sa.Column("sitting_id", sa.Uuid(), nullable=False),
        sa.Column("skill", sa.String(length=20), nullable=False),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("result_json", JSONB, nullable=False),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_unit_test_skill_evidence_user_id_users",
        ),
        sa.ForeignKeyConstraint(
            ["unit_id"],
            ["units.id"],
            name="fk_unit_test_skill_evidence_unit_id_units",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_unit_test_skill_evidence"),
        sa.UniqueConstraint(
            "user_id",
            "unit_id",
            "skill",
            "sitting_id",
            name="uq_unit_test_skill_evidence_sitting",
        ),
    )
    op.create_index(
        "ix_unit_test_skill_evidence_user_id",
        "unit_test_skill_evidence",
        ["user_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_unit_test_skill_evidence_user_id",
        table_name="unit_test_skill_evidence",
    )
    op.drop_table("unit_test_skill_evidence")
    op.drop_index(
        "uq_skill_exercise_attempts_active",
        table_name="skill_exercise_attempts",
    )
    op.drop_index(
        "ix_skill_exercise_attempts_user_skill_level",
        table_name="skill_exercise_attempts",
    )
    op.drop_index(
        "ix_skill_exercise_attempts_user_id",
        table_name="skill_exercise_attempts",
    )
    op.drop_table("skill_exercise_attempts")
    op.drop_column("user_skill_levels", "ema_score")
