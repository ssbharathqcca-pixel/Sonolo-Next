"""C1 content contract: F3 JSON, validation, GET /api/learn/units/F3."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.services.content_service import (
    load_reading_documents,
    load_unit_documents,
    load_vocabulary_hunt_documents,
    load_writing_documents,
    persist_curriculum,
    validate_curriculum_content,
)

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def learn_client(
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


async def _auth(client: AsyncClient) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": "c1@example.com",
            "name": "C1",
            "password": "maple-syrup-99",
            "native_language": "en",
            "target_language": "en-CA",
        },
    )
    assert register.status_code == 201
    login = await client.post(
        "/api/auth/login",
        json={"email": "c1@example.com", "password": "maple-syrup-99"},
    )
    assert login.status_code == 200
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


def test_f3_content_validates_with_zero_errors() -> None:
    errors = validate_curriculum_content()
    assert errors == []


def test_f3_is_level_2_with_required_reading_and_hunt() -> None:
    units = load_unit_documents()
    assert {item["id"] for item in units} == {"F1", "F2", "F3"}
    unit = next(item for item in units if item["id"] == "F3")
    assert unit["id"] == "F3"
    assert unit["level_target"] == 2
    assert unit["title"] == "First Week"
    assert unit["story_chapter"] == "Grocery run & transit"
    required = unit["reading_required_activities"]
    types = {item["type"] for item in required}
    assert types == {"reading_exercise", "vocabulary_hunt"}
    reading = next(
        item
        for item in load_reading_documents()
        if item["id"] == "reading-F3-grocery-flyer"
    )
    assert reading["level"] == 2
    assert all(q["type"] != "vocabulary_hunt" for q in reading["questions"])
    hunt = next(
        item
        for item in load_vocabulary_hunt_documents()
        if item["id"] == "hunt-F3-grocery-flyer"
    )
    assert hunt["type"] == "vocabulary_hunt"
    assert hunt["reading_exercise_id"] == "reading-F3-grocery-flyer"
    assert len(load_writing_documents()) == 9


async def test_persist_curriculum_upserts_catalog(db_session: AsyncSession) -> None:
    counts = await persist_curriculum(db_session)
    assert counts["units"] == 3
    assert counts["reading"] == 5
    assert counts["writing"] == 9
    again = await persist_curriculum(db_session)
    assert again == counts


async def test_get_unit_f3_requires_auth(learn_client: AsyncClient) -> None:
    response = await learn_client.get("/api/learn/units/F3")
    assert response.status_code == 401


async def test_get_unit_f3_returns_linked_ids(learn_client: AsyncClient) -> None:
    headers = await _auth(learn_client)
    response = await learn_client.get("/api/learn/units/F3", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == "F3"
    assert body["level_target"] == 2
    assert "reading-F3-grocery-flyer" in body["reading_ids"]
    assert "writing-F3-sentence-builder" in body["writing_ids"]
    assert len(body["vocab_primer_ids"]) == 20
    assert body["grammar_spotlight_id"] == "grammar-F3-articles"
    required_ids = {item["id"] for item in body["reading_required_activities"]}
    assert required_ids == {"reading-F3-grocery-flyer", "hunt-F3-grocery-flyer"}


async def test_unknown_unit_is_404(learn_client: AsyncClient) -> None:
    headers = await _auth(learn_client)
    response = await learn_client.get("/api/learn/units/F9", headers=headers)
    assert response.status_code == 404
