"""Deterministic reading scoring and timer policy (Part XI §11.2, D-015–D-021)."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta
from statistics import mean
from typing import Any

from app.core.time import utc_now

_BOUNDARY_PUNCT = re.compile(r"^[^\w$]+|[^\w$]+$", re.UNICODE)

ACTIVITY_READING_EXERCISE = "reading_exercise"
ACTIVITY_VOCABULARY_HUNT = "vocabulary_hunt"

TIME_LIMITS_MINUTES = {
    "none": None,
    "middle": 10,
    "advanced": 7,
}


def time_limit_minutes(sonolo_level: int | None) -> int | None:
    if sonolo_level is None or sonolo_level <= 3:
        return None
    if sonolo_level <= 6:
        return 10
    return 7


def _aware(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def is_late(
    started_at: datetime,
    sonolo_level: int | None,
    now: datetime | None = None,
) -> bool:
    limit = time_limit_minutes(sonolo_level)
    if limit is None:
        return False
    current = _aware(now or utc_now())
    return current > _aware(started_at) + timedelta(minutes=limit)


def normalize_answer(value: str) -> str:
    """Casefold, collapse whitespace, strip punctuation at token boundaries."""
    tokens: list[str] = []
    for raw in value.strip().split():
        trimmed = _BOUNDARY_PUNCT.sub("", raw.casefold())
        if trimmed:
            tokens.append(trimmed)
    return " ".join(tokens)


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def score_question(question: dict[str, Any], submitted: Any) -> float:
    qtype = question.get("type")
    if qtype == "multiple_choice":
        return 100.0 if submitted == question.get("correct_answer") else 0.0
    if qtype == "true_false":
        return 100.0 if submitted == question.get("correct_answer") else 0.0
    if qtype == "fill_blank":
        return _score_fill_blank(question, submitted)
    if qtype == "short_answer":
        return _score_short_answer(question, submitted)
    raise ValueError(f"Unsupported reading question type: {qtype!r}")


def _score_fill_blank(question: dict[str, Any], submitted: Any) -> float:
    normalized = normalize_answer(_as_text(submitted))
    correct = normalize_answer(_as_text(question.get("correct_answer")))
    if normalized and normalized == correct:
        return 100.0
    for accepted in question.get("accepted_answers") or []:
        if normalized and normalized == normalize_answer(_as_text(accepted)):
            return 80.0
    return 0.0


def _score_short_answer(question: dict[str, Any], submitted: Any) -> float:
    """L1–L3 only. Exact normalized match, then all authored keywords."""
    level = question.get("level") or question.get("sonolo_level")
    if isinstance(level, int) and level >= 4:
        raise ValueError("L4+ short_answer scoring is deferred (D-016).")
    text = _as_text(submitted)
    normalized = normalize_answer(text)
    correct = normalize_answer(_as_text(question.get("correct_answer")))
    if normalized and correct and normalized == correct:
        return 100.0
    for accepted in question.get("accepted_answers") or []:
        if normalized and normalized == normalize_answer(_as_text(accepted)):
            return 80.0
    keywords = [normalize_answer(_as_text(item)) for item in (question.get("keywords") or [])]
    keywords = [item for item in keywords if item]
    if keywords:
        tokens = set(normalized.split())
        if all(keyword in tokens or keyword in normalized for keyword in keywords):
            return 100.0
    return 0.0


def score_reading_submission(
    questions: list[dict[str, Any]], answers: dict[str, Any]
) -> tuple[float, list[dict[str, Any]]]:
    if not questions:
        return 0.0, []
    details: list[dict[str, Any]] = []
    scores: list[float] = []
    for question in questions:
        qid = str(question["id"])
        submitted = answers.get(qid)
        points = score_question(question, submitted)
        scores.append(points)
        details.append({"id": qid, "type": question.get("type"), "score": points})
    return float(mean(scores)), details


def score_vocabulary_hunt(
    target_words: list[str], submitted_words: list[str]
) -> tuple[float, list[dict[str, Any]]]:
    """D-017: unique found targets / total targets × 100. No extra-word penalty."""
    if not target_words:
        raise ValueError("Vocabulary Hunt requires at least one target word.")
    found_normalized: set[str] = set()
    for word in submitted_words:
        found_normalized.add(normalize_answer(word))
    word_results: list[dict[str, Any]] = []
    hit = 0
    for target in target_words:
        key = normalize_answer(target)
        matched = bool(key) and key in found_normalized
        if matched:
            hit += 1
        word_results.append({"word": target, "found": matched})
    score = (hit / len(target_words)) * 100.0
    return score, word_results
