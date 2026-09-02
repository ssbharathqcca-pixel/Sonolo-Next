"""C6 Daily Mix v2 (Part XII). Does not change quest XP or C2 formulas."""

from collections.abc import AsyncIterator
from datetime import UTC, date, datetime, timedelta
from random import Random
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.user import User
from app.models.vocabulary import VocabularyCard
from app.services.daily_mix_service import (
    MixCandidate,
    calculate_skill_weights,
    deterministic_seed,
    generate_daily_mix,
    normalize_goal,
    select_content_for_skill,
)
from app.services.mastery_service import SKILLS, get_skill_recommendation
from app.services.quest_service import QUEST_DEFINITIONS

pytestmark = pytest.mark.asyncio


def _catalog() -> list[MixCandidate]:
    items: list[MixCandidate] = []
    for skill, count in (("speaking", 3), ("listening", 3), ("reading", 3), ("writing", 3)):
        for index in range(count):
            items.append(
                MixCandidate(
                    id=f"{skill}-{index}",
                    skill=skill,
                    title=f"{skill} {index}",
                    duration_minutes=8,
                    unit_code="F3" if index < 2 else None,
                    sonolo_level=2 if index < 2 else 1,
                    is_premium=index == 2,
                    source="unit" if index < 2 else "standalone",
                )
            )
    items.append(
        MixCandidate(
            id="hidden-unpub",
            skill="reading",
            title="hidden",
            duration_minutes=8,
            unit_code=None,
            sonolo_level=2,
            is_premium=False,
            source="standalone",
        )
    )
    return items


def _mix(**kwargs):
    defaults = dict(
        local_date=date(2026, 9, 2),
        skill_levels={skill: 2 for skill in SKILLS},
        goal="casual",
        overdue_reviews=0,
        unfinished_blocks=list(SKILLS),
        recent_errors={},
        catalog=_catalog(),
        rng=Random(1),
        allow_premium=False,
        current_unit="F3",
    )
    defaults.update(kwargs)
    return generate_daily_mix(**defaults)


def test_quest_xp_rewards_unchanged() -> None:
    rewards = {item.code: item.reward_xp for item in QUEST_DEFINITIONS}
    assert rewards == {"session_1": 20, "session_2": 30, "vocab_10": 20}


def test_c2_imbalance_api_unchanged() -> None:
    rec = get_skill_recommendation(
        {"speaking": 6, "listening": 6, "reading": 6, "writing": 2}
    )
    assert rec.priority == "critical"
    assert rec.daily_mix_weight == 0.50


def test_new_beginner_equal_weights() -> None:
    weights = calculate_skill_weights({skill: 1 for skill in SKILLS}, "casual")
    assert max(weights.values()) - min(weights.values()) < 0.12
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_balanced_learner_equal_base() -> None:
    weights = calculate_skill_weights({skill: 4 for skill in SKILLS}, "casual")
    assert abs(sum(weights.values()) - 1.0) < 1e-9
    assert weights["speaking"] > weights["writing"]


def test_highly_unbalanced_writing_boost() -> None:
    levels = {"speaking": 6, "listening": 6, "reading": 6, "writing": 2}
    weights = calculate_skill_weights(levels, "casual")
    assert weights["writing"] == max(weights.values())
    assert weights["writing"] >= 0.30
    mix = _mix(skill_levels=levels, rng=Random(7), unfinished_blocks=["writing"])
    writing_items = [item for item in mix.items if item.skill == "writing"]
    assert len(writing_items) >= 1
    assert mix.imbalance["priority"] == "critical"


def test_overdue_reviews_take_top_slot() -> None:
    mix = _mix(overdue_reviews=50)
    assert mix.items[0].type == "vocab_review"
    assert mix.items[0].priority == 0


def test_casual_goal_keeps_non_speaking() -> None:
    mix = _mix(goal="casual", unfinished_blocks=["speaking"])
    skills = {item.skill for item in mix.items if item.skill}
    assert skills & {"listening", "reading", "writing"}


def test_pr_readiness_boosts_speaking_and_writing() -> None:
    weights = calculate_skill_weights({skill: 3 for skill in SKILLS}, "pr_readiness")
    assert weights["speaking"] > weights["listening"]
    assert weights["writing"] > weights["listening"]


def test_legacy_travel_goal_maps_to_casual() -> None:
    assert normalize_goal("travel") == "casual"
    casual = calculate_skill_weights({skill: 3 for skill in SKILLS}, "casual")
    travel = calculate_skill_weights({skill: 3 for skill in SKILLS}, "travel")
    assert travel == casual


def test_deterministic_same_day_fresh_next_day() -> None:
    user_id = uuid4()
    seed_a = deterministic_seed(user_id, date(2026, 9, 2))
    seed_b = deterministic_seed(user_id, date(2026, 9, 2))
    seed_c = deterministic_seed(user_id, date(2026, 9, 3))
    assert seed_a == seed_b
    assert seed_a != seed_c
    first = _mix(rng=Random(seed_a))
    second = _mix(rng=Random(seed_b))
    assert [item.content_id for item in first.items] == [
        item.content_id for item in second.items
    ]
    third = _mix(rng=Random(seed_c), local_date=date(2026, 9, 3))
    assert third.date != first.date


def test_fallback_skips_premium_for_free_and_uses_standalone() -> None:
    catalog = [
        MixCandidate(
            id="unit-write",
            skill="writing",
            title="unit",
            duration_minutes=8,
            unit_code="F1",
            sonolo_level=1,
            is_premium=False,
            source="unit",
        ),
        MixCandidate(
            id="prem",
            skill="speaking",
            title="premium",
            duration_minutes=10,
            unit_code=None,
            sonolo_level=1,
            is_premium=True,
            source="standalone",
        ),
        MixCandidate(
            id="free-speak",
            skill="speaking",
            title="free",
            duration_minutes=10,
            unit_code=None,
            sonolo_level=1,
            is_premium=False,
            source="standalone",
        ),
    ]
    picked = select_content_for_skill(
        "speaking", 1, "F3", Random(1), set(), catalog, allow_premium=False
    )
    assert picked is not None
    assert picked.id == "free-speak"
    empty = select_content_for_skill(
        "listening", 9, "F9", Random(1), set(), catalog, allow_premium=False
    )
    assert empty is None


def test_empty_catalog_is_safe() -> None:
    mix = _mix(catalog=[], unfinished_blocks=[], overdue_reviews=0)
    assert mix.items == []
    assert mix.estimated_minutes == 0
    mix_vocab = _mix(catalog=[], unfinished_blocks=[], overdue_reviews=12)
    assert mix_vocab.items[0].type == "vocab_review"


def test_welcome_back_flag() -> None:
    mix = _mix(welcome_back=True, overdue_reviews=5)
    assert mix.welcome_back is True
    assert mix.items[0].type == "vocab_review"


def test_xp_possible_is_informational_not_award() -> None:
    mix = _mix(overdue_reviews=1)
    skill_count = sum(1 for item in mix.items if item.skill)
    assert mix.xp_possible == skill_count * 15 + 15


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
            "name": "C6",
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


async def test_daily_mix_api_auth_and_shape(client: AsyncClient) -> None:
    denied = await client.get("/api/learn/daily-mix")
    assert denied.status_code == 401
    headers = await _auth(client, "mix@example.com")
    response = await client.get("/api/learn/daily-mix", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert "items" in body
    assert "weights" in body
    assert set(body["weights"]) == set(SKILLS)
    assert abs(sum(body["weights"].values()) - 1.0) < 1e-6
    assert body["welcome_back"] is False
    ids = [item["content_id"] for item in body["items"] if item["content_id"]]
    assert "hidden-unpub" not in ids
    second = await client.get("/api/learn/daily-mix", headers=headers)
    assert second.json()["items"] == body["items"]


async def test_daily_mix_excludes_premium_for_free_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "free-mix@example.com")
    user_row = (
        await db_session.execute(select(User).where(User.email == "free-mix@example.com"))
    ).scalar_one()
    db_session.add(
        VocabularyCard(
            user_id=user_row.id,
            word="aisle",
            translations={"en": "aisle"},
            due_date=datetime.now(UTC) - timedelta(days=1),
            state=2,
        )
    )
    await db_session.commit()
    response = await client.get("/api/learn/daily-mix", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["items"][0]["type"] == "vocab_review"
    content_ids = [item["content_id"] for item in body["items"] if item["content_id"]]
    from app.services.content_service import load_listening_dialogues

    gym_premium = [d.id for d in load_listening_dialogues() if d.is_premium]
    assert not set(content_ids) & set(gym_premium)
