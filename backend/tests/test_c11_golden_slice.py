"""C11 Golden Slice: F3 integration against the real C0–C10 surface.

Walks every executable F3 stage and asserts remaining physical blockers.
C12–C14 added unit-test, speaking, vocab primer, grammar, and F3 review
catalog surfaces. Remaining blockers: F1/F2 and Golden Slice completion.
Vocab/Grammar/Review are not Unit Test gates.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.learn import get_llm_provider
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import UserSkillLevel, UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.models.user import User
from app.services.content_service import REPO_ROOT, get_unit_document
from app.services.mastery_service import (
    SKILLS,
    UNITS_FOR_LEVEL,
    compute_mastery_score,
    display_level,
    get_skill_recommendation,
    readiness_level,
)
from app.services.progress_service import build_skill_progress
from app.services.quest_service import QUEST_DEFINITIONS

F3_LISTEN = "listen-F3-superstore"
F3_READING = "reading-F3-grocery-flyer"
F3_HUNT = "hunt-F3-grocery-flyer"
F3_SB = "writing-F3-sentence-builder"
F3_GW = "writing-F3-shopping-list"
F3_EF = "writing-F3-error-fix"
READING_ANSWERS = {
    "reading-F3-grocery-flyer-q1": 0,
    "reading-F3-grocery-flyer-q2": 1,
    "reading-F3-grocery-flyer-q3": "4",
}
HUNT_WORDS = ["aisle", "fresh", "loaf", "milk", "savings"]
LISTEN_ANSWERS = [1, 2, 1]
DICT_1 = "Rice is in aisle 4, next to the dairy."
DICT_2 = "Yes, it's on sale this week."


class ScriptedLLM:
    def __init__(self) -> None:
        self.payload = json.dumps(
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

    async def generate(
        self, system_prompt: str, history: list[dict[str, str]]
    ) -> str:
        del system_prompt, history
        return self.payload


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


async def _auth(http: AsyncClient, email: str = "c11@example.com") -> dict[str, str]:
    register = await http.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C11",
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


def _f3_unit(payload: dict[str, Any], code: str = "F3") -> dict[str, Any]:
    for band in payload["bands"]:
        for unit in band["units"]:
            if unit["id"] == code:
                return unit
    raise AssertionError(f"{code} missing from journey")


def test_c2_and_xp_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0
    assert display_level({"speaking": 5, "listening": 4, "reading": 6, "writing": 3}) == 4
    assert readiness_level({"speaking": 5, "listening": 4, "reading": 6, "writing": 3}) == 3
    rec = get_skill_recommendation(
        {"speaking": 5, "listening": 4, "reading": 6, "writing": 3}
    )
    assert rec.priority == "critical"
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }
    assert UNITS_FOR_LEVEL[2] == ("F3", "F4")


def test_f3_catalog_is_authoritative_and_incomplete_for_golden_slice() -> None:
    document = get_unit_document("F3")
    assert document is not None
    assert document["id"] == "F3"
    assert document["title"] == "First Week"
    assert document["story_chapter"] == "Grocery run & transit"
    assert 15 <= len(document["vocabulary_targets"]) <= 25
    assert document["listening_ids"] == [F3_LISTEN]
    assert document["reading_ids"] == [F3_READING]
    assert {item["id"] for item in document["reading_required_activities"]} == {
        F3_READING,
        F3_HUNT,
    }
    assert document["writing_ids"] == [F3_SB, F3_GW, F3_EF]
    assert document["vocab_primer_ids"] == [
        "vocab-F3-grocery",
        "vocab-F3-aisle",
        "vocab-F3-receipt",
        "vocab-F3-checkout",
        "vocab-F3-cashier",
        "vocab-F3-discount",
        "vocab-F3-sale",
        "vocab-F3-flyer",
        "vocab-F3-cart",
        "vocab-F3-transfer",
        "vocab-F3-fare",
        "vocab-F3-presto",
        "vocab-F3-bus",
        "vocab-F3-subway",
        "vocab-F3-change",
        "vocab-F3-bag",
        "vocab-F3-fresh",
        "vocab-F3-frozen",
        "vocab-F3-dairy",
        "vocab-F3-loaf",
    ]
    assert document["speaking_ids"] == [
        "speak-F3-clerk",
        "speak-F3-sprint",
        "pron-F3-grocery",
    ]
    assert document["grammar_spotlight_id"] == "grammar-F3-articles"
    assert document["unit_test_id"] == "test-F3"
    content = REPO_ROOT / "content"
    assert (content / "listening" / "listening-F3-superstore.json").is_file()
    assert (content / "reading" / "reading-F3-grocery-flyer.json").is_file()
    assert (content / "vocabulary_hunts" / "hunt-F3-grocery-flyer.json").is_file()
    assert (content / "writing" / "writing-F3-en-ca.json").is_file()
    assert (content / "unit_tests" / "test-F3-en-ca.json").is_file()
    assert (content / "scenarios" / "speaking-F3-en-ca.json").is_file()
    assert (content / "pronunciation" / "pronunciation-F3-en-ca.json").is_file()
    assert (content / "vocabulary" / "vocab-F3-en-ca.json").is_file()
    assert (content / "grammar" / "grammar-F3-en-ca.json").is_file()


@pytest.mark.asyncio
async def test_executable_lrw_evidence_does_not_complete_f3(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client)
    catalog = await client.get("/api/learn/units/F3", headers=headers)
    assert catalog.status_code == 200
    assert catalog.json()["id"] == "F3"
    assert catalog.json()["listening_ids"] == [F3_LISTEN]
    assert catalog.json()["speaking_ids"] == [
        "speak-F3-clerk",
        "speak-F3-sprint",
        "pron-F3-grocery",
    ]
    assert len(catalog.json()["vocab_primer_ids"]) == 20
    assert catalog.json()["grammar_spotlight_id"] == "grammar-F3-articles"

    listen = await client.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json={
            "answers": LISTEN_ANSWERS,
            "time_seconds": 40,
            "dictation": [DICT_1, DICT_2],
        },
    )
    assert listen.status_code == 200, listen.text
    assert listen.json()["unit_id"] == "F3"
    assert listen.json()["score"] == 100

    start_r = await client.post(
        f"/api/learn/reading/{F3_READING}/start", headers=headers
    )
    assert start_r.status_code == 200
    submit_r = await client.post(
        f"/api/learn/reading/{F3_READING}/submit",
        headers=headers,
        json={
            "attempt_id": start_r.json()["attempt_id"],
            "answers": READING_ANSWERS,
        },
    )
    assert submit_r.status_code == 200, submit_r.text
    assert submit_r.json()["reading_complete"] is False
    start_h = await client.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/start", headers=headers
    )
    hunt = await client.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/submit",
        headers=headers,
        json={
            "attempt_id": start_h.json()["attempt_id"],
            "found_words": HUNT_WORDS,
        },
    )
    assert hunt.status_code == 200, hunt.text
    assert hunt.json()["reading_complete"] is True

    sb = await client.post(
        f"/api/learn/writing/{F3_SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread."},
    )
    assert sb.status_code == 200
    assert sb.json()["writing_complete"] is False
    ef = await client.post(
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
    assert ef.status_code == 200
    gw = await client.post(
        f"/api/learn/writing/{F3_GW}/submit",
        headers=headers,
        json={
            "text": "I need to buy: 1. Two bags of rice 2. A loaf of bread 3. Some milk"
        },
    )
    assert gw.status_code == 200, gw.text
    assert gw.json()["writing_complete"] is True

    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.listening_complete is True
    assert progress.reading_complete is True
    assert progress.writing_complete is True
    assert progress.speaking_complete is False
    assert progress.unit_test_passed is False

    submitted = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED
            )
        )
    ).scalars().all()
    by_skill: dict[str, int] = {}
    for row in submitted:
        by_skill[row.skill] = by_skill.get(row.skill, 0) + 1
        assert row.score is not None
    assert by_skill["listening"] >= 1
    assert by_skill["reading"] >= 2
    assert by_skill["writing"] >= 3
    assert "speaking" not in by_skill

    levels = (
        await db_session.execute(select(UserSkillLevel))
    ).scalars().all()
    by_name = {row.skill: row for row in levels}
    for skill in ("listening", "reading", "writing"):
        assert by_name[skill].ema_score is not None
        assert by_name[skill].sonolo_level == 1
    assert "speaking" not in by_name or by_name["speaking"].sonolo_level == 1

    assert submit_r.json()["mastery"].get("mastery_available") is False
    assert hunt.json()["mastery"].get("mastery_available") is False
    assert gw.json()["mastery"].get("mastery_available") is False

    journey = await client.get("/api/learn/journey", headers=headers)
    assert journey.status_code == 200
    body = journey.json()
    assert body["current_unit_id"] == "F1"
    f3 = _f3_unit(body)
    assert f3["status"] == "locked"
    f4 = _f3_unit(body, "F4")
    assert f4["status"] == "locked"
    assert all(item["status"] == "locked" for item in f3["skills"])

    skills = await client.get("/api/progress/skills", headers=headers)
    assert skills.status_code == 200
    snapshot = skills.json()
    assert snapshot["display_level"] == 1
    assert snapshot["readiness_level"] == 1
    assert snapshot["imbalance"]["priority"] == "balanced"
    assert {item["skill"]: item["level"] for item in snapshot["skills"]} == {
        skill: 1 for skill in SKILLS
    }
    recomputed = build_skill_progress(
        {item["skill"]: item["level"] for item in snapshot["skills"]}
    )
    assert recomputed["display_level"] == snapshot["display_level"]
    assert recomputed["readiness_level"] == snapshot["readiness_level"]
    assert recomputed["imbalance"]["priority"] == snapshot["imbalance"]["priority"]

    user = (
        await db_session.execute(select(User).where(User.email == "c11@example.com"))
    ).scalar_one()
    assert user.current_level != "F3"
    assert user.sonolo_level == 1


@pytest.mark.asyncio
async def test_missing_f3_stages_have_no_product_surface(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "c11-gap@example.com")
    for path in (
        "/api/learn/unit-test/test-F3",
        "/api/learn/unit-test/test-F3/submit",
        "/api/learn/speaking/F3",
    ):
        missing = await client.get(path, headers=headers)
        assert missing.status_code in {404, 405, 422}
    grammar = await client.get("/api/learn/grammar/F3", headers=headers)
    assert grammar.status_code == 200
    primer = await client.get("/api/learn/vocab-primer/F3", headers=headers)
    assert primer.status_code == 200

    due = await client.get("/api/review/due", headers=headers)
    assert due.status_code == 200
    words = {card["word"].casefold() for card in due.json()}
    f3_targets = {word.casefold() for word in get_unit_document("F3")["vocabulary_targets"]}
    assert not f3_targets.issubset(words)

    gamification = await client.get("/api/gamification/me", headers=headers)
    assert gamification.status_code == 200
    assert "xp_total" in gamification.json()
    assert "current_streak" in gamification.json()
    assert "badges" in gamification.json()
