"""Property-level condition aggregation tests (no DB) — the §27.3 roll-up discipline: median
(not mean) grades, max-pooled red flags, virtual-staging exclusion, explicit coverage gaps, and
confidence that moves with coverage, not with the grade.
"""

from decimal import Decimal

from deallens.modules.vision import aggregate
from deallens.modules.vision.schemas import (
    PhotoPerception,
    PhotoQuality,
    RedFlag,
    RedFlagSeverity,
    RedFlagType,
)


def _p(
    room: str | None, grade: int | None, *, conf: float = 0.6, usable: bool = True,
    staged: bool = False, flags: list[RedFlag] | None = None,
) -> PhotoPerception:
    return PhotoPerception(
        room_type=room, condition_grade=grade, confidence=conf,
        quality=PhotoQuality(photo_usable=usable, staged_or_virtual=staged),
        red_flags=flags or [], model_id="heuristic-v0", pipeline_version="heuristic-v0",
    )


def test_median_grade_per_system() -> None:
    agg = aggregate.aggregate_condition([
        _p("kitchen", 2), _p("kitchen", 4), _p("kitchen", 4),  # median 4
        _p("bath", 3),
    ])
    assert agg.grades["kitchen"] == 4
    assert agg.grades["bath"] == 3


def test_unphotographed_system_is_none_not_average() -> None:
    agg = aggregate.aggregate_condition([_p("kitchen", 4)])
    assert agg.grades["roof"] is None  # never faked to "average" (§27.1)
    assert "roof" in agg.coverage.missing_classes


def test_virtually_staged_photo_excluded_from_grade() -> None:
    # A staged kitchen photo must not launder the kitchen into "renovated".
    agg = aggregate.aggregate_condition([_p("kitchen", 5, staged=True)])
    assert agg.grades["kitchen"] is None
    assert "kitchen" not in agg.coverage.photographed_classes


def test_unusable_photo_excluded() -> None:
    agg = aggregate.aggregate_condition([_p("kitchen", 2, usable=False)])
    assert agg.grades["kitchen"] is None


def test_red_flags_max_pooled() -> None:
    agg = aggregate.aggregate_condition([
        _p("basement", 2, flags=[RedFlag(type=RedFlagType.FOUNDATION_CRACK,
                                         severity=RedFlagSeverity.POSSIBLE)]),
        _p("basement", 2, flags=[RedFlag(type=RedFlagType.FOUNDATION_CRACK,
                                         severity=RedFlagSeverity.SEVERE)]),
    ])
    foundation = [f for f in agg.red_flags if f.type is RedFlagType.FOUNDATION_CRACK]
    assert len(foundation) == 1  # deduped by type
    assert foundation[0].severity is RedFlagSeverity.SEVERE  # worst wins


def test_renovation_difficulty_rises_with_gut_systems_and_flags() -> None:
    easy = aggregate.aggregate_condition([_p("kitchen", 4), _p("bath", 4)])
    hard = aggregate.aggregate_condition([
        _p("kitchen", 1), _p("bath", 2),
        _p("basement", 1, flags=[RedFlag(type=RedFlagType.FOUNDATION_CRACK,
                                         severity=RedFlagSeverity.SEVERE)]),
    ])
    assert easy.renovation_difficulty is not None and easy.renovation_difficulty <= 2
    assert hard.renovation_difficulty == 5  # capped gut


def test_confidence_lower_with_thin_coverage() -> None:
    full = aggregate.aggregate_condition([
        _p("kitchen", 4), _p("bath", 4), _p("living", 4),
        _p("exterior_front", 4), _p("roof", 4), _p("yard", 4),
    ])
    thin = aggregate.aggregate_condition([_p("kitchen", 4)])
    assert full.confidence > thin.confidence


def test_empty_perceptions_zero_confidence() -> None:
    agg = aggregate.aggregate_condition([])
    assert agg.confidence == Decimal("0")
    assert all(g is None for g in agg.grades.values())
    assert agg.renovation_difficulty is None
