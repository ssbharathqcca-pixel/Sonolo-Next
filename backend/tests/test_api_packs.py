"""Integration tests for the content pack catalog endpoint (SN-030, SN-035)."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

import app.models  # noqa: F401  — registers all tables on Base.metadata
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.services.content_service import seed_scenarios

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def packs_client(
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
    token = login.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _packs_by_id(body: dict) -> dict:
    return {pack["id"]: pack for pack in body["packs"]}


async def test_packs_returns_only_scenario_packs_with_exact_fields(
    packs_client: AsyncClient, db_session: AsyncSession
) -> None:
    response = await packs_client.get("/api/packs")

    assert response.status_code == 200
    body = response.json()
    ids = {pack["id"] for pack in body["packs"]}
    # Vocabulary packs stay server-side (SN-030).
    assert ids == {
        "canadian-life-v1",
        "canadian-life-v2",
        "quebec-life-v1",
        "workplace-english-v1",
        "healthcare-english-v1",
        "quebec-healthcare-v1",
        "quebec-workplace-v1",
        "housing-english-v1",
        "finance-english-v1",
        "quebec-housing-v1",
        "quebec-finance-v1",
        "smalltalk-english-v1",
        "job-interviews-english-v1",
        "hospitality-english-v1",
        "speaking-f3-en-ca",
        "speaking-f1-en-ca",
        "speaking-f2-en-ca",
    }
    first = body["packs"][0]
    assert set(first.keys()) == {
        "id",
        "type",
        "language",
        "title",
        "description",
        "category",
        "tier",
        "theme_color",
        "icon",
        "scenario_count",
        "premium_count",
    }


async def test_pack_counts_match_seeded_scenarios(
    packs_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)

    response = await packs_client.get("/api/packs")

    assert response.status_code == 200
    by_id = _packs_by_id(response.json())
    assert by_id["workplace-english-v1"]["scenario_count"] == 10
    assert by_id["workplace-english-v1"]["premium_count"] == 3
    assert by_id["healthcare-english-v1"]["scenario_count"] == 10
    assert by_id["healthcare-english-v1"]["premium_count"] == 3
    assert by_id["quebec-life-v1"]["scenario_count"] == 5
    assert by_id["quebec-life-v1"]["premium_count"] == 0
    assert by_id["canadian-life-v1"]["scenario_count"] == 20
    assert by_id["canadian-life-v2"]["scenario_count"] == 20
    assert by_id["quebec-healthcare-v1"]["scenario_count"] == 10
    assert by_id["quebec-healthcare-v1"]["premium_count"] == 3
    assert by_id["quebec-workplace-v1"]["scenario_count"] == 10
    assert by_id["quebec-workplace-v1"]["premium_count"] == 3
    assert by_id["housing-english-v1"]["scenario_count"] == 10
    assert by_id["housing-english-v1"]["premium_count"] == 3
    assert by_id["finance-english-v1"]["scenario_count"] == 10
    assert by_id["finance-english-v1"]["premium_count"] == 3
    assert by_id["quebec-housing-v1"]["scenario_count"] == 10
    assert by_id["quebec-housing-v1"]["premium_count"] == 3
    assert by_id["quebec-finance-v1"]["scenario_count"] == 10
    assert by_id["quebec-finance-v1"]["premium_count"] == 3
    assert by_id["smalltalk-english-v1"]["scenario_count"] == 10
    assert by_id["smalltalk-english-v1"]["premium_count"] == 0
    assert by_id["job-interviews-english-v1"]["scenario_count"] == 10
    assert by_id["job-interviews-english-v1"]["premium_count"] == 10
    assert by_id["hospitality-english-v1"]["scenario_count"] == 10
    assert by_id["hospitality-english-v1"]["premium_count"] == 10
    assert by_id["speaking-f3-en-ca"]["scenario_count"] == 2
    assert by_id["speaking-f3-en-ca"]["premium_count"] == 0
    assert by_id["speaking-f1-en-ca"]["scenario_count"] == 2
    assert by_id["speaking-f2-en-ca"]["scenario_count"] == 2
    total = sum(pack["scenario_count"] for pack in by_id.values())
    assert total == 161


async def test_scenario_responses_carry_pack_id_per_language(
    packs_client: AsyncClient, db_session: AsyncSession
) -> None:
    await seed_scenarios(db_session)
    headers = await auth_headers(packs_client)

    english = await packs_client.get("/api/scenarios", headers=headers)
    assert english.status_code == 200
    english_pack_ids = {
        scenario["pack_id"] for scenario in english.json()["scenarios"]
    }
    assert english_pack_ids == {
        "canadian-life-v1",
        "canadian-life-v2",
        "workplace-english-v1",
        "healthcare-english-v1",
        "housing-english-v1",
        "finance-english-v1",
        "smalltalk-english-v1",
        "job-interviews-english-v1",
        "hospitality-english-v1",
    }

    french = await packs_client.get(
        "/api/scenarios", params={"language": "fr"}, headers=headers
    )
    assert french.status_code == 200
    french_pack_ids = {
        scenario["pack_id"] for scenario in french.json()["scenarios"]
    }
    assert french_pack_ids == {
        "quebec-life-v1",
        "quebec-healthcare-v1",
        "quebec-workplace-v1",
        "quebec-housing-v1",
        "quebec-finance-v1",
    }
