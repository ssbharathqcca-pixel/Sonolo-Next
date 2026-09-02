"""C4 writing engine tests. LLM is mocked; no live provider calls."""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.learn import get_llm_provider
from app.core.config import Settings
from app.db.session import get_db
from app.main import create_app
from app.models.curriculum import UserUnitProgress, WritingExercise
from app.models.evidence import ATTEMPT_SUBMITTED, SkillExerciseAttempt
from app.services.content_service import content_writing_id, persist_curriculum
from app.services.evidence_service import last_exercise_scores
from app.services.writing_service import (
    MAX_CORRECTIONS,
    MAX_REVISIONS,
    score_error_fix,
    score_sentence_builder,
    weighted_writing_score,
    writing_eval_system_prompt,
)

pytestmark = pytest.mark.asyncio

SB = "writing-F3-sentence-builder"
GW = "writing-F3-shopping-list"
EF = "writing-F3-error-fix"


class ScriptedLLM:
    """LLMProvider stand-in that returns scripted JSON."""

    def __init__(self, payload: str | dict[str, Any]) -> None:
        self.payload = payload if isinstance(payload, str) else json.dumps(payload)
        self.calls: list[tuple[str, list[dict[str, str]]]] = []

    async def generate(
        self, system_prompt: str, history: list[dict[str, str]]
    ) -> str:
        self.calls.append((system_prompt, history))
        return self.payload


def _guided_payload(**overrides: Any) -> dict[str, Any]:
    body = {
        "dimensions": {
            "grammar_mechanics": 80,
            "vocabulary_register": 80,
            "task_fulfillment": 80,
            "coherence_organization": 80,
            "spelling": 80,
        },
        "corrections": [
            {
                "original": "buyed",
                "corrected": "bought",
                "rule": "past tense",
                "explanation": "Use bought.",
                "confidence": 0.9,
                "priority": "meaning-changing",
            }
        ],
    }
    body.update(overrides)
    return body


@pytest.fixture
def writing_llm() -> ScriptedLLM:
    return ScriptedLLM(_guided_payload())


@pytest_asyncio.fixture
async def client(
    db_engine, db_session: AsyncSession, writing_llm: ScriptedLLM
) -> AsyncIterator[AsyncClient]:
    app = create_app(Settings(_env_file=None))

    async def override_session() -> AsyncIterator[AsyncSession]:
        yield db_session

    app.dependency_overrides[get_db] = override_session
    app.dependency_overrides[get_llm_provider] = lambda: writing_llm
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as http:
        yield http


async def _auth(client: AsyncClient, email: str) -> dict[str, str]:
    register = await client.post(
        "/api/auth/register",
        json={
            "email": email,
            "name": "C4",
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


def test_sentence_builder_scoring_contract() -> None:
    correct = "I need some milk and bread."
    assert score_sentence_builder(correct, "I need some milk and bread.") == 100
    assert score_sentence_builder(correct, "need I some milk and bread") == 50
    assert score_sentence_builder(correct, "I need coffee") == 0


def test_error_fix_partial_all_and_false_positive() -> None:
    error_text = "I buyed some breads and a milks at the stores yesterday."
    corrected = "I bought some bread and milk at the store yesterday."
    partial = score_error_fix(
        error_text=error_text,
        corrected_text=corrected,
        error_count=4,
        submitted_text=None,
        found_errors=[
            {"original": "buyed", "corrected": "bought"},
            {"original": "breads", "corrected": "bread"},
        ],
    )
    assert partial["errors_found"] == 2
    assert partial["score"] == 50
    assert partial["bonus_applied"] is False

    full = score_error_fix(
        error_text=error_text,
        corrected_text=corrected,
        error_count=4,
        submitted_text=None,
        found_errors=[
            {"original": "buyed", "corrected": "bought"},
            {"original": "breads", "corrected": "bread"},
            {"original": "milks", "corrected": "milk"},
            {"original": "stores", "corrected": "store"},
        ],
    )
    assert full["errors_found"] == 4
    assert full["bonus_applied"] is True
    assert full["raw_score"] == 110
    assert full["score"] == 100

    penalized = score_error_fix(
        error_text=error_text,
        corrected_text=corrected,
        error_count=4,
        submitted_text=None,
        found_errors=[
            {"original": "buyed", "corrected": "bought"},
            {"original": "I", "corrected": "We"},
        ],
    )
    assert penalized["false_positives"] == 1
    assert penalized["score"] == 15


def test_guided_write_weights() -> None:
    dims = {
        "grammar_mechanics": 100,
        "vocabulary_register": 100,
        "task_fulfillment": 100,
        "coherence_organization": 100,
        "spelling": 100,
    }
    assert weighted_writing_score(dims) == 100
    mixed = {
        "grammar_mechanics": 80,
        "vocabulary_register": 60,
        "task_fulfillment": 100,
        "coherence_organization": 40,
        "spelling": 20,
    }
    expected = 0.25 * 80 + 0.20 * 60 + 0.25 * 100 + 0.20 * 40 + 0.10 * 20
    assert weighted_writing_score(mixed) == pytest.approx(expected)


def test_level_guardrail_in_prompt() -> None:
    prompt = writing_eval_system_prompt(
        sonolo_level=2,
        grammar_targets=["Articles: a, an, the"],
        language="en-CA",
    )
    assert "Sonolo level is 2" in prompt
    assert "do NOT flag subjunctive" in prompt
    assert "Articles: a, an, the" in prompt
    assert "If unsure, do not mark it as wrong" in prompt


async def test_get_published_writing(client: AsyncClient) -> None:
    headers = await _auth(client, "get@example.com")
    response = await client.get(f"/api/learn/writing/{SB}", headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["id"] == SB
    assert body["exercise_type"] == "sentence_builder"
    assert body["word_bank"] is not None
    assert "correct_sentence" not in body
    assert "model_answer" not in body


async def test_unpublished_writing_hidden(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await persist_curriculum(db_session)
    db_session.add(
        WritingExercise(
            id=content_writing_id("writing-hidden"),
            content_id="writing-hidden",
            title="Hidden",
            language="en-CA",
            exercise_type="sentence_builder",
            prompt="x",
            word_count_target={},
            rubric={},
            vocabulary_targets=[],
            grammar_targets=[],
            is_published=False,
            sonolo_level=2,
        )
    )
    await db_session.commit()
    headers = await _auth(client, "hiddenw@example.com")
    response = await client.get("/api/learn/writing/writing-hidden", headers=headers)
    assert response.status_code == 404


async def test_sentence_builder_api_scores(client: AsyncClient) -> None:
    headers = await _auth(client, "sb@example.com")
    exact = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread."},
    )
    assert exact.status_code == 200
    assert exact.json()["score"] == 100

    headers2 = await _auth(client, "sb2@example.com")
    shuffled = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers2,
        json={"text": "need I some milk and bread"},
    )
    assert shuffled.json()["score"] == 50

    headers3 = await _auth(client, "sb3@example.com")
    wrong = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers3,
        json={"text": "I need coffee"},
    )
    assert wrong.json()["score"] == 0


async def test_error_fix_api_partial_and_bonus(client: AsyncClient) -> None:
    headers = await _auth(client, "ef@example.com")
    partial = await client.post(
        f"/api/learn/writing/{EF}/submit",
        headers=headers,
        json={
            "text": "partial",
            "found_errors": [
                {"original": "buyed", "corrected": "bought"},
                {"original": "breads", "corrected": "bread"},
            ],
        },
    )
    assert partial.status_code == 200
    assert partial.json()["score"] == 50

    headers2 = await _auth(client, "ef2@example.com")
    full = await client.post(
        f"/api/learn/writing/{EF}/submit",
        headers=headers2,
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
    assert full.json()["details"]["bonus_applied"] is True
    assert full.json()["details"]["raw_score"] == 110
    assert full.json()["score"] == 100

    headers3 = await _auth(client, "ef3@example.com")
    fp = await client.post(
        f"/api/learn/writing/{EF}/submit",
        headers=headers3,
        json={
            "text": "x",
            "found_errors": [
                {"original": "buyed", "corrected": "bought"},
                {"original": "I", "corrected": "We"},
            ],
        },
    )
    assert fp.json()["details"]["false_positives"] == 1
    assert fp.json()["score"] == 15


async def test_guided_write_invokes_llm_and_caps_corrections(
    client: AsyncClient, writing_llm: ScriptedLLM
) -> None:
    writing_llm.payload = json.dumps(
        _guided_payload(
            corrections=[
                {
                    "original": f"e{i}",
                    "corrected": f"c{i}",
                    "rule": "r",
                    "explanation": "fix",
                    "confidence": 0.4 if i == 0 else 0.95,
                    "priority": "minor" if i > 2 else "meaning-changing",
                }
                for i in range(7)
            ]
        )
    )
    headers = await _auth(client, "gw@example.com")
    response = await client.post(
        f"/api/learn/writing/{GW}/submit",
        headers=headers,
        json={
            "text": "I need to buy: 1. Two bags of rice 2. A loaf of bread 3. Some milk"
        },
    )
    assert response.status_code == 200, response.text
    assert writing_llm.calls, "existing LLMProvider.generate must be invoked"
    system, history = writing_llm.calls[0]
    assert "Sonolo level is 2" in system
    assert "do NOT flag subjunctive" in system
    assert "model_answer" in history[0]["content"]
    body = response.json()
    assert body["score"] == 80
    dims = body["details"]["dimensions"]
    assert set(dims) == {
        "grammar_mechanics",
        "vocabulary_register",
        "task_fulfillment",
        "coherence_organization",
        "spelling",
    }
    assert body["details"]["weights"]["grammar_mechanics"] == 0.25
    assert body["details"]["weights"]["spelling"] == 0.10
    corrections = body["details"]["corrections"]
    assert len(corrections) == MAX_CORRECTIONS
    assert corrections[0]["explanation"].startswith("This might be an error:")


async def test_revision_loop_max_three_and_highest_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "rev@example.com")
    first = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "I need coffee"},
    )
    assert first.json()["score"] == 0
    assert first.json()["revision"] == 1
    second = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "need I some milk and bread"},
    )
    assert second.json()["score"] == 50
    third = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread."},
    )
    assert third.json()["score"] == 100
    fourth = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread now"},
    )
    assert fourth.status_code == 409
    user_id = (
        await db_session.execute(
            select(SkillExerciseAttempt.user_id)
            .where(SkillExerciseAttempt.content_id == SB)
            .limit(1)
        )
    ).scalar_one()
    scores = await last_exercise_scores(db_session, user_id, "writing", 2)
    assert scores == [100.0]
    count = (
        await db_session.execute(
            select(func.count()).select_from(SkillExerciseAttempt).where(
                SkillExerciseAttempt.content_id == SB,
                SkillExerciseAttempt.status == ATTEMPT_SUBMITTED,
            )
        )
    ).scalar_one()
    assert int(count) == MAX_REVISIONS


async def test_duplicate_finalized_does_not_duplicate_evidence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "dupw@example.com")
    payload = {"text": "I need some milk and bread."}
    first = await client.post(
        f"/api/learn/writing/{SB}/submit", headers=headers, json=payload
    )
    second = await client.post(
        f"/api/learn/writing/{SB}/submit", headers=headers, json=payload
    )
    assert first.status_code == 200
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


async def test_f3_writing_complete_after_all_three(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    headers = await _auth(client, "allw@example.com")
    sb = await client.post(
        f"/api/learn/writing/{SB}/submit",
        headers=headers,
        json={"text": "I need some milk and bread."},
    )
    assert sb.json()["writing_complete"] is False
    ef = await client.post(
        f"/api/learn/writing/{EF}/submit",
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
    assert ef.json()["writing_complete"] is False
    gw = await client.post(
        f"/api/learn/writing/{GW}/submit",
        headers=headers,
        json={
            "text": "I need to buy: 1. Two bags of rice 2. A loaf of bread 3. Some milk"
        },
    )
    assert gw.status_code == 200
    assert gw.json()["writing_complete"] is True
    assert gw.json()["required_remaining"] == []
    progress = (await db_session.execute(select(UserUnitProgress))).scalar_one()
    assert progress.writing_complete is True
