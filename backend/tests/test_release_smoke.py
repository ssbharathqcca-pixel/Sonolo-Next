"""Release closure smoke test (SN-014B Track 3).

Proves the fresh-user journey end to end on a fresh database:
register -> login -> scenarios -> vocabulary materialization -> due
reviews -> one answer -> one eligible session with XP/streak/quests/
skills -> quest list -> cross-user session isolation.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

import app.models  # noqa: F401
from app.core.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import create_app
from app.models.scenario import Scenario

pytestmark = pytest.mark.asyncio

FIXED_NOW = datetime(2026, 8, 22, 20, 0, tzinfo=UTC)


@pytest_asyncio.fixture
async def db_engine(tmp_path):
    """Fresh file-backed database per test (nothing pre-seeded)."""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{tmp_path.as_posix()}/release.db"
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield engine
    await engine.dispose()


@pytest_asyncio.fixture
async def db_session(db_engine) -> AsyncIterator[AsyncSession]:
    factory = async_sessionmaker(db_engine, expire_on_commit=False)
    session = factory()
    yield session
    await session.close()


@pytest_asyncio.fixture
async def smoke_client(
    db_engine, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))
    factory = async_sessionmaker(db_engine, expire_on_commit=False)

    async def override_session() -> AsyncIterator[AsyncSession]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = override_session
    monkeypatch.setattr(
        "app.api.sessions.utc_now", lambda: FIXED_NOW
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def register_and_login(
    client: AsyncClient, email: str
) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": email.split("@")[0].title(),
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
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def test_fresh_user_release_journey(smoke_client, db_session) -> None:
    # 3. Ensure scenarios exist (same service the seed CLI uses).
    from app.services.content_service import seed_scenarios

    seeded = await seed_scenarios(db_session)
    assert seeded == 161
    scenario = (
        await db_session.execute(select(Scenario).limit(1))
    ).scalar_one()

    # 1-2. Register and log in a brand-new user.
    headers = await register_and_login(smoke_client, "fresh@example.com")

    # 4-5. First /review/due materializes the SN-009 pack: >= 1 due card.
    due = await smoke_client.get("/api/review/due", headers=headers)
    assert due.status_code == 200
    due_cards = due.json()
    assert len(due_cards) >= 1
    first_card = due_cards[0]
    assert first_card["state"] == 0
    assert first_card["translations"].keys() == {"pa", "hi", "zh", "es"}

    # 6. Answer one review card successfully.
    answer = await smoke_client.post(
        "/api/review/answer",
        json={"card_id": first_card["id"], "rating": "good"},
        headers=headers,
    )
    assert answer.status_code == 200
    assert answer.json()["state"] == 2
    assert answer.json()["scheduled_days"] == 1

    # 7. Complete one eligible session against a real scenario.
    completion = await smoke_client.post(
        "/api/sessions/complete",
        headers=headers,
        json={
            "client_session_id": str(uuid.uuid4()),
            "scenario_id": str(scenario.id),
            "started_at": (FIXED_NOW - timedelta(seconds=300)).isoformat(),
            "ended_at": FIXED_NOW.isoformat(),
            "duration_seconds": 300,
            "transcript": [
                {"role": "user", "text": "Could I get a medium double-double?"},
                {"role": "assistant", "text": "Great choice!"},
            ],
            "evaluation": {
                "scores": {
                    "fluency": 82.0,
                    "pronunciation": 82.0,
                    "grammar": 82.0,
                    "vocabulary": 82.0,
                    "coherence": 82.0,
                    "task_completion": 82.0,
                },
                "overall_score": 82.0,
                "insights": [],
            },
        },
    )
    assert completion.status_code == 200

    # 8. XP, streak, quests, and skills all advanced.
    body = completion.json()
    assert body["xp_eligible"] is True
    assert body["xp"]["session_xp"] > 0
    assert body["streak_current"] == 1
    assert len(body["skills"]) == 6
    quest_codes = {quest["code"] for quest in body["quests"]}
    assert quest_codes == {"session_1", "session_2", "vocab_10"}
    session_id = body["session_id"]

    # 9. /quests/today shows the three expected quests.
    quests = await smoke_client.get("/api/quests/today", headers=headers)
    assert quests.status_code == 200
    assert len(quests.json()["quests"]) == 3

    # 10. User B cannot access User A's session feedback.
    other_headers = await register_and_login(smoke_client, "other@example.com")
    feedback_body = {
        "session_id": str(session_id),
        "transcript": [
            {"role": "user", "text": "Could I get a medium double-double please?"}
        ],
        "scenario_targets": {"vocabulary": [], "grammar": []},
        "duration_seconds": 5.0,
    }
    forbidden = await smoke_client.post(
        f"/api/sessions/{session_id}/feedback",
        json=feedback_body,
        headers=other_headers,
    )
    assert forbidden.status_code == 404

    own = await smoke_client.post(
        f"/api/sessions/{session_id}/feedback",
        json=feedback_body,
        headers=headers,
    )
    assert own.status_code == 200


async def test_review_due_materialization_is_idempotent(
    smoke_client, db_session
) -> None:
    from sqlalchemy import func

    from app.models.user import User
    from app.models.vocabulary import VocabularyCard
    from app.services.content_service import (
        _vocabulary_pack_limit,
        load_vocabulary_seeds,
    )

    headers = await register_and_login(smoke_client, "fresh@example.com")
    first = await smoke_client.get("/api/review/due", headers=headers)
    second = await smoke_client.get("/api/review/due", headers=headers)

    assert first.status_code == second.status_code == 200
    # The response is capped by the default limit=20; the materialized
    # set behind it is the full pack, exactly once.
    assert len(first.json()) == len(second.json()) == 20
    user = (
        await db_session.execute(
            select(User).where(User.email == "fresh@example.com")
        )
    ).scalar_one()
    card_count = int(
        (
            await db_session.execute(
                select(func.count())
                .select_from(VocabularyCard)
                .where(VocabularyCard.user_id == user.id)
            )
        ).scalar_one()
    )
    expected = min(len(load_vocabulary_seeds()), _vocabulary_pack_limit())
    assert card_count == expected  # Full manifest pack, materialized once.
