"""C14 F3 Vocab Primer, Grammar Spotlight, Review & Reinforce.

Does not add C0 completion flags. Does not change C2, C8, C12, or C13.
"""

from __future__ import annotations

import inspect
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.learning.fsrs import FSRS
from app.main import create_app
from app.models.curriculum import UserUnitProgress
from app.services.content_service import (
    get_grammar_document,
    get_unit_document,
    get_vocabulary_seed,
    load_microlesson_seeds,
    load_vocabulary_seeds,
    validate_curriculum_content,
)
from app.services.mastery_service import (
    UNIT_TEST_OVERALL_MIN,
    UNIT_TEST_SKILL_MIN,
    UNITS_FOR_LEVEL,
    compute_mastery_score,
)
from app.services.quest_service import QUEST_DEFINITIONS
from app.services.unit_test_service import SECTION_WEIGHTS

F3_TARGETS = [
    "grocery",
    "aisle",
    "receipt",
    "checkout",
    "cashier",
    "discount",
    "sale",
    "flyer",
    "cart",
    "transfer",
    "fare",
    "Presto",
    "bus",
    "subway",
    "change",
    "bag",
    "fresh",
    "frozen",
    "dairy",
    "loaf",
]
F3_PRIMER_IDS = [f"vocab-F3-{word.casefold()}" for word in F3_TARGETS]


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


async def _auth(http: AsyncClient, email: str) -> dict[str, str]:
    register = await http.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C14",
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


def test_c2_c12_c13_contracts_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0
    assert UNIT_TEST_OVERALL_MIN == 70.0
    assert UNIT_TEST_SKILL_MIN == 60.0
    assert SECTION_WEIGHTS["speaking"] == 0.30
    assert UNITS_FOR_LEVEL[1] == ("F1", "F2")
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }
    assert inspect.getsource(FSRS).count("def review_card") == 1


def test_f3_primer_and_grammar_content() -> None:
    assert validate_curriculum_content() == []
    document = get_unit_document("F3")
    assert document is not None
    assert document["vocab_primer_ids"] == F3_PRIMER_IDS
    assert document["grammar_spotlight_id"] == "grammar-F3-articles"
    assert document["listening_ids"] == ["listen-F3-superstore"]
    assert document["reading_ids"] == ["reading-F3-grocery-flyer"]
    assert document["speaking_ids"] == [
        "speak-F3-clerk",
        "speak-F3-sprint",
        "pron-F3-grocery",
    ]
    assert document["writing_ids"] == [
        "writing-F3-sentence-builder",
        "writing-F3-shopping-list",
        "writing-F3-error-fix",
    ]
    assert document["unit_test_id"] == "test-F3"
    words = []
    for content_id in document["vocab_primer_ids"]:
        seed = get_vocabulary_seed(content_id)
        assert seed is not None
        assert seed.unit_id == "F3"
        assert seed.is_published is True
        assert seed.sonolo_level == 2
        words.append(seed.word)
    assert words == F3_TARGETS
    spotlight = get_grammar_document("grammar-F3-articles")
    assert spotlight is not None
    assert spotlight["unit_id"] == "F3"
    assert spotlight["is_published"] is True
    assert spotlight["level"] == 2
    assert spotlight["grammar_targets"] == document["grammar_targets"]
    assert len(load_microlesson_seeds()) == 24


@pytest.mark.asyncio
async def test_primer_grammar_review_auth_and_published_filter(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/learn/vocab-primer/F3")).status_code == 401
    assert (await client.get("/api/learn/grammar/F3")).status_code == 401
    assert (await client.get("/api/learn/review/F3")).status_code == 401
    headers = await _auth(client, "pub@example.com")
    assert (await client.get("/api/learn/vocab-primer/F9", headers=headers)).status_code == 404
    assert (await client.get("/api/learn/grammar/F9", headers=headers)).status_code == 404
    assert (await client.get("/api/learn/review/F9", headers=headers)).status_code == 404


@pytest.mark.asyncio
async def test_vocab_primer_resolves_twenty_f3_targets(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "primer@example.com")
    response = await client.get("/api/learn/vocab-primer/F3", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["unit_id"] == "F3"
    assert body["language"] == "en-CA"
    assert body["level"] == 2
    assert body["ids"] == F3_PRIMER_IDS
    assert [item["word"] for item in body["items"]] == F3_TARGETS
    assert all(item["unit_id"] == "F3" for item in body["items"])
    assert all(item["sonolo_level"] == 2 for item in body["items"])


@pytest.mark.asyncio
async def test_grammar_spotlight_scope_and_no_mastery_engine(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "grammar@example.com")
    response = await client.get("/api/learn/grammar/F3", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "grammar-F3-articles"
    assert body["unit_id"] == "F3"
    assert body["language"] == "en-CA"
    assert body["level"] == 2
    assert body["grammar_targets"] == [
        "Articles: a, an, the",
        "Can I have / Where is...?",
        "Count vs. uncount nouns",
    ]
    blob = (body["explanation"] + str(body["sections"])).casefold()
    assert "can i have" in blob
    assert "where is" in blob
    assert "uncount" in blob
    assert "score" not in body
    assert "mastery" not in body


@pytest.mark.asyncio
async def test_f3_review_uses_fsrs_cards_and_does_not_complete(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "review@example.com")
    empty = await client.get("/api/learn/review/F3", headers=headers)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["type"] == "unit_review"
    assert body["fsrs"] is True
    assert body["completes_unit"] is False
    assert body["unit_id"] == "F3"
    assert body["grammar_spotlight_id"] == "grammar-F3-articles"
    assert body["situation"] == "Grocery run & transit"
    assert body["vocabulary_ids"] == F3_PRIMER_IDS
    assert {item["word"] for item in body["cards"]} == set(F3_TARGETS)
    assert all(item["card_id"] is None for item in body["cards"])

    due = await client.get("/api/review/due", headers=headers)
    assert due.status_code == 200
    due_words = {card["word"] for card in due.json()}
    assert len(due.json()) <= 20
    stored = (
        await db_session.execute(select(UserUnitProgress))
    ).scalars().all()
    assert stored == []

    filled = await client.get("/api/learn/review/F3", headers=headers)
    assert filled.status_code == 200
    bound = [item for item in filled.json()["cards"] if item["card_id"]]
    assert bound
    assert {item["word"] for item in bound} <= set(F3_TARGETS)
    first = bound[0]
    answer = await client.post(
        "/api/review/answer",
        headers=headers,
        json={"card_id": first["card_id"], "rating": "good"},
    )
    assert answer.status_code == 200
    assert "scheduled_days" in answer.json()
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one_or_none()
    assert progress is None or (
        progress.unit_test_passed is False
        and progress.speaking_complete is False
        and progress.listening_complete is False
        and progress.reading_complete is False
        and progress.writing_complete is False
    )


@pytest.mark.asyncio
async def test_generic_review_still_materializes_and_catalog_keeps_skills(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "generic@example.com")
    due = await client.get("/api/review/due", headers=headers)
    assert due.status_code == 200
    assert due.json()
    f3_targets = {word.casefold() for word in F3_TARGETS}
    due_words = {card["word"].casefold() for card in due.json()}
    assert not f3_targets.issubset(due_words)
    catalog = await client.get("/api/learn/units/F3", headers=headers)
    assert catalog.status_code == 200
    body = catalog.json()
    assert len(body["vocab_primer_ids"]) == 20
    assert body["grammar_spotlight_id"] == "grammar-F3-articles"
    assert body["listening_ids"] == ["listen-F3-superstore"]
    assert body["speaking_ids"] == [
        "speak-F3-clerk",
        "speak-F3-sprint",
        "pron-F3-grocery",
    ]
    assert "writing-F3-sentence-builder" in body["writing_ids"]
    gym = len(load_vocabulary_seeds())
    assert gym == 870
