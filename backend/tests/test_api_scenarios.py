"""Integration tests for the scenario catalog endpoint (SN-015, SN-026)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, update as sa_update
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.scenario import Scenario
from app.services.content_service import seed_scenarios

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def scenarios_client(
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


async def auth_headers(client: AsyncClient) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": "pavan@example.com",
            "name": "Pavan",
            "password": "maple-syrup-99",
            "native_language": "hi",
            "target_language": "en-CA",
        },
    )
    assert register.status_code == 201
    login = await client.post(
        "/api/auth/login",
        json={"email": "pavan@example.com", "password": "maple-syrup-99"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_scenarios_requires_authentication(
    scenarios_client: AsyncClient,
) -> None:
    response = await scenarios_client.get("/api/scenarios")
    assert response.status_code == 401


async def test_scenarios_returns_the_seeded_catalog(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    seeded = await seed_scenarios(db_session)
    assert seeded == 161

    response = await scenarios_client.get(
        "/api/scenarios", headers=await auth_headers(scenarios_client)
    )

    assert response.status_code == 200
    body = response.json()
    assert len(body["scenarios"]) == 110
    titles = [scenario["title"] for scenario in body["scenarios"]]
    assert titles == sorted(titles)  # Stable, title-ordered list.
    first = body["scenarios"][0]
    assert set(first.keys()) == {
        "id",
        "title",
        "description",
        "category",
        "target_language",
        "pack_id",
        "difficulty",
        "is_locked",
    }
    assert isinstance(first["id"], str)
    assert first["difficulty"] is None or 1 <= first["difficulty"] <= 5
    # Every seeded row maps back to its manifest pack (SN-035).
    assert all(scenario["pack_id"] for scenario in body["scenarios"])


async def test_free_user_sees_premium_scenarios_locked(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    response = await scenarios_client.get(
        "/api/scenarios", headers=await auth_headers(scenarios_client)
    )

    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    locked_titles = {
        scenario["title"] for scenario in scenarios if scenario["is_locked"]
    }
    premium_titles = set(
        (
            await db_session.execute(
                select(Scenario.title).where(Scenario.is_premium.is_(True))
            )
        ).scalars().all()
    )
    en_premium_titles = set(
        (
            await db_session.execute(
                select(Scenario.title).where(
                    Scenario.is_premium.is_(True),
                    Scenario.target_language.like("en%"),
                )
            )
        ).scalars().all()
    )
    # Premium scenarios are exactly the locked entries for a free-tier
    # caller — 8 from canadian-life-v1, 5 from canadian-life-v2, and 3
    # each from workplace-english-v1, healthcare-english-v1,
    # housing-english-v1, and finance-english-v1 = 25 en; plus 10 from
    # job-interviews-english-v1 and 10 from hospitality-english-v1 =
    # 45 en; plus 3 each from quebec-healthcare-v1 and
    # quebec-workplace-v1 = 51 total. The English catalog sees only the
    # 45 English premiums.
    assert len(premium_titles) == 57
    assert len(en_premium_titles) == 45
    assert locked_titles == en_premium_titles


async def test_premium_user_sees_nothing_locked(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    headers = await auth_headers(scenarios_client)
    upgrade = await scenarios_client.post("/api/users/me/upgrade", headers=headers)
    assert upgrade.status_code == 200

    response = await scenarios_client.get("/api/scenarios", headers=headers)

    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    assert len(scenarios) == 110
    assert all(scenario["is_locked"] is False for scenario in scenarios)


async def test_language_param_returns_only_french_scenarios(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    headers = await auth_headers(scenarios_client)

    response = await scenarios_client.get(
        "/api/scenarios",
        params={"language": "fr"},
        headers=headers,
    )

    assert response.status_code == 200
    scenarios = response.json()["scenarios"]
    french_titles = set(
        (
            await db_session.execute(
                select(Scenario.title).where(
                    Scenario.target_language.like("fr%")
                )
            )
        ).scalars().all()
    )
    assert len(french_titles) == 45
    assert {scenario["title"] for scenario in scenarios} == french_titles


async def test_default_catalog_follows_preferred_language(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    headers = await auth_headers(scenarios_client)

    english = await scenarios_client.get("/api/scenarios", headers=headers)
    assert english.status_code == 200
    # Default preference is English: the 110 en-CA scenarios, no French rows.
    assert len(english.json()["scenarios"]) == 110

    switched = await scenarios_client.post(
        "/api/users/me/language", json={"language": "fr"}, headers=headers
    )
    assert switched.status_code == 200

    french = await scenarios_client.get("/api/scenarios", headers=headers)
    assert french.status_code == 200
    titles = [scenario["title"] for scenario in french.json()["scenarios"]]
    french_titles = set(
        (
            await db_session.execute(
                select(Scenario.title).where(Scenario.target_language.like("fr%"))
            )
        ).scalars().all()
    )
    assert set(titles) == french_titles

    back = await scenarios_client.post(
        "/api/users/me/language", json={"language": "en"}, headers=headers
    )
    assert back.status_code == 200
    restored = await scenarios_client.get("/api/scenarios", headers=headers)
    assert len(restored.json()["scenarios"]) == 110


async def test_language_rejects_unknown_values(
    scenarios_client: AsyncClient,
) -> None:
    headers = await auth_headers(scenarios_client)
    response = await scenarios_client.get(
        "/api/scenarios", params={"language": "es"}, headers=headers
    )
    assert response.status_code == 422


async def test_premium_gating_still_applies_to_french_scenarios(
    scenarios_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    # Pick a free French scenario and flip it to premium; the gating
    # must cover all existing plus the newly flipped one.
    free_french_title = (
        await db_session.execute(
            select(Scenario.title).where(
                Scenario.target_language.like("fr%"),
                Scenario.is_premium.is_(False),
            ).limit(1)
        )
    ).scalars().first()
    await db_session.execute(
        sa_update(Scenario)
        .where(Scenario.title == free_french_title)
        .values(is_premium=True)
    )
    await db_session.commit()
    headers = await auth_headers(scenarios_client)

    free_view = await scenarios_client.get(
        "/api/scenarios", params={"language": "fr"}, headers=headers
    )
    assert free_view.status_code == 200
    locked = {
        scenario["title"]
        for scenario in free_view.json()["scenarios"]
        if scenario["is_locked"]
    }
    expected = set(
        (
            await db_session.execute(
                select(Scenario.title).where(
                    Scenario.target_language.like("fr%"),
                    Scenario.is_premium.is_(True),
                )
            )
        ).scalars().all()
    )
    assert locked == expected
    assert free_french_title in locked

    upgrade = await scenarios_client.post(
        "/api/users/me/upgrade", headers=headers
    )
    assert upgrade.status_code == 200
    premium_view = await scenarios_client.get(
        "/api/scenarios", params={"language": "fr"}, headers=headers
    )
    assert all(
        scenario["is_locked"] is False
        for scenario in premium_view.json()["scenarios"]
    )
