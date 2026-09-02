"""C7 diagnostic / placement (Part VI). Does not change C2, XP, or onboarding."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import utc_now
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import UserSkillLevel
from app.models.evidence import SkillExerciseAttempt
from app.models.user import User
from app.services.diagnostic_service import (
    apply_skipped_skills,
    calculate_placement,
    load_diagnostic_items,
)
from app.services.mastery_service import SKILLS, compute_mastery_score
from app.services.quest_service import QUEST_DEFINITIONS

pytestmark = pytest.mark.asyncio


def test_placement_algorithm_all_paths() -> None:
    assert calculate_placement([("hard", 0.8), ("hard", 0.7)]) == 7
    assert calculate_placement(
        [("hard", 0.5), ("hard", 0.5), ("medium", 0.8), ("medium", 0.7)]
    ) == 6
    assert calculate_placement([("medium", 0.8), ("medium", 0.7), ("hard", 0.0)]) == 5
    assert calculate_placement(
        [("medium", 0.5), ("medium", 0.5), ("easy", 0.9), ("easy", 0.8), ("hard", 0.0)]
    ) == 4
    assert calculate_placement([("easy", 0.9), ("easy", 0.8), ("medium", 0.0)]) == 3
    assert calculate_placement([("easy", 0.6), ("easy", 0.6)]) == 2
    assert calculate_placement([("easy", 0.2), ("easy", 0.2)]) == 1
    assert calculate_placement([]) == 1


def test_skipped_skill_rules() -> None:
    placed = {"listening": 5, "reading": 4, "speaking": 5, "writing": 5}
    assert apply_skipped_skills(placed, {"speaking"})["speaking"] == 3
    both = apply_skipped_skills(placed, {"speaking", "writing"})
    assert both["speaking"] == 3
    assert both["writing"] == 3
    assert apply_skipped_skills(placed, set(SKILLS)) == {skill: 1 for skill in SKILLS}


def test_quest_xp_untouched() -> None:
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }


def test_c2_formula_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0


def test_catalog_has_six_items_per_skill() -> None:
    items = load_diagnostic_items()
    assert len(items) == 24
    for skill in SKILLS:
        skill_items = [item for item in items if item.skill == skill]
        assert len(skill_items) == 6
        assert [item.tier for item in skill_items].count("easy") == 2
        assert [item.tier for item in skill_items].count("medium") == 2
        assert [item.tier for item in skill_items].count("hard") == 2


@pytest_asyncio.fixture
async def client(
    db_engine, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def _auth(client: AsyncClient, email: str) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C7",
            "password": "maple-syrup-99",
            "native_language": "en",
            "target_language": "en-CA",
        },
    )
    assert register.status_code == 201
    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "maple-syrup-99"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _answer_skill(client, headers, items, skill: str, correct: bool) -> None:
    for item in items:
        if item["skill"] != skill:
            continue
        payload = 0
        if correct:
            catalog = {row.id: row for row in load_diagnostic_items()}
            payload = catalog[item["id"]].correct_index
        response = await client.post(
            "/api/learn/diagnostic/answer",
            headers=headers,
            json={"item_id": item["id"], "answer": payload},
        )
        assert response.status_code == 200, response.text


async def test_start_hides_answers_and_resume(client: AsyncClient) -> None:
    headers = await _auth(client, "start@example.com")
    first = await client.post("/api/learn/diagnostic/start", headers=headers)
    assert first.status_code == 200
    body = first.json()
    assert body["status"] == "in_progress"
    assert len(body["items"]) == 24
    assert "correct_index" not in body["items"][0]
    second = await client.get("/api/learn/diagnostic", headers=headers)
    assert second.json()["session_id"] == body["session_id"]


async def test_skip_all_places_beginner(client: AsyncClient, db_session: AsyncSession) -> None:
    headers = await _auth(client, "skipall@example.com")
    response = await client.post("/api/learn/diagnostic/skip-all", headers=headers)
    assert response.status_code == 200
    assert response.json()["placement"] == {skill: 1 for skill in SKILLS}
    user = (
        await db_session.execute(select(User).where(User.email == "skipall@example.com"))
    ).scalar_one()
    assert user.onboarding_completed is False
    assert user.sonolo_level == 1
    levels = (
        await db_session.execute(
            select(UserSkillLevel).where(UserSkillLevel.user_id == user.id)
        )
    ).scalars().all()
    assert {row.skill: row.sonolo_level for row in levels} == {skill: 1 for skill in SKILLS}


async def test_complete_listening_reading_skip_production(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "place@example.com")
    start = await client.post("/api/learn/diagnostic/start", headers=headers)
    items = start.json()["items"]
    await client.post(
        "/api/learn/diagnostic/skip",
        headers=headers,
        json={"skills": ["speaking", "writing"]},
    )
    await _answer_skill(client, headers, items, "listening", True)
    await _answer_skill(client, headers, items, "reading", True)
    done = await client.post("/api/learn/diagnostic/complete", headers=headers)
    assert done.status_code == 200, done.text
    placement = done.json()["placement"]
    assert placement["listening"] == 7
    assert placement["reading"] == 7
    assert placement["speaking"] == 6
    assert placement["writing"] == 6
    again = await client.post("/api/learn/diagnostic/complete", headers=headers)
    assert again.json()["placement"] == placement
    user = (
        await db_session.execute(select(User).where(User.email == "place@example.com"))
    ).scalar_one()
    assert user.onboarding_completed is False
    assert user.sonolo_level == 6


async def test_incomplete_complete_rejected(client: AsyncClient) -> None:
    headers = await _auth(client, "inc@example.com")
    await client.post("/api/learn/diagnostic/start", headers=headers)
    response = await client.post("/api/learn/diagnostic/complete", headers=headers)
    assert response.status_code == 409


async def test_placement_correction_one_time(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "corr@example.com")
    await client.post("/api/learn/diagnostic/skip-all", headers=headers)
    user = (
        await db_session.execute(select(User).where(User.email == "corr@example.com"))
    ).scalar_one()
    now = utc_now()
    for index in range(5):
        db_session.add(
            SkillExerciseAttempt(
                user_id=user.id,
                unit_id=None,
                skill="reading",
                activity_type="reading_exercise",
                content_id=f"real-{index}",
                sonolo_level=1,
                score=95.0,
                status="submitted",
                started_at=now,
                submitted_at=now,
                result_json={},
            )
        )
    await db_session.commit()
    first = await client.post(
        "/api/learn/diagnostic/correction/reading", headers=headers
    )
    assert first.status_code == 200
    assert first.json()["adjusted"] is True
    assert first.json()["new_level"] == 2
    second = await client.post(
        "/api/learn/diagnostic/correction/reading", headers=headers
    )
    assert second.json()["adjusted"] is False
    assert second.json()["reason"] == "already_applied"
    level = (
        await db_session.execute(
            select(UserSkillLevel).where(
                UserSkillLevel.user_id == user.id,
                UserSkillLevel.skill == "reading",
            )
        )
    ).scalar_one()
    assert level.sonolo_level == 2
    attempts = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.content_id.like("diag-%")
            )
        )
    ).scalars().all()
    assert attempts == []
