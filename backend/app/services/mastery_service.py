"""Four-skill mastery engine (C2 / Part V §5.1–§5.6).

Pure domain logic. Does not award XP, write certificates, or call
legacy user_skills. Callers persist skill levels to C0
``user_skill_levels``.

Founder/Architect D-012: mastery is unavailable when any required
component is missing. No numeric fallback. Advancement does not occur.

Founder/Architect D-013: band completion (Part V §5.5, includes band
test) is independent of certificate eligibility (Part IV §4.4, no
band test). C2 evaluates both; it does not issue certificates.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from math import floor
from statistics import mean
from typing import Literal

SKILLS: tuple[str, ...] = ("speaking", "listening", "reading", "writing")

SkillName = Literal["speaking", "listening", "reading", "writing"]
BandName = Literal["foundation", "middle", "advanced"]
ImbalancePriority = Literal["critical", "high", "balanced"]

EXERCISE_WEIGHT = 0.40
UNIT_TEST_WEIGHT = 0.35
EMA_WEIGHT = 0.25
EMA_OLD_WEIGHT = 0.7
EMA_NEW_WEIGHT = 0.3
EMA_DECIMALS = 2
EMA_MIN = 0.0
EMA_MAX = 100.0
EXERCISE_WINDOW = 20
MAX_LEVEL = 9
UNIT_TEST_OVERALL_MIN = 70.0
UNIT_TEST_SKILL_MIN = 60.0

DISPLAY_WEIGHTS: dict[str, float] = {
    "speaking": 0.30,
    "listening": 0.25,
    "reading": 0.25,
    "writing": 0.20,
}

#: Part V §5.3 — keyed by current level.
MASTERY_THRESHOLDS: dict[int, float] = {
    1: 60.0,
    2: 65.0,
    3: 70.0,
    4: 70.0,
    5: 72.0,
    6: 75.0,
    7: 75.0,
    8: 78.0,
    9: 80.0,
}

REQUIRED_EXERCISES: dict[int, int] = {
    1: 6,
    2: 8,
    3: 10,
    4: 10,
    5: 12,
    6: 12,
    7: 14,
    8: 14,
    9: 16,
}

UNITS_FOR_LEVEL: dict[int, tuple[str, ...]] = {
    1: ("F1", "F2"),
    2: ("F3", "F4"),
    3: ("F5", "F6"),
    4: ("M1", "M2"),
    5: ("M3", "M4"),
    6: ("M5", "M6"),
    7: ("A1", "A2"),
    8: ("A3", "A4"),
    9: ("A5", "A6"),
}

#: Additional band-test evidence required to leave this current level.
BAND_TEST_FOR_ADVANCEMENT: dict[int, BandName] = {
    3: "foundation",
    6: "middle",
}

BANDS: dict[BandName, dict[str, object]] = {
    "foundation": {
        "levels": (1, 2, 3),
        "units": ("F1", "F2", "F3", "F4", "F5", "F6"),
        "final_level": 3,
        "band_test": "foundation",
    },
    "middle": {
        "levels": (4, 5, 6),
        "units": ("M1", "M2", "M3", "M4", "M5", "M6"),
        "final_level": 6,
        "band_test": "middle",
    },
    "advanced": {
        "levels": (7, 8, 9),
        "units": ("A1", "A2", "A3", "A4", "A5", "A6"),
        "final_level": 9,
        "band_test": "advanced",
    },
}

CERTIFICATE_MIN_LEVEL: dict[BandName, int] = {
    "foundation": 3,
    "middle": 6,
    "advanced": 9,
}


class MasteryUnavailable(Exception):
    """A required mastery component has no observations (D-012).

    Part V §5.2 defines no numeric fallback. Missing data is not zero,
    is not omitted from the weighted sum, and is not replaced.
    """


@dataclass(frozen=True)
class SkillRecommendation:
    """Part V §5.6 imbalance recommendation (Daily Mix is not applied here)."""

    priority: ImbalancePriority
    skill: str | None
    message: str
    daily_mix_weight: float | None


@dataclass(frozen=True)
class UnitTestEvidence:
    """One unit-test sitting used for §5.3 / §5.5 evidence."""

    unit_code: str
    overall_score: float | None = None
    skill_scores: Mapping[str, float] | None = None


@dataclass(frozen=True)
class AdvancementDecision:
    """Result of §5.3. ``new_level`` is never less than ``previous_level``."""

    skill: str
    previous_level: int
    new_level: int
    advanced: bool


def get_units_for_level(level: int) -> tuple[str, ...]:
    """Return the two unit codes that belong to ``level``."""
    return UNITS_FOR_LEVEL[level]


def update_ema(old_ema: float, session_score: float) -> float:
    """Part V §5.2 EMA: round(0.7 * old + 0.3 * new, 2), clamped [0, 100]."""
    updated = round(
        EMA_OLD_WEIGHT * old_ema + EMA_NEW_WEIGHT * session_score,
        EMA_DECIMALS,
    )
    return min(EMA_MAX, max(EMA_MIN, updated))


def _average(scores: Sequence[float]) -> float:
    if len(scores) == 0:
        raise MasteryUnavailable(
            "Mastery source has no observations; Part V §5.2 defines no fallback."
        )
    return mean(scores)


def compute_mastery_score(
    exercise_scores: Sequence[float],
    unit_test_skill_scores: Sequence[float],
    ema_session_score: float | None,
) -> float:
    """Part V §5.2 weighted mastery at one skill and level.

    Uses the last 20 exercise scores. D-012: empty exercise scores,
    empty unit-test scores, or an unset EMA raise MasteryUnavailable.
    """
    if ema_session_score is None:
        raise MasteryUnavailable(
            "EMA session score is unset; Part V §5.2 defines no initial EMA."
        )
    window = exercise_scores[-EXERCISE_WINDOW:]
    return (
        EXERCISE_WEIGHT * _average(window)
        + UNIT_TEST_WEIGHT * _average(unit_test_skill_scores)
        + EMA_WEIGHT * ema_session_score
    )


def _require_all_skills(levels: Mapping[str, int]) -> dict[str, int]:
    missing = [skill for skill in SKILLS if skill not in levels]
    if missing:
        raise MasteryUnavailable(
            f"Skill levels missing {missing}; all four skills are required."
        )
    return {skill: levels[skill] for skill in SKILLS}


def readiness_level(skill_levels: Mapping[str, int]) -> int:
    """Part V §5.1 weakest-link readiness: min of the four skill levels."""
    levels = _require_all_skills(skill_levels)
    return min(levels.values())


def display_level(skill_levels: Mapping[str, int]) -> int:
    """Part V §5.1 display level: floor of the weighted skill levels."""
    levels = _require_all_skills(skill_levels)
    weighted = sum(DISPLAY_WEIGHTS[skill] * levels[skill] for skill in SKILLS)
    return floor(weighted)


def unit_test_meets_criteria(evidence: UnitTestEvidence) -> bool:
    """§5.5: overall ≥ 70 and every skill ≥ 60. No rounding before compare."""
    if evidence.overall_score is None or evidence.skill_scores is None:
        return False
    if any(skill not in evidence.skill_scores for skill in SKILLS):
        return False
    if evidence.overall_score < UNIT_TEST_OVERALL_MIN:
        return False
    return all(
        evidence.skill_scores[skill] >= UNIT_TEST_SKILL_MIN for skill in SKILLS
    )


def count_units_passed(
    unit_tests: Sequence[UnitTestEvidence],
    required_units: Sequence[str],
    skill: str,
) -> int:
    """Count required units whose unit test meets §5.5 thresholds for ``skill``."""
    by_code = {item.unit_code: item for item in unit_tests}
    passed = 0
    for code in required_units:
        evidence = by_code.get(code)
        if evidence is None:
            continue
        if not unit_test_meets_criteria(evidence):
            continue
        assert evidence.skill_scores is not None
        if evidence.skill_scores.get(skill, 0.0) < UNIT_TEST_SKILL_MIN:
            continue
        passed += 1
    return passed


def check_level_advancement(
    skill: str,
    current_level: int,
    exercise_scores: Sequence[float],
    unit_test_skill_scores: Sequence[float],
    ema_session_score: float | None,
    unit_tests: Sequence[UnitTestEvidence],
    band_tests_passed: Sequence[str],
) -> AdvancementDecision:
    """Part V §5.3. Never returns a level below ``current_level``.

    D-012: if mastery cannot be computed, ``new_level`` stays at
    ``current_level`` and ``advanced`` is False.
    """
    previous = current_level
    if current_level >= MAX_LEVEL:
        return AdvancementDecision(
            skill=skill,
            previous_level=previous,
            new_level=MAX_LEVEL,
            advanced=False,
        )
    try:
        mastery = compute_mastery_score(
            exercise_scores, unit_test_skill_scores, ema_session_score
        )
    except MasteryUnavailable:
        return AdvancementDecision(
            skill=skill,
            previous_level=previous,
            new_level=previous,
            advanced=False,
        )
    threshold = MASTERY_THRESHOLDS[current_level]
    required_exercises = REQUIRED_EXERCISES[current_level]
    required_units = get_units_for_level(current_level)
    units_passed = count_units_passed(unit_tests, required_units, skill)
    additional_band = BAND_TEST_FOR_ADVANCEMENT.get(current_level)
    band_ok = (
        additional_band is None or additional_band in band_tests_passed
    )
    if (
        mastery >= threshold
        and len(exercise_scores) >= required_exercises
        and units_passed >= len(required_units)
        and band_ok
    ):
        new_level = current_level + 1
        return AdvancementDecision(
            skill=skill,
            previous_level=previous,
            new_level=new_level,
            advanced=True,
        )
    return AdvancementDecision(
        skill=skill,
        previous_level=previous,
        new_level=previous,
        advanced=False,
    )


def check_band_completion(
    band: BandName,
    skill_levels: Mapping[str, int],
    unit_tests: Sequence[UnitTestEvidence],
    band_tests_passed: Sequence[str],
) -> bool:
    """Part V §5.5: all unit tests, all skills at final level, band test.

    D-013: this is not certificate eligibility. The band test is required
    for Foundation, Middle, and Advanced completion.
    """
    config = BANDS[band]
    units = config["units"]
    final_level = config["final_level"]
    band_test = config["band_test"]
    assert isinstance(units, tuple)
    assert isinstance(final_level, int)
    assert isinstance(band_test, str)
    levels = _require_all_skills(skill_levels)
    all_units_passed = all(
        any(
            item.unit_code == code and unit_test_meets_criteria(item)
            for item in unit_tests
        )
        for code in units
    )
    all_skills_at_level = all(levels[skill] >= final_level for skill in SKILLS)
    band_test_ok = band_test in band_tests_passed
    return all_units_passed and all_skills_at_level and band_test_ok


def certificate_eligible(
    band: BandName,
    skill_levels: Mapping[str, int],
    unit_tests: Sequence[UnitTestEvidence],
) -> bool:
    """Part IV §4.4 certificate trigger (D-013: no band-test requirement).

    Independent of ``check_band_completion``. C2 does not issue certificates.
    """
    levels = _require_all_skills(skill_levels)
    min_level = CERTIFICATE_MIN_LEVEL[band]
    units = BANDS[band]["units"]
    assert isinstance(units, tuple)
    units_passed = all(
        any(item.unit_code == code and unit_test_meets_criteria(item) for item in unit_tests)
        for code in units
    )
    return readiness_level(levels) >= min_level and units_passed


def get_skill_recommendation(skill_levels: Mapping[str, int]) -> SkillRecommendation:
    """Part V §5.6. Tie-break among equal minima is SKILLS tuple order."""
    levels = _require_all_skills(skill_levels)
    min_level = min(levels[skill] for skill in SKILLS)
    max_level = max(levels[skill] for skill in SKILLS)
    min_skill = next(skill for skill in SKILLS if levels[skill] == min_level)
    gap = max_level - min_level
    if gap >= 3:
        return SkillRecommendation(
            priority="critical",
            skill=min_skill,
            message=(
                f"Your {min_skill} (Level {levels[min_skill]}) is holding back "
                f"your readiness. Focus on {min_skill} to unlock your potential."
            ),
            daily_mix_weight=0.50,
        )
    if gap >= 2:
        return SkillRecommendation(
            priority="high",
            skill=min_skill,
            message=f"Boost your {min_skill} to catch up with your other skills.",
            daily_mix_weight=0.40,
        )
    return SkillRecommendation(
        priority="balanced",
        skill=None,
        message="Your skills are well-balanced! Keep it up.",
        daily_mix_weight=None,
    )
