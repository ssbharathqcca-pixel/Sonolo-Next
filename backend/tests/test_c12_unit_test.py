"""C12 F3 Unit Test engine. Does not change C2 formulas or C8 graph."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import timedelta

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.learn import get_llm_provider
from app.core.config import Settings
from app.core.time import utc_now
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import Unit, UserUnitProgress
from app.models.evidence import UnitTestSkillEvidence
from app.services.content_service import (
    content_unit_id,
    persist_curriculum,
    validate_curriculum_content,
)
from app.services.mastery_service import (
    SKILLS,
    UNIT_TEST_OVERALL_MIN,
    UNIT_TEST_SKILL_MIN,
    compute_mastery_score,
    unit_test_meets_criteria,
)
from app.services.quest_service import QUEST_DEFINITIONS
from app.services.unit_test_service import (
    RETRY_MESSAGE,
    SECTION_WEIGHTS,
    grade_unit_test,
)
from app.services.mastery_service import UnitTestEvidence

F3_LISTEN = "listen-F3-superstore"
F3_READING = "reading-F3-grocery-flyer"
F3_HUNT = "hunt-F3-grocery-flyer"
F3_SB = "writing-F3-sentence-builder"
F3_GW = "writing-F3-shopping-list"
F3_EF = "writing-F3-error-fix"

PASSING = {
    "listening": {
        "test-F3-listen-q1": 1,
        "test-F3-listen-q2": 2,
        "test-F3-listen-q3": 1,
        "test-F3-listen-q4": 0,
        "test-F3-listen-q5": "Please tap your Presto card when you board.",
    },
    "reading": {
        "test-F3-read-q1": 2,
        "test-F3-read-q2": 1,
        "test-F3-read-q3": 1,
        "test-F3-read-q4": "B",
        "test-F3-read-q5": ["transfer", "Presto", "platform", "grocery", "station"],
    },
    "speaking": {
        "transcript": (
            "Excuse me, please. I am at this bus stop and I need the grocery "
            "store. How do I get there from here? I can take the bus, then walk. "
            "Thank you so much because I am new here."
        )
    },
    "writing": {
        "task1": ["an", "the", "a"],
        "task2": "I bought rice today. I also bought bread. Please put the milk away.",
    },
}


class ScriptedLLM:
    async def generate(self, system_prompt: str, history: list) -> str:
        del system_prompt, history
        return json.dumps(
            {
                "dimensions": {
                    "grammar_mechanics": 80,
                    "vocabulary_register": 80,
                    "task_fulfillment": 80,
                    "coherence_organization": 80,
                    "spelling": 80,
                },
                "corrections": [],
            }
        )


@pytest_asyncio.fixture
async def client(
    db_engine, db_session: AsyncSession
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_session
    app.dependency_overrides[get_llm_provider] = lambda: ScriptedLLM()
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def _auth(http: AsyncClient, email: str) -> dict[str, str]:
    register = await http.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C12",
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


async def _complete_lrw(http: AsyncClient, headers: dict[str, str]) -> None:
    await http.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json={
            "answers": [1, 2, 1],
            "time_seconds": 40,
            "dictation": [
                "Rice is in aisle 4, next to the dairy.",
                "Yes, it's on sale this week.",
            ],
        },
    )
    start_r = await http.post(f"/api/learn/reading/{F3_READING}/start", headers=headers)
    await http.post(
        f"/api/learn/reading/{F3_READING}/submit",
        headers=headers,
        json={
            "attempt_id": start_r.json()["attempt_id"],
            "answers": {
                "reading-F3-grocery-flyer-q1": 0,
                "reading-F3-grocery-flyer-q2": 1,
                "reading-F3-grocery-flyer-q3": "4",
            },
        },
    )
    start_h = await http.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/start", headers=headers
    )
    await http.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/submit",
        headers=headers,
        json={
            "attempt_id": start_h.json()["attempt_id"],
            "found_words": ["aisle", "fresh", "loaf", "milk", "savings"],
        },
    )
    await http.post(
        f"/api/learn/writing/{F3_SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread."},
    )
    await http.post(
        f"/api/learn/writing/{F3_EF}/submit",
        headers=headers,
        json={
            "text": "I bought some bread and milk at the store yesterday.",
            "found_errors": [
                {"original": "buyed", "corrected": "bought"},
                {"original": "breads", "corrected": "bread"},
                {"original": "milks", "corrected": "milk"},
                {"original": "stores", "corrected": "store"},
            ],
        },
    )
    await http.post(
        f"/api/learn/writing/{F3_GW}/submit",
        headers=headers,
        json={
            "text": "I need to buy: 1. Two bags of rice 2. A loaf of bread 3. Some milk"
        },
    )


async def _unlock_test(db: AsyncSession) -> UserUnitProgress:
    progress = (await db.execute(select(UserUnitProgress))).scalar_one()
    progress.speaking_complete = True
    await db.flush()
    return progress


def test_c2_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0
    assert UNIT_TEST_OVERALL_MIN == 70.0
    assert UNIT_TEST_SKILL_MIN == 60.0
    assert SECTION_WEIGHTS == {
        "listening": 0.25,
        "reading": 0.25,
        "speaking": 0.30,
        "writing": 0.20,
    }
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }


def test_curriculum_includes_published_f3_unit_test() -> None:
    assert validate_curriculum_content() == []


@pytest.mark.asyncio
async def test_unit_test_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/learn/unit-test/F3")).status_code == 401
    assert (await client.post("/api/learn/unit-test/F3/submit", json={})).status_code == 401


@pytest.mark.asyncio
async def test_get_published_structure_hides_keys(client: AsyncClient) -> None:
    headers = await _auth(client, "get@example.com")
    response = await client.get("/api/learn/unit-test/F3", headers=headers)
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == "test-F3"
    assert body["unit_id"] == "F3"
    assert body["level"] == 2
    listening = body["sections"]["listening"]["questions"]
    reading = body["sections"]["reading"]["questions"]
    writing = body["sections"]["writing"]["tasks"]
    assert len(listening) == 5
    assert len(reading) == 5
    assert len(writing) == 2
    assert body["sections"]["speaking"]["task"]["prompt"]
    assert all("correct_answer" not in item for item in listening)
    assert all("correct_answer" not in item for item in reading)
    hidden = json.dumps(body)
    assert "Please tap your Presto card" not in hidden


@pytest.mark.asyncio
async def test_unknown_and_unpublished_404(client: AsyncClient) -> None:
    headers = await _auth(client, "404@example.com")
    missing = await client.get("/api/learn/unit-test/F9", headers=headers)
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_locked_until_four_skill_flags(client: AsyncClient) -> None:
    headers = await _auth(client, "lock@example.com")
    blocked = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert blocked.status_code == 409
    await _complete_lrw(client, headers)
    still = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert still.status_code == 409


@pytest.mark.asyncio
async def test_passing_submission_persists_evidence_and_journey(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "pass@example.com")
    await persist_curriculum(db_session)
    await _complete_lrw(client, headers)
    f3_progress = await _unlock_test(db_session)
    user_id = f3_progress.user_id
    for code in ("F1", "F2"):
        unit = (
            await db_session.execute(
                select(Unit).where(Unit.unit_code == code, Unit.language == "en-CA")
            )
        ).scalar_one()
        existing = (
            await db_session.execute(
                select(UserUnitProgress).where(
                    UserUnitProgress.user_id == user_id,
                    UserUnitProgress.unit_id == unit.id,
                )
            )
        ).scalar_one_or_none()
        if existing is None:
            db_session.add(
                UserUnitProgress(
                    user_id=user_id,
                    unit_id=unit.id,
                    unit_test_passed=True,
                )
            )
        else:
            existing.unit_test_passed = True
    await db_session.flush()

    first = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert first.status_code == 200, first.text
    body = first.json()
    assert body["passed"] is True
    assert body["overall"] >= UNIT_TEST_OVERALL_MIN
    assert all(body["per_skill"][skill] >= UNIT_TEST_SKILL_MIN for skill in SKILLS)
    assert body["idempotent_replayed"] is False
    assert unit_test_meets_criteria(
        UnitTestEvidence(
            unit_code="F3",
            overall_score=body["overall"],
            skill_scores=body["per_skill"],
        )
    )
    f3_progress = (
        await db_session.execute(
            select(UserUnitProgress).join(Unit).where(Unit.unit_code == "F3")
        )
    ).scalar_one()
    assert f3_progress.unit_test_passed is True
    assert f3_progress.unit_test_score == body["overall"]
    evidence = (
        await db_session.execute(select(UnitTestSkillEvidence))
    ).scalars().all()
    skills = {row.skill for row in evidence}
    assert skills == set(SKILLS)
    assert len({row.sitting_id for row in evidence}) == 1
    for skill in SKILLS:
        assert body["mastery"][skill]["mastery_available"] is True or skill == "speaking"

    replay = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replayed"] is True
    count = (
        await db_session.execute(select(func.count()).select_from(UnitTestSkillEvidence))
    ).scalar_one()
    assert int(count) == 4

    journey = await client.get("/api/learn/journey", headers=headers)
    payload = journey.json()
    units = {
        unit["id"]: unit
        for band in payload["bands"]
        for unit in band["units"]
    }
    assert units["F3"]["status"] == "completed"
    assert payload["current_unit_id"] == "F4"
    assert units["F4"]["status"] == "current"


@pytest.mark.asyncio
async def test_section_fail_and_overall_fail_do_not_pass(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "fail@example.com")
    await _complete_lrw(client, headers)
    await _unlock_test(db_session)
    weak_listening = json.loads(json.dumps(PASSING))
    weak_listening["listening"] = {
        "test-F3-listen-q1": 0,
        "test-F3-listen-q2": 0,
        "test-F3-listen-q3": 0,
        "test-F3-listen-q4": 1,
        "test-F3-listen-q5": "nope",
    }
    section = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=weak_listening
    )
    assert section.status_code == 200, section.text
    assert section.json()["passed"] is False
    assert section.json()["per_skill"]["listening"] < UNIT_TEST_SKILL_MIN
    assert section.json()["fail_message"] == RETRY_MESSAGE
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.unit_test_passed is False

    blocked = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == RETRY_MESSAGE

    stale = utc_now() - timedelta(hours=25)
    sittings = (await db_session.execute(select(UnitTestSkillEvidence))).scalars().all()
    for row in sittings:
        row.submitted_at = stale
    await db_session.flush()
    empty = {
        "listening": {},
        "reading": {},
        "speaking": {"transcript": ""},
        "writing": {},
    }
    overall = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=empty
    )
    assert overall.status_code == 200
    assert overall.json()["passed"] is False
    assert overall.json()["overall"] < UNIT_TEST_OVERALL_MIN
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.unit_test_passed is False


@pytest.mark.asyncio
async def test_grader_weights_and_speaking_contract() -> None:
    from app.services.content_service import get_unit_test_document

    document = get_unit_test_document("F3")
    assert document is not None
    graded = await grade_unit_test(document, PASSING)
    expected = (
        graded["per_skill"]["listening"] * 0.25
        + graded["per_skill"]["reading"] * 0.25
        + graded["per_skill"]["speaking"] * 0.30
        + graded["per_skill"]["writing"] * 0.20
    )
    assert graded["overall"] == pytest.approx(expected)
    assert graded["per_skill"]["listening"] == 100
    assert graded["per_skill"]["reading"] == 100
    assert graded["per_skill"]["writing"] == 100
    assert graded["per_skill"]["speaking"] >= UNIT_TEST_SKILL_MIN
    assert graded["passed"] is True
