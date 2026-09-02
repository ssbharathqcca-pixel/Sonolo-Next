"""C12 Unit Test engine (Part XI §11.3, F3 §15.3).

Grades authored sections with existing C3/C5/C4 helpers and the SN-011
speaking evaluator. Pass/fail uses C2 ``UNIT_TEST_*_MIN`` constants.
Does not copy mastery/EMA/display formulas.
"""

from __future__ import annotations

import hashlib
import json
import re
from datetime import timedelta
from statistics import mean
from typing import Any
from uuid import uuid4

from app.core.time import utc_now
from app.learning.evaluator import SessionEvaluator
from app.learning.schemas import EvaluationRequest, ScenarioTargets, TranscriptTurn
from app.services.listening_service import score_dictation_segment
from app.services.mastery_service import (
    SKILLS,
    UNIT_TEST_OVERALL_MIN,
    UNIT_TEST_SKILL_MIN,
)
from app.services.reading_service import normalize_answer, score_question, score_vocabulary_hunt

RETRY_MESSAGE = "Almost there! Review and try again in 24 hours."
RETRY_COOLDOWN = timedelta(hours=24)
SECTION_WEIGHTS = {
    "listening": 0.25,
    "reading": 0.25,
    "speaking": 0.30,
    "writing": 0.20,
}
_SENTENCE_SPLIT = re.compile(r"[.!?]+")


def submission_fingerprint(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _mean(scores: list[float]) -> float:
    if not scores:
        return 0.0
    return float(mean(scores))


def score_listening_section(section: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    scores: list[float] = []
    for question in section.get("questions") or []:
        qid = str(question["id"])
        submitted = answers.get(qid)
        qtype = question.get("type")
        if qtype == "dictation":
            points = score_dictation_segment(
                str(question.get("correct_answer") or ""),
                str(submitted or ""),
            )
        elif qtype in {"multiple_choice", "true_false"}:
            points = score_question(question, submitted)
        else:
            points = 0.0
        scores.append(points)
        details.append({"id": qid, "type": qtype, "score": points})
    return {"score": _mean(scores), "details": details}


def score_reading_section(section: dict[str, Any], answers: dict[str, Any]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    scores: list[float] = []
    for question in section.get("questions") or []:
        qid = str(question["id"])
        submitted = answers.get(qid)
        qtype = question.get("type")
        if qtype == "vocabulary_hunt":
            points, _words = score_vocabulary_hunt(
                list(question.get("target_words") or []),
                list(submitted or []),
            )
        else:
            points = score_question(question, submitted)
        scores.append(points)
        details.append({"id": qid, "type": qtype, "score": points})
    return {"score": _mean(scores), "details": details}


async def score_speaking_section(section: dict[str, Any], transcript: str) -> dict[str, Any]:
    task = section.get("task") or {}
    text = str(transcript or "").strip()
    if not text:
        return {"score": 0.0, "details": {"reason": "empty_transcript"}}
    request = EvaluationRequest(
        session_id=uuid4(),
        transcript=[TranscriptTurn(role="user", text=text)],
        scenario_targets=ScenarioTargets(
            vocabulary=list(task.get("vocabulary_targets") or [])
        ),
        duration_seconds=float(task.get("duration_seconds") or 120),
    )
    result = await SessionEvaluator().evaluate(request)
    return {
        "score": float(result.speaking_power_score),
        "details": {"speaking_power_score": result.speaking_power_score},
    }


def _score_articles(task: dict[str, Any], submitted: list[Any]) -> float:
    items = list(task.get("items") or [])
    if not items:
        return 0.0
    scores: list[float] = []
    for index, item in enumerate(items):
        got = submitted[index] if index < len(submitted) else ""
        expected = normalize_answer(str(item.get("correct") or ""))
        scores.append(100.0 if normalize_answer(str(got)) == expected else 0.0)
    return _mean(scores)


def _score_short_message(task: dict[str, Any], text: str) -> float:
    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    required = int(task.get("min_sentences") or 3)
    count_score = 100.0 if not required else min(100.0, 100.0 * len(sentences) / required)
    keywords = [normalize_answer(str(word)) for word in (task.get("keywords") or [])]
    keywords = [word for word in keywords if word]
    blob = normalize_answer(text)
    if not keywords:
        return count_score
    hits = sum(1 for word in keywords if word in blob)
    return _mean([count_score, 100.0 * hits / len(keywords)])


def score_writing_section(section: dict[str, Any], payload: dict[str, Any]) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    scores: list[float] = []
    for task in section.get("tasks") or []:
        tid = str(task["id"])
        ttype = task.get("type")
        if ttype == "articles":
            points = _score_articles(task, list(payload.get("task1") or payload.get(tid) or []))
        elif ttype == "short_message":
            points = _score_short_message(
                task, str(payload.get("task2") or payload.get(tid) or "")
            )
        else:
            points = 0.0
        scores.append(points)
        details.append({"id": tid, "type": ttype, "score": points})
    return {"score": _mean(scores), "details": details}


async def grade_unit_test(
    document: dict[str, Any], answers: dict[str, Any]
) -> dict[str, Any]:
    sections = document.get("sections") or {}
    listening = score_listening_section(
        sections.get("listening") or {}, dict(answers.get("listening") or {})
    )
    reading = score_reading_section(
        sections.get("reading") or {}, dict(answers.get("reading") or {})
    )
    speaking = await score_speaking_section(
        sections.get("speaking") or {},
        str((answers.get("speaking") or {}).get("transcript") or ""),
    )
    writing = score_writing_section(
        sections.get("writing") or {}, dict(answers.get("writing") or {})
    )
    skill_scores = {
        "listening": float(listening["score"]),
        "reading": float(reading["score"]),
        "speaking": float(speaking["score"]),
        "writing": float(writing["score"]),
    }
    overall = sum(skill_scores[skill] * SECTION_WEIGHTS[skill] for skill in SKILLS)
    passed = overall >= UNIT_TEST_OVERALL_MIN and all(
        skill_scores[skill] >= UNIT_TEST_SKILL_MIN for skill in SKILLS
    )
    return {
        "overall": overall,
        "per_skill": skill_scores,
        "passed": passed,
        "sections": {
            "listening": listening,
            "reading": reading,
            "speaking": speaking,
            "writing": writing,
        },
        "retake_cooldown_hours": 24,
        "fail_message": None if passed else RETRY_MESSAGE,
    }


def public_unit_test(document: dict[str, Any]) -> dict[str, Any]:
    """Strip answer keys for GET."""
    sections_in = document.get("sections") or {}
    listening_qs = []
    for question in (sections_in.get("listening") or {}).get("questions") or []:
        item = {
            "id": question["id"],
            "type": question["type"],
            "question": question["question"],
            "options": question.get("options"),
        }
        listening_qs.append(item)
    reading_qs = []
    for question in (sections_in.get("reading") or {}).get("questions") or []:
        item = {
            "id": question["id"],
            "type": question["type"],
            "question": question["question"],
            "options": question.get("options"),
            "target_words": question.get("target_words"),
        }
        reading_qs.append(item)
    writing_tasks = []
    for task in (sections_in.get("writing") or {}).get("tasks") or []:
        writing_tasks.append(
            {
                "id": task["id"],
                "type": task.get("type"),
                "prompt": task.get("prompt"),
                "items": [
                    {"id": item.get("id"), "sentence": item.get("sentence")}
                    for item in (task.get("items") or [])
                ],
                "min_sentences": task.get("min_sentences"),
            }
        )
    speaking_task = (sections_in.get("speaking") or {}).get("task") or {}
    return {
        "id": document["id"],
        "unit_id": document["unit_id"],
        "language": document["language"],
        "level": document.get("level"),
        "time_limit_minutes": document.get("time_limit_minutes"),
        "sections": {
            "listening": {
                "weight": 0.25,
                "title": (sections_in.get("listening") or {}).get("title"),
                "prompt": (sections_in.get("listening") or {}).get("prompt"),
                "questions": listening_qs,
            },
            "reading": {
                "weight": 0.25,
                "title": (sections_in.get("reading") or {}).get("title"),
                "text_content": (sections_in.get("reading") or {}).get("text_content"),
                "questions": reading_qs,
            },
            "speaking": {
                "weight": 0.30,
                "task": {
                    "id": speaking_task.get("id"),
                    "prompt": speaking_task.get("prompt"),
                    "duration_seconds": speaking_task.get("duration_seconds"),
                },
            },
            "writing": {"weight": 0.20, "tasks": writing_tasks},
        },
    }
