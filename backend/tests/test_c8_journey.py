"""C8 Journey Map API. Does not change C2, XP, or skill engines."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import Unit, UserUnitProgress
from app.models.user import User
from app.services.content_service import content_unit_id
from app.services.journey_service import (
    UNIT_SEQUENCE,
    UNIT_TITLES,
    UnitProgressSnapshot,
    build_journey,
    unit_title,
)
from app.services.mastery_service import BANDS, SKILLS, compute_mastery_score
from app.services.quest_service import QUEST_DEFINITIONS


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


async def _auth(
    client: AsyncClient, email: str = "c8@example.com"
) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C8",
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


async def _user(db: AsyncSession, email: str = "c8@example.com") -> User:
    result = await db.execute(select(User).where(User.email == email))
    return result.scalar_one()


def _band_units(band: str) -> tuple[str, ...]:
    units = BANDS[band]["units"]
    assert isinstance(units, tuple)
    return tuple(str(code) for code in units)


async def _ensure_unit(db: AsyncSession, code: str) -> Unit:
    language = "en-CA"
    unit_id = content_unit_id(code, language)
    existing = await db.get(Unit, unit_id)
    if existing is not None:
        return existing
    if code.startswith("F"):
        band = "foundation"
        level = 1
    elif code.startswith("M"):
        band = "middle"
        level = 4
    else:
        band = "advanced"
        level = 7
    unit = Unit(
        id=unit_id,
        unit_code=code,
        band=band,
        title=UNIT_TITLES[code],
        story_chapter="",
        theme="",
        icon="",
        level_target=level,
        sort_order=UNIT_SEQUENCE.index(code) + 1,
        language=language,
        vocabulary_targets=[],
        grammar_targets=[],
        prerequisites=[],
        is_published=False,
    )
    db.add(unit)
    await db.flush()
    return unit


async def _set_progress(
    db: AsyncSession,
    user_id,
    code: str,
    *,
    unit_test_passed: bool = False,
    speaking: bool = False,
    listening: bool = False,
    reading: bool = False,
    writing: bool = False,
) -> None:
    unit = await _ensure_unit(db, code)
    row = UserUnitProgress(
        user_id=user_id,
        unit_id=unit.id,
        speaking_complete=speaking,
        listening_complete=listening,
        reading_complete=reading,
        writing_complete=writing,
        unit_test_passed=unit_test_passed,
    )
    db.add(row)
    await db.flush()


def _band(payload: dict, band_id: str) -> dict:
    return next(item for item in payload["bands"] if item["id"] == band_id)


def _unit(payload: dict, code: str) -> dict:
    for band in payload["bands"]:
        for unit in band["units"]:
            if unit["id"] == code:
                return unit
    raise AssertionError(f"unit {code} missing")


def test_c2_formula_untouched() -> None:
    assert compute_mastery_score([80], [80], 80.0) == 80.0


def test_quest_xp_untouched() -> None:
    assert {item.code: item.reward_xp for item in QUEST_DEFINITIONS} == {
        "session_1": 20,
        "session_2": 30,
        "vocab_10": 20,
    }


def test_skeleton_has_three_bands_and_eighteen_units() -> None:
    payload = build_journey({})
    assert [band["id"] for band in payload["bands"]] == [
        "advanced",
        "middle",
        "foundation",
    ]
    assert len(UNIT_SEQUENCE) == 18
    for band in payload["bands"]:
        assert len(band["units"]) == 6


def test_fresh_progress_unlocks_only_f1() -> None:
    payload = build_journey({})
    assert payload["current_unit_id"] == "F1"
    foundation = _band(payload, "foundation")
    middle = _band(payload, "middle")
    advanced = _band(payload, "advanced")
    assert foundation["status"] == "active"
    assert foundation["expanded"] is True
    assert middle["status"] == "locked"
    assert middle["expanded"] is False
    assert middle["unlock_condition"] == "Complete Foundation Band to unlock"
    assert advanced["status"] == "locked"
    assert advanced["expanded"] is False
    assert _unit(payload, "F1")["status"] == "current"
    assert _unit(payload, "F2")["status"] == "locked"
    assert _unit(payload, "F3")["status"] == "locked"
    assert _unit(payload, "M1")["status"] == "locked"


def test_published_content_does_not_complete_a_unit() -> None:
    assert unit_title("F3") == "First Week"
    payload = build_journey({})
    assert _unit(payload, "F3")["status"] == "locked"
    assert _unit(payload, "F3")["title"] == "First Week"


def test_partial_curriculum_f1_passed() -> None:
    payload = build_journey(
        {
            "F1": UnitProgressSnapshot(
                speaking_complete=True,
                listening_complete=True,
                reading_complete=True,
                writing_complete=True,
                unit_test_passed=True,
            )
        }
    )
    assert payload["current_unit_id"] == "F2"
    assert _unit(payload, "F1")["status"] == "completed"
    assert _unit(payload, "F2")["status"] == "current"
    assert _band(payload, "foundation")["status"] == "active"
    assert _band(payload, "middle")["status"] == "locked"


def test_foundation_complete_unlocks_middle() -> None:
    progress = {
        code: UnitProgressSnapshot(unit_test_passed=True)
        for code in _band_units("foundation")
    }
    payload = build_journey(progress)
    assert payload["current_unit_id"] == "M1"
    assert _band(payload, "foundation")["status"] == "completed"
    assert _band(payload, "foundation")["expanded"] is False
    assert _band(payload, "middle")["status"] == "active"
    assert _band(payload, "middle")["expanded"] is True
    assert _band(payload, "advanced")["status"] == "locked"
    assert _unit(payload, "M1")["status"] == "current"


def test_fully_complete_collapses_every_band() -> None:
    progress = {
        code: UnitProgressSnapshot(unit_test_passed=True) for code in UNIT_SEQUENCE
    }
    payload = build_journey(progress)
    assert payload["current_unit_id"] is None
    for band in payload["bands"]:
        assert band["status"] == "completed"
        assert band["expanded"] is False
        assert all(unit["status"] == "completed" for unit in band["units"])


def test_skill_icons_on_current_and_locked_units() -> None:
    payload = build_journey(
        {
            "F1": UnitProgressSnapshot(
                speaking_complete=True,
                listening_complete=False,
                reading_complete=False,
                writing_complete=False,
            )
        }
    )
    f1_skills = {item["skill"]: item["status"] for item in _unit(payload, "F1")["skills"]}
    assert list(f1_skills) == list(SKILLS)
    assert f1_skills["speaking"] == "complete"
    assert f1_skills["listening"] == "in_progress"
    assert f1_skills["reading"] == "in_progress"
    assert f1_skills["writing"] == "in_progress"
    f2_skills = {item["skill"]: item["status"] for item in _unit(payload, "F2")["skills"]}
    assert set(f2_skills.values()) == {"locked"}


@pytest.mark.asyncio
async def test_journey_requires_auth(client: AsyncClient) -> None:
    response = await client.get("/api/learn/journey")
    assert response.status_code == 401


@pytest.mark.asyncio
async def test_fresh_user_api_matches_skeleton(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client)
    user = await _user(db_session)
    user.current_level = "summit"
    user.sonolo_level = 9
    await db_session.flush()
    response = await client.get("/api/learn/journey", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["current_unit_id"] == "F1"
    assert [band["id"] for band in payload["bands"]] == [
        "advanced",
        "middle",
        "foundation",
    ]
    foundation = _band(payload, "foundation")
    assert foundation["status"] == "active"
    assert foundation["expanded"] is True
    assert _band(payload, "advanced")["status"] == "locked"
    assert _unit(payload, "F3")["title"] == "First Week"
    assert _unit(payload, "F3")["status"] == "locked"


@pytest.mark.asyncio
async def test_api_partial_and_complete_progress(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client)
    user = await _user(db_session)
    await _set_progress(
        db_session,
        user.id,
        "F1",
        unit_test_passed=True,
        speaking=True,
        listening=True,
        reading=True,
        writing=True,
    )
    response = await client.get("/api/learn/journey", headers=headers)
    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["current_unit_id"] == "F2"
    assert _unit(payload, "F1")["status"] == "completed"
    assert _band(payload, "foundation")["status"] == "active"

    for code in _band_units("foundation")[1:]:
        await _set_progress(db_session, user.id, code, unit_test_passed=True)
    response = await client.get("/api/learn/journey", headers=headers)
    payload = response.json()
    assert payload["current_unit_id"] == "M1"
    assert _band(payload, "foundation")["status"] == "completed"
    assert _band(payload, "foundation")["expanded"] is False
    assert _band(payload, "middle")["status"] == "active"
    assert _band(payload, "advanced")["status"] == "locked"

    for code in _band_units("middle") + _band_units("advanced"):
        await _set_progress(db_session, user.id, code, unit_test_passed=True)
    response = await client.get("/api/learn/journey", headers=headers)
    payload = response.json()
    assert payload["current_unit_id"] is None
    assert all(band["status"] == "completed" for band in payload["bands"])
    assert all(band["expanded"] is False for band in payload["bands"])
