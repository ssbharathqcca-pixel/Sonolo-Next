"""C3 reading engine + vocabulary hunt activity."""

from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.time import utc_now
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import ReadingExercise, UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.services.content_service import content_reading_id, persist_curriculum
from app.services.reading_service import score_question, score_vocabulary_hunt

pytestmark = pytest.mark.asyncio

F3_READING = "reading-F3-grocery-flyer"
F3_HUNT = "hunt-F3-grocery-flyer"
CORRECT = {
    "reading-F3-grocery-flyer-q1": 0,
    "reading-F3-grocery-flyer-q2": 1,
    "reading-F3-grocery-flyer-q3": "4",
}


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


async def _auth(client: AsyncClient, email: str = "c3@example.com") -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C3",
            "password": "maple-syrup-99",
            "native_language": "en",
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


async def _start_submit(
    client: AsyncClient,
    headers: dict[str, str],
    answers: dict,
    content_id: str = F3_READING,
) -> tuple[object, object]:
    start = await client.post(f"/api/learn/reading/{content_id}/start", headers=headers)
    assert start.status_code == 200, start.text
    submit = await client.post(
        f"/api/learn/reading/{content_id}/submit",
        headers=headers,
        json={"attempt_id": start.json()["attempt_id"], "answers": answers},
    )
    return start, submit


def test_mc_tf_fill_blank_scoring_contract() -> None:
    mc = {
        "id": "q1",
        "type": "multiple_choice",
        "correct_answer": 0,
        "options": ["a", "b", "c", "d"],
    }
    tf = {
        "id": "q2",
        "type": "true_false",
        "correct_answer": 1,
        "options": ["True", "False"],
    }
    fill = {
        "id": "q3",
        "type": "fill_blank",
        "correct_answer": "4",
        "accepted_answers": ["aisle 4"],
    }
    assert score_question(mc, 0) == 100
    assert score_question(mc, 1) == 0
    assert score_question(tf, 1) == 100
    assert score_question(tf, 0) == 0
    assert score_question(fill, "4") == 100
    assert score_question(fill, "Aisle 4") == 80
    assert score_question(fill, "9") == 0


def test_short_answer_deterministic_keywords() -> None:
    question = {
        "id": "q",
        "type": "short_answer",
        "correct_answer": "The apples are in aisle 4.",
        "keywords": ["apples", "aisle"],
        "accepted_answers": ["apples in aisle four"],
        "sonolo_level": 2,
    }
    assert score_question(question, "The apples are in aisle 4!") == 100
    assert score_question(question, "I found the apples in the aisle") == 100
    assert score_question(question, "apples in aisle four") == 80
    assert score_question(question, "bananas") == 0


def test_hunt_normalized_score() -> None:
    score, words = score_vocabulary_hunt(
        ["aisle", "fresh", "loaf", "milk", "savings"],
        ["Aisle", "fresh", "loaf", "milk", "aisle", "unknown"],
    )
    assert score == 80.0
    found = {item["word"]: item["found"] for item in words}
    assert found["savings"] is False
    assert found["aisle"] is True


async def test_get_reading_hides_answers_and_requires_auth(
    client: AsyncClient,
) -> None:
    denied = await client.get(f"/api/learn/reading/{F3_READING}")
    assert denied.status_code == 401
    headers = await _auth(client)
    response = await client.get(f"/api/learn/reading/{F3_READING}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == F3_READING
    assert body["level"] == 2
    assert body["time_limit_minutes"] is None
    assert "correct_answer" not in body["questions"][0]
    assert body["questions"][0]["options"] is not None


async def test_correct_submission_scores_100(client: AsyncClient) -> None:
    headers = await _auth(client)
    _, submit = await _start_submit(client, headers, CORRECT)
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["score"] == 100
    assert body["activity_complete"] is True
    assert body["reading_complete"] is False
    assert "hunt-F3-grocery-flyer" in body["required_remaining"]


async def test_wrong_answers_score_0(client: AsyncClient) -> None:
    headers = await _auth(client, "wrong@example.com")
    _, submit = await _start_submit(
        client,
        headers,
        {
            "reading-F3-grocery-flyer-q1": 1,
            "reading-F3-grocery-flyer-q2": 0,
            "reading-F3-grocery-flyer-q3": "99",
        },
    )
    assert submit.status_code == 200
    assert submit.json()["score"] == 0


async def test_fill_blank_accepted_answer_partial(client: AsyncClient) -> None:
    headers = await _auth(client, "partial@example.com")
    answers = dict(CORRECT)
    answers["reading-F3-grocery-flyer-q3"] = "Aisle 4"
    _, submit = await _start_submit(client, headers, answers)
    assert submit.status_code == 200
    scores = {item["id"]: item["score"] for item in submit.json()["question_scores"]}
    assert scores["reading-F3-grocery-flyer-q3"] == 80
    assert submit.json()["score"] == pytest.approx(280 / 3)


async def test_duplicate_submit_does_not_duplicate_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "dup@example.com")
    start, first = await _start_submit(client, headers, CORRECT)
    assert first.status_code == 200
    second = await client.post(
        f"/api/learn/reading/{F3_READING}/submit",
        headers=headers,
        json={"attempt_id": start.json()["attempt_id"], "answers": CORRECT},
    )
    assert second.status_code == 200
    assert second.json()["idempotent_replayed"] is True
    count = (
        await db_session.execute(
            select(func.count()).select_from(SkillExerciseAttempt).where(
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED
            )
        )
    ).scalar_one()
    assert int(count) == 1


async def test_retry_creates_new_attempt(client: AsyncClient) -> None:
    headers = await _auth(client, "retry@example.com")
    start1, first = await _start_submit(client, headers, CORRECT)
    assert first.status_code == 200
    start2 = await client.post(
        f"/api/learn/reading/{F3_READING}/start", headers=headers
    )
    assert start2.status_code == 200
    assert start2.json()["attempt_id"] != start1.json()["attempt_id"]


async def test_duplicate_start_reuses_active_attempt(client: AsyncClient) -> None:
    headers = await _auth(client, "start2@example.com")
    first = await client.post(
        f"/api/learn/reading/{F3_READING}/start", headers=headers
    )
    second = await client.post(
        f"/api/learn/reading/{F3_READING}/start", headers=headers
    )
    assert first.json()["attempt_id"] == second.json()["attempt_id"]
    assert second.json()["reused"] is True


async def test_unpublished_reading_is_hidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await persist_curriculum(db_session)
    db_session.add(
        ReadingExercise(
            id=content_reading_id("reading-hidden"),
            content_id="reading-hidden",
            title="Hidden",
            language="en-CA",
            text_content="x " * 60,
            questions=[],
            vocabulary_targets=[],
            grammar_targets=[],
            is_published=False,
            sonolo_level=2,
        )
    )
    await db_session.commit()
    headers = await _auth(client, "hidden@example.com")
    response = await client.get("/api/learn/reading/reading-hidden", headers=headers)
    assert response.status_code == 404


async def test_l4_late_submission_rejected(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await persist_curriculum(db_session)
    db_session.add(
        ReadingExercise(
            id=content_reading_id("reading-L4-timer"),
            content_id="reading-L4-timer",
            title="Timed",
            language="en-CA",
            text_content="word " * 120,
            questions=[
                {
                    "id": "q1",
                    "type": "multiple_choice",
                    "question": "Q?",
                    "options": ["a", "b", "c", "d"],
                    "correct_answer": 0,
                    "skill_tested": "literal",
                }
            ],
            vocabulary_targets=[],
            grammar_targets=[],
            is_published=True,
            sonolo_level=4,
        )
    )
    await db_session.commit()
    headers = await _auth(client, "late@example.com")
    start = await client.post(
        "/api/learn/reading/reading-L4-timer/start", headers=headers
    )
    assert start.status_code == 200
    attempt_id = UUID(start.json()["attempt_id"])
    attempt = await db_session.get(SkillExerciseAttempt, attempt_id)
    assert attempt is not None
    attempt.started_at = utc_now() - timedelta(minutes=11)
    await db_session.commit()
    submit = await client.post(
        "/api/learn/reading/reading-L4-timer/submit",
        headers=headers,
        json={"attempt_id": str(attempt_id), "answers": {"q1": 0}},
    )
    assert submit.status_code == 409
    await db_session.refresh(attempt)
    assert attempt.score is None
    assert attempt.status == "rejected_late"


async def test_hunt_completes_f3_reading_block(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "hunt@example.com")
    _, reading = await _start_submit(client, headers, CORRECT)
    assert reading.json()["reading_complete"] is False
    start = await client.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/start", headers=headers
    )
    assert start.status_code == 200
    get_hunt = await client.get(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}", headers=headers
    )
    assert get_hunt.status_code == 200
    assert get_hunt.json()["target_word_count"] == 5
    submit = await client.post(
        f"/api/learn/vocabulary-hunt/{F3_HUNT}/submit",
        headers=headers,
        json={
            "attempt_id": start.json()["attempt_id"],
            "found_words": ["aisle", "fresh", "loaf", "milk", "savings"],
        },
    )
    assert submit.status_code == 200, submit.text
    body = submit.json()
    assert body["score"] == 100
    assert body["reading_complete"] is True
    assert body["required_remaining"] == []
    progress = (
        await db_session.execute(select(UserUnitProgress))
    ).scalar_one()
    assert progress.reading_complete is True
