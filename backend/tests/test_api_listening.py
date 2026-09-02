"""Listening Gym API tests (SN-050).

Covers the dialogue catalog with is_locked gating, the detail endpoint
(200 for unlocked, 403 for premium-gated, 404 for unknown), and the
deterministic mock evaluation (identical input -> identical output,
all-correct = 100, all-wrong = 0, partial = expected, time round-trip,
engine version exact).
"""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  — registers all tables on Base.metadata
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.user import User
from app.services.content_service import (
    load_listening_dialogues,
    load_microlesson_seeds,
    load_pronunciation_drills,
    load_scenario_seeds,
    load_vocabulary_seeds,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def listening_client(
    db_engine, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_session
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def auth_headers(client: AsyncClient, email: str) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "Listen",
            "password": "maple-syrup-99",
            "native_language": "hi",
            "target_language": "en-CA",
        },
    )
    assert register.status_code == 201
    login = await client.post(
        "/api/auth/login",
        json={"email": email, "password": "maple-syrup-99"},
    )
    assert login.status_code == 200
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


async def test_listening_requires_authentication(
    listening_client: AsyncClient,
) -> None:
    response = await listening_client.get("/api/listening/dialogues")
    assert response.status_code == 401


async def test_list_dialogues_shows_all_12_with_correct_lock_state(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "free@example.com")

    response = await listening_client.get(
        "/api/listening/dialogues", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    gym = [d for d in body["dialogues"] if not d.get("unit_id")]
    assert len(gym) == 12
    for i, dialogue in enumerate(gym):
        assert "id" in dialogue
        assert "is_locked" in dialogue
        assert "listening_focus" in dialogue
        # First 4 free, rest premium — free tier sees premium locked.
        if i < 4:
            assert dialogue["is_premium"] is False, f"dialogue {i} should be free"
            assert dialogue["is_locked"] is False, f"free dialogue {i} locked"
        else:
            assert dialogue["is_premium"] is True, f"dialogue {i} premium"
            assert dialogue["is_locked"] is True, f"premium dialogue {i} unlocked"


async def test_premium_user_sees_all_unlocked(
    listening_client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(listening_client, "premium@example.com")
    user = (
        await db_session.execute(
            select(User).where(User.email == "premium@example.com")
        )
    ).scalar_one()
    user.subscription_tier = "premium"
    await db_session.commit()

    response = await listening_client.get(
        "/api/listening/dialogues", headers=headers
    )

    assert response.status_code == 200
    for dialogue in response.json()["dialogues"]:
        assert dialogue["is_locked"] is False, f"{dialogue['id']} should be unlocked"


async def test_detail_returns_full_dialogue_for_free_dialogue(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "detail@example.com")
    dialogues = load_listening_dialogues()
    free_dialogue = [d for d in dialogues if not d.is_premium][0]

    response = await listening_client.get(
        f"/api/listening/dialogues/{free_dialogue.id}", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == free_dialogue.id
    assert body["context"]
    assert len(body["turns"]) >= 4
    assert len(body["questions"]) == 3
    assert body["vocab_targets"]
    for turn in body["turns"]:
        assert turn["role"] in {"speaker", "listener", "system"}
        assert turn["pause_after_ms"] > 0
    for question in body["questions"]:
        assert len(question["choices"]) == 4
        assert 0 <= question["correct_index"] <= 3


async def test_detail_403_for_premium_dialogue_on_free_tier(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "free-detail@example.com")
    dialogues = load_listening_dialogues()
    premium_dialogue = [d for d in dialogues if d.is_premium][0]

    response = await listening_client.get(
        f"/api/listening/dialogues/{premium_dialogue.id}", headers=headers
    )

    assert response.status_code == 403


async def test_detail_404_for_unknown_dialogue(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "unknown@example.com")

    response = await listening_client.get(
        "/api/listening/dialogues/listen-does-not-exist", headers=headers
    )

    assert response.status_code == 404


async def test_evaluate_deterministic(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "eval@example.com")

    body = {"answers": [0, 1, 2], "time_seconds": 45}
    first = await listening_client.post(
        "/api/listening/dialogues/listen-coffee-morning-rush/evaluate",
        json=body,
        headers=headers,
    )
    second = await listening_client.post(
        "/api/listening/dialogues/listen-coffee-morning-rush/evaluate",
        json=body,
        headers=headers,
    )

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["engine_version"] == "sn050-mock-listening-v1"
    assert first.json()["total"] == 3
    assert first.json()["time_seconds"] == 45


async def test_evaluate_score_100_all_correct(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "perfect@example.com")
    dialogue = [d for d in load_listening_dialogues() if not d.is_premium][0]
    answers = [q.correct_index for q in dialogue.questions]

    response = await listening_client.post(
        f"/api/listening/dialogues/{dialogue.id}/evaluate",
        json={"answers": answers, "time_seconds": 60},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct_count"] == 3
    assert body["score"] == 100
    assert body["missed"] == []


async def test_evaluate_score_0_all_wrong(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "all-wrong@example.com")
    dialogue = [d for d in load_listening_dialogues() if not d.is_premium][0]
    wrong = [
        (q.correct_index + 1) % 4 for q in dialogue.questions
    ]

    response = await listening_client.post(
        f"/api/listening/dialogues/{dialogue.id}/evaluate",
        json={"answers": wrong, "time_seconds": 30},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct_count"] == 0
    assert body["score"] == 0
    assert len(body["missed"]) == 3
    for missed in body["missed"]:
        assert missed["prompt"]
        assert missed["your_answer"]
        assert missed["correct_answer"]
        assert missed["explanation"]


async def test_evaluate_partial_score(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "partial@example.com")
    dialogue = [d for d in load_listening_dialogues() if not d.is_premium][0]
    answers = [q.correct_index for q in dialogue.questions]
    answers[1] = (answers[1] + 1) % 4  # Miss exactly one.

    response = await listening_client.post(
        f"/api/listening/dialogues/{dialogue.id}/evaluate",
        json={"answers": answers, "time_seconds": 40},
        headers=headers,
    )

    assert response.status_code == 200
    body = response.json()
    assert body["correct_count"] == 2
    assert body["score"] == 67  # round(2/3 * 100)
    assert len(body["missed"]) == 1


async def test_evaluate_404_for_unknown_dialogue(
    listening_client: AsyncClient,
) -> None:
    headers = await auth_headers(listening_client, "eval404@example.com")

    response = await listening_client.post(
        "/api/listening/dialogues/listen-does-not-exist/evaluate",
        json={"answers": [0, 1, 2], "time_seconds": 30},
        headers=headers,
    )

    assert response.status_code == 404


async def test_listening_not_in_other_content_loaders() -> None:
    """Regression: the listening type stays isolated from other loaders."""
    assert len(load_scenario_seeds()) == 161
    assert len(load_vocabulary_seeds()) == 870
    assert len(load_microlesson_seeds()) == 24
    assert len(load_pronunciation_drills()) == 15
    listening_ids = {d.id for d in load_listening_dialogues()}
    scenario_ids = {s.id for s in load_scenario_seeds()}
    assert listening_ids.isdisjoint(scenario_ids)
