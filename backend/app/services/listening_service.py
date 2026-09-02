"""C5 listening scoring: dictation, MC/TF, sequence. Additive to SN-050."""

from __future__ import annotations

from statistics import mean

from app.services.reading_service import normalize_answer

ACTIVITY_LISTENING_EXERCISE = "listening_exercise"
FULL_REPLAY_MAX = 3
SEGMENT_REPLAY_UNLIMITED = True
SPEEDS_ALL_LEVELS = (0.8, 1.0)
SPEED_L4_PLUS = 1.2


def levenshtein(left: str, right: str) -> int:
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)
    previous = list(range(len(right) + 1))
    for i, char_l in enumerate(left, start=1):
        current = [i]
        for j, char_r in enumerate(right, start=1):
            insert = current[j - 1] + 1
            delete = previous[j] + 1
            replace = previous[j - 1] + (char_l != char_r)
            current.append(min(insert, delete, replace))
        previous = current
    return previous[-1]


def words_match(expected: str, got: str) -> bool:
    """Exact after normalize, or 1-edit fuzzy for words of length >= 4 (D-028)."""
    if expected == got:
        return True
    if min(len(expected), len(got)) < 4:
        return False
    return levenshtein(expected, got) <= 1


def tokenize_dictation(text: str) -> list[str]:
    return normalize_answer(text).split()


def score_dictation_segment(expected_text: str, submitted_text: str) -> float:
    """100 * correct_words / total_words. Empty expected → 0 (no division by zero)."""
    expected = tokenize_dictation(expected_text)
    if not expected:
        return 0.0
    got = tokenize_dictation(submitted_text)
    correct = 0
    for index, word in enumerate(expected):
        if index >= len(got):
            break
        if words_match(word, got[index]):
            correct += 1
    return 100.0 * correct / len(expected)


def score_multiple_choice(correct_index: int, submitted: int) -> float:
    return 100.0 if submitted == correct_index else 0.0


def score_true_false(correct_index: int, submitted: int) -> float:
    return 100.0 if submitted == correct_index else 0.0


def score_sequence(correct_order: list[int], submitted: list[int]) -> float:
    """100 if identical; 50 if exactly one swap; else 0."""
    if submitted == correct_order:
        return 100.0
    if len(submitted) != len(correct_order):
        return 0.0
    diffs = [i for i, (exp, got) in enumerate(zip(correct_order, submitted)) if exp != got]
    if (
        len(diffs) == 2
        and submitted[diffs[0]] == correct_order[diffs[1]]
        and submitted[diffs[1]] == correct_order[diffs[0]]
    ):
        return 50.0
    return 0.0


def mean_listening_scores(scores: list[float]) -> float:
    if not scores:
        return 0.0
    return float(mean(scores))
