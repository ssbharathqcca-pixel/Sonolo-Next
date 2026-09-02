"""Pronunciation Lab API tests (SN-049).

Covers the drill catalog with is_locked gating, the detail endpoint
(200 for unlocked, 403 for premium-gated, 404 for unknown), and the
deterministic mock evaluation (two calls identical, engine version
exact, phoneme count 3‑5, overall range 0‑100).
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
    load_pronunciation_drills,
    load_scenario_seeds,
    load_vocabulary_seeds,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def pron_client(
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
            "name": "Pron",
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


async def test_pronunciation_requires_authentication(pron_client: AsyncClient) -> None:
    response = await pron_client.get("/api/pronunciation/drills")
    assert response.status_code == 401


async def test_list_drills_shows_all_12_with_correct_lock_state(
    pron_client: AsyncClient,
) -> None:
    headers = await auth_headers(pron_client, "free@example.com")

    response = await pron_client.get("/api/pronunciation/drills", headers=headers)

    assert response.status_code == 200
    body = response.json()
    assert len(body["drills"]) == 12
    for drill in body["drills"]:
        assert "id" in drill
        assert "title" in drill
        assert "is_locked" in drill
        assert "is_premium" in drill
        assert "theme_color" in drill
        assert "icon" in drill
    # First 3 free, rest premium — free tier sees premium drills locked.
    for i, drill in enumerate(body["drills"]):
        if i < 3:
            assert drill["is_premium"] is False, f"drill {i} should be free"
            assert drill["is_locked"] is False, f"free drill {i} should not be locked"
        else:
            assert drill["is_premium"] is True, f"drill {i} should be premium"
            assert drill["is_locked"] is True, f"premium drill {i} should be locked"


async def test_premium_user_sees_all_unlocked(
    pron_client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await auth_headers(pron_client, "premium@example.com")
    user = (
        await db_session.execute(
            select(User).where(User.email == "premium@example.com")
        )
    ).scalar_one()
    user.subscription_tier = "premium"
    await db_session.commit()

    response = await pron_client.get("/api/pronunciation/drills", headers=headers)

    assert response.status_code == 200
    for drill in response.json()["drills"]:
        assert drill["is_locked"] is False, f"{drill['id']} should be unlocked"


async def test_detail_returns_full_drill_for_free_drill(
    pron_client: AsyncClient,
) -> None:
    headers = await auth_headers(pron_client, "detail@example.com")
    drills = load_pronunciation_drills()
    free_drill = [d for d in drills if not d.is_premium][0]

    response = await pron_client.get(
        f"/api/pronunciation/drills/{free_drill.id}", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == free_drill.id
    assert body["target_sentence"]
    assert body["ipa_hint"]
    assert body["tip"]
    assert body["is_premium"] is False


async def test_detail_403_for_premium_drill_on_free_tier(
    pron_client: AsyncClient,
) -> None:
    headers = await auth_headers(pron_client, "free-detail@example.com")
    drills = load_pronunciation_drills()
    premium_drill = [d for d in drills if d.is_premium][0]

    response = await pron_client.get(
        f"/api/pronunciation/drills/{premium_drill.id}", headers=headers
    )

    assert response.status_code == 403


async def test_detail_404_for_unknown_drill(
    pron_client: AsyncClient,
) -> None:
    headers = await auth_headers(pron_client, "unknown@example.com")

    response = await pron_client.get(
        "/api/pronunciation/drills/pron-does-not-exist", headers=headers
    )

    assert response.status_code == 404


async def test_evaluate_deterministic(
    pron_client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    headers = await auth_headers(pron_client, "eval@example.com")
    # The flapped-T drill is premium — evaluate it as a premium user.
    user = (
        await db_session.execute(
            select(User).where(User.email == "eval@example.com")
        )
    ).scalar_one()
    user.subscription_tier = "premium"
    await db_session.commit()

    body = {"duration_seconds": 3}
    first = await pron_client.post(
        "/api/pronunciation/drills/pron-flapped-t/evaluate",
        json=body,
        headers=headers,
    )
    second = await pron_client.post(
        "/api/pronunciation/drills/pron-flapped-t/evaluate",
        json=body,
        headers=headers,
    )

    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["engine_version"] == "sn049-mock-phoneme-v1"
    assert 3 <= len(first.json()["phonemes"]) <= 5
    assert 0 <= first.json()["overall"] <= 100
    for phoneme in first.json()["phonemes"]:
        assert 0 <= phoneme["score"] <= 100
        assert phoneme["symbol"]
        assert phoneme["tip"]


async def test_evaluate_404_for_unknown_drill(
    pron_client: AsyncClient,
) -> None:
    headers = await auth_headers(pron_client, "eval404@example.com")

    response = await pron_client.post(
        "/api/pronunciation/drills/pron-does-not-exist/evaluate",
        json={"duration_seconds": 3},
        headers=headers,
    )

    assert response.status_code == 404


async def test_pronunciation_not_in_scenario_or_vocab_seeds() -> None:
    """Regression: the pronunciation type is ignored by scenario/vocab seeders."""
    assert len(load_scenario_seeds()) == 161
    assert all(
        not seed.pack_id.startswith("canadian-speech")
        for seed in load_scenario_seeds()
    )
    assert len(load_vocabulary_seeds()) == 870