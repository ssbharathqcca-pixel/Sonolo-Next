"""C9 Progress skill-level API. Consumes C2; does not change C2."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import UserSkillLevel
from app.models.user import User
from app.services.mastery_service import (
    SKILLS,
    compute_mastery_score,
    display_level,
    get_skill_recommendation,
    readiness_level,
)
from app.services.progress_service import build_skill_progress
from app.services.quest_service import QUEST_DEFINITIONS

CRITICAL = {"speaking": 5, "listening": 4, "reading": 6, "writing": 3}
HIGH = {"speaking": 5, "listening": 5, "reading": 5, "writing": 3}
BALANCED = {"speaking": 5, "listening": 5, "reading": 5, "writing": 5}


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


async def _auth(
    http: AsyncClient, email: str = "c9@example.com"
) -> dict[str, str]:
    register = await http.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C9",
            "password": "maple-syrup-99",
            "native_language": "en",
            "target_language": "en-CA",
        },
    )
    assert register.status_code == 201
    login = await http.post(
        "/api/auth/login",
        json={"email": email, "password": "maple-syrup-99"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _user(db: AsyncSession, email: str = "c9@example.com") -> User:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one()


async def _set_levels(db: AsyncSession, user_id, levels: dict[str, int]) -> None:
    for skill, level in levels.items():
        db.add(
            UserSkillLevel(user_id=user_id, skill=skill, sonolo_level=level)
        )
    await db.flush()


def test_c2_formula_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0


def test_quest_xp_untouched() -> None:
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }


def test_assembly_delegates_display_readiness_and_imbalance_to_c2() -> None:
    payload = build_skill_progress(CRITICAL)
    rec = get_skill_recommendation(CRITICAL)
    assert payload["display_level"] == display_level(CRITICAL) == 4
    assert payload["readiness_level"] == readiness_level(CRITICAL) == 3
    assert [item["skill"] for item in payload["skills"]] == list(SKILLS)
    assert {item["skill"]: item["level"] for item in payload["skills"]} == CRITICAL
    assert payload["imbalance"]["priority"] == rec.priority == "critical"
    assert payload["imbalance"]["skill"] == rec.skill == "writing"
    assert payload["imbalance"]["message"] == rec.message
    assert payload["imbalance"]["daily_mix_weight"] == rec.daily_mix_weight == 0.50


def test_gap_2_high_and_balanced_match_c2() -> None:
    high = build_skill_progress(HIGH)
    rec_high = get_skill_recommendation(HIGH)
    assert high["imbalance"]["priority"] == rec_high.priority == "high"
    assert high["imbalance"]["skill"] == "writing"
    assert high["imbalance"]["message"] == rec_high.message
    assert high["imbalance"]["daily_mix_weight"] == 0.40

    balanced = build_skill_progress(BALANCED)
    rec_bal = get_skill_recommendation(BALANCED)
    assert balanced["imbalance"]["priority"] == rec_bal.priority == "balanced"
    assert balanced["imbalance"]["skill"] is None
    assert balanced["display_level"] == 5
    assert balanced["readiness_level"] == 5


def test_missing_rows_default_to_level_1_without_inventing_progress() -> None:
    payload = build_skill_progress({})
    assert {item["skill"]: item["level"] for item in payload["skills"]} == {
        skill: 1 for skill in SKILLS
    }
    assert payload["display_level"] == 1
    assert payload["readiness_level"] == 1
    assert payload["imbalance"]["priority"] == "balanced"


@pytest.mark.asyncio
async def test_skills_endpoint_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/progress/skills")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_api_returns_c2_view_and_ignores_legacy_level(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client)
    user = await _user(db_session)
    user.current_level = "summit"
    user.sonolo_level = 9
    await _set_levels(db_session, user.id, CRITICAL)
    response = await client.get("/api/progress/skills", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["display_level"] == 4
    assert payload["readiness_level"] == 3
    assert payload["imbalance"]["priority"] == "critical"
    assert payload["imbalance"]["skill"] == "writing"
    assert payload["imbalance"]["message"] == get_skill_recommendation(CRITICAL).message
    assert "summit" not in str(payload)
    assert payload["display_level"] != user.sonolo_level
    levels = {item["skill"]: item["level"] for item in payload["skills"]}
    assert levels == CRITICAL


@pytest.mark.asyncio
async def test_fresh_user_api_defaults_all_skills_to_one(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, email="c9-fresh@example.com")
    response = await client.get("/api/progress/skills", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["display_level"] == 1
    assert payload["readiness_level"] == 1
    assert payload["imbalance"]["priority"] == "balanced"
    assert {item["level"] for item in payload["skills"]} == {1}
