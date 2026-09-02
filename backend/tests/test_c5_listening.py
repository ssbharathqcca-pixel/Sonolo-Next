"""C5 listening dictation, unit_id, evidence. Gym SN-050 behavior preserved."""

from collections.abc import AsyncIterator
from statistics import mean

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import UserUnitProgress
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.services.content_service import load_listening_dialogues
from app.services.listening_service import (
    score_dictation_segment,
    score_multiple_choice,
    score_sequence,
    score_true_false,
)

pytestmark = pytest.mark.asyncio

F3_LISTEN = "listen-F3-superstore"
DICT_1 = "Rice is in aisle 4, next to the dairy."
DICT_2 = "Yes, it's on sale this week."


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


async def _auth(client: AsyncClient, email: str) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C5",
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


def test_mc_tf_sequence_contracts() -> None:
    assert score_multiple_choice(1, 1) == 100
    assert score_multiple_choice(1, 0) == 0
    assert score_true_false(0, 0) == 100
    assert score_true_false(0, 1) == 0
    assert score_sequence([0, 1, 2], [0, 1, 2]) == 100
    assert score_sequence([0, 1, 2], [1, 0, 2]) == 50
    assert score_sequence([0, 1, 2], [2, 0, 1]) == 0


def test_dictation_exact_and_normalization() -> None:
    assert score_dictation_segment(DICT_1, DICT_1) == 100
    assert score_dictation_segment(DICT_1, "rice is in aisle 4, next to the dairy!") == 100


def test_dictation_fuzzy_minor_spelling() -> None:
    # "dairy" vs "dairyy" is 1 edit, length >= 4
    score = score_dictation_segment(DICT_1, "Rice is in aisle 4, next to the dairyy.")
    assert score == 100
    # short word "in" misspelled as "inn" is not credited
    score_short = score_dictation_segment("in the aisle", "inn the aisle")
    assert score_short < 100


def test_dictation_partial_missing_and_wrong() -> None:
    expected = "rice is in aisle four now"
    # 6 words; first three exact → 50
    partial = score_dictation_segment(expected, "rice is in")
    assert partial == pytest.approx(50.0)
    wrong = score_dictation_segment(expected, "I want pizza please thanks bye now extra")
    assert wrong == 0
    empty = score_dictation_segment(expected, "")
    assert empty == 0
    assert score_dictation_segment("", "anything") == 0


def test_multiple_dictation_segments_mean() -> None:
    s1 = score_dictation_segment(DICT_1, DICT_1)
    s2 = score_dictation_segment(DICT_2, "completely different words here")
    assert mean([s1, s2]) == pytest.approx(50.0)


async def test_gym_endpoints_still_work(client: AsyncClient) -> None:
    headers = await _auth(client, "gym@example.com")
    listed = await client.get("/api/listening/dialogues", headers=headers)
    assert listed.status_code == 200
    gym = [
        d
        for d in listed.json()["dialogues"]
        if not d.get("unit_id")
    ]
    assert len(gym) == 12
    coffee = await client.get(
        "/api/listening/dialogues/listen-coffee-morning-rush", headers=headers
    )
    assert coffee.status_code == 200
    assert coffee.json()["transcript"] is None
    assert coffee.json()["questions"][0]["correct_index"] in {0, 1, 2, 3}
    evaluate = await client.post(
        "/api/listening/dialogues/listen-coffee-morning-rush/evaluate",
        headers=headers,
        json={"answers": [1, 1, 1], "time_seconds": 20},
    )
    assert evaluate.status_code == 200
    assert evaluate.json()["engine_version"] == "sn050-mock-listening-v1"


async def test_f3_listening_loads_and_hides_dictation_transcript(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "f3l@example.com")
    dialogues = load_listening_dialogues()
    f3 = next(item for item in dialogues if item.id == F3_LISTEN)
    assert f3.unit_id == "F3"
    assert f3.sonolo_level == 2
    assert len(f3.turns) == 7
    assert f3.dictation_segments is not None
    assert len(f3.dictation_segments) == 2
    assert f3.dictation_segments[0].text == DICT_1
    assert f3.dictation_segments[1].text == DICT_2
    unit_vocab = {
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
    }
    text = " ".join(turn.text.casefold() for turn in f3.turns)
    hits = {word for word in unit_vocab if word.casefold() in text}
    assert len(hits) >= 12

    detail = await client.get(f"/api/listening/dialogues/{F3_LISTEN}", headers=headers)
    assert detail.status_code == 200
    body = detail.json()
    assert body["unit_id"] == "F3"
    assert body["transcript"] is None
    assert body["transcript_available"] is False
    assert len(body["dictation_prompts"]) == 2
    assert "text" not in body["dictation_prompts"][0]
    assert DICT_1 not in str(body["dictation_prompts"])


async def test_f3_evaluate_dictation_and_transcript_after_submit(
    client: AsyncClient,
) -> None:
    headers = await _auth(client, "dict@example.com")
    before = await client.get(f"/api/listening/dialogues/{F3_LISTEN}", headers=headers)
    assert before.json()["transcript"] is None
    response = await client.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json={
            "answers": [1, 2, 1],
            "time_seconds": 40,
            "dictation": [DICT_1, DICT_2],
            "full_replays": 2,
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["score"] == 100
    assert body["dictation_scores"] == [100.0, 100.0]
    assert body["transcript"] == [DICT_1, DICT_2]
    assert body["full_replays"] == 2
    replay = await client.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json={
            "answers": [1, 2, 1],
            "time_seconds": 40,
            "dictation": [DICT_1, DICT_2],
            "full_replays": 3,
        },
    )
    assert replay.json()["score"] == 100
    assert replay.json()["full_replays"] == 3


async def test_replay_does_not_change_mc_score(client: AsyncClient) -> None:
    headers = await _auth(client, "replay@example.com")
    body = {"answers": [1, 1, 1], "time_seconds": 15, "full_replays": 0}
    first = await client.post(
        "/api/listening/dialogues/listen-coffee-morning-rush/evaluate",
        headers=headers,
        json=body,
    )
    body["full_replays"] = 3
    second = await client.post(
        "/api/listening/dialogues/listen-coffee-morning-rush/evaluate",
        headers=headers,
        json=body,
    )
    assert first.json()["score"] == second.json()["score"]
    assert first.json()["correct_count"] == second.json()["correct_count"]


async def test_listening_evidence_and_duplicate(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "evl@example.com")
    payload = {
        "answers": [1, 2, 1],
        "time_seconds": 30,
        "dictation": [DICT_1, DICT_2],
    }
    first = await client.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json=payload,
    )
    assert first.status_code == 200
    second = await client.post(
        f"/api/listening/dialogues/{F3_LISTEN}/evaluate",
        headers=headers,
        json=payload,
    )
    assert second.status_code == 200
    count = (
        await db_session.execute(
            select(func.count()).select_from(SkillExerciseAttempt).where(
                SkillExerciseAttempt.content_id == F3_LISTEN,
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
                SkillExerciseAttempt.skill == "listening",
            )
        )
    ).scalar_one()
    assert int(count) == 1
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.listening_complete is True
