"""C4 writing scoring (Part VIII §8.3, Part X). Deterministic types + LLM guided_write."""

from __future__ import annotations

import json
import re
from collections import Counter
from difflib import SequenceMatcher
from typing import Any

from app.services.ai.llm import LLMProvider
from app.services.reading_service import normalize_answer

ACTIVITY_WRITING_EXERCISE = "writing_exercise"
MAX_REVISIONS = 3
MAX_CORRECTIONS = 5
CONFIDENCE_THRESHOLD = 0.7
UNCERTAIN_PREFIX = "This might be an error:"

GRAMMAR_WEIGHT = 0.25
VOCABULARY_WEIGHT = 0.20
TASK_WEIGHT = 0.25
COHERENCE_WEIGHT = 0.20
SPELLING_WEIGHT = 0.10

DIMENSION_KEYS = (
    "grammar_mechanics",
    "vocabulary_register",
    "task_fulfillment",
    "coherence_organization",
    "spelling",
)

_SENTENCE_SPLIT = re.compile(r"[.!?]+")
_JSON_BLOCK = re.compile(r"\{.*\}", re.DOTALL)

CORRECTION_PRIORITY = {
    "meaning-changing": 0,
    "systematic": 1,
    "minor": 2,
}


def clamp_score(value: float) -> float:
    return max(0.0, min(100.0, float(value)))


def tokenize_words(text: str) -> list[str]:
    return normalize_answer(text).split()


def score_sentence_builder(correct_sentence: str, submitted: str) -> float:
    """Exact order 100; all words present wrong order 50; else 0."""
    expected = tokenize_words(correct_sentence)
    got = tokenize_words(submitted)
    if not expected:
        return 0.0
    if got == expected:
        return 100.0
    if Counter(got) == Counter(expected):
        return 50.0
    return 0.0


def expected_error_pairs(error_text: str, corrected_text: str) -> list[tuple[str, str]]:
    """Authored error spans from word-level diff of error_text vs corrected_text."""
    source = error_text.split()
    target = corrected_text.split()
    matcher = SequenceMatcher(a=[w.casefold() for w in source], b=[w.casefold() for w in target])
    pairs: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        original = " ".join(source[i1:i2])
        corrected = " ".join(target[j1:j2])
        pairs.append((original, corrected))
    return pairs


def _pair_key(original: str, corrected: str) -> tuple[str, str]:
    return (normalize_answer(original), normalize_answer(corrected))


def score_error_fix(
    *,
    error_text: str,
    corrected_text: str,
    error_count: int,
    submitted_text: str | None,
    found_errors: list[dict[str, str]] | None,
) -> dict[str, Any]:
    """100 * found/error_count, +10 all found, -10 per false positive; clamp [0,100]."""
    expected = expected_error_pairs(error_text, corrected_text)
    count = error_count if error_count > 0 else max(len(expected), 1)
    if found_errors:
        student = [
            (str(item.get("original") or ""), str(item.get("corrected") or ""))
            for item in found_errors
        ]
    else:
        student = expected_error_pairs(error_text, submitted_text or "")

    expected_keys = [_pair_key(a, b) for a, b in expected]
    student_keys = [_pair_key(a, b) for a, b in student]
    found = 0
    matched: set[int] = set()
    false_positives = 0
    for key in student_keys:
        hit = None
        for index, expected_key in enumerate(expected_keys):
            if index in matched:
                continue
            if key == expected_key or (
                key[0] and key[0] in expected_key[0] and key[1] == expected_key[1]
            ) or (
                key[0] == expected_key[0] and key[1] and key[1] in expected_key[1]
            ):
                hit = index
                break
        if hit is None:
            if key[0] or key[1]:
                false_positives += 1
        else:
            matched.add(hit)
            found += 1

    raw = 100.0 * (found / count)
    all_found = found >= count
    if all_found:
        raw += 10.0
    raw -= 10.0 * false_positives
    return {
        "score": clamp_score(raw),
        "raw_score": raw,
        "errors_found": found,
        "error_count": count,
        "false_positives": false_positives,
        "bonus_applied": all_found,
        "expected_errors": [
            {"original": a, "corrected": b} for a, b in expected
        ],
    }


def stage1_precheck(
    text: str,
    *,
    word_count_target: dict[str, Any] | None,
    vocabulary_targets: list[str],
    rubric_criteria: list[str],
) -> dict[str, Any]:
    """Deterministic pre-check. No third-party spellchecker is in the stack."""
    words = text.split()
    word_count = len(words)
    sentences = [part for part in _SENTENCE_SPLIT.split(text) if part.strip()]
    sentence_count = len(sentences) if text.strip() else 0
    min_words = int((word_count_target or {}).get("min") or 0)
    max_words = int((word_count_target or {}).get("max") or 10_000)
    word_count_ok = min_words <= word_count <= max_words
    keywords = [str(item) for item in vocabulary_targets if str(item).strip()]
    present = [
        word
        for word in keywords
        if normalize_answer(word) and normalize_answer(word) in normalize_answer(text)
    ]
    missing = [word for word in keywords if word not in present]
    return {
        "word_count": word_count,
        "word_count_ok": word_count_ok,
        "word_count_target": {"min": min_words, "max": max_words},
        "sentence_count": sentence_count,
        "keywords_present": present,
        "keywords_missing": missing,
        "rubric_criteria": rubric_criteria,
        "spellcheck": {
            "available": False,
            "reason": "No dictionary-based spellchecker is in the backend stack.",
        },
    }


def writing_eval_system_prompt(
    *,
    sonolo_level: int | None,
    grammar_targets: list[str],
    language: str,
) -> str:
    level = sonolo_level if sonolo_level is not None else 2
    scope = "; ".join(grammar_targets) if grammar_targets else "unit grammar targets"
    extra = ""
    if level <= 2:
        extra = (
            " At L2, do NOT flag subjunctive errors or other grammar outside "
            "the expected scope."
        )
    return (
        "You are Sonolo's writing evaluator. Return ONLY valid JSON. "
        "Only correct errors that violate standard English/French grammar. "
        "If unsure, do not mark it as wrong. Do not hallucinate grammar rules. "
        f"The learner's Sonolo level is {level}. Language: {language}. "
        f"Expected grammar scope: {scope}.{extra} "
        "Compare the learner text to the model answer for TASK FULFILLMENT only, "
        "not for stylistic or lexical matching. The learner does not need to use "
        "the model answer's words. "
        "Cap corrections at 5. Prioritize: (1) meaning-changing errors, "
        "(2) systematic patterns, (3) minor slips. "
        "Each correction must include original, corrected, rule, explanation, "
        "confidence (0-1), and priority. "
        "If confidence is below 0.7, the explanation must start with "
        f"'{UNCERTAIN_PREFIX}'. "
        "JSON schema: "
        '{"dimensions": {"grammar_mechanics": n, "vocabulary_register": n, '
        '"task_fulfillment": n, "coherence_organization": n, "spelling": n}, '
        '"corrections": [{"original": "", "corrected": "", "rule": "", '
        '"explanation": "", "confidence": 0.0, "priority": "minor"}]}'
    )


def _parse_llm_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    match = _JSON_BLOCK.search(text)
    if match:
        parsed = json.loads(match.group(0))
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("LLM did not return JSON")


def _normalize_corrections(items: list[Any]) -> list[dict[str, Any]]:
    cleaned: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        confidence = float(item.get("confidence") or 0.0)
        explanation = str(item.get("explanation") or "")
        if confidence < CONFIDENCE_THRESHOLD and not explanation.startswith(
            UNCERTAIN_PREFIX
        ):
            explanation = f"{UNCERTAIN_PREFIX} {explanation}".strip()
        priority = str(item.get("priority") or "minor")
        cleaned.append(
            {
                "original": str(item.get("original") or ""),
                "corrected": str(item.get("corrected") or ""),
                "rule": str(item.get("rule") or ""),
                "explanation": explanation,
                "confidence": confidence,
                "priority": priority,
            }
        )
    cleaned.sort(key=lambda row: CORRECTION_PRIORITY.get(str(row["priority"]), 9))
    return cleaned[:MAX_CORRECTIONS]


def _dimension_scores(payload: dict[str, Any]) -> dict[str, float]:
    raw = payload.get("dimensions") or {}
    scores: dict[str, float] = {}
    for key in DIMENSION_KEYS:
        scores[key] = clamp_score(float(raw.get(key) or 0.0))
    return scores


def weighted_writing_score(dimensions: dict[str, float]) -> float:
    return clamp_score(
        GRAMMAR_WEIGHT * dimensions["grammar_mechanics"]
        + VOCABULARY_WEIGHT * dimensions["vocabulary_register"]
        + TASK_WEIGHT * dimensions["task_fulfillment"]
        + COHERENCE_WEIGHT * dimensions["coherence_organization"]
        + SPELLING_WEIGHT * dimensions["spelling"]
    )


async def score_guided_write(
    *,
    text: str,
    exercise: dict[str, Any],
    sonolo_level: int | None,
    llm: LLMProvider,
    previous_text: str | None = None,
) -> dict[str, Any]:
    rubric = exercise.get("rubric") or {}
    criteria = list(rubric.get("criteria") or [])
    precheck = stage1_precheck(
        text,
        word_count_target=exercise.get("word_count_target") or {},
        vocabulary_targets=list(exercise.get("vocabulary_targets") or []),
        rubric_criteria=criteria,
    )
    system = writing_eval_system_prompt(
        sonolo_level=sonolo_level,
        grammar_targets=list(exercise.get("grammar_targets") or []),
        language=str(exercise.get("language") or "en-CA"),
    )
    user_payload = {
        "prompt": exercise.get("prompt"),
        "scaffold": exercise.get("scaffold"),
        "model_answer": exercise.get("model_answer"),
        "rubric": rubric,
        "stage1": precheck,
        "learner_text": text,
        "previous_text": previous_text,
        "revision_focus": (
            "Focus feedback only on changes from previous_text."
            if previous_text
            else None
        ),
    }
    history = [{"role": "user", "content": json.dumps(user_payload)}]
    raw = await llm.generate(system, history)
    try:
        parsed = _parse_llm_json(raw)
        dimensions = _dimension_scores(parsed)
        corrections = _normalize_corrections(list(parsed.get("corrections") or []))
    except (ValueError, json.JSONDecodeError, TypeError, KeyError):
        # Provider returned non-JSON (e.g. tutor mock). Do not invent a rubric:
        # dimensions stay 0 except task_fulfillment from stage 1.
        task = 100.0 if precheck["word_count_ok"] and not precheck["keywords_missing"] else 40.0
        dimensions = {
            "grammar_mechanics": 0.0,
            "vocabulary_register": 0.0,
            "task_fulfillment": task,
            "coherence_organization": 0.0,
            "spelling": 0.0,
        }
        corrections = []
        parsed = {"parse_error": True, "raw": raw[:500]}
    score = weighted_writing_score(dimensions)
    return {
        "score": score,
        "dimensions": dimensions,
        "weights": {
            "grammar_mechanics": GRAMMAR_WEIGHT,
            "vocabulary_register": VOCABULARY_WEIGHT,
            "task_fulfillment": TASK_WEIGHT,
            "coherence_organization": COHERENCE_WEIGHT,
            "spelling": SPELLING_WEIGHT,
        },
        "corrections": corrections,
        "stage1": precheck,
        "llm_invoked": True,
    }


async def score_writing_submission(
    *,
    exercise: dict[str, Any],
    text: str,
    found_errors: list[dict[str, str]] | None,
    sonolo_level: int | None,
    llm: LLMProvider,
    previous_text: str | None,
) -> dict[str, Any]:
    kind = str(exercise.get("exercise_type") or "")
    if kind == "sentence_builder":
        score = score_sentence_builder(str(exercise.get("correct_sentence") or ""), text)
        return {"score": score, "exercise_type": kind, "text": text}
    if kind == "error_fix":
        detail = score_error_fix(
            error_text=str(exercise.get("error_text") or ""),
            corrected_text=str(exercise.get("corrected_text") or ""),
            error_count=int(exercise.get("error_count") or 0),
            submitted_text=text,
            found_errors=found_errors,
        )
        detail["exercise_type"] = kind
        detail["text"] = text
        return detail
    if kind == "guided_write":
        detail = await score_guided_write(
            text=text,
            exercise=exercise,
            sonolo_level=sonolo_level,
            llm=llm,
            previous_text=previous_text,
        )
        detail["exercise_type"] = kind
        detail["text"] = text
        return detail
    raise ValueError(f"Unsupported writing exercise type: {kind!r}")
