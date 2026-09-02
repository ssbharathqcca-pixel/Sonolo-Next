"""C2 mastery engine: Part V §5.1–§5.6. No XP, no gamification, no UI."""

from __future__ import annotations

import random

import pytest

from app.services.mastery_service import (
    BAND_TEST_FOR_ADVANCEMENT,
    EXERCISE_WINDOW,
    MASTERY_THRESHOLDS,
    MAX_LEVEL,
    REQUIRED_EXERCISES,
    SKILLS,
    UNITS_FOR_LEVEL,
    AdvancementDecision,
    MasteryUnavailable,
    UnitTestEvidence,
    certificate_eligible,
    check_band_completion,
    check_level_advancement,
    compute_mastery_score,
    count_units_passed,
    display_level,
    get_skill_recommendation,
    get_units_for_level,
    readiness_level,
    unit_test_meets_criteria,
    update_ema,
)

PASSING_SKILL_SCORES = {
    "speaking": 80.0,
    "listening": 80.0,
    "reading": 80.0,
    "writing": 80.0,
}


def passing_unit(code: str, overall: float = 80.0) -> UnitTestEvidence:
    return UnitTestEvidence(
        unit_code=code,
        overall_score=overall,
        skill_scores=dict(PASSING_SKILL_SCORES),
    )


def scores(n: int, value: float = 80.0) -> list[float]:
    return [value] * n


def decide(
    current: int,
    *,
    mastery_exercises: list[float] | None = None,
    unit_test_scores: list[float] | None = None,
    ema: float | None = 80.0,
    units: list[UnitTestEvidence] | None = None,
    band_tests: list[str] | None = None,
    exercise_count: int | None = None,
    skill: str = "speaking",
) -> AdvancementDecision:
    required_units = get_units_for_level(current) if current in UNITS_FOR_LEVEL else ()
    if exercise_count is None:
        exercise_count = REQUIRED_EXERCISES.get(current, 16)
    if mastery_exercises is None:
        mastery_exercises = scores(exercise_count, 100.0)
    if unit_test_scores is None:
        unit_test_scores = [100.0, 100.0]
    if units is None:
        units = [passing_unit(code) for code in required_units]
    if band_tests is None:
        extra = BAND_TEST_FOR_ADVANCEMENT.get(current)
        band_tests = [extra] if extra else []
    return check_level_advancement(
        skill=skill,
        current_level=current,
        exercise_scores=mastery_exercises,
        unit_test_skill_scores=unit_test_scores,
        ema_session_score=ema,
        unit_tests=units,
        band_tests_passed=band_tests,
    )


# --- §5.1 parallel metrics -------------------------------------------------


def test_readiness_level_is_minimum() -> None:
    assert readiness_level(
        {"speaking": 5, "listening": 4, "reading": 6, "writing": 3}
    ) == 3


def test_display_level_uses_specified_weights() -> None:
    levels = {"speaking": 5, "listening": 4, "reading": 6, "writing": 3}
    # 0.30*5 + 0.25*4 + 0.25*6 + 0.20*3 = 1.5+1.0+1.5+0.6 = 4.6 → floor 4
    assert display_level(levels) == 4


def test_display_level_is_not_readiness_level() -> None:
    levels = {"speaking": 9, "listening": 9, "reading": 9, "writing": 3}
    assert readiness_level(levels) == 3
    assert display_level(levels) == 7


# --- §5.2 mastery formula --------------------------------------------------


def test_mastery_score_exact_weights() -> None:
    score = compute_mastery_score(
        exercise_scores=[100.0],
        unit_test_skill_scores=[100.0],
        ema_session_score=100.0,
    )
    assert score == 100.0


def test_mastery_score_component_weights() -> None:
    score = compute_mastery_score(
        exercise_scores=[50.0],
        unit_test_skill_scores=[0.0],
        ema_session_score=0.0,
    )
    assert score == pytest.approx(0.40 * 50.0)
    score = compute_mastery_score(
        exercise_scores=[0.0],
        unit_test_skill_scores=[80.0],
        ema_session_score=0.0,
    )
    assert score == pytest.approx(0.35 * 80.0)
    score = compute_mastery_score(
        exercise_scores=[0.0],
        unit_test_skill_scores=[0.0],
        ema_session_score=40.0,
    )
    assert score == pytest.approx(0.25 * 40.0)


def test_mastery_uses_last_20_exercises_only() -> None:
    early = [0.0] * 10
    recent = [100.0] * 20
    score = compute_mastery_score(
        exercise_scores=early + recent,
        unit_test_skill_scores=[0.0],
        ema_session_score=0.0,
    )
    assert score == pytest.approx(0.40 * 100.0)
    assert EXERCISE_WINDOW == 20


def test_ema_update_coefficients_rounding_and_clamp() -> None:
    assert update_ema(100.0, 0.0) == 70.0
    assert update_ema(0.0, 100.0) == 30.0
    assert update_ema(10.0, 20.0) == 13.0
    assert update_ema(-10.0, -10.0) == 0.0
    assert update_ema(200.0, 200.0) == 100.0
    assert isinstance(update_ema(10.05, 10.04), float)
    assert update_ema(1.11, 2.22) == round(0.7 * 1.11 + 0.3 * 2.22, 2)


def test_mastery_unavailable_when_a_source_is_missing() -> None:
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([], [80.0], 80.0)
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([80.0], [], 80.0)
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([80.0], [80.0], None)


def test_d012_missing_data_is_not_treated_as_zero_or_omitted() -> None:
    """D-012: missing sources raise; they are not 0 and not dropped from the sum."""
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([100.0], [], 100.0)
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([], [100.0], 100.0)
    with pytest.raises(MasteryUnavailable):
        compute_mastery_score([100.0], [100.0], None)
    present = compute_mastery_score([100.0], [0.0], 100.0)
    assert present == pytest.approx(0.40 * 100.0 + 0.35 * 0.0 + 0.25 * 100.0)


def test_d012_advancement_does_not_occur_when_mastery_unavailable() -> None:
    """D-012: complete other evidence still cannot advance without all sources."""
    for exercises, tests, ema in (
        ([], [100.0, 100.0], 100.0),
        (scores(REQUIRED_EXERCISES[1], 100.0), [], 100.0),
        (scores(REQUIRED_EXERCISES[1], 100.0), [100.0, 100.0], None),
    ):
        decision = decide(
            1,
            mastery_exercises=exercises,
            unit_test_scores=tests,
            ema=ema,
        )
        assert decision.advanced is False
        assert decision.new_level == 1
        assert decision.new_level >= decision.previous_level


# --- §5.3 advancement ------------------------------------------------------


TRANSITIONS = [
    (1, 2, "foundation"),
    (2, 3, "foundation"),
    (3, 4, "foundation"),
    (4, 5, "middle"),
    (5, 6, "middle"),
    (6, 7, "middle"),
    (7, 8, "advanced"),
    (8, 9, "advanced"),
]


@pytest.mark.parametrize("current,target,band", TRANSITIONS)
def test_advancement_when_all_evidence_met(
    current: int, target: int, band: str
) -> None:
    decision = decide(current)
    assert decision.new_level == target
    assert decision.advanced is True
    assert decision.new_level >= decision.previous_level


@pytest.mark.parametrize("current,target,band", TRANSITIONS)
def test_advancement_at_exact_threshold(
    current: int, target: int, band: str
) -> None:
    threshold = MASTERY_THRESHOLDS[current]
    # 0.40*x + 0.35*x + 0.25*x = x; set all sources to threshold
    decision = decide(
        current,
        mastery_exercises=scores(REQUIRED_EXERCISES[current], threshold),
        unit_test_scores=[threshold, threshold],
        ema=threshold,
    )
    mastery = compute_mastery_score(
        scores(REQUIRED_EXERCISES[current], threshold),
        [threshold, threshold],
        threshold,
    )
    assert mastery == pytest.approx(threshold)
    assert decision.advanced is True
    assert decision.new_level == target


@pytest.mark.parametrize("current,target,band", TRANSITIONS)
def test_no_advancement_just_below_threshold(
    current: int, target: int, band: str
) -> None:
    threshold = MASTERY_THRESHOLDS[current]
    below = threshold - 0.01
    decision = decide(
        current,
        mastery_exercises=scores(REQUIRED_EXERCISES[current], below),
        unit_test_scores=[below, below],
        ema=below,
    )
    assert decision.advanced is False
    assert decision.new_level == current


@pytest.mark.parametrize("current,target,band", TRANSITIONS)
def test_no_advancement_insufficient_exercises(
    current: int, target: int, band: str
) -> None:
    needed = REQUIRED_EXERCISES[current]
    decision = decide(current, mastery_exercises=scores(needed - 1, 100.0))
    assert decision.advanced is False
    assert decision.new_level == current


@pytest.mark.parametrize("current,target,band", TRANSITIONS)
def test_no_advancement_insufficient_units(
    current: int, target: int, band: str
) -> None:
    required = get_units_for_level(current)
    decision = decide(current, units=[passing_unit(required[0])])
    assert decision.advanced is False
    assert decision.new_level == current


def test_l9_remains_capped() -> None:
    decision = decide(9, mastery_exercises=scores(20, 100.0))
    assert decision.new_level == 9
    assert decision.advanced is False


def test_foundation_band_test_required_for_l3_to_l4() -> None:
    denied = decide(3, band_tests=[])
    assert denied.advanced is False
    allowed = decide(3, band_tests=["foundation"])
    assert allowed.advanced is True
    assert allowed.new_level == 4


def test_middle_band_test_required_for_l6_to_l7() -> None:
    denied = decide(6, band_tests=[])
    assert denied.advanced is False
    allowed = decide(6, band_tests=["middle"])
    assert allowed.advanced is True


def test_l8_to_l9_does_not_require_advanced_band_test() -> None:
    decision = decide(8, band_tests=[])
    assert decision.advanced is True
    assert decision.new_level == 9


# --- §5.4 regression invariant --------------------------------------------


def test_poor_scores_do_not_lower_level() -> None:
    decision = decide(
        5,
        mastery_exercises=scores(20, 0.0),
        unit_test_scores=[0.0],
        ema=0.0,
        units=[],
        band_tests=[],
    )
    assert decision.new_level == 5
    assert decision.new_level >= decision.previous_level


def test_previously_advanced_user_does_not_regress_when_mastery_drops() -> None:
    after = decide(
        4,
        mastery_exercises=scores(REQUIRED_EXERCISES[4], 10.0),
        unit_test_scores=[10.0],
        ema=10.0,
    )
    assert after.previous_level == 4
    assert after.new_level == 4


def test_level_never_decreases_under_random_outcomes() -> None:
    rng = random.Random(20260902)
    for skill in SKILLS:
        level = 1
        for _ in range(80):
            exercise_n = rng.randint(0, 20)
            exercises = [rng.uniform(0.0, 100.0) for _ in range(exercise_n)]
            tests = [rng.uniform(0.0, 100.0) for _ in range(rng.randint(0, 3))]
            ema = rng.choice([None, rng.uniform(0.0, 100.0)])
            unit_codes = get_units_for_level(min(level, 9))
            units = []
            for code in unit_codes:
                if rng.random() < 0.5:
                    units.append(
                        passing_unit(code, overall=rng.uniform(50.0, 100.0))
                    )
            band_tests = [name for name in ("foundation", "middle", "advanced") if rng.random() < 0.3]
            decision = check_level_advancement(
                skill=skill,
                current_level=level,
                exercise_scores=exercises,
                unit_test_skill_scores=tests,
                ema_session_score=ema,
                unit_tests=units,
                band_tests_passed=band_tests,
            )
            assert decision.new_level >= level
            assert decision.new_level >= decision.previous_level
            level = decision.new_level
            if level >= MAX_LEVEL:
                assert level == MAX_LEVEL


# --- §5.5 band completion --------------------------------------------------


def _band_units(prefix: str) -> list[UnitTestEvidence]:
    return [passing_unit(f"{prefix}{n}") for n in range(1, 7)]


def test_foundation_band_completes_when_all_conditions_met() -> None:
    assert check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 3},
        _band_units("F"),
        ["foundation"],
    )


def test_middle_and_advanced_band_completion() -> None:
    assert check_band_completion(
        "middle",
        {"speaking": 6, "listening": 6, "reading": 6, "writing": 6},
        _band_units("M"),
        ["middle"],
    )
    assert check_band_completion(
        "advanced",
        {"speaking": 9, "listening": 9, "reading": 9, "writing": 9},
        _band_units("A"),
        ["advanced"],
    )


def test_band_completion_fails_if_one_unit_missing() -> None:
    units = _band_units("F")[:-1]
    assert not check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 3},
        units,
        ["foundation"],
    )


def test_band_completion_fails_insufficient_overall_score() -> None:
    units = [
        UnitTestEvidence(
            unit_code=f"F{n}",
            overall_score=69.0,
            skill_scores=dict(PASSING_SKILL_SCORES),
        )
        for n in range(1, 7)
    ]
    assert not check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 3},
        units,
        ["foundation"],
    )


def test_band_completion_fails_insufficient_per_skill_score() -> None:
    weak = dict(PASSING_SKILL_SCORES)
    weak["writing"] = 59.0
    units = [
        UnitTestEvidence(unit_code=f"F{n}", overall_score=80.0, skill_scores=weak)
        for n in range(1, 7)
    ]
    assert not check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 3},
        units,
        ["foundation"],
    )


def test_band_completion_fails_if_skill_below_final_level() -> None:
    assert not check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 2},
        _band_units("F"),
        ["foundation"],
    )


def test_band_completion_fails_without_band_test() -> None:
    assert not check_band_completion(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 3},
        _band_units("F"),
        [],
    )


def test_unit_test_69_does_not_round_to_pass() -> None:
    evidence = UnitTestEvidence(
        unit_code="F1",
        overall_score=69.0,
        skill_scores=dict(PASSING_SKILL_SCORES),
    )
    assert unit_test_meets_criteria(evidence) is False
    evidence_70 = UnitTestEvidence(
        unit_code="F1",
        overall_score=70.0,
        skill_scores=dict(PASSING_SKILL_SCORES),
    )
    assert unit_test_meets_criteria(evidence_70) is True


def test_count_units_passed_for_skill() -> None:
    units = [passing_unit("F1"), passing_unit("F2")]
    assert count_units_passed(units, ("F1", "F2"), "speaking") == 2
    assert count_units_passed([passing_unit("F1")], ("F1", "F2"), "speaking") == 1


def test_certificate_eligibility_matches_section_4_4() -> None:
    levels = {"speaking": 3, "listening": 3, "reading": 3, "writing": 3}
    assert certificate_eligible("foundation", levels, _band_units("F"))
    assert not certificate_eligible(
        "foundation",
        {"speaking": 3, "listening": 3, "reading": 3, "writing": 2},
        _band_units("F"),
    )


def test_d013_certificate_eligibility_does_not_require_band_test() -> None:
    """D-013: §4.4 eligibility is independent of §5.5 band-test completion."""
    levels_f = {"speaking": 3, "listening": 3, "reading": 3, "writing": 3}
    units_f = _band_units("F")
    assert certificate_eligible("foundation", levels_f, units_f) is True
    assert check_band_completion("foundation", levels_f, units_f, []) is False
    assert check_band_completion(
        "foundation", levels_f, units_f, ["foundation"]
    ) is True

    levels_m = {"speaking": 6, "listening": 6, "reading": 6, "writing": 6}
    units_m = _band_units("M")
    assert certificate_eligible("middle", levels_m, units_m) is True
    assert check_band_completion("middle", levels_m, units_m, []) is False

    levels_a = {"speaking": 9, "listening": 9, "reading": 9, "writing": 9}
    units_a = _band_units("A")
    assert certificate_eligible("advanced", levels_a, units_a) is True
    assert check_band_completion("advanced", levels_a, units_a, []) is False


# --- §5.6 imbalance -------------------------------------------------------


def test_imbalance_gap_3_is_critical() -> None:
    rec = get_skill_recommendation(
        {"speaking": 5, "listening": 4, "reading": 6, "writing": 3}
    )
    assert rec.priority == "critical"
    assert rec.skill == "writing"
    assert rec.daily_mix_weight == 0.50
    assert "writing" in rec.message
    assert "Level 3" in rec.message


def test_imbalance_gap_2_is_high() -> None:
    rec = get_skill_recommendation(
        {"speaking": 5, "listening": 5, "reading": 5, "writing": 3}
    )
    assert rec.priority == "high"
    assert rec.skill == "writing"
    assert rec.daily_mix_weight == 0.40


def test_imbalance_gap_1_and_0_are_balanced() -> None:
    rec1 = get_skill_recommendation(
        {"speaking": 5, "listening": 5, "reading": 5, "writing": 4}
    )
    rec0 = get_skill_recommendation(
        {"speaking": 5, "listening": 5, "reading": 5, "writing": 5}
    )
    assert rec1.priority == "balanced"
    assert rec1.skill is None
    assert rec1.daily_mix_weight is None
    assert rec0.priority == "balanced"
    assert rec0.skill is None


def test_imbalance_tie_uses_skills_tuple_order() -> None:
    rec = get_skill_recommendation(
        {"speaking": 3, "listening": 6, "reading": 3, "writing": 6}
    )
    assert rec.priority == "critical"
    assert rec.skill == "speaking"


def test_units_for_level_mapping() -> None:
    assert get_units_for_level(1) == ("F1", "F2")
    assert get_units_for_level(2) == ("F3", "F4")
    assert get_units_for_level(9) == ("A5", "A6")
