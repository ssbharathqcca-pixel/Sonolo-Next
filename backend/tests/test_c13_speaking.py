"""C13 F3 speaking content + production speaking_complete writer.

Does not change C2 formulas, C8 graph, C10 gate, or C12 scoring.
"""

from __future__ import annotations

import inspect
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
from app.learning.evaluator import SessionEvaluator
from app.main import create_app
from app.models.curriculum import Unit, UserSkillLevel, UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.models.scenario import Scenario
from app.models.session import SpeakingSession
from app.services.content_service import (
    content_scenario_id,
    content_unit_id,
    get_pronunciation_drill,
    get_scenario_seed_by_content_id,
    get_unit_document,
    gym_pronunciation_drills,
    persist_curriculum,
    validate_curriculum_content,
)
from app.services.evidence_service import (
    ACTIVITY_PRONUNCIATION_DRILL,
    ACTIVITY_SPEAKING_SESSION,
    record_speaking_practice,
    refresh_speaking_complete,
)
from app.services.mastery_service import (
    SKILLS,
    UNITS_FOR_LEVEL,
    compute_mastery_score,
    display_level,
    readiness_level,
)
from app.services.quest_service import QUEST_DEFINITIONS
from app.services.session_service import SessionService
from app.services.unit_test_service import SECTION_WEIGHTS

F3_LISTEN = "listen-F3-superstore"
F3_READING = "reading-F3-grocery-flyer"
F3_HUNT = "hunt-F3-grocery-flyer"
F3_SB = "writing-F3-sentence-builder"
F3_GW = "writing-F3-shopping-list"
F3_EF = "writing-F3-error-fix"
F3_CLERK = "speak-F3-clerk"
F3_SPRINT = "speak-F3-sprint"
F3_PRON = "pron-F3-grocery"
F3_SPEAKING = [F3_CLERK, F3_SPRINT, F3_PRON]
FIXED_NOW = datetime(2026, 9, 2, 18, 0, tzinfo=UTC)
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
            "name": "C13",
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


def _session_payload(
    scenario_id: str,
    *,
    duration: int = 60,
    overall: float = 82.0,
    with_user_turn: bool = True,
    text: str = "Where is the rice? Thank you.",
    client_session_id: str | None = None,
) -> dict:
    started = FIXED_NOW - timedelta(seconds=duration)
    transcript = []
    if with_user_turn:
        transcript.append({"role": "user", "text": text})
    transcript.append({"role": "assistant", "text": "Aisle 4, next to the dairy."})
    return {
        "client_session_id": client_session_id or str(uuid4()),
        "scenario_id": scenario_id,
        "started_at": started.isoformat(),
        "ended_at": FIXED_NOW.isoformat(),
        "duration_seconds": duration,
        "transcript": transcript,
        "evaluation": {
            "scores": {
                "fluency": overall,
                "pronunciation": overall,
                "grammar": overall,
                "vocabulary": overall,
                "coherence": overall,
                "task_completion": overall,
            },
            "overall_score": overall,
            "insights": [],
        },
    }


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


async def _complete_speaking(http: AsyncClient, headers: dict[str, str]) -> None:
    clerk = str(content_scenario_id(F3_CLERK))
    sprint = str(content_scenario_id(F3_SPRINT))
    await http.post(
        f"/api/pronunciation/drills/{F3_PRON}/evaluate",
        headers=headers,
        json={"duration_seconds": 8},
    )
    await http.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(
            clerk,
            duration=90,
            text=(
                "Excuse me, where is the rice? Is it in aisle 4 next to the dairy? "
                "Thank you so much."
            ),
        ),
    )
    await http.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(
            sprint,
            duration=60,
            text=(
                "My grocery list is a loaf of bread, rice, and milk. I put them in "
                "the cart, check the flyer, then tap Presto for the bus home."
            ),
        ),
    )


def test_c2_untouched_and_no_formula_copy() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0
    assert display_level({"speaking": 5, "listening": 4, "reading": 6, "writing": 3}) == 4
    assert readiness_level({"speaking": 5, "listening": 4, "reading": 6, "writing": 3}) == 3
    assert UNITS_FOR_LEVEL[1] == ("F1", "F2")
    assert SECTION_WEIGHTS["speaking"] == 0.30
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }
    source = inspect.getsource(record_speaking_practice) + inspect.getsource(
        refresh_speaking_complete
    )
    assert "0.40" not in source
    assert "0.35" not in source
    assert "compute_mastery_score" not in inspect.getsource(refresh_speaking_complete)
    assert "display_level" not in source
    session_src = inspect.getsource(SessionService)
    assert "compute_mastery_score" not in session_src


def test_f3_speaking_content_published_and_bound() -> None:
    assert validate_curriculum_content() == []
    document = get_unit_document("F3")
    assert document is not None
    assert document["speaking_ids"] == F3_SPEAKING
    assert document["language"] == "en-CA"
    assert document["level_target"] == 2
    clerk = get_scenario_seed_by_content_id(F3_CLERK)
    sprint = get_scenario_seed_by_content_id(F3_SPRINT)
    drill = get_pronunciation_drill(F3_PRON)
    assert clerk is not None and clerk.is_published and clerk.unit_id == "F3"
    assert clerk.expected_turns == 5
    assert clerk.sonolo_level == 2
    assert "rice" in " ".join(clerk.vocabulary_targets).casefold()
    assert sprint is not None and sprint.is_published and sprint.unit_id == "F3"
    assert "60" in f"{sprint.description} {sprint.opening_line}"
    assert drill is not None and drill.is_published and drill.unit_id == "F3"
    words = {word.casefold() for word in drill.target_words}
    assert {"grocery", "receipt", "aisle"} <= words
    gym_ids = {item.id for item in gym_pronunciation_drills()}
    assert F3_PRON not in gym_ids
    assert len(gym_pronunciation_drills()) == 12


@pytest.mark.asyncio
async def test_speaking_requires_auth(client: AsyncClient) -> None:
    assert (await client.get("/api/learn/units/F3")).status_code == 401
    assert (
        await client.post(
            f"/api/pronunciation/drills/{F3_PRON}/evaluate",
            json={"duration_seconds": 3},
        )
    ).status_code == 401
    assert (
        await client.post("/api/sessions/complete", json=_session_payload(str(uuid4())))
    ).status_code == 401


@pytest.mark.asyncio
async def test_f3_speaking_retrieval_and_published_filter(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "retrieve@example.com")
    await persist_curriculum(db_session)
    catalog = await client.get("/api/learn/units/F3", headers=headers)
    assert catalog.status_code == 200
    assert catalog.json()["speaking_ids"] == F3_SPEAKING
    drills = await client.get("/api/pronunciation/drills", headers=headers)
    assert drills.status_code == 200
    gym_ids = {item["id"] for item in drills.json()["drills"]}
    assert F3_PRON not in gym_ids
    assert len(drills.json()["drills"]) == 12
    detail = await client.get(f"/api/pronunciation/drills/{F3_PRON}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["id"] == F3_PRON
    assert "aisle" in [word.casefold() for word in detail.json()["target_words"]]
    gym = await client.get("/api/scenarios", headers=headers)
    assert gym.status_code == 200
    gym_titles = {item["title"] for item in gym.json()["scenarios"]}
    assert "Ask a grocery clerk for help" not in gym_titles
    clerk = (
        await db_session.execute(
            select(Scenario).where(Scenario.id == content_scenario_id(F3_CLERK))
        )
    ).scalar_one()
    assert clerk.is_published is True
    assert clerk.unit_id == content_unit_id("F3", "en-CA")


@pytest.mark.asyncio
async def test_pronunciation_clerk_and_sprint_lifecycle(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    headers = await _auth(client, "flow@example.com")
    await persist_curriculum(db_session)
    captured: list = []
    original = SessionEvaluator.evaluate

    async def spy(self, request):
        captured.append(request)
        return await original(self, request)

    monkeypatch.setattr(SessionEvaluator, "evaluate", spy)

    pron = await client.post(
        f"/api/pronunciation/drills/{F3_PRON}/evaluate",
        headers=headers,
        json={"duration_seconds": 8},
    )
    assert pron.status_code == 200, pron.text
    assert pron.json()["engine_version"] == "sn049-mock-phoneme-v1"
    assert 0 <= pron.json()["overall"] <= 100

    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.speaking_complete is False

    clerk_id = str(content_scenario_id(F3_CLERK))
    clerk = await client.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(
            clerk_id,
            duration=90,
            text="Where is the rice aisle please? Thank you.",
        ),
    )
    assert clerk.status_code == 200, clerk.text
    assert clerk.json()["xp_eligible"] is True
    assert captured
    vocab = [word.casefold() for word in (captured[0].scenario_targets.vocabulary if captured[0].scenario_targets else [])]
    assert "aisle" in vocab or "rice" in vocab or "grocery" in vocab

    sprint = await client.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(
            str(content_scenario_id(F3_SPRINT)),
            duration=60,
            text="I need a loaf, rice, and milk in my cart, then Presto for the bus.",
        ),
    )
    assert sprint.status_code == 200, sprint.text

    await db_session.refresh(progress)
    assert progress.speaking_complete is True
    attempts = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
                SkillExerciseAttempt.skill == "speaking",
            )
        )
    ).scalars().all()
    by_content = {row.content_id: row for row in attempts}
    assert set(by_content) >= set(F3_SPEAKING)
    assert by_content[F3_PRON].activity_type == ACTIVITY_PRONUNCIATION_DRILL
    assert by_content[F3_CLERK].activity_type == ACTIVITY_SPEAKING_SESSION
    assert by_content[F3_SPRINT].activity_type == ACTIVITY_SPEAKING_SESSION
    for row in by_content.values():
        assert row.score is not None
        assert 0 <= float(row.score) <= 100
    stored = (await db_session.execute(select(SpeakingSession))).scalars().all()
    assert len(stored) == 2
    ema = (
        await db_session.execute(
            select(UserSkillLevel).where(UserSkillLevel.skill == "speaking")
        )
    ).scalar_one()
    assert ema.ema_score is not None


@pytest.mark.asyncio
async def test_incomplete_does_not_complete_and_replay_is_idempotent(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "incomplete@example.com")
    await persist_curriculum(db_session)
    clerk_id = str(content_scenario_id(F3_CLERK))
    short = await client.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(clerk_id, duration=10, overall=90.0),
    )
    assert short.status_code == 200
    assert short.json()["xp_eligible"] is False
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one_or_none()
    assert progress is None or progress.speaking_complete is False
    speaking_rows = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(SkillExerciseAttempt.skill == "speaking")
        )
    ).scalars().all()
    assert speaking_rows == []

    await persist_curriculum(db_session)
    await _complete_speaking(client, headers)
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.speaking_complete is True
    first_count = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.skill == "speaking",
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
            )
        )
    ).scalars().all()
    replay_pron = await client.post(
        f"/api/pronunciation/drills/{F3_PRON}/evaluate",
        headers=headers,
        json={"duration_seconds": 8},
    )
    assert replay_pron.status_code == 200
    await db_session.refresh(progress)
    assert progress.speaking_complete is True
    after = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(
                SkillExerciseAttempt.skill == "speaking",
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
            )
        )
    ).scalars().all()
    pron_rows = [row for row in after if row.content_id == F3_PRON]
    assert len(pron_rows) == len([row for row in first_count if row.content_id == F3_PRON])


@pytest.mark.asyncio
async def test_legacy_gym_session_does_not_complete_f3(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "gym@example.com")
    gym = Scenario(
        title="Order at the coffee shop",
        category="shopping",
        mode="casual",
        level="seed",
        difficulty=3,
        expected_turns=6,
        is_published=True,
    )
    db_session.add(gym)
    await db_session.flush()
    response = await client.post(
        "/api/sessions/complete",
        headers=headers,
        json=_session_payload(str(gym.id), duration=300),
    )
    assert response.status_code == 200
    assert response.json()["xp_eligible"] is True
    attempts = (
        await db_session.execute(
            select(SkillExerciseAttempt).where(SkillExerciseAttempt.skill == "speaking")
        )
    ).scalars().all()
    assert attempts == []
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one_or_none()
    assert progress is None or progress.speaking_complete is False


@pytest.mark.asyncio
async def test_four_skill_flags_reach_c12_without_f1_f2_bypass(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "gate@example.com")
    await persist_curriculum(db_session)
    await _complete_lrw(client, headers)
    blocked = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert blocked.status_code == 409
    await _complete_speaking(client, headers)
    progress = (
        await db_session.execute(
            select(UserUnitProgress).join(Unit).where(Unit.unit_code == "F3")
        )
    ).scalar_one()
    assert progress.listening_complete is True
    assert progress.reading_complete is True
    assert progress.writing_complete is True
    assert progress.speaking_complete is True
    reachable = await client.post(
        "/api/learn/unit-test/F3/submit", headers=headers, json=PASSING
    )
    assert reachable.status_code == 200, reachable.text
    assert reachable.json()["idempotent_replayed"] is False
    journey = await client.get("/api/learn/journey", headers=headers)
    payload = journey.json()
    units = {
        unit["id"]: unit
        for band in payload["bands"]
        for unit in band["units"]
    }
    assert units["F3"]["status"] == "locked"
    assert payload["current_unit_id"] == "F1"
    assert UNITS_FOR_LEVEL[1] == ("F1", "F2")
