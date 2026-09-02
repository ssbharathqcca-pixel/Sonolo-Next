"""C15 F1/F2 prerequisite units + C12 unit tests.

Does not change C2 formulas, C8 graph, or C12 scoring.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.learn import get_llm_provider
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import Unit, UserSkillLevel, UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt, UnitTestSkillEvidence
from app.models.user import User
from app.services.content_service import (
    content_scenario_id,
    get_unit_document,
    persist_curriculum,
    validate_curriculum_content,
)
from app.services.mastery_service import (
    REQUIRED_EXERCISES,
    SKILLS,
    UNITS_FOR_LEVEL,
    compute_mastery_score,
)
from app.services.unit_test_service import RETRY_MESSAGE, SECTION_WEIGHTS

FIXED_NOW = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)

F1_PASS = {
    "listening": {
        "test-F1-listen-q1": 1,
        "test-F1-listen-q2": 1,
        "test-F1-listen-q3": 1,
        "test-F1-listen-q4": 0,
        "test-F1-listen-q5": "Please have your passport ready.",
    },
    "reading": {
        "test-F1-read-q1": 1,
        "test-F1-read-q2": 1,
        "test-F1-read-q3": 1,
        "test-F1-read-q4": "free",
        "test-F1-read-q5": ["passport", "ticket", "customs", "address", "gate"],
    },
    "speaking": {
        "transcript": (
            "Excuse me, please. I am at Pearson and I need a taxi. "
            "My address is 12 King Street in Toronto. Here is my passport. "
            "I can take the taxi from arrivals. Thank you so much because I am new here."
        )
    },
    "writing": {
        "task1": ["a", "the", "an"],
        "task2": "My name is Harpreet. I landed at Pearson. My address is 12 King Street.",
    },
}

F2_PASS = {
    "listening": {
        "test-F2-listen-q1": 1,
        "test-F2-listen-q2": 1,
        "test-F2-listen-q3": 1,
        "test-F2-listen-q4": 0,
        "test-F2-listen-q5": "The rent is due on the first day.",
    },
    "reading": {
        "test-F2-read-q1": 1,
        "test-F2-read-q2": 1,
        "test-F2-read-q3": 1,
        "test-F2-read-q4": "notice",
        "test-F2-read-q5": ["lease", "downtown", "rent", "deposit", "inquiry"],
    },
    "speaking": {
        "transcript": (
            "Excuse me, please. I need an apartment with a bedroom downtown. "
            "What is the rent for this place? Can I have a viewing on Saturday? "
            "Thank you so much because I am new here."
        )
    },
    "writing": {
        "task1": ["an", "the", "a"],
        "task2": "Hello landlord. What is the rent? I want a viewing on Saturday.",
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
            "name": "C15",
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


def _session_payload(scenario_id: str, text: str, duration: int = 60) -> dict:
    started = FIXED_NOW - timedelta(seconds=duration)
    return {
        "client_session_id": str(uuid4()),
        "scenario_id": scenario_id,
        "started_at": started.isoformat(),
        "ended_at": FIXED_NOW.isoformat(),
        "duration_seconds": duration,
        "transcript": [
            {"role": "user", "text": text},
            {"role": "assistant", "text": "Thank you."},
        ],
        "evaluation": {
            "scores": {
                "fluency": 82,
                "pronunciation": 82,
                "grammar": 82,
                "vocabulary": 82,
                "coherence": 82,
                "task_completion": 82,
            },
            "overall_score": 82,
            "insights": [],
        },
    }


def _journey_units(payload: dict) -> dict:
    return {
        unit["id"]: unit
        for band in payload["bands"]
        for unit in band["units"]
    }


async def _listen(http, headers, dialogue_id: str, dictation: str) -> None:
    response = await http.post(
        f"/api/listening/dialogues/{dialogue_id}/evaluate",
        headers=headers,
        json={"answers": [1, 1, 1], "time_seconds": 30, "dictation": [dictation]},
    )
    assert response.status_code == 200, response.text


async def _read(http, headers, content_id: str, answers: dict) -> None:
    start = await http.post(f"/api/learn/reading/{content_id}/start", headers=headers)
    assert start.status_code == 200, start.text
    submit = await http.post(
        f"/api/learn/reading/{content_id}/submit",
        headers=headers,
        json={"attempt_id": start.json()["attempt_id"], "answers": answers},
    )
    assert submit.status_code == 200, submit.text


async def _hunt(http, headers, hunt_id: str, words: list[str]) -> None:
    start = await http.post(
        f"/api/learn/vocabulary-hunt/{hunt_id}/start", headers=headers
    )
    assert start.status_code == 200
    submit = await http.post(
        f"/api/learn/vocabulary-hunt/{hunt_id}/submit",
        headers=headers,
        json={"attempt_id": start.json()["attempt_id"], "found_words": words},
    )
    assert submit.status_code == 200, submit.text


async def _speak(http, headers, scenario_slug: str, text: str) -> None:
    response = await http.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(str(content_scenario_id(scenario_slug)), text),
    )
    assert response.status_code == 200, response.text


async def _complete_f1(http: AsyncClient, headers: dict[str, str]) -> None:
    await _listen(
        http, headers, "listen-F1-announcement",
        "Have your passport ready for the officer.",
    )
    await _listen(
        http, headers, "listen-F1-customs",
        "Good morning. Passport and arrival form, please.",
    )
    await _listen(
        http, headers, "listen-F1-taxi",
        "I can take you. Put your luggage in the back.",
    )
    await _read(
        http, headers, "reading-F1-arrival-card",
        {
            "reading-F1-arrival-card-q1": 1,
            "reading-F1-arrival-card-q2": 1,
            "reading-F1-arrival-card-q3": "address",
        },
    )
    await _read(
        http, headers, "reading-F1-airport-signs",
        {
            "reading-F1-airport-signs-q1": 1,
            "reading-F1-airport-signs-q2": 1,
            "reading-F1-airport-signs-q3": "taxi",
        },
    )
    await _hunt(
        http, headers, "hunt-F1-arrival-card",
        ["passport", "Pearson", "customs", "luggage", "taxi"],
    )
    await http.post(
        "/api/learn/writing/writing-F1-sentence-builder/submit",
        headers=headers,
        json={"text": "My name is Harpreet."},
    )
    await http.post(
        "/api/learn/writing/writing-F1-address/submit",
        headers=headers,
        json={"text": "I am Harpreet. My address is 12 King Street, Toronto."},
    )
    await http.post(
        "/api/learn/writing/writing-F1-error-fix/submit",
        headers=headers,
        json={
            "text": "My name is Harpreet and I am at Pearson.",
            "found_errors": [
                {"original": "are", "corrected": "is"},
                {"original": "is", "corrected": "am"},
            ],
        },
    )
    await http.post(
        "/api/pronunciation/drills/pron-F1-airport/evaluate",
        headers=headers,
        json={"duration_seconds": 8},
    )
    await _speak(
        http, headers, "speak-F1-officer",
        "My name is Harpreet. Here is my passport and form. My address is 12 King Street. Thank you.",
    )
    await _speak(
        http, headers, "speak-F1-sprint",
        "Please take me from Pearson. My address is 12 King Street. I have luggage and a ticket.",
    )


async def _complete_f2(http: AsyncClient, headers: dict[str, str]) -> None:
    await _listen(
        http, headers, "listen-F2-voicemail",
        "Please come for a viewing Saturday at two.",
    )
    await _listen(
        http, headers, "listen-F2-roommate",
        "Yes. There is a kitchen. Utilities are in the rent.",
    )
    await _listen(
        http, headers, "listen-F2-viewing",
        "The deposit is one month of rent.",
    )
    await _read(
        http, headers, "reading-F2-listing",
        {
            "reading-F2-listing-q1": 1,
            "reading-F2-listing-q2": 1,
            "reading-F2-listing-q3": "rent",
        },
    )
    await _read(
        http, headers, "reading-F2-lease",
        {
            "reading-F2-lease-q1": 1,
            "reading-F2-lease-q2": 1,
            "reading-F2-lease-q3": "approval",
        },
    )
    await _hunt(
        http, headers, "hunt-F2-listing",
        ["rent", "utilities", "deposit", "landlord", "lease"],
    )
    await http.post(
        "/api/learn/writing/writing-F2-sentence-builder/submit",
        headers=headers,
        json={"text": "There is a bedroom."},
    )
    await http.post(
        "/api/learn/writing/writing-F2-inquiry/submit",
        headers=headers,
        json={"text": "Hello, I need an apartment downtown. I want a viewing on Saturday."},
    )
    await http.post(
        "/api/learn/writing/writing-F2-error-fix/submit",
        headers=headers,
        json={
            "text": "There is a kitchen and I need a lease.",
            "found_errors": [
                {"original": "are", "corrected": "is"},
                {"original": "needs", "corrected": "need"},
            ],
        },
    )
    await http.post(
        "/api/pronunciation/drills/pron-F2-home/evaluate",
        headers=headers,
        json={"duration_seconds": 8},
    )
    await _speak(
        http, headers, "speak-F2-landlord",
        "Hello, I need an apartment. What is the rent? Can I have a viewing please?",
    )
    await _speak(
        http, headers, "speak-F2-sprint",
        "I need a downtown apartment with a bedroom and a kitchen. The rent and lease should be clear.",
    )


def test_c2_contracts_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0
    assert REQUIRED_EXERCISES[1] == 6
    assert UNITS_FOR_LEVEL[1] == ("F1", "F2")
    assert SECTION_WEIGHTS["speaking"] == 0.30


def test_f1_f2_catalog_and_validation() -> None:
    assert validate_curriculum_content() == []
    f1 = get_unit_document("F1")
    f2 = get_unit_document("F2")
    f3 = get_unit_document("F3")
    assert f1 is not None and f2 is not None and f3 is not None
    assert f1["title"] == "Arrival Day"
    assert f1["story_chapter"] == "Harpreet lands at Pearson"
    assert f1["level_target"] == 1
    assert f1["language"] == "en-CA"
    assert f1["prerequisites"] == []
    assert len(f1["vocabulary_targets"]) == 15
    assert f1["unit_test_id"] == "test-F1"
    assert f2["title"] == "Finding Home"
    assert f2["story_chapter"] == "The apartment hunt"
    assert f2["prerequisites"] == ["F1"]
    assert f2["unit_test_id"] == "test-F2"
    assert f3["prerequisites"] == ["F2"]
    assert f3["listening_ids"] == ["listen-F3-superstore"]


@pytest.mark.asyncio
async def test_f1_f2_retrieval_auth_and_c10(
    client: AsyncClient,
) -> None:
    assert (await client.get("/api/learn/units/F1")).status_code == 401
    assert (await client.get("/api/learn/unit-test/F1")).status_code == 401
    headers = await _auth(client, "get@example.com")
    f1 = await client.get("/api/learn/units/F1", headers=headers)
    f2 = await client.get("/api/learn/units/F2", headers=headers)
    assert f1.status_code == 200
    assert f2.status_code == 200
    assert f1.json()["id"] == "F1"
    assert f2.json()["id"] == "F2"
    assert len(f1.json()["vocab_primer_ids"]) == 15
    assert f1.json()["grammar_spotlight_id"] == "grammar-F1-name-address"
    assert f2.json()["grammar_spotlight_id"] == "grammar-F2-there-is"
    test = await client.get("/api/learn/unit-test/F1", headers=headers)
    assert test.status_code == 200
    hidden = json.dumps(test.json())
    assert "Please have your passport ready." not in hidden


@pytest.mark.asyncio
async def test_fresh_user_f1_to_f3_and_c2_advancement(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "path@example.com")
    await persist_curriculum(db_session)
    start = await client.get("/api/learn/journey", headers=headers)
    assert start.status_code == 200
    assert start.json()["current_unit_id"] == "F1"
    units = _journey_units(start.json())
    assert units["F1"]["status"] == "current"
    assert units["F2"]["status"] == "locked"
    assert units["F3"]["status"] == "locked"

    blocked = await client.post(
        "/api/learn/unit-test/F1/submit", headers=headers, json=F1_PASS
    )
    assert blocked.status_code == 409

    await _complete_f1(client, headers)
    progress = (
        await db_session.execute(
            select(UserUnitProgress).join(Unit).where(Unit.unit_code == "F1")
        )
    ).scalar_one()
    assert progress.listening_complete is True
    assert progress.reading_complete is True
    assert progress.writing_complete is True
    assert progress.speaking_complete is True
    assert progress.unit_test_passed is False

    first = await client.post(
        "/api/learn/unit-test/F1/submit", headers=headers, json=F1_PASS
    )
    assert first.status_code == 200, first.text
    assert first.json()["passed"] is True
    await db_session.refresh(progress)
    assert progress.unit_test_passed is True
    after_f1 = await client.get("/api/learn/journey", headers=headers)
    units = _journey_units(after_f1.json())
    assert units["F1"]["status"] == "completed"
    assert units["F2"]["status"] == "current"
    assert units["F3"]["status"] == "locked"
    assert after_f1.json()["current_unit_id"] == "F2"
    levels = {
        row.skill: row.sonolo_level
        for row in (await db_session.execute(select(UserSkillLevel))).scalars()
    }
    assert all(level == 1 for level in levels.values())
    user = (
        await db_session.execute(select(User).where(User.email == "path@example.com"))
    ).scalar_one()
    assert user.sonolo_level == 1

    replay = await client.post(
        "/api/learn/unit-test/F1/submit", headers=headers, json=F1_PASS
    )
    assert replay.status_code == 200
    assert replay.json()["idempotent_replayed"] is True

    await _complete_f2(client, headers)
    second = await client.post(
        "/api/learn/unit-test/F2/submit", headers=headers, json=F2_PASS
    )
    assert second.status_code == 200, second.text
    assert second.json()["passed"] is True
    f2_progress = (
        await db_session.execute(
            select(UserUnitProgress).join(Unit).where(Unit.unit_code == "F2")
        )
    ).scalar_one()
    assert f2_progress.unit_test_passed is True
    after_f2 = await client.get("/api/learn/journey", headers=headers)
    units = _journey_units(after_f2.json())
    assert units["F2"]["status"] == "completed"
    assert units["F3"]["status"] == "current"
    assert units["F4"]["status"] == "locked"
    assert after_f2.json()["current_unit_id"] == "F3"

    evidence = (await db_session.execute(select(UnitTestSkillEvidence))).scalars().all()
    codes = set()
    for row in evidence:
        unit = await db_session.get(Unit, row.unit_id)
        codes.add(unit.unit_code)
    assert {"F1", "F2"} <= codes
    assert {row.skill for row in evidence} == set(SKILLS)

    practice = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED
            )
        )
    ).scalars().all()
    by_skill: dict[str, int] = {}
    for row in practice:
        by_skill[row.skill] = by_skill.get(row.skill, 0) + 1
        assert row.score is not None
    assert by_skill["listening"] >= 6
    assert by_skill["reading"] >= 6
    assert by_skill["writing"] >= 6
    assert by_skill["speaking"] >= 6

    levels = {
        row.skill: row
        for row in (await db_session.execute(select(UserSkillLevel))).scalars()
    }
    for skill in SKILLS:
        assert levels[skill].sonolo_level == 2
        assert levels[skill].ema_score is not None
    user = (
        await db_session.execute(select(User).where(User.email == "path@example.com"))
    ).scalar_one()
    assert user.sonolo_level == 1
    assert user.current_level != "F3"


@pytest.mark.asyncio
async def test_failed_unit_test_does_not_unlock_and_cools_down(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "fail@example.com")
    await persist_curriculum(db_session)
    await _complete_f1(client, headers)
    empty = {
        "listening": {},
        "reading": {},
        "speaking": {"transcript": ""},
        "writing": {},
    }
    failed = await client.post(
        "/api/learn/unit-test/F1/submit", headers=headers, json=empty
    )
    assert failed.status_code == 200
    assert failed.json()["passed"] is False
    assert failed.json()["fail_message"] == RETRY_MESSAGE
    progress = (
        await db_session.execute(
            select(UserUnitProgress).join(Unit).where(Unit.unit_code == "F1")
        )
    ).scalar_one()
    assert progress.unit_test_passed is False
    journey = await client.get("/api/learn/journey", headers=headers)
    units = _journey_units(journey.json())
    assert units["F1"]["status"] == "current"
    assert units["F2"]["status"] == "locked"
    blocked = await client.post(
        "/api/learn/unit-test/F1/submit", headers=headers, json=F1_PASS
    )
    assert blocked.status_code == 409
    assert blocked.json()["detail"] == RETRY_MESSAGE
