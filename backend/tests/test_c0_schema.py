"""C0 migration/schema tests (Alembic only; no ORM model changes).

Verifies Part XVI §16.2 tables/columns and Part II §2.3 level backfill
from generated SQL. Does not import application models for C0 entities.
"""

import subprocess
import sys

LEGACY_TABLES = {
    "users",
    "user_skills",
    "sessions",
    "scenarios",
    "vocabulary_cards",
    "user_badges",
    "daily_quests",
    "analytics_events",
}

C0_TABLES = {
    "units",
    "user_skill_levels",
    "user_unit_progress",
    "learning_sessions",
    "reading_exercises",
    "writing_exercises",
    "user_certificates",
}

FORBIDDEN_TABLES = {
    "curriculum_bands",
    "curriculum_levels",
    "curriculum_skills",
    "curriculum_units",
    "curriculum_activities",
    "curriculum_exercises",
    "curriculum_assessments",
    "user_skill_mastery",
    "user_activity_progress",
    "user_exercise_progress",
    "user_assessment_progress",
}


def _alembic(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        capture_output=True,
        text=True,
        timeout=120,
    )


def _upgrade_sql() -> str:
    result = _alembic("upgrade", "head", "--sql")
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_alembic_head_is_four_skill_curriculum() -> None:
    result = _alembic("heads")
    assert result.returncode == 0, result.stderr
    assert "0005_skill_evidence" in result.stdout
    heads = [line for line in result.stdout.splitlines() if line.strip()]
    assert len(heads) == 1


def test_alembic_history_is_linear_from_0003() -> None:
    result = _alembic("history")
    assert result.returncode == 0, result.stderr
    assert "0004_four_skill_curriculum -> 0005_skill_evidence" in result.stdout
    assert "0003_scenario_pack_id -> 0004_four_skill_curriculum" in result.stdout
    assert "0002_preferred_language -> 0003_scenario_pack_id" in result.stdout


def test_upgrade_sql_creates_c0_tables_and_preserves_legacy() -> None:
    sql = _upgrade_sql()
    for table in LEGACY_TABLES | C0_TABLES:
        assert f"CREATE TABLE {table}" in sql, f"missing CREATE TABLE {table}"
    for table in FORBIDDEN_TABLES:
        assert f"CREATE TABLE {table}" not in sql
    assert "DROP TABLE users" not in sql
    assert "DROP TABLE user_skills" not in sql
    assert "DROP TABLE sessions" not in sql
    assert "DROP TABLE scenarios" not in sql
    assert "DROP COLUMN current_level" not in sql
    assert "0004_four_skill_curriculum" in sql


def test_upgrade_sql_adds_sonolo_level_and_scenario_unit_id() -> None:
    sql = _upgrade_sql()
    assert "ALTER TABLE users ADD COLUMN sonolo_level" in sql
    assert "ALTER TABLE scenarios ADD COLUMN unit_id" in sql
    assert "ALTER TABLE scenarios ADD COLUMN sonolo_level" in sql
    assert "fk_scenarios_unit_id_units" in sql
    assert "FOREIGN KEY(unit_id) REFERENCES units (id)" in sql


def test_upgrade_sql_backfills_part_ii_section_2_3_mapping() -> None:
    sql = _upgrade_sql()
    assert "UPDATE users SET sonolo_level =" in sql
    assert "UPDATE scenarios SET sonolo_level =" in sql
    assert "WHEN 'seed' THEN 1" in sql
    assert "WHEN 'sprout' THEN 2" in sql
    assert "WHEN 'branch' THEN 4" in sql
    assert "WHEN 'bloom' THEN 6" in sql
    assert "WHEN 'canopy' THEN 7" in sql
    assert "WHEN 'summit' THEN 9" in sql
    assert "ELSE 1" in sql


def test_upgrade_sql_constraints_defaults_and_nullability() -> None:
    sql = _upgrade_sql()
    assert "uq_units_unit_code_language" in sql
    assert "uq_user_skill_levels_user_skill" in sql
    assert "uq_user_unit_progress_user_unit" in sql
    assert "uq_reading_exercises_content_id" in sql
    assert "uq_writing_exercises_content_id" in sql
    assert "uq_user_certificates_user_type_band_language" in sql
    assert "fk_user_skill_levels_user_id_users" in sql
    assert "fk_user_unit_progress_user_id_users" in sql
    assert "fk_user_unit_progress_unit_id_units" in sql
    assert "fk_learning_sessions_user_id_users" in sql
    assert "fk_learning_sessions_unit_id_units" in sql
    assert "fk_reading_exercises_unit_id_units" in sql
    assert "fk_writing_exercises_unit_id_units" in sql
    assert "fk_user_certificates_user_id_users" in sql
    assert "sonolo_level INTEGER DEFAULT 1 NOT NULL" in sql
    assert "unit_id UUID" in sql
    assert "speaking_complete BOOLEAN DEFAULT false NOT NULL" in sql
    assert "unit_test_passed BOOLEAN DEFAULT false NOT NULL" in sql
    assert "unit_test_score FLOAT" in sql
    assert "is_published BOOLEAN DEFAULT false NOT NULL" in sql


def test_downgrade_sql_to_0003_drops_c0_only() -> None:
    result = _alembic("downgrade", "--sql", "head:0003_scenario_pack_id")
    assert result.returncode == 0, result.stderr
    sql = result.stdout
    for table in C0_TABLES:
        assert f"DROP TABLE {table}" in sql, f"missing DROP TABLE {table}"
    assert "DROP TABLE users" not in sql
    assert "DROP TABLE user_skills" not in sql
    assert "DROP TABLE sessions" not in sql
    assert "DROP TABLE scenarios" not in sql
    assert "fk_scenarios_unit_id_units" in sql
    assert "DROP COLUMN sonolo_level" in sql
    assert "DROP COLUMN unit_id" in sql
